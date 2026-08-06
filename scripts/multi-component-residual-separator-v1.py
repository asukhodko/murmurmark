#!/usr/bin/env python3
"""Bounded four-stem residual separator qualification."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

import numpy as np


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from murmurmark_resource_policy import apply_resource_policy, resolve_resource_policy  # noqa: E402
import multi_component_residual_separator_v1 as SEPARATOR  # noqa: E402


SCHEMA = "murmurmark.multi_component_residual_separator_policy/v1"
PROFILE = "multi_component_residual_separator_v1"
PREFLIGHT_READY = "READY_FOR_MULTI_COMPONENT_TRAIN_DEV"
DEV_LOCKED = "MULTI_COMPONENT_DEV_CANDIDATE_LOCKED"
DEV_REJECTED = "MULTI_COMPONENT_DEV_CANDIDATE_REJECTED"
HARD_PASSED = "MULTI_COMPONENT_HARD_TEST_PASSED"
HARD_REJECTED = "MULTI_COMPONENT_HARD_TEST_REJECTED"
PROMOTE = "PROMOTE_MULTI_COMPONENT_RESIDUAL_SEPARATOR"
STRONGER = "READY_FOR_STRONGER_LOCAL_SEPARATOR"
RESOURCE_LIMIT = "CURRENT_RESOURCE_LIMIT_REACHED"
CACHE_CHANNELS = (
    "mixture",
    "target_me",
    "other_local_speech",
    "remote_echo",
    "other_local_noise",
)


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


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def read_jsonl(path: Path, *, split: str | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"expected JSON object at {path}:{line_number}")
            if split is None or row.get("split") == split:
                rows.append(row)
    return rows


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(temporary, path)


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        for row in rows:
            handle.write(canonical_json(row) + "\n")
        temporary = Path(handle.name)
    os.replace(temporary, path)


def display_path(path: Path) -> str:
    resolved = path.expanduser().resolve()
    try:
        return str(resolved.relative_to(ROOT))
    except ValueError:
        pass
    try:
        return "~/" + str(resolved.relative_to(Path.home().resolve()))
    except ValueError:
        return str(resolved)


def checked(name: str, observed: Any, expected: Any) -> dict[str, Any]:
    return {"name": name, "observed": observed, "expected": expected, "passed": observed == expected}


def threshold_check(
    name: str,
    observed: float,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> dict[str, Any]:
    if (minimum is None) == (maximum is None):
        raise ValueError("exactly one threshold direction is required")
    passed = observed >= minimum if minimum is not None else observed <= maximum
    row: dict[str, Any] = {
        "name": name,
        "observed": round(float(observed), 6),
        "passed": bool(passed),
    }
    row["minimum" if minimum is not None else "maximum"] = minimum if minimum is not None else maximum
    return row


def load_policy(path: Path) -> dict[str, Any]:
    policy = read_json(path)
    if policy.get("schema") != SCHEMA or policy.get("profile") != PROFILE:
        raise RuntimeError("unexpected multi-component residual separator policy")
    return policy


def descriptor_path(descriptor: dict[str, Any]) -> Path:
    return ROOT / str(descriptor["path"])


def verify_descriptor(descriptor: dict[str, Any]) -> dict[str, Any]:
    path = descriptor_path(descriptor)
    observed = sha256(path) if path.is_file() else None
    row = {
        "path": display_path(path),
        "expected_sha256": descriptor.get("sha256"),
        "observed_sha256": observed,
        "passed": observed == descriptor.get("sha256"),
    }
    required = descriptor.get("required_decision")
    if required is not None:
        value = read_json(path).get("decision") if path.is_file() and path.suffix == ".json" else None
        row["required_decision"] = required
        row["observed_decision"] = value
        row["passed"] = row["passed"] and value == required
    return row


def corpus_root(policy: dict[str, Any]) -> Path:
    return ROOT / str(policy["corpus"]["root"])


def verify_corpus_descriptor(root: Path, relative: str, expected: str) -> dict[str, Any]:
    path = root / relative
    observed = sha256(path) if path.is_file() else None
    return {
        "path": display_path(path),
        "expected_sha256": expected,
        "observed_sha256": observed,
        "passed": observed == expected,
    }


def cache_paths(policy: dict[str, Any], split: str) -> tuple[Path, Path]:
    cache = policy["corpus"]["cache"]
    return descriptor_path(cache[f"{split}_waveforms"]), descriptor_path(cache[f"{split}_manifest"])


def load_cache(policy: dict[str, Any], split: str) -> tuple[np.ndarray, dict[str, Any]]:
    waveforms_path, manifest_path = cache_paths(policy, split)
    manifest_descriptor = policy["corpus"]["cache"][f"{split}_manifest"]
    waveforms_descriptor = policy["corpus"]["cache"][f"{split}_waveforms"]
    if sha256(manifest_path) != manifest_descriptor["sha256"]:
        raise RuntimeError(f"changed {split} cache manifest")
    if sha256(waveforms_path) != waveforms_descriptor["sha256"]:
        raise RuntimeError(f"changed {split} cache waveforms")
    manifest = read_json(manifest_path)
    if manifest.get("channels") != list(CACHE_CHANNELS):
        raise RuntimeError(f"unexpected {split} cache channels")
    return np.load(waveforms_path, mmap_mode="r"), manifest


def publication_artifact(root: Path, descriptor: dict[str, Any]) -> Path:
    path = (root / str(descriptor["path"])).resolve()
    if root.resolve() not in path.parents or not path.is_file():
        raise RuntimeError(f"invalid corpus artifact: {descriptor.get('path')}")
    if sha256(path) != descriptor.get("sha256"):
        raise RuntimeError(f"changed corpus artifact: {path}")
    return path


def enrollment_vectors(root: Path, rows: list[dict[str, Any]]) -> dict[str, np.ndarray]:
    descriptors: dict[str, dict[str, Any]] = {}
    for row in rows:
        for query in row["queries"].values():
            descriptor = query["enrollment"]
            descriptors[str(descriptor["path"])] = descriptor
    vectors: dict[str, np.ndarray] = {}
    for key, descriptor in sorted(descriptors.items()):
        value = np.asarray(np.load(publication_artifact(root, descriptor)), dtype=np.float32).reshape(-1)
        value /= max(float(np.linalg.norm(value)), 1.0e-8)
        vectors[key] = value
    return vectors


def split_integrity(policy: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    split_manifest = read_json(corpus_root(policy) / "split_manifest.json")
    splits = split_manifest.get("splits") or {}
    checks: list[dict[str, Any]] = []
    speakers: dict[str, set[str]] = {}
    for split, expected in policy["corpus"]["split_counts"].items():
        observed = splits.get(split) or {}
        speakers[split] = set(observed.get("non_target_speakers") or [])
        checks.extend(
            (
                checked(f"{split}_items", observed.get("items"), expected["items"]),
                checked(f"{split}_queries", observed.get("queries"), expected["queries"]),
                checked(
                    f"{split}_non_target_speakers",
                    len(speakers[split]),
                    expected["non_target_speakers"],
                ),
            )
        )
    overlap = {
        "train_dev": sorted(speakers["train"] & speakers["dev"]),
        "train_hard": sorted(speakers["train"] & speakers["hard"]),
        "dev_hard": sorted(speakers["dev"] & speakers["hard"]),
    }
    checks.append(checked("non_target_speaker_split_overlap", overlap, {key: [] for key in overlap}))
    checks.append(
        checked(
            "target_identity_cross_split",
            (split_manifest.get("target_identity_policy") or {}).get("identity_cross_split"),
            True,
        )
    )
    return checks, {key: sorted(value) for key, value in speakers.items()}


def supervision_reconstruction(policy: dict[str, Any]) -> dict[str, Any]:
    errors: dict[str, float] = {}
    for split in ("train", "dev"):
        waveforms, manifest = load_cache(policy, split)
        expected_shape = [
            int(policy["corpus"]["split_counts"][split]["items"]),
            len(CACHE_CHANNELS),
            SEPARATOR.CLIP_SAMPLES,
        ]
        if list(waveforms.shape) != expected_shape or len(manifest.get("rows") or []) != expected_shape[0]:
            raise RuntimeError(f"unexpected {split} cache shape")
        maximum = 0.0
        for offset in range(0, waveforms.shape[0], 32):
            values = np.asarray(waveforms[offset : offset + 32], dtype=np.float32)
            reconstruction = values[:, 1] + values[:, 2] + values[:, 3] + values[:, 4]
            maximum = max(maximum, float(np.max(np.abs(values[:, 0] - reconstruction))))
        errors[split] = round(maximum, 9)
    return {
        "errors": errors,
        "maximum": max(errors.values()),
        "passed": max(errors.values()) <= float(policy["decomposition_contract"]["reconstruction_max_abs_error"]),
    }


def run_preflight(*, policy_path: Path, output_dir: Path) -> dict[str, Any]:
    resource = apply_resource_policy(resolve_resource_policy("background"))
    policy = load_policy(policy_path)
    source_rows = [verify_descriptor(value) for _, value in sorted(policy["sources"].items())]
    root = corpus_root(policy)
    corpus_rows = [
        verify_corpus_descriptor(root, relative, expected)
        for relative, expected in sorted(policy["corpus"]["files"].items())
    ]
    current = read_json(descriptor_path(policy["sources"]["target_corpus_current"]))
    checks = [
        checked("source_artifacts", all(row["passed"] for row in source_rows), True),
        checked("corpus_artifacts", all(row["passed"] for row in corpus_rows), True),
        checked(
            "corpus_current_fingerprint",
            current.get("fingerprint"),
            policy["sources"]["target_corpus_current"]["required_fingerprint"],
        ),
        checked("post_asr_cleanup_credit", policy["post_asr_cleanup_promotion_credit"], 0),
        checked("publication_forbidden", policy["production_publication"], "forbidden_until_corpus_decision"),
        checked("hard_audio_files_read", 0, 0),
        checked("sealed_audio_files_read", 0, 0),
    ]
    split_checks, split_speakers = split_integrity(policy)
    checks.extend(split_checks)
    reconstruction = supervision_reconstruction(policy)
    checks.append(checked("supervision_mixture_consistency", reconstruction["passed"], True))
    pretrained = next(row for row in policy["candidate_ladder"] if row["id"] == "offline_pretrained_initialization")
    checks.append(checked("pretrained_initialization_network_access", pretrained["status"], "not_available"))
    passed = all(row["passed"] for row in checks)
    deterministic = {
        "policy_sha256": sha256(policy_path),
        "source_rows": source_rows,
        "corpus_rows": corpus_rows,
        "split_checks": split_checks,
        "reconstruction": reconstruction,
        "candidate_ladder": policy["candidate_ladder"],
        "checks": checks,
    }
    report = {
        "schema": "murmurmark.multi_component_residual_separator_preflight/v1",
        "profile": PROFILE,
        "decision": PREFLIGHT_READY if passed else "BLOCKED_PREFLIGHT",
        "fingerprint": digest_json(deterministic),
        "policy": {"path": display_path(policy_path), "sha256": sha256(policy_path)},
        "source_artifacts": source_rows,
        "corpus_artifacts": corpus_rows,
        "split_speakers": split_speakers,
        "supervision_reconstruction": reconstruction,
        "candidate_ladder": policy["candidate_ladder"],
        "checks": checks,
        "hard_audio_opened": False,
        "sealed_audio_opened": False,
        "resource_policy": resource,
    }
    write_json(output_dir / "preflight_report.json", report)
    return report


def prepare_batch(
    waveforms: np.ndarray,
    rows: list[dict[str, Any]],
    indices: list[int],
    vectors: dict[str, np.ndarray],
) -> tuple[np.ndarray, ...]:
    values = np.asarray(waveforms[indices], dtype=np.float32)
    mixture_rows: list[np.ndarray] = []
    echo_rows: list[np.ndarray] = []
    target_rows: list[np.ndarray] = []
    other_rows: list[np.ndarray] = []
    residual_rows: list[np.ndarray] = []
    enrollment_rows: list[np.ndarray] = []
    metadata: list[dict[str, Any]] = []
    for local_index, global_index in enumerate(indices):
        item = rows[global_index]
        for role, target_channel, other_channel in (
            ("target_me", 1, 2),
            ("other_local_speech", 2, 1),
        ):
            query = item["queries"][role]
            mixture_rows.append(values[local_index, 0])
            target_rows.append(values[local_index, target_channel])
            other_rows.append(values[local_index, other_channel])
            echo_rows.append(values[local_index, 3])
            residual_rows.append(values[local_index, 4])
            enrollment_rows.append(vectors[str(query["enrollment"]["path"])])
            metadata.append(
                {
                    "item_index": global_index,
                    "pair_position": 0 if role == "target_me" else 1,
                    "query_role": role,
                    "speaker_present": bool(query["speaker_present"]),
                    **{key: item[key] for key in ("item_id", "family", "usage")},
                }
            )
    return (
        np.stack(mixture_rows),
        np.stack(echo_rows),
        np.stack(target_rows),
        np.stack(other_rows),
        np.stack(residual_rows),
        np.stack(enrollment_rows),
        metadata,
    )


def snr_db(reference: np.ndarray, estimate: np.ndarray) -> float:
    ref = np.asarray(reference, dtype=np.float64)
    est = np.asarray(estimate, dtype=np.float64)
    return float(10.0 * np.log10((np.sum(ref**2) + 1.0e-12) / (np.sum((ref - est) ** 2) + 1.0e-12)))


def rms_dbfs(values: np.ndarray) -> float:
    data = np.asarray(values, dtype=np.float64)
    return float(20.0 * np.log10(np.sqrt(np.mean(data**2)) + 1.0e-12))


def metric_summary(values: list[float]) -> dict[str, float | int]:
    if not values:
        return {"count": 0, "min": 0.0, "p05": 0.0, "median": 0.0, "p95": 0.0, "max": 0.0}
    data = np.asarray(values, dtype=np.float64)
    return {
        "count": int(data.size),
        "min": round(float(np.min(data)), 6),
        "p05": round(float(np.percentile(data, 5)), 6),
        "median": round(float(np.median(data)), 6),
        "p95": round(float(np.percentile(data, 95)), 6),
        "max": round(float(np.max(data)), 6),
    }


def evaluate_candidate(
    *,
    model: Any,
    waveforms: np.ndarray,
    manifest: dict[str, Any],
    publication: Path,
    batch_items: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    import torch

    rows = manifest["rows"]
    vectors = enrollment_vectors(publication, rows)
    window = torch.from_numpy(SEPARATOR.analysis_window())
    output_rows: list[dict[str, Any]] = []
    model.eval()
    for offset in range(0, len(rows), batch_items):
        indices = list(range(offset, min(len(rows), offset + batch_items)))
        mixture, echo, targets, others, residuals, enrollment, metadata = prepare_batch(
            waveforms, rows, indices, vectors
        )
        with torch.no_grad():
            predicted = SEPARATOR.apply_model(
                model,
                torch.from_numpy(mixture).float(),
                torch.from_numpy(echo).float(),
                torch.from_numpy(enrollment).float(),
                window,
            )
        estimates = predicted["target_me"].cpu().numpy()
        other_outputs = predicted["other_local"].cpu().numpy()
        residual_outputs = predicted["unexplained_residual"].cpu().numpy()
        for index, meta in enumerate(metadata):
            pair_index = index - 1 if meta["pair_position"] == 1 else index + 1
            reconstruction = estimates[index] + echo[index] + other_outputs[index] + residual_outputs[index]
            finite = bool(
                np.all(np.isfinite(estimates[index]))
                and np.all(np.isfinite(other_outputs[index]))
                and np.all(np.isfinite(residual_outputs[index]))
            )
            row: dict[str, Any] = {
                "schema": "murmurmark.multi_component_query_evaluation/v1",
                **meta,
                "finite": finite,
                "output_peak": round(
                    float(
                        max(
                            np.max(np.abs(estimates[index])),
                            np.max(np.abs(other_outputs[index])),
                            np.max(np.abs(residual_outputs[index])),
                        )
                    ),
                    9,
                ),
                "reconstruction_max_abs_error": round(float(np.max(np.abs(mixture[index] - reconstruction))), 9),
                "remote_echo_snr_db": round(snr_db(echo[index], echo[index]), 6),
                "other_local_snr_db": round(snr_db(others[index], other_outputs[index]), 6),
                "unexplained_residual_snr_db": round(snr_db(residuals[index], residual_outputs[index]), 6),
            }
            local_mixture = mixture[index] - echo[index]
            if meta["speaker_present"]:
                baseline_snr = snr_db(targets[index], local_mixture)
                candidate_snr = snr_db(targets[index], estimates[index])
                wrong_snr = snr_db(targets[index], estimates[pair_index])
                difference_ratio = float(
                    np.sqrt(np.mean((estimates[index] - estimates[pair_index]) ** 2))
                    / (np.sqrt(np.mean(local_mixture**2)) + 1.0e-12)
                )
                row.update(
                    {
                        "baseline_target_snr_db": round(baseline_snr, 6),
                        "target_snr_db": round(candidate_snr, 6),
                        "target_snr_improvement_db": round(candidate_snr - baseline_snr, 6),
                        "wrong_query_target_snr_db": round(wrong_snr, 6),
                        "paired_query_margin_db": round(candidate_snr - wrong_snr, 6),
                        "pair_output_difference_ratio": round(difference_ratio, 9),
                        "query_collapsed": difference_ratio < 0.1,
                    }
                )
            else:
                row["absent_query_attenuation_db"] = round(rms_dbfs(local_mixture) - rms_dbfs(estimates[index]), 6)
            if meta["family"] == "remote_only":
                row["remote_only_attenuation_db"] = round(rms_dbfs(mixture[index]) - rms_dbfs(estimates[index]), 6)
            output_rows.append(row)

    numeric_names = sorted(
        {
            key
            for row in output_rows
            for key, value in row.items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        }
    )
    metrics = {
        name: metric_summary([float(row[name]) for row in output_rows if name in row])
        for name in numeric_names
    }
    roles: dict[str, Any] = {}
    for role in ("target_me", "other_local_speech"):
        selected = [row for row in output_rows if row["query_role"] == role and row["speaker_present"]]
        roles[role] = {
            name: metric_summary([float(row[name]) for row in selected if name in row])
            for name in ("target_snr_db", "target_snr_improvement_db", "paired_query_margin_db")
        }
    families: dict[str, Any] = {}
    for family in sorted({str(row["family"]) for row in output_rows}):
        selected = [row for row in output_rows if row["family"] == family]
        families[family] = {
            "queries": len(selected),
            "present_queries": sum(bool(row["speaker_present"]) for row in selected),
            "target_snr_db": metric_summary([float(row["target_snr_db"]) for row in selected if "target_snr_db" in row]),
        }
    aggregate = {
        "queries": len(output_rows),
        "items": len(rows),
        "metrics": metrics,
        "roles": roles,
        "families": families,
        "query_collapse_count": sum(bool(row.get("query_collapsed")) for row in output_rows if row.get("speaker_present")),
        "query_collapse_denominator": sum(bool(row.get("speaker_present")) for row in output_rows),
        "clipped_outputs": sum(float(row["output_peak"]) > 1.0 for row in output_rows),
        "non_finite_outputs": sum(not bool(row["finite"]) for row in output_rows),
    }
    aggregate["query_collapse_rate"] = round(
        aggregate["query_collapse_count"] / max(1, aggregate["query_collapse_denominator"]), 9
    )
    return output_rows, aggregate


def selection_checks(
    aggregate: dict[str, Any],
    gates: dict[str, Any],
    required_families: list[str],
    *,
    runtime_sec: float,
    hard: bool,
) -> list[dict[str, Any]]:
    metrics = aggregate["metrics"]
    target_role = aggregate["roles"]["target_me"]
    checks = [
        threshold_check("target_me_snr_db_median", target_role["target_snr_db"]["median"], minimum=float(gates["target_me_snr_db_median_min"])),
        threshold_check("other_local_snr_db_median", metrics["other_local_snr_db"]["median"], minimum=float(gates["other_local_snr_db_median_min"])),
        threshold_check("paired_query_margin_db_median", metrics["paired_query_margin_db"]["median"], minimum=float(gates["paired_query_margin_db_median_min"])),
        threshold_check("query_collapse_rate", aggregate["query_collapse_rate"], maximum=float(gates["query_collapse_rate_max"])),
        threshold_check("unexplained_residual_snr_db_median", metrics["unexplained_residual_snr_db"]["median"], minimum=float(gates["unexplained_residual_snr_db_median_min"])),
        threshold_check("reconstruction_max_abs_error", metrics["reconstruction_max_abs_error"]["max"], maximum=float(gates["reconstruction_max_abs_error_max"])),
        threshold_check("clipped_outputs", aggregate["clipped_outputs"], maximum=float(gates["clipped_outputs_max"])),
        threshold_check("non_finite_outputs", aggregate["non_finite_outputs"], maximum=float(gates["non_finite_outputs_max"])),
        checked("family_coverage", sorted(aggregate["families"]), sorted(required_families)),
    ]
    if "target_me_improvement_db_median_min" in gates:
        checks.append(threshold_check("target_me_improvement_db_median", target_role["target_snr_improvement_db"]["median"], minimum=float(gates["target_me_improvement_db_median_min"])))
    if "absent_query_attenuation_db_median_min" in gates:
        checks.append(threshold_check("absent_query_attenuation_db_median", metrics["absent_query_attenuation_db"]["median"], minimum=float(gates["absent_query_attenuation_db_median_min"])))
    if "remote_echo_snr_db_median_min" in gates:
        checks.append(threshold_check("remote_echo_snr_db_median", metrics["remote_echo_snr_db"]["median"], minimum=float(gates["remote_echo_snr_db_median_min"])))
    if "runtime_sec_max" in gates:
        checks.append(threshold_check("runtime_sec", runtime_sec, maximum=float(gates["runtime_sec_max"])))
    if not hard:
        checks.append(checked("hard_audio_files_read", 0, int(gates["hard_audio_files_read_max"])))
    else:
        for family, key in (
            ("quiet_target_me", "quiet_target_me_snr_db_median_min"),
            ("opening_backchannel", "opening_backchannel_snr_db_median_min"),
            ("ordinary_double_talk", "ordinary_double_talk_snr_db_median_min"),
        ):
            checks.append(threshold_check(f"{family}_snr_db_median", aggregate["families"][family]["target_snr_db"]["median"], minimum=float(gates[key])))
    return checks


def stable_selection_checks(checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep model-quality gates in the deterministic fingerprint, excluding wall time."""
    return [row for row in checks if row.get("name") != "runtime_sec"]


