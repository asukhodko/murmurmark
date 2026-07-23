#!/usr/bin/env python3
"""Canonical remote-to-microphone timeline helpers for Echo Guard.

The signed-delay convention is:

    echo_at_mic(t) ~= remote(t - delay(t))

A positive delay means that playback appears later in the microphone track.
A negative delay means that the recorded remote timeline leads the microphone
timeline and the reference must be advanced.
"""

from __future__ import annotations

import math
from typing import Any, Iterable

import numpy as np
from scipy import signal
from scipy.fft import irfft, next_fast_len, rfft


TIMELINE_SCHEMA = "murmurmark.echo.timeline_contract/v1"
SIGN_CONVENTION = "echo_at_mic(t) ~= remote(t - delay(t))"


def delay_ms_to_samples(delay_ms: float, sample_rate: int) -> int:
    if sample_rate <= 0:
        raise ValueError("sample_rate must be positive")
    if not math.isfinite(delay_ms):
        raise ValueError("delay_ms must be finite")
    return int(round(delay_ms * sample_rate / 1_000.0))


def align_remote_constant(remote: np.ndarray, sample_rate: int, delay_ms: float) -> np.ndarray:
    """Place a remote reference on the microphone timeline."""
    source = np.asarray(remote)
    aligned = np.zeros_like(source)
    delay_samples = delay_ms_to_samples(delay_ms, sample_rate)
    if delay_samples > 0:
        if delay_samples < source.size:
            aligned[delay_samples:] = source[:-delay_samples]
    elif delay_samples < 0:
        lead = -delay_samples
        if lead < source.size:
            aligned[:-lead] = source[lead:]
    else:
        aligned[:] = source
    return aligned


def align_remote_curve(
    remote: np.ndarray,
    sample_rate: int,
    rows: Iterable[dict[str, Any]],
    *,
    delay_key: str = "smoothed_delay_ms",
) -> np.ndarray:
    """Place a remote reference on the microphone timeline using bounded rows.

    Rows must contain ``start_sec`` and ``end_sec``. Missing delay values fall
    back to ``delay_ms`` and then zero. Overlapping rows are blended by taking
    the latest row, matching the existing hop-based Echo Guard contract.
    """
    source = np.asarray(remote)
    aligned = np.zeros_like(source)
    covered = np.zeros(source.size, dtype=bool)
    for row in rows:
        start = max(0, int(round(float(row.get("start_sec", 0.0)) * sample_rate)))
        end = min(source.size, int(round(float(row.get("end_sec", 0.0)) * sample_rate)))
        if end <= start:
            continue
        delay_value = row.get(delay_key)
        if delay_value is None:
            delay_value = row.get("delay_ms", 0.0)
        delay_samples = delay_ms_to_samples(float(delay_value or 0.0), sample_rate)
        source_start = start - delay_samples
        source_end = end - delay_samples
        destination_start = start
        destination_end = end
        if source_start < 0:
            destination_start -= source_start
            source_start = 0
        if source_end > source.size:
            destination_end -= source_end - source.size
            source_end = source.size
        if destination_end <= destination_start or source_end <= source_start:
            continue
        count = min(destination_end - destination_start, source_end - source_start)
        aligned[destination_start : destination_start + count] = source[source_start : source_start + count]
        covered[destination_start : destination_start + count] = True

    if not np.any(covered):
        return source.copy()

    # Delay estimators may omit a short final window. Preserve a deterministic
    # zero-delay reference there instead of manufacturing silence.
    aligned[~covered] = source[~covered]
    return aligned


def gcc_phat_signed(
    mic: np.ndarray,
    remote: np.ndarray,
    sample_rate: int,
    min_delay_ms: float,
    max_delay_ms: float,
    *,
    second_peak_radius_ms: float = 50.0,
) -> dict[str, Any]:
    """Estimate signed delay with the canonical mic-vs-remote convention."""
    mic_array = np.asarray(mic, dtype=np.float32)
    remote_array = np.asarray(remote, dtype=np.float32)
    count = min(mic_array.size, remote_array.size)
    if count < 4:
        return {"delay_ms": None, "confidence": 0.0, "peak": 0.0}
    mic_array = mic_array[:count] - float(np.mean(mic_array[:count]))
    remote_array = remote_array[:count] - float(np.mean(remote_array[:count]))
    taper = np.hanning(count).astype(np.float32)
    mic_array *= taper
    remote_array *= taper

    fft_size = next_fast_len(mic_array.size + remote_array.size - 1)
    mic_fft = rfft(mic_array, fft_size)
    remote_fft = rfft(remote_array, fft_size)
    cross_power = mic_fft * np.conj(remote_fft)
    cross_power /= np.maximum(np.abs(cross_power), 1.0e-12)
    correlation = irfft(cross_power, fft_size)
    correlation = np.concatenate(
        (correlation[-(remote_array.size - 1) :], correlation[: mic_array.size])
    )
    lags = np.arange(-remote_array.size + 1, mic_array.size)
    minimum = delay_ms_to_samples(min_delay_ms, sample_rate)
    maximum = delay_ms_to_samples(max_delay_ms, sample_rate)
    mask = (lags >= minimum) & (lags <= maximum)
    limited = correlation[mask]
    limited_lags = lags[mask]
    if limited.size == 0:
        return {"delay_ms": None, "confidence": 0.0, "peak": 0.0}

    absolute = np.abs(limited)
    peak_index = int(np.argmax(absolute))
    peak_lag = int(limited_lags[peak_index])
    peak = float(absolute[peak_index])
    radius = abs(delay_ms_to_samples(second_peak_radius_ms, sample_rate))
    second_mask = np.abs(limited_lags - peak_lag) > radius
    second = float(np.max(absolute[second_mask])) if np.any(second_mask) else 0.0
    return {
        "delay_ms": peak_lag * 1_000.0 / sample_rate,
        "confidence": peak / (second + 1.0e-12),
        "peak": peak,
    }


