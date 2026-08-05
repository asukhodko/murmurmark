#!/usr/bin/env python3
"""Exact identity and integrity helpers for authoritative whisper.cpp chunks."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import wave
from pathlib import Path
from typing import Any


IDENTITY_SCHEMA = "murmurmark.authoritative_asr_chunk_identity/v1"
CACHE_ENTRY_SCHEMA = "murmurmark.authoritative_asr_chunk_cache/v1"
RAW_CACHE_ENTRY_SCHEMA = "murmurmark.authoritative_asr_raw_cache/v1"
RECONCILIATION_POLICY = "hard_window_center_v1"


_FILE_SHA256_CACHE: dict[tuple[str, int, int], str] = {}
_FILE_SHA256_CACHE_LOCK = threading.Lock()


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def content_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def sha256_file(path: Path) -> str:
    resolved = path.expanduser().resolve()
    stat = resolved.stat()
    key = (str(resolved), stat.st_size, stat.st_mtime_ns)
    with _FILE_SHA256_CACHE_LOCK:
        cached = _FILE_SHA256_CACHE.get(key)
    if cached is not None:
        return cached
    digest = hashlib.sha256()
    with resolved.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    value = digest.hexdigest()
    with _FILE_SHA256_CACHE_LOCK:
        stale = [item for item in _FILE_SHA256_CACHE if item[0] == str(resolved)]
        for item in stale:
            _FILE_SHA256_CACHE.pop(item, None)
        _FILE_SHA256_CACHE[key] = value
    return value


def file_fingerprint(path: Path, *, include_path: bool = True) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    stat = resolved.stat()
    result: dict[str, Any] = {
        "bytes": stat.st_size,
        "sha256": sha256_file(resolved),
    }
    if include_path:
        result["path"] = str(resolved)
    return result


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    atomic_write_bytes(path, (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def wave_format(path: Path) -> dict[str, Any]:
    with wave.open(str(path), "rb") as audio:
        if audio.getcomptype() != "NONE":
            raise ValueError(f"compressed WAV is not canonical PCM: {path}")
        return {
            "sample_rate": audio.getframerate(),
            "channels": audio.getnchannels(),
            "sample_width_bytes": audio.getsampwidth(),
            "frames": audio.getnframes(),
            "encoding": f"pcm_s{audio.getsampwidth() * 8}le",
        }


def pcm_fingerprint(path: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    with wave.open(str(path), "rb") as audio:
        if audio.getcomptype() != "NONE":
            raise ValueError(f"compressed WAV is not canonical PCM: {path}")
        while True:
            frames = audio.readframes(65536)
            if not frames:
                break
            digest.update(frames)
        return {
            "sha256": digest.hexdigest(),
            "sample_rate": audio.getframerate(),
            "channels": audio.getnchannels(),
            "sample_width_bytes": audio.getsampwidth(),
            "frames": audio.getnframes(),
            "encoding": f"pcm_s{audio.getsampwidth() * 8}le",
        }


def slice_pcm_wav(source: Path, destination: Path, start_sample: int, end_sample: int) -> None:
    if start_sample < 0 or end_sample <= start_sample:
        raise ValueError(f"invalid PCM slice {start_sample}..{end_sample}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".wav", dir=destination.parent)
    os.close(descriptor)
    temporary_path = Path(temporary)
    try:
        with wave.open(str(source), "rb") as input_audio:
            if input_audio.getcomptype() != "NONE":
                raise ValueError(f"compressed WAV is not canonical PCM: {source}")
            total_frames = input_audio.getnframes()
            bounded_end = min(total_frames, end_sample)
            if start_sample >= bounded_end:
                raise ValueError(f"PCM slice begins after EOF: {start_sample} >= {total_frames}")
            input_audio.setpos(start_sample)
            frames = input_audio.readframes(bounded_end - start_sample)
            with wave.open(str(temporary_path), "wb") as output_audio:
                output_audio.setparams(input_audio.getparams())
                output_audio.writeframes(frames)
        os.replace(temporary_path, destination)
    finally:
        temporary_path.unlink(missing_ok=True)


def decode_contract(
    *,
    language: str,
    threads: int,
    max_context: int,
    prompt: str | None,
    duration_ms: int,
) -> dict[str, Any]:
    return {
        "language": language,
        "threads": threads,
        "max_context": max_context,
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest() if prompt else None,
        "duration_ms": duration_ms,
        "temperature": 0.0,
        "output_json": True,
        "output_json_full": True,
        "log_score": True,
        "suppress_nst": True,
        "suppress_regex": r"^(Редактор субтитров|Продолжение следует|Спасибо за просмотр|Субтитры.*)$",
        "context_policy": "isolated_window",
    }


def build_raw_cache_config(
    *,
    whisper_cli: Path,
    model: Path,
    language: str,
    threads: int,
    max_context: int,
    prompt: str | None,
    duration_ms: int,
    asr_mode: str,
    asr_window_sec: int,
    asr_overlap_sec: int,
    audio_prep: str,
    source_audio: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config: dict[str, Any] = {
        "schema": "murmurmark.whisper_cpp_raw_cache/v2",
        "model": str(model),
        "language": language,
        "max_context": max_context,
        "prompt": prompt,
        "duration_ms": duration_ms,
        "asr_mode": asr_mode,
        "asr_window_sec": asr_window_sec,
        "asr_overlap_sec": asr_overlap_sec,
        "audio_prep": audio_prep,
        "output_json_full": True,
        "log_score": True,
        "suppress_nst": True,
        "suppress_regex": r"^(Редактор субтитров|Продолжение следует|Спасибо за просмотр|Субтитры.*)$",
        "engine_identity": {
            "binary": file_fingerprint(whisper_cli, include_path=False),
            "model": file_fingerprint(model, include_path=False),
        },
        "decode_contract": decode_contract(
            language=language,
            threads=threads,
            max_context=max_context,
            prompt=prompt,
            duration_ms=duration_ms,
        ),
    }
    if source_audio is not None:
        config["source_audio"] = source_audio
    return config


def build_chunk_identity(
    *,
    track: str,
    role: str,
    spec: dict[str, int],
    chunk_wav: Path,
    model: Path,
    whisper_cli: Path,
    decode: dict[str, Any],
    audio_prep: str,
) -> dict[str, Any]:
    pcm = pcm_fingerprint(chunk_wav)
    sample_rate = int(pcm["sample_rate"])
    window = {
        "index": int(spec["index"]),
        "hard_start_sample": int(spec["hard_start_sample"]),
        "hard_end_sample": int(spec["hard_end_sample"]),
        "clip_start_sample": int(spec["seek_sample"]),
        "clip_end_sample": int(spec["clip_end_sample"]),
        "overlap_before_sample": int(spec["hard_start_sample"] - spec["seek_sample"]),
        "overlap_after_sample": int(spec["clip_end_sample"] - spec["hard_end_sample"]),
        "sample_rate": sample_rate,
        "reconciliation": RECONCILIATION_POLICY,
    }
    identity = {
        "schema": IDENTITY_SCHEMA,
        "track": track,
        "role": role,
        "audio_prep": audio_prep,
        "window": window,
        "pcm": pcm,
        "engine": {
            "name": "whisper.cpp",
            "binary": file_fingerprint(whisper_cli, include_path=False),
            "model": file_fingerprint(model, include_path=False),
        },
        "decode": decode,
    }
    return identity


def output_fingerprint(path: Path) -> dict[str, Any]:
    return file_fingerprint(path, include_path=False)


def build_chunk_cache_entry(
    *,
    identity: dict[str, Any],
    json_path: Path,
    origin: str,
    created_at: str,
    execution: dict[str, Any] | None = None,
    source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema": CACHE_ENTRY_SCHEMA,
        "completed": True,
        "identity": identity,
        "identity_sha256": content_sha256(identity),
        "output": {
            "json": output_fingerprint(json_path),
        },
        "provenance": {
            "origin": origin,
            "created_at": created_at,
            "execution": execution or {},
            "source": source or {},
        },
    }


def validate_chunk_cache(chunk_base: Path, expected_identity: dict[str, Any]) -> tuple[bool, str, dict[str, Any] | None]:
    json_path = chunk_base.with_suffix(".json")
    meta_path = chunk_base.with_suffix(".meta.json")
    wav_path = chunk_base.with_suffix(".wav")
    meta = read_json(meta_path)
    if meta is None:
        return False, "metadata_missing_or_invalid", None
    if meta.get("schema") != CACHE_ENTRY_SCHEMA or meta.get("completed") is not True:
        return False, "cache_entry_schema_or_completion_mismatch", meta
    identity = meta.get("identity")
    if not isinstance(identity, dict):
        return False, "identity_missing", meta
    identity_sha = content_sha256(identity)
    if meta.get("identity_sha256") != identity_sha:
        return False, "stored_identity_hash_mismatch", meta
    if identity_sha != content_sha256(expected_identity):
        return False, "canonical_identity_mismatch", meta
    if not wav_path.exists():
        return False, "canonical_pcm_missing", meta
    try:
        if pcm_fingerprint(wav_path) != expected_identity.get("pcm"):
            return False, "canonical_pcm_hash_mismatch", meta
    except (OSError, EOFError, wave.Error, ValueError):
        return False, "canonical_pcm_invalid", meta
    if not json_path.exists():
        return False, "json_missing", meta
    output = meta.get("output") if isinstance(meta.get("output"), dict) else {}
    expected_output = output.get("json") if isinstance(output.get("json"), dict) else None
    if expected_output != output_fingerprint(json_path):
        return False, "json_hash_mismatch", meta
    payload = read_json(json_path)
    if payload is None or not isinstance(payload.get("transcription"), list):
        return False, "json_payload_invalid", meta
    return True, "exact_identity_and_integrity_match", meta


def build_raw_cache_entry(*, config: dict[str, Any], json_path: Path, created_at: str) -> dict[str, Any]:
    return {
        "schema": RAW_CACHE_ENTRY_SCHEMA,
        "completed": True,
        "config": config,
        "config_sha256": content_sha256(config),
        "output": {"json": output_fingerprint(json_path)},
        "created_at": created_at,
    }


def validate_raw_cache(output_base: Path, expected_config: dict[str, Any]) -> tuple[bool, str]:
    json_path = output_base.with_suffix(".json")
    meta = read_json(output_base.with_suffix(".meta.json"))
    if meta is None:
        return False, "metadata_missing_or_invalid"
    if meta.get("schema") != RAW_CACHE_ENTRY_SCHEMA or meta.get("completed") is not True:
        return False, "raw_cache_schema_or_completion_mismatch"
    config = meta.get("config")
    if not isinstance(config, dict):
        return False, "raw_cache_config_missing"
    if meta.get("config_sha256") != content_sha256(config):
        return False, "raw_cache_config_hash_mismatch"
    if content_sha256(config) != content_sha256(expected_config):
        return False, "raw_cache_config_mismatch"
    if not json_path.exists():
        return False, "raw_json_missing"
    output = meta.get("output") if isinstance(meta.get("output"), dict) else {}
    expected_output = output.get("json") if isinstance(output.get("json"), dict) else None
    if expected_output != output_fingerprint(json_path):
        return False, "raw_json_hash_mismatch"
    payload = read_json(json_path)
    if payload is None or not isinstance(payload.get("transcription"), list):
        return False, "raw_json_invalid"
    return True, "exact_config_and_integrity_match"
