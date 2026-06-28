#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_REPORT = "murmurmark.transcript_order_corpus_report/v1"
SCHEMA_ITEM = "murmurmark.transcript_order_corpus_item/v1"
SCRIPT_VERSION = "0.2.0"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aggregate transcript order audits across a session corpus.")
    parser.add_argument("sessions", nargs="*", type=Path, help="Session directories. Defaults to sessions from --session-quality.")
    parser.add_argument("--session-quality", type=Path, default=Path("sessions/_reports/session-quality/session_quality_report.json"))
    parser.add_argument("--out-dir", type=Path, default=Path("sessions/_reports/transcript-order"))
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
        if not line.strip():
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
                continue
            result.append(
                {
                    "session_id": session_id,
                    "session": str(session),
                    "selected_profile": None,
                    "pipeline_status": None,
                    "use_gate": None,
                }
            )
        return result
    result: list[dict[str, Any]] = []
    for row in quality_rows:
        result.append(row)
    return result


def label_seconds(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    counts = Counter(str(item.get("label") or "unknown") for item in items)
    seconds: Counter[str] = Counter()
    for item in items:
        interval = item.get("interval") if isinstance(item.get("interval"), dict) else {}
        seconds[str(item.get("label") or "unknown")] += safe_float(interval.get("duration_sec"))
    return {
        label: {"count": counts[label], "seconds": round(seconds[label], 3)}
        for label in sorted(counts)
    }


def has_metric(row: dict[str, Any], key: str) -> bool:
    return key in row and row.get(key) is not None


def effective_order_metrics(session_row: dict[str, Any], audit_summary: dict[str, Any]) -> dict[str, Any]:
    probable_count = (
        safe_int(session_row.get("transcript_order_probable_order_risk_count"))
        if has_metric(session_row, "transcript_order_probable_order_risk_count")
        else safe_int(audit_summary.get("probable_order_risk_count"))
    )
    probable_seconds = (
        safe_float(session_row.get("transcript_order_probable_order_risk_seconds"))
        if has_metric(session_row, "transcript_order_probable_order_risk_seconds")
        else safe_float(audit_summary.get("probable_order_risk_seconds"))
    )
    needs_count = (
        safe_int(session_row.get("transcript_order_needs_review_count"))
        if has_metric(session_row, "transcript_order_needs_review_count")
        else safe_int(audit_summary.get("needs_review_count"))
    )
    needs_seconds = (
        safe_float(session_row.get("transcript_order_review_seconds"))
        if has_metric(session_row, "transcript_order_review_seconds")
        else safe_float(audit_summary.get("needs_review_seconds"))
    )
    blocking = (
        bool(session_row.get("transcript_order_blocking_order_risk"))
        if has_metric(session_row, "transcript_order_blocking_order_risk")
        else bool(audit_summary.get("blocking_order_risk"))
    )
    return {
        "probable_order_risk_count": probable_count,
        "probable_order_risk_seconds": round(probable_seconds, 3),
        "needs_review_count": needs_count,
        "needs_review_seconds": round(needs_seconds, 3),
        "blocking_order_risk": blocking,
        "recommended_next_step": session_row.get("transcript_order_recommended_next_step") or audit_summary.get("recommended_next_step"),
    }


def compact_item(row: dict[str, Any], session_row: dict[str, Any]) -> dict[str, Any]:
    interval = row.get("interval") if isinstance(row.get("interval"), dict) else {}
    utterances = row.get("utterances") if isinstance(row.get("utterances"), dict) else {}
    me = utterances.get("me") if isinstance(utterances.get("me"), dict) else {}
    remote = utterances.get("remote") if isinstance(utterances.get("remote"), dict) else {}
    session = str(session_row.get("session") or "")
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
        "interval": interval,
        "reason": row.get("reason"),
        "utterance_ids": [item for item in [me.get("id"), remote.get("id")] if item],
        "me_text": me.get("text"),
        "remote_text": remote.get("text"),
        "features": row.get("features") if isinstance(row.get("features"), dict) else {},
        "review_path": f"{session}/derived/audit/order/transcript_order_review.md" if session else None,
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
        audit_path = session / "derived/audit/order/transcript_order_audit.json"
        items_path = session / "derived/audit/order/transcript_order_items.jsonl"
        audit = read_json(audit_path)
        items = read_jsonl(items_path)
        if not audit:
            missing.append(
                {
                    "session_id": session_id,
                    "session": str(session),
                    "expected": str(audit_path),
                    "reason": "missing_transcript_order_audit",
                }
            )
            continue
        summary = audit.get("summary") if isinstance(audit.get("summary"), dict) else {}
        metrics = effective_order_metrics(session_row, summary)
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
                "audited_overlap_count": safe_int(summary.get("audited_overlap_count")),
                "audit_probable_order_risk_count": safe_int(summary.get("probable_order_risk_count")),
                "audit_probable_order_risk_seconds": round(safe_float(summary.get("probable_order_risk_seconds")), 3),
                "audit_needs_review_count": safe_int(summary.get("needs_review_count")),
                "audit_needs_review_seconds": round(safe_float(summary.get("needs_review_seconds")), 3),
                **metrics,
                "transcript_order_repair_applied_repairs": safe_int(session_row.get("transcript_order_repair_applied_repairs")),
                "transcript_order_repair_unrepaired_order_risks": safe_int(session_row.get("transcript_order_repair_unrepaired_order_risks")),
            }
        )

    all_items.sort(
        key=lambda item: (
            str(item.get("label")) != "probable_order_risk",
            -safe_float((item.get("interval") or {}).get("duration_sec")),
            str(item.get("session_id") or ""),
        )
    )
    blocking = [row for row in session_summaries if row.get("blocking_order_risk")]
    complete_blocking = [
        row for row in blocking if row.get("pipeline_status") == "complete" or row.get("use_gate") in {"ready_for_notes", "review_first"}
    ]
    active_blocking_sessions = {str(row.get("session_id") or "") for row in blocking}
    review_items = [
        item
        for item in all_items
        if item.get("label") in {"probable_order_risk", "needs_review"}
        and str(item.get("session_id") or "") in active_blocking_sessions
    ][: max(0, args.max_review_items)]
    effective_by_label = {
        "probable_order_risk": {
            "count": sum(safe_int(row.get("probable_order_risk_count")) for row in session_summaries),
            "seconds": round(sum(safe_float(row.get("probable_order_risk_seconds")) for row in session_summaries), 3),
        },
        "needs_review": {
            "count": sum(safe_int(row.get("needs_review_count")) for row in session_summaries),
            "seconds": round(sum(safe_float(row.get("needs_review_seconds")) for row in session_summaries), 3),
        },
    }
    audit_probable_order_risk_count = sum(safe_int(row.get("audit_probable_order_risk_count")) for row in session_summaries)
    audit_probable_order_risk_seconds = round(
        sum(safe_float(row.get("audit_probable_order_risk_seconds")) for row in session_summaries),
        3,
    )
    effective_probable_order_risk_count = effective_by_label["probable_order_risk"]["count"]
    effective_probable_order_risk_seconds = effective_by_label["probable_order_risk"]["seconds"]
    repaired_sessions = [
        row
        for row in session_summaries
        if safe_int(row.get("transcript_order_repair_applied_repairs")) > 0
    ]
    repair_cleared_sessions = [
        row
        for row in repaired_sessions
        if safe_int(row.get("probable_order_risk_count")) == 0 and not row.get("blocking_order_risk")
    ]
    repair_partial_sessions = [
        row
        for row in repaired_sessions
        if safe_int(row.get("probable_order_risk_count")) > 0 or row.get("blocking_order_risk")
    ]
    repair_summary = {
        "sessions_with_repair": len(repaired_sessions),
        "cleared_session_count": len(repair_cleared_sessions),
        "partial_session_count": len(repair_partial_sessions),
        "applied_repairs": sum(safe_int(row.get("transcript_order_repair_applied_repairs")) for row in session_summaries),
        "unrepaired_order_risks": sum(safe_int(row.get("transcript_order_repair_unrepaired_order_risks")) for row in session_summaries),
        "audit_probable_order_risk_count": audit_probable_order_risk_count,
        "audit_probable_order_risk_seconds": audit_probable_order_risk_seconds,
        "effective_probable_order_risk_count": effective_probable_order_risk_count,
        "effective_probable_order_risk_seconds": effective_probable_order_risk_seconds,
        "resolved_order_risk_count": max(0, audit_probable_order_risk_count - effective_probable_order_risk_count),
        "resolved_order_risk_seconds": round(max(0.0, audit_probable_order_risk_seconds - effective_probable_order_risk_seconds), 3),
    }
    report = {
        "schema": SCHEMA_REPORT,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": {"name": "report-transcript-order-corpus", "version": SCRIPT_VERSION},
        "inputs": {
            "session_quality": str(args.session_quality),
            "sessions": [str(path) for path in args.sessions] if args.sessions else "from_session_quality",
        },
        "summary": {
            "session_count": len(rows),
            "audited_session_count": len(session_summaries),
            "missing_order_audit_count": len(missing),
            "item_count": len(all_items),
            "audit_by_label": label_seconds(all_items),
            "by_label": effective_by_label,
            "blocking_session_count": len(blocking),
            "complete_blocking_session_count": len(complete_blocking),
            "probable_order_risk_count": effective_by_label["probable_order_risk"]["count"],
            "probable_order_risk_seconds": effective_by_label["probable_order_risk"]["seconds"],
            "needs_review_count": effective_by_label["needs_review"]["count"],
            "needs_review_seconds": effective_by_label["needs_review"]["seconds"],
            "order_repair": repair_summary,
            "recommended_next_step": (
                "close_complete_order_regressions"
                if complete_blocking
                else ("review_incomplete_order_candidates" if blocking else "keep_order_audit_in_corpus_loop")
            ),
        },
        "sessions": sorted(
            session_summaries,
            key=lambda row: (
                not bool(row.get("blocking_order_risk")),
                -safe_float(row.get("probable_order_risk_seconds")),
                -safe_float(row.get("needs_review_seconds")),
                str(row.get("session_id") or ""),
            ),
        ),
        "missing_order_audits": missing,
        "review_items": review_items,
        "next_commands": build_next_commands(complete_blocking, blocking),
    }
    return report, all_items


