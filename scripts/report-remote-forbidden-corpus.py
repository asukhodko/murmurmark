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
    row = {
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
        "guarded_seconds": safe_float(metrics.get("guarded_seconds")),
        "review_burden_seconds": safe_float(metrics.get("review_burden_seconds")),
        "asr_windows_selected": safe_int(metrics.get("asr_windows_selected")),
        "asr_windows_evaluable": safe_int(metrics.get("asr_windows_evaluable")),
        "asr_windows_skipped": safe_int(metrics.get("asr_windows_skipped")),
        "asr_windows_selected_by_reason": metrics.get("asr_windows_selected_by_reason") or {},
        "asr_windows_skipped_by_reason": metrics.get("asr_windows_skipped_by_reason") or {},
        "suggest_drop_seconds": safe_float(metrics.get("suggest_drop_seconds")),
        "quarantine_seconds": safe_float(metrics.get("quarantine_seconds")),
        "needs_review_seconds": safe_float(metrics.get("needs_review_seconds")),
        "recommendation": summary.get("recommendation"),
    }
    row["assessment"] = assess_session(row)
    return row


def assess_session(row: dict[str, Any]) -> dict[str, Any]:
    status = row.get("status")
    if status != "ok":
        return {
            "class": "not_evaluable",
            "reason": str(row.get("status") or "missing_report"),
            "explanation": "Remote-forbidden evidence was not available for this session.",
        }

    leak_before = row.get("remote_token_leak_rate_before")
    leak_delta = row.get("remote_token_leak_delta")
    recall_delta = row.get("local_word_recall_delta")
    remote_rows = safe_int(row.get("remote_forbidden_rows"))
    local_rows = safe_int(row.get("local_speech_gate_rows"))
    selected_windows = safe_int(row.get("asr_windows_selected"))
    evaluable_windows = safe_int(row.get("asr_windows_evaluable"))
    skipped_windows = safe_int(row.get("asr_windows_skipped"))
    suggest_drop = safe_int(row.get("suggest_drop_count"))
    quarantine = safe_int(row.get("quarantine_count"))
    recall_regression = recall_delta is not None and safe_float(recall_delta) < -0.02
    leak_improved = leak_delta is not None and safe_float(leak_delta) < 0.0

    if recall_regression:
        return {
            "class": "unsafe_local_recall_regression",
            "reason": "local_recall_delta_below_minus_0_02",
            "explanation": "The guarded candidate would lose too many local Me words, so it is not safe to use.",
        }
    if leak_improved:
        return {
            "class": "safe_improved",
            "reason": "remote_leak_reduced_local_recall_preserved",
            "explanation": "The ASR-visible remote-token leak goes down and local Me recall stays inside the safety gate.",
        }
    if selected_windows <= 0:
        return {
            "class": "no_suspicious_windows",
            "reason": "selector_found_no_asr_windows",
            "explanation": "The coverage selector did not find suspicious windows worth ASR auditing in this session.",
        }
    if evaluable_windows <= 0 and skipped_windows > 0:
        return {
            "class": "window_not_selected",
            "reason": "all_candidate_windows_were_skipped",
            "explanation": "The selector found candidate windows, but cap or deduplication skipped all of them.",
        }
    if leak_before is not None and safe_float(leak_before) <= 0.0 and remote_rows > 0:
        return {
            "class": "no_baseline_asr_visible_leak",
            "reason": "local_fir_leak_rate_before_is_zero",
            "explanation": (
                "The selected ASR audit windows did not contain ASR-visible remote words in the current "
                "local_fir output, so the guard has nothing measurable to reduce without inventing a cleanup."
            ),
        }
    if remote_rows <= 0:
        return {
            "class": "no_remote_forbidden_rows",
            "reason": "no_remote_only_evidence_rows",
            "explanation": "The ASR clip audit did not produce remote-forbidden token rows for this session.",
        }
    if suggest_drop <= 0 and quarantine > 0:
        return {
            "class": "quarantine_only",
            "reason": "evidence_not_strong_enough_for_suggest_drop",
            "explanation": "The evidence found risk but only at quarantine strength; it is visible, but not safe to fix automatically.",
        }
    if local_rows <= 0:
        return {
            "class": "missing_local_gate",
            "reason": "near_end_preservation_rows_missing",
            "explanation": "The guard lacks local-speech preservation evidence for the sampled windows.",
        }
    return {
        "class": "not_improved",
        "reason": "remote_token_leak_not_reduced",
        "explanation": "The candidate does not reduce ASR-visible remote-token leakage enough to count as a safe improvement.",
    }


