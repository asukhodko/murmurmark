#!/usr/bin/env python3
"""Develop and one-shot evaluate segment-context remote-speaker attribution."""

from __future__ import annotations

import argparse
from collections import defaultdict
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
from typing import Any, Iterable

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.1.0"
POLICY_SCHEMA = "murmurmark.segment_context_remote_speaker_attribution_policy/v1"
DEVELOPMENT_SCHEMA = "murmurmark.segment_context_remote_speaker_development/v1"
CANDIDATE_SCHEMA = "murmurmark.segment_context_remote_speaker_candidate_freeze/v1"
REPORT_SCHEMA = "murmurmark.segment_context_remote_speaker_attribution_report/v1"
REPLAY_SCHEMA = "murmurmark.segment_context_remote_speaker_attribution_replay/v1"
PREDICTION_SCHEMA = "murmurmark.segment_context_remote_speaker_prediction/v1"
LEDGER_SCHEMA = "murmurmark.remote_speaker_hard_v3_opening_ledger/v1"
DEFAULT_POLICY = ROOT / "policies/segment-context-remote-speaker-attribution-v1.json"
DEFAULT_OUT = ROOT / "sessions/_reports/segment-context-remote-speaker-attribution-v1"
DEFAULT_TRUTH_LAB = ROOT / "sessions/_reports/controlled-remote-speaker-truth-lab-v1"
DEFAULT_DURATION_OUT = ROOT / "sessions/_reports/duration-aware-remote-speaker-attribution-v2"
TRUTH_LAB_POLICY = ROOT / "policies/controlled-remote-speaker-truth-lab-v1.json"
TRUTH_LAB_SCRIPT = ROOT / "scripts/controlled-remote-speaker-truth-lab-v1.py"
DURATION_POLICY = ROOT / "policies/duration-aware-remote-speaker-attribution-v2.json"
DURATION_EVALUATOR = ROOT / "scripts/evaluate-duration-aware-remote-speaker-attribution-v2.py"
FREEZE_SCRIPT = ROOT / "scripts/freeze-remote-speaker-hard-v3.py"
ABSOLUTE_PATH_MARKERS = ("/Users/", "/home/", "C:\\")
PRIVATE_KEYS = {
    "text",
    "system_voice",
    "private_seed",
    "vocabulary",
    "hard_vocabulary",
    "enrollment_scripts",
}


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
    topology_ids = {str(row.get("id")) for row in policy.get("topologies") or []}
    expected = {
        "silence_bounded_context_prototypes",
        "embedding_change_point_context",
        "conservative_dual_backend_context_fusion",
    }
    if topology_ids != expected or len(policy["topologies"]) != 3:
        raise AttributionError("predeclared_topology_scope_changed")
    selection = policy["selection"]
    if selection.get("truth_allowed_for_boundary_detection") is not False:
        raise AttributionError("speaker_truth_boundary_detection_forbidden")
    if selection.get("unknown_policy") != "fail_open":
        raise AttributionError("unknown_policy_must_fail_open")
    return policy


def topology(policy: dict[str, Any], topology_id: str) -> dict[str, Any]:
    for row in policy["topologies"]:
        if row["id"] == topology_id:
            return row
    raise AttributionError(f"unknown_topology:{topology_id}")


def verify_development_inputs(policy: dict[str, Any], *, fixture_mode: bool) -> None:
    if fixture_mode:
        return
    for source in ("truth_lab_v1", "duration_aware_v2"):
        for row in policy["development_evidence"][source].values():
            if not isinstance(row, dict) or "path" not in row:
                continue
            path = ROOT / str(row["path"])
            if not path.is_file() or sha256(path) != str(row["sha256"]):
                raise AttributionError(f"development_input_missing_or_stale:{source}")


def verify_hard_public(args: argparse.Namespace) -> dict[str, Any]:
    path = args.out_dir / "hard_v3_public_manifest.json"
    manifest = read_json(path)
    if manifest.get("schema") != "murmurmark.remote_speaker_hard_v3_public_manifest/v1":
        raise AttributionError("hard_v3_public_manifest_schema_invalid")
    if manifest.get("policy", {}).get("sha256") != sha256(args.policy):
        raise AttributionError("hard_v3_policy_stale")
    if manifest.get("implementation", {}).get("sha256") != sha256(FREEZE_SCRIPT):
        raise AttributionError("hard_v3_freezer_stale")
    if manifest.get("decision") != "HARD_V3_FROZEN_UNOPENED":
        raise AttributionError("hard_v3_not_frozen_unopened")
    if not manifest.get("scripts_disjoint_from_truth_lab_v1"):
        raise AttributionError("hard_v3_scripts_not_disjoint_from_v1")
    if not manifest.get("scripts_disjoint_from_hard_v2"):
        raise AttributionError("hard_v3_scripts_not_disjoint_from_v2")
    return manifest


def prefixed_scenario(source: dict[str, Any], corpus_id: str, split: str) -> dict[str, Any]:
    row = copy.deepcopy(source)
    prefix = f"{corpus_id}:"
    row["corpus_id"] = corpus_id
    row["split"] = split
    for word in row["words"]:
        word["speaker_id"] = prefix + str(word["speaker_id"])
        word["split"] = split
    for event in row["scenario"]["events"]:
        event["speaker_id"] = prefix + str(event["speaker_id"])
    for boundary in row["boundaries"]:
        boundary["left_speaker_id"] = prefix + str(boundary["left_speaker_id"])
        boundary["right_speaker_id"] = prefix + str(boundary["right_speaker_id"])
        boundary["split"] = split
    return row


def sealed_scenarios(root: Path, sealed_split: str, corpus_id: str, output_split: str) -> list[dict[str, Any]]:
    frozen = read_json(root / "frozen_manifest.json")
    rows = []
    for summary in frozen["scenario_summaries"]:
        scenario_id = str(summary["scenario_id"])
        directory = root / "sealed/sessions" / sealed_split / scenario_id
        rows.append(
            prefixed_scenario(
                {
                    "scenario_id": scenario_id,
                    "directory": directory,
                    "mixture": directory / "mixture.wav",
                    "scenario": read_json(directory / "scenario.json"),
                    "words": read_jsonl(directory / "truth_words.jsonl"),
                    "boundaries": read_jsonl(directory / "truth_boundaries.jsonl"),
                },
                corpus_id,
                output_split,
            )
        )
    return rows


