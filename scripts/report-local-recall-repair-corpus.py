#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_REPORT = "murmurmark.local_recall_repair_corpus_report/v1"
SCHEMA_ITEM = "murmurmark.local_recall_repair_corpus_item/v1"
SCRIPT_VERSION = "0.1.0"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aggregate local-recall repair profiles across a session corpus.")
    parser.add_argument("sessions", nargs="*", type=Path, help="Session directories. Defaults to sessions from --session-quality.")
    parser.add_argument("--session-quality", type=Path, default=Path("sessions/_reports/session-quality/session_quality_report.json"))
    parser.add_argument("--out-dir", type=Path, default=Path("sessions/_reports/local-recall-repair"))
    parser.add_argument("--profile", default="local_recall_repair_v1")
    parser.add_argument("--max-items", type=int, default=60)
    return parser.parse_args()


def suffix(profile: str) -> str:
    return "" if profile == "current" else f".{profile}"


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


def compact_patch(row: dict[str, Any], session_row: dict[str, Any]) -> dict[str, Any]:
    session = str(session_row.get("session") or "")
    utterance = row.get("utterance") if isinstance(row.get("utterance"), dict) else {}
    micro = row.get("micro_asr") if isinstance(row.get("micro_asr"), dict) else {}
    return {
        "schema": SCHEMA_ITEM,
        "kind": "patch",
        "session_id": str(session_row.get("session_id") or session_id_from_path(Path(session))),
        "session": session,
        "selected_profile": session_row.get("selected_profile"),
        "pipeline_status": session_row.get("pipeline_status"),
        "use_gate": session_row.get("use_gate"),
        "source_item_id": row.get("source_item_id"),
        "status": row.get("status"),
        "utterance_id": utterance.get("id"),
        "start_sec": utterance.get("start"),
        "end_sec": utterance.get("end"),
        "duration_sec": round(max(0.0, safe_float(utterance.get("end")) - safe_float(utterance.get("start"))), 3),
        "text": utterance.get("text"),
        "micro_score": micro.get("score"),
        "micro_source": micro.get("source_label"),
        "micro_window": micro.get("window_label"),
        "needs_review": bool((utterance.get("quality") or {}).get("needs_review")) if isinstance(utterance.get("quality"), dict) else None,
        "report_path": f"{session}/derived/transcript-simple/whisper-cpp/local-recall-repair/local_recall_repair.local_recall_repair_v1.md"
        if session
        else None,
    }


def compact_rejection(row: dict[str, Any], session_row: dict[str, Any]) -> dict[str, Any]:
    session = str(session_row.get("session") or "")
    return {
        "schema": SCHEMA_ITEM,
        "kind": "rejection",
        "session_id": str(session_row.get("session_id") or session_id_from_path(Path(session))),
        "session": session,
        "selected_profile": session_row.get("selected_profile"),
        "pipeline_status": session_row.get("pipeline_status"),
        "use_gate": session_row.get("use_gate"),
        "source_item_id": row.get("source_item_id"),
        "reason": row.get("reason"),
        "report_path": f"{session}/derived/transcript-simple/whisper-cpp/local-recall-repair/local_recall_repair.local_recall_repair_v1.md"
        if session
        else None,
    }


