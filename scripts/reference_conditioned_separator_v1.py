#!/usr/bin/env python3
"""Small deterministic three-stem separator used by the bounded v1 experiment."""

from __future__ import annotations

import math
import os
import random
from pathlib import Path
from typing import Any


SAMPLE_RATE = 16_000
CLIP_SAMPLES = 64_000
FRAME_SIZE = 320
HOP_SIZE = 160
FREQUENCY_BINS = FRAME_SIZE // 2 + 1


def configure_determinism(seed: int) -> None:
    import numpy as np
    import torch

    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.use_deterministic_algorithms(True)
    configured = int(os.environ.get("MURMURMARK_MAX_COMPUTE_THREADS") or 0)
    default = max(1, min(4, os.cpu_count() or 1))
    torch.set_num_threads(max(1, min(default, configured)) if configured > 0 else default)


def analysis_window() -> Any:
    import numpy as np

    return np.sqrt(np.hanning(FRAME_SIZE + 1)[:-1]).astype(np.float32)


def frame_audio(values: Any) -> Any:
    import torch

    left = HOP_SIZE
    frame_count = max(1, math.ceil((values.shape[-1] + left - FRAME_SIZE) / HOP_SIZE) + 1)
    total = FRAME_SIZE + (frame_count - 1) * HOP_SIZE
    right = max(0, total - (values.shape[-1] + left))
    padded = torch.nn.functional.pad(values, (left, right))
    return padded.unfold(-1, FRAME_SIZE, HOP_SIZE)


def stft(values: Any, window: Any) -> Any:
    import torch

    return torch.fft.rfft(frame_audio(values) * window, n=FRAME_SIZE, dim=-1)


def overlap_add(spectrum: Any, window: Any, output_samples: int) -> Any:
    import torch

    frames = torch.fft.irfft(spectrum, n=FRAME_SIZE, dim=-1) * window
    batch, frame_count, _ = frames.shape
    total = FRAME_SIZE + (frame_count - 1) * HOP_SIZE
    output = torch.zeros((batch, total), dtype=frames.dtype, device=frames.device)
    normalization = torch.zeros(total, dtype=frames.dtype, device=frames.device)
    window_square = window.square()
    for index in range(frame_count):
        start = index * HOP_SIZE
        output[:, start : start + FRAME_SIZE] += frames[:, index]
        normalization[start : start + FRAME_SIZE] += window_square
    output = output / normalization.clamp_min(1.0e-8)
    return output[:, HOP_SIZE : HOP_SIZE + output_samples]


def spectral_features(mixture_spec: Any, remote_spec: Any, echo_hint_spec: Any | None = None) -> Any:
    import torch

    mixture_mag = mixture_spec.abs().clamp_min(1.0e-7)
    remote_mag = remote_spec.abs().clamp_min(1.0e-7)
    if echo_hint_spec is None:
        echo_hint_spec = remote_spec
    echo_hint_mag = echo_hint_spec.abs().clamp_min(1.0e-7)
    cross = mixture_spec * remote_spec.conj()
    cross_scale = (mixture_mag * remote_mag).clamp_min(1.0e-7)
    echo_cross = mixture_spec * echo_hint_spec.conj()
    echo_cross_scale = (mixture_mag * echo_hint_mag).clamp_min(1.0e-7)
    return torch.cat(
        (
            torch.log1p(100.0 * mixture_mag),
            torch.log1p(100.0 * remote_mag),
            cross.real / cross_scale,
            cross.imag / cross_scale,
            torch.log1p(100.0 * echo_hint_mag),
            echo_cross.real / echo_cross_scale,
            echo_cross.imag / echo_cross_scale,
        ),
        dim=-1,
    )