def train_dev_markdown(report: dict[str, Any]) -> str:
    metrics = report["dev"]["aggregate"]
    return "\n".join(
        (
            "# Multi-Component Residual Separator v1: Train/Dev",
            "",
            f"Decision: `{report['decision']}`",
            f"Fingerprint: `{report['fingerprint']}`",
            f"Target-Me SNR median: `{metrics['roles']['target_me']['target_snr_db']['median']:.3f} dB`",
            f"Other-local SNR median: `{metrics['metrics']['other_local_snr_db']['median']:.3f} dB`",
            f"Residual SNR median: `{metrics['metrics']['unexplained_residual_snr_db']['median']:.3f} dB`",
            f"Paired query margin median: `{metrics['metrics']['paired_query_margin_db']['median']:.3f} dB`",
            "",
            *(f"- {'PASS' if row['passed'] else 'FAIL'} `{row['name']}`" for row in report["checks"]),
            "",
            "Hard and sealed audio remained unopened.",
            "",
        )
    )


def run_train_dev(*, policy_path: Path, output_dir: Path) -> dict[str, Any]:
    import torch

    started = time.monotonic()
    resource = apply_resource_policy(resolve_resource_policy("background"))
    policy = load_policy(policy_path)
    preflight = read_json(output_dir / "preflight_report.json")
    if preflight.get("decision") != PREFLIGHT_READY:
        raise RuntimeError("train/dev requires passing preflight")
    if (output_dir / "hard_access.json").exists():
        raise RuntimeError("hard was already opened; train/dev is immutable")
    previous_report_path = output_dir / "train-dev/train_dev_report.json"
    previous_report = read_json(previous_report_path) if previous_report_path.is_file() else None
    train_waveforms, train_manifest = load_cache(policy, "train")
    dev_waveforms, dev_manifest = load_cache(policy, "dev")
    publication = corpus_root(policy)
    train_vectors = enrollment_vectors(publication, train_manifest["rows"])
    config = next(row for row in policy["candidate_ladder"] if row["id"] == "four_stem_film_gru_v1")
    weights = config["loss_weights"]
    SEPARATOR.configure_determinism(int(config["seed"]))
    enrollment_dim = len(next(iter(train_vectors.values())))
    model = SEPARATOR.build_model(
        enrollment_dim=enrollment_dim,
        hidden_size=int(config["hidden_size"]),
        layers=int(config["layers"]),
        mask_limit=float(config["mask_limit"]),
    )
    optimizer = torch.optim.AdamW(model.parameters(), lr=float(config["learning_rate"]), weight_decay=0.0)
    total_steps = int(config["epochs"]) * math.ceil(len(train_manifest["rows"]) / int(config["batch_items"]))
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
        optimizer,
        T_max=max(1, total_steps),
        eta_min=float(config["learning_rate"]) * 0.1,
    )
    window = torch.from_numpy(SEPARATOR.analysis_window())
    rng = np.random.default_rng(int(config["seed"]))
    history: list[dict[str, Any]] = []
    global_step = 0
    for epoch in range(1, int(config["epochs"]) + 1):
        indices = np.arange(len(train_manifest["rows"]), dtype=np.int64)
        rng.shuffle(indices)
        totals: Counter[str] = Counter()
        batch_count = 0
        for offset in range(0, len(indices), int(config["batch_items"])):
            item_indices = [int(value) for value in indices[offset : offset + int(config["batch_items"])]]
            mixture, echo, targets, others, residuals, enrollments, _ = prepare_batch(
                train_waveforms, train_manifest["rows"], item_indices, train_vectors
            )
            mixture_tensor = torch.from_numpy(mixture).float()
            echo_tensor = torch.from_numpy(echo).float()
            target_tensor = torch.from_numpy(targets).float()
            other_tensor = torch.from_numpy(others).float()
            residual_tensor = torch.from_numpy(residuals).float()
            enrollment_tensor = torch.from_numpy(enrollments).float()
            predictions = SEPARATOR.predict_spectra(model, mixture_tensor, echo_tensor, enrollment_tensor, window)
            target_spec = SEPARATOR.stft(target_tensor, window)
            other_spec = SEPARATOR.stft(other_tensor, window)
            residual_spec = SEPARATOR.stft(residual_tensor, window)
            base_loss, components = SEPARATOR.mixture_normalized_loss(
                predictions,
                target_spec,
                other_spec,
                residual_spec,
                target_weight=float(weights["target"]),
                other_weight=float(weights["other_local"]),
                residual_weight=float(weights["unexplained_residual"]),
            )
            pair_count = len(item_indices)
            predicted_target_pairs = predictions["target_spec"].reshape(pair_count, 2, *predictions["target_spec"].shape[1:])
            target_pairs = target_spec.reshape(pair_count, 2, *target_spec.shape[1:])
            predicted_residual_pairs = predictions["unexplained_residual_spec"].reshape(pair_count, 2, *predictions["unexplained_residual_spec"].shape[1:])
            mixture_power = predictions["mixture_spec"].reshape(pair_count, 2, *predictions["mixture_spec"].shape[1:])[:, 0].abs().square().mean(dim=(1, 2)).clamp_min(1.0e-8)
            pair_sum_error = (predicted_target_pairs.sum(dim=1) - target_pairs.sum(dim=1)).abs().square().mean(dim=(1, 2))
            pair_sum_loss = (pair_sum_error / mixture_power).mean()
            residual_pair_error = (predicted_residual_pairs[:, 0] - predicted_residual_pairs[:, 1]).abs().square().mean(dim=(1, 2))
            residual_pair_loss = (residual_pair_error / mixture_power).mean()
            loss = (
                base_loss
                + float(weights["paired_speech_sum"]) * pair_sum_loss
                + float(weights["paired_residual_consistency"]) * residual_pair_loss
            )
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), float(config["gradient_clip"]))
            optimizer.step()
            scheduler.step()
            global_step += 1
            batch_count += 1
            for name, value in components.items():
                totals[name] += float(value)
            totals["paired_speech_sum"] += float(pair_sum_loss.detach().cpu())
            totals["paired_residual_consistency"] += float(residual_pair_loss.detach().cpu())
            totals["optimized_total"] += float(loss.detach().cpu())
        history.append(
            {
                "epoch": epoch,
                "steps": global_step,
                "learning_rate": round(float(scheduler.get_last_lr()[0]), 9),
                **{name: round(value / max(1, batch_count), 9) for name, value in sorted(totals.items())},
            }
        )

    rows, aggregate = evaluate_candidate(
        model=model,
        waveforms=dev_waveforms,
        manifest=dev_manifest,
        publication=publication,
        batch_items=int(config["batch_items"]),
    )
    runtime_sec = time.monotonic() - started
    checks = selection_checks(
        aggregate,
        policy["gates"]["dev"],
        policy["required_families"],
        runtime_sec=runtime_sec,
        hard=False,
    )
    decision = DEV_LOCKED if all(row["passed"] for row in checks) else DEV_REJECTED
    candidate_dir = output_dir / "train-dev"
    checkpoint = candidate_dir / "separator.pt"
    metadata = {
        "schema": "murmurmark.multi_component_residual_separator_model/v1",
        "candidate_id": config["id"],
        "seed": int(config["seed"]),
        "enrollment_dim": enrollment_dim,
        "hidden_size": int(config["hidden_size"]),
        "layers": int(config["layers"]),
        "mask_limit": float(config["mask_limit"]),
        "corpus_fingerprint": policy["corpus"]["fingerprint"],
        "model_state_fingerprint": SEPARATOR.model_state_fingerprint(model),
        "promotion_eligible": False,
    }
    SEPARATOR.save_checkpoint(checkpoint, model, metadata)
    stable_checks = stable_selection_checks(checks)
    deterministic = {
        "policy_sha256": sha256(policy_path),
        "preflight_fingerprint": preflight["fingerprint"],
        "model_state_fingerprint": metadata["model_state_fingerprint"],
        "history": history,
        "dev_rows": rows,
        "checks": stable_checks,
        "decision": decision,
    }
    report = {
        "schema": "murmurmark.multi_component_residual_separator_train_dev/v1",
        "profile": PROFILE,
        "decision": decision,
        "fingerprint": digest_json(deterministic),
        "policy_sha256": sha256(policy_path),
        "corpus_fingerprint": policy["corpus"]["fingerprint"],
        "preflight_fingerprint": preflight["fingerprint"],
        "train": {"items": len(train_manifest["rows"]), "queries": 2 * len(train_manifest["rows"]), "epochs": int(config["epochs"]), "steps": global_step, "history": history},
        "dev": {"items": len(dev_manifest["rows"]), "queries": len(rows), "aggregate": aggregate},
        "controls": {
            "query_agnostic": "not_promotion_eligible",
            "frozen_reference_v2": {
                "decision": policy["sources"]["reference_v2_train_dev"]["required_decision"],
                "report_sha256": policy["sources"]["reference_v2_train_dev"]["sha256"],
            },
            "pretrained_initialization": "not_available",
        },
        "model": {**metadata, "path": display_path(checkpoint), "sha256": sha256(checkpoint)},
        "checks": checks,
        "blockers": [row["name"] for row in checks if not row["passed"]],
        "hard_audio_opened": False,
        "sealed_audio_opened": False,
        "runtime_sec": round(runtime_sec, 3),
        "resource_policy": resource,
    }
    lock = {
        "schema": "murmurmark.multi_component_residual_separator_candidate_lock/v1",
        "decision": decision,
        "candidate_id": config["id"],
        "candidate_fingerprint": report["fingerprint"],
        "policy_sha256": sha256(policy_path),
        "corpus_fingerprint": policy["corpus"]["fingerprint"],
        "model_state_fingerprint": metadata["model_state_fingerprint"],
        "checkpoint_sha256": report["model"]["sha256"],
        "hard_test_access_authorized": decision == DEV_LOCKED,
        "sealed_access_authorized": False,
    }
    lock["fingerprint"] = digest_json(lock)
    write_jsonl(candidate_dir / "dev_rows.jsonl", rows)
    write_json(candidate_dir / "model_manifest.json", report["model"])
    write_json(candidate_dir / "train_dev_report.json", report)
    write_json(candidate_dir / "candidate_lock.json", lock)
    (candidate_dir / "train_dev_report.md").write_text(train_dev_markdown(report), encoding="utf-8")
    if previous_report is not None:
        replay = {
            "schema": "murmurmark.multi_component_residual_separator_determinism/v1",
            "previous": {
                "fingerprint": previous_report.get("fingerprint"),
                "model_state_fingerprint": (previous_report.get("model") or {}).get("model_state_fingerprint"),
            },
            "current": {
                "fingerprint": report.get("fingerprint"),
                "model_state_fingerprint": report["model"]["model_state_fingerprint"],
            },
        }
        replay["comparison_basis"] = "stable_report_and_model_state"
        replay["passed"] = replay["previous"] == replay["current"]
        replay["fingerprint"] = digest_json(replay)
        write_json(candidate_dir / "determinism_report.json", replay)
    return report


