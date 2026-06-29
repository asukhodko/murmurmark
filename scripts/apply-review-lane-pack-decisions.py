#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shlex
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_VERSION = "0.5.0"
SCHEMA = "murmurmark.review_lane_pack_apply_report/v1"
VALID_DECISIONS = {"drop_me", "drop_remote", "keep_me", "needs_review", "skip", "todo", ""}
KNOWN_REVIEW_DECISIONS = {"drop_me", "drop_remote", "keep_me", "needs_review", "skip"}
DEFAULT_ALLOWED_DECISIONS = {"drop_me", "keep_me", "needs_review", "skip"}
ANSWER_SHORTCUTS = {
    "d": "drop_me",
    "c": "drop_remote",
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
        help="Compact answers in pack order. d=drop_me, c=drop_remote, k=keep_me, r/?=needs_review, s=skip, ./n/t=todo.",
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


def display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(resolved)


def shell_path(path: Path) -> str:
    return shlex.quote(display_path(path))


def command_item(item_id: str, command: str, reason: str) -> dict[str, str]:
    return {"id": item_id, "command": command, "reason": reason}


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
    cluster_id = str(row.get("cluster_id") or "").strip()
    utterance_ids = row.get("utterance_ids")
    utterance_key = ",".join(str(item) for item in utterance_ids) if isinstance(utterance_ids, list) else ""
    interval = row.get("interval") if isinstance(row.get("interval"), dict) else {}
    start = interval.get("start")
    end = interval.get("end")
    return (
        "review:"
        f"{row.get('session_id') or ''}:"
        f"{cluster_id}:"
        f"{utterance_key}:"
        f"{start}:{end}:"
        f"{row.get('label')}"
    )


def merge_existing(template_rows: list[dict[str, Any]], existing_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    existing_by_key = {review_row_key(row): row for row in existing_rows}
    template_keys = {review_row_key(row) for row in template_rows}
    merged = [{**row, **existing_by_key.get(review_row_key(row), {})} for row in template_rows]
    merged.extend(row for row in existing_rows if review_row_key(row) not in template_keys and is_reviewed(row))
    return merged


def allowed_decisions(row: dict[str, Any]) -> set[str]:
    values = row.get("allowed_decisions")
    if not isinstance(values, list) or not values:
        return set(DEFAULT_ALLOWED_DECISIONS)
    allowed = {str(value) for value in values if str(value) in KNOWN_REVIEW_DECISIONS}
    return allowed or set(DEFAULT_ALLOWED_DECISIONS)


def is_reviewed(row: dict[str, Any]) -> bool:
    return str(row.get("decision") or "todo") not in {"", "todo"}


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
    source_counts = Counter(str(row.get("source_audit_id") or "").strip() for row in rows)
    for index, row in enumerate(rows):
        lookup[review_row_key(row)] = index
        source_id = str(row.get("source_audit_id") or "").strip()
        if source_id and source_counts[source_id] == 1:
            lookup[f"source:{source_id}"] = index
    return lookup


def item_lookup_key(item: dict[str, Any]) -> str:
    key = str(item.get("review_row_key") or "").strip()
    if key:
        return key
    source_id = str(item.get("source_audit_id") or "").strip()
    return f"source:{source_id}" if source_id else ""


def item_lookup_keys(item: dict[str, Any]) -> list[str]:
    keys = item.get("review_row_keys")
    if isinstance(keys, list):
        result = [str(key).strip() for key in keys if str(key).strip()]
        if result:
            return list(dict.fromkeys(result))
    key = item_lookup_key(item)
    return [key] if key else []


def lane_apply_base_command(args: argparse.Namespace, manifest_path: Path, template: Path, out: Path) -> str:
    parts = [
        "murmurmark",
        "review",
        "lane",
        "apply",
        shlex.quote(str(read_json(manifest_path).get("lane") or "unknown")),
        "--manifest",
        shell_path(manifest_path),
        "--template",
        shell_path(template),
        "--decisions-out",
        shell_path(out),
    ]
    if args.answers_file:
        parts.extend(["--answers-file", shell_path(args.answers_file.expanduser())])
    elif args.answers:
        parts.extend(["--answers", shlex.quote(str(args.answers))])
    if args.reviewer:
        parts.extend(["--reviewer", shlex.quote(str(args.reviewer))])
    return " ".join(parts)


def lane_apply_handoff(
    *,
    args: argparse.Namespace,
    manifest_path: Path,
    template: Path,
    out: Path,
    report_path: Path,
    summary: dict[str, Any],
    rejected: list[dict[str, Any]],
) -> dict[str, Any]:
    base = lane_apply_base_command(args, manifest_path, template, out)
    batch_command = (
        "murmurmark review apply "
        f"--decisions {shell_path(out)} "
        f"--review-template {shell_path(template)}"
    )
    next_commands: list[dict[str, str]] = []
    if args.dry_run:
        if rejected or summary.get("todo_count") or not summary.get("reviewed_count"):
            if args.answers_file:
                next_commands.append(
                    command_item(
                        "edit_review_lane_answers",
                        f"$EDITOR {shell_path(args.answers_file.expanduser())}",
                        "finish manual answers before applying",
                    )
                )
            next_commands.append(command_item("retry_review_lane_dry_run", f"{base} --dry-run", "rerun lane apply validation"))
        else:
            next_commands.append(command_item("apply_review_lane_answers", base, "apply validated lane answers"))
    else:
        next_commands.append(command_item("refresh_review_progress", "murmurmark review progress", "refresh review progress"))
        next_commands.append(command_item("apply_review_decisions", batch_command, "materialize reviewed decisions into transcript profile"))
    open_commands = [
        command_item("open_review_lane_apply_report", f"less {shell_path(report_path)}", "inspect lane apply report"),
        command_item("open_review_lane_manifest", f"less {shell_path(manifest_path)}", "inspect lane pack manifest"),
    ]
    if args.answers_file:
        open_commands.append(
            command_item("edit_review_lane_answers", f"$EDITOR {shell_path(args.answers_file.expanduser())}", "edit manual answers")
        )
    return {
        "recommended_next": next_commands[0]["command"] if next_commands else f"less {shell_path(report_path)}",
        "next_commands": next_commands,
        "open_commands": open_commands,
    }


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
        source_ids = item.get("source_audit_ids") if isinstance(item.get("source_audit_ids"), list) else [source_id]
        item_keys = item_lookup_keys(item)
        row_indexes = [lookup.get(key) for key in item_keys]
        missing_keys = [key for key, row_index in zip(item_keys, row_indexes) if row_index is None]
        if missing_keys:
            rejected.append(
                {
                    "source_audit_id": source_id,
                    "source_audit_ids": source_ids,
                    "decision": decision,
                    "reason": "missing_template_row",
                    "missing_review_row_keys": missing_keys,
                }
            )
            continue
        concrete_indexes = [row_index for row_index in row_indexes if row_index is not None]
        invalid_rows = [
            {
                "review_row_key": review_row_key(rows[row_index]),
                "source_audit_id": rows[row_index].get("source_audit_id"),
                "allowed_decisions": sorted(allowed_decisions(rows[row_index])),
            }
            for row_index in concrete_indexes
            if decision not in {"", "todo"} and decision not in allowed_decisions(rows[row_index])
        ]
        if invalid_rows:
            rejected.append(
                {
                    "source_audit_id": source_id,
                    "source_audit_ids": source_ids,
                    "decision": decision,
                    "reason": "decision_not_allowed_for_row",
                    "invalid_rows": invalid_rows,
                }
            )
            continue
        for row_index in concrete_indexes:
            row = rows[row_index]
            if decision in {"", "todo"}:
                if not is_reviewed(row):
                    row["decision"] = "todo"
                    row["status"] = "todo"
            else:
                row["decision"] = decision
                row["status"] = "reviewed"
                row["reviewed_at"] = now
                row["review_source"] = "lane_pack"
                row["review_lane_pack"] = str(manifest_path)
                row["review_lane_pack_index"] = item.get("index")
                if item.get("grouped"):
                    row["review_lane_pack_group_size"] = item.get("group_size")
                if args.reviewer:
                    row["reviewer"] = args.reviewer
            rows[row_index] = row
            applied.append(
                {
                    "source_audit_id": row.get("source_audit_id") or source_id,
                    "index": item.get("index"),
                    "answer_decision": decision,
                    "decision": row.get("decision"),
                    "status": row["status"],
                    "review_row_key": review_row_key(row),
                }
            )

    report_path = out.with_name("review_lane_pack_apply_report.json")
    summary = {
        "manifest_items": len(items),
        "answer_count": len(decisions),
        "applied_count": len(applied),
        "rejected_count": len(rejected),
        "reviewed_count": sum(1 for row in applied if row.get("status") == "reviewed"),
        "todo_count": sum(1 for row in applied if row.get("status") == "todo"),
    }
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
        "summary": summary,
        "applied": applied,
        "rejected": rejected,
    }
    report.update(
        lane_apply_handoff(
            args=args,
            manifest_path=manifest_path,
            template=template,
            out=out,
            report_path=report_path,
            summary=summary,
            rejected=rejected,
        )
    )

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