def build_model(
    *,
    enrollment_dim: int,
    hidden_size: int,
    layers: int,
    mask_limit: float = 8.0,
) -> Any:
    import torch

    class ReferenceConditionedSeparator(torch.nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.enrollment_dim = enrollment_dim
            self.hidden_size = hidden_size
            self.layers = layers
            self.mask_limit = mask_limit
            self.spectral_projection = torch.nn.Sequential(
                torch.nn.Linear(7 * FREQUENCY_BINS, hidden_size),
                torch.nn.LayerNorm(hidden_size),
                torch.nn.SiLU(),
            )
            self.enrollment_projection = torch.nn.Sequential(
                torch.nn.Linear(enrollment_dim, hidden_size),
                torch.nn.LayerNorm(hidden_size),
                torch.nn.SiLU(),
            )
            self.recurrent = torch.nn.GRU(
                hidden_size,
                hidden_size,
                num_layers=layers,
                batch_first=True,
            )
            self.output = torch.nn.Linear(hidden_size, 4 * FREQUENCY_BINS)
            torch.nn.init.zeros_(self.output.weight)
            torch.nn.init.zeros_(self.output.bias)

        def forward(self, features: Any, enrollment: Any) -> tuple[Any, Any]:
            spectral = self.spectral_projection(features)
            speaker = self.enrollment_projection(enrollment)[:, None, :]
            recurrent, _ = self.recurrent(spectral + speaker)
            values = self.output(recurrent)
            target_real, target_imag, echo_real, echo_imag = values.chunk(4, dim=-1)
            target_mask = torch.complex(
                self.mask_limit * torch.tanh(target_real),
                self.mask_limit * torch.tanh(target_imag),
            )
            echo_mask = torch.complex(
                self.mask_limit * torch.tanh(echo_real),
                self.mask_limit * torch.tanh(echo_imag),
            )
            return target_mask, echo_mask

    return ReferenceConditionedSeparator()


def predict_spectra(
    model: Any,
    mixture: Any,
    remote: Any,
    enrollment: Any,
    window: Any,
    echo_hint: Any | None = None,
) -> dict[str, Any]:
    mixture_spec = stft(mixture, window)
    remote_spec = stft(remote, window)
    echo_hint_spec = stft(echo_hint, window) if echo_hint is not None else remote_spec
    target_mask, echo_mask = model(
        spectral_features(mixture_spec, remote_spec, echo_hint_spec),
        enrollment,
    )
    target_spec = target_mask * mixture_spec
    echo_spec = echo_mask * mixture_spec
    other_spec = mixture_spec - target_spec - echo_spec
    return {
        "mixture_spec": mixture_spec,
        "remote_spec": remote_spec,
        "echo_hint_spec": echo_hint_spec,
        "target_spec": target_spec,
        "echo_spec": echo_spec,
        "other_spec": other_spec,
    }


def apply_model(
    model: Any,
    mixture: Any,
    remote: Any,
    enrollment: Any,
    window: Any,
    echo_hint: Any | None = None,
) -> dict[str, Any]:
    spectra = predict_spectra(
        model,
        mixture,
        remote,
        enrollment,
        window,
        echo_hint=echo_hint,
    )
    target = overlap_add(spectra["target_spec"], window, mixture.shape[-1])
    echo = overlap_add(spectra["echo_spec"], window, mixture.shape[-1])
    other = mixture - target - echo
    return {
        "target_me": target,
        "remote_echo": echo,
        "other_local": other,
        **spectra,
    }


def normalized_complex_mse(estimate: Any, reference: Any) -> Any:
    numerator = (estimate - reference).abs().square().mean()
    denominator = reference.abs().square().mean().clamp_min(1.0e-8)
    return numerator / denominator


def separation_loss(
    predictions: dict[str, Any],
    target_spec: Any,
    echo_spec: Any,
) -> tuple[Any, dict[str, float]]:
    target_loss = normalized_complex_mse(predictions["target_spec"], target_spec)
    echo_loss = normalized_complex_mse(predictions["echo_spec"], echo_spec)
    other_loss = predictions["other_spec"].abs().square().mean() / (
        predictions["mixture_spec"].abs().square().mean().clamp_min(1.0e-8)
    )
    total = target_loss + echo_loss + 0.05 * other_loss
    return total, {
        "target": float(target_loss.detach().cpu()),
        "echo": float(echo_loss.detach().cpu()),
        "other": float(other_loss.detach().cpu()),
        "total": float(total.detach().cpu()),
    }


def mixture_normalized_separation_loss(
    predictions: dict[str, Any],
    target_spec: Any,
    echo_spec: Any,
    other_spec: Any,
) -> tuple[Any, dict[str, float]]:
    mixture_power = (
        predictions["mixture_spec"].abs().square().mean(dim=(1, 2)).clamp_min(1.0e-8)
    )

    def component(estimate: Any, reference: Any) -> Any:
        error = (estimate - reference).abs().square().mean(dim=(1, 2))
        return (error / mixture_power).mean()

    target_loss = component(predictions["target_spec"], target_spec)
    echo_loss = component(predictions["echo_spec"], echo_spec)
    other_loss = component(predictions["other_spec"], other_spec)
    total = target_loss + echo_loss + 0.2 * other_loss
    return total, {
        "target": float(target_loss.detach().cpu()),
        "echo": float(echo_loss.detach().cpu()),
        "other": float(other_loss.detach().cpu()),
        "total": float(total.detach().cpu()),
    }


def save_checkpoint(path: Path, model: Any, metadata: dict[str, Any]) -> None:
    import torch

    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save({"state_dict": model.state_dict(), "metadata": metadata}, path)


def load_checkpoint(path: Path, *, device: str = "cpu") -> tuple[Any, dict[str, Any]]:
    import torch

    payload = torch.load(path, map_location=device, weights_only=False)
    metadata = payload["metadata"]
    model = build_model(
        enrollment_dim=int(metadata["enrollment_dim"]),
        hidden_size=int(metadata["hidden_size"]),
        layers=int(metadata["layers"]),
        mask_limit=float(metadata.get("mask_limit", 8.0)),
    )
    model.load_state_dict(payload["state_dict"])
    return model.to(device), metadata
