#!/usr/bin/env python3
from __future__ import annotations

import hashlib
from collections import Counter
from typing import Any, Callable


SCHEMA = "murmurmark.live_order_role_reconciliation/v1"
CLASSIFIER_VERSION = "1.1.0"

EXPECTED_ROLE_BY_SOURCE = {
    "mic_segment": "Me",
    "remote_segment": "Colleagues",
}


def _expected_role_for_source(source: str) -> str | None:
    exact = EXPECTED_ROLE_BY_SOURCE.get(source)
    if exact is not None:
        return exact
    if source.startswith("mic_"):
        return "Me"
    if source.startswith("remote_"):
        return "Colleagues"
    return None


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _turn(item: dict[str, Any], key: str) -> dict[str, Any]:
    value = item.get(key)
    return value if isinstance(value, dict) else {}


def _stable_id(session: str, profile: str, previous_id: str, current_id: str) -> str:
    raw = "\0".join((session, profile, previous_id, current_id)).encode("utf-8")
    return f"lor_{hashlib.sha256(raw).hexdigest()[:16]}"


def _source_role_evidence(turn: dict[str, Any]) -> dict[str, Any]:
    source = str(turn.get("source") or "")
    role = str(turn.get("role") or "")
    expected_role = _expected_role_for_source(source)
    return {
        "source": source,
        "role": role,
        "expected_role": expected_role,
        "consistent": expected_role is not None and role == expected_role,
    }


def legacy_order_risk_classification(item: dict[str, Any]) -> dict[str, Any]:
    previous = _turn(item, "previous")
    current = _turn(item, "current")
    previous_tokens = safe_int(previous.get("turn_content_token_count"))
    current_tokens = safe_int(current.get("turn_content_token_count"))
    previous_margin = safe_float(previous.get("score_margin"), 1.0)
    current_margin = safe_float(current.get("score_margin"), 1.0)
    min_margin = min(previous_margin, current_margin)
    previous_plausible = safe_int(previous.get("plausible_match_count"))
    current_plausible = safe_int(current.get("plausible_match_count"))
    max_plausible = max(previous_plausible, current_plausible)
    min_tokens = min(previous_tokens or 999, current_tokens or 999)
    same_source = bool(item.get("same_source"))
    same_chunk = bool(item.get("same_chunk"))
    ambiguous = bool(
        item.get("match_ambiguity") == "ambiguous"
        or previous.get("ambiguous_match")
        or current.get("ambiguous_match")
    )
    batch_delta = abs(safe_float(item.get("batch_start_delta_sec")))
    live_delta = abs(safe_float(item.get("live_start_delta_sec")))
    previous_live_start = safe_float(item.get("previous_live_start"))
    current_live_start = safe_float(item.get("current_live_start"))
    previous_live_end = safe_float(previous.get("end"))
    current_batch_start = safe_float(item.get("current_batch_start"))
    previous_inside = bool(item.get("previous_live_inside_own_batch_interval"))
    current_inside = bool(item.get("current_live_inside_own_batch_interval"))
    source_pair = str(item.get("source_pair") or "")
    reference_gap_like = bool(
        same_source
        and same_chunk
        and current_inside
        and not previous_inside
        and live_delta <= 5
        and previous_margin <= 0.25
        and previous_plausible >= 5
        and previous_live_end > 0
        and current_batch_start > 0
        and previous_live_start <= current_live_start
        and previous_live_end <= current_batch_start + 0.5
    )

    label = "needs_review_order_risk"
    severity = "blocking"
    confidence = "medium"
    reason = "order risk remains contentful and needs manual or algorithmic repair"
    if ambiguous and min_margin <= 0.15 and max_plausible >= 8:
        label = "weak_generic_match_false_positive_candidate"
        severity = "advisory"
        confidence = "high"
        reason = "match is ambiguous, has small score margin, and many plausible alternatives"
    elif same_source and min_tokens <= 2 and max_plausible >= 20 and batch_delta >= 120:
        label = "same_source_short_high_plausible_far_match_candidate"
        severity = "advisory"
        confidence = "high"
        reason = "same-source reorder is driven by a short phrase with many plausible far-away batch matches"
    elif same_source and min_tokens <= 2 and max_plausible >= 5 and min_margin <= 0.25 and batch_delta >= 20:
        label = "same_source_weak_short_match_candidate"
        severity = "advisory"
        confidence = "medium"
        reason = "same-source reorder is driven by a very short live phrase matched far away in batch"
    elif reference_gap_like:
        label = "same_source_reference_gap_or_weak_match_candidate"
        severity = "advisory"
        confidence = "medium"
        reason = "same-source reorder is explained by a weak previous match near a batch timing/text gap"
    elif same_chunk and not same_source and live_delta <= 8 and (not previous_inside or not current_inside):
        label = "boundary_retime_candidate"
        severity = "blocking"
        confidence = "medium"
        reason = "cross-source order risk is near a chunk boundary and at least one live turn is outside its batch interval"
    elif same_chunk and same_source and batch_delta >= 20:
        label = "same_source_timeline_reorder_candidate"
        severity = "blocking"
        confidence = "medium"
        reason = "same-source order risk has a large batch-time reversal that is not explained by weak-match rules"
    elif "mic_segment" in source_pair and "remote_segment" in source_pair:
        label = "cross_source_order_risk"
        severity = "blocking"
        confidence = "medium"
        reason = "mic/remote order risk remains after current live profile"

    return {
        "label": label,
        "severity": severity,
        "confidence": confidence,
        "reason": reason,
        "features": {
            "same_chunk": same_chunk,
            "same_source": same_source,
            "source_pair": source_pair,
            "ambiguous_match": ambiguous,
            "min_score_margin": round(min_margin, 6),
            "max_plausible_match_count": max_plausible,
            "min_turn_content_token_count": 0 if min_tokens == 999 else min_tokens,
            "batch_start_delta_abs_sec": round(batch_delta, 3),
            "live_start_delta_abs_sec": round(live_delta, 3),
            "previous_live_end": round(previous_live_end, 3),
            "current_batch_start": round(current_batch_start, 3),
            "reference_gap_like": reference_gap_like,
            "previous_live_inside_own_batch_interval": previous_inside,
            "current_live_inside_own_batch_interval": current_inside,
        },
    }


