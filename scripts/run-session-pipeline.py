#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
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


SCRIPT_VERSION = "0.2.1"
SCHEMA = "murmurmark.session_pipeline_run/v1"
RUN_STATE_SCHEMA = "murmurmark.pipeline_run_state/v1"
HANDOFF_SCHEMA = "murmurmark.authoritative_handoff/v1"
HANDOFF_RUN_SCHEMA = "murmurmark.authoritative_handoff_run/v1"
HANDOFF_PHASE = "authoritative_handoff"
DEFERRED_PHASE = "deferred_enrichment"
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
    "echo_suppression_policy": {
        "cost": "light",
        "reason": "validates the frozen Echo Guard promotion policy and fails open to local_fir",
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
        "--asr-threads",
        type=int,
        default=6,
        help="whisper.cpp compute threads per ASR process. Default: 6.",
    )
    parser.add_argument(
        "--asr-track-workers",
        type=int,
        choices=(1, 2),
        default=2,
        help="Bounded independent mic/remote whisper.cpp workers. Default: 2.",
    )
    parser.add_argument(
        "--micro-asr-workers",
        type=int,
        choices=(1, 2, 4),
        default=4,
        help="Bounded independent micro-ASR source/window workers. Default: 4.",
    )
    parser.add_argument(
        "--phase",
        choices=("handoff", "deferred", "full"),
        default="handoff",
        help="Run the authoritative handoff, deferred enrichment, or both. Default: handoff.",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Compatibility alias for --phase full.",
    )
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
    parser.add_argument(
        "--deferred-step-timeout-sec",
        type=int,
        default=3600,
        help="Terminate one deferred enrichment step after this many seconds. Use 0 to disable.",
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


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as file:
            file.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


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
    phase: str,
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
        "phase": phase,
        "started_at": started_at,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "report": rel(report_path, session),
        "resume_command": (
            f"murmurmark enrich {session_arg}"
            if phase == DEFERRED_PHASE
            else f"murmurmark process {session_arg}"
        ),
        "next_command": f"murmurmark next {session_arg}",
        "safe_interrupt": True,
        "safe_interrupt_hint": (
            f"Ctrl-C is safe; rerun `murmurmark enrich {session_arg}`"
            if phase == DEFERRED_PHASE
            else f"Ctrl-C is safe; rerun `murmurmark process {session_arg}`"
        ),
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
    phase: str = HANDOFF_PHASE,
) -> dict[str, Any]:
    return {
        "name": name,
        "enabled": enabled,
        "skip_reason": reason if not enabled else None,
        "command": command,
        "warning_returncodes": sorted(warning_returncodes or set()),
        "phase": phase,
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
        "--track-workers",
        str(args.asr_track_workers),
        "--threads",
        str(args.asr_threads),
        "--micro-asr-workers",
        str(args.micro_asr_workers),
    ]
    transcribe_base = add_prompt(transcribe_base, prompt)
    # One invocation writes both the baseline and shadow_v2 outputs. Running the
    # same script again with --skip-transcribe repeated current timeline repair
    # and made the authoritative path materially slower without changing output.
    current_transcribe = list(transcribe_base) + ["--repair-profile", "shadow_v2"]
    if args.force_asr:
        current_transcribe.append("--force")
    if args.reuse_asr_cache:
        current_transcribe += ["--skip-export", "--skip-transcribe"]

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
            "echo_suppression_policy",
            [
                py,
                str(repo_root / "scripts/echo-suppression-promotion-v1.py"),
                "apply-policy",
                str(session),
            ],
            enabled=not args.skip_preprocess,
            reason="--skip-preprocess",
        ),
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
        step(
            "audit_local_recall",
            [
                py,
                str(repo_root / "scripts/audit-local-recall.py"),
                str(session),
                "--profile",
                "shadow_v2",
                "--dialogue-profile",
                "shadow_v2",
            ],
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
            "session_operational_readiness_for_audio_review",
            [
                py,
                str(repo_root / "scripts/report-operational-readiness.py"),
                "--session-quality",
                str(session / "derived/readiness/session-quality/session_quality_report.json"),
                "--out-dir",
                str(session / "derived/readiness/operational-readiness"),
            ],
            enabled=not args.skip_audits,
            reason="--skip-audits",
            phase=DEFERRED_PHASE,
        ),
        step(
            "review_plan_for_audio_review",
            [
                py,
                str(repo_root / "scripts/build-review-plan.py"),
                "--operational-readiness",
                str(session / "derived/readiness/operational-readiness/operational_readiness_report.json"),
                "--out-dir",
                str(session / "derived/readiness/review-plan"),
            ],
            enabled=not args.skip_audits,
            reason="--skip-audits",
            phase=DEFERRED_PHASE,
        ),
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
            phase=DEFERRED_PHASE,
        ),
        step(
            "audit_audio_review_pack_v2",
            [py, str(repo_root / "scripts/audit-audio-review-pack.py"), str(session)],
            enabled=not args.skip_audits,
            reason="--skip-audits",
            phase=DEFERRED_PHASE,
        ),
        step(
            "audit_stronger_audio_judge",
            stronger_audio_judge,
            enabled=not args.skip_audits and not args.skip_stronger_audio_judge,
            reason="--skip-audits/--skip-stronger-audio-judge",
            phase=DEFERRED_PHASE,
        ),
        step(
            "plan_remote_leak_segment_repair",
            [py, str(repo_root / "scripts/plan-remote-leak-segment-repair.py"), str(session)],
            enabled=not args.skip_audits,
            reason="--skip-audits",
            phase=DEFERRED_PHASE,
        ),
        step(
            "remote_forbidden_evidence",
            [py, str(repo_root / "scripts/harden-remote-forbidden-evidence.py"), str(session), "--profile", "auto"],
            enabled=not args.skip_audits,
            reason="--skip-audits",
            phase=DEFERRED_PHASE,
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
            phase=DEFERRED_PHASE,
        ),
        step(
            "synthesize_v3",
            [py, str(repo_root / "scripts/synthesize-simple-extractive.py"), str(session), "--transcript-profile", "audit_cleanup_v3"],
            enabled=audio_judge_exists and not args.skip_cleanup,
            reason="missing audio judge queue/--skip-cleanup",
            phase=DEFERRED_PHASE,
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
            phase=DEFERRED_PHASE,
        ),
        step(
            "synthesize_v4",
            [py, str(repo_root / "scripts/synthesize-simple-extractive.py"), str(session), "--transcript-profile", "audit_cleanup_v4"],
            enabled=audio_judge_exists and not args.skip_cleanup,
            reason="missing audio judge queue/--skip-cleanup",
            phase=DEFERRED_PHASE,
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
            enabled=live_report_exists and not args.skip_audits,
            reason="missing live pipeline report/--skip-audits",
            phase=DEFERRED_PHASE,
        ),
        step(
            "synthesize_authoritative_final",
            [
                py,
                str(repo_root / "scripts/synthesize-simple-extractive.py"),
                str(session),
                "--transcript-profile",
                "authoritative",
            ],
            phase=DEFERRED_PHASE,
        ),
        step(
            "audit_transcript_order_authoritative_final",
            [
                py,
                str(repo_root / "scripts/audit-transcript-order.py"),
                str(session),
                "--profile",
                "authoritative",
            ],
            enabled=not args.skip_audits,
            reason="--skip-audits",
            phase=DEFERRED_PHASE,
        ),
        step(
            "session_readiness_authoritative_final",
            [
                py,
                str(repo_root / "scripts/report-session-quality.py"),
                str(session),
                "--out-dir",
                str(session / "derived/readiness/session-quality"),
                "--write-session-readiness",
                "--preserve-authoritative-profile",
            ],
            phase=DEFERRED_PHASE,
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


def authoritative_handoff_path(session: Path) -> Path:
    return session / "derived/pipeline-run/authoritative_handoff.json"


def authoritative_handoff_runs_path(session: Path) -> Path:
    return session / "derived/pipeline-run/authoritative_handoff_runs.jsonl"


def append_authoritative_handoff_run(
    *,
    session: Path,
    mode: str,
    started_at: str,
    elapsed_sec: float,
    checkpoint: dict[str, Any],
    args: argparse.Namespace,
) -> None:
    readiness = read_json(session / "derived/readiness/session_readiness.json") or {}
    provenance = checkpoint.get("asr_provenance")
    if not isinstance(provenance, dict):
        provenance = asr_provenance(session)
    row = {
        "schema": HANDOFF_RUN_SCHEMA,
        "generator": {"name": "run-session-pipeline", "version": SCRIPT_VERSION},
        "session": str(session),
        "mode": mode,
        "status": checkpoint.get("status"),
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_sec": round(elapsed_sec, 3),
        "selected_transcript_profile": checkpoint.get("selected_transcript_profile"),
        "verdict": checkpoint.get("verdict"),
        "transcript_fingerprint": checkpoint.get("transcript_fingerprint"),
        "asr_provenance": provenance,
        "runtime": {
            "asr_track_workers": args.asr_track_workers,
            "asr_threads": args.asr_threads,
            "micro_asr_workers": args.micro_asr_workers,
            "force_asr": bool(args.force_asr),
            "reuse_asr_cache": bool(args.reuse_asr_cache),
        },
        "quality_metrics": readiness.get("metrics") if isinstance(readiness.get("metrics"), dict) else {},
    }
    path = authoritative_handoff_runs_path(session)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(row, ensure_ascii=False) + "\n")
        file.flush()
        os.fsync(file.fileno())


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_fingerprint(path: Path, session: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "path": rel(path, session),
        "size": stat.st_size,
        "sha256": sha256_file(path),
    }


