#!/usr/bin/env python3
"""Decompose frozen real-session ECAPA shadow errors without changing production."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import soundfile as sf


SCRIPT_PATH = Path(__file__).resolve()
ROOT = SCRIPT_PATH.parents[1]
DEFAULT_POLICY = ROOT / "policies/remote-speaker-shadow-error-decomposition-v1.json"
DEFAULT_OUT = ROOT / "sessions/_reports/remote-speaker-shadow-error-decomposition-v1"
DEFAULT_TRACKED = ROOT / "docs/testing/remote-speaker-shadow-error-decomposition-v1-manifest.json"

POLICY_SCHEMA = "murmurmark.remote_speaker_shadow_error_decomposition_policy/v1"
PRIVATE_INPUT_SCHEMA = "murmurmark.remote_speaker_shadow_error_decomposition_input/v1"
PUBLIC_INPUT_SCHEMA = "murmurmark.remote_speaker_shadow_error_decomposition_public_input/v1"
ITEM_SCHEMA = "murmurmark.remote_speaker_shadow_item_error/v1"
ENROLLMENT_SCHEMA = "murmurmark.remote_speaker_shadow_enrollment_diagnostic/v1"
REFERENCE_SCHEMA = "murmurmark.remote_speaker_shadow_reference_explanation/v1"
REPORT_SCHEMA = "murmurmark.remote_speaker_shadow_error_decomposition_report/v1"
REPLAY_SCHEMA = "murmurmark.remote_speaker_shadow_error_decomposition_replay/v1"
TRACKED_SCHEMA = "murmurmark.remote_speaker_shadow_error_decomposition_manifest/v1"
STATUS_SCHEMA = "murmurmark.remote_speaker_shadow_error_decomposition_status/v1"
VERSION = "0.1.0"

ALLOWED_OUTCOMES = {
    "ADVANCE_INTERVAL_PURIFICATION",
    "ADVANCE_ENROLLMENT_HARDENING",
    "ADVANCE_REFERENCE_ACQUISITION",
    "ADVANCE_IDENTITY_BACKEND",
    "EVIDENCE_BOUND",
}


class DecompositionError(RuntimeError):
    pass


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def pretty_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def compact_json(value: Any) -> bytes:
    return canonical_json(value)


def jsonl_bytes(rows: Iterable[dict[str, Any]]) -> bytes:
    return b"".join(compact_json(row) for row in rows)


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
        raise DecompositionError(f"invalid_json:{path}:{error}") from error
    if not isinstance(value, dict):
        raise DecompositionError(f"json_object_required:{path}")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise DecompositionError(f"jsonl_object_required:{path}:{number}")
            rows.append(row)
    except (OSError, json.JSONDecodeError) as error:
        raise DecompositionError(f"invalid_jsonl:{path}:{error}") from error
    return rows


def atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
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
        raise DecompositionError(f"path_outside_repository:{path}") from error


def fingerprint(path: Path, role: str, *, include_path: bool = True) -> dict[str, Any]:
    if not path.is_file():
        raise DecompositionError(f"required_artifact_missing:{role}:{path}")
    row: dict[str, Any] = {
        "id": role,
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
    }
    if include_path:
        row["path"] = portable(path)
    return row


def load_policy(path: Path) -> dict[str, Any]:
    policy = read_json(path)
    if policy.get("schema") != POLICY_SCHEMA:
        raise DecompositionError("unsupported_policy_schema")
    if set(policy.get("decision", {}).get("allowed_outcomes") or []) != ALLOWED_OUTCOMES:
        raise DecompositionError("terminal_outcomes_changed")
    safety = policy.get("safety") or {}
    required_false = (
        "production_mutation",
        "coverage_v3_mutation",
        "selected_transcript_mutation",
        "raw_audio_mutation",
        "primary_asr_mutation",
        "echo_guard_mutation",
        "threshold_tuning",
        "human_name_inference",
        "cross_session_voice_linking",
        "cloud_allowed",
        "manual_listening_required",
    )
    if any(safety.get(key) is not False for key in required_false):
        raise DecompositionError("unsafe_policy")
    if safety.get("diagnostic_only") is not True:
        raise DecompositionError("diagnostic_only_required")
    return policy


def source_paths(policy: dict[str, Any]) -> dict[str, Path]:
    rows = policy.get("source", {}).get("artifacts") or []
    result: dict[str, Path] = {}
    for row in rows:
        role = str(row.get("id") or "")
        if not role or role in result:
            raise DecompositionError("duplicate_or_empty_source_id")
        path = resolve(str(row.get("path") or ""))
        actual = fingerprint(path, role)
        if actual["sha256"] != row.get("sha256"):
            raise DecompositionError(f"source_hash_mismatch:{role}")
        result[role] = path
    if len(result) != len(rows):
        raise DecompositionError("source_inventory_incomplete")
    return result


def inherited_artifact_rows(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for group in ("frozen_inputs", "clip_files", "exemplar_files", "word_files"):
        for index, row in enumerate(manifest.get(group) or []):
            if not isinstance(row, dict):
                raise DecompositionError(f"invalid_inherited_artifact:{group}:{index}")
            rows.append({**row, "_role": f"{group}:{index}"})
    for guard_index, guard in enumerate(manifest.get("production_guards") or []):
        if not isinstance(guard, dict):
            raise DecompositionError(f"invalid_production_guard:{guard_index}")
        for key in ("selected_dialogue", "v3_manifest", "v3_report"):
            row = guard.get(key)
            if isinstance(row, dict):
                rows.append({**row, "_role": f"production_guard:{guard_index}:{key}"})
        for audio_index, row in enumerate(guard.get("raw_audio") or []):
            if isinstance(row, dict):
                rows.append({**row, "_role": f"production_guard:{guard_index}:raw:{audio_index}"})
    return rows


def verify_inherited_artifacts(manifest: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    verified: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in inherited_artifact_rows(manifest):
        path = resolve(str(row.get("path") or ""))
        role = str(row["_role"])
        relative = portable(path)
        if relative in seen:
            continue
        seen.add(relative)
        actual = fingerprint(path, role)
        if row.get("bytes") is not None and int(row["bytes"]) != actual["bytes"]:
            raise DecompositionError(f"inherited_size_mismatch:{role}")
        if str(row.get("sha256") or "") != actual["sha256"]:
            raise DecompositionError(f"inherited_hash_mismatch:{role}")
        verified.append(actual)
    verified.sort(key=lambda row: (str(row["id"]), str(row["path"])))
    return verified, sha256_bytes(canonical_json(verified))


def embedding_digest(vector: np.ndarray | None) -> str | None:
    if vector is None:
        return None
    return sha256_bytes(np.asarray(vector, dtype="<f4").tobytes())


def normalize(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    if not math.isfinite(norm) or norm <= 1e-12:
        raise DecompositionError("invalid_embedding_vector")
    return np.asarray(vector / norm, dtype=np.float32)


def parse_enrollment_key(key: str) -> tuple[str, str] | None:
    parts = key.split(":")
    if len(parts) != 4 or parts[0] != "enroll":
        return None
    return parts[1], parts[2]


def session_from_path(path: str) -> str:
    parts = Path(path).parts
    try:
        return parts[parts.index("sessions") + 1]
    except (ValueError, IndexError) as error:
        raise DecompositionError(f"session_not_found_in_path:{path}") from error


def load_data(policy: dict[str, Any], paths: dict[str, Path]) -> dict[str, Any]:
    shadow_manifest = read_json(paths["shadow_input_manifest"])
    embedding_request = read_json(paths["embedding_request"])
    embeddings_payload = read_json(paths["embeddings"])
    shadow_report = read_json(paths["shadow_report"])
    shadow_replay = read_json(paths["shadow_replay"])
    boundary_cases = read_json(paths["boundary_cases"])
    reference = read_json(paths["independent_reference"])
    items = read_jsonl(paths["item_decisions"])
    words = read_jsonl(paths["word_decisions"])

    raw_vectors = {
        str(row["key"]): np.asarray(row["embedding"], dtype=np.float32)
        for row in embeddings_payload.get("rows") or []
    }
    vectors = {key: normalize(vector) for key, vector in raw_vectors.items()}
    errors = {str(row["key"]): str(row["reason"]) for row in embeddings_payload.get("errors") or []}
    requests = {str(row["key"]): row for row in embedding_request.get("requests") or []}

    coverage_words: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for artifact in shadow_manifest.get("word_files") or []:
        path = resolve(str(artifact["path"]))
        session_id = str(artifact.get("session_id") or session_from_path(str(artifact["path"])))
        coverage_words[session_id].extend(read_jsonl(path))
    for session_rows in coverage_words.values():
        session_rows.sort(key=lambda row: (float(row.get("start") or 0), float(row.get("end") or 0), str(row.get("word_id") or "")))

    expected = policy["source"]
    if shadow_report.get("decision") != expected["expected_decision"]:
        raise DecompositionError("upstream_decision_changed")
    if shadow_replay.get("byte_identical") is not True:
        raise DecompositionError("upstream_replay_not_verified")
    if len(items) != int(expected["expected_items"]) or len(words) != int(expected["expected_words"]):
        raise DecompositionError("upstream_item_or_word_count_changed")
    accepted = sum(row.get("shadow", {}).get("speaker_id") is not None for row in items)
    if accepted != int(expected["expected_accepted_items"]):
        raise DecompositionError("upstream_accepted_item_count_changed")
    if len(items) - accepted != int(expected["expected_abstentions"]):
        raise DecompositionError("upstream_abstention_count_changed")
    if len(requests) != int(expected["expected_embedding_requests"]):
        raise DecompositionError("embedding_request_count_changed")
    if len(errors) != int(expected["expected_embedding_failures"]):
        raise DecompositionError("embedding_failure_count_changed")
    enrollment_requests = {key: row for key, row in requests.items() if key.startswith("enroll:")}
    if len(enrollment_requests) != int(expected["expected_enrollment_exemplars"]):
        raise DecompositionError("enrollment_exemplar_count_changed")
    if set(vectors) | set(errors) != set(requests):
        raise DecompositionError("embedding_result_coverage_mismatch")

    item_keys = {(str(row["session_id"]), str(row["item_id"])) for row in items}
    word_keys = {(str(row["session_id"]), str(row["word_id"])) for row in words}
    if len(item_keys) != len(items) or len(word_keys) != len(words):
        raise DecompositionError("duplicate_item_or_word_id")
    words_by_item: Counter[tuple[str, str]] = Counter(
        (str(row["session_id"]), str(row["item_id"])) for row in words
    )
    for row in items:
        key = (str(row["session_id"]), str(row["item_id"]))
        if words_by_item[key] != int(row["word_count"]):
            raise DecompositionError(f"item_word_count_mismatch:{key[1]}")
        embedding_key = str(row["embedding"]["key"])
        if embedding_key not in vectors and embedding_key not in errors:
            raise DecompositionError(f"item_embedding_missing:{key[1]}")
        if embedding_digest(raw_vectors.get(embedding_key)) != row["embedding"].get("sha256"):
            raise DecompositionError(f"item_embedding_digest_mismatch:{key[1]}")

    boundary_set = {
        (str(row.get("session_id")), str(row.get("utterance_id")))
        for row in boundary_cases.get("cases") or []
    }
    return {
        "shadow_manifest": shadow_manifest,
        "embedding_request": embedding_request,
        "embeddings_payload": embeddings_payload,
        "shadow_report": shadow_report,
        "items": sorted(items, key=lambda row: (str(row["session_id"]), float(row["start"]), str(row["item_id"]))),
        "words": words,
        "vectors": vectors,
        "errors": errors,
        "requests": requests,
        "enrollment_requests": enrollment_requests,
        "coverage_words": dict(coverage_words),
        "boundary_set": boundary_set,
        "reference": reference,
    }


def build_input_manifest(policy_path: Path, policy: dict[str, Any], paths: dict[str, Path], data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    source_artifacts = [fingerprint(path, role) for role, path in sorted(paths.items())]
    inherited, inherited_fingerprint = verify_inherited_artifacts(data["shadow_manifest"])
    production_roles = [row for row in inherited if str(row["id"]).startswith("production_guard:")]
    private = {
        "schema": PRIVATE_INPUT_SCHEMA,
        "version": VERSION,
        "policy": fingerprint(policy_path, "decomposition_policy"),
        "source_artifacts": source_artifacts,
        "inherited_artifacts": inherited,
        "inherited_artifact_fingerprint": inherited_fingerprint,
        "production_guard_fingerprint": sha256_bytes(canonical_json(production_roles)),
        "counts": {
            "items": len(data["items"]),
            "words": len(data["words"]),
            "accepted_items": sum(row["shadow"]["speaker_id"] is not None for row in data["items"]),
            "abstentions": sum(row["shadow"]["speaker_id"] is None for row in data["items"]),
            "embedding_requests": len(data["requests"]),
            "embedding_failures": len(data["errors"]),
            "enrollment_exemplars": len(data["enrollment_requests"]),
        },
        "safety": policy["safety"],
    }
    private["source_fingerprint"] = sha256_bytes(canonical_json(private))
    public = {
        "schema": PUBLIC_INPUT_SCHEMA,
        "version": VERSION,
        "source_fingerprint": private["source_fingerprint"],
        "source_artifacts": [
            {"id": row["id"], "bytes": row["bytes"], "sha256": row["sha256"]}
            for row in source_artifacts
        ],
        "inherited_artifact_count": len(inherited),
        "inherited_artifact_fingerprint": inherited_fingerprint,
        "production_guard_fingerprint": private["production_guard_fingerprint"],
        "counts": private["counts"],
        "private_paths_excluded": True,
        "speech_text_excluded": True,
        "embedding_values_excluded": True,
    }
    return private, public


def verify_frozen_manifest(path: Path, policy_path: Path, policy: dict[str, Any], paths: dict[str, Path], data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    expected_private, expected_public = build_input_manifest(policy_path, policy, paths, data)
    saved = read_json(path)
    if canonical_json(saved) != canonical_json(expected_private):
        raise DecompositionError("frozen_input_manifest_mismatch")
    return saved, expected_public


def dbfs(samples: np.ndarray) -> float:
    if samples.size == 0:
        return -120.0
    rms = float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))
    return round(20.0 * math.log10(max(rms, 1e-6)), 6)


def frame_dbfs(samples: np.ndarray, sample_rate: int, frame_ms: int, hop_ms: int) -> np.ndarray:
    frame = max(1, int(round(sample_rate * frame_ms / 1000.0)))
    hop = max(1, int(round(sample_rate * hop_ms / 1000.0)))
    if samples.size < frame:
        samples = np.pad(samples, (0, frame - samples.size))
    values = []
    for start in range(0, max(1, samples.size - frame + 1), hop):
        values.append(dbfs(samples[start : start + frame]))
    return np.asarray(values or [-120.0], dtype=np.float64)


def speech_band_ratio(samples: np.ndarray, sample_rate: int) -> float:
    if samples.size < 8:
        return 0.0
    windowed = samples.astype(np.float64) * np.hanning(samples.size)
    power = np.square(np.abs(np.fft.rfft(windowed)))
    frequencies = np.fft.rfftfreq(samples.size, 1.0 / sample_rate)
    total = float(power[(frequencies >= 20.0)].sum())
    band = float(power[(frequencies >= 80.0) & (frequencies <= min(4000.0, sample_rate / 2.0))].sum())
    return round(band / total, 6) if total > 1e-12 else 0.0


def read_audio(path: Path) -> tuple[np.ndarray, int]:
    try:
        samples, sample_rate = sf.read(path, always_2d=True, dtype="float32")
    except (OSError, RuntimeError) as error:
        raise DecompositionError(f"invalid_audio:{path}:{error}") from error
    if sample_rate <= 0 or samples.size == 0:
        raise DecompositionError(f"empty_audio:{path}")
    return np.asarray(samples.mean(axis=1), dtype=np.float32), int(sample_rate)


def audio_metrics(item: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    config = policy["measurement"]["audio"]
    path = resolve(str(item["audio"]["path"]))
    samples, sample_rate = read_audio(path)
    duration = samples.size / sample_rate
    expected_duration = float(item["audio"]["end"]) - float(item["audio"]["start"])
    if abs(duration - expected_duration) > 0.03:
        raise DecompositionError(f"audio_duration_mismatch:{item['item_id']}")
    if sha256(path) != item["audio"]["sha256"]:
        raise DecompositionError(f"audio_hash_mismatch:{item['item_id']}")

    exact_start = max(0.0, float(item["start"]) - float(item["audio"]["start"]))
    exact_end = min(duration, float(item["end"]) - float(item["audio"]["start"]))
    center = max(0.0, min(duration, (exact_start + exact_end) / 2.0))
    minimum_core = float(config["minimum_core_sec"])
    if exact_end - exact_start < minimum_core:
        exact_start = max(0.0, center - minimum_core / 2.0)
        exact_end = min(duration, exact_start + minimum_core)
        exact_start = max(0.0, exact_end - minimum_core)
    start_sample = min(samples.size, max(0, int(round(exact_start * sample_rate))))
    end_sample = min(samples.size, max(start_sample + 1, int(round(exact_end * sample_rate))))
    core = samples[start_sample:end_sample]
    padding = np.concatenate((samples[:start_sample], samples[end_sample:]))

    frames = frame_dbfs(core, sample_rate, int(config["frame_ms"]), int(config["hop_ms"]))
    noise = float(np.percentile(frames, 20))
    peak_frame = float(np.max(frames))
    threshold = min(
        max(float(config["activity_floor_dbfs"]), noise + float(config["activity_above_noise_db"])),
        peak_frame - 3.0,
    )
    active_ratio = float(np.mean(frames >= threshold))
    core_rms_dbfs = dbfs(core)
    full_rms_dbfs = dbfs(samples)
    padding_rms_dbfs = dbfs(padding)
    band_ratio = speech_band_ratio(core, sample_rate)
    supported = (
        core_rms_dbfs > float(config["silence_rms_dbfs"])
        and (
            active_ratio >= float(config["minimum_active_frame_ratio"])
            or (
                core_rms_dbfs > float(config["activity_floor_dbfs"])
                and band_ratio >= float(config["minimum_speech_band_ratio"])
            )
        )
    )
    core_rms = 10.0 ** (core_rms_dbfs / 20.0)
    padding_rms = 10.0 ** (padding_rms_dbfs / 20.0)
    return {
        "path": portable(path),
        "sha256": item["audio"]["sha256"],
        "sample_rate": sample_rate,
        "duration_sec": round(duration, 6),
        "exact_span_sec": round(max(0.0, float(item["end"]) - float(item["start"])), 6),
        "analysis_core_start_sec": round(exact_start, 6),
        "analysis_core_end_sec": round(exact_end, 6),
        "full_rms_dbfs": full_rms_dbfs,
        "core_rms_dbfs": core_rms_dbfs,
        "padding_rms_dbfs": padding_rms_dbfs,
        "padding_to_core_rms_ratio": round(padding_rms / max(core_rms, 1e-6), 6),
        "active_frame_ratio": round(active_ratio, 6),
        "speech_band_ratio": band_ratio,
        "speech_supported": bool(supported),
    }


def word_time(row: dict[str, Any]) -> tuple[float, float]:
    start = float(row.get("start") or 0.0)
    end = float(row.get("end") or start)
    return start, max(start, end)


def intersects(row: dict[str, Any], start: float, end: float) -> bool:
    row_start, row_end = word_time(row)
    if row_end <= row_start + 1e-9:
        return start - 1e-9 <= row_start <= end + 1e-9
    return row_start < end + 1e-9 and row_end > start - 1e-9


def interval_metrics(item: dict[str, Any], audio: dict[str, Any], data: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    config = policy["measurement"]["interval"]
    session_id = str(item["session_id"])
    words = data["coverage_words"].get(session_id, [])
    item_word_ids = {str(value) for value in item["word_ids"]}
    item_start = float(item["start"])
    item_end = float(item["end"])
    audio_start = float(item["audio"]["start"])
    audio_end = float(item["audio"]["end"])
    contextual = [
        row for row in words
        if str(row.get("word_id")) not in item_word_ids and intersects(row, audio_start, audio_end)
    ]
    known_context = [row for row in contextual if row.get("speaker_id")]
    context_speakers = sorted({str(row["speaker_id"]) for row in known_context})
    same_utterance_speakers = sorted({
        str(row["speaker_id"])
        for row in words
        if row.get("speaker_id") and str(row.get("utterance_id")) == str(item["utterance_id"])
    })

    left = []
    right = []
    for row in words:
        if str(row.get("word_id")) in item_word_ids or not row.get("speaker_id"):
            continue
        row_start, row_end = word_time(row)
        if row_end <= item_start + 1e-9:
            left.append((item_start - row_end, row))
        if row_start >= item_end - 1e-9:
            right.append((row_start - item_end, row))
    left.sort(key=lambda pair: (pair[0], str(pair[1].get("word_id") or "")))
    right.sort(key=lambda pair: (pair[0], str(pair[1].get("word_id") or "")))
    left_distance = left[0][0] if left else None
    right_distance = right[0][0] if right else None
    left_speaker = str(left[0][1]["speaker_id"]) if left else None
    right_speaker = str(right[0][1]["speaker_id"]) if right else None
    near = float(config["boundary_near_sec"])
    opposing_boundary = (
        left_distance is not None
        and right_distance is not None
        and left_distance <= near
        and right_distance <= near
        and left_speaker != right_speaker
    )
    near_known_neighbor = (
        (left_distance is not None and left_distance <= near)
        or (right_distance is not None and right_distance <= near)
    )
    multiple_context = len(context_speakers) > 1
    multiple_utterance = len(same_utterance_speakers) > 1
    padding_dominates = (
        audio["padding_to_core_rms_ratio"]
        >= float(config["minimum_padding_to_core_rms_ratio"])
    )
    known_boundary_case = (session_id, str(item["utterance_id"])) in data["boundary_set"]
    risky = bool(
        opposing_boundary
        or (bool(config["multiple_context_speakers_is_risky"]) and multiple_context)
        or (bool(config["multiple_utterance_speakers_is_risky"]) and multiple_utterance)
        or known_boundary_case
        or (near_known_neighbor and padding_dominates)
    )
    reasons = []
    if opposing_boundary:
        reasons.append("opposing_nearby_speakers")
    if multiple_context:
        reasons.append("multiple_speakers_in_audio_window")
    if multiple_utterance:
        reasons.append("multiple_speakers_in_utterance")
    if known_boundary_case:
        reasons.append("frozen_boundary_case")
    if near_known_neighbor and padding_dominates:
        reasons.append("neighbor_energy_dominates_core")
    return {
        "risky": risky,
        "reasons": reasons,
        "context_word_count": len(contextual),
        "known_context_word_count": len(known_context),
        "context_speakers": context_speakers,
        "same_utterance_speakers": same_utterance_speakers,
        "left_neighbor": {
            "speaker_id": left_speaker,
            "distance_sec": round(left_distance, 6) if left_distance is not None else None,
        },
        "right_neighbor": {
            "speaker_id": right_speaker,
            "distance_sec": round(right_distance, 6) if right_distance is not None else None,
        },
        "known_boundary_case": known_boundary_case,
    }


def enrollment_groups(data: dict[str, Any]) -> dict[tuple[str, str], list[tuple[str, np.ndarray]]]:
    groups: dict[tuple[str, str], list[tuple[str, np.ndarray]]] = defaultdict(list)
    for key in sorted(data["enrollment_requests"]):
        parsed = parse_enrollment_key(key)
        if parsed is None or key not in data["vectors"]:
            continue
        groups[parsed].append((key, data["vectors"][key]))
    return dict(groups)


def centers_for(groups: dict[tuple[str, str], list[tuple[str, np.ndarray]]]) -> dict[str, dict[str, np.ndarray]]:
    centers: dict[str, dict[str, np.ndarray]] = defaultdict(dict)
    for (session_id, speaker_id), rows in groups.items():
        if rows:
            centers[session_id][speaker_id] = normalize(np.mean([vector for _, vector in rows], axis=0))
    return dict(centers)


def classify_embedding(vector: np.ndarray, centers: dict[str, np.ndarray], choices: list[str], policy: dict[str, Any]) -> dict[str, Any]:
    identity = policy["measurement"]["identity"]
    eligible = {speaker: centers[speaker] for speaker in choices if speaker in centers}
    if set(eligible) != set(choices) or not eligible:
        return {
            "speaker_id": None,
            "top_speaker_id": None,
            "similarity": None,
            "margin": None,
            "scores": {},
            "reason": "incomplete_enrollment",
        }
    scores = sorted(((float(vector @ center), speaker) for speaker, center in eligible.items()), reverse=True)
    top_score, top_speaker = scores[0]
    second_score = scores[1][0] if len(scores) > 1 else -1.0
    margin = top_score - second_score
    accepted = (
        top_score >= float(identity["minimum_similarity"])
        and margin >= float(identity["minimum_margin"])
    )
    return {
        "speaker_id": top_speaker if accepted else None,
        "top_speaker_id": top_speaker,
        "similarity": round(top_score, 6),
        "margin": round(margin, 6),
        "scores": {speaker: round(score, 6) for score, speaker in scores},
        "reason": "accepted_centroid" if accepted else "open_set_abstention",
    }


def enrollment_diagnostics(data: dict[str, Any], policy: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[tuple[str, str], dict[str, Any]], dict[tuple[str, str], list[tuple[str, np.ndarray]]]]:
    config = policy["measurement"]["enrollment"]
    groups = enrollment_groups(data)
    centers = centers_for(groups)
    diagnostics: list[dict[str, Any]] = []
    by_pair: dict[tuple[str, str], dict[str, Any]] = {}
    for pair in sorted(groups):
        session_id, speaker_id = pair
        rows = groups[pair]
        trials = []
        for key, vector in rows:
            remaining = [candidate for candidate_key, candidate in rows if candidate_key != key]
            if not remaining:
                trials.append({
                    "embedding_key": key,
                    "same_speaker_similarity": None,
                    "nearest_impostor_similarity": None,
                    "impostor_margin": None,
                    "passed": False,
                    "reason": "single_exemplar",
                })
                continue
            same_center = normalize(np.mean(remaining, axis=0))
            same_similarity = float(vector @ same_center)
            impostors = [
                (float(vector @ center), other_speaker)
                for other_speaker, center in centers.get(session_id, {}).items()
                if other_speaker != speaker_id
            ]
            impostors.sort(reverse=True)
            impostor_similarity = impostors[0][0] if impostors else -1.0
            margin = same_similarity - impostor_similarity
            passed = (
                same_similarity >= float(config["minimum_same_speaker_similarity"])
                and margin >= float(config["minimum_impostor_margin"])
            )
            trials.append({
                "embedding_key": key,
                "embedding_sha256": embedding_digest(vector),
                "same_speaker_similarity": round(same_similarity, 6),
                "nearest_impostor_similarity": round(impostor_similarity, 6),
                "impostor_margin": round(margin, 6),
                "passed": passed,
                "reason": "stable" if passed else "loo_identity_or_margin_failure",
            })
        pass_ratio = sum(row["passed"] for row in trials) / len(trials) if trials else 0.0
        pairwise = [
            float(left[1] @ right[1])
            for index, left in enumerate(rows)
            for right in rows[index + 1 :]
        ]
        stable = pass_ratio >= float(config["minimum_loo_decision_consistency"])
        payload = {
            "schema": ENROLLMENT_SCHEMA,
            "session_id": session_id,
            "speaker_id": speaker_id,
            "exemplar_count": len(rows),
            "centroid_sha256": embedding_digest(centers[session_id][speaker_id]),
            "pairwise_similarity_min": round(min(pairwise), 6) if pairwise else None,
            "pairwise_similarity_mean": round(float(np.mean(pairwise)), 6) if pairwise else None,
            "loo_pass_ratio": round(pass_ratio, 6),
            "stable": stable,
            "trials": trials,
        }
        diagnostics.append(payload)
        by_pair[pair] = payload
    return diagnostics, by_pair, groups


def item_enrollment_metrics(
    item: dict[str, Any],
    data: dict[str, Any],
    policy: dict[str, Any],
    groups: dict[tuple[str, str], list[tuple[str, np.ndarray]]],
    diagnostics: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    key = str(item["embedding"]["key"])
    vector = data["vectors"].get(key)
    if vector is None:
        return {
            "available": False,
            "decision_consistency_ratio": None,
            "top_speaker_consistency_ratio": None,
            "top_profile_stable": None,
            "unstable": False,
            "variants": [],
        }
    session_id = str(item["session_id"])
    choices = [str(value) for value in item["speaker_choices"]]
    full_centers = centers_for(groups).get(session_id, {})
    reproduced = classify_embedding(vector, full_centers, choices, policy)
    original = item["shadow"]
    if reproduced["speaker_id"] != original.get("speaker_id") or reproduced["top_speaker_id"] != original.get("top_speaker_id"):
        raise DecompositionError(f"frozen_shadow_decision_not_reproduced:{item['item_id']}")
    for metric in ("similarity", "margin"):
        if reproduced[metric] is None and original.get(metric) is None:
            continue
        if abs(float(reproduced[metric]) - float(original[metric])) > 1e-5:
            raise DecompositionError(f"frozen_shadow_score_not_reproduced:{item['item_id']}:{metric}")

    variants = []
    for speaker_id in choices:
        for removed_key, _ in groups.get((session_id, speaker_id), []):
            modified = {
                pair: [(candidate_key, candidate) for candidate_key, candidate in rows if candidate_key != removed_key]
                for pair, rows in groups.items()
            }
            result = classify_embedding(vector, centers_for(modified).get(session_id, {}), choices, policy)
            variants.append({
                "removed_enrollment_key": removed_key,
                "removed_speaker_id": speaker_id,
                "speaker_id": result["speaker_id"],
                "top_speaker_id": result["top_speaker_id"],
                "similarity": result["similarity"],
                "margin": result["margin"],
                "reason": result["reason"],
            })
    decision_consistency = (
        sum(row["speaker_id"] == original.get("speaker_id") for row in variants) / len(variants)
        if variants else 0.0
    )
    top_consistency = (
        sum(row["top_speaker_id"] == original.get("top_speaker_id") for row in variants) / len(variants)
        if variants else 0.0
    )
    top_speaker = original.get("top_speaker_id")
    profile = diagnostics.get((session_id, str(top_speaker))) if top_speaker else None
    profile_stable = profile.get("stable") if profile else False
    config = policy["measurement"]["enrollment"]
    unstable = (
        decision_consistency < float(config["minimum_loo_decision_consistency"])
        or top_consistency < float(config["minimum_loo_top_speaker_consistency"])
        or profile_stable is not True
    )
    return {
        "available": True,
        "decision_consistency_ratio": round(decision_consistency, 6),
        "top_speaker_consistency_ratio": round(top_consistency, 6),
        "top_profile_stable": profile_stable,
        "unstable": unstable,
        "variants": variants,
    }


def reference_mapping(reference: dict[str, Any]) -> dict[str, str]:
    rows = [
        row for row in reference.get("rows") or []
        if row.get("predicted_speaker") and row.get("reference_speaker")
    ]
    counts = Counter((str(row["predicted_speaker"]), str(row["reference_speaker"])) for row in rows)
    mapping = {}
    for predicted in sorted({pair[0] for pair in counts}):
        candidates = sorted(
            ((count, reference_speaker) for (speaker, reference_speaker), count in counts.items() if speaker == predicted),
            reverse=True,
        )
        if candidates:
            mapping[predicted] = candidates[0][1]
    return mapping


def reference_metrics(item: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    truth = item["truth"]
    mapping = reference_mapping(data["reference"])
    grade = str(truth.get("grade") or "unknown")
    outcome = truth.get("outcome")
    candidate = item["shadow"].get("speaker_id")
    mapped_candidate = mapping.get(str(candidate)) if candidate else None
    mismatch = truth.get("candidate_correct") is False
    unmapped_outcome = (
        grade == "independent_machine_reference"
        and outcome is not None
        and str(outcome) not in set(mapping.values())
    )
    return {
        "grade": grade,
        "outcome_available": outcome is not None,
        "eligible_for_promotion": truth.get("eligible_for_promotion") is True,
        "candidate_correct": truth.get("candidate_correct"),
        "mismatch": mismatch,
        "mapped_candidate_outcome": mapped_candidate,
        "outcome_mapped_to_enrollment": not unmapped_outcome if outcome is not None else None,
        "granularity": "utterance_level" if grade == "independent_machine_reference" else "item_or_topology",
    }


def classify_cause(
    item: dict[str, Any],
    audio: dict[str, Any],
    interval: dict[str, Any],
    enrollment: dict[str, Any],
    reference: dict[str, Any],
    policy: dict[str, Any],
) -> tuple[str, list[str], str]:
    shadow = item["shadow"]
    identity = policy["measurement"]["identity"]
    embedding_failed = shadow.get("reason") == "embedding_unavailable"
    if embedding_failed or not audio["speech_supported"]:
        primary = "insufficient_audio_evidence"
        confidence = "high" if audio["core_rms_dbfs"] <= policy["measurement"]["audio"]["silence_rms_dbfs"] else "medium"
    elif interval["risky"]:
        primary = "interval_boundary_or_mixed_speech"
        confidence = "high" if len(interval["reasons"]) >= 2 else "medium"
    elif enrollment["unstable"]:
        primary = "enrollment_instability"
        confidence = "high" if enrollment["decision_consistency_ratio"] is not None and enrollment["decision_consistency_ratio"] < 0.5 else "medium"
    elif reference["mismatch"] and reference["outcome_mapped_to_enrollment"] is False:
        primary = "independent_reference_unmapped_speaker"
        confidence = "medium"
    elif reference["mismatch"]:
        primary = "identity_reference_conflict"
        confidence = "medium"
    elif shadow.get("speaker_id") is None and (
        shadow.get("similarity") is None
        or float(shadow["similarity"]) < float(identity["minimum_similarity"])
    ):
        primary = "identity_similarity_limit"
        confidence = "high"
    elif shadow.get("speaker_id") is None and (
        shadow.get("margin") is None
        or float(shadow["margin"]) < float(identity["minimum_margin"])
    ):
        primary = "identity_margin_limit"
        confidence = "high"
    elif shadow.get("speaker_id") is not None and reference["candidate_correct"] is True:
        primary = "accepted_reference_supported"
        confidence = "medium" if reference["grade"] != "human_reviewed" else "high"
    elif shadow.get("speaker_id") is not None and not reference["outcome_available"]:
        primary = "accepted_without_direct_reference"
        confidence = "high"
    else:
        primary = "evidence_bound"
        confidence = "low"

    secondary = []
    if audio["exact_span_sec"] < float(policy["measurement"]["audio"]["minimum_core_sec"]):
        secondary.append("short_or_zero_word_interval")
    if not audio["speech_supported"]:
        secondary.append("weak_acoustic_activity")
    secondary.extend(interval["reasons"])
    if enrollment["unstable"]:
        secondary.append("leave_one_out_instability")
    if not reference["outcome_available"]:
        secondary.append("no_contextual_truth")
    elif reference["grade"] != "human_reviewed":
        secondary.append(f"{reference['grade']}_only")
    if reference["mismatch"]:
        secondary.append("candidate_reference_disagreement")
    if shadow.get("similarity") is not None and float(shadow["similarity"]) < float(identity["minimum_similarity"]):
        secondary.append("similarity_below_frozen_threshold")
    if shadow.get("margin") is not None and float(shadow["margin"]) < float(identity["minimum_margin"]):
        secondary.append("margin_below_frozen_threshold")
    return primary, sorted(set(secondary)), confidence


def build_item_diagnostics(
    data: dict[str, Any], policy: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    enrollment_rows, enrollment_by_pair, groups = enrollment_diagnostics(data, policy)
    items = []
    explanations = []
    error_by_key = data["errors"]
    for item in data["items"]:
        audio = audio_metrics(item, policy)
        interval = interval_metrics(item, audio, data, policy)
        enrollment = item_enrollment_metrics(
            item, data, policy, groups, enrollment_by_pair
        )
        reference = reference_metrics(item, data)
        primary, secondary, confidence = classify_cause(
            item, audio, interval, enrollment, reference, policy
        )
        failure_scope = (
            item["shadow"].get("speaker_id") is None
            or item["truth"].get("candidate_correct") is False
        )
        row = {
            "schema": ITEM_SCHEMA,
            "session_id": item["session_id"],
            "item_id": item["item_id"],
            "item_sha256": item["item_sha256"],
            "utterance_id": item["utterance_id"],
            "word_ids": item["word_ids"],
            "word_count": item["word_count"],
            "start": item["start"],
            "end": item["end"],
            "coverage_weight_sec": item["coverage_weight_sec"],
            "audio": audio,
            "interval": interval,
            "enrollment": enrollment,
            "identity": {
                "embedding_key": item["embedding"]["key"],
                "embedding_sha256": item["embedding"].get("sha256"),
                "embedding_error": error_by_key.get(str(item["embedding"]["key"])),
                "speaker_id": item["shadow"].get("speaker_id"),
                "top_speaker_id": item["shadow"].get("top_speaker_id"),
                "similarity": item["shadow"].get("similarity"),
                "margin": item["shadow"].get("margin"),
                "reason": item["shadow"].get("reason"),
            },
            "reference": reference,
            "failure_scope": failure_scope,
            "classification": {
                "primary_cause": primary,
                "secondary_causes": secondary,
                "confidence": confidence,
            },
            "provenance": {
                "frozen_minimum_similarity": policy["measurement"]["identity"]["minimum_similarity"],
                "frozen_minimum_margin": policy["measurement"]["identity"]["minimum_margin"],
                "threshold_tuned": False,
                "human_name_inferred": False,
                "cross_session_voice_linked": False,
            },
        }
        items.append(row)

        if item["shadow"].get("reason") == "embedding_unavailable":
            explanation = (
                "silent_audio_confirmed"
                if audio["core_rms_dbfs"] <= float(policy["measurement"]["audio"]["silence_rms_dbfs"])
                else "embedding_failure_with_residual_audio_activity"
            )
            explanations.append({
                "schema": REFERENCE_SCHEMA,
                "type": "embedding_failure",
                "session_id": item["session_id"],
                "item_id": item["item_id"],
                "word_ids": item["word_ids"],
                "word_count": item["word_count"],
                "explanation": explanation,
                "audio_sha256": audio["sha256"],
                "core_rms_dbfs": audio["core_rms_dbfs"],
                "active_frame_ratio": audio["active_frame_ratio"],
                "upstream_error": error_by_key.get(str(item["embedding"]["key"])),
                "human_truth_available": False,
            })
        if reference["mismatch"]:
            mismatch_type = (
                "independent_reference_unmapped_speaker"
                if reference["outcome_mapped_to_enrollment"] is False
                else "identity_reference_conflict"
            )
            explanations.append({
                "schema": REFERENCE_SCHEMA,
                "type": "independent_reference_mismatch",
                "session_id": item["session_id"],
                "item_id": item["item_id"],
                "utterance_id": item["utterance_id"],
                "word_ids": item["word_ids"],
                "word_count": item["word_count"],
                "explanation": mismatch_type,
                "reference_granularity": reference["granularity"],
                "candidate_speaker_id": item["shadow"].get("speaker_id"),
                "mapped_candidate_outcome": reference["mapped_candidate_outcome"],
                "reference_outcome": item["truth"].get("outcome"),
                "interval_risky": interval["risky"],
                "enrollment_unstable": enrollment["unstable"],
                "human_truth_available": False,
            })
    return items, enrollment_rows, sorted(
        explanations,
        key=lambda row: (str(row["type"]), str(row["session_id"]), str(row["item_id"])),
    )


def aggregate_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row["classification"]["primary_cause"])].append(row)
    result = []
    for cause in sorted(groups):
        selected = groups[cause]
        result.append({
            "cause": cause,
            "items": len(selected),
            "words": sum(int(row["word_count"]) for row in selected),
            "seconds": round(sum(float(row["coverage_weight_sec"]) for row in selected), 6),
            "failure_items": sum(bool(row["failure_scope"]) for row in selected),
            "failure_words": sum(int(row["word_count"]) for row in selected if row["failure_scope"]),
            "failure_seconds": round(
                sum(float(row["coverage_weight_sec"]) for row in selected if row["failure_scope"]),
                6,
            ),
        })
    return result


def axis_rows(rows: list[dict[str, Any]], policy: dict[str, Any]) -> list[dict[str, Any]]:
    failures = [row for row in rows if row["failure_scope"]]
    total_items = len(failures)
    total_seconds = sum(float(row["coverage_weight_sec"]) for row in failures)
    mapping = policy["classification"]["technical_axis_map"]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in failures:
        axis = mapping.get(str(row["classification"]["primary_cause"]))
        if axis:
            grouped[str(axis)].append(row)
    result = []
    for axis in ("interval_purification", "enrollment_hardening", "identity_backend"):
        selected = grouped.get(axis, [])
        items = len(selected)
        seconds = sum(float(row["coverage_weight_sec"]) for row in selected)
        item_ratio = items / total_items if total_items else 0.0
        seconds_ratio = seconds / total_seconds if total_seconds else 0.0
        result.append({
            "axis": axis,
            "items": items,
            "seconds": round(seconds, 6),
            "item_ratio": round(item_ratio, 6),
            "seconds_ratio": round(seconds_ratio, 6),
            "material_score": round(min(item_ratio, seconds_ratio), 6),
        })
    result.sort(key=lambda row: (-float(row["material_score"]), str(row["axis"])))
    return result


def choose_decision(
    rows: list[dict[str, Any]], axes: list[dict[str, Any]], invariants: dict[str, bool], policy: dict[str, Any]
) -> tuple[str, dict[str, Any]]:
    decision = policy["decision"]
    failures = [row for row in rows if row["failure_scope"]]
    explained = [row for row in failures if row["classification"]["primary_cause"] != "evidence_bound"]
    explained_ratio = len(explained) / len(failures) if failures else 0.0
    reference_gap = sum(not row["reference"]["outcome_available"] for row in rows) / len(rows) if rows else 0.0
    top = axes[0]
    second = axes[1] if len(axes) > 1 else {"material_score": 0.0}
    top_material = (
        float(top["item_ratio"]) >= float(decision["minimum_technical_item_ratio"])
        and float(top["seconds_ratio"]) >= float(decision["minimum_technical_seconds_ratio"])
    )
    dominant = (
        float(top["material_score"]) - float(second["material_score"])
        >= float(decision["minimum_axis_dominance_margin"])
    )
    explained_enough = explained_ratio >= float(decision["minimum_explained_failure_item_ratio"])
    if not all(invariants.values()) or not explained_enough:
        outcome = "EVIDENCE_BOUND"
        reason = "input_conservation_or_explanation_gate_failed"
    elif top_material and dominant:
        outcome = {
            "interval_purification": "ADVANCE_INTERVAL_PURIFICATION",
            "enrollment_hardening": "ADVANCE_ENROLLMENT_HARDENING",
            "identity_backend": "ADVANCE_IDENTITY_BACKEND",
        }[str(top["axis"])]
        reason = "material_dominant_technical_axis"
    elif reference_gap >= float(decision["minimum_reference_gap_item_ratio"]):
        outcome = "ADVANCE_REFERENCE_ACQUISITION"
        reason = "material_reference_gap_without_dominant_technical_axis"
    else:
        outcome = "EVIDENCE_BOUND"
        reason = "no_material_dominant_axis"
    return outcome, {
        "reason": reason,
        "top_axis": top["axis"],
        "top_material": top_material,
        "dominant": dominant,
        "axis_dominance_margin": round(float(top["material_score"]) - float(second["material_score"]), 6),
        "explained_failure_item_ratio": round(explained_ratio, 6),
        "reference_gap_item_ratio": round(reference_gap, 6),
    }


def public_session_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["session_id"])].append(row)
    result = []
    for index, session_id in enumerate(sorted(grouped), 1):
        selected = grouped[session_id]
        result.append({
            "scenario": f"session_{index:02d}",
            "items": len(selected),
            "words": sum(int(row["word_count"]) for row in selected),
            "seconds": round(sum(float(row["coverage_weight_sec"]) for row in selected), 6),
            "accepted_items": sum(row["identity"]["speaker_id"] is not None for row in selected),
            "failure_items": sum(bool(row["failure_scope"]) for row in selected),
            "primary_causes": dict(sorted(Counter(row["classification"]["primary_cause"] for row in selected).items())),
        })
    return result


def report_for(
    policy: dict[str, Any],
    manifest: dict[str, Any],
    rows: list[dict[str, Any]],
    enrollment: list[dict[str, Any]],
    explanations: list[dict[str, Any]],
    deterministic: bool,
) -> dict[str, Any]:
    failures = [row for row in rows if row["failure_scope"]]
    reference_mismatches = [row for row in explanations if row["type"] == "independent_reference_mismatch"]
    embedding_failures = [row for row in explanations if row["type"] == "embedding_failure"]
    expected = policy["source"]
    word_ids = [
        (str(row["session_id"]), str(word_id))
        for row in rows
        for word_id in row["word_ids"]
    ]
    invariants = {
        "all_items_accounted_once": len(rows) == int(expected["expected_items"])
        and len({(row["session_id"], row["item_id"]) for row in rows}) == len(rows),
        "all_words_accounted_once": len(word_ids) == int(expected["expected_words"])
        and len(set(word_ids)) == len(word_ids),
        "accepted_and_abstention_counts_unchanged": (
            sum(row["identity"]["speaker_id"] is not None for row in rows)
            == int(expected["expected_accepted_items"])
            and sum(row["identity"]["speaker_id"] is None for row in rows)
            == int(expected["expected_abstentions"])
        ),
        "independent_reference_wrong_words_explained": (
            sum(int(row["word_count"]) for row in reference_mismatches)
            == int(expected["expected_independent_reference_wrong_words"])
        ),
        "two_embedding_failures_explained": len(embedding_failures) == int(expected["expected_embedding_failures"]),
        "frozen_thresholds_unchanged": all(
            row["provenance"]["frozen_minimum_similarity"] == policy["measurement"]["identity"]["minimum_similarity"]
            and row["provenance"]["frozen_minimum_margin"] == policy["measurement"]["identity"]["minimum_margin"]
            and row["provenance"]["threshold_tuned"] is False
            for row in rows
        ),
        "deterministic_analysis": deterministic,
        "production_guards_frozen": bool(manifest.get("production_guard_fingerprint")),
    }
    axes = axis_rows(rows, policy)
    decision, decision_evidence = choose_decision(rows, axes, invariants, policy)
    mismatch_summary = dict(sorted(Counter(str(row["explanation"]) for row in reference_mismatches).items()))
    embedding_summary = dict(sorted(Counter(str(row["explanation"]) for row in embedding_failures).items()))
    return {
        "schema": REPORT_SCHEMA,
        "version": VERSION,
        "decision": decision,
        "source_fingerprint": manifest["source_fingerprint"],
        "scope": {
            "items": len(rows),
            "words": sum(int(row["word_count"]) for row in rows),
            "seconds": round(sum(float(row["coverage_weight_sec"]) for row in rows), 6),
            "failure_items": len(failures),
            "failure_words": sum(int(row["word_count"]) for row in failures),
            "failure_seconds": round(sum(float(row["coverage_weight_sec"]) for row in failures), 6),
            "enrollment_exemplars": sum(int(row["exemplar_count"]) for row in enrollment),
        },
        "cause_distribution": aggregate_rows(rows),
        "technical_axes": axes,
        "decision_evidence": decision_evidence,
        "reference": {
            "human_reviewed_items": sum(row["reference"]["grade"] == "human_reviewed" for row in rows),
            "contextual_truth_items": sum(row["reference"]["outcome_available"] for row in rows),
            "independent_reference_mismatch_words": sum(int(row["word_count"]) for row in reference_mismatches),
            "mismatch_explanations": mismatch_summary,
            "machine_reference_is_human_truth": False,
        },
        "embedding_failures": {
            "items": len(embedding_failures),
            "explanations": embedding_summary,
        },
        "enrollment": {
            "speaker_profiles": len(enrollment),
            "stable_profiles": sum(row["stable"] for row in enrollment),
            "unstable_profiles": sum(not row["stable"] for row in enrollment),
        },
        "sessions": public_session_rows(rows),
        "invariants": invariants,
        "safety": {
            "diagnostic_only": True,
            "production_mutated": False,
            "coverage_v3_mutated": False,
            "selected_transcript_mutated": False,
            "raw_audio_mutated": False,
            "primary_asr_mutated": False,
            "echo_guard_mutated": False,
            "thresholds_tuned": False,
            "human_names_inferred": False,
            "cross_session_voice_linking": False,
            "private_values_excluded": True,
        },
        "next_action": {
            "ADVANCE_INTERVAL_PURIFICATION": "build_bounded_interval_purification_candidate",
            "ADVANCE_ENROLLMENT_HARDENING": "build_session_local_enrollment_hardening_candidate",
            "ADVANCE_REFERENCE_ACQUISITION": "acquire_direct_reviewed_real_speaker_truth",
            "ADVANCE_IDENTITY_BACKEND": "compare_one_new_identity_backend_on_frozen_inputs",
            "EVIDENCE_BOUND": "keep_coverage_v3_and_stop_this_branch",
        }[decision],
    }


def markdown_report(report: dict[str, Any]) -> str:
    scope = report["scope"]
    evidence = report["decision_evidence"]
    lines = [
        "# Remote Speaker Shadow Error Decomposition v1",
        "",
        f"Decision: `{report['decision']}`",
        "",
        "## Scope",
        "",
        f"- items: `{scope['items']}`; words: `{scope['words']}`; seconds: `{scope['seconds']:.6f}`;",
        f"- failure scope: `{scope['failure_items']}` items / `{scope['failure_words']}` words / `{scope['failure_seconds']:.6f}s`;",
        f"- enrollment exemplars: `{scope['enrollment_exemplars']}`.",
        "",
        "## Technical Axes",
        "",
        "| Axis | Items | Seconds | Item ratio | Seconds ratio | Material score |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in report["technical_axes"]:
        lines.append(
            f"| `{row['axis']}` | {row['items']} | {row['seconds']:.6f} | "
            f"{row['item_ratio']:.6f} | {row['seconds_ratio']:.6f} | {row['material_score']:.6f} |"
        )
    lines.extend([
        "",
        "## Cause Distribution",
        "",
        "| Primary cause | Items | Words | Seconds | Failure items |",
        "|---|---:|---:|---:|---:|",
    ])
    for row in report["cause_distribution"]:
        lines.append(
            f"| `{row['cause']}` | {row['items']} | {row['words']} | "
            f"{row['seconds']:.6f} | {row['failure_items']} |"
        )
    lines.extend([
        "",
        "## Decision Evidence",
        "",
        f"- reason: `{evidence['reason']}`;",
        f"- top axis: `{evidence['top_axis']}`; material: `{evidence['top_material']}`; dominant: `{evidence['dominant']}`;",
        f"- dominance margin: `{evidence['axis_dominance_margin']:.6f}`;",
        f"- explained failure items: `{evidence['explained_failure_item_ratio']:.6f}`;",
        f"- items without contextual truth: `{evidence['reference_gap_item_ratio']:.6f}`.",
        "",
        "## Reference And Failures",
        "",
        f"- independent-reference mismatch words: `{report['reference']['independent_reference_mismatch_words']}`;",
        f"- mismatch explanations: `{json.dumps(report['reference']['mismatch_explanations'], sort_keys=True)}`;",
        f"- embedding failures: `{report['embedding_failures']['items']}`; explanations: "
        f"`{json.dumps(report['embedding_failures']['explanations'], sort_keys=True)}`;",
        "- the independent machine reference is utterance-level evidence, not human truth.",
        "",
        "## Safety",
        "",
        "Coverage v3, selected transcripts, raw CAF, primary ASR and Echo Guard remain unchanged. "
        "The analysis is diagnostic-only, keeps anonymous session-local speaker IDs private and "
        "does not tune the frozen ECAPA thresholds.",
        "",
    ])
    return "\n".join(lines)


def analysis_bundle(
    data: dict[str, Any], policy: dict[str, Any], manifest: dict[str, Any]
) -> dict[str, Any]:
    items, enrollment, explanations = build_item_diagnostics(data, policy)
    repeat_items, repeat_enrollment, repeat_explanations = build_item_diagnostics(data, policy)
    deterministic = (
        jsonl_bytes(items) == jsonl_bytes(repeat_items)
        and jsonl_bytes(enrollment) == jsonl_bytes(repeat_enrollment)
        and jsonl_bytes(explanations) == jsonl_bytes(repeat_explanations)
    )
    report = report_for(
        policy, manifest, items, enrollment, explanations, deterministic
    )
    markdown = markdown_report(report).encode("utf-8")
    return {
        "items": items,
        "enrollment": enrollment,
        "explanations": explanations,
        "report": report,
        "markdown": markdown,
    }


def output_payloads(bundle: dict[str, Any]) -> dict[str, bytes]:
    return {
        "private/item_error_decomposition.jsonl": jsonl_bytes(bundle["items"]),
        "private/enrollment_diagnostics.jsonl": jsonl_bytes(bundle["enrollment"]),
        "private/reference_explanations.jsonl": jsonl_bytes(bundle["explanations"]),
        "remote_speaker_shadow_error_decomposition_report.json": pretty_json(bundle["report"]),
        "remote_speaker_shadow_error_decomposition_report.md": bundle["markdown"],
    }


def write_bundle(out: Path, bundle: dict[str, Any]) -> None:
    for relative, payload in output_payloads(bundle).items():
        atomic_write(out / relative, payload)


def replay_bundle(out: Path, bundle: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    expected = output_payloads(bundle)
    matches = {}
    hashes = {}
    for relative, payload in expected.items():
        path = out / relative
        matches[relative] = path.is_file() and path.read_bytes() == payload
        hashes[relative] = sha256_bytes(payload)
    byte_identical = all(matches.values())
    report = {
        "schema": REPLAY_SCHEMA,
        "version": VERSION,
        "decision": "DETERMINISTIC_REPLAY_VERIFIED" if byte_identical else "REPLAY_MISMATCH",
        "analysis_decision": bundle["report"]["decision"],
        "source_fingerprint": manifest["source_fingerprint"],
        "byte_identical": byte_identical,
        "matches": matches,
        "reproduced_sha256": hashes,
        "production_mutated": False,
    }
    write_json(out / "replay_report.json", report)
    if not byte_identical:
        raise DecompositionError("deterministic_replay_mismatch")
    return report


def tracked_manifest(policy_path: Path, out: Path) -> dict[str, Any]:
    report = read_json(out / "remote_speaker_shadow_error_decomposition_report.json")
    replay = read_json(out / "replay_report.json")
    artifacts = {}
    for role, path in {
        "policy": policy_path,
        "public_input_manifest": out / "input_manifest.public.json",
        "report": out / "remote_speaker_shadow_error_decomposition_report.json",
        "markdown_report": out / "remote_speaker_shadow_error_decomposition_report.md",
        "replay_report": out / "replay_report.json",
    }.items():
        artifacts[role] = fingerprint(path, role)
    return {
        "schema": TRACKED_SCHEMA,
        "version": VERSION,
        "decision": report["decision"],
        "source_fingerprint": report["source_fingerprint"],
        "scope": report["scope"],
        "technical_axes": report["technical_axes"],
        "decision_evidence": report["decision_evidence"],
        "reference": report["reference"],
        "embedding_failures": report["embedding_failures"],
        "invariants": report["invariants"],
        "safety": report["safety"],
        "replay_verified": replay.get("byte_identical") is True,
        "artifacts": artifacts,
        "private_values_excluded": True,
    }


def status(out: Path) -> dict[str, Any]:
    report_path = out / "remote_speaker_shadow_error_decomposition_report.json"
    report = read_json(report_path) if report_path.is_file() else None
    replay_path = out / "replay_report.json"
    replay = read_json(replay_path) if replay_path.is_file() else None
    return {
        "schema": STATUS_SCHEMA,
        "frozen": (out / "private/input_manifest.json").is_file(),
        "analyzed": report is not None,
        "replayed": replay is not None and replay.get("byte_identical") is True,
        "decision": report.get("decision") if report else None,
        "next_action": report.get("next_action") if report else None,
    }


def print_status(payload: dict[str, Any]) -> None:
    print(f"frozen: {str(payload['frozen']).lower()}")
    print(f"analyzed: {str(payload['analyzed']).lower()}")
    print(f"replayed: {str(payload['replayed']).lower()}")
    print(f"decision: {payload['decision']}")
    print(f"next_action: {payload['next_action']}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=("preflight", "freeze", "analyze", "status", "replay", "finalize", "all"),
    )
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--write-manifest", type=Path, default=DEFAULT_TRACKED)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    policy_path = args.policy.expanduser().resolve()
    out = args.out_dir.expanduser().resolve()
    tracked_path = args.write_manifest.expanduser().resolve()

    if args.action == "status":
        print_status(status(out))
        return 0
    if args.action == "finalize":
        payload = tracked_manifest(policy_path, out)
        write_json(tracked_path, payload)
        print(f"manifest: {portable(tracked_path)}")
        print(f"decision: {payload['decision']}")
        return 0

    policy = load_policy(policy_path)
    paths = source_paths(policy)
    data = load_data(policy, paths)
    private_manifest, public_manifest = build_input_manifest(
        policy_path, policy, paths, data
    )

    if args.action == "preflight":
        print("preflight: passed")
        print(f"items: {private_manifest['counts']['items']}")
        print(f"words: {private_manifest['counts']['words']}")
        print(f"source_fingerprint: {private_manifest['source_fingerprint']}")
        return 0

    if args.action in {"freeze", "all"}:
        write_json(out / "private/input_manifest.json", private_manifest)
        write_json(out / "input_manifest.public.json", public_manifest)
        if args.action == "freeze":
            print(f"frozen: {portable(out / 'private/input_manifest.json')}")
            return 0
    else:
        private_manifest, public_manifest = verify_frozen_manifest(
            out / "private/input_manifest.json", policy_path, policy, paths, data
        )
        if not (out / "input_manifest.public.json").is_file() or canonical_json(
            read_json(out / "input_manifest.public.json")
        ) != canonical_json(public_manifest):
            raise DecompositionError("public_input_manifest_mismatch")

    bundle = analysis_bundle(data, policy, private_manifest)
    if args.action in {"analyze", "all"}:
        write_bundle(out, bundle)
        if args.action == "analyze":
            print(f"report: {portable(out / 'remote_speaker_shadow_error_decomposition_report.json')}")
            print(f"decision: {bundle['report']['decision']}")
            return 0

    if args.action in {"replay", "all"}:
        replay = replay_bundle(out, bundle, private_manifest)
        if args.action == "replay":
            print(f"replay: {replay['decision']}")
            return 0

    if args.action == "all":
        payload = tracked_manifest(policy_path, out)
        write_json(tracked_path, payload)
        print(f"report: {portable(out / 'remote_speaker_shadow_error_decomposition_report.json')}")
        print(f"decision: {payload['decision']}")
        print(f"manifest: {portable(tracked_path)}")
        return 0
    raise DecompositionError(f"unsupported_action:{args.action}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DecompositionError as error:
        print(f"error: {error}", file=os.sys.stderr)
        raise SystemExit(2) from error
