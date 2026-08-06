#!/usr/bin/env python3
"""Audit and prove causal candidates for canonical microphone ASR chunks.

The current production microphone is selected after Echo Guard and, for some
sessions, Speaker-Preserving Neural Echo. This tool is deliberately isolated:
it replays the only causal candidate (raw microphone fallback), compares every
canonical 60s/5s window with the finalized batch input, and publishes an
authoritative proof only for byte-identical windows. A mismatch is a normal
batch fallback, never an approximation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import authoritative_asr_cache as cache
from murmurmark_resource_policy import apply_resource_policy, resolve_resource_policy


SCRIPT_VERSION = "0.1.0"
REPORT_SCHEMA = "murmurmark.causal_canonical_mic_asr_report/v1"
LINEAGE_SCHEMA = "murmurmark.causal_mic_lineage/v1"
WINDOW_SCHEMA = "murmurmark.causal_canonical_mic_window/v1"
PROOF_SCHEMA = "murmurmark.authoritative_live_asr_chunk/v1"
DEFAULT_MODEL = Path.home() / ".local/share/murmurmark/models/whisper.cpp/ggml-large-v3-q5_0.bin"
MIC_FILTER = "highpass=f=100,lowpass=f=7600,alimiter=limit=0.98"
CANONICAL_RATE = 16_000


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare causal raw-mic checkpoints with the finalized canonical mic ASR input."
    )
    parser.add_argument("session", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--whisper-cli", default=os.environ.get("WHISPER_CLI", "whisper-cli"))
    parser.add_argument("--language", default="ru")
    parser.add_argument("--prompt-file", type=Path)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--max-context", type=int, default=0)
    parser.add_argument("--window-sec", type=int, default=60)
    parser.add_argument("--overlap-sec", type=int, default=5)
    parser.add_argument("--decode-exact", action="store_true")
    parser.add_argument("--decode-timeout-sec", type=float, default=900.0)
    parser.add_argument("--max-decode-chunks", type=int, default=80)
    parser.add_argument("--interrupt-after-chunks", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument(
        "--prefix-probe",
        action="store_true",
        help="Replay the first local-FIR window with bounded future contexts.",
    )
    parser.add_argument("--prefix-lookahead-sec", default="5,30,120")
    parser.add_argument("--resource-profile", choices=("background", "opportunistic", "performance"), default="background")
    parser.add_argument("--max-compute-threads", type=int, default=4)
    args = parser.parse_args()
    if args.window_sec <= 0 or args.overlap_sec < 0 or args.overlap_sec >= args.window_sec / 2:
        parser.error("invalid window/overlap geometry")
    if args.max_decode_chunks < 0 or args.interrupt_after_chunks < 0:
        parser.error("chunk limits must be non-negative")
    try:
        args.prefix_lookaheads = sorted(
            {int(value.strip()) for value in args.prefix_lookahead_sec.split(",") if value.strip()}
        )
    except ValueError as error:
        parser.error(f"invalid --prefix-lookahead-sec: {error}")
    if any(value < args.overlap_sec for value in args.prefix_lookaheads):
        parser.error("prefix lookahead must cover canonical overlap")
    return args


def read_json(path: Path) -> dict[str, Any]:
    return cache.read_json(path) or {}


def relative(path: Path, session: Path) -> str:
    try:
        return str(path.resolve().relative_to(session.resolve()))
    except ValueError:
        return str(path.resolve())


def artifact(path: Path, session: Path | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"exists": path.is_file()}
    if session is not None:
        result["path"] = relative(path, session)
    else:
        result["path"] = str(path)
    if path.is_file():
        result.update(cache.file_fingerprint(path, include_path=False))
    return result


def atomic_write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    content = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
    cache.atomic_write_bytes(path, content.encode("utf-8"))


def run(command: list[str], *, timeout: float | None = None, log: Path | None = None) -> float:
    started = time.monotonic()
    completed = subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE if log is not None else subprocess.DEVNULL,
        stderr=subprocess.STDOUT if log is not None else subprocess.PIPE,
        text=True,
        timeout=timeout,
        check=False,
    )
    if log is not None:
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(completed.stdout or "", encoding="utf-8")
    if completed.returncode != 0:
        detail = (completed.stdout if log is not None else completed.stderr) or ""
        raise RuntimeError(f"command_failed:{completed.returncode}:{Path(command[0]).name}:{detail[-500:]}")
    return time.monotonic() - started


def ffmpeg_export(source: Path, destination: Path, *, duration_sec: int | None = None) -> float:
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = [
        shutil.which("ffmpeg") or "ffmpeg",
        "-nostdin",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
    ]
    if duration_sec is not None:
        command += ["-t", str(duration_sec)]
    command += [
        "-i",
        str(source),
        "-map",
        "0:a:0",
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(CANONICAL_RATE),
        str(destination),
    ]
    return run(command)


def ffmpeg_trim_working_audio(source: Path, destination: Path, duration_sec: int) -> float:
    """Trim prepared float audio without changing production helper input geometry."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    return run(
        [
            shutil.which("ffmpeg") or "ffmpeg",
            "-nostdin",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-t",
            str(duration_sec),
            "-i",
            str(source),
            "-map",
            "0:a:0",
            "-vn",
            "-c:a",
            "pcm_f32le",
            str(destination),
        ]
    )


