#!/usr/bin/env python3
"""Aggregate ASR-positive Echo Guard candidate reports across sessions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


SCHEMA = "murmurmark.echo.asr_positive_echo_candidate_corpus_report/v1"
DEFAULT_OUT_DIR = Path("sessions/_reports/asr-positive-echo-candidate")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a corpus report for an experimental Echo Guard candidate.")
    parser.add_argument("sessions", nargs="+", type=Path)
    parser.add_argument("--candidate", default="coverage_v2_remote_gate_local_fir")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--min-safe-improved-sessions", type=int, default=2)
    parser.add_argument("--max-local-recall-regressions", type=int, default=0)
    parser.add_argument("--max-review-burden-growth-sessions", type=int, default=0)
    return parser.parse_args()


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
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


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


def row_for_session(session: Path, candidate: str) -> dict[str, Any]:
    path = session / "derived/preprocess/echo/asr_positive_echo_candidate_report.json"
    payload = read_json(path)
    if not payload:
        return {"session": str(session), "status": "missing", "report": str(path)}
    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
    assessment = payload.get("assessment") if isinstance(payload.get("assessment"), dict) else {}
    coverage = metrics.get("coverage_gate") if isinstance(metrics.get("coverage_gate"), dict) else {}
    row = {
        "session": str(session),
        "status": "ok",
        "report": str(path),
        "profile": payload.get("profile"),
        "candidate_matches": payload.get("profile") == candidate,
        "assessment_status": assessment.get("status"),
        "assessment_reason": assessment.get("reason"),
        "remote_token_leak_rate_local_fir": metrics.get("remote_token_leak_rate_local_fir"),
        "remote_token_leak_rate_candidate": metrics.get("remote_token_leak_rate_candidate"),
        "remote_token_leak_delta": metrics.get("remote_token_leak_delta"),
        "local_word_recall_delta": metrics.get("local_word_recall_delta"),
        "review_burden_seconds": metrics.get("review_burden_seconds"),
        "remote_duplicate_in_me_seconds": metrics.get("remote_duplicate_in_me_seconds"),
        "coverage_windows": safe_int(coverage.get("windows")),
        "coverage_applied_windows": safe_int(coverage.get("applied_windows")),
        "promotion_decision": payload.get("promotion_decision"),
    }
    row["class"] = classify(row)
    return row


def classify(row: dict[str, Any]) -> str:
    if row.get("status") != "ok":
        return "missing_report"
    if row.get("candidate_matches") is not True:
        return "candidate_mismatch"
    status = str(row.get("assessment_status") or "")
    if status == "passed":
        return "safe_improved"
    if status == "not_applicable":
        return "not_applicable"
    if row.get("assessment_reason") == "local_word_recall_regression":
        return "local_recall_regression"
    if status == "skipped":
        return "skipped"
    return "not_improved"


def count_by(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = str(row.get(key) or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def promotion_gate(args: argparse.Namespace, rows: list[dict[str, Any]]) -> dict[str, Any]:
    ok_rows = [row for row in rows if row.get("status") == "ok"]
    safe_improved = [row for row in ok_rows if row.get("class") == "safe_improved"]
    local_regressions = [row for row in ok_rows if row.get("class") == "local_recall_regression"]
    candidate_mismatch = [row for row in ok_rows if row.get("candidate_matches") is not True]
    shadow_only_bad = [
        row for row in ok_rows
        if row.get("promotion_decision") != "shadow_only_do_not_promote"
    ]
    passed = (
        len(safe_improved) >= args.min_safe_improved_sessions
        and len(local_regressions) <= args.max_local_recall_regressions
        and not candidate_mismatch
        and not shadow_only_bad
    )
    reasons: list[str] = []
    if len(safe_improved) < args.min_safe_improved_sessions:
        reasons.append("not_enough_safe_improved_sessions")
    if len(local_regressions) > args.max_local_recall_regressions:
        reasons.append("local_recall_regressions_present")
    if candidate_mismatch:
        reasons.append("candidate_mismatch")
    if shadow_only_bad:
        reasons.append("non_shadow_promotion_decision_found")
    return {
        "passed": passed,
        "reason": "passed" if passed else ",".join(reasons),
        "safe_improved_sessions": [row["session"] for row in safe_improved],
        "local_recall_regression_sessions": [row["session"] for row in local_regressions],
        "candidate_mismatch_sessions": [row["session"] for row in candidate_mismatch],
        "shadow_only_violation_sessions": [row["session"] for row in shadow_only_bad],
        "promotion_decision": "shadow_only_do_not_promote",
        "promotion_ready": False,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    gate = payload["promotion_gate"]
    lines = [
        "# ASR-Positive Echo Candidate Corpus Report",
        "",
        "This report evaluates a shadow-only Echo Guard audio candidate against `local_fir`.",
        "It does not promote the candidate to default ASR input.",
        "",
        "## Summary",
        "",
    ]
    for key, value in summary.items():
        lines.append(f"- `{key}`: `{json.dumps(value, ensure_ascii=False, sort_keys=True)}`")
    lines.extend(
        [
            "",
            "## Promotion Gate",
            "",
            f"- Passed: `{gate.get('passed')}`",
            f"- Reason: `{gate.get('reason')}`",
            f"- Promotion decision: `{gate.get('promotion_decision')}`",
            "",
            "## Sessions",
            "",
            "| Session | Class | Reason | Leak delta | Local recall delta | Coverage applied | Review burden sec |",
            "|---|---|---|---:|---:|---:|---:|",
        ]
    )
    for row in payload["sessions"]:
        lines.append(
            "| `{session}` | `{klass}` | `{reason}` | {leak} | {recall} | {applied} | {review} |".format(
                session=row.get("session"),
                klass=row.get("class"),
                reason=row.get("assessment_reason"),
                leak=fmt(row.get("remote_token_leak_delta")),
                recall=fmt(row.get("local_word_recall_delta")),
                applied=row.get("coverage_applied_windows") or 0,
                review=fmt(row.get("review_burden_seconds")),
            )
        )
    lines.extend(
        [
            "",
            "## Reading",
            "",
            "- `safe_improved` means sampled ASR-visible remote-token leakage decreased while local recall stayed within the gate.",
            "- `not_applicable` means `local_fir` did not expose ASR-visible remote leakage in sampled windows.",
            "- `promotion_ready` remains false: this candidate is experimental until a separate promotion goal changes default Echo Guard behavior.",
        ]
    )
    return "\n".join(lines) + "\n"


def fmt(value: Any) -> str:
    if value is None:
        return ""
    try:
        return f"{float(value):.6f}"
    except (TypeError, ValueError):
        return str(value)


def main() -> int:
    args = parse_args()
    rows = [row_for_session(session, args.candidate) for session in args.sessions]
    ok_rows = [row for row in rows if row.get("status") == "ok"]
    gate = promotion_gate(args, rows)
    summary = {
        "candidate": args.candidate,
        "sessions": len(rows),
        "reports_found": len(ok_rows),
        "classes": count_by(rows, "class"),
        "safe_improved_sessions": sum(1 for row in rows if row.get("class") == "safe_improved"),
        "not_applicable_sessions": sum(1 for row in rows if row.get("class") == "not_applicable"),
        "local_recall_regressions": sum(1 for row in rows if row.get("class") == "local_recall_regression"),
        "missing_reports": sum(1 for row in rows if row.get("status") != "ok"),
        "total_coverage_applied_windows": sum(safe_int(row.get("coverage_applied_windows")) for row in ok_rows),
        "avg_remote_token_leak_delta": average([
            safe_float(row.get("remote_token_leak_delta"))
            for row in ok_rows
            if row.get("remote_token_leak_delta") is not None
        ]),
        "promotion_decision": "shadow_only_do_not_promote",
    }
    payload = {
        "schema": SCHEMA,
        "summary": summary,
        "promotion_gate": gate,
        "sessions": rows,
    }
    args.out_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.out_dir / "asr_positive_echo_candidate_corpus_report.json"
    md_path = args.out_dir / "asr_positive_echo_candidate_corpus_report.md"
    write_json(json_path, payload)
    md_path.write_text(render_markdown(payload), encoding="utf-8")
    print(f"asr_positive_echo_candidate_corpus_report: {json_path}")
    print(f"reports_found: {summary['reports_found']}/{summary['sessions']}")
    print(f"safe_improved_sessions: {summary['safe_improved_sessions']}")
    print(f"promotion_decision: {summary['promotion_decision']}")
    return 0


def average(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 6)


if __name__ == "__main__":
    raise SystemExit(main())
