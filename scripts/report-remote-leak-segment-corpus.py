#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_REPORT = "murmurmark.remote_leak_segment_corpus_report/v1"
SCHEMA_ITEM = "murmurmark.remote_leak_segment_corpus_item/v1"
SCRIPT_VERSION = "0.1.0"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aggregate remote-leak segment repair plans across a session corpus.")
    parser.add_argument("sessions", nargs="*", type=Path, help="Session directories. Defaults to sessions from --session-quality.")
    parser.add_argument("--session-quality", type=Path, default=Path("sessions/_reports/session-quality/session_quality_report.json"))
    parser.add_argument("--out-dir", type=Path, default=Path("sessions/_reports/remote-leak-segment"))
    parser.add_argument("--max-items", type=int, default=60)
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
                result.append({"session_id": session_id, "session": str(session), "selected_profile": None, "pipeline_status": None})
        return result
    return list(quality_rows)


def compact_item(item: dict[str, Any], session_row: dict[str, Any]) -> dict[str, Any]:
    interval = item.get("interval") if isinstance(item.get("interval"), dict) else {}
    diagnostic = item.get("diagnostic") if isinstance(item.get("diagnostic"), dict) else {}
    proposal = item.get("proposal") if isinstance(item.get("proposal"), dict) else {}
    evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
    text = evidence.get("text") if isinstance(evidence.get("text"), dict) else {}
    session = str(session_row.get("session") or "")
    return {
        "schema": SCHEMA_ITEM,
        "session_id": str(session_row.get("session_id") or session_id_from_path(Path(session))),
        "session": session,
        "selected_profile": session_row.get("selected_profile"),
        "pipeline_status": session_row.get("pipeline_status"),
        "use_gate": session_row.get("use_gate"),
        "item_id": item.get("id"),
        "source_audit_id": item.get("source_audit_id"),
        "diagnostic": diagnostic.get("label"),
        "protect_local_content": bool(diagnostic.get("protect_local_content")),
        "proposal": proposal.get("action"),
        "future_patch_type": proposal.get("future_patch_type"),
        "whole_me_drop_allowed": bool(proposal.get("whole_me_drop_allowed")),
        "interval": interval,
        "utterance_ids": item.get("utterance_ids") or [],
        "me_text": text.get("me_text"),
        "remote_text": text.get("remote_text"),
        "content_tokens": text.get("content_tokens") or [],
        "domain_terms": text.get("domain_terms") or [],
        "report_path": f"{session}/derived/transcript-simple/whisper-cpp/remote-leak-repair/remote_leak_segment_repair.md"
        if session
        else None,
    }


