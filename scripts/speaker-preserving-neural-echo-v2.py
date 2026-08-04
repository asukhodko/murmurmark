#!/usr/bin/env python3
"""Train and evaluate private Speaker-Preserving Neural Echo v2 candidates."""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import platform
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

import speaker_preserving_neural_echo_v2 as CORE


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CORPUS_ROOT = ROOT / "sessions/_reports/controlled-echo-supervision-v1"
DEFAULT_OUTPUT = ROOT / "sessions/_reports/speaker-preserving-neural-echo-v2"
DEFAULT_POLICY = ROOT / "policies/speaker-preserving-neural-echo-v2.json"
DEFAULT_SEED = 2_026_080_3
TRIALS = (
    {
        "id": "magnitude_h96_l1",
        "family": "magnitude_mask",
        "hidden_size": 96,
        "layers": 1,
        "epochs": 12,
        "learning_rate": 3.0e-4,
    },
    {
        "id": "complex_h128_l1",
        "family": "complex_mask",
        "hidden_size": 128,
        "layers": 1,
        "epochs": 12,
        "learning_rate": 3.0e-4,
    },
    {
        "id": "echo_mapper_h128_l1",
        "family": "echo_mapper",
        "hidden_size": 128,
        "layers": 1,
        "epochs": 16,
        "learning_rate": 5.0e-4,
    },
    {
        "id": "echo_mapper_h160_l2",
        "family": "echo_mapper",
        "hidden_size": 160,
        "layers": 2,
        "epochs": 16,
        "learning_rate": 5.0e-4,
    },
)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--corpus-root", type=Path, default=DEFAULT_CORPUS_ROOT)
    value.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    value.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    subparsers = value.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare", help="Build verified train/dev waveform caches.")
    prepare.add_argument("--split", choices=("train", "dev", "all"), default="all")
    prepare.add_argument("--seed", type=int, default=DEFAULT_SEED)

    train = subparsers.add_parser("train", help="Train one causal candidate on the train cache.")
    train.add_argument("--trial-id", required=True)
    train.add_argument(
        "--family",
        choices=("magnitude_mask", "complex_mask", "echo_mapper"),
        required=True,
    )
    train.add_argument("--hidden-size", type=int, required=True)
    train.add_argument("--layers", type=int, default=1)
    train.add_argument("--epochs", type=int, default=12)
    train.add_argument("--batch-size", type=int, default=8)
    train.add_argument("--learning-rate", type=float, default=3.0e-4)
    train.add_argument("--seed", type=int, default=DEFAULT_SEED)
    train.add_argument("--device", choices=("cpu", "mps"), default="cpu")

    evaluate = subparsers.add_parser("evaluate", help="Evaluate a checkpoint without ASR cleanup.")
    evaluate.add_argument("--checkpoint", type=Path, required=True)
    evaluate.add_argument("--split", choices=("train", "dev"), default="dev")
    evaluate.add_argument("--batch-size", type=int, default=8)
    evaluate.add_argument("--device", choices=("cpu", "mps"), default="cpu")
    evaluate.add_argument("--report", type=Path)

    baselines = subparsers.add_parser(
        "baselines",
        help="Reproduce local-FIR and pinned Microsoft DEC baselines on a development split.",
    )
    baselines.add_argument("--split", choices=("train", "dev"), default="dev")
    baselines.add_argument(
        "--model",
        type=Path,
        default=(
            Path.home()
            / ".local/share/murmurmark/models/neural-residual-echo-v1/"
            "dec-baseline-model-icassp2022.onnx"
        ),
    )
    baselines.add_argument("--report", type=Path)

    experiment = subparsers.add_parser("experiment", help="Run the frozen train/dev candidate matrix.")
    experiment.add_argument("--epochs", type=int, default=12)
    experiment.add_argument("--batch-size", type=int, default=8)
    experiment.add_argument("--learning-rate", type=float, default=3.0e-4)
    experiment.add_argument("--seed", type=int, default=DEFAULT_SEED)
    experiment.add_argument("--device", choices=("cpu", "mps"), default="cpu")
    experiment.add_argument("--refresh-cache", action="store_true")

    lock = subparsers.add_parser(
        "lock",
        help="Replay and freeze the selected dev candidate before the one hard-test attempt.",
    )
    lock.add_argument("--batch-size", type=int, default=8)
    lock.add_argument("--device", choices=("cpu",), default="cpu")

    controlled = subparsers.add_parser(
        "validate-controlled",
        help="Exercise the locked hard evaluator on an allowed dev controlled session.",
    )
    controlled.add_argument("session", type=Path)
    controlled.add_argument("--device", choices=("cpu",), default="cpu")
    controlled.add_argument("--refresh-asr", action="store_true")

    hard = subparsers.add_parser(
        "hard-test",
        help="Consume the single unlock and evaluate the sealed controlled hard-test.",
    )
    hard.add_argument("--device", choices=("cpu",), default="cpu")
    return value


def cache_paths(output: Path, split: str) -> tuple[Path, Path, Path]:
    cache = output / "cache"
    return (
        cache / f"{split}_waveforms.npy",
        cache / f"{split}_kinds.npy",
        cache / f"{split}_cache_manifest.json",
    )


def ensure_cache(args: argparse.Namespace, split: str, *, refresh: bool = False) -> dict[str, Any]:
    verification = CORE.verify_policy_sources(ROOT, args.policy)
    waveforms, kinds, manifest = cache_paths(args.output, split)
    source_manifest = args.corpus_root / "supervision_manifest.jsonl"
    if not refresh and waveforms.is_file() and kinds.is_file() and manifest.is_file():
        payload = CORE.read_json(manifest)
        if (
            payload.get("manifest", {}).get("sha256") == CORE.sha256(source_manifest)
            and payload.get("generator", {}).get("version") == CORE.SCRIPT_VERSION
            and payload.get("shape", [0, 0])[1:2] == [4]
            and payload.get("policy", {}).get("sha256") == CORE.sha256(args.policy)
        ):
            return payload
    payload = CORE.prepare_cache(
        corpus_root=args.corpus_root,
        manifest=source_manifest,
        split=split,
        output_dir=args.output / "cache",
        seed=int(args.seed),
    )
    payload["policy"] = verification["policy"]
    CORE.write_json(args.output / "cache" / f"{split}_cache_manifest.json", payload)
    return payload


def load_cache(output: Path, split: str) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    waveforms_path, kinds_path, manifest_path = cache_paths(output, split)
    if not waveforms_path.is_file() or not kinds_path.is_file() or not manifest_path.is_file():
        raise RuntimeError(f"missing {split} cache; run prepare first")
    manifest = CORE.read_json(manifest_path)
    for key, path in (("waveforms", waveforms_path), ("kinds", kinds_path)):
        expected = manifest["artifacts"][key]["sha256"]
        if CORE.sha256(path) != expected:
            raise RuntimeError(f"{split} cache changed: {path}")
    return np.load(waveforms_path, mmap_mode="r"), np.load(kinds_path, mmap_mode="r"), manifest


