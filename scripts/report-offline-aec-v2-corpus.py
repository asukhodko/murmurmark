#!/usr/bin/env python3
"""Summarize offline_aec_v2 lab reports across MurmurMark sessions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


SCHEMA = "murmurmark.echo.offline_aec_v2_corpus_report/v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build corpus summary for offline_aec_v2 lab reports.")
    parser.add_argument("sessions", nargs="+", type=Path)
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("sessions/_reports/offline-aec-v2"),
    )
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def assess_audio_candidate(row: dict[str, Any]) -> dict[str, str]:
    if row.get("status") != "ok":
        return {"class": "missing_report", "reason": str(row.get("status") or "missing_report")}
    if row.get("asr_audit_mode") != "faster_whisper_clip_audit":
        return {
            "class": "asr_audit_inconclusive",
            "reason": "asr_audit_not_available",
        }
    baseline_leak = row.get("asr_remote_token_leak_rate_local_fir")
    leak_delta = row.get("asr_audio_candidate_remote_token_leak_delta")
    recall_delta = row.get("asr_audio_candidate_local_word_recall_delta")
    if baseline_leak is None:
        return {
            "class": "asr_audit_inconclusive",
            "reason": "baseline_metrics_missing",
        }
    if number(baseline_leak) <= 0.0:
        return {
            "class": "no_baseline_asr_visible_leak",
            "reason": "local_fir_leak_rate_before_is_zero",
        }
    if leak_delta is None or recall_delta is None:
        return {
            "class": "asr_audit_inconclusive",
            "reason": "candidate_metrics_missing",
        }
    if number(recall_delta) < -0.02:
        return {
            "class": "local_recall_risk",
            "reason": "audio_candidate_loses_local_words_vs_local_fir",
        }
    if number(leak_delta) < -0.02:
        return {
            "class": "safe_improved",
            "reason": "audio_candidate_reduced_remote_tokens_without_local_recall_regression",
        }
    return {
        "class": "candidate_not_better",
        "reason": "audio_candidate_remote_token_leak_not_reduced",
    }


def row_for_session(session: Path) -> dict[str, Any]:
    report_path = session / "derived" / "preprocess" / "echo" / "offline_aec_v2_report.json"
    report = read_json(report_path)
    if not report:
        return {
            "session": str(session),
            "status": "missing_report",
            "report": str(report_path),
        }
    selected = report.get("selected_candidate") if isinstance(report.get("selected_candidate"), dict) else {}
    selected_metrics = selected.get("metrics") if isinstance(selected.get("metrics"), dict) else {}
    baseline_root = report.get("baseline") if isinstance(report.get("baseline"), dict) else {}
    baseline = baseline_root.get("local_fir") if isinstance(baseline_root.get("local_fir"), dict) else {}
    selected_remote = number(selected_metrics.get("remote_only_median_reduction_db"))
    baseline_remote = number(baseline.get("remote_only_median_reduction_db"))
    selected_harmful = number(selected_metrics.get("harmful_remote_seconds_in_me_proxy"))
    baseline_harmful = number(baseline.get("harmful_remote_seconds_in_me_proxy"))
    selected_local = number(selected_metrics.get("local_only_word_recall_proxy"), 1.0)
    baseline_local = number(baseline.get("local_only_word_recall_proxy"), 1.0)
    selected_opening = number(selected_metrics.get("opening_ack_recall_proxy"), 1.0)
    baseline_opening = number(baseline.get("opening_ack_recall_proxy"), 1.0)
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    asr_leak = read_json(session / "derived" / "preprocess" / "echo" / "offline_aec_v2_asr_leak_report.json") or {}
    asr_preservation = (
        read_json(session / "derived" / "preprocess" / "echo" / "offline_aec_v2_near_end_preservation_report.json")
        or {}
    )
    candidates = asr_leak.get("candidates") if isinstance(asr_leak.get("candidates"), dict) else {}
    audio_candidate_key = asr_leak.get("asr_selected_audio_candidate")
    audio_candidate = candidates.get(str(audio_candidate_key)) if audio_candidate_key else {}
    if not isinstance(audio_candidate, dict):
        audio_candidate = {}
    row = {
        "session": str(session),
        "status": "ok",
        "report": str(report_path),
        "selected_candidate": summary.get("selected_candidate"),
        "candidate_gate_reason": summary.get("candidate_gate_reason"),
        "candidate_gate_passed": summary.get("candidate_gate_passed"),
        "asr_selected_candidate": summary.get("asr_selected_candidate") or asr_leak.get("asr_selected_candidate"),
        "asr_candidate_gate_passed": summary.get("asr_candidate_gate_passed") or asr_leak.get("asr_candidate_gate_passed"),
        "asr_candidate_gate_reason": summary.get("asr_candidate_gate_reason") or asr_leak.get("asr_candidate_gate_reason"),
        "asr_selected_audio_candidate": summary.get("asr_selected_audio_candidate")
        or asr_leak.get("asr_selected_audio_candidate"),
        "asr_audio_candidate_gate_passed": summary.get("asr_audio_candidate_gate_passed")
        or asr_leak.get("asr_audio_candidate_gate_passed"),
        "asr_audio_candidate_gate_reason": summary.get("asr_audio_candidate_gate_reason")
        or asr_leak.get("asr_audio_candidate_gate_reason"),
        "promotion_decision": summary.get("promotion_decision"),
        "remote_only_median_reduction_db_local_fir": baseline_remote,
        "remote_only_median_reduction_db_offline_aec_v2": selected_remote,
        "remote_only_reduction_delta_db": round(selected_remote - baseline_remote, 3),
        "harmful_remote_seconds_local_fir_proxy": baseline_harmful,
        "harmful_remote_seconds_offline_aec_v2_proxy": selected_harmful,
        "harmful_remote_seconds_delta_proxy": round(selected_harmful - baseline_harmful, 3),
        "local_only_word_recall_local_fir_proxy": baseline_local,
        "local_only_word_recall_offline_aec_v2_proxy": selected_local,
        "local_only_word_recall_delta_proxy": round(selected_local - baseline_local, 6),
        "opening_ack_recall_local_fir_proxy": baseline_opening,
        "opening_ack_recall_offline_aec_v2_proxy": selected_opening,
        "opening_ack_recall_delta_proxy": round(selected_opening - baseline_opening, 6),
        "asr_audit_mode": asr_leak.get("mode"),
        "asr_remote_token_leak_rate_local_fir": asr_leak.get("local_fir_remote_token_leak_rate"),
        "asr_remote_token_leak_rate_offline_aec_v2": asr_leak.get("remote_token_leak_rate"),
        "asr_remote_token_leak_delta": asr_leak.get("remote_token_leak_delta"),
        "asr_local_word_recall_local_fir": asr_preservation.get("local_fir_local_only_word_recall"),
        "asr_local_word_recall_offline_aec_v2": asr_preservation.get("local_only_word_recall"),
        "asr_local_word_recall_delta": asr_preservation.get("local_only_word_recall_delta"),
        "asr_audio_candidate_remote_token_leak_rate": audio_candidate.get("remote_token_leak_rate"),
        "asr_audio_candidate_remote_token_leak_delta": audio_candidate.get("remote_token_leak_delta"),
        "asr_audio_candidate_local_word_recall": audio_candidate.get("local_only_word_recall"),
        "asr_audio_candidate_local_word_recall_delta": audio_candidate.get("local_only_word_recall_delta"),
    }
    assessment = assess_audio_candidate(row)
    row["audio_candidate_assessment"] = assessment["class"]
    row["audio_candidate_assessment_reason"] = assessment["reason"]
    return row


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def render_markdown(payload: dict[str, Any]) -> str:
    rows = payload["sessions"]
    lines = [
        "# offline_aec_v2 Corpus Report",
        "",
        "This report compares shadow offline AEC v2 candidates with the current local_fir baseline.",
        "It is diagnostic only; no candidate is promoted to default from this report.",
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
            "| Session | Candidate | Gate | Remote dB delta | Harmful sec delta | Local recall delta | Opening delta |",
            "|---|---|---|---:|---:|---:|---:|",
        ]
    )
    for row in rows:
        if row.get("status") != "ok":
            lines.append(f"| `{row['session']}` | missing | `{row['status']}` | | | | |")
            continue
        lines.append(
            "| `{session}` | `{candidate}` | `{gate}` | {remote_delta:.3f} | {harmful_delta:.3f} | {local_delta:.6f} | {opening_delta:.6f} |".format(
                session=row["session"],
                candidate=row.get("selected_candidate"),
                gate=row.get("candidate_gate_reason"),
                remote_delta=number(row.get("remote_only_reduction_delta_db")),
                harmful_delta=number(row.get("harmful_remote_seconds_delta_proxy")),
                local_delta=number(row.get("local_only_word_recall_delta_proxy")),
                opening_delta=number(row.get("opening_ack_recall_delta_proxy")),
            )
        )
    lines.extend(
        [
            "",
            "## ASR Clip Audit",
            "",
            "| Session | Mode | ASR candidate | ASR gate | Remote token leak delta | Local word recall delta | Audio candidate | Audio assessment | Audio leak delta | Audio recall delta |",
            "|---|---|---|---|---:|---:|---|---|---:|---:|",
        ]
    )
    for row in rows:
        if row.get("status") != "ok":
            continue
        lines.append(
            "| `{session}` | `{mode}` | `{candidate}` | `{gate}` | {leak_delta} | {recall_delta} | `{audio_candidate}` | `{audio_assessment}` | {audio_leak_delta} | {audio_recall_delta} |".format(
                session=row["session"],
                mode=row.get("asr_audit_mode"),
                candidate=row.get("asr_selected_candidate"),
                gate=row.get("asr_candidate_gate_reason"),
                leak_delta=fmt_optional(row.get("asr_remote_token_leak_delta")),
                recall_delta=fmt_optional(row.get("asr_local_word_recall_delta")),
                audio_candidate=row.get("asr_selected_audio_candidate"),
                audio_assessment=row.get("audio_candidate_assessment"),
                audio_leak_delta=fmt_optional(row.get("asr_audio_candidate_remote_token_leak_delta")),
                audio_recall_delta=fmt_optional(row.get("asr_audio_candidate_local_word_recall_delta")),
            )
        )
    not_improved = [
        row for row in rows
        if row.get("status") == "ok" and row.get("audio_candidate_assessment") != "safe_improved"
    ]
    if not_improved:
        lines.extend(["", "## Why Audio Candidate Did Not Improve More", ""])
        for row in not_improved:
            lines.append(
                "- `{session}`: `{klass}` / `{reason}`".format(
                    session=row.get("session"),
                    klass=row.get("audio_candidate_assessment"),
                    reason=row.get("audio_candidate_assessment_reason"),
                )
            )
    lines.extend(
        [
            "",
            "## Reading The Result",
            "",
            "- Positive `Remote dB delta` means offline_aec_v2 reduced remote-only energy more than local_fir.",
            "- Negative `Harmful sec delta` means offline_aec_v2 reduced proxy harmful remote seconds.",
            "- Negative local/opening deltas are preservation regressions.",
            "- `blocked_by_quality_gates` is expected until ASR-level leak and local recall gates pass.",
            "",
        ]
    )
    return "\n".join(lines)


def fmt_optional(value: Any) -> str:
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
    summary = {
        "sessions": len(rows),
        "reports_found": len(ok_rows),
        "candidate_gate_passed": sum(1 for row in ok_rows if row.get("candidate_gate_passed") is True),
        "harmful_proxy_improved": sum(1 for row in ok_rows if number(row.get("harmful_remote_seconds_delta_proxy")) < 0),
        "remote_reduction_improved": sum(1 for row in ok_rows if number(row.get("remote_only_reduction_delta_db")) > 0),
        "local_recall_regressions": sum(1 for row in ok_rows if number(row.get("local_only_word_recall_delta_proxy")) < -0.02),
        "opening_recall_regressions": sum(1 for row in ok_rows if number(row.get("opening_ack_recall_delta_proxy")) < 0),
        "asr_audit_reports": sum(1 for row in ok_rows if row.get("asr_audit_mode") == "faster_whisper_clip_audit"),
        "asr_candidate_gate_passed": sum(1 for row in ok_rows if row.get("asr_candidate_gate_passed") is True),
        "asr_remote_token_leak_improved": sum(
            1 for row in ok_rows
            if row.get("asr_remote_token_leak_delta") is not None
            and number(row.get("asr_remote_token_leak_delta")) < 0
        ),
        "asr_audio_candidate_gate_passed": sum(
            1 for row in ok_rows
            if row.get("asr_audio_candidate_gate_passed") is True
        ),
        "asr_audio_candidate_safe_improved": sum(
            1 for row in ok_rows
            if row.get("audio_candidate_assessment") == "safe_improved"
        ),
        "asr_audio_candidate_assessment_classes": {
            klass: sum(1 for row in ok_rows if row.get("audio_candidate_assessment") == klass)
            for klass in sorted({str(row.get("audio_candidate_assessment")) for row in ok_rows})
        },
        "asr_local_word_recall_regressions": sum(
            1 for row in ok_rows
            if row.get("asr_local_word_recall_delta") is not None
            and number(row.get("asr_local_word_recall_delta")) < -0.02
        ),
        "promotion_decision": "do_not_promote_from_v0_corpus_report",
    }
    payload = {
        "schema": SCHEMA,
        "summary": summary,
        "sessions": rows,
    }
    args.out_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.out_dir / "offline_aec_v2_corpus_report.json"
    md_path = args.out_dir / "offline_aec_v2_corpus_report.md"
    write_json(json_path, payload)
    md_path.write_text(render_markdown(payload), encoding="utf-8")
    print(f"offline_aec_v2_corpus_report: {json_path}")
    print(f"markdown: {md_path}")
    print(f"reports_found: {summary['reports_found']}/{summary['sessions']}")
    print(f"harmful_proxy_improved: {summary['harmful_proxy_improved']}")
    print(f"remote_reduction_improved: {summary['remote_reduction_improved']}")
    print(f"promotion_decision: {summary['promotion_decision']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
