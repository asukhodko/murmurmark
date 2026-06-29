#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_VERSION = "0.7.0"
SCHEMA = "murmurmark.review_lane_pack/v1"
KNOWN_REVIEW_DECISIONS = {"drop_me", "drop_remote", "keep_me", "needs_review", "skip"}
DEFAULT_ALLOWED_DECISIONS = {"drop_me", "keep_me", "needs_review", "skip"}
GROUPABLE_REVIEW_LANES = {"check_transcript_order", "check_unique_me_content", "classify_audio"}
DECISION_SHORTCUTS = {
    "drop_me": "d",
    "drop_remote": "c",
    "keep_me": "k",
    "needs_review": "r",
    "skip": "s",
    "todo": ".",
    "": ".",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build one listening WAV from a MurmurMark review lane.")
    parser.add_argument(
        "--template",
        type=Path,
        default=Path("sessions/_reports/review-plan/review_decisions.template.jsonl"),
        help="Input review_decisions.template.jsonl.",
    )
    parser.add_argument(
        "--decisions",
        type=Path,
        default=Path("sessions/_reports/review-plan/review_decisions.jsonl"),
        help="Optional existing decisions JSONL. Reviewed rows are skipped unless --include-reviewed is set.",
    )
    parser.add_argument("--lane", default="fast_confirm_drop", help="review_lane to pack.")
    parser.add_argument("--session", action="append", default=[], help="Optional session id/path filter. Can be repeated.")
    parser.add_argument("--command-key", default="stereo_clean_left_remote_right", help="Preferred command key.")
    parser.add_argument("--out-dir", type=Path, default=Path("sessions/_reports/review-plan/lane-packs"))
    parser.add_argument("--silence-sec", type=float, default=0.5, help="Silence inserted between clips.")
    parser.add_argument("--include-reviewed", action="store_true", help="Include rows that already have a non-todo decision.")
    parser.add_argument(
        "--group-related",
        choices=["auto", "off"],
        default="auto",
        help="Group related review rows into one pack item when safe. Groups selected lanes by Me utterance.",
    )
    return parser.parse_args()


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


def display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(resolved)


def shell_path(path: Path) -> str:
    return shlex.quote(display_path(path))


def command_item(item_id: str, command: str, reason: str) -> dict[str, str]:
    return {"id": item_id, "command": command, "reason": reason}


def review_lane_handoff(
    *,
    lane: str,
    audio_path: Path,
    manifest_path: Path,
    md_path: Path,
    answer_sheet_path: Path,
    suggested_answer_sheet_path: Path,
    template_path: Path,
    decisions_path: Path,
) -> dict[str, Any]:
    lane_arg = shlex.quote(lane)
    manual_apply_base = (
        f"murmurmark review lane apply {lane_arg} "
        f"--manifest {shell_path(manifest_path)} "
        f"--template {shell_path(template_path)} "
        f"--decisions-out {shell_path(decisions_path)} "
        f"--answers-file {shell_path(answer_sheet_path)}"
    )
    suggested_apply_base = (
        f"murmurmark review lane apply {lane_arg} "
        f"--manifest {shell_path(manifest_path)} "
        f"--template {shell_path(template_path)} "
        f"--decisions-out {shell_path(decisions_path)} "
        "--answers-source suggested"
    )
    next_commands = [
        command_item("listen_review_lane_pack", f"afplay {shell_path(audio_path)}", "listen to the review lane audio pack"),
        command_item("open_review_lane_pack", f"less {shell_path(md_path)}", "inspect review lane evidence"),
        command_item("edit_review_lane_answers", f"$EDITOR {shell_path(answer_sheet_path)}", "fill manual review decisions"),
        command_item("dry_run_review_lane_answers", f"{manual_apply_base} --dry-run", "validate manual decisions before applying"),
        command_item("apply_review_lane_answers", manual_apply_base, "apply manual decisions to review_decisions.jsonl"),
        command_item(
            "dry_run_suggested_review_answers",
            f"{suggested_apply_base} --dry-run",
            "validate generated suggestions before applying",
        ),
        command_item("apply_suggested_review_answers", suggested_apply_base, "apply generated suggestions"),
    ]
    open_commands = [
        command_item("listen_review_lane_pack", f"afplay {shell_path(audio_path)}", "listen to the review lane audio pack"),
        command_item("open_review_lane_pack", f"less {shell_path(md_path)}", "inspect review lane evidence"),
        command_item("open_review_lane_manifest", f"less {shell_path(manifest_path)}", "inspect review lane manifest"),
        command_item("edit_review_lane_answers", f"$EDITOR {shell_path(answer_sheet_path)}", "fill manual review decisions"),
        command_item(
            "open_suggested_review_answers",
            f"less {shell_path(suggested_answer_sheet_path)}",
            "inspect generated suggested answers",
        ),
    ]
    return {
        "recommended_next": next_commands[0]["command"],
        "next_commands": next_commands,
        "open_commands": open_commands,
        "manual_flow": {
            "dry_run": f"{manual_apply_base} --dry-run",
            "apply": manual_apply_base,
        },
        "suggested_flow": {
            "dry_run": f"{suggested_apply_base} --dry-run",
            "apply": suggested_apply_base,
        },
        "after_apply": ["murmurmark review progress", "murmurmark review apply"],
    }


def normalize_text(value: Any) -> str:
    return " ".join(str(value or "").lower().replace("ё", "е").split())


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


def merge_existing(template_rows: list[dict[str, Any]], existing_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    existing_by_key = {review_row_key(row): row for row in existing_rows}
    return [{**row, **existing_by_key.get(review_row_key(row), {})} for row in template_rows]


def undecided(row: dict[str, Any]) -> bool:
    return str(row.get("decision") or "todo") in {"", "todo"}


def session_aliases(*values: Any) -> set[str]:
    aliases: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        aliases.add(text)
        if text.startswith("./"):
            aliases.add(text[2:])
        path = Path(text)
        name = path.name
        if name:
            aliases.add(name)
            aliases.add(f"sessions/{name}")
            aliases.add(f"./sessions/{name}")
    return aliases


def session_matches(row: dict[str, Any], filters: set[str]) -> bool:
    if not filters:
        return True
    row_aliases = session_aliases(row.get("session"), row.get("session_id"))
    filter_aliases = set().union(*(session_aliases(item) for item in filters))
    return bool(row_aliases & filter_aliases)


def command_for_row(row: dict[str, Any], preferred_key: str) -> tuple[str, str] | None:
    commands = row.get("commands") if isinstance(row.get("commands"), dict) else {}
    for key in (
        preferred_key,
        "stereo_clean_left_remote_right",
        "stereo_mic_left_remote_right",
        "mic_raw",
        "remote",
        "mic_clean",
        "mic_role_masked",
    ):
        command = commands.get(key)
        if command:
            return key, str(command)
    return None


def list_values(row: dict[str, Any], key: str) -> list[str]:
    values = row.get(key)
    if isinstance(values, list):
        return [str(value) for value in values if value is not None and str(value)]
    return []


def first_me_utterance_id(row: dict[str, Any]) -> str:
    me_ids = list_values(row, "me_utterance_ids")
    if me_ids:
        return me_ids[0]
    utterance_ids = list_values(row, "utterance_ids")
    return utterance_ids[0] if utterance_ids else ""


def me_utterance_group_key(row: dict[str, Any]) -> str:
    me_ids = list_values(row, "me_utterance_ids")
    if me_ids:
        return ",".join(me_ids)
    return first_me_utterance_id(row)


def related_group_key(row: dict[str, Any], mode: str) -> str:
    if mode != "auto":
        return ""
    lane = str(row.get("review_lane") or "")
    if lane not in GROUPABLE_REVIEW_LANES:
        return ""
    me_key = me_utterance_group_key(row)
    if not me_key:
        return ""
    session_id = str(row.get("session_id") or row.get("session") or "")
    action = str(row.get("review_action") or "")
    if lane == "check_unique_me_content":
        return f"{lane}:{session_id}:{action}:{me_key}"
    label = str(row.get("label") or "")
    allowed = ",".join(sorted(allowed_decisions_for_item(row)))
    return f"{lane}:{session_id}:{label}:{action}:{allowed}:{me_key}"


def group_selected_rows(rows: list[dict[str, Any]], mode: str) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    by_key: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        key = related_group_key(row, mode)
        if not key:
            groups.append([row])
            continue
        group = by_key.get(key)
        if group is None:
            group = []
            by_key[key] = group
            groups.append(group)
        group.append(row)
    return groups


def unique_from_rows(rows: list[dict[str, Any]], key: str) -> list[str]:
    values: list[str] = []
    for row in rows:
        values.extend(list_values(row, key))
    return list(dict.fromkeys(values))


def common_allowed_decisions(rows: list[dict[str, Any]]) -> list[str]:
    allowed_sets = [allowed_decisions_for_item(row) for row in rows]
    if not allowed_sets:
        return sorted(DEFAULT_ALLOWED_DECISIONS)
    common = set.intersection(*allowed_sets)
    return sorted(common or allowed_sets[0])


def common_value(rows: list[dict[str, Any]], key: str, default: Any = None) -> Any:
    values = [row.get(key) for row in rows]
    if not values:
        return default
    first = values[0]
    return first if all(value == first for value in values) else default


def group_suggested_decision(rows: list[dict[str, Any]]) -> str:
    decision = common_value(rows, "suggested_decision", "todo")
    allowed = set(common_allowed_decisions(rows))
    return str(decision or "todo") if str(decision or "todo") in allowed else "todo"


def group_suggested_reason(rows: list[dict[str, Any]]) -> str:
    reason = common_value(rows, "suggested_decision_reason")
    if reason:
        return str(reason)
    if len(rows) <= 1:
        return str(rows[0].get("suggested_decision_reason") or "")
    return f"grouped_{len(rows)}_review_rows_for_same_me_utterance"


def group_clip_text(rows: list[dict[str, Any]]) -> str:
    if len(rows) == 1:
        return clip_text(rows[0])
    pieces: list[str] = []
    for index, row in enumerate(rows, start=1):
        source_id = row.get("source_audit_id") or f"row_{index}"
        text = clip_text(row)
        if text:
            pieces.append(f"{source_id}: {text}")
    return " || ".join(pieces)


def group_evidence_text(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    if len(rows) == 1:
        return evidence_text(rows[0])
    pieces: list[dict[str, str]] = []
    for index, row in enumerate(rows, start=1):
        source_id = str(row.get("source_audit_id") or f"row_{index}")
        for piece in evidence_text(row):
            role = piece.get("role") or "?"
            text = piece.get("text") or ""
            if text:
                pieces.append({"role": f"{index}:{source_id}:{role}", "text": text})
    return pieces


def feature_number(row: dict[str, Any], key: str) -> float | None:
    features = row.get("review_features") if isinstance(row.get("review_features"), dict) else {}
    value = features.get(key)
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def feature_bool(row: dict[str, Any], key: str) -> bool:
    features = row.get("review_features") if isinstance(row.get("review_features"), dict) else {}
    return bool(features.get(key))


def average_feature(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [value for row in rows if (value := feature_number(row, key)) is not None]
    if not values:
        return None
    return round(sum(values) / len(values), 6)


def row_feature_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    features = row.get("review_features") if isinstance(row.get("review_features"), dict) else {}
    interesting = (
        "me_overlap_coverage",
        "remote_overlap_coverage",
        "me_utterance_duration_sec",
        "remote_utterance_duration_sec",
        "text_similarity",
        "token_containment",
        "sequence_ratio",
        "likely_partial_me_utterance",
    )
    return {key: features[key] for key in interesting if key in features}


def review_hint_for_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    lane = str(common_value(rows, "review_lane", rows[0].get("review_lane") if rows else "") or "")
    labels = sorted({str(row.get("label") or "") for row in rows if row.get("label")})
    allowed = set(common_allowed_decisions(rows))
    grouped = len(rows) > 1

    focus = "Listen to the clip and choose only one of the allowed decisions."
    short_focus = "listen and choose allowed decision"
    why_review_required = "The current evidence is not strong enough for an automatic transcript edit."
    risk_factors: list[str] = []
    guide: list[dict[str, str]] = []

    if lane == "check_unique_me_content":
        focus = "Check whether the Me utterance contains unique local speech outside the remote overlap."
        short_focus = "unique Me content outside remote overlap"
        why_review_required = (
            "Dropping the whole Me utterance may remove real local speech; keep the risk explicit unless "
            "the clip proves it is only remote duplicate/noise."
        )
        guide = [
            {"decision": "keep_me", "when": "Me contains real local speech or a unique continuation."},
            {"decision": "drop_me", "when": "Me is only remote duplicate/noise and has no unique local content."},
            {"decision": "drop_remote", "when": "Allowed, and the remote utterance is the duplicate of local Me speech."},
            {"decision": "needs_review", "when": "Double-talk, garbled ASR or partial overlap makes the content ambiguous."},
        ]
        if "remote_duplicate" in labels:
            risk_factors.append("remote_duplicate evidence may cover only part of Me")
        if "remote_leak" in labels:
            risk_factors.append("remote_leak can still contain valid local speech")
    elif lane == "check_transcript_order":
        focus = "Verify chronology: whether Me and Colleagues are ordered correctly around the overlap."
        short_focus = "chronology around overlap"
        guide = [
            {"decision": "keep_me", "when": "The order risk is acceptable for this transcript."},
            {"decision": "needs_review", "when": "The utterance order could change meaning."},
            {"decision": "skip", "when": "Leave this row unresolved for a later pass."},
        ]
    elif lane == "check_local_recall":
        focus = "Check whether the candidate recovered a real missing Me phrase."
        short_focus = "possible missing Me phrase"
        guide = [
            {"decision": "keep_me", "when": "The inserted Me phrase is audible and belongs in the transcript."},
            {"decision": "drop_me", "when": "The phrase is not local speech or is ASR noise."},
            {"decision": "needs_review", "when": "The phrase is real but the wording/timing is uncertain."},
        ]
    elif lane == "fast_confirm_drop":
        focus = "Confirm that the suggested drop is a whole-utterance duplicate or ASR noise."
        short_focus = "confirm safe drop"
        guide = [
            {"decision": "drop_me", "when": "The whole Me utterance is duplicate/noise."},
            {"decision": "keep_me", "when": "Any local content is present."},
            {"decision": "needs_review", "when": "The evidence is not obvious."},
        ]
    elif lane == "classify_audio":
        focus = "Classify the audio evidence without editing transcript content."
        short_focus = "classify audio evidence"
        guide = [
            {"decision": "keep_me", "when": "The local speech is valid."},
            {"decision": "drop_me", "when": "The item is clearly duplicate/noise."},
            {"decision": "needs_review", "when": "The audio class is uncertain."},
        ]

    if grouped:
        risk_factors.append(f"grouped {len(rows)} review rows behind one answer")
    if any(feature_bool(row, "likely_partial_me_utterance") for row in rows):
        risk_factors.append("partial Me overlap; a whole-utterance drop is risky")
    mean_me_coverage = average_feature(rows, "me_overlap_coverage")
    if mean_me_coverage is not None and mean_me_coverage < 0.8 and "drop_me" in allowed:
        risk_factors.append(f"mean Me overlap coverage is {mean_me_coverage:.2f}")
    if "drop_remote" in allowed:
        risk_factors.append("drop_remote is available only when remote duplicates local Me speech")
    if not risk_factors:
        risk_factors.append("no automatic high-confidence edit available")

    return {
        "focus": focus,
        "short_focus": short_focus,
        "why_review_required": why_review_required,
        "risk_factors": risk_factors,
        "decision_guide": [item for item in guide if item["decision"] in allowed],
        "evidence_features": {
            "labels": labels,
            "mean_me_overlap_coverage": mean_me_coverage,
            "mean_remote_overlap_coverage": average_feature(rows, "remote_overlap_coverage"),
            "mean_text_similarity": average_feature(rows, "text_similarity"),
            "mean_token_containment": average_feature(rows, "token_containment"),
            "rows": [
                {
                    "source_audit_id": row.get("source_audit_id"),
                    "review_features": row_feature_snapshot(row),
                }
                for row in rows
            ],
        },
    }


def parse_play_command(command: str) -> dict[str, str] | None:
    tokens = shlex.split(command)
    if not tokens:
        return None
    input_path = ""
    start = ""
    duration = ""
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "-ss" and index + 1 < len(tokens):
            start = tokens[index + 1]
            index += 2
            continue
        if token == "-t" and index + 1 < len(tokens):
            duration = tokens[index + 1]
            index += 2
            continue
        index += 1
    for token in reversed(tokens):
        if token.startswith("-") or token in {"afplay", "ffplay"}:
            continue
        candidate = Path(token)
        if candidate.exists():
            input_path = str(candidate)
            break
    if not input_path:
        return None
    return {"input": input_path, "start": start, "duration": duration}


def render_command(command: str, out_path: Path) -> bool:
    parsed = parse_play_command(command)
    if not parsed:
        return False
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    if parsed.get("start"):
        cmd.extend(["-ss", parsed["start"]])
    cmd.extend(["-i", parsed["input"]])
    if parsed.get("duration"):
        cmd.extend(["-t", parsed["duration"]])
    cmd.extend(["-ac", "2", "-ar", "16000", "-sample_fmt", "s16", str(out_path)])
    completed = subprocess.run(cmd, check=False)
    return completed.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0


def probe_duration(path: Path) -> float:
    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ],
        check=False,
        text=True,
        capture_output=True,
    )
    if completed.returncode != 0:
        return 0.0
    try:
        value = json.loads(completed.stdout)
        return max(0.0, float(value.get("format", {}).get("duration") or 0.0))
    except (json.JSONDecodeError, TypeError, ValueError):
        return 0.0


def make_silence(path: Path, duration: float) -> bool:
    if duration <= 0:
        return False
    completed = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=16000",
            "-t",
            f"{duration:.3f}",
            "-sample_fmt",
            "s16",
            str(path),
        ],
        check=False,
    )
    return completed.returncode == 0 and path.exists() and path.stat().st_size > 0


