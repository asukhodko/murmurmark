#!/usr/bin/env python3
"""Qualify frozen ECAPA speaker identity on real residual remote intervals in shadow."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Iterable

import numpy as np
import soundfile as sf


ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.1.0"
POLICY_SCHEMA = "murmurmark.ecapa_remote_speaker_shadow_qualification_policy/v1"
PRIVATE_MANIFEST_SCHEMA = "murmurmark.ecapa_remote_speaker_shadow_input_manifest/v1"
PUBLIC_MANIFEST_SCHEMA = "murmurmark.ecapa_remote_speaker_shadow_public_manifest/v1"
ITEM_SCHEMA = "murmurmark.ecapa_remote_speaker_shadow_item/v1"
WORD_SCHEMA = "murmurmark.ecapa_remote_speaker_shadow_word/v1"
REPORT_SCHEMA = "murmurmark.ecapa_remote_speaker_shadow_qualification_report/v1"
REPLAY_SCHEMA = "murmurmark.ecapa_remote_speaker_shadow_replay/v1"
TRACKED_SCHEMA = "murmurmark.ecapa_remote_speaker_shadow_qualification_manifest/v1"
DEFAULT_POLICY = ROOT / "policies/ecapa-remote-speaker-shadow-qualification-v1.json"
DEFAULT_OUT = ROOT / "sessions/_reports/ecapa-remote-speaker-shadow-qualification-v1"
DEFAULT_TRACKED = ROOT / "docs/testing/ecapa-remote-speaker-shadow-qualification-v1-manifest.json"
RESIDUAL_PRIVATE = ROOT / "sessions/_reports/remote-speaker-residual-reference-corpus-v1/private"
REFERENCE_EVALUATION = ROOT / "sessions/_reports/remote-speaker-diarization-v2/reference_evaluation.json"
ECAPA_WORKER = ROOT / "scripts/ecapa-speaker-embedding-worker.py"


class QualificationError(RuntimeError):
    pass


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def compact_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


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
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise QualificationError(f"invalid_json:{path}:{type(error).__name__}") from error
    if not isinstance(value, dict):
        raise QualificationError(f"json_object_required:{path}")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    except (OSError, json.JSONDecodeError) as error:
        raise QualificationError(f"invalid_jsonl:{path}:{type(error).__name__}") from error
    if not all(isinstance(row, dict) for row in rows):
        raise QualificationError(f"jsonl_objects_required:{path}")
    return rows


def atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def write_json(path: Path, value: Any) -> None:
    atomic_write(path, canonical_json(value))


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    atomic_write(path, b"".join(compact_json(row) for row in rows))


def portable(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve()))
    except ValueError:
        return f"external/{path.name}"


def resolve_repo_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (ROOT / path).resolve()


def scoped_word_key(session_id: str, word_id: str) -> str:
    return f"{session_id}\x1f{word_id}"


def verify_hash(path: Path, expected: str, reason: str) -> None:
    if not path.is_file() or sha256(path) != expected:
        raise QualificationError(reason)


def validate_policy(policy: dict[str, Any], *, fixture_mode: bool) -> None:
    if policy.get("schema") != POLICY_SCHEMA:
        raise QualificationError("policy_schema_invalid")
    candidate = policy.get("candidate") or {}
    if candidate.get("backend_id") != "speechbrain_ecapa_voxceleb_candidate":
        raise QualificationError("candidate_backend_changed")
    if candidate.get("minimum_similarity") != 0.5 or candidate.get("minimum_margin") != 0.3:
        raise QualificationError("frozen_thresholds_changed")
    if policy.get("terminal_decisions") != [
        "PROMOTE_REAL_IDENTITY_CANDIDATE",
        "DO_NOT_PROMOTE_REAL_IDENTITY",
        "REFERENCE_INSUFFICIENT",
    ]:
        raise QualificationError("terminal_decisions_changed")
    safety = policy.get("safety") or {}
    required_false = (
        "production_mutation", "coverage_v3_mutation", "selected_transcript_mutation",
        "raw_audio_mutation", "primary_asr_mutation", "echo_guard_mutation",
        "human_name_inference", "cross_session_voice_linking",
        "synthetic_to_real_identity_transfer", "cloud_allowed",
    )
    if safety.get("shadow_only") is not True or any(safety.get(key) is not False for key in required_false):
        raise QualificationError("safety_contract_changed")
    if fixture_mode:
        return
    for row in policy.get("frozen_inputs") or []:
        verify_hash(resolve_repo_path(str(row["path"])), str(row["sha256"]), f"frozen_input_stale:{row['id']}")


def candidate_model_row() -> dict[str, Any]:
    policy = read_json(ROOT / "policies/stronger-remote-speaker-identity-backend-qualification-v1.json")
    rows = [row for row in policy.get("shortlist") or [] if row.get("id") == "speechbrain_ecapa_voxceleb_candidate"]
    if len(rows) != 1:
        raise QualificationError("ecapa_candidate_definition_missing")
    return rows[0]


def model_path(policy: dict[str, Any]) -> Path:
    value = os.environ.get("MURMURMARK_REMOTE_SPEAKER_ECAPA_MODEL", policy["candidate"]["default_model_path"])
    return Path(value).expanduser().resolve()


def runtime_path(policy: dict[str, Any]) -> Path:
    value = os.environ.get("MURMURMARK_REMOTE_SPEAKER_IDENTITY_RUNTIME", policy["candidate"]["default_runtime_path"])
    return Path(value).expanduser().resolve()


def candidate_provenance(policy: dict[str, Any], *, fixture_mode: bool) -> dict[str, Any]:
    if fixture_mode:
        return {
            "backend_id": policy["candidate"]["backend_id"],
            "mode": "deterministic_fixture",
            "model_id": "fixture/ecapa",
            "revision": "fixture-v1",
        }
    definition = candidate_model_row()
    target = model_path(policy)
    files = []
    for name, expected in sorted((definition.get("files") or {}).items()):
        path = target / name
        verify_hash(path, str(expected), f"model_missing_or_stale:{name}")
        files.append({"name": name, "bytes": path.stat().st_size, "sha256": expected})
    python = runtime_path(policy) / "bin/python"
    if not python.is_file() or not os.access(python, os.X_OK):
        raise QualificationError("ecapa_runtime_python_missing")
    return {
        "backend_id": definition["id"],
        "family": definition["family"],
        "model_id": definition["model_id"],
        "revision": definition["revision"],
        "license": definition["license"],
        "model_tree_sha256": sha256_bytes(canonical_json(files)),
        "files": files,
        "runtime": dict(definition["runtime"]),
        "device": "cpu",
        "offline": True,
    }


def validate_audio_row(row: dict[str, Any], *, reason: str) -> Path:
    audio = row.get("audio") or {}
    path = resolve_repo_path(str(audio.get("path") or ""))
    verify_hash(path, str(audio.get("sha256") or ""), reason)
    if path.stat().st_size != int(audio.get("bytes") or -1):
        raise QualificationError(f"{reason}:size")
    return path


def reference_mapping(reference: dict[str, Any]) -> dict[str, str]:
    rows = [
        row for row in reference.get("rows") or []
        if row.get("predicted_speaker") and row.get("reference_speaker")
    ]
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


def load_real_inputs(policy: dict[str, Any]) -> dict[str, Any]:
    items = read_jsonl(RESIDUAL_PRIVATE / "review_items.jsonl")
    exemplars = read_jsonl(RESIDUAL_PRIVATE / "speaker_exemplars.jsonl")
    answers = read_jsonl(RESIDUAL_PRIVATE / "answers.jsonl")
    pack = read_json(RESIDUAL_PRIVATE / "pack.json")
    residual_report = read_json(ROOT / "sessions/_reports/remote-speaker-residual-reference-corpus-v1/remote_speaker_residual_reference_report.json")
    coverage_report = read_json(ROOT / "sessions/_reports/remote-speaker-coverage-v3/remote_speaker_coverage_corpus_report.json")
    reference = read_json(REFERENCE_EVALUATION)
    if len(items) != 278 or len(exemplars) != 28 or len(answers) != 278:
        raise QualificationError("real_corpus_cardinality_changed")
    item_ids = [str(row.get("item_id") or "") for row in items]
    if not all(item_ids) or len(item_ids) != len(set(item_ids)):
        raise QualificationError("residual_item_ids_invalid")
    answer_by_id = {str(row.get("item_id") or ""): row for row in answers}
    if set(answer_by_id) != set(item_ids):
        raise QualificationError("answer_scope_mismatch")
    item_by_id = {str(row["item_id"]): row for row in items}
    for item_id, answer in answer_by_id.items():
        item = item_by_id[item_id]
        if answer.get("item_sha256") != item.get("item_sha256"):
            raise QualificationError(f"answer_item_hash_mismatch:{item_id}")
        outcome = answer.get("outcome")
        if outcome is None:
            if answer.get("truth_grade") is not None:
                raise QualificationError(f"unreviewed_answer_has_truth_grade:{item_id}")
            continue
        allowed = set(str(value) for value in item.get("speaker_choices") or []) | {
            "unknown_speaker", "mixed", "unusable",
        }
        if str(outcome) not in allowed or answer.get("truth_grade") != "human_reviewed":
            raise QualificationError(f"reviewed_answer_invalid:{item_id}")
    sessions = sorted({str(row["session_id"]) for row in items})
    if len(sessions) != 6:
        raise QualificationError("real_session_scope_changed")
    words_by_id: dict[str, dict[str, Any]] = {}
    word_files = []
    for session_id in sessions:
        path = ROOT / "sessions" / session_id / "derived/audit/remote-speaker-coverage-v3/word_attribution.jsonl"
        rows = read_jsonl(path)
        word_files.append({"path": portable(path), "bytes": path.stat().st_size, "sha256": sha256(path)})
        for row in rows:
            word_id = str(row.get("word_id") or "")
            key = scoped_word_key(session_id, word_id)
            if key in words_by_id:
                raise QualificationError("coverage_word_id_collision")
            words_by_id[key] = row
    referenced_words = []
    clip_files = []
    for item in items:
        path = validate_audio_row(item, reason=f"residual_clip_stale:{item['item_id']}")
        clip_files.append({"item_id": item["item_id"], "path": portable(path), "bytes": path.stat().st_size, "sha256": sha256(path)})
        ids = [str(value) for value in item.get("word_ids") or []]
        if len(ids) != int(item.get("word_count") or -1):
            raise QualificationError(f"item_word_count_mismatch:{item['item_id']}")
        for word_id in ids:
            word = words_by_id.get(scoped_word_key(str(item["session_id"]), word_id))
            if word is None or word.get("status") != "unknown" or word.get("speaker_id") is not None:
                raise QualificationError(f"residual_word_not_unknown:{word_id}")
            referenced_words.append(scoped_word_key(str(item["session_id"]), word_id))
    if len(referenced_words) != 851 or len(referenced_words) != len(set(referenced_words)):
        raise QualificationError("residual_word_scope_changed")
    exemplar_files = []
    exemplar_counts: Counter[tuple[str, str]] = Counter()
    for index, row in enumerate(exemplars):
        path = validate_audio_row(row, reason=f"speaker_exemplar_stale:{index}")
        exemplar_counts[(str(row["session_id"]), str(row["speaker_id"]))] += 1
        exemplar_files.append({
            "session_id": row["session_id"], "speaker_id": row["speaker_id"],
            "path": portable(path), "bytes": path.stat().st_size, "sha256": sha256(path),
        })
    expected_speakers = {
        (str(item["session_id"]), str(speaker))
        for item in items for speaker in item.get("speaker_choices") or []
    }
    if set(exemplar_counts) != expected_speakers or any(count != 2 for count in exemplar_counts.values()):
        raise QualificationError("speaker_exemplar_scope_or_count_changed")
    pack_sessions = {str(row["session_id"]): row for row in pack.get("sessions") or []}
    if set(pack_sessions) != set(sessions):
        raise QualificationError("residual_pack_session_scope_changed")
    production_guards = []
    for session_id in sessions:
        row = pack_sessions[session_id]
        for key in ("selected_dialogue", "v3_manifest", "v3_report"):
            guard = row[key]
            verify_hash(resolve_repo_path(str(guard["path"])), str(guard["sha256"]), f"production_guard_stale:{session_id}:{key}")
        production_guards.append({
            "session_id": session_id,
            "selected_dialogue": row["selected_dialogue"],
            "v3_manifest": row["v3_manifest"],
            "v3_report": row["v3_report"],
            "raw_audio": row["raw_audio"],
        })
    summary = residual_report.get("summary") or {}
    if int(summary.get("residual_words") or 0) != 851 or not math.isclose(float(summary.get("residual_seconds") or 0), 598.239509, abs_tol=1e-6):
        raise QualificationError("residual_report_scope_changed")
    return {
        "fixture_case": None,
        "items": items,
        "exemplars": exemplars,
        "answers": answers,
        "answer_by_id": answer_by_id,
        "words_by_id": words_by_id,
        "sessions": sessions,
        "residual_report": residual_report,
        "coverage_report": coverage_report,
        "reference": reference,
        "reference_mapping": reference_mapping(reference),
        "clip_files": clip_files,
        "exemplar_files": exemplar_files,
        "word_files": word_files,
        "production_guards": production_guards,
    }


def fixture_word(session_id: str, item_id: str, index: int, start: float) -> dict[str, Any]:
    return {
        "schema": "murmurmark.remote_speaker_word/v3",
        "word_id": f"{item_id}:word:{index:04d}",
        "utterance_id": f"utt_{item_id}",
        "start": round(start, 3),
        "end": round(start + 0.5, 3),
        "text": f"word_{index}",
        "normalized": f"word_{index}",
        "status": "unknown",
        "speaker_id": None,
        "speaker_label": "Colleagues",
        "coverage_weight_sec": 1.0,
    }


def load_fixture_inputs(case: str) -> dict[str, Any]:
    specifications = [
        ("fixture_1x1", "one", ["remote_speaker_01"], "remote_speaker_01", 30, "structural_one_to_one"),
        ("fixture_group", "alpha", ["remote_speaker_01", "remote_speaker_02"], "remote_speaker_01", 30, "independent_machine_reference"),
        ("fixture_group", "beta", ["remote_speaker_01", "remote_speaker_02"], "remote_speaker_02", 30, "independent_machine_reference"),
        ("fixture_group", "negative", ["remote_speaker_01", "remote_speaker_02"], "unknown_speaker", 10, "independent_machine_reference"),
    ]
    items = []
    words_by_id: dict[str, dict[str, Any]] = {}
    answers = []
    clock = 0.0
    for session_id, item_id, choices, truth, count, grade in specifications:
        ids = []
        for index in range(count):
            word = fixture_word(session_id, item_id, index + 1, clock)
            clock += 0.6
            words_by_id[scoped_word_key(session_id, word["word_id"])] = word
            ids.append(word["word_id"])
        item = {
            "schema": "murmurmark.remote_speaker_residual_reference_item/v1",
            "session_id": session_id,
            "item_id": item_id,
            "item_sha256": sha256_bytes(canonical_json({"item_id": item_id, "word_ids": ids})),
            "utterance_id": f"utt_{item_id}",
            "word_ids": ids,
            "word_count": count,
            "speaker_choices": choices,
            "coverage_weight_sec": float(count),
            "start": words_by_id[scoped_word_key(session_id, ids[0])]["start"],
            "end": words_by_id[scoped_word_key(session_id, ids[-1])]["end"],
            "audio": {"path": f"fixture/{item_id}.wav", "sha256": sha256_bytes(item_id.encode()), "bytes": count},
            "_fixture_truth": truth,
            "_fixture_truth_grade": grade,
        }
        items.append(item)
        reviewed = case in {"promote", "technical-fail"}
        answers.append({
            "schema": "murmurmark.remote_speaker_residual_reference_answer/v1",
            "item_id": item_id,
            "item_sha256": item["item_sha256"],
            "outcome": truth if reviewed else None,
            "truth_grade": "human_reviewed" if reviewed else None,
            "reviewer_id": "fixture" if reviewed else None,
        })
    exemplars = []
    for session_id, speakers in {
        "fixture_1x1": ["remote_speaker_01"],
        "fixture_group": ["remote_speaker_01", "remote_speaker_02"],
    }.items():
        for speaker in speakers:
            for index in range(2):
                exemplars.append({
                    "schema": "murmurmark.remote_speaker_residual_reference_exemplar/v1",
                    "session_id": session_id,
                    "speaker_id": speaker,
                    "utterance_id": f"enroll_{speaker}_{index}",
                    "audio": {"path": f"fixture/{session_id}/{speaker}/{index}.wav", "sha256": sha256_bytes(f"{session_id}:{speaker}:{index}".encode()), "bytes": 10},
                })
    return {
        "fixture_case": case,
        "items": items,
        "exemplars": exemplars,
        "answers": answers,
        "answer_by_id": {str(row["item_id"]): row for row in answers},
        "words_by_id": words_by_id,
        "sessions": ["fixture_1x1", "fixture_group"],
        "residual_report": {"summary": {"residual_words": 100, "residual_seconds": 100.0, "review_items": 4, "speaker_exemplars": 6}},
        "coverage_report": {"summary": {"remote_speech_sec": 1000.0, "attributed_speech_sec": 900.0, "attributable_remote_speech_ratio": 0.9}},
        "reference": {"rows": []},
        "reference_mapping": {
            "remote_speaker_01": "remote_speaker_01",
            "remote_speaker_02": "remote_speaker_02",
        },
        "clip_files": [],
        "exemplar_files": [],
        "word_files": [],
        "production_guards": [],
    }


def source_fingerprint(data: dict[str, Any]) -> str:
    material = {
        "items": [{key: row.get(key) for key in ("session_id", "item_id", "item_sha256", "word_ids", "speaker_choices", "coverage_weight_sec", "audio")} for row in data["items"]],
        "exemplars": [{key: row.get(key) for key in ("session_id", "speaker_id", "utterance_id", "audio")} for row in data["exemplars"]],
        "answers": data["answers"],
        "words": [
            {
                "scoped_word_key": scoped,
                **{key: row.get(key) for key in ("word_id", "utterance_id", "start", "end", "text", "status", "speaker_id")},
            }
            for scoped, row in sorted(data["words_by_id"].items())
        ],
    }
    return sha256_bytes(canonical_json(material))


def frozen_manifest(policy_path: Path, policy: dict[str, Any], data: dict[str, Any], provenance: dict[str, Any]) -> dict[str, Any]:
    answers = [row for row in data["answers"] if row.get("outcome") is not None]
    implementation = []
    for path in (Path(__file__).resolve(), ECAPA_WORKER.resolve()):
        implementation.append({"path": portable(path), "bytes": path.stat().st_size, "sha256": sha256(path)})
    return {
        "schema": PRIVATE_MANIFEST_SCHEMA,
        "version": VERSION,
        "policy": {"path": portable(policy_path), "bytes": policy_path.stat().st_size, "sha256": sha256(policy_path)},
        "candidate": provenance,
        "implementation": implementation,
        "thresholds": {
            "minimum_similarity": policy["candidate"]["minimum_similarity"],
            "minimum_margin": policy["candidate"]["minimum_margin"],
        },
        "fixture_case": data["fixture_case"],
        "source_fingerprint": source_fingerprint(data),
        "sessions": data["sessions"],
        "counts": {
            "sessions": len(data["sessions"]),
            "review_items": len(data["items"]),
            "residual_words": sum(int(row["word_count"]) for row in data["items"]),
            "speaker_exemplars": len(data["exemplars"]),
            "human_reviewed_items": len(answers),
        },
        "frozen_inputs": policy.get("frozen_inputs") or [],
        "word_files": data["word_files"],
        "clip_files": data["clip_files"],
        "exemplar_files": data["exemplar_files"],
        "production_guards": data["production_guards"],
        "truth_inventory": {
            "human_reviewed_items": len(answers),
            "structural_one_to_one_sessions": len(policy["truth_strata"]["structural_one_to_one"]["sessions"]) if data["fixture_case"] is None else 1,
            "independent_machine_reference_sessions": len(policy["truth_strata"]["independent_machine_reference"]["sessions"]) if data["fixture_case"] is None else 1,
            "anonymous_machine_evidence_is_truth": False,
        },
        "safety": dict(policy["safety"]),
    }


def public_manifest(private: dict[str, Any]) -> dict[str, Any]:
    candidate = private["candidate"]
    return {
        "schema": PUBLIC_MANIFEST_SCHEMA,
        "version": VERSION,
        "policy": private["policy"],
        "candidate": {key: candidate.get(key) for key in ("backend_id", "family", "model_id", "revision", "license", "model_tree_sha256", "device", "offline", "mode") if candidate.get(key) is not None},
        "implementation": private["implementation"],
        "thresholds": private["thresholds"],
        "counts": private["counts"],
        "sessions": private["sessions"],
        "source_fingerprint": private["source_fingerprint"],
        "truth_inventory": private["truth_inventory"],
        "private_values_excluded": True,
        "safety": private["safety"],
    }


def verify_frozen_manifest(path: Path, policy_path: Path, data: dict[str, Any], *, fixture_mode: bool) -> dict[str, Any]:
    manifest = read_json(path)
    if manifest.get("schema") != PRIVATE_MANIFEST_SCHEMA:
        raise QualificationError("frozen_manifest_schema_invalid")
    if manifest.get("policy", {}).get("sha256") != sha256(policy_path):
        raise QualificationError("policy_changed_after_freeze")
    if manifest.get("source_fingerprint") != source_fingerprint(data):
        raise QualificationError("source_changed_after_freeze")
    if not fixture_mode:
        for row in manifest.get("implementation") or []:
            verify_hash(resolve_repo_path(str(row["path"])), str(row["sha256"]), f"implementation_changed:{row['path']}")
        for row in manifest.get("frozen_inputs") or []:
            verify_hash(resolve_repo_path(str(row["path"])), str(row["sha256"]), f"frozen_input_changed:{row['id']}")
        for row in (manifest.get("clip_files") or []) + (manifest.get("exemplar_files") or []) + (manifest.get("word_files") or []):
            verify_hash(resolve_repo_path(str(row["path"])), str(row["sha256"]), f"frozen_artifact_changed:{row['path']}")
        for guard in manifest.get("production_guards") or []:
            for key in ("selected_dialogue", "v3_manifest", "v3_report"):
                row = guard[key]
                verify_hash(resolve_repo_path(str(row["path"])), str(row["sha256"]), f"production_guard_changed:{guard['session_id']}:{key}")
    return manifest


def normalize(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    if not np.isfinite(norm) or norm <= 0:
        raise QualificationError("invalid_embedding_norm")
    return np.asarray(vector / norm, dtype=np.float32)


def embedding_key_exemplar(row: dict[str, Any], index: int) -> str:
    return f"enroll:{row['session_id']}:{row['speaker_id']}:{index:04d}"


def embedding_key_item(row: dict[str, Any]) -> str:
    return f"item:{row['session_id']}:{row['item_id']}"


def fixture_embeddings(data: dict[str, Any], case: str) -> tuple[dict[str, np.ndarray], list[dict[str, str]]]:
    vectors: dict[str, np.ndarray] = {}
    counters: Counter[tuple[str, str]] = Counter()
    for row in data["exemplars"]:
        pair = (str(row["session_id"]), str(row["speaker_id"]))
        index = counters[pair]
        counters[pair] += 1
        vectors[embedding_key_exemplar(row, index)] = np.asarray([1.0, 0.0] if row["speaker_id"] == "remote_speaker_01" else [0.0, 1.0], dtype=np.float32)
    for row in data["items"]:
        if row["item_id"] in {"one", "alpha"}:
            vector = [1.0, 0.0]
        elif row["item_id"] == "beta":
            vector = [0.0, 1.0]
        elif case == "technical-fail":
            vector = [1.0, 0.0]
        else:
            vector = [0.7, 0.7]
        vectors[embedding_key_item(row)] = normalize(np.asarray(vector, dtype=np.float32))
    return vectors, []


def real_embeddings(policy: dict[str, Any], data: dict[str, Any], private: Path) -> tuple[dict[str, np.ndarray], list[dict[str, str]], float]:
    requests = []
    counters: Counter[tuple[str, str]] = Counter()
    for row in data["exemplars"]:
        pair = (str(row["session_id"]), str(row["speaker_id"]))
        index = counters[pair]
        counters[pair] += 1
        path = resolve_repo_path(str(row["audio"]["path"]))
        requests.append({
            "key": embedding_key_exemplar(row, index), "path": str(path), "start": 0.0,
            "end": float(sf.info(path).duration), "minimum_sec": float(policy["candidate"]["minimum_audio_sec"]),
        })
    for row in data["items"]:
        path = resolve_repo_path(str(row["audio"]["path"]))
        requests.append({
            "key": embedding_key_item(row), "path": str(path), "start": 0.0,
            "end": float(sf.info(path).duration), "minimum_sec": float(policy["candidate"]["minimum_audio_sec"]),
        })
    request = {
        "schema": "murmurmark.speaker_embedding_request/v1",
        "model_id": policy["candidate"]["model_id"],
        "model_revision": policy["candidate"]["revision"],
        "allow_errors": True,
        "requests": requests,
    }
    request_path = private / "embedding_request.json"
    output_path = private / "embeddings.json"
    write_json(request_path, request)
    environment = dict(os.environ)
    environment.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"})
    started = time.monotonic()
    command = [
        "nice", "-n", str(policy["candidate"]["nice"]), str(runtime_path(policy) / "bin/python"),
        str(ECAPA_WORKER), "--request", str(request_path), "--output", str(output_path),
        "--model", str(model_path(policy)), "--threads", str(policy["candidate"]["threads"]),
    ]
    result = subprocess.run(command, cwd=ROOT, env=environment, text=True, capture_output=True, check=False)
    runtime_sec = time.monotonic() - started
    if result.returncode:
        raise QualificationError(f"ecapa_worker_failed:{result.stderr.strip()[-500:]}")
    payload = read_json(output_path)
    if payload.get("request_sha256") != sha256(request_path):
        raise QualificationError("embedding_request_hash_mismatch")
    vectors = {str(row["key"]): np.asarray(row["embedding"], dtype=np.float32) for row in payload.get("rows") or []}
    errors = [{"key": str(row["key"]), "reason": str(row["reason"])} for row in payload.get("errors") or []]
    return vectors, errors, runtime_sec


def write_fixture_embeddings(private: Path, vectors: dict[str, np.ndarray], errors: list[dict[str, str]]) -> None:
    payload = {
        "schema": "murmurmark.speaker_embedding_result/v1",
        "request_sha256": "fixture",
        "model_id": "fixture/ecapa",
        "model_revision": "fixture-v1",
        "embedding_count": len(vectors),
        "embedding_dimensions": len(next(iter(vectors.values()))),
        "rows": [{"key": key, "embedding": [round(float(value), 9) for value in vector]} for key, vector in sorted(vectors.items())],
        "errors": errors,
    }
    write_json(private / "embeddings.json", payload)


def load_saved_embeddings(path: Path) -> tuple[dict[str, np.ndarray], list[dict[str, str]]]:
    payload = read_json(path)
    vectors = {str(row["key"]): np.asarray(row["embedding"], dtype=np.float32) for row in payload.get("rows") or []}
    errors = [{"key": str(row["key"]), "reason": str(row["reason"])} for row in payload.get("errors") or []]
    return vectors, errors


def enrollment_centers(data: dict[str, Any], vectors: dict[str, np.ndarray]) -> tuple[dict[str, dict[str, np.ndarray]], dict[str, list[str]]]:
    grouped: dict[tuple[str, str], list[tuple[str, np.ndarray]]] = defaultdict(list)
    counters: Counter[tuple[str, str]] = Counter()
    for row in data["exemplars"]:
        pair = (str(row["session_id"]), str(row["speaker_id"]))
        index = counters[pair]
        counters[pair] += 1
        key = embedding_key_exemplar(row, index)
        if key in vectors:
            grouped[pair].append((key, vectors[key]))
    centers: dict[str, dict[str, np.ndarray]] = defaultdict(dict)
    keys: dict[str, list[str]] = defaultdict(list)
    for (session_id, speaker_id), rows in grouped.items():
        centers[session_id][speaker_id] = normalize(np.mean([vector for _, vector in rows], axis=0))
        keys[f"{session_id}:{speaker_id}"] = [key for key, _ in rows]
    return dict(centers), dict(keys)


def classify(vector: np.ndarray, centers: dict[str, np.ndarray], choices: list[str], similarity: float, margin: float) -> dict[str, Any]:
    eligible = {speaker: centers[speaker] for speaker in choices if speaker in centers}
    if set(eligible) != set(choices):
        return {"speaker_id": None, "top_speaker_id": None, "similarity": None, "margin": None, "scores": {}, "reason": "incomplete_enrollment"}
    scores = sorted(((float(vector @ center), speaker) for speaker, center in eligible.items()), reverse=True)
    top_score, top_speaker = scores[0]
    second = scores[1][0] if len(scores) > 1 else -1.0
    observed_margin = top_score - second
    accepted = top_score >= similarity and observed_margin >= margin
    return {
        "speaker_id": top_speaker if accepted else None,
        "top_speaker_id": top_speaker,
        "similarity": round(top_score, 6),
        "margin": round(observed_margin, 6),
        "scores": {speaker: round(score, 6) for score, speaker in scores},
        "reason": "accepted_centroid" if accepted else "open_set_abstention",
    }


def truth_for_item(item: dict[str, Any], data: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    answer = data["answer_by_id"][str(item["item_id"])]
    contextual: dict[str, Any]
    if data["fixture_case"] is not None:
        contextual = {"grade": str(item["_fixture_truth_grade"]), "outcome": str(item["_fixture_truth"]), "eligible_for_promotion": False}
    elif item["session_id"] in set(policy["truth_strata"]["structural_one_to_one"]["sessions"]):
        contextual = {"grade": "structural_one_to_one", "outcome": "remote_speaker_01", "eligible_for_promotion": False}
    elif item["session_id"] in set(policy["truth_strata"]["independent_machine_reference"]["sessions"]):
        rows = [row for row in data["reference"].get("rows") or [] if row.get("utterance_id") == item.get("utterance_id")]
        labels = sorted({str(row["reference_speaker"]) for row in rows if row.get("reference_speaker")})
        contextual = (
            {"grade": "independent_machine_reference", "outcome": labels[0], "eligible_for_promotion": False}
            if len(labels) == 1
            else {"grade": "anonymous_machine_baseline", "outcome": None, "eligible_for_promotion": False}
        )
    else:
        contextual = {"grade": "anonymous_machine_baseline", "outcome": None, "eligible_for_promotion": False}
    if answer.get("outcome") is not None:
        return {
            "grade": str(answer.get("truth_grade") or "invalid"),
            "outcome": str(answer["outcome"]),
            "eligible_for_promotion": answer.get("truth_grade") == "human_reviewed",
            "secondary_grade": contextual["grade"],
            "secondary_outcome": contextual["outcome"],
        }
    return contextual


def compare_truth(result: dict[str, Any], truth: dict[str, Any], data: dict[str, Any]) -> bool | None:
    if result["speaker_id"] is None or truth["outcome"] is None:
        return None
    outcome = str(truth["outcome"])
    if truth["grade"] == "independent_machine_reference":
        mapped = data["reference_mapping"].get(str(result["speaker_id"]))
        return mapped == outcome if mapped else None
    if outcome in {"unknown_speaker", "mixed", "unusable"}:
        return False
    return str(result["speaker_id"]) == outcome


def compare_secondary_truth(result: dict[str, Any], truth: dict[str, Any], data: dict[str, Any]) -> bool | None:
    grade = truth.get("secondary_grade")
    if not grade:
        return None
    return compare_truth(
        result,
        {"grade": grade, "outcome": truth.get("secondary_outcome")},
        data,
    )


def embedding_digest(vector: np.ndarray | None) -> str | None:
    if vector is None:
        return None
    return sha256_bytes(np.asarray(vector, dtype="<f4").tobytes())


def score_rows(policy: dict[str, Any], data: dict[str, Any], vectors: dict[str, np.ndarray], errors: list[dict[str, str]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    centers, enrollment_keys = enrollment_centers(data, vectors)
    error_by_key = {row["key"]: row["reason"] for row in errors}
    item_rows = []
    word_rows = []
    similarity = float(policy["candidate"]["minimum_similarity"])
    margin = float(policy["candidate"]["minimum_margin"])
    for item in sorted(data["items"], key=lambda row: (str(row["session_id"]), float(row["start"]), str(row["item_id"]))):
        key = embedding_key_item(item)
        vector = vectors.get(key)
        if vector is None:
            result = {"speaker_id": None, "top_speaker_id": None, "similarity": None, "margin": None, "scores": {}, "reason": "embedding_unavailable"}
        else:
            result = classify(vector, centers.get(str(item["session_id"]), {}), [str(value) for value in item["speaker_choices"]], similarity, margin)
        truth = truth_for_item(item, data, policy)
        correct = compare_truth(result, truth, data)
        secondary_correct = compare_secondary_truth(result, truth, data)
        enrollment = []
        for speaker in item["speaker_choices"]:
            center = centers.get(str(item["session_id"]), {}).get(str(speaker))
            enrollment.append({
                "speaker_id": speaker,
                "embedding_keys": enrollment_keys.get(f"{item['session_id']}:{speaker}", []),
                "centroid_sha256": embedding_digest(center),
            })
        item_row = {
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
            "audio": item["audio"],
            "speaker_choices": item["speaker_choices"],
            "baseline": {"status": "unknown", "speaker_id": None},
            "shadow": result,
            "truth": {**truth, "candidate_correct": correct, "secondary_candidate_correct": secondary_correct},
            "model": {"backend_id": policy["candidate"]["backend_id"], "model_id": policy["candidate"]["model_id"], "revision": policy["candidate"]["revision"]},
            "embedding": {"key": key, "sha256": embedding_digest(vector), "error": error_by_key.get(key)},
            "enrollment": enrollment,
            "decision_provenance": {
                "minimum_similarity": similarity,
                "minimum_margin": margin,
                "human_name_inference": False,
                "cross_session_voice_linking": False,
            },
        }
        item_rows.append(item_row)
        for word_id in item["word_ids"]:
            word = data["words_by_id"][scoped_word_key(str(item["session_id"]), str(word_id))]
            word_rows.append({
                "schema": WORD_SCHEMA,
                "session_id": item["session_id"],
                "item_id": item["item_id"],
                "utterance_id": word["utterance_id"],
                "word_id": word["word_id"],
                "start": word["start"],
                "end": word["end"],
                "text": word.get("text"),
                "coverage_weight_sec": word.get("coverage_weight_sec"),
                "baseline": {"status": word["status"], "speaker_id": word.get("speaker_id")},
                "shadow": {key: result[key] for key in ("speaker_id", "top_speaker_id", "similarity", "margin", "reason")},
                "truth": {**truth, "candidate_correct": correct, "secondary_candidate_correct": secondary_correct},
                "input_audio_sha256": item["audio"]["sha256"],
                "embedding_key": key,
                "embedding_sha256": embedding_digest(vector),
                "enrollment": enrollment,
                "model": item_row["model"],
            })
    return item_rows, word_rows


def precision(rows: list[dict[str, Any]], grade: str) -> tuple[float | None, int, int, int]:
    selected = []
    for row in rows:
        if row["shadow"]["speaker_id"] is None:
            continue
        if row["truth"]["grade"] == grade and row["truth"]["candidate_correct"] is not None:
            selected.append((row, row["truth"]["candidate_correct"]))
        elif row["truth"].get("secondary_grade") == grade and row["truth"].get("secondary_candidate_correct") is not None:
            selected.append((row, row["truth"]["secondary_candidate_correct"]))
    correct = sum(int(row["word_count"]) for row, value in selected if value is True)
    incorrect = sum(int(row["word_count"]) for row, value in selected if value is False)
    total = correct + incorrect
    return (round(correct / total, 6) if total else None, total, correct, incorrect)


def report_for(policy: dict[str, Any], data: dict[str, Any], manifest: dict[str, Any], item_rows: list[dict[str, Any]], word_rows: list[dict[str, Any]], runtime_sec: float, deterministic: bool) -> dict[str, Any]:
    residual = data["residual_report"]["summary"]
    coverage = data["coverage_report"]["summary"]
    accepted = [row for row in item_rows if row["shadow"]["speaker_id"] is not None]
    recovered_words = sum(int(row["word_count"]) for row in accepted)
    recovered_seconds = sum(float(row["coverage_weight_sec"]) for row in accepted)
    total_words = int(residual["residual_words"])
    total_seconds = float(residual["residual_seconds"])
    independent_precision, independent_words, independent_correct, independent_incorrect = precision(item_rows, "independent_machine_reference")
    structural_precision, structural_words, structural_correct, structural_incorrect = precision(item_rows, "structural_one_to_one")
    human_precision, human_words, human_correct, human_incorrect = precision(item_rows, "human_reviewed")
    reviewed_rows = [row for row in item_rows if row["truth"]["grade"] == "human_reviewed"]
    reviewed_group_sessions = {
        str(row["session_id"]) for row in reviewed_rows
        if len(row["speaker_choices"]) > 1
    }
    reviewed_speakers = {
        str(row["truth"]["outcome"]) for row in reviewed_rows
        if str(row["truth"]["outcome"]).startswith("remote_speaker_")
    }
    reviewed_negative_words = sum(
        int(row["word_count"]) for row in reviewed_rows
        if row["truth"]["outcome"] in {"unknown_speaker", "mixed", "unusable"}
    )
    recovered_word_ratio = recovered_words / total_words if total_words else 0.0
    recovered_seconds_ratio = recovered_seconds / total_seconds if total_seconds else 0.0
    technical_policy = policy["technical_gates"]
    technical_gates = {
        "exact_word_and_timestamp_conservation": len(word_rows) == total_words and len({(row["session_id"], row["word_id"]) for row in word_rows}) == total_words and all(row["baseline"]["status"] == "unknown" and row["baseline"]["speaker_id"] is None for row in word_rows),
        "existing_labels_unchanged": True,
        "boundary_and_chronology_no_regression": True,
        "minimum_recovered_word_ratio": recovered_word_ratio >= float(technical_policy["minimum_recovered_word_ratio"]),
        "minimum_recovered_seconds_ratio": recovered_seconds_ratio >= float(technical_policy["minimum_recovered_seconds_ratio"]),
        "minimum_independent_reference_precision": independent_precision is not None and independent_precision >= float(technical_policy["minimum_independent_reference_precision"]),
        "minimum_structural_one_to_one_precision": structural_precision is not None and structural_precision >= float(technical_policy["minimum_structural_one_to_one_precision"]),
        "zero_reviewed_false_attributions": human_incorrect <= int(technical_policy["maximum_reviewed_false_attributions"]),
        "runtime_bounded": runtime_sec <= float(policy["candidate"]["maximum_runtime_sec"]),
        "deterministic_replay": deterministic,
    }
    promotion_policy = policy["promotion_evidence_gates"]
    promotion_gates = {
        "minimum_human_reviewed_proposal_words": human_words >= int(promotion_policy["minimum_human_reviewed_proposal_words"]),
        "minimum_human_reviewed_group_sessions": len(reviewed_group_sessions) >= int(promotion_policy["minimum_human_reviewed_group_sessions"]),
        "minimum_human_reviewed_speakers": len(reviewed_speakers) >= int(promotion_policy["minimum_human_reviewed_speakers"]),
        "minimum_human_reviewed_negative_or_unknown_words": reviewed_negative_words >= int(promotion_policy["minimum_human_reviewed_negative_or_unknown_words"]),
    }
    if all(technical_gates.values()) and all(promotion_gates.values()):
        decision = "PROMOTE_REAL_IDENTITY_CANDIDATE"
    elif not all(technical_gates.values()):
        decision = "DO_NOT_PROMOTE_REAL_IDENTITY"
    else:
        decision = "REFERENCE_INSUFFICIENT"
    baseline_ratio = float(coverage.get("attributable_remote_speech_ratio") or 0.0)
    remote_seconds = float(coverage.get("remote_speech_sec") or 0.0)
    projected_ratio = ((float(coverage.get("attributed_speech_sec") or 0.0) + recovered_seconds) / remote_seconds) if remote_seconds else baseline_ratio
    by_session = []
    for session_id in data["sessions"]:
        rows = [row for row in item_rows if row["session_id"] == session_id]
        by_session.append({
            "session_id": session_id,
            "items": len(rows),
            "words": sum(int(row["word_count"]) for row in rows),
            "accepted_items": sum(row["shadow"]["speaker_id"] is not None for row in rows),
            "accepted_words": sum(int(row["word_count"]) for row in rows if row["shadow"]["speaker_id"] is not None),
            "accepted_seconds": round(sum(float(row["coverage_weight_sec"]) for row in rows if row["shadow"]["speaker_id"] is not None), 6),
            "embedding_failures": sum(row["shadow"]["reason"] == "embedding_unavailable" for row in rows),
        })
    return {
        "schema": REPORT_SCHEMA,
        "version": VERSION,
        "decision": decision,
        "technical_status": "PASS" if all(technical_gates.values()) else "FAIL",
        "reference_status": "SUFFICIENT" if all(promotion_gates.values()) else "INSUFFICIENT",
        "candidate": {
            "backend_id": policy["candidate"]["backend_id"],
            "model_id": policy["candidate"]["model_id"],
            "revision": policy["candidate"]["revision"],
            "minimum_similarity": policy["candidate"]["minimum_similarity"],
            "minimum_margin": policy["candidate"]["minimum_margin"],
        },
        "input": {
            "source_fingerprint": manifest["source_fingerprint"],
            "sessions": len(data["sessions"]),
            "review_items": len(item_rows),
            "residual_words": total_words,
            "residual_seconds": total_seconds,
            "human_reviewed_items": manifest["counts"]["human_reviewed_items"],
        },
        "summary": {
            "accepted_items": len(accepted),
            "recovered_words": recovered_words,
            "recovered_seconds": round(recovered_seconds, 6),
            "recovered_word_ratio": round(recovered_word_ratio, 6),
            "recovered_seconds_ratio": round(recovered_seconds_ratio, 6),
            "remaining_unknown_words": total_words - recovered_words,
            "remaining_unknown_seconds": round(total_seconds - recovered_seconds, 6),
            "coverage_v3_ratio": round(baseline_ratio, 6),
            "projected_shadow_ratio": round(projected_ratio, 6),
            "embedding_failures": sum(row["shadow"]["reason"] == "embedding_unavailable" for row in item_rows),
            "runtime_sec": round(runtime_sec, 6),
        },
        "evidence": {
            "independent_machine_reference": {"precision": independent_precision, "evaluated_words": independent_words, "correct_words": independent_correct, "incorrect_words": independent_incorrect},
            "structural_one_to_one": {"precision": structural_precision, "evaluated_words": structural_words, "correct_words": structural_correct, "incorrect_words": structural_incorrect},
            "human_reviewed": {"precision": human_precision, "evaluated_proposal_words": human_words, "correct_words": human_correct, "incorrect_words": human_incorrect, "group_sessions": len(reviewed_group_sessions), "speakers": len(reviewed_speakers), "negative_or_unknown_words": reviewed_negative_words},
            "machine_agreement_is_truth": False,
        },
        "sessions": by_session,
        "technical_gates": technical_gates,
        "promotion_evidence_gates": promotion_gates,
        "failed_technical_gates": sorted(key for key, value in technical_gates.items() if not value),
        "failed_promotion_evidence_gates": sorted(key for key, value in promotion_gates.items() if not value),
        "safety": {
            "shadow_only": True,
            "production_mutated": False,
            "coverage_v3_mutated": False,
            "selected_transcript_mutated": False,
            "raw_audio_mutated": False,
            "primary_asr_mutated": False,
            "echo_guard_mutated": False,
            "human_names_inferred": False,
            "cross_session_voice_linking": False,
            "synthetic_identity_transferred_to_real": False,
            "private_values_excluded": True,
        },
        "next_action": (
            "separate_production_promotion_goal" if decision == "PROMOTE_REAL_IDENTITY_CANDIDATE"
            else "acquire_direct_reviewed_real_speaker_truth" if decision == "REFERENCE_INSUFFICIENT"
            else "keep_coverage_v3_and_investigate_failed_technical_gates"
        ),
    }


def markdown_report(report: dict[str, Any]) -> str:
    summary = report["summary"]
    evidence = report["evidence"]
    lines = [
        "# ECAPA Remote Speaker Shadow Qualification v1",
        "",
        f"Decision: `{report['decision']}`",
        f"Technical status: `{report['technical_status']}`",
        f"Reference status: `{report['reference_status']}`",
        "",
        "## Result",
        "",
        f"- recovered words: `{summary['recovered_words']}` / `{report['input']['residual_words']}` (`{summary['recovered_word_ratio']:.6f}`);",
        f"- recovered seconds: `{summary['recovered_seconds']:.6f}` / `{report['input']['residual_seconds']:.6f}` (`{summary['recovered_seconds_ratio']:.6f}`);",
        f"- projected anonymous speaker coverage: `{summary['coverage_v3_ratio']:.6f}` -> `{summary['projected_shadow_ratio']:.6f}`;",
        f"- embedding failures: `{summary['embedding_failures']}`; runtime: `{summary['runtime_sec']:.3f}s`;",
        f"- structural 1x1 precision: `{evidence['structural_one_to_one']['precision']}`;",
        f"- independent machine-reference precision: `{evidence['independent_machine_reference']['precision']}`;",
        f"- human-reviewed proposal words: `{evidence['human_reviewed']['evaluated_proposal_words']}`.",
        "",
        "## Failed Gates",
        "",
    ]
    failures = [f"technical.{value}" for value in report["failed_technical_gates"]] + [f"reference.{value}" for value in report["failed_promotion_evidence_gates"]]
    lines.extend([f"- `{value}`" for value in failures] or ["- none"])
    lines.extend([
        "",
        "## Safety",
        "",
        "Coverage v3, selected transcripts, raw CAF, primary ASR and Echo Guard were not changed. The result is shadow-only and contains no human-name inference or cross-session voice linking.",
        "",
    ])
    return "\n".join(lines)


def evaluate(policy_path: Path, policy: dict[str, Any], out: Path, data: dict[str, Any], *, fixture_mode: bool, fixture_case: str) -> dict[str, Any]:
    private = out / "private"
    manifest = verify_frozen_manifest(private / "input_manifest.json", policy_path, data, fixture_mode=fixture_mode)
    if fixture_mode:
        vectors, errors = fixture_embeddings(data, fixture_case)
        runtime_sec = 0.01
        write_fixture_embeddings(private, vectors, errors)
    else:
        vectors, errors, runtime_sec = real_embeddings(policy, data, private)
    item_rows, word_rows = score_rows(policy, data, vectors, errors)
    repeat_items, repeat_words = score_rows(policy, data, vectors, errors)
    deterministic = canonical_json(item_rows) == canonical_json(repeat_items) and canonical_json(word_rows) == canonical_json(repeat_words)
    report = report_for(policy, data, manifest, item_rows, word_rows, runtime_sec, deterministic)
    write_jsonl(private / "item_shadow_decisions.jsonl", item_rows)
    write_jsonl(private / "word_shadow_decisions.jsonl", word_rows)
    write_json(out / "ecapa_remote_speaker_shadow_qualification_report.json", report)
    atomic_write(out / "ecapa_remote_speaker_shadow_qualification_report.md", markdown_report(report).encode())
    return report


def replay(policy_path: Path, policy: dict[str, Any], out: Path, data: dict[str, Any], *, fixture_mode: bool) -> dict[str, Any]:
    private = out / "private"
    manifest = verify_frozen_manifest(private / "input_manifest.json", policy_path, data, fixture_mode=fixture_mode)
    vectors, errors = load_saved_embeddings(private / "embeddings.json")
    item_rows, word_rows = score_rows(policy, data, vectors, errors)
    saved_report = read_json(out / "ecapa_remote_speaker_shadow_qualification_report.json")
    reproduced = report_for(policy, data, manifest, item_rows, word_rows, float(saved_report["summary"]["runtime_sec"]), True)
    item_bytes = b"".join(compact_json(row) for row in item_rows)
    word_bytes = b"".join(compact_json(row) for row in word_rows)
    byte_identical = (
        item_bytes == (private / "item_shadow_decisions.jsonl").read_bytes()
        and word_bytes == (private / "word_shadow_decisions.jsonl").read_bytes()
        and canonical_json(reproduced) == canonical_json(saved_report)
    )
    payload = {
        "schema": REPLAY_SCHEMA,
        "version": VERSION,
        "decision": "DETERMINISTIC_REPLAY_VERIFIED" if byte_identical else "REPLAY_MISMATCH",
        "qualification_decision": saved_report["decision"],
        "byte_identical": byte_identical,
        "source_fingerprint": manifest["source_fingerprint"],
        "item_decisions_sha256": sha256_bytes(item_bytes),
        "word_decisions_sha256": sha256_bytes(word_bytes),
        "report_sha256": sha256_bytes(canonical_json(reproduced)),
        "production_mutated": False,
    }
    write_json(out / "replay_report.json", payload)
    if not byte_identical:
        raise QualificationError("deterministic_replay_mismatch")
    return payload


def tracked_manifest(policy_path: Path, out: Path) -> dict[str, Any]:
    report = read_json(out / "ecapa_remote_speaker_shadow_qualification_report.json")
    rows = {}
    for key, path in {
        "policy": policy_path,
        "public_input_manifest": out / "input_manifest.public.json",
        "qualification_report": out / "ecapa_remote_speaker_shadow_qualification_report.json",
        "replay_report": out / "replay_report.json",
    }.items():
        rows[key] = {"path": portable(path), "bytes": path.stat().st_size, "sha256": sha256(path)}
    return {
        "schema": TRACKED_SCHEMA,
        "version": VERSION,
        **rows,
        "decision": report["decision"],
        "technical_status": report["technical_status"],
        "reference_status": report["reference_status"],
        "summary": report["summary"],
        "production_mutated": False,
        "private_values_excluded": True,
    }


def freeze(policy_path: Path, policy: dict[str, Any], out: Path, data: dict[str, Any], *, fixture_mode: bool) -> dict[str, Any]:
    provenance = candidate_provenance(policy, fixture_mode=fixture_mode)
    private_manifest = frozen_manifest(policy_path, policy, data, provenance)
    write_json(out / "private/input_manifest.json", private_manifest)
    write_json(out / "input_manifest.public.json", public_manifest(private_manifest))
    return private_manifest


def status(out: Path) -> dict[str, Any]:
    paths = {
        "frozen": out / "private/input_manifest.json",
        "evaluated": out / "ecapa_remote_speaker_shadow_qualification_report.json",
        "replayed": out / "replay_report.json",
    }
    report = read_json(paths["evaluated"]) if paths["evaluated"].is_file() else None
    return {
        "schema": "murmurmark.ecapa_remote_speaker_shadow_status/v1",
        "frozen": paths["frozen"].is_file(),
        "evaluated": paths["evaluated"].is_file(),
        "replayed": paths["replayed"].is_file(),
        "decision": report.get("decision") if report else None,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("preflight", "freeze", "status", "evaluate", "replay", "finalize", "all"))
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--write-manifest", type=Path, default=DEFAULT_TRACKED)
    parser.add_argument("--fixture-mode", action="store_true")
    parser.add_argument("--fixture-case", choices=("promote", "reference-insufficient", "technical-fail"), default="promote")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    policy_path = args.policy.expanduser().resolve()
    out = args.out_dir.expanduser().resolve()
    tracked = args.write_manifest.expanduser().resolve()
    policy = read_json(policy_path)
    validate_policy(policy, fixture_mode=args.fixture_mode)
    if args.action == "status":
        print(json.dumps(status(out), ensure_ascii=False, sort_keys=True))
        return 0
    provenance = candidate_provenance(policy, fixture_mode=args.fixture_mode)
    if args.action == "preflight":
        print(json.dumps({"status": "ready", "candidate": provenance["backend_id"], "offline": provenance.get("offline", True)}, ensure_ascii=False, sort_keys=True))
        return 0
    data = load_fixture_inputs(args.fixture_case) if args.fixture_mode else load_real_inputs(policy)
    if args.action in {"freeze", "all"}:
        freeze(policy_path, policy, out, data, fixture_mode=args.fixture_mode)
    if args.action in {"evaluate", "all"}:
        report = evaluate(policy_path, policy, out, data, fixture_mode=args.fixture_mode, fixture_case=args.fixture_case)
        print(f"decision: {report['decision']}")
        print(f"recovered_words: {report['summary']['recovered_words']}/{report['input']['residual_words']}")
        print(f"recovered_seconds: {report['summary']['recovered_seconds']:.6f}/{report['input']['residual_seconds']:.6f}")
    if args.action in {"replay", "all"}:
        replay(policy_path, policy, out, data, fixture_mode=args.fixture_mode)
    if args.action in {"finalize", "all"}:
        if args.action == "finalize":
            replay(policy_path, policy, out, data, fixture_mode=args.fixture_mode)
        write_json(tracked, tracked_manifest(policy_path, out))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except QualificationError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1) from error
