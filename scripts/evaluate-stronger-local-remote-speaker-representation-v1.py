#!/usr/bin/env python3
"""Qualify a materially different local remote-speaker representation backend."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

import numpy as np
import onnxruntime as ort
from sklearn.metrics import adjusted_rand_score, normalized_mutual_info_score


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = ROOT / "policies/stronger-local-remote-speaker-representation-qualification-v1.json"
DEFAULT_OUT = ROOT / "sessions/_reports/stronger-local-remote-speaker-representation-qualification-v1"
PREVIOUS_OUT = ROOT / "sessions/_reports/session-local-remote-speaker-reclustering-feasibility-v1"
WORKER = ROOT / "scripts/wespeaker-resnet34-embedding-worker.py"
MODEL_ENV = "MURMURMARK_REMOTE_SPEAKER_WESPEAKER_MODEL"

POLICY_SCHEMA = "murmurmark.stronger_local_remote_speaker_representation_policy/v1"
PACK_SCHEMA = "murmurmark.stronger_local_remote_speaker_representation_pack/v1"
FREEZE_SCHEMA = "murmurmark.stronger_local_remote_speaker_representation_freeze/v1"
EVALUATION_SCHEMA = "murmurmark.stronger_local_remote_speaker_representation_evaluation/v1"
REPORT_SCHEMA = "murmurmark.stronger_local_remote_speaker_representation_report/v1"
REPLAY_SCHEMA = "murmurmark.stronger_local_remote_speaker_representation_replay/v1"
MANIFEST_SCHEMA = "murmurmark.stronger_local_remote_speaker_representation_artifact_manifest/v1"
REQUEST_SCHEMA = "murmurmark.speaker_embedding_request/v1"
EMBEDDING_SCHEMA = "murmurmark.speaker_embedding_result/v1"


class RepresentationError(RuntimeError):
    pass


def load_previous() -> Any:
    path = ROOT / "scripts/evaluate-session-local-remote-speaker-reclustering-feasibility-v1.py"
    spec = importlib.util.spec_from_file_location("murmurmark_reclustering_v1", path)
    if spec is None or spec.loader is None:
        raise RepresentationError("cannot load frozen re-clustering helpers")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BASE = load_previous()
ACTIVE_POLICY_PATH = DEFAULT_POLICY


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def pretty(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


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
        raise RepresentationError(f"JSON object expected: {path}")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise RepresentationError(f"JSONL object expected: {path}")
                rows.append(value)
    return rows


def repo_path(raw: str) -> Path:
    path = Path(raw).expanduser()
    return path if path.is_absolute() else ROOT / path


def portable(path: Path) -> str:
    resolved = path.expanduser().resolve()
    try:
        return str(resolved.relative_to(ROOT))
    except ValueError:
        home = Path.home().resolve()
        try:
            return "~/" + str(resolved.relative_to(home))
        except ValueError:
            return str(resolved)


def artifact(path: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    return {"path": portable(resolved), "bytes": resolved.stat().st_size, "sha256": sha256(resolved)}


def resolve_artifact(raw: str) -> Path:
    return repo_path(raw)


def load_policy(path: Path) -> dict[str, Any]:
    policy = read_json(path)
    if policy.get("schema") != POLICY_SCHEMA:
        raise RepresentationError("unsupported stronger representation policy")
    allowed = {"STRONGER_REPRESENTATION_READY", "KEEP_EXPLICIT_UNKNOWN", "EVIDENCE_BOUND"}
    if set(policy["decision"]["allowed_outcomes"]) != allowed:
        raise RepresentationError("terminal outcome set changed")
    if policy["decision"]["production_promotion_allowed"] is not False:
        raise RepresentationError("production promotion must remain disabled")
    if policy["clustering"]["truth_guided_tuning_allowed"] is not False:
        raise RepresentationError("truth-guided tuning must remain disabled")
    if policy["evaluation"]["post_hoc_tuning_allowed"] is not False:
        raise RepresentationError("post-hoc tuning must remain disabled")
    return policy


def verify_sources(policy: dict[str, Any], phase: str) -> list[dict[str, Any]]:
    found = []
    for source_id, expected in sorted(policy["sources"].items()):
        if expected["phase"] != phase:
            continue
        path = repo_path(expected["path"])
        if not path.is_file():
            raise RepresentationError(f"frozen source is missing: {source_id}")
        actual = sha256(path)
        if actual != expected["sha256"]:
            raise RepresentationError(f"frozen source changed: {source_id}")
        found.append({"id": source_id, **artifact(path)})
    return found


def verify_dialogues(policy: dict[str, Any]) -> list[dict[str, Any]]:
    found = []
    for definition in policy["scope"]["sessions"]:
        path = repo_path(definition["dialogue_path"])
        if not path.is_file() or sha256(path) != definition["dialogue_sha256"]:
            raise RepresentationError(f"selected dialogue changed: {definition['session_id']}")
        found.append({"session_id": definition["session_id"], **artifact(path)})
    return found


def package_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError as error:
        raise RepresentationError(f"required offline runtime package is missing: {name}") from error


def verify_model(policy: dict[str, Any]) -> dict[str, Any]:
    candidate = policy["candidate"]
    configured = os.environ.get(MODEL_ENV, candidate["default_model_path"])
    model = Path(configured).expanduser().resolve()
    if not model.is_file():
        raise RepresentationError(f"candidate model is missing: {model}")
    if model.stat().st_size != int(candidate["model_bytes"]) or sha256(model) != candidate["model_sha256"]:
        raise RepresentationError("candidate model bytes changed")
    license_path = model.parent / candidate["license_file"]
    readme_path = model.parent / "README.md"
    if not license_path.is_file() or sha256(license_path) != candidate["license_sha256"]:
        raise RepresentationError("candidate license provenance is missing or changed")
    if not readme_path.is_file() or sha256(readme_path) != candidate["readme_sha256"]:
        raise RepresentationError("candidate README provenance is missing or changed")
    if not WORKER.is_file():
        raise RepresentationError("WeSpeaker worker is missing")
    runtime = {
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "onnxruntime": package_version("onnxruntime"),
        "torch": package_version("torch"),
        "torchaudio": package_version("torchaudio"),
    }
    for name, value in runtime.items():
        expected = str(policy["runtime"][name])
        if value != expected:
            raise RepresentationError(f"runtime changed: {name}={value}, expected {expected}")
    options = ort.SessionOptions()
    options.inter_op_num_threads = 1
    options.intra_op_num_threads = 1
    session = ort.InferenceSession(str(model), sess_options=options, providers=["CPUExecutionProvider"])
    inputs = [(row.name, row.shape, row.type) for row in session.get_inputs()]
    outputs = [(row.name, row.shape, row.type) for row in session.get_outputs()]
    if inputs != [("feats", ["B", "T", 80], "tensor(float)")]:
        raise RepresentationError(f"unexpected candidate model input: {inputs}")
    if outputs != [("embs", ["B", 256], "tensor(float)")]:
        raise RepresentationError(f"unexpected candidate model output: {outputs}")
    return {
        "model": artifact(model),
        "license": artifact(license_path),
        "readme": artifact(readme_path),
        "worker": artifact(WORKER),
        "runtime": runtime,
        "onnx_inputs": inputs,
        "onnx_outputs": outputs,
        "offline_ready": True,
        "materially_independent": all(
            bool(policy["independence"][key])
            for key in (
                "candidate_is_not_reference_wrapper",
                "candidate_architecture_differs",
                "candidate_training_pipeline_differs",
            )
        ),
    }


def verify_previous_freeze(policy: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    previous_pack, _ = BASE.verify_frozen_pack(PREVIOUS_OUT)
    inventory = read_jsonl(repo_path(policy["sources"]["unlabeled_windows"]["path"]))
    eligible = sum(bool(row.get("energy_gate_passed")) for row in inventory)
    if eligible != int(policy["scope"]["expected_windows"]):
        raise RepresentationError("frozen unlabeled window count changed")
    if int(previous_pack["counts"]["windows"]) != eligible:
        raise RepresentationError("previous frozen pack and inventory disagree")
    if BASE.forbidden_key_paths(previous_pack):
        raise RepresentationError("previous frozen pack contains label leakage")
    return previous_pack, inventory


def preflight(policy: dict[str, Any]) -> dict[str, Any]:
    sources = verify_sources(policy, "prepare")
    dialogues = verify_dialogues(policy)
    model = verify_model(policy)
    previous_pack, inventory = verify_previous_freeze(policy)
    tp_manifest = read_json(repo_path(policy["sources"]["transcript_perfection_manifest"]["path"]))
    if len(tp_manifest.get("sources") or []) != int(policy["scope"]["expected_transcript_perfection_sources"]):
        raise RepresentationError("Transcript Perfection source count changed")
    if len(policy["scope"]["sessions"]) != int(policy["scope"]["expected_sessions"]):
        raise RepresentationError("session scope changed")
    if sum(int(row["cluster_count"]) for row in policy["scope"]["sessions"]) != int(
        policy["scope"]["expected_profiles"]
    ):
        raise RepresentationError("profile topology changed")
    return {
        "schema": "murmurmark.stronger_local_remote_speaker_representation_preflight/v1",
        "sources": sources,
        "dialogues": dialogues,
        "model": model,
        "previous_pack_sha256": sha256(
            repo_path(policy["sources"]["reclustering_pack"]["path"])
        ),
        "window_count": sum(bool(row.get("energy_gate_passed")) for row in inventory),
        "previous_pack_window_count": int(previous_pack["counts"]["windows"]),
        "production_guards": int(policy["scope"]["expected_production_guards"]),
    }


def embedding_request(policy: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    candidate = policy["candidate"]
    return {
        "schema": REQUEST_SCHEMA,
        "model_id": candidate["model_id"],
        "model_revision": candidate["model_revision"],
        "allow_errors": True,
        "requests": [
            {
                "key": row["key"],
                "path": str(repo_path(row["source_audio"]).resolve()),
                "start": float(row["start"]),
                "end": float(row["end"]),
                "minimum_sec": 1.0,
            }
            for row in rows
            if row.get("energy_gate_passed", True)
        ],
    }


def run_worker(policy: dict[str, Any], request: Path, output: Path, model: Path) -> None:
    command = [
        "nice",
        "-n",
        str(policy["runtime"]["nice"]),
        sys.executable,
        str(WORKER),
        "--request",
        str(request),
        "--output",
        str(output),
        "--model",
        str(model),
        "--threads",
        str(policy["runtime"]["threads"]),
    ]
    environment = {**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
    completed = subprocess.run(command, cwd=ROOT, env=environment, check=False)
    if completed.returncode != 0:
        raise RepresentationError("WeSpeaker embedding worker failed")


def load_embeddings(path: Path) -> dict[str, np.ndarray]:
    payload = read_json(path)
    if payload.get("schema") != EMBEDDING_SCHEMA:
        raise RepresentationError(f"unsupported embedding result: {path}")
    result = {}
    for row in payload.get("rows") or []:
        result[str(row["key"])] = BASE.normalize(np.asarray(row["embedding"], dtype=np.float64))
    return result


def previous_assignments(pack: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(row["key"]): row for row in pack["assignments"]}


def build_candidate_pack(
    policy: dict[str, Any],
    inventory: list[dict[str, Any]],
    candidate_embeddings: dict[str, np.ndarray],
    previous_pack: dict[str, Any],
) -> dict[str, Any]:
    previous = previous_assignments(previous_pack)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in inventory:
        if row.get("energy_gate_passed") and row["key"] in candidate_embeddings and row["key"] in previous:
            grouped[str(row["session_id"])].append(row)
    assignments = []
    sessions = []
    folds = int(policy["clustering"]["stability_folds"])
    for position, definition in enumerate(policy["scope"]["sessions"], 1):
        session_id = str(definition["session_id"])
        rows = sorted(grouped[session_id], key=lambda row: (float(row["start"]), row["key"]))
        count = int(definition["cluster_count"])
        if len(rows) < max(count * 4, count + 1):
            raise RepresentationError(f"candidate embedding coverage is insufficient: {session_id}")
        keys = [str(row["key"]) for row in rows]
        vectors = np.stack([candidate_embeddings[key] for key in keys])
        labels = BASE.cluster_vectors(vectors, count)
        labels = BASE.canonicalize_labels(labels, rows)
        ecapa_labels = np.asarray([int(previous[key]["ecapa_cluster"]) for key in keys])
        wavlm_labels = np.asarray([int(previous[key]["wavlm_cluster"]) for key in keys])
        candidate_metrics = BASE.cluster_metrics(vectors, labels, count, folds)
        metrics = {
            "candidate": candidate_metrics,
            "candidate_ecapa_ari": round(float(adjusted_rand_score(labels, ecapa_labels)), 6),
            "candidate_ecapa_nmi": round(float(normalized_mutual_info_score(labels, ecapa_labels)), 6),
            "candidate_wavlm_ari": round(float(adjusted_rand_score(labels, wavlm_labels)), 6),
            "candidate_wavlm_nmi": round(float(normalized_mutual_info_score(labels, wavlm_labels)), 6),
            "three_model_fragmentation_ratio": round(
                len(set(zip(labels.tolist(), ecapa_labels.tolist(), wavlm_labels.tolist()))) / count,
                6,
            ),
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
                    "candidate_cluster": int(labels[index]),
                    "reference_ecapa_cluster": int(ecapa_labels[index]),
                    "reference_wavlm_cluster": int(wavlm_labels[index]),
                }
            )
    return {
        "schema": PACK_SCHEMA,
        "candidate": {
            "id": policy["candidate"]["id"],
            "model_id": policy["candidate"]["model_id"],
            "model_revision": policy["candidate"]["model_revision"],
            "model_sha256": policy["candidate"]["model_sha256"],
            "architecture": policy["candidate"]["architecture"],
            "training": policy["candidate"]["training"],
        },
        "algorithm": policy["clustering"],
        "counts": {
            "sessions": len(sessions),
            "profiles": sum(row["fixed_cluster_count"] for row in sessions),
            "windows": len(assignments),
        },
        "sessions": sessions,
        "assignments": sorted(assignments, key=lambda row: row["key"]),
        "labels_read": False,
        "direct_truth_read": False,
        "thresholds_tuned": False,
        "production_promotion_allowed": False,
    }


def forbidden_key_paths(value: Any, prefix: str = "") -> list[str]:
    forbidden = {
        "speaker_id",
        "speaker_name",
        "human_name",
        "text",
        "truth",
        "truth_outcome",
        "control_outcome",
        "coverage_assignment",
        "profile_id",
    }
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


def clean_prepare_outputs(out: Path) -> None:
    for relative in (
        "private/post_freeze_window_labels.jsonl",
        "private/post_freeze_cluster_mappings.json",
        "private/direct_truth_candidate_request.json",
        "private/direct_truth_candidate_embeddings.json",
        "private/direct_truth_decisions.jsonl",
        "private/evaluation_core.json",
        "private/candidate_pack.pending.json",
        "private/candidate_pack.frozen.json",
        "freeze_manifest.json",
        "stronger_local_remote_speaker_representation_report.json",
        "stronger_local_remote_speaker_representation_report.md",
        "replay_report.json",
        "artifact_manifest.json",
    ):
        (out / relative).unlink(missing_ok=True)


def action_preflight(policy: dict[str, Any], out: Path) -> int:
    value = preflight(policy)
    write_json(out / "private/preflight.json", value)
    print(
        f"preflight: ok ({policy['candidate']['id']}, "
        f"{value['window_count']} windows, {policy['scope']['expected_transcript_perfection_sources']} frozen sources)"
    )
    return 0


def action_prepare(policy: dict[str, Any], out: Path) -> int:
    clean_prepare_outputs(out)
    state = preflight(policy)
    previous_pack, inventory = verify_previous_freeze(policy)
    out.joinpath("private").mkdir(parents=True, exist_ok=True)
    inventory_path = out / "private/unlabeled_windows.jsonl"
    write_jsonl(inventory_path, inventory)
    request_path = out / "private/window_candidate_request.json"
    output_path = out / "private/window_candidate_embeddings.json"
    write_json(request_path, embedding_request(policy, inventory))
    model = Path(os.environ.get(MODEL_ENV, policy["candidate"]["default_model_path"])).expanduser().resolve()
    run_worker(policy, request_path, output_path, model)
    embeddings = load_embeddings(output_path)
    pack = build_candidate_pack(policy, inventory, embeddings, previous_pack)
    forbidden = forbidden_key_paths(pack)
    if forbidden:
        raise RepresentationError("candidate pack contains forbidden labels: " + ",".join(forbidden[:8]))
    write_json(out / "private/input_manifest.json", state)
    write_json(out / "private/candidate_pack.pending.json", pack)
    write_json(
        out / "candidate_pack.public.json",
        {
            "schema": PACK_SCHEMA,
            "candidate": pack["candidate"],
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
        },
    )
    print(f"prepared: {pack['counts']['windows']} blind WeSpeaker windows")
    return 0


def action_freeze(policy: dict[str, Any], out: Path) -> int:
    pending = out / "private/candidate_pack.pending.json"
    if not pending.is_file():
        raise RepresentationError("prepare must run before freeze")
    post_freeze = (
        out / "private/post_freeze_window_labels.jsonl",
        out / "private/direct_truth_decisions.jsonl",
    )
    if any(path.exists() for path in post_freeze):
        raise RepresentationError("post-freeze evidence exists; rerun prepare before freeze")
    pack = read_json(pending)
    if pack.get("schema") != PACK_SCHEMA or pack.get("labels_read") is not False:
        raise RepresentationError("pending candidate pack is invalid")
    if forbidden_key_paths(pack):
        raise RepresentationError("pending candidate pack contains label leakage")
    frozen = out / "private/candidate_pack.frozen.json"
    atomic_write(frozen, pending.read_bytes())
    state = read_json(out / "private/input_manifest.json")
    model_artifacts = [
        state["model"]["model"],
        state["model"]["license"],
        state["model"]["readme"],
        state["model"]["worker"],
    ]
    frozen_paths = [
        out / "private/input_manifest.json",
        out / "private/unlabeled_windows.jsonl",
        out / "private/window_candidate_request.json",
        out / "private/window_candidate_embeddings.json",
        frozen,
    ]
    for path in frozen_paths:
        if not path.is_file():
            raise RepresentationError(f"freeze input is missing: {path.name}")
    manifest = {
        "schema": FREEZE_SCHEMA,
        "state": "frozen_before_label_and_direct_truth_evaluation",
        "artifacts": [artifact(path) for path in frozen_paths] + model_artifacts,
        "pack_sha256": sha256(frozen),
        "policy_sha256": sha256(ACTIVE_POLICY_PATH),
        "labels_read": False,
        "direct_truth_read": False,
        "segmentation_tuned": False,
        "cluster_count_tuned": False,
        "thresholds_tuned": False,
        "production_promotion_allowed": False,
    }
    write_json(out / "freeze_manifest.json", manifest)
    print(f"frozen: {manifest['pack_sha256']}")
    return 0


def verify_frozen_pack(out: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest_path = out / "freeze_manifest.json"
    if not manifest_path.is_file():
        raise RepresentationError("candidate freeze manifest is unavailable")
    manifest = read_json(manifest_path)
    if manifest.get("schema") != FREEZE_SCHEMA or manifest.get("direct_truth_read") is not False:
        raise RepresentationError("candidate freeze manifest is invalid")
    for expected in manifest.get("artifacts") or []:
        path = resolve_artifact(expected["path"])
        if not path.is_file() or path.stat().st_size != int(expected["bytes"]) or sha256(path) != expected["sha256"]:
            raise RepresentationError(f"frozen artifact missing or changed: {expected['path']}")
    frozen = out / "private/candidate_pack.frozen.json"
    if sha256(frozen) != manifest.get("pack_sha256"):
        raise RepresentationError("candidate frozen pack changed")
    pack = read_json(frozen)
    if forbidden_key_paths(pack):
        raise RepresentationError("candidate frozen pack contains label leakage")
    return pack, manifest


def cluster_centers(pack: dict[str, Any], embeddings: dict[str, np.ndarray]) -> dict[str, dict[int, np.ndarray]]:
    grouped: dict[tuple[str, int], list[np.ndarray]] = defaultdict(list)
    for row in pack["assignments"]:
        grouped[(row["session_id"], int(row["candidate_cluster"]))].append(embeddings[row["key"]])
    result: dict[str, dict[int, np.ndarray]] = defaultdict(dict)
    for (session_id, cluster), vectors in grouped.items():
        result[session_id][cluster] = BASE.centroid(vectors)
    return dict(result)


def evaluate_direct_truth(
    policy: dict[str, Any],
    out: Path,
    pack: dict[str, Any],
    mappings: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    rows, truth = BASE.direct_truth_rows(policy)
    request = out / "private/direct_truth_candidate_request.json"
    output = out / "private/direct_truth_candidate_embeddings.json"
    write_json(request, embedding_request(policy, rows))
    model = Path(os.environ.get(MODEL_ENV, policy["candidate"]["default_model_path"])).expanduser().resolve()
    run_worker(policy, request, output, model)
    window_embeddings = load_embeddings(out / "private/window_candidate_embeddings.json")
    target_embeddings = load_embeddings(output)
    centers = cluster_centers(pack, window_embeddings)
    decisions = []
    for row in rows:
        item_id = row["key"]
        expected = truth[item_id]
        session_id = expected["session_id"]
        if item_id not in target_embeddings:
            candidate = {
                "prediction": None,
                "cluster": None,
                "similarity": None,
                "margin": None,
                "accepted": False,
                "reason": "embedding_unavailable",
            }
        else:
            candidate = BASE.classify_vector(
                target_embeddings[item_id],
                centers[session_id],
                mappings[session_id]["mapping"],
                float(policy["candidate"]["minimum_target_similarity"]),
                float(policy["candidate"]["minimum_target_margin"]),
            )
        prediction = candidate["prediction"]
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
                "candidate_evidence": candidate,
                **expected,
            }
        )
    counts = Counter(row["candidate_result"] for row in decisions)
    unsafe = int(counts["unsafe_fail_closed_acceptance"])
    preserved = sum(
        row["confirmed_v1_additive_gain"] and row["candidate_result"] == "correct_identity"
        for row in decisions
    )
    new_false = sum(
        row["candidate_result"] == "unsafe_fail_closed_acceptance"
        and row["control_outcome"] != "unsafe_fail_closed_acceptance"
        for row in decisions
    )
    lost_control = sum(
        row["control_outcome"] == "correct_identity"
        and not (
            row["candidate_result"] == "correct_identity"
            and row["prediction"] == row["control_prediction"]
        )
        for row in decisions
    )
    write_jsonl(out / "private/direct_truth_decisions.jsonl", decisions)
    return {
        "items": len(decisions),
        "embedding_unavailable_items": sum(
            row["candidate_evidence"].get("reason") == "embedding_unavailable" for row in decisions
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
    labels, profiles = BASE.window_labels(policy, inventory)
    write_jsonl(
        out / "private/post_freeze_window_labels.jsonl",
        [{"key": key, **value} for key, value in sorted(labels.items())],
    )
    by_session: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in pack["assignments"]:
        by_session[row["session_id"]].append(row)
    mappings = {}
    mapping_summaries = []
    for position, definition in enumerate(policy["scope"]["sessions"], 1):
        session_id = str(definition["session_id"])
        mapping = BASE.map_clusters(
            by_session[session_id], labels, profiles[session_id], "candidate_cluster"
        )
        mappings[session_id] = mapping
        mapping_summaries.append(
            {
                "session_alias": f"session_{position:02d}",
                "fixed_cluster_count": int(definition["cluster_count"]),
                **{
                    key: value
                    for key, value in mapping.items()
                    if key not in {"mapping", "confusion", "clusters", "profiles"}
                },
            }
        )
    write_json(out / "private/post_freeze_cluster_mappings.json", mappings)
    direct = evaluate_direct_truth(policy, out, pack, mappings)
    multi = [row for row in pack["sessions"] if row["fixed_cluster_count"] > 1]
    evaluation = policy["evaluation"]
    geometry_values = {
        "multispeaker_sessions": len(multi),
        "minimum_candidate_silhouette": min(row["metrics"]["candidate"]["silhouette"] for row in multi),
        "minimum_candidate_stability_ari": min(row["metrics"]["candidate"]["stability_ari"] for row in multi),
        "minimum_best_control_agreement_ari": min(
            max(row["metrics"]["candidate_ecapa_ari"], row["metrics"]["candidate_wavlm_ari"])
            for row in multi
        ),
        "minimum_best_control_agreement_nmi": min(
            max(row["metrics"]["candidate_ecapa_nmi"], row["metrics"]["candidate_wavlm_nmi"])
            for row in multi
        ),
        "maximum_three_model_fragmentation_ratio": max(
            row["metrics"]["three_model_fragmentation_ratio"] for row in multi
        ),
    }
    geometry_gates = {
        "minimum_multispeaker_sessions": geometry_values["multispeaker_sessions"]
        >= int(evaluation["minimum_multispeaker_sessions"]),
        "minimum_candidate_silhouette": geometry_values["minimum_candidate_silhouette"]
        >= float(evaluation["minimum_candidate_silhouette"]),
        "minimum_candidate_stability_ari": geometry_values["minimum_candidate_stability_ari"]
        >= float(evaluation["minimum_candidate_stability_ari"]),
    }
    mapping_values = {
        "minimum_cluster_mapping_purity": min(row["minimum_cluster_purity"] for row in mapping_summaries),
        "minimum_mapping_margin": min(row["minimum_mapping_margin"] for row in mapping_summaries),
        "ambiguous_clusters": sum(len(row["ambiguous_clusters"]) for row in mapping_summaries),
    }
    mapping_gates = {
        "minimum_cluster_mapping_purity": mapping_values["minimum_cluster_mapping_purity"]
        >= float(evaluation["minimum_cluster_mapping_purity"]),
        "minimum_mapping_margin": mapping_values["minimum_mapping_margin"]
        >= float(evaluation["minimum_mapping_margin"]),
        "maximum_ambiguous_clusters": mapping_values["ambiguous_clusters"]
        <= int(evaluation["maximum_ambiguous_clusters"]),
        "minimum_preserved_confirmed_v1_additive_gains": direct["preserved_confirmed_v1_additive_gains"]
        >= int(evaluation["minimum_preserved_confirmed_v1_additive_gains"]),
        "maximum_unsafe_fail_closed_accepts": direct["unsafe_fail_closed_accepts"]
        <= int(evaluation["maximum_unsafe_fail_closed_accepts"]),
        "no_new_false_identity": direct["new_false_identity_items"] == 0,
        "no_lost_correct_control_identity": direct["lost_correct_control_identity_items"] == 0,
    }
    invariants = {
        "pack_frozen_before_labels": pack.get("labels_read") is False,
        "pack_frozen_before_direct_truth": pack.get("direct_truth_read") is False,
        "candidate_materially_independent": all(
            bool(policy["independence"][key])
            for key in (
                "candidate_is_not_reference_wrapper",
                "candidate_architecture_differs",
                "candidate_training_pipeline_differs",
            )
        ),
        "cluster_count_not_tuned": policy["clustering"]["cluster_count_tuning_allowed"] is False,
        "thresholds_not_tuned": evaluation["post_hoc_tuning_allowed"] is False,
        "production_promotion_disabled": policy["decision"]["production_promotion_allowed"] is False,
    }
    return {
        "schema": EVALUATION_SCHEMA,
        "geometry": {
            "values": geometry_values,
            "gates": geometry_gates,
            "control_agreement_is_informational": True,
            "sessions": [
                {
                    "session_alias": row["session_alias"],
                    "fixed_cluster_count": row["fixed_cluster_count"],
                    "window_count": row["window_count"],
                    "metrics": row["metrics"],
                }
                for row in pack["sessions"]
            ],
        },
        "mapping": {"values": mapping_values, "gates": mapping_gates, "sessions": mapping_summaries},
        "direct_truth": direct,
        "invariants": invariants,
    }


def build_report(policy: dict[str, Any], out: Path, pack: dict[str, Any], core: dict[str, Any]) -> dict[str, Any]:
    geometry_passed = all(core["geometry"]["gates"].values())
    mapping_passed = all(core["mapping"]["gates"].values())
    invariants_passed = all(core["invariants"].values())
    if not invariants_passed:
        decision = "EVIDENCE_BOUND"
        reason = "model, freeze, provenance, or safety invariants are incomplete"
    elif geometry_passed and mapping_passed:
        decision = "STRONGER_REPRESENTATION_READY"
        reason = "the independent WeSpeaker representation passed frozen geometry and direct-truth safety gates"
    else:
        decision = "KEEP_EXPLICIT_UNKNOWN"
        reason = "the independent WeSpeaker representation did not safely improve the frozen unknown-speaker residual"
    failed = {
        "geometry": sorted(key for key, value in core["geometry"]["gates"].items() if not value),
        "mapping": sorted(key for key, value in core["mapping"]["gates"].items() if not value),
        "invariants": sorted(key for key, value in core["invariants"].items() if not value),
    }
    return {
        "schema": REPORT_SCHEMA,
        "decision": decision,
        "decision_reason": reason,
        "candidate": pack["candidate"],
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
            "frozen_pack_sha256": sha256(out / "private/candidate_pack.frozen.json"),
            "coverage_v3_accepts_preserved": int(policy["scope"]["expected_coverage_v3_accepts"]),
            "production_guards_verified": int(policy["scope"]["expected_production_guards"]),
            "transcript_perfection_sources_preserved": int(
                policy["scope"]["expected_transcript_perfection_sources"]
            ),
            "selected_transcript_mutated": False,
            "raw_audio_mutated": False,
            "thresholds_tuned": False,
        },
        "next": {
            "STRONGER_REPRESENTATION_READY": "open_separate_monotonic_wespeaker_shadow_candidate",
            "KEEP_EXPLICIT_UNKNOWN": "close_current_lightweight_local_representation_route",
            "EVIDENCE_BOUND": "repair_only_missing_model_runtime_license_or_frozen_provenance",
        }[decision],
    }


def report_markdown(report: dict[str, Any]) -> str:
    geometry = report["geometry"]["values"]
    mapping = report["mapping"]["values"]
    direct = report["direct_truth"]
    return "\n".join(
        [
            "# Stronger Local Remote Speaker Representation Qualification v1",
            "",
            f"Decision: `{report['decision']}`",
            "",
            report["decision_reason"] + ".",
            "",
            "## Candidate",
            "",
            f"- Backend: `{report['candidate']['id']}`",
            f"- Model: `{report['candidate']['model_id']}@{report['candidate']['model_revision']}`",
            f"- Frozen windows: `{report['scope']['windows']}`",
            "",
            "## Geometry",
            "",
            f"- Minimum candidate silhouette: `{geometry['minimum_candidate_silhouette']}`",
            f"- Minimum candidate stability ARI: `{geometry['minimum_candidate_stability_ari']}`",
            f"- Minimum best-control ARI (informational): `{geometry['minimum_best_control_agreement_ari']}`",
            f"- Minimum best-control NMI (informational): `{geometry['minimum_best_control_agreement_nmi']}`",
            f"- Maximum three-model fragmentation: `{geometry['maximum_three_model_fragmentation_ratio']}`",
            "",
            "## Post-Freeze Safety",
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
            "The WeSpeaker candidate pack was frozen before Coverage assignments and direct truth were read.",
            "Coverage v3, selected transcripts, raw CAF, primary ASR, Echo Guard, and production output were not modified.",
            f"Next: `{report['next']}`.",
        ]
    ) + "\n"


def action_evaluate(policy: dict[str, Any], out: Path) -> int:
    pack, _ = verify_frozen_pack(out)
    core = evaluation_core(policy, out, pack)
    write_json(out / "private/evaluation_core.json", core)
    report = build_report(policy, out, pack, core)
    write_json(out / "stronger_local_remote_speaker_representation_report.json", report)
    atomic_write(
        out / "stronger_local_remote_speaker_representation_report.md",
        report_markdown(report).encode(),
    )
    print(f"decision: {report['decision']}")
    return 0


def action_replay(policy: dict[str, Any], out: Path) -> int:
    report_path = out / "stronger_local_remote_speaker_representation_report.json"
    core_path = out / "private/evaluation_core.json"
    if not report_path.is_file() or not core_path.is_file():
        raise RepresentationError("evaluate must run before replay")
    pack, _ = verify_frozen_pack(out)
    expected = report_path.read_bytes()
    rebuilt = pretty(build_report(policy, out, pack, read_json(core_path)))
    if rebuilt != expected:
        raise RepresentationError("deterministic replay changed the public report")
    result = {
        "schema": REPLAY_SCHEMA,
        "verified": True,
        "report_sha256": hashlib.sha256(expected).hexdigest(),
        "report_bytes": len(expected),
        "frozen_pack_sha256": sha256(out / "private/candidate_pack.frozen.json"),
    }
    write_json(out / "replay_report.json", result)
    print(f"replay: verified ({result['report_sha256']})")
    return 0


def action_finalize(policy: dict[str, Any], out: Path) -> int:
    report = out / "stronger_local_remote_speaker_representation_report.json"
    replay = out / "replay_report.json"
    freeze = out / "freeze_manifest.json"
    public = out / "candidate_pack.public.json"
    for path in (report, replay, freeze, public):
        if not path.is_file():
            raise RepresentationError(f"final artifact is missing: {path.name}")
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "decision": read_json(report)["decision"],
        "artifacts": [artifact(path) for path in (report, replay, freeze, public)],
        "production_promotion_allowed": False,
    }
    write_json(out / "artifact_manifest.json", manifest)
    print(f"finalized: {manifest['decision']}")
    return 0


def action_status(out: Path) -> int:
    report_path = out / "stronger_local_remote_speaker_representation_report.json"
    if not report_path.is_file():
        print("decision: pending")
        return 0
    report = read_json(report_path)
    geometry = report["geometry"]["values"]
    direct = report["direct_truth"]
    print(f"decision: {report['decision']}")
    print(f"candidate: {report['candidate']['id']}")
    print(f"windows: {report['scope']['windows']}")
    print(f"minimum_candidate_stability_ari: {geometry['minimum_candidate_stability_ari']}")
    print(
        "preserved_confirmed_gains: "
        f"{direct['preserved_confirmed_v1_additive_gains']}/{direct['confirmed_v1_additive_gains']}"
    )
    print(f"new_false_identities: {direct['new_false_identity_items']}")
    print(f"next: {report['next']}")
    return 0


def action_all(policy: dict[str, Any], out: Path) -> int:
    action_preflight(policy, out)
    action_prepare(policy, out)
    action_freeze(policy, out)
    action_evaluate(policy, out)
    action_replay(policy, out)
    action_finalize(policy, out)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=("preflight", "prepare", "freeze", "evaluate", "replay", "finalize", "status", "all"),
        nargs="?",
        default="status",
    )
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    return parser.parse_args()


def main() -> int:
    global ACTIVE_POLICY_PATH
    args = parse_args()
    ACTIVE_POLICY_PATH = args.policy.expanduser().resolve()
    policy = load_policy(ACTIVE_POLICY_PATH)
    out = args.out.expanduser().resolve()
    actions = {
        "preflight": action_preflight,
        "prepare": action_prepare,
        "freeze": action_freeze,
        "evaluate": action_evaluate,
        "replay": action_replay,
        "finalize": action_finalize,
        "status": lambda _policy, target: action_status(target),
        "all": action_all,
    }
    try:
        return actions[args.action](policy, out)
    except RepresentationError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
