"""Shared identity checks for audio evidence and automatic review application."""
from __future__ import annotations

import hashlib
import json
import math
import re
from functools import lru_cache
from pathlib import Path
from typing import Any


RECEIPT_SCHEMA = "murmurmark.review_suggestion_receipt/v1"


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def normalize(value: Any) -> str:
    return " ".join(re.sub(r"[^0-9a-zа-я_./+-]+", " ", str(value or "").lower().replace("ё", "е")).split())


def role(row: dict[str, Any]) -> str:
    value = str(row.get("role") or "").lower()
    track = str(row.get("source_track") or "").lower()
    if value == "me" and track not in {"", "mic"}:
        return "conflict"
    if value in {"remote", "colleagues"} and track not in {"", "remote"}:
        return "conflict"
    return "me" if value == "me" or track == "mic" else "remote" if value in {"remote", "colleagues"} or track == "remote" else "unknown"


def bounds(row: dict[str, Any]) -> tuple[float, float] | None:
    try:
        start, end = float(row["start"]), float(row["end"])
        if math.isfinite(start) and math.isfinite(end) and end > start:
            return round(start, 3), round(end, 3)
    except (KeyError, TypeError, ValueError):
        pass
    return None


def signature(row: dict[str, Any]) -> tuple[Any, ...]:
    return role(row), normalize(row.get("text")), bounds(row)


def item_fingerprint_payload(item: dict[str, Any]) -> dict[str, Any]:
    interval = item.get("interval") or {}
    return {
        "session_id": str(item.get("session_id") or ""),
        "profile": str(item.get("profile") or ""),
        "interval": {key: round(float(interval.get(key) or 0), 3) for key in ("start", "end")},
        "utterance_ids": [str(value) for value in item.get("utterance_ids") or []],
        "utterances": [
            {
                "id": str(row.get("id") or ""), "role": str(row.get("role") or ""),
                "source_track": str(row.get("source_track") or ""),
                "start": round(float(row.get("start") or 0), 3),
                "end": round(float(row.get("end") or 0), 3), "text": normalize(row.get("text")),
            }
            for row in item.get("utterances") or [] if isinstance(row, dict)
        ],
    }


def item_fingerprint(item: dict[str, Any]) -> str:
    return digest(item_fingerprint_payload(item))


@lru_cache(maxsize=1024)
def _file_hash(path: str, size: int, mtime: int, ctime: int) -> str:
    hasher = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def file_identity(path: Path) -> dict[str, Any]:
    path = path.expanduser().resolve()
    try:
        stat = path.stat()
        return {"path": str(path), "sha256": _file_hash(str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)}
    except OSError:
        return {"path": str(path), "sha256": None}


def files_current(files: list[dict[str, Any]]) -> bool:
    return isinstance(files, list) and bool(files) and all(
        isinstance(item, dict) and item.get("path") and item.get("sha256")
        and file_identity(Path(item["path"]))["sha256"] == item["sha256"] for item in files
    )


def seal_evidence(row: dict[str, Any], *dependencies: Path) -> dict[str, Any]:
    paths = {str(path) for path in dependencies}
    clips = row.get("clips") or {}
    paths.update(str(clips[source]) for source in row.get("sources") or clips if clips.get(source))
    paths.update(str(value["path"]) for value in (row.get("source_scores") or {}).values() if isinstance(value, dict) and value.get("path"))
    row["evidence_files"] = [file_identity(Path(path)) for path in sorted(paths)]
    return row


def evidence_files_current(row: dict[str, Any]) -> bool:
    # Optional classification inputs may be absent, but their later appearance
    # must invalidate a verdict computed without that evidence.
    for item in row.get("classification_inputs") or []:
        if not isinstance(item, dict) or not item.get("path") or file_identity(Path(item["path"])) != item:
            return False
    if "evidence_files" in row:
        return files_current(row["evidence_files"])
    # Production decisions must be refreshed under the current policy before
    # use. Their content-addressed decodes remain reusable by the producer.
    return not row.get("schema")


def dialogue_path(row: dict[str, Any]) -> Path | None:
    session, profile = row.get("session"), row.get("input_profile")
    if not session or not profile or profile == "auto":
        return None
    suffix = "" if profile == "current" else f".{profile}"
    return Path(session) / "derived/transcript-simple/whisper-cpp/resolved" / f"clean_dialogue{suffix}.json"


