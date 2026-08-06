#!/usr/bin/env python3
"""Deterministic four-stem Target-Me separator used by the v1 qualification."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path
from typing import Any

import reference_conditioned_separator_v1 as BASE


SAMPLE_RATE = BASE.SAMPLE_RATE
CLIP_SAMPLES = BASE.CLIP_SAMPLES
FRAME_SIZE = BASE.FRAME_SIZE
HOP_SIZE = BASE.HOP_SIZE
FREQUENCY_BINS = BASE.FREQUENCY_BINS

configure_determinism = BASE.configure_determinism
analysis_window = BASE.analysis_window
stft = BASE.stft
overlap_add = BASE.overlap_add


def spectral_features(mixture_spec: Any, echo_hint_spec: Any) -> Any:
    """Describe the mixture, frozen echo hint and their unexplained remainder."""
    import torch

    local_spec = mixture_spec - echo_hint_spec
    mixture_mag = mixture_spec.abs().clamp_min(1.0e-7)
    echo_mag = echo_hint_spec.abs().clamp_min(1.0e-7)
    local_mag = local_spec.abs().clamp_min(1.0e-7)
    cross = mixture_spec * echo_hint_spec.conj()
    cross_scale = (mixture_mag * echo_mag).clamp_min(1.0e-7)
    return torch.cat(
        (
            torch.log1p(100.0 * mixture_mag),
            torch.log1p(100.0 * echo_mag),
            torch.log1p(100.0 * local_mag),
            local_spec.real / local_mag,
            local_spec.imag / local_mag,
            cross.real / cross_scale,
            cross.imag / cross_scale,
        ),
        dim=-1,
    )


def build_model(
    *,
    enrollment_dim: int,
    hidden_size: int,
    layers: int,
    mask_limit: float = 4.0,
) -> Any:
    """Build a query-conditioned target and residual extractor.

    The frozen echo estimate is a separate stem. The network predicts the
    queried speaker and unexplained residual; the other-local stem is the
    exact mixture remainder. This makes reconstruction true by construction.
    """
    import torch

    class MultiComponentSeparator(torch.nn.Module):
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
            self.query_projection = torch.nn.Sequential(
                torch.nn.Linear(enrollment_dim, 3 * hidden_size),
                torch.nn.Tanh(),
            )
            self.query_fusion = torch.nn.Sequential(
                torch.nn.Linear(2 * hidden_size, hidden_size),
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
            scale, shift, token = self.query_projection(enrollment).chunk(3, dim=-1)
            conditioned = spectral * (1.0 + 0.5 * scale[:, None, :]) + shift[:, None, :]
            token_frames = token[:, None, :].expand(-1, conditioned.shape[1], -1)
            fused = self.query_fusion(torch.cat((conditioned, token_frames), dim=-1))
            recurrent, _ = self.recurrent(fused)
            target_real, target_imag, residual_real, residual_imag = self.output(recurrent).chunk(
                4, dim=-1
            )
            target_mask = torch.complex(
                self.mask_limit * torch.tanh(target_real),
                self.mask_limit * torch.tanh(target_imag),
            )
            residual_mask = torch.complex(
                self.mask_limit * torch.tanh(residual_real),
                self.mask_limit * torch.tanh(residual_imag),
            )
            return target_mask, residual_mask

    return MultiComponentSeparator()


def predict_spectra(
    model: Any,
    mixture: Any,
    echo_hint: Any,
    enrollment: Any,
    window: Any,
) -> dict[str, Any]:
    mixture_spec = stft(mixture, window)
    echo_spec = stft(echo_hint, window)
    local_spec = mixture_spec - echo_spec
    target_mask, residual_mask = model(
        spectral_features(mixture_spec, echo_spec),
        enrollment,
    )
    target_spec = target_mask * local_spec
    residual_spec = residual_mask * local_spec
    other_spec = mixture_spec - echo_spec - target_spec - residual_spec
    return {
        "mixture_spec": mixture_spec,
        "target_spec": target_spec,
        "remote_echo_spec": echo_spec,
        "other_local_spec": other_spec,
        "unexplained_residual_spec": residual_spec,
    }


def apply_model(
    model: Any,
    mixture: Any,
    echo_hint: Any,
    enrollment: Any,
    window: Any,
) -> dict[str, Any]:
    spectra = predict_spectra(model, mixture, echo_hint, enrollment, window)
    target = overlap_add(spectra["target_spec"], window, mixture.shape[-1])
    remote_echo = echo_hint
    residual = overlap_add(
        spectra["unexplained_residual_spec"], window, mixture.shape[-1]
    )
    other = mixture - target - remote_echo - residual
    return {
        "target_me": target,
        "remote_echo": remote_echo,
        "other_local": other,
        "unexplained_residual": residual,
        **spectra,
    }


def mixture_normalized_loss(
    predictions: dict[str, Any],
    target_spec: Any,
    other_spec: Any,
    residual_spec: Any,
    *,
    target_weight: float,
    other_weight: float,
    residual_weight: float,
) -> tuple[Any, dict[str, float]]:
    mixture_power = predictions["mixture_spec"].abs().square().mean(dim=(1, 2)).clamp_min(1.0e-8)

    def component(estimate: Any, reference: Any) -> Any:
        error = (estimate - reference).abs().square().mean(dim=(1, 2))
        return (error / mixture_power).mean()

    target_loss = component(predictions["target_spec"], target_spec)
    other_loss = component(predictions["other_local_spec"], other_spec)
    residual_loss = component(predictions["unexplained_residual_spec"], residual_spec)
    total = (
        target_weight * target_loss
        + other_weight * other_loss
        + residual_weight * residual_loss
    )
    return total, {
        "target": float(target_loss.detach().cpu()),
        "other_local": float(other_loss.detach().cpu()),
        "residual": float(residual_loss.detach().cpu()),
        "total": float(total.detach().cpu()),
    }


def model_state_fingerprint(model: Any) -> str:
    import torch

    buffer = io.BytesIO()
    ordered = {name: value.detach().cpu() for name, value in sorted(model.state_dict().items())}
    torch.save(ordered, buffer)
    return hashlib.sha256(buffer.getvalue()).hexdigest()


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
        mask_limit=float(metadata["mask_limit"]),
    )
    model.load_state_dict(payload["state_dict"])
    return model.to(device), metadata
