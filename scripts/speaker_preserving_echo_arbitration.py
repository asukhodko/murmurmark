#!/usr/bin/env python3
"""Deterministic improvement-only arbitration for neural echo candidates."""

from __future__ import annotations

import math
from typing import Any

import numpy as np


SAMPLE_RATE = 16_000
FRAME_SIZE = 320
HOP_SIZE = 160
MIN_BASELINE_COHERENCE = 0.05
MAX_CANDIDATE_TO_BASELINE_COHERENCE = 0.99
MAX_ENERGY_INCREASE_DB = 6.0
MAX_ENERGY_REDUCTION_DB = 18.0


def analysis_window() -> np.ndarray:
    return np.sqrt(np.hanning(FRAME_SIZE + 1)[:-1]).astype(np.float32)


def frame_spectrum(values: np.ndarray) -> np.ndarray:
    audio = np.asarray(values, dtype=np.float32)
    padded = np.pad(audio, (HOP_SIZE, 0))
    if padded.size < FRAME_SIZE:
        padded = np.pad(padded, (0, FRAME_SIZE - padded.size))
    frames = np.lib.stride_tricks.sliding_window_view(padded, FRAME_SIZE)[::HOP_SIZE]
    return np.fft.rfft(frames * analysis_window(), axis=-1)


def remote_coherence(values: np.ndarray, remote: np.ndarray) -> float:
    candidate = np.asarray(values, dtype=np.float32)
    reference = np.asarray(remote, dtype=np.float32)
    count = min(candidate.size, reference.size)
    if count <= FRAME_SIZE or not np.any(reference[:count]):
        return 0.0
    candidate_spec = frame_spectrum(candidate[:count])
    remote_spec = frame_spectrum(reference[:count])
    frame_count = min(candidate_spec.shape[0], remote_spec.shape[0])
    candidate_spec = candidate_spec[:frame_count]
    remote_spec = remote_spec[:frame_count]
    cross = np.sum(candidate_spec * np.conj(remote_spec), axis=0)
    denominator = np.sqrt(
        np.sum(np.abs(candidate_spec) ** 2, axis=0)
        * np.sum(np.abs(remote_spec) ** 2, axis=0)
    )
    coherence = np.abs(cross) / np.maximum(denominator, 1.0e-9)
    speech_band = coherence[3:151]
    return float(np.mean(speech_band)) if speech_band.size else 0.0


def rms_db(values: np.ndarray) -> float:
    audio = np.asarray(values, dtype=np.float64)
    return 10.0 * math.log10(float(np.mean(audio**2)) + 1.0e-12)


def arbitrate(
    *,
    baseline: np.ndarray,
    candidate: np.ndarray,
    remote: np.ndarray,
) -> tuple[np.ndarray, dict[str, Any]]:
    fallback = np.asarray(baseline, dtype=np.float32)
    proposed = np.asarray(candidate, dtype=np.float32)
    reference = np.asarray(remote, dtype=np.float32)
    reasons: list[str] = []
    if fallback.ndim != 1 or proposed.shape != fallback.shape or reference.shape != fallback.shape:
        reasons.append("shape_mismatch")
    if not np.all(np.isfinite(proposed)):
        reasons.append("candidate_non_finite")
    if not reasons:
        baseline_coherence = remote_coherence(fallback, reference)
        candidate_coherence = remote_coherence(proposed, reference)
        baseline_rms = rms_db(fallback)
        candidate_rms = rms_db(proposed)
        energy_delta = candidate_rms - baseline_rms
        if baseline_coherence < MIN_BASELINE_COHERENCE:
            reasons.append("baseline_remote_coherence_below_floor")
        if candidate_coherence > baseline_coherence * MAX_CANDIDATE_TO_BASELINE_COHERENCE:
            reasons.append("candidate_does_not_reduce_remote_coherence")
        if energy_delta > MAX_ENERGY_INCREASE_DB:
            reasons.append("candidate_energy_increase")
        if energy_delta < -MAX_ENERGY_REDUCTION_DB:
            reasons.append("candidate_energy_reduction")
        if float(np.mean(np.abs(proposed) >= 0.995)) > 0.0001:
            reasons.append("candidate_clipping")
    else:
        baseline_coherence = None
        candidate_coherence = None
        baseline_rms = rms_db(fallback) if fallback.ndim == 1 and fallback.size else None
        candidate_rms = None
        energy_delta = None
    selected = fallback.copy() if reasons else proposed.copy()
    return selected, {
        "schema": "murmurmark.speaker_preserving_echo_arbitration/v1",
        "selected": "baseline" if reasons else "candidate",
        "fail_open": bool(reasons),
        "reasons": reasons,
        "baseline_remote_coherence": baseline_coherence,
        "candidate_remote_coherence": candidate_coherence,
        "baseline_rms_db": baseline_rms,
        "candidate_rms_db": candidate_rms,
        "energy_delta_db": energy_delta,
        "thresholds": {
            "minimum_baseline_coherence": MIN_BASELINE_COHERENCE,
            "maximum_candidate_to_baseline_coherence": (
                MAX_CANDIDATE_TO_BASELINE_COHERENCE
            ),
            "maximum_energy_increase_db": MAX_ENERGY_INCREASE_DB,
            "maximum_energy_reduction_db": MAX_ENERGY_REDUCTION_DB,
        },
    }
