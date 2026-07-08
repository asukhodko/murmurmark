#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from scipy.io import wavfile


SCHEMA = "murmurmark.live_batch_comparison/v1"
SESSION_REPORT_SCHEMA = "murmurmark.live_parity_session_report/v1"
SCRIPT_VERSION = "0.19.0"
EPSILON = 1.0e-12
TOKEN_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9_+-]+")
GENERIC_TOKENS = {
    "а",
    "ага",
    "будет",
    "бы",
    "в",
    "во",
    "вот",
    "все",
    "да",
    "для",
    "же",
    "и",
    "из",
    "или",
    "как",
    "когда",
    "либо",
    "мне",
    "мы",
    "на",
    "не",
    "но",
    "ну",
    "ок",
    "окей",
    "он",
    "она",
    "они",
    "оно",
    "по",
    "просто",
    "с",
    "со",
    "так",
    "там",
    "те",
    "то",
    "тут",
    "ты",
    "у",
    "это",
    "этот",
    "я",
}
CAPTURE_SAFETY_BLOCKERS = {"interrupted_capture", "silent_capture", "sparse_capture"}
CAPTURE_SAFETY_WARNING_MARKERS = (
    "no screencapturekit audio samples",
    "capture produced no audio samples",
    "screencapturekit stream restarted",
    "capture finalized as partial",
    "captured audio covers only",
    "track appears silent or almost silent",
)
BOUNDARY_ISSUE_STATUSES = {"suppressed", "failed", "warning", "blocked", "not_evaluated"}
SUPPRESSED_MIC_RESCUE_POLICIES = (
    "current_text_segment_gate",
    "strict_text_unique_v1",
    "remote_silent_text_v1",
    "audio_remote_quiet_v1",
    "audio_mic_dominant_v1",
    "audio_low_coherence_v1",
    "audio_low_corr_text_guard_v1",
    "audio_safe_union_v1",
    "batch_oracle_local_ceiling",
)
TARGET_ME_RESCUE_POLICIES = (
    "target_me_confirmed_v1",
    "target_me_confirmed_remote_guard_v1",
    "target_me_confirmed_remote_guard_timeline_safe_v1",
    "target_me_possible_v1",
)
TARGET_ME_DERIVED_POLICY_BASE = {
    "target_me_confirmed_remote_guard_timeline_safe_v1": "target_me_confirmed_remote_guard_v1",
}
TARGET_ME_SHADOW_PROFILE_POLICIES = (
    "target_me_confirmed_remote_guard_timeline_safe_v1",
    "target_me_confirmed_remote_guard_timeline_safe_batch_remote_forbidden_oracle_v1",
    "target_me_confirmed_remote_guard_timeline_safe_batch_remote_forbidden_online_suppressed_mic_audio_safe_union_v1",
    "target_me_confirmed_remote_guard_timeline_safe_batch_remote_forbidden_online_suppressed_mic_low_corr_text_guard_v1",
    "target_me_confirmed_remote_guard_timeline_safe_batch_remote_forbidden_visible_suppressed_mic_oracle_v1",
    "online_suppressed_mic_dual_target_remote_guard_v1",
    "online_live_me_remote_overlap_filter_v1",
    "online_live_me_remote_overlap_filter_plus_dual_target_remote_guard_v1",
    "online_live_me_remote_overlap_filter_plus_target_me_remote_guard_v1",
    "online_live_me_remote_overlap_filter_plus_target_me_remote_guard_audio_safe_union_v1",
    "online_live_me_remote_overlap_filter_plus_target_me_timeline_safe_v1",
    "online_live_me_remote_overlap_filter_plus_target_me_timeline_safe_audio_safe_union_v1",
)
TARGET_ME_SHADOW_PROFILE_BASE_POLICY = {
    "target_me_confirmed_remote_guard_timeline_safe_batch_remote_forbidden_oracle_v1": (
        "target_me_confirmed_remote_guard_timeline_safe_v1"
    ),
    "target_me_confirmed_remote_guard_timeline_safe_batch_remote_forbidden_online_suppressed_mic_audio_safe_union_v1": (
        "target_me_confirmed_remote_guard_timeline_safe_v1"
    ),
    "target_me_confirmed_remote_guard_timeline_safe_batch_remote_forbidden_online_suppressed_mic_low_corr_text_guard_v1": (
        "target_me_confirmed_remote_guard_timeline_safe_v1"
    ),
    "target_me_confirmed_remote_guard_timeline_safe_batch_remote_forbidden_visible_suppressed_mic_oracle_v1": (
        "target_me_confirmed_remote_guard_timeline_safe_v1"
    ),
    "online_live_me_remote_overlap_filter_plus_target_me_timeline_safe_v1": (
        "target_me_confirmed_remote_guard_timeline_safe_v1"
    ),
    "online_live_me_remote_overlap_filter_plus_target_me_timeline_safe_audio_safe_union_v1": (
        "target_me_confirmed_remote_guard_timeline_safe_v1"
    ),
    "online_live_me_remote_overlap_filter_plus_target_me_remote_guard_v1": (
        "target_me_confirmed_remote_guard_v1"
    ),
    "online_live_me_remote_overlap_filter_plus_target_me_remote_guard_audio_safe_union_v1": (
        "target_me_confirmed_remote_guard_v1"
    ),
}
TARGET_ME_REMOTE_FORBIDDEN_ORACLE_POLICIES = {
    "target_me_confirmed_remote_guard_timeline_safe_batch_remote_forbidden_oracle_v1",
    "target_me_confirmed_remote_guard_timeline_safe_batch_remote_forbidden_online_suppressed_mic_audio_safe_union_v1",
    "target_me_confirmed_remote_guard_timeline_safe_batch_remote_forbidden_online_suppressed_mic_low_corr_text_guard_v1",
    "target_me_confirmed_remote_guard_timeline_safe_batch_remote_forbidden_visible_suppressed_mic_oracle_v1",
}
TARGET_ME_ONLINE_SUPPRESSED_MIC_PROFILE_POLICIES = {
    "target_me_confirmed_remote_guard_timeline_safe_batch_remote_forbidden_online_suppressed_mic_audio_safe_union_v1": (
        "audio_safe_union_v1"
    ),
    "target_me_confirmed_remote_guard_timeline_safe_batch_remote_forbidden_online_suppressed_mic_low_corr_text_guard_v1": (
        "audio_low_corr_text_guard_v1"
    ),
    "online_live_me_remote_overlap_filter_plus_target_me_timeline_safe_audio_safe_union_v1": (
        "audio_safe_union_v1"
    ),
    "online_live_me_remote_overlap_filter_plus_target_me_remote_guard_audio_safe_union_v1": (
        "audio_safe_union_v1"
    ),
}
TARGET_ME_VISIBLE_SUPPRESSED_MIC_ORACLE_POLICIES = {
    "target_me_confirmed_remote_guard_timeline_safe_batch_remote_forbidden_visible_suppressed_mic_oracle_v1",
}
SUPPRESSED_MIC_COMPOSITE_SHADOW_PROFILE_POLICIES = {
    "online_suppressed_mic_dual_target_remote_guard_v1",
    "online_live_me_remote_overlap_filter_plus_dual_target_remote_guard_v1",
}
LIVE_ME_REMOTE_OVERLAP_FILTER_SHADOW_PROFILE_POLICIES = {
    "online_live_me_remote_overlap_filter_v1",
    "online_live_me_remote_overlap_filter_plus_dual_target_remote_guard_v1",
    "online_live_me_remote_overlap_filter_plus_target_me_remote_guard_v1",
    "online_live_me_remote_overlap_filter_plus_target_me_remote_guard_audio_safe_union_v1",
    "online_live_me_remote_overlap_filter_plus_target_me_timeline_safe_v1",
    "online_live_me_remote_overlap_filter_plus_target_me_timeline_safe_audio_safe_union_v1",
}
LIVE_ME_REMOTE_OVERLAP_FILTER_NO_TARGET_PROFILE_POLICIES = {
    "online_live_me_remote_overlap_filter_v1",
    "online_live_me_remote_overlap_filter_plus_dual_target_remote_guard_v1",
}
MATERIALIZED_TARGET_ME_SHADOW_POLICIES = TARGET_ME_SHADOW_PROFILE_POLICIES


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare near-realtime shadow draft with authoritative batch transcript.")
    parser.add_argument("session", type=Path)
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
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
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


def rel(path: Path, session: Path) -> str:
    try:
        return str(path.relative_to(session))
    except ValueError:
        return str(path)


def resolve_session_path(session: Path, value: Any) -> Path | None:
    if not value:
        return None
    path = Path(str(value))
    if not path.is_absolute():
        path = session / path
    return path if path.exists() else None


def resolve_source_path(session: Path, source: dict[str, Any], keys: tuple[str, ...]) -> Path | None:
    for key in keys:
        path = resolve_session_path(session, source.get(key))
        if path is not None:
            return path
    return None


def tokens(text: str) -> list[str]:
    return [match.group(0).lower() for match in TOKEN_RE.finditer(text)]


def content_tokens(value: str | list[str]) -> list[str]:
    source = tokens(value) if isinstance(value, str) else value
    return [token for token in source if len(token) >= 3 and token not in GENERIC_TOKENS]


def is_contentful_text(text: Any) -> bool:
    return len(content_tokens(str(text or ""))) >= 2


def clean_text(text: str) -> str:
    return " ".join(text.split()).strip()


def bag_recall(source_tokens: list[str], target_tokens: list[str]) -> float | None:
    if not source_tokens:
        return None
    source = Counter(source_tokens)
    target = Counter(target_tokens)
    matched = sum(min(count, target[token]) for token, count in source.items())
    return matched / max(1, sum(source.values()))


def chunk_text(row: dict[str, Any]) -> str:
    parts: list[str] = []
    for source in ("mic", "remote"):
        value = row.get(source)
        if isinstance(value, dict):
            text = str(value.get("text") or "")
            if text:
                parts.append(text)
    return " ".join(parts)


def duplicate_adjacent_chunks(chunks: list[dict[str, Any]]) -> int:
    count = 0
    previous: list[str] = []
    for row in chunks:
        current = tokens(chunk_text(row))
        if current and previous and bag_recall(current, previous) is not None and (bag_recall(current, previous) or 0.0) >= 0.8:
            count += 1
        previous = current
    return count


def counter_unique_tokens(source_tokens: list[str], target_tokens: list[str]) -> list[str]:
    source = Counter(source_tokens)
    target = Counter(target_tokens)
    unique: list[str] = []
    for token, count in source.items():
        remaining = count - target[token]
        if remaining > 0:
            unique.extend([token] * remaining)
    return unique


def read_audio(path: Path | None, cache: dict[Path, tuple[int, np.ndarray] | None]) -> tuple[int, np.ndarray] | None:
    if path is None:
        return None
    if path in cache:
        return cache[path]
    try:
        sample_rate, data = wavfile.read(path)
    except (OSError, ValueError):
        cache[path] = None
        return None
    array = np.asarray(data)
    if array.ndim > 1:
        array = array.mean(axis=1)
    if np.issubdtype(array.dtype, np.integer):
        max_value = max(abs(float(np.iinfo(array.dtype).min)), float(np.iinfo(array.dtype).max))
        array = array.astype(np.float32) / max_value
    else:
        array = array.astype(np.float32)
    cache[path] = (int(sample_rate), array)
    return cache[path]


def rms_db(values: np.ndarray) -> float | None:
    if values.size == 0:
        return None
    rms = float(np.sqrt(np.mean(np.square(values.astype(np.float64)))))
    return round(20.0 * np.log10(rms + EPSILON), 3)


def peak_db(values: np.ndarray) -> float | None:
    if values.size == 0:
        return None
    peak = float(np.max(np.abs(values.astype(np.float64))))
    return round(20.0 * np.log10(peak + EPSILON), 3)


def audio_slice(audio: tuple[int, np.ndarray] | None, start_sec: float, end_sec: float) -> np.ndarray:
    if audio is None:
        return np.asarray([], dtype=np.float32)
    sample_rate, data = audio
    start = max(0, int(round(start_sec * sample_rate)))
    end = min(len(data), int(round(end_sec * sample_rate)))
    if end <= start:
        return np.asarray([], dtype=np.float32)
    return data[start:end]


def zero_lag_abs_corr(left: np.ndarray, right: np.ndarray) -> float | None:
    count = min(left.size, right.size)
    if count < 160:
        return None
    a = left[:count].astype(np.float64)
    b = right[:count].astype(np.float64)
    a -= float(np.mean(a))
    b -= float(np.mean(b))
    denom = float(np.sqrt(np.sum(a * a) * np.sum(b * b)))
    if denom <= EPSILON:
        return None
    return round(abs(float(np.sum(a * b) / denom)), 6)


def segment_audio_features(
    *,
    mic_audio: tuple[int, np.ndarray] | None,
    remote_audio: tuple[int, np.ndarray] | None,
    clip_start_sec: float,
    start_sec: float,
    end_sec: float,
) -> dict[str, Any]:
    local_start = max(0.0, start_sec - clip_start_sec)
    local_end = max(local_start, end_sec - clip_start_sec)
    mic_slice = audio_slice(mic_audio, local_start, local_end)
    remote_slice = audio_slice(remote_audio, local_start, local_end)
    mic_db = rms_db(mic_slice)
    remote_db = rms_db(remote_slice)
    mic_peak = peak_db(mic_slice)
    remote_peak = peak_db(remote_slice)
    return {
        "mic_clean_rms_db": mic_db,
        "remote_rms_db": remote_db,
        "mic_clean_peak_db": mic_peak,
        "remote_peak_db": remote_peak,
        "mic_minus_remote_rms_db": round(mic_db - remote_db, 3)
        if mic_db is not None and remote_db is not None
        else None,
        "mic_remote_zero_lag_abs_corr": zero_lag_abs_corr(mic_slice, remote_slice),
    }


def classify_suppressed_boundary_duplicate(
    previous_source: dict[str, Any] | None,
    current_source: dict[str, Any],
    boundary_gate: dict[str, Any],
) -> dict[str, Any]:
    previous_text = clean_text(str((previous_source or {}).get("text") or ""))
    current_text = clean_text(
        str(current_source.get("raw_text_before_boundary_gate") or current_source.get("text") or "")
    )
    previous_tokens = tokens(previous_text)
    current_tokens = tokens(current_text)
    current_in_previous = bag_recall(current_tokens, previous_tokens) or 0.0
    previous_in_current = bag_recall(previous_tokens, current_tokens) or 0.0
    unique_current_tokens = counter_unique_tokens(current_tokens, previous_tokens)
    resolved = bool(
        current_tokens
        and previous_tokens
        and current_in_previous >= 0.98
        and not unique_current_tokens
    )
    return {
        "resolution": "resolved_duplicate" if resolved else "unresolved_suppression",
        "is_resolved": resolved,
        "current_token_count": len(current_tokens),
        "previous_token_count": len(previous_tokens),
        "unique_current_token_count": len(unique_current_tokens),
        "unique_current_tokens": unique_current_tokens[:12],
        "current_token_recall_in_previous": round(current_in_previous, 6),
        "previous_token_recall_in_current": round(previous_in_current, 6),
        "reported_current_token_recall_in_previous": boundary_gate.get("current_token_recall_in_previous"),
        "reported_previous_token_recall_in_current": boundary_gate.get("previous_token_recall_in_current"),
    }


def live_boundary_gate_summary(chunks: list[dict[str, Any]]) -> dict[str, Any]:
    status_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    issue_count = 0
    suppressed_count = 0
    resolved_suppressed_count = 0
    unresolved_suppressed_count = 0
    examples: list[dict[str, Any]] = []
    resolved_examples: list[dict[str, Any]] = []
    for index, row in enumerate(chunks):
        previous = chunks[index - 1] if index > 0 else None
        chunk_index = row.get("index")
        for source in ("mic", "remote"):
            source_row = row.get(source)
            if not isinstance(source_row, dict):
                continue
            boundary_gate = source_row.get("live_boundary_gate")
            if not isinstance(boundary_gate, dict):
                continue
            status = str(boundary_gate.get("status") or "unknown")
            reason = str(boundary_gate.get("reason") or "unknown")
            status_counts[status] += 1
            reason_counts[reason] += 1
            resolution: dict[str, Any] | None = None
            if status == "suppressed":
                suppressed_count += 1
                previous_source = previous.get(source) if isinstance(previous, dict) else None
                previous_source = previous_source if isinstance(previous_source, dict) else None
                resolution = classify_suppressed_boundary_duplicate(previous_source, source_row, boundary_gate)
                if resolution.get("is_resolved") is True:
                    resolved_suppressed_count += 1
                else:
                    unresolved_suppressed_count += 1
            is_issue = status in BOUNDARY_ISSUE_STATUSES
            if status == "suppressed" and resolution and resolution.get("is_resolved") is True:
                is_issue = False
            if is_issue:
                issue_count += 1
                if len(examples) < 20:
                    example = {
                        "chunk_index": chunk_index,
                        "source": source,
                        "status": status,
                        "reason": reason,
                        "duplicate_score": boundary_gate.get("duplicate_score"),
                        "current_token_recall_in_previous": boundary_gate.get("current_token_recall_in_previous"),
                        "previous_token_recall_in_current": boundary_gate.get("previous_token_recall_in_current"),
                        "hard_start_sec": source_row.get("hard_start_sec"),
                        "hard_end_sec": source_row.get("hard_end_sec"),
                    }
                    if resolution:
                        example["resolution"] = resolution
                    examples.append(example)
            elif status == "suppressed" and resolution and len(resolved_examples) < 20:
                resolved_examples.append(
                    {
                        "chunk_index": chunk_index,
                        "source": source,
                        "status": status,
                        "reason": reason,
                        "duplicate_score": boundary_gate.get("duplicate_score"),
                        "hard_start_sec": source_row.get("hard_start_sec"),
                        "hard_end_sec": source_row.get("hard_end_sec"),
                        "resolution": resolution,
                    }
                )
    return {
        "evaluated_count": sum(status_counts.values()),
        "issue_count": issue_count,
        "suppressed_count": suppressed_count,
        "resolved_suppressed_count": resolved_suppressed_count,
        "unresolved_suppressed_count": unresolved_suppressed_count,
        "status_counts": dict(sorted(status_counts.items())),
        "reason_counts": dict(sorted(reason_counts.items())),
        "examples": examples,
        "resolved_examples": resolved_examples,
    }