def by_diagnostic(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    counts = Counter(str(item.get("diagnostic") or "unknown") for item in items)
    seconds: Counter[str] = Counter()
    protected: Counter[str] = Counter()
    for item in items:
        label = str(item.get("diagnostic") or "unknown")
        seconds[label] += safe_float((item.get("interval") or {}).get("duration_sec"))
        if item.get("protect_local_content"):
            protected[label] += 1
    return {
        label: {"count": counts[label], "seconds": round(seconds[label], 3), "protect_local_content_count": protected[label]}
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
        plan_path = session / "derived/transcript-simple/whisper-cpp/remote-leak-repair/remote_leak_segment_repair_plan.json"
        items_path = session / "derived/transcript-simple/whisper-cpp/remote-leak-repair/remote_leak_segment_repair_items.jsonl"
        plan = read_json(plan_path)
        items = read_jsonl(items_path)
        if not plan:
            missing.append({"session_id": session_id, "session": str(session), "expected": str(plan_path), "reason": "missing_remote_leak_segment_plan"})
            continue
        summary = plan.get("summary") if isinstance(plan.get("summary"), dict) else {}
        compacted = [compact_item(item, session_row) for item in items]
        all_items.extend(compacted)
        session_summaries.append(
            {
                "session_id": session_id,
                "session": str(session),
                "selected_profile": session_row.get("selected_profile"),
                "pipeline_status": session_row.get("pipeline_status"),
                "use_gate": session_row.get("use_gate"),
                "items": safe_int(summary.get("items")),
                "seconds": round(safe_float(summary.get("seconds")), 3),
                "protect_local_content_items": safe_int(summary.get("protect_local_content_items")),
                "protect_local_content_seconds": round(safe_float(summary.get("protect_local_content_seconds")), 3),
                "by_diagnostic": summary.get("by_diagnostic") if isinstance(summary.get("by_diagnostic"), dict) else {},
                "report_path": str(session / "derived/transcript-simple/whisper-cpp/remote-leak-repair/remote_leak_segment_repair.md"),
            }
        )

    all_items.sort(
        key=lambda item: (
            not bool(item.get("protect_local_content")),
            -safe_float((item.get("interval") or {}).get("duration_sec")),
            str(item.get("session_id") or ""),
        )
    )
    protected_items = [item for item in all_items if item.get("protect_local_content")]
    sessions_with_protected = {
        str(row.get("session_id") or "")
        for row in session_summaries
        if safe_int(row.get("protect_local_content_items")) > 0
    }
    summary = {
        "session_count": len(rows),
        "planned_session_count": len(session_summaries),
        "missing_plan_count": len(missing),
        "item_count": len(all_items),
        "seconds": round(sum(safe_float((item.get("interval") or {}).get("duration_sec")) for item in all_items), 3),
        "protect_local_content_items": len(protected_items),
        "protect_local_content_seconds": round(sum(safe_float((item.get("interval") or {}).get("duration_sec")) for item in protected_items), 3),
        "sessions_with_protect_local_content": len(sessions_with_protected),
        "by_diagnostic": by_diagnostic(all_items),
        "recommended_next_step": (
            "implement_segment_level_remote_leak_repair"
            if protected_items
            else ("run_remote_leak_segment_plan_for_missing_sessions" if missing else "keep_remote_leak_mark_only")
        ),
    }
    report = {
        "schema": SCHEMA_REPORT,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": {"name": "report-remote-leak-segment-corpus", "version": SCRIPT_VERSION},
        "inputs": {
            "session_quality": str(args.session_quality),
            "sessions": [str(path) for path in args.sessions] if args.sessions else "from_session_quality",
        },
        "summary": summary,
        "sessions": sorted(
            session_summaries,
            key=lambda row: (
                -safe_int(row.get("protect_local_content_items")),
                -safe_float(row.get("protect_local_content_seconds")),
                str(row.get("session_id") or ""),
            ),
        ),
        "missing_plans": missing,
        "review_items": all_items[: max(0, args.max_items)],
        "policy": {"mode": "audit_only", "may_modify_transcript": False, "may_modify_raw_audio": False},
    }
    return report, all_items


def format_time(seconds: float) -> str:
    total = max(0, int(seconds))
    return f"{total // 60:02d}:{total % 60:02d}"


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    lines = [
        "# Remote Leak Segment Corpus Report",
        "",
        f"- Sessions: `{summary.get('planned_session_count')}` / `{summary.get('session_count')}` planned",
        f"- Missing plans: `{summary.get('missing_plan_count')}`",
        f"- Items: `{summary.get('item_count')}` / `{summary.get('seconds')}` sec",
        f"- Protect local content: `{summary.get('protect_local_content_items')}` / `{summary.get('protect_local_content_seconds')}` sec",
        f"- Sessions with protected local content: `{summary.get('sessions_with_protect_local_content')}`",
        f"- Next: `{summary.get('recommended_next_step')}`",
        "",
    ]
    sessions = [row for row in report.get("sessions", []) if isinstance(row, dict) and safe_int(row.get("protect_local_content_items")) > 0]
    if sessions:
        lines += ["## Sessions With Local-Content Risk", ""]
        for row in sessions[:25]:
            lines.append(
                f"- `{row.get('session_id')}` `{row.get('selected_profile')}`: "
                f"`{row.get('protect_local_content_items')}` items / `{row.get('protect_local_content_seconds')}` sec, "
                f"gate `{row.get('use_gate')}`, report `less {row.get('report_path')}`"
            )
        lines.append("")
    items = report.get("review_items") if isinstance(report.get("review_items"), list) else []
    if items:
        lines += ["## Top Items", ""]
        for item in items[:30]:
            interval = item.get("interval") if isinstance(item.get("interval"), dict) else {}
            start = format_time(safe_float(interval.get("start")))
            end = format_time(safe_float(interval.get("end")))
            lines += [
                f"### `{item.get('session_id')}` `{item.get('item_id')}` `{item.get('diagnostic')}` {start}-{end}",
                "",
                f"- Proposal: `{item.get('proposal')}` / `{item.get('future_patch_type')}`",
                f"- Whole Me drop allowed: `{item.get('whole_me_drop_allowed')}`",
                f"- Me: {item.get('me_text')}",
                f"- Colleagues: {item.get('remote_text')}",
                f"- Report: `less {item.get('report_path')}`",
                "",
            ]
    else:
        lines += ["No remote-leak segment items found.", ""]
    missing = report.get("missing_plans") if isinstance(report.get("missing_plans"), list) else []
    if missing:
        lines += ["## Missing Plans", ""]
        for row in missing[:25]:
            lines.append(f"- `{row.get('session_id')}`: `{row.get('expected')}`")
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    report, items = build_report(args)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    write_json(args.out_dir / "remote_leak_segment_corpus_report.json", report)
    write_jsonl(args.out_dir / "remote_leak_segment_corpus_items.jsonl", items)
    write_markdown(args.out_dir / "remote_leak_segment_corpus_report.md", report)
    summary = report["summary"]
    print(f"remote_leak_segment_corpus: {args.out_dir / 'remote_leak_segment_corpus_report.json'}")
    print(f"planned_sessions: {summary['planned_session_count']} / {summary['session_count']}")
    print(f"protect_local_content_items: {summary['protect_local_content_items']}")
    print(f"protect_local_content_seconds: {summary['protect_local_content_seconds']}")
    print(f"recommended_next_step: {summary['recommended_next_step']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
