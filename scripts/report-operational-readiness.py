#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shlex
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_VERSION = "0.4.5"
SCHEMA = "murmurmark.operational_readiness_report/v1"
TOKEN_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9_+-]+")
GROUPABLE_REVIEW_LANES = {"check_transcript_order", "check_unique_me_content", "classify_audio"}
CROSS_LANE_RELATED_LANES = {"check_unique_me_content", "classify_audio"}
IRREDUCIBLE_REVIEW_ACTION_COUNT_MAX = 15
IRREDUCIBLE_REVIEW_QUEUE_SECONDS_MAX = 60.0
IRREDUCIBLE_NOTES_REVIEW_SECONDS_MAX = 120.0
IRREDUCIBLE_NOTES_REVIEW_RATIO_MAX = 0.005
REVIEW_LANE_ORDER = [
    "fast_confirm_drop",
    "check_unique_me_content",
    "check_local_recall",
    "check_transcript_text",
    "check_transcript_order",
    "confirm_benign",
    "classify_audio",
]
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
INTERRUPTED_CAPTURE_WARNING_MARKERS = (
    "stream stopped with error",
    "capture produced no audio samples",
)
LOW_MATERIALITY_STOP_WORDS = {
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
    "между",
    "мне",
    "меня",
    "мы",
    "на",
    "нас",
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
    "так",
    "тем",
    "то",
    "тогда",
    "тоже",
    "тут",
    "ты",
    "у",
    "уже",
    "что",
    "это",
    "этого",
    "этой",
    "этот",
    "я",
}
PROTECTED_REVIEW_MARKERS = (
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


def normalized_tokens(text: Any) -> list[str]:
    return [token.lower().replace("ё", "е") for token in TOKEN_RE.findall(str(text or ""))]


def low_materiality_content_tokens(text: Any) -> list[str]:
    return [
        token
        for token in normalized_tokens(text)
        if len(token) > 2 and token not in LOW_MATERIALITY_STOP_WORDS
    ]


def edit_distance_at_most_one(left: str, right: str) -> bool:
    if left == right:
        return True
    if abs(len(left) - len(right)) > 1:
        return False
    if len(left) > len(right):
        left, right = right, left
    i = 0
    j = 0
    edits = 0
    while i < len(left) and j < len(right):
        if left[i] == right[j]:
            i += 1
            j += 1
            continue
        edits += 1
        if edits > 1:
            return False
        if len(left) == len(right):
            i += 1
        j += 1
    if i < len(left) or j < len(right):
        edits += 1
    return edits <= 1


def fuzzy_content_covered_by_remote(me_text: Any, remote_text: Any) -> bool:
    me_tokens = low_materiality_content_tokens(me_text)
    if not me_tokens or len(me_tokens) > 2:
        return False
    remote_tokens = low_materiality_content_tokens(remote_text)
    if not remote_tokens:
        return False
    for token in me_tokens:
        covered = False
        for remote_token in remote_tokens:
            if token == remote_token or (len(token) >= 4 and len(remote_token) >= 4 and edit_distance_at_most_one(token, remote_token)):
                covered = True
                break
        if not covered:
            return False
    return True


def low_materiality_duration_limit(
    backchannel: bool,
    content_tokens: list[str],
    tokens: list[str] | None = None,
) -> float:
    if backchannel:
        if tokens == ["так"]:
            return 4.25
        return 4.0 if content_tokens and content_tokens[0] == "спасибо" else 3.0
    if len(content_tokens) <= 1:
        return 2.0
    return 1.25


def tiny_boundary_review_overlap(features: dict[str, Any], duration: float) -> bool:
    return (
        0.0 < duration <= 0.50
        and safe_float(features.get("me_overlap_coverage")) <= 0.05
        and bool(features.get("likely_partial_me_utterance"))
    )


def short_exact_partial_duplicate(
    label: str,
    confidence: float,
    duration: float,
    content_tokens: list[str],
    features: dict[str, Any],
) -> bool:
    return (
        label == "remote_duplicate"
        and confidence >= 0.95
        and 0.0 < duration <= 1.0
        and len(content_tokens) <= 2
        and bool(features.get("likely_partial_me_utterance"))
        and safe_float(features.get("text_similarity")) >= 0.92
        and safe_float(features.get("token_containment")) >= 0.75
    )


def has_protected_review_marker(text: Any) -> bool:
    lowered = str(text or "").lower().replace("ё", "е")
    return any(marker in lowered for marker in PROTECTED_REVIEW_MARKERS)


def has_interrupted_capture_warning(row: dict[str, Any]) -> bool:
    if row.get("pipeline_status") not in {"partial", "incomplete"}:
        return False
    session_path = row.get("session")
    if not session_path:
        return False
    session_json = Path(str(session_path)).expanduser() / "session.json"
    events_jsonl = Path(str(session_path)).expanduser() / "events.jsonl"
    final_reason = ""
    if events_jsonl.exists():
        try:
            with events_jsonl.open("r", encoding="utf-8") as file:
                for line in file:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(event, dict) and event.get("type") == "capture.stopped":
                        final_reason = str(event.get("reason") or "")
        except OSError:
            final_reason = ""
    interrupted_reasons = {"stream_stopped", "capture_stalled", "sigterm", "sighup"}
    if final_reason and final_reason not in interrupted_reasons:
        return False
    if not session_json.exists():
        return False
    try:
        session = read_json(session_json)
    except (OSError, json.JSONDecodeError, ValueError):
        return False
    health = session.get("health")
    if not isinstance(health, dict):
        return False
    health_reason = str(health.get("stop_reason") or "")
    reason = health_reason or final_reason
    partial = bool(health.get("partial")) or session.get("status") == "partial" or reason in interrupted_reasons
    if not partial and session.get("status") != "completed_with_warnings":
        return False
    warnings = health.get("warnings")
    if not isinstance(warnings, list):
        warnings = []
    warning_text = "\n".join(str(warning).lower() for warning in warnings)
    return partial or any(marker in warning_text for marker in INTERRUPTED_CAPTURE_WARNING_MARKERS)


def is_diagnostic_session(row: dict[str, Any]) -> bool:
    session_id = str(row.get("session_id") or row.get("label") or "").lower()
    label = str(row.get("label") or "").lower()
    candidates = {session_id, label}
    if any(candidate in DIAGNOSTIC_SESSION_EXACT_IDS for candidate in candidates):
        return True
    if any(marker in session_id or marker in label for marker in DIAGNOSTIC_SESSION_MARKERS):
        return True
    if has_interrupted_capture_warning(row):
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


def review_confirmed_me_ids(session_path: Path, profile: str) -> set[str]:
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
        if source != "mic" and role != "me":
            continue
        quality = row.get("quality") if isinstance(row.get("quality"), dict) else {}
        human = quality.get("human_review") if isinstance(quality.get("human_review"), dict) else {}
        decisions = {str(item) for item in human.get("decisions") or [] if item}
        if decisions and decisions <= {"keep_me", "drop_remote"}:
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


def audio_review_row_explained_by_strong_local(row: dict[str, Any]) -> bool:
    classification = row.get("classification") if isinstance(row.get("classification"), dict) else {}
    if str(classification.get("label") or "") != "remote_leak":
        return False
    if str(classification.get("verdict") or "") != "probable_transcript_error":
        return False
    scores = row.get("scores") if isinstance(row.get("scores"), dict) else {}
    features = row.get("features") if isinstance(row.get("features"), dict) else {}
    text = features.get("text") if isinstance(features.get("text"), dict) else {}
    local_support = safe_float(scores.get("local_support")) or 0.0
    remote_similarity = safe_float(scores.get("remote_similarity")) or 0.0
    text_similarity = safe_float(text.get("similarity")) or 0.0
    containment = safe_float(text.get("containment")) or 0.0
    remote_duplicate = safe_float(scores.get("remote_duplicate")) or 0.0
    asr_noise = safe_float(scores.get("asr_noise")) or 0.0
    return (
        local_support >= 70.0
        and remote_similarity <= 35.0
        and text_similarity <= 0.25
        and containment <= 0.25
        and remote_duplicate <= 0.0
        and asr_noise <= 0.0
    )


def cleanup_input_profile(session_path: Path, profile: str) -> str | None:
    if not profile.startswith("audit_cleanup_"):
        return None
    report = read_json(
        session_path
        / "derived/transcript-simple/whisper-cpp/audit-cleanup"
        / f"audit_cleanup_report{suffix(profile)}.json"
    )
    if not isinstance(report, dict):
        return None
    value = report.get("input_profile")
    return str(value) if value else None


def review_input_profile(session_path: Path, profile: str) -> str | None:
    report = read_json(
        session_path
        / "derived/transcript-simple/whisper-cpp/review-decisions"
        / f"review_decisions_report{suffix(profile)}.json"
    )
    if not isinstance(report, dict):
        return None
    summary = report.get("summary")
    summary_input_profile = summary.get("input_profile") if isinstance(summary, dict) else None
    value = report.get("input_profile") or summary_input_profile
    return str(value) if value and str(value) != profile else None


def local_recall_repair_input_profile(session_path: Path, profile: str) -> str | None:
    if profile != "local_recall_repair_v1":
        return None
    report = read_json(
        session_path
        / "derived/transcript-simple/whisper-cpp/local-recall-repair/local_recall_repair_report.local_recall_repair_v1.json"
    )
    if not isinstance(report, dict):
        return None
    value = report.get("input_profile")
    return str(value) if value and str(value) != profile else None


def authoritative_boundary_input_profile(session_path: Path, profile: str) -> str | None:
    if profile != "authoritative_boundary_v1":
        return None
    report = read_json(
        session_path
        / "derived/transcript-simple/whisper-cpp/authoritative-boundary-v1/boundary_repair_report.json"
    )
    if not isinstance(report, dict):
        return None
    value = report.get("input_profile")
    return str(value) if value and str(value) != profile else None


def authoritative_boundary_resolved_ids(session_path: Path, profile: str, sources: set[str]) -> set[str]:
    if profile != "authoritative_boundary_v1":
        return set()
    path = (
        session_path
        / "derived/transcript-simple/whisper-cpp/authoritative-boundary-v1/boundary_repair_applied.jsonl"
    )
    return {
        str(row.get("source_audit_id"))
        for row in read_jsonl(path)
        if str(row.get("source") or "") in sources
        and row.get("closed") is True
        and row.get("source_audit_id")
    }


def residual_me_evidence_input_profile(session_path: Path, profile: str) -> str | None:
    if profile not in {"residual_me_evidence_v1", "residual_audio_arbitration_v1", "residual_local_recall_v1"}:
        return None
    report_paths = {
        "residual_me_evidence_v1": "residual-me-evidence-v1/residual_me_evidence_profile_report.json",
        "residual_audio_arbitration_v1": "residual-audio-arbitration-v1/residual_audio_arbitration_profile_report.json",
        "residual_local_recall_v1": "residual-local-recall-v1/residual_local_recall_profile_report.json",
    }
    report_path = report_paths[profile]
    report = read_json(session_path / "derived/transcript-simple/whisper-cpp" / report_path)
    if not isinstance(report, dict):
        return None
    value = report.get("input_profile")
    return str(value) if value and str(value) != profile else None


def residual_me_evidence_resolved_ids(session_path: Path, profile: str, sources: set[str]) -> set[str]:
    if profile not in {"residual_me_evidence_v1", "residual_audio_arbitration_v1", "residual_local_recall_v1"}:
        return set()
    applied_paths = {
        "residual_me_evidence_v1": "residual-me-evidence-v1/residual_me_applied.jsonl",
        "residual_audio_arbitration_v1": "residual-audio-arbitration-v1/residual_audio_applied.jsonl",
        "residual_local_recall_v1": "residual-local-recall-v1/residual_local_recall_applied.jsonl",
    }
    applied_path = applied_paths[profile]
    path = session_path / "derived/transcript-simple/whisper-cpp" / applied_path
    return {
        str(row.get("source_audit_id"))
        for row in read_jsonl(path)
        if str(row.get("source") or "") in sources
        and row.get("closed") is True
        and row.get("source_audit_id")
    }


def local_speech_completion_input_profile(session_path: Path, profile: str) -> str | None:
    if profile != "local_speech_completion_v2":
        return None
    report = read_json(
        session_path
        / "derived/transcript-simple/whisper-cpp/local-speech-completion-v2"
        / "local_speech_completion_profile_report.json"
    )
    if not isinstance(report, dict):
        return None
    value = report.get("input_profile")
    return str(value) if value and str(value) != profile else None


def local_speech_completion_resolved_ids(session_path: Path, profile: str) -> set[str]:
    if profile != "local_speech_completion_v2":
        return set()
    path = (
        session_path
        / "derived/transcript-simple/whisper-cpp/local-speech-completion-v2"
        / "local_speech_completion_dispositions.jsonl"
    )
    return {
        str(row.get("source_audit_id"))
        for row in read_jsonl(path)
        if row.get("closed") is True and row.get("source_audit_id")
    }


def inherited_profiles_for_review(session_path: Path, profile: str) -> list[str]:
    profiles: list[str] = []
    for candidate in (
        cleanup_input_profile(session_path, profile),
        review_input_profile(session_path, profile),
        local_recall_repair_input_profile(session_path, profile),
        authoritative_boundary_input_profile(session_path, profile),
        residual_me_evidence_input_profile(session_path, profile),
        local_speech_completion_input_profile(session_path, profile),
    ):
        if candidate and candidate != profile and candidate not in profiles:
            profiles.append(candidate)
    return profiles


def pending_review_decision_rows(session_path: Path, profile: str) -> list[dict[str, Any]]:
    path = session_path / "derived/readiness/review-plan/review_decisions.jsonl"
    rows: list[dict[str, Any]] = []
    for row in read_jsonl(path):
        input_profile = str(row.get("input_profile") or "")
        if input_profile and input_profile != profile:
            continue
        rows.append(row)
    return rows


def text_utterance_ids(row: dict[str, Any], *, role: str) -> list[str]:
    rows = row.get("text") if isinstance(row.get("text"), list) else []
    ids: list[str] = []
    role = role.lower()
    for item in rows:
        if not isinstance(item, dict):
            continue
        source_track = str(item.get("source_track") or "").lower()
        row_role = str(item.get("role") or "").lower()
        if role == "me":
            matches = source_track == "mic" or row_role == "me"
        else:
            matches = source_track == "remote" or row_role in {"remote", "colleagues"}
        value = item.get("id")
        if matches and value is not None and str(value):
            ids.append(str(value))
    return ids


def review_decision_identity_key(row: dict[str, Any]) -> str:
    interval = row.get("interval") if isinstance(row.get("interval"), dict) else {}
    utterance_ids = row.get("utterance_ids") if isinstance(row.get("utterance_ids"), list) else []
    stable_utterance_ids = [str(item) for item in utterance_ids if item is not None and str(item)]
    if not stable_utterance_ids:
        stable_utterance_ids = text_utterance_ids(row, role="me") + text_utterance_ids(row, role="remote")
    return "|".join(
        [
            str(row.get("session_id") or ""),
            str(row.get("source") or "audio_review"),
            ",".join(stable_utterance_ids),
            str(interval.get("start") or ""),
            str(interval.get("end") or ""),
            str(row.get("label") or ""),
            str(row.get("review_lane") or ""),
            str(row.get("review_action") or ""),
        ]
    )


def review_resolved_audio_ids(session_path: Path, profile: str, seen: set[str] | None = None) -> set[str]:
    seen = seen or set()
    if profile in seen:
        return set()
    seen.add(profile)
    inherited: set[str] = set()
    for inherited_profile in inherited_profiles_for_review(session_path, profile):
        inherited.update(review_resolved_audio_ids(session_path, inherited_profile, seen))
    resolved: set[str] = set(inherited)
    resolved.update(authoritative_boundary_resolved_ids(session_path, profile, {"audio_review"}))
    resolved.update(residual_me_evidence_resolved_ids(session_path, profile, {"audio_review"}))
    for row in pending_review_decision_rows(session_path, profile):
        if str(row.get("status") or "") != "reviewed":
            continue
        if str(row.get("source") or "") != "audio_review":
            continue
        if str(row.get("decision") or "") not in {"drop_me", "drop_remote", "keep_me", "skip"}:
            continue
        source_id = str(row.get("source_audit_id") or "")
        if source_id:
            resolved.add(source_id)
    if profile not in {"reviewed_v1", "agent_reviewed_v1", "local_recall_repair_v1"}:
        return resolved
    path = (
        session_path
        / "derived/transcript-simple/whisper-cpp/review-decisions"
        / f"review_decisions_applied{suffix(profile)}.jsonl"
    )
    for row in read_jsonl(path):
        if str(row.get("source") or "") != "audio_review":
            continue
        if str(row.get("decision") or "") not in {"drop_me", "drop_remote", "keep_me", "skip"}:
            continue
        source_id = str(row.get("source_audit_id") or "")
        if source_id:
            resolved.add(source_id)
    return resolved


def review_resolved_audio_keys(session_path: Path, profile: str, seen: set[str] | None = None) -> set[str]:
    seen = seen or set()
    if profile in seen:
        return set()
    seen.add(profile)
    resolved: set[str] = set()
    for inherited_profile in inherited_profiles_for_review(session_path, profile):
        resolved.update(review_resolved_audio_keys(session_path, inherited_profile, seen))
    for row in pending_review_decision_rows(session_path, profile):
        if str(row.get("status") or "") != "reviewed":
            continue
        if str(row.get("source") or "") != "audio_review":
            continue
        if str(row.get("decision") or "") not in {"drop_me", "drop_remote", "keep_me", "skip"}:
            continue
        key = review_decision_identity_key(row)
        if key:
            resolved.add(key)
    if profile not in {"reviewed_v1", "agent_reviewed_v1", "local_recall_repair_v1"}:
        return resolved
    path = (
        session_path
        / "derived/transcript-simple/whisper-cpp/review-decisions"
        / f"review_decisions_applied{suffix(profile)}.jsonl"
    )
    for row in read_jsonl(path):
        if str(row.get("source") or "") != "audio_review":
            continue
        if str(row.get("decision") or "") not in {"drop_me", "drop_remote", "keep_me", "skip"}:
            continue
        key = review_decision_identity_key(row)
        if key:
            resolved.add(key)
    return resolved


def review_resolved_local_recall_ids(session_path: Path, profile: str, seen: set[str] | None = None) -> set[str]:
    seen = seen or set()
    if profile in seen:
        return set()
    seen.add(profile)
    inherited: set[str] = set()
    for inherited_profile in inherited_profiles_for_review(session_path, profile):
        inherited.update(review_resolved_local_recall_ids(session_path, inherited_profile, seen))
    resolved: set[str] = set(inherited)
    resolved.update(authoritative_boundary_resolved_ids(session_path, profile, {"local_recall", "local_recall_repair"}))
    resolved.update(residual_me_evidence_resolved_ids(session_path, profile, {"local_recall", "local_recall_repair"}))
    resolved.update(local_speech_completion_resolved_ids(session_path, profile))
    for row in pending_review_decision_rows(session_path, profile):
        if str(row.get("status") or "") != "reviewed":
            continue
        if str(row.get("source") or "") not in {"local_recall", "local_recall_repair"}:
            continue
        if str(row.get("decision") or "") not in {"drop_me", "keep_me", "skip"}:
            continue
        source_id = str(row.get("source_audit_id") or "")
        if source_id:
            resolved.add(source_id)
    if profile not in {"reviewed_v1", "agent_reviewed_v1", "local_recall_repair_v1"}:
        return resolved
    path = (
        session_path
        / "derived/transcript-simple/whisper-cpp/review-decisions"
        / f"review_decisions_applied{suffix(profile)}.jsonl"
    )
    for row in read_jsonl(path):
        if str(row.get("source") or "") not in {"local_recall", "local_recall_repair"}:
            continue
        if str(row.get("decision") or "") not in {"drop_me", "keep_me", "skip"}:
            continue
        source_id = str(row.get("source_audit_id") or "")
        if source_id:
            resolved.add(source_id)
    return resolved


def review_resolved_transcript_order_ids(session_path: Path, profile: str, seen: set[str] | None = None) -> set[str]:
    seen = seen or set()
    if profile in seen:
        return set()
    seen.add(profile)
    inherited: set[str] = set()
    for inherited_profile in inherited_profiles_for_review(session_path, profile):
        inherited.update(review_resolved_transcript_order_ids(session_path, inherited_profile, seen))
    resolved: set[str] = set(inherited)
    resolved.update(authoritative_boundary_resolved_ids(session_path, profile, {"transcript_order"}))
    resolved.update(residual_me_evidence_resolved_ids(session_path, profile, {"transcript_order"}))
    for row in pending_review_decision_rows(session_path, profile):
        if str(row.get("status") or "") != "reviewed":
            continue
        if str(row.get("source") or "") != "transcript_order":
            continue
        if str(row.get("decision") or "") not in {"keep_me", "skip"}:
            continue
        source_id = str(row.get("source_audit_id") or "")
        if source_id:
            resolved.add(source_id)
    if profile not in {"reviewed_v1", "agent_reviewed_v1", "local_recall_repair_v1"}:
        return resolved
    path = (
        session_path
        / "derived/transcript-simple/whisper-cpp/review-decisions"
        / f"review_decisions_applied{suffix(profile)}.jsonl"
    )
    for row in read_jsonl(path):
        if str(row.get("source") or "") != "transcript_order":
            continue
        if str(row.get("decision") or "") not in {"keep_me", "skip"}:
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
    review_scope_remaining = safe_float(session.get("review_scope_remaining_seconds"))
    harmful = safe_float(session.get("audit_harmful_seconds_after"))
    burden = probable_error + stronger_judge + local_recall + transcript_order
    transcript_burden = transcript_probable_error + transcript_stronger_judge + local_recall + transcript_order
    burden = max(burden, review_scope_remaining)
    transcript_burden = max(transcript_burden, review_scope_remaining)
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
        "review_scope_status": session.get("review_scope_status"),
        "review_scope_complete": session.get("review_scope_complete"),
        "review_scope_allowed": session.get("review_scope_allowed"),
        "review_scope_partial_allowed": session.get("review_scope_partial_allowed"),
        "review_scope_remaining_seconds": round(review_scope_remaining, 3),
        "audit_harmful_seconds_after": round(harmful, 3),
        "risk_flags": session.get("risk_flags") or [],
        "review_blockers": session.get("review_blockers") or [],
        "export_blockers": session.get("export_blockers") or [],
    }
    source_gate = session.get("use_gate")
    row["use_gate"] = source_gate if isinstance(source_gate, str) and source_gate else session_use_gate(row)
    row["formal_residual_risk"] = formal_residual_risk(row)
    return row


