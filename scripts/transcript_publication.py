"""Lossless read-view projection with explicit timing and review uncertainty."""

from __future__ import annotations

import math
from typing import Any

from micro_asr_evidence import assess_utterance
import transcript_interval_evidence as interval_evidence

VERSION = "display_turns_v2"


def interval(row: dict[str, Any]) -> tuple[float, float] | None:
    values = (row.get("start"), row.get("end"))
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
        return None
    start, end = values
    return (float(start), float(end)) if 0 <= start <= end else None


def format_time(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        return "??:??"
    return f"{int(value // 60):02d}:{int(value % 60):02d}"


def review_reasons(quality: Any) -> list[dict[str, str]]:
    if not isinstance(quality, dict):
        return []
    reasons: list[dict[str, str]] = []

    def visit(value: Any, path: str) -> None:
        if isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, f"{path}.{index}")
            return
        if not isinstance(value, dict) or value.get("status") == "cleared":
            return
        if value.get("needs_review") is True or value.get("status") == "needs_review":
            scope = str(value.get("scope") or path).lower()
            facet = "review"
            if any(word in scope for word in ("order", "timing", "chronology")):
                facet = "time"
            elif any(word in scope for word in ("local_voice", "role")):
                facet = "role"
            elif any(word in scope for word in ("text", "integrity", "micro")):
                facet = "text"
            elif "speaker" in scope:
                facet = "speaker"
            reasons.append({"facet": facet, "reason": str(value.get("reason") or "unresolved_source_review"),
                            "evidence_key": path or "quality"})
        for key, item in value.items():
            if isinstance(item, (dict, list)):
                visit(item, f"{path}.{key}" if path else key)

    visit(quality, "")
    detailed = [r for r in reasons if r["evidence_key"] != "quality"]
    if detailed:
        return [r for r in reasons if r["evidence_key"] != "quality" or quality.get("reason")]
    return reasons


def display_turns(utterances: list[dict[str, Any]], attributions: dict[str, dict[str, Any]] | None = None,
                  timing_evidence: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for position, utterance in enumerate(utterances):
        uid = str(utterance.get("id") or "")
        parent = interval(utterance)
        remote = utterance.get("role") == "remote"
        timing = ((timing_evidence or {}).get("utterances") or {}).get(uid) or {}
        if (timing_evidence or {}).get("status") != "checked":
            timing = {}
        turns = utterance.get("speaker_turns") if remote else None
        reasons = review_reasons(utterance.get("quality"))
        interval_review = interval_evidence.assess_utterance(utterance)
        if interval_review:
            reasons.extend({"facet": "time", "reason": reason, "evidence_key": "interval_provenance"}
                           for reason in interval_review["reasons"])
        micro_review = assess_utterance(utterance)
        if micro_review:
            reasons.extend({"facet": "text", "reason": reason, "evidence_key": "quality.repair.micro_reasr"}
                           for reason in micro_review["reasons"])
        if turns and (not isinstance(turns, list) or any(not isinstance(t, dict) for t in turns)
                      or "".join(str(t.get("text") or "") for t in turns) != str(utterance.get("text") or "")):
            reasons.append({"facet": "speaker", "reason": "speaker_turn_text_mismatch", "evidence_key": "speaker_turns"})
            turns = None
        attributed = (attributions or {}).get(uid) or {}
        label = (attributed.get("speaker_label") or utterance.get("speaker_label") or "remote_speaker_unknown") if remote else "Me"
        if label == "Colleagues":
            label = "remote_speaker_unknown"
        if not turns:
            turns = [{"text": utterance.get("text"), "start": utterance.get("start"),
                      "end": utterance.get("end"), "speaker_id": label}]
        bounds = [interval(turn) for turn in turns]
        nested_valid = parent is not None and all(
            b is not None and parent[0] - 0.001 <= b[0] <= b[1] <= parent[1] + 0.001
            and (index == 0 or bounds[index - 1] is not None and bounds[index - 1][0] <= b[0])
            for index, b in enumerate(bounds)
        )
        # A fallback applies to the whole parent so sorting cannot reverse its words.
        for index, turn in enumerate(turns):
            chosen = bounds[index] if nested_valid else parent
            warnings = reasons + review_reasons(turn.get("quality"))
            basis = "speaker_turn" if nested_valid and remote else ("utterance" if nested_valid else "parent_interval" if parent else "unknown")
            if not nested_valid:
                warnings.append({"facet": "time", "reason": "speaker_turn_time_conflict" if parent else "timestamp_unavailable",
                                 "evidence_key": "speaker_turns" if remote else "utterance"})
            if timing and parent and timing.get("original_interval") == {"start": parent[0], "end": parent[1]}:
                warnings.append({"facet": "time", "reason": timing["reason"], "evidence_key": "acoustic_timing"})
                onset = timing.get("sound_not_before")
                if isinstance(onset, (int, float)) and math.isfinite(onset) and chosen and chosen[0] < onset:
                    # Display a lower bound, not invented word timestamps. All text survives.
                    chosen = (onset, max(onset, chosen[1]))
                    basis = "acoustic_lower_bound"
                elif onset is None:
                    basis = "unsupported_audio_time"
            result.append({
                "utterance_id": uid, "parent_position": position, "turn_index": index,
                "role": utterance.get("role"), "speaker_label": (turn.get("speaker_id") or "remote_speaker_unknown") if remote else "Me",
                "text": str(turn.get("text") or ""),
                "start": chosen[0] if chosen else None, "end": chosen[1] if chosen else None,
                "time_basis": basis,
                "source_interval": {"start": turn.get("start"), "end": turn.get("end")},
                "interval_provenance": interval_evidence.provenance(utterance),
                "review_reasons": warnings,
            })
    result.sort(key=lambda r: (r["start"] if r["start"] is not None else float("inf"), r["parent_position"], r["turn_index"]))
    return result


def render_body(turns: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for turn in turns:
        label = turn["speaker_label"]
        suffix = " [unattributed]" if label == "remote_speaker_unknown" else ""
        facets = sorted({row["facet"] for row in turn["review_reasons"]})
        if facets:
            suffix += " [needs_review: " + ", ".join(facets) + "]"
        lines.extend([f"## {format_time(turn['start'])} {label}{suffix}", "", turn["text"].strip(), ""])
        if turn["time_basis"] == "parent_interval":
            lines.extend([f"> Approximate parent interval: {format_time(turn['start'])}-{format_time(turn['end'])}; nested timing is missing or conflicting.", ""])
        if turn["time_basis"] == "acoustic_lower_bound":
            lines.extend(["> Approximate sound-onset lower bound after digital silence; word timing is not established.", ""])
        if turn["time_basis"] == "unsupported_audio_time":
            lines.extend(["> No nonzero audio in the checked remote window; source timing is unverified.", ""])
        if facets:
            details = "; ".join(sorted({row["reason"] for row in turn["review_reasons"]}))
            lines.extend([f"> Review required ({', '.join(facets)}): {details}. Source: `{turn['utterance_id']}`.", ""])
    return lines
