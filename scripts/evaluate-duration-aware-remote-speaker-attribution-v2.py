#!/usr/bin/env python3
"""Develop and one-shot evaluate duration-aware remote-speaker attribution."""

from __future__ import annotations

import argparse
from collections import defaultdict
import copy
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import sys
from typing import Any, Iterable

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.2.0"
POLICY_SCHEMA = "murmurmark.duration_aware_remote_speaker_attribution_policy/v2"
DEVELOPMENT_SCHEMA = "murmurmark.duration_aware_remote_speaker_development/v2"
CANDIDATE_SCHEMA = "murmurmark.duration_aware_remote_speaker_candidate_freeze/v2"
REPORT_SCHEMA = "murmurmark.duration_aware_remote_speaker_attribution_report/v2"
REPLAY_SCHEMA = "murmurmark.duration_aware_remote_speaker_attribution_replay/v2"
PREDICTION_SCHEMA = "murmurmark.duration_aware_remote_speaker_prediction/v2"
LEDGER_SCHEMA = "murmurmark.remote_speaker_hard_v2_opening_ledger/v1"
DEFAULT_POLICY = ROOT / "policies/duration-aware-remote-speaker-attribution-v2.json"
DEFAULT_OUT = ROOT / "sessions/_reports/duration-aware-remote-speaker-attribution-v2"
DEFAULT_TRUTH_LAB = ROOT / "sessions/_reports/controlled-remote-speaker-truth-lab-v1"
TRUTH_LAB_POLICY = ROOT / "policies/controlled-remote-speaker-truth-lab-v1.json"
TRUTH_LAB_SCRIPT = ROOT / "scripts/controlled-remote-speaker-truth-lab-v1.py"
FREEZE_SCRIPT = ROOT / "scripts/freeze-remote-speaker-hard-v2.py"
ABSOLUTE_PATH_MARKERS = ("/Users/", "/home/", "C:\\")
PRIVATE_KEYS = {"text", "system_voice", "private_seed", "vocabulary", "hard_vocabulary", "enrollment_scripts"}


class AttributionError(RuntimeError):
    pass


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def compact_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise AttributionError(f"invalid_json:{path.name}:{type(error).__name__}") from error
    if not isinstance(value, dict):
        raise AttributionError(f"json_object_required:{path.name}")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    try:
        for line in path.read_text().splitlines():
            if line.strip():
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise AttributionError(f"jsonl_object_required:{path.name}")
                rows.append(value)
    except (OSError, json.JSONDecodeError) as error:
        raise AttributionError(f"invalid_jsonl:{path.name}:{type(error).__name__}") from error
    return rows


def write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def write_json(path: Path, value: Any) -> None:
    write_bytes(path, canonical_json(value))


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    write_bytes(path, b"".join(compact_json(row) + b"\n" for row in rows))


def portable(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(ROOT))
    except ValueError:
        return f"external/{resolved.name}"


def fingerprint(path: Path) -> dict[str, Any]:
    return {"path": portable(path), "bytes": path.stat().st_size, "sha256": sha256(path)}


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AttributionError(f"module_import_failed:{path.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def nested_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            keys.add(str(key))
            keys.update(nested_keys(child))
    elif isinstance(value, list):
        for child in value:
            keys.update(nested_keys(child))
    return keys


def assert_public_safe(value: Any) -> None:
    encoded = canonical_json(value).decode()
    if any(marker in encoded for marker in ABSOLUTE_PATH_MARKERS):
        raise AttributionError("public_artifact_contains_absolute_path")
    leaked = sorted(PRIVATE_KEYS & nested_keys(value))
    if leaked:
        raise AttributionError("public_artifact_contains_private_keys:" + ",".join(leaked))


def load_policy(path: Path) -> dict[str, Any]:
    policy = read_json(path)
    if policy.get("schema") != POLICY_SCHEMA:
        raise AttributionError("policy_schema_invalid")
    topology_ids = [str(row.get("id")) for row in policy.get("topologies") or []]
    expected = {
        "duration_binned_prototype_bank",
        "cohort_normalized_wavlm",
        "conservative_resemblyzer_wavlm_fusion",
    }
    if len(topology_ids) != 3 or set(topology_ids) != expected:
        raise AttributionError("predeclared_topology_scope_changed")
    if policy["selection"].get("mixed_detection") != "timestamp_only":
        raise AttributionError("mixed_detection_must_be_timestamp_only")
    if policy["selection"].get("unknown_policy") != "fail_open":
        raise AttributionError("unknown_policy_must_fail_open")
    return policy


def topology(policy: dict[str, Any], topology_id: str) -> dict[str, Any]:
    for row in policy["topologies"]:
        if row["id"] == topology_id:
            return row
    raise AttributionError(f"unknown_topology:{topology_id}")


def verify_development_inputs(
    policy: dict[str, Any], truth_lab_out: Path, *, fixture_mode: bool
) -> None:
    if fixture_mode:
        required = [truth_lab_out / "private/frozen_manifest.json"]
    else:
        required = []
        for key in ("tracked_manifest", "private_frozen_manifest", "public_report"):
            row = policy["development_evidence"][key]
            path = ROOT / row["path"]
            if not path.is_file() or sha256(path) != row["sha256"]:
                raise AttributionError(f"development_input_missing_or_stale:{key}")
            required.append(path)
    if any(not path.is_file() for path in required):
        raise AttributionError("development_truth_lab_missing")


def verify_hard_public(args: argparse.Namespace, policy: dict[str, Any]) -> dict[str, Any]:
    path = args.out_dir / "hard_v2_public_manifest.json"
    manifest = read_json(path)
    if manifest.get("schema") != "murmurmark.remote_speaker_hard_v2_public_manifest/v1":
        raise AttributionError("hard_v2_public_manifest_schema_invalid")
    if manifest.get("policy", {}).get("sha256") != sha256(args.policy):
        raise AttributionError("hard_v2_policy_stale")
    if manifest.get("implementation", {}).get("sha256") != sha256(FREEZE_SCRIPT):
        raise AttributionError("hard_v2_freezer_stale")
    if manifest.get("decision") != "HARD_V2_FROZEN_UNOPENED":
        raise AttributionError("hard_v2_not_frozen_unopened")
    if not manifest.get("scripts_disjoint_from_truth_lab_v1"):
        raise AttributionError("hard_v2_scripts_not_disjoint")
    return manifest


def development_scenarios(lab: Any, truth_lab_out: Path) -> list[dict[str, Any]]:
    old_policy = lab.load_policy(TRUTH_LAB_POLICY)
    lab.verify_frozen(truth_lab_out / "private", old_policy, TRUTH_LAB_POLICY)
    scenarios = copy.deepcopy(lab.scenario_paths(truth_lab_out / "private", old_policy))
    for scenario in scenarios:
        if scenario["split"] != "train":
            scenario["split"] = "development"
    return scenarios


def request_fingerprint(requests: list[Any], provenance: dict[str, Any]) -> str:
    audio_hashes: dict[str, str] = {}
    rows = []
    for request in sorted(requests, key=lambda row: row.key):
        path = Path(request.path).resolve()
        key = str(path)
        if key not in audio_hashes:
            audio_hashes[key] = sha256(path)
        rows.append(
            {
                "key": request.key,
                "scenario_id": request.scenario_id,
                "audio_sha256": audio_hashes[key],
                "start": round(float(request.start), 6),
                "end": round(float(request.end), 6),
            }
        )
    return sha256_bytes(canonical_json({"backend": provenance, "requests": rows}))


def embed_cached(
    backend: Any, requests: list[Any], cache_dir: Path, cache_name: str
) -> dict[str, np.ndarray]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    vector_path = cache_dir / f"{cache_name}.npz"
    meta_path = cache_dir / f"{cache_name}.json"
    expected = request_fingerprint(requests, backend.provenance)
    if vector_path.is_file() and meta_path.is_file():
        meta = read_json(meta_path)
        if meta.get("fingerprint") == expected and meta.get("vector_sha256") == sha256(vector_path):
            archive = np.load(vector_path, allow_pickle=False)
            keys = [str(value) for value in archive["keys"].tolist()]
            vectors = np.asarray(archive["vectors"], dtype=np.float32)
            if len(keys) == len(vectors):
                return {key: vector for key, vector in zip(keys, vectors)}
    embedded = backend.embed_requests(requests)
    keys = sorted(embedded)
    vectors = np.stack([embedded[key] for key in keys]).astype(np.float32)
    temporary = vector_path.with_name(f".{vector_path.name}.{os.getpid()}.tmp.npz")
    np.savez_compressed(temporary, keys=np.asarray(keys), vectors=vectors)
    os.replace(temporary, vector_path)
    write_json(
        meta_path,
        {
            "fingerprint": expected,
            "backend": backend.provenance,
            "request_count": len(requests),
            "vector_sha256": sha256(vector_path),
        },
    )
    return embedded


def duration_bin(duration: float, boundaries: list[float]) -> int:
    for index, right in enumerate(boundaries[1:]):
        if duration < float(right):
            return index
    return len(boundaries) - 2


def normalize(vector: np.ndarray) -> np.ndarray:
    values = np.asarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(values))
    if norm <= 1e-8:
        raise AttributionError("zero_embedding")
    return values / norm


