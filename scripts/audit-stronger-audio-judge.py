#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shlex
import subprocess
import wave
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


SCHEMA_ROW = "murmurmark.faster_whisper_judge/v1"
SCHEMA_SUMMARY = "murmurmark.faster_whisper_judge_summary/v1"
SCRIPT_VERSION = "0.1.1"
DEFAULT_MODEL = Path.home() / ".local/share/murmurmark/models/faster-whisper/large-v3"
DEFAULT_MAX_ITEMS = 80
DEFAULT_SOURCES = ("mic_role_masked", "mic_clean", "mic_raw", "remote")
QUICK_SOURCES = ("mic_clean", "remote")
STOP_WORDS = {
    "а",
    "и",
    "но",
    "ну",
    "да",
    "вот",
    "это",
    "как",
    "то",
    "же",
    "там",
    "тут",
    "у",
    "в",
    "на",
    "не",
    "по",
    "за",
    "из",
    "с",
    "со",
    "что",
    "чтобы",
    "мы",
    "ты",
    "он",
    "она",
    "они",
    "оно",
    "я",
}
MEANINGFUL_SHORT_UTTERANCES = {
    "да",
    "нет",
    "ок",
    "окей",
    "ага",
    "угу",
    "неа",
    "ну",
    "вот",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Use local faster-whisper as a stronger audio judge for review clips.")
    parser.add_argument("session", type=Path)
    parser.add_argument("--profile", default="auto", help="Profile used for the audio-review pack. Written to reports.")
    parser.add_argument("--pack-dir", type=Path, default=None)
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument("--model", type=Path, default=None)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--compute-type", default="int8")
    parser.add_argument("--language", default="ru")
    parser.add_argument("--max-items", type=int, default=DEFAULT_MAX_ITEMS)
    parser.add_argument(
        "--max-computed-items",
        type=int,
        default=0,
        help="Compute at most this many missing items in this run. Cached rows are still kept. 0 means no extra cap.",
    )
    parser.add_argument("--beam-size", type=int, default=1)
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Quick lane triage: decode mic_clean and remote only unless --source is provided.",
    )
    parser.add_argument(
        "--progress",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Print progress while loading the model and decoding clips.",
    )
    parser.add_argument("--allow-download", action="store_true", help="Allow Hugging Face network access.")
    parser.add_argument("--no-cache", action="store_true", help="Recompute selected clips even when cached judge rows exist.")
    parser.add_argument(
        "--pack-item-id",
        action="append",
        default=[],
        help="Audit a specific audio-review pack item id, e.g. arp_000042. Can repeat.",
    )
    parser.add_argument(
        "--review-lane-pack",
        action="append",
        type=Path,
        default=[],
        help="Read source_audit_id/source_audit_ids from a review lane pack and audit those pack items first.",
    )
    parser.add_argument("--source", action="append", choices=DEFAULT_SOURCES, help="Clip source to decode. Can repeat.")
    parser.add_argument("--write-clips", action=argparse.BooleanOptionalAction, default=None, help=argparse.SUPPRESS)
    return parser.parse_args()


def progress(args: argparse.Namespace, message: str) -> None:
    if args.progress:
        print(f"stronger_audio_judge: {message}", flush=True)


def selected_sources(args: argparse.Namespace) -> tuple[str, ...]:
    if args.source:
        return tuple(args.source)
    if args.quick:
        return QUICK_SOURCES
    return DEFAULT_SOURCES


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def collect_source_audit_ids(value: Any) -> list[str]:
    ids: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "source_audit_id" and child:
                ids.append(str(child))
            elif key == "source_audit_ids" and isinstance(child, list):
                ids.extend(str(item) for item in child if item)
            else:
                ids.extend(collect_source_audit_ids(child))
    elif isinstance(value, list):
        for item in value:
            ids.extend(collect_source_audit_ids(item))
    return ids


def list_strings(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if item is not None and str(item)]
    return []


def lane_pack_selectors(args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[str]]:
    selectors: list[dict[str, Any]] = []
    missing_pack_files: list[str] = []
    for path in args.review_lane_pack:
        lane_pack = read_json(path.expanduser())
        if lane_pack is None:
            missing_pack_files.append(str(path))
            continue
        for item in lane_pack.get("items") or []:
            if not isinstance(item, dict):
                continue
            selectors.append(
                {
                    "source_ids": collect_source_audit_ids(item),
                    "utterance_ids": list_strings(item.get("utterance_ids")),
                    "me_utterance_ids": list_strings(item.get("me_utterance_ids")),
                    "remote_utterance_ids": list_strings(item.get("remote_utterance_ids")),
                }
            )
    return selectors, missing_pack_files


def parse_ffplay_slice(command: Any) -> tuple[Path | None, float, float] | None:
    if not isinstance(command, str) or not command.strip():
        return None
    try:
        parts = shlex.split(command)
    except ValueError:
        return None
    if not parts or Path(parts[0]).name not in {"ffplay", "afplay"}:
        return None
    start = 0.0
    duration = 0.0
    path: Path | None = None
    index = 1
    options_with_value = {"-ss", "-t", "-loglevel", "-i"}
    while index < len(parts):
        part = parts[index]
        if part == "-ss" and index + 1 < len(parts):
            start = safe_float(parts[index + 1])
            index += 2
            continue
        if part == "-t" and index + 1 < len(parts):
            duration = safe_float(parts[index + 1])
            index += 2
            continue
        if part in options_with_value and index + 1 < len(parts):
            if part == "-i":
                path = Path(parts[index + 1]).expanduser()
            index += 2
            continue
        if part.startswith("-"):
            index += 1
            continue
        path = Path(part).expanduser()
        index += 1
    if path is None or duration <= 0:
        return None
    return path, start, duration


def parse_play_path(command: Any) -> Path | None:
    if not isinstance(command, str) or not command.strip():
        return None
    try:
        parts = shlex.split(command)
    except ValueError:
        return None
    if not parts or Path(parts[0]).name not in {"ffplay", "afplay"}:
        return None
    path: Path | None = None
    index = 1
    options_with_value = {"-ss", "-t", "-loglevel", "-i"}
    while index < len(parts):
        part = parts[index]
        if part in options_with_value and index + 1 < len(parts):
            if part == "-i":
                path = Path(parts[index + 1]).expanduser()
            index += 2
            continue
        if part.startswith("-"):
            index += 1
            continue
        path = Path(part).expanduser()
        index += 1
    return path


