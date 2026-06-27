#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


SCRIPT_VERSION = "0.1.0"
SCHEMA = "murmurmark.suggested_cleanup_apply_report/v1"
OUTPUT_PROFILE_DEFAULT = "audit_cleanup_v5"
SUGGESTED_PROFILE = "suggested_review_v1"
CANDIDATE_ASSESSMENTS = {"promising_shadow_candidate", "promising_cleanup_candidate_with_residual_review"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Materialize safe suggested_review_v1 drops as an audit cleanup profile.")
    parser.add_argument("sessions", nargs="*", type=Path)
    parser.add_argument(
        "--shadow-report",
        type=Path,
        default=Path("sessions/_reports/suggested-review-shadow/suggested_review_shadow_report.json"),
        help="Input suggested review shadow report.",
    )
    parser.add_argument("--output-profile", default=OUTPUT_PROFILE_DEFAULT)
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("sessions/_reports/suggested-review-shadow/suggested_cleanup_apply_report.json"),
        help="Batch apply report.",
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


def rel(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def safe_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def normalize_text(text: Any) -> str:
    return " ".join(str(text or "").lower().replace("ё", "е").split())


def text_similarity(left: Any, right: Any) -> float:
    left_norm = normalize_text(left)
    right_norm = normalize_text(right)
    if not left_norm and not right_norm:
        return 1.0
    if not left_norm or not right_norm:
        return 0.0
    return round(SequenceMatcher(None, left_norm, right_norm).ratio(), 6)


def role_name(row: dict[str, Any]) -> str:
    source = str(row.get("source_track") or "").lower()
    label = str(row.get("speaker_label") or row.get("role") or "").lower()
    if source == "mic" or label == "me":
        return "Me"
    if source == "remote" or "colleague" in label or label == "remote":
        return "Colleagues"
    return str(row.get("speaker_label") or row.get("role") or "Unknown")


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


def duration(row: dict[str, Any] | None) -> float:
    if not isinstance(row, dict):
        return 0.0
    return max(0.0, safe_float(row.get("end")) - safe_float(row.get("start")))


def build_overlaps(utterances: list[dict[str, Any]]) -> list[dict[str, Any]]:
    me_rows = [row for row in utterances if role_name(row) == "Me"]
    remote_rows = [row for row in utterances if role_name(row) == "Colleagues"]
    overlaps: list[dict[str, Any]] = []
    index = 1
    for me in me_rows:
        for remote in remote_rows:
            overlap_start = max(safe_float(me.get("start")), safe_float(remote.get("start")))
            overlap_end = min(safe_float(me.get("end")), safe_float(remote.get("end")))
            overlap_duration = max(0.0, overlap_end - overlap_start)
            if overlap_duration <= 0:
                continue
            overlaps.append(
                {
                    "id": f"ov_{index:06d}",
                    "start": round(overlap_start, 3),
                    "end": round(overlap_end, 3),
                    "duration_sec": round(overlap_duration, 3),
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


def shadow_rows(report: dict[str, Any], sessions: list[Path]) -> list[dict[str, Any]]:
    rows = report.get("sessions") if isinstance(report.get("sessions"), list) else []
    selected = [row for row in rows if isinstance(row, dict)]
    if not sessions:
        return selected
    filters = {session.as_posix() for session in sessions} | {str(session) for session in sessions} | {session.name for session in sessions}
    return [
        row
        for row in selected
        if str(row.get("session") or "") in filters
        or str(row.get("session_id") or "") in filters
        or f"sessions/{row.get('session_id')}" in filters
    ]


def session_path(row: dict[str, Any]) -> Path:
    session = str(row.get("session") or "").strip()
    if session:
        return Path(session)
    return Path("sessions") / str(row.get("session_id"))


def dropped_rows(session: Path) -> list[dict[str, Any]]:
    path = session / "derived/transcript-simple/whisper-cpp/review-decisions" / f"review_decisions_applied.{SUGGESTED_PROFILE}.jsonl"
    return [
        row
        for row in read_jsonl(path)
        if row.get("decision") == "drop_me" and str(row.get("source") or "") != "local_recall"
    ]


def decision_utterance_id(row: dict[str, Any]) -> str:
    value = str(row.get("utterance_id") or "")
    if value:
        return value
    values = row.get("me_utterance_ids")
    if isinstance(values, list) and values:
        return str(values[0])
    return ""


def patch_from_decision(index: int, row: dict[str, Any], by_id: dict[str, dict[str, Any]], output_profile: str) -> dict[str, Any]:
    utterance_id = decision_utterance_id(row)
    target = by_id.get(utterance_id, {})
    label = str(row.get("label") or "")
    action = "drop_me_noise" if label == "asr_noise" else "drop_me_duplicate"
    remote = {}
    texts = row.get("text") if isinstance(row.get("text"), list) else []
    for item in texts:
        if isinstance(item, dict) and str(item.get("source_track") or "").lower() == "remote":
            remote = item
            break
    return {
        "schema": "murmurmark.audit_cleanup_patch/v1",
        "patch_id": f"patch_{index:06d}",
        "status": "applied",
        "action": action,
        "reason": "suggested_review_shadow_candidate",
        "input_profile": row.get("input_profile"),
        "output_profile": output_profile,
        "target": {
            "utterance_id": utterance_id,
            "role": "Me",
            "start": target.get("start"),
            "end": target.get("end"),
            "text": target.get("text"),
        },
        "matched_remote": {
            "utterance_id": remote.get("id"),
            "start": remote.get("start"),
            "end": remote.get("end"),
            "text": remote.get("text"),
        },
        "audit_overlap_ids": [row.get("source_audit_id")],
        "evidence": {
            "source": "suggested_review_shadow",
            "label": label,
            "verdict": row.get("verdict"),
            "classification_confidence": row.get("confidence"),
            "review_features": row.get("review_features"),
            "suggested_decision_reason": row.get("suggested_decision_reason"),
            "review_lane": row.get("review_lane"),
        },
        "safety_checks": {
            "shadow_assessment_required": sorted(CANDIDATE_ASSESSMENTS),
            "decision": row.get("decision"),
            "suggested_decision": row.get("suggested_decision"),
            "allowed_decisions": row.get("allowed_decisions"),
            "safe_to_drop_entire_utterance": True,
        },
    }


def update_quality(
    selected_quality: dict[str, Any],
    output_utterances: list[dict[str, Any]],
    overlaps: list[dict[str, Any]],
    patches: list[dict[str, Any]],
    output_profile: str,
    shadow_row: dict[str, Any],
) -> dict[str, Any]:
    quality = copy.deepcopy(selected_quality)
    quality["profile"] = output_profile
    quality["utterances"] = len(output_utterances)
    quality["needs_review_count"] = sum(
        1 for row in output_utterances if isinstance(row.get("quality"), dict) and row["quality"].get("needs_review")
    )
    quality["needs_review_ratio"] = round(quality["needs_review_count"] / max(1, len(output_utterances)), 6)
    quality["cross_role_overlap_count"] = len(overlaps)
    quality["cross_role_overlap_seconds"] = round(sum(safe_float(row.get("duration_sec")) for row in overlaps), 3)
    quality["cross_role_overlap_gt2_count"] = sum(1 for row in overlaps if safe_float(row.get("duration_sec")) > 2.0)
    quality["cross_role_overlap_gt2_seconds"] = round(
        sum(safe_float(row.get("duration_sec")) for row in overlaps if safe_float(row.get("duration_sec")) > 2.0),
        3,
    )
    duplicate_overlaps = [row for row in overlaps if safe_float(row.get("text_similarity")) >= 0.65]
    quality["remote_duplicate_in_me_count"] = len(duplicate_overlaps)
    quality["remote_duplicate_in_me_seconds"] = round(sum(safe_float(row.get("duration_sec")) for row in duplicate_overlaps), 3)
    quality["meeting_duration_sec"] = round(max((safe_float(row.get("end")) for row in output_utterances), default=0.0), 3)

    duplicate_seconds = round(sum(duration(patch.get("target")) for patch in patches if patch.get("action") == "drop_me_duplicate"), 3)
    noise_seconds = round(sum(duration(patch.get("target")) for patch in patches if patch.get("action") == "drop_me_noise"), 3)
    previous = selected_quality.get("audit_cleanup") if isinstance(selected_quality.get("audit_cleanup"), dict) else {}
    before = safe_float(previous.get("audit_harmful_seconds_before", selected_quality.get("audit_harmful_seconds_before")))
    previous_after = safe_float(previous.get("audit_harmful_seconds_after", selected_quality.get("audit_harmful_seconds_after")))
    cleanup = copy.deepcopy(previous)
    cleanup.update(
        {
            "profile": output_profile,
            "applied_patches": int(previous.get("applied_patches", 0) or 0) + len(patches),
            "suggested_cleanup_applied_patches": len(patches),
            "suggested_cleanup_source_profile": SUGGESTED_PROFILE,
            "suggested_cleanup_assessment": shadow_row.get("assessment"),
            "dropped_me_duplicate_seconds": round(safe_float(previous.get("dropped_me_duplicate_seconds")) + duplicate_seconds, 3),
            "dropped_me_noise_seconds": round(safe_float(previous.get("dropped_me_noise_seconds")) + noise_seconds, 3),
            "audit_harmful_seconds_before": round(before, 3),
            "audit_harmful_seconds_after": round(max(0.0, previous_after - duplicate_seconds - noise_seconds), 3),
            "audit_benign_seconds": previous.get("audit_benign_seconds", selected_quality.get("audit_benign_seconds")),
            "audit_review_seconds": previous.get("audit_review_seconds", selected_quality.get("audit_review_seconds")),
        }
    )
    quality["audit_cleanup"] = cleanup
    for key in (
        "applied_patches",
        "dropped_me_duplicate_seconds",
        "dropped_me_noise_seconds",
        "audit_harmful_seconds_before",
        "audit_harmful_seconds_after",
        "audit_benign_seconds",
        "audit_review_seconds",
        "protected_intentional_repeat_count",
    ):
        if key in cleanup:
            quality[key] = cleanup[key]
    quality["suggested_cleanup"] = {
        "schema": "murmurmark.suggested_cleanup_quality/v1",
        "source_profile": SUGGESTED_PROFILE,
        "shadow_assessment": shadow_row.get("assessment"),
        "shadow_flags": shadow_row.get("flags") or [],
        "added_needs_review_count": (shadow_row.get("needs_review_sources") or {}).get("added_needs_review_count"),
        "removed_needs_review_count": (shadow_row.get("needs_review_sources") or {}).get("removed_needs_review_count"),
    }
    return quality


def apply_session(row: dict[str, Any], output_profile: str) -> dict[str, Any]:
    session = session_path(row)
    selected_profile = str(row.get("selected_profile") or "")
    assessment = str(row.get("assessment") or "")
    output_suffix = suffix(output_profile)
    selected_suffix = suffix(selected_profile)
    resolved = session / "derived/transcript-simple/whisper-cpp/resolved"
    cleanup_dir = session / "derived/transcript-simple/whisper-cpp/audit-cleanup"
    report_dir = session / "derived/transcript-simple/whisper-cpp/review-decisions"

    if assessment not in CANDIDATE_ASSESSMENTS:
        return {"session": session.as_posix(), "session_id": session.name, "status": "skipped", "reason": f"assessment:{assessment}"}
    if (row.get("needs_review_sources") or {}).get("added_needs_review_count") not in {0, None}:
        return {"session": session.as_posix(), "session_id": session.name, "status": "skipped", "reason": "added_needs_review_items"}

    dialogue_path = resolved / f"clean_dialogue{selected_suffix}.json"
    quality_path = resolved / f"quality_report{selected_suffix}.json"
    report_path = resolved / f"transcribe_simple_report{selected_suffix}.json"
    dialogue = read_json(dialogue_path)
    selected_quality = read_json(quality_path)
    transcript_report = read_json(report_path) if report_path.exists() else {}
    utterances = [item for item in dialogue.get("utterances", []) if isinstance(item, dict)]
    by_id = {str(item.get("id")): item for item in utterances}

    decisions = dropped_rows(session)
    drop_ids = {decision_utterance_id(item) for item in decisions if decision_utterance_id(item) in by_id}
    if not drop_ids:
        return {"session": session.as_posix(), "session_id": session.name, "status": "skipped", "reason": "no_drop_decisions"}

    patches = [patch_from_decision(index + 1, decision, by_id, output_profile) for index, decision in enumerate(decisions) if decision_utterance_id(decision) in drop_ids]
    output_utterances = [copy.deepcopy(item) for item in utterances if str(item.get("id")) not in drop_ids]
    overlaps = build_overlaps(output_utterances)
    quality = update_quality(selected_quality, output_utterances, overlaps, patches, output_profile, row)

    hard_failures: list[str] = []
    if int(quality.get("unrepaired_long_mic_crossings_count", 0) or 0) != 0:
        hard_failures.append("unrepaired_long_mic_crossings")
    if int(quality.get("golden_phrase_fail_count", 0) or 0) != 0:
        hard_failures.append("golden_phrase_fail")
    if safe_float(quality.get("local_only_island_recall")) < 0.70:
        hard_failures.append("local_recall_below_failed_threshold")
    if safe_float(quality.get("remote_duplicate_in_me_seconds")) > safe_float((row.get("metrics") or {}).get("remote_duplicate_in_me_seconds", {}).get("selected")):
        hard_failures.append("remote_duplicate_increased")
    gates = {"passed": not hard_failures, "hard_failures": hard_failures, "warnings": []}
    if row.get("assessment") == "promising_cleanup_candidate_with_residual_review":
        gates["warnings"].append("residual_review_required")

    output_dialogue = {"schema": "murmurmark.clean_dialogue/v1", "session": dialogue.get("session", session.name), "utterances": output_utterances}
    simple_payload = {
        "schema": "murmurmark.transcript_simple/v1",
        "session": dialogue.get("session", session.name),
        "backend": "whisper.cpp",
        "utterances": [
            {**copy.deepcopy(item), "raw_text": item.get("text"), "corrected_text": item.get("text"), "corrections": []}
            for item in output_utterances
        ],
    }
    diff = {
        "schema": "murmurmark.audit_cleanup_diff/v1",
        "input_profile": selected_profile,
        "output_profile": output_profile,
        "removed_utterance_ids": sorted(drop_ids),
        "inserted_utterances": [],
        "modified_utterances": [],
    }
    report = {
        "schema": "murmurmark.audit_cleanup_report/v1",
        "input_profile": selected_profile,
        "output_profile": output_profile,
        "mode": "conservative",
        "generator": {"name": "apply-suggested-cleanup", "version": SCRIPT_VERSION},
        "inputs": {
            "shadow_report": rel(Path("sessions/_reports/suggested-review-shadow/suggested_review_shadow_report.json"), session),
            "suggested_review_report": rel(report_dir / f"review_decisions_report.{SUGGESTED_PROFILE}.json", session),
            "clean_dialogue": rel(dialogue_path, session),
            "quality_report": rel(quality_path, session),
        },
        "summary": {
            "input_utterances": len(utterances),
            "output_utterances": len(output_utterances),
            "applied_patches": len(patches),
            "rejected_patches": 0,
            "suggested_shadow_assessment": row.get("assessment"),
            "suggested_shadow_flags": row.get("flags") or [],
            "dropped_me_duplicate_seconds": quality["audit_cleanup"]["dropped_me_duplicate_seconds"],
            "dropped_me_noise_seconds": quality["audit_cleanup"]["dropped_me_noise_seconds"],
            "audit_harmful_seconds_before": quality["audit_cleanup"]["audit_harmful_seconds_before"],
            "audit_harmful_seconds_after": quality["audit_cleanup"]["audit_harmful_seconds_after"],
            "added_needs_review_count": (row.get("needs_review_sources") or {}).get("added_needs_review_count"),
            "removed_needs_review_count": (row.get("needs_review_sources") or {}).get("removed_needs_review_count"),
        },
        "gates": gates,
    }

    write_json(resolved / f"clean_dialogue{output_suffix}.json", output_dialogue)
    write_json(resolved / f"quality_report{output_suffix}.json", quality)
    write_json(resolved / f"overlaps{output_suffix}.json", {"schema": "murmurmark.transcript_overlaps/v1", "session": session.name, "overlaps": overlaps})
    write_json(resolved / f"transcript.simple{output_suffix}.json", simple_payload)
    write_markdown(resolved / f"transcript{output_suffix}.md", output_utterances, transcript_report.get("model"), transcript_report.get("language"))
    write_json(cleanup_dir / f"audit_cleanup_report{output_suffix}.json", report)
    write_jsonl(cleanup_dir / f"audit_cleanup_patches{output_suffix}.jsonl", patches)
    write_jsonl(cleanup_dir / f"audit_cleanup_rejected_patches{output_suffix}.jsonl", [])
    write_json(cleanup_dir / f"audit_cleanup_diff{output_suffix}.json", diff)

    return {
        "session": session.as_posix(),
        "session_id": session.name,
        "status": "applied" if gates["passed"] else "failed",
        "input_profile": selected_profile,
        "output_profile": output_profile,
        "applied_patches": len(patches),
        "removed_utterance_ids": sorted(drop_ids),
        "gates": gates,
        "remote_duplicate_in_me_seconds": quality.get("remote_duplicate_in_me_seconds"),
        "needs_review_count": quality.get("needs_review_count"),
        "report": rel(cleanup_dir / f"audit_cleanup_report{output_suffix}.json", session),
    }


def main() -> int:
    args = parse_args()
    shadow_report = read_json(args.shadow_report.expanduser())
    rows = shadow_rows(shadow_report, [session.expanduser() for session in args.sessions])
    results = [apply_session(row, args.output_profile) for row in rows]
    failed = [row for row in results if row.get("status") == "failed"]
    applied = [row for row in results if row.get("status") == "applied"]
    payload = {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": {"name": "apply-suggested-cleanup", "version": SCRIPT_VERSION},
        "inputs": {"shadow_report": args.shadow_report.as_posix(), "output_profile": args.output_profile},
        "summary": {
            "session_count": len(results),
            "applied_sessions": len(applied),
            "failed_sessions": len(failed),
            "skipped_sessions": sum(1 for row in results if row.get("status") == "skipped"),
            "applied_patches": sum(int(row.get("applied_patches", 0) or 0) for row in applied),
        },
        "sessions": results,
    }
    write_json(args.out.expanduser(), payload)
    print(f"written: {args.out}")
    print(f"applied_sessions: {payload['summary']['applied_sessions']}")
    print(f"failed_sessions: {payload['summary']['failed_sessions']}")
    print(f"applied_patches: {payload['summary']['applied_patches']}")
    return 0 if not failed else 2


if __name__ == "__main__":
    raise SystemExit(main())