def ffmpeg_prepare(source: Path, destination: Path) -> float:
    destination.parent.mkdir(parents=True, exist_ok=True)
    return run(
        [
            shutil.which("ffmpeg") or "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-af",
            MIC_FILTER,
            "-ar",
            str(CANONICAL_RATE),
            "-ac",
            "1",
            str(destination),
        ]
    )


def chunk_specs(frames: int, window_sec: int, overlap_sec: int) -> list[dict[str, int]]:
    window = window_sec * CANONICAL_RATE
    overlap = overlap_sec * CANONICAL_RATE
    specs: list[dict[str, int]] = []
    hard_start = 0
    index = 1
    while hard_start < frames:
        hard_end = min(frames, hard_start + window)
        clip_start = max(0, hard_start - overlap)
        clip_end = min(frames, hard_end + overlap)
        specs.append(
            {
                "index": index,
                "hard_start_sample": hard_start,
                "hard_end_sample": hard_end,
                "seek_sample": clip_start,
                "clip_end_sample": clip_end,
                "hard_start_ms": round(hard_start * 1000 / CANONICAL_RATE),
                "hard_end_ms": round(hard_end * 1000 / CANONICAL_RATE),
                "seek_ms": round(clip_start * 1000 / CANONICAL_RATE),
                "clip_end_ms": round(clip_end * 1000 / CANONICAL_RATE),
                "clip_duration_ms": round((clip_end - clip_start) * 1000 / CANONICAL_RATE),
                "sample_rate": CANONICAL_RATE,
            }
        )
        hard_start += window
        index += 1
    return specs


def slice_fingerprint(path: Path, start: int, end: int) -> dict[str, Any]:
    digest = hashlib.sha256()
    with wave.open(str(path), "rb") as audio:
        if audio.getcomptype() != "NONE":
            raise ValueError(f"compressed WAV is not canonical PCM: {path}")
        available = audio.getnframes()
        bounded_end = min(end, available)
        if start < 0 or start >= bounded_end:
            raise ValueError(f"invalid PCM slice {start}..{end} for {available}")
        audio.setpos(start)
        remaining = bounded_end - start
        while remaining:
            count = min(65536, remaining)
            payload = audio.readframes(count)
            if not payload:
                break
            digest.update(payload)
            remaining -= len(payload) // (audio.getnchannels() * audio.getsampwidth())
        frames = bounded_end - start - remaining
        return {
            "sha256": digest.hexdigest(),
            "sample_rate": audio.getframerate(),
            "channels": audio.getnchannels(),
            "sample_width_bytes": audio.getsampwidth(),
            "frames": frames,
            "encoding": f"pcm_s{audio.getsampwidth() * 8}le",
        }


