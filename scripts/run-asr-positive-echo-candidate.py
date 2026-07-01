#!/usr/bin/env python3
"""Materialize the ASR-positive Echo Guard audio candidate as a shadow profile."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


SCRIPT_VERSION = "0.1.0"
SCHEMA = "murmurmark.echo.asr_positive_echo_candidate_report/v1"
DEFAULT_CANDIDATE = "coverage_v2_remote_gate_local_fir"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run and report one ASR-positive Echo Guard candidate.")
    parser.add_argument("session", type=Path)
    parser.add_argument("--candidate", default=DEFAULT_CANDIDATE)
    parser.add_argument("--skip-lab", action="store_true", help="Reuse existing offline_aec_v2 artifacts.")
    parser.add_argument("--asr-max-clips", type=int, default=2)
    parser.add_argument("--asr-max-local-clips", type=int, default=1)
    parser.add_argument("--asr-max-risk-clips", type=int, default=2)
    parser.add_argument("--asr-window-profile", default="coverage_v2")
    parser.add_argument("--out-dir", type=Path)
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
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


def script_path(name: str) -> Path:
    return Path(__file__).resolve().parent / name


def run_lab(args: argparse.Namespace) -> None:
    command = [
        sys.executable,
        str(script_path("echo-guard-offline-aec-v2-lab.py")),
        str(args.session),
        "--asr-audit",
        "--asr-window-profile",
        args.asr_window_profile,
        "--asr-max-clips",
        str(args.asr_max_clips),
        "--asr-max-local-clips",
        str(args.asr_max_local_clips),
        "--asr-max-risk-clips",
        str(args.asr_max_risk_clips),
        "--asr-candidate-keys",
        args.candidate,
    ]
    subprocess.run(command, check=True)


def candidate_row(session: Path, candidate: str) -> dict[str, Any]:
    for row in read_jsonl(session / "derived/preprocess/echo/offline_aec_v2_candidates.jsonl"):
        if row.get("candidate") == candidate:
            return row
    return {}


def coverage_plan_summary(session: Path) -> dict[str, Any]:
    rows = read_jsonl(session / "derived/preprocess/echo/offline_aec_v2_coverage_gate_plan.jsonl")
    applied = [row for row in rows if row.get("applied") is True]
    skipped = [row for row in rows if row.get("applied") is not True]
    by_reason: dict[str, int] = {}
    for row in rows:
        reason = str(row.get("decision_reason") or "unknown")
        by_reason[reason] = by_reason.get(reason, 0) + 1
    return {
        "windows": len(rows),
        "applied_windows": len(applied),
        "skipped_windows": len(skipped),
        "decision_reasons": dict(sorted(by_reason.items())),
        "path": "derived/preprocess/echo/offline_aec_v2_coverage_gate_plan.jsonl",
    }


def session_quality_row(session: Path) -> dict[str, Any]:
    payload = read_json(session / "derived/readiness/session-quality/session_quality_report.json")
    rows = payload.get("sessions") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        return {}
    first = rows[0]
    return first if isinstance(first, dict) else {}


def remote_forbidden_metrics(session: Path) -> dict[str, Any]:
    payload = read_json(session / "derived/audit/remote-forbidden/remote_forbidden_summary.json")
    metrics = payload.get("metrics") if isinstance(payload, dict) and isinstance(payload.get("metrics"), dict) else {}
    gates = payload.get("gates") if isinstance(payload, dict) and isinstance(payload.get("gates"), dict) else {}
    return {
        "status": payload.get("status") if isinstance(payload, dict) else None,
        "gate_passed": gates.get("passed"),
        "gate_reason": gates.get("reason"),
        "remote_forbidden_rows": metrics.get("remote_forbidden_rows"),
        "review_burden_seconds": metrics.get("review_burden_seconds"),
        "suggest_drop_seconds": metrics.get("suggest_drop_seconds"),
        "quarantine_seconds": metrics.get("quarantine_seconds"),
        "needs_review_seconds": metrics.get("needs_review_seconds"),
    }


def target_me_metrics(session: Path) -> dict[str, Any]:
    payload = read_json(session / "derived/audit/target-me/target_me_summary.json")
    metrics = payload.get("metrics") if isinstance(payload, dict) and isinstance(payload.get("metrics"), dict) else {}
    return {
        "status": payload.get("status") if isinstance(payload, dict) else None,
        "rows": metrics.get("rows"),
        "keep_evidence_rows": metrics.get("keep_evidence_rows"),
        "keep_evidence_seconds": metrics.get("keep_evidence_seconds"),
        "needs_review_rows": metrics.get("needs_review_rows"),
        "needs_review_seconds": metrics.get("needs_review_seconds"),
    }


def assess_candidate(
    *,
    leak_report: dict[str, Any] | None,
    preservation_report: dict[str, Any] | None,
    candidate: str,
) -> dict[str, Any]:
    if not leak_report:
        return {"status": "skipped", "reason": "missing_offline_aec_v2_asr_leak_report"}
    if leak_report.get("mode") != "faster_whisper_clip_audit":
        return {
            "status": "skipped",
            "reason": str(leak_report.get("skipped_reason") or "asr_clip_audit_not_available"),
        }
    candidates = leak_report.get("candidates") if isinstance(leak_report.get("candidates"), dict) else {}
    stats = candidates.get(candidate)
    if not isinstance(stats, dict):
        return {"status": "failed", "reason": "candidate_missing_from_asr_report"}
    baseline_leak = stats.get("local_fir_remote_token_leak_rate")
    leak_delta = stats.get("remote_token_leak_delta")
    recall_delta = stats.get("local_only_word_recall_delta")
    if baseline_leak is None:
        return {"status": "skipped", "reason": "baseline_local_fir_leak_missing"}
    if safe_float(baseline_leak) <= 0.0:
        return {"status": "not_applicable", "reason": "no_baseline_asr_visible_remote_leak"}
    if leak_delta is None or recall_delta is None:
        return {"status": "failed", "reason": "candidate_asr_metrics_missing"}
    if safe_float(recall_delta) < -0.02:
        return {"status": "failed", "reason": "local_word_recall_regression"}
    if safe_float(leak_delta) < -0.02:
        return {"status": "passed", "reason": "remote_token_leak_reduced_without_local_recall_regression"}
    return {"status": "failed", "reason": "remote_token_leak_not_reduced"}


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    session = args.session
    echo_dir = session / "derived/preprocess/echo"
    audio_path = f"derived/preprocess/audio/mic_clean_offline_aec_v2_{args.candidate}.wav"
    leak_report = read_json(echo_dir / "offline_aec_v2_asr_leak_report.json")
    preservation_report = read_json(echo_dir / "offline_aec_v2_near_end_preservation_report.json")
    report = read_json(echo_dir / "offline_aec_v2_report.json") or {}
    candidates = leak_report.get("candidates") if isinstance(leak_report, dict) and isinstance(leak_report.get("candidates"), dict) else {}
    stats = candidates.get(args.candidate) if isinstance(candidates.get(args.candidate), dict) else {}
    quality = session_quality_row(session)
    assessment = assess_candidate(leak_report=leak_report, preservation_report=preservation_report, candidate=args.candidate)
    candidate_meta = candidate_row(session, args.candidate)
    return {
        "schema": SCHEMA,
        "generator": {"name": "run-asr-positive-echo-candidate", "version": SCRIPT_VERSION},
        "session": str(session),
        "profile": args.candidate,
        "engine": "offline_aec_v2",
        "mode": "experimental_shadow_only",
        "default_pipeline_unchanged": True,
        "local_fir_remains_default": True,
        "promotion_decision": "shadow_only_do_not_promote",
        "inputs": {
            "mic": "derived/preprocess/audio/mic_raw_for_asr.wav",
            "remote": "derived/preprocess/audio/remote_for_aec.wav",
            "speaker_state": "derived/preprocess/echo/speaker_state.jsonl",
            "local_fir": "derived/preprocess/audio/mic_clean_local_fir.wav",
        },
        "outputs": {
            "candidate_clean_mic": audio_path,
            "candidate_echo_hat": f"derived/preprocess/audio/echo_hat_offline_aec_v2_{args.candidate}.wav",
            "report": "derived/preprocess/echo/asr_positive_echo_candidate_report.json",
            "review": "derived/preprocess/echo/asr_positive_echo_candidate_report.md",
        },
        "source_reports": {
            "offline_aec_v2_report": "derived/preprocess/echo/offline_aec_v2_report.json",
            "asr_leak_report": "derived/preprocess/echo/offline_aec_v2_asr_leak_report.json",
            "near_end_preservation_report": "derived/preprocess/echo/offline_aec_v2_near_end_preservation_report.json",
            "coverage_gate_plan": "derived/preprocess/echo/offline_aec_v2_coverage_gate_plan.jsonl",
        },
        "assessment": assessment,
        "metrics": {
            "candidate_kind": stats.get("candidate_kind"),
            "remote_token_leak_rate_local_fir": stats.get("local_fir_remote_token_leak_rate"),
            "remote_token_leak_rate_candidate": stats.get("remote_token_leak_rate"),
            "remote_token_leak_delta": stats.get("remote_token_leak_delta"),
            "local_word_recall_local_fir": stats.get("local_fir_local_only_word_recall"),
            "local_word_recall_candidate": stats.get("local_only_word_recall"),
            "local_word_recall_delta": stats.get("local_only_word_recall_delta"),
            "asr_audio_candidate_gate_passed": leak_report.get("asr_audio_candidate_gate_passed") if isinstance(leak_report, dict) else None,
            "asr_audio_candidate_gate_reason": leak_report.get("asr_audio_candidate_gate_reason") if isinstance(leak_report, dict) else None,
            "asr_selected_audio_candidate": leak_report.get("asr_selected_audio_candidate") if isinstance(leak_report, dict) else None,
            "coverage_gate": coverage_plan_summary(session),
            "review_burden_seconds": quality.get("review_burden_sec"),
            "review_burden_ratio": quality.get("review_burden_ratio"),
            "remote_duplicate_in_me_seconds": quality.get("remote_duplicate_in_me_seconds"),
            "target_me": target_me_metrics(session),
            "remote_forbidden": remote_forbidden_metrics(session),
        },
        "candidate": {
            "metadata": candidate_meta,
            "asr_stats": stats,
        },
        "lab_summary": report.get("summary") if isinstance(report.get("summary"), dict) else {},
    }


def render_markdown(payload: dict[str, Any]) -> str:
    metrics = payload["metrics"]
    assessment = payload["assessment"]
    coverage = metrics.get("coverage_gate") if isinstance(metrics.get("coverage_gate"), dict) else {}
    lines = [
        "# ASR-Positive Echo Candidate",
        "",
        f"- Session: `{payload['session']}`",
        f"- Profile: `{payload['profile']}`",
        f"- Mode: `{payload['mode']}`",
        f"- Assessment: `{assessment.get('status')}` / `{assessment.get('reason')}`",
        f"- Promotion: `{payload['promotion_decision']}`",
        f"- Default unchanged: `{payload['default_pipeline_unchanged']}`",
        "",
        "## ASR Metrics",
        "",
        f"- Local-fir remote token leak: `{metrics.get('remote_token_leak_rate_local_fir')}`",
        f"- Candidate remote token leak: `{metrics.get('remote_token_leak_rate_candidate')}`",
        f"- Remote token leak delta: `{metrics.get('remote_token_leak_delta')}`",
        f"- Local word recall delta: `{metrics.get('local_word_recall_delta')}`",
        f"- Review burden seconds: `{metrics.get('review_burden_seconds')}`",
        f"- Remote duplicate in Me seconds: `{metrics.get('remote_duplicate_in_me_seconds')}`",
        "",
        "## Coverage Gate",
        "",
        f"- Windows: `{coverage.get('windows')}`",
        f"- Applied: `{coverage.get('applied_windows')}`",
        f"- Skipped: `{coverage.get('skipped_windows')}`",
        f"- Reasons: `{json.dumps(coverage.get('decision_reasons') or {}, ensure_ascii=False, sort_keys=True)}`",
        "",
        "## Reading",
        "",
        "- `passed` means this candidate reduced ASR-visible remote tokens versus `local_fir` without local-word recall regression on sampled windows.",
        "- `not_applicable` usually means sampled `local_fir` clips did not expose ASR-visible remote leakage.",
        "- This report never promotes the candidate to `mic_for_asr.wav`; promotion requires separate corpus gates.",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    if not args.skip_lab:
        run_lab(args)
    payload = build_report(args)
    out_dir = args.out_dir or args.session / "derived/preprocess/echo"
    json_path = out_dir / "asr_positive_echo_candidate_report.json"
    md_path = out_dir / "asr_positive_echo_candidate_report.md"
    write_json(json_path, payload)
    md_path.write_text(render_markdown(payload), encoding="utf-8")
    print(f"asr_positive_echo_candidate_report: {json_path}")
    print(f"profile: {payload['profile']}")
    print(f"assessment: {payload['assessment'].get('status')}")
    print(f"reason: {payload['assessment'].get('reason')}")
    print(f"promotion_decision: {payload['promotion_decision']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
