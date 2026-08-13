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
SCRIPT_VERSION = "0.1.11"
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
KNOWN_HALLUCINATION_RE = re.compile(
    r"^(?:редактор субтитров|продолжение следует|спасибо за просмотр|субтитры.*)$",
    re.IGNORECASE,
)


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
        "--cached-only",
        action="store_true",
        help="Do not decode missing clips; only rewrite reports from cached rows matching selected items.",
    )
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
        "review_features": (
            lane_item.get("review_features")
            if isinstance(lane_item.get("review_features"), dict)
            else {}
        ),
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
                    "review_features": (
                        item.get("review_features")
                        if isinstance(item.get("review_features"), dict)
                        else {}
                    ),
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


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    write_text_atomic(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    text = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    write_text_atomic(path, text)


def normalize_text(value: Any) -> str:
    text = str(value or "").lower().replace("ё", "е")
    text = re.sub(r"[^0-9a-zа-я_./+-]+", " ", text)
    return " ".join(text.split())


def content_tokens(value: Any) -> list[str]:
    return [token for token in normalize_text(value).split() if token not in STOP_WORDS and len(token) > 1]


def is_known_hallucination(value: Any) -> bool:
    text = str(value or "").lower().replace("ё", "е")
    text = " ".join(re.sub(r"[^0-9a-zа-я]+", " ", text).split())
    return bool(KNOWN_HALLUCINATION_RE.fullmatch(text))


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


def phrase_window_similarity(needle: Any, haystack: Any) -> float:
    needle_tokens = normalize_text(needle).split()
    haystack_tokens = normalize_text(haystack).split()
    if not needle_tokens or not haystack_tokens or len(needle_tokens) > 5:
        return 0.0
    best = 0.0
    for size in range(max(1, len(needle_tokens) - 1), min(len(haystack_tokens), len(needle_tokens) + 1) + 1):
        for start in range(0, len(haystack_tokens) - size + 1):
            window = haystack_tokens[start : start + size]
            best = max(
                best,
                SequenceMatcher(None, " ".join(needle_tokens), " ".join(window)).ratio(),
            )
    return round(best, 6)


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(number) or math.isinf(number):
        return default
    return number


def interval_speaker_state_evidence(
    rows: list[dict[str, Any]],
    interval: dict[str, Any] | None,
) -> dict[str, Any]:
    interval = interval if isinstance(interval, dict) else {}
    start = safe_float(interval.get("start"), -1.0)
    end = safe_float(interval.get("end"), -1.0)
    duration = max(0.0, end - start)
    if start < 0 or duration <= 0:
        return {
            "available": False,
            "reason": "invalid_interval",
            "interval_sec": round(duration, 3),
            "covered_sec": 0.0,
            "coverage_ratio": 0.0,
            "remote_only_ratio": 0.0,
            "local_active_ratio": 0.0,
            "double_talk_ratio": 0.0,
            "states": {},
        }

    covered = 0.0
    remote_only = 0.0
    local_only = 0.0
    double_talk = 0.0
    silence = 0.0
    local_scores: list[tuple[float, float]] = []
    states: Counter[str] = Counter()
    for row in rows:
        row_start = safe_float(row.get("start"), -1.0)
        row_end = safe_float(row.get("end"), -1.0)
        overlap = max(0.0, min(end, row_end) - max(start, row_start))
        if overlap <= 0:
            continue
        state = str(row.get("state") or "unknown")
        states[state] += overlap
        covered += overlap
        if state.startswith("remote_only"):
            remote_only += overlap
        elif state.startswith("local_only"):
            local_only += overlap
        elif state == "double_talk":
            double_talk += overlap
        elif state == "silence":
            silence += overlap
        if row.get("local_score") is not None:
            local_scores.append((safe_float(row.get("local_score")), overlap))

    denominator = max(covered, 1e-9)
    local_active = local_only + double_talk
    local_score_weight = sum(weight for _, weight in local_scores)
    local_score_mean = (
        sum(score * weight for score, weight in local_scores) / local_score_weight
        if local_score_weight > 0
        else None
    )
    return {
        "available": covered > 0,
        "reason": "speaker_state_overlap" if covered > 0 else "speaker_state_not_covered",
        "interval_sec": round(duration, 3),
        "covered_sec": round(covered, 3),
        "coverage_ratio": round(min(1.0, covered / duration), 6),
        "remote_only_ratio": round(remote_only / denominator, 6),
        "local_only_ratio": round(local_only / denominator, 6),
        "local_active_ratio": round(local_active / denominator, 6),
        "double_talk_ratio": round(double_talk / denominator, 6),
        "silence_ratio": round(silence / denominator, 6),
        "local_score_mean": round(local_score_mean, 6) if local_score_mean is not None else None,
        "local_score_max": round(max((score for score, _ in local_scores), default=0.0), 6),
        "states": {key: round(value, 3) for key, value in sorted(states.items())},
    }


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


def cached_row_matches_item(row: dict[str, Any], item: dict[str, Any], sources: tuple[str, ...] = ()) -> bool:
    expected = item_fingerprint(item)
    actual = str(row.get("source_pack_item_fingerprint") or "")
    fingerprint_matches = actual == expected if actual else item_fingerprint_payload(row) == item_fingerprint_payload(item)
    if not fingerprint_matches:
        return False
    row_sources = {str(value) for value in row.get("sources") or [] if value}
    if not row_sources:
        transcripts = row.get("transcripts") if isinstance(row.get("transcripts"), dict) else {}
        row_sources = {str(source) for source, value in transcripts.items() if isinstance(value, dict)}
    return not sources or set(sources) <= row_sources


def item_matches_legacy_fingerprint(row: dict[str, Any], item: dict[str, Any]) -> bool:
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
    model_options: dict[str, Any] = {
        "device": args.device,
        "compute_type": args.compute_type,
    }
    thread_limit = int(os.environ.get("MURMURMARK_MAX_COMPUTE_THREADS") or 0)
    if args.device == "cpu" and thread_limit > 0:
        model_options["cpu_threads"] = thread_limit
        model_options["num_workers"] = 1
    return WhisperModel(str(model_path), **model_options)


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
    decoded_remote_text = str(transcripts.get("remote", {}).get("text") or "")
    if is_known_hallucination(decoded_remote_text):
        decoded_remote_text = ""
    remote_reference_text = remote_text or decoded_remote_text
    for source, result in transcripts.items():
        raw_text = str(result.get("text") or "")
        hallucination = is_known_hallucination(raw_text)
        text = "" if hallucination else raw_text
        metrics[source] = {
            "text_len": len(text),
            "content_token_count": len(content_tokens(text)),
            "known_hallucination": hallucination,
            "to_me": text_similarity(text, me_text),
            "to_remote": text_similarity(text, remote_reference_text),
            "to_decoded_remote": text_similarity(text, decoded_remote_text),
            "avg_logprob": result.get("avg_logprob"),
            "no_speech_prob": result.get("no_speech_prob"),
        }
    return metrics


def group_overlap_contexts(item: dict[str, Any]) -> list[dict[str, Any]]:
    contexts = item.get("source_contexts")
    if not isinstance(contexts, list):
        return []
    return [
        context
        for context in contexts
        if isinstance(context, dict) and context.get("type") == "group_overlap_audit"
    ]


def group_timing_overlap_support(item: dict[str, Any]) -> dict[str, Any] | None:
    for context in group_overlap_contexts(item):
        classification = context.get("classification") if isinstance(context.get("classification"), dict) else {}
        scores = context.get("scores") if isinstance(context.get("scores"), dict) else {}
        label = str(classification.get("label") or "")
        confidence = safe_float(classification.get("confidence"), 0.0)
        local_evidence = safe_float(scores.get("local_evidence"), 0.0)
        remote_evidence = safe_float(scores.get("remote_evidence"), 0.0)
        audio_leak = safe_float(scores.get("audio_leak"), 0.0)
        text_duplicate = safe_float(scores.get("text_duplicate"), 0.0)
        if (
            label == "probable_timing_overlap"
            and confidence >= 0.74
            and local_evidence >= 70
            and remote_evidence <= 10
            and audio_leak <= 10
            and text_duplicate <= 20
        ):
            return context
    return None


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
    speaker_state: dict[str, Any] | None = None,
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
    best_decoded_remote_in_mic, decoded_remote_in_mic_source = best_score(
        metrics,
        mic_sources,
        "to_decoded_remote",
    )
    best_me_source_no_speech = safe_float(
        metrics.get(best_me_any_source or best_me_source, {}).get("no_speech_prob"),
        0.0,
    )
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
    group_timing_context = group_timing_overlap_support(item)
    group_contexts = group_overlap_contexts(item)
    group_local_evidence = max(
        (safe_float((context.get("scores") or {}).get("local_evidence"), 0.0) for context in group_contexts),
        default=0.0,
    )
    group_remote_evidence = max(
        (safe_float((context.get("scores") or {}).get("remote_evidence"), 0.0) for context in group_contexts),
        default=0.0,
    )
    group_audio_leak = max(
        (safe_float((context.get("scores") or {}).get("audio_leak"), 0.0) for context in group_contexts),
        default=0.0,
    )
    speaker_state = speaker_state if isinstance(speaker_state, dict) else {}
    state_coverage = safe_float(speaker_state.get("coverage_ratio"), 0.0)
    state_remote_only = safe_float(speaker_state.get("remote_only_ratio"), 0.0)
    state_local_active = safe_float(speaker_state.get("local_active_ratio"), 0.0)
    state_double_talk = safe_float(speaker_state.get("double_talk_ratio"), 0.0)
    state_silence = safe_float(speaker_state.get("silence_ratio"), 0.0)
    review_features = item.get("review_features") if isinstance(item.get("review_features"), dict) else {}
    unsupported_micro_fallback = review_features.get("unsupported_micro_asr_fallback") is True
    unstable_micro_success = review_features.get("unstable_micro_asr_success") is True
    micro_selection_reasons = {
        str(value) for value in review_features.get("micro_asr_selection_review_reasons") or [] if value
    }
    canonical_mic_sources = {"mic_role_masked", "mic_clean", "mic_raw"}
    decoded_mic_sources = canonical_mic_sources & set(transcripts)
    mic_sources_empty_or_hallucinated = decoded_mic_sources == canonical_mic_sources and all(
        not normalize_text((transcripts.get(source) or {}).get("text"))
        or bool((metrics.get(source) or {}).get("known_hallucination"))
        for source in canonical_mic_sources
    )
    unsupported_silence_noise = (
        unsupported_micro_fallback
        and very_short_me
        and state_coverage >= 0.80
        and state_silence >= 0.90
        and state_local_active <= 0.05
        and mic_sources_empty_or_hallucinated
    )
    unstable_micro_remote_artifact = (
        unstable_micro_success
        and bool(
            micro_selection_reasons
            & {
                "implausible_short_island_speech_rate",
                "baseline_only_selection_without_canonical_support",
                "short_island_source_disagreement",
            }
        )
        and state_coverage >= 0.80
        and state_remote_only >= 0.90
        and state_local_active <= 0.05
        and remote_source_to_remote >= 0.65
        and best_decoded_remote_in_mic >= 0.80
        and best_me_any < 0.58
    )
    unstable_micro_rejected_text = (
        unstable_micro_success
        and "baseline_only_selection_without_canonical_support" in micro_selection_reasons
        and state_coverage >= 0.80
        and state_remote_only >= 0.90
        and state_local_active <= 0.05
        and remote_source_tokens >= 3
        and best_me_any < 0.45
    )
    unstable_micro_remote_only_veto = (
        unstable_micro_success
        and state_coverage >= 0.80
        and state_remote_only >= 0.90
        and state_local_active <= 0.05
    )
    remote_only_keep_veto = (
        state_coverage >= 0.80
        and state_remote_only >= 0.80
        and state_local_active <= 0.10
        and group_local_evidence < 50
        and best_remote_in_mic >= 0.70
    )
    remote_dominant_mic_decode_keep_veto = (
        me_confirmed
        and remote_source_tokens >= 4
        and mic_content_tokens >= 4
        and best_decoded_remote_in_mic >= 0.82
        and best_decoded_remote_in_mic - best_me_any >= 0.18
        and remote_source_to_remote >= 0.65
        and state_local_active <= 0.10
        and (
            group_audio_leak >= 70
            or (
                state_coverage >= 0.80
                and local_support <= 10
                and best_remote_in_mic >= 0.90
            )
        )
    )
    decoded_remote_to_me = text_similarity(str(transcripts.get("remote", {}).get("text") or ""), me_text)
    decoded_remote_phrase_match = phrase_window_similarity(
        me_text, str(transcripts.get("remote", {}).get("text") or "")
    )
    remote_contains_me_veto = (
        audit_verdict != "probable_transcript_error"
        and len(me_tokens) >= 3
        and remote_source_tokens >= len(me_tokens)
        and remote_source_to_me >= 0.90
        and remote_source_to_me_containment >= 0.90
        and best_decoded_remote_in_mic >= 0.75
    )
    direct_decoded_remote_duplicate = (
        audit_verdict == "probable_transcript_error"
        and audit_label in {"remote_duplicate", "remote_leak"}
        and local_support <= 35
        and remote_similarity >= 70
        and best_decoded_remote_in_mic >= 0.80
        and safe_float(decoded_remote_to_me.get("similarity"), 0.0) >= 0.60
        and remote_source_tokens >= max(4, mic_content_tokens - 2)
        and group_local_evidence <= 35
        and group_remote_evidence >= 40
        and group_audio_leak >= 40
    )
    short_decoded_remote_duplicate = (
        short_content_me
        and 2 <= len(me_tokens) <= 3
        and best_me_any < 0.40
        and local_support <= 40
        and state_coverage >= 0.80
        and state_local_active <= 0.05
        and remote_source_to_remote >= 0.80
        and remote_source_tokens >= 4
        and decoded_remote_phrase_match >= 0.88
    )
    remote_only_decoded_duplicate = (
        short_content_me
        and 2 <= len(me_tokens) <= 3
        and state_coverage >= 0.80
        and state_remote_only >= 0.90
        and state_local_active <= 0.05
        and remote_source_to_remote >= 0.80
        and remote_source_tokens >= 4
        and decoded_remote_phrase_match >= 0.88
        and remote_similarity >= 70
        and best_me_source_no_speech >= 0.55
    )
    single_token_remote_only_duplicate = (
        len(me_tokens) == 1
        and len(me_tokens[0]) >= 5
        and state_coverage >= 0.80
        and state_remote_only >= 0.90
        and state_local_active <= 0.05
        and remote_source_to_remote >= 0.80
        and remote_source_tokens >= 4
        and decoded_remote_phrase_match >= 0.95
        and local_support <= 10
        and best_remote_in_mic >= 0.60
        and best_me_source_no_speech >= 0.50
    )
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
    remote_leak_behaves_like_duplicate = (
        short_content_me
        and len(me_tokens) >= 2
        and best_me_any < 0.45
        and audit_verdict == "probable_transcript_error"
        and audit_label == "remote_leak"
        and local_support <= 45
        and remote_similarity >= 65
        and best_remote_in_mic >= 0.58
        and remote_source_to_remote >= 0.80
        and remote_source_to_me >= 0.68
        and remote_source_to_me_containment >= 0.60
        and mic_content_tokens >= 2
    )
    lost_me_behaves_like_remote_artifact = (
        short_content_me
        and audit_verdict == "probable_transcript_error"
        and audit_label == "lost_me"
        and local_support <= 20
        and best_remote_in_mic >= 0.85
        and remote_source_to_remote >= 0.80
        and remote_source_tokens >= 3
        and mic_content_tokens >= 3
        and best_me < 0.12
        and (
            best_remote_in_mic - best_me_any >= 0.45
            or noise_fragment_me
            or len(me_tokens) <= 1
        )
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

    if unsupported_silence_noise:
        label = "confirm_asr_noise"
        suggested = "drop_me"
        confidence = 0.92
        reasons.append(
            "three-source micro-ASR fallback and full-source stronger judge reject the short Me text; "
            "speaker_state confirms silence"
        )
    elif unstable_micro_remote_artifact:
        label = "confirm_remote_duplicate"
        suggested = "drop_me"
        confidence = min(0.92, max(0.88, best_decoded_remote_in_mic + 0.04))
        reasons.append(
            "short micro-ASR selection is unstable, speaker_state is remote-only, and decoded mic "
            "is explained by decoded remote rather than the proposed Me text"
        )
    elif unstable_micro_rejected_text:
        label = "confirm_asr_noise"
        suggested = "drop_me"
        confidence = 0.88
        reasons.append(
            "baseline-only micro-ASR text is rejected by canonical mic decodes while speaker_state "
            "is remote-only"
        )
    elif direct_decoded_remote_duplicate:
        label = "confirm_remote_duplicate"
        suggested = "drop_me"
        confidence = min(0.92, max(0.88, best_decoded_remote_in_mic + 0.08))
        reasons.append(
            f"decoded mic matches decoded remote directly via {decoded_remote_in_mic_source}; "
            "group evidence is remote/leak dominant"
        )
    elif short_decoded_remote_duplicate:
        label = "confirm_remote_duplicate"
        suggested = "drop_me"
        confidence = 0.90
        reasons.append(
            "short Me fragment is absent from local state and fuzzy-contained in decoded remote"
        )
    elif remote_only_decoded_duplicate:
        label = "confirm_remote_duplicate"
        suggested = "drop_me"
        confidence = 0.91
        reasons.append(
            "short Me fragment is fuzzy-contained in decoded remote while speaker_state is remote-only "
            "and the matching mic decode has high no-speech probability"
        )
    elif single_token_remote_only_duplicate:
        label = "confirm_remote_duplicate"
        suggested = "drop_me"
        confidence = 0.90
        reasons.append(
            "single content-word Me fragment is contained in decoded remote while speaker_state is "
            "remote-only and the matching mic decode has high no-speech probability"
        )
    elif group_timing_context and best_remote_in_mic < 0.58 and remote_duplicate is False:
        label = "confirm_timing_or_doubletalk"
        suggested = "keep_me"
        confidence = max(
            0.78,
            min(0.90, safe_float((group_timing_context.get("classification") or {}).get("confidence"), 0.0) + 0.06),
        )
        reasons.append("group-overlap confirms local-only timing overlap")
        if best_me_any_source:
            reasons.append(f"mic evidence comes from {best_me_any_source}")
    elif remote_contains_me_veto:
        label = "uncertain"
        suggested = "needs_review"
        confidence = 0.88
        reasons.append(
            "remote track fully contains the proposed Me phrase and the mic decode also follows remote; "
            "local speech is not independently confirmed"
        )
    elif remote_only_keep_veto and me_confirmed:
        label = "uncertain"
        suggested = "needs_review"
        confidence = min(0.69, max(0.60, best_remote_in_mic))
        reasons.append(
            "speaker_state is remote-only across the interval and mic follows remote; "
            "decoded mic text cannot independently confirm Me"
        )
    elif remote_dominant_mic_decode_keep_veto:
        label = "uncertain"
        suggested = "needs_review"
        confidence = min(0.88, max(0.72, best_decoded_remote_in_mic))
        reasons.append(
            "mic decode follows decoded remote substantially more closely than the proposed Me text; "
            "strong leak evidence vetoes automatic keep"
        )
    elif unstable_micro_remote_only_veto:
        label = "uncertain"
        suggested = "needs_review"
        confidence = min(0.69, max(0.55, best_decoded_remote_in_mic))
        reasons.append(
            "unstable micro-ASR text occurs in a remote-only interval; leaked mic speech cannot "
            "independently confirm Me"
        )
    elif me_confirmed and remote_confirmed and best_remote_in_mic < 0.68:
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
    elif remote_leak_behaves_like_duplicate:
        label = "confirm_remote_duplicate"
        suggested = "drop_me"
        confidence = 0.88
        reasons.append("remote-leak evidence behaves like a duplicate: mic decode is closer to remote than Me")
    elif lost_me_behaves_like_remote_artifact:
        label = "confirm_asr_noise"
        suggested = "drop_me"
        confidence = 0.90
        reasons.append("lost-Me fragment behaves like remote/noise: mic decode is closer to remote than Me")
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
            "best_decoded_remote_in_mic_similarity": round(best_decoded_remote_in_mic, 6),
            "remote_source_to_remote_similarity": round(remote_source_to_remote, 6),
            "remote_source_to_me_similarity": round(remote_source_to_me, 6),
            "remote_source_to_me_containment": round(remote_source_to_me_containment, 6),
            "mic_content_tokens": mic_content_tokens,
            "me_content_tokens": len(me_tokens),
            "remote_content_tokens": len(remote_tokens),
            "decoded_remote_to_me_similarity": round(safe_float(decoded_remote_to_me.get("similarity"), 0.0), 6),
            "decoded_remote_phrase_match": decoded_remote_phrase_match,
            "group_local_evidence": round(group_local_evidence, 3),
            "group_remote_evidence": round(group_remote_evidence, 3),
            "group_audio_leak": round(group_audio_leak, 3),
            "audio_review_local_support": local_support,
            "audio_review_remote_similarity": remote_similarity,
            "speaker_state_coverage_ratio": round(state_coverage, 6),
            "speaker_state_remote_only_ratio": round(state_remote_only, 6),
            "speaker_state_local_active_ratio": round(state_local_active, 6),
            "speaker_state_double_talk_ratio": round(state_double_talk, 6),
            "speaker_state_silence_ratio": round(state_silence, 6),
            "speaker_state_remote_only_keep_veto": remote_only_keep_veto,
            "remote_dominant_mic_decode_keep_veto": remote_dominant_mic_decode_keep_veto,
            "best_me_source_no_speech_probability": round(best_me_source_no_speech, 6),
            "remote_only_decoded_duplicate": remote_only_decoded_duplicate,
            "single_token_remote_only_duplicate": single_token_remote_only_duplicate,
            "unsupported_micro_asr_fallback": unsupported_micro_fallback,
            "unstable_micro_asr_success": unstable_micro_success,
            "micro_asr_selection_review_reasons": sorted(micro_selection_reasons),
            "unstable_micro_remote_artifact": unstable_micro_remote_artifact,
            "unstable_micro_rejected_text": unstable_micro_rejected_text,
            "unstable_micro_remote_only_veto": unstable_micro_remote_only_veto,
            "mic_sources_empty_or_hallucinated": mic_sources_empty_or_hallucinated,
        },
        "best_sources": {
            "me": best_me_source,
            "me_any": best_me_any_source,
            "remote_in_mic": remote_in_mic_source,
            "decoded_remote_in_mic": decoded_remote_in_mic_source,
        },
    }


