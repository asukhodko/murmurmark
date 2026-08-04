#!/usr/bin/env python3
"""State-aware completion of the FIR echo subtraction in double-talk."""

from __future__ import annotations

import math
from typing import Any

import numpy as np

import speaker_preserving_echo_arbitration as ARBITER


MIN_STRENGTH = 0.20
MAX_STRENGTH = 0.90
MAX_ADDITIONAL_MULTIPLIER = 4.0
MIN_ECHO_RMS_DB = -60.0
MIN_SIGNED_GAIN_TO_EXPECTED_RATIO = 0.50
MAX_RESIDUAL_PROJECTION_RATIO = 0.75
MIN_BASELINE_REMOTE_COHERENCE = 0.05
MAX_REMOTE_COHERENCE_RATIO = 0.95
MIN_ENERGY_DELTA_DB = -12.0
MAX_ENERGY_DELTA_DB = 3.0
MAX_CLIPPED_SAMPLE_RATIO = 0.0001


def signed_projection(values: np.ndarray, reference: np.ndarray) -> float:
    signal = np.asarray(values, dtype=np.float64)
    basis = np.asarray(reference, dtype=np.float64)
    denominator = float(np.dot(basis, basis)) + 1.0e-12
    return float(np.dot(signal, basis) / denominator)


def select(
    *,
    baseline: np.ndarray,
    echo_estimate: np.ndarray,
    remote: np.ndarray,
    applied_strength: float,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Complete a known partial FIR subtraction or return exact baseline."""

    fallback = np.asarray(baseline, dtype=np.float32)
    estimate = np.asarray(echo_estimate, dtype=np.float32)
    reference = np.asarray(remote, dtype=np.float32)
    reasons: list[str] = []
    if fallback.ndim != 1:
        reasons.append("baseline_not_mono")
    if estimate.shape != fallback.shape:
        reasons.append("echo_estimate_shape_mismatch")
    if reference.shape != fallback.shape:
        reasons.append("remote_shape_mismatch")
    if not np.all(np.isfinite(fallback)):
        reasons.append("baseline_non_finite")
    if not np.all(np.isfinite(estimate)):
        reasons.append("echo_estimate_non_finite")
    if not np.all(np.isfinite(reference)):
        reasons.append("remote_non_finite")
    strength = float(applied_strength)
    if not math.isfinite(strength) or not MIN_STRENGTH <= strength <= MAX_STRENGTH:
        reasons.append("unsupported_applied_strength")
    if reasons:
        return fallback.copy(), {
            "schema": "murmurmark.speaker_preserving_echo_state_completion/v1",
            "selected": "baseline",
            "fail_open": True,
            "reasons": reasons,
        }

    additional_multiplier = (1.0 - strength) / strength
    if additional_multiplier > MAX_ADDITIONAL_MULTIPLIER:
        reasons.append("additional_multiplier_above_limit")
    proposed = fallback - additional_multiplier * estimate
    echo_rms_db = ARBITER.rms_db(estimate)
    baseline_gain = signed_projection(fallback, estimate)
    candidate_gain = signed_projection(proposed, estimate)
    baseline_coherence = ARBITER.remote_coherence(fallback, reference)
    candidate_coherence = ARBITER.remote_coherence(proposed, reference)
    coherence_ratio = candidate_coherence / max(baseline_coherence, 1.0e-9)
    baseline_rms_db = ARBITER.rms_db(fallback)
    candidate_rms_db = ARBITER.rms_db(proposed)
    energy_delta_db = candidate_rms_db - baseline_rms_db
    projection_ratio = abs(candidate_gain) / max(abs(baseline_gain), 1.0e-9)
    clipped_sample_ratio = float(np.mean(np.abs(proposed) >= 0.995))

    if echo_rms_db < MIN_ECHO_RMS_DB:
        reasons.append("echo_estimate_below_floor")
    if baseline_gain < additional_multiplier * MIN_SIGNED_GAIN_TO_EXPECTED_RATIO:
        reasons.append("signed_residual_gain_below_physical_floor")
    if projection_ratio > MAX_RESIDUAL_PROJECTION_RATIO:
        reasons.append("signed_residual_projection_not_reduced")
    if baseline_coherence < MIN_BASELINE_REMOTE_COHERENCE:
        reasons.append("baseline_remote_coherence_below_floor")
    if coherence_ratio > MAX_REMOTE_COHERENCE_RATIO:
        reasons.append("candidate_remote_coherence_not_reduced")
    if energy_delta_db < MIN_ENERGY_DELTA_DB:
        reasons.append("candidate_energy_reduction")
    if energy_delta_db > MAX_ENERGY_DELTA_DB:
        reasons.append("candidate_energy_increase")
    if clipped_sample_ratio > MAX_CLIPPED_SAMPLE_RATIO:
        reasons.append("candidate_clipping")

    selected = fallback.copy() if reasons else proposed.astype(np.float32)
    return selected, {
        "schema": "murmurmark.speaker_preserving_echo_state_completion/v1",
        "selected": "baseline" if reasons else "state_completed_fir",
        "fail_open": bool(reasons),
        "reasons": reasons,
        "applied_strength": strength,
        "additional_multiplier": additional_multiplier,
        "echo_rms_db": echo_rms_db,
        "baseline_signed_projection": baseline_gain,
        "candidate_signed_projection": candidate_gain,
        "signed_projection_ratio": projection_ratio,
        "baseline_remote_coherence": baseline_coherence,
        "candidate_remote_coherence": candidate_coherence,
        "remote_coherence_ratio": coherence_ratio,
        "baseline_rms_db": baseline_rms_db,
        "candidate_rms_db": candidate_rms_db,
        "energy_delta_db": energy_delta_db,
        "clipped_sample_ratio": clipped_sample_ratio,
        "thresholds": {
            "minimum_strength": MIN_STRENGTH,
            "maximum_strength": MAX_STRENGTH,
            "maximum_additional_multiplier": MAX_ADDITIONAL_MULTIPLIER,
            "minimum_echo_rms_db": MIN_ECHO_RMS_DB,
            "minimum_signed_gain_to_expected_ratio": (
                MIN_SIGNED_GAIN_TO_EXPECTED_RATIO
            ),
            "maximum_residual_projection_ratio": MAX_RESIDUAL_PROJECTION_RATIO,
            "minimum_baseline_remote_coherence": MIN_BASELINE_REMOTE_COHERENCE,
            "maximum_remote_coherence_ratio": MAX_REMOTE_COHERENCE_RATIO,
            "minimum_energy_delta_db": MIN_ENERGY_DELTA_DB,
            "maximum_energy_delta_db": MAX_ENERGY_DELTA_DB,
            "maximum_clipped_sample_ratio": MAX_CLIPPED_SAMPLE_RATIO,
        },
    }
