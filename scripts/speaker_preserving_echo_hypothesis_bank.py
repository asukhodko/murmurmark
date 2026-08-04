#!/usr/bin/env python3
"""Physical residual-echo hypotheses with improvement-only selection."""

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
    neural_candidate: np.ndarray | None = None,
) -> tuple[np.ndarray, dict[str, Any]]:
    fallback = np.asarray(baseline, dtype=np.float32)
    estimate = np.asarray(echo_estimate, dtype=np.float32)
    reference = np.asarray(remote, dtype=np.float32)
    input_failures: list[str] = []
    if fallback.ndim != 1:
        input_failures.append("baseline_not_mono")
    if estimate.shape != fallback.shape:
        input_failures.append("echo_estimate_shape_mismatch")
    if reference.shape != fallback.shape:
        input_failures.append("remote_shape_mismatch")
    if not np.all(np.isfinite(fallback)):
        input_failures.append("baseline_non_finite")
    if not np.all(np.isfinite(estimate)):
        input_failures.append("echo_estimate_non_finite")
    if not np.all(np.isfinite(reference)):
        input_failures.append("remote_non_finite")
    if input_failures:
        return fallback.copy(), {
            "schema": "murmurmark.speaker_preserving_echo_hypothesis_bank/v1",
            "selected": "baseline",
            "fail_open": True,
            "reasons": input_failures,
            "hypotheses": [],
        }
    hypotheses: list[tuple[str, np.ndarray]] = [
        (f"additional_fir_{multiplier:g}", fallback - multiplier * estimate)
        for multiplier in ADDITIONAL_FIR_MULTIPLIERS
    ]
    if neural_candidate is not None:
        neural = np.asarray(neural_candidate, dtype=np.float32)
        if neural.shape == fallback.shape and np.all(np.isfinite(neural)):
            hypotheses.append(("neural_echo_mapper", neural))
    accepted: list[tuple[float, str, np.ndarray, dict[str, Any]]] = []
    audit: list[dict[str, Any]] = []
    for name, candidate in hypotheses:
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
            "schema": "murmurmark.speaker_preserving_echo_hypothesis_bank/v1",
            "selected": "baseline",
            "fail_open": True,
            "hypotheses": audit,
        }
    _, name, selected, winner = min(accepted, key=lambda row: (row[0], row[1]))
    return selected.copy(), {
        "schema": "murmurmark.speaker_preserving_echo_hypothesis_bank/v1",
        "selected": name,
        "fail_open": False,
        "winner": winner,
        "hypotheses": audit,
    }