def centroids_from_samples(samples: dict[str, list[np.ndarray]]) -> dict[str, np.ndarray]:
    return {
        speaker: normalize(np.mean(vectors, axis=0))
        for speaker, vectors in samples.items()
        if vectors
    }


def raw_scores(vector: np.ndarray, centroids: dict[str, np.ndarray]) -> list[tuple[float, str]]:
    return sorted(
        ((float(vector @ centroid), speaker) for speaker, centroid in centroids.items()),
        reverse=True,
    )


def accepted_raw(
    vector: np.ndarray, centroids: dict[str, np.ndarray], similarity: float, margin: float
) -> dict[str, Any]:
    scores = raw_scores(vector, centroids)
    if not scores:
        return {"speaker_id": None, "top_speaker_id": None, "similarity": None, "margin": None}
    top_similarity, top_speaker = scores[0]
    top_margin = top_similarity - scores[1][0] if len(scores) > 1 else top_similarity
    accepted = top_similarity >= similarity and top_margin >= margin
    return {
        "speaker_id": top_speaker if accepted else None,
        "top_speaker_id": top_speaker,
        "similarity": round(top_similarity, 6),
        "margin": round(top_margin, 6),
    }


def base_prediction(
    word: dict[str, Any], scenario: dict[str, Any], track: str, result: dict[str, Any]
) -> dict[str, Any]:
    return {
        "schema": PREDICTION_SCHEMA,
        "track": track,
        "word_id": word["word_id"],
        "scenario_id": scenario["scenario_id"],
        "split": scenario["split"],
        "speaker_id": result["speaker_id"],
        "top_speaker_id": result.get("top_speaker_id"),
        "similarity": result.get("similarity"),
        "margin": result.get("margin"),
        "reason": result["reason"],
    }


def timestamp_mixed(word: dict[str, Any]) -> bool:
    return bool(word.get("overlap_word_ids"))


def duration_development_prototypes(
    scenarios: list[dict[str, Any]], embeddings: dict[str, np.ndarray], bins: list[float]
) -> dict[tuple[str, int], np.ndarray]:
    samples: dict[tuple[str, int], list[np.ndarray]] = defaultdict(list)
    fallback: dict[str, list[np.ndarray]] = defaultdict(list)
    for scenario in scenarios:
        if scenario["split"] != "train":
            continue
        events = {row["event_id"]: row for row in scenario["scenario"]["events"]}
        for event_id, event in events.items():
            vector = embeddings.get(f"enrollment:{event_id}")
            if vector is None or not event["enrolled"]:
                continue
            index = duration_bin(float(event["end"]) - float(event["start"]), bins)
            samples[(str(event["speaker_id"]), index)].append(vector)
            fallback[str(event["speaker_id"])].append(vector)
        for word in scenario["words"]:
            if word["truth_class"] != "known_speaker":
                continue
            vector = embeddings[f"word:{word['word_id']}"]
            index = duration_bin(float(word["end"]) - float(word["start"]), bins)
            samples[(str(word["speaker_id"]), index)].append(vector)
            fallback[str(word["speaker_id"])].append(vector)
    speakers = sorted(fallback)
    prototypes: dict[tuple[str, int], np.ndarray] = {}
    for speaker in speakers:
        overall = normalize(np.mean(fallback[speaker], axis=0))
        for index in range(len(bins) - 1):
            values = samples.get((speaker, index)) or []
            prototypes[(speaker, index)] = normalize(np.mean(values, axis=0)) if values else overall
    return prototypes


