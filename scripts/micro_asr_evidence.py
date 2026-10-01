#!/usr/bin/env python3
"""Deterministic evidence checks for short micro-ASR selections."""

from __future__ import annotations

import difflib
import math
import re
from typing import Any


SCHEMA = "murmurmark.micro_reasr_selection_stability/v1"
REVISION = "target_ownership_v2"
SHORT_SELECTION_MAX_MS = 1_250
MAX_UNSUPPORTED_CHARS_PER_SEC = 24.0
SUPPORT_SIMILARITY = 0.72
DISAGREEMENT_SIMILARITY = 0.45
TARGET_TOLERANCE_MS = 250


def finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def target_ownership(text: str, meta: dict[str, Any], start_ms: int, end_ms: int) -> dict[str, Any]:
    """Segment overlap cannot establish ownership of all its words by an island."""
    rows = meta.get("rows") if isinstance(meta.get("rows"), list) else []
    outside: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or not normalize_text(row.get("text")):
            continue
        start, end = row.get("start_ms"), row.get("end_ms")
        if not finite_number(start) or not finite_number(end) or end <= start:
            outside.append({"reason": "decoded_row_time_unavailable"})
        elif start < start_ms - TARGET_TOLERANCE_MS or end > end_ms + TARGET_TOLERANCE_MS:
            outside.append({"reason": "decoded_row_outside_target", "start_ms": start, "end_ms": end})
    # Optional word evidence must describe the complete selected text in global ms.
    # Missing words, offsets or a partial match are not a positive vote.
    words = meta.get("selected_words")
    words_match = bool(
        isinstance(words, list) and words
        and all(isinstance(word, dict) for word in words)
        and normalize_text(" ".join(str(word.get("word") or "") for word in words)) == normalize_text(text)
        and all(
            finite_number(word.get("start_ms")) and finite_number(word.get("end_ms"))
            and start_ms - 80 <= word["start_ms"] < word["end_ms"] <= end_ms + 80
            and (index == 0 or words[index - 1]["end_ms"] <= word["start_ms"])
            for index, word in enumerate(words)
        )
    )
    return {"status": "needs_review" if outside and not words_match else "supported" if words_match else "not_established",
            "outside_rows": outside, "word_bounded_support": words_match,
            "target_start_ms": start_ms, "target_end_ms": end_ms,
            "tolerance_ms": TARGET_TOLERANCE_MS}


