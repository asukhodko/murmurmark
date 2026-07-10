#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
import json
import os
import signal
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_VERSION = "0.1.3"
SCHEMA = "murmurmark.session_pipeline_run/v1"
RUN_STATE_SCHEMA = "murmurmark.pipeline_run_state/v1"
INTERRUPTED_CAPTURE_WARNING_MARKERS = (
    "stream stopped with error",
    "capture produced no audio samples",
    "capture ended unexpectedly",
    "capture finalized as partial because both mic and remote tracks are silent",
)
INTERRUPTED_CAPTURE_STOP_REASONS = {"stream_stopped", "capture_stalled", "sigterm", "sighup"}
SPARSE_CAPTURE_MIN_DURATION_SEC = 180.0
SPARSE_CAPTURE_ACTIVE_DB = -60.0
SPARSE_CAPTURE_MIN_ACTIVE_RATIO = 0.03
SPARSE_CAPTURE_MIN_ACTIVE_SEC = 15.0
SPARSE_CAPTURE_MAX_REQUIRED_ACTIVE_SEC = 60.0


STEP_COST_HINTS: dict[str, dict[str, str]] = {
    "swift_build": {
        "cost": "medium",
        "reason": "builds the Swift CLI when --skip-build is not used",
    },
    "echo_preprocess": {
        "cost": "medium",
        "reason": "runs Echo Guard and writes ASR-ready mic audio",
    },
    "transcribe_current": {
        "cost": "heavy",
        "reason": "runs whisper.cpp ASR unless cached raw ASR is reused",
    },
    "check_asr_chunk_cache": {
        "cost": "light",
        "reason": "verifies that raw ASR JSON can be rebuilt from cached chunks",
    },
    "materialize_live_asr_cache": {
        "cost": "light",
        "reason": "uses live ASR chunks only when they are batch-cache compatible",
    },
    "audit_group_overlaps": {
        "cost": "medium",
        "reason": "reads audio and creates overlap audit features/clips",
    },
    "build_audio_review_pack": {
        "cost": "medium",
        "reason": "cuts review clips for risky audio/transcript regions",
    },
    "audit_audio_review_pack": {
        "cost": "medium",
        "reason": "classifies the generated audio review pack",
    },
    "audit_stronger_audio_judge": {
        "cost": "heavy",
        "reason": "runs local faster-whisper on selected short review clips when the model is available",
    },
    "plan_remote_leak_segment_repair": {
        "cost": "medium",
        "reason": "plans segment-level remote leak repair candidates",
    },
    "remote_forbidden_evidence": {
        "cost": "light",
        "reason": "materializes remote-forbidden evidence rows when offline_aec_v2 ASR audit exists",
    },
}


def format_duration(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.1f}s"
    minutes = seconds / 60.0
    if minutes < 90:
        return f"{minutes:.1f}m"
    return f"{minutes / 60.0:.1f}h"


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
    parser.add_argument(
        "--murmurmark-bin",
        type=Path,
        default=None,
        help="MurmurMark executable. Default: MURMURMARK_BIN, then murmurmark from PATH, then .build/debug/murmurmark.",
    )
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
    parser.add_argument("--skip-stronger-audio-judge", action="store_true")
    parser.add_argument("--skip-cleanup", action="store_true")
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="Debug only: process a session whose capture was interrupted before Ctrl-C or requested duration.",
    )
    parser.add_argument("--plan-only", action="store_true", help="Write the planned steps without executing them.")
    parser.add_argument(
        "--progress-interval-sec",
        type=int,
        default=60,
        help="Print a heartbeat for long-running steps. Use 0 to disable.",
    )
    parser.add_argument("--max-clips", type=int, default=80)
    parser.add_argument("--max-audio-review-items", type=int, default=160)
    parser.add_argument("--max-stronger-audio-judge-items", type=int, default=80)
    parser.add_argument(
        "--stronger-audio-judge-exhaustive",
        action="store_true",
        help="Decode all four audio sources instead of the bounded two-source pipeline triage.",
    )
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


def final_capture_stop_reason(session: Path) -> str:
    events = session / "events.jsonl"
    if not events.exists():
        return ""
    reason = ""
    try:
        with events.open("r", encoding="utf-8") as file:
            for line in file:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict) and row.get("type") == "capture.stopped":
                    reason = str(row.get("reason") or "")
    except OSError:
        return ""
    return reason


def interrupted_capture_warnings(session: Path) -> list[str]:
    final_reason = final_capture_stop_reason(session)
    if final_reason and final_reason not in INTERRUPTED_CAPTURE_STOP_REASONS:
        return []
    session_json = read_json(session / "session.json")
    if not isinstance(session_json, dict):
        return []
    health = session_json.get("health")
    if not isinstance(health, dict):
        return []
    health_reason = str(health.get("stop_reason") or "")
    reason = health_reason or final_reason
    partial = bool(health.get("partial")) or session_json.get("status") == "partial" or reason in INTERRUPTED_CAPTURE_STOP_REASONS
    if not partial and session_json.get("status") != "completed_with_warnings":
        return []
    warnings = health.get("warnings")
    if not isinstance(warnings, list):
        warnings = []
    matched: list[str] = []
    for warning in warnings:
        text = str(warning)
        lowered = text.lower()
        if any(marker in lowered for marker in INTERRUPTED_CAPTURE_WARNING_MARKERS):
            matched.append(text)
    if partial and not matched:
        matched.append(f"capture ended unexpectedly: {reason or 'unknown'}")
    return matched


def silent_capture_warnings(session: Path) -> list[str]:
    session_json = read_json(session / "session.json")
    if not isinstance(session_json, dict):
        return []
    health = session_json.get("health")
    if not isinstance(health, dict):
        return []
    actual_duration = float(health.get("actual_duration_sec") or 0.0)
    warnings = health.get("warnings")
    if not isinstance(warnings, list):
        return []
    mic_silent = any("mic track appears silent or almost silent" in str(item) for item in warnings)
    remote_silent = any("remote track appears silent or almost silent" in str(item) for item in warnings)
    if actual_duration >= 30 and mic_silent and remote_silent:
        return [str(item) for item in warnings if "track appears silent or almost silent" in str(item)] + [
            "both mic and remote tracks are silent; ASR would produce an empty transcript"
        ]
    return []


