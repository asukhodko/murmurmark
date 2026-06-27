#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_VERSION = "0.1.0"
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


def assess(
    selected_quality: dict[str, Any] | None,
    suggested_quality: dict[str, Any] | None,
    suggested_verdict: str | None,
    suggested_gates: bool | None,
) -> tuple[str, list[str]]:
    flags: list[str] = []
    if not suggested_quality:
        return "missing_suggested_profile", ["missing_suggested_quality"]
    if suggested_gates is not True:
        flags.append("suggested_gates_not_passed")
    if suggested_verdict in {"risky", "failed"}:
        flags.append(f"suggested_verdict:{suggested_verdict}")

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

    hard_flags = [flag for flag in flags if flag.startswith("suggested_verdict:") or flag in {"suggested_gates_not_passed", "local_recall_worse"}]
    if hard_flags:
        return "do_not_promote", flags
    if duplicate_reduction >= 5.0:
        return "promising_shadow_candidate", flags
    return "low_gain_shadow_candidate", flags


def collect_session(session: Path, selected_row: dict[str, Any]) -> dict[str, Any]:
    selected_profile = str(selected_row.get("selected_profile") or "missing")
    selected_quality = read_json(quality_path(session, selected_profile))
    suggested_quality = read_json(quality_path(session, SUGGESTED_PROFILE))
    selected_verdict = read_json(verdict_path(session, selected_profile))
    suggested_verdict = read_json(verdict_path(session, SUGGESTED_PROFILE))
    review_report = read_json(suggested_report_path(session))
    review_summary = review_report.get("summary") if isinstance(review_report, dict) else {}
    review_gates = review_report.get("gates") if isinstance(review_report, dict) else {}
    suggested_gates_passed = review_gates.get("passed") if isinstance(review_gates, dict) else None
    assessment, flags = assess(
        selected_quality,
        suggested_quality,
        str(suggested_verdict.get("verdict")) if isinstance(suggested_verdict, dict) else None,
        suggested_gates_passed if isinstance(suggested_gates_passed, bool) else None,
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
        "suggested_gates_passed": suggested_gates_passed,
        "assessment": assessment,
        "flags": flags,
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
    duplicate_reduction = 0.0
    needs_review_reduction = 0
    for row in available:
        duplicate_delta = row["metrics"]["remote_duplicate_in_me_seconds"]["delta"]
        if isinstance(duplicate_delta, (int, float)):
            duplicate_reduction += -duplicate_delta
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
        "total_dropped_me_utterances": sum(safe_int((row.get("review_summary") or {}).get("dropped_me_utterances")) for row in available),
        "total_dropped_me_seconds": rounded(
            sum(safe_float((row.get("review_summary") or {}).get("dropped_me_seconds")) or 0.0 for row in available)
        ),
        "remote_duplicate_reduction_seconds": rounded(duplicate_reduction),
        "needs_review_reduction_count": needs_review_reduction,
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
        f"- Assessments: `{json.dumps(summary['by_assessment'], ensure_ascii=False, sort_keys=True)}`",
        "",
        "## Sessions",
        "",
        "| Session | Selected | Suggested Verdict | Assessment | Drop s | Dup Δ s | Needs Δ | Flags |",
        "|---|---|---|---|---:|---:|---:|---|",
    ]
    for row in payload["sessions"]:
        metrics = row["metrics"]
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
            "but do not promote it while any suggested session has a `risky` or `failed` verdict.",
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
