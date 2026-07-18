#!/usr/bin/env python3
"""Shared past-only routing for causal remote-active Me recovery candidates."""

from __future__ import annotations

from collections import Counter
import difflib
import hashlib
import json
import math
import re
from typing import Any


SCHEMA = "murmurmark.causal_candidate_prefilter/v1"
SCRIPT_VERSION = "1.0.0"
MIN_TARGET_MARGIN = 0.15
MIN_REMOTE_TEXT_SIMILARITY = 0.88
MIN_REMOTE_TOKEN_RECALL = 0.65
MAX_CONTEXT_GAP_SEC = 0.30
REMOTE_ACTIVE_MIN_DB = -65.0
TOKEN_RE = re.compile(r"[\w-]+", re.UNICODE)


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def clean_text(value: Any) -> str:
    return " ".join(str(value or "").replace("\n", " ").split()).strip()


def tokens(value: Any) -> list[str]:
    return [match.group(0).casefold() for match in TOKEN_RE.finditer(clean_text(value))]


def bag_recall(source: list[str], target: list[str]) -> float:
    if not source:
        return 0.0
    remaining = Counter(target)
    matched = 0
    for token in source:
        if remaining[token] > 0:
            matched += 1
            remaining[token] -= 1
    return matched / len(source)


def asr_text_similarity(left: Any, right: Any) -> float:
    left_norm = " ".join(tokens(left))
    right_norm = " ".join(tokens(right))
    if not left_norm or not right_norm:
        return 0.0

    def ngrams(value: str, size: int = 3) -> set[str]:
        compact = value.replace(" ", "")
        if len(compact) <= size:
            return {compact} if compact else set()
        return {compact[index : index + size] for index in range(len(compact) - size + 1)}

    def jaccard(left_values: set[str], right_values: set[str]) -> float:
        if not left_values or not right_values:
            return 0.0
        return len(left_values & right_values) / len(left_values | right_values)

    return max(
        difflib.SequenceMatcher(None, left_norm, right_norm).ratio(),
        jaccard(set(left_norm.split()), set(right_norm.split())),
        jaccard(ngrams(left_norm), ngrams(right_norm)),
    )


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def source_row(row: dict[str, Any]) -> dict[str, Any]:
    projected = row.get("causal_selection")
    return projected if isinstance(projected, dict) else row


def source_id(row: dict[str, Any]) -> str:
    return str(source_row(row).get("id") or row.get("id") or "")


def contract_checks(row: dict[str, Any]) -> dict[str, bool]:
    source = source_row(row)
    checks = source.get("checks") if isinstance(source.get("checks"), dict) else {}
    recording = (
        source.get("recording_time_evidence")
        if isinstance(source.get("recording_time_evidence"), dict)
        else {}
    )
    remote = (
        source.get("remote_audio_guard")
        if isinstance(source.get("remote_audio_guard"), dict)
        else {}
    )
    duration = safe_float(
        source.get("duration_sec"),
        safe_float(source.get("end")) - safe_float(source.get("start")),
    )
    return {
        "timeline_causal": source.get("timeline_causal") is True
        and checks.get("timeline_causal") is True,
        "selection_does_not_use_batch": source.get("used_batch_fields_for_selection") is False
        and checks.get("selection_does_not_use_batch") is True,
        "recording_time_committed_pcm": checks.get("recording_time_committed_pcm") is True,
        "recording_time_evidence": recording.get("status") == "passed"
        or checks.get("recording_time_evidence") is True,
        "past_only_enrollment": checks.get("past_only_enrollment") is True,
        "source_text_contentful": checks.get("source_text_contentful") is True,
        "supported_duration": 0.35 <= duration <= 30.0,
        "not_already_published": checks.get("not_already_published") is True,
        "remote_audio_active": safe_float(remote.get("remote_db"), -120.0)
        > REMOTE_ACTIVE_MIN_DB,
    }