@lru_cache(maxsize=32)
def _dialogue(path: str, sha256: str) -> list[dict[str, Any]]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value.get("utterances", []) if isinstance(value, dict) else []
    except (OSError, ValueError):
        return []


def review_targets(row: dict[str, Any]) -> list[dict[str, Any]]:
    text = row.get("text")
    snapshots = text if isinstance(text, list) else []
    path = dialogue_path(row)
    current: dict[str, dict[str, Any]] | None = None
    if path:
        identity = file_identity(path)
        if not identity.get("sha256"):
            return []
        current = {str(item.get("id")): item for item in _dialogue(str(path), identity["sha256"])}
    targets = []
    for snapshot in snapshots:
        if not isinstance(snapshot, dict) or not normalize(snapshot.get("text")):
            return []
        target = dict(snapshot)
        if current is not None:
            target = current.get(str(snapshot.get("id")), {})
            if role(target) != role(snapshot) or normalize(target.get("text")) != normalize(snapshot.get("text")):
                return []
            if ("start" in snapshot or "end" in snapshot) and bounds(target) != bounds(snapshot):
                return []
        elif len(snapshots) == 1 and bounds(target) is None:
            target.update(row.get("interval") or {})
        if bounds(target) is None or role(target) not in {"me", "remote"}:
            return []
        targets.append(target)
    return targets


def evidence_matches_review_row(row: dict[str, Any], candidate: dict[str, Any]) -> bool:
    if not row.get("session_id") or row.get("session_id") != candidate.get("session_id"):
        return False
    fingerprint = candidate.get("source_pack_item_fingerprint")
    if fingerprint:
        try:
            if fingerprint != item_fingerprint(candidate):
                return False
        except (TypeError, ValueError):
            return False
    targets = review_targets(row)
    evidence = [value for value in candidate.get("utterances") or [] if isinstance(value, dict)]
    if not targets or not evidence or not evidence_files_current(candidate):
        return False
    # IDs are intentionally excluded: an unchanged utterance can be renumbered.
    available = [signature(value) for value in evidence]
    for target in targets:
        key = signature(target)
        if key not in available:
            return False
        available.remove(key)
    # A clip's joint classification cannot stand in for a different Me phrase
    # merely because both phrases occur in the same padded clip.
    if any(key[0] == "me" for key in available):
        return False
    path = dialogue_path(row)
    if available and path:
        identity = file_identity(path)
        current = {signature(value) for value in _dialogue(str(path), identity.get("sha256") or "")}
        if any(key not in current for key in available):
            return False
    target_bounds = bounds(row.get("interval") or {})
    evidence_bounds = bounds(candidate.get("interval") or {})
    return bool(target_bounds and evidence_bounds and min(target_bounds[1], evidence_bounds[1]) > max(target_bounds[0], evidence_bounds[0]))


def row_identity(row: dict[str, Any]) -> dict[str, Any]:
    return {key: row.get(key) for key in (
        "session_id", "input_profile", "source", "source_audit_id", "interval", "text",
        "utterance_ids", "me_utterance_ids", "remote_utterance_ids", "allowed_decisions", "review_features",
    )}


def automatic_drop_supported(rows: list[dict[str, Any]], evidence: list[dict[str, Any]]) -> bool:
    """Require current, interval-bounded audio evidence for every whole-Me drop."""
    if not rows:
        return False
    for row in rows:
        if "drop_me" not in (row.get("allowed_decisions") or []) or not any(
            role(target) == "me" for target in review_targets(row)
        ):
            return False
        matches = [item for item in evidence if item.get("evidence_files") and evidence_matches_review_row(row, item)]

        def classified(item: dict[str, Any], labels: set[str], minimum: float) -> bool:
            classification = item.get("classification") or {}
            confidence = classification.get("confidence")
            if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
                return False
            return classification.get("label") in labels and math.isfinite(confidence) and minimum <= confidence <= 1

        if any(classified(item, {"confirm_me", "confirm_timing_or_doubletalk"}, 0.74)
               or classified(item, {"target_me_confirmed"}, 0.88) for item in matches):
            return False
        absent = any(classified(item, {"target_me_absent_remote_like"}, 0.88) for item in matches)
        supported = False
        for item in matches:
            if item.get("schema") != "murmurmark.faster_whisper_judge/v1" or not classified(
                item, {"confirm_remote_duplicate", "confirm_asr_noise"}, 0.74 if absent else 0.86
            ):
                continue
            required = {"mic_clean", "remote"}
            if item["classification"]["label"] == "confirm_asr_noise":
                required.update({"mic_raw", "mic_role_masked"})
            clips, scope = item.get("clips") or {}, item.get("classification_scope") or {}
            if required <= set(item.get("sources") or []) and all(
                scope.get(source) in {"word_bounded", "segment_bounded"}
                and clips.get(source)
                and file_identity(Path(clips[source])) in item["evidence_files"] for source in required
            ):
                supported = True
                break
        if not supported:
            return False
    return True