def predict_duration_bank(
    scenarios: list[dict[str, Any]],
    embeddings: dict[str, np.ndarray],
    prototypes: dict[tuple[str, int], np.ndarray],
    bins: list[float],
    similarity: float,
    margin: float,
    track: str,
) -> list[dict[str, Any]]:
    speakers = sorted({speaker for speaker, _ in prototypes})
    rows = []
    for scenario in scenarios:
        for word in scenario["words"]:
            if timestamp_mixed(word):
                result = {
                    "speaker_id": "mixed",
                    "top_speaker_id": None,
                    "similarity": None,
                    "margin": None,
                    "reason": "timestamp_overlap",
                }
            else:
                index = duration_bin(float(word["end"]) - float(word["start"]), bins)
                centroids = {speaker: prototypes[(speaker, index)] for speaker in speakers}
                raw = accepted_raw(
                    embeddings[f"word:{word['word_id']}"], centroids, similarity, margin
                )
                result = {
                    **raw,
                    "speaker_id": raw["speaker_id"] or "unknown_speaker",
                    "reason": f"duration_bin_{index}_accepted" if raw["speaker_id"] else f"duration_bin_{index}_abstained",
                }
            rows.append(base_prediction(word, scenario, track, result))
    return rows


def predict_cohort(
    scenarios: list[dict[str, Any]],
    embeddings: dict[str, np.ndarray],
    centroids: dict[str, np.ndarray],
    similarity: float,
    margin: float,
    cohort_z: float,
    track: str,
) -> list[dict[str, Any]]:
    rows = []
    for scenario in scenarios:
        for word in scenario["words"]:
            if timestamp_mixed(word):
                result = {
                    "speaker_id": "mixed",
                    "top_speaker_id": None,
                    "similarity": None,
                    "margin": None,
                    "reason": "timestamp_overlap",
                }
            else:
                vector = embeddings[f"word:{word['word_id']}"]
                scores = raw_scores(vector, centroids)
                values = np.asarray([score for score, _ in scores], dtype=np.float64)
                top_similarity, top_speaker = scores[0]
                top_margin = top_similarity - scores[1][0] if len(scores) > 1 else top_similarity
                standard = float(np.std(values))
                z_score = (top_similarity - float(np.mean(values))) / max(standard, 1e-6)
                accepted = top_similarity >= similarity and top_margin >= margin and z_score >= cohort_z
                result = {
                    "speaker_id": top_speaker if accepted else "unknown_speaker",
                    "top_speaker_id": top_speaker,
                    "similarity": round(top_similarity, 6),
                    "margin": round(top_margin, 6),
                    "reason": "cohort_normalized_accepted" if accepted else "cohort_normalized_abstained",
                    "cohort_z": round(z_score, 6),
                }
            prediction = base_prediction(word, scenario, track, result)
            if "cohort_z" in result:
                prediction["cohort_z"] = result["cohort_z"]
            rows.append(prediction)
    return rows


def predict_fusion(
    scenarios: list[dict[str, Any]],
    wavlm_embeddings: dict[str, np.ndarray],
    resemblyzer_embeddings: dict[str, np.ndarray],
    wavlm_centroids: dict[str, np.ndarray],
    resemblyzer_centroids: dict[str, np.ndarray],
    config: dict[str, float],
    track: str,
) -> list[dict[str, Any]]:
    rows = []
    for scenario in scenarios:
        for word in scenario["words"]:
            if timestamp_mixed(word):
                result = {
                    "speaker_id": "mixed",
                    "top_speaker_id": None,
                    "similarity": None,
                    "margin": None,
                    "reason": "timestamp_overlap",
                }
            else:
                wavlm = accepted_raw(
                    wavlm_embeddings[f"word:{word['word_id']}"] ,
                    wavlm_centroids,
                    config["wavlm_similarity"],
                    config["wavlm_margin"],
                )
                resemblyzer = accepted_raw(
                    resemblyzer_embeddings[f"word:{word['word_id']}"] ,
                    resemblyzer_centroids,
                    config["resemblyzer_similarity"],
                    config["resemblyzer_margin"],
                )
                agreed = wavlm["speaker_id"] is not None and wavlm["speaker_id"] == resemblyzer["speaker_id"]
                result = {
                    "speaker_id": wavlm["speaker_id"] if agreed else "unknown_speaker",
                    "top_speaker_id": wavlm["top_speaker_id"],
                    "similarity": wavlm["similarity"],
                    "margin": wavlm["margin"],
                    "reason": "independent_backend_agreement" if agreed else "fusion_abstained",
                    "resemblyzer_top_speaker_id": resemblyzer["top_speaker_id"],
                    "resemblyzer_similarity": resemblyzer["similarity"],
                    "resemblyzer_margin": resemblyzer["margin"],
                }
            prediction = base_prediction(word, scenario, track, result)
            for key in ("resemblyzer_top_speaker_id", "resemblyzer_similarity", "resemblyzer_margin"):
                if key in result:
                    prediction[key] = result[key]
            rows.append(prediction)
    return rows


def trial_score(metrics: dict[str, Any], config: dict[str, Any]) -> tuple[Any, ...]:
    return (
        -int(metrics["open_set_false_attributions"]),
        float(metrics["pairwise"]["precision"]),
        float(metrics["boundary_recall"]),
        float(metrics["bcubed"]["f1"]),
        float(metrics["known_attribution_recall"]),
        canonical_json(config),
    )


