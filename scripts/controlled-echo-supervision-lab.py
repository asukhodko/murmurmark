#!/usr/bin/env python3
"""Prepare, capture and inspect Controlled Echo Supervision Lab v1 sessions."""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np

from controlled_echo_supervision import (
    ANALYSIS_SAMPLE_RATE,
    DOUBLE_TALK_PROMPTS,
    LOCAL_ONLY_PROMPTS,
    OPENING_PROMPTS,
    REMOTE_TTS_TEXT,
    SCHEMA_CAPTURE,
    SCHEMA_INSPECTION,
    SCHEMA_SCHEDULE,
    audio_metrics,
    build_schedule,
    command_path,
    convert_audio,
    default_lab_root,
    default_model_path,
    default_policy_path,
    default_sessions_root,
    expected_prompt_text,
    fingerprint,
    load_policy,
    materialize_looped_stimulus,
    normalize_text,
    phase_bounds,
    phase_operator_instruction,
    policy_sha,
    prompt_supported_word_spans,
    prompt_operator_action,
    prompt_operator_message,
    prompts_for_phase,
    read_audio,
    read_audio_slice,
    read_json,
    read_jsonl,
    relative,
    sha256,
    shift_remote_for_mic,
    speaker_validation_chunk_sec,
    stable_id,
    local_only_gate_reasons,
    remote_only_gate_reasons,
    token_recall,
    tokens,
    total_duration,
    unique_token_ratio,
    validate_stimulus_audio,
    validate_schedule,
    write_audio,
    write_json,
    write_jsonl,
)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_utc(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def output_volume() -> int | None:
    try:
        result = subprocess.run(
            [command_path("osascript"), "-e", "output volume of (get volume settings)"],
            check=True,
            text=True,
            capture_output=True,
            timeout=5,
        )
        return int(result.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def print_operator_instructions() -> None:
    print("\n=== ВАЖНО: ТВОИ ДЕЙСТВИЯ ВО ВРЕМЯ ЗАПИСИ ===")
    print("- >>> ПРОИЗНЕСИ ВСЛУХ СЕЙЧАС: произнеси показанную фразу своим голосом.")
    print("- МОЛЧИ / СОХРАНЯЙ ТИШИНУ: не говори и не печатай.")
    print("- ПЕЧАТАЙ НА КЛАВИАТУРЕ: открой другое окно, например заметки; не говори.")
    print("- Во время double-talk говори поверх цифрового диктора.")
    print("- Не меняй громкость macOS до полного завершения записи.")


def confirm_operator_instructions(
    *,
    confirmed_by_flag: bool,
    confirmation_mode: str,
) -> str:
    if confirmed_by_flag:
        print(f"Инструкции подтверждены: {confirmation_mode}.\n")
        return confirmation_mode
    print_operator_instructions()
    if not sys.stdin.isatty():
        raise RuntimeError(
            "echo-lab capture requires operator confirmation in an interactive terminal; "
            "read the instructions and rerun with `--confirm-operator-instructions` only when ready"
        )
    answer = input("Введи ГОТОВ, если будешь читать фразы вслух и не менять громкость: ")
    if answer.strip().upper() != "ГОТОВ":
        raise RuntimeError("operator instructions were not confirmed; capture was not started")
    print("Инструкции подтверждены. Запись начинается.\n")
    return "interactive"


def device_metadata() -> dict[str, Any]:
    payload: dict[str, Any] = {
        "output_volume_percent": output_volume(),
        "host": os.uname().nodename,
        "platform": sys.platform,
    }
    try:
        result = subprocess.run(
            [command_path("system_profiler"), "SPAudioDataType", "-json"],
            check=True,
            text=True,
            capture_output=True,
            timeout=30,
        )
        decoded = json.loads(result.stdout)
        payload["audio_devices"] = decoded.get("SPAudioDataType", [])
    except (OSError, json.JSONDecodeError, subprocess.SubprocessError):
        payload["audio_devices"] = []
        payload["audio_devices_error"] = "unavailable"
    return payload


def choose_russian_voice() -> str:
    result = subprocess.run(
        [command_path("say"), "-v", "?"],
        check=True,
        text=True,
        capture_output=True,
    )
    candidates: list[str] = []
    for line in result.stdout.splitlines():
        if "ru_RU" not in line:
            continue
        name = line.split()[0].strip()
        if name:
            candidates.append(name)
    if not candidates:
        raise RuntimeError("macOS has no ru_RU `say` voice; install a Russian system voice")
    for preferred in ("Milena", "Yuri"):
        if preferred in candidates:
            return preferred
    return candidates[0]


def stimulus_text(minimum_words: int = 60) -> str:
    pieces: list[str] = []
    while len(tokens(" ".join(pieces))) < minimum_words:
        pieces.append(REMOTE_TTS_TEXT)
    return " ".join(pieces)


def generate_stimulus(
    *,
    destination: Path,
    duration_sec: float,
    source_aiff: Path,
) -> None:
    materialize_looped_stimulus(
        source_aiff,
        destination,
        duration_sec=duration_sec,
        sample_rate=48_000,
    )


def prepare(args: argparse.Namespace) -> int:
    lab_root = default_lab_root(args.sessions_root.resolve())
    lab_root.mkdir(parents=True, exist_ok=True)
    lock_path = lab_root / "prepare.lock"
    lock = lock_path.open("a+", encoding="utf-8")
    try:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError(
                "another `murmurmark echo-lab prepare` is already active"
            ) from error
        lock.seek(0)
        lock.truncate()
        lock.write(f"pid={os.getpid()} started_at={utc_now()}\n")
        lock.flush()
        return prepare_locked(args)
    finally:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        finally:
            lock.close()


def prepare_locked(args: argparse.Namespace) -> int:
    policy_path = args.policy.resolve()
    policy = load_policy(policy_path)
    sessions_root = args.sessions_root.resolve()
    lab_root = default_lab_root(sessions_root)
    stimuli_dir = lab_root / "stimuli"
    stimuli_dir.mkdir(parents=True, exist_ok=True)
    requirements: dict[str, float] = {}
    for phase in policy["phases"]:
        stimulus = phase.get("stimulus")
        if stimulus:
            requirements[str(stimulus)] = max(
                requirements.get(str(stimulus), 0.0),
                float(phase["duration_sec"]),
            )
    text = stimulus_text()
    manifest_path = stimuli_dir / "stimuli_manifest.json"
    if manifest_path.is_file():
        existing = read_json(manifest_path)
        existing_rows = {
            str(row.get("id")): row
            for row in existing.get("stimuli", [])
            if isinstance(row, dict)
        }
        valid = True
        for name, duration_sec in requirements.items():
            row = existing_rows.get(name)
            path = sessions_root / str(row.get("path") or "") if row else Path()
            row_valid = (
                row is not None
                and float(row.get("duration_sec") or 0.0) == duration_sec
                and row.get("expected_text_sha256") == sha256_text(text)
                and path.is_file()
                and sha256(path) == row.get("sha256")
            )
            if row_valid:
                try:
                    validate_stimulus_audio(
                        path,
                        duration_sec=duration_sec,
                        sample_rate=48_000,
                        maximum_peak=float(policy["validation"]["maximum_abs_peak"]),
                    )
                except RuntimeError:
                    row_valid = False
            valid = valid and row_valid
        if valid:
            existing["policy"] = relative(policy_path, args.repo_root.resolve())
            existing["policy_sha256"] = policy_sha(policy_path)
            write_json(manifest_path, existing)
            print(f"already prepared: {manifest_path}")
            print(
                "next: murmurmark echo-lab capture --out sessions/<session> "
                "--scenario speaker_train_quiet"
            )
            return 0

    voice = choose_russian_voice()

    rows: list[dict[str, Any]] = []
    source_aiff = stimuli_dir / "source.aiff"
    print(f"rendering local Russian TTS with voice {voice}...", flush=True)
    subprocess.run(
        [
            command_path("say"),
            "-v",
            voice,
            "-r",
            "165",
            "-o",
            str(source_aiff),
            text,
        ],
        check=True,
        timeout=120,
    )
    for name, duration_sec in sorted(requirements.items()):
        print(f"materializing {name} ({duration_sec:.0f}s)...", flush=True)
        destination = stimuli_dir / f"{name}.wav"
        generate_stimulus(
            destination=destination,
            duration_sec=duration_sec,
            source_aiff=source_aiff,
        )
        audio_validation = validate_stimulus_audio(
            destination,
            duration_sec=duration_sec,
            sample_rate=48_000,
            maximum_peak=float(policy["validation"]["maximum_abs_peak"]),
        )
        rows.append(
            {
                "id": name,
                "path": relative(destination, sessions_root),
                "duration_sec": duration_sec,
                "sample_rate": 48000,
                "channels": 1,
                "sha256": sha256(destination),
                "voice": voice,
                "rate_words_per_minute": 165,
                "expected_text": text,
                "expected_text_sha256": sha256_text(text),
                "expected_token_count": len(tokens(text)),
                "content_class": "generic_non_work_russian_tts",
                "audio_validation": audio_validation,
            }
        )
    source_aiff.unlink(missing_ok=True)

    manifest = {
        "schema": "murmurmark.controlled_echo_stimuli/v1",
        "profile": policy["profile"],
        "policy": relative(policy_path, args.repo_root.resolve()),
        "policy_sha256": policy_sha(policy_path),
        "processing": "local_only",
        "network_used": False,
        "redistribution": "forbidden",
        "stimuli": rows,
    }
    write_json(manifest_path, manifest)
    print(f"prepared: {manifest_path}")
    for row in rows:
        print(f"  {row['id']}: {row['duration_sec']:.0f}s sha256={row['sha256'][:12]}")
    print("next: murmurmark echo-lab capture --out sessions/<session> --scenario speaker_train_quiet")
    return 0


def sha256_text(text: str) -> str:
    import hashlib

    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


def wait_until(
    deadline: float,
    child: subprocess.Popen[Any],
    *,
    monitor: Callable[[], None] | None = None,
    monitor_interval_sec: float = 5.0,
) -> None:
    next_monitor_at = time.monotonic()
    while True:
        if child.poll() is not None:
            raise RuntimeError(f"recording stopped early with status {child.returncode}")
        now = time.monotonic()
        remaining = deadline - now
        if remaining <= 0:
            return
        if monitor is not None and now >= next_monitor_at:
            monitor()
            next_monitor_at = now + monitor_interval_sec
            remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.05, remaining))


