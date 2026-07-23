#!/usr/bin/env python3
"""Deterministic checks for the Echo Suppression Promotion timeline contract."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from echo_promotion_timeline import (
    SIGN_CONVENTION,
    align_remote_constant,
    align_remote_curve,
    estimate_delay_rows,
    timeline_contract,
)


ROOT = Path(__file__).resolve().parent.parent


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def impulse_check() -> None:
    sample_rate = 1_000
    remote = np.zeros(500, dtype=np.float32)
    remote[100] = 1.0
    delayed = align_remote_constant(remote, sample_rate, 80.0)
    advanced = align_remote_constant(remote, sample_rate, -80.0)
    require(int(np.argmax(delayed)) == 180, "+80ms must place remote 80 samples later")
    require(int(np.argmax(advanced)) == 20, "-80ms must place remote 80 samples earlier")


def drift_and_path_change_check() -> None:
    sample_rate = 1_000
    random = np.random.default_rng(42)
    remote = random.normal(0.0, 0.1, sample_rate * 20).astype(np.float32)
    mic = np.zeros_like(remote)
    midpoint = remote.size // 2
    mic[:midpoint] = align_remote_constant(remote, sample_rate, 80.0)[:midpoint]
    mic[midpoint:] = align_remote_constant(remote, sample_rate, -80.0)[midpoint:]
    rows = estimate_delay_rows(
        remote,
        mic,
        sample_rate,
        window_sec=2.0,
        hop_sec=2.0,
        min_delay_ms=-200.0,
        max_delay_ms=200.0,
        min_confidence=1.01,
    )
    require(rows, "delay estimator returned no rows")
    require(abs(float(rows[1]["smoothed_delay_ms"]) - 80.0) <= 2.0, "positive delay was not recovered")
    require(abs(float(rows[-2]["smoothed_delay_ms"]) + 80.0) <= 2.0, "negative delay was not recovered")
    contract = timeline_contract(
        sample_rate=sample_rate,
        delay_rows=rows,
        source="synthetic",
        estimator="test",
    )
    require(contract["sign_convention"] == SIGN_CONVENTION, "sign convention changed")
    require(int(contract["path_change_count"]) >= 1, "path change was not exposed")
    aligned = align_remote_curve(remote, sample_rate, rows)
    correlation = float(np.corrcoef(aligned[2_000:8_000], mic[2_000:8_000])[0, 1])
    require(correlation >= 0.99, "piecewise alignment failed on stable positive-delay region")


def helper_contract_check() -> None:
    rust = (ROOT / "tools/murmurmark-aec-webrtc/src/main.rs").read_text(encoding="utf-8")
    speex = (ROOT / "tools/murmurmark-aec-speexdsp.c").read_text(encoding="utf-8")
    require('parse::<f64>()' in rust, "WebRTC helper must parse signed floating-point delay")
    require('parse::<u16>()' not in rust, "WebRTC helper still silently narrows signed delay")
    require("signed_delay_ms" in speex, "Speex helper does not expose the signed-delay contract")


def main() -> int:
    impulse_check()
    drift_and_path_change_check()
    helper_contract_check()
    print("echo promotion timeline checks ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
