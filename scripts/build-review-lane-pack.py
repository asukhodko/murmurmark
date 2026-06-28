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


SCRIPT_VERSION = "0.3.0"
SCHEMA = "murmurmark.review_lane_pack/v1"
DEFAULT_ALLOWED_DECISIONS = {"drop_me", "keep_me", "needs_review", "skip"}
DECISION_SHORTCUTS = {
    "drop_me": "d",
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


def session_matches(row: dict[str, Any], filters: set[str]) -> bool:
    if not filters:
        return True
    session = str(row.get("session") or "")
    session_id = str(row.get("session_id") or "")
    return bool({session, session_id, f"./{session}"} & filters)


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


def format_time(seconds: float) -> str:
    total = max(0, int(seconds))
    minutes = total // 60
    secs = total % 60
    millis = int(round((seconds - int(seconds)) * 1000))
    return f"{minutes:02d}:{secs:02d}.{millis:03d}"


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
        "| # | Pack time | Session | Audit id | Suggestion | Text |",
        "|---:|---|---|---|---|---|",
    ]
    for item in manifest["items"]:
        text = str(item.get("text") or "").replace("|", "\\|")
        lines.append(
            f"| {item['index']} | {item['pack_start_time']}-{item['pack_end_time']} | "
            f"`{item.get('session_id')}` | `{item.get('source_audit_id')}` | "
            f"`{item.get('suggested_decision')}` | {text} |"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def allowed_decisions_for_item(item: dict[str, Any]) -> set[str]:
    values = item.get("allowed_decisions")
    if not isinstance(values, list) or not values:
        return set(DEFAULT_ALLOWED_DECISIONS)
    allowed = {str(value) for value in values if str(value) in DEFAULT_ALLOWED_DECISIONS}
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
        "# d=drop_me, k=keep_me, r/?=needs_review, s=skip, ./n/t=todo",
        "# Keep dots for items you have not reviewed or cannot confidently classify.",
        f"answers={answers}",
        "",
        "# Items",
    ]
    for item in items:
        text = " ".join(str(item.get("text") or "").split())
        allowed = ",".join(sorted(allowed_decisions_for_item(item)))
        lines.append(
            f"# {item.get('index')}: {item.get('pack_start_time')}-{item.get('pack_end_time')} "
            f"{item.get('source_audit_id')} suggested={item.get('suggested_decision')} "
            f"allowed={allowed} {text}"
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

    manifest_items: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    cursor = 0.0
    concat_parts: list[Path] = []

    with tempfile.TemporaryDirectory(prefix="murmurmark-review-lane-") as tmp:
        tmp_dir = Path(tmp)
        silence_wav = tmp_dir / "silence.wav"
        has_silence = make_silence(silence_wav, args.silence_sec)
        for index, row in enumerate(selected, start=1):
            command_choice = command_for_row(row, args.command_key)
            is_text_only = str(row.get("source") or "") == "transcript_order"
            if command_choice is None and not is_text_only:
                skipped.append({"source_audit_id": row.get("source_audit_id"), "reason": "missing_audio_command"})
                continue
            command_key, command = command_choice if command_choice is not None else ("review", str((row.get("commands") or {}).get("review") or ""))
            tmp_wav = tmp_dir / f"clip_{index:04d}.wav"
            rendered = make_silence(tmp_wav, 0.25) if is_text_only else render_command(command, tmp_wav)
            if not rendered:
                skipped.append({"source_audit_id": row.get("source_audit_id"), "reason": "render_failed", "command": command})
                continue
            duration = probe_duration(tmp_wav)
            if duration <= 0:
                skipped.append({"source_audit_id": row.get("source_audit_id"), "reason": "empty_rendered_clip"})
                continue
            start = cursor
            end = cursor + duration
            concat_parts.append(tmp_wav)
            if has_silence:
                concat_parts.append(silence_wav)
            manifest_items.append(
                {
                    "index": len(manifest_items) + 1,
                    "review_row_key": review_row_key(row),
                    "source": row.get("source"),
                    "source_audit_id": row.get("source_audit_id"),
                    "session_id": row.get("session_id"),
                    "input_profile": row.get("input_profile"),
                    "review_lane": row.get("review_lane"),
                    "label": row.get("label"),
                    "utterance_ids": row.get("utterance_ids"),
                    "me_utterance_ids": row.get("me_utterance_ids"),
                    "remote_utterance_ids": row.get("remote_utterance_ids"),
                    "suggested_decision": row.get("suggested_decision"),
                    "suggested_decision_confidence": row.get("suggested_decision_confidence"),
                    "suggested_decision_reason": row.get("suggested_decision_reason"),
                    "allowed_decisions": row.get("allowed_decisions"),
                    "command_key": command_key,
                    "command": command,
                    "pack_start": round(start, 3),
                    "pack_end": round(end, 3),
                    "pack_start_time": format_time(start),
                    "pack_end_time": format_time(end),
                    "text": clip_text(row),
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
            "item_count": len(manifest_items),
            "skipped_count": len(skipped),
            "duration_sec": round(output_duration, 3),
        },
        "items": manifest_items,
        "skipped": skipped,
    }
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
