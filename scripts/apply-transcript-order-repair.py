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


SCRIPT_VERSION = "0.1.0"
INPUT_PROFILE_DEFAULT = "auto"
OUTPUT_PROFILE_DEFAULT = "order_repair_v1"
TOKEN_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё_./+-]+")
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
    "мы",
    "на",
    "не",
    "но",
    "ну",
    "по",
    "с",
    "то",
    "у",
    "что",
    "это",
    "я",
}
ACTION_DECISION_RISK_MARKERS = {
    "надо",
    "нужно",
    "давай",
    "давайте",
    "решили",
    "договорились",
    "согласовали",
    "риск",
    "проблема",
    "блокер",
    "вопрос",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply conservative transcript order repair from source ASR segments.")
    parser.add_argument("session", type=Path)
    parser.add_argument("--input-profile", default=INPUT_PROFILE_DEFAULT)
    parser.add_argument("--output-profile", default=OUTPUT_PROFILE_DEFAULT)
    parser.add_argument("--mode", choices=("dry-run", "conservative"), default="conservative")
    parser.add_argument("--long-me-sec", type=float, default=6.0)
    parser.add_argument("--tail-sec", type=float, default=0.8)
    parser.add_argument("--min-overlap-sec", type=float, default=0.5)
    parser.add_argument("--remote-guard-sec", type=float, default=0.2)
    return parser.parse_args()


def suffix(profile: str) -> str:
    return "" if profile == "current" else f".{profile}"


def display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(resolved)


def shell_path(path: Path) -> str:
    return shlex.quote(display_path(path))


def repair_handoff(session: Path, output_profile: str, report_path: Path, transcript_path: Path) -> dict[str, Any]:
    session_arg = shell_path(session)
    profile_arg = shlex.quote(output_profile)
    synthesize_command = f"murmurmark synthesize {session_arg} --transcript-profile {profile_arg}"
    transcript_command = f"murmurmark transcript {session_arg} --profile {profile_arg}"
    report_command = f"murmurmark report {session_arg}"
    return {
        "recommended_next": synthesize_command,
        "next_commands": [
            {
                "id": "synthesize_repair_profile",
                "command": synthesize_command,
                "reason": "build quality verdict and notes from the repair profile",
            },
            {
                "id": "open_repair_transcript",
                "command": transcript_command,
                "reason": "inspect the repair transcript through the CLI",
            },
            {
                "id": "refresh_session_report",
                "command": report_command,
                "reason": "refresh readiness after repair-derived synthesis",
            },
        ],
        "open_commands": [
            {
                "id": "open_repair_report",
                "command": f"less {shell_path(report_path)}",
                "path": display_path(report_path),
            },
            {
                "id": "open_repair_transcript",
                "command": f"less {shell_path(transcript_path)}",
                "path": display_path(transcript_path),
            },
        ],
    }


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        value = json.load(file)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def read_json_if_exists(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return read_json(path)
    except (OSError, ValueError, json.JSONDecodeError):
        return None


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


def safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def normalize_text(value: Any) -> str:
    text = str(value or "").lower().replace("ё", "е")
    text = re.sub(r"[^0-9a-zа-я_./+-]+", " ", text)
    return " ".join(text.split())


def text_similarity(left: Any, right: Any) -> float:
    left_text = normalize_text(left)
    right_text = normalize_text(right)
    if not left_text or not right_text:
        return 0.0
    return SequenceMatcher(None, left_text, right_text).ratio()


def tokens(text: Any) -> list[str]:
    return [token.lower().replace("ё", "е") for token in TOKEN_RE.findall(str(text or ""))]


def content_tokens(text: Any) -> list[str]:
    return [token for token in tokens(text) if token not in STOP_WORDS and len(token) > 2]


def has_protected_marker(text: Any) -> bool:
    return bool(set(tokens(text)) & ACTION_DECISION_RISK_MARKERS)


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


def interval_overlap(left: dict[str, Any], right: dict[str, Any]) -> float:
    start = max(safe_float(left.get("start")), safe_float(right.get("start")))
    end = min(safe_float(left.get("end")), safe_float(right.get("end")))
    return max(0.0, end - start)


def item_duration(item: dict[str, Any]) -> float:
    interval = item.get("interval") if isinstance(item.get("interval"), dict) else {}
    duration = safe_float(interval.get("duration_sec"))
    if duration > 0:
        return duration
    return max(0.0, safe_float(interval.get("end")) - safe_float(interval.get("start")))


def format_time(seconds: Any) -> str:
    total = max(0, int(safe_float(seconds)))
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def resolve_profile(session: Path, requested: str) -> str:
    if requested != "auto":
        return requested
    resolved = session / "derived/transcript-simple/whisper-cpp/resolved"
    for profile in (
        "agent_reviewed_v1",
        "reviewed_v1",
        "audit_cleanup_v6",
        "audit_cleanup_v5",
        "audit_cleanup_v4",
        "audit_cleanup_v3",
        "audit_cleanup_v2",
        "audit_cleanup_v1",
        "shadow_v2",
        "current",
    ):
        if (resolved / f"clean_dialogue{suffix(profile)}.json").exists():
            return profile
    return "current"


def payload_rows(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    rows = payload.get(key)
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def load_raw_segments(resolved: Path, profile: str) -> list[dict[str, Any]]:
    for path in (resolved / f"raw_segments{suffix(profile)}.json", resolved / "raw_segments.json"):
        payload = read_json_if_exists(path)
        if not payload:
            continue
        rows = payload_rows(payload, "segments")
        if rows:
            return rows
    return []


def load_candidates(resolved: Path, profile: str) -> dict[str, dict[str, Any]]:
    for path in (resolved / f"candidate_utterances{suffix(profile)}.json", resolved / "candidate_utterances.json"):
        payload = read_json_if_exists(path)
        if not payload:
            continue
        rows = payload_rows(payload, "candidates")
        if rows:
            return {str(row.get("id")): row for row in rows if row.get("id")}
    return {}


def load_existing_order_items(session: Path, profile: str) -> list[dict[str, Any]]:
    audit_dir = session / "derived/audit/order"
    audit = read_json_if_exists(audit_dir / "transcript_order_audit.json")
    if audit and str(audit.get("profile") or "") == profile:
        return read_jsonl(audit_dir / "transcript_order_items.jsonl")
    return []


def build_overlaps(utterances: list[dict[str, Any]]) -> list[dict[str, Any]]:
    me_rows = [row for row in utterances if is_me(row)]
    remote_rows = [row for row in utterances if role_name(row) == "Colleagues"]
    overlaps: list[dict[str, Any]] = []
    index = 1
    for me in me_rows:
        for remote in remote_rows:
            duration = interval_overlap(me, remote)
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
                    "left_utterance_id": me.get("id"),
                    "right_utterance_id": remote.get("id"),
                    "me_utterance_id": me.get("id"),
                    "remote_utterance_id": remote.get("id"),
                    "left_role": "Me",
                    "right_role": "Colleagues",
                    "text_similarity": round(text_similarity(me.get("text"), remote.get("text")), 6),
                    "type": "profile_recomputed",
                }
            )
            index += 1
    return overlaps


def derive_order_items(
    utterances: list[dict[str, Any]],
    overlaps: list[dict[str, Any]],
    args: argparse.Namespace,
) -> list[dict[str, Any]]:
    by_id = {str(row.get("id")): row for row in utterances if row.get("id")}
    items: list[dict[str, Any]] = []
    for overlap in overlaps:
        left = by_id.get(str(overlap.get("left_utterance_id") or overlap.get("me_utterance_id") or ""))
        right = by_id.get(str(overlap.get("right_utterance_id") or overlap.get("remote_utterance_id") or ""))
        if not left or not right:
            continue
        roles = {role_name(left), role_name(right)}
        if roles != {"Me", "Colleagues"}:
            continue
        me = left if is_me(left) else right
        remote = right if me is left else left
        me_start = safe_float(me.get("start"))
        me_end = safe_float(me.get("end"))
        remote_start = safe_float(remote.get("start"))
        remote_end = safe_float(remote.get("end"))
        overlap_sec = safe_float(overlap.get("duration_sec")) or interval_overlap(me, remote)
        me_duration = max(0.0, me_end - me_start)
        remote_duration = max(0.0, remote_end - remote_start)
        me_wraps_remote = me_start < remote_start and me_end > remote_end
        post_tail = max(0.0, me_end - remote_end)
        similarity = safe_float(overlap.get("text_similarity")) or text_similarity(me.get("text"), remote.get("text"))
        if similarity >= 0.70 or overlap_sec < args.min_overlap_sec:
            continue
        if me_duration >= args.long_me_sec and remote_duration >= 0.8 and me_wraps_remote and post_tail >= args.tail_sec:
            items.append(
                {
                    "schema": "murmurmark.transcript_order_item/v1",
                    "item_id": f"order_derived_{len(items) + 1:04d}",
                    "label": "probable_order_risk",
                    "confidence": 0.78,
                    "reason": "derived from clean dialogue overlap",
                    "interval": {
                        "start": round(max(me_start, remote_start), 3),
                        "end": round(min(me_end, remote_end), 3),
                        "duration_sec": round(overlap_sec, 3),
                    },
                    "utterances": {
                        "me": {"id": me.get("id"), "start": me_start, "end": me_end, "text": me.get("text")},
                        "remote": {"id": remote.get("id"), "start": remote_start, "end": remote_end, "text": remote.get("text")},
                    },
                    "features": {
                        "me_duration_sec": round(me_duration, 3),
                        "remote_duration_sec": round(remote_duration, 3),
                        "overlap_duration_sec": round(overlap_sec, 3),
                        "post_remote_tail_sec": round(post_tail, 3),
                        "me_wraps_remote": True,
                        "text_similarity": round(similarity, 6),
                    },
                }
            )
    return items


def candidate_segments_for_me(
    me: dict[str, Any],
    candidates: dict[str, dict[str, Any]],
    raw_by_id: dict[str, dict[str, Any]],
    raw_segments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    candidate = candidates.get(str(me.get("source_candidate_id") or ""))
    segment_ids = candidate.get("source_segments") if isinstance(candidate, dict) else None
    if isinstance(segment_ids, list) and segment_ids:
        rows = [raw_by_id[str(item)] for item in segment_ids if str(item) in raw_by_id]
    else:
        me_start = safe_float(me.get("start"))
        me_end = safe_float(me.get("end"))
        rows = [
            row
            for row in raw_segments
            if str(row.get("source_track") or "").lower() == "mic"
            and max(0.0, min(me_end, safe_float(row.get("end"))) - max(me_start, safe_float(row.get("start")))) > 0
        ]
    return sorted(rows, key=lambda row: (safe_float(row.get("start")), safe_float(row.get("end"))))


def segment_text(rows: list[dict[str, Any]]) -> str:
    text = " ".join(str(row.get("text") or "").strip() for row in rows if str(row.get("text") or "").strip())
    return " ".join(text.split())


def make_split_row(
    original: dict[str, Any],
    rows: list[dict[str, Any]],
    *,
    part: str,
    item: dict[str, Any],
    output_profile: str,
) -> dict[str, Any]:
    new_row = copy.deepcopy(original)
    new_row["id"] = f"{original.get('id')}__order_{part}_1"
    new_row["start"] = round(min(safe_float(row.get("start")) for row in rows), 3)
    new_row["end"] = round(max(safe_float(row.get("end")) for row in rows), 3)
    new_row["source_start"] = new_row["start"]
    new_row["source_end"] = new_row["end"]
    new_row["text"] = segment_text(rows)
    new_row["source_segments"] = [row.get("id") for row in rows if row.get("id")]
    quality = copy.deepcopy(new_row.get("quality")) if isinstance(new_row.get("quality"), dict) else {}
    quality["transcript_order_repair"] = {
        "profile": output_profile,
        "status": "applied",
        "action": "split_me_from_source_segments",
        "part": part,
        "source_audit_ids": [item.get("item_id")],
        "original_utterance_id": original.get("id"),
        "remote_utterance_id": ((item.get("utterances") or {}).get("remote") or {}).get("id"),
    }
    new_row["quality"] = quality
    return new_row


def mark_needs_review(row: dict[str, Any], *, item: dict[str, Any], output_profile: str, reason: str) -> dict[str, Any]:
    new_row = copy.deepcopy(row)
    quality = copy.deepcopy(new_row.get("quality")) if isinstance(new_row.get("quality"), dict) else {}
    quality["needs_review"] = True
    quality["transcript_order_repair"] = {
        "profile": output_profile,
        "status": "needs_review",
        "reason": reason,
        "source_audit_ids": [item.get("item_id")],
        "remote_utterance_id": ((item.get("utterances") or {}).get("remote") or {}).get("id"),
    }
    new_row["quality"] = quality
    return new_row


def attempt_repair(
    *,
    me: dict[str, Any],
    remote: dict[str, Any],
    item: dict[str, Any],
    candidates: dict[str, dict[str, Any]],
    raw_by_id: dict[str, dict[str, Any]],
    raw_segments: list[dict[str, Any]],
    args: argparse.Namespace,
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    source_segments = candidate_segments_for_me(me, candidates, raw_by_id, raw_segments)
    remote_start = safe_float(remote.get("start"))
    remote_end = safe_float(remote.get("end"))
    guard = args.remote_guard_sec
    if len(source_segments) < 2:
        return "needs_review", [], {"reason": "missing_or_unsplittable_source_segments", "source_segment_count": len(source_segments)}

    pre = [row for row in source_segments if (safe_float(row.get("start")) + safe_float(row.get("end"))) / 2 < remote_start - guard]
    post = [row for row in source_segments if (safe_float(row.get("start")) + safe_float(row.get("end"))) / 2 > remote_end + guard]
    middle = [row for row in source_segments if row not in pre and row not in post]
    pre_text = segment_text(pre)
    post_text = segment_text(post)
    middle_text = segment_text(middle)
    if not pre_text or not post_text:
        return "needs_review", [], {"reason": "missing_pre_or_post_local_island", "pre_text": pre_text, "post_text": post_text}

    original_tokens = set(content_tokens(me.get("text")))
    selected_tokens = set(content_tokens(f"{pre_text} {post_text}"))
    coverage = len(original_tokens & selected_tokens) / max(1, len(original_tokens))
    dropped_unique = sorted(set(content_tokens(middle_text)) - set(content_tokens(remote.get("text"))))
    if coverage < 0.35:
        return "needs_review", [], {"reason": "selected_segments_cover_too_little_text", "content_token_coverage": round(coverage, 6)}
    if has_protected_marker(middle_text) and len(dropped_unique) > 0:
        return "needs_review", [], {"reason": "overlap_segment_has_protected_marker", "dropped_unique_tokens": dropped_unique[:12]}
    if len(dropped_unique) > 2:
        return "needs_review", [], {"reason": "overlap_segment_has_unique_me_content", "dropped_unique_tokens": dropped_unique[:12]}

    rows = [
        make_split_row(me, pre, part="pre", item=item, output_profile=args.output_profile),
        make_split_row(me, post, part="post", item=item, output_profile=args.output_profile),
    ]
    return (
        "applied",
        rows,
        {
            "reason": "split_from_source_segments",
            "source_segment_ids": [row.get("id") for row in source_segments if row.get("id")],
            "dropped_overlap_segment_ids": [row.get("id") for row in middle if row.get("id")],
            "dropped_overlap_text": middle_text,
            "content_token_coverage": round(coverage, 6),
        },
    )


def quality_report(
    input_quality: dict[str, Any],
    utterances: list[dict[str, Any]],
    overlaps: list[dict[str, Any]],
    repair_summary: dict[str, Any],
) -> dict[str, Any]:
    report = copy.deepcopy(input_quality)
    report["schema"] = "murmurmark.simple_transcript_quality/v1"
    report["utterances"] = len(utterances)
    report["needs_review_count"] = sum(1 for row in utterances if isinstance(row.get("quality"), dict) and row["quality"].get("needs_review"))
    report["needs_review_ratio"] = round(report["needs_review_count"] / max(1, len(utterances)), 6)
    report["cross_role_overlap_count"] = len(overlaps)
    report["cross_role_overlap_seconds"] = round(sum(safe_float(row.get("duration_sec")) for row in overlaps), 3)
    report["cross_role_overlap_gt2_count"] = sum(1 for row in overlaps if safe_float(row.get("duration_sec")) > 2.0)
    report["cross_role_overlap_gt2_seconds"] = round(
        sum(safe_float(row.get("duration_sec")) for row in overlaps if safe_float(row.get("duration_sec")) > 2.0),
        3,
    )
    duplicate_overlaps = [row for row in overlaps if safe_float(row.get("text_similarity")) >= 0.65]
    report["remote_duplicate_in_me_count"] = len(duplicate_overlaps)
    report["remote_duplicate_in_me_seconds"] = round(sum(safe_float(row.get("duration_sec")) for row in duplicate_overlaps), 3)
    report["meeting_duration_sec"] = round(max((safe_float(row.get("end")) for row in utterances), default=0.0), 3)
    report["transcript_order_repair"] = repair_summary
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
        lines.extend([f"## {format_time(row.get('start'))} {role_name(row)}", "", str(row.get("text") or "").strip(), ""])
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    session = args.session.expanduser().resolve()
    resolved = session / "derived/transcript-simple/whisper-cpp/resolved"
    repair_dir = session / "derived/transcript-simple/whisper-cpp/order-repair"
    input_profile = resolve_profile(session, args.input_profile)
    input_suffix = suffix(input_profile)
    output_suffix = suffix(args.output_profile)
    report_path = repair_dir / f"transcript_order_repair_report{output_suffix}.json"
    transcript_path = resolved / f"transcript{output_suffix}.md"

    dialogue_path = resolved / f"clean_dialogue{input_suffix}.json"
    quality_path = resolved / f"quality_report{input_suffix}.json"
    if not dialogue_path.exists() or not quality_path.exists():
        report = {
            "schema": "murmurmark.transcript_order_repair_report/v1",
            "generator": {"name": "apply-transcript-order-repair", "version": SCRIPT_VERSION},
            "input_profile": input_profile,
            "output_profile": args.output_profile,
            "status": "missing_inputs",
            "inputs": {"clean_dialogue": str(dialogue_path), "quality_report": str(quality_path)},
            "gates": {"passed": False, "hard_failures": ["missing_inputs"], "warnings": []},
            "summary": {},
            **repair_handoff(session, args.output_profile, report_path, transcript_path),
        }
        write_json(report_path, report)
        print(f"order_repair_report: {report_path}")
        print("status: missing_inputs")
        return 2

    dialogue = read_json(dialogue_path)
    input_quality = read_json(quality_path)
    transcript_report = read_json_if_exists(resolved / f"transcribe_simple_report{input_suffix}.json") or read_json_if_exists(resolved / "transcribe_simple_report.json") or {}
    utterances = [row for row in dialogue.get("utterances", []) if isinstance(row, dict)]
    by_id = {str(row.get("id")): row for row in utterances if row.get("id")}
    overlaps_payload = read_json_if_exists(resolved / f"overlaps{input_suffix}.json") or {}
    overlaps = payload_rows(overlaps_payload, "overlaps")
    items = [row for row in load_existing_order_items(session, input_profile) if row.get("label") == "probable_order_risk"]
    if not items:
        items = derive_order_items(utterances, overlaps, args)

    items_by_me: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        me_id = str((((item.get("utterances") or {}).get("me") or {}).get("id")) or "")
        if me_id:
            items_by_me.setdefault(me_id, []).append(item)

    raw_segments = load_raw_segments(resolved, input_profile)
    raw_by_id = {str(row.get("id")): row for row in raw_segments if row.get("id")}
    candidates = load_candidates(resolved, input_profile)
    replacements: dict[str, list[dict[str, Any]]] = {}
    marks: dict[str, tuple[dict[str, Any], str]] = {}
    patches: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    for me_id, me_items in items_by_me.items():
        me = by_id.get(me_id)
        if not me:
            rejected.append({"me_utterance_id": me_id, "reason": "me_utterance_not_found", "items": me_items})
            continue
        if len(me_items) != 1:
            marks[me_id] = (me_items[0], "multiple_order_risks_for_one_me_utterance")
            rejected.append({"me_utterance_id": me_id, "reason": "multiple_order_risks_for_one_me_utterance", "items": me_items})
            continue
        item = me_items[0]
        remote_id = str((((item.get("utterances") or {}).get("remote") or {}).get("id")) or "")
        remote = by_id.get(remote_id)
        if not remote:
            marks[me_id] = (item, "remote_utterance_not_found")
            rejected.append({"me_utterance_id": me_id, "source_audit_id": item.get("item_id"), "reason": "remote_utterance_not_found"})
            continue
        status, rows, meta = attempt_repair(
            me=me,
            remote=remote,
            item=item,
            candidates=candidates,
            raw_by_id=raw_by_id,
            raw_segments=raw_segments,
            args=args,
        )
        if status == "applied" and args.mode == "conservative":
            replacements[me_id] = rows
            patches.append(
                {
                    "schema": "murmurmark.transcript_order_repair_patch/v1",
                    "source_audit_id": item.get("item_id"),
                    "action": "split_me_from_source_segments",
                    "status": "applied",
                    "me_utterance_id": me_id,
                    "remote_utterance_id": remote_id,
                    "created_utterance_ids": [row.get("id") for row in rows],
                    "meta": meta,
                }
            )
        else:
            marks[me_id] = (item, str(meta.get("reason") or "not_repairable"))
            rejected.append(
                {
                    "schema": "murmurmark.transcript_order_repair_rejection/v1",
                    "source_audit_id": item.get("item_id"),
                    "me_utterance_id": me_id,
                    "remote_utterance_id": remote_id,
                    "reason": str(meta.get("reason") or "not_repairable"),
                    "meta": meta,
                }
            )

    output_utterances: list[dict[str, Any]] = []
    for row in utterances:
        row_id = str(row.get("id") or "")
        if row_id in replacements:
            output_utterances.extend(replacements[row_id])
            continue
        if row_id in marks:
            item, reason = marks[row_id]
            output_utterances.append(mark_needs_review(row, item=item, output_profile=args.output_profile, reason=reason))
            continue
        output_utterances.append(copy.deepcopy(row))

    output_utterances.sort(key=lambda row: (safe_float(row.get("start")), safe_float(row.get("end")), str(row.get("id") or "")))
    output_overlaps = build_overlaps(output_utterances)
    repaired_item_ids = {str(patch.get("source_audit_id")) for patch in patches if patch.get("source_audit_id")}
    repaired_seconds = round(sum(item_duration(item) for item in items if str(item.get("item_id")) in repaired_item_ids), 3)
    unrepaired_items = [item for item, _reason in marks.values()]
    unrepaired_seconds = round(sum(item_duration(item) for item in unrepaired_items), 3)
    repair_summary = {
        "schema": "murmurmark.transcript_order_repair_summary/v1",
        "input_profile": input_profile,
        "output_profile": args.output_profile,
        "mode": args.mode,
        "order_risk_items": len(items),
        "applied_repairs": len(patches),
        "split_utterances_created": sum(len(rows) for rows in replacements.values()),
        "removed_original_me_utterances": len(replacements),
        "marked_needs_review": len(marks),
        "unrepaired_order_risks": len(marks),
        "repaired_order_risk_seconds": repaired_seconds,
        "marked_needs_review_seconds": unrepaired_seconds,
        "unrepaired_order_risk_seconds": unrepaired_seconds,
        "overlap_gt2_seconds_before": round(sum(safe_float(row.get("duration_sec")) for row in overlaps if safe_float(row.get("duration_sec")) > 2.0), 3),
        "overlap_gt2_seconds_after": round(sum(safe_float(row.get("duration_sec")) for row in output_overlaps if safe_float(row.get("duration_sec")) > 2.0), 3),
    }
    output_quality = quality_report(input_quality, output_utterances, output_overlaps, repair_summary)
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
    gates = {
        "passed": True,
        "hard_failures": [],
        "warnings": (
            (["partial_order_repair_needs_review"] if repair_summary["unrepaired_order_risks"] else [])
            + ([] if patches else ["no_order_repairs_applied"])
        ),
    }
    report = {
        "schema": "murmurmark.transcript_order_repair_report/v1",
        "generator": {"name": "apply-transcript-order-repair", "version": SCRIPT_VERSION},
        "input_profile": input_profile,
        "output_profile": args.output_profile,
        "status": "ok",
        "inputs": {
            "clean_dialogue": str(dialogue_path),
            "quality_report": str(quality_path),
            "raw_segments": f"raw_segments{input_suffix}.json or raw_segments.json",
            "candidate_utterances": f"candidate_utterances{input_suffix}.json or candidate_utterances.json",
        },
        "parameters": {
            "long_me_sec": args.long_me_sec,
            "tail_sec": args.tail_sec,
            "min_overlap_sec": args.min_overlap_sec,
            "remote_guard_sec": args.remote_guard_sec,
        },
        "summary": repair_summary,
        "gates": gates,
        **repair_handoff(session, args.output_profile, report_path, transcript_path),
    }

    write_json(resolved / f"clean_dialogue{output_suffix}.json", output_dialogue)
    write_json(resolved / f"quality_report{output_suffix}.json", output_quality)
    write_json(resolved / f"overlaps{output_suffix}.json", {"schema": "murmurmark.transcript_overlaps/v1", "session": session.name, "overlaps": output_overlaps})
    write_json(resolved / f"transcript.simple{output_suffix}.json", simple_payload)
    write_markdown(
        transcript_path,
        output_utterances,
        transcript_report.get("model"),
        transcript_report.get("language"),
    )
    write_json(report_path, report)
    write_jsonl(repair_dir / f"transcript_order_repair_patches{output_suffix}.jsonl", patches)
    write_jsonl(repair_dir / f"transcript_order_repair_rejected{output_suffix}.jsonl", rejected)

    print(f"order_repair_report: {report_path}")
    print(f"output_profile: {args.output_profile}")
    print(f"applied_repairs: {repair_summary['applied_repairs']}")
    print(f"unrepaired_order_risks: {repair_summary['unrepaired_order_risks']}")
    return 0 if gates["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