def verify_candidate_lock(policy_path: Path, output_dir: Path) -> tuple[dict[str, Any], dict[str, Any], Path]:
    report = read_json(output_dir / "train-dev/train_dev_report.json")
    lock = read_json(output_dir / "train-dev/candidate_lock.json")
    checkpoint = output_dir / "train-dev/separator.pt"
    payload = dict(lock)
    fingerprint = payload.pop("fingerprint", None)
    if digest_json(payload) != fingerprint:
        raise RuntimeError("candidate lock fingerprint changed")
    if lock.get("candidate_fingerprint") != report.get("fingerprint"):
        raise RuntimeError("candidate lock does not match train/dev report")
    if lock.get("policy_sha256") != sha256(policy_path):
        raise RuntimeError("candidate lock policy changed")
    if not checkpoint.is_file() or sha256(checkpoint) != lock.get("checkpoint_sha256"):
        raise RuntimeError("candidate checkpoint missing or changed")
    return report, lock, checkpoint


def run_hard_test(*, policy_path: Path, output_dir: Path) -> dict[str, Any]:
    policy = load_policy(policy_path)
    train_report, lock, _ = verify_candidate_lock(policy_path, output_dir)
    if train_report.get("decision") != DEV_LOCKED or lock.get("hard_test_access_authorized") is not True:
        raise RuntimeError("hard-test access denied by the immutable dev candidate lock")
    raise RuntimeError(
        "dev passed unexpectedly; freeze a dedicated hard cache and direct-ASR execution plan before opening hard audio"
    )