def append_event(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()


def wait_for_capture_start(
    events_path: Path,
    child: subprocess.Popen[Any],
    timeout_sec: float = 60.0,
) -> tuple[dt.datetime, float]:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if child.poll() is not None:
            raise RuntimeError(f"recording failed before capture.started: status={child.returncode}")
        for row in read_jsonl(events_path):
            if row.get("type") == "capture.started":
                started_wall = parse_utc(str(row["t"]))
                now_wall = dt.datetime.now(dt.timezone.utc)
                started_mono = time.monotonic() - max(0.0, (now_wall - started_wall).total_seconds())
                return started_wall, started_mono
        time.sleep(0.05)
    raise RuntimeError("timed out waiting for capture.started")


def stimulus_map(
    manifest: dict[str, Any],
    sessions_root: Path,
    policy: dict[str, Any],
) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for row in manifest.get("stimuli", []):
        path = sessions_root / str(row["path"])
        expected_sha256 = str(row.get("sha256") or "")
        actual_sha256 = sha256(path) if path.is_file() else "missing"
        if actual_sha256 != expected_sha256:
            raise RuntimeError(
                f"stimulus missing or changed: {path}; "
                f"expected_sha256={expected_sha256 or 'missing'}; "
                f"actual_sha256={actual_sha256}; "
                "run `murmurmark echo-lab prepare`, then rerun capture with a fresh SESSION"
            )
        validate_stimulus_audio(
            path,
            duration_sec=float(row["duration_sec"]),
            sample_rate=int(row["sample_rate"]),
            maximum_peak=float(policy["validation"]["maximum_abs_peak"]),
        )
        result[str(row["id"])] = path
    return result


def capture(args: argparse.Namespace) -> int:
    policy_path = args.policy.resolve()
    policy = load_policy(policy_path)
    sessions_root = args.sessions_root.resolve()
    session = args.out.resolve()
    if session.exists():
        raise RuntimeError(f"capture output already exists: {session}")
    scenario = policy["scenarios"].get(args.scenario)
    if not isinstance(scenario, dict):
        allowed = ", ".join(sorted(policy["scenarios"]))
        raise RuntimeError(f"unknown scenario {args.scenario!r}; choose one of: {allowed}")

    stimuli_manifest_path = default_lab_root(sessions_root) / "stimuli" / "stimuli_manifest.json"
    if not stimuli_manifest_path.is_file():
        raise RuntimeError("stimuli are not prepared; run `murmurmark echo-lab prepare`")
    stimuli_manifest = read_json(stimuli_manifest_path)
    if stimuli_manifest.get("policy_sha256") != policy_sha(policy_path):
        raise RuntimeError("stimuli policy hash is stale; rerun `murmurmark echo-lab prepare`")
    stimuli = stimulus_map(stimuli_manifest, sessions_root, policy)

    metadata_before = device_metadata()
    volume_before = metadata_before.get("output_volume_percent")
    minimum_volume = int(scenario["output_volume_percent_min"])
    maximum_volume = int(scenario["output_volume_percent_max"])
    if volume_before is None:
        raise RuntimeError("cannot read macOS output volume")
    if not minimum_volume <= volume_before <= maximum_volume:
        raise RuntimeError(
            f"scenario {args.scenario} requires output volume {minimum_volume}..{maximum_volume}%, "
            f"current value is {volume_before}%"
        )

    schedule = build_schedule(policy)
    validate_schedule(schedule, total_duration(policy))
    prompts = [prompt for phase in schedule for prompt in prompts_for_phase(phase)]
    print(f"scenario: {args.scenario}")
    print(f"placement: {scenario['placement']}")
    print(f"output volume: {volume_before}%")
    print(f"duration: {total_duration(policy):.0f}s")
    if args.preflight_only:
        print_operator_instructions()
        print("\necho-lab preflight: passed; raw capture has not started.")
        return 0
    confirmation_mode = confirm_operator_instructions(
        confirmed_by_flag=bool(args.confirm_operator_instructions),
        confirmation_mode=str(args.operator_confirmation_mode),
    )
    run_id = stable_id(session.name, policy_sha(policy_path), utc_now())
    run_dir = default_lab_root(sessions_root) / "runs" / run_id
    run_events = run_dir / "echo_lab_events.jsonl"
    run_dir.mkdir(parents=True, exist_ok=False)
    write_json(
        run_dir / "planned_schedule.json",
        {
            "schema": SCHEMA_SCHEDULE,
            "policy_sha256": policy_sha(policy_path),
            "scenario": args.scenario,
            "total_duration_sec": total_duration(policy),
            "phases": schedule,
            "prompts": prompts,
            "operator_instructions_confirmed": True,
            "operator_confirmation_mode": confirmation_mode,
        },
    )

    record_command = [
        str(args.murmurmark_executable.resolve()),
        "record",
        "--out",
        str(session),
        "--duration",
        f"{total_duration(policy) + float(policy['capture']['post_roll_sec']):.6f}",
        "--target-bundle",
        "system",
    ]
    append_event(
        run_events,
        {
            "schema": "murmurmark.controlled_echo_event/v1",
            "type": "operator_instructions_confirmed",
            "confirmation_mode": confirmation_mode,
            "wall_time": utc_now(),
        },
    )
    print(f"+ {' '.join(record_command)}", flush=True)
    child = subprocess.Popen(record_command)
    active_player: subprocess.Popen[Any] | None = None
    phase_events: list[dict[str, Any]] = []
    capture_started_wall: dt.datetime | None = None
    capture_started_mono: float | None = None
    active_phase_id = "capture_start"
    maximum_volume_drift = int(policy["validation"]["maximum_output_volume_drift_percent"])

    def monitor_output_volume() -> None:
        current_volume = output_volume()
        if current_volume is None:
            raise RuntimeError(
                "output volume became unavailable during capture; partial session is excluded"
            )
        if abs(current_volume - volume_before) > maximum_volume_drift:
            raise RuntimeError(
                "output volume changed during "
                f"{active_phase_id}: {volume_before}% -> {current_volume}% "
                f"(allowed drift: {maximum_volume_drift}%); partial session is excluded; "
                "rerun with a fresh SESSION and keep volume fixed"
            )

    try:
        capture_started_wall, capture_started_mono = wait_for_capture_start(session / "events.jsonl", child)
        for phase in schedule:
            active_phase_id = str(phase["id"])
            planned_start = float(phase["planned_start_sec"])
            planned_end = float(phase["planned_end_sec"])
            wait_until(
                capture_started_mono + planned_start,
                child,
                monitor=monitor_output_volume,
            )
            actual_start = time.monotonic() - capture_started_mono
            operator_instruction = phase_operator_instruction(str(phase["kind"]))
            row = {
                "schema": "murmurmark.controlled_echo_event/v1",
                "type": "phase_started",
                "phase_id": phase["id"],
                "kind": phase["kind"],
                "planned_at_sec": planned_start,
                "actual_at_sec": round(actual_start, 6),
                "error_sec": round(actual_start - planned_start, 6),
                "operator_instruction": operator_instruction,
                "wall_time": utc_now(),
            }
            phase_events.append(row)
            append_event(run_events, row)
            print(f"\n[{actual_start:07.2f}s] {phase['id']}")
            print(f">>> {operator_instruction}", flush=True)

            stimulus_name = phase.get("stimulus")
            if stimulus_name:
                active_player = subprocess.Popen([command_path("afplay"), str(stimuli[str(stimulus_name)])])
                playback_row = {
                    "schema": "murmurmark.controlled_echo_event/v1",
                    "type": "stimulus_started",
                    "phase_id": phase["id"],
                    "stimulus_id": stimulus_name,
                    "actual_at_sec": round(time.monotonic() - capture_started_mono, 6),
                    "source_sha256": sha256(stimuli[str(stimulus_name)]),
                    "wall_time": utc_now(),
                }
                phase_events.append(playback_row)
                append_event(run_events, playback_row)

            phase_prompts = prompts_for_phase(phase)
            for prompt in phase_prompts:
                wait_until(
                    capture_started_mono + float(prompt["planned_at_sec"]),
                    child,
                    monitor=monitor_output_volume,
                )
                message = prompt_operator_message(str(phase["kind"]), str(prompt["text"]))
                actual_prompt = time.monotonic() - capture_started_mono
                print(f"[{actual_prompt:07.2f}s] {message}", flush=True)
                prompt_row = {
                    "schema": "murmurmark.controlled_echo_event/v1",
                    "type": "prompt_shown",
                    "phase_id": phase["id"],
                    "prompt_id": prompt["prompt_id"],
                    "planned_at_sec": prompt["planned_at_sec"],
                    "actual_at_sec": round(actual_prompt, 6),
                    "error_sec": round(actual_prompt - float(prompt["planned_at_sec"]), 6),
                    "text": prompt["text"],
                    "text_sha256": prompt["text_sha256"],
                    "operator_action": prompt_operator_action(str(phase["kind"])),
                    "wall_time": utc_now(),
                }
                phase_events.append(prompt_row)
                append_event(run_events, prompt_row)

            if phase["kind"] == "keyboard_noise" and not phase_prompts:
                print(">>> ПЕЧАТАЙ НА КЛАВИАТУРЕ СЕЙЧАС; НЕ ГОВОРИ.", flush=True)
            wait_until(
                capture_started_mono + planned_end,
                child,
                monitor=monitor_output_volume,
            )
            actual_phase_boundary = time.monotonic() - capture_started_mono
            if active_player is not None:
                if active_player.poll() is None:
                    active_player.terminate()
                    active_player.wait(timeout=5)
                playback_finished = {
                    "schema": "murmurmark.controlled_echo_event/v1",
                    "type": "stimulus_finished",
                    "phase_id": phase["id"],
                    "stimulus_id": stimulus_name,
                    "actual_at_sec": round(time.monotonic() - capture_started_mono, 6),
                    "status": active_player.returncode,
                    "wall_time": utc_now(),
                }
                phase_events.append(playback_finished)
                append_event(run_events, playback_finished)
                active_player = None
            end_row = {
                "schema": "murmurmark.controlled_echo_event/v1",
                "type": "phase_finished",
                "phase_id": phase["id"],
                "planned_at_sec": planned_end,
                "actual_at_sec": round(actual_phase_boundary, 6),
                "error_sec": round(actual_phase_boundary - planned_end, 6),
                "wall_time": utc_now(),
            }
            phase_events.append(end_row)
            append_event(run_events, end_row)
        status = child.wait(timeout=45)
        if status != 0:
            raise RuntimeError(f"recording failed with status {status}")
    except BaseException as error:
        if active_player is not None and active_player.poll() is None:
            active_player.terminate()
        if child.poll() is None:
            child.send_signal(signal.SIGINT)
            try:
                child.wait(timeout=30)
            except subprocess.TimeoutExpired:
                child.terminate()
        abort_reason = str(error) or type(error).__name__
        abort_payload = {
            "schema": "murmurmark.controlled_echo_capture_abort/v1",
            "profile": policy["profile"],
            "session_id": session.name,
            "scenario": args.scenario,
            "phase_id": active_phase_id,
            "reason": abort_reason,
            "operator_instructions_confirmed": True,
            "operator_confirmation_mode": confirmation_mode,
            "output_volume_before_percent": volume_before,
            "output_volume_at_abort_percent": output_volume(),
            "aborted_at": utc_now(),
        }
        abort_path = session / "derived" / "echo-lab" / "capture_abort.json"
        try:
            write_json(abort_path, abort_payload)
            append_event(
                run_events,
                {
                    "schema": "murmurmark.controlled_echo_event/v1",
                    "type": "capture_aborted",
                    "phase_id": active_phase_id,
                    "reason": abort_reason,
                    "wall_time": abort_payload["aborted_at"],
                },
            )
            print(f"echo-lab abort report: {abort_path}", file=sys.stderr)
        except OSError:
            pass
        raise

    metadata_after = device_metadata()
    volume_after = metadata_after.get("output_volume_percent")
    derived = session / "derived" / "echo-lab"
    derived.mkdir(parents=True, exist_ok=True)
    write_jsonl(derived / "echo_lab_events.jsonl", phase_events)
    write_json(
        derived / "echo_lab_schedule.json",
        {
            "schema": SCHEMA_SCHEDULE,
            "profile": policy["profile"],
            "policy": relative(policy_path, args.repo_root.resolve()),
            "policy_sha256": policy_sha(policy_path),
            "scenario": args.scenario,
            "split": scenario["split"],
            "total_duration_sec": total_duration(policy),
            "post_roll_sec": float(policy["capture"]["post_roll_sec"]),
            "record_duration_sec": total_duration(policy)
            + float(policy["capture"]["post_roll_sec"]),
            "capture_started_at": capture_started_wall.isoformat().replace("+00:00", "Z")
            if capture_started_wall
            else None,
            "phases": schedule,
            "prompts": prompts,
            "operator_instructions_confirmed": True,
            "operator_confirmation_mode": confirmation_mode,
        },
    )
    raw_paths = [session / "audio" / role / "000001.caf" for role in ("mic", "remote")]
    missing_raw = [str(path) for path in raw_paths if not path.is_file()]
    if missing_raw:
        raise RuntimeError(f"recording finished without raw tracks: {missing_raw}")
    capture_manifest = {
        "schema": SCHEMA_CAPTURE,
        "profile": policy["profile"],
        "session_id": session.name,
        "session": relative(session, args.repo_root.resolve()),
        "scenario": args.scenario,
        "split": scenario["split"],
        "policy": relative(policy_path, args.repo_root.resolve()),
        "policy_sha256": policy_sha(policy_path),
        "stimuli_manifest": fingerprint(stimuli_manifest_path, sessions_root),
        "raw": [fingerprint(path, session) for path in raw_paths],
        "session_files": [
            fingerprint(path, session)
            for path in (session / "session.json", session / "events.jsonl", session / "pipeline_job.json")
            if path.is_file()
        ],
        "device_before": metadata_before,
        "device_after": metadata_after,
        "output_volume_before_percent": volume_before,
        "output_volume_after_percent": volume_after,
        "operator_instructions_confirmed": True,
        "operator_confirmation_mode": confirmation_mode,
        "phase_duration_sec": total_duration(policy),
        "post_roll_sec": float(policy["capture"]["post_roll_sec"]),
        "record_duration_sec": total_duration(policy)
        + float(policy["capture"]["post_roll_sec"]),
        "capture_mode": {
            "durable_raw_writer": True,
            "live_shadow": False,
            "second_capture": False,
            "target_bundle": "system",
        },
    }
    write_json(derived / "capture_manifest.json", capture_manifest)
    print(f"captured: {session}")
    print(f"manifest: {derived / 'capture_manifest.json'}")
    print(f"next: murmurmark echo-lab inspect {relative(session, args.repo_root.resolve())}")
    return 0


def transcribe_words(
    audio_path: Path,
    *,
    model_path: Path,
    cache_path: Path,
    source_sha256: str,
    model_instance: Any | None = None,
) -> dict[str, Any]:
    if cache_path.is_file():
        cached = read_json(cache_path)
        if cached.get("source_sha256") == source_sha256 and cached.get("model_path") == str(model_path):
            return cached
    model = model_instance or load_faster_whisper_model(model_path)
    segments, info = model.transcribe(
        str(audio_path),
        language="ru",
        beam_size=1,
        temperature=0.0,
        word_timestamps=True,
        vad_filter=False,
        condition_on_previous_text=False,
    )
    segment_rows: list[dict[str, Any]] = []
    word_rows: list[dict[str, Any]] = []
    for segment in segments:
        segment_row = {
            "start": round(float(segment.start), 6),
            "end": round(float(segment.end), 6),
            "text": str(segment.text).strip(),
            "avg_logprob": float(segment.avg_logprob),
            "no_speech_prob": float(segment.no_speech_prob),
        }
        segment_rows.append(segment_row)
        for word in segment.words or []:
            word_rows.append(
                {
                    "start": round(float(word.start), 6),
                    "end": round(float(word.end), 6),
                    "text": str(word.word).strip(),
                    "probability": float(word.probability),
                }
            )
    payload = {
        "schema": "murmurmark.controlled_echo_asr/v1",
        "source": str(audio_path),
        "source_sha256": source_sha256,
        "model_path": str(model_path),
        "language": getattr(info, "language", "ru"),
        "segments": segment_rows,
        "words": word_rows,
    }
    write_json(cache_path, payload)
    return payload


def materialize_double_talk_validation_audio(
    *,
    session: Path,
    mic_path: Path,
    remote_path: Path,
    phase: dict[str, Any],
    trim_sec: float,
    analysis_dir: Path,
) -> tuple[Path, dict[str, Any]]:
    helper_path = Path(__file__).resolve().parent / "echo-guard-session-local-fir.py"
    clean_phase_path = analysis_dir / "controlled_double_talk.mic_clean_local_fir.wav"
    report_path = analysis_dir / "controlled_double_talk.echo_clean_validation.json"
    source_identity = {
        "mic_sha256": sha256(mic_path),
        "remote_sha256": sha256(remote_path),
        "helper_sha256": sha256(helper_path),
    }
    if report_path.is_file() and clean_phase_path.is_file():
        cached = read_json(report_path)
        clean_artifact = cached.get("clean_phase") or {}
        if (
            cached.get("source_identity") == source_identity
            and clean_artifact.get("sha256") == sha256(clean_phase_path)
        ):
            return clean_phase_path, cached

    analysis_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="double-talk-fir-", dir=analysis_dir) as temporary:
        work = Path(temporary)
        helper_report_path = work / "local_fir_report.json"
        result = subprocess.run(
            [
                sys.executable,
                str(helper_path),
                str(session),
                "--input-mic",
                str(mic_path),
                "--input-remote",
                str(remote_path),
                "--profile",
                "conservative",
                "--role-policy",
                "preserve_local",
                "--output-clean",
                str(work / "mic_clean.wav"),
                "--output-echo",
                str(work / "echo_hat.wav"),
                "--output-role-mask",
                str(work / "mic_role_masked.wav"),
                "--output-role-preview",
                str(work / "mic_role_preview.wav"),
                "--asr-segments-dir",
                str(work / "mic_asr_segments"),
                "--report",
                str(helper_report_path),
                "--segments",
                str(work / "segments.jsonl"),
                "--speaker-state",
                str(work / "speaker_state.jsonl"),
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        if result.returncode != 0 or not helper_report_path.is_file():
            detail = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else "no report"
            raise RuntimeError(f"double-talk echo-clean validator failed: {detail}")
        helper_report = read_json(helper_report_path)
        decision = helper_report.get("decision") or {}
        if not decision.get("accepted_for_asr"):
            failures = decision.get("quality_gate_failures") or [decision.get("reason") or "rejected"]
            raise RuntimeError(
                "double-talk echo-clean validator rejected the candidate: "
                + ",".join(str(value) for value in failures)
            )
        start, end = phase_bounds(phase, trim_sec)
        clean_phase = read_audio_slice(work / "mic_clean.wav", start, end)
        write_audio(clean_phase_path, clean_phase)
        payload = {
            "schema": "murmurmark.controlled_echo_double_talk_validation/v1",
            "engine": "local_fir_preserve_local",
            "source_identity": source_identity,
            "analysis_start_sec": round(start, 6),
            "analysis_end_sec": round(end, 6),
            "helper_decision": decision,
            "helper_metrics": helper_report.get("metrics") or {},
            "helper_parameters": helper_report.get("parameters") or {},
            "clean_phase": fingerprint(clean_phase_path, session),
        }
        write_json(report_path, payload)
    return clean_phase_path, payload


def asr_cache_matches(cache_path: Path, source_sha256: str, model_path: Path) -> bool:
    if not cache_path.is_file():
        return False
    cached = read_json(cache_path)
    return (
        cached.get("source_sha256") == source_sha256
        and cached.get("model_path") == str(model_path)
    )


def load_faster_whisper_model(model_path: Path) -> Any:
    if not model_path.is_dir():
        raise RuntimeError(f"faster-whisper model not found: {model_path}")
    try:
        from faster_whisper import WhisperModel
    except ImportError as error:
        raise RuntimeError("faster-whisper is not installed") from error
    return WhisperModel(
        str(model_path),
        device="cpu",
        compute_type="int8",
        local_files_only=True,
    )


def interval_text(asr: dict[str, Any], start: float, end: float) -> str:
    words = [
        str(row.get("text") or "")
        for row in asr.get("words", [])
        if float(row.get("end") or 0.0) > start and float(row.get("start") or 0.0) < end
    ]
    if words:
        return " ".join(words)
    return " ".join(
        str(row.get("text") or "")
        for row in asr.get("segments", [])
        if float(row.get("end") or 0.0) > start and float(row.get("start") or 0.0) < end
    )


def interval_word_spans(asr: dict[str, Any], start: float, end: float) -> list[dict[str, float]]:
    spans: list[dict[str, float]] = []
    for row in asr.get("words", []):
        word_start = float(row.get("start") or 0.0)
        word_end = float(row.get("end") or 0.0)
        if word_end <= start or word_start >= end:
            continue
        spans.append(
            {
                "start_sec": round(max(start, word_start) - start, 6),
                "end_sec": round(min(end, word_end) - start, 6),
            }
        )
    return spans


def cosine(left: np.ndarray, right: np.ndarray) -> float:
    denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
    if denominator <= 1.0e-12:
        return 0.0
    return float(np.dot(left, right) / denominator)


def chunk_embeddings(
    audio: np.ndarray,
    encoder: Any,
    preprocess_wav: Any,
    *,
    chunk_sec: float = 4.0,
) -> list[np.ndarray]:
    chunk = int(round(chunk_sec * ANALYSIS_SAMPLE_RATE))
    result: list[np.ndarray] = []
    for start in range(0, max(0, audio.size - chunk + 1), chunk):
        piece = np.asarray(audio[start : start + chunk], dtype=np.float32)
        if audio_rms_db(piece) < -50.0:
            continue
        try:
            prepared = preprocess_wav(piece, source_sr=ANALYSIS_SAMPLE_RATE)
            if prepared.size < ANALYSIS_SAMPLE_RATE // 2:
                continue
            embedding = encoder.embed_utterance(prepared)
        except (ValueError, RuntimeError):
            continue
        if np.all(np.isfinite(embedding)):
            result.append(np.asarray(embedding, dtype=np.float32))
    return result


def audio_rms_db(audio: np.ndarray) -> float:
    if not audio.size:
        return -240.0
    value = np.sqrt(float(np.mean(np.asarray(audio, dtype=np.float64) ** 2)) + 1.0e-24)
    return 20.0 * np.log10(value + 1.0e-12)


def speaker_validation(
    *,
    mic_path: Path,
    remote_path: Path,
    phases: Sequence[dict[str, Any]],
    trim_sec: float,
    policy: dict[str, Any],
) -> dict[str, Any]:
    try:
        from resemblyzer import VoiceEncoder, preprocess_wav
    except ImportError as error:
        raise RuntimeError("resemblyzer is not installed") from error
    encoder = VoiceEncoder(verbose=False)
    by_id = {str(row["id"]): row for row in phases}
    local_start, local_end = phase_bounds(by_id["local_only"], trim_sec)
    remote_start, remote_end = phase_bounds(by_id["remote_only"], trim_sec)
    local_audio = read_audio_slice(mic_path, local_start, local_end)
    remote_mic_audio = read_audio_slice(mic_path, remote_start, remote_end)
    remote_ref_audio = read_audio_slice(remote_path, remote_start, remote_end)
    local_embeddings = chunk_embeddings(local_audio, encoder, preprocess_wav)
    remote_mic_embeddings = chunk_embeddings(remote_mic_audio, encoder, preprocess_wav)
    remote_ref_embeddings = chunk_embeddings(remote_ref_audio, encoder, preprocess_wav)
    if len(local_embeddings) < 8:
        raise RuntimeError(f"insufficient local speaker chunks: {len(local_embeddings)}")
    enrollment = np.mean(np.stack(local_embeddings[:3]), axis=0)
    local_scores = [cosine(enrollment, row) for row in local_embeddings[3:]]
    remote_ref_centroid = (
        np.mean(np.stack(remote_ref_embeddings), axis=0) if remote_ref_embeddings else None
    )
    remote_rows: list[dict[str, Any]] = []
    for embedding in remote_mic_embeddings:
        local_similarity = cosine(enrollment, embedding)
        remote_similarity = cosine(remote_ref_centroid, embedding) if remote_ref_centroid is not None else 0.0
        remote_rows.append(
            {
                "local_similarity": round(local_similarity, 6),
                "remote_similarity": round(remote_similarity, 6),
                "local_margin": round(local_similarity - remote_similarity, 6),
            }
        )
    target_policy = policy["validation"]["target_me"]
    contamination = any(
        row["local_similarity"] >= float(target_policy["remote_contamination_local_similarity"])
        and row["local_margin"] >= float(target_policy["remote_contamination_margin"])
        for row in remote_rows
    )
    phase_scores: dict[str, Any] = {}
    for phase_id in ("controlled_double_talk", "opening_backchannel"):
        start, end = phase_bounds(by_id[phase_id], trim_sec)
        chunk_sec = speaker_validation_chunk_sec(phase_id)
        embeddings = chunk_embeddings(
            read_audio_slice(mic_path, start, end),
            encoder,
            preprocess_wav,
            chunk_sec=chunk_sec,
        )
        scores = [cosine(enrollment, row) for row in embeddings]
        phase_scores[phase_id] = {
            "chunk_duration_sec": chunk_sec,
            "chunk_count": len(scores),
            "maximum_similarity": round(max(scores), 6) if scores else 0.0,
            "median_similarity": round(float(np.median(scores)), 6) if scores else 0.0,
        }
    return {
        "backend": "resemblyzer_dvector_v0",
        "enrollment_chunks": 3,
        "local_validation_chunks": len(local_scores),
        "local_similarity_median": round(float(np.median(local_scores)), 6) if local_scores else 0.0,
        "local_similarity_minimum": round(min(local_scores), 6) if local_scores else 0.0,
        "remote_mic_chunks": len(remote_rows),
        "remote_contamination": contamination,
        "remote_rows": remote_rows,
        "phase_scores": phase_scores,
    }


def inspect(args: argparse.Namespace) -> int:
    policy_path = args.policy.resolve()
    policy = load_policy(policy_path)
    session = args.session.resolve()
    derived = session / "derived" / "echo-lab"
    capture_manifest_path = derived / "capture_manifest.json"
    schedule_path = derived / "echo_lab_schedule.json"
    if not capture_manifest_path.is_file() or not schedule_path.is_file():
        abort_path = derived / "capture_abort.json"
        if abort_path.is_file():
            abort = read_json(abort_path)
            raise RuntimeError(
                f"echo-lab capture was aborted during {abort.get('phase_id', 'unknown')}: "
                f"{abort.get('reason', 'unknown reason')}; rerun with a fresh SESSION"
            )
        raise RuntimeError(f"not an echo-lab capture: {session}")
    capture_manifest = read_json(capture_manifest_path)
    schedule_payload = read_json(schedule_path)
    if capture_manifest.get("policy_sha256") != policy_sha(policy_path):
        raise RuntimeError("capture policy hash differs from the current frozen policy")
    schedule = schedule_payload.get("phases", [])
    validate_schedule(schedule, total_duration(policy))

    raw_errors: list[str] = []
    for row in capture_manifest.get("raw", []) + capture_manifest.get("session_files", []):
        path = session / str(row["path"])
        if not path.is_file():
            raw_errors.append(f"missing:{row['path']}")
        elif sha256(path) != row.get("sha256"):
            raw_errors.append(f"sha256:{row['path']}")

    analysis_dir = derived / "analysis"
    mic_wav = analysis_dir / "mic.wav"
    remote_wav = analysis_dir / "remote.wav"
    convert_audio(session / "audio" / "mic" / "000001.caf", mic_wav)
    convert_audio(session / "audio" / "remote" / "000001.caf", remote_wav)
    mic_audio = read_audio(mic_wav)
    remote_audio = read_audio(remote_wav)
    track_duration = min(mic_audio.samples.size, remote_audio.samples.size) / ANALYSIS_SAMPLE_RATE

    validation = policy["validation"]
    events = read_jsonl(derived / "echo_lab_events.jsonl")
    phase_start_errors = {
        str(row["phase_id"]): abs(float(row.get("error_sec") or 0.0))
        for row in events
        if row.get("type") == "phase_started"
    }
    phase_end_errors = {
        str(row["phase_id"]): abs(float(row.get("error_sec") or 0.0))
        for row in events
        if row.get("type") == "phase_finished"
    }
    phase_prompt_errors: dict[str, float] = {}
    for row in events:
        if row.get("type") != "prompt_shown":
            continue
        phase_id = str(row["phase_id"])
        phase_prompt_errors[phase_id] = max(
            phase_prompt_errors.get(phase_id, 0.0),
            abs(float(row.get("error_sec") or 0.0)),
        )
    capture_events = read_jsonl(session / "events.jsonl")
    stop_rows = [row for row in capture_events if row.get("type") == "capture.stopped"]
    partial = not stop_rows or bool(stop_rows[-1].get("partial"))
    global_reasons: list[str] = []
    if raw_errors:
        global_reasons.append("raw_fingerprint_mismatch")
    if partial:
        global_reasons.append("partial_capture")
    if track_duration / total_duration(policy) < float(validation["minimum_track_coverage_ratio"]):
        global_reasons.append("insufficient_track_coverage")
    volume_before = capture_manifest.get("output_volume_before_percent")
    volume_after = capture_manifest.get("output_volume_after_percent")
    if volume_before is None or volume_after is None:
        global_reasons.append("output_volume_unavailable")
    elif abs(int(volume_after) - int(volume_before)) > int(validation["maximum_output_volume_drift_percent"]):
        global_reasons.append("output_volume_changed")

    validators_error: str | None = None
    double_talk_clean_error: str | None = None
    mic_asr: dict[str, Any] = {}
    remote_asr: dict[str, Any] = {}
    double_talk_clean_asr: dict[str, Any] = {}
    double_talk_clean_path: Path | None = None
    double_talk_clean_report: dict[str, Any] = {}
    speaker: dict[str, Any] = {}
    double_talk_phase = next(
        (row for row in schedule if row.get("kind") == "controlled_double_talk"),
        None,
    )
    if double_talk_phase is not None and not raw_errors:
        try:
            double_talk_clean_path, double_talk_clean_report = materialize_double_talk_validation_audio(
                session=session,
                mic_path=mic_wav,
                remote_path=remote_wav,
                phase=double_talk_phase,
                trim_sec=float(policy["capture"]["phase_trim_sec"]),
                analysis_dir=analysis_dir,
            )
        except RuntimeError as error:
            double_talk_clean_error = str(error)
    try:
        model_path = args.model.resolve()
        mic_source_sha = sha256(mic_wav)
        remote_source_sha = sha256(remote_wav)
        mic_cache = analysis_dir / "mic.faster_whisper.json"
        remote_cache = analysis_dir / "remote.faster_whisper.json"
        double_talk_clean_cache = analysis_dir / "controlled_double_talk.mic_clean_local_fir.faster_whisper.json"
        double_talk_clean_sha = sha256(double_talk_clean_path) if double_talk_clean_path else None
        shared_model = None
        if not (
            asr_cache_matches(mic_cache, mic_source_sha, model_path)
            and asr_cache_matches(remote_cache, remote_source_sha, model_path)
            and (
                double_talk_clean_path is None
                or (
                    double_talk_clean_sha is not None
                    and asr_cache_matches(double_talk_clean_cache, double_talk_clean_sha, model_path)
                )
            )
        ):
            shared_model = load_faster_whisper_model(model_path)
        mic_asr = transcribe_words(
            mic_wav,
            model_path=model_path,
            cache_path=mic_cache,
            source_sha256=mic_source_sha,
            model_instance=shared_model,
        )
        remote_asr = transcribe_words(
            remote_wav,
            model_path=model_path,
            cache_path=remote_cache,
            source_sha256=remote_source_sha,
            model_instance=shared_model,
        )
        if double_talk_clean_path is not None and double_talk_clean_sha is not None:
            double_talk_clean_asr = transcribe_words(
                double_talk_clean_path,
                model_path=model_path,
                cache_path=double_talk_clean_cache,
                source_sha256=double_talk_clean_sha,
                model_instance=shared_model,
            )
        speaker = speaker_validation(
            mic_path=mic_wav,
            remote_path=remote_wav,
            phases=schedule,
            trim_sec=float(policy["capture"]["phase_trim_sec"]),
            policy=policy,
        )
    except RuntimeError as error:
        validators_error = str(error)

    stimulus_fingerprint = capture_manifest.get("stimuli_manifest")
    if not isinstance(stimulus_fingerprint, dict) or not stimulus_fingerprint.get("path"):
        raise RuntimeError("capture manifest lacks the stimulus fingerprint")
    stimuli_manifest_path = args.sessions_root.resolve() / str(stimulus_fingerprint["path"])
    if not stimuli_manifest_path.is_file():
        raise RuntimeError(f"captured stimulus manifest is missing: {stimuli_manifest_path}")
    if sha256(stimuli_manifest_path) != stimulus_fingerprint.get("sha256"):
        global_reasons.append("stimulus_manifest_sha256_mismatch")
    stimuli_manifest = read_json(stimuli_manifest_path)
    expected_stimulus = {
        str(row["id"]): str(row.get("expected_text") or "")
        for row in stimuli_manifest.get("stimuli", [])
    }
    trim = float(policy["capture"]["phase_trim_sec"])
    phase_rows: list[dict[str, Any]] = []
    phase_audio_dir = derived / "phases"
    maximum_lag_ms = float(validation["maximum_echo_lag_ms"])
    for phase in schedule:
        start, end = phase_bounds(phase, trim)
        mic = read_audio_slice(mic_wav, start, end)
        remote = read_audio_slice(remote_wav, start, end)
        metrics = audio_metrics(mic, remote, ANALYSIS_SAMPLE_RATE, maximum_lag_ms)
        reasons = list(global_reasons)
        phase_id = str(phase["id"])
        maximum_phase_error = float(validation["maximum_phase_start_error_sec"])
        if phase_start_errors.get(phase_id, 999.0) > maximum_phase_error:
            reasons.append("phase_start_error")
        if phase_end_errors.get(phase_id, 999.0) > maximum_phase_error:
            reasons.append("phase_end_error")
        if prompts_for_phase(phase) and phase_prompt_errors.get(phase_id, 999.0) > maximum_phase_error:
            reasons.append("prompt_timing_error")
        if not metrics["finite"]:
            reasons.append("non_finite")
        if metrics["mic_peak"] > float(validation["maximum_abs_peak"]) or metrics["remote_peak"] > float(
            validation["maximum_abs_peak"]
        ):
            reasons.append("peak_over_limit")
        if metrics["mic_clipped_ratio"] > float(validation["maximum_clipped_sample_ratio"]) or metrics[
            "remote_clipped_ratio"
        ] > float(validation["maximum_clipped_sample_ratio"]):
            reasons.append("clipping")
        if validators_error and phase["kind"] in {
            "remote_only",
            "local_only",
            "controlled_double_talk",
            "opening_backchannel",
        }:
            reasons.append("required_validator_unavailable")

        mic_text = interval_text(mic_asr, start, end) if mic_asr else ""
        remote_text = interval_text(remote_asr, start, end) if remote_asr else ""
        phase_extra_artifacts: dict[str, Any] = {}
        evidence: dict[str, Any] = {
            "mic_text_sha256": sha256_text(mic_text),
            "mic_text_token_count": len(tokens(mic_text)),
            "remote_text_sha256": sha256_text(remote_text),
            "remote_text_token_count": len(tokens(remote_text)),
        }
        kind = str(phase["kind"])
        if kind == "remote_only":
            stimulus_text_value = expected_stimulus.get(str(phase.get("stimulus")), "")
            evidence.update(
                {
                    "remote_stimulus_token_recall": round(token_recall(stimulus_text_value, remote_text), 6),
                    "remote_stimulus_unique_token_ratio": round(
                        unique_token_ratio(remote_text, stimulus_text_value), 6
                    ),
                    "mic_unique_token_ratio_against_remote": round(
                        unique_token_ratio(mic_text, remote_text), 6
                    ),
                    "target_me_remote_contamination": speaker.get("remote_contamination"),
                }
            )
            reasons.extend(remote_only_gate_reasons(metrics, evidence, validation))
        elif kind == "local_only":
            expected = expected_prompt_text(phase)
            evidence.update(
                {
                    "prompt_token_recall": round(token_recall(expected, mic_text), 6),
                    "target_me_local_similarity": speaker.get("local_similarity_median"),
                    "target_me_validation_chunks": speaker.get("local_validation_chunks"),
                    "mic_word_intervals": interval_word_spans(mic_asr, start, end) if mic_asr else [],
                }
            )
            reasons.extend(local_only_gate_reasons(metrics, evidence, validation))
        elif kind in {"controlled_double_talk", "opening_backchannel"}:
            expected = expected_prompt_text(phase)
            score = speaker.get("phase_scores", {}).get(str(phase["id"]), {})
            threshold_name = (
                "double_talk_prompt_min_token_recall"
                if kind == "controlled_double_talk"
                else "opening_prompt_min_token_recall"
            )
            prompt_text = mic_text
            prompt_asr = mic_asr
            prompt_asr_origin = 0.0
            prompt_source = "raw_mic"
            raw_prompt_recall = token_recall(expected, mic_text)
            echo_clean_prompt_recall: float | None = None
            if kind == "controlled_double_talk" and double_talk_clean_asr:
                echo_clean_text = interval_text(double_talk_clean_asr, 0.0, end - start)
                echo_clean_prompt_recall = token_recall(expected, echo_clean_text)
                evidence.update(
                    {
                        "echo_clean_text_sha256": sha256_text(echo_clean_text),
                        "echo_clean_text_token_count": len(tokens(echo_clean_text)),
                        "echo_clean_prompt_token_recall": round(echo_clean_prompt_recall, 6),
                    }
                )
                if echo_clean_prompt_recall >= raw_prompt_recall:
                    prompt_text = echo_clean_text
                    prompt_asr = double_talk_clean_asr
                    prompt_asr_origin = start
                    prompt_source = "echo_clean_local_fir"
                if double_talk_clean_path is not None:
                    phase_extra_artifacts["echo_clean_validation"] = fingerprint(
                        double_talk_clean_path,
                        session,
                    )
            if kind == "controlled_double_talk":
                forbidden = expected_stimulus.get(str(phase.get("stimulus")), "")
                prompt_support, prompt_support_evidence = prompt_supported_word_spans(
                    words=prompt_asr.get("words", []),
                    phase=phase,
                    analysis_start_sec=start,
                    analysis_end_sec=end,
                    asr_origin_sec=prompt_asr_origin,
                    forbidden_text=forbidden,
                    minimum_token_recall=float(validation[threshold_name]),
                )
            else:
                prompt_support = interval_word_spans(mic_asr, start, end) if mic_asr else []
                prompt_support_evidence = []
            evidence.update(
                {
                    "prompt_token_recall": round(token_recall(expected, prompt_text), 6),
                    "raw_prompt_token_recall": round(raw_prompt_recall, 6),
                    "prompt_validation_source": prompt_source,
                    "target_me_maximum_similarity": score.get("maximum_similarity"),
                    "target_me_chunk_count": score.get("chunk_count"),
                    "mic_word_intervals": prompt_support,
                }
            )
            if prompt_support_evidence:
                evidence["prompt_support"] = prompt_support_evidence
            if metrics["remote_rms_db"] < float(validation["remote_active_min_rms_db"]) and kind == "controlled_double_talk":
                reasons.append("double_talk_remote_inactive")
            if metrics["mic_rms_db"] < float(validation["local_speech_min_rms_db"]):
                reasons.append("local_speech_too_quiet")
            if evidence["prompt_token_recall"] < float(validation[threshold_name]):
                reasons.append("prompt_recall")
                if kind == "controlled_double_talk" and double_talk_clean_error:
                    reasons.append("double_talk_echo_clean_validator_unavailable")
            if (evidence["target_me_maximum_similarity"] or 0.0) < float(
                validation["target_me"]["minimum_local_similarity"]
            ):
                reasons.append("target_me_not_confirmed")
            if kind == "opening_backchannel" and metrics["remote_rms_db"] > float(
                validation["remote_silent_max_rms_db"]
            ):
                reasons.append("opening_remote_not_silent")
        elif kind in {"silence_background", "keyboard_noise"}:
            if metrics["remote_rms_db"] > float(validation["remote_silent_max_rms_db"]):
                reasons.append("negative_control_remote_not_silent")
        elif kind == "guard":
            reasons.append("guard_phase")

        mic_phase_path = phase_audio_dir / f"{int(phase['index']):02d}_{phase['id']}_mic.wav"
        remote_phase_path = phase_audio_dir / f"{int(phase['index']):02d}_{phase['id']}_remote.wav"
        write_audio(mic_phase_path, mic)
        write_audio(remote_phase_path, remote)
        aligned_path: Path | None = None
        if kind == "remote_only":
            aligned_path = phase_audio_dir / f"{int(phase['index']):02d}_{phase['id']}_remote_aligned.wav"
            write_audio(
                aligned_path,
                shift_remote_for_mic(remote, float(metrics["lag_ms"]), ANALYSIS_SAMPLE_RATE),
            )
        accepted = not reasons
        phase_rows.append(
            {
                "schema": "murmurmark.controlled_echo_phase_inspection/v1",
                "phase_id": phase["id"],
                "kind": kind,
                "planned_start_sec": phase["planned_start_sec"],
                "planned_end_sec": phase["planned_end_sec"],
                "analysis_start_sec": round(start, 6),
                "analysis_end_sec": round(end, 6),
                "accepted": accepted,
                "outcome": "included" if accepted else "excluded",
                "reasons": sorted(set(reasons)),
                "metrics": metrics,
                "evidence": evidence,
                "artifacts": {
                    "mic": fingerprint(mic_phase_path, session),
                    "remote": fingerprint(remote_phase_path, session),
                    **phase_extra_artifacts,
                    **(
                        {"aligned_remote": fingerprint(aligned_path, session)}
                        if aligned_path is not None
                        else {}
                    ),
                },
            }
        )

    required_kinds = {
        "silence_background",
        "remote_only",
        "local_only",
        "keyboard_noise",
        "controlled_double_talk",
        "opening_backchannel",
    }
    accepted_kinds = {row["kind"] for row in phase_rows if row["accepted"]}
    accepted = not global_reasons and required_kinds.issubset(accepted_kinds)
    inspection = {
        "schema": SCHEMA_INSPECTION,
        "profile": policy["profile"],
        "session_id": session.name,
        "scenario": capture_manifest["scenario"],
        "split": capture_manifest["split"],
        "policy_sha256": policy_sha(policy_path),
        "accepted": accepted,
        "outcome": "accepted" if accepted else "excluded",
        "reasons": sorted(
            set(
                global_reasons
                + [
                    f"phase:{row['phase_id']}"
                    for row in phase_rows
                    if not row["accepted"] and row["kind"] in required_kinds
                ]
            )
        ),
        "track_duration_sec": round(track_duration, 6),
        "track_coverage_ratio": round(track_duration / total_duration(policy), 9),
        "required_validators": {
            "faster_whisper": bool(mic_asr and remote_asr),
            "target_me": bool(speaker),
            "double_talk_echo_clean": bool(double_talk_clean_asr),
            "double_talk_echo_clean_error": double_talk_clean_error,
            "error": validators_error,
        },
        "analysis_profile": {
            "sample_rate": ANALYSIS_SAMPLE_RATE,
            "mono_channel_policy": "first_input_channel_no_gain_v1",
            "double_talk_prompt_validation": "best_of_raw_and_local_fir_clean_v1",
            "double_talk_local_support": "prompt_discriminative_words_v1",
            "speaker_validation_chunk_sec": {
                phase_id: speaker_validation_chunk_sec(phase_id)
                for phase_id in ("controlled_double_talk", "opening_backchannel")
            },
        },
        "speaker_validation": speaker,
        "raw_fingerprint_errors": raw_errors,
        "phases": phase_rows,
        "artifacts": {
            "capture_manifest": fingerprint(capture_manifest_path, session),
            "schedule": fingerprint(schedule_path, session),
            "mic_analysis": fingerprint(mic_wav, session),
            "remote_analysis": fingerprint(remote_wav, session),
            **(
                {
                    "double_talk_echo_clean_report": fingerprint(
                        analysis_dir / "controlled_double_talk.echo_clean_validation.json",
                        session,
                    )
                }
                if double_talk_clean_report
                else {}
            ),
        },
    }
    write_json(derived / "inspection.json", inspection)
    write_jsonl(derived / "phase_inventory.jsonl", phase_rows)
    print(f"session: {session}")
    print(f"outcome: {inspection['outcome']}")
    print(f"accepted phases: {sum(1 for row in phase_rows if row['accepted'])}/{len(phase_rows)}")
    if inspection["reasons"]:
        print("reasons:")
        for reason in inspection["reasons"]:
            print(f"  - {reason}")
    print(f"inspection: {derived / 'inspection.json'}")
    print("next: murmurmark corpus echo-supervision build")
    return 0 if accepted else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help=argparse.SUPPRESS,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare_parser = subparsers.add_parser("prepare", help="Generate and fingerprint local TTS stimuli.")
    prepare_parser.add_argument("--policy", type=Path, default=default_policy_path())
    prepare_parser.add_argument("--sessions-root", type=Path, default=default_sessions_root())

    capture_parser = subparsers.add_parser("capture", help="Run one controlled durable capture.")
    capture_parser.add_argument("--out", type=Path, required=True)
    capture_parser.add_argument("--scenario", required=True)
    capture_parser.add_argument("--policy", type=Path, default=default_policy_path())
    capture_parser.add_argument("--sessions-root", type=Path, default=default_sessions_root())
    capture_parser.add_argument(
        "--confirm-operator-instructions",
        action="store_true",
        help=(
            "Confirm that the operator has read the prompts and will speak aloud when instructed; "
            "intended for deliberate non-interactive runs."
        ),
    )
    capture_parser.add_argument("--preflight-only", action="store_true", help=argparse.SUPPRESS)
    capture_parser.add_argument(
        "--operator-confirmation-mode",
        choices=("flag", "interactive_cli"),
        default="flag",
        help=argparse.SUPPRESS,
    )
    capture_parser.add_argument(
        "--murmurmark-executable",
        type=Path,
        default=Path(os.environ.get("MURMURMARK_BIN", ".build/debug/murmurmark")),
        help=argparse.SUPPRESS,
    )

    inspect_parser = subparsers.add_parser("inspect", help="Validate and materialize one lab capture.")
    inspect_parser.add_argument("session", type=Path)
    inspect_parser.add_argument("--policy", type=Path, default=default_policy_path())
    inspect_parser.add_argument("--sessions-root", type=Path, default=default_sessions_root())
    inspect_parser.add_argument("--model", type=Path, default=default_model_path())
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            return prepare(args)
        if args.command == "capture":
            return capture(args)
        if args.command == "inspect":
            return inspect(args)
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    parser.error(f"unsupported command: {args.command}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