def best_trial(trials: list[dict[str, Any]]) -> dict[str, Any]:
    if not trials:
        raise AttributionError("topology_has_no_trials")
    return max(trials, key=lambda row: trial_score(row["metrics"], row["config"]))


def tune_duration(
    policy: dict[str, Any], lab: Any, scenarios: list[dict[str, Any]], embeddings: dict[str, np.ndarray]
) -> dict[str, Any]:
    spec = topology(policy, "duration_binned_prototype_bank")
    bins = [float(value) for value in spec["duration_bins_sec"]]
    prototypes = duration_development_prototypes(scenarios, embeddings, bins)
    trials = []
    for similarity in spec["similarity_grid"]:
        for margin in spec["margin_grid"]:
            config = {"minimum_similarity": float(similarity), "minimum_margin": float(margin)}
            predictions = predict_duration_bank(
                scenarios, embeddings, prototypes, bins, float(similarity), float(margin), spec["id"]
            )
            trials.append(
                {"config": config, "metrics": lab.evaluate_predictions(scenarios, predictions, "development")}
            )
    selected = best_trial(trials)
    predictions = predict_duration_bank(
        scenarios,
        embeddings,
        prototypes,
        bins,
        selected["config"]["minimum_similarity"],
        selected["config"]["minimum_margin"],
        spec["id"],
    )
    return {"id": spec["id"], "selected": selected, "trial_count": len(trials), "predictions": predictions}


def tune_cohort(
    policy: dict[str, Any], lab: Any, scenarios: list[dict[str, Any]], embeddings: dict[str, np.ndarray]
) -> dict[str, Any]:
    spec = topology(policy, "cohort_normalized_wavlm")
    centroids, enrollment = lab.build_centroids(scenarios, embeddings, "word_and_event")
    trials = []
    for similarity in spec["similarity_grid"]:
        for margin in spec["margin_grid"]:
            for cohort_z in spec["cohort_z_grid"]:
                config = {
                    "minimum_similarity": float(similarity),
                    "minimum_margin": float(margin),
                    "minimum_cohort_z": float(cohort_z),
                }
                predictions = predict_cohort(
                    scenarios,
                    embeddings,
                    centroids,
                    float(similarity),
                    float(margin),
                    float(cohort_z),
                    spec["id"],
                )
                trials.append(
                    {"config": config, "metrics": lab.evaluate_predictions(scenarios, predictions, "development")}
                )
    selected = best_trial(trials)
    predictions = predict_cohort(
        scenarios,
        embeddings,
        centroids,
        selected["config"]["minimum_similarity"],
        selected["config"]["minimum_margin"],
        selected["config"]["minimum_cohort_z"],
        spec["id"],
    )
    return {
        "id": spec["id"],
        "selected": selected,
        "trial_count": len(trials),
        "enrollment": enrollment,
        "predictions": predictions,
    }


def tune_fusion(
    policy: dict[str, Any],
    lab: Any,
    scenarios: list[dict[str, Any]],
    wavlm_embeddings: dict[str, np.ndarray],
    resemblyzer_embeddings: dict[str, np.ndarray],
) -> dict[str, Any]:
    spec = topology(policy, "conservative_resemblyzer_wavlm_fusion")
    wavlm_centroids, wavlm_enrollment = lab.build_centroids(scenarios, wavlm_embeddings, "word_only")
    resemblyzer_centroids, resemblyzer_enrollment = lab.build_centroids(
        scenarios, resemblyzer_embeddings, "word_and_event"
    )
    trials = []
    for wavlm_similarity in spec["wavlm_similarity_grid"]:
        for wavlm_margin in spec["wavlm_margin_grid"]:
            for resemblyzer_similarity in spec["resemblyzer_similarity_grid"]:
                for resemblyzer_margin in spec["resemblyzer_margin_grid"]:
                    config = {
                        "wavlm_similarity": float(wavlm_similarity),
                        "wavlm_margin": float(wavlm_margin),
                        "resemblyzer_similarity": float(resemblyzer_similarity),
                        "resemblyzer_margin": float(resemblyzer_margin),
                    }
                    predictions = predict_fusion(
                        scenarios,
                        wavlm_embeddings,
                        resemblyzer_embeddings,
                        wavlm_centroids,
                        resemblyzer_centroids,
                        config,
                        spec["id"],
                    )
                    trials.append(
                        {"config": config, "metrics": lab.evaluate_predictions(scenarios, predictions, "development")}
                    )
    selected = best_trial(trials)
    predictions = predict_fusion(
        scenarios,
        wavlm_embeddings,
        resemblyzer_embeddings,
        wavlm_centroids,
        resemblyzer_centroids,
        selected["config"],
        spec["id"],
    )
    return {
        "id": spec["id"],
        "selected": selected,
        "trial_count": len(trials),
        "enrollment": {"wavlm": wavlm_enrollment, "resemblyzer": resemblyzer_enrollment},
        "predictions": predictions,
    }


def public_topology(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "trial_count": row["trial_count"],
        "selected_config": row["selected"]["config"],
        "development_metrics": row["selected"]["metrics"],
    }