def cheap_features(row: dict[str, Any], remote_text: str) -> dict[str, Any]:
    source = source_row(row)
    speaker = (
        source.get("speaker_evidence")
        if isinstance(source.get("speaker_evidence"), dict)
        else {}
    )
    enrollment = (
        speaker.get("enrollment") if isinstance(speaker.get("enrollment"), dict) else {}
    )
    scores = speaker.get("scores") if isinstance(speaker.get("scores"), dict) else {}
    source_evaluation = (
        source.get("source_evaluation")
        if isinstance(source.get("source_evaluation"), dict)
        else {}
    )
    source_tokens = tokens(source.get("text"))
    remote_tokens = tokens(remote_text)
    target = scores.get("target")
    return {
        "positive_seed_count": safe_int(enrollment.get("positive_seed_count")),
        "negative_seed_count": safe_int(enrollment.get("negative_seed_count")),
        "target_margin": round(safe_float(target), 6) if target is not None else None,
        "positive_score": (
            round(safe_float(scores.get("positive")), 6)
            if scores.get("positive") is not None
            else None
        ),
        "negative_score": (
            round(safe_float(scores.get("negative")), 6)
            if scores.get("negative") is not None
            else None
        ),
        "segment_gate_status": source_evaluation.get("segment_gate_status"),
        "source_token_count": len(source_tokens),
        "remote_token_count": len(remote_tokens),
        "remote_text_available": bool(remote_tokens),
        "remote_text_similarity": round(asr_text_similarity(source.get("text"), remote_text), 6),
        "remote_token_recall_in_source": round(bag_recall(remote_tokens, source_tokens), 6),
        "source_token_recall_in_remote": round(bag_recall(source_tokens, remote_tokens), 6),
    }


def _base_decision(
    row: dict[str, Any],
    *,
    remote_text: str,
    session: str | None,
) -> dict[str, Any]:
    source = source_row(row)
    checks = contract_checks(row)
    features = cheap_features(row, remote_text)
    contract_valid = all(checks.values())
    positive_target = bool(
        contract_valid
        and safe_int(features.get("positive_seed_count")) > 0
        and features.get("target_margin") is not None
        and safe_float(features.get("target_margin"), -1.0) >= MIN_TARGET_MARGIN
    )
    strong_remote_copy = bool(
        contract_valid
        and not positive_target
        and features.get("remote_text_available") is True
        and safe_float(features.get("remote_text_similarity")) >= MIN_REMOTE_TEXT_SIMILARITY
        and safe_float(features.get("remote_token_recall_in_source")) >= MIN_REMOTE_TOKEN_RECALL
    )
    if positive_target:
        route = "expensive_candidate"
        reasons = ["positive_past_target_me_margin"]
    elif strong_remote_copy:
        route = "cheap_reject"
        reasons = ["strong_live_remote_text_copy_without_positive_local_margin"]
    else:
        route = "unresolved"
        reasons = (
            ["causal_input_contract_incomplete"]
            if not contract_valid
            else ["no_strong_positive_or_negative_cheap_evidence"]
        )
    payload = {
        "schema": SCHEMA,
        "generator": {"name": "causal_candidate_prefilter", "version": SCRIPT_VERSION},
        "kind": "causal_candidate_prefilter_decision",
        "id": source_id(row),
        "corpus_row_id": row.get("id") if source is not row else None,
        "session": session or row.get("session"),
        "chunk_index": source.get("chunk_index"),
        "start": source.get("start"),
        "end": source.get("end"),
        "duration_sec": source.get("duration_sec"),
        "route": route,
        "reasons": reasons,
        "checks": checks,
        "features": features,
        "routing": {
            "run_expensive_stage": route == "expensive_candidate",
            "terminal_cheap_decision": route in {"cheap_reject", "unresolved"},
        },
        "thresholds": {
            "min_target_margin": MIN_TARGET_MARGIN,
            "min_remote_text_similarity": MIN_REMOTE_TEXT_SIMILARITY,
            "min_remote_token_recall": MIN_REMOTE_TOKEN_RECALL,
            "max_context_gap_sec": MAX_CONTEXT_GAP_SEC,
        },
        "timeline_causal": True,
        "used_batch_fields_for_selection": False,
        "forbidden_inputs": [
            "authoritative_batch_text",
            "future_chunks",
            "future_target_me_enrollment",
            "evaluation_reference",
        ],
    }
    payload["decision_fingerprint_sha256"] = canonical_sha256(payload)
    return payload


