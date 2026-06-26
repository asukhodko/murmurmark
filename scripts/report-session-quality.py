#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_VERSION = "0.1.0"
SCHEMA = "murmurmark.session_quality_report/v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a private quality summary for MurmurMark sessions.")
    parser.add_argument("sessions", nargs="+", type=Path)
    parser.add_argument(
        "--label",
        action="append",
        default=[],
        metavar="SESSION=LABEL",
        help="Optional display label. Match by full path, relative path, or session directory name.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("sessions/_reports/session-quality"),
        help="Output directory. Default is under ignored sessions/.",
    )
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as file:
            value = json.load(file)
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def round_or_none(value: Any, digits: int = 3) -> float | None:
    number = safe_float(value)
    if number is None:
        return None
    return round(number, digits)


def suffix(profile: str) -> str:
    return "" if profile == "current" else f".{profile}"


def parse_labels(rows: list[str]) -> dict[str, str]:
    labels: dict[str, str] = {}
    for row in rows:
        if "=" not in row:
            raise SystemExit(f"--label must be SESSION=LABEL: {row}")
        key, value = row.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or not value:
            raise SystemExit(f"--label must be SESSION=LABEL: {row}")
        labels[key] = value
    return labels


def label_for(session: Path, labels: dict[str, str]) -> str:
    candidates = [
        str(session),
        str(session.as_posix()),
        session.name,
        f"./{session.as_posix()}",
    ]
    try:
        resolved = str(session.resolve())
        candidates.append(resolved)
    except OSError:
        pass
    for candidate in candidates:
        if candidate in labels:
            return labels[candidate]
    return session.name


def artifact(path: Path) -> dict[str, Any]:
    return {"path": str(path), "exists": path.exists()}


def first_existing(session: Path, names: list[str]) -> Path | None:
    for name in names:
        path = session / name
        if path.exists():
            return path
    return None


def session_duration_sec(session_json: dict[str, Any]) -> float | None:
    files = session_json.get("files")
    if isinstance(files, dict):
        durations: list[float] = []
        for key in ("mic", "remote"):
            rows = files.get(key)
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                frames = safe_float(row.get("frames"))
                sample_rate = safe_float(row.get("sample_rate"))
                start = safe_float(row.get("start_session_sec")) or 0.0
                if frames is not None and sample_rate and sample_rate > 0:
                    durations.append(start + frames / sample_rate)
        if durations:
            return round(max(durations), 3)

    created = session_json.get("created_at")
    ended = session_json.get("ended_at")
    if isinstance(created, str) and isinstance(ended, str):
        try:
            created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            ended_dt = datetime.fromisoformat(ended.replace("Z", "+00:00"))
            return round(max(0.0, (ended_dt - created_dt).total_seconds()), 3)
        except ValueError:
            return None
    return None


def selected_profile(session: Path) -> str:
    resolved = session / "derived/transcript-simple/whisper-cpp/resolved"
    cleanup = session / "derived/transcript-simple/whisper-cpp/audit-cleanup"
    review_decisions = session / "derived/transcript-simple/whisper-cpp/review-decisions"
    reviewed = read_json(review_decisions / "review_decisions_report.reviewed_v1.json")
    reviewed_gates = reviewed.get("gates") if isinstance(reviewed, dict) else {}
    if (
        (resolved / "quality_report.reviewed_v1.json").exists()
        and (resolved / "clean_dialogue.reviewed_v1.json").exists()
        and isinstance(reviewed_gates, dict)
        and reviewed_gates.get("passed") is True
    ):
        return "reviewed_v1"
    cleanup_v3 = read_json(cleanup / "audit_cleanup_report.audit_cleanup_v3.json")
    cleanup_v3_summary = cleanup_v3.get("summary") if isinstance(cleanup_v3, dict) else {}
    cleanup_v3_gates = cleanup_v3.get("gates") if isinstance(cleanup_v3, dict) else {}
    cleanup_v3_applied = safe_int(cleanup_v3_summary.get("applied_patches") if isinstance(cleanup_v3_summary, dict) else None) or 0
    if (
        (resolved / "quality_report.audit_cleanup_v3.json").exists()
        and (resolved / "clean_dialogue.audit_cleanup_v3.json").exists()
        and isinstance(cleanup_v3_gates, dict)
        and cleanup_v3_gates.get("passed") is True
        and cleanup_v3_applied > 0
    ):
        return "audit_cleanup_v3"
    if (resolved / "quality_report.audit_cleanup_v2.json").exists() and (resolved / "clean_dialogue.audit_cleanup_v2.json").exists():
        return "audit_cleanup_v2"
    if (resolved / "quality_report.audit_cleanup_v1.json").exists() and (resolved / "clean_dialogue.audit_cleanup_v1.json").exists():
        return "audit_cleanup_v1"
    if (resolved / "quality_report.shadow_v2.json").exists() and (resolved / "clean_dialogue.shadow_v2.json").exists():
        return "shadow_v2"
    if (resolved / "quality_report.json").exists() and (resolved / "clean_dialogue.json").exists():
        return "current"
    return "missing"


