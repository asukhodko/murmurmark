#!/usr/bin/env python3
"""Shared helpers for Controlled Echo Supervision Lab v1."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
import soundfile as sf
from scipy import signal
from scipy.io import wavfile


SCHEMA_POLICY = "murmurmark.controlled_echo_supervision_policy/v1"
SCHEMA_SCHEDULE = "murmurmark.controlled_echo_schedule/v1"
SCHEMA_CAPTURE = "murmurmark.controlled_echo_capture/v1"
SCHEMA_INSPECTION = "murmurmark.controlled_echo_inspection/v1"
SCHEMA_PHASE = "murmurmark.controlled_echo_phase/v1"
SCHEMA_FROZEN_CORPUS = "murmurmark.controlled_echo_frozen_corpus/v1"
SCHEMA_SUPERVISION = "murmurmark.controlled_echo_supervision_item/v1"
SCHEMA_DECISION = "murmurmark.controlled_echo_corpus_decision/v1"

ANALYSIS_SAMPLE_RATE = 16_000
CONTENT_TOKEN_RE = re.compile(r"[0-9a-zа-яё]+", re.IGNORECASE)

REMOTE_TTS_TEXT = (
    "Сегодня мы проверяем устойчивую передачу речи через обычные динамики. "
    "Цифровой источник произносит нейтральные фразы с разной длиной и паузами. "
    "Система должна измерить акустический путь, задержку и остаточное эхо. "
    "Эти предложения не относятся к рабочим встречам и не содержат личных данных. "
    "После короткой паузы чтение продолжается в спокойном темпе. "
    "Разные сочетания согласных и гласных помогают проверить разборчивость речи. "
    "Надёжный эксперимент сохраняет исходный звук и исключает догадки о разметке. "
)

LOCAL_ONLY_PROMPTS = (
    "Проверяю чистую локальную речь",
    "Сегодня обычный спокойный день",
    "Короткая фраза звучит отчётливо",
    "Я говорю в микрофон без спешки",
    "Нужно сохранить каждое моё слово",
    "Локальная речь не должна пропасть",
    "Проверка согласных и гласных звуков",
    "Теперь произношу предложение длиннее",
    "Мой голос остаётся главным сигналом",
    "Фраза завершается небольшой паузой",
)

DOUBLE_TALK_PROMPTS = (
    "Я продолжаю говорить поверх собеседника",
    "Мою короткую реплику нужно сохранить",
    "Одновременная речь проверяет защиту голоса",
    "Да, я слышу и отвечаю сейчас",
    "Локальная фраза важнее остаточного эха",
    "Этот ответ произнесён во время воспроизведения",
)

OPENING_PROMPTS = (
    "Привет",
    "Привет, да",
    "Меня слышно",
    "Да, слышно",
    "Ага",
    "Угу",
    "Окей",
    "Ладно, давай начнём",
)

PROMPT_SETS = {
    "local_only": LOCAL_ONLY_PROMPTS,
    "double_talk": DOUBLE_TALK_PROMPTS,
    "opening_backchannel": OPENING_PROMPTS,
}


@dataclass(frozen=True)
class AudioData:
    samples: np.ndarray
    sample_rate: int


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def default_policy_path() -> Path:
    return repo_root() / "policies" / "controlled-echo-supervision-v1.json"


def default_sessions_root() -> Path:
    return repo_root() / "sessions"


def default_report_dir(sessions_root: Path) -> Path:
    return sessions_root / "_reports" / "controlled-echo-supervision-v1"


def default_lab_root(sessions_root: Path) -> Path:
    return sessions_root / "_echo_lab" / "controlled-echo-supervision-v1"


def default_model_path() -> Path:
    configured = os.environ.get("MURMURMARK_FASTER_WHISPER_MODEL")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".local" / "share" / "murmurmark" / "models" / "faster-whisper" / "large-v3"


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def relative(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path.resolve())


def fingerprint(path: Path, root: Path) -> dict[str, Any]:
    return {
        "path": relative(path, root),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
    }


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return payload


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise RuntimeError(f"expected JSON object at {path}:{number}")
        rows.append(payload)
    return rows


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def stable_id(*parts: Any, length: int = 20) -> str:
    digest = hashlib.sha256("\x1f".join(str(part) for part in parts).encode("utf-8")).hexdigest()
    return digest[:length]


def load_policy(path: Path) -> dict[str, Any]:
    policy = read_json(path)
    if policy.get("schema") != SCHEMA_POLICY:
        raise RuntimeError(f"unsupported controlled echo policy schema: {policy.get('schema')}")
    phase_ids = [str(row.get("id") or "") for row in policy.get("phases", [])]
    if not phase_ids or len(phase_ids) != len(set(phase_ids)):
        raise RuntimeError("controlled echo policy phase IDs must be non-empty and unique")
    if not policy.get("scenarios"):
        raise RuntimeError("controlled echo policy has no scenarios")
    return policy


def policy_sha(path: Path) -> str:
    return sha256(path)


def total_duration(policy: dict[str, Any]) -> float:
    return float(sum(float(row["duration_sec"]) for row in policy["phases"]))


def build_schedule(policy: dict[str, Any]) -> list[dict[str, Any]]:
    cursor = 0.0
    rows: list[dict[str, Any]] = []
    for index, phase in enumerate(policy["phases"], 1):
        duration = float(phase["duration_sec"])
        row = dict(phase)
        row.update(
            {
                "schema": SCHEMA_PHASE,
                "index": index,
                "planned_start_sec": round(cursor, 6),
                "planned_end_sec": round(cursor + duration, 6),
            }
        )
        rows.append(row)
        cursor += duration
    return rows


def validate_schedule(schedule: Sequence[dict[str, Any]], expected_total: float) -> None:
    cursor = 0.0
    for row in schedule:
        start = float(row["planned_start_sec"])
        end = float(row["planned_end_sec"])
        if abs(start - cursor) > 1.0e-9:
            raise RuntimeError(f"phase schedule gap or overlap before {row.get('id')}")
        if end <= start:
            raise RuntimeError(f"invalid phase duration for {row.get('id')}")
        cursor = end
    if abs(cursor - expected_total) > 1.0e-9:
        raise RuntimeError(f"phase schedule total mismatch: {cursor} != {expected_total}")


def prompts_for_phase(phase: dict[str, Any]) -> list[dict[str, Any]]:
    prompt_name = phase.get("prompt_set")
    if not prompt_name:
        return []
    prompts = PROMPT_SETS.get(str(prompt_name))
    if not prompts:
        raise RuntimeError(f"unknown prompt set: {prompt_name}")
    interval = float(phase.get("prompt_interval_sec") or 4.0)
    start = float(phase["planned_start_sec"])
    end = float(phase["planned_end_sec"])
    rows: list[dict[str, Any]] = []
    offset = interval
    index = 0
    while start + offset < end - 0.5:
        text = prompts[index % len(prompts)]
        rows.append(
            {
                "prompt_id": stable_id(phase["id"], index, text),
                "phase_id": phase["id"],
                "planned_at_sec": round(start + offset, 6),
                "text": text,
                "text_sha256": hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest(),
            }
        )
        index += 1
        offset += interval
    return rows


def normalize_text(text: Any) -> str:
    return " ".join(CONTENT_TOKEN_RE.findall(str(text or "").lower().replace("ё", "е")))


def tokens(text: Any) -> list[str]:
    return [token for token in normalize_text(text).split() if token]


def token_recall(expected: Any, observed: Any) -> float:
    expected_tokens = tokens(expected)
    observed_tokens = set(tokens(observed))
    if not expected_tokens:
        return 1.0
    return sum(1 for token in expected_tokens if token in observed_tokens) / len(expected_tokens)


def unique_token_ratio(candidate: Any, reference: Any) -> float:
    candidate_tokens = tokens(candidate)
    if not candidate_tokens:
        return 0.0
    reference_tokens = set(tokens(reference))
    return sum(1 for token in candidate_tokens if token not in reference_tokens) / len(candidate_tokens)


def command_path(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise RuntimeError(f"required command not found: {name}")
    return path


def run_checked(args: Sequence[str], *, capture_output: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        check=True,
        text=True,
        capture_output=capture_output,
    )


def convert_audio(source: Path, destination: Path, sample_rate: int = ANALYSIS_SAMPLE_RATE) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    run_checked(
        [
            command_path("ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-ac",
            "1",
            "-ar",
            str(sample_rate),
            "-c:a",
            "pcm_f32le",
            str(destination),
        ]
    )


def read_audio(path: Path, expected_rate: int = ANALYSIS_SAMPLE_RATE) -> AudioData:
    audio, sample_rate = sf.read(path, dtype="float32", always_2d=False)
    if sample_rate != expected_rate:
        raise RuntimeError(f"unexpected sample rate for {path}: {sample_rate} != {expected_rate}")
    if np.asarray(audio).ndim == 2:
        audio = np.mean(np.asarray(audio), axis=1)
    return AudioData(np.asarray(audio, dtype=np.float32), sample_rate)


def read_audio_slice(
    path: Path,
    start_sec: float,
    end_sec: float,
    expected_rate: int = ANALYSIS_SAMPLE_RATE,
) -> np.ndarray:
    start = max(0, int(round(start_sec * expected_rate)))
    end = max(start, int(round(end_sec * expected_rate)))
    with sf.SoundFile(path) as handle:
        if handle.samplerate != expected_rate:
            raise RuntimeError(f"unexpected sample rate for {path}: {handle.samplerate}")
        handle.seek(start)
        audio = handle.read(end - start, dtype="float32", always_2d=False)
    array = np.asarray(audio, dtype=np.float32)
    if array.ndim == 2:
        array = np.mean(array, axis=1)
    return array


def write_audio(path: Path, audio: np.ndarray, sample_rate: int = ANALYSIS_SAMPLE_RATE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    wavfile.write(path, sample_rate, np.asarray(audio, dtype=np.float32))


def materialize_looped_stimulus(
    source: Path,
    destination: Path,
    *,
    duration_sec: float,
    sample_rate: int = 48_000,
) -> None:
    audio, source_rate = sf.read(source, dtype="float32", always_2d=True)
    mono = np.mean(np.asarray(audio, dtype=np.float32), axis=1)
    if not mono.size:
        raise RuntimeError(f"empty stimulus source: {source}")
    if not np.all(np.isfinite(mono)):
        raise RuntimeError(f"non-finite stimulus source: {source}")
    if int(source_rate) != sample_rate:
        divisor = math.gcd(int(source_rate), sample_rate)
        mono = np.asarray(
            signal.resample_poly(
                mono,
                sample_rate // divisor,
                int(source_rate) // divisor,
            ),
            dtype=np.float32,
        )
    expected_frames = int(round(duration_sec * sample_rate))
    if expected_frames <= 0:
        raise RuntimeError(f"invalid stimulus duration: {duration_sec}")
    repeats = (expected_frames + mono.size - 1) // mono.size
    looped = np.tile(mono, repeats)[:expected_frames]
    peak = float(np.max(np.abs(looped)))
    if peak > 1.0:
        raise RuntimeError(f"stimulus source clips after resampling: peak={peak:.6f}")
    pcm = np.rint(looped.astype(np.float64) * 32_767.0).astype(np.int16)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(
        f".{destination.stem}.{os.getpid()}.partial{destination.suffix}"
    )
    try:
        wavfile.write(temporary, sample_rate, pcm)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def validate_stimulus_audio(
    path: Path,
    *,
    duration_sec: float,
    sample_rate: int,
    maximum_peak: float,
) -> dict[str, Any]:
    expected_frames = int(round(duration_sec * sample_rate))
    peak = 0.0
    finite = True
    with sf.SoundFile(path) as handle:
        channels = int(handle.channels)
        actual_rate = int(handle.samplerate)
        frames = int(handle.frames)
        while True:
            chunk = np.asarray(
                handle.read(65_536, dtype="float32", always_2d=False),
                dtype=np.float32,
            )
            if not chunk.size:
                break
            finite = finite and bool(np.all(np.isfinite(chunk)))
            if finite:
                peak = max(peak, float(np.max(np.abs(chunk))))
    reasons: list[str] = []
    if channels != 1:
        reasons.append("stimulus_not_mono")
    if actual_rate != sample_rate:
        reasons.append("stimulus_sample_rate")
    if frames != expected_frames:
        reasons.append("stimulus_duration")
    if not finite:
        reasons.append("stimulus_non_finite")
    if peak > maximum_peak:
        reasons.append("stimulus_clipping")
    if reasons:
        raise RuntimeError(f"invalid stimulus {path}: {', '.join(reasons)}")
    return {
        "channels": channels,
        "sample_rate": actual_rate,
        "frames": frames,
        "duration_sec": round(frames / actual_rate, 6),
        "finite": finite,
        "peak": round(peak, 9),
    }


def rms_db(audio: np.ndarray) -> float:
    array = np.asarray(audio, dtype=np.float64)
    if not array.size:
        return -240.0
    value = math.sqrt(float(np.mean(array * array)) + 1.0e-24)
    return 20.0 * math.log10(value + 1.0e-12)


def normalized_correlation(left: np.ndarray, right: np.ndarray) -> float:
    count = min(left.size, right.size)
    if count < 2:
        return 0.0
    a = np.asarray(left[:count], dtype=np.float64)
    b = np.asarray(right[:count], dtype=np.float64)
    a -= float(np.mean(a))
    b -= float(np.mean(b))
    denominator = math.sqrt(float(np.dot(a, a) * np.dot(b, b)))
    if denominator <= 1.0e-18:
        return 0.0
    return float(np.dot(a, b) / denominator)


def _rms_envelope(audio: np.ndarray, sample_rate: int, frame_ms: int = 10) -> np.ndarray:
    frame = max(1, int(round(sample_rate * frame_ms / 1000.0)))
    count = audio.size // frame
    if count == 0:
        return np.zeros(0, dtype=np.float64)
    trimmed = np.asarray(audio[: count * frame], dtype=np.float64).reshape(count, frame)
    return np.sqrt(np.mean(trimmed * trimmed, axis=1) + 1.0e-18)


def lagged_correlation(
    mic: np.ndarray,
    remote: np.ndarray,
    sample_rate: int,
    maximum_lag_ms: float,
) -> dict[str, Any]:
    mic_env = _rms_envelope(mic, sample_rate)
    remote_env = _rms_envelope(remote, sample_rate)
    count = min(mic_env.size, remote_env.size)
    if count < 20:
        return {"lag_ms": 0.0, "correlation": 0.0}
    mic_env = mic_env[:count] - float(np.mean(mic_env[:count]))
    remote_env = remote_env[:count] - float(np.mean(remote_env[:count]))
    correlations = signal.correlate(mic_env, remote_env, mode="full", method="fft")
    lags = signal.correlation_lags(mic_env.size, remote_env.size, mode="full")
    max_frames = int(round(maximum_lag_ms / 10.0))
    allowed = (lags >= -max_frames) & (lags <= max_frames)
    if not np.any(allowed):
        return {"lag_ms": 0.0, "correlation": 0.0}
    candidate_corr = correlations[allowed]
    candidate_lags = lags[allowed]
    index = int(np.argmax(candidate_corr))
    lag_frames = int(candidate_lags[index])
    if lag_frames >= 0:
        left = mic_env[lag_frames:]
        right = remote_env[: left.size]
    else:
        right = remote_env[-lag_frames:]
        left = mic_env[: right.size]
    return {
        "lag_ms": float(lag_frames * 10),
        "correlation": float(max(0.0, normalized_correlation(left, right))),
    }


def shift_remote_for_mic(remote: np.ndarray, lag_ms: float, sample_rate: int) -> np.ndarray:
    shift = int(round(lag_ms * sample_rate / 1000.0))
    output = np.zeros_like(remote, dtype=np.float32)
    if 0 <= shift < remote.size:
        output[shift:] = remote[: remote.size - shift]
    elif -remote.size < shift < 0:
        output[: remote.size + shift] = remote[-shift:]
    return output


def audio_metrics(
    mic: np.ndarray,
    remote: np.ndarray,
    sample_rate: int,
    maximum_lag_ms: float,
) -> dict[str, Any]:
    count = min(mic.size, remote.size)
    mic = np.asarray(mic[:count], dtype=np.float32)
    remote = np.asarray(remote[:count], dtype=np.float32)
    finite = bool(np.all(np.isfinite(mic)) and np.all(np.isfinite(remote)))
    lag = lagged_correlation(mic, remote, sample_rate, maximum_lag_ms) if finite else {
        "lag_ms": 0.0,
        "correlation": 0.0,
    }
    return {
        "samples": int(count),
        "duration_sec": round(count / sample_rate, 6),
        "finite": finite,
        "mic_peak": round(float(np.max(np.abs(mic))) if count else 0.0, 9),
        "remote_peak": round(float(np.max(np.abs(remote))) if count else 0.0, 9),
        "mic_clipped_ratio": round(float(np.mean(np.abs(mic) >= 0.995)) if count else 0.0, 12),
        "remote_clipped_ratio": round(float(np.mean(np.abs(remote) >= 0.995)) if count else 0.0, 12),
        "mic_rms_db": round(rms_db(mic), 6),
        "remote_rms_db": round(rms_db(remote), 6),
        "zero_ratio_mic": round(float(np.mean(np.abs(mic) < 1.0e-12)) if count else 1.0, 12),
        "zero_ratio_remote": round(float(np.mean(np.abs(remote) < 1.0e-12)) if count else 1.0, 12),
        "lag_ms": round(float(lag["lag_ms"]), 6),
        "lagged_correlation": round(float(lag["correlation"]), 9),
    }


def phase_bounds(phase: dict[str, Any], trim_sec: float) -> tuple[float, float]:
    start = float(phase["planned_start_sec"]) + trim_sec
    end = float(phase["planned_end_sec"]) - trim_sec
    if end <= start:
        raise RuntimeError(f"phase too short after trim: {phase.get('id')}")
    return start, end


def remote_only_gate_reasons(
    metrics: dict[str, Any],
    evidence: dict[str, Any],
    validation: dict[str, Any],
) -> list[str]:
    reasons: list[str] = []
    if float(metrics["remote_rms_db"]) < float(validation["remote_active_min_rms_db"]):
        reasons.append("remote_inactive")
    if float(metrics["mic_rms_db"]) < float(validation["measured_echo_min_rms_db"]):
        reasons.append("measured_echo_too_quiet")
    if float(metrics["lagged_correlation"]) < float(validation["measured_echo_min_lagged_correlation"]):
        reasons.append("measured_echo_not_correlated")
    if float(evidence["remote_stimulus_token_recall"]) < float(
        validation["remote_stimulus_min_token_recall"]
    ):
        reasons.append("remote_stimulus_not_confirmed")
    if float(evidence["remote_stimulus_unique_token_ratio"]) > float(
        validation["remote_stimulus_max_unique_token_ratio"]
    ):
        reasons.append("unknown_remote_audio")
    if float(evidence["mic_unique_token_ratio_against_remote"]) > float(
        validation["remote_only_max_unique_mic_token_ratio"]
    ):
        reasons.append("remote_only_local_speech_suspected")
    if evidence.get("target_me_remote_contamination") is not False:
        reasons.append("remote_only_target_me_contamination")
    return reasons


def local_only_gate_reasons(
    metrics: dict[str, Any],
    evidence: dict[str, Any],
    validation: dict[str, Any],
) -> list[str]:
    reasons: list[str] = []
    if float(metrics["remote_rms_db"]) > float(validation["remote_silent_max_rms_db"]):
        reasons.append("local_only_remote_not_silent")
    if float(metrics["mic_rms_db"]) < float(validation["local_speech_min_rms_db"]):
        reasons.append("local_speech_too_quiet")
    if float(evidence["prompt_token_recall"]) < float(validation["local_prompt_min_token_recall"]):
        reasons.append("local_prompt_recall")
    target_policy = validation["target_me"]
    if float(evidence.get("target_me_local_similarity") or 0.0) < float(
        target_policy["minimum_local_similarity"]
    ):
        reasons.append("target_me_local_not_confirmed")
    if int(evidence.get("target_me_validation_chunks") or 0) < int(
        target_policy["minimum_local_validation_chunks"]
    ):
        reasons.append("target_me_local_chunks")
    return reasons


def expected_prompt_text(phase: dict[str, Any]) -> str:
    return " ".join(row["text"] for row in prompts_for_phase(phase))


def make_clip_rows(
    *,
    audio_path: Path,
    phase: dict[str, Any],
    session_id: str,
    split: str,
    kind: str,
    clip_duration_sec: float,
    clip_hop_sec: float,
    trim_sec: float,
    root: Path,
) -> list[dict[str, Any]]:
    start, end = phase_bounds(phase, trim_sec)
    rows: list[dict[str, Any]] = []
    cursor = start
    while cursor + clip_duration_sec <= end + 1.0e-9:
        clip_end = cursor + clip_duration_sec
        rows.append(
            {
                "clip_id": stable_id(session_id, phase["id"], kind, f"{cursor:.6f}", f"{clip_end:.6f}"),
                "session_id": session_id,
                "split": split,
                "kind": kind,
                "phase_id": phase["id"],
                "source": relative(audio_path, root),
                "start_sec": round(cursor, 6),
                "end_sec": round(clip_end, 6),
                "duration_sec": round(clip_duration_sec, 6),
            }
        )
        cursor += clip_hop_sec
    return rows


def deterministic_tree_manifest(root: Path, *, exclude: set[str] | None = None) -> list[dict[str, Any]]:
    excluded = exclude or set()
    rows: list[dict[str, Any]] = []
    if not root.exists():
        return rows
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        rel = str(path.relative_to(root))
        if rel in excluded:
            continue
        rows.append({"path": rel, "bytes": path.stat().st_size, "sha256": sha256(path)})
    return rows


def verify_fingerprints(rows: Sequence[dict[str, Any]], root: Path) -> list[str]:
    errors: list[str] = []
    for row in rows:
        path = root / str(row["path"])
        if not path.is_file():
            errors.append(f"missing:{row['path']}")
            continue
        if int(row.get("bytes", -1)) != path.stat().st_size:
            errors.append(f"bytes:{row['path']}")
        if str(row.get("sha256")) != sha256(path):
            errors.append(f"sha256:{row['path']}")
    return errors


def safe_output_dir(path: Path, sessions_root: Path) -> None:
    resolved = path.resolve()
    expected_parent = (sessions_root / "_reports").resolve()
    if expected_parent not in resolved.parents:
        raise RuntimeError(f"refusing to replace report directory outside {expected_parent}: {resolved}")


def copy_tree_atomic(source: Path, destination: Path, sessions_root: Path) -> None:
    safe_output_dir(destination, sessions_root)
    temporary = destination.with_name(destination.name + ".next")
    if temporary.exists():
        shutil.rmtree(temporary)
    shutil.copytree(source, temporary)
    if destination.exists():
        shutil.rmtree(destination)
    temporary.rename(destination)
