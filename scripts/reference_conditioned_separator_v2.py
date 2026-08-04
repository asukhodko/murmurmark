#!/usr/bin/env python3
"""Small deterministic speaker-query separator for the bounded v2 experiment."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path
from typing import Any

import reference_conditioned_separator_v1 as V1


SAMPLE_RATE = V1.SAMPLE_RATE
CLIP_SAMPLES = V1.CLIP_SAMPLES
FRAME_SIZE = V1.FRAME_SIZE
HOP_SIZE = V1.HOP_SIZE
FREQUENCY_BINS = V1.FREQUENCY_BINS

configure_determinism = V1.configure_determinism
analysis_window = V1.analysis_window
stft = V1.stft
overlap_add = V1.overlap_add


def spectral_features(local_spec: Any, mixture_spec: Any, echo_spec: Any) -> Any:
    """Build bounded features without exposing a labelled speaker stem."""
    import torch

    local_mag = local_spec.abs().clamp_min(1.0e-7)
    mixture_mag = mixture_spec.abs().clamp_min(1.0e-7)
    echo_mag = echo_spec.abs().clamp_min(1.0e-7)
    local_scale = local_mag.clamp_min(1.0e-7)
    cross = local_spec * echo_spec.conj()
    cross_scale = (local_mag * echo_mag).clamp_min(1.0e-7)
    return torch.cat(
        (
            torch.log1p(100.0 * local_mag),
            local_spec.real / local_scale,
            local_spec.imag / local_scale,
            torch.log1p(100.0 * mixture_mag),
            torch.log1p(100.0 * echo_mag),
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
    """Build a FiLM-conditioned complex-mask extractor.

    The same local mixture is evaluated with both enrollment queries. The
    enrollment therefore modulates every frame before the recurrent stack;
    it cannot be ignored by a separate post-hoc classifier.
    """
    import torch

    class SpeakerQuerySeparator(torch.nn.Module):
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
            self.output = torch.nn.Linear(hidden_size, 2 * FREQUENCY_BINS)
            torch.nn.init.zeros_(self.output.weight)
            torch.nn.init.zeros_(self.output.bias)

        def forward(self, features: Any, enrollment: Any) -> Any:
            spectral = self.spectral_projection(features)
            scale, shift, token = self.query_projection(enrollment).chunk(3, dim=-1)
            conditioned = spectral * (1.0 + 0.5 * scale[:, None, :]) + shift[:, None, :]
            token_frames = token[:, None, :].expand(-1, conditioned.shape[1], -1)
            fused = self.query_fusion(torch.cat((conditioned, token_frames), dim=-1))
            recurrent, _ = self.recurrent(fused)
            real, imag = self.output(recurrent).chunk(2, dim=-1)
            return torch.complex(
                self.mask_limit * torch.tanh(real),
                self.mask_limit * torch.tanh(imag),
            )

    return SpeakerQuerySeparator()


def predict_spectra(
    model: Any,
    mixture: Any,
    echo: Any,
    enrollment: Any,
    window: Any,
) -> dict[str, Any]:
    mixture_spec = stft(mixture, window)
    echo_spec = stft(echo, window)
    local_spec = mixture_spec - echo_spec
    target_mask = model(spectral_features(local_spec, mixture_spec, echo_spec), enrollment)
    target_spec = target_mask * local_spec
    other_spec = mixture_spec - echo_spec - target_spec
    return {
        "mixture_spec": mixture_spec,
        "echo_spec": echo_spec,
        "local_spec": local_spec,
        "target_spec": target_spec,
        "other_spec": other_spec,
    }


def apply_model(
    model: Any,
    mixture: Any,
    echo: Any,
    enrollment: Any,
    window: Any,
) -> dict[str, Any]:
    spectra = predict_spectra(model, mixture, echo, enrollment, window)
    target = overlap_add(spectra["target_spec"], window, mixture.shape[-1])
    remote_echo = echo
    other = mixture - target - remote_echo
    return {
        "query_target": target,
        "remote_echo": remote_echo,
        "other_local": other,
        **spectra,
    }


def mixture_normalized_loss(
    predictions: dict[str, Any],
    target_spec: Any,
    other_spec: Any,
    *,
    target_weight: float,
    other_weight: float,
) -> tuple[Any, dict[str, float]]:
    mixture_power = predictions["mixture_spec"].abs().square().mean(dim=(1, 2)).clamp_min(1.0e-8)

    def component(estimate: Any, reference: Any) -> Any:
        error = (estimate - reference).abs().square().mean(dim=(1, 2))
        return (error / mixture_power).mean()

    target_loss = component(predictions["target_spec"], target_spec)
    other_loss = component(predictions["other_spec"], other_spec)
    total = target_weight * target_loss + other_weight * other_loss
    return total, {
        "target": float(target_loss.detach().cpu()),
        "other": float(other_loss.detach().cpu()),
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
