#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import shlex
import statistics
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_VERSION = "0.4.0"
SCHEMA = "murmurmark.session_quality_report/v1"
READINESS_SCHEMA = "murmurmark.session_readiness/v1"
CLEANUP_PROFILES = {
    "audit_cleanup_v1",
    "audit_cleanup_v2",
    "audit_cleanup_v3",
    "audit_cleanup_v4",
    "audit_cleanup_v5",
    "audit_cleanup_v6",
    "order_repair_v1",
    "reviewed_v1",
    "agent_reviewed_v1",
}


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
    parser.add_argument(
        "--write-session-readiness",
        action="store_true",
        help="Also write SESSION/derived/readiness/session_readiness.{json,md} for every input session.",
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


def rel(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


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
    order_repair = session / "derived/transcript-simple/whisper-cpp/order-repair"
    order_repair_v1 = read_json(order_repair / "transcript_order_repair_report.order_repair_v1.json")
    order_repair_summary = order_repair_v1.get("summary") if isinstance(order_repair_v1, dict) else {}
    order_repair_gates = order_repair_v1.get("gates") if isinstance(order_repair_v1, dict) else {}
    order_repair_applied = safe_int(order_repair_summary.get("applied_repairs") if isinstance(order_repair_summary, dict) else None) or 0

    def order_repair_usable_for(profile: str) -> bool:
        return (
            (resolved / "quality_report.order_repair_v1.json").exists()
            and (resolved / "clean_dialogue.order_repair_v1.json").exists()
            and isinstance(order_repair_gates, dict)
            and order_repair_gates.get("passed") is True
            and order_repair_applied > 0
            and isinstance(order_repair_v1, dict)
            and order_repair_v1.get("input_profile") == profile
        )

    reviewed = read_json(review_decisions / "review_decisions_report.reviewed_v1.json")
    reviewed_gates = reviewed.get("gates") if isinstance(reviewed, dict) else {}
    if (
        (resolved / "quality_report.reviewed_v1.json").exists()
        and (resolved / "clean_dialogue.reviewed_v1.json").exists()
        and isinstance(reviewed_gates, dict)
        and reviewed_gates.get("passed") is True
    ):
        if order_repair_usable_for("reviewed_v1"):
            return "order_repair_v1"
        return "reviewed_v1"
    agent = read_json(review_decisions / "review_decisions_report.agent_reviewed_v1.json")
    agent_gates = agent.get("gates") if isinstance(agent, dict) else {}
    if (
        (resolved / "quality_report.agent_reviewed_v1.json").exists()
        and (resolved / "clean_dialogue.agent_reviewed_v1.json").exists()
        and isinstance(agent_gates, dict)
        and agent_gates.get("passed") is True
    ):
        if order_repair_usable_for("agent_reviewed_v1"):
            return "order_repair_v1"
        return "agent_reviewed_v1"
    cleanup_v6 = read_json(cleanup / "audit_cleanup_report.audit_cleanup_v6.json")
    cleanup_v6_summary = cleanup_v6.get("summary") if isinstance(cleanup_v6, dict) else {}
    cleanup_v6_gates = cleanup_v6.get("gates") if isinstance(cleanup_v6, dict) else {}
    cleanup_v6_applied = safe_int(cleanup_v6_summary.get("applied_patches") if isinstance(cleanup_v6_summary, dict) else None) or 0
    if (
        (resolved / "quality_report.audit_cleanup_v6.json").exists()
        and (resolved / "clean_dialogue.audit_cleanup_v6.json").exists()
        and isinstance(cleanup_v6_gates, dict)
        and cleanup_v6_gates.get("passed") is True
        and cleanup_v6_applied > 0
    ):
        if order_repair_usable_for("audit_cleanup_v6"):
            return "order_repair_v1"
        return "audit_cleanup_v6"
    cleanup_v5 = read_json(cleanup / "audit_cleanup_report.audit_cleanup_v5.json")
    cleanup_v5_summary = cleanup_v5.get("summary") if isinstance(cleanup_v5, dict) else {}
    cleanup_v5_gates = cleanup_v5.get("gates") if isinstance(cleanup_v5, dict) else {}
    cleanup_v5_applied = safe_int(cleanup_v5_summary.get("applied_patches") if isinstance(cleanup_v5_summary, dict) else None) or 0
    if (
        (resolved / "quality_report.audit_cleanup_v5.json").exists()
        and (resolved / "clean_dialogue.audit_cleanup_v5.json").exists()
        and isinstance(cleanup_v5_gates, dict)
        and cleanup_v5_gates.get("passed") is True
        and cleanup_v5_applied > 0
    ):
        if order_repair_usable_for("audit_cleanup_v5"):
            return "order_repair_v1"
        return "audit_cleanup_v5"
    cleanup_v4 = read_json(cleanup / "audit_cleanup_report.audit_cleanup_v4.json")
    cleanup_v4_summary = cleanup_v4.get("summary") if isinstance(cleanup_v4, dict) else {}
    cleanup_v4_gates = cleanup_v4.get("gates") if isinstance(cleanup_v4, dict) else {}
    cleanup_v4_applied = safe_int(cleanup_v4_summary.get("applied_patches") if isinstance(cleanup_v4_summary, dict) else None) or 0
    if (
        (resolved / "quality_report.audit_cleanup_v4.json").exists()
        and (resolved / "clean_dialogue.audit_cleanup_v4.json").exists()
        and isinstance(cleanup_v4_gates, dict)
        and cleanup_v4_gates.get("passed") is True
        and cleanup_v4_applied > 0
    ):
        if order_repair_usable_for("audit_cleanup_v4"):
            return "order_repair_v1"
        return "audit_cleanup_v4"
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
        if order_repair_usable_for("audit_cleanup_v3"):
            return "order_repair_v1"
        return "audit_cleanup_v3"
    if (resolved / "quality_report.audit_cleanup_v2.json").exists() and (resolved / "clean_dialogue.audit_cleanup_v2.json").exists():
        if order_repair_usable_for("audit_cleanup_v2"):
            return "order_repair_v1"
        return "audit_cleanup_v2"
    if (resolved / "quality_report.audit_cleanup_v1.json").exists() and (resolved / "clean_dialogue.audit_cleanup_v1.json").exists():
        if order_repair_usable_for("audit_cleanup_v1"):
            return "order_repair_v1"
        return "audit_cleanup_v1"
    if (resolved / "quality_report.shadow_v2.json").exists() and (resolved / "clean_dialogue.shadow_v2.json").exists():
        if order_repair_usable_for("shadow_v2"):
            return "order_repair_v1"
        return "shadow_v2"
    if (resolved / "quality_report.json").exists() and (resolved / "clean_dialogue.json").exists():
        if order_repair_usable_for("current"):
            return "order_repair_v1"
        return "current"
    return "missing"


def stage_status(session: Path) -> dict[str, bool]:
    resolved = session / "derived/transcript-simple/whisper-cpp/resolved"
    synthesis = session / "derived/synthesis-simple/extractive"
    cleanup = session / "derived/transcript-simple/whisper-cpp/audit-cleanup"
    review_decisions = session / "derived/transcript-simple/whisper-cpp/review-decisions"
    audio_review = session / "derived/audit/audio-review-pack"
    order_audit = session / "derived/audit/order"
    order_repair = session / "derived/transcript-simple/whisper-cpp/order-repair"
    remote_leak_repair = session / "derived/transcript-simple/whisper-cpp/remote-leak-repair"
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
        "transcript_order_audit": (order_audit / "transcript_order_audit.json").exists()
        and (order_audit / "transcript_order_items.jsonl").exists(),
        "remote_leak_segment_plan": (remote_leak_repair / "remote_leak_segment_repair_plan.json").exists()
        and (remote_leak_repair / "remote_leak_segment_repair_items.jsonl").exists(),
        "audit_cleanup_v1": (resolved / "quality_report.audit_cleanup_v1.json").exists()
        and (resolved / "clean_dialogue.audit_cleanup_v1.json").exists()
        and (cleanup / "audit_cleanup_report.audit_cleanup_v1.json").exists(),
        "audit_cleanup_v2": (resolved / "quality_report.audit_cleanup_v2.json").exists()
        and (resolved / "clean_dialogue.audit_cleanup_v2.json").exists()
        and (cleanup / "audit_cleanup_report.audit_cleanup_v2.json").exists(),
        "audit_cleanup_v3": (resolved / "quality_report.audit_cleanup_v3.json").exists()
        and (resolved / "clean_dialogue.audit_cleanup_v3.json").exists()
        and (cleanup / "audit_cleanup_report.audit_cleanup_v3.json").exists(),
        "audit_cleanup_v4": (resolved / "quality_report.audit_cleanup_v4.json").exists()
        and (resolved / "clean_dialogue.audit_cleanup_v4.json").exists()
        and (cleanup / "audit_cleanup_report.audit_cleanup_v4.json").exists(),
        "audit_cleanup_v5": (resolved / "quality_report.audit_cleanup_v5.json").exists()
        and (resolved / "clean_dialogue.audit_cleanup_v5.json").exists()
        and (cleanup / "audit_cleanup_report.audit_cleanup_v5.json").exists(),
        "audit_cleanup_v6": (resolved / "quality_report.audit_cleanup_v6.json").exists()
        and (resolved / "clean_dialogue.audit_cleanup_v6.json").exists()
        and (cleanup / "audit_cleanup_report.audit_cleanup_v6.json").exists(),
        "reviewed_v1": (resolved / "quality_report.reviewed_v1.json").exists()
        and (resolved / "clean_dialogue.reviewed_v1.json").exists()
        and (review_decisions / "review_decisions_report.reviewed_v1.json").exists(),
        "agent_reviewed_v1": (resolved / "quality_report.agent_reviewed_v1.json").exists()
        and (resolved / "clean_dialogue.agent_reviewed_v1.json").exists()
        and (review_decisions / "review_decisions_report.agent_reviewed_v1.json").exists(),
        "suggested_review_v1": (resolved / "quality_report.suggested_review_v1.json").exists()
        and (resolved / "clean_dialogue.suggested_review_v1.json").exists()
        and (review_decisions / "review_decisions_report.suggested_review_v1.json").exists(),
        "order_repair_v1": (resolved / "quality_report.order_repair_v1.json").exists()
        and (resolved / "clean_dialogue.order_repair_v1.json").exists()
        and (order_repair / "transcript_order_repair_report.order_repair_v1.json").exists(),
        "synthesis": (synthesis / "quality_verdict.json").exists() and (synthesis / "evidence_notes.json").exists(),
        "synthesis_audit_cleanup_v1": (synthesis / "quality_verdict.audit_cleanup_v1.json").exists()
        and (synthesis / "evidence_notes.audit_cleanup_v1.json").exists(),
        "synthesis_audit_cleanup_v2": (synthesis / "quality_verdict.audit_cleanup_v2.json").exists()
        and (synthesis / "evidence_notes.audit_cleanup_v2.json").exists(),
        "synthesis_audit_cleanup_v3": (synthesis / "quality_verdict.audit_cleanup_v3.json").exists()
        and (synthesis / "evidence_notes.audit_cleanup_v3.json").exists(),
        "synthesis_audit_cleanup_v4": (synthesis / "quality_verdict.audit_cleanup_v4.json").exists()
        and (synthesis / "evidence_notes.audit_cleanup_v4.json").exists(),
        "synthesis_audit_cleanup_v5": (synthesis / "quality_verdict.audit_cleanup_v5.json").exists()
        and (synthesis / "evidence_notes.audit_cleanup_v5.json").exists(),
        "synthesis_audit_cleanup_v6": (synthesis / "quality_verdict.audit_cleanup_v6.json").exists()
        and (synthesis / "evidence_notes.audit_cleanup_v6.json").exists(),
        "synthesis_reviewed_v1": (synthesis / "quality_verdict.reviewed_v1.json").exists()
        and (synthesis / "evidence_notes.reviewed_v1.json").exists(),
        "synthesis_agent_reviewed_v1": (synthesis / "quality_verdict.agent_reviewed_v1.json").exists()
        and (synthesis / "evidence_notes.agent_reviewed_v1.json").exists(),
        "synthesis_suggested_review_v1": (synthesis / "quality_verdict.suggested_review_v1.json").exists()
        and (synthesis / "evidence_notes.suggested_review_v1.json").exists(),
        "synthesis_order_repair_v1": (synthesis / "quality_verdict.order_repair_v1.json").exists()
        and (synthesis / "evidence_notes.order_repair_v1.json").exists(),
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
        "transcript_order_audit",
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


def review_resolved_audio_ids(session: Path, profile: str) -> set[str]:
    if profile not in {"reviewed_v1", "agent_reviewed_v1"}:
        return set()
    path = (
        session
        / "derived/transcript-simple/whisper-cpp/review-decisions"
        / f"review_decisions_applied{suffix(profile)}.jsonl"
    )
    resolved: set[str] = set()
    for row in read_jsonl(path):
        if str(row.get("source") or "") != "audio_review":
            continue
        if str(row.get("decision") or "") not in {"drop_me", "keep_me", "skip"}:
            continue
        source_id = str(row.get("source_audit_id") or "")
        if source_id:
            resolved.add(source_id)
    return resolved


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


def synthesis_review_metrics(verdict: dict[str, Any] | None) -> dict[str, Any]:
    summary = verdict.get("review_summary") if isinstance(verdict, dict) and isinstance(verdict.get("review_summary"), dict) else {}
    by_type = summary.get("by_type") if isinstance(summary.get("by_type"), dict) else {}
    by_severity = summary.get("by_severity") if isinstance(summary.get("by_severity"), dict) else {}
    top_types: list[dict[str, Any]] = []
    for item_type, bucket in by_type.items():
        if not isinstance(bucket, dict):
            continue
        top_types.append(
            {
                "type": str(item_type),
                "count": safe_int(bucket.get("count")) or 0,
                "seconds": round_or_none(bucket.get("seconds")) or 0.0,
            }
        )
    top_types.sort(key=lambda item: (-int(item["count"]), str(item["type"])))
    return {
        "synthesis_review_item_count": safe_int(summary.get("review_item_count")),
        "synthesis_review_item_seconds": round_or_none(summary.get("review_item_seconds")),
        "synthesis_review_items_by_type": by_type,
        "synthesis_review_items_by_severity": by_severity,
        "synthesis_review_top_types": top_types[:5],
    }


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
    coverage = review_report.get("coverage") if isinstance(review_report, dict) else None
    if not isinstance(coverage, dict):
        coverage = {}
    return {
        "review_decisions_gates_passed": gates.get("passed"),
        "review_scope_complete": coverage.get("complete"),
        "review_scope_required_rows": safe_int(coverage.get("required_rows")),
        "review_scope_closed_rows": safe_int(coverage.get("closed_rows")),
        "review_scope_coverage_ratio": round_or_none(coverage.get("coverage_ratio"), 6),
        "review_scope_missing_rows": safe_int(coverage.get("missing_rows")),
        "review_scope_pending_rows": safe_int(coverage.get("pending_rows")),
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
    review_resolved_ids = review_resolved_audio_ids(session, profile)
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
            "likely_reliable": {"count": 0, "intervals": []},
            "probable_error": {"count": 0, "intervals": []},
            "needs_stronger_audio_judge": {"count": 0, "intervals": []},
        }
        resolved_count = 0
        resolved_by_review_count = 0
        resolved_intervals: list[tuple[float, float]] = []
        resolved_by_review_intervals: list[tuple[float, float]] = []
        remote_leak_intervals: list[tuple[float, float]] = []
        active_count = 0
        active_intervals: list[tuple[float, float]] = []
        for row in audit_rows:
            source_id = str(row.get("id") or "")
            interval = row.get("interval") if isinstance(row.get("interval"), dict) else {}
            start = safe_float(interval.get("start"))
            end = safe_float(interval.get("end"))
            seconds = safe_float(interval.get("duration_sec")) or 0.0
            if start is None or end is None or end <= start:
                start = 0.0
                end = max(0.0, seconds)
            if not active_audio_review_row(row, selected_me_ids):
                resolved_count += 1
                resolved_intervals.append((start, end))
                continue
            if source_id in review_resolved_ids:
                resolved_by_review_count += 1
                resolved_by_review_intervals.append((start, end))
                continue
            active_count += 1
            active_intervals.append((start, end))
            classification = row.get("classification") if isinstance(row.get("classification"), dict) else {}
            label = str(classification.get("label") or "")
            verdict = str(classification.get("verdict") or "")
            if verdict == "probable_transcript_error":
                verdict = "probable_error"
                if label == "remote_leak":
                    remote_leak_intervals.append((start, end))
            if verdict in buckets:
                buckets[verdict]["count"] += 1
                buckets[verdict]["intervals"].append((start, end))

        raw_error = audio_summary.get("probable_error") if isinstance(audio_summary.get("probable_error"), dict) else {}
        raw_stronger = (
            audio_summary.get("needs_stronger_audio_judge")
            if isinstance(audio_summary.get("needs_stronger_audio_judge"), dict)
            else {}
        )
        return {
            "audio_review_items": active_count,
            "audio_review_seconds": union_seconds(active_intervals),
            "audio_review_reliable_count": buckets["likely_reliable"]["count"],
            "audio_review_reliable_seconds": union_seconds(buckets["likely_reliable"]["intervals"]),
            "audio_review_probable_error_count": buckets["probable_error"]["count"],
            "audio_review_probable_error_seconds": union_seconds(buckets["probable_error"]["intervals"]),
            "audio_review_stronger_judge_count": buckets["needs_stronger_audio_judge"]["count"],
            "audio_review_stronger_judge_seconds": union_seconds(buckets["needs_stronger_audio_judge"]["intervals"]),
            "audio_review_resolved_by_cleanup_count": resolved_count,
            "audio_review_resolved_by_cleanup_seconds": union_seconds(resolved_intervals),
            "audio_review_resolved_by_review_count": resolved_by_review_count,
            "audio_review_resolved_by_review_seconds": union_seconds(resolved_by_review_intervals),
            "audio_review_remote_leak_probable_error_count": len(remote_leak_intervals),
            "audio_review_remote_leak_probable_error_seconds": union_seconds(remote_leak_intervals),
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
    by_label = audio_summary.get("by_label") if isinstance(audio_summary.get("by_label"), dict) else {}
    remote_leak = by_label.get("remote_leak") if isinstance(by_label.get("remote_leak"), dict) else {}
    return {
        "audio_review_items": safe_int(audio_summary.get("items")),
        "audio_review_reliable_count": safe_int(reliable.get("count")),
        "audio_review_reliable_seconds": round_or_none(reliable.get("seconds")),
        "audio_review_probable_error_count": safe_int(error.get("count")),
        "audio_review_probable_error_seconds": round_or_none(error.get("seconds")),
        "audio_review_stronger_judge_count": safe_int(stronger.get("count")),
        "audio_review_stronger_judge_seconds": round_or_none(stronger.get("seconds")),
        "audio_review_remote_leak_probable_error_count": safe_int(remote_leak.get("count")),
        "audio_review_remote_leak_probable_error_seconds": round_or_none(remote_leak.get("seconds")),
        "audio_review_recommended_next_step": audio_summary.get("recommended_next_step"),
    }


def remote_leak_segment_plan_metrics(plan: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(plan, dict):
        return {
            "remote_leak_segment_plan_status": "missing",
            "remote_leak_segment_plan_items": None,
            "remote_leak_segment_plan_seconds": None,
            "remote_leak_segment_plan_protect_local_content_items": None,
            "remote_leak_segment_plan_protect_local_content_seconds": None,
            "remote_leak_segment_plan_next_work": None,
        }
    summary = plan.get("summary") if isinstance(plan.get("summary"), dict) else {}
    action_plan = plan.get("action_plan") if isinstance(plan.get("action_plan"), list) else []
    first_action = action_plan[0] if action_plan and isinstance(action_plan[0], dict) else {}
    return {
        "remote_leak_segment_plan_status": "ok",
        "remote_leak_segment_plan_items": safe_int(summary.get("items")),
        "remote_leak_segment_plan_seconds": round_or_none(summary.get("seconds")),
        "remote_leak_segment_plan_protect_local_content_items": safe_int(summary.get("protect_local_content_items")),
        "remote_leak_segment_plan_protect_local_content_seconds": round_or_none(summary.get("protect_local_content_seconds")),
        "remote_leak_segment_plan_next_work": first_action.get("next_work"),
    }


def local_recall_metrics(local_recall: dict[str, Any] | None, review_report: dict[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(local_recall, dict):
        return {
            "local_recall_audit_status": "missing",
            "local_recall_missing_island_count": None,
            "local_recall_possible_lost_me_count": None,
            "local_recall_possible_lost_me_seconds": None,
            "local_recall_needs_review_count": None,
            "local_recall_needs_review_seconds": None,
            "local_recall_meaningful_review_seconds": None,
            "local_recall_blocking_low_local_recall": None,
            "local_recall_recommended_next_step": "run_local_recall_audit",
        }
    summary = local_recall.get("summary") if isinstance(local_recall.get("summary"), dict) else {}
    review_summary = review_report.get("summary") if isinstance(review_report, dict) and isinstance(review_report.get("summary"), dict) else {}
    review_gates = review_report.get("gates") if isinstance(review_report, dict) and isinstance(review_report.get("gates"), dict) else {}
    if review_gates.get("passed") is True and safe_int(review_summary.get("local_recall_decision_rows")):
        possible_lost_seconds = round_or_none(review_summary.get("local_recall_remaining_possible_lost_me_seconds"))
        needs_review_seconds = round_or_none(review_summary.get("local_recall_remaining_needs_review_seconds"))
        possible_lost_count = safe_int(review_summary.get("local_recall_remaining_possible_lost_me_count"))
        needs_review_count = safe_int(review_summary.get("local_recall_remaining_needs_review_count"))
        meaningful_seconds = round_or_none(review_summary.get("local_recall_remaining_review_seconds"))
        blocking = bool((meaningful_seconds or 0.0) > 0.0)
        return {
            "local_recall_audit_status": local_recall.get("status"),
            "local_recall_missing_island_count": safe_int(summary.get("audited_missing_island_count")),
            "local_recall_possible_lost_me_count": possible_lost_count,
            "local_recall_possible_lost_me_seconds": possible_lost_seconds,
            "local_recall_needs_review_count": needs_review_count,
            "local_recall_needs_review_seconds": needs_review_seconds,
            "local_recall_meaningful_review_seconds": meaningful_seconds,
            "local_recall_blocking_low_local_recall": blocking,
            "local_recall_recommended_next_step": "local_recall_reviewed_clear" if not blocking else "review_local_recall_items",
        }
    return {
        "local_recall_audit_status": local_recall.get("status"),
        "local_recall_missing_island_count": safe_int(summary.get("audited_missing_island_count")),
        "local_recall_possible_lost_me_count": safe_int(summary.get("possible_lost_me_count")),
        "local_recall_possible_lost_me_seconds": round_or_none(summary.get("possible_lost_me_seconds")),
        "local_recall_needs_review_count": safe_int(summary.get("needs_review_count")),
        "local_recall_needs_review_seconds": round_or_none(summary.get("needs_review_seconds")),
        "local_recall_meaningful_review_seconds": round_or_none(summary.get("meaningful_review_seconds")),
        "local_recall_blocking_low_local_recall": summary.get("blocking_low_local_recall"),
        "local_recall_recommended_next_step": summary.get("recommended_next_step"),
    }


def transcript_order_metrics(
    order_audit: dict[str, Any] | None,
    review_report: dict[str, Any] | None = None,
    order_repair_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    order_repair_summary = (
        order_repair_report.get("summary")
        if isinstance(order_repair_report, dict) and isinstance(order_repair_report.get("summary"), dict)
        else {}
    )
    order_repair_gates = (
        order_repair_report.get("gates")
        if isinstance(order_repair_report, dict) and isinstance(order_repair_report.get("gates"), dict)
        else {}
    )
    if not isinstance(order_audit, dict):
        if order_repair_gates.get("passed") is True:
            unrepaired = safe_int(order_repair_summary.get("unrepaired_order_risks")) or 0
            marked = safe_int(order_repair_summary.get("marked_needs_review")) or 0
            risk_seconds = round_or_none(order_repair_summary.get("unrepaired_order_risk_seconds")) or 0.0
            blocking = bool(risk_seconds > 0.0)
            return {
                "transcript_order_audit_status": "missing",
                "transcript_order_audited_overlap_count": safe_int(order_repair_summary.get("order_risk_items")),
                "transcript_order_probable_order_risk_count": unrepaired,
                "transcript_order_probable_order_risk_seconds": risk_seconds,
                "transcript_order_needs_review_count": marked,
                "transcript_order_needs_review_seconds": 0.0,
                "transcript_order_review_seconds": risk_seconds,
                "transcript_order_blocking_order_risk": blocking,
                "transcript_order_recommended_next_step": "transcript_order_repaired_clear" if not blocking else "review_transcript_order_items",
                "transcript_order_repair_gates_passed": True,
                "transcript_order_repair_applied_repairs": safe_int(order_repair_summary.get("applied_repairs")),
                "transcript_order_repair_unrepaired_order_risks": unrepaired,
            }
        return {
            "transcript_order_audit_status": "missing",
            "transcript_order_audited_overlap_count": None,
            "transcript_order_probable_order_risk_count": None,
            "transcript_order_probable_order_risk_seconds": None,
            "transcript_order_needs_review_count": None,
            "transcript_order_needs_review_seconds": None,
            "transcript_order_review_seconds": None,
            "transcript_order_blocking_order_risk": None,
            "transcript_order_recommended_next_step": "run_transcript_order_audit",
            "transcript_order_repair_gates_passed": None,
            "transcript_order_repair_applied_repairs": None,
            "transcript_order_repair_unrepaired_order_risks": None,
        }
    summary = order_audit.get("summary") if isinstance(order_audit.get("summary"), dict) else {}
    if order_repair_gates.get("passed") is True:
        unrepaired = safe_int(order_repair_summary.get("unrepaired_order_risks")) or 0
        marked = safe_int(order_repair_summary.get("marked_needs_review")) or 0
        risk_seconds = round_or_none(order_repair_summary.get("unrepaired_order_risk_seconds")) or 0.0
        review_seconds = 0.0
        blocking = bool(risk_seconds > 0.0 or review_seconds >= 10.0)
        return {
            "transcript_order_audit_status": order_audit.get("status"),
            "transcript_order_audited_overlap_count": safe_int(summary.get("audited_overlap_count")),
            "transcript_order_probable_order_risk_count": unrepaired,
            "transcript_order_probable_order_risk_seconds": risk_seconds,
            "transcript_order_needs_review_count": marked,
            "transcript_order_needs_review_seconds": review_seconds,
            "transcript_order_review_seconds": round(risk_seconds + review_seconds, 3),
            "transcript_order_blocking_order_risk": blocking,
            "transcript_order_recommended_next_step": "transcript_order_repaired_clear" if not blocking else "review_transcript_order_items",
            "transcript_order_repair_gates_passed": True,
            "transcript_order_repair_applied_repairs": safe_int(order_repair_summary.get("applied_repairs")),
            "transcript_order_repair_unrepaired_order_risks": unrepaired,
        }
    review_summary = review_report.get("summary") if isinstance(review_report, dict) and isinstance(review_report.get("summary"), dict) else {}
    review_gates = review_report.get("gates") if isinstance(review_report, dict) and isinstance(review_report.get("gates"), dict) else {}
    if review_gates.get("passed") is True and safe_int(review_summary.get("transcript_order_decision_rows")):
        risk_count = safe_int(review_summary.get("transcript_order_remaining_probable_order_risk_count"))
        risk_seconds = round_or_none(review_summary.get("transcript_order_remaining_probable_order_risk_seconds")) or 0.0
        review_seconds = round_or_none(review_summary.get("transcript_order_remaining_review_seconds")) or 0.0
        blocking = bool(risk_seconds > 0.0 or review_seconds >= 10.0)
        return {
            "transcript_order_audit_status": order_audit.get("status"),
            "transcript_order_audited_overlap_count": safe_int(summary.get("audited_overlap_count")),
            "transcript_order_probable_order_risk_count": risk_count,
            "transcript_order_probable_order_risk_seconds": risk_seconds,
            "transcript_order_needs_review_count": safe_int(review_summary.get("transcript_order_needs_review_decisions")),
            "transcript_order_needs_review_seconds": review_seconds,
            "transcript_order_review_seconds": round(risk_seconds + review_seconds, 3),
            "transcript_order_blocking_order_risk": blocking,
            "transcript_order_recommended_next_step": "transcript_order_reviewed_clear" if not blocking else "review_transcript_order_items",
            "transcript_order_repair_gates_passed": order_repair_gates.get("passed") if order_repair_gates else None,
            "transcript_order_repair_applied_repairs": safe_int(order_repair_summary.get("applied_repairs")),
            "transcript_order_repair_unrepaired_order_risks": safe_int(order_repair_summary.get("unrepaired_order_risks")),
        }
    risk_seconds = round_or_none(summary.get("probable_order_risk_seconds")) or 0.0
    needs_review_seconds = round_or_none(summary.get("needs_review_seconds")) or 0.0
    review_seconds = round(risk_seconds + needs_review_seconds, 3)
    return {
        "transcript_order_audit_status": order_audit.get("status"),
        "transcript_order_audited_overlap_count": safe_int(summary.get("audited_overlap_count")),
        "transcript_order_probable_order_risk_count": safe_int(summary.get("probable_order_risk_count")),
        "transcript_order_probable_order_risk_seconds": round_or_none(summary.get("probable_order_risk_seconds")),
        "transcript_order_needs_review_count": safe_int(summary.get("needs_review_count")),
        "transcript_order_needs_review_seconds": round_or_none(summary.get("needs_review_seconds")),
        "transcript_order_review_seconds": review_seconds,
        "transcript_order_blocking_order_risk": summary.get("blocking_order_risk"),
        "transcript_order_recommended_next_step": summary.get("recommended_next_step"),
        "transcript_order_repair_gates_passed": order_repair_gates.get("passed") if order_repair_gates else None,
        "transcript_order_repair_applied_repairs": safe_int(order_repair_summary.get("applied_repairs")),
        "transcript_order_repair_unrepaired_order_risks": safe_int(order_repair_summary.get("unrepaired_order_risks")),
    }


def risk_flags(row: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    verdict = row.get("verdict")
    if verdict in {"risky", "failed"}:
        flags.append(f"verdict:{verdict}")
    if row.get("selected_profile") not in CLEANUP_PROFILES:
        flags.append("no_audit_cleanup_profile")
    if row.get("selected_profile") == "reviewed_v1" and row.get("review_decisions_gates_passed") is not True:
        flags.append("review_decisions_gates_failed")
    if row.get("selected_profile") == "agent_reviewed_v1" and row.get("review_decisions_gates_passed") is not True:
        flags.append("agent_review_decisions_gates_failed")
    missing = row.get("missing_artifacts") or []
    if missing:
        flags.append("missing:" + ",".join(missing[:3]))
    if safe_int(row.get("unrepaired_long_mic_crossings_count")):
        flags.append("unrepaired_long_mic_crossings")
    if safe_int(row.get("golden_phrase_fail_count")):
        flags.append("golden_phrase_fail")
    if row.get("local_recall_blocking_low_local_recall") is True:
        flags.append("local_recall_possible_lost_me")
    if row.get("transcript_order_blocking_order_risk") is True:
        flags.append("transcript_order_risk")
    recall = safe_float(row.get("local_only_island_recall"))
    if recall is not None and recall < 0.9:
        if row.get("local_recall_audit_status") != "ok":
            flags.append("low_local_recall")
        elif row.get("local_recall_blocking_low_local_recall") is True and "local_recall_possible_lost_me" not in flags:
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
    remote_leak_protect = safe_int(row.get("remote_leak_segment_plan_protect_local_content_items")) or 0
    if remote_leak_protect:
        flags.append("remote_leak_segment_repair_candidates")
    audio_judge_count = safe_int(row.get("audio_review_stronger_judge_count")) or 0
    audio_judge_seconds = safe_float(row.get("audio_review_stronger_judge_seconds")) or 0.0
    if audio_judge_count >= 20 or audio_judge_seconds >= 60.0:
        flags.append("needs_stronger_audio_judge")
    return flags


def use_gate_reasons(row: dict[str, Any]) -> list[dict[str, Any]]:
    reasons: list[dict[str, Any]] = []
    pipeline_status = row.get("pipeline_status")
    if pipeline_status != "complete":
        reasons.append(
            {
                "id": "pipeline_incomplete",
                "severity": "block",
                "message": "The post-recording pipeline is incomplete.",
                "value": pipeline_status,
            }
        )
    verdict = row.get("verdict")
    if verdict in {"failed", "risky"}:
        reasons.append(
            {
                "id": f"quality_verdict_{verdict}",
                "severity": "block",
                "message": "The quality verdict is not safe for unattended use.",
                "value": verdict,
            }
        )
    profile = row.get("selected_profile")
    if profile not in CLEANUP_PROFILES:
        reasons.append(
            {
                "id": "missing_cleanup_profile",
                "severity": "review",
                "message": "No reviewed or audit-cleaned transcript profile is selected.",
                "value": profile,
            }
        )

    duration = safe_float(row.get("meeting_duration_sec")) or 0.0
    burden = safe_float(row.get("review_burden_sec")) or 0.0
    ratio = burden / duration if duration > 0 else 0.0
    if ratio > 0.08:
        reasons.append(
            {
                "id": "review_burden_too_high",
                "severity": "block",
                "message": "Too much of the meeting remains in review-required regions.",
                "value": round(ratio, 6),
                "threshold": 0.08,
            }
        )
    elif ratio > 0.025:
        reasons.append(
            {
                "id": "review_burden_requires_review",
                "severity": "review",
                "message": "Review burden is above the ready-for-notes threshold.",
                "value": round(ratio, 6),
                "threshold": 0.025,
            }
        )

    for flag in row.get("risk_flags") or []:
        severity = "block" if flag.startswith("verdict:") else "review"
        if flag in {"unrepaired_long_mic_crossings", "golden_phrase_fail", "high_needs_review_ratio", "transcript_order_risk"}:
            severity = "block"
        reasons.append(
            {
                "id": f"risk:{flag}",
                "severity": severity,
                "message": "Session quality report raised a risk flag.",
                "value": flag,
            }
        )
    return reasons


def session_use_gate(row: dict[str, Any]) -> str:
    if row.get("pipeline_status") != "complete":
        return "pipeline_incomplete"
    if row.get("verdict") in {"failed", "risky"}:
        return "do_not_use_without_manual_review"
    if row.get("selected_profile") not in CLEANUP_PROFILES:
        return "pipeline_incomplete_review_first"

    duration = safe_float(row.get("meeting_duration_sec")) or 0.0
    burden = safe_float(row.get("review_burden_sec")) or 0.0
    ratio = burden / duration if duration > 0 else 0.0
    flags = row.get("risk_flags") or []
    if ratio <= 0.025 and not flags:
        return "ready_for_notes"
    if ratio <= 0.08:
        return "review_first"
    return "do_not_use_without_manual_review"


def add_use_gate(row: dict[str, Any]) -> None:
    duration = safe_float(row.get("meeting_duration_sec")) or 0.0
    probable_error = safe_float(row.get("audio_review_probable_error_seconds")) or 0.0
    stronger_judge = safe_float(row.get("audio_review_stronger_judge_seconds")) or 0.0
    local_recall_review = safe_float(row.get("local_recall_meaningful_review_seconds")) or 0.0
    transcript_order_review = safe_float(row.get("transcript_order_review_seconds")) or 0.0
    burden = probable_error + stronger_judge + local_recall_review + transcript_order_review
    row["review_burden_sec"] = round(burden, 3)
    row["review_burden_ratio"] = round(burden / duration, 6) if duration > 0 else 0.0
    row["use_gate"] = session_use_gate(row)
    reasons = use_gate_reasons(row)
    row["use_gate_reasons"] = reasons
    row["review_blockers"] = [reason["id"] for reason in reasons if reason.get("severity") in {"review", "block"}]
    row["export_blockers"] = (
        []
        if row["use_gate"] == "ready_for_notes"
        else [reason["id"] for reason in reasons if reason.get("severity") in {"review", "block"}] or [row["use_gate"]]
    )
    row["readiness_warnings"] = [reason["id"] for reason in reasons if reason.get("severity") == "warning"]


def collect_session(session: Path, labels: dict[str, str]) -> dict[str, Any]:
    status = stage_status(session)
    profile = selected_profile(session)
    quality = read_quality(session, profile)
    verdict = read_verdict(session, profile)
    evidence = read_evidence(session, profile)
    group_summary = read_json(session / "derived/audit/group-overlaps/group_overlap_summary.json")
    audio_summary = read_json(session / "derived/audit/audio-review-pack/audio_review_summary.json")
    remote_leak_plan = read_json(
        session / "derived/transcript-simple/whisper-cpp/remote-leak-repair/remote_leak_segment_repair_plan.json"
    )
    local_recall = read_json(session / "derived/audit/local-recall/local_recall_audit.json")
    order_audit = read_json(session / "derived/audit/order/transcript_order_audit.json")
    local_fir = read_json(session / "derived/preprocess/echo/local_fir_report.json")
    suggested_report = read_json(
        session / "derived/transcript-simple/whisper-cpp/review-decisions/review_decisions_report.suggested_review_v1.json"
    )
    cleanup_report = (
        read_json(session / "derived/transcript-simple/whisper-cpp/audit-cleanup" / f"audit_cleanup_report{suffix(profile)}.json")
        if profile in {"audit_cleanup_v1", "audit_cleanup_v2", "audit_cleanup_v3", "audit_cleanup_v4", "audit_cleanup_v5", "audit_cleanup_v6"}
        else None
    )
    review_report = (
        read_json(session / "derived/transcript-simple/whisper-cpp/review-decisions" / f"review_decisions_report{suffix(profile)}.json")
        if profile in {"reviewed_v1", "agent_reviewed_v1"}
        else None
    )
    order_repair_report = (
        read_json(session / "derived/transcript-simple/whisper-cpp/order-repair/transcript_order_repair_report.order_repair_v1.json")
        if profile == "order_repair_v1"
        else None
    )
    session_json = read_json(session / "session.json") or {}

    metrics = verdict.get("metrics") if isinstance(verdict, dict) else None
    if not isinstance(metrics, dict):
        metrics = {}
    suggested_summary = suggested_report.get("summary") if isinstance(suggested_report, dict) else {}
    suggested_gates = suggested_report.get("gates") if isinstance(suggested_report, dict) else {}
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
        "suggested_review_v1_available": status.get("suggested_review_v1"),
        "suggested_review_v1_gates_passed": suggested_gates.get("passed") if isinstance(suggested_gates, dict) else None,
        "suggested_review_v1_applied_decision_rows": safe_int(
            suggested_summary.get("applied_decision_rows") if isinstance(suggested_summary, dict) else None
        ),
        "suggested_review_v1_dropped_me_utterances": safe_int(
            suggested_summary.get("dropped_me_utterances") if isinstance(suggested_summary, dict) else None
        ),
        "suggested_review_v1_dropped_me_seconds": round_or_none(
            suggested_summary.get("dropped_me_seconds") if isinstance(suggested_summary, dict) else None
        ),
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
    row.update(synthesis_review_metrics(verdict))
    row.update(audio_review_metrics(audio_summary, session, profile))
    row.update(remote_leak_segment_plan_metrics(remote_leak_plan))
    row.update(local_recall_metrics(local_recall, review_report))
    row.update(transcript_order_metrics(order_audit, review_report, order_repair_report))
    row["risk_flags"] = risk_flags(row)
    add_use_gate(row)
    return row


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    verdicts = Counter(str(row.get("verdict") or "missing") for row in rows)
    profiles = Counter(str(row.get("selected_profile") or "missing") for row in rows)
    durations = [safe_float(row.get("meeting_duration_sec")) for row in rows]
    durations = [value for value in durations if value is not None]
    risk_rows = [row for row in rows if row.get("risk_flags")]
    complete = sum(1 for row in rows if row.get("pipeline_status") == "complete")
    synthesis_review_seconds = sum(safe_float(row.get("synthesis_review_item_seconds")) or 0.0 for row in rows)
    return {
        "session_count": len(rows),
        "complete_pipeline_count": complete,
        "partial_or_incomplete_count": len(rows) - complete,
        "total_duration_sec": round(sum(durations), 3),
        "total_duration_min": round(sum(durations) / 60.0, 2),
        "median_duration_min": round(statistics.median(durations) / 60.0, 2) if durations else None,
        "by_verdict": dict(sorted(verdicts.items())),
        "by_selected_profile": dict(sorted(profiles.items())),
        "sessions_with_suggested_review_v1": sum(1 for row in rows if row.get("suggested_review_v1_available")),
        "total_synthesis_review_items": sum(safe_int(row.get("synthesis_review_item_count")) or 0 for row in rows),
        "total_synthesis_review_seconds": round(synthesis_review_seconds, 3),
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
        "use_gate",
        "verdict",
        "meeting_duration_sec",
        "review_burden_sec",
        "review_burden_ratio",
        "synthesis_review_item_count",
        "synthesis_review_item_seconds",
        "synthesis_review_top_types",
        "utterances",
        "needs_review_count",
        "needs_review_ratio",
        "local_only_island_recall",
        "local_recall_recommended_next_step",
        "local_recall_missing_island_count",
        "local_recall_possible_lost_me_seconds",
        "local_recall_needs_review_seconds",
        "transcript_order_recommended_next_step",
        "transcript_order_probable_order_risk_count",
        "transcript_order_probable_order_risk_seconds",
        "transcript_order_needs_review_seconds",
        "transcript_order_review_seconds",
        "transcript_order_repair_gates_passed",
        "transcript_order_repair_applied_repairs",
        "transcript_order_repair_unrepaired_order_risks",
        "cross_role_overlap_gt2_seconds",
        "remote_duplicate_in_me_seconds",
        "audit_harmful_seconds_after",
        "audit_review_seconds",
        "applied_patches",
        "dropped_me_duplicate_seconds",
        "dropped_me_noise_seconds",
        "review_scope_complete",
        "review_scope_required_rows",
        "review_scope_closed_rows",
        "review_scope_coverage_ratio",
        "review_decisions_applied",
        "review_decisions_dropped_me",
        "review_decisions_dropped_me_seconds",
        "suggested_review_v1_available",
        "suggested_review_v1_gates_passed",
        "suggested_review_v1_applied_decision_rows",
        "suggested_review_v1_dropped_me_utterances",
        "suggested_review_v1_dropped_me_seconds",
        "echo_reduction_db",
        "selected_notes",
        "hidden_meeting_facilitation_count",
        "audio_review_items",
        "audio_review_probable_error_count",
        "audio_review_stronger_judge_count",
        "audio_review_remote_leak_probable_error_count",
        "remote_leak_segment_plan_items",
        "remote_leak_segment_plan_protect_local_content_items",
        "remote_leak_segment_plan_next_work",
        "risk_flags",
        "review_blockers",
        "export_blockers",
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


def fmt_review_top_types(row: dict[str, Any]) -> str:
    items = row.get("synthesis_review_top_types") if isinstance(row.get("synthesis_review_top_types"), list) else []
    rendered = []
    for item in items[:3]:
        if isinstance(item, dict):
            rendered.append(f"{item.get('type')}={item.get('count')}")
    return ", ".join(rendered) if rendered else "0"


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
        f"- Suggested review shadow profiles: `{payload['summary'].get('sessions_with_suggested_review_v1', 0)}`",
        f"- Synthesis review items: `{payload['summary'].get('total_synthesis_review_items', 0)}` / `{payload['summary'].get('total_synthesis_review_seconds', 0.0)}` sec",
        f"- Sessions with risk flags: `{payload['summary']['sessions_with_risk_flags']}`",
        "",
        "## Sessions",
        "",
        "| Session | Label | Gate | Status | Profile | Verdict | Min | Review % | Synthesis Review | Utterances | Needs Review | Local Recall | Local Audit | Order Audit | Harmful s | Review s | Audio Review | Actions/Decisions | Flags | Missing |",
        "|---|---|---|---|---|---:|---:|---:|---|---:|---:|---:|---|---|---:|---:|---:|---:|---|---|",
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
        local_audit = (
            f"{fmt(row.get('local_recall_recommended_next_step'))}; "
            f"lost/review {fmt(row.get('local_recall_possible_lost_me_seconds'), '0')}/"
            f"{fmt(row.get('local_recall_needs_review_seconds'), '0')}s"
        )
        order_audit = (
            f"{fmt(row.get('transcript_order_recommended_next_step'))}; "
            f"risk/review {fmt(row.get('transcript_order_probable_order_risk_seconds'), '0')}/"
            f"{fmt(row.get('transcript_order_needs_review_seconds'), '0')}s"
        )
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{row['session_id']}`",
                    row["label"].replace("|", "\\|"),
                    row.get("use_gate") or "unknown",
                    row["pipeline_status"],
                    row["selected_profile"],
                    fmt(row.get("verdict")),
                    fmt(round(duration / 60.0, 1) if duration is not None else None),
                    fmt((safe_float(row.get("review_burden_ratio")) or 0.0) * 100.0),
                    f"{fmt(row.get('synthesis_review_item_count'), '0')} / {fmt(row.get('synthesis_review_item_seconds'), '0')}s; {fmt_review_top_types(row)}",
                    fmt(row.get("utterances")),
                    fmt(row.get("needs_review_count")),
                    fmt(row.get("local_only_island_recall")),
                    local_audit,
                    order_audit,
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
    missing_order_audit = [row for row in rows if "transcript_order_audit" in (row.get("missing_artifacts") or [])]
    if missing_order_audit:
        lines.append("- Run transcript order audit for:")
        for row in missing_order_audit:
            lines.append(f"  - `{row['session_id']}`: {row['label']}")
    risky = [row for row in rows if row.get("risk_flags")]
    if risky:
        lines.append("- Review sessions with risk flags before using notes as working memory:")
        for row in risky[:10]:
            lines.append(f"  - `{row['session_id']}`: {', '.join(row['risk_flags'])}")
    if not missing_cleanup and not missing_audio_review and not missing_order_audit and not risky:
        lines.append("- No immediate structural gaps detected.")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def readiness_outputs(session: Path, profile: str) -> dict[str, Any]:
    resolved = session / "derived/transcript-simple/whisper-cpp/resolved"
    synthesis = session / "derived/synthesis-simple/extractive"
    outputs = {
        "transcript": resolved / f"transcript{suffix(profile)}.md",
        "clean_dialogue": resolved / f"clean_dialogue{suffix(profile)}.json",
        "quality_report": resolved / f"quality_report{suffix(profile)}.json",
        "notes": synthesis / f"notes{suffix(profile)}.md",
        "quality_verdict": synthesis / f"quality_verdict{suffix(profile)}.md",
        "evidence_notes": synthesis / f"evidence_notes{suffix(profile)}.json",
        "review_items": synthesis / f"review_items{suffix(profile)}.jsonl",
        "audio_review_report": session / "derived/audit/audio-review-pack/audio_review_report.md",
        "remote_leak_segment_report": session
        / "derived/transcript-simple/whisper-cpp/remote-leak-repair/remote_leak_segment_repair.md",
        "remote_leak_segment_plan": session
        / "derived/transcript-simple/whisper-cpp/remote-leak-repair/remote_leak_segment_repair_plan.json",
        "local_recall_review": session / "derived/audit/local-recall/local_recall_review.md",
        "transcript_order_review": session / "derived/audit/order/transcript_order_review.md",
        "pipeline_run_report": session / "derived/pipeline-run/pipeline_run_report.json",
    }
    if profile == "current":
        outputs["transcript"] = resolved / "transcript.md"
        outputs["notes"] = synthesis / "notes.md"
        outputs["quality_verdict"] = synthesis / "quality_verdict.md"
        outputs["evidence_notes"] = synthesis / "evidence_notes.json"
        outputs["review_items"] = synthesis / "review_items.jsonl"
    return {
        key: {"path": rel(path, session), "exists": path.exists()}
        for key, path in outputs.items()
    }


def readiness_recommendation(gate: str) -> str:
    if gate == "ready_for_notes":
        return "use_notes_with_normal_caution"
    if gate == "review_first":
        return "review_flagged_audio_before_using_for_medium_risk_work"
    if gate == "pipeline_incomplete":
        return "rerun_full_session_pipeline"
    if gate == "pipeline_incomplete_review_first":
        return "run_audit_cleanup_and_synthesis_before_use"
    return "do_not_use_without_manual_review"


def command_path(path: Path) -> str:
    if not path.is_absolute():
        return shlex.quote(str(path))
    try:
        display = path.resolve().relative_to(Path.cwd().resolve())
    except ValueError:
        display = path
    return shlex.quote(str(display))


def readiness_next_commands(session: Path, row: dict[str, Any]) -> list[dict[str, str]]:
    session_arg = command_path(session)
    gate = str(row.get("use_gate") or "pipeline_incomplete")
    export_blockers = row.get("export_blockers") or []
    review_blockers = row.get("review_blockers") or []

    if gate.startswith("pipeline_incomplete") or "pipeline_incomplete" in export_blockers:
        return [
            {
                "id": "process_session",
                "label": "Run or refresh the full post-recording pipeline.",
                "command": f"murmurmark process {session_arg}",
            }
        ]

    if review_blockers or export_blockers or gate == "review_first":
        commands: list[dict[str, str]] = []
        if "transcript_order_risk" in (row.get("risk_flags") or []):
            commands.append(
                {
                    "id": "inspect_transcript_order",
                    "label": "Inspect chronology-risk regions before relying on reply order.",
                    "command": f"less {command_path(session / 'derived/audit/order/transcript_order_review.md')}",
                }
            )
        if "remote_leak_segment_repair_candidates" in (row.get("risk_flags") or []):
            commands.append(
                {
                    "id": "inspect_remote_leak_segment_plan",
                    "label": "Inspect remote-leak intervals where Me may still contain local content.",
                    "command": f"less {command_path(session / 'derived/transcript-simple/whisper-cpp/remote-leak-repair/remote_leak_segment_repair.md')}",
                }
            )
        elif safe_int(row.get("audio_review_remote_leak_probable_error_count")):
            commands.append(
                {
                    "id": "plan_remote_leak_segment_repair",
                    "label": "Build an audit-only plan for remote-leak regions before deciding how to repair them.",
                    "command": f"murmurmark repair remote-leak {session_arg}",
                }
            )
        commands.extend(
            [
                {
                    "id": "review_plan",
                    "label": "Build the review queue for flagged regions.",
                    "command": "murmurmark review plan",
                },
                {
                    "id": "review_first_lane",
                    "label": "Build the recommended first review lane pack.",
                    "command": f"murmurmark review first-lane --session {session_arg}",
                },
                {
                    "id": "review_lane_apply_first",
                    "label": "Apply the edited first lane answer sheet.",
                    "command": f"murmurmark review lane apply first --session {session_arg}",
                },
                {
                    "id": "review_workspace",
                    "label": "Build lane packs and answer sheets for this session.",
                    "command": f"murmurmark review workspace --session {session_arg}",
                },
                {
                    "id": "review_workspace_apply",
                    "label": "Apply edited review workspace answers.",
                    "command": f"murmurmark review workspace apply --session {session_arg}",
                },
                {
                    "id": "review_progress",
                    "label": "Check whether enough review decisions are closed for batch apply.",
                    "command": f"murmurmark review progress --session {session_arg}",
                },
                {
                    "id": "review_apply",
                    "label": "Apply closed review decisions and refresh reports when progress is ready.",
                    "command": f"murmurmark review apply --session {session_arg}",
                },
            ]
        )
        return commands

    if gate == "ready_for_notes":
        return [
            {
                "id": "export_markdown",
                "label": "Export a local Markdown handoff bundle.",
                "command": f"murmurmark export {session_arg} --format markdown --include-json",
            },
            {
                "id": "retention_plan",
                "label": "Inspect local retention/privacy actions.",
                "command": f"murmurmark retention plan {session_arg}",
            },
        ]

    return [
        {
            "id": "open_readiness",
            "label": "Inspect readiness details before using this session.",
            "command": f"less {command_path(session / 'derived/readiness/session_readiness.md')}",
        }
    ]


def write_session_readiness(session: Path, row: dict[str, Any]) -> None:
    out_dir = session / "derived/readiness"
    out_dir.mkdir(parents=True, exist_ok=True)
    profile = str(row.get("selected_profile") or "missing")
    gate = str(row.get("use_gate") or "pipeline_incomplete")
    payload = {
        "schema": READINESS_SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": {"name": "report-session-quality", "version": SCRIPT_VERSION},
        "session": str(session),
        "session_id": row.get("session_id"),
        "label": row.get("label"),
        "use_gate": gate,
        "recommendation": readiness_recommendation(gate),
        "selected_profile": profile,
        "verdict": row.get("verdict"),
        "pipeline_status": row.get("pipeline_status"),
        "risk_flags": row.get("risk_flags") or [],
        "use_gate_reasons": row.get("use_gate_reasons") or [],
        "review_blockers": row.get("review_blockers") or [],
        "export_blockers": row.get("export_blockers") or [],
        "warnings": row.get("readiness_warnings") or [],
        "next_commands": readiness_next_commands(session, row),
        "metrics": {
            "meeting_duration_sec": row.get("meeting_duration_sec"),
            "review_burden_sec": row.get("review_burden_sec"),
            "review_burden_ratio": row.get("review_burden_ratio"),
            "synthesis_review_item_count": row.get("synthesis_review_item_count"),
            "synthesis_review_item_seconds": row.get("synthesis_review_item_seconds"),
            "synthesis_review_top_types": row.get("synthesis_review_top_types"),
            "audio_review_probable_error_count": row.get("audio_review_probable_error_count"),
            "audio_review_probable_error_seconds": row.get("audio_review_probable_error_seconds"),
            "audio_review_stronger_judge_count": row.get("audio_review_stronger_judge_count"),
            "audio_review_stronger_judge_seconds": row.get("audio_review_stronger_judge_seconds"),
            "audio_review_remote_leak_probable_error_count": row.get("audio_review_remote_leak_probable_error_count"),
            "audio_review_remote_leak_probable_error_seconds": row.get("audio_review_remote_leak_probable_error_seconds"),
            "remote_leak_segment_plan_items": row.get("remote_leak_segment_plan_items"),
            "remote_leak_segment_plan_seconds": row.get("remote_leak_segment_plan_seconds"),
            "remote_leak_segment_plan_protect_local_content_items": row.get("remote_leak_segment_plan_protect_local_content_items"),
            "remote_leak_segment_plan_protect_local_content_seconds": row.get("remote_leak_segment_plan_protect_local_content_seconds"),
            "remote_leak_segment_plan_next_work": row.get("remote_leak_segment_plan_next_work"),
            "needs_review_count": row.get("needs_review_count"),
            "needs_review_ratio": row.get("needs_review_ratio"),
            "audit_harmful_seconds_after": row.get("audit_harmful_seconds_after"),
            "audit_review_seconds": row.get("audit_review_seconds"),
            "local_only_island_recall": row.get("local_only_island_recall"),
            "local_recall_missing_island_count": row.get("local_recall_missing_island_count"),
            "local_recall_possible_lost_me_seconds": row.get("local_recall_possible_lost_me_seconds"),
            "local_recall_needs_review_seconds": row.get("local_recall_needs_review_seconds"),
            "local_recall_recommended_next_step": row.get("local_recall_recommended_next_step"),
        },
        "outputs": readiness_outputs(session, profile),
    }
    write_json_path = out_dir / "session_readiness.json"
    write_json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    review_min = (safe_float(row.get("review_burden_sec")) or 0.0) / 60.0
    review_pct = (safe_float(row.get("review_burden_ratio")) or 0.0) * 100.0
    lines = [
        "# MurmurMark Session Readiness",
        "",
        f"Gate: `{gate}`",
        f"Recommendation: `{payload['recommendation']}`",
        f"Selected profile: `{profile}`",
        f"Verdict: `{row.get('verdict')}`",
        f"Review burden: `{review_min:.2f} min` / `{review_pct:.2f}%`",
        "",
        "## Open First",
        "",
    ]
    for key in ("quality_verdict", "notes", "transcript", "audio_review_report", "remote_leak_segment_report", "local_recall_review"):
        item = payload["outputs"].get(key) or {}
        if item.get("exists"):
            lines.append(f"- `{key}`: `{item['path']}`")
    lines.extend(["", "## Risk Flags", ""])
    flags = row.get("risk_flags") or []
    if flags:
        lines.extend(f"- `{flag}`" for flag in flags)
    else:
        lines.append("- none")
    lines.extend(["", "## Review Blockers", ""])
    review_blockers = row.get("review_blockers") or []
    if review_blockers:
        lines.extend(f"- `{item}`" for item in review_blockers)
    else:
        lines.append("- none")
    lines.extend(["", "## Export Blockers", ""])
    export_blockers = row.get("export_blockers") or []
    if export_blockers:
        lines.extend(f"- `{item}`" for item in export_blockers)
    else:
        lines.append("- none")
    if payload["use_gate_reasons"]:
        lines.extend(["", "## Gate Reasons", ""])
        for reason in payload["use_gate_reasons"]:
            lines.append(
                f"- `{reason.get('id')}` / `{reason.get('severity')}`: {reason.get('message')} "
                f"(value `{fmt(reason.get('value'))}`)"
            )
    lines.extend(["", "## Next Commands", ""])
    next_commands = payload.get("next_commands") or []
    if next_commands:
        for item in next_commands:
            lines.append(f"- `{item['command']}` — {item['label']}")
    else:
        lines.append("- none")
    lines.extend(["", "## Metrics", ""])
    for key, value in payload["metrics"].items():
        lines.append(f"- `{key}`: `{fmt(value)}`")
    (out_dir / "session_readiness.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


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
    if args.write_session_readiness:
        for session, row in zip(args.sessions, rows):
            write_session_readiness(session, row)
    print(f"written: {write_json_path}")
    print(f"markdown: {out_dir / 'session_quality_report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