def select_topology(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return max(rows, key=lambda row: trial_score(row["selected"]["metrics"], {"id": row["id"]}))


def develop(args: argparse.Namespace, policy: dict[str, Any], lab: Any) -> dict[str, Any]:
    verify_development_inputs(policy, args.truth_lab_out, fixture_mode=args.fixture_mode)
    hard_public = verify_hard_public(args, policy)
    ledger = args.out_dir / "private/hard-v2" / policy["hard_v2"]["opening_ledger"]
    if ledger.exists():
        raise AttributionError("hard_v2_already_opened_before_development")
    scenarios = development_scenarios(lab, args.truth_lab_out)
    enrollment_requests, word_requests = lab.build_requests(scenarios)
    requests = enrollment_requests + word_requests
    minimum = float(read_json(TRUTH_LAB_POLICY)["analysis"]["minimum_analysis_sec"])
    if args.fixture_mode:
        wavlm_backend = lab.FixtureEmbeddingBackend("wavlm_duration_fixture_v2", minimum)
        resemblyzer_backend = lab.FixtureEmbeddingBackend("resemblyzer_fusion_fixture_v2", minimum)
    else:
        wavlm_backend = lab.WavLMBackend(
            read_json(TRUTH_LAB_POLICY),
            minimum,
            int(read_json(TRUTH_LAB_POLICY)["analysis"]["wavlm_batch_size"]),
        )
        resemblyzer_backend = lab.ResemblyzerBackend(minimum)
    cache = args.out_dir / "private/development/cache"
    print("develop: embed WavLM", flush=True)
    wavlm_embeddings = embed_cached(wavlm_backend, requests, cache, "wavlm")
    print("develop: embed Resemblyzer", flush=True)
    resemblyzer_embeddings = embed_cached(resemblyzer_backend, requests, cache, "resemblyzer")
    print("develop: duration-binned prototype bank", flush=True)
    duration_row = tune_duration(policy, lab, scenarios, wavlm_embeddings)
    print("develop: cohort-normalized WavLM", flush=True)
    cohort_row = tune_cohort(policy, lab, scenarios, wavlm_embeddings)
    print("develop: conservative fusion", flush=True)
    fusion_row = tune_fusion(policy, lab, scenarios, wavlm_embeddings, resemblyzer_embeddings)
    rows = [duration_row, cohort_row, fusion_row]
    selected = select_topology(rows)
    private_dir = args.out_dir / "private/development"
    for row in rows:
        write_jsonl(private_dir / f"predictions.{row['id']}.jsonl", row["predictions"])
    public = {
        "schema": DEVELOPMENT_SCHEMA,
        "version": VERSION,
        "decision": "CANDIDATE_SELECTED_ON_DEVELOPMENT",
        "policy": fingerprint(args.policy),
        "implementation": fingerprint(Path(__file__).resolve()),
        "development_truth_lab": {
            "frozen_manifest_sha256": sha256(args.truth_lab_out / "private/frozen_manifest.json"),
            "splits_used": ["train", "dev", "hard"],
            "treated_as_development_only": True,
        },
        "hard_v2": {
            "public_manifest_sha256": sha256(args.out_dir / "hard_v2_public_manifest.json"),
            "corpus_sha256": hard_public["corpus_sha256"],
            "opened": False,
            "used_for_selection": False,
        },
        "backends": {"wavlm": wavlm_backend.provenance, "resemblyzer": resemblyzer_backend.provenance},
        "topologies": [public_topology(row) for row in rows],
        "selected_topology": selected["id"],
        "selected_config": selected["selected"]["config"],
        "selected_development_metrics": selected["selected"]["metrics"],
        "production_changed": False,
    }
    assert_public_safe(public)
    write_json(args.out_dir / "development_report.json", public)
    candidate = {
        "schema": CANDIDATE_SCHEMA,
        "version": VERSION,
        "policy": fingerprint(args.policy),
        "implementation": fingerprint(Path(__file__).resolve()),
        "freeze_implementation": fingerprint(FREEZE_SCRIPT),
        "development_report": fingerprint(args.out_dir / "development_report.json"),
        "hard_v2_public_manifest": fingerprint(args.out_dir / "hard_v2_public_manifest.json"),
        "hard_v2_corpus_sha256": hard_public["corpus_sha256"],
        "selected_topology": selected["id"],
        "selected_config": selected["selected"]["config"],
        "selected_development_metrics": selected["selected"]["metrics"],
        "topology_count": len(rows),
        "hard_v2_used_for_selection": False,
        "frozen": True,
    }
    write_json(args.out_dir / "private/candidate_freeze.json", candidate)
    print(f"selected: {selected['id']}")
    print(f"candidate: {portable(args.out_dir / 'private/candidate_freeze.json')}")
    return public


def verify_candidate(args: argparse.Namespace) -> dict[str, Any]:
    path = args.out_dir / "private/candidate_freeze.json"
    candidate = read_json(path)
    if candidate.get("schema") != CANDIDATE_SCHEMA or candidate.get("frozen") is not True:
        raise AttributionError("candidate_not_frozen")
    pins = {
        "policy": (args.policy, candidate.get("policy", {}).get("sha256")),
        "implementation": (Path(__file__).resolve(), candidate.get("implementation", {}).get("sha256")),
        "freeze_implementation": (FREEZE_SCRIPT, candidate.get("freeze_implementation", {}).get("sha256")),
        "development_report": (
            args.out_dir / "development_report.json",
            candidate.get("development_report", {}).get("sha256"),
        ),
        "hard_v2_public_manifest": (
            args.out_dir / "hard_v2_public_manifest.json",
            candidate.get("hard_v2_public_manifest", {}).get("sha256"),
        ),
    }
    for name, (path_value, expected) in pins.items():
        if not path_value.is_file() or sha256(path_value) != expected:
            raise AttributionError(f"candidate_pin_stale:{name}")
    if candidate.get("hard_v2_used_for_selection") is not False:
        raise AttributionError("candidate_used_hard_v2_for_selection")
    return candidate


def open_hard_once(args: argparse.Namespace, policy: dict[str, Any], candidate: dict[str, Any]) -> Path:
    hard_private = args.out_dir / "private/hard-v2"
    ledger = hard_private / policy["hard_v2"]["opening_ledger"]
    payload = {
        "schema": LEDGER_SCHEMA,
        "decision_open_count": 1,
        "candidate_sha256": sha256(args.out_dir / "private/candidate_freeze.json"),
        "hard_v2_corpus_sha256": candidate["hard_v2_corpus_sha256"],
        "status": "opening",
    }
    try:
        descriptor = os.open(ledger, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as error:
        existing = read_json(ledger)
        if existing.get("status") == "opening" and existing.get("candidate_sha256") == payload["candidate_sha256"]:
            return ledger
        raise AttributionError("hard_v2_decision_opening_already_consumed") from error
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(canonical_json(payload))
        handle.flush()
        os.fsync(handle.fileno())
    return ledger


def hard_scenarios(args: argparse.Namespace) -> list[dict[str, Any]]:
    private = args.out_dir / "private/hard-v2"
    frozen = read_json(private / "frozen_manifest.json")
    rows = []
    for summary in frozen["scenario_summaries"]:
        scenario_id = str(summary["scenario_id"])
        directory = private / "sealed/sessions/hard_v2" / scenario_id
        rows.append(
            {
                "split": "hard_v2",
                "scenario_id": scenario_id,
                "directory": directory,
                "mixture": directory / "mixture.wav",
                "scenario": read_json(directory / "scenario.json"),
                "words": read_jsonl(directory / "truth_words.jsonl"),
                "boundaries": read_jsonl(directory / "truth_boundaries.jsonl"),
            }
        )
    return rows


def hard_requests(lab: Any, args: argparse.Namespace, scenarios: list[dict[str, Any]]) -> dict[str, Any]:
    private = args.out_dir / "private/hard-v2"
    enrollment_manifest = read_json(private / "enrollment/enrollment_manifest.json")
    word_requests = []
    for scenario in scenarios:
        for word in scenario["words"]:
            word_requests.append(
                lab.AudioRequest(
                    key=f"word:{word['word_id']}",
                    scenario_id=scenario["scenario_id"],
                    path=scenario["mixture"],
                    start=float(word["start"]),
                    end=float(word["end"]),
                )
            )
    full = []
    windows = []
    window_labels: dict[str, tuple[str, int]] = {}
    durations = (0.66, 0.96, 1.35)
    for row in enrollment_manifest["rows"]:
        speaker = str(row["speaker_id"])
        path = private / row["path"]
        total = float(row["duration_sec"])
        full.append(lab.AudioRequest(f"enrollment:{speaker}:full", "enrollment", path, 0.0, total))
        for bin_index, duration in enumerate(durations):
            for sample_index, fraction in enumerate((0.0, 0.35, 0.7)):
                start = max(0.0, min(total - duration, (total - duration) * fraction))
                key = f"enrollment:{speaker}:bin:{bin_index}:sample:{sample_index}"
                windows.append(lab.AudioRequest(key, "enrollment", path, start, start + duration))
                window_labels[key] = (speaker, bin_index)
    return {
        "word": word_requests,
        "full": full,
        "windows": windows,
        "window_labels": window_labels,
        "speakers": sorted(str(row["speaker_id"]) for row in enrollment_manifest["rows"]),
    }


def hard_centroids(embeddings: dict[str, np.ndarray], speakers: list[str]) -> dict[str, np.ndarray]:
    return {speaker: embeddings[f"enrollment:{speaker}:full"] for speaker in speakers}


def hard_duration_prototypes(
    embeddings: dict[str, np.ndarray], labels: dict[str, tuple[str, int]], speakers: list[str]
) -> dict[tuple[str, int], np.ndarray]:
    samples: dict[tuple[str, int], list[np.ndarray]] = defaultdict(list)
    for key, label in labels.items():
        samples[label].append(embeddings[key])
    return {
        (speaker, bin_index): normalize(np.mean(samples[(speaker, bin_index)], axis=0))
        for speaker in speakers
        for bin_index in range(3)
    }


def selected_hard_predictions(
    policy: dict[str, Any],
    lab: Any,
    candidate: dict[str, Any],
    scenarios: list[dict[str, Any]],
    requests: dict[str, Any],
    wavlm_embeddings: dict[str, np.ndarray],
    resemblyzer_embeddings: dict[str, np.ndarray],
) -> list[dict[str, Any]]:
    topology_id = str(candidate["selected_topology"])
    config = candidate["selected_config"]
    speakers = requests["speakers"]
    if topology_id == "duration_binned_prototype_bank":
        bins = [float(value) for value in topology(policy, topology_id)["duration_bins_sec"]]
        prototypes = hard_duration_prototypes(wavlm_embeddings, requests["window_labels"], speakers)
        return predict_duration_bank(
            scenarios,
            wavlm_embeddings,
            prototypes,
            bins,
            float(config["minimum_similarity"]),
            float(config["minimum_margin"]),
            topology_id,
        )
    wavlm_centroids = hard_centroids(wavlm_embeddings, speakers)
    if topology_id == "cohort_normalized_wavlm":
        return predict_cohort(
            scenarios,
            wavlm_embeddings,
            wavlm_centroids,
            float(config["minimum_similarity"]),
            float(config["minimum_margin"]),
            float(config["minimum_cohort_z"]),
            topology_id,
        )
    if topology_id == "conservative_resemblyzer_wavlm_fusion":
        resemblyzer_centroids = hard_centroids(resemblyzer_embeddings, speakers)
        return predict_fusion(
            scenarios,
            wavlm_embeddings,
            resemblyzer_embeddings,
            wavlm_centroids,
            resemblyzer_centroids,
            {key: float(value) for key, value in config.items()},
            topology_id,
        )
    raise AttributionError(f"candidate_topology_not_predeclared:{topology_id}")


def control_predictions(
    lab: Any,
    scenarios: list[dict[str, Any]],
    embeddings: dict[str, np.ndarray],
    centroids: dict[str, np.ndarray],
) -> list[dict[str, Any]]:
    old_policy = read_json(TRUTH_LAB_POLICY)
    return lab.predict_words(
        scenarios,
        embeddings,
        centroids,
        float(old_policy["analysis"]["coverage_v3_similarity"]),
        float(old_policy["analysis"]["coverage_v3_margin"]),
        "coverage_v3_control",
    )


def metric_non_regression(candidate: dict[str, Any], control: dict[str, Any]) -> dict[str, bool]:
    return {
        "bcubed_f1": candidate["bcubed"]["f1"] >= control["bcubed"]["f1"],
        "pairwise_precision": candidate["pairwise"]["precision"] >= control["pairwise"]["precision"],
        "known_speaker_recall": candidate["known_attribution_recall"] >= control["known_attribution_recall"],
        "boundary_recall": candidate["boundary_recall"] >= control["boundary_recall"],
        "open_set_false_attribution": candidate["open_set_false_attributions"] <= control["open_set_false_attributions"],
    }


def compute_hard(
    args: argparse.Namespace, policy: dict[str, Any], lab: Any, candidate: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    freezer = load_module(FREEZE_SCRIPT, "murmurmark_remote_speaker_hard_v2_freezer")
    frozen = freezer.verify(args.out_dir / "private/hard-v2", args.policy, lab)
    if frozen.get("corpus_sha256") != candidate["hard_v2_corpus_sha256"]:
        raise AttributionError("hard_v2_frozen_corpus_pin_stale")
    scenarios = hard_scenarios(args)
    requests = hard_requests(lab, args, scenarios)
    minimum = float(read_json(TRUTH_LAB_POLICY)["analysis"]["minimum_analysis_sec"])
    if args.fixture_mode:
        wavlm_backend = lab.FixtureEmbeddingBackend("wavlm_duration_fixture_v2", minimum)
        resemblyzer_backend = lab.FixtureEmbeddingBackend("resemblyzer_fusion_fixture_v2", minimum)
    else:
        old_policy = read_json(TRUTH_LAB_POLICY)
        wavlm_backend = lab.WavLMBackend(old_policy, minimum, int(old_policy["analysis"]["wavlm_batch_size"]))
        resemblyzer_backend = lab.ResemblyzerBackend(minimum)
    all_requests = requests["word"] + requests["full"] + requests["windows"]
    cache = args.out_dir / "private/hard-v2/evaluation/cache"
    print("hard-v2: embed WavLM", flush=True)
    wavlm_embeddings = embed_cached(wavlm_backend, all_requests, cache, "wavlm")
    print("hard-v2: embed Resemblyzer", flush=True)
    resemblyzer_embeddings = embed_cached(resemblyzer_backend, requests["word"] + requests["full"], cache, "resemblyzer")
    selected_predictions = selected_hard_predictions(
        policy,
        lab,
        candidate,
        scenarios,
        requests,
        wavlm_embeddings,
        resemblyzer_embeddings,
    )
    resemblyzer_centroids = hard_centroids(resemblyzer_embeddings, requests["speakers"])
    baseline_predictions = control_predictions(lab, scenarios, resemblyzer_embeddings, resemblyzer_centroids)
    candidate_metrics = lab.evaluate_predictions(scenarios, selected_predictions, "hard_v2")
    control_metrics = lab.evaluate_predictions(scenarios, baseline_predictions, "hard_v2")
    gates_policy = policy["gates"]
    non_regression = metric_non_regression(candidate_metrics, control_metrics)
    gates = {
        "word_conservation": bool(candidate_metrics["word_conservation"]),
        "direct_truth_coverage": bool(candidate_metrics["direct_truth_coverage"]),
        "bcubed_f1": candidate_metrics["bcubed"]["f1"] >= float(gates_policy["minimum_bcubed_f1"]),
        "pairwise_precision": candidate_metrics["pairwise"]["precision"] >= float(gates_policy["minimum_pairwise_precision"]),
        "known_speaker_recall": candidate_metrics["known_attribution_recall"] >= float(gates_policy["minimum_known_speaker_recall"]),
        "boundary_recall": candidate_metrics["boundary_recall"] >= float(gates_policy["minimum_boundary_recall"]),
        "zero_open_set_false_attribution": candidate_metrics["open_set_false_attributions"] <= int(gates_policy["maximum_open_set_false_attributions"]),
        "mixed_words_fail_closed": candidate_metrics["mixed_safely_marked"] == candidate_metrics["mixed_words"],
        "coverage_v3_control_non_regression": all(non_regression.values()),
        "production_boundaries_unchanged": all(value is False for value in policy["production_boundaries"].values()),
        "hard_v2_not_used_for_selection": candidate["hard_v2_used_for_selection"] is False,
    }
    # The final policy flag is phrased as an allowed action, unlike the changed-state fields.
    gates["production_boundaries_unchanged"] = (
        policy["production_boundaries"]["selected_transcript_changed"] is False
        and policy["production_boundaries"]["coverage_v3_changed"] is False
        and policy["production_boundaries"]["raw_caf_changed"] is False
        and policy["production_boundaries"]["primary_asr_changed"] is False
        and policy["production_boundaries"]["echo_guard_changed"] is False
        and policy["production_boundaries"]["synthetic_labels_allowed_in_real_sessions"] is False
    )
    decision = "PROMOTE_LAB_CANDIDATE" if all(gates.values()) else "DO_NOT_PROMOTE_TOPOLOGY"
    public = {
        "schema": REPORT_SCHEMA,
        "version": VERSION,
        "decision": decision,
        "policy": fingerprint(args.policy),
        "implementation": fingerprint(Path(__file__).resolve()),
        "candidate_freeze_sha256": sha256(args.out_dir / "private/candidate_freeze.json"),
        "hard_v2": {
            "corpus_sha256": candidate["hard_v2_corpus_sha256"],
            "decision_open_count": 1,
            "used_for_selection": False,
            "scenario_count": len(scenarios),
        },
        "selected_topology": candidate["selected_topology"],
        "selected_config": candidate["selected_config"],
        "development_metrics": candidate["selected_development_metrics"],
        "hard_v2_metrics": candidate_metrics,
        "coverage_v3_control_metrics": control_metrics,
        "coverage_v3_non_regression": non_regression,
        "gates": gates,
        "blockers": sorted(name for name, passed in gates.items() if not passed),
        "production": {
            "changed": False,
            "synthetic_labels_applied_to_real_sessions": False,
        },
        "next": "bounded_real_audit_only" if decision == "PROMOTE_LAB_CANDIDATE" else "keep_coverage_v3_and_reject_duration_topologies",
    }
    assert_public_safe(public)
    return selected_predictions, baseline_predictions, public


def report_markdown(report: dict[str, Any]) -> str:
    candidate = report["hard_v2_metrics"]
    control = report["coverage_v3_control_metrics"]
    lines = [
        "# Duration-Aware Remote Speaker Attribution v2",
        "",
        f"- Decision: `{report['decision']}`",
        f"- Selected topology: `{report['selected_topology']}`",
        f"- Hard-v2 B-cubed F1: `{candidate['bcubed']['f1']:.6f}`",
        f"- Hard-v2 pairwise precision: `{candidate['pairwise']['precision']:.6f}`",
        f"- Hard-v2 known-speaker recall: `{candidate['known_attribution_recall']:.6f}`",
        f"- Hard-v2 boundary recall: `{candidate['boundary_recall']:.6f}`",
        f"- Hard-v2 open-set false attributions: `{candidate['open_set_false_attributions']}`",
        f"- Coverage v3 control B-cubed F1: `{control['bcubed']['f1']:.6f}`",
        f"- Coverage v3 control boundary recall: `{control['boundary_recall']:.6f}`",
        "",
        "## Blockers",
        "",
    ]
    lines.extend(f"- `{name}`" for name in report["blockers"])
    if not report["blockers"]:
        lines.append("- none")
    lines.extend(
        [
            "",
            "The result is laboratory-only. Production Coverage v3 and selected transcripts are unchanged.",
            "",
        ]
    )
    return "\n".join(lines)


def evaluate_hard(args: argparse.Namespace, policy: dict[str, Any], lab: Any) -> dict[str, Any]:
    candidate = verify_candidate(args)
    hard_public = verify_hard_public(args, policy)
    if hard_public["corpus_sha256"] != candidate["hard_v2_corpus_sha256"]:
        raise AttributionError("candidate_hard_v2_corpus_pin_stale")
    ledger = open_hard_once(args, policy, candidate)
    selected, control, public = compute_hard(args, policy, lab, candidate)
    evaluation = args.out_dir / "private/hard-v2/evaluation"
    write_jsonl(evaluation / "candidate_predictions.jsonl", selected)
    write_jsonl(evaluation / "coverage_v3_control_predictions.jsonl", control)
    write_json(args.out_dir / "duration_aware_remote_speaker_attribution_report.json", public)
    write_bytes(args.out_dir / "duration_aware_remote_speaker_attribution_report.md", report_markdown(public).encode())
    ledger_value = read_json(ledger)
    ledger_value.update(
        {
            "status": "completed",
            "decision": public["decision"],
            "report_sha256": sha256(args.out_dir / "duration_aware_remote_speaker_attribution_report.json"),
        }
    )
    write_json(ledger, ledger_value)
    print(f"decision: {public['decision']}")
    print(f"selected: {public['selected_topology']}")
    print(f"report: {portable(args.out_dir / 'duration_aware_remote_speaker_attribution_report.json')}")
    return public


def replay(args: argparse.Namespace, policy: dict[str, Any], lab: Any) -> dict[str, Any]:
    candidate = verify_candidate(args)
    ledger_path = args.out_dir / "private/hard-v2" / policy["hard_v2"]["opening_ledger"]
    ledger = read_json(ledger_path)
    if ledger.get("status") != "completed" or ledger.get("decision_open_count") != 1:
        raise AttributionError("hard_v2_decision_not_completed_once")
    selected, control, public = compute_hard(args, policy, lab, candidate)
    expected = {
        "candidate_predictions": sha256_bytes(b"".join(compact_json(row) + b"\n" for row in selected)),
        "control_predictions": sha256_bytes(b"".join(compact_json(row) + b"\n" for row in control)),
        "public_report": sha256_bytes(canonical_json(public)),
    }
    actual = {
        "candidate_predictions": sha256(args.out_dir / "private/hard-v2/evaluation/candidate_predictions.jsonl"),
        "control_predictions": sha256(args.out_dir / "private/hard-v2/evaluation/coverage_v3_control_predictions.jsonl"),
        "public_report": sha256(args.out_dir / "duration_aware_remote_speaker_attribution_report.json"),
    }
    matches = {key: expected[key] == actual[key] for key in expected}
    value = {
        "schema": REPLAY_SCHEMA,
        "decision": "DETERMINISTIC_REPLAY_VERIFIED" if all(matches.values()) else "REPLAY_MISMATCH",
        "candidate_sha256": sha256(args.out_dir / "private/candidate_freeze.json"),
        "hard_v2_corpus_sha256": candidate["hard_v2_corpus_sha256"],
        "decision_open_count": 1,
        "matches": matches,
    }
    assert_public_safe(value)
    write_json(args.out_dir / "replay_report.json", value)
    if value["decision"] != "DETERMINISTIC_REPLAY_VERIFIED":
        raise AttributionError("duration_aware_replay_mismatch")
    print("replay: deterministic")
    return value


def status(args: argparse.Namespace) -> int:
    hard_public = args.out_dir / "hard_v2_public_manifest.json"
    candidate = args.out_dir / "private/candidate_freeze.json"
    report = args.out_dir / "duration_aware_remote_speaker_attribution_report.json"
    if report.is_file():
        value = read_json(report)
        print(f"decision: {value['decision']}")
        print(f"selected: {value['selected_topology']}")
        print(f"blockers: {len(value['blockers'])}")
        return 0
    if candidate.is_file():
        value = read_json(candidate)
        print("decision: CANDIDATE_FROZEN_HARD_V2_UNOPENED")
        print(f"selected: {value['selected_topology']}")
        print("next: murmurmark corpus remote-duration-v2 evaluate-hard")
        return 0
    if hard_public.is_file():
        print("decision: HARD_V2_FROZEN_DEVELOPMENT_PENDING")
        print("next: murmurmark corpus remote-duration-v2 develop")
        return 0
    print("decision: HARD_V2_NOT_FROZEN")
    print("next: murmurmark corpus remote-duration-v2 freeze")
    return 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("develop", "evaluate-hard", "status", "replay"))
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--truth-lab-out", type=Path, default=DEFAULT_TRUTH_LAB)
    parser.add_argument("--fixture-mode", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.policy = args.policy.expanduser().resolve()
    args.out_dir = args.out_dir.expanduser().resolve()
    args.truth_lab_out = args.truth_lab_out.expanduser().resolve()
    try:
        policy = load_policy(args.policy)
        lab = load_module(TRUTH_LAB_SCRIPT, "murmurmark_truth_lab_v1_duration_v2")
        if args.action == "develop":
            develop(args, policy, lab)
        elif args.action == "evaluate-hard":
            evaluate_hard(args, policy, lab)
        elif args.action == "replay":
            replay(args, policy, lab)
        else:
            return status(args)
        return 0
    except AttributionError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
