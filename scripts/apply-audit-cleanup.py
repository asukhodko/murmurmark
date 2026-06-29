#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
import re
import shlex
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


SCRIPT_VERSION = "0.3.0"
INPUT_PROFILE_DEFAULT = "shadow_v2"
OUTPUT_PROFILE_DEFAULT = "audit_cleanup_v1"
TOKEN_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё_./+-]+")
SEGMENT_REPAIR_PROFILES = {"audit_cleanup_v7"}

STOP_WORDS = {
    "а",
    "бы",
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
    "ну",
    "о",
    "об",
    "он",
    "она",
    "они",
    "по",
    "просто",
    "с",
    "со",
    "там",
    "типа",
    "то",
    "тут",
    "ты",
    "у",
    "это",
    "этот",
    "эта",
    "эти",
    "что",
    "чтобы",
    "я",
}
FILLER_WORDS = {
    "ага",
    "алло",
    "да",
    "ладно",
    "ну",
    "ок",
    "окей",
    "понял",
    "сейчас",
    "так",
    "угу",
    "хм",
    "это",
}
DOMAIN_TERMS = {
    "api",
    "backend",
    "ci",
    "deploy",
    "git",
    "gitlab",
    "github",
    "kubernetes",
    "mcp",
    "merge",
    "mr",
    "openapi",
    "pipeline",
    "slo",
    "админка",
    "агент",
    "бэкенд",
    "деплой",
    "дока",
    "квота",
    "квоты",
    "лог",
    "логи",
    "миграция",
    "пайплайн",
    "прод",
    "сервис",
    "стейдж",
    "троттлинг",
}
ACTION_DECISION_RISK_MARKERS = (
    "надо",
    "нужно",
    "давай",
    "давайте",
    "решили",
    "договорились",
    "согласовали",
    "принимаем",
    "берем",
    "берём",
    "риск",
    "проблема",
    "блокер",
    "сломается",
    "непонятно",
    "вопрос",
)
AGREEMENT_PREFIXES = (
    "да",
    "да,",
    "ну да",
    "угу",
    "ага",
    "окей",
    "согласен",
    "согласна",
    "точно",
    "вот именно",
)
WEAK_NOISE_PHRASES = {
    "вот",
    "хм",
    "да",
    "угу",
    "ага",
    "спасибо",
    "сейчас",
    "ну",
    "окей",
}
HARMFUL_LABELS = {"probable_duplicate", "probable_remote_leak", "probable_asr_noise"}
BENIGN_LABELS = {"probable_double_talk", "probable_timing_overlap", "double_talk", "timing_overlap"}
AUDIO_REVIEW_DROP_LABELS = {"remote_duplicate", "asr_noise"}
AUDIO_REVIEW_MARK_LABELS = {"remote_leak", "lost_me", "uncertain", "double_talk", "timing_overlap"}
AUDIO_JUDGE_DROP_LABELS = {"drop_error"}
AUDIO_JUDGE_MARK_LABELS = {"mark_only_error"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply conservative transcript cleanup from group overlap audit.")
    parser.add_argument("session", type=Path)
    parser.add_argument("--input-profile", default=INPUT_PROFILE_DEFAULT)
    parser.add_argument("--output-profile", default=OUTPUT_PROFILE_DEFAULT)
    parser.add_argument("--mode", choices=("dry-run", "conservative"), default="conservative")
    parser.add_argument(
        "--audio-judge-queue",
        type=Path,
        default=Path("sessions/_reports/audio-judge-v0/audio_judge_v0_queue_predictions.jsonl"),
        help="Optional cross-session audio-judge queue predictions used by audit_cleanup_v3/v4.",
    )
    return parser.parse_args()


def suffix(profile: str) -> str:
    return "" if profile == "current" else f".{profile}"


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"expected JSON object: {path}")
    return data


def read_jsonl(path: Path) -> list[dict[str, Any]]:
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