def readiness_output_path(readiness: dict[str, Any], key: str, session: Path) -> Path | None:
    outputs = readiness.get("outputs")
    if not isinstance(outputs, dict):
        return None
    item = outputs.get(key)
    if not isinstance(item, dict):
        return None
    raw_path = item.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        return None
    path = Path(raw_path)
    return path if path.is_absolute() else session / path


def handoff_fingerprint_matches(payload: dict[str, Any] | None, session: Path) -> bool:
    if not isinstance(payload, dict) or payload.get("schema") != HANDOFF_SCHEMA:
        return False
    if payload.get("status") not in {"ready", "review_required"}:
        return False
    fingerprint = payload.get("transcript_fingerprint")
    if not isinstance(fingerprint, dict):
        return False
    raw_path = fingerprint.get("path")
    expected_sha = fingerprint.get("sha256")
    expected_size = fingerprint.get("size")
    if not isinstance(raw_path, str) or not isinstance(expected_sha, str):
        return False
    path = Path(raw_path)
    path = path if path.is_absolute() else session / path
    if not path.exists():
        return False
    paths = payload.get("paths")
    if not isinstance(paths, dict) or paths.get("transcript") != raw_path:
        return False
    readiness = read_json(session / "derived/readiness/session_readiness.json")
    selected_profile = payload.get("selected_transcript_profile")
    if not isinstance(readiness, dict) or readiness.get("selected_profile") != selected_profile:
        return False
    readiness_transcript = readiness_output_path(readiness, "transcript", session)
    if readiness_transcript is None or readiness_transcript.resolve() != path.resolve():
        return False
    if isinstance(expected_size, int) and path.stat().st_size != expected_size:
        return False
    return sha256_file(path) == expected_sha


