"""Current-dialogue overlap construction with legacy reference compatibility."""

from __future__ import annotations

import math
from difflib import SequenceMatcher
from typing import Any, Callable


def role(row: dict[str, Any]) -> str:
    value = str(row.get("role") or row.get("speaker_label") or "").lower()
    source = str(row.get("source_track") or "").lower()
    if source == "mic" or value == "me":
        return "me"
    if source == "remote" or value in {"remote", "colleagues"}:
        return "remote"
    return value


def references(row: dict[str, Any]) -> tuple[str, str]:
    return (
        str(row.get("left_utterance_id") or row.get("me_utterance_id") or ""),
        str(row.get("right_utterance_id") or row.get("remote_utterance_id") or ""),
    )


def text(row: dict[str, Any]) -> str:
    return str(row.get("text") or row.get("corrected_text") or row.get("raw_text") or "")


def similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, left.lower(), right.lower()).ratio()


def build_overlaps(
    rows: list[dict[str, Any]],
    source_rows: list[dict[str, Any]] | None = None,
    *,
    text_similarity: Callable[[str, str], float] = similarity,
) -> list[dict[str, Any]]:
    """Rebuild intervals, retaining source annotations only for surviving pairs."""
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or "start" not in row or "end" not in row:
            raise ValueError("overlap dialogue row is missing timing data")
        identity = str(row.get("id") or "")
        if not identity or identity in seen:
            raise ValueError("overlap dialogue contains missing or duplicate utterance ids")
        seen.add(identity)
        start, end = float(row["start"]), float(row["end"])
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end < start:
            raise ValueError(f"invalid overlap interval: {identity}")
    annotations = {
        frozenset(references(row)): row
        for row in source_rows or [] if isinstance(row, dict)
    }
    result: list[dict[str, Any]] = []
    for me in (row for row in rows if role(row) == "me"):
        for remote in (row for row in rows if role(row) == "remote"):
            start = max(float(me["start"]), float(remote["start"]))
            end = min(float(me["end"]), float(remote["end"]))
            if end <= start:
                continue
            item = dict(annotations.get(frozenset((str(me["id"]), str(remote["id"]))), {}))
            item.update({
                "id": f"ov_{len(result) + 1:06d}",
                "start": round(start, 6), "end": round(end, 6),
                "duration_sec": round(end - start, 6),
                "left_utterance_id": me["id"], "right_utterance_id": remote["id"],
                "left_role": "me", "right_role": "remote",
                "me_utterance_id": me["id"], "remote_utterance_id": remote["id"],
                "me_text": text(me), "remote_text": text(remote),
                "text_similarity": round(text_similarity(text(me), text(remote)), 6),
            })
            if "duration" in item:
                item["duration"] = item["duration_sec"]
            result.append(item)
    return result
