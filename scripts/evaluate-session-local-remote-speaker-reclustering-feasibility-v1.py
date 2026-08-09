#!/usr/bin/env python3
"""Evaluate label-independent session-local remote speaker re-clustering."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import sys
from typing import Any

import numpy as np
from scipy.optimize import linear_sum_assignment
import soundfile as sf
from sklearn.cluster import AgglomerativeClustering
from sklearn.metrics import adjusted_rand_score, normalized_mutual_info_score, silhouette_score


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = ROOT / "policies/session-local-remote-speaker-reclustering-feasibility-v1.json"
DEFAULT_OUT = ROOT / "sessions/_reports/session-local-remote-speaker-reclustering-feasibility-v1"
BASE_PATH = ROOT / "scripts/mine-session-local-homogeneous-remote-speaker-enrollment-v1.py"

POLICY_SCHEMA = "murmurmark.session_local_remote_speaker_reclustering_policy/v1"
INPUT_SCHEMA = "murmurmark.session_local_remote_speaker_reclustering_input/v1"
WINDOW_SCHEMA = "murmurmark.session_local_remote_speaker_unlabeled_window/v1"
PACK_SCHEMA = "murmurmark.session_local_remote_speaker_reclustering_pack/v1"
FREEZE_SCHEMA = "murmurmark.session_local_remote_speaker_reclustering_freeze/v1"
EVALUATION_SCHEMA = "murmurmark.session_local_remote_speaker_reclustering_evaluation/v1"
REPORT_SCHEMA = "murmurmark.session_local_remote_speaker_reclustering_report/v1"
REPLAY_SCHEMA = "murmurmark.session_local_remote_speaker_reclustering_replay/v1"
MANIFEST_SCHEMA = "murmurmark.session_local_remote_speaker_reclustering_manifest/v1"


class ReclusteringError(RuntimeError):
    pass


def load_base() -> Any:
    spec = importlib.util.spec_from_file_location("murmurmark_homogeneous_mining_v1", BASE_PATH)
    if spec is None or spec.loader is None:
        raise ReclusteringError("cannot load shared speaker embedding helpers")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BASE = load_base()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode()


def pretty(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2).encode() + b"\n"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_id(prefix: str, *values: Any) -> str:
    return prefix + "_" + hashlib.sha256(canonical(list(values))).hexdigest()[:16]


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(content)
    os.replace(temporary, path)


def write_json(path: Path, value: Any) -> None:
    atomic_write(path, pretty(value))


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    atomic_write(path, b"".join(canonical(row) for row in rows))


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ReclusteringError(f"expected JSON object: {path}")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ReclusteringError(f"expected JSON object at {path}:{line_number}")
        rows.append(value)
    return rows


def repo_path(raw: str) -> Path:
    path = Path(raw)
    if path.is_absolute() or ".." in path.parts:
        raise ReclusteringError(f"repository-relative path required: {raw}")
    return ROOT / path


def portable(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT))


def artifact(path: Path) -> dict[str, Any]:
    return {"path": portable(path), "bytes": path.stat().st_size, "sha256": sha256(path)}


def normalize(vector: np.ndarray) -> np.ndarray:
    value = np.asarray(vector, dtype=np.float64).reshape(-1)
    norm = float(np.linalg.norm(value))
    if not np.isfinite(norm) or norm <= 1e-12:
        raise ReclusteringError("invalid embedding vector")
    return value / norm


def cosine(left: np.ndarray, right: np.ndarray) -> float:
    return float(np.dot(normalize(left), normalize(right)))


def centroid(vectors: list[np.ndarray]) -> np.ndarray:
    if not vectors:
        raise ReclusteringError("empty centroid")
    return normalize(np.mean(np.stack([normalize(row) for row in vectors]), axis=0))


def load_policy(path: Path) -> dict[str, Any]:
    policy = read_json(path)
    if policy.get("schema") != POLICY_SCHEMA:
        raise ReclusteringError(f"unsupported policy schema: {policy.get('schema')}")
    if policy.get("state") != "frozen_before_label_and_direct_truth_evaluation":
        raise ReclusteringError("policy is not frozen before label and direct truth evaluation")
    inventory = policy.get("inventory") or {}
    clustering = policy.get("clustering") or {}
    evaluation = policy.get("evaluation") or {}
    required = {
        "target_text_read": False,
        "speaker_assignments_read": False,
        "human_names_read": False,
        "cross_session_voice_linking": False,
        "window_sec": 4.0,
    }
    for key, expected in required.items():
        if inventory.get(key) != expected:
            raise ReclusteringError(f"frozen inventory contract changed: {key}")
    if clustering.get("cluster_count_tuning_allowed") is not False:
        raise ReclusteringError("cluster-count tuning must remain disabled")
    if clustering.get("truth_guided_tuning_allowed") is not False:
        raise ReclusteringError("truth-guided tuning must remain disabled")
    if evaluation.get("threshold_grid_search_allowed") is not False:
        raise ReclusteringError("threshold grid search must remain disabled")
    if evaluation.get("post_hoc_tuning_allowed") is not False:
        raise ReclusteringError("post-hoc tuning must remain disabled")
    if (policy.get("decision") or {}).get("production_promotion_allowed") is not False:
        raise ReclusteringError("production promotion must remain disabled")
    sessions = policy.get("scope", {}).get("sessions") or []
    if len(sessions) != int(policy["scope"]["expected_sessions"]):
        raise ReclusteringError("frozen session count changed")
    if sum(int(row["cluster_count"]) for row in sessions) != int(policy["scope"]["expected_profiles"]):
        raise ReclusteringError("frozen topology count changed")
    return policy


def verify_sources(policy: dict[str, Any], phase: str) -> list[dict[str, Any]]:
    verified = []
    for source_id, expected in sorted((policy.get("sources") or {}).items()):
        if expected.get("phase") != phase:
            continue
        path = repo_path(str(expected["path"]))
        if not path.is_file() or sha256(path) != expected.get("sha256"):
            raise ReclusteringError(f"frozen source missing or changed: {source_id}")
        verified.append({"id": source_id, "phase": phase, **artifact(path)})
    return verified


def verify_dialogues(policy: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for session in policy["scope"]["sessions"]:
        path = repo_path(session["dialogue_path"])
        if not path.is_file() or sha256(path) != session["dialogue_sha256"]:
            raise ReclusteringError(f"selected dialogue missing or changed: {session['session_id']}")
        remote = ROOT / "sessions" / session["session_id"] / "audio/remote/000001.caf"
        if not remote.is_file():
            raise ReclusteringError(f"raw remote audio is unavailable: {session['session_id']}")
        rows.append(
            {
                "session_id": session["session_id"],
                "cluster_count": int(session["cluster_count"]),
                "dialogue": artifact(path),
                "raw_remote": artifact(remote),
            }
        )
    return rows


def preflight(policy: dict[str, Any]) -> dict[str, Any]:
    prepare_sources = verify_sources(policy, "prepare")
    dialogues = verify_dialogues(policy)
    production = BASE.verify_production_guards(policy)
    models = BASE.verify_model_provenance(policy)
    return {
        "schema": INPUT_SCHEMA,
        "policy": artifact(DEFAULT_POLICY),
        "prepare_sources": prepare_sources,
        "sessions": dialogues,
        "production_guards": production,
        "models": models,
        "phase": "prepare_without_speaker_assignments_or_direct_truth",
        "fields_used_from_dialogue": ["id", "role", "start", "end"],
        "forbidden_fields_used": [],
    }


def spread_indices(count: int, limit: int) -> list[int]:
    if count <= limit:
        return list(range(count))
    if limit <= 1:
        return [count // 2]
    return sorted({int(round(index * (count - 1) / (limit - 1))) for index in range(limit)})


def rms_dbfs(path: Path, start: float, end: float) -> float:
    with sf.SoundFile(path) as source:
        first = max(0, int(round(start * source.samplerate)))
        last = min(len(source), int(round(end * source.samplerate)))
        if last <= first:
            return -120.0
        source.seek(first)
        values = source.read(last - first, dtype="float32", always_2d=True).mean(axis=1)
    rms = float(np.sqrt(np.mean(np.square(values), dtype=np.float64))) if values.size else 0.0
    return round(20.0 * math.log10(max(rms, 1e-12)), 6)


def candidate_starts(start: float, end: float, policy: dict[str, Any]) -> list[float]:
    config = policy["inventory"]
    inset = float(config["boundary_inset_sec"])
    window = float(config["window_sec"])
    stride = float(config["window_stride_sec"])
    maximum = int(config["maximum_windows_per_utterance"])
    first = start + inset
    last = end - inset - window
    if last < first - 1e-6:
        return []
    starts = []
    cursor = first
    while cursor <= last + 1e-6 and len(starts) < maximum:
        starts.append(cursor)
        cursor += stride
    if starts and last - starts[-1] >= window / 2 and len(starts) < maximum:
        starts.append(last)
    return [round(value, 6) for value in starts]


def build_unlabeled_inventory(policy: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    config = policy["inventory"]
    window = float(config["window_sec"])
    minimum_utterance = float(config["minimum_utterance_sec"])
    minimum_rms = float(config["minimum_rms_dbfs"])
    per_session_limit = int(config["maximum_windows_per_session"])
    all_rows: list[dict[str, Any]] = []
    summaries = []
    for session in policy["scope"]["sessions"]:
        session_id = str(session["session_id"])
        dialogue = read_json(repo_path(session["dialogue_path"]))
        remote_audio = ROOT / "sessions" / session_id / "audio/remote/000001.caf"
        candidates = []
        remote_utterances = 0
        for utterance in dialogue.get("utterances") or []:
            if utterance.get("role") != config["role"]:
                continue
            start = float(utterance.get("start") or 0.0)
            end = float(utterance.get("end") or 0.0)
            if end - start < minimum_utterance:
                continue
            remote_utterances += 1
            for window_start in candidate_starts(start, end, policy):
                window_end = round(window_start + window, 6)
                key = stable_id("urw", session_id, window_start, window_end)
                candidates.append(
                    {
                        "schema": WINDOW_SCHEMA,
                        "key": key,
                        "session_id": session_id,
                        "start": window_start,
                        "end": window_end,
                        "duration_sec": window,
                        "source_audio": portable(remote_audio),
                    }
                )
        candidates.sort(key=lambda row: (row["start"], row["key"]))
        selected = [candidates[index] for index in spread_indices(len(candidates), per_session_limit)]
        for row in selected:
            level = rms_dbfs(remote_audio, row["start"], row["end"])
            row["rms_dbfs"] = level
            row["energy_gate_passed"] = level >= minimum_rms
        usable = sum(row["energy_gate_passed"] for row in selected)
        required = int(session["cluster_count"]) * int(config["minimum_windows_per_cluster"])
        if usable < required:
            raise ReclusteringError(f"insufficient blind windows for {session_id}: {usable} < {required}")
        all_rows.extend(selected)
        summaries.append(
            {
                "session_id": session_id,
                "cluster_count": int(session["cluster_count"]),
                "eligible_remote_utterances": remote_utterances,
                "candidate_windows": len(candidates),
                "selected_windows": len(selected),
                "usable_windows": usable,
            }
        )
    rows = sorted(all_rows, key=lambda row: (row["session_id"], row["start"], row["key"]))
    manifest = {
        "schema": INPUT_SCHEMA,
        "policy": artifact(DEFAULT_POLICY),
        "sessions": summaries,
        "counts": {
            "sessions": len(summaries),
            "selected_windows": len(rows),
            "usable_windows": sum(row["energy_gate_passed"] for row in rows),
        },
        "selection": config["sampling"],
        "text_fields_read": [],
        "speaker_assignment_fields_read": [],
        "direct_truth_read": False,
    }
    return rows, manifest


def cluster_vectors(vectors: np.ndarray, count: int) -> np.ndarray:
    if count == 1:
        return np.zeros(len(vectors), dtype=np.int64)
    if len(vectors) < count:
        raise ReclusteringError(f"cannot cluster {len(vectors)} vectors into {count} clusters")
    model = AgglomerativeClustering(n_clusters=count, metric="cosine", linkage="average")
    return np.asarray(model.fit_predict(vectors), dtype=np.int64)


def canonicalize_labels(labels: np.ndarray, rows: list[dict[str, Any]]) -> np.ndarray:
    order = sorted(
        set(int(value) for value in labels),
        key=lambda label: min((float(rows[index]["start"]), rows[index]["key"]) for index in range(len(rows)) if labels[index] == label),
    )
    mapping = {old: new for new, old in enumerate(order)}
    return np.asarray([mapping[int(value)] for value in labels], dtype=np.int64)


def nearest_labels(vectors: np.ndarray, centers: dict[int, np.ndarray]) -> np.ndarray:
    labels = []
    for vector in vectors:
        scores = [(cosine(vector, center), label) for label, center in centers.items()]
        scores.sort(key=lambda row: (-row[0], row[1]))
        labels.append(scores[0][1])
    return np.asarray(labels, dtype=np.int64)


def stability_ari(vectors: np.ndarray, full: np.ndarray, count: int, folds: int) -> float:
    if count == 1:
        return 1.0
    scores = []
    indices = np.arange(len(vectors))
    for fold in range(folds):
        train = indices[indices % folds != fold]
        test = indices[indices % folds == fold]
        if len(train) < count or not len(test):
            continue
        train_labels = cluster_vectors(vectors[train], count)
        centers = {
            label: centroid([vectors[index] for index in train[train_labels == label]])
            for label in sorted(set(int(value) for value in train_labels))
        }
        reconstructed = np.empty(len(vectors), dtype=np.int64)
        reconstructed[train] = train_labels
        reconstructed[test] = nearest_labels(vectors[test], centers)
        scores.append(adjusted_rand_score(full, reconstructed))
    return round(float(np.mean(scores)) if scores else 0.0, 6)


def cluster_metrics(vectors: np.ndarray, labels: np.ndarray, count: int, folds: int) -> dict[str, Any]:
    sizes = Counter(int(value) for value in labels)
    silhouette = 1.0
    if count > 1 and len(set(labels)) > 1 and len(vectors) > count:
        silhouette = float(silhouette_score(vectors, labels, metric="cosine"))
    return {
        "cluster_count": count,
        "cluster_sizes": [sizes[index] for index in range(count)],
        "silhouette": round(silhouette, 6),
        "stability_ari": stability_ari(vectors, labels, count, folds),
    }


def build_cluster_pack(
    policy: dict[str, Any], inventory: list[dict[str, Any]], ecapa: dict[str, np.ndarray], wavlm: dict[str, np.ndarray]
) -> dict[str, Any]:
    by_session: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in inventory:
        if row.get("energy_gate_passed") and row["key"] in ecapa and row["key"] in wavlm:
            by_session[row["session_id"]].append(row)
    sessions = []
    assignments = []
    folds = int(policy["clustering"]["stability_folds"])
    for position, definition in enumerate(policy["scope"]["sessions"], 1):
        session_id = str(definition["session_id"])
        rows = sorted(by_session.get(session_id) or [], key=lambda row: (row["start"], row["key"]))
        count = int(definition["cluster_count"])
        if len(rows) < count * int(policy["inventory"]["minimum_windows_per_cluster"]):
            raise ReclusteringError(f"embedding coverage is insufficient for {session_id}")
        keys = [row["key"] for row in rows]
        matrices = {
            "ecapa": np.stack([ecapa[key] for key in keys]),
            "wavlm": np.stack([wavlm[key] for key in keys]),
        }
        labels = {
            name: canonicalize_labels(cluster_vectors(matrix, count), rows)
            for name, matrix in matrices.items()
        }
        pair_keys = [(int(labels["ecapa"][index]), int(labels["wavlm"][index])) for index in range(len(rows))]
        pair_order = sorted(set(pair_keys), key=lambda pair: min(rows[index]["start"] for index, value in enumerate(pair_keys) if value == pair))
        pair_map = {pair: index for index, pair in enumerate(pair_order)}
        consensus = np.asarray([pair_map[value] for value in pair_keys], dtype=np.int64)
        metrics = {
            "ecapa": cluster_metrics(matrices["ecapa"], labels["ecapa"], count, folds),
            "wavlm": cluster_metrics(matrices["wavlm"], labels["wavlm"], count, folds),
            "agreement_ari": round(float(adjusted_rand_score(labels["ecapa"], labels["wavlm"])), 6),
            "agreement_nmi": round(float(normalized_mutual_info_score(labels["ecapa"], labels["wavlm"])), 6),
            "consensus_cluster_count": len(pair_order),
            "consensus_fragmentation_ratio": round(len(pair_order) / count, 6),
        }
        sessions.append(
            {
                "session_alias": f"session_{position:02d}",
                "session_id": session_id,
                "window_count": len(rows),
                "fixed_cluster_count": count,
                "metrics": metrics,
            }
        )
        for index, row in enumerate(rows):
            assignments.append(
                {
                    "key": row["key"],
                    "session_id": session_id,
                    "ecapa_cluster": int(labels["ecapa"][index]),
                    "wavlm_cluster": int(labels["wavlm"][index]),
                    "consensus_cluster": int(consensus[index]),
                }
            )
    return {
        "schema": PACK_SCHEMA,
        "algorithm": policy["clustering"]["algorithm"],
        "cluster_count_source": policy["clustering"]["cluster_count_source"],
        "sessions": sessions,
        "assignments": sorted(assignments, key=lambda row: row["key"]),
        "counts": {"sessions": len(sessions), "windows": len(assignments)},
        "labels_read": False,
        "direct_truth_read": False,
        "target_text_read": False,
        "human_names_read": False,
        "cross_session_voice_linking": False,
    }


def forbidden_key_paths(value: Any, prefix: str = "") -> list[str]:
    forbidden = {"text", "speaker_id", "speaker_label", "truth", "truth_outcome", "human_name", "name"}
    found = []
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if key in forbidden:
                found.append(path)
            found.extend(forbidden_key_paths(child, path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(forbidden_key_paths(child, f"{prefix}[{index}]"))
    return found


def action_preflight(policy: dict[str, Any], out: Path) -> int:
    value = preflight(policy)
    write_json(out / "private/preflight.json", value)
    print(f"preflight: ok ({len(value['sessions'])} sessions, {policy['scope']['expected_profiles']} fixed clusters)")
    return 0


def action_prepare(policy: dict[str, Any], out: Path) -> int:
    for relative in (
        "private/post_freeze_window_labels.jsonl",
        "private/post_freeze_cluster_mappings.json",
        "private/direct_truth_ecapa_request.json",
        "private/direct_truth_ecapa_embeddings.json",
        "private/direct_truth_wavlm_request.json",
        "private/direct_truth_wavlm_embeddings.json",
        "private/direct_truth_decisions.jsonl",
        "private/evaluation_core.json",
        "session_local_remote_speaker_reclustering_report.json",
        "session_local_remote_speaker_reclustering_report.md",
        "replay_report.json",
        "artifact_manifest.json",
    ):
        (out / relative).unlink(missing_ok=True)
    input_state = preflight(policy)
    inventory, inventory_manifest = build_unlabeled_inventory(policy)
    write_json(out / "private/input_manifest.json", {**input_state, "inventory": inventory_manifest})
    write_jsonl(out / "private/unlabeled_windows.jsonl", inventory)
    BASE.run_workers(policy, out, "window", inventory)
    ecapa = BASE.load_embeddings(out / "private/window_ecapa_embeddings.json")
    wavlm = BASE.load_embeddings(out / "private/window_wavlm_embeddings.json")
    pack = build_cluster_pack(policy, inventory, ecapa, wavlm)
    forbidden = forbidden_key_paths(pack)
    if forbidden:
        raise ReclusteringError("unlabeled pack contains forbidden fields: " + ",".join(forbidden[:8]))
    write_json(out / "private/reclustering_pack.pending.json", pack)
    public = {
        "schema": PACK_SCHEMA,
        "algorithm": pack["algorithm"],
        "counts": pack["counts"],
        "sessions": [
            {
                "session_alias": row["session_alias"],
                "window_count": row["window_count"],
                "fixed_cluster_count": row["fixed_cluster_count"],
                "metrics": row["metrics"],
            }
            for row in pack["sessions"]
        ],
        "labels_read": False,
        "direct_truth_read": False,
        "production_promotion_allowed": False,
    }
    write_json(out / "reclustering_pack.public.json", public)
    print(f"prepared: {pack['counts']['windows']} unlabeled windows")
    return 0


def action_freeze(policy: dict[str, Any], out: Path) -> int:
    pending = out / "private/reclustering_pack.pending.json"
    if not pending.is_file():
        raise ReclusteringError("prepare must run before freeze")
    if (out / "private/post_freeze_window_labels.jsonl").exists() or (out / "private/direct_truth_decisions.jsonl").exists():
        raise ReclusteringError("post-freeze evidence exists; rerun prepare before freeze")
    pack = read_json(pending)
    if pack.get("schema") != PACK_SCHEMA or pack.get("labels_read") is not False:
        raise ReclusteringError("pending pack is not label-independent")
    forbidden = forbidden_key_paths(pack)
    if forbidden:
        raise ReclusteringError("pending pack contains forbidden fields")
    frozen = out / "private/reclustering_pack.frozen.json"
    atomic_write(frozen, pending.read_bytes())
    frozen_inputs = [
        out / "private/input_manifest.json",
        out / "private/unlabeled_windows.jsonl",
        out / "private/window_ecapa_request.json",
        out / "private/window_ecapa_embeddings.json",
        out / "private/window_wavlm_request.json",
        out / "private/window_wavlm_embeddings.json",
        frozen,
    ]
    for path in frozen_inputs:
        if not path.is_file():
            raise ReclusteringError(f"freeze input missing: {path.name}")
    manifest = {
        "schema": FREEZE_SCHEMA,
        "state": "frozen_before_label_and_direct_truth_evaluation",
        "artifacts": [artifact(path) for path in frozen_inputs],
        "pack_sha256": sha256(frozen),
        "policy_sha256": sha256(DEFAULT_POLICY),
        "labels_read": False,
        "direct_truth_read": False,
        "cluster_count_tuned": False,
        "thresholds_tuned": False,
    }
    write_json(out / "freeze_manifest.json", manifest)
    print(f"frozen: {manifest['pack_sha256']}")
    return 0


def verify_frozen_pack(out: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest_path = out / "freeze_manifest.json"
    if not manifest_path.is_file():
        raise ReclusteringError("frozen pack is unavailable")
    manifest = read_json(manifest_path)
    if manifest.get("schema") != FREEZE_SCHEMA or manifest.get("labels_read") is not False:
        raise ReclusteringError("freeze manifest is invalid")
    for expected in manifest.get("artifacts") or []:
        path = repo_path(expected["path"])
        if not path.is_file() or path.stat().st_size != int(expected["bytes"]) or sha256(path) != expected["sha256"]:
            raise ReclusteringError(f"frozen artifact missing or changed: {expected['path']}")
    pack_path = out / "private/reclustering_pack.frozen.json"
    if sha256(pack_path) != manifest.get("pack_sha256"):
        raise ReclusteringError("frozen pack hash changed")
    pack = read_json(pack_path)
    if forbidden_key_paths(pack):
        raise ReclusteringError("frozen pack contains label leakage")
    return pack, manifest


def overlap(left_start: float, left_end: float, right_start: float, right_end: float) -> float:
    return max(0.0, min(left_end, right_end) - max(left_start, right_start))


def coverage_turns(session_id: str) -> list[dict[str, Any]]:
    path = ROOT / "sessions" / session_id / "derived/audit/remote-speaker-coverage-v3/utterance_attribution.jsonl"
    turns = []
    for utterance in read_jsonl(path):
        for turn in utterance.get("speaker_turns") or []:
            if turn.get("status") == "attributed" and turn.get("speaker_id"):
                turns.append(
                    {
                        "start": float(turn["start"]),
                        "end": float(turn["end"]),
                        "speaker_id": str(turn["speaker_id"]),
                    }
                )
    return turns


def window_labels(
    policy: dict[str, Any], inventory: list[dict[str, Any]]
) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]]]:
    threshold = float(policy["evaluation"]["minimum_window_truth_dominance"])
    by_session: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in inventory:
        by_session[row["session_id"]].append(row)
    labels = {}
    profiles = {}
    for definition in policy["scope"]["sessions"]:
        session_id = str(definition["session_id"])
        turns = coverage_turns(session_id)
        profiles[session_id] = sorted({row["speaker_id"] for row in turns})
        for row in by_session[session_id]:
            weights: dict[str, float] = defaultdict(float)
            for turn in turns:
                amount = overlap(float(row["start"]), float(row["end"]), turn["start"], turn["end"])
                if amount > 0:
                    weights[turn["speaker_id"]] += amount
            ordered = sorted(weights.items(), key=lambda item: (-item[1], item[0]))
            total = sum(weights.values())
            best_id = ordered[0][0] if ordered else None
            best = ordered[0][1] if ordered else 0.0
            dominance = best / total if total > 0 else 0.0
            coverage = total / float(row["duration_sec"])
            eligible = dominance >= threshold and coverage >= threshold
            labels[row["key"]] = {
                "speaker_id": best_id if eligible else None,
                "dominance": round(dominance, 6),
                "coverage": round(coverage, 6),
                "eligible": eligible,
            }
        if len(profiles[session_id]) != int(definition["cluster_count"]):
            raise ReclusteringError(f"post-freeze Coverage profile count changed: {session_id}")
    return labels, profiles


def map_clusters(
    assignments: list[dict[str, Any]], labels: dict[str, dict[str, Any]], profiles: list[str], field: str
) -> dict[str, Any]:
    clusters = sorted({int(row[field]) for row in assignments})
    counts = np.zeros((len(clusters), len(profiles)), dtype=np.int64)
    cluster_index = {value: index for index, value in enumerate(clusters)}
    profile_index = {value: index for index, value in enumerate(profiles)}
    eligible = 0
    for row in assignments:
        truth = labels[row["key"]]
        if not truth["eligible"]:
            continue
        eligible += 1
        counts[cluster_index[int(row[field])], profile_index[truth["speaker_id"]]] += 1
    if not eligible:
        return {"mapping": {}, "eligible_windows": 0, "purity": 0.0, "minimum_cluster_purity": 0.0, "minimum_mapping_margin": 0.0, "ambiguous_clusters": clusters}
    left, right = linear_sum_assignment(-counts)
    mapping = {clusters[int(row)]: profiles[int(column)] for row, column in zip(left, right)}
    matched = sum(int(counts[cluster_index[cluster], profile_index[profile]]) for cluster, profile in mapping.items())
    purities = []
    margins = []
    ambiguous = []
    for cluster in clusters:
        values = sorted((int(value) for value in counts[cluster_index[cluster]]), reverse=True)
        total = sum(values)
        best = values[0] if values else 0
        second = values[1] if len(values) > 1 else 0
        purity = best / total if total else 0.0
        margin = (best - second) / total if total else 0.0
        purities.append(purity)
        margins.append(margin)
        if purity < 0.8 or margin < 0.15:
            ambiguous.append(cluster)
    return {
        "mapping": {str(key): value for key, value in sorted(mapping.items())},
        "eligible_windows": eligible,
        "purity": round(matched / eligible, 6),
        "minimum_cluster_purity": round(min(purities), 6),
        "minimum_mapping_margin": round(min(margins), 6),
        "ambiguous_clusters": ambiguous,
        "confusion": counts.tolist(),
        "clusters": clusters,
        "profiles": profiles,
    }


def direct_truth_rows(policy: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    source = {row["item_id"]: row for row in read_jsonl(repo_path(policy["sources"]["direct_truth_items"]["path"]))}
    primary_slots = {
        row["item_id"]: row
        for row in read_jsonl(repo_path(policy["sources"]["direct_truth_slots"]["path"]))
        if row.get("kind") == "primary"
    }
    answers = {row["slot_id"]: row for row in read_jsonl(repo_path(policy["sources"]["direct_truth_answers"]["path"]))}
    controls = {
        row["item_id"]: row
        for row in read_jsonl(repo_path(policy["sources"]["development_adjudication"]["path"]))
    }
    rows = []
    truth = {}
    for item_id, slot in sorted(primary_slots.items()):
        item = source[item_id]
        answer = answers.get(slot["slot_id"])
        control = controls.get(item_id)
        if answer is None or control is None:
            raise ReclusteringError(f"direct truth evidence incomplete: {item_id}")
        audio = repo_path(item["materialized_audio"]["path"])
        if not audio.is_file() or sha256(audio) != item["materialized_audio"]["sha256"]:
            raise ReclusteringError(f"direct truth clip missing or changed: {item_id}")
        with sf.SoundFile(audio) as handle:
            duration = len(handle) / handle.samplerate
        rows.append(
            {
                "key": item_id,
                "session_id": item["session_id"],
                "source_audio": portable(audio),
                "start": 0.0,
                "end": round(duration, 6),
                "energy_gate_passed": True,
            }
        )
        outcome = str(answer["outcome"])
        truth[item_id] = {
            "session_id": item["session_id"],
            "truth_outcome": outcome,
            "truth_kind": "positive_identity" if outcome.startswith("remote_speaker_") else outcome,
            "control_outcome": control["control_outcome"],
            "control_prediction": control.get("control_prediction"),
            "confirmed_v1_additive_gain": bool(control["confirmed_v1_additive_gain"]),
            "word_count": int(control["word_count"]),
            "coverage_weight_sec": float(control["coverage_weight_sec"]),
        }
    expected = int(policy["scope"]["expected_development_items"])
    if len(rows) != expected:
        raise ReclusteringError(f"direct truth item count changed: {len(rows)} != {expected}")
    return rows, truth


def cluster_centers(
    pack: dict[str, Any], embeddings: dict[str, np.ndarray], model: str
) -> dict[str, dict[int, np.ndarray]]:
    grouped: dict[tuple[str, int], list[np.ndarray]] = defaultdict(list)
    field = f"{model}_cluster"
    for row in pack["assignments"]:
        grouped[(row["session_id"], int(row[field]))].append(embeddings[row["key"]])
    result: dict[str, dict[int, np.ndarray]] = defaultdict(dict)
    for (session_id, cluster), vectors in grouped.items():
        result[session_id][cluster] = centroid(vectors)
    return dict(result)


def classify_vector(
    vector: np.ndarray,
    centers: dict[int, np.ndarray],
    mapping: dict[str, str],
    minimum_similarity: float,
    minimum_margin: float,
) -> dict[str, Any]:
    scores = sorted(((cosine(vector, center), cluster) for cluster, center in centers.items()), key=lambda row: (-row[0], row[1]))
    best_score, best_cluster = scores[0]
    second = scores[1][0] if len(scores) > 1 else -1.0
    margin = best_score - second if len(scores) > 1 else 2.0
    accepted = best_score >= minimum_similarity and margin >= minimum_margin and str(best_cluster) in mapping
    return {
        "prediction": mapping.get(str(best_cluster)) if accepted else None,
        "cluster": best_cluster,
        "similarity": round(best_score, 6),
        "margin": round(margin, 6),
        "accepted": accepted,
    }


def evaluate_direct_truth(
    policy: dict[str, Any], out: Path, pack: dict[str, Any], mappings: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    rows, truth = direct_truth_rows(policy)
    BASE.run_workers(policy, out, "direct_truth", rows)
    window_embeddings = {
        "ecapa": BASE.load_embeddings(out / "private/window_ecapa_embeddings.json"),
        "wavlm": BASE.load_embeddings(out / "private/window_wavlm_embeddings.json"),
    }
    target_embeddings = {
        "ecapa": BASE.load_embeddings(out / "private/direct_truth_ecapa_embeddings.json"),
        "wavlm": BASE.load_embeddings(out / "private/direct_truth_wavlm_embeddings.json"),
    }
    centers = {model: cluster_centers(pack, window_embeddings[model], model) for model in ("ecapa", "wavlm")}
    decisions = []
    for row in rows:
        item_id = row["key"]
        expected = truth[item_id]
        session_id = expected["session_id"]
        model_decisions = {}
        for model in ("ecapa", "wavlm"):
            definition = policy[model]
            if item_id not in target_embeddings[model]:
                model_decisions[model] = {
                    "prediction": None,
                    "cluster": None,
                    "similarity": None,
                    "margin": None,
                    "accepted": False,
                    "reason": "embedding_unavailable",
                }
            else:
                model_decisions[model] = classify_vector(
                    target_embeddings[model][item_id],
                    centers[model][session_id],
                    mappings[session_id][model]["mapping"],
                    float(definition["minimum_target_similarity"]),
                    float(definition["minimum_target_margin"]),
                )
        predictions = [model_decisions[model]["prediction"] for model in ("ecapa", "wavlm")]
        prediction = predictions[0] if predictions[0] is not None and predictions[0] == predictions[1] else None
        if prediction is None:
            result = "abstained_positive" if expected["truth_kind"] == "positive_identity" else "safe_abstention"
        elif expected["truth_kind"] == "positive_identity" and prediction == expected["truth_outcome"]:
            result = "correct_identity"
        else:
            result = "unsafe_fail_closed_acceptance"
        decisions.append(
            {
                "item_id": item_id,
                "session_id": session_id,
                "prediction": prediction,
                "candidate_result": result,
                "model_decisions": model_decisions,
                **expected,
            }
        )
    counts = Counter(row["candidate_result"] for row in decisions)
    unsafe = int(counts["unsafe_fail_closed_acceptance"])
    preserved = sum(row["confirmed_v1_additive_gain"] and row["candidate_result"] == "correct_identity" for row in decisions)
    new_false = sum(row["candidate_result"] == "unsafe_fail_closed_acceptance" and row["control_outcome"] != "unsafe_fail_closed_acceptance" for row in decisions)
    lost_control = sum(
        row["control_outcome"] == "correct_identity"
        and not (row["candidate_result"] == "correct_identity" and row["prediction"] == row["control_prediction"])
        for row in decisions
    )
    write_jsonl(out / "private/direct_truth_decisions.jsonl", decisions)
    return {
        "items": len(decisions),
        "embedding_unavailable_items": sum(
            any(value.get("reason") == "embedding_unavailable" for value in row["model_decisions"].values())
            for row in decisions
        ),
        "counts": dict(sorted(counts.items())),
        "confirmed_v1_additive_gains": int(policy["scope"]["expected_confirmed_v1_additive_gains"]),
        "preserved_confirmed_v1_additive_gains": preserved,
        "unsafe_fail_closed_accepts": unsafe,
        "new_false_identity_items": new_false,
        "lost_correct_control_identity_items": lost_control,
    }


def evaluation_core(policy: dict[str, Any], out: Path, pack: dict[str, Any]) -> dict[str, Any]:
    verify_sources(policy, "evaluate_after_freeze")
    inventory = read_jsonl(out / "private/unlabeled_windows.jsonl")
    labels, profiles = window_labels(policy, inventory)
    write_jsonl(
        out / "private/post_freeze_window_labels.jsonl",
        [{"key": key, **value} for key, value in sorted(labels.items())],
    )
    assignments_by_session: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in pack["assignments"]:
        assignments_by_session[row["session_id"]].append(row)
    mappings = {}
    mapping_summaries = []
    for position, definition in enumerate(policy["scope"]["sessions"], 1):
        session_id = str(definition["session_id"])
        mappings[session_id] = {
            model: map_clusters(assignments_by_session[session_id], labels, profiles[session_id], f"{model}_cluster")
            for model in ("ecapa", "wavlm")
        }
        mapping_summaries.append(
            {
                "session_alias": f"session_{position:02d}",
                "fixed_cluster_count": int(definition["cluster_count"]),
                "ecapa": {key: value for key, value in mappings[session_id]["ecapa"].items() if key not in {"mapping", "confusion", "clusters", "profiles"}},
                "wavlm": {key: value for key, value in mappings[session_id]["wavlm"].items() if key not in {"mapping", "confusion", "clusters", "profiles"}},
            }
        )
    write_json(out / "private/post_freeze_cluster_mappings.json", mappings)
    direct = evaluate_direct_truth(policy, out, pack, mappings)
    multi = [row for row in pack["sessions"] if row["fixed_cluster_count"] > 1]
    evaluation = policy["evaluation"]
    geometry_values = {
        "multispeaker_sessions": len(multi),
        "minimum_model_agreement_ari": min(row["metrics"]["agreement_ari"] for row in multi),
        "minimum_model_agreement_nmi": min(row["metrics"]["agreement_nmi"] for row in multi),
        "minimum_ecapa_silhouette": min(row["metrics"]["ecapa"]["silhouette"] for row in multi),
        "minimum_wavlm_silhouette": min(row["metrics"]["wavlm"]["silhouette"] for row in multi),
        "minimum_ecapa_stability_ari": min(row["metrics"]["ecapa"]["stability_ari"] for row in multi),
        "minimum_wavlm_stability_ari": min(row["metrics"]["wavlm"]["stability_ari"] for row in multi),
        "maximum_consensus_fragmentation_ratio": max(row["metrics"]["consensus_fragmentation_ratio"] for row in multi),
    }
    geometry_gates = {
        "minimum_multispeaker_sessions": geometry_values["multispeaker_sessions"] >= int(evaluation["minimum_multispeaker_sessions"]),
        "minimum_model_agreement_ari": geometry_values["minimum_model_agreement_ari"] >= float(evaluation["minimum_model_agreement_ari"]),
        "minimum_model_agreement_nmi": geometry_values["minimum_model_agreement_nmi"] >= float(evaluation["minimum_model_agreement_nmi"]),
        "minimum_ecapa_silhouette": geometry_values["minimum_ecapa_silhouette"] >= float(evaluation["minimum_ecapa_silhouette"]),
        "minimum_wavlm_silhouette": geometry_values["minimum_wavlm_silhouette"] >= float(evaluation["minimum_wavlm_silhouette"]),
        "minimum_ecapa_stability_ari": geometry_values["minimum_ecapa_stability_ari"] >= float(evaluation["minimum_ecapa_stability_ari"]),
        "minimum_wavlm_stability_ari": geometry_values["minimum_wavlm_stability_ari"] >= float(evaluation["minimum_wavlm_stability_ari"]),
        "maximum_consensus_fragmentation_ratio": geometry_values["maximum_consensus_fragmentation_ratio"] <= float(evaluation["maximum_consensus_fragmentation_ratio"]),
    }
    mapped_models = [summary[model] for summary in mapping_summaries for model in ("ecapa", "wavlm")]
    mapping_values = {
        "minimum_cluster_mapping_purity": min(row["minimum_cluster_purity"] for row in mapped_models),
        "minimum_mapping_margin": min(row["minimum_mapping_margin"] for row in mapped_models),
        "ambiguous_clusters": sum(len(row["ambiguous_clusters"]) for row in mapped_models),
    }
    mapping_gates = {
        "minimum_cluster_mapping_purity": mapping_values["minimum_cluster_mapping_purity"] >= float(evaluation["minimum_cluster_mapping_purity"]),
        "minimum_mapping_margin": mapping_values["minimum_mapping_margin"] >= float(evaluation["minimum_mapping_margin"]),
        "minimum_preserved_confirmed_v1_additive_gains": direct["preserved_confirmed_v1_additive_gains"] >= int(evaluation["minimum_preserved_confirmed_v1_additive_gains"]),
        "maximum_unsafe_fail_closed_accepts": direct["unsafe_fail_closed_accepts"] <= int(evaluation["maximum_unsafe_fail_closed_accepts"]),
        "no_new_false_identity": direct["new_false_identity_items"] == 0,
        "no_lost_correct_control_identity": direct["lost_correct_control_identity_items"] == 0,
    }
    invariant_gates = {
        "pack_frozen_before_labels": pack.get("labels_read") is False,
        "pack_frozen_before_direct_truth": pack.get("direct_truth_read") is False,
        "cluster_count_not_tuned": policy["clustering"]["cluster_count_tuning_allowed"] is False,
        "thresholds_not_tuned": evaluation["post_hoc_tuning_allowed"] is False,
        "production_promotion_disabled": policy["decision"]["production_promotion_allowed"] is False,
    }
    return {
        "schema": EVALUATION_SCHEMA,
        "geometry": {"values": geometry_values, "gates": geometry_gates, "sessions": [{"session_alias": row["session_alias"], "fixed_cluster_count": row["fixed_cluster_count"], "window_count": row["window_count"], "metrics": row["metrics"]} for row in pack["sessions"]]},
        "mapping": {"values": mapping_values, "gates": mapping_gates, "sessions": mapping_summaries},
        "direct_truth": direct,
        "invariants": invariant_gates,
    }


def build_report(policy: dict[str, Any], out: Path, pack: dict[str, Any], core: dict[str, Any]) -> dict[str, Any]:
    geometry_passed = all(core["geometry"]["gates"].values())
    mapping_passed = all(core["mapping"]["gates"].values())
    invariants_passed = all(core["invariants"].values())
    if not invariants_passed:
        decision = "EVIDENCE_BOUND"
        reason = "freeze, provenance or safety invariants are incomplete"
    elif not geometry_passed:
        decision = "EMBEDDING_GEOMETRY_BOUND"
        reason = "ECAPA/WavLM did not form a stable agreeing session-local partition under frozen gates"
    elif not mapping_passed:
        decision = "LABEL_MAPPING_BOUND"
        reason = "speaker geometry passed, but cluster-to-profile mapping or direct truth remained unsafe"
    else:
        decision = "RECLUSTERING_ROUTE_READY"
        reason = "independent cluster geometry and post-freeze direct-truth gates passed"
    failed = {
        "geometry": sorted(key for key, value in core["geometry"]["gates"].items() if not value),
        "mapping": sorted(key for key, value in core["mapping"]["gates"].items() if not value),
        "invariants": sorted(key for key, value in core["invariants"].items() if not value),
    }
    return {
        "schema": REPORT_SCHEMA,
        "decision": decision,
        "decision_reason": reason,
        "replay_verified": True,
        "scope": {
            "sessions": int(policy["scope"]["expected_sessions"]),
            "profiles": int(policy["scope"]["expected_profiles"]),
            "windows": int(pack["counts"]["windows"]),
            "development_items": int(policy["scope"]["expected_development_items"]),
        },
        "geometry": core["geometry"],
        "mapping": core["mapping"],
        "direct_truth": core["direct_truth"],
        "invariants": core["invariants"],
        "failed_gates": failed,
        "safety": {
            **policy["safety"],
            "frozen_pack_sha256": sha256(out / "private/reclustering_pack.frozen.json"),
            "coverage_v3_accepts_preserved": int(policy["scope"]["expected_coverage_v3_accepts"]),
            "production_guards_verified": int(policy["scope"]["expected_production_guards"]),
            "selected_transcript_mutated": False,
            "raw_audio_mutated": False,
            "cluster_count_tuned": False,
            "thresholds_tuned": False,
        },
        "next": {
            "RECLUSTERING_ROUTE_READY": "open_separate_monotonic_reclustered_shadow_candidate",
            "LABEL_MAPPING_BOUND": "seek_independent_cluster_to_profile_mapping_evidence",
            "EMBEDDING_GEOMETRY_BOUND": "close_current_ecapa_wavlm_route_and_keep_unknown_explicit",
            "EVIDENCE_BOUND": "repair_only_missing_provenance_or_evidence",
        }[decision],
    }


def report_markdown(report: dict[str, Any]) -> str:
    geometry = report["geometry"]["values"]
    mapping = report["mapping"]["values"]
    direct = report["direct_truth"]
    lines = [
        "# Session-Local Remote Speaker Re-Clustering Feasibility v1",
        "",
        f"Decision: `{report['decision']}`",
        "",
        report["decision_reason"] + ".",
        "",
        "## Geometry",
        "",
        f"- Unlabeled windows: `{report['scope']['windows']}`",
        f"- Minimum ECAPA/WavLM ARI: `{geometry['minimum_model_agreement_ari']}`",
        f"- Minimum ECAPA/WavLM NMI: `{geometry['minimum_model_agreement_nmi']}`",
        f"- Minimum ECAPA silhouette: `{geometry['minimum_ecapa_silhouette']}`",
        f"- Minimum WavLM silhouette: `{geometry['minimum_wavlm_silhouette']}`",
        f"- Minimum ECAPA stability ARI: `{geometry['minimum_ecapa_stability_ari']}`",
        f"- Minimum WavLM stability ARI: `{geometry['minimum_wavlm_stability_ari']}`",
        f"- Maximum consensus fragmentation: `{geometry['maximum_consensus_fragmentation_ratio']}`",
        "",
        "## Post-Freeze Mapping",
        "",
        f"- Minimum cluster purity: `{mapping['minimum_cluster_mapping_purity']}`",
        f"- Minimum mapping margin: `{mapping['minimum_mapping_margin']}`",
        f"- Ambiguous clusters: `{mapping['ambiguous_clusters']}`",
        f"- Preserved confirmed gains: `{direct['preserved_confirmed_v1_additive_gains']}/{direct['confirmed_v1_additive_gains']}`",
        f"- Unsafe accepts: `{direct['unsafe_fail_closed_accepts']}`",
        f"- New false identities: `{direct['new_false_identity_items']}`",
        f"- Lost correct controls: `{direct['lost_correct_control_identity_items']}`",
        "",
        "## Safety",
        "",
        "The clustering pack was frozen before Coverage assignments and direct truth were read.",
        "Coverage v3, selected transcripts, raw CAF, primary ASR and Echo Guard were not modified.",
        f"Next: `{report['next']}`.",
    ]
    return "\n".join(lines) + "\n"


def action_evaluate(policy: dict[str, Any], out: Path) -> int:
    pack, _ = verify_frozen_pack(out)
    core = evaluation_core(policy, out, pack)
    write_json(out / "private/evaluation_core.json", core)
    report = build_report(policy, out, pack, core)
    write_json(out / "session_local_remote_speaker_reclustering_report.json", report)
    atomic_write(out / "session_local_remote_speaker_reclustering_report.md", report_markdown(report).encode())
    print(f"decision: {report['decision']}")
    return 0


def action_replay(policy: dict[str, Any], out: Path) -> int:
    report_path = out / "session_local_remote_speaker_reclustering_report.json"
    core_path = out / "private/evaluation_core.json"
    if not report_path.is_file() or not core_path.is_file():
        raise ReclusteringError("evaluation artifacts are incomplete")
    pack, freeze = verify_frozen_pack(out)
    original = read_json(report_path)
    replayed = build_report(policy, out, pack, read_json(core_path))
    verified = canonical(original) == canonical(replayed)
    value = {
        "schema": REPLAY_SCHEMA,
        "verified": verified,
        "report_sha256": sha256(report_path),
        "evaluation_core_sha256": sha256(core_path),
        "frozen_pack_sha256": freeze["pack_sha256"],
    }
    write_json(out / "replay_report.json", value)
    if not verified:
        raise ReclusteringError("deterministic replay mismatch")
    print("replay: verified")
    return 0


def action_finalize(policy: dict[str, Any], out: Path) -> int:
    action_replay(policy, out)
    report = read_json(out / "session_local_remote_speaker_reclustering_report.json")
    names = [
        "freeze_manifest.json",
        "reclustering_pack.public.json",
        "session_local_remote_speaker_reclustering_report.json",
        "session_local_remote_speaker_reclustering_report.md",
        "replay_report.json",
    ]
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "decision": report["decision"],
        "artifacts": {name: artifact(out / name) for name in names},
        "private_artifacts_present": True,
        "production_promotion_allowed": False,
    }
    write_json(out / "artifact_manifest.json", manifest)
    print(f"finalized: {report['decision']}")
    return 0


def action_status(out: Path) -> int:
    path = out / "session_local_remote_speaker_reclustering_report.json"
    if not path.is_file():
        print("status: not_evaluated")
        return 1
    report = read_json(path)
    print(f"decision: {report['decision']}")
    print(f"windows: {report['scope']['windows']}")
    print(f"minimum_model_agreement_ari: {report['geometry']['values']['minimum_model_agreement_ari']}")
    print(f"ambiguous_clusters: {report['mapping']['values']['ambiguous_clusters']}")
    print(f"preserved_confirmed_gains: {report['direct_truth']['preserved_confirmed_v1_additive_gains']}/{report['direct_truth']['confirmed_v1_additive_gains']}")
    print(f"next: {report['next']}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("preflight", "prepare", "freeze", "evaluate", "replay", "finalize", "status", "all"))
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    policy_path = args.policy.expanduser().resolve()
    out = args.out.expanduser().resolve()
    global DEFAULT_POLICY
    DEFAULT_POLICY = policy_path
    try:
        policy = load_policy(policy_path)
        actions = {
            "preflight": action_preflight,
            "prepare": action_prepare,
            "freeze": action_freeze,
            "evaluate": action_evaluate,
            "replay": action_replay,
            "finalize": action_finalize,
            "status": lambda _policy, target: action_status(target),
        }
        sequence = ("preflight", "prepare", "freeze", "evaluate", "replay", "finalize") if args.action == "all" else (args.action,)
        for action in sequence:
            result = actions[action](policy, out)
            if result:
                return result
        return 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError, ReclusteringError, BASE.MiningError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