def normalize_text(value: Any) -> str:
    text = str(value or "").lower().replace("ё", "е")
    text = re.sub(r"[^\wа-яa-z0-9/+-]+", " ", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip()


def text_similarity(left: Any, right: Any) -> float:
    left_text = normalize_text(left)
    right_text = normalize_text(right)
    if not left_text or not right_text:
        return 0.0
    sequence = difflib.SequenceMatcher(None, left_text, right_text).ratio()
    left_tokens = set(left_text.split())
    right_tokens = set(right_text.split())
    token_union = left_tokens | right_tokens
    token_jaccard = len(left_tokens & right_tokens) / len(token_union) if token_union else 0.0
    return max(sequence, token_jaccard)


def attempt_text(attempt: dict[str, Any]) -> str:
    for key in ("selected_text", "raw_text"):
        text = str(attempt.get(key) or "").strip()
        if text:
            return text
    rows = attempt.get("rows") if isinstance(attempt.get("rows"), list) else []
    return " ".join(
        str(row.get("text") or "").strip()
        for row in rows
        if isinstance(row, dict) and str(row.get("text") or "").strip()
    ).strip()


def source_family(source_label: Any) -> str | None:
    label = str(source_label or "")
    if label.startswith("current_"):
        return None
    if label == "raw_for_asr":
        return "raw"
    if label in {"clean_local_fir", "role_masked_for_asr"}:
        return "filtered"
    return None


def assess_micro_reasr_selection(
    selected_text: str,
    micro_meta: dict[str, Any],
    start_ms: int,
    end_ms: int,
) -> dict[str, Any]:
    """Require review when a short successful selection lacks stable evidence.

    Raw mic and filtered mic form two evidence families. clean_local_fir and
    role_masked_for_asr are deliberately one family because both are derived
    from the same capture and must not masquerade as independent votes.
    """

    duration_ms = max(1, end_ms - start_ms)
    normalized = normalize_text(selected_text)
    compact_chars = len(normalized.replace(" ", ""))
    chars_per_sec = compact_chars / (duration_ms / 1000.0)
    attempts = micro_meta.get("attempts") if isinstance(micro_meta.get("attempts"), list) else []

    canonical_rows: list[dict[str, Any]] = []
    support_sources: set[str] = set()
    support_families: set[str] = set()
    texts_by_family: dict[str, list[str]] = {"raw": [], "filtered": []}
    for attempt in attempts:
        if not isinstance(attempt, dict) or str(attempt.get("status") or "") != "ok":
            continue
        family = source_family(attempt.get("source_label"))
        if family is None:
            continue
        text = attempt_text(attempt)
        if not normalize_text(text):
            continue
        similarity = text_similarity(selected_text, text)
        source_label = str(attempt.get("source_label") or "")
        canonical_rows.append(
            {
                "source_label": source_label,
                "window_label": attempt.get("window_label"),
                "text": text,
                "similarity": round(similarity, 6),
            }
        )
        texts_by_family[family].append(text)
        if similarity >= SUPPORT_SIMILARITY:
            support_sources.add(source_label)
            support_families.add(family)

    independent_support = {"raw", "filtered"} <= support_families
    short_selection = duration_ms <= SHORT_SELECTION_MAX_MS
    reasons: list[str] = []
    ownership = target_ownership(selected_text, micro_meta, start_ms, end_ms)
    if ownership["status"] == "needs_review":
        reasons.append("micro_asr_context_not_owned_by_target")

    if short_selection and chars_per_sec > MAX_UNSUPPORTED_CHARS_PER_SEC and not independent_support:
        reasons.append("implausible_short_island_speech_rate")

    selected_source = str(micro_meta.get("source_label") or "")
    if short_selection and selected_source.startswith("current_") and not support_sources:
        reasons.append("baseline_only_selection_without_canonical_support")

    cross_family_similarity: float | None = None
    if texts_by_family["raw"] and texts_by_family["filtered"]:
        cross_family_similarity = max(
            text_similarity(raw_text, filtered_text)
            for raw_text in texts_by_family["raw"]
            for filtered_text in texts_by_family["filtered"]
        )
        if (
            short_selection
            and len(normalized.split()) >= 2
            and not independent_support
            and cross_family_similarity < DISAGREEMENT_SIMILARITY
        ):
            reasons.append("short_island_source_disagreement")

    return {
        "schema": SCHEMA,
        "revision": REVISION,
        "status": "needs_review" if reasons else "stable",
        "reasons": reasons,
        "duration_ms": duration_ms,
        "chars_per_sec": round(chars_per_sec, 6),
        "selected_source_label": selected_source,
        "support_sources": sorted(support_sources),
        "support_families": sorted(support_families),
        "independent_support": independent_support,
        "cross_family_similarity": (
            round(cross_family_similarity, 6) if cross_family_similarity is not None else None
        ),
        "thresholds": {
            "short_selection_max_ms": SHORT_SELECTION_MAX_MS,
            "max_unsupported_chars_per_sec": MAX_UNSUPPORTED_CHARS_PER_SEC,
            "support_similarity": SUPPORT_SIMILARITY,
            "disagreement_similarity": DISAGREEMENT_SIMILARITY,
        },
        "canonical_attempts": canonical_rows,
        "target_ownership": ownership,
    }


def assess_utterance(row: dict[str, Any]) -> dict[str, Any] | None:
    quality = row.get("quality") if isinstance(row.get("quality"), dict) else {}
    repair = quality.get("repair") if isinstance(quality.get("repair"), dict) else {}
    micro = repair.get("micro_reasr") if isinstance(repair.get("micro_reasr"), dict) else {}
    if row.get("role") not in {"me", "mic", "Me"} or repair.get("action") != "micro_reasr" or micro.get("status") != "ok":
        return None
    start, end = repair.get("island_start_ms"), repair.get("island_end_ms")
    if not finite_number(start) and finite_number(row.get("start")):
        start = round(row["start"] * 1000)
    if not finite_number(end) and finite_number(row.get("end")):
        end = round(row["end"] * 1000)
    if not finite_number(start) or not finite_number(end) or end <= start:
        return None
    text = str(micro.get("selected_text") or micro.get("raw_text") or row.get("text") or "")
    assessed = assess_micro_reasr_selection(text, micro, int(start), int(end))
    stored = micro.get("selection_stability")
    if isinstance(stored, dict) and stored.get("status") == "needs_review":
        assessed["reasons"] = sorted(set(assessed["reasons"]) | set(stored.get("reasons") or []))
        assessed["status"] = "needs_review"
    return assessed if assessed["status"] == "needs_review" else None
