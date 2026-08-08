#!/usr/bin/env python3
"""Evaluate one frozen word-bounded ECAPA interval candidate without production promotion."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import importlib.util
import itertools
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any, Iterable

import numpy as np
import soundfile as sf


ROOT = Path(__file__).resolve().parents[1]
POLICY_SCHEMA = "murmurmark.bounded_remote_speaker_interval_purification_policy/v1"
PRIVATE_MANIFEST_SCHEMA = "murmurmark.bounded_remote_speaker_interval_purification_input/v1"
PUBLIC_MANIFEST_SCHEMA = "murmurmark.bounded_remote_speaker_interval_purification_public_input/v1"
INTERVAL_SCHEMA = "murmurmark.bounded_remote_speaker_interval_candidate/v1"
ITEM_SCHEMA = "murmurmark.bounded_remote_speaker_interval_comparison/v1"
REPORT_SCHEMA = "murmurmark.bounded_remote_speaker_interval_purification_report/v1"
REPLAY_SCHEMA = "murmurmark.bounded_remote_speaker_interval_purification_replay/v1"
TRACKED_SCHEMA = "murmurmark.bounded_remote_speaker_interval_purification_manifest/v1"
VERSION = "0.1.0"
DEFAULT_POLICY = ROOT / "policies/bounded-remote-speaker-interval-purification-v1.json"
DEFAULT_OUT = ROOT / "sessions/_reports/bounded-remote-speaker-interval-purification-v1"
DEFAULT_TRACKED = ROOT / "docs/testing/bounded-remote-speaker-interval-purification-v1-manifest.json"
SHADOW_SCRIPT = ROOT / "scripts/qualify-ecapa-remote-speaker-shadow-v1.py"
ALLOWED_OUTCOMES = {
    "ADVANCE_PURIFIED_SHADOW_CANDIDATE",
    "DO_NOT_ADVANCE_INTERVAL_PURIFICATION",
    "EVIDENCE_BOUND",
}


class PurificationError(RuntimeError):
    pass


def load_shadow_module() -> Any:
    spec = importlib.util.spec_from_file_location("ecapa_shadow_v1", SHADOW_SCRIPT)
    if spec is None or spec.loader is None:
        raise PurificationError("shadow_implementation_unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SHADOW = load_shadow_module()


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
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def write_json(path: Path, value: Any) -> None:
    atomic_write(path, pretty_json(value))


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    atomic_write(path, jsonl_bytes(rows))


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PurificationError(f"invalid_json:{portable(path)}") from error
    if not isinstance(value, dict):
        raise PurificationError(f"json_object_required:{portable(path)}")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError("row is not an object")
                rows.append(value)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        raise PurificationError(f"invalid_jsonl:{portable(path)}") from error
    return rows


def resolve(value: str | Path) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (ROOT / path).resolve()


def portable(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve()))
    except ValueError:
        return f"external/{path.name}"


def load_policy(path: Path) -> dict[str, Any]:
    policy = read_json(path)
    if policy.get("schema") != POLICY_SCHEMA:
        raise PurificationError("policy_schema_invalid")
    candidate = policy.get("candidate") or {}
    if candidate.get("id") != "word_span_guard_80ms_v1":
        raise PurificationError("candidate_changed")
    exact_candidate = {
        "guard_sec": 0.08,
        "known_context_separation_sec": 0.02,
        "minimum_unpadded_span_sec": 0.4,
        "minimum_audio_sec_for_ecapa": 1.0,
        "parameter_search_allowed": False,
        "post_hoc_tuning_allowed": False,
    }
    if any(candidate.get(key) != value for key, value in exact_candidate.items()):
        raise PurificationError("candidate_parameters_changed")
    identity = policy.get("identity") or {}
    if identity.get("minimum_similarity") != 0.5 or identity.get("minimum_margin") != 0.3:
        raise PurificationError("frozen_thresholds_changed")
    if set((policy.get("decision") or {}).get("allowed_outcomes") or []) != ALLOWED_OUTCOMES:
        raise PurificationError("terminal_outcomes_changed")
    safety = policy.get("safety") or {}
    required_false = (
        "production_mutation", "coverage_v3_mutation", "selected_transcript_mutation",
        "raw_audio_mutation", "primary_asr_mutation", "echo_guard_mutation",
        "enrollment_mutation", "threshold_tuning", "human_name_inference",
        "cross_session_voice_linking", "cloud_allowed", "manual_listening_required",
    )
    if safety.get("shadow_only") is not True or any(safety.get(key) is not False for key in required_false):
        raise PurificationError("safety_contract_changed")
    return policy


def source_paths(policy: dict[str, Any]) -> dict[str, Path]:
    paths = {}
    for row in policy["source"]["artifacts"]:
        path = resolve(str(row["path"]))
        if not path.is_file():
            raise PurificationError(f"required_artifact_missing:{row['id']}")
        if sha256(path) != row["sha256"]:
            raise PurificationError(f"required_artifact_changed:{row['id']}")
        paths[str(row["id"])] = path
    return paths


def normalized(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    if not np.isfinite(norm) or norm <= 0:
        raise PurificationError("invalid_embedding")
    return np.asarray(vector / norm, dtype=np.float32)


def embedding_digest(vector: np.ndarray) -> str:
    return sha256_bytes(np.asarray(vector, dtype="<f4").tobytes())


def load_vectors(path: Path) -> tuple[dict[str, np.ndarray], dict[str, str]]:
    payload = read_json(path)
    vectors = {
        str(row["key"]): normalized(np.asarray(row["embedding"], dtype=np.float32))
        for row in payload.get("rows") or []
    }
    errors = {str(row["key"]): str(row["reason"]) for row in payload.get("errors") or []}
    return vectors, errors


def session_from_word_path(path: Path) -> str:
    parts = path.parts
    try:
        return parts[parts.index("sessions") + 1]
    except (ValueError, IndexError) as error:
        raise PurificationError(f"session_path_invalid:{portable(path)}") from error


def verify_production_guards(shadow_manifest: dict[str, Any]) -> list[dict[str, Any]]:
    verified = []
    for session in shadow_manifest.get("production_guards") or []:
        rows = list(session.get("raw_audio") or [])
        rows.extend([session["selected_dialogue"], session["v3_manifest"], session["v3_report"]])
        for row in rows:
            path = resolve(str(row["path"]))
            if not path.is_file() or path.stat().st_size != int(row["bytes"]) or sha256(path) != row["sha256"]:
                raise PurificationError("production_guard_changed")
        verified.append({"session_id": str(session["session_id"]), "artifact_count": len(rows)})
    return verified


def load_data(policy: dict[str, Any], paths: dict[str, Path]) -> dict[str, Any]:
    decomposition = read_json(paths["decomposition_report"])
    if decomposition.get("decision") != "ADVANCE_INTERVAL_PURIFICATION":
        raise PurificationError("decomposition_decision_changed")
    error_rows = read_jsonl(paths["decomposition_items"])
    shadow_rows = read_jsonl(paths["shadow_item_decisions"])
    shadow_by_id = {str(row["item_id"]): row for row in shadow_rows}
    if len(shadow_by_id) != len(shadow_rows):
        raise PurificationError("duplicate_shadow_item")
    if {str(row["item_id"]) for row in error_rows} != set(shadow_by_id):
        raise PurificationError("decomposition_shadow_item_mismatch")

    shadow_manifest = read_json(paths["shadow_input_manifest"])
    words: dict[str, list[dict[str, Any]]] = {}
    for row in shadow_manifest.get("word_files") or []:
        path = resolve(str(row["path"]))
        if not path.is_file() or path.stat().st_size != int(row["bytes"]) or sha256(path) != row["sha256"]:
            raise PurificationError("coverage_word_file_changed")
        words[session_from_word_path(path)] = read_jsonl(path)

    vectors, vector_errors = load_vectors(paths["shadow_embeddings"])
    scope_causes = set(policy["candidate"]["scope_primary_causes"])
    scope = [
        row for row in error_rows
        if row.get("failure_scope") is True
        and (row.get("classification") or {}).get("primary_cause") in scope_causes
    ]
    expected = policy["source"]
    counts = {
        "items": len(error_rows),
        "words": sum(int(row["word_count"]) for row in error_rows),
        "interval_failure_items": len(scope),
        "speech_supported_interval_items": sum(bool((row.get("audio") or {}).get("speech_supported")) for row in scope),
        "insufficient_audio_items": sum((row.get("classification") or {}).get("primary_cause") == "insufficient_audio_evidence" for row in scope),
        "control_accepted_items": sum((row.get("identity") or {}).get("speaker_id") is not None for row in error_rows),
    }
    expected_counts = {
        "items": int(expected["expected_items"]),
        "words": int(expected["expected_words"]),
        "interval_failure_items": int(expected["expected_interval_failure_items"]),
        "speech_supported_interval_items": int(expected["expected_speech_supported_interval_items"]),
        "insufficient_audio_items": int(expected["expected_insufficient_audio_items"]),
        "control_accepted_items": int(expected["expected_control_accepted_items"]),
    }
    if counts != expected_counts:
        raise PurificationError(f"frozen_counts_changed:{counts}")
    seconds = round(sum(float(row["coverage_weight_sec"]) for row in scope), 6)
    if seconds != float(expected["expected_interval_failure_seconds"]):
        raise PurificationError("interval_scope_seconds_changed")

    for row in shadow_rows:
        audio = row.get("audio") or {}
        path = resolve(str(audio.get("path") or ""))
        if not path.is_file() or path.stat().st_size != int(audio.get("bytes") or -1) or sha256(path) != audio.get("sha256"):
            raise PurificationError(f"control_clip_changed:{row['item_id']}")
    guards = verify_production_guards(shadow_manifest)
    decomposition_policy = read_json(paths["decomposition_policy"])
    reference_row = next(row for row in decomposition_policy["source"]["artifacts"] if row["id"] == "independent_reference")
    reference_path = resolve(str(reference_row["path"]))
    if not reference_path.is_file() or sha256(reference_path) != reference_row["sha256"]:
        raise PurificationError("independent_reference_changed")
    return {
        "error_rows": error_rows,
        "error_by_id": {str(row["item_id"]): row for row in error_rows},
        "shadow_rows": shadow_rows,
        "shadow_by_id": shadow_by_id,
        "shadow_manifest": shadow_manifest,
        "shadow_report": read_json(paths["shadow_report"]),
        "words": words,
        "vectors": vectors,
        "vector_errors": vector_errors,
        "scope": sorted(scope, key=lambda row: (str(row["session_id"]), float(row["start"]), str(row["item_id"]))),
        "reference": read_json(reference_path),
        "guards": guards,
        "counts": counts,
        "scope_seconds": seconds,
    }


def candidate_model_provenance(policy: dict[str, Any]) -> dict[str, Any]:
    shadow_policy = read_json(ROOT / "policies/ecapa-remote-speaker-shadow-qualification-v1.json")
    provenance = SHADOW.candidate_provenance(shadow_policy, fixture_mode=False)
    identity = policy["identity"]
    if (
        provenance.get("backend_id") != identity["backend_id"]
        or provenance.get("model_id") != identity["model_id"]
        or provenance.get("revision") != identity["revision"]
    ):
        raise PurificationError("identity_backend_changed")
    return provenance


def scope_fingerprint(scope: list[dict[str, Any]]) -> str:
    rows = [
        {
            "item_id": row["item_id"],
            "session_id": row["session_id"],
            "start": row["start"],
            "end": row["end"],
            "word_ids": row["word_ids"],
            "cause": row["classification"]["primary_cause"],
            "audio_sha256": row["audio"]["sha256"],
        }
        for row in scope
    ]
    return sha256_bytes(canonical_json(rows))


def input_manifests(policy_path: Path, policy: dict[str, Any], paths: dict[str, Path], data: dict[str, Any], provenance: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    artifacts = [
        {
            "id": row["id"],
            "path": portable(paths[str(row["id"])]),
            "bytes": paths[str(row["id"])].stat().st_size,
            "sha256": row["sha256"],
        }
        for row in policy["source"]["artifacts"]
    ]
    private = {
        "schema": PRIVATE_MANIFEST_SCHEMA,
        "version": VERSION,
        "policy": {"path": portable(policy_path), "sha256": sha256(policy_path)},
        "source_artifacts": artifacts,
        "source_fingerprint": sha256_bytes(canonical_json(artifacts)),
        "scope": {
            "items": len(data["scope"]),
            "words": sum(int(row["word_count"]) for row in data["scope"]),
            "seconds": data["scope_seconds"],
            "sha256": scope_fingerprint(data["scope"]),
        },
        "candidate": dict(policy["candidate"]),
        "identity": {
            "backend_id": provenance["backend_id"],
            "model_id": provenance["model_id"],
            "revision": provenance["revision"],
            "model_tree_sha256": provenance["model_tree_sha256"],
            "minimum_similarity": policy["identity"]["minimum_similarity"],
            "minimum_margin": policy["identity"]["minimum_margin"],
            "enrollment_exemplars": policy["source"]["expected_enrollment_exemplars"],
        },
        "production_guards": data["shadow_manifest"]["production_guards"],
        "safety": dict(policy["safety"]),
    }
    public = {
        "schema": PUBLIC_MANIFEST_SCHEMA,
        "version": VERSION,
        "policy_sha256": private["policy"]["sha256"],
        "source_fingerprint": private["source_fingerprint"],
        "source_artifacts": [
            {"id": row["id"], "bytes": row["bytes"], "sha256": row["sha256"]}
            for row in artifacts
        ],
        "scope": dict(private["scope"]),
        "candidate": {key: value for key, value in private["candidate"].items() if key != "rules_in_order"},
        "candidate_rules": list(private["candidate"]["rules_in_order"]),
        "identity": dict(private["identity"]),
        "guarded_sessions": len(data["guards"]),
        "private_values_excluded": True,
        "safety": dict(policy["safety"]),
    }
    return private, public


def verify_frozen_manifest(out: Path, policy_path: Path, policy: dict[str, Any], paths: dict[str, Path], data: dict[str, Any], provenance: dict[str, Any]) -> dict[str, Any]:
    path = out / "private/input_manifest.json"
    if not path.is_file():
        raise PurificationError("input_not_frozen")
    expected, _ = input_manifests(policy_path, policy, paths, data, provenance)
    saved = read_json(path)
    if canonical_json(saved) != canonical_json(expected):
        raise PurificationError("frozen_manifest_changed")
    return saved


def word_times(row: dict[str, Any]) -> tuple[float, float]:
    start = float(row.get("start") or 0.0)
    end = max(start, float(row.get("end") or start))
    return start, end


def candidate_bounds(row: dict[str, Any], words: list[dict[str, Any]], policy: dict[str, Any]) -> dict[str, Any]:
    candidate = policy["candidate"]
    cause = str(row["classification"]["primary_cause"])
    start = float(row["start"])
    end = float(row["end"])
    span = max(0.0, end - start)
    base = {
        "schema": INTERVAL_SCHEMA,
        "item_id": str(row["item_id"]),
        "session_id": str(row["session_id"]),
        "source_sha256": str(row["audio"]["sha256"]),
        "source_start": float(row["shadow_audio_start"]),
        "source_end": float(row["shadow_audio_end"]),
        "word_start": start,
        "word_end": end,
        "word_span_sec": round(span, 6),
        "primary_cause": cause,
    }
    if cause == "insufficient_audio_evidence" or not bool(row["audio"]["speech_supported"]):
        return {**base, "status": "unknown", "reason": "insufficient_audio_evidence"}
    if span < float(candidate["minimum_unpadded_span_sec"]):
        return {**base, "status": "unknown", "reason": "word_span_too_short"}

    item_ids = {str(value) for value in row["word_ids"]}
    context = [word for word in words if word.get("speaker_id") and str(word.get("word_id")) not in item_ids]
    overlap = []
    for word in context:
        left, right = word_times(word)
        if left < end - 1e-6 and right > start + 1e-6:
            overlap.append(word)
    if overlap:
        return {**base, "status": "unknown", "reason": "known_context_overlaps_word_span", "overlapping_context_words": len(overlap)}

    audio_start = float(row["shadow_audio_start"])
    audio_end = float(row["shadow_audio_end"])
    guard = float(candidate["guard_sec"])
    separation = float(candidate["known_context_separation_sec"])
    bounded_start = max(audio_start, start - guard)
    bounded_end = min(audio_end, end + guard)
    left_context = [word_times(word)[1] for word in context if word_times(word)[1] <= start + 1e-6]
    right_context = [word_times(word)[0] for word in context if word_times(word)[0] >= end - 1e-6]
    if left_context:
        bounded_start = max(bounded_start, max(left_context) + separation)
    if right_context:
        bounded_end = min(bounded_end, min(right_context) - separation)
    if bounded_end - bounded_start < float(candidate["minimum_unpadded_span_sec"]):
        return {**base, "status": "unknown", "reason": "context_clamp_too_short"}
    return {
        **base,
        "status": "materialize",
        "reason": "word_span_guarded",
        "candidate_start": round(bounded_start, 9),
        "candidate_end": round(bounded_end, 9),
        "candidate_span_sec": round(bounded_end - bounded_start, 6),
        "left_guard_sec": round(start - bounded_start, 6),
        "right_guard_sec": round(bounded_end - end, 6),
    }


def write_candidate_audio(source: Path, target: Path, relative_start: float, relative_end: float, subtype: str) -> dict[str, Any]:
    with sf.SoundFile(source) as handle:
        sample_rate = int(handle.samplerate)
        first = max(0, int(round(relative_start * sample_rate)))
        last = min(len(handle), int(round(relative_end * sample_rate)))
        if last <= first:
            raise PurificationError("candidate_audio_empty")
        handle.seek(first)
        values = handle.read(last - first, dtype="float32", always_2d=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.stem}.{os.getpid()}{target.suffix}")
    sf.write(temporary, values, sample_rate, subtype=subtype, format="WAV")
    os.replace(temporary, target)
    return {
        "path": portable(target),
        "sha256": sha256(target),
        "bytes": target.stat().st_size,
        "sample_rate": sample_rate,
        "first_sample": first,
        "last_sample": last,
        "frames": last - first,
        "duration_sec": round((last - first) / sample_rate, 6),
    }


def materialize_candidates(out: Path, data: dict[str, Any], policy: dict[str, Any]) -> list[dict[str, Any]]:
    target_root = out / "private/candidate_audio"
    if target_root.exists():
        shutil.rmtree(target_root)
    rows = []
    for error in data["scope"]:
        shadow = data["shadow_by_id"][str(error["item_id"])]
        merged = dict(error)
        merged["shadow_audio_start"] = float(shadow["audio"]["start"])
        merged["shadow_audio_end"] = float(shadow["audio"]["end"])
        interval = candidate_bounds(merged, data["words"].get(str(error["session_id"]), []), policy)
        if interval["status"] == "materialize":
            source = resolve(str(shadow["audio"]["path"]))
            target = target_root / str(error["session_id"]) / f"{error['item_id']}.wav"
            audio = write_candidate_audio(
                source,
                target,
                float(interval["candidate_start"]) - float(shadow["audio"]["start"]),
                float(interval["candidate_end"]) - float(shadow["audio"]["start"]),
                str(policy["candidate"]["output_subtype"]),
            )
            interval["candidate_audio"] = audio
        rows.append(interval)
    write_jsonl(out / "private/candidate_intervals.jsonl", rows)
    return rows


def verify_candidate_rows(out: Path, data: dict[str, Any], policy: dict[str, Any]) -> list[dict[str, Any]]:
    path = out / "private/candidate_intervals.jsonl"
    if not path.is_file():
        raise PurificationError("candidate_intervals_missing")
    rows = read_jsonl(path)
    if len(rows) != len(data["scope"]):
        raise PurificationError("candidate_scope_count_changed")
    for row in rows:
        if row.get("status") == "materialize":
            audio = row.get("candidate_audio") or {}
            target = resolve(str(audio.get("path") or ""))
            if not target.is_file() or target.stat().st_size != int(audio.get("bytes") or -1) or sha256(target) != audio.get("sha256"):
                raise PurificationError("candidate_audio_changed")
    return rows


def run_embeddings(out: Path, intervals: list[dict[str, Any]], policy: dict[str, Any]) -> tuple[dict[str, np.ndarray], dict[str, str], float]:
    requests = [
        {
            "key": f"candidate:{row['session_id']}:{row['item_id']}",
            "path": str(resolve(row["candidate_audio"]["path"])),
            "start": 0.0,
            "end": float(row["candidate_audio"]["duration_sec"]),
            "minimum_sec": float(policy["candidate"]["minimum_audio_sec_for_ecapa"]),
        }
        for row in intervals if row.get("status") == "materialize"
    ]
    if not requests:
        raise PurificationError("no_candidate_audio")
    request = {
        "schema": "murmurmark.speaker_embedding_request/v1",
        "model_id": policy["identity"]["model_id"],
        "model_revision": policy["identity"]["revision"],
        "allow_errors": True,
        "requests": requests,
    }
    request_path = out / "private/embedding_request.json"
    output_path = out / "private/candidate_embeddings.json"
    write_json(request_path, request)
    shadow_policy = read_json(ROOT / "policies/ecapa-remote-speaker-shadow-qualification-v1.json")
    environment = dict(os.environ)
    environment.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"})
    command = [
        "nice", "-n", str(policy["identity"]["nice"]),
        str(SHADOW.runtime_path(shadow_policy) / "bin/python"), str(SHADOW.ECAPA_WORKER),
        "--request", str(request_path), "--output", str(output_path),
        "--model", str(SHADOW.model_path(shadow_policy)), "--threads", str(policy["identity"]["threads"]),
    ]
    started = time.monotonic()
    result = subprocess.run(command, cwd=ROOT, env=environment, text=True, capture_output=True, check=False)
    runtime = time.monotonic() - started
    if result.returncode:
        raise PurificationError(f"ecapa_worker_failed:{result.stderr.strip()[-400:]}")
    payload = read_json(output_path)
    if payload.get("request_sha256") != sha256(request_path):
        raise PurificationError("candidate_embedding_request_mismatch")
    vectors = {
        str(row["key"]): normalized(np.asarray(row["embedding"], dtype=np.float32))
        for row in payload.get("rows") or []
    }
    errors = {str(row["key"]): str(row["reason"]) for row in payload.get("errors") or []}
    return vectors, errors, runtime


def load_candidate_embeddings(out: Path) -> tuple[dict[str, np.ndarray], dict[str, str]]:
    return load_vectors(out / "private/candidate_embeddings.json")


def enrollment_centers(data: dict[str, Any]) -> dict[str, dict[str, np.ndarray]]:
    grouped: dict[tuple[str, str], tuple[str, ...]] = {}
    for item in data["shadow_rows"]:
        session = str(item["session_id"])
        for row in item.get("enrollment") or []:
            key = (session, str(row["speaker_id"]))
            keys = tuple(str(value) for value in row["embedding_keys"])
            if key in grouped and grouped[key] != keys:
                raise PurificationError("enrollment_keys_changed")
            grouped[key] = keys
    centers: dict[str, dict[str, np.ndarray]] = defaultdict(dict)
    for (session, speaker), keys in grouped.items():
        if any(key not in data["vectors"] for key in keys):
            raise PurificationError("enrollment_embedding_missing")
        center = normalized(np.mean([data["vectors"][key] for key in keys], axis=0))
        centers[session][speaker] = center
    return dict(centers)


def classify(vector: np.ndarray, centers: dict[str, np.ndarray], choices: list[str], policy: dict[str, Any]) -> dict[str, Any]:
    result = SHADOW.classify(
        vector, centers, choices,
        float(policy["identity"]["minimum_similarity"]),
        float(policy["identity"]["minimum_margin"]),
    )
    result["embedding_sha256"] = embedding_digest(vector)
    return result


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


def reference_outcome(item: dict[str, Any], speaker_id: str | None, data: dict[str, Any]) -> dict[str, Any]:
    grade = str((item.get("truth") or {}).get("grade") or "anonymous_machine_baseline")
    if speaker_id is None:
        return {"grade": grade, "evaluated": False, "correct": None}
    if grade == "structural_one_to_one":
        return {"grade": grade, "evaluated": True, "correct": speaker_id == "remote_speaker_01"}
    if grade != "independent_machine_reference":
        return {"grade": grade, "evaluated": False, "correct": None}
    labels = sorted({
        str(row["reference_speaker"])
        for row in data["reference"].get("rows") or []
        if row.get("utterance_id") == item.get("utterance_id") and row.get("reference_speaker")
    })
    mapping = reference_mapping(data["reference"])
    correct = len(labels) == 1 and mapping.get(speaker_id) == labels[0]
    return {
        "grade": grade,
        "evaluated": bool(labels),
        "correct": correct if labels else None,
        "mapped": speaker_id in mapping,
    }


def item_comparisons(data: dict[str, Any], intervals: list[dict[str, Any]], candidate_vectors: dict[str, np.ndarray], candidate_errors: dict[str, str], policy: dict[str, Any]) -> list[dict[str, Any]]:
    interval_by_id = {str(row["item_id"]): row for row in intervals}
    centers = enrollment_centers(data)
    rows = []
    for control in data["shadow_rows"]:
        item_id = str(control["item_id"])
        error = data["error_by_id"][item_id]
        control_decision = dict(control["shadow"])
        interval = interval_by_id.get(item_id)
        candidate_decision = dict(control_decision)
        source = "unchanged_control"
        if interval is not None:
            source = str(interval["reason"])
            if interval.get("status") != "materialize":
                candidate_decision = {
                    "speaker_id": None,
                    "top_speaker_id": None,
                    "similarity": None,
                    "margin": None,
                    "scores": {},
                    "reason": source,
                    "embedding_sha256": None,
                }
            else:
                key = f"candidate:{control['session_id']}:{item_id}"
                if key in candidate_vectors:
                    candidate_decision = classify(
                        candidate_vectors[key],
                        centers.get(str(control["session_id"]), {}),
                        [str(value) for value in control["speaker_choices"]],
                        policy,
                    )
                    source = "candidate_ecapa"
                else:
                    candidate_decision = {
                        "speaker_id": None,
                        "top_speaker_id": None,
                        "similarity": None,
                        "margin": None,
                        "scores": {},
                        "reason": candidate_errors.get(key, "candidate_embedding_missing"),
                        "embedding_sha256": None,
                    }
                    source = "candidate_embedding_error"
        control_speaker = control_decision.get("speaker_id")
        candidate_speaker = candidate_decision.get("speaker_id")
        comparison = {
            "schema": ITEM_SCHEMA,
            "item_id": item_id,
            "session_id": str(control["session_id"]),
            "utterance_id": str(control["utterance_id"]),
            "start": float(control["start"]),
            "end": float(control["end"]),
            "word_ids": list(control["word_ids"]),
            "word_count": int(control["word_count"]),
            "coverage_weight_sec": float(control["coverage_weight_sec"]),
            "in_interval_scope": interval is not None,
            "candidate_source": source,
            "control": control_decision,
            "candidate": candidate_decision,
            "change": (
                "newly_accepted" if control_speaker is None and candidate_speaker is not None
                else "removed_acceptance" if control_speaker is not None and candidate_speaker is None
                else "changed_speaker" if control_speaker and candidate_speaker and control_speaker != candidate_speaker
                else "unchanged"
            ),
            "control_reference": reference_outcome(control, control_speaker, data),
            "candidate_reference": reference_outcome(control, candidate_speaker, data),
            "provenance": {
                "candidate_id": policy["candidate"]["id"],
                "minimum_similarity": policy["identity"]["minimum_similarity"],
                "minimum_margin": policy["identity"]["minimum_margin"],
                "enrollment_changed": False,
                "threshold_tuned": False,
                "production_applied": False,
            },
        }
        rows.append(comparison)
    return sorted(rows, key=lambda row: (row["session_id"], row["start"], row["item_id"]))


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
            "evaluated_items": len(selected),
            "evaluated_words": words,
            "correct_words": correct,
            "incorrect_words": words - correct,
            "precision": round(correct / words, 6) if words else None,
        }
    return result


def accepted_metrics(rows: list[dict[str, Any]], side: str, *, scope_only: bool = False) -> dict[str, Any]:
    selected = [row for row in rows if (not scope_only or row["in_interval_scope"]) and row[side].get("speaker_id") is not None]
    return {
        "items": len(selected),
        "words": sum(int(row["word_count"]) for row in selected),
        "seconds": round(sum(float(row["coverage_weight_sec"]) for row in selected), 6),
    }


def scoped_word_ids(row: dict[str, Any]) -> set[str]:
    return {f"{row['session_id']}\x1f{word_id}" for word_id in row["word_ids"]}


def exact_control_conservation(rows: list[dict[str, Any]], data: dict[str, Any]) -> bool:
    if len(rows) != len(data["shadow_rows"]):
        return False
    seen: set[str] = set()
    for row in rows:
        control = data["shadow_by_id"].get(str(row["item_id"]))
        if control is None:
            return False
        if (
            str(row["session_id"]) != str(control["session_id"])
            or float(row["start"]) != float(control["start"])
            or float(row["end"]) != float(control["end"])
            or list(row["word_ids"]) != list(control["word_ids"])
        ):
            return False
        item_words = scoped_word_ids(row)
        if seen.intersection(item_words):
            return False
        seen.update(item_words)
    return len(seen) == 851


def choose_decision(gates: dict[str, bool], invariants: dict[str, bool]) -> str:
    if not invariants or not all(invariants.values()):
        return "EVIDENCE_BOUND"
    return (
        "ADVANCE_PURIFIED_SHADOW_CANDIDATE"
        if gates and all(gates.values())
        else "DO_NOT_ADVANCE_INTERVAL_PURIFICATION"
    )


def verify_output_payloads(out: Path, expected: dict[str, bytes]) -> list[str]:
    return [
        name for name, payload in expected.items()
        if not (out / name).is_file() or (out / name).read_bytes() != payload
    ]


def build_report(data: dict[str, Any], intervals: list[dict[str, Any]], rows: list[dict[str, Any]], policy: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    control = accepted_metrics(rows, "control")
    candidate = accepted_metrics(rows, "candidate")
    control_scope = accepted_metrics(rows, "control", scope_only=True)
    candidate_scope = accepted_metrics(rows, "candidate", scope_only=True)
    newly = [row for row in rows if row["change"] == "newly_accepted"]
    removed = [row for row in rows if row["change"] == "removed_acceptance"]
    changed = [row for row in rows if row["change"] == "changed_speaker"]
    control_evidence = evidence_metrics(rows, "control")
    candidate_evidence = evidence_metrics(rows, "candidate")
    control_errors = set().union(*[
        scoped_word_ids(row)
        for row in rows
        if row["control_reference"]["evaluated"] and row["control_reference"]["correct"] is False
    ]) if rows else set()
    candidate_errors = set().union(*[
        scoped_word_ids(row)
        for row in rows
        if row["candidate_reference"]["evaluated"] and row["candidate_reference"]["correct"] is False
    ]) if rows else set()
    materialized = sum(row.get("status") == "materialize" for row in intervals)
    unknown_reasons = dict(sorted(Counter(str(row["reason"]) for row in intervals if row.get("status") != "materialize").items()))
    decision_policy = policy["decision"]
    new_words = sum(int(row["word_count"]) for row in newly)
    new_seconds = round(sum(float(row["coverage_weight_sec"]) for row in newly), 6)
    item_gain_ratio = round(len(newly) / max(1, len(intervals)), 6)
    seconds_gain_ratio = round(max(0.0, candidate_scope["seconds"] - control_scope["seconds"]) / float(data["scope_seconds"]), 6)
    structural_control = control_evidence["structural_one_to_one"]["precision"]
    structural_candidate = candidate_evidence["structural_one_to_one"]["precision"]
    independent_control = control_evidence["independent_machine_reference"]["precision"]
    independent_candidate = candidate_evidence["independent_machine_reference"]["precision"]
    gates = {
        "materialized_candidate_items": materialized >= int(decision_policy["minimum_materialized_candidate_items"]),
        "minimum_newly_accepted_items": len(newly) >= int(decision_policy["minimum_newly_accepted_items"]),
        "minimum_newly_accepted_words": new_words >= int(decision_policy["minimum_newly_accepted_words"]),
        "minimum_newly_accepted_seconds": new_seconds >= float(decision_policy["minimum_newly_accepted_seconds"]),
        "minimum_interval_accepted_item_gain_ratio": item_gain_ratio >= float(decision_policy["minimum_interval_accepted_item_gain_ratio"]),
        "minimum_interval_recovered_seconds_gain_ratio": seconds_gain_ratio >= float(decision_policy["minimum_interval_recovered_seconds_gain_ratio"]),
        "structural_precision_no_regression": structural_candidate is not None and structural_control is not None and structural_candidate >= structural_control,
        "independent_precision_no_regression": independent_candidate is not None and independent_control is not None and independent_candidate >= independent_control,
        "no_new_reference_errors": candidate_errors.issubset(control_errors),
        "no_changed_accepted_speaker": not changed,
        "silent_fail_open": all(row["candidate"].get("speaker_id") is None for row in rows if data["error_by_id"][row["item_id"]]["identity"].get("embedding_error")),
        "exact_word_and_timestamp_conservation": exact_control_conservation(rows, data),
        "existing_coverage_labels_unchanged": all(data["shadow_by_id"][row["item_id"]]["baseline"].get("speaker_id") is None for row in rows),
        "boundary_and_chronology_no_regression": all(float(row["end"]) >= float(row["start"]) for row in rows),
        "frozen_control_reproduced": control["items"] == policy["source"]["expected_control_accepted_items"],
        "production_guards_unchanged": len(data["guards"]) == len(data["shadow_manifest"]["production_guards"]),
    }
    invariants = {
        "all_items_accounted_once": len({row["item_id"] for row in rows}) == len(rows) == 278,
        "all_words_accounted_once": sum(int(row["word_count"]) for row in rows) == 851,
        "interval_scope_frozen": len(intervals) == 93,
        "enrollment_unchanged": True,
        "thresholds_unchanged": True,
        "production_guards_unchanged": gates["production_guards_unchanged"],
        "candidate_is_shadow_only": True,
    }
    decision = choose_decision(gates, invariants)
    return {
        "schema": REPORT_SCHEMA,
        "version": VERSION,
        "decision": decision,
        "source_fingerprint": manifest["source_fingerprint"],
        "scope": {
            "items": len(rows),
            "words": sum(int(row["word_count"]) for row in rows),
            "interval_failure_items": len(intervals),
            "interval_failure_words": sum(int(row["word_count"]) for row in rows if row["in_interval_scope"]),
            "interval_failure_seconds": data["scope_seconds"],
            "guarded_sessions": len(data["guards"]),
        },
        "candidate": {
            "id": policy["candidate"]["id"],
            "materialized_items": materialized,
            "unknown_items": len(intervals) - materialized,
            "unknown_reasons": unknown_reasons,
            "parameter_search_used": False,
            "post_hoc_tuning_used": False,
        },
        "comparison": {
            "control": control,
            "candidate": candidate,
            "control_interval_scope": control_scope,
            "candidate_interval_scope": candidate_scope,
            "newly_accepted_items": len(newly),
            "newly_accepted_words": new_words,
            "newly_accepted_seconds": new_seconds,
            "removed_control_acceptances": len(removed),
            "changed_accepted_speakers": len(changed),
            "interval_accepted_item_gain_ratio": item_gain_ratio,
            "interval_recovered_seconds_gain_ratio": seconds_gain_ratio,
            "control_evidence": control_evidence,
            "candidate_evidence": candidate_evidence,
            "control_reference_error_words": len(control_errors),
            "candidate_reference_error_words": len(candidate_errors),
            "new_reference_error_words": len(candidate_errors - control_errors),
        },
        "gates": gates,
        "invariants": invariants,
        "safety": {
            "production_mutated": False,
            "coverage_v3_mutated": False,
            "selected_transcript_mutated": False,
            "raw_audio_mutated": False,
            "primary_asr_mutated": False,
            "echo_guard_mutated": False,
            "enrollment_mutated": False,
            "thresholds_tuned": False,
            "human_names_inferred": False,
            "cross_session_voice_linking": False,
            "private_values_excluded": True,
            "shadow_only": True,
        },
        "next_action": (
            "qualify_purified_shadow_candidate"
            if decision == "ADVANCE_PURIFIED_SHADOW_CANDIDATE"
            else "advance_enrollment_hardening_without_retuning_this_candidate"
        ),
    }


def markdown_report(report: dict[str, Any]) -> bytes:
    comparison = report["comparison"]
    gates = report["gates"]
    lines = [
        "# Bounded Remote Speaker Interval Purification v1",
        "",
        f"Decision: `{report['decision']}`",
        "",
        "## Scope",
        "",
        f"- Frozen items: {report['scope']['items']}",
        f"- Frozen words: {report['scope']['words']}",
        f"- Interval failures: {report['scope']['interval_failure_items']} / {report['scope']['interval_failure_seconds']:.6f}s",
        f"- Materialized candidate clips: {report['candidate']['materialized_items']}",
        "",
        "## Comparison",
        "",
        f"- Control accepted: {comparison['control']['items']} items / {comparison['control']['words']} words / {comparison['control']['seconds']:.6f}s",
        f"- Candidate accepted: {comparison['candidate']['items']} items / {comparison['candidate']['words']} words / {comparison['candidate']['seconds']:.6f}s",
        f"- Newly accepted: {comparison['newly_accepted_items']} items / {comparison['newly_accepted_words']} words / {comparison['newly_accepted_seconds']:.6f}s",
        f"- Removed control accepts: {comparison['removed_control_acceptances']}",
        f"- New reference-error words: {comparison['new_reference_error_words']}",
        "",
        "## Evidence",
        "",
        f"- Structural precision: {comparison['control_evidence']['structural_one_to_one']['precision']} -> {comparison['candidate_evidence']['structural_one_to_one']['precision']}",
        f"- Independent precision: {comparison['control_evidence']['independent_machine_reference']['precision']} -> {comparison['candidate_evidence']['independent_machine_reference']['precision']}",
        "",
        "## Gates",
        "",
    ]
    lines.extend(f"- {'PASS' if passed else 'FAIL'} `{name}`" for name, passed in gates.items())
    lines.extend([
        "",
        "## Boundary",
        "",
        "This result is diagnostic and shadow-only. Coverage v3, selected transcripts, raw audio,",
        "enrollment, ECAPA thresholds and production output remain unchanged.",
    ])
    return ("\n".join(lines) + "\n").encode()


def output_payloads(rows: list[dict[str, Any]], report: dict[str, Any]) -> dict[str, bytes]:
    return {
        "private/item_comparison.jsonl": jsonl_bytes(rows),
        "bounded_remote_speaker_interval_purification_report.json": pretty_json(report),
        "bounded_remote_speaker_interval_purification_report.md": markdown_report(report),
    }


def write_outputs(out: Path, rows: list[dict[str, Any]], report: dict[str, Any]) -> None:
    for name, payload in output_payloads(rows, report).items():
        atomic_write(out / name, payload)


def replay(out: Path, data: dict[str, Any], policy: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    intervals = verify_candidate_rows(out, data, policy)
    vectors, errors = load_candidate_embeddings(out)
    rows = item_comparisons(data, intervals, vectors, errors, policy)
    report = build_report(data, intervals, rows, policy, manifest)
    expected = output_payloads(rows, report)
    mismatches = verify_output_payloads(out, expected)
    if mismatches:
        raise PurificationError(f"deterministic_replay_mismatch:{','.join(mismatches)}")
    result = {
        "schema": REPLAY_SCHEMA,
        "version": VERSION,
        "source_fingerprint": manifest["source_fingerprint"],
        "byte_identical": True,
        "verified_outputs": {name: sha256(out / name) for name in sorted(expected)},
        "candidate_audio_verified": sum(row.get("status") == "materialize" for row in intervals),
        "production_mutated": False,
    }
    write_json(out / "replay_report.json", result)
    return result


def tracked_manifest(policy_path: Path, out: Path) -> dict[str, Any]:
    report = read_json(out / "bounded_remote_speaker_interval_purification_report.json")
    replay_row = read_json(out / "replay_report.json")
    artifacts = {}
    for key, path in {
        "policy": policy_path,
        "public_input_manifest": out / "input_manifest.public.json",
        "report": out / "bounded_remote_speaker_interval_purification_report.json",
        "markdown_report": out / "bounded_remote_speaker_interval_purification_report.md",
        "replay_report": out / "replay_report.json",
    }.items():
        artifacts[key] = {"id": key, "path": portable(path), "bytes": path.stat().st_size, "sha256": sha256(path)}
    return {
        "schema": TRACKED_SCHEMA,
        "version": VERSION,
        "decision": report["decision"],
        "source_fingerprint": report["source_fingerprint"],
        "scope": report["scope"],
        "candidate": report["candidate"],
        "comparison": report["comparison"],
        "gates": report["gates"],
        "invariants": report["invariants"],
        "safety": report["safety"],
        "replay_verified": replay_row.get("byte_identical") is True,
        "private_values_excluded": True,
        "artifacts": artifacts,
    }


def status(out: Path) -> dict[str, Any]:
    report_path = out / "bounded_remote_speaker_interval_purification_report.json"
    report = read_json(report_path) if report_path.is_file() else {}
    return {
        "schema": "murmurmark.bounded_remote_speaker_interval_purification_status/v1",
        "frozen": (out / "private/input_manifest.json").is_file(),
        "materialized": (out / "private/candidate_intervals.jsonl").is_file(),
        "evaluated": bool(report),
        "replayed": (out / "replay_report.json").is_file(),
        "decision": report.get("decision"),
        "next_action": report.get("next_action"),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("preflight", "freeze", "materialize", "evaluate", "status", "replay", "finalize", "all"))
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
    provenance = candidate_model_provenance(policy)
    if args.action == "preflight":
        print(json.dumps({"status": "ready", "candidate": policy["candidate"]["id"], "scope_items": len(data["scope"]), "offline": True}, ensure_ascii=False, sort_keys=True))
        return 0
    if args.action in {"freeze", "all"}:
        private, public = input_manifests(policy_path, policy, paths, data, provenance)
        write_json(out / "private/input_manifest.json", private)
        write_json(out / "input_manifest.public.json", public)
    manifest = verify_frozen_manifest(out, policy_path, policy, paths, data, provenance)
    if args.action in {"materialize", "all"}:
        intervals = materialize_candidates(out, data, policy)
        print(f"materialized: {sum(row.get('status') == 'materialize' for row in intervals)}/{len(intervals)}")
    if args.action in {"evaluate", "all"}:
        intervals = verify_candidate_rows(out, data, policy)
        vectors, errors, _runtime = run_embeddings(out, intervals, policy)
        rows = item_comparisons(data, intervals, vectors, errors, policy)
        report = build_report(data, intervals, rows, policy, manifest)
        write_outputs(out, rows, report)
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
    except PurificationError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2) from error
