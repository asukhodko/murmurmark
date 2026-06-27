#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shlex
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_VERSION = "0.2.0"
SCHEMA = "murmurmark.review_lane_pack_apply_report/v1"
VALID_DECISIONS = {"drop_me", "keep_me", "needs_review", "skip", "todo", ""}
DEFAULT_ALLOWED_DECISIONS = {"drop_me", "keep_me", "needs_review", "skip"}
ANSWER_SHORTCUTS = {
    "d": "drop_me",
    "k": "keep_me",
    "r": "needs_review",
    "s": "skip",
    "n": "todo",
    "t": "todo",
    ".": "todo",
    "-": "todo",
    "?": "needs_review",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply compact answers for a MurmurMark review lane pack.")
    parser.add_argument("manifest", type=Path, help="review_lane_pack.<lane>.json.")
    parser.add_argument(
        "--template",
        type=Path,
        default=Path("sessions/_reports/review-plan/review_decisions.template.jsonl"),
        help="Input review_decisions.template.jsonl.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("sessions/_reports/review-plan/review_decisions.jsonl"),
        help="Editable output decisions JSONL.",
    )
    answers = parser.add_mutually_exclusive_group(required=True)
    answers.add_argument(
        "--answers",
        help="Compact answers in pack order. d=drop_me, k=keep_me, r/?=needs_review, s=skip, ./n/t=todo.",
    )
    answers.add_argument(
        "--answers-file",
        type=Path,
        help="Text file with an answers=... line or a bare compact answer line.",
    )
    parser.add_argument("--reviewer", default="", help="Reviewer name written to decided rows.")
    parser.add_argument("--dry-run", action="store_true", help="Validate and print a report without writing --out.")
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        value = json.load(file)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(value)
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")


def normalize_text(value: Any) -> str:
    return " ".join(str(value or "").lower().replace("ё", "е").split())


def review_row_key(row: dict[str, Any]) -> str:
    source_id = str(row.get("source_audit_id") or "").strip()
    if source_id:
        return f"source:{source_id}"
    cluster_id = str(row.get("cluster_id") or "").strip()
    if cluster_id:
        return f"cluster:{cluster_id}"
    utterance_ids = row.get("utterance_ids")
    if isinstance(utterance_ids, list) and utterance_ids:
        return "utterances:" + ",".join(str(item) for item in utterance_ids)
    interval = row.get("interval") if isinstance(row.get("interval"), dict) else {}
    return f"interval:{interval.get('start')}:{interval.get('end')}:{row.get('label')}:{normalize_text(row.get('text'))[:80]}"


def merge_existing(template_rows: list[dict[str, Any]], existing_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    existing_by_key = {review_row_key(row): row for row in existing_rows}
    return [{**row, **existing_by_key.get(review_row_key(row), {})} for row in template_rows]


def allowed_decisions(row: dict[str, Any]) -> set[str]:
    values = row.get("allowed_decisions")
    if not isinstance(values, list) or not values:
        return set(DEFAULT_ALLOWED_DECISIONS)
    allowed = {str(value) for value in values if str(value) in DEFAULT_ALLOWED_DECISIONS}
    return allowed or set(DEFAULT_ALLOWED_DECISIONS)


def parse_answers(raw: str) -> list[str]:
    stripped = raw.strip()
    if not stripped:
        return []
    if "," in stripped or " " in stripped:
        tokens = [token.strip().lower() for token in stripped.replace(",", " ").split() if token.strip()]
    else:
        tokens = list(stripped.lower())
    decisions: list[str] = []
    for token in tokens:
        decision = ANSWER_SHORTCUTS.get(token, token)
        if decision not in VALID_DECISIONS:
            raise ValueError(f"unknown answer: {token}")
        decisions.append(decision)
    return decisions


def read_answers_file(path: Path) -> str:
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("answers="):
            return line.split("=", 1)[1].strip()
        return line
    return ""


def manifest_items(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    items = manifest.get("items")
    if not isinstance(items, list):
        raise ValueError("manifest.items must be an array")
    return [item for item in items if isinstance(item, dict)]


def row_lookup(rows: list[dict[str, Any]]) -> dict[str, int]:
    lookup: dict[str, int] = {}
    for index, row in enumerate(rows):
        source_id = str(row.get("source_audit_id") or "").strip()
        if source_id:
            lookup[source_id] = index
    return lookup


def main() -> int:
    args = parse_args()
    manifest_path = args.manifest.expanduser()
    manifest = read_json(manifest_path)
    items = manifest_items(manifest)
    answers_raw = read_answers_file(args.answers_file.expanduser()) if args.answers_file else str(args.answers or "")
    decisions = parse_answers(answers_raw)
    if len(decisions) != len(items):
        raise SystemExit(f"answers length {len(decisions)} does not match lane pack item count {len(items)}")

    template = args.template.expanduser()
    out = args.out.expanduser()
    rows = merge_existing(read_jsonl(template), read_jsonl(out))
    lookup = row_lookup(rows)
    now = datetime.now(timezone.utc).isoformat()
    applied: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    for item, decision in zip(items, decisions):
        source_id = str(item.get("source_audit_id") or "").strip()
        row_index = lookup.get(source_id)
        if row_index is None:
            rejected.append({"source_audit_id": source_id, "decision": decision, "reason": "missing_template_row"})
            continue
        row = rows[row_index]
        allowed = allowed_decisions(row)
        if decision not in {"", "todo"} and decision not in allowed:
            rejected.append(
                {
                    "source_audit_id": source_id,
                    "decision": decision,
                    "reason": "decision_not_allowed_for_row",
                    "allowed_decisions": sorted(allowed),
                }
            )
            continue
        if decision in {"", "todo"}:
            row["decision"] = "todo"
            row["status"] = "todo"
        else:
            row["decision"] = decision
            row["status"] = "reviewed"
            row["reviewed_at"] = now
            row["review_source"] = "lane_pack"
            row["review_lane_pack"] = str(manifest_path)
            row["review_lane_pack_index"] = item.get("index")
            if args.reviewer:
                row["reviewer"] = args.reviewer
        rows[row_index] = row
        applied.append(
            {
                "source_audit_id": source_id,
                "index": item.get("index"),
                "decision": decision,
                "status": row["status"],
            }
        )

    report = {
        "schema": SCHEMA,
        "generated_at": now,
        "generator": {"name": "apply-review-lane-pack-decisions", "version": SCRIPT_VERSION},
        "inputs": {
            "manifest": str(manifest_path),
            "template": str(template),
            "out": str(out),
            "answers_file": str(args.answers_file) if args.answers_file else None,
        },
        "lane": manifest.get("lane"),
        "dry_run": args.dry_run,
        "summary": {
            "manifest_items": len(items),
            "answer_count": len(decisions),
            "applied_count": len(applied),
            "rejected_count": len(rejected),
            "reviewed_count": sum(1 for row in applied if row.get("status") == "reviewed"),
            "todo_count": sum(1 for row in applied if row.get("status") == "todo"),
        },
        "applied": applied,
        "rejected": rejected,
    }

    report_path = out.with_name("review_lane_pack_apply_report.json")
    if not args.dry_run:
        write_jsonl(out, rows)
        write_json(report_path, report)
    print(json.dumps(report["summary"], ensure_ascii=False))
    if rejected:
        print(f"Rejected: {len(rejected)}")
        for row in rejected:
            print(f"- {row.get('source_audit_id')}: {row.get('reason')}")
    if args.dry_run:
        print("Dry run: no files written.")
    else:
        print(f"Written: {out}")
        print(f"Report: {report_path}")
        print(
            "Next: .venv/bin/python scripts/apply-review-decisions-batch.py "
            f"--decisions {shlex.quote(str(out))} "
            f"--review-template {shlex.quote(str(template))} "
            "--synthesize --refresh-reports"
        )
    return 1 if rejected else 0


if __name__ == "__main__":
    raise SystemExit(main())
