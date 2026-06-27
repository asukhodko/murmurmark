#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


SCRIPT_VERSION = "0.2.1"
OUTPUT_PROFILE_DEFAULT = "reviewed_v1"
VALID_DECISIONS = {"drop_me", "keep_me", "needs_review", "skip", "todo", ""}
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
    for profile in ("audit_cleanup_v2", "audit_cleanup_v1", "shadow_v2", "current"):
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
    if is_local_recall_decision(normalized) and decision == "drop_me":
        normalized["_invalid"] = True
        normalized["_invalid_reason"] = "drop_me_is_not_supported_for_local_recall"
    if is_transcript_order_decision(normalized) and decision == "drop_me":
        normalized["_invalid"] = True
        normalized["_invalid_reason"] = "drop_me_is_not_supported_for_transcript_order"
    return normalized


def is_local_recall_decision(row: dict[str, Any]) -> bool:
    source = str(row.get("source") or "").strip()
    label = str(row.get("label") or "").strip()
    source_id = str(row.get("source_audit_id") or "").strip()
    return source == "local_recall" or source_id.startswith("local_recall_") or label in {"lost_me", "local_recall_needs_review"}


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
    for path in candidate_template_paths(args):
        if not path.exists():
            continue
        rows = [normalize_decision(row) for row in read_jsonl(path) if str(row.get("session_id") or "") == session.name]
        if rows:
            return rows, path
    return [], None


def review_row_key(row: dict[str, Any]) -> str:
    source_id = str(row.get("source_audit_id") or "").strip()
    cluster_id = str(row.get("cluster_id") or "").strip()
    utterance_ids = row.get("utterance_ids")
    utterance_key = ",".join(str(item) for item in utterance_ids) if isinstance(utterance_ids, list) else ""
    interval = row.get("interval") if isinstance(row.get("interval"), dict) else {}
    return (
        "review:"
        f"{source_id}:"
        f"{row.get('session_id') or ''}:"
        f"{cluster_id}:"
        f"{utterance_key}:"
        f"{interval.get('start')}:{interval.get('end')}:"
        f"{row.get('label')}"
    )


def review_coverage(
    all_decisions: list[dict[str, Any]],
    template_rows: list[dict[str, Any]],
    template_path: Path | None,
    allow_partial_review: bool,
) -> dict[str, Any]:
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
        complete = len(missing) == 0 and len(pending) == 0
        status = "complete" if complete else "incomplete"
    elif allow_partial_review:
        required_rows = len(all_decisions)
        closed_rows = sum(1 for row in all_decisions if str(row.get("decision") or "") not in OPEN_DECISIONS)
        complete = True
        status = "partial_allowed"
    else:
        required_rows = 0
        closed_rows = 0
        complete = False
        status = "missing_template_scope"

    return {
        "schema": "murmurmark.review_coverage/v1",
        "status": status,
        "complete": complete,
        "allow_partial_review": allow_partial_review,
        "template_path": str(template_path) if template_path else None,
        "required_rows": required_rows,
        "closed_rows": closed_rows,
        "coverage_ratio": round(closed_rows / max(1, required_rows), 6),
        "missing_rows": len(missing),
        "pending_rows": len(pending),
        "duplicate_decision_keys": sorted(set(duplicates)),
        "missing_examples": [
            {
                "review_key": row.get("review_key"),
                "source_audit_id": row.get("source_audit_id"),
                "cluster_id": row.get("cluster_id"),
                "utterance_ids": row.get("utterance_ids"),
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
    decisions = decisions_for_session(args.decisions.expanduser(), session)
    template_rows, template_path = template_for_session(args, session)
    coverage = review_coverage(decisions, template_rows, template_path, args.allow_partial_review)
    invalid_decisions = [row for row in decisions if row.get("_invalid")]
    profile_decisions = [row for row in decisions if not row.get("_invalid") and row.get("decision") not in OPEN_DECISIONS]
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
    utterances = [row for row in dialogue.get("utterances", []) if isinstance(row, dict)]
    by_id = {str(row.get("id")): row for row in utterances}

    per_utterance: dict[str, list[dict[str, Any]]] = {}
    audit_only_applied: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for row in valid_decisions:
        if is_local_recall_decision(row):
            audit_only_applied.append({**row, "review_effect": "audit_only_local_recall"})
            continue
        if is_transcript_order_decision(row):
            audit_only_applied.append({**row, "review_effect": "audit_only_transcript_order"})
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
    applied: list[dict[str, Any]] = []
    for utterance_id, rows in per_utterance.items():
        decisions_set = {str(row.get("decision")) for row in rows}
        if "drop_me" in decisions_set and (decisions_set - {"drop_me"}):
            conflicts.append({"utterance_id": utterance_id, "decisions": sorted(decisions_set), "rows": rows})
            continue
        if decisions_set == {"drop_me"}:
            dropped_ids.add(utterance_id)
        applied.extend({**row, "utterance_id": utterance_id} for row in rows)

    output_utterances: list[dict[str, Any]] = []
    for row in utterances:
        utterance_id = str(row.get("id"))
        if utterance_id in dropped_ids:
            continue
        new_row = copy.deepcopy(row)
        rows = per_utterance.get(utterance_id, [])
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
            elif decisions_set == {"keep_me"}:
                quality["needs_review"] = False
        output_utterances.append(new_row)

    overlaps = build_overlaps(output_utterances)
    applied_all = applied + audit_only_applied
    local_recall_rows = [row for row in audit_only_applied if is_local_recall_decision(row)]
    local_recall_cleared = [row for row in local_recall_rows if row.get("decision") in {"keep_me", "skip"}]
    local_recall_remaining = [row for row in local_recall_rows if row.get("decision") == "needs_review"]
    local_recall_possible_lost_remaining = [
        row for row in local_recall_remaining if str(row.get("label") or "") == "lost_me"
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
        "applied_decision_rows": len(applied_all),
        "transcript_applied_decision_rows": len(applied),
        "audit_only_applied_decision_rows": len(audit_only_applied),
        "rejected_decision_rows": len(rejected),
        "conflict_count": len(conflicts),
        "dropped_me_utterances": len(dropped_ids),
        "dropped_me_seconds": round(sum(safe_float(by_id[item].get("end")) - safe_float(by_id[item].get("start")) for item in dropped_ids), 3),
        "kept_me_decisions": sum(1 for row in applied if row.get("decision") == "keep_me"),
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
        "review_scope_complete": coverage["complete"],
        "review_scope_required_rows": coverage["required_rows"],
        "review_scope_closed_rows": coverage["closed_rows"],
        "review_scope_coverage_ratio": coverage["coverage_ratio"],
    }
    output_quality = quality_report(input_quality, output_utterances, overlaps, review_summary, args.output_profile)
    gates = {
        "passed": not invalid_decisions and not conflicts and coverage["complete"],
        "hard_failures": [],
        "warnings": [],
    }
    if invalid_decisions:
        gates["hard_failures"].append("invalid_decisions")
    if conflicts:
        gates["hard_failures"].append("conflicting_decisions")
    if not coverage["complete"]:
        gates["hard_failures"].append("incomplete_review_scope")
    if not valid_decisions:
        gates["warnings"].append("no_review_decisions_applied")
    if coverage["status"] == "partial_allowed":
        gates["warnings"].append("partial_review_scope_allowed")
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
    print(f"gates_passed: {gates['passed']}")
    return 0 if gates["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
