#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shlex
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_VERSION = "0.1.0"
SCHEMA = "murmurmark.review_workspace_apply_report/v1"
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
    parser = argparse.ArgumentParser(description="Apply all MurmurMark review workspace answer sheets.")
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path("sessions/_reports/review-plan/review_workspace.json"),
        help="Input review_workspace.json.",
    )
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
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("sessions/_reports/review-plan/review_workspace_apply_report.json"),
        help="Output apply report JSON.",
    )
    parser.add_argument("--reviewer", default="", help="Reviewer name written to decided rows.")
    parser.add_argument("--require-complete", action="store_true", help="Fail if any workspace answer is still todo.")
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


def path_from_value(value: Any) -> Path:
    return Path(str(value or "")).expanduser()


def workspace_lane_inputs(workspace: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for lane in workspace.get("lanes") or []:
        if not isinstance(lane, dict) or lane.get("status") != "ok":
            continue
        manifest = lane.get("manifest")
        answer_sheet = lane.get("answer_sheet")
        if not manifest or not answer_sheet:
            result.append({"lane": lane.get("lane"), "error": "missing_manifest_or_answer_sheet"})
            continue
        result.append(
            {
                "lane": lane.get("lane"),
                "manifest": path_from_value(manifest),
                "answer_sheet": path_from_value(answer_sheet),
            }
        )
    return result


def row_seconds(row: dict[str, Any]) -> float:
    interval = row.get("interval") if isinstance(row.get("interval"), dict) else {}
    try:
        return max(0.0, float(interval.get("duration_sec") or 0.0))
    except (TypeError, ValueError):
        return 0.0


def is_reviewed(row: dict[str, Any]) -> bool:
    return str(row.get("decision") or "todo") not in {"", "todo"}


def apply_lane(
    lane_input: dict[str, Any],
    rows: list[dict[str, Any]],
    lookup: dict[str, int],
    now: str,
    reviewer: str,
) -> dict[str, Any]:
    lane = str(lane_input.get("lane") or "unknown")
    if lane_input.get("error"):
        return {
            "lane": lane,
            "status": "failed",
            "error": lane_input.get("error"),
            "summary": {"applied_count": 0, "reviewed_count": 0, "todo_count": 0, "rejected_count": 1},
            "rejected": [{"reason": lane_input.get("error")}],
        }
    manifest_path = path_from_value(lane_input.get("manifest"))
    answer_sheet = path_from_value(lane_input.get("answer_sheet"))
    rejected: list[dict[str, Any]] = []
    applied: list[dict[str, Any]] = []

    if not manifest_path.exists():
        rejected.append({"lane": lane, "reason": "missing_manifest", "path": str(manifest_path)})
    if not answer_sheet.exists():
        rejected.append({"lane": lane, "reason": "missing_answer_sheet", "path": str(answer_sheet)})
    if rejected:
        return {
            "lane": lane,
            "status": "failed",
            "manifest": str(manifest_path),
            "answer_sheet": str(answer_sheet),
            "summary": {"applied_count": 0, "reviewed_count": 0, "todo_count": 0, "rejected_count": len(rejected)},
            "rejected": rejected,
        }

    manifest = read_json(manifest_path)
    items = manifest_items(manifest)
    manifest_summary = manifest.get("summary") if isinstance(manifest.get("summary"), dict) else {}
    skipped_count = safe_int(manifest_summary.get("skipped_count"))
    answers_raw = read_answers_file(answer_sheet)
    decisions = parse_answers(answers_raw)
    if len(decisions) != len(items):
        rejected.append(
            {
                "lane": lane,
                "reason": "answers_length_mismatch",
                "answer_count": len(decisions),
                "item_count": len(items),
            }
        )
        return {
            "lane": lane,
            "status": "failed",
            "manifest": str(manifest_path),
            "answer_sheet": str(answer_sheet),
            "summary": {"applied_count": 0, "reviewed_count": 0, "todo_count": 0, "rejected_count": len(rejected)},
            "rejected": rejected,
        }

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
            row["review_source"] = "workspace_answer_sheet"
            row["review_workspace_lane"] = lane
            row["review_lane_pack"] = str(manifest_path)
            row["review_lane_pack_index"] = item.get("index")
            if reviewer:
                row["reviewer"] = reviewer
        rows[row_index] = row
        applied.append(
            {
                "source_audit_id": source_id,
                "index": item.get("index"),
                "decision": decision,
                "status": row["status"],
            }
        )

    return {
        "lane": lane,
        "status": "failed" if rejected else "ok",
        "manifest": str(manifest_path),
        "answer_sheet": str(answer_sheet),
            "summary": {
                "manifest_items": len(items),
                "answer_count": len(decisions),
                "skipped_count": skipped_count,
                "applied_count": len(applied),
                "reviewed_count": sum(1 for row in applied if row.get("status") == "reviewed"),
                "todo_count": sum(1 for row in applied if row.get("status") == "todo"),
            "rejected_count": len(rejected),
        },
        "rejected": rejected,
    }


def main() -> int:
    args = parse_args()
    workspace_path = args.workspace.expanduser()
    template = args.template.expanduser()
    out = args.out.expanduser()
    workspace = read_json(workspace_path)
    rows = merge_existing(read_jsonl(template), read_jsonl(out))
    lookup = row_lookup(rows)
    now = datetime.now(timezone.utc).isoformat()
    lanes = workspace_lane_inputs(workspace)
    lane_reports = [apply_lane(lane, rows, lookup, now, args.reviewer) for lane in lanes]
    rejected = [row for lane in lane_reports for row in lane.get("rejected") or []]
    remaining_rows = [row for row in rows if not is_reviewed(row)]
    decision_counts = Counter(str(row.get("decision") or "todo") for row in rows)
    workspace_todo_count = sum(safe_int((lane.get("summary") or {}).get("todo_count")) for lane in lane_reports)
    complete_error = args.require_complete and workspace_todo_count > 0

    report = {
        "schema": SCHEMA,
        "generated_at": now,
        "generator": {"name": "apply-review-workspace-decisions", "version": SCRIPT_VERSION},
        "inputs": {
            "workspace": str(workspace_path),
            "template": str(template),
            "out": str(out),
        },
        "dry_run": args.dry_run,
        "require_complete": args.require_complete,
        "summary": {
            "lane_count": len(lane_reports),
            "failed_lanes": sum(1 for lane in lane_reports if lane.get("status") != "ok"),
            "skipped_count": sum(safe_int((lane.get("summary") or {}).get("skipped_count")) for lane in lane_reports),
            "applied_count": sum(safe_int((lane.get("summary") or {}).get("applied_count")) for lane in lane_reports),
            "reviewed_count": sum(safe_int((lane.get("summary") or {}).get("reviewed_count")) for lane in lane_reports),
            "workspace_todo_count": workspace_todo_count,
            "rejected_count": len(rejected),
            "total_rows": len(rows),
            "remaining_rows": len(remaining_rows),
            "remaining_seconds": round(sum(row_seconds(row) for row in remaining_rows), 3),
            "remaining_minutes": round(sum(row_seconds(row) for row in remaining_rows) / 60.0, 2),
            "ready_for_batch_apply": len(rows) > 0 and not remaining_rows and not rejected and not complete_error,
            "decisions": dict(sorted(decision_counts.items())),
        },
        "lanes": lane_reports,
        "errors": rejected + ([{"reason": "workspace_answers_incomplete", "todo_count": workspace_todo_count}] if complete_error else []),
    }

    if not args.dry_run and not rejected and not complete_error:
        write_jsonl(out, rows)
    if not args.dry_run:
        write_json(args.report.expanduser(), report)

    print(json.dumps(report["summary"], ensure_ascii=False))
    if report["errors"]:
        print(f"Errors: {len(report['errors'])}")
        for row in report["errors"][:20]:
            print(f"- {row.get('source_audit_id') or row.get('lane') or 'workspace'}: {row.get('reason')}")
    if args.dry_run:
        print("Dry run: no files written.")
    elif rejected or complete_error:
        print(f"Report: {args.report}")
        print("No decisions file written because validation failed.")
    else:
        print(f"Written: {out}")
        print(f"Report: {args.report}")
        print(
            "Next: .venv/bin/python scripts/report-review-decisions-progress.py "
            f"--decisions {shlex.quote(str(out))}"
        )
    return 1 if rejected or complete_error else 0


def safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
