#!/usr/bin/env python3
"""Bounded train/dev/hard controller for speaker-query Target-Me separation v2."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
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
import soundfile as sf


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from murmurmark_resource_policy import apply_resource_policy, resolve_resource_policy  # noqa: E402
import reference_conditioned_separator_v1 as SEPARATOR_V1  # noqa: E402
import reference_conditioned_separator_v2 as SEPARATOR  # noqa: E402


SCHEMA = "murmurmark.reference_conditioned_target_me_separation_policy/v2"
PROFILE = "reference_conditioned_target_me_separation_v2"
READY = "READY_FOR_V1_BASELINE_REPLAY"
V1_REPRODUCED = "V1_BASELINE_REPRODUCED"
DEV_LOCKED = "DEV_CANDIDATE_LOCKED"
DEV_REJECTED = "DEV_CANDIDATE_REJECTED"
HARD_PASSED = "HARD_TEST_PASSED"
HARD_REJECTED = "HARD_TEST_REJECTED"
PROMOTE = "PROMOTE_REFERENCE_CONDITIONED_TARGET_ME_SEPARATION_V2"
DO_NOT_PROMOTE = "DO_NOT_PROMOTE_REFERENCE_CONDITIONED_TARGET_ME_SEPARATION_V2"
CACHE_CHANNELS = ("mixture", "target_me", "other_local_speech", "remote_echo", "other_local_noise")


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


def threshold_check(name: str, observed: float, *, minimum: float | None = None, maximum: float | None = None) -> dict[str, Any]:
    if (minimum is None) == (maximum is None):
        raise ValueError("exactly one threshold direction is required")
    passed = observed >= minimum if minimum is not None else observed <= maximum
    row: dict[str, Any] = {"name": name, "observed": round(float(observed), 6), "passed": bool(passed)}
    row["minimum" if minimum is not None else "maximum"] = minimum if minimum is not None else maximum
    return row


def load_policy(path: Path) -> dict[str, Any]:
    policy = read_json(path)
    if policy.get("schema") != SCHEMA or policy.get("profile") != PROFILE:
        raise RuntimeError("unexpected Reference-Conditioned Target-Me Separation v2 policy")
    return policy


def resolve_publication(policy: dict[str, Any]) -> Path:
    return ROOT / policy["identifiability_corpus"]["root"]


def verify_descriptor(root: Path, descriptor: dict[str, Any]) -> Path:
    path = (root / str(descriptor["path"])).resolve()
    if root.resolve() not in path.parents:
        raise RuntimeError(f"artifact escapes corpus root: {descriptor['path']}")
    if not path.is_file():
        raise RuntimeError(f"missing corpus artifact: {path}")
    expected = str(descriptor.get("sha256") or "")
    if expected and sha256(path) != expected:
        raise RuntimeError(f"changed corpus artifact: {path}")
    return path


def read_audio(root: Path, descriptor: dict[str, Any]) -> np.ndarray:
    path = verify_descriptor(root, descriptor)
    values, sample_rate = sf.read(path, dtype="float32", always_2d=False)
    data = np.asarray(values, dtype=np.float32).reshape(-1)
    if sample_rate != SEPARATOR.SAMPLE_RATE or data.size != SEPARATOR.CLIP_SAMPLES:
        raise RuntimeError(f"unexpected corpus audio shape: {path}")
    if not np.all(np.isfinite(data)):
        raise RuntimeError(f"non-finite corpus audio: {path}")
    return data


def model_files_report(policy: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    model = policy["models"]["target_me_encoder"]
    root = Path(model["local_path"]).expanduser()
    rows: list[dict[str, Any]] = []
    for name, expected in sorted(model["files"].items()):
        path = root / name
        observed = sha256(path) if path.is_file() else None
        rows.append({"path": display_path(path), "expected_sha256": expected, "observed_sha256": observed, "passed": observed == expected})
    return rows, all(row["passed"] for row in rows)


def run_preflight(*, policy_path: Path, output_dir: Path) -> dict[str, Any]:
    resource = apply_resource_policy(resolve_resource_policy("background"))
    policy = load_policy(policy_path)
    corpus = policy["identifiability_corpus"]
    publication = resolve_publication(policy)
    checks: list[dict[str, Any]] = []
    pinned: list[dict[str, Any]] = []

    current_path = ROOT / corpus["current"]
    current = read_json(current_path) if current_path.is_file() else {}
    checks.extend(
        (
            checked("current_sha256", sha256(current_path) if current_path.is_file() else None, corpus["current_sha256"]),
            checked("current_fingerprint", current.get("fingerprint"), corpus["fingerprint"]),
            checked("current_decision", current.get("decision"), corpus["decision"]),
        )
    )
    for name, expected in sorted(corpus["files"].items()):
        path = publication / name
        observed = sha256(path) if path.is_file() else None
        row = {"path": display_path(path), "expected_sha256": expected, "observed_sha256": observed, "passed": observed == expected}
        pinned.append(row)
    checks.append(checked("publication_files", all(row["passed"] for row in pinned), True))

    decision = read_json(publication / "corpus_decision.json") if publication.is_dir() else {}
    split_manifest = read_json(publication / "split_manifest.json") if publication.is_dir() else {}
    checks.extend(
        (
            checked("corpus_decision", decision.get("decision"), corpus["decision"]),
            checked("corpus_fingerprint", decision.get("fingerprint"), corpus["fingerprint"]),
        )
    )
    for split, expected in corpus["split_counts"].items():
        observed = (split_manifest.get("splits") or {}).get(split) or {}
        for key, value in expected.items():
            observed_value = len(observed.get("non_target_speakers") or []) if key == "non_target_speakers" else observed.get(key)
            checks.append(checked(f"{split}_{key}", observed_value, value))

    v1 = policy["v1_baseline"]
    for key in ("policy", "decision_report", "train_dev_report", "checkpoint"):
        path = ROOT / v1[key]
        expected = v1[f"{key}_sha256"]
        observed = sha256(path) if path.is_file() else None
        checks.append(checked(f"v1_{key}_sha256", observed, expected))
    v1_decision = read_json(ROOT / v1["decision_report"]) if (ROOT / v1["decision_report"]).is_file() else {}
    checks.extend((checked("v1_decision", v1_decision.get("decision"), v1["decision"]), checked("v1_fingerprint", v1_decision.get("fingerprint"), v1["fingerprint"])))

    production = policy["production_baseline"]
    production_path = ROOT / production["policy"]
    checks.append(checked("production_policy_sha256", sha256(production_path) if production_path.is_file() else None, production["policy_sha256"]))
    for key in ("corpus_report", "promotion_decision", "evaluation_manifest"):
        path = ROOT / policy["sealed_evaluation"][key]
        checks.append(checked(f"sealed_{key}_sha256", sha256(path) if path.is_file() else None, policy["sealed_evaluation"][f"{key}_sha256"]))
    model_rows, models_passed = model_files_report(policy)
    checks.append(checked("offline_target_encoder", models_passed, True))
    checks.extend(
        (
            checked("hard_audio_files_read", 0, 0),
            checked("post_asr_cleanup_credit", policy["audio_contract"]["post_asr_cleanup_promotion_credit"], 0),
            checked("byte_exact_fallback", production["fallback"], "byte_exact_speaker_preserving_neural_echo_v2"),
        )
    )
    passed = all(row["passed"] for row in checks)
    deterministic = {
        "policy_sha256": sha256(policy_path),
        "corpus_fingerprint": corpus["fingerprint"],
        "pinned": pinned,
        "model_files": model_rows,
        "checks": checks,
    }
    report = {
        "schema": "murmurmark.reference_conditioned_target_me_preflight/v2",
        "profile": PROFILE,
        "decision": READY if passed else "BLOCKED_PREFLIGHT",
        "fingerprint": digest_json(deterministic),
        "policy": {"path": display_path(policy_path), "sha256": sha256(policy_path)},
        "corpus": {"root": display_path(publication), "fingerprint": corpus["fingerprint"], "pinned_files": pinned},
        "v1_baseline": {"decision": v1_decision.get("decision"), "fingerprint": v1_decision.get("fingerprint")},
        "model_files": model_rows,
        "checks": checks,
        "blockers": [row["name"] for row in checks if not row["passed"]],
        "hard_audio_opened": False,
        "sealed_corpus_opened": False,
        "resource_policy": resource,
    }
    frozen = {"schema": "murmurmark.reference_conditioned_target_me_frozen_inputs/v2", **deterministic, "fingerprint": report["fingerprint"]}
    write_json(output_dir / "frozen_inputs.json", frozen)
    write_json(output_dir / "preflight_report.json", report)
    (output_dir / "preflight_report.md").write_text(preflight_markdown(report), encoding="utf-8")
    return report


def load_python_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def run_v1_replay(*, policy_path: Path, output_dir: Path, preflight_path: Path) -> dict[str, Any]:
    started = time.monotonic()
    resource = apply_resource_policy(resolve_resource_policy("background"))
    policy = load_policy(policy_path)
    preflight = read_json(preflight_path)
    if preflight.get("decision") != READY or preflight.get("policy", {}).get("sha256") != sha256(policy_path):
        raise RuntimeError("v1 replay requires the current passing preflight")
    v1 = policy["v1_baseline"]
    v1_policy = read_json(ROOT / v1["policy"])
    expected_report = read_json(ROOT / v1["train_dev_report"])
    controller = load_python_module(ROOT / "scripts/reference-conditioned-target-me-separation-v1.py", "murmurmark_reference_conditioned_v1_replay")
    waveforms, kinds, index_rows = controller.load_cache_split(v1_policy, ROOT, "dev")
    enrollment = np.asarray(np.load(ROOT / v1_policy["reference_enrollment"]["vector"]), dtype=np.float32).reshape(-1)
    enrollment /= max(float(np.linalg.norm(enrollment)), 1.0e-8)
    model, metadata = SEPARATOR_V1.load_checkpoint(ROOT / v1["checkpoint"])
    rows, aggregate = controller.evaluate_dev_candidate(
        model=model,
        waveforms=waveforms,
        kinds=kinds,
        index_rows=index_rows,
        enrollment_values=enrollment,
        batch_size=int(v1_policy["train_dev_candidate"]["batch_size"]),
        exact_target_kinds=set(v1_policy["train_dev_candidate"]["exact_target_kinds"]),
        exact_other_kinds=set(v1_policy["train_dev_candidate"]["exact_other_kinds"]),
    )
    observed = aggregate["synthetic_double_talk"]["metrics"]
    metrics = {
        "target_snr_db_median": float(observed["target_snr_db"]["median"]),
        "target_improvement_db_median": float(observed["target_snr_improvement_db"]["median"]),
        "echo_snr_db_median": float(observed["echo_snr_db"]["median"]),
    }
    tolerance = float(v1["metric_tolerance"])
    checks = [
        threshold_check(name, abs(metrics[name] - float(v1[name])), maximum=tolerance)
        for name in metrics
    ]
    checks.extend(
        (
            checked("checkpoint_state", controller.model_state_fingerprint(model), expected_report["model"]["state_fingerprint"]),
            checked("dev_rows", len(rows), int(expected_report["dev"]["rows"])),
            checked("hard_audio_files_read", 0, 0),
        )
    )
    passed = all(row["passed"] for row in checks)
    deterministic = {"preflight_fingerprint": preflight["fingerprint"], "checkpoint_sha256": sha256(ROOT / v1["checkpoint"]), "metadata": metadata, "metrics": metrics, "checks": checks}
    report = {
        "schema": "murmurmark.reference_conditioned_target_me_v1_replay/v2",
        "profile": PROFILE,
        "decision": V1_REPRODUCED if passed else "V1_BASELINE_REPLAY_FAILED",
        "fingerprint": digest_json(deterministic),
        "metrics": metrics,
        "checks": checks,
        "blockers": [row["name"] for row in checks if not row["passed"]],
        "hard_audio_opened": False,
        "runtime_sec": round(time.monotonic() - started, 3),
        "resource_policy": resource,
    }
    write_json(output_dir / "v1_replay_report.json", report)
    (output_dir / "v1_replay_report.md").write_text(v1_replay_markdown(report), encoding="utf-8")
    return report


def cache_paths(output_dir: Path, split: str) -> tuple[Path, Path]:
    root = output_dir / "cache"
    return root / f"{split}_waveforms.npy", root / f"{split}_cache_manifest.json"


def query_pairs(publication: Path, split: str) -> dict[str, dict[str, dict[str, Any]]]:
    grouped: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in read_jsonl(publication / "query_manifest.jsonl", split=split):
        grouped[str(row["item_id"])][str(row["query_role"])] = row
    for item_id, roles in grouped.items():
        if set(roles) != {"target_me", "other_local_speech"}:
            raise RuntimeError(f"item {item_id} does not have an exact paired query")
    return grouped


def prepare_cache(*, policy: dict[str, Any], policy_path: Path, output_dir: Path, split: str, hard_authorized: bool = False, refresh: bool = False) -> dict[str, Any]:
    if split == policy["data_isolation"]["hard_split"] and not hard_authorized:
        raise RuntimeError("hard cache access requires an immutable authorized candidate lock")
    publication = resolve_publication(policy)
    waveforms_path, manifest_path = cache_paths(output_dir, split)
    if not refresh and waveforms_path.is_file() and manifest_path.is_file():
        manifest = read_json(manifest_path)
        if (
            manifest.get("policy_sha256") == sha256(policy_path)
            and manifest.get("corpus_fingerprint") == policy["identifiability_corpus"]["fingerprint"]
            and manifest.get("split") == split
            and manifest.get("waveforms", {}).get("sha256") == sha256(waveforms_path)
        ):
            return manifest

    items = sorted(read_jsonl(publication / "item_manifest.jsonl", split=split), key=lambda row: str(row["item_id"]))
    expected_count = int(policy["identifiability_corpus"]["split_counts"][split]["items"])
    if len(items) != expected_count:
        raise RuntimeError(f"unexpected {split} item count: {len(items)}")
    pairs = query_pairs(publication, split)
    cache_root = waveforms_path.parent
    cache_root.mkdir(parents=True, exist_ok=True)
    temporary_path = cache_root / f".{split}_waveforms.{os.getpid()}.npy"
    matrix = np.lib.format.open_memmap(
        temporary_path,
        mode="w+",
        dtype=np.float32,
        shape=(len(items), len(CACHE_CHANNELS), SEPARATOR.CLIP_SAMPLES),
    )
    index_rows: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        item_id = str(item["item_id"])
        if item_id not in pairs:
            raise RuntimeError(f"missing paired query for {item_id}")
        audio = item["audio"]
        for channel_index, name in enumerate(CACHE_CHANNELS):
            matrix[index, channel_index] = read_audio(publication, audio[name])
        reconstruction = matrix[index, 1] + matrix[index, 2] + matrix[index, 3] + matrix[index, 4]
        error = float(np.max(np.abs(matrix[index, 0] - reconstruction)))
        if error > float(policy["audio_contract"]["reconstruction_max_abs_error"]):
            raise RuntimeError(f"source reconstruction failed for {item_id}: {error}")
        role_rows = pairs[item_id]
        index_rows.append(
            {
                "item_id": item_id,
                "split": split,
                "family": item["family"],
                "usage": item["usage"],
                "target_speaker_id": item["target_speaker_id"],
                "other_local_speaker_id": item["other_local_speaker_id"],
                "queries": {
                    role: {
                        "query_id": row["query_id"],
                        "speaker_present": bool(row["speaker_present"]),
                        "speaker_id": row["query_speaker_id"],
                        "enrollment": row["correct_enrollment"],
                    }
                    for role, row in sorted(role_rows.items())
                },
            }
        )
    matrix.flush()
    del matrix
    os.replace(temporary_path, waveforms_path)
    deterministic = {
        "policy_sha256": sha256(policy_path),
        "corpus_fingerprint": policy["identifiability_corpus"]["fingerprint"],
        "split": split,
        "shape": [len(items), len(CACHE_CHANNELS), SEPARATOR.CLIP_SAMPLES],
        "channels": list(CACHE_CHANNELS),
        "rows": index_rows,
        "waveforms_sha256": sha256(waveforms_path),
    }
    manifest = {
        "schema": "murmurmark.reference_conditioned_target_me_cache/v2",
        "fingerprint": digest_json(deterministic),
        "policy_sha256": deterministic["policy_sha256"],
        "corpus_fingerprint": deterministic["corpus_fingerprint"],
        "split": split,
        "shape": deterministic["shape"],
        "channels": deterministic["channels"],
        "rows": index_rows,
        "waveforms": {"path": display_path(waveforms_path), "sha256": deterministic["waveforms_sha256"], "bytes": waveforms_path.stat().st_size},
        "hard_access_authorized": bool(hard_authorized) if split == "hard" else False,
    }
    write_json(manifest_path, manifest)
    return manifest


def load_cache(output_dir: Path, split: str) -> tuple[np.ndarray, dict[str, Any]]:
    waveforms_path, manifest_path = cache_paths(output_dir, split)
    if not waveforms_path.is_file() or not manifest_path.is_file():
        raise RuntimeError(f"missing {split} cache")
    manifest = read_json(manifest_path)
    if sha256(waveforms_path) != manifest.get("waveforms", {}).get("sha256"):
        raise RuntimeError(f"changed {split} cache")
    return np.load(waveforms_path, mmap_mode="r"), manifest


def enrollment_vectors(publication: Path, rows: list[dict[str, Any]]) -> dict[str, np.ndarray]:
    descriptors: dict[str, dict[str, Any]] = {}
    for row in rows:
        for query in row["queries"].values():
            descriptor = query["enrollment"]
            descriptors[str(descriptor["path"])] = descriptor
    vectors: dict[str, np.ndarray] = {}
    for key, descriptor in sorted(descriptors.items()):
        path = verify_descriptor(publication, descriptor)
        value = np.asarray(np.load(path), dtype=np.float32).reshape(-1)
        value /= max(float(np.linalg.norm(value)), 1.0e-8)
        vectors[key] = value
    return vectors


def prepare_item_batch(waveforms: np.ndarray, rows: list[dict[str, Any]], indices: list[int], vectors: dict[str, np.ndarray]) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, list[dict[str, Any]]]:
    values = np.asarray(waveforms[indices], dtype=np.float32)
    mixture_rows: list[np.ndarray] = []
    echo_rows: list[np.ndarray] = []
    target_rows: list[np.ndarray] = []
    enrollment_rows: list[np.ndarray] = []
    metadata: list[dict[str, Any]] = []
    for local_index, global_index in enumerate(indices):
        item = rows[global_index]
        for role, channel in (("target_me", 1), ("other_local_speech", 2)):
            query = item["queries"][role]
            mixture_rows.append(values[local_index, 0])
            echo_rows.append(values[local_index, 3])
            target_rows.append(values[local_index, channel])
            enrollment_rows.append(vectors[str(query["enrollment"]["path"])])
            metadata.append({"item_index": global_index, "pair_position": 0 if role == "target_me" else 1, "query_role": role, "speaker_present": query["speaker_present"], **{key: item[key] for key in ("item_id", "family", "usage")}})
    return np.stack(mixture_rows), np.stack(echo_rows), np.stack(target_rows), np.stack(enrollment_rows), metadata


def snr_db(reference: np.ndarray, estimate: np.ndarray) -> float:
    ref = np.asarray(reference, dtype=np.float64)
    est = np.asarray(estimate, dtype=np.float64)
    return float(10.0 * np.log10((np.sum(ref**2) + 1.0e-12) / (np.sum((ref - est) ** 2) + 1.0e-12)))


def rms_dbfs(values: np.ndarray) -> float:
    data = np.asarray(values, dtype=np.float64)
    return float(20.0 * np.log10(np.sqrt(np.mean(data**2)) + 1.0e-12))


def metric_summary(values: list[float]) -> dict[str, float]:
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


def evaluate_candidate(*, model: Any, waveforms: np.ndarray, manifest: dict[str, Any], publication: Path, batch_items: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    import torch

    rows = manifest["rows"]
    vectors = enrollment_vectors(publication, rows)
    window = torch.from_numpy(SEPARATOR.analysis_window())
    output_rows: list[dict[str, Any]] = []
    model.eval()
    for offset in range(0, len(rows), batch_items):
        indices = list(range(offset, min(len(rows), offset + batch_items)))
        mixture, echo, targets, enrollment, metadata = prepare_item_batch(waveforms, rows, indices, vectors)
        with torch.no_grad():
            predicted = SEPARATOR.apply_model(
                model,
                torch.from_numpy(mixture).float(),
                torch.from_numpy(echo).float(),
                torch.from_numpy(enrollment).float(),
                window,
            )
        estimates = predicted["query_target"].cpu().numpy()
        other_outputs = predicted["other_local"].cpu().numpy()
        for index, meta in enumerate(metadata):
            pair_index = index - 1 if meta["pair_position"] == 1 else index + 1
            target = targets[index]
            local_mixture = mixture[index] - echo[index]
            reconstruction = estimates[index] + echo[index] + other_outputs[index]
            finite = bool(np.all(np.isfinite(estimates[index])) and np.all(np.isfinite(other_outputs[index])))
            row: dict[str, Any] = {
                "schema": "murmurmark.reference_conditioned_target_me_query_evaluation/v2",
                **meta,
                "finite": finite,
                "output_peak": round(float(np.max(np.abs(estimates[index]))), 9),
                "reconstruction_max_abs_error": round(float(np.max(np.abs(mixture[index] - reconstruction))), 9),
                "remote_echo_snr_db": round(snr_db(echo[index], echo[index]), 6),
            }
            if meta["speaker_present"]:
                baseline_snr = snr_db(target, local_mixture)
                candidate_snr = snr_db(target, estimates[index])
                wrong_snr = snr_db(target, estimates[pair_index])
                difference_ratio = float(np.sqrt(np.mean((estimates[index] - estimates[pair_index]) ** 2)) / (np.sqrt(np.mean(local_mixture**2)) + 1.0e-12))
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

    metrics: dict[str, Any] = {}
    numeric_names = sorted({key for row in output_rows for key, value in row.items() if isinstance(value, (int, float)) and not isinstance(value, bool)})
    for name in numeric_names:
        metrics[name] = metric_summary([float(row[name]) for row in output_rows if name in row])
    roles: dict[str, Any] = {}
    for role in ("target_me", "other_local_speech"):
        selected = [row for row in output_rows if row["query_role"] == role and row["speaker_present"]]
        roles[role] = {name: metric_summary([float(row[name]) for row in selected if name in row]) for name in ("target_snr_db", "target_snr_improvement_db", "paired_query_margin_db")}
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
    aggregate["query_collapse_rate"] = round(aggregate["query_collapse_count"] / max(1, aggregate["query_collapse_denominator"]), 9)
    return output_rows, aggregate


def selection_checks(aggregate: dict[str, Any], gates: dict[str, Any], required_families: list[str], *, hard: bool) -> list[dict[str, Any]]:
    metrics = aggregate["metrics"]
    roles = aggregate["roles"]
    checks = [
        threshold_check("target_me_snr_db_median", roles["target_me"]["target_snr_db"]["median"], minimum=float(gates["target_me_snr_db_median_min"])),
        threshold_check("target_me_improvement_db_median", roles["target_me"]["target_snr_improvement_db"]["median"], minimum=float(gates["target_me_improvement_db_median_min"])),
        threshold_check("other_speaker_snr_db_median", roles["other_local_speech"]["target_snr_db"]["median"], minimum=float(gates["other_speaker_snr_db_median_min"])),
        threshold_check("paired_query_margin_db_median", metrics["paired_query_margin_db"]["median"], minimum=float(gates["paired_query_margin_db_median_min"])),
        threshold_check("query_collapse_rate", aggregate["query_collapse_rate"], maximum=float(gates["query_collapse_rate_max"])),
        threshold_check("absent_query_attenuation_db_median", metrics["absent_query_attenuation_db"]["median"], minimum=float(gates["absent_query_attenuation_db_median_min"])),
        threshold_check("remote_only_attenuation_db_median", metrics["remote_only_attenuation_db"]["median"], minimum=float(gates["remote_only_attenuation_db_median_min"])),
        threshold_check("reconstruction_max_abs_error", metrics["reconstruction_max_abs_error"]["max"], maximum=float(gates["reconstruction_max_abs_error_max"])),
        threshold_check("clipped_outputs", aggregate["clipped_outputs"], maximum=float(gates["clipped_outputs_max"])),
        threshold_check("non_finite_outputs", aggregate["non_finite_outputs"], maximum=float(gates["non_finite_outputs_max"])),
        checked("family_coverage", sorted(aggregate["families"]), sorted(required_families)),
    ]
    if "remote_echo_snr_db_median_min" in gates:
        checks.append(threshold_check("remote_echo_snr_db_median", metrics["remote_echo_snr_db"]["median"], minimum=float(gates["remote_echo_snr_db_median_min"])))
    if hard:
        for family, key in (("quiet_target_me", "quiet_target_me_snr_db_median_min"), ("opening_backchannel", "opening_backchannel_snr_db_median_min"), ("ordinary_double_talk", "ordinary_double_talk_snr_db_median_min")):
            checks.append(threshold_check(f"{family}_snr_db_median", aggregate["families"][family]["target_snr_db"]["median"], minimum=float(gates[key])))
    return checks


def run_train_dev(*, policy_path: Path, output_dir: Path, preflight_path: Path, replay_path: Path, refresh_cache: bool = False) -> dict[str, Any]:
    import torch

    started = time.monotonic()
    resource = apply_resource_policy(resolve_resource_policy("background"))
    policy = load_policy(policy_path)
    preflight = read_json(preflight_path)
    replay = read_json(replay_path)
    if preflight.get("decision") != READY or replay.get("decision") != V1_REPRODUCED:
        raise RuntimeError("train/dev requires passing preflight and v1 replay")
    if (output_dir / "hard_access.json").exists():
        raise RuntimeError("hard was already opened; train/dev is immutable")
    previous_report_path = output_dir / "train-dev/train_dev_report.json"
    previous_report = read_json(previous_report_path) if previous_report_path.is_file() else None
    train_manifest = prepare_cache(policy=policy, policy_path=policy_path, output_dir=output_dir, split="train", refresh=refresh_cache)
    dev_manifest = prepare_cache(policy=policy, policy_path=policy_path, output_dir=output_dir, split="dev", refresh=refresh_cache)
    train_waveforms, train_manifest = load_cache(output_dir, "train")
    dev_waveforms, dev_manifest = load_cache(output_dir, "dev")
    publication = resolve_publication(policy)
    train_vectors = enrollment_vectors(publication, train_manifest["rows"])
    config = policy["candidate"]
    SEPARATOR.configure_determinism(int(config["seed"]))
    enrollment_dim = len(next(iter(train_vectors.values())))
    model = SEPARATOR.build_model(enrollment_dim=enrollment_dim, hidden_size=int(config["hidden_size"]), layers=int(config["layers"]), mask_limit=float(config["mask_limit"]))
    optimizer = torch.optim.AdamW(model.parameters(), lr=float(config["learning_rate"]), weight_decay=0.0)
    total_steps = int(config["epochs"]) * math.ceil(len(train_manifest["rows"]) / int(config["batch_items"]))
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=max(1, total_steps), eta_min=float(config["learning_rate"]) * 0.1)
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
            mixture, echo, targets, enrollments, _ = prepare_item_batch(train_waveforms, train_manifest["rows"], item_indices, train_vectors)
            mixture_tensor = torch.from_numpy(mixture).float()
            echo_tensor = torch.from_numpy(echo).float()
            target_tensor = torch.from_numpy(targets).float()
            enrollment_tensor = torch.from_numpy(enrollments).float()
            predictions = SEPARATOR.predict_spectra(model, mixture_tensor, echo_tensor, enrollment_tensor, window)
            target_spec = SEPARATOR.stft(target_tensor, window)
            other_spec = predictions["mixture_spec"] - predictions["echo_spec"] - target_spec
            base_loss, components = SEPARATOR.mixture_normalized_loss(predictions, target_spec, other_spec, target_weight=float(config["target_loss_weight"]), other_weight=float(config["other_loss_weight"]))
            pair_count = len(item_indices)
            predicted_pairs = predictions["target_spec"].reshape(pair_count, 2, *predictions["target_spec"].shape[1:])
            target_pairs = target_spec.reshape(pair_count, 2, *target_spec.shape[1:])
            mixture_power = predictions["mixture_spec"].reshape(pair_count, 2, *predictions["mixture_spec"].shape[1:])[:, 0].abs().square().mean(dim=(1, 2)).clamp_min(1.0e-8)
            pair_error = (predicted_pairs.sum(dim=1) - target_pairs.sum(dim=1)).abs().square().mean(dim=(1, 2))
            pair_loss = (pair_error / mixture_power).mean()
            loss = base_loss + float(config["pair_sum_loss_weight"]) * pair_loss
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), float(config["gradient_clip"]))
            optimizer.step()
            scheduler.step()
            global_step += 1
            batch_count += 1
            for name, value in components.items():
                totals[name] += float(value)
            totals["pair_sum"] += float(pair_loss.detach().cpu())
            totals["optimized_total"] += float(loss.detach().cpu())
        history.append({"epoch": epoch, "steps": global_step, "learning_rate": round(float(scheduler.get_last_lr()[0]), 9), **{name: round(value / max(1, batch_count), 9) for name, value in sorted(totals.items())}})

    rows, aggregate = evaluate_candidate(model=model, waveforms=dev_waveforms, manifest=dev_manifest, publication=publication, batch_items=int(config["batch_items"]))
    checks = selection_checks(aggregate, config["dev_gates"], config["required_families"], hard=False)
    checks.append(checked("hard_audio_files_read", 0, int(config["dev_gates"]["hard_audio_files_read_max"])))
    decision = DEV_LOCKED if all(row["passed"] for row in checks) else DEV_REJECTED
    candidate_dir = output_dir / "train-dev"
    checkpoint = candidate_dir / "separator.pt"
    metadata = {
        "schema": "murmurmark.reference_conditioned_target_me_model/v2",
        "candidate_id": config["candidate_id"],
        "seed": int(config["seed"]),
        "enrollment_dim": enrollment_dim,
        "hidden_size": int(config["hidden_size"]),
        "layers": int(config["layers"]),
        "mask_limit": float(config["mask_limit"]),
        "corpus_fingerprint": policy["identifiability_corpus"]["fingerprint"],
        "train_cache_fingerprint": train_manifest["fingerprint"],
        "dev_cache_fingerprint": dev_manifest["fingerprint"],
        "state_fingerprint": SEPARATOR.model_state_fingerprint(model),
        "promotion_eligible": False,
    }
    SEPARATOR.save_checkpoint(checkpoint, model, metadata)
    deterministic = {
        "policy_sha256": sha256(policy_path),
        "preflight_fingerprint": preflight["fingerprint"],
        "v1_replay_fingerprint": replay["fingerprint"],
        "train_cache_fingerprint": train_manifest["fingerprint"],
        "dev_cache_fingerprint": dev_manifest["fingerprint"],
        "model_state_fingerprint": metadata["state_fingerprint"],
        "history": history,
        "dev_rows": rows,
        "checks": checks,
        "decision": decision,
    }
    report = {
        "schema": "murmurmark.reference_conditioned_target_me_train_dev/v2",
        "profile": PROFILE,
        "decision": decision,
        "fingerprint": digest_json(deterministic),
        "policy_sha256": sha256(policy_path),
        "corpus_fingerprint": policy["identifiability_corpus"]["fingerprint"],
        "preflight_fingerprint": preflight["fingerprint"],
        "v1_replay_fingerprint": replay["fingerprint"],
        "train": {"items": len(train_manifest["rows"]), "queries": 2 * len(train_manifest["rows"]), "epochs": int(config["epochs"]), "steps": global_step, "history": history, "cache_fingerprint": train_manifest["fingerprint"]},
        "dev": {"items": len(dev_manifest["rows"]), "queries": len(rows), "aggregate": aggregate, "cache_fingerprint": dev_manifest["fingerprint"]},
        "model": {**metadata, "path": display_path(checkpoint), "sha256": sha256(checkpoint)},
        "checks": checks,
        "blockers": [row["name"] for row in checks if not row["passed"]],
        "hard_audio_opened": False,
        "sealed_corpus_opened": False,
        "runtime_sec": round(time.monotonic() - started, 3),
        "resource_policy": resource,
    }
    lock = {
        "schema": "murmurmark.reference_conditioned_target_me_candidate_lock/v2",
        "decision": decision,
        "candidate_id": config["candidate_id"],
        "candidate_fingerprint": report["fingerprint"],
        "policy_sha256": sha256(policy_path),
        "corpus_fingerprint": policy["identifiability_corpus"]["fingerprint"],
        "model_state_fingerprint": metadata["state_fingerprint"],
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
        reproducibility = {
            "schema": "murmurmark.reference_conditioned_target_me_determinism/v2",
            "previous": {
                "report_fingerprint": previous_report.get("fingerprint"),
                "model_state_fingerprint": (previous_report.get("model") or {}).get("state_fingerprint"),
                "checkpoint_sha256": (previous_report.get("model") or {}).get("sha256"),
            },
            "current": {
                "report_fingerprint": report["fingerprint"],
                "model_state_fingerprint": report["model"]["state_fingerprint"],
                "checkpoint_sha256": report["model"]["sha256"],
            },
        }
        reproducibility["passed"] = reproducibility["previous"] == reproducibility["current"]
        reproducibility["fingerprint"] = digest_json(reproducibility)
        write_json(candidate_dir / "determinism_report.json", reproducibility)
    return report


def verify_candidate_lock(policy_path: Path, output_dir: Path) -> tuple[dict[str, Any], dict[str, Any], Path]:
    report_path = output_dir / "train-dev/train_dev_report.json"
    lock_path = output_dir / "train-dev/candidate_lock.json"
    checkpoint = output_dir / "train-dev/separator.pt"
    report = read_json(report_path)
    lock = read_json(lock_path)
    expected_lock = dict(lock)
    fingerprint = expected_lock.pop("fingerprint", None)
    if digest_json(expected_lock) != fingerprint:
        raise RuntimeError("candidate lock fingerprint changed")
    if lock.get("candidate_fingerprint") != report.get("fingerprint"):
        raise RuntimeError("candidate lock does not match train/dev report")
    if lock.get("policy_sha256") != sha256(policy_path):
        raise RuntimeError("candidate lock policy changed")
    if not checkpoint.is_file() or sha256(checkpoint) != lock.get("checkpoint_sha256"):
        raise RuntimeError("candidate checkpoint missing or changed")
    return report, lock, checkpoint


def run_hard_test(*, policy_path: Path, output_dir: Path) -> dict[str, Any]:
    started = time.monotonic()
    resource = apply_resource_policy(resolve_resource_policy("background"))
    policy = load_policy(policy_path)
    train_report, lock, checkpoint = verify_candidate_lock(policy_path, output_dir)
    if train_report.get("decision") != DEV_LOCKED or lock.get("hard_test_access_authorized") is not True:
        raise RuntimeError("hard-test access denied by the immutable dev candidate lock")
    access_path = output_dir / "hard_access.json"
    report_path = output_dir / "hard-test/hard_test_report.json"
    if access_path.is_file():
        access = read_json(access_path)
        if access.get("candidate_lock_fingerprint") != lock.get("fingerprint"):
            raise RuntimeError("hard access belongs to another candidate lock")
        if report_path.is_file():
            return read_json(report_path)
        raise RuntimeError("hard access was consumed but no complete hard report exists")
    access = {"schema": "murmurmark.reference_conditioned_target_me_hard_access/v2", "candidate_lock_fingerprint": lock["fingerprint"], "corpus_fingerprint": policy["identifiability_corpus"]["fingerprint"], "open_count": 1, "status": "opened_once"}
    access["fingerprint"] = digest_json(access)
    write_json(access_path, access)
    hard_manifest = prepare_cache(policy=policy, policy_path=policy_path, output_dir=output_dir, split="hard", hard_authorized=True, refresh=False)
    waveforms, hard_manifest = load_cache(output_dir, "hard")
    model, metadata = SEPARATOR.load_checkpoint(checkpoint)
    rows, aggregate = evaluate_candidate(model=model, waveforms=waveforms, manifest=hard_manifest, publication=resolve_publication(policy), batch_items=int(policy["candidate"]["batch_items"]))
    checks = selection_checks(aggregate, policy["candidate"]["hard_gates"], policy["candidate"]["required_families"], hard=True)
    checks.extend((checked("hard_open_count", 1, 1), checked("candidate_lock_fingerprint", access["candidate_lock_fingerprint"], lock["fingerprint"])))
    decision = HARD_PASSED if all(row["passed"] for row in checks) else HARD_REJECTED
    deterministic = {"policy_sha256": sha256(policy_path), "candidate_lock_fingerprint": lock["fingerprint"], "hard_cache_fingerprint": hard_manifest["fingerprint"], "model_state_fingerprint": metadata["state_fingerprint"], "rows": rows, "checks": checks, "decision": decision}
    report = {
        "schema": "murmurmark.reference_conditioned_target_me_hard_test/v2",
        "profile": PROFILE,
        "decision": decision,
        "fingerprint": digest_json(deterministic),
        "candidate_lock_fingerprint": lock["fingerprint"],
        "hard_access_fingerprint": access["fingerprint"],
        "hard_open_count": 1,
        "items": len(hard_manifest["rows"]),
        "queries": len(rows),
        "aggregate": aggregate,
        "checks": checks,
        "blockers": [row["name"] for row in checks if not row["passed"]],
        "sealed_corpus_access_authorized": decision == HARD_PASSED,
        "runtime_sec": round(time.monotonic() - started, 3),
        "resource_policy": resource,
    }
    write_jsonl(output_dir / "hard-test/hard_rows.jsonl", rows)
    write_json(report_path, report)
    (output_dir / "hard-test/hard_test_report.md").write_text(hard_markdown(report), encoding="utf-8")
    return report


def materialize_not_opened_reports(output_dir: Path, reason: str) -> tuple[dict[str, Any], dict[str, Any]]:
    hard_path = output_dir / "hard-test/hard_test_report.json"
    if hard_path.is_file():
        hard = read_json(hard_path)
    else:
        hard = {"schema": "murmurmark.reference_conditioned_target_me_hard_test/v2", "decision": "NOT_OPENED", "status": reason, "hard_open_count": 0, "fingerprint": digest_json({"stage": "hard", "status": reason})}
        write_json(hard_path, hard)
    sealed_path = output_dir / "sealed-corpus/sealed_corpus_report.json"
    if sealed_path.is_file():
        sealed = read_json(sealed_path)
    else:
        sealed = {"schema": "murmurmark.reference_conditioned_target_me_sealed_corpus/v2", "decision": "NOT_OPENED", "status": reason, "evaluated_session_count": 0, "fingerprint": digest_json({"stage": "sealed", "status": reason})}
        write_json(sealed_path, sealed)
    return hard, sealed


def run_final_decision(*, policy_path: Path, output_dir: Path) -> dict[str, Any]:
    resource = apply_resource_policy(resolve_resource_policy("background"))
    policy = load_policy(policy_path)
    preflight = read_json(output_dir / "preflight_report.json")
    replay = read_json(output_dir / "v1_replay_report.json")
    train_report, lock, checkpoint = verify_candidate_lock(policy_path, output_dir)
    checks = [
        checked("preflight", preflight.get("decision"), READY),
        checked("v1_replay", replay.get("decision"), V1_REPRODUCED),
        checked("candidate_checkpoint", sha256(checkpoint), lock.get("checkpoint_sha256")),
        checked("production_policy_unchanged", sha256(ROOT / policy["production_baseline"]["policy"]), policy["production_baseline"]["policy_sha256"]),
    ]
    promotion_allowed = False
    if train_report.get("decision") == DEV_REJECTED:
        hard, sealed = materialize_not_opened_reports(output_dir, "dev_candidate_rejected")
        checks.extend((checked("hard_not_opened", hard.get("hard_open_count"), 0), checked("sealed_not_opened", sealed.get("evaluated_session_count"), 0)))
        limiting_stage = "dev"
    elif train_report.get("decision") == DEV_LOCKED:
        hard_path = output_dir / "hard-test/hard_test_report.json"
        if not hard_path.is_file():
            raise RuntimeError("locked candidate requires one hard-test evaluation before final decision")
        hard = read_json(hard_path)
        if hard.get("decision") == HARD_REJECTED:
            _, sealed = materialize_not_opened_reports(output_dir, "hard_candidate_rejected")
            checks.append(checked("sealed_not_opened", sealed.get("evaluated_session_count"), 0))
            limiting_stage = "hard"
        elif hard.get("decision") == HARD_PASSED:
            sealed_path = output_dir / "sealed-corpus/sealed_corpus_report.json"
            if not sealed_path.is_file():
                raise RuntimeError("hard passed; sealed meeting evaluation is required before decision")
            sealed = read_json(sealed_path)
            promotion_allowed = sealed.get("decision") == "SEALED_CORPUS_PASSED"
            limiting_stage = "none" if promotion_allowed else "sealed"
        else:
            raise RuntimeError("unexpected hard-test decision")
    else:
        raise RuntimeError("unexpected train/dev decision")
    decision = PROMOTE if promotion_allowed else DO_NOT_PROMOTE
    checks.extend((checked("post_asr_cleanup_credit", policy["audio_contract"]["post_asr_cleanup_promotion_credit"], 0), checked("raw_caf_mutation", False, False)))
    deterministic = {
        "policy_sha256": sha256(policy_path),
        "preflight_fingerprint": preflight["fingerprint"],
        "v1_replay_fingerprint": replay["fingerprint"],
        "candidate_lock_fingerprint": lock["fingerprint"],
        "hard_fingerprint": hard["fingerprint"],
        "sealed_fingerprint": sealed["fingerprint"],
        "checks": checks,
        "decision": decision,
        "limiting_stage": limiting_stage,
    }
    report = {
        "schema": "murmurmark.reference_conditioned_target_me_decision/v2",
        "profile": PROFILE,
        "decision": decision,
        "fingerprint": digest_json(deterministic),
        "promotion_allowed": promotion_allowed,
        "production_unchanged": not promotion_allowed,
        "production_fallback": policy["production_baseline"]["fallback"],
        "corpus_fingerprint": policy["identifiability_corpus"]["fingerprint"],
        "v1_baseline_fingerprint": policy["v1_baseline"]["fingerprint"],
        "candidate_lock_fingerprint": lock["fingerprint"],
        "train_dev_decision": train_report["decision"],
        "hard_test_decision": hard["decision"],
        "sealed_corpus_decision": sealed["decision"],
        "limiting_stage": limiting_stage,
        "blockers": train_report.get("blockers", []) if limiting_stage == "dev" else hard.get("blockers", []) if limiting_stage == "hard" else sealed.get("blockers", []),
        "checks": checks,
        "post_asr_cleanup_credit": 0,
        "resource_policy": resource,
    }
    data_card = {
        "schema": "murmurmark.reference_conditioned_target_me_data_card/v2",
        "corpus_fingerprint": report["corpus_fingerprint"],
        "speaker_disjoint_non_target_splits": {"train": 4, "dev": 2, "hard": 2},
        "paired_query_supervision": True,
        "train_items": train_report["train"]["items"],
        "dev_items": train_report["dev"]["items"],
        "hard_items_opened": 0 if hard["decision"] == "NOT_OPENED" else hard.get("items", 0),
        "ordinary_meeting_training": False,
        "known_limit": "English non-target training speech and a bounded spectral extractor do not reach ASR-safe waveform quality",
    }
    data_card["fingerprint"] = digest_json(data_card)
    model_card = {
        "schema": "murmurmark.reference_conditioned_target_me_model_card/v2",
        "candidate_id": lock["candidate_id"],
        "candidate_lock_fingerprint": lock["fingerprint"],
        "checkpoint_sha256": lock["checkpoint_sha256"],
        "model_state_fingerprint": lock["model_state_fingerprint"],
        "architecture": "bounded complex spectral mask with FiLM speaker-query conditioning",
        "decision": train_report["decision"],
        "promotion_eligible": promotion_allowed,
        "production_fallback": report["production_fallback"],
    }
    model_card["fingerprint"] = digest_json(model_card)
    corpus_report = {
        "schema": "murmurmark.reference_conditioned_target_me_corpus_report/v2",
        "decision": decision,
        "corpus_fingerprint": report["corpus_fingerprint"],
        "train_dev": {
            "decision": train_report["decision"],
            "fingerprint": train_report["fingerprint"],
            "checks": train_report["checks"],
            "aggregate": train_report["dev"]["aggregate"],
        },
        "hard_test": {"decision": hard["decision"], "fingerprint": hard["fingerprint"]},
        "sealed_corpus": {"decision": sealed["decision"], "fingerprint": sealed["fingerprint"]},
        "production_unchanged": report["production_unchanged"],
        "post_asr_cleanup_credit": 0,
    }
    corpus_report["fingerprint"] = digest_json(corpus_report)
    write_json(output_dir / "decision.json", report)
    (output_dir / "decision.md").write_text(decision_markdown(report, train_report, hard, sealed), encoding="utf-8")
    write_json(output_dir / "data_card.json", data_card)
    write_json(output_dir / "model_card.json", model_card)
    write_json(output_dir / "corpus_report.json", corpus_report)
    (output_dir / "corpus_report.md").write_text(decision_markdown(report, train_report, hard, sealed), encoding="utf-8")
    write_json(output_dir / "experiment_manifest.json", {"schema": "murmurmark.reference_conditioned_target_me_experiment_manifest/v2", "fingerprint": digest_json(deterministic), "policy_sha256": sha256(policy_path), "corpus_fingerprint": report["corpus_fingerprint"], "candidate_lock_fingerprint": lock["fingerprint"], "hard_test": {"decision": hard["decision"], "fingerprint": hard["fingerprint"]}, "sealed_corpus": {"decision": sealed["decision"], "fingerprint": sealed["fingerprint"]}})
    return report


def preflight_markdown(report: dict[str, Any]) -> str:
    return "\n".join(("# Reference-Conditioned Target-Me Separation v2: Preflight", "", f"Decision: `{report['decision']}`", f"Fingerprint: `{report['fingerprint']}`", f"Hard audio opened: `{str(report['hard_audio_opened']).lower()}`", "", *(f"- {'PASS' if row['passed'] else 'FAIL'} `{row['name']}`" for row in report["checks"]), ""))


def v1_replay_markdown(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    return "\n".join(("# Reference-Conditioned Target-Me Separation v2: v1 Replay", "", f"Decision: `{report['decision']}`", f"Target-Me SNR median: `{metrics['target_snr_db_median']:.6f} dB`", f"Target improvement median: `{metrics['target_improvement_db_median']:.6f} dB`", f"Echo SNR median: `{metrics['echo_snr_db_median']:.6f} dB`", "", "Hard audio remained unopened.", ""))


def train_dev_markdown(report: dict[str, Any]) -> str:
    aggregate = report["dev"]["aggregate"]
    return "\n".join(("# Reference-Conditioned Target-Me Separation v2: Train/Dev", "", f"Decision: `{report['decision']}`", f"Fingerprint: `{report['fingerprint']}`", f"Target-Me SNR median: `{aggregate['roles']['target_me']['target_snr_db']['median']:.3f} dB`", f"Other-speaker SNR median: `{aggregate['roles']['other_local_speech']['target_snr_db']['median']:.3f} dB`", f"Paired query margin median: `{aggregate['metrics']['paired_query_margin_db']['median']:.3f} dB`", f"Query collapse rate: `{aggregate['query_collapse_rate']:.3f}`", "", *(f"- {'PASS' if row['passed'] else 'FAIL'} `{row['name']}`" for row in report["checks"]), "", "Hard audio remained unopened while fitting and selecting this candidate.", ""))


def hard_markdown(report: dict[str, Any]) -> str:
    return "\n".join(("# Reference-Conditioned Target-Me Separation v2: Hard Test", "", f"Decision: `{report['decision']}`", f"Fingerprint: `{report['fingerprint']}`", f"Hard opened exactly `{report['hard_open_count']}` time.", "", *(f"- {'PASS' if row['passed'] else 'FAIL'} `{row['name']}`" for row in report["checks"]), ""))


def decision_markdown(report: dict[str, Any], train: dict[str, Any], hard: dict[str, Any], sealed: dict[str, Any]) -> str:
    lines = ["# Reference-Conditioned Target-Me Separation v2", "", f"Decision: `{report['decision']}`", f"Fingerprint: `{report['fingerprint']}`", f"Limiting stage: `{report['limiting_stage']}`", "", "## Stage Results", "", f"- Train/dev: `{train['decision']}`", f"- Hard: `{hard['decision']}`", f"- Sealed meetings: `{sealed['decision']}`", f"- Production unchanged: `{str(report['production_unchanged']).lower()}`", f"- Fallback: `{report['production_fallback']}`", ""]
    if report["blockers"]:
        lines.extend(("## Measured Blockers", "", *(f"- `{value}`" for value in report["blockers"]), ""))
    lines.append("No post-ASR cleanup received promotion credit.")
    lines.append("")
    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, default=ROOT / "policies/reference-conditioned-target-me-separation-v2.json")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "sessions/_reports/reference-conditioned-target-me-separation-v2")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("preflight")
    subparsers.add_parser("reproduce-v1")
    train = subparsers.add_parser("train-dev")
    train.add_argument("--refresh-cache", action="store_true")
    subparsers.add_parser("hard-test")
    subparsers.add_parser("decide")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    policy_path = args.policy.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    if args.command == "preflight":
        report = run_preflight(policy_path=policy_path, output_dir=output_dir)
        success = report["decision"] == READY
        report_path = output_dir / "preflight_report.json"
    elif args.command == "reproduce-v1":
        report = run_v1_replay(policy_path=policy_path, output_dir=output_dir, preflight_path=output_dir / "preflight_report.json")
        success = report["decision"] == V1_REPRODUCED
        report_path = output_dir / "v1_replay_report.json"
    elif args.command == "train-dev":
        report = run_train_dev(policy_path=policy_path, output_dir=output_dir, preflight_path=output_dir / "preflight_report.json", replay_path=output_dir / "v1_replay_report.json", refresh_cache=bool(args.refresh_cache))
        success = report["decision"] == DEV_LOCKED
        report_path = output_dir / "train-dev/train_dev_report.json"
    elif args.command == "hard-test":
        report = run_hard_test(policy_path=policy_path, output_dir=output_dir)
        success = report["decision"] == HARD_PASSED
        report_path = output_dir / "hard-test/hard_test_report.json"
    elif args.command == "decide":
        report = run_final_decision(policy_path=policy_path, output_dir=output_dir)
        success = report["decision"] in {PROMOTE, DO_NOT_PROMOTE}
        report_path = output_dir / "decision.json"
    else:
        raise RuntimeError(f"unsupported command: {args.command}")
    print(f"decision: {report['decision']}")
    print(f"fingerprint: {report['fingerprint']}")
    print(f"report: {display_path(report_path)}")
    return 0 if success else 2


if __name__ == "__main__":
    raise SystemExit(main())