def decision_origin(row: dict[str, Any]) -> str:
    source = str(row.get("review_source") or "")
    if "suggested" in source or source.startswith("agent"):
        return "automatic"
    if source in {"workspace_answer_sheet", "lane_pack", "manual", "human"}:
        return "human"
    return "legacy_unknown"


def effective_decision(row: dict[str, Any]) -> str:
    decision = str(row.get("decision") or "todo").strip()
    if decision != "keep_me" or decision_origin(row) == "human":
        return decision
    receipt = (row.get("review_evidence") or {}).get("suggestion_receipt") or {}
    if (decision_origin(row) == "automatic" and receipt.get("resolved_scope") == "local_voice"
            and row.get("source") not in {"transcript_order", "local_recall", "transcript_text"}
            and row.get("review_lane") not in {"check_transcript_order", "check_transcript_text"}):
        return decision
    return "needs_review"


def automatic_keep_supported(rows: list[dict[str, Any]], evidence: list[dict[str, Any]]) -> bool:
    """Audio presence cannot close chronology, missing speech or lexical tasks."""
    if not rows:
        return False
    for row in rows:
        if (row.get("source") in {"transcript_order", "local_recall", "transcript_text"}
                or row.get("review_lane") in {"check_transcript_order", "check_transcript_text"}):
            return False
        matches = [item for item in evidence if item.get("evidence_files") and evidence_matches_review_row(row, item)]
        supported = False
        for item in matches:
            classification = item.get("classification") or {}
            confidence = classification.get("confidence")
            if (isinstance(confidence, bool) or not isinstance(confidence, (int, float))
                    or not math.isfinite(confidence) or not 0 <= confidence <= 1):
                continue
            if classification.get("label") in {"confirm_remote_duplicate", "confirm_asr_noise", "target_me_absent_remote_like"} and confidence >= 0.74:
                return False
            scope = item.get("classification_scope") or {}
            clips = item.get("clips") or {}
            if (item.get("schema") == "murmurmark.faster_whisper_judge/v1"
                    and classification.get("label") in {"confirm_me", "confirm_timing_or_doubletalk"}
                    and confidence >= 0.86
                    and {"mic_clean", "remote"} <= set(item.get("sources") or [])
                    and all(scope.get(source) in {"word_bounded", "segment_bounded"}
                            and clips.get(source)
                            and file_identity(Path(clips[source])) in item["evidence_files"]
                            for source in ("mic_clean", "remote"))):
                supported = True
        if not supported:
            return False
    return True


def neighboring_remote_text(row: dict[str, Any], padding_sec: float = 5.0) -> str:
    path = dialogue_path(row)
    interval = bounds(row.get("interval") or {})
    if path is None or interval is None:
        return ""
    identity = file_identity(path)
    if not identity.get("sha256"):
        return ""
    neighbors = [item for item in _dialogue(str(path), identity["sha256"])
                 if role(item) == "remote" and bounds(item)
                 and bounds(item)[0] < interval[1] + padding_sec
                 and bounds(item)[1] > interval[0] - padding_sec]
    return " ".join(str(item.get("text") or "") for item in sorted(neighbors, key=lambda item: bounds(item)[0]))


