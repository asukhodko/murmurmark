#!/usr/bin/env python3
"""Aggregate remote-forbidden evidence summaries across sessions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


SCHEMA = "murmurmark.echo.remote_forbidden_corpus_report/v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build corpus report for remote-forbidden evidence.")
    parser.add_argument("sessions", nargs="+", type=Path)
    parser.add_argument("--out-dir", type=Path, default=Path("sessions/_reports/remote-forbidden"))
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


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


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def row_for_session(session: Path) -> dict[str, Any]:
    path = session / "derived/audit/remote-forbidden/remote_forbidden_summary.json"
    summary = read_json(path)
    if not isinstance(summary, dict):
        return {
            "session": str(session),
            "status": "missing",
            "summary": str(path),
        }
    metrics = summary.get("metrics") if isinstance(summary.get("metrics"), dict) else {}
    gates = summary.get("gates") if isinstance(summary.get("gates"), dict) else {}
    actions = metrics.get("actions") if isinstance(metrics.get("actions"), dict) else {}
    return {
        "session": str(session),
        "status": summary.get("status"),
        "summary": str(path),
        "gate_passed": gates.get("passed"),
        "gate_reason": gates.get("reason"),
        "remote_token_leak_rate_before": metrics.get("remote_token_leak_rate_before"),
        "remote_token_leak_rate_after": metrics.get("remote_token_leak_rate_after"),
        "remote_token_leak_delta": metrics.get("remote_token_leak_delta"),
        "local_word_recall_before": metrics.get("local_word_recall_before"),
        "local_word_recall_after": metrics.get("local_word_recall_after"),
        "local_word_recall_delta": metrics.get("local_word_recall_delta"),
        "remote_forbidden_rows": safe_int(metrics.get("remote_forbidden_rows")),
        "local_speech_gate_rows": safe_int(metrics.get("local_speech_gate_rows")),
        "suggest_drop_count": safe_int(actions.get("suggest_drop")),
        "quarantine_count": safe_int(actions.get("quarantine")),
        "needs_review_count": safe_int(actions.get("needs_review")),
        "keep_count": safe_int(actions.get("keep")),
        "suggest_drop_seconds": safe_float(metrics.get("suggest_drop_seconds")),
        "quarantine_seconds": safe_float(metrics.get("quarantine_seconds")),
        "needs_review_seconds": safe_float(metrics.get("needs_review_seconds")),
        "recommendation": summary.get("recommendation"),
    }


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Remote-Forbidden Corpus Report",
        "",
        "This report aggregates shadow-only remote-forbidden evidence. It does not promote Echo Guard candidates.",
        "",
        "## Summary",
        "",
    ]
    for key, value in payload["summary"].items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(
        [
            "",
            "## Sessions",
            "",
            "| Session | Status | Gate | Leak delta | Local recall delta | Suggested drops | Quarantine | Needs review |",
            "|---|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for row in payload["sessions"]:
        lines.append(
            "| `{session}` | `{status}` | `{gate}` | {leak_delta} | {recall_delta} | {drop} | {quarantine} | {review} |".format(
                session=row.get("session"),
                status=row.get("status"),
                gate=row.get("gate_reason"),
                leak_delta=fmt(row.get("remote_token_leak_delta")),
                recall_delta=fmt(row.get("local_word_recall_delta")),
                drop=row.get("suggest_drop_count") or 0,
                quarantine=row.get("quarantine_count") or 0,
                review=row.get("needs_review_count") or 0,
            )
        )
    lines.extend(["", "## Reading", ""])
    lines.append("- A negative leak delta means the guard reduced ASR-visible remote tokens versus local_fir.")
    lines.append("- A local recall delta below `-0.02` is treated as a local-speech regression.")
    lines.append("- `target_status` says whether the current corpus meets the goal's two-session evidence target.")
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
    rows = [row_for_session(session) for session in args.sessions]
    ok_rows = [row for row in rows if row.get("status") == "ok"]
    leak_improved = [
        row
        for row in ok_rows
        if row.get("remote_token_leak_delta") is not None and safe_float(row.get("remote_token_leak_delta")) < 0.0
    ]
    local_regressions = [
        row
        for row in ok_rows
        if row.get("local_word_recall_delta") is not None and safe_float(row.get("local_word_recall_delta")) < -0.02
    ]
    safe_improved = [
        row
        for row in leak_improved
        if row.get("local_word_recall_delta") is not None and safe_float(row.get("local_word_recall_delta")) >= -0.02
    ]
    target_status = (
        "target_met_two_sessions"
        if len(safe_improved) >= 2
        else "target_not_met_only_one_safe_session"
        if len(safe_improved) == 1
        else "target_not_met_no_safe_sessions"
    )
    summary = {
        "sessions": len(rows),
        "reports_found": len(ok_rows),
        "gate_passed_sessions": sum(1 for row in ok_rows if row.get("gate_passed") is True),
        "remote_token_leak_improved_sessions": len(leak_improved),
        "safe_improved_sessions": len(safe_improved),
        "local_recall_regressions": len(local_regressions),
        "remote_forbidden_rows": sum(safe_int(row.get("remote_forbidden_rows")) for row in ok_rows),
        "local_speech_gate_rows": sum(safe_int(row.get("local_speech_gate_rows")) for row in ok_rows),
        "suggest_drop_count": sum(safe_int(row.get("suggest_drop_count")) for row in ok_rows),
        "quarantine_count": sum(safe_int(row.get("quarantine_count")) for row in ok_rows),
        "needs_review_count": sum(safe_int(row.get("needs_review_count")) for row in ok_rows),
        "suggest_drop_seconds": round(sum(safe_float(row.get("suggest_drop_seconds")) for row in ok_rows), 3),
        "quarantine_seconds": round(sum(safe_float(row.get("quarantine_seconds")) for row in ok_rows), 3),
        "needs_review_seconds": round(sum(safe_float(row.get("needs_review_seconds")) for row in ok_rows), 3),
        "target_status": target_status,
        "promotion_decision": "shadow_review_only_do_not_promote",
    }
    payload = {"schema": SCHEMA, "summary": summary, "sessions": rows}
    args.out_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.out_dir / "remote_forbidden_corpus_report.json"
    md_path = args.out_dir / "remote_forbidden_corpus_report.md"
    write_json(json_path, payload)
    md_path.write_text(render_markdown(payload), encoding="utf-8")
    print(f"remote_forbidden_corpus_report: {json_path}")
    print(f"reports_found: {summary['reports_found']}/{summary['sessions']}")
    print(f"safe_improved_sessions: {summary['safe_improved_sessions']}")
    print(f"target_status: {summary['target_status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
