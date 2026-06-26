#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_VERSION = "0.2.0"
SCHEMA = "murmurmark.review_decision/v1"
VALID_DECISIONS = {"drop_me", "keep_me", "needs_review", "skip", "todo", ""}
SHORTCUTS = {
    "d": "drop_me",
    "k": "keep_me",
    "r": "needs_review",
    "s": "skip",
}
DEFAULT_ALLOWED_DECISIONS = {"drop_me", "keep_me", "needs_review", "skip"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fill MurmurMark review decisions by walking through review-plan clips.")
    parser.add_argument(
        "--template",
        type=Path,
        default=Path("sessions/_reports/review-plan/review_decisions.template.jsonl"),
        help="Input review_decisions.template.jsonl.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("sessions/_reports/review-plan/review_decisions.jsonl"),
        help="Editable output decisions JSONL.",
    )
    parser.add_argument("--session", action="append", default=[], help="Optional session id/path filter. Can be repeated.")
    parser.add_argument("--reviewer", default="", help="Reviewer name written to decided rows.")
    parser.add_argument("--command-key", default="stereo_clean_left_remote_right", help="Preferred afplay command key.")
    parser.add_argument("--context-utterances", type=int, default=2, help="Show this many transcript turns around the reviewed interval.")
    parser.add_argument("--no-play", action="store_true", help="Do not auto-play clips before prompting.")
    parser.add_argument("--limit", type=int, default=0, help="Stop after this many prompted rows. 0 means no limit.")
    return parser.parse_args()


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


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")


def normalize_text(value: Any) -> str:
    return " ".join(str(value or "").lower().replace("ё", "е").split())


def suffix(profile: str) -> str:
    return "" if profile == "current" else f".{profile}"


def safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def review_row_key(row: dict[str, Any]) -> str:
    source_id = str(row.get("source_audit_id") or "").strip()
    if source_id:
        return f"source:{source_id}"
    cluster_id = str(row.get("cluster_id") or "").strip()
    if cluster_id:
        return f"cluster:{cluster_id}"
    utterance_ids = row.get("utterance_ids")
    if isinstance(utterance_ids, list) and utterance_ids:
        return "utterances:" + ",".join(str(item) for item in utterance_ids)
    interval = row.get("interval") if isinstance(row.get("interval"), dict) else {}
    return f"interval:{interval.get('start')}:{interval.get('end')}:{row.get('label')}:{normalize_text(row.get('text'))[:80]}"


def merge_existing(template_rows: list[dict[str, Any]], existing_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    existing_by_key = {review_row_key(row): row for row in existing_rows}
    merged: list[dict[str, Any]] = []
    for row in template_rows:
        existing = existing_by_key.get(review_row_key(row))
        if existing:
            merged.append({**row, **existing})
        else:
            merged.append(dict(row))
    return merged


def session_matches(row: dict[str, Any], filters: set[str]) -> bool:
    if not filters:
        return True
    session = str(row.get("session") or "")
    session_id = str(row.get("session_id") or "")
    candidates = {session, session_id, f"./{session}"}
    return bool(candidates & filters)


def selected_command(row: dict[str, Any], preferred_key: str) -> str:
    commands = row.get("commands") if isinstance(row.get("commands"), dict) else {}
    for key in (
        preferred_key,
        "stereo_clean_left_remote_right",
        "stereo_mic_left_remote_right",
        "mic_raw",
        "remote",
    ):
        command = commands.get(key)
        if command:
            return str(command)
    return ""


def allowed_decisions(row: dict[str, Any]) -> set[str]:
    values = row.get("allowed_decisions")
    if not isinstance(values, list) or not values:
        return set(DEFAULT_ALLOWED_DECISIONS)
    allowed = {str(value) for value in values if str(value) in DEFAULT_ALLOWED_DECISIONS}
    return allowed or set(DEFAULT_ALLOWED_DECISIONS)


def play_command(command: str) -> None:
    if not command:
        print("No audio command for this row.")
        return
    print(f"$ {command}")
    completed = subprocess.run(shlex.split(command), check=False)
    if completed.returncode != 0:
        print(f"playback exited with {completed.returncode}")


def compact(value: Any, limit: int = 180) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


def role_name(row: dict[str, Any]) -> str:
    source = str(row.get("source_track") or "").lower()
    role = str(row.get("role") or row.get("speaker_label") or "").lower()
    if source == "mic" or role == "me":
        return "Me"
    if source == "remote" or role in {"remote", "colleagues"}:
        return "Colleagues"
    return str(row.get("role") or row.get("speaker_label") or "?")


def dialogue_for_row(row: dict[str, Any], cache: dict[tuple[str, str], list[dict[str, Any]]]) -> list[dict[str, Any]]:
    session = str(row.get("session") or "").strip()
    profile = str(row.get("input_profile") or "").strip() or "current"
    if not session:
        return []
    key = (session, profile)
    if key in cache:
        return cache[key]
    path = Path(session) / "derived/transcript-simple/whisper-cpp/resolved" / f"clean_dialogue{suffix(profile)}.json"
    data = read_json(path)
    utterances = data.get("utterances") if isinstance(data, dict) else None
    cache[key] = [item for item in utterances if isinstance(item, dict)] if isinstance(utterances, list) else []
    return cache[key]


def context_rows(row: dict[str, Any], dialogue: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    if count <= 0 or not dialogue:
        return []
    target_ids = {str(item) for item in row.get("utterance_ids", []) or []}
    target_ids.update(str(item) for item in row.get("me_utterance_ids", []) or [])
    target_ids.update(str(item) for item in row.get("remote_utterance_ids", []) or [])
    indexes = [index for index, item in enumerate(dialogue) if str(item.get("id")) in target_ids]
    if indexes:
        start = max(0, min(indexes) - count)
        end = min(len(dialogue), max(indexes) + count + 1)
        return dialogue[start:end]

    interval = row.get("interval") if isinstance(row.get("interval"), dict) else {}
    start_sec = safe_float(interval.get("start"))
    end_sec = safe_float(interval.get("end"))
    before = [
        item
        for item in dialogue
        if safe_float(item.get("end")) <= start_sec
    ][-count:]
    during = [
        item
        for item in dialogue
        if safe_float(item.get("start")) < end_sec and safe_float(item.get("end")) > start_sec
    ]
    after = [
        item
        for item in dialogue
        if safe_float(item.get("start")) >= end_sec
    ][:count]
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for item in before + during + after:
        item_id = str(item.get("id") or "")
        if item_id in seen:
            continue
        seen.add(item_id)
        rows.append(item)
    return rows


def print_context(row: dict[str, Any], cache: dict[tuple[str, str], list[dict[str, Any]]], count: int) -> None:
    dialogue = dialogue_for_row(row, cache)
    rows = context_rows(row, dialogue, count)
    if not rows:
        return
    target_ids = {str(item) for item in row.get("utterance_ids", []) or []}
    target_ids.update(str(item) for item in row.get("me_utterance_ids", []) or [])
    target_ids.update(str(item) for item in row.get("remote_utterance_ids", []) or [])
    print("Context:")
    for item in rows:
        marker = "*" if str(item.get("id")) in target_ids else " "
        print(
            f"{marker} {safe_float(item.get('start')):07.2f}-{safe_float(item.get('end')):07.2f} "
            f"{role_name(item)} {item.get('id')}: {compact(item.get('text'), 140)}"
        )


def print_row(row: dict[str, Any], index: int, total: int, cache: dict[tuple[str, str], list[dict[str, Any]]], context_count: int) -> None:
    interval = row.get("interval") if isinstance(row.get("interval"), dict) else {}
    print()
    print(f"[{index}/{total}] {row.get('session_id')} {interval.get('start_time')}..{interval.get('end_time')}")
    print(
        f"label={row.get('label')} verdict={row.get('verdict')} confidence={row.get('confidence')} "
        f"action={row.get('review_action')}"
    )
    print(f"allowed={', '.join(sorted(allowed_decisions(row)))}")
    if row.get("suggested_decision"):
        print(
            f"suggested={row.get('suggested_decision')} "
            f"({row.get('suggested_decision_confidence')}) - {row.get('suggested_decision_reason')}"
        )
    for item in row.get("text") or []:
        if not isinstance(item, dict):
            continue
        role = item.get("role") or item.get("source_track") or "?"
        print(f"- {role} {item.get('id')}: {compact(item.get('text'))}")
    print_context(row, cache, context_count)


def prompt_decision(row: dict[str, Any]) -> str | None:
    allowed = allowed_decisions(row)
    suggested = str(row.get("suggested_decision") or "").strip()
    if suggested in allowed:
        default = suggested
    elif "needs_review" in allowed:
        default = "needs_review"
    elif "skip" in allowed:
        default = "skip"
    else:
        default = sorted(allowed)[0]
    shortcuts = [
        f"{key}={value}"
        for key, value in SHORTCUTS.items()
        if value in allowed
    ]
    prompt = (
        "Decision ["
        + ", ".join(shortcuts + ["p=play", "n=todo", "q=quit", f"Enter={default}"])
        + "]: "
    )
    while True:
        answer = input(prompt).strip().lower()
        if answer == "":
            return default
        if answer in SHORTCUTS:
            decision = SHORTCUTS[answer]
            if decision in allowed:
                return decision
            print(f"Decision {decision} is not allowed for this row.")
            continue
        if answer in {"n", "todo"}:
            return "todo"
        if answer in {"q", "quit"}:
            return None
        if answer in {"p", "play"}:
            return "play"
        if answer in VALID_DECISIONS:
            if answer not in allowed:
                print(f"Decision {answer} is not allowed for this row.")
                continue
            return answer
        print("Unknown answer.")


def undecided(row: dict[str, Any]) -> bool:
    return str(row.get("decision") or "todo") in {"", "todo"}


def main() -> int:
    args = parse_args()
    template = args.template.expanduser()
    out = args.out.expanduser()
    if not template.exists():
        raise SystemExit(f"missing template: {template}")
    template_rows = read_jsonl(template)
    existing_rows = read_jsonl(out) if out.exists() else []
    rows = merge_existing(template_rows, existing_rows)
    filters = {item.strip() for item in args.session if item.strip()}
    indexes = [index for index, row in enumerate(rows) if session_matches(row, filters) and undecided(row)]
    total = len(indexes)
    prompted = 0
    dialogue_cache: dict[tuple[str, str], list[dict[str, Any]]] = {}

    if not indexes:
        write_jsonl(out, rows)
        print(f"No undecided rows. Written: {out}")
        return 0

    for visible_index, row_index in enumerate(indexes, start=1):
        row = rows[row_index]
        command = selected_command(row, args.command_key)
        print_row(row, visible_index, total, dialogue_cache, args.context_utterances)
        if command and not args.no_play:
            play_command(command)
        while True:
            decision = prompt_decision(row)
            if decision is None:
                write_jsonl(out, rows)
                print(f"Stopped. Written: {out}")
                return 0
            if decision == "play":
                play_command(command)
                continue
            break
        row["decision"] = decision
        row["status"] = "reviewed" if decision != "todo" else "todo"
        if decision != "todo":
            row["reviewed_at"] = datetime.now(timezone.utc).isoformat()
            if args.reviewer:
                row["reviewer"] = args.reviewer
        rows[row_index] = row
        write_jsonl(out, rows)
        prompted += 1
        if args.limit and prompted >= args.limit:
            print(f"Limit reached. Written: {out}")
            return 0

    print(f"Done. Written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
