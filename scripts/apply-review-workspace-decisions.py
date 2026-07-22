#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shlex
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_VERSION = "0.6.2"
SCHEMA = "murmurmark.review_workspace_apply_report/v1"
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

SAFE_SUGGESTED_DECISION_CLASSES = {
    "keep_me": [
        "confirmed local Me speech",
        "confirmed timing/double-talk where both sides are real",
    ],
    "drop_me": [
        "confirmed remote duplicate after safety gates",
        "confirmed short ASR noise after safety gates",
    ],
    "needs_review": [
        "uncertain audio evidence",
        "local recall risk",
        "transcript order risk without matching high-confidence judge evidence",
        "any case where keep/drop evidence conflicts",
    ],
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
    parser.add_argument(
        "--answers-source",
        choices=["review", "suggested"],
        default="review",
        help="Use edited review answer sheets or generated suggested answer sheets.",
    )
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="Write reviewed rows even when some workspace answers are still todo.",
    )
    parser.add_argument("--require-complete", action="store_true", help="Fail if any workspace answer is still todo.")
    parser.add_argument("--dry-run", action="store_true", help="Validate and print a report without writing --out.")
    parser.add_argument("--quiet", action="store_true", help="Write outputs and suppress human-readable stdout.")
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


def obsolete_audit_only_local_recall_keep(row: dict[str, Any]) -> bool:
    return str(row.get("source") or "") == "local_recall" and str(row.get("decision") or "") == "keep_me"


