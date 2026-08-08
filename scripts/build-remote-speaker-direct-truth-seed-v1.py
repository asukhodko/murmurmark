#!/usr/bin/env python3
"""Build and review a frozen blind direct remote-speaker truth seed."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = ROOT / "policies/remote-speaker-direct-truth-seed-v1.json"
DEFAULT_OUT = ROOT / "sessions/_reports/remote-speaker-direct-truth-seed-v1"

POLICY_SCHEMA = "murmurmark.remote_speaker_direct_truth_seed_policy/v1"
PACK_SCHEMA = "murmurmark.remote_speaker_direct_truth_seed_pack/v1"
SELECTION_SCHEMA = "murmurmark.remote_speaker_direct_truth_seed_item/v1"
QUEUE_SCHEMA = "murmurmark.remote_speaker_direct_truth_review_slot/v1"
SLOT_MAP_SCHEMA = "murmurmark.remote_speaker_direct_truth_slot_map/v1"
ANSWER_SCHEMA = "murmurmark.remote_speaker_direct_truth_answer/v1"
REPORT_SCHEMA = "murmurmark.remote_speaker_direct_truth_seed_report/v1"
REPLAY_SCHEMA = "murmurmark.remote_speaker_direct_truth_seed_replay/v1"
TRACKED_SCHEMA = "murmurmark.remote_speaker_direct_truth_seed_manifest/v1"

ALLOWED_DECISIONS = {"DIRECT_TRUTH_SEED_READY", "REFERENCE_INSUFFICIENT", "EVIDENCE_BOUND"}
PUBLIC_FORBIDDEN_KEYS = {
    "speech_text",
    "transcript_fragment",
    "human_name",
    "reviewer_id",
    "session_id",
    "speaker_id",
    "embedding",
    "absolute_path",
}
SESSION_ID_RE = re.compile(r"\b20\d\d-\d\d-\d\d[_-]\d\d-\d\d-\d\d(?:-live)?\b")
ABSOLUTE_PATH_RE = re.compile(r"(?:/Users/|/home/|[A-Za-z]:\\\\)")


class DirectTruthSeedError(RuntimeError):
    pass


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def pretty_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def jsonl_bytes(rows: Iterable[dict[str, Any]]) -> bytes:
    return b"".join(canonical_json(row) for row in rows)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
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
        raise DirectTruthSeedError(f"invalid_json:{portable(path)}:{error}") from error
    if not isinstance(value, dict):
        raise DirectTruthSeedError(f"json_object_required:{portable(path)}")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise DirectTruthSeedError(f"cannot_read:{portable(path)}:{error}") from error
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as error:
            raise DirectTruthSeedError(f"invalid_jsonl:{portable(path)}:{number}:{error}") from error
        if not isinstance(row, dict):
            raise DirectTruthSeedError(f"jsonl_object_required:{portable(path)}:{number}")
        rows.append(row)
    return rows


def portable(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError:
        return resolved.as_posix()


def resolve(value: str | Path) -> Path:
    path = Path(value).expanduser()
    return (ROOT / path).resolve() if not path.is_absolute() else path.resolve()


def fingerprint(path: Path, *, include_path: bool = True) -> dict[str, Any]:
    if not path.is_file():
        raise DirectTruthSeedError(f"artifact_missing:{portable(path)}")
    result: dict[str, Any] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    if include_path:
        result["path"] = portable(path)
    return result


def fingerprint_matches(expected: dict[str, Any], path: Path) -> bool:
    return (
        path.is_file()
        and path.stat().st_size == int(expected.get("bytes", -1))
        and sha256(path) == expected.get("sha256")
    )


def nested_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            keys.add(str(key))
            keys.update(nested_keys(nested))
    elif isinstance(value, list):
        for nested in value:
            keys.update(nested_keys(nested))
    return keys


def assert_public_safe(value: Any) -> None:
    forbidden = nested_keys(value) & PUBLIC_FORBIDDEN_KEYS
    if forbidden:
        raise DirectTruthSeedError(f"public_forbidden_keys:{sorted(forbidden)}")
    text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    if ABSOLUTE_PATH_RE.search(text):
        raise DirectTruthSeedError("public_absolute_path")
    if SESSION_ID_RE.search(text):
        raise DirectTruthSeedError("public_session_id")


def rank(salt: str, value: str) -> str:
    return sha256_bytes(f"{salt}:{value}".encode())


def load_policy(path: Path) -> dict[str, Any]:
    policy = read_json(path)
    if policy.get("schema") != POLICY_SCHEMA:
        raise DirectTruthSeedError("unsupported_policy_schema")
    if set(policy.get("decision", {}).get("allowed_outcomes") or []) != ALLOWED_DECISIONS:
        raise DirectTruthSeedError("decision_outcomes_changed")
    if policy.get("decision", {}).get("production_promotion_allowed") is not False:
        raise DirectTruthSeedError("production_promotion_enabled")
    if policy.get("review", {}).get("show_model_suggestion") is not False:
        raise DirectTruthSeedError("model_suggestion_enabled")
    if policy.get("review", {}).get("allow_human_names") is not False:
        raise DirectTruthSeedError("human_names_enabled")
    if policy.get("review", {}).get("allow_cross_session_identity") is not False:
        raise DirectTruthSeedError("cross_session_identity_enabled")
    if policy.get("selection", {}).get("forbidden_selection_inputs") != [
        "speech_text", "human_name", "direct_truth", "future_answers", "candidate_correctness"
    ]:
        raise DirectTruthSeedError("selection_boundary_changed")
    return policy


def source_paths(policy: dict[str, Any]) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for row in policy.get("sources") or []:
        artifact_id = str(row.get("id") or "")
        path = resolve(str(row.get("path") or ""))
        if not artifact_id or artifact_id in paths:
            raise DirectTruthSeedError("source_id_missing_or_duplicate")
        if not fingerprint_matches(row, path):
            raise DirectTruthSeedError(f"source_missing_or_changed:{artifact_id}")
        paths[artifact_id] = path
    required = {
        "enrollment_policy", "enrollment_tracked_manifest", "enrollment_input_manifest",
        "enrollment_item_comparison", "enrollment_candidate_centroids", "enrollment_report",
        "residual_pack", "residual_review_items", "residual_speaker_exemplars",
        "residual_reference_report",
    }
    if set(paths) != required:
        raise DirectTruthSeedError("source_set_changed")
    return paths


def verify_inherited_production_guards(manifest: dict[str, Any]) -> tuple[int, str]:
    rows = manifest.get("inherited_artifacts") or []
    if not rows:
        raise DirectTruthSeedError("inherited_production_guards_missing")
    for row in rows:
        path = resolve(str(row.get("path") or ""))
        if not fingerprint_matches(row, path):
            raise DirectTruthSeedError(f"inherited_artifact_missing_or_changed:{row.get('id')}")
    payload = [{"id": row["id"], "bytes": row["bytes"], "sha256": row["sha256"]} for row in rows]
    return len(rows), sha256_bytes(canonical_json(payload))


def load_sources(policy: dict[str, Any]) -> dict[str, Any]:
    paths = source_paths(policy)
    enrollment_report = read_json(paths["enrollment_report"])
    if enrollment_report.get("decision") != "DO_NOT_ADVANCE_ENROLLMENT_HARDENING":
        raise DirectTruthSeedError("enrollment_terminal_decision_changed")
    residual_report = read_json(paths["residual_reference_report"])
    if residual_report.get("decision") != "REFERENCE_INSUFFICIENT":
        raise DirectTruthSeedError("residual_reference_decision_changed")
    residual_pack = read_json(paths["residual_pack"])
    if residual_pack.get("schema") != "murmurmark.remote_speaker_residual_reference_pack/v1":
        raise DirectTruthSeedError("residual_pack_schema_changed")
    items = read_jsonl(paths["residual_review_items"])
    comparisons = read_jsonl(paths["enrollment_item_comparison"])
    exemplars = read_jsonl(paths["residual_speaker_exemplars"])
    input_manifest = read_json(paths["enrollment_input_manifest"])
    inherited_count, inherited_fingerprint = verify_inherited_production_guards(input_manifest)

    item_by_id = {str(row.get("item_id")): row for row in items}
    comparison_by_id = {str(row.get("item_id")): row for row in comparisons}
    if len(item_by_id) != len(items) or len(comparison_by_id) != len(comparisons):
        raise DirectTruthSeedError("source_item_ids_duplicate")
    if set(item_by_id) != set(comparison_by_id):
        raise DirectTruthSeedError("source_item_coverage_mismatch")
    scope = policy["frozen_scope"]
    if len(items) != int(scope["items"]):
        raise DirectTruthSeedError("frozen_item_count_changed")
    if sum(int(row["word_count"]) for row in items) != int(scope["words"]):
        raise DirectTruthSeedError("frozen_word_count_changed")
    if len(exemplars) != int(scope["enrollment_exemplars"]):
        raise DirectTruthSeedError("frozen_exemplar_count_changed")
    changes = Counter(str(row.get("change")) for row in comparisons)
    if changes["newly_accepted"] != int(scope["newly_accepted_items"]):
        raise DirectTruthSeedError("newly_accepted_scope_changed")
    if changes["removed_acceptance"] != int(scope["removed_control_acceptances"]):
        raise DirectTruthSeedError("removed_acceptance_scope_changed")

    pack_artifacts = residual_pack.get("artifacts") or {}
    for key, source_id in (
        ("review_items", "residual_review_items"),
        ("speaker_exemplars", "residual_speaker_exemplars"),
    ):
        if pack_artifacts.get(key, {}).get("sha256") != sha256(paths[source_id]):
            raise DirectTruthSeedError(f"residual_pack_artifact_changed:{key}")
    return {
        "paths": paths,
        "items": items,
        "comparisons": comparisons,
        "exemplars": exemplars,
        "item_by_id": item_by_id,
        "comparison_by_id": comparison_by_id,
        "inherited_count": inherited_count,
        "inherited_fingerprint": inherited_fingerprint,
    }


def choose_seed(data: dict[str, Any], policy: dict[str, Any]) -> list[dict[str, Any]]:
    rows = data["comparisons"]
    items = data["item_by_id"]
    settings = policy["selection"]
    salt = str(settings["selection_salt"])
    selected: dict[str, dict[str, Any]] = {}

    def add(row: dict[str, Any], stratum: str) -> None:
        item_id = str(row["item_id"])
        if item_id not in selected:
            selected[item_id] = {"comparison": row, "item": items[item_id], "stratum": stratum}

    changed = [row for row in rows if row.get("change") in settings["include_all_changes"]]
    for row in changed:
        add(row, str(row["change"]))
    changed_sessions = sorted({str(row["session_id"]) for row in changed})
    if len(changed_sessions) != int(policy["frozen_scope"]["changed_sessions"]):
        raise DirectTruthSeedError("changed_session_count_changed")

    for session_id in changed_sessions:
        stable_accept = [
            row for row in rows
            if row["session_id"] == session_id
            and row["change"] == "unchanged"
            and row["control"].get("speaker_id") is not None
            and row["candidate"].get("speaker_id") == row["control"].get("speaker_id")
        ]
        for row in sorted(stable_accept, key=lambda value: rank(salt, value["item_id"]))[
            : int(settings["stable_accepted_per_changed_session"])
        ]:
            add(row, "stable_accept")

        stable_abstentions = [
            row for row in rows
            if row["session_id"] == session_id
            and row["change"] == "unchanged"
            and row["control"].get("speaker_id") is None
            and row["candidate"].get("speaker_id") is None
            and row["control"].get("reason") != "embedding_unavailable"
            and settings["mixed_candidate_cause"] not in items[row["item_id"]].get("baseline_causes", [])
        ]
        for row in sorted(stable_abstentions, key=lambda value: rank(salt, value["item_id"]))[
            : int(settings["stable_abstentions_per_changed_session"])
        ]:
            add(row, "stable_abstention")

    mixed_cause = str(settings["mixed_candidate_cause"])
    mixed_sessions = sorted(
        {str(row["session_id"]) for row in rows if mixed_cause in items[row["item_id"]].get("baseline_causes", [])},
        key=lambda value: rank(salt, f"session:{value}"),
    )[: int(settings["mixed_candidate_sessions"])]
    for session_id in mixed_sessions:
        candidates = [
            row for row in rows
            if row["session_id"] == session_id and mixed_cause in items[row["item_id"]].get("baseline_causes", [])
        ]
        if not candidates:
            raise DirectTruthSeedError(f"mixed_candidate_unavailable:{session_id}")
        add(min(candidates, key=lambda value: rank(salt, value["item_id"])), "mixed_candidate")

    if settings.get("include_all_embedding_unavailable") is True:
        unavailable = [row for row in rows if row["control"].get("reason") == "embedding_unavailable"]
        for row in sorted(unavailable, key=lambda value: rank(salt, value["item_id"])):
            add(row, "unusable_candidate")

    result = sorted(selected.values(), key=lambda value: rank(salt, value["comparison"]["item_id"]))
    expected = {
        "items": int(settings["expected_seed_items"]),
        "words": int(settings["expected_seed_words"]),
        "seconds": float(settings["expected_seed_seconds"]),
        "sessions": int(settings["expected_sessions"]),
    }
    actual = {
        "items": len(result),
        "words": sum(int(row["item"]["word_count"]) for row in result),
        "seconds": round(sum(float(row["item"]["coverage_weight_sec"]) for row in result), 6),
        "sessions": len({row["item"]["session_id"] for row in result}),
    }
    if actual != expected:
        raise DirectTruthSeedError(f"seed_selection_changed:{actual}:{expected}")
    return result


def choose_repeats(selected: list[dict[str, Any]], policy: dict[str, Any]) -> set[str]:
    settings = policy["repeat_review"]
    salt = str(settings["selection_salt"])
    result: set[str] = set()
    for stratum, count in settings["stratum_counts"].items():
        candidates = [row for row in selected if row["stratum"] == stratum]
        chosen = sorted(candidates, key=lambda value: rank(salt, value["item"]["item_id"]))[: int(count)]
        if len(chosen) != int(count):
            raise DirectTruthSeedError(f"repeat_stratum_insufficient:{stratum}")
        result.update(str(row["item"]["item_id"]) for row in chosen)
    if len(result) != int(settings["expected_repeat_items"]):
        raise DirectTruthSeedError("repeat_selection_count_changed")
    return result


def copy_verified(source_row: dict[str, Any], destination: Path) -> dict[str, Any]:
    source = resolve(str(source_row.get("path") or ""))
    if not fingerprint_matches(source_row, source):
        raise DirectTruthSeedError(f"source_clip_missing_or_changed:{portable(source)}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    copied = fingerprint(destination)
    if copied["bytes"] != int(source_row["bytes"]) or copied["sha256"] != source_row["sha256"]:
        raise DirectTruthSeedError("copied_clip_changed")
    return copied


def materialize(out: Path, data: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    selected = choose_seed(data, policy)
    repeated = choose_repeats(selected, policy)
    private = out / "private"
    previous_answers = {
        str(row.get("slot_id")): row
        for row in read_jsonl(private / "answers.jsonl")
    } if (private / "answers.jsonl").is_file() else {}
    if private.exists():
        shutil.rmtree(private)
    private.mkdir(parents=True)

    session_ids = sorted({str(row["item"]["session_id"]) for row in selected})
    aliases = {session_id: f"session_{index:02d}" for index, session_id in enumerate(session_ids, 1)}
    exemplars_by_session: dict[str, list[dict[str, Any]]] = defaultdict(list)
    exemplar_records: list[dict[str, Any]] = []
    for source in data["exemplars"]:
        session_id = str(source["session_id"])
        if session_id not in aliases:
            continue
        alias = aliases[session_id]
        speaker_id = str(source["speaker_id"])
        token = rank(policy["selection"]["selection_salt"], f"exemplar:{source['audio']['sha256']}")[:12]
        destination = private / "exemplars" / alias / speaker_id / f"{token}.wav"
        audio = copy_verified(source["audio"], destination)
        record = {
            "session_alias": alias,
            "session_id": session_id,
            "speaker_id": speaker_id,
            "audio": audio,
            "source_sha256": source["audio"]["sha256"],
        }
        exemplar_records.append(record)
        exemplars_by_session[session_id].append(record)
    write_jsonl(private / "exemplars.jsonl", exemplar_records)

    selection_records: list[dict[str, Any]] = []
    target_audio: dict[str, dict[str, Any]] = {}
    for row in selected:
        item = row["item"]
        item_id = str(item["item_id"])
        session_id = str(item["session_id"])
        alias = aliases[session_id]
        token = rank(policy["selection"]["selection_salt"], f"target:{item_id}")[:16]
        destination = private / "clips" / alias / f"{token}.wav"
        audio = copy_verified(item["audio"], destination)
        target_audio[item_id] = audio
        selection_records.append({
            "schema": SELECTION_SCHEMA,
            "item_id": item_id,
            "session_id": session_id,
            "session_alias": alias,
            "stratum": row["stratum"],
            "word_ids": list(item["word_ids"]),
            "word_count": int(item["word_count"]),
            "coverage_weight_sec": float(item["coverage_weight_sec"]),
            "start": float(item["start"]),
            "end": float(item["end"]),
            "speaker_choices": list(item["speaker_choices"]),
            "source_item_sha256": item["item_sha256"],
            "source_audio_sha256": item["audio"]["sha256"],
            "materialized_audio": audio,
            "repeat_selected": item_id in repeated,
        })
    write_jsonl(private / "seed_selection.jsonl", selection_records)

    queue: list[dict[str, Any]] = []
    slot_map: list[dict[str, Any]] = []
    special = list(policy["review"]["special_outcomes"])
    queue_salt = f"{policy['selection']['selection_salt']}:queue"
    for selection in selection_records:
        kinds = ["primary", "repeat"] if selection["repeat_selected"] else ["primary"]
        session_id = selection["session_id"]
        exemplar_audio = [
            {"speaker": row["speaker_id"], "path": row["audio"]["path"], "sha256": row["audio"]["sha256"]}
            for row in sorted(
                exemplars_by_session[session_id],
                key=lambda value: (value["speaker_id"], value["audio"]["sha256"]),
            )
        ]
        for kind in kinds:
            slot_id = f"dts_{rank(policy['repeat_review']['selection_salt'], f'{kind}:{selection["item_id"]}')[:16]}"
            queue.append({
                "schema": QUEUE_SCHEMA,
                "slot_id": slot_id,
                "session_alias": selection["session_alias"],
                "audio": {
                    "path": selection["materialized_audio"]["path"],
                    "sha256": selection["materialized_audio"]["sha256"],
                },
                "speaker_choices": list(selection["speaker_choices"]) + special,
                "exemplars": exemplar_audio,
            })
            slot_map.append({
                "schema": SLOT_MAP_SCHEMA,
                "slot_id": slot_id,
                "item_id": selection["item_id"],
                "session_id": session_id,
                "session_alias": selection["session_alias"],
                "stratum": selection["stratum"],
                "kind": kind,
            })
    queue.sort(key=lambda row: rank(queue_salt, row["slot_id"]))
    slot_map.sort(key=lambda row: row["slot_id"])
    if nested_keys(queue) & set(policy["review"]["blind_forbidden_keys"]):
        raise DirectTruthSeedError("blind_queue_contains_forbidden_evidence")
    write_jsonl(private / "review_queue.jsonl", queue)
    write_jsonl(private / "slot_map.jsonl", slot_map)

    answers_path = private / "answers.jsonl"
    answers = []
    for row in queue:
        previous = previous_answers.get(row["slot_id"])
        answers.append(previous if previous else {
            "schema": ANSWER_SCHEMA,
            "slot_id": row["slot_id"],
            "outcome": None,
            "truth_grade": None,
            "reviewed_at": None,
        })
    write_jsonl(answers_path, answers)

    frozen_artifacts = {
        name: fingerprint(private / name)
        for name in ("seed_selection.jsonl", "exemplars.jsonl", "review_queue.jsonl", "slot_map.jsonl")
    }
    for index, path in enumerate(sorted((private / "clips").rglob("*.wav"))):
        frozen_artifacts[f"clip:{index}"] = fingerprint(path)
    for index, path in enumerate(sorted((private / "exemplars").rglob("*.wav"))):
        frozen_artifacts[f"exemplar:{index}"] = fingerprint(path)
    pack = {
        "schema": PACK_SCHEMA,
        "version": 1,
            "policy": fingerprint(resolve(policy["_path"])),
        "source_fingerprint": sha256_bytes(canonical_json(policy["sources"])),
        "inherited_artifact_count": data["inherited_count"],
        "inherited_artifact_fingerprint": data["inherited_fingerprint"],
        "selection": {
            "id": policy["selection"]["id"],
            "items": len(selection_records),
            "words": sum(row["word_count"] for row in selection_records),
            "seconds": round(sum(row["coverage_weight_sec"] for row in selection_records), 6),
            "sessions": len(aliases),
            "strata": dict(sorted(Counter(row["stratum"] for row in selection_records).items())),
            "repeat_items": len(repeated),
        },
        "frozen_artifacts": frozen_artifacts,
        "answers_path": portable(answers_path),
        "safety": {
            "blind_queue_has_no_model_suggestion": True,
            "selection_did_not_read_direct_truth": True,
            "production_promotion_allowed": False,
        },
    }
    write_json(private / "pack.json", pack)
    return pack


def load_pack(out: Path, policy: dict[str, Any], *, verify_sources: bool = True) -> dict[str, Any]:
    data = load_sources(policy) if verify_sources else None
    private = out / "private"
    pack = read_json(private / "pack.json")
    if pack.get("schema") != PACK_SCHEMA:
        raise DirectTruthSeedError("pack_schema_changed")
    if pack.get("source_fingerprint") != sha256_bytes(canonical_json(policy["sources"])):
        raise DirectTruthSeedError("pack_source_fingerprint_changed")
    if not fingerprint_matches(pack.get("policy") or {}, resolve(policy["_path"])):
        raise DirectTruthSeedError("pack_policy_missing_or_changed")
    if data and pack.get("inherited_artifact_fingerprint") != data["inherited_fingerprint"]:
        raise DirectTruthSeedError("pack_inherited_fingerprint_changed")
    for artifact_id, expected in (pack.get("frozen_artifacts") or {}).items():
        path = resolve(str(expected.get("path") or ""))
        if not fingerprint_matches(expected, path):
            raise DirectTruthSeedError(f"pack_artifact_missing_or_changed:{artifact_id}")
    return {
        "pack": pack,
        "selection": read_jsonl(private / "seed_selection.jsonl"),
        "queue": read_jsonl(private / "review_queue.jsonl"),
        "slot_map": read_jsonl(private / "slot_map.jsonl"),
        "answers": read_jsonl(private / "answers.jsonl"),
        "data": data,
    }


def validate_answers(bundle: dict[str, Any], policy: dict[str, Any]) -> dict[str, dict[str, Any]]:
    queue = {row["slot_id"]: row for row in bundle["queue"]}
    answers = bundle["answers"]
    if len({row.get("slot_id") for row in answers}) != len(answers):
        raise DirectTruthSeedError("answer_slot_ids_duplicate")
    if {row.get("slot_id") for row in answers} != set(queue):
        raise DirectTruthSeedError("answer_slot_coverage_changed")
    accepted: dict[str, dict[str, Any]] = {}
    for row in answers:
        outcome = row.get("outcome")
        if outcome is None:
            continue
        slot_id = str(row["slot_id"])
        if outcome not in queue[slot_id]["speaker_choices"]:
            raise DirectTruthSeedError(f"answer_outcome_invalid:{slot_id}")
        if row.get("truth_grade") not in policy["review"]["truth_grades"]:
            raise DirectTruthSeedError(f"answer_truth_grade_invalid:{slot_id}")
        if not row.get("reviewed_at"):
            raise DirectTruthSeedError(f"answer_reviewed_at_missing:{slot_id}")
        accepted[slot_id] = row
    return accepted


def build_report(bundle: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    accepted = validate_answers(bundle, policy)
    slot_map = {row["slot_id"]: row for row in bundle["slot_map"]}
    selection = {row["item_id"]: row for row in bundle["selection"]}
    primary_by_item = {
        row["item_id"]: accepted[row["slot_id"]]
        for row in bundle["slot_map"] if row["kind"] == "primary" and row["slot_id"] in accepted
    }
    repeat_by_item = {
        row["item_id"]: accepted[row["slot_id"]]
        for row in bundle["slot_map"] if row["kind"] == "repeat" and row["slot_id"] in accepted
    }
    repeat_compared = sorted(set(primary_by_item) & set(repeat_by_item))
    repeat_matches = sum(primary_by_item[item]["outcome"] == repeat_by_item[item]["outcome"] for item in repeat_compared)
    consistency = round(repeat_matches / len(repeat_compared), 6) if repeat_compared else None
    changed_strata = {"newly_accepted", "removed_acceptance"}
    changed_answered = sum(
        item_id in primary_by_item and row["stratum"] in changed_strata for item_id, row in selection.items()
    )
    attributed = sum(
        answer["outcome"].startswith("remote_speaker_") for answer in primary_by_item.values()
    )
    strata = dict(sorted(Counter(row["stratum"] for row in selection.values()).items()))
    answered_strata = dict(sorted(Counter(selection[item]["stratum"] for item in primary_by_item).items()))
    expected_word_ids = [
        (row["session_id"], word_id)
        for row in selection.values()
        for word_id in row["word_ids"]
    ]
    invariants = {
        "all_seed_items_unique": len(selection) == len(bundle["selection"]),
        "all_seed_words_unique": len(expected_word_ids) == len(set(expected_word_ids)),
        "all_changed_items_once": (
            strata.get("newly_accepted") == int(policy["frozen_scope"]["newly_accepted_items"])
            and strata.get("removed_acceptance") == int(policy["frozen_scope"]["removed_control_acceptances"])
        ),
        "seed_item_count_exact": len(selection) == int(policy["selection"]["expected_seed_items"]),
        "seed_word_count_exact": len(expected_word_ids) == int(policy["selection"]["expected_seed_words"]),
        "seed_seconds_exact": round(sum(row["coverage_weight_sec"] for row in selection.values()), 6)
        == float(policy["selection"]["expected_seed_seconds"]),
        "session_count_exact": len({row["session_alias"] for row in selection.values()})
        == int(policy["selection"]["expected_sessions"]),
        "repeat_count_exact": sum(row["kind"] == "repeat" for row in bundle["slot_map"])
        == int(policy["repeat_review"]["expected_repeat_items"]),
        "blind_queue_has_no_forbidden_keys": not bool(
            nested_keys(bundle["queue"]) & set(policy["review"]["blind_forbidden_keys"])
        ),
        "frozen_artifacts_verified": True,
        "source_and_production_guards_verified": bundle["data"] is not None,
    }
    structural_ok = all(invariants.values())
    readiness = policy["readiness"]
    gates = {
        "all_primary_answers": len(primary_by_item) >= int(readiness["required_primary_answers"]),
        "all_repeat_answers": len(repeat_by_item) >= int(readiness["required_repeat_answers"]),
        "all_changed_answers": changed_answered >= int(readiness["required_changed_answers"]),
        "minimum_attributed_answers": attributed >= int(readiness["minimum_attributed_primary_answers"]),
        "repeat_consistency": consistency is not None and consistency >= float(readiness["minimum_consistency"]),
    }
    decision = "EVIDENCE_BOUND" if not structural_ok else (
        "DIRECT_TRUTH_SEED_READY" if all(gates.values()) else "REFERENCE_INSUFFICIENT"
    )
    report = {
        "schema": REPORT_SCHEMA,
        "generator": {"name": "build-remote-speaker-direct-truth-seed-v1", "version": "1.0.0", "mode": "deterministic_offline"},
        "decision": decision,
        "scope": {
            "source_items": int(policy["frozen_scope"]["items"]),
            "source_words": int(policy["frozen_scope"]["words"]),
            "seed_items": len(selection),
            "seed_words": len(expected_word_ids),
            "seed_seconds": round(sum(row["coverage_weight_sec"] for row in selection.values()), 6),
            "sessions": len({row["session_alias"] for row in selection.values()}),
            "strata": strata,
            "repeat_items": int(policy["repeat_review"]["expected_repeat_items"]),
        },
        "review": {
            "primary_answers": len(primary_by_item),
            "repeat_answers": len(repeat_by_item),
            "changed_answers": changed_answered,
            "attributed_primary_answers": attributed,
            "answered_strata": answered_strata,
            "repeat_compared": len(repeat_compared),
            "repeat_matches": repeat_matches,
            "repeat_consistency": consistency,
            "remaining_slots": len(bundle["queue"]) - len(accepted),
        },
        "gates": gates,
        "invariants": invariants,
        "safety": {
            "blind_review_without_model_suggestion": True,
            "human_names_recorded": False,
            "cross_session_identity_used": False,
            "speech_text_public": False,
            "raw_audio_mutated": False,
            "selected_transcript_mutated": False,
            "coverage_v3_mutated": False,
            "primary_asr_mutated": False,
            "echo_guard_mutated": False,
            "ecapa_shadow_mutated": False,
            "interval_or_enrollment_retuned": False,
            "production_promoted": False,
        },
        "next_action": (
            "qualify_next_identity_backend_against_direct_truth"
            if decision == "DIRECT_TRUTH_SEED_READY"
            else "complete_blind_review_slots"
            if decision == "REFERENCE_INSUFFICIENT"
            else "restore_source_or_pack_integrity"
        ),
    }
    assert_public_safe(report)
    return report


def report_markdown(report: dict[str, Any]) -> str:
    scope = report["scope"]
    review = report["review"]
    return "\n".join([
        "# Remote Speaker Direct Truth Seed v1",
        "",
        f"Decision: `{report['decision']}`",
        f"Seed: `{scope['seed_items']}` items / `{scope['seed_words']}` words / `{scope['seed_seconds']:.6f}s`",
        f"Sessions: `{scope['sessions']}`; hidden repeats: `{scope['repeat_items']}`",
        f"Primary answers: `{review['primary_answers']}` / `{scope['seed_items']}`",
        f"Repeat answers: `{review['repeat_answers']}` / `{scope['repeat_items']}`",
        f"Repeat consistency: `{review['repeat_consistency']}`",
        "",
        "Production transcript, raw capture, ASR, Echo Guard, ECAPA shadow and prior experiments are unchanged.",
        "",
    ])


def tracked_manifest(report_path: Path, report: dict[str, Any]) -> dict[str, Any]:
    result = {
        "schema": TRACKED_SCHEMA,
        "decision": report["decision"],
        "report": fingerprint(report_path),
        "scope": report["scope"],
        "review": report["review"],
        "gates": report["gates"],
        "invariants": report["invariants"],
        "safety": report["safety"],
    }
    assert_public_safe(result)
    return result


def publish(out: Path, policy: dict[str, Any], write_manifest: Path | None = None) -> dict[str, Any]:
    bundle = load_pack(out, policy)
    report = build_report(bundle, policy)
    report_path = out / "remote_speaker_direct_truth_seed_report.json"
    write_json(report_path, report)
    atomic_write(out / "remote_speaker_direct_truth_seed_report.md", report_markdown(report).encode())
    if write_manifest:
        write_json(write_manifest, tracked_manifest(report_path, report))
    return report


def preflight(policy: dict[str, Any]) -> int:
    data = load_sources(policy)
    selected = choose_seed(data, policy)
    repeats = choose_repeats(selected, policy)
    print(json.dumps({
        "status": "ready",
        "source_items": len(data["items"]),
        "seed_items": len(selected),
        "repeat_items": len(repeats),
        "inherited_artifacts": data["inherited_count"],
    }, sort_keys=True))
    return 0


def build_command(args: argparse.Namespace, policy: dict[str, Any]) -> int:
    data = load_sources(policy)
    materialize(args.out_dir, data, policy)
    report = publish(args.out_dir, policy, args.write_manifest)
    print(json.dumps({"decision": report["decision"], "seed_items": report["scope"]["seed_items"], "review_slots": report["scope"]["seed_items"] + report["scope"]["repeat_items"]}, sort_keys=True))
    return 0


def next_command(args: argparse.Namespace, policy: dict[str, Any]) -> int:
    bundle = load_pack(args.out_dir, policy, verify_sources=False)
    accepted = validate_answers(bundle, policy)
    selected = next((row for row in bundle["queue"] if row["slot_id"] not in accepted), None)
    if selected is None:
        print("review_queue: complete")
        return 0
    print(f"slot: {selected['slot_id']}")
    print(f"session: {selected['session_alias']}")
    print(f"clip: {selected['audio']['path']}")
    print(f"play: afplay {json.dumps(selected['audio']['path'])}")
    print("exemplars:")
    for row in selected["exemplars"]:
        print(f"  {row['speaker']}: afplay {json.dumps(row['path'])}")
    print("outcomes: " + " | ".join(selected["speaker_choices"]))
    print(f"grade: murmurmark corpus remote-truth-seed-v1 grade {selected['slot_id']} --outcome <outcome>")
    return 0


def grade_command(args: argparse.Namespace, policy: dict[str, Any]) -> int:
    bundle = load_pack(args.out_dir, policy)
    queue = {row["slot_id"]: row for row in bundle["queue"]}
    if args.slot_id not in queue:
        raise DirectTruthSeedError("review_slot_not_found")
    if args.outcome not in queue[args.slot_id]["speaker_choices"]:
        raise DirectTruthSeedError("review_outcome_invalid")
    reviewed_at = args.reviewed_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    replacement = {
        "schema": ANSWER_SCHEMA,
        "slot_id": args.slot_id,
        "outcome": args.outcome,
        "truth_grade": "human_reviewed",
        "reviewed_at": reviewed_at,
    }
    answers = [replacement if row["slot_id"] == args.slot_id else row for row in bundle["answers"]]
    write_jsonl(args.out_dir / "private/answers.jsonl", answers)
    report = publish(args.out_dir, policy, args.write_manifest)
    print(json.dumps({"decision": report["decision"], "remaining_slots": report["review"]["remaining_slots"]}, sort_keys=True))
    return 0


def status_command(args: argparse.Namespace) -> int:
    report = read_json(args.out_dir / "remote_speaker_direct_truth_seed_report.json")
    print(json.dumps({
        "schema": report["schema"],
        "decision": report["decision"],
        "seed_items": report["scope"]["seed_items"],
        "primary_answers": report["review"]["primary_answers"],
        "repeat_answers": report["review"]["repeat_answers"],
        "remaining_slots": report["review"]["remaining_slots"],
        "next_action": report["next_action"],
    }, sort_keys=True))
    return 0


def replay_command(args: argparse.Namespace, policy: dict[str, Any]) -> int:
    bundle = load_pack(args.out_dir, policy)
    data = bundle["data"]
    expected_selection = choose_seed(data, policy)
    expected_ids = {row["item"]["item_id"] for row in expected_selection}
    actual_ids = {row["item_id"] for row in bundle["selection"]}
    if expected_ids != actual_ids:
        raise DirectTruthSeedError("replay_selection_mismatch")
    report = build_report(bundle, policy)
    report_path = args.out_dir / "remote_speaker_direct_truth_seed_report.json"
    if pretty_json(report) != report_path.read_bytes():
        raise DirectTruthSeedError("replay_report_mismatch")
    replay = {
        "schema": REPLAY_SCHEMA,
        "decision": report["decision"],
        "source_fingerprint": bundle["pack"]["source_fingerprint"],
        "selection_sha256": sha256(args.out_dir / "private/seed_selection.jsonl"),
        "report_sha256": sha256(report_path),
        "byte_exact": True,
    }
    assert_public_safe(replay)
    write_json(args.out_dir / "replay_report.json", replay)
    if args.write_manifest:
        write_json(args.write_manifest, tracked_manifest(report_path, report))
    print(json.dumps(replay, sort_keys=True))
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument(
        "action",
        choices=["preflight", "build", "next", "grade", "status", "finalize", "replay", "all"],
    )
    result.add_argument("slot_id", nargs="?")
    result.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    result.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    result.add_argument("--write-manifest", type=Path)
    result.add_argument("--outcome")
    result.add_argument("--reviewed-at")
    return result


def main() -> int:
    args = parser().parse_args()
    args.policy = resolve(args.policy)
    args.out_dir = resolve(args.out_dir)
    args.write_manifest = resolve(args.write_manifest) if args.write_manifest else None
    policy = load_policy(args.policy)
    policy["_path"] = portable(args.policy)
    try:
        if args.action == "preflight":
            return preflight(policy)
        if args.action == "build":
            return build_command(args, policy)
        if args.action == "next":
            return next_command(args, policy)
        if args.action == "grade":
            if not args.slot_id or not args.outcome:
                raise DirectTruthSeedError("grade_requires_slot_id_and_outcome")
            return grade_command(args, policy)
        if args.action == "status":
            return status_command(args)
        if args.action == "finalize":
            report = publish(args.out_dir, policy, args.write_manifest)
            print(json.dumps({"decision": report["decision"], "remaining_slots": report["review"]["remaining_slots"]}, sort_keys=True))
            return 0
        if args.action == "replay":
            return replay_command(args, policy)
        if args.action == "all":
            build_command(args, policy)
            return replay_command(args, policy)
        raise DirectTruthSeedError(f"unsupported_action:{args.action}")
    except DirectTruthSeedError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
