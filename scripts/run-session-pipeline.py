#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_VERSION = "0.1.0"
SCHEMA = "murmurmark.session_pipeline_run/v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the current MurmurMark post-recording pipeline for one session.")
    parser.add_argument("session", type=Path)
    parser.add_argument(
        "--model",
        type=Path,
        default=Path.home() / ".local/share/murmurmark/models/whisper.cpp/ggml-large-v3-q5_0.bin",
    )
    parser.add_argument("--language", default="ru")
    parser.add_argument(
        "--prompt-file",
        type=Path,
        default=None,
        help="Optional whisper prompt file. Ignored when omitted or when the file does not exist.",
    )
    parser.add_argument("--murmurmark-bin", type=Path, default=Path(".build/debug/murmurmark"))
    parser.add_argument(
        "--audio-judge-queue",
        type=Path,
        default=Path("sessions/_reports/audio-judge-v0/audio_judge_v0_queue_predictions.jsonl"),
    )
    parser.add_argument("--force-asr", action="store_true", help="Force whisper.cpp transcription even when cached raw ASR exists.")
    parser.add_argument("--reuse-asr-cache", action="store_true", help="Use cached raw ASR JSON and skip export/transcribe work.")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--skip-preprocess", action="store_true")
    parser.add_argument("--skip-transcription", action="store_true")
    parser.add_argument("--skip-audits", action="store_true")
    parser.add_argument("--skip-cleanup", action="store_true")
    parser.add_argument("--plan-only", action="store_true", help="Write the planned steps without executing them.")
    parser.add_argument("--max-clips", type=int, default=80)
    parser.add_argument("--max-audio-review-items", type=int, default=160)
    parser.add_argument(
        "--report",
        type=Path,
        help="Report path. Defaults to SESSION/derived/pipeline-run/pipeline_run_report.json.",
    )
    return parser.parse_args()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def rel(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def add_prompt(command: list[str], prompt_file: Path | None) -> list[str]:
    if prompt_file and prompt_file.exists():
        return command + ["--prompt-file", str(prompt_file)]
    return command


def step(name: str, command: list[str], *, enabled: bool = True, reason: str | None = None) -> dict[str, Any]:
    return {
        "name": name,
        "enabled": enabled,
        "skip_reason": reason if not enabled else None,
        "command": command,
    }


def build_steps(args: argparse.Namespace, repo_root: Path, session: Path) -> list[dict[str, Any]]:
    py = sys.executable
    prompt = args.prompt_file
    transcribe_base = [
        py,
        str(repo_root / "scripts/transcribe-simple-whispercpp.py"),
        str(session),
        "--model",
        str(args.model),
        "--language",
        args.language,
    ]
    transcribe_base = add_prompt(transcribe_base, prompt)
    current_transcribe = list(transcribe_base)
    if args.force_asr:
        current_transcribe.append("--force")
    if args.reuse_asr_cache:
        current_transcribe += ["--skip-export", "--skip-transcribe"]

    shadow_transcribe = list(transcribe_base) + ["--skip-export", "--skip-transcribe", "--repair-profile", "shadow_v2"]

    audio_judge_exists = args.audio_judge_queue.exists()
    return [
        step("swift_build", ["swift", "build"], enabled=not args.skip_build, reason="--skip-build"),
        step("inspect", [str(args.murmurmark_bin), "inspect", str(session)]),
        step(
            "echo_preprocess",
            [str(args.murmurmark_bin), "preprocess", str(session), "--echo", "clean", "--echo-engine", "local_fir"],
            enabled=not args.skip_preprocess,
            reason="--skip-preprocess",
        ),
        step("inspect_echo", [str(args.murmurmark_bin), "inspect", str(session), "--echo"], enabled=not args.skip_preprocess, reason="--skip-preprocess"),
        step("transcribe_current", current_transcribe, enabled=not args.skip_transcription, reason="--skip-transcription"),
        step("transcribe_shadow_v2", shadow_transcribe, enabled=not args.skip_transcription, reason="--skip-transcription"),
        step(
            "audit_local_recall",
            [py, str(repo_root / "scripts/audit-local-recall.py"), str(session), "--profile", "shadow_v2"],
            enabled=not args.skip_audits,
            reason="--skip-audits",
        ),
        step(
            "audit_group_overlaps",
            [
                py,
                str(repo_root / "scripts/audit-group-overlaps.py"),
                str(session),
                "--profile",
                "shadow_v2",
                "--min-overlap-sec",
                "0.5",
                "--review-threshold-sec",
                "2.0",
                "--write-clips",
                "--max-clips",
                str(args.max_clips),
            ],
            enabled=not args.skip_audits,
            reason="--skip-audits",
        ),
        step(
            "cleanup_v1",
            [py, str(repo_root / "scripts/apply-audit-cleanup.py"), str(session), "--input-profile", "shadow_v2", "--output-profile", "audit_cleanup_v1", "--mode", "conservative"],
            enabled=not args.skip_cleanup,
            reason="--skip-cleanup",
        ),
        step("synthesize_v1", [py, str(repo_root / "scripts/synthesize-simple-extractive.py"), str(session), "--transcript-profile", "audit_cleanup_v1"]),
        step(
            "build_audio_review_pack",
            [
                py,
                str(repo_root / "scripts/build-audio-review-pack.py"),
                str(session),
                "--profile",
                "audit_cleanup_v1",
                "--write-clips",
                "--max-items",
                str(args.max_audio_review_items),
            ],
            enabled=not args.skip_audits,
            reason="--skip-audits",
        ),
        step("audit_audio_review_pack", [py, str(repo_root / "scripts/audit-audio-review-pack.py"), str(session)], enabled=not args.skip_audits, reason="--skip-audits"),
        step(
            "plan_remote_leak_segment_repair",
            [py, str(repo_root / "scripts/plan-remote-leak-segment-repair.py"), str(session)],
            enabled=not args.skip_audits,
            reason="--skip-audits",
        ),
        step(
            "cleanup_v2",
            [py, str(repo_root / "scripts/apply-audit-cleanup.py"), str(session), "--input-profile", "audit_cleanup_v1", "--output-profile", "audit_cleanup_v2", "--mode", "conservative"],
            enabled=not args.skip_cleanup,
            reason="--skip-cleanup",
        ),
        step("synthesize_v2", [py, str(repo_root / "scripts/synthesize-simple-extractive.py"), str(session), "--transcript-profile", "audit_cleanup_v2"]),
        step(
            "cleanup_v3",
            [
                py,
                str(repo_root / "scripts/apply-audit-cleanup.py"),
                str(session),
                "--input-profile",
                "audit_cleanup_v2",
                "--output-profile",
                "audit_cleanup_v3",
                "--mode",
                "conservative",
                "--audio-judge-queue",
                str(args.audio_judge_queue),
            ],
            enabled=(not args.skip_cleanup and audio_judge_exists),
            reason="missing audio judge queue" if not audio_judge_exists else "--skip-cleanup",
        ),
        step(
            "synthesize_v3",
            [py, str(repo_root / "scripts/synthesize-simple-extractive.py"), str(session), "--transcript-profile", "audit_cleanup_v3"],
            enabled=audio_judge_exists,
            reason="missing audio judge queue",
        ),
        step(
            "cleanup_v4",
            [
                py,
                str(repo_root / "scripts/apply-audit-cleanup.py"),
                str(session),
                "--input-profile",
                "audit_cleanup_v3",
                "--output-profile",
                "audit_cleanup_v4",
                "--mode",
                "conservative",
                "--audio-judge-queue",
                str(args.audio_judge_queue),
            ],
            enabled=(not args.skip_cleanup and audio_judge_exists),
            reason="missing audio judge queue" if not audio_judge_exists else "--skip-cleanup",
        ),
        step(
            "synthesize_v4",
            [py, str(repo_root / "scripts/synthesize-simple-extractive.py"), str(session), "--transcript-profile", "audit_cleanup_v4"],
            enabled=audio_judge_exists,
            reason="missing audio judge queue",
        ),
        step("synthesize_auto", [py, str(repo_root / "scripts/synthesize-simple-extractive.py"), str(session), "--transcript-profile", "auto"]),
        step(
            "audit_transcript_order",
            [py, str(repo_root / "scripts/audit-transcript-order.py"), str(session), "--profile", "auto"],
            enabled=not args.skip_audits,
            reason="--skip-audits",
        ),
        step(
            "session_readiness",
            [
                py,
                str(repo_root / "scripts/report-session-quality.py"),
                str(session),
                "--out-dir",
                str(session / "derived/readiness/session-quality"),
                "--write-session-readiness",
            ],
        ),
    ]


def run_step(item: dict[str, Any], repo_root: Path, plan_only: bool) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc).isoformat()
    started = time.monotonic()
    result = {**item, "status": "planned" if plan_only else "pending", "started_at": started_at}
    if not item["enabled"]:
        result["status"] = "skipped"
        result["finished_at"] = datetime.now(timezone.utc).isoformat()
        result["duration_sec"] = 0.0
        return result
    if plan_only:
        result["finished_at"] = datetime.now(timezone.utc).isoformat()
        result["duration_sec"] = 0.0
        return result
    completed = subprocess.run(item["command"], cwd=repo_root, text=True, capture_output=True, check=False)
    result.update(
        {
            "status": "passed" if completed.returncode == 0 else "failed",
            "returncode": completed.returncode,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "duration_sec": round(time.monotonic() - started, 3),
            "stdout_tail": completed.stdout[-4000:],
            "stderr_tail": completed.stderr[-4000:],
        }
    )
    return result


