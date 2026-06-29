#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


SCRIPT_VERSION = "0.7.0"
SCHEMA = "murmurmark.agent_review_decisions/v1"
OUTPUT_PROFILE = "agent_reviewed_v1"
TOKEN_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9_+-]+")
LOCAL_RECALL_REPAIR_PROFILE = "local_recall_repair_v1"
SPEAKER_STATE_CACHE: dict[Path, list[dict[str, Any]]] = {}
DIALOGUE_CACHE: dict[tuple[Path, str], list[dict[str, Any]]] = {}
KEEP_PROPAGATION_REASONS = {
    "adjacent_me_continuation_keep",
    "bounded_remote_leak_with_local_content",
    "likely_reliable_with_local_support",
    "speaker_state_local_only_asr_noise_keep",
    "speaker_state_local_short_backchannel_keep",
    "speaker_state_local_only_remote_leak_keep",
    "speaker_state_mostly_local_short_remote_leak_keep",
    "speaker_state_pure_local_partial_duplicate_keep",
    "speaker_state_pure_local_remote_context_keep",
    "speaker_state_pure_local_short_duplicate_keep",
    "stronger_audio_judge_confirmed_local_keep",
}

STOP_WORDS = {
    "а",
    "в",
    "во",
    "вот",
    "да",
    "для",
    "же",
    "и",
    "или",
    "как",
    "когда",
    "мы",
    "на",
    "не",
    "но",
    "ну",
    "он",
    "она",
    "они",
    "оно",
    "по",
    "при",
    "с",
    "со",
    "там",
    "то",
    "тогда",
    "тоже",
    "тут",
    "ты",
    "у",
    "что",
    "это",
    "этого",
    "этой",
    "этот",
    "я",
}