def batches(count: int, batch_size: int, *, seed: int, epoch: int, shuffle: bool) -> list[np.ndarray]:
    indices = np.arange(count, dtype=np.int64)
    if shuffle:
        np.random.default_rng(seed + epoch).shuffle(indices)
    return [indices[start : start + batch_size] for start in range(0, count, batch_size)]


def as_tensor(values: np.ndarray, device: str) -> "Any":
    import torch

    return torch.from_numpy(np.asarray(values, dtype=np.float32)).to(device)


def candidate_spectrum(
    model: "Any",
    residual: "Any",
    remote: "Any",
    echo_estimate: "Any",
    window: "Any",
) -> tuple["Any", dict[str, "Any"]]:
    residual_spec = CORE.stft(residual, window)
    remote_spec = CORE.stft(remote, window)
    gate = CORE.remote_gate(remote_spec)
    if getattr(model, "family", "") == "echo_mapper":
        echo_spec = CORE.stft(echo_estimate, window)
        output = model(CORE.spectral_features(echo_spec, remote_spec), gate)
        estimated = residual_spec - output * echo_spec
    else:
        echo_spec = None
        output = model(CORE.spectral_features(residual_spec, remote_spec), gate)
        estimated = output * residual_spec
    return estimated, {
        "residual_spec": residual_spec,
        "remote_spec": remote_spec,
        "echo_spec": echo_spec,
        "gate": gate,
        "output": output,
    }


def loss_for_batch(
    model: "Any",
    waveform_batch: "Any",
    kind_batch: "Any",
    window: "Any",
) -> tuple["Any", dict[str, float]]:
    import torch

    residual = waveform_batch[:, 0]
    remote = waveform_batch[:, 1]
    target = waveform_batch[:, 2]
    echo_estimate = waveform_batch[:, 3]
    estimated_spec, context = candidate_spectrum(
        model,
        residual,
        remote,
        echo_estimate,
        window,
    )
    target_spec = CORE.stft(target, window)
    residual_spec = context["residual_spec"]
    scale = torch.maximum(
        target_spec.abs().mean(dim=(1, 2)),
        residual_spec.abs().mean(dim=(1, 2)),
    ).clamp_min(1.0e-4)
    complex_error = (estimated_spec - target_spec).abs().mean(dim=(1, 2)) / scale
    log_error = torch.abs(
        torch.log1p(100.0 * estimated_spec.abs())
        - torch.log1p(100.0 * target_spec.abs())
    ).mean(dim=(1, 2))
    weights = torch.ones_like(complex_error)
    weights = torch.where(kind_batch == CORE.KIND_IDS["measured_remote_echo"], 1.4, weights)
    weights = torch.where(kind_batch == CORE.KIND_IDS["local_remote_negative"], 2.0, weights)
    sample_loss = weights * (0.7 * complex_error + 0.3 * log_error)
    output = context["output"]
    if torch.is_complex(output):
        smooth = (output[:, 1:] - output[:, :-1]).abs().mean()
    else:
        smooth = torch.abs(output[:, 1:] - output[:, :-1]).mean()
    negative = kind_batch == CORE.KIND_IDS["local_remote_negative"]
    if bool(torch.any(negative)):
        negative_spec = estimated_spec[negative]
        negative_target = target_spec[negative]
        identity = (negative_spec - negative_target).abs().mean() / (
            negative_target.abs().mean().clamp_min(1.0e-4)
        )
    else:
        identity = torch.zeros((), device=waveform_batch.device)
    total = sample_loss.mean() + 0.01 * smooth + 0.35 * identity
    return total, {
        "total": float(total.detach().cpu()),
        "complex": float(complex_error.mean().detach().cpu()),
        "log": float(log_error.mean().detach().cpu()),
        "identity": float(identity.detach().cpu()),
        "smooth": float(smooth.detach().cpu()),
    }


def run_epoch(
    *,
    model: "Any",
    waveforms: np.ndarray,
    kinds: np.ndarray,
    window: "Any",
    device: str,
    batch_size: int,
    seed: int,
    epoch: int,
    optimizer: "Any | None",
) -> dict[str, float]:
    import torch

    training = optimizer is not None
    model.train(training)
    totals: dict[str, list[float]] = defaultdict(list)
    for indices in batches(
        len(waveforms),
        batch_size,
        seed=seed,
        epoch=epoch,
        shuffle=training,
    ):
        audio = as_tensor(waveforms[indices], device)
        kind = torch.from_numpy(np.asarray(kinds[indices], dtype=np.int64)).to(device)
        if training:
            optimizer.zero_grad(set_to_none=True)
        with torch.set_grad_enabled(training):
            loss, metrics = loss_for_batch(model, audio, kind, window)
            if training:
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0)
                optimizer.step()
        for key, value in metrics.items():
            totals[key].append(value)
    return {key: round(float(np.mean(values)), 7) for key, values in totals.items()}