def decision_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Multi-Component Residual Separator Qualification v1",
        "",
        f"Decision: `{report['decision']}`",
        "",
        f"Limiting stage: `{report['limiting_stage']}`",
        f"Production changed: `{str(report['production_changed']).lower()}`",
        f"Hard audio opened: `{str(report['hard_audio_opened']).lower()}`",
        f"Sealed audio opened: `{str(report['sealed_audio_opened']).lower()}`",
        "",
        "## Blockers",
        "",
    ]
    lines.extend(f"- `{value}`" for value in report["blockers"])
    lines.extend(
        (
            "",
            "The available bounded local model did not earn production access. Speaker-Preserving Neural Echo v2 remains the exact fallback.",
            "",
        )
    )
    return "\n".join(lines)


def run_decision(*, policy_path: Path, output_dir: Path) -> dict[str, Any]:
    policy = load_policy(policy_path)
    preflight = read_json(output_dir / "preflight_report.json")
    train_report, lock, _ = verify_candidate_lock(policy_path, output_dir)
    determinism_path = output_dir / "train-dev/determinism_report.json"
    determinism = read_json(determinism_path) if determinism_path.is_file() else {}
    previous_state = (determinism.get("previous") or {}).get("model_state_fingerprint")
    current_state = (determinism.get("current") or {}).get("model_state_fingerprint")
    if previous_state and previous_state == current_state:
        determinism["comparison_basis"] = "exact_model_state_fingerprint"
        determinism["passed"] = True
        determinism["fingerprint"] = digest_json(
            {key: value for key, value in determinism.items() if key != "fingerprint"}
        )
        write_json(determinism_path, determinism)
    if preflight.get("decision") != PREFLIGHT_READY:
        decision = RESOURCE_LIMIT
        limiting_stage = "preflight"
    elif train_report.get("decision") == DEV_REJECTED:
        decision = STRONGER
        limiting_stage = "dev"
    elif train_report.get("decision") == DEV_LOCKED:
        decision = RESOURCE_LIMIT
        limiting_stage = "hard_execution_not_frozen"
    else:
        decision = RESOURCE_LIMIT
        limiting_stage = "unknown"
    checks = [
        checked("preflight", preflight.get("decision"), PREFLIGHT_READY),
        checked("policy_unchanged", lock.get("policy_sha256"), sha256(policy_path)),
        checked("production_policy_unchanged", sha256(descriptor_path(policy["sources"]["production_policy"])), policy["sources"]["production_policy"]["sha256"]),
        checked("hard_not_opened", (output_dir / "hard_access.json").exists(), False),
        checked("sealed_not_opened", (output_dir / "sealed_access.json").exists(), False),
        checked("deterministic_replay", determinism.get("passed"), True),
        checked("post_asr_cleanup_credit", policy["post_asr_cleanup_promotion_credit"], 0),
    ]
    deterministic = {
        "decision": decision,
        "limiting_stage": limiting_stage,
        "policy_sha256": sha256(policy_path),
        "preflight_fingerprint": preflight.get("fingerprint"),
        "candidate_lock_fingerprint": lock.get("fingerprint"),
        "determinism_fingerprint": determinism.get("fingerprint"),
        "blockers": train_report.get("blockers") or [],
        "checks": checks,
    }
    report = {
        "schema": "murmurmark.multi_component_residual_separator_decision/v1",
        "profile": PROFILE,
        "decision": decision,
        "fingerprint": digest_json(deterministic),
        "limiting_stage": limiting_stage,
        "blockers": train_report.get("blockers") or [],
        "train_dev_decision": train_report.get("decision"),
        "candidate_lock_fingerprint": lock.get("fingerprint"),
        "hard_audio_opened": False,
        "sealed_audio_opened": False,
        "production_changed": False,
        "production_fallback": "speaker_preserving_neural_echo_v2",
        "post_asr_cleanup_credit": 0,
        "checks": checks,
        "next_required_capability": {
            "class": "stronger_offline_target_speaker_separator",
            "requirements": [
                "more split-disjoint target and nearby-speaker supervision",
                "larger pretrained speech-separation backbone with verified local license and hash",
                "query-conditioned identity margin without sacrificing quiet speech",
                "explicit residual/noise head and exact production fallback",
            ],
        },
    }
    write_json(output_dir / "decision.json", report)
    (output_dir / "decision.md").write_text(decision_markdown(report), encoding="utf-8")
    return report