def stage_status(session: Path) -> dict[str, bool]:
    resolved = session / "derived/transcript-simple/whisper-cpp/resolved"
    synthesis = session / "derived/synthesis-simple/extractive"
    cleanup = session / "derived/transcript-simple/whisper-cpp/audit-cleanup"
    review_decisions = session / "derived/transcript-simple/whisper-cpp/review-decisions"
    audio_review = session / "derived/audit/audio-review-pack"
    return {
        "capture": (session / "session.json").exists()
        and (session / "audio/mic/000001.caf").exists()
        and (session / "audio/remote/000001.caf").exists(),
        "echo_local_fir": (session / "derived/preprocess/echo/local_fir_report.json").exists(),
        "transcript_current": (resolved / "quality_report.json").exists() and (resolved / "clean_dialogue.json").exists(),
        "transcript_shadow_v2": (resolved / "quality_report.shadow_v2.json").exists()
        and (resolved / "clean_dialogue.shadow_v2.json").exists()
        and (resolved / "repair_comparison.json").exists(),
        "group_overlap_audit": (session / "derived/audit/group-overlaps/group_overlap_summary.json").exists(),
        "audit_cleanup_v1": (resolved / "quality_report.audit_cleanup_v1.json").exists()
        and (resolved / "clean_dialogue.audit_cleanup_v1.json").exists()
        and (cleanup / "audit_cleanup_report.audit_cleanup_v1.json").exists(),
        "audit_cleanup_v2": (resolved / "quality_report.audit_cleanup_v2.json").exists()
        and (resolved / "clean_dialogue.audit_cleanup_v2.json").exists()
        and (cleanup / "audit_cleanup_report.audit_cleanup_v2.json").exists(),
        "audit_cleanup_v3": (resolved / "quality_report.audit_cleanup_v3.json").exists()
        and (resolved / "clean_dialogue.audit_cleanup_v3.json").exists()
        and (cleanup / "audit_cleanup_report.audit_cleanup_v3.json").exists(),
        "reviewed_v1": (resolved / "quality_report.reviewed_v1.json").exists()
        and (resolved / "clean_dialogue.reviewed_v1.json").exists()
        and (review_decisions / "review_decisions_report.reviewed_v1.json").exists(),
        "synthesis": (synthesis / "quality_verdict.json").exists() and (synthesis / "evidence_notes.json").exists(),
        "synthesis_audit_cleanup_v1": (synthesis / "quality_verdict.audit_cleanup_v1.json").exists()
        and (synthesis / "evidence_notes.audit_cleanup_v1.json").exists(),
        "synthesis_audit_cleanup_v2": (synthesis / "quality_verdict.audit_cleanup_v2.json").exists()
        and (synthesis / "evidence_notes.audit_cleanup_v2.json").exists(),
        "synthesis_audit_cleanup_v3": (synthesis / "quality_verdict.audit_cleanup_v3.json").exists()
        and (synthesis / "evidence_notes.audit_cleanup_v3.json").exists(),
        "synthesis_reviewed_v1": (synthesis / "quality_verdict.reviewed_v1.json").exists()
        and (synthesis / "evidence_notes.reviewed_v1.json").exists(),
        "audio_review_pack": (audio_review / "review_pack_summary.json").exists()
        and (audio_review / "review_pack_items.jsonl").exists(),
        "audio_review_audit": (audio_review / "audio_review_summary.json").exists()
        and (audio_review / "audio_review_audit.jsonl").exists(),
    }