def audit_item(
    model: Any,
    item: dict[str, Any],
    audit_row: dict[str, Any] | None,
    args: argparse.Namespace,
    speaker_state_rows: list[dict[str, Any]],
) -> dict[str, Any]:
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
    speaker_state = interval_speaker_state_evidence(speaker_state_rows, item.get("interval"))
    classification = classify_item(item, audit_row, transcripts, metrics, speaker_state)
    return {
        "schema": SCHEMA_ROW,
        "id": f"fwj_{str(item.get('id') or '').replace('arp_', '')}",
        "source_pack_item_id": item.get("id"),
        "source_pack_item_fingerprint": item_fingerprint(item),
        "session_id": item.get("session_id"),
        "profile": item.get("profile") or args.profile,
        "sources": list(sources),
        "interval": item.get("interval"),
        "source_reasons": item.get("source_reasons") or [],
        "review_features": item.get("review_features") or {},
        "utterance_ids": item_utterance_ids(item),
        "utterances": item.get("utterances") or [],
        "audio_review_classification": (audit_row or {}).get("classification"),
        "audio_review_scores": (audit_row or {}).get("scores"),
        "clips": clips,
        "transcripts": transcripts,
        "text_metrics": metrics,
        "speaker_state_evidence": speaker_state,
        "classification": classification,
    }