def selected_profile(session: Path) -> tuple[str, str, dict[str, Any]]:
    neural_path = session / "derived/preprocess/speaker-preserving-neural-echo-v2/production_selection_report.json"
    neural = read_json(neural_path)
    if neural.get("status") == "candidate" and neural.get("selected_profile") == "speaker_preserving_neural_echo_v2":
        return "speaker_preserving_neural_echo_v2", "production_selection_report", neural
    echo = read_json(session / "derived/preprocess/echo/echo_suppression_report.json")
    decision = echo.get("decision") if isinstance(echo.get("decision"), dict) else {}
    if decision.get("accepted_for_asr") is True:
        return "local_fir_role_masked", "echo_suppression_report", echo
    return "raw_fallback", "echo_suppression_report", echo


def lineage(session: Path, profile: str) -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    operations = [
        {
            "id": "committed_pcm_copy",
            "class": "causal",
            "future_context_sec": 0.0,
            "reason": "closed committed PCM can be copied outside the capture callback",
        },
        {
            "id": "export_resample_16k",
            "class": "delayed_commit",
            "future_context_sec": None,
            "reason": "interior samples stream, but exact final-tail flush is known only at source close",
        },
        {
            "id": "speech_band_filter",
            "class": "delayed_commit",
            "future_context_sec": None,
            "reason": "causal filter state is streamable; final-tail container identity closes post-stop",
        },
        {
            "id": "local_fir_activity_floors",
            "class": "whole_session_only",
            "future_context_sec": None,
            "reason": "remote and mic thresholds use p20 over every 8s/2s session window",
        },
        {
            "id": "local_fir_canonical_delay",
            "class": "whole_session_only",
            "future_context_sec": None,
            "reason": "the signed delay is the median of all reliable windows",
        },
        {
            "id": "local_fir_nearest_remote_only_fit",
            "class": "whole_session_only",
            "future_context_sec": None,
            "reason": "a chunk may select a future remote-only window with no fixed lookahead bound",
        },
        {
            "id": "local_fir_peak_scale_and_acceptance",
            "class": "whole_session_only",
            "future_context_sec": None,
            "reason": "global peak, quality medians and acceptance are evaluated after the full session",
        },
        {
            "id": "acoustic_mode_and_echo_policy",
            "class": "whole_session_only",
            "future_context_sec": None,
            "reason": "branch selection uses all remote-only windows and frozen corpus policy",
        },
        {
            "id": "speaker_preserving_neural_selection",
            "class": "whole_session_only",
            "future_context_sec": None,
            "reason": "candidate selection requires complete-session neural audio, ASR and full-shadow gates",
        },
    ]
    branches = [
        {
            "profile": "raw_fallback",
            "checkpointable": True,
            "selection_known_during_capture": False,
            "status": "speculative_exact_candidate",
        },
        {
            "profile": "local_fir_role_masked",
            "checkpointable": False,
            "selection_known_during_capture": False,
            "status": "whole_session_only",
        },
        {
            "profile": "speaker_preserving_neural_echo_v2",
            "checkpointable": False,
            "selection_known_during_capture": False,
            "status": "whole_session_only",
        },
    ]
    sources = {
        "local_fir": artifact(root / "scripts/echo-guard-session-local-fir.py"),
        "echo_policy": artifact(root / "scripts/echo-suppression-promotion-v1.py"),
        "speaker_preserving_selection": artifact(root / "scripts/apply-speaker-preserving-neural-echo-v2.py"),
        "transcriber": artifact(root / "scripts/transcribe-simple-whispercpp.py"),
    }
    return {
        "schema": LINEAGE_SCHEMA,
        "selected_profile": profile,
        "minimum_future_context": {
            "kind": "session_end",
            "bounded_sec": None,
            "reason": "current production branches contain whole-session statistics and selection gates",
        },
        "operations": operations,
        "production_eligible_branches": branches,
        "source_code": sources,
        "batch_authoritative": True,
    }