PROTECTED_MARKERS = (
    "надо",
    "нужно",
    "сделаю",
    "сделаем",
    "давай",
    "давайте",
    "решили",
    "договорились",
    "согласовали",
    "риск",
    "проблем",
    "вопрос",
    "блокер",
    "проверь",
    "посмотрю",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build conservative agent review decisions from audio-review audit artifacts.")
    parser.add_argument(
        "--session-quality",
        type=Path,
        default=Path("sessions/_reports/session-quality/session_quality_report.json"),
        help="Session quality report with currently selected profiles.",
    )
    parser.add_argument(
        "--audio-judge-queue",
        type=Path,
        default=Path("sessions/_reports/audio-judge-v0/audio_judge_v0_queue_predictions.jsonl"),
        help="Optional audio judge v0 queue predictions used as extra evidence.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("sessions/_reports/review-plan/review_decisions.agent_reviewed_v1.jsonl"),
        help="Output closed agent decisions JSONL.",
    )
    parser.add_argument(
        "--template-out",
        type=Path,
        default=Path("sessions/_reports/review-plan/review_decisions.agent_reviewed_v1.template.jsonl"),
        help="Output agent decision scope JSONL.",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("sessions/_reports/review-plan/agent_review_report.agent_reviewed_v1.json"),
        help="Output agent decision build report.",
    )
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        value = json.load(file)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(value)
    return rows


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")


def suffix(profile: str) -> str:
    return "" if profile == "current" else f".{profile}"


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


def tokens(text: Any) -> list[str]:
    return [token.replace("ё", "е").lower() for token in TOKEN_RE.findall(str(text or ""))]


def content_tokens(text: Any) -> set[str]:
    return {token for token in tokens(text) if token not in STOP_WORDS and len(token) > 2}


def has_protected_marker(text: Any) -> bool:
    lowered = str(text or "").lower().replace("ё", "е")
    return any(marker in lowered for marker in PROTECTED_MARKERS)


def role_name(row: dict[str, Any]) -> str:
    source = str(row.get("source_track") or "").lower()
    role = str(row.get("role") or row.get("speaker_label") or "").lower()
    if source == "mic" or role == "me":
        return "me"
    if source == "remote" or role in {"remote", "colleagues"}:
        return "remote"
    return role or "unknown"


def selected_me_ids(session: Path, profile: str) -> set[str]:
    rows = clean_dialogue_rows(session, profile)
    return {str(row.get("id")) for row in rows if isinstance(row, dict) and role_name(row) == "me" and row.get("id")}


def clean_dialogue_rows(session: Path, profile: str) -> list[dict[str, Any]]:
    key = (session, profile)
    if key in DIALOGUE_CACHE:
        return DIALOGUE_CACHE[key]
    path = session / "derived/transcript-simple/whisper-cpp/resolved" / f"clean_dialogue{suffix(profile)}.json"
    if not path.exists():
        DIALOGUE_CACHE[key] = []
        return []
    rows = read_json(path).get("utterances")
    DIALOGUE_CACHE[key] = rows if isinstance(rows, list) else []
    return DIALOGUE_CACHE[key]


def adjacent_me_context(session: Path, profile: str, me_ids: set[str]) -> dict[str, Any] | None:
    if not me_ids:
        return None
    rows = clean_dialogue_rows(session, profile)
    by_id = {str(row.get("id")): index for index, row in enumerate(rows) if isinstance(row, dict) and row.get("id")}
    contexts: list[dict[str, Any]] = []
    for me_id in sorted(me_ids):
        index = by_id.get(str(me_id))
        if index is None:
            continue
        current = rows[index]
        prev = rows[index - 1] if index > 0 and isinstance(rows[index - 1], dict) else None
        nxt = rows[index + 1] if index + 1 < len(rows) and isinstance(rows[index + 1], dict) else None
        prev_gap = None
        next_gap = None
        if prev:
            prev_gap = round(safe_float(current.get("start")) - safe_float(prev.get("end")), 6)
        if nxt:
            next_gap = round(safe_float(nxt.get("start")) - safe_float(current.get("end")), 6)
        contexts.append(
            {
                "utterance_id": me_id,
                "start": safe_float(current.get("start")),
                "end": safe_float(current.get("end")),
                "duration_sec": round(max(0.0, safe_float(current.get("end")) - safe_float(current.get("start"))), 6),
                "text": current.get("text"),
                "prev_id": prev.get("id") if prev else None,
                "prev_role": role_name(prev) if prev else None,
                "prev_gap_sec": prev_gap,
                "prev_text": prev.get("text") if prev else None,
                "next_id": nxt.get("id") if nxt else None,
                "next_role": role_name(nxt) if nxt else None,
                "next_gap_sec": next_gap,
                "next_text": nxt.get("text") if nxt else None,
            }
        )
    if not contexts:
        return None
    return min(contexts, key=lambda item: (safe_float(item.get("start")), str(item.get("utterance_id") or "")))


def local_recall_repair_report(session: Path) -> dict[str, Any] | None:
    path = (
        session
        / "derived/transcript-simple/whisper-cpp/local-recall-repair"
        / f"local_recall_repair_report.{LOCAL_RECALL_REPAIR_PROFILE}.json"
    )
    if not path.exists():
        return None
    return read_json(path)


def local_recall_repair_usable_for(session: Path, profile: str) -> bool:
    report = local_recall_repair_report(session)
    if not isinstance(report, dict):
        return False
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    gates = report.get("gates") if isinstance(report.get("gates"), dict) else {}
    resolved = session / "derived/transcript-simple/whisper-cpp/resolved"
    return (
        report.get("input_profile") == profile
        and gates.get("passed") is True
        and safe_float(summary.get("applied_repairs")) > 0
        and (resolved / f"clean_dialogue{suffix(LOCAL_RECALL_REPAIR_PROFILE)}.json").exists()
        and (resolved / f"quality_report{suffix(LOCAL_RECALL_REPAIR_PROFILE)}.json").exists()
    )


def base_profile_for_agent(session: Path, selected_profile: str) -> str:
    def with_local_recall_repair(profile: str) -> str:
        if local_recall_repair_usable_for(session, profile):
            return LOCAL_RECALL_REPAIR_PROFILE
        return profile

    if selected_profile != OUTPUT_PROFILE:
        return with_local_recall_repair(selected_profile)
    if local_recall_repair_usable_for(session, OUTPUT_PROFILE):
        return LOCAL_RECALL_REPAIR_PROFILE
    report_path = (
        session
        / "derived/transcript-simple/whisper-cpp/review-decisions"
        / f"review_decisions_report.{OUTPUT_PROFILE}.json"
    )
    if report_path.exists():
        report = read_json(report_path)
        summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
        input_profile = str(report.get("input_profile") or summary.get("input_profile") or "")
        if input_profile and input_profile != OUTPUT_PROFILE:
            return with_local_recall_repair(input_profile)
    for candidate in ("audit_cleanup_v6", "audit_cleanup_v5", "audit_cleanup_v4", "audit_cleanup_v3", "audit_cleanup_v2", "audit_cleanup_v1", "shadow_v2", "current"):
        path = session / "derived/transcript-simple/whisper-cpp/resolved" / f"clean_dialogue{suffix(candidate)}.json"
        if path.exists():
            return with_local_recall_repair(candidate)
    return "current"


def audio_review_me_ids(row: dict[str, Any]) -> set[str]:
    rows = row.get("utterances")
    if not isinstance(rows, list):
        return set()
    return {str(item.get("id")) for item in rows if isinstance(item, dict) and role_name(item) == "me" and item.get("id")}


def audio_review_remote_text(row: dict[str, Any]) -> str:
    parts: list[str] = []
    for item in row.get("utterances") or []:
        if isinstance(item, dict) and role_name(item) == "remote":
            parts.append(str(item.get("text") or ""))
    return " ".join(parts)


def audio_review_remote_ids(row: dict[str, Any]) -> set[str]:
    rows = row.get("utterances")
    if not isinstance(rows, list):
        return set()
    return {str(item.get("id")) for item in rows if isinstance(item, dict) and role_name(item) == "remote" and item.get("id")}


def audio_review_me_text(row: dict[str, Any]) -> str:
    parts: list[str] = []
    for item in row.get("utterances") or []:
        if isinstance(item, dict) and role_name(item) == "me":
            parts.append(str(item.get("text") or ""))
    return " ".join(parts)


def interval_coverage(row: dict[str, Any], role: str) -> float:
    interval = row.get("interval") if isinstance(row.get("interval"), dict) else {}
    start = safe_float(interval.get("start"))
    end = safe_float(interval.get("end"))
    if end <= start:
        return 0.0
    coverages: list[float] = []
    for item in row.get("utterances") or []:
        if not isinstance(item, dict) or role_name(item) != role:
            continue
        item_start = safe_float(item.get("start"))
        item_end = safe_float(item.get("end"))
        item_duration = item_end - item_start
        if item_duration <= 0.0:
            continue
        overlap = max(0.0, min(end, item_end) - max(start, item_start))
        coverages.append(overlap / item_duration)
    return max(coverages, default=0.0)


def is_active_audio_review_row(row: dict[str, Any], selected_ids: set[str]) -> bool:
    classification = row.get("classification") if isinstance(row.get("classification"), dict) else {}
    label = str(classification.get("label") or "")
    me_ids = audio_review_me_ids(row)
    if label == "lost_me":
        return True
    if not me_ids:
        return True
    return bool(me_ids & selected_ids)


def unique_me_content_tokens(row: dict[str, Any]) -> set[str]:
    me = content_tokens(audio_review_me_text(row))
    remote = content_tokens(audio_review_remote_text(row))
    return me - remote


def speaker_state_rows(session: Path) -> list[dict[str, Any]]:
    path = session / "derived/preprocess/echo/speaker_state.jsonl"
    if path not in SPEAKER_STATE_CACHE:
        SPEAKER_STATE_CACHE[path] = read_jsonl(path)
    return SPEAKER_STATE_CACHE[path]


def speaker_state_ratios(session: Path, start: float, end: float) -> dict[str, float]:
    duration = max(0.0, end - start)
    if duration <= 0.0:
        return {
            "covered_ratio": 0.0,
            "local_only_ratio": 0.0,
            "remote_only_ratio": 0.0,
            "double_talk_ratio": 0.0,
            "remote_active_ratio": 0.0,
        }
    covered = 0.0
    local_only = 0.0
    remote_only = 0.0
    double_talk = 0.0
    for row in speaker_state_rows(session):
        row_start = safe_float(row.get("start"))
        row_end = safe_float(row.get("end"))
        overlap = max(0.0, min(end, row_end) - max(start, row_start))
        if overlap <= 0.0:
            continue
        covered += overlap
        state = str(row.get("state") or "")
        if state == "local_only":
            local_only += overlap
        elif state.startswith("remote_only"):
            remote_only += overlap
        elif state.startswith("double_talk"):
            double_talk += overlap
    return {
        "covered_ratio": round(min(1.0, covered / duration), 6),
        "local_only_ratio": round(local_only / duration, 6),
        "remote_only_ratio": round(remote_only / duration, 6),
        "double_talk_ratio": round(double_talk / duration, 6),
        "remote_active_ratio": round((remote_only + double_talk) / duration, 6),
    }


def queue_index(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (str(row.get("session_id") or ""), str(row.get("source_audit_id") or "")): row
        for row in read_jsonl(path)
    }


def stronger_judge_index(session: Path) -> dict[str, dict[str, Any]]:
    path = session / "derived/audit/audio-review-pack/faster_whisper_judge.jsonl"
    rows: dict[str, dict[str, Any]] = {}
    for row in read_jsonl(path):
        source_id = str(row.get("source_pack_item_id") or "")
        if not source_id:
            continue
        classification = row.get("classification") if isinstance(row.get("classification"), dict) else {}
        confidence = safe_float(classification.get("confidence"))
        previous = rows.get(source_id)
        previous_classification = previous.get("classification") if isinstance(previous, dict) and isinstance(previous.get("classification"), dict) else {}
        if previous is None or confidence > safe_float(previous_classification.get("confidence")):
            rows[source_id] = row
    return rows


def rejection_reason(evidence: dict[str, Any]) -> str:
    label = str(evidence.get("label") or "")
    verdict = str(evidence.get("verdict") or "")
    confidence = safe_float(evidence.get("confidence"))
    duration = safe_float(evidence.get("duration_sec"))
    local_support = safe_float(evidence.get("local_support"))
    remote_duplicate = safe_float(evidence.get("remote_duplicate"))
    remote_leak = safe_float(evidence.get("remote_leak"))
    remote_similarity = safe_float(evidence.get("remote_similarity"))
    asr_noise = safe_float(evidence.get("asr_noise"))
    text_similarity = safe_float(evidence.get("text_similarity"))
    token_containment = safe_float(evidence.get("token_containment"))
    me_coverage = safe_float(evidence.get("me_overlap_coverage"))
    remote_coverage = safe_float(evidence.get("remote_overlap_coverage"))
    unique_tokens = evidence.get("unique_me_content_tokens") if isinstance(evidence.get("unique_me_content_tokens"), list) else []
    state = evidence.get("speaker_state") if isinstance(evidence.get("speaker_state"), dict) else {}
    local_only_ratio = safe_float(state.get("local_only_ratio"))
    remote_active_ratio = safe_float(state.get("remote_active_ratio"))

    if evidence.get("protected_marker"):
        return "protected_action_decision_risk_marker"
    if label == "likely_reliable" or verdict == "likely_reliable":
        return "likely_reliable_not_error"
    if label == "remote_duplicate":
        if confidence < 0.82:
            return "duplicate_confidence_too_low"
        if me_coverage < 0.75:
            return "partial_duplicate_overlap"
        if local_support > 25 and local_only_ratio < 0.95:
            return "local_support_conflicts_with_drop"
        if unique_tokens:
            return "unique_me_content_present"
        if text_similarity < 0.75 or token_containment < 0.75:
            return "duplicate_text_evidence_too_weak"
        if remote_leak > 0:
            return "duplicate_mixed_with_remote_leak_signal"
        return "remote_duplicate_missing_safe_gate"
    if label == "remote_leak":
        if confidence < 0.78:
            return "remote_leak_confidence_too_low"
        if remote_duplicate > 0 or asr_noise > 0:
            return "remote_leak_has_competing_error_signal"
        if local_support < 15:
            return "remote_leak_local_support_too_weak"
        if duration > 5.0:
            return "remote_leak_too_long_for_agent_keep"
        if remote_coverage > 0.15:
            return "remote_overlap_too_large"
        if remote_similarity > 70:
            return "remote_similarity_too_high"
        if len(unique_tokens) < 2:
            return "remote_leak_unique_local_text_too_weak"
        if local_only_ratio < 0.85 or remote_active_ratio > 0.15:
            return "speaker_state_not_local_only_enough"
        return "remote_leak_missing_safe_keep_gate"
    if label == "asr_noise":
        if confidence < 0.78:
            return "asr_noise_confidence_too_low"
        if duration > 1.0:
            return "asr_noise_too_long_for_agent_drop"
        if local_support > 15:
            return "asr_noise_has_local_support"
        return "asr_noise_missing_safe_drop_gate"
    if label == "uncertain":
        if asr_noise > 0 and (duration > 2.2 or remote_active_ratio < 0.95):
            return "uncertain_noise_state_or_duration_not_safe"
        if local_support < 40:
            return "uncertain_local_support_too_weak"
        if remote_duplicate > 0 or remote_leak > 0 or asr_noise > 0:
            return "uncertain_has_competing_error_signal"
        return "uncertain_missing_safe_keep_gate"
    return "not_safe_for_agent_resolution"


def decision_reason(
    row: dict[str, Any],
    queue_row: dict[str, Any] | None,
    session: Path,
    profile: str | None = None,
) -> tuple[str | None, str, dict[str, Any]]:
    classification = row.get("classification") if isinstance(row.get("classification"), dict) else {}
    features = row.get("features") if isinstance(row.get("features"), dict) else {}
    text_features = features.get("text") if isinstance(features.get("text"), dict) else {}
    scores = row.get("scores") if isinstance(row.get("scores"), dict) else {}
    interval = row.get("interval") if isinstance(row.get("interval"), dict) else {}

    label = str(classification.get("label") or "")
    verdict = str(classification.get("verdict") or "")
    confidence = safe_float(classification.get("confidence"))
    duration = safe_float(interval.get("duration_sec"))
    local_support = safe_float(scores.get("local_support"))
    remote_duplicate = safe_float(scores.get("remote_duplicate"))
    remote_leak = safe_float(scores.get("remote_leak"))
    remote_similarity = safe_float(scores.get("remote_similarity"))
    asr_noise = safe_float(scores.get("asr_noise"))
    likely_reliable = safe_float(scores.get("likely_reliable"))
    similarity = safe_float(text_features.get("similarity"))
    containment = safe_float(text_features.get("containment"))
    me_text = audio_review_me_text(row)
    remote_text = audio_review_remote_text(row)
    remote_ids = sorted(audio_review_remote_ids(row))
    unique_tokens = sorted(unique_me_content_tokens(row))
    protected = has_protected_marker(me_text)
    me_coverage = interval_coverage(row, "me")
    remote_coverage = interval_coverage(row, "remote")
    judge_label = str((queue_row or {}).get("judge_label") or "")
    judge_confidence = safe_float((queue_row or {}).get("judge_confidence"))
    stronger = (queue_row or {}).get("stronger_audio_judge") if isinstance((queue_row or {}).get("stronger_audio_judge"), dict) else {}
    stronger_classification = stronger.get("classification") if isinstance(stronger.get("classification"), dict) else {}
    stronger_label = str(stronger_classification.get("label") or "")
    stronger_confidence = safe_float(stronger_classification.get("confidence"))
    stronger_suggested = str(stronger_classification.get("suggested_decision") or "")
    stronger_reason = str(stronger_classification.get("reason") or "")
    state = speaker_state_ratios(session, safe_float(interval.get("start")), safe_float(interval.get("end")))
    adjacent_context = adjacent_me_context(session, profile, audio_review_me_ids(row)) if profile else None

    evidence = {
        "label": label,
        "verdict": verdict,
        "confidence": confidence,
        "duration_sec": duration,
        "local_support": local_support,
        "remote_duplicate": remote_duplicate,
        "remote_leak": remote_leak,
        "remote_similarity": remote_similarity,
        "asr_noise": asr_noise,
        "likely_reliable": likely_reliable,
        "text_similarity": similarity,
        "token_containment": containment,
        "me_overlap_coverage": round(me_coverage, 6),
        "remote_overlap_coverage": round(remote_coverage, 6),
        "unique_me_content_tokens": unique_tokens,
        "protected_marker": protected,
        "remote_utterance_ids": remote_ids,
        "speaker_state": state,
        "adjacent_me_context": adjacent_context,
        "audio_judge_label": judge_label or None,
        "audio_judge_confidence": judge_confidence if judge_label else None,
        "stronger_audio_judge": {
            "id": stronger.get("id"),
            "label": stronger_label,
            "confidence": stronger_confidence,
            "suggested_decision": stronger_suggested,
            "reason": stronger_reason,
        }
        if stronger_label
        else None,
    }

    if (
        stronger_label in {"confirm_me", "confirm_timing_or_doubletalk"}
        and stronger_suggested == "keep_me"
        and stronger_confidence >= 0.88
        and label == "remote_leak"
        and verdict == "probable_transcript_error"
        and confidence >= 0.78
        and local_support >= 25
        and remote_duplicate <= 0
        and asr_noise <= 0
        and not protected
    ):
        return "keep_me", "stronger_audio_judge_confirmed_local_keep", evidence

    if (
        stronger_label in {"confirm_remote_duplicate", "confirm_asr_noise"}
        and stronger_suggested == "drop_me"
        and stronger_confidence >= 0.95
        and verdict == "probable_transcript_error"
        and label in {"remote_duplicate", "remote_leak", "asr_noise"}
        and confidence >= 0.78
        and local_support <= 45
        and me_coverage >= 0.50
        and not unique_tokens
        and not protected
    ):
        return "drop_me", "stronger_audio_judge_confirmed_duplicate_or_noise_drop", evidence

    if (
        label == "asr_noise"
        and verdict == "probable_transcript_error"
        and confidence >= 0.78
        and local_support <= 15
        and duration <= 1.0
        and len(content_tokens(me_text)) <= 3
        and not protected
    ):
        return "drop_me", "safe_short_asr_noise", evidence

    if (
        label == "remote_duplicate"
        and verdict == "probable_transcript_error"
        and confidence >= 0.82
        and local_support <= 25
        and remote_leak <= 0
        and similarity >= 0.75
        and containment >= 0.75
        and set(unique_tokens) <= {"типа"}
        and not protected
    ):
        return "drop_me", "safe_remote_duplicate_or_asr_noise", evidence

    if (
        label == "remote_duplicate"
        and verdict == "probable_transcript_error"
        and confidence >= 0.96
        and local_support <= 60
        and remote_duplicate >= 95
        and remote_leak <= 0
        and similarity >= 0.95
        and containment >= 0.95
        and me_coverage >= 0.90
        and not unique_tokens
        and not protected
    ):
        return "drop_me", "safe_exact_remote_duplicate_full_coverage", evidence

    if (
        label == "uncertain"
        and verdict == "needs_stronger_audio_judge"
        and asr_noise >= 75
        and local_support <= 0
        and remote_duplicate <= 0
        and remote_leak <= 0
        and duration <= 2.2
        and len(content_tokens(me_text)) <= 2
        and len(str(me_text).strip()) <= 16
        and state["covered_ratio"] >= 0.90
        and state["remote_active_ratio"] >= 0.95
        and state["local_only_ratio"] <= 0.05
        and not protected
    ):
        return "drop_me", "safe_remote_active_short_asr_noise", evidence

    if (
        label == "asr_noise"
        and verdict == "probable_transcript_error"
        and confidence >= 0.78
        and local_support >= 40
        and 1.0 <= duration <= 2.5
        and len(unique_tokens) >= 1
        and state["covered_ratio"] >= 0.90
        and state["local_only_ratio"] >= 0.95
        and state["remote_active_ratio"] <= 0.05
        and not protected
    ):
        return "keep_me", "speaker_state_local_only_asr_noise_keep", evidence

    if (
        label == "remote_duplicate"
        and verdict == "probable_transcript_error"
        and confidence >= 0.82
        and local_support >= 25
        and remote_duplicate >= 80
        and remote_leak <= 0
        and asr_noise <= 0
        and duration <= 1.20
        and 0.25 <= me_coverage <= 0.70
        and remote_coverage <= 0.08
        and len(unique_tokens) >= 1
        and not protected
        and similarity >= 0.70
        and containment >= 0.70
        and state["covered_ratio"] >= 0.90
        and state["local_only_ratio"] >= 0.95
        and state["remote_active_ratio"] <= 0.05
    ):
        return "keep_me", "speaker_state_pure_local_partial_duplicate_keep", evidence

    if (
        label == "remote_duplicate"
        and verdict == "probable_transcript_error"
        and confidence >= 0.82
        and local_support >= 50
        and remote_duplicate >= 80
        and remote_leak <= 0
        and asr_noise <= 0
        and remote_similarity <= 60
        and duration <= 1.20
        and me_coverage >= 0.90
        and remote_coverage <= 0.08
        and len(unique_tokens) >= 1
        and not protected
        and similarity >= 0.70
        and containment >= 0.70
        and state["covered_ratio"] >= 0.90
        and state["local_only_ratio"] >= 0.95
        and state["remote_active_ratio"] <= 0.05
    ):
        return "keep_me", "speaker_state_pure_local_short_duplicate_keep", evidence

    if (
        judge_label == "drop_error"
        and judge_confidence >= 0.90
        and label == "remote_duplicate"
        and local_support <= 25
        and remote_leak <= 0
        and set(unique_tokens) <= {"типа"}
        and not protected
    ):
        return "drop_me", "audio_judge_high_confidence_drop", evidence

    if (
        label in {"remote_duplicate", "remote_leak"}
        and verdict == "probable_transcript_error"
        and confidence >= 0.78
        and local_support >= 25
        and asr_noise <= 0
        and duration >= 1.50
        and duration <= 4.05
        and me_coverage >= 0.25
        and remote_coverage <= 0.60
        and len(unique_tokens) >= 1
        and not protected
        and isinstance(adjacent_context, dict)
        and adjacent_context.get("prev_role") == "me"
        and -0.05 <= safe_float(adjacent_context.get("prev_gap_sec")) <= 0.20
        and safe_float(adjacent_context.get("duration_sec")) <= 4.05
    ):
        return "keep_me", "adjacent_me_continuation_keep", evidence

    if (
        label == "uncertain"
        and verdict == "needs_stronger_audio_judge"
        and local_support >= 40
        and remote_duplicate <= 0
        and remote_leak <= 0
        and asr_noise <= 0
    ):
        return "keep_me", "strong_local_support_without_error_signal", evidence

    if (
        label == "uncertain"
        and verdict == "needs_stronger_audio_judge"
        and likely_reliable >= 75
        and local_support >= 40
        and remote_duplicate <= 0
        and asr_noise <= 0
    ):
        return "keep_me", "likely_reliable_with_local_support", evidence

    if (
        label == "uncertain"
        and verdict == "needs_stronger_audio_judge"
        and remote_duplicate <= 0
        and remote_leak <= 0
        and asr_noise <= 0
        and remote_similarity <= 35
        and me_coverage >= 0.90
        and remote_coverage <= 0.05
        and not remote_ids
        and len(unique_tokens) >= 1
        and state["covered_ratio"] >= 0.90
        and state["local_only_ratio"] >= 0.85
        and state["remote_active_ratio"] <= 0.15
        and (not protected or state["local_only_ratio"] >= 0.95)
    ):
        return "keep_me", "speaker_state_local_only_uncertain_keep", evidence

    if judge_label == "keep" and judge_confidence >= 0.90:
        return "keep_me", "audio_judge_high_confidence_keep", evidence

    if (
        label == "remote_leak"
        and verdict == "probable_transcript_error"
        and confidence >= 0.78
        and local_support >= 40
        and remote_similarity <= 45
        and remote_duplicate <= 0
        and asr_noise <= 0
        and similarity <= 0.25
        and containment <= 0.25
        and duration <= 4.5
        and len(unique_tokens) >= 3
        and not protected
    ):
        return "keep_me", "bounded_remote_leak_with_local_content", evidence

    if (
        label == "remote_leak"
        and verdict == "probable_transcript_error"
        and confidence >= 0.78
        and local_support >= 40
        and remote_duplicate <= 0
        and asr_noise <= 0
        and duration <= 5.0
        and me_coverage >= 0.75
        and not remote_ids
        and not remote_text.strip()
        and len(unique_tokens) >= 2
        and state["covered_ratio"] >= 0.80
        and state["local_only_ratio"] >= 0.85
        and state["remote_active_ratio"] <= 0.15
        and (not protected or state["local_only_ratio"] >= 0.95)
    ):
        return "keep_me", "speaker_state_local_only_remote_leak_keep", evidence

    if (
        label == "remote_leak"
        and verdict == "probable_transcript_error"
        and confidence >= 0.78
        and local_support >= 15
        and remote_duplicate <= 0
        and asr_noise <= 0
        and remote_similarity <= 70
        and duration <= 2.5
        and me_coverage >= 0.75
        and remote_coverage <= 0.10
        and len(unique_tokens) >= 3
        and state["covered_ratio"] >= 0.90
        and state["local_only_ratio"] >= 0.85
        and state["remote_active_ratio"] <= 0.15
        and not protected
        and similarity <= 0.40
        and containment <= 0.40
    ):
        return "keep_me", "speaker_state_mostly_local_short_remote_leak_keep", evidence

    if (
        label == "remote_leak"
        and verdict == "probable_transcript_error"
        and confidence >= 0.78
        and local_support >= 40
        and remote_duplicate <= 0
        and asr_noise <= 0
        and duration <= 1.5
        and me_coverage >= 0.75
        and remote_coverage <= 0.12
        and len(tokens(me_text)) <= 4
        and state["covered_ratio"] >= 0.90
        and state["local_only_ratio"] >= 0.85
        and state["remote_active_ratio"] <= 0.12
        and not protected
        and similarity <= 0.55
        and containment <= 0.55
    ):
        return "keep_me", "speaker_state_local_short_backchannel_keep", evidence

    if (
        label == "remote_leak"
        and verdict == "probable_transcript_error"
        and confidence >= 0.78
        and local_support >= 40
        and remote_duplicate <= 0
        and asr_noise <= 0
        and duration <= 1.25
        and me_coverage >= 0.75
        and remote_coverage <= 0.15
        and len(unique_tokens) >= 2
        and state["covered_ratio"] >= 0.90
        and state["local_only_ratio"] >= 0.95
        and state["remote_active_ratio"] <= 0.05
        and (not protected or state["local_only_ratio"] >= 0.98)
        and similarity <= 0.55
        and containment <= 0.55
    ):
        return "keep_me", "speaker_state_pure_local_remote_context_keep", evidence

    if (
        judge_label == "mark_only_error"
        and judge_confidence >= 0.90
        and label == "remote_leak"
        and local_support >= 40
    ):
        return "keep_me", "audio_judge_mark_only_error_no_safe_drop", evidence

    return None, rejection_reason(evidence), evidence


def keep_propagation_reason(
    row: dict[str, Any],
    evidence: dict[str, Any],
    keep_rows: list[dict[str, Any]],
) -> tuple[str | None, dict[str, Any]]:
    if not keep_rows:
        return None, evidence
    label = str(evidence.get("label") or "")
    verdict = str(evidence.get("verdict") or "")
    if label in {"remote_duplicate", "remote_leak"}:
        if verdict != "probable_transcript_error":
            return None, evidence
    elif label == "uncertain":
        if verdict != "needs_stronger_audio_judge":
            return None, evidence
    else:
        return None, evidence
    if safe_float(evidence.get("asr_noise")) > 0:
        return None, evidence
    if label == "remote_leak" and safe_float(evidence.get("remote_duplicate")) > 0:
        return None, evidence
    min_local_support = 15.0 if label == "uncertain" else 25.0
    if safe_float(evidence.get("local_support")) < min_local_support:
        return None, evidence
    if safe_float(evidence.get("me_overlap_coverage")) < 0.25:
        return None, evidence
    if safe_float(evidence.get("duration_sec")) > 8.0:
        return None, evidence
    if not audio_review_me_ids(row):
        return None, evidence

    propagated_from = [
        {
            "source_audit_id": item.get("source_audit_id"),
            "reason": item.get("suggested_decision_reason"),
            "label": item.get("label"),
            "evidence": item.get("review_features"),
        }
        for item in keep_rows
        if item.get("suggested_decision_reason") in KEEP_PROPAGATION_REASONS
    ]
    if not propagated_from:
        return None, evidence

    enriched = {
        **evidence,
        "propagated_from": propagated_from,
    }
    return "same_me_utterance_confirmed_local_keep", enriched


def decision_base(
    row: dict[str, Any],
    session: Path,
    input_profile: str,
    decision: str,
    reason: str,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    classification = row.get("classification") if isinstance(row.get("classification"), dict) else {}
    interval = row.get("interval") if isinstance(row.get("interval"), dict) else {}
    utterance_ids = [str(item) for item in row.get("utterance_ids") or [] if item]
    me_ids = sorted(audio_review_me_ids(row))
    remote_ids = [
        str(item.get("id"))
        for item in row.get("utterances") or []
        if isinstance(item, dict) and role_name(item) == "remote" and item.get("id")
    ]
    return {
        "schema": "murmurmark.review_decision/v1",
        "status": "reviewed",
        "decision": decision,
        "allowed_decisions": ["drop_me", "keep_me", "needs_review", "skip"],
        "session_id": session.name,
        "session": session.as_posix(),
        "input_profile": input_profile,
        "cluster_id": f"agent_{row.get('id')}",
        "source": "audio_review",
        "source_audit_id": str(row.get("id") or ""),
        "label": classification.get("label"),
        "verdict": classification.get("verdict"),
        "confidence": classification.get("confidence"),
        "review_action": "agent_audio_review_resolution",
        "review_lane": "agent_audio_review",
        "suggested_decision": decision,
        "suggested_decision_confidence": "high" if decision == "drop_me" else "medium",
        "suggested_decision_reason": reason,
        "interval": interval,
        "review_features": evidence,
        "me_utterance_ids": me_ids,
        "remote_utterance_ids": remote_ids,
        "utterance_ids": utterance_ids,
        "text": [
            {
                "id": item.get("id"),
                "role": role_name(item),
                "source_track": item.get("source_track"),
                "text": item.get("text"),
            }
            for item in row.get("utterances") or []
            if isinstance(item, dict)
        ],
        "commands": row.get("commands") or row.get("clips") or {},
        "reviewer": "agent:audio_review_rules_v1",
        "notes": reason,
        "agent_review": {
            "schema": SCHEMA,
            "profile": OUTPUT_PROFILE,
            "generator": "build-agent-review-decisions",
            "version": SCRIPT_VERSION,
            "reason": reason,
            "evidence": evidence,
        },
    }


def local_recall_item_index(session: Path) -> dict[str, dict[str, Any]]:
    rows = read_jsonl(session / "derived/audit/local-recall/local_recall_items.jsonl")
    return {str(row.get("item_id") or ""): row for row in rows if row.get("item_id")}


def local_recall_patch_decision(
    patch: dict[str, Any],
    item: dict[str, Any] | None,
    session: Path,
    input_profile: str,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    utterance = patch.get("utterance") if isinstance(patch.get("utterance"), dict) else {}
    micro = patch.get("micro_asr") if isinstance(patch.get("micro_asr"), dict) else {}
    state = item.get("state") if isinstance(item, dict) and isinstance(item.get("state"), dict) else {}
    text = str(utterance.get("text") or micro.get("text") or "").strip()
    utterance_id = str(utterance.get("id") or "")
    duration = max(0.0, safe_float(utterance.get("end")) - safe_float(utterance.get("start")))
    token_probs = [
        safe_float(row.get("token_avg_prob"))
        for row in micro.get("rows") or []
        if isinstance(row, dict) and row.get("token_avg_prob") is not None
    ]
    token_avg_prob = max(token_probs) if token_probs else 0.0
    evidence = {
        "source_item_id": patch.get("source_item_id"),
        "item_confidence": safe_float(item.get("confidence")) if isinstance(item, dict) else 0.0,
        "patch_status": patch.get("status"),
        "micro_status": micro.get("status"),
        "micro_reason": micro.get("reason"),
        "micro_score": safe_float(micro.get("score")),
        "token_avg_prob": token_avg_prob,
        "raw_transcription_count": safe_int(micro.get("raw_transcription_count")),
        "duration_sec": round(duration, 3),
        "local_only_ratio": safe_float(state.get("local_only_ratio")),
        "remote_active_ratio": safe_float(state.get("remote_active_ratio")),
        "double_talk_ratio": safe_float(state.get("double_talk_ratio")),
        "text": text,
    }
    if not utterance_id:
        return None, {**evidence, "reason": "missing_inserted_utterance_id"}
    if patch.get("status") not in {"applied", "already_present"} or micro.get("status") != "ok":
        return None, {**evidence, "reason": "patch_or_micro_asr_not_ok"}
    if not text or has_protected_marker(text):
        return None, {**evidence, "reason": "empty_or_protected_text"}
    if not (0.45 <= duration <= 3.0):
        return None, {**evidence, "reason": "duration_outside_agent_bounds"}
    high_confidence = safe_float(micro.get("score")) >= 0.95 and token_avg_prob >= 0.90
    strong_local_boundary_recovery = (
        safe_float(micro.get("score")) >= 1.0
        and token_avg_prob >= 0.85
        and isinstance(item, dict)
        and safe_float(item.get("confidence")) >= 0.82
    )
    if not (high_confidence or strong_local_boundary_recovery):
        return None, {**evidence, "reason": "micro_asr_confidence_too_low"}
    if safe_float(state.get("local_only_ratio")) < 0.85 or safe_float(state.get("remote_active_ratio")) > 0.05:
        return None, {**evidence, "reason": "state_not_local_only_enough"}

    interval = {
        "start": safe_float(utterance.get("start")),
        "end": safe_float(utterance.get("end")),
        "duration_sec": round(duration, 3),
    }
    decision = {
        "schema": "murmurmark.review_decision/v1",
        "status": "reviewed",
        "decision": "keep_me",
        "allowed_decisions": ["drop_me", "keep_me", "needs_review", "skip"],
        "session_id": session.name,
        "session": session.as_posix(),
        "input_profile": input_profile,
        "cluster_id": f"agent_{patch.get('source_item_id')}",
        "source": "local_recall_repair",
        "source_audit_id": str(patch.get("source_item_id") or ""),
        "label": "local_recall_repair_inserted",
        "verdict": "probable_valid_local_speech",
        "confidence": round(min(0.99, max(token_avg_prob, safe_float(micro.get("score")) / 1.2)), 6),
        "review_action": "agent_local_recall_repair_resolution",
        "review_lane": "agent_local_recall_repair",
        "suggested_decision": "keep_me",
        "suggested_decision_confidence": "high",
        "suggested_decision_reason": "strong_local_only_micro_asr_repair",
        "interval": interval,
        "review_features": evidence,
        "me_utterance_ids": [utterance_id],
        "remote_utterance_ids": [],
        "utterance_ids": [utterance_id],
        "text": [
            {
                "id": utterance_id,
                "role": "me",
                "source_track": "mic",
                "text": text,
            }
        ],
        "commands": {},
        "reviewer": "agent:local_recall_repair_rules_v1",
        "notes": "strong_local_only_micro_asr_repair",
        "agent_review": {
            "schema": SCHEMA,
            "profile": OUTPUT_PROFILE,
            "generator": "build-agent-review-decisions",
            "version": SCRIPT_VERSION,
            "reason": "strong_local_only_micro_asr_repair",
            "evidence": evidence,
        },
    }
    return decision, evidence


def local_recall_repair_rows(session: Path, profile: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if profile != LOCAL_RECALL_REPAIR_PROFILE:
        return [], []
    report = local_recall_repair_report(session)
    if not isinstance(report, dict) or (report.get("gates") or {}).get("passed") is not True:
        return [], []
    repair_dir = session / "derived/transcript-simple/whisper-cpp/local-recall-repair"
    patch_path = repair_dir / f"local_recall_repair_patches.{LOCAL_RECALL_REPAIR_PROFILE}.jsonl"
    items = local_recall_item_index(session)
    decisions: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for patch in read_jsonl(patch_path):
        source_item_id = str(patch.get("source_item_id") or "")
        decision, evidence = local_recall_patch_decision(patch, items.get(source_item_id), session, profile)
        if decision is None:
            rejected.append(
                {
                    "session_id": session.name,
                    "source_audit_id": source_item_id,
                    "reason": evidence.get("reason") or "not_safe_for_agent_local_recall_resolution",
                    "evidence": evidence,
                }
            )
            continue
        decisions.append(decision)
    return decisions, rejected


def template_row(decision: dict[str, Any]) -> dict[str, Any]:
    row = dict(decision)
    row["status"] = "todo"
    row["decision"] = "todo"
    row["reviewer"] = ""
    row["notes"] = ""
    return row


def normalized_words(text: Any) -> list[str]:
    return [token.lower() for token in TOKEN_RE.findall(str(text or ""))]


def is_short_remote_backchannel(text: Any) -> bool:
    words = normalized_words(text)
    return words in (["спасибо"], ["спасибо", "тебе"])


def transcript_order_decision(
    item: dict[str, Any],
    session: Path,
    input_profile: str,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    features = item.get("features") if isinstance(item.get("features"), dict) else {}
    utterances = item.get("utterances") if isinstance(item.get("utterances"), dict) else {}
    me = utterances.get("me") if isinstance(utterances.get("me"), dict) else {}
    remote = utterances.get("remote") if isinstance(utterances.get("remote"), dict) else {}
    interval = item.get("interval") if isinstance(item.get("interval"), dict) else {}
    remote_text = str(remote.get("text") or "")
    evidence = {
        "item_id": item.get("item_id"),
        "label": item.get("label"),
        "confidence": safe_float(item.get("confidence")),
        "reason": item.get("reason"),
        "me_duration_sec": safe_float(features.get("me_duration_sec")),
        "remote_duration_sec": safe_float(features.get("remote_duration_sec")),
        "overlap_duration_sec": safe_float(features.get("overlap_duration_sec")),
        "pre_remote_lead_sec": safe_float(features.get("pre_remote_lead_sec")),
        "post_remote_tail_sec": safe_float(features.get("post_remote_tail_sec")),
        "remote_inside_me": bool(features.get("remote_inside_me")),
        "me_wraps_remote": bool(features.get("me_wraps_remote")),
        "text_similarity": safe_float(features.get("text_similarity")),
        "remote_text_contained_in_me": safe_float(features.get("remote_text_contained_in_me")),
        "remote_text": remote_text,
        "me_text": me.get("text"),
    }
    if item.get("label") != "probable_order_risk":
        return None, {**evidence, "reason": "not_probable_order_risk"}
    if not is_short_remote_backchannel(remote_text):
        return None, {**evidence, "reason": "remote_not_supported_short_backchannel"}
    if not (evidence["remote_inside_me"] and evidence["me_wraps_remote"]):
        return None, {**evidence, "reason": "remote_not_wrapped_by_me"}
    if evidence["me_duration_sec"] < 8.0 or evidence["remote_duration_sec"] > 1.8 or evidence["overlap_duration_sec"] > 1.8:
        return None, {**evidence, "reason": "duration_outside_short_backchannel_bounds"}
    if evidence["pre_remote_lead_sec"] < 1.0 or evidence["post_remote_tail_sec"] < 2.0:
        return None, {**evidence, "reason": "not_enough_context_around_backchannel"}
    if evidence["text_similarity"] > 0.15 or evidence["remote_text_contained_in_me"] > 0.0:
        return None, {**evidence, "reason": "text_overlap_too_high_for_backchannel_clear"}
    me_id = str(me.get("id") or "")
    remote_id = str(remote.get("id") or "")
    if not me_id or not remote_id:
        return None, {**evidence, "reason": "missing_order_utterance_ids"}

    decision = {
        "schema": "murmurmark.review_decision/v1",
        "status": "reviewed",
        "decision": "keep_me",
        "allowed_decisions": ["keep_me", "needs_review", "skip"],
        "session_id": session.name,
        "session": session.as_posix(),
        "input_profile": input_profile,
        "cluster_id": f"agent_{item.get('item_id')}",
        "source": "transcript_order",
        "source_audit_id": str(item.get("item_id") or ""),
        "label": item.get("label"),
        "verdict": "needs_transcript_order_review",
        "confidence": evidence["confidence"],
        "review_action": "agent_transcript_order_resolution",
        "review_lane": "agent_transcript_order",
        "suggested_decision": "keep_me",
        "suggested_decision_confidence": "medium",
        "suggested_decision_reason": "short_remote_backchannel_inside_long_me_keep",
        "interval": interval,
        "review_features": evidence,
        "me_utterance_ids": [me_id],
        "remote_utterance_ids": [remote_id],
        "utterance_ids": [me_id, remote_id],
        "text": [
            {"id": me_id, "role": "Me", "source_track": "mic", "text": me.get("text")},
            {"id": remote_id, "role": "Colleagues", "source_track": "remote", "text": remote.get("text")},
        ],
        "commands": item.get("commands") or {},
        "reviewer": "agent:transcript_order_rules_v1",
        "notes": "short_remote_backchannel_inside_long_me_keep",
        "agent_review": {
            "schema": SCHEMA,
            "profile": OUTPUT_PROFILE,
            "generator": "build-agent-review-decisions",
            "version": SCRIPT_VERSION,
            "reason": "short_remote_backchannel_inside_long_me_keep",
            "evidence": evidence,
        },
    }
    return decision, evidence


def transcript_order_rows(session: Path, profile: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    path = session / "derived/audit/order/transcript_order_items.jsonl"
    if not path.exists():
        return [], []
    decisions: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for item in read_jsonl(path):
        decision, evidence = transcript_order_decision(item, session, profile)
        if decision is None:
            rejected.append(
                {
                    "session_id": session.name,
                    "source_audit_id": item.get("item_id"),
                    "reason": evidence.get("reason") or "not_safe_for_agent_transcript_order_resolution",
                    "evidence": evidence,
                }
            )
            continue
        decisions.append(decision)
    return decisions, rejected


def session_rows(session: Path, profile: str, queue: dict[tuple[str, str], dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    audit_path = session / "derived/audit/audio-review-pack/audio_review_audit.jsonl"
    if not audit_path.exists():
        return [], []
    selected_ids = selected_me_ids(session, profile)
    stronger_by_source_id = stronger_judge_index(session)
    candidates: list[dict[str, Any]] = []
    rejected_candidates: list[tuple[dict[str, Any], str, dict[str, Any]]] = []
    rejected: list[dict[str, Any]] = []
    for row in read_jsonl(audit_path):
        if not is_active_audio_review_row(row, selected_ids):
            continue
        queue_row = dict(queue.get((session.name, str(row.get("id") or ""))) or {})
        stronger = stronger_by_source_id.get(str(row.get("id") or ""))
        if stronger:
            queue_row["stronger_audio_judge"] = stronger
        decision, reason, evidence = decision_reason(row, queue_row, session, profile)
        if decision is None:
            rejected_candidates.append((row, reason, evidence))
            continue
        candidates.append(decision_base(row, session, profile, decision, reason, evidence))

    keep_rows_by_me_id: dict[str, list[dict[str, Any]]] = {}
    for row in candidates:
        if row.get("decision") != "keep_me" or row.get("suggested_decision_reason") not in KEEP_PROPAGATION_REASONS:
            continue
        for utterance_id in row.get("me_utterance_ids") or []:
            keep_rows_by_me_id.setdefault(str(utterance_id), []).append(row)

    candidate_source_ids = {str(row.get("source_audit_id")) for row in candidates}
    for row, reason, evidence in rejected_candidates:
        source_id = str(row.get("id") or "")
        keep_rows: list[dict[str, Any]] = []
        for utterance_id in audio_review_me_ids(row):
            keep_rows.extend(keep_rows_by_me_id.get(str(utterance_id), []))
        propagation_reason, propagated_evidence = keep_propagation_reason(row, evidence, keep_rows)
        if propagation_reason and source_id not in candidate_source_ids:
            candidates.append(decision_base(row, session, profile, "keep_me", propagation_reason, propagated_evidence))
            candidate_source_ids.add(source_id)
            continue
        rejected.append(
            {
                "session_id": session.name,
                "source_audit_id": row.get("id"),
                "reason": reason,
                "evidence": evidence,
            }
        )

    by_me_id: dict[str, list[dict[str, Any]]] = {}
    for row in candidates:
        for utterance_id in row.get("me_utterance_ids") or []:
            by_me_id.setdefault(str(utterance_id), []).append(row)

    suppressed_ids: set[str] = set()
    for utterance_id, rows in by_me_id.items():
        decisions = {str(row.get("decision")) for row in rows}
        if "drop_me" in decisions and len(decisions) > 1:
            for row in rows:
                if row.get("decision") == "drop_me":
                    suppressed_ids.add(str(row.get("source_audit_id")))
                    rejected.append(
                        {
                            "session_id": session.name,
                            "source_audit_id": row.get("source_audit_id"),
                            "utterance_id": utterance_id,
                            "reason": "drop_suppressed_due_to_keep_conflict",
                            "evidence": row.get("review_features"),
                        }
                    )

    decisions = [row for row in candidates if str(row.get("source_audit_id")) not in suppressed_ids]
    local_recall_decisions, local_recall_rejected = local_recall_repair_rows(session, profile)
    decisions.extend(local_recall_decisions)
    rejected.extend(local_recall_rejected)
    order_decisions, order_rejected = transcript_order_rows(session, profile)
    decisions.extend(order_decisions)
    rejected.extend(order_rejected)
    decisions.sort(key=lambda item: (str(item.get("session_id")), safe_float((item.get("interval") or {}).get("start")), str(item.get("source_audit_id"))))
    return decisions, rejected


def main() -> int:
    args = parse_args()
    session_quality = read_json(args.session_quality)
    sessions = session_quality.get("sessions")
    if not isinstance(sessions, list):
        raise SystemExit("session quality report does not contain sessions[]")

    queue = queue_index(args.audio_judge_queue)
    decisions: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for row in sessions:
        if not isinstance(row, dict):
            continue
        session_path = Path(str(row.get("session") or ""))
        profile = base_profile_for_agent(session_path, str(row.get("selected_profile") or ""))
        if not session_path.exists() or not profile:
            continue
        session_decisions, session_rejected = session_rows(session_path, profile, queue)
        decisions.extend(session_decisions)
        rejected.extend(session_rejected)

    templates = [template_row(row) for row in decisions]
    write_jsonl(args.out, decisions)
    write_jsonl(args.template_out, templates)

    by_decision = Counter(str(row.get("decision")) for row in decisions)
    by_reason = Counter(str(row.get("suggested_decision_reason")) for row in decisions)
    by_session = Counter(str(row.get("session_id")) for row in decisions)
    rejected_by_reason = Counter(str(row.get("reason") or "unknown") for row in rejected)
    rejected_by_label = Counter(
        str(((row.get("evidence") if isinstance(row.get("evidence"), dict) else {}) or {}).get("label") or "unknown")
        for row in rejected
    )
    rejected_by_verdict = Counter(
        str(((row.get("evidence") if isinstance(row.get("evidence"), dict) else {}) or {}).get("verdict") or "unknown")
        for row in rejected
    )
    rejected_by_reason_and_label = Counter(
        (
            str(row.get("reason") or "unknown"),
            str(((row.get("evidence") if isinstance(row.get("evidence"), dict) else {}) or {}).get("label") or "unknown"),
        )
        for row in rejected
    )
    report = {
        "schema": SCHEMA,
        "generator": {"name": "build-agent-review-decisions", "version": SCRIPT_VERSION},
        "profile": OUTPUT_PROFILE,
        "inputs": {
            "session_quality": args.session_quality.as_posix(),
            "audio_judge_queue": args.audio_judge_queue.as_posix(),
        },
        "outputs": {
            "decisions": args.out.as_posix(),
            "template": args.template_out.as_posix(),
        },
        "summary": {
            "decision_rows": len(decisions),
            "template_rows": len(templates),
            "rejected_candidate_rows": len(rejected),
            "by_decision": dict(sorted(by_decision.items())),
            "by_reason": dict(sorted(by_reason.items())),
            "by_session": dict(sorted(by_session.items())),
            "rejected_by_reason": dict(sorted(rejected_by_reason.items())),
            "rejected_by_label": dict(sorted(rejected_by_label.items())),
            "rejected_by_verdict": dict(sorted(rejected_by_verdict.items())),
            "rejected_by_reason_and_label": {
                f"{reason}|{label}": count
                for (reason, label), count in sorted(rejected_by_reason_and_label.items())
            },
            "top_rejected_reasons": [
                {"reason": reason, "count": count}
                for reason, count in rejected_by_reason.most_common(10)
            ],
        },
        "rejected_examples": rejected[:50],
    }
    write_json(args.report, report)
    print(f"agent_decisions: {args.out}")
    print(f"agent_template: {args.template_out}")
    print(f"decision_rows: {len(decisions)}")
    print(f"by_decision: {dict(sorted(by_decision.items()))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