def first_audio_paths(session: Path, session_json: dict[str, Any], track: str) -> list[Path]:
    files = ((session_json.get("files") or {}).get(track) or [])
    paths: list[Path] = []
    if isinstance(files, list):
        for item in files:
            if not isinstance(item, dict):
                continue
            raw_path = item.get("path")
            if isinstance(raw_path, str) and raw_path:
                paths.append(session / raw_path)
    return [path for path in paths if path.exists()]


def audio_active_seconds(path: Path, *, active_db: float = SPARSE_CAPTURE_ACTIVE_DB) -> tuple[float, float] | None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    sample_rate = 8000
    bytes_per_sample = 2
    chunk_bytes = sample_rate * bytes_per_sample
    active_sec = 0.0
    duration_sec = 0.0
    threshold = 10 ** (active_db / 20.0)
    process = subprocess.Popen(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-ac",
            "1",
            "-ar",
            str(sample_rate),
            "-f",
            "s16le",
            "-",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdout is not None
    while True:
        chunk = process.stdout.read(chunk_bytes)
        if not chunk:
            break
        sample_count = len(chunk) // bytes_per_sample
        if sample_count <= 0:
            continue
        # s16le by construction above.
        total = 0
        for index in range(0, sample_count * bytes_per_sample, bytes_per_sample):
            value = int.from_bytes(chunk[index : index + bytes_per_sample], "little", signed=True)
            total += value * value
        rms = math.sqrt(total / sample_count) / 32768.0
        seconds = sample_count / sample_rate
        duration_sec += seconds
        if rms > threshold:
            active_sec += seconds
    _, stderr = process.communicate()
    if process.returncode != 0:
        message = stderr.decode("utf-8", errors="replace").strip()
        print(f"[warn] cannot probe audio activity for {path}: {message[-500:]}", flush=True)
        return None
    return active_sec, duration_sec


def sparse_capture_warnings(session: Path) -> list[str]:
    session_json = read_json(session / "session.json")
    if not isinstance(session_json, dict):
        return []
    health = session_json.get("health")
    if not isinstance(health, dict):
        return []
    actual_duration = float(health.get("actual_duration_sec") or 0.0)
    if actual_duration < SPARSE_CAPTURE_MIN_DURATION_SEC:
        return []
    if bool(health.get("partial")) or session_json.get("status") == "partial":
        return []

    track_profiles: dict[str, dict[str, float]] = {}
    for track in ("mic", "remote"):
        active_sec = 0.0
        decoded_sec = 0.0
        for path in first_audio_paths(session, session_json, track):
            stats = audio_active_seconds(path)
            if stats is None:
                return []
            track_active, track_decoded = stats
            active_sec += track_active
            decoded_sec += track_decoded
        track_profiles[track] = {"active_sec": active_sec, "decoded_sec": decoded_sec}

    strongest_active = max(profile["active_sec"] for profile in track_profiles.values())
    active_ratio = strongest_active / actual_duration if actual_duration > 0 else 0.0
    required_active = min(
        SPARSE_CAPTURE_MAX_REQUIRED_ACTIVE_SEC,
        max(SPARSE_CAPTURE_MIN_ACTIVE_SEC, actual_duration * SPARSE_CAPTURE_MIN_ACTIVE_RATIO),
    )
    if strongest_active >= required_active:
        return []
    return [
        (
            "captured audio is too sparse for meeting transcription: "
            f"strongest track has {strongest_active:.1f}s above {SPARSE_CAPTURE_ACTIVE_DB:.0f} dB "
            f"over {actual_duration:.1f}s ({active_ratio * 100:.2f}%); "
            f"minimum expected active audio is {required_active:.1f}s"
        ),
        f"mic active audio: {track_profiles['mic']['active_sec']:.1f}s; remote active audio: {track_profiles['remote']['active_sec']:.1f}s",
    ]


def resolve_murmurmark_bin(explicit: Path | None, repo_root: Path) -> str:
    if explicit is not None:
        return str(explicit.expanduser())
    env_value = os.environ.get("MURMURMARK_BIN")
    if env_value:
        return env_value
    from_path = shutil.which("murmurmark")
    if from_path:
        return from_path
    debug_bin = repo_root / ".build/debug/murmurmark"
    if debug_bin.exists():
        return str(debug_bin)
    return "murmurmark"


def rel(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def shell_path(path: Path, base: Path) -> str:
    return shlex.quote(rel(path, base))


def pipeline_run_state_path(session: Path) -> Path:
    return session / "derived/pipeline-run/pipeline_run_state.json"


def write_pipeline_run_state(
    *,
    session: Path,
    repo_root: Path,
    report_path: Path,
    started_at: str,
    status: str,
    active_step: str | None = None,
    active_step_started_at: str | None = None,
    active_step_elapsed_sec: float | None = None,
    progress: dict[str, Any] | None = None,
    message: str | None = None,
    completed_steps: list[dict[str, Any]] | None = None,
) -> None:
    session_arg = rel(session, repo_root)
    payload: dict[str, Any] = {
        "schema": RUN_STATE_SCHEMA,
        "generator": {"name": "run-session-pipeline", "version": SCRIPT_VERSION},
        "session": str(session),
        "status": status,
        "started_at": started_at,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "report": rel(report_path, session),
        "resume_command": f"murmurmark process {session_arg}",
        "next_command": f"murmurmark next {session_arg}",
        "safe_interrupt": True,
        "safe_interrupt_hint": f"Ctrl-C is safe; rerun `murmurmark process {session_arg}`",
    }
    if active_step:
        payload["active_step"] = active_step
    if active_step_started_at:
        payload["active_step_started_at"] = active_step_started_at
    if active_step_elapsed_sec is not None:
        payload["active_step_elapsed_sec"] = round(active_step_elapsed_sec, 3)
    if progress is not None:
        payload["progress"] = progress
    if message:
        payload["message"] = message
    if completed_steps is not None:
        payload["completed_steps"] = [
            {
                "name": str(item.get("name") or ""),
                "status": str(item.get("status") or ""),
                "duration_sec": item.get("duration_sec"),
            }
            for item in completed_steps
        ]
    write_json(pipeline_run_state_path(session), payload)


def first_next_command(readiness: dict[str, Any] | None) -> str | None:
    if not isinstance(readiness, dict):
        return None
    recommended = readiness.get("recommended_next")
    if isinstance(recommended, str) and recommended.strip():
        return recommended.strip()
    commands = readiness.get("next_commands")
    if isinstance(commands, list):
        for item in commands:
            if isinstance(item, dict):
                command = item.get("command")
                if isinstance(command, str) and command.strip():
                    return command.strip()
    return None


def pipeline_handoff(
    *,
    status: str,
    session: Path,
    report_path: Path,
    repo_root: Path,
    readiness: dict[str, Any] | None,
) -> dict[str, Any]:
    session_arg = shell_path(session, repo_root)
    report_arg = shell_path(report_path, repo_root)
    readiness_md = session / "derived/readiness/session_readiness.md"
    quality_md = session / "derived/synthesis-simple/extractive/quality_verdict.md"

    open_commands = [
        {
            "id": "open_pipeline_run_report",
            "command": f"less {report_arg}",
            "path": rel(report_path, repo_root),
        }
    ]
    if readiness_md.exists():
        open_commands.append(
            {
                "id": "open_readiness",
                "command": f"less {shell_path(readiness_md, repo_root)}",
                "path": rel(readiness_md, repo_root),
            }
        )
    if quality_md.exists():
        open_commands.append(
            {
                "id": "open_quality_verdict",
                "command": f"less {shell_path(quality_md, repo_root)}",
                "path": rel(quality_md, repo_root),
            }
        )

    if status == "planned":
        next_commands = [
            {
                "id": "run_process",
                "command": f"murmurmark process {session_arg}",
                "reason": "execute the planned post-recording pipeline",
            },
            {
                "id": "current_next",
                "command": f"murmurmark next {session_arg}",
                "reason": "inspect the current readiness state before running the plan",
            },
        ]
    elif status == "passed":
        readiness_next = first_next_command(readiness)
        next_commands = []
        if readiness_next:
            next_commands.append(
                {
                    "id": "readiness_next",
                    "command": readiness_next,
                    "reason": "continue from the refreshed session readiness state",
                }
            )
        next_commands.append(
            {
                "id": "refresh_report",
                "command": f"murmurmark report {session_arg}",
                "reason": "refresh and inspect the post-process readiness summary",
            }
        )
    elif status == "interrupted":
        next_commands = [
            {
                "id": "rerun_process",
                "command": f"murmurmark process {session_arg}",
                "reason": "resume the post-recording pipeline after Ctrl-C",
            },
            {
                "id": "open_pipeline_run_report",
                "command": f"less {report_arg}",
                "reason": "inspect the interrupted step and command tails",
            },
        ]
    else:
        next_commands = [
            {
                "id": "open_pipeline_run_report",
                "command": f"less {report_arg}",
                "reason": "inspect the failed pipeline step and command tails",
            },
            {
                "id": "rerun_process",
                "command": f"murmurmark process {session_arg}",
                "reason": "rerun the post-recording pipeline after fixing the failure",
            },
        ]

    return {
        "recommended_next": next_commands[0]["command"],
        "next_commands": next_commands,
        "open_commands": open_commands,
    }


def add_prompt(command: list[str], prompt_file: Path | None) -> list[str]:
    if prompt_file and prompt_file.exists():
        return command + ["--prompt-file", str(prompt_file)]
    return command


def step(
    name: str,
    command: list[str],
    *,
    enabled: bool = True,
    reason: str | None = None,
    warning_returncodes: set[int] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "enabled": enabled,
        "skip_reason": reason if not enabled else None,
        "command": command,
        "warning_returncodes": sorted(warning_returncodes or set()),
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
    live_cache_materialize = [
        py,
        str(repo_root / "scripts/materialize-live-asr-cache.py"),
        str(session),
        "--model",
        str(args.model),
        "--language",
        args.language,
    ]
    live_cache_materialize = add_prompt(live_cache_materialize, prompt)

    audio_judge_exists = args.audio_judge_queue.exists()
    live_report_exists = (session / "derived/live/live_pipeline_report.json").exists()
    stronger_audio_judge = [
        py,
        str(repo_root / "scripts/audit-stronger-audio-judge.py"),
        str(session),
        "--profile",
        "audit_cleanup_v2",
        "--max-items",
        str(args.max_stronger_audio_judge_items),
    ]
    if not args.stronger_audio_judge_exhaustive:
        stronger_audio_judge.append("--quick")
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
        step(
            "materialize_live_asr_cache",
            live_cache_materialize,
            enabled=live_report_exists and not args.skip_transcription and not args.force_asr,
            reason="missing live report/--skip-transcription/--force-asr",
        ),
        step("transcribe_current", current_transcribe, enabled=not args.skip_transcription, reason="--skip-transcription"),
        step(
            "check_asr_chunk_cache",
            [py, str(repo_root / "scripts/check-asr-chunk-cache.py"), str(session), "--require-chunks"],
            enabled=not args.skip_transcription,
            reason="--skip-transcription",
        ),
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
            warning_returncodes={2},
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
            "cleanup_v2",
            [py, str(repo_root / "scripts/apply-audit-cleanup.py"), str(session), "--input-profile", "audit_cleanup_v1", "--output-profile", "audit_cleanup_v2", "--mode", "conservative"],
            enabled=not args.skip_cleanup,
            reason="--skip-cleanup",
            warning_returncodes={2},
        ),
        step("synthesize_v2", [py, str(repo_root / "scripts/synthesize-simple-extractive.py"), str(session), "--transcript-profile", "audit_cleanup_v2"]),
        step(
            "rebuild_audio_review_pack_v2",
            [
                py,
                str(repo_root / "scripts/build-audio-review-pack.py"),
                str(session),
                "--profile",
                "audit_cleanup_v2",
                "--write-clips",
                "--max-items",
                str(args.max_audio_review_items),
            ],
            enabled=not args.skip_audits,
            reason="--skip-audits",
        ),
        step(
            "audit_audio_review_pack_v2",
            [py, str(repo_root / "scripts/audit-audio-review-pack.py"), str(session)],
            enabled=not args.skip_audits,
            reason="--skip-audits",
        ),
        step(
            "audit_stronger_audio_judge",
            stronger_audio_judge,
            enabled=not args.skip_audits and not args.skip_stronger_audio_judge,
            reason="--skip-audits/--skip-stronger-audio-judge",
        ),
        step(
            "plan_remote_leak_segment_repair",
            [py, str(repo_root / "scripts/plan-remote-leak-segment-repair.py"), str(session)],
            enabled=not args.skip_audits,
            reason="--skip-audits",
        ),
        step(
            "remote_forbidden_evidence",
            [py, str(repo_root / "scripts/harden-remote-forbidden-evidence.py"), str(session), "--profile", "auto"],
            enabled=not args.skip_audits,
            reason="--skip-audits",
        ),
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
            warning_returncodes={2},
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
            warning_returncodes={2},
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
        step(
            "live_batch_comparison",
            [py, str(repo_root / "scripts/compare-live-batch.py"), str(session)],
            enabled=live_report_exists,
            reason="missing live pipeline report",
        ),
    ]


def step_cost_hint(item: dict[str, Any]) -> dict[str, str] | None:
    hint = STEP_COST_HINTS.get(str(item.get("name") or ""))
    if not hint:
        return None
    cost = hint["cost"]
    reason = hint["reason"]
    command = item.get("command") if isinstance(item.get("command"), list) else []
    if item.get("name") == "transcribe_current" and "--skip-transcribe" in command:
        cost = "light"
        reason = "reuses cached raw ASR JSON because --reuse-asr-cache was requested"
    return {"cost": cost, "reason": reason}


def transcribe_chunk_progress(session: Path) -> dict[str, Any] | None:
    raw_chunks = session / "derived/transcript-simple/whisper-cpp/raw/chunks"
    reports = sorted(raw_chunks.glob("*/chunk_cache_report.json"))
    if not reports:
        return None
    tracks: list[dict[str, Any]] = []
    total_chunks = 0
    completed_chunks = 0
    reused_chunks = 0
    transcribed_chunks = 0
    total_sec = 0.0
    completed_sec = 0.0
    reused_sec = 0.0
    transcribed_sec = 0.0
    for path in reports:
        report = read_json(path)
        if not isinstance(report, dict):
            continue
        chunks_total = int(report.get("chunks_total") or 0)
        chunks_completed = int(report.get("chunks_completed") or 0)
        chunks_reused = int(report.get("chunks_reused") or 0)
        chunks_transcribed = int(report.get("chunks_transcribed") or 0)
        track_total_sec = float(report.get("total_sec") or 0.0)
        track_completed_sec = float(report.get("completed_hard_sec") or 0.0)
        track_reused_sec = 0.0
        track_transcribed_sec = 0.0
        for chunk in report.get("chunks") or []:
            if not isinstance(chunk, dict):
                continue
            hard_sec = max(
                0.0,
                (float(chunk.get("hard_end_ms") or 0.0) - float(chunk.get("hard_start_ms") or 0.0)) / 1000.0,
            )
            if chunk.get("status") == "reused":
                track_reused_sec += hard_sec
            elif chunk.get("status") == "transcribed":
                track_transcribed_sec += hard_sec
        total_chunks += chunks_total
        completed_chunks += chunks_completed
        reused_chunks += chunks_reused
        transcribed_chunks += chunks_transcribed
        total_sec += track_total_sec
        completed_sec += track_completed_sec
        reused_sec += track_reused_sec
        transcribed_sec += track_transcribed_sec
        tracks.append(
            {
                "track": str(report.get("track") or path.parent.name),
                "status": str(report.get("status") or "unknown"),
                "chunks_completed": chunks_completed,
                "chunks_total": chunks_total,
                "chunks_missing": max(0, chunks_total - chunks_completed),
                "chunks_reused": chunks_reused,
                "chunks_transcribed": chunks_transcribed,
                "completed_sec": track_completed_sec,
                "total_sec": track_total_sec,
                "remaining_sec": max(0.0, round(track_total_sec - track_completed_sec, 3)),
                "reused_sec": round(track_reused_sec, 3),
                "transcribed_sec": round(track_transcribed_sec, 3),
                "report": rel(path, session),
            }
        )
    if not tracks:
        return None
    return {
        "tracks": tracks,
        "chunks_completed": completed_chunks,
        "chunks_total": total_chunks,
        "chunks_missing": max(0, total_chunks - completed_chunks),
        "chunks_reused": reused_chunks,
        "chunks_transcribed": transcribed_chunks,
        "completed_sec": completed_sec,
        "total_sec": total_sec,
        "remaining_sec": max(0.0, round(total_sec - completed_sec, 3)),
        "reused_sec": round(reused_sec, 3),
        "transcribed_sec": round(transcribed_sec, 3),
        "completed_ratio": round(completed_sec / total_sec, 6) if total_sec > 0 else None,
    }


def estimate_remaining_runtime(progress: dict[str, Any] | None, elapsed_sec: float) -> dict[str, Any] | None:
    if not isinstance(progress, dict):
        return None
    completed_sec = float(progress.get("completed_sec") or 0.0)
    remaining_sec = float(progress.get("remaining_sec") or 0.0)
    if completed_sec <= 0 or remaining_sec <= 0 or elapsed_sec <= 0:
        return {
            "remaining_audio_sec": round(remaining_sec, 3),
            "estimated_wall_sec": None,
            "basis": "insufficient_progress",
        }
    seconds_per_audio_sec = elapsed_sec / completed_sec
    return {
        "remaining_audio_sec": round(remaining_sec, 3),
        "estimated_wall_sec": round(remaining_sec * seconds_per_audio_sec, 3),
        "seconds_per_audio_sec": round(seconds_per_audio_sec, 6),
        "basis": "current_step_completed_audio_ratio",
    }


def checkpoint_progress_for_step(
    *,
    step_name: str,
    session: Path,
    report_path: Path,
) -> dict[str, Any]:
    outputs = [
        item
        for item in expected_output_specs(session, report_path)
        if item.get("produced_by") == step_name
    ]
    existing: list[str] = []
    missing: list[str] = []
    for item in outputs:
        raw_path = str(item.get("path") or "")
        if not raw_path:
            continue
        path = Path(raw_path)
        absolute = path if path.is_absolute() else session / path
        if absolute.exists():
            existing.append(raw_path)
        else:
            missing.append(raw_path)
    return {
        "total": len(outputs),
        "existing": len(existing),
        "missing": len(missing),
        "existing_paths": existing,
        "missing_paths": missing,
        "asr_chunks": transcribe_chunk_progress(session)
        if step_name in {"transcribe_current", "transcribe_shadow_v2"}
        else None,
    }


def expected_output_specs(session: Path, report_path: Path) -> list[dict[str, str]]:
    return [
        {
            "id": "mic_for_asr",
            "path": rel(session / "derived/preprocess/audio/mic_for_asr.wav", session),
            "produced_by": "echo_preprocess",
            "purpose": "ASR-ready local speaker audio",
        },
        {
            "id": "transcript",
            "path": rel(session / "derived/transcript-simple/whisper-cpp/resolved/transcript.md", session),
            "produced_by": "transcribe_current",
            "purpose": "baseline readable transcript",
        },
        {
            "id": "asr_chunk_rebuild_check",
            "path": rel(session / "derived/transcript-simple/whisper-cpp/raw/chunk_rebuild_check.json", session),
            "produced_by": "check_asr_chunk_cache",
            "purpose": "raw ASR chunk rebuild parity check",
        },
        {
            "id": "best_notes",
            "path": rel(session / "derived/synthesis-simple/extractive/notes.md", session),
            "produced_by": "synthesize_auto",
            "purpose": "extractive notes from the selected safe profile",
        },
        {
            "id": "quality_verdict",
            "path": rel(session / "derived/synthesis-simple/extractive/quality_verdict.md", session),
            "produced_by": "synthesize_auto",
            "purpose": "human-readable quality verdict",
        },
        {
            "id": "readiness",
            "path": rel(session / "derived/readiness/session_readiness.md", session),
            "produced_by": "session_readiness",
            "purpose": "final readiness gate and next commands",
        },
        {
            "id": "pipeline_report",
            "path": rel(report_path, session),
            "produced_by": "run-session-pipeline",
            "purpose": "machine-readable run/plan report",
        },
        {
            "id": "outcome",
            "path": rel(session / "derived/outcome/outcome.json", session),
            "produced_by": "run-session-pipeline",
            "purpose": "machine-readable final user-facing outcome",
        },
        {
            "id": "next_command",
            "path": rel(session / "derived/outcome/next_command.txt", session),
            "produced_by": "run-session-pipeline",
            "purpose": "single next safe command",
        },
    ]


def build_plan_metadata(
    steps: list[dict[str, Any]],
    session: Path,
    report_path: Path,
    repo_root: Path,
    *,
    plan_only: bool,
) -> dict[str, Any]:
    enabled_names = {str(item.get("name") or "") for item in steps if item.get("enabled")}
    session_arg = rel(session, repo_root)
    heavy_steps: list[dict[str, str]] = []
    for item in steps:
        if not item.get("enabled"):
            continue
        hint = step_cost_hint(item)
        if not hint or hint["cost"] == "light":
            continue
        heavy_steps.append(
            {
                "name": str(item.get("name") or ""),
                "cost": hint["cost"],
                "reason": hint["reason"],
            }
        )

    expected_outputs = []
    for output in expected_output_specs(session, report_path):
        produced_by = output["produced_by"]
        if produced_by != "run-session-pipeline" and produced_by not in enabled_names:
            continue
        expected_outputs.append(output)

    enabled = [item for item in steps if item["enabled"]]
    skipped = [item for item in steps if not item["enabled"]]
    return {
        "mode": "plan_only" if plan_only else "run",
        "session": session_arg,
        "enabled_steps": len(enabled),
        "skipped_steps": len(skipped),
        "heavy_steps": heavy_steps,
        "expected_outputs": expected_outputs,
        "run_command": f"murmurmark process {session_arg}",
        "current_next": f"murmurmark next {session_arg}",
    }


def read_tail(path: Path, limit: int = 4000) -> str:
    try:
        data = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return data[-limit:]


def terminate_process_group(process: subprocess.Popen[str], *, timeout_sec: float = 5.0) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except OSError:
        process.terminate()
    try:
        process.wait(timeout=timeout_sec)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except OSError:
        process.kill()
    process.wait(timeout=timeout_sec)


def run_step(
    item: dict[str, Any],
    repo_root: Path,
    plan_only: bool,
    *,
    progress_interval_sec: int,
    session: Path,
    report_path: Path,
    pipeline_started_at: str,
) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc).isoformat()
    started = time.monotonic()
    result = {
        **item,
        "status": "planned" if plan_only else "pending",
        "started_at": started_at,
        "cost_hint": step_cost_hint(item),
    }
    if not item["enabled"]:
        result["status"] = "skipped"
        result["finished_at"] = datetime.now(timezone.utc).isoformat()
        result["duration_sec"] = 0.0
        return result
    if plan_only:
        result["finished_at"] = datetime.now(timezone.utc).isoformat()
        result["duration_sec"] = 0.0
        return result
    with tempfile.TemporaryDirectory(prefix="murmurmark-pipeline-") as temp_dir:
        stdout_path = Path(temp_dir) / "stdout.log"
        stderr_path = Path(temp_dir) / "stderr.log"
        with stdout_path.open("w", encoding="utf-8") as stdout_file, stderr_path.open("w", encoding="utf-8") as stderr_file:
            step_name = str(item.get("name") or "unknown")
            write_pipeline_run_state(
                session=session,
                repo_root=repo_root,
                report_path=report_path,
                started_at=pipeline_started_at,
                status="running",
                active_step=step_name,
                active_step_started_at=started_at,
                active_step_elapsed_sec=0.0,
                progress=checkpoint_progress_for_step(
                    step_name=step_name,
                    session=session,
                    report_path=report_path,
                ),
                message="step_started",
            )
            process = subprocess.Popen(
                item["command"],
                cwd=repo_root,
                text=True,
                stdin=subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
                start_new_session=True,
            )
            next_progress_at = time.monotonic() + max(1, progress_interval_sec)
            try:
                while True:
                    returncode = process.poll()
                    if returncode is not None:
                        break
                    now = time.monotonic()
                    if progress_interval_sec > 0 and now >= next_progress_at:
                        elapsed = now - started
                        hint = step_cost_hint(item) or {}
                        progress = checkpoint_progress_for_step(
                            step_name=step_name,
                            session=session,
                            report_path=report_path,
                        )
                        write_pipeline_run_state(
                            session=session,
                            repo_root=repo_root,
                            report_path=report_path,
                            started_at=pipeline_started_at,
                            status="running",
                            active_step=step_name,
                            active_step_started_at=started_at,
                            active_step_elapsed_sec=elapsed,
                            progress=progress,
                            message="step_heartbeat",
                        )
                        checkpoint = ""
                        if progress["total"]:
                            checkpoint = (
                                f"; checkpoints {progress['existing']}/{progress['total']} present"
                            )
                        chunk_text = ""
                        chunk_progress = progress.get("asr_chunks")
                        if isinstance(chunk_progress, dict):
                            chunks_completed = int(chunk_progress.get("chunks_completed") or 0)
                            chunks_total = int(chunk_progress.get("chunks_total") or 0)
                            completed_sec = float(chunk_progress.get("completed_sec") or 0.0)
                            total_sec = float(chunk_progress.get("total_sec") or 0.0)
                            remaining_sec = float(chunk_progress.get("remaining_sec") or 0.0)
                            reused = int(chunk_progress.get("chunks_reused") or 0)
                            transcribed = int(chunk_progress.get("chunks_transcribed") or 0)
                            if chunks_total:
                                estimate = estimate_remaining_runtime(chunk_progress, elapsed)
                                eta_text = ""
                                if isinstance(estimate, dict) and estimate.get("estimated_wall_sec") is not None:
                                    eta_text = f", eta~{format_duration(float(estimate['estimated_wall_sec']))}"
                                chunk_text = (
                                    f"; ASR chunks {chunks_completed}/{chunks_total}"
                                    f" ({format_duration(completed_sec)}/{format_duration(total_sec)}),"
                                    f" remaining={format_duration(remaining_sec)},"
                                    f" reused={reused}, transcribed={transcribed}{eta_text}"
                                )
                        reason = str(hint.get("reason") or "working")
                        print(
                            f"[run] {step_name} still running ({format_duration(elapsed)})"
                            f"; {reason}{checkpoint}{chunk_text}",
                            flush=True,
                        )
                        print(
                            f"[run] resume hint: Ctrl-C is safe; rerun `murmurmark process {rel(session, repo_root)}`",
                            flush=True,
                        )
                        next_progress_at = now + progress_interval_sec
                    time.sleep(0.5)
            except KeyboardInterrupt:
                terminate_process_group(process)
                stdout_tail = read_tail(stdout_path)
                stderr_tail = read_tail(stderr_path)
                elapsed = time.monotonic() - started
                write_pipeline_run_state(
                    session=session,
                    repo_root=repo_root,
                    report_path=report_path,
                    started_at=pipeline_started_at,
                    status="interrupted",
                    active_step=str(item.get("name") or "unknown"),
                    active_step_started_at=started_at,
                    active_step_elapsed_sec=elapsed,
                    progress=checkpoint_progress_for_step(
                        step_name=str(item.get("name") or "unknown"),
                        session=session,
                        report_path=report_path,
                    ),
                    message="interrupted_by_user",
                )
                result.update(
                    {
                        "status": "interrupted",
                        "returncode": process.returncode,
                        "finished_at": datetime.now(timezone.utc).isoformat(),
                        "duration_sec": round(time.monotonic() - started, 3),
                        "stdout_tail": stdout_tail,
                        "stderr_tail": stderr_tail,
                        "message": "interrupted by Ctrl-C; rerun murmurmark process for the same session",
                    }
                )
                return result
        returncode = process.returncode
        stdout_tail = read_tail(stdout_path)
        stderr_tail = read_tail(stderr_path)
    warning_returncodes = {int(value) for value in item.get("warning_returncodes", [])}
    if returncode == 0:
        status = "passed"
    elif returncode in warning_returncodes:
        status = "passed_with_warnings"
    else:
        status = "failed"
    result.update(
        {
            "status": status,
            "returncode": returncode,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "duration_sec": round(time.monotonic() - started, 3),
            "stdout_tail": stdout_tail,
            "stderr_tail": stderr_tail,
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
    if status in {"failed", "interrupted"}:
        for key in ("stderr_tail", "stdout_tail"):
            tail = str(result.get(key) or "").strip()
            if tail:
                print(f"{key}:\n{tail}", flush=True)


def print_pipeline_plan(steps: list[dict[str, Any]], session: Path, report_path: Path, repo_root: Path, plan: dict[str, Any]) -> None:
    enabled = [item for item in steps if item["enabled"]]
    skipped = [item for item in steps if not item["enabled"]]
    session_arg = rel(session, repo_root)
    print("pipeline_plan:", flush=True)
    print(f"  session: {session_arg}", flush=True)
    print("  mode: plan_only", flush=True)
    print(f"  enabled_steps: {len(enabled)}", flush=True)
    print(f"  skipped_steps: {len(skipped)}", flush=True)
    print("  steps:", flush=True)
    for item in steps:
        if item["enabled"]:
            print(f"    run: {item['name']}", flush=True)
        else:
            reason = item.get("skip_reason") or "disabled"
            print(f"    skip: {item['name']} ({reason})", flush=True)
    heavy_steps = plan.get("heavy_steps") if isinstance(plan.get("heavy_steps"), list) else []
    if heavy_steps:
        print("  heavy_steps:", flush=True)
        for item in heavy_steps:
            name = item.get("name")
            cost = item.get("cost")
            reason = item.get("reason")
            print(f"    {name}: {cost} - {reason}", flush=True)
    expected_outputs = plan.get("expected_outputs") if isinstance(plan.get("expected_outputs"), list) else []
    if expected_outputs:
        print("  expected_outputs:", flush=True)
        for item in expected_outputs:
            output_id = item.get("id")
            output_path = item.get("path")
            produced_by = item.get("produced_by")
            print(f"    {output_id}: {output_path} ({produced_by})", flush=True)
    print(f"  report: {rel(report_path, repo_root)}", flush=True)
    print(f"  run_command: murmurmark process {session_arg}", flush=True)
    print(f"  current_next: murmurmark next {session_arg}", flush=True)


def print_pipeline_summary(report: dict[str, Any], report_path: Path, repo_root: Path) -> None:
    status = str(report.get("status") or "unknown")
    session = Path(str(report.get("session") or ""))
    session_arg = rel(session, repo_root) if str(session) else "SESSION"
    outputs = report.get("outputs") if isinstance(report.get("outputs"), dict) else {}
    selected_profile = outputs.get("selected_transcript_profile")
    verdict = outputs.get("verdict")
    next_commands = command_list(report.get("next_commands"))
    recommended_next = str(report.get("recommended_next") or "").strip()
    if not recommended_next:
        recommended_next = next_commands[0] if next_commands else f"less {rel(report_path, repo_root)}"

    print("", flush=True)
    print("pipeline_run:", flush=True)
    print(f"  report: {rel(report_path, repo_root)}", flush=True)
    print(f"  status: {status}", flush=True)
    if selected_profile:
        print(f"  selected_profile: {selected_profile}", flush=True)
    if verdict:
        print(f"  verdict: {verdict}", flush=True)
    if status == "planned":
        print(f"  run_command: murmurmark process {session_arg}", flush=True)
        print(f"  current_next: murmurmark next {session_arg}", flush=True)
    print(f"  recommended_next: {recommended_next}", flush=True)
    if next_commands:
        print("  next:", flush=True)
        for command in next_commands:
            print(f"    {command}", flush=True)


def command_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    commands: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        command = str(item.get("command") or "").strip()
        if command:
            commands.append(command)
    return commands


def write_outcome_artifacts(session: Path, report_path: Path, repo_root: Path) -> None:
    script = repo_root / "scripts/evaluate-outcome.py"
    if not script.exists():
        return
    command = [
        sys.executable,
        str(script),
        str(session),
        "--pipeline-report",
        str(report_path),
    ]
    result = subprocess.run(
        command,
        cwd=repo_root,
        text=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip()
        print("[warn] evaluate_outcome failed", flush=True)
        if tail:
            print(tail[-2000:], flush=True)


def write_interrupted_capture_report(
    *,
    args: argparse.Namespace,
    session: Path,
    report_path: Path,
    repo_root: Path,
    plan_metadata: dict[str, Any],
    warnings: list[str],
    started_at: str,
    blocker: str = "interrupted_capture",
) -> dict[str, Any]:
    session_arg = shell_path(session, repo_root)
    report_arg = shell_path(report_path, repo_root)
    next_commands = [
        {
            "id": "inspect_partial_session",
            "command": f"murmurmark inspect {session_arg}",
            "reason": "inspect the partial recording and capture warning",
        },
        {
            "id": "record_again",
            "command": "murmurmark record --target-bundle system",
            "reason": "start a fresh recording for a live meeting",
        },
        {
            "id": "debug_process_partial",
            "command": f"murmurmark process {session_arg} --allow-partial",
            "reason": "debug only: force processing of the partial recording",
        },
    ]
    report = {
        "schema": SCHEMA,
        "generator": {"name": "run-session-pipeline", "version": SCRIPT_VERSION},
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "session": str(session),
        "status": "blocked",
        "blocker": blocker,
        "inputs": {
            "model": str(args.model),
            "language": args.language,
            "prompt_file": str(args.prompt_file) if args.prompt_file and args.prompt_file.exists() else None,
            "audio_judge_queue": str(args.audio_judge_queue) if args.audio_judge_queue.exists() else None,
            "progress_interval_sec": args.progress_interval_sec,
            "allow_partial": args.allow_partial,
        },
        "outputs": {
            "quality_verdict": None,
            "session_readiness": rel(session / "derived/readiness/session_readiness.json", session)
            if (session / "derived/readiness/session_readiness.json").exists()
            else None,
            "remote_leak_segment_repair_plan": None,
            "selected_transcript_profile": None,
            "synthesis_selected_transcript_profile": None,
            "readiness_selected_profile": None,
            "verdict": None,
            "synthesis_verdict": None,
            "readiness_verdict": None,
            "use_gate": "pipeline_incomplete",
        },
        "warnings": warnings,
        "recommended_next": next_commands[0]["command"],
        "next_commands": next_commands,
        "open_commands": [
            {
                "id": "open_pipeline_run_report",
                "command": f"less {report_arg}",
                "path": rel(report_path, repo_root),
            }
        ],
        "plan": plan_metadata,
        "steps": [],
    }
    write_json(report_path, report)
    write_outcome_artifacts(session, report_path, repo_root)
    return report


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    args.murmurmark_bin = resolve_murmurmark_bin(args.murmurmark_bin, repo_root)
    session = args.session.expanduser()
    if args.report:
        report_path = args.report.expanduser()
    elif args.plan_only:
        report_path = session / "derived/pipeline-run/pipeline_plan_report.json"
    else:
        report_path = session / "derived/pipeline-run/pipeline_run_report.json"
    steps = build_steps(args, repo_root, session)
    plan_metadata = build_plan_metadata(steps, session, report_path, repo_root, plan_only=args.plan_only)
    results: list[dict[str, Any]] = []
    started_at = datetime.now(timezone.utc).isoformat()
    final_status = "passed"
    write_pipeline_run_state(
        session=session,
        repo_root=repo_root,
        report_path=report_path,
        started_at=started_at,
        status="running",
        message="pipeline_started",
        completed_steps=[],
    )
    interrupted_warnings = interrupted_capture_warnings(session)
    silent_warnings = silent_capture_warnings(session)
    sparse_warnings = sparse_capture_warnings(session)
    if interrupted_warnings and not args.allow_partial and not args.plan_only:
        report = write_interrupted_capture_report(
            args=args,
            session=session,
            report_path=report_path,
            repo_root=repo_root,
            plan_metadata=plan_metadata,
            warnings=interrupted_warnings,
            started_at=started_at,
            blocker="interrupted_capture",
        )
        print_pipeline_summary(report, report_path, repo_root)
        print("  blocker: interrupted_capture", flush=True)
        print("  warning: capture stopped before Ctrl-C or requested duration", flush=True)
        print("  hint: inspect the partial session or re-record; use --allow-partial only for debugging", flush=True)
        write_pipeline_run_state(
            session=session,
            repo_root=repo_root,
            report_path=report_path,
            started_at=started_at,
            status="blocked",
            message="interrupted_capture",
            completed_steps=results,
        )
        return 2
    if silent_warnings and not args.allow_partial and not args.plan_only:
        report = write_interrupted_capture_report(
            args=args,
            session=session,
            report_path=report_path,
            repo_root=repo_root,
            plan_metadata=plan_metadata,
            warnings=silent_warnings,
            started_at=started_at,
            blocker="silent_capture",
        )
        if report.get("next_commands"):
            report["next_commands"][0] = {
                "id": "inspect_silent_session",
                "command": f"murmurmark inspect {shell_path(session, repo_root)}",
                "reason": "inspect the silent recording and capture warnings",
            }
        report["recommended_next"] = report["next_commands"][0]["command"]
        write_json(report_path, report)
        write_outcome_artifacts(session, report_path, repo_root)
        print_pipeline_summary(report, report_path, repo_root)
        print("  blocker: silent_capture", flush=True)
        print("  warning: both mic and remote tracks are silent; transcription would be empty", flush=True)
        print("  hint: inspect the session, run live capture check, then re-record", flush=True)
        write_pipeline_run_state(
            session=session,
            repo_root=repo_root,
            report_path=report_path,
            started_at=started_at,
            status="blocked",
            message="silent_capture",
            completed_steps=results,
        )
        return 2
    if sparse_warnings and not args.allow_partial and not args.plan_only:
        report = write_interrupted_capture_report(
            args=args,
            session=session,
            report_path=report_path,
            repo_root=repo_root,
            plan_metadata=plan_metadata,
            warnings=sparse_warnings,
            started_at=started_at,
            blocker="sparse_capture",
        )
        if report.get("next_commands"):
            report["next_commands"][0] = {
                "id": "inspect_sparse_session",
                "command": f"murmurmark inspect {shell_path(session, repo_root)}",
                "reason": "inspect sparse recording health and capture warnings",
            }
        report["recommended_next"] = report["next_commands"][0]["command"]
        write_json(report_path, report)
        write_outcome_artifacts(session, report_path, repo_root)
        print_pipeline_summary(report, report_path, repo_root)
        print("  blocker: sparse_capture", flush=True)
        print("  warning: captured audio is too sparse for meeting transcription", flush=True)
        print("  hint: inspect the session, run doctor --strict, then re-record", flush=True)
        write_pipeline_run_state(
            session=session,
            repo_root=repo_root,
            report_path=report_path,
            started_at=started_at,
            status="blocked",
            message="sparse_capture",
            completed_steps=results,
        )
        return 2

    if args.plan_only:
        print_pipeline_plan(steps, session, report_path, repo_root, plan_metadata)

    for item in steps:
        if not args.plan_only:
            print_step_start(item, args.plan_only)
        result = run_step(
            item,
            repo_root,
            args.plan_only,
            progress_interval_sec=args.progress_interval_sec,
            session=session,
            report_path=report_path,
            pipeline_started_at=started_at,
        )
        results.append(result)
        write_pipeline_run_state(
            session=session,
            repo_root=repo_root,
            report_path=report_path,
            started_at=started_at,
            status="running" if result["status"] not in {"failed", "interrupted"} else str(result["status"]),
            active_step=str(result.get("name") or ""),
            active_step_elapsed_sec=float(result.get("duration_sec") or 0.0),
            progress=checkpoint_progress_for_step(
                step_name=str(result.get("name") or ""),
                session=session,
                report_path=report_path,
            ),
            message=f"step_{result['status']}",
            completed_steps=results,
        )
        if not args.plan_only:
            print_step_result(result)
        if result["status"] == "failed":
            final_status = "failed"
            break
        if result["status"] == "interrupted":
            final_status = "interrupted"
            break

    quality_path = session / "derived/synthesis-simple/extractive/quality_verdict.json"
    readiness_path = session / "derived/readiness/session_readiness.json"
    remote_leak_plan_path = session / "derived/transcript-simple/whisper-cpp/remote-leak-repair/remote_leak_segment_repair_plan.json"
    live_comparison_path = session / "derived/live/live_batch_comparison.json"
    live_asr_cache_path = session / "derived/live/live_asr_cache_report.json"
    asr_chunk_check_path = session / "derived/transcript-simple/whisper-cpp/raw/chunk_rebuild_check.json"
    quality = read_json(quality_path)
    readiness = read_json(readiness_path)
    synthesis_profile = quality.get("selected_transcript_profile") if isinstance(quality, dict) else None
    readiness_profile = readiness.get("selected_profile") if isinstance(readiness, dict) else None
    synthesis_verdict = quality.get("verdict") if isinstance(quality, dict) else None
    readiness_verdict = readiness.get("verdict") if isinstance(readiness, dict) else None
    selected_profile = readiness_profile or synthesis_profile
    selected_verdict = readiness_verdict or synthesis_verdict
    status = "planned" if args.plan_only else final_status
    handoff = pipeline_handoff(
        status=status,
        session=session,
        report_path=report_path,
        repo_root=repo_root,
        readiness=readiness,
    )
    report = {
        "schema": SCHEMA,
        "generator": {"name": "run-session-pipeline", "version": SCRIPT_VERSION},
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "session": str(session),
        "status": status,
        "inputs": {
            "model": str(args.model),
            "language": args.language,
            "prompt_file": str(args.prompt_file) if args.prompt_file and args.prompt_file.exists() else None,
            "audio_judge_queue": str(args.audio_judge_queue) if args.audio_judge_queue.exists() else None,
            "progress_interval_sec": args.progress_interval_sec,
        },
        "outputs": {
            "quality_verdict": rel(quality_path, session) if quality_path.exists() else None,
            "session_readiness": rel(readiness_path, session) if readiness_path.exists() else None,
            "remote_leak_segment_repair_plan": rel(remote_leak_plan_path, session) if remote_leak_plan_path.exists() else None,
            "asr_chunk_rebuild_check": rel(asr_chunk_check_path, session) if asr_chunk_check_path.exists() else None,
            "live_asr_cache_report": rel(live_asr_cache_path, session) if live_asr_cache_path.exists() else None,
            "live_batch_comparison": rel(live_comparison_path, session) if live_comparison_path.exists() else None,
            "selected_transcript_profile": selected_profile,
            "synthesis_selected_transcript_profile": synthesis_profile,
            "readiness_selected_profile": readiness_profile,
            "verdict": selected_verdict,
            "synthesis_verdict": synthesis_verdict,
            "readiness_verdict": readiness_verdict,
            "use_gate": readiness.get("use_gate") if isinstance(readiness, dict) else None,
        },
        "recommended_next": handoff["recommended_next"],
        "next_commands": handoff["next_commands"],
        "open_commands": handoff["open_commands"],
        "progress": {
            "asr_chunks": transcribe_chunk_progress(session),
            "asr_remaining_estimate": estimate_remaining_runtime(
                transcribe_chunk_progress(session),
                sum(float(item.get("duration_sec") or 0.0) for item in results if item.get("name") == "transcribe_current"),
            ),
        },
        "plan": plan_metadata,
        "steps": results,
    }
    write_json(report_path, report)
    write_pipeline_run_state(
        session=session,
        repo_root=repo_root,
        report_path=report_path,
        started_at=started_at,
        status=status,
        progress=report["progress"],
        message="pipeline_finished",
        completed_steps=results,
    )
    if not args.plan_only:
        write_outcome_artifacts(session, report_path, repo_root)
    print_pipeline_summary(report, report_path, repo_root)
    return 0 if report["status"] in {"passed", "planned"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
