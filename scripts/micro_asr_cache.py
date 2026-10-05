"""Candidate relative-decode cache; not wired into the frozen production ASR."""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import shutil
import tempfile
import threading
import time
import uuid
import wave
from pathlib import Path
from typing import Any, Callable

import authoritative_asr_cache as artifacts
from murmurmark_deadline import check_deadline


SCHEMA = "murmurmark.micro_asr_decode_cache/v1"
SUFFIXES = (".json", ".txt", ".score.txt", ".run.log")
_identities: dict[tuple[Any, ...], str] = {}
_identity_lock = threading.Lock()


def digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def file_identity(path: Path) -> str:
    path = path.resolve()
    stat = path.stat()
    key = (str(path), stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
    with _identity_lock:
        if key not in _identities:
            _identities[key] = digest(path)
        return _identities[key]


def pcm_identity(path: Path) -> dict[str, Any]:
    try:
        stream = wave.open(str(path), "rb")
    except (wave.Error, EOFError) as error:
        raise ValueError("invalid prepared micro-ASR PCM") from error
    with stream as audio:
        result = {
            "rate": audio.getframerate(), "channels": audio.getnchannels(),
            "sample_width": audio.getsampwidth(), "frames": audio.getnframes(),
            "compression": audio.getcomptype(),
        }
        checksum = hashlib.sha256()
        frames = 0
        while block := audio.readframes(65536):
            checksum.update(block)
            frames += len(block) // (audio.getnchannels() * audio.getsampwidth())
        if frames != audio.getnframes():
            raise ValueError("incomplete prepared micro-ASR PCM")
        result["sha256"] = checksum.hexdigest()
        return result


def valid_decode(decoded: Any) -> bool:
    return (
        isinstance(decoded, dict) and isinstance(decoded.get("transcription"), list)
        and all(isinstance(row, dict) and isinstance(row.get("text"), str)
                and isinstance(row.get("offsets"), dict)
                and all(isinstance(row["offsets"].get(key), (int, float))
                        and not isinstance(row["offsets"][key], bool)
                        and math.isfinite(row["offsets"][key]) for key in ("from", "to"))
                and 0 <= row["offsets"]["from"] <= row["offsets"]["to"]
                for row in decoded["transcription"])
    )


def decode_config(command: list[str], pcm: dict[str, Any]) -> dict[str, Any]:
    options = []
    input_files = {}
    index = 1
    while index < len(command):
        if command[index] in {"--prompt-file", "--vad-model"}:
            flag = command[index]
            input_files[flag] = file_identity(Path(command[index + 1]))
            index += 2
        elif command[index] in {"--file", "--output-file", "--model"}:
            index += 2
        else:
            options.append(command[index])
            index += 1
    executable = Path(shutil.which(command[0]) or command[0])
    return {
        "schema": SCHEMA, "pcm": pcm,
        "model_sha256": file_identity(Path(command[command.index("--model") + 1])),
        "executable_sha256": file_identity(executable),
        "options": options,
        "input_files": input_files,
        "environment": {key: value for key, value in sorted(os.environ.items())
                        if key.startswith(("GGML_", "WHISPER_"))},
    }


def valid_entry(directory: Path, key: str) -> dict[str, Any] | None:
    try:
        payload = json.loads((directory / "completion.json").read_text())
        if not isinstance(payload, dict) or payload.get("schema") != SCHEMA or payload.get("decode_key") != key:
            return None
        config = payload.get("config")
        if not isinstance(config, dict) or hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest() != key:
            return None
        files = payload["artifacts"]
        if not isinstance(files, dict) or ".json" not in files or not isinstance(payload.get("execution"), dict):
            return None
        # A CPU fallback is not evidence for the requested GPU configuration.
        execution_mode = payload["execution"].get("mode")
        if execution_mode == "cpu_fallback" or (execution_mode == "cpu" and "--no-gpu" not in config["options"]):
            return None
        generation = str(payload.get("generation") or "")
        if generation and (Path(generation).name != generation or generation in {".", ".."}):
            return None
        source = directory / generation
        for suffix, checksum in files.items():
            if suffix not in SUFFIXES or digest(source / f"decode{suffix}") != checksum:
                return None
        decoded = json.loads((source / "decode.json").read_text())
        if not valid_decode(decoded):
            return None
        return payload
    except (OSError, ValueError, KeyError, TypeError):
        return None


def materialize(
    command: list[str], output_base: Path, *, force: bool,
    decode: Callable[[list[str], Path], dict[str, Any]],
    cache_root: Path | None = None,
) -> dict[str, Any]:
    check_deadline()
    pcm = pcm_identity(Path(command[command.index("--file") + 1]))
    config = decode_config(command, pcm)
    key = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    cache = cache_root or output_base.parent / ".decode-cache-v1"
    cache.mkdir(parents=True, exist_ok=True)
    output_base.parent.mkdir(parents=True, exist_ok=True)
    entry = cache / key
    # flock also serializes independent processes; interrupted owners release it.
    with (cache / f"{key}.lock").open("a") as lock:
        while True:
            check_deadline()
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(0.05)
        check_deadline()
        if decode_config(command, pcm_identity(Path(command[command.index("--file") + 1]))) != config:
            raise ValueError("micro-ASR inputs changed while waiting for cache lock")
        payload = None if force else valid_entry(entry, key)
        hit = payload is not None
        if payload is None:
            with tempfile.TemporaryDirectory(prefix=f".{key}.", dir=cache) as temporary:
                stage = Path(temporary)
                staged_base = stage / "decode"
                invocation = list(command)
                invocation[invocation.index("--output-file") + 1] = str(staged_base)
                execution = decode(invocation, staged_base.with_suffix(".run.log"))
                decoded = json.loads(staged_base.with_suffix(".json").read_text())
                if not valid_decode(decoded):
                    raise ValueError("Whisper produced an incomplete micro-ASR result")
                check_deadline()
                if decode_config(command, pcm_identity(Path(command[command.index("--file") + 1]))) != config:
                    raise ValueError("micro-ASR inputs changed during decoding")
                generation = uuid.uuid4().hex
                payload = {
                    "schema": SCHEMA, "decode_key": key, "config": config,
                    "generation": generation,
                    "execution": execution,
                    "artifacts": {suffix: digest(staged_base.with_suffix(suffix))
                                  for suffix in SUFFIXES if staged_base.with_suffix(suffix).is_file()},
                }
                entry.mkdir(parents=True, exist_ok=True)
                os.replace(stage, entry / generation)
                try:
                    artifacts.atomic_write_json(entry / "completion.json", payload)
                except BaseException:
                    shutil.rmtree(entry / generation)
                    raise
                for old in entry.iterdir():
                    if old.is_dir() and old.name != generation:
                        shutil.rmtree(old)
        source = entry / str(payload.get("generation") or "")
        check_deadline()
        # JSON is the consumer's completion marker. Publish it after sidecars.
        for suffix in (*SUFFIXES[1:], SUFFIXES[0]):
            destination = output_base.with_suffix(suffix)
            if suffix not in payload["artifacts"]:
                destination.unlink(missing_ok=True)
                continue
            temporary = destination.with_name(f".{destination.name}.{os.getpid()}.{threading.get_ident()}.tmp")
            try:
                shutil.copyfile(source / f"decode{suffix}", temporary)
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
        execution = dict(payload["execution"])
        if hit:
            execution = {"mode": "slice_content_cache", "original_execution": execution}
        execution.update({"decode_key": key, "pcm_sha256": pcm["sha256"],
                          "cached_output_base": str(source / "decode"), "cache_hit": hit})
        return execution