def baseline_local_fir_export(session: Path, profile: str) -> Path | None:
    backup = (
        session
        / "derived/preprocess/speaker-preserving-neural-echo-v2/"
        "baseline-local-fir-role-masked/derived/asr/mic.wav"
    )
    if backup.is_file():
        return backup
    current = session / "derived/asr/mic.wav"
    return current if profile == "local_fir_role_masked" and current.is_file() else None


def prefix_probe(
    session: Path,
    work: Path,
    *,
    profile: str,
    window_sec: int,
    overlap_sec: int,
    lookaheads: list[int],
) -> dict[str, Any]:
    mic = session / "derived/preprocess/audio/mic_raw_for_asr.wav"
    remote = session / "derived/preprocess/audio/remote_for_aec.wav"
    baseline_export = baseline_local_fir_export(session, profile)
    required = [mic, remote]
    if baseline_export is not None:
        required.append(baseline_export)
    missing = [relative(path, session) for path in required if not path.is_file()]
    if baseline_export is None:
        missing.append("exact_local_fir_baseline_export")
    if missing:
        return {"status": "unavailable", "reasons": [f"missing:{value}" for value in missing], "rows": []}

    assert baseline_export is not None
    helper = Path(__file__).with_name("echo-guard-session-local-fir.py")
    final_prepared = work / "prefix-probe/final-local-fir-speech.wav"
    ffmpeg_prepare(baseline_export, final_prepared)
    clip_end = (window_sec + overlap_sec) * CANONICAL_RATE
    final_fingerprint = slice_fingerprint(final_prepared, 0, clip_end)
    rows: list[dict[str, Any]] = []
    for lookahead in lookaheads:
        prefix = work / f"prefix-probe/lookahead-{lookahead:04d}"
        prefix.mkdir(parents=True, exist_ok=True)
        duration = window_sec + lookahead
        mic_prefix = prefix / "mic.wav"
        remote_prefix = prefix / "remote.wav"
        ffmpeg_trim_working_audio(mic, mic_prefix, duration)
        ffmpeg_trim_working_audio(remote, remote_prefix, duration)
        report_path = prefix / "local_fir_report.json"
        role_path = prefix / "mic_role_masked.wav"
        run(
            [
                sys.executable,
                str(helper),
                str(session),
                "--input-mic",
                str(mic_prefix),
                "--input-remote",
                str(remote_prefix),
                "--output-clean",
                str(prefix / "mic_clean.wav"),
                "--output-role-mask",
                str(role_path),
                "--output-role-preview",
                str(prefix / "role_preview.wav"),
                "--asr-segments-dir",
                str(prefix / "segments"),
                "--output-echo",
                str(prefix / "echo.wav"),
                "--report",
                str(report_path),
                "--segments",
                str(prefix / "segments.jsonl"),
                "--speaker-state",
                str(prefix / "speaker_state.jsonl"),
            ]
        )
        exported = prefix / "mic_export.wav"
        prepared = prefix / "mic_speech.wav"
        ffmpeg_export(role_path, exported)
        ffmpeg_prepare(exported, prepared)
        candidate_fingerprint = slice_fingerprint(prepared, 0, clip_end)
        helper_report = read_json(report_path)
        summary = helper_report.get("summary") if isinstance(helper_report.get("summary"), dict) else {}
        rows.append(
            {
                "lookahead_sec": lookahead,
                "prefix_end_sec": duration,
                "candidate_pcm": candidate_fingerprint,
                "final_local_fir_pcm": final_fingerprint,
                "exact": candidate_fingerprint == final_fingerprint,
                "prefix_statistics": {
                    "median_delay_ms": summary.get("median_delay_ms"),
                    "remote_floor_db": summary.get("remote_floor_db"),
                    "mic_floor_db": summary.get("mic_floor_db"),
                    "windows_total": summary.get("windows_total"),
                    "remote_only_windows": summary.get("remote_only_windows"),
                },
            }
        )
    return {
        "status": "completed",
        "window_index": 1,
        "window_sec": window_sec,
        "overlap_sec": overlap_sec,
        "all_bounded_contexts_exact": all(row["exact"] for row in rows),
        "rows": rows,
    }


