#!/usr/bin/env python3
"""Shadow offline AEC v2 lab for MurmurMark.

This is an experiment runner, not a production Echo Guard engine. It reads the
already materialized mic/remote working WAV files, writes separate candidate
artifacts, and never updates mic_for_asr.wav.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from scipy import linalg, signal
from scipy.fft import irfft, next_fast_len, rfft
from scipy.io import wavfile


EPSILON = 1.0e-12
SCRIPT_VERSION = "0.1.0"


@dataclass(frozen=True)
class CandidateConfig:
    key: str
    tail_ms: float
    bases: tuple[str, ...]
    residual_mask: bool
    remote_target_db: float | None = None
    remote_max_mask_db: float | None = None
    double_talk_max_mask_db: float | None = None
    remote_mask_min_corr: float = 0.08


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run shadow offline_aec_v2 Echo Guard lab.")
    parser.add_argument("session", type=Path)
    parser.add_argument("--sample-rate", type=int, default=16_000)
    parser.add_argument("--highpass-hz", type=float, default=100.0)
    parser.add_argument("--lowpass-hz", type=float, default=7_600.0)
    parser.add_argument("--delay-window-sec", type=float, default=2.0)
    parser.add_argument("--delay-hop-sec", type=float, default=1.0)
    parser.add_argument("--min-delay-ms", type=float, default=-500.0)
    parser.add_argument("--max-delay-ms", type=float, default=2_000.0)
    parser.add_argument("--min-delay-confidence", type=float, default=1.15)
    parser.add_argument("--regularization", type=float, default=1.0e-2)
    parser.add_argument("--remote-only-residual-target-db", type=float, default=-52.0)
    parser.add_argument("--remote-only-max-mask-db", type=float, default=24.0)
    parser.add_argument("--double-talk-max-mask-db", type=float, default=6.0)
    parser.add_argument("--fit-max-sec", type=float, default=240.0)
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument("--write-all-audio", action="store_true")
    parser.add_argument("--asr-audit", action="store_true", help="Run local faster-whisper clip audit.")
    parser.add_argument("--asr-max-clips", type=int, default=6)
    parser.add_argument("--faster-whisper-model", type=Path, default=None)
    return parser.parse_args()


def read_wav_float(path: Path) -> tuple[int, np.ndarray]:
    sample_rate, data = wavfile.read(path)
    if data.ndim > 1:
        data = data.mean(axis=1)
    if np.issubdtype(data.dtype, np.integer):
        info = np.iinfo(data.dtype)
        scale = float(max(abs(info.min), info.max))
        audio = data.astype(np.float32) / scale
    else:
        audio = data.astype(np.float32)
    return sample_rate, np.nan_to_num(audio)


def write_wav_float(path: Path, sample_rate: int, audio: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    wavfile.write(path, sample_rate, np.clip(audio, -1.0, 1.0).astype(np.float32))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def model_path(args: argparse.Namespace) -> Path:
    if args.faster_whisper_model:
        return args.faster_whisper_model
    explicit = os.environ.get("MURMURMARK_FASTER_WHISPER_MODEL")
    if explicit:
        return Path(explicit)
    return Path.home() / ".local" / "share" / "murmurmark" / "models" / "faster-whisper" / "large-v3"


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def resample_if_needed(audio: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    if source_rate == target_rate:
        return audio.astype(np.float32)
    divisor = math.gcd(source_rate, target_rate)
    return signal.resample_poly(audio, target_rate // divisor, source_rate // divisor).astype(np.float32)


def speech_band(audio: np.ndarray, sample_rate: int, highpass_hz: float, lowpass_hz: float) -> np.ndarray:
    result = audio.astype(np.float32)
    if highpass_hz > 0:
        sos = signal.butter(4, highpass_hz, btype="highpass", fs=sample_rate, output="sos")
        result = signal.sosfilt(sos, result).astype(np.float32)
    if 0 < lowpass_hz < sample_rate / 2:
        sos = signal.butter(6, lowpass_hz, btype="lowpass", fs=sample_rate, output="sos")
        result = signal.sosfilt(sos, result).astype(np.float32)
    return result


def rms_db(audio: np.ndarray) -> float:
    return 20.0 * math.log10(math.sqrt(float(np.mean(np.asarray(audio, dtype=np.float64) ** 2)) + EPSILON) + EPSILON)


def energy_db(audio: np.ndarray) -> float:
    return 10.0 * math.log10(float(np.mean(np.asarray(audio, dtype=np.float64) ** 2)) + EPSILON)


def normalized_corr(left: np.ndarray, right: np.ndarray) -> float:
    if left.size == 0 or right.size == 0:
        return 0.0
    size = min(left.size, right.size)
    a = left[:size].astype(np.float64) - float(np.mean(left[:size]))
    b = right[:size].astype(np.float64) - float(np.mean(right[:size]))
    denom = math.sqrt(float(np.dot(a, a) * np.dot(b, b))) + EPSILON
    return float(np.dot(a, b) / denom)


def frame_vad_ratio(before: np.ndarray, after: np.ndarray, sample_rate: int, threshold_db: float = -50.0) -> float:
    frame = max(1, int(round(sample_rate * 0.02)))
    before_count = 0
    after_count = 0
    size = min(before.size, after.size)
    for start in range(0, size, frame):
        before_frame = before[start : start + frame]
        after_frame = after[start : start + frame]
        if before_frame.size == 0 or after_frame.size == 0:
            continue
        if rms_db(before_frame) >= threshold_db:
            before_count += 1
        if rms_db(after_frame) >= threshold_db:
            after_count += 1
    return 1.0 if before_count == 0 else after_count / before_count


def gcc_phat(mic: np.ndarray, remote: np.ndarray, sample_rate: int, min_delay_ms: float, max_delay_ms: float) -> dict[str, Any]:
    size = min(mic.size, remote.size)
    if size < sample_rate // 4:
        return {"delay_ms": None, "confidence": 0.0, "peak": 0.0}
    mic = mic[:size].astype(np.float32) - float(np.mean(mic[:size]))
    remote = remote[:size].astype(np.float32) - float(np.mean(remote[:size]))
    taper = np.hanning(size).astype(np.float32)
    mic *= taper
    remote *= taper
    fft_size = next_fast_len(size * 2 - 1)
    cross_power = rfft(mic, fft_size) * np.conj(rfft(remote, fft_size))
    cross_power /= np.maximum(np.abs(cross_power), EPSILON)
    corr = irfft(cross_power, fft_size)
    corr = np.concatenate((corr[-(size - 1) :], corr[:size]))
    lags = np.arange(-size + 1, size)
    min_lag = int(round(min_delay_ms * sample_rate / 1_000.0))
    max_lag = int(round(max_delay_ms * sample_rate / 1_000.0))
    mask = (lags >= min_lag) & (lags <= max_lag)
    if not np.any(mask):
        return {"delay_ms": None, "confidence": 0.0, "peak": 0.0}
    limited = np.abs(corr[mask])
    limited_lags = lags[mask]
    peak_index = int(np.argmax(limited))
    peak_lag = int(limited_lags[peak_index])
    peak = float(limited[peak_index])
    second_mask = np.abs(limited_lags - peak_lag) > int(round(sample_rate * 0.05))
    second = float(np.max(limited[second_mask])) if np.any(second_mask) else 0.0
    return {
        "delay_ms": peak_lag * 1_000.0 / sample_rate,
        "confidence": peak / (second + EPSILON),
        "peak": peak,
    }


def load_speaker_state(session: Path) -> list[dict[str, Any]]:
    path = session / "derived" / "preprocess" / "echo" / "speaker_state.jsonl"
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def simple_state_rows(remote: np.ndarray, mic: np.ndarray, sample_rate: int, hop_sec: float = 2.0) -> list[dict[str, Any]]:
    hop = max(1, int(round(hop_sec * sample_rate)))
    remote_levels = []
    mic_levels = []
    bounds: list[tuple[int, int]] = []
    for start in range(0, min(remote.size, mic.size), hop):
        end = min(start + hop, remote.size, mic.size)
        bounds.append((start, end))
        remote_levels.append(rms_db(remote[start:end]))
        mic_levels.append(rms_db(mic[start:end]))
    remote_threshold = max(float(np.percentile(remote_levels, 30)) + 4.0, -55.0)
    mic_threshold = max(float(np.percentile(mic_levels, 30)) + 4.0, -60.0)
    rows = []
    for index, (start, end) in enumerate(bounds):
        remote_db = remote_levels[index]
        mic_db = mic_levels[index]
        remote_active = remote_db >= remote_threshold
        mic_active = mic_db >= mic_threshold
        if remote_active and mic_active and mic_db <= remote_db - 6.0:
            state = "remote_only"
        elif remote_active and mic_active:
            state = "double_talk"
        elif mic_active:
            state = "local_only"
        else:
            state = "silence"
        rows.append(
            {
                "start": round(start / sample_rate, 3),
                "end": round(end / sample_rate, 3),
                "state": state,
                "remote_db": round(remote_db, 3),
                "mic_db": round(mic_db, 3),
                "confidence": 0.5,
            }
        )
    return rows


def estimate_delay_curve(
    remote: np.ndarray,
    mic: np.ndarray,
    sample_rate: int,
    args: argparse.Namespace,
) -> tuple[np.ndarray, list[dict[str, Any]], dict[str, Any]]:
    window = max(1, int(round(args.delay_window_sec * sample_rate)))
    hop = max(1, int(round(args.delay_hop_sec * sample_rate)))
    count = min(remote.size, mic.size)
    rows: list[dict[str, Any]] = []
    reliable: list[float] = []
    for index, start in enumerate(range(0, max(1, count - window + 1), hop)):
        end = min(count, start + window)
        remote_db = rms_db(remote[start:end])
        mic_db = rms_db(mic[start:end])
        estimate = gcc_phat(
            mic[start:end],
            remote[start:end],
            sample_rate,
            args.min_delay_ms,
            args.max_delay_ms,
        )
        delay = estimate["delay_ms"]
        is_reliable = bool(delay is not None and estimate["confidence"] >= args.min_delay_confidence)
        if is_reliable:
            reliable.append(float(delay))
        rows.append(
            {
                "index": index,
                "start_sec": round(start / sample_rate, 3),
                "end_sec": round(end / sample_rate, 3),
                "delay_ms": None if delay is None else round(float(delay), 3),
                "confidence": round(float(estimate["confidence"]), 4),
                "peak": round(float(estimate["peak"]), 6),
                "reliable": is_reliable,
                "remote_db": round(remote_db, 3),
                "mic_db": round(mic_db, 3),
            }
        )
    fallback = float(np.median(reliable)) if reliable else 0.0
    raw_delays = np.array([
        float(row["delay_ms"]) if row["reliable"] and row["delay_ms"] is not None else fallback
        for row in rows
    ], dtype=np.float64)
    if raw_delays.size >= 5:
        raw_delays = signal.medfilt(raw_delays, kernel_size=5)
    for index, row in enumerate(rows):
        row["smoothed_delay_ms"] = round(float(raw_delays[index]), 3)
    summary = {
        "windows": len(rows),
        "reliable_windows": len(reliable),
        "median_delay_ms": round(fallback, 3),
        "delay_p10_ms": None if not reliable else round(float(np.percentile(reliable, 10)), 3),
        "delay_p90_ms": None if not reliable else round(float(np.percentile(reliable, 90)), 3),
    }
    return raw_delays, rows, summary


def aligned_remote_from_delay_curve(
    remote: np.ndarray,
    delay_curve: np.ndarray,
    delay_rows: list[dict[str, Any]],
    sample_rate: int,
) -> np.ndarray:
    aligned = np.zeros_like(remote)
    for index, row in enumerate(delay_rows):
        start = int(round(float(row["start_sec"]) * sample_rate))
        end = int(round(float(row["end_sec"]) * sample_rate))
        if end <= start:
            continue
        delay_ms = float(delay_curve[min(index, delay_curve.size - 1)])
        delay_samples = int(round(delay_ms * sample_rate / 1_000.0))
        source_start = start - delay_samples
        source_end = end - delay_samples
        dest_start = start
        dest_end = end
        if source_start < 0:
            dest_start += -source_start
            source_start = 0
        if source_end > remote.size:
            dest_end -= source_end - remote.size
            source_end = remote.size
        if dest_end > dest_start and source_end > source_start:
            aligned[dest_start:dest_end] = remote[source_start:source_end]
    return aligned.astype(np.float32)


def basis_signal(name: str, remote: np.ndarray, sample_rate: int) -> np.ndarray:
    base = remote.astype(np.float32)
    if name == "remote":
        result = base
    elif name == "band_limited":
        result = speech_band(base, sample_rate, 250.0, min(3_800.0, sample_rate / 2 - 100.0))
    elif name == "clipped":
        result = np.clip(base * 2.5, -0.7, 0.7).astype(np.float32)
    elif name == "tanh":
        result = (np.tanh(base * 3.0) / math.tanh(3.0)).astype(np.float32)
    elif name == "compressed":
        result = (np.sign(base) * np.sqrt(np.abs(base))).astype(np.float32)
    elif name == "signed_power":
        result = (base * np.abs(base)).astype(np.float32)
    else:
        raise ValueError(f"unknown basis: {name}")
    source_rms = math.sqrt(float(np.mean(base.astype(np.float64) ** 2)) + EPSILON)
    result_rms = math.sqrt(float(np.mean(result.astype(np.float64) ** 2)) + EPSILON)
    if result_rms > EPSILON:
        result = result * float(source_rms / result_rms)
    return np.nan_to_num(result).astype(np.float32)


def fit_rows(rows: list[dict[str, Any]], sample_rate: int, max_sec: float) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    total = 0.0
    for row in rows:
        duration = safe_float(row.get("end", row.get("end_sec"))) - safe_float(row.get("start", row.get("start_sec")))
        if duration <= 0:
            continue
        selected.append(row)
        total += duration
        if total >= max_sec:
            break
    return selected


def row_bounds(row: dict[str, Any], sample_rate: int, count: int) -> tuple[int, int]:
    start = int(round(safe_float(row.get("start", row.get("start_sec"))) * sample_rate))
    end = int(round(safe_float(row.get("end", row.get("end_sec"))) * sample_rate))
    start = max(0, min(start, count))
    end = max(start, min(end, count))
    return start, end


def fit_fir_for_basis(
    reference: np.ndarray,
    target: np.ndarray,
    rows: list[dict[str, Any]],
    sample_rate: int,
    taps: int,
    regularization: float,
) -> np.ndarray:
    r_xx = np.zeros(taps, dtype=np.float64)
    p_yx = np.zeros(taps, dtype=np.float64)
    used = 0
    count = min(reference.size, target.size)
    for row in rows:
        start, end = row_bounds(row, sample_rate, count)
        x = reference[start:end].astype(np.float64)
        y = target[start:end].astype(np.float64)
        if x.size <= taps or y.size <= taps:
            continue
        x -= float(np.mean(x))
        y -= float(np.mean(y))
        corr_xx = signal.correlate(x, x, mode="full", method="fft")
        corr_yx = signal.correlate(y, x, mode="full", method="fft")
        center = x.size - 1
        r_xx += corr_xx[center : center + taps]
        p_yx += corr_yx[center : center + taps]
        used += 1
    if used == 0 or float(r_xx[0]) <= EPSILON:
        return np.zeros(taps, dtype=np.float64)
    toeplitz_col = r_xx.copy()
    toeplitz_col[0] += max(float(r_xx[0]) * regularization, EPSILON)
    try:
        return np.asarray(linalg.solve_toeplitz((toeplitz_col, toeplitz_col), p_yx, check_finite=False), dtype=np.float64)
    except Exception:
        return np.zeros(taps, dtype=np.float64)


def build_echo_hat(
    mic: np.ndarray,
    basis_refs: dict[str, np.ndarray],
    rows: list[dict[str, Any]],
    config: CandidateConfig,
    sample_rate: int,
    regularization: float,
) -> tuple[np.ndarray, dict[str, Any]]:
    taps = max(1, int(round(config.tail_ms * sample_rate / 1_000.0)))
    residual_target = mic.astype(np.float64).copy()
    echo_hat = np.zeros_like(residual_target)
    basis_reports: list[dict[str, Any]] = []
    for basis_name in config.bases:
        reference = basis_refs[basis_name]
        fir = fit_fir_for_basis(reference, residual_target, rows, sample_rate, taps, regularization)
        component = signal.fftconvolve(reference.astype(np.float64), fir, mode="full")[: reference.size]
        echo_hat += component
        residual_target -= component
        basis_reports.append(
            {
                "basis": basis_name,
                "tail_ms": config.tail_ms,
                "taps": taps,
                "filter_energy": round(float(np.sum(fir * fir)), 8),
                "filter_peak": round(float(np.max(np.abs(fir))) if fir.size else 0.0, 8),
            }
        )
    return echo_hat.astype(np.float64), {"basis_reports": basis_reports, "taps": taps}


def apply_residual_mask(
    clean: np.ndarray,
    remote_aligned: np.ndarray,
    rows: list[dict[str, Any]],
    sample_rate: int,
    args: argparse.Namespace,
    config: CandidateConfig,
) -> tuple[np.ndarray, list[dict[str, Any]]]:
    masked = clean.astype(np.float64).copy()
    mask_rows: list[dict[str, Any]] = []
    count = min(masked.size, remote_aligned.size)
    for row in rows:
        start, end = row_bounds(row, sample_rate, count)
        if end <= start:
            continue
        state = str(row.get("state") or "")
        before = masked[start:end].copy()
        corr = abs(normalized_corr(before, remote_aligned[start:end]))
        gain = 1.0
        reason = "none"
        if state.startswith("remote_only") and corr >= config.remote_mask_min_corr:
            clean_rms = math.sqrt(float(np.mean(before * before)) + EPSILON)
            target_db = config.remote_target_db if config.remote_target_db is not None else args.remote_only_residual_target_db
            max_mask_db = config.remote_max_mask_db if config.remote_max_mask_db is not None else args.remote_only_max_mask_db
            target_rms = 10.0 ** (target_db / 20.0)
            if clean_rms > target_rms:
                min_gain = 10.0 ** (-max_mask_db / 20.0)
                gain = max(min_gain, target_rms / (clean_rms + EPSILON))
                reason = "remote_only_residual"
        elif state.startswith("double_talk") and corr >= 0.35:
            max_mask_db = (
                config.double_talk_max_mask_db
                if config.double_talk_max_mask_db is not None
                else args.double_talk_max_mask_db
            )
            if max_mask_db > 0:
                gain = 10.0 ** (-max_mask_db / 20.0)
                reason = "double_talk_high_remote_similarity"
        if gain < 1.0:
            masked[start:end] *= gain
        mask_rows.append(
            {
                "start_sec": round(start / sample_rate, 3),
                "end_sec": round(end / sample_rate, 3),
                "state": state,
                "remote_similarity": round(corr, 5),
                "gain_db": round(20.0 * math.log10(gain + EPSILON), 3),
                "reason": reason,
            }
        )
    return masked, mask_rows


def candidate_metrics(
    key: str,
    mic: np.ndarray,
    clean: np.ndarray,
    echo_hat: np.ndarray,
    remote_aligned: np.ndarray,
    rows: list[dict[str, Any]],
    sample_rate: int,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    segment_rows: list[dict[str, Any]] = []
    remote_reductions: list[float] = []
    remote_similarity_after: list[float] = []
    local_deltas: list[float] = []
    local_vad_ratios: list[float] = []
    double_talk_local_ratios: list[float] = []
    harmful_seconds = 0.0
    artifact_segments = 0
    count = min(mic.size, clean.size, echo_hat.size, remote_aligned.size)
    for index, row in enumerate(rows):
        start, end = row_bounds(row, sample_rate, count)
        if end <= start:
            continue
        state = str(row.get("state") or "")
        before = mic[start:end].astype(np.float64)
        after = clean[start:end].astype(np.float64)
        remote = remote_aligned[start:end].astype(np.float64)
        duration = (end - start) / sample_rate
        before_power = float(np.mean(before * before) + EPSILON)
        after_power = float(np.mean(after * after) + EPSILON)
        reduction_db = 10.0 * math.log10(before_power / after_power)
        before_corr = abs(normalized_corr(before, remote))
        after_corr = abs(normalized_corr(after, remote))
        local_delta_db = 10.0 * math.log10(after_power / before_power)
        vad_ratio = frame_vad_ratio(before, after, sample_rate)
        artifact = bool(not np.all(np.isfinite(after)) or float(np.max(np.abs(after))) >= 0.999)
        if artifact:
            artifact_segments += 1
        if state.startswith("remote_only"):
            remote_reductions.append(reduction_db)
            remote_similarity_after.append(after_corr)
            if after_corr >= 0.12 and rms_db(after) >= -55.0:
                harmful_seconds += duration
        elif state == "local_only":
            local_deltas.append(local_delta_db)
            local_vad_ratios.append(vad_ratio)
        elif state.startswith("double_talk"):
            double_talk_local_ratios.append(vad_ratio)
            if after_corr >= 0.30 and rms_db(after) >= -50.0:
                harmful_seconds += duration * 0.5
        segment_rows.append(
            {
                "candidate": key,
                "index": index,
                "start_sec": round(start / sample_rate, 3),
                "end_sec": round(end / sample_rate, 3),
                "state": state,
                "reduction_db": round(reduction_db, 3),
                "remote_similarity_before": round(before_corr, 5),
                "remote_similarity_after": round(after_corr, 5),
                "local_energy_delta_db": round(local_delta_db, 3),
                "vad_duration_ratio": round(vad_ratio, 4),
                "artifact": artifact,
            }
        )
    remote_median_reduction = median(remote_reductions)
    local_delta_median = median(local_deltas, default=0.0)
    local_vad_median = median(local_vad_ratios, default=1.0)
    double_talk_vad_median = median(double_talk_local_ratios, default=1.0)
    max_abs = float(np.max(np.abs(clean))) if clean.size else 0.0
    finite = bool(np.all(np.isfinite(clean)))
    artifact_flags = {
        "finite": finite,
        "max_abs_clean": round(max_abs, 6),
        "clipping": max_abs >= 0.999,
        "artifact_segments": artifact_segments,
    }
    metrics = {
        "candidate": key,
        "remote_only_median_reduction_db": remote_median_reduction,
        "remote_similarity_after_median": median(remote_similarity_after, default=0.0),
        "harmful_remote_seconds_in_me_proxy": round(harmful_seconds, 3),
        "local_only_energy_delta_db_median": local_delta_median,
        "local_only_word_recall_proxy": round(min(1.0, max(0.0, local_vad_median)), 6),
        "opening_ack_recall_proxy": opening_ack_recall(mic, clean, sample_rate),
        "double_talk_local_recall_proxy": round(min(1.0, max(0.0, double_talk_vad_median)), 6),
        "artifact_flags": artifact_flags,
    }
    leak_report = {
        "schema": "murmurmark.echo.offline_aec_v2_asr_leak_report/v1",
        "candidate": key,
        "mode": "proxy_without_asr",
        "remote_token_leak_rate": None,
        "remote_token_leak_rate_proxy": metrics["remote_similarity_after_median"],
        "harmful_remote_seconds_in_me": metrics["harmful_remote_seconds_in_me_proxy"],
        "note": "ASR token audit is not run by this v0 lab yet; proxy uses residual remote similarity and remote-active seconds.",
    }
    preservation_report = {
        "schema": "murmurmark.echo.offline_aec_v2_near_end_preservation_report/v1",
        "candidate": key,
        "local_only_word_recall": metrics["local_only_word_recall_proxy"],
        "opening_ack_recall": metrics["opening_ack_recall_proxy"],
        "double_talk_local_recall": metrics["double_talk_local_recall_proxy"],
        "local_only_energy_delta_db_median": local_delta_median,
        "artifact_flags": artifact_flags,
    }
    return metrics, segment_rows, leak_report, preservation_report


def median(values: list[float], default: float | None = None) -> float | None:
    finite = np.array([value for value in values if math.isfinite(value)], dtype=np.float64)
    if finite.size == 0:
        return default
    return round(float(np.median(finite)), 3)


def opening_ack_recall(mic: np.ndarray, clean: np.ndarray, sample_rate: int) -> float:
    end = min(mic.size, clean.size, int(round(12.0 * sample_rate)))
    if end <= 0:
        return 1.0
    return round(min(1.0, max(0.0, frame_vad_ratio(mic[:end], clean[:end], sample_rate))), 6)


def score_candidate(metrics: dict[str, Any], baseline: dict[str, Any]) -> tuple[float, list[str], str]:
    reasons: list[str] = []
    score = 0.0
    reduction = safe_float(metrics.get("remote_only_median_reduction_db"), 0.0)
    baseline_reduction = safe_float(baseline.get("remote_only_median_reduction_db"), 0.0)
    local_recall = safe_float(metrics.get("local_only_word_recall_proxy"), 0.0)
    opening_recall = safe_float(metrics.get("opening_ack_recall_proxy"), 0.0)
    double_talk_recall = safe_float(metrics.get("double_talk_local_recall_proxy"), 0.0)
    harmful_seconds = safe_float(metrics.get("harmful_remote_seconds_in_me_proxy"), 999.0)
    artifact_flags = metrics.get("artifact_flags") if isinstance(metrics.get("artifact_flags"), dict) else {}
    if reduction >= baseline_reduction + 1.0:
        score += 35.0
        reasons.append("remote_reduction_beats_local_fir")
    else:
        reasons.append("remote_reduction_not_better_than_local_fir")
    if reduction >= 3.0:
        score += 20.0
        reasons.append("remote_reduction_gate_passed")
    if local_recall >= 0.98:
        score += 20.0
        reasons.append("local_recall_preserved")
    else:
        reasons.append("local_recall_regression")
    if opening_recall >= 0.98:
        score += 10.0
        reasons.append("opening_ack_preserved")
    else:
        reasons.append("opening_ack_regression")
    if double_talk_recall >= 0.95:
        score += 10.0
        reasons.append("double_talk_preserved")
    else:
        reasons.append("double_talk_regression")
    if harmful_seconds <= safe_float(baseline.get("harmful_remote_seconds_in_me_proxy"), 999.0):
        score += 5.0
        reasons.append("harmful_seconds_not_worse")
    if harmful_seconds <= 5.0:
        score += 15.0
        reasons.append("harmful_seconds_low")
    if artifact_flags.get("finite") is False or artifact_flags.get("clipping") is True:
        score -= 100.0
        reasons.append("artifact_gate_failed")
    passed = (
        reduction >= max(3.0, baseline_reduction + 1.0)
        and local_recall >= 0.98
        and opening_recall >= 0.98
        and double_talk_recall >= 0.95
        and artifact_flags.get("finite") is not False
        and artifact_flags.get("clipping") is not True
    )
    return round(score, 3), reasons, "shadow_candidate_passed_gates" if passed else "blocked_by_quality_gates"


def segment_candidate_score(row: dict[str, Any]) -> float:
    state = str(row.get("state") or "")
    reduction = safe_float(row.get("reduction_db"), 0.0)
    similarity_after = safe_float(row.get("remote_similarity_after"), 0.0)
    vad_ratio = safe_float(row.get("vad_duration_ratio"), 1.0)
    local_delta = abs(safe_float(row.get("local_energy_delta_db"), 0.0))
    if row.get("artifact") is True:
        return -1_000.0
    if state.startswith("remote_only"):
        return round(reduction * 3.0 - similarity_after * 80.0, 6)
    if state.startswith("double_talk"):
        return round(vad_ratio * 100.0 - similarity_after * 30.0 - local_delta * 2.0, 6)
    if state == "local_only":
        return round(vad_ratio * 100.0 - local_delta * 4.0, 6)
    return round(reduction - similarity_after * 20.0, 6)


def rank_segment_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(int(row.get("index", -1)), []).append(row)
    ranked: list[dict[str, Any]] = []
    for _, group in sorted(grouped.items()):
        scored = []
        for row in group:
            next_row = dict(row)
            next_row["segment_candidate_score"] = segment_candidate_score(next_row)
            scored.append(next_row)
        scored.sort(key=lambda item: safe_float(item.get("segment_candidate_score"), -1_000.0), reverse=True)
        for rank, row in enumerate(scored, start=1):
            row["segment_candidate_rank"] = rank
            row["segment_candidate_selected"] = rank == 1
            ranked.append(row)
    return ranked


def token_counts(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for token in re.findall(r"[\wёЁ]+", text.lower()):
        if len(token) <= 1:
            continue
        counts[token] = counts.get(token, 0) + 1
    return counts


def token_overlap_precision(reference_text: str, candidate_text: str) -> float:
    reference = token_counts(reference_text)
    candidate = token_counts(candidate_text)
    total = sum(candidate.values())
    if total == 0:
        return 0.0
    overlap = 0
    for token, count in candidate.items():
        overlap += min(count, reference.get(token, 0))
    return round(overlap / total, 6)


def token_overlap_recall(reference_text: str, candidate_text: str) -> float:
    reference = token_counts(reference_text)
    candidate = token_counts(candidate_text)
    total = sum(reference.values())
    if total == 0:
        return 1.0 if sum(candidate.values()) == 0 else 0.0
    overlap = 0
    for token, count in reference.items():
        overlap += min(count, candidate.get(token, 0))
    return round(overlap / total, 6)


def overlap_rate(reference_text: str, candidate_text: str) -> float:
    return token_overlap_precision(reference_text, candidate_text)


def transcribe_clip(model: Any, path: Path) -> str:
    segments, _ = model.transcribe(
        str(path),
        language="ru",
        beam_size=1,
        temperature=0.0,
        condition_on_previous_text=False,
        vad_filter=False,
        word_timestamps=False,
    )
    return " ".join(segment.text.strip() for segment in segments if segment.text.strip()).strip()


def select_asr_rows(rows: list[dict[str, Any]], state_prefix: str, limit: int) -> list[dict[str, Any]]:
    candidates = [
        row for row in rows
        if str(row.get("state", "")).startswith(state_prefix)
        and safe_float(row.get("end", row.get("end_sec"))) - safe_float(row.get("start", row.get("start_sec"))) >= 1.5
    ]
    if state_prefix.startswith("local_only"):
        return sorted(
            candidates,
            key=lambda row: (
                safe_float(row.get("mic_db"), -120.0),
                -safe_float(row.get("remote_db"), 0.0),
            ),
            reverse=True,
        )[:limit]
    return sorted(candidates, key=lambda row: safe_float(row.get("remote_db"), -120.0), reverse=True)[:limit]


def average(items: list[float]) -> float | None:
    finite = [item for item in items if math.isfinite(item)]
    return None if not finite else round(float(np.mean(np.asarray(finite, dtype=np.float64))), 6)


def choose_asr_candidate(candidate_stats: dict[str, dict[str, Any]]) -> dict[str, Any]:
    eligible: list[dict[str, Any]] = []
    for key, stats in candidate_stats.items():
        leak_delta = stats.get("remote_token_leak_delta")
        recall_delta = stats.get("local_only_word_recall_delta")
        if leak_delta is None or recall_delta is None:
            continue
        if float(leak_delta) < -0.02 and float(recall_delta) >= -0.02:
            eligible.append(
                {
                    "candidate": key,
                    "remote_token_leak_delta": float(leak_delta),
                    "local_only_word_recall_delta": float(recall_delta),
                    "remote_token_leak_rate": stats.get("remote_token_leak_rate"),
                    "local_only_word_recall": stats.get("local_only_word_recall"),
                }
            )
    if not eligible:
        return {
            "asr_candidate_gate_passed": False,
            "asr_candidate_gate_reason": "no_candidate_reduced_remote_tokens_without_local_recall_regression",
            "asr_selected_candidate": None,
        }
    selected = sorted(
        eligible,
        key=lambda row: (
            row["remote_token_leak_delta"],
            -row["local_only_word_recall_delta"],
        ),
    )[0]
    return {
        "asr_candidate_gate_passed": True,
        "asr_candidate_gate_reason": "remote_token_leak_reduced_without_local_recall_regression",
        "asr_selected_candidate": selected["candidate"],
        "asr_selected_candidate_metrics": selected,
    }


def run_asr_clip_audit(
    session: Path,
    out_dir: Path,
    remote: np.ndarray,
    mic: np.ndarray,
    local_fir_clean: np.ndarray | None,
    candidate_audio: dict[str, np.ndarray],
    proxy_selected_candidate: str,
    rows: list[dict[str, Any]],
    sample_rate: int,
    args: argparse.Namespace,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    path = model_path(args)
    if not path.exists():
        skipped = {
            "schema": "murmurmark.echo.offline_aec_v2_asr_leak_report/v1",
            "mode": "skipped",
            "skipped_reason": f"faster-whisper model not found: {path}",
            "remote_token_leak_rate": None,
            "candidates": {},
        }
        preservation = {
            "schema": "murmurmark.echo.offline_aec_v2_near_end_preservation_report/v1",
            "mode": "skipped",
            "skipped_reason": skipped["skipped_reason"],
            "candidates": {},
        }
        return skipped, preservation, {"asr_candidate_gate_passed": False, "asr_candidate_gate_reason": "asr_audit_skipped"}
    try:
        from faster_whisper import WhisperModel
    except Exception as error:  # pragma: no cover - environment dependent
        skipped = {
            "schema": "murmurmark.echo.offline_aec_v2_asr_leak_report/v1",
            "mode": "skipped",
            "skipped_reason": f"faster_whisper unavailable: {error}",
            "remote_token_leak_rate": None,
            "candidates": {},
        }
        preservation = {
            "schema": "murmurmark.echo.offline_aec_v2_near_end_preservation_report/v1",
            "mode": "skipped",
            "skipped_reason": skipped["skipped_reason"],
            "candidates": {},
        }
        return skipped, preservation, {"asr_candidate_gate_passed": False, "asr_candidate_gate_reason": "asr_audit_skipped"}

    model = WhisperModel(str(path), device="cpu", compute_type="int8")
    clips_dir = out_dir / "offline_aec_v2_asr_clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    count = min([remote.size, mic.size, *[audio.size for audio in candidate_audio.values()]])
    if local_fir_clean is not None:
        count = min(count, local_fir_clean.size)

    candidate_stats: dict[str, dict[str, Any]] = {
        key: {
            "remote_only_rows": [],
            "local_only_rows": [],
        }
        for key in sorted(candidate_audio)
    }
    remote_rows: list[dict[str, Any]] = []
    for index, row in enumerate(select_asr_rows(rows, "remote_only", args.asr_max_clips), start=1):
        start, end = row_bounds(row, sample_rate, count)
        stem = f"remote_only_{index:02d}_{start / sample_rate:.1f}s"
        remote_path = clips_dir / f"{stem}_remote.wav"
        local_path = clips_dir / f"{stem}_local_fir.wav"
        write_wav_float(remote_path, sample_rate, remote[start:end])
        if local_fir_clean is not None:
            write_wav_float(local_path, sample_rate, local_fir_clean[start:end])
        remote_text = transcribe_clip(model, remote_path)
        local_text = transcribe_clip(model, local_path) if local_fir_clean is not None else ""
        row_result: dict[str, Any] = {
            "index": index,
            "start_sec": round(start / sample_rate, 3),
            "end_sec": round(end / sample_rate, 3),
            "remote_text": remote_text,
            "local_fir_text": local_text,
            "local_fir_remote_token_overlap": token_overlap_precision(remote_text, local_text),
            "candidates": {},
        }
        for key, clean in sorted(candidate_audio.items()):
            candidate_path = clips_dir / f"{stem}_{key}.wav"
            write_wav_float(candidate_path, sample_rate, clean[start:end])
            candidate_text = transcribe_clip(model, candidate_path)
            overlap = token_overlap_precision(remote_text, candidate_text)
            candidate_row = {
                "text": candidate_text,
                "remote_token_overlap": overlap,
                "wav": str(candidate_path),
            }
            row_result["candidates"][key] = candidate_row
            candidate_stats[key]["remote_only_rows"].append(
                {
                    "index": index,
                    "start_sec": row_result["start_sec"],
                    "end_sec": row_result["end_sec"],
                    "remote_text": remote_text,
                    "candidate_text": candidate_text,
                    "local_fir_text": local_text,
                    "candidate_remote_token_overlap": overlap,
                    "local_fir_remote_token_overlap": row_result["local_fir_remote_token_overlap"],
                    "wav": str(candidate_path),
                }
            )
        remote_rows.append(row_result)

    local_rows: list[dict[str, Any]] = []
    for index, row in enumerate(select_asr_rows(rows, "local_only", args.asr_max_clips), start=1):
        start, end = row_bounds(row, sample_rate, count)
        stem = f"local_only_{index:02d}_{start / sample_rate:.1f}s"
        mic_path = clips_dir / f"{stem}_raw_mic.wav"
        local_path = clips_dir / f"{stem}_local_fir.wav"
        write_wav_float(mic_path, sample_rate, mic[start:end])
        if local_fir_clean is not None:
            write_wav_float(local_path, sample_rate, local_fir_clean[start:end])
        mic_text = transcribe_clip(model, mic_path)
        local_text = transcribe_clip(model, local_path) if local_fir_clean is not None else ""
        row_result = {
            "index": index,
            "start_sec": round(start / sample_rate, 3),
            "end_sec": round(end / sample_rate, 3),
            "raw_mic_text": mic_text,
            "local_fir_text": local_text,
            "local_fir_local_token_recall": token_overlap_recall(mic_text, local_text),
            "candidates": {},
        }
        for key, clean in sorted(candidate_audio.items()):
            candidate_path = clips_dir / f"{stem}_{key}.wav"
            write_wav_float(candidate_path, sample_rate, clean[start:end])
            candidate_text = transcribe_clip(model, candidate_path)
            recall = token_overlap_recall(mic_text, candidate_text)
            candidate_row = {
                "text": candidate_text,
                "local_token_recall": recall,
                "wav": str(candidate_path),
            }
            row_result["candidates"][key] = candidate_row
            candidate_stats[key]["local_only_rows"].append(
                {
                    "index": index,
                    "start_sec": row_result["start_sec"],
                    "end_sec": row_result["end_sec"],
                    "raw_mic_text": mic_text,
                    "candidate_text": candidate_text,
                    "local_fir_text": local_text,
                    "candidate_local_token_recall": recall,
                    "local_fir_local_token_recall": row_result["local_fir_local_token_recall"],
                    "wav": str(candidate_path),
                }
            )
        local_rows.append(row_result)

    baseline_leak = average([safe_float(row["local_fir_remote_token_overlap"]) for row in remote_rows])
    baseline_local_recall = average([safe_float(row["local_fir_local_token_recall"]) for row in local_rows])
    for key, stats in candidate_stats.items():
        leak_rate = average([safe_float(row["candidate_remote_token_overlap"]) for row in stats["remote_only_rows"]])
        local_recall = average([safe_float(row["candidate_local_token_recall"]) for row in stats["local_only_rows"]])
        stats["remote_token_leak_rate"] = leak_rate
        stats["local_fir_remote_token_leak_rate"] = baseline_leak
        stats["remote_token_leak_delta"] = None if leak_rate is None or baseline_leak is None else round(leak_rate - baseline_leak, 6)
        stats["local_only_word_recall"] = local_recall
        stats["local_fir_local_only_word_recall"] = baseline_local_recall
        stats["local_only_word_recall_delta"] = (
            None
            if local_recall is None or baseline_local_recall is None
            else round(local_recall - baseline_local_recall, 6)
        )
    choice = choose_asr_candidate(candidate_stats)
    top_key = choice.get("asr_selected_candidate") or proxy_selected_candidate
    top_stats = candidate_stats.get(str(top_key), {})
    leak_report = {
        "schema": "murmurmark.echo.offline_aec_v2_asr_leak_report/v1",
        "mode": "faster_whisper_clip_audit",
        "model": str(path),
        "proxy_selected_candidate": proxy_selected_candidate,
        "asr_selected_candidate": choice.get("asr_selected_candidate"),
        "asr_candidate_gate_passed": choice.get("asr_candidate_gate_passed"),
        "asr_candidate_gate_reason": choice.get("asr_candidate_gate_reason"),
        "remote_only_clips": len(remote_rows),
        "remote_token_leak_rate": top_stats.get("remote_token_leak_rate"),
        "local_fir_remote_token_leak_rate": baseline_leak,
        "remote_token_leak_delta": top_stats.get("remote_token_leak_delta"),
        "candidates": candidate_stats,
        "rows": remote_rows,
    }
    preservation_report = {
        "schema": "murmurmark.echo.offline_aec_v2_near_end_preservation_report/v1",
        "mode": "faster_whisper_clip_audit",
        "model": str(path),
        "proxy_selected_candidate": proxy_selected_candidate,
        "asr_selected_candidate": choice.get("asr_selected_candidate"),
        "asr_candidate_gate_passed": choice.get("asr_candidate_gate_passed"),
        "asr_candidate_gate_reason": choice.get("asr_candidate_gate_reason"),
        "local_only_clips": len(local_rows),
        "local_only_word_recall": top_stats.get("local_only_word_recall"),
        "local_fir_local_only_word_recall": baseline_local_recall,
        "local_only_word_recall_delta": top_stats.get("local_only_word_recall_delta"),
        "candidates": candidate_stats,
        "rows": local_rows,
    }
    return leak_report, preservation_report, choice


def read_local_fir_baseline(session: Path) -> dict[str, Any]:
    path = session / "derived" / "preprocess" / "echo" / "local_fir_report.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
    return {
        "remote_only_median_reduction_db": safe_float(metrics.get("remote_only_median_reduction_db"), 0.0),
        "local_only_word_recall_proxy": safe_float(metrics.get("local_only_vad_duration_ratio"), 1.0),
        "harmful_remote_seconds_in_me_proxy": 999.0,
    }


def compute_local_fir_baseline(
    session: Path,
    audio_dir: Path,
    mic: np.ndarray,
    remote_aligned: np.ndarray,
    rows: list[dict[str, Any]],
    sample_rate: int,
    args: argparse.Namespace,
) -> dict[str, Any]:
    clean_path = audio_dir / "mic_clean_local_fir.wav"
    if not clean_path.exists():
        return read_local_fir_baseline(session)
    clean_rate, clean_raw = read_wav_float(clean_path)
    clean = resample_if_needed(clean_raw, clean_rate, sample_rate)
    clean = speech_band(clean[: mic.size], sample_rate, args.highpass_hz, args.lowpass_hz)
    count = min(mic.size, clean.size, remote_aligned.size)
    metrics, _, leak_report, preservation_report = candidate_metrics(
        "local_fir",
        mic[:count],
        clean[:count],
        mic[:count] - clean[:count],
        remote_aligned[:count],
        rows,
        sample_rate,
    )
    return {
        **metrics,
        "source": "derived/preprocess/audio/mic_clean_local_fir.wav",
        "asr_leak_report": leak_report,
        "near_end_preservation_report": preservation_report,
    }


def default_candidate_configs() -> list[CandidateConfig]:
    return [
        CandidateConfig("linear_tail80", 80.0, ("remote",), False),
        CandidateConfig("linear_tail160", 160.0, ("remote",), False),
        CandidateConfig("linear_tail320", 320.0, ("remote",), False),
        CandidateConfig(
            "nonlinear_tail160_mask",
            160.0,
            ("remote", "band_limited", "clipped", "tanh", "compressed", "signed_power"),
            True,
        ),
        CandidateConfig(
            "nonlinear_tail160_remote_strongmask",
            160.0,
            ("remote", "band_limited", "clipped", "tanh", "compressed", "signed_power"),
            True,
            remote_target_db=-62.0,
            remote_max_mask_db=36.0,
            double_talk_max_mask_db=0.0,
        ),
        CandidateConfig(
            "nonlinear_tail160_remote_floor",
            160.0,
            ("remote", "band_limited", "clipped", "tanh", "compressed", "signed_power"),
            True,
            remote_target_db=-90.0,
            remote_max_mask_db=80.0,
            double_talk_max_mask_db=0.0,
            remote_mask_min_corr=0.0,
        ),
    ]


def main() -> int:
    args = parse_args()
    session = args.session
    audio_dir = session / "derived" / "preprocess" / "audio"
    echo_dir = session / "derived" / "preprocess" / "echo"
    out_dir = args.out_dir or echo_dir
    remote_path = audio_dir / "remote_for_aec.wav"
    mic_path = audio_dir / "mic_raw_for_asr.wav"
    if not remote_path.exists() or not mic_path.exists():
        raise SystemExit("working audio not found; run: murmurmark preprocess SESSION --echo clean --echo-engine local_fir")

    remote_rate, remote_raw = read_wav_float(remote_path)
    mic_rate, mic_raw = read_wav_float(mic_path)
    remote = resample_if_needed(remote_raw, remote_rate, args.sample_rate)
    mic = resample_if_needed(mic_raw, mic_rate, args.sample_rate)
    count = min(remote.size, mic.size)
    remote = speech_band(remote[:count], args.sample_rate, args.highpass_hz, args.lowpass_hz)
    mic = speech_band(mic[:count], args.sample_rate, args.highpass_hz, args.lowpass_hz)

    speaker_rows = load_speaker_state(session)
    if not speaker_rows:
        speaker_rows = simple_state_rows(remote, mic, args.sample_rate)
    remote_only_rows = [row for row in speaker_rows if str(row.get("state", "")).startswith("remote_only")]
    fit_remote_only_rows = fit_rows(remote_only_rows, args.sample_rate, args.fit_max_sec)
    if not fit_remote_only_rows:
        raise SystemExit("no remote_only speaker_state rows found; run local_fir first or use a session with remote bleed")

    delay_curve, delay_rows, delay_summary = estimate_delay_curve(remote, mic, args.sample_rate, args)
    remote_aligned = aligned_remote_from_delay_curve(remote, delay_curve, delay_rows, args.sample_rate)
    basis_refs = {
        name: basis_signal(name, remote_aligned, args.sample_rate)
        for name in ("remote", "band_limited", "clipped", "tanh", "compressed", "signed_power")
    }
    baseline = compute_local_fir_baseline(session, audio_dir, mic, remote_aligned, speaker_rows, args.sample_rate, args)

    candidate_rows: list[dict[str, Any]] = []
    all_segment_rows: list[dict[str, Any]] = []
    leak_reports: dict[str, Any] = {}
    preservation_reports: dict[str, Any] = {}
    best_key = None
    best_score = -1.0e9
    best_clean: np.ndarray | None = None
    best_echo_hat: np.ndarray | None = None
    candidate_audio: dict[str, np.ndarray] = {}
    asr_choice: dict[str, Any] = {
        "asr_candidate_gate_passed": False,
        "asr_candidate_gate_reason": "asr_audit_not_requested",
        "asr_selected_candidate": None,
    }

    for config in default_candidate_configs():
        echo_hat, fit_report = build_echo_hat(
            mic,
            basis_refs,
            fit_remote_only_rows,
            config,
            args.sample_rate,
            args.regularization,
        )
        clean = mic.astype(np.float64) - echo_hat
        mask_rows: list[dict[str, Any]] = []
        if config.residual_mask:
            clean, mask_rows = apply_residual_mask(clean, remote_aligned, speaker_rows, args.sample_rate, args, config)
        metrics, segment_rows, leak_report, preservation_report = candidate_metrics(
            config.key,
            mic,
            clean,
            echo_hat,
            remote_aligned,
            speaker_rows,
            args.sample_rate,
        )
        score, reasons, promotion_decision = score_candidate(metrics, baseline)
        candidate_row = {
            "schema": "murmurmark.echo.offline_aec_v2_candidate/v1",
            "candidate": config.key,
            "tail_ms": config.tail_ms,
            "bases": list(config.bases),
            "residual_mask": config.residual_mask,
            "score": score,
            "promotion_decision": promotion_decision,
            "reasons": reasons,
            "metrics": metrics,
            "fit": fit_report,
            "mask_segments": len([row for row in mask_rows if row.get("reason") != "none"]),
            "outputs": {
                "clean_mic": f"derived/preprocess/audio/mic_clean_offline_aec_v2_{config.key}.wav",
                "echo_hat": f"derived/preprocess/audio/echo_hat_offline_aec_v2_{config.key}.wav",
            },
        }
        candidate_rows.append(candidate_row)
        all_segment_rows.extend(segment_rows)
        leak_reports[config.key] = leak_report
        preservation_reports[config.key] = preservation_report
        candidate_audio[config.key] = clean.astype(np.float32)
        write_wav_float(audio_dir / f"mic_clean_offline_aec_v2_{config.key}.wav", args.sample_rate, clean)
        write_wav_float(audio_dir / f"echo_hat_offline_aec_v2_{config.key}.wav", args.sample_rate, echo_hat)
        if score > best_score:
            best_score = score
            best_key = config.key
            best_clean = clean
            best_echo_hat = echo_hat

    assert best_key is not None and best_clean is not None and best_echo_hat is not None
    write_wav_float(audio_dir / "mic_clean_offline_aec_v2.wav", args.sample_rate, best_clean)
    write_wav_float(audio_dir / "echo_hat_offline_aec_v2.wav", args.sample_rate, best_echo_hat)

    selected_candidate = next(row for row in candidate_rows if row["candidate"] == best_key)
    local_fir_clean: np.ndarray | None = None
    local_fir_path = audio_dir / "mic_clean_local_fir.wav"
    if local_fir_path.exists():
        local_fir_rate, local_fir_raw = read_wav_float(local_fir_path)
        local_fir_clean = resample_if_needed(local_fir_raw, local_fir_rate, args.sample_rate)
        local_fir_clean = speech_band(local_fir_clean[: mic.size], args.sample_rate, args.highpass_hz, args.lowpass_hz)
    if args.asr_audit:
        leak_reports[best_key], preservation_reports[best_key], asr_choice = run_asr_clip_audit(
            session,
            out_dir,
            remote,
            mic,
            local_fir_clean,
            candidate_audio,
            best_key,
            speaker_rows,
            args.sample_rate,
            args,
        )
        asr_candidates = leak_reports[best_key].get("candidates")
        if isinstance(asr_candidates, dict):
            for candidate_row in candidate_rows:
                candidate_key = str(candidate_row.get("candidate"))
                candidate_asr = asr_candidates.get(candidate_key)
                if isinstance(candidate_asr, dict):
                    candidate_row["asr_audit"] = {
                        "remote_token_leak_rate": candidate_asr.get("remote_token_leak_rate"),
                        "local_fir_remote_token_leak_rate": candidate_asr.get("local_fir_remote_token_leak_rate"),
                        "remote_token_leak_delta": candidate_asr.get("remote_token_leak_delta"),
                        "local_only_word_recall": candidate_asr.get("local_only_word_recall"),
                        "local_fir_local_only_word_recall": candidate_asr.get("local_fir_local_only_word_recall"),
                        "local_only_word_recall_delta": candidate_asr.get("local_only_word_recall_delta"),
                    }
    gates_passed = selected_candidate["promotion_decision"] == "shadow_candidate_passed_gates"
    report = {
        "schema": "murmurmark.echo.offline_aec_v2_report/v1",
        "version": SCRIPT_VERSION,
        "session": str(session),
        "engine": "offline_aec_v2_v0",
        "mode": "shadow_only",
        "inputs": {
            "mic": "derived/preprocess/audio/mic_raw_for_asr.wav",
            "remote": "derived/preprocess/audio/remote_for_aec.wav",
            "speaker_state": "derived/preprocess/echo/speaker_state.jsonl",
            "local_fir_report": "derived/preprocess/echo/local_fir_report.json",
        },
        "outputs": {
            "best_clean_mic": "derived/preprocess/audio/mic_clean_offline_aec_v2.wav",
            "best_echo_hat": "derived/preprocess/audio/echo_hat_offline_aec_v2.wav",
            "report": "derived/preprocess/echo/offline_aec_v2_report.json",
            "segments": "derived/preprocess/echo/offline_aec_v2_segments.jsonl",
            "candidates": "derived/preprocess/echo/offline_aec_v2_candidates.jsonl",
            "delay_curve": "derived/preprocess/echo/offline_aec_v2_delay_curve.jsonl",
            "window_metrics": "derived/preprocess/echo/offline_aec_v2_window_metrics.jsonl",
            "asr_leak_report": "derived/preprocess/echo/offline_aec_v2_asr_leak_report.json",
            "near_end_preservation_report": "derived/preprocess/echo/offline_aec_v2_near_end_preservation_report.json",
        },
        "parameters": {
            "sample_rate": args.sample_rate,
            "highpass_hz": args.highpass_hz,
            "lowpass_hz": args.lowpass_hz,
            "delay_window_sec": args.delay_window_sec,
            "delay_hop_sec": args.delay_hop_sec,
            "regularization": args.regularization,
            "fit_max_sec": args.fit_max_sec,
        },
        "delay_summary": delay_summary,
        "baseline": {"local_fir": baseline},
        "summary": {
            "selected_candidate": best_key,
            "selected_score": best_score,
            "promotion_decision": "shadow_only_not_promoted",
            "candidate_gate_passed": gates_passed,
            "candidate_gate_reason": selected_candidate["promotion_decision"],
            "local_fir_remains_default": True,
            "asr_audit_mode": leak_reports[best_key].get("mode"),
            "asr_selected_candidate": asr_choice.get("asr_selected_candidate"),
            "asr_candidate_gate_passed": asr_choice.get("asr_candidate_gate_passed"),
            "asr_candidate_gate_reason": asr_choice.get("asr_candidate_gate_reason"),
        },
        "selected_candidate": selected_candidate,
    }
    ranked_segment_rows = rank_segment_rows(all_segment_rows)

    write_json(out_dir / "offline_aec_v2_report.json", report)
    write_jsonl(out_dir / "offline_aec_v2_delay_curve.jsonl", delay_rows)
    write_jsonl(out_dir / "offline_aec_v2_segments.jsonl", ranked_segment_rows)
    write_jsonl(out_dir / "offline_aec_v2_candidates.jsonl", candidate_rows)
    write_jsonl(out_dir / "offline_aec_v2_window_metrics.jsonl", ranked_segment_rows)
    write_json(out_dir / "offline_aec_v2_asr_leak_report.json", leak_reports[best_key])
    write_json(out_dir / "offline_aec_v2_near_end_preservation_report.json", preservation_reports[best_key])

    print(f"offline_aec_v2_report: {out_dir / 'offline_aec_v2_report.json'}")
    print(f"selected_candidate: {best_key}")
    print(f"selected_score: {best_score}")
    print(f"candidate_gate: {selected_candidate['promotion_decision']}")
    print("promotion_decision: shadow_only_not_promoted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