def formal_residual_risk(row: dict[str, Any]) -> dict[str, Any] | None:
    profile = str(row.get("selected_profile") or "")
    verdict = str(row.get("verdict") or "")
    if verdict != "risky":
        return None
    if profile not in {
        "audit_cleanup_v1",
        "audit_cleanup_v2",
        "audit_cleanup_v3",
        "audit_cleanup_v4",
        "audit_cleanup_v5",
        "audit_cleanup_v6",
        "audit_cleanup_v7",
        "reviewed_v1",
        "agent_reviewed_v1",
        "local_recall_repair_v1",
        "authoritative_boundary_v1",
        "residual_me_evidence_v1",
        "residual_audio_arbitration_v1",
        "residual_local_recall_v1",
        "local_speech_completion_v2",
    }:
        return None
    flags = {str(flag) for flag in row.get("risk_flags") or []}
    allowed_flags = {
        "verdict:risky",
        "partial_review_scope",
        "local_recall_possible_lost_me",
        "low_local_recall",
        "notes_high_needs_review_ratio",
    }
    if flags - allowed_flags:
        return None
    remaining = safe_float(row.get("review_scope_remaining_seconds"))
    if remaining <= 0.001 or remaining > 15.0:
        return None
    ratio = safe_float(row.get("review_burden_ratio"))
    if ratio > 0.025:
        return None
    return {
        "status": "review_first_residual_risk",
        "remaining_review_seconds": round(remaining, 3),
        "review_burden_ratio": round(ratio, 6),
        "allowed_flags": sorted(flags),
    }