def read_prompt(path: Path | None) -> str | None:
    if path is None:
        return None
    value = path.expanduser().read_text(encoding="utf-8").strip()
    return value or None


def run_whisper(
    *,
    whisper_cli: Path,
    model: Path,
    language: str,
    threads: int,
    max_context: int,
    prompt: str | None,
    wav: Path,
    output: Path,
    timeout: float,
) -> float:
    command = [
        str(whisper_cli),
        "--model",
        str(model),
        "--language",
        language,
        "--threads",
        str(threads),
        "--max-context",
        str(max_context),
        "--output-txt",
        "--output-json",
        "--output-json-full",
        "--output-vtt",
        "--output-file",
        str(output),
        "--no-prints",
        "--log-score",
        "--suppress-nst",
        "--suppress-regex",
        r"^(Редактор субтитров|Продолжение следует|Спасибо за просмотр|Субтитры.*)$",
        "--file",
        str(wav),
    ]
    if prompt:
        command += ["--prompt", prompt]
    elapsed = run(command, timeout=timeout, log=output.with_suffix(".run.log"))
    payload = read_json(output.with_suffix(".json"))
    if not isinstance(payload.get("transcription"), list):
        raise RuntimeError("whisper_json_invalid")
    return elapsed


def existing_proof_valid(
    chunk_dir: Path,
    expected_identity: dict[str, Any],
) -> bool:
    proof = read_json(chunk_dir / "mic.proof.json")
    payload = read_json(chunk_dir / "mic.json")
    wav = chunk_dir / "mic.wav"
    if (
        proof.get("schema") != PROOF_SCHEMA
        or proof.get("completed") is not True
        or proof.get("identity") != expected_identity
        or proof.get("identity_sha256") != cache.content_sha256(expected_identity)
        or not isinstance(payload.get("transcription"), list)
        or not wav.is_file()
    ):
        return False
    return (
        cache.pcm_fingerprint(wav) == expected_identity.get("pcm")
        and proof.get("output_json") == cache.output_fingerprint(chunk_dir / "mic.json")
    )


def decode_exact_window(
    *,
    output: Path,
    candidate: Path,
    spec: dict[str, int],
    model: Path,
    whisper_cli: Path,
    language: str,
    threads: int,
    max_context: int,
    prompt: str | None,
    timeout: float,
) -> tuple[dict[str, Any], bool, float]:
    chunk_dir = output / "chunks" / f"{spec['index']:06d}"
    wav = chunk_dir / "mic.wav"
    cache.slice_pcm_wav(candidate, wav, spec["seek_sample"], spec["clip_end_sample"])
    decode = cache.decode_contract(
        language=language,
        threads=threads,
        max_context=max_context,
        prompt=prompt,
        duration_ms=0,
    )
    identity = cache.build_chunk_identity(
        track="mic",
        role="Me",
        spec=spec,
        chunk_wav=wav,
        model=model,
        whisper_cli=whisper_cli,
        decode=decode,
        audio_prep="speech",
    )
    if existing_proof_valid(chunk_dir, identity):
        proof = read_json(chunk_dir / "mic.proof.json")
        return proof, True, 0.0
    elapsed = run_whisper(
        whisper_cli=whisper_cli,
        model=model,
        language=language,
        threads=threads,
        max_context=max_context,
        prompt=prompt,
        wav=wav,
        output=chunk_dir / "mic",
        timeout=timeout,
    )
    proof = {
        "schema": PROOF_SCHEMA,
        "completed": True,
        "created_at": utc_now(),
        "identity": identity,
        "identity_sha256": cache.content_sha256(identity),
        "output_json": cache.output_fingerprint(chunk_dir / "mic.json"),
        "provenance": {
            "origin": "historical_replay",
            "producer": {"name": "causal-canonical-mic-asr", "version": SCRIPT_VERSION},
            "candidate_branch": "raw_fallback",
            "verification": "post_stop_byte_identical_pcm",
        },
    }
    cache.atomic_write_json(chunk_dir / "mic.proof.json", proof)
    return proof, False, elapsed


