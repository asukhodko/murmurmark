#!/usr/bin/env python3
"""Model-neutral local inference primitives for neural residual echo experiments."""

from __future__ import annotations

import hashlib
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

import numpy as np
from scipy import signal


SAMPLE_RATE = 16_000
FRAME_SIZE = 320
HOP_SIZE = 160
HIDDEN_SIZE = 322
FEATURE_SIZE = 322
MASK_SIZE = 161
MODEL_SHA256 = "4436ee4f80e5f1d0299196bd7057137a3cad7cac324409dce7540f2a113bb931"
AECMOS_SHA256 = "b517d8d9ca2f91ea55d15f605a15917c19be5d832868fe115c7c5bc48986dae1"


class EchoSuppressor(Protocol):
    """Minimal contract shared by real and fixture suppressors."""

    identity: str

    def enhance(self, mic: np.ndarray, farend: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
        """Return enhanced mic at 16 kHz and deterministic inference metadata."""


@dataclass(frozen=True)
class InferenceRequest:
    mic: np.ndarray
    farend: np.ndarray
    sample_rate: int = SAMPLE_RATE


class InferenceContractError(RuntimeError):
    """Raised when inference cannot satisfy the fail-open audio contract."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def deterministic_resample(
    audio: np.ndarray,
    source_rate: int,
    target_rate: int = SAMPLE_RATE,
) -> np.ndarray:
    """Resample without gain normalization and with a deterministic output length."""
    values = np.asarray(audio, dtype=np.float32)
    if values.ndim != 1:
        raise InferenceContractError("audio_must_be_mono")
    if source_rate <= 0 or target_rate <= 0:
        raise InferenceContractError("sample_rate_must_be_positive")
    if source_rate == target_rate:
        return values.copy()
    divisor = math.gcd(int(source_rate), int(target_rate))
    result = signal.resample_poly(
        values,
        target_rate // divisor,
        source_rate // divisor,
    ).astype(np.float32)
    expected = int(round(values.size * target_rate / source_rate))
    if result.size < expected:
        result = np.pad(result, (0, expected - result.size))
    return result[:expected]


def validate_request(request: InferenceRequest) -> tuple[np.ndarray, np.ndarray]:
    mic = np.asarray(request.mic, dtype=np.float32)
    farend = np.asarray(request.farend, dtype=np.float32)
    if request.sample_rate != SAMPLE_RATE:
        mic = deterministic_resample(mic, request.sample_rate)
        farend = deterministic_resample(farend, request.sample_rate)
    if mic.ndim != 1 or farend.ndim != 1:
        raise InferenceContractError("inputs_must_be_mono")
    if mic.size == 0 or farend.size == 0:
        raise InferenceContractError("inputs_must_be_nonempty")
    if mic.size != farend.size:
        raise InferenceContractError(
            f"input_length_mismatch:mic={mic.size}:farend={farend.size}"
        )
    if not np.all(np.isfinite(mic)) or not np.all(np.isfinite(farend)):
        raise InferenceContractError("inputs_must_be_finite")
    return mic, farend


def output_integrity(
    *,
    candidate: np.ndarray,
    expected_length: int,
    baseline: np.ndarray,
) -> dict[str, Any]:
    values = np.asarray(candidate)
    baseline_values = np.asarray(baseline)
    finite = bool(values.size and np.all(np.isfinite(values)))
    peak = float(np.max(np.abs(values))) if finite else None
    clipped_ratio = (
        float(np.mean(np.abs(values) >= 0.999))
        if finite and values.size
        else None
    )
    baseline_peak = (
        float(np.max(np.abs(baseline_values))) if baseline_values.size else 0.0
    )
    baseline_clipped_ratio = (
        float(np.mean(np.abs(baseline_values) >= 0.999))
        if baseline_values.size
        else 0.0
    )
    checks = {
        "nonempty": bool(values.size),
        "mono": values.ndim == 1,
        "exact_length": values.ndim == 1 and values.size == expected_length,
        "finite": finite,
        "not_clipped": (
            finite
            and peak is not None
            and peak <= max(0.9999, baseline_peak + 1.0e-6)
            and clipped_ratio is not None
            and clipped_ratio
            <= baseline_clipped_ratio + max(1.0e-7, baseline_clipped_ratio * 0.05)
        ),
    }
    return {
        "checks": checks,
        "passed": all(checks.values()),
        "samples": int(values.size),
        "expected_samples": int(expected_length),
        "peak": round(peak, 9) if peak is not None else None,
        "clipped_ratio": round(clipped_ratio, 12)
        if clipped_ratio is not None
        else None,
        "baseline_peak": round(baseline_peak, 9),
        "baseline_clipped_ratio": round(baseline_clipped_ratio, 12),
    }


def run_fail_open(
    suppressor: EchoSuppressor,
    request: InferenceRequest,
    *,
    baseline: np.ndarray,
    integrity_reference: np.ndarray | None = None,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Run one suppressor and select the exact baseline on any failure."""
    started = time.monotonic()
    baseline_values = np.asarray(baseline, dtype=np.float32)
    integrity_values = (
        baseline_values
        if integrity_reference is None
        else np.asarray(integrity_reference, dtype=np.float32)
    )
    candidate_integrity: dict[str, Any] | None = None
    try:
        mic, farend = validate_request(request)
        if baseline_values.ndim != 1 or baseline_values.size != mic.size:
            raise InferenceContractError("baseline_length_mismatch")
        candidate, metadata = suppressor.enhance(mic, farend)
        candidate_integrity = output_integrity(
            candidate=candidate,
            expected_length=mic.size,
            baseline=integrity_values,
        )
        if not candidate_integrity["passed"]:
            failed = [
                name
                for name, passed in candidate_integrity["checks"].items()
                if not passed
            ]
            raise InferenceContractError(
                "output_integrity_failed:" + ",".join(failed)
            )
        selected = np.asarray(candidate, dtype=np.float32)
        status = "completed"
        reason = "candidate_satisfies_inference_contract"
        fail_open = False
    except Exception as error:  # adapters expose backend-specific runtime errors
        selected = baseline_values.copy()
        metadata = {}
        fallback_integrity = output_integrity(
            candidate=selected,
            expected_length=baseline_values.size,
            baseline=baseline_values,
        )
        status = "failed_open"
        reason = f"{type(error).__name__}:{error}"
        fail_open = True
    else:
        fallback_integrity = None
    return selected, {
        "schema": "murmurmark.neural_residual_echo_inference/v1",
        "suppressor": suppressor.identity,
        "status": status,
        "reason": reason,
        "fail_open": fail_open,
        "selected": "baseline" if fail_open else "candidate",
        "runtime_sec": round(time.monotonic() - started, 6),
        "integrity": candidate_integrity,
        "fallback_integrity": fallback_integrity,
        "adapter": metadata,
    }


class MicrosoftDECAdapter:
    """ONNX adapter for Microsoft's ICASSP 2022 DEC baseline."""

    identity = "microsoft_icassp2022_dec"

    def __init__(
        self,
        model_path: Path,
        *,
        sequence_frames: int = 512,
        session_factory: Callable[[Path], Any] | None = None,
    ) -> None:
        self.model_path = model_path.expanduser().resolve()
        self.sequence_frames = max(1, int(sequence_frames))
        self._session_factory = session_factory or self._create_session
        self._session: Any | None = None
        self.window = np.sqrt(np.hanning(FRAME_SIZE + 1)[:-1]).astype(np.float32)

    @staticmethod
    def _create_session(path: Path) -> Any:
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        options.enable_mem_pattern = False
        return ort.InferenceSession(
            str(path),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )

    @property
    def session(self) -> Any:
        if self._session is None:
            self._session = self._session_factory(self.model_path)
        return self._session

    @staticmethod
    def _features(mic_mag: np.ndarray, far_mag: np.ndarray) -> np.ndarray:
        mic_log_power = np.log10(np.maximum(mic_mag**2, 1.0e-12))
        far_log_power = np.log10(np.maximum(far_mag**2, 1.0e-12))
        return (
            np.concatenate((mic_log_power, far_log_power), axis=1)[:, None, :]
            / 20.0
        ).astype(np.float32)

    def enhance(
        self,
        mic: np.ndarray,
        farend: np.ndarray,
    ) -> tuple[np.ndarray, dict[str, Any]]:
        mic = np.asarray(mic, dtype=np.float32)
        farend = np.asarray(farend, dtype=np.float32)
        if mic.ndim != 1 or farend.ndim != 1 or mic.size != farend.size:
            raise InferenceContractError("adapter_input_contract_failed")
        original_length = mic.size
        padded_length = original_length + HOP_SIZE
        frame_count = max(
            1,
            int(math.ceil(max(0, padded_length - FRAME_SIZE) / HOP_SIZE)) + 1,
        )
        synthesis_length = FRAME_SIZE + (frame_count - 1) * HOP_SIZE
        right_padding = max(0, synthesis_length - padded_length)
        mic_padded = np.pad(mic, (HOP_SIZE, right_padding))
        far_padded = np.pad(farend, (HOP_SIZE, right_padding))
        output = np.zeros(synthesis_length, dtype=np.float64)
        h01 = np.zeros((1, 1, HIDDEN_SIZE), dtype=np.float32)
        h02 = np.zeros((1, 1, HIDDEN_SIZE), dtype=np.float32)
        mask_min = math.inf
        mask_max = -math.inf

        for frame_start in range(0, frame_count, self.sequence_frames):
            frame_end = min(frame_count, frame_start + self.sequence_frames)
            indices = (
                np.arange(frame_start, frame_end, dtype=np.int64)[:, None] * HOP_SIZE
                + np.arange(FRAME_SIZE, dtype=np.int64)[None, :]
            )
            mic_frames = mic_padded[indices] * self.window
            far_frames = far_padded[indices] * self.window
            mic_spec = np.fft.rfft(mic_frames, n=FRAME_SIZE, axis=1)
            far_spec = np.fft.rfft(far_frames, n=FRAME_SIZE, axis=1)
            mic_mag = np.abs(mic_spec)
            mic_phase = np.ones_like(mic_spec)
            significant = mic_mag > 1.0e-20
            np.divide(
                mic_spec,
                mic_mag,
                out=mic_phase,
                where=significant,
            )
            features = self._features(mic_mag, np.abs(far_spec))
            masks, h01, h02 = self.session.run(
                None,
                {"input": features, "h01": h01, "h02": h02},
            )
            masks = np.asarray(masks, dtype=np.float32)
            if masks.shape != (frame_end - frame_start, 1, MASK_SIZE):
                raise InferenceContractError(
                    f"model_output_shape:{masks.shape}"
                )
            if not np.all(np.isfinite(masks)):
                raise InferenceContractError("model_output_nonfinite")
            mask_values = masks[:, 0, :]
            mask_min = min(mask_min, float(np.min(mask_values)))
            mask_max = max(mask_max, float(np.max(mask_values)))
            enhanced = (
                np.fft.irfft(
                    mask_values * mic_mag * mic_phase,
                    n=FRAME_SIZE,
                    axis=1,
                )
                * self.window
            )
            for offset, frame in enumerate(enhanced):
                start = (frame_start + offset) * HOP_SIZE
                output[start : start + FRAME_SIZE] += frame

        exact = output[HOP_SIZE : HOP_SIZE + original_length].astype(np.float32)
        return exact, {
            "model_path": str(self.model_path),
            "sample_rate": SAMPLE_RATE,
            "frame_size": FRAME_SIZE,
            "hop_size": HOP_SIZE,
            "sequence_frames": self.sequence_frames,
            "frame_count": frame_count,
            "left_padding_samples": HOP_SIZE,
            "right_padding_samples": right_padding,
            "normalization": "none",
            "mask_min": round(mask_min, 9),
            "mask_max": round(mask_max, 9),
        }


class AECMOSNoScenario:
    """Secondary, non-gating Microsoft AECMOS estimator."""

    identity = "microsoft_aecmos_16k_no_scenarios"

    def __init__(
        self,
        model_path: Path,
        *,
        session_factory: Callable[[Path], Any] | None = None,
    ) -> None:
        self.model_path = model_path.expanduser().resolve()
        self._session_factory = session_factory or MicrosoftDECAdapter._create_session
        self._session: Any | None = None

    @property
    def session(self) -> Any:
        if self._session is None:
            self._session = self._session_factory(self.model_path)
        return self._session

    @staticmethod
    def _mel(audio: np.ndarray) -> np.ndarray:
        import librosa

        mel = librosa.feature.melspectrogram(
            y=np.asarray(audio, dtype=np.float32),
            sr=SAMPLE_RATE,
            n_fft=513,
            hop_length=256,
            n_mels=160,
        )
        maximum = float(np.max(mel)) if mel.size else 0.0
        if maximum <= 0.0:
            return np.zeros((mel.shape[1], 160), dtype=np.float32)
        scaled = (librosa.power_to_db(mel, ref=maximum) + 40.0) / 40.0
        return scaled.T.astype(np.float32)

    def score(
        self,
        farend: np.ndarray,
        mic: np.ndarray,
        enhanced: np.ndarray,
    ) -> dict[str, Any]:
        count = min(farend.size, mic.size, enhanced.size, 20 * SAMPLE_RATE)
        if count <= 0:
            raise InferenceContractError("aecmos_empty_input")
        features = np.stack(
            (
                self._mel(farend[:count]),
                self._mel(mic[:count]),
                self._mel(enhanced[:count]),
            )
        )[None, ...].astype(np.float32)
        hidden = np.zeros((4, 1, 64), dtype=np.float32)
        result = np.asarray(
            self.session.run(None, {"input": features, "h0": hidden})[0]
        ).reshape(-1)
        if result.size < 2 or not np.all(np.isfinite(result[:2])):
            raise InferenceContractError("aecmos_invalid_output")
        return {
            "echo_mos": round(float(result[0]), 6),
            "degradation_mos": round(float(result[1]), 6),
            "samples": int(count),
            "seconds": round(count / SAMPLE_RATE, 3),
        }