def group_assessments(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        assessment = row.get("assessment") if isinstance(row.get("assessment"), dict) else {}
        key = str(assessment.get("class") or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def sum_reason_maps(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        mapping = row.get(key)
        if not isinstance(mapping, dict):
            continue
        for reason, value in mapping.items():
            counts[str(reason)] = counts.get(str(reason), 0) + safe_int(value)
    return dict(sorted(counts.items()))


def acceptance_explanation(safe_improved: list[dict[str, Any]], ok_rows: list[dict[str, Any]]) -> str:
    if len(safe_improved) >= 2:
        sessions = ", ".join(f"`{row.get('session')}`" for row in safe_improved)
        return (
            "The two-session target is met: at least two sessions reduce ASR-visible remote leakage "
            f"while preserving local-word recall within the safety gate ({sessions})."
        )
    if len(safe_improved) == 1:
        non_safe = [row for row in ok_rows if row not in safe_improved]
        no_baseline = sum(
            1
            for row in non_safe
            if isinstance(row.get("assessment"), dict)
            and row["assessment"].get("class") == "no_baseline_asr_visible_leak"
        )
        quarantine_only = sum(
            1
            for row in non_safe
            if isinstance(row.get("assessment"), dict) and row["assessment"].get("class") == "quarantine_only"
        )
        recall_regressions = sum(
            1
            for row in non_safe
            if isinstance(row.get("assessment"), dict)
            and row["assessment"].get("class") == "unsafe_local_recall_regression"
        )
        parts = [
            "The two-session target is not met yet: only one session has a measurable ASR-visible "
            "remote-token reduction with local-word recall preserved."
        ]
        if no_baseline:
            parts.append(
                f"For {no_baseline} session(s), the sampled audit windows had zero baseline ASR-visible "
                "remote leakage, so the current clip audit cannot prove a safe correction."
            )
        if quarantine_only:
            parts.append(
                f"For {quarantine_only} session(s), evidence exists only at quarantine strength and is not "
                "safe enough for `suggest_drop`."
            )
        if recall_regressions:
            parts.append(
                f"For {recall_regressions} session(s), the local-word recall gate would fail."
            )
        parts.append(
            "The layer therefore remains shadow/review-only; the next work is broader window selection "
            "or stronger evidence, not default promotion."
        )
        return " ".join(parts)
    return (
        "The two-session target is not met: no session currently shows both ASR-visible remote-token "
        "reduction and preserved local-word recall. The layer remains shadow/review-only."
    )


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
        lines.append(f"- `{key}`: `{fmt_summary_value(value)}`")
    lines.extend(
        [
            "",
            "## Sessions",
            "",
            "| Session | Status | Assessment | Gate | Windows | Leak delta | Local recall delta | Guarded sec | Suggested drops | Quarantine | Needs review |",
            "|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in payload["sessions"]:
        assessment = row.get("assessment") if isinstance(row.get("assessment"), dict) else {}
        lines.append(
            "| `{session}` | `{status}` | `{assessment}` | `{gate}` | {windows} | {leak_delta} | {recall_delta} | {guarded} | {drop} | {quarantine} | {review} |".format(
                session=row.get("session"),
                status=row.get("status"),
                assessment=assessment.get("class"),
                gate=row.get("gate_reason"),
                windows=f"{row.get('asr_windows_evaluable') or 0}/{row.get('asr_windows_skipped') or 0}",
                leak_delta=fmt(row.get("remote_token_leak_delta")),
                recall_delta=fmt(row.get("local_word_recall_delta")),
                guarded=fmt(row.get("guarded_seconds")),
                drop=row.get("suggest_drop_count") or 0,
                quarantine=row.get("quarantine_count") or 0,
                review=row.get("needs_review_count") or 0,
            )
        )
    lines.extend(["", "## Two-Session Target", ""])
    acceptance = payload.get("acceptance") if isinstance(payload.get("acceptance"), dict) else {}
    lines.append(acceptance.get("explanation") or "No acceptance explanation was generated.")
    lines.extend(["", "### Per-session Explanation", ""])
    for row in payload["sessions"]:
        assessment = row.get("assessment") if isinstance(row.get("assessment"), dict) else {}
        lines.append(
            "- `{session}`: `{klass}` / `{reason}`. {explanation}".format(
                session=row.get("session"),
                klass=assessment.get("class"),
                reason=assessment.get("reason"),
                explanation=assessment.get("explanation"),
            )
        )
    lines.extend(["", "## Reading", ""])
    lines.append("- A negative leak delta means the guard reduced ASR-visible remote tokens versus local_fir.")
    lines.append("- A local recall delta below `-0.02` is treated as a local-speech regression.")
    lines.append("- `no_baseline_asr_visible_leak` means the sampled clip did not reproduce the harmful ASR-visible leak.")
    lines.append("- `target_status` says whether the current corpus meets the goal's two-session evidence target.")
    return "\n".join(lines) + "\n"


def fmt(value: Any) -> str:
    if value is None:
        return ""
    try:
        return f"{float(value):.6f}"
    except (TypeError, ValueError):
        return str(value)


def fmt_summary_value(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
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
        "assessment_classes": group_assessments(ok_rows),
        "asr_windows_selected": sum(safe_int(row.get("asr_windows_selected")) for row in ok_rows),
        "asr_windows_evaluable": sum(safe_int(row.get("asr_windows_evaluable")) for row in ok_rows),
        "asr_windows_skipped": sum(safe_int(row.get("asr_windows_skipped")) for row in ok_rows),
        "asr_windows_selected_by_reason": sum_reason_maps(ok_rows, "asr_windows_selected_by_reason"),
        "asr_windows_skipped_by_reason": sum_reason_maps(ok_rows, "asr_windows_skipped_by_reason"),
        "suggest_drop_count": sum(safe_int(row.get("suggest_drop_count")) for row in ok_rows),
        "quarantine_count": sum(safe_int(row.get("quarantine_count")) for row in ok_rows),
        "needs_review_count": sum(safe_int(row.get("needs_review_count")) for row in ok_rows),
        "guarded_seconds": round(sum(safe_float(row.get("guarded_seconds")) for row in ok_rows), 3),
        "review_burden_seconds": round(sum(safe_float(row.get("review_burden_seconds")) for row in ok_rows), 3),
        "suggest_drop_seconds": round(sum(safe_float(row.get("suggest_drop_seconds")) for row in ok_rows), 3),
        "quarantine_seconds": round(sum(safe_float(row.get("quarantine_seconds")) for row in ok_rows), 3),
        "needs_review_seconds": round(sum(safe_float(row.get("needs_review_seconds")) for row in ok_rows), 3),
        "target_status": target_status,
        "promotion_decision": "shadow_review_only_do_not_promote",
    }
    acceptance = {
        "two_session_target_met": len(safe_improved) >= 2,
        "safe_improved_sessions": [row.get("session") for row in safe_improved],
        "why_not_more_safe_sessions": [
            {
                "session": row.get("session"),
                "class": (row.get("assessment") or {}).get("class"),
                "reason": (row.get("assessment") or {}).get("reason"),
                "explanation": (row.get("assessment") or {}).get("explanation"),
            }
            for row in ok_rows
            if row not in safe_improved
        ],
        "explanation": acceptance_explanation(safe_improved, ok_rows),
    }
    payload = {"schema": SCHEMA, "summary": summary, "acceptance": acceptance, "sessions": rows}
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