def rel(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(resolved)


def shell_path(path: Path) -> str:
    return shlex.quote(display_path(path))


def cleanup_handoff(session: Path, output_profile: str, report_path: Path, transcript_path: Path) -> dict[str, Any]:
    session_arg = shell_path(session)
    synthesize_command = f"murmurmark synthesize {session_arg} --transcript-profile {shlex.quote(output_profile)}"
    report_command = f"less {shell_path(report_path)}"
    transcript_command = f"less {shell_path(transcript_path)}"
    return {
        "recommended_next": synthesize_command,
        "next_commands": [
            {
                "id": "synthesize_cleanup_profile",
                "command": synthesize_command,
                "reason": "build quality verdict and notes from the cleanup profile",
            },
            {
                "id": "refresh_session_report",
                "command": f"murmurmark report {session_arg}",
                "reason": "refresh readiness after cleanup-derived synthesis",
            },
        ],
        "open_commands": [
            {
                "id": "open_audit_cleanup_report",
                "command": report_command,
                "path": display_path(report_path),
            },
            {
                "id": "open_cleanup_transcript",
                "command": transcript_command,
                "path": display_path(transcript_path),
            },
        ],
    }


def tokens(text: Any) -> list[str]:
    return [token.lower().replace("ё", "е") for token in TOKEN_RE.findall(str(text or ""))]


def content_tokens(text: Any) -> list[str]:
    return [token for token in tokens(text) if token not in STOP_WORDS and token not in FILLER_WORDS and len(token) > 2]


def token_spans(text: Any) -> list[dict[str, Any]]:
    value = str(text or "")
    spans: list[dict[str, Any]] = []
    for match in TOKEN_RE.finditer(value):
        raw = match.group(0)
        norm = raw.lower().replace("ё", "е")
        spans.append(
            {
                "raw": raw,
                "norm": norm,
                "start": match.start(),
                "end": match.end(),
                "is_content": norm not in STOP_WORDS and norm not in FILLER_WORDS and len(norm) > 2,
            }
        )
    return spans


def domain_terms(text: Any) -> list[str]:
    return sorted({token for token in tokens(text) if token in DOMAIN_TERMS})


def normalize_text(text: Any) -> str:
    return " ".join(tokens(text))


def has_marker(text: Any) -> bool:
    lowered = str(text or "").lower().replace("ё", "е")
    return any(marker in lowered for marker in ACTION_DECISION_RISK_MARKERS)


def starts_with_agreement(text: Any) -> bool:
    lowered = normalize_text(text)
    return any(lowered == prefix or lowered.startswith(prefix + " ") for prefix in AGREEMENT_PREFIXES)


def format_time(seconds: float | int | None) -> str:
    if seconds is None:
        return "??:??"
    total = max(0, int(float(seconds)))
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def role_name(row: dict[str, Any]) -> str:
    source = str(row.get("source_track") or "").lower()
    label = str(row.get("speaker_label") or row.get("role") or "").lower()
    if source == "mic" or label == "me":
        return "Me"
    if source == "remote" or "colleague" in label or label == "remote":
        return "Colleagues"
    return str(row.get("speaker_label") or row.get("role") or "Unknown")


def text_similarity(left: Any, right: Any) -> float:
    left_norm = normalize_text(left)
    right_norm = normalize_text(right)
    if not left_norm and not right_norm:
        return 1.0
    if not left_norm or not right_norm:
        return 0.0
    left_tokens = set(content_tokens(left_norm))
    right_tokens = set(content_tokens(right_norm))
    containment = 0.0
    if left_tokens and right_tokens:
        containment = len(left_tokens & right_tokens) / max(1, min(len(left_tokens), len(right_tokens)))
    sequence = SequenceMatcher(None, left_norm, right_norm).ratio()
    return round(max(containment, sequence), 6)


def build_overlaps(utterances: list[dict[str, Any]]) -> list[dict[str, Any]]:
    overlaps: list[dict[str, Any]] = []
    rows = sorted(utterances, key=lambda row: (float(row.get("start", 0.0) or 0.0), float(row.get("end", 0.0) or 0.0)))
    for left_index, left in enumerate(rows):
        left_start = float(left.get("start", 0.0) or 0.0)
        left_end = float(left.get("end", left_start) or left_start)
        if left_end <= left_start:
            continue
        for right in rows[left_index + 1 :]:
            right_start = float(right.get("start", 0.0) or 0.0)
            if right_start >= left_end:
                break
            right_end = float(right.get("end", right_start) or right_start)
            overlap_start = max(left_start, right_start)
            overlap_end = min(left_end, right_end)
            duration = overlap_end - overlap_start
            if duration <= 0:
                continue
            left_role = role_name(left)
            right_role = role_name(right)
            if {left_role, right_role} != {"Me", "Colleagues"}:
                continue
            overlaps.append(
                {
                    "left_utterance_id": str(left.get("id")),
                    "right_utterance_id": str(right.get("id")),
                    "left_role": left_role,
                    "right_role": right_role,
                    "start": round(overlap_start, 3),
                    "end": round(overlap_end, 3),
                    "duration_sec": round(duration, 3),
                    "type": "audit_cleanup_overlap",
                    "text_similarity": text_similarity(left.get("text"), right.get("text")),
                }
            )
    return overlaps


def union_seconds(intervals: list[tuple[float, float]]) -> float:
    normalized = sorted((start, end) for start, end in intervals if end > start)
    if not normalized:
        return 0.0
    total = 0.0
    current_start, current_end = normalized[0]
    for start, end in normalized[1:]:
        if start <= current_end:
            current_end = max(current_end, end)
            continue
        total += current_end - current_start
        current_start, current_end = start, end
    total += current_end - current_start
    return round(total, 3)


def selected_note_ids(session: Path) -> set[str]:
    path = session / "derived" / "synthesis-simple" / "extractive" / "evidence_notes.json"
    if not path.exists():
        return set()
    try:
        evidence = read_json(path)
    except Exception:
        return set()
    selected = evidence.get("selected")
    if not isinstance(selected, dict):
        return set()
    ids: set[str] = set()
    for value in selected.values():
        if not isinstance(value, list):
            continue
        for item in value:
            if not isinstance(item, dict):
                continue
            for utterance_id in item.get("evidence_utterance_ids", []) or []:
                ids.add(str(utterance_id))
            for block_item in item.get("representatives", []) or []:
                if isinstance(block_item, dict) and block_item.get("utterance_id"):
                    ids.add(str(block_item["utterance_id"]))
    return ids


def audit_by_me(records: list[dict[str, Any]], existing_ids: set[str] | None = None) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        me = ((record.get("utterances") or {}).get("me") or {})
        utterance_id = str(me.get("id") or "")
        if not utterance_id:
            continue
        if existing_ids is not None and utterance_id not in existing_ids:
            continue
        record.setdefault("source", "group_overlap")
        grouped.setdefault(utterance_id, []).append(record)
    return grouped


def audio_review_role(row: dict[str, Any]) -> str:
    role = str(row.get("role") or row.get("speaker_label") or "").lower()
    source = str(row.get("source_track") or "").lower()
    if role == "me" or source == "mic":
        return "me"
    if "colleague" in role or source == "remote":
        return "remote"
    return role or source


def audio_review_summary_to_utterance(row: dict[str, Any], fallback_start: float, fallback_end: float) -> dict[str, Any]:
    role = audio_review_role(row)
    return {
        "id": str(row.get("id") or ""),
        "role": "Me" if role == "me" else "Colleagues" if role == "remote" else row.get("role", "Unknown"),
        "speaker_label": "Me" if role == "me" else "Colleagues" if role == "remote" else row.get("role", "Unknown"),
        "source_track": "mic" if role == "me" else "remote" if role == "remote" else row.get("source_track"),
        "start": float(row.get("start", fallback_start) or fallback_start),
        "end": float(row.get("end", fallback_end) or fallback_end),
        "text": str(row.get("text") or ""),
        "quality": {"needs_review": bool(row.get("needs_review"))},
    }


def normalize_audio_review_record(row: dict[str, Any], me_row: dict[str, Any], remote_row: dict[str, Any] | None) -> dict[str, Any]:
    interval = row.get("interval") if isinstance(row.get("interval"), dict) else {}
    start = float(interval.get("start", me_row.get("start", 0.0)) or 0.0)
    end = float(interval.get("end", me_row.get("end", start)) or start)
    duration_sec = max(0.0, end - start)
    features = row.get("features") if isinstance(row.get("features"), dict) else {}
    text_features = features.get("text") if isinstance(features.get("text"), dict) else {}
    scores = row.get("scores") if isinstance(row.get("scores"), dict) else {}
    classification = row.get("classification") if isinstance(row.get("classification"), dict) else {}
    me_duration = max(0.001, float(me_row.get("end", end) or end) - float(me_row.get("start", start) or start))
    coverage = min(1.0, duration_sec / me_duration)
    remote = remote_row or {"id": "", "start": start, "end": end, "text": "", "quality": {}}
    label = str(classification.get("label") or "")
    top_score = scores.get(label, classification.get("top_score", 0))
    return {
        "id": str(row.get("id") or ""),
        "source": "audio_review",
        "utterances": {"me": me_row, "remote": remote},
        "classification": {
            "label": label,
            "verdict": classification.get("verdict"),
            "confidence": classification.get("confidence", 0.0),
            "top_score": top_score,
            "second_score": classification.get("second_score", 0),
            "action_suggestion": classification.get("label"),
        },
        "scores": {
            "local_evidence": scores.get("local_support", 0),
            "audio_review_local_support": scores.get("local_support", 0),
            "audio_review_remote_similarity": scores.get("remote_similarity", 0),
            "audio_review_remote_duplicate": scores.get("remote_duplicate", 0),
            "audio_review_remote_leak": scores.get("remote_leak", 0),
            "audio_review_asr_noise": scores.get("asr_noise", 0),
            "audio_review_lost_me": scores.get("lost_me", 0),
            "text_duplicate": max(float(scores.get("remote_duplicate", 0) or 0), float(text_features.get("similarity", 0.0) or 0.0) * 100.0),
        },
        "features": {
            "text": {
                "similarity_max": text_features.get("similarity", 0.0),
                "token_containment": text_features.get("containment", 0.0),
                "sequence_ratio": text_features.get("sequence_ratio", 0.0),
                "me_text": text_features.get("me_text", me_row.get("text")),
                "remote_text": text_features.get("remote_text", remote.get("text")),
            },
            "speaker_state": {},
            "interval": {
                "me_coverage": round(coverage, 6),
                "time_overlap_ratio": round(coverage, 6),
                "near_boundary": True,
            },
            "audio_review": features,
        },
        "audio_review": row,
    }


def audio_review_by_me(records: list[dict[str, Any]], existing_ids: set[str]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in records:
        classification = row.get("classification") if isinstance(row.get("classification"), dict) else {}
        label = str(classification.get("label") or "")
        if label not in AUDIO_REVIEW_DROP_LABELS and label not in AUDIO_REVIEW_MARK_LABELS:
            continue
        interval = row.get("interval") if isinstance(row.get("interval"), dict) else {}
        start = float(interval.get("start", 0.0) or 0.0)
        end = float(interval.get("end", start) or start)
        summaries = row.get("utterances") if isinstance(row.get("utterances"), list) else []
        me_rows = [audio_review_summary_to_utterance(item, start, end) for item in summaries if isinstance(item, dict) and audio_review_role(item) == "me"]
        remote_rows = [audio_review_summary_to_utterance(item, start, end) for item in summaries if isinstance(item, dict) and audio_review_role(item) == "remote"]
        remote = remote_rows[0] if remote_rows else None
        for me in me_rows:
            utterance_id = str(me.get("id") or "")
            if not utterance_id or utterance_id not in existing_ids:
                continue
            grouped.setdefault(utterance_id, []).append(normalize_audio_review_record(row, me, remote))
    return grouped


def audio_review_record_me_ids(row: dict[str, Any]) -> set[str]:
    summaries = row.get("utterances") if isinstance(row.get("utterances"), list) else []
    ids: set[str] = set()
    for item in summaries:
        if not isinstance(item, dict):
            continue
        if audio_review_role(item) == "me" and item.get("id"):
            ids.add(str(item.get("id")))
    return ids


def active_audio_review_seconds(records: list[dict[str, Any]], utterances: list[dict[str, Any]]) -> float:
    selected_me_ids = {str(row.get("id")) for row in utterances if role_name(row) == "Me"}
    intervals: list[tuple[float, float]] = []
    for row in records:
        classification = row.get("classification") if isinstance(row.get("classification"), dict) else {}
        label = str(classification.get("label") or "")
        verdict = str(classification.get("verdict") or "")
        if verdict not in {"probable_transcript_error", "needs_stronger_audio_judge"}:
            continue
        me_ids = audio_review_record_me_ids(row)
        if label != "lost_me" and me_ids and not (me_ids & selected_me_ids):
            continue
        interval = row.get("interval") if isinstance(row.get("interval"), dict) else {}
        start = float(interval.get("start", 0.0) or 0.0)
        end = float(interval.get("end", start) or start)
        if end <= start:
            duration_sec = float(interval.get("duration_sec", 0.0) or 0.0)
            end = start + max(0.0, duration_sec)
        if end > start:
            intervals.append((start, end))
    return union_seconds(intervals)


def audio_judge_predictions_by_audit_id(predictions: list[dict[str, Any]], session_name: str) -> dict[str, dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for row in predictions:
        if str(row.get("session_id") or "") != session_name:
            continue
        audit_id = str(row.get("source_audit_id") or "")
        judge_label = str(row.get("judge_label") or "")
        if not audit_id or judge_label not in AUDIO_JUDGE_DROP_LABELS | AUDIO_JUDGE_MARK_LABELS:
            continue
        by_id[audit_id] = row
    return by_id


def audio_judge_by_me(
    audio_review_records: list[dict[str, Any]],
    predictions: list[dict[str, Any]],
    existing_ids: set[str],
    session_name: str,
) -> dict[str, list[dict[str, Any]]]:
    predictions_by_audit = audio_judge_predictions_by_audit_id(predictions, session_name)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in audio_review_records:
        prediction = predictions_by_audit.get(str(row.get("id") or ""))
        if not prediction:
            continue
        classification = row.get("classification") if isinstance(row.get("classification"), dict) else {}
        label = str(classification.get("label") or "")
        if label not in AUDIO_REVIEW_DROP_LABELS and label not in AUDIO_REVIEW_MARK_LABELS:
            continue
        interval = row.get("interval") if isinstance(row.get("interval"), dict) else {}
        start = float(interval.get("start", 0.0) or 0.0)
        end = float(interval.get("end", start) or start)
        summaries = row.get("utterances") if isinstance(row.get("utterances"), list) else []
        me_rows = [audio_review_summary_to_utterance(item, start, end) for item in summaries if isinstance(item, dict) and audio_review_role(item) == "me"]
        remote_rows = [audio_review_summary_to_utterance(item, start, end) for item in summaries if isinstance(item, dict) and audio_review_role(item) == "remote"]
        remote = remote_rows[0] if remote_rows else None
        for me in me_rows:
            utterance_id = str(me.get("id") or "")
            if not utterance_id or utterance_id not in existing_ids:
                continue
            normalized = normalize_audio_review_record(row, me, remote)
            normalized["source"] = "audio_judge"
            normalized["audio_judge_prediction"] = prediction
            normalized.setdefault("classification", {})["top_score"] = round(float(prediction.get("judge_confidence", 0.0) or 0.0) * 100.0, 3)
            grouped.setdefault(utterance_id, []).append(normalized)
    return grouped


def unique_me_tokens(record: dict[str, Any]) -> set[str]:
    utterances = record.get("utterances") or {}
    me = utterances.get("me") or {}
    remote = utterances.get("remote") or {}
    return set(content_tokens(me.get("text"))) - set(content_tokens(remote.get("text")))


def marker_is_protected(record: dict[str, Any], unique_count: int, notes_impact: bool) -> bool:
    me = ((record.get("utterances") or {}).get("me") or {})
    if not has_marker(me.get("text")):
        return False
    confidence = float((record.get("classification") or {}).get("confidence", 0.0) or 0.0)
    if confidence >= 0.95 and unique_count == 0:
        return False
    if confidence >= 0.95 and unique_count <= 2 and not notes_impact:
        return False
    return True


def intentional_repeat(record: dict[str, Any], unique_count: int) -> bool:
    utterances = record.get("utterances") or {}
    me = utterances.get("me") or {}
    remote = utterances.get("remote") or {}
    state = ((record.get("features") or {}).get("speaker_state") or {})
    interval = ((record.get("features") or {}).get("interval") or {})
    me_start = float(me.get("start", 0.0) or 0.0)
    remote_end = float(remote.get("end", 0.0) or 0.0)
    reply_shaped = -0.2 <= me_start - remote_end <= 2.5
    strong_local = (
        float((record.get("scores") or {}).get("local_evidence", 0.0) or 0.0) >= 45.0
        or float(state.get("local_score_max", 0.0) or 0.0) >= 0.65
        or float(state.get("local_only_ratio", 0.0) or 0.0) >= 0.25
    )
    boundary = bool(interval.get("near_boundary"))
    if starts_with_agreement(me.get("text")) and unique_count >= 3:
        return True
    if unique_count >= 3 and reply_shaped and strong_local:
        return True
    if unique_count >= 3 and boundary and strong_local:
        return True
    return False


def duplicate_gate(record: dict[str, Any], notes_ids: set[str]) -> tuple[bool, dict[str, Any]]:
    classification = record.get("classification") or {}
    scores = record.get("scores") or {}
    features = record.get("features") or {}
    text = features.get("text") or {}
    state = features.get("speaker_state") or {}
    interval = features.get("interval") or {}
    me = ((record.get("utterances") or {}).get("me") or {})
    unique_tokens = unique_me_tokens(record)
    unique_count = len(unique_tokens)
    notes_impact = str(me.get("id")) in notes_ids
    confidence = float(classification.get("confidence", 0.0) or 0.0)
    text_duplicate = float(scores.get("text_duplicate", 0.0) or 0.0)
    local_evidence = float(scores.get("local_evidence", 0.0) or 0.0)
    similarity = float(text.get("similarity_max", 0.0) or 0.0)
    containment = float(text.get("token_containment", 0.0) or 0.0)
    coverage = float(interval.get("me_coverage", interval.get("time_overlap_ratio", 0.0)) or 0.0)
    local_only = float(state.get("local_only_ratio", 0.0) or 0.0)
    double_talk = float(state.get("double_talk_ratio", 0.0) or 0.0)
    local_score_mean = float(state.get("local_score_mean", 0.0) or 0.0)
    local_score_max = float(state.get("local_score_max", 0.0) or 0.0)
    protected_marker = marker_is_protected(record, unique_count, notes_impact)
    protected_repeat = intentional_repeat(record, unique_count)
    text_ok = (
        text_duplicate >= 85.0
        or (similarity >= 0.78 and containment >= 0.75)
        or (confidence >= 0.95 and text_duplicate >= 95.0)
    )
    local_ok = (
        (local_evidence <= 45.0 and local_only < 0.70 and double_talk < 0.75)
        or (confidence >= 0.95 and text_duplicate >= 95.0 and unique_count <= 2)
    )
    coverage_ok = coverage >= 0.80 or (confidence >= 0.95 and text_duplicate >= 95.0 and coverage >= 0.50)
    safe = (
        classification.get("label") == "probable_duplicate"
        and confidence >= 0.88
        and text_ok
        and local_ok
        and coverage_ok
        and unique_count <= 2
        and not protected_marker
        and not protected_repeat
        and not (notes_impact and not (confidence >= 0.95 and unique_count == 0))
    )
    checks = {
        "label": classification.get("label"),
        "classification_confidence": confidence,
        "text_duplicate_score": text_duplicate,
        "text_similarity_max": similarity,
        "token_containment": containment,
        "local_evidence_score": local_evidence,
        "local_score_mean": local_score_mean,
        "local_score_max": local_score_max,
        "local_only_ratio": local_only,
        "double_talk_ratio": double_talk,
        "me_utterance_overlap_coverage": coverage,
        "unique_me_content_token_count": unique_count,
        "unique_me_content_tokens": sorted(unique_tokens),
        "notes_impact": notes_impact,
        "has_protected_action_decision_risk_marker": protected_marker,
        "intentional_repeat_candidate": protected_repeat,
        "text_gate_passed": text_ok,
        "local_gate_passed": local_ok,
        "coverage_gate_passed": coverage_ok,
        "safe_to_drop_entire_utterance": safe,
    }
    return safe, checks


def noise_gate(record: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    classification = record.get("classification") or {}
    scores = record.get("scores") or {}
    features = record.get("features") or {}
    state = features.get("speaker_state") or {}
    me = ((record.get("utterances") or {}).get("me") or {})
    text = str(me.get("text") or "")
    phrase = normalize_text(text)
    duration = float((record.get("interval") or {}).get("duration_sec", 0.0) or 0.0)
    confidence = float(classification.get("confidence", 0.0) or 0.0)
    local_evidence = float(scores.get("local_evidence", 0.0) or 0.0)
    local_only = float(state.get("local_only_ratio", 0.0) or 0.0)
    content_count = len(content_tokens(text))
    domain_count = len(domain_terms(text))
    weak_phrase = phrase in WEAK_NOISE_PHRASES
    safe = (
        classification.get("label") == "probable_asr_noise"
        and confidence >= 0.85
        and duration <= 2.5
        and content_count <= 3
        and domain_count == 0
        and not has_marker(text)
        and local_evidence <= 25.0
        and local_only < 0.20
    )
    checks = {
        "label": classification.get("label"),
        "classification_confidence": confidence,
        "duration_sec": duration,
        "content_token_count": content_count,
        "domain_term_count": domain_count,
        "local_evidence_score": local_evidence,
        "local_only_ratio": local_only,
        "weak_noise_phrase": weak_phrase,
        "has_action_decision_risk_marker": has_marker(text),
        "safe_to_drop_entire_utterance": safe,
    }
    return safe, checks


def audio_review_gate(record: dict[str, Any], notes_ids: set[str]) -> tuple[bool, dict[str, Any], str]:
    classification = record.get("classification") or {}
    scores = record.get("scores") or {}
    features = record.get("features") or {}
    text_features = features.get("text") or {}
    interval = features.get("interval") or {}
    me = ((record.get("utterances") or {}).get("me") or {})
    label = str(classification.get("label") or "")
    verdict = str(classification.get("verdict") or "")
    confidence = float(classification.get("confidence", 0.0) or 0.0)
    local_support = float(scores.get("audio_review_local_support", scores.get("local_evidence", 0.0)) or 0.0)
    duplicate_score = float(scores.get("audio_review_remote_duplicate", 0.0) or 0.0)
    noise_score = float(scores.get("audio_review_asr_noise", 0.0) or 0.0)
    similarity = float(text_features.get("similarity_max", 0.0) or 0.0)
    containment = float(text_features.get("token_containment", 0.0) or 0.0)
    coverage = float(interval.get("me_coverage", interval.get("time_overlap_ratio", 0.0)) or 0.0)
    unique_tokens = unique_me_tokens(record)
    unique_count = len(unique_tokens)
    notes_impact = str(me.get("id")) in notes_ids
    protected_marker = marker_is_protected(record, unique_count, notes_impact)
    protected_repeat = intentional_repeat(record, unique_count)
    duration_sec = float((record.get("audio_review") or {}).get("interval", {}).get("duration_sec", 0.0) or 0.0)
    if duration_sec <= 0.0:
        duration_sec = float(me.get("end", 0.0) or 0.0) - float(me.get("start", 0.0) or 0.0)
    content_count = len(content_tokens(me.get("text")))
    domain_count = len(domain_terms(me.get("text")))

    common_ok = (
        verdict == "probable_transcript_error"
        and confidence >= 0.90
        and not protected_marker
        and not protected_repeat
        and not (notes_impact and not (confidence >= 0.96 and unique_count == 0))
    )
    duplicate_safe = (
        label == "remote_duplicate"
        and common_ok
        and duplicate_score >= 85.0
        and local_support <= 55.0
        and (similarity >= 0.72 or containment >= 0.75)
        and coverage >= 0.50
        and unique_count <= 2
    )
    noise_safe = (
        label == "asr_noise"
        and common_ok
        and noise_score >= 85.0
        and local_support <= 45.0
        and duration_sec <= 2.5
        and content_count <= 3
        and domain_count == 0
        and not has_marker(me.get("text"))
    )
    action = "drop_me_duplicate" if label == "remote_duplicate" else "drop_me_noise" if label == "asr_noise" else "mark_audio_review"
    checks = {
        "source": "audio_review",
        "label": label,
        "audio_review_verdict": verdict,
        "classification_confidence": confidence,
        "audio_review_duplicate_score": duplicate_score,
        "audio_review_noise_score": noise_score,
        "audio_review_local_support": local_support,
        "text_similarity_max": similarity,
        "token_containment": containment,
        "me_utterance_overlap_coverage": coverage,
        "duration_sec": round(max(0.0, duration_sec), 3),
        "content_token_count": content_count,
        "domain_term_count": domain_count,
        "unique_me_content_token_count": unique_count,
        "unique_me_content_tokens": sorted(unique_tokens),
        "notes_impact": notes_impact,
        "has_protected_action_decision_risk_marker": protected_marker,
        "intentional_repeat_candidate": protected_repeat,
        "common_gate_passed": common_ok,
        "safe_to_drop_entire_utterance": duplicate_safe or noise_safe,
    }
    return duplicate_safe or noise_safe, checks, action


def segment_remote_duplicate_gate(record: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    classification = record.get("classification") or {}
    scores = record.get("scores") or {}
    features = record.get("features") or {}
    text_features = features.get("text") or {}
    label = str(classification.get("label") or "")
    verdict = str(classification.get("verdict") or "")
    confidence = float(classification.get("confidence", 0.0) or 0.0)
    local_support = float(scores.get("audio_review_local_support", scores.get("local_evidence", 0.0)) or 0.0)
    duplicate_score = float(scores.get("audio_review_remote_duplicate", 0.0) or 0.0)
    remote_similarity = float(scores.get("audio_review_remote_similarity", 0.0) or 0.0)
    remote_leak = float(scores.get("audio_review_remote_leak", 0.0) or 0.0)
    similarity = float(text_features.get("similarity_max", 0.0) or 0.0)
    containment = float(text_features.get("token_containment", 0.0) or 0.0)
    safe = (
        record.get("source") == "audio_review"
        and label == "remote_duplicate"
        and verdict == "probable_transcript_error"
        and confidence >= 0.82
        and duplicate_score >= 82.0
        and remote_similarity >= 90.0
        and remote_leak <= 5.0
        and local_support <= 60.0
        and (similarity >= 0.75 or containment >= 0.60)
    )
    return safe, {
        "source": record.get("source"),
        "label": label,
        "audio_review_verdict": verdict,
        "classification_confidence": confidence,
        "audio_review_duplicate_score": duplicate_score,
        "audio_review_remote_similarity": remote_similarity,
        "audio_review_remote_leak": remote_leak,
        "audio_review_local_support": local_support,
        "text_similarity_max": similarity,
        "token_containment": containment,
        "safe_for_segment_repair": safe,
    }


def remote_text_for_segment(record: dict[str, Any]) -> str:
    text_features = ((record.get("features") or {}).get("text") or {})
    value = str(text_features.get("remote_text") or "").strip()
    if value:
        return value
    return str((((record.get("utterances") or {}).get("remote") or {}).get("text")) or "").strip()


def matching_token_blocks(me_spans: list[dict[str, Any]], remote_text: str) -> tuple[set[int], list[dict[str, Any]]]:
    remote_spans = token_spans(remote_text)
    if not me_spans or not remote_spans:
        return set(), []
    matcher = SequenceMatcher(
        None,
        [row["norm"] for row in me_spans],
        [row["norm"] for row in remote_spans],
        autojunk=False,
    )
    remove_indexes: set[int] = set()
    blocks: list[dict[str, Any]] = []
    for match in matcher.get_matching_blocks():
        if match.size <= 0:
            continue
        me_slice = me_spans[match.a : match.a + match.size]
        remote_slice = remote_spans[match.b : match.b + match.size]
        content_count = sum(1 for row in me_slice if row["is_content"])
        remote_content_count = sum(1 for row in remote_slice if row["is_content"])
        if match.size < 4 and content_count < 2:
            continue
        if remote_content_count < 2 and match.size < 5:
            continue
        indexes = list(range(match.a, match.a + match.size))
        remove_indexes.update(indexes)
        blocks.append(
            {
                "me_token_start": match.a,
                "me_token_end_exclusive": match.a + match.size,
                "remote_token_start": match.b,
                "remote_token_end_exclusive": match.b + match.size,
                "token_count": match.size,
                "content_token_count": content_count,
                "text": " ".join(row["raw"] for row in me_slice),
            }
        )
    return remove_indexes, blocks


def contiguous_ranges(indexes: list[int]) -> list[tuple[int, int]]:
    if not indexes:
        return []
    ranges: list[tuple[int, int]] = []
    start = indexes[0]
    previous = indexes[0]
    for index in indexes[1:]:
        if index == previous + 1:
            previous = index
            continue
        ranges.append((start, previous + 1))
        start = previous = index
    ranges.append((start, previous + 1))
    return ranges


def segment_patch_payload(
    index: int,
    *,
    action: str,
    input_profile: str,
    output_profile: str,
    target: dict[str, Any],
    records: list[dict[str, Any]],
    checks: dict[str, Any],
    kept_segments: list[dict[str, Any]],
    removed_blocks: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema": "murmurmark.audit_cleanup_patch/v1",
        "patch_id": f"patch_{index:06d}",
        "action": action,
        "status": "applied",
        "reason": "segment_remote_duplicate_gate_passed",
        "input_profile": input_profile,
        "output_profile": output_profile,
        "target": {
            "utterance_id": str(target.get("id")),
            "role": "Me",
            "start": target.get("start"),
            "end": target.get("end"),
            "text": target.get("text"),
        },
        "matched_remote": [
            {
                "utterance_id": str(((record.get("utterances") or {}).get("remote") or {}).get("id") or ""),
                "start": ((record.get("utterances") or {}).get("remote") or {}).get("start"),
                "end": ((record.get("utterances") or {}).get("remote") or {}).get("end"),
                "text": remote_text_for_segment(record),
            }
            for record in records
        ],
        "audit_overlap_ids": [record.get("id") for record in records],
        "evidence": {
            "source": "audio_review_segment",
            "label": "remote_duplicate",
            "records": [
                {
                    "id": record.get("id"),
                    "classification": record.get("classification"),
                    "scores": record.get("scores"),
                    "text": ((record.get("features") or {}).get("text") or {}),
                    "interval": ((record.get("features") or {}).get("interval") or {}),
                }
                for record in records
            ],
            "segment_repair": {
                "removed_blocks": removed_blocks,
                "kept_segments": kept_segments,
            },
        },
        "safety_checks": checks,
    }


def repaired_segment_rows(row: dict[str, Any], records: list[dict[str, Any]], output_profile: str) -> tuple[list[dict[str, Any]], dict[str, Any]] | None:
    me_text = str(row.get("text") or "")
    me_spans = token_spans(me_text)
    if len(me_spans) < 4:
        return None
    safe_records: list[dict[str, Any]] = []
    gate_checks: list[dict[str, Any]] = []
    remove_indexes: set[int] = set()
    removed_blocks: list[dict[str, Any]] = []
    for record in records:
        safe, checks = segment_remote_duplicate_gate(record)
        gate_checks.append({"record_id": record.get("id"), **checks})
        if not safe:
            continue
        indexes, blocks = matching_token_blocks(me_spans, remote_text_for_segment(record))
        if not indexes:
            gate_checks[-1]["matching_blocks_found"] = False
            continue
        gate_checks[-1]["matching_blocks_found"] = True
        safe_records.append(record)
        remove_indexes.update(indexes)
        for block in blocks:
            removed_blocks.append({"record_id": record.get("id"), **block})
    if not safe_records or len(remove_indexes) < 3:
        return None

    keep_indexes = [index for index in range(len(me_spans)) if index not in remove_indexes]
    keep_ranges = contiguous_ranges(keep_indexes)
    start = float(row.get("start", 0.0) or 0.0)
    end = float(row.get("end", start) or start)
    total_duration = max(0.001, end - start)
    kept_segments: list[dict[str, Any]] = []
    output_rows: list[dict[str, Any]] = []
    for segment_index, (left, right) in enumerate(keep_ranges, start=1):
        segment_text = " ".join(me_text[me_spans[left]["start"] : me_spans[right - 1]["end"]].split()).strip(" ,.;:!?")
        if not segment_text:
            continue
        segment_content_count = len(content_tokens(segment_text))
        if segment_content_count < 2 and not has_marker(segment_text) and not domain_terms(segment_text):
            continue
        segment_start = start + total_duration * (left / max(1, len(me_spans)))
        segment_end = start + total_duration * (right / max(1, len(me_spans)))
        new_row = copy.deepcopy(row)
        new_row["id"] = f"{row.get('id')}_seg{segment_index:02d}"
        new_row["start"] = round(segment_start, 3)
        new_row["end"] = round(max(segment_start + 0.2, segment_end), 3)
        new_row["text"] = segment_text
        quality = new_row.setdefault("quality", {})
        if not isinstance(quality, dict):
            quality = {}
            new_row["quality"] = quality
        quality["needs_review"] = False
        quality.pop("overlap_type", None)
        quality["audit_cleanup"] = {
            "profile": output_profile,
            "actions": ["segment_remove_remote_duplicate"],
            "source_utterance_id": str(row.get("id")),
            "overlap_ids": [str(record.get("id") or "") for record in safe_records],
            "labels": ["remote_duplicate"],
        }
        output_rows.append(new_row)
        kept_segments.append(
            {
                "id": new_row["id"],
                "start": new_row["start"],
                "end": new_row["end"],
                "text": segment_text,
                "content_token_count": segment_content_count,
                "token_start": left,
                "token_end_exclusive": right,
            }
        )

    removed_token_count = len(remove_indexes)
    removed_ratio = removed_token_count / max(1, len(me_spans))
    if not output_rows and removed_ratio < 0.75:
        return None
    checks = {
        "safe_for_segment_repair": True,
        "gate_checks": gate_checks,
        "source_token_count": len(me_spans),
        "removed_token_count": removed_token_count,
        "removed_token_ratio": round(removed_ratio, 6),
        "kept_segment_count": len(output_rows),
    }
    return output_rows, {
        "records": safe_records,
        "checks": checks,
        "kept_segments": kept_segments,
        "removed_blocks": removed_blocks,
        "action": "drop_me_after_segment_remote_duplicate_repair" if not output_rows else "segment_remove_remote_duplicate",
    }


def audio_judge_gate(record: dict[str, Any], notes_ids: set[str], output_profile: str) -> tuple[bool, dict[str, Any], str]:
    prediction = record.get("audio_judge_prediction") if isinstance(record.get("audio_judge_prediction"), dict) else {}
    review_safe, review_checks, review_action = audio_review_gate(record, notes_ids)
    classification = record.get("classification") or {}
    scores = record.get("scores") or {}
    features = record.get("features") or {}
    text_features = features.get("text") or {}
    interval = features.get("interval") or {}
    me = ((record.get("utterances") or {}).get("me") or {})
    label = str(classification.get("label") or "")
    verdict = str(classification.get("verdict") or "")
    judge_label = str(prediction.get("judge_label") or "")
    judge_confidence = float(prediction.get("judge_confidence", 0.0) or 0.0)
    local_support = float(scores.get("audio_review_local_support", scores.get("local_evidence", 0.0)) or 0.0)
    duplicate_score = float(scores.get("audio_review_remote_duplicate", 0.0) or 0.0)
    noise_score = float(scores.get("audio_review_asr_noise", 0.0) or 0.0)
    similarity = float(text_features.get("similarity_max", 0.0) or 0.0)
    containment = float(text_features.get("token_containment", 0.0) or 0.0)
    coverage = float(interval.get("me_coverage", interval.get("time_overlap_ratio", 0.0)) or 0.0)
    unique_tokens = unique_me_tokens(record)
    unique_count = len(unique_tokens)
    notes_impact = str(me.get("id")) in notes_ids
    protected_marker = marker_is_protected(record, unique_count, notes_impact)
    protected_repeat = intentional_repeat(record, unique_count)
    duration_sec = float((record.get("audio_review") or {}).get("interval", {}).get("duration_sec", 0.0) or 0.0)
    if duration_sec <= 0.0:
        duration_sec = float(me.get("end", 0.0) or 0.0) - float(me.get("start", 0.0) or 0.0)
    content_count = len(content_tokens(me.get("text")))
    domain_count = len(domain_terms(me.get("text")))

    common_ok = (
        verdict == "probable_transcript_error"
        and judge_label == "drop_error"
        and judge_confidence >= 0.98
        and not protected_marker
        and not protected_repeat
        and not (notes_impact and not (judge_confidence >= 0.995 and unique_count == 0))
    )
    judge_duplicate_safe = (
        label == "remote_duplicate"
        and common_ok
        and duplicate_score >= 75.0
        and local_support <= 60.0
        and (similarity >= 0.60 or containment >= 0.65 or duplicate_score >= 90.0)
        and coverage >= 0.45
        and unique_count <= 2
    )
    judge_noise_safe = (
        label == "asr_noise"
        and common_ok
        and noise_score >= 75.0
        and local_support <= 45.0
        and duration_sec <= 2.5
        and content_count <= 3
        and domain_count == 0
        and not has_marker(me.get("text"))
    )
    expanded_common_ok = (
        output_profile == "audit_cleanup_v4"
        and verdict == "probable_transcript_error"
        and judge_label == "drop_error"
        and judge_confidence >= 0.93
        and not protected_marker
        and not protected_repeat
        and not notes_impact
    )
    expanded_duplicate_safe = (
        label == "remote_duplicate"
        and expanded_common_ok
        and duplicate_score >= 82.0
        and local_support <= 55.0
        and similarity >= 0.75
        and containment >= 0.75
        and coverage >= 0.60
        and unique_count <= 2
    )
    safe = review_safe or judge_duplicate_safe or judge_noise_safe or expanded_duplicate_safe
    action = "drop_me_duplicate" if label == "remote_duplicate" else "drop_me_noise" if label == "asr_noise" else "mark_audio_judge"
    checks = {
        **review_checks,
        "source": "audio_judge",
        "audio_judge_label": judge_label,
        "audio_judge_confidence": judge_confidence,
        "audio_judge_shadow_action": prediction.get("shadow_action"),
        "audio_judge_common_gate_passed": common_ok,
        "audio_judge_duplicate_gate_passed": judge_duplicate_safe,
        "audio_judge_noise_gate_passed": judge_noise_safe,
        "audio_judge_expanded_profile": output_profile == "audit_cleanup_v4",
        "audio_judge_expanded_common_gate_passed": expanded_common_ok,
        "audio_judge_expanded_duplicate_gate_passed": expanded_duplicate_safe,
        "audio_review_gate_passed": review_safe,
        "safe_to_drop_entire_utterance": safe,
    }
    if judge_label == "mark_only_error":
        action = "mark_audio_judge"
        checks["safe_to_drop_entire_utterance"] = False
        return False, checks, action
    return safe, checks, action


def best_patch_for_utterance(
    utterance_id: str,
    rows: list[dict[str, Any]],
    notes_ids: set[str],
    patch_index: int,
    output_profile: str,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    candidates: list[tuple[int, dict[str, Any], dict[str, Any], str]] = []
    rejected: list[dict[str, Any]] = []
    for record in rows:
        label = str((record.get("classification") or {}).get("label") or "")
        if record.get("source") == "audio_judge":
            safe, checks, action = audio_judge_gate(record, notes_ids, output_profile)
        elif record.get("source") == "audio_review":
            safe, checks, action = audio_review_gate(record, notes_ids)
        elif label == "probable_duplicate":
            safe, checks = duplicate_gate(record, notes_ids)
            action = "drop_me_duplicate"
        elif label == "probable_asr_noise":
            safe, checks = noise_gate(record)
            action = "drop_me_noise"
        else:
            safe = False
            checks = {"label": label, "safe_to_drop_entire_utterance": False}
            action = {
                "probable_double_talk": "mark_double_talk",
                "probable_timing_overlap": "mark_timing_overlap",
                "probable_remote_leak": "mark_remote_leak",
                "needs_human_review": "needs_review",
                "remote_leak": "mark_remote_leak",
                "lost_me": "mark_lost_me",
                "uncertain": "needs_audio_judge",
                "double_talk": "mark_double_talk",
                "timing_overlap": "mark_timing_overlap",
            }.get(label, "needs_review")
        score = int(float((record.get("classification") or {}).get("top_score", 0) or 0))
        if safe:
            candidates.append((score, record, checks, action))
        else:
            rejected.append(patch_payload(patch_index, "rejected", action, record, checks, reason="gate_not_passed"))
            patch_index += 1
    if not candidates:
        return None, rejected
    candidates.sort(key=lambda item: (item[0], float((item[1].get("interval") or {}).get("duration_sec", 0.0) or 0.0)), reverse=True)
    _, record, checks, action = candidates[0]
    applied = patch_payload(patch_index, "applied", action, record, checks, reason="conservative_gate_passed")
    for _, extra_record, extra_checks, extra_action in candidates[1:]:
        rejected.append(patch_payload(patch_index + 1, "rejected", extra_action, extra_record, extra_checks, reason=f"utterance_already_handled_by:{applied['patch_id']}"))
        patch_index += 1
    return applied, rejected


def patch_payload(
    index: int,
    status: str,
    action: str,
    record: dict[str, Any],
    checks: dict[str, Any],
    *,
    reason: str,
) -> dict[str, Any]:
    utterances = record.get("utterances") or {}
    me = utterances.get("me") or {}
    remote = utterances.get("remote") or {}
    classification = record.get("classification") or {}
    scores = record.get("scores") or {}
    features = record.get("features") or {}
    return {
        "schema": "murmurmark.audit_cleanup_patch/v1",
        "patch_id": f"patch_{index:06d}",
        "action": action,
        "status": status,
        "reason": reason,
        "input_profile": INPUT_PROFILE_DEFAULT,
        "output_profile": OUTPUT_PROFILE_DEFAULT,
        "target": {
            "utterance_id": str(me.get("id")),
            "role": "Me",
            "start": me.get("start"),
            "end": me.get("end"),
            "text": me.get("text"),
        },
        "matched_remote": {
            "utterance_id": str(remote.get("id")),
            "start": remote.get("start"),
            "end": remote.get("end"),
            "text": remote.get("text"),
        },
        "audit_overlap_ids": [record.get("id")],
        "evidence": {
            "source": record.get("source", "group_overlap"),
            "label": classification.get("label"),
            "verdict": classification.get("verdict"),
            "classification_confidence": classification.get("confidence"),
            "scores": scores,
            "text": features.get("text"),
            "speaker_state": features.get("speaker_state"),
            "interval": features.get("interval"),
            "audio_review": record.get("audio_review"),
            "audio_judge": record.get("audio_judge_prediction"),
        },
        "safety_checks": checks,
    }


def mark_quality(utterance: dict[str, Any], record: dict[str, Any], action: str, output_profile: str) -> None:
    quality = utterance.setdefault("quality", {})
    if not isinstance(quality, dict):
        quality = {}
        utterance["quality"] = quality
    audit = quality.setdefault("audit_cleanup", {"profile": output_profile, "labels": [], "overlap_ids": [], "actions": []})
    if not isinstance(audit, dict):
        audit = {"profile": output_profile, "labels": [], "overlap_ids": [], "actions": []}
        quality["audit_cleanup"] = audit
    label = str((record.get("classification") or {}).get("label") or "unknown")
    overlap_id = str(record.get("id") or "")
    if label not in audit.setdefault("labels", []):
        audit["labels"].append(label)
    if overlap_id and overlap_id not in audit.setdefault("overlap_ids", []):
        audit["overlap_ids"].append(overlap_id)
    if action not in audit.setdefault("actions", []):
        audit["actions"].append(action)
    if label in BENIGN_LABELS:
        quality["overlap_type"] = label
    elif label in {"needs_human_review", "uncertain", "lost_me", "remote_duplicate", "asr_noise", "remote_leak"}:
        quality["needs_review"] = True
        quality["overlap_type"] = label
    elif label == "probable_remote_leak":
        quality["overlap_type"] = "probable_remote_leak"


def duration(row: dict[str, Any]) -> float:
    return max(0.0, float(row.get("end", 0.0) or 0.0) - float(row.get("start", 0.0) or 0.0))


def quality_report(
    *,
    input_quality: dict[str, Any],
    utterances: list[dict[str, Any]],
    overlaps: list[dict[str, Any]],
    summary: dict[str, Any],
    applied: list[dict[str, Any]],
    rejected: list[dict[str, Any]],
    output_profile: str,
) -> dict[str, Any]:
    report = copy.deepcopy(input_quality)
    report["schema"] = "murmurmark.simple_transcript_quality/v1"
    report["utterances"] = len(utterances)
    report["needs_review_count"] = sum(1 for row in utterances if isinstance(row.get("quality"), dict) and row["quality"].get("needs_review"))
    report["cross_role_overlap_count"] = len(overlaps)
    report["cross_role_overlap_seconds"] = round(sum(float(row.get("duration_sec", 0.0) or 0.0) for row in overlaps), 3)
    report["cross_role_overlap_gt2_count"] = sum(1 for row in overlaps if float(row.get("duration_sec", 0.0) or 0.0) > 2.0)
    report["cross_role_overlap_gt2_seconds"] = round(sum(float(row.get("duration_sec", 0.0) or 0.0) for row in overlaps if float(row.get("duration_sec", 0.0) or 0.0) > 2.0), 3)
    duplicate_overlaps = [row for row in overlaps if float(row.get("text_similarity", 0.0) or 0.0) >= 0.65]
    report["remote_duplicate_in_me_count"] = len(duplicate_overlaps)
    report["remote_duplicate_in_me_seconds"] = round(sum(float(row.get("duration_sec", 0.0) or 0.0) for row in duplicate_overlaps), 3)
    report["meeting_duration_sec"] = round(max((float(row.get("end", 0.0) or 0.0) for row in utterances), default=0.0), 3)
    harmful_before = float(((summary.get("harmful") or {}).get("seconds", 0.0)) or 0.0)
    benign = float(((summary.get("benign_or_expected") or {}).get("seconds", 0.0)) or 0.0)
    review = float(((summary.get("review") or {}).get("seconds", 0.0)) or 0.0)
    dropped_duplicate = sum(duration(patch["target"]) for patch in applied if patch["action"] == "drop_me_duplicate")
    dropped_noise = sum(duration(patch["target"]) for patch in applied if patch["action"] == "drop_me_noise")
    segment_duplicate = 0.0
    for patch in applied:
        if patch.get("action") not in {"segment_remove_remote_duplicate", "drop_me_after_segment_remote_duplicate_repair"}:
            continue
        target_duration = duration(patch["target"])
        removed_ratio = float((patch.get("safety_checks") or {}).get("removed_token_ratio", 0.0) or 0.0)
        if patch.get("action") == "drop_me_after_segment_remote_duplicate_repair":
            segment_duplicate += target_duration
        else:
            segment_duplicate += target_duration * removed_ratio
    report["audit_cleanup"] = {
        "profile": output_profile,
        "applied_patches": len(applied),
        "rejected_patches": len(rejected),
        "audio_review_applied_patches": sum(1 for patch in applied if patch.get("evidence", {}).get("source") == "audio_review"),
        "audio_review_rejected_patches": sum(1 for patch in rejected if patch.get("evidence", {}).get("source") == "audio_review"),
        "segment_repair_applied_patches": sum(1 for patch in applied if patch.get("evidence", {}).get("source") == "audio_review_segment"),
        "segment_repair_rejected_patches": sum(1 for patch in rejected if patch.get("evidence", {}).get("source") == "audio_review_segment"),
        "audio_judge_applied_patches": sum(1 for patch in applied if patch.get("evidence", {}).get("source") == "audio_judge"),
        "audio_judge_rejected_patches": sum(1 for patch in rejected if patch.get("evidence", {}).get("source") == "audio_judge"),
        "dropped_me_duplicate_seconds": round(dropped_duplicate, 3),
        "dropped_me_noise_seconds": round(dropped_noise, 3),
        "segment_repaired_remote_duplicate_seconds": round(segment_duplicate, 3),
        "audit_harmful_seconds_before": round(harmful_before, 3),
        "audit_harmful_seconds_after": round(max(0.0, harmful_before - dropped_duplicate - dropped_noise - segment_duplicate), 3),
        "audit_benign_seconds": round(benign, 3),
        "audit_review_seconds": round(review, 3),
        "protected_intentional_repeat_count": sum(1 for row in rejected if row.get("safety_checks", {}).get("intentional_repeat_candidate")),
    }
    report.update(report["audit_cleanup"])
    return report


def write_markdown(path: Path, utterances: list[dict[str, Any]], model: str | None, language: str | None) -> None:
    lines = [
        "# Simple Transcript",
        "",
        "Backend: whisper.cpp  ",
        f"Model: `{Path(model).name if model else 'unknown'}`  ",
        f"Language: `{language or 'unknown'}`",
        "",
    ]
    for row in utterances:
        label = role_name(row)
        lines.extend(
            [
                f"## {format_time(row.get('start'))} {label}",
                "",
                str(row.get("text") or "").strip(),
                "",
            ]
        )
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    session = args.session.expanduser().resolve()
    resolved = session / "derived" / "transcript-simple" / "whisper-cpp" / "resolved"
    audit_dir = session / "derived" / "audit" / "group-overlaps"
    audio_review_dir = session / "derived" / "audit" / "audio-review-pack"
    cleanup_dir = session / "derived" / "transcript-simple" / "whisper-cpp" / "audit-cleanup"
    input_suffix = suffix(args.input_profile)
    output_suffix = suffix(args.output_profile)
    paths = {
        "dialogue": resolved / f"clean_dialogue{input_suffix}.json",
        "quality": resolved / f"quality_report{input_suffix}.json",
        "overlaps": resolved / f"overlaps{input_suffix}.json",
        "simple": resolved / f"transcript.simple{input_suffix}.json",
        "report": resolved / f"transcribe_simple_report{input_suffix}.json",
        "audit": audit_dir / "group_overlap_audit.jsonl",
        "audit_summary": audit_dir / "group_overlap_summary.json",
        "audio_review_audit": audio_review_dir / "audio_review_audit.jsonl",
        "audio_review_summary": audio_review_dir / "audio_review_summary.json",
        "audio_judge_queue": args.audio_judge_queue,
    }
    for label, path in paths.items():
        if label in {"simple", "report", "audio_review_audit", "audio_review_summary", "audio_judge_queue"}:
            continue
        if not path.exists():
            raise FileNotFoundError(f"missing {label}: {path}")
    use_audio_review = args.output_profile in {"audit_cleanup_v2", "audit_cleanup_v3", "audit_cleanup_v4", "audit_cleanup_v6"} | SEGMENT_REPAIR_PROFILES
    use_audio_judge = args.output_profile in {"audit_cleanup_v3", "audit_cleanup_v4"}
    if use_audio_review:
        for label in ("audio_review_audit", "audio_review_summary"):
            if not paths[label].exists():
                raise FileNotFoundError(f"missing {label}: {paths[label]}")
    if use_audio_judge and not paths["audio_judge_queue"].exists():
        raise FileNotFoundError(f"missing audio_judge_queue: {paths['audio_judge_queue']}")

    dialogue = read_json(paths["dialogue"])
    input_quality = read_json(paths["quality"])
    audit_records = read_jsonl(paths["audit"])
    audio_review_records = read_jsonl(paths["audio_review_audit"]) if use_audio_review else []
    audio_judge_predictions = read_jsonl(paths["audio_judge_queue"]) if use_audio_judge else []
    audit_summary = read_json(paths["audit_summary"])
    utterances = [row for row in dialogue.get("utterances", []) if isinstance(row, dict)]
    by_id = {str(row.get("id")): row for row in utterances}
    notes_ids = selected_note_ids(session)
    grouped = audit_by_me(audit_records, set(by_id))
    if use_audio_review:
        for utterance_id, records in audio_review_by_me(audio_review_records, set(by_id)).items():
            grouped.setdefault(utterance_id, []).extend(records)
    if use_audio_judge:
        for utterance_id, records in audio_judge_by_me(audio_review_records, audio_judge_predictions, set(by_id), session.name).items():
            grouped.setdefault(utterance_id, []).extend(records)

    applied: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    patch_index = 1
    dropped_ids: set[str] = set()
    replacements: dict[str, list[dict[str, Any]]] = {}

    for utterance_id, records in grouped.items():
        patch, rejected_rows = best_patch_for_utterance(utterance_id, records, notes_ids, patch_index, args.output_profile)
        rejected.extend(rejected_rows)
        patch_index += len(rejected_rows)
        if patch and args.mode == "conservative":
            patch["input_profile"] = args.input_profile
            patch["output_profile"] = args.output_profile
            applied.append(patch)
            patch_index += 1
            dropped_ids.add(utterance_id)
        elif patch:
            patch["status"] = "dry_run"
            patch["input_profile"] = args.input_profile
            patch["output_profile"] = args.output_profile
            rejected.append(patch)
            patch_index += 1

    if args.output_profile in SEGMENT_REPAIR_PROFILES:
        for utterance_id, records in grouped.items():
            if utterance_id in dropped_ids:
                continue
            source_row = by_id.get(utterance_id)
            if not source_row or role_name(source_row) != "Me":
                continue
            repaired = repaired_segment_rows(source_row, records, args.output_profile)
            if not repaired:
                continue
            new_rows, metadata = repaired
            if args.mode != "conservative":
                rejected.append(
                    segment_patch_payload(
                        patch_index,
                        action=metadata["action"],
                        input_profile=args.input_profile,
                        output_profile=args.output_profile,
                        target=source_row,
                        records=metadata["records"],
                        checks=metadata["checks"],
                        kept_segments=metadata["kept_segments"],
                        removed_blocks=metadata["removed_blocks"],
                    )
                )
                rejected[-1]["status"] = "dry_run"
                patch_index += 1
                continue
            applied.append(
                segment_patch_payload(
                    patch_index,
                    action=metadata["action"],
                    input_profile=args.input_profile,
                    output_profile=args.output_profile,
                    target=source_row,
                    records=metadata["records"],
                    checks=metadata["checks"],
                    kept_segments=metadata["kept_segments"],
                    removed_blocks=metadata["removed_blocks"],
                )
            )
            patch_index += 1
            dropped_ids.add(utterance_id)
            replacements[utterance_id] = new_rows

    for patch in rejected:
        patch["input_profile"] = args.input_profile
        patch["output_profile"] = args.output_profile

    output_utterances: list[dict[str, Any]] = []
    for row in utterances:
        row_id = str(row.get("id"))
        if row_id in replacements:
            output_utterances.extend(copy.deepcopy(replacements[row_id]))
            continue
        if row_id in dropped_ids:
            continue
        new_row = copy.deepcopy(row)
        for record in grouped.get(row_id, []):
            label = str((record.get("classification") or {}).get("label") or "")
            action = {
                "probable_double_talk": "kept_mark_double_talk",
                "probable_timing_overlap": "kept_mark_timing_overlap",
                "probable_remote_leak": "kept_mark_remote_leak",
                "needs_human_review": "kept_needs_review",
                "remote_duplicate": "kept_needs_review",
                "asr_noise": "kept_needs_review",
                "remote_leak": "kept_mark_remote_leak",
                "lost_me": "kept_mark_lost_me",
                "uncertain": "kept_needs_audio_judge",
                "double_talk": "kept_mark_double_talk",
                "timing_overlap": "kept_mark_timing_overlap",
            }.get(label)
            if action:
                mark_quality(new_row, record, action, args.output_profile)
        output_utterances.append(new_row)

    output_overlaps = build_overlaps(output_utterances)
    output_quality = quality_report(
        input_quality=input_quality,
        utterances=output_utterances,
        overlaps=output_overlaps,
        summary=audit_summary,
        applied=applied,
        rejected=rejected,
        output_profile=args.output_profile,
    )
    if args.output_profile in SEGMENT_REPAIR_PROFILES and audio_review_records:
        active_review_seconds = active_audio_review_seconds(audio_review_records, output_utterances)
        output_quality["audit_cleanup"]["audit_review_seconds"] = active_review_seconds
        output_quality["audit_review_seconds"] = active_review_seconds
    gates = {
        "passed": (
            int(output_quality.get("unrepaired_long_mic_crossings_count", 0) or 0) == 0
            and int(output_quality.get("golden_phrase_fail_count", 0) or 0) == 0
            and float(output_quality.get("local_only_island_recall", 1.0) or 0.0) >= 0.70
        ),
        "hard_failures": [],
        "warnings": [],
    }
    if float(output_quality.get("local_only_island_recall", 1.0) or 0.0) < 0.80:
        gates["warnings"].append("local_only_island_recall_below_usable_threshold")
    if output_quality["audit_cleanup"]["audit_review_seconds"] > 300:
        gates["warnings"].append("audit_review_seconds_high")

    output_dialogue = {
        "schema": "murmurmark.clean_dialogue/v1",
        "session": dialogue.get("session", session.name),
        "utterances": output_utterances,
    }
    transcript_report = read_json(paths["report"]) if paths["report"].exists() else {}
    simple_payload = {
        "schema": "murmurmark.transcript_simple/v1",
        "session": dialogue.get("session", session.name),
        "backend": "whisper.cpp",
        "utterances": [
            {
                **copy.deepcopy(row),
                "raw_text": row.get("text"),
                "corrected_text": row.get("text"),
                "corrections": [],
            }
            for row in output_utterances
        ],
    }
    clean_dialogue_path = resolved / f"clean_dialogue{output_suffix}.json"
    quality_report_path = resolved / f"quality_report{output_suffix}.json"
    overlaps_path = resolved / f"overlaps{output_suffix}.json"
    simple_transcript_path = resolved / f"transcript.simple{output_suffix}.json"
    transcript_path = resolved / f"transcript{output_suffix}.md"
    cleanup_report_path = cleanup_dir / f"audit_cleanup_report{output_suffix}.json"
    write_json(clean_dialogue_path, output_dialogue)
    write_json(quality_report_path, output_quality)
    write_json(overlaps_path, {"schema": "murmurmark.transcript_overlaps/v1", "session": session.name, "overlaps": output_overlaps})
    write_json(simple_transcript_path, simple_payload)
    write_markdown(
        transcript_path,
        output_utterances,
        transcript_report.get("model"),
        transcript_report.get("language"),
    )

    diff = {
        "schema": "murmurmark.audit_cleanup_diff/v1",
        "input_profile": args.input_profile,
        "output_profile": args.output_profile,
        "removed_utterance_ids": sorted(dropped_ids),
        "inserted_utterances": [
            {
                "id": str(row.get("id")),
                "source_utterance_id": str((row.get("quality") or {}).get("audit_cleanup", {}).get("source_utterance_id", "")),
                "start": row.get("start"),
                "end": row.get("end"),
                "text": row.get("text"),
            }
            for rows in replacements.values()
            for row in rows
        ],
        "modified_utterances": [
            {
                "id": str(row.get("id")),
                "quality_added": (row.get("quality") or {}).get("audit_cleanup"),
            }
            for row in output_utterances
            if isinstance(row.get("quality"), dict) and row["quality"].get("audit_cleanup")
        ],
    }
    handoff = cleanup_handoff(session, args.output_profile, cleanup_report_path, transcript_path)
    report = {
        "schema": "murmurmark.audit_cleanup_report/v1",
        "input_profile": args.input_profile,
        "output_profile": args.output_profile,
        "mode": args.mode,
        "generator": {"name": "apply-audit-cleanup", "version": SCRIPT_VERSION},
        "inputs": {
            key: rel(path, session)
            for key, path in paths.items()
            if path.exists() and (key not in {"audio_review_audit", "audio_review_summary"} or use_audio_review)
            and (key != "audio_judge_queue" or use_audio_judge)
        },
        "summary": {
            "input_utterances": len(utterances),
            "output_utterances": len(output_utterances),
            "applied_patches": len(applied),
            "rejected_patches": len(rejected),
            "audio_review_records": len(audio_review_records),
            "audio_judge_predictions": len(audio_judge_predictions),
            "audio_review_applied_patches": sum(1 for patch in applied if patch.get("evidence", {}).get("source") == "audio_review"),
            "audio_review_rejected_patches": sum(1 for patch in rejected if patch.get("evidence", {}).get("source") == "audio_review"),
            "segment_repair_applied_patches": sum(1 for patch in applied if patch.get("evidence", {}).get("source") == "audio_review_segment"),
            "segment_repair_rejected_patches": sum(1 for patch in rejected if patch.get("evidence", {}).get("source") == "audio_review_segment"),
            "audio_judge_applied_patches": sum(1 for patch in applied if patch.get("evidence", {}).get("source") == "audio_judge"),
            "audio_judge_rejected_patches": sum(1 for patch in rejected if patch.get("evidence", {}).get("source") == "audio_judge"),
            "dropped_me_duplicate_seconds": output_quality["audit_cleanup"]["dropped_me_duplicate_seconds"],
            "dropped_me_noise_seconds": output_quality["audit_cleanup"]["dropped_me_noise_seconds"],
            "segment_repaired_remote_duplicate_seconds": output_quality["audit_cleanup"]["segment_repaired_remote_duplicate_seconds"],
            "protected_intentional_repeat_count": output_quality["audit_cleanup"]["protected_intentional_repeat_count"],
            "needs_review_untouched_seconds": output_quality["audit_cleanup"]["audit_review_seconds"],
            "audit_harmful_seconds_before": output_quality["audit_cleanup"]["audit_harmful_seconds_before"],
            "audit_harmful_seconds_after": output_quality["audit_cleanup"]["audit_harmful_seconds_after"],
        },
        "gates": gates,
        "recommended_next": handoff["recommended_next"],
        "next_commands": handoff["next_commands"],
        "open_commands": handoff["open_commands"],
    }
    write_json(cleanup_report_path, report)
    write_jsonl(cleanup_dir / f"audit_cleanup_patches{output_suffix}.jsonl", applied)
    write_jsonl(cleanup_dir / f"audit_cleanup_rejected_patches{output_suffix}.jsonl", rejected)
    write_json(cleanup_dir / f"audit_cleanup_diff{output_suffix}.json", diff)

    print(f"audit_cleanup_report: {cleanup_report_path}")
    print(f"clean_dialogue: {clean_dialogue_path}")
    print(f"transcript: {transcript_path}")
    print(f"applied_patches: {len(applied)}")
    print(f"dropped_me_duplicate_seconds: {output_quality['audit_cleanup']['dropped_me_duplicate_seconds']}")
    print(f"harmful_after: {output_quality['audit_cleanup']['audit_harmful_seconds_after']}")
    print(f"gates_passed: {gates['passed']}")
    return 0 if gates["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