def merge_existing(template_rows: list[dict[str, Any]], existing_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    existing_rows = [row for row in existing_rows if not obsolete_audit_only_local_recall_keep(row)]
    existing_by_key = {
        review_row_key(row): row
        for row in existing_rows
        if str(row.get("decision") or "todo") not in {"", "todo"}
    }
    template_keys = {review_row_key(row) for row in template_rows}
    merged = [{**row, **existing_by_key.get(review_row_key(row), {})} for row in template_rows]
    merged.extend(
        row
        for row in existing_rows
        if review_row_key(row) not in template_keys and str(row.get("decision") or "todo") not in {"", "todo"}
    )
    return merged


def allowed_decisions(row: dict[str, Any]) -> set[str]:
    values = row.get("allowed_decisions")
    if not isinstance(values, list) or not values:
        return set(DEFAULT_ALLOWED_DECISIONS)
    allowed = {str(value) for value in values if str(value) in KNOWN_REVIEW_DECISIONS}
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


def path_from_value(value: Any) -> Path:
    return Path(str(value or "")).expanduser()


def workspace_lane_inputs(workspace: dict[str, Any], answers_source: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    answer_key = "suggested_answer_sheet" if answers_source == "suggested" else "answer_sheet"
    for lane in workspace.get("lanes") or []:
        if not isinstance(lane, dict) or lane.get("status") != "ok":
            continue
        manifest = lane.get("manifest")
        answer_sheet = lane.get(answer_key)
        if not manifest or not answer_sheet:
            result.append({"lane": lane.get("lane"), "error": f"missing_manifest_or_{answer_key}"})
            continue
        result.append(
            {
                "lane": lane.get("lane"),
                "manifest": path_from_value(manifest),
                "answer_sheet": path_from_value(answer_sheet),
                "answer_key": answer_key,
            }
        )
    return result


def workspace_apply_base_command(args: argparse.Namespace, workspace_path: Path, template: Path, out: Path) -> str:
    base = (
        "murmurmark review workspace apply "
        f"--workspace {shell_path(workspace_path)} "
        f"--template {shell_path(template)} "
        f"--out {shell_path(out)} "
        f"--report {shell_path(args.report.expanduser())}"
    )
    if args.answers_source == "suggested":
        base += " --answers-source suggested"
    if args.reviewer:
        base += f" --reviewer {shlex.quote(str(args.reviewer))}"
    if args.require_complete:
        base += " --require-complete"
    if args.allow_partial:
        base += " --allow-partial"
    return base


def session_arg_from_workspace(workspace_path: Path) -> str | None:
    plan = workspace_path.expanduser().resolve().parent
    if plan.name != "review-plan":
        return None
    readiness = plan.parent
    derived = readiness.parent
    session = derived.parent
    if readiness.name != "readiness" or derived.name != "derived" or not session.name:
        return None
    return display_path(session)


def workspace_apply_handoff(
    *,
    args: argparse.Namespace,
    workspace_path: Path,
    template: Path,
    out: Path,
    report_path: Path,
    summary: dict[str, Any],
    lanes: list[dict[str, Any]],
    errors: list[dict[str, Any]],
) -> dict[str, Any]:
    base = workspace_apply_base_command(args, workspace_path, template, out)
    progress_command = f"murmurmark review progress --template {shell_path(template)} --decisions {shell_path(out)}"
    batch_apply_command = f"murmurmark review apply --decisions {shell_path(out)} --review-template {shell_path(template)}"
    session_arg = session_arg_from_workspace(workspace_path)
    status_command = f"murmurmark status {shlex.quote(session_arg)}" if session_arg else "murmurmark status"
    report_command = f"murmurmark report {shlex.quote(session_arg)}" if session_arg else "murmurmark report corpus"
    first_todo_lane = next(
        (
            lane
            for lane in lanes
            if safe_int((lane.get("summary") or {}).get("todo_count")) > 0
        ),
        None,
    )
    next_commands: list[dict[str, str]] = []
    can_write_partial = args.allow_partial and not errors and safe_int(summary.get("reviewed_count")) > 0
    total_rows = safe_int(summary.get("total_rows"))
    if total_rows == 0:
        return {
            "recommended_next": status_command,
            "next_commands": [
                command_item("status_session", status_command, "review workspace has no actionable rows"),
                command_item("report_session", report_command, "refresh readiness before finish/export"),
            ],
            "open_commands": [
                command_item("open_review_workspace_apply_report", f"less {shell_path(report_path)}", "inspect workspace apply report"),
                command_item("open_review_workspace", f"less {shell_path(workspace_path)}", "inspect review workspace JSON"),
            ],
        }
    if not lanes and safe_int(summary.get("remaining_rows")) > 0:
        review_workspace_command = (
            f"murmurmark review workspace --session {shlex.quote(session_arg)}"
            if session_arg
            else "murmurmark review workspace"
        )
        return {
            "recommended_next": review_workspace_command,
            "next_commands": [
                command_item("rebuild_review_workspace", review_workspace_command, "workspace has remaining rows but no usable lane reports"),
                command_item("status_session", status_command, "inspect current readiness"),
            ],
            "open_commands": [
                command_item("open_review_workspace_apply_report", f"less {shell_path(report_path)}", "inspect workspace apply report"),
                command_item("open_review_workspace", f"less {shell_path(workspace_path)}", "inspect review workspace JSON"),
            ],
        }
    if args.dry_run:
        if first_todo_lane and can_write_partial:
            next_commands.append(command_item("apply_review_workspace_partial", base, "apply reviewed suggested rows and leave todo rows open"))
            next_commands.append(command_item("refresh_review_progress", progress_command, "show exact manual review remainder"))
            next_commands.append(command_item("apply_partial_review_decisions", f"{batch_apply_command} --allow-partial-review", "refresh reviewed transcript profile with closed rows"))
            if first_todo_lane.get("markdown"):
                next_commands.append(
                    command_item(
                        "open_first_incomplete_lane_pack",
                        f"less {shell_path(Path(str(first_todo_lane.get('markdown'))))}",
                        "inspect evidence for the first remaining manual lane",
                    )
                )
        elif first_todo_lane:
            if args.answers_source == "suggested" and first_todo_lane.get("markdown"):
                next_commands.append(
                    command_item(
                        "open_first_manual_review_lane",
                        f"less {shell_path(Path(str(first_todo_lane.get('markdown'))))}",
                        "generated suggestions left todo rows; inspect this lane manually",
                    )
                )
            elif first_todo_lane.get("answer_sheet"):
                next_commands.append(
                    command_item(
                        "edit_workspace_lane_answers",
                        f"$EDITOR {shell_path(Path(str(first_todo_lane.get('answer_sheet'))))}",
                        "finish the first incomplete lane answer sheet",
                    )
                )
            if first_todo_lane.get("markdown"):
                next_commands.append(
                    command_item(
                        "open_workspace_lane_pack",
                        f"less {shell_path(Path(str(first_todo_lane.get('markdown'))))}",
                        "inspect evidence for the first incomplete lane",
                    )
                )
            next_commands.append(command_item("retry_review_workspace_dry_run", f"{base} --dry-run", "rerun workspace validation"))
        elif errors:
            next_commands.append(command_item("open_review_workspace_apply_report", f"less {shell_path(report_path)}", "inspect validation errors"))
            next_commands.append(command_item("retry_review_workspace_dry_run", f"{base} --dry-run", "rerun workspace validation"))
        else:
            next_commands.append(command_item("apply_review_workspace", base, "apply validated workspace answers"))
    elif summary.get("ready_for_batch_apply"):
        next_commands.append(command_item("apply_review_decisions", batch_apply_command, "materialize reviewed decisions into transcript profile"))
    elif summary.get("ready_for_partial_apply"):
        next_commands.append(command_item("refresh_review_progress", progress_command, "show exact manual review remainder"))
        next_commands.append(command_item("apply_partial_review_decisions", f"{batch_apply_command} --allow-partial-review", "refresh reviewed transcript profile with closed rows"))
    elif args.answers_source == "suggested" and first_todo_lane:
        if first_todo_lane.get("markdown"):
            next_commands.append(
                command_item(
                    "open_first_manual_review_lane",
                    f"less {shell_path(Path(str(first_todo_lane.get('markdown'))))}",
                    "no safe suggestions were applied; inspect the first remaining manual lane",
                )
            )
        next_commands.append(command_item("refresh_review_progress", progress_command, "show exact manual review remainder"))
    else:
        next_commands.append(command_item("refresh_review_progress", progress_command, "refresh review progress"))
    open_commands = [
        command_item("open_review_workspace_apply_report", f"less {shell_path(report_path)}", "inspect workspace apply report"),
        command_item("open_review_workspace", f"less {shell_path(workspace_path)}", "inspect review workspace JSON"),
    ]
    return {
        "recommended_next": next_commands[0]["command"] if next_commands else f"less {shell_path(report_path)}",
        "next_commands": next_commands,
        "open_commands": open_commands,
    }


def row_seconds(row: dict[str, Any]) -> float:
    interval = row.get("interval") if isinstance(row.get("interval"), dict) else {}
    try:
        return max(0.0, float(interval.get("duration_sec") or 0.0))
    except (TypeError, ValueError):
        return 0.0


def row_lane(row: dict[str, Any]) -> str:
    return str(row.get("review_lane") or "unknown")


def row_label(row: dict[str, Any]) -> str:
    return str(row.get("label") or "unknown")


def is_reviewed(row: dict[str, Any]) -> bool:
    return str(row.get("decision") or "todo") not in {"", "todo"}


def reviewed_transition(before: dict[str, Any], after: dict[str, Any]) -> bool:
    return not is_reviewed(before) and is_reviewed(after)


def decision_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    return dict(sorted(Counter(str(row.get("decision") or "todo") for row in rows).items()))


def row_seconds_sum(rows: list[dict[str, Any]]) -> float:
    return round(sum(row_seconds(row) for row in rows), 3)


def bucket_rows(rows: list[dict[str, Any]], key_fn) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        buckets.setdefault(str(key_fn(row)), []).append(row)
    result: list[dict[str, Any]] = []
    for key, bucket in sorted(buckets.items()):
        result.append(
            {
                "key": key,
                "count": len(bucket),
                "seconds": row_seconds_sum(bucket),
            }
        )
    return result


def bucket_records(records: list[dict[str, Any]], key_name: str) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        buckets.setdefault(str(record.get(key_name) or "unknown"), []).append(record)
    result: list[dict[str, Any]] = []
    for key, bucket in sorted(buckets.items()):
        result.append(
            {
                "key": key,
                "count": sum(safe_int(item.get("rows")) or 1 for item in bucket),
                "items": len(bucket),
                "seconds": round(sum(float(item.get("seconds") or 0.0) for item in bucket), 3),
            }
        )
    return result


def suggested_records_from_lane_reports(lane_reports: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for lane in lane_reports or []:
        for item in lane.get("suggested_decision_items") or []:
            if isinstance(item, dict):
                records.append(item)
    return records


def suggested_decision_summary(lane_reports: list[dict[str, Any]] | None) -> dict[str, Any]:
    records = suggested_records_from_lane_reports(lane_reports)
    actionable = [record for record in records if str(record.get("decision") or "") in {"keep_me", "drop_me"}]
    needs_review = [record for record in records if str(record.get("decision") or "") == "needs_review"]
    todo = [record for record in records if str(record.get("decision") or "") in {"todo", ""}]
    return {
        "rows": sum(safe_int(record.get("rows")) or 1 for record in records),
        "items": len(records),
        "seconds": round(sum(float(record.get("seconds") or 0.0) for record in records), 3),
        "actionable_rows": sum(safe_int(record.get("rows")) or 1 for record in actionable),
        "actionable_seconds": round(sum(float(record.get("seconds") or 0.0) for record in actionable), 3),
        "needs_review_rows": sum(safe_int(record.get("rows")) or 1 for record in needs_review),
        "needs_review_seconds": round(sum(float(record.get("seconds") or 0.0) for record in needs_review), 3),
        "todo_rows": sum(safe_int(record.get("rows")) or 1 for record in todo),
        "todo_seconds": round(sum(float(record.get("seconds") or 0.0) for record in todo), 3),
        "by_decision": bucket_records(records, "decision"),
    }


def closure_item(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "review_row_key": review_row_key(row),
        "source_audit_id": row.get("source_audit_id"),
        "review_lane": row.get("review_lane"),
        "label": row.get("label"),
        "decision": row.get("decision") or "todo",
        "seconds": round(row_seconds(row), 3),
        "reason": row.get("review_suggested_decision_reason")
        or row.get("suggested_decision_reason")
        or row.get("review_reason")
        or "",
        "confidence": row.get("review_suggested_decision_confidence")
        or row.get("suggested_decision_confidence"),
        "evidence": row.get("review_evidence") or {},
    }


def suggested_closure_summary(
    before_rows: list[dict[str, Any]],
    after_rows: list[dict[str, Any]],
    answers_source: str,
    lane_reports: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    before_remaining = [row for row in before_rows if not is_reviewed(row)]
    after_remaining = [row for row in after_rows if not is_reviewed(row)]
    before_by_key = {review_row_key(row): row for row in before_rows}
    closed_rows = [
        after
        for after in after_rows
        if (before := before_by_key.get(review_row_key(after))) is not None
        and reviewed_transition(before, after)
    ]
    suggested_closed = [
        row for row in closed_rows if str(row.get("review_source") or "") == "workspace_suggested_answer_sheet"
    ]
    suggested_by_decision = bucket_rows(suggested_closed, lambda row: row.get("decision") or "todo")
    remaining_by_lane = bucket_rows(after_remaining, row_lane)
    remaining_by_label = bucket_rows(after_remaining, row_label)
    if suggested_closed:
        status = "partial_apply_ready" if after_remaining else "ready_for_review_apply"
    elif after_remaining:
        status = "manual_review_required"
    else:
        status = "already_closed"
    before_seconds = row_seconds_sum(before_remaining)
    after_seconds = row_seconds_sum(after_remaining)
    if suggested_closed and after_remaining:
        readiness_effect = "manual_review_reduced"
        projected_after = "review_required"
    elif suggested_closed:
        readiness_effect = "all_suggested_rows_closed"
        projected_after = "review_apply_ready"
    elif after_remaining:
        readiness_effect = "no_safe_closure"
        projected_after = "review_required"
    else:
        readiness_effect = "already_closed"
        projected_after = "review_closed"
    return {
        "schema": "murmurmark.suggested_review_closure/v1",
        "answers_source": answers_source,
        "status": status,
        "before": {
            "manual_rows": len(before_remaining),
            "manual_seconds": before_seconds,
            "decisions": decision_counts(before_rows),
        },
        "after": {
            "manual_rows": len(after_remaining),
            "manual_seconds": after_seconds,
            "decisions": decision_counts(after_rows),
        },
        "readiness_projection": {
            "before_state": "review_required" if before_remaining else "review_closed",
            "after_state": projected_after,
            "effect": readiness_effect,
            "manual_rows_delta": len(after_remaining) - len(before_remaining),
            "manual_seconds_delta": round(after_seconds - before_seconds, 3),
            "requires_review_apply": projected_after == "review_apply_ready",
            "requires_manual_review": bool(after_remaining),
        },
        "closed_by_suggestions": {
            "rows": len(suggested_closed),
            "seconds": row_seconds_sum(suggested_closed),
            "by_decision": suggested_by_decision,
            "items": [closure_item(row) for row in suggested_closed[:50]],
        },
        "generated_suggestions": suggested_decision_summary(lane_reports),
        "remaining_manual_queue": {
            "rows": len(after_remaining),
            "seconds": row_seconds_sum(after_remaining),
            "by_lane": remaining_by_lane,
            "by_label": remaining_by_label,
            "items": [closure_item(row) for row in after_remaining[:50]],
        },
        "safe_decision_classes": SAFE_SUGGESTED_DECISION_CLASSES,
    }


def me_utterance_ids(row: dict[str, Any]) -> list[str]:
    values = row.get("me_utterance_ids")
    if isinstance(values, list):
        return [str(value) for value in values if str(value)]
    return []


def resolve_suggested_drop_conflicts(rows: list[dict[str, Any]], now: str) -> list[dict[str, Any]]:
    """Generated suggestions must not force-drop an utterance that another row asks to review."""
    by_utterance: dict[str, list[int]] = {}
    for index, row in enumerate(rows):
        if not is_reviewed(row):
            continue
        for utterance_id in me_utterance_ids(row):
            by_utterance.setdefault(utterance_id, []).append(index)

    downgraded: list[dict[str, Any]] = []
    for utterance_id, indexes in by_utterance.items():
        decisions = {str(rows[index].get("decision") or "") for index in indexes}
        if "drop_me" not in decisions or not (decisions - {"drop_me"}):
            continue
        for index in indexes:
            row = rows[index]
            if row.get("decision") != "drop_me":
                continue
            if "needs_review" not in allowed_decisions(row):
                continue
            row["decision"] = "needs_review"
            row["status"] = "reviewed"
            row["reviewed_at"] = now
            row["suggested_conflict_resolution"] = "drop_me_downgraded_to_needs_review"
            rows[index] = row
            downgraded.append(
                {
                    "review_row_key": review_row_key(row),
                    "source_audit_id": row.get("source_audit_id"),
                    "session_id": row.get("session_id"),
                    "utterance_id": utterance_id,
                    "from": "drop_me",
                    "to": "needs_review",
                    "reason": "suggested decisions conflicted for the same Me utterance",
                }
            )
    return downgraded


def apply_lane(
    lane_input: dict[str, Any],
    rows: list[dict[str, Any]],
    lookup: dict[str, int],
    now: str,
    reviewer: str,
    answers_source: str,
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
    suggested_items: list[dict[str, Any]] = []

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
            "answer_key": lane_input.get("answer_key"),
            "summary": {"applied_count": 0, "reviewed_count": 0, "todo_count": 0, "rejected_count": len(rejected)},
            "rejected": rejected,
        }

    manifest = read_json(manifest_path)
    manifest_outputs = manifest.get("outputs") if isinstance(manifest.get("outputs"), dict) else {}
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
            "markdown": manifest_outputs.get("markdown"),
            "answer_sheet": str(answer_sheet),
            "answer_key": lane_input.get("answer_key"),
            "summary": {"applied_count": 0, "reviewed_count": 0, "todo_count": 0, "rejected_count": len(rejected)},
            "rejected": rejected,
        }

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
        suggested_decision = str(item.get("suggested_decision") or "todo")
        if suggested_decision not in VALID_DECISIONS:
            suggested_decision = "todo"
        suggested_items.append(
            {
                "index": item.get("index"),
                "source_audit_id": source_id,
                "source_audit_ids": source_ids,
                "decision": suggested_decision,
                "confidence": item.get("suggested_decision_confidence"),
                "reason": item.get("suggested_decision_reason") or "",
                "rows": len(concrete_indexes),
                "seconds": round(sum(row_seconds(rows[row_index]) for row_index in concrete_indexes), 3),
                "allowed_decisions": item.get("allowed_decisions"),
            }
        )
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
                stronger_summary = item.get("stronger_audio_judge") if isinstance(item.get("stronger_audio_judge"), dict) else {}
                target_me_summary = item.get("target_me") if isinstance(item.get("target_me"), dict) else {}
                row["decision"] = decision
                row["status"] = "reviewed"
                row["reviewed_at"] = now
                row["review_source"] = (
                    "workspace_suggested_answer_sheet" if answers_source == "suggested" else "workspace_answer_sheet"
                )
                row["review_workspace_lane"] = lane
                row["review_lane_pack"] = str(manifest_path)
                row["review_lane_pack_index"] = item.get("index")
                if item.get("grouped"):
                    row["review_lane_pack_group_size"] = item.get("group_size")
                row["review_reason"] = item.get("suggested_decision_reason") or ""
                row["review_evidence"] = {
                    "source_audit_ids": source_ids,
                    "review_lane": lane,
                    "lane_pack_index": item.get("index"),
                    "suggested_decision": item.get("suggested_decision"),
                    "suggested_decision_confidence": item.get("suggested_decision_confidence"),
                    "suggested_decision_reason": item.get("suggested_decision_reason"),
                    "allowed_decisions": item.get("allowed_decisions"),
                    "stronger_audio_judge": {
                        "count": stronger_summary.get("count"),
                        "labels": stronger_summary.get("labels"),
                        "max_confidence": stronger_summary.get("max_confidence"),
                    },
                    "target_me": {
                        "count": target_me_summary.get("count"),
                        "labels": target_me_summary.get("labels"),
                        "impacts": target_me_summary.get("impacts"),
                        "max_confidence": target_me_summary.get("max_confidence"),
                    },
                }
                if answers_source == "suggested":
                    row["review_suggested_decision"] = item.get("suggested_decision")
                    row["review_suggested_decision_confidence"] = item.get("suggested_decision_confidence")
                    row["review_suggested_decision_reason"] = item.get("suggested_decision_reason")
                if reviewer:
                    row["reviewer"] = reviewer
            rows[row_index] = row
            applied.append(
                {
                    "source_audit_id": row.get("source_audit_id") or source_id,
                    "index": item.get("index"),
                    "answer_decision": decision,
                    "decision": row.get("decision"),
                    "status": row["status"],
                    "review_row_key": review_row_key(row),
                    "reason": row.get("review_reason") or row.get("suggested_decision_reason") or "",
                    "evidence": row.get("review_evidence") or {},
                }
            )

    return {
        "lane": lane,
        "status": "failed" if rejected else "ok",
        "manifest": str(manifest_path),
        "markdown": manifest_outputs.get("markdown"),
        "answer_sheet": str(answer_sheet),
        "answer_key": lane_input.get("answer_key"),
        "summary": {
            "manifest_items": len(items),
            "answer_count": len(decisions),
            "skipped_count": skipped_count,
            "applied_count": len(applied),
            "reviewed_count": sum(1 for row in applied if row.get("status") == "reviewed"),
            "todo_count": sum(1 for row in applied if row.get("status") == "todo"),
            "rejected_count": len(rejected),
        },
        "suggested_decision_items": suggested_items,
        "suggested_decision_summary": suggested_decision_summary([{"suggested_decision_items": suggested_items}]),
        "rejected": rejected,
    }


def main() -> int:
    args = parse_args()
    workspace_path = args.workspace.expanduser()
    template = args.template.expanduser()
    out = args.out.expanduser()
    workspace = read_json(workspace_path)
    rows = merge_existing(read_jsonl(template), read_jsonl(out))
    before_rows = [dict(row) for row in rows]
    lookup = row_lookup(rows)
    now = datetime.now(timezone.utc).isoformat()
    lanes = workspace_lane_inputs(workspace, args.answers_source)
    lane_reports = [apply_lane(lane, rows, lookup, now, args.reviewer, args.answers_source) for lane in lanes]
    suggested_conflict_downgrades = (
        resolve_suggested_drop_conflicts(rows, now) if args.answers_source == "suggested" else []
    )
    rejected = [row for lane in lane_reports for row in lane.get("rejected") or []]
    decision_counts = Counter(str(row.get("decision") or "todo") for row in rows)
    workspace_todo_count = sum(safe_int((lane.get("summary") or {}).get("todo_count")) for lane in lane_reports)
    complete_error = args.require_complete and workspace_todo_count > 0
    remaining_rows = [row for row in rows if not is_reviewed(row)]
    newly_reviewed_count = sum(safe_int((lane.get("summary") or {}).get("reviewed_count")) for lane in lane_reports)
    can_write_partial = args.allow_partial and not rejected and not complete_error and newly_reviewed_count > 0
    ready_for_batch_apply = len(rows) > 0 and not remaining_rows and not rejected and not complete_error
    ready_for_partial_apply = can_write_partial and bool(remaining_rows)

    summary = {
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
        "ready_for_batch_apply": ready_for_batch_apply,
        "ready_for_partial_apply": ready_for_partial_apply,
        "partial_apply_allowed": args.allow_partial,
        "decisions": dict(sorted(decision_counts.items())),
        "suggested_conflict_downgraded_count": len(suggested_conflict_downgrades),
    }
    suggested_closure = suggested_closure_summary(before_rows, rows, args.answers_source, lane_reports)
    errors = rejected + ([{"reason": "workspace_answers_incomplete", "todo_count": workspace_todo_count}] if complete_error else [])
    report_path = args.report.expanduser()
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
        "answers_source": args.answers_source,
        "require_complete": args.require_complete,
        "allow_partial": args.allow_partial,
        "summary": summary,
        "suggested_closure": suggested_closure,
        "lanes": lane_reports,
        "suggested_conflict_downgrades": suggested_conflict_downgrades,
        "errors": errors,
    }
    report.update(
        workspace_apply_handoff(
            args=args,
            workspace_path=workspace_path,
            template=template,
            out=out,
            report_path=report_path,
            summary=summary,
            lanes=lane_reports,
            errors=errors,
        )
    )

    can_write_incomplete_manual = args.answers_source == "review"
    if not args.dry_run and not rejected and not complete_error and (
        not remaining_rows or can_write_partial or can_write_incomplete_manual
    ):
        write_jsonl(out, rows)
    write_json(report_path, report)

    if not args.quiet:
        print(json.dumps(report["summary"], ensure_ascii=False))
        if report["errors"]:
            print(f"Errors: {len(report['errors'])}")
            for row in report["errors"][:20]:
                print(f"- {row.get('source_audit_id') or row.get('lane') or 'workspace'}: {row.get('reason')}")
        if args.dry_run:
            print(f"Report: {args.report}")
            print("Dry run: no decisions file written.")
        elif rejected or complete_error or (remaining_rows and not args.allow_partial):
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