def asr_provenance(session: Path) -> dict[str, Any]:
    progress = transcribe_chunk_progress(session) or {}
    live_cache_path = session / "derived/live/live_asr_cache_report.json"
    live_cache = read_json(live_cache_path)
    transcribe_report_path = session / "derived/transcript-simple/whisper-cpp/resolved/transcribe_simple_report.json"
    transcribe_report = read_json(transcribe_report_path)
    tracks: dict[str, Any] = {}
    for row in progress.get("tracks") or []:
        if not isinstance(row, dict):
            continue
        track = str(row.get("track") or "unknown")
        reused = int(row.get("chunks_reused") or 0)
        transcribed = int(row.get("chunks_transcribed") or 0)
        if reused and not transcribed:
            mode = "reused"
        elif reused and transcribed:
            mode = "mixed_reuse_and_batch"
        else:
            mode = "batch"
        tracks[track] = {
            "mode": mode,
            "chunks_total": int(row.get("chunks_total") or 0),
            "chunks_reused": reused,
            "chunks_transcribed": transcribed,
            "reused_sec": float(row.get("reused_sec") or 0.0),
            "transcribed_sec": float(row.get("transcribed_sec") or 0.0),
            "remaining_sec": float(row.get("remaining_sec") or 0.0),
            "report": row.get("report"),
        }
    return {
        "tracks": tracks,
        "live_cache": {
            "status": live_cache.get("status") if isinstance(live_cache, dict) else "missing",
            "report": rel(live_cache_path, session) if live_cache_path.exists() else None,
            "reasons": live_cache.get("reasons", []) if isinstance(live_cache, dict) else ["live_cache_report_missing"],
            "track_compatibility": live_cache.get("track_compatibility", {}) if isinstance(live_cache, dict) else {},
        },
        "runtime": {
            "track_workers": transcribe_report.get("track_workers") if isinstance(transcribe_report, dict) else None,
            "threads": transcribe_report.get("threads") if isinstance(transcribe_report, dict) else None,
            "micro_asr_workers": transcribe_report.get("micro_asr_workers")
            if isinstance(transcribe_report, dict)
            else None,
            "report": rel(transcribe_report_path, session) if transcribe_report_path.exists() else None,
        },
        "totals": progress,
    }