def missing_artifacts(status: dict[str, bool]) -> list[str]:
    important = [
        "capture",
        "echo_local_fir",
        "transcript_current",
        "transcript_shadow_v2",
        "group_overlap_audit",
        "audit_cleanup_v1",
        "synthesis_audit_cleanup_v1",
        "audio_review_pack",
        "audio_review_audit",
    ]
    return [name for name in important if not status.get(name)]


def read_quality(session: Path, profile: str) -> dict[str, Any] | None:
    if profile == "missing":
        return None
    return read_json(session / "derived/transcript-simple/whisper-cpp/resolved" / f"quality_report{suffix(profile)}.json")


def read_verdict(session: Path, profile: str) -> dict[str, Any] | None:
    synthesis = session / "derived/synthesis-simple/extractive"
    preferred = synthesis / f"quality_verdict{suffix(profile)}.json"
    if preferred.exists():
        return read_json(preferred)
    return read_json(synthesis / "quality_verdict.json")


def read_evidence(session: Path, profile: str) -> dict[str, Any] | None:
    synthesis = session / "derived/synthesis-simple/extractive"
    preferred = synthesis / f"evidence_notes{suffix(profile)}.json"
    if preferred.exists():
        return read_json(preferred)
    return read_json(synthesis / "evidence_notes.json")


def dialogue_me_ids(session: Path, profile: str) -> set[str]:
    dialogue = read_json(session / "derived/transcript-simple/whisper-cpp/resolved" / f"clean_dialogue{suffix(profile)}.json")
    if not isinstance(dialogue, dict):
        return set()
    rows = dialogue.get("utterances")
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


def active_audio_review_row(row: dict[str, Any], selected_me_ids: set[str]) -> bool:
    classification = row.get("classification") if isinstance(row.get("classification"), dict) else {}
    label = str(classification.get("label") or "")
    me_ids = audio_review_me_ids(row)
    if label == "lost_me":
        return True
    if not me_ids:
        return True
    return bool(me_ids & selected_me_ids)


def selected_counts(evidence: dict[str, Any] | None) -> dict[str, int]:
    selected = evidence.get("selected") if isinstance(evidence, dict) else None
    if not isinstance(selected, dict):
        return {"decisions": 0, "actions": 0, "risks": 0, "open_questions": 0, "outline_blocks": 0}
    return {
        "decisions": len(selected.get("decisions") or []),
        "actions": len(selected.get("actions") or []),
        "risks": len(selected.get("risks") or []),
        "open_questions": len(selected.get("open_questions") or []),
        "outline_blocks": len(selected.get("outline_blocks") or []),
    }


def hidden_facilitation_count(evidence: dict[str, Any] | None) -> int:
    candidates = evidence.get("candidates") if isinstance(evidence, dict) else None
    if not isinstance(candidates, list):
        return 0
    return sum(1 for item in candidates if isinstance(item, dict) and item.get("subtype") == "meeting_facilitation" and item.get("status") == "hidden")


def cleanup_metrics(quality: dict[str, Any] | None, cleanup_report: dict[str, Any] | None) -> dict[str, Any]:
    nested = quality.get("audit_cleanup") if isinstance(quality, dict) else None
    if not isinstance(nested, dict):
        nested = {}
    summary = cleanup_report.get("summary") if isinstance(cleanup_report, dict) else None
    if not isinstance(summary, dict):
        summary = {}
    gates = cleanup_report.get("gates") if isinstance(cleanup_report, dict) else None
    if not isinstance(gates, dict):
        gates = {}
    return {
        "cleanup_gates_passed": gates.get("passed"),
        "applied_patches": safe_int(nested.get("applied_patches", summary.get("applied_patches"))),
        "rejected_patches": safe_int(nested.get("rejected_patches", summary.get("rejected_patches"))),
        "dropped_me_duplicate_seconds": round_or_none(nested.get("dropped_me_duplicate_seconds")),
        "dropped_me_noise_seconds": round_or_none(nested.get("dropped_me_noise_seconds")),
        "audit_harmful_seconds_before": round_or_none(nested.get("audit_harmful_seconds_before")),
        "audit_harmful_seconds_after": round_or_none(nested.get("audit_harmful_seconds_after")),
        "audit_benign_seconds": round_or_none(nested.get("audit_benign_seconds")),
        "audit_review_seconds": round_or_none(nested.get("audit_review_seconds")),
        "protected_intentional_repeat_count": safe_int(nested.get("protected_intentional_repeat_count")),
    }


