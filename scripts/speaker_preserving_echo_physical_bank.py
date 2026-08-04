#!/usr/bin/env python3
"""Physical-only residual echo hypotheses with conservative selection."""

from __future__ import annotations

from typing import Any

import numpy as np

import speaker_preserving_echo_arbitration as ARBITER


ADDITIONAL_FIR_MULTIPLIERS = (0.25, 0.5, 1.0, 1.75)


def select(
    *,
    baseline: np.ndarray,
    echo_estimate: np.ndarray,
    remote: np.ndarray,
) -> tuple[np.ndarray, dict[str, Any]]:
    fallback = np.asarray(baseline, dtype=np.float32)
    estimate = np.asarray(echo_estimate, dtype=np.float32)
    reference = np.asarray(remote, dtype=np.float32)
    failures: list[str] = []
    if fallback.ndim != 1:
        failures.append("baseline_not_mono")
    if estimate.shape != fallback.shape:
        failures.append("echo_estimate_shape_mismatch")
    if reference.shape != fallback.shape:
        failures.append("remote_shape_mismatch")
    if not np.all(np.isfinite(fallback)):
        failures.append("baseline_non_finite")
    if not np.all(np.isfinite(estimate)):
        failures.append("echo_estimate_non_finite")
    if not np.all(np.isfinite(reference)):
        failures.append("remote_non_finite")
    if failures:
        return fallback.copy(), {
            "schema": "murmurmark.speaker_preserving_echo_physical_bank/v1",
            "selected": "baseline",
            "fail_open": True,
            "reasons": failures,
            "hypotheses": [],
        }

    accepted: list[tuple[float, str, np.ndarray, dict[str, Any]]] = []
    audit: list[dict[str, Any]] = []
    for multiplier in ADDITIONAL_FIR_MULTIPLIERS:
        name = f"additional_fir_{multiplier:g}"
        candidate = fallback - multiplier * estimate
        selected, arbitration = ARBITER.arbitrate(
            baseline=fallback,
            candidate=candidate,
            remote=reference,
        )
        row = {"hypothesis": name, **arbitration}
        audit.append(row)
        if arbitration["selected"] == "candidate":
            accepted.append(
                (
                    float(arbitration["candidate_remote_coherence"]),
                    name,
                    selected,
                    row,
                )
            )

    if not accepted:
        return fallback.copy(), {
            "schema": "murmurmark.speaker_preserving_echo_physical_bank/v1",
            "selected": "baseline",
            "fail_open": True,
            "reasons": ["no_improving_physical_hypothesis"],
            "hypotheses": audit,
        }

    _, name, selected, winner = min(accepted, key=lambda row: (row[0], row[1]))
    return selected.copy(), {
        "schema": "murmurmark.speaker_preserving_echo_physical_bank/v1",
        "selected": name,
        "fail_open": False,
        "winner": winner,
        "hypotheses": audit,
    }