def build_next_commands(complete_blocking: list[dict[str, Any]], blocking: list[dict[str, Any]]) -> list[dict[str, Any]]:
    commands: list[dict[str, Any]] = []
    review_rows = sorted(
        complete_blocking,
        key=lambda row: (
            -safe_float(row.get("probable_order_risk_seconds")),
            -safe_float(row.get("needs_review_seconds")),
            str(row.get("session_id") or ""),
        ),
    )
    for row in review_rows:
        session = str(row.get("session") or "")
        session_id = str(row.get("session_id") or Path(session).name)
        if not session:
            continue
        commands.append(
            {
                "id": f"review_transcript_order_{session_id}",
                "label": f"Review transcript-order risks for {session_id}.",
                "command": f"murmurmark review lane check_transcript_order --session {command_path(session)}",
                "session_id": session_id,
                "session": session,
            }
        )
    if commands:
        return commands
    incomplete_rows = sorted(
        blocking,
        key=lambda row: (
            -safe_float(row.get("probable_order_risk_seconds")),
            -safe_float(row.get("needs_review_seconds")),
            str(row.get("session_id") or ""),
        ),
    )
    for row in incomplete_rows[:1]:
        session = str(row.get("session") or "")
        session_id = str(row.get("session_id") or Path(session).name)
        if not session:
            continue
        commands.append(
            {
                "id": f"process_transcript_order_{session_id}",
                "label": f"Complete the pipeline before reviewing transcript-order risks for {session_id}.",
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
        "# Transcript Order Corpus Report",
        "",
        f"- Sessions: `{summary.get('audited_session_count')}` / `{summary.get('session_count')}` audited",
        f"- Missing order audit: `{summary.get('missing_order_audit_count')}`",
        f"- Blocking sessions: `{summary.get('blocking_session_count')}`",
        f"- Complete blocking sessions: `{summary.get('complete_blocking_session_count')}`",
        f"- Probable order risk: `{summary.get('probable_order_risk_count')}` / `{summary.get('probable_order_risk_seconds')}` sec",
        f"- Needs review: `{summary.get('needs_review_count')}` / `{summary.get('needs_review_seconds')}` sec",
        f"- Next: `{summary.get('recommended_next_step')}`",
        "",
    ]
    repair = summary.get("order_repair") if isinstance(summary.get("order_repair"), dict) else {}
    if repair:
        lines += [
            "## Order Repair Effect",
            "",
            f"- Sessions with repair: `{repair.get('sessions_with_repair')}`",
            f"- Cleared sessions: `{repair.get('cleared_session_count')}`",
            f"- Partial sessions: `{repair.get('partial_session_count')}`",
            f"- Applied repairs: `{repair.get('applied_repairs')}`",
            f"- Unrepaired order risks: `{repair.get('unrepaired_order_risks')}`",
            f"- Resolved order risk: `{repair.get('resolved_order_risk_count')}` / `{repair.get('resolved_order_risk_seconds')}` sec",
            "",
        ]
    sessions = [row for row in report.get("sessions", []) if isinstance(row, dict) and row.get("blocking_order_risk")]
    if sessions:
        lines += ["## Blocking Sessions", ""]
        for row in sessions:
            lines.append(
                f"- `{row.get('session_id')}` `{row.get('selected_profile')}` "
                f"risk `{row.get('probable_order_risk_seconds')}`s, review `{row.get('needs_review_seconds')}`s, "
                f"gate `{row.get('use_gate')}`"
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
            interval = item.get("interval") if isinstance(item.get("interval"), dict) else {}
            start = format_time(safe_float(interval.get("start")))
            end = format_time(safe_float(interval.get("end")))
            lines += [
                f"### `{item.get('session_id')}` `{item.get('item_id')}` `{item.get('label')}` {start}-{end}",
                "",
                f"- Reason: {item.get('reason')}",
                f"- Me: {item.get('me_text')}",
                f"- Colleagues: {item.get('remote_text')}",
                f"- Review: `less {item.get('review_path')}`",
                "",
            ]
    else:
        lines += ["No transcript-order review items found.", ""]
    missing = report.get("missing_order_audits") if isinstance(report.get("missing_order_audits"), list) else []
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
    write_json(args.out_dir / "transcript_order_corpus_report.json", report)
    write_jsonl(args.out_dir / "transcript_order_corpus_items.jsonl", items)
    write_markdown(args.out_dir / "transcript_order_corpus_report.md", report)
    summary = report["summary"]
    print(f"transcript_order_corpus: {args.out_dir / 'transcript_order_corpus_report.json'}")
    print(f"audited_sessions: {summary['audited_session_count']} / {summary['session_count']}")
    print(f"blocking_sessions: {summary['blocking_session_count']}")
    print(f"probable_order_risk_seconds: {summary['probable_order_risk_seconds']}")
    print(f"recommended_next_step: {summary['recommended_next_step']}")
    next_commands = [row for row in report.get("next_commands", []) if isinstance(row, dict)]
    if next_commands:
        print(f"next_command: {next_commands[0].get('command')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