def route_rows(
    rows: list[dict[str, Any]],
    *,
    remote_text_by_id: dict[str, str] | None = None,
    session: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    remote_text_by_id = remote_text_by_id or {}
    ordered = sorted(
        rows,
        key=lambda row: (
            safe_int(source_row(row).get("chunk_index")),
            safe_float(source_row(row).get("start")),
            source_id(row),
        ),
    )
    decisions = [
        _base_decision(
            row,
            remote_text=remote_text_by_id.get(source_id(row), ""),
            session=session,
        )
        for row in ordered
    ]
    direct_ids = {
        str(decision.get("id"))
        for decision in decisions
        if decision.get("route") == "expensive_candidate"
    }
    by_id = {str(decision.get("id")): decision for decision in decisions}
    for index, row in enumerate(ordered):
        row_id = source_id(row)
        if row_id in direct_ids:
            continue
        source = source_row(row)
        neighbor_id: str | None = None
        neighbor_gap: float | None = None
        # Context may only flow forward from evidence already available when this row ends.
        # Looking at index + 1 would silently make offline routing stronger than recording-time
        # routing and violate the causal contract.
        for candidate_index in range(index - 1, -1, -1):
            neighbor = source_row(ordered[candidate_index])
            candidate_id = source_id(ordered[candidate_index])
            if safe_int(neighbor.get("chunk_index")) < safe_int(source.get("chunk_index")):
                break
            if candidate_id not in direct_ids:
                continue
            if safe_int(neighbor.get("chunk_index")) != safe_int(source.get("chunk_index")):
                continue
            if safe_float(neighbor.get("end")) > safe_float(source.get("end")):
                continue
            gap = max(0.0, safe_float(source.get("start")) - safe_float(neighbor.get("end")))
            if gap <= MAX_CONTEXT_GAP_SEC:
                neighbor_id = candidate_id
                neighbor_gap = gap
                break
            if safe_float(neighbor.get("end")) < safe_float(source.get("start")):
                break
        if neighbor_id is None:
            continue
        decision = by_id[row_id]
        decision["route"] = "expensive_candidate"
        decision["reasons"] = ["contiguous_context_for_positive_target_me_candidate"]
        decision["routing"] = {
            "run_expensive_stage": True,
            "terminal_cheap_decision": False,
            "context_seed_id": neighbor_id,
            "context_gap_sec": round(neighbor_gap or 0.0, 6),
        }
        decision.pop("decision_fingerprint_sha256", None)
        decision["decision_fingerprint_sha256"] = canonical_sha256(decision)

    routed: list[dict[str, Any]] = []
    for row in ordered:
        row_id = source_id(row)
        decision = by_id[row_id]
        if decision.get("route") != "expensive_candidate":
            continue
        source = dict(source_row(row))
        source.setdefault("source_selection_id", row_id)
        source["cheap_prefilter_route"] = "expensive_candidate"
        source["cheap_prefilter_decision_fingerprint_sha256"] = decision.get(
            "decision_fingerprint_sha256"
        )
        routed.append(source)
    return routed, decisions


def decision_set_fingerprint(decisions: list[dict[str, Any]]) -> str:
    stable = sorted(
        (
            {
                "id": row.get("id"),
                "route": row.get("route"),
                "decision_fingerprint_sha256": row.get("decision_fingerprint_sha256"),
            }
            for row in decisions
        ),
        key=lambda row: str(row.get("id") or ""),
    )
    return canonical_sha256(stable)
