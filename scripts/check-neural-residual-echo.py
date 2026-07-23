#!/usr/bin/env python3
"""Deterministic checks for Neural Residual Echo Suppression v1."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path
from typing import Any

import numpy as np


ROOT = Path(__file__).resolve().parent.parent


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


NEURAL = load_module(
    ROOT / "scripts/neural_residual_echo.py",
    "neural_residual_echo_checks",
)
BOOTSTRAP = load_module(
    ROOT / "scripts/bootstrap-neural-residual-echo-v1.py",
    "bootstrap_neural_residual_echo_checks",
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


class FixtureSuppressor:
    identity = "fixture"

    def __init__(self, behavior: str) -> None:
        self.behavior = behavior

    def enhance(
        self,
        mic: np.ndarray,
        farend: np.ndarray,
    ) -> tuple[np.ndarray, dict[str, Any]]:
        if self.behavior == "identity":
            return mic.copy(), {"behavior": self.behavior}
        if self.behavior == "oracle_subtract":
            return (mic - farend).astype(np.float32), {"behavior": self.behavior}
        if self.behavior == "nan":
            output = mic.copy()
            output[0] = np.nan
            return output, {"behavior": self.behavior}
        if self.behavior == "clip":
            return np.ones_like(mic) * 2.0, {"behavior": self.behavior}
        if self.behavior == "short":
            return mic[:-1], {"behavior": self.behavior}
        if self.behavior == "empty":
            return np.zeros(0, dtype=np.float32), {"behavior": self.behavior}
        raise RuntimeError("fixture_model_failure")


class UnityMaskSession:
    def run(
        self,
        _outputs: Any,
        inputs: dict[str, np.ndarray],
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        count = inputs["input"].shape[0]
        return (
            np.ones((count, 1, NEURAL.MASK_SIZE), dtype=np.float32),
            inputs["h01"],
            inputs["h02"],
        )


def fail_open_checks() -> None:
    random = np.random.default_rng(17)
    mic = random.normal(0.0, 0.08, NEURAL.SAMPLE_RATE).astype(np.float32)
    farend = random.normal(0.0, 0.03, mic.size).astype(np.float32)
    baseline = np.linspace(-0.2, 0.2, mic.size, dtype=np.float32)
    request = NEURAL.InferenceRequest(mic=mic, farend=farend)
    for behavior, expected_reason in (
        ("nan", "output_integrity_failed:finite,not_clipped"),
        ("clip", "output_integrity_failed:not_clipped"),
        ("short", "output_integrity_failed:exact_length"),
        ("empty", "output_integrity_failed:nonempty,exact_length,finite,not_clipped"),
        ("failure", "fixture_model_failure"),
    ):
        selected, report = NEURAL.run_fail_open(
            FixtureSuppressor(behavior),
            request,
            baseline=baseline,
            integrity_reference=mic,
        )
        require(report["fail_open"] is True, f"{behavior} must fail open")
        require(
            np.array_equal(selected, baseline),
            f"{behavior} must select the exact baseline",
        )
        require(
            expected_reason in report["reason"],
            f"{behavior} must expose a stable reason: {report['reason']}",
        )

    mismatch, report = NEURAL.run_fail_open(
        FixtureSuppressor("identity"),
        NEURAL.InferenceRequest(mic=mic, farend=farend[:-1]),
        baseline=baseline,
    )
    require(report["fail_open"] is True, "wrong input duration must fail open")
    require(np.array_equal(mismatch, baseline), "duration failure must keep baseline")


def synthetic_audio_checks() -> None:
    sample_rate = NEURAL.SAMPLE_RATE
    time_axis = np.arange(sample_rate, dtype=np.float32) / sample_rate
    local = (0.08 * np.sin(2.0 * np.pi * 220.0 * time_axis)).astype(np.float32)
    remote = (0.04 * np.sin(2.0 * np.pi * 440.0 * time_axis)).astype(np.float32)
    silence = np.zeros_like(local)

    selected, report = NEURAL.run_fail_open(
        FixtureSuppressor("identity"),
        NEURAL.InferenceRequest(mic=silence, farend=silence),
        baseline=silence,
    )
    require(report["status"] == "completed", "silence must be a valid input")
    require(np.array_equal(selected, silence), "silence must stay silent")

    selected, report = NEURAL.run_fail_open(
        FixtureSuppressor("oracle_subtract"),
        NEURAL.InferenceRequest(mic=remote, farend=remote),
        baseline=remote,
    )
    require(report["status"] == "completed", "remote-only fixture must run")
    require(float(np.max(np.abs(selected))) == 0.0, "remote-only must be removable")

    selected, report = NEURAL.run_fail_open(
        FixtureSuppressor("oracle_subtract"),
        NEURAL.InferenceRequest(mic=local, farend=silence),
        baseline=local,
    )
    require(report["status"] == "completed", "local-only fixture must run")
    require(np.allclose(selected, local), "local-only must stay intact")

    selected, report = NEURAL.run_fail_open(
        FixtureSuppressor("oracle_subtract"),
        NEURAL.InferenceRequest(mic=local + remote, farend=remote),
        baseline=local + remote,
    )
    require(report["status"] == "completed", "double-talk fixture must run")
    require(
        np.allclose(selected, local, atol=1.0e-7),
        "double-talk fixture must preserve local speech",
    )


def adapter_contract_checks() -> None:
    random = np.random.default_rng(23)
    mic = random.normal(0.0, 0.05, 16_003).astype(np.float32)
    farend = np.zeros_like(mic)
    adapter = NEURAL.MicrosoftDECAdapter(
        Path("fixture.onnx"),
        sequence_frames=7,
        session_factory=lambda _path: UnityMaskSession(),
    )
    output, metadata = adapter.enhance(mic, farend)
    require(output.size == mic.size, "adapter must preserve exact sample count")
    require(np.all(np.isfinite(output)), "adapter output must be finite")
    require(
        np.corrcoef(output, mic)[0, 1] >= 0.999999,
        "unity mask must preserve waveform",
    )
    require(metadata["normalization"] == "none", "adapter must not normalize gain")
    replay, _ = NEURAL.MicrosoftDECAdapter(
        Path("fixture.onnx"),
        sequence_frames=7,
        session_factory=lambda _path: UnityMaskSession(),
    ).enhance(mic, farend)
    require(np.array_equal(output, replay), "adapter replay must be bit deterministic")


def bootstrap_checks() -> None:
    with tempfile.TemporaryDirectory(prefix="murmurmark-neural-models-") as temporary:
        model_dir = Path(temporary)
        missing = BOOTSTRAP.check_model(
            BOOTSTRAP.MODELS[0],
            model_dir,
            download=False,
        )
        require(missing["passed"] is False, "missing model must fail verification")
        require(
            missing["checks"]["exists"] is False,
            "missing model must identify absence",
        )
        corrupt = model_dir / BOOTSTRAP.MODELS[0]["filename"]
        corrupt.write_bytes(b"not an ONNX model")
        invalid = BOOTSTRAP.check_model(
            BOOTSTRAP.MODELS[0],
            model_dir,
            download=False,
        )
        require(invalid["passed"] is False, "bad checksum must fail verification")
        require(
            invalid["checks"]["sha256_match"] is False,
            "bad checksum must be explicit",
        )


def optional_real_model_smoke() -> None:
    model = (
        Path.home()
        / ".local/share/murmurmark/models/neural-residual-echo-v1/"
        "dec-baseline-model-icassp2022.onnx"
    )
    if not model.exists() or NEURAL.sha256(model) != NEURAL.MODEL_SHA256:
        return
    sample_rate = NEURAL.SAMPLE_RATE
    time_axis = np.arange(sample_rate, dtype=np.float32) / sample_rate
    farend = (0.04 * np.sin(2.0 * np.pi * 440.0 * time_axis)).astype(np.float32)
    mic = (
        farend
        + 0.08 * np.sin(2.0 * np.pi * 220.0 * time_axis)
    ).astype(np.float32)
    first, first_report = NEURAL.run_fail_open(
        NEURAL.MicrosoftDECAdapter(model),
        NEURAL.InferenceRequest(mic=mic, farend=farend),
        baseline=mic,
        integrity_reference=mic,
    )
    second, second_report = NEURAL.run_fail_open(
        NEURAL.MicrosoftDECAdapter(model),
        NEURAL.InferenceRequest(mic=mic, farend=farend),
        baseline=mic,
        integrity_reference=mic,
    )
    require(first_report["status"] == "completed", "real model smoke must complete")
    require(second_report["status"] == "completed", "real model replay must complete")
    require(np.array_equal(first, second), "real model must replay deterministically")


def main() -> int:
    fail_open_checks()
    synthetic_audio_checks()
    adapter_contract_checks()
    bootstrap_checks()
    optional_real_model_smoke()
    print("neural residual echo checks ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