def refresh_cached_classification(
    row: dict[str, Any],
    item: dict[str, Any],
    audit_row: dict[str, Any] | None,
    speaker_state_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    refreshed = dict(row)
    transcripts = row.get("transcripts") if isinstance(row.get("transcripts"), dict) else {}
    me_text, remote_text = utterance_texts(item)
    metrics = source_metrics(transcripts, me_text, remote_text)
    speaker_state = interval_speaker_state_evidence(speaker_state_rows, item.get("interval"))
    refreshed["text_metrics"] = metrics
    refreshed["speaker_state_evidence"] = speaker_state
    refreshed["review_features"] = item.get("review_features") or {}
    refreshed["classification"] = classify_item(item, audit_row, transcripts, metrics, speaker_state)
    refreshed["classification_policy_version"] = SCRIPT_VERSION
    return refreshed


def refreshed_valid_existing_rows_by_pack_id(
    out_dir: Path,
    items: list[dict[str, Any]],
    sources: tuple[str, ...],
    audio_review_rows: dict[str, dict[str, Any]],
    speaker_state_rows: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    items_by_id = {str(item.get("id") or ""): item for item in items if item.get("id")}
    refreshed: dict[str, dict[str, Any]] = {}
    for pack_id, row in valid_existing_rows_by_pack_id(out_dir, items, sources).items():
        item = items_by_id.get(pack_id)
        if item is None:
            continue
        audit_row = audio_review_rows.get(pack_id)
        if not audit_row_matches_item(audit_row, item):
            audit_row = None
        refreshed[pack_id] = refresh_cached_classification(
            row,
            item,
            audit_row,
            speaker_state_rows,
        )
    return refreshed


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
    sources: tuple[str, ...] = (),
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
        if row and cached_row_matches_item(row, item, sources):
            cached.append(row)
        else:
            missing.append(item)
    return cached, missing, len(cached)


def valid_existing_rows_by_pack_id(
    out_dir: Path,
    items: list[dict[str, Any]],
    sources: tuple[str, ...] = (),
) -> dict[str, dict[str, Any]]:
    by_item_id = {str(item.get("id") or ""): item for item in items if item.get("id")}
    rows: dict[str, dict[str, Any]] = {}
    for row in read_jsonl(out_dir / "faster_whisper_judge.jsonl"):
        pack_id = str(row.get("source_pack_item_id") or "")
        item = by_item_id.get(pack_id)
        if item and cached_row_matches_item(row, item, sources):
            rows[pack_id] = row
    return rows


def existing_rows_by_pack_id(out_dir: Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for row in read_jsonl(out_dir / "faster_whisper_judge.jsonl"):
        pack_id = str(row.get("source_pack_item_id") or "")
        if pack_id:
            rows[pack_id] = row
    return rows


def ordered_rows_for_items(
    items: list[dict[str, Any]],
    rows_by_pack_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    item_ids = [str(item.get("id") or "") for item in items if item.get("id")]
    item_id_set = set(item_ids)
    ordered_ids = item_ids + sorted(pack_id for pack_id in rows_by_pack_id if pack_id not in item_id_set)
    return [rows_by_pack_id[pack_id] for pack_id in ordered_ids if pack_id in rows_by_pack_id]


def write_incremental_checkpoint(
    out_dir: Path,
    items: list[dict[str, Any]],
    rows_by_pack_id: dict[str, dict[str, Any]],
    *,
    model_path: Path,
    pack_summary: dict[str, Any] | None,
    selected_items: int,
    cached_items: int,
    computed_items: int,
    pending_items: int,
    sources: tuple[str, ...],
) -> None:
    rows = ordered_rows_for_items(items, rows_by_pack_id)
    write_jsonl(out_dir / "faster_whisper_judge.jsonl", rows)
    summary = summarize(rows, model_path=model_path, pack_summary=pack_summary)
    summary.update(
        {
            "status": "in_progress",
            "cached_items": max(0, cached_items),
            "computed_items": max(0, computed_items),
            "selected_items": selected_items,
            "pending_selected_items_after_cap": max(0, pending_items),
            "sources": list(sources),
        }
    )
    write_json(out_dir / "faster_whisper_judge_summary.json", summary)


def final_run_status(
    pending_items: int,
    missing_target_ids: list[str],
    missing_lane_pack_files: list[str],
) -> str:
    if pending_items > 0 or missing_target_ids or missing_lane_pack_files:
        return "completed_partial"
    return "completed"


def write_report(path: Path, summary: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    lines = [
        "# Faster Whisper Judge",
        "",
        "This report uses a local faster-whisper model over existing audio-review clips. It does not edit transcripts.",
        "",
        "## Summary",
        "",
        f"- Status: `{summary.get('status', 'unknown')}`",
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
    write_text_atomic(path, "\n".join(lines).rstrip() + "\n")


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
    speaker_state_rows = read_jsonl(session / "derived/preprocess/echo/speaker_state.jsonl")
    pack_summary = read_json(pack_dir / "review_pack_summary.json")
    model_path = resolve_model(args)
    ready, ready_reason = model_ready(model_path)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not ready:
        sources = selected_sources(args)
        rows = ordered_rows_for_items(
            items,
            refreshed_valid_existing_rows_by_pack_id(
                out_dir,
                items,
                sources,
                audio_review_rows,
                speaker_state_rows,
            ),
        )
        summary = summarize(rows, model_path=model_path, pack_summary=pack_summary, skipped_reason=ready_reason)
        summary["status"] = "skipped"
        summary["sources"] = list(sources)
        write_jsonl(out_dir / "faster_whisper_judge.jsonl", rows)
        write_json(out_dir / "faster_whisper_judge_summary.json", summary)
        write_report(out_dir / "faster_whisper_judge_report.md", summary, rows)
        print(f"stronger_audio_judge: skipped ({ready_reason})")
        print(f"summary: {out_dir / 'faster_whisper_judge_summary.json'}")
        return 0

    if not items:
        summary = summarize([], model_path=model_path, pack_summary=pack_summary)
        summary["status"] = "completed"
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
    sources = selected_sources(args)
    cached_rows, missing_items, cached_count = cached_rows_for_items(
        out_dir,
        selected,
        disabled=args.no_cache,
        sources=sources,
    )
    pending_selected_count = len(missing_items)
    if args.cached_only:
        missing_items = []
    elif args.max_computed_items > 0 and len(missing_items) > args.max_computed_items:
        missing_items = missing_items[: args.max_computed_items]
    cached_by_pack_id = {str(row.get("source_pack_item_id") or ""): row for row in cached_rows}
    targeted_run = bool(requested_target_ids or args.review_lane_pack or args.pack_item_id)
    checkpoint_by_pack_id = (
        existing_rows_by_pack_id(out_dir)
        if targeted_run
        else refreshed_valid_existing_rows_by_pack_id(
            out_dir,
            items,
            sources,
            audio_review_rows,
            speaker_state_rows,
        )
    )
    checkpoint_by_pack_id.update(cached_by_pack_id)
    if missing_items:
        progress(
            args,
            (
                f"selected={len(selected)} cached={cached_count} "
                f"pending={pending_selected_count} computing={len(missing_items)} "
                f"sources={','.join(sources)}"
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
            new_by_pack_id[item_id] = audit_item(
                model,
                item,
                matched_audio_review_rows.get(item_id),
                args,
                speaker_state_rows,
            )
            new_by_pack_id[item_id]["classification_policy_version"] = SCRIPT_VERSION
            checkpoint_by_pack_id[item_id] = new_by_pack_id[item_id]
            write_incremental_checkpoint(
                out_dir,
                items,
                checkpoint_by_pack_id,
                model_path=model_path,
                pack_summary=pack_summary,
                selected_items=len(selected),
                cached_items=cached_count,
                computed_items=index,
                pending_items=pending_selected_count - index,
                sources=sources,
            )
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
            selected_rows.append(
                refresh_cached_classification(
                    row,
                    item,
                    matched_audio_review_rows.get(item_id),
                    speaker_state_rows,
                )
            )
    merged_by_pack_id = existing_rows_by_pack_id(out_dir) if targeted_run else {}
    merged_by_pack_id.update(
        refreshed_valid_existing_rows_by_pack_id(
            out_dir,
            items,
            sources,
            audio_review_rows,
            speaker_state_rows,
        )
    )
    for row in selected_rows:
        if row.get("source_pack_item_id"):
            merged_by_pack_id[str(row["source_pack_item_id"])] = row
    rows = ordered_rows_for_items(items, merged_by_pack_id)
    summary = summarize(rows, model_path=model_path, pack_summary=pack_summary)
    summary["cached_items"] = cached_count
    summary["computed_items"] = len(missing_items)
    summary["selected_items"] = len(selected)
    summary["pending_selected_items_before_cap"] = pending_selected_count
    summary["pending_selected_items_after_cap"] = max(0, pending_selected_count - len(missing_items))
    summary["sources"] = list(sources)
    summary["quick"] = bool(args.quick)
    summary["cached_only"] = bool(args.cached_only)
    summary["max_computed_items"] = args.max_computed_items
    summary["target_item_ids"] = requested_target_ids
    summary["missing_target_item_ids"] = missing_target_ids
    summary["missing_review_lane_pack_files"] = missing_lane_pack_files
    summary["review_lane_pack_selector_keys"] = lane_pack_selector_keys
    summary["status"] = final_run_status(
        summary["pending_selected_items_after_cap"],
        missing_target_ids,
        missing_lane_pack_files,
    )
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
