#!/usr/bin/env python3
"""Produce exact remote whisper.cpp chunks from durable committed PCM.

The producer is deliberately advisory. It consumes only closed sidecar CAF
segments, publishes a proof only after PCM and whisper JSON are complete, and
never touches raw capture files. The post-stop materializer independently
rebuilds canonical audio and either accepts the proof or falls back to batch.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

import authoritative_asr_cache as authoritative_cache
from murmurmark_resource_policy import (
    apply_resource_policy,
    bounded_threads,
    resolve_resource_policy,
)


SCRIPT_VERSION = "0.2.0"
STATE_SCHEMA = "murmurmark.canonical_live_asr_producer_state/v1"
REPORT_SCHEMA = "murmurmark.canonical_live_asr_producer_report/v1"
WINDOW_SCHEMA = "murmurmark.authoritative_live_asr_window/v1"
PROOF_SCHEMA = "murmurmark.authoritative_live_asr_chunk/v1"
EXACT_SEGMENT_SCHEMA = "murmurmark.live_segment/v1"
DEFAULT_MODEL = Path.home() / ".local/share/murmurmark/models/whisper.cpp/ggml-large-v3-q5_0.bin"
REMOTE_PREP_FILTER = "highpass=f=80,lowpass=f=7800,loudnorm=I=-20:LRA=9:TP=-2,alimiter=limit=0.98"
CANONICAL_RATE = 16_000
STOP_REQUESTED = False


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def request_stop(_signum: int, _frame: Any) -> None:
    global STOP_REQUESTED
    STOP_REQUESTED = True


def read_json(path: Path) -> dict[str, Any] | None:
    return authoritative_cache.read_json(path)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as file:
            for line in file:
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    rows.append(value)
    except OSError:
        return []
    return rows


def rel(path: Path, session: Path) -> str:
    try:
        return str(path.resolve().relative_to(session.resolve()))
    except ValueError:
        return str(path.resolve())


def load_local_config(path: Path | None) -> dict[str, Any]:
    if path is None:
        configured = os.environ.get("MURMURMARK_CONFIG")
        path = Path(configured).expanduser() if configured else Path.cwd() / "murmurmark.config.json"
    payload = read_json(path.expanduser())
    return payload or {}


def resolve_path(value: str | None, default: Path) -> Path:
    return Path(value).expanduser().resolve() if value else default.expanduser().resolve()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Produce strict canonical remote ASR evidence during capture.")
    parser.add_argument("session", type=Path)
    parser.add_argument("--experiment", default="live-shadow-v1")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--model", type=Path)
    parser.add_argument("--language")
    parser.add_argument("--prompt-file", type=Path)
    parser.add_argument("--whisper-cli", default=os.environ.get("WHISPER_CLI", "whisper-cli"))
    parser.add_argument("--threads", type=int)
    parser.add_argument("--max-context", type=int, default=0)
    parser.add_argument("--window-sec", type=int, default=60)
    parser.add_argument("--overlap-sec", type=int, default=5)
    parser.add_argument("--lookahead-sec", type=int, default=5)
    parser.add_argument("--poll-sec", type=float, default=1.0)
    parser.add_argument("--idle-after-session-json-sec", type=float, default=3.0)
    parser.add_argument("--whisper-timeout-sec", type=float, default=900.0)
    parser.add_argument("--once", action="store_true", help="Process currently available rows and return.")
    parser.add_argument(
        "--replay-from-raw",
        action="store_true",
        help="Offline evidence mode for finalized historical sessions; never labels output as live-origin.",
    )
    args = parser.parse_args()
    if args.window_sec <= 0 or args.overlap_sec < 0 or args.overlap_sec >= args.window_sec / 2:
        parser.error("invalid window/overlap geometry")
    if args.lookahead_sec < 0:
        parser.error("--lookahead-sec must be non-negative")

    config = load_local_config(args.config)
    transcription = config.get("transcription") if isinstance(config.get("transcription"), dict) else {}
    processing = config.get("processing") if isinstance(config.get("processing"), dict) else {}
    profile = str(processing.get("resource_profile") or "background")
    max_threads_raw = processing.get("max_compute_threads")
    try:
        max_threads = int(max_threads_raw) if max_threads_raw is not None else None
    except (TypeError, ValueError):
        max_threads = None
    policy = resolve_resource_policy(profile, max_threads)
    args.resource_policy = policy
    args.threads = bounded_threads(args.threads or policy.asr_threads, policy)
    args.model = resolve_path(
        str(args.model) if args.model else str(transcription.get("model") or ""),
        DEFAULT_MODEL,
    )
    args.language = args.language or str(transcription.get("language") or "ru")
    prompt_value = args.prompt_file or (
        Path(str(transcription["prompt_file"])).expanduser() if transcription.get("prompt_file") else None
    )
    args.prompt_file = prompt_value.resolve() if prompt_value else None
    return args


def write_state(output: Path, *, status: str, stage: str, progress: dict[str, Any], reason: str | None = None) -> None:
    authoritative_cache.atomic_write_json(
        output / "state.json",
        {
            "schema": STATE_SCHEMA,
            "generator": {"name": "canonical-live-asr-producer", "version": SCRIPT_VERSION},
            "status": status,
            "stage": stage,
            "reason": reason,
            "updated_at": utc_now(),
            "batch_authoritative": True,
            "promotion_allowed": False,
            "scope": "remote_only_v1",
            "progress": progress,
        },
    )


def append_event(output: Path, *, event: str, **fields: Any) -> None:
    payload = {
        "schema": "murmurmark.canonical_live_asr_producer_event/v1",
        "event": event,
        "created_at": utc_now(),
        **fields,
    }
    path = output / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def exact_remote_rows(session: Path) -> tuple[list[dict[str, Any]], list[str]]:
    manifest = session / "derived/live/segments.jsonl"
    rows = [
        row
        for row in read_jsonl(manifest)
        if row.get("schema") == EXACT_SEGMENT_SCHEMA and row.get("source") == "remote" and row.get("closed") is True
    ]
    rows.sort(key=lambda row: int(row.get("index") or 0))
    reasons: list[str] = []
    required = (
        "hard_start_frame",
        "hard_end_frame",
        "clip_start_frame",
        "clip_end_frame",
        "sample_rate",
        "path",
    )
    for row in rows:
        missing = [key for key in required if row.get(key) is None]
        if missing:
            reasons.append(f"segment_{row.get('index')}_missing_exact_fields:{','.join(missing)}")
    return rows, reasons


def rebuild_prefix_from_raw(session: Path, destination: Path) -> dict[str, Any]:
    raw = session / "audio/remote/000001.caf"
    if not raw.exists():
        raise FileNotFoundError(raw)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sf.SoundFile(str(raw), "r") as audio:
        sample_rate = int(audio.samplerate)
        channels = int(audio.channels)
        frames = int(len(audio))
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            with temporary.open("wb") as output_file:
                while True:
                    data = audio.read(65536, dtype="float32", always_2d=True)
                    if data.size == 0:
                        break
                    output_file.write(np.asarray(data, dtype="<f4").tobytes(order="C"))
                output_file.flush()
                os.fsync(output_file.fileno())
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
    metadata = {
        "schema": "murmurmark.committed_pcm_prefix/v1",
        "sample_rate": sample_rate,
        "channels": channels,
        "frames": frames,
        "duration_sec": frames / sample_rate,
        "encoding": "f32le",
        "bytes": destination.stat().st_size,
        "sha256": authoritative_cache.sha256_file(destination),
        "replay_source": authoritative_cache.file_fingerprint(raw),
        "canonicalization": "post_stop_caf_bridge_two_stage_replay_v1",
    }
    authoritative_cache.atomic_write_json(destination.with_suffix(".json"), metadata)
    return metadata


def read_hard_segment_pcm(session: Path, row: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
    hard_start = int(row["hard_start_frame"])
    hard_end = int(row["hard_end_frame"])
    clip_start = int(row["clip_start_frame"])
    clip_end = int(row["clip_end_frame"])
    path = Path(str(row["path"]))
    if not path.is_absolute():
        path = session / path
    if not path.exists():
        raise FileNotFoundError(path)
    with sf.SoundFile(str(path), "r") as audio:
        sample_rate = int(audio.samplerate)
        channels = int(audio.channels)
        expected_clip_frames = clip_end - clip_start
        if len(audio) != expected_clip_frames:
            raise ValueError(f"segment_clip_frame_mismatch:{len(audio)}!={expected_clip_frames}")
        offset = hard_start - clip_start
        count = hard_end - hard_start
        if offset < 0 or count <= 0 or offset + count > len(audio):
            raise ValueError("hard_interval_outside_segment")
        audio.seek(offset)
        samples = audio.read(count, dtype="float32", always_2d=True)
        if samples.shape != (count, channels):
            raise ValueError("short_segment_read")
    payload = np.asarray(samples, dtype="<f4").tobytes(order="C")
    return payload, {
        "index": int(row.get("index") or 0),
        "path": rel(path, session),
        "file": authoritative_cache.file_fingerprint(path, include_path=False),
        "hard_start_frame": hard_start,
        "hard_end_frame": hard_end,
        "sample_rate": sample_rate,
        "channels": channels,
    }


class StreamingCanonicalizer:
    """Feed each committed source frame once through the exact two-stage batch filters."""

    def __init__(self, session: Path, output: Path, *, sample_rate: int, channels: int) -> None:
        self.session = session
        self.output = output
        self.sample_rate = sample_rate
        self.channels = channels
        self.frames = 0
        self.segments: list[dict[str, Any]] = []
        self.source_hash = hashlib.sha256()
        self.closed = False
        self.output_raw = output / "work/remote_prepared_stream.s16le"
        self.output_raw.parent.mkdir(parents=True, exist_ok=True)
        log_dir = output / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        self._caf_log = (log_dir / "ffmpeg_stream_caf.log").open("w", encoding="utf-8")
        self._export_log = (log_dir / "ffmpeg_stream_export.log").open("w", encoding="utf-8")
        self._prepare_log = (log_dir / "ffmpeg_stream_prepare.log").open("w", encoding="utf-8")
        self._output_handle = self.output_raw.open("wb")
        ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
        caf_command = [
            ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "f32le",
            "-ar",
            str(sample_rate),
            "-ac",
            str(channels),
            "-i",
            "pipe:0",
            "-c:a",
            "pcm_f32le",
            "-f",
            "caf",
            "pipe:1",
        ]
        export_command = [
            ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "caf",
            "-i",
            "pipe:0",
            "-map",
            "0:a:0",
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(CANONICAL_RATE),
            "-f",
            "s16le",
            "pipe:1",
        ]
        prepare_command = [
            ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "s16le",
            "-ar",
            str(CANONICAL_RATE),
            "-ac",
            "1",
            "-i",
            "pipe:0",
            "-af",
            REMOTE_PREP_FILTER,
            "-ar",
            str(CANONICAL_RATE),
            "-ac",
            "1",
            "-flush_packets",
            "1",
            "-f",
            "s16le",
            "pipe:1",
        ]
        self._caf = subprocess.Popen(
            caf_command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._caf_log,
        )
        if self._caf.stdout is None:
            raise RuntimeError("stream_caf_stdout_unavailable")
        self._export = subprocess.Popen(
            export_command,
            stdin=self._caf.stdout,
            stdout=subprocess.PIPE,
            stderr=self._export_log,
        )
        self._caf.stdout.close()
        if self._export.stdout is None:
            raise RuntimeError("stream_export_stdout_unavailable")
        self._prepare = subprocess.Popen(
            prepare_command,
            stdin=self._export.stdout,
            stdout=self._output_handle,
            stderr=self._prepare_log,
        )
        self._export.stdout.close()

    def append(self, rows: list[dict[str, Any]]) -> float:
        if self.closed:
            return 0.0
        started = time.monotonic()
        for row in rows:
            hard_start = int(row["hard_start_frame"])
            hard_end = int(row["hard_end_frame"])
            if hard_end <= self.frames:
                continue
            if hard_start != self.frames:
                raise ValueError(f"non_contiguous_hard_geometry:{self.frames}!={hard_start}..{hard_end}")
            payload, fingerprint = read_hard_segment_pcm(self.session, row)
            if fingerprint["sample_rate"] != self.sample_rate or fingerprint["channels"] != self.channels:
                raise ValueError("segment_audio_format_changed")
            if self._caf.stdin is None:
                raise RuntimeError("stream_caf_stdin_unavailable")
            self._caf.stdin.write(payload)
            self._caf.stdin.flush()
            self.source_hash.update(payload)
            self.segments.append(fingerprint)
            self.frames = hard_end
            self._check_running(allow_finished=False)
        return time.monotonic() - started

    def available_samples(self) -> int:
        try:
            size = self.output_raw.stat().st_size
        except FileNotFoundError:
            return 0
        if size % 2:
            raise ValueError("prepared_stream_has_partial_sample")
        return size // 2

    def wait_for_samples(self, minimum: int, timeout_sec: float = 10.0) -> int:
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            available = self.available_samples()
            if available >= minimum:
                return available
            self._check_running(allow_finished=self.closed)
            time.sleep(0.05)
        return self.available_samples()

    def finalize(self, timeout_sec: float = 30.0) -> float:
        if self.closed:
            return 0.0
        started = time.monotonic()
        self.closed = True
        if self._caf.stdin is not None:
            self._caf.stdin.close()
        try:
            caf_code = self._caf.wait(timeout=timeout_sec)
            export_code = self._export.wait(timeout=timeout_sec)
            prepare_code = self._prepare.wait(timeout=timeout_sec)
        except subprocess.TimeoutExpired as error:
            self.terminate()
            raise RuntimeError("stream_canonicalizer_finalize_timeout") from error
        self._close_handles()
        if caf_code != 0 or export_code != 0 or prepare_code != 0:
            raise RuntimeError(f"stream_canonicalizer_failed:{caf_code}:{export_code}:{prepare_code}")
        return time.monotonic() - started

    def terminate(self) -> None:
        for process in (self._caf, self._export, self._prepare):
            if process.poll() is None:
                process.terminate()
        for process in (self._caf, self._export, self._prepare):
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        self.closed = True
        self._close_handles()

    def metadata(self) -> dict[str, Any]:
        return {
            "schema": "murmurmark.committed_pcm_prefix/v1",
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "frames": self.frames,
            "duration_sec": self.frames / self.sample_rate,
            "encoding": "f32le",
            "bytes": self.frames * self.channels * 4,
            "sha256": self.source_hash.hexdigest(),
            "segments": list(self.segments),
            "canonicalization": "streaming_two_stage_v1",
        }

    def _check_running(self, *, allow_finished: bool) -> None:
        caf_code = self._caf.poll()
        export_code = self._export.poll()
        prepare_code = self._prepare.poll()
        if allow_finished and caf_code == 0 and export_code == 0 and prepare_code == 0:
            return
        if caf_code is not None or export_code is not None or prepare_code is not None:
            raise RuntimeError(f"stream_canonicalizer_stopped:{caf_code}:{export_code}:{prepare_code}")

    def _close_handles(self) -> None:
        for handle in (self._output_handle, self._caf_log, self._export_log, self._prepare_log):
            try:
                handle.close()
            except OSError:
                pass


def slice_s16le_to_wav(source: Path, destination: Path, start_sample: int, end_sample: int) -> None:
    if start_sample < 0 or end_sample <= start_sample:
        raise ValueError(f"invalid PCM slice {start_sample}..{end_sample}")
    byte_start = start_sample * 2
    byte_count = (end_sample - start_sample) * 2
    with source.open("rb") as input_file:
        input_file.seek(byte_start)
        payload = input_file.read(byte_count)
    if len(payload) != byte_count:
        raise ValueError(f"short prepared stream slice:{len(payload)}!={byte_count}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".wav", dir=destination.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with wave.open(str(temporary), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(CANONICAL_RATE)
            audio.writeframes(payload)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def run_ffmpeg(command: list[str], log_path: Path) -> float:
    started = time.monotonic()
    completed = subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    elapsed = time.monotonic() - started
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(completed.stderr or "", encoding="utf-8")
    if completed.returncode != 0:
        raise RuntimeError(f"ffmpeg_failed:{completed.returncode}:{(completed.stderr or '')[-500:]}")
    return elapsed


def canonicalize_prefix(prefix: Path, metadata: dict[str, Any], output: Path) -> dict[str, Any]:
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    source_caf = output / "work/remote_committed_prefix.caf"
    export_wav = output / "work/remote_export_prefix.wav"
    prepared_wav = output / "work/remote_prepared_prefix.wav"
    export_wav.parent.mkdir(parents=True, exist_ok=True)
    caf_elapsed = run_ffmpeg(
        [
            ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "f32le",
            "-ar",
            str(metadata["sample_rate"]),
            "-ac",
            str(metadata["channels"]),
            "-i",
            str(prefix),
            "-c:a",
            "pcm_f32le",
            str(source_caf),
        ],
        output / "logs/ffmpeg_caf_bridge.log",
    )
    export_elapsed = run_ffmpeg(
        [
            ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source_caf),
            "-map",
            "0:a:0",
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(CANONICAL_RATE),
            str(export_wav),
        ],
        output / "logs/ffmpeg_export.log",
    )
    prep_elapsed = run_ffmpeg(
        [
            ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(export_wav),
            "-af",
            REMOTE_PREP_FILTER,
            "-ar",
            str(CANONICAL_RATE),
            "-ac",
            "1",
            str(prepared_wav),
        ],
        output / "logs/ffmpeg_prepare.log",
    )
    return {
        "path": prepared_wav,
        "format": authoritative_cache.wave_format(prepared_wav),
        "caf_elapsed_sec": round(caf_elapsed, 6),
        "export_elapsed_sec": round(export_elapsed, 6),
        "prepare_elapsed_sec": round(prep_elapsed, 6),
    }


def read_prompt(path: Path | None) -> str | None:
    if path is None:
        return None
    text = path.read_text(encoding="utf-8").strip()
    return text or None


def run_whisper(
    *,
    whisper_cli: Path,
    model: Path,
    language: str,
    threads: int,
    max_context: int,
    prompt: str | None,
    input_wav: Path,
    output_base: Path,
    timeout_sec: float,
) -> dict[str, Any]:
    output_base.parent.mkdir(parents=True, exist_ok=True)
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
        str(output_base),
        "--no-prints",
        "--log-score",
        "--suppress-nst",
        "--suppress-regex",
        r"^(Редактор субтитров|Продолжение следует|Спасибо за просмотр|Субтитры.*)$",
        "--file",
        str(input_wav),
    ]
    if prompt:
        command += ["--prompt", prompt]
    started = time.monotonic()
    log_path = output_base.with_suffix(".run.log")
    with log_path.open("w", encoding="utf-8") as log:
        try:
            completed = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=False,
                timeout=timeout_sec,
            )
        except subprocess.TimeoutExpired as error:
            raise RuntimeError(f"whisper_timeout:{timeout_sec}") from error
    if completed.returncode in {-11, 139}:
        retry = list(command)
        retry.insert(retry.index("--file"), "--no-gpu")
        with log_path.open("a", encoding="utf-8") as log:
            completed = subprocess.run(
                retry,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=False,
                timeout=timeout_sec,
            )
    if completed.returncode != 0:
        raise RuntimeError(f"whisper_failed:{completed.returncode}")
    payload = read_json(output_base.with_suffix(".json"))
    if payload is None or not isinstance(payload.get("transcription"), list):
        raise RuntimeError("whisper_json_invalid")
    return {
        "elapsed_sec": round(time.monotonic() - started, 6),
        "returncode": completed.returncode,
        "command_sha256": hashlib.sha256("\0".join(command).encode("utf-8")).hexdigest(),
    }


def chunk_specs(total_frames: int, *, window_sec: int, overlap_sec: int) -> list[dict[str, int]]:
    window = window_sec * CANONICAL_RATE
    overlap = overlap_sec * CANONICAL_RATE
    specs: list[dict[str, int]] = []
    hard_start = 0
    index = 1
    while hard_start < total_frames:
        hard_end = min(total_frames, hard_start + window)
        seek = max(0, hard_start - overlap)
        clip_end = min(total_frames, hard_end + overlap)
        specs.append(
            {
                "index": index,
                "hard_start_sample": hard_start,
                "hard_end_sample": hard_end,
                "seek_sample": seek,
                "clip_end_sample": clip_end,
                "hard_start_ms": round(hard_start * 1000 / CANONICAL_RATE),
                "hard_end_ms": round(hard_end * 1000 / CANONICAL_RATE),
                "seek_ms": round(seek * 1000 / CANONICAL_RATE),
                "clip_end_ms": round(clip_end * 1000 / CANONICAL_RATE),
                "clip_duration_ms": round((clip_end - seek) * 1000 / CANONICAL_RATE),
                "sample_rate": CANONICAL_RATE,
            }
        )
        hard_start += window
        index += 1
    return specs


def load_windows(path: Path) -> list[dict[str, Any]]:
    rows = [row for row in read_jsonl(path) if row.get("schema") == WINDOW_SCHEMA]
    return sorted(rows, key=lambda row: int(row.get("index") or 0))


def write_windows(path: Path, rows: list[dict[str, Any]]) -> None:
    content = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
    authoritative_cache.atomic_write_bytes(path, content.encode("utf-8"))


def proof_is_valid(
    row: dict[str, Any],
    *,
    session: Path,
    model: Path,
    whisper_cli: Path,
    decode: dict[str, Any],
) -> bool:
    remote = row.get("remote") if isinstance(row.get("remote"), dict) else {}
    proof = remote.get("batch_cache_compatibility") if isinstance(remote, dict) else None
    if not isinstance(proof, dict) or proof.get("schema") != PROOF_SCHEMA or proof.get("completed") is not True:
        return False
    provenance = proof.get("provenance") if isinstance(proof.get("provenance"), dict) else {}
    producer = provenance.get("producer") if isinstance(provenance.get("producer"), dict) else {}
    if producer.get("version") != SCRIPT_VERSION:
        return False
    identity = proof.get("identity")
    if not isinstance(identity, dict) or proof.get("identity_sha256") != authoritative_cache.content_sha256(identity):
        return False
    wav_value = remote.get("wav")
    asr = remote.get("asr") if isinstance(remote.get("asr"), dict) else {}
    json_value = asr.get("json")
    if not isinstance(wav_value, str) or not isinstance(json_value, str):
        return False
    wav_path = Path(wav_value)
    json_path = Path(json_value)
    if not wav_path.is_absolute():
        wav_path = session / wav_path
    if not json_path.is_absolute():
        json_path = session / json_path
    try:
        if authoritative_cache.pcm_fingerprint(wav_path) != identity.get("pcm"):
            return False
        if authoritative_cache.output_fingerprint(json_path) != proof.get("output_json"):
            return False
        payload = read_json(json_path)
        if payload is None or not isinstance(payload.get("transcription"), list):
            return False
        source_pcm = provenance.get("source_pcm") if isinstance(provenance.get("source_pcm"), dict) else {}
        for segment in source_pcm.get("segments") or []:
            if not isinstance(segment, dict) or not isinstance(segment.get("path"), str):
                return False
            path = Path(segment["path"])
            if not path.is_absolute():
                path = session / path
            if authoritative_cache.file_fingerprint(path, include_path=False) != segment.get("file"):
                return False
    except (OSError, ValueError):
        return False
    engine = identity.get("engine") if isinstance(identity.get("engine"), dict) else {}
    return (
        identity.get("decode") == decode
        and engine.get("model") == authoritative_cache.file_fingerprint(model, include_path=False)
        and engine.get("binary") == authoritative_cache.file_fingerprint(whisper_cli, include_path=False)
    )


def publish_window(
    *,
    session: Path,
    output: Path,
    prepared_prefix: Path,
    spec: dict[str, int],
    model: Path,
    whisper_cli: Path,
    language: str,
    threads: int,
    max_context: int,
    prompt: str | None,
    timeout_sec: float,
    origin: str,
    prepared_is_raw_s16le: bool = False,
    source_pcm: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    index = spec["index"]
    chunk_dir = output / "chunks" / f"{index:06d}"
    chunk_wav = chunk_dir / "remote.wav"
    if prepared_is_raw_s16le:
        slice_s16le_to_wav(
            prepared_prefix,
            chunk_wav,
            spec["seek_sample"],
            spec["clip_end_sample"],
        )
    else:
        authoritative_cache.slice_pcm_wav(
            prepared_prefix,
            chunk_wav,
            spec["seek_sample"],
            spec["clip_end_sample"],
        )
    decode = authoritative_cache.decode_contract(
        language=language,
        threads=threads,
        max_context=max_context,
        prompt=prompt,
        duration_ms=0,
    )
    identity = authoritative_cache.build_chunk_identity(
        track="remote",
        role="Colleagues",
        spec=spec,
        chunk_wav=chunk_wav,
        model=model,
        whisper_cli=whisper_cli,
        decode=decode,
        audio_prep="loudnorm",
    )
    with tempfile.TemporaryDirectory(prefix=f"murmurmark-live-asr-{index:04d}-") as temporary:
        temporary_base = Path(temporary) / "remote"
        execution = run_whisper(
            whisper_cli=whisper_cli,
            model=model,
            language=language,
            threads=threads,
            max_context=max_context,
            prompt=prompt,
            input_wav=chunk_wav,
            output_base=temporary_base,
            timeout_sec=timeout_sec,
        )
        for suffix in (".json", ".txt", ".vtt", ".run.log"):
            source = temporary_base.with_suffix(suffix)
            if source.exists():
                authoritative_cache.atomic_write_bytes(chunk_dir / f"remote{suffix}", source.read_bytes())
    json_path = chunk_dir / "remote.json"
    proof = {
        "schema": PROOF_SCHEMA,
        "completed": True,
        "created_at": utc_now(),
        "identity": identity,
        "identity_sha256": authoritative_cache.content_sha256(identity),
        "output_json": authoritative_cache.output_fingerprint(json_path),
        "provenance": {
            "origin": origin,
            "execution": execution,
            "producer": {"name": "canonical-live-asr-producer", "version": SCRIPT_VERSION},
            "source_pcm": source_pcm or {},
        },
    }
    authoritative_cache.atomic_write_json(chunk_dir / "remote.proof.json", proof)
    row = {
        "schema": WINDOW_SCHEMA,
        "index": index,
        "start_sec": spec["hard_start_sample"] / CANONICAL_RATE,
        "end_sec": spec["hard_end_sample"] / CANONICAL_RATE,
        "duration_sec": (spec["hard_end_sample"] - spec["hard_start_sample"]) / CANONICAL_RATE,
        "clip_start_sec": spec["seek_sample"] / CANONICAL_RATE,
        "clip_end_sec": spec["clip_end_sample"] / CANONICAL_RATE,
        "created_at": utc_now(),
        "provenance": origin,
        "remote": {
            "wav": rel(chunk_wav, session),
            "asr_wav": rel(chunk_wav, session),
            "audio_prep": "loudnorm",
            "hard_start_sec": spec["hard_start_sample"] / CANONICAL_RATE,
            "hard_end_sec": spec["hard_end_sample"] / CANONICAL_RATE,
            "clip_start_sec": spec["seek_sample"] / CANONICAL_RATE,
            "clip_end_sec": spec["clip_end_sample"] / CANONICAL_RATE,
            "preprocess_status": "passed",
            "asr": {
                "status": "passed",
                "elapsed_sec": execution["elapsed_sec"],
                "json": rel(json_path, session),
            },
            "batch_cache_compatibility": proof,
        },
    }
    return row, execution


def write_report(
    output: Path,
    *,
    status: str,
    reason: str | None,
    progress: dict[str, Any],
    parameters: dict[str, Any],
) -> None:
    payload = {
        "schema": REPORT_SCHEMA,
        "generator": {"name": "canonical-live-asr-producer", "version": SCRIPT_VERSION},
        "created_at": utc_now(),
        "status": status,
        "reason": reason,
        "scope": "remote_only_v1",
        "batch_authoritative": True,
        "promotion_allowed": False,
        "progress": progress,
        "parameters": parameters,
        "outputs": {
            "windows": "chunks.jsonl",
            "state": "state.json",
            "events": "events.jsonl",
        },
        "safety": {
            "raw_capture_modified": False,
            "capture_callback_work": False,
            "mic_live_origin_allowed": False,
            "fallback": "ordinary_post_stop_batch",
        },
    }
    authoritative_cache.atomic_write_json(output / "report.json", payload)
    lines = [
        "# Canonical Live ASR Producer",
        "",
        f"- Status: `{status}`",
        f"- Scope: `remote_only_v1`",
        f"- Proven chunks: `{progress.get('chunks_completed', 0)}/{progress.get('chunks_expected', 0)}`",
        f"- Proven seconds: `{progress.get('proven_sec', 0.0)}`",
        f"- Remaining seconds: `{progress.get('remaining_sec', 0.0)}`",
        f"- Reason: `{reason or 'none'}`",
        "- Raw capture modified: `false`",
        "- Batch authoritative: `true`",
        "",
        "`mic` remains an intentional batch fallback because its authoritative source is created by post-stop Echo Guard.",
    ]
    authoritative_cache.atomic_write_bytes(output / "report.md", ("\n".join(lines) + "\n").encode("utf-8"))


def main() -> int:
    args = parse_args()
    policy_report = apply_resource_policy(args.resource_policy)
    session = args.session.expanduser().resolve()
    output = session / "derived/experiments" / args.experiment / "authoritative-asr"
    output.mkdir(parents=True, exist_ok=True)
    prefix = output / "work/remote_committed_prefix.f32le"
    manifest_path = output / "chunks.jsonl"
    model = args.model
    whisper_raw = shutil.which(args.whisper_cli) or args.whisper_cli
    whisper_cli = Path(whisper_raw).expanduser().resolve()
    prompt = read_prompt(args.prompt_file)
    parameters = {
        "model": str(model),
        "whisper_cli": str(whisper_cli),
        "language": args.language,
        "threads": args.threads,
        "max_context": args.max_context,
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest() if prompt else None,
        "window_sec": args.window_sec,
        "overlap_sec": args.overlap_sec,
        "canonicalization_lookahead_sec": args.lookahead_sec,
        "audio_prep": "loudnorm",
        "resource_policy": policy_report,
        "replay_from_raw": args.replay_from_raw,
    }
    progress: dict[str, Any] = {
        "source_frames_committed": 0,
        "source_sec_committed": 0.0,
        "canonical_sec_ready": 0.0,
        "chunks_expected": 0,
        "chunks_completed": 0,
        "proven_sec": 0.0,
        "remaining_sec": 0.0,
        "decode_elapsed_sec": 0.0,
        "canonicalization_elapsed_sec": 0.0,
    }
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    if not model.exists() or not whisper_cli.exists():
        reason = "model_missing" if not model.exists() else "whisper_cli_missing"
        write_state(output, status="fallback", stage="preflight", progress=progress, reason=reason)
        write_report(output, status="fallback", reason=reason, progress=progress, parameters=parameters)
        return 0

    decode = authoritative_cache.decode_contract(
        language=args.language,
        threads=args.threads,
        max_context=args.max_context,
        prompt=prompt,
        duration_ms=0,
    )
    rows_by_index = {
        int(row.get("index") or 0): row
        for row in load_windows(manifest_path)
        if proof_is_valid(row, session=session, model=model, whisper_cli=whisper_cli, decode=decode)
    }
    source_signature: tuple[int, int, int] | None = None
    cached_metadata: dict[str, Any] | None = None
    cached_canonical: dict[str, Any] | None = None
    streamer: StreamingCanonicalizer | None = None
    final_seen_at: float | None = None
    status = "running"
    reason: str | None = None
    finalized = False
    append_event(output, event="started", parameters=parameters)

    while not STOP_REQUESTED:
        finalized = (session / "session.json").exists()
        if finalized and final_seen_at is None:
            final_seen_at = time.monotonic()
        try:
            if args.replay_from_raw:
                if not finalized:
                    raise RuntimeError("replay_requires_finalized_session")
                raw = session / "audio/remote/000001.caf"
                raw_stat = raw.stat()
                current_source_signature = (1, raw_stat.st_size, raw_stat.st_mtime_ns)
                if cached_metadata is None or current_source_signature != source_signature:
                    cached_metadata = rebuild_prefix_from_raw(session, prefix)
                    cached_canonical = canonicalize_prefix(prefix, cached_metadata, output)
                    progress["canonicalization_elapsed_sec"] = round(
                        float(progress["canonicalization_elapsed_sec"])
                        + float(cached_canonical["caf_elapsed_sec"])
                        + float(cached_canonical["export_elapsed_sec"])
                        + float(cached_canonical["prepare_elapsed_sec"]),
                        6,
                    )
                    source_signature = current_source_signature
                metadata = cached_metadata
                canonical = cached_canonical
                if canonical is None:
                    raise RuntimeError("replay_canonicalization_missing")
                prepared = Path(canonical["path"])
                available = int(canonical["format"]["frames"])
                prepared_is_raw_s16le = False
                segment_count = 1
            else:
                segment_rows, exact_reasons = exact_remote_rows(session)
                if exact_reasons:
                    raise RuntimeError(exact_reasons[0])
                if not segment_rows:
                    write_state(output, status="waiting", stage="waiting_for_exact_segments", progress=progress)
                    if args.once:
                        break
                    time.sleep(max(0.1, args.poll_sec))
                    continue
                if streamer is None:
                    first_path = Path(str(segment_rows[0]["path"]))
                    if not first_path.is_absolute():
                        first_path = session / first_path
                    with sf.SoundFile(str(first_path), "r") as first_audio:
                        streamer = StreamingCanonicalizer(
                            session,
                            output,
                            sample_rate=int(first_audio.samplerate),
                            channels=int(first_audio.channels),
                        )
                    append_event(
                        output,
                        event="stream_canonicalizer_started",
                        sample_rate=streamer.sample_rate,
                        channels=streamer.channels,
                    )
                append_elapsed = streamer.append(segment_rows)
                progress["canonicalization_elapsed_sec"] = round(
                    float(progress["canonicalization_elapsed_sec"]) + append_elapsed,
                    6,
                )
                if finalized and not streamer.closed:
                    progress["canonicalization_elapsed_sec"] = round(
                        float(progress["canonicalization_elapsed_sec"]) + streamer.finalize(),
                        6,
                    )
                    append_event(output, event="stream_canonicalizer_finalized")
                metadata = streamer.metadata()
                prepared = streamer.output_raw
                if finalized:
                    available = streamer.available_samples()
                else:
                    target_ready = max(
                        0,
                        int(round((float(metadata["duration_sec"]) - args.lookahead_sec) * CANONICAL_RATE)),
                    )
                    available = streamer.wait_for_samples(target_ready) if target_ready else streamer.available_samples()
                prepared_is_raw_s16le = True
                segment_count = len(segment_rows)
            progress["source_frames_committed"] = int(metadata["frames"])
            progress["source_sec_committed"] = round(float(metadata["duration_sec"]), 3)
            progress["canonical_sec_ready"] = round(available / CANONICAL_RATE, 3)

            total_source_sec = float(metadata["duration_sec"])
            specs = chunk_specs(available, window_sec=args.window_sec, overlap_sec=args.overlap_sec)
            eligible = [
                spec
                for spec in specs
                if spec["index"] not in rows_by_index
                and (
                    finalized
                    or (
                        spec["hard_end_sample"] - spec["hard_start_sample"] == args.window_sec * CANONICAL_RATE
                        and spec["clip_end_sample"] <= available
                    )
                )
            ]
            source_provenance = {
                "schema": metadata.get("schema"),
                "sha256": metadata.get("sha256"),
                "sample_rate": metadata.get("sample_rate"),
                "channels": metadata.get("channels"),
                "frames": metadata.get("frames"),
                "segments": metadata.get("segments") or [],
                "canonicalization": metadata.get("canonicalization") or "post_stop_two_stage_replay",
            }
            for spec in eligible:
                if STOP_REQUESTED:
                    break
                write_state(
                    output,
                    status="running",
                    stage=f"decoding_remote_chunk_{spec['index']}",
                    progress=progress,
                )
                row, execution = publish_window(
                    session=session,
                    output=output,
                    prepared_prefix=prepared,
                    spec=spec,
                    model=model,
                    whisper_cli=whisper_cli,
                    language=args.language,
                    threads=args.threads,
                    max_context=args.max_context,
                    prompt=prompt,
                    timeout_sec=args.whisper_timeout_sec,
                    origin="historical_replay" if args.replay_from_raw else "recording_time_committed_pcm",
                    prepared_is_raw_s16le=prepared_is_raw_s16le,
                    source_pcm=source_provenance,
                )
                rows_by_index[spec["index"]] = row
                write_windows(manifest_path, [rows_by_index[key] for key in sorted(rows_by_index)])
                progress["decode_elapsed_sec"] = round(
                    float(progress["decode_elapsed_sec"]) + float(execution["elapsed_sec"]),
                    6,
                )
                append_event(
                    output,
                    event="chunk_published",
                    index=spec["index"],
                    hard_start_sample=spec["hard_start_sample"],
                    hard_end_sample=spec["hard_end_sample"],
                    identity_sha256=row["remote"]["batch_cache_compatibility"]["identity_sha256"],
                )

            expected_total_frames = available if finalized else int(round(total_source_sec * CANONICAL_RATE))
            expected_specs = chunk_specs(expected_total_frames, window_sec=args.window_sec, overlap_sec=args.overlap_sec)
            progress["chunks_expected"] = len(expected_specs) if finalized else max(
                len(rows_by_index),
                int(total_source_sec // args.window_sec),
            )
            progress["chunks_completed"] = len(rows_by_index)
            progress["proven_sec"] = round(
                sum(float(row.get("duration_sec") or 0.0) for row in rows_by_index.values()),
                3,
            )
            progress["remaining_sec"] = round(
                max(0.0, total_source_sec - float(progress["proven_sec"])),
                3,
            )
            write_state(output, status="running", stage="waiting_for_committed_pcm", progress=progress)

            if finalized and available > 0 and len(rows_by_index) == len(expected_specs):
                status = "completed_replay" if args.replay_from_raw else "completed"
                break
            if args.once:
                status = "completed_partial" if rows_by_index else "waiting"
                reason = None if rows_by_index else "no_provable_window_available"
                break
            if finalized and final_seen_at is not None and time.monotonic() - final_seen_at >= args.idle_after_session_json_sec:
                status = "completed_partial"
                reason = "finalized_but_not_all_windows_proven"
                break
            time.sleep(max(0.1, args.poll_sec))
        except Exception as error:
            reason = f"{type(error).__name__}:{error}"
            if args.once or finalized:
                status = "fallback"
                append_event(output, event="failed_open", reason=reason)
                break
            write_state(output, status="waiting", stage="waiting_for_compatible_segments", progress=progress, reason=reason)
            time.sleep(max(0.1, args.poll_sec))

    if streamer is not None and not streamer.closed:
        streamer.terminate()
    if STOP_REQUESTED and status == "running":
        status = "completed_partial"
        reason = "terminated_before_all_windows_proven"
    write_state(output, status=status, stage="finished", progress=progress, reason=reason)
    write_report(output, status=status, reason=reason, progress=progress, parameters=parameters)
    append_event(output, event="finished", status=status, reason=reason, progress=progress)
    print(f"canonical_live_asr: {status}", flush=True)
    print(f"canonical_live_asr_report: {output / 'report.json'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