def development_data(args: argparse.Namespace, lab: Any) -> tuple[list[dict[str, Any]], list[Any], dict[str, str]]:
    old_policy = lab.load_policy(TRUTH_LAB_POLICY)
    if not args.fixture_mode:
        lab.verify_frozen(args.truth_lab_out / "private", old_policy, TRUTH_LAB_POLICY)
    v1_raw = lab.scenario_paths(args.truth_lab_out / "private", old_policy)
    v1 = [prefixed_scenario(row, "truth_lab_v1", "development") for row in v1_raw]
    v2_root = args.duration_out / "private/hard-v2"
    v2 = sealed_scenarios(v2_root, "hard_v2", "hard_v2", "development")
    scenarios = v1 + v2
    requests = []
    enrollment_labels: dict[str, str] = {}
    raw_by_id = {str(row["scenario_id"]): row for row in v1_raw}
    for scenario in v1:
        raw = raw_by_id[scenario["scenario_id"]]
        if raw["split"] != "train":
            continue
        for event in scenario["scenario"]["events"]:
            key = f"enrollment:v1:{event['event_id']}"
            requests.append(
                lab.AudioRequest(
                    key,
                    scenario["scenario_id"],
                    scenario["mixture"],
                    float(event["start"]),
                    float(event["end"]),
                )
            )
            enrollment_labels[key] = str(event["speaker_id"])
    enrollment_manifest = read_json(v2_root / "enrollment/enrollment_manifest.json")
    for row in enrollment_manifest["rows"]:
        key = f"enrollment:v2:{row['speaker_id']}:full"
        requests.append(
            lab.AudioRequest(
                key,
                "hard_v2_enrollment",
                v2_root / row["path"],
                0.0,
                float(row["duration_sec"]),
            )
        )
        enrollment_labels[key] = "hard_v2:" + str(row["speaker_id"])
    for scenario in scenarios:
        for word in scenario["words"]:
            requests.append(
                lab.AudioRequest(
                    f"word:{word['word_id']}",
                    scenario["scenario_id"],
                    scenario["mixture"],
                    float(word["start"]),
                    float(word["end"]),
                )
            )
    return scenarios, requests, enrollment_labels


def hard_data(args: argparse.Namespace, lab: Any) -> tuple[list[dict[str, Any]], list[Any], dict[str, str]]:
    root = args.out_dir / "private/hard-v3"
    scenarios = sealed_scenarios(root, "hard_v3", "", "hard_v3")
    for scenario in scenarios:
        scenario["corpus_id"] = "hard_v3"
        for word in scenario["words"]:
            word["speaker_id"] = str(word["speaker_id"]).removeprefix(":")
        for event in scenario["scenario"]["events"]:
            event["speaker_id"] = str(event["speaker_id"]).removeprefix(":")
        for boundary in scenario["boundaries"]:
            boundary["left_speaker_id"] = str(boundary["left_speaker_id"]).removeprefix(":")
            boundary["right_speaker_id"] = str(boundary["right_speaker_id"]).removeprefix(":")
    requests = []
    enrollment_labels: dict[str, str] = {}
    enrollment_manifest = read_json(root / "enrollment/enrollment_manifest.json")
    for row in enrollment_manifest["rows"]:
        key = f"enrollment:hard_v3:{row['speaker_id']}:full"
        requests.append(
            lab.AudioRequest(
                key,
                "hard_v3_enrollment",
                root / row["path"],
                0.0,
                float(row["duration_sec"]),
            )
        )
        enrollment_labels[key] = str(row["speaker_id"])
    for scenario in scenarios:
        for word in scenario["words"]:
            requests.append(
                lab.AudioRequest(
                    f"word:{word['word_id']}",
                    scenario["scenario_id"],
                    scenario["mixture"],
                    float(word["start"]),
                    float(word["end"]),
                )
            )
    return scenarios, requests, enrollment_labels


def normalize(vector: np.ndarray) -> np.ndarray:
    values = np.asarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(values))
    if norm <= 1e-8:
        raise AttributionError("zero_embedding")
    return values / norm


def centroids(
    embeddings: dict[str, np.ndarray], labels: dict[str, str]
) -> dict[str, dict[str, np.ndarray]]:
    samples: dict[str, dict[str, list[np.ndarray]]] = defaultdict(lambda: defaultdict(list))
    for key, speaker in labels.items():
        corpus = speaker.split(":", 1)[0] if ":" in speaker else "hard_v3"
        samples[corpus][speaker].append(embeddings[key])
    return {
        corpus: {
            speaker: normalize(np.mean(vectors, axis=0))
            for speaker, vectors in speakers.items()
        }
        for corpus, speakers in samples.items()
    }


def scenario_centroids(
    row: dict[str, Any], values: dict[str, dict[str, np.ndarray]]
) -> dict[str, np.ndarray]:
    key = row["corpus_id"]
    return values[key]


def mean_word_vector(words: list[dict[str, Any]], embeddings: dict[str, np.ndarray]) -> np.ndarray:
    weighted = []
    weights = []
    for word in words:
        weighted.append(embeddings[f"word:{word['word_id']}"])
        weights.append(max(0.05, float(word["end"]) - float(word["start"])))
    return normalize(np.average(np.stack(weighted), axis=0, weights=np.asarray(weights)))


def probe_words(words: list[dict[str, Any]], boundary_index: int, side: str, seconds: float) -> list[dict[str, Any]]:
    if side == "left":
        anchor = float(words[boundary_index]["end"])
        indexes = range(boundary_index, -1, -1)
        chosen = [words[index] for index in indexes if anchor - float(words[index]["start"]) <= seconds]
        return list(reversed(chosen)) or [words[boundary_index]]
    anchor = float(words[boundary_index + 1]["start"])
    indexes = range(boundary_index + 1, len(words))
    chosen = [words[index] for index in indexes if float(words[index]["end"]) - anchor <= seconds]
    return chosen or [words[boundary_index + 1]]