def build_report(args: argparse.Namespace) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    session_quality = read_json(args.session_quality)
    rows = session_rows(session_quality, args.sessions)
    profile_suffix = suffix(args.profile)
    missing: list[dict[str, Any]] = []
    session_summaries: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []

    for session_row in rows:
        session = Path(str(session_row.get("session") or ""))
        session_id = str(session_row.get("session_id") or session_id_from_path(session))
        repair_dir = session / "derived/transcript-simple/whisper-cpp/local-recall-repair"
        report_path = repair_dir / f"local_recall_repair_report{profile_suffix}.json"
        patches_path = repair_dir / f"local_recall_repair_patches{profile_suffix}.jsonl"
        rejected_path = repair_dir / f"local_recall_repair_rejected{profile_suffix}.jsonl"
        report = read_json(report_path)
        patches = read_jsonl(patches_path)
        rejected = read_jsonl(rejected_path)
        if not report:
            missing.append({"session_id": session_id, "session": str(session), "expected": str(report_path), "reason": "missing_local_recall_repair_report"})
            continue
        summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
        gates = report.get("gates") if isinstance(report.get("gates"), dict) else {}
        patch_items = [compact_patch(patch, session_row) for patch in patches]
        rejection_items = [compact_rejection(row, session_row) for row in rejected]
        items.extend(patch_items)
        items.extend(rejection_items)
        session_summaries.append(
            {
                "session_id": session_id,
                "session": str(session),
                "selected_profile": session_row.get("selected_profile"),
                "pipeline_status": session_row.get("pipeline_status"),
                "use_gate": session_row.get("use_gate"),
                "input_profile": report.get("input_profile"),
                "output_profile": report.get("output_profile"),
                "status": report.get("status"),
                "gates_passed": gates.get("passed"),
                "gate_warnings": gates.get("warnings") if isinstance(gates.get("warnings"), list) else [],
                "source_items": safe_int(summary.get("source_items")),
                "eligible_items": safe_int(summary.get("eligible_items")),
                "planned_repairs": safe_int(summary.get("planned_repairs")),
                "applied_repairs": safe_int(summary.get("applied_repairs")),
                "inserted_me_seconds": round(safe_float(summary.get("inserted_me_seconds")), 3),
                "rejected_items": safe_int(summary.get("rejected_items")),
                "report_path": str(repair_dir / f"local_recall_repair{profile_suffix}.md"),
            }
        )

    rejection_reasons = Counter(str(item.get("reason") or "unknown") for item in items if item.get("kind") == "rejection")
    patches = [item for item in items if item.get("kind") == "patch"]
    sessions_with_repairs = {str(item.get("session_id") or "") for item in patches}
    summary = {
        "session_count": len(rows),
        "repaired_session_count": len(session_summaries),
        "missing_repair_report_count": len(missing),
        "sessions_with_repairs": len(sessions_with_repairs),
        "source_items": sum(safe_int(row.get("source_items")) for row in session_summaries),
        "eligible_items": sum(safe_int(row.get("eligible_items")) for row in session_summaries),
        "planned_repairs": sum(safe_int(row.get("planned_repairs")) for row in session_summaries),
        "applied_repairs": sum(safe_int(row.get("applied_repairs")) for row in session_summaries),
        "inserted_me_seconds": round(sum(safe_float(row.get("inserted_me_seconds")) for row in session_summaries), 3),
        "rejected_items": sum(safe_int(row.get("rejected_items")) for row in session_summaries),
        "rejection_reasons": dict(sorted(rejection_reasons.items())),
        "gates_failed_sessions": sum(1 for row in session_summaries if row.get("gates_passed") is not True),
        "recommended_next_step": (
            "review_inserted_local_recall_repairs"
            if patches
            else ("run_local_recall_repair_for_missing_sessions" if missing else "keep_local_recall_repair_explicit")
        ),
    }
    report = {
        "schema": SCHEMA_REPORT,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": {"name": "report-local-recall-repair-corpus", "version": SCRIPT_VERSION},
        "inputs": {
            "session_quality": str(args.session_quality),
            "sessions": [str(path) for path in args.sessions] if args.sessions else "from_session_quality",
            "profile": args.profile,
        },
        "summary": summary,
        "sessions": sorted(
            session_summaries,
            key=lambda row: (
                -safe_int(row.get("applied_repairs")),
                -safe_float(row.get("inserted_me_seconds")),
                -safe_int(row.get("eligible_items")),
                str(row.get("session_id") or ""),
            ),
        ),
        "missing_repair_reports": missing,
        "items": sorted(
            items,
            key=lambda item: (
                item.get("kind") != "patch",
                -safe_float(item.get("duration_sec")),
                str(item.get("session_id") or ""),
            ),
        )[: max(0, args.max_items)],
        "policy": {"mode": "explicit_profile", "auto_promotion": False, "inserted_me_turns_need_review": True},
    }
    return report, items