def group_metrics(group_summary: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(group_summary, dict):
        return {}
    classified = group_summary.get("classified")
    if not isinstance(classified, dict):
        classified = {}
    harmful = group_summary.get("harmful")
    benign = group_summary.get("benign_or_expected")
    review = group_summary.get("review")
    return {
        "group_overlap_count": safe_int(classified.get("total_overlap_count")),
        "group_overlap_seconds": round_or_none(classified.get("total_overlap_seconds")),
        "group_harmful_seconds": round_or_none(harmful.get("seconds") if isinstance(harmful, dict) else None),
        "group_benign_seconds": round_or_none(benign.get("seconds") if isinstance(benign, dict) else None),
        "group_review_seconds": round_or_none(review.get("seconds") if isinstance(review, dict) else None),
        "group_review_count": safe_int(review.get("count") if isinstance(review, dict) else None),
        "group_recommended_verdict": (
            group_summary.get("recommended_verdict_adjustment", {}).get("new")
            if isinstance(group_summary.get("recommended_verdict_adjustment"), dict)
            else None
        ),
    }


def echo_metrics(local_fir_report: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(local_fir_report, dict):
        return {}
    decision = local_fir_report.get("decision")
    metrics = local_fir_report.get("metrics")
    if not isinstance(decision, dict):
        decision = {}
    if not isinstance(metrics, dict):
        metrics = {}
    return {
        "echo_accepted_for_asr": decision.get("accepted_for_asr"),
        "echo_reduction_db": round_or_none(metrics.get("estimated_echo_reduction_db")),
        "remote_only_reduction_db": round_or_none(metrics.get("remote_only_median_reduction_db")),
        "local_energy_delta_db": round_or_none(metrics.get("local_only_energy_delta_db_median")),
        "local_vad_ratio": round_or_none(metrics.get("local_only_vad_duration_ratio")),
        "echo_segments_processed": safe_int(metrics.get("segments_processed")),
        "echo_segments_rejected": safe_int(metrics.get("segments_rejected")),
    }


def review_decision_metrics(review_report: dict[str, Any] | None) -> dict[str, Any]:
    summary = review_report.get("summary") if isinstance(review_report, dict) else None
    if not isinstance(summary, dict):
        summary = {}
    gates = review_report.get("gates") if isinstance(review_report, dict) else None
    if not isinstance(gates, dict):
        gates = {}
    return {
        "review_decisions_gates_passed": gates.get("passed"),
        "review_decisions_applied": safe_int(summary.get("applied_decision_rows")),
        "review_decisions_rejected": safe_int(summary.get("rejected_decision_rows")),
        "review_decisions_conflicts": safe_int(summary.get("conflict_count")),
        "review_decisions_dropped_me": safe_int(summary.get("dropped_me_utterances")),
        "review_decisions_dropped_me_seconds": round_or_none(summary.get("dropped_me_seconds")),
        "review_decisions_keep_me": safe_int(summary.get("kept_me_decisions")),
        "review_decisions_needs_review": safe_int(summary.get("needs_review_decisions")),
    }


def audio_review_metrics(audio_summary: dict[str, Any] | None, session: Path, profile: str) -> dict[str, Any]:
    if not isinstance(audio_summary, dict):
        return {}

    audit_path = session / "derived/audit/audio-review-pack/audio_review_audit.jsonl"
    selected_me_ids = dialogue_me_ids(session, profile)
    audit_rows: list[dict[str, Any]] = []
    if audit_path.exists() and selected_me_ids:
        with audit_path.open("r", encoding="utf-8") as file:
            for line in file:
                line = line.strip()
                if not line:
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    audit_rows.append(value)

    if audit_rows:
        buckets = {
            "likely_reliable": {"count": 0, "seconds": 0.0},
            "probable_error": {"count": 0, "seconds": 0.0},
            "needs_stronger_audio_judge": {"count": 0, "seconds": 0.0},
        }
        resolved_count = 0
        resolved_seconds = 0.0
        active_count = 0
        active_seconds = 0.0
        for row in audit_rows:
            interval = row.get("interval") if isinstance(row.get("interval"), dict) else {}
            seconds = safe_float(interval.get("duration_sec")) or 0.0
            if not active_audio_review_row(row, selected_me_ids):
                resolved_count += 1
                resolved_seconds += seconds
                continue
            active_count += 1
            active_seconds += seconds
            classification = row.get("classification") if isinstance(row.get("classification"), dict) else {}
            verdict = str(classification.get("verdict") or "")
            if verdict == "probable_transcript_error":
                verdict = "probable_error"
            if verdict in buckets:
                buckets[verdict]["count"] += 1
                buckets[verdict]["seconds"] += seconds

        raw_error = audio_summary.get("probable_error") if isinstance(audio_summary.get("probable_error"), dict) else {}
        raw_stronger = (
            audio_summary.get("needs_stronger_audio_judge")
            if isinstance(audio_summary.get("needs_stronger_audio_judge"), dict)
            else {}
        )
        return {
            "audio_review_items": active_count,
            "audio_review_seconds": round(active_seconds, 3),
            "audio_review_reliable_count": buckets["likely_reliable"]["count"],
            "audio_review_reliable_seconds": round(buckets["likely_reliable"]["seconds"], 3),
            "audio_review_probable_error_count": buckets["probable_error"]["count"],
            "audio_review_probable_error_seconds": round(buckets["probable_error"]["seconds"], 3),
            "audio_review_stronger_judge_count": buckets["needs_stronger_audio_judge"]["count"],
            "audio_review_stronger_judge_seconds": round(buckets["needs_stronger_audio_judge"]["seconds"], 3),
            "audio_review_resolved_by_cleanup_count": resolved_count,
            "audio_review_resolved_by_cleanup_seconds": round(resolved_seconds, 3),
            "audio_review_raw_probable_error_count": safe_int(raw_error.get("count")),
            "audio_review_raw_probable_error_seconds": round_or_none(raw_error.get("seconds")),
            "audio_review_raw_stronger_judge_count": safe_int(raw_stronger.get("count")),
            "audio_review_raw_stronger_judge_seconds": round_or_none(raw_stronger.get("seconds")),
            "audio_review_recommended_next_step": audio_summary.get("recommended_next_step"),
        }

    reliable = audio_summary.get("likely_reliable") if isinstance(audio_summary.get("likely_reliable"), dict) else {}
    error = audio_summary.get("probable_error") if isinstance(audio_summary.get("probable_error"), dict) else {}
    stronger = (
        audio_summary.get("needs_stronger_audio_judge")
        if isinstance(audio_summary.get("needs_stronger_audio_judge"), dict)
        else {}
    )
    return {
        "audio_review_items": safe_int(audio_summary.get("items")),
        "audio_review_reliable_count": safe_int(reliable.get("count")),
        "audio_review_reliable_seconds": round_or_none(reliable.get("seconds")),
        "audio_review_probable_error_count": safe_int(error.get("count")),
        "audio_review_probable_error_seconds": round_or_none(error.get("seconds")),
        "audio_review_stronger_judge_count": safe_int(stronger.get("count")),
        "audio_review_stronger_judge_seconds": round_or_none(stronger.get("seconds")),
        "audio_review_recommended_next_step": audio_summary.get("recommended_next_step"),
    }


def risk_flags(row: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    verdict = row.get("verdict")
    if verdict in {"risky", "failed"}:
        flags.append(f"verdict:{verdict}")
    if row.get("selected_profile") not in {"audit_cleanup_v1", "audit_cleanup_v2", "audit_cleanup_v3", "reviewed_v1"}:
        flags.append("no_audit_cleanup_profile")
    if row.get("selected_profile") == "reviewed_v1" and row.get("review_decisions_gates_passed") is not True:
        flags.append("review_decisions_gates_failed")
    missing = row.get("missing_artifacts") or []
    if missing:
        flags.append("missing:" + ",".join(missing[:3]))
    if safe_int(row.get("unrepaired_long_mic_crossings_count")):
        flags.append("unrepaired_long_mic_crossings")
    if safe_int(row.get("golden_phrase_fail_count")):
        flags.append("golden_phrase_fail")
    recall = safe_float(row.get("local_only_island_recall"))
    if recall is not None and recall < 0.9:
        flags.append("low_local_recall")
    needs_ratio = safe_float(row.get("needs_review_ratio"))
    if needs_ratio is not None and needs_ratio > 0.12:
        flags.append("high_needs_review_ratio")
    duration = safe_float(row.get("meeting_duration_sec")) or 0.0
    harmful = safe_float(row.get("audit_harmful_seconds_after"))
    review = safe_float(row.get("audit_review_seconds"))
    if duration > 0 and harmful is not None and harmful / duration > 0.03:
        flags.append("high_harmful_overlap_ratio")
    if duration > 0 and review is not None and review / duration > 0.12:
        flags.append("high_review_overlap_ratio")
    audio_error_count = safe_int(row.get("audio_review_probable_error_count")) or 0
    audio_error_seconds = safe_float(row.get("audio_review_probable_error_seconds")) or 0.0
    if audio_error_count >= 3 or audio_error_seconds >= 10.0:
        flags.append("audio_review_probable_errors")
    audio_judge_count = safe_int(row.get("audio_review_stronger_judge_count")) or 0
    audio_judge_seconds = safe_float(row.get("audio_review_stronger_judge_seconds")) or 0.0
    if audio_judge_count >= 20 or audio_judge_seconds >= 60.0:
        flags.append("needs_stronger_audio_judge")
    return flags


def collect_session(session: Path, labels: dict[str, str]) -> dict[str, Any]:
    status = stage_status(session)
    profile = selected_profile(session)
    quality = read_quality(session, profile)
    verdict = read_verdict(session, profile)
    evidence = read_evidence(session, profile)
    group_summary = read_json(session / "derived/audit/group-overlaps/group_overlap_summary.json")
    audio_summary = read_json(session / "derived/audit/audio-review-pack/audio_review_summary.json")
    local_fir = read_json(session / "derived/preprocess/echo/local_fir_report.json")
    cleanup_report = (
        read_json(session / "derived/transcript-simple/whisper-cpp/audit-cleanup" / f"audit_cleanup_report{suffix(profile)}.json")
        if profile in {"audit_cleanup_v1", "audit_cleanup_v2", "audit_cleanup_v3"}
        else None
    )
    review_report = (
        read_json(session / "derived/transcript-simple/whisper-cpp/review-decisions/review_decisions_report.reviewed_v1.json")
        if profile == "reviewed_v1"
        else None
    )
    session_json = read_json(session / "session.json") or {}

    metrics = verdict.get("metrics") if isinstance(verdict, dict) else None
    if not isinstance(metrics, dict):
        metrics = {}
    counts = selected_counts(evidence)
    missing = missing_artifacts(status)

    row: dict[str, Any] = {
        "session": str(session),
        "session_id": session.name,
        "label": label_for(session, labels),
        "selected_profile": profile,
        "pipeline_status": "complete" if not missing else ("partial" if status.get("transcript_current") else "incomplete"),
        "missing_artifacts": missing,
        "stages": status,
        "verdict": verdict.get("verdict") if isinstance(verdict, dict) else None,
        "selected_transcript_profile": verdict.get("selected_transcript_profile") if isinstance(verdict, dict) else None,
        "risk_items_count": len(verdict.get("risk_items") or []) if isinstance(verdict, dict) else None,
        "created_at": session_json.get("created_at"),
        "ended_at": session_json.get("ended_at"),
        "meeting_duration_sec": round_or_none(
            (quality or {}).get("meeting_duration_sec", metrics.get("meeting_duration_sec", session_duration_sec(session_json)))
        ),
        "utterances": safe_int((quality or {}).get("utterances", metrics.get("utterances"))),
        "needs_review_count": safe_int((quality or {}).get("needs_review_count", metrics.get("needs_review_count"))),
        "needs_review_ratio": round_or_none(metrics.get("needs_review_ratio")),
        "cross_role_overlap_gt2_count": safe_int(
            (quality or {}).get("cross_role_overlap_gt2_count", metrics.get("cross_role_overlap_gt2_count"))
        ),
        "cross_role_overlap_gt2_seconds": round_or_none(
            (quality or {}).get("cross_role_overlap_gt2_seconds", metrics.get("cross_role_overlap_gt2_seconds"))
        ),
        "remote_duplicate_in_me_seconds": round_or_none(
            (quality or {}).get("remote_duplicate_in_me_seconds", metrics.get("remote_duplicate_in_me_seconds"))
        ),
        "unrepaired_long_mic_crossings_count": safe_int(
            (quality or {}).get("unrepaired_long_mic_crossings_count", metrics.get("unrepaired_long_mic_crossings_count"))
        ),
        "golden_phrase_fail_count": safe_int((quality or {}).get("golden_phrase_fail_count", metrics.get("golden_phrase_fail_count"))),
        "local_only_island_recall": round_or_none((quality or {}).get("local_only_island_recall", metrics.get("local_only_island_recall")), 6),
        "micro_reasr_success_count": safe_int((quality or {}).get("micro_reasr_success_count")),
        "micro_reasr_attempt_count": safe_int((quality or {}).get("micro_reasr_attempt_count")),
        "selected_notes": counts,
        "hidden_meeting_facilitation_count": hidden_facilitation_count(evidence),
        "artifacts": {
            "session_json": artifact(session / "session.json"),
            "quality_report": artifact(
                session / "derived/transcript-simple/whisper-cpp/resolved" / f"quality_report{suffix(profile)}.json"
            )
            if profile != "missing"
            else None,
            "quality_verdict": artifact(
                session / "derived/synthesis-simple/extractive" / f"quality_verdict{suffix(profile)}.json"
            )
            if profile != "missing"
            else None,
        },
    }
    row.update(echo_metrics(local_fir))
    row.update(group_metrics(group_summary))
    row.update(cleanup_metrics(quality, cleanup_report))
    row.update(review_decision_metrics(review_report))
    row.update(audio_review_metrics(audio_summary, session, profile))
    row["risk_flags"] = risk_flags(row)
    return row


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    verdicts = Counter(str(row.get("verdict") or "missing") for row in rows)
    profiles = Counter(str(row.get("selected_profile") or "missing") for row in rows)
    durations = [safe_float(row.get("meeting_duration_sec")) for row in rows]
    durations = [value for value in durations if value is not None]
    risk_rows = [row for row in rows if row.get("risk_flags")]
    complete = sum(1 for row in rows if row.get("pipeline_status") == "complete")
    return {
        "session_count": len(rows),
        "complete_pipeline_count": complete,
        "partial_or_incomplete_count": len(rows) - complete,
        "total_duration_sec": round(sum(durations), 3),
        "total_duration_min": round(sum(durations) / 60.0, 2),
        "median_duration_min": round(statistics.median(durations) / 60.0, 2) if durations else None,
        "by_verdict": dict(sorted(verdicts.items())),
        "by_selected_profile": dict(sorted(profiles.items())),
        "sessions_with_risk_flags": len(risk_rows),
        "top_risk_sessions": [
            {"session_id": row["session_id"], "label": row["label"], "risk_flags": row["risk_flags"]}
            for row in risk_rows[:10]
        ],
    }


def csv_value(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "session_id",
        "label",
        "pipeline_status",
        "selected_profile",
        "verdict",
        "meeting_duration_sec",
        "utterances",
        "needs_review_count",
        "needs_review_ratio",
        "local_only_island_recall",
        "cross_role_overlap_gt2_seconds",
        "remote_duplicate_in_me_seconds",
        "audit_harmful_seconds_after",
        "audit_review_seconds",
        "applied_patches",
        "dropped_me_duplicate_seconds",
        "dropped_me_noise_seconds",
        "review_decisions_applied",
        "review_decisions_dropped_me",
        "review_decisions_dropped_me_seconds",
        "echo_reduction_db",
        "selected_notes",
        "hidden_meeting_facilitation_count",
        "audio_review_items",
        "audio_review_probable_error_count",
        "audio_review_stronger_judge_count",
        "risk_flags",
        "missing_artifacts",
    ]
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: csv_value(row.get(field)) for field in fields})


def fmt(value: Any, missing: str = "n/a") -> str:
    if value is None:
        return missing
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".")
    return str(value)