def estimate_delay_rows(
    remote: np.ndarray,
    mic: np.ndarray,
    sample_rate: int,
    *,
    window_sec: float = 2.0,
    hop_sec: float = 1.0,
    min_delay_ms: float = -500.0,
    max_delay_ms: float = 2_000.0,
    min_confidence: float = 1.15,
) -> list[dict[str, Any]]:
    """Estimate a bounded, smoothed signed-delay curve."""
    remote_array = np.asarray(remote)
    mic_array = np.asarray(mic)
    count = min(remote_array.size, mic_array.size)
    window = max(4, int(round(window_sec * sample_rate)))
    hop = max(1, int(round(hop_sec * sample_rate)))
    starts = list(range(0, max(1, count - window + 1), hop))
    if count and (not starts or starts[-1] + window < count):
        starts.append(max(0, count - window))

    rows: list[dict[str, Any]] = []
    reliable: list[float] = []
    for index, start in enumerate(starts):
        end = min(count, start + window)
        estimate = gcc_phat_signed(
            mic_array[start:end],
            remote_array[start:end],
            sample_rate,
            min_delay_ms,
            max_delay_ms,
        )
        delay = estimate["delay_ms"]
        is_reliable = bool(delay is not None and float(estimate["confidence"]) >= min_confidence)
        if is_reliable:
            reliable.append(float(delay))
        rows.append(
            {
                "index": index,
                "start_sec": round(start / sample_rate, 6),
                "end_sec": round(end / sample_rate, 6),
                "delay_ms": None if delay is None else round(float(delay), 6),
                "confidence": round(float(estimate["confidence"]), 6),
                "peak": round(float(estimate["peak"]), 9),
                "reliable": is_reliable,
            }
        )
    fallback = float(np.median(reliable)) if reliable else 0.0
    smoothed = np.array(
        [
            float(row["delay_ms"]) if row["reliable"] and row["delay_ms"] is not None else fallback
            for row in rows
        ],
        dtype=np.float64,
    )
    if smoothed.size >= 5:
        smoothed = signal.medfilt(smoothed, kernel_size=5)
    for index, row in enumerate(rows):
        row["smoothed_delay_ms"] = round(float(smoothed[index]), 6)
    return rows


def timeline_contract(
    *,
    sample_rate: int,
    delay_rows: list[dict[str, Any]],
    source: str,
    estimator: str,
) -> dict[str, Any]:
    reliable = [
        float(row["smoothed_delay_ms"])
        for row in delay_rows
        if row.get("reliable") is True and row.get("smoothed_delay_ms") is not None
    ]
    all_delays = [
        float(row.get("smoothed_delay_ms", row.get("delay_ms", 0.0)) or 0.0)
        for row in delay_rows
    ]
    path_changes = 0
    for previous, current in zip(all_delays, all_delays[1:]):
        if abs(current - previous) >= 40.0:
            path_changes += 1
    drift = (all_delays[-1] - all_delays[0]) if len(all_delays) >= 2 else 0.0
    return {
        "schema": TIMELINE_SCHEMA,
        "sign_convention": SIGN_CONVENTION,
        "sample_rate": sample_rate,
        "source": source,
        "estimator": estimator,
        "delay_rows": len(delay_rows),
        "reliable_delay_rows": len(reliable),
        "median_delay_ms": None if not reliable else round(float(np.median(reliable)), 3),
        "delay_p10_ms": None if not reliable else round(float(np.percentile(reliable, 10)), 3),
        "delay_p90_ms": None if not reliable else round(float(np.percentile(reliable, 90)), 3),
        "estimated_drift_ms": round(float(drift), 3),
        "path_change_count": path_changes,
    }
