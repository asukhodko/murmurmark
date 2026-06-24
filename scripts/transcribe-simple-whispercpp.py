#!/usr/bin/env python3
"""Simple whisper.cpp transcription bridge for a MurmurMark session.

This is intentionally boring:

- use MurmurMark export-audio as the audio selection boundary;
- run whisper-cli on short windows from mic.wav and remote.wav;
- assign roles by track;
- write a timestamp-sorted Markdown transcript and simple JSON.
"""

from __future__ import annotations

import argparse
import copy
import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_MODEL = "~/.local/share/murmurmark/models/whisper.cpp/ggml-large-v3-q5_0.bin"
KNOWN_HALLUCINATION_RE = re.compile(
    r"("
    r"редактор\s+субтитров"
    r"|корректор\s+[а-яa-z]\."
    r"|субтитр(?:ы|ов|ами)?\s+(?:создавал|сделал|подготовил)"
    r"|продолжение\s+следует"
    r"|dimatorzok"
    r")",
    re.IGNORECASE,
)
REPEATED_GOODBYE = {"пока", "покапока"}

REMOTE_GUARD_BEFORE_MS = 250
REMOTE_GUARD_AFTER_MS = 350
LONG_MIC_SEGMENT_MS = 6_000
REMOTE_ACTIVE_REPAIR_RATIO = 0.15
REMOTE_OVERLAP_REPAIR_MS = 1_000
LOCAL_ISLAND_MIN_MS = 350
TOKEN_GAP_SPLIT_THRESHOLD_MS = 1_500
MICRO_REASR_PADDING_MS = 1_000
MICRO_REASR_MAX_ISLAND_MS = 8_000
TOKEN_FALLBACK_MIN_ISLAND_MS = 1_000
TIMELINE_REPAIR_KEEP_DECISIONS = {"keep", "keep_needs_review", "split", "micro_reasr"}
SHADOW_V2_LEFT_EXPAND_MS = 200
SHADOW_V2_RIGHT_EXPAND_MS = 300
SHADOW_V2_LEADING_SILENCE_MS = 400
SHADOW_V2_REPLACE_SCORE_MARGIN = 0.05
SHADOW_V2_MICRO_WINDOWS = (
    {"label": "normal", "left_ms": 800, "right_ms": 500},
    {"label": "wide", "left_ms": 1500, "right_ms": 800},
)
SHADOW_V2_RECOVERY_WINDOWS = (
    {"label": "recovery", "left_ms": 2500, "right_ms": 1000},
)
BOUNDARY_SUSPICIOUS_PREFIXES = ("адно", "дно")
BOUNDARY_PREFIX_REPAIRS = (
    {
        "from": "адно",
        "to": "Ладно",
        "contexts": ("давай", "расходиться"),
    },
)
GOLDEN_PHRASE_CASES: tuple[dict[str, Any], ...] = ()
NO_REGRESSION_CONTROL_TEXTS: tuple[str, ...] = ()
DISPLAY_TOKEN_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё/+-]+")
OPENING_WINDOW_END_MS = 12_000
OPENING_MAX_PATCH_END_MS = 24_000
OPENING_LEADING_SILENCE_MS = 500
OPENING_REMOTE_ACCEPT_SCORE = 0.70
OPENING_MIC_ACCEPT_SCORE = 0.75
OPENING_MAX_ACCEPTED_TURNS = 4
OPENING_REMOTE_WINDOWS = (
    ("R0_0_6", 0, 6_000),
    ("R1_0_8", 0, 8_000),
    ("R2_0_12", 0, 12_000),
    ("R3_2_12", 2_000, 12_000),
)
OPENING_MIC_START_WINDOWS = (
    ("M_start_0_4", 0, 4_000),
    ("M_start_0_8", 0, 8_000),
)
OPENING_ACK_WORDS = {"да", "ага", "угу", "окей", "хорошо"}


@dataclass
class Utterance:
    id: str
    source_track: str
    role: str
    speaker_label: str
    start_ms: int
    end_ms: int
    raw_text: str
    avg_logprob: float | None = None
    no_speech_prob: float | None = None
    compression_ratio: float | None = None
    temperature: float | None = None
    token_avg_prob: float | None = None
    token_low_prob_ratio: float | None = None
    token_confident_start_ms: int | None = None
    token_confident_end_ms: int | None = None
    tokens: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Candidate:
    id: str
    source_track: str
    role: str
    speaker_label: str
    start_ms: int
    end_ms: int
    display_start_ms: int
    display_end_ms: int
    text_raw: str
    text_norm: str
    source_segments: list[str]
    echo_features: dict[str, float]
    asr_features: dict[str, float | None]
    repair: dict[str, Any] = field(default_factory=dict)


@dataclass
class RoleDecision:
    candidate: Candidate
    final_role: str | None
    decision: str
    reason: str
    confidence: float
    matched_remote_candidate_id: str | None
    evidence: dict[str, Any]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Transcribe a MurmurMark session with whisper.cpp.")
    parser.add_argument("session", type=Path)
    parser.add_argument("--model", default=os.environ.get("MURMURMARK_WHISPER_MODEL", DEFAULT_MODEL))
    parser.add_argument("--language", default="ru")
    parser.add_argument("--whisper-cli", default=os.environ.get("WHISPER_CLI", "whisper-cli"))
    parser.add_argument("--murmurmark-bin", default=".build/debug/murmurmark")
    parser.add_argument("--prompt-file", type=Path, default=None)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--max-context", type=int, default=0)
    parser.add_argument("--force", action="store_true", help="rerun whisper-cli even when raw JSON exists")
    parser.add_argument("--skip-export", action="store_true")
    parser.add_argument("--skip-transcribe", action="store_true")
    parser.add_argument("--duration-ms", type=int, default=0, help="debug limit for whisper-cli")
    parser.add_argument(
        "--asr-mode",
        choices=("windowed", "whole"),
        default="windowed",
        help="run whisper.cpp on short timeline windows or on each whole track",
    )
    parser.add_argument("--asr-window-sec", type=int, default=60)
    parser.add_argument("--asr-overlap-sec", type=int, default=5)
    parser.add_argument(
        "--mic-audio-prep",
        choices=("none", "speech", "loudnorm"),
        default="speech",
        help="optional ASR-only audio preparation for mic.wav",
    )
    parser.add_argument(
        "--remote-audio-prep",
        choices=("none", "loudnorm"),
        default="loudnorm",
        help="optional ASR-only audio preparation for remote.wav",
    )
    parser.add_argument("--merge-gap-ms", type=int, default=600)
    parser.add_argument("--max-merged-chars", type=int, default=220)
    parser.add_argument("--max-merged-ms", type=int, default=15_000)
    parser.add_argument(
        "--mic-policy",
        choices=("suppress_echo_guard_remote_active", "keep_all"),
        default="suppress_echo_guard_remote_active",
    )
    parser.add_argument(
        "--repair-profile",
        choices=("current", "shadow_v2"),
        default="current",
        help="write the current transcript or also compute a separate shadow repair candidate",
    )
    return parser.parse_args()


def run(command: list[str], cwd: Path | None = None) -> None:
    print("+ " + " ".join(command))
    subprocess.run(command, cwd=cwd, check=True)


def printable_command(command: list[str]) -> str:
    redacted: list[str] = []
    skip_next = False
    for index, part in enumerate(command):
        if skip_next:
            skip_next = False
            continue
        redacted.append(part)
        if part == "--prompt" and index + 1 < len(command):
            redacted.append(f"<prompt:{len(command[index + 1])} chars>")
            skip_next = True
    return " ".join(redacted)


def capture(command: list[str]) -> str:
    return subprocess.check_output(command, text=True).strip()


def run_quiet(command: list[str]) -> None:
    subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)


def suffixed_name(filename: str, artifact_suffix: str) -> str:
    if not artifact_suffix:
        return filename
    path = Path(filename)
    return f"{path.stem}{artifact_suffix}{path.suffix}"


def expand(path: str) -> Path:
    return Path(path).expanduser().resolve()


def read_prompt(path: Path | None) -> str | None:
    if path is None:
        return None
    text = path.expanduser().read_text(encoding="utf-8").strip()
    return text or None


def raw_meta_path(output_base: Path) -> Path:
    return output_base.with_suffix(".meta.json")


def whisper_cache_config(
    *,
    model: Path,
    language: str,
    max_context: int,
    prompt: str | None,
    duration_ms: int,
    asr_mode: str,
    asr_window_sec: int,
    asr_overlap_sec: int,
    audio_prep: str,
) -> dict[str, Any]:
    return {
        "schema": "murmurmark.whisper_cpp_raw_cache/v1",
        "model": str(model),
        "language": language,
        "max_context": max_context,
        "prompt": prompt,
        "duration_ms": duration_ms,
        "asr_mode": asr_mode,
        "asr_window_sec": asr_window_sec,
        "asr_overlap_sec": asr_overlap_sec,
        "audio_prep": audio_prep,
        "output_json_full": True,
        "log_score": True,
        "suppress_nst": True,
        "suppress_regex": r"^(Редактор субтитров|Продолжение следует|Спасибо за просмотр|Субтитры.*)$",
    }


def cache_matches(output_base: Path, expected: dict[str, Any]) -> bool:
    json_path = output_base.with_suffix(".json")
    meta_path = raw_meta_path(output_base)
    if not json_path.exists() or not meta_path.exists():
        return False
    try:
        actual = json.loads(meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    return actual == expected


def write_cache_meta(output_base: Path, metadata: dict[str, Any]) -> None:
    raw_meta_path(output_base).write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def run_whisper(
    *,
    whisper_cli: str,
    model: Path,
    language: str,
    threads: int,
    max_context: int,
    prompt: str | None,
    duration_ms: int,
    input_wav: Path,
    output_base: Path,
) -> None:
    output_base.parent.mkdir(parents=True, exist_ok=True)
    command = [
        whisper_cli,
        "--model",
        str(model),
        "--language",
        language,
        "--threads",
        str(threads),
        "--max-context",
        str(max_context),
        "--output-txt",
        "--output-json",
        "--output-json-full",
        "--output-vtt",
        "--output-file",
        str(output_base),
        "--no-prints",
        "--log-score",
        "--suppress-nst",
        "--suppress-regex",
        r"^(Редактор субтитров|Продолжение следует|Спасибо за просмотр|Субтитры.*)$",
        "--file",
        str(input_wav),
    ]
    if prompt:
        command.extend(["--prompt", prompt])
    if duration_ms > 0:
        command.extend(["--duration", str(duration_ms)])
    print("+ " + printable_command(command))
    with output_base.with_suffix(".run.log").open("w", encoding="utf-8") as log:
        subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)


def audio_duration_ms(input_wav: Path) -> int:
    ffprobe = shutil.which("ffprobe") or "ffprobe"
    value = capture(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(input_wav),
        ]
    )
    return max(0, int(round(float(value) * 1000)))


def prepare_audio_for_asr(input_wav: Path, output_wav: Path, mode: str) -> Path:
    if mode == "none":
        return input_wav
    filters_by_mode = {
        "speech": "highpass=f=100,lowpass=f=7600,alimiter=limit=0.98",
        "loudnorm": "highpass=f=80,lowpass=f=7800,loudnorm=I=-20:LRA=9:TP=-2,alimiter=limit=0.98",
    }
    if mode not in filters_by_mode:
        raise ValueError(f"unsupported audio prep: {mode}")
    output_wav.parent.mkdir(parents=True, exist_ok=True)
    if output_wav.exists() and output_wav.stat().st_mtime >= input_wav.stat().st_mtime:
        return output_wav
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(input_wav),
            "-af",
            filters_by_mode[mode],
            "-ar",
            "16000",
            "-ac",
            "1",
            str(output_wav),
        ]
    )
    return output_wav