def selected_transcript_path(session: Path) -> Path | None:
    readiness = read_json(session / "derived/readiness/session_readiness.json")
    outputs = readiness.get("outputs") if isinstance(readiness, dict) else None
    transcript = outputs.get("transcript") if isinstance(outputs, dict) else None
    path = transcript.get("path") if isinstance(transcript, dict) else None
    if isinstance(path, str):
        candidate = session / path
        if candidate.exists():
            return candidate
    resolved = session / "derived/transcript-simple/whisper-cpp/resolved"
    candidates = sorted(resolved.glob("transcript.*.md"), key=lambda item: item.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def selected_profile(session: Path) -> str | None:
    readiness = read_json(session / "derived/readiness/session_readiness.json")
    if not isinstance(readiness, dict):
        return None
    value = readiness.get("selected_profile") or readiness.get("selected_transcript_profile")
    return str(value) if value else None


def quality_report_path(session: Path, profile: str | None) -> Path | None:
    resolved = session / "derived/transcript-simple/whisper-cpp/resolved"
    candidates: list[Path] = []
    if profile:
        candidates.append(resolved / f"quality_report.{profile}.json")
    candidates.append(resolved / "quality_report.json")
    candidates.extend(sorted(resolved.glob("quality_report.*.json"), key=lambda item: item.stat().st_mtime, reverse=True))
    return next((path for path in candidates if path.exists()), None)


def metric_value(report: dict[str, Any] | None, key: str) -> Any:
    if not isinstance(report, dict):
        return None
    for container in (report, report.get("metrics"), report.get("summary")):
        if isinstance(container, dict) and key in container:
            return container.get(key)
    return None


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def build_capture_safety_gate(session: Path) -> dict[str, Any]:
    session_json = read_json(session / "session.json")
    pipeline = read_json(session / "derived/pipeline-run/pipeline_run_report.json")
    pipeline_status = pipeline.get("status") if isinstance(pipeline, dict) else None
    pipeline_blocker = str(pipeline.get("blocker") or "") if isinstance(pipeline, dict) else ""
    health = session_json.get("health") if isinstance(session_json, dict) else None
    health = health if isinstance(health, dict) else {}
    warnings = health.get("warnings") if isinstance(health.get("warnings"), list) else []
    warning_texts = [str(item) for item in warnings]
    restart_count = safe_int(health.get("screen_capture_restart_count"))
    partial = bool(health.get("partial")) or (isinstance(session_json, dict) and session_json.get("status") == "partial")
    explicit_stop = health.get("explicit_stop")
    health_summary = str(health.get("summary") or "")
    stop_reason = str(health.get("stop_reason") or "")
    safety_warnings = [
        text for text in warning_texts if any(marker in text.lower() for marker in CAPTURE_SAFETY_WARNING_MARKERS)
    ]
    evidence = {
        "pipeline_status": pipeline_status,
        "pipeline_blocker": pipeline_blocker or None,
        "session_status": session_json.get("status") if isinstance(session_json, dict) else None,
        "health_summary": health_summary or None,
        "partial": partial,
        "explicit_stop": explicit_stop,
        "stop_reason": stop_reason or None,
        "screen_capture_restart_count": restart_count,
        "warning_count": len(warning_texts),
        "safety_warning_count": len(safety_warnings),
        "sample_warnings": safety_warnings[:5],
    }
    if pipeline_status == "blocked" and pipeline_blocker in CAPTURE_SAFETY_BLOCKERS:
        return gate(
            "capture_safety",
            "blocked",
            f"batch pipeline blocked the session as {pipeline_blocker}",
            evidence,
        )
    if not isinstance(session_json, dict) or not health:
        return gate("capture_safety", "not_evaluated", "session capture health is missing", evidence)
    if partial or explicit_stop is False:
        return gate("capture_safety", "blocked", "capture was partial or did not end through an explicit stop", evidence)
    if safety_warnings or restart_count > 0 or health_summary in {"warning", "partial"}:
        return gate(
            "capture_safety",
            "warning",
            "capture completed with ScreenCaptureKit/audio health warnings",
            evidence,
        )
    return gate("capture_safety", "passed", "capture health is complete and warning-free", evidence)


def interval_overlap(start_a: float, end_a: float, start_b: float, end_b: float) -> float:
    return max(0.0, min(end_a, end_b) - max(start_a, start_b))


def read_utterances(path: Path | None) -> list[dict[str, Any]]:
    data = read_json(path) if path else None
    rows = data.get("utterances") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return []
    utterances: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        start = safe_float(row.get("start"), safe_float(row.get("source_start")))
        end = safe_float(row.get("end"), safe_float(row.get("source_end"), start))
        role = str(row.get("speaker_label") or row.get("role") or "")
        if role.lower() in {"me", "mic"}:
            role = "Me"
        elif role.lower() in {"remote", "colleagues", "colleague"}:
            role = "Colleagues"
        utterances.append(
            {
                "id": str(row.get("id") or f"batch_{index:06d}"),
                "start": start,
                "end": max(end, start),
                "role": role,
                "text": text,
                "tokens": tokens(text),
                "needs_review": bool((row.get("quality") or {}).get("needs_review")) if isinstance(row.get("quality"), dict) else False,
                "quality": row.get("quality") if isinstance(row.get("quality"), dict) else {},
            }
        )
    return utterances


def selected_clean_dialogue_path(session: Path, profile: str | None) -> Path | None:
    resolved = session / "derived/transcript-simple/whisper-cpp/resolved"
    candidates: list[Path] = []
    if profile and profile != "missing":
        candidates.append(resolved / f"clean_dialogue.{profile}.json")
    candidates.append(resolved / "clean_dialogue.json")
    candidates.extend(sorted(resolved.glob("clean_dialogue.*.json"), key=lambda item: item.stat().st_mtime, reverse=True))
    return next((path for path in candidates if path.exists()), None)


def live_turns(session: Path, chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    turns: list[dict[str, Any]] = []
    for row in chunks:
        try:
            index = int(row.get("index") or 0)
        except (TypeError, ValueError):
            index = 0
        for source, role in (("mic", "Me"), ("remote", "Colleagues")):
            source_row = row.get(source)
            if not isinstance(source_row, dict):
                continue
            text = str(source_row.get("text") or "").strip()
            if not text:
                continue
            source_tokens = tokens(text)
            segment_turns: list[dict[str, Any]] = []
            for segment_index, segment in enumerate(read_global_asr_segments(session, source_row), start=1):
                segment_text = clean_text(str(segment.get("text") or ""))
                segment_tokens = tokens(segment_text)
                if not segment_tokens:
                    continue
                segment_recall = bag_recall(segment_tokens, source_tokens) or 0.0
                if segment_recall < 0.60 and segment_text not in clean_text(text):
                    continue
                segment_turns.append(
                    {
                        "id": f"live_{index:06d}_{source}_{segment_index:03d}",
                        "chunk_index": index,
                        "segment_index": segment_index,
                        "source": f"{source}_segment",
                        "role": role,
                        "start": safe_float(segment.get("start")),
                        "end": safe_float(segment.get("end"), safe_float(segment.get("start"))),
                        "text": segment_text,
                        "tokens": segment_tokens,
                    }
                )
            if segment_turns:
                turns.extend(segment_turns)
                continue
            start = safe_float(source_row.get("hard_start_sec"), safe_float(row.get("start_sec")))
            end = safe_float(source_row.get("hard_end_sec"), safe_float(row.get("end_sec"), start))
            turns.append(
                {
                    "id": f"live_{index:06d}_{source}",
                    "chunk_index": index,
                    "source": source,
                    "role": role,
                    "start": start,
                    "end": max(end, start),
                    "text": text,
                    "tokens": tokens(text),
                }
            )
    return sorted(turns, key=lambda item: (item["start"], item["end"], item["source"]))


def live_suppressed_mic_turns(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    turns: list[dict[str, Any]] = []
    for row in chunks:
        try:
            index = int(row.get("index") or 0)
        except (TypeError, ValueError):
            index = 0
        mic = row.get("mic")
        if not isinstance(mic, dict):
            continue
        gate = mic.get("live_role_gate") if isinstance(mic.get("live_role_gate"), dict) else {}
        if gate.get("status") != "suppressed":
            continue
        text = str(mic.get("raw_text_before_role_gate") or "").strip()
        if not text:
            continue
        start = safe_float(mic.get("hard_start_sec"), safe_float(row.get("start_sec")))
        end = safe_float(mic.get("hard_end_sec"), safe_float(row.get("end_sec"), start))
        turns.append(
            {
                "id": f"live_{index:06d}_mic_suppressed",
                "chunk_index": index,
                "source": "mic_suppressed",
                "role": "Me",
                "start": start,
                "end": max(end, start),
                "text": text,
                "tokens": tokens(text),
                "role_gate": gate,
            }
        )
    return sorted(turns, key=lambda item: (item["start"], item["end"], item["source"]))


def live_segment_role_gate_summary(chunks: list[dict[str, Any]]) -> dict[str, Any]:
    candidate_count = 0
    kept_segment_count = 0
    suppressed_segment_count = 0
    examples: list[dict[str, Any]] = []
    for row in chunks:
        try:
            index = int(row.get("index") or 0)
        except (TypeError, ValueError):
            index = 0
        mic = row.get("mic")
        if not isinstance(mic, dict):
            continue
        gate = mic.get("live_segment_role_gate")
        if not isinstance(gate, dict):
            continue
        kept = safe_int(gate.get("kept_segment_count"))
        suppressed = safe_int(gate.get("suppressed_segment_count"))
        kept_segment_count += kept
        suppressed_segment_count += suppressed
        if gate.get("status") == "rescued" and kept > 0:
            candidate_count += 1
            if len(examples) < 20:
                examples.append(
                    {
                        "chunk_index": index,
                        "kept_segment_count": kept,
                        "suppressed_segment_count": suppressed,
                        "publish_policy": (mic.get("live_role_gate") or {}).get("segment_gate_publish_policy")
                        if isinstance(mic.get("live_role_gate"), dict)
                        else None,
                        "kept_text": gate.get("kept_text"),
                    }
                )
    return {
        "candidate_chunk_count": candidate_count,
        "kept_segment_count": kept_segment_count,
        "suppressed_segment_count": suppressed_segment_count,
        "examples": examples,
    }


def live_rescue_shadow_turns(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    turns: list[dict[str, Any]] = []
    for row in chunks:
        try:
            index = int(row.get("index") or 0)
        except (TypeError, ValueError):
            index = 0
        mic = row.get("mic") if isinstance(row.get("mic"), dict) else {}
        shadow = mic.get("live_rescue_shadow") if isinstance(mic.get("live_rescue_shadow"), dict) else {}
        segments = shadow.get("segments") if isinstance(shadow.get("segments"), list) else []
        segment_turns: list[dict[str, Any]] = []
        for segment_index, segment in enumerate(segments, start=1):
            if not isinstance(segment, dict):
                continue
            text = clean_text(str(segment.get("text") or ""))
            if not text:
                continue
            start = safe_float(segment.get("start"), safe_float(row.get("start_sec")))
            end = safe_float(segment.get("end"), start)
            segment_turns.append(
                {
                    "id": f"live_rescue_shadow_{index:06d}_{segment_index:03d}",
                    "chunk_index": index,
                    "segment_index": segment_index,
                    "source": "mic_rescue_shadow_segment",
                    "role": "Me",
                    "start": start,
                    "end": max(end, start),
                    "text": text,
                    "tokens": tokens(text),
                    "policy": shadow.get("policy"),
                    "publish_policy": shadow.get("publish_policy"),
                    "segment_count": 1,
                    "rescue_policy_candidates": segment.get("rescue_policy_candidates") or [],
                    "audio": segment.get("audio") if isinstance(segment.get("audio"), dict) else {},
                }
            )
        if segment_turns:
            turns.extend(segment_turns)
            continue
        text = clean_text(str(shadow.get("text") or ""))
        if not text:
            continue
        start = safe_float(row.get("start_sec"))
        end = safe_float(row.get("end_sec"), start)
        turns.append(
            {
                "id": f"live_rescue_shadow_{index:06d}",
                "chunk_index": index,
                "source": "mic_rescue_shadow_chunk_fallback",
                "role": "Me",
                "start": start,
                "end": max(end, start),
                "text": text,
                "tokens": tokens(text),
                "policy": shadow.get("policy"),
                "publish_policy": shadow.get("publish_policy"),
                "segment_count": safe_int(shadow.get("segment_count")),
            }
        )
    return sorted(turns, key=lambda item: (safe_float(item.get("start")), safe_float(item.get("end")), str(item.get("id") or "")))


def live_rescue_shadow_summary(chunks: list[dict[str, Any]]) -> dict[str, Any]:
    turns = live_rescue_shadow_turns(chunks)
    chunk_ids = {safe_int(row.get("chunk_index")) for row in turns}
    return {
        "turns": turns,
        "metrics": {
            "live_rescue_shadow_candidate_chunk_count": len(chunk_ids),
            "live_rescue_shadow_candidate_segment_count": sum(safe_int(row.get("segment_count")) for row in turns),
            "live_rescue_shadow_candidate_token_count": sum(len(row.get("tokens") or []) for row in turns),
        },
        "examples": turns[:20],
    }


def read_target_me_live_local_recall_rows(session: Path) -> list[dict[str, Any]]:
    path = session / "derived/audit/live-local-recall-target-me/live_local_recall_target_me_audit.jsonl"
    return read_jsonl(path)


def read_persistent_target_me_profile_rows(session: Path) -> list[dict[str, Any]]:
    root = session.parent.parent
    rows: list[dict[str, Any]] = []
    for base in (
        root / "sessions/_reports/live-pipeline/persistent_target_me_profile_lab.real/targets",
        root / "sessions/_reports/live-pipeline/persistent_target_me_profile_lab/targets",
    ):
        path = base / session.name / "persistent_target_me_profile_lab_rows.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = None
        if not isinstance(payload, list):
            continue
        rows.extend(row for row in payload if isinstance(row, dict))
    return rows


def suppressed_mic_segment_key(row: dict[str, Any]) -> tuple[int, float, float]:
    return (
        safe_int(row.get("chunk_index")),
        round(safe_float(row.get("start")), 3),
        round(safe_float(row.get("end")), 3),
    )


def target_me_audit_row_key(row: dict[str, Any]) -> tuple[int, float, float]:
    interval = row.get("interval") if isinstance(row.get("interval"), dict) else {}
    return (
        safe_int(row.get("chunk_index")),
        round(safe_float(interval.get("start")), 3),
        round(safe_float(interval.get("end")), 3),
    )


def persistent_target_me_row_key(row: dict[str, Any]) -> tuple[int, float, float]:
    return (
        safe_int(row.get("chunk_index")),
        round(safe_float(row.get("start")), 3),
        round(safe_float(row.get("end")), 3),
    )


def target_me_shadow_turns(rows: list[dict[str, Any]], policy: str) -> list[dict[str, Any]]:
    turns: list[dict[str, Any]] = []
    source_policy = TARGET_ME_DERIVED_POLICY_BASE.get(policy, policy)
    for row in rows:
        if source_policy not in (row.get("target_me_rescue_policy_candidates") or []):
            continue
        interval = row.get("interval") if isinstance(row.get("interval"), dict) else {}
        start = safe_float(interval.get("start"))
        end = safe_float(interval.get("end"), start)
        text = clean_text(str(row.get("text") or ""))
        if not text or end <= start:
            continue
        turns.append(
            {
                "id": f"live_target_me_shadow_{policy}_{row.get('id')}",
                "chunk_index": row.get("chunk_index"),
                "source": f"mic_target_me_shadow_{policy}",
                "role": "Me",
                "start": start,
                "end": end,
                "text": text,
                "tokens": tokens(text),
                "policy": policy,
                "target_me_label": (row.get("classification") or {}).get("label")
                if isinstance(row.get("classification"), dict)
                else None,
                "target_me_confidence": (row.get("classification") or {}).get("confidence")
                if isinstance(row.get("classification"), dict)
                else None,
            }
        )
    return sorted(turns, key=lambda item: (safe_float(item.get("start")), safe_float(item.get("end")), str(item.get("id") or "")))


def suppressed_mic_composite_shadow_turns(
    *,
    policy: str,
    suppressed_mic_asr_segments: list[dict[str, Any]],
    target_me_rows: list[dict[str, Any]],
    persistent_target_me_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if policy not in SUPPRESSED_MIC_COMPOSITE_SHADOW_PROFILE_POLICIES:
        return []
    target_by_key = {target_me_audit_row_key(row): row for row in target_me_rows}
    persistent_by_key = {persistent_target_me_row_key(row): row for row in persistent_target_me_rows}
    turns: list[dict[str, Any]] = []
    for segment in suppressed_mic_asr_segments:
        key = suppressed_mic_segment_key(segment)
        target_row = target_by_key.get(key)
        persistent_row = persistent_by_key.get(key)
        if not target_row or not persistent_row:
            continue
        if "target_me_confirmed_remote_guard_v1" not in (target_row.get("target_me_rescue_policy_candidates") or []):
            continue
        persistent = (
            persistent_row.get("persistent_target_me")
            if isinstance(persistent_row.get("persistent_target_me"), dict)
            else {}
        )
        if "confirmed_remote_guard" not in (persistent.get("policy_candidates") or []):
            continue
        start = safe_float(segment.get("start"))
        end = safe_float(segment.get("end"), start)
        text = clean_text(str(segment.get("text") or target_row.get("text") or ""))
        if not text or end <= start:
            continue
        turns.append(
            {
                "id": (
                    "live_suppressed_mic_composite_"
                    f"{policy}_{safe_int(segment.get('chunk_index'))}_{start:.3f}_{end:.3f}"
                ),
                "chunk_index": segment.get("chunk_index"),
                "source": f"mic_suppressed_mic_composite_{policy}",
                "role": "Me",
                "start": start,
                "end": end,
                "text": text,
                "tokens": tokens(text),
                "policy": policy,
                "composite_policy": "dual_target_remote_guard_v1",
                "target_me_label": (target_row.get("classification") or {}).get("label")
                if isinstance(target_row.get("classification"), dict)
                else None,
                "target_me_confidence": (target_row.get("classification") or {}).get("confidence")
                if isinstance(target_row.get("classification"), dict)
                else None,
                "persistent_target_me_label": (persistent.get("classification") or {}).get("label")
                if isinstance(persistent.get("classification"), dict)
                else None,
                "persistent_target_me_confidence": (persistent.get("classification") or {}).get("confidence")
                if isinstance(persistent.get("classification"), dict)
                else None,
            }
        )
    return sorted(turns, key=lambda item: (safe_float(item.get("start")), safe_float(item.get("end")), str(item.get("id") or "")))


def timeline_safe_target_me_turns(
    *,
    candidates: list[dict[str, Any]],
    live_turns_rows: list[dict[str, Any]],
    batch_utterances: list[dict[str, Any]],
    baseline_contentful_role_order_mismatch_count: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for candidate in candidates:
        trial = accepted + [candidate]
        remote_leak = remote_leak_rows_for_turns(trial, batch_utterances)
        if remote_leak:
            rejected.append(
                {
                    "id": candidate.get("id"),
                    "reason": "would_add_suspected_remote_leak",
                    "start": candidate.get("start"),
                    "end": candidate.get("end"),
                    "duration_sec": round(
                        max(0.0, safe_float(candidate.get("end")) - safe_float(candidate.get("start"))),
                        3,
                    ),
                    "text": candidate.get("text"),
                    "remote_leak": remote_leak[:3],
                }
            )
            continue
        contentful_order_mismatches = order_mismatch_rows_for_turns(
            live_turns_rows + trial,
            batch_utterances,
            same_role_only=True,
            contentful_only=True,
            min_token_recall=0.45,
            min_score=0.65,
            match_mode="target_me_timeline_safe_candidate",
        )
        if len(contentful_order_mismatches) > baseline_contentful_role_order_mismatch_count:
            rejected.append(
                {
                    "id": candidate.get("id"),
                    "reason": "would_add_contentful_order_mismatch",
                    "start": candidate.get("start"),
                    "end": candidate.get("end"),
                    "duration_sec": round(
                        max(0.0, safe_float(candidate.get("end")) - safe_float(candidate.get("start"))),
                        3,
                    ),
                    "text": candidate.get("text"),
                    "contentful_order_mismatch_count": len(contentful_order_mismatches),
                    "baseline_contentful_order_mismatch_count": baseline_contentful_role_order_mismatch_count,
                    "examples": contentful_order_mismatches[:3],
                }
            )
            continue
        accepted.append(candidate)
    return accepted, rejected


def target_me_shadow_policy_metrics(
    *,
    batch_utterances: list[dict[str, Any]],
    live_turns_rows: list[dict[str, Any]],
    target_me_rows: list[dict[str, Any]],
    baseline_missing_rows: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    metrics: dict[str, Any] = {}
    examples: dict[str, list[dict[str, Any]]] = {}
    turns_by_policy: dict[str, list[dict[str, Any]]] = {}
    baseline_missing_seconds = round(sum(safe_float(row.get("duration_sec")) for row in baseline_missing_rows), 3)
    baseline_order_mismatches = order_mismatch_rows_for_turns(live_turns_rows, batch_utterances)
    baseline_role_order_mismatches = order_mismatch_rows_for_turns(
        live_turns_rows,
        batch_utterances,
        same_role_only=True,
        min_token_recall=0.45,
        min_score=0.65,
        match_mode="target_me_shadow_baseline_role_constrained_strict",
    )
    baseline_contentful_role_order_mismatches = order_mismatch_rows_for_turns(
        live_turns_rows,
        batch_utterances,
        same_role_only=True,
        contentful_only=True,
        min_token_recall=0.45,
        min_score=0.65,
        match_mode="target_me_shadow_baseline_role_constrained_contentful",
    )
    rejected_by_policy: dict[str, list[dict[str, Any]]] = {}
    for policy in TARGET_ME_RESCUE_POLICIES:
        policy_turns = target_me_shadow_turns(target_me_rows, policy)
        if policy in TARGET_ME_DERIVED_POLICY_BASE:
            policy_turns, rejected = timeline_safe_target_me_turns(
                candidates=policy_turns,
                live_turns_rows=live_turns_rows,
                batch_utterances=batch_utterances,
                baseline_contentful_role_order_mismatch_count=len(baseline_contentful_role_order_mismatches),
            )
            rejected_by_policy[policy] = rejected
        policy_seconds = round(sum(safe_float(row.get("end")) - safe_float(row.get("start")) for row in policy_turns), 3)
        missing_after = local_missing_rows_for_turns(batch_utterances, live_turns_rows + policy_turns)
        missing_after_seconds = round(sum(safe_float(row.get("duration_sec")) for row in missing_after), 3)
        remote_leak = remote_leak_rows_for_turns(policy_turns, batch_utterances)
        order_mismatches = order_mismatch_rows_for_turns(live_turns_rows + policy_turns, batch_utterances)
        role_order_mismatches = order_mismatch_rows_for_turns(
            live_turns_rows + policy_turns,
            batch_utterances,
            same_role_only=True,
            min_token_recall=0.45,
            min_score=0.65,
            match_mode=f"target_me_shadow_{policy}_role_constrained_strict",
        )
        contentful_role_order_mismatches = order_mismatch_rows_for_turns(
            live_turns_rows + policy_turns,
            batch_utterances,
            same_role_only=True,
            contentful_only=True,
            min_token_recall=0.45,
            min_score=0.65,
            match_mode=f"target_me_shadow_{policy}_role_constrained_contentful",
        )
        base = f"live_target_me_shadow_policy_{policy}"
        metrics[f"{base}_candidate_segment_count"] = len(policy_turns)
        metrics[f"{base}_candidate_seconds"] = policy_seconds
        metrics[f"{base}_missing_me_seconds_after"] = missing_after_seconds
        metrics[f"{base}_missing_me_recovered_seconds"] = round(
            max(0.0, baseline_missing_seconds - missing_after_seconds),
            3,
        )
        metrics[f"{base}_suspected_remote_leak_in_me_count"] = len(remote_leak)
        metrics[f"{base}_suspected_remote_leak_in_me_seconds"] = round(
            sum(safe_float(row.get("duration_sec")) for row in remote_leak),
            3,
        )
        metrics[f"{base}_order_mismatch_count"] = len(order_mismatches)
        metrics[f"{base}_role_constrained_order_mismatch_count"] = len(role_order_mismatches)
        metrics[f"{base}_contentful_role_constrained_order_mismatch_count"] = len(contentful_role_order_mismatches)
        metrics[f"{base}_order_mismatch_delta_count"] = len(order_mismatches) - len(baseline_order_mismatches)
        metrics[f"{base}_role_constrained_order_mismatch_delta_count"] = (
            len(role_order_mismatches) - len(baseline_role_order_mismatches)
        )
        metrics[f"{base}_contentful_role_constrained_order_mismatch_delta_count"] = (
            len(contentful_role_order_mismatches) - len(baseline_contentful_role_order_mismatches)
        )
        examples[policy] = policy_turns[:20]
        examples[f"{policy}_remote_leak"] = remote_leak[:20]
        examples[f"{policy}_order_mismatches"] = order_mismatches[:20]
        turns_by_policy[policy] = policy_turns
        if policy in rejected_by_policy:
            rejected = rejected_by_policy[policy]
            metrics[f"{base}_rejected_candidate_count"] = len(rejected)
            metrics[f"{base}_rejected_candidate_seconds"] = round(
                sum(safe_float(row.get("duration_sec")) for row in rejected),
                3,
            )
            reason_counts: dict[str, int] = {}
            reason_seconds: dict[str, float] = {}
            for row in rejected:
                reason = str(row.get("reason") or "unknown")
                reason_counts[reason] = reason_counts.get(reason, 0) + 1
                reason_seconds[reason] = reason_seconds.get(reason, 0.0) + safe_float(row.get("duration_sec"))
            for reason, count in sorted(reason_counts.items()):
                metrics[f"{base}_rejected_{reason}_count"] = count
                metrics[f"{base}_rejected_{reason}_seconds"] = round(reason_seconds.get(reason, 0.0), 3)
            examples[f"{policy}_rejected"] = rejected[:20]
    return metrics, examples, turns_by_policy


def segment_role_decision_key(row: dict[str, Any]) -> tuple[float, float, str]:
    return (
        round(safe_float(row.get("start")), 3),
        round(safe_float(row.get("end")), 3),
        clean_text(str(row.get("text") or "")),
    )


def batch_role_label_for_interval(batch_utterances: list[dict[str, Any]], start: float, end: float) -> dict[str, Any]:
    me_overlap = 0.0
    remote_overlap = 0.0
    me_ids: list[str] = []
    remote_ids: list[str] = []
    for row in batch_utterances:
        row_overlap = interval_overlap(start, end, safe_float(row.get("start")), safe_float(row.get("end")))
        if row_overlap <= 0:
            continue
        if row.get("role") == "Me":
            me_overlap += row_overlap
            me_ids.append(str(row.get("id") or ""))
        elif row.get("role") == "Colleagues":
            remote_overlap += row_overlap
            remote_ids.append(str(row.get("id") or ""))
    duration = max(0.0, end - start)
    me_overlap_capped = min(me_overlap, duration)
    remote_overlap_capped = min(remote_overlap, duration)
    if me_overlap_capped <= 0.2 and remote_overlap_capped <= 0.2:
        label = "none"
    elif (
        me_overlap_capped > 0.2
        and remote_overlap_capped > 0.2
        and min(me_overlap_capped, remote_overlap_capped) / max(me_overlap_capped, remote_overlap_capped) >= 0.75
    ):
        label = "mixed"
    elif me_overlap_capped > remote_overlap_capped:
        label = "me_dominant"
    else:
        label = "remote_dominant"
    return {
        "label": label,
        "me_overlap_sec": round(me_overlap, 3),
        "remote_overlap_sec": round(remote_overlap, 3),
        "duration_sec": round(duration, 3),
        "me_coverage_ratio": round(me_overlap_capped / duration, 6) if duration > 0 else 0.0,
        "remote_coverage_ratio": round(remote_overlap_capped / duration, 6) if duration > 0 else 0.0,
        "batch_me_ids": [item for item in me_ids if item],
        "batch_remote_ids": [item for item in remote_ids if item],
    }


def overlapping_tokens_for_rows(rows: list[dict[str, Any]], start: float, end: float, guard_sec: float = 1.0) -> list[str]:
    result: list[str] = []
    guarded_start = start - guard_sec
    guarded_end = end + guard_sec
    for row in rows:
        row_start = safe_float(row.get("start"))
        row_end = safe_float(row.get("end"), row_start)
        if interval_overlap(guarded_start, guarded_end, row_start, row_end) > 0:
            result.extend(tokens(str(row.get("text") or "")))
    return result


def text_features_against_remote(text: str, remote_tokens: list[str]) -> dict[str, Any]:
    mic_tokens = tokens(text)
    unique_tokens = counter_unique_tokens(mic_tokens, remote_tokens)
    return {
        "token_count": len(mic_tokens),
        "overlapping_remote_token_count": len(remote_tokens),
        "unique_token_count": len(unique_tokens),
        "unique_tokens": unique_tokens[:12],
        "mic_token_recall_in_overlapping_remote": round(bag_recall(mic_tokens, remote_tokens) or 0.0, 6),
        "overlapping_remote_token_recall_in_mic": round(bag_recall(remote_tokens, mic_tokens) or 0.0, 6),
    }


def live_me_remote_overlap_filter_decision(
    turn: dict[str, Any],
    remote_turns: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if turn.get("role") != "Me":
        return None
    text = clean_text(str(turn.get("text") or ""))
    start = safe_float(turn.get("start"))
    end = safe_float(turn.get("end"), start)
    duration = max(0.0, end - start)
    mic_tokens = tokens(text)
    remote_tokens = overlapping_tokens_for_rows(remote_turns, start, end, guard_sec=1.0)
    features = text_features_against_remote(text, remote_tokens)
    mic_in_remote = safe_float(features.get("mic_token_recall_in_overlapping_remote"))
    remote_in_mic = safe_float(features.get("overlapping_remote_token_recall_in_mic"))
    should_remove = (
        duration >= 3.0
        and len(mic_tokens) >= 5
        and len(remote_tokens) >= 5
        and (mic_in_remote >= 0.70 or remote_in_mic >= 0.75)
    )
    if not should_remove:
        return None
    return {
        "schema": "murmurmark.live_me_remote_overlap_filter_decision/v1",
        "decision": "drop_live_me_shadow_only",
        "reason": "live_me_text_overlaps_contemporary_live_remote",
        "start": round(start, 3),
        "end": round(end, 3),
        "duration": round(duration, 3),
        "thresholds": {
            "min_duration_sec": 3.0,
            "min_mic_tokens": 5,
            "min_overlapping_remote_tokens": 5,
            "mic_token_recall_in_overlapping_remote": 0.70,
            "overlapping_remote_token_recall_in_mic": 0.75,
        },
        "features": features,
    }


def read_global_asr_segments(session: Path, source: dict[str, Any]) -> list[dict[str, Any]]:
    asr = source.get("asr") if isinstance(source.get("asr"), dict) else {}
    asr_json = resolve_session_path(session, asr.get("json"))
    if asr_json is None:
        return []
    data = read_json(asr_json)
    if not isinstance(data, dict):
        return []
    clip_start = safe_float(source.get("clip_start_sec"))
    hard_start = safe_float(source.get("hard_start_sec"))
    hard_end = safe_float(source.get("hard_end_sec"), hard_start)
    rows: list[dict[str, Any]] = []
    for item in data.get("transcription") or []:
        if not isinstance(item, dict):
            continue
        offsets = item.get("offsets") if isinstance(item.get("offsets"), dict) else {}
        local_start_ms = safe_float(offsets.get("from"))
        local_end_ms = safe_float(offsets.get("to"), local_start_ms)
        start = clip_start + local_start_ms / 1000.0
        end = clip_start + local_end_ms / 1000.0
        center = (start + end) / 2.0
        if not (hard_start <= center < hard_end):
            continue
        text = clean_text(str(item.get("text") or ""))
        if text:
            rows.append({"start": round(start, 3), "end": round(max(end, start), 3), "text": text})
    return rows


def suppressed_mic_rescue_policy_labels(row: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    token_count = safe_int(row.get("token_count"))
    unique_count = safe_int(row.get("segment_gate_unique_token_count"))
    mic_in_remote = safe_float(row.get("segment_gate_mic_token_recall_in_overlapping_remote"))
    remote_in_mic = safe_float(row.get("segment_gate_overlapping_remote_token_recall_in_mic"))
    remote_token_count = safe_int(row.get("segment_gate_overlapping_remote_token_count"))
    mic_db = row.get("audio_mic_clean_rms_db")
    remote_db = row.get("audio_remote_rms_db")
    mic_minus_remote_db = row.get("audio_mic_minus_remote_rms_db")
    corr = row.get("audio_mic_remote_zero_lag_abs_corr")
    mic_db_value = safe_float(mic_db, -120.0) if mic_db is not None else -120.0
    remote_db_value = safe_float(remote_db, -120.0) if remote_db is not None else -120.0
    mic_minus_remote_value = safe_float(mic_minus_remote_db, 0.0) if mic_minus_remote_db is not None else 0.0
    corr_value = safe_float(corr, 1.0) if corr is not None else 1.0
    if row.get("segment_gate_status") == "kept":
        labels.append("current_text_segment_gate")
    if token_count >= 5 and unique_count >= 4 and mic_in_remote <= 0.35 and remote_in_mic <= 0.45:
        labels.append("strict_text_unique_v1")
    if token_count >= 3 and remote_token_count == 0:
        labels.append("remote_silent_text_v1")
    if token_count >= 3 and mic_db_value >= -58.0 and remote_db_value <= -48.0:
        labels.append("audio_remote_quiet_v1")
    if (
        token_count >= 4
        and mic_db_value >= -56.0
        and mic_minus_remote_value >= 8.0
        and mic_in_remote <= 0.55
    ):
        labels.append("audio_mic_dominant_v1")
    if (
        token_count >= 4
        and mic_db_value >= -56.0
        and corr_value <= 0.20
        and mic_in_remote <= 0.55
        and unique_count >= 2
    ):
        labels.append("audio_low_coherence_v1")
    if token_count >= 3 and mic_db_value >= -58.0 and corr_value <= 0.08 and remote_in_mic <= 0.45:
        labels.append("audio_low_corr_text_guard_v1")
    if "remote_silent_text_v1" in labels or "audio_mic_dominant_v1" in labels:
        labels.append("audio_safe_union_v1")
    if row.get("batch_role_label") in {"me_dominant", "mixed"}:
        labels.append("batch_oracle_local_ceiling")
    return labels


def summarize_suppressed_mic_rescue_policies(rows: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    examples: dict[str, list[dict[str, Any]]] = {policy: [] for policy in SUPPRESSED_MIC_RESCUE_POLICIES}
    total_local_seconds = sum(
        safe_float(row.get("duration_sec"))
        for row in rows
        if row.get("batch_role_label") in {"me_dominant", "mixed"}
    )
    for policy in SUPPRESSED_MIC_RESCUE_POLICIES:
        selected = [row for row in rows if policy in (row.get("rescue_policy_candidates") or [])]
        selected_seconds = sum(safe_float(row.get("duration_sec")) for row in selected)
        me_seconds = sum(
            safe_float(row.get("duration_sec")) for row in selected if row.get("batch_role_label") == "me_dominant"
        )
        mixed_seconds = sum(
            safe_float(row.get("duration_sec")) for row in selected if row.get("batch_role_label") == "mixed"
        )
        remote_seconds = sum(
            safe_float(row.get("duration_sec")) for row in selected if row.get("batch_role_label") == "remote_dominant"
        )
        none_seconds = sum(
            safe_float(row.get("duration_sec")) for row in selected if row.get("batch_role_label") == "none"
        )
        local_seconds = me_seconds + mixed_seconds
        remote_risk_seconds = remote_seconds + none_seconds
        result[f"live_rescue_policy_{policy}_selected_segment_count"] = len(selected)
        result[f"live_rescue_policy_{policy}_selected_seconds"] = round(selected_seconds, 3)
        result[f"live_rescue_policy_{policy}_local_seconds"] = round(local_seconds, 3)
        result[f"live_rescue_policy_{policy}_me_dominant_seconds"] = round(me_seconds, 3)
        result[f"live_rescue_policy_{policy}_mixed_seconds"] = round(mixed_seconds, 3)
        result[f"live_rescue_policy_{policy}_remote_risk_seconds"] = round(remote_risk_seconds, 3)
        result[f"live_rescue_policy_{policy}_precision_proxy"] = (
            round(local_seconds / selected_seconds, 6) if selected_seconds > 0 else None
        )
        result[f"live_rescue_policy_{policy}_local_recall_proxy"] = (
            round(local_seconds / total_local_seconds, 6) if total_local_seconds > 0 else None
        )
        for row in selected[:10]:
            examples[policy].append(
                {
                    "chunk_index": row.get("chunk_index"),
                    "start": row.get("start"),
                    "end": row.get("end"),
                    "duration_sec": row.get("duration_sec"),
                    "text": row.get("text"),
                    "batch_role_label": row.get("batch_role_label"),
                    "segment_gate_unique_token_count": row.get("segment_gate_unique_token_count"),
                    "segment_gate_mic_token_recall_in_overlapping_remote": row.get(
                        "segment_gate_mic_token_recall_in_overlapping_remote"
                    ),
                    "segment_gate_overlapping_remote_token_recall_in_mic": row.get(
                        "segment_gate_overlapping_remote_token_recall_in_mic"
                    ),
                    "audio_mic_clean_rms_db": row.get("audio_mic_clean_rms_db"),
                    "audio_remote_rms_db": row.get("audio_remote_rms_db"),
                    "audio_mic_minus_remote_rms_db": row.get("audio_mic_minus_remote_rms_db"),
                    "audio_mic_remote_zero_lag_abs_corr": row.get("audio_mic_remote_zero_lag_abs_corr"),
                }
            )
    return {"metrics": result, "examples": examples}


def read_suppressed_mic_asr_segment_audit(
    session: Path,
    chunks: list[dict[str, Any]],
    batch_utterances: list[dict[str, Any]],
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    label_counts: Counter[str] = Counter()
    label_seconds: Counter[str] = Counter()
    candidate_label_counts: Counter[str] = Counter()
    candidate_label_seconds: Counter[str] = Counter()
    audio_cache: dict[Path, tuple[int, np.ndarray] | None] = {}
    for chunk in chunks:
        try:
            index = int(chunk.get("index") or 0)
        except (TypeError, ValueError):
            index = 0
        mic = chunk.get("mic")
        if not isinstance(mic, dict):
            continue
        remote = chunk.get("remote") if isinstance(chunk.get("remote"), dict) else {}
        mic_audio_path = resolve_source_path(session, mic, ("asr_wav", "wav", "input"))
        remote_audio_path = resolve_source_path(session, remote, ("wav", "input"))
        mic_audio = read_audio(mic_audio_path, audio_cache)
        remote_audio = read_audio(remote_audio_path, audio_cache)
        remote_segments = read_global_asr_segments(session, remote)
        role_gate = mic.get("live_role_gate") if isinstance(mic.get("live_role_gate"), dict) else {}
        if role_gate.get("status") != "suppressed":
            continue
        asr = mic.get("asr") if isinstance(mic.get("asr"), dict) else {}
        asr_json = resolve_session_path(session, asr.get("json"))
        if asr_json is None:
            continue
        data = read_json(asr_json)
        if not isinstance(data, dict):
            continue
        clip_start = safe_float(mic.get("clip_start_sec"))
        hard_start = safe_float(mic.get("hard_start_sec"))
        hard_end = safe_float(mic.get("hard_end_sec"), hard_start)
        segment_gate = mic.get("live_segment_role_gate") if isinstance(mic.get("live_segment_role_gate"), dict) else {}
        decisions: dict[tuple[float, float, str], dict[str, Any]] = {}
        for decision in list(segment_gate.get("kept_segments") or []) + list(segment_gate.get("suppressed_segments") or []):
            if isinstance(decision, dict):
                decisions[segment_role_decision_key(decision)] = decision
        for item in data.get("transcription") or []:
            if not isinstance(item, dict):
                continue
            offsets = item.get("offsets") if isinstance(item.get("offsets"), dict) else {}
            local_start_ms = safe_float(offsets.get("from"))
            local_end_ms = safe_float(offsets.get("to"), local_start_ms)
            start = clip_start + local_start_ms / 1000.0
            end = clip_start + local_end_ms / 1000.0
            center = (start + end) / 2.0
            if not (hard_start <= center < hard_end):
                continue
            text = clean_text(str(item.get("text") or ""))
            if not text:
                continue
            role = batch_role_label_for_interval(batch_utterances, start, end)
            computed_features = text_features_against_remote(
                text,
                overlapping_tokens_for_rows(remote_segments, start, end),
            )
            computed_audio = segment_audio_features(
                mic_audio=mic_audio,
                remote_audio=remote_audio,
                clip_start_sec=clip_start,
                start_sec=start,
                end_sec=end,
            )
            decision = decisions.get((round(start, 3), round(end, 3), text), {})
            label = str(role.get("label") or "none")
            duration = max(0.0, end - start)
            label_counts[label] += 1
            label_seconds[label] += duration
            if decision.get("status") == "kept":
                candidate_label_counts[label] += 1
                candidate_label_seconds[label] += duration
            row = {
                "chunk_index": index,
                "start": round(start, 3),
                "end": round(end, 3),
                "duration_sec": round(duration, 3),
                "text": text,
                "token_count": len(tokens(text)),
                "batch_role_label": label,
                "batch_role_evidence": role,
                "segment_gate_status": decision.get("status") or "not_evaluated",
                "segment_gate_reason": decision.get("reason"),
                "segment_gate_unique_token_count": decision.get("unique_token_count")
                if decision.get("unique_token_count") is not None
                else computed_features.get("unique_token_count"),
                "segment_gate_unique_tokens": decision.get("unique_tokens") or computed_features.get("unique_tokens"),
                "segment_gate_overlapping_remote_token_count": computed_features.get("overlapping_remote_token_count"),
                "segment_gate_mic_token_recall_in_overlapping_remote": decision.get(
                    "mic_token_recall_in_overlapping_remote"
                )
                if decision.get("mic_token_recall_in_overlapping_remote") is not None
                else computed_features.get("mic_token_recall_in_overlapping_remote"),
                "segment_gate_overlapping_remote_token_recall_in_mic": decision.get(
                    "overlapping_remote_token_recall_in_mic"
                )
                if decision.get("overlapping_remote_token_recall_in_mic") is not None
                else computed_features.get("overlapping_remote_token_recall_in_mic"),
                "audio_mic_clean_rms_db": computed_audio.get("mic_clean_rms_db"),
                "audio_remote_rms_db": computed_audio.get("remote_rms_db"),
                "audio_mic_clean_peak_db": computed_audio.get("mic_clean_peak_db"),
                "audio_remote_peak_db": computed_audio.get("remote_peak_db"),
                "audio_mic_minus_remote_rms_db": computed_audio.get("mic_minus_remote_rms_db"),
                "audio_mic_remote_zero_lag_abs_corr": computed_audio.get("mic_remote_zero_lag_abs_corr"),
                "publish_policy": role_gate.get("segment_gate_publish_policy"),
            }
            row["rescue_policy_candidates"] = suppressed_mic_rescue_policy_labels(row)
            rows.append(row)
    examples = [
        row
        for row in rows
        if row.get("batch_role_label") in {"me_dominant", "mixed"} or row.get("segment_gate_status") == "kept"
    ][:30]
    policy_summary = summarize_suppressed_mic_rescue_policies(rows)
    return {
        "segments": rows,
        "examples": examples,
        "policy_examples": policy_summary.get("examples") or {},
        "metrics": {
            "live_suppressed_mic_asr_segment_count": len(rows),
            "live_suppressed_mic_asr_segment_seconds": round(sum(safe_float(row.get("duration_sec")) for row in rows), 3),
            **{
                f"live_suppressed_mic_asr_{label}_segment_count": label_counts[label]
                for label in ("me_dominant", "mixed", "remote_dominant", "none")
            },
            **{
                f"live_suppressed_mic_asr_{label}_segment_seconds": round(label_seconds[label], 3)
                for label in ("me_dominant", "mixed", "remote_dominant", "none")
            },
            **{
                f"live_segment_role_gate_candidate_{label}_segment_count": candidate_label_counts[label]
                for label in ("me_dominant", "mixed", "remote_dominant", "none")
            },
            **{
                f"live_segment_role_gate_candidate_{label}_segment_seconds": round(candidate_label_seconds[label], 3)
                for label in ("me_dominant", "mixed", "remote_dominant", "none")
            },
            **(policy_summary.get("metrics") or {}),
        },
    }


def utterance_tokens_in_interval(utterances: list[dict[str, Any]], start: float, end: float, role: str | None = None) -> list[str]:
    result: list[str] = []
    for row in utterances:
        if role and row.get("role") != role:
            continue
        if interval_overlap(start, end, safe_float(row.get("start")), safe_float(row.get("end"))) <= 0:
            continue
        result.extend(row.get("tokens") or [])
    return result


def utterance_ids_in_interval(utterances: list[dict[str, Any]], start: float, end: float, role: str | None = None) -> list[str]:
    result: list[str] = []
    for row in utterances:
        if role and row.get("role") != role:
            continue
        if interval_overlap(start, end, safe_float(row.get("start")), safe_float(row.get("end"))) <= 0:
            continue
        result.append(str(row.get("id") or ""))
    return [item for item in result if item]


def nested_token_probability(row: dict[str, Any]) -> float | None:
    quality = row.get("quality") if isinstance(row.get("quality"), dict) else {}
    repair = quality.get("repair") if isinstance(quality.get("repair"), dict) else {}
    micro = repair.get("micro_reasr") if isinstance(repair.get("micro_reasr"), dict) else {}
    rows = micro.get("rows") if isinstance(micro.get("rows"), list) else []
    values: list[float] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        try:
            values.append(float(item.get("token_avg_prob")))
        except (TypeError, ValueError):
            continue
    if not values:
        return None
    return sum(values) / len(values)


def suspicious_batch_me_utterance(row: dict[str, Any]) -> bool:
    quality = row.get("quality") if isinstance(row.get("quality"), dict) else {}
    repair = quality.get("repair") if isinstance(quality.get("repair"), dict) else {}
    duration = max(0.0, safe_float(row.get("end")) - safe_float(row.get("start")))
    reason = str(quality.get("decision_reason") or repair.get("reason") or "")
    matched_remote = repair.get("matched_remote_candidate_ids") if isinstance(repair, dict) else None
    token_prob = nested_token_probability(row)
    return bool(
        row.get("role") == "Me"
        and duration <= 1.2
        and len(row.get("tokens") or []) <= 4
        and (
            "crosses_authoritative_remote" in reason
            or (isinstance(matched_remote, list) and len(matched_remote) > 0)
        )
        and (token_prob is None or token_prob < 0.70)
    )


def best_batch_match(
    turn: dict[str, Any],
    batch: list[dict[str, Any]],
    *,
    same_role_only: bool = False,
    contentful_only: bool = False,
) -> dict[str, Any] | None:
    turn_tokens = turn.get("tokens") or []
    if not turn_tokens:
        return None
    if contentful_only and not is_contentful_text(turn.get("text")):
        return None
    candidates: list[dict[str, Any]] = []
    for row in batch:
        if same_role_only and row.get("role") != turn.get("role"):
            continue
        if contentful_only and not is_contentful_text(row.get("text")):
            continue
        row_tokens = row.get("tokens") or []
        if not row_tokens:
            continue
        score = bag_recall(turn_tokens, row_tokens) or 0.0
        overlap = interval_overlap(
            safe_float(turn.get("start")),
            safe_float(turn.get("end")),
            safe_float(row.get("start")),
            safe_float(row.get("end")),
        )
        if overlap > 0:
            score += 0.15
        if row.get("role") == turn.get("role"):
            score += 0.1
        candidate = {
            "batch_id": row.get("id"),
            "batch_start": row.get("start"),
            "batch_end": row.get("end"),
            "batch_role": row.get("role"),
            "score": round(score, 6),
            "token_recall": round(bag_recall(turn_tokens, row_tokens) or 0.0, 6),
            "turn_content_token_count": len(content_tokens(turn_tokens)),
            "batch_content_token_count": len(content_tokens(row_tokens)),
        }
        candidates.append(candidate)
    if not candidates:
        return None
    candidates.sort(
        key=lambda item: (
            safe_float(item.get("score")),
            safe_float(item.get("token_recall")),
            -safe_float(item.get("batch_start")),
        ),
        reverse=True,
    )
    best = dict(candidates[0])
    second_score = safe_float(candidates[1].get("score")) if len(candidates) > 1 else 0.0
    plausible = [
        item
        for item in candidates
        if safe_float(item.get("token_recall")) >= 0.25 and safe_float(item.get("score")) >= 0.35
    ]
    best["second_score"] = round(second_score, 6)
    best["score_margin"] = round(safe_float(best.get("score")) - second_score, 6)
    best["plausible_match_count"] = len(plausible)
    best["ambiguous_match"] = bool(len(plausible) > 1 and safe_float(best.get("score_margin")) < 0.20)
    return best


def matched_turn_rows(
    turns: list[dict[str, Any]],
    batch_utterances: list[dict[str, Any]],
    *,
    same_role_only: bool = False,
    contentful_only: bool = False,
    min_token_recall: float = 0.25,
    min_score: float = 0.0,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for turn in sorted(turns, key=lambda item: (safe_float(item.get("start")), safe_float(item.get("end")), str(item.get("id") or ""))):
        match = best_batch_match(
            turn,
            batch_utterances,
            same_role_only=same_role_only,
            contentful_only=contentful_only,
        )
        if (
            match
            and safe_float(match.get("token_recall")) >= min_token_recall
            and safe_float(match.get("score")) >= min_score
        ):
            result.append({**turn, "match": match})
    return result


def short_text(text: Any, limit: int = 180) -> str:
    value = clean_text(str(text or ""))
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


def order_mismatch_category(previous: dict[str, Any], current: dict[str, Any]) -> str:
    previous_chunk = safe_int(previous.get("chunk_index"))
    current_chunk = safe_int(current.get("chunk_index"))
    previous_end = safe_float(previous.get("end"), safe_float(previous.get("start")))
    current_start = safe_float(current.get("start"))
    if previous_chunk == current_chunk:
        if previous.get("source") == current.get("source"):
            return "same_chunk_same_source_reorder"
        return "same_chunk_cross_source_reorder"
    if current_start < previous_end:
        return "chunk_overlap_context_reorder"
    return "cross_chunk_reorder"


def order_mismatch_min_metric(previous: dict[str, Any], current: dict[str, Any], metric: str) -> float:
    previous_match = previous.get("match") if isinstance(previous.get("match"), dict) else {}
    current_match = current.get("match") if isinstance(current.get("match"), dict) else {}
    return min(safe_float(previous_match.get(metric)), safe_float(current_match.get(metric)))


def order_mismatch_role_conflict(previous: dict[str, Any], current: dict[str, Any]) -> bool:
    previous_match = previous.get("match") if isinstance(previous.get("match"), dict) else {}
    current_match = current.get("match") if isinstance(current.get("match"), dict) else {}
    return previous_match.get("batch_role") != previous.get("role") or current_match.get("batch_role") != current.get("role")


def order_mismatch_primary_risk(previous: dict[str, Any], current: dict[str, Any], category: str) -> str:
    if order_mismatch_role_conflict(previous, current):
        return "role_conflict_or_remote_leak"
    min_recall = order_mismatch_min_metric(previous, current, "token_recall")
    min_score = order_mismatch_min_metric(previous, current, "score")
    if min_recall < 0.45 or min_score < 0.65:
        return "weak_text_match_possible_false_positive"
    if category == "same_chunk_same_source_reorder":
        return "same_source_timeline_reorder"
    if category == "same_chunk_cross_source_reorder":
        return "cross_source_timeline_reorder"
    if category == "chunk_overlap_context_reorder":
        return "chunk_overlap_context_reorder"
    return "cross_chunk_timeline_reorder"


def order_mismatch_confidence(previous: dict[str, Any], current: dict[str, Any]) -> str:
    if order_mismatch_role_conflict(previous, current):
        return "role_conflict"
    min_recall = order_mismatch_min_metric(previous, current, "token_recall")
    min_score = order_mismatch_min_metric(previous, current, "score")
    if min_recall >= 0.75 and min_score >= 0.85:
        return "high"
    if min_recall >= 0.45 and min_score >= 0.65:
        return "medium"
    return "low"


def order_mismatch_ambiguous_match(previous: dict[str, Any], current: dict[str, Any]) -> bool:
    previous_match = previous.get("match") if isinstance(previous.get("match"), dict) else {}
    current_match = current.get("match") if isinstance(current.get("match"), dict) else {}
    return bool(previous_match.get("ambiguous_match") or current_match.get("ambiguous_match"))


def order_mismatch_min_score_margin(previous: dict[str, Any], current: dict[str, Any]) -> float:
    previous_match = previous.get("match") if isinstance(previous.get("match"), dict) else {}
    current_match = current.get("match") if isinstance(current.get("match"), dict) else {}
    return min(safe_float(previous_match.get("score_margin")), safe_float(current_match.get("score_margin")))


def order_mismatch_turn_payload(turn: dict[str, Any]) -> dict[str, Any]:
    match = turn.get("match") if isinstance(turn.get("match"), dict) else {}
    return {
        "live_id": turn.get("id"),
        "chunk_index": turn.get("chunk_index"),
        "segment_index": turn.get("segment_index"),
        "source": turn.get("source"),
        "role": turn.get("role"),
        "start": turn.get("start"),
        "end": turn.get("end"),
        "text": short_text(turn.get("text")),
        "batch_id": match.get("batch_id"),
        "batch_role": match.get("batch_role"),
        "batch_start": match.get("batch_start"),
        "batch_end": match.get("batch_end"),
        "token_recall": match.get("token_recall"),
        "score": match.get("score"),
        "turn_content_token_count": match.get("turn_content_token_count"),
        "batch_content_token_count": match.get("batch_content_token_count"),
        "score_margin": match.get("score_margin"),
        "second_score": match.get("second_score"),
        "plausible_match_count": match.get("plausible_match_count"),
        "ambiguous_match": match.get("ambiguous_match"),
    }


def order_mismatch_rows_for_turns(
    turns: list[dict[str, Any]],
    batch_utterances: list[dict[str, Any]],
    *,
    same_role_only: bool = False,
    contentful_only: bool = False,
    min_token_recall: float = 0.25,
    min_score: float = 0.0,
    match_mode: str = "best_overall",
) -> list[dict[str, Any]]:
    order_mismatches: list[dict[str, Any]] = []
    previous: dict[str, Any] | None = None
    for turn in matched_turn_rows(
        turns,
        batch_utterances,
        same_role_only=same_role_only,
        contentful_only=contentful_only,
        min_token_recall=min_token_recall,
        min_score=min_score,
    ):
        if previous is not None:
            previous_batch_start = safe_float((previous.get("match") or {}).get("batch_start"))
            current_batch_start = safe_float((turn.get("match") or {}).get("batch_start"))
            if current_batch_start + 1.0 < previous_batch_start:
                category = order_mismatch_category(previous, turn)
                primary_risk = order_mismatch_primary_risk(previous, turn, category)
                confidence = order_mismatch_confidence(previous, turn)
                order_mismatches.append(
                    {
                        "previous_live_id": previous.get("id"),
                        "current_live_id": turn.get("id"),
                        "match_mode": match_mode,
                        "category": category,
                        "primary_risk": primary_risk,
                        "confidence": confidence,
                        "match_ambiguity": "ambiguous" if order_mismatch_ambiguous_match(previous, turn) else "unambiguous",
                        "previous_batch_start": round(previous_batch_start, 3),
                        "current_batch_start": round(current_batch_start, 3),
                        "batch_start_delta_sec": round(current_batch_start - previous_batch_start, 3),
                        "live_start_delta_sec": round(safe_float(turn.get("start")) - safe_float(previous.get("start")), 3),
                        "min_token_recall": round(order_mismatch_min_metric(previous, turn, "token_recall"), 6),
                        "min_score": round(order_mismatch_min_metric(previous, turn, "score"), 6),
                        "min_score_margin": round(order_mismatch_min_score_margin(previous, turn), 6),
                        "same_chunk": previous.get("chunk_index") == turn.get("chunk_index"),
                        "same_source": previous.get("source") == turn.get("source"),
                        "source_pair": f"{previous.get('source')}->{turn.get('source')}",
                        "role_pair": f"{previous.get('role')}->{turn.get('role')}",
                        "role_mismatch_in_pair": order_mismatch_role_conflict(previous, turn),
                        "previous": order_mismatch_turn_payload(previous),
                        "current": order_mismatch_turn_payload(turn),
                    }
                )
        previous = turn
    return order_mismatches


def order_mismatch_category_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(str(row.get("category") or "unknown") for row in rows)
    return dict(sorted(counts.items()))


def order_mismatch_field_counts(rows: list[dict[str, Any]], field: str) -> dict[str, int]:
    counts = Counter(str(row.get(field) or "unknown") for row in rows)
    return dict(sorted(counts.items()))


def remote_leak_rows_for_turns(turns: list[dict[str, Any]], batch_utterances: list[dict[str, Any]]) -> list[dict[str, Any]]:
    remote_leak: list[dict[str, Any]] = []
    for turn in turns:
        if turn.get("role") != "Me" or len(turn.get("tokens") or []) < 3:
            continue
        same_role_tokens = utterance_tokens_in_interval(
            batch_utterances,
            safe_float(turn.get("start")),
            safe_float(turn.get("end")),
            "Me",
        )
        remote_tokens = utterance_tokens_in_interval(
            batch_utterances,
            safe_float(turn.get("start")),
            safe_float(turn.get("end")),
            "Colleagues",
        )
        same_recall = bag_recall(turn.get("tokens") or [], same_role_tokens) or 0.0
        remote_recall = bag_recall(turn.get("tokens") or [], remote_tokens) or 0.0
        if remote_recall >= 0.55 and same_recall < 0.35:
            remote_leak.append(
                {
                    "live_id": turn.get("id"),
                    "start": turn.get("start"),
                    "end": turn.get("end"),
                    "duration_sec": round(safe_float(turn.get("end")) - safe_float(turn.get("start")), 3),
                    "same_role_recall": round(same_recall, 6),
                    "remote_role_recall": round(remote_recall, 6),
                    "text": turn.get("text"),
                }
            )
    return remote_leak


def local_missing_rows_for_turns(batch_utterances: list[dict[str, Any]], turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    local_missing: list[dict[str, Any]] = []
    for row in batch_utterances:
        if row.get("role") != "Me" or len(row.get("tokens") or []) < 2:
            continue
        live_tokens_for_me = utterance_tokens_in_interval(turns, safe_float(row.get("start")), safe_float(row.get("end")), "Me")
        recall = bag_recall(row.get("tokens") or [], live_tokens_for_me) or 0.0
        if recall < 0.35 and not suspicious_batch_me_utterance(row):
            local_missing.append(
                {
                    "batch_id": row.get("id"),
                    "start": row.get("start"),
                    "end": row.get("end"),
                    "duration_sec": round(safe_float(row.get("end")) - safe_float(row.get("start")), 3),
                    "recall_in_live_me": round(recall, 6),
                    "text": row.get("text"),
                }
            )
    return local_missing


def local_missing_diagnostics_for_turns(
    *,
    batch_utterances: list[dict[str, Any]],
    turns: list[dict[str, Any]],
    suppressed_mic_turns: list[dict[str, Any]],
    target_me_turns_by_policy: dict[str, list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for batch_row in batch_utterances:
        if batch_row.get("role") != "Me" or len(batch_row.get("tokens") or []) < 2:
            continue
        if suspicious_batch_me_utterance(batch_row):
            continue
        start = safe_float(batch_row.get("start"))
        end = safe_float(batch_row.get("end"), start)
        live_tokens_for_me = utterance_tokens_in_interval(turns, start, end, "Me")
        live_recall = bag_recall(batch_row.get("tokens") or [], live_tokens_for_me) or 0.0
        if live_recall >= 0.35:
            continue
        suppressed_tokens = utterance_tokens_in_interval(suppressed_mic_turns, start, end, "Me")
        suppressed_recall = bag_recall(batch_row.get("tokens") or [], suppressed_tokens) or 0.0
        target_me_candidate_policies: list[str] = []
        for policy, policy_turns in sorted(target_me_turns_by_policy.items()):
            policy_tokens = utterance_tokens_in_interval(policy_turns, start, end, "Me")
            if (bag_recall(batch_row.get("tokens") or [], policy_tokens) or 0.0) >= 0.35:
                target_me_candidate_policies.append(policy)
        rows.append(
            {
                "batch_id": batch_row.get("id"),
                "start": batch_row.get("start"),
                "end": batch_row.get("end"),
                "duration_sec": round(max(0.0, end - start), 3),
                "recall_in_live_me": round(live_recall, 6),
                "recall_in_suppressed_mic": round(suppressed_recall, 6),
                "suppressed_mic_turn_ids": utterance_ids_in_interval(suppressed_mic_turns, start, end, "Me"),
                "target_me_candidate_policies": target_me_candidate_policies,
                "text": batch_row.get("text"),
            }
        )

    visible_rows = [row for row in rows if safe_float(row.get("recall_in_suppressed_mic")) >= 0.35]
    not_visible_rows = [row for row in rows if safe_float(row.get("recall_in_suppressed_mic")) < 0.35]
    target_me_rows = [row for row in rows if row.get("target_me_candidate_policies")]
    without_target_me_rows = [row for row in rows if not row.get("target_me_candidate_policies")]
    visible_with_target_me_rows = [
        row for row in visible_rows if row.get("target_me_candidate_policies")
    ]
    visible_without_target_me_rows = [
        row for row in visible_rows if not row.get("target_me_candidate_policies")
    ]
    not_visible_with_target_me_rows = [
        row for row in not_visible_rows if row.get("target_me_candidate_policies")
    ]
    not_visible_without_target_me_rows = [
        row for row in not_visible_rows if not row.get("target_me_candidate_policies")
    ]

    def row_seconds(items: list[dict[str, Any]]) -> float:
        return round(sum(safe_float(row.get("duration_sec")) for row in items), 3)

    return rows, {
        "live_missing_me_visible_in_suppressed_mic_count": len(visible_rows),
        "live_missing_me_visible_in_suppressed_mic_seconds": row_seconds(visible_rows),
        "live_missing_me_not_visible_in_suppressed_mic_count": len(not_visible_rows),
        "live_missing_me_not_visible_in_suppressed_mic_seconds": row_seconds(not_visible_rows),
        "live_missing_me_with_target_me_candidate_count": len(target_me_rows),
        "live_missing_me_with_target_me_candidate_seconds": row_seconds(target_me_rows),
        "live_missing_me_without_target_me_candidate_count": len(without_target_me_rows),
        "live_missing_me_without_target_me_candidate_seconds": row_seconds(without_target_me_rows),
        "live_missing_me_visible_with_target_me_candidate_count": len(visible_with_target_me_rows),
        "live_missing_me_visible_with_target_me_candidate_seconds": row_seconds(visible_with_target_me_rows),
        "live_missing_me_visible_without_target_me_candidate_count": len(visible_without_target_me_rows),
        "live_missing_me_visible_without_target_me_candidate_seconds": row_seconds(visible_without_target_me_rows),
        "live_missing_me_not_visible_with_target_me_candidate_count": len(not_visible_with_target_me_rows),
        "live_missing_me_not_visible_with_target_me_candidate_seconds": row_seconds(not_visible_with_target_me_rows),
        "live_missing_me_not_visible_without_target_me_candidate_count": len(not_visible_without_target_me_rows),
        "live_missing_me_not_visible_without_target_me_candidate_seconds": row_seconds(not_visible_without_target_me_rows),
    }


def visible_suppressed_mic_oracle_turns(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    turns: list[dict[str, Any]] = []
    for index, row in enumerate(segments, start=1):
        if row.get("batch_role_label") not in {"me_dominant", "mixed"}:
            continue
        text = clean_text(str(row.get("text") or ""))
        start = safe_float(row.get("start"))
        end = safe_float(row.get("end"), start)
        if not text or end <= start or len(tokens(text)) < 2:
            continue
        turns.append(
            {
                "id": f"live_visible_suppressed_mic_oracle_{safe_int(row.get('chunk_index')):06d}_{index:06d}",
                "chunk_index": row.get("chunk_index"),
                "source": "mic_suppressed_visible_oracle",
                "role": "Me",
                "start": start,
                "end": end,
                "text": text,
                "tokens": tokens(text),
                "batch_role_label": row.get("batch_role_label"),
                "rescue_policy_candidates": row.get("rescue_policy_candidates") or [],
            }
        )
    return sorted(turns, key=lambda item: (safe_float(item.get("start")), safe_float(item.get("end")), str(item.get("id") or "")))


def online_suppressed_mic_policy_turns(segments: list[dict[str, Any]], policy: str) -> list[dict[str, Any]]:
    turns: list[dict[str, Any]] = []
    for index, row in enumerate(segments, start=1):
        if policy not in (row.get("rescue_policy_candidates") or []):
            continue
        text = clean_text(str(row.get("text") or ""))
        start = safe_float(row.get("start"))
        end = safe_float(row.get("end"), start)
        if not text or end <= start or len(tokens(text)) < 2:
            continue
        turns.append(
            {
                "id": f"live_suppressed_mic_{policy}_{safe_int(row.get('chunk_index')):06d}_{index:06d}",
                "chunk_index": row.get("chunk_index"),
                "source": f"mic_suppressed_{policy}",
                "role": "Me",
                "start": start,
                "end": end,
                "text": text,
                "tokens": tokens(text),
                "suppressed_mic_policy": policy,
                "rescue_policy_candidates": row.get("rescue_policy_candidates") or [],
            }
        )
    return sorted(turns, key=lambda item: (safe_float(item.get("start")), safe_float(item.get("end")), str(item.get("id") or "")))


def timeline_safe_visible_suppressed_mic_turns(
    *,
    candidates: list[dict[str, Any]],
    base_turns: list[dict[str, Any]],
    batch_utterances: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    baseline_missing_seconds = round(
        sum(safe_float(row.get("duration_sec")) for row in local_missing_rows_for_turns(batch_utterances, base_turns)),
        3,
    )
    baseline_contentful_order_count = len(
        order_mismatch_rows_for_turns(
            base_turns,
            batch_utterances,
            same_role_only=True,
            contentful_only=True,
            min_token_recall=0.45,
            min_score=0.65,
            match_mode="visible_suppressed_mic_oracle_baseline_contentful",
        )
    )
    current_missing_seconds = baseline_missing_seconds
    for candidate in candidates:
        trial = sorted(
            base_turns + accepted + [candidate],
            key=lambda item: (safe_float(item.get("start")), safe_float(item.get("end")), str(item.get("id") or "")),
        )
        remote_leak = remote_leak_rows_for_turns(trial, batch_utterances)
        if remote_leak:
            rejected.append(
                {
                    "id": candidate.get("id"),
                    "reason": "would_add_suspected_remote_leak",
                    "text": candidate.get("text"),
                    "remote_leak": remote_leak[:3],
                }
            )
            continue
        contentful_order_mismatches = order_mismatch_rows_for_turns(
            trial,
            batch_utterances,
            same_role_only=True,
            contentful_only=True,
            min_token_recall=0.45,
            min_score=0.65,
            match_mode="visible_suppressed_mic_oracle_contentful",
        )
        if len(contentful_order_mismatches) > baseline_contentful_order_count:
            rejected.append(
                {
                    "id": candidate.get("id"),
                    "reason": "would_add_contentful_order_mismatch",
                    "text": candidate.get("text"),
                    "contentful_order_mismatch_count": len(contentful_order_mismatches),
                    "baseline_contentful_order_mismatch_count": baseline_contentful_order_count,
                    "examples": contentful_order_mismatches[:3],
                }
            )
            continue
        missing_after_seconds = round(
            sum(safe_float(row.get("duration_sec")) for row in local_missing_rows_for_turns(batch_utterances, trial)),
            3,
        )
        if missing_after_seconds >= current_missing_seconds:
            rejected.append(
                {
                    "id": candidate.get("id"),
                    "reason": "no_local_recall_gain",
                    "text": candidate.get("text"),
                    "missing_me_seconds_before": current_missing_seconds,
                    "missing_me_seconds_after": missing_after_seconds,
                }
            )
            continue
        accepted.append(candidate)
        current_missing_seconds = missing_after_seconds
    return accepted, rejected


def rescued_turns_for_policy(segments: list[dict[str, Any]], policy: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, row in enumerate(segments, start=1):
        if policy not in (row.get("rescue_policy_candidates") or []):
            continue
        text = clean_text(str(row.get("text") or ""))
        if not text:
            continue
        result.append(
            {
                "id": f"live_rescue_{policy}_{index:06d}",
                "chunk_index": row.get("chunk_index"),
                "source": f"mic_rescue_{policy}",
                "role": "Me",
                "start": row.get("start"),
                "end": row.get("end"),
                "text": text,
                "tokens": tokens(text),
            }
        )
    return result


def rescue_policy_counterfactual_metrics(
    batch_utterances: list[dict[str, Any]],
    live_turns_rows: list[dict[str, Any]],
    suppressed_segment_rows: list[dict[str, Any]],
    baseline_missing_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    baseline_missing_seconds = round(sum(safe_float(row.get("duration_sec")) for row in baseline_missing_rows), 3)
    for policy in SUPPRESSED_MIC_RESCUE_POLICIES:
        if policy == "batch_oracle_local_ceiling":
            continue
        policy_turns = rescued_turns_for_policy(suppressed_segment_rows, policy)
        missing_after = local_missing_rows_for_turns(batch_utterances, live_turns_rows + policy_turns)
        missing_after_seconds = round(sum(safe_float(row.get("duration_sec")) for row in missing_after), 3)
        result[f"live_rescue_policy_{policy}_missing_me_seconds_after"] = missing_after_seconds
        result[f"live_rescue_policy_{policy}_missing_me_recovered_seconds"] = round(
            max(0.0, baseline_missing_seconds - missing_after_seconds),
            3,
        )
    return result


def parity_metrics_for_turns(
    turns: list[dict[str, Any]],
    batch_utterances: list[dict[str, Any]],
    *,
    match_mode_prefix: str,
) -> dict[str, Any]:
    order_mismatches = order_mismatch_rows_for_turns(
        turns,
        batch_utterances,
        match_mode=f"{match_mode_prefix}_best_overall",
    )
    role_constrained_order_mismatches = order_mismatch_rows_for_turns(
        turns,
        batch_utterances,
        same_role_only=True,
        min_token_recall=0.45,
        min_score=0.65,
        match_mode=f"{match_mode_prefix}_role_constrained_strict",
    )
    contentful_role_constrained_order_mismatches = order_mismatch_rows_for_turns(
        turns,
        batch_utterances,
        same_role_only=True,
        contentful_only=True,
        min_token_recall=0.45,
        min_score=0.65,
        match_mode=f"{match_mode_prefix}_role_constrained_contentful",
    )
    local_missing = local_missing_rows_for_turns(batch_utterances, turns)
    remote_leak = remote_leak_rows_for_turns(turns, batch_utterances)
    return {
        "live_turn_count": len(turns),
        "live_me_turn_count": sum(1 for turn in turns if turn.get("role") == "Me"),
        "live_remote_turn_count": sum(1 for turn in turns if turn.get("role") == "Colleagues"),
        "batch_utterance_count": len(batch_utterances),
        "batch_me_utterance_count": sum(1 for row in batch_utterances if row.get("role") == "Me"),
        "batch_remote_utterance_count": sum(1 for row in batch_utterances if row.get("role") == "Colleagues"),
        "live_order_mismatch_count": len(order_mismatches),
        "live_order_mismatch_by_category": order_mismatch_category_counts(order_mismatches),
        "live_order_mismatch_by_primary_risk": order_mismatch_field_counts(order_mismatches, "primary_risk"),
        "live_order_mismatch_by_confidence": order_mismatch_field_counts(order_mismatches, "confidence"),
        "live_role_constrained_order_mismatch_count": len(role_constrained_order_mismatches),
        "live_role_constrained_order_mismatch_by_category": order_mismatch_category_counts(
            role_constrained_order_mismatches,
        ),
        "live_role_constrained_order_mismatch_by_confidence": order_mismatch_field_counts(
            role_constrained_order_mismatches,
            "confidence",
        ),
        "live_contentful_role_constrained_order_mismatch_count": len(contentful_role_constrained_order_mismatches),
        "live_contentful_role_constrained_order_mismatch_by_category": order_mismatch_category_counts(
            contentful_role_constrained_order_mismatches,
        ),
        "live_contentful_role_constrained_order_mismatch_by_confidence": order_mismatch_field_counts(
            contentful_role_constrained_order_mismatches,
            "confidence",
        ),
        "live_contentful_role_constrained_order_mismatch_by_ambiguity": order_mismatch_field_counts(
            contentful_role_constrained_order_mismatches,
            "match_ambiguity",
        ),
        "live_unambiguous_contentful_role_constrained_order_mismatch_count": sum(
            1
            for row in contentful_role_constrained_order_mismatches
            if row.get("match_ambiguity") == "unambiguous"
        ),
        "live_missing_me_utterance_count": len(local_missing),
        "live_missing_me_seconds": round(sum(safe_float(row.get("duration_sec")) for row in local_missing), 3),
        "live_suspected_remote_leak_in_me_count": len(remote_leak),
        "live_suspected_remote_leak_in_me_seconds": round(
            sum(safe_float(row.get("duration_sec")) for row in remote_leak),
            3,
        ),
    }


def assess_live_vs_batch(session: Path, chunks: list[dict[str, Any]], batch_utterances: list[dict[str, Any]]) -> dict[str, Any]:
    turns = live_turns(session, chunks)
    suppressed_mic_turns = live_suppressed_mic_turns(chunks)
    segment_gate_summary = live_segment_role_gate_summary(chunks)
    rescue_shadow_summary = live_rescue_shadow_summary(chunks)
    rescue_shadow_turns = rescue_shadow_summary.get("turns") or []
    suppressed_segment_audit = read_suppressed_mic_asr_segment_audit(session, chunks, batch_utterances)
    target_me_rows = read_target_me_live_local_recall_rows(session)
    local_missing: list[dict[str, Any]] = []
    local_missing_suspicious_batch_me: list[dict[str, Any]] = []
    local_missing_visible_in_suppressed_mic: list[dict[str, Any]] = []
    local_missing_not_visible_in_suppressed_mic: list[dict[str, Any]] = []
    remote_leak = remote_leak_rows_for_turns(turns, batch_utterances)
    shadow_remote_leak = remote_leak_rows_for_turns(rescue_shadow_turns, batch_utterances)
    order_mismatches = order_mismatch_rows_for_turns(turns, batch_utterances)
    shadow_order_mismatches = order_mismatch_rows_for_turns(turns + rescue_shadow_turns, batch_utterances)
    role_constrained_order_mismatches = order_mismatch_rows_for_turns(
        turns,
        batch_utterances,
        same_role_only=True,
        min_token_recall=0.45,
        min_score=0.65,
        match_mode="role_constrained_strict",
    )
    contentful_role_constrained_order_mismatches = order_mismatch_rows_for_turns(
        turns,
        batch_utterances,
        same_role_only=True,
        contentful_only=True,
        min_token_recall=0.45,
        min_score=0.65,
        match_mode="role_constrained_contentful",
    )
    shadow_role_constrained_order_mismatches = order_mismatch_rows_for_turns(
        turns + rescue_shadow_turns,
        batch_utterances,
        same_role_only=True,
        min_token_recall=0.45,
        min_score=0.65,
        match_mode="role_constrained_strict",
    )
    shadow_contentful_role_constrained_order_mismatches = order_mismatch_rows_for_turns(
        turns + rescue_shadow_turns,
        batch_utterances,
        same_role_only=True,
        contentful_only=True,
        min_token_recall=0.45,
        min_score=0.65,
        match_mode="role_constrained_contentful",
    )
    matched_turns = matched_turn_rows(turns, batch_utterances)
    for row in batch_utterances:
        if row.get("role") != "Me" or len(row.get("tokens") or []) < 2:
            continue
        live_tokens_for_me = utterance_tokens_in_interval(turns, safe_float(row.get("start")), safe_float(row.get("end")), "Me")
        recall = bag_recall(row.get("tokens") or [], live_tokens_for_me) or 0.0
        if recall < 0.35:
            suppressed_tokens = utterance_tokens_in_interval(
                suppressed_mic_turns,
                safe_float(row.get("start")),
                safe_float(row.get("end")),
                "Me",
            )
            suppressed_recall = bag_recall(row.get("tokens") or [], suppressed_tokens) or 0.0
            missing_row = {
                "batch_id": row.get("id"),
                "start": row.get("start"),
                "end": row.get("end"),
                "duration_sec": round(safe_float(row.get("end")) - safe_float(row.get("start")), 3),
                "recall_in_live_me": round(recall, 6),
                "recall_in_suppressed_mic": round(suppressed_recall, 6),
                "suppressed_mic_turn_ids": utterance_ids_in_interval(
                    suppressed_mic_turns,
                    safe_float(row.get("start")),
                    safe_float(row.get("end")),
                    "Me",
                ),
                "text": row.get("text"),
            }
            if suppressed_recall >= 0.35:
                local_missing_visible_in_suppressed_mic.append(missing_row)
            else:
                local_missing_not_visible_in_suppressed_mic.append(missing_row)
            if suspicious_batch_me_utterance(row):
                missing_row["reason"] = "suspicious_short_batch_me_crosses_authoritative_remote"
                local_missing_suspicious_batch_me.append(missing_row)
            else:
                local_missing.append(missing_row)
    shadow_missing_after = local_missing_rows_for_turns(batch_utterances, turns + rescue_shadow_turns)
    shadow_missing_after_seconds = round(sum(safe_float(row.get("duration_sec")) for row in shadow_missing_after), 3)
    baseline_missing_seconds = round(sum(safe_float(row.get("duration_sec")) for row in local_missing), 3)
    target_me_shadow_metrics, target_me_shadow_examples, target_me_shadow_turns_by_policy = target_me_shadow_policy_metrics(
        batch_utterances=batch_utterances,
        live_turns_rows=turns,
        target_me_rows=target_me_rows,
        baseline_missing_rows=local_missing,
    )
    return {
        "live_turns": turns,
        "suppressed_mic_turns": suppressed_mic_turns,
        "matched_turns": matched_turns,
        "local_missing": local_missing,
        "local_missing_suspicious_batch_me": local_missing_suspicious_batch_me,
        "local_missing_visible_in_suppressed_mic": local_missing_visible_in_suppressed_mic,
        "local_missing_not_visible_in_suppressed_mic": local_missing_not_visible_in_suppressed_mic,
        "remote_leak": remote_leak,
        "live_rescue_shadow_remote_leak": shadow_remote_leak,
        "order_mismatches": order_mismatches,
        "role_constrained_order_mismatches": role_constrained_order_mismatches,
        "contentful_role_constrained_order_mismatches": contentful_role_constrained_order_mismatches,
        "live_rescue_shadow_order_mismatches": shadow_order_mismatches,
        "live_rescue_shadow_role_constrained_order_mismatches": shadow_role_constrained_order_mismatches,
        "live_rescue_shadow_contentful_role_constrained_order_mismatches": (
            shadow_contentful_role_constrained_order_mismatches
        ),
        "segment_role_gate_candidates": segment_gate_summary.get("examples", []),
        "live_rescue_shadow_examples": rescue_shadow_summary.get("examples", []),
        "suppressed_mic_asr_segment_examples": suppressed_segment_audit.get("examples", []),
        "suppressed_mic_asr_segments": suppressed_segment_audit.get("segments", []),
        "suppressed_mic_rescue_policy_examples": suppressed_segment_audit.get("policy_examples", {}),
        "live_target_me_shadow_examples": target_me_shadow_examples,
        "live_target_me_shadow_turns_by_policy": target_me_shadow_turns_by_policy,
        "metrics": {
            "live_turn_count": len(turns),
            "live_me_turn_count": sum(1 for turn in turns if turn.get("role") == "Me"),
            "live_remote_turn_count": sum(1 for turn in turns if turn.get("role") == "Colleagues"),
            "batch_utterance_count": len(batch_utterances),
            "batch_me_utterance_count": sum(1 for row in batch_utterances if row.get("role") == "Me"),
            "batch_remote_utterance_count": sum(1 for row in batch_utterances if row.get("role") == "Colleagues"),
            "live_order_mismatch_count": len(order_mismatches),
            "live_order_mismatch_by_category": order_mismatch_category_counts(order_mismatches),
            "live_order_mismatch_by_primary_risk": order_mismatch_field_counts(order_mismatches, "primary_risk"),
            "live_order_mismatch_by_confidence": order_mismatch_field_counts(order_mismatches, "confidence"),
            "live_role_constrained_order_mismatch_count": len(role_constrained_order_mismatches),
            "live_role_constrained_order_mismatch_by_category": order_mismatch_category_counts(
                role_constrained_order_mismatches,
            ),
            "live_role_constrained_order_mismatch_by_confidence": order_mismatch_field_counts(
                role_constrained_order_mismatches,
                "confidence",
            ),
            "live_contentful_role_constrained_order_mismatch_count": len(
                contentful_role_constrained_order_mismatches,
            ),
            "live_contentful_role_constrained_order_mismatch_by_category": order_mismatch_category_counts(
                contentful_role_constrained_order_mismatches,
            ),
            "live_contentful_role_constrained_order_mismatch_by_confidence": order_mismatch_field_counts(
                contentful_role_constrained_order_mismatches,
                "confidence",
            ),
            "live_contentful_role_constrained_order_mismatch_by_ambiguity": order_mismatch_field_counts(
                contentful_role_constrained_order_mismatches,
                "match_ambiguity",
            ),
            "live_unambiguous_contentful_role_constrained_order_mismatch_count": sum(
                1
                for row in contentful_role_constrained_order_mismatches
                if row.get("match_ambiguity") == "unambiguous"
            ),
            "live_missing_me_utterance_count": len(local_missing),
            "live_missing_me_seconds": round(sum(safe_float(row.get("duration_sec")) for row in local_missing), 3),
            "live_suspicious_batch_me_missing_count": len(local_missing_suspicious_batch_me),
            "live_suspicious_batch_me_missing_seconds": round(
                sum(safe_float(row.get("duration_sec")) for row in local_missing_suspicious_batch_me),
                3,
            ),
            "live_missing_me_visible_in_suppressed_mic_count": len(local_missing_visible_in_suppressed_mic),
            "live_missing_me_visible_in_suppressed_mic_seconds": round(
                sum(safe_float(row.get("duration_sec")) for row in local_missing_visible_in_suppressed_mic),
                3,
            ),
            "live_missing_me_not_visible_in_suppressed_mic_count": len(local_missing_not_visible_in_suppressed_mic),
            "live_missing_me_not_visible_in_suppressed_mic_seconds": round(
                sum(safe_float(row.get("duration_sec")) for row in local_missing_not_visible_in_suppressed_mic),
                3,
            ),
            "live_suppressed_mic_turn_count": len(suppressed_mic_turns),
            "live_segment_role_gate_candidate_chunk_count": segment_gate_summary.get("candidate_chunk_count"),
            "live_segment_role_gate_candidate_kept_segment_count": segment_gate_summary.get("kept_segment_count"),
            "live_segment_role_gate_candidate_suppressed_segment_count": segment_gate_summary.get("suppressed_segment_count"),
            **(rescue_shadow_summary.get("metrics") or {}),
            "live_rescue_shadow_order_mismatch_count": len(shadow_order_mismatches),
            "live_rescue_shadow_order_mismatch_by_category": order_mismatch_category_counts(shadow_order_mismatches),
            "live_rescue_shadow_order_mismatch_by_primary_risk": order_mismatch_field_counts(
                shadow_order_mismatches,
                "primary_risk",
            ),
            "live_rescue_shadow_order_mismatch_by_confidence": order_mismatch_field_counts(
                shadow_order_mismatches,
                "confidence",
            ),
            "live_rescue_shadow_role_constrained_order_mismatch_count": len(
                shadow_role_constrained_order_mismatches,
            ),
            "live_rescue_shadow_role_constrained_order_mismatch_by_category": order_mismatch_category_counts(
                shadow_role_constrained_order_mismatches,
            ),
            "live_rescue_shadow_role_constrained_order_mismatch_by_confidence": order_mismatch_field_counts(
                shadow_role_constrained_order_mismatches,
                "confidence",
            ),
            "live_rescue_shadow_contentful_role_constrained_order_mismatch_count": len(
                shadow_contentful_role_constrained_order_mismatches,
            ),
            "live_rescue_shadow_contentful_role_constrained_order_mismatch_by_category": order_mismatch_category_counts(
                shadow_contentful_role_constrained_order_mismatches,
            ),
            "live_rescue_shadow_contentful_role_constrained_order_mismatch_by_confidence": order_mismatch_field_counts(
                shadow_contentful_role_constrained_order_mismatches,
                "confidence",
            ),
            "live_rescue_shadow_contentful_role_constrained_order_mismatch_by_ambiguity": order_mismatch_field_counts(
                shadow_contentful_role_constrained_order_mismatches,
                "match_ambiguity",
            ),
            "live_rescue_shadow_unambiguous_contentful_role_constrained_order_mismatch_count": sum(
                1
                for row in shadow_contentful_role_constrained_order_mismatches
                if row.get("match_ambiguity") == "unambiguous"
            ),
            "live_rescue_shadow_suspected_remote_leak_in_me_count": len(shadow_remote_leak),
            "live_rescue_shadow_suspected_remote_leak_in_me_seconds": round(
                sum(safe_float(row.get("duration_sec")) for row in shadow_remote_leak),
                3,
            ),
            "live_rescue_shadow_missing_me_seconds_after": shadow_missing_after_seconds,
            "live_rescue_shadow_missing_me_recovered_seconds": round(
                max(0.0, baseline_missing_seconds - shadow_missing_after_seconds),
                3,
            ),
            **(suppressed_segment_audit.get("metrics") or {}),
            **target_me_shadow_metrics,
            **rescue_policy_counterfactual_metrics(
                batch_utterances,
                turns,
                suppressed_segment_audit.get("segments") or [],
                local_missing,
            ),
            "live_suspected_remote_leak_in_me_count": len(remote_leak),
            "live_suspected_remote_leak_in_me_seconds": round(sum(safe_float(row.get("duration_sec")) for row in remote_leak), 3),
        },
    }


def gate(name: str, status: str, reason: str, evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    row: dict[str, Any] = {"name": name, "status": status, "reason": reason}
    if evidence:
        row["evidence"] = evidence
    return row


def parity_gates(
    *,
    capture_safety_gate: dict[str, Any],
    blockers: list[str],
    duplicate_count: int,
    boundary_summary: dict[str, Any],
    recall: float | None,
    batch_quality: dict[str, Any] | None,
    live_assessment: dict[str, Any],
    readiness: dict[str, Any] | None,
    outcome: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    gates: list[dict[str, Any]] = [
        capture_safety_gate,
        gate(
            "raw_batch_authoritative",
            "passed",
            "near-realtime remains shadow-only and batch transcript is source of truth",
            {"batch_authoritative": True},
        )
    ]
    if blockers:
        gates.append(gate("required_artifacts", "blocked", "comparison inputs are missing", {"blockers": blockers}))
    else:
        gates.append(gate("required_artifacts", "passed", "live and batch artifacts are present"))
    gates.append(
        gate(
            "duplicate_chunks",
            "passed" if duplicate_count == 0 else "failed",
            "adjacent live chunks should not repeat the same decoded text",
            {"adjacent_duplicate_chunk_count": duplicate_count},
        )
    )
    if recall is None:
        gates.append(gate("live_token_recall", "not_evaluated", "live draft has no decoded tokens"))
    else:
        gates.append(
            gate(
                "live_token_recall",
                "passed" if recall >= 0.60 else "warning",
                "bag-of-words live draft tokens should mostly appear in selected batch transcript",
                {"live_token_recall_in_batch": round(recall, 6)},
            )
        )
    assessment_metrics = live_assessment.get("metrics") if isinstance(live_assessment, dict) else {}
    batch_metrics = {
        "unrepaired_long_mic_crossings_count": metric_value(batch_quality, "unrepaired_long_mic_crossings_count"),
        "local_only_island_recall": metric_value(batch_quality, "local_only_island_recall"),
        "remote_duplicate_in_me_seconds": metric_value(batch_quality, "remote_duplicate_in_me_seconds"),
        "needs_review_count": metric_value(batch_quality, "needs_review_count"),
        "cross_role_overlap_gt2_seconds": metric_value(batch_quality, "cross_role_overlap_gt2_seconds"),
    }
    order_mismatches = int(assessment_metrics.get("live_order_mismatch_count") or 0)
    role_constrained_order_mismatches = int(assessment_metrics.get("live_role_constrained_order_mismatch_count") or 0)
    contentful_role_constrained_order_mismatches = int(
        assessment_metrics.get("live_contentful_role_constrained_order_mismatch_count") or 0
    )
    missing_me_seconds = safe_float(assessment_metrics.get("live_missing_me_seconds"))
    remote_leak_seconds = safe_float(assessment_metrics.get("live_suspected_remote_leak_in_me_seconds"))
    gates.append(
        gate(
            "order_risk",
            "passed" if order_mismatches == 0 else "warning",
            "live turn order should not contradict the selected batch transcript order",
            {
                "live_order_mismatch_count": order_mismatches,
                "live_role_constrained_order_mismatch_count": role_constrained_order_mismatches,
                "live_contentful_role_constrained_order_mismatch_count": contentful_role_constrained_order_mismatches,
                "live_order_mismatch_by_primary_risk": assessment_metrics.get("live_order_mismatch_by_primary_risk"),
                "batch_metrics": batch_metrics,
            },
        )
    )
    gates.append(
        gate(
            "local_recall",
            "passed" if missing_me_seconds <= 0.5 else "warning",
            "batch Me speech should be visible in live mic turns when live draft is used as evidence",
            {
                "live_missing_me_seconds": round(missing_me_seconds, 3),
                "live_missing_me_utterance_count": assessment_metrics.get("live_missing_me_utterance_count"),
                "batch_metrics": batch_metrics,
            },
        )
    )
    gates.append(
        gate(
            "remote_duplicate_leak",
            "passed" if remote_leak_seconds <= 0.5 else "warning",
            "live mic turns should not look like selected batch remote speech",
            {
                "live_suspected_remote_leak_in_me_seconds": round(remote_leak_seconds, 3),
                "live_suspected_remote_leak_in_me_count": assessment_metrics.get("live_suspected_remote_leak_in_me_count"),
                "batch_metrics": batch_metrics,
            },
        )
    )
    outcome_metrics = outcome.get("metrics") if isinstance(outcome, dict) else {}
    review_burden_sec = safe_float(outcome_metrics.get("review_burden_sec"))
    review_burden_ratio = safe_float(outcome_metrics.get("review_burden_ratio"))
    gates.append(
        gate(
            "review_burden",
            "passed" if review_burden_ratio <= 0.03 else ("warning" if review_burden_ratio <= 0.12 else "failed"),
            "selected batch outcome review burden is the maximum allowed burden for live cache promotion",
            {
                "review_burden_sec": round(review_burden_sec, 3),
                "review_burden_ratio": round(review_burden_ratio, 6),
            },
        )
    )
    use_gate = readiness.get("use_gate") if isinstance(readiness, dict) else None
    outcome_value = outcome.get("outcome") if isinstance(outcome, dict) else None
    gates.append(
        gate(
            "selected_notes_readiness",
            "passed" if use_gate == "ready_for_notes" or outcome_value == "ready_for_notes" else "warning",
            "live parity is only promotion-ready when the authoritative batch result is notes-ready",
            {"readiness_use_gate": use_gate, "outcome": outcome_value},
        )
    )
    gates.append(
        gate(
            "chunk_boundary_risks",
            "passed"
            if duplicate_count == 0 and safe_int(boundary_summary.get("issue_count")) == 0
            else ("failed" if duplicate_count > 0 else "warning"),
            "live chunk boundaries should not introduce duplicate text or unresolved boundary suppression",
            {
                "adjacent_duplicate_chunk_count": duplicate_count,
                "live_boundary_gate_issue_count": boundary_summary.get("issue_count"),
                "live_boundary_gate_suppressed_count": boundary_summary.get("suppressed_count"),
                "live_boundary_gate_resolved_suppressed_count": boundary_summary.get("resolved_suppressed_count"),
                "live_boundary_gate_unresolved_suppressed_count": boundary_summary.get("unresolved_suppressed_count"),
                "live_boundary_gate_status_counts": boundary_summary.get("status_counts"),
                "live_boundary_gate_reason_counts": boundary_summary.get("reason_counts"),
            },
        )
    )
    return gates


def check_row(check_id: str, status: str, message: str, evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": check_id,
        "status": status,
        "message": message,
        "evidence": evidence or {},
    }


def live_session_next_commands(session: Path, payload: dict[str, Any]) -> list[str]:
    session_path = str(session)
    comparison_path = session / "derived/live/live_batch_comparison.json"
    coverage_command = (
        "murmurmark corpus live all --min-live-sessions 3 --min-compared-sessions 3 "
        "--min-meaningful-compared-sessions 3 --min-passing-compared-sessions 3 "
        "--max-order-mismatches 0 --max-missing-me-sec 0 --max-remote-in-me-sec 0 "
        "--max-boundary-duplicates 0 --require-passing-gates --fail-on-promotion"
    )
    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
    gates = (payload.get("parity_gates") or {}).get("gates") if isinstance(payload.get("parity_gates"), dict) else []
    non_passing = [gate for gate in gates or [] if isinstance(gate, dict) and gate.get("status") != "passed"]
    commands = [f"murmurmark status {session_path}"]
    if non_passing:
        commands.append(f"jq '.parity_gates.gates[] | select(.status != \"passed\")' {comparison_path}")
    risk_examples = payload.get("risk_examples") if isinstance(payload.get("risk_examples"), dict) else {}
    if any(risk_examples.values()):
        commands.append(f"jq '.risk_examples' {comparison_path}")
    if not metrics.get("meaningful_live_comparison") or not metrics.get("all_parity_gates_passed"):
        commands.append(
            "murmurmark status latest  # production meetings still use normal record/process; "
            "controlled Live Evidence uses live pilot"
        )
    commands.append(coverage_command)
    deduped: list[str] = []
    for command in commands:
        if command not in deduped:
            deduped.append(command)
    return deduped


def build_session_report(session: Path, payload: dict[str, Any]) -> dict[str, Any]:
    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
    blockers = payload.get("blockers") if isinstance(payload.get("blockers"), list) else []
    gates = (payload.get("parity_gates") or {}).get("gates") if isinstance(payload.get("parity_gates"), dict) else []
    non_passing = [gate for gate in gates or [] if isinstance(gate, dict) and gate.get("status") != "passed"]
    checks = [
        check_row(
            "live_artifacts_present",
            "pass" if "live_report_missing" not in blockers and "live_chunks_missing" not in blockers else "block",
            "live report and chunks are required for a live comparison",
            {"blockers": [item for item in blockers if item in {"live_report_missing", "live_chunks_missing"}]},
        ),
        check_row(
            "batch_artifacts_present",
            "pass"
            if "batch_transcript_missing" not in blockers and "batch_clean_dialogue_missing" not in blockers
            else "block",
            "authoritative batch transcript and clean dialogue are required",
            {"blockers": [item for item in blockers if item in {"batch_transcript_missing", "batch_clean_dialogue_missing"}]},
        ),
        check_row(
            "meaningful_two_role_comparison",
            "pass" if metrics.get("meaningful_live_comparison") else "block",
            "live and batch outputs must both contain Me and Colleagues evidence",
            {
                "live_me_turn_count": metrics.get("live_me_turn_count"),
                "live_remote_turn_count": metrics.get("live_remote_turn_count"),
                "batch_me_utterance_count": metrics.get("batch_me_utterance_count"),
                "batch_remote_utterance_count": metrics.get("batch_remote_utterance_count"),
            },
        ),
        check_row(
            "batch_ready_for_notes",
            "pass" if metrics.get("batch_ready_for_notes") else "block",
            "live parity can pass only when the authoritative batch result is notes-ready",
            {
                "batch_use_gate": metrics.get("batch_use_gate"),
                "batch_outcome": metrics.get("batch_outcome"),
            },
        ),
        check_row(
            "all_parity_gates_passed",
            "pass" if metrics.get("all_parity_gates_passed") else "block",
            "every live parity gate must pass before this session can count as a passing comparison",
            {"non_passing_gates": [gate.get("name") for gate in non_passing]},
        ),
        check_row(
            "promotion_blocked",
            "pass" if payload.get("promotion_allowed") is False else "fail",
            "live promotion must stay blocked in v1",
            {"promotion_allowed": payload.get("promotion_allowed")},
        ),
        check_row(
            "suspicious_batch_me_missing",
            "pass" if safe_float(metrics.get("live_suspicious_batch_me_missing_seconds")) == 0 else "warn",
            "suspicious short batch Me missing from live does not count as missing local speech, but should be inspected",
            {
                "seconds": metrics.get("live_suspicious_batch_me_missing_seconds"),
                "count": metrics.get("live_suspicious_batch_me_missing_count"),
            },
        ),
    ]
    hard_statuses = {row["status"] for row in checks if row["id"] != "suspicious_batch_me_missing"}
    if "fail" in hard_statuses:
        status = "failed"
    elif "block" in hard_statuses:
        status = "not_passing"
    elif any(row["status"] == "warn" for row in checks):
        status = "passing_with_warnings"
    else:
        status = "passing_shadow_locked"
    next_commands = live_session_next_commands(session, payload)
    return {
        "schema": SESSION_REPORT_SCHEMA,
        "generator": {"name": "compare-live-batch", "version": SCRIPT_VERSION},
        "created_at": datetime.now(timezone.utc).isoformat(),
        "session": str(session),
        "status": status,
        "promotion_allowed": False,
        "checks": checks,
        "non_passing_gates": non_passing,
        "metrics": metrics,
        "risk_examples": payload.get("risk_examples") if isinstance(payload.get("risk_examples"), dict) else {},
        "recommended_next": next_commands[0],
        "next_commands": next_commands,
    }


def write_session_report_markdown(path: Path, report: dict[str, Any]) -> None:
    metrics = report.get("metrics") if isinstance(report.get("metrics"), dict) else {}
    lines = [
        "# Live Parity Session Report",
        "",
        f"- status: `{report.get('status')}`",
        f"- promotion allowed: `{report.get('promotion_allowed')}`",
        f"- meaningful comparison: `{metrics.get('meaningful_live_comparison')}`",
        f"- all parity gates passed: `{metrics.get('all_parity_gates_passed')}`",
        f"- batch use gate: `{metrics.get('batch_use_gate')}`",
        f"- batch outcome: `{metrics.get('batch_outcome')}`",
        f"- suspicious batch-Me missing seconds: `{metrics.get('live_suspicious_batch_me_missing_seconds')}`",
        "",
        "## Checks",
        "",
    ]
    for row in report.get("checks") or []:
        if not isinstance(row, dict):
            continue
        lines.append(f"- `{row.get('id')}`: `{row.get('status')}` - {row.get('message')}")
    issues = [row for row in report.get("non_passing_gates") or [] if isinstance(row, dict)]
    if issues:
        lines += ["", "## Non-Passing Gates", ""]
        for row in issues:
            lines.append(f"- `{row.get('name')}`: `{row.get('status')}` - {row.get('reason')}")
    risk_examples = report.get("risk_examples") if isinstance(report.get("risk_examples"), dict) else {}
    if any(risk_examples.values()):
        lines += ["", "## Risk Examples", ""]
        for key, values in risk_examples.items():
            if values:
                lines.append(f"- `{key}`: `{len(values)}`")
    lines += ["", "## Next", ""]
    for command in report.get("next_commands") or []:
        lines.append(f"- `{command}`")
    lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def fmt_time(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    return f"{total // 60:02d}:{total % 60:02d}"


def shadow_turn_payload(turn: dict[str, Any], added_by_policy: str | None) -> dict[str, Any]:
    return {
        "id": turn.get("id"),
        "role": turn.get("role"),
        "start": safe_float(turn.get("start")),
        "end": safe_float(turn.get("end")),
        "text": clean_text(str(turn.get("text") or "")),
        "source": turn.get("source"),
        "chunk_index": turn.get("chunk_index"),
        "segment_index": turn.get("segment_index"),
        "shadow_added": added_by_policy is not None,
        "shadow_policy": added_by_policy,
    }


def target_me_shadow_profile_components(
    *,
    policy: str,
    live_turns_rows: list[dict[str, Any]],
    suppressed_mic_asr_segments: list[dict[str, Any]],
    target_me_rows: list[dict[str, Any]],
    target_me_turns_by_policy: dict[str, list[dict[str, Any]]],
    persistent_target_me_rows: list[dict[str, Any]],
    batch_utterances: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    base_policy = TARGET_ME_SHADOW_PROFILE_BASE_POLICY.get(policy, policy)
    target_turns = (
        []
        if policy in SUPPRESSED_MIC_COMPOSITE_SHADOW_PROFILE_POLICIES
        or policy in LIVE_ME_REMOTE_OVERLAP_FILTER_NO_TARGET_PROFILE_POLICIES
        else list(target_me_turns_by_policy.get(base_policy) or [])
    )
    live_turns = list(live_turns_rows)
    supplemental_turns: list[dict[str, Any]] = []
    rejected_supplemental_turns: list[dict[str, Any]] = []
    removed_live_turns: list[dict[str, Any]] = []
    if policy in LIVE_ME_REMOTE_OVERLAP_FILTER_SHADOW_PROFILE_POLICIES:
        remote_turns = [turn for turn in live_turns if turn.get("role") == "Colleagues"]
        kept_live_turns = []
        for turn in live_turns:
            evidence = live_me_remote_overlap_filter_decision(turn, remote_turns)
            if evidence:
                payload = shadow_turn_payload(turn, None)
                payload.update(
                    {
                        "removed_by_policy": policy,
                        "removal_reason": "online_live_me_remote_overlap_filter",
                        "evidence": evidence,
                    }
                )
                removed_live_turns.append(payload)
            else:
                kept_live_turns.append(turn)
        live_turns = kept_live_turns

    if policy in TARGET_ME_REMOTE_FORBIDDEN_ORACLE_POLICIES:
        remote_leak_by_id = {
            str(row.get("live_id")): row
            for row in remote_leak_rows_for_turns(live_turns, batch_utterances)
            if row.get("live_id") is not None
        }
        if remote_leak_by_id:
            kept_live_turns: list[dict[str, Any]] = []
            for turn in live_turns:
                turn_id = str(turn.get("id") or "")
                evidence = remote_leak_by_id.get(turn_id)
                if turn.get("role") == "Me" and evidence:
                    payload = shadow_turn_payload(turn, None)
                    payload.update(
                        {
                            "removed_by_policy": policy,
                            "removal_reason": "batch_remote_forbidden_oracle_remote_leak",
                            "evidence": evidence,
                        }
                    )
                    removed_live_turns.append(payload)
                else:
                    kept_live_turns.append(turn)
            live_turns = kept_live_turns

    if policy in TARGET_ME_VISIBLE_SUPPRESSED_MIC_ORACLE_POLICIES:
        supplemental_candidates = visible_suppressed_mic_oracle_turns(suppressed_mic_asr_segments)
        supplemental_turns, rejected_supplemental_turns = timeline_safe_visible_suppressed_mic_turns(
            candidates=supplemental_candidates,
            base_turns=live_turns + target_turns,
            batch_utterances=batch_utterances,
        )
    elif policy in TARGET_ME_ONLINE_SUPPRESSED_MIC_PROFILE_POLICIES:
        supplemental_turns = online_suppressed_mic_policy_turns(
            suppressed_mic_asr_segments,
            TARGET_ME_ONLINE_SUPPRESSED_MIC_PROFILE_POLICIES[policy],
        )
    elif policy in SUPPRESSED_MIC_COMPOSITE_SHADOW_PROFILE_POLICIES:
        supplemental_turns = suppressed_mic_composite_shadow_turns(
            policy=policy,
            suppressed_mic_asr_segments=suppressed_mic_asr_segments,
            target_me_rows=target_me_rows,
            persistent_target_me_rows=persistent_target_me_rows,
        )
    return live_turns, target_turns, supplemental_turns, removed_live_turns, rejected_supplemental_turns


def write_target_me_shadow_drafts(
    *,
    session: Path,
    live_turns_rows: list[dict[str, Any]],
    suppressed_mic_asr_segments: list[dict[str, Any]],
    target_me_rows: list[dict[str, Any]],
    target_me_turns_by_policy: dict[str, list[dict[str, Any]]],
    persistent_target_me_rows: list[dict[str, Any]],
    batch_utterances: list[dict[str, Any]],
    metrics: dict[str, Any],
) -> dict[str, dict[str, str]]:
    outputs: dict[str, dict[str, str]] = {}
    for policy in MATERIALIZED_TARGET_ME_SHADOW_POLICIES:
        live_turns, target_turns, supplemental_turns, removed_live_turns, rejected_supplemental_turns = (
            target_me_shadow_profile_components(
                policy=policy,
                live_turns_rows=live_turns_rows,
                suppressed_mic_asr_segments=suppressed_mic_asr_segments,
                target_me_rows=target_me_rows,
                target_me_turns_by_policy=target_me_turns_by_policy,
                persistent_target_me_rows=persistent_target_me_rows,
                batch_utterances=batch_utterances,
            )
        )
        out_dir = session / "derived/live/target-me-shadow" / policy
        json_path = out_dir / "draft.json"
        md_path = out_dir / "draft.md"
        live_payload = [shadow_turn_payload(turn, None) for turn in live_turns]
        target_payload = [shadow_turn_payload(turn, policy) for turn in target_turns]
        supplemental_payload = [shadow_turn_payload(turn, policy) for turn in supplemental_turns]
        combined = sorted(
            live_payload + target_payload + supplemental_payload,
            key=lambda item: (safe_float(item.get("start")), safe_float(item.get("end")), str(item.get("id") or "")),
        )
        policy_metrics = parity_metrics_for_turns(
            live_turns + target_turns + supplemental_turns,
            batch_utterances,
            match_mode_prefix=f"target_me_shadow_draft_{policy}",
        )
        base_policy = TARGET_ME_SHADOW_PROFILE_BASE_POLICY.get(policy, policy)
        base = f"live_target_me_shadow_policy_{base_policy}"
        removed_seconds = round(
            sum(safe_float(turn.get("end")) - safe_float(turn.get("start")) for turn in removed_live_turns),
            3,
        )
        supplemental_seconds = round(
            sum(safe_float(turn.get("end")) - safe_float(turn.get("start")) for turn in supplemental_turns),
            3,
        )
        payload = {
            "schema": "murmurmark.live_target_me_shadow_draft/v1",
            "generator": {"name": "compare-live-batch", "version": SCRIPT_VERSION},
            "created_at": datetime.now(timezone.utc).isoformat(),
            "policy": policy,
            "base_policy": base_policy,
            "diagnostic_oracle": policy in TARGET_ME_REMOTE_FORBIDDEN_ORACLE_POLICIES,
            "promotion_allowed": False,
            "promotion_reason": (
                "batch_remote_forbidden_oracle_is_not_live_promotable"
                if policy in TARGET_ME_REMOTE_FORBIDDEN_ORACLE_POLICIES
                else "target_me_shadow_draft_is_diagnostic_only"
            ),
            "batch_authoritative": True,
            "metrics": {
                "live_turn_count": len(live_payload),
                "target_me_added_turn_count": len(target_payload),
                "visible_suppressed_mic_added_turn_count": len(supplemental_payload),
                "visible_suppressed_mic_added_turn_seconds": supplemental_seconds,
                "visible_suppressed_mic_rejected_turn_count": len(rejected_supplemental_turns),
                "removed_live_turn_count": len(removed_live_turns),
                "removed_live_turn_seconds": removed_seconds,
                "combined_turn_count": len(combined),
                "target_me_added_seconds": metrics.get(f"{base}_candidate_seconds"),
                "missing_me_recovered_seconds": metrics.get(f"{base}_missing_me_recovered_seconds"),
                "live_missing_me_seconds": policy_metrics.get("live_missing_me_seconds"),
                "suspected_remote_leak_in_me_seconds": policy_metrics.get("live_suspected_remote_leak_in_me_seconds"),
                "live_contentful_role_constrained_order_mismatch_count": (
                    policy_metrics.get("live_contentful_role_constrained_order_mismatch_count")
                ),
                "contentful_role_constrained_order_mismatch_delta_count": (
                    metrics.get(f"{base}_contentful_role_constrained_order_mismatch_delta_count")
                ),
            },
            "removed_live_turns": removed_live_turns,
            "rejected_visible_suppressed_mic_turns": rejected_supplemental_turns[:50],
            "turns": combined,
        }
        write_json(json_path, payload)
        lines = [
            "# Target-Me Shadow Draft",
            "",
            f"- policy: `{policy}`",
            f"- base policy: `{base_policy}`",
            f"- diagnostic oracle: `{str(payload['diagnostic_oracle']).lower()}`",
            "- promotion allowed: `false`",
            "- batch authoritative: `true`",
            f"- target-me added turns: `{len(target_payload)}`",
            f"- visible suppressed mic added turns: `{len(supplemental_payload)}`",
            f"- visible suppressed mic rejected turns: `{len(rejected_supplemental_turns)}`",
            f"- removed live turns: `{len(removed_live_turns)}` / `{removed_seconds}s`",
            f"- missing-Me recovered seconds: `{payload['metrics']['missing_me_recovered_seconds']}`",
            f"- suspected remote leak seconds: `{payload['metrics']['suspected_remote_leak_in_me_seconds']}`",
            f"- contentful order delta: `{payload['metrics']['contentful_role_constrained_order_mismatch_delta_count']}`",
            "",
        ]
        for turn in combined:
            role = turn.get("role") or "Unknown"
            marker = " target-me-shadow" if turn.get("shadow_added") else ""
            text = clean_text(str(turn.get("text") or ""))
            if not text:
                continue
            lines += [
                f"## {fmt_time(safe_float(turn.get('start')))} {role}{marker}",
                "",
                text,
                "",
            ]
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        outputs[policy] = {
            "draft_json": rel(json_path, session),
            "draft_markdown": rel(md_path, session),
        }
    return outputs


def build_target_me_shadow_profiles(
    *,
    live_turns_rows: list[dict[str, Any]],
    suppressed_mic_turns: list[dict[str, Any]],
    suppressed_mic_asr_segments: list[dict[str, Any]],
    target_me_rows: list[dict[str, Any]],
    target_me_turns_by_policy: dict[str, list[dict[str, Any]]],
    persistent_target_me_rows: list[dict[str, Any]],
    target_me_shadow_outputs: dict[str, dict[str, str]],
    batch_utterances: list[dict[str, Any]],
    batch_final_tokens: list[str],
    capture_safety_gate: dict[str, Any],
    blockers: list[str],
    duplicate_count: int,
    boundary_summary: dict[str, Any],
    batch_quality: dict[str, Any] | None,
    readiness: dict[str, Any] | None,
    outcome: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    profiles: dict[str, Any] = {}
    top_level_metrics: dict[str, Any] = {}
    for policy in MATERIALIZED_TARGET_ME_SHADOW_POLICIES:
        live_turns, target_turns, supplemental_turns, removed_live_turns, rejected_supplemental_turns = (
            target_me_shadow_profile_components(
                policy=policy,
                live_turns_rows=live_turns_rows,
                suppressed_mic_asr_segments=suppressed_mic_asr_segments,
                target_me_rows=target_me_rows,
                target_me_turns_by_policy=target_me_turns_by_policy,
                persistent_target_me_rows=persistent_target_me_rows,
                batch_utterances=batch_utterances,
            )
        )
        combined_turns = sorted(
            live_turns + target_turns + supplemental_turns,
            key=lambda item: (safe_float(item.get("start")), safe_float(item.get("end")), str(item.get("id") or "")),
        )
        supplemental_seconds = round(
            sum(safe_float(turn.get("end")) - safe_float(turn.get("start")) for turn in supplemental_turns),
            3,
        )
        removed_seconds = round(
            sum(safe_float(turn.get("end")) - safe_float(turn.get("start")) for turn in removed_live_turns),
            3,
        )
        metrics = parity_metrics_for_turns(
            combined_turns,
            batch_utterances,
            match_mode_prefix=f"target_me_shadow_profile_{policy}",
        )
        missing_rows, missing_diagnostics = local_missing_diagnostics_for_turns(
            batch_utterances=batch_utterances,
            turns=combined_turns,
            suppressed_mic_turns=suppressed_mic_turns,
            target_me_turns_by_policy=target_me_turns_by_policy,
        )
        metrics.update(missing_diagnostics)
        recall = bag_recall(
            tokens("\n".join(clean_text(str(turn.get("text") or "")) for turn in combined_turns)),
            batch_final_tokens,
        )
        profile_assessment = {"metrics": metrics}
        gates = parity_gates(
            capture_safety_gate=capture_safety_gate,
            blockers=blockers,
            duplicate_count=duplicate_count,
            boundary_summary=boundary_summary,
            recall=recall,
            batch_quality=batch_quality,
            live_assessment=profile_assessment,
            readiness=readiness,
            outcome=outcome,
        )
        all_gates_passed = bool(gates) and all(row.get("status") == "passed" for row in gates)
        gate_statuses = {str(row.get("status")) for row in gates}
        status = "passed_but_shadow_locked" if all_gates_passed else "not_promotable"
        base = f"live_target_me_shadow_profile_{policy}"
        top_level_metrics[f"{base}_all_parity_gates_passed"] = all_gates_passed
        top_level_metrics[f"{base}_non_passing_gate_count"] = sum(1 for row in gates if row.get("status") != "passed")
        top_level_metrics[f"{base}_live_token_recall_in_batch"] = round(recall, 6) if recall is not None else None
        top_level_metrics[f"{base}_removed_live_turn_count"] = len(removed_live_turns)
        top_level_metrics[f"{base}_removed_live_turn_seconds"] = removed_seconds
        top_level_metrics[f"{base}_visible_suppressed_mic_added_turn_count"] = len(supplemental_turns)
        top_level_metrics[f"{base}_visible_suppressed_mic_added_turn_seconds"] = supplemental_seconds
        top_level_metrics[f"{base}_visible_suppressed_mic_rejected_turn_count"] = len(rejected_supplemental_turns)
        for key in (
            "live_turn_count",
            "live_me_turn_count",
            "live_remote_turn_count",
            "live_order_mismatch_count",
            "live_role_constrained_order_mismatch_count",
            "live_contentful_role_constrained_order_mismatch_count",
            "live_missing_me_utterance_count",
            "live_missing_me_seconds",
            "live_missing_me_visible_in_suppressed_mic_count",
            "live_missing_me_visible_in_suppressed_mic_seconds",
            "live_missing_me_not_visible_in_suppressed_mic_count",
            "live_missing_me_not_visible_in_suppressed_mic_seconds",
            "live_missing_me_with_target_me_candidate_count",
            "live_missing_me_with_target_me_candidate_seconds",
            "live_missing_me_without_target_me_candidate_count",
            "live_missing_me_without_target_me_candidate_seconds",
            "live_missing_me_visible_with_target_me_candidate_count",
            "live_missing_me_visible_with_target_me_candidate_seconds",
            "live_missing_me_visible_without_target_me_candidate_count",
            "live_missing_me_visible_without_target_me_candidate_seconds",
            "live_missing_me_not_visible_with_target_me_candidate_count",
            "live_missing_me_not_visible_with_target_me_candidate_seconds",
            "live_missing_me_not_visible_without_target_me_candidate_count",
            "live_missing_me_not_visible_without_target_me_candidate_seconds",
            "live_suspected_remote_leak_in_me_count",
            "live_suspected_remote_leak_in_me_seconds",
        ):
            top_level_metrics[f"{base}_{key}"] = metrics.get(key)
        profiles[policy] = {
            "schema": "murmurmark.live_shadow_profile_parity/v1",
            "policy": policy,
            "status": status,
            "promotion_allowed": False,
            "promotion_reason": "target_me_shadow_profile_never_promotes_by_default",
            "batch_authoritative": True,
            "outputs": target_me_shadow_outputs.get(policy) or {},
            "metrics": {
                **metrics,
                "live_token_recall_in_batch": round(recall, 6) if recall is not None else None,
                "all_parity_gates_passed": all_gates_passed,
                "removed_live_turn_count": len(removed_live_turns),
                "removed_live_turn_seconds": removed_seconds,
                "visible_suppressed_mic_added_turn_count": len(supplemental_turns),
                "visible_suppressed_mic_added_turn_seconds": supplemental_seconds,
                "visible_suppressed_mic_rejected_turn_count": len(rejected_supplemental_turns),
            },
            "removed_live_turns": removed_live_turns[:50],
            "rejected_visible_suppressed_mic_turns": rejected_supplemental_turns[:50],
            "risk_examples": {
                "local_missing": missing_rows[:20],
                "remote_leak": remote_leak_rows_for_turns(combined_turns, batch_utterances)[:20],
            },
            "parity_gates": {
                "status": "not_promotable" if gate_statuses - {"passed"} else "passed_but_shadow_locked",
                "gates": gates,
            },
        }
    return profiles, top_level_metrics


def main() -> int:
    args = parse_args()
    session = args.session.expanduser().resolve()
    live_report_path = session / "derived/live/live_pipeline_report.json"
    chunks_path = session / "derived/live/chunks.jsonl"
    comparison_path = session / "derived/live/live_batch_comparison.json"
    session_report_path = session / "derived/live/live_parity_session_report.json"
    session_report_md_path = session / "derived/live/live_parity_session_report.md"
    live_report = read_json(live_report_path)
    chunks = read_jsonl(chunks_path)
    transcript_path = selected_transcript_path(session)
    profile = selected_profile(session)
    clean_dialogue_path = selected_clean_dialogue_path(session, profile)
    batch_utterances = read_utterances(clean_dialogue_path)
    batch_quality_path = quality_report_path(session, profile)
    batch_quality = read_json(batch_quality_path) if batch_quality_path else None
    readiness = read_json(session / "derived/readiness/session_readiness.json")
    outcome = read_json(session / "derived/outcome/outcome.json")
    final_text = transcript_path.read_text(encoding="utf-8", errors="ignore") if transcript_path else ""
    live_text = "\n".join(chunk_text(row) for row in chunks)
    live_tokens = tokens(live_text)
    final_tokens = tokens(final_text)
    recall = bag_recall(live_tokens, final_tokens)
    duplicate_count = duplicate_adjacent_chunks(chunks)
    boundary_summary = live_boundary_gate_summary(chunks)
    live_assessment = assess_live_vs_batch(session, chunks, batch_utterances)
    blockers: list[str] = []
    warnings: list[str] = []
    if live_report is None:
        blockers.append("live_report_missing")
    if not chunks:
        blockers.append("live_chunks_missing")
    if transcript_path is None:
        blockers.append("batch_transcript_missing")
    if clean_dialogue_path is None or not batch_utterances:
        blockers.append("batch_clean_dialogue_missing")
    if duplicate_count > 0:
        warnings.append("adjacent_live_chunk_duplicates_detected")
    if safe_int(boundary_summary.get("issue_count")) > 0:
        warnings.append("live_boundary_gate_issues_detected")
    if recall is not None and recall < 0.60:
        warnings.append("low_live_token_recall_in_batch")
    if safe_float((live_assessment.get("metrics") or {}).get("live_missing_me_seconds")) > 0.5:
        warnings.append("live_missing_me_speech_detected")
    if safe_float((live_assessment.get("metrics") or {}).get("live_suspected_remote_leak_in_me_seconds")) > 0.5:
        warnings.append("live_remote_leak_in_me_detected")
    if int((live_assessment.get("metrics") or {}).get("live_order_mismatch_count") or 0) > 0:
        warnings.append("live_order_mismatch_detected")
    capture_safety_gate = build_capture_safety_gate(session)
    if capture_safety_gate.get("status") != "passed":
        warnings.append("capture_safety_not_passed")
    gates = parity_gates(
        capture_safety_gate=capture_safety_gate,
        blockers=blockers,
        duplicate_count=duplicate_count,
        boundary_summary=boundary_summary,
        recall=recall,
        batch_quality=batch_quality,
        live_assessment=live_assessment,
        readiness=readiness,
        outcome=outcome,
    )
    gate_statuses = {str(row.get("status")) for row in gates}
    live_metrics = live_assessment.get("metrics") if isinstance(live_assessment, dict) else {}
    batch_use_gate = readiness.get("use_gate") if isinstance(readiness, dict) else None
    batch_outcome = outcome.get("outcome") if isinstance(outcome, dict) else None
    all_parity_gates_passed = bool(gates) and all(row.get("status") == "passed" for row in gates)
    meaningful_live_comparison = bool(
        not blockers
        and int(live_metrics.get("live_turn_count") or 0) > 0
        and int(live_metrics.get("batch_utterance_count") or 0) > 0
        and int(live_metrics.get("live_me_turn_count") or 0) > 0
        and int(live_metrics.get("live_remote_turn_count") or 0) > 0
        and int(live_metrics.get("batch_me_utterance_count") or 0) > 0
        and int(live_metrics.get("batch_remote_utterance_count") or 0) > 0
    )
    target_me_rows = read_target_me_live_local_recall_rows(session)
    persistent_target_me_rows = read_persistent_target_me_profile_rows(session)
    target_me_shadow_outputs = write_target_me_shadow_drafts(
        session=session,
        live_turns_rows=live_assessment.get("live_turns") or [],
        suppressed_mic_asr_segments=live_assessment.get("suppressed_mic_asr_segments") or [],
        target_me_rows=target_me_rows,
        target_me_turns_by_policy=live_assessment.get("live_target_me_shadow_turns_by_policy") or {},
        persistent_target_me_rows=persistent_target_me_rows,
        batch_utterances=batch_utterances,
        metrics=live_metrics,
    )
    target_me_shadow_profiles, target_me_shadow_profile_metrics = build_target_me_shadow_profiles(
        live_turns_rows=live_assessment.get("live_turns") or [],
        suppressed_mic_turns=live_assessment.get("suppressed_mic_turns") or [],
        suppressed_mic_asr_segments=live_assessment.get("suppressed_mic_asr_segments") or [],
        target_me_rows=target_me_rows,
        target_me_turns_by_policy=live_assessment.get("live_target_me_shadow_turns_by_policy") or {},
        persistent_target_me_rows=persistent_target_me_rows,
        target_me_shadow_outputs=target_me_shadow_outputs,
        batch_utterances=batch_utterances,
        batch_final_tokens=final_tokens,
        capture_safety_gate=capture_safety_gate,
        blockers=blockers,
        duplicate_count=duplicate_count,
        boundary_summary=boundary_summary,
        batch_quality=batch_quality,
        readiness=readiness,
        outcome=outcome,
    )
    promotion_blockers = [
        "shadow_v1_never_promotes_by_default",
        *[str(row.get("name")) for row in gates if row.get("status") in {"blocked", "failed", "warning", "not_evaluated"}],
    ]
    payload = {
        "schema": SCHEMA,
        "generator": {"name": "compare-live-batch", "version": SCRIPT_VERSION},
        "created_at": datetime.now(timezone.utc).isoformat(),
        "session": str(session),
        "status": "blocked" if blockers else "shadow_compared",
        "promotion_allowed": False,
        "promotion_reason": "near_realtime_shadow_v1_never_promotes_by_default",
        "promotion_blockers": promotion_blockers,
        "blockers": blockers,
        "warnings": warnings,
        "inputs": {
            "live_report": rel(live_report_path, session) if live_report_path.exists() else None,
            "live_chunks": rel(chunks_path, session) if chunks_path.exists() else None,
            "batch_transcript": rel(transcript_path, session) if transcript_path else None,
            "batch_clean_dialogue": rel(clean_dialogue_path, session) if clean_dialogue_path else None,
            "batch_quality_report": rel(batch_quality_path, session) if batch_quality_path else None,
            "readiness": "derived/readiness/session_readiness.json" if (session / "derived/readiness/session_readiness.json").exists() else None,
            "outcome": "derived/outcome/outcome.json" if (session / "derived/outcome/outcome.json").exists() else None,
            "selected_batch_profile": profile,
        },
        "metrics": {
            "live_chunks": len(chunks),
            "live_token_count": len(live_tokens),
            "batch_token_count": len(final_tokens),
            "live_token_recall_in_batch": round(recall, 6) if recall is not None else None,
            "adjacent_duplicate_chunk_count": duplicate_count,
            "live_boundary_gate_evaluated_count": boundary_summary.get("evaluated_count"),
            "live_boundary_gate_issue_count": boundary_summary.get("issue_count"),
            "live_boundary_gate_suppressed_count": boundary_summary.get("suppressed_count"),
            "live_boundary_gate_resolved_suppressed_count": boundary_summary.get("resolved_suppressed_count"),
            "live_boundary_gate_unresolved_suppressed_count": boundary_summary.get("unresolved_suppressed_count"),
            "batch_authoritative": True,
            "batch_use_gate": batch_use_gate,
            "batch_outcome": batch_outcome,
            "batch_ready_for_notes": bool(batch_use_gate == "ready_for_notes" or batch_outcome == "ready_for_notes"),
            "all_parity_gates_passed": all_parity_gates_passed,
            "meaningful_live_comparison": meaningful_live_comparison,
            "capture_safety_status": capture_safety_gate.get("status"),
            "screen_capture_restart_count": (capture_safety_gate.get("evidence") or {}).get("screen_capture_restart_count"),
            "capture_safety_warning_count": (capture_safety_gate.get("evidence") or {}).get("safety_warning_count"),
            **live_metrics,
            **target_me_shadow_profile_metrics,
        },
        "shadow_profiles": {
            "target_me": target_me_shadow_profiles,
        },
        "risk_examples": {
            "order_mismatches": (live_assessment.get("order_mismatches") or [])[:20],
            "role_constrained_order_mismatches": (
                live_assessment.get("role_constrained_order_mismatches") or []
            )[:20],
            "contentful_role_constrained_order_mismatches": (
                live_assessment.get("contentful_role_constrained_order_mismatches") or []
            )[:20],
            "live_rescue_shadow_order_mismatches": (
                live_assessment.get("live_rescue_shadow_order_mismatches") or []
            )[:20],
            "live_rescue_shadow_role_constrained_order_mismatches": (
                live_assessment.get("live_rescue_shadow_role_constrained_order_mismatches") or []
            )[:20],
            "live_rescue_shadow_contentful_role_constrained_order_mismatches": (
                live_assessment.get("live_rescue_shadow_contentful_role_constrained_order_mismatches") or []
            )[:20],
            "local_missing": (live_assessment.get("local_missing") or [])[:20],
            "local_missing_suspicious_batch_me": (live_assessment.get("local_missing_suspicious_batch_me") or [])[:20],
            "local_missing_visible_in_suppressed_mic": (
                live_assessment.get("local_missing_visible_in_suppressed_mic") or []
            )[:20],
            "local_missing_not_visible_in_suppressed_mic": (
                live_assessment.get("local_missing_not_visible_in_suppressed_mic") or []
            )[:20],
            "remote_leak": (live_assessment.get("remote_leak") or [])[:20],
            "live_rescue_shadow_remote_leak": (
                live_assessment.get("live_rescue_shadow_remote_leak") or []
            )[:20],
            "suppressed_mic_asr_segments": (
                live_assessment.get("suppressed_mic_asr_segment_examples") or []
            )[:30],
            "segment_role_gate_candidates": (
                live_assessment.get("segment_role_gate_candidates") or []
            )[:30],
            "live_rescue_shadow": (
                live_assessment.get("live_rescue_shadow_examples") or []
            )[:30],
            "suppressed_mic_rescue_policies": (
                live_assessment.get("suppressed_mic_rescue_policy_examples") or {}
            ),
            "live_target_me_shadow": (
                live_assessment.get("live_target_me_shadow_examples") or {}
            ),
            "boundary_gate_issues": boundary_summary.get("examples") or [],
            "boundary_gate_resolved": boundary_summary.get("resolved_examples") or [],
        },
        "parity_gates": {
            "status": "not_promotable" if gate_statuses - {"passed"} else "passed_but_shadow_locked",
            "gates": gates,
        },
        "outputs": {
            "live_parity_session_report": rel(session_report_path, session),
            "live_parity_session_report_markdown": rel(session_report_md_path, session),
            "target_me_shadow_drafts": target_me_shadow_outputs,
        },
        "recommended_next": "murmurmark status " + str(session),
    }
    session_report = build_session_report(session, payload)
    write_json(comparison_path, payload)
    write_json(session_report_path, session_report)
    write_session_report_markdown(session_report_md_path, session_report)
    print(f"live_batch_comparison: {comparison_path}")
    print(f"live_parity_session_report: {session_report_path}")
    print(f"status: {payload['status']}")
    print("promotion_allowed: false")
    if warnings:
        print("warnings: " + ", ".join(warnings))
    if blockers:
        print("blockers: " + ", ".join(blockers))
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