def session_use_gate(row: dict[str, Any]) -> str:
    ratio = safe_float(row.get("review_burden_ratio"))
    flags = set(row.get("risk_flags") or [])
    profile = str(row.get("selected_profile") or "")
    verdict = str(row.get("verdict") or "")
    if verdict == "failed":
        return "do_not_use_without_manual_review"
    if verdict == "risky" and not formal_residual_risk(row):
        return "do_not_use_without_manual_review"
    if profile not in {
        "audit_cleanup_v1",
        "audit_cleanup_v2",
        "audit_cleanup_v3",
        "audit_cleanup_v4",
        "audit_cleanup_v5",
        "audit_cleanup_v6",
        "audit_cleanup_v7",
        "reviewed_v1",
        "agent_reviewed_v1",
        "local_recall_repair_v1",
        "authoritative_boundary_v1",
        "residual_me_evidence_v1",
        "residual_audio_arbitration_v1",
        "residual_local_recall_v1",
        "local_speech_completion_v2",
    }:
        return "pipeline_incomplete_review_first"
    if formal_residual_risk(row):
        return "review_first"
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


def audio_review_me_text(row: dict[str, Any]) -> str:
    utterances = row.get("utterances") if isinstance(row.get("utterances"), list) else []
    parts: list[str] = []
    for item in utterances:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source_track") or "").lower()
        role = str(item.get("role") or item.get("speaker_label") or "").lower()
        if source == "mic" or role == "me":
            parts.append(str(item.get("text") or ""))
    return " ".join(parts)