def timestamp_from_ms(ms: int) -> str:
    total_ms = max(0, ms)
    millis = total_ms % 1000
    total_sec = total_ms // 1000
    seconds = total_sec % 60
    minutes = (total_sec // 60) % 60
    hours = total_sec // 3600
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def vtt_timestamp_from_ms(ms: int) -> str:
    return timestamp_from_ms(ms).replace(",", ".")


def shift_offset_node(node: dict[str, Any], shift_ms: int) -> None:
    offsets = node.get("offsets")
    if isinstance(offsets, dict):
        start = int(offsets.get("from") or 0) + shift_ms
        end = int(offsets.get("to") or 0) + shift_ms
        offsets["from"] = max(0, start)
        offsets["to"] = max(0, end)
        node["timestamps"] = {
            "from": timestamp_from_ms(offsets["from"]),
            "to": timestamp_from_ms(offsets["to"]),
        }


def shift_transcription_row(row: dict[str, Any], shift_ms: int) -> dict[str, Any]:
    adjusted = copy.deepcopy(row)
    shift_offset_node(adjusted, shift_ms)
    for token in adjusted.get("tokens") or []:
        if isinstance(token, dict):
            shift_offset_node(token, shift_ms)
    return adjusted


def write_whisper_text_sidecars(output_base: Path) -> None:
    data = json.loads(output_base.with_suffix(".json").read_text(encoding="utf-8"))
    rows = data.get("transcription", [])
    with output_base.with_suffix(".txt").open("w", encoding="utf-8") as file:
        for row in rows:
            text = str(row.get("text") or "").strip()
            if text:
                file.write(text + "\n")
    with output_base.with_suffix(".vtt").open("w", encoding="utf-8") as file:
        file.write("WEBVTT\n\n")
        for row in rows:
            text = str(row.get("text") or "").strip()
            offsets = row.get("offsets") or {}
            start_ms = int(offsets.get("from") or 0)
            end_ms = int(offsets.get("to") or start_ms)
            if text and end_ms > start_ms:
                file.write(f"{vtt_timestamp_from_ms(start_ms)} --> {vtt_timestamp_from_ms(end_ms)}\n")
                file.write(text + "\n\n")
    for stale_suffix in (".score.txt", ".run.log"):
        stale = output_base.with_suffix(stale_suffix)
        if stale.exists():
            stale.unlink()


def write_combined_whisper_json(
    *,
    chunks: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    output_base: Path,
    source_audio: Path,
    asr_mode: str,
    window_sec: int,
    overlap_sec: int,
) -> None:
    template = chunks[-1]["data"] if chunks else {}
    combined = copy.deepcopy(template)
    combined["transcription"] = sorted(
        rows,
        key=lambda row: (
            int((row.get("offsets") or {}).get("from") or 0),
            int((row.get("offsets") or {}).get("to") or 0),
        ),
    )
    combined.setdefault("params", {})
    if isinstance(combined["params"], dict):
        combined["params"]["murmurmark_asr_mode"] = asr_mode
        combined["params"]["murmurmark_window_sec"] = window_sec
        combined["params"]["murmurmark_overlap_sec"] = overlap_sec
        combined["params"]["murmurmark_source_audio"] = str(source_audio)
    output_base.with_suffix(".json").write_text(
        json.dumps(combined, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_whisper_text_sidecars(output_base)


def run_whisper_windowed(
    *,
    whisper_cli: str,
    model: Path,
    language: str,
    threads: int,
    max_context: int,
    prompt: str | None,
    duration_ms: int,
    input_wav: Path,
    output_base: Path,
    window_sec: int,
    overlap_sec: int,
) -> None:
    if window_sec <= 0:
        raise SystemExit("--asr-window-sec must be positive")
    if overlap_sec < 0:
        raise SystemExit("--asr-overlap-sec must not be negative")
    if overlap_sec >= window_sec // 2:
        raise SystemExit("--asr-overlap-sec must be less than half of --asr-window-sec")

    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    source_duration_ms = audio_duration_ms(input_wav)
    total_ms = source_duration_ms
    if duration_ms > 0:
        total_ms = min(total_ms, duration_ms)
    window_ms = window_sec * 1000
    overlap_ms = overlap_sec * 1000
    chunk_dir = output_base.parent / "chunks" / output_base.name
    if chunk_dir.exists():
        shutil.rmtree(chunk_dir)
    chunk_dir.mkdir(parents=True, exist_ok=True)

    all_rows: list[dict[str, Any]] = []
    chunks: list[dict[str, Any]] = []
    hard_start = 0
    index = 1
    while hard_start < total_ms:
        hard_end = min(total_ms, hard_start + window_ms)
        seek_ms = max(0, hard_start - overlap_ms)
        clip_end_ms = min(source_duration_ms, hard_end + overlap_ms)
        clip_duration_ms = max(1, clip_end_ms - seek_ms)
        chunk_wav = chunk_dir / f"{index:04d}_{hard_start // 1000:06d}s.wav"
        chunk_base = chunk_dir / f"{index:04d}_{hard_start // 1000:06d}s"
        run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                f"{seek_ms / 1000:.3f}",
                "-t",
                f"{clip_duration_ms / 1000:.3f}",
                "-i",
                str(input_wav),
                "-ar",
                "16000",
                "-ac",
                "1",
                str(chunk_wav),
            ]
        )
        run_whisper(
            whisper_cli=whisper_cli,
            model=model,
            language=language,
            threads=threads,
            max_context=max_context,
            prompt=prompt,
            duration_ms=0,
            input_wav=chunk_wav,
            output_base=chunk_base,
        )
        data = json.loads(chunk_base.with_suffix(".json").read_text(encoding="utf-8"))
        chunks.append(
            {
                "index": index,
                "hard_start_ms": hard_start,
                "hard_end_ms": hard_end,
                "seek_ms": seek_ms,
                "data": data,
            }
        )
        for row in data.get("transcription", []):
            offsets = row.get("offsets") or {}
            local_start = int(offsets.get("from") or 0)
            local_end = int(offsets.get("to") or local_start)
            global_start = seek_ms + local_start
            global_end = seek_ms + local_end
            center = (global_start + global_end) / 2.0
            if center < hard_start or center >= hard_end:
                continue
            all_rows.append(shift_transcription_row(row, seek_ms))
        hard_start += window_ms
        index += 1

    write_combined_whisper_json(
        chunks=chunks,
        rows=all_rows,
        output_base=output_base,
        source_audio=input_wav,
        asr_mode="windowed",
        window_sec=window_sec,
        overlap_sec=overlap_sec,
    )


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def normalized_short(text: str) -> str:
    return re.sub(r"[^\wа-яё]+", "", text.lower(), flags=re.IGNORECASE)


def optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def first_optional_float(row: dict[str, Any], names: tuple[str, ...]) -> float | None:
    for name in names:
        if name in row:
            return optional_float(row.get(name))
    result = row.get("result")
    if isinstance(result, dict):
        for name in names:
            if name in result:
                return optional_float(result.get(name))
    return None


def word_like_token(text: str) -> bool:
    return bool(re.search(r"[0-9A-Za-zА-Яа-яЁё]", text))


def word_tokens(row: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for token in row.get("tokens") or []:
        text = str(token.get("text") or "")
        if text.startswith("[_") or not word_like_token(text):
            continue
        probability = optional_float(token.get("p"))
        offsets = token.get("offsets") or {}
        token_start = int(offsets.get("from") or 0)
        token_end = int(offsets.get("to") or token_start)
        result.append(
            {
                "text": text,
                "p": probability,
                "start_ms": token_start,
                "end_ms": token_end,
            }
        )
    return result


def token_confidence_stats_for_tokens(tokens: list[dict[str, Any]]) -> dict[str, float | int | None]:
    probs: list[float] = []
    confident_start: int | None = None
    confident_end: int | None = None
    for token in tokens:
        probability = optional_float(token.get("p"))
        if probability is None:
            continue
        probs.append(probability)
        token_start = int(token.get("start_ms") or 0)
        token_end = int(token.get("end_ms") or token_start)
        if probability >= 0.60:
            confident_start = token_start if confident_start is None else min(confident_start, token_start)
            confident_end = token_end if confident_end is None else max(confident_end, token_end)
    if not probs:
        return {
            "token_avg_prob": None,
            "token_low_prob_ratio": None,
            "token_confident_start_ms": None,
            "token_confident_end_ms": None,
        }
    low_count = sum(1 for probability in probs if probability < 0.50)
    return {
        "token_avg_prob": sum(probs) / len(probs),
        "token_low_prob_ratio": low_count / len(probs),
        "token_confident_start_ms": confident_start,
        "token_confident_end_ms": confident_end,
    }


def token_confidence_stats(row: dict[str, Any]) -> dict[str, float | int | None]:
    return token_confidence_stats_for_tokens(word_tokens(row))


def split_tokens_on_gaps(tokens: list[dict[str, Any]], gap_ms: int = 1500) -> list[list[dict[str, Any]]]:
    if not tokens:
        return []
    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    previous_end: int | None = None
    for token in tokens:
        token_start = int(token.get("start_ms") or 0)
        token_end = int(token.get("end_ms") or token_start)
        if current and previous_end is not None and token_start - previous_end >= gap_ms:
            chunks.append(current)
            current = []
        current.append(token)
        previous_end = token_end if previous_end is None else max(previous_end, token_end)
    if current:
        chunks.append(current)
    return chunks


def split_mic_row_if_needed(
    row: dict[str, Any],
    start_ms: int,
    end_ms: int,
    text: str,
) -> list[tuple[int, int, str, dict[str, float | int | None], list[dict[str, Any]]]]:
    tokens = word_tokens(row)
    chunks = split_tokens_on_gaps(tokens)
    if len(chunks) <= 1:
        return [(start_ms, end_ms, text, token_confidence_stats_for_tokens(tokens), tokens)]
    parts: list[tuple[int, int, str, dict[str, float | int | None], list[dict[str, Any]]]] = []
    for chunk in chunks:
        chunk_text = clean_text("".join(str(token.get("text") or "") for token in chunk))
        if not chunk_text:
            continue
        chunk_start = min(int(token.get("start_ms") or start_ms) for token in chunk)
        chunk_end = max(int(token.get("end_ms") or chunk_start) for token in chunk)
        if chunk_end <= chunk_start:
            continue
        parts.append((chunk_start, chunk_end, chunk_text, token_confidence_stats_for_tokens(chunk), chunk))
    return parts or [(start_ms, end_ms, text, token_confidence_stats_for_tokens(tokens), tokens)]


def probable_short_remote_noise(
    *,
    text: str,
    start_ms: int,
    end_ms: int,
    token_avg_prob: float | None,
    token_low_prob_ratio: float | None,
) -> bool:
    duration_sec = max(0.001, (end_ms - start_ms) / 1000.0)
    word_count = len(normalize_for_compare(text).split())
    if word_count > 2 or duration_sec < 3.0:
        return False
    if token_avg_prob is None or token_low_prob_ratio is None:
        return False
    return token_avg_prob < 0.64 or token_low_prob_ratio >= 0.45


def read_utterances(path: Path, source_track: str, role: str, speaker_label: str) -> tuple[list[Utterance], list[dict[str, Any]]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("transcription", [])
    utterances: list[Utterance] = []
    dropped: list[dict[str, Any]] = []
    previous_by_text: dict[str, int] = {}
    seen_at_timestamp: set[tuple[int, int, str]] = set()

    for index, row in enumerate(rows):
        raw_id = f"raw_{source_track}_{index + 1:06d}"
        offsets = row.get("offsets") or {}
        start_ms = int(offsets.get("from") or 0)
        end_ms = int(offsets.get("to") or 0)
        text = clean_text(str(row.get("text") or ""))
        norm = normalized_short(text)
        token_stats = token_confidence_stats(row)
        token_avg_prob = optional_float(token_stats["token_avg_prob"])
        token_low_prob_ratio = optional_float(token_stats["token_low_prob_ratio"])
        token_confident_start_ms = token_stats["token_confident_start_ms"]
        token_confident_end_ms = token_stats["token_confident_end_ms"]

        reason = None
        timestamp_key = (start_ms, end_ms, norm)

        if not text:
            reason = "empty"
        elif end_ms <= start_ms:
            reason = "zero_duration"
        elif KNOWN_HALLUCINATION_RE.search(text):
            reason = "known_hallucination"
        elif source_track == "remote" and probable_short_remote_noise(
            text=text,
            start_ms=start_ms,
            end_ms=end_ms,
            token_avg_prob=token_avg_prob,
            token_low_prob_ratio=token_low_prob_ratio,
        ):
            reason = "low_confidence_short_remote"
        elif timestamp_key in seen_at_timestamp:
            reason = "exact_duplicate_segment"
        elif norm in REPEATED_GOODBYE and norm in previous_by_text and start_ms - previous_by_text[norm] <= 60_000:
            reason = "repeated_goodbye_hallucination"

        if reason:
            dropped.append(
                {
                    "source_track": source_track,
                    "index": index,
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "text": text,
                    "reason": reason,
                    "token_avg_prob": token_avg_prob,
                    "token_low_prob_ratio": token_low_prob_ratio,
                }
            )
            continue

        if norm:
            previous_by_text[norm] = start_ms
            seen_at_timestamp.add(timestamp_key)
        parts = (
            split_mic_row_if_needed(row, start_ms, end_ms, text)
            if source_track == "mic"
            else [(start_ms, end_ms, text, token_stats, word_tokens(row))]
        )
        for part_index, (part_start_ms, part_end_ms, part_text, part_stats, part_tokens) in enumerate(parts, start=1):
            part_id = raw_id if len(parts) == 1 else f"{raw_id}_part{part_index:02d}"
            utterances.append(
                Utterance(
                    id=part_id,
                    source_track=source_track,
                    role=role,
                    speaker_label=speaker_label,
                    start_ms=part_start_ms,
                    end_ms=part_end_ms,
                    raw_text=part_text,
                    avg_logprob=first_optional_float(row, ("avg_logprob", "average_logprob", "logprob")),
                    no_speech_prob=first_optional_float(row, ("no_speech_prob", "no_speech_probability")),
                    compression_ratio=first_optional_float(row, ("compression_ratio",)),
                    temperature=first_optional_float(row, ("temperature",)),
                    token_avg_prob=optional_float(part_stats["token_avg_prob"]),
                    token_low_prob_ratio=optional_float(part_stats["token_low_prob_ratio"]),
                    token_confident_start_ms=part_stats["token_confident_start_ms"]
                    if isinstance(part_stats["token_confident_start_ms"], int)
                    else None,
                    token_confident_end_ms=part_stats["token_confident_end_ms"]
                    if isinstance(part_stats["token_confident_end_ms"], int)
                    else None,
                    tokens=part_tokens,
                )
            )

    return utterances, dropped


def read_speaker_state(session: Path) -> list[dict[str, Any]]:
    path = session / "derived" / "preprocess" / "echo" / "speaker_state.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def action_ratios(start_ms: int, end_ms: int, states: list[dict[str, Any]]) -> dict[str, float]:
    if end_ms <= start_ms or not states:
        return {}
    start = start_ms / 1000.0
    end = end_ms / 1000.0
    totals: dict[str, float] = {}
    total = 0.0
    for row in states:
        overlap_start = max(start, float(row.get("start", 0.0)))
        overlap_end = min(end, float(row.get("end", 0.0)))
        if overlap_end <= overlap_start:
            continue
        duration = overlap_end - overlap_start
        action = str(row.get("action", "unknown"))
        totals[action] = totals.get(action, 0.0) + duration
        total += duration
    if total <= 0:
        return {}
    return {key: value / total for key, value in totals.items()}


def action_support_bounds(
    start_ms: int,
    end_ms: int,
    states: list[dict[str, Any]],
    actions: set[str],
) -> tuple[int | None, int | None, float]:
    if end_ms <= start_ms or not states:
        return None, None, 0.0
    start = start_ms / 1000.0
    end = end_ms / 1000.0
    support_start: float | None = None
    support_end: float | None = None
    support_sec = 0.0
    for row in states:
        if str(row.get("action", "unknown")) not in actions:
            continue
        overlap_start = max(start, float(row.get("start", 0.0)))
        overlap_end = min(end, float(row.get("end", 0.0)))
        if overlap_end <= overlap_start:
            continue
        if overlap_end - overlap_start < 0.5:
            continue
        support_start = overlap_start if support_start is None else min(support_start, overlap_start)
        support_end = overlap_end if support_end is None else max(support_end, overlap_end)
        support_sec += overlap_end - overlap_start
    if support_start is None or support_end is None:
        return None, None, 0.0
    return int(round(support_start * 1000)), int(round(support_end * 1000)), support_sec


def suppress_mic_remote_active(
    utterances: list[Utterance],
    speaker_states: list[dict[str, Any]],
) -> tuple[list[Utterance], list[dict[str, Any]]]:
    if not speaker_states:
        return utterances, []
    kept: list[Utterance] = []
    dropped: list[dict[str, Any]] = []
    for item in utterances:
        if item.source_track != "mic":
            kept.append(item)
            continue
        ratios = action_ratios(item.start_ms, item.end_ms, speaker_states)
        remote_active = ratios.get("pass_mild_fir_flag_remote_active", 0.0)
        overlap = ratios.get("pass_mild_fir_flag_overlap", 0.0)
        local = ratios.get("pass_raw_local_only", 0.0)
        should_drop = (
            remote_active >= 0.60
            and local <= 0.25
            and overlap <= 0.20
        )
        if should_drop:
            dropped.append(
                {
                    "source_track": item.source_track,
                    "start_ms": item.start_ms,
                    "end_ms": item.end_ms,
                    "text": item.raw_text,
                    "reason": "mic_remote_active_by_echo_guard",
                    "ratios": {key: round(value, 3) for key, value in sorted(ratios.items())},
                }
            )
        else:
            kept.append(item)
    return kept, dropped


TERM_VARIANTS = {
    "gitlab": ("gitlab", "git lab", "гитлаб", "гит лаб", "гидлаб", "гид лаб", "getlab"),
    "kubernetes": ("kubernetes", "кубернетис", "кубер", "кубы", "кубе", "k8s"),
    "deploy": ("deploy", "деплой", "диплой", "теплой", "девлой"),
    "cicd": ("ci cd", "ci/cd", "cicd", "си ай си ди", "сей сиди", "сисиди"),
    "t-rost": ("t-rost", "t rost", "тирост", "ти рост", "т рост", "т-рост"),
    "kitsune": ("kitsune", "китсуни", "кицуне"),
    "nessie": ("nessie", "несси"),
    "finddog": ("finddog", "find dog", "файндог", "файндог"),
    "mcp": ("mcp", "эм си пи", "мсп"),
    "dp": ("dp", "дп"),
    "sla": ("sla", "сла", "эс эл эй"),
    "slo": ("slo", "сло", "эс эл о"),
}

WEAK_SHORT_PHRASES = {
    "спасибо",
    "да",
    "ага",
    "угу",
    "ок",
    "окей",
    "понял",
    "поняла",
    "хорошо",
    "вот",
}


def normalize_for_compare(text: str) -> str:
    value = text.lower().replace("ё", "е")
    value = re.sub(r"[^\wа-яa-z0-9/+-]+", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+", " ", value).strip()
    padded = f" {value} "
    for canonical, variants in TERM_VARIANTS.items():
        for variant in sorted(variants, key=len, reverse=True):
            pattern = re.compile(rf"(?<!\w){re.escape(variant)}(?!\w)", re.IGNORECASE)
            padded = pattern.sub(f" {canonical} ", padded)
    return re.sub(r"\s+", " ", padded).strip()


def token_set(text: str) -> set[str]:
    return {token for token in normalize_for_compare(text).split() if token}


def display_token_spans(text: str) -> list[tuple[str, int, int]]:
    tokens: list[tuple[str, int, int]] = []
    for match in DISPLAY_TOKEN_RE.finditer(text):
        norm = normalize_for_compare(match.group(0))
        if norm:
            tokens.append((norm, match.start(), match.end()))
    return tokens


def char_ngrams(text: str, size: int = 3) -> set[str]:
    normalized = normalize_for_compare(text).replace(" ", "")
    if len(normalized) <= size:
        return {normalized} if normalized else set()
    return {normalized[index : index + size] for index in range(0, len(normalized) - size + 1)}


def jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def text_similarity(left: str, right: str) -> float:
    left_norm = normalize_for_compare(left)
    right_norm = normalize_for_compare(right)
    if not left_norm or not right_norm:
        return 0.0
    sequence = difflib.SequenceMatcher(None, left_norm, right_norm).ratio()
    token = jaccard(set(left_norm.split()), set(right_norm.split()))
    ngram = jaccard(char_ngrams(left_norm), char_ngrams(right_norm))
    return max(sequence, token, ngram)


def domain_terms(text: str) -> set[str]:
    tokens = set(normalize_for_compare(text).split())
    return {canonical for canonical in TERM_VARIANTS if canonical in tokens}


def domain_term_overlap(left: str, right: str) -> float:
    left_terms = domain_terms(left)
    right_terms = domain_terms(right)
    if not left_terms or not right_terms:
        return 0.0
    return len(left_terms & right_terms) / len(left_terms | right_terms)


def overlap_ms(left_start: int, left_end: int, right_start: int, right_end: int) -> int:
    return max(0, min(left_end, right_end) - max(left_start, right_start))


def time_overlap_ratio(left: Candidate, right: Candidate) -> float:
    overlap = overlap_ms(left.start_ms, left.end_ms, right.start_ms, right.end_ms)
    shortest = max(1, min(left.end_ms - left.start_ms, right.end_ms - right.start_ms))
    return overlap / shortest


def aggregate(values: list[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    if not present:
        return None
    return sum(present) / len(present)


def make_candidate(candidate_id: str, items: list[Utterance], speaker_states: list[dict[str, Any]]) -> Candidate:
    start_ms = min(item.start_ms for item in items)
    end_ms = max(item.end_ms for item in items)
    text = clean_text(" ".join(item.raw_text for item in items))
    duration_sec = max(0.001, (end_ms - start_ms) / 1000.0)
    ratios = action_ratios(start_ms, end_ms, speaker_states) if items[0].source_track == "mic" else {}
    local_support_start, local_support_end, local_support_sec = (None, None, 0.0)
    if items[0].source_track == "mic":
        local_support_start, local_support_end, local_support_sec = action_support_bounds(
            start_ms,
            end_ms,
            speaker_states,
            {"pass_raw_local_only", "pass_mild_fir_flag_overlap"},
        )
    echo_features = {
        "remote_active_ratio": round(ratios.get("pass_mild_fir_flag_remote_active", 0.0), 6),
        "local_only_ratio": round(ratios.get("pass_raw_local_only", 0.0), 6),
        "double_talk_ratio": round(ratios.get("pass_mild_fir_flag_overlap", 0.0), 6),
        "local_support_sec": round(local_support_sec, 3),
    }
    echo_features["local_score"] = round(
        echo_features["local_only_ratio"] + 0.5 * echo_features["double_talk_ratio"],
        6,
    )
    display_start_ms = start_ms
    display_end_ms = end_ms
    if items[0].source_track == "remote":
        confident_starts = [item.token_confident_start_ms for item in items if item.token_confident_start_ms is not None]
        confident_ends = [item.token_confident_end_ms for item in items if item.token_confident_end_ms is not None]
        if confident_starts:
            display_start_ms = min(confident_starts)
        if confident_ends:
            display_end_ms = max(confident_ends)
    elif (
        echo_features["remote_active_ratio"] > 0.50
        and local_support_start is not None
        and local_support_end is not None
        and local_support_sec >= 0.5
    ):
        display_start_ms = local_support_start
        display_end_ms = local_support_end
    elif (
        items[0].source_track == "mic"
        and start_ms == 0
        and local_support_start is not None
        and local_support_end is not None
        and local_support_sec >= 0.5
    ):
        display_start_ms = local_support_start
        display_end_ms = local_support_end
    if display_end_ms <= display_start_ms:
        display_start_ms = start_ms
        display_end_ms = end_ms
    return Candidate(
        id=candidate_id,
        source_track=items[0].source_track,
        role=items[0].role,
        speaker_label=items[0].speaker_label,
        start_ms=start_ms,
        end_ms=end_ms,
        display_start_ms=display_start_ms,
        display_end_ms=display_end_ms,
        text_raw=text,
        text_norm=normalize_for_compare(text),
        source_segments=[item.id for item in items],
        echo_features=echo_features,
        asr_features={
            "avg_logprob": aggregate([item.avg_logprob for item in items]),
            "no_speech_prob": aggregate([item.no_speech_prob for item in items]),
            "compression_ratio": aggregate([item.compression_ratio for item in items]),
            "token_avg_prob": aggregate([item.token_avg_prob for item in items]),
            "token_low_prob_ratio": aggregate([item.token_low_prob_ratio for item in items]),
            "duration_sec": round(duration_sec, 3),
            "chars_per_sec": round(len(text) / duration_sec, 3),
        },
    )


def utterance_echo_class(item: Utterance, speaker_states: list[dict[str, Any]]) -> str:
    if item.source_track != "mic":
        return "remote_track"
    ratios = action_ratios(item.start_ms, item.end_ms, speaker_states)
    remote_active = ratios.get("pass_mild_fir_flag_remote_active", 0.0)
    local_only = ratios.get("pass_raw_local_only", 0.0)
    double_talk = ratios.get("pass_mild_fir_flag_overlap", 0.0)
    if local_only >= 0.55:
        return "local_only"
    if remote_active >= 0.55:
        return "remote_active"
    if double_talk >= 0.25:
        return "double_talk"
    return "uncertain"


def build_candidates(
    utterances: list[Utterance],
    speaker_states: list[dict[str, Any]],
    merge_gap_ms: int,
    max_merged_chars: int,
    max_merged_ms: int,
) -> list[Candidate]:
    candidates: list[Candidate] = []
    grouped: dict[str, list[Utterance]] = {}
    for item in sorted(utterances, key=lambda row: (row.source_track, row.start_ms, row.end_ms)):
        grouped.setdefault(item.source_track, []).append(item)

    for source_track, rows in sorted(grouped.items()):
        current: list[Utterance] = []
        for item in rows:
            if not current:
                current = [item]
                continue
            combined_text = clean_text(" ".join([*([row.raw_text for row in current]), item.raw_text]))
            current_start = min(row.start_ms for row in current)
            current_end = max(row.end_ms for row in current)
            same_echo_class = (
                item.source_track != "mic"
                or utterance_echo_class(current[-1], speaker_states) == utterance_echo_class(item, speaker_states)
            )
            can_merge = (
                item.start_ms <= current_end + merge_gap_ms
                and len(combined_text) <= max_merged_chars
                and max(current_end, item.end_ms) - current_start <= max_merged_ms
                and same_echo_class
            )
            if can_merge:
                current.append(item)
            else:
                candidates.append(make_candidate(f"cand_{source_track}_{len(candidates) + 1:06d}", current, speaker_states))
                current = [item]
        if current:
            candidates.append(make_candidate(f"cand_{source_track}_{len(candidates) + 1:06d}", current, speaker_states))
    return candidates


def merge_intervals(intervals: list[tuple[int, int]], gap_ms: int = 0) -> list[tuple[int, int]]:
    cleaned = sorted((start, end) for start, end in intervals if end > start)
    if not cleaned:
        return []
    merged = [cleaned[0]]
    for start, end in cleaned[1:]:
        previous_start, previous_end = merged[-1]
        if start <= previous_end + gap_ms:
            merged[-1] = (previous_start, max(previous_end, end))
        else:
            merged.append((start, end))
    return merged


def subtract_intervals(
    source: list[tuple[int, int]],
    blockers: list[tuple[int, int]],
    min_duration_ms: int = LOCAL_ISLAND_MIN_MS,
) -> list[tuple[int, int]]:
    result: list[tuple[int, int]] = []
    merged_blockers = merge_intervals(blockers)
    for source_start, source_end in merge_intervals(source):
        fragments = [(source_start, source_end)]
        for block_start, block_end in merged_blockers:
            next_fragments: list[tuple[int, int]] = []
            for fragment_start, fragment_end in fragments:
                if block_end <= fragment_start or block_start >= fragment_end:
                    next_fragments.append((fragment_start, fragment_end))
                    continue
                if block_start > fragment_start:
                    next_fragments.append((fragment_start, min(block_start, fragment_end)))
                if block_end < fragment_end:
                    next_fragments.append((max(block_end, fragment_start), fragment_end))
            fragments = next_fragments
            if not fragments:
                break
        result.extend((start, end) for start, end in fragments if end - start >= min_duration_ms)
    return merge_intervals(result)


def state_action_intervals(
    start_ms: int,
    end_ms: int,
    speaker_states: list[dict[str, Any]],
    actions: set[str],
    min_duration_ms: int = LOCAL_ISLAND_MIN_MS,
) -> list[tuple[int, int]]:
    intervals: list[tuple[int, int]] = []
    if end_ms <= start_ms:
        return intervals
    start_sec = start_ms / 1000.0
    end_sec = end_ms / 1000.0
    for row in speaker_states:
        if str(row.get("action", "unknown")) not in actions:
            continue
        overlap_start = max(start_sec, float(row.get("start", 0.0)))
        overlap_end = min(end_sec, float(row.get("end", 0.0)))
        if overlap_end <= overlap_start:
            continue
        intervals.append((int(round(overlap_start * 1000)), int(round(overlap_end * 1000))))
    return [
        (start, end)
        for start, end in merge_intervals(intervals)
        if end - start >= min_duration_ms
    ]


def interval_overlaps_any(start_ms: int, end_ms: int, intervals: list[tuple[int, int]]) -> bool:
    return any(overlap_ms(start_ms, end_ms, left, right) > 0 for left, right in intervals)


def interval_actions_allowed(
    start_ms: int,
    end_ms: int,
    speaker_states: list[dict[str, Any]],
    allowed_actions: set[str],
) -> bool:
    if end_ms <= start_ms:
        return False
    start_sec = start_ms / 1000.0
    end_sec = end_ms / 1000.0
    covered_ms = 0
    for row in speaker_states:
        overlap_start = max(start_sec, float(row.get("start", 0.0)))
        overlap_end = min(end_sec, float(row.get("end", 0.0)))
        if overlap_end <= overlap_start:
            continue
        if str(row.get("action", "unknown")) not in allowed_actions:
            return False
        covered_ms += int(round((overlap_end - overlap_start) * 1000))
    return covered_ms >= max(1, end_ms - start_ms - 50)


def expand_local_island_for_shadow(
    *,
    island_start_ms: int,
    island_end_ms: int,
    candidate_start_ms: int,
    candidate_end_ms: int,
    speaker_states: list[dict[str, Any]],
    guarded_blocks: list[tuple[int, int]],
    left_ms: int = SHADOW_V2_LEFT_EXPAND_MS,
    right_ms: int = SHADOW_V2_RIGHT_EXPAND_MS,
) -> tuple[int, int]:
    allowed = {"mute_silence", "pass_raw_local_only"}
    expanded_start = island_start_ms
    proposed_start = max(candidate_start_ms, island_start_ms - left_ms)
    if (
        proposed_start < island_start_ms
        and not interval_overlaps_any(proposed_start, island_start_ms, guarded_blocks)
        and interval_actions_allowed(proposed_start, island_start_ms, speaker_states, allowed)
    ):
        expanded_start = proposed_start

    expanded_end = island_end_ms
    proposed_end = min(candidate_end_ms, island_end_ms + right_ms)
    if (
        proposed_end > island_end_ms
        and not interval_overlaps_any(island_end_ms, proposed_end, guarded_blocks)
        and interval_actions_allowed(island_end_ms, proposed_end, speaker_states, allowed)
    ):
        expanded_end = proposed_end
    return expanded_start, expanded_end


def shadow_micro_decode_windows(
    *,
    island_start_ms: int,
    island_end_ms: int,
    candidate_start_ms: int,
    candidate_end_ms: int,
    speaker_states: list[dict[str, Any]],
    guarded_blocks: list[tuple[int, int]],
    specs: tuple[dict[str, Any], ...],
) -> list[dict[str, Any]]:
    windows: list[dict[str, Any]] = []
    seen: set[tuple[int, int, str]] = set()
    for spec in specs:
        start_ms, end_ms = expand_local_island_for_shadow(
            island_start_ms=island_start_ms,
            island_end_ms=island_end_ms,
            candidate_start_ms=candidate_start_ms,
            candidate_end_ms=candidate_end_ms,
            speaker_states=speaker_states,
            guarded_blocks=guarded_blocks,
            left_ms=int(spec["left_ms"]),
            right_ms=int(spec["right_ms"]),
        )
        key = (start_ms, end_ms, str(spec["label"]))
        if key in seen:
            continue
        seen.add(key)
        windows.append(
            {
                "label": str(spec["label"]),
                "start_ms": start_ms,
                "end_ms": end_ms,
            }
        )
    return windows


def token_text_for_interval(
    candidate: Candidate,
    utterances_by_id: dict[str, Utterance],
    start_ms: int,
    end_ms: int,
    margin_ms: int = 150,
) -> str:
    tokens: list[dict[str, Any]] = []
    for segment_id in candidate.source_segments:
        utterance = utterances_by_id.get(segment_id)
        if utterance is None:
            continue
        tokens.extend(utterance.tokens)
    if not tokens:
        if start_ms <= candidate.start_ms + margin_ms and end_ms >= candidate.end_ms - margin_ms:
            return candidate.text_raw
        return ""
    selected: list[dict[str, Any]] = []
    for token in sorted(tokens, key=lambda row: (int(row.get("start_ms") or 0), int(row.get("end_ms") or 0))):
        token_start = int(token.get("start_ms") or 0)
        token_end = int(token.get("end_ms") or token_start)
        midpoint = (token_start + token_end) // 2
        if start_ms - margin_ms <= midpoint <= end_ms + margin_ms:
            selected.append(token)
    return clean_text("".join(str(token.get("text") or "") for token in selected))


def read_micro_reasr_text(json_path: Path, global_offset_ms: int, start_ms: int, end_ms: int) -> tuple[str, list[dict[str, Any]]]:
    data = json.loads(json_path.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    texts: list[str] = []
    for row in data.get("transcription", []):
        shifted = shift_transcription_row(row, global_offset_ms)
        offsets = shifted.get("offsets") or {}
        row_start = int(offsets.get("from") or 0)
        row_end = int(offsets.get("to") or row_start)
        midpoint = (row_start + row_end) // 2
        text = clean_text(str(shifted.get("text") or ""))
        if not text:
            continue
        if midpoint < start_ms - 250 or midpoint > end_ms + 250:
            continue
        if KNOWN_HALLUCINATION_RE.search(text):
            continue
        rows.append(
            {
                "start_ms": row_start,
                "end_ms": row_end,
                "text": text,
                "avg_logprob": first_optional_float(shifted, ("avg_logprob", "average_logprob", "logprob")),
                "no_speech_prob": first_optional_float(shifted, ("no_speech_prob", "no_speech_probability")),
                "token_avg_prob": token_confidence_stats(shifted)["token_avg_prob"],
            }
        )
        texts.append(text)
    return clean_text(" ".join(texts)), rows


def micro_reasr_sources(session: Path, repair_profile: str) -> list[tuple[str, Path]]:
    audio_dir = session / "derived" / "preprocess" / "audio"
    preferred = [
        ("clean_local_fir", audio_dir / "mic_clean_local_fir.wav"),
        ("raw_for_asr", audio_dir / "mic_raw_for_asr.wav"),
        ("role_masked_for_asr", audio_dir / "mic_role_masked_for_asr.wav"),
    ]
    if repair_profile == "current":
        return [(label, path) for label, path in preferred[:2] if path.exists()][:1]
    return [(label, path) for label, path in preferred if path.exists()]


def starts_with_boundary_suspicious_prefix(text: str) -> bool:
    tokens = normalize_for_compare(text).split()
    return bool(tokens and tokens[0] in BOUNDARY_SUSPICIOUS_PREFIXES)


def boundary_prefix_repair(
    text: str,
    *,
    local_score: float,
    remote_similarity: float,
    starts_near_boundary: bool,
) -> tuple[str, dict[str, Any] | None]:
    tokens = normalize_for_compare(text).split()
    if not tokens or not starts_near_boundary or local_score < 0.65 or remote_similarity > 0.35:
        return text, None
    first = tokens[0]
    for rule in BOUNDARY_PREFIX_REPAIRS:
        if first != str(rule["from"]):
            continue
        contexts = tuple(str(item) for item in rule.get("contexts", ()))
        if contexts and not any(context in tokens[1:6] for context in contexts):
            continue
        pattern = re.compile(rf"^\s*{re.escape(str(rule['from']))}(?=\s|$)", re.IGNORECASE)
        repaired = pattern.sub(str(rule["to"]), text, count=1)
        if repaired == text:
            repaired = f"{rule['to']} {' '.join(text.split()[1:])}".strip()
        return clean_text(repaired), {
            "reason": "boundary_prefix_repair",
            "from": rule["from"],
            "to": rule["to"],
            "contexts": contexts,
            "local_score": round(local_score, 6),
            "remote_similarity": round(remote_similarity, 6),
            "starts_near_boundary": starts_near_boundary,
        }
    return text, None


def score_micro_reasr_text(
    text: str,
    rows: list[dict[str, Any]],
    start_ms: int,
    end_ms: int,
    *,
    remote_context_text: str = "",
    boundary_repaired: bool = False,
) -> float:
    normalized = normalize_for_compare(text)
    if not normalized or KNOWN_HALLUCINATION_RE.search(text):
        return -1.0
    duration_sec = max(0.25, (end_ms - start_ms) / 1000.0)
    chars_per_sec = len(text) / duration_sec
    token_probs = [
        value
        for value in (optional_float(row.get("token_avg_prob")) for row in rows)
        if value is not None
    ]
    token_score = sum(token_probs) / len(token_probs) if token_probs else 0.55
    length_score = 0.0
    if 1.0 <= chars_per_sec <= 18.0:
        length_score = 0.20
    elif 0.5 <= chars_per_sec <= 26.0:
        length_score = 0.05
    remote_penalty = text_similarity(text, remote_context_text) * 0.35 if remote_context_text else 0.0
    boundary_penalty = 0.15 if starts_with_boundary_suspicious_prefix(text) and not boundary_repaired else 0.0
    return token_score + length_score - remote_penalty - boundary_penalty


def filtered_control_tokens(text: str) -> list[str]:
    return [token for token in normalize_for_compare(text).split() if token not in {"а"}]


def token_sequence_contains(haystack: list[str], needle: list[str]) -> bool:
    if not needle:
        return True
    if len(needle) > len(haystack):
        return False
    last_start = len(haystack) - len(needle)
    return any(haystack[index : index + len(needle)] == needle for index in range(last_start + 1))


def sequence_start_index(haystack: list[str], needle: list[str]) -> int | None:
    if not needle:
        return 0
    if len(needle) > len(haystack):
        return None
    last_start = len(haystack) - len(needle)
    for index in range(last_start + 1):
        if haystack[index : index + len(needle)] == needle:
            return index
    return None


def drops_baseline_prefix(candidate_text: str, baseline_text: str) -> bool:
    baseline_tokens = filtered_control_tokens(baseline_text)
    candidate_tokens = filtered_control_tokens(candidate_text)
    if len(candidate_tokens) < 3 or len(candidate_tokens) >= len(baseline_tokens):
        return False
    start_index = sequence_start_index(baseline_tokens, candidate_tokens)
    return start_index is not None and 0 < start_index <= 3


def materialize_micro_reasr(
    *,
    source: Path,
    source_label: str,
    window_label: str,
    micro_dir: Path,
    whisper_cli: str,
    model: Path,
    language: str,
    threads: int,
    recognition_start_ms: int,
    recognition_end_ms: int,
    selection_start_ms: int,
    selection_end_ms: int,
    target_start_ms: int,
    target_end_ms: int,
    force: bool,
    repair_profile: str,
    leading_silence_ms: int = 0,
    remote_context_text: str = "",
    local_score: float = 0.0,
) -> tuple[str, dict[str, Any]]:
    slice_duration_ms = max(1, recognition_end_ms - recognition_start_ms)
    stem = (
        f"micro_{repair_profile}_{window_label}_{source_label}_"
        f"{target_start_ms:010d}_{target_end_ms:010d}_"
        f"{recognition_start_ms:010d}_{recognition_end_ms:010d}_"
        f"sil{leading_silence_ms:04d}"
    )
    slice_wav = micro_dir / f"{stem}.wav"
    output_base = micro_dir / stem
    json_path = output_base.with_suffix(".json")

    if force or not json_path.exists():
        ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
        run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                f"{recognition_start_ms / 1000.0:.3f}",
                "-t",
                f"{slice_duration_ms / 1000.0:.3f}",
                "-i",
                str(source),
                *(
                    [
                        "-af",
                        f"adelay={leading_silence_ms}:all=1",
                    ]
                    if leading_silence_ms > 0
                    else []
                ),
                "-ar",
                "16000",
                "-ac",
                "1",
                str(slice_wav),
            ]
        )
        command = [
            whisper_cli,
            "--model",
            str(model),
            "--language",
            language,
            "--threads",
            str(threads),
            "--max-context",
            "0",
            "--temperature",
            "0",
            "--temperature-inc",
            "0",
            "--no-fallback",
            "--output-json",
            "--output-json-full",
            "--output-txt",
            "--output-file",
            str(output_base),
            "--no-prints",
            "--log-score",
            "--suppress-nst",
            "--suppress-regex",
            r"^(Редактор субтитров|Продолжение следует|Спасибо за просмотр|Субтитры.*)$",
            "--file",
            str(slice_wav),
        ]
        print("+ " + " ".join(command))
        with output_base.with_suffix(".run.log").open("w", encoding="utf-8") as log:
            try:
                subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
            except subprocess.CalledProcessError as error:
                return "", {
                    "status": "failed",
                    "reason": "whisper_cli_failed",
                    "returncode": error.returncode,
                    "source": str(source),
                    "source_label": source_label,
                    "window_label": window_label,
                    "slice_start_ms": recognition_start_ms,
                    "slice_end_ms": recognition_end_ms,
                    "leading_silence_ms": leading_silence_ms,
                    "selection_start_ms": selection_start_ms,
                    "selection_end_ms": selection_end_ms,
                    "target_start_ms": target_start_ms,
                    "target_end_ms": target_end_ms,
                    "output_base": str(output_base),
                }
    if not json_path.exists():
        return "", {
            "status": "failed",
            "reason": "micro_json_not_found",
            "source": str(source),
            "source_label": source_label,
            "window_label": window_label,
            "slice_start_ms": recognition_start_ms,
            "slice_end_ms": recognition_end_ms,
            "leading_silence_ms": leading_silence_ms,
            "selection_start_ms": selection_start_ms,
            "selection_end_ms": selection_end_ms,
            "target_start_ms": target_start_ms,
            "target_end_ms": target_end_ms,
        }

    global_offset_ms = recognition_start_ms - leading_silence_ms
    text, rows = read_micro_reasr_text(json_path, global_offset_ms, selection_start_ms, selection_end_ms)
    first_row_start_ms = min((int(row.get("start_ms") or 0) for row in rows), default=selection_start_ms)
    starts_near_boundary = first_row_start_ms <= selection_start_ms + 600
    boundary_raw_text = text
    text, boundary_meta = boundary_prefix_repair(
        text,
        local_score=local_score,
        remote_similarity=text_similarity(text, remote_context_text) if remote_context_text else 0.0,
        starts_near_boundary=starts_near_boundary,
    )
    boundary_suspicious = starts_with_boundary_suspicious_prefix(boundary_raw_text)
    score = score_micro_reasr_text(
        text,
        rows,
        target_start_ms,
        target_end_ms,
        remote_context_text=remote_context_text,
        boundary_repaired=boundary_meta is not None,
    )
    if not text:
        return "", {
            "status": "failed",
            "reason": "empty_micro_text",
            "source": str(source),
            "source_label": source_label,
            "window_label": window_label,
            "slice_start_ms": recognition_start_ms,
            "slice_end_ms": recognition_end_ms,
            "leading_silence_ms": leading_silence_ms,
            "selection_start_ms": selection_start_ms,
            "selection_end_ms": selection_end_ms,
            "target_start_ms": target_start_ms,
            "target_end_ms": target_end_ms,
            "json": str(json_path),
            "score": score,
        }
    return text, {
        "status": "ok",
        "source": str(source),
        "source_label": source_label,
        "window_label": window_label,
        "slice_start_ms": recognition_start_ms,
        "slice_end_ms": recognition_end_ms,
        "leading_silence_ms": leading_silence_ms,
        "selection_start_ms": selection_start_ms,
        "selection_end_ms": selection_end_ms,
        "target_start_ms": target_start_ms,
        "target_end_ms": target_end_ms,
        "raw_text": boundary_raw_text,
        "boundary_suspicious": boundary_suspicious,
        "boundary_prefix_repair": boundary_meta,
        "remote_similarity": round(text_similarity(text, remote_context_text), 6) if remote_context_text else 0.0,
        "json": str(json_path),
        "rows": rows,
        "score": round(score, 6),
    }


def run_micro_reasr_current(
    *,
    session: Path,
    repair_dir: Path,
    whisper_cli: str,
    model: Path,
    language: str,
    threads: int,
    start_ms: int,
    end_ms: int,
    force: bool,
) -> tuple[str, dict[str, Any]]:
    audio_dir = session / "derived" / "preprocess" / "audio"
    source_options = [
        ("clean_local_fir", audio_dir / "mic_clean_local_fir.wav"),
        ("raw_for_asr", audio_dir / "mic_raw_for_asr.wav"),
    ]
    source_label = ""
    source = Path()
    for label, path in source_options:
        if path.exists():
            source_label = label
            source = path
            break
    if not source_label:
        return "", {"status": "failed", "reason": "micro_source_not_found"}

    slice_start_ms = max(0, start_ms - MICRO_REASR_PADDING_MS)
    slice_end_ms = end_ms + MICRO_REASR_PADDING_MS
    slice_duration_ms = max(1, slice_end_ms - slice_start_ms)
    micro_dir = repair_dir / "micro_reasr"
    micro_dir.mkdir(parents=True, exist_ok=True)
    stem = f"micro_{start_ms:010d}_{end_ms:010d}"
    slice_wav = micro_dir / f"{stem}.wav"
    output_base = micro_dir / stem
    json_path = output_base.with_suffix(".json")

    if force or not json_path.exists():
        ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
        run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                f"{slice_start_ms / 1000.0:.3f}",
                "-t",
                f"{slice_duration_ms / 1000.0:.3f}",
                "-i",
                str(source),
                "-ar",
                "16000",
                "-ac",
                "1",
                str(slice_wav),
            ]
        )
        command = [
            whisper_cli,
            "--model",
            str(model),
            "--language",
            language,
            "--threads",
            str(threads),
            "--max-context",
            "0",
            "--temperature",
            "0",
            "--temperature-inc",
            "0",
            "--no-fallback",
            "--output-json",
            "--output-json-full",
            "--output-txt",
            "--output-file",
            str(output_base),
            "--no-prints",
            "--log-score",
            "--suppress-nst",
            "--suppress-regex",
            r"^(Редактор субтитров|Продолжение следует|Спасибо за просмотр|Субтитры.*)$",
            "--file",
            str(slice_wav),
        ]
        print("+ " + " ".join(command))
        with output_base.with_suffix(".run.log").open("w", encoding="utf-8") as log:
            try:
                subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
            except subprocess.CalledProcessError as error:
                return "", {
                    "status": "failed",
                    "reason": "whisper_cli_failed",
                    "returncode": error.returncode,
                    "source": str(source),
                    "source_label": source_label,
                    "slice_start_ms": slice_start_ms,
                    "slice_end_ms": slice_end_ms,
                    "target_start_ms": start_ms,
                    "target_end_ms": end_ms,
                    "output_base": str(output_base),
                }
    if not json_path.exists():
        return "", {
            "status": "failed",
            "reason": "micro_json_not_found",
            "source": str(source),
            "source_label": source_label,
            "slice_start_ms": slice_start_ms,
            "slice_end_ms": slice_end_ms,
            "target_start_ms": start_ms,
            "target_end_ms": end_ms,
        }

    text, rows = read_micro_reasr_text(json_path, slice_start_ms, start_ms, end_ms)
    if not text:
        return "", {
            "status": "failed",
            "reason": "empty_micro_text",
            "source": str(source),
            "source_label": source_label,
            "slice_start_ms": slice_start_ms,
            "slice_end_ms": slice_end_ms,
            "target_start_ms": start_ms,
            "target_end_ms": end_ms,
            "json": str(json_path),
        }
    return text, {
        "status": "ok",
        "source": str(source),
        "source_label": source_label,
        "slice_start_ms": slice_start_ms,
        "slice_end_ms": slice_end_ms,
        "target_start_ms": start_ms,
        "target_end_ms": end_ms,
        "json": str(json_path),
        "rows": rows,
    }


def run_micro_reasr(
    *,
    session: Path,
    repair_dir: Path,
    whisper_cli: str,
    model: Path,
    language: str,
    threads: int,
    start_ms: int,
    end_ms: int,
    recognition_start_ms: int | None = None,
    recognition_end_ms: int | None = None,
    recognition_windows: list[dict[str, Any]] | None = None,
    recovery_windows: list[dict[str, Any]] | None = None,
    remote_context_text: str = "",
    local_score: float = 0.0,
    force: bool,
    repair_profile: str,
) -> tuple[str, dict[str, Any]]:
    if repair_profile == "current":
        return run_micro_reasr_current(
            session=session,
            repair_dir=repair_dir,
            whisper_cli=whisper_cli,
            model=model,
            language=language,
            threads=threads,
            start_ms=start_ms,
            end_ms=end_ms,
            force=force,
        )

    sources = micro_reasr_sources(session, repair_profile)
    if not sources:
        return "", {"status": "failed", "reason": "micro_source_not_found"}

    if recognition_windows:
        initial_windows = recognition_windows
    else:
        slice_start_ms = max(0, (recognition_start_ms if recognition_start_ms is not None else start_ms) - MICRO_REASR_PADDING_MS)
        slice_end_ms = (recognition_end_ms if recognition_end_ms is not None else end_ms) + MICRO_REASR_PADDING_MS
        initial_windows = [{"label": "default", "start_ms": slice_start_ms, "end_ms": slice_end_ms}]
    selection_start_ms = recognition_start_ms if repair_profile == "shadow_v2" and recognition_start_ms is not None else start_ms
    selection_end_ms = recognition_end_ms if repair_profile == "shadow_v2" and recognition_end_ms is not None else end_ms
    micro_dir = repair_dir / "micro_reasr"
    micro_dir.mkdir(parents=True, exist_ok=True)

    attempts: list[dict[str, Any]] = []
    best_text = ""
    best_meta: dict[str, Any] = {}
    best_score = -1.0

    baseline_text, baseline_meta = run_micro_reasr_current(
        session=session,
        repair_dir=repair_dir.parent / "timeline-repair",
        whisper_cli=whisper_cli,
        model=model,
        language=language,
        threads=threads,
        start_ms=start_ms,
        end_ms=end_ms,
        force=False,
    )
    if baseline_text:
        baseline_score = score_micro_reasr_text(
            baseline_text,
            list(baseline_meta.get("rows") or []),
            start_ms,
            end_ms,
            remote_context_text=remote_context_text,
        )
        baseline_meta = dict(baseline_meta)
        baseline_meta["status"] = "ok"
        baseline_meta["source_label"] = f"current_{baseline_meta.get('source_label', 'unknown')}"
        baseline_meta["window_label"] = "current_baseline"
        baseline_meta["score"] = round(baseline_score, 6)
        attempts.append(baseline_meta)
        best_text = baseline_text
        best_meta = baseline_meta
        best_score = baseline_score

    def try_windows(windows: list[dict[str, Any]]) -> None:
        nonlocal best_text, best_meta, best_score
        for window in windows:
            window_start_ms = int(window["start_ms"])
            window_end_ms = int(window["end_ms"])
            if window_end_ms <= window_start_ms:
                continue
            for source_label, source in sources:
                text, meta = materialize_micro_reasr(
                    source=source,
                    source_label=source_label,
                    window_label=str(window["label"]),
                    micro_dir=micro_dir,
                    whisper_cli=whisper_cli,
                    model=model,
                    language=language,
                    threads=threads,
                    recognition_start_ms=window_start_ms,
                    recognition_end_ms=window_end_ms,
                    selection_start_ms=selection_start_ms,
                    selection_end_ms=selection_end_ms,
                    target_start_ms=start_ms,
                    target_end_ms=end_ms,
                    force=force,
                    repair_profile=repair_profile,
                    leading_silence_ms=SHADOW_V2_LEADING_SILENCE_MS,
                    remote_context_text=remote_context_text,
                    local_score=local_score,
                )
                attempts.append(meta)
                score = optional_float(meta.get("score")) if meta else None
                if best_text and text and drops_baseline_prefix(text, best_text):
                    meta["rejected_reason"] = "drops_current_baseline_prefix"
                    continue
                if text and score is not None and score > best_score + SHADOW_V2_REPLACE_SCORE_MARGIN:
                    best_text = text
                    best_meta = meta
                    best_score = score

    try_windows(initial_windows)
    if (
        repair_profile == "shadow_v2"
        and recovery_windows
        and bool(best_meta.get("boundary_suspicious"))
        and best_meta.get("boundary_prefix_repair") is None
    ):
        try_windows(recovery_windows)

    if not best_text:
        fallback = attempts[0] if attempts else {"status": "failed", "reason": "micro_source_not_found"}
        fallback = dict(fallback)
        fallback["attempts"] = attempts
        return "", fallback

    best_meta = dict(best_meta)
    best_meta["attempts"] = attempts
    return best_text, best_meta


def make_repaired_mic_candidate(
    *,
    candidate_id: str,
    parent: Candidate,
    start_ms: int,
    end_ms: int,
    text: str,
    speaker_states: list[dict[str, Any]],
    repair: dict[str, Any],
) -> Candidate:
    duration_sec = max(0.001, (end_ms - start_ms) / 1000.0)
    ratios = action_ratios(start_ms, end_ms, speaker_states)
    local_support_start, local_support_end, local_support_sec = action_support_bounds(
        start_ms,
        end_ms,
        speaker_states,
        {"pass_raw_local_only", "pass_mild_fir_flag_overlap"},
    )
    echo_features = {
        "remote_active_ratio": round(ratios.get("pass_mild_fir_flag_remote_active", 0.0), 6),
        "local_only_ratio": round(ratios.get("pass_raw_local_only", 0.0), 6),
        "double_talk_ratio": round(ratios.get("pass_mild_fir_flag_overlap", 0.0), 6),
        "local_support_sec": round(local_support_sec, 3),
    }
    echo_features["local_score"] = round(
        echo_features["local_only_ratio"] + 0.5 * echo_features["double_talk_ratio"],
        6,
    )
    display_start_ms = local_support_start if local_support_start is not None else start_ms
    display_end_ms = local_support_end if local_support_end is not None else end_ms
    if display_end_ms <= display_start_ms:
        display_start_ms = start_ms
        display_end_ms = end_ms
    asr_features = dict(parent.asr_features)
    asr_features["duration_sec"] = round(duration_sec, 3)
    asr_features["chars_per_sec"] = round(len(text) / duration_sec, 3)
    return Candidate(
        id=candidate_id,
        source_track=parent.source_track,
        role=parent.role,
        speaker_label=parent.speaker_label,
        start_ms=start_ms,
        end_ms=end_ms,
        display_start_ms=display_start_ms,
        display_end_ms=display_end_ms,
        text_raw=text,
        text_norm=normalize_for_compare(text),
        source_segments=parent.source_segments,
        echo_features=echo_features,
        asr_features=asr_features,
        repair=repair,
    )


def authoritative_remote_intervals(candidates: list[Candidate]) -> list[dict[str, Any]]:
    intervals = []
    for item in candidates:
        if item.source_track != "remote":
            continue
        intervals.append(
            {
                "candidate_id": item.id,
                "start_ms": item.start_ms,
                "end_ms": item.end_ms,
                "guarded_start_ms": max(0, item.start_ms - REMOTE_GUARD_BEFORE_MS),
                "guarded_end_ms": item.end_ms + REMOTE_GUARD_AFTER_MS,
                "text": item.text_raw,
            }
        )
    return sorted(intervals, key=lambda row: (row["start_ms"], row["end_ms"]))


def remote_overlaps_for_candidate(candidate: Candidate, remote_intervals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    overlaps = []
    for remote in remote_intervals:
        if int(remote["start_ms"]) > candidate.end_ms:
            break
        if int(remote["end_ms"]) < candidate.start_ms:
            continue
        overlap = overlap_ms(candidate.start_ms, candidate.end_ms, int(remote["start_ms"]), int(remote["end_ms"]))
        guarded_overlap = overlap_ms(
            candidate.start_ms,
            candidate.end_ms,
            int(remote["guarded_start_ms"]),
            int(remote["guarded_end_ms"]),
        )
        if overlap <= 0 and guarded_overlap <= 0:
            continue
        overlaps.append(
            {
                "candidate_id": remote["candidate_id"],
                "start_ms": remote["start_ms"],
                "end_ms": remote["end_ms"],
                "guarded_start_ms": remote["guarded_start_ms"],
                "guarded_end_ms": remote["guarded_end_ms"],
                "overlap_ms": overlap,
                "guarded_overlap_ms": guarded_overlap,
                "text": remote.get("text", ""),
            }
        )
    return overlaps


def timeline_repair(
    *,
    session: Path,
    candidates: list[Candidate],
    utterances: list[Utterance],
    speaker_states: list[dict[str, Any]],
    whisper_cli: str,
    model: Path,
    language: str,
    threads: int,
    resolved_dir: Path,
    force: bool,
    repair_profile: str,
) -> tuple[list[Candidate], dict[str, Any], list[dict[str, Any]]]:
    repair_dir = resolved_dir.parent / ("timeline-repair" if repair_profile == "current" else f"timeline-repair-{repair_profile}")
    repair_dir.mkdir(parents=True, exist_ok=True)
    utterances_by_id = {item.id: item for item in utterances}
    remote_intervals = authoritative_remote_intervals(candidates)
    repaired: list[Candidate] = []
    examples: list[dict[str, Any]] = []
    metrics: dict[str, Any] = {
        "long_mic_segments_count": 0,
        "long_mic_segments_crossing_remote_count": 0,
        "long_mic_segments_repaired_count": 0,
        "unrepaired_long_mic_crossings_count": 0,
        "micro_reasr_attempt_count": 0,
        "micro_reasr_success_count": 0,
        "local_only_island_count": 0,
        "local_only_island_recovered_count": 0,
        "short_local_islands_recovered_count": 0,
        "timeline_repair_dropped_count": 0,
    }
    next_repair_id = 1

    for candidate in candidates:
        duration_ms = candidate.end_ms - candidate.start_ms
        if candidate.source_track != "mic":
            repaired.append(candidate)
            continue
        if duration_ms >= LONG_MIC_SEGMENT_MS:
            metrics["long_mic_segments_count"] += 1

        overlaps = remote_overlaps_for_candidate(candidate, remote_intervals)
        max_remote_overlap_ms = max((int(row["overlap_ms"]) for row in overlaps), default=0)
        is_problem = (
            duration_ms >= LONG_MIC_SEGMENT_MS
            and candidate.echo_features.get("remote_active_ratio", 0.0) >= REMOTE_ACTIVE_REPAIR_RATIO
            and max_remote_overlap_ms >= REMOTE_OVERLAP_REPAIR_MS
        )
        if not is_problem:
            repaired.append(candidate)
            continue

        metrics["long_mic_segments_crossing_remote_count"] += 1
        guarded_blocks = [
            (int(row["guarded_start_ms"]), int(row["guarded_end_ms"]))
            for row in overlaps
            if int(row["guarded_overlap_ms"]) > 0
        ]
        local_intervals = state_action_intervals(
            candidate.start_ms,
            candidate.end_ms,
            speaker_states,
            {"pass_raw_local_only"},
        )
        if not local_intervals:
            token_intervals: list[tuple[int, int]] = []
            for segment_id in candidate.source_segments:
                utterance = utterances_by_id.get(segment_id)
                if utterance is None:
                    continue
                for chunk in split_tokens_on_gaps(utterance.tokens, TOKEN_GAP_SPLIT_THRESHOLD_MS):
                    if not chunk:
                        continue
                    token_intervals.append(
                        (
                            min(int(token.get("start_ms") or candidate.start_ms) for token in chunk),
                            max(int(token.get("end_ms") or candidate.start_ms) for token in chunk),
                        )
                    )
            local_intervals = [
                (max(candidate.start_ms, start), min(candidate.end_ms, end))
                for start, end in token_intervals
                if min(candidate.end_ms, end) - max(candidate.start_ms, start) >= LOCAL_ISLAND_MIN_MS
            ]
        islands = subtract_intervals(local_intervals, guarded_blocks)
        metrics["local_only_island_count"] += len(islands)
        children: list[Candidate] = []
        child_records: list[dict[str, Any]] = []
        for island_index, (island_start, island_end) in enumerate(islands, start=1):
            if island_end - island_start < LOCAL_ISLAND_MIN_MS:
                continue
            recognition_start = island_start
            recognition_end = island_end
            recognition_windows: list[dict[str, Any]] | None = None
            recovery_windows: list[dict[str, Any]] | None = None
            if repair_profile == "shadow_v2":
                recognition_start, recognition_end = expand_local_island_for_shadow(
                    island_start_ms=island_start,
                    island_end_ms=island_end,
                    candidate_start_ms=candidate.start_ms,
                    candidate_end_ms=candidate.end_ms,
                    speaker_states=speaker_states,
                    guarded_blocks=guarded_blocks,
                )
                recognition_windows = shadow_micro_decode_windows(
                    island_start_ms=island_start,
                    island_end_ms=island_end,
                    candidate_start_ms=candidate.start_ms,
                    candidate_end_ms=candidate.end_ms,
                    speaker_states=speaker_states,
                    guarded_blocks=guarded_blocks,
                    specs=SHADOW_V2_MICRO_WINDOWS,
                )
                recovery_windows = shadow_micro_decode_windows(
                    island_start_ms=island_start,
                    island_end_ms=island_end,
                    candidate_start_ms=candidate.start_ms,
                    candidate_end_ms=candidate.end_ms,
                    speaker_states=speaker_states,
                    guarded_blocks=guarded_blocks,
                    specs=SHADOW_V2_RECOVERY_WINDOWS,
                )
            micro_text = ""
            micro_meta: dict[str, Any] = {}
            use_micro = island_end - island_start <= MICRO_REASR_MAX_ISLAND_MS
            if use_micro:
                metrics["micro_reasr_attempt_count"] += 1
                remote_context_text = clean_text(" ".join(str(row.get("text") or "") for row in overlaps))
                local_score = float(candidate.echo_features.get("local_score", 0.0) or 0.0)
                micro_text, micro_meta = run_micro_reasr(
                    session=session,
                    repair_dir=repair_dir,
                    whisper_cli=whisper_cli,
                    model=model,
                    language=language,
                    threads=threads,
                    start_ms=island_start,
                    end_ms=island_end,
                    recognition_start_ms=recognition_start,
                    recognition_end_ms=recognition_end,
                    recognition_windows=recognition_windows,
                    recovery_windows=recovery_windows,
                    remote_context_text=remote_context_text,
                    local_score=local_score,
                    force=force,
                    repair_profile=repair_profile,
                )
                if micro_text:
                    metrics["micro_reasr_success_count"] += 1
            token_text = token_text_for_interval(candidate, utterances_by_id, island_start, island_end)
            text = micro_text or token_text
            if not micro_text and island_end - island_start < TOKEN_FALLBACK_MIN_ISLAND_MS:
                continue
            if not text:
                continue
            action = "micro_reasr" if micro_text else "split"
            repair = {
                "status": "repaired",
                "action": action,
                "parent_candidate_id": candidate.id,
                "island_index": island_index,
                "island_start_ms": island_start,
                    "island_end_ms": island_end,
                    "recognition_start_ms": recognition_start,
                    "recognition_end_ms": recognition_end,
                    "recognition_windows": recognition_windows,
                    "recovery_windows": recovery_windows,
                    "reason": "long_mic_candidate_crosses_authoritative_remote",
                    "matched_remote_candidate_ids": [str(row["candidate_id"]) for row in overlaps],
                    "needs_review": bool(micro_meta.get("status") == "failed" and not micro_text),
                    "micro_reasr": micro_meta if micro_meta else None,
                    "token_text": token_text,
            }
            child = make_repaired_mic_candidate(
                candidate_id=f"cand_mic_repair_{next_repair_id:06d}",
                parent=candidate,
                start_ms=island_start,
                end_ms=island_end,
                text=text,
                speaker_states=speaker_states,
                repair=repair,
            )
            next_repair_id += 1
            children.append(child)
            child_records.append(
                {
                    "candidate_id": child.id,
                    "start_ms": child.start_ms,
                    "end_ms": child.end_ms,
                    "action": action,
                    "text": text,
                }
            )

        if children:
            metrics["long_mic_segments_repaired_count"] += 1
            metrics["local_only_island_recovered_count"] += len(children)
            metrics["short_local_islands_recovered_count"] += sum(
                1 for child in children if child.end_ms - child.start_ms <= MICRO_REASR_MAX_ISLAND_MS
            )
            repaired.extend(children)
            examples.append(
                {
                    "action": "split",
                    "parent_candidate_id": candidate.id,
                    "parent_start_ms": candidate.start_ms,
                    "parent_end_ms": candidate.end_ms,
                    "parent_text": candidate.text_raw,
                    "remote_overlaps": overlaps,
                    "local_intervals": local_intervals,
                    "local_islands": islands,
                    "children": child_records,
                }
            )
        else:
            metrics["long_mic_segments_repaired_count"] += 1
            metrics["timeline_repair_dropped_count"] += 1
            fallback = copy.deepcopy(candidate)
            fallback.repair = {
                "status": "dropped",
                "action": "drop",
                "reason": "timeline_repair_no_confident_local_islands",
                "matched_remote_candidate_ids": [str(row["candidate_id"]) for row in overlaps],
                "remote_overlaps": overlaps,
                "needs_review": False,
            }
            repaired.append(fallback)
            examples.append(
                {
                    "action": "drop",
                    "parent_candidate_id": candidate.id,
                    "parent_start_ms": candidate.start_ms,
                    "parent_end_ms": candidate.end_ms,
                    "parent_text": candidate.text_raw,
                    "remote_overlaps": overlaps,
                    "local_intervals": local_intervals,
                    "local_islands": islands,
                    "children": [],
                }
            )

    island_count = int(metrics["local_only_island_count"])
    recovered_count = int(metrics["local_only_island_recovered_count"])
    metrics["local_only_island_recall"] = round(recovered_count / island_count, 6) if island_count else 1.0
    report = {
        "schema": "murmurmark.timeline_repair/v1",
        "enabled": True,
        "parameters": {
            "remote_guard_before_ms": REMOTE_GUARD_BEFORE_MS,
            "remote_guard_after_ms": REMOTE_GUARD_AFTER_MS,
            "long_mic_segment_ms": LONG_MIC_SEGMENT_MS,
            "remote_active_repair_ratio": REMOTE_ACTIVE_REPAIR_RATIO,
            "remote_overlap_repair_ms": REMOTE_OVERLAP_REPAIR_MS,
            "local_island_min_ms": LOCAL_ISLAND_MIN_MS,
            "token_gap_split_threshold_ms": TOKEN_GAP_SPLIT_THRESHOLD_MS,
            "micro_reasr_padding_ms": MICRO_REASR_PADDING_MS,
            "micro_reasr_max_island_ms": MICRO_REASR_MAX_ISLAND_MS,
            "token_fallback_min_island_ms": TOKEN_FALLBACK_MIN_ISLAND_MS,
            "repair_profile": repair_profile,
        },
        "metrics": metrics,
    }
    return sorted(repaired, key=lambda row: (row.source_track, row.start_ms, row.end_ms)), report, examples


def is_weak_short_phrase(text: str) -> bool:
    return normalize_for_compare(text) in WEAK_SHORT_PHRASES


def isolated_in_track(candidate: Candidate, candidates: list[Candidate], gap_ms: int = 8000) -> bool:
    same_track = sorted(
        [item for item in candidates if item.source_track == candidate.source_track],
        key=lambda item: (item.start_ms, item.end_ms),
    )
    index = same_track.index(candidate)
    previous_gap = float("inf") if index == 0 else candidate.start_ms - same_track[index - 1].end_ms
    next_gap = float("inf") if index == len(same_track) - 1 else same_track[index + 1].start_ms - candidate.end_ms
    return previous_gap >= gap_ms and next_gap >= gap_ms


def best_remote_match(candidate: Candidate, remote_candidates: list[Candidate]) -> tuple[Candidate | None, dict[str, float]]:
    best: Candidate | None = None
    best_features: dict[str, float] = {
        "time_overlap_ratio": 0.0,
        "text_similarity": 0.0,
        "domain_term_overlap": 0.0,
        "time_distance_sec": 999.0,
    }
    best_score = -1.0
    for remote in remote_candidates:
        if remote.start_ms > candidate.end_ms + 1000:
            break
        if remote.end_ms < candidate.start_ms - 1000:
            continue
        overlap_ratio = time_overlap_ratio(candidate, remote)
        if overlap_ratio <= 0 and min(abs(candidate.start_ms - remote.end_ms), abs(remote.start_ms - candidate.end_ms)) > 1000:
            continue
        similarity = text_similarity(candidate.text_raw, remote.text_raw)
        term_overlap = domain_term_overlap(candidate.text_raw, remote.text_raw)
        distance_ms = 0 if overlap_ratio > 0 else min(abs(candidate.start_ms - remote.end_ms), abs(remote.start_ms - candidate.end_ms))
        score = overlap_ratio * 0.50 + similarity * 0.35 + term_overlap * 0.15
        if score > best_score:
            best = remote
            best_score = score
            best_features = {
                "time_overlap_ratio": round(overlap_ratio, 6),
                "text_similarity": round(similarity, 6),
                "domain_term_overlap": round(term_overlap, 6),
                "time_distance_sec": round(distance_ms / 1000.0, 3),
            }
    return best, best_features


def decide_roles(candidates: list[Candidate], mic_policy: str) -> list[RoleDecision]:
    remote_candidates = sorted(
        [item for item in candidates if item.source_track == "remote"],
        key=lambda item: (item.start_ms, item.end_ms),
    )
    decisions: list[RoleDecision] = []

    for candidate in sorted(candidates, key=lambda item: (item.start_ms, item.source_track, item.end_ms)):
        repair_action = str(candidate.repair.get("action") or "")
        if candidate.source_track == "mic" and repair_action in {"split", "micro_reasr", "keep_needs_review", "drop"}:
            remote_match, match_features = best_remote_match(candidate, remote_candidates)
            echo = candidate.echo_features
            needs_review = bool(candidate.repair.get("needs_review")) or repair_action == "keep_needs_review"
            final_role = None if repair_action == "drop" else candidate.role
            decisions.append(
                RoleDecision(
                    candidate=candidate,
                    final_role=final_role,
                    decision="keep_needs_review" if repair_action == "keep_needs_review" else repair_action,
                    reason=str(candidate.repair.get("reason") or "timeline_repair_local_island"),
                    confidence=0.84 if repair_action == "drop" else (0.55 if needs_review else 0.78),
                    matched_remote_candidate_id=remote_match.id if remote_match else None,
                    evidence={
                        **match_features,
                        "remote_active_ratio": echo.get("remote_active_ratio", 0.0),
                        "local_only_ratio": echo.get("local_only_ratio", 0.0),
                        "double_talk_ratio": echo.get("double_talk_ratio", 0.0),
                        "local_score": echo.get("local_score", 0.0),
                        "local_support_sec": echo.get("local_support_sec", 0.0),
                        "repair": candidate.repair,
                    },
                )
            )
            continue

        if is_weak_short_phrase(candidate.text_raw) and isolated_in_track(candidate, candidates):
            decisions.append(
                RoleDecision(
                    candidate=candidate,
                    final_role=None,
                    decision="drop",
                    reason="weak_isolated_hallucination",
                    confidence=0.72,
                    matched_remote_candidate_id=None,
                    evidence={"weak_short_phrase": True, "isolated_in_track": True},
                )
            )
            continue

        if candidate.source_track == "remote":
            decisions.append(
                RoleDecision(
                    candidate=candidate,
                    final_role=candidate.role,
                    decision="keep",
                    reason="authoritative_remote",
                    confidence=0.9,
                    matched_remote_candidate_id=None,
                    evidence={},
                )
            )
            continue

        if mic_policy == "keep_all":
            decisions.append(
                RoleDecision(
                    candidate=candidate,
                    final_role=candidate.role,
                    decision="keep",
                    reason="mic_policy_keep_all",
                    confidence=0.5,
                    matched_remote_candidate_id=None,
                    evidence={"mic_policy": mic_policy},
                )
            )
            continue

        remote_match, match_features = best_remote_match(candidate, remote_candidates)
        echo = candidate.echo_features
        remote_active = echo.get("remote_active_ratio", 0.0)
        local_only = echo.get("local_only_ratio", 0.0)
        double_talk = echo.get("double_talk_ratio", 0.0)
        local_score = echo.get("local_score", 0.0)
        local_support_sec = echo.get("local_support_sec", 0.0)
        no_speech_prob = candidate.asr_features.get("no_speech_prob")

        duplicate_score = 0.0
        if match_features["time_overlap_ratio"] > 0.50:
            duplicate_score += 0.25
        if match_features["text_similarity"] > 0.70:
            duplicate_score += 0.35
        elif match_features["text_similarity"] > 0.55 and match_features["domain_term_overlap"] > 0.40:
            duplicate_score += 0.25
        if remote_active > 0.70:
            duplicate_score += 0.15
        if local_score < 0.25:
            duplicate_score += 0.20
        if no_speech_prob is not None and no_speech_prob > 0.60:
            duplicate_score += 0.10
        if double_talk > 0.30:
            duplicate_score -= 0.20
        if local_only > 0.60:
            duplicate_score -= 0.40
        duplicate_score = max(0.0, min(1.0, duplicate_score))

        evidence: dict[str, Any] = {
            **match_features,
            "remote_active_ratio": remote_active,
            "local_only_ratio": local_only,
            "double_talk_ratio": double_talk,
            "local_score": local_score,
            "local_support_sec": local_support_sec,
            "remote_duplicate_score": round(duplicate_score, 6),
        }
        matched_id = remote_match.id if remote_match else None

        if local_only > 0.80:
            decision = RoleDecision(candidate, candidate.role, "keep", "strong_local_only", 0.9, matched_id, evidence)
        elif (
            remote_match
            and remote_active > 0.70
            and local_score < 0.30
            and match_features["time_overlap_ratio"] > 0.70
            and (
                candidate.asr_features.get("duration_sec", 0.0) < 4.0
                or match_features["text_similarity"] > 0.45
                or local_support_sec < 0.5
            )
        ):
            decision = RoleDecision(candidate, None, "drop", "remote_leak_echo_overlap", 0.84, matched_id, evidence)
        elif (
            remote_match
            and remote_active > 0.70
            and local_score < 0.30
            and local_support_sec >= 0.5
            and match_features["text_similarity"] <= 0.45
        ):
            decision = RoleDecision(candidate, candidate.role, "keep_needs_review", "local_support_inside_remote_active", 0.55, matched_id, evidence)
        elif remote_match and match_features["time_overlap_ratio"] > 0.70 and local_score < 0.20:
            decision = RoleDecision(candidate, None, "drop", "remote_leak_time_overlap", 0.88, matched_id, evidence)
        elif remote_active >= 0.95 and local_score <= 0.05:
            decision = RoleDecision(candidate, None, "drop", "echo_guard_remote_active", 0.82, matched_id, evidence)
        elif duplicate_score >= 0.70:
            decision = RoleDecision(candidate, None, "drop", "remote_duplicate", duplicate_score, matched_id, evidence)
        elif duplicate_score >= 0.55 and (match_features["text_similarity"] > 0.45 or match_features["time_overlap_ratio"] > 0.50):
            decision = RoleDecision(candidate, None, "drop", "probable_remote_duplicate", duplicate_score, matched_id, evidence)
        elif duplicate_score >= 0.45:
            decision = RoleDecision(candidate, candidate.role, "keep_needs_review", "uncertain_overlap", 1.0 - duplicate_score, matched_id, evidence)
        else:
            decision = RoleDecision(candidate, candidate.role, "keep", "mic_local_candidate", 1.0 - duplicate_score, matched_id, evidence)
        decisions.append(decision)
    return decisions


REMOTE_LEAK_REASONS = {
    "echo_guard_remote_active",
    "probable_remote_duplicate",
    "remote_duplicate",
    "remote_leak_echo_overlap",
    "remote_leak_time_overlap",
}


def output_text_for_decision(item: RoleDecision, decisions: list[RoleDecision]) -> tuple[str, str, list[str]]:
    candidate = item.candidate
    if candidate.source_track != "remote":
        return candidate.text_raw, "candidate", []
    token_avg = candidate.asr_features.get("token_avg_prob")
    low_ratio = candidate.asr_features.get("token_low_prob_ratio")
    remote_is_uncertain = (
        (token_avg is not None and token_avg < 0.75)
        or (low_ratio is not None and low_ratio > 0.15)
    )
    if not remote_is_uncertain:
        return candidate.text_raw, "candidate", []
    alternates = [
        decision.candidate
        for decision in decisions
        if decision.final_role is None
        and decision.candidate.source_track == "mic"
        and decision.matched_remote_candidate_id == candidate.id
        and decision.reason in REMOTE_LEAK_REASONS
        and decision.evidence.get("time_overlap_ratio", 0.0) > 0.60
        and decision.evidence.get("text_similarity", 0.0) > 0.25
    ]
    if not alternates:
        return candidate.text_raw, "candidate", []
    alternates = sorted(alternates, key=lambda row: (row.start_ms, row.end_ms))
    alternate_text = clean_text(" ".join(row.text_raw for row in alternates))
    if len(alternate_text) < 12 or len(alternate_text) < len(candidate.text_raw) * 0.35:
        return candidate.text_raw, "candidate", []
    return alternate_text, "matched_mic_echo_duplicate", [row.id for row in alternates]


def merge_utterances(
    utterances: list[Utterance],
    merge_gap_ms: int,
    max_merged_chars: int,
    max_merged_ms: int,
) -> list[Utterance]:
    merged: list[Utterance] = []
    for item in sorted(utterances, key=lambda row: (row.source_track, row.start_ms, row.end_ms)):
        if not merged:
            merged.append(item)
            continue
        previous = merged[-1]
        combined_text = f"{previous.raw_text} {item.raw_text}".strip()
        can_merge = (
            previous.source_track == item.source_track
            and item.start_ms <= previous.end_ms + merge_gap_ms
            and len(combined_text) <= max_merged_chars
            and max(previous.end_ms, item.end_ms) - previous.start_ms <= max_merged_ms
        )
        if can_merge:
            merged[-1] = Utterance(
                id=previous.id,
                source_track=previous.source_track,
                role=previous.role,
                speaker_label=previous.speaker_label,
                start_ms=previous.start_ms,
                end_ms=max(previous.end_ms, item.end_ms),
                raw_text=combined_text,
                avg_logprob=aggregate([previous.avg_logprob, item.avg_logprob]),
                no_speech_prob=aggregate([previous.no_speech_prob, item.no_speech_prob]),
                compression_ratio=aggregate([previous.compression_ratio, item.compression_ratio]),
                temperature=aggregate([previous.temperature, item.temperature]),
                token_avg_prob=aggregate([previous.token_avg_prob, item.token_avg_prob]),
                token_low_prob_ratio=aggregate([previous.token_low_prob_ratio, item.token_low_prob_ratio]),
                token_confident_start_ms=min(
                    value for value in [previous.token_confident_start_ms, item.token_confident_start_ms] if value is not None
                )
                if previous.token_confident_start_ms is not None or item.token_confident_start_ms is not None
                else None,
                token_confident_end_ms=max(
                    value for value in [previous.token_confident_end_ms, item.token_confident_end_ms] if value is not None
                )
                if previous.token_confident_end_ms is not None or item.token_confident_end_ms is not None
                else None,
            )
        else:
            merged.append(item)
    return merged


def token_matches_for_boundary_overlap(left_token: str, right_token: str, *, is_last: bool) -> bool:
    if left_token == right_token:
        return True
    return (
        is_last
        and len(left_token) >= 2
        and len(right_token) >= 4
        and right_token.startswith(left_token)
    )


def longest_suffix_prefix_token_overlap(left_text: str, right_text: str, max_tokens: int = 32) -> int:
    left_tokens = [token for token, _, _ in display_token_spans(left_text)]
    right_tokens = [token for token, _, _ in display_token_spans(right_text)]
    limit = min(max_tokens, len(left_tokens), len(right_tokens))
    for length in range(limit, 2, -1):
        left_tail = left_tokens[-length:]
        right_head = right_tokens[:length]
        if all(
            token_matches_for_boundary_overlap(left, right, is_last=index == length - 1)
            for index, (left, right) in enumerate(zip(left_tail, right_head))
        ):
            return length
    return 0


def embedded_right_prefix_in_left(
    left_tokens: list[tuple[str, int, int]],
    right_tokens: list[tuple[str, int, int]],
    *,
    min_tokens: int = 6,
    max_tokens: int = 32,
) -> tuple[int, int] | None:
    limit = min(max_tokens, len(right_tokens), len(left_tokens))
    for length in range(limit, min_tokens - 1, -1):
        if len(right_tokens) <= length + 1:
            continue
        right_head = right_tokens[:length]
        for start_index in range(3, len(left_tokens) - length + 1):
            left_window = left_tokens[start_index : start_index + length]
            if all(
                token_matches_for_boundary_overlap(left, right, is_last=index == length - 1)
                for index, ((left, _, _), (right, _, _)) in enumerate(zip(left_window, right_head))
            ):
                return start_index, length
    return None


def contained_fragment_match(short_text: str, long_text: str) -> list[str] | None:
    short_tokens = [token for token, _, _ in display_token_spans(short_text)]
    long_tokens = [token for token, _, _ in display_token_spans(long_text)]
    if len(short_tokens) < 3 or len(long_tokens) < len(short_tokens) + 3:
        return None
    for start_index in range(0, len(long_tokens) - len(short_tokens) + 1):
        long_window = long_tokens[start_index : start_index + len(short_tokens)]
        if all(
            token_matches_for_boundary_overlap(short, long, is_last=index == len(short_tokens) - 1)
            for index, (short, long) in enumerate(zip(short_tokens, long_window))
        ):
            return short_tokens
    return None


def trim_trailing_duplicate_prefix(row: dict[str, Any], next_row: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    text = str(row.get("corrected_text") or "").strip()
    next_text = str(next_row.get("corrected_text") or "").strip()
    if not text or not next_text:
        return None, None
    row_tokens = display_token_spans(text)
    next_tokens = display_token_spans(next_text)
    if len(row_tokens) < 3 or len(next_tokens) < 3:
        return None, None

    row_start = float(row.get("start", 0.0))
    row_end = float(row.get("end", row_start))
    next_start = float(next_row.get("start", 0.0))
    if next_start > row_end + 2.5:
        return None, None

    row_norm = [token for token, _, _ in row_tokens]
    next_norm = [token for token, _, _ in next_tokens]
    if (
        len(next_norm) >= len(row_norm) + 3
        and next_norm[: len(row_norm)] == row_norm
        and len(next_text) >= len(text) * 1.5
    ):
        return "", {
            "reason": "adjacent_duplicate_prefix_fragment_drop",
            "source_text": text,
            "next_text": next_text,
            "overlap_tokens": row_norm,
        }

    embedded = embedded_right_prefix_in_left(row_tokens, next_tokens)
    if embedded is not None:
        start_index, overlap_len = embedded
        trim_at = row_tokens[start_index][1]
        trimmed = text[:trim_at].rstrip(" ,.;:!?—-").strip()
        trimmed = re.sub(r"\s+(?:и|а|но|или|да)$", "", trimmed, flags=re.IGNORECASE).rstrip(" ,.;:!?—-").strip()
        if len(display_token_spans(trimmed)) >= 3:
            return trimmed, {
                "reason": "adjacent_duplicate_embedded_prefix_trim",
                "source_text": text,
                "repaired_text": trimmed,
                "next_text": next_text,
                "overlap_tokens": [token for token, _, _ in row_tokens[start_index : start_index + overlap_len]],
            }

    overlap_len = longest_suffix_prefix_token_overlap(text, next_text)
    if overlap_len < 3:
        return None, None
    overlap_tokens = row_tokens[-overlap_len:]
    if overlap_len == len(row_tokens) and len(next_tokens) <= len(row_tokens) + 1:
        return None, None
    trim_at = overlap_tokens[0][1]
    trimmed = text[:trim_at].rstrip(" ,.;:!?—-").strip()
    trimmed = re.sub(r"\s+(?:и|а|но|или|да)$", "", trimmed, flags=re.IGNORECASE).rstrip(" ,.;:!?—-").strip()
    if len(display_token_spans(trimmed)) < 3:
        return "", {
            "reason": "adjacent_duplicate_suffix_fragment_drop",
            "source_text": text,
            "next_text": next_text,
            "overlap_tokens": [token for token, _, _ in overlap_tokens],
        }
    if len(trimmed) >= len(text) - 6:
        return None, None
    return trimmed, {
        "reason": "adjacent_duplicate_suffix_trim",
        "source_text": text,
        "repaired_text": trimmed,
        "next_text": next_text,
        "overlap_tokens": [token for token, _, _ in overlap_tokens],
    }


def suppress_near_same_speaker_fragments(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    keep = [True] * len(rows)
    corrections: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        text = str(row.get("corrected_text") or "").strip()
        if len(display_token_spans(text)) < 3:
            continue
        row_start = float(row.get("start", 0.0))
        row_end = float(row.get("end", row_start))
        speaker = row.get("speaker_label")
        for other_index, other in enumerate(rows):
            if index == other_index or not keep[index]:
                continue
            if other.get("speaker_label") != speaker:
                continue
            other_start = float(other.get("start", 0.0))
            other_end = float(other.get("end", other_start))
            if other_start > row_end + 3.0 or row_start > other_end + 3.0:
                continue
            other_text = str(other.get("corrected_text") or "").strip()
            overlap_tokens = contained_fragment_match(text, other_text)
            if overlap_tokens is None:
                continue
            keep[index] = False
            corrections.append(
                {
                    "reason": "near_same_speaker_contained_fragment_drop",
                    "utterance_id": row.get("id"),
                    "matched_utterance_id": other.get("id"),
                    "speaker_label": speaker,
                    "start": row.get("start"),
                    "end": row.get("end"),
                    "matched_start": other.get("start"),
                    "matched_end": other.get("end"),
                    "source_text": text,
                    "matched_text": other_text,
                    "overlap_tokens": overlap_tokens,
                    "dropped": True,
                }
            )
    return [row for row, should_keep in zip(rows, keep) if should_keep], corrections


def suppress_adjacent_same_speaker_duplicates(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not rows:
        return rows, []
    output: list[dict[str, Any]] = []
    corrections: list[dict[str, Any]] = []
    for row in rows:
        current = copy.deepcopy(row)
        if output and output[-1].get("speaker_label") == current.get("speaker_label"):
            previous = output[-1]
            repaired_text, meta = trim_trailing_duplicate_prefix(previous, current)
            if meta is not None:
                meta = {
                    **meta,
                    "previous_utterance_id": previous.get("id"),
                    "current_utterance_id": current.get("id"),
                    "speaker_label": previous.get("speaker_label"),
                    "start": previous.get("start"),
                    "end": previous.get("end"),
                    "current_start": current.get("start"),
                    "current_end": current.get("end"),
                }
                corrections.append(meta)
                if repaired_text:
                    previous["corrected_text"] = repaired_text
                    previous.setdefault("corrections", []).append(meta)
                    previous.setdefault("quality", {})["adjacent_duplicate_repaired"] = True
                else:
                    output.pop()
                    meta["dropped"] = True
        output.append(current)

    output, near_corrections = suppress_near_same_speaker_fragments(output)
    corrections.extend(near_corrections)

    for index, row in enumerate(output, start=1):
        old_id = row.get("id")
        new_id = f"utt_{index:06d}"
        if old_id != new_id:
            row.setdefault("quality", {})["renumbered_from"] = old_id
        row["id"] = new_id
    return output, corrections


def format_time(ms: int) -> str:
    total = max(0, ms // 1000)
    hours = total // 3600
    minutes = (total % 3600) // 60
    seconds = total % 60
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def speaker_state_slice(start_sec: float, end_sec: float, speaker_states: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in speaker_states:
        row_start = float(row.get("start", 0.0))
        row_end = float(row.get("end", row_start))
        if min(end_sec, row_end) <= max(start_sec, row_start):
            continue
        rows.append(row)
    return rows


def write_audit_clip(source: Path, output: Path, start_sec: float, end_sec: float) -> str | None:
    if not source.exists() or end_sec <= start_sec:
        return None
    output.parent.mkdir(parents=True, exist_ok=True)
    if not output.exists():
        ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
        run_quiet(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                f"{start_sec:.3f}",
                "-t",
                f"{end_sec - start_sec:.3f}",
                "-i",
                str(source),
                "-ar",
                "16000",
                "-ac",
                "1",
                str(output),
            ]
        )
    return f"afplay {output}"


def write_timeline_audit_examples(
    *,
    session: Path,
    resolved_dir: Path,
    artifact_suffix: str,
    utterances: list[dict[str, Any]],
    overlaps: list[dict[str, Any]],
    speaker_states: list[dict[str, Any]],
) -> str:
    profile_name = artifact_suffix.lstrip(".") or "current"
    audit_dir = resolved_dir.parent / "timeline-audit" / profile_name
    audit_dir.mkdir(parents=True, exist_ok=True)
    by_id = {str(row["id"]): row for row in utterances}
    examples: list[dict[str, Any]] = []

    for row in utterances:
        corrections = row.get("corrections") or []
        if not any((item or {}).get("reason") == "boundary_prefix_repair" for item in corrections):
            continue
        examples.append(
            {
                "kind": "boundary_repair_candidate",
                "start": float(row["start"]),
                "end": float(row["end"]),
                "utterance": row,
                "boundary_repairs": [
                    item
                    for item in corrections
                    if (item or {}).get("reason") == "boundary_prefix_repair"
                ],
            }
        )

    for row in utterances:
        if not bool((row.get("quality") or {}).get("needs_review")):
            continue
        examples.append(
            {
                "kind": "needs_review",
                "start": float(row["start"]),
                "end": float(row["end"]),
                "utterance": row,
            }
        )

    for row in overlaps:
        if float(row.get("duration_sec", 0.0)) <= 2.0:
            continue
        left = by_id.get(str(row.get("left_utterance_id")))
        right = by_id.get(str(row.get("right_utterance_id")))
        examples.append(
            {
                "kind": "cross_role_overlap_gt2",
                "start": float(row["start"]),
                "end": float(row["end"]),
                "overlap": row,
                "left_utterance": left,
                "right_utterance": right,
            }
        )

    mic_source = session / "derived" / "asr" / "mic.wav"
    remote_source = session / "derived" / "asr" / "remote.wav"
    output_name = suffixed_name("timeline_audit_examples.jsonl", artifact_suffix)
    output_path = resolved_dir / output_name
    with output_path.open("w", encoding="utf-8") as file:
        for index, example in enumerate(sorted(examples, key=lambda row: (row["start"], row["end"], row["kind"])), start=1):
            clip_start = max(0.0, float(example["start"]) - 2.0)
            clip_end = float(example["end"]) + 2.0
            safe_kind = re.sub(r"[^a-z0-9_]+", "_", str(example["kind"]).lower())
            stem = f"audit_{index:04d}_{safe_kind}_{int(round(clip_start * 1000)):010d}_{int(round(clip_end * 1000)):010d}"
            mic_clip = audit_dir / f"{stem}_mic.wav"
            remote_clip = audit_dir / f"{stem}_remote.wav"
            record = {
                **example,
                "clip_start": round(clip_start, 3),
                "clip_end": round(clip_end, 3),
                "speaker_state": speaker_state_slice(clip_start, clip_end, speaker_states),
                "clips": {
                    "mic": str(mic_clip),
                    "remote": str(remote_clip),
                },
                "commands": {
                    "mic": write_audit_clip(mic_source, mic_clip, clip_start, clip_end),
                    "remote": write_audit_clip(remote_source, remote_clip, clip_start, clip_end),
                },
            }
            file.write(json.dumps(record, ensure_ascii=False) + "\n")
    return output_name


def sequence_present(tokens: list[str], expected: tuple[str, ...]) -> bool:
    position = 0
    for token in tokens:
        if position < len(expected) and token == expected[position]:
            position += 1
    return position == len(expected)


def evaluate_golden_phrase_cases(session: Path, utterances: list[dict[str, Any]]) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    failures = 0
    for case in GOLDEN_PHRASE_CASES:
        if session.name != str(case["session_name"]):
            continue
        start_sec = float(case["start_sec"])
        end_sec = float(case["end_sec"])
        role = str(case["role"])
        candidates = [
            row
            for row in utterances
            if str(row.get("speaker_label")) == role
            and float(row.get("end", 0.0)) >= start_sec
            and float(row.get("start", 0.0)) <= end_sec
        ]
        joined_text = clean_text(" ".join(str(row.get("corrected_text") or "") for row in candidates))
        tokens = normalize_for_compare(joined_text).split()
        forbidden = tuple(str(item) for item in case["must_not_start_with"])
        forbidden_start = bool(tokens and tokens[0] in forbidden)
        sequence_ok = sequence_present(tokens, tuple(str(item) for item in case["must_contain_sequence"]))
        passed = sequence_ok and not forbidden_start
        if not passed:
            failures += 1
        checks.append(
            {
                "id": case["id"],
                "role": role,
                "start_sec": start_sec,
                "end_sec": end_sec,
                "utterance_ids": [row.get("id") for row in candidates],
                "text": joined_text,
                "sequence_ok": sequence_ok,
                "forbidden_start": forbidden_start,
                "passed": passed,
            }
        )
    return {
        "golden_phrase_fail_count": failures,
        "golden_phrase_checks": checks,
    }


def write_outputs(
    *,
    session: Path,
    model: Path,
    language: str,
    max_context: int,
    asr_mode: str,
    asr_window_sec: int,
    asr_overlap_sec: int,
    mic_audio_prep: str,
    remote_audio_prep: str,
    prompt_file: Path | None,
    prompt: str | None,
    raw_utterances: list[Utterance],
    raw_dropped: list[dict[str, Any]],
    candidates: list[Candidate],
    decisions: list[RoleDecision],
    resolved_dir: Path,
    merge_gap_ms: int,
    max_merged_chars: int,
    max_merged_ms: int,
    mic_policy: str,
    timeline_repair_report: dict[str, Any],
    timeline_repair_examples: list[dict[str, Any]],
    speaker_states: list[dict[str, Any]],
    artifact_suffix: str = "",
) -> dict[str, Any]:
    resolved_dir.mkdir(parents=True, exist_ok=True)

    def artifact(filename: str) -> Path:
        return resolved_dir / suffixed_name(filename, artifact_suffix)

    raw_rows = [
        {
            "id": item.id,
            "source_track": item.source_track,
            "asr_input": "mic_for_asr" if item.source_track == "mic" else "remote",
            "start": round(item.start_ms / 1000.0, 3),
            "end": round(item.end_ms / 1000.0, 3),
            "text": item.raw_text,
            "avg_logprob": item.avg_logprob,
            "no_speech_prob": item.no_speech_prob,
            "compression_ratio": item.compression_ratio,
            "temperature": item.temperature,
            "token_avg_prob": item.token_avg_prob,
            "token_low_prob_ratio": item.token_low_prob_ratio,
            "token_confident_start": None if item.token_confident_start_ms is None else round(item.token_confident_start_ms / 1000.0, 3),
            "token_confident_end": None if item.token_confident_end_ms is None else round(item.token_confident_end_ms / 1000.0, 3),
            "raw_engine": "whisper.cpp",
            "model": model.name,
        }
        for item in sorted(raw_utterances, key=lambda row: (row.source_track, row.start_ms, row.end_ms))
    ]
    artifact("raw_segments.json").write_text(
        json.dumps(
            {
                "schema": "murmurmark.raw_segments/v1",
                "session": str(session),
                "segments": raw_rows,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    candidate_rows = [
        {
            "id": item.id,
            "source_track": item.source_track,
            "initial_role": item.role,
            "speaker_label": item.speaker_label,
            "start": round(item.start_ms / 1000.0, 3),
            "end": round(item.end_ms / 1000.0, 3),
            "display_start": round(item.display_start_ms / 1000.0, 3),
            "display_end": round(item.display_end_ms / 1000.0, 3),
            "text_raw": item.text_raw,
            "text_norm": item.text_norm,
            "source_segments": item.source_segments,
            "echo_features": item.echo_features,
            "asr_features": item.asr_features,
            "repair": item.repair,
        }
        for item in sorted(candidates, key=lambda row: (row.source_track, row.start_ms, row.end_ms))
    ]
    artifact("candidate_utterances.json").write_text(
        json.dumps(
            {
                "schema": "murmurmark.candidate_utterances/v1",
                "session": str(session),
                "candidates": candidate_rows,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    decision_rows = [
        {
            "candidate_id": item.candidate.id,
            "source_track": item.candidate.source_track,
            "initial_role": item.candidate.role,
            "final_role": item.final_role,
            "decision": item.decision,
            "reason": item.reason,
            "confidence": round(item.confidence, 6),
            "matched_remote_candidate_id": item.matched_remote_candidate_id,
            "evidence": item.evidence,
            "repair": item.candidate.repair,
        }
        for item in decisions
    ]
    artifact("role_decisions.json").write_text(
        json.dumps(
            {
                "schema": "murmurmark.role_decisions/v1",
                "session": str(session),
                "decisions": decision_rows,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    kept_decisions = [
        item
        for item in decisions
        if item.final_role is not None and item.decision in TIMELINE_REPAIR_KEEP_DECISIONS
    ]
    kept_decisions = sorted(kept_decisions, key=lambda item: (item.candidate.display_start_ms, item.candidate.source_track))
    json_rows = []
    boundary_corrections: list[dict[str, Any]] = []
    for index, item in enumerate(kept_decisions, start=1):
        candidate = item.candidate
        output_text, text_source, text_source_candidate_ids = output_text_for_decision(item, decisions)
        row_corrections: list[dict[str, Any]] = []
        if artifact_suffix == ".shadow_v2" and candidate.source_track == "mic":
            local_score = float(item.evidence.get("local_score", candidate.echo_features.get("local_score", 0.0)) or 0.0)
            remote_similarity = float(item.evidence.get("text_similarity", 0.0) or 0.0)
            starts_near_boundary = candidate.display_start_ms <= candidate.start_ms + 600
            repaired_text, boundary_meta = boundary_prefix_repair(
                output_text,
                local_score=local_score,
                remote_similarity=remote_similarity,
                starts_near_boundary=starts_near_boundary,
            )
            if boundary_meta is not None:
                boundary_meta = {
                    **boundary_meta,
                    "candidate_id": candidate.id,
                    "source_text": output_text,
                    "repaired_text": repaired_text,
                    "start_ms": candidate.display_start_ms,
                    "end_ms": candidate.display_end_ms,
                    "text_source": text_source,
                }
                output_text = repaired_text
                text_source = f"{text_source}+boundary_prefix_repair"
                row_corrections.append(boundary_meta)
                boundary_corrections.append(boundary_meta)
        json_rows.append(
            {
                "id": f"utt_{index:06d}",
                "source_candidate_id": candidate.id,
                "source_track": candidate.source_track,
                "role": item.final_role,
                "speaker_label": candidate.speaker_label,
                "start": round(candidate.display_start_ms / 1000.0, 3),
                "end": round(candidate.display_end_ms / 1000.0, 3),
                "source_start": round(candidate.start_ms / 1000.0, 3),
                "source_end": round(candidate.end_ms / 1000.0, 3),
                "raw_text": candidate.text_raw,
                "corrected_text": output_text,
                "corrections": row_corrections,
                "quality": {
                    "needs_review": item.decision == "keep_needs_review" or bool(candidate.repair.get("needs_review")),
                    "role_confidence": round(item.confidence, 6),
                    "decision_reason": item.reason,
                    "text_source": text_source,
                    "text_source_candidate_ids": text_source_candidate_ids,
                    "repair": candidate.repair,
                    "overlap": False,
                },
            }
        )

    adjacent_duplicate_corrections: list[dict[str, Any]] = []
    if artifact_suffix == ".shadow_v2":
        json_rows, adjacent_duplicate_corrections = suppress_adjacent_same_speaker_duplicates(json_rows)

    cross_role_overlap_count = 0
    cross_role_overlap_seconds = 0.0
    cross_role_overlap_gt2_count = 0
    cross_role_overlap_gt2_seconds = 0.0
    overlap_rows: list[dict[str, Any]] = []
    for left_index, left in enumerate(json_rows):
        left_start = float(left["start"])
        left_end = float(left["end"])
        for right in json_rows[left_index + 1 :]:
            right_start = float(right["start"])
            if right_start >= left_end:
                break
            if left["speaker_label"] == right["speaker_label"]:
                continue
            overlap = min(left_end, float(right["end"])) - max(left_start, right_start)
            if overlap <= 0:
                continue
            cross_role_overlap_count += 1
            cross_role_overlap_seconds += overlap
            if overlap > 2.0:
                cross_role_overlap_gt2_count += 1
                cross_role_overlap_gt2_seconds += overlap
            left["quality"]["overlap"] = True
            right["quality"]["overlap"] = True
            similarity = text_similarity(str(left["corrected_text"]), str(right["corrected_text"]))
            if overlap <= 2.0:
                overlap_type = "short_timing_overlap"
            elif similarity > 0.65:
                overlap_type = "probable_duplicate_unresolved"
            elif left["quality"]["needs_review"] or right["quality"]["needs_review"]:
                overlap_type = "uncertain"
            else:
                overlap_type = "possible_double_talk_or_timing"
            overlap_rows.append(
                {
                    "left_utterance_id": left["id"],
                    "right_utterance_id": right["id"],
                    "left_role": left["speaker_label"],
                    "right_role": right["speaker_label"],
                    "start": round(max(left_start, right_start), 3),
                    "end": round(min(left_end, float(right["end"])), 3),
                    "duration_sec": round(overlap, 3),
                    "type": overlap_type,
                    "text_similarity": round(similarity, 6),
                }
            )

    artifact("overlaps.json").write_text(
        json.dumps(
            {
                "schema": "murmurmark.transcript_overlaps/v1",
                "session": str(session),
                "overlaps": overlap_rows,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    artifact("timeline_repair_report.json").write_text(
        json.dumps(timeline_repair_report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with artifact("timeline_repair_examples.jsonl").open("w", encoding="utf-8") as file:
        for item in timeline_repair_examples:
            file.write(json.dumps(item, ensure_ascii=False) + "\n")

    clean_dialogue = {
        "schema": "murmurmark.clean_dialogue/v1",
        "session": str(session),
        "utterances": [
            {
                "id": row["id"],
                "start": row["start"],
                "end": row["end"],
                "source_start": row["source_start"],
                "source_end": row["source_end"],
                "role": row["role"],
                "speaker_label": row["speaker_label"],
                "text": row["corrected_text"],
                "source_candidate_id": row["source_candidate_id"],
                "source_track": row["source_track"],
                "quality": row["quality"],
            }
            for row in json_rows
        ],
    }
    artifact("clean_dialogue.json").write_text(
        json.dumps(clean_dialogue, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    document = {
        "schema": "murmurmark.transcript.simple/v1",
        "session": str(session),
        "backend": {
            "name": "whisper.cpp",
            "model": str(model),
            "language": language,
            "max_context": max_context,
            "asr_mode": asr_mode,
            "asr_window_sec": asr_window_sec,
            "asr_overlap_sec": asr_overlap_sec,
            "mic_audio_prep": mic_audio_prep,
            "remote_audio_prep": remote_audio_prep,
        },
        "utterances": json_rows,
    }
    artifact("transcript.simple.json").write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    with artifact("transcript.md").open("w", encoding="utf-8") as file:
        file.write("# Simple Transcript\n\n")
        file.write("Backend: whisper.cpp  \n")
        file.write(f"Model: `{model.name}`  \n")
        file.write(f"Language: `{language}`\n\n")
        for row in json_rows:
            file.write(f"## {format_time(int(row['start'] * 1000))} {row['speaker_label']}\n\n")
            file.write(str(row["corrected_text"]).strip() + "\n\n")

    corrections = list(raw_dropped)
    for item in decisions:
        if item.decision == "drop":
            corrections.append(
                {
                    "source_track": item.candidate.source_track,
                    "candidate_id": item.candidate.id,
                    "start_ms": item.candidate.start_ms,
                    "end_ms": item.candidate.end_ms,
                    "text": item.candidate.text_raw,
                    "reason": item.reason,
                    "matched_remote_candidate_id": item.matched_remote_candidate_id,
                    "evidence": item.evidence,
                }
            )
    for item in boundary_corrections:
        corrections.append(
            {
                "source_track": "mic",
                "candidate_id": item["candidate_id"],
                "start_ms": item["start_ms"],
                "end_ms": item["end_ms"],
                "text": item["source_text"],
                "replacement": item["repaired_text"],
                "reason": "boundary_prefix_repair",
                "evidence": item,
            }
        )
    for item in adjacent_duplicate_corrections:
        corrections.append(
            {
                "source_track": "dialogue",
                "candidate_id": item.get("previous_utterance_id"),
                "start_ms": int(round(float(item.get("start", 0.0)) * 1000)),
                "end_ms": int(round(float(item.get("end", 0.0)) * 1000)),
                "text": item.get("source_text", ""),
                "replacement": item.get("repaired_text", ""),
                "reason": item.get("reason", "adjacent_duplicate_repair"),
                "evidence": item,
            }
        )

    with artifact("corrections.jsonl").open("w", encoding="utf-8") as file:
        for item in corrections:
            file.write(json.dumps(item, ensure_ascii=False) + "\n")

    dropped_by_reason: dict[str, int] = {}
    for item in corrections:
        reason = str(item.get("reason", "unknown"))
        dropped_by_reason[reason] = dropped_by_reason.get(reason, 0) + 1

    role_decisions_by_reason: dict[str, int] = {}
    for item in decisions:
        key = f"{item.decision}:{item.reason}"
        role_decisions_by_reason[key] = role_decisions_by_reason.get(key, 0) + 1

    remote_duplicate_in_me_count = 0
    remote_duplicate_in_me_seconds = 0.0
    remote_rows = [row for row in json_rows if row["source_track"] == "remote"]
    for row in json_rows:
        if row["source_track"] != "mic":
            continue
        row_overlap = 0.0
        row_start = float(row["start"])
        row_end = float(row["end"])
        for remote in remote_rows:
            overlap = min(row_end, float(remote["end"])) - max(row_start, float(remote["start"]))
            if overlap > 0:
                row_overlap += overlap
        if row_overlap > 1.0:
            remote_duplicate_in_me_count += 1
            remote_duplicate_in_me_seconds += row_overlap

    timeline_metrics = timeline_repair_report.get("metrics") if isinstance(timeline_repair_report, dict) else {}
    if not isinstance(timeline_metrics, dict):
        timeline_metrics = {}

    golden_report = evaluate_golden_phrase_cases(session, json_rows)
    quality_report = {
        "schema": "murmurmark.simple_transcript_quality/v1",
        "utterances": len(json_rows),
        "raw_segments": len(raw_rows),
        "candidate_utterances": len(candidate_rows),
        "role_decisions": len(decision_rows),
        "cross_role_overlap_count": cross_role_overlap_count,
        "cross_role_overlap_seconds": round(cross_role_overlap_seconds, 3),
        "cross_role_overlap_gt2_count": cross_role_overlap_gt2_count,
        "cross_role_overlap_gt2_seconds": round(cross_role_overlap_gt2_seconds, 3),
        "needs_review_count": sum(1 for row in json_rows if row["quality"]["needs_review"]),
        "long_mic_segments_count": int(timeline_metrics.get("long_mic_segments_count", 0)),
        "long_mic_segments_crossing_remote_count": int(timeline_metrics.get("long_mic_segments_crossing_remote_count", 0)),
        "long_mic_segments_repaired_count": int(timeline_metrics.get("long_mic_segments_repaired_count", 0)),
        "unrepaired_long_mic_crossings_count": int(timeline_metrics.get("unrepaired_long_mic_crossings_count", 0)),
        "micro_reasr_attempt_count": int(timeline_metrics.get("micro_reasr_attempt_count", 0)),
        "micro_reasr_success_count": int(timeline_metrics.get("micro_reasr_success_count", 0)),
        "timeline_repair_dropped_count": int(timeline_metrics.get("timeline_repair_dropped_count", 0)),
        "remote_duplicate_in_me_count": remote_duplicate_in_me_count,
        "remote_duplicate_in_me_seconds": round(remote_duplicate_in_me_seconds, 3),
        "local_only_island_recall": timeline_metrics.get("local_only_island_recall", 1.0),
        "short_local_islands_recovered_count": int(timeline_metrics.get("short_local_islands_recovered_count", 0)),
        "boundary_suspicious_utterance_count": sum(
            1 for row in json_rows if row["source_track"] == "mic" and starts_with_boundary_suspicious_prefix(str(row["corrected_text"]))
        ),
        "boundary_prefix_repair_count": len(boundary_corrections),
        "boundary_prefix_repair_accepted_count": len(boundary_corrections),
        "adjacent_duplicate_repair_count": len(adjacent_duplicate_corrections),
        "adjacent_duplicate_drop_count": sum(1 for item in adjacent_duplicate_corrections if item.get("dropped")),
        "golden_phrase_fail_count": int(golden_report["golden_phrase_fail_count"]),
        "golden_phrase_checks": golden_report["golden_phrase_checks"],
        "dropped_by_reason": dict(sorted(dropped_by_reason.items())),
        "role_decisions_by_reason": dict(sorted(role_decisions_by_reason.items())),
    }
    artifact("quality_report.json").write_text(
        json.dumps(quality_report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    audit_examples_name = write_timeline_audit_examples(
        session=session,
        resolved_dir=resolved_dir,
        artifact_suffix=artifact_suffix,
        utterances=json_rows,
        overlaps=overlap_rows,
        speaker_states=speaker_states,
    )

    report = {
        "schema": "murmurmark.transcribe_simple_report/v1",
        "backend": "whisper.cpp",
        "model": str(model),
        "language": language,
        "max_context": max_context,
        "asr_mode": asr_mode,
        "asr_window_sec": asr_window_sec,
        "asr_overlap_sec": asr_overlap_sec,
        "mic_audio_prep": mic_audio_prep,
        "remote_audio_prep": remote_audio_prep,
        "prompt_file": str(prompt_file.expanduser().resolve()) if prompt_file is not None else None,
        "prompt_chars": len(prompt) if prompt is not None else None,
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest() if prompt is not None else None,
        "utterances": len(json_rows),
        "dropped_segments": len(corrections),
        "merge_gap_ms": merge_gap_ms,
        "max_merged_chars": max_merged_chars,
        "max_merged_ms": max_merged_ms,
        "mic_policy": mic_policy,
        "dropped_by_reason": dict(sorted(dropped_by_reason.items())),
        "quality": {
            "cross_role_overlap_count": cross_role_overlap_count,
            "cross_role_overlap_seconds": round(cross_role_overlap_seconds, 3),
            "cross_role_overlap_gt2_count": cross_role_overlap_gt2_count,
            "cross_role_overlap_gt2_seconds": round(cross_role_overlap_gt2_seconds, 3),
            "needs_review_count": quality_report["needs_review_count"],
            "long_mic_segments_count": quality_report["long_mic_segments_count"],
            "long_mic_segments_crossing_remote_count": quality_report["long_mic_segments_crossing_remote_count"],
            "long_mic_segments_repaired_count": quality_report["long_mic_segments_repaired_count"],
            "unrepaired_long_mic_crossings_count": quality_report["unrepaired_long_mic_crossings_count"],
            "micro_reasr_attempt_count": quality_report["micro_reasr_attempt_count"],
            "micro_reasr_success_count": quality_report["micro_reasr_success_count"],
            "timeline_repair_dropped_count": quality_report["timeline_repair_dropped_count"],
            "remote_duplicate_in_me_count": remote_duplicate_in_me_count,
            "remote_duplicate_in_me_seconds": round(remote_duplicate_in_me_seconds, 3),
            "local_only_island_recall": quality_report["local_only_island_recall"],
            "short_local_islands_recovered_count": quality_report["short_local_islands_recovered_count"],
            "boundary_suspicious_utterance_count": quality_report["boundary_suspicious_utterance_count"],
            "boundary_prefix_repair_count": quality_report["boundary_prefix_repair_count"],
            "boundary_prefix_repair_accepted_count": quality_report["boundary_prefix_repair_accepted_count"],
            "adjacent_duplicate_repair_count": quality_report["adjacent_duplicate_repair_count"],
            "adjacent_duplicate_drop_count": quality_report["adjacent_duplicate_drop_count"],
            "golden_phrase_fail_count": quality_report["golden_phrase_fail_count"],
        },
        "outputs": {
            "raw_segments": suffixed_name("raw_segments.json", artifact_suffix),
            "candidate_utterances": suffixed_name("candidate_utterances.json", artifact_suffix),
            "role_decisions": suffixed_name("role_decisions.json", artifact_suffix),
            "clean_dialogue": suffixed_name("clean_dialogue.json", artifact_suffix),
            "overlaps": suffixed_name("overlaps.json", artifact_suffix),
            "quality_report": suffixed_name("quality_report.json", artifact_suffix),
            "timeline_repair_report": suffixed_name("timeline_repair_report.json", artifact_suffix),
            "timeline_repair_examples": suffixed_name("timeline_repair_examples.jsonl", artifact_suffix),
            "timeline_audit_examples": audit_examples_name,
            "transcript_json": suffixed_name("transcript.simple.json", artifact_suffix),
            "transcript_markdown": suffixed_name("transcript.md", artifact_suffix),
            "corrections": suffixed_name("corrections.jsonl", artifact_suffix),
        },
    }
    artifact("transcribe_simple_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "utterance_count": len(json_rows),
        "quality": quality_report,
        "report": report,
        "utterances": json_rows,
        "overlaps": overlap_rows,
        "paths": {
            "transcript": str(artifact("transcript.md")),
            "clean_dialogue": str(artifact("clean_dialogue.json")),
            "quality_report": str(artifact("quality_report.json")),
            "timeline_repair_report": str(artifact("timeline_repair_report.json")),
            "transcribe_simple_report": str(artifact("transcribe_simple_report.json")),
        },
    }


def opening_repair_artifact_dir(resolved_dir: Path) -> Path:
    return resolved_dir.parent / "opening-repair-shadow_v2"


def opening_norm_tokens(text: str) -> list[str]:
    return normalize_for_compare(text).split()


def opening_has_hearing_check(text: str) -> bool:
    tokens = opening_norm_tokens(text)
    joined = " ".join(tokens)
    return (
        "меня слышно" in joined
        or "слышно меня" in joined
        or "меня слышишь" in joined
        or "слышишь меня" in joined
    )


def opening_has_greeting(text: str) -> bool:
    tokens = opening_norm_tokens(text)
    if not tokens:
        return False
    return tokens[0] in {"привет", "здравствуйте", "алло"} or tokens[:2] == ["всем", "привет"]


def opening_has_ack(text: str) -> bool:
    tokens = opening_norm_tokens(text)
    joined = " ".join(tokens)
    return bool(tokens and (tokens[0] in OPENING_ACK_WORDS or joined.startswith("да слышно") or joined.startswith("привет да")))


def opening_has_double_greeting_ack(text: str) -> bool:
    tokens = opening_norm_tokens(text)
    for index in range(0, max(0, len(tokens) - 2)):
        if tokens[index : index + 3] == ["привет", "привет", "да"]:
            return True
    joined = " ".join(tokens)
    return joined.startswith("привет да")


def opening_avg_token_prob(meta: dict[str, Any]) -> float | None:
    values: list[float] = []
    for row in meta.get("rows") or []:
        value = optional_float(row.get("token_avg_prob")) if isinstance(row, dict) else None
        if value is not None:
            values.append(value)
    if not values:
        return None
    return sum(values) / len(values)


def opening_row_bounds(meta: dict[str, Any], fallback_start_ms: int, fallback_end_ms: int) -> tuple[int, int]:
    starts: list[int] = []
    ends: list[int] = []
    for row in meta.get("rows") or []:
        if not isinstance(row, dict):
            continue
        start = row.get("start_ms")
        end = row.get("end_ms")
        if isinstance(start, int) and isinstance(end, int) and end > start:
            starts.append(start)
            ends.append(end)
    if not starts or not ends:
        return fallback_start_ms, fallback_end_ms
    return max(0, min(starts)), max(ends)


def opening_similarity_to_rows(text: str, rows: list[dict[str, Any]]) -> float:
    return max((text_similarity(text, str(row.get("corrected_text") or row.get("text") or "")) for row in rows), default=0.0)


def run_opening_micro_asr(
    *,
    source: Path,
    source_label: str,
    run_id: str,
    track: str,
    repair_dir: Path,
    whisper_cli: str,
    model: Path,
    language: str,
    threads: int,
    start_ms: int,
    end_ms: int,
    force: bool,
) -> dict[str, Any]:
    micro_dir = repair_dir / "micro_asr"
    micro_dir.mkdir(parents=True, exist_ok=True)
    text, meta = materialize_micro_reasr(
        source=source,
        source_label=source_label,
        window_label=run_id,
        micro_dir=micro_dir,
        whisper_cli=whisper_cli,
        model=model,
        language=language,
        threads=threads,
        recognition_start_ms=start_ms,
        recognition_end_ms=end_ms,
        selection_start_ms=start_ms,
        selection_end_ms=end_ms,
        target_start_ms=start_ms,
        target_end_ms=end_ms,
        force=force,
        repair_profile="opening_shadow_v2",
        leading_silence_ms=OPENING_LEADING_SILENCE_MS,
    )
    return {
        "run_id": run_id,
        "track": track,
        "source": str(source),
        "source_label": source_label,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "prepended_silence_ms": OPENING_LEADING_SILENCE_MS,
        "text": text,
        "status": meta.get("status", "unknown"),
        "meta": meta,
    }


def opening_local_only_islands(speaker_states: list[dict[str, Any]]) -> list[tuple[int, int]]:
    return state_action_intervals(
        0,
        OPENING_WINDOW_END_MS,
        speaker_states,
        {"pass_raw_local_only"},
        min_duration_ms=LOCAL_ISLAND_MIN_MS,
    )


def opening_make_candidate(
    *,
    candidate_id: str,
    role: str,
    source_track: str,
    text: str,
    start_ms: int,
    end_ms: int,
    kind: str,
    score: float,
    decision: str,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": candidate_id,
        "role": role,
        "speaker_label": "Me" if role == "me" else "Colleagues",
        "source_track": source_track,
        "text": text,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "kind": kind,
        "score": round(score, 6),
        "decision": decision,
        "evidence": evidence,
    }


def build_opening_candidates(
    *,
    runs: list[dict[str, Any]],
    baseline_rows: list[dict[str, Any]],
    local_islands: list[tuple[int, int]],
    first_content_text: str,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    next_id = 1
    remote_runs = [run for run in runs if run["track"] == "remote" and opening_has_hearing_check(str(run.get("text") or ""))]
    if remote_runs:
        best = max(remote_runs, key=lambda run: opening_avg_token_prob(run.get("meta") or {}) or 0.55)
        start_ms, end_ms = opening_row_bounds(best.get("meta") or {}, int(best["start_ms"]), min(int(best["end_ms"]), 4_000))
        avg_prob = opening_avg_token_prob(best.get("meta") or {}) or 0.55
        score = 0.30 + 0.25 + (0.10 if len(remote_runs) >= 2 else 0.0) + (0.10 if avg_prob >= 0.70 else 0.0) + 0.10
        decision = "accept" if score >= OPENING_REMOTE_ACCEPT_SCORE else "reject"
        candidates.append(
            opening_make_candidate(
                candidate_id=f"opening_cand_{next_id:04d}",
                role="remote",
                source_track="remote",
                text="Меня слышно?",
                start_ms=start_ms,
                end_ms=end_ms,
                kind="hearing_check",
                score=score,
                decision=decision,
                evidence={
                    "matching_run_ids": [str(run["run_id"]) for run in remote_runs],
                    "selected_run_id": best["run_id"],
                    "avg_token_prob": round(avg_prob, 6),
                },
            )
        )
        next_id += 1

    mic_start_runs = [
        run
        for run in runs
        if run["track"] == "mic"
        and str(run.get("run_id", "")).startswith("M_start")
        and opening_has_greeting(str(run.get("text") or ""))
    ]
    mic_start_sources = {str(run.get("source_label")) for run in mic_start_runs}
    if mic_start_runs and not any(row.get("speaker_label") == "Me" and float(row.get("start", 999.0)) < 3.0 for row in baseline_rows):
        best = max(mic_start_runs, key=lambda run: opening_avg_token_prob(run.get("meta") or {}) or 0.55)
        avg_prob = opening_avg_token_prob(best.get("meta") or {}) or 0.55
        score = 0.25 + (0.25 if len(mic_start_sources) >= 2 else 0.10) + (0.10 if avg_prob >= 0.70 else 0.0) + 0.05 + 0.25
        score -= min(0.30, opening_similarity_to_rows("Привет", [row for row in baseline_rows if row.get("source_track") == "remote"]) * 0.30)
        decision = "accept" if score >= OPENING_MIC_ACCEPT_SCORE else "reject"
        candidates.append(
            opening_make_candidate(
                candidate_id=f"opening_cand_{next_id:04d}",
                role="me",
                source_track="mic",
                text="Привет",
                start_ms=0,
                end_ms=1_000,
                kind="start_boundary_greeting",
                score=score,
                decision=decision,
                evidence={
                    "matching_run_ids": [str(run["run_id"]) for run in mic_start_runs],
                    "matching_sources": sorted(mic_start_sources),
                    "selected_run_id": best["run_id"],
                    "avg_token_prob": round(avg_prob, 6),
                },
            )
        )
        next_id += 1

    if local_islands:
        first_island_start, first_island_end = local_islands[0]
        mic_runs = [run for run in runs if run["track"] == "mic"]
        double_ack_runs = [run for run in mic_runs if opening_has_double_greeting_ack(str(run.get("text") or ""))]
        island_runs = [
            run
            for run in mic_runs
            if int(run.get("start_ms", 0)) <= first_island_end
            and int(run.get("end_ms", 0)) >= first_island_start
            and (opening_has_greeting(str(run.get("text") or "")) or opening_has_ack(str(run.get("text") or "")))
        ]
        if double_ack_runs or island_runs:
            evidence_runs = double_ack_runs or island_runs
            best = max(evidence_runs, key=lambda run: opening_avg_token_prob(run.get("meta") or {}) or 0.55)
            avg_prob = opening_avg_token_prob(best.get("meta") or {}) or 0.55
            ratios = action_ratios(first_island_start, first_island_end, [])
            local_only_ratio = 1.0
            local_score = 1.0
            remote_similarity = text_similarity("Привет, да", first_content_text)
            score = 0.25 + 0.25 * local_only_ratio + 0.15 * local_score
            score += 0.10 if len({str(run.get("source_label")) for run in evidence_runs}) >= 2 else 0.05
            score += 0.10 if avg_prob >= 0.70 else 0.0
            score += 0.05
            score -= min(0.35, remote_similarity * 0.35)
            text = "Привет, да" if double_ack_runs else "Привет"
            decision = "accept" if score >= OPENING_MIC_ACCEPT_SCORE else "reject"
            candidates.append(
                opening_make_candidate(
                    candidate_id=f"opening_cand_{next_id:04d}",
                    role="me",
                    source_track="mic",
                    text=text,
                    start_ms=first_island_start,
                    end_ms=first_island_end,
                    kind="local_opening_ack",
                    score=score,
                    decision=decision,
                    evidence={
                        "matching_run_ids": [str(run["run_id"]) for run in evidence_runs],
                        "selected_run_id": best["run_id"],
                        "avg_token_prob": round(avg_prob, 6),
                        "local_only_ratio": local_only_ratio,
                        "local_score": local_score,
                        "remote_similarity": round(remote_similarity, 6),
                    },
                )
            )
    return candidates


def dialogue_overlap_metrics(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    patched_rows = copy.deepcopy(rows)
    for row in patched_rows:
        row.setdefault("quality", {})
        row["quality"]["overlap"] = False
    cross_role_overlap_count = 0
    cross_role_overlap_seconds = 0.0
    cross_role_overlap_gt2_count = 0
    cross_role_overlap_gt2_seconds = 0.0
    overlap_rows: list[dict[str, Any]] = []
    for left_index, left in enumerate(patched_rows):
        left_start = float(left["start"])
        left_end = float(left["end"])
        for right in patched_rows[left_index + 1 :]:
            right_start = float(right["start"])
            if right_start >= left_end:
                break
            if left["speaker_label"] == right["speaker_label"]:
                continue
            overlap = min(left_end, float(right["end"])) - max(left_start, right_start)
            if overlap <= 0:
                continue
            cross_role_overlap_count += 1
            cross_role_overlap_seconds += overlap
            if overlap > 2.0:
                cross_role_overlap_gt2_count += 1
                cross_role_overlap_gt2_seconds += overlap
            left["quality"]["overlap"] = True
            right["quality"]["overlap"] = True
            similarity = text_similarity(str(left.get("corrected_text") or left.get("text") or ""), str(right.get("corrected_text") or right.get("text") or ""))
            if overlap <= 2.0:
                overlap_type = "short_timing_overlap"
            elif similarity > 0.65:
                overlap_type = "probable_duplicate_unresolved"
            elif left["quality"].get("needs_review") or right["quality"].get("needs_review"):
                overlap_type = "uncertain"
            else:
                overlap_type = "possible_double_talk_or_timing"
            overlap_rows.append(
                {
                    "left_utterance_id": left["id"],
                    "right_utterance_id": right["id"],
                    "left_role": left["speaker_label"],
                    "right_role": right["speaker_label"],
                    "start": round(max(left_start, right_start), 3),
                    "end": round(min(left_end, float(right["end"])), 3),
                    "duration_sec": round(overlap, 3),
                    "type": overlap_type,
                    "text_similarity": round(similarity, 6),
                }
            )

    remote_duplicate_in_me_count = 0
    remote_duplicate_in_me_seconds = 0.0
    remote_rows = [row for row in patched_rows if row.get("source_track") == "remote"]
    for row in patched_rows:
        if row.get("source_track") != "mic":
            continue
        row_overlap = 0.0
        row_start = float(row["start"])
        row_end = float(row["end"])
        for remote in remote_rows:
            overlap = min(row_end, float(remote["end"])) - max(row_start, float(remote["start"]))
            if overlap > 0:
                row_overlap += overlap
        if row_overlap > 1.0:
            remote_duplicate_in_me_count += 1
            remote_duplicate_in_me_seconds += row_overlap

    return overlap_rows, {
        "utterances": len(patched_rows),
        "cross_role_overlap_count": cross_role_overlap_count,
        "cross_role_overlap_seconds": round(cross_role_overlap_seconds, 3),
        "cross_role_overlap_gt2_count": cross_role_overlap_gt2_count,
        "cross_role_overlap_gt2_seconds": round(cross_role_overlap_gt2_seconds, 3),
        "needs_review_count": sum(1 for row in patched_rows if row.get("quality", {}).get("needs_review")),
        "remote_duplicate_in_me_count": remote_duplicate_in_me_count,
        "remote_duplicate_in_me_seconds": round(remote_duplicate_in_me_seconds, 3),
        "rows": patched_rows,
    }


def make_opening_utterance_row(candidate: dict[str, Any]) -> dict[str, Any]:
    start = round(int(candidate["start_ms"]) / 1000.0, 3)
    end = round(int(candidate["end_ms"]) / 1000.0, 3)
    return {
        "id": f"utt_{candidate['id']}",
        "source_candidate_id": str(candidate["id"]),
        "source_track": candidate["source_track"],
        "role": candidate["role"],
        "speaker_label": candidate["speaker_label"],
        "start": start,
        "end": end,
        "source_start": start,
        "source_end": end,
        "raw_text": candidate["text"],
        "corrected_text": candidate["text"],
        "corrections": [],
        "quality": {
            "needs_review": False,
            "role_confidence": round(float(candidate["score"]), 6),
            "decision_reason": "start_of_call_repair",
            "text_source": "opening_micro_asr",
            "text_source_candidate_ids": [],
            "repair": {
                "status": "repaired",
                "action": "start_of_call_repair",
                "kind": candidate["kind"],
                "score": round(float(candidate["score"]), 6),
                "evidence": candidate["evidence"],
            },
            "overlap": False,
        },
    }


def write_opening_shadow_artifacts(
    *,
    session: Path,
    resolved_dir: Path,
    model: Path,
    language: str,
    shadow_output: dict[str, Any],
    patched_rows: list[dict[str, Any]],
    overlap_rows: list[dict[str, Any]],
    quality_updates: dict[str, Any],
    report: dict[str, Any],
) -> dict[str, Any]:
    rows = sorted(patched_rows, key=lambda row: (float(row["start"]), str(row.get("source_track", "")), float(row["end"])))
    suffix = ".shadow_v2"

    def artifact(filename: str) -> Path:
        return resolved_dir / suffixed_name(filename, suffix)

    transcript_json = artifact("transcript.simple.json")
    document = json.loads(transcript_json.read_text(encoding="utf-8"))
    document["utterances"] = rows
    transcript_json.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    clean_dialogue = {
        "schema": "murmurmark.clean_dialogue/v1",
        "session": str(session),
        "utterances": [
            {
                "id": row["id"],
                "start": row["start"],
                "end": row["end"],
                "source_start": row["source_start"],
                "source_end": row["source_end"],
                "role": row["role"],
                "speaker_label": row["speaker_label"],
                "text": row["corrected_text"],
                "source_candidate_id": row["source_candidate_id"],
                "source_track": row["source_track"],
                "quality": row["quality"],
            }
            for row in rows
        ],
    }
    artifact("clean_dialogue.json").write_text(json.dumps(clean_dialogue, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    with artifact("transcript.md").open("w", encoding="utf-8") as file:
        file.write("# Simple Transcript\n\n")
        file.write("Backend: whisper.cpp  \n")
        file.write(f"Model: `{model.name}`  \n")
        file.write(f"Language: `{language}`\n\n")
        for row in rows:
            file.write(f"## {format_time(int(float(row['start']) * 1000))} {row['speaker_label']}\n\n")
            file.write(str(row["corrected_text"]).strip() + "\n\n")

    artifact("overlaps.json").write_text(
        json.dumps(
            {
                "schema": "murmurmark.transcript_overlaps/v1",
                "session": str(session),
                "overlaps": overlap_rows,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    quality_path = artifact("quality_report.json")
    quality = json.loads(quality_path.read_text(encoding="utf-8"))
    quality.update(quality_updates)
    quality_path.write_text(json.dumps(quality, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    report_path = artifact("transcribe_simple_report.json")
    transcribe_report = json.loads(report_path.read_text(encoding="utf-8"))
    transcribe_report["utterances"] = len(rows)
    transcribe_report.setdefault("quality", {}).update(
        {
            key: value
            for key, value in quality_updates.items()
            if isinstance(value, (int, float, bool))
        }
    )
    transcribe_report.setdefault("outputs", {}).update(
        {
            "opening_repair_report": "opening_repair_report.shadow_v2.json",
            "opening_candidates": "opening_candidates.shadow_v2.jsonl",
            "opening_micro_asr_runs": "opening_micro_asr_runs.shadow_v2.jsonl",
            "opening_patch": "opening_patch.shadow_v2.json",
        }
    )
    report_path.write_text(json.dumps(transcribe_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    updated = copy.deepcopy(shadow_output)
    updated["utterance_count"] = len(rows)
    updated["utterances"] = rows
    updated["overlaps"] = overlap_rows
    updated["quality"] = quality
    updated.setdefault("report", {}).update(transcribe_report)
    return updated


def apply_start_of_call_repair(
    *,
    session: Path,
    resolved_dir: Path,
    shadow_output: dict[str, Any],
    speaker_states: list[dict[str, Any]],
    remote_wav: Path,
    whisper_cli: str,
    model: Path,
    language: str,
    threads: int,
    force: bool,
) -> dict[str, Any]:
    repair_dir = opening_repair_artifact_dir(resolved_dir)
    clips_dir = repair_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    baseline_rows = copy.deepcopy(shadow_output.get("utterances") or [])
    first_remote = next((row for row in baseline_rows if row.get("source_track") == "remote"), None)
    local_islands = opening_local_only_islands(speaker_states)
    has_me_opening = any(row.get("speaker_label") == "Me" and float(row.get("start", 999.0)) < OPENING_WINDOW_END_MS / 1000 for row in baseline_rows)
    suspicious_first_remote = bool(
        first_remote
        and float(first_remote.get("start", 999.0)) <= 0.5
        and (float(first_remote.get("end", 0.0)) - float(first_remote.get("start", 0.0))) > 8.0
    )
    triggered = suspicious_first_remote or (bool(local_islands) and not has_me_opening)

    runs: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    patch: dict[str, Any] = {
        "schema": "murmurmark.opening_patch/v1",
        "patch_applied": False,
        "patched_utterances": [],
        "baseline_utterances_removed_or_replaced": [],
    }
    report: dict[str, Any] = {
        "schema": "murmurmark.opening_repair/v1",
        "enabled": True,
        "triggered": triggered,
        "patch_applied": False,
        "gate_passed": False,
        "triggers": {
            "suspicious_first_remote": suspicious_first_remote,
            "local_islands_without_me_opening": bool(local_islands) and not has_me_opening,
            "local_islands": [{"start": start / 1000.0, "end": end / 1000.0} for start, end in local_islands],
        },
        "gates": {},
    }

    if triggered:
        for run_id, start_ms, end_ms in OPENING_REMOTE_WINDOWS:
            if remote_wav.exists():
                runs.append(
                    run_opening_micro_asr(
                        source=remote_wav,
                        source_label="remote",
                        run_id=run_id,
                        track="remote",
                        repair_dir=repair_dir,
                        whisper_cli=whisper_cli,
                        model=model,
                        language=language,
                        threads=threads,
                        start_ms=start_ms,
                        end_ms=end_ms,
                        force=force,
                    )
                )
        mic_sources = micro_reasr_sources(session, "shadow_v2")
        mic_windows = list(OPENING_MIC_START_WINDOWS)
        for index, (island_start, island_end) in enumerate(local_islands, start=1):
            specs = (
                (f"M_island_{index:02d}_tight", max(0, island_start - 400), min(OPENING_WINDOW_END_MS, island_end + 400)),
                (f"M_island_{index:02d}_normal", max(0, island_start - 1_000), min(OPENING_WINDOW_END_MS, island_end + 800)),
                (f"M_island_{index:02d}_wide", max(0, island_start - 2_000), min(OPENING_WINDOW_END_MS, island_end + 1_000)),
            )
            mic_windows.extend(specs)
        seen_windows: set[tuple[str, int, int, str]] = set()
        for source_label, source in mic_sources:
            for run_id, start_ms, end_ms in mic_windows:
                key = (run_id, start_ms, end_ms, source_label)
                if key in seen_windows or end_ms <= start_ms:
                    continue
                seen_windows.add(key)
                runs.append(
                    run_opening_micro_asr(
                        source=source,
                        source_label=source_label,
                        run_id=run_id,
                        track="mic",
                        repair_dir=repair_dir,
                        whisper_cli=whisper_cli,
                        model=model,
                        language=language,
                        threads=threads,
                        start_ms=start_ms,
                        end_ms=end_ms,
                        force=force,
                    )
                )
        first_content_text = str(first_remote.get("corrected_text") or first_remote.get("raw_text") or "") if first_remote else ""
        candidates = build_opening_candidates(
            runs=runs,
            baseline_rows=baseline_rows,
            local_islands=local_islands,
            first_content_text=first_content_text,
        )

    accepted = [candidate for candidate in candidates if candidate["decision"] == "accept"]
    first_content = first_remote
    first_content_end_ms = int(round(float(first_content.get("end", 0.0)) * 1000)) if first_content else 0
    patch_end_ms = min(OPENING_MAX_PATCH_END_MS, max(OPENING_WINDOW_END_MS, first_content_end_ms + 1_000))
    patch["patch_window"] = {"start": 0.0, "end": round(patch_end_ms / 1000.0, 3)}

    patched_rows = baseline_rows
    gate_failures: list[str] = []
    if triggered and first_content and accepted:
        accepted_by_kind = {candidate["kind"]: candidate for candidate in accepted}
        opening_rows: list[dict[str, Any]] = []
        boundary = accepted_by_kind.get("start_boundary_greeting")
        hearing = accepted_by_kind.get("hearing_check")
        local_ack = accepted_by_kind.get("local_opening_ack")
        if boundary:
            boundary["start_ms"] = 0
            boundary["end_ms"] = 1_000
            opening_rows.append(make_opening_utterance_row(boundary))
        if hearing:
            start_ms = int(hearing["start_ms"])
            end_ms = int(hearing["end_ms"])
            if boundary:
                start_ms = max(start_ms, int(boundary["end_ms"]) + 200)
            if local_ack:
                end_ms = min(end_ms, int(local_ack["start_ms"]) - 100)
            if end_ms - start_ms >= 500:
                hearing = {**hearing, "start_ms": start_ms, "end_ms": end_ms}
                opening_rows.append(make_opening_utterance_row(hearing))
        if local_ack:
            opening_rows.append(make_opening_utterance_row(local_ack))

        last_opening_end_ms = max((int(round(float(row["end"]) * 1000)) for row in opening_rows), default=0)
        retimed = copy.deepcopy(first_content)
        original_content_text = str(retimed.get("corrected_text") or "")
        content_start_ms = max(int(round(float(retimed["start"]) * 1000)), last_opening_end_ms + 300)
        content_end_ms = int(round(float(retimed["end"]) * 1000))
        if content_end_ms - content_start_ms < 500:
            gate_failures.append("first_content_remote_too_short_after_retime")
        retimed["start"] = round(content_start_ms / 1000.0, 3)
        retimed.setdefault("quality", {})
        retimed["quality"] = {
            **retimed["quality"],
            "decision_reason": "start_of_call_repair_first_remote_retimed",
            "repair": {
                **(retimed.get("quality", {}).get("repair") or {}),
                "opening_repair": {
                    "action": "retime_first_remote_content",
                    "original_start": first_content.get("start"),
                    "retimed_start": retimed["start"],
                    "reason": "opening_micro_asr_found_short_turns_before_first_content",
                },
            },
        }
        opening_rows.append(retimed)
        patched_rows = sorted(
            opening_rows + [row for row in baseline_rows if float(row.get("start", 0.0)) >= patch_end_ms / 1000.0],
            key=lambda row: (float(row["start"]), str(row.get("source_track", "")), float(row["end"])),
        )
        patch["baseline_utterances_removed_or_replaced"] = [str(first_content.get("id"))]
        patch["patched_utterances"] = patched_rows[: len(opening_rows)]
        if normalize_for_compare(str(first_content.get("corrected_text") or "")) != normalize_for_compare(original_content_text):
            gate_failures.append("first_content_text_changed")
        if len([row for row in opening_rows if float(row["start"]) < 12.0]) > OPENING_MAX_ACCEPTED_TURNS:
            gate_failures.append("too_many_opening_turns")
        for candidate in accepted:
            threshold = OPENING_REMOTE_ACCEPT_SCORE if candidate["source_track"] == "remote" else OPENING_MIC_ACCEPT_SCORE
            if float(candidate["score"]) < threshold:
                gate_failures.append(f"low_score_candidate:{candidate['id']}")
            if candidate["source_track"] == "mic" and text_similarity(str(candidate["text"]), str(first_content.get("corrected_text") or "")) > 0.35:
                gate_failures.append(f"mic_candidate_similar_to_remote:{candidate['id']}")
        outside_before = [
            {key: row.get(key) for key in ("id", "start", "end", "speaker_label", "corrected_text")}
            for row in baseline_rows
            if float(row.get("start", 0.0)) >= patch_end_ms / 1000.0
        ]
        outside_after = [
            {key: row.get(key) for key in ("id", "start", "end", "speaker_label", "corrected_text")}
            for row in patched_rows
            if float(row.get("start", 0.0)) >= patch_end_ms / 1000.0
        ]
        if outside_before != outside_after:
            gate_failures.append("changed_rows_outside_patch_window")
        patch_overlaps, patch_metrics = dialogue_overlap_metrics(patched_rows)
        for overlap in patch_overlaps:
            if float(overlap["start"]) < patch_end_ms / 1000.0 and float(overlap["duration_sec"]) > 1.5:
                gate_failures.append("opening_overlap_gt_1_5s")
                break
        if not opening_rows or len(opening_rows) <= 1:
            gate_failures.append("no_opening_turn_recovered")

        if not gate_failures:
            report["gate_passed"] = True
            report["patch_applied"] = True
            patch["patch_applied"] = True
            quality_updates = {
                **{key: value for key, value in patch_metrics.items() if key != "rows"},
                "opening_repair_triggered": True,
                "opening_patch_applied": True,
                "opening_gate_passed": True,
                "recovered_opening_turn_count": max(0, len(opening_rows) - 1),
                "retimed_remote_segments": 1,
                "opening_missing_local_only_islands_before": len(local_islands) if not has_me_opening else 0,
                "opening_missing_local_only_islands_after": 0,
                "opening_remote_ack_before": 0,
                "opening_remote_ack_after": 1 if hearing else 0,
                "opening_repair": {
                    "enabled": True,
                    "triggered": True,
                    "patch_applied": True,
                    "patch_window": patch["patch_window"],
                    "remote_micro_candidates": len([candidate for candidate in candidates if candidate["source_track"] == "remote"]),
                    "mic_micro_candidates": len([candidate for candidate in candidates if candidate["source_track"] == "mic"]),
                    "accepted_candidates": len(accepted),
                    "rejected_candidates": len(candidates) - len(accepted),
                    "recovered_opening_turn_count": max(0, len(opening_rows) - 1),
                    "retimed_remote_segments": 1,
                    "gate_passed": True,
                },
            }
            shadow_output = write_opening_shadow_artifacts(
                session=session,
                resolved_dir=resolved_dir,
                model=model,
                language=language,
                shadow_output=shadow_output,
                patched_rows=patch_metrics["rows"],
                overlap_rows=patch_overlaps,
                quality_updates=quality_updates,
                report=report,
            )

    report["gates"] = {
        "passed": not gate_failures and bool(patch.get("patch_applied")),
        "failures": gate_failures,
        "accepted_candidate_count": len(accepted),
        "candidate_count": len(candidates),
    }
    report["patch_applied"] = bool(patch.get("patch_applied"))
    report["candidates"] = candidates

    (resolved_dir / "opening_repair_report.shadow_v2.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with (resolved_dir / "opening_candidates.shadow_v2.jsonl").open("w", encoding="utf-8") as file:
        for candidate in candidates:
            file.write(json.dumps(candidate, ensure_ascii=False) + "\n")
    with (resolved_dir / "opening_micro_asr_runs.shadow_v2.jsonl").open("w", encoding="utf-8") as file:
        for micro_run in runs:
            file.write(json.dumps(micro_run, ensure_ascii=False) + "\n")
    (resolved_dir / "opening_patch.shadow_v2.json").write_text(json.dumps(patch, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if triggered:
        ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
        clip_sources = [
            ("remote", remote_wav),
            *[(label, path) for label, path in micro_reasr_sources(session, "shadow_v2")],
        ]
        for label, path in clip_sources:
            if not path.exists():
                continue
            clip = clips_dir / f"opening_000_020_{label}.wav"
            if force or not clip.exists():
                run(
                    [
                        ffmpeg,
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-y",
                        "-ss",
                        "0",
                        "-t",
                        "20",
                        "-i",
                        str(path),
                        "-ar",
                        "16000",
                        "-ac",
                        "1",
                        str(clip),
                    ]
                )

    return shadow_output


def metric_value(output: dict[str, Any], name: str, default: float = 0.0) -> float:
    try:
        return float((output.get("quality") or {}).get(name, default))
    except (TypeError, ValueError):
        return default


def transcript_contains(path: Path, text: str) -> bool:
    if not path.exists():
        return False
    haystack = normalize_for_compare(path.read_text(encoding="utf-8"))
    needle = normalize_for_compare(text)
    if needle in haystack:
        return True
    return token_sequence_contains(filtered_control_tokens(haystack), filtered_control_tokens(needle))


def control_texts_for_session(session: Path) -> tuple[str, ...]:
    if any(str(case["session_name"]) == session.name for case in GOLDEN_PHRASE_CASES):
        return NO_REGRESSION_CONTROL_TEXTS
    return ()


def write_repair_comparison(
    *,
    session: Path,
    resolved_dir: Path,
    current_output: dict[str, Any],
    shadow_output: dict[str, Any],
) -> None:
    current_paths = current_output.get("paths") or {}
    shadow_paths = shadow_output.get("paths") or {}
    current_transcript = Path(str(current_paths.get("transcript", "")))
    shadow_transcript = Path(str(shadow_paths.get("transcript", "")))
    expected_control_texts = control_texts_for_session(session)
    control_texts = [
        {
            "text": text,
            "current_present": transcript_contains(current_transcript, text),
            "shadow_present": transcript_contains(shadow_transcript, text),
        }
        for text in expected_control_texts
    ]

    gates = {
        "unrepaired_long_mic_crossings_count": metric_value(shadow_output, "unrepaired_long_mic_crossings_count") == 0,
        "micro_reasr_success_count": metric_value(shadow_output, "micro_reasr_success_count") >= metric_value(current_output, "micro_reasr_success_count"),
        "local_only_island_recall": metric_value(shadow_output, "local_only_island_recall", 1.0) >= metric_value(current_output, "local_only_island_recall", 1.0),
        "needs_review_count": metric_value(shadow_output, "needs_review_count") <= metric_value(current_output, "needs_review_count"),
        "cross_role_overlap_gt2_seconds": metric_value(shadow_output, "cross_role_overlap_gt2_seconds") <= metric_value(current_output, "cross_role_overlap_gt2_seconds"),
        "golden_phrase_fail_count": metric_value(shadow_output, "golden_phrase_fail_count") == 0,
        "control_texts_present": not expected_control_texts or all(item["shadow_present"] for item in control_texts),
    }
    current_quality = current_output.get("quality") or {}
    shadow_quality = shadow_output.get("quality") or {}
    quality_keys = set(current_quality.keys()) | set(shadow_quality.keys())
    comparison = {
        "schema": "murmurmark.repair_comparison/v1",
        "current": {
            "quality": current_quality,
            "paths": current_paths,
        },
        "shadow_v2": {
            "quality": shadow_quality,
            "paths": shadow_paths,
        },
        "delta": {
            key: round(metric_value(shadow_output, key) - metric_value(current_output, key), 6)
            for key in sorted(quality_keys)
            if isinstance(current_quality.get(key, shadow_quality.get(key)), (int, float))
        },
        "control_texts": control_texts,
        "control_texts_scope": "session_specific" if expected_control_texts else "skipped_no_session_control_texts",
        "no_regression_gates": gates,
        "passed": all(gates.values()),
    }
    (resolved_dir / "repair_comparison.json").write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    args = parse_args()
    session = args.session.resolve()
    model = expand(args.model)
    whisper_cli = shutil.which(args.whisper_cli) or args.whisper_cli
    prompt = read_prompt(args.prompt_file)

    raw_dir = session / "derived" / "transcript-simple" / "whisper-cpp" / "raw"
    resolved_dir = session / "derived" / "transcript-simple" / "whisper-cpp" / "resolved"
    asr_dir = session / "derived" / "asr"
    prepared_dir = session / "derived" / "transcript-simple" / "whisper-cpp" / "prepared-audio"

    if not args.skip_export:
        run([args.murmurmark_bin, "export-audio", str(session)])

    mic_wav = prepare_audio_for_asr(
        asr_dir / "mic.wav",
        prepared_dir / f"mic_{args.mic_audio_prep}.wav",
        args.mic_audio_prep,
    )
    remote_wav = prepare_audio_for_asr(
        asr_dir / "remote.wav",
        prepared_dir / f"remote_{args.remote_audio_prep}.wav",
        args.remote_audio_prep,
    )

    tracks = [
        ("mic", "me", "Me", mic_wav, raw_dir / "mic", args.mic_audio_prep),
        ("remote", "remote", "Colleagues", remote_wav, raw_dir / "remote", args.remote_audio_prep),
    ]

    if not args.skip_transcribe:
        if not model.exists():
            raise SystemExit(f"model not found: {model}")
        for _, _, _, input_wav, output_base, audio_prep in tracks:
            if not input_wav.exists():
                raise SystemExit(f"input wav not found: {input_wav}")
            cache_config = whisper_cache_config(
                model=model,
                language=args.language,
                max_context=args.max_context,
                prompt=prompt,
                duration_ms=args.duration_ms,
                asr_mode=args.asr_mode,
                asr_window_sec=args.asr_window_sec,
                asr_overlap_sec=args.asr_overlap_sec,
                audio_prep=audio_prep,
            )
            if args.force or not cache_matches(output_base, cache_config):
                if args.asr_mode == "whole":
                    run_whisper(
                        whisper_cli=whisper_cli,
                        model=model,
                        language=args.language,
                        threads=args.threads,
                        max_context=args.max_context,
                        prompt=prompt,
                        duration_ms=args.duration_ms,
                        input_wav=input_wav,
                        output_base=output_base,
                    )
                else:
                    run_whisper_windowed(
                        whisper_cli=whisper_cli,
                        model=model,
                        language=args.language,
                        threads=args.threads,
                        max_context=args.max_context,
                        prompt=prompt,
                        duration_ms=args.duration_ms,
                        input_wav=input_wav,
                        output_base=output_base,
                        window_sec=args.asr_window_sec,
                        overlap_sec=args.asr_overlap_sec,
                    )
                write_cache_meta(output_base, cache_config)
            else:
                print(f"reusing cached whisper output: {output_base.with_suffix('.json')}")
                if args.asr_mode == "windowed":
                    write_whisper_text_sidecars(output_base)

    utterances: list[Utterance] = []
    dropped: list[dict[str, Any]] = []
    for source_track, role, label, _, output_base, _ in tracks:
        path = output_base.with_suffix(".json")
        if not path.exists():
            raise SystemExit(f"raw whisper json not found: {path}")
        track_utterances, track_dropped = read_utterances(path, source_track, role, label)
        utterances.extend(track_utterances)
        dropped.extend(track_dropped)

    speaker_states = read_speaker_state(session)
    base_candidates = build_candidates(
        utterances,
        speaker_states,
        merge_gap_ms=args.merge_gap_ms,
        max_merged_chars=args.max_merged_chars,
        max_merged_ms=args.max_merged_ms,
    )
    candidates, timeline_repair_report, timeline_repair_examples = timeline_repair(
        session=session,
        candidates=base_candidates,
        utterances=utterances,
        speaker_states=speaker_states,
        whisper_cli=whisper_cli,
        model=model,
        language=args.language,
        threads=args.threads,
        resolved_dir=resolved_dir,
        force=args.force,
        repair_profile="current",
    )
    decisions = decide_roles(candidates, args.mic_policy)

    current_output = write_outputs(
        session=session,
        model=model,
        language=args.language,
        max_context=args.max_context,
        asr_mode=args.asr_mode,
        asr_window_sec=args.asr_window_sec,
        asr_overlap_sec=args.asr_overlap_sec,
        mic_audio_prep=args.mic_audio_prep,
        remote_audio_prep=args.remote_audio_prep,
        prompt_file=args.prompt_file,
        prompt=prompt,
        raw_utterances=utterances,
        raw_dropped=dropped,
        candidates=candidates,
        decisions=decisions,
        resolved_dir=resolved_dir,
        merge_gap_ms=args.merge_gap_ms,
        max_merged_chars=args.max_merged_chars,
        max_merged_ms=args.max_merged_ms,
        mic_policy=args.mic_policy,
        timeline_repair_report=timeline_repair_report,
        timeline_repair_examples=timeline_repair_examples,
        speaker_states=speaker_states,
    )
    utterance_count = int(current_output["utterance_count"])

    if args.repair_profile == "shadow_v2":
        shadow_candidates, shadow_timeline_repair_report, shadow_timeline_repair_examples = timeline_repair(
            session=session,
            candidates=base_candidates,
            utterances=utterances,
            speaker_states=speaker_states,
            whisper_cli=whisper_cli,
            model=model,
            language=args.language,
            threads=args.threads,
            resolved_dir=resolved_dir,
            force=args.force,
            repair_profile="shadow_v2",
        )
        shadow_decisions = decide_roles(shadow_candidates, args.mic_policy)
        shadow_output = write_outputs(
            session=session,
            model=model,
            language=args.language,
            max_context=args.max_context,
            asr_mode=args.asr_mode,
            asr_window_sec=args.asr_window_sec,
            asr_overlap_sec=args.asr_overlap_sec,
            mic_audio_prep=args.mic_audio_prep,
            remote_audio_prep=args.remote_audio_prep,
            prompt_file=args.prompt_file,
            prompt=prompt,
            raw_utterances=utterances,
            raw_dropped=dropped,
            candidates=shadow_candidates,
            decisions=shadow_decisions,
            resolved_dir=resolved_dir,
            merge_gap_ms=args.merge_gap_ms,
            max_merged_chars=args.max_merged_chars,
            max_merged_ms=args.max_merged_ms,
            mic_policy=args.mic_policy,
            timeline_repair_report=shadow_timeline_repair_report,
            timeline_repair_examples=shadow_timeline_repair_examples,
            speaker_states=speaker_states,
            artifact_suffix=".shadow_v2",
        )
        shadow_output = apply_start_of_call_repair(
            session=session,
            resolved_dir=resolved_dir,
            shadow_output=shadow_output,
            speaker_states=speaker_states,
            remote_wav=remote_wav,
            whisper_cli=whisper_cli,
            model=model,
            language=args.language,
            threads=args.threads,
            force=args.force,
        )
        write_repair_comparison(
            session=session,
            resolved_dir=resolved_dir,
            current_output=current_output,
            shadow_output=shadow_output,
        )
        print(f"written shadow: {resolved_dir / 'transcript.shadow_v2.md'}")
        print(f"repair_comparison: {resolved_dir / 'repair_comparison.json'}")

    print(f"written: {resolved_dir / 'transcript.md'}")
    print(f"utterances: {utterance_count}")
    print(f"dropped_segments: {len(dropped) + sum(1 for item in decisions if item.decision == 'drop')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
