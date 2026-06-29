#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_VERSION = "0.3.3"
SCHEMA = "murmurmark.operational_readiness_report/v1"
GROUPABLE_REVIEW_LANES = {"check_transcript_order", "check_unique_me_content", "classify_audio"}
MIN_OPERATIONAL_SESSION_DURATION_SEC = 60.0
DIAGNOSTIC_SESSION_EXACT_IDS = {
    "smoke",
    "test",
    "talk-solo",
    "voice-processing-smoke",
}
DIAGNOSTIC_SESSION_MARKERS = (
    "audio-input",
    "talk-audio-input",
    "talk-routed",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Report whether MurmurMark is ready for medium-risk working meetings.")
    parser.add_argument(
        "--session-quality",
        type=Path,
        default=Path("sessions/_reports/session-quality/session_quality_report.json"),
    )
    parser.add_argument(
        "--corpus-evaluation",
        type=Path,
        default=Path("sessions/_reports/regression-corpus/regression_corpus_evaluation.json"),
    )
    parser.add_argument(
        "--audio-judge",
        type=Path,
        default=Path("sessions/_reports/audio-judge-v0/audio_judge_v0_report.json"),
    )
    parser.add_argument(
        "--audio-judge-queue",
        type=Path,
        default=Path("sessions/_reports/audio-judge-v0/audio_judge_v0_queue_predictions.jsonl"),
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("sessions/_reports/operational-readiness"),
    )
    parser.add_argument("--max-review-items", type=int, default=40)
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
    return rows


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def is_diagnostic_session(row: dict[str, Any]) -> bool:
    session_id = str(row.get("session_id") or row.get("label") or "").lower()
    label = str(row.get("label") or "").lower()
    candidates = {session_id, label}
    if any(candidate in DIAGNOSTIC_SESSION_EXACT_IDS for candidate in candidates):
        return True
    if any(marker in session_id or marker in label for marker in DIAGNOSTIC_SESSION_MARKERS):
        return True
    if "meeting_duration_sec" in row:
        duration = safe_float(row.get("meeting_duration_sec"))
        return 0 < duration < MIN_OPERATIONAL_SESSION_DURATION_SEC
    return False


def aggregate_session_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    verdicts: dict[str, int] = {}
    profiles: dict[str, int] = {}
    total_duration = 0.0
    for row in rows:
        verdict = str(row.get("verdict") or "missing")
        profile = str(row.get("selected_profile") or "missing")
        verdicts[verdict] = verdicts.get(verdict, 0) + 1
        profiles[profile] = profiles.get(profile, 0) + 1
        total_duration += safe_float(row.get("meeting_duration_sec"))
    complete = sum(1 for row in rows if row.get("pipeline_status") == "complete")
    return {
        "session_count": len(rows),
        "complete_pipeline_count": complete,
        "partial_or_incomplete_count": len(rows) - complete,
        "total_duration_sec": round(total_duration, 3),
        "total_duration_min": round(total_duration / 60.0, 2),
        "by_verdict": dict(sorted(verdicts.items())),
        "by_selected_profile": dict(sorted(profiles.items())),
        "sessions_with_suggested_review_v1": sum(1 for row in rows if row.get("suggested_review_v1_available")),
    }


def operational_scope(session_quality: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    sessions = session_quality.get("sessions") if isinstance(session_quality.get("sessions"), list) else []
    included = [row for row in sessions if isinstance(row, dict) and not is_diagnostic_session(row)]
    excluded = [row for row in sessions if isinstance(row, dict) and is_diagnostic_session(row)]
    scoped = dict(session_quality)
    scoped["sessions"] = included
    scoped["summary"] = aggregate_session_rows(included)
    return scoped, excluded


def suffix(profile: str) -> str:
    return "" if profile == "current" else f".{profile}"


def format_time(seconds: float) -> str:
    total = max(0, int(seconds))
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def selected_me_ids(session_path: Path, profile: str) -> set[str]:
    path = session_path / "derived/transcript-simple/whisper-cpp/resolved" / f"clean_dialogue{suffix(profile)}.json"
    data = read_json(path)
    rows = data.get("utterances") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return set()
    ids: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        source = str(row.get("source_track") or "").lower()
        role = str(row.get("role") or row.get("speaker_label") or "").lower()
        if source == "mic" or role == "me":
            ids.add(str(row.get("id")))
    return ids


def audio_review_me_ids(row: dict[str, Any]) -> set[str]:
    rows = row.get("utterances")
    if not isinstance(rows, list):
        return set()
    ids: set[str] = set()
    for item in rows:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source_track") or "").lower()
        role = str(item.get("role") or item.get("speaker_label") or "").lower()
        if source == "mic" or role == "me":
            ids.add(str(item.get("id")))
    return ids


def audio_review_interval(row: dict[str, Any]) -> tuple[float, float]:
    interval = row.get("interval") if isinstance(row.get("interval"), dict) else {}
    start = safe_float(interval.get("start"))
    end = safe_float(interval.get("end"))
    seconds = safe_float(interval.get("duration_sec"))
    if end <= start:
        start = 0.0
        end = max(0.0, seconds)
    return start, end


def active_audio_review_row(row: dict[str, Any], selected_ids: set[str]) -> bool:
    classification = row.get("classification") if isinstance(row.get("classification"), dict) else {}
    label = str(classification.get("label") or "")
    me_ids = audio_review_me_ids(row)
    if label == "lost_me":
        return True
    if not me_ids:
        return True
    return bool(me_ids & selected_ids)


def reliable_audio_review_rows_by_me_id(rows: list[dict[str, Any]], selected_ids: set[str]) -> dict[str, list[dict[str, Any]]]:
    reliable: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if not active_audio_review_row(row, selected_ids):
            continue
        classification = row.get("classification") if isinstance(row.get("classification"), dict) else {}
        if str(classification.get("verdict") or "") != "likely_reliable":
            continue
        confidence = safe_float(classification.get("confidence"))
        scores = row.get("scores") if isinstance(row.get("scores"), dict) else {}
        likely_score = safe_float(scores.get("likely_reliable"))
        if confidence < 0.70 or likely_score < 70.0:
            continue
        for me_id in audio_review_me_ids(row) & selected_ids:
            reliable.setdefault(me_id, []).append(row)
    return reliable


def audio_review_row_explained_by_reliable(
    row: dict[str, Any], selected_ids: set[str], reliable_by_me_id: dict[str, list[dict[str, Any]]]
) -> bool:
    classification = row.get("classification") if isinstance(row.get("classification"), dict) else {}
    if str(classification.get("label") or "") != "uncertain":
        return False
    if str(classification.get("verdict") or "") != "needs_stronger_audio_judge":
        return False
    me_ids = audio_review_me_ids(row) & selected_ids
    if not me_ids:
        return False
    start, end = audio_review_interval(row)
    duration = end - start
    if duration <= 0.0:
        return False
    for me_id in me_ids:
        covered = False
        for reliable_row in reliable_by_me_id.get(me_id, []):
            reliable_start, reliable_end = audio_review_interval(reliable_row)
            overlap = max(0.0, min(end, reliable_end) - max(start, reliable_start))
            if overlap / duration >= 0.80:
                covered = True
                break
        if not covered:
            return False
    return True


def review_resolved_audio_ids(session_path: Path, profile: str) -> set[str]:
    if profile not in {"reviewed_v1", "agent_reviewed_v1"}:
        return set()
    path = (
        session_path
        / "derived/transcript-simple/whisper-cpp/review-decisions"
        / f"review_decisions_applied{suffix(profile)}.jsonl"
    )
    resolved: set[str] = set()
    for row in read_jsonl(path):
        if str(row.get("source") or "") != "audio_review":
            continue
        if str(row.get("decision") or "") not in {"drop_me", "drop_remote", "keep_me", "skip"}:
            continue
        source_id = str(row.get("source_audit_id") or "")
        if source_id:
            resolved.add(source_id)
    return resolved


def session_review_burden(session: dict[str, Any]) -> dict[str, Any]:
    duration = safe_float(session.get("meeting_duration_sec"))
    probable_error = safe_float(session.get("audio_review_notes_probable_error_seconds"))
    stronger_judge = safe_float(session.get("audio_review_notes_stronger_judge_seconds"))
    transcript_probable_error = safe_float(session.get("audio_review_probable_error_seconds"))
    transcript_stronger_judge = safe_float(session.get("audio_review_stronger_judge_seconds"))
    local_recall = safe_float(session.get("local_recall_meaningful_review_seconds"))
    transcript_order = safe_float(session.get("transcript_order_review_seconds"))
    harmful = safe_float(session.get("audit_harmful_seconds_after"))
    burden = probable_error + stronger_judge + local_recall + transcript_order
    transcript_burden = transcript_probable_error + transcript_stronger_judge + local_recall + transcript_order
    ratio = burden / duration if duration > 0 else 0.0
    transcript_ratio = transcript_burden / duration if duration > 0 else 0.0
    row = {
        "session_id": session.get("session_id"),
        "label": session.get("label"),
        "session": session.get("session"),
        "duration_sec": round(duration, 3),
        "selected_profile": session.get("selected_profile"),
        "verdict": session.get("verdict"),
        "review_burden_sec": round(burden, 3),
        "review_burden_ratio": round(ratio, 6),
        "notes_review_burden_sec": round(burden, 3),
        "notes_review_burden_ratio": round(ratio, 6),
        "transcript_review_burden_sec": round(transcript_burden, 3),
        "transcript_review_burden_ratio": round(transcript_ratio, 6),
        "audio_review_probable_error_seconds": round(probable_error, 3),
        "audio_review_stronger_judge_seconds": round(stronger_judge, 3),
        "transcript_audio_review_probable_error_seconds": round(transcript_probable_error, 3),
        "transcript_audio_review_stronger_judge_seconds": round(transcript_stronger_judge, 3),
        "transcript_audio_review_explained_by_reliable_seconds": round(
            safe_float(session.get("audio_review_explained_by_reliable_seconds")) or 0.0, 3
        ),
        "notes_audio_review_explained_by_reliable_seconds": round(
            safe_float(session.get("audio_review_notes_explained_by_reliable_seconds")) or 0.0, 3
        ),
        "local_recall_meaningful_review_seconds": round(local_recall, 3),
        "local_recall_possible_lost_me_seconds": round(safe_float(session.get("local_recall_possible_lost_me_seconds")), 3),
        "local_recall_needs_review_seconds": round(safe_float(session.get("local_recall_needs_review_seconds")), 3),
        "transcript_order_review_seconds": round(transcript_order, 3),
        "transcript_order_probable_order_risk_seconds": round(safe_float(session.get("transcript_order_probable_order_risk_seconds")), 3),
        "transcript_order_needs_review_seconds": round(safe_float(session.get("transcript_order_needs_review_seconds")), 3),
        "audit_harmful_seconds_after": round(harmful, 3),
        "risk_flags": session.get("risk_flags") or [],
        "review_blockers": session.get("review_blockers") or [],
        "export_blockers": session.get("export_blockers") or [],
    }
    source_gate = session.get("use_gate")
    row["use_gate"] = source_gate if isinstance(source_gate, str) and source_gate else session_use_gate(row)
    return row


def session_use_gate(row: dict[str, Any]) -> str:
    ratio = safe_float(row.get("review_burden_ratio"))
    flags = set(row.get("risk_flags") or [])
    profile = str(row.get("selected_profile") or "")
    verdict = str(row.get("verdict") or "")
    if verdict in {"failed", "risky"}:
        return "do_not_use_without_manual_review"
    if profile not in {"audit_cleanup_v1", "audit_cleanup_v2", "audit_cleanup_v3", "audit_cleanup_v4", "audit_cleanup_v5", "audit_cleanup_v6", "reviewed_v1", "agent_reviewed_v1"}:
        return "pipeline_incomplete_review_first"
    if ratio <= 0.025 and not flags:
        return "ready_for_notes"
    if ratio <= 0.08:
        return "review_first"
    return "do_not_use_without_manual_review"


def review_priority(row: dict[str, Any]) -> float:
    classification = row.get("classification") if isinstance(row.get("classification"), dict) else {}
    interval = row.get("interval") if isinstance(row.get("interval"), dict) else {}
    verdict = str(classification.get("verdict") or "")
    label = str(classification.get("label") or "")
    confidence = safe_float(classification.get("confidence"))
    duration = safe_float(interval.get("duration_sec"))
    score = duration + confidence * 10.0
    if verdict == "probable_transcript_error":
        score += 100.0
    elif verdict == "needs_stronger_audio_judge":
        score += 70.0
    if label in {"remote_duplicate", "asr_noise"}:
        score += 12.0
    elif label in {"remote_leak", "lost_me", "uncertain"}:
        score += 8.0
    return round(score, 3)


def compact_review_item(session: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    classification = row.get("classification") if isinstance(row.get("classification"), dict) else {}
    interval = row.get("interval") if isinstance(row.get("interval"), dict) else {}
    utterances = row.get("utterances") if isinstance(row.get("utterances"), list) else []
    commands = row.get("commands") if isinstance(row.get("commands"), dict) else {}
    features = row.get("features") if isinstance(row.get("features"), dict) else {}
    text_features = features.get("text") if isinstance(features.get("text"), dict) else {}
    me_rows = [
        item
        for item in utterances
        if isinstance(item, dict)
        and (str(item.get("source_track") or "").lower() == "mic" or str(item.get("role") or "").lower() == "me")
    ]
    remote_rows = [
        item
        for item in utterances
        if isinstance(item, dict)
        and (str(item.get("source_track") or "").lower() == "remote" or str(item.get("role") or "").lower() in {"remote", "colleagues"})
    ]

    def utterance_duration(item: dict[str, Any] | None) -> float:
        if not isinstance(item, dict):
            return 0.0
        return max(0.0, safe_float(item.get("end")) - safe_float(item.get("start")))

    def interval_coverage(item: dict[str, Any] | None) -> float:
        duration = utterance_duration(item)
        if duration <= 0.0 or not isinstance(item, dict):
            return 0.0
        start = max(safe_float(interval.get("start")), safe_float(item.get("start")))
        end = min(safe_float(interval.get("end")), safe_float(item.get("end")))
        return max(0.0, end - start) / duration

    me_row = me_rows[0] if me_rows else None
    remote_row = remote_rows[0] if remote_rows else None
    me_coverage = interval_coverage(me_row)
    return {
        "session_id": session.get("session_id"),
        "session": session.get("session"),
        "source_audit_id": row.get("id"),
        "label": classification.get("label"),
        "verdict": classification.get("verdict"),
        "confidence": classification.get("confidence"),
        "priority_score": review_priority(row),
        "interval": interval,
        "utterance_ids": row.get("utterance_ids", []),
        "review_features": {
            "me_overlap_coverage": round(me_coverage, 6),
            "remote_overlap_coverage": round(interval_coverage(remote_row), 6),
            "me_utterance_duration_sec": round(utterance_duration(me_row), 3),
            "remote_utterance_duration_sec": round(utterance_duration(remote_row), 3),
            "text_similarity": round(safe_float(text_features.get("similarity")), 6),
            "token_containment": round(safe_float(text_features.get("containment")), 6),
            "sequence_ratio": round(safe_float(text_features.get("sequence_ratio")), 6),
            "likely_partial_me_utterance": bool(0.0 < me_coverage < 0.55),
        },
        "text": [
            {
                "id": item.get("id"),
                "role": item.get("role"),
                "source_track": item.get("source_track"),
                "text": item.get("text"),
            }
            for item in utterances[:3]
            if isinstance(item, dict)
        ],
        "commands": {
            key: commands[key]
            for key in ("stereo_clean_left_remote_right", "stereo_mic_left_remote_right", "mic_raw", "remote")
            if commands.get(key)
        },
    }


def duplicate_drop_hint_allowed(item: dict[str, Any]) -> bool:
    features = item.get("review_features") if isinstance(item.get("review_features"), dict) else {}
    coverage = safe_float(features.get("me_overlap_coverage"))
    similarity = safe_float(features.get("text_similarity"))
    containment = safe_float(features.get("token_containment"))
    return coverage >= 0.80 and (similarity >= 0.92 or containment >= 0.75)


def review_lane(item: dict[str, Any]) -> str:
    source = str(item.get("source") or "")
    label = str(item.get("label") or "")
    verdict = str(item.get("verdict") or "")
    if source in {"local_recall", "local_recall_repair"} or label in {
        "lost_me",
        "local_recall_needs_review",
        "local_recall_repair_inserted",
    }:
        return "check_local_recall"
    if source == "transcript_order" or label in {"probable_order_risk"}:
        return "check_transcript_order"
    if label == "remote_duplicate" and verdict == "probable_transcript_error":
        return "fast_confirm_drop" if duplicate_drop_hint_allowed(item) else "check_unique_me_content"
    if label == "asr_noise" and verdict == "probable_transcript_error":
        return "fast_confirm_drop"
    if label == "remote_leak":
        return "check_unique_me_content"
    if label in {"double_talk", "timing_overlap"}:
        return "confirm_benign"
    return "classify_audio"


def review_action(item: dict[str, Any]) -> str:
    label = str(item.get("label") or "")
    verdict = str(item.get("verdict") or "")
    if label == "remote_duplicate" and verdict == "probable_transcript_error":
        return "confirm_drop_or_keep_me" if duplicate_drop_hint_allowed(item) else "check_unique_me_content"
    if label == "asr_noise" and verdict == "probable_transcript_error":
        return "confirm_drop_or_keep_me"
    if label == "remote_leak":
        return "check_unique_me_content"
    if label == "lost_me":
        return "check_lost_local_speech"
    if label == "local_recall_needs_review":
        return "check_local_recall_island"
    if label == "local_recall_repair_inserted":
        return "check_inserted_local_recall_repair"
    if label in {"probable_order_risk", "needs_review", "transcript_order_needs_review"}:
        if str(item.get("source") or "") == "transcript_order":
            return "check_transcript_order"
    if label in {"double_talk", "timing_overlap"}:
        return "confirm_benign_overlap"
    return "classify_audio"


def item_list_values(item: dict[str, Any], key: str) -> list[str]:
    values = item.get(key)
    if isinstance(values, list):
        return [str(value) for value in values if value is not None and str(value)]
    return []


def item_text_utterance_ids(item: dict[str, Any], *, role: str) -> list[str]:
    rows = item.get("text")
    if not isinstance(rows, list):
        return []
    ids: list[str] = []
    role = role.lower()
    for row in rows:
        if not isinstance(row, dict):
            continue
        source_track = str(row.get("source_track") or "").lower()
        row_role = str(row.get("role") or "").lower()
        if role == "me":
            matches = source_track == "mic" or row_role == "me"
        else:
            matches = source_track == "remote" or row_role in {"remote", "colleagues"}
        value = row.get("id")
        if matches and value is not None and str(value):
            ids.append(str(value))
    return ids


def review_item_allowed_decisions(item: dict[str, Any]) -> list[str]:
    values = item.get("allowed_decisions")
    if isinstance(values, list) and values:
        decisions = [str(value) for value in values if value is not None and str(value)]
        source = str(item.get("source") or "")
        if (
            source not in {"local_recall", "transcript_order"}
            and (item_list_values(item, "remote_utterance_ids") or item_text_utterance_ids(item, role="remote"))
            and "drop_remote" not in decisions
        ):
            insert_at = decisions.index("drop_me") + 1 if "drop_me" in decisions else 0
            decisions.insert(insert_at, "drop_remote")
        return decisions
    source = str(item.get("source") or "")
    if source in {"local_recall", "transcript_order"}:
        return ["keep_me", "needs_review", "skip"]
    decisions = ["drop_me", "keep_me", "needs_review", "skip"]
    if item_list_values(item, "remote_utterance_ids") or item_text_utterance_ids(item, role="remote"):
        decisions.insert(1, "drop_remote")
    return decisions


def first_me_utterance_id(item: dict[str, Any]) -> str:
    me_ids = item_list_values(item, "me_utterance_ids") or item_text_utterance_ids(item, role="me")
    if me_ids:
        return me_ids[0]
    utterance_ids = item_list_values(item, "utterance_ids")
    return utterance_ids[0] if utterance_ids else ""


def me_utterance_group_key(item: dict[str, Any]) -> str:
    me_ids = item_list_values(item, "me_utterance_ids") or item_text_utterance_ids(item, role="me")
    if me_ids:
        return ",".join(me_ids)
    return first_me_utterance_id(item)


def enrich_review_item(item: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(item)
    enriched["review_action"] = str(enriched.get("review_action") or review_action(enriched))
    enriched["review_lane"] = str(enriched.get("review_lane") or review_lane(enriched))
    if not isinstance(enriched.get("allowed_decisions"), list) or not enriched.get("allowed_decisions"):
        enriched["allowed_decisions"] = review_item_allowed_decisions(enriched)
    if not isinstance(enriched.get("me_utterance_ids"), list):
        enriched["me_utterance_ids"] = item_text_utterance_ids(enriched, role="me")
    if not isinstance(enriched.get("remote_utterance_ids"), list):
        enriched["remote_utterance_ids"] = item_text_utterance_ids(enriched, role="remote")
    return enriched


def review_group_key(item: dict[str, Any]) -> str:
    lane = str(item.get("review_lane") or review_lane(item))
    if lane not in GROUPABLE_REVIEW_LANES:
        return ""
    me_key = me_utterance_group_key(item)
    if not me_key:
        return ""
    session_id = str(item.get("session_id") or item.get("session") or "")
    action = str(item.get("review_action") or review_action(item))
    if lane == "check_unique_me_content":
        return f"{lane}:{session_id}:{action}:{me_key}"
    label = str(item.get("label") or "")
    allowed = ",".join(sorted(review_item_allowed_decisions(item)))
    return f"{lane}:{session_id}:{label}:{action}:{allowed}:{me_key}"


def review_action_groups(items: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    by_key: dict[str, list[dict[str, Any]]] = {}
    for raw_item in items:
        item = enrich_review_item(raw_item)
        key = review_group_key(item)
        if not key:
            groups.append([item])
            continue
        group = by_key.get(key)
        if group is None:
            group = []
            by_key[key] = group
            groups.append(group)
        group.append(item)
    return groups


def review_action_summary(review_queue: list[dict[str, Any]]) -> dict[str, Any]:
    groups = review_action_groups(review_queue)
    by_lane_actions: dict[str, int] = {}
    by_lane_grouped_rows: dict[str, int] = {}
    by_action: dict[str, int] = {}
    for item in review_queue:
        enriched = enrich_review_item(item)
        action = str(enriched.get("review_action") or "classify_audio")
        by_action[action] = by_action.get(action, 0) + 1
    for group in groups:
        if not group:
            continue
        lane = str(group[0].get("review_lane") or review_lane(group[0]))
        by_lane_actions[lane] = by_lane_actions.get(lane, 0) + 1
        by_lane_grouped_rows[lane] = by_lane_grouped_rows.get(lane, 0) + max(0, len(group) - 1)
    return {
        "review_action_count": len(groups),
        "grouped_review_row_count": sum(max(0, len(group) - 1) for group in groups),
        "by_review_action": dict(sorted(by_action.items())),
        "by_review_lane_actions": dict(sorted(by_lane_actions.items())),
        "by_review_lane_grouped_rows": dict(sorted(by_lane_grouped_rows.items())),
    }


def review_queue_lane_summary(review_queue: list[dict[str, Any]]) -> dict[str, Any]:
    lane_order = [
        "fast_confirm_drop",
        "check_unique_me_content",
        "check_local_recall",
        "check_transcript_order",
        "confirm_benign",
        "classify_audio",
    ]
    rows: dict[str, dict[str, Any]] = {
        lane: {
            "lane": lane,
            "items": 0,
            "seconds": 0.0,
            "labels": {},
        }
        for lane in lane_order
    }
    action_summary = review_action_summary(review_queue)
    actions_by_lane = action_summary.get("by_review_lane_actions") or {}
    grouped_by_lane = action_summary.get("by_review_lane_grouped_rows") or {}
    for raw_item in review_queue:
        item = enrich_review_item(raw_item)
        lane = str(item.get("review_lane") or review_lane(item))
        row = rows.setdefault(lane, {"lane": lane, "items": 0, "seconds": 0.0, "labels": {}})
        interval = item.get("interval") if isinstance(item.get("interval"), dict) else {}
        label = str(item.get("label") or "unknown")
        row["items"] += 1
        row["seconds"] += safe_float(interval.get("duration_sec"))
        row["labels"][label] = row["labels"].get(label, 0) + 1
    by_lane = []
    for lane in lane_order:
        row = rows.get(lane)
        if not row or not row.get("items"):
            continue
        by_lane.append(
            {
                "lane": lane,
                "items": row["items"],
                "actions": safe_int(actions_by_lane.get(lane)) or row["items"],
                "grouped_rows": safe_int(grouped_by_lane.get(lane)),
                "seconds": round(safe_float(row["seconds"]), 3),
                "minutes": round(safe_float(row["seconds"]) / 60.0, 2),
                "labels": dict(sorted((row.get("labels") or {}).items())),
            }
        )
    total_items = len(review_queue)
    total_seconds = sum(
        safe_float((item.get("interval") if isinstance(item.get("interval"), dict) else {}).get("duration_sec"))
        for item in review_queue
    )
    blocker_lane_order = ("check_transcript_order", "check_local_recall", "check_unique_me_content")
    first_lane = next((lane for lane in blocker_lane_order if safe_int(rows.get(lane, {}).get("items"))), None)
    fast = rows.get("fast_confirm_drop") or {}
    fast_items = safe_int(fast.get("items"))
    quick_lane = "fast_confirm_drop" if fast_items else None
    first_lane = first_lane or quick_lane or (by_lane[0]["lane"] if by_lane else None)
    first_row = rows.get(first_lane or "") or {}
    first_items = safe_int(first_row.get("items"))
    first_actions = safe_int(actions_by_lane.get(first_lane or "")) or first_items
    first_seconds = safe_float(first_row.get("seconds"))
    return {
        "by_lane": by_lane,
        "review_action_count": action_summary["review_action_count"],
        "grouped_review_row_count": action_summary["grouped_review_row_count"],
        "by_review_action": action_summary["by_review_action"],
        "by_review_lane_actions": action_summary["by_review_lane_actions"],
        "by_review_lane_grouped_rows": action_summary["by_review_lane_grouped_rows"],
        "first_recommended_lane": first_lane,
        "quick_recommended_lane": quick_lane,
        "first_recommended_reason": (
            "close_blocking_review_lane"
            if first_lane and first_lane != quick_lane
            else ("close_fast_confirm_drop" if first_lane else None)
        ),
        "after_first_lane_estimate": {
            "remaining_items": max(0, total_items - first_items),
            "remaining_actions": max(0, action_summary["review_action_count"] - first_actions),
            "remaining_seconds": round(max(0.0, total_seconds - first_seconds), 3),
            "remaining_minutes": round(max(0.0, total_seconds - first_seconds) / 60.0, 2),
        },
        "commands": {
            "build_review_workspace": (
                ".venv/bin/python scripts/build-review-workspace.py"
                if review_queue
                else None
            ),
            "apply_review_workspace": (
                ".venv/bin/python scripts/apply-review-workspace-decisions.py "
                "--workspace sessions/_reports/review-plan/review_workspace.json "
                "--out sessions/_reports/review-plan/review_decisions.jsonl"
                if review_queue
                else None
            ),
            "dry_run_suggested_workspace": (
                ".venv/bin/python scripts/apply-review-workspace-decisions.py "
                "--workspace sessions/_reports/review-plan/review_workspace.json "
                "--answers-source suggested --dry-run"
                if review_queue
                else None
            ),
            "apply_suggested_workspace": (
                ".venv/bin/python scripts/apply-review-workspace-decisions.py "
                "--workspace sessions/_reports/review-plan/review_workspace.json "
                "--answers-source suggested "
                "--out sessions/_reports/review-plan/review_decisions.suggested.jsonl"
                if review_queue
                else None
            ),
            "build_suggested_review_profile": (
                ".venv/bin/python scripts/apply-review-decisions-batch.py "
                "--decisions sessions/_reports/review-plan/review_decisions.suggested.jsonl "
                "--review-template sessions/_reports/review-plan/review_decisions.template.jsonl "
                "--output-profile suggested_review_v1 "
                "--synthesize "
                "--out sessions/_reports/review-plan/review_decisions_apply.suggested_review_v1.json"
                if review_queue
                else None
            ),
            "report_suggested_review_shadow": (
                ".venv/bin/python scripts/report-suggested-review-shadow.py"
                if review_queue
                else None
            ),
            "apply_suggested_cleanup": (
                ".venv/bin/python scripts/apply-suggested-cleanup.py"
                if review_queue
                else None
            ),
            "apply_audio_review_cleanup_v6": (
                ".venv/bin/python scripts/apply-audit-cleanup.py <session> "
                "--input-profile audit_cleanup_v5 --output-profile audit_cleanup_v6"
                if review_queue
                else None
            ),
            "build_first_lane_pack": (
                "murmurmark review first-lane"
                if first_lane
                else None
            ),
            "review_first_lane": (
                ".venv/bin/python scripts/apply-review-lane-pack-decisions.py "
                f"sessions/_reports/review-plan/lane-packs/review_lane_pack.{first_lane}.json "
                f"--answers-file sessions/_reports/review-plan/lane-packs/review_lane_answers.{first_lane}.txt "
                "--out sessions/_reports/review-plan/review_decisions.jsonl"
                if first_lane
                else None
            ),
        },
    }


def local_recall_review_priority(row: dict[str, Any]) -> float:
    label = str(row.get("label") or "")
    confidence = safe_float(row.get("confidence"))
    duration = safe_float(row.get("duration_sec"))
    score = duration + confidence * 10.0 + 90.0
    if label == "possible_lost_me":
        score += 45.0
    elif label == "needs_review":
        score += 25.0
    return round(score, 3)


def transcript_order_review_priority(row: dict[str, Any]) -> float:
    interval = row.get("interval") if isinstance(row.get("interval"), dict) else {}
    features = row.get("features") if isinstance(row.get("features"), dict) else {}
    label = str(row.get("label") or "")
    duration = safe_float(interval.get("duration_sec"))
    confidence = safe_float(row.get("confidence"))
    post_tail = safe_float(features.get("post_remote_tail_sec"))
    score = duration + confidence * 10.0 + post_tail
    if label == "probable_order_risk":
        score += 220.0
    elif label == "needs_review":
        score += 70.0
    return round(score, 3)


def compact_local_recall_item(session: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    start = safe_float(row.get("start_sec"))
    end = safe_float(row.get("end_sec"))
    if end < start:
        end = start
    duration = max(0.0, end - start)
    label = str(row.get("label") or "needs_review")
    if label == "possible_lost_me":
        review_label = "lost_me"
        verdict = "needs_stronger_audio_judge"
    else:
        review_label = "local_recall_needs_review"
        verdict = "needs_stronger_audio_judge"
    session_path = str(session.get("session") or "")
    listen_start = max(0.0, start - 1.0)
    listen_duration = duration + 2.0
    return {
        "session_id": session.get("session_id"),
        "session": session.get("session"),
        "source_audit_id": row.get("item_id"),
        "source": "local_recall",
        "label": review_label,
        "verdict": verdict,
        "confidence": row.get("confidence"),
        "priority_score": local_recall_review_priority(row),
        "interval": {
            "start": round(start, 3),
            "end": round(end, 3),
            "duration_sec": round(duration, 3),
            "start_time": format_time(start),
            "end_time": format_time(end),
        },
        "utterance_ids": [],
        "text": [
            {
                "id": row.get("parent_candidate_id"),
                "role": "Me",
                "source_track": "mic",
                "text": row.get("parent_text"),
            }
        ],
        "commands": {
            "mic_raw": (
                f"ffplay -hide_banner -loglevel error -ss {listen_start:.3f} "
                f"-t {listen_duration:.3f} \"{session_path}/audio/mic/000001.caf\""
            )
        },
        "reason": row.get("reason"),
    }


def compact_local_recall_repair_patch(session: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any] | None:
    utterance = patch.get("utterance") if isinstance(patch.get("utterance"), dict) else {}
    if not utterance:
        return None
    start = safe_float(utterance.get("start"))
    end = safe_float(utterance.get("end"))
    if end < start:
        end = start
    duration = max(0.0, end - start)
    session_path = str(session.get("session") or "")
    listen_start = max(0.0, start - 1.0)
    listen_duration = duration + 2.0
    micro = patch.get("micro_asr") if isinstance(patch.get("micro_asr"), dict) else {}
    confidence = safe_float(micro.get("score"))
    return {
        "session_id": session.get("session_id"),
        "session": session.get("session"),
        "source_audit_id": patch.get("source_item_id"),
        "source": "local_recall_repair",
        "label": "local_recall_repair_inserted",
        "verdict": "needs_human_review",
        "confidence": confidence,
        "priority_score": round(duration + confidence * 10.0 + 150.0, 3),
        "input_profile": "local_recall_repair_v1",
        "allowed_decisions": ["drop_me", "keep_me", "needs_review", "skip"],
        "interval": {
            "start": round(start, 3),
            "end": round(end, 3),
            "duration_sec": round(duration, 3),
            "start_time": format_time(start),
            "end_time": format_time(end),
        },
        "utterance_ids": [utterance.get("id")] if utterance.get("id") else [],
        "text": [
            {
                "id": utterance.get("id"),
                "role": "Me",
                "source_track": "mic",
                "text": utterance.get("text"),
            }
        ],
        "review_features": {
            "micro_asr_source_label": micro.get("source_label"),
            "micro_asr_window_label": micro.get("window_label"),
            "micro_asr_score": micro.get("score"),
            "selection_policy": micro.get("selection_policy"),
            "raw_transcription_text": micro.get("raw_transcription_text"),
        },
        "commands": {
            "mic_raw": (
                f"ffplay -hide_banner -loglevel error -ss {listen_start:.3f} "
                f"-t {listen_duration:.3f} \"{session_path}/audio/mic/000001.caf\""
            )
        },
        "reason": "inserted local-recall repair requires explicit review",
    }


def compact_transcript_order_item(session: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    interval = row.get("interval") if isinstance(row.get("interval"), dict) else {}
    utterances = row.get("utterances") if isinstance(row.get("utterances"), dict) else {}
    me = utterances.get("me") if isinstance(utterances.get("me"), dict) else {}
    remote = utterances.get("remote") if isinstance(utterances.get("remote"), dict) else {}
    session_path = str(session.get("session") or "")
    starts = [
        safe_float(value)
        for value in (me.get("start"), remote.get("start"), interval.get("start"))
        if value is not None
    ]
    ends = [
        safe_float(value)
        for value in (me.get("end"), remote.get("end"), interval.get("end"))
        if value is not None
    ]
    if not starts:
        starts = [0.0]
    if not ends:
        ends = [max(starts)]
    overlap_start = safe_float(interval.get("start")) if interval.get("start") is not None else min(starts)
    overlap_end = safe_float(interval.get("end")) if interval.get("end") is not None else max(ends)
    if overlap_end < overlap_start:
        overlap_end = overlap_start
    listen_start = max(0.0, overlap_start - 2.0)
    listen_end = min(max(ends) + 1.0, overlap_end + 6.0)
    listen_duration = max(0.25, listen_end - listen_start)
    return {
        "session_id": session.get("session_id"),
        "session": session.get("session"),
        "source_audit_id": row.get("item_id"),
        "source": "transcript_order",
        "label": row.get("label"),
        "verdict": "needs_transcript_order_review",
        "confidence": row.get("confidence"),
        "priority_score": transcript_order_review_priority(row),
        "interval": interval,
        "utterance_ids": [item for item in [me.get("id"), remote.get("id")] if item],
        "review_features": row.get("features") if isinstance(row.get("features"), dict) else {},
        "text": [
            {
                "id": me.get("id"),
                "role": "Me",
                "source_track": "mic",
                "text": me.get("text"),
            },
            {
                "id": remote.get("id"),
                "role": "Colleagues",
                "source_track": "remote",
                "text": remote.get("text"),
            },
        ],
        "commands": {
            "mic_raw": (
                f"ffplay -hide_banner -loglevel error -ss {listen_start:.3f} "
                f"-t {listen_duration:.3f} \"{session_path}/audio/mic/000001.caf\""
            ),
            "remote": (
                f"ffplay -hide_banner -loglevel error -ss {listen_start:.3f} "
                f"-t {listen_duration:.3f} \"{session_path}/audio/remote/000001.caf\""
            ),
            "review": f"less \"{session_path}/derived/audit/order/transcript_order_review.md\"",
        },
        "reason": row.get("reason"),
    }


def review_queue_sort_key(item: dict[str, Any]) -> tuple[float, str, str]:
    return (
        -safe_float(item.get("priority_score")),
        str(item.get("session_id") or ""),
        str(item.get("source_audit_id") or ""),
    )


def review_item_key(item: dict[str, Any]) -> str:
    interval = item.get("interval") if isinstance(item.get("interval"), dict) else {}
    return "|".join(
        [
            str(item.get("session_id") or ""),
            str(item.get("source") or ""),
            str(item.get("source_audit_id") or ""),
            str(item.get("label") or ""),
            str(interval.get("start") or ""),
            str(interval.get("end") or ""),
        ]
    )


def select_review_queue(rows: list[dict[str, Any]], max_items: int) -> list[dict[str, Any]]:
    limit = max(0, max_items)
    if limit == 0:
        return []

    # Keep important lanes visible without letting one audit class consume the whole queue.
    lane_reserve = {
        "fast_confirm_drop": 2,
        "check_unique_me_content": 2,
        "check_local_recall": 2,
        "check_transcript_order": 2,
    }
    selected: list[dict[str, Any]] = []
    selected_keys: set[str] = set()

    def add(item: dict[str, Any]) -> None:
        if len(selected) >= limit:
            return
        key = review_item_key(item)
        if key in selected_keys:
            return
        selected.append(item)
        selected_keys.add(key)

    for lane, lane_limit in lane_reserve.items():
        before = len(selected)
        for item in rows:
            if review_lane(item) == lane:
                add(item)
            if len(selected) - before >= lane_limit:
                break

    for item in rows:
        add(item)

    selected.sort(key=review_queue_sort_key)
    return [enrich_review_item(item) for item in selected[:limit]]


def build_review_queue(sessions: list[dict[str, Any]], max_items: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    by_session = {str(session.get("session_id")): session for session in sessions}
    me_ids_cache: dict[tuple[str, str], set[str]] = {}
    review_resolved_cache: dict[tuple[str, str], set[str]] = {}
    for session in sessions:
        use_gate = str(session.get("use_gate") or "")
        export_blockers = session.get("export_blockers") if isinstance(session.get("export_blockers"), list) else []
        transcript_review_burden = safe_float(session.get("transcript_review_burden_sec"))
        if use_gate == "ready_for_notes" and (not export_blockers or transcript_review_burden <= 0.0):
            continue
        session_path = Path(str(session.get("session") or ""))
        profile = str(session.get("selected_profile") or "")
        cache_key = (str(session_path), profile)
        if cache_key not in me_ids_cache:
            me_ids_cache[cache_key] = selected_me_ids(session_path, profile)
        if cache_key not in review_resolved_cache:
            review_resolved_cache[cache_key] = review_resolved_audio_ids(session_path, profile)
        selected_ids = me_ids_cache[cache_key]
        review_resolved_ids = review_resolved_cache[cache_key]
        audit_path = session_path / "derived/audit/audio-review-pack/audio_review_audit.jsonl"
        audit_rows = read_jsonl(audit_path)
        reliable_by_me_id = reliable_audio_review_rows_by_me_id(audit_rows, selected_ids)
        for row in audit_rows:
            classification = row.get("classification") if isinstance(row.get("classification"), dict) else {}
            verdict = str(classification.get("verdict") or "")
            if verdict not in {"probable_transcript_error", "needs_stronger_audio_judge"}:
                continue
            source_id = str(row.get("id") or "")
            if source_id in review_resolved_ids:
                continue
            if selected_ids and not active_audio_review_row(row, selected_ids):
                continue
            if audio_review_row_explained_by_reliable(row, selected_ids, reliable_by_me_id):
                continue
            session_id = str(row.get("session_id") or session.get("session_id"))
            rows.append(compact_review_item(by_session.get(session_id, session), row))
        repair_patches_path = (
            session_path
            / "derived/transcript-simple/whisper-cpp/local-recall-repair/local_recall_repair_patches.local_recall_repair_v1.jsonl"
        )
        repaired_local_recall_ids: set[str] = set()
        for patch in read_jsonl(repair_patches_path):
            if str(patch.get("status") or "") != "applied":
                continue
            source_item_id = str(patch.get("source_item_id") or "")
            if source_item_id:
                repaired_local_recall_ids.add(source_item_id)
            compacted = compact_local_recall_repair_patch(session, patch)
            if compacted:
                rows.append(compacted)
        local_recall_path = session_path / "derived/audit/local-recall/local_recall_items.jsonl"
        for row in read_jsonl(local_recall_path):
            label = str(row.get("label") or "")
            if label not in {"possible_lost_me", "needs_review"}:
                continue
            if str(row.get("item_id") or "") in repaired_local_recall_ids:
                continue
            rows.append(compact_local_recall_item(session, row))
        order_path = session_path / "derived/audit/order/transcript_order_items.jsonl"
        for row in read_jsonl(order_path):
            label = str(row.get("label") or "")
            if label not in {"probable_order_risk", "needs_review"}:
                continue
            rows.append(compact_transcript_order_item(session, row))
    rows.sort(key=review_queue_sort_key)
    return select_review_queue(rows, max_items)


def review_queue_minutes(items: list[dict[str, Any]]) -> float:
    seconds = 0.0
    for item in items:
        interval = item.get("interval") if isinstance(item.get("interval"), dict) else {}
        seconds += safe_float(interval.get("duration_sec"))
    return round(seconds / 60.0, 2)


def promotion_plan(
    verdict: str,
    blockers: list[str],
    warnings: list[str],
    burdens: list[dict[str, Any]],
    review_queue: list[dict[str, Any]],
    audio_judge_review_queue: dict[str, Any] | None,
) -> dict[str, Any]:
    not_ready = [row for row in burdens if row.get("use_gate") != "ready_for_notes"]
    high_burden = sorted(not_ready, key=lambda row: safe_float(row.get("review_burden_ratio")), reverse=True)
    by_session: dict[str, dict[str, Any]] = {}
    by_session_items: dict[str, list[dict[str, Any]]] = {}
    for item in review_queue:
        session_id = str(item.get("session_id") or "unknown")
        row = by_session.setdefault(
            session_id,
            {
                "session_id": session_id,
                "items": 0,
                "seconds": 0.0,
                "labels": {},
            },
        )
        by_session_items.setdefault(session_id, []).append(item)
        row["items"] += 1
        interval = item.get("interval") if isinstance(item.get("interval"), dict) else {}
        row["seconds"] += safe_float(interval.get("duration_sec"))
        label = str(item.get("label") or "unknown")
        row["labels"][label] = row["labels"].get(label, 0) + 1
    queue_by_session = []
    for row in by_session.values():
        session_id = str(row.get("session_id") or "")
        session_strategy = review_queue_lane_summary(by_session_items.get(session_id, []))
        queue_by_session.append(
            {
                **row,
                "minutes": round(safe_float(row.get("seconds")) / 60.0, 2),
                "seconds": round(safe_float(row.get("seconds")), 3),
                "labels": dict(sorted((row.get("labels") or {}).items())),
                "first_review_lane": session_strategy.get("first_recommended_lane"),
                "first_review_reason": session_strategy.get("first_recommended_reason"),
                "by_review_lane": session_strategy.get("by_lane") or [],
            }
        )
    queue_by_session.sort(key=lambda row: (-safe_float(row.get("seconds")), str(row.get("session_id"))))
    queue_strategy = review_queue_lane_summary(review_queue)
    review_focus = review_queue_focus(review_queue, queue_by_session)
    queue_actions = {
        "review_action_count": safe_int(queue_strategy.get("review_action_count")),
        "grouped_review_row_count": safe_int(queue_strategy.get("grouped_review_row_count")),
    }

    if blockers:
        status = "blocked_by_structural_issues"
    elif warnings:
        status = "manual_review_or_algorithmic_cleanup_needed"
    elif not_ready:
        status = "medium_risk_ready_but_some_sessions_need_review"
    else:
        status = "medium_risk_ready"

    next_actions: list[str] = []
    if blockers:
        next_actions.append("fix_blockers_before_using_for_medium_risk_meetings")
    if not_ready:
        next_actions.append("review_or_improve_sessions_with_use_gate_not_ready_for_notes")
    if review_queue:
        next_actions.append("run_build_review_plan_and_close_review_decisions")
    if audio_judge_review_queue and safe_int(audio_judge_review_queue.get("remaining_human_review_items")):
        next_actions.append("use_audio_judge_queue_as_shadow_signal_only_until_reviewed")
    if not next_actions:
        next_actions.append("keep_collecting_regression_sessions_and_watch_for_new_risk_flags")

    return {
        "target": "medium_risk_ready",
        "current_verdict": verdict,
        "status": status,
        "outstanding_conditions": {
            "blockers": blockers,
            "warnings": warnings,
            "sessions_not_ready_for_notes": len(not_ready),
            "review_queue_items": len(review_queue),
            **queue_actions,
            "review_queue_raw_audio_minutes": review_queue_minutes(review_queue),
            "audio_judge_remaining_human_review_items": (
                safe_int(audio_judge_review_queue.get("remaining_human_review_items"))
                if isinstance(audio_judge_review_queue, dict)
                else None
            ),
        },
        "session_targets": [
            {
                "session_id": row.get("session_id"),
                "label": row.get("label"),
                "use_gate": row.get("use_gate"),
                "selected_profile": row.get("selected_profile"),
                "review_burden_min": round(safe_float(row.get("review_burden_sec")) / 60.0, 2),
                "review_burden_ratio": row.get("review_burden_ratio"),
                "risk_flags": row.get("risk_flags") or [],
                "recommended_action": session_target_action(row),
            }
            for row in high_burden
        ],
        "review_queue_by_session": queue_by_session,
        "review_queue_strategy": queue_strategy,
        "review_focus": review_focus,
        "next_actions": next_actions,
    }


def session_cli_arg(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.startswith("./sessions/") or text.startswith("sessions/"):
        return text
    parts = Path(text).parts
    if "sessions" in parts:
        index = parts.index("sessions")
        if len(parts) > index + 1:
            return f"sessions/{parts[index + 1]}"
    return f"sessions/{Path(text).name}"


def review_queue_focus(review_queue: list[dict[str, Any]], queue_by_session: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
    if not review_queue:
        return None
    if queue_by_session:
        target_row = queue_by_session[0]
        target = session_cli_arg(target_row.get("session_id") or target_row.get("session"))
        lane = str(target_row.get("first_review_lane") or "")
        if target and lane:
            lane_items = [
                item
                for item in review_queue
                if same_session_target(item.get("session_id") or item.get("session"), target)
                and str(item.get("review_lane") or review_lane(item) or "") == lane
            ]
            item = lane_items[0] if lane_items else {}
            labels = target_row.get("labels") if isinstance(target_row.get("labels"), dict) else {}
            lane_labels: dict[str, Any] = {}
            for lane_row in target_row.get("by_review_lane") or []:
                if isinstance(lane_row, dict) and lane_row.get("lane") == lane and isinstance(lane_row.get("labels"), dict):
                    lane_labels = lane_row["labels"]
                    break
            return {
                "session_id": Path(target).name,
                "session_arg": target,
                "source_audit_id": item.get("source_audit_id"),
                "label": next(iter(lane_labels), next(iter(labels), None)),
                "labels": lane_labels or labels,
                "reason": item.get("reason"),
                "review_lane": lane,
                "review_action": item.get("review_action") or lane,
            }
    item = review_queue[0]
    target = session_cli_arg(item.get("session_id") or item.get("session"))
    if not target:
        return None
    return {
        "session_id": Path(target).name,
        "session_arg": target,
        "source_audit_id": item.get("source_audit_id"),
        "label": item.get("label"),
        "reason": item.get("reason"),
        "review_lane": item.get("review_lane") or review_lane(item),
    }


def first_review_target(promotion: dict[str, Any]) -> str | None:
    focus = promotion.get("review_focus") if isinstance(promotion.get("review_focus"), dict) else {}
    target = session_cli_arg(focus.get("session_arg") or focus.get("session_id"))
    if target:
        return target
    targets = promotion.get("session_targets")
    if isinstance(targets, list):
        for row in targets:
            if not isinstance(row, dict):
                continue
            if row.get("recommended_action") == "rerun_pipeline_or_fix_artifacts":
                continue
            use_gate = str(row.get("use_gate") or "")
            if use_gate == "ready_for_notes" or use_gate.startswith("pipeline_incomplete"):
                continue
            target = session_cli_arg(row.get("session_id") or row.get("session"))
            if target:
                return target
    by_session = promotion.get("review_queue_by_session")
    if isinstance(by_session, list):
        for row in by_session:
            if not isinstance(row, dict):
                continue
            target = session_cli_arg(row.get("session_id") or row.get("session"))
            if target:
                return target
    return None


def same_session_target(left: Any, right: Any) -> bool:
    left_arg = session_cli_arg(left)
    right_arg = session_cli_arg(right)
    if not left_arg or not right_arg:
        return False
    return left_arg == right_arg or Path(left_arg).name == Path(right_arg).name


def first_review_lane_for_target(promotion: dict[str, Any], review_target: str | None) -> str | None:
    if review_target:
        queue_by_session = promotion.get("review_queue_by_session")
        if isinstance(queue_by_session, list):
            for row in queue_by_session:
                if not isinstance(row, dict):
                    continue
                if not same_session_target(row.get("session_id") or row.get("session"), review_target):
                    continue
                lane = str(row.get("first_review_lane") or "")
                if lane:
                    return lane

        focus = promotion.get("review_focus") if isinstance(promotion.get("review_focus"), dict) else {}
        if same_session_target(focus.get("session_arg") or focus.get("session_id"), review_target):
            lane = str(focus.get("review_lane") or "")
            if lane:
                return lane

    strategy = promotion.get("review_queue_strategy") if isinstance(promotion.get("review_queue_strategy"), dict) else {}
    lane = str(strategy.get("first_recommended_lane") or "")
    return lane or None


def session_target_action(row: dict[str, Any]) -> str:
    use_gate = str(row.get("use_gate") or "")
    selected_profile = str(row.get("selected_profile") or "")
    flags = {str(item) for item in (row.get("risk_flags") or [])}
    missing_artifacts = any(item.startswith("missing:") for item in flags)
    if (
        use_gate.startswith("pipeline_incomplete")
        or selected_profile in {"", "missing", "None"}
        or "no_audit_cleanup_profile" in flags
        or missing_artifacts
    ):
        return "rerun_pipeline_or_fix_artifacts"
    if use_gate == "ready_for_notes":
        return "use_notes_with_normal_caution"
    return "close_review_decisions_or_improve_cleanup"


def active_audio_judge_queue_summary(sessions: list[dict[str, Any]], predictions: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not predictions:
        return None
    sessions_by_id = {str(row.get("session_id")): row for row in sessions}
    me_ids_by_session: dict[str, set[str]] = {}
    audit_rows_by_session: dict[str, dict[str, dict[str, Any]]] = {}
    review_resolved_by_session: dict[str, set[str]] = {}
    active: list[dict[str, Any]] = []
    resolved = 0
    resolved_by_current_audio_review = 0
    resolved_by_review = 0
    for row in predictions:
        session_id = str(row.get("session_id") or "")
        session = sessions_by_id.get(session_id)
        if not session:
            continue
        session_path = Path(str(session.get("session") or ""))
        profile = str(session.get("selected_profile") or "")
        if session_id not in me_ids_by_session:
            me_ids_by_session[session_id] = selected_me_ids(session_path, profile)
        if session_id not in audit_rows_by_session:
            audit_path = session_path / "derived/audit/audio-review-pack/audio_review_audit.jsonl"
            audit_rows_by_session[session_id] = {
                str(item.get("id") or ""): item
                for item in read_jsonl(audit_path)
                if isinstance(item, dict) and item.get("id")
            }
        if session_id not in review_resolved_by_session:
            review_resolved_by_session[session_id] = review_resolved_audio_ids(session_path, profile)
        selected_ids = me_ids_by_session[session_id]
        utterance_ids = {str(item) for item in row.get("utterance_ids", []) or []}
        if selected_ids and not (utterance_ids & selected_ids):
            resolved += 1
            continue
        current_audit = audit_rows_by_session[session_id].get(str(row.get("source_audit_id") or ""))
        if current_audit:
            current_classification = (
                current_audit.get("classification")
                if isinstance(current_audit.get("classification"), dict)
                else {}
            )
            current_verdict = str(current_classification.get("verdict") or "")
            if selected_ids and not active_audio_review_row(current_audit, selected_ids):
                resolved += 1
                continue
            if current_verdict not in {"probable_transcript_error", "needs_stronger_audio_judge"}:
                resolved_by_current_audio_review += 1
                continue
        if str(row.get("source_audit_id") or "") in review_resolved_by_session[session_id]:
            resolved_by_review += 1
            continue
        active.append(row)
    by_label: dict[str, int] = {}
    by_action: dict[str, int] = {}
    for row in active:
        label = str(row.get("judge_label") or "unknown")
        action = str(row.get("shadow_action") or "unknown")
        by_label[label] = by_label.get(label, 0) + 1
        by_action[action] = by_action.get(action, 0) + 1
    remove_candidates = by_action.get("candidate_remove_from_review_queue", 0)
    future_cleanup = by_action.get("candidate_future_cleanup_review", 0)
    mark_only = by_action.get("candidate_mark_only_review", 0)
    return {
        "items": len(active),
        "resolved_by_selected_profile_items": resolved,
        "resolved_by_current_audio_review_items": resolved_by_current_audio_review,
        "resolved_by_review_items": resolved_by_review,
        "by_judge_label": dict(sorted(by_label.items())),
        "by_shadow_action": dict(sorted(by_action.items())),
        "candidate_review_reduction_items": remove_candidates,
        "candidate_future_cleanup_items": future_cleanup,
        "candidate_mark_only_items": mark_only,
        "remaining_human_review_items": len(active) - remove_candidates,
    }


def operational_verdict(
    session_quality: dict[str, Any],
    corpus: dict[str, Any] | None,
    audio_judge: dict[str, Any] | None,
) -> tuple[str, list[str], list[str]]:
    sessions = session_quality.get("sessions") if isinstance(session_quality.get("sessions"), list) else []
    summary = session_quality.get("summary") if isinstance(session_quality.get("summary"), dict) else {}
    blockers: list[str] = []
    warnings: list[str] = []

    session_count = safe_int(summary.get("session_count"))
    complete = safe_int(summary.get("complete_pipeline_count"))
    complete_ratio = complete / session_count if session_count > 0 else 0.0
    verdicts = summary.get("by_verdict") if isinstance(summary.get("by_verdict"), dict) else {}
    risky_or_failed = safe_int(verdicts.get("risky")) + safe_int(verdicts.get("failed"))
    selected_profiles = summary.get("by_selected_profile") if isinstance(summary.get("by_selected_profile"), dict) else {}
    cleanup_profiles = (
        safe_int(selected_profiles.get("audit_cleanup_v1"))
        + safe_int(selected_profiles.get("audit_cleanup_v2"))
        + safe_int(selected_profiles.get("audit_cleanup_v3"))
        + safe_int(selected_profiles.get("audit_cleanup_v4"))
        + safe_int(selected_profiles.get("audit_cleanup_v5"))
        + safe_int(selected_profiles.get("audit_cleanup_v6"))
        + safe_int(selected_profiles.get("reviewed_v1"))
        + safe_int(selected_profiles.get("agent_reviewed_v1"))
    )
    cleanup_ratio = cleanup_profiles / session_count if session_count > 0 else 0.0

    burdens = [session_review_burden(row) for row in sessions]
    total_duration = sum(item["duration_sec"] for item in burdens)
    total_burden = sum(item["review_burden_sec"] for item in burdens)
    burden_ratio = total_burden / total_duration if total_duration > 0 else 0.0
    max_session_burden = max((item["review_burden_ratio"] for item in burdens), default=0.0)
    high_risk_sessions = [item for item in burdens if item["review_burden_ratio"] > 0.08 or len(item["risk_flags"]) >= 4]

    corpus_readiness = corpus.get("readiness") if isinstance(corpus, dict) else None
    missing_labels = corpus.get("missing_labels") if isinstance(corpus, dict) and isinstance(corpus.get("missing_labels"), list) else []
    audio_judge_readiness = audio_judge.get("readiness") if isinstance(audio_judge, dict) else None

    if session_count < 5:
        blockers.append("too_few_regression_sessions")
    if complete_ratio < 0.80:
        blockers.append("not_enough_complete_pipelines")
    if risky_or_failed > 0:
        blockers.append("risky_or_failed_session_verdicts_present")
    if cleanup_ratio < 0.70:
        warnings.append("many_sessions_without_audit_cleanup_profile")
    if burden_ratio > 0.08:
        blockers.append("total_review_burden_too_high")
    elif burden_ratio > 0.04:
        warnings.append("total_review_burden_noticeable")
    if max_session_burden > 0.12:
        blockers.append("single_session_review_burden_too_high")
    elif high_risk_sessions:
        warnings.append("some_sessions_need_manual_review_before_use")
    if corpus_readiness not in {"useful_for_audio_judge_v0", "broad_regression_ready"}:
        warnings.append("regression_corpus_not_ready_for_audio_judge")
    if audio_judge_readiness not in {"shadow_ready", "cleanup_shadow_candidate"}:
        warnings.append("audio_judge_v0_not_shadow_ready")
    if missing_labels:
        warnings.append("regression_corpus_missing_labels:" + ",".join(str(label) for label in missing_labels))

    if blockers:
        verdict = "not_ready"
    elif warnings:
        verdict = "pilot_ready_with_review"
    else:
        verdict = "medium_risk_ready"
    return verdict, blockers, warnings


def build_report(
    session_quality: dict[str, Any],
    corpus: dict[str, Any] | None,
    audio_judge: dict[str, Any] | None,
    audio_judge_queue: list[dict[str, Any]],
    inputs: dict[str, str],
    max_review_items: int,
) -> dict[str, Any]:
    raw_sessions = session_quality.get("sessions") if isinstance(session_quality.get("sessions"), list) else []
    session_quality, excluded_diagnostics = operational_scope(session_quality)
    sessions = session_quality.get("sessions") if isinstance(session_quality.get("sessions"), list) else []
    burdens = [session_review_burden(row) for row in sessions]
    total_duration = sum(item["duration_sec"] for item in burdens)
    total_burden = sum(item["review_burden_sec"] for item in burdens)
    total_transcript_burden = sum(item["transcript_review_burden_sec"] for item in burdens)
    verdict, blockers, warnings = operational_verdict(session_quality, corpus, audio_judge)
    gates: dict[str, int] = {}
    for row in burdens:
        gate = str(row.get("use_gate") or "unknown")
        gates[gate] = gates.get(gate, 0) + 1
    review_queue = build_review_queue(burdens, max_review_items)
    review_actions = review_action_summary(review_queue)
    active_judge_queue = active_audio_judge_queue_summary(burdens, audio_judge_queue)
    audio_judge_review_queue = active_judge_queue or (audio_judge.get("review_queue") if isinstance(audio_judge, dict) else None)
    promotion = promotion_plan(verdict, blockers, warnings, burdens, review_queue, audio_judge_review_queue)
    return {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": {"name": "report-operational-readiness", "version": SCRIPT_VERSION},
        "inputs": inputs,
        "operational_verdict": verdict,
        "scope": "local tool for medium-risk working meetings",
        "blockers": blockers,
        "warnings": warnings,
        "summary": {
            "session_count": len(sessions),
            "all_session_count": len(raw_sessions),
            "excluded_diagnostic_session_count": len(excluded_diagnostics),
            "excluded_diagnostic_sessions": [str(row.get("session_id") or row.get("label") or "") for row in excluded_diagnostics],
            "complete_pipeline_count": safe_int((session_quality.get("summary") or {}).get("complete_pipeline_count")),
            "selected_profiles": (session_quality.get("summary") or {}).get("by_selected_profile", {}),
            "sessions_with_suggested_review_v1": safe_int(
                (session_quality.get("summary") or {}).get("sessions_with_suggested_review_v1")
            ),
            "session_verdicts": (session_quality.get("summary") or {}).get("by_verdict", {}),
            "total_duration_sec": round(total_duration, 3),
            "total_review_burden_sec": round(total_burden, 3),
            "total_review_burden_ratio": round(total_burden / total_duration, 6) if total_duration > 0 else 0.0,
            "total_notes_review_burden_sec": round(total_burden, 3),
            "total_notes_review_burden_ratio": round(total_burden / total_duration, 6) if total_duration > 0 else 0.0,
            "total_transcript_review_burden_sec": round(total_transcript_burden, 3),
            "total_transcript_review_burden_ratio": (
                round(total_transcript_burden / total_duration, 6) if total_duration > 0 else 0.0
            ),
            "use_gates": dict(sorted(gates.items())),
            "corpus_readiness": corpus.get("readiness") if isinstance(corpus, dict) else None,
            "corpus_item_count": safe_int(corpus.get("item_count")) if isinstance(corpus, dict) else 0,
            "corpus_missing_labels": corpus.get("missing_labels") if isinstance(corpus, dict) else None,
            "audio_judge_readiness": audio_judge.get("readiness") if isinstance(audio_judge, dict) else None,
            "audio_judge_cv_accuracy": (
                safe_float((audio_judge.get("evaluation") or {}).get("cv_accuracy"))
                if isinstance(audio_judge, dict)
                else None
            ),
            "audio_judge_review_queue": audio_judge_review_queue,
            "review_queue_items": len(review_queue),
            "review_action_count": review_actions["review_action_count"],
            "grouped_review_row_count": review_actions["grouped_review_row_count"],
            "by_review_action": review_actions["by_review_action"],
            "by_review_lane_actions": review_actions["by_review_lane_actions"],
            "by_review_lane_grouped_rows": review_actions["by_review_lane_grouped_rows"],
        },
        "session_review_burden": burdens,
        "review_queue": review_queue,
        "promotion_plan": promotion,
        "recommendations": recommendations(verdict, blockers, warnings),
        "next_commands": build_next_commands(blockers, promotion),
    }


def build_next_commands(blockers: list[str], promotion: dict[str, Any]) -> list[dict[str, str]]:
    commands: list[dict[str, str]] = []
    if "not_enough_complete_pipelines" in blockers:
        target = first_pipeline_target(promotion)
        if target:
            commands.append(
                {
                    "id": "process_session",
                    "label": "Process the first incomplete high-value session.",
                    "command": f"murmurmark process {target}",
                }
            )
        else:
            commands.append(
                {
                    "id": "process_corpus",
                    "label": "Rebuild the corpus pipeline so operational readiness has enough complete sessions.",
                    "command": "murmurmark corpus process all",
                }
            )
    review_target = first_review_target(promotion)
    first_lane = first_review_lane_for_target(promotion, review_target)
    if first_lane:
        session_option = f" --session {review_target}" if review_target else ""
        commands.append(
            {
                "id": "review_first_lane",
                "label": f"Build the first review lane pack ({first_lane}).",
                "command": f"murmurmark review first-lane{session_option}",
            }
        )
        commands.append(
            {
                "id": "review_workspace",
                "label": "Build all review lane packs and answer sheets.",
                "command": f"murmurmark review workspace{session_option}",
            }
        )
    if not commands:
        commands.append(
            {
                "id": "corpus_report",
                "label": "Refresh the current corpus status view.",
                "command": "murmurmark corpus report",
            }
        )
    return commands


def first_pipeline_target(promotion: dict[str, Any]) -> str | None:
    targets = promotion.get("session_targets")
    if not isinstance(targets, list):
        return None
    for target in targets:
        if not isinstance(target, dict):
            continue
        if target.get("recommended_action") != "rerun_pipeline_or_fix_artifacts":
            continue
        use_gate = str(target.get("use_gate") or "")
        if not use_gate.startswith("pipeline_incomplete"):
            continue
        session_id = str(target.get("session_id") or "").strip()
        if not session_id:
            continue
        if session_id.startswith("sessions/") or session_id.startswith("./sessions/"):
            return session_id
        return f"sessions/{session_id}"
    return None


def recommendations(verdict: str, blockers: list[str], warnings: list[str]) -> list[str]:
    rows: list[str] = []
    if verdict == "not_ready":
        rows.append("do_not_use_without_manual_audio_review")
    if "single_session_review_burden_too_high" in blockers or "some_sessions_need_manual_review_before_use" in warnings:
        rows.append("review_audio_review_report_for_high_burden_sessions")
    if any(item.startswith("regression_corpus") for item in warnings):
        rows.append("expand_or_rebuild_regression_corpus_before_audio_judge_v1")
    if "audio_judge_v0_not_shadow_ready" in warnings:
        rows.append("do_not_use_audio_judge_for_cleanup_yet")
    rows.append("use_quality_verdict_and_notes_for_medium_risk_meetings_with_review")
    rows.append("keep_raw_audio_private_and_derived_artifacts_ignored")
    return rows


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# MurmurMark Operational Readiness",
        "",
        f"Verdict: `{report['operational_verdict']}`",
        f"Scope: `{report['scope']}`",
        "",
        "## Summary",
        "",
        f"- Sessions: `{report['summary']['session_count']}`",
        f"- Excluded diagnostic sessions: `{report['summary'].get('excluded_diagnostic_session_count', 0)}` / `{report['summary'].get('all_session_count', report['summary']['session_count'])}` total",
        f"- Complete pipelines: `{report['summary']['complete_pipeline_count']}`",
        f"- Total notes review burden: `{round(report['summary']['total_review_burden_sec'] / 60.0, 2)} min`",
        f"- Notes review burden ratio: `{round(report['summary']['total_review_burden_ratio'] * 100.0, 2)}%`",
        f"- Total transcript/export review burden: `{round(safe_float(report['summary'].get('total_transcript_review_burden_sec')) / 60.0, 2)} min`",
        f"- Transcript/export review burden ratio: `{round(safe_float(report['summary'].get('total_transcript_review_burden_ratio')) * 100.0, 2)}%`",
        f"- Review queue rows: `{report['summary'].get('review_queue_items')}`",
        f"- Packed review actions: `{report['summary'].get('review_action_count')}`",
        f"- Grouped review rows saved: `{report['summary'].get('grouped_review_row_count')}`",
        f"- Corpus readiness: `{report['summary']['corpus_readiness']}`",
        f"- Suggested review shadow profiles: `{report['summary'].get('sessions_with_suggested_review_v1')}`",
        f"- Corpus items: `{report['summary']['corpus_item_count']}`",
        f"- Audio judge readiness: `{report['summary']['audio_judge_readiness']}`",
        f"- Audio judge CV accuracy: `{report['summary']['audio_judge_cv_accuracy']}`",
        f"- Audio judge remaining review items: `{((report['summary'].get('audio_judge_review_queue') or {}).get('remaining_human_review_items'))}`",
        f"- Audio judge resolved by selected profile: `{((report['summary'].get('audio_judge_review_queue') or {}).get('resolved_by_selected_profile_items'))}`",
        "",
        "## Next Commands",
        "",
    ]
    next_commands = [item for item in report.get("next_commands", []) if isinstance(item, dict)]
    if next_commands:
        for item in next_commands:
            lines.append(f"- {item.get('label')} `{item.get('command')}`")
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Blockers",
            "",
        ]
    )
    if report["blockers"]:
        lines.extend(f"- `{item}`" for item in report["blockers"])
    else:
        lines.append("- none")
    lines.extend(["", "## Warnings", ""])
    if report["warnings"]:
        lines.extend(f"- `{item}`" for item in report["warnings"])
    else:
        lines.append("- none")
    plan = report.get("promotion_plan") if isinstance(report.get("promotion_plan"), dict) else {}
    conditions = plan.get("outstanding_conditions") if isinstance(plan.get("outstanding_conditions"), dict) else {}
    lines.extend(
        [
            "",
            "## Promotion Plan",
            "",
            f"- Target: `{plan.get('target')}`",
            f"- Status: `{plan.get('status')}`",
            f"- Sessions not ready for notes: `{conditions.get('sessions_not_ready_for_notes')}`",
            f"- Review queue: `{conditions.get('review_queue_items')}` rows / `{conditions.get('review_action_count')}` packed actions / `{conditions.get('review_queue_raw_audio_minutes')}` raw audio min",
            f"- Grouped review rows saved: `{conditions.get('grouped_review_row_count')}`",
            f"- Audio judge remaining human review items: `{conditions.get('audio_judge_remaining_human_review_items')}`",
            "",
            "### Review Queue Strategy",
            "",
        ]
    )
    strategy = plan.get("review_queue_strategy") if isinstance(plan.get("review_queue_strategy"), dict) else {}
    first_lane = strategy.get("first_recommended_lane")
    after_first = strategy.get("after_first_lane_estimate") if isinstance(strategy.get("after_first_lane_estimate"), dict) else {}
    if first_lane:
        lines.extend(
            [
                f"- First lane: `{first_lane}`",
                f"- First lane reason: `{strategy.get('first_recommended_reason')}`",
                f"- Quick lane: `{strategy.get('quick_recommended_lane')}`",
                f"- After first lane estimate: `{after_first.get('remaining_items')}` rows / `{after_first.get('remaining_actions')}` actions / `{after_first.get('remaining_minutes')}` min",
                "",
                "| Lane | Rows | Actions | Grouped Rows | Raw sec | Labels |",
                "|---|---:|---:|---:|---:|---|",
            ]
        )
        for row in strategy.get("by_lane", []) or []:
            lines.append(
                f"| `{row.get('lane')}` | {row.get('items')} | {row.get('actions')} | {row.get('grouped_rows')} | {safe_float(row.get('seconds')):.2f} | `{row.get('labels')}` |"
            )
        commands = strategy.get("commands") if isinstance(strategy.get("commands"), dict) else {}
        workspace_cmd = commands.get("build_review_workspace")
        apply_workspace_cmd = commands.get("apply_review_workspace")
        dry_run_suggested_cmd = commands.get("dry_run_suggested_workspace")
        apply_suggested_cmd = commands.get("apply_suggested_workspace")
        build_suggested_profile_cmd = commands.get("build_suggested_review_profile")
        report_suggested_cmd = commands.get("report_suggested_review_shadow")
        apply_suggested_cleanup_cmd = commands.get("apply_suggested_cleanup")
        apply_audio_review_cleanup_v6_cmd = commands.get("apply_audio_review_cleanup_v6")
        build_cmd = commands.get("build_first_lane_pack")
        review_cmd = commands.get("review_first_lane")
        if (
            workspace_cmd
            or apply_workspace_cmd
            or dry_run_suggested_cmd
            or apply_suggested_cmd
            or build_suggested_profile_cmd
            or report_suggested_cmd
            or apply_suggested_cleanup_cmd
            or apply_audio_review_cleanup_v6_cmd
            or build_cmd
            or review_cmd
        ):
            lines.extend(["", "```bash"])
            if workspace_cmd:
                lines.append(str(workspace_cmd))
                lines.append("# edit sessions/_reports/review-plan/lane-packs/review_lane_answers.<lane>.txt")
            if apply_workspace_cmd:
                lines.append(str(apply_workspace_cmd))
            if dry_run_suggested_cmd:
                lines.append(str(dry_run_suggested_cmd))
            if apply_suggested_cmd:
                lines.append(str(apply_suggested_cmd))
            if build_suggested_profile_cmd:
                lines.append(str(build_suggested_profile_cmd))
            if report_suggested_cmd:
                lines.append(str(report_suggested_cmd))
            if apply_suggested_cleanup_cmd:
                lines.append(str(apply_suggested_cleanup_cmd))
            if apply_audio_review_cleanup_v6_cmd:
                lines.append(str(apply_audio_review_cleanup_v6_cmd))
            if build_cmd:
                lines.append(str(build_cmd))
            if review_cmd:
                lines.append(str(review_cmd))
            lines.append("```")
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "### Session Targets",
            "",
            "| Session | Gate | Review min | Flags | Action |",
            "|---|---|---:|---|---|",
        ]
    )
    for row in plan.get("session_targets", [])[:10]:
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{row.get('session_id')}`",
                    str(row.get("use_gate")),
                    f"{safe_float(row.get('review_burden_min')):.2f}",
                    ", ".join(row.get("risk_flags") or []),
                    str(row.get("recommended_action")),
                ]
            )
            + " |"
        )
    if not plan.get("session_targets"):
        lines.append("| none | - | 0.00 | - | - |")
    lines.extend(["", "### Next Actions", ""])
    lines.extend(f"- `{item}`" for item in plan.get("next_actions", []))
    lines.extend(["", "## Session Review Burden", ""])
    lines.append("| Session | Gate | Profile | Verdict | Notes review min | Notes review % | Transcript/export review min | Flags |")
    lines.append("|---|---|---|---|---:|---:|---:|---|")
    for row in sorted(report["session_review_burden"], key=lambda item: item["review_burden_ratio"], reverse=True):
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{row['session_id']}`",
                    str(row["use_gate"]),
                    str(row["selected_profile"]),
                    str(row["verdict"]),
                    f"{row['review_burden_sec'] / 60.0:.2f}",
                    f"{row['review_burden_ratio'] * 100.0:.2f}",
                    f"{safe_float(row.get('transcript_review_burden_sec')) / 60.0:.2f}",
                    ", ".join(row["risk_flags"]),
                ]
            )
            + " |"
        )
    lines.extend(["", "## Review Queue", ""])
    for item in report.get("review_queue", [])[:25]:
        interval = item.get("interval") if isinstance(item.get("interval"), dict) else {}
        commands = item.get("commands") if isinstance(item.get("commands"), dict) else {}
        stereo = commands.get("stereo_clean_left_remote_right") or commands.get("stereo_mic_left_remote_right")
        lines.extend(
            [
                f"### `{item.get('session_id')}` `{item.get('label')}` {interval.get('start_time', '')}-{interval.get('end_time', '')}",
                "",
                f"- Verdict: `{item.get('verdict')}`, confidence `{item.get('confidence')}`",
                f"- Audit id: `{item.get('source_audit_id')}`",
            ]
        )
        if stereo:
            lines.append(f"- Stereo: `{stereo}`")
        for text in item.get("text", [])[:3]:
            lines.append(f"- {text.get('role')} `{text.get('id')}`: {text.get('text')}")
        lines.append("")
    lines.extend(["", "## Recommendations", ""])
    lines.extend(f"- `{item}`" for item in report["recommendations"])
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    session_quality = read_json(args.session_quality)
    if not session_quality:
        raise SystemExit(f"missing session quality report: {args.session_quality}")
    corpus = read_json(args.corpus_evaluation)
    audio_judge = read_json(args.audio_judge)
    audio_judge_queue = read_jsonl(args.audio_judge_queue)
    inputs = {
        "session_quality": str(args.session_quality),
        "corpus_evaluation": str(args.corpus_evaluation),
        "audio_judge": str(args.audio_judge),
        "audio_judge_queue": str(args.audio_judge_queue),
    }
    report = build_report(session_quality, corpus, audio_judge, audio_judge_queue, inputs, args.max_review_items)
    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(out_dir / "operational_readiness_report.json", report)
    write_markdown(out_dir / "operational_readiness_report.md", report)
    print(f"verdict: {report['operational_verdict']}")
    print(f"written: {out_dir / 'operational_readiness_report.json'}")
    next_commands = [item for item in report.get("next_commands", []) if isinstance(item, dict)]
    if next_commands:
        print(f"next_command: {next_commands[0].get('command')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
