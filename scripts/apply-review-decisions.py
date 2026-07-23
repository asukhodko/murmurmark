#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


SCRIPT_VERSION = "0.3.3"
OUTPUT_PROFILE_DEFAULT = "reviewed_v1"
VALID_DECISIONS = {"drop_me", "drop_remote", "keep_me", "needs_review", "skip", "todo", ""}
OPEN_DECISIONS = {"", "todo"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply explicit human review decisions to a transcript profile.")
    parser.add_argument("session", type=Path)
    parser.add_argument(
        "--decisions",
        type=Path,
        default=Path("sessions/_reports/review-plan/review_decisions.jsonl"),
        help="Edited JSONL decisions. Copy review_decisions.template.jsonl to this path and set decision fields.",
    )
    parser.add_argument("--input-profile", default="auto")
    parser.add_argument("--output-profile", default=OUTPUT_PROFILE_DEFAULT)
    parser.add_argument(
        "--review-template",
        type=Path,
        help="Review decision template used as the required coverage scope. Defaults to the template next to --decisions, then sessions/_reports/review-plan/review_decisions.template.jsonl.",
    )
    parser.add_argument(
        "--allow-partial-review",
        action="store_true",
        help="Allow gates to pass without proving that every template row for this session was decided.",
    )
    return parser.parse_args()


def suffix(profile: str) -> str:
    return "" if profile == "current" else f".{profile}"


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        value = json.load(file)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


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


def safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def role_name(row: dict[str, Any]) -> str:
    source = str(row.get("source_track") or "").lower()
    role = str(row.get("role") or row.get("speaker_label") or "").lower()
    if source == "mic" or role == "me":
        return "Me"
    if source == "remote" or role in {"remote", "colleagues"}:
        return "Colleagues"
    return str(row.get("role") or row.get("speaker_label") or "Unknown")


def is_me(row: dict[str, Any]) -> bool:
    return role_name(row) == "Me"


def format_time(seconds: Any) -> str:
    total = max(0, int(safe_float(seconds)))
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def normalize_text(value: Any) -> str:
    return " ".join(str(value or "").lower().replace("ё", "е").split())


def text_similarity(left: Any, right: Any) -> float:
    return SequenceMatcher(None, normalize_text(left), normalize_text(right)).ratio()


def overlap_duration(left: dict[str, Any], right: dict[str, Any]) -> float:
    start = max(safe_float(left.get("start")), safe_float(right.get("start")))
    end = min(safe_float(left.get("end")), safe_float(right.get("end")))
    return max(0.0, end - start)


def build_overlaps(utterances: list[dict[str, Any]]) -> list[dict[str, Any]]:
    me_rows = [row for row in utterances if is_me(row)]
    remote_rows = [row for row in utterances if role_name(row) == "Colleagues"]
    overlaps: list[dict[str, Any]] = []
    index = 1
    for me in me_rows:
        for remote in remote_rows:
            duration = overlap_duration(me, remote)
            if duration <= 0:
                continue
            start = max(safe_float(me.get("start")), safe_float(remote.get("start")))
            end = min(safe_float(me.get("end")), safe_float(remote.get("end")))
            overlaps.append(
                {
                    "id": f"ov_{index:06d}",
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "duration_sec": round(duration, 3),
                    "me_utterance_id": me.get("id"),
                    "remote_utterance_id": remote.get("id"),
                    "text_similarity": round(text_similarity(me.get("text"), remote.get("text")), 6),
                    "me_text": me.get("text"),
                    "remote_text": remote.get("text"),
                }
            )
            index += 1
    return overlaps


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
        lines.extend([f"## {format_time(row.get('start'))} {role_name(row)}", "", str(row.get("text") or "").strip(), ""])
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def selected_profile_from_decisions(rows: list[dict[str, Any]]) -> str | None:
    profiles = {str(row.get("input_profile") or "") for row in rows if row.get("input_profile")}
    if len(profiles) == 1:
        return next(iter(profiles))
    return None


def existing_profile(session: Path) -> str:
    resolved = session / "derived/transcript-simple/whisper-cpp/resolved"
    cleanup = session / "derived/transcript-simple/whisper-cpp/audit-cleanup"
    review_dir = session / "derived/transcript-simple/whisper-cpp/review-decisions"
    report = read_json(review_dir / "review_decisions_report.reviewed_v1.json") if (review_dir / "review_decisions_report.reviewed_v1.json").exists() else None
    if (
        report
        and (report.get("gates") or {}).get("passed") is True
        and (resolved / "clean_dialogue.reviewed_v1.json").exists()
        and (resolved / "quality_report.reviewed_v1.json").exists()
    ):
        return "reviewed_v1"
    v7 = cleanup / "audit_cleanup_report.audit_cleanup_v7.json"
    if v7.exists():
        data = read_json(v7)
        if (data.get("gates") or {}).get("passed") is True and (data.get("summary") or {}).get("applied_patches", 0) > 0:
            return "audit_cleanup_v7"
    agent_report = read_json(review_dir / "review_decisions_report.agent_reviewed_v1.json") if (review_dir / "review_decisions_report.agent_reviewed_v1.json").exists() else None
    if (
        agent_report
        and (agent_report.get("gates") or {}).get("passed") is True
        and (resolved / "clean_dialogue.agent_reviewed_v1.json").exists()
        and (resolved / "quality_report.agent_reviewed_v1.json").exists()
    ):
        return "agent_reviewed_v1"
    v4 = cleanup / "audit_cleanup_report.audit_cleanup_v4.json"
    if v4.exists():
        data = read_json(v4)
        if (data.get("gates") or {}).get("passed") is True and (data.get("summary") or {}).get("applied_patches", 0) > 0:
            return "audit_cleanup_v4"
    v3 = cleanup / "audit_cleanup_report.audit_cleanup_v3.json"
    if v3.exists():
        data = read_json(v3)
        if (data.get("gates") or {}).get("passed") is True and (data.get("summary") or {}).get("applied_patches", 0) > 0:
            return "audit_cleanup_v3"
    for profile in ("audit_cleanup_v6", "audit_cleanup_v5", "audit_cleanup_v2", "audit_cleanup_v1", "shadow_v2", "current"):
        if (resolved / f"clean_dialogue{suffix(profile)}.json").exists() and (resolved / f"quality_report{suffix(profile)}.json").exists():
            return profile
    return "current"


def normalize_decision(row: dict[str, Any]) -> dict[str, Any]:
    decision = str(row.get("decision") or row.get("status") or "").strip()
    normalized = {**row, "decision": decision}
    if decision not in VALID_DECISIONS:
        normalized["_invalid"] = True
        normalized["_invalid_reason"] = "unknown_decision"
    allowed_values = row.get("allowed_decisions")
    allowed = {str(value) for value in allowed_values} if isinstance(allowed_values, list) else set()
    if decision not in OPEN_DECISIONS and allowed and decision not in allowed:
        normalized["_invalid"] = True
        normalized["_invalid_reason"] = "decision_not_allowed_for_row"
    if is_local_recall_decision(normalized) and decision in {"drop_me", "drop_remote"}:
        normalized["_invalid"] = True
        normalized["_invalid_reason"] = f"{decision}_is_not_supported_for_local_recall"
    if is_local_recall_decision(normalized) and decision == "keep_me":
        normalized["_invalid"] = True
        normalized["_invalid_reason"] = "keep_me_requires_materialized_local_recall_utterance"
    if is_transcript_order_decision(normalized) and decision in {"drop_me", "drop_remote"}:
        normalized["_invalid"] = True
        normalized["_invalid_reason"] = f"{decision}_is_not_supported_for_transcript_order"
    return normalized


def is_local_recall_decision(row: dict[str, Any]) -> bool:
    source = str(row.get("source") or "").strip()
    if source == "local_recall_repair":
        return False
    label = str(row.get("label") or "").strip()
    source_id = str(row.get("source_audit_id") or "").strip()
    return source == "local_recall" or source_id.startswith("local_recall_") or label in {"lost_me", "local_recall_needs_review"}


def is_local_recall_repair_decision(row: dict[str, Any]) -> bool:
    return str(row.get("source") or "").strip() == "local_recall_repair"


def obsolete_audit_only_local_recall_keep(row: dict[str, Any]) -> bool:
    return str(row.get("source") or "").strip() == "local_recall" and str(row.get("decision") or "").strip() == "keep_me"


def is_transcript_order_decision(row: dict[str, Any]) -> bool:
    source = str(row.get("source") or "").strip()
    label = str(row.get("label") or "").strip()
    source_id = str(row.get("source_audit_id") or "").strip()
    return source == "transcript_order" or source_id.startswith("order_") or label in {"probable_order_risk", "transcript_order_needs_review"}


def decisions_for_session(path: Path, session: Path) -> list[dict[str, Any]]:
    rows = read_jsonl(path)
    selected: list[dict[str, Any]] = []
    for row in rows:
        if str(row.get("session_id") or "") != session.name:
            continue
        selected.append(normalize_decision(row))
    return selected


def candidate_template_paths(args: argparse.Namespace) -> list[Path]:
    if args.review_template:
        return [args.review_template.expanduser()]
    return [
        args.decisions.expanduser().with_name("review_decisions.template.jsonl"),
        Path("sessions/_reports/review-plan/review_decisions.template.jsonl"),
    ]


def template_for_session(args: argparse.Namespace, session: Path) -> tuple[list[dict[str, Any]], Path | None]:
    existing_path: Path | None = None
    for path in candidate_template_paths(args):
        if not path.exists():
            continue
        if existing_path is None:
            existing_path = path
        rows = [normalize_decision(row) for row in read_jsonl(path) if str(row.get("session_id") or "") == session.name]
        if rows:
            return rows, path
    return [], existing_path


def review_row_key(row: dict[str, Any]) -> str:
    cluster_id = str(row.get("cluster_id") or "").strip()
    utterance_ids = row.get("utterance_ids")
    utterance_key = ",".join(str(item) for item in utterance_ids) if isinstance(utterance_ids, list) else ""
    interval = row.get("interval") if isinstance(row.get("interval"), dict) else {}
    start = interval.get("start")
    end = interval.get("end")
    return (
        "review:"
        f"{row.get('session_id') or ''}:"
        f"{cluster_id}:"
        f"{utterance_key}:"
        f"{start}:{end}:"
        f"{row.get('label')}"
    )


def review_coverage(
    all_decisions: list[dict[str, Any]],
    template_rows: list[dict[str, Any]],
    template_path: Path | None,
    allow_partial_review: bool,
) -> dict[str, Any]:
    def row_duration(row: dict[str, Any]) -> float:
        interval = row.get("interval") if isinstance(row.get("interval"), dict) else {}
        duration = safe_float(interval.get("duration_sec"))
        if duration > 0:
            return duration
        start = safe_float(interval.get("start", row.get("start")))
        end = safe_float(interval.get("end", row.get("end")))
        return max(0.0, end - start)

    decision_by_key: dict[str, dict[str, Any]] = {}
    duplicates: list[str] = []
    for row in all_decisions:
        key = review_row_key(row)
        if key in decision_by_key:
            duplicates.append(key)
            continue
        decision_by_key[key] = row

    missing: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    for row in template_rows:
        key = review_row_key(row)
        decided = decision_by_key.get(key)
        if not decided:
            missing.append({**row, "review_key": key, "reason": "missing_decision_row"})
            continue
        if str(decided.get("decision") or "") in OPEN_DECISIONS:
            pending.append({**decided, "review_key": key, "reason": "decision_still_todo"})

    if template_rows:
        required_rows = len(template_rows)
        closed_rows = required_rows - len(missing) - len(pending)
        full_scope_complete = len(missing) == 0 and len(pending) == 0
        partial_allowed = allow_partial_review and not full_scope_complete and closed_rows > 0
        status = "complete" if full_scope_complete else ("partial_allowed" if partial_allowed else "incomplete")
    elif template_path is not None and template_path.exists():
        required_rows = 0
        closed_rows = 0
        full_scope_complete = True
        partial_allowed = False
        status = "complete_empty_scope"
    elif all_decisions and all(str(row.get("decision") or "") not in OPEN_DECISIONS for row in all_decisions):
        required_rows = len(all_decisions)
        closed_rows = required_rows
        full_scope_complete = True
        partial_allowed = False
        status = "complete_from_decisions"
    elif allow_partial_review:
        required_rows = len(all_decisions)
        closed_rows = sum(1 for row in all_decisions if str(row.get("decision") or "") not in OPEN_DECISIONS)
        full_scope_complete = False
        partial_allowed = closed_rows > 0
        status = "partial_allowed" if partial_allowed else "missing_template_scope"
    else:
        required_rows = 0
        closed_rows = 0
        full_scope_complete = False
        partial_allowed = False
        status = "missing_template_scope"
    allowed = full_scope_complete or partial_allowed
    missing_seconds = round(sum(row_duration(row) for row in missing), 3)
    pending_seconds = round(sum(row_duration(row) for row in pending), 3)

    return {
        "schema": "murmurmark.review_coverage/v1",
        "status": status,
        "complete": full_scope_complete,
        "full_scope_complete": full_scope_complete,
        "allowed": allowed,
        "partial_allowed": partial_allowed,
        "allow_partial_review": allow_partial_review,
        "template_path": str(template_path) if template_path else None,
        "required_rows": required_rows,
        "closed_rows": closed_rows,
        "coverage_ratio": round(closed_rows / max(1, required_rows), 6),
        "missing_rows": len(missing),
        "pending_rows": len(pending),
        "missing_review_seconds": missing_seconds,
        "pending_review_seconds": pending_seconds,
        "remaining_review_seconds": round(missing_seconds + pending_seconds, 3),
        "duplicate_decision_keys": sorted(set(duplicates)),
        "missing_examples": [
            {
                "review_key": row.get("review_key"),
                "source_audit_id": row.get("source_audit_id"),
                "cluster_id": row.get("cluster_id"),
                "utterance_ids": row.get("utterance_ids"),
                "interval": row.get("interval"),
                "reason": row.get("reason"),
            }
            for row in missing[:10]
        ],
        "pending_examples": [
            {
                "review_key": row.get("review_key"),
                "source_audit_id": row.get("source_audit_id"),
                "cluster_id": row.get("cluster_id"),
                "utterance_ids": row.get("utterance_ids"),
                "interval": row.get("interval"),
                "decision": row.get("decision"),
                "reason": row.get("reason"),
            }
            for row in pending[:10]
        ],
    }


def decision_me_ids(row: dict[str, Any]) -> list[str]:
    ids = row.get("me_utterance_ids")
    if isinstance(ids, list) and ids:
        return [str(item) for item in ids if item]
    out: list[str] = []
    for item in row.get("text") or []:
        if isinstance(item, dict) and (str(item.get("source_track") or "").lower() == "mic" or str(item.get("role") or "").lower() == "me"):
            if item.get("id"):
                out.append(str(item["id"]))
    return out


def decision_remote_ids(row: dict[str, Any]) -> list[str]:
    ids = row.get("remote_utterance_ids")
    if isinstance(ids, list) and ids:
        return [str(item) for item in ids if item]
    out: list[str] = []
    for item in row.get("text") or []:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source_track") or "").lower()
        role = str(item.get("role") or "").lower()
        if source == "remote" or role in {"remote", "colleagues"}:
            if item.get("id"):
                out.append(str(item["id"]))
    return out


def decision_utterance_ids(row: dict[str, Any]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []

    def add(value: Any) -> None:
        utterance_id = str(value or "").strip()
        if utterance_id and utterance_id not in seen:
            seen.add(utterance_id)
            out.append(utterance_id)

    for key in ("utterance_ids", "me_utterance_ids", "remote_utterance_ids"):
        values = row.get(key)
        if isinstance(values, list):
            for value in values:
                add(value)

    for item in row.get("text") or []:
        if isinstance(item, dict):
            add(item.get("id"))

    return out


def add_review_quality(row: dict[str, Any], key: str, rows: list[dict[str, Any]], output_profile: str) -> None:
    if not rows:
        return
    quality = row.setdefault("quality", {})
    if not isinstance(quality, dict):
        quality = {}
        row["quality"] = quality
    decisions = sorted({str(item.get("decision") or "") for item in rows if item.get("decision")})
    needs_review = "needs_review" in decisions
    quality[key] = {
        "profile": output_profile,
        "status": "needs_review" if needs_review else "cleared",
        "decisions": decisions,
        "source_audit_ids": sorted({str(item.get("source_audit_id")) for item in rows if item.get("source_audit_id")}),
    }
    if needs_review:
        quality["needs_review"] = True


def quality_report(
    input_quality: dict[str, Any],
    utterances: list[dict[str, Any]],
    overlaps: list[dict[str, Any]],
    review_summary: dict[str, Any],
    output_profile: str,
) -> dict[str, Any]:
    report = copy.deepcopy(input_quality)
    report["schema"] = "murmurmark.simple_transcript_quality/v1"
    report["utterances"] = len(utterances)
    report["needs_review_count"] = sum(1 for row in utterances if isinstance(row.get("quality"), dict) and row["quality"].get("needs_review"))
    report["needs_review_ratio"] = round(report["needs_review_count"] / max(1, len(utterances)), 6)
    report["cross_role_overlap_count"] = len(overlaps)
    report["cross_role_overlap_seconds"] = round(sum(safe_float(row.get("duration_sec")) for row in overlaps), 3)
    report["cross_role_overlap_gt2_count"] = sum(1 for row in overlaps if safe_float(row.get("duration_sec")) > 2.0)
    report["cross_role_overlap_gt2_seconds"] = round(sum(safe_float(row.get("duration_sec")) for row in overlaps if safe_float(row.get("duration_sec")) > 2.0), 3)
    duplicate_overlaps = [row for row in overlaps if safe_float(row.get("text_similarity")) >= 0.65]
    report["remote_duplicate_in_me_count"] = len(duplicate_overlaps)
    report["remote_duplicate_in_me_seconds"] = round(sum(safe_float(row.get("duration_sec")) for row in duplicate_overlaps), 3)
    report["meeting_duration_sec"] = round(max((safe_float(row.get("end")) for row in utterances), default=0.0), 3)
    report["human_review"] = review_summary
    if output_profile.startswith("agent_reviewed"):
        report["agent_review"] = review_summary
    return report


def main() -> int:
    args = parse_args()
    session = args.session.expanduser().resolve()
    resolved = session / "derived/transcript-simple/whisper-cpp/resolved"
    review_dir = session / "derived/transcript-simple/whisper-cpp/review-decisions"
    all_decisions = decisions_for_session(args.decisions.expanduser(), session)
    obsolete_decisions = [row for row in all_decisions if obsolete_audit_only_local_recall_keep(row)]
    decisions = [row for row in all_decisions if not obsolete_audit_only_local_recall_keep(row)]
    template_rows, template_path = template_for_session(args, session)
    coverage = review_coverage(decisions, template_rows, template_path, args.allow_partial_review)
    invalid_decisions = [row for row in decisions if row.get("_invalid")]
    template_keys = {review_row_key(row) for row in template_rows}
    if template_path is not None:
        in_scope_decisions = [row for row in decisions if review_row_key(row) in template_keys]
    else:
        in_scope_decisions = decisions
    out_of_scope_decisions = [row for row in decisions if row not in in_scope_decisions]
    profile_decisions = [
        row for row in in_scope_decisions if not row.get("_invalid") and row.get("decision") not in OPEN_DECISIONS
    ]
    valid_decisions = [
        row
        for row in profile_decisions
        if row.get("decision") != "skip" or is_local_recall_decision(row) or is_transcript_order_decision(row)
    ]
    input_profile = args.input_profile
    if input_profile == "auto":
        input_profile = selected_profile_from_decisions(profile_decisions) or existing_profile(session)
        if input_profile == args.output_profile:
            input_profile = selected_profile_from_decisions(profile_decisions) or "audit_cleanup_v2"

    input_suffix = suffix(input_profile)
    output_suffix = suffix(args.output_profile)
    dialogue_path = resolved / f"clean_dialogue{input_suffix}.json"
    quality_path = resolved / f"quality_report{input_suffix}.json"
    report_path = resolved / f"transcribe_simple_report{input_suffix}.json"
    dialogue = read_json(dialogue_path)
    input_quality = read_json(quality_path)
    transcript_report = read_json(report_path) if report_path.exists() else {}
    if not transcript_report:
        base_report_path = resolved / "transcribe_simple_report.json"
        transcript_report = read_json(base_report_path) if base_report_path.exists() else {}
    utterances = [row for row in dialogue.get("utterances", []) if isinstance(row, dict)]
    by_id = {str(row.get("id")): row for row in utterances}

    per_utterance: dict[str, list[dict[str, Any]]] = {}
    per_remote_utterance: dict[str, list[dict[str, Any]]] = {}
    transcript_order_by_utterance: dict[str, list[dict[str, Any]]] = {}
    audit_only_applied: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for row in valid_decisions:
        if is_local_recall_decision(row):
            audit_only_applied.append({**row, "review_effect": "audit_only_local_recall"})
            continue
        if is_transcript_order_decision(row):
            audit_only_applied.append({**row, "review_effect": "audit_only_transcript_order"})
            for utterance_id in decision_utterance_ids(row):
                if utterance_id in by_id:
                    transcript_order_by_utterance.setdefault(utterance_id, []).append(row)
            continue
        if row.get("decision") == "drop_remote":
            remote_ids = decision_remote_ids(row)
            if not remote_ids:
                rejected.append({**row, "reason": "missing_remote_utterance_id"})
                continue
            for utterance_id in remote_ids:
                if utterance_id not in by_id:
                    rejected.append({**row, "reason": "remote_utterance_not_found", "utterance_id": utterance_id})
                    continue
                if role_name(by_id[utterance_id]) != "Colleagues":
                    rejected.append({**row, "reason": "target_is_not_remote", "utterance_id": utterance_id})
                    continue
                per_remote_utterance.setdefault(utterance_id, []).append(row)
            for utterance_id in decision_me_ids(row):
                if utterance_id in by_id and is_me(by_id[utterance_id]):
                    per_utterance.setdefault(utterance_id, []).append(row)
            continue
        me_ids = decision_me_ids(row)
        if not me_ids:
            rejected.append({**row, "reason": "missing_me_utterance_id"})
            continue
        for utterance_id in me_ids:
            if utterance_id not in by_id:
                rejected.append({**row, "reason": "utterance_not_found", "utterance_id": utterance_id})
                continue
            if not is_me(by_id[utterance_id]):
                rejected.append({**row, "reason": "target_is_not_me", "utterance_id": utterance_id})
                continue
            per_utterance.setdefault(utterance_id, []).append(row)

    conflicts: list[dict[str, Any]] = []
    dropped_ids: set[str] = set()
    dropped_remote_ids: set[str] = set()
    applied: list[dict[str, Any]] = []
    remote_applied: list[dict[str, Any]] = []
    for utterance_id, rows in per_utterance.items():
        decisions_set = {str(row.get("decision")) for row in rows}
        if "drop_me" in decisions_set and (decisions_set - {"drop_me"}):
            conflicts.append({"utterance_id": utterance_id, "decisions": sorted(decisions_set), "rows": rows})
            continue
        if decisions_set == {"drop_me"}:
            dropped_ids.add(utterance_id)
        applied.extend({**row, "utterance_id": utterance_id} for row in rows)
    for utterance_id, rows in per_remote_utterance.items():
        decisions_set = {str(row.get("decision")) for row in rows}
        if decisions_set != {"drop_remote"}:
            conflicts.append({"remote_utterance_id": utterance_id, "decisions": sorted(decisions_set), "rows": rows})
            continue
        dropped_remote_ids.add(utterance_id)
        remote_applied.extend({**row, "remote_utterance_id": utterance_id, "review_effect": "drop_remote"} for row in rows)

    output_utterances: list[dict[str, Any]] = []
    for row in utterances:
        utterance_id = str(row.get("id"))
        if utterance_id in dropped_ids or utterance_id in dropped_remote_ids:
            continue
        new_row = copy.deepcopy(row)
        rows = per_utterance.get(utterance_id, [])
        order_rows = transcript_order_by_utterance.get(utterance_id, [])
        if rows:
            decisions_set = {str(item.get("decision")) for item in rows}
            quality = new_row.setdefault("quality", {})
            if not isinstance(quality, dict):
                quality = {}
                new_row["quality"] = quality
            review_key = "agent_review" if args.output_profile.startswith("agent_reviewed") else "human_review"
            quality[review_key] = {
                "profile": args.output_profile,
                "decisions": sorted(decisions_set),
                "source_audit_ids": sorted({str(item.get("source_audit_id")) for item in rows if item.get("source_audit_id")}),
            }
            if "needs_review" in decisions_set or utterance_id in {item.get("utterance_id") for item in conflicts}:
                quality["needs_review"] = True
            elif decisions_set <= {"keep_me", "drop_remote"}:
                quality["needs_review"] = False
        add_review_quality(new_row, "transcript_order_review", order_rows, args.output_profile)
        output_utterances.append(new_row)

    overlaps = build_overlaps(output_utterances)
    applied_all = applied + remote_applied + audit_only_applied
    local_recall_rows = [row for row in audit_only_applied if is_local_recall_decision(row)] + [
        row for row in applied if is_local_recall_repair_decision(row)
    ]
    local_recall_cleared = [row for row in local_recall_rows if row.get("decision") in {"keep_me", "skip"}]
    local_recall_remaining = [row for row in local_recall_rows if row.get("decision") == "needs_review"]
    local_recall_possible_lost_remaining = [
        row
        for row in local_recall_remaining
        if str(row.get("label") or "") in {"lost_me", "local_recall_repair_inserted"}
    ]
    local_recall_review_remaining = [
        row for row in local_recall_remaining if str(row.get("label") or "") == "local_recall_needs_review"
    ]
    transcript_order_rows = [row for row in audit_only_applied if is_transcript_order_decision(row)]
    transcript_order_cleared = [row for row in transcript_order_rows if row.get("decision") in {"keep_me", "skip"}]
    transcript_order_remaining = [row for row in transcript_order_rows if row.get("decision") == "needs_review"]
    transcript_order_risk_remaining = [
        row for row in transcript_order_remaining if str(row.get("label") or "") == "probable_order_risk"
    ]
    review_summary = {
        "schema": "murmurmark.review_decisions_summary/v1",
        "review_mode": "agent" if args.output_profile.startswith("agent_reviewed") else "human",
        "input_profile": input_profile,
        "output_profile": args.output_profile,
        "decision_rows": len(decisions),
        "closed_decision_rows": sum(1 for row in decisions if str(row.get("decision") or "") not in OPEN_DECISIONS),
        "skipped_decision_rows": sum(1 for row in decisions if row.get("decision") == "skip"),
        "pending_decision_rows": sum(1 for row in decisions if str(row.get("decision") or "") in OPEN_DECISIONS),
        "in_scope_decision_rows": len(in_scope_decisions),
        "ignored_out_of_scope_decision_rows": len(out_of_scope_decisions),
        "applied_decision_rows": len(applied_all),
        "transcript_applied_decision_rows": len(applied),
        "audit_only_applied_decision_rows": len(audit_only_applied),
        "rejected_decision_rows": len(rejected),
        "ignored_obsolete_audit_only_local_recall_keep_rows": len(obsolete_decisions),
        "conflict_count": len(conflicts),
        "dropped_me_utterances": len(dropped_ids),
        "dropped_me_seconds": round(sum(safe_float(by_id[item].get("end")) - safe_float(by_id[item].get("start")) for item in dropped_ids), 3),
        "dropped_remote_utterances": len(dropped_remote_ids),
        "dropped_remote_seconds": round(
            sum(safe_float(by_id[item].get("end")) - safe_float(by_id[item].get("start")) for item in dropped_remote_ids),
            3,
        ),
        "kept_me_decisions": sum(1 for row in applied if row.get("decision") == "keep_me"),
        "drop_remote_decisions": sum(1 for row in remote_applied if row.get("decision") == "drop_remote"),
        "needs_review_decisions": sum(1 for row in applied if row.get("decision") == "needs_review"),
        "local_recall_decision_rows": len(local_recall_rows),
        "local_recall_cleared_decisions": len(local_recall_cleared),
        "local_recall_needs_review_decisions": len(local_recall_remaining),
        "local_recall_remaining_possible_lost_me_count": len(local_recall_possible_lost_remaining),
        "local_recall_remaining_needs_review_count": len(local_recall_review_remaining),
        "local_recall_reviewed_seconds": round(
            sum(safe_float((row.get("interval") or {}).get("duration_sec")) for row in local_recall_rows),
            3,
        ),
        "local_recall_cleared_seconds": round(
            sum(safe_float((row.get("interval") or {}).get("duration_sec")) for row in local_recall_cleared),
            3,
        ),
        "local_recall_remaining_review_seconds": round(
            sum(safe_float((row.get("interval") or {}).get("duration_sec")) for row in local_recall_remaining),
            3,
        ),
        "local_recall_remaining_possible_lost_me_seconds": round(
            sum(safe_float((row.get("interval") or {}).get("duration_sec")) for row in local_recall_possible_lost_remaining),
            3,
        ),
        "local_recall_remaining_needs_review_seconds": round(
            sum(safe_float((row.get("interval") or {}).get("duration_sec")) for row in local_recall_review_remaining),
            3,
        ),
        "transcript_order_decision_rows": len(transcript_order_rows),
        "transcript_order_cleared_decisions": len(transcript_order_cleared),
        "transcript_order_needs_review_decisions": len(transcript_order_remaining),
        "transcript_order_remaining_probable_order_risk_count": len(transcript_order_risk_remaining),
        "transcript_order_reviewed_seconds": round(
            sum(safe_float((row.get("interval") or {}).get("duration_sec")) for row in transcript_order_rows),
            3,
        ),
        "transcript_order_cleared_seconds": round(
            sum(safe_float((row.get("interval") or {}).get("duration_sec")) for row in transcript_order_cleared),
            3,
        ),
        "transcript_order_remaining_review_seconds": round(
            sum(safe_float((row.get("interval") or {}).get("duration_sec")) for row in transcript_order_remaining),
            3,
        ),
        "transcript_order_remaining_probable_order_risk_seconds": round(
            sum(safe_float((row.get("interval") or {}).get("duration_sec")) for row in transcript_order_risk_remaining),
            3,
        ),
        "review_scope_status": coverage["status"],
        "review_scope_allowed": coverage["allowed"],
        "review_scope_partial_allowed": coverage["partial_allowed"],
        "review_scope_complete": coverage["complete"],
        "review_scope_required_rows": coverage["required_rows"],
        "review_scope_closed_rows": coverage["closed_rows"],
        "review_scope_coverage_ratio": coverage["coverage_ratio"],
        "review_scope_missing_rows": coverage["missing_rows"],
        "review_scope_pending_rows": coverage["pending_rows"],
        "review_scope_remaining_seconds": coverage["remaining_review_seconds"],
    }
    output_quality = quality_report(input_quality, output_utterances, overlaps, review_summary, args.output_profile)
    gates = {
        "passed": not invalid_decisions and not conflicts and coverage["allowed"],
        "hard_failures": [],
        "warnings": [],
    }
    if invalid_decisions:
        gates["hard_failures"].append("invalid_decisions")
    if conflicts:
        gates["hard_failures"].append("conflicting_decisions")
    if not coverage["allowed"]:
        gates["hard_failures"].append("incomplete_review_scope")
    elif not coverage["complete"]:
        gates["warnings"].append("incomplete_review_scope_allowed")
    if not valid_decisions:
        gates["warnings"].append("no_review_decisions_applied")
    if coverage["status"] == "partial_allowed":
        gates["warnings"].append("partial_review_scope_allowed")
    if obsolete_decisions:
        gates["warnings"].append("obsolete_audit_only_local_recall_keep_ignored")
    if out_of_scope_decisions:
        gates["warnings"].append("out_of_scope_review_decisions_ignored")
    if coverage["duplicate_decision_keys"]:
        gates["warnings"].append("duplicate_decision_keys")

    output_dialogue = {
        "schema": "murmurmark.clean_dialogue/v1",
        "session": dialogue.get("session", session.name),
        "utterances": output_utterances,
    }
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
    report = {
        "schema": "murmurmark.review_decisions_report/v1",
        "generator": {"name": "apply-review-decisions", "version": SCRIPT_VERSION},
        "input_profile": input_profile,
        "output_profile": args.output_profile,
        "inputs": {
            "decisions": str(args.decisions),
            "clean_dialogue": rel(dialogue_path, session),
            "quality_report": rel(quality_path, session),
            "allow_partial_review": args.allow_partial_review,
        },
        "summary": review_summary,
        "coverage": coverage,
        "gates": gates,
    }

    write_json(resolved / f"clean_dialogue{output_suffix}.json", output_dialogue)
    write_json(resolved / f"quality_report{output_suffix}.json", output_quality)
    write_json(resolved / f"overlaps{output_suffix}.json", {"schema": "murmurmark.transcript_overlaps/v1", "session": session.name, "overlaps": overlaps})
    write_json(resolved / f"transcript.simple{output_suffix}.json", simple_payload)
    write_markdown(resolved / f"transcript{output_suffix}.md", output_utterances, transcript_report.get("model"), transcript_report.get("language"))
    write_json(review_dir / f"review_decisions_report{output_suffix}.json", report)
    write_jsonl(review_dir / f"review_decisions_applied{output_suffix}.jsonl", applied_all)
    write_jsonl(review_dir / f"review_decisions_rejected{output_suffix}.jsonl", rejected)
    write_jsonl(review_dir / f"review_decisions_conflicts{output_suffix}.jsonl", conflicts)

    print(f"review_decisions_report: {review_dir / f'review_decisions_report{output_suffix}.json'}")
    print(f"clean_dialogue: {resolved / f'clean_dialogue{output_suffix}.json'}")
    print(f"applied_decision_rows: {len(applied_all)}")
    print(f"dropped_me_utterances: {len(dropped_ids)}")
    print(f"dropped_remote_utterances: {len(dropped_remote_ids)}")
    print(f"gates_passed: {gates['passed']}")
    return 0 if gates["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