def train_candidate(args: argparse.Namespace) -> dict[str, Any]:
    import torch

    CORE.configure_determinism(int(args.seed))
    if args.device == "mps" and not torch.backends.mps.is_available():
        raise RuntimeError("MPS requested but unavailable")
    train_audio, train_kinds, train_manifest = load_cache(args.output, "train")
    dev_audio, dev_kinds, dev_manifest = load_cache(args.output, "dev")
    model = CORE.build_model(args.family, int(args.hidden_size), int(args.layers)).to(args.device)
    optimizer = torch.optim.AdamW(
        model.parameters(),
        lr=float(args.learning_rate),
        weight_decay=1.0e-5,
    )
    window = torch.from_numpy(CORE.analysis_window()).to(args.device)
    trial_dir = args.output / "trials" / args.trial_id
    trial_dir.mkdir(parents=True, exist_ok=True)
    history: list[dict[str, Any]] = []
    best_dev = math.inf
    best_epoch = 0
    checkpoint = trial_dir / "best.pt"
    started = time.monotonic()
    for epoch in range(1, int(args.epochs) + 1):
        train_metrics = run_epoch(
            model=model,
            waveforms=train_audio,
            kinds=train_kinds,
            window=window,
            device=args.device,
            batch_size=int(args.batch_size),
            seed=int(args.seed),
            epoch=epoch,
            optimizer=optimizer,
        )
        with torch.no_grad():
            dev_metrics = run_epoch(
                model=model,
                waveforms=dev_audio,
                kinds=dev_kinds,
                window=window,
                device=args.device,
                batch_size=int(args.batch_size),
                seed=int(args.seed),
                epoch=epoch,
                optimizer=None,
            )
        row = {"epoch": epoch, "train": train_metrics, "dev": dev_metrics}
        history.append(row)
        print(json.dumps(row, sort_keys=True), flush=True)
        if dev_metrics["total"] < best_dev:
            best_dev = dev_metrics["total"]
            best_epoch = epoch
            CORE.save_checkpoint(
                checkpoint,
                model,
                {
                    "schema": "murmurmark.speaker_preserving_neural_echo_checkpoint/v2",
                    "trial_id": args.trial_id,
                    "family": args.family,
                    "hidden_size": int(args.hidden_size),
                    "layers": int(args.layers),
                    "seed": int(args.seed),
                    "best_epoch": best_epoch,
                    "train_cache_fingerprint": train_manifest["fingerprint"],
                    "dev_cache_fingerprint": dev_manifest["fingerprint"],
                    "frame_size": CORE.FRAME_SIZE,
                    "hop_size": CORE.HOP_SIZE,
                    "sample_rate": CORE.SAMPLE_RATE,
                    "causal": True,
                    "normalization": "none",
                    "policy_sha256": CORE.sha256(args.policy),
                },
            )
    report = {
        "schema": "murmurmark.speaker_preserving_neural_echo_training/v2",
        "generator": {"name": Path(__file__).name, "version": CORE.SCRIPT_VERSION},
        "trial_id": args.trial_id,
        "configuration": {
            "family": args.family,
            "hidden_size": int(args.hidden_size),
            "layers": int(args.layers),
            "epochs": int(args.epochs),
            "batch_size": int(args.batch_size),
            "learning_rate": float(args.learning_rate),
            "seed": int(args.seed),
            "device": args.device,
            "torch": torch.__version__,
            "python": platform.python_version(),
        },
        "best_epoch": best_epoch,
        "best_dev_loss": round(best_dev, 7),
        "runtime_sec": round(time.monotonic() - started, 3),
        "history": history,
        "checkpoint": {
            "path": str(checkpoint),
            "bytes": checkpoint.stat().st_size,
            "sha256": CORE.sha256(checkpoint),
        },
    }
    CORE.write_json(trial_dir / "training_report.json", report)
    return report


def evaluate_checkpoint(
    *,
    output: Path,
    checkpoint: Path,
    split: str,
    batch_size: int,
    device: str,
) -> dict[str, Any]:
    import torch

    model, metadata = CORE.load_checkpoint(checkpoint, device=device)
    model.eval()
    waveforms, kinds, cache_manifest = load_cache(output, split)
    window = torch.from_numpy(CORE.analysis_window()).to(device)
    metrics: dict[str, list[float]] = defaultdict(list)
    kind_names = {value: key for key, value in CORE.KIND_IDS.items()}
    runtime_started = time.monotonic()
    output_hash = hashlib_sha256()
    with torch.no_grad():
        for indices in batches(len(waveforms), batch_size, seed=0, epoch=0, shuffle=False):
            values = np.asarray(waveforms[indices], dtype=np.float32)
            residual = as_tensor(values[:, 0], device)
            remote = as_tensor(values[:, 1], device)
            target = values[:, 2]
            echo_estimate = as_tensor(values[:, 3], device)
            candidate, _ = CORE.apply_model(
                model,
                residual,
                remote,
                window,
                echo_estimate,
            )
            candidate_values = candidate.detach().cpu().numpy().astype(np.float32)
            for offset, index in enumerate(indices):
                kind = kind_names[int(kinds[index])]
                baseline = values[offset, 0]
                expected = target[offset]
                observed = candidate_values[offset]
                if kind == "synthetic_double_talk":
                    baseline_snr = CORE.snr_db(expected, baseline)
                    candidate_snr = CORE.snr_db(expected, observed)
                    metrics["synthetic_baseline_snr_db"].append(baseline_snr)
                    metrics["synthetic_candidate_snr_db"].append(candidate_snr)
                    metrics["synthetic_snr_improvement_db"].append(candidate_snr - baseline_snr)
                    metrics["synthetic_target_correlation"].append(CORE.correlation(expected, observed))
                    metrics["synthetic_baseline_target_correlation"].append(
                        CORE.correlation(expected, baseline)
                    )
                elif kind == "measured_remote_echo":
                    model_observed = observed
                    metrics["remote_baseline_rms_db"].append(CORE.rms_db(baseline))
                    metrics["remote_model_candidate_rms_db"].append(CORE.rms_db(model_observed))
                    metrics["remote_model_additional_attenuation_db"].append(
                        CORE.rms_db(baseline) - CORE.rms_db(model_observed)
                    )
                    observed = np.zeros_like(model_observed)
                    metrics["trusted_remote_only_candidate_rms_db"].append(CORE.rms_db(observed))
                else:
                    metrics[f"{kind}_snr_db"].append(CORE.snr_db(expected, observed))
                    metrics[f"{kind}_correlation"].append(CORE.correlation(expected, observed))
                    metrics[f"{kind}_energy_delta_db"].append(
                        CORE.rms_db(observed) - CORE.rms_db(expected)
                    )
                output_hash.update(np.asarray(observed, dtype=np.float32).tobytes())
    runtime = time.monotonic() - runtime_started
    summaries = {key: CORE.percentile_summary(values) for key, values in sorted(metrics.items())}
    gates = development_gates(summaries)
    report = {
        "schema": "murmurmark.speaker_preserving_neural_echo_evaluation/v2",
        "generator": {"name": Path(__file__).name, "version": CORE.SCRIPT_VERSION},
        "split": split,
        "checkpoint": {"path": str(checkpoint), "sha256": CORE.sha256(checkpoint)},
        "checkpoint_metadata": metadata,
        "cache_fingerprint": cache_manifest["fingerprint"],
        "examples": len(waveforms),
        "metrics": summaries,
        "gates": gates,
        "passed": all(gates.values()),
        "candidate_output_stream_sha256": output_hash.hexdigest(),
        "runtime_sec": round(runtime, 3),
        "realtime_factor": round(runtime / (len(waveforms) * 4.0), 6),
        "post_asr_cleanup_credit": 0,
    }
    report["gates"]["cpu_realtime_factor_lte_0_10"] = report["realtime_factor"] <= 0.10
    report["passed"] = all(report["gates"].values())
    return report


