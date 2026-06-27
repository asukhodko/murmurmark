#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_VERSION = "0.2.0"
SCHEMA = "murmurmark.suggested_review_shadow_report/v1"
SUGGESTED_PROFILE = "suggested_review_v1"
METRIC_KEYS = (
    "utterances",
    "needs_review_count",
    "remote_duplicate_in_me_seconds",
    "cross_role_overlap_gt2_seconds",
    "audit_harmful_seconds_after",
    "audit_review_seconds",
    "local_only_island_recall",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare suggested_review_v1 against selected transcript profiles.")
    parser.add_argument("sessions", nargs="*", type=Path)
    parser.add_argument(
        "--session-quality",
        type=Path,
        default=Path("sessions/_reports/session-quality/session_quality_report.json"),
        help="Session quality report used to find selected profiles.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("sessions/_reports/suggested-review-shadow"),
        help="Output directory.",
    )
    return parser.parse_args()


def suffix(profile: str) -> str:
    return "" if profile == "current" else f".{profile}"


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def rounded(value: float | None, digits: int = 3) -> float | None:
    return round(value, digits) if value is not None else None


def metric_value(payload: dict[str, Any] | None, key: str) -> float | None:
    if not isinstance(payload, dict):
        return None
    return safe_float(payload.get(key))


def quality_path(session: Path, profile: str) -> Path:
    return session / "derived/transcript-simple/whisper-cpp/resolved" / f"quality_report{suffix(profile)}.json"


def verdict_path(session: Path, profile: str) -> Path:
    return session / "derived/synthesis-simple/extractive" / f"quality_verdict{suffix(profile)}.json"


def dialogue_path(session: Path, profile: str) -> Path:
    return session / "derived/transcript-simple/whisper-cpp/resolved" / f"clean_dialogue{suffix(profile)}.json"


def selected_rows_from_report(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = report.get("sessions") if isinstance(report.get("sessions"), list) else []
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        session = str(row.get("session") or "")
        session_id = str(row.get("session_id") or "")
        if session:
            result[session] = row
            result[f"./{session}"] = row
        if session_id:
            result[session_id] = row
    return result


def session_from_row(row: dict[str, Any]) -> Path:
    return Path(str(row.get("session") or f"sessions/{row.get('session_id')}"))


def row_for_session(session: Path, rows_by_key: dict[str, dict[str, Any]]) -> dict[str, Any]:
    candidates = [str(session), session.as_posix(), f"./{session.as_posix()}", session.name]
    try:
        candidates.append(str(session.resolve()))
    except OSError:
        pass
    for candidate in candidates:
        row = rows_by_key.get(candidate)
        if row:
            return row
    return {"session": session.as_posix(), "session_id": session.name, "selected_profile": "missing"}


def metric_comparison(selected: dict[str, Any] | None, suggested: dict[str, Any] | None) -> dict[str, dict[str, float | None]]:
    result: dict[str, dict[str, float | None]] = {}
    for key in METRIC_KEYS:
        selected_value = metric_value(selected, key)
        suggested_value = metric_value(suggested, key)
        result[key] = {
            "selected": rounded(selected_value, 6),
            "suggested": rounded(suggested_value, 6),
            "delta": rounded(suggested_value - selected_value, 6) if selected_value is not None and suggested_value is not None else None,
        }
    return result


def suggested_report_path(session: Path) -> Path:
    return (
        session
        / "derived/transcript-simple/whisper-cpp/review-decisions"
        / f"review_decisions_report.{SUGGESTED_PROFILE}.json"
    )


def needs_review_ids(session: Path, profile: str) -> set[str]:
    payload = read_json(dialogue_path(session, profile))
    utterances = payload.get("utterances") if isinstance(payload, dict) else []
    if not isinstance(utterances, list):
        return set()
    return {
        str(row.get("id"))
        for row in utterances
        if isinstance(row, dict) and str(row.get("id") or "") and isinstance(row.get("quality"), dict) and row["quality"].get("needs_review") is True
    }


def utterance_ids(session: Path, profile: str) -> set[str]:
    payload = read_json(dialogue_path(session, profile))
    utterances = payload.get("utterances") if isinstance(payload, dict) else []
    if not isinstance(utterances, list):
        return set()
    return {str(row.get("id")) for row in utterances if isinstance(row, dict) and str(row.get("id") or "")}


def risk_types(verdict: dict[str, Any] | None) -> set[str]:
    items = verdict.get("risk_items") if isinstance(verdict, dict) else []
    if not isinstance(items, list):
        return set()
    return {str(item.get("type")) for item in items if isinstance(item, dict) and item.get("type")}


def severe_risk_types(verdict: dict[str, Any] | None) -> set[str]:
    items = verdict.get("risk_items") if isinstance(verdict, dict) else []
    if not isinstance(items, list):
        return set()
    return {
        str(item.get("type"))
        for item in items
        if isinstance(item, dict) and item.get("type") and item.get("severity") in {"high", "fatal"}
    }


def needs_review_sources(session: Path, selected_profile: str) -> dict[str, Any]:
    suggested_dialogue = dialogue_path(session, SUGGESTED_PROFILE)
    if not suggested_dialogue.exists():
        selected_needs = needs_review_ids(session, selected_profile)
        return {
            "selected_needs_review_count": len(selected_needs),
            "suggested_needs_review_count": None,
            "inherited_needs_review_count": None,
            "added_needs_review_count": None,
            "removed_needs_review_count": None,
            "dropped_utterance_count": None,
            "added_needs_review_ids": [],
            "removed_needs_review_ids": [],
        }
    selected_needs = needs_review_ids(session, selected_profile)
    suggested_needs = needs_review_ids(session, SUGGESTED_PROFILE)
    selected_ids = utterance_ids(session, selected_profile)
    suggested_ids = utterance_ids(session, SUGGESTED_PROFILE)
    return {
        "selected_needs_review_count": len(selected_needs),
        "suggested_needs_review_count": len(suggested_needs),
        "inherited_needs_review_count": len(selected_needs & suggested_needs),
        "added_needs_review_count": len(suggested_needs - selected_needs),
        "removed_needs_review_count": len(selected_needs - suggested_needs),
        "dropped_utterance_count": len(selected_ids - suggested_ids),
        "added_needs_review_ids": sorted(suggested_needs - selected_needs)[:20],
        "removed_needs_review_ids": sorted(selected_needs - suggested_needs)[:20],
    }


def assess(
    selected_quality: dict[str, Any] | None,
    suggested_quality: dict[str, Any] | None,
    suggested_verdict: str | None,
    suggested_gates: bool | None,
    suggested_severe_risk_types: set[str],
    needs_sources: dict[str, Any],
) -> tuple[str, list[str]]:
    flags: list[str] = []
    if not suggested_quality:
        return "missing_suggested_profile", ["missing_suggested_quality"]
    if suggested_gates is not True:
        flags.append("suggested_gates_not_passed")
    residual_risk = suggested_verdict in {"risky", "failed"}
    if suggested_verdict in {"risky", "failed"}:
        flags.append(f"residual_suggested_verdict:{suggested_verdict}")
    non_residual_risks = sorted(suggested_severe_risk_types - {"needs_review_ratio", "suggested_review_candidate_profile"})
    if non_residual_risks:
        flags.append("non_residual_risk_items:" + ",".join(non_residual_risks[:5]))
    if safe_int(needs_sources.get("added_needs_review_count")) > 0:
        flags.append("added_needs_review_items")

    selected_recall = metric_value(selected_quality, "local_only_island_recall")
    suggested_recall = metric_value(suggested_quality, "local_only_island_recall")
    if selected_recall is not None and suggested_recall is not None and suggested_recall + 0.001 < selected_recall:
        flags.append("local_recall_worse")

    for key in ("audit_harmful_seconds_after", "audit_review_seconds", "needs_review_count"):
        selected_value = metric_value(selected_quality, key)
        suggested_value = metric_value(suggested_quality, key)
        if selected_value is not None and suggested_value is not None and suggested_value > selected_value + 0.001:
            flags.append(f"{key}_worse")

    selected_duplicate = metric_value(selected_quality, "remote_duplicate_in_me_seconds") or 0.0
    suggested_duplicate = metric_value(suggested_quality, "remote_duplicate_in_me_seconds") or 0.0
    duplicate_reduction = selected_duplicate - suggested_duplicate
    if duplicate_reduction <= 0.0:
        flags.append("no_remote_duplicate_reduction")

    hard_flags = [
        flag
        for flag in flags
        if flag.startswith("non_residual_risk_items:")
        or flag
        in {
            "suggested_gates_not_passed",
            "local_recall_worse",
            "audit_harmful_seconds_after_worse",
            "audit_review_seconds_worse",
            "needs_review_count_worse",
            "added_needs_review_items",
        }
    ]
    if hard_flags:
        return "do_not_promote", flags
    if duplicate_reduction >= 5.0:
        if residual_risk:
            return "promising_cleanup_candidate_with_residual_review", flags
        return "promising_shadow_candidate", flags
    return "low_gain_shadow_candidate", flags


def collect_session(session: Path, selected_row: dict[str, Any]) -> dict[str, Any]:
    selected_profile = str(selected_row.get("selected_profile") or "missing")
    selected_quality = read_json(quality_path(session, selected_profile))
    suggested_quality = read_json(quality_path(session, SUGGESTED_PROFILE))
    selected_verdict = read_json(verdict_path(session, selected_profile))
    suggested_verdict = read_json(verdict_path(session, SUGGESTED_PROFILE))
    needs_sources = needs_review_sources(session, selected_profile)
    review_report = read_json(suggested_report_path(session))
    review_summary = review_report.get("summary") if isinstance(review_report, dict) else {}
    review_gates = review_report.get("gates") if isinstance(review_report, dict) else {}
    suggested_gates_passed = review_gates.get("passed") if isinstance(review_gates, dict) else None
    assessment, flags = assess(
        selected_quality,
        suggested_quality,
        str(suggested_verdict.get("verdict")) if isinstance(suggested_verdict, dict) else None,
        suggested_gates_passed if isinstance(suggested_gates_passed, bool) else None,
        severe_risk_types(suggested_verdict),
        needs_sources,
    )
    metrics = metric_comparison(selected_quality, suggested_quality)
    return {
        "session": session.as_posix(),
        "session_id": session.name,
        "selected_profile": selected_profile,
        "suggested_profile": SUGGESTED_PROFILE,
        "suggested_available": suggested_quality is not None,
        "selected_verdict": selected_verdict.get("verdict") if isinstance(selected_verdict, dict) else None,
        "suggested_verdict": suggested_verdict.get("verdict") if isinstance(suggested_verdict, dict) else None,
        "suggested_risk_types": sorted(risk_types(suggested_verdict)),
        "suggested_gates_passed": suggested_gates_passed,
        "assessment": assessment,
        "flags": flags,
        "needs_review_sources": needs_sources,
        "review_summary": {
            "applied_decision_rows": safe_int(review_summary.get("applied_decision_rows") if isinstance(review_summary, dict) else None),
            "dropped_me_utterances": safe_int(review_summary.get("dropped_me_utterances") if isinstance(review_summary, dict) else None),
            "dropped_me_seconds": rounded(safe_float(review_summary.get("dropped_me_seconds") if isinstance(review_summary, dict) else None)),
            "needs_review_decisions": safe_int(review_summary.get("needs_review_decisions") if isinstance(review_summary, dict) else None),
            "local_recall_needs_review_decisions": safe_int(
                review_summary.get("local_recall_needs_review_decisions") if isinstance(review_summary, dict) else None
            ),
        },
        "metrics": metrics,
    }


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    available = [row for row in rows if row.get("suggested_available")]
    cleanup_candidates = [
        row
        for row in available
        if row.get("assessment") in {"promising_shadow_candidate", "promising_cleanup_candidate_with_residual_review"}
    ]
    duplicate_reduction = 0.0
    candidate_duplicate_reduction = 0.0
    needs_review_reduction = 0
    for row in available:
        duplicate_delta = row["metrics"]["remote_duplicate_in_me_seconds"]["delta"]
        if isinstance(duplicate_delta, (int, float)):
            duplicate_reduction += -duplicate_delta
            if row in cleanup_candidates:
                candidate_duplicate_reduction += -duplicate_delta
        needs_delta = row["metrics"]["needs_review_count"]["delta"]
        if isinstance(needs_delta, (int, float)):
            needs_review_reduction += int(-needs_delta)
    by_assessment: dict[str, int] = {}
    for row in rows:
        key = str(row.get("assessment") or "unknown")
        by_assessment[key] = by_assessment.get(key, 0) + 1
    return {
        "session_count": len(rows),
        "suggested_available_sessions": len(available),
        "suggested_gates_passed_sessions": sum(1 for row in available if row.get("suggested_gates_passed") is True),
        "suggested_risky_or_failed_sessions": sum(1 for row in available if row.get("suggested_verdict") in {"risky", "failed"}),
        "by_assessment": dict(sorted(by_assessment.items())),
        "cleanup_candidate_sessions": len(cleanup_candidates),
        "cleanup_candidate_dropped_me_seconds": rounded(
            sum(safe_float((row.get("review_summary") or {}).get("dropped_me_seconds")) or 0.0 for row in cleanup_candidates)
        ),
        "cleanup_candidate_remote_duplicate_reduction_seconds": rounded(candidate_duplicate_reduction),
        "total_dropped_me_utterances": sum(safe_int((row.get("review_summary") or {}).get("dropped_me_utterances")) for row in available),
        "total_dropped_me_seconds": rounded(
            sum(safe_float((row.get("review_summary") or {}).get("dropped_me_seconds")) or 0.0 for row in available)
        ),
        "remote_duplicate_reduction_seconds": rounded(duplicate_reduction),
        "needs_review_reduction_count": needs_review_reduction,
        "added_needs_review_count": sum(safe_int((row.get("needs_review_sources") or {}).get("added_needs_review_count")) for row in available),
        "removed_needs_review_count": sum(safe_int((row.get("needs_review_sources") or {}).get("removed_needs_review_count")) for row in available),
    }


def write_markdown(path: Path, payload: dict[str, Any]) -> None:
    summary = payload["summary"]
    lines = [
        "# Suggested Review Shadow Report",
        "",
        f"Generated: `{payload['generated_at']}`",
        "",
        "## Summary",
        "",
        f"- Sessions: `{summary['session_count']}`",
        f"- Suggested profiles available: `{summary['suggested_available_sessions']}`",
        f"- Suggested gates passed: `{summary['suggested_gates_passed_sessions']}`",
        f"- Suggested risky/failed verdicts: `{summary['suggested_risky_or_failed_sessions']}`",
        f"- Dropped Me seconds in shadow: `{summary['total_dropped_me_seconds']}`",
        f"- Remote duplicate reduction: `{summary['remote_duplicate_reduction_seconds']}` seconds",
        f"- Needs-review reduction: `{summary['needs_review_reduction_count']}` utterances",
        f"- Added needs-review items: `{summary['added_needs_review_count']}`",
        f"- Removed needs-review items: `{summary['removed_needs_review_count']}`",
        f"- Cleanup candidate sessions: `{summary['cleanup_candidate_sessions']}`",
        f"- Cleanup candidate duplicate reduction: `{summary['cleanup_candidate_remote_duplicate_reduction_seconds']}` seconds",
        f"- Assessments: `{json.dumps(summary['by_assessment'], ensure_ascii=False, sort_keys=True)}`",
        "",
        "## Sessions",
        "",
        "| Session | Selected | Suggested Verdict | Assessment | Drop s | Dup Δ s | Needs Δ | Added Needs | Removed Needs | Flags |",
        "|---|---|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for row in payload["sessions"]:
        metrics = row["metrics"]
        sources = row.get("needs_review_sources") or {}
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{row['session_id']}`",
                    str(row.get("selected_profile")),
                    str(row.get("suggested_verdict")),
                    str(row.get("assessment")),
                    str((row.get("review_summary") or {}).get("dropped_me_seconds")),
                    str(metrics["remote_duplicate_in_me_seconds"].get("delta")),
                    str(metrics["needs_review_count"].get("delta")),
                    str(sources.get("added_needs_review_count")),
                    str(sources.get("removed_needs_review_count")),
                    ", ".join(row.get("flags") or []),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Recommendation",
            "",
            "Keep `suggested_review_v1` explicit-only. Use it to design the next conservative cleanup rule, "
            "but do not make it the user-facing auto profile. Residual `needs_review_ratio` means the session "
            "still needs review; it does not by itself prove that the suggested cleanup edits are unsafe.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    session_quality = read_json(args.session_quality.expanduser()) or {}
    rows_by_key = selected_rows_from_report(session_quality)
    if args.sessions:
        sessions = [path.expanduser() for path in args.sessions]
    else:
        report_rows = session_quality.get("sessions") if isinstance(session_quality.get("sessions"), list) else []
        sessions = [session_from_row(row) for row in report_rows if isinstance(row, dict)]
    rows = [collect_session(session, row_for_session(session, rows_by_key)) for session in sessions]
    payload = {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": {"name": "report-suggested-review-shadow", "version": SCRIPT_VERSION},
        "inputs": {"session_quality": args.session_quality.as_posix()},
        "summary": aggregate(rows),
        "sessions": rows,
    }
    out_dir = args.out_dir.expanduser()
    write_json(out_dir / "suggested_review_shadow_report.json", payload)
    write_markdown(out_dir / "suggested_review_shadow_report.md", payload)
    print(f"written: {out_dir / 'suggested_review_shadow_report.json'}")
    print(f"markdown: {out_dir / 'suggested_review_shadow_report.md'}")
    print(f"suggested_available_sessions: {payload['summary']['suggested_available_sessions']}")
    print(f"remote_duplicate_reduction_seconds: {payload['summary']['remote_duplicate_reduction_seconds']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