def wav_duration(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as file:
            frames = file.getnframes()
            rate = file.getframerate()
            if rate > 0:
                return frames / rate
    except (OSError, EOFError, wave.Error):
        return 0.0
    return 0.0


def session_audio_sources(session: Path, mic_raw: Path | None) -> dict[str, Path]:
    return {
        "mic_raw": mic_raw or session / "audio/mic/000001.caf",
        "remote": session / "audio/remote/000001.caf",
        "mic_clean": session / "derived/preprocess/audio/mic_clean_local_fir.wav",
        "mic_role_masked": session / "derived/preprocess/audio/mic_role_masked_for_asr.wav",
    }


def slice_audio(source: Path, destination: Path, start: float, duration: float) -> bool:
    if not source.exists() or duration <= 0:
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{start:.3f}",
        "-t",
        f"{duration:.3f}",
        "-i",
        str(source),
        "-ac",
        "1",
        "-ar",
        "16000",
        str(destination),
    ]
    return subprocess.run(command, check=False).returncode == 0 and destination.exists() and destination.stat().st_size > 0


def lane_item_text_rows(item: dict[str, Any], start: float, end: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    me_ids = list_strings(item.get("me_utterance_ids"))
    remote_ids = list_strings(item.get("remote_utterance_ids"))
    me_index = 0
    remote_index = 0
    for piece in item.get("evidence_text") or []:
        if not isinstance(piece, dict):
            continue
        role_text = str(piece.get("role") or "").lower()
        text = str(piece.get("text") or "").strip()
        if not text:
            continue
        if "me" in role_text:
            utterance_id = me_ids[me_index] if me_index < len(me_ids) else ""
            me_index += 1
            rows.append(
                {
                    "id": utterance_id,
                    "role": "me",
                    "source_track": "mic",
                    "start": start,
                    "end": end,
                    "text": text,
                    "needs_review": True,
                    "quality_flags": ["review_lane", "transcript_order"],
                }
            )
        elif "remote" in role_text or "colleague" in role_text:
            utterance_id = remote_ids[remote_index] if remote_index < len(remote_ids) else ""
            remote_index += 1
            rows.append(
                {
                    "id": utterance_id,
                    "role": "remote",
                    "source_track": "remote",
                    "start": start,
                    "end": end,
                    "text": text,
                    "needs_review": False,
                    "quality_flags": ["review_lane", "transcript_order"],
                }
            )
    return rows


def lane_item_text_rows_for_source(item: dict[str, Any], source_id: str, start: float, end: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for piece in item.get("evidence_text") or []:
        if not isinstance(piece, dict):
            continue
        role_text = str(piece.get("role") or "").lower()
        if source_id and source_id.lower() not in role_text:
            continue
        text = str(piece.get("text") or "").strip()
        if not text:
            continue
        if "me" in role_text:
            rows.append(
                {
                    "id": "",
                    "role": "me",
                    "source_track": "mic",
                    "start": start,
                    "end": end,
                    "text": text,
                    "needs_review": True,
                    "quality_flags": ["review_lane", "transcript_order"],
                }
            )
        elif "remote" in role_text or "colleague" in role_text:
            rows.append(
                {
                    "id": "",
                    "role": "remote",
                    "source_track": "remote",
                    "start": start,
                    "end": end,
                    "text": text,
                    "needs_review": False,
                    "quality_flags": ["review_lane", "transcript_order"],
                }
            )
    return rows


def source_clips_from_review_lane_clip(source_id: str, clip_path: Path) -> dict[str, str]:
    clips: dict[str, str] = {}
    clip_dir = clip_path.parent
    for source in DEFAULT_SOURCES:
        candidate = clip_dir / f"{source_id}_{source}.wav"
        if candidate.exists() and candidate.stat().st_size > 0:
            clips[source] = str(candidate)
    return clips


def synthetic_item_from_existing_clips(
    lane_item: dict[str, Any],
    source_id: str,
    clip_path: Path,
    args: argparse.Namespace,
) -> dict[str, Any] | None:
    clips = source_clips_from_review_lane_clip(source_id, clip_path)
    if not clips:
        return None
    duration = max(wav_duration(Path(path)) for path in clips.values())
    if duration <= 0:
        duration = safe_float((lane_item.get("interval") or {}).get("duration_sec"), 0.0)
    if duration <= 0:
        duration = max(0.0, safe_float(lane_item.get("pack_end")) - safe_float(lane_item.get("pack_start")))
    start = safe_float((lane_item.get("interval") or {}).get("start"), 0.0)
    end = start + duration
    rows = lane_item_text_rows_for_source(lane_item, source_id, start, end) or lane_item_text_rows(lane_item, start, end)
    return {
        "schema": "murmurmark.audio_review_pack_item/v1",
        "id": source_id,
        "session_id": lane_item.get("session_id") or args.session.name,
        "profile": lane_item.get("input_profile") or args.profile,
        "interval": {
            "start": round(start, 3),
            "end": round(end, 3),
            "duration_sec": round(duration, 3),
            "start_time": format_time(start),
            "end_time": format_time(end),
        },
        "source_reasons": [
            f"review_lane:{lane_item.get('review_lane') or 'unknown'}",
            str(lane_item.get("label") or "needs_review"),
        ],
        "utterance_ids": list_strings(lane_item.get("utterance_ids")),
        "utterances": rows,
        "clips": clips,
    }


def synthetic_lane_pack_items(args: argparse.Namespace, session: Path, out_dir: Path) -> tuple[list[dict[str, Any]], list[str]]:
    items: list[dict[str, Any]] = []
    missing_pack_files: list[str] = []
    clip_dir = out_dir / "review-lane-clips"
    for path in args.review_lane_pack:
        lane_pack = read_json(path.expanduser())
        if lane_pack is None:
            missing_pack_files.append(str(path))
            continue
        for item in lane_pack.get("items") or []:
            if not isinstance(item, dict):
                continue
            source_ids = list_strings(item.get("source_audit_ids")) or list_strings([item.get("source_audit_id")])
            source_id = source_ids[0] if source_ids else ""
            if not source_id:
                continue
            existing_clip_items: list[dict[str, Any]] = []
            group_commands = item.get("group_commands") if isinstance(item.get("group_commands"), list) else []
            command_rows = group_commands or [
                {"source_audit_id": source_id, "command": item.get("command")},
            ]
            for command_row in command_rows:
                if not isinstance(command_row, dict):
                    continue
                command_source_id = str(command_row.get("source_audit_id") or source_id)
                play_path = parse_play_path(command_row.get("command"))
                if play_path is None:
                    continue
                synthetic = synthetic_item_from_existing_clips(item, command_source_id, play_path, args)
                if synthetic is not None:
                    existing_clip_items.append(synthetic)
            if existing_clip_items:
                items.extend(existing_clip_items)
                continue
            parsed = parse_ffplay_slice(item.get("command"))
            if parsed is None:
                continue
            mic_raw_path, start, duration = parsed
            end = start + duration
            clips: dict[str, str] = {}
            for source, audio_path in session_audio_sources(session, mic_raw_path).items():
                destination = clip_dir / f"{source_id}_{source}.wav"
                if destination.exists() and destination.stat().st_size > 0:
                    clips[source] = str(destination)
                    continue
                if slice_audio(audio_path, destination, start, duration):
                    clips[source] = str(destination)
            if not clips:
                continue
            utterance_rows = lane_item_text_rows(item, start, end)
            items.append(
                {
                    "schema": "murmurmark.audio_review_pack_item/v1",
                    "id": source_id,
                    "session_id": item.get("session_id") or session.name,
                    "profile": item.get("input_profile") or args.profile,
                    "interval": {
                        "start": round(start, 3),
                        "end": round(end, 3),
                        "duration_sec": round(duration, 3),
                        "start_time": format_time(start),
                        "end_time": format_time(end),
                    },
                    "source_reasons": [
                        f"review_lane:{item.get('review_lane') or lane_pack.get('lane') or 'unknown'}",
                        str(item.get("label") or "needs_review"),
                    ],
                    "utterance_ids": list_strings(item.get("utterance_ids")),
                    "utterances": utterance_rows,
                    "clips": clips,
                }
            )
    return items, missing_pack_files


def item_id_set(item: dict[str, Any]) -> set[str]:
    ids = set(str(value) for value in item.get("utterance_ids") or [] if value)
    ids.update(utterance_ids(item))
    return ids


def item_matches_lane_selector(item: dict[str, Any], selector: dict[str, Any]) -> bool:
    item_ids = item_id_set(item)
    selector_ids = set(selector.get("utterance_ids") or [])
    me_ids = set(selector.get("me_utterance_ids") or [])
    remote_ids = set(selector.get("remote_utterance_ids") or [])
    if item_ids and selector_ids and (item_ids <= selector_ids or selector_ids <= item_ids):
        return True
    if me_ids and item_ids & me_ids:
        return True
    if not me_ids and remote_ids and item_ids & remote_ids:
        return True
    return False


def target_item_ids(args: argparse.Namespace, items: list[dict[str, Any]]) -> tuple[list[str], list[str], list[str]]:
    explicit_ids = [str(value).strip() for value in args.pack_item_id if str(value).strip()]
    ids: list[str] = []
    selectors, missing_pack_files = lane_pack_selectors(args)
    matched_selector_ids: list[str] = []
    for selector in selectors:
        source_ids = [str(value) for value in selector.get("source_ids") or [] if value]
        exact = [
            str(item.get("id") or "")
            for item in items
            if item.get("id")
            and str(item.get("id") or "") in source_ids
            and item_matches_lane_selector(item, selector)
        ]
        if exact:
            matched_selector_ids.extend(exact)
            continue
        matched = [str(item.get("id") or "") for item in items if item.get("id") and item_matches_lane_selector(item, selector)]
        if matched:
            matched_selector_ids.extend(matched)
        else:
            ids.extend(str(value) for value in selector.get("source_ids") or [] if value)
    ids = explicit_ids + matched_selector_ids + ids
    return list(dict.fromkeys(ids)), missing_pack_files, [",".join(selector.get("utterance_ids") or []) for selector in selectors]


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


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")


def normalize_text(value: Any) -> str:
    text = str(value or "").lower().replace("ё", "е")
    text = re.sub(r"[^0-9a-zа-я_./+-]+", " ", text)
    return " ".join(text.split())


def content_tokens(value: Any) -> list[str]:
    return [token for token in normalize_text(value).split() if token not in STOP_WORDS and len(token) > 1]


def looks_like_noise_fragment(value: Any) -> bool:
    tokens = normalize_text(value).split()
    if not tokens or len(tokens) > 2:
        return False
    text = " ".join(tokens)
    if text in MEANINGFUL_SHORT_UTTERANCES:
        return False
    return all(len(token) <= 2 for token in tokens)


def text_similarity(left: Any, right: Any) -> dict[str, float]:
    left_norm = normalize_text(left)
    right_norm = normalize_text(right)
    sequence = SequenceMatcher(None, left_norm, right_norm).ratio() if left_norm or right_norm else 0.0
    left_tokens = set(content_tokens(left_norm))
    right_tokens = set(content_tokens(right_norm))
    if left_tokens and right_tokens:
        containment = len(left_tokens & right_tokens) / max(1, min(len(left_tokens), len(right_tokens)))
        jaccard = len(left_tokens & right_tokens) / max(1, len(left_tokens | right_tokens))
        length_ratio = min(len(left_tokens), len(right_tokens)) / max(1, max(len(left_tokens), len(right_tokens)))
    else:
        containment = 0.0
        jaccard = 0.0
        length_ratio = 0.0
    containment_score = containment * min(1.0, length_ratio * 2.0)
    return {
        "sequence_ratio": round(sequence, 6),
        "containment": round(containment, 6),
        "jaccard": round(jaccard, 6),
        "length_ratio": round(length_ratio, 6),
        "similarity": round(max(sequence, containment_score, jaccard), 6),
    }


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(number) or math.isinf(number):
        return default
    return number


def format_time(seconds: float) -> str:
    total = max(0, int(seconds))
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def resolve_model(args: argparse.Namespace) -> Path:
    if args.model is not None:
        return args.model.expanduser()
    env_value = os.environ.get("MURMURMARK_FASTER_WHISPER_MODEL")
    if env_value:
        return Path(env_value).expanduser()
    return DEFAULT_MODEL


def model_ready(model_path: Path) -> tuple[bool, str]:
    if not model_path.exists():
        return False, f"model path not found: {model_path}"
    if model_path.is_dir() and not (model_path / "model.bin").exists():
        return False, f"model.bin not found under: {model_path}"
    if model_path.is_file() and model_path.name != "model.bin":
        return False, f"expected CTranslate2 model directory or model.bin: {model_path}"
    return True, "ok"


def utterance_texts(item: dict[str, Any]) -> tuple[str, str]:
    me: list[str] = []
    remote: list[str] = []
    for row in item.get("utterances") or []:
        if not isinstance(row, dict):
            continue
        role = str(row.get("role") or "").lower()
        track = str(row.get("source_track") or "").lower()
        text = str(row.get("text") or "")
        if role == "me" or track == "mic":
            me.append(text)
        elif role == "remote" or "colleague" in role or track == "remote":
            remote.append(text)
    return " ".join(me).strip(), " ".join(remote).strip()


def utterance_ids(item: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for row in item.get("utterances") or []:
        if isinstance(row, dict) and row.get("id"):
            ids.append(str(row["id"]))
    return list(dict.fromkeys(ids))


def item_utterance_ids(item: dict[str, Any]) -> list[str]:
    return utterance_ids(item) or list_strings(item.get("utterance_ids"))


def item_fingerprint_payload(item: dict[str, Any]) -> dict[str, Any]:
    interval = item.get("interval") if isinstance(item.get("interval"), dict) else {}
    utterances: list[dict[str, Any]] = []
    for row in item.get("utterances") or []:
        if not isinstance(row, dict):
            continue
        utterances.append(
            {
                "id": str(row.get("id") or ""),
                "role": str(row.get("role") or ""),
                "source_track": str(row.get("source_track") or ""),
                "start": round(safe_float(row.get("start")), 3),
                "end": round(safe_float(row.get("end")), 3),
                "text": normalize_text(row.get("text")),
            }
        )
    return {
        "session_id": str(item.get("session_id") or ""),
        "profile": str(item.get("profile") or ""),
        "interval": {
            "start": round(safe_float(interval.get("start")), 3),
            "end": round(safe_float(interval.get("end")), 3),
        },
        "utterance_ids": [str(value) for value in item.get("utterance_ids") or []],
        "utterances": utterances,
    }


def item_fingerprint(item: dict[str, Any]) -> str:
    payload = item_fingerprint_payload(item)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def cached_row_matches_item(row: dict[str, Any], item: dict[str, Any]) -> bool:
    expected = item_fingerprint(item)
    actual = str(row.get("source_pack_item_fingerprint") or "")
    if actual:
        return actual == expected
    # Compatibility for rows written before fingerprints existed.
    return item_fingerprint_payload(row) == item_fingerprint_payload(item)


def audit_by_id(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row.get("id")): row for row in rows if row.get("id")}


def audit_row_matches_item(row: dict[str, Any] | None, item: dict[str, Any]) -> bool:
    if not row:
        return False
    return item_fingerprint_payload(row) == item_fingerprint_payload(item)


def item_priority(item: dict[str, Any], audit_row: dict[str, Any] | None) -> tuple[int, float, str]:
    reasons = set(str(value) for value in item.get("source_reasons") or [])
    classification = audit_row.get("classification") if isinstance(audit_row, dict) else {}
    if not isinstance(classification, dict):
        classification = {}
    label = str(classification.get("label") or "")
    verdict = str(classification.get("verdict") or "")
    duration = safe_float((item.get("interval") or {}).get("duration_sec"), 0.0)
    if verdict == "needs_stronger_audio_judge" or label == "uncertain":
        return (0, -duration, str(item.get("id") or ""))
    if any("local_recall" in reason for reason in reasons):
        return (1, -duration, str(item.get("id") or ""))
    if any("cross_role_overlap" in reason or "group_overlap:needs_human_review" in reason for reason in reasons):
        return (2, -duration, str(item.get("id") or ""))
    if verdict == "probable_transcript_error":
        return (3, -duration, str(item.get("id") or ""))
    return (9, -duration, str(item.get("id") or ""))


def selected_items(
    items: list[dict[str, Any]],
    audit_rows: dict[str, dict[str, Any]],
    limit: int,
    *,
    target_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    if target_ids:
        by_id = {str(item.get("id") or ""): item for item in items}
        targeted = [by_id[item_id] for item_id in target_ids if item_id in by_id]
        if limit > 0:
            return targeted[:limit]
        return targeted
    ranked = sorted(items, key=lambda item: item_priority(item, audit_rows.get(str(item.get("id") or ""))))
    selected = [item for item in ranked if item_priority(item, audit_rows.get(str(item.get("id") or "")))[0] < 9]
    if not selected:
        selected = ranked
    return selected[: max(0, limit)]


def load_model(model_path: Path, args: argparse.Namespace) -> Any:
    if not args.allow_download:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
    try:
        from faster_whisper import WhisperModel
    except ImportError as error:
        raise SystemExit("missing faster_whisper module; install faster-whisper ctranslate2") from error
    return WhisperModel(str(model_path), device=args.device, compute_type=args.compute_type)


def transcribe_clip(model: Any, path: Path, args: argparse.Namespace) -> dict[str, Any]:
    if not path.exists() or path.stat().st_size <= 0:
        return {"path": str(path), "exists": False, "text": "", "segments": [], "avg_logprob": None, "no_speech_prob": None}
    try:
        segments, info = model.transcribe(
            str(path),
            language=args.language,
            beam_size=args.beam_size,
            vad_filter=False,
            condition_on_previous_text=False,
            word_timestamps=False,
        )
        rows: list[dict[str, Any]] = []
        for segment in segments:
            rows.append(
                {
                    "start": round(float(segment.start), 3),
                    "end": round(float(segment.end), 3),
                    "text": str(segment.text or "").strip(),
                    "avg_logprob": round(safe_float(getattr(segment, "avg_logprob", None), 0.0), 6),
                    "no_speech_prob": round(safe_float(getattr(segment, "no_speech_prob", None), 0.0), 6),
                }
            )
    except Exception as error:
        return {"path": str(path), "exists": True, "error": str(error), "text": "", "segments": []}
    text = " ".join(row["text"] for row in rows if row.get("text")).strip()
    durations = [max(0.0, row["end"] - row["start"]) for row in rows]
    total_duration = sum(durations)
    if rows and total_duration > 0:
        avg_logprob = sum(row["avg_logprob"] * duration for row, duration in zip(rows, durations)) / total_duration
        no_speech_prob = max(row["no_speech_prob"] for row in rows)
    else:
        avg_logprob = None
        no_speech_prob = None
    return {
        "path": str(path),
        "exists": True,
        "text": text,
        "segments": rows,
        "segment_count": len(rows),
        "language": getattr(info, "language", args.language),
        "language_probability": round(safe_float(getattr(info, "language_probability", 0.0), 0.0), 6),
        "avg_logprob": round(avg_logprob, 6) if avg_logprob is not None else None,
        "no_speech_prob": round(no_speech_prob, 6) if no_speech_prob is not None else None,
    }


def source_metrics(transcripts: dict[str, dict[str, Any]], me_text: str, remote_text: str) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    remote_reference_text = remote_text or str(transcripts.get("remote", {}).get("text") or "")
    for source, result in transcripts.items():
        text = str(result.get("text") or "")
        metrics[source] = {
            "text_len": len(text),
            "content_token_count": len(content_tokens(text)),
            "to_me": text_similarity(text, me_text),
            "to_remote": text_similarity(text, remote_reference_text),
            "avg_logprob": result.get("avg_logprob"),
            "no_speech_prob": result.get("no_speech_prob"),
        }
    return metrics


def best_score(metrics: dict[str, Any], sources: tuple[str, ...], target: str) -> tuple[float, str]:
    best = (0.0, "")
    for source in sources:
        score = safe_float(metrics.get(source, {}).get(target, {}).get("similarity"), 0.0)
        if score > best[0]:
            best = (score, source)
    return best


def short_me_tokens_contained(
    transcripts: dict[str, dict[str, Any]],
    sources: tuple[str, ...],
    me_tokens: list[str],
) -> tuple[bool, str]:
    meaningful = [token for token in me_tokens if len(token) >= 5]
    if not meaningful or len(meaningful) > 2:
        return False, ""
    target = set(meaningful)
    for source in sources:
        source_tokens = set(content_tokens(str(transcripts.get(source, {}).get("text") or "")))
        if target and target <= source_tokens:
            return True, source
    return False, ""


def classify_item(
    item: dict[str, Any],
    audit_row: dict[str, Any] | None,
    transcripts: dict[str, dict[str, Any]],
    metrics: dict[str, Any],
) -> dict[str, Any]:
    me_text, remote_text = utterance_texts(item)
    remote_reference_text = remote_text or str(transcripts.get("remote", {}).get("text") or "")
    me_tokens = content_tokens(me_text)
    remote_tokens = content_tokens(remote_reference_text)
    mic_sources = tuple(source for source in ("mic_role_masked", "mic_clean", "mic_raw") if source in metrics)
    clean_sources = tuple(source for source in ("mic_role_masked", "mic_clean") if source in metrics)
    best_me, best_me_source = best_score(metrics, clean_sources or mic_sources, "to_me")
    best_me_any, best_me_any_source = best_score(metrics, mic_sources, "to_me")
    best_remote_in_mic, remote_in_mic_source = best_score(metrics, mic_sources, "to_remote")
    remote_source_to_remote = safe_float(metrics.get("remote", {}).get("to_remote", {}).get("similarity"), 0.0)
    remote_source_to_me = safe_float(metrics.get("remote", {}).get("to_me", {}).get("similarity"), 0.0)
    remote_source_to_me_containment = safe_float(metrics.get("remote", {}).get("to_me", {}).get("containment"), 0.0)
    remote_source_tokens = int(metrics.get("remote", {}).get("content_token_count") or 0)
    mic_content_tokens = max(int(metrics.get(source, {}).get("content_token_count") or 0) for source in mic_sources) if mic_sources else 0

    audit_class = audit_row.get("classification") if isinstance(audit_row, dict) else {}
    if not isinstance(audit_class, dict):
        audit_class = {}
    audit_scores = audit_row.get("scores") if isinstance(audit_row, dict) else {}
    if not isinstance(audit_scores, dict):
        audit_scores = {}
    audit_label = str(audit_class.get("label") or "")
    audit_verdict = str(audit_class.get("verdict") or "")
    local_support = safe_float(audit_scores.get("local_support"), 0.0)
    remote_similarity = safe_float(audit_scores.get("remote_similarity"), 0.0)

    reasons: list[str] = []
    label = "uncertain"
    suggested = "needs_review"
    confidence = 0.5

    me_confirmed = best_me >= 0.46 or best_me_any >= 0.58
    remote_confirmed = remote_source_to_remote >= 0.46 or (remote_source_tokens >= 2 and remote_text)
    remote_duplicate = best_remote_in_mic >= 0.70 and best_me_any < 0.42 and remote_similarity >= 35
    very_short_me = len(me_tokens) <= 3 and len(normalize_text(me_text).split()) <= 4
    short_content_me = len(me_tokens) <= 3 and len(normalize_text(me_text).split()) <= 6
    short_me_contained, short_me_source = short_me_tokens_contained(transcripts, clean_sources or mic_sources, me_tokens)
    noise_fragment_me = looks_like_noise_fragment(me_text)
    no_mic_me = best_me_any < 0.24 and mic_content_tokens <= 2
    mic_rejects_noise_fragment = (
        noise_fragment_me
        and best_me_any < 0.18
        and audit_verdict == "probable_transcript_error"
        and audit_label in {"asr_noise", "remote_leak", "uncertain"}
        and local_support <= 20
    )
    short_remote_leak_rejected = (
        very_short_me
        and len(me_tokens) >= 2
        and best_me_any < 0.32
        and audit_verdict == "probable_transcript_error"
        and audit_label in {"remote_leak", "asr_noise", "uncertain"}
        and remote_similarity >= 60
        and mic_content_tokens >= 4
        and remote_source_tokens >= 4
    )
    remote_contains_short_me = (
        short_content_me
        and len(me_tokens) >= 2
        and best_me_any < 0.46
        and audit_verdict == "probable_transcript_error"
        and audit_label in {"remote_leak", "asr_noise", "uncertain"}
        and local_support <= 35
        and remote_similarity >= 60
        and remote_source_to_me >= 0.80
        and remote_source_to_me_containment >= 0.90
    )
    short_remote_leak_unconfirmed = (
        short_content_me
        and len(me_tokens) >= 2
        and best_me_any < 0.43
        and audit_verdict == "probable_transcript_error"
        and audit_label == "remote_leak"
        and local_support <= 20
        and mic_content_tokens >= 4
        and best_remote_in_mic >= 0.35
    )

    if me_confirmed and remote_confirmed and best_remote_in_mic < 0.68:
        label = "confirm_timing_or_doubletalk" if remote_text else "confirm_me"
        suggested = "keep_me"
        confidence = min(0.92, max(0.78, best_me_any + 0.25))
        reasons.append(f"mic confirms Me via {best_me_any_source or best_me_source}")
        if remote_text:
            reasons.append("remote track confirms Colleagues; overlap is likely timing/double-talk")
    elif me_confirmed:
        label = "confirm_me"
        suggested = "keep_me"
        confidence = min(0.90, max(0.75, best_me_any + 0.20))
        reasons.append(f"mic confirms Me via {best_me_any_source or best_me_source}")
    elif short_me_contained and local_support >= 50 and best_remote_in_mic < 0.68:
        label = "confirm_me"
        suggested = "keep_me"
        confidence = 0.78
        reasons.append(f"short Me phrase is contained in mic decode via {short_me_source}")
    elif remote_contains_short_me:
        label = "confirm_remote_duplicate"
        suggested = "drop_me"
        confidence = 0.90
        reasons.append("remote track contains the short Me text while mic decodes do not confirm it")
    elif remote_duplicate:
        label = "confirm_remote_duplicate"
        suggested = "drop_me"
        confidence = min(0.95, max(0.82, best_remote_in_mic + 0.12))
        reasons.append(f"mic resembles remote via {remote_in_mic_source}")
    elif mic_rejects_noise_fragment:
        label = "confirm_asr_noise"
        suggested = "drop_me"
        confidence = 0.88
        reasons.append("non-word short Me fragment is rejected by mic decodes")
    elif short_remote_leak_rejected:
        label = "confirm_asr_noise"
        suggested = "drop_me"
        confidence = 0.90
        reasons.append("short Me text is rejected by mic decodes while audio review points to remote leak")
    elif short_remote_leak_unconfirmed:
        label = "confirm_asr_noise"
        suggested = "drop_me"
        confidence = 0.88
        reasons.append("short Me text is unconfirmed by mic decodes under low local support remote-leak evidence")
    elif very_short_me and no_mic_me and local_support < 45 and audit_label in {"uncertain", "asr_noise", "remote_leak"}:
        label = "confirm_asr_noise"
        suggested = "drop_me"
        confidence = 0.82 if audit_verdict != "probable_transcript_error" else 0.88
        reasons.append("short Me text is not confirmed by mic decodes")
    else:
        label = "uncertain"
        suggested = "needs_review"
        confidence = max(0.0, min(0.69, max(best_me_any, best_remote_in_mic, remote_source_to_remote)))
        reasons.append("faster-whisper evidence is weak or conflicting")

    if label in {"confirm_remote_duplicate", "confirm_asr_noise"} and any(
        marker in normalize_text(me_text)
        for marker in ("надо", "нужно", "решили", "договорились", "сделаю", "давай", "проверь")
    ):
        label = "uncertain"
        suggested = "needs_review"
        confidence = min(confidence, 0.69)
        reasons.append("protected work marker prevents automatic drop suggestion")

    return {
        "label": label,
        "suggested_decision": suggested,
        "confidence": round(confidence, 3),
        "reason": "; ".join(reasons),
        "scores": {
            "best_me_similarity": round(best_me, 6),
            "best_me_any_similarity": round(best_me_any, 6),
            "best_remote_in_mic_similarity": round(best_remote_in_mic, 6),
            "remote_source_to_remote_similarity": round(remote_source_to_remote, 6),
            "remote_source_to_me_similarity": round(remote_source_to_me, 6),
            "remote_source_to_me_containment": round(remote_source_to_me_containment, 6),
            "mic_content_tokens": mic_content_tokens,
            "me_content_tokens": len(me_tokens),
            "remote_content_tokens": len(remote_tokens),
            "audio_review_local_support": local_support,
            "audio_review_remote_similarity": remote_similarity,
        },
        "best_sources": {
            "me": best_me_source,
            "me_any": best_me_any_source,
            "remote_in_mic": remote_in_mic_source,
        },
    }


def audit_item(model: Any, item: dict[str, Any], audit_row: dict[str, Any] | None, args: argparse.Namespace) -> dict[str, Any]:
    sources = selected_sources(args)
    clips = item.get("clips") if isinstance(item.get("clips"), dict) else {}
    transcripts: dict[str, dict[str, Any]] = {}
    for source in sources:
        path_value = clips.get(source)
        if not path_value:
            continue
        transcripts[source] = transcribe_clip(model, Path(path_value), args)
    me_text, remote_text = utterance_texts(item)
    metrics = source_metrics(transcripts, me_text, remote_text)
    classification = classify_item(item, audit_row, transcripts, metrics)
    return {
        "schema": SCHEMA_ROW,
        "id": f"fwj_{str(item.get('id') or '').replace('arp_', '')}",
        "source_pack_item_id": item.get("id"),
        "source_pack_item_fingerprint": item_fingerprint(item),
        "session_id": item.get("session_id"),
        "profile": item.get("profile") or args.profile,
        "interval": item.get("interval"),
        "source_reasons": item.get("source_reasons") or [],
        "utterance_ids": item_utterance_ids(item),
        "utterances": item.get("utterances") or [],
        "audio_review_classification": (audit_row or {}).get("classification"),
        "audio_review_scores": (audit_row or {}).get("scores"),
        "clips": clips,
        "transcripts": transcripts,
        "text_metrics": metrics,
        "classification": classification,
    }


def summarize(rows: list[dict[str, Any]], *, model_path: Path, pack_summary: dict[str, Any] | None, skipped_reason: str | None = None) -> dict[str, Any]:
    by_label: dict[str, dict[str, Any]] = {}
    by_suggested: dict[str, dict[str, Any]] = {}
    for row in rows:
        duration = safe_float((row.get("interval") or {}).get("duration_sec"), 0.0)
        classification = row.get("classification") if isinstance(row.get("classification"), dict) else {}
        label = str(classification.get("label") or "unknown")
        suggested = str(classification.get("suggested_decision") or "unknown")
        for bucket, key in ((by_label, label), (by_suggested, suggested)):
            value = bucket.setdefault(key, {"count": 0, "seconds": 0.0})
            value["count"] += 1
            value["seconds"] += duration
    for bucket in list(by_label.values()) + list(by_suggested.values()):
        bucket["seconds"] = round(float(bucket["seconds"]), 3)
    closed = sum(
        safe_float((row.get("interval") or {}).get("duration_sec"), 0.0)
        for row in rows
        if (row.get("classification") or {}).get("label") in {"confirm_me", "confirm_timing_or_doubletalk"}
    )
    drops = sum(
        safe_float((row.get("interval") or {}).get("duration_sec"), 0.0)
        for row in rows
        if (row.get("classification") or {}).get("label") in {"confirm_remote_duplicate", "confirm_asr_noise"}
    )
    return {
        "schema": SCHEMA_SUMMARY,
        "generator": {"name": "audit-stronger-audio-judge", "version": SCRIPT_VERSION},
        "model": str(model_path),
        "input_pack": pack_summary or {},
        "items": len(rows),
        "by_label": dict(sorted(by_label.items())),
        "by_suggested_decision": dict(sorted(by_suggested.items())),
        "suggested_keep_me_seconds": round(closed, 3),
        "suggested_drop_me_seconds": round(drops, 3),
        "skipped_reason": skipped_reason,
        "recommended_next_step": (
            "build_review_lane_pack_with_suggested_answers"
            if rows
            else "no_stronger_audio_judge_rows"
            if not skipped_reason
            else "install_or_download_faster_whisper_model"
        ),
    }


def cached_rows_for_items(
    out_dir: Path,
    items: list[dict[str, Any]],
    *,
    disabled: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    if disabled:
        return [], items, 0
    cached_by_pack_id = {
        str(row.get("source_pack_item_id") or ""): row
        for row in read_jsonl(out_dir / "faster_whisper_judge.jsonl")
        if row.get("source_pack_item_id")
    }
    cached: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for item in items:
        pack_id = str(item.get("id") or "")
        row = cached_by_pack_id.get(pack_id)
        if row and cached_row_matches_item(row, item):
            cached.append(row)
        else:
            missing.append(item)
    return cached, missing, len(cached)


def valid_existing_rows_by_pack_id(out_dir: Path, items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_item_id = {str(item.get("id") or ""): item for item in items if item.get("id")}
    rows: dict[str, dict[str, Any]] = {}
    for row in read_jsonl(out_dir / "faster_whisper_judge.jsonl"):
        pack_id = str(row.get("source_pack_item_id") or "")
        item = by_item_id.get(pack_id)
        if item and cached_row_matches_item(row, item):
            rows[pack_id] = row
    return rows


def write_report(path: Path, summary: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    lines = [
        "# Faster Whisper Judge",
        "",
        "This report uses a local faster-whisper model over existing audio-review clips. It does not edit transcripts.",
        "",
        "## Summary",
        "",
        f"- Items: `{summary['items']}`",
        f"- Selected items: `{summary.get('selected_items', 0)}`",
        f"- Computed items: `{summary.get('computed_items', 0)}`",
        f"- Cached items: `{summary.get('cached_items', 0)}`",
        f"- Still pending selected items: `{summary.get('pending_selected_items_after_cap', 0)}`",
        f"- Sources: `{', '.join(summary.get('sources') or [])}`",
        f"- Suggested keep seconds: `{summary['suggested_keep_me_seconds']}`",
        f"- Suggested drop seconds: `{summary['suggested_drop_me_seconds']}`",
        f"- Recommended next step: `{summary['recommended_next_step']}`",
        "",
        "## By Label",
        "",
    ]
    if summary.get("skipped_reason"):
        lines.extend(["", f"Skipped: `{summary['skipped_reason']}`", ""])
    for label, bucket in summary.get("by_label", {}).items():
        lines.append(f"- `{label}`: `{bucket['count']}` items, `{bucket['seconds']}` sec")
    lines.extend(["", "## Examples", ""])
    ordered = sorted(rows, key=lambda row: -safe_float((row.get("classification") or {}).get("confidence"), 0.0))
    for row in ordered[:20]:
        classification = row.get("classification") or {}
        interval = row.get("interval") or {}
        lines.extend(
            [
                f"### {row.get('source_pack_item_id')} {interval.get('start_time') or format_time(safe_float(interval.get('start')))}-{interval.get('end_time') or format_time(safe_float(interval.get('end')))}",
                "",
                f"- Label: `{classification.get('label')}`",
                f"- Suggested: `{classification.get('suggested_decision')}`",
                f"- Confidence: `{classification.get('confidence')}`",
                f"- Reason: {classification.get('reason')}",
            ]
        )
        for utterance in row.get("utterances") or []:
            role = utterance.get("role") or utterance.get("source_track") or "?"
            lines.append(f"- {role} `{utterance.get('id')}`: {utterance.get('text')}")
        transcripts = row.get("transcripts") if isinstance(row.get("transcripts"), dict) else {}
        for source in DEFAULT_SOURCES:
            result = transcripts.get(source)
            if isinstance(result, dict):
                lines.append(f"- {source}: `{str(result.get('text') or '').strip()}`")
        lines.append("")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    session = args.session.expanduser()
    pack_dir = args.pack_dir or session / "derived/audit/audio-review-pack"
    out_dir = args.out_dir or pack_dir
    items = read_jsonl(pack_dir / "review_pack_items.jsonl")
    lane_items, lane_missing_files = synthetic_lane_pack_items(args, session, out_dir)
    if lane_items:
        existing_ids = {str(item.get("id") or "") for item in items}
        items.extend(item for item in lane_items if str(item.get("id") or "") not in existing_ids)
    audio_review_rows = audit_by_id(read_jsonl(pack_dir / "audio_review_audit.jsonl"))
    pack_summary = read_json(pack_dir / "review_pack_summary.json")
    model_path = resolve_model(args)
    ready, ready_reason = model_ready(model_path)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not ready:
        summary = summarize([], model_path=model_path, pack_summary=pack_summary, skipped_reason=ready_reason)
        write_jsonl(out_dir / "faster_whisper_judge.jsonl", [])
        write_json(out_dir / "faster_whisper_judge_summary.json", summary)
        write_report(out_dir / "faster_whisper_judge_report.md", summary, [])
        print(f"stronger_audio_judge: skipped ({ready_reason})")
        print(f"summary: {out_dir / 'faster_whisper_judge_summary.json'}")
        return 0

    if not items:
        summary = summarize([], model_path=model_path, pack_summary=pack_summary)
        write_jsonl(out_dir / "faster_whisper_judge.jsonl", [])
        write_json(out_dir / "faster_whisper_judge_summary.json", summary)
        write_report(out_dir / "faster_whisper_judge_report.md", summary, [])
        print("stronger_audio_judge: no review pack items")
        print(f"summary: {out_dir / 'faster_whisper_judge_summary.json'}")
        return 0

    matched_audio_review_rows = {
        str(item.get("id") or ""): row
        for item in items
        if (row := audio_review_rows.get(str(item.get("id") or ""))) and audit_row_matches_item(row, item)
    }
    requested_target_ids, missing_lane_pack_files, lane_pack_selector_keys = target_item_ids(args, items)
    missing_lane_pack_files = list(dict.fromkeys(missing_lane_pack_files + lane_missing_files))
    selected = selected_items(items, matched_audio_review_rows, args.max_items, target_ids=requested_target_ids)
    missing_target_ids = [item_id for item_id in requested_target_ids if item_id not in {str(item.get("id") or "") for item in items}]
    cached_rows, missing_items, cached_count = cached_rows_for_items(out_dir, selected, disabled=args.no_cache)
    pending_selected_count = len(missing_items)
    if args.max_computed_items > 0 and len(missing_items) > args.max_computed_items:
        missing_items = missing_items[: args.max_computed_items]
    cached_by_pack_id = {str(row.get("source_pack_item_id") or ""): row for row in cached_rows}
    if missing_items:
        progress(
            args,
            (
                f"selected={len(selected)} cached={cached_count} "
                f"pending={pending_selected_count} computing={len(missing_items)} "
                f"sources={','.join(selected_sources(args))}"
            ),
        )
        progress(args, f"loading faster-whisper model: {model_path}")
        model = load_model(model_path, args)
        progress(args, "model loaded")
        new_by_pack_id: dict[str, dict[str, Any]] = {}
        for index, item in enumerate(missing_items, start=1):
            item_id = str(item.get("id") or "")
            duration = safe_float((item.get("interval") or {}).get("duration_sec"), 0.0)
            progress(args, f"decode {index}/{len(missing_items)} {item_id} ({duration:.2f}s)")
            new_by_pack_id[item_id] = audit_item(model, item, matched_audio_review_rows.get(item_id), args)
            classification = new_by_pack_id[item_id].get("classification") or {}
            progress(
                args,
                (
                    f"done {index}/{len(missing_items)} {item_id}: "
                    f"{classification.get('label')} -> {classification.get('suggested_decision')} "
                    f"confidence={classification.get('confidence')}"
                ),
            )
    else:
        progress(args, f"all selected items are cached ({cached_count}/{len(selected)})")
        new_by_pack_id = {}
    selected_rows: list[dict[str, Any]] = []
    for item in selected:
        item_id = str(item.get("id") or "")
        row = new_by_pack_id.get(item_id) or cached_by_pack_id.get(item_id)
        if row:
            selected_rows.append(row)
    merged_by_pack_id = valid_existing_rows_by_pack_id(out_dir, items)
    for row in selected_rows:
        if row.get("source_pack_item_id"):
            merged_by_pack_id[str(row["source_pack_item_id"])] = row
    rows = [merged_by_pack_id[str(item.get("id") or "")] for item in items if str(item.get("id") or "") in merged_by_pack_id]
    summary = summarize(rows, model_path=model_path, pack_summary=pack_summary)
    summary["cached_items"] = cached_count
    summary["computed_items"] = len(missing_items)
    summary["selected_items"] = len(selected)
    summary["pending_selected_items_before_cap"] = pending_selected_count
    summary["pending_selected_items_after_cap"] = max(0, pending_selected_count - len(missing_items))
    summary["sources"] = list(selected_sources(args))
    summary["quick"] = bool(args.quick)
    summary["max_computed_items"] = args.max_computed_items
    summary["target_item_ids"] = requested_target_ids
    summary["missing_target_item_ids"] = missing_target_ids
    summary["missing_review_lane_pack_files"] = missing_lane_pack_files
    summary["review_lane_pack_selector_keys"] = lane_pack_selector_keys
    write_jsonl(out_dir / "faster_whisper_judge.jsonl", rows)
    write_json(out_dir / "faster_whisper_judge_summary.json", summary)
    write_report(out_dir / "faster_whisper_judge_report.md", summary, rows)
    print(f"items: {len(rows)}")
    print(f"cached_items: {cached_count}")
    print(f"computed_items: {len(missing_items)}")
    print(f"pending_selected_items_after_cap: {summary['pending_selected_items_after_cap']}")
    print(f"sources: {', '.join(selected_sources(args))}")
    if requested_target_ids:
        print(f"target_items: {len(selected)}/{len(requested_target_ids)}")
    if missing_target_ids:
        print(f"missing_target_items: {', '.join(missing_target_ids)}")
    print(f"summary: {out_dir / 'faster_whisper_judge_summary.json'}")
    print(f"report: {out_dir / 'faster_whisper_judge_report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