def audio_review_remote_text(row: dict[str, Any]) -> str:
    utterances = row.get("utterances") if isinstance(row.get("utterances"), list) else []
    parts: list[str] = []
    for item in utterances:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source_track") or "").lower()
        role = str(item.get("role") or item.get("speaker_label") or "").lower()
        if source == "remote" or role in {"remote", "colleagues"}:
            parts.append(str(item.get("text") or ""))
    return " ".join(parts)


def audio_review_row_low_materiality(row: dict[str, Any]) -> bool:
    classification = row.get("classification") if isinstance(row.get("classification"), dict) else {}
    label = str(classification.get("label") or "")
    verdict = str(classification.get("verdict") or "")
    if label not in {"remote_duplicate", "remote_leak", "uncertain"}:
        return False
    if verdict not in {"probable_transcript_error", "needs_stronger_audio_judge"}:
        return False

    interval = row.get("interval") if isinstance(row.get("interval"), dict) else {}
    duration = safe_float(interval.get("duration_sec"))
    if duration <= 0.0:
        return False

    me_text = audio_review_me_text(row)
    backchannel = is_low_materiality_me_backchannel(me_text)

    compacted = compact_review_item({}, row)
    features = compacted.get("review_features") if isinstance(compacted.get("review_features"), dict) else {}
    confidence = safe_float(compacted.get("confidence"))
    text_similarity = safe_float(features.get("text_similarity"))
    containment = safe_float(features.get("token_containment"))
    me_coverage = safe_float(features.get("me_overlap_coverage"))
    content_tokens = low_materiality_content_tokens(me_text)
    tiny_boundary = tiny_boundary_review_overlap(features, duration)
    if has_protected_review_marker(me_text) and not tiny_boundary:
        return False
    if duration > low_materiality_duration_limit(backchannel, content_tokens, normalized_tokens(me_text)) and not tiny_boundary:
        return False
    fuzzy_duplicate = fuzzy_content_covered_by_remote(me_text, audio_review_remote_text(row))
    high_confidence_duplicate = (
        label == "remote_duplicate"
        and confidence >= 0.95
        and me_coverage >= 0.80
        and (text_similarity >= 0.92 or containment >= 0.75 or fuzzy_duplicate)
    )

    return (
        (label != "remote_duplicate" and len(content_tokens) <= 1)
        or me_coverage <= 0.40
        or tiny_boundary
        or (backchannel and duration <= 4.0)
        or (label != "remote_duplicate" and fuzzy_duplicate)
        or high_confidence_duplicate
        or short_exact_partial_duplicate(label, confidence, duration, content_tokens, features)
        or (label != "remote_duplicate" and len(content_tokens) <= 2 and text_similarity <= 0.30 and containment <= 0.25)
    )


def low_materiality_review_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_label: dict[str, int] = {}
    seconds = 0.0
    for row in rows:
        classification = row.get("classification") if isinstance(row.get("classification"), dict) else {}
        label = str(row.get("label") or classification.get("label") or "unknown")
        by_label[label] = by_label.get(label, 0) + 1
        interval = row.get("interval") if isinstance(row.get("interval"), dict) else {}
        seconds += safe_float(interval.get("duration_sec"))
    return {
        "items": len(rows),
        "seconds": round(seconds, 3),
        "minutes": round(seconds / 60.0, 2),
        "by_label": dict(sorted(by_label.items())),
    }


def review_item_me_text(item: dict[str, Any]) -> str:
    rows = item.get("text") if isinstance(item.get("text"), list) else []
    parts: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        source = str(row.get("source_track") or "").lower()
        role = str(row.get("role") or row.get("speaker_label") or "").lower()
        if source == "mic" or role == "me":
            parts.append(str(row.get("text") or ""))
    return " ".join(parts)


def review_item_remote_text(item: dict[str, Any]) -> str:
    rows = item.get("text") if isinstance(item.get("text"), list) else []
    parts: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        source = str(row.get("source_track") or "").lower()
        role = str(row.get("role") or row.get("speaker_label") or "").lower()
        if source == "remote" or role in {"remote", "colleagues"}:
            parts.append(str(row.get("text") or ""))
    return " ".join(parts)


def is_order_backchannel(text: Any) -> bool:
    tokens = normalized_tokens(text)
    return tokens in (
        ["вот"],
        ["да"],
        ["окей"],
        ["окей", "да"],
        ["ну", "да"],
        ["ну", "да", "да"],
        ["ага"],
        ["угу"],
        ["так"],
        ["хорошо"],
        ["понял"],
        ["поняла"],
        ["спасибо"],
        ["спасибо", "тебе"],
    )


def is_low_materiality_me_backchannel(text: Any) -> bool:
    tokens = normalized_tokens(text)
    return tokens in (
        ["вот"],
        ["да"],
        ["окей"],
        ["окей", "да"],
        ["ну", "да"],
        ["ну", "да", "да"],
        ["ага"],
        ["угу"],
        ["так"],
        ["хорошо"],
        ["понял"],
        ["поняла"],
        ["спасибо"],
        ["спасибо", "тебе"],
    )


def review_item_low_materiality(item: dict[str, Any]) -> bool:
    source = str(item.get("source") or "")
    label = str(item.get("label") or "")
    verdict = str(item.get("verdict") or "")
    interval = item.get("interval") if isinstance(item.get("interval"), dict) else {}
    duration = safe_float(interval.get("duration_sec"))
    me_text = review_item_me_text(item)
    content_tokens = low_materiality_content_tokens(me_text)
    normalized = normalized_tokens(me_text)
    if label == "lost_me" and verdict == "probable_transcript_error":
        return (
            0.0 < duration <= 1.5
            and len(normalized) <= 3
            and len(content_tokens) <= 2
            and safe_float(item.get("confidence")) <= 0.75
            and not has_protected_review_marker(me_text)
        )
    if source == "transcript_order":
        if label != "needs_review" or verdict != "needs_transcript_order_review":
            return False
        if has_protected_review_marker(me_text):
            return False
        features = item.get("review_features") if isinstance(item.get("review_features"), dict) else {}
        text_similarity = safe_float(features.get("text_similarity"))
        remote_containment = safe_float(features.get("remote_text_contained_in_me"))
        simple_me_tail = len(content_tokens) <= 2
        remote_backchannel = (
            is_order_backchannel(review_item_remote_text(item))
            and 0.0 < safe_float(features.get("overlap_duration_sec")) <= 8.0
            and safe_float(features.get("pre_remote_lead_sec")) <= 1.0
        )
        return (
            (simple_me_tail or remote_backchannel)
            and text_similarity <= 0.30
            and remote_containment <= 0.05
            and not bool(features.get("remote_inside_me"))
            and not bool(features.get("me_wraps_remote"))
        )
    if label not in {"remote_duplicate", "remote_leak", "uncertain"}:
        return False
    if verdict not in {"probable_transcript_error", "needs_stronger_audio_judge"}:
        return False
    if duration <= 0.0:
        return False
    backchannel = is_low_materiality_me_backchannel(me_text)
    features = item.get("review_features") if isinstance(item.get("review_features"), dict) else {}
    confidence = safe_float(item.get("confidence"))
    text_similarity = safe_float(features.get("text_similarity"))
    containment = safe_float(features.get("token_containment"))
    me_coverage = safe_float(features.get("me_overlap_coverage"))
    tiny_boundary = tiny_boundary_review_overlap(features, duration)
    if has_protected_review_marker(me_text) and not tiny_boundary:
        return False
    if duration > low_materiality_duration_limit(backchannel, content_tokens, normalized) and not tiny_boundary:
        return False
    fuzzy_duplicate = fuzzy_content_covered_by_remote(me_text, review_item_remote_text(item))
    high_confidence_duplicate = (
        label == "remote_duplicate"
        and confidence >= 0.95
        and me_coverage >= 0.80
        and (text_similarity >= 0.92 or containment >= 0.75 or fuzzy_duplicate)
    )
    return (
        (label != "remote_duplicate" and len(content_tokens) <= 1)
        or me_coverage <= 0.40
        or tiny_boundary
        or (backchannel and duration <= 4.0)
        or (label != "remote_duplicate" and fuzzy_duplicate)
        or high_confidence_duplicate
        or short_exact_partial_duplicate(label, confidence, duration, content_tokens, features)
        or (label != "remote_duplicate" and len(content_tokens) <= 2 and text_similarity <= 0.30 and containment <= 0.25)
    )


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
    if source == "transcript_text" or label == "transcript_text_needs_review":
        return "check_transcript_text"
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
    if source == "local_recall":
        return ["drop_me", "keep_me", "needs_review", "skip"]
    if source == "transcript_order":
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
    if lane in CROSS_LANE_RELATED_LANES:
        me_key = me_utterance_group_key(item)
        if me_key:
            session_id = str(item.get("session_id") or item.get("session") or "")
            return f"cross_lane_me_audio:{session_id}:{me_key}"
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