def write_markdown(output: Path, report: dict[str, Any]) -> None:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    prefix = report.get("prefix_probe") if isinstance(report.get("prefix_probe"), dict) else {}
    lines = [
        "# Causal Canonical Mic ASR v1",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Selected mic profile: `{report.get('selected_profile')}`",
        f"- Exact raw-fallback windows: `{summary.get('exact_windows', 0)}/{summary.get('windows_total', 0)}`",
        f"- Exact hard seconds: `{summary.get('exact_hard_sec', 0.0)}/{summary.get('total_hard_sec', 0.0)}`",
        f"- Bounded local-FIR prefix contexts exact: `{prefix.get('all_bounded_contexts_exact')}`",
        f"- Minimum exact future context: `{report.get('lineage', {}).get('minimum_future_context', {}).get('kind')}`",
        f"- Decision: `{report.get('decision')}`",
        "- Batch authoritative: `true`",
        "- Raw capture modified: `false`",
        "",
        "The raw fallback is the only causal candidate. Current local-FIR and speaker-preserving branches",
        "contain whole-session statistics or full-shadow gates, so unmatched windows stay on ordinary batch ASR.",
    ]
    cache.atomic_write_bytes(output / "report.md", ("\n".join(lines) + "\n").encode("utf-8"))


def audit(args: argparse.Namespace) -> dict[str, Any]:
    policy = resolve_resource_policy(args.resource_profile, args.max_compute_threads)
    resource = apply_resource_policy(policy)
    args.threads = min(max(1, args.threads), policy.max_compute_threads or args.threads)
    session = args.session.expanduser().resolve()
    output = (
        args.output.expanduser().resolve()
        if args.output
        else session / "derived/experiments/live-shadow-v1/authoritative-mic-asr"
    )
    output.mkdir(parents=True, exist_ok=True)
    raw_mic = session / "audio/mic/000001.caf"
    final_mic = session / "derived/asr/mic.wav"
    raw_before = artifact(raw_mic, session)
    profile, profile_source, profile_report = selected_profile(session)
    mic_lineage = lineage(session, profile)
    cache.atomic_write_json(output / "lineage.json", mic_lineage)
    prompt = read_prompt(args.prompt_file)
    model = args.model.expanduser().resolve()
    whisper_raw = shutil.which(args.whisper_cli) or args.whisper_cli
    whisper_cli = Path(whisper_raw).expanduser().resolve()
    started = time.monotonic()
    rows: list[dict[str, Any]] = []
    exact_windows = 0
    exact_hard_sec = 0.0
    decoded = 0
    reused = 0
    decode_elapsed = 0.0
    status = "completed"
    reason: str | None = None
    prefix_result: dict[str, Any] = {"status": "not_run", "rows": []}

    if not raw_mic.is_file() or not final_mic.is_file():
        status = "fallback"
        reason = "raw_mic_missing" if not raw_mic.is_file() else "canonical_mic_missing"
        specs: list[dict[str, int]] = []
        final_format: dict[str, Any] = {}
        candidate_format: dict[str, Any] = {}
    else:
        with tempfile.TemporaryDirectory(prefix="murmurmark-causal-mic-") as temporary:
            work = Path(temporary)
            raw_export = work / "raw_mic_export.wav"
            candidate = work / "raw_mic_speech.wav"
            final_prepared = work / "final_mic_speech.wav"
            ffmpeg_export(raw_mic, raw_export)
            ffmpeg_prepare(raw_export, candidate)
            ffmpeg_prepare(final_mic, final_prepared)
            final_format = cache.wave_format(final_prepared)
            candidate_format = cache.wave_format(candidate)
            specs = chunk_specs(int(final_format["frames"]), args.window_sec, args.overlap_sec)
            geometry_equal = candidate_format == final_format
            for spec in specs:
                candidate_pcm: dict[str, Any] | None = None
                final_pcm = slice_fingerprint(final_prepared, spec["seek_sample"], spec["clip_end_sample"])
                exact = False
                window_reason = "candidate_geometry_mismatch"
                if geometry_equal:
                    candidate_pcm = slice_fingerprint(candidate, spec["seek_sample"], spec["clip_end_sample"])
                    exact = candidate_pcm == final_pcm
                    window_reason = "byte_identical" if exact else "candidate_pcm_mismatch"
                row: dict[str, Any] = {
                    "schema": WINDOW_SCHEMA,
                    "index": spec["index"],
                    "hard_start_sec": spec["hard_start_sample"] / CANONICAL_RATE,
                    "hard_end_sec": spec["hard_end_sample"] / CANONICAL_RATE,
                    "clip_start_sec": spec["seek_sample"] / CANONICAL_RATE,
                    "clip_end_sec": spec["clip_end_sample"] / CANONICAL_RATE,
                    "candidate_branch": "raw_fallback",
                    "selected_profile": profile,
                    "candidate_pcm": candidate_pcm,
                    "canonical_pcm": final_pcm,
                    "exact": exact,
                    "reason": window_reason,
                    "proof_published": False,
                }
                if exact:
                    exact_windows += 1
                    exact_hard_sec += (spec["hard_end_sample"] - spec["hard_start_sample"]) / CANONICAL_RATE
                    can_decode = (
                        args.decode_exact
                        and decoded < args.max_decode_chunks
                        and model.is_file()
                        and whisper_cli.is_file()
                    )
                    if can_decode:
                        proof, was_reused, elapsed = decode_exact_window(
                            output=output,
                            candidate=candidate,
                            spec=spec,
                            model=model,
                            whisper_cli=whisper_cli,
                            language=args.language,
                            threads=args.threads,
                            max_context=args.max_context,
                            prompt=prompt,
                            timeout=args.decode_timeout_sec,
                        )
                        row["proof_published"] = True
                        row["proof_identity_sha256"] = proof.get("identity_sha256")
                        row["decode_status"] = "reused" if was_reused else "completed"
                        decoded += 1
                        reused += int(was_reused)
                        decode_elapsed += elapsed
                        if args.interrupt_after_chunks and decoded >= args.interrupt_after_chunks:
                            rows.append(row)
                            status = "interrupted_fail_open"
                            reason = "test_interruption"
                            break
                    elif args.decode_exact:
                        row["decode_status"] = (
                            "model_missing"
                            if not model.is_file()
                            else "whisper_cli_missing"
                            if not whisper_cli.is_file()
                            else "bounded_limit"
                        )
                    else:
                        row["decode_status"] = "not_requested"
                rows.append(row)
            if args.prefix_probe:
                prefix_result = prefix_probe(
                    session,
                    work,
                    profile=profile,
                    window_sec=args.window_sec,
                    overlap_sec=args.overlap_sec,
                    lookaheads=args.prefix_lookaheads,
                )

    total_hard_sec = sum(
        max(0.0, float(row["hard_end_sec"]) - float(row["hard_start_sec"])) for row in rows
    )
    exact_ratio = exact_hard_sec / total_hard_sec if total_hard_sec else 0.0
    if status == "completed" and exact_ratio < 0.5:
        decision = "DO_NOT_PROMOTE"
    elif status == "completed" and profile == "raw_fallback" and exact_ratio >= 0.5:
        decision = "CANDIDATE_REQUIRES_RECORDING_TIME_EVIDENCE"
    else:
        decision = "DO_NOT_PROMOTE"
    raw_after = artifact(raw_mic, session)
    report = {
        "schema": REPORT_SCHEMA,
        "generator": {"name": "causal-canonical-mic-asr", "version": SCRIPT_VERSION},
        "created_at": utc_now(),
        "session_id": session.name,
        "status": status,
        "reason": reason,
        "decision": decision,
        "selected_profile": profile,
        "selected_profile_source": profile_source,
        "selected_profile_report": {
            "schema": profile_report.get("schema"),
            "status": profile_report.get("status"),
            "reason": profile_report.get("reason"),
        },
        "lineage": mic_lineage,
        "prefix_probe": prefix_result,
        "summary": {
            "windows_total": len(specs),
            "windows_evaluated": len(rows),
            "exact_windows": exact_windows,
            "exact_hard_sec": round(exact_hard_sec, 6),
            "total_hard_sec": round(total_hard_sec, 6),
            "exact_hard_ratio": round(exact_ratio, 9),
            "proofs_published": sum(1 for row in rows if row.get("proof_published") is True),
            "chunks_decoded": decoded,
            "chunks_reused": reused,
            "decode_elapsed_sec": round(decode_elapsed, 6),
            "audit_elapsed_sec": round(time.monotonic() - started, 6),
        },
        "candidate": {
            "profile": "raw_fallback",
            "transport": "post_stop_raw_replay",
            "recording_time_evidence": False,
            "format": candidate_format,
        },
        "canonical": {
            "profile": profile,
            "source": artifact(final_mic, session),
            "format": final_format,
        },
        "engine": {
            "model": artifact(model),
            "binary": artifact(whisper_cli),
            "language": args.language,
            "threads": args.threads,
            "max_context": args.max_context,
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest() if prompt else None,
            "window_sec": args.window_sec,
            "overlap_sec": args.overlap_sec,
            "audio_prep": "speech",
        },
        "resource_policy": resource,
        "safety": {
            "raw_capture_before": raw_before,
            "raw_capture_after": raw_after,
            "raw_capture_unchanged": raw_before == raw_after,
            "capture_callback_work": False,
            "batch_authoritative": True,
            "automatic_cache_materialization": False,
            "fallback": "ordinary_post_stop_batch",
        },
        "outputs": {"lineage": "lineage.json", "windows": "windows.jsonl", "report": "report.json"},
    }
    atomic_write_jsonl(output / "windows.jsonl", rows)
    cache.atomic_write_json(output / "report.json", report)
    write_markdown(output, report)
    return report


