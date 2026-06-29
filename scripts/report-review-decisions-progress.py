#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_VERSION = "0.3.0"
SCHEMA = "murmurmark.review_decisions_progress/v1"
VALID_DECISIONS = {"drop_me", "drop_remote", "keep_me", "needs_review", "skip", "todo", ""}
KNOWN_REVIEW_DECISIONS = {"drop_me", "drop_remote", "keep_me", "needs_review", "skip"}
DEFAULT_ALLOWED_DECISIONS = {"drop_me", "keep_me", "needs_review", "skip"}
GROUPABLE_REVIEW_LANES = {"check_transcript_order", "check_unique_me_content", "classify_audio"}
CROSS_LANE_RELATED_LANES = {"check_unique_me_content", "classify_audio"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Report progress for MurmurMark review_decisions.jsonl.")
    parser.add_argument(
        "--template",
        type=Path,
        default=Path("sessions/_reports/review-plan/review_decisions.template.jsonl"),
        help="Input review_decisions.template.jsonl.",
    )
    parser.add_argument(
        "--decisions",
        type=Path,
        default=Path("sessions/_reports/review-plan/review_decisions.jsonl"),
        help="Editable review decisions JSONL.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("sessions/_reports/review-plan/review_decisions_progress.json"),
        help="Output JSON report.",
    )
    parser.add_argument(
        "--markdown",
        type=Path,
        default=Path("sessions/_reports/review-plan/review_decisions_progress.md"),
        help="Output Markdown report.",
    )
    return parser.parse_args()


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


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize_text(value: Any) -> str:
    return " ".join(str(value or "").lower().replace("ё", "е").split())


def review_row_key(row: dict[str, Any]) -> str:
    source_id = str(row.get("source_audit_id") or "").strip()
    cluster_id = str(row.get("cluster_id") or "").strip()
    utterance_ids = row.get("utterance_ids")
    utterance_key = ",".join(str(item) for item in utterance_ids) if isinstance(utterance_ids, list) else ""
    interval = row.get("interval") if isinstance(row.get("interval"), dict) else {}
    return (
        "review:"
        f"{source_id}:"
        f"{row.get('session_id') or ''}:"
        f"{cluster_id}:"
        f"{utterance_key}:"
        f"{interval.get('start')}:{interval.get('end')}:"
        f"{row.get('label')}"
    )


def merge_existing(template_rows: list[dict[str, Any]], existing_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    existing_by_key = {review_row_key(row): row for row in existing_rows}
    return [{**row, **existing_by_key.get(review_row_key(row), {})} for row in template_rows]


def allowed_decisions(row: dict[str, Any]) -> set[str]:
    values = row.get("allowed_decisions")
    if not isinstance(values, list) or not values:
        return set(DEFAULT_ALLOWED_DECISIONS)
    allowed = {str(value) for value in values if str(value) in KNOWN_REVIEW_DECISIONS}
    return allowed or set(DEFAULT_ALLOWED_DECISIONS)


def normalized_decision(row: dict[str, Any]) -> str:
    decision = str(row.get("decision") or "todo").strip()
    return decision if decision else "todo"


def is_reviewed(row: dict[str, Any]) -> bool:
    return normalized_decision(row) not in {"todo", ""}


def row_seconds(row: dict[str, Any]) -> float:
    interval = row.get("interval") if isinstance(row.get("interval"), dict) else {}
    try:
        return max(0.0, float(interval.get("duration_sec") or 0.0))
    except (TypeError, ValueError):
        return 0.0


def item_list_values(item: dict[str, Any], key: str) -> list[str]:
    values = item.get(key)
    if isinstance(values, list):
        return [str(value) for value in values if value is not None and str(value)]
    return []


def output_allowed_decisions(item: dict[str, Any]) -> list[str]:
    values = item.get("allowed_decisions")
    if isinstance(values, list) and values:
        return [str(value) for value in values if value is not None and str(value)]
    return sorted(DEFAULT_ALLOWED_DECISIONS)


def first_me_utterance_id(item: dict[str, Any]) -> str:
    me_ids = item_list_values(item, "me_utterance_ids")
    if me_ids:
        return me_ids[0]
    utterance_ids = item_list_values(item, "utterance_ids")
    return utterance_ids[0] if utterance_ids else ""


def me_utterance_group_key(item: dict[str, Any]) -> str:
    me_ids = item_list_values(item, "me_utterance_ids")
    if me_ids:
        return ",".join(me_ids)
    return first_me_utterance_id(item)


def review_group_key(item: dict[str, Any]) -> str:
    lane = str(item.get("review_lane") or "")
    if lane in CROSS_LANE_RELATED_LANES:
        me_key = me_utterance_group_key(item)
        if me_key:
            session_id = str(item.get("session_id") or item.get("session") or "")
            return f"cross_lane_me_audio:{session_id}:{me_key}"
    if lane not in GROUPABLE_REVIEW_LANES:
        return ""
    me_key = me_utterance_group_key(item)
    if not me_key:
        return ""
    session_id = str(item.get("session_id") or item.get("session") or "")
    action = str(item.get("review_action") or "")
    if lane == "check_unique_me_content":
        return f"{lane}:{session_id}:{action}:{me_key}"
    label = str(item.get("label") or "")
    allowed = ",".join(sorted(output_allowed_decisions(item)))
    return f"{lane}:{session_id}:{label}:{action}:{allowed}:{me_key}"


def review_action_groups(items: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    by_key: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        key = review_group_key(item)
        if not key:
            groups.append([item])
            continue
        group = by_key.get(key)
        if group is None:
            group = []
            by_key[key] = group
            groups.append(group)
        group.append(item)
    return groups


def action_progress(rows: list[dict[str, Any]]) -> dict[str, Any]:
    groups = review_action_groups(rows)
    reviewed_actions = sum(1 for group in groups if all(is_reviewed(row) for row in group))
    remaining_actions = len(groups) - reviewed_actions
    return {
        "action_count": len(groups),
        "reviewed_actions": reviewed_actions,
        "remaining_actions": remaining_actions,
        "grouped_review_row_count": sum(max(0, len(group) - 1) for group in groups),
        "remaining_grouped_review_row_count": sum(
            max(0, len(group) - 1)
            for group in groups
            if not all(is_reviewed(row) for row in group)
        ),
    }


def validate_row(row: dict[str, Any]) -> list[str]:
    decision = normalized_decision(row)
    errors: list[str] = []
    if decision not in VALID_DECISIONS:
        errors.append("unknown_decision")
    if decision not in {"", "todo"} and decision not in allowed_decisions(row):
        errors.append("decision_not_allowed_for_row")
    return errors


def progress_bucket(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row.get(key) or "unknown")].append(row)
    result: list[dict[str, Any]] = []
    for name, group in sorted(groups.items()):
        decisions = Counter(normalized_decision(row) for row in group)
        reviewed = sum(1 for row in group if is_reviewed(row))
        total = len(group)
        seconds = sum(row_seconds(row) for row in group)
        remaining_seconds = sum(row_seconds(row) for row in group if not is_reviewed(row))
        actions = action_progress(group)
        result.append(
            {
                key: name,
                "total": total,
                "reviewed": reviewed,
                "remaining": total - reviewed,
                **actions,
                "reviewed_ratio": round(reviewed / total, 6) if total else 0.0,
                "seconds": round(seconds, 3),
                "remaining_seconds": round(remaining_seconds, 3),
                "decisions": dict(sorted(decisions.items())),
            }
        )
    return result


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    template_rows = read_jsonl(args.template.expanduser())
    existing_rows = read_jsonl(args.decisions.expanduser())
    rows = merge_existing(template_rows, existing_rows)
    errors: list[dict[str, Any]] = []
    for row in rows:
        row_errors = validate_row(row)
        if row_errors:
            errors.append(
                {
                    "source_audit_id": row.get("source_audit_id"),
                    "session_id": row.get("session_id"),
                    "review_lane": row.get("review_lane"),
                    "decision": normalized_decision(row),
                    "errors": row_errors,
                    "allowed_decisions": sorted(allowed_decisions(row)),
                }
            )

    total = len(rows)
    reviewed = sum(1 for row in rows if is_reviewed(row))
    remaining = total - reviewed
    total_seconds = sum(row_seconds(row) for row in rows)
    remaining_seconds = sum(row_seconds(row) for row in rows if not is_reviewed(row))
    decisions = Counter(normalized_decision(row) for row in rows)
    actions = action_progress(rows)
    return {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": {"name": "report-review-decisions-progress", "version": SCRIPT_VERSION},
        "inputs": {
            "template": str(args.template),
            "decisions": str(args.decisions) if args.decisions.exists() else None,
        },
        "summary": {
            "total": total,
            "reviewed": reviewed,
            "remaining": remaining,
            **actions,
            "reviewed_ratio": round(reviewed / total, 6) if total else 0.0,
            "seconds": round(total_seconds, 3),
            "remaining_seconds": round(remaining_seconds, 3),
            "remaining_minutes": round(remaining_seconds / 60.0, 2),
            "invalid_rows": len(errors),
            "ready_for_batch_apply": total > 0 and remaining == 0 and not errors,
            "has_partial_decisions": reviewed > 0 and remaining > 0,
            "decisions": dict(sorted(decisions.items())),
        },
        "by_lane": progress_bucket(rows, "review_lane"),
        "by_session": progress_bucket(rows, "session_id"),
        "errors": errors,
    }


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    summary = report["summary"]
    lines = [
        "# MurmurMark Review Decisions Progress",
        "",
        f"- Reviewed: `{summary['reviewed']}/{summary['total']}`",
        f"- Remaining: `{summary['remaining']}` items / `{summary['remaining_minutes']}` min raw audio",
        f"- Review actions: `{summary['reviewed_actions']}/{summary['action_count']}` reviewed, `{summary['remaining_actions']}` remaining",
        f"- Grouped review rows: `{summary['grouped_review_row_count']}` saved, `{summary['remaining_grouped_review_row_count']}` still open",
        f"- Invalid rows: `{summary['invalid_rows']}`",
        f"- Ready for batch apply: `{summary['ready_for_batch_apply']}`",
        f"- Decisions: `{summary['decisions']}`",
        "",
        "## By Lane",
        "",
        "| Lane | Rows Reviewed | Actions Remaining | Remaining sec | Decisions |",
        "|---|---:|---:|---:|---|",
    ]
    for row in report.get("by_lane") or []:
        lines.append(
            f"| `{row.get('review_lane')}` | {row.get('reviewed')}/{row.get('total')} | "
            f"{row.get('remaining_actions')} | {row.get('remaining_seconds')} | `{row.get('decisions')}` |"
        )
    lines.extend(["", "## By Session", "", "| Session | Rows Reviewed | Actions Remaining | Remaining sec | Decisions |", "|---|---:|---:|---:|---|"])
    for row in report.get("by_session") or []:
        lines.append(
            f"| `{row.get('session_id')}` | {row.get('reviewed')}/{row.get('total')} | "
            f"{row.get('remaining_actions')} | {row.get('remaining_seconds')} | `{row.get('decisions')}` |"
        )
    if report.get("errors"):
        lines.extend(["", "## Errors", ""])
        for row in report["errors"]:
            lines.append(
                f"- `{row.get('session_id')}` `{row.get('source_audit_id')}` "
                f"decision `{row.get('decision')}`: {', '.join(row.get('errors') or [])}"
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    report = build_report(args)
    write_json(args.out.expanduser(), report)
    write_markdown(args.markdown.expanduser(), report)
    print(f"review_decisions_progress: {args.out}")
    print(
        "reviewed={reviewed}/{total} remaining={remaining} invalid={invalid_rows}".format(
            **report["summary"]
        )
    )
    return 1 if report["summary"]["invalid_rows"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