def review_group_lane(group: list[dict[str, Any]]) -> str:
    lanes = {str(item.get("review_lane") or review_lane(item)) for item in group}
    for lane in REVIEW_LANE_ORDER:
        if lane in lanes:
            return lane
    return str(group[0].get("review_lane") or review_lane(group[0])) if group else "classify_audio"


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
        lane = review_group_lane(group)
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
    rows: dict[str, dict[str, Any]] = {
        lane: {
            "lane": lane,
            "items": 0,
            "seconds": 0.0,
            "labels": {},
        }
        for lane in REVIEW_LANE_ORDER
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
    for lane in REVIEW_LANE_ORDER:
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
    blocker_lane_order = (
        "check_transcript_order",
        "check_unique_me_content",
        "check_local_recall",
        "check_transcript_text",
    )
    blocker_candidates = [
        lane
        for lane in blocker_lane_order
        if safe_int(rows.get(lane, {}).get("items"))
    ]
    lane_rank = {lane: index for index, lane in enumerate(blocker_lane_order)}
    first_lane = max(
        blocker_candidates,
        key=lambda lane: (
            safe_int(actions_by_lane.get(lane)) or safe_int(rows.get(lane, {}).get("items")),
            safe_float(rows.get(lane, {}).get("seconds")),
            -lane_rank.get(lane, 99),
        ),
        default=None,
    )
    fast = rows.get("fast_confirm_drop") or {}
    fast_items = safe_int(fast.get("items"))
    quick_lane = "fast_confirm_drop" if fast_items else None
    first_lane = first_lane or quick_lane or (by_lane[0]["lane"] if by_lane else None)
    first_row = rows.get(first_lane or "") or {}
    first_items = safe_int(first_row.get("items"))
    first_actions = safe_int(actions_by_lane.get(first_lane or "")) or first_items
    first_seconds = safe_float(first_row.get("seconds"))
    first_group_items = first_items
    first_group_seconds = first_seconds
    if first_lane:
        first_groups = [group for group in review_action_groups(review_queue) if review_group_lane(group) == first_lane]
        first_group_items = sum(len(group) for group in first_groups)
        first_group_seconds = sum(
            safe_float((item.get("interval") if isinstance(item.get("interval"), dict) else {}).get("duration_sec"))
            for group in first_groups
            for item in group
        )
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
            "reduce_largest_blocking_review_lane"
            if first_lane and first_lane != quick_lane
            else ("close_fast_confirm_drop" if first_lane else None)
        ),
        "after_first_lane_estimate": {
            "remaining_items": max(0, total_items - first_group_items),
            "remaining_actions": max(0, action_summary["review_action_count"] - first_actions),
            "remaining_seconds": round(max(0.0, total_seconds - first_group_seconds), 3),
            "remaining_minutes": round(max(0.0, total_seconds - first_group_seconds) / 60.0, 2),
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


def manual_tail_reason(item: dict[str, Any]) -> str:
    lane = str(item.get("review_lane") or review_lane(item))
    label = str(item.get("label") or "")
    if lane == "check_local_recall":
        return "local recall evidence is present but not strong enough for automatic keep/drop"
    if lane == "check_unique_me_content":
        return "remote leak may still contain unique Me content; automatic drop is unsafe"
    if lane == "check_transcript_order":
        return "chronology overlap remains ambiguous after stronger audio judge"
    if lane == "classify_audio" and label == "uncertain":
        return "audio-review and stronger audio judge remain weak or conflicting"
    if lane == "fast_confirm_drop":
        return "fast drop candidate still lacks a safe suggested decision"
    return "no safe local evidence for automatic closure"


def manual_tail_explanation(review_queue: list[dict[str, Any]]) -> dict[str, Any]:
    action_summary = review_action_summary(review_queue)
    by_reason: dict[str, dict[str, Any]] = {}
    examples: list[dict[str, Any]] = []
    for raw_item in review_queue:
        item = enrich_review_item(raw_item)
        interval = item.get("interval") if isinstance(item.get("interval"), dict) else {}
        duration = safe_float(interval.get("duration_sec"))
        reason = manual_tail_reason(item)
        row = by_reason.setdefault(
            reason,
            {
                "items": 0,
                "seconds": 0.0,
                "lanes": {},
                "labels": {},
            },
        )
        row["items"] += 1
        row["seconds"] += duration
        lane = str(item.get("review_lane") or review_lane(item))
        label = str(item.get("label") or "unknown")
        row["lanes"][lane] = row["lanes"].get(lane, 0) + 1
        row["labels"][label] = row["labels"].get(label, 0) + 1
        text_rows = item.get("text") if isinstance(item.get("text"), list) else []
        text = " | ".join(str(piece.get("text") or "") for piece in text_rows if isinstance(piece, dict))
        examples.append(
            {
                "session_id": item.get("session_id"),
                "source_audit_id": item.get("source_audit_id"),
                "review_lane": lane,
                "review_action": item.get("review_action"),
                "label": label,
                "duration_sec": round(duration, 3),
                "reason": reason,
                "text": text[:240],
            }
        )
    reasons = []
    for reason, row in sorted(by_reason.items(), key=lambda pair: (-safe_float(pair[1].get("seconds")), pair[0])):
        reasons.append(
            {
                "reason": reason,
                "items": row["items"],
                "seconds": round(safe_float(row["seconds"]), 3),
                "lanes": dict(sorted(row["lanes"].items())),
                "labels": dict(sorted(row["labels"].items())),
            }
        )
    return {
        "schema": "murmurmark.manual_tail_explanation/v1",
        "items": len(review_queue),
        "actions": action_summary["review_action_count"],
        "grouped_rows": action_summary["grouped_review_row_count"],
        "seconds": round(
            sum(
                safe_float((item.get("interval") if isinstance(item.get("interval"), dict) else {}).get("duration_sec"))
                for item in review_queue
            ),
            3,
        ),
        "reasons": reasons,
        "examples": examples[:20],
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


def compact_local_speech_completion_item(session: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    interval = row.get("interval") if isinstance(row.get("interval"), dict) else {}
    start = safe_float(interval.get("start"))
    end_value = safe_float(interval.get("end"))
    end = max(start, end_value if end_value is not None else start)
    duration = max(0.0, end - start)
    session_path = str(session.get("session") or "")
    label = str(row.get("label") or "local_recall_needs_review")
    utterance_ids = [str(value) for value in row.get("utterance_ids") or [] if value]
    allowed = ["keep_me", "needs_review", "skip"] if label == "transcript_text_needs_review" else ["needs_review", "skip"]
    listen_start = max(0.0, start - 1.0)
    listen_duration = duration + 2.0
    return {
        "session_id": session.get("session_id"),
        "session": session.get("session"),
        "source_audit_id": row.get("source_audit_id") or row.get("queue_id"),
        "source": "local_speech_completion",
        "label": label,
        "verdict": "needs_human_review",
        "confidence": row.get("confidence"),
        "priority_score": round(duration + safe_float(row.get("confidence")) * 10.0 + 180.0, 3),
        "input_profile": "local_speech_completion_v2",
        "review_lane": row.get("review_lane") or ("check_transcript_text" if label == "transcript_text_needs_review" else "check_local_recall"),
        "review_action": row.get("review_action") or ("check_transcript_text" if label == "transcript_text_needs_review" else "check_lost_local_speech"),
        "allowed_decisions": allowed,
        "interval": {
            "start": round(start, 3),
            "end": round(end, 3),
            "duration_sec": round(duration, 3),
            "start_time": format_time(start),
            "end_time": format_time(end),
        },
        "utterance_ids": utterance_ids,
        "me_utterance_ids": utterance_ids,
        "text": [
            {
                "id": utterance_ids[0] if utterance_ids else None,
                "role": "Me",
                "source_track": "mic",
                "text": row.get("text"),
            }
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
        },
        "reason": row.get("reason"),
        "evidence_fingerprint": row.get("evidence_fingerprint"),
    }


def compact_transcript_text_utterance(session: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    start = safe_float(row.get("start"))
    end_value = safe_float(row.get("end"))
    end = max(start, end_value if end_value is not None else start)
    duration = max(0.0, end - start)
    session_path = str(session.get("session") or "")
    utterance_id = str(row.get("id") or "")
    listen_start = max(0.0, start - 1.0)
    listen_duration = duration + 2.0
    return {
        "session_id": session.get("session_id"),
        "session": session.get("session"),
        "source_audit_id": f"transcript_text:{utterance_id}",
        "source": "transcript_text",
        "label": "transcript_text_needs_review",
        "verdict": "needs_human_review",
        "confidence": safe_float((row.get("quality") or {}).get("role_confidence")),
        "priority_score": round(duration + 160.0, 3),
        "input_profile": "local_speech_completion_v2",
        "review_lane": "check_transcript_text",
        "review_action": "check_transcript_text",
        "allowed_decisions": ["keep_me", "needs_review", "skip"],
        "interval": {
            "start": round(start, 3),
            "end": round(end, 3),
            "duration_sec": round(duration, 3),
            "start_time": format_time(start),
            "end_time": format_time(end),
        },
        "utterance_ids": [utterance_id] if utterance_id else [],
        "me_utterance_ids": [utterance_id] if utterance_id else [],
        "text": [{"id": utterance_id, "role": "Me", "source_track": "mic", "text": row.get("text")}],
        "commands": {
            "mic_raw": (
                f"ffplay -hide_banner -loglevel error -ss {listen_start:.3f} "
                f"-t {listen_duration:.3f} \"{session_path}/audio/mic/000001.caf\""
            ),
            "remote": (
                f"ffplay -hide_banner -loglevel error -ss {listen_start:.3f} "
                f"-t {listen_duration:.3f} \"{session_path}/audio/remote/000001.caf\""
            ),
        },
        "reason": "selected transcript marks this Me utterance as needs_review",
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
        "check_transcript_text": 2,
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


def build_review_queue_details(sessions: list[dict[str, Any]], max_items: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    by_session = {str(session.get("session_id")): session for session in sessions}
    me_ids_cache: dict[tuple[str, str], set[str]] = {}
    review_confirmed_me_ids_cache: dict[tuple[str, str], set[str]] = {}
    review_resolved_cache: dict[tuple[str, str], set[str]] = {}
    review_resolved_keys_cache: dict[tuple[str, str], set[str]] = {}
    local_recall_resolved_cache: dict[tuple[str, str], set[str]] = {}
    transcript_order_resolved_cache: dict[tuple[str, str], set[str]] = {}
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
        if cache_key not in review_confirmed_me_ids_cache:
            review_confirmed_me_ids_cache[cache_key] = review_confirmed_me_ids(session_path, profile)
        if cache_key not in review_resolved_cache:
            review_resolved_cache[cache_key] = review_resolved_audio_ids(session_path, profile)
        if cache_key not in review_resolved_keys_cache:
            review_resolved_keys_cache[cache_key] = review_resolved_audio_keys(session_path, profile)
        if cache_key not in local_recall_resolved_cache:
            local_recall_resolved_cache[cache_key] = review_resolved_local_recall_ids(session_path, profile)
        if cache_key not in transcript_order_resolved_cache:
            transcript_order_resolved_cache[cache_key] = review_resolved_transcript_order_ids(session_path, profile)
        selected_ids = me_ids_cache[cache_key]
        confirmed_me_ids = review_confirmed_me_ids_cache[cache_key]
        review_resolved_ids = review_resolved_cache[cache_key]
        review_resolved_keys = review_resolved_keys_cache[cache_key]
        local_recall_resolved_ids = local_recall_resolved_cache[cache_key]
        transcript_order_resolved_ids = transcript_order_resolved_cache[cache_key]
        completion_open_local_ids: set[str] = set()
        completion_text_utterance_ids: set[str] = set()
        if profile == "local_speech_completion_v2":
            completion_dir = (
                session_path
                / "derived/transcript-simple/whisper-cpp/local-speech-completion-v2"
            )
            for completion_row in read_jsonl(completion_dir / "local_speech_completion_review_queue.jsonl"):
                label = str(completion_row.get("label") or "")
                if label == "local_recall_needs_review" and completion_row.get("source_audit_id"):
                    completion_open_local_ids.add(str(completion_row.get("source_audit_id")))
                completion_text_utterance_ids.update(
                    str(value) for value in completion_row.get("utterance_ids") or [] if value
                )
                rows.append(compact_local_speech_completion_item(session, completion_row))
            dialogue = read_json(
                session_path
                / "derived/transcript-simple/whisper-cpp/resolved"
                / "clean_dialogue.local_speech_completion_v2.json"
            ) or {}
            for utterance in dialogue.get("utterances") or []:
                if not isinstance(utterance, dict):
                    continue
                quality = utterance.get("quality") if isinstance(utterance.get("quality"), dict) else {}
                utterance_id = str(utterance.get("id") or "")
                if (
                    quality.get("needs_review") is True
                    and str(utterance.get("role") or utterance.get("speaker_label") or "").lower() in {"me", "mic"}
                    and utterance_id not in completion_text_utterance_ids
                ):
                    rows.append(compact_transcript_text_utterance(session, utterance))
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
            row_me_ids = audio_review_me_ids(row)
            if row_me_ids and row_me_ids <= confirmed_me_ids:
                continue
            if selected_ids and not active_audio_review_row(row, selected_ids):
                continue
            if audio_review_row_explained_by_reliable(row, selected_ids, reliable_by_me_id):
                continue
            if audio_review_row_explained_by_strong_local(row):
                continue
            session_id = str(row.get("session_id") or session.get("session_id"))
            compacted = enrich_review_item(compact_review_item(by_session.get(session_id, session), row))
            if review_decision_identity_key(compacted) in review_resolved_keys:
                continue
            rows.append(compacted)
        repair_patches_path = (
            session_path
            / "derived/transcript-simple/whisper-cpp/local-recall-repair/local_recall_repair_patches.local_recall_repair_v1.jsonl"
        )
        repaired_local_recall_ids: set[str] = set()
        for patch in read_jsonl(repair_patches_path):
            if str(patch.get("status") or "") != "applied":
                continue
            source_item_id = str(patch.get("source_item_id") or "")
            if source_item_id in local_recall_resolved_ids:
                continue
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
            item_id = str(row.get("item_id") or "")
            if (
                item_id in repaired_local_recall_ids
                or item_id in local_recall_resolved_ids
                or item_id in completion_open_local_ids
            ):
                continue
            rows.append(compact_local_recall_item(session, row))
        order_path = session_path / "derived/audit/order/transcript_order_items.jsonl"
        for row in read_jsonl(order_path):
            label = str(row.get("label") or "")
            if label not in {"probable_order_risk", "needs_review"}:
                continue
            if str(row.get("item_id") or row.get("id") or "") in transcript_order_resolved_ids:
                continue
            rows.append(compact_transcript_order_item(session, row))
    rows.sort(key=review_queue_sort_key)
    selected = select_review_queue(rows, max_items)
    low_materiality_rows = [item for item in selected if review_item_low_materiality(item)]
    mandatory = [item for item in selected if not review_item_low_materiality(item)]
    return mandatory, low_materiality_rows


def build_review_queue(sessions: list[dict[str, Any]], max_items: int) -> list[dict[str, Any]]:
    rows, _ = build_review_queue_details(sessions, max_items)
    return rows


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
    non_actionable_targets = [row for row in not_ready if session_review_non_actionable(row)]
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
    strategic_lane = str(queue_strategy.get("first_recommended_lane") or "") or None
    review_focus = review_queue_focus(review_queue, queue_by_session, strategic_lane)
    queue_actions = {
        "review_action_count": safe_int(queue_strategy.get("review_action_count")),
        "grouped_review_row_count": safe_int(queue_strategy.get("grouped_review_row_count")),
    }

    if blockers:
        status = "blocked_by_structural_issues"
    elif warnings:
        status = "manual_review_or_algorithmic_cleanup_needed"
    elif not_ready and not review_queue and non_actionable_targets:
        status = "medium_risk_ready_with_documented_non_actionable_review_blockers"
    elif not_ready:
        status = "medium_risk_ready_but_some_sessions_need_review"
    else:
        status = "medium_risk_ready"

    next_actions: list[str] = []
    if blockers:
        next_actions.append("fix_blockers_before_using_for_medium_risk_meetings")
    if not_ready and review_queue:
        next_actions.append("review_or_improve_sessions_with_use_gate_not_ready_for_notes")
    elif not_ready and non_actionable_targets:
        next_actions.append("inspect_documented_non_actionable_review_blockers_or_improve_cleanup")
    elif not_ready:
        next_actions.append("investigate_missing_review_queue_or_improve_cleanup")
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
            "non_actionable_review_targets": len(non_actionable_targets),
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
                "review_scope_status": row.get("review_scope_status"),
                "review_scope_complete": row.get("review_scope_complete"),
                "review_scope_remaining_seconds": row.get("review_scope_remaining_seconds"),
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


def review_queue_focus(
    review_queue: list[dict[str, Any]],
    queue_by_session: list[dict[str, Any]] | None = None,
    strategic_lane: str | None = None,
) -> dict[str, Any] | None:
    if not review_queue:
        return None
    if queue_by_session:
        target_row = queue_by_session[0]
        lane = str(strategic_lane or target_row.get("first_review_lane") or "")
        if strategic_lane:
            candidates: list[tuple[int, float, str, dict[str, Any]]] = []
            for row in queue_by_session:
                if not isinstance(row, dict):
                    continue
                target = session_cli_arg(row.get("session_id") or row.get("session"))
                if not target:
                    continue
                for lane_row in row.get("by_review_lane") or []:
                    if not isinstance(lane_row, dict) or str(lane_row.get("lane") or "") != strategic_lane:
                        continue
                    actions = safe_int(lane_row.get("actions")) or safe_int(lane_row.get("items"))
                    seconds = safe_float(lane_row.get("seconds"))
                    if actions > 0 or seconds > 0:
                        candidates.append((actions, seconds, target, row))
                    break
            if candidates:
                candidates.sort(key=lambda item: (-item[0], -item[1], item[2]))
                target_row = candidates[0][3]
        target = session_cli_arg(target_row.get("session_id") or target_row.get("session"))
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


def first_review_target_for_lane(promotion: dict[str, Any], lane: str | None) -> str | None:
    if not lane:
        return first_review_target(promotion)

    queue_by_session = promotion.get("review_queue_by_session")
    candidates: list[tuple[int, float, str]] = []
    if isinstance(queue_by_session, list):
        for row in queue_by_session:
            if not isinstance(row, dict):
                continue
            target = session_cli_arg(row.get("session_id") or row.get("session"))
            if not target:
                continue
            by_lane = row.get("by_review_lane")
            if not isinstance(by_lane, list):
                continue
            for lane_row in by_lane:
                if not isinstance(lane_row, dict) or str(lane_row.get("lane") or "") != lane:
                    continue
                actions = int(safe_float(lane_row.get("actions")))
                items = int(safe_float(lane_row.get("items")))
                seconds = safe_float(lane_row.get("seconds"))
                if actions > 0 or items > 0:
                    candidates.append((actions or items, seconds, target))

    if candidates:
        candidates.sort(key=lambda item: (-item[0], -item[1], item[2]))
        return candidates[0][2]

    focus = promotion.get("review_focus") if isinstance(promotion.get("review_focus"), dict) else {}
    if str(focus.get("review_lane") or "") == lane:
        target = session_cli_arg(focus.get("session_arg") or focus.get("session_id"))
        if target:
            return target

    return first_review_target(promotion)


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
    if session_review_non_actionable(row):
        return "inspect_documented_non_actionable_blocker"
    return "close_review_decisions_or_improve_cleanup"


def session_review_non_actionable(row: dict[str, Any]) -> bool:
    if str(row.get("use_gate") or "") == "ready_for_notes":
        return False
    remaining = safe_float(row.get("review_scope_remaining_seconds"))
    if row.get("review_scope_complete") is not True and str(row.get("review_scope_status") or "") != "partial_allowed":
        return False
    return remaining <= 0.001


def irreducible_review_assessment(
    blockers: list[str],
    warnings: list[str],
    burdens: list[dict[str, Any]],
    review_queue: list[dict[str, Any]],
    low_materiality_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    not_ready = [row for row in burdens if row.get("use_gate") != "ready_for_notes"]
    verdicts = Counter(str(row.get("verdict") or "missing") for row in burdens)
    review_actions = review_action_summary(review_queue)
    queue_seconds = sum(
        safe_float((item.get("interval") if isinstance(item.get("interval"), dict) else {}).get("duration_sec"))
        for item in review_queue
    )
    total_duration = sum(safe_float(row.get("duration_sec")) for row in burdens)
    notes_burden_seconds = sum(safe_float(row.get("notes_review_burden_sec")) for row in burdens)
    notes_burden_ratio = notes_burden_seconds / total_duration if total_duration > 0 else 0.0
    queue_by_session = {str(item.get("session_id") or "") for item in review_queue if item.get("session_id")}
    low_materiality_by_session = {
        str(item.get("session_id") or "") for item in low_materiality_rows if item.get("session_id")
    }
    not_ready_without_queue = [
        str(row.get("session_id") or "")
        for row in not_ready
        if str(row.get("session_id") or "") not in queue_by_session
        and str(row.get("session_id") or "") not in low_materiality_by_session
        and not session_review_non_actionable(row)
    ]
    pending_safe_suggestions = [
        str(row.get("session_id") or "")
        for row in not_ready
        if safe_int(row.get("suggested_closure_actionable_rows")) > 0
    ]
    hard_blockers = [item for item in blockers if item != "risky_or_failed_session_verdicts_present"]
    passed = (
        not hard_blockers
        and safe_int(verdicts.get("failed")) == 0
        and review_actions["review_action_count"] <= IRREDUCIBLE_REVIEW_ACTION_COUNT_MAX
        and queue_seconds <= IRREDUCIBLE_REVIEW_QUEUE_SECONDS_MAX
        and notes_burden_seconds <= IRREDUCIBLE_NOTES_REVIEW_SECONDS_MAX
        and notes_burden_ratio <= IRREDUCIBLE_NOTES_REVIEW_RATIO_MAX
        and not not_ready_without_queue
        and not pending_safe_suggestions
    )
    reasons: list[str] = []
    if hard_blockers:
        reasons.append("hard_blockers_present")
    if safe_int(verdicts.get("failed")):
        reasons.append("failed_sessions_present")
    if review_actions["review_action_count"] > IRREDUCIBLE_REVIEW_ACTION_COUNT_MAX:
        reasons.append("review_action_count_above_limit")
    if queue_seconds > IRREDUCIBLE_REVIEW_QUEUE_SECONDS_MAX:
        reasons.append("review_queue_seconds_above_limit")
    if (
        notes_burden_seconds > IRREDUCIBLE_NOTES_REVIEW_SECONDS_MAX
        or notes_burden_ratio > IRREDUCIBLE_NOTES_REVIEW_RATIO_MAX
    ):
        reasons.append("notes_review_burden_above_limit")
    if not_ready_without_queue:
        reasons.append("not_ready_sessions_without_queue_or_non_actionable_explanation")
    if pending_safe_suggestions:
        reasons.append("safe_suggestions_still_pending")
    if not reasons:
        reasons.append("short_irreducible_review_queue")
    return {
        "schema": "murmurmark.operational_irreducible_review/v1",
        "passed": passed,
        "status": "pilot_ready_with_irreducible_review" if passed else "not_irreducible",
        "reasons": reasons,
        "limits": {
            "review_action_count_max": IRREDUCIBLE_REVIEW_ACTION_COUNT_MAX,
            "review_queue_seconds_max": IRREDUCIBLE_REVIEW_QUEUE_SECONDS_MAX,
            "notes_review_burden_seconds_max": IRREDUCIBLE_NOTES_REVIEW_SECONDS_MAX,
            "notes_review_burden_ratio_max": IRREDUCIBLE_NOTES_REVIEW_RATIO_MAX,
        },
        "metrics": {
            "not_ready_sessions": len(not_ready),
            "review_queue_items": len(review_queue),
            "low_materiality_review_items": len(low_materiality_rows),
            "review_action_count": review_actions["review_action_count"],
            "review_queue_seconds": round(queue_seconds, 3),
            "review_queue_minutes": round(queue_seconds / 60.0, 2),
            "notes_review_burden_seconds": round(notes_burden_seconds, 3),
            "notes_review_burden_ratio": round(notes_burden_ratio, 6),
            "failed_sessions": safe_int(verdicts.get("failed")),
            "risky_sessions": safe_int(verdicts.get("risky")),
            "not_ready_without_queue": not_ready_without_queue,
            "not_ready_with_low_materiality_only": sorted(
                session_id
                for session_id in low_materiality_by_session
                if session_id in {str(row.get("session_id") or "") for row in not_ready}
                and session_id not in queue_by_session
            ),
            "pending_safe_suggestions": pending_safe_suggestions,
        },
    }


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
        + safe_int(selected_profiles.get("audit_cleanup_v7"))
        + safe_int(selected_profiles.get("reviewed_v1"))
        + safe_int(selected_profiles.get("agent_reviewed_v1"))
        + safe_int(selected_profiles.get("local_recall_repair_v1"))
        + safe_int(selected_profiles.get("authoritative_boundary_v1"))
        + safe_int(selected_profiles.get("residual_me_evidence_v1"))
        + safe_int(selected_profiles.get("residual_audio_arbitration_v1"))
        + safe_int(selected_profiles.get("residual_local_recall_v1"))
        + safe_int(selected_profiles.get("local_speech_completion_v2"))
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
    operational_readiness_path: Path | None = None,
) -> dict[str, Any]:
    raw_sessions = session_quality.get("sessions") if isinstance(session_quality.get("sessions"), list) else []
    session_quality, excluded_diagnostics = operational_scope(session_quality)
    sessions = session_quality.get("sessions") if isinstance(session_quality.get("sessions"), list) else []
    burdens = [session_review_burden(row) for row in sessions]
    total_duration = sum(item["duration_sec"] for item in burdens)
    total_burden = sum(item["review_burden_sec"] for item in burdens)
    total_transcript_burden = sum(item["transcript_review_burden_sec"] for item in burdens)
    formal_residual_count = sum(1 for item in burdens if isinstance(item.get("formal_residual_risk"), dict))
    gates: dict[str, int] = {}
    for row in burdens:
        gate = str(row.get("use_gate") or "unknown")
        gates[gate] = gates.get(gate, 0) + 1
    review_queue, low_materiality_rows = build_review_queue_details(burdens, max_review_items)
    low_materiality_summary = low_materiality_review_summary(low_materiality_rows)
    review_actions = review_action_summary(review_queue)
    tail_explanation = manual_tail_explanation(review_queue)
    active_judge_queue = active_audio_judge_queue_summary(burdens, audio_judge_queue)
    audio_judge_review_queue = active_judge_queue or (audio_judge.get("review_queue") if isinstance(audio_judge, dict) else None)
    verdict, blockers, warnings = operational_verdict(session_quality, corpus, audio_judge)
    irreducible_review = irreducible_review_assessment(
        blockers,
        warnings,
        burdens,
        review_queue,
        low_materiality_rows,
    )
    if verdict == "not_ready" and irreducible_review.get("passed") is True:
        blockers = [item for item in blockers if item != "risky_or_failed_session_verdicts_present"]
        if "irreducible_manual_review_queue_present" not in warnings:
            warnings.append("irreducible_manual_review_queue_present")
        verdict = "pilot_ready_with_review"
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
            "formal_residual_risk_sessions": formal_residual_count,
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
            "review_queue_low_materiality_excluded": low_materiality_summary,
            "review_action_count": review_actions["review_action_count"],
            "grouped_review_row_count": review_actions["grouped_review_row_count"],
            "by_review_action": review_actions["by_review_action"],
            "by_review_lane_actions": review_actions["by_review_lane_actions"],
            "by_review_lane_grouped_rows": review_actions["by_review_lane_grouped_rows"],
            "manual_tail_explanation": tail_explanation,
            "irreducible_review": irreducible_review,
        },
        "session_review_burden": burdens,
        "review_queue": review_queue,
        "promotion_plan": promotion,
        "recommendations": recommendations(verdict, blockers, warnings),
        "next_commands": build_next_commands(blockers, promotion, operational_readiness_path),
    }


def build_next_commands(
    blockers: list[str],
    promotion: dict[str, Any],
    operational_readiness_path: Path | None = None,
) -> list[dict[str, str]]:
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
    strategy = promotion.get("review_queue_strategy") if isinstance(promotion.get("review_queue_strategy"), dict) else {}
    strategic_lane = str(strategy.get("first_recommended_lane") or "") or None
    review_target = first_review_target_for_lane(promotion, strategic_lane)
    first_lane = strategic_lane or first_review_lane_for_target(promotion, review_target)
    if first_lane:
        session_option = f" --session {review_target}" if review_target else ""
        readiness_option = (
            f" --operational-readiness {shlex.quote(str(operational_readiness_path))}"
            if operational_readiness_path is not None
            else ""
        )
        commands.append(
            {
                "id": "review_first_lane",
                "label": f"Build the first review lane pack ({first_lane}).",
                "command": f"murmurmark review lane {first_lane}{session_option}{readiness_option}",
            }
        )
        commands.append(
            {
                "id": "review_workspace",
                "label": "Build all review lane packs and answer sheets.",
                "command": f"murmurmark review workspace{session_option}{readiness_option}",
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
    low_materiality = (
        report["summary"].get("review_queue_low_materiality_excluded")
        if isinstance(report["summary"].get("review_queue_low_materiality_excluded"), dict)
        else {}
    )
    irreducible = (
        report["summary"].get("irreducible_review")
        if isinstance(report["summary"].get("irreducible_review"), dict)
        else {}
    )
    irreducible_metrics = (
        irreducible.get("metrics")
        if isinstance(irreducible.get("metrics"), dict)
        else {}
    )
    tail = (
        report["summary"].get("manual_tail_explanation")
        if isinstance(report["summary"].get("manual_tail_explanation"), dict)
        else {}
    )
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
        f"- Irreducible review gate: `{irreducible.get('status')}`",
        f"- Irreducible review queue: `{irreducible_metrics.get('review_action_count')}` actions / `{irreducible_metrics.get('review_queue_seconds')}` sec",
        f"- Grouped review rows saved: `{report['summary'].get('grouped_review_row_count')}`",
        f"- Low-materiality rows outside mandatory queue: `{low_materiality.get('items', 0)}` / `{low_materiality.get('seconds', 0.0)} sec`",
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
            f"- Non-actionable review blockers: `{conditions.get('non_actionable_review_targets')}`",
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
            "### Manual Tail Explanation",
            "",
            f"- Tail: `{tail.get('actions', 0)}` actions / `{tail.get('items', 0)}` rows / `{tail.get('seconds', 0.0)}` sec",
            "",
            "| Reason | Rows | Seconds | Lanes | Labels |",
            "|---|---:|---:|---|---|",
        ]
    )
    for row in tail.get("reasons", []) or []:
        if not isinstance(row, dict):
            continue
        lines.append(
            f"| {row.get('reason')} | {row.get('items')} | {safe_float(row.get('seconds')):.2f} | `{row.get('lanes')}` | `{row.get('labels')}` |"
        )
    if not tail.get("reasons"):
        lines.append("| none | 0 | 0.00 | `{}` | `{}` |")
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
    lines.append("| Session | Gate | Profile | Verdict | Notes review min | Notes review % | Transcript/export review min | Residual | Flags |")
    lines.append("|---|---|---|---|---:|---:|---:|---|---|")
    for row in sorted(report["session_review_burden"], key=lambda item: item["review_burden_ratio"], reverse=True):
        residual = row.get("formal_residual_risk") if isinstance(row.get("formal_residual_risk"), dict) else None
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
                    str(residual.get("status")) if residual else "-",
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
    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / "operational_readiness_report.json"
    report = build_report(
        session_quality,
        corpus,
        audio_judge,
        audio_judge_queue,
        inputs,
        args.max_review_items,
        report_path,
    )
    write_json(report_path, report)
    write_markdown(out_dir / "operational_readiness_report.md", report)
    print(f"verdict: {report['operational_verdict']}")
    print(f"written: {out_dir / 'operational_readiness_report.json'}")
    next_commands = [item for item in report.get("next_commands", []) if isinstance(item, dict)]
    if next_commands:
        print(f"next_command: {next_commands[0].get('command')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