def change_distance(
    words: list[dict[str, Any]],
    index: int,
    embeddings: dict[str, np.ndarray],
    probe_sec: float,
) -> float:
    left = mean_word_vector(probe_words(words, index, "left", probe_sec), embeddings)
    right = mean_word_vector(probe_words(words, index, "right", probe_sec), embeddings)
    return float(1.0 - float(left @ right))


def segment_words(
    scenario: dict[str, Any],
    topology_id: str,
    config: dict[str, float],
    wavlm_embeddings: dict[str, np.ndarray],
    resemblyzer_embeddings: dict[str, np.ndarray],
    spec: dict[str, Any],
) -> list[list[dict[str, Any]]]:
    words = sorted(scenario["words"], key=lambda row: (float(row["start"]), float(row["end"]), row["word_id"]))
    segments: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    probe_sec = float(spec.get("probe_window_sec") or 1.2)
    for index, word in enumerate(words):
        if word.get("overlap_word_ids"):
            if current:
                segments.append(current)
                current = []
            segments.append([word])
            continue
        split = False
        if current:
            left = current[-1]
            gap = float(word["start"]) - float(left["end"])
            prior_index = words.index(left)
            if topology_id == "silence_bounded_context_prototypes":
                split = gap >= config["silence_gap_sec"]
            elif topology_id == "embedding_change_point_context":
                distance = change_distance(words, prior_index, wavlm_embeddings, probe_sec)
                split = gap >= 0.5 or distance >= config["change_distance"]
            else:
                wavlm_distance = change_distance(words, prior_index, wavlm_embeddings, probe_sec)
                resemblyzer_distance = change_distance(words, prior_index, resemblyzer_embeddings, probe_sec)
                split = gap >= config["silence_gap_sec"] or (
                    wavlm_distance >= config["change_distance"]
                    and resemblyzer_distance >= config["change_distance"]
                )
            if float(word["end"]) - float(current[0]["start"]) > float(spec["maximum_context_sec"]):
                split = True
        if split and current:
            segments.append(current)
            current = []
        current.append(word)
    if current:
        segments.append(current)
    return segments


def accepted(
    vector: np.ndarray,
    known: dict[str, np.ndarray],
    minimum_similarity: float,
    minimum_margin: float,
) -> dict[str, Any]:
    scores = sorted(
        ((float(vector @ centroid), speaker) for speaker, centroid in known.items()),
        reverse=True,
    )
    if not scores:
        return {"speaker_id": None, "top_speaker_id": None, "similarity": None, "margin": None}
    similarity, speaker = scores[0]
    margin = similarity - scores[1][0] if len(scores) > 1 else similarity
    return {
        "speaker_id": speaker if similarity >= minimum_similarity and margin >= minimum_margin else None,
        "top_speaker_id": speaker,
        "similarity": round(similarity, 6),
        "margin": round(margin, 6),
    }


def predict(
    policy: dict[str, Any],
    scenarios: list[dict[str, Any]],
    wavlm_embeddings: dict[str, np.ndarray],
    resemblyzer_embeddings: dict[str, np.ndarray],
    wavlm_centroids: dict[str, dict[str, np.ndarray]],
    resemblyzer_centroids: dict[str, dict[str, np.ndarray]],
    topology_id: str,
    config: dict[str, float],
    track: str,
    segment_cache: dict[tuple[Any, ...], list[list[dict[str, Any]]]] | None = None,
    vector_cache: dict[tuple[str, str, str, tuple[str, ...]], np.ndarray] | None = None,
) -> list[dict[str, Any]]:
    spec = topology(policy, topology_id)
    segment_cache = segment_cache if segment_cache is not None else {}
    vector_cache = vector_cache if vector_cache is not None else {}
    rows = []
    for scenario in scenarios:
        wavlm_known = scenario_centroids(scenario, wavlm_centroids)
        resemblyzer_known = scenario_centroids(scenario, resemblyzer_centroids)
        segment_key = (
            str(scenario["corpus_id"]),
            str(scenario["scenario_id"]),
            topology_id,
            config.get("silence_gap_sec"),
            config.get("change_distance"),
        )
        segments = segment_cache.get(segment_key)
        if segments is None:
            segments = segment_words(
                scenario,
                topology_id,
                config,
                wavlm_embeddings,
                resemblyzer_embeddings,
                spec,
            )
            segment_cache[segment_key] = segments
        for segment_index, segment in enumerate(segments):
            duration = float(segment[-1]["end"]) - float(segment[0]["start"])
            mixed = any(word.get("overlap_word_ids") for word in segment)
            wavlm = {"speaker_id": None, "top_speaker_id": None, "similarity": None, "margin": None}
            resemblyzer = dict(wavlm)
            if not mixed and duration >= config["minimum_context_sec"]:
                word_ids = tuple(str(word["word_id"]) for word in segment)
                vector_context = (
                    str(scenario["corpus_id"]),
                    str(scenario["scenario_id"]),
                    word_ids,
                )
                wavlm_key = ("wavlm", *vector_context)
                wavlm_vector = vector_cache.get(wavlm_key)
                if wavlm_vector is None:
                    wavlm_vector = mean_word_vector(segment, wavlm_embeddings)
                    vector_cache[wavlm_key] = wavlm_vector
                wavlm = accepted(
                    wavlm_vector,
                    wavlm_known,
                    config["wavlm_similarity"],
                    config["wavlm_margin"],
                )
                if topology_id == "conservative_dual_backend_context_fusion":
                    resemblyzer_key = ("resemblyzer", *vector_context)
                    resemblyzer_vector = vector_cache.get(resemblyzer_key)
                    if resemblyzer_vector is None:
                        resemblyzer_vector = mean_word_vector(segment, resemblyzer_embeddings)
                        vector_cache[resemblyzer_key] = resemblyzer_vector
                    resemblyzer = accepted(
                        resemblyzer_vector,
                        resemblyzer_known,
                        config["resemblyzer_similarity"],
                        config["resemblyzer_margin"],
                    )
            if mixed:
                speaker = "mixed"
                reason = "timestamp_overlap"
            elif topology_id == "conservative_dual_backend_context_fusion":
                agreed = wavlm["speaker_id"] is not None and wavlm["speaker_id"] == resemblyzer["speaker_id"]
                speaker = str(wavlm["speaker_id"]) if agreed else "unknown_speaker"
                reason = "segment_backend_agreement" if agreed else "segment_fusion_abstained"
            else:
                speaker = str(wavlm["speaker_id"]) if wavlm["speaker_id"] else "unknown_speaker"
                reason = "segment_context_accepted" if wavlm["speaker_id"] else "segment_context_abstained"
            for word in segment:
                rows.append(
                    {
                        "schema": PREDICTION_SCHEMA,
                        "track": track,
                        "word_id": word["word_id"],
                        "scenario_id": scenario["scenario_id"],
                        "split": scenario["split"],
                        "speaker_id": speaker,
                        "top_speaker_id": wavlm["top_speaker_id"],
                        "similarity": wavlm["similarity"],
                        "margin": wavlm["margin"],
                        "segment_index": segment_index,
                        "segment_duration_sec": round(duration, 6),
                        "reason": reason,
                    }
                )
    return rows