def classify_order_risk(
    item: dict[str, Any],
    *,
    session: str = "",
    profile: str = "",
    previous_classification: dict[str, Any] | None = None,
) -> dict[str, Any]:
    previous = _turn(item, "previous")
    current = _turn(item, "current")
    previous_source_role = _source_role_evidence(previous)
    current_source_role = _source_role_evidence(current)

    previous_start = safe_float(previous.get("start"), safe_float(item.get("previous_live_start")))
    previous_end = safe_float(previous.get("end"), previous_start)
    current_start = safe_float(current.get("start"), safe_float(item.get("current_live_start")))
    current_end = safe_float(current.get("end"), current_start)
    live_overlap_sec = max(0.0, previous_end - current_start)
    live_gap_sec = max(0.0, current_start - previous_end)
    shorter_duration = max(
        0.001,
        min(max(0.0, previous_end - previous_start), max(0.0, current_end - current_start)),
    )
    live_overlap_ratio = min(1.0, live_overlap_sec / shorter_duration)

    previous_batch_start = safe_float(previous.get("batch_start"), safe_float(item.get("previous_batch_start")))
    current_batch_start = safe_float(current.get("batch_start"), safe_float(item.get("current_batch_start")))
    previous_temporal_error = abs(previous_batch_start - previous_start)
    current_temporal_error = abs(current_batch_start - current_start)
    max_temporal_error = max(previous_temporal_error, current_temporal_error)

    previous_margin = safe_float(previous.get("score_margin"), 1.0)
    current_margin = safe_float(current.get("score_margin"), 1.0)
    min_margin = min(previous_margin, current_margin)
    previous_plausible = safe_int(previous.get("plausible_match_count"))
    current_plausible = safe_int(current.get("plausible_match_count"))
    max_plausible = max(previous_plausible, current_plausible)
    ambiguous = bool(
        item.get("match_ambiguity") == "ambiguous"
        or previous.get("ambiguous_match")
        or current.get("ambiguous_match")
    )

    previous_inside = bool(item.get("previous_live_inside_own_batch_interval"))
    current_inside = bool(item.get("current_live_inside_own_batch_interval"))
    outside_reference_count = int(not previous_inside) + int(not current_inside)
    same_source = bool(item.get("same_source")) or (
        previous_source_role["source"] != ""
        and previous_source_role["source"] == current_source_role["source"]
    )
    cross_source = not same_source
    causal_start_order = current_start >= previous_start
    source_role_consistent = bool(
        previous_source_role["consistent"]
        and current_source_role["consistent"]
        and not item.get("role_mismatch_in_pair")
    )

    classification = "unresolved"
    disposition = "blocking"
    action = "keep_blocking_and_run_offline_replay"
    confidence = "medium"
    reason = "available causal and matcher evidence does not resolve the order risk"
    shadow_repair_required = False

    if not source_role_consistent:
        classification = "real_role_conflict"
        action = "materialize_shadow_role_repair"
        confidence = "high"
        reason = "live source and published role disagree"
        shadow_repair_required = True
    elif not causal_start_order:
        classification = "real_timeline_conflict"
        action = "materialize_shadow_timeline_repair"
        confidence = "high"
        reason = "published live order contradicts causal segment start times"
        shadow_repair_required = True
    elif (
        max_temporal_error >= 120.0
        and max_plausible >= 5
        and (ambiguous or min_margin <= 0.25 or outside_reference_count > 0)
    ):
        classification = "matcher_temporal_false_positive"
        disposition = "advisory"
        action = "exclude_reference_match_from_effective_gate"
        confidence = "high"
        reason = "a weak or non-unique reference match is temporally remote from the causal live turn"
    elif live_overlap_sec >= 0.2 and cross_source:
        classification = "causal_cross_source_overlap"
        disposition = "advisory"
        action = "keep_live_timeline_without_total_order_claim"
        confidence = "high"
        reason = "mic and remote turns overlap in causal live time, so batch start order is not a contradiction"
    elif live_overlap_sec >= 0.2 and same_source:
        classification = "causal_same_source_overlap_context"
        disposition = "advisory"
        action = "keep_live_timeline_and_preserve_overlap_audit"
        confidence = "high"
        reason = "same-source turns overlap because adjacent decode context is not a strict total-order interval"
    elif (
        live_gap_sec > 1.0
        and safe_float(item.get("batch_start_delta_sec")) < -1.0
        and not ambiguous
        and min_margin >= 0.25
        and max_plausible <= 2
        and max_temporal_error <= 5.0
    ):
        classification = "real_timeline_conflict"
        action = "materialize_shadow_timeline_repair"
        confidence = "high"
        reason = "non-overlapping live turns contradict two strong, temporally local batch references"
        shadow_repair_required = True
    elif (
        live_gap_sec <= 1.0
        and ambiguous
        and min_margin <= 0.20
        and max_plausible >= 2
        and outside_reference_count > 0
    ):
        classification = "matcher_ambiguous_reference"
        disposition = "advisory"
        action = "exclude_reference_match_from_effective_gate"
        confidence = "high"
        reason = "causal live order is coherent near a boundary while the batch reference match is ambiguous"
    elif previous_classification and previous_classification.get("severity") == "advisory" and (
        ambiguous or min_margin <= 0.25 or max_plausible >= 5
    ):
        classification = "matcher_ambiguous_reference"
        disposition = "advisory"
        action = "exclude_reference_match_from_effective_gate"
        confidence = str(previous_classification.get("confidence") or "medium")
        reason = "legacy weak-match evidence remains sufficient to keep this row advisory"

    previous_id = str(previous.get("live_id") or item.get("previous_live_id") or "")
    current_id = str(current.get("live_id") or item.get("current_live_id") or "")
    return {
        "id": _stable_id(session, profile, previous_id, current_id),
        "session": session,
        "profile": profile,
        "previous_classification": previous_classification or {
            "label": "unclassified",
            "severity": "blocking",
            "confidence": "unknown",
        },
        "classification": classification,
        "disposition": disposition,
        "action": action,
        "confidence": confidence,
        "reason": reason,
        "shadow_repair_required": shadow_repair_required,
        "shadow_repair_applied": False,
        "previous_live_id": previous_id,
        "current_live_id": current_id,
        "previous_text": previous.get("text"),
        "current_text": current.get("text"),
        "category": item.get("category"),
        "primary_risk": item.get("primary_risk"),
        "machine_evidence": {
            "causal_live_timeline": {
                "previous_start": round(previous_start, 3),
                "previous_end": round(previous_end, 3),
                "current_start": round(current_start, 3),
                "current_end": round(current_end, 3),
                "start_order_consistent": causal_start_order,
                "overlap_sec": round(live_overlap_sec, 3),
                "overlap_ratio_of_shorter": round(live_overlap_ratio, 6),
                "gap_sec": round(live_gap_sec, 3),
                "same_chunk": bool(item.get("same_chunk")),
                "same_source": same_source,
                "source_pair": item.get("source_pair"),
            },
            "source_role": {
                "previous": previous_source_role,
                "current": current_source_role,
                "pair_consistent": source_role_consistent,
                "reported_role_pair": item.get("role_pair"),
                "reported_role_mismatch": bool(item.get("role_mismatch_in_pair")),
            },
            "batch_matcher": {
                "previous_batch_id": previous.get("batch_id"),
                "current_batch_id": current.get("batch_id"),
                "previous_batch_start": round(previous_batch_start, 3),
                "current_batch_start": round(current_batch_start, 3),
                "previous_temporal_error_sec": round(previous_temporal_error, 3),
                "current_temporal_error_sec": round(current_temporal_error, 3),
                "max_temporal_error_sec": round(max_temporal_error, 3),
                "ambiguous": ambiguous,
                "min_score_margin": round(min_margin, 6),
                "max_plausible_match_count": max_plausible,
                "previous_inside_own_batch_interval": previous_inside,
                "current_inside_own_batch_interval": current_inside,
                "outside_reference_count": outside_reference_count,
            },
            "online_evidence_scope": {
                "uses_only_live_timing_for_disposition": classification
                in {
                    "causal_cross_source_overlap",
                    "causal_same_source_overlap_context",
                    "real_role_conflict",
                    "real_timeline_conflict",
                },
                "batch_match_used_only_as_reference_diagnostic": classification
                in {"matcher_temporal_false_positive", "matcher_ambiguous_reference"},
                "remote_forbidden_evidence": "not_required_for_order_only_classification",
                "causal_speaker_evidence": (
                    "source_role_consistent" if source_role_consistent else "source_role_conflict"
                ),
            },
        },
    }