def asr_invocation(args: argparse.Namespace) -> dict[str, Any]:
    if args.skip_transcription:
        mode = "transcription_skipped"
    elif args.force_asr:
        mode = "forced_batch"
    elif args.reuse_asr_cache:
        mode = "cache_reuse_requested"
    else:
        mode = "default"
    return {
        "mode": mode,
        "force_asr": bool(args.force_asr),
        "reuse_asr_cache": bool(args.reuse_asr_cache),
        "skip_transcription": bool(args.skip_transcription),
        "track_workers": args.asr_track_workers,
        "threads": args.asr_threads,
        "micro_asr_workers": args.micro_asr_workers,
    }


def build_authoritative_handoff(
    *,
    session: Path,
    repo_root: Path,
    started_at: str,
    elapsed_sec: float,
    report_path: Path,
    deferred_status: str = "pending",
    asr_invocation_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    readiness_path = session / "derived/readiness/session_readiness.json"
    quality_path = session / "derived/synthesis-simple/extractive/quality_verdict.json"
    readiness = read_json(readiness_path)
    quality = read_json(quality_path)
    readiness = readiness if isinstance(readiness, dict) else {}
    quality = quality if isinstance(quality, dict) else {}
    selected_profile = str(readiness.get("selected_profile") or quality.get("selected_transcript_profile") or "")
    transcript = readiness_output_path(readiness, "transcript", session)
    notes = readiness_output_path(readiness, "notes", session)
    verdict_markdown = readiness_output_path(readiness, "quality_verdict", session)
    required_paths: list[tuple[str, Path | None]] = [
        ("transcript", transcript),
        ("notes", notes),
        ("verdict", verdict_markdown),
        ("readiness", readiness_path),
        ("verdict_json", quality_path),
    ]
    missing = [name if path is None else rel(path, session) for name, path in required_paths if path is None or not path.exists()]
    use_gate = str(readiness.get("use_gate") or "pipeline_incomplete")
    verdict = str(readiness.get("verdict") or quality.get("verdict") or "unknown")
    if missing or not selected_profile:
        status = "blocked"
    elif use_gate == "review_first" or verdict != "good":
        status = "review_required"
    else:
        status = "ready"
    readiness_next = first_next_command(readiness) or f"murmurmark status {rel(session, repo_root)}"
    recommended_next = (
        f"murmurmark enrich {rel(session, repo_root)}"
        if status == "review_required" and deferred_status == "pending"
        else readiness_next
    )
    fingerprint = file_fingerprint(transcript, session) if transcript is not None and transcript.exists() else None
    now = datetime.now(timezone.utc).isoformat()
    provenance = asr_provenance(session)
    if asr_invocation_context is not None:
        provenance["invocation"] = asr_invocation_context
    checkpoint = {
        "schema": HANDOFF_SCHEMA,
        "version": 1,
        "generator": {"name": "run-session-pipeline", "version": SCRIPT_VERSION},
        "session": str(session),
        "status": status,
        "selected_transcript_profile": selected_profile or None,
        "verdict": verdict,
        "use_gate": use_gate,
        "paths": {
            "transcript": rel(transcript, session) if transcript is not None and transcript.exists() else None,
            "notes": rel(notes, session) if notes is not None and notes.exists() else None,
            "verdict": rel(verdict_markdown, session)
            if verdict_markdown is not None and verdict_markdown.exists()
            else None,
            "verdict_json": rel(quality_path, session) if quality_path.exists() else None,
            "readiness": rel(readiness_path, session) if readiness_path.exists() else None,
            "pipeline_report": rel(report_path, session),
        },
        "started_at": started_at,
        "ready_at": now if status in {"ready", "review_required"} else None,
        "elapsed_sec": round(elapsed_sec, 3),
        "asr_provenance": provenance,
        "required_gates": {
            "selected_profile_present": bool(selected_profile),
            "required_outputs_present": not missing,
            "missing_outputs": missing,
            "use_gate": use_gate,
        },
        "deferred_enrichment": {
            "status": deferred_status,
            "report": "derived/pipeline-run/deferred_enrichment_report.json",
            "command": f"murmurmark enrich {rel(session, repo_root)}",
        },
        "recommended_next": recommended_next,
        "transcript_fingerprint": fingerprint,
    }
    write_json_atomic(authoritative_handoff_path(session), checkpoint)
    return checkpoint


def update_deferred_checkpoint(
    *,
    session: Path,
    status: str,
    elapsed_sec: float,
    report_path: Path,
    error: str | None = None,
) -> dict[str, Any] | None:
    path = authoritative_handoff_path(session)
    payload = read_json(path)
    if not isinstance(payload, dict):
        return None
    deferred = payload.get("deferred_enrichment")
    deferred = dict(deferred) if isinstance(deferred, dict) else {}
    deferred.update(
        {
            "status": status,
            "finished_at": datetime.now(timezone.utc).isoformat() if status in {"completed", "failed", "interrupted"} else None,
            "elapsed_sec": round(elapsed_sec, 3),
            "report": rel(report_path, session),
        }
    )
    if error:
        deferred["error"] = error
    payload["deferred_enrichment"] = deferred
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    write_json_atomic(path, payload)
    return payload


def print_authoritative_handoff(payload: dict[str, Any], session: Path, repo_root: Path) -> None:
    paths = payload.get("paths") if isinstance(payload.get("paths"), dict) else {}
    deferred = payload.get("deferred_enrichment") if isinstance(payload.get("deferred_enrichment"), dict) else {}
    print("", flush=True)
    print("authoritative handoff ready", flush=True)
    print(f"status: {payload.get('status')}", flush=True)
    print(f"profile: {payload.get('selected_transcript_profile')}", flush=True)
    if paths.get("transcript"):
        print(f"transcript: {rel(session / str(paths['transcript']), repo_root)}", flush=True)
    if paths.get("verdict"):
        print(f"verdict: {rel(session / str(paths['verdict']), repo_root)}", flush=True)
    print(f"deferred enrichment: {deferred.get('status', 'pending')}", flush=True)
    print(f"next: {payload.get('recommended_next')}", flush=True)


def default_handoff_reuse_allowed(args: argparse.Namespace) -> bool:
    return not any(
        (
            args.force_asr,
            args.reuse_asr_cache,
            args.skip_build,
            args.skip_preprocess,
            args.skip_transcription,
            args.skip_audits,
            args.skip_stronger_audio_judge,
            args.skip_cleanup,
            args.allow_partial,
            args.plan_only,
        )
    )


def steps_for_phase(steps: list[dict[str, Any]], phase: str) -> list[dict[str, Any]]:
    handoff = [item for item in steps if item.get("phase") == HANDOFF_PHASE]
    deferred = [item for item in steps if item.get("phase") == DEFERRED_PHASE]
    if phase == "handoff":
        return handoff
    if phase == "deferred":
        return deferred
    return handoff + deferred


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
    except subprocess.TimeoutExpired:
        pass
    # The group leader may exit on SIGTERM while children keep running. Probe
    # the process group itself before declaring the step stopped.
    try:
        os.killpg(process.pid, 0)
    except ProcessLookupError:
        return
    except OSError:
        if process.poll() is not None:
            return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except OSError:
        process.kill()
    if process.poll() is None:
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
    pipeline_phase: str,
    timeout_sec: int = 0,
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
                phase=pipeline_phase,
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
            timed_out = False
            try:
                while True:
                    returncode = process.poll()
                    if returncode is not None:
                        break
                    now = time.monotonic()
                    if timeout_sec > 0 and now - started >= timeout_sec:
                        timed_out = True
                        terminate_process_group(process)
                        break
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
                            phase=pipeline_phase,
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
                        resume_command = (
                            f"murmurmark enrich {rel(session, repo_root)}"
                            if pipeline_phase == DEFERRED_PHASE
                            else f"murmurmark process {rel(session, repo_root)}"
                        )
                        print(f"[run] resume hint: Ctrl-C is safe; rerun `{resume_command}`", flush=True)
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
                    phase=pipeline_phase,
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
                resume_command = (
                    f"murmurmark enrich {rel(session, repo_root)}"
                    if pipeline_phase == DEFERRED_PHASE
                    else f"murmurmark process {rel(session, repo_root)}"
                )
                result.update(
                    {
                        "status": "interrupted",
                        "returncode": process.returncode,
                        "finished_at": datetime.now(timezone.utc).isoformat(),
                        "duration_sec": round(time.monotonic() - started, 3),
                        "stdout_tail": stdout_tail,
                        "stderr_tail": stderr_tail,
                        "message": f"interrupted by Ctrl-C; rerun `{resume_command}`",
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
    if timed_out:
        result["status"] = "failed"
        result["message"] = f"step exceeded timeout of {timeout_sec}s; process group terminated"
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
            "asr_track_workers": args.asr_track_workers,
            "asr_threads": args.asr_threads,
            "micro_asr_workers": args.micro_asr_workers,
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
    invocation_started_at = datetime.now(timezone.utc).isoformat()
    invocation_started_clock = time.monotonic()
    args = parse_args()
    requested_phase = "full" if args.full else args.phase
    repo_root = Path(__file__).resolve().parents[1]
    args.murmurmark_bin = resolve_murmurmark_bin(args.murmurmark_bin, repo_root)
    session = args.session.expanduser()
    existing_handoff = read_json(authoritative_handoff_path(session))
    reusable_handoff = handoff_fingerprint_matches(existing_handoff, session) and default_handoff_reuse_allowed(args)

    if requested_phase == "handoff" and reusable_handoff:
        assert isinstance(existing_handoff, dict)
        append_authoritative_handoff_run(
            session=session,
            mode="checkpoint_reuse",
            started_at=invocation_started_at,
            elapsed_sec=time.monotonic() - invocation_started_clock,
            checkpoint=existing_handoff,
            args=args,
        )
        print_authoritative_handoff(existing_handoff, session, repo_root)
        return 0
    if requested_phase == "deferred" and not handoff_fingerprint_matches(existing_handoff, session):
        print("deferred enrichment blocked: authoritative handoff is missing or stale", file=sys.stderr)
        print(f"next: murmurmark process {rel(session, repo_root)}", file=sys.stderr)
        return 2

    if args.report:
        report_path = args.report.expanduser()
    elif args.plan_only:
        name = "deferred_enrichment_plan.json" if requested_phase == "deferred" else "pipeline_plan_report.json"
        report_path = session / "derived/pipeline-run" / name
    elif requested_phase == "deferred":
        report_path = session / "derived/pipeline-run/deferred_enrichment_report.json"
    else:
        report_path = session / "derived/pipeline-run/pipeline_run_report.json"

    all_steps = build_steps(args, repo_root, session)
    effective_phase = "deferred" if requested_phase == "full" and reusable_handoff else requested_phase
    steps = steps_for_phase(all_steps, effective_phase)
    plan_metadata = build_plan_metadata(steps, session, report_path, repo_root, plan_only=args.plan_only)
    results: list[dict[str, Any]] = []
    started_at = datetime.now(timezone.utc).isoformat()
    started_clock = time.monotonic()
    final_status = "passed"
    initial_state_phase = DEFERRED_PHASE if effective_phase == "deferred" else HANDOFF_PHASE
    write_pipeline_run_state(
        session=session,
        repo_root=repo_root,
        report_path=report_path,
        started_at=started_at,
        status="running",
        phase=initial_state_phase,
        message="pipeline_started",
        completed_steps=[],
    )
    if effective_phase == "deferred":
        update_deferred_checkpoint(
            session=session,
            status="running",
            elapsed_sec=0.0,
            report_path=report_path,
        )
    if not args.plan_only:
        write_json(
            report_path,
            {
                "schema": SCHEMA,
                "generator": {"name": "run-session-pipeline", "version": SCRIPT_VERSION},
                "started_at": started_at,
                "finished_at": None,
                "session": str(session),
                "status": "running",
                "phase": requested_phase,
                "plan": plan_metadata,
                "steps": [],
            },
        )
    check_capture = effective_phase != "deferred"
    interrupted_warnings = interrupted_capture_warnings(session) if check_capture else []
    silent_warnings = silent_capture_warnings(session) if check_capture else []
    sparse_warnings = sparse_capture_warnings(session) if check_capture else []
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
            phase=HANDOFF_PHASE,
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
            phase=HANDOFF_PHASE,
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
            phase=HANDOFF_PHASE,
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
            pipeline_phase=str(item.get("phase") or HANDOFF_PHASE),
            timeout_sec=(
                args.deferred_step_timeout_sec
                if str(item.get("phase") or HANDOFF_PHASE) == DEFERRED_PHASE
                else 0
            ),
        )
        results.append(result)
        item_phase = str(item.get("phase") or HANDOFF_PHASE)
        write_pipeline_run_state(
            session=session,
            repo_root=repo_root,
            report_path=report_path,
            started_at=started_at,
            status="running" if result["status"] not in {"failed", "interrupted"} else str(result["status"]),
            phase=item_phase,
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
        if (
            not args.plan_only
            and result["status"] in {"passed", "passed_with_warnings"}
            and result.get("name") == "session_readiness"
        ):
            checkpoint = build_authoritative_handoff(
                session=session,
                repo_root=repo_root,
                started_at=started_at,
                elapsed_sec=time.monotonic() - started_clock,
                report_path=report_path,
                deferred_status="running" if requested_phase == "full" else "pending",
                asr_invocation_context=asr_invocation(args),
            )
            if checkpoint.get("status") in {"ready", "review_required"}:
                append_authoritative_handoff_run(
                    session=session,
                    mode="computed",
                    started_at=invocation_started_at,
                    elapsed_sec=time.monotonic() - invocation_started_clock,
                    checkpoint=checkpoint,
                    args=args,
                )
                print_authoritative_handoff(checkpoint, session, repo_root)
            else:
                final_status = "failed"
                break
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
    command_handoff = pipeline_handoff(
        status=status,
        session=session,
        report_path=report_path,
        repo_root=repo_root,
        readiness=readiness,
    )
    handoff_checkpoint = read_json(authoritative_handoff_path(session))
    handoff_elapsed = (
        float(handoff_checkpoint.get("elapsed_sec") or 0.0)
        if isinstance(handoff_checkpoint, dict)
        else sum(
            float(item.get("duration_sec") or 0.0)
            for item in results
            if item.get("phase") == HANDOFF_PHASE
        )
    )
    deferred_elapsed = sum(
        float(item.get("duration_sec") or 0.0)
        for item in results
        if item.get("phase") == DEFERRED_PHASE
    )
    pending_deferred = [
        str(item.get("name") or "")
        for item in all_steps
        if item.get("phase") == DEFERRED_PHASE and item.get("enabled")
    ]
    if effective_phase in {"deferred", "full"} and status == "passed":
        pending_deferred = []
    report = {
        "schema": SCHEMA,
        "generator": {"name": "run-session-pipeline", "version": SCRIPT_VERSION},
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "session": str(session),
        "status": status,
        "phase": requested_phase,
        "inputs": {
            "model": str(args.model),
            "language": args.language,
            "prompt_file": str(args.prompt_file) if args.prompt_file and args.prompt_file.exists() else None,
            "audio_judge_queue": str(args.audio_judge_queue) if args.audio_judge_queue.exists() else None,
            "progress_interval_sec": args.progress_interval_sec,
            "asr_track_workers": args.asr_track_workers,
            "asr_threads": args.asr_threads,
            "micro_asr_workers": args.micro_asr_workers,
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
        "recommended_next": command_handoff["recommended_next"],
        "next_commands": command_handoff["next_commands"],
        "open_commands": command_handoff["open_commands"],
        "progress": {
            "asr_chunks": transcribe_chunk_progress(session),
            "asr_remaining_estimate": estimate_remaining_runtime(
                transcribe_chunk_progress(session),
                sum(float(item.get("duration_sec") or 0.0) for item in results if item.get("name") == "transcribe_current"),
            ),
        },
        "performance": {
            "authoritative_handoff_elapsed_sec": round(handoff_elapsed, 3),
            "deferred_elapsed_sec": round(deferred_elapsed, 3),
            "critical_path_stages": [
                str(item.get("name") or "")
                for item in results
                if item.get("phase") == HANDOFF_PHASE and item.get("status") not in {"skipped", "planned"}
            ],
            "deferred_stages_pending": pending_deferred,
            "transcript_ready_at": handoff_checkpoint.get("ready_at")
            if isinstance(handoff_checkpoint, dict)
            else None,
            "asr_provenance": handoff_checkpoint.get("asr_provenance")
            if isinstance(handoff_checkpoint, dict)
            else asr_provenance(session),
        },
        "plan": plan_metadata,
        "steps": results,
    }

    ran_deferred = effective_phase == "deferred" or any(
        item.get("phase") == DEFERRED_PHASE and item.get("enabled") for item in steps
    )
    if ran_deferred and not args.plan_only:
        deferred_status = "completed" if status == "passed" else status
        if not handoff_fingerprint_matches(read_json(authoritative_handoff_path(session)), session):
            deferred_status = "failed"
            status = "failed"
            report["status"] = "failed"
            report.setdefault("warnings", []).append("authoritative_transcript_fingerprint_changed")
        update_deferred_checkpoint(
            session=session,
            status=deferred_status,
            elapsed_sec=time.monotonic() - started_clock,
            report_path=report_path,
            error="authoritative_transcript_fingerprint_changed" if deferred_status == "failed" else None,
        )

    write_json(report_path, report)
    write_pipeline_run_state(
        session=session,
        repo_root=repo_root,
        report_path=report_path,
        started_at=started_at,
        status=status,
        phase=DEFERRED_PHASE if ran_deferred else HANDOFF_PHASE,
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