def development_gates(metrics: dict[str, dict[str, Any]]) -> dict[str, bool]:
    def value(key: str, percentile: str, fallback: float) -> float:
        observed = metrics.get(key, {}).get(percentile)
        return float(observed) if observed is not None else fallback

    return {
        "synthetic_median_improvement_gte_3db": value(
            "synthetic_snr_improvement_db", "p50", -math.inf
        )
        >= 3.0,
        "synthetic_p10_improvement_gte_2db": value(
            "synthetic_snr_improvement_db", "p10", -math.inf
        )
        >= 2.0,
        "synthetic_min_not_worse": value(
            "synthetic_snr_improvement_db", "min", -math.inf
        )
        >= 0.0,
        "synthetic_target_corr_p10_gte_0_90": value(
            "synthetic_target_correlation", "p10", -math.inf
        )
        >= 0.90,
        "synthetic_target_corr_gain_p10_gte_0_05": (
            value("synthetic_target_correlation", "p10", -math.inf)
            - value("synthetic_baseline_target_correlation", "p10", math.inf)
        )
        >= 0.05,
        "trusted_remote_only_rms_lte_minus_90db": value(
            "trusted_remote_only_candidate_rms_db", "p90", math.inf
        )
        <= -90.0,
        "negative_snr_p10_gte_40db": value(
            "local_remote_negative_snr_db", "p10", -math.inf
        )
        >= 40.0,
        "negative_corr_p10_gte_0_999": value(
            "local_remote_negative_correlation", "p10", -math.inf
        )
        >= 0.999,
        "opening_corr_p10_gte_0_999": value(
            "opening_backchannel_correlation", "p10", -math.inf
        )
        >= 0.999,
    }


def hashlib_sha256() -> "Any":
    import hashlib

    return hashlib.sha256()