def print_step_start(item: dict[str, Any], plan_only: bool) -> None:
    if not item["enabled"]:
        prefix = "[skip]"
    elif plan_only:
        prefix = "[plan]"
    else:
        prefix = "[run]"
    print(f"{prefix} {item['name']}", flush=True)


def print_step_result(result: dict[str, Any]) -> None:
    status = str(result.get("status") or "unknown")
    duration = float(result.get("duration_sec") or 0.0)
    if status in {"planned", "skipped"}:
        print(f"[{status}] {result['name']}", flush=True)
        return
    print(f"[{status}] {result['name']} ({duration:.1f}s)", flush=True)
    if status == "failed":
        for key in ("stderr_tail", "stdout_tail"):
            tail = str(result.get(key) or "").strip()
            if tail:
                print(f"{key}:\n{tail}", flush=True)


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    session = args.session.expanduser()
    report_path = args.report.expanduser() if args.report else session / "derived/pipeline-run/pipeline_run_report.json"
    steps = build_steps(args, repo_root, session)
    results: list[dict[str, Any]] = []
    started_at = datetime.now(timezone.utc).isoformat()
    final_status = "passed"

    for item in steps:
        print_step_start(item, args.plan_only)
        result = run_step(item, repo_root, args.plan_only)
        results.append(result)
        print_step_result(result)
        if result["status"] == "failed":
            final_status = "failed"
            break

    quality_path = session / "derived/synthesis-simple/extractive/quality_verdict.json"
    readiness_path = session / "derived/readiness/session_readiness.json"
    remote_leak_plan_path = session / "derived/transcript-simple/whisper-cpp/remote-leak-repair/remote_leak_segment_repair_plan.json"
    quality = read_json(quality_path)
    readiness = read_json(readiness_path)
    synthesis_profile = quality.get("selected_transcript_profile") if isinstance(quality, dict) else None
    readiness_profile = readiness.get("selected_profile") if isinstance(readiness, dict) else None
    synthesis_verdict = quality.get("verdict") if isinstance(quality, dict) else None
    readiness_verdict = readiness.get("verdict") if isinstance(readiness, dict) else None
    selected_profile = readiness_profile or synthesis_profile
    selected_verdict = readiness_verdict or synthesis_verdict
    report = {
        "schema": SCHEMA,
        "generator": {"name": "run-session-pipeline", "version": SCRIPT_VERSION},
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "session": str(session),
        "status": "planned" if args.plan_only else final_status,
        "inputs": {
            "model": str(args.model),
            "language": args.language,
            "prompt_file": str(args.prompt_file) if args.prompt_file and args.prompt_file.exists() else None,
            "audio_judge_queue": str(args.audio_judge_queue) if args.audio_judge_queue.exists() else None,
        },
        "outputs": {
            "quality_verdict": rel(quality_path, session) if quality_path.exists() else None,
            "session_readiness": rel(readiness_path, session) if readiness_path.exists() else None,
            "remote_leak_segment_repair_plan": rel(remote_leak_plan_path, session) if remote_leak_plan_path.exists() else None,
            "selected_transcript_profile": selected_profile,
            "synthesis_selected_transcript_profile": synthesis_profile,
            "readiness_selected_profile": readiness_profile,
            "verdict": selected_verdict,
            "synthesis_verdict": synthesis_verdict,
            "readiness_verdict": readiness_verdict,
            "use_gate": readiness.get("use_gate") if isinstance(readiness, dict) else None,
        },
        "steps": results,
    }
    write_json(report_path, report)
    print(f"pipeline_run_report: {report_path}", flush=True)
    if report["outputs"]["selected_transcript_profile"]:
        print(f"selected_profile: {report['outputs']['selected_transcript_profile']}", flush=True)
    if report["outputs"]["verdict"]:
        print(f"quality_verdict: {report['outputs']['verdict']}", flush=True)
    return 0 if report["status"] in {"passed", "planned"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
