#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_REPORT = "murmurmark.local_recall_corpus_report/v1"
SCHEMA_ITEM = "murmurmark.local_recall_corpus_item/v1"
SCRIPT_VERSION = "0.1.0"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aggregate local-recall audits across a session corpus.")
    parser.add_argument("sessions", nargs="*", type=Path, help="Session directories. Defaults to sessions from --session-quality.")
    parser.add_argument("--session-quality", type=Path, default=Path("sessions/_reports/session-quality/session_quality_report.json"))
    parser.add_argument("--out-dir", type=Path, default=Path("sessions/_reports/local-recall"))
    parser.add_argument("--max-review-items", type=int, default=40)
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return rows
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def session_id_from_path(path: Path) -> str:
    return path.name


def session_rows(session_quality: dict[str, Any] | None, explicit_sessions: list[Path]) -> list[dict[str, Any]]:
    quality_rows = [
        row
        for row in ((session_quality or {}).get("sessions") or [])
        if isinstance(row, dict) and row.get("session")
    ]
    by_session_id = {str(row.get("session_id") or ""): row for row in quality_rows if row.get("session_id")}
    by_session_path = {str(Path(str(row.get("session")))): row for row in quality_rows}
    if explicit_sessions:
        result: list[dict[str, Any]] = []
        for session in explicit_sessions:
            session_id = session_id_from_path(session)
            row = by_session_id.get(session_id) or by_session_path.get(str(session))
            if isinstance(row, dict):
                result.append({**row, "session": str(session), "session_id": row.get("session_id") or session_id})
            else:
                result.append({"session_id": session_id, "session": str(session), "selected_profile": None, "pipeline_status": None, "use_gate": None})
        return result
    return list(quality_rows)


def has_metric(row: dict[str, Any], key: str) -> bool:
    return key in row and row.get(key) is not None


def effective_local_recall_metrics(session_row: dict[str, Any], audit_summary: dict[str, Any]) -> dict[str, Any]:
    possible_count = (
        safe_int(session_row.get("local_recall_possible_lost_me_count"))
        if has_metric(session_row, "local_recall_possible_lost_me_count")
        else safe_int(audit_summary.get("possible_lost_me_count"))
    )
    possible_seconds = (
        safe_float(session_row.get("local_recall_possible_lost_me_seconds"))
        if has_metric(session_row, "local_recall_possible_lost_me_seconds")
        else safe_float(audit_summary.get("possible_lost_me_seconds"))
    )
    needs_count = (
        safe_int(session_row.get("local_recall_needs_review_count"))
        if has_metric(session_row, "local_recall_needs_review_count")
        else safe_int(audit_summary.get("needs_review_count"))
    )
    needs_seconds = (
        safe_float(session_row.get("local_recall_needs_review_seconds"))
        if has_metric(session_row, "local_recall_needs_review_seconds")
        else safe_float(audit_summary.get("needs_review_seconds"))
    )
    meaningful_seconds = (
        safe_float(session_row.get("local_recall_meaningful_review_seconds"))
        if has_metric(session_row, "local_recall_meaningful_review_seconds")
        else safe_float(audit_summary.get("meaningful_review_seconds"))
    )
    blocking = (
        bool(session_row.get("local_recall_blocking_low_local_recall"))
        if has_metric(session_row, "local_recall_blocking_low_local_recall")
        else bool(audit_summary.get("blocking_low_local_recall"))
    )
    return {
        "possible_lost_me_count": possible_count,
        "possible_lost_me_seconds": round(possible_seconds, 3),
        "needs_review_count": needs_count,
        "needs_review_seconds": round(needs_seconds, 3),
        "meaningful_review_seconds": round(meaningful_seconds, 3),
        "blocking_low_local_recall": blocking,
        "recommended_next_step": session_row.get("local_recall_recommended_next_step") or audit_summary.get("recommended_next_step"),
    }


