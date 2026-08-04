#!/usr/bin/env python3
"""Deterministic train/dev primitives for Speaker-Preserving Neural Echo v2."""

from __future__ import annotations

import hashlib
import json
import math
import os
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
import soundfile as sf
from scipy import linalg, signal


SAMPLE_RATE = 16_000
CLIP_SAMPLES = 4 * SAMPLE_RATE
FRAME_SIZE = 320
HOP_SIZE = 160
FREQUENCY_BINS = FRAME_SIZE // 2 + 1
EPSILON = 1.0e-8
SCRIPT_VERSION = "0.2.1"
DOUBLE_TALK_STRENGTH = 0.35
DOUBLE_TALK_HIGH_CORRELATION_THRESHOLD = 0.45
DOUBLE_TALK_HIGH_STRENGTH = 0.70
ALLOWED_DEVELOPMENT_SPLITS = frozenset({"train", "dev"})
TRAINABLE_KINDS = frozenset(
    {
        "synthetic_double_talk",
        "measured_remote_echo",
        "measured_local_target",
        "opening_backchannel",
        "keyboard_noise",
        "silence_background",
    }
)
KIND_IDS = {
    "synthetic_double_talk": 1,
    "measured_remote_echo": 2,
    "measured_local_target": 3,
    "opening_backchannel": 4,
    "keyboard_noise": 5,
    "silence_background": 6,
    "local_remote_negative": 7,
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return payload


def verify_policy_sources(repo_root: Path, policy_path: Path) -> dict[str, Any]:
    policy = read_json(policy_path)
    if policy.get("schema") != "murmurmark.speaker_preserving_neural_echo_policy/v2":
        raise RuntimeError(f"unexpected policy schema: {policy_path}")
    source = policy.get("source") if isinstance(policy.get("source"), dict) else {}
    pairs = (
        ("controlled_policy", "controlled_policy_sha256"),
        ("frozen_corpus", "frozen_corpus_sha256"),
        ("split_manifest", "split_manifest_sha256"),
        ("supervision_manifest", "supervision_manifest_sha256"),
        ("corpus_decision", "corpus_decision_sha256"),
        ("replay_report", "replay_report_sha256"),
        ("production_policy", "production_policy_sha256"),
    )
    artifacts: dict[str, Any] = {}
    for path_key, digest_key in pairs:
        path = repo_root / str(source.get(path_key) or "")
        expected = str(source.get(digest_key) or "")
        observed = sha256(path) if path.is_file() else None
        artifacts[path_key] = {
            "path": str(path),
            "expected_sha256": expected,
            "observed_sha256": observed,
            "passed": observed == expected,
        }
    decision = read_json(repo_root / str(source["corpus_decision"]))
    replay = read_json(repo_root / str(source["replay_report"]))
    semantic = {
        "corpus_ready": decision.get("decision") == "READY_FOR_ADAPTATION",
        "corpus_fingerprint": decision.get("fingerprint") == source.get("corpus_fingerprint"),
        "replay_passed": replay.get("status") == "passed",
        "replay_count": replay.get("matched_files") == source.get("replay_matched_files"),
        "hard_test_sealed": policy.get("status") == "pre_hard_locked",
    }
    passed = all(row["passed"] for row in artifacts.values()) and all(semantic.values())
    report = {
        "schema": "murmurmark.speaker_preserving_neural_echo_source_verification/v2",
        "policy": {"path": str(policy_path), "sha256": sha256(policy_path)},
        "artifacts": artifacts,
        "semantic": semantic,
        "passed": passed,
    }
    if not passed:
        raise RuntimeError("frozen policy source verification failed")
    return report


def read_manifest_rows(manifest: Path, split: str) -> list[dict[str, Any]]:
    if split not in ALLOWED_DEVELOPMENT_SPLITS:
        raise RuntimeError(
            f"split {split!r} is sealed; hard-test requires the separate locked evaluator"
        )
    rows: list[dict[str, Any]] = []
    with manifest.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise RuntimeError(f"invalid manifest row at {manifest}:{line_number}")
            if payload.get("split") == split and payload.get("kind") in TRAINABLE_KINDS:
                rows.append(payload)
    return sorted(rows, key=row_identity)


def row_identity(row: dict[str, Any]) -> str:
    return str(row.get("item_id") or row.get("clip_id") or "")


def resolve_artifact(root: Path, artifact: dict[str, Any]) -> Path:
    path = root / str(artifact["path"])
    if not path.is_file():
        raise RuntimeError(f"missing frozen artifact: {path}")
    if path.stat().st_size != int(artifact["bytes"]):
        raise RuntimeError(f"frozen artifact size changed: {path}")
    observed = sha256(path)
    if observed != str(artifact["sha256"]):
        raise RuntimeError(f"frozen artifact SHA-256 changed: {path}")
    return path


def read_artifact(root: Path, artifact: dict[str, Any]) -> np.ndarray:
    path = resolve_artifact(root, artifact)
    audio, sample_rate = sf.read(path, dtype="float32", always_2d=False)
    if sample_rate != SAMPLE_RATE:
        raise RuntimeError(f"unexpected sample rate {sample_rate}: {path}")
    values = np.asarray(audio, dtype=np.float32)
    if values.ndim != 1 or values.size != CLIP_SAMPLES:
        raise RuntimeError(f"expected mono four-second clip: {path}")
    if not np.all(np.isfinite(values)):
        raise RuntimeError(f"non-finite frozen audio: {path}")
    return values


def fit_fir(
    remote: np.ndarray,
    measured_echo: np.ndarray,
    *,
    taps: int = 1_280,
    regularization: float = 1.0e-2,
) -> np.ndarray:
    x = np.asarray(remote, dtype=np.float64) - float(np.mean(remote))
    y = np.asarray(measured_echo, dtype=np.float64) - float(np.mean(measured_echo))
    corr_xx = signal.correlate(x, x, mode="full", method="fft")
    corr_yx = signal.correlate(y, x, mode="full", method="fft")
    center = x.size - 1
    autocorrelation = corr_xx[center : center + taps].copy()
    crosscorrelation = corr_yx[center : center + taps]
    if (
        autocorrelation.size != taps
        or crosscorrelation.size != taps
        or float(autocorrelation[0]) <= EPSILON
    ):
        return np.zeros(taps, dtype=np.float64)
    autocorrelation[0] += max(float(autocorrelation[0]) * regularization, EPSILON)
    try:
        return np.asarray(
            linalg.solve_toeplitz(
                (autocorrelation, autocorrelation),
                crosscorrelation,
                check_finite=False,
            ),
            dtype=np.float64,
        )
    except Exception:
        return np.zeros(taps, dtype=np.float64)


def fir_residual(
    mixture: np.ndarray,
    remote: np.ndarray,
    measured_echo: np.ndarray,
    gain: float,
) -> tuple[np.ndarray, np.ndarray]:
    coefficients = fit_fir(remote, measured_echo)
    estimate = signal.lfilter(coefficients, [1.0], remote.astype(np.float64))
    estimate = (float(gain) * estimate).astype(np.float32)
    residual = np.asarray(mixture, dtype=np.float32) - estimate
    return residual.astype(np.float32), estimate


@dataclass(frozen=True)
class PreparedExample:
    item_id: str
    kind: str
    residual: np.ndarray
    remote: np.ndarray
    target: np.ndarray
    echo_estimate: np.ndarray
    source_fingerprint: str


def prepare_row(root: Path, row: dict[str, Any]) -> PreparedExample:
    kind = str(row["kind"])
    item_id = row_identity(row)
    if kind == "synthetic_double_talk":
        mixture = read_artifact(root, row["mixture"])
        target = read_artifact(root, row["target"])
        measured_echo = read_artifact(root, row["measured_echo"])
        remote = read_artifact(root, row["aligned_remote_reference"])
        gain = float(row["gain_linear"])
        _, full_echo_estimate = fir_residual(
            mixture,
            remote,
            measured_echo,
            gain,
        )
        before_correlation = abs(np.corrcoef(remote, mixture)[0, 1])
        strength = (
            DOUBLE_TALK_HIGH_STRENGTH
            if before_correlation >= DOUBLE_TALK_HIGH_CORRELATION_THRESHOLD
            else DOUBLE_TALK_STRENGTH
        )
        echo_estimate = strength * full_echo_estimate
        residual = mixture - echo_estimate
        artifacts = [
            row["mixture"],
            row["target"],
            row["measured_echo"],
            row["aligned_remote_reference"],
        ]
    elif kind == "measured_remote_echo":
        measured_echo = read_artifact(root, row["audio"])
        remote = read_artifact(root, row["aligned_remote_reference"])
        target = np.zeros_like(measured_echo)
        residual, echo_estimate = fir_residual(measured_echo, remote, measured_echo, 1.0)
        artifacts = [row["audio"], row["aligned_remote_reference"]]
    else:
        target = read_artifact(root, row["audio"])
        residual = target.copy()
        remote = np.zeros_like(target)
        echo_estimate = np.zeros_like(target)
        artifacts = [row["audio"]]
    fingerprint = digest_json(
        {
            "item_id": item_id,
            "kind": kind,
            "artifacts": [artifact["sha256"] for artifact in artifacts],
            "preparation": "production_preserve_local_fir_80ms_reg_1e-2_v2",
        }
    )
    return PreparedExample(
        item_id=item_id,
        kind=kind,
        residual=residual,
        remote=remote,
        target=target,
        echo_estimate=echo_estimate,
        source_fingerprint=fingerprint,
    )


def add_local_remote_negatives(
    examples: list[PreparedExample],
    rows: Sequence[dict[str, Any]],
    root: Path,
    *,
    seed: int,
) -> list[PreparedExample]:
    local = [
        example
        for example in examples
        if example.kind
        in {"measured_local_target", "opening_backchannel", "keyboard_noise", "silence_background"}
    ]
    remote_rows = [row for row in rows if row.get("kind") == "measured_remote_echo"]
    if not local or not remote_rows:
        return examples
    ordered_remote = sorted(remote_rows, key=row_identity)
    random_generator = random.Random(seed)
    offset = random_generator.randrange(len(ordered_remote))
    augmented = list(examples)
    for index, example in enumerate(local):
        row = ordered_remote[(index + offset) % len(ordered_remote)]
        remote = read_artifact(root, row["aligned_remote_reference"])
        item_id = f"negative-{example.item_id}-{row_identity(row)}"
        augmented.append(
            PreparedExample(
                item_id=item_id,
                kind="local_remote_negative",
                residual=example.target.copy(),
                remote=remote,
                target=example.target.copy(),
                echo_estimate=np.zeros_like(example.target),
                source_fingerprint=digest_json(
                    {
                        "item_id": item_id,
                        "local": example.source_fingerprint,
                        "remote": row["aligned_remote_reference"]["sha256"],
                        "augmentation": "uncorrelated_remote_without_echo_v1",
                    }
                ),
            )
        )
    return sorted(augmented, key=lambda example: (example.kind, example.item_id))


def prepare_cache(
    *,
    corpus_root: Path,
    manifest: Path,
    split: str,
    output_dir: Path,
    seed: int,
) -> dict[str, Any]:
    rows = read_manifest_rows(manifest, split)
    examples = [prepare_row(corpus_root, row) for row in rows]
    examples = add_local_remote_negatives(examples, rows, corpus_root, seed=seed)
    output_dir.mkdir(parents=True, exist_ok=True)
    waveforms_path = output_dir / f"{split}_waveforms.npy"
    kinds_path = output_dir / f"{split}_kinds.npy"
    shape = (len(examples), 4, CLIP_SAMPLES)
    waveforms = np.lib.format.open_memmap(
        waveforms_path,
        mode="w+",
        dtype=np.float16,
        shape=shape,
    )
    kinds = np.lib.format.open_memmap(
        kinds_path,
        mode="w+",
        dtype=np.uint8,
        shape=(len(examples),),
    )
    index_rows: list[dict[str, Any]] = []
    for index, example in enumerate(examples):
        waveforms[index, 0] = example.residual.astype(np.float16)
        waveforms[index, 1] = example.remote.astype(np.float16)
        waveforms[index, 2] = example.target.astype(np.float16)
        waveforms[index, 3] = example.echo_estimate.astype(np.float16)
        kinds[index] = KIND_IDS[example.kind]
        index_rows.append(
            {
                "index": index,
                "item_id": example.item_id,
                "kind": example.kind,
                "source_fingerprint": example.source_fingerprint,
            }
        )
    waveforms.flush()
    kinds.flush()
    index_path = output_dir / f"{split}_index.json"
    write_json(index_path, {"schema": "murmurmark.neural_echo_cache_index/v1", "rows": index_rows})
    payload = {
        "schema": "murmurmark.neural_echo_cache/v1",
        "generator": {"name": Path(__file__).name, "version": SCRIPT_VERSION},
        "split": split,
        "seed": seed,
        "manifest": {
            "path": str(manifest),
            "sha256": sha256(manifest),
        },
        "examples": len(examples),
        "shape": list(shape),
        "dtype": "float16",
        "kind_counts": {
            kind: sum(1 for example in examples if example.kind == kind)
            for kind in sorted(KIND_IDS)
        },
        "artifacts": {
            "waveforms": {"path": str(waveforms_path), "sha256": sha256(waveforms_path)},
            "kinds": {"path": str(kinds_path), "sha256": sha256(kinds_path)},
            "index": {"path": str(index_path), "sha256": sha256(index_path)},
        },
        "fingerprint": digest_json([example.source_fingerprint for example in examples]),
    }
    write_json(output_dir / f"{split}_cache_manifest.json", payload)
    return payload


def configure_determinism(seed: int) -> None:
    os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
    random.seed(seed)
    np.random.seed(seed)
    import torch

    torch.manual_seed(seed)
    if torch.backends.mps.is_available():
        torch.mps.manual_seed(seed)
    torch.use_deterministic_algorithms(True)
    torch.set_num_threads(max(1, min(8, os.cpu_count() or 1)))


def analysis_window() -> np.ndarray:
    return np.sqrt(np.hanning(FRAME_SIZE + 1)[:-1]).astype(np.float32)


def frame_audio(values: "Any") -> "Any":
    import torch

    left = HOP_SIZE
    frame_count = max(1, math.ceil((values.shape[-1] + left - FRAME_SIZE) / HOP_SIZE) + 1)
    total = FRAME_SIZE + (frame_count - 1) * HOP_SIZE
    right = max(0, total - (values.shape[-1] + left))
    padded = torch.nn.functional.pad(values, (left, right))
    return padded.unfold(-1, FRAME_SIZE, HOP_SIZE)


def stft(values: "Any", window: "Any") -> "Any":
    import torch

    return torch.fft.rfft(frame_audio(values) * window, n=FRAME_SIZE, dim=-1)


def overlap_add(spectrum: "Any", window: "Any", output_samples: int) -> "Any":
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


def spectral_features(residual_spec: "Any", remote_spec: "Any") -> "Any":
    import torch

    residual_mag = residual_spec.abs().clamp_min(1.0e-7)
    remote_mag = remote_spec.abs().clamp_min(1.0e-7)
    cross = residual_spec * remote_spec.conj()
    cross_scale = (residual_mag * remote_mag).clamp_min(1.0e-7)
    features = torch.cat(
        (
            torch.log1p(100.0 * residual_mag),
            torch.log1p(100.0 * remote_mag),
            cross.real / cross_scale,
            cross.imag / cross_scale,
        ),
        dim=-1,
    )
    return features


def remote_gate(remote_spec: "Any") -> "Any":
    import torch

    frame_rms = torch.sqrt(torch.mean(remote_spec.abs().square(), dim=-1, keepdim=True) + 1.0e-12)
    threshold = 10.0 ** (-58.0 / 20.0)
    soft = torch.sigmoid((torch.log(frame_rms + 1.0e-12) - math.log(threshold)) / 0.8)
    hard = (frame_rms >= threshold).to(remote_spec.real.dtype)
    return soft * hard


def build_model(family: str, hidden_size: int, layers: int) -> "Any":
    import torch

    output_size = FREQUENCY_BINS if family == "magnitude_mask" else 2 * FREQUENCY_BINS

    class ResidualMaskModel(torch.nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.family = family
            self.projection = torch.nn.Sequential(
                torch.nn.Linear(4 * FREQUENCY_BINS, hidden_size),
                torch.nn.LayerNorm(hidden_size),
                torch.nn.SiLU(),
            )
            self.recurrent = torch.nn.GRU(
                hidden_size,
                hidden_size,
                num_layers=layers,
                batch_first=True,
            )
            self.output = torch.nn.Linear(hidden_size, output_size)
            torch.nn.init.zeros_(self.output.weight)
            torch.nn.init.zeros_(self.output.bias)

        def forward(self, features: "Any", gate: "Any") -> "Any":
            encoded = self.projection(features)
            recurrent, _ = self.recurrent(encoded)
            values = self.output(recurrent)
            if self.family == "magnitude_mask":
                learned = 1.05 * torch.sigmoid(values + 3.0)
                return 1.0 + gate * (learned - 1.0)
            real, imaginary = values.chunk(2, dim=-1)
            if self.family == "echo_mapper":
                return torch.complex(
                    gate * 2.0 * torch.tanh(real),
                    gate * 2.0 * torch.tanh(imaginary),
                )
            return torch.complex(
                1.0 + gate * 2.0 * torch.tanh(real),
                gate * 2.0 * torch.tanh(imaginary),
            )

    return ResidualMaskModel()


def apply_model(
    model: "Any",
    residual: "Any",
    remote: "Any",
    window: "Any",
    echo_estimate: "Any | None" = None,
) -> tuple["Any", dict[str, "Any"]]:
    residual_spec = stft(residual, window)
    remote_spec = stft(remote, window)
    gate = remote_gate(remote_spec)
    if getattr(model, "family", "") == "echo_mapper":
        if echo_estimate is None:
            raise RuntimeError("echo_mapper requires the FIR echo estimate")
        echo_spec = stft(echo_estimate, window)
        values = model(spectral_features(echo_spec, remote_spec), gate)
        enhanced_spec = residual_spec - values * echo_spec
    else:
        echo_spec = None
        values = model(spectral_features(residual_spec, remote_spec), gate)
        enhanced_spec = values * residual_spec
    enhanced = overlap_add(enhanced_spec, window, residual.shape[-1])
    active = torch_any_remote_active(gate)
    enhanced = np_where_tensor(active, enhanced, residual)
    return enhanced, {
        "enhanced_spec": enhanced_spec,
        "residual_spec": residual_spec,
        "remote_spec": remote_spec,
        "echo_spec": echo_spec,
        "gate": gate,
        "model_output": values,
    }


def torch_is_complex(value: "Any") -> bool:
    import torch

    return torch.is_complex(value)


def torch_any_remote_active(gate: "Any") -> "Any":
    return (gate.amax(dim=(1, 2)) > 0.0)[:, None]


def np_where_tensor(condition: "Any", when_true: "Any", when_false: "Any") -> "Any":
    import torch

    return torch.where(condition, when_true, when_false)


def snr_db(target: np.ndarray, candidate: np.ndarray) -> float:
    target64 = np.asarray(target, dtype=np.float64)
    error64 = np.asarray(candidate, dtype=np.float64) - target64
    return 10.0 * math.log10(
        (float(np.mean(target64**2)) + 1.0e-12)
        / (float(np.mean(error64**2)) + 1.0e-12)
    )


def rms_db(values: np.ndarray) -> float:
    return 10.0 * math.log10(float(np.mean(np.asarray(values, dtype=np.float64) ** 2)) + 1.0e-12)


def correlation(left: np.ndarray, right: np.ndarray) -> float:
    a = np.asarray(left, dtype=np.float64)
    b = np.asarray(right, dtype=np.float64)
    if float(np.std(a)) <= 1.0e-12 or float(np.std(b)) <= 1.0e-12:
        return 1.0 if np.array_equal(a, b) else 0.0
    return float(np.corrcoef(a, b)[0, 1])


def percentile_summary(values: Iterable[float]) -> dict[str, float | int | None]:
    finite = np.asarray([value for value in values if math.isfinite(value)], dtype=np.float64)
    if not finite.size:
        return {"count": 0, "min": None, "p10": None, "p50": None, "p90": None}
    return {
        "count": int(finite.size),
        "min": round(float(np.min(finite)), 6),
        "p10": round(float(np.percentile(finite, 10)), 6),
        "p50": round(float(np.percentile(finite, 50)), 6),
        "p90": round(float(np.percentile(finite, 90)), 6),
    }


def checkpoint_payload(model: "Any", metadata: dict[str, Any]) -> dict[str, Any]:
    return {"state_dict": model.state_dict(), "metadata": metadata}


def save_checkpoint(path: Path, model: "Any", metadata: dict[str, Any]) -> None:
    import torch

    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(checkpoint_payload(model, metadata), path)


def load_checkpoint(path: Path, *, device: str = "cpu") -> tuple["Any", dict[str, Any]]:
    import torch

    payload = torch.load(path, map_location=device, weights_only=False)
    metadata = payload["metadata"]
    model = build_model(
        str(metadata["family"]),
        int(metadata["hidden_size"]),
        int(metadata["layers"]),
    )
    model.load_state_dict(payload["state_dict"])
    return model.to(device), metadata