def suggestion_receipt(rows: list[dict[str, Any]], evidence: list[dict[str, Any]], decision: str) -> dict[str, Any]:
    paths = {Path(__file__), Path(__file__).with_name("build-review-lane-pack.py")}
    paths.update(path for row in rows if (path := dialogue_path(row)) is not None)
    paths.update(Path(item["evidence_artifact"]) for item in evidence if item.get("evidence_artifact"))
    files = [file_identity(path) for path in sorted(paths)]
    return {"schema": RECEIPT_SCHEMA, "decision": decision, "rows_sha256": digest([row_identity(row) for row in rows]),
            "evidence_sha256": digest(evidence), "files": files,
            "resolved_scope": "local_voice" if decision == "keep_me" and automatic_keep_supported(rows, evidence) else None}


def suggestion_receipt_current(receipt: Any, rows: list[dict[str, Any]], evidence: list[dict[str, Any]], decision: str) -> bool:
    return bool(isinstance(receipt, dict) and receipt.get("schema") == RECEIPT_SCHEMA
                and receipt.get("decision") == decision
                and receipt.get("rows_sha256") == digest([row_identity(row) for row in rows])
                and receipt.get("evidence_sha256") == digest(evidence)
                and (decision != "drop_me" or automatic_drop_supported(rows, evidence))
                and (decision != "keep_me" or (receipt.get("resolved_scope") == "local_voice"
                                               and automatic_keep_supported(rows, evidence)))
                and (decision == "skip" or all(review_targets(row) for row in rows))
                and files_current(receipt.get("files") or [])
                and all(evidence_files_current(item) for item in evidence))


def queue_snapshot(rows: list[dict[str, Any]], template: Path, decisions: Path) -> dict[str, Any]:
    """Unresolved tasks, distinct from completed answers and listening effort."""
    pending = [row for row in rows if effective_decision(row) not in {"keep_me", "drop_me", "drop_remote", "skip"}]
    intervals: dict[str, list[tuple[float, float]]] = {}
    known_sum = 0.0
    unknown = 0
    items = []
    for row in pending:
        interval = bounds(row.get("interval") or {})
        session = str(row.get("session_id") or row.get("session") or "")
        if interval is None:
            unknown += 1
        else:
            known_sum += interval[1] - interval[0]
            intervals.setdefault(session, []).append(interval)
        identity = row_identity(row)
        items.append({"id": digest(identity), "session_id": session, "profile": row.get("input_profile"),
                      "facet": row.get("source"), "interval": list(interval) if interval else None,
                      "decision": effective_decision(row), "origin": decision_origin(row)})
    union = 0.0
    for values in intervals.values():
        end = float("-inf")
        for start, stop in sorted(values):
            union += max(0.0, stop - max(start, end))
            end = max(end, stop)
    paths = {template.resolve(), decisions.resolve(), Path(__file__).resolve(),
             Path(__file__).with_name("report-review-decisions-progress.py").resolve()}
    paths.update(path.resolve() for row in rows if (path := dialogue_path(row)) is not None)
    files = [file_identity(path) for path in sorted(paths)]
    return {"schema": "murmurmark.review_queue_snapshot/v1", "fingerprint": digest({"files": files, "items": items}),
            "files": files, "scope": "session_timeline_any_track", "items": items, "unresolved_rows": len(pending),
            "known_interval_sum_seconds": round(known_sum, 3), "known_interval_union_seconds": round(union, 3),
            "interval_sum_seconds": round(known_sum, 3) if not unknown else None,
            "interval_union_seconds": round(union, 3) if not unknown else None,
            "unknown_duration_rows": unknown, "duration_complete": not unknown,
            "is_manual_work_estimate": False}


def read_queue_snapshot(session: Path) -> dict[str, Any] | None:
    try:
        progress = json.loads((session / "derived/readiness/review-plan/review_decisions_progress.json").read_text())
        snapshot = progress.get("queue_snapshot") or {}
        if snapshot.get("schema") != "murmurmark.review_queue_snapshot/v1" or not snapshot.get("files"):
            return None
        if any(file_identity(Path(item["path"])) != item for item in snapshot["files"]):
            return None
        if snapshot.get("fingerprint") != digest({"files": snapshot["files"], "items": snapshot["items"]}):
            return None
        return snapshot
    except (OSError, ValueError, TypeError, KeyError):
        return None
