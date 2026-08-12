#!/usr/bin/env python3
"""Build and review the frozen disjoint remote-speaker truth expansion v2."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = ROOT / "policies/remote-speaker-disjoint-truth-expansion-v2.json"
DEFAULT_OUT = ROOT / "sessions/_reports/remote-speaker-disjoint-truth-expansion-v2"
BASE_PATH = ROOT / "scripts/build-remote-speaker-direct-truth-seed-v1.py"

POLICY_SCHEMA = "murmurmark.remote_speaker_disjoint_truth_policy/v2"
SELECTION_SCHEMA = "murmurmark.remote_speaker_disjoint_truth_item/v2"
QUEUE_SCHEMA = "murmurmark.remote_speaker_disjoint_truth_review_slot/v2"
SLOT_SCHEMA = "murmurmark.remote_speaker_disjoint_truth_slot_map/v2"
ANSWER_SCHEMA = "murmurmark.remote_speaker_disjoint_truth_answer/v2"
PACK_SCHEMA = "murmurmark.remote_speaker_disjoint_truth_pack/v2"
REVIEW_PACK_SCHEMA = "murmurmark.remote_speaker_disjoint_truth_review_pack/v2"
REPORT_SCHEMA = "murmurmark.remote_speaker_disjoint_truth_report/v2"
REPLAY_SCHEMA = "murmurmark.remote_speaker_disjoint_truth_replay/v2"
MANIFEST_SCHEMA = "murmurmark.remote_speaker_disjoint_truth_manifest/v2"


def load_base() -> Any:
    spec = importlib.util.spec_from_file_location("murmurmark_direct_truth_v1_base", BASE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load direct-truth v1 helpers")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


BASE = load_base()


class DisjointTruthError(RuntimeError):
    pass


def source_map(policy: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = policy.get("sources") or []
    result = {str(row.get("id")): row for row in rows}
    if len(result) != len(rows) or not all(result):
        raise DisjointTruthError("source_ids_missing_or_duplicate")
    return result


def load_policy(path: Path) -> dict[str, Any]:
    policy = BASE.read_json(path)
    if policy.get("schema") != POLICY_SCHEMA:
        raise DisjointTruthError("unsupported_policy_schema")
    decision = policy.get("decision") or {}
    if set(decision.get("allowed_outcomes") or []) != {"DIRECT_TRUTH_V2_READY", "REFERENCE_INSUFFICIENT"}:
        raise DisjointTruthError("terminal_outcomes_changed")
    if decision.get("production_promotion_allowed") is not False or decision.get("model_selection_allowed") is not False:
        raise DisjointTruthError("promotion_or_model_selection_enabled")
    review = policy.get("review") or {}
    if review.get("show_model_suggestion") is not False or review.get("allow_human_names") is not False:
        raise DisjointTruthError("blind_review_boundary_changed")
    if review.get("allow_cross_session_identity") is not False:
        raise DisjointTruthError("cross_session_identity_enabled")
    expected_sources = {
        "truth_v1_policy", "truth_v1_manifest", "truth_v1_pack", "truth_v1_selection",
        "truth_v1_slot_map", "truth_v1_answers", "truth_v1_report", "residual_pack",
        "residual_items", "residual_exemplars", "enrollment_comparison", "ecapa_wavlm_pack",
        "wespeaker_pack", "temporal_pack", "temporal_windows", "temporal_mappings",
        "transcript_perfection_manifest", "transcript_perfection_report",
    }
    if set(source_map(policy)) != expected_sources:
        raise DisjointTruthError("source_set_changed")
    policy["_path"] = BASE.portable(path)
    return policy


def source_path(row: dict[str, Any]) -> Path:
    return BASE.resolve(str(row.get("path") or ""))


def verify_source(row: dict[str, Any], *, override: Path | None = None) -> Path:
    path = override or source_path(row)
    if not BASE.fingerprint_matches(row, path):
        raise DisjointTruthError(f"source_missing_or_changed:{row.get('id')}")
    return path


def verify_sources(policy: dict[str, Any], phases: set[str], out: Path | None = None) -> dict[str, Path]:
    result: dict[str, Path] = {}
    frozen_dir = out / "private/frozen_inputs" if out else None
    for artifact_id, row in source_map(policy).items():
        if str(row.get("phase")) not in phases:
            continue
        override = None
        if row.get("snapshot_before_append") and frozen_dir:
            candidate = frozen_dir / Path(str(row["path"])).name
            if candidate.is_file():
                override = candidate
        result[artifact_id] = verify_source(row, override=override)
    return result


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return BASE.read_jsonl(path)


def overlap_seconds(left: dict[str, Any], right: dict[str, Any]) -> float:
    return max(0.0, min(float(left["end"]), float(right["end"])) - max(float(left["start"]), float(right["start"])))


def assignment_map(pack: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(row["key"]): row for row in pack.get("assignments") or []}


def load_selection_data(policy: dict[str, Any], out: Path | None = None) -> dict[str, Any]:
    paths = verify_sources(policy, {"selection"}, out)
    items = read_jsonl(paths["residual_items"])
    old = read_jsonl(paths["truth_v1_selection"])
    comparisons = read_jsonl(paths["enrollment_comparison"])
    windows = read_jsonl(paths["temporal_windows"])
    residual_pack = BASE.read_json(paths["residual_pack"])
    truth_pack = BASE.read_json(paths["truth_v1_pack"])
    ecapa_wavlm = BASE.read_json(paths["ecapa_wavlm_pack"])
    wespeaker = BASE.read_json(paths["wespeaker_pack"])
    temporal = BASE.read_json(paths["temporal_pack"])
    selection = policy["selection"]
    if len(items) != int(selection["source_items"]):
        raise DisjointTruthError("source_item_count_changed")
    if sum(int(row["word_count"]) for row in items) != int(selection["source_words"]):
        raise DisjointTruthError("source_word_count_changed")
    if round(sum(float(row["coverage_weight_sec"]) for row in items), 6) != float(selection["source_seconds"]):
        raise DisjointTruthError("source_seconds_changed")
    if residual_pack.get("schema") != "murmurmark.remote_speaker_residual_reference_pack/v1":
        raise DisjointTruthError("residual_pack_schema_changed")
    if truth_pack.get("selection", {}).get("items") != 33 or truth_pack.get("selection", {}).get("repeat_items") != 8:
        raise DisjointTruthError("truth_v1_scope_changed")
    if truth_pack.get("inherited_artifact_count") != 355:
        raise DisjointTruthError("production_guard_count_changed")
    if len(old) != 33:
        raise DisjointTruthError("truth_v1_primary_count_changed")
    if len({row["item_id"] for row in items}) != len(items):
        raise DisjointTruthError("residual_item_ids_duplicate")
    comparison = {str(row["item_id"]): row for row in comparisons}
    if set(comparison) != {str(row["item_id"]) for row in items}:
        raise DisjointTruthError("comparison_coverage_changed")
    return {
        "paths": paths,
        "items": items,
        "old": old,
        "comparison": comparison,
        "windows": windows,
        "ecapa_wavlm": assignment_map(ecapa_wavlm),
        "wespeaker": assignment_map(wespeaker),
        "temporal": assignment_map(temporal),
        "temporal_spans": temporal.get("spans") or {},
        "truth_pack": truth_pack,
    }


def model_tags(item: dict[str, Any], data: dict[str, Any], maximum_distance: float) -> tuple[list[str], str | None]:
    center = (float(item["start"]) + float(item["end"])) / 2.0
    candidates: list[tuple[float, float, str]] = []
    for window in data["windows"]:
        if window["session_id"] != item["session_id"]:
            continue
        key = str(window["key"])
        if key not in data["ecapa_wavlm"] or key not in data["wespeaker"] or key not in data["temporal"]:
            continue
        overlap = overlap_seconds(item, window)
        distance = abs((float(window["start"]) + float(window["end"])) / 2.0 - center)
        if overlap > 0.0 or distance <= maximum_distance:
            candidates.append((-overlap, distance, key))
    if not candidates:
        return [], None
    _, _, key = min(candidates)
    reference = data["ecapa_wavlm"][key]
    stronger = data["wespeaker"][key]
    temporal = data["temporal"][key]
    tags: list[str] = []
    if reference["ecapa_cluster"] != reference["wavlm_cluster"]:
        tags.append("ecapa_wavlm_disagreement")
    if stronger["candidate_cluster"] not in {
        stronger["reference_ecapa_cluster"], stronger["reference_wavlm_cluster"]
    }:
        tags.append("wespeaker_disagreement")
    if temporal["candidate_cluster"] != temporal["shifted_cluster"]:
        tags.append("temporal_shift_instability")
    if float(temporal["candidate_coverage_ratio"]) < 0.85 or float(temporal["candidate_dominance_margin"]) < 0.6:
        tags.append("temporal_boundary_uncertain")
    return tags, key


def classify_items(data: dict[str, Any], policy: dict[str, Any]) -> list[dict[str, Any]]:
    settings = policy["selection"]
    old_by_session: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in data["old"]:
        old_by_session[str(row["session_id"])].append(row)
    threshold = float(settings["minimum_overlap_to_exclude_sec"])
    remaining = [
        row for row in data["items"]
        if not any(overlap_seconds(row, old) >= threshold for old in old_by_session[str(row["session_id"])])
    ]
    if len(remaining) != int(settings["expected_remaining_pool_items"]):
        raise DisjointTruthError(f"disjoint_pool_changed:{len(remaining)}")
    session_end = {
        session_id: max(float(row["end"]) for row in remaining if row["session_id"] == session_id)
        for session_id in settings["session_quotas"]
    }
    result: list[dict[str, Any]] = []
    for item in remaining:
        tags, window_key = model_tags(item, data, float(settings["nearest_model_window_max_distance_sec"]))
        causes = list(item.get("baseline_causes") or [])
        center = (float(item["start"]) + float(item["end"])) / 2.0
        if any(cause in causes for cause in ("conflicting_frame_speakers", "protected_remote_overlap")):
            tags.append("mixed_or_overlap")
        if int(item["word_count"]) <= 2 or float(item["end"]) - float(item["start"]) <= 1.25:
            tags.append("short_turn")
        if any(str(word_id).endswith(":word:0001") for word_id in item["word_ids"]):
            tags.append("utterance_boundary")
        if center <= 0.1 * session_end[item["session_id"]] or center >= 0.9 * session_end[item["session_id"]]:
            tags.append("session_edge")
        if item["session_id"] == settings["five_speaker_session"]:
            tags.append("five_speaker")
        disagreement = any(tag in tags for tag in (
            "ecapa_wavlm_disagreement", "wespeaker_disagreement", "temporal_shift_instability"
        ))
        if disagreement:
            stratum = "model_disagreement"
        elif "temporal_boundary_uncertain" in tags:
            stratum = "temporal_boundary"
        elif "mixed_or_overlap" in tags:
            stratum = "mixed_or_overlap"
        elif "utterance_boundary" in tags:
            stratum = "utterance_boundary"
        elif "short_turn" in tags:
            stratum = "short_turn"
        elif "session_edge" in tags:
            stratum = "session_edge"
        else:
            stratum = "residual_control"
        score = (
            70 * disagreement
            + 35 * ("temporal_boundary_uncertain" in tags)
            + 30 * ("mixed_or_overlap" in tags)
            + 18 * ("utterance_boundary" in tags)
            + 12 * ("short_turn" in tags)
            + 8 * ("session_edge" in tags)
            + 6 * ("five_speaker" in tags)
            + 4 * len(set(causes))
        )
        result.append({"item": item, "stratum": stratum, "tags": sorted(set(tags)), "score": score, "window_key": window_key})
    return result


def choose_primary(data: dict[str, Any], policy: dict[str, Any]) -> list[dict[str, Any]]:
    classified = classify_items(data, policy)
    settings = policy["selection"]
    selected: list[dict[str, Any]] = []
    for session_id, quota_value in settings["session_quotas"].items():
        candidates = [row for row in classified if row["item"]["session_id"] == session_id]
        candidates.sort(key=lambda row: (-int(row["score"]), BASE.rank(settings["salt"], row["item"]["item_id"])))
        quota = int(quota_value)
        if len(candidates) < quota:
            raise DisjointTruthError(f"session_quota_insufficient:{session_id}:{len(candidates)}:{quota}")
        selected.extend(candidates[:quota])
    selected.sort(key=lambda row: BASE.rank(settings["salt"], row["item"]["item_id"]))
    actual = {
        "items": len(selected),
        "words": sum(int(row["item"]["word_count"]) for row in selected),
        "seconds": round(sum(float(row["item"]["coverage_weight_sec"]) for row in selected), 6),
    }
    expected = {
        "items": int(settings["expected_primary_items"]),
        "words": int(settings["expected_primary_words"]),
        "seconds": float(settings["expected_primary_seconds"]),
    }
    if actual != expected:
        raise DisjointTruthError(f"selection_changed:{actual}:{expected}")
    old = data["old"]
    if any(overlap_seconds(row["item"], previous) >= float(settings["minimum_overlap_to_exclude_sec"])
           for row in selected for previous in old if row["item"]["session_id"] == previous["session_id"]):
        raise DisjointTruthError("v1_primary_interval_overlap")
    return selected


def choose_repeats(selected: list[dict[str, Any]], policy: dict[str, Any]) -> set[str]:
    settings = policy["repeats"]
    result: set[str] = set()
    by_session: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in selected:
        by_session[row["item"]["session_id"]].append(row)
    for session_id, rows in sorted(by_session.items()):
        ranked = sorted(rows, key=lambda row: BASE.rank(settings["salt"], row["item"]["item_id"]))
        result.update(str(row["item"]["item_id"]) for row in ranked[: int(settings["per_session"])])
    if len(result) != int(settings["expected_items"]):
        raise DisjointTruthError("repeat_count_changed")
    return result


def snapshot_mutable_sources(policy: dict[str, Any], out: Path) -> dict[str, dict[str, Any]]:
    frozen_dir = out / "private/frozen_inputs"
    frozen_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, dict[str, Any]] = {}
    for artifact_id, row in source_map(policy).items():
        if not row.get("snapshot_before_append"):
            continue
        source = verify_source(row)
        destination = frozen_dir / source.name
        shutil.copyfile(source, destination)
        result[artifact_id] = BASE.fingerprint(destination)
    return result


def selection_records(selected: list[dict[str, Any]], repeats: set[str], out: Path, policy: dict[str, Any]) -> list[dict[str, Any]]:
    private = out / "private"
    aliases = {
        session_id: f"session_{index:02d}"
        for index, session_id in enumerate(sorted(policy["selection"]["session_quotas"]), 1)
    }
    records: list[dict[str, Any]] = []
    for row in selected:
        item = row["item"]
        token = BASE.rank(policy["selection"]["salt"], f"target:{item['item_id']}")[:16]
        destination = private / "clips" / aliases[item["session_id"]] / f"{token}.wav"
        audio = BASE.copy_verified(item["audio"], destination)
        records.append({
            "schema": SELECTION_SCHEMA,
            "item_id": item["item_id"],
            "session_id": item["session_id"],
            "session_alias": aliases[item["session_id"]],
            "start": float(item["start"]),
            "end": float(item["end"]),
            "word_ids": list(item["word_ids"]),
            "word_count": int(item["word_count"]),
            "coverage_weight_sec": float(item["coverage_weight_sec"]),
            "source_item_sha256": item["item_sha256"],
            "source_audio_sha256": item["audio"]["sha256"],
            "materialized_audio": audio,
            "stratum": row["stratum"],
            "tags": row["tags"],
            "selection_score": int(row["score"]),
            "model_window_key": row["window_key"],
            "repeat_selected": item["item_id"] in repeats,
        })
    return records


def prepare(policy: dict[str, Any], out: Path) -> dict[str, Any]:
    frozen_path = out / "private/candidate_pack.frozen.json"
    if frozen_path.is_file():
        pack = BASE.read_json(frozen_path)
        if not BASE.fingerprint_matches(pack.get("policy") or {}, BASE.resolve(policy["_path"])):
            raise DisjointTruthError("frozen_policy_changed")
        return pack
    data = load_selection_data(policy)
    selected = choose_primary(data, policy)
    repeats = choose_repeats(selected, policy)
    private = out / "private"
    if private.exists():
        shutil.rmtree(private)
    private.mkdir(parents=True)
    records = selection_records(selected, repeats, out, policy)
    BASE.write_jsonl(private / "selection.jsonl", records)
    snapshots = snapshot_mutable_sources(policy, out)
    artifacts = {"selection": BASE.fingerprint(private / "selection.jsonl")}
    for index, path in enumerate(sorted((private / "clips").rglob("*.wav"))):
        artifacts[f"clip:{index}"] = BASE.fingerprint(path)
    pack = {
        "schema": PACK_SCHEMA,
        "state": "frozen_before_prior_truth",
        "policy": BASE.fingerprint(BASE.resolve(policy["_path"])),
        "selection_source_fingerprint": BASE.sha256_bytes(BASE.canonical_json([
            {key: row[key] for key in ("id", "bytes", "sha256")} for row in policy["sources"] if row["phase"] == "selection"
        ])),
        "selection": {
            "items": len(records),
            "words": sum(row["word_count"] for row in records),
            "seconds": round(sum(row["coverage_weight_sec"] for row in records), 6),
            "sessions": len({row["session_alias"] for row in records}),
            "strata": dict(sorted(Counter(row["stratum"] for row in records).items())),
            "tags": dict(sorted(Counter(tag for row in records for tag in row["tags"]).items())),
            "repeat_items": len(repeats),
        },
        "excluded_v1_primary_intervals": 33,
        "remaining_pool_items": int(policy["selection"]["expected_remaining_pool_items"]),
        "inherited_production_guards": int(data["truth_pack"]["inherited_artifact_count"]),
        "inherited_production_guard_fingerprint": data["truth_pack"]["inherited_artifact_fingerprint"],
        "frozen_artifacts": artifacts,
        "frozen_mutable_sources": snapshots,
        "prior_truth_read": False,
        "model_labels_read": False,
        "production_promotion_allowed": False,
    }
    BASE.assert_public_safe({key: value for key, value in pack.items() if key not in {"policy", "frozen_artifacts", "frozen_mutable_sources"}})
    BASE.write_json(private / "candidate_pack.pending.json", pack)
    os.replace(private / "candidate_pack.pending.json", frozen_path)
    return pack


def temporal_purity(exemplar: dict[str, Any], temporal_pack: dict[str, Any], mappings: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any] | None:
    session_id = exemplar["session_id"]
    start = float(exemplar["audio"]["start"])
    end = float(exemplar["audio"]["end"])
    durations: Counter[int] = Counter()
    for span in temporal_pack.get("spans", {}).get(session_id, []):
        amount = max(0.0, min(end, float(span["end"])) - max(start, float(span["start"])))
        if amount > 0:
            durations[int(span["candidate_cluster"])] += amount
    if not durations:
        return None
    cluster, dominant = durations.most_common(1)[0]
    total = sum(durations.values())
    purity = dominant / total
    mapped = (mappings.get(session_id) or {}).get("mapping", {}).get(str(cluster))
    settings = policy["exemplars"]
    if total < float(settings["minimum_temporal_speech_sec"]):
        return None
    if purity < float(settings["minimum_temporal_cluster_purity"]):
        return None
    if mapped != exemplar["speaker_id"]:
        return None
    return {"basis": "temporal_single_cluster_and_coverage_mapping", "cluster": cluster, "speech_sec": round(total, 6), "purity": round(purity, 6)}


def build_exemplars(policy: dict[str, Any], out: Path, selection: list[dict[str, Any]]) -> list[dict[str, Any]]:
    paths = verify_sources(policy, {"post_freeze"}, out)
    old_selection = {row["item_id"]: row for row in read_jsonl(source_path(source_map(policy)["truth_v1_selection"]))}
    slot_map = read_jsonl(paths["truth_v1_slot_map"])
    answers = {row["slot_id"]: row for row in read_jsonl(paths["truth_v1_answers"])}
    residual_exemplars = read_jsonl(paths["residual_exemplars"])
    temporal_pack = BASE.read_json(source_path(source_map(policy)["temporal_pack"]))
    mappings = BASE.read_json(paths["temporal_mappings"])
    candidate_rows: list[dict[str, Any]] = []
    for slot in slot_map:
        answer = answers.get(slot["slot_id"]) or {}
        outcome = answer.get("outcome")
        if slot.get("kind") != "primary" or not str(outcome or "").startswith("remote_speaker_"):
            continue
        source = old_selection[slot["item_id"]]
        candidate_rows.append({
            "session_id": slot["session_id"],
            "speaker_id": outcome,
            "audio": source["materialized_audio"],
            "purity": {"basis": "human_reviewed_single_speaker_v1", "truth_grade": answer["truth_grade"]},
        })
    choices_by_session: dict[str, set[str]] = defaultdict(set)
    residual_items = read_jsonl(source_path(source_map(policy)["residual_items"]))
    for item in residual_items:
        choices_by_session[item["session_id"]].update(item.get("speaker_choices") or [])
    for exemplar in residual_exemplars:
        purity = temporal_purity(exemplar, temporal_pack, mappings, policy)
        if purity:
            candidate_rows.append({**exemplar, "purity": purity})
    if policy["exemplars"]["allow_single_speaker_topology_fallback"]:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for exemplar in residual_exemplars:
            grouped[exemplar["session_id"]].append(exemplar)
        for session_id, choices in choices_by_session.items():
            if len(choices) != 1:
                continue
            if any(row["session_id"] == session_id for row in candidate_rows):
                continue
            candidates = sorted(grouped.get(session_id) or [], key=lambda row: BASE.rank(policy["selection"]["salt"], row["audio"]["sha256"]))
            if candidates:
                candidate_rows.append({**candidates[0], "purity": {"basis": "single_remote_speaker_topology"}})
    unique: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in candidate_rows:
        unique[(row["session_id"], row["speaker_id"], row["audio"]["sha256"])] = row
    by_speaker: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in unique.values():
        by_speaker[(row["session_id"], row["speaker_id"])].append(row)
    aliases = {row["session_id"]: row["session_alias"] for row in selection}
    records: list[dict[str, Any]] = []
    maximum = int(policy["exemplars"]["maximum_per_speaker"])
    for (session_id, speaker_id), rows in sorted(by_speaker.items()):
        rows.sort(key=lambda row: (0 if row["purity"]["basis"].startswith("human_reviewed") else 1, row["audio"]["sha256"]))
        for source in rows[:maximum]:
            token = BASE.rank(policy["selection"]["salt"], f"exemplar:{source['audio']['sha256']}")[:12]
            destination = out / "private/exemplars" / aliases[session_id] / speaker_id / f"{token}.wav"
            audio = BASE.copy_verified(source["audio"], destination)
            records.append({
                "session_id": session_id,
                "session_alias": aliases[session_id],
                "speaker_id": speaker_id,
                "audio": audio,
                "source_audio_sha256": source["audio"]["sha256"],
                "purity": source["purity"],
            })
    return records


def materialize_review(policy: dict[str, Any], out: Path) -> dict[str, Any]:
    pack = BASE.read_json(out / "private/candidate_pack.frozen.json")
    if pack.get("prior_truth_read") is not False:
        raise DisjointTruthError("candidate_pack_truth_boundary_changed")
    verify_sources(policy, {"selection"}, out)
    selection = read_jsonl(out / "private/selection.jsonl")
    old_answers = {
        row["slot_id"]: row for row in read_jsonl(out / "private/answers.jsonl")
    } if (out / "private/answers.jsonl").is_file() else {}
    exemplars = build_exemplars(policy, out, selection)
    BASE.write_jsonl(out / "private/exemplars.jsonl", exemplars)
    exemplars_by_session: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in exemplars:
        exemplars_by_session[row["session_id"]].append(row)
    queue: list[dict[str, Any]] = []
    slot_map: list[dict[str, Any]] = []
    for row in selection:
        kinds = ["primary", "repeat"] if row["repeat_selected"] else ["primary"]
        candidates = sorted(exemplars_by_session[row["session_id"]], key=lambda item: (item["speaker_id"], item["audio"]["sha256"]))
        speaker_choices = sorted({item["speaker_id"] for item in candidates}) + list(policy["review"]["special_outcomes"])
        for kind in kinds:
            slot_token = f"{kind}:{row['item_id']}"
            slot_id = f"dtv2_{BASE.rank(policy['repeats']['salt'], slot_token)[:16]}"
            queue.append({
                "schema": QUEUE_SCHEMA,
                "slot_id": slot_id,
                "session_alias": row["session_alias"],
                "audio": {"path": row["materialized_audio"]["path"], "sha256": row["materialized_audio"]["sha256"]},
                "speaker_choices": speaker_choices,
                "exemplars": [
                    {"speaker": item["speaker_id"], "path": item["audio"]["path"], "sha256": item["audio"]["sha256"]}
                    for item in candidates
                ],
            })
            slot_map.append({
                "schema": SLOT_SCHEMA,
                "slot_id": slot_id,
                "item_id": row["item_id"],
                "session_id": row["session_id"],
                "session_alias": row["session_alias"],
                "kind": kind,
                "stratum": row["stratum"],
            })
    slot_by_id = {row["slot_id"]: row for row in slot_map}
    queue.sort(key=lambda row: (
        0 if slot_by_id[row["slot_id"]]["kind"] == "primary" else 1,
        slot_by_id[row["slot_id"]]["session_alias"],
        BASE.rank(
            f"{policy['selection']['salt']}:queue:{slot_by_id[row['slot_id']]['kind']}:{slot_by_id[row['slot_id']]['session_alias']}",
            row["slot_id"],
        ),
    ))
    slot_map.sort(key=lambda row: row["slot_id"])
    forbidden = set(policy["selection"]["forbidden_inputs"]) | {
        "stratum", "kind", "score", "suggested_outcome", "truth", "change", "control", "candidate"
    }
    if BASE.nested_keys(queue) & forbidden:
        raise DisjointTruthError("blind_queue_contains_forbidden_evidence")
    BASE.write_jsonl(out / "private/review_queue.jsonl", queue)
    BASE.write_jsonl(out / "private/slot_map.jsonl", slot_map)
    answers = []
    for row in queue:
        previous = old_answers.get(row["slot_id"])
        answers.append(previous if previous else {
            "schema": ANSWER_SCHEMA,
            "slot_id": row["slot_id"],
            "outcome": None,
            "truth_grade": None,
            "reviewed_at": None,
        })
    BASE.write_jsonl(out / "private/answers.jsonl", answers)
    artifacts = {
        name: BASE.fingerprint(out / "private" / name)
        for name in ("selection.jsonl", "exemplars.jsonl", "review_queue.jsonl", "slot_map.jsonl")
    }
    for index, path in enumerate(sorted((out / "private/exemplars").rglob("*.wav"))):
        artifacts[f"exemplar:{index}"] = BASE.fingerprint(path)
    review_pack = {
        "schema": REVIEW_PACK_SCHEMA,
        "candidate_pack_sha256": BASE.sha256(out / "private/candidate_pack.frozen.json"),
        "primary_items": len(selection),
        "repeat_items": sum(row["kind"] == "repeat" for row in slot_map),
        "review_slots": len(queue),
        "exemplars": len(exemplars),
        "speaker_profiles_with_pure_exemplars": len({(row["session_alias"], row["speaker_id"]) for row in exemplars}),
        "frozen_artifacts": artifacts,
        "prior_truth_read_after_candidate_freeze": True,
        "mixed_exemplars_allowed": False,
        "model_suggestions_visible": False,
        "queue_order": "session_blocks_primary_then_blind_repeats_v1",
        "interactive_exemplar_playback": "once_per_session_block_v1",
    }
    BASE.write_json(out / "private/review_pack.json", review_pack)
    return review_pack


def load_bundle(policy: dict[str, Any], out: Path) -> dict[str, Any]:
    pack = BASE.read_json(out / "private/candidate_pack.frozen.json")
    review_pack = BASE.read_json(out / "private/review_pack.json")
    if pack.get("schema") != PACK_SCHEMA or review_pack.get("schema") != REVIEW_PACK_SCHEMA:
        raise DisjointTruthError("pack_schema_changed")
    if not BASE.fingerprint_matches(pack.get("policy") or {}, BASE.resolve(policy["_path"])):
        raise DisjointTruthError("frozen_policy_changed")
    if review_pack.get("candidate_pack_sha256") != BASE.sha256(out / "private/candidate_pack.frozen.json"):
        raise DisjointTruthError("candidate_pack_changed")
    for artifact_id, expected in pack["frozen_artifacts"].items():
        path = BASE.resolve(expected["path"])
        if not BASE.fingerprint_matches(expected, path):
            raise DisjointTruthError(f"candidate_artifact_changed:{artifact_id}")
    for artifact_id, expected in review_pack["frozen_artifacts"].items():
        path = BASE.resolve(expected["path"])
        if not BASE.fingerprint_matches(expected, path):
            raise DisjointTruthError(f"review_artifact_changed:{artifact_id}")
    verify_sources(policy, {"selection", "post_freeze"}, out)
    return {
        "pack": pack,
        "review_pack": review_pack,
        "selection": read_jsonl(out / "private/selection.jsonl"),
        "queue": read_jsonl(out / "private/review_queue.jsonl"),
        "slot_map": read_jsonl(out / "private/slot_map.jsonl"),
        "answers": read_jsonl(out / "private/answers.jsonl"),
        "exemplars": read_jsonl(out / "private/exemplars.jsonl"),
    }


def accepted_answers(bundle: dict[str, Any], policy: dict[str, Any]) -> dict[str, dict[str, Any]]:
    queue = {row["slot_id"]: row for row in bundle["queue"]}
    if len(queue) != len(bundle["queue"]):
        raise DisjointTruthError("queue_slot_ids_duplicate")
    if {row["slot_id"] for row in bundle["answers"]} != set(queue):
        raise DisjointTruthError("answer_slot_coverage_changed")
    result: dict[str, dict[str, Any]] = {}
    for row in bundle["answers"]:
        if row.get("outcome") is None:
            continue
        slot_id = row["slot_id"]
        if row["outcome"] not in queue[slot_id]["speaker_choices"]:
            raise DisjointTruthError(f"invalid_outcome:{slot_id}")
        if row.get("truth_grade") not in policy["review"]["truth_grades"] or not row.get("reviewed_at"):
            raise DisjointTruthError(f"invalid_truth_provenance:{slot_id}")
        result[slot_id] = row
    return result


def build_report(policy: dict[str, Any], out: Path) -> dict[str, Any]:
    bundle = load_bundle(policy, out)
    accepted = accepted_answers(bundle, policy)
    slot_map = {row["slot_id"]: row for row in bundle["slot_map"]}
    primary: dict[str, dict[str, Any]] = {}
    repeats: dict[str, dict[str, Any]] = {}
    for slot_id, answer in accepted.items():
        mapped = slot_map[slot_id]
        (primary if mapped["kind"] == "primary" else repeats)[mapped["item_id"]] = answer
    compared = sorted(set(primary) & set(repeats))
    matches = sum(primary[item]["outcome"] == repeats[item]["outcome"] for item in compared)
    consistency = round(matches / len(compared), 6) if compared else None
    attributed = [answer for answer in primary.values() if answer["outcome"].startswith("remote_speaker_")]
    attributed_sessions = {
        next(row["session_alias"] for row in bundle["selection"] if row["item_id"] == item_id)
        for item_id, answer in primary.items() if answer["outcome"].startswith("remote_speaker_")
    }
    outcomes = Counter(answer["outcome"] for answer in primary.values())
    readiness = policy["readiness"]
    gates = {
        "all_primary_answers": len(primary) == int(readiness["required_primary_answers"]),
        "all_repeat_answers": len(repeats) == int(readiness["required_repeat_answers"]),
        "minimum_attributed_primary_answers": len(attributed) >= int(readiness["minimum_attributed_primary_answers"]),
        "minimum_attributed_sessions": len(attributed_sessions) >= int(readiness["minimum_attributed_sessions"]),
        "repeat_consistency": consistency is not None and consistency >= float(readiness["minimum_repeat_consistency"]),
    }
    decision = "DIRECT_TRUTH_V2_READY" if all(gates.values()) else "REFERENCE_INSUFFICIENT"
    strata = Counter(row["stratum"] for row in bundle["selection"])
    tags = Counter(tag for row in bundle["selection"] for tag in row["tags"])
    report = {
        "schema": REPORT_SCHEMA,
        "generator": {"name": "build-remote-speaker-disjoint-truth-v2", "version": "2.0.0", "mode": "deterministic_offline"},
        "decision": decision,
        "scope": {
            "source_items": int(policy["selection"]["source_items"]),
            "remaining_disjoint_pool_items": bundle["pack"]["remaining_pool_items"],
            "primary_items": len(bundle["selection"]),
            "primary_words": sum(row["word_count"] for row in bundle["selection"]),
            "primary_seconds": round(sum(row["coverage_weight_sec"] for row in bundle["selection"]), 6),
            "repeat_items": int(policy["repeats"]["expected_items"]),
            "sessions": len({row["session_alias"] for row in bundle["selection"]}),
            "strata": dict(sorted(strata.items())),
            "tags": dict(sorted(tags.items())),
            "excluded_v1_primary_intervals": 33,
        },
        "review": {
            "primary_answers": len(primary),
            "repeat_answers": len(repeats),
            "remaining_slots": len(bundle["queue"]) - len(accepted),
            "repeat_compared": len(compared),
            "repeat_matches": matches,
            "repeat_consistency": consistency,
            "attributed_primary_answers": len(attributed),
            "attributed_sessions": len(attributed_sessions),
            "unknown_primary_answers": outcomes.get("unknown_speaker", 0),
            "mixed_primary_answers": outcomes.get("mixed", 0),
            "unusable_primary_answers": outcomes.get("unusable", 0),
        },
        "exemplars": {
            "clips": len(bundle["exemplars"]),
            "speaker_profiles": len({(row["session_alias"], row["speaker_id"]) for row in bundle["exemplars"]}),
            "mixed_truth_exemplars": 0,
            "purity_bases": dict(sorted(Counter(row["purity"]["basis"] for row in bundle["exemplars"]).items())),
        },
        "gates": gates,
        "invariants": {
            "candidate_pack_frozen_before_prior_truth": bundle["pack"]["prior_truth_read"] is False,
            "primary_count_exact": len(bundle["selection"]) == int(policy["selection"]["expected_primary_items"]),
            "repeat_count_exact": sum(row["kind"] == "repeat" for row in bundle["slot_map"]) == int(policy["repeats"]["expected_items"]),
            "sessions_exact": len({row["session_alias"] for row in bundle["selection"]}) == 6,
            "v1_primary_interval_overlap_count": 0,
            "model_suggestions_hidden": True,
            "mixed_exemplars_excluded": True,
            "production_guards_verified": bundle["pack"]["inherited_production_guards"] == 355,
        },
        "safety": {
            "raw_audio_mutated": False,
            "selected_transcript_mutated": False,
            "coverage_v3_mutated": False,
            "primary_asr_mutated": False,
            "echo_guard_mutated": False,
            "truth_v1_mutated": False,
            "model_selected": False,
            "production_promoted": False,
            "public_speech_text": False,
            "public_human_names": False,
            "public_absolute_paths": False,
        },
        "next_action": "qualify_one_materially_new_model_on_disjoint_truth" if decision == "DIRECT_TRUTH_V2_READY" else "complete_or_expand_disjoint_truth_review",
    }
    BASE.assert_public_safe(report)
    return report


def report_markdown(report: dict[str, Any]) -> str:
    scope = report["scope"]
    review = report["review"]
    return "\n".join([
        "# Remote Speaker Disjoint Truth Expansion v2", "",
        f"Decision: `{report['decision']}`",
        f"Primary: `{scope['primary_items']}` items / `{scope['primary_words']}` words / `{scope['primary_seconds']:.6f}s`",
        f"Sessions: `{scope['sessions']}`; hidden repeats: `{scope['repeat_items']}`",
        f"Answers: `{review['primary_answers']}` primary + `{review['repeat_answers']}` repeat; remaining: `{review['remaining_slots']}`",
        f"Attributed / unknown / mixed / unusable: `{review['attributed_primary_answers']}` / `{review['unknown_primary_answers']}` / `{review['mixed_primary_answers']}` / `{review['unusable_primary_answers']}`",
        f"Repeat consistency: `{review['repeat_consistency']}`", "",
        "Coverage v3, selected transcripts, raw CAF, ASR, Echo Guard, v1 truth and production remain unchanged.", "",
    ])


def publish(policy: dict[str, Any], out: Path, manifest_path: Path | None = None) -> dict[str, Any]:
    report = build_report(policy, out)
    report_path = out / "remote_speaker_disjoint_truth_report.json"
    BASE.write_json(report_path, report)
    BASE.atomic_write(out / "remote_speaker_disjoint_truth_report.md", report_markdown(report).encode())
    if manifest_path:
        manifest = {
            "schema": MANIFEST_SCHEMA,
            "decision": report["decision"],
            "report": BASE.fingerprint(report_path),
            "scope": report["scope"],
            "review": report["review"],
            "gates": report["gates"],
            "invariants": report["invariants"],
            "safety": report["safety"],
        }
        BASE.assert_public_safe(manifest)
        BASE.write_json(manifest_path, manifest)
    return report


def freeze(policy: dict[str, Any], out: Path, manifest_path: Path | None = None) -> dict[str, Any]:
    prepare(policy, out)
    if not (out / "private/review_pack.json").is_file():
        materialize_review(policy, out)
    return publish(policy, out, manifest_path)


def ensure_review_refresh_allowed(answers: list[dict[str, Any]]) -> None:
    if any(row.get("outcome") is not None for row in answers):
        raise DisjointTruthError("review_refresh_forbidden_after_first_answer")


def refresh_review(policy: dict[str, Any], out: Path, manifest_path: Path | None = None) -> int:
    bundle = load_bundle(policy, out)
    accepted_answers(bundle, policy)
    ensure_review_refresh_allowed(bundle["answers"])
    private = out / "private"
    for name in ("review_pack.json", "exemplars.jsonl", "review_queue.jsonl", "slot_map.jsonl", "answers.jsonl"):
        (private / name).unlink(missing_ok=True)
    shutil.rmtree(private / "exemplars", ignore_errors=True)
    review_pack = materialize_review(policy, out)
    report = publish(policy, out, manifest_path)
    replay(policy, out, manifest_path)
    print(json.dumps({
        "status": "review_pack_refreshed",
        "decision": report["decision"],
        "review_slots": review_pack["review_slots"],
        "queue_order": review_pack["queue_order"],
    }, sort_keys=True))
    return 0


def next_slot(policy: dict[str, Any], out: Path, play: bool) -> int:
    bundle = load_bundle(policy, out)
    accepted = accepted_answers(bundle, policy)
    slot = next((row for row in bundle["queue"] if row["slot_id"] not in accepted), None)
    if slot is None:
        print("review_queue: complete")
        return 0
    print(f"slot: {slot['slot_id']}")
    print(f"session: {slot['session_alias']}")
    print(f"clip: {slot['audio']['path']}")
    print("exemplars:")
    for row in slot["exemplars"]:
        print(f"  {row['speaker']}: {row['path']}")
    print("outcomes: " + " | ".join(slot["speaker_choices"]))
    print(f"grade: murmurmark corpus remote-truth-seed-v2 grade {slot['slot_id']} --outcome <outcome>")
    if play:
        BASE.play_blind_slot(slot)
    return 0


def grade(policy: dict[str, Any], out: Path, slot_id: str, outcome: str, reviewed_at: str | None, manifest_path: Path | None) -> int:
    bundle = load_bundle(policy, out)
    queue = {row["slot_id"]: row for row in bundle["queue"]}
    if slot_id not in queue:
        raise DisjointTruthError("review_slot_not_found")
    if outcome not in queue[slot_id]["speaker_choices"]:
        raise DisjointTruthError("review_outcome_invalid")
    timestamp = reviewed_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    replacement = {"schema": ANSWER_SCHEMA, "slot_id": slot_id, "outcome": outcome, "truth_grade": "human_reviewed", "reviewed_at": timestamp}
    answers = [replacement if row["slot_id"] == slot_id else row for row in bundle["answers"]]
    BASE.write_jsonl(out / "private/answers.jsonl", answers)
    report = publish(policy, out, manifest_path)
    print(json.dumps({"decision": report["decision"], "remaining_slots": report["review"]["remaining_slots"]}, sort_keys=True))
    return 0


def play_sequence(sequence: list[tuple[str, str]]) -> None:
    player_name = os.environ.get("MURMURMARK_BLIND_AUDIO_PLAYER", "afplay")
    player = shutil.which(player_name) if "/" not in player_name else player_name
    if not player or not Path(player).is_file():
        raise BASE.DirectTruthSeedError(f"blind_audio_player_missing:{player_name}")
    for label, path in sequence:
        print(f"[play] {label}", flush=True)
        result = subprocess.run([player, str(BASE.resolve(path))], check=False)
        if result.returncode != 0:
            raise BASE.DirectTruthSeedError(f"blind_audio_playback_failed:{label}:{result.returncode}")


def play_target(slot: dict[str, Any]) -> None:
    play_sequence([("target", str(slot["audio"]["path"]))])


def play_session_reference(slot: dict[str, Any]) -> None:
    sequence = [("target", str(slot["audio"]["path"]))]
    sequence.extend(
        (f"{row['speaker']} exemplar", str(row["path"]))
        for row in slot["exemplars"]
    )
    sequence.append(("target again", str(slot["audio"]["path"])))
    play_sequence(sequence)


def interactive_review(policy: dict[str, Any], out: Path, manifest_path: Path | None) -> int:
    last_session: str | None = None
    try:
        while True:
            bundle = load_bundle(policy, out)
            accepted = accepted_answers(bundle, policy)
            slot = next((row for row in bundle["queue"] if row["slot_id"] not in accepted), None)
            if slot is None:
                print("review_queue: complete")
                return 0
            print(f"\nslot: {slot['slot_id']} ({len(accepted) + 1}/{len(bundle['queue'])})")
            print(f"session: {slot['session_alias']}")
            if slot["session_alias"] != last_session:
                print("reference: new session block; exemplars play once")
                play_session_reference(slot)
                last_session = slot["session_alias"]
            else:
                play_target(slot)
            choices = list(slot["speaker_choices"])
            for index, choice in enumerate(choices, 1):
                print(f"  {index}: {choice}")
            aliases = {"u": "unknown_speaker", "m": "mixed", "x": "unusable"}
            while True:
                value = input("outcome [number/u/m/x, r=target, e=exemplars, q=quit]: ").strip().lower()
                if value == "q":
                    print("review: stopped; progress saved")
                    return 0
                if value == "r":
                    play_target(slot)
                    continue
                if value == "e":
                    play_session_reference(slot)
                    continue
                outcome = aliases.get(value)
                if value.isdigit() and 1 <= int(value) <= len(choices):
                    outcome = choices[int(value) - 1]
                if outcome in choices:
                    grade(policy, out, slot["slot_id"], outcome, None, manifest_path)
                    break
                print("invalid outcome")
    except EOFError:
        print("\nerror: interactive review input is unavailable; run the command directly in a terminal", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nreview: stopped; progress saved")
        return 0


def status(out: Path) -> int:
    report = BASE.read_json(out / "remote_speaker_disjoint_truth_report.json")
    print(json.dumps({
        "decision": report["decision"],
        "primary_items": report["scope"]["primary_items"],
        "primary_answers": report["review"]["primary_answers"],
        "repeat_answers": report["review"]["repeat_answers"],
        "remaining_slots": report["review"]["remaining_slots"],
        "repeat_consistency": report["review"]["repeat_consistency"],
        "next_action": report["next_action"],
    }, sort_keys=True))
    return 0


def replay(policy: dict[str, Any], out: Path, manifest_path: Path | None) -> int:
    bundle = load_bundle(policy, out)
    data = load_selection_data(policy, out)
    expected = {row["item"]["item_id"] for row in choose_primary(data, policy)}
    actual = {row["item_id"] for row in bundle["selection"]}
    if expected != actual:
        raise DisjointTruthError("replay_selection_mismatch")
    report = build_report(policy, out)
    report_path = out / "remote_speaker_disjoint_truth_report.json"
    if BASE.pretty_json(report) != report_path.read_bytes():
        raise DisjointTruthError("replay_report_mismatch")
    value = {
        "schema": REPLAY_SCHEMA,
        "decision": report["decision"],
        "candidate_pack_sha256": BASE.sha256(out / "private/candidate_pack.frozen.json"),
        "review_pack_sha256": BASE.sha256(out / "private/review_pack.json"),
        "report_sha256": BASE.sha256(report_path),
        "byte_exact": True,
    }
    BASE.assert_public_safe(value)
    BASE.write_json(out / "replay_report.json", value)
    if manifest_path:
        publish(policy, out, manifest_path)
    print(json.dumps(value, sort_keys=True))
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("action", choices=["preflight", "prepare", "freeze", "refresh-review", "next", "grade", "review", "progress", "status", "finalize", "replay", "all"])
    result.add_argument("slot_id", nargs="?")
    result.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    result.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    result.add_argument("--write-manifest", type=Path)
    result.add_argument("--outcome")
    result.add_argument("--reviewed-at")
    result.add_argument("--play", action="store_true")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        policy_path = BASE.resolve(args.policy)
        out = BASE.resolve(args.out_dir)
        manifest = BASE.resolve(args.write_manifest) if args.write_manifest else None
        policy = load_policy(policy_path)
        if args.action == "preflight":
            frozen = out / "private/frozen_inputs"
            data = load_selection_data(policy, out if frozen.is_dir() else None)
            selected = choose_primary(data, policy)
            print(json.dumps({"status": "ready", "remaining_pool": len(classify_items(data, policy)), "primary_items": len(selected), "repeat_items": len(choose_repeats(selected, policy)), "sessions": 6}, sort_keys=True))
            return 0
        if args.action == "prepare":
            pack = prepare(policy, out)
            print(json.dumps({"status": "frozen_before_prior_truth", **pack["selection"]}, sort_keys=True))
            return 0
        if args.action == "freeze":
            report = freeze(policy, out, manifest)
            print(json.dumps({"decision": report["decision"], "review_slots": report["scope"]["primary_items"] + report["scope"]["repeat_items"]}, sort_keys=True))
            return 0
        if args.action == "refresh-review":
            return refresh_review(policy, out, manifest)
        if args.action == "next":
            return next_slot(policy, out, args.play)
        if args.action == "grade":
            if not args.slot_id or not args.outcome:
                raise DisjointTruthError("grade_requires_slot_id_and_outcome")
            return grade(policy, out, args.slot_id, args.outcome, args.reviewed_at, manifest)
        if args.action == "review":
            return interactive_review(policy, out, manifest)
        if args.action in {"progress", "status"}:
            return status(out)
        if args.action == "finalize":
            report = publish(policy, out, manifest)
            print(json.dumps({"decision": report["decision"], "remaining_slots": report["review"]["remaining_slots"]}, sort_keys=True))
            return 0
        if args.action == "replay":
            return replay(policy, out, manifest)
        if args.action == "all":
            freeze(policy, out, manifest)
            return replay(policy, out, manifest)
    except (DisjointTruthError, BASE.DirectTruthSeedError, OSError, ValueError, KeyError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
