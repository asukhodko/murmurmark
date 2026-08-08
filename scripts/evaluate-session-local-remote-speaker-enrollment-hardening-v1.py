#!/usr/bin/env python3
"""Evaluate one frozen session-local ECAPA enrollment hardening candidate."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, Iterable

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.1.0"
POLICY_SCHEMA = "murmurmark.session_local_remote_speaker_enrollment_hardening_policy/v1"
PRIVATE_INPUT_SCHEMA = "murmurmark.session_local_remote_speaker_enrollment_hardening_input/v1"
PUBLIC_INPUT_SCHEMA = "murmurmark.session_local_remote_speaker_enrollment_hardening_public_input/v1"
PROFILE_SCHEMA = "murmurmark.session_local_remote_speaker_enrollment_profile/v1"
CENTROID_SCHEMA = "murmurmark.session_local_remote_speaker_candidate_centroids/v1"
ITEM_SCHEMA = "murmurmark.session_local_remote_speaker_enrollment_item_comparison/v1"
REPORT_SCHEMA = "murmurmark.session_local_remote_speaker_enrollment_hardening_report/v1"
REPLAY_SCHEMA = "murmurmark.session_local_remote_speaker_enrollment_hardening_replay/v1"
TRACKED_SCHEMA = "murmurmark.session_local_remote_speaker_enrollment_hardening_manifest/v1"
DEFAULT_POLICY = ROOT / "policies/session-local-remote-speaker-enrollment-hardening-v1.json"
DEFAULT_OUT = ROOT / "sessions/_reports/session-local-remote-speaker-enrollment-hardening-v1"
DEFAULT_TRACKED = ROOT / "docs/testing/session-local-remote-speaker-enrollment-hardening-v1-manifest.json"
ALLOWED_OUTCOMES = {
    "ADVANCE_HARDENED_ENROLLMENT_SHADOW",
    "DO_NOT_ADVANCE_ENROLLMENT_HARDENING",
    "EVIDENCE_BOUND",
}


class EnrollmentHardeningError(RuntimeError):
    pass


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def pretty_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def jsonl_bytes(rows: Iterable[dict[str, Any]]) -> bytes:
    return b"".join(canonical_json(row) for row in rows)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EnrollmentHardeningError(f"invalid_json:{path}:{type(error).__name__}") from error
    if not isinstance(value, dict):
        raise EnrollmentHardeningError(f"json_object_required:{path}")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise EnrollmentHardeningError(f"jsonl_object_required:{path}:{number}")
            rows.append(row)
    except (OSError, json.JSONDecodeError) as error:
        raise EnrollmentHardeningError(f"invalid_jsonl:{path}:{type(error).__name__}") from error
    return rows


def atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_json(path: Path, value: Any) -> None:
    atomic_write(path, pretty_json(value))


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    atomic_write(path, jsonl_bytes(rows))


def resolve(value: str | Path) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (ROOT / path).resolve()


def portable(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError as error:
        raise EnrollmentHardeningError(f"path_outside_repository:{path}") from error


def fingerprint(path: Path, role: str) -> dict[str, Any]:
    if not path.is_file():
        raise EnrollmentHardeningError(f"required_artifact_missing:{role}")
    return {"id": role, "path": portable(path), "bytes": path.stat().st_size, "sha256": sha256(path)}


def normalize(vector: np.ndarray) -> np.ndarray:
    vector = np.asarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(vector))
    if not math.isfinite(norm) or norm <= 1e-12:
        raise EnrollmentHardeningError("invalid_embedding_vector")
    return np.asarray(vector / norm, dtype=np.float32)


def embedding_digest(vector: np.ndarray | None) -> str | None:
    if vector is None:
        return None
    return sha256_bytes(np.asarray(vector, dtype="<f4").tobytes())


def parse_enrollment_key(key: str) -> tuple[str, str] | None:
    parts = key.split(":")
    return (parts[1], parts[2]) if len(parts) == 4 and parts[0] == "enroll" else None


def validate_policy(policy: dict[str, Any]) -> None:
    if policy.get("schema") != POLICY_SCHEMA:
        raise EnrollmentHardeningError("unsupported_policy_schema")
    if set(policy.get("decision", {}).get("allowed_outcomes") or []) != ALLOWED_OUTCOMES:
        raise EnrollmentHardeningError("terminal_outcomes_changed")
    identity = policy.get("identity") or {}
    if identity.get("minimum_similarity") != 0.5 or identity.get("minimum_margin") != 0.3:
        raise EnrollmentHardeningError("frozen_identity_thresholds_changed")
    candidate = policy.get("candidate") or {}
    if candidate.get("id") != "contrastive_reliability_weighted_centroid_v1":
        raise EnrollmentHardeningError("candidate_changed")
    forbidden_reads = ("reads_target_item_embeddings", "reads_target_item_truth", "reads_target_item_outcomes")
    if any(candidate.get(key) is not False for key in forbidden_reads):
        raise EnrollmentHardeningError("candidate_reads_target_evidence")
    if candidate.get("parameter_search_allowed") is not False or candidate.get("post_hoc_tuning_allowed") is not False:
        raise EnrollmentHardeningError("candidate_tuning_enabled")
    safety = policy.get("safety") or {}
    required_false = (
        "production_mutation", "coverage_v3_mutation", "selected_transcript_mutation",
        "raw_audio_mutation", "primary_asr_mutation", "echo_guard_mutation",
        "item_embedding_mutation", "threshold_tuning", "human_name_inference",
        "cross_session_voice_linking", "cloud_allowed", "manual_listening_required",
    )
    if safety.get("shadow_only") is not True or any(safety.get(key) is not False for key in required_false):
        raise EnrollmentHardeningError("unsafe_policy")


def load_policy(path: Path) -> dict[str, Any]:
    policy = read_json(path)
    validate_policy(policy)
    return policy


def source_paths(policy: dict[str, Any]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for row in policy.get("source", {}).get("artifacts") or []:
        role = str(row.get("id") or "")
        if not role or role in result:
            raise EnrollmentHardeningError("duplicate_or_empty_source_id")
        path = resolve(str(row.get("path") or ""))
        actual = fingerprint(path, role)
        if actual["sha256"] != row.get("sha256"):
            raise EnrollmentHardeningError(f"source_hash_mismatch:{role}")
        result[role] = path
    return result


def inherited_rows(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for group in ("frozen_inputs", "clip_files", "exemplar_files", "word_files"):
        for index, row in enumerate(manifest.get(group) or []):
            if isinstance(row, dict):
                rows.append({**row, "_role": f"{group}:{index}"})
    for guard_index, guard in enumerate(manifest.get("production_guards") or []):
        for key in ("selected_dialogue", "v3_manifest", "v3_report"):
            row = guard.get(key) if isinstance(guard, dict) else None
            if isinstance(row, dict):
                rows.append({**row, "_role": f"production_guard:{guard_index}:{key}"})
        for audio_index, row in enumerate((guard.get("raw_audio") or []) if isinstance(guard, dict) else []):
            if isinstance(row, dict):
                rows.append({**row, "_role": f"production_guard:{guard_index}:raw:{audio_index}"})
    return rows


def verify_inherited(manifest: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    verified: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in inherited_rows(manifest):
        path = resolve(str(row.get("path") or ""))
        relative = portable(path)
        if relative in seen:
            continue
        seen.add(relative)
        actual = fingerprint(path, str(row["_role"]))
        if row.get("bytes") is not None and actual["bytes"] != int(row["bytes"]):
            raise EnrollmentHardeningError(f"inherited_size_mismatch:{row['_role']}")
        if actual["sha256"] != str(row.get("sha256") or ""):
            raise EnrollmentHardeningError(f"inherited_hash_mismatch:{row['_role']}")
        verified.append(actual)
    verified.sort(key=lambda row: (str(row["id"]), str(row["path"])))
    return verified, sha256_bytes(canonical_json(verified))


def reference_mapping(reference: dict[str, Any]) -> dict[str, str]:
    rows = [row for row in reference.get("rows") or [] if row.get("predicted_speaker") and row.get("reference_speaker")]
    predicted = sorted({str(row["predicted_speaker"]) for row in rows})
    expected = sorted({str(row["reference_speaker"]) for row in rows})
    if not predicted or len(predicted) > len(expected):
        return {}
    counts = Counter((str(row["predicted_speaker"]), str(row["reference_speaker"])) for row in rows)
    best_score = -1
    best: dict[str, str] = {}
    for permutation in itertools.permutations(expected, len(predicted)):
        mapping = dict(zip(predicted, permutation, strict=True))
        score = sum(counts[(speaker, mapping[speaker])] for speaker in predicted)
        if score > best_score:
            best_score = score
            best = mapping
    return best


def control_centers(groups: dict[tuple[str, str], list[tuple[str, np.ndarray]]]) -> dict[str, dict[str, np.ndarray]]:
    centers: dict[str, dict[str, np.ndarray]] = defaultdict(dict)
    for (session_id, speaker_id), rows in sorted(groups.items()):
        if rows:
            centers[session_id][speaker_id] = normalize(np.mean([vector for _, vector in rows], axis=0))
    return dict(centers)


def classify(vector: np.ndarray, centers: dict[str, np.ndarray], choices: list[str], policy: dict[str, Any]) -> dict[str, Any]:
    eligible = {speaker: centers[speaker] for speaker in choices if speaker in centers}
    if not eligible or set(eligible) != set(choices):
        return {"speaker_id": None, "top_speaker_id": None, "similarity": None, "margin": None, "scores": {}, "reason": "incomplete_enrollment"}
    scores = sorted(((float(vector @ center), speaker) for speaker, center in eligible.items()), reverse=True)
    top_score, top_speaker = scores[0]
    second_score = scores[1][0] if len(scores) > 1 else -1.0
    margin = top_score - second_score
    accepted = top_score >= float(policy["identity"]["minimum_similarity"]) and margin >= float(policy["identity"]["minimum_margin"])
    return {
        "speaker_id": top_speaker if accepted else None,
        "top_speaker_id": top_speaker,
        "similarity": round(top_score, 6),
        "margin": round(margin, 6),
        "scores": {speaker: round(score, 6) for score, speaker in scores},
        "reason": "accepted_centroid" if accepted else "open_set_abstention",
    }


def load_data(policy: dict[str, Any], paths: dict[str, Path]) -> dict[str, Any]:
    shadow_manifest = read_json(paths["shadow_input_manifest"])
    requests_payload = read_json(paths["shadow_embedding_request"])
    embeddings_payload = read_json(paths["shadow_embeddings"])
    shadow_rows = read_jsonl(paths["shadow_item_decisions"])
    word_rows = read_jsonl(paths["shadow_word_decisions"])
    error_rows = read_jsonl(paths["decomposition_items"])
    enrollment_diagnostics = read_jsonl(paths["enrollment_diagnostics"])
    decomposition_report = read_json(paths["decomposition_report"])
    decomposition_replay = read_json(paths["decomposition_replay"])
    shadow_report = read_json(paths["shadow_report"])
    shadow_replay = read_json(paths["shadow_replay"])
    interval_report = read_json(paths["interval_report"])
    interval_replay = read_json(paths["interval_replay"])
    reference = read_json(paths["independent_reference"])

    raw_vectors = {str(row["key"]): np.asarray(row["embedding"], dtype=np.float32) for row in embeddings_payload.get("rows") or []}
    vectors = {key: normalize(vector) for key, vector in raw_vectors.items()}
    embedding_errors = {str(row["key"]): str(row["reason"]) for row in embeddings_payload.get("errors") or []}
    requests = {str(row["key"]): row for row in requests_payload.get("requests") or []}
    groups: dict[tuple[str, str], list[tuple[str, np.ndarray]]] = defaultdict(list)
    for key in sorted(requests):
        pair = parse_enrollment_key(key)
        if pair is not None and key in vectors:
            groups[pair].append((key, vectors[key]))

    expected = policy["source"]
    scope = [row for row in error_rows if row.get("failure_scope") is True and row.get("classification", {}).get("primary_cause") == "enrollment_instability"]
    scope_seconds = round(sum(float(row["coverage_weight_sec"]) for row in scope), 6)
    scope_words = sum(int(row["word_count"]) for row in scope)
    enrollment_keys = [key for key in requests if key.startswith("enroll:")]
    unstable_profiles = sum(row.get("stable") is False for row in enrollment_diagnostics)
    accepted = sum(row.get("shadow", {}).get("speaker_id") is not None for row in shadow_rows)
    checks = {
        "items": len(shadow_rows) == int(expected["expected_items"]),
        "words": len(word_rows) == int(expected["expected_words"]),
        "scope_items": len(scope) == int(expected["expected_enrollment_failure_items"]),
        "scope_words": scope_words == int(expected["expected_enrollment_failure_words"]),
        "scope_seconds": math.isclose(scope_seconds, float(expected["expected_enrollment_failure_seconds"]), abs_tol=1e-6),
        "exemplars": len(enrollment_keys) == int(expected["expected_enrollment_exemplars"]),
        "profiles": len(groups) == int(expected["expected_speaker_profiles"]),
        "unstable_profiles": unstable_profiles == int(expected["expected_unstable_profiles"]),
        "control_accepted": accepted == int(expected["expected_control_accepted_items"]),
        "control_abstentions": len(shadow_rows) - accepted == int(expected["expected_control_abstentions"]),
        "decomposition_replay": decomposition_replay.get("byte_identical") is True,
        "shadow_replay": shadow_replay.get("byte_identical") is True,
        "interval_replay": interval_replay.get("byte_identical") is True,
        "interval_closed": interval_report.get("decision") == "DO_NOT_ADVANCE_INTERVAL_PURIFICATION",
    }
    failures = sorted(key for key, passed in checks.items() if not passed)
    if failures:
        raise EnrollmentHardeningError(f"frozen_scope_changed:{','.join(failures)}")
    if set(vectors) | set(embedding_errors) != set(requests):
        raise EnrollmentHardeningError("embedding_result_coverage_mismatch")
    if len({str(row["item_id"]) for row in shadow_rows}) != len(shadow_rows):
        raise EnrollmentHardeningError("duplicate_item_id")
    if len({(str(row["session_id"]), str(row["word_id"])) for row in word_rows}) != len(word_rows):
        raise EnrollmentHardeningError("duplicate_word_id")
    for row in shadow_rows:
        key = str(row["embedding"]["key"])
        if embedding_digest(raw_vectors.get(key)) != row["embedding"].get("sha256"):
            raise EnrollmentHardeningError(f"item_embedding_digest_mismatch:{row['item_id']}")
    for diagnostic in enrollment_diagnostics:
        pair = (str(diagnostic["session_id"]), str(diagnostic["speaker_id"]))
        for trial in diagnostic.get("trials") or []:
            key = str(trial["embedding_key"])
            if key not in dict(groups.get(pair, [])) or embedding_digest(vectors.get(key)) != trial.get("embedding_sha256"):
                raise EnrollmentHardeningError(f"enrollment_embedding_digest_mismatch:{key}")

    centers = control_centers(dict(groups))
    for row in shadow_rows:
        key = str(row["embedding"]["key"])
        vector = vectors.get(key)
        if vector is None:
            reproduced = {"speaker_id": None, "top_speaker_id": None, "similarity": None, "margin": None, "reason": "embedding_unavailable"}
        else:
            reproduced = classify(vector, centers.get(str(row["session_id"]), {}), [str(value) for value in row["speaker_choices"]], policy)
        original = row["shadow"]
        if reproduced.get("speaker_id") != original.get("speaker_id") or reproduced.get("top_speaker_id") != original.get("top_speaker_id"):
            raise EnrollmentHardeningError(f"control_decision_not_reproduced:{row['item_id']}")
        for metric in ("similarity", "margin"):
            if reproduced.get(metric) is None and original.get(metric) is None:
                continue
            if abs(float(reproduced[metric]) - float(original[metric])) > 1e-5:
                raise EnrollmentHardeningError(f"control_score_not_reproduced:{row['item_id']}:{metric}")

    return {
        "shadow_manifest": shadow_manifest,
        "shadow_rows": sorted(shadow_rows, key=lambda row: (str(row["session_id"]), float(row["start"]), str(row["item_id"]))),
        "word_rows": word_rows,
        "error_rows": error_rows,
        "scope": sorted(scope, key=lambda row: (str(row["session_id"]), float(row["start"]), str(row["item_id"]))),
        "scope_ids": {str(row["item_id"]) for row in scope},
        "scope_seconds": scope_seconds,
        "scope_words": scope_words,
        "vectors": vectors,
        "raw_vectors": raw_vectors,
        "embedding_errors": embedding_errors,
        "groups": dict(groups),
        "control_centers": centers,
        "diagnostics": enrollment_diagnostics,
        "reference_mapping": reference_mapping(reference),
        "production_guards": shadow_manifest.get("production_guards") or [],
        "shadow_report": shadow_report,
    }


def manifests(policy_path: Path, policy: dict[str, Any], paths: dict[str, Path], data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    source_artifacts = [fingerprint(path, role) for role, path in sorted(paths.items())]
    inherited, inherited_fingerprint = verify_inherited(data["shadow_manifest"])
    production_rows = [row for row in inherited if str(row["id"]).startswith("production_guard:")]
    source_fingerprint = sha256_bytes(canonical_json({
        "policy": fingerprint(policy_path, "policy"),
        "source_artifacts": source_artifacts,
        "inherited_artifact_fingerprint": inherited_fingerprint,
        "candidate": policy["candidate"],
    }))
    counts = {
        "items": len(data["shadow_rows"]),
        "words": len(data["word_rows"]),
        "enrollment_failure_items": len(data["scope"]),
        "enrollment_failure_words": data["scope_words"],
        "enrollment_failure_seconds": data["scope_seconds"],
        "enrollment_exemplars": sum(len(rows) for rows in data["groups"].values()),
        "speaker_profiles": len(data["groups"]),
        "unstable_profiles": sum(row.get("stable") is False for row in data["diagnostics"]),
    }
    private = {
        "schema": PRIVATE_INPUT_SCHEMA,
        "version": VERSION,
        "source_fingerprint": source_fingerprint,
        "policy": fingerprint(policy_path, "policy"),
        "source_artifacts": source_artifacts,
        "inherited_artifacts": inherited,
        "inherited_artifact_fingerprint": inherited_fingerprint,
        "production_guard_fingerprint": sha256_bytes(canonical_json(production_rows)),
        "counts": counts,
        "candidate": policy["candidate"],
        "identity": policy["identity"],
    }
    public = {
        "schema": PUBLIC_INPUT_SCHEMA,
        "version": VERSION,
        "source_fingerprint": source_fingerprint,
        "counts": counts,
        "candidate_id": policy["candidate"]["id"],
        "source_artifact_sha256": {row["id"]: row["sha256"] for row in source_artifacts},
        "inherited_artifact_fingerprint": inherited_fingerprint,
        "production_guard_fingerprint": private["production_guard_fingerprint"],
        "private_values_excluded": True,
    }
    return private, public


def verify_frozen(out: Path, policy_path: Path, policy: dict[str, Any], paths: dict[str, Path], data: dict[str, Any]) -> dict[str, Any]:
    private, public = manifests(policy_path, policy, paths, data)
    private_path = out / "private/input_manifest.json"
    public_path = out / "input_manifest.public.json"
    if not private_path.is_file() or private_path.read_bytes() != pretty_json(private):
        raise EnrollmentHardeningError("private_frozen_manifest_missing_or_changed")
    if not public_path.is_file() or public_path.read_bytes() != pretty_json(public):
        raise EnrollmentHardeningError("public_frozen_manifest_missing_or_changed")
    return private


def build_candidate_centroids(data: dict[str, Any], policy: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, dict[str, np.ndarray]], dict[str, Any]]:
    config = policy["candidate"]
    control = data["control_centers"]
    profile_rows: list[dict[str, Any]] = []
    candidate_centers: dict[str, dict[str, np.ndarray]] = defaultdict(dict)
    centroid_payload: dict[str, Any] = {}
    for (session_id, speaker_id), rows in sorted(data["groups"].items()):
        if len(rows) < int(config["minimum_exemplars_per_profile"]):
            profile_rows.append({
                "schema": PROFILE_SCHEMA, "session_id": session_id, "speaker_id": speaker_id,
                "status": "unavailable", "reason": "insufficient_exemplars", "exemplars": [],
            })
            continue
        exemplar_rows = []
        raw_weights = []
        for key, vector in rows:
            others = [candidate for candidate_key, candidate in rows if candidate_key != key]
            if not others:
                raise EnrollmentHardeningError(f"candidate_requires_loo_peer:{key}")
            same_center = normalize(np.mean(others, axis=0))
            same_similarity = float(vector @ same_center)
            impostors = [
                (float(vector @ center), other_speaker)
                for other_speaker, center in control.get(session_id, {}).items()
                if other_speaker != speaker_id
            ]
            impostors.sort(reverse=True)
            nearest_impostor_similarity, nearest_impostor = impostors[0] if impostors else (-1.0, None)
            margin = same_similarity - nearest_impostor_similarity
            loo_passed = (
                same_similarity >= float(config["loo_same_speaker_threshold"])
                and margin >= float(config["loo_impostor_margin_threshold"])
            )
            cohesion = max(float(config["cohesion_floor"]), same_similarity)
            separation = max(float(config["separation_floor"]), margin)
            raw_weight = cohesion * separation * (float(config["loo_pass_weight_multiplier"]) if loo_passed else 1.0)
            raw_weights.append(raw_weight)
            exemplar_rows.append({
                "embedding_key": key,
                "embedding_sha256": embedding_digest(vector),
                "same_speaker_similarity": round(same_similarity, 6),
                "nearest_impostor_similarity": round(nearest_impostor_similarity, 6),
                "nearest_impostor_speaker_id": nearest_impostor,
                "impostor_margin": round(margin, 6),
                "loo_passed": loo_passed,
                "raw_weight": round(raw_weight, 9),
            })
        total = sum(raw_weights)
        fallback = total <= 1e-12
        weights = ([1.0 / len(rows)] * len(rows)) if fallback else [value / total for value in raw_weights]
        centroid = normalize(np.sum([weight * vector for weight, (_, vector) in zip(weights, rows, strict=True)], axis=0))
        control_center = control[session_id][speaker_id]
        for exemplar, weight in zip(exemplar_rows, weights, strict=True):
            exemplar["normalized_weight"] = round(weight, 9)
        candidate_centers[session_id][speaker_id] = centroid
        profile_key = f"{session_id}:{speaker_id}"
        centroid_payload[profile_key] = {
            "embedding": [float(value) for value in centroid],
            "embedding_sha256": embedding_digest(centroid),
        }
        profile_rows.append({
            "schema": PROFILE_SCHEMA,
            "session_id": session_id,
            "speaker_id": speaker_id,
            "status": "control_fallback" if fallback else "weighted_candidate",
            "reason": "no_positive_reliability_weight" if fallback else "contrastive_reliability_weighted",
            "exemplar_count": len(rows),
            "exemplars": exemplar_rows,
            "control_centroid_sha256": embedding_digest(control_center),
            "candidate_centroid_sha256": embedding_digest(centroid),
            "centroid_changed": embedding_digest(control_center) != embedding_digest(centroid),
            "target_item_evidence_read": False,
        })
    payload = {
        "schema": CENTROID_SCHEMA,
        "version": VERSION,
        "candidate_id": config["id"],
        "profiles": centroid_payload,
        "target_item_evidence_read": False,
    }
    return profile_rows, dict(candidate_centers), payload


def load_candidate_centroids(out: Path, data: dict[str, Any], policy: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, dict[str, np.ndarray]]]:
    expected_rows, _expected_centers, expected_payload = build_candidate_centroids(data, policy)
    rows_path = out / "private/enrollment_candidate.jsonl"
    payload_path = out / "private/candidate_centroids.json"
    if not rows_path.is_file() or rows_path.read_bytes() != jsonl_bytes(expected_rows):
        raise EnrollmentHardeningError("candidate_profile_provenance_missing_or_changed")
    if not payload_path.is_file() or payload_path.read_bytes() != pretty_json(expected_payload):
        raise EnrollmentHardeningError("candidate_centroids_missing_or_changed")
    centers: dict[str, dict[str, np.ndarray]] = defaultdict(dict)
    for profile_key, row in expected_payload["profiles"].items():
        session_id, speaker_id = profile_key.split(":", 1)
        vector = np.asarray(row["embedding"], dtype=np.float32)
        norm = float(np.linalg.norm(vector))
        if not math.isfinite(norm) or not math.isclose(norm, 1.0, abs_tol=1e-5):
            raise EnrollmentHardeningError(f"candidate_centroid_not_normalized:{profile_key}")
        if embedding_digest(vector) != row["embedding_sha256"]:
            raise EnrollmentHardeningError(f"candidate_centroid_digest_mismatch:{profile_key}")
        centers[session_id][speaker_id] = vector
    return expected_rows, dict(centers)


def result_reference(row: dict[str, Any], speaker_id: str | None, data: dict[str, Any]) -> dict[str, Any]:
    truth = row.get("truth") or {}
    grade = str(truth.get("grade") or "unknown")
    outcome = truth.get("outcome")
    correct: bool | None = None
    if speaker_id is not None and outcome is not None:
        if grade == "independent_machine_reference":
            mapped = data["reference_mapping"].get(speaker_id)
            correct = mapped == str(outcome) if mapped else None
        elif str(outcome) in {"unknown_speaker", "mixed", "unusable"}:
            correct = False
        else:
            correct = speaker_id == str(outcome)
    return {
        "grade": grade,
        "outcome_available": outcome is not None,
        "evaluated": correct is not None,
        "correct": correct,
        "open_set_negative": outcome in {"unknown_speaker", "mixed", "unusable"},
    }


def item_comparisons(data: dict[str, Any], centers: dict[str, dict[str, np.ndarray]], policy: dict[str, Any], profile_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    profile_hashes = {
        (str(row["session_id"]), str(row["speaker_id"])): row.get("candidate_centroid_sha256")
        for row in profile_rows if row.get("candidate_centroid_sha256")
    }
    rows = []
    for control in data["shadow_rows"]:
        key = str(control["embedding"]["key"])
        vector = data["vectors"].get(key)
        if vector is None:
            candidate = {"speaker_id": None, "top_speaker_id": None, "similarity": None, "margin": None, "scores": {}, "reason": "embedding_unavailable"}
        else:
            candidate = classify(vector, centers.get(str(control["session_id"]), {}), [str(value) for value in control["speaker_choices"]], policy)
        control_result = dict(control["shadow"])
        control_speaker = control_result.get("speaker_id")
        candidate_speaker = candidate.get("speaker_id")
        change = (
            "newly_accepted" if control_speaker is None and candidate_speaker is not None
            else "removed_acceptance" if control_speaker is not None and candidate_speaker is None
            else "changed_speaker" if control_speaker and candidate_speaker and control_speaker != candidate_speaker
            else "unchanged"
        )
        rows.append({
            "schema": ITEM_SCHEMA,
            "session_id": control["session_id"],
            "item_id": control["item_id"],
            "utterance_id": control["utterance_id"],
            "start": control["start"],
            "end": control["end"],
            "word_ids": list(control["word_ids"]),
            "word_count": int(control["word_count"]),
            "coverage_weight_sec": float(control["coverage_weight_sec"]),
            "in_enrollment_scope": str(control["item_id"]) in data["scope_ids"],
            "item_embedding_key": key,
            "item_embedding_sha256": control["embedding"].get("sha256"),
            "control": control_result,
            "candidate": candidate,
            "change": change,
            "control_reference": result_reference(control, control_speaker, data),
            "candidate_reference": result_reference(control, candidate_speaker, data),
            "candidate_profile_sha256": {
                speaker: profile_hashes.get((str(control["session_id"]), str(speaker)))
                for speaker in control["speaker_choices"]
            },
            "provenance": {
                "candidate_id": policy["candidate"]["id"],
                "minimum_similarity": policy["identity"]["minimum_similarity"],
                "minimum_margin": policy["identity"]["minimum_margin"],
                "item_embedding_recomputed": False,
                "target_item_used_for_enrollment": False,
                "threshold_tuned": False,
                "production_applied": False,
            },
        })
    return rows


def accepted_metrics(rows: list[dict[str, Any]], side: str, *, scope_only: bool = False) -> dict[str, Any]:
    selected = [row for row in rows if (not scope_only or row["in_enrollment_scope"]) and row[side].get("speaker_id") is not None]
    return {
        "items": len(selected),
        "words": sum(int(row["word_count"]) for row in selected),
        "seconds": round(sum(float(row["coverage_weight_sec"]) for row in selected), 6),
    }


def evidence_metrics(rows: list[dict[str, Any]], side: str) -> dict[str, Any]:
    result = {}
    for grade in ("structural_one_to_one", "independent_machine_reference"):
        selected = [
            row for row in rows
            if row[f"{side}_reference"]["grade"] == grade
            and row[f"{side}_reference"]["evaluated"]
            and row[side].get("speaker_id") is not None
        ]
        words = sum(int(row["word_count"]) for row in selected)
        correct = sum(int(row["word_count"]) for row in selected if row[f"{side}_reference"]["correct"] is True)
        result[grade] = {
            "evaluated_items": len(selected), "evaluated_words": words, "correct_words": correct,
            "incorrect_words": words - correct, "precision": round(correct / words, 6) if words else None,
        }
    negatives = [row for row in rows if row[f"{side}_reference"]["open_set_negative"]]
    result["open_set"] = {
        "reference_items": len(negatives),
        "false_accept_items": sum(row[side].get("speaker_id") is not None for row in negatives),
        "abstained_items": sum(row[side].get("speaker_id") is None for row in negatives),
    }
    return result


def word_keys(row: dict[str, Any]) -> set[str]:
    return {f"{row['session_id']}\x1f{word_id}" for word_id in row["word_ids"]}


def exact_conservation(rows: list[dict[str, Any]], data: dict[str, Any]) -> bool:
    if len(rows) != len(data["shadow_rows"]):
        return False
    controls = {str(row["item_id"]): row for row in data["shadow_rows"]}
    seen: set[str] = set()
    for row in rows:
        control = controls.get(str(row["item_id"]))
        if control is None or any(row[key] != control[key] for key in ("session_id", "start", "end", "word_ids")):
            return False
        keys = word_keys(row)
        if seen.intersection(keys):
            return False
        seen.update(keys)
    return len(seen) == 851


def precision_not_worse(control: float | None, candidate: float | None) -> bool:
    if control is None:
        return True
    return candidate is not None and candidate >= control


def build_report(data: dict[str, Any], profile_rows: list[dict[str, Any]], rows: list[dict[str, Any]], policy: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    control = accepted_metrics(rows, "control")
    candidate = accepted_metrics(rows, "candidate")
    control_scope = accepted_metrics(rows, "control", scope_only=True)
    candidate_scope = accepted_metrics(rows, "candidate", scope_only=True)
    newly = [row for row in rows if row["change"] == "newly_accepted"]
    removed = [row for row in rows if row["change"] == "removed_acceptance"]
    changed = [row for row in rows if row["change"] == "changed_speaker"]
    control_evidence = evidence_metrics(rows, "control")
    candidate_evidence = evidence_metrics(rows, "candidate")
    control_errors = set().union(*(word_keys(row) for row in rows if row["control_reference"]["evaluated"] and row["control_reference"]["correct"] is False)) if rows else set()
    candidate_errors = set().union(*(word_keys(row) for row in rows if row["candidate_reference"]["evaluated"] and row["candidate_reference"]["correct"] is False)) if rows else set()
    changed_profiles = sum(row.get("centroid_changed") is True for row in profile_rows)
    fallback_profiles = sum(row.get("status") == "control_fallback" for row in profile_rows)
    unavailable_profiles = sum(row.get("status") == "unavailable" for row in profile_rows)
    new_words = sum(int(row["word_count"]) for row in newly)
    new_seconds = round(sum(float(row["coverage_weight_sec"]) for row in newly), 6)
    scope_item_gain = candidate_scope["items"] - control_scope["items"]
    scope_seconds_gain = round(candidate_scope["seconds"] - control_scope["seconds"], 6)
    decision_policy = policy["decision"]
    structural_control = control_evidence["structural_one_to_one"]["precision"]
    structural_candidate = candidate_evidence["structural_one_to_one"]["precision"]
    independent_control = control_evidence["independent_machine_reference"]["precision"]
    independent_candidate = candidate_evidence["independent_machine_reference"]["precision"]
    gates = {
        "minimum_changed_profiles": changed_profiles >= int(decision_policy["minimum_changed_profiles"]),
        "minimum_newly_accepted_items": len(newly) >= int(decision_policy["minimum_newly_accepted_items"]),
        "minimum_newly_accepted_words": new_words >= int(decision_policy["minimum_newly_accepted_words"]),
        "minimum_newly_accepted_seconds": new_seconds >= float(decision_policy["minimum_newly_accepted_seconds"]),
        "minimum_enrollment_scope_item_gain_ratio": scope_item_gain / max(1, len(data["scope"])) >= float(decision_policy["minimum_enrollment_scope_item_gain_ratio"]),
        "minimum_enrollment_scope_seconds_gain_ratio": scope_seconds_gain / max(1e-9, data["scope_seconds"]) >= float(decision_policy["minimum_enrollment_scope_seconds_gain_ratio"]),
        "no_removed_control_acceptance": not removed,
        "no_changed_accepted_speaker": not changed,
        "structural_precision_no_regression": precision_not_worse(structural_control, structural_candidate),
        "independent_precision_no_regression": precision_not_worse(independent_control, independent_candidate),
        "open_set_false_accepts_no_regression": candidate_evidence["open_set"]["false_accept_items"] <= control_evidence["open_set"]["false_accept_items"],
        "no_new_reference_errors": candidate_errors.issubset(control_errors),
        "silent_fail_open": all(row["candidate"].get("speaker_id") is None for row in rows if row["item_embedding_key"] in data["embedding_errors"]),
        "exact_word_and_timestamp_conservation": exact_conservation(rows, data),
        "existing_coverage_labels_unchanged": all(row.get("baseline", {}).get("speaker_id") is None for row in data["shadow_rows"]),
        "boundary_and_chronology_no_regression": all(float(row["end"]) >= float(row["start"]) for row in rows),
        "frozen_control_reproduced": control["items"] == int(policy["source"]["expected_control_accepted_items"]),
        "production_guards_unchanged": bool(data["production_guards"]),
    }
    invariants = {
        "all_items_accounted_once": len({row["item_id"] for row in rows}) == len(rows) == 278,
        "all_words_accounted_once": sum(int(row["word_count"]) for row in rows) == 851,
        "enrollment_scope_frozen": sum(row["in_enrollment_scope"] for row in rows) == 83,
        "all_exemplars_accounted_once": sum(int(row.get("exemplar_count") or 0) for row in profile_rows) == 28,
        "all_profiles_accounted_once": len(profile_rows) == 14,
        "candidate_uses_enrollment_only": all(row.get("target_item_evidence_read") is False for row in profile_rows),
        "item_embeddings_unchanged": all(row["item_embedding_sha256"] == next(control["embedding"]["sha256"] for control in data["shadow_rows"] if control["item_id"] == row["item_id"]) for row in rows),
        "thresholds_unchanged": True,
        "candidate_is_shadow_only": True,
    }
    if not all(invariants.values()):
        decision = "EVIDENCE_BOUND"
    else:
        decision = "ADVANCE_HARDENED_ENROLLMENT_SHADOW" if all(gates.values()) else "DO_NOT_ADVANCE_ENROLLMENT_HARDENING"
    return {
        "schema": REPORT_SCHEMA,
        "version": VERSION,
        "decision": decision,
        "source_fingerprint": manifest["source_fingerprint"],
        "scope": {
            "items": len(rows), "words": sum(int(row["word_count"]) for row in rows),
            "enrollment_failure_items": len(data["scope"]), "enrollment_failure_words": data["scope_words"],
            "enrollment_failure_seconds": data["scope_seconds"], "enrollment_exemplars": 28,
            "speaker_profiles": len(profile_rows), "guarded_sessions": len(data["production_guards"]),
        },
        "candidate": {
            "id": policy["candidate"]["id"], "changed_profiles": changed_profiles,
            "fallback_profiles": fallback_profiles, "unavailable_profiles": unavailable_profiles,
            "parameter_search_used": False, "post_hoc_tuning_used": False,
            "target_item_evidence_read": False,
        },
        "comparison": {
            "control": control, "candidate": candidate,
            "control_enrollment_scope": control_scope, "candidate_enrollment_scope": candidate_scope,
            "newly_accepted_items": len(newly), "newly_accepted_words": new_words,
            "newly_accepted_seconds": new_seconds, "removed_control_acceptances": len(removed),
            "changed_accepted_speakers": len(changed), "enrollment_scope_item_gain": scope_item_gain,
            "enrollment_scope_seconds_gain": scope_seconds_gain,
            "control_evidence": control_evidence, "candidate_evidence": candidate_evidence,
            "control_reference_error_words": len(control_errors), "candidate_reference_error_words": len(candidate_errors),
            "new_reference_error_words": len(candidate_errors - control_errors),
        },
        "gates": gates,
        "invariants": invariants,
        "safety": {
            "production_mutated": False, "coverage_v3_mutated": False, "selected_transcript_mutated": False,
            "raw_audio_mutated": False, "primary_asr_mutated": False, "echo_guard_mutated": False,
            "item_embeddings_mutated": False, "thresholds_tuned": False, "human_names_inferred": False,
            "cross_session_voice_linking": False, "private_values_excluded": True, "shadow_only": True,
        },
        "next_action": (
            "qualify_hardened_enrollment_shadow_candidate"
            if decision == "ADVANCE_HARDENED_ENROLLMENT_SHADOW"
            else "close_enrollment_weighting_and_advance_identity_backend_or_direct_truth"
        ),
    }


def markdown_report(report: dict[str, Any]) -> bytes:
    comparison = report["comparison"]
    lines = [
        "# Session-Local Remote Speaker Enrollment Hardening v1", "",
        f"Decision: `{report['decision']}`", "", "## Scope", "",
        f"- Frozen items: {report['scope']['items']} / {report['scope']['words']} words",
        f"- Enrollment failures: {report['scope']['enrollment_failure_items']} / {report['scope']['enrollment_failure_seconds']:.6f}s",
        f"- Enrollment: {report['scope']['enrollment_exemplars']} exemplars / {report['scope']['speaker_profiles']} profiles",
        "", "## Comparison", "",
        f"- Control accepted: {comparison['control']['items']} items / {comparison['control']['words']} words / {comparison['control']['seconds']:.6f}s",
        f"- Candidate accepted: {comparison['candidate']['items']} items / {comparison['candidate']['words']} words / {comparison['candidate']['seconds']:.6f}s",
        f"- Newly accepted: {comparison['newly_accepted_items']} items / {comparison['newly_accepted_words']} words / {comparison['newly_accepted_seconds']:.6f}s",
        f"- Removed control accepts: {comparison['removed_control_acceptances']}",
        f"- Changed accepted speakers: {comparison['changed_accepted_speakers']}",
        f"- New reference-error words: {comparison['new_reference_error_words']}",
        "", "## Evidence", "",
        f"- Structural precision: {comparison['control_evidence']['structural_one_to_one']['precision']} -> {comparison['candidate_evidence']['structural_one_to_one']['precision']}",
        f"- Independent precision: {comparison['control_evidence']['independent_machine_reference']['precision']} -> {comparison['candidate_evidence']['independent_machine_reference']['precision']}",
        "", "## Gates", "",
    ]
    lines.extend(f"- {'PASS' if passed else 'FAIL'} `{name}`" for name, passed in report["gates"].items())
    lines.extend([
        "", "## Boundary", "",
        "The candidate is diagnostic and shadow-only. Coverage v3, selected transcripts, raw audio,",
        "item embeddings, ECAPA thresholds and production output remain unchanged.",
    ])
    return ("\n".join(lines) + "\n").encode()


def output_payloads(profile_rows: list[dict[str, Any]], centroid_payload: dict[str, Any], rows: list[dict[str, Any]], report: dict[str, Any]) -> dict[str, bytes]:
    return {
        "private/enrollment_candidate.jsonl": jsonl_bytes(profile_rows),
        "private/candidate_centroids.json": pretty_json(centroid_payload),
        "private/item_comparison.jsonl": jsonl_bytes(rows),
        "session_local_remote_speaker_enrollment_hardening_report.json": pretty_json(report),
        "session_local_remote_speaker_enrollment_hardening_report.md": markdown_report(report),
    }


def replay(out: Path, data: dict[str, Any], policy: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    profile_rows, centers, centroid_payload = build_candidate_centroids(data, policy)
    rows = item_comparisons(data, centers, policy, profile_rows)
    report = build_report(data, profile_rows, rows, policy, manifest)
    expected = output_payloads(profile_rows, centroid_payload, rows, report)
    mismatches = [name for name, payload in expected.items() if not (out / name).is_file() or (out / name).read_bytes() != payload]
    if mismatches:
        raise EnrollmentHardeningError(f"deterministic_replay_mismatch:{','.join(mismatches)}")
    result = {
        "schema": REPLAY_SCHEMA, "version": VERSION, "source_fingerprint": manifest["source_fingerprint"],
        "byte_identical": True, "verified_outputs": {name: sha256(out / name) for name in sorted(expected)},
        "production_mutated": False,
    }
    write_json(out / "replay_report.json", result)
    return result


def tracked_manifest(policy_path: Path, out: Path) -> dict[str, Any]:
    report = read_json(out / "session_local_remote_speaker_enrollment_hardening_report.json")
    replay_row = read_json(out / "replay_report.json")
    artifact_paths = {
        "policy": policy_path, "public_input_manifest": out / "input_manifest.public.json",
        "report": out / "session_local_remote_speaker_enrollment_hardening_report.json",
        "markdown_report": out / "session_local_remote_speaker_enrollment_hardening_report.md",
        "replay_report": out / "replay_report.json",
    }
    artifacts = {
        key: {"id": key, "path": portable(path), "bytes": path.stat().st_size, "sha256": sha256(path)}
        for key, path in artifact_paths.items()
    }
    return {
        "schema": TRACKED_SCHEMA, "version": VERSION, "decision": report["decision"],
        "source_fingerprint": report["source_fingerprint"], "scope": report["scope"],
        "candidate": report["candidate"], "comparison": report["comparison"], "gates": report["gates"],
        "invariants": report["invariants"], "safety": report["safety"],
        "replay_verified": replay_row.get("byte_identical") is True,
        "private_values_excluded": True, "artifacts": artifacts,
    }


def status(out: Path) -> dict[str, Any]:
    report_path = out / "session_local_remote_speaker_enrollment_hardening_report.json"
    report = read_json(report_path) if report_path.is_file() else {}
    return {
        "schema": "murmurmark.session_local_remote_speaker_enrollment_hardening_status/v1",
        "frozen": (out / "private/input_manifest.json").is_file(),
        "centroids_built": (out / "private/candidate_centroids.json").is_file(),
        "evaluated": bool(report), "replayed": (out / "replay_report.json").is_file(),
        "decision": report.get("decision"), "next_action": report.get("next_action"),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("preflight", "freeze", "build", "evaluate", "status", "replay", "finalize", "all"))
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--write-manifest", type=Path, default=DEFAULT_TRACKED)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    policy_path = args.policy.expanduser().resolve()
    out = args.out_dir.expanduser().resolve()
    tracked = args.write_manifest.expanduser().resolve()
    policy = load_policy(policy_path)
    if args.action == "status":
        print(json.dumps(status(out), ensure_ascii=False, sort_keys=True))
        return 0
    paths = source_paths(policy)
    data = load_data(policy, paths)
    if args.action == "preflight":
        print(json.dumps({"status": "ready", "candidate": policy["candidate"]["id"], "scope_items": len(data["scope"]), "offline": True}, ensure_ascii=False, sort_keys=True))
        return 0
    if args.action in {"freeze", "all"}:
        private, public = manifests(policy_path, policy, paths, data)
        write_json(out / "private/input_manifest.json", private)
        write_json(out / "input_manifest.public.json", public)
    manifest = verify_frozen(out, policy_path, policy, paths, data)
    if args.action in {"build", "all"}:
        profile_rows, _centers, centroid_payload = build_candidate_centroids(data, policy)
        write_jsonl(out / "private/enrollment_candidate.jsonl", profile_rows)
        write_json(out / "private/candidate_centroids.json", centroid_payload)
        print(f"profiles: {len(profile_rows)}; changed: {sum(row.get('centroid_changed') is True for row in profile_rows)}")
    if args.action in {"evaluate", "all"}:
        profile_rows, centers = load_candidate_centroids(out, data, policy)
        _expected_rows, _expected_centers, centroid_payload = build_candidate_centroids(data, policy)
        rows = item_comparisons(data, centers, policy, profile_rows)
        report = build_report(data, profile_rows, rows, policy, manifest)
        for name, payload in output_payloads(profile_rows, centroid_payload, rows, report).items():
            atomic_write(out / name, payload)
        print(f"decision: {report['decision']}")
        print(f"newly_accepted: {report['comparison']['newly_accepted_items']} items / {report['comparison']['newly_accepted_seconds']:.6f}s")
    if args.action in {"replay", "all"}:
        replay(out, data, policy, manifest)
    if args.action in {"finalize", "all"}:
        if args.action == "finalize":
            replay(out, data, policy, manifest)
        write_json(tracked, tracked_manifest(policy_path, out))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except EnrollmentHardeningError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2) from error
