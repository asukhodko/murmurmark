#!/usr/bin/env python3
"""Deterministic evidence checks for short micro-ASR selections."""

from __future__ import annotations

import difflib
import re
from typing import Any


SCHEMA = "murmurmark.micro_reasr_selection_stability/v1"
SHORT_SELECTION_MAX_MS = 1_250
MAX_UNSUPPORTED_CHARS_PER_SEC = 24.0
SUPPORT_SIMILARITY = 0.72
DISAGREEMENT_SIMILARITY = 0.45


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
    }
