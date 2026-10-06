"""Read-only checks for text retained after an unsupported interval refinement."""

from __future__ import annotations

import math
from typing import Any

from micro_asr_evidence import MAX_UNSUPPORTED_CHARS_PER_SEC, normalize_text, target_ownership


VERSION = "interval_ownership_v1"
TOLERANCE_SEC = 0.250
MIN_REVIEW_WORDS = 4
MATERIAL_TRIM_FRACTION = 1 / 3
MATERIAL_TRIM_WORDS_PER_SEC = 5.0


def mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def bounds(start: Any, end: Any) -> dict[str, float] | None:
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
           for v in (start, end)) or not 0 <= start < end:
        return None
    return {"start": float(start), "end": float(end)}


def provenance(row: dict[str, Any]) -> dict[str, Any]:
    quality = mapping(row.get("quality"))
    repair = mapping(quality.get("repair"))
    micro = mapping(repair.get("micro_reasr"))
    recognition = (bounds(micro.get("slice_start_ms"), micro.get("slice_end_ms"))
                   or bounds(repair.get("recognition_start_ms"), repair.get("recognition_end_ms")))
    if recognition:
        recognition = {key: value / 1000 for key, value in recognition.items()}
    return {
        "schema": "murmurmark.text_interval_provenance/v1",
        "source_candidate_id": row.get("source_candidate_id"),
        "source_track": row.get("source_track"),
        "source_interval": bounds(row.get("source_start"), row.get("source_end")),
        "recognition_interval": recognition,
        "selected_interval": bounds(row.get("start"), row.get("end")),
        "repair_action": repair.get("action"),
    }


def assess_utterance(row: dict[str, Any]) -> dict[str, Any] | None:
    """A refinement is not a lexical error, nor permission to delete or retime words."""
    normalized = normalize_text(row.get("text"))
    if not normalized:
        return None
    origin = provenance(row)
    source, target = origin["source_interval"], origin["selected_interval"]
    if not source or not target:
        return None
    narrowed = (target["start"] > source["start"] + TOLERANCE_SEC
                or target["end"] < source["end"] - TOLERANCE_SEC)
    if not narrowed:
        return None
    duration = target["end"] - target["start"]
    word_count = len(normalized.split())
    chars_per_sec = len(normalized.replace(" ", "")) / duration
    words_per_sec = word_count / duration
    trim_fraction = 1 - duration / (source["end"] - source["start"])
    # Silence trimming alone is common and is not a mandatory review question.
    # These are triage thresholds, not a claim that faster speech is impossible.
    if word_count < MIN_REVIEW_WORDS or not (
        chars_per_sec > MAX_UNSUPPORTED_CHARS_PER_SEC
        or (trim_fraction >= MATERIAL_TRIM_FRACTION and words_per_sec >= MATERIAL_TRIM_WORDS_PER_SEC)
    ):
        return None
    quality = mapping(row.get("quality"))
    repair = mapping(quality.get("repair"))
    # Only the existing complete selected-word evidence can justify a refinement.
    # A role score, VAD boundary or successful padded decode cannot do so.
    micro = mapping(repair.get("micro_reasr"))
    supported = target_ownership(str(row["text"]), micro, round(target["start"] * 1000),
                                 round(target["end"] * 1000))["word_bounded_support"]
    if supported:
        return None
    return {"schema": "murmurmark.text_interval_evidence/v1", "version": VERSION,
            "status": "needs_review", "reasons": ["text_interval_narrowed_without_word_support"],
            "provenance": origin, "word_bounded_support": False,
            "word_count": word_count, "words_per_sec": round(words_per_sec, 6),
            "chars_per_sec": round(chars_per_sec, 6), "trim_fraction": round(trim_fraction, 6),
            "thresholds": {"min_review_words": MIN_REVIEW_WORDS,
                           "max_unsupported_chars_per_sec": MAX_UNSUPPORTED_CHARS_PER_SEC,
                           "material_trim_fraction": MATERIAL_TRIM_FRACTION,
                           "material_trim_words_per_sec": MATERIAL_TRIM_WORDS_PER_SEC},
            "scope": "text_time", "automatic_edit_allowed": False}