def concat_escape(path: Path) -> str:
    return str(path).replace("'", "'\\''")


def concat_wavs(parts: list[Path], out_path: Path, list_path: Path) -> bool:
    if not parts:
        return make_silence(out_path, 0.01)
    list_path.write_text(
        "".join(f"file '{concat_escape(path)}'\n" for path in parts),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_path),
            "-c",
            "copy",
            str(out_path),
        ],
        check=False,
    )
    return completed.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0


def command_entries_for_group(
    rows: list[dict[str, Any]],
    preferred_key: str,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    entries: list[dict[str, Any]] = []
    for row in rows:
        command_choice = command_for_row(row, preferred_key)
        is_text_only = str(row.get("source") or "") == "transcript_order" and command_choice is None
        if command_choice is None and not is_text_only:
            return [], {
                "source_audit_id": row.get("source_audit_id"),
                "source_audit_ids": [item.get("source_audit_id") for item in rows],
                "reason": "missing_audio_command",
            }
        command_key, command = (
            command_choice if command_choice is not None else ("review", str((row.get("commands") or {}).get("review") or ""))
        )
        entries.append(
            {
                "row": row,
                "command_key": command_key,
                "command": command,
                "text_only": is_text_only,
            }
        )
    return entries, None


def render_group_clip(
    entries: list[dict[str, Any]],
    tmp_dir: Path,
    index: int,
    out_path: Path,
) -> tuple[bool, str]:
    if len(entries) == 1:
        entry = entries[0]
        rendered = (
            make_silence(out_path, 0.25)
            if entry.get("text_only")
            else render_command(str(entry.get("command") or ""), out_path)
        )
        return rendered, "render_failed"

    parts: list[Path] = []
    between_silence = tmp_dir / f"clip_{index:04d}_within_group_silence.wav"
    has_between_silence = make_silence(between_silence, 0.25)
    for sub_index, entry in enumerate(entries, start=1):
        child_wav = tmp_dir / f"clip_{index:04d}_{sub_index:02d}.wav"
        rendered = (
            make_silence(child_wav, 0.25)
            if entry.get("text_only")
            else render_command(str(entry.get("command") or ""), child_wav)
        )
        if not rendered:
            return False, "render_failed"
        parts.append(child_wav)
        if has_between_silence and sub_index < len(entries):
            parts.append(between_silence)
    return concat_wavs(parts, out_path, tmp_dir / f"clip_{index:04d}_concat.txt"), "render_failed"


def clip_text(row: dict[str, Any]) -> str:
    pieces: list[str] = []
    for item in row.get("text") or []:
        if not isinstance(item, dict):
            continue
        role = item.get("role") or item.get("source_track") or "?"
        text = " ".join(str(item.get("text") or "").split())
        if text:
            pieces.append(f"{role}: {text}")
    return " | ".join(pieces)


def evidence_text(row: dict[str, Any]) -> list[dict[str, str]]:
    pieces: list[dict[str, str]] = []
    for item in row.get("text") or []:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or item.get("source_track") or "?")
        text = " ".join(str(item.get("text") or "").split())
        if text:
            pieces.append({"role": role, "text": text})
    return pieces