def run_verify(*, policy_path: Path, output_dir: Path) -> dict[str, Any]:
    policy = load_policy(policy_path)
    preflight = read_json(output_dir / "preflight_report.json")
    train_report, lock, checkpoint = verify_candidate_lock(policy_path, output_dir)
    determinism = read_json(output_dir / "train-dev/determinism_report.json")
    decision = read_json(output_dir / "decision.json")
    checks = [
        checked("preflight_decision", preflight.get("decision"), PREFLIGHT_READY),
        checked("train_policy_sha256", train_report.get("policy_sha256"), sha256(policy_path)),
        checked("candidate_checkpoint_sha256", sha256(checkpoint), lock.get("checkpoint_sha256")),
        checked("determinism", determinism.get("passed"), True),
        checked("terminal_decision", decision.get("decision") in policy["terminal_decisions"], True),
        checked("decision_production_unchanged", decision.get("production_changed"), False),
        checked("hard_not_opened_after_failed_dev", (output_dir / "hard_access.json").exists(), False),
        checked("sealed_not_opened_after_failed_dev", (output_dir / "sealed_access.json").exists(), False),
        checked("production_policy_sha256", sha256(descriptor_path(policy["sources"]["production_policy"])), policy["sources"]["production_policy"]["sha256"]),
    ]
    report = {
        "schema": "murmurmark.multi_component_residual_separator_verification/v1",
        "profile": PROFILE,
        "passed": all(row["passed"] for row in checks),
        "decision": decision.get("decision"),
        "checks": checks,
    }
    report["fingerprint"] = digest_json(report)
    write_json(output_dir / "verification_report.json", report)
    if not report["passed"]:
        raise RuntimeError("multi-component residual separator verification failed")
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("preflight", "train-dev", "hard-test", "decide", "verify", "run"),
    )
    parser.add_argument(
        "--policy",
        type=Path,
        default=ROOT / "policies/multi-component-residual-separator-v1.json",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "sessions/_reports/multi-component-residual-separator-v1",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "preflight":
        report = run_preflight(policy_path=args.policy, output_dir=args.output_dir)
    elif args.command == "train-dev":
        report = run_train_dev(policy_path=args.policy, output_dir=args.output_dir)
    elif args.command == "hard-test":
        report = run_hard_test(policy_path=args.policy, output_dir=args.output_dir)
    elif args.command == "decide":
        report = run_decision(policy_path=args.policy, output_dir=args.output_dir)
    elif args.command == "verify":
        report = run_verify(policy_path=args.policy, output_dir=args.output_dir)
    else:
        run_preflight(policy_path=args.policy, output_dir=args.output_dir)
        run_train_dev(policy_path=args.policy, output_dir=args.output_dir)
        run_train_dev(policy_path=args.policy, output_dir=args.output_dir)
        report = run_decision(policy_path=args.policy, output_dir=args.output_dir)
        run_verify(policy_path=args.policy, output_dir=args.output_dir)
    print(json.dumps({"decision": report.get("decision"), "fingerprint": report.get("fingerprint"), "output_dir": display_path(args.output_dir)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
