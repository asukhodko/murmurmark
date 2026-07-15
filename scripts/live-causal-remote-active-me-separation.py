#!/usr/bin/env python3
"""Build remote-conditioned causal Me candidates from committed live PCM.

The experiment is intentionally shadow-only. Candidate selection uses live artifacts produced at
recording time; authoritative batch text and timing are evaluated only by the focused reporter.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

import numpy as np
import soundfile as sf
from scipy import linalg, signal

from live_recovery_incremental_cache import (
    FileDigestMemo,
    algorithm_fingerprint,
    chunk_plan,
    content_hash,
    context_fingerprint,
    object_directory,
    payload_file_fingerprints,
    read_json as read_cache_json,
    relative_or_absolute,
    resolve_cached_path,
    write_json as write_cache_json,
)


SCHEMA = "murmurmark.live_causal_remote_active_me_separation/v1"
SCRIPT_VERSION = "1.1.0"
OUTPUT_RELATIVE = Path("derived/live/causal-remote-active-me-separation-v1")
BASELINE_PROFILE = (
    "online_live_me_remote_overlap_filter_live_boundary_split_retime_causal_remote_energy_"
    "local_island_micro_asr_v2"
)
METHODS = ("causal_fir_v1", "spectral_projection_v1", "hybrid_fir_spectral_v1")
SAMPLE_RATE = 16_000
EPSILON = 1.0e-12
MIN_TRAINING_SECONDS = 4.0
MAX_TRAINING_SECONDS = 30.0
MAX_CANDIDATE_SECONDS = 12.0
GROUP_GAP_SECONDS = 0.30
CONTEXT_BEFORE_SECONDS = 0.60
CONTEXT_AFTER_SECONDS = 0.40
LEADING_SILENCE_SECONDS = 0.40
MIN_ASR_SCORE = 0.58
MIN_SOURCE_ALIGNMENT = 0.22
MAX_REMOTE_TEXT_SIMILARITY = 0.24
MAX_REMOTE_TOKEN_RECALL = 0.20
MAX_REMOTE_AUDIO_STRENGTH = 0.30
REMOTE_ACTIVE_MIN_DB = -65.0
MIN_PROJECTION_REDUCTION_DB = 0.75
MAX_RESIDUAL_ENERGY_LOSS_DB = 30.0
MAX_RESIDUAL_GAIN_DB = 3.0
REMOTE_CONTENT_STOPWORDS = {
    "а",
    "бы",
    "в",
    "вот",
    "да",
    "для",
    "же",
    "и",
    "как",
    "мы",
    "на",
    "не",
    "но",
    "ну",
    "он",
    "она",
    "по",
    "с",
    "так",
    "там",
    "то",
    "у",
    "что",
    "это",
}


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_script(filename: str, module_name: str) -> ModuleType:
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load helper: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def stable_id(session: str, chunk_index: int, start: float, end: float) -> str:
    digest = hashlib.sha256(
        f"{session}:{chunk_index}:{start:.3f}:{end:.3f}".encode("utf-8")
    ).hexdigest()[:16]
    return f"remote_active_{digest}"


def interval_overlap(start: float, end: float, other_start: float, other_end: float) -> float:
    return max(0.0, min(end, other_end) - max(start, other_start))


def resample(audio: np.ndarray, source_rate: int, target_rate: int = SAMPLE_RATE) -> np.ndarray:
    if source_rate == target_rate:
        return np.asarray(audio, dtype=np.float32)
    divisor = math.gcd(source_rate, target_rate)
    return signal.resample_poly(
        np.asarray(audio, dtype=np.float32),
        target_rate // divisor,
        source_rate // divisor,
    ).astype(np.float32)


def speech_band(audio: np.ndarray, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    if audio.size < 32:
        return np.asarray(audio, dtype=np.float32)
    sos = signal.butter(
        4,
        [120.0, min(7600.0, sample_rate * 0.47)],
        btype="bandpass",
        fs=sample_rate,
        output="sos",
    )
    return signal.sosfilt(sos, np.asarray(audio, dtype=np.float32)).astype(np.float32)


def rms_db(audio: np.ndarray) -> float:
    if audio.size == 0:
        return -120.0
    rms = float(np.sqrt(np.mean(np.square(audio), dtype=np.float64) + EPSILON))
    return 20.0 * math.log10(rms + EPSILON)


def max_abs_corr(reference: np.ndarray, target: np.ndarray, max_lag_ms: float = 400.0) -> tuple[float, int]:
    count = min(reference.size, target.size)
    if count < 320:
        return 0.0, 0
    ref = np.asarray(reference[:count], dtype=np.float64)
    tar = np.asarray(target[:count], dtype=np.float64)
    ref -= float(np.mean(ref))
    tar -= float(np.mean(tar))
    denominator = math.sqrt(float(np.dot(ref, ref) * np.dot(tar, tar))) + EPSILON
    corr = signal.correlate(tar, ref, mode="full", method="fft") / denominator
    lags = signal.correlation_lags(count, count, mode="full")
    limit = int(round(max_lag_ms * SAMPLE_RATE / 1_000.0))
    mask = np.abs(lags) <= limit
    if not np.any(mask):
        return 0.0, 0
    values = np.abs(corr[mask])
    index = int(np.argmax(values))
    return float(values[index]), int(lags[mask][index])


def speech_coherence(reference: np.ndarray, target: np.ndarray) -> float:
    count = min(reference.size, target.size)
    if count < 512:
        return 0.0
    nperseg = min(1024, count)
    frequencies, coherence = signal.coherence(
        reference[:count], target[:count], fs=SAMPLE_RATE, nperseg=nperseg
    )
    mask = (frequencies >= 300.0) & (frequencies <= 7600.0)
    if not np.any(mask):
        return 0.0
    return float(np.mean(np.nan_to_num(coherence[mask], nan=0.0)))


def shift_reference(remote: np.ndarray, delay_samples: int) -> np.ndarray:
    shifted = np.zeros_like(remote)
    if delay_samples > 0:
        shifted[delay_samples:] = remote[:-delay_samples]
    elif delay_samples < 0:
        lead = -delay_samples
        shifted[:-lead] = remote[lead:]
    else:
        shifted[:] = remote
    return shifted


def estimate_delay(training: list[tuple[np.ndarray, np.ndarray]]) -> tuple[int, dict[str, Any]]:
    estimates: list[tuple[int, float]] = []
    for remote, mic in training:
        corr, lag = max_abs_corr(remote, mic)
        if corr >= 0.03:
            estimates.append((lag, corr))
    if not estimates:
        return 0, {"status": "fallback_zero", "delay_samples": 0, "window_count": 0}
    expanded: list[int] = []
    for lag, corr in estimates:
        expanded.extend([lag] * max(1, int(round(corr * 20.0))))
    delay = int(np.median(np.asarray(expanded, dtype=np.int64)))
    delay = max(-int(0.4 * SAMPLE_RATE), min(int(0.4 * SAMPLE_RATE), delay))
    return delay, {
        "status": "estimated_from_past_remote_dominant_windows",
        "delay_samples": delay,
        "delay_ms": round(delay * 1_000.0 / SAMPLE_RATE, 3),
        "window_count": len(estimates),
        "median_training_corr": round(float(np.median([corr for _, corr in estimates])), 6),
    }


def fit_fir(
    training: list[tuple[np.ndarray, np.ndarray]],
    delay_samples: int,
    taps: int = 1_280,
    regularization: float = 2.0e-2,
) -> np.ndarray:
    r_xx = np.zeros(taps, dtype=np.float64)
    p_yx = np.zeros(taps, dtype=np.float64)
    used = 0
    for remote, mic in training:
        aligned = shift_reference(remote, delay_samples).astype(np.float64)
        target = mic.astype(np.float64)
        count = min(aligned.size, target.size)
        if count <= taps * 2:
            continue
        x = aligned[:count] - float(np.mean(aligned[:count]))
        y = target[:count] - float(np.mean(target[:count]))
        corr_xx = signal.correlate(x, x, mode="full", method="fft")
        corr_yx = signal.correlate(y, x, mode="full", method="fft")
        center = count - 1
        r_xx += corr_xx[center : center + taps]
        p_yx += corr_yx[center : center + taps]
        used += 1
    if used == 0 or r_xx[0] <= EPSILON:
        return np.zeros(taps, dtype=np.float64)
    column = r_xx.copy()
    column[0] += max(float(r_xx[0]) * regularization, EPSILON)
    try:
        fitted = linalg.solve_toeplitz((column, column), p_yx, check_finite=False)
    except (ValueError, linalg.LinAlgError):
        return np.zeros(taps, dtype=np.float64)
    return np.nan_to_num(np.asarray(fitted, dtype=np.float64))


def fit_spectral_transfer(
    training: list[tuple[np.ndarray, np.ndarray]],
    delay_samples: int,
    regularization: float = 2.0e-2,
) -> np.ndarray:
    numerator: np.ndarray | None = None
    denominator: np.ndarray | None = None
    for remote, mic in training:
        aligned = shift_reference(remote, delay_samples)
        count = min(aligned.size, mic.size)
        if count < 1024:
            continue
        _, _, remote_stft = signal.stft(
            aligned[:count], fs=SAMPLE_RATE, nperseg=512, noverlap=384, boundary=None
        )
        _, _, mic_stft = signal.stft(
            mic[:count], fs=SAMPLE_RATE, nperseg=512, noverlap=384, boundary=None
        )
        frames = min(remote_stft.shape[1], mic_stft.shape[1])
        cross = np.sum(mic_stft[:, :frames] * np.conj(remote_stft[:, :frames]), axis=1)
        auto = np.sum(np.abs(remote_stft[:, :frames]) ** 2, axis=1)
        numerator = cross if numerator is None else numerator + cross
        denominator = auto if denominator is None else denominator + auto
    if numerator is None or denominator is None:
        return np.zeros(257, dtype=np.complex128)
    floor = max(float(np.median(denominator[denominator > 0])) * regularization, EPSILON)
    transfer = numerator / (denominator + floor)
    magnitude = np.abs(transfer)
    transfer = np.where(magnitude > 4.0, transfer * (4.0 / np.maximum(magnitude, EPSILON)), transfer)
    return np.nan_to_num(transfer)


def apply_spectral_transfer(remote: np.ndarray, transfer: np.ndarray) -> np.ndarray:
    _, _, remote_stft = signal.stft(
        remote, fs=SAMPLE_RATE, nperseg=512, noverlap=384, boundary="zeros", padded=True
    )
    bins = min(remote_stft.shape[0], transfer.size)
    estimate_stft = np.zeros_like(remote_stft)
    estimate_stft[:bins] = transfer[:bins, None] * remote_stft[:bins]
    _, estimate = signal.istft(
        estimate_stft, fs=SAMPLE_RATE, nperseg=512, noverlap=384, boundary=True
    )
    if estimate.size < remote.size:
        estimate = np.pad(estimate, (0, remote.size - estimate.size))
    return np.asarray(estimate[: remote.size], dtype=np.float64)


@dataclass
class SeparationModel:
    chunk_index: int
    training_rows: list[dict[str, Any]]
    training_seconds: float
    delay_samples: int
    delay_evidence: dict[str, Any]
    fir: np.ndarray
    spectral_transfer: np.ndarray


class AudioStore:
    def __init__(self, session: Path, chunks: dict[int, dict[str, Any]]) -> None:
        self.session = session
        self.chunks = chunks
        self.cache: dict[str, tuple[int, np.ndarray]] = {}

    def source(self, chunk_index: int, source: str) -> tuple[dict[str, Any], Path | None]:
        chunk = self.chunks.get(chunk_index) or {}
        row = chunk.get(source) if isinstance(chunk.get(source), dict) else {}
        value = row.get("input") or row.get("wav")
        if not value:
            return row, None
        path = Path(str(value))
        if not path.is_absolute():
            path = self.session / path
        return row, path if path.exists() else None

    def load(self, path: Path) -> tuple[int, np.ndarray]:
        key = str(path)
        if key not in self.cache:
            data, rate = sf.read(path, dtype="float32", always_2d=False)
            if np.asarray(data).ndim > 1:
                data = np.mean(np.asarray(data), axis=1)
            self.cache[key] = (int(rate), np.nan_to_num(np.asarray(data, dtype=np.float32)))
        return self.cache[key]

    def slice(self, chunk_index: int, source: str, start: float, end: float) -> np.ndarray | None:
        row, path = self.source(chunk_index, source)
        if path is None:
            return None
        rate, audio = self.load(path)
        clip_start = safe_float(row.get("clip_start_sec"))
        local_start = max(0, int(round((start - clip_start) * rate)))
        local_end = min(audio.size, int(round((end - clip_start) * rate)))
        if local_end - local_start < int(0.25 * rate):
            return None
        return speech_band(resample(audio[local_start:local_end], rate))

    def pair(self, chunk_index: int, start: float, end: float) -> tuple[np.ndarray, np.ndarray] | None:
        mic = self.slice(chunk_index, "mic", start, end)
        remote = self.slice(chunk_index, "remote", start, end)
        if mic is None or remote is None:
            return None
        count = min(mic.size, remote.size)
        if count < int(0.25 * SAMPLE_RATE):
            return None
        return remote[:count], mic[:count]


def content_tokens(progressive: Any, text: str) -> list[str]:
    return [token for token in progressive.tokens(text) if len(token) >= 3]


def text_similarity(progressive: Any, left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    return safe_float(progressive.asr_text_similarity(left, right))


def training_row_is_remote_dominant(row: dict[str, Any], progressive: Any) -> bool:
    audio = row.get("audio") if isinstance(row.get("audio"), dict) else {}
    duration = safe_float(row.get("duration_sec"), safe_float(row.get("end")) - safe_float(row.get("start")))
    similarity = text_similarity(progressive, clean_text(row.get("text")), clean_text(row.get("remote_text")))
    return bool(
        row.get("timeline_causal") is True
        and row.get("used_batch_fields_for_selection") is False
        and row.get("classification") == "not_supported"
        and 1.0 <= duration <= 12.0
        and safe_float(audio.get("remote_db"), -120.0) >= -50.0
        and safe_float(audio.get("mic_minus_remote_db"), 99.0) <= -6.0
        and (similarity >= 0.20 or safe_float(audio.get("corr")) >= 0.08)
    )


def choose_training_rows(
    evaluations: list[dict[str, Any]], chunk_index: int, progressive: Any
) -> list[dict[str, Any]]:
    eligible = [
        row
        for row in evaluations
        if safe_int(row.get("chunk_index")) < chunk_index
        and training_row_is_remote_dominant(row, progressive)
    ]
    eligible.sort(key=lambda row: safe_float(row.get("end")), reverse=True)
    selected: list[dict[str, Any]] = []
    seconds = 0.0
    for row in eligible:
        duration = safe_float(row.get("duration_sec"), safe_float(row.get("end")) - safe_float(row.get("start")))
        selected.append(row)
        seconds += max(0.0, duration)
        if seconds >= MAX_TRAINING_SECONDS:
            break
    return sorted(selected, key=lambda row: safe_float(row.get("start")))


def build_model(
    chunk_index: int,
    evaluations: list[dict[str, Any]],
    audio: AudioStore,
    progressive: Any,
) -> SeparationModel | None:
    rows = choose_training_rows(evaluations, chunk_index, progressive)
    windows: list[tuple[np.ndarray, np.ndarray]] = []
    used_rows: list[dict[str, Any]] = []
    for row in rows:
        pair = audio.pair(
            safe_int(row.get("chunk_index")), safe_float(row.get("start")), safe_float(row.get("end"))
        )
        if pair is None:
            continue
        windows.append(pair)
        used_rows.append(row)
    seconds = sum(min(remote.size, mic.size) / SAMPLE_RATE for remote, mic in windows)
    if seconds < MIN_TRAINING_SECONDS:
        return None
    delay, delay_evidence = estimate_delay(windows)
    return SeparationModel(
        chunk_index=chunk_index,
        training_rows=used_rows,
        training_seconds=seconds,
        delay_samples=delay,
        delay_evidence=delay_evidence,
        fir=fit_fir(windows, delay),
        spectral_transfer=fit_spectral_transfer(windows, delay),
    )


def signal_metrics(remote: np.ndarray, audio: np.ndarray) -> dict[str, Any]:
    count = min(remote.size, audio.size)
    remote = remote[:count]
    audio = audio[:count]
    corr, lag = max_abs_corr(remote, audio)
    coherence = speech_coherence(remote, audio)
    return {
        "rms_db": round(rms_db(audio), 3),
        "remote_corr": round(corr, 6),
        "remote_lag_ms": round(lag * 1_000.0 / SAMPLE_RATE, 3),
        "remote_coherence": round(coherence, 6),
        "remote_strength": round(max(corr, coherence), 6),
        "clipping_ratio": round(float(np.mean(np.abs(audio) >= 0.999)), 8),
        "finite": bool(np.all(np.isfinite(audio))),
    }


def method_residuals(
    model: SeparationModel, remote: np.ndarray, mic: np.ndarray
) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    aligned = shift_reference(remote, model.delay_samples).astype(np.float64)
    fir_echo = signal.lfilter(model.fir, [1.0], aligned)
    spectral_echo = apply_spectral_transfer(aligned, model.spectral_transfer)
    hybrid_echo = 0.65 * fir_echo + 0.35 * spectral_echo
    source = mic.astype(np.float64)
    return {
        "causal_fir_v1": (source - fir_echo, fir_echo),
        "spectral_projection_v1": (source - spectral_echo, spectral_echo),
        "hybrid_fir_spectral_v1": (source - hybrid_echo, hybrid_echo),
    }


def evaluate_method(
    method: str,
    remote: np.ndarray,
    mic: np.ndarray,
    residual: np.ndarray,
    echo_hat: np.ndarray,
) -> dict[str, Any]:
    before = signal_metrics(remote, mic)
    after = signal_metrics(remote, residual)
    energy_delta = after["rms_db"] - before["rms_db"]
    before_projection = max(EPSILON, (before["remote_strength"] ** 2) * np.mean(np.square(mic)))
    after_projection = max(EPSILON, (after["remote_strength"] ** 2) * np.mean(np.square(residual)))
    projection_reduction = 10.0 * math.log10(before_projection / after_projection)
    echo_ratio = rms_db(echo_hat) - before["rms_db"]
    passed = bool(
        after["finite"]
        and after["clipping_ratio"] <= 0.0001
        and after["rms_db"] >= -68.0
        and -MAX_RESIDUAL_ENERGY_LOSS_DB <= energy_delta <= MAX_RESIDUAL_GAIN_DB
        and after["remote_strength"] <= MAX_REMOTE_AUDIO_STRENGTH
        and (
            projection_reduction >= MIN_PROJECTION_REDUCTION_DB
            or (
                before["remote_strength"] <= 0.10
                and after["remote_strength"] <= 0.08
                and energy_delta >= -12.0
            )
        )
    )
    score = (
        min(1.0, max(0.0, projection_reduction) / 8.0) * 0.50
        + min(1.0, max(0.0, before["remote_strength"] - after["remote_strength"]) / 0.20) * 0.25
        + min(1.0, max(0.0, energy_delta + 24.0) / 24.0) * 0.15
        + (0.10 if after["remote_strength"] <= 0.12 else 0.0)
    )
    return {
        "method": method,
        "status": "passed" if passed else "rejected",
        "before": before,
        "after": after,
        "residual_energy_delta_db": round(energy_delta, 3),
        "projection_reduction_db": round(projection_reduction, 3),
        "echo_hat_relative_db": round(echo_ratio, 3),
        "separation_score": round(score, 6),
        "thresholds": {
            "max_remote_audio_strength": MAX_REMOTE_AUDIO_STRENGTH,
            "min_projection_reduction_db": MIN_PROJECTION_REDUCTION_DB,
            "max_residual_energy_loss_db": MAX_RESIDUAL_ENERGY_LOSS_DB,
            "max_residual_gain_db": MAX_RESIDUAL_GAIN_DB,
        },
    }


def selection_decisions(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    selected: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    for row in rows:
        checks = row.get("checks") if isinstance(row.get("checks"), dict) else {}
        recording = (
            row.get("recording_time_evidence")
            if isinstance(row.get("recording_time_evidence"), dict)
            else {}
        )
        remote_guard = row.get("remote_audio_guard") if isinstance(row.get("remote_audio_guard"), dict) else {}
        contract = {
            "speaker_supported": checks.get("speaker_supported") is True,
            "remote_audio_active": safe_float(remote_guard.get("remote_db"), -120.0) > REMOTE_ACTIVE_MIN_DB,
            "recording_time_committed_pcm": checks.get("recording_time_committed_pcm") is True,
            "timeline_causal": checks.get("timeline_causal") is True,
            "selection_does_not_use_batch": checks.get("selection_does_not_use_batch") is True,
            "past_only_enrollment": checks.get("past_only_enrollment") is True,
            "past_enrollment_ready": checks.get("past_enrollment_ready") is True,
            "source_text_contentful": checks.get("source_text_contentful") is True,
            "supported_duration": checks.get("supported_duration") is True,
            "not_already_published": checks.get("not_already_published") is True,
            "recording_time_evidence": recording.get("status") == "passed",
        }
        decision = {
            "schema": SCHEMA,
            "kind": "causal_remote_active_selection",
            "id": row.get("id"),
            "session": row.get("session"),
            "chunk_index": row.get("chunk_index"),
            "start": row.get("start"),
            "end": row.get("end"),
            "duration_sec": row.get("duration_sec"),
            "text": row.get("text"),
            "status": "selected" if all(contract.values()) else "rejected",
            "checks": contract,
            "reasons": [name for name, passed in contract.items() if not passed],
            "source_selection_id": row.get("id"),
            "source_evaluation": row.get("source_evaluation") or {},
            "speaker_evidence": row.get("speaker_evidence") or {},
            "remote_audio_guard": remote_guard,
            "recording_time_evidence": recording,
            "timeline_causal": True,
            "used_batch_fields_for_selection": False,
        }
        decisions.append(decision)
        if decision["status"] == "selected":
            selected.append(decision)
    return selected, decisions


def group_selected(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    for row in sorted(rows, key=lambda item: (safe_int(item.get("chunk_index")), safe_float(item.get("start")))):
        if not groups:
            groups.append([row])
            continue
        previous = groups[-1][-1]
        proposed_duration = safe_float(row.get("end")) - safe_float(groups[-1][0].get("start"))
        if (
            safe_int(row.get("chunk_index")) == safe_int(previous.get("chunk_index"))
            and safe_float(row.get("start")) - safe_float(previous.get("end")) <= GROUP_GAP_SECONDS
            and proposed_duration <= MAX_CANDIDATE_SECONDS
        ):
            groups[-1].append(row)
        else:
            groups.append([row])
    return groups


def resolve_original_evaluations(
    group: list[dict[str, Any]], evaluations: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    keys = {
        (
            safe_int(row.get("chunk_index")),
            safe_int((row.get("source_evaluation") or {}).get("segment_index")),
        )
        for row in group
    }
    return [
        row
        for row in evaluations
        if (safe_int(row.get("chunk_index")), safe_int(row.get("segment_index"))) in keys
    ]


def read_remote_text(
    chunk: dict[str, Any], session: Path, start: float, end: float, progressive: Any
) -> str:
    remote = chunk.get("remote") if isinstance(chunk.get("remote"), dict) else {}
    rows = progressive.read_asr_segments(remote, session)
    nearby = [
        row
        for row in rows
        if interval_overlap(start - 0.25, end + 0.25, safe_float(row.get("start")), safe_float(row.get("end")))
        > 0.0
    ]
    return clean_text(" ".join(str(row.get("text") or "") for row in nearby))


def cached_or_run_asr(
    wav: Path,
    output_base: Path,
    *,
    progressive: Any,
    model: str,
    language: str,
    whisper_cli: str,
    force: bool,
    runner: Callable[[Path, Path, str, str, str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    json_path = output_base.with_suffix(".json")
    txt_path = output_base.with_suffix(".txt")
    if not force and json_path.exists() and txt_path.exists():
        return {
            "status": "passed",
            "text": clean_text(txt_path.read_text(encoding="utf-8", errors="ignore")),
            "score": round(progressive.token_average_probability(json_path), 6),
            "json": str(json_path),
            "rows": progressive.asr_rows(json_path),
            "cache_hit": True,
        }
    result = (runner or progressive.default_micro_runner)(
        wav, output_base, model, language, whisper_cli
    )
    result["cache_hit"] = False
    return result


def select_asr_rows(
    rows: list[dict[str, Any]], selection_start: float, selection_end: float
) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if isinstance(row, dict)
        and selection_start - EPSILON
        <= (safe_float(row.get("start_sec")) + safe_float(row.get("end_sec"))) / 2.0
        <= selection_end + EPSILON
    ]


def resolve_executable(value: str) -> Path | None:
    candidate = Path(value).expanduser()
    if candidate.is_absolute() and candidate.exists():
        return candidate.resolve()
    resolved = shutil.which(value)
    return Path(resolved).resolve() if resolved else None


def incremental_contexts(
    *,
    args: argparse.Namespace,
    algorithm: dict[str, Any],
    model: dict[str, Any],
    whisper_cli: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    preparation_configuration = {
        "schema": SCHEMA,
        "contract": "causal_remote_active_dsp_preparation_v1",
        "methods": list(METHODS),
        "thresholds": {
            "remote_active_min_db": REMOTE_ACTIVE_MIN_DB,
            "min_training_seconds": MIN_TRAINING_SECONDS,
            "max_training_seconds": MAX_TRAINING_SECONDS,
            "max_candidate_seconds": MAX_CANDIDATE_SECONDS,
            "group_gap_seconds": GROUP_GAP_SECONDS,
            "min_projection_reduction_db": MIN_PROJECTION_REDUCTION_DB,
            "max_remote_audio_strength": MAX_REMOTE_AUDIO_STRENGTH,
        },
    }
    candidate_configuration = {
        "schema": SCHEMA,
        "contract": "causal_remote_active_candidate_v1",
        "language": args.language,
        "whisper_cli": whisper_cli,
        "max_asr_groups": args.max_asr_groups,
        "skip_asr": args.skip_asr,
        "runtime_shadow": args.runtime_shadow,
        "thresholds": {
            "min_asr_score": MIN_ASR_SCORE,
            "min_source_alignment": MIN_SOURCE_ALIGNMENT,
            "max_remote_text_similarity": MAX_REMOTE_TEXT_SIMILARITY,
            "max_remote_token_recall": MAX_REMOTE_TOKEN_RECALL,
        },
    }
    return (
        context_fingerprint(
            stage="causal_remote_active_me_separation_v1/preparation",
            algorithm=algorithm,
            model={"status": "not_applicable"},
            configuration=preparation_configuration,
        ),
        context_fingerprint(
            stage="causal_remote_active_me_separation_v1/candidate",
            algorithm=algorithm,
            model=model,
            configuration=candidate_configuration,
        ),
    )


def model_training_evidence(model: SeparationModel, start: float) -> dict[str, Any]:
    return {
        "status": "passed",
        "seconds": round(model.training_seconds, 3),
        "row_count": len(model.training_rows),
        "latest_end_sec": round(
            max((safe_float(row.get("end")) for row in model.training_rows), default=0.0),
            3,
        ),
        "target_start_sec": round(start, 3),
        "past_only": all(
            safe_int(row.get("chunk_index")) < model.chunk_index
            for row in model.training_rows
        ),
        "source": "causal_past_remote_dominant_committed_pcm",
        "training_rows": model.training_rows,
    }


def prepare_groups(
    *,
    session: Path,
    groups: list[list[dict[str, Any]]],
    evaluations: list[dict[str, Any]],
    chunks: dict[int, dict[str, Any]],
    audio: AudioStore,
    progressive: Any,
    output: Path,
    group_index_offset: int = 0,
    prepared_input_sha256: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    models: dict[int, SeparationModel | None] = {}
    residual_rows: list[dict[str, Any]] = []
    prepared: list[dict[str, Any]] = []
    for local_index, group in enumerate(groups, start=1):
        group_index = group_index_offset + local_index
        chunk_index = safe_int(group[0].get("chunk_index"))
        start = safe_float(group[0].get("start"))
        end = safe_float(group[-1].get("end"), start)
        item_id = stable_id(session.name, chunk_index, start, end)
        if chunk_index not in models:
            models[chunk_index] = build_model(chunk_index, evaluations, audio, progressive)
        model = models[chunk_index]
        base = {
            "schema": SCHEMA,
            "kind": "causal_remote_active_residual_evaluation",
            "id": item_id,
            "session": session.name,
            "group_index": group_index,
            "chunk_index": chunk_index,
            "start": round(start, 3),
            "end": round(end, 3),
            "duration_sec": round(max(0.0, end - start), 3),
            "source_selection_ids": [row.get("source_selection_id") for row in group],
            "source_text": clean_text(" ".join(str(row.get("text") or "") for row in group)),
            "speaker_evidence": [row.get("speaker_evidence") or {} for row in group],
            "timeline_causal": True,
            "used_batch_fields_for_selection": False,
            "batch_authoritative": True,
            "promotion_allowed": False,
        }
        if model is None:
            residual_rows.append(
                {**base, "status": "rejected", "reason": "insufficient_causal_training"}
            )
            continue
        chunk = chunks.get(chunk_index) or {}
        mic_source = chunk.get("mic") if isinstance(chunk.get("mic"), dict) else {}
        extraction_start = max(
            safe_float(mic_source.get("clip_start_sec")),
            start - CONTEXT_BEFORE_SECONDS,
        )
        extraction_end = min(
            safe_float(mic_source.get("clip_end_sec"), end + CONTEXT_AFTER_SECONDS),
            end + CONTEXT_AFTER_SECONDS,
        )
        pair = audio.pair(chunk_index, extraction_start, extraction_end)
        if pair is None:
            residual_rows.append(
                {**base, "status": "rejected", "reason": "committed_pcm_extract_failed"}
            )
            continue
        remote, mic = pair
        method_audio = method_residuals(model, remote, mic)
        method_rows: list[dict[str, Any]] = []
        training = model_training_evidence(model, start)
        for method in METHODS:
            residual, echo_hat = method_audio[method]
            metrics = evaluate_method(method, remote, mic, residual, echo_hat)
            method_rows.append(metrics)
            residual_rows.append(
                {
                    **base,
                    **metrics,
                    "training": {key: value for key, value in training.items() if key != "training_rows"},
                    "delay_evidence": model.delay_evidence,
                }
            )
        passing = [row for row in method_rows if row.get("status") == "passed"]
        if not passing:
            continue
        best = max(
            passing,
            key=lambda row: (
                safe_float(row.get("separation_score")),
                -METHODS.index(row["method"]),
            ),
        )
        payload = np.asarray(method_audio[best["method"]][0], dtype=np.float32)
        payload = np.concatenate(
            [np.zeros(int(LEADING_SILENCE_SECONDS * SAMPLE_RATE), dtype=np.float32), payload]
        )
        residual_wav = output / "residual_audio" / f"{item_id}_{best['method']}.wav"
        residual_wav.parent.mkdir(parents=True, exist_ok=True)
        sf.write(residual_wav, payload, SAMPLE_RATE, subtype="FLOAT")
        prepared.append(
            {
                **base,
                "method_metrics": best,
                "method_comparison": method_rows,
                "extraction_start": extraction_start,
                "extraction_end": extraction_end,
                "original_evaluations": resolve_original_evaluations(group, evaluations),
                "training_evidence": training,
                "delay_evidence": model.delay_evidence,
                "residual_wav": str(residual_wav),
                "work_dir": str(output),
                "prepared_input_sha256": prepared_input_sha256,
            }
        )
    return residual_rows, prepared


def candidate_outcome_key(
    *,
    row: dict[str, Any],
    asr_allowed: bool,
    existing_me: list[dict[str, Any]],
    accepted_so_far: list[dict[str, Any]],
    max_asr_groups: int,
    candidate_context_sha256: str = "",
) -> str:
    chunk_index = safe_int(row.get("chunk_index"))
    return content_hash(
        {
            "schema": SCHEMA,
            "script_version": SCRIPT_VERSION,
            "candidate_context_sha256": candidate_context_sha256,
            "prepared_input_sha256": row.get("prepared_input_sha256"),
            "id": row.get("id"),
            "asr_allowed": asr_allowed,
            "max_asr_groups": max_asr_groups,
            "existing_me": [
                {
                    key: item.get(key)
                    for key in ("id", "chunk_index", "start", "end", "text")
                }
                for item in existing_me
                if safe_int(item.get("chunk_index"), chunk_index) <= chunk_index
            ],
            "accepted_prefix": [
                {
                    key: item.get(key)
                    for key in ("id", "chunk_index", "start", "end", "text", "status")
                }
                for item in accepted_so_far
            ],
        }
    )


def finalize_candidates(
    *,
    session: Path,
    prepared: list[dict[str, Any]],
    chunks: dict[int, dict[str, Any]],
    existing_me: list[dict[str, Any]],
    local_island: Any,
    progressive: Any,
    model: str,
    language: str,
    whisper_cli: str,
    max_asr_groups: int,
    skip_asr: bool,
    force: bool,
    runtime_shadow: bool,
    outcome_cache_root: Path | None = None,
    candidate_context_sha256: str = "",
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    ranked = sorted(
        prepared,
        key=lambda row: safe_float((row.get("method_metrics") or {}).get("separation_score")),
        reverse=True,
    )
    asr_allowed = {
        row["id"]
        for row in (ranked if max_asr_groups <= 0 else ranked[:max_asr_groups])
    }
    candidates: list[dict[str, Any]] = []
    accepted_so_far: list[dict[str, Any]] = []
    telemetry = {
        "candidate_cache_hits": 0,
        "candidate_cache_misses": 0,
        "asr_cache_hits": 0,
        "asr_cache_misses": 0,
    }
    for row in sorted(
        prepared,
        key=lambda item: (safe_float(item.get("start")), safe_float(item.get("end"))),
    ):
        allowed = row["id"] in asr_allowed and not skip_asr
        outcome_key = candidate_outcome_key(
            row=row,
            asr_allowed=allowed,
            existing_me=existing_me,
            accepted_so_far=accepted_so_far,
            max_asr_groups=max_asr_groups,
            candidate_context_sha256=candidate_context_sha256,
        )
        outcome_path = (
            object_directory(outcome_cache_root, outcome_key) / "candidate.json"
            if outcome_cache_root
            else None
        )
        cached = read_cache_json(outcome_path) if outcome_path and not force else {}
        cached_candidate = cached.get("candidate") if isinstance(cached.get("candidate"), dict) else {}
        cached_asr_path = Path(str(cached_candidate.get("asr_json") or ""))
        cache_valid = bool(
            cached.get("status") == "complete"
            and cached.get("outcome_sha256") == outcome_key
            and cached_candidate
            and (
                not allowed
                or (cached_asr_path.exists() and cached_asr_path.is_file())
            )
        )
        if cache_valid:
            candidate = cached_candidate
            telemetry["candidate_cache_hits"] += 1
            if allowed:
                telemetry["asr_cache_hits"] += 1
        else:
            telemetry["candidate_cache_misses"] += 1
            candidate = {
                key: value
                for key, value in row.items()
                if key
                not in {
                    "original_evaluations",
                    "training_evidence",
                    "delay_evidence",
                    "residual_wav",
                    "work_dir",
                    "prepared_input_sha256",
                }
            }
            reasons: list[str] = []
            if skip_asr:
                reasons.append("micro_asr_skipped_by_cli")
            elif not allowed:
                reasons.append("causal_asr_budget_exhausted")
            wav = Path(str(row.get("residual_wav") or ""))
            work_dir = Path(str(row.get("work_dir") or wav.parent.parent))
            output_base = (
                outcome_path.parent / "micro_asr" / str(row["id"])
                if outcome_path
                else work_dir / "micro_asr" / str(row["id"])
            )
            selected_rows: list[dict[str, Any]] = []
            result: dict[str, Any] = {"status": "not_run", "rows": [], "score": 0.0}
            if not reasons:
                result = cached_or_run_asr(
                    wav,
                    output_base,
                    progressive=progressive,
                    model=model,
                    language=language,
                    whisper_cli=whisper_cli,
                    force=force,
                )
                if result.get("cache_hit") is True:
                    telemetry["asr_cache_hits"] += 1
                else:
                    telemetry["asr_cache_misses"] += 1
                selection_start = (
                    LEADING_SILENCE_SECONDS
                    + safe_float(row.get("start"))
                    - safe_float(row.get("extraction_start"))
                )
                selection_end = (
                    LEADING_SILENCE_SECONDS
                    + safe_float(row.get("end"))
                    - safe_float(row.get("extraction_start"))
                )
                selected_rows = select_asr_rows(
                    result.get("rows") or [], selection_start, selection_end
                )
            text = clean_text(" ".join(str(item.get("text") or "") for item in selected_rows))
            text_tokens = progressive.tokens(text)
            source_text = clean_text(row.get("source_text"))
            source_alignment = (
                max(
                    progressive.bag_recall(progressive.tokens(source_text), text_tokens),
                    progressive.bag_recall(text_tokens, progressive.tokens(source_text)),
                )
                if text_tokens
                else 0.0
            )
            remote_text = read_remote_text(
                chunks.get(safe_int(row.get("chunk_index"))) or {},
                session,
                safe_float(row.get("start")),
                safe_float(row.get("end")),
                progressive,
            )
            if not remote_text:
                remote_text = clean_text(
                    " ".join(
                        str(item.get("remote_text") or "")
                        for item in row.get("original_evaluations") or []
                    )
                )
            remote_similarity = text_similarity(progressive, text, remote_text)
            remote_tokens = progressive.tokens(remote_text)
            remote_recall = (
                progressive.bag_recall(remote_tokens, text_tokens) if remote_tokens else 0.0
            )
            remote_matches = (
                progressive.bag_match_count(remote_tokens, text_tokens) if remote_tokens else 0
            )
            source_content = set(content_tokens(progressive, source_text))
            remote_forbidden = {
                token
                for token in content_tokens(progressive, remote_text)
                if token not in source_content and token not in REMOTE_CONTENT_STOPWORDS
            }
            forbidden_matches = sorted(remote_forbidden & set(text_tokens))
            remote_text_guard_passed = bool(
                remote_similarity <= MAX_REMOTE_TEXT_SIMILARITY
                and not (remote_recall > MAX_REMOTE_TOKEN_RECALL and remote_matches >= 1)
                and not forbidden_matches
            )
            scores = [
                safe_float(item.get("score"))
                for item in selected_rows
                if safe_float(item.get("score")) > 0.0
            ]
            score = float(np.mean(scores)) if scores else safe_float(result.get("score"))
            causal_existing_me = (
                local_island.existing_me_through_chunk(
                    existing_me + accepted_so_far,
                    safe_int(row.get("chunk_index")),
                )
                if runtime_shadow
                else existing_me + accepted_so_far
            )
            existing = (
                local_island.covered_by_existing_me(
                    {"start": row.get("start"), "end": row.get("end"), "text": text},
                    causal_existing_me,
                    progressive,
                )
                if text
                else None
            )
            if not reasons and result.get("status") != "passed":
                reasons.append(str(result.get("reason") or "micro_asr_failed"))
            if not reasons and not selected_rows:
                reasons.append("no_micro_asr_rows_inside_supported_interval")
            if not reasons and len(text_tokens) < 2:
                reasons.append("micro_asr_text_too_short")
            if not reasons and score < MIN_ASR_SCORE:
                reasons.append("low_micro_asr_score")
            if not reasons and source_alignment < MIN_SOURCE_ALIGNMENT:
                reasons.append("low_live_source_alignment")
            if not reasons and not remote_text_guard_passed:
                reasons.append("remote_forbidden_text_guard")
            if not reasons and existing is not None:
                reasons.append("already_published_or_duplicate_candidate")
            training = row.get("training_evidence") or {}
            candidate.update(
                {
                    "schema": SCHEMA,
                    "kind": "causal_remote_active_me_candidate",
                    "status": "accepted" if not reasons else "rejected",
                    "outcome": "accepted" if not reasons else "rejected",
                    "reasons": reasons,
                    "text": text,
                    "remote_text": remote_text,
                    "score": round(score, 6),
                    "source_alignment": round(source_alignment, 6),
                    "remote_similarity": round(remote_similarity, 6),
                    "remote_text_recall_in_micro": round(remote_recall, 6),
                    "remote_text_matched_token_count": remote_matches,
                    "remote_forbidden_matches": forbidden_matches,
                    "remote_text_guard": {
                        "status": "passed" if remote_text_guard_passed else "rejected",
                        "max_similarity": MAX_REMOTE_TEXT_SIMILARITY,
                        "max_token_recall": MAX_REMOTE_TOKEN_RECALL,
                        "similarity": round(remote_similarity, 6),
                        "token_recall": round(remote_recall, 6),
                        "matched_token_count": remote_matches,
                        "forbidden_matches": forbidden_matches,
                    },
                    "residual_method": row["method_metrics"]["method"],
                    "residual_audio_guard": row["method_metrics"],
                    "past_training_evidence": {
                        key: value for key, value in training.items() if key != "training_rows"
                    },
                    "remote_active_guard": {
                        "status": "passed",
                        "threshold_db": REMOTE_ACTIVE_MIN_DB,
                    },
                    "target_me_evidence": {
                        "status": "passed" if row.get("speaker_evidence") else "rejected",
                        "source_count": len(row.get("speaker_evidence") or []),
                        "mode": "past_only_target_me_enrollment",
                    },
                    "recording_time_evidence": {
                        "status": "passed",
                        "source": "committed_pcm",
                    },
                    "wav": str(wav) if wav.exists() else None,
                    "asr_json": result.get("json"),
                    "cache_hit": result.get("cache_hit") is True,
                    "selected_asr_row_count": len(selected_rows),
                    "existing_live_turn": existing,
                    "selection_mode": "recording_time_causal_remote_active_separation_v1",
                    "used_batch_fields_for_selection": False,
                    "timeline_causal": True,
                    "publication_allowed": False,
                    "promotion_allowed": False,
                    "batch_authoritative": True,
                }
            )
            if outcome_path:
                outcome_path.parent.mkdir(parents=True, exist_ok=True)
                write_cache_json(
                    outcome_path,
                    {
                        "schema": "murmurmark.live_recovery_candidate_cache/v1",
                        "status": "complete",
                        "outcome_sha256": outcome_key,
                        "candidate": candidate,
                    },
                )
        candidates.append(candidate)
        if candidate.get("status") == "accepted":
            accepted_so_far.append(candidate)
    return candidates, telemetry


def run_incremental(
    *,
    args: argparse.Namespace,
    session: Path,
    local_island: Any,
    progressive: Any,
    output: Path,
    evaluations: list[dict[str, Any]],
    source_rows: list[dict[str, Any]],
    chunks: dict[int, dict[str, Any]],
    chunk_paths: dict[int, Path],
    existing_me: list[dict[str, Any]],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any],
]:
    started = time.monotonic()
    cache_root = args.incremental_cache_dir.expanduser().resolve()
    if args.force:
        shutil.rmtree(cache_root, ignore_errors=True)
    cache_root.mkdir(parents=True, exist_ok=True)
    digest_memo = FileDigestMemo(cache_root.parent / "file-digests-v1", session=session)
    scripts = Path(__file__).resolve().parent
    algorithm = algorithm_fingerprint(
        [
            Path(__file__),
            scripts / "live-causal-local-island-micro-asr.py",
            scripts / "live-progressive-target-me.py",
            scripts / "live_recovery_incremental_cache.py",
        ],
        digest_memo,
    )
    model = digest_memo.fingerprint(Path(args.model))
    whisper_cli_path = resolve_executable(args.whisper_cli)
    whisper_cli_fingerprint = (
        digest_memo.fingerprint(whisper_cli_path)
        if whisper_cli_path
        else {"path": args.whisper_cli, "status": "missing"}
    )
    preparation_context, candidate_context = incremental_contexts(
        args=args,
        algorithm=algorithm,
        model=model,
        whisper_cli=whisper_cli_fingerprint,
    )
    evaluations_by_chunk: dict[int, list[dict[str, Any]]] = {}
    for row in evaluations:
        evaluations_by_chunk.setdefault(safe_int(row.get("chunk_index")), []).append(row)
    source_by_chunk: dict[int, list[dict[str, Any]]] = {}
    for row in source_rows:
        source_by_chunk.setdefault(safe_int(row.get("chunk_index")), []).append(row)

    current_inputs: dict[int, str] = {}
    current_components: dict[int, dict[str, str]] = {}
    chain_sha256 = preparation_context["sha256"]
    for chunk_index in sorted(chunks):
        chunk = chunks[chunk_index]
        chunk_path = chunk_paths.get(chunk_index)
        descriptor = {
            "session": session.name,
            "chunk_index": chunk_index,
            "chunk_json": digest_memo.fingerprint(chunk_path) if chunk_path else None,
            "chunk_files": payload_file_fingerprints(session, [chunk], digest_memo),
            "evaluations": evaluations_by_chunk.get(chunk_index) or [],
            "source_selection": source_by_chunk.get(chunk_index) or [],
            "baseline_me_through_chunk": [
                {
                    key: row.get(key)
                    for key in ("id", "chunk_index", "source", "start", "end", "text")
                }
                for row in local_island.existing_me_through_chunk(existing_me, chunk_index)
            ],
        }
        components = {
            "chunk_json": content_hash(descriptor["chunk_json"]),
            "chunk_files": content_hash(descriptor["chunk_files"]),
            "evaluations": content_hash(descriptor["evaluations"]),
            "source_selection": content_hash(descriptor["source_selection"]),
            "baseline_me": content_hash(descriptor["baseline_me_through_chunk"]),
            "previous_chain": chain_sha256,
        }
        chain_sha256 = content_hash(
            {"previous_chain_sha256": chain_sha256, "input_components": components}
        )
        current_inputs[chunk_index] = chain_sha256
        current_components[chunk_index] = components

    state_path = cache_root / "state.json"
    previous_state = read_cache_json(state_path)
    previous_candidate_context = (
        previous_state.get("candidate_context")
        if isinstance(previous_state.get("candidate_context"), dict)
        else {}
    )
    candidate_invalidation_reason = None
    if previous_candidate_context and (
        previous_candidate_context.get("sha256") != candidate_context["sha256"]
    ):
        candidate_invalidation_reason = "candidate_context_changed"
    plan = chunk_plan(
        previous_state=previous_state,
        context_sha256=preparation_context["sha256"],
        current_inputs=current_inputs,
        current_components=current_components,
    )
    if args.force and current_inputs:
        plan = {
            "reused_indexes": [],
            "process_indexes": sorted(current_inputs),
            "earliest_invalidated_chunk": min(current_inputs),
            "invalidation_reason": "forced_refresh",
        }
    reused_indexes = set(plan["reused_indexes"])
    process_indexes = set(plan["process_indexes"])
    entries = previous_state.get("entries") if isinstance(previous_state.get("entries"), dict) else {}
    entries = dict(entries)
    earliest_invalidated = plan.get("earliest_invalidated_chunk")
    if earliest_invalidated is not None:
        entries = {
            key: value
            for key, value in entries.items()
            if safe_int(key) < safe_int(earliest_invalidated)
        }

    all_decisions: list[dict[str, Any]] = []
    all_residual_rows: list[dict[str, Any]] = []
    all_prepared: list[dict[str, Any]] = []
    audio = AudioStore(session, chunks)
    group_index_offset = 0
    reused_group_count = 0
    processed_group_count = 0
    processed_chunk_count = 0
    recovered_object_count = 0

    for chunk_index in sorted(chunks):
        input_sha256 = current_inputs[chunk_index]
        entry = entries.get(str(chunk_index)) if chunk_index in reused_indexes else None
        manifest_path = (
            resolve_cached_path(entry.get("object_path"), cache_root)
            if isinstance(entry, dict)
            else object_directory(cache_root, input_sha256) / "object.json"
        )
        manifest = read_cache_json(manifest_path)
        cache_object_valid = bool(
            manifest.get("status") == "complete"
            and manifest.get("input_sha256") == input_sha256
            and (manifest_path.parent / "selection.jsonl").exists()
            and (manifest_path.parent / "residual_candidates.jsonl").exists()
            and (manifest_path.parent / "prepared.jsonl").exists()
        )
        if chunk_index in reused_indexes and not cache_object_valid:
            process_indexes.update(index for index in chunks if index >= chunk_index)
            reused_indexes.difference_update(index for index in chunks if index >= chunk_index)
            plan["earliest_invalidated_chunk"] = chunk_index
            plan["invalidation_reason"] = "cache_object_corrupt"
            entries = {
                key: value for key, value in entries.items() if safe_int(key) < chunk_index
            }
        if chunk_index in process_indexes and cache_object_valid:
            recovered_object_count += 1

        if chunk_index in reused_indexes or (chunk_index in process_indexes and cache_object_valid):
            object_dir = manifest_path.parent
            decisions = read_jsonl(object_dir / "selection.jsonl")
            residual_rows = read_jsonl(object_dir / "residual_candidates.jsonl")
            prepared = read_jsonl(object_dir / "prepared.jsonl")
            selected_group_count = safe_int(manifest.get("selected_group_count"))
            reused_group_count += selected_group_count
        else:
            processed_chunk_count += 1
            object_dir = object_directory(cache_root, input_sha256)
            if object_dir.exists():
                shutil.rmtree(object_dir)
            object_dir.mkdir(parents=True, exist_ok=True)
            selected, decisions = selection_decisions(source_by_chunk.get(chunk_index) or [])
            groups = group_selected(selected)
            selected_group_count = len(groups)
            residual_rows, prepared = prepare_groups(
                session=session,
                groups=groups,
                evaluations=evaluations,
                chunks=chunks,
                audio=audio,
                progressive=progressive,
                output=object_dir,
                group_index_offset=group_index_offset,
                prepared_input_sha256=input_sha256,
            )
            write_jsonl(object_dir / "selection.jsonl", decisions)
            write_jsonl(object_dir / "residual_candidates.jsonl", residual_rows)
            write_jsonl(object_dir / "prepared.jsonl", prepared)
            write_cache_json(
                object_dir / "object.json",
                {
                    "schema": "murmurmark.live_recovery_incremental_object/v1",
                    "stage": "causal_remote_active_me_separation_v1",
                    "status": "complete",
                    "input_sha256": input_sha256,
                    "input_components": current_components[chunk_index],
                    "chunk_index": chunk_index,
                    "selection_count": len(decisions),
                    "selected_group_count": selected_group_count,
                    "residual_method_evaluation_count": len(residual_rows),
                    "prepared_group_count": len(prepared),
                },
            )
            processed_group_count += selected_group_count
        entries[str(chunk_index)] = {
            "input_sha256": input_sha256,
            "input_components": current_components[chunk_index],
            "object_path": relative_or_absolute(manifest_path, cache_root),
            "selection_count": len(decisions),
            "selected_group_count": selected_group_count,
            "prepared_group_count": len(prepared),
        }
        all_decisions.extend(decisions)
        all_residual_rows.extend(residual_rows)
        all_prepared.extend(prepared)
        group_index_offset += selected_group_count

    candidates, candidate_telemetry = finalize_candidates(
        session=session,
        prepared=all_prepared,
        chunks=chunks,
        existing_me=existing_me,
        local_island=local_island,
        progressive=progressive,
        model=args.model,
        language=args.language,
        whisper_cli=args.whisper_cli,
        max_asr_groups=args.max_asr_groups,
        skip_asr=args.skip_asr,
        force=False,
        runtime_shadow=args.runtime_shadow,
        outcome_cache_root=cache_root / "candidate-outcomes",
        candidate_context_sha256=candidate_context["sha256"],
    )
    watermark = max(current_inputs, default=0)
    telemetry = {
        "schema": "murmurmark.live_recovery_incremental_stage/v1",
        "stage": "causal_remote_active_me_separation_v1",
        "context_sha256": preparation_context["sha256"],
        "preparation_context_sha256": preparation_context["sha256"],
        "candidate_context_sha256": candidate_context["sha256"],
        "candidate_invalidation_reason": candidate_invalidation_reason,
        "watermark_chunk_index": watermark,
        "input_chunk_count": len(current_inputs),
        "new_chunk_count": processed_chunk_count,
        "reused_chunk_count": len(reused_indexes) + recovered_object_count,
        "processed_group_count": processed_group_count,
        "reused_group_count": reused_group_count,
        "prepared_cache_hits": reused_group_count,
        "prepared_cache_misses": processed_group_count,
        "recovered_object_count": recovered_object_count,
        "earliest_invalidated_chunk": plan.get("earliest_invalidated_chunk"),
        "invalidation_reason": plan.get("invalidation_reason"),
        "changed_input_components": plan.get("changed_components") or [],
        "elapsed_sec": round(time.monotonic() - started, 3),
        **candidate_telemetry,
        **digest_memo.telemetry(),
    }
    write_cache_json(
        state_path,
        {
            "schema": "murmurmark.live_recovery_incremental_stage_state/v1",
            "stage": telemetry["stage"],
            "cache_root": str(cache_root),
            "context_sha256": preparation_context["sha256"],
            "context": preparation_context,
            "preparation_context": preparation_context,
            "candidate_context": candidate_context,
            "watermark_chunk_index": watermark,
            "entries": entries,
            "last_run": telemetry,
        },
    )
    digest_memo.save()
    return candidates, all_decisions, all_residual_rows, all_prepared, telemetry


def render_markdown(state: dict[str, Any], candidates: list[dict[str, Any]]) -> str:
    accepted = [row for row in candidates if row.get("status") == "accepted"]
    lines = [
        "# Causal Remote-Active Me Separation v1",
        "",
        f"- Status: `{state.get('status')}`",
        f"- Selected causal groups: `{state.get('selected_group_count')}`",
        f"- Audio-gate passes: `{state.get('audio_gate_pass_count')}`",
        f"- Micro-ASR candidates: `{state.get('candidate_count')}`",
        f"- Accepted candidates: `{len(accepted)}` / `{state.get('accepted_candidate_seconds')}s`",
        "- Batch authoritative: `true`",
        "- Promotion allowed: `false`",
        "",
        "## Accepted",
        "",
    ]
    if not accepted:
        lines.append("No candidate passed the full causal audio, speaker, ASR and remote-forbidden contract.")
    for row in accepted:
        lines.extend(
            [
                f"- `{row.get('start'):.2f}-{row.get('end'):.2f}s` "
                f"`{row.get('residual_method')}`: {row.get('text')}",
            ]
        )
    lines.extend(
        [
            "",
            "## Contract",
            "",
            "Selection uses committed PCM, past-only training and Target-Me evidence. Batch text and timing are evaluation-only.",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build causal remote-conditioned residual candidates for remote-active Me intervals."
    )
    parser.add_argument("session", type=Path)
    parser.add_argument("--baseline-profile", default=BASELINE_PROFILE)
    parser.add_argument(
        "--model",
        default=str(Path.home() / ".local/share/murmurmark/models/whisper.cpp/ggml-large-v3-q5_0.bin"),
    )
    parser.add_argument("--language", default="ru")
    parser.add_argument("--whisper-cli", default=os.environ.get("WHISPER_CLI", "whisper-cli"))
    parser.add_argument("--max-asr-groups", type=int, default=80)
    parser.add_argument("--skip-asr", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--through-chunk-index",
        type=int,
        default=0,
        help="Recording-time cutoff. Zero keeps the historical full replay behavior.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Optional isolated output directory used by the recording-time runtime profile.",
    )
    parser.add_argument(
        "--source-selection",
        type=Path,
        help="Optional local-island selection JSONL from an isolated runtime profile.",
    )
    parser.add_argument(
        "--existing-me-json",
        type=Path,
        help="Optional {turns:[...]} live-only baseline used for causal duplicate checks.",
    )
    parser.add_argument(
        "--runtime-shadow",
        action="store_true",
        help="Mark this materialization as explicit-only recording-time runtime evidence.",
    )
    parser.add_argument(
        "--incremental-cache-dir",
        type=Path,
        help="Optional persistent content-addressed cache for recording-time invocations.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    session = args.session.expanduser().resolve()
    local_island = load_script(
        "live-causal-local-island-micro-asr.py",
        "murmurmark_remote_active_local_island_helper",
    )
    progressive = local_island.load_progressive_module()
    output = (
        args.output_dir.expanduser().resolve()
        if args.output_dir
        else session / OUTPUT_RELATIVE
    )
    output.mkdir(parents=True, exist_ok=True)
    evaluations = read_jsonl(session / "derived/live/causal-target-me/evaluations.jsonl")
    if args.through_chunk_index > 0:
        evaluations = [
            row
            for row in evaluations
            if safe_int(row.get("chunk_index")) <= args.through_chunk_index
        ]
    source_selection = (
        args.source_selection.expanduser().resolve()
        if args.source_selection
        else session / "derived/live/causal-local-island-micro-asr-v2/selection.jsonl"
    )
    source_rows = read_jsonl(source_selection)
    if args.through_chunk_index > 0:
        source_rows = [
            row
            for row in source_rows
            if safe_int(row.get("chunk_index")) <= args.through_chunk_index
        ]
    chunk_paths = sorted((session / "derived/live/chunks").glob("*/chunk.json"))
    chunks = {
        safe_int(row.get("index")): row
        for row in (read_json(path) for path in chunk_paths)
        if row
        and (
            args.through_chunk_index <= 0
            or safe_int(row.get("index")) <= args.through_chunk_index
        )
    }
    baseline = (
        read_json(args.existing_me_json.expanduser().resolve())
        if args.existing_me_json
        else read_json(
            session / "derived/live/target-me-shadow" / args.baseline_profile / "draft.json"
        )
    )
    existing_me = [
        row
        for row in baseline.get("turns") or []
        if isinstance(row, dict) and row.get("role") == "Me"
    ]
    chunk_path_by_index = {
        safe_int(row.get("index")): path
        for path in chunk_paths
        for row in [read_json(path)]
        if row
    }
    incremental: dict[str, Any] | None = None
    if args.incremental_cache_dir:
        candidates, decisions, residual_rows, prepared, incremental = run_incremental(
            args=args,
            session=session,
            local_island=local_island,
            progressive=progressive,
            output=output,
            evaluations=evaluations,
            source_rows=source_rows,
            chunks=chunks,
            chunk_paths=chunk_path_by_index,
            existing_me=existing_me,
        )
        selected_segment_count = sum(row.get("status") == "selected" for row in decisions)
        selected_group_count = len(
            group_selected([row for row in decisions if row.get("status") == "selected"])
        )
    else:
        selected, decisions = selection_decisions(source_rows)
        groups = group_selected(selected)
        residual_rows, prepared = prepare_groups(
            session=session,
            groups=groups,
            evaluations=evaluations,
            chunks=chunks,
            audio=AudioStore(session, chunks),
            progressive=progressive,
            output=output,
        )
        candidates, _candidate_telemetry = finalize_candidates(
            session=session,
            prepared=prepared,
            chunks=chunks,
            existing_me=existing_me,
            local_island=local_island,
            progressive=progressive,
            model=args.model,
            language=args.language,
            whisper_cli=args.whisper_cli,
            max_asr_groups=args.max_asr_groups,
            skip_asr=args.skip_asr,
            force=args.force,
            runtime_shadow=args.runtime_shadow,
        )
        selected_segment_count = len(selected)
        selected_group_count = len(groups)

    accepted = [row for row in candidates if row.get("status") == "accepted"]
    try:
        output_relative = output.relative_to(session)
    except ValueError:
        output_relative = output
    state = {
        "schema": SCHEMA,
        "generator": {"name": "live-causal-remote-active-me-separation", "version": SCRIPT_VERSION},
        "created_at": now_iso(),
        "status": "completed",
        "session": session.name,
        "baseline_profile": args.baseline_profile,
        "selection_mode": "recording_time_causal_remote_active_separation_v1",
        "runtime_shadow": args.runtime_shadow,
        "through_chunk_index": args.through_chunk_index or None,
        "source_selection_count": len(source_rows),
        "selected_segment_count": selected_segment_count,
        "selected_group_count": selected_group_count,
        "residual_method_evaluation_count": len(residual_rows),
        "audio_gate_pass_count": len(prepared),
        "candidate_count": len(candidates),
        "accepted_candidate_count": len(accepted),
        "accepted_candidate_seconds": round(sum(safe_float(row.get("duration_sec")) for row in accepted), 3),
        "used_batch_fields_for_selection": False,
        "timeline_causal": True,
        "batch_authoritative": True,
        "promotion_allowed": False,
        "incremental_cache": incremental,
        "methods": list(METHODS),
        "thresholds": {
            "remote_active_min_db": REMOTE_ACTIVE_MIN_DB,
            "min_training_seconds": MIN_TRAINING_SECONDS,
            "max_training_seconds": MAX_TRAINING_SECONDS,
            "max_candidate_seconds": MAX_CANDIDATE_SECONDS,
            "min_projection_reduction_db": MIN_PROJECTION_REDUCTION_DB,
            "max_remote_audio_strength": MAX_REMOTE_AUDIO_STRENGTH,
            "min_asr_score": MIN_ASR_SCORE,
            "min_source_alignment": MIN_SOURCE_ALIGNMENT,
            "max_remote_text_similarity": MAX_REMOTE_TEXT_SIMILARITY,
            "max_remote_token_recall": MAX_REMOTE_TOKEN_RECALL,
            "max_asr_groups": args.max_asr_groups,
        },
        "outputs": {
            "selection": str(output_relative / "selection.jsonl"),
            "residual_candidates": str(output_relative / "residual_candidates.jsonl"),
            "candidates": str(output_relative / "candidates.jsonl"),
            "report": str(output_relative / "report.md"),
        },
    }
    write_jsonl(output / "selection.jsonl", decisions)
    write_jsonl(output / "residual_candidates.jsonl", residual_rows)
    write_jsonl(output / "candidates.jsonl", candidates)
    write_json(output / "state.json", state)
    (output / "report.md").write_text(render_markdown(state, candidates), encoding="utf-8")
    print(f"status: {state['status']}")
    print(f"selected_groups: {state['selected_group_count']}")
    print(f"audio_gate_passes: {state['audio_gate_pass_count']}")
    print(f"candidates: {state['candidate_count']}")
    print(f"accepted: {state['accepted_candidate_count']}")
    print(f"accepted_seconds: {state['accepted_candidate_seconds']}")
    print(f"report: {output / 'state.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