def fallback_report(args: argparse.Namespace, error: Exception) -> dict[str, Any]:
    session = args.session.expanduser().resolve()
    output = (
        args.output.expanduser().resolve()
        if args.output
        else session / "derived/experiments/live-shadow-v1/authoritative-mic-asr"
    )
    output.mkdir(parents=True, exist_ok=True)
    report = {
        "schema": REPORT_SCHEMA,
        "generator": {"name": "causal-canonical-mic-asr", "version": SCRIPT_VERSION},
        "created_at": utc_now(),
        "session_id": session.name,
        "status": "fallback",
        "reason": f"{type(error).__name__}:{error}",
        "decision": "DO_NOT_PROMOTE",
        "summary": {
            "windows_total": 0,
            "windows_evaluated": 0,
            "exact_windows": 0,
            "exact_hard_sec": 0.0,
            "total_hard_sec": 0.0,
            "exact_hard_ratio": 0.0,
            "proofs_published": 0,
        },
        "safety": {
            "capture_callback_work": False,
            "batch_authoritative": True,
            "automatic_cache_materialization": False,
            "fallback": "ordinary_post_stop_batch",
        },
    }
    cache.atomic_write_json(output / "report.json", report)
    write_markdown(output, report)
    return report


def main() -> int:
    args = parse_args()
    try:
        report = audit(args)
    except Exception as error:
        report = fallback_report(args, error)
    print(f"causal_canonical_mic_asr_report: {args.output or args.session / 'derived/experiments/live-shadow-v1/authoritative-mic-asr/report.json'}")
    print(f"status: {report.get('status')}")
    print(f"decision: {report.get('decision')}")
    print(f"exact_hard_ratio: {(report.get('summary') or {}).get('exact_hard_ratio', 0.0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