def format_time(seconds: Any) -> str:
    total = max(0, int(safe_float(seconds)))
    return f"{total // 60:02d}:{total % 60:02d}"


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    lines = [
        "# Local Recall Repair Corpus Report",
        "",
        f"- Sessions: `{summary.get('repaired_session_count')}` / `{summary.get('session_count')}` with repair reports",
        f"- Missing repair reports: `{summary.get('missing_repair_report_count')}`",
        f"- Sessions with repairs: `{summary.get('sessions_with_repairs')}`",
        f"- Applied repairs: `{summary.get('applied_repairs')}` / `{summary.get('inserted_me_seconds')}` sec",
        f"- Eligible items: `{summary.get('eligible_items')}`",
        f"- Rejected items: `{summary.get('rejected_items')}`",
        f"- Next: `{summary.get('recommended_next_step')}`",
        "",
    ]
    sessions = [row for row in report.get("sessions", []) if isinstance(row, dict) and safe_int(row.get("applied_repairs")) > 0]
    if sessions:
        lines += ["## Sessions With Repairs", ""]
        for row in sessions:
            lines.append(
                f"- `{row.get('session_id')}` `{row.get('input_profile')}` -> `{row.get('output_profile')}` "
                f"repairs `{row.get('applied_repairs')}`, inserted `{row.get('inserted_me_seconds')}`s, "
                f"report `less {row.get('report_path')}`"
            )
        lines.append("")
    items = [item for item in report.get("items", []) if isinstance(item, dict) and item.get("kind") == "patch"]
    if items:
        lines += ["## Inserted Me Turns", ""]
        for item in items[:25]:
            lines += [
                f"### `{item.get('session_id')}` `{item.get('utterance_id')}` {format_time(item.get('start_sec'))}-{format_time(item.get('end_sec'))}",
                "",
                f"- Source item: `{item.get('source_item_id')}`",
                f"- Micro-ASR: `{item.get('micro_source')}` / `{item.get('micro_window')}` / score `{item.get('micro_score')}`",
                f"- Text: {item.get('text')}",
                f"- Review: `less {item.get('report_path')}`",
                "",
            ]
    else:
        lines += ["No inserted local-recall repairs found.", ""]
    reasons = summary.get("rejection_reasons") if isinstance(summary.get("rejection_reasons"), dict) else {}
    if reasons:
        lines += ["## Rejection Reasons", ""]
        for reason, count in sorted(reasons.items()):
            lines.append(f"- `{reason}`: `{count}`")
        lines.append("")
    missing = report.get("missing_repair_reports") if isinstance(report.get("missing_repair_reports"), list) else []
    if missing:
        lines += ["## Missing Repair Reports", ""]
        for row in missing[:25]:
            lines.append(f"- `{row.get('session_id')}`: `{row.get('expected')}`")
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    report, items = build_report(args)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    write_json(args.out_dir / "local_recall_repair_corpus_report.json", report)
    write_jsonl(args.out_dir / "local_recall_repair_corpus_items.jsonl", items)
    write_markdown(args.out_dir / "local_recall_repair_corpus_report.md", report)
    summary = report["summary"]
    print(f"local_recall_repair_corpus: {args.out_dir / 'local_recall_repair_corpus_report.json'}")
    print(f"repaired_sessions: {summary['repaired_session_count']} / {summary['session_count']}")
    print(f"applied_repairs: {summary['applied_repairs']}")
    print(f"inserted_me_seconds: {summary['inserted_me_seconds']}")
    print(f"recommended_next_step: {summary['recommended_next_step']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
