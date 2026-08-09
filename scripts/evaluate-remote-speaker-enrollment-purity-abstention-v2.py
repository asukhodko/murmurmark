#!/usr/bin/env python3
"""Evaluate one monotonic, purity-gated remote-speaker enrollment candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = ROOT / "policies/remote-speaker-enrollment-purity-abstention-hardening-v2.json"
DEFAULT_OUT = ROOT / "sessions/_reports/remote-speaker-enrollment-purity-abstention-hardening-v2"
ECAPA_WORKER = ROOT / "scripts/ecapa-speaker-embedding-worker.py"

POLICY_SCHEMA = "murmurmark.remote_speaker_enrollment_purity_abstention_policy/v2"
INPUT_SCHEMA = "murmurmark.remote_speaker_enrollment_purity_abstention_input/v2"
PUBLIC_INPUT_SCHEMA = "murmurmark.remote_speaker_enrollment_purity_abstention_public_input/v2"
PROFILE_SCHEMA = "murmurmark.remote_speaker_enrollment_purity_profile/v2"
ITEM_SCHEMA = "murmurmark.remote_speaker_enrollment_purity_item/v2"
DEVELOPMENT_SCHEMA = "murmurmark.remote_speaker_enrollment_purity_development_item/v2"
CORE_SCHEMA = "murmurmark.remote_speaker_enrollment_purity_abstention_core/v2"
REPORT_SCHEMA = "murmurmark.remote_speaker_enrollment_purity_abstention_report/v2"
REPLAY_SCHEMA = "murmurmark.remote_speaker_enrollment_purity_abstention_replay/v2"
MANIFEST_SCHEMA = "murmurmark.remote_speaker_enrollment_purity_abstention_manifest/v2"
REQUEST_SCHEMA = "murmurmark.speaker_embedding_request/v1"
EMBEDDING_SCHEMA = "murmurmark.speaker_embedding_result/v1"


class EvaluationError(ValueError):
    pass


def canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode()


def pretty(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True).encode() + b"\n"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(content)
    temporary.replace(path)


def write_json(path: Path, value: Any) -> None:
    atomic_write(path, pretty(value))


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    atomic_write(path, b"".join(canonical(row) for row in rows))


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise EvaluationError(f"expected JSON object: {path}")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise EvaluationError(f"expected JSON object at {path}:{line_number}")
        rows.append(value)
    return rows


def repo_path(raw: str) -> Path:
    path = Path(raw)
    if path.is_absolute() or ".." in path.parts:
        raise EvaluationError(f"source path must be repository-relative: {raw}")
    return ROOT / path


def portable_path(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT))


def artifact(path: Path, artifact_id: str | None = None) -> dict[str, Any]:
    row: dict[str, Any] = {
        "path": portable_path(path),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
    }
    if artifact_id:
        row["id"] = artifact_id
    return row


def normalize(vector: np.ndarray) -> np.ndarray:
    value = np.asarray(vector, dtype=np.float64)
    norm = float(np.linalg.norm(value))
    if not np.isfinite(norm) or norm <= 1e-12:
        raise EvaluationError("invalid embedding vector")
    return value / norm


def embedding_digest(vector: np.ndarray) -> str:
    rounded = [round(float(value), 9) for value in normalize(vector)]
    return hashlib.sha256(canonical(rounded)).hexdigest()


def load_policy(path: Path) -> dict[str, Any]:
    policy = read_json(path)
    if policy.get("schema") != POLICY_SCHEMA:
        raise EvaluationError(f"unsupported policy schema: {policy.get('schema')}")
    if policy.get("state") != "frozen_before_development_truth_evaluation":
        raise EvaluationError("policy was not frozen before development-truth evaluation")
    candidate = policy.get("candidate") or {}
    fixed = {
        "window_sec": 2.0,
        "core_similarity_threshold": 0.5,
        "minimum_core_windows": 4,
        "minimum_source_exemplars_in_core": 2,
        "minimum_core_pairwise_similarity": 0.45,
        "minimum_window_impostor_margin": 0.3,
        "minimum_target_similarity": 0.5,
        "minimum_target_margin": 0.3,
        "minimum_target_coverage_sec": 1.0,
        "minimum_active_frame_ratio": 0.2,
    }
    for key, expected in fixed.items():
        if float(candidate.get(key, -1)) != expected:
            raise EvaluationError(f"frozen candidate threshold changed: {key}")
    forbidden_true = (
        "threshold_grid_search_allowed",
        "post_hoc_tuning_allowed",
        "target_text_read",
        "human_names_read",
        "cross_session_voice_linking",
    )
    if any(candidate.get(key) is not False for key in forbidden_true):
        raise EvaluationError("candidate safety contract changed")
    decision = policy.get("decision") or {}
    if set(decision.get("allowed_outcomes") or []) != {
        "CANDIDATE_READY_FOR_DISJOINT_TRUTH_V2",
        "KEEP_COVERAGE_V3",
        "EVIDENCE_BOUND",
    }:
        raise EvaluationError("terminal outcomes changed")
    if decision.get("production_promotion_allowed") is not False:
        raise EvaluationError("production promotion must remain disabled")
    return policy


def verify_sources(policy: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    failures: list[str] = []
    seen: set[str] = set()
    for expected in policy.get("sources") or []:
        source_id = str(expected.get("id") or "")
        row = {"id": source_id, "path": expected.get("path"), "status": "verified"}
        if not source_id or source_id in seen:
            row["status"] = "invalid_id"
        seen.add(source_id)
        try:
            path = repo_path(str(expected.get("path") or ""))
        except EvaluationError:
            path = ROOT / "__invalid_source__"
            row["status"] = "invalid_path"
        if not path.is_file():
            row["status"] = "missing"
        else:
            row["bytes"] = path.stat().st_size
            row["sha256"] = sha256(path)
            if row["bytes"] != int(expected.get("bytes", -1)):
                row["status"] = "size_mismatch"
            if row["sha256"] != expected.get("sha256"):
                row["status"] = "sha256_mismatch"
        if row["status"] != "verified":
            failures.append(f"source_{row['status']}:{source_id}")
        rows.append(row)
    return rows, failures


def source_paths(policy: dict[str, Any]) -> dict[str, Path]:
    return {str(row["id"]): repo_path(str(row["path"])) for row in policy["sources"]}


def verify_artifact_set(
    values: list[dict[str, Any]] | dict[str, dict[str, Any]], expected_count: int, label: str
) -> tuple[list[dict[str, Any]], list[str]]:
    iterable = (
        [{"id": key, **value} for key, value in values.items()]
        if isinstance(values, dict)
        else list(values)
    )
    rows: list[dict[str, Any]] = []
    failures: list[str] = []
    if len(iterable) != expected_count:
        failures.append(f"{label}_count:{len(iterable)}")
    for expected in iterable:
        row = {"id": expected.get("id"), "path": expected.get("path"), "status": "verified"}
        try:
            path = repo_path(str(expected.get("path") or ""))
        except EvaluationError:
            path = ROOT / "__invalid_artifact__"
            row["status"] = "invalid_path"
        if not path.is_file():
            row["status"] = "missing"
        elif path.stat().st_size != int(expected.get("bytes", -1)):
            row["status"] = "size_mismatch"
        elif sha256(path) != expected.get("sha256"):
            row["status"] = "sha256_mismatch"
        if row["status"] != "verified":
            failures.append(f"{label}_{row['status']}:{row.get('id')}")
        rows.append(row)
    return rows, failures


def verify_frozen_artifacts(policy: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    paths = source_paths(policy)
    pack = read_json(paths["direct_truth_pack"])
    enrollment = read_json(paths["enrollment_input_manifest"])
    pack_rows, pack_failures = verify_artifact_set(
        pack.get("frozen_artifacts") or {}, int(policy["scope"]["review_pack_artifacts"]), "review_pack"
    )
    guard_rows, guard_failures = verify_artifact_set(
        enrollment.get("inherited_artifacts") or [],
        int(policy["scope"]["inherited_production_guards"]),
        "production_guard",
    )
    return {
        "review_pack": pack_rows,
        "production_guards": guard_rows,
    }, pack_failures + guard_failures


def model_provenance(policy: dict[str, Any]) -> tuple[dict[str, Any], Path, Path]:
    paths = source_paths(policy)
    shadow_policy = read_json(paths["ecapa_shadow_policy"])
    backend_policy = read_json(paths["ecapa_backend_policy"])
    backend_id = str(policy["candidate"]["backend_id"])
    definitions = [row for row in backend_policy.get("shortlist") or [] if row.get("id") == backend_id]
    if len(definitions) != 1:
        raise EvaluationError("ECAPA backend definition missing")
    definition = definitions[0]
    model = Path(
        os.environ.get(
            "MURMURMARK_REMOTE_SPEAKER_ECAPA_MODEL",
            str(shadow_policy["candidate"]["default_model_path"]),
        )
    ).expanduser().resolve()
    runtime = Path(
        os.environ.get(
            "MURMURMARK_REMOTE_SPEAKER_IDENTITY_RUNTIME",
            str(shadow_policy["candidate"]["default_runtime_path"]),
        )
    ).expanduser().resolve()
    files = []
    for name, expected_hash in sorted((definition.get("files") or {}).items()):
        path = model / name
        if not path.is_file() or sha256(path) != expected_hash:
            raise EvaluationError(f"ECAPA model missing or changed: {name}")
        files.append({"name": name, "bytes": path.stat().st_size, "sha256": expected_hash})
    python = runtime / "bin/python"
    if not python.is_file() or not os.access(python, os.X_OK):
        raise EvaluationError("ECAPA runtime is unavailable")
    return {
        "backend_id": backend_id,
        "model_id": definition["model_id"],
        "revision": definition["revision"],
        "family": definition["family"],
        "license": definition["license"],
        "model_tree_sha256": hashlib.sha256(canonical(files)).hexdigest(),
        "files": files,
        "device": "cpu",
        "offline": True,
    }, model, python


def build_purity_request(policy: dict[str, Any]) -> dict[str, Any]:
    paths = source_paths(policy)
    source = read_json(paths["shadow_embedding_request"])
    enrollment = read_json(paths["enrollment_input_manifest"])
    inherited = {str(row["path"]): row for row in enrollment.get("inherited_artifacts") or []}
    requests: list[dict[str, Any]] = []
    exemplars = [row for row in source.get("requests") or [] if str(row.get("key") or "").startswith("enroll:")]
    if len(exemplars) != int(policy["scope"]["source_exemplars"]):
        raise EvaluationError(f"unexpected exemplar count: {len(exemplars)}")
    window_sec = float(policy["candidate"]["window_sec"])
    window_count = int(policy["scope"]["subwindows_per_exemplar"])
    for row in sorted(exemplars, key=lambda value: str(value["key"])):
        path = Path(str(row["path"])).expanduser().resolve()
        relative = portable_path(path)
        guard = inherited.get(relative)
        if guard is None or path.stat().st_size != int(guard["bytes"]) or sha256(path) != guard["sha256"]:
            raise EvaluationError(f"exemplar not covered by frozen production guard: {row['key']}")
        parts = str(row["key"]).split(":")
        if len(parts) != 4:
            raise EvaluationError(f"unexpected exemplar key: {row['key']}")
        profile_key = f"{parts[1]}:{parts[2]}"
        source_exemplar_key = str(row["key"])
        start = float(row["start"])
        end = float(row["end"])
        if abs((end - start) - window_sec * window_count) > 1e-6:
            raise EvaluationError(f"exemplar duration changed: {row['key']}")
        for window_index in range(window_count):
            requests.append({
                "key": f"purity:{source_exemplar_key}:window:{window_index}",
                "path": str(path),
                "start": round(start + window_index * window_sec, 6),
                "end": round(start + (window_index + 1) * window_sec, 6),
                "minimum_sec": float(policy["candidate"]["minimum_window_sec"]),
                "profile_key": profile_key,
                "source_exemplar_key": source_exemplar_key,
                "window_index": window_index,
                "source_sha256": guard["sha256"],
            })
    provenance, _, _ = model_provenance(policy)
    return {
        "schema": REQUEST_SCHEMA,
        "model_id": provenance["model_id"],
        "model_revision": provenance["revision"],
        "allow_errors": True,
        "requests": requests,
    }


def prepare(policy: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    request = build_purity_request(policy)
    expected = int(policy["scope"]["expected_subwindow_requests"])
    if len(request["requests"]) != expected:
        raise EvaluationError(f"unexpected subwindow request count: {len(request['requests'])}")
    private = out_dir / "private"
    request_path = private / "purity_embedding_request.json"
    output_path = private / "purity_embeddings.json"
    write_json(request_path, request)
    provenance, model, python = model_provenance(policy)
    command = [
        "nice", "-n", "20", str(python), str(ECAPA_WORKER),
        "--request", str(request_path), "--output", str(output_path),
        "--model", str(model), "--threads", "4",
    ]
    environment = dict(os.environ)
    environment["HF_HUB_OFFLINE"] = "1"
    environment["TRANSFORMERS_OFFLINE"] = "1"
    result = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise EvaluationError(f"ECAPA purity worker failed: {result.stderr.strip()[-400:]}")
    embeddings = read_json(output_path)
    if embeddings.get("schema") != EMBEDDING_SCHEMA:
        raise EvaluationError("unsupported purity embedding output")
    if embeddings.get("request_sha256") != sha256(request_path):
        raise EvaluationError("purity embedding request hash mismatch")
    errors = embeddings.get("errors") or []
    if len(errors) > int(policy["candidate"]["maximum_embedding_errors"]):
        raise EvaluationError(f"too many purity embedding errors: {len(errors)}")
    return {
        "requests": len(request["requests"]),
        "embeddings": len(embeddings.get("rows") or []),
        "errors": len(errors),
        "model": provenance,
    }


def source_fingerprint(rows: list[dict[str, Any]]) -> str:
    return hashlib.sha256(canonical([(row["id"], row.get("sha256"), row["status"]) for row in rows])).hexdigest()


def freeze_inputs(policy_path: Path, policy: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    sources, source_failures = verify_sources(policy)
    frozen, artifact_failures = verify_frozen_artifacts(policy)
    request_path = out_dir / "private/purity_embedding_request.json"
    embeddings_path = out_dir / "private/purity_embeddings.json"
    if not request_path.is_file() or not embeddings_path.is_file():
        raise EvaluationError("run prepare before freeze")
    request = read_json(request_path)
    embeddings = read_json(embeddings_path)
    prepared_failures: list[str] = []
    if request.get("schema") != REQUEST_SCHEMA:
        prepared_failures.append("purity_request_schema")
    if len(request.get("requests") or []) != int(policy["scope"]["expected_subwindow_requests"]):
        prepared_failures.append("purity_request_count")
    if embeddings.get("schema") != EMBEDDING_SCHEMA:
        prepared_failures.append("purity_embeddings_schema")
    if embeddings.get("request_sha256") != sha256(request_path):
        prepared_failures.append("purity_request_digest")
    if len(embeddings.get("errors") or []) > int(policy["candidate"]["maximum_embedding_errors"]):
        prepared_failures.append("purity_embedding_errors")
    provenance, _, _ = model_provenance(policy)
    failures = source_failures + artifact_failures + prepared_failures
    manifest = {
        "schema": INPUT_SCHEMA,
        "state": "frozen_before_development_truth_evaluation",
        "policy": artifact(policy_path, "policy"),
        "source_fingerprint": source_fingerprint(sources),
        "sources": sources,
        "review_pack": {
            "expected": int(policy["scope"]["review_pack_artifacts"]),
            "verified": sum(row["status"] == "verified" for row in frozen["review_pack"]),
        },
        "production_guards": {
            "expected": int(policy["scope"]["inherited_production_guards"]),
            "verified": sum(row["status"] == "verified" for row in frozen["production_guards"]),
        },
        "prepared": {
            "request": artifact(request_path, "purity_embedding_request"),
            "embeddings": artifact(embeddings_path, "purity_embeddings"),
            "requests": len(request.get("requests") or []),
            "embeddings_count": len(embeddings.get("rows") or []),
            "errors": len(embeddings.get("errors") or []),
        },
        "model": provenance,
        "failures": sorted(set(failures)),
        "ready": not failures,
    }
    write_json(out_dir / "private/input_manifest.json", manifest)
    public = {
        "schema": PUBLIC_INPUT_SCHEMA,
        "state": manifest["state"],
        "source_fingerprint": manifest["source_fingerprint"],
        "source_count": len(sources),
        "sources_verified": sum(row["status"] == "verified" for row in sources),
        "review_pack": manifest["review_pack"],
        "production_guards": manifest["production_guards"],
        "prepared": {
            "requests": manifest["prepared"]["requests"],
            "embeddings": manifest["prepared"]["embeddings_count"],
            "errors": manifest["prepared"]["errors"],
        },
        "model": {
            "backend_id": provenance["backend_id"],
            "model_id": provenance["model_id"],
            "revision": provenance["revision"],
            "model_tree_sha256": provenance["model_tree_sha256"],
            "offline": True,
        },
        "failures": manifest["failures"],
        "ready": manifest["ready"],
    }
    write_json(out_dir / "input_manifest.public.json", public)
    return manifest


def load_payloads(policy: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for source_id, path in source_paths(policy).items():
        values[source_id] = read_jsonl(path) if path.suffix == ".jsonl" else read_json(path)
    return values


def verify_frozen_manifest(policy_path: Path, policy: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    path = out_dir / "private/input_manifest.json"
    if not path.is_file():
        raise EvaluationError("frozen input manifest is missing")
    manifest = read_json(path)
    if manifest.get("schema") != INPUT_SCHEMA or manifest.get("state") != policy["state"]:
        raise EvaluationError("frozen input manifest schema or state changed")
    if manifest.get("policy", {}).get("sha256") != sha256(policy_path):
        raise EvaluationError("policy changed after input freeze")
    sources, failures = verify_sources(policy)
    if manifest.get("source_fingerprint") != source_fingerprint(sources):
        failures.append("source_fingerprint_changed")
    frozen, artifact_failures = verify_frozen_artifacts(policy)
    failures.extend(artifact_failures)
    if sum(row["status"] == "verified" for row in frozen["review_pack"]) != int(
        policy["scope"]["review_pack_artifacts"]
    ):
        failures.append("review_pack_not_fully_verified")
    if sum(row["status"] == "verified" for row in frozen["production_guards"]) != int(
        policy["scope"]["inherited_production_guards"]
    ):
        failures.append("production_guards_not_fully_verified")
    for key, relative in (
        ("request", "private/purity_embedding_request.json"),
        ("embeddings", "private/purity_embeddings.json"),
    ):
        actual = out_dir / relative
        expected = manifest.get("prepared", {}).get(key) or {}
        if not actual.is_file() or actual.stat().st_size != int(expected.get("bytes", -1)):
            failures.append(f"prepared_{key}_size")
        elif sha256(actual) != expected.get("sha256"):
            failures.append(f"prepared_{key}_sha256")
    if failures or manifest.get("ready") is not True:
        raise EvaluationError("frozen input verification failed: " + ",".join(sorted(set(failures))))
    return manifest


def profile_key_parts(profile_key: str) -> tuple[str, str]:
    if ":" not in profile_key:
        raise EvaluationError(f"invalid profile key: {profile_key}")
    return tuple(profile_key.split(":", 1))  # type: ignore[return-value]


def build_purity_profiles(
    policy: dict[str, Any], request: dict[str, Any], embeddings: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, dict[str, np.ndarray]], dict[str, Any]]:
    request_by_key = {str(row["key"]): row for row in request.get("requests") or []}
    if len(request_by_key) != int(policy["scope"]["expected_subwindow_requests"]):
        raise EvaluationError("purity request key conservation failed")
    vectors: dict[str, np.ndarray] = {}
    for row in embeddings.get("rows") or []:
        key = str(row.get("key") or "")
        if key not in request_by_key or key in vectors:
            raise EvaluationError(f"unexpected or duplicate purity embedding: {key}")
        vectors[key] = normalize(np.asarray(row["embedding"], dtype=np.float64))
    error_keys = {str(row.get("key") or "") for row in embeddings.get("errors") or []}
    if set(vectors) & error_keys or set(vectors) | error_keys != set(request_by_key):
        raise EvaluationError("purity embedding result coverage mismatch")

    grouped: dict[str, list[tuple[str, dict[str, Any], np.ndarray]]] = defaultdict(list)
    for key, vector in vectors.items():
        request_row = request_by_key[key]
        grouped[str(request_row["profile_key"])].append((key, request_row, vector))

    config = policy["candidate"]
    provisional: dict[str, dict[str, Any]] = {}
    for profile_key, unsorted_rows in sorted(grouped.items()):
        rows = sorted(unsorted_rows, key=lambda value: value[0])
        count = len(rows)
        pairwise = np.asarray([[float(left[2] @ right[2]) for right in rows] for left in rows])
        means = (
            (pairwise.sum(axis=1) - 1.0) / (count - 1)
            if count > 1
            else np.asarray([-1.0])
        )
        medoid_index = int(np.argmax(means))
        medoid = rows[medoid_index][2]
        similarities = [float(row[2] @ medoid) for row in rows]
        selected_indices = [
            index for index, similarity in enumerate(similarities)
            if similarity >= float(config["core_similarity_threshold"])
        ]
        selected = [rows[index] for index in selected_indices]
        selected_pairwise = [
            float(rows[left][2] @ rows[right][2])
            for offset, left in enumerate(selected_indices)
            for right in selected_indices[offset + 1:]
        ]
        selected_min = min(selected_pairwise) if selected_pairwise else None
        source_exemplars = {str(row[1]["source_exemplar_key"]) for row in selected}
        preliminary_pass = (
            count >= int(config["minimum_available_windows"])
            and len(selected) >= int(config["minimum_core_windows"])
            and len(source_exemplars) >= int(config["minimum_source_exemplars_in_core"])
            and selected_min is not None
            and selected_min >= float(config["minimum_core_pairwise_similarity"])
        )
        centroid = normalize(np.mean([row[2] for row in selected], axis=0)) if preliminary_pass else None
        provisional[profile_key] = {
            "rows": rows,
            "medoid_key": rows[medoid_index][0],
            "medoid_mean_similarity": round(float(means[medoid_index]), 6),
            "similarities": similarities,
            "selected": selected,
            "selected_pairwise_min": selected_min,
            "selected_pairwise_mean": float(np.mean(selected_pairwise)) if selected_pairwise else None,
            "source_exemplars": source_exemplars,
            "preliminary_pass": preliminary_pass,
            "centroid": centroid,
            "error_count": sum(
                str(row.get("profile_key")) == profile_key
                for key, row in request_by_key.items() if key in error_keys
            ),
        }

    preliminary_centers = {
        key: row["centroid"] for key, row in provisional.items() if row["preliminary_pass"]
    }
    centers: dict[str, dict[str, np.ndarray]] = defaultdict(dict)
    profile_rows: list[dict[str, Any]] = []
    centroid_payload: dict[str, Any] = {}
    for profile_key, value in sorted(provisional.items()):
        session_id, speaker_id = profile_key_parts(profile_key)
        selected = value["selected"]
        window_margins: list[float] = []
        for _key, _request, vector in selected:
            impostors = [
                float(vector @ center)
                for other_key, center in preliminary_centers.items()
                if other_key != profile_key and profile_key_parts(other_key)[0] == session_id
            ]
            own = float(vector @ value["centroid"]) if value["centroid"] is not None else -1.0
            window_margins.append(own - max(impostors) if impostors else 1.0)
        minimum_impostor_margin = min(window_margins) if window_margins else None
        qualified = (
            value["preliminary_pass"]
            and minimum_impostor_margin is not None
            and minimum_impostor_margin >= float(config["minimum_window_impostor_margin"])
        )
        if qualified:
            centers[session_id][speaker_id] = value["centroid"]
            centroid_payload[profile_key] = {
                "embedding": [round(float(number), 9) for number in value["centroid"]],
                "embedding_sha256": embedding_digest(value["centroid"]),
            }
        rejection_reasons: list[str] = []
        if len(value["rows"]) < int(config["minimum_available_windows"]):
            rejection_reasons.append("insufficient_available_windows")
        if len(selected) < int(config["minimum_core_windows"]):
            rejection_reasons.append("insufficient_core_windows")
        if len(value["source_exemplars"]) < int(config["minimum_source_exemplars_in_core"]):
            rejection_reasons.append("core_does_not_span_both_exemplars")
        if value["selected_pairwise_min"] is None or value["selected_pairwise_min"] < float(
            config["minimum_core_pairwise_similarity"]
        ):
            rejection_reasons.append("weak_core_pairwise_similarity")
        if value["preliminary_pass"] and (
            minimum_impostor_margin is None
            or minimum_impostor_margin < float(config["minimum_window_impostor_margin"])
        ):
            rejection_reasons.append("weak_window_impostor_margin")
        selected_keys = {row[0] for row in selected}
        profile_rows.append({
            "schema": PROFILE_SCHEMA,
            "profile_key": profile_key,
            "session_id": session_id,
            "speaker_id": speaker_id,
            "status": "qualified" if qualified else "rejected",
            "available_windows": len(value["rows"]),
            "embedding_errors": int(value["error_count"]),
            "core_windows": len(selected),
            "source_exemplars_in_core": len(value["source_exemplars"]),
            "medoid_key": value["medoid_key"],
            "medoid_mean_similarity": value["medoid_mean_similarity"],
            "core_pairwise_min": round(float(value["selected_pairwise_min"]), 6)
            if value["selected_pairwise_min"] is not None else None,
            "core_pairwise_mean": round(float(value["selected_pairwise_mean"]), 6)
            if value["selected_pairwise_mean"] is not None else None,
            "minimum_window_impostor_margin": round(float(minimum_impostor_margin), 6)
            if minimum_impostor_margin is not None else None,
            "centroid_sha256": embedding_digest(value["centroid"]) if qualified else None,
            "rejection_reasons": rejection_reasons,
            "windows": [
                {
                    "key": key,
                    "source_exemplar_key": request_row["source_exemplar_key"],
                    "window_index": request_row["window_index"],
                    "embedding_sha256": embedding_digest(vector),
                    "similarity_to_medoid": round(float(similarity), 6),
                    "selected": key in selected_keys,
                }
                for (key, request_row, vector), similarity in zip(
                    value["rows"], value["similarities"], strict=True
                )
            ],
        })
    payload = {
        "schema": "murmurmark.remote_speaker_enrollment_purity_centroids/v2",
        "candidate_id": policy["candidate"]["id"],
        "profiles": centroid_payload,
    }
    return profile_rows, dict(centers), payload


def index_unique(rows: list[dict[str, Any]], key: str, label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        value = str(row.get(key) or "")
        if not value or value in result:
            raise EvaluationError(f"duplicate or empty {label}: {value}")
        result[value] = row
    return result


def score_embedding(vector: np.ndarray, centers: dict[str, np.ndarray]) -> dict[str, Any]:
    scores = sorted(
        ((float(vector @ center), speaker_id) for speaker_id, center in centers.items()),
        reverse=True,
    )
    if not scores:
        return {"top_speaker_id": None, "similarity": None, "margin": None, "scores": {}}
    similarity, speaker_id = scores[0]
    second = scores[1][0] if len(scores) > 1 else -1.0
    return {
        "top_speaker_id": speaker_id,
        "similarity": round(similarity, 6),
        "margin": round(similarity - second, 6),
        "scores": {candidate: round(score, 6) for score, candidate in scores},
    }


def context_conflicts(interval: dict[str, Any], speaker_id: str, maximum_distance: float) -> bool:
    speakers = set(interval.get("context_speakers") or []) | set(interval.get("same_utterance_speakers") or [])
    for side in ("left_neighbor", "right_neighbor"):
        neighbor = interval.get(side) or {}
        distance = neighbor.get("distance_sec")
        if isinstance(distance, (int, float)) and float(distance) <= maximum_distance:
            if neighbor.get("speaker_id"):
                speakers.add(str(neighbor["speaker_id"]))
    return any(candidate != speaker_id for candidate in speakers)


def build_item_decisions(
    policy: dict[str, Any], payloads: dict[str, Any], centers: dict[str, dict[str, np.ndarray]]
) -> list[dict[str, Any]]:
    comparisons = index_unique(payloads["enrollment_item_comparison"], "item_id", "comparison")
    decomposition = index_unique(payloads["item_error_decomposition"], "item_id", "decomposition")
    shadow_embeddings = {
        str(row["key"]): normalize(np.asarray(row["embedding"], dtype=np.float64))
        for row in payloads["shadow_embeddings"].get("rows") or []
    }
    embedding_errors = {
        str(row.get("key") or "") for row in payloads["shadow_embeddings"].get("errors") or []
    }
    config = policy["candidate"]
    rows: list[dict[str, Any]] = []
    for item_id, source in sorted(comparisons.items()):
        session_id = str(source["session_id"])
        control_speaker = source.get("control", {}).get("speaker_id")
        item_key = str(source.get("item_embedding_key") or "")
        vector = shadow_embeddings.get(item_key)
        score = score_embedding(vector, centers.get(session_id, {})) if vector is not None else {
            "top_speaker_id": None, "similarity": None, "margin": None, "scores": {}
        }
        top_speaker = score["top_speaker_id"]
        detail = decomposition.get(item_id)
        if detail is None:
            raise EvaluationError(f"missing decomposition row: {item_id}")
        interval = detail.get("interval") or {}
        audio = detail.get("audio") or {}
        original_candidate = source.get("candidate", {}).get("speaker_id")
        gates = {
            "embedding_available": vector is not None and item_key not in embedding_errors,
            "qualified_profile_available": top_speaker is not None,
            "original_candidate_same_speaker": (
                original_candidate == top_speaker
                if config.get("require_original_candidate_same_speaker") is True else True
            ),
            "minimum_similarity": score["similarity"] is not None
            and float(score["similarity"]) >= float(config["minimum_target_similarity"]),
            "minimum_margin": score["margin"] is not None
            and float(score["margin"]) >= float(config["minimum_target_margin"]),
            "minimum_coverage": float(source.get("coverage_weight_sec") or 0)
            >= float(config["minimum_target_coverage_sec"]),
            "speech_supported": audio.get("speech_supported") is True,
            "minimum_active_frame_ratio": float(audio.get("active_frame_ratio") or 0)
            >= float(config["minimum_active_frame_ratio"]),
            "interval_not_risky": interval.get("risky") is not True,
            "context_not_conflicting": top_speaker is not None and not context_conflicts(
                interval, str(top_speaker), float(config["maximum_context_neighbor_distance_sec"])
            ),
        }
        if control_speaker is not None:
            candidate_speaker = control_speaker
            reason = "coverage_v3_preserved"
        elif all(gates.values()):
            candidate_speaker = top_speaker
            reason = "purity_and_abstention_gates_passed"
        else:
            candidate_speaker = None
            reason = "fail_closed_unknown"
        rows.append({
            "schema": ITEM_SCHEMA,
            "item_id": item_id,
            "session_id": session_id,
            "utterance_id": source.get("utterance_id"),
            "start": source.get("start"),
            "end": source.get("end"),
            "word_ids": source.get("word_ids"),
            "word_count": int(source.get("word_count") or 0),
            "coverage_weight_sec": round(float(source.get("coverage_weight_sec") or 0), 6),
            "source_item_sha256": source.get("item_embedding_sha256"),
            "control_speaker_id": control_speaker,
            "original_candidate_speaker_id": original_candidate,
            "candidate_speaker_id": candidate_speaker,
            "change": "preserved_control" if control_speaker is not None else "newly_accepted"
            if candidate_speaker is not None else "stable_abstention",
            "reason": reason,
            "score": score,
            "gates": gates,
            "provenance": {
                "candidate_id": policy["candidate"]["id"],
                "target_text_read": False,
                "threshold_tuned": False,
                "cross_session_voice_linked": False,
            },
        })
    return rows


def prediction_outcome(truth: str, prediction: str | None) -> str:
    if truth.startswith("remote_speaker_"):
        if prediction is None:
            return "abstained_positive"
        return "correct_identity" if prediction == truth else "false_identity"
    return "safe_abstention" if prediction is None else "unsafe_fail_closed_acceptance"


def aggregate_outcomes(rows: list[dict[str, Any]], side: str) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    words: Counter[str] = Counter()
    seconds: Counter[str] = Counter()
    for row in rows:
        outcome = str(row[f"{side}_outcome"])
        counts[outcome] += 1
        words[outcome] += int(row["word_count"])
        seconds[outcome] += float(row["coverage_weight_sec"])
    return {
        "items": dict(sorted(counts.items())),
        "words": dict(sorted(words.items())),
        "seconds": {key: round(value, 6) for key, value in sorted(seconds.items())},
    }


def build_development_rows(
    payloads: dict[str, Any], decisions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    decision_by_item = index_unique(decisions, "item_id", "candidate decision")
    rows: list[dict[str, Any]] = []
    for truth in sorted(payloads["direct_truth_items"], key=lambda row: str(row["item_id"])):
        item_id = str(truth["item_id"])
        decision = decision_by_item.get(item_id)
        if decision is None:
            raise EvaluationError(f"direct truth item missing candidate decision: {item_id}")
        truth_outcome = str(truth["truth_outcome"])
        control_prediction = decision.get("control_speaker_id")
        candidate_prediction = decision.get("candidate_speaker_id")
        rows.append({
            "schema": DEVELOPMENT_SCHEMA,
            "item_id": item_id,
            "truth_kind": truth["truth_kind"],
            "truth_outcome": truth_outcome,
            "control_prediction": control_prediction,
            "v1_candidate_prediction": truth.get("candidate_prediction"),
            "candidate_prediction": candidate_prediction,
            "control_outcome": prediction_outcome(truth_outcome, control_prediction),
            "v1_candidate_outcome": truth.get("candidate_outcome"),
            "candidate_outcome": prediction_outcome(truth_outcome, candidate_prediction),
            "confirmed_v1_additive_gain": (
                truth.get("control_outcome") != "correct_identity"
                and truth.get("candidate_outcome") == "correct_identity"
            ),
            "word_count": int(truth.get("word_count") or 0),
            "coverage_weight_sec": round(float(truth.get("coverage_weight_sec") or 0), 6),
        })
    return rows


def compute_evaluation(
    policy_path: Path, policy: dict[str, Any], out_dir: Path
) -> dict[str, bytes]:
    manifest = verify_frozen_manifest(policy_path, policy, out_dir)
    payloads = load_payloads(policy)
    request = read_json(out_dir / "private/purity_embedding_request.json")
    embeddings = read_json(out_dir / "private/purity_embeddings.json")
    profile_rows, centers, centroid_payload = build_purity_profiles(policy, request, embeddings)
    item_rows = build_item_decisions(policy, payloads, centers)
    development_rows = build_development_rows(payloads, item_rows)

    scope = policy["scope"]
    decision_policy = policy["decision"]
    control_accepts = [row for row in item_rows if row["control_speaker_id"] is not None]
    preserved_accepts = [
        row for row in control_accepts if row["candidate_speaker_id"] == row["control_speaker_id"]
    ]
    changed_accepts = [
        row for row in control_accepts if row["candidate_speaker_id"] != row["control_speaker_id"]
    ]
    candidate_additions = [
        row for row in item_rows
        if row["control_speaker_id"] is None and row["candidate_speaker_id"] is not None
    ]
    control = aggregate_outcomes(development_rows, "control")
    candidate = aggregate_outcomes(development_rows, "candidate")
    v1_candidate = aggregate_outcomes(development_rows, "v1_candidate")
    confirmed_gains = [row for row in development_rows if row["confirmed_v1_additive_gain"]]
    retained_gains = [
        row for row in confirmed_gains if row["candidate_outcome"] == "correct_identity"
    ]
    lost_controls = sum(
        row["control_outcome"] == "correct_identity" and row["candidate_outcome"] != "correct_identity"
        for row in development_rows
    )
    new_false = max(
        0,
        candidate["items"].get("false_identity", 0) - control["items"].get("false_identity", 0),
    )
    candidate_unsafe = candidate["items"].get("unsafe_fail_closed_acceptance", 0)
    v1_unsafe = v1_candidate["items"].get("unsafe_fail_closed_acceptance", 0)
    exact_conservation = (
        len(item_rows) == int(scope["source_items"])
        and sum(row["word_count"] for row in item_rows) == int(scope["source_words"])
        and all(row["word_count"] == len(row["word_ids"] or []) for row in item_rows)
    )
    gates = {
        "frozen_inputs_verified": manifest.get("ready") is True,
        "all_coverage_v3_accepts_preserved": len(preserved_accepts)
        == int(scope["coverage_v3_accepted_items"]),
        "no_changed_accepted_identity": not changed_accepts,
        "exact_word_and_timestamp_conservation": exact_conservation,
        "no_lost_correct_control_identity": lost_controls
        <= int(decision_policy["maximum_lost_correct_control_identity_items"]),
        "no_new_false_identity": new_false <= int(decision_policy["maximum_new_false_identity_items"]),
        "fail_closed_unsafe_accepts_at_or_below_control": candidate_unsafe
        <= int(decision_policy["maximum_fail_closed_unsafe_accepts"]),
        "fail_closed_improved_over_v1_candidate": candidate_unsafe < v1_unsafe,
        "minimum_preserved_confirmed_v1_gains": len(retained_gains)
        >= int(decision_policy["minimum_preserved_confirmed_v1_gains"]),
    }
    integrity_gates = (
        "frozen_inputs_verified",
        "all_coverage_v3_accepts_preserved",
        "no_changed_accepted_identity",
        "exact_word_and_timestamp_conservation",
    )
    if not all(gates[name] for name in integrity_gates):
        decision = "EVIDENCE_BOUND"
    elif all(gates.values()):
        decision = "CANDIDATE_READY_FOR_DISJOINT_TRUTH_V2"
    else:
        decision = "KEEP_COVERAGE_V3"
    profile_status = Counter(row["status"] for row in profile_rows)
    rejection_reasons = Counter(
        reason for row in profile_rows for reason in row.get("rejection_reasons") or []
    )
    item_rejections = Counter(
        gate
        for row in item_rows if row["control_speaker_id"] is None and row["candidate_speaker_id"] is None
        for gate, passed in row["gates"].items() if not passed
    )
    core = {
        "schema": CORE_SCHEMA,
        "generator": {
            "name": "evaluate-remote-speaker-enrollment-purity-abstention-v2",
            "version": "0.2.0",
            "mode": "deterministic_offline_development_evaluation",
        },
        "decision": decision,
        "scope": {
            "source_items": len(item_rows),
            "source_words": sum(row["word_count"] for row in item_rows),
            "coverage_v3_accepted_items": len(control_accepts),
            "development_primary_items": len(development_rows),
            "development_positive_identity_items": sum(
                str(row["truth_outcome"]).startswith("remote_speaker_") for row in development_rows
            ),
        },
        "purity": {
            "profiles": len(profile_rows),
            "qualified_profiles": profile_status.get("qualified", 0),
            "rejected_profiles": profile_status.get("rejected", 0),
            "embedding_requests": len(request.get("requests") or []),
            "embedding_results": len(embeddings.get("rows") or []),
            "embedding_errors": len(embeddings.get("errors") or []),
            "profile_rejection_reasons": dict(sorted(rejection_reasons.items())),
        },
        "candidate": {
            "id": policy["candidate"]["id"],
            "added_items": len(candidate_additions),
            "added_words": sum(row["word_count"] for row in candidate_additions),
            "added_seconds": round(sum(row["coverage_weight_sec"] for row in candidate_additions), 6),
            "item_rejection_reasons": dict(sorted(item_rejections.items())),
            "threshold_grid_search_used": False,
            "post_hoc_tuning_used": False,
        },
        "development": {
            "control": control,
            "v1_candidate": v1_candidate,
            "candidate": candidate,
            "confirmed_v1_additive_gains": len(confirmed_gains),
            "preserved_confirmed_v1_additive_gains": len(retained_gains),
            "lost_correct_control_identity_items": int(lost_controls),
            "new_false_identity_items": int(new_false),
            "control_fail_closed_unsafe_accepts": control["items"].get(
                "unsafe_fail_closed_acceptance", 0
            ),
            "v1_candidate_fail_closed_unsafe_accepts": v1_unsafe,
            "candidate_fail_closed_unsafe_accepts": candidate_unsafe,
        },
        "gates": gates,
        "limitations": {
            "development_truth_reused_for_design": True,
            "disjoint_truth_required_before_promotion": True,
            "direct_identity_truth_items": int(scope["development_positive_identity_items"]),
            "candidate_is_not_production_qualified": True,
        },
        "safety": {
            "shadow_only": True,
            "production_mutated": False,
            "coverage_v3_mutated": False,
            "selected_transcript_mutated": False,
            "raw_audio_mutated": False,
            "primary_asr_mutated": False,
            "echo_guard_mutated": False,
            "thresholds_tuned": False,
            "target_text_read": False,
            "human_names_inferred": False,
            "cross_session_voice_linking": False,
            "speech_text_public": False,
            "session_ids_public": False,
            "embeddings_public": False,
        },
    }
    outputs = {
        "private/purity_profiles.jsonl": b"".join(canonical(row) for row in profile_rows),
        "private/purified_centroids.json": pretty(centroid_payload),
        "private/item_decisions.jsonl": b"".join(canonical(row) for row in item_rows),
        "private/development_adjudication.jsonl": b"".join(canonical(row) for row in development_rows),
        "private/evaluation_core.json": pretty(core),
    }
    return outputs


def evaluate(policy_path: Path, policy: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    outputs = compute_evaluation(policy_path, policy, out_dir)
    for relative, content in outputs.items():
        atomic_write(out_dir / relative, content)
    return json.loads(outputs["private/evaluation_core.json"])


def replay(policy_path: Path, policy: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    expected = compute_evaluation(policy_path, policy, out_dir)
    compared: dict[str, dict[str, Any]] = {}
    matched = True
    for relative, content in expected.items():
        path = out_dir / relative
        row = {
            "path": relative,
            "expected_sha256": hashlib.sha256(content).hexdigest(),
            "actual_sha256": sha256(path) if path.is_file() else None,
            "matched": path.is_file() and path.read_bytes() == content,
        }
        matched = matched and bool(row["matched"])
        compared[relative] = row
    result = {"schema": REPLAY_SCHEMA, "matched": matched, "artifacts": compared}
    write_json(out_dir / "replay_report.json", result)
    return result


def public_report(core: dict[str, Any], replay_verified: bool) -> dict[str, Any]:
    report = {key: value for key, value in core.items() if key != "schema"}
    report["schema"] = REPORT_SCHEMA
    report["replay_verified"] = replay_verified
    report["portable_aggregate"] = {
        "decision": report["decision"],
        "qualified_profiles": report.get("purity", {}).get("qualified_profiles", 0),
        "rejected_profiles": report.get("purity", {}).get("rejected_profiles", 0),
        "candidate_added_items": report.get("candidate", {}).get("added_items", 0),
        "preserved_confirmed_v1_gains": report.get("development", {}).get(
            "preserved_confirmed_v1_additive_gains", 0
        ),
        "candidate_fail_closed_unsafe_accepts": report.get("development", {}).get(
            "candidate_fail_closed_unsafe_accepts", 0
        ),
        "production_promoted": False,
    }
    return report


def markdown(report: dict[str, Any]) -> str:
    aggregate = report["portable_aggregate"]
    failed = [name for name, passed in report.get("gates", {}).items() if not passed]
    lines = [
        "# Remote Speaker Enrollment Purity and Abstention Hardening v2",
        "",
        f"Decision: `{report['decision']}`",
        "",
        "## Evidence",
        "",
        f"- qualified profiles: `{aggregate['qualified_profiles']}`; rejected: `{aggregate['rejected_profiles']}`;",
        f"- monotonic additions: `{aggregate['candidate_added_items']}` items;",
        f"- preserved confirmed v1 gains: `{aggregate['preserved_confirmed_v1_gains']}` / `3`;",
        f"- fail-closed unsafe accepts: control `8`, v1 candidate `13`, v2 `{aggregate['candidate_fail_closed_unsafe_accepts']}`;",
        f"- deterministic replay: `{'passed' if report.get('replay_verified') else 'failed'}`.",
        "",
        "## Decision",
        "",
    ]
    if report["decision"] == "CANDIDATE_READY_FOR_DISJOINT_TRUTH_V2":
        lines.append("The candidate may be frozen for a new disjoint blind truth set. Production remains Coverage v3.")
    elif report["decision"] == "KEEP_COVERAGE_V3":
        lines.append("Purity gates remain safer than the available additions. Production remains Coverage v3.")
    else:
        lines.append("Input or replay evidence is incomplete. Restore frozen provenance before continuing.")
    lines.extend([
        "",
        "## Gates",
        "",
        f"Failed gates: `{', '.join(failed) if failed else 'none'}`.",
        "",
        "No speech, session identifiers, human names, reviewer identity or embeddings are published.",
        "",
    ])
    return "\n".join(lines)


def finalize(
    policy_path: Path, policy: dict[str, Any], out_dir: Path, manifest_path: Path | None
) -> dict[str, Any]:
    core = read_json(out_dir / "private/evaluation_core.json")
    replay_result = read_json(out_dir / "replay_report.json")
    replay_verified = replay_result.get("matched") is True
    if not replay_verified:
        core["decision"] = "EVIDENCE_BOUND"
        core.setdefault("gates", {})["deterministic_replay"] = False
    else:
        core.setdefault("gates", {})["deterministic_replay"] = True
    report = public_report(core, replay_verified)
    report_path = out_dir / "remote_speaker_enrollment_purity_abstention_report.json"
    markdown_path = out_dir / "remote_speaker_enrollment_purity_abstention_report.md"
    write_json(report_path, report)
    atomic_write(markdown_path, markdown(report).encode())
    if manifest_path:
        manifest = {
            "schema": MANIFEST_SCHEMA,
            "decision": report["decision"],
            "scope": report.get("scope"),
            "purity": report.get("purity"),
            "candidate": report.get("candidate"),
            "development": report.get("development"),
            "gates": report.get("gates"),
            "limitations": report.get("limitations"),
            "safety": report.get("safety"),
            "replay_verified": replay_verified,
            "artifacts": {
                "policy": artifact(policy_path, "policy"),
                "public_input_manifest": artifact(out_dir / "input_manifest.public.json", "public_input_manifest"),
                "report": artifact(report_path, "report"),
                "markdown_report": artifact(markdown_path, "markdown_report"),
                "replay_report": artifact(out_dir / "replay_report.json", "replay_report"),
            },
        }
        write_json(manifest_path, manifest)
    return report


def evidence_bound(out_dir: Path, reason: str) -> None:
    report = {
        "schema": REPORT_SCHEMA,
        "decision": "EVIDENCE_BOUND",
        "scope": {},
        "purity": {},
        "candidate": {},
        "development": {},
        "gates": {"input_integrity": False, "deterministic_replay": False},
        "limitations": {"reason": reason[:500]},
        "safety": {
            "shadow_only": True,
            "production_mutated": False,
            "coverage_v3_mutated": False,
            "selected_transcript_mutated": False,
            "raw_audio_mutated": False,
            "primary_asr_mutated": False,
            "echo_guard_mutated": False,
            "thresholds_tuned": False,
            "target_text_read": False,
            "human_names_inferred": False,
            "cross_session_voice_linking": False,
            "speech_text_public": False,
            "session_ids_public": False,
            "embeddings_public": False,
        },
        "replay_verified": False,
        "portable_aggregate": {
            "decision": "EVIDENCE_BOUND",
            "qualified_profiles": 0,
            "rejected_profiles": 0,
            "candidate_added_items": 0,
            "preserved_confirmed_v1_gains": 0,
            "candidate_fail_closed_unsafe_accepts": 0,
            "production_promoted": False,
        },
    }
    write_json(out_dir / "remote_speaker_enrollment_purity_abstention_report.json", report)
    atomic_write(out_dir / "remote_speaker_enrollment_purity_abstention_report.md", markdown(report).encode())


def status(out_dir: Path) -> int:
    path = out_dir / "remote_speaker_enrollment_purity_abstention_report.json"
    if not path.is_file():
        print("decision: NOT_EVALUATED")
        return 2
    report = read_json(path)
    aggregate = report.get("portable_aggregate") or {}
    print(f"decision: {report['decision']}")
    print(
        "profiles: "
        f"qualified={aggregate.get('qualified_profiles', 0)} "
        f"rejected={aggregate.get('rejected_profiles', 0)}"
    )
    print(f"candidate additions: {aggregate.get('candidate_added_items', 0)}")
    print(f"preserved confirmed v1 gains: {aggregate.get('preserved_confirmed_v1_gains', 0)}/3")
    print(f"candidate fail-closed unsafe accepts: {aggregate.get('candidate_fail_closed_unsafe_accepts', 0)}")
    print(f"replay verified: {report.get('replay_verified')}")
    return 2 if report["decision"] == "EVIDENCE_BOUND" else 0


def preflight(policy: dict[str, Any]) -> dict[str, Any]:
    sources, failures = verify_sources(policy)
    frozen: dict[str, Any] = {"review_pack": [], "production_guards": []}
    if not failures:
        frozen, artifact_failures = verify_frozen_artifacts(policy)
        failures.extend(artifact_failures)
    model: dict[str, Any] | None = None
    try:
        model, _, _ = model_provenance(policy)
    except EvaluationError as error:
        failures.append(str(error))
    return {
        "decision": "EVIDENCE_BOUND" if failures else "READY",
        "sources": {"expected": len(sources), "verified": sum(row["status"] == "verified" for row in sources)},
        "review_pack": {
            "expected": int(policy["scope"]["review_pack_artifacts"]),
            "verified": sum(row["status"] == "verified" for row in frozen["review_pack"]),
        },
        "production_guards": {
            "expected": int(policy["scope"]["inherited_production_guards"]),
            "verified": sum(row["status"] == "verified" for row in frozen["production_guards"]),
        },
        "model": model,
        "failures": sorted(set(failures)),
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument(
        "action", choices=("preflight", "prepare", "freeze", "evaluate", "replay", "finalize", "status", "all")
    )
    result.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    result.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    result.add_argument("--write-manifest", type=Path)
    return result


def main() -> int:
    args = parser().parse_args()
    policy_path = args.policy.resolve()
    out_dir = args.out_dir.resolve()
    try:
        policy = load_policy(policy_path)
        if args.action == "status":
            return status(out_dir)
        if args.action == "preflight":
            result = preflight(policy)
            print(json.dumps(result, sort_keys=True))
            return 2 if result["decision"] == "EVIDENCE_BOUND" else 0
        if args.action in {"prepare", "all"}:
            result = preflight(policy)
            if result["decision"] == "EVIDENCE_BOUND":
                raise EvaluationError("preflight failed: " + ",".join(result["failures"]))
            prepared = prepare(policy, out_dir)
            if args.action == "prepare":
                print(json.dumps(prepared, sort_keys=True))
                return 0
        if args.action in {"freeze", "all"}:
            frozen = freeze_inputs(policy_path, policy, out_dir)
            if frozen.get("ready") is not True:
                raise EvaluationError("input freeze failed: " + ",".join(frozen.get("failures") or []))
            if args.action == "freeze":
                print(json.dumps({"ready": True, "source_fingerprint": frozen["source_fingerprint"]}, sort_keys=True))
                return 0
        if args.action in {"evaluate", "all"}:
            core = evaluate(policy_path, policy, out_dir)
            if args.action == "evaluate":
                print(json.dumps({"decision": core["decision"]}, sort_keys=True))
                return 2 if core["decision"] == "EVIDENCE_BOUND" else 0
        if args.action in {"replay", "all"}:
            replay_result = replay(policy_path, policy, out_dir)
            if args.action == "replay":
                print(json.dumps(replay_result, sort_keys=True))
                return 0 if replay_result["matched"] else 2
        if args.action in {"finalize", "all"}:
            report = finalize(policy_path, policy, out_dir, args.write_manifest)
            print(json.dumps({"decision": report["decision"], "report": str(out_dir)}, sort_keys=True))
            return 2 if report["decision"] == "EVIDENCE_BOUND" else 0
        return 0
    except (OSError, EvaluationError, json.JSONDecodeError) as error:
        evidence_bound(out_dir, str(error))
        print(f"decision: EVIDENCE_BOUND ({error})", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