def compact_item(row: dict[str, Any], session_row: dict[str, Any]) -> dict[str, Any]:
    session = str(session_row.get("session") or "")
    state = row.get("state") if isinstance(row.get("state"), dict) else {}
    boundary = row.get("boundary") if isinstance(row.get("boundary"), dict) else {}
    return {
        "schema": SCHEMA_ITEM,
        "session_id": str(session_row.get("session_id") or session_id_from_path(Path(session))),
        "session": session,
        "selected_profile": session_row.get("selected_profile"),
        "pipeline_status": session_row.get("pipeline_status"),
        "use_gate": session_row.get("use_gate"),
        "item_id": row.get("item_id"),
        "label": row.get("label"),
        "confidence": row.get("confidence"),
        "reason": row.get("reason"),
        "start_sec": row.get("start_sec"),
        "end_sec": row.get("end_sec"),
        "duration_sec": row.get("duration_sec"),
        "parent_candidate_id": row.get("parent_candidate_id"),
        "parent_text": row.get("parent_text"),
        "parent_has_work_marker": row.get("parent_has_work_marker"),
        "state": {
            "local_only_ratio": state.get("local_only_ratio"),
            "double_talk_ratio": state.get("double_talk_ratio"),
            "remote_active_ratio": state.get("remote_active_ratio"),
            "mic_db_mean": state.get("mic_db_mean"),
        },
        "boundary": {
            "boundary_fragment": boundary.get("boundary_fragment"),
            "near_parent_boundary": boundary.get("near_parent_boundary"),
            "adjacent_to_child": boundary.get("adjacent_to_child"),
            "adjacent_to_remote_guard": boundary.get("adjacent_to_remote_guard"),
        },
        "review_path": f"{session}/derived/audit/local-recall/local_recall_review.md" if session else None,
    }