def build_reconciliation(
    rows: list[dict[str, Any]],
    *,
    session: str = "",
    profile: str = "",
    previous_classifier: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for row in rows:
        previous = previous_classifier(row) if previous_classifier else None
        items.append(
            classify_order_risk(
                row,
                session=session,
                profile=profile,
                previous_classification=previous,
            )
        )

    previous_blocking_count = sum(
        1 for item in items if (item.get("previous_classification") or {}).get("severity") == "blocking"
    )
    blocking_count = sum(1 for item in items if item.get("disposition") == "blocking")
    advisory_count = sum(1 for item in items if item.get("disposition") == "advisory")
    resolved_previous_blocking_count = sum(
        1
        for item in items
        if (item.get("previous_classification") or {}).get("severity") == "blocking"
        and item.get("disposition") == "advisory"
    )
    stable_classification_count = sum(1 for item in items if item.get("classification") != "unresolved")
    shadow_repair_required_count = sum(1 for item in items if item.get("shadow_repair_required"))
    shadow_repair_applied_count = sum(1 for item in items if item.get("shadow_repair_applied"))

    def counts(key: str) -> dict[str, int]:
        return dict(sorted(Counter(str(item.get(key) or "unknown") for item in items).items()))

    return {
        "schema": SCHEMA,
        "classifier_version": CLASSIFIER_VERSION,
        "status": "passed" if blocking_count == 0 else "blocked",
        "session": session,
        "profile": profile,
        "item_count": len(items),
        "previous_blocking_count": previous_blocking_count,
        "resolved_previous_blocking_count": resolved_previous_blocking_count,
        "blocking_count": blocking_count,
        "advisory_count": advisory_count,
        "stable_classification_count": stable_classification_count,
        "unresolved_count": len(items) - stable_classification_count,
        "shadow_repair_required_count": shadow_repair_required_count,
        "shadow_repair_applied_count": shadow_repair_applied_count,
        "repair_not_required_count": resolved_previous_blocking_count,
        "selected_shadow_profile_changed": False,
        "by_classification": counts("classification"),
        "by_disposition": counts("disposition"),
        "by_action": counts("action"),
        "items": items,
        "interpretation": (
            "effective order gate uses causal live timing and source-role evidence; raw matcher rows remain in audit"
        ),
    }