def bcubed(truth: list[str], predicted: list[str]) -> dict[str, float]:
    truth_members: dict[str, set[int]] = defaultdict(set)
    predicted_members: dict[str, set[int]] = defaultdict(set)
    for index, (expected, actual) in enumerate(zip(truth, predicted)):
        truth_members[expected].add(index)
        predicted_members[actual].add(index)
    precisions = []
    recalls = []
    for index, (expected, actual) in enumerate(zip(truth, predicted)):
        intersection = len(truth_members[expected] & predicted_members[actual])
        precisions.append(intersection / len(predicted_members[actual]))
        recalls.append(intersection / len(truth_members[expected]))
    precision = float(np.mean(precisions)) if precisions else 0.0
    recall = float(np.mean(recalls)) if recalls else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": round(precision, 6), "recall": round(recall, 6), "f1": round(f1, 6)}


def pairwise(truth: list[str], predicted: list[str]) -> dict[str, Any]:
    truth_counts: dict[str, int] = defaultdict(int)
    predicted_counts: dict[str, int] = defaultdict(int)
    contingency: dict[tuple[str, str], int] = defaultdict(int)
    for expected, actual in zip(truth, predicted):
        truth_counts[expected] += 1
        if not actual.startswith("unknown:"):
            predicted_counts[actual] += 1
            contingency[(expected, actual)] += 1

    true_positive = sum(count * (count - 1) // 2 for count in contingency.values())
    predicted_positive = sum(count * (count - 1) // 2 for count in predicted_counts.values())
    truth_positive = sum(count * (count - 1) // 2 for count in truth_counts.values())
    false_positive = predicted_positive - true_positive
    false_negative = truth_positive - true_positive
    precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 1.0
    recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "f1": round(f1, 6),
        "true_positive_pairs": true_positive,
        "false_positive_pairs": false_positive,
        "false_negative_pairs": false_negative,
    }


def evaluate(scenarios: list[dict[str, Any]], predictions: list[dict[str, Any]]) -> dict[str, Any]:
    truth_rows = [word for scenario in scenarios for word in scenario["words"]]
    predicted = {str(row["word_id"]): row for row in predictions}
    conservation = len(predicted) == len(truth_rows) and set(predicted) == {
        str(row["word_id"]) for row in truth_rows
    }
    known = [row for row in truth_rows if row["truth_class"] == "known_speaker"]
    truth_labels = [str(row["speaker_id"]) for row in known]
    predicted_labels = []
    accepted_count = 0
    correct = 0
    for row in known:
        value = str(predicted[str(row["word_id"])]["speaker_id"])
        if value in {"unknown_speaker", "mixed"}:
            predicted_labels.append(f"unknown:{row['word_id']}")
        else:
            predicted_labels.append(value)
            accepted_count += 1
            correct += int(value == str(row["speaker_id"]))
    open_set = [row for row in truth_rows if row["truth_class"] == "open_set_speaker"]
    open_false = sum(
        str(predicted[str(row["word_id"])]["speaker_id"]) not in {"unknown_speaker", "mixed"}
        for row in open_set
    )
    mixed = [row for row in truth_rows if row["truth_class"] == "mixed"]
    mixed_safe = sum(
        predicted[str(row["word_id"])]["speaker_id"] == "mixed" for row in mixed
    )
    boundaries = [
        boundary
        for scenario in scenarios
        for boundary in scenario["boundaries"]
        if boundary["evaluation"]
    ]
    recovered = 0
    for boundary in boundaries:
        left = str(predicted[str(boundary["left_word_id"])]["speaker_id"])
        right = str(predicted[str(boundary["right_word_id"])]["speaker_id"])
        recovered += int(
            left == str(boundary["left_speaker_id"])
            and right == str(boundary["right_speaker_id"])
            and left != right
        )
    bcubed_metrics = bcubed(truth_labels, predicted_labels)
    pairwise_metrics = pairwise(truth_labels, predicted_labels)
    return {
        "word_count": len(truth_rows),
        "prediction_count": len(predicted),
        "word_conservation": conservation,
        "direct_truth_coverage": all(row.get("truth_source") == "exact_scripted" for row in truth_rows),
        "known_single_speaker_words": len(known),
        "known_attributed_words": accepted_count,
        "known_correct_words": correct,
        "known_attribution_coverage": round(accepted_count / len(known), 6) if known else 0.0,
        "known_speaker_recall": round(correct / len(known), 6) if known else 0.0,
        "known_attributed_precision": round(correct / accepted_count, 6) if accepted_count else None,
        "bcubed": bcubed_metrics,
        "pairwise": pairwise_metrics,
        "open_set_words": len(open_set),
        "open_set_false_attributions": open_false,
        "mixed_words": len(mixed),
        "mixed_safely_marked": mixed_safe,
        "boundary_count": len(boundaries),
        "boundaries_recovered": recovered,
        "boundary_recall": round(recovered / len(boundaries), 6) if boundaries else 0.0,
    }


def trial_score(metrics: dict[str, Any], config: dict[str, Any]) -> tuple[Any, ...]:
    return (
        -int(metrics["open_set_false_attributions"]),
        float(metrics["pairwise"]["precision"]),
        float(metrics["boundary_recall"]),
        float(metrics["bcubed"]["f1"]),
        float(metrics["known_speaker_recall"]),
        canonical_json(config),
    )


def best_trial(trials: list[dict[str, Any]]) -> dict[str, Any]:
    if not trials:
        raise AttributionError("topology_has_no_trials")
    return max(trials, key=lambda row: trial_score(row["metrics"], row["config"]))


def trial_configs(spec: dict[str, Any]) -> Iterable[dict[str, float]]:
    topology_id = spec["id"]
    if topology_id == "silence_bounded_context_prototypes":
        for gap in spec["silence_gap_grid_sec"]:
            for context in spec["minimum_context_grid_sec"]:
                for similarity in spec["similarity_grid"]:
                    for margin in spec["margin_grid"]:
                        yield {
                            "silence_gap_sec": float(gap),
                            "minimum_context_sec": float(context),
                            "wavlm_similarity": float(similarity),
                            "wavlm_margin": float(margin),
                            "resemblyzer_similarity": 1.0,
                            "resemblyzer_margin": 1.0,
                        }
        return
    if topology_id == "embedding_change_point_context":
        for distance in spec["change_distance_grid"]:
            for context in spec["minimum_context_grid_sec"]:
                for similarity in spec["similarity_grid"]:
                    for margin in spec["margin_grid"]:
                        yield {
                            "change_distance": float(distance),
                            "minimum_context_sec": float(context),
                            "wavlm_similarity": float(similarity),
                            "wavlm_margin": float(margin),
                            "resemblyzer_similarity": 1.0,
                            "resemblyzer_margin": 1.0,
                        }
        return
    for gap in spec["silence_gap_grid_sec"]:
        for distance in spec["change_distance_grid"]:
            for context in spec["minimum_context_grid_sec"]:
                for wavlm_similarity in spec["wavlm_similarity_grid"]:
                    for wavlm_margin in spec["wavlm_margin_grid"]:
                        for resemblyzer_similarity in spec["resemblyzer_similarity_grid"]:
                            for resemblyzer_margin in spec["resemblyzer_margin_grid"]:
                                yield {
                                    "silence_gap_sec": float(gap),
                                    "change_distance": float(distance),
                                    "minimum_context_sec": float(context),
                                    "wavlm_similarity": float(wavlm_similarity),
                                    "wavlm_margin": float(wavlm_margin),
                                    "resemblyzer_similarity": float(resemblyzer_similarity),
                                    "resemblyzer_margin": float(resemblyzer_margin),
                                }


def tune_topology(
    policy: dict[str, Any],
    scenarios: list[dict[str, Any]],
    wavlm_embeddings: dict[str, np.ndarray],
    resemblyzer_embeddings: dict[str, np.ndarray],
    wavlm_centroids: dict[str, dict[str, np.ndarray]],
    resemblyzer_centroids: dict[str, dict[str, np.ndarray]],
    topology_id: str,
) -> dict[str, Any]:
    trials = []
    segment_cache: dict[tuple[Any, ...], list[list[dict[str, Any]]]] = {}
    vector_cache: dict[tuple[str, str, str, tuple[str, ...]], np.ndarray] = {}
    for config in trial_configs(topology(policy, topology_id)):
        predictions = predict(
            policy,
            scenarios,
            wavlm_embeddings,
            resemblyzer_embeddings,
            wavlm_centroids,
            resemblyzer_centroids,
            topology_id,
            config,
            topology_id,
            segment_cache,
            vector_cache,
        )
        trials.append({"config": config, "metrics": evaluate(scenarios, predictions)})
    selected = best_trial(trials)
    predictions = predict(
        policy,
        scenarios,
        wavlm_embeddings,
        resemblyzer_embeddings,
        wavlm_centroids,
        resemblyzer_centroids,
        topology_id,
        selected["config"],
        topology_id,
        segment_cache,
        vector_cache,
    )
    return {
        "id": topology_id,
        "trial_count": len(trials),
        "selected": selected,
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


def backends(lab: Any, fixture_mode: bool) -> tuple[Any, Any]:
    old_policy = read_json(TRUTH_LAB_POLICY)
    minimum = float(old_policy["analysis"]["minimum_analysis_sec"])
    if fixture_mode:
        return (
            lab.FixtureEmbeddingBackend("wavlm_segment_context_fixture_v1", minimum),
            lab.FixtureEmbeddingBackend("resemblyzer_segment_context_fixture_v1", minimum),
        )
    return (
        lab.WavLMBackend(old_policy, minimum, int(old_policy["analysis"]["wavlm_batch_size"])),
        lab.ResemblyzerBackend(minimum),
    )


def develop(args: argparse.Namespace, policy: dict[str, Any], lab: Any, common: Any) -> dict[str, Any]:
    verify_development_inputs(policy, fixture_mode=args.fixture_mode)
    hard_public = verify_hard_public(args)
    ledger = args.out_dir / "private/hard-v3" / policy["hard_v3"]["opening_ledger"]
    if ledger.exists():
        raise AttributionError("hard_v3_already_opened_before_development")
    scenarios, requests, enrollment_labels = development_data(args, lab)
    wavlm_backend, resemblyzer_backend = backends(lab, args.fixture_mode)
    cache = args.out_dir / "private/development/cache"
    print("develop: embed WavLM", flush=True)
    wavlm_embeddings = common.embed_cached(wavlm_backend, requests, cache, "wavlm")
    print("develop: embed Resemblyzer", flush=True)
    resemblyzer_embeddings = common.embed_cached(resemblyzer_backend, requests, cache, "resemblyzer")
    wavlm_known = centroids(wavlm_embeddings, enrollment_labels)
    resemblyzer_known = centroids(resemblyzer_embeddings, enrollment_labels)
    rows = []
    for topology_id in (
        "silence_bounded_context_prototypes",
        "embedding_change_point_context",
        "conservative_dual_backend_context_fusion",
    ):
        print(f"develop: tune {topology_id}", flush=True)
        rows.append(
            tune_topology(
                policy,
                scenarios,
                wavlm_embeddings,
                resemblyzer_embeddings,
                wavlm_known,
                resemblyzer_known,
                topology_id,
            )
        )
    selected = select_topology(rows)
    report = {
        "schema": DEVELOPMENT_SCHEMA,
        "version": VERSION,
        "decision": "CANDIDATE_SELECTED_ON_DEVELOPMENT",
        "policy": fingerprint(args.policy),
        "implementation": fingerprint(Path(__file__).resolve()),
        "development_sources": ["truth_lab_v1", "hard_v2"],
        "hard_v3": {
            "corpus_sha256": hard_public["corpus_sha256"],
            "opened": False,
            "used_for_selection": False,
        },
        "topologies": [public_topology(row) for row in rows],
        "selected_topology": selected["id"],
        "selected_config": selected["selected"]["config"],
        "selected_development_metrics": selected["selected"]["metrics"],
        "backends": {
            "wavlm": wavlm_backend.provenance,
            "resemblyzer": resemblyzer_backend.provenance,
        },
        "production": {"changed": False},
    }
    assert_public_safe(report)
    write_json(args.out_dir / "development_report.json", report)
    write_jsonl(args.out_dir / "private/development/selected_predictions.jsonl", selected["predictions"])
    candidate = {
        "schema": CANDIDATE_SCHEMA,
        "version": VERSION,
        "policy": fingerprint(args.policy),
        "implementation": fingerprint(Path(__file__).resolve()),
        "freezer": fingerprint(FREEZE_SCRIPT),
        "hard_v3_public_manifest": fingerprint(args.out_dir / "hard_v3_public_manifest.json"),
        "hard_v3_frozen_manifest": fingerprint(args.out_dir / "private/hard-v3/frozen_manifest.json"),
        "hard_v3_corpus_sha256": hard_public["corpus_sha256"],
        "hard_v3_used_for_selection": False,
        "development_report_sha256": sha256(args.out_dir / "development_report.json"),
        "selected_topology": selected["id"],
        "selected_config": selected["selected"]["config"],
        "selected_development_metrics": selected["selected"]["metrics"],
    }
    write_json(args.out_dir / "private/candidate_freeze.json", candidate)
    print(f"selected: {selected['id']}")
    print("hard-v3 remains unopened")
    return report


def verify_candidate(args: argparse.Namespace) -> dict[str, Any]:
    path = args.out_dir / "private/candidate_freeze.json"
    candidate = read_json(path)
    if candidate.get("schema") != CANDIDATE_SCHEMA:
        raise AttributionError("candidate_schema_invalid")
    pins = {
        "policy": (args.policy, candidate["policy"]["sha256"]),
        "implementation": (Path(__file__).resolve(), candidate["implementation"]["sha256"]),
        "freezer": (FREEZE_SCRIPT, candidate["freezer"]["sha256"]),
        "hard_v3_public": (
            args.out_dir / "hard_v3_public_manifest.json",
            candidate["hard_v3_public_manifest"]["sha256"],
        ),
        "hard_v3_frozen": (
            args.out_dir / "private/hard-v3/frozen_manifest.json",
            candidate["hard_v3_frozen_manifest"]["sha256"],
        ),
        "development": (
            args.out_dir / "development_report.json",
            candidate["development_report_sha256"],
        ),
    }
    for name, (path_value, expected) in pins.items():
        if not path_value.is_file() or sha256(path_value) != expected:
            raise AttributionError(f"candidate_pin_stale:{name}")
    if candidate.get("hard_v3_used_for_selection") is not False:
        raise AttributionError("candidate_used_hard_v3_for_selection")
    return candidate


def open_hard_once(args: argparse.Namespace, policy: dict[str, Any], candidate: dict[str, Any]) -> Path:
    ledger = args.out_dir / "private/hard-v3" / policy["hard_v3"]["opening_ledger"]
    payload = {
        "schema": LEDGER_SCHEMA,
        "decision_open_count": 1,
        "candidate_sha256": sha256(args.out_dir / "private/candidate_freeze.json"),
        "hard_v3_corpus_sha256": candidate["hard_v3_corpus_sha256"],
        "status": "opening",
    }
    try:
        descriptor = os.open(ledger, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as error:
        existing = read_json(ledger)
        if existing.get("status") == "opening" and existing.get("candidate_sha256") == payload["candidate_sha256"]:
            return ledger
        raise AttributionError("hard_v3_decision_opening_already_consumed") from error
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(canonical_json(payload))
        handle.flush()
        os.fsync(handle.fileno())
    return ledger


def control_predictions(
    scenarios: list[dict[str, Any]],
    embeddings: dict[str, np.ndarray],
    known: dict[str, dict[str, np.ndarray]],
) -> list[dict[str, Any]]:
    old = read_json(TRUTH_LAB_POLICY)["analysis"]
    rows = []
    for scenario in scenarios:
        centroids_for_scenario = scenario_centroids(scenario, known)
        for word in scenario["words"]:
            if word.get("overlap_word_ids"):
                speaker = "mixed"
                raw = {"top_speaker_id": None, "similarity": None, "margin": None}
            else:
                raw = accepted(
                    embeddings[f"word:{word['word_id']}"],
                    centroids_for_scenario,
                    float(old["coverage_v3_similarity"]),
                    float(old["coverage_v3_margin"]),
                )
                speaker = str(raw["speaker_id"]) if raw["speaker_id"] else "unknown_speaker"
            rows.append(
                {
                    "schema": PREDICTION_SCHEMA,
                    "track": "coverage_v3_control",
                    "word_id": word["word_id"],
                    "scenario_id": scenario["scenario_id"],
                    "split": scenario["split"],
                    "speaker_id": speaker,
                    "top_speaker_id": raw["top_speaker_id"],
                    "similarity": raw["similarity"],
                    "margin": raw["margin"],
                    "reason": "coverage_v3_control",
                }
            )
    return rows


def metric_non_regression(candidate: dict[str, Any], control: dict[str, Any]) -> dict[str, bool]:
    return {
        "bcubed_f1": candidate["bcubed"]["f1"] >= control["bcubed"]["f1"],
        "pairwise_precision": candidate["pairwise"]["precision"] >= control["pairwise"]["precision"],
        "known_speaker_recall": candidate["known_speaker_recall"] >= control["known_speaker_recall"],
        "boundary_recall": candidate["boundary_recall"] >= control["boundary_recall"],
        "open_set_false_attribution": candidate["open_set_false_attributions"] <= control["open_set_false_attributions"],
    }


def compute_hard(
    args: argparse.Namespace,
    policy: dict[str, Any],
    lab: Any,
    common: Any,
    candidate: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    freezer = load_module(FREEZE_SCRIPT, "murmurmark_remote_speaker_hard_v3_freezer")
    frozen = freezer.verify(args.out_dir / "private/hard-v3", args.policy, lab)
    if frozen.get("corpus_sha256") != candidate["hard_v3_corpus_sha256"]:
        raise AttributionError("hard_v3_frozen_corpus_pin_stale")
    scenarios, requests, enrollment_labels = hard_data(args, lab)
    wavlm_backend, resemblyzer_backend = backends(lab, args.fixture_mode)
    cache = args.out_dir / "private/hard-v3/evaluation/cache"
    print("hard-v3: embed WavLM", flush=True)
    wavlm_embeddings = common.embed_cached(wavlm_backend, requests, cache, "wavlm")
    print("hard-v3: embed Resemblyzer", flush=True)
    resemblyzer_embeddings = common.embed_cached(resemblyzer_backend, requests, cache, "resemblyzer")
    wavlm_known = centroids(wavlm_embeddings, enrollment_labels)
    resemblyzer_known = centroids(resemblyzer_embeddings, enrollment_labels)
    selected = predict(
        policy,
        scenarios,
        wavlm_embeddings,
        resemblyzer_embeddings,
        wavlm_known,
        resemblyzer_known,
        str(candidate["selected_topology"]),
        {key: float(value) for key, value in candidate["selected_config"].items()},
        str(candidate["selected_topology"]),
    )
    control = control_predictions(scenarios, resemblyzer_embeddings, resemblyzer_known)
    candidate_metrics = evaluate(scenarios, selected)
    control_metrics = evaluate(scenarios, control)
    non_regression = metric_non_regression(candidate_metrics, control_metrics)
    gates_policy = policy["gates"]
    gates = {
        "word_conservation": bool(candidate_metrics["word_conservation"]),
        "direct_truth_coverage": bool(candidate_metrics["direct_truth_coverage"]),
        "bcubed_f1": candidate_metrics["bcubed"]["f1"] >= float(gates_policy["minimum_bcubed_f1"]),
        "pairwise_precision": candidate_metrics["pairwise"]["precision"] >= float(gates_policy["minimum_pairwise_precision"]),
        "known_speaker_recall": candidate_metrics["known_speaker_recall"] >= float(gates_policy["minimum_known_speaker_recall"]),
        "boundary_recall": candidate_metrics["boundary_recall"] >= float(gates_policy["minimum_boundary_recall"]),
        "zero_open_set_false_attribution": candidate_metrics["open_set_false_attributions"] <= int(gates_policy["maximum_open_set_false_attributions"]),
        "mixed_words_fail_closed": candidate_metrics["mixed_safely_marked"] == candidate_metrics["mixed_words"],
        "coverage_v3_control_non_regression": all(non_regression.values()),
        "hard_v3_not_used_for_selection": candidate["hard_v3_used_for_selection"] is False,
        "production_boundaries_unchanged": all(
            value is False for value in policy["production_boundaries"].values()
        ),
    }
    decision = "PROMOTE_LAB_CANDIDATE" if all(gates.values()) else "DO_NOT_PROMOTE_SEGMENT_CONTEXT"
    report = {
        "schema": REPORT_SCHEMA,
        "version": VERSION,
        "decision": decision,
        "policy": fingerprint(args.policy),
        "implementation": fingerprint(Path(__file__).resolve()),
        "candidate_freeze_sha256": sha256(args.out_dir / "private/candidate_freeze.json"),
        "hard_v3": {
            "corpus_sha256": candidate["hard_v3_corpus_sha256"],
            "decision_open_count": 1,
            "used_for_selection": False,
            "scenario_count": len(scenarios),
        },
        "selected_topology": candidate["selected_topology"],
        "selected_config": candidate["selected_config"],
        "development_metrics": candidate["selected_development_metrics"],
        "hard_v3_metrics": candidate_metrics,
        "coverage_v3_control_metrics": control_metrics,
        "coverage_v3_non_regression": non_regression,
        "gates": gates,
        "blockers": sorted(name for name, passed in gates.items() if not passed),
        "production": {"changed": False, "synthetic_labels_applied_to_real_sessions": False},
        "next": "bounded_real_audit_only" if decision == "PROMOTE_LAB_CANDIDATE" else "keep_coverage_v3_and_close_segment_context_topologies",
    }
    assert_public_safe(report)
    return selected, control, report


def report_markdown(report: dict[str, Any]) -> str:
    candidate = report["hard_v3_metrics"]
    control = report["coverage_v3_control_metrics"]
    lines = [
        "# Segment-Context Remote Speaker Attribution v1",
        "",
        f"- Decision: `{report['decision']}`",
        f"- Selected topology: `{report['selected_topology']}`",
        f"- Hard-v3 B-cubed F1: `{candidate['bcubed']['f1']:.6f}`",
        f"- Hard-v3 pairwise precision: `{candidate['pairwise']['precision']:.6f}`",
        f"- Hard-v3 known-speaker recall: `{candidate['known_speaker_recall']:.6f}`",
        f"- Hard-v3 boundary recall: `{candidate['boundary_recall']:.6f}`",
        f"- Hard-v3 open-set false attributions: `{candidate['open_set_false_attributions']}`",
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


def evaluate_hard(args: argparse.Namespace, policy: dict[str, Any], lab: Any, common: Any) -> dict[str, Any]:
    candidate = verify_candidate(args)
    hard_public = verify_hard_public(args)
    if hard_public["corpus_sha256"] != candidate["hard_v3_corpus_sha256"]:
        raise AttributionError("candidate_hard_v3_corpus_pin_stale")
    ledger = open_hard_once(args, policy, candidate)
    selected, control, report = compute_hard(args, policy, lab, common, candidate)
    evaluation = args.out_dir / "private/hard-v3/evaluation"
    write_jsonl(evaluation / "candidate_predictions.jsonl", selected)
    write_jsonl(evaluation / "coverage_v3_control_predictions.jsonl", control)
    write_json(args.out_dir / "segment_context_remote_speaker_attribution_report.json", report)
    write_bytes(
        args.out_dir / "segment_context_remote_speaker_attribution_report.md",
        report_markdown(report).encode(),
    )
    ledger_value = read_json(ledger)
    ledger_value.update(
        {
            "status": "completed",
            "decision": report["decision"],
            "report_sha256": sha256(args.out_dir / "segment_context_remote_speaker_attribution_report.json"),
        }
    )
    write_json(ledger, ledger_value)
    print(f"decision: {report['decision']}")
    print(f"selected: {report['selected_topology']}")
    return report


def replay(args: argparse.Namespace, policy: dict[str, Any], lab: Any, common: Any) -> dict[str, Any]:
    candidate = verify_candidate(args)
    ledger_path = args.out_dir / "private/hard-v3" / policy["hard_v3"]["opening_ledger"]
    ledger = read_json(ledger_path)
    if ledger.get("status") != "completed" or ledger.get("decision_open_count") != 1:
        raise AttributionError("hard_v3_decision_not_completed_once")
    selected, control, report = compute_hard(args, policy, lab, common, candidate)
    expected = {
        "candidate_predictions": sha256_bytes(b"".join(compact_json(row) + b"\n" for row in selected)),
        "control_predictions": sha256_bytes(b"".join(compact_json(row) + b"\n" for row in control)),
        "public_report": sha256_bytes(canonical_json(report)),
    }
    actual = {
        "candidate_predictions": sha256(args.out_dir / "private/hard-v3/evaluation/candidate_predictions.jsonl"),
        "control_predictions": sha256(args.out_dir / "private/hard-v3/evaluation/coverage_v3_control_predictions.jsonl"),
        "public_report": sha256(args.out_dir / "segment_context_remote_speaker_attribution_report.json"),
    }
    matches = {key: expected[key] == actual[key] for key in expected}
    value = {
        "schema": REPLAY_SCHEMA,
        "decision": "DETERMINISTIC_REPLAY_VERIFIED" if all(matches.values()) else "REPLAY_MISMATCH",
        "candidate_sha256": sha256(args.out_dir / "private/candidate_freeze.json"),
        "hard_v3_corpus_sha256": candidate["hard_v3_corpus_sha256"],
        "decision_open_count": 1,
        "matches": matches,
    }
    assert_public_safe(value)
    write_json(args.out_dir / "replay_report.json", value)
    if value["decision"] != "DETERMINISTIC_REPLAY_VERIFIED":
        raise AttributionError("segment_context_replay_mismatch")
    print("replay: deterministic")
    return value


def status(args: argparse.Namespace) -> int:
    hard_public = args.out_dir / "hard_v3_public_manifest.json"
    candidate = args.out_dir / "private/candidate_freeze.json"
    report = args.out_dir / "segment_context_remote_speaker_attribution_report.json"
    if report.is_file():
        value = read_json(report)
        print(f"decision: {value['decision']}")
        print(f"selected: {value['selected_topology']}")
        print(f"blockers: {len(value['blockers'])}")
        return 0
    if candidate.is_file():
        value = read_json(candidate)
        print("decision: CANDIDATE_FROZEN_HARD_V3_UNOPENED")
        print(f"selected: {value['selected_topology']}")
        print("next: murmurmark corpus remote-segment-context evaluate-hard")
        return 0
    if hard_public.is_file():
        print("decision: HARD_V3_FROZEN_DEVELOPMENT_PENDING")
        print("next: murmurmark corpus remote-segment-context develop")
        return 0
    print("decision: HARD_V3_NOT_FROZEN")
    print("next: murmurmark corpus remote-segment-context freeze")
    return 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("develop", "evaluate-hard", "status", "replay"))
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--truth-lab-out", type=Path, default=DEFAULT_TRUTH_LAB)
    parser.add_argument("--duration-out", type=Path, default=DEFAULT_DURATION_OUT)
    parser.add_argument("--fixture-mode", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.policy = args.policy.expanduser().resolve()
    args.out_dir = args.out_dir.expanduser().resolve()
    args.truth_lab_out = args.truth_lab_out.expanduser().resolve()
    args.duration_out = args.duration_out.expanduser().resolve()
    try:
        policy = load_policy(args.policy)
        lab = load_module(TRUTH_LAB_SCRIPT, "murmurmark_truth_lab_v1_segment_context")
        common = load_module(DURATION_EVALUATOR, "murmurmark_duration_v2_segment_context_common")
        if args.action == "develop":
            develop(args, policy, lab, common)
        elif args.action == "evaluate-hard":
            evaluate_hard(args, policy, lab, common)
        elif args.action == "replay":
            replay(args, policy, lab, common)
        else:
            return status(args)
        return 0
    except (AttributionError, OSError, KeyError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