def format_time(seconds: float) -> str:
    total = max(0, int(seconds))
    minutes = total // 60
    secs = total % 60
    millis = int(round((seconds - int(seconds)) * 1000))
    return f"{minutes:02d}:{secs:02d}.{millis:03d}"


def markdown_inline(value: Any) -> str:
    text = str(value or "").replace("`", "\\`")
    return f"`{text}`"


def markdown_cell(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ")


def truncate_text(value: Any, limit: int = 420) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def format_allowed_decisions(item: dict[str, Any]) -> str:
    return ", ".join(markdown_inline(value) for value in sorted(allowed_decisions_for_item(item)))


def format_utterance_ids(item: dict[str, Any]) -> str:
    values: list[str] = []
    for key in ("utterance_ids", "me_utterance_ids", "remote_utterance_ids"):
        raw = item.get(key)
        if isinstance(raw, list):
            values.extend(str(value) for value in raw if value is not None)
    unique = list(dict.fromkeys(values))
    return ", ".join(markdown_inline(value) for value in unique) if unique else "-"


def write_item_details(lines: list[str], item: dict[str, Any]) -> None:
    source_id = item.get("source_audit_id") or f"item_{item.get('index')}"
    source_ids = item.get("source_audit_ids") if isinstance(item.get("source_audit_ids"), list) else []
    label = item.get("label") or item.get("source") or "review"
    confidence = item.get("suggested_decision_confidence")
    confidence_text = f" ({confidence})" if confidence not in (None, "") else ""
    reason = truncate_text(item.get("suggested_decision_reason") or "-", 520)
    command = str(item.get("command") or "").strip()
    group_text = f"- Grouped rows: `{item.get('group_size')}`" if item.get("grouped") else "- Grouped rows: `1`"
    hint = item.get("review_hint") if isinstance(item.get("review_hint"), dict) else {}
    risk_factors = hint.get("risk_factors") if isinstance(hint.get("risk_factors"), list) else []
    decision_guide = hint.get("decision_guide") if isinstance(hint.get("decision_guide"), list) else []
    lines.extend(
        [
            "",
            f"### {item.get('index')}. {markdown_inline(source_id)} / {markdown_inline(label)}",
            (
                f"- Pack: {markdown_inline(item.get('pack_start_time'))}-"
                f"{markdown_inline(item.get('pack_end_time'))}"
            ),
            f"- Session: {markdown_inline(item.get('session_id'))}",
            f"- Suggested: {markdown_inline(item.get('suggested_decision'))}{confidence_text}",
            f"- Suggested reason: {reason}",
            f"- Allowed: {format_allowed_decisions(item)}",
            group_text,
            f"- Source audit ids: {', '.join(markdown_inline(value) for value in source_ids) if source_ids else markdown_inline(source_id)}",
            f"- Utterances: {format_utterance_ids(item)}",
            f"- Command: {markdown_inline(item.get('command_key'))}",
        ]
    )
    if hint:
        lines.append(f"- Review focus: {truncate_text(hint.get('focus'), 420)}")
        lines.append(f"- Why review is required: {truncate_text(hint.get('why_review_required'), 420)}")
        if risk_factors:
            lines.append(f"- Risk factors: {truncate_text('; '.join(str(value) for value in risk_factors), 520)}")
        if decision_guide:
            guide_text = "; ".join(
                f"{item.get('decision')}: {item.get('when')}" for item in decision_guide if isinstance(item, dict)
            )
            lines.append(f"- Decision guide: {truncate_text(guide_text, 700)}")
    if command:
        lines.extend(["", "```bash", command, "```"])
    evidence = item.get("evidence_text") if isinstance(item.get("evidence_text"), list) else []
    if evidence:
        lines.append("- Evidence:")
        for piece in evidence:
            if not isinstance(piece, dict):
                continue
            role = piece.get("role") or "?"
            text = truncate_text(piece.get("text"), 700)
            lines.append(f"  - {markdown_inline(role)}: {text}")
    elif item.get("text"):
        lines.append(f"- Evidence: {truncate_text(item.get('text'), 700)}")


def write_markdown(path: Path, manifest: dict[str, Any]) -> None:
    lines = [
        "# MurmurMark Review Lane Pack",
        "",
        f"Lane: `{manifest['lane']}`",
        f"Items: `{manifest['summary']['item_count']}`",
        f"Audio: `{manifest['outputs']['audio']}`",
        f"Answers: `{manifest['outputs']['answer_sheet']}`",
        f"Suggested answers: `{manifest['outputs']['suggested_answer_sheet']}`",
        f"Duration: `{manifest['summary']['duration_sec']}` sec",
        "",
        "Decision shortcuts: `d=drop_me`, `c=drop_remote`, `k=keep_me`, `r/?=needs_review`, `s=skip`, `.=todo`.",
        "Use only decisions listed in `Allowed` for each item.",
        "",
        "```bash",
        f"afplay {shlex.quote(manifest['outputs']['audio'])}",
        f"$EDITOR {shlex.quote(manifest['outputs']['answer_sheet'])}",
        (
            ".venv/bin/python scripts/apply-review-lane-pack-decisions.py "
            f"{shlex.quote(manifest['outputs']['manifest'])} "
            f"--answers-file {shlex.quote(manifest['outputs']['answer_sheet'])} "
            "--out sessions/_reports/review-plan/review_decisions.jsonl"
        ),
        "```",
        "",
        "| # | Pack time | Session | Rows | Audit id | Suggestion | Text |",
        "|---:|---|---|---:|---|---|---|",
    ]
    for item in manifest["items"]:
        text = markdown_cell(truncate_text(item.get("text"), 260))
        row_count = item.get("group_size") or 1
        lines.append(
            f"| {item['index']} | {item['pack_start_time']}-{item['pack_end_time']} | "
            f"`{item.get('session_id')}` | {row_count} | `{item.get('source_audit_id')}` | "
            f"`{item.get('suggested_decision')}` | {text} |"
        )
    lines.extend(["", "## Review Items"])
    if not manifest["items"]:
        lines.append("")
        lines.append("_No review items._")
    for item in manifest["items"]:
        write_item_details(lines, item)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def allowed_decisions_for_item(item: dict[str, Any]) -> set[str]:
    values = item.get("allowed_decisions")
    if not isinstance(values, list) or not values:
        return set(DEFAULT_ALLOWED_DECISIONS)
    allowed = {str(value) for value in values if str(value) in KNOWN_REVIEW_DECISIONS}
    return allowed or set(DEFAULT_ALLOWED_DECISIONS)


def answer_for_item(item: dict[str, Any], suggested: bool) -> str:
    if not suggested:
        return "."
    decision = str(item.get("suggested_decision") or "todo")
    if decision not in allowed_decisions_for_item(item):
        return "."
    return DECISION_SHORTCUTS.get(decision, ".")


def write_answer_sheet(path: Path, manifest: dict[str, Any], *, suggested: bool = False) -> None:
    items = [item for item in manifest.get("items") or [] if isinstance(item, dict)]
    answers = "".join(answer_for_item(item, suggested) for item in items)
    title = "suggested review answers" if suggested else "review answers"
    lines = [
        f"# MurmurMark {title} for lane {manifest.get('lane')}",
        "# Listen to the lane WAV before applying decisions to medium-risk transcripts.",
        "# d=drop_me, c=drop_remote, k=keep_me, r/?=needs_review, s=skip, ./n/t=todo",
        "# Keep dots for items you have not reviewed or cannot confidently classify.",
        f"answers={answers}",
        "",
        "# Items",
    ]
    for item in items:
        text = " ".join(str(item.get("text") or "").split())
        allowed = ",".join(sorted(allowed_decisions_for_item(item)))
        group_size = item.get("group_size") or 1
        hint = item.get("review_hint") if isinstance(item.get("review_hint"), dict) else {}
        focus = truncate_text(hint.get("short_focus") or hint.get("focus") or "", 96)
        lines.append(
            f"# {item.get('index')}: {item.get('pack_start_time')}-{item.get('pack_end_time')} "
            f"{item.get('source_audit_id')} suggested={item.get('suggested_decision')} "
            f"allowed={allowed} rows={group_size} focus={focus} {text}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    template_rows = read_jsonl(args.template.expanduser())
    existing_rows = read_jsonl(args.decisions.expanduser())
    rows = merge_existing(template_rows, existing_rows)
    session_filters = {item.strip() for item in args.session if item.strip()}
    selected = [
        row
        for row in rows
        if str(row.get("review_lane") or "") == args.lane
        and session_matches(row, session_filters)
        and (args.include_reviewed or undecided(row))
    ]

    out_dir = args.out_dir.expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    audio_path = out_dir / f"review_lane_pack.{args.lane}.wav"
    manifest_path = out_dir / f"review_lane_pack.{args.lane}.json"
    md_path = out_dir / f"review_lane_pack.{args.lane}.md"
    answer_sheet_path = out_dir / f"review_lane_answers.{args.lane}.txt"
    suggested_answer_sheet_path = out_dir / f"review_lane_answers.{args.lane}.suggested.txt"
    template_path = args.template.expanduser()
    decisions_path = args.decisions.expanduser()

    selected_groups = group_selected_rows(selected, args.group_related)
    manifest_items: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    cursor = 0.0
    concat_parts: list[Path] = []

    with tempfile.TemporaryDirectory(prefix="murmurmark-review-lane-") as tmp:
        tmp_dir = Path(tmp)
        silence_wav = tmp_dir / "silence.wav"
        has_silence = make_silence(silence_wav, args.silence_sec)
        for index, group_rows in enumerate(selected_groups, start=1):
            row = group_rows[0]
            command_entries, command_error = command_entries_for_group(group_rows, args.command_key)
            if command_error is not None:
                skipped.append(command_error)
                continue
            first_command = command_entries[0] if command_entries else {"command_key": "review", "command": ""}
            command_key = str(first_command.get("command_key") or "review")
            command = str(first_command.get("command") or "")
            group_commands = [
                {
                    "source_audit_id": entry["row"].get("source_audit_id"),
                    "command_key": entry.get("command_key"),
                    "command": entry.get("command"),
                    "text_only": entry.get("text_only"),
                }
                for entry in command_entries
            ]
            if len(group_commands) > 1:
                command_key = f"grouped:{command_key}"
            tmp_wav = tmp_dir / f"clip_{index:04d}.wav"
            rendered, render_error = render_group_clip(command_entries, tmp_dir, index, tmp_wav)
            if not rendered:
                skipped.append(
                    {
                        "source_audit_id": row.get("source_audit_id"),
                        "source_audit_ids": [item.get("source_audit_id") for item in group_rows],
                        "reason": render_error,
                        "command": command,
                    }
                )
                continue
            duration = probe_duration(tmp_wav)
            if duration <= 0:
                skipped.append(
                    {
                        "source_audit_id": row.get("source_audit_id"),
                        "source_audit_ids": [item.get("source_audit_id") for item in group_rows],
                        "reason": "empty_rendered_clip",
                    }
                )
                continue
            start = cursor
            end = cursor + duration
            concat_parts.append(tmp_wav)
            if has_silence:
                concat_parts.append(silence_wav)
            review_row_keys = [review_row_key(item) for item in group_rows]
            source_audit_ids = [str(item.get("source_audit_id") or "") for item in group_rows if item.get("source_audit_id")]
            review_hint = review_hint_for_rows(group_rows)
            manifest_items.append(
                {
                    "index": len(manifest_items) + 1,
                    "review_row_key": review_row_key(row),
                    "review_row_keys": review_row_keys,
                    "source": row.get("source"),
                    "source_audit_id": row.get("source_audit_id"),
                    "source_audit_ids": source_audit_ids,
                    "grouped": len(group_rows) > 1,
                    "group_size": len(group_rows),
                    "group_key": related_group_key(row, args.group_related),
                    "session_id": row.get("session_id"),
                    "input_profile": row.get("input_profile"),
                    "review_lane": row.get("review_lane"),
                    "label": row.get("label"),
                    "utterance_ids": unique_from_rows(group_rows, "utterance_ids"),
                    "me_utterance_ids": unique_from_rows(group_rows, "me_utterance_ids"),
                    "remote_utterance_ids": unique_from_rows(group_rows, "remote_utterance_ids"),
                    "suggested_decision": group_suggested_decision(group_rows),
                    "suggested_decision_confidence": common_value(group_rows, "suggested_decision_confidence", "mixed"),
                    "suggested_decision_reason": group_suggested_reason(group_rows),
                    "allowed_decisions": common_allowed_decisions(group_rows),
                    "command_key": command_key,
                    "command": command,
                    "group_commands": group_commands,
                    "pack_start": round(start, 3),
                    "pack_end": round(end, 3),
                    "pack_start_time": format_time(start),
                    "pack_end_time": format_time(end),
                    "text": group_clip_text(group_rows),
                    "evidence_text": group_evidence_text(group_rows),
                    "review_hint": review_hint,
                }
            )
            cursor = end + args.silence_sec
        concat_ok = concat_wavs(concat_parts, audio_path, tmp_dir / "concat.txt")

    if not concat_ok:
        raise SystemExit(f"failed to write audio pack: {audio_path}")
    output_duration = probe_duration(audio_path)

    manifest = {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": {"name": "build-review-lane-pack", "version": SCRIPT_VERSION},
        "lane": args.lane,
        "inputs": {
            "template": str(args.template),
            "decisions": str(args.decisions) if args.decisions.exists() else None,
        },
        "parameters": {
            "session_filters": sorted(session_filters),
            "command_key": args.command_key,
            "silence_sec": args.silence_sec,
            "include_reviewed": args.include_reviewed,
            "group_related": args.group_related,
        },
        "outputs": {
            "audio": str(audio_path),
            "manifest": str(manifest_path),
            "markdown": str(md_path),
            "answer_sheet": str(answer_sheet_path),
            "suggested_answer_sheet": str(suggested_answer_sheet_path),
        },
        "summary": {
            "selected_rows": len(selected),
            "selected_groups": len(selected_groups),
            "item_count": len(manifest_items),
            "grouped_item_count": sum(1 for item in manifest_items if item.get("grouped")),
            "grouped_row_count": sum(max(0, int(item.get("group_size") or 1) - 1) for item in manifest_items),
            "skipped_count": len(skipped),
            "duration_sec": round(output_duration, 3),
        },
        "items": manifest_items,
        "skipped": skipped,
    }
    manifest.update(
        review_lane_handoff(
            lane=args.lane,
            audio_path=audio_path,
            manifest_path=manifest_path,
            md_path=md_path,
            answer_sheet_path=answer_sheet_path,
            suggested_answer_sheet_path=suggested_answer_sheet_path,
            template_path=template_path,
            decisions_path=decisions_path,
        )
    )
    write_json(manifest_path, manifest)
    write_answer_sheet(answer_sheet_path, manifest)
    write_answer_sheet(suggested_answer_sheet_path, manifest, suggested=True)
    write_markdown(md_path, manifest)
    print(f"audio: {audio_path}")
    print(f"manifest: {manifest_path}")
    print(f"answers: {answer_sheet_path}")
    print(f"suggested_answers: {suggested_answer_sheet_path}")
    print(f"items: {len(manifest_items)}")
    print(f"skipped: {len(skipped)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