def label_seconds(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    counts = Counter(str(item.get("label") or "unknown") for item in items)
    seconds: Counter[str] = Counter()
    for item in items:
        seconds[str(item.get("label") or "unknown")] += safe_float(item.get("duration_sec"))
    return {
        label: {"count": counts[label], "seconds": round(seconds[label], 3)}
        for label in sorted(counts)
    }


def build_report(args: argparse.Namespace) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    session_quality = read_json(args.session_quality)
    rows = session_rows(session_quality, args.sessions)
    missing: list[dict[str, Any]] = []
    session_summaries: list[dict[str, Any]] = []
    all_items: list[dict[str, Any]] = []

    for session_row in rows:
        session = Path(str(session_row.get("session") or ""))
        session_id = str(session_row.get("session_id") or session_id_from_path(session))
        audit_path = session / "derived/audit/local-recall/local_recall_audit.json"
        items_path = session / "derived/audit/local-recall/local_recall_items.jsonl"
        audit = read_json(audit_path)
        items = read_jsonl(items_path)
        if not audit:
            missing.append(
                {
                    "session_id": session_id,
                    "session": str(session),
                    "expected": str(audit_path),
                    "reason": "missing_local_recall_audit",
                }
            )
            continue
        summary = audit.get("summary") if isinstance(audit.get("summary"), dict) else {}
        metrics = effective_local_recall_metrics(session_row, summary)
        compacted = [compact_item(item, session_row) for item in items]
        all_items.extend(compacted)
        session_summaries.append(
            {
                "session_id": session_id,
                "session": str(session),
                "selected_profile": session_row.get("selected_profile") or audit.get("profile"),
                "pipeline_status": session_row.get("pipeline_status"),
                "use_gate": session_row.get("use_gate"),
                "status": audit.get("status"),
                "audit_missing_island_count": safe_int(summary.get("audited_missing_island_count")),
                "audit_possible_lost_me_count": safe_int(summary.get("possible_lost_me_count")),
                "audit_possible_lost_me_seconds": round(safe_float(summary.get("possible_lost_me_seconds")), 3),
                "audit_needs_review_count": safe_int(summary.get("needs_review_count")),
                "audit_needs_review_seconds": round(safe_float(summary.get("needs_review_seconds")), 3),
                "audit_likely_harmless_seconds": round(safe_float(summary.get("likely_harmless_seconds")), 3),
                **metrics,
                "report_path": str(session / "derived/audit/local-recall/local_recall_review.md"),
            }
        )

    all_items.sort(
        key=lambda item: (
            str(item.get("label")) not in {"possible_lost_me", "needs_review"},
            str(item.get("label")) != "possible_lost_me",
            -safe_float(item.get("duration_sec")),
            str(item.get("session_id") or ""),
        )
    )
    blocking = [row for row in session_summaries if row.get("blocking_low_local_recall")]
    complete_blocking = [
        row
        for row in blocking
        if row.get("pipeline_status") == "complete" or row.get("use_gate") in {"ready_for_notes", "review_first"}
    ]
    active_blocking_sessions = {str(row.get("session_id") or "") for row in blocking}
    review_items = [
        item
        for item in all_items
        if item.get("label") in {"possible_lost_me", "needs_review"}
        and str(item.get("session_id") or "") in active_blocking_sessions
    ][: max(0, args.max_review_items)]
    effective_by_label = {
        "possible_lost_me": {
            "count": sum(safe_int(row.get("possible_lost_me_count")) for row in session_summaries),
            "seconds": round(sum(safe_float(row.get("possible_lost_me_seconds")) for row in session_summaries), 3),
        },
        "needs_review": {
            "count": sum(safe_int(row.get("needs_review_count")) for row in session_summaries),
            "seconds": round(sum(safe_float(row.get("needs_review_seconds")) for row in session_summaries), 3),
        },
        "meaningful_review": {
            "seconds": round(sum(safe_float(row.get("meaningful_review_seconds")) for row in session_summaries), 3),
        },
    }
    likely_harmless_seconds = round(sum(safe_float(row.get("audit_likely_harmless_seconds")) for row in session_summaries), 3)
    report = {
        "schema": SCHEMA_REPORT,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": {"name": "report-local-recall-corpus", "version": SCRIPT_VERSION},
        "inputs": {
            "session_quality": str(args.session_quality),
            "sessions": [str(path) for path in args.sessions] if args.sessions else "from_session_quality",
        },
        "summary": {
            "session_count": len(rows),
            "audited_session_count": len(session_summaries),
            "missing_local_recall_audit_count": len(missing),
            "item_count": len(all_items),
            "audit_by_label": label_seconds(all_items),
            "by_label": effective_by_label,
            "blocking_session_count": len(blocking),
            "complete_blocking_session_count": len(complete_blocking),
            "possible_lost_me_count": effective_by_label["possible_lost_me"]["count"],
            "possible_lost_me_seconds": effective_by_label["possible_lost_me"]["seconds"],
            "needs_review_count": effective_by_label["needs_review"]["count"],
            "needs_review_seconds": effective_by_label["needs_review"]["seconds"],
            "meaningful_review_seconds": effective_by_label["meaningful_review"]["seconds"],
            "likely_harmless_seconds": likely_harmless_seconds,
            "recommended_next_step": (
                "review_complete_local_recall_items"
                if complete_blocking
                else ("review_incomplete_local_recall_items" if blocking else "keep_local_recall_audit_in_corpus_loop")
            ),
        },
        "sessions": sorted(
            session_summaries,
            key=lambda row: (
                not bool(row.get("blocking_low_local_recall")),
                -safe_float(row.get("meaningful_review_seconds")),
                -safe_float(row.get("audit_likely_harmless_seconds")),
                str(row.get("session_id") or ""),
            ),
        ),
        "missing_local_recall_audits": missing,
        "review_items": review_items,
        "next_commands": build_next_commands(complete_blocking, blocking),
    }
    return report, all_items


def build_next_commands(complete_blocking: list[dict[str, Any]], blocking: list[dict[str, Any]]) -> list[dict[str, Any]]:
    commands: list[dict[str, Any]] = []
    review_rows = sorted(
        complete_blocking,
        key=lambda row: (-safe_float(row.get("meaningful_review_seconds")), str(row.get("session_id") or "")),
    )
    for row in review_rows:
        session = str(row.get("session") or "")
        session_id = str(row.get("session_id") or Path(session).name)
        if not session:
            continue
        commands.append(
            {
                "id": f"review_local_recall_{session_id}",
                "label": f"Review local-recall items for {session_id}.",
                "command": f"murmurmark review lane check_local_recall --session {command_path(session)}",
                "session_id": session_id,
                "session": session,
            }
        )
    if commands:
        return commands
    incomplete_rows = sorted(
        blocking,
        key=lambda row: (-safe_float(row.get("meaningful_review_seconds")), str(row.get("session_id") or "")),
    )
    for row in incomplete_rows[:1]:
        session = str(row.get("session") or "")
        session_id = str(row.get("session_id") or Path(session).name)
        if not session:
            continue
        commands.append(
            {
                "id": f"process_local_recall_{session_id}",
                "label": f"Complete the pipeline before reviewing local-recall items for {session_id}.",
                "command": f"murmurmark process {command_path(session)}",
                "session_id": session_id,
                "session": session,
            }
        )
    return commands


def command_path(value: str) -> str:
    path = Path(value)
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return value


def format_time(seconds: float) -> str:
    total = max(0, int(seconds))
    return f"{total // 60:02d}:{total % 60:02d}"


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    lines = [
        "# Local Recall Corpus Report",
        "",
        f"- Sessions: `{summary.get('audited_session_count')}` / `{summary.get('session_count')}` audited",
        f"- Missing local-recall audit: `{summary.get('missing_local_recall_audit_count')}`",
        f"- Blocking sessions: `{summary.get('blocking_session_count')}`",
        f"- Complete blocking sessions: `{summary.get('complete_blocking_session_count')}`",
        f"- Possible lost Me: `{summary.get('possible_lost_me_count')}` / `{summary.get('possible_lost_me_seconds')}` sec",
        f"- Needs review: `{summary.get('needs_review_count')}` / `{summary.get('needs_review_seconds')}` sec",
        f"- Likely harmless: `{summary.get('likely_harmless_seconds')}` sec",
        f"- Next: `{summary.get('recommended_next_step')}`",
        "",
    ]
    sessions = [row for row in report.get("sessions", []) if isinstance(row, dict) and row.get("blocking_low_local_recall")]
    if sessions:
        lines += ["## Blocking Sessions", ""]
        for row in sessions:
            lines.append(
                f"- `{row.get('session_id')}` `{row.get('selected_profile')}` "
                f"possible `{row.get('possible_lost_me_seconds')}`s, review `{row.get('needs_review_seconds')}`s, "
                f"gate `{row.get('use_gate')}`, report `less {row.get('report_path')}`"
            )
        lines.append("")
    next_commands = [row for row in report.get("next_commands", []) if isinstance(row, dict)]
    if next_commands:
        lines += ["## Next Commands", ""]
        for row in next_commands[:10]:
            lines.append(f"- {row.get('label')} `{row.get('command')}`")
        lines.append("")
    items = report.get("review_items") if isinstance(report.get("review_items"), list) else []
    if items:
        lines += ["## Review Items", ""]
        for item in items[:25]:
            start = format_time(safe_float(item.get("start_sec")))
            end = format_time(safe_float(item.get("end_sec")))
            lines += [
                f"### `{item.get('session_id')}` `{item.get('item_id')}` `{item.get('label')}` {start}-{end}",
                "",
                f"- Confidence: `{item.get('confidence')}`",
                f"- Reason: {item.get('reason')}",
                f"- Parent candidate: `{item.get('parent_candidate_id')}`",
                f"- Parent text: {item.get('parent_text')}",
                f"- Review: `less {item.get('review_path')}`",
                "",
            ]
    else:
        lines += ["No local-recall review items found.", ""]
    missing = report.get("missing_local_recall_audits") if isinstance(report.get("missing_local_recall_audits"), list) else []
    if missing:
        lines += ["## Missing Audits", ""]
        for row in missing[:25]:
            lines.append(f"- `{row.get('session_id')}`: `{row.get('expected')}`")
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    report, items = build_report(args)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    write_json(args.out_dir / "local_recall_corpus_report.json", report)
    write_jsonl(args.out_dir / "local_recall_corpus_items.jsonl", items)
    write_markdown(args.out_dir / "local_recall_corpus_report.md", report)
    summary = report["summary"]
    print(f"local_recall_corpus: {args.out_dir / 'local_recall_corpus_report.json'}")
    print(f"audited_sessions: {summary['audited_session_count']} / {summary['session_count']}")
    print(f"blocking_sessions: {summary['blocking_session_count']}")
    print(f"possible_lost_me_seconds: {summary['possible_lost_me_seconds']}")
    print(f"needs_review_seconds: {summary['needs_review_seconds']}")
    print(f"recommended_next_step: {summary['recommended_next_step']}")
    next_commands = [row for row in report.get("next_commands", []) if isinstance(row, dict)]
    if next_commands:
        print(f"next_command: {next_commands[0].get('command')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