def write_markdown(path: Path, payload: dict[str, Any]) -> None:
    rows = payload["sessions"]
    lines = [
        "# MurmurMark Session Quality Report",
        "",
        f"Generated: `{payload['generated_at']}`",
        f"Sessions: `{payload['summary']['session_count']}`",
        f"Total duration: `{payload['summary']['total_duration_min']} min`",
        "",
        "## Summary",
        "",
        f"- Complete pipeline: `{payload['summary']['complete_pipeline_count']}` / `{payload['summary']['session_count']}`",
        f"- Verdicts: `{json.dumps(payload['summary']['by_verdict'], ensure_ascii=False, sort_keys=True)}`",
        f"- Profiles: `{json.dumps(payload['summary']['by_selected_profile'], ensure_ascii=False, sort_keys=True)}`",
        f"- Sessions with risk flags: `{payload['summary']['sessions_with_risk_flags']}`",
        "",
        "## Sessions",
        "",
        "| Session | Label | Status | Profile | Verdict | Min | Utterances | Needs Review | Local Recall | Harmful s | Review s | Audio Review | Actions/Decisions | Flags | Missing |",
        "|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for row in rows:
        duration = safe_float(row.get("meeting_duration_sec"))
        notes = row.get("selected_notes") or {}
        actions_decisions = f"{notes.get('actions', 0)}/{notes.get('decisions', 0)}"
        audio_review = (
            f"{fmt(row.get('audio_review_reliable_count'), '0')}/"
            f"{fmt(row.get('audio_review_probable_error_count'), '0')}/"
            f"{fmt(row.get('audio_review_stronger_judge_count'), '0')}"
        )
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{row['session_id']}`",
                    row["label"].replace("|", "\\|"),
                    row["pipeline_status"],
                    row["selected_profile"],
                    fmt(row.get("verdict")),
                    fmt(round(duration / 60.0, 1) if duration is not None else None),
                    fmt(row.get("utterances")),
                    fmt(row.get("needs_review_count")),
                    fmt(row.get("local_only_island_recall")),
                    fmt(row.get("audit_harmful_seconds_after")),
                    fmt(row.get("audit_review_seconds")),
                    audio_review,
                    actions_decisions,
                    ", ".join(row.get("risk_flags") or []),
                    ", ".join(row.get("missing_artifacts") or []),
                ]
            )
            + " |"
        )
    lines.extend(["", "## Recommended Next Checks", ""])
    missing_cleanup = [row for row in rows if "audit_cleanup_v1" in (row.get("missing_artifacts") or [])]
    if missing_cleanup:
        lines.append("- Regenerate group audit and `audit_cleanup_v1` for:")
        for row in missing_cleanup:
            lines.append(f"  - `{row['session_id']}`: {row['label']}")
    missing_audio_review = [row for row in rows if "audio_review_audit" in (row.get("missing_artifacts") or [])]
    if missing_audio_review:
        lines.append("- Build and audit audio review packs for:")
        for row in missing_audio_review:
            lines.append(f"  - `{row['session_id']}`: {row['label']}")
    risky = [row for row in rows if row.get("risk_flags")]
    if risky:
        lines.append("- Review sessions with risk flags before using notes as working memory:")
        for row in risky[:10]:
            lines.append(f"  - `{row['session_id']}`: {', '.join(row['risk_flags'])}")
    if not missing_cleanup and not risky:
        lines.append("- No immediate structural gaps detected.")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    labels = parse_labels(args.label)
    rows = [collect_session(session, labels) for session in args.sessions]
    payload = {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": {"name": "report-session-quality", "version": SCRIPT_VERSION},
        "summary": aggregate(rows),
        "sessions": rows,
    }

    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json_path = out_dir / "session_quality_report.json"
    write_json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_csv(out_dir / "session_quality_report.csv", rows)
    write_markdown(out_dir / "session_quality_report.md", payload)
    print(f"written: {write_json_path}")
    print(f"markdown: {out_dir / 'session_quality_report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