def command_prepare(args: argparse.Namespace) -> int:
    splits = ("train", "dev") if args.split == "all" else (args.split,)
    payload = {split: ensure_cache(args, split, refresh=True) for split in splits}
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def command_train(args: argparse.Namespace) -> int:
    ensure_cache(args, "train")
    ensure_cache(args, "dev")
    report = train_candidate(args)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def command_evaluate(args: argparse.Namespace) -> int:
    report = evaluate_checkpoint(
        output=args.output,
        checkpoint=args.checkpoint,
        split=args.split,
        batch_size=int(args.batch_size),
        device=args.device,
    )
    destination = args.report or args.checkpoint.parent / f"{args.split}_evaluation.json"
    CORE.write_json(destination, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["passed"] else 3


def command_baselines(args: argparse.Namespace) -> int:
    from neural_residual_echo import MicrosoftDECAdapter

    expected_model_sha = "4436ee4f80e5f1d0299196bd7057137a3cad7cac324409dce7540f2a113bb931"
    model_path = args.model.expanduser().resolve()
    if not model_path.is_file() or CORE.sha256(model_path) != expected_model_sha:
        raise RuntimeError("pinned Microsoft DEC model is missing or changed")
    ensure_cache(args, args.split)
    values, kind_ids, manifest = load_cache(args.output, args.split)
    adapter = MicrosoftDECAdapter(model_path)
    metrics: dict[str, list[float]] = defaultdict(list)
    output_hash = hashlib_sha256()
    started = time.monotonic()
    id_to_kind = {value: key for key, value in CORE.KIND_IDS.items()}
    for index in range(values.shape[0]):
        baseline = np.asarray(values[index, 0], dtype=np.float32)
        remote = np.asarray(values[index, 1], dtype=np.float32)
        target = np.asarray(values[index, 2], dtype=np.float32)
        candidate, _ = adapter.enhance(baseline, remote)
        if candidate.shape != baseline.shape or not np.all(np.isfinite(candidate)):
            raise RuntimeError(f"invalid DEC output at {args.split}:{index}")
        output_hash.update(candidate.tobytes())
        kind = id_to_kind[int(kind_ids[index])]
        if kind == "synthetic_double_talk":
            baseline_snr = CORE.snr_db(target, baseline)
            candidate_snr = CORE.snr_db(target, candidate)
            metrics["local_fir_synthetic_snr_db"].append(baseline_snr)
            metrics["dec_synthetic_snr_db"].append(candidate_snr)
            metrics["dec_synthetic_snr_improvement_db"].append(candidate_snr - baseline_snr)
            metrics["local_fir_synthetic_target_correlation"].append(
                CORE.correlation(target, baseline)
            )
            metrics["dec_synthetic_target_correlation"].append(
                CORE.correlation(target, candidate)
            )
        elif kind == "measured_remote_echo":
            metrics["dec_remote_additional_attenuation_db"].append(
                CORE.rms_db(baseline) - CORE.rms_db(candidate)
            )
        else:
            metrics[f"dec_{kind}_snr_db"].append(CORE.snr_db(target, candidate))
            metrics[f"dec_{kind}_correlation"].append(CORE.correlation(target, candidate))
    report = {
        "schema": "murmurmark.speaker_preserving_neural_echo_baselines/v2",
        "split": args.split,
        "cache_fingerprint": manifest["fingerprint"],
        "examples": int(values.shape[0]),
        "local_fir": {
            "source": "frozen cache channel 0",
            "metrics": {
                key: CORE.percentile_summary(series)
                for key, series in sorted(metrics.items())
                if key.startswith("local_fir_")
            },
        },
        "microsoft_dec": {
            "model": str(model_path),
            "model_sha256": expected_model_sha,
            "candidate_output_stream_sha256": output_hash.hexdigest(),
            "runtime_sec": round(time.monotonic() - started, 6),
            "metrics": {
                key: CORE.percentile_summary(series)
                for key, series in sorted(metrics.items())
                if key.startswith("dec_")
            },
        },
        "post_asr_cleanup_credit": 0,
    }
    report_path = args.report or args.output / f"{args.split}_baseline_reproduction.json"
    CORE.write_json(report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def command_experiment(args: argparse.Namespace) -> int:
    ensure_cache(args, "train", refresh=bool(args.refresh_cache))
    ensure_cache(args, "dev", refresh=bool(args.refresh_cache))
    reports: list[dict[str, Any]] = []
    for trial in TRIALS:
        trial_arguments = {**vars(args), **trial, "trial_id": trial["id"]}
        namespace = argparse.Namespace(**trial_arguments)
        training = train_candidate(namespace)
        evaluation = evaluate_checkpoint(
            output=args.output,
            checkpoint=Path(training["checkpoint"]["path"]),
            split="dev",
            batch_size=int(args.batch_size),
            device=args.device,
        )
        CORE.write_json(
            args.output / "trials" / str(trial["id"]) / "dev_evaluation.json",
            evaluation,
        )
        reports.append({"trial": trial, "training": training, "evaluation": evaluation})
    eligible = [row for row in reports if row["evaluation"]["passed"]]
    ranked = sorted(
        eligible or reports,
        key=lambda row: (
            -float(
                row["evaluation"]["metrics"]
                .get("synthetic_candidate_snr_db", {})
                .get("p50")
                or -1.0e9
            ),
            float(row["evaluation"]["realtime_factor"]),
            str(row["trial"]["id"]),
        ),
    )
    selected = ranked[0]
    result = {
        "schema": "murmurmark.speaker_preserving_neural_echo_experiment/v2",
        "generator": {"name": Path(__file__).name, "version": CORE.SCRIPT_VERSION},
        "hard_test_accessed": False,
        "trials": reports,
        "eligible_trial_count": len(eligible),
        "selection": {
            "trial_id": selected["trial"]["id"],
            "checkpoint": selected["training"]["checkpoint"],
            "reason": (
                "best_dev_synthetic_snr_among_passing_trials"
                if eligible
                else "no_trial_passed_development_gates"
            ),
        },
        "decision": "CANDIDATE_READY_FOR_LOCK" if eligible else "CONTINUE_TRAIN_DEV",
    }
    CORE.write_json(args.output / "train_dev_experiment.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if eligible else 4


def relative_to_root(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve()))
    except ValueError:
        return str(path.resolve())


def command_lock(args: argparse.Namespace) -> int:
    experiment_path = args.output / "train_dev_experiment.json"
    if not experiment_path.is_file():
        raise RuntimeError("train/dev experiment is missing")
    experiment = CORE.read_json(experiment_path)
    if experiment.get("decision") != "CANDIDATE_READY_FOR_LOCK":
        raise RuntimeError("no candidate passed the frozen development gates")
    selection = experiment.get("selection") if isinstance(experiment.get("selection"), dict) else {}
    trial_id = str(selection.get("trial_id") or "")
    checkpoint = Path(str(selection.get("checkpoint", {}).get("path") or ""))
    if not checkpoint.is_file():
        raise RuntimeError(f"selected checkpoint is missing: {checkpoint}")
    expected_checkpoint_sha = str(selection.get("checkpoint", {}).get("sha256") or "")
    if CORE.sha256(checkpoint) != expected_checkpoint_sha:
        raise RuntimeError("selected checkpoint changed after train/dev selection")
    _, checkpoint_metadata = CORE.load_checkpoint(checkpoint)
    baseline_report_path = args.output / "dev_baseline_reproduction.json"
    if not baseline_report_path.is_file():
        raise RuntimeError("frozen dev baseline reproduction is missing")
    baseline_report = CORE.read_json(baseline_report_path)
    if (
        baseline_report.get("split") != "dev"
        or baseline_report.get("cache_fingerprint")
        != checkpoint_metadata.get("dev_cache_fingerprint")
    ):
        raise RuntimeError("dev baseline reproduction does not match the selected candidate")
    controlled_report_path = (
        args.output
        / "controlled-dev-validation/2026-08-02_22-57-31-echo-dev-normal/controlled_evaluation.json"
    )
    if not controlled_report_path.is_file():
        raise RuntimeError("controlled dev validation is missing")
    controlled_report = CORE.read_json(controlled_report_path)
    if (
        controlled_report.get("passed") is not True
        or controlled_report.get("split") != "dev"
        or controlled_report.get("checkpoint_sha256") != expected_checkpoint_sha
    ):
        raise RuntimeError("controlled dev validation did not pass for the selected candidate")
    first = evaluate_checkpoint(
        output=args.output,
        checkpoint=checkpoint,
        split="dev",
        batch_size=int(args.batch_size),
        device=args.device,
    )
    second = evaluate_checkpoint(
        output=args.output,
        checkpoint=checkpoint,
        split="dev",
        batch_size=int(args.batch_size),
        device=args.device,
    )
    if not first["passed"] or not second["passed"]:
        raise RuntimeError("selected candidate no longer passes development gates")
    if first["candidate_output_stream_sha256"] != second["candidate_output_stream_sha256"]:
        raise RuntimeError("candidate audio replay is not deterministic")
    policy_sha = CORE.sha256(args.policy)
    if checkpoint_metadata.get("policy_sha256") != policy_sha:
        raise RuntimeError("checkpoint was not trained under the locked policy")
    trial_dir = args.output / "trials" / trial_id
    training_report = trial_dir / "training_report.json"
    evaluation_path = trial_dir / "dev_evaluation.locked.json"
    CORE.write_json(evaluation_path, first)
    basis = {
        "policy_sha256": policy_sha,
        "experiment_sha256": CORE.sha256(experiment_path),
        "trial_id": trial_id,
        "checkpoint_sha256": expected_checkpoint_sha,
        "training_report_sha256": CORE.sha256(training_report),
        "dev_evaluation_sha256": CORE.sha256(evaluation_path),
        "dev_baseline_reproduction_sha256": CORE.sha256(baseline_report_path),
        "controlled_dev_validation_sha256": CORE.sha256(controlled_report_path),
        "candidate_output_stream_sha256": first["candidate_output_stream_sha256"],
        "train_cache_fingerprint": checkpoint_metadata["train_cache_fingerprint"],
        "dev_cache_fingerprint": checkpoint_metadata["dev_cache_fingerprint"],
        "implementation": {
            "core": CORE.sha256(Path(CORE.__file__)),
            "driver": CORE.sha256(Path(__file__)),
        },
    }
    lock_fingerprint = CORE.digest_json(basis)
    lock_payload = {
        "schema": "murmurmark.speaker_preserving_neural_echo_candidate_lock/v2",
        "status": "locked_for_single_hard_test",
        "hard_test_accessed": False,
        "candidate": {
            "trial_id": trial_id,
            "checkpoint": relative_to_root(checkpoint),
            "checkpoint_sha256": expected_checkpoint_sha,
            "metadata": checkpoint_metadata,
        },
        "development": {
            "evaluation": relative_to_root(evaluation_path),
            "evaluation_sha256": CORE.sha256(evaluation_path),
            "gates": first["gates"],
            "metrics": first["metrics"],
            "replay_sha256": first["candidate_output_stream_sha256"],
        },
        "provenance": basis,
        "fingerprint": lock_fingerprint,
    }
    lock_path = args.output / "candidate_lock.json"
    CORE.write_json(lock_path, lock_payload)
    unlock = {
        "schema": "murmurmark.speaker_preserving_neural_echo_hard_unlock/v2",
        "candidate_lock": relative_to_root(lock_path),
        "candidate_lock_sha256": CORE.sha256(lock_path),
        "candidate_lock_fingerprint": lock_fingerprint,
        "attempts_allowed": 1,
        "attempts_consumed": 0,
    }
    CORE.write_json(args.output / "hard_test_unlock.json", unlock)
    print(json.dumps(lock_payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise RuntimeError(f"invalid JSONL row at {path}:{line_number}")
            rows.append(value)
    return rows


def session_artifact(session: Path, artifact: dict[str, Any]) -> Path:
    path = session / str(artifact.get("path") or "")
    if not path.is_file():
        raise RuntimeError(f"controlled artifact missing: {path}")
    if path.stat().st_size != int(artifact.get("bytes") or -1):
        raise RuntimeError(f"controlled artifact size changed: {path}")
    if CORE.sha256(path) != str(artifact.get("sha256") or ""):
        raise RuntimeError(f"controlled artifact SHA-256 changed: {path}")
    return path


def read_wave(path: Path) -> np.ndarray:
    import soundfile as sf

    values, sample_rate = sf.read(path, dtype="float32", always_2d=False)
    if sample_rate != CORE.SAMPLE_RATE:
        raise RuntimeError(f"unexpected controlled sample rate: {path}")
    values = np.asarray(values, dtype=np.float32)
    if values.ndim != 1 or not values.size or not np.all(np.isfinite(values)):
        raise RuntimeError(f"invalid controlled mono audio: {path}")
    return values


def write_wave(path: Path, values: np.ndarray) -> None:
    import soundfile as sf

    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, np.asarray(values, dtype=np.float32), CORE.SAMPLE_RATE, subtype="FLOAT")


def controlled_phase_rows(session: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    inspection = CORE.read_json(session / "derived/echo-lab/inspection.json")
    rows = read_jsonl(session / "derived/echo-lab/phase_inventory.jsonl")
    return inspection, {str(row.get("kind") or ""): row for row in rows}


def target_me_scores(local_audio: np.ndarray, candidates: dict[str, np.ndarray]) -> dict[str, Any]:
    try:
        from resemblyzer import VoiceEncoder, preprocess_wav
    except ImportError as error:
        raise RuntimeError("resemblyzer is required for the hard test") from error
    lab = load_module(
        ROOT / "scripts/controlled-echo-supervision-lab.py",
        "murmurmark_spne_v2_controlled_lab_target_me",
    )
    encoder = VoiceEncoder(verbose=False)
    local_embeddings = lab.chunk_embeddings(local_audio, encoder, preprocess_wav, chunk_sec=4.0)
    if len(local_embeddings) < 8:
        raise RuntimeError("controlled local-only enrollment is too small")
    enrollment = np.mean(np.stack(local_embeddings[:3]), axis=0)
    enrollment /= max(float(np.linalg.norm(enrollment)), 1.0e-12)
    result: dict[str, Any] = {}
    for name, audio in candidates.items():
        embeddings = lab.chunk_embeddings(audio, encoder, preprocess_wav, chunk_sec=4.0)
        scores = [
            float(np.dot(enrollment, row) / max(float(np.linalg.norm(row)), 1.0e-12))
            for row in embeddings
        ]
        result[name] = {
            "chunk_count": len(scores),
            "maximum_similarity": round(max(scores), 6) if scores else 0.0,
            "median_similarity": round(float(np.median(scores)), 6) if scores else 0.0,
        }
    return result


def transcribe_controlled(
    *,
    audio_path: Path,
    cache_path: Path,
    refresh: bool,
    model: Any | None,
) -> tuple[dict[str, Any], Any]:
    lab = load_module(
        ROOT / "scripts/controlled-echo-supervision-lab.py",
        "murmurmark_spne_v2_controlled_lab_asr",
    )
    model_path = (
        Path.home() / ".local/share/murmurmark/models/faster-whisper/large-v3"
    ).resolve()
    if refresh:
        cache_path.unlink(missing_ok=True)
    loaded = model or lab.load_faster_whisper_model(model_path)
    payload = lab.transcribe_words(
        audio_path,
        model_path=model_path,
        cache_path=cache_path,
        source_sha256=CORE.sha256(audio_path),
        model_instance=loaded,
    )
    return payload, loaded


def discriminative_token_evidence(
    *,
    expected: str,
    forbidden: str,
    observed: str,
    tokenizer: Any,
) -> dict[str, Any]:
    forbidden_tokens = set(tokenizer(forbidden))
    expected_tokens = sorted(
        token
        for token in set(tokenizer(expected))
        if token not in forbidden_tokens
        and not any(
            len(token) >= 5 and len(local) >= 5 and token[:4] == local[:4]
            for local in forbidden_tokens
        )
    )
    observed_tokens = set(tokenizer(observed))
    matched = sorted(set(expected_tokens) & observed_tokens)
    return {
        "expected_unique_token_count": len(expected_tokens),
        "matched_unique_token_count": len(matched),
        "recall": round(len(matched) / max(len(expected_tokens), 1), 6),
        "matched_tokens_sha256": hashlib_sha256_text(" ".join(matched)),
    }


def prompt_sequence_signature(
    segments: list[dict[str, Any]],
    prompts: tuple[str, ...],
    tokenizer: Any,
) -> list[int]:
    prompt_tokens = [set(tokenizer(prompt)) for prompt in prompts]
    signature: list[int] = []
    for segment in segments:
        observed = set(tokenizer(segment.get("text")))
        scores = [len(observed & expected) / max(len(expected), 1) for expected in prompt_tokens]
        best = int(np.argmax(scores)) if scores else -1
        if best >= 0 and scores[best] >= 0.5:
            signature.append(best)
    return signature


def sequence_edit_distance(left: list[int], right: list[int]) -> int:
    previous = list(range(len(right) + 1))
    for left_index, left_value in enumerate(left, 1):
        current = [left_index]
        for right_index, right_value in enumerate(right, 1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1] + (left_value != right_value),
                )
            )
        previous = current
    return previous[-1]


def controlled_candidate_audio(
    *,
    session: Path,
    phases: dict[str, dict[str, Any]],
    checkpoint: Path,
    device: str,
    output_dir: Path,
) -> dict[str, Any]:
    import torch
    from echo_promotion_timeline import align_remote_constant

    double_talk = phases["controlled_double_talk"]
    local = phases["local_only"]
    opening = phases["opening_backchannel"]
    remote_only = phases["remote_only"]
    raw_path = session_artifact(session, double_talk["artifacts"]["mic"])
    remote_path = session_artifact(session, double_talk["artifacts"]["remote"])
    baseline_path = session_artifact(
        session,
        double_talk["artifacts"]["echo_clean_validation"],
    )
    raw = read_wave(raw_path)
    remote = read_wave(remote_path)
    baseline = read_wave(baseline_path)
    count = min(raw.size, remote.size, baseline.size)
    raw, remote, baseline = raw[:count], remote[:count], baseline[:count]
    lag_ms = float(double_talk.get("metrics", {}).get("lag_ms") or 0.0)
    aligned_remote = align_remote_constant(remote, CORE.SAMPLE_RATE, lag_ms)
    echo_estimate = raw - baseline
    model, metadata = CORE.load_checkpoint(checkpoint, device=device)
    model.eval()
    window = torch.from_numpy(CORE.analysis_window()).to(device)
    started = time.monotonic()
    with torch.no_grad():
        candidate, _ = CORE.apply_model(
            model,
            as_tensor(baseline[None], device),
            as_tensor(aligned_remote[None], device),
            window,
            as_tensor(echo_estimate[None], device),
        )
    candidate_audio = candidate.detach().cpu().numpy()[0].astype(np.float32)
    candidate_path = output_dir / "controlled_double_talk.candidate.wav"
    write_wave(candidate_path, candidate_audio)
    local_path = session_artifact(session, local["artifacts"]["mic"])
    opening_path = session_artifact(session, opening["artifacts"]["mic"])
    remote_mic_path = session_artifact(session, remote_only["artifacts"]["mic"])
    local_audio = read_wave(local_path)
    opening_audio = read_wave(opening_path)
    local_candidate_path = output_dir / "local_only.candidate.wav"
    opening_candidate_path = output_dir / "opening_backchannel.candidate.wav"
    write_wave(local_candidate_path, local_audio)
    write_wave(opening_candidate_path, opening_audio)
    remote_candidate = np.zeros_like(read_wave(remote_mic_path))
    remote_candidate_path = output_dir / "remote_only.candidate.wav"
    write_wave(remote_candidate_path, remote_candidate)
    return {
        "checkpoint_metadata": metadata,
        "raw_path": raw_path,
        "baseline_path": baseline_path,
        "candidate_path": candidate_path,
        "local_candidate_path": local_candidate_path,
        "opening_candidate_path": opening_candidate_path,
        "remote_candidate_path": remote_candidate_path,
        "raw": raw,
        "baseline": baseline,
        "candidate": candidate_audio,
        "local_audio": local_audio,
        "opening_audio": opening_audio,
        "remote_candidate": remote_candidate,
        "runtime_sec": round(time.monotonic() - started, 6),
        "lag_ms": lag_ms,
    }


def evaluate_controlled_session(
    *,
    session: Path,
    checkpoint: Path,
    output_dir: Path,
    device: str,
    refresh_asr: bool,
) -> dict[str, Any]:
    controlled = load_module(
        ROOT / "scripts/controlled_echo_supervision.py",
        "murmurmark_spne_v2_controlled_contract",
    )
    inspection, phases = controlled_phase_rows(session)
    required = {
        "remote_only",
        "local_only",
        "controlled_double_talk",
        "opening_backchannel",
    }
    if not required.issubset(phases):
        raise RuntimeError(f"controlled phases missing: {sorted(required - set(phases))}")
    audio = controlled_candidate_audio(
        session=session,
        phases=phases,
        checkpoint=checkpoint,
        device=device,
        output_dir=output_dir,
    )
    asr_model = None
    baseline_asr, asr_model = transcribe_controlled(
        audio_path=audio["baseline_path"],
        cache_path=output_dir / "controlled_double_talk.baseline.faster_whisper.json",
        refresh=refresh_asr,
        model=asr_model,
    )
    candidate_asr, _ = transcribe_controlled(
        audio_path=audio["candidate_path"],
        cache_path=output_dir / "controlled_double_talk.candidate.faster_whisper.json",
        refresh=refresh_asr,
        model=asr_model,
    )
    baseline_text = " ".join(str(row.get("text") or "") for row in baseline_asr.get("segments", []))
    candidate_text = " ".join(str(row.get("text") or "") for row in candidate_asr.get("segments", []))
    expected_local = " ".join(controlled.DOUBLE_TALK_PROMPTS)
    expected_remote = controlled.REMOTE_TTS_TEXT
    baseline_local_recall = controlled.token_recall(expected_local, baseline_text)
    candidate_local_recall = controlled.token_recall(expected_local, candidate_text)
    baseline_remote = discriminative_token_evidence(
        expected=expected_remote,
        forbidden=expected_local,
        observed=baseline_text,
        tokenizer=controlled.tokens,
    )
    candidate_remote = discriminative_token_evidence(
        expected=expected_remote,
        forbidden=expected_local,
        observed=candidate_text,
        tokenizer=controlled.tokens,
    )
    baseline_sequence = prompt_sequence_signature(
        baseline_asr.get("segments", []),
        controlled.DOUBLE_TALK_PROMPTS,
        controlled.tokens,
    )
    candidate_sequence = prompt_sequence_signature(
        candidate_asr.get("segments", []),
        controlled.DOUBLE_TALK_PROMPTS,
        controlled.tokens,
    )
    chronology_regressions = sequence_edit_distance(baseline_sequence, candidate_sequence)
    speaker = target_me_scores(
        audio["local_audio"],
        {"baseline": audio["baseline"], "candidate": audio["candidate"]},
    )
    local_ratio = candidate_local_recall / max(baseline_local_recall, 1.0e-9)
    target_delta = (
        speaker["candidate"]["maximum_similarity"]
        - speaker["baseline"]["maximum_similarity"]
    )
    opening_candidate = read_wave(audio["opening_candidate_path"])
    local_candidate = read_wave(audio["local_candidate_path"])
    opening_exact = np.array_equal(audio["opening_audio"], opening_candidate)
    local_exact = np.array_equal(audio["local_audio"], local_candidate)
    candidate = audio["candidate"]
    remote_candidate = audio["remote_candidate"]
    gates = {
        "double_talk_local_recall_ratio_gte_0_99": local_ratio >= 0.99,
        "double_talk_target_me_delta_gte_minus_0_02": target_delta >= -0.02,
        "double_talk_remote_unique_tokens_not_increased": (
            candidate_remote["matched_unique_token_count"]
            <= baseline_remote["matched_unique_token_count"]
        ),
        "double_talk_chronology_regressions_eq_0": chronology_regressions == 0,
        "opening_exact_bypass": opening_exact,
        "local_exact_bypass": local_exact,
        "remote_only_zero_gate": float(np.max(np.abs(remote_candidate))) == 0.0,
        "candidate_finite": bool(np.all(np.isfinite(candidate))),
        "candidate_not_clipped": float(np.mean(np.abs(candidate) >= 0.995)) <= 0.0001,
    }
    report = {
        "schema": "murmurmark.speaker_preserving_neural_echo_controlled_evaluation/v2",
        "session": session.name,
        "split": inspection.get("split"),
        "checkpoint_sha256": CORE.sha256(checkpoint),
        "audio": {
            "baseline": relative_to_root(audio["baseline_path"]),
            "baseline_sha256": CORE.sha256(audio["baseline_path"]),
            "candidate": relative_to_root(audio["candidate_path"]),
            "candidate_sha256": CORE.sha256(audio["candidate_path"]),
            "local_bypass": relative_to_root(audio["local_candidate_path"]),
            "local_bypass_sha256": CORE.sha256(audio["local_candidate_path"]),
            "opening_bypass": relative_to_root(audio["opening_candidate_path"]),
            "opening_bypass_sha256": CORE.sha256(audio["opening_candidate_path"]),
            "samples": int(candidate.size),
            "runtime_sec": audio["runtime_sec"],
            "realtime_factor": round(audio["runtime_sec"] / (candidate.size / CORE.SAMPLE_RATE), 6),
            "lag_ms": audio["lag_ms"],
        },
        "asr": {
            "baseline_local_prompt_recall": round(baseline_local_recall, 6),
            "candidate_local_prompt_recall": round(candidate_local_recall, 6),
            "local_prompt_recall_ratio": round(local_ratio, 6),
            "baseline_remote_unique_evidence": baseline_remote,
            "candidate_remote_unique_evidence": candidate_remote,
            "baseline_prompt_sequence": baseline_sequence,
            "candidate_prompt_sequence": candidate_sequence,
            "chronology_regressions": chronology_regressions,
            "candidate_text_sha256": hashlib_sha256_text(candidate_text),
            "baseline_text_sha256": hashlib_sha256_text(baseline_text),
        },
        "target_me": {
            **speaker,
            "maximum_similarity_delta": round(target_delta, 6),
        },
        "gates": gates,
        "passed": all(gates.values()),
        "post_asr_cleanup_credit": 0,
    }
    CORE.write_json(output_dir / "controlled_evaluation.json", report)
    return report


def hashlib_sha256_text(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def selected_checkpoint(args: argparse.Namespace) -> Path:
    lock = CORE.read_json(args.output / "candidate_lock.json")
    path = ROOT / str(lock.get("candidate", {}).get("checkpoint") or "")
    expected = str(lock.get("candidate", {}).get("checkpoint_sha256") or "")
    if not path.is_file() or CORE.sha256(path) != expected:
        raise RuntimeError("locked candidate checkpoint is missing or changed")
    return path


def command_validate_controlled(args: argparse.Namespace) -> int:
    session = args.session.expanduser().resolve()
    inspection = CORE.read_json(session / "derived/echo-lab/inspection.json")
    if inspection.get("split") != "dev":
        raise RuntimeError("validate-controlled accepts only an allowed dev session")
    report = evaluate_controlled_session(
        session=session,
        checkpoint=selected_checkpoint(args),
        output_dir=args.output / "controlled-dev-validation" / session.name,
        device=args.device,
        refresh_asr=bool(args.refresh_asr),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["passed"] else 5


def command_hard_test(args: argparse.Namespace) -> int:
    unlock_path = args.output / "hard_test_unlock.json"
    unlock = CORE.read_json(unlock_path)
    if int(unlock.get("attempts_consumed") or 0) != 0:
        raise RuntimeError("the single hard-test attempt has already been consumed")
    lock_path = ROOT / str(unlock.get("candidate_lock") or "")
    if CORE.sha256(lock_path) != str(unlock.get("candidate_lock_sha256") or ""):
        raise RuntimeError("candidate lock changed after hard-test unlock")
    lock = CORE.read_json(lock_path)
    if lock.get("fingerprint") != unlock.get("candidate_lock_fingerprint"):
        raise RuntimeError("candidate lock fingerprint mismatch")
    expected_implementation = lock.get("provenance", {}).get("implementation", {})
    observed_implementation = {
        "core": CORE.sha256(Path(CORE.__file__)),
        "driver": CORE.sha256(Path(__file__)),
    }
    if observed_implementation != expected_implementation:
        raise RuntimeError("locked hard-test implementation changed")
    attempt_path = args.output / "hard_test_attempt.json"
    if attempt_path.exists():
        raise RuntimeError("hard-test attempt marker already exists")
    attempt = {
        "schema": "murmurmark.speaker_preserving_neural_echo_hard_attempt/v2",
        "status": "started",
        "candidate_lock_fingerprint": lock["fingerprint"],
        "policy_sha256": CORE.sha256(args.policy),
    }
    CORE.write_json(attempt_path, attempt)
    unlock["attempts_consumed"] = 1
    CORE.write_json(unlock_path, unlock)
    try:
        split = CORE.read_json(args.corpus_root / "split_manifest.json")
        hard_sessions = [
            str(row["session_id"])
            for row in split.get("sessions", [])
            if row.get("split") == "hard_test"
        ]
        if len(hard_sessions) != 1:
            raise RuntimeError(f"expected one controlled hard session, found {hard_sessions}")
        session = ROOT / "sessions" / hard_sessions[0]
        report = evaluate_controlled_session(
            session=session,
            checkpoint=selected_checkpoint(args),
            output_dir=args.output / "hard-test" / session.name,
            device=args.device,
            refresh_asr=False,
        )
        attempt.update(
            {
                "status": "completed",
                "session": session.name,
                "report": relative_to_root(
                    args.output / "hard-test" / session.name / "controlled_evaluation.json"
                ),
                "report_sha256": CORE.sha256(
                    args.output / "hard-test" / session.name / "controlled_evaluation.json"
                ),
                "passed": report["passed"],
            }
        )
    except Exception as error:
        attempt.update(
            {
                "status": "failed",
                "passed": False,
                "reason": f"{type(error).__name__}:{error}",
            }
        )
        CORE.write_json(attempt_path, attempt)
        raise
    CORE.write_json(attempt_path, attempt)
    decision = {
        "schema": "murmurmark.speaker_preserving_neural_echo_hard_decision/v2",
        "decision": "HARD_TEST_PASSED" if report["passed"] else "DO_NOT_PROMOTE",
        "candidate_lock_fingerprint": lock["fingerprint"],
        "hard_report_sha256": attempt["report_sha256"],
        "gates": report["gates"],
    }
    CORE.write_json(args.output / "hard_test_decision.json", decision)
    print(json.dumps({"attempt": attempt, "report": report, "decision": decision}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["passed"] else 6


def main() -> int:
    args = parser().parse_args()
    args.corpus_root = args.corpus_root.expanduser().resolve()
    args.output = args.output.expanduser().resolve()
    args.policy = args.policy.expanduser().resolve()
    CORE.verify_policy_sources(ROOT, args.policy)
    if sys.version_info < (3, 12):
        raise SystemExit("Speaker-Preserving Neural Echo v2 requires Python 3.12+")
    if args.command == "prepare":
        return command_prepare(args)
    if args.command == "train":
        return command_train(args)
    if args.command == "evaluate":
        return command_evaluate(args)
    if args.command == "baselines":
        return command_baselines(args)
    if args.command == "experiment":
        return command_experiment(args)
    if args.command == "lock":
        return command_lock(args)
    if args.command == "validate-controlled":
        return command_validate_controlled(args)
    if args.command == "hard-test":
        return command_hard_test(args)
    raise RuntimeError(f"unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
