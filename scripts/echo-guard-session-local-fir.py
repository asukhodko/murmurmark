#!/usr/bin/env python3
"""Session-wide local FIR Echo Guard helper.

This helper is intentionally conservative. It creates a cleaned microphone
candidate for the whole session, but the Swift caller decides whether that file
becomes mic_for_asr.wav based on the quality gate in this report.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
from scipy import linalg, signal
from scipy.fft import irfft, next_fast_len, rfft
from scipy.io import wavfile


EPSILON = 1.0e-12
INPUT_PEAK_LIMIT = 0.999
MAX_SPARSE_OVERRANGE_MS = 250.0
MAX_SPARSE_OVERRANGE_FRACTION = 0.001
ACOUSTIC_MIN_REMOTE_ONLY_WINDOWS = 5
ACOUSTIC_COUPLED_SIMILARITY = 0.04
ACOUSTIC_SPEAKER_COUPLED_RATIO_MIN = 0.30
ACOUSTIC_LOW_LEAK_COUPLED_RATIO_MAX = 0.15
ACOUSTIC_LOW_LEAK_P75_MAX = 0.03
ACOUSTIC_LOW_LEAK_P90_MAX = 0.06


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Clean a MurmurMark session mic track using local FIR echo models.")
    parser.add_argument("session", type=Path)
    parser.add_argument("--profile", choices=("conservative", "experimental_aggressive"), default="conservative")
    parser.add_argument("--role-policy", choices=("preserve_local", "role_safe", "strict_silence"), default="preserve_local")
    parser.add_argument("--output-clean", type=Path, required=True)
    parser.add_argument("--output-echo", type=Path, required=True)
    parser.add_argument("--output-role-mask", type=Path, default=None)
    parser.add_argument("--output-role-preview", type=Path, default=None)
    parser.add_argument("--asr-segments-dir", type=Path, default=None)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--segments", type=Path, required=True)
    parser.add_argument("--speaker-state", type=Path, default=None)
    parser.add_argument("--sample-rate", type=int, default=16_000)
    parser.add_argument("--window-sec", type=float, default=8.0)
    parser.add_argument("--hop-sec", type=float, default=2.0)
    parser.add_argument("--fit-tail-ms", type=float, default=80.0)
    parser.add_argument("--regularization", type=float, default=1.0e-2)
    parser.add_argument("--highpass-hz", type=float, default=120.0)
    parser.add_argument("--min-delay-ms", type=float, default=-500.0)
    parser.add_argument("--max-delay-ms", type=float, default=2_000.0)
    parser.add_argument("--remote-margin-db", type=float, default=3.0)
    parser.add_argument("--mic-margin-db", type=float, default=3.0)
    parser.add_argument("--min-remote-db", type=float, default=-55.0)
    parser.add_argument("--min-mic-db", type=float, default=-60.0)
    parser.add_argument("--min-confidence", type=float, default=1.25)
    parser.add_argument("--remote-only-gap-db", type=float, default=6.0)
    parser.add_argument("--remote-only-strength", type=float, default=1.0)
    parser.add_argument("--double-talk-strength", type=float, default=0.35)
    parser.add_argument("--double-talk-high-correlation-threshold", type=float, default=0.45)
    parser.add_argument("--double-talk-high-strength", type=float, default=0.70)
    parser.add_argument("--correlation-remote-threshold", type=float, default=0.10)
    parser.add_argument("--correlation-double-talk-threshold", type=float, default=0.22)
    parser.add_argument("--remote-only-leak-floor-margin-db", type=float, default=8.0)
    parser.add_argument("--preserve-local-floor-margin-db", type=float, default=18.0)
    parser.add_argument("--remote-only-residual-target-db", type=float, default=-50.0)
    parser.add_argument("--remote-only-residual-max-attenuation-db", type=float, default=28.0)
    parser.add_argument("--preview-guard-sec", type=float, default=0.35)
    parser.add_argument("--preview-merge-gap-sec", type=float, default=1.0)
    parser.add_argument("--preview-separator-sec", type=float, default=0.25)
    parser.add_argument("--crossfade-ms", type=float, default=50.0)
    parser.add_argument(
        "--metrics-only",
        action="store_true",
        help="Compute the full candidate report without writing derived audio or segment artifacts.",
    )
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


def resample_if_needed(audio: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    if source_rate == target_rate:
        return audio.astype(np.float32)
    divisor = math.gcd(source_rate, target_rate)
    up = target_rate // divisor
    down = source_rate // divisor
    return signal.resample_poly(audio, up, down).astype(np.float32)


def highpass(audio: np.ndarray, sample_rate: int, cutoff_hz: float) -> np.ndarray:
    if cutoff_hz <= 0:
        return audio.astype(np.float32)
    sos = signal.butter(4, cutoff_hz, btype="highpass", fs=sample_rate, output="sos")
    return signal.sosfilt(sos, audio).astype(np.float32)


def limit_sparse_input_overrange(audio: np.ndarray, sample_rate: int) -> tuple[np.ndarray, dict[str, Any]]:
    values = np.asarray(audio, dtype=np.float32)
    peak_before = float(np.max(np.abs(values))) if values.size else 0.0
    indices = np.flatnonzero(np.abs(values) > 1.0)
    sample_count = int(indices.size)
    region_count = int(np.count_nonzero(np.diff(indices) > 1) + 1) if sample_count else 0
    duration_ms = sample_count * 1_000.0 / sample_rate if sample_rate > 0 else 0.0
    fraction = sample_count / values.size if values.size else 0.0
    applied = bool(
        sample_count
        and duration_ms <= MAX_SPARSE_OVERRANGE_MS
        and fraction <= MAX_SPARSE_OVERRANGE_FRACTION
    )
    output = np.clip(values, -INPUT_PEAK_LIMIT, INPUT_PEAK_LIMIT) if applied else values
    return output.astype(np.float32, copy=False), {
        "applied": applied,
        "peak_before": round(peak_before, 6),
        "peak_after": round(float(np.max(np.abs(output))) if output.size else 0.0, 6),
        "sample_count": sample_count,
        "region_count": region_count,
        "duration_ms": round(duration_ms, 3),
        "fraction": round(fraction, 9),
        "first_sec": round(float(indices[0]) / sample_rate, 3) if sample_count and sample_rate > 0 else None,
        "last_sec": round(float(indices[-1] + 1) / sample_rate, 3) if sample_count and sample_rate > 0 else None,
        "limit": INPUT_PEAK_LIMIT,
        "max_sparse_duration_ms": MAX_SPARSE_OVERRANGE_MS,
        "max_sparse_fraction": MAX_SPARSE_OVERRANGE_FRACTION,
    }


def rms_db(audio: np.ndarray) -> float:
    rms = float(np.sqrt(np.mean(np.square(audio), dtype=np.float64) + EPSILON))
    return 20.0 * math.log10(rms + EPSILON)


def power_db(audio: np.ndarray) -> float:
    return 10.0 * math.log10(float(np.mean(np.asarray(audio, dtype=np.float64) ** 2)) + EPSILON)


def safe_percentile(values: list[float], percentile: float, fallback: float) -> float:
    finite = np.array([value for value in values if math.isfinite(value)], dtype=np.float64)
    if finite.size == 0:
        return fallback
    return float(np.percentile(finite, percentile))


def median(values: list[float], default: float = 0.0) -> float:
    finite = np.array([value for value in values if math.isfinite(value)], dtype=np.float64)
    if finite.size == 0:
        return default
    return float(np.median(finite))


def acoustic_mode_from_segments(segment_rows: list[dict[str, Any]]) -> dict[str, Any]:
    remote_only = [row for row in segment_rows if str(row.get("state") or "") == "remote_only"]
    similarities = sorted(float(row.get("remote_similarity_before") or 0.0) for row in remote_only)

    def quantile(value: float) -> float:
        if not similarities:
            return 0.0
        index = min(len(similarities) - 1, max(0, int(math.floor(len(similarities) * value))))
        return similarities[index]

    count = len(similarities)
    coupled = sum(1 for value in similarities if value >= ACOUSTIC_COUPLED_SIMILARITY)
    coupled_ratio = coupled / count if count else 0.0
    p75 = quantile(0.75)
    p90 = quantile(0.90)
    if count < ACOUSTIC_MIN_REMOTE_ONLY_WINDOWS:
        mode = "uncertain"
        confidence = 0.0
        reasons = ["not_enough_remote_only_windows"]
    elif coupled_ratio >= ACOUSTIC_SPEAKER_COUPLED_RATIO_MIN and p75 >= ACOUSTIC_COUPLED_SIMILARITY:
        mode = "speaker_playback"
        confidence = 0.9 if coupled_ratio >= 0.50 and p75 >= 0.08 else 0.75
        reasons = ["remote_only_windows_show_repeatable_remote_to_mic_coupling"]
    elif (
        coupled_ratio <= ACOUSTIC_LOW_LEAK_COUPLED_RATIO_MAX
        and p75 <= ACOUSTIC_LOW_LEAK_P75_MAX
        and p90 <= ACOUSTIC_LOW_LEAK_P90_MAX
    ):
        mode = "headphones_or_low_leak"
        confidence = 0.9 if coupled_ratio <= 0.10 and p75 <= 0.02 else 0.75
        reasons = ["remote_only_windows_show_low_remote_to_mic_coupling"]
    else:
        mode = "uncertain"
        confidence = 0.5
        reasons = ["remote_to_mic_coupling_is_between_calibrated_modes"]
    return {
        "schema": "murmurmark.acoustic_mode_report/v1",
        "mode": mode,
        "confidence": confidence,
        "reasons": reasons,
        "metrics": {
            "remote_only_windows": count,
            "similarity_p50": round(quantile(0.50), 6),
            "similarity_p75": round(p75, 6),
            "similarity_p90": round(p90, 6),
            "coupled_window_count": coupled,
            "coupled_window_ratio": round(coupled_ratio, 6),
        },
        "thresholds": {
            "min_remote_only_windows": ACOUSTIC_MIN_REMOTE_ONLY_WINDOWS,
            "coupled_similarity": ACOUSTIC_COUPLED_SIMILARITY,
            "speaker_coupled_ratio_min": ACOUSTIC_SPEAKER_COUPLED_RATIO_MIN,
            "low_leak_coupled_ratio_max": ACOUSTIC_LOW_LEAK_COUPLED_RATIO_MAX,
            "low_leak_p75_max": ACOUSTIC_LOW_LEAK_P75_MAX,
            "low_leak_p90_max": ACOUSTIC_LOW_LEAK_P90_MAX,
        },
    }


def normalized_corr(left: np.ndarray, right: np.ndarray) -> float:
    a = np.asarray(left, dtype=np.float64) - float(np.mean(left))
    b = np.asarray(right, dtype=np.float64) - float(np.mean(right))
    return float(np.dot(a, b) / (math.sqrt(np.dot(a, a) * np.dot(b, b)) + EPSILON))


def gcc_phat(
    mic: np.ndarray,
    remote: np.ndarray,
    sample_rate: int,
    min_delay_ms: float,
    max_delay_ms: float,
    second_peak_radius_ms: float = 50.0,
) -> dict[str, Any]:
    mic = mic.astype(np.float32, copy=False)
    remote = remote.astype(np.float32, copy=False)
    mic = mic - float(np.mean(mic))
    remote = remote - float(np.mean(remote))
    taper = np.hanning(min(len(mic), len(remote))).astype(np.float32)
    mic = mic[: taper.size] * taper
    remote = remote[: taper.size] * taper

    fft_size = next_fast_len(len(mic) + len(remote) - 1)
    mic_fft = rfft(mic, fft_size)
    remote_fft = rfft(remote, fft_size)
    cross_power = mic_fft * np.conj(remote_fft)
    cross_power /= np.maximum(np.abs(cross_power), EPSILON)
    corr = irfft(cross_power, fft_size)
    corr = np.concatenate((corr[-(len(remote) - 1) :], corr[: len(mic)]))
    lags = np.arange(-len(remote) + 1, len(mic))

    min_lag = int(round(min_delay_ms * sample_rate / 1_000.0))
    max_lag = int(round(max_delay_ms * sample_rate / 1_000.0))
    mask = (lags >= min_lag) & (lags <= max_lag)
    limited_corr = corr[mask]
    limited_lags = lags[mask]
    if limited_corr.size == 0:
        return {"delay_ms": None, "confidence": 0.0, "peak": 0.0}

    abs_corr = np.abs(limited_corr)
    peak_index = int(np.argmax(abs_corr))
    peak_lag = int(limited_lags[peak_index])
    peak = float(abs_corr[peak_index])
    radius = int(round(second_peak_radius_ms * sample_rate / 1_000.0))
    second_mask = np.abs(limited_lags - peak_lag) > radius
    second = float(np.max(abs_corr[second_mask])) if np.any(second_mask) else 0.0
    return {
        "delay_ms": peak_lag * 1_000.0 / sample_rate,
        "confidence": peak / (second + EPSILON),
        "peak": peak,
    }


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


def fit_local_fir(remote_fit: np.ndarray, mic_fit: np.ndarray, taps: int, regularization: float) -> np.ndarray:
    x = remote_fit.astype(np.float64) - float(np.mean(remote_fit))
    y = mic_fit.astype(np.float64) - float(np.mean(mic_fit))
    corr_xx = signal.correlate(x, x, mode="full", method="fft")
    corr_yx = signal.correlate(y, x, mode="full", method="fft")
    center = x.size - 1
    r_xx = corr_xx[center : center + taps]
    p_yx = corr_yx[center : center + taps]
    if r_xx.size < taps or p_yx.size < taps or float(r_xx[0]) <= EPSILON:
        return np.zeros(taps, dtype=np.float64)
    toeplitz_col = r_xx.copy()
    toeplitz_col[0] += max(float(r_xx[0]) * regularization, EPSILON)
    try:
        fir = linalg.solve_toeplitz((toeplitz_col, toeplitz_col), p_yx, check_finite=False)
    except Exception:
        return np.zeros(taps, dtype=np.float64)
    return np.asarray(fir, dtype=np.float64)


def frame_vad_ratio(raw: np.ndarray, clean: np.ndarray, sample_rate: int, threshold_db: float = -50.0) -> float:
    frame = max(1, int(round(sample_rate * 0.02)))
    raw_count = 0
    clean_count = 0
    for start in range(0, min(raw.size, clean.size), frame):
        raw_frame = raw[start : start + frame]
        clean_frame = clean[start : start + frame]
        if raw_frame.size == 0 or clean_frame.size == 0:
            continue
        if rms_db(raw_frame) >= threshold_db:
            raw_count += 1
        if rms_db(clean_frame) >= threshold_db:
            clean_count += 1
    if raw_count == 0:
        return 1.0
    return clean_count / raw_count


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def default_paths(session: Path) -> tuple[Path, Path]:
    audio_dir = session / "derived" / "preprocess" / "audio"
    return audio_dir / "remote_for_aec.wav", audio_dir / "mic_raw_for_asr.wav"


def default_role_mask_path(session: Path) -> Path:
    return session / "derived" / "preprocess" / "audio" / "mic_role_masked_for_asr.wav"


def default_role_preview_path(session: Path) -> Path:
    return session / "derived" / "preprocess" / "audio" / "mic_role_preview.wav"


def default_asr_segments_dir(session: Path) -> Path:
    return session / "derived" / "preprocess" / "mic_asr_segments"


def default_speaker_state_path(session: Path) -> Path:
    return session / "derived" / "preprocess" / "echo" / "speaker_state.jsonl"


def classify_windows(
    remote: np.ndarray,
    mic: np.ndarray,
    args: argparse.Namespace,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    sample_rate = args.sample_rate
    window = int(round(args.window_sec * sample_rate))
    hop = int(round(args.hop_sec * sample_rate))
    if remote.size < window:
        window = remote.size
    if window <= 0:
        raise ValueError("audio has no samples")

    starts = list(range(0, max(1, remote.size - window + 1), hop))
    if starts[-1] + window < remote.size:
        starts.append(remote.size - window)

    remote_levels: list[float] = []
    mic_levels: list[float] = []
    for start in starts:
        end = min(remote.size, start + window)
        remote_levels.append(rms_db(remote[start:end]))
        mic_levels.append(rms_db(mic[start:end]))

    remote_floor = safe_percentile(remote_levels, 20.0, -80.0)
    mic_floor = safe_percentile(mic_levels, 20.0, -80.0)
    remote_threshold = max(remote_floor + args.remote_margin_db, args.min_remote_db)
    mic_threshold = max(mic_floor + args.mic_margin_db, args.min_mic_db)

    rows: list[dict[str, Any]] = []
    reliable_delays: list[float] = []
    for index, start in enumerate(starts):
        end = min(remote.size, start + window)
        remote_db = remote_levels[index]
        mic_db = mic_levels[index]
        remote_active = remote_db >= remote_threshold
        mic_active = mic_db >= mic_threshold
        estimate = {"delay_ms": None, "confidence": 0.0, "peak": 0.0}
        reliable = False
        if remote_active and mic_active:
            estimate = gcc_phat(
                mic[start:end],
                remote[start:end],
                sample_rate,
                args.min_delay_ms,
                args.max_delay_ms,
            )
            reliable = bool(estimate["delay_ms"] is not None and estimate["confidence"] >= args.min_confidence)
            if reliable:
                reliable_delays.append(float(estimate["delay_ms"]))

        if remote_active and mic_active and reliable and mic_db <= remote_db - args.remote_only_gap_db:
            state = "remote_only"
        elif mic_active and not remote_active:
            state = "local_only"
        elif remote_active and mic_active:
            state = "double_talk"
        else:
            state = "silence"

        rows.append(
            {
                "index": index,
                "start_sec": round(start / sample_rate, 3),
                "end_sec": round(end / sample_rate, 3),
                "remote_db": round(remote_db, 3),
                "mic_db": round(mic_db, 3),
                "remote_active": remote_active,
                "mic_active": mic_active,
                "state": state,
                "delay_ms": None if estimate["delay_ms"] is None else round(float(estimate["delay_ms"]), 3),
                "confidence": round(float(estimate["confidence"]), 4),
                "reliable": reliable,
            }
        )

    summary = {
        "windows_total": len(rows),
        "reliable_delay_windows": len(reliable_delays),
        "median_delay_ms": None if not reliable_delays else round(median(reliable_delays), 3),
        "delay_p10_ms": None if not reliable_delays else round(float(np.percentile(reliable_delays, 10)), 3),
        "delay_p90_ms": None if not reliable_delays else round(float(np.percentile(reliable_delays, 90)), 3),
        "remote_floor_db": round(remote_floor, 3),
        "mic_floor_db": round(mic_floor, 3),
        "remote_threshold_db": round(remote_threshold, 3),
        "mic_threshold_db": round(mic_threshold, 3),
    }
    return rows, summary


def nearest_remote_only(window: dict[str, Any], remote_only: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not remote_only:
        return None
    center = (float(window["start_sec"]) + float(window["end_sec"])) / 2.0
    return min(
        remote_only,
        key=lambda candidate: abs(((float(candidate["start_sec"]) + float(candidate["end_sec"])) / 2.0) - center),
    )


def main() -> int:
    args = parse_args()
    remote_path, mic_path = default_paths(args.session)
    if not remote_path.exists() or not mic_path.exists():
        raise SystemExit("working audio not found; run preprocess --echo diagnostic first")

    remote_rate, remote = read_wav_float(remote_path)
    mic_rate, mic = read_wav_float(mic_path)
    remote = resample_if_needed(remote, remote_rate, args.sample_rate)
    mic = resample_if_needed(mic, mic_rate, args.sample_rate)
    remote = highpass(remote, args.sample_rate, args.highpass_hz)
    mic = highpass(mic, args.sample_rate, args.highpass_hz)
    remote, remote_overrange = limit_sparse_input_overrange(remote, args.sample_rate)
    mic, mic_overrange = limit_sparse_input_overrange(mic, args.sample_rate)
    count = min(remote.size, mic.size)
    remote = remote[:count]
    mic = mic[:count]

    windows, delay_summary = classify_windows(remote, mic, args)
    delay_ms = delay_summary["median_delay_ms"]
    if delay_ms is None:
        delay_ms = 0.0
    delay_samples = int(round(float(delay_ms) * args.sample_rate / 1_000.0))
    remote_aligned = shift_reference(remote, delay_samples)

    remote_only = [window for window in windows if window["state"] == "remote_only"]
    taps = int(round(args.fit_tail_ms * args.sample_rate / 1_000.0))
    hop_samples = int(round(args.hop_sec * args.sample_rate))
    clean = mic.astype(np.float64).copy()
    role_masked = np.zeros_like(clean)
    removed_echo = np.zeros_like(clean)
    fir_cache: dict[int, np.ndarray] = {}

    segment_rows: list[dict[str, Any]] = []
    speaker_state_rows: list[dict[str, Any]] = []
    pass_intervals: list[dict[str, Any]] = []
    remote_reductions: list[float] = []
    remote_similarity_before: list[float] = []
    remote_similarity_after: list[float] = []
    local_energy_deltas: list[float] = []
    local_raw_parts: list[np.ndarray] = []
    local_clean_parts: list[np.ndarray] = []
    processed = 0
    rejected = 0
    role_masked_remote_only_sec = 0.0
    role_masked_silence_sec = 0.0
    role_masked_local_only_sec = 0.0
    role_masked_double_talk_sec = 0.0
    role_masked_uncertain_sec = 0.0

    for index, start in enumerate(range(0, count, hop_samples)):
        end = min(count, start + hop_samples)
        if end <= start:
            continue
        chunk_center_sec = ((start + end) / 2.0) / args.sample_rate
        window = min(windows, key=lambda row: abs(((float(row["start_sec"]) + float(row["end_sec"])) / 2.0) - chunk_center_sec))
        state = window["state"]
        chunk_remote_db = rms_db(remote[start:end])
        chunk_mic_db = rms_db(mic[start:end])
        chunk_remote_active = chunk_remote_db >= float(delay_summary["remote_threshold_db"])
        chunk_mic_active = chunk_mic_db >= float(delay_summary["mic_threshold_db"])
        mic_floor_margin = (
            args.preserve_local_floor_margin_db
            if args.role_policy == "preserve_local"
            else args.remote_only_leak_floor_margin_db
        )
        chunk_mic_leak_active = chunk_mic_db >= float(delay_summary["mic_threshold_db"]) - mic_floor_margin
        if not chunk_remote_active:
            state = "local_only" if chunk_mic_leak_active else "silence"
        elif not chunk_mic_leak_active:
            state = "silence"
        before_corr = abs(normalized_corr(remote_aligned[start:end], mic[start:end]))
        strength = 0.0
        strength_reason = "passthrough"
        if state == "remote_only":
            strength = args.remote_only_strength
            strength_reason = "remote_only"
        elif state == "double_talk":
            strength = args.double_talk_strength
            strength_reason = "double_talk"
            if before_corr >= args.double_talk_high_correlation_threshold:
                strength = max(strength, args.double_talk_high_strength)
                strength_reason = "double_talk_high_correlation"

        fit_window = nearest_remote_only(window, remote_only)
        fir = np.zeros(taps, dtype=np.float64)
        fit_index = None
        if fit_window is not None:
            if state == "silence":
                if (
                    chunk_remote_active
                    and chunk_mic_leak_active
                    and chunk_mic_db <= chunk_remote_db - args.remote_only_gap_db
                ):
                    state = "remote_only_level"
                    strength = args.remote_only_strength
                    strength_reason = "remote_level"
                elif before_corr >= args.correlation_remote_threshold:
                    state = "remote_only_correlation"
                    strength = args.remote_only_strength
                    strength_reason = "remote_correlation"
            elif state == "local_only" and before_corr >= args.correlation_double_talk_threshold:
                state = "double_talk_correlation"
                strength = args.double_talk_strength
                strength_reason = "local_only_remote_correlation"
                if before_corr >= args.double_talk_high_correlation_threshold:
                    strength = max(strength, args.double_talk_high_strength)
                    strength_reason = "local_only_high_remote_correlation"

        if strength > 0 and fit_window is not None:
            if state in {"remote_only", "remote_only_correlation", "remote_only_level"}:
                fit_index = -(index + 1)
                if fit_index not in fir_cache:
                    fir_cache[fit_index] = fit_local_fir(
                        remote_aligned[start:end],
                        mic[start:end],
                        taps,
                        args.regularization,
                    )
            else:
                fit_index = int(fit_window["index"])
            if fit_index not in fir_cache:
                fit_start = int(round(float(fit_window["start_sec"]) * args.sample_rate))
                fit_end = int(round(float(fit_window["end_sec"]) * args.sample_rate))
                fir_cache[fit_index] = fit_local_fir(
                    remote_aligned[fit_start:fit_end],
                    mic[fit_start:fit_end],
                    taps,
                    args.regularization,
                )
            fir = fir_cache[fit_index]

        before_power = float(np.mean(mic[start:end].astype(np.float64) ** 2) + EPSILON)

        def subtract_with_fir(candidate_fir: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
            context_start = max(0, start - taps + 1)
            remote_context = remote_aligned[context_start:end].astype(np.float64)
            echo_context = signal.lfilter(candidate_fir, [1.0], remote_context)
            chunk_offset = start - context_start
            echo_chunk = echo_context[chunk_offset : chunk_offset + (end - start)]
            removed = strength * echo_chunk
            return removed, mic[start:end].astype(np.float64) - removed

        removed_chunk, clean_chunk = subtract_with_fir(fir)
        if fit_index is not None and fit_index < 0 and fit_window is not None:
            tentative_after_power = float(np.mean(clean_chunk.astype(np.float64) ** 2) + EPSILON)
            tentative_after_corr = abs(normalized_corr(remote_aligned[start:end], clean_chunk))
            tentative_peak = float(np.max(np.abs(clean_chunk))) if clean_chunk.size else 0.0
            unstable_current_fit = (
                tentative_after_power > before_power * 1.2
                or tentative_after_corr > max(before_corr * 0.98, 0.12)
                or tentative_peak > 0.99
            )
            if unstable_current_fit:
                fallback_index = int(fit_window["index"])
                if fallback_index not in fir_cache:
                    fit_start = int(round(float(fit_window["start_sec"]) * args.sample_rate))
                    fit_end = int(round(float(fit_window["end_sec"]) * args.sample_rate))
                    fir_cache[fallback_index] = fit_local_fir(
                        remote_aligned[fit_start:fit_end],
                        mic[fit_start:fit_end],
                        taps,
                        args.regularization,
                    )
                fit_index = fallback_index
                fir = fir_cache[fit_index]
                removed_chunk, clean_chunk = subtract_with_fir(fir)
                strength_reason += "_fallback_window_fit"

        residual_gate_db = 0.0
        if args.role_policy != "preserve_local" and state == "remote_only":
            clean_rms = math.sqrt(float(np.mean(clean_chunk.astype(np.float64) ** 2)) + EPSILON)
            target_rms = 10.0 ** (args.remote_only_residual_target_db / 20.0)
            if clean_rms > target_rms:
                gate = max(
                    target_rms / clean_rms,
                    10.0 ** (-args.remote_only_residual_max_attenuation_db / 20.0),
                )
                clean_chunk *= gate
                removed_chunk = mic[start:end].astype(np.float64) - clean_chunk
                residual_gate_db = 20.0 * math.log10(gate + EPSILON)

        clean[start:end] = clean_chunk
        removed_echo[start:end] = removed_chunk

        duration_sec = (end - start) / args.sample_rate
        role_action = "pass_raw_uncertain"
        role_confidence = 0.5
        if args.role_policy == "strict_silence" and state in {
            "remote_only",
            "remote_only_correlation",
            "remote_only_level",
            "double_talk",
            "double_talk_correlation",
        }:
            role_chunk = np.zeros(end - start, dtype=np.float64)
            role_action = "mute_mic_remote_active_strict"
            role_confidence = 0.75
            role_masked_remote_only_sec += duration_sec
        elif args.role_policy == "role_safe" and state == "remote_only":
            role_chunk = np.zeros(end - start, dtype=np.float64)
            role_action = "mute_mic_remote_only"
            role_confidence = 0.95
            role_masked_remote_only_sec += duration_sec
        elif state in {"remote_only", "remote_only_correlation", "remote_only_level"}:
            role_chunk = clean_chunk
            role_action = "pass_mild_fir_flag_remote_active"
            role_confidence = 0.70 if state == "remote_only" else 0.60
            role_masked_uncertain_sec += duration_sec
        elif state == "local_only":
            role_chunk = mic[start:end].astype(np.float64)
            role_action = "pass_raw_local_only"
            role_confidence = 0.90
            role_masked_local_only_sec += duration_sec
        elif state in {"double_talk", "double_talk_correlation"}:
            role_chunk = clean_chunk
            role_action = "pass_mild_fir_flag_overlap"
            role_confidence = 0.65
            role_masked_double_talk_sec += duration_sec
        elif state == "silence":
            role_chunk = np.zeros(end - start, dtype=np.float64)
            role_action = "mute_silence"
            role_confidence = 0.80
            role_masked_silence_sec += duration_sec
        else:
            role_chunk = mic[start:end].astype(np.float64)
        role_masked[start:end] = role_chunk
        if role_action.startswith("pass_"):
            pass_intervals.append(
                {
                    "start_sample": start,
                    "end_sample": end,
                    "state": state,
                    "action": role_action,
                    "confidence": role_confidence,
                }
            )

        after_power = float(np.mean(clean[start:end].astype(np.float64) ** 2) + EPSILON)
        reduction = 10.0 * math.log10(before_power / after_power)
        after_corr = abs(normalized_corr(remote_aligned[start:end], clean[start:end]))

        if state in {"remote_only", "remote_only_correlation", "remote_only_level"}:
            remote_reductions.append(reduction)
            remote_similarity_before.append(before_corr)
            remote_similarity_after.append(after_corr)
        elif state == "local_only":
            local_energy_deltas.append(10.0 * math.log10(after_power / before_power))
            local_raw_parts.append(mic[start:end])
            local_clean_parts.append(clean[start:end].astype(np.float32))

        if strength > 0:
            processed += 1
        else:
            rejected += 1

        segment_rows.append(
            {
                "index": index,
                "start_sec": round(start / args.sample_rate, 3),
                "end_sec": round(end / args.sample_rate, 3),
                "state": state,
                "strength": round(strength, 3),
                "strength_reason": strength_reason,
                "remote_db": round(chunk_remote_db, 3),
                "mic_db": round(chunk_mic_db, 3),
                "residual_gate_db": round(residual_gate_db, 3),
                "fit_window_index": fit_index,
                "reduction_db": round(reduction, 3),
                "remote_similarity_before": round(before_corr, 5),
                "remote_similarity_after": round(after_corr, 5),
                "role_action": role_action,
                "role_confidence": round(role_confidence, 3),
            }
        )
        speaker_state_rows.append(
            {
                "start": round(start / args.sample_rate, 3),
                "end": round(end / args.sample_rate, 3),
                "state": state,
                "action": role_action,
                "confidence": round(role_confidence, 3),
                "delay_ms": round(float(delay_ms), 3),
                "remote_db": round(chunk_remote_db, 3),
                "mic_db": round(chunk_mic_db, 3),
                "strength": round(strength, 3),
                "residual_gate_db": round(residual_gate_db, 3),
            }
        )

    max_abs = float(np.max(np.abs(clean))) if clean.size else 0.0
    peak_scale = 1.0
    if max_abs > 0.999:
        peak_scale = 0.999 / max_abs
        clean *= peak_scale
        removed_echo = mic.astype(np.float64) - clean
        max_abs = float(np.max(np.abs(clean))) if clean.size else 0.0
    max_abs_role_masked = float(np.max(np.abs(role_masked))) if role_masked.size else 0.0
    finite = bool(np.isfinite(clean).all())
    local_raw = np.concatenate(local_raw_parts) if local_raw_parts else np.array([], dtype=np.float32)
    local_clean = np.concatenate(local_clean_parts) if local_clean_parts else np.array([], dtype=np.float32)
    local_vad_ratio = frame_vad_ratio(local_raw, local_clean, args.sample_rate) if local_raw.size else 1.0
    local_loss_ratio = 0.0
    if local_raw.size:
        raw_rms = math.sqrt(float(np.mean(local_raw.astype(np.float64) ** 2)) + EPSILON)
        clean_rms = math.sqrt(float(np.mean(local_clean.astype(np.float64) ** 2)) + EPSILON)
        local_loss_ratio = max(0.0, 1.0 - clean_rms / max(raw_rms, EPSILON))

    remote_reduction_median = median(remote_reductions, 0.0)
    local_energy_delta_median = median(local_energy_deltas, 0.0)
    sim_before = median(remote_similarity_before, 0.0)
    sim_after = median(remote_similarity_after, 0.0)

    warnings: list[dict[str, Any]] = []
    if not local_energy_deltas:
        warnings.append({"type": "local_only_windows_missing", "start": 0.0, "end": 0.0})
    for source, conditioning in (("mic", mic_overrange), ("remote", remote_overrange)):
        if conditioning["applied"]:
            warning_start = conditioning["first_sec"] if conditioning["region_count"] == 1 else 0.0
            warning_end = conditioning["last_sec"] if conditioning["region_count"] == 1 else 0.0
            warnings.append(
                {
                    "type": "sparse_input_overrange_limited",
                    "source": source,
                    "start": warning_start,
                    "end": warning_end,
                    "sample_count": conditioning["sample_count"],
                    "region_count": conditioning["region_count"],
                    "duration_ms": conditioning["duration_ms"],
                    "peak_before": conditioning["peak_before"],
                }
            )

    conservative_failures: list[str] = []
    if delay_summary["reliable_delay_windows"] < 10:
        conservative_failures.append("not_enough_reliable_delay_windows")
    if len(remote_only) < 5:
        conservative_failures.append("not_enough_remote_only_windows")
    if remote_reduction_median < 3.0:
        conservative_failures.append("remote_only_reduction_too_low")
    if local_energy_delta_median < -1.5 or local_energy_delta_median > 1.0:
        conservative_failures.append("local_only_energy_delta_out_of_range")
    if local_vad_ratio < 0.90 or local_vad_ratio > 1.10:
        conservative_failures.append("local_only_vad_duration_changed")
    if not finite:
        conservative_failures.append("clean_contains_nan_or_inf")
    if max_abs > 1.0:
        conservative_failures.append("clean_would_clip")
    if peak_scale < 0.95:
        conservative_failures.append("clean_requires_large_peak_scaling")

    if args.profile == "experimental_aggressive":
        accepted = finite and max_abs <= 1.25
        reason = None if accepted else "integrity_check_failed"
        warnings.append({"type": "experimental_forced_clean_candidate", "start": 0.0, "end": count / args.sample_rate})
    else:
        accepted = not conservative_failures
        reason = None if accepted else conservative_failures[0]

    role_mask_path = args.output_role_mask or default_role_mask_path(args.session)
    role_preview_path = args.output_role_preview or default_role_preview_path(args.session)
    asr_segments_dir = args.asr_segments_dir or default_asr_segments_dir(args.session)
    speaker_state_path = args.speaker_state or default_speaker_state_path(args.session)
    if not args.metrics_only:
        write_wav_float(args.output_clean, args.sample_rate, clean)
        write_wav_float(args.output_echo, args.sample_rate, removed_echo)
        write_wav_float(role_mask_path, args.sample_rate, role_masked)

    merge_gap_samples = max(0, int(round(args.preview_merge_gap_sec * args.sample_rate)))
    guard_samples = max(0, int(round(args.preview_guard_sec * args.sample_rate)))
    merged_intervals: list[dict[str, Any]] = []
    for interval in pass_intervals:
        start_sample = max(0, int(interval["start_sample"]) - guard_samples)
        end_sample = min(count, int(interval["end_sample"]) + guard_samples)
        if not merged_intervals or start_sample > int(merged_intervals[-1]["end_sample"]) + merge_gap_samples:
            merged_intervals.append(
                {
                    "start_sample": start_sample,
                    "end_sample": end_sample,
                    "states": {str(interval["state"])},
                    "actions": {str(interval["action"])},
                    "confidence": float(interval["confidence"]),
                    "source_chunks": 1,
                }
            )
        else:
            current = merged_intervals[-1]
            current["end_sample"] = max(int(current["end_sample"]), end_sample)
            current["states"].add(str(interval["state"]))
            current["actions"].add(str(interval["action"]))
            current["confidence"] = min(float(current["confidence"]), float(interval["confidence"]))
            current["source_chunks"] = int(current["source_chunks"]) + 1

    if not args.metrics_only:
        if asr_segments_dir.exists():
            for old_chunk in asr_segments_dir.glob("mic_*.wav"):
                old_chunk.unlink()
        asr_segments_dir.mkdir(parents=True, exist_ok=True)
    asr_segment_rows: list[dict[str, Any]] = []
    role_preview_parts: list[np.ndarray] = []
    role_preview_samples = 0
    separator = np.zeros(max(0, int(round(args.preview_separator_sec * args.sample_rate))), dtype=np.float64)
    for index, interval in enumerate(merged_intervals, start=1):
        chunk_name = f"mic_{index:06d}.wav"
        start_sample = int(interval["start_sample"])
        end_sample = int(interval["end_sample"])
        chunk_audio = role_masked[start_sample:end_sample]
        states = sorted(interval["states"])
        actions = sorted(interval["actions"])
        row = {
            "path": chunk_name,
            "start_session_sec": round(start_sample / args.sample_rate, 3),
            "end_session_sec": round(end_sample / args.sample_rate, 3),
            "state": states[0] if len(states) == 1 else "mixed",
            "states": states,
            "action": actions[0] if len(actions) == 1 else "mixed_pass",
            "actions": actions,
            "confidence": round(float(interval["confidence"]), 3),
            "source_chunks": int(interval["source_chunks"]),
        }
        asr_segment_rows.append(row)
        role_preview_samples += chunk_audio.size + separator.size
        if not args.metrics_only:
            write_wav_float(asr_segments_dir / chunk_name, args.sample_rate, chunk_audio)
            role_preview_parts.append(chunk_audio.astype(np.float64))
            role_preview_parts.append(separator)
    if not args.metrics_only:
        preview = np.concatenate(role_preview_parts) if role_preview_parts else np.zeros(1, dtype=np.float64)
        write_wav_float(role_preview_path, args.sample_rate, preview)
        write_json(
            asr_segments_dir / "segments_manifest.json",
            {
                "schema": "murmurmark.mic_asr_segments/v1",
                "source": str(role_mask_path),
                "policy": args.role_policy,
                "sample_rate": args.sample_rate,
                "segments": asr_segment_rows,
            },
        )
        write_jsonl(args.segments, segment_rows)
        write_jsonl(speaker_state_path, speaker_state_rows)

    report = {
        "schema": "murmurmark.local_fir_report/v1",
        "session": str(args.session),
        "profile": args.profile,
        "role_policy": args.role_policy,
        "parameters": {
            "sample_rate": args.sample_rate,
            "role_policy": args.role_policy,
            "window_sec": args.window_sec,
            "hop_sec": args.hop_sec,
            "fit_tail_ms": args.fit_tail_ms,
            "regularization": args.regularization,
            "highpass_hz": args.highpass_hz,
            "delay_ms": round(float(delay_ms), 3),
            "delay_samples": delay_samples,
            "remote_only_strength": args.remote_only_strength,
            "double_talk_strength": args.double_talk_strength,
            "double_talk_high_correlation_threshold": args.double_talk_high_correlation_threshold,
            "double_talk_high_strength": args.double_talk_high_strength,
            "correlation_remote_threshold": args.correlation_remote_threshold,
            "correlation_double_talk_threshold": args.correlation_double_talk_threshold,
            "remote_only_leak_floor_margin_db": args.remote_only_leak_floor_margin_db,
            "preserve_local_floor_margin_db": args.preserve_local_floor_margin_db,
            "remote_only_residual_target_db": args.remote_only_residual_target_db,
            "remote_only_residual_max_attenuation_db": args.remote_only_residual_max_attenuation_db,
            "requested_crossfade_ms": args.crossfade_ms,
            "effective_crossfade_ms": 0.0,
        },
        "input_conditioning": {
            "mic": mic_overrange,
            "remote": remote_overrange,
        },
        "acoustic_mode": acoustic_mode_from_segments(segment_rows),
        "summary": {
            **delay_summary,
            "remote_only_windows": len(remote_only),
            "remote_only_correlation_segments": sum(1 for row in segment_rows if row["state"] == "remote_only_correlation"),
            "remote_only_level_segments": sum(1 for row in segment_rows if row["state"] == "remote_only_level"),
            "double_talk_correlation_segments": sum(1 for row in segment_rows if row["state"] == "double_talk_correlation"),
            "remote_only_residual_gated_segments": sum(1 for row in segment_rows if row["residual_gate_db"] < 0),
            "role_masked_remote_only_sec": round(role_masked_remote_only_sec, 3),
            "role_masked_silence_sec": round(role_masked_silence_sec, 3),
            "role_masked_local_only_sec": round(role_masked_local_only_sec, 3),
            "role_masked_double_talk_sec": round(role_masked_double_talk_sec, 3),
            "role_masked_uncertain_sec": round(role_masked_uncertain_sec, 3),
            "mic_asr_segments": len(asr_segment_rows),
            "role_preview_sec": round(role_preview_samples / args.sample_rate, 3),
            "local_only_windows": sum(1 for row in windows if row["state"] == "local_only"),
            "double_talk_windows": sum(1 for row in windows if row["state"] == "double_talk"),
            "segments_processed": processed,
            "segments_rejected": rejected,
        },
        "metrics": {
            "remote_similarity_before": round(sim_before, 5),
            "remote_similarity_after": round(sim_after, 5),
            "estimated_echo_reduction_db": round(remote_reduction_median, 3),
            "near_end_speech_loss_ratio": round(local_loss_ratio, 5),
            "segments_processed": processed,
            "segments_rejected": rejected,
            "remote_only_median_reduction_db": round(remote_reduction_median, 3),
            "local_only_energy_delta_db_median": round(local_energy_delta_median, 3),
            "local_only_vad_duration_ratio": round(local_vad_ratio, 5),
            "max_abs_clean": round(max_abs, 6),
            "max_abs_role_masked": round(max_abs_role_masked, 6),
            "peak_scale": round(peak_scale, 6),
            "finite": finite,
        },
        "decision": {
            "accepted_for_asr": accepted,
            "reason": reason,
            "quality_gate_failures": conservative_failures,
        },
        "outputs": {
            "persisted": not args.metrics_only,
            "clean_mic": str(args.output_clean),
            "role_masked_mic": str(role_mask_path),
            "role_preview": str(role_preview_path),
            "mic_asr_segments_manifest": str(asr_segments_dir / "segments_manifest.json"),
            "echo_hat": str(args.output_echo),
            "segments": str(args.segments),
            "speaker_state": str(speaker_state_path),
        },
        "warnings": warnings,
    }
    write_json(args.report, report)

    print(f"local_fir_report: {args.report}")
    print(f"accepted_for_asr: {str(accepted).lower()}")
    print(f"reason: {reason or 'accepted'}")
    print(f"remote_only_median_reduction_db: {remote_reduction_median:.3f}")
    print(f"local_only_energy_delta_db_median: {local_energy_delta_median:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
