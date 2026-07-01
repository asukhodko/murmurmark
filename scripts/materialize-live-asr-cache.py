#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = "murmurmark.live_asr_cache_report/v1"
SCRIPT_VERSION = "0.1.0"
DEFAULT_MODEL = Path.home() / ".local/share/murmurmark/models/whisper.cpp/ggml-large-v3-q5_0.bin"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Materialize live ASR chunks as batch-compatible whisper.cpp raw cache when safe.")
    parser.add_argument("session", type=Path)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--language", default="ru")
    parser.add_argument("--prompt-file", type=Path)
    parser.add_argument("--max-context", type=int, default=0)
    parser.add_argument("--duration-ms", type=int, default=0)
    parser.add_argument("--asr-mode", choices=("windowed", "whole"), default="windowed")
    parser.add_argument("--asr-window-sec", type=int, default=60)
    parser.add_argument("--asr-overlap-sec", type=int, default=5)
    parser.add_argument("--mic-audio-prep", default="speech")
    parser.add_argument("--remote-audio-prep", default="loudnorm")
    parser.add_argument("--force", action="store_true", help="Overwrite existing raw cache when compatibility gates pass.")
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
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


def rel(path: Path, session: Path) -> str:
    try:
        return str(path.relative_to(session))
    except ValueError:
        return str(path)


def read_prompt(path: Path | None) -> str | None:
    if path is None:
        return None
    text = path.expanduser().read_text(encoding="utf-8").strip()
    return text or None


def expand_path(path: Path) -> Path:
    return path.expanduser().resolve()


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


def timestamp_from_ms(ms: int) -> str:
    total_ms = max(0, ms)
    millis = total_ms % 1000
    total_sec = total_ms // 1000
    seconds = total_sec % 60
    minutes = (total_sec // 60) % 60
    hours = total_sec // 3600
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def shift_offsets(value: dict[str, Any], delta_ms: int) -> None:
    offsets = value.get("offsets")
    if isinstance(offsets, dict):
        start = int(offsets.get("from") or 0) + delta_ms
        end = int(offsets.get("to") or start) + delta_ms
        offsets["from"] = start
        offsets["to"] = end
        value["timestamps"] = {"from": timestamp_from_ms(start), "to": timestamp_from_ms(end)}


def shifted_row(row: dict[str, Any], delta_ms: int) -> dict[str, Any]:
    result = copy.deepcopy(row)
    shift_offsets(result, delta_ms)
    for token in result.get("tokens") or []:
        if isinstance(token, dict):
            shift_offsets(token, delta_ms)
    return result


def write_text_sidecars(output_base: Path, rows: list[dict[str, Any]]) -> None:
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
                file.write(f"{timestamp_from_ms(start_ms)} --> {timestamp_from_ms(end_ms)}\n")
                file.write(text + "\n\n")


def source_json_path(session: Path, source_record: dict[str, Any]) -> Path | None:
    asr = source_record.get("asr")
    if not isinstance(asr, dict) or asr.get("status") != "passed":
        return None
    raw_path = asr.get("json")
    if not isinstance(raw_path, str) or not raw_path:
        return None
    path = Path(raw_path)
    if not path.is_absolute():
        path = session / path
    return path if path.exists() else None


def check_geometry(chunks: list[dict[str, Any]], window_sec: int, overlap_sec: int) -> list[str]:
    reasons: list[str] = []
    if not chunks:
        reasons.append("live_chunks_missing")
        return reasons
    sorted_chunks = sorted(chunks, key=lambda row: int(row.get("index") or 0))
    for index, row in enumerate(sorted_chunks):
        chunk_index = int(row.get("index") or index + 1)
        start = float(row.get("start_sec") or 0.0)
        duration = float(row.get("duration_sec") or 0.0)
        expected_start = index * window_sec
        if abs(start - expected_start) > 1.0:
            reasons.append(f"window_start_mismatch:{chunk_index}")
            break
        is_final = index == len(sorted_chunks) - 1
        if not is_final and abs(duration - window_sec) > 1.0:
            reasons.append(f"window_duration_mismatch:{chunk_index}")
            break
        clip_start = float(row.get("clip_start_sec") if row.get("clip_start_sec") is not None else start)
        clip_end = float(row.get("clip_end_sec") if row.get("clip_end_sec") is not None else start + duration)
        if overlap_sec > 0 and index > 0 and clip_start > start - overlap_sec + 1.0:
            reasons.append(f"overlap_before_mismatch:{chunk_index}")
            break
        if overlap_sec > 0 and not is_final and clip_end < start + duration + overlap_sec - 1.0:
            reasons.append(f"overlap_after_mismatch:{chunk_index}")
            break
    return sorted(set(reasons))


def build_combined_json(session: Path, chunks: list[dict[str, Any]], source: str, output_base: Path) -> tuple[int, list[str]]:
    rows: list[dict[str, Any]] = []
    templates: list[dict[str, Any]] = []
    used: list[str] = []
    for chunk in sorted(chunks, key=lambda row: int(row.get("index") or 0)):
        source_record = chunk.get(source)
        if not isinstance(source_record, dict):
            continue
        json_path = source_json_path(session, source_record)
        if json_path is None:
            continue
        data = read_json(json_path)
        if data is None:
            continue
        templates.append(data)
        hard_start_sec = float(source_record.get("hard_start_sec") or chunk.get("start_sec") or 0.0)
        hard_end_sec = float(source_record.get("hard_end_sec") or chunk.get("end_sec") or hard_start_sec)
        clip_start_sec = float(source_record.get("clip_start_sec") or chunk.get("clip_start_sec") or hard_start_sec)
        delta_ms = int(round(clip_start_sec * 1000))
        for row in data.get("transcription") or []:
            if isinstance(row, dict):
                offsets = row.get("offsets") or {}
                local_start = int(offsets.get("from") or 0)
                local_end = int(offsets.get("to") or local_start)
                center_sec = clip_start_sec + ((local_start + local_end) / 2.0) / 1000.0
                if hard_start_sec <= center_sec < hard_end_sec:
                    rows.append(shifted_row(row, delta_ms))
        used.append(rel(json_path, session))
    template = copy.deepcopy(templates[-1]) if templates else {"params": {}, "transcription": []}
    template["transcription"] = sorted(
        rows,
        key=lambda row: (
            int((row.get("offsets") or {}).get("from") or 0),
            int((row.get("offsets") or {}).get("to") or 0),
        ),
    )
    template.setdefault("params", {})
    if isinstance(template["params"], dict):
        template["params"]["murmurmark_asr_mode"] = "windowed"
        template["params"]["murmurmark_source"] = "live_asr_cache"
    output_base.parent.mkdir(parents=True, exist_ok=True)
    write_json(output_base.with_suffix(".json"), template)
    write_text_sidecars(output_base, template["transcription"])
    return len(rows), used


def existing_raw_cache(session: Path) -> bool:
    raw_dir = session / "derived/transcript-simple/whisper-cpp/raw"
    return (raw_dir / "mic.json").exists() or (raw_dir / "remote.json").exists()


def main() -> int:
    args = parse_args()
    session = args.session.expanduser().resolve()
    model = expand_path(args.model)
    report_path = session / "derived/live/live_asr_cache_report.json"
    live_report = read_json(session / "derived/live/live_pipeline_report.json")
    chunks = read_jsonl(session / "derived/live/chunks.jsonl")
    prompt = read_prompt(args.prompt_file)
    reasons: list[str] = []
    if live_report is None:
        reasons.append("live_report_missing")
    elif live_report.get("status") != "completed":
        reasons.append("live_pipeline_not_completed")
    if existing_raw_cache(session) and not args.force:
        reasons.append("raw_cache_already_exists")
    reasons.extend(check_geometry(chunks, args.asr_window_sec, args.asr_overlap_sec))
    expected_prep = {"mic": args.mic_audio_prep, "remote": args.remote_audio_prep}
    for source, prep in expected_prep.items():
        for chunk in chunks:
            index = int(chunk.get("index") or 0)
            source_record = chunk.get(source)
            if not isinstance(source_record, dict):
                reasons.append(f"live_record_missing:{source}:{index}")
                break
            if source_record.get("audio_prep") != prep:
                reasons.append(f"audio_prep_mismatch:{source}:{index}")
                break
            if source_json_path(session, source_record) is None:
                reasons.append(f"asr_json_missing:{source}:{index}")
                break
    reasons = sorted(set(reasons))
    raw_dir = session / "derived/transcript-simple/whisper-cpp/raw"
    outputs: dict[str, Any] = {}
    materialized = False
    if not reasons:
        rows_by_source: dict[str, int] = {}
        used_json_by_source: dict[str, list[str]] = {}
        for source, prep in expected_prep.items():
            output_base = raw_dir / source
            row_count, used = build_combined_json(session, chunks, source, output_base)
            rows_by_source[source] = row_count
            used_json_by_source[source] = used
            meta = whisper_cache_config(
                model=model,
                language=args.language,
                max_context=args.max_context,
                prompt=prompt,
                duration_ms=args.duration_ms,
                asr_mode=args.asr_mode,
                asr_window_sec=args.asr_window_sec,
                asr_overlap_sec=args.asr_overlap_sec,
                audio_prep=prep,
            )
            write_json(output_base.with_suffix(".meta.json"), meta)
            outputs[source] = rel(output_base.with_suffix(".json"), session)
        materialized = True
    status = "materialized" if materialized else "not_eligible"
    payload = {
        "schema": SCHEMA,
        "generator": {"name": "materialize-live-asr-cache", "version": SCRIPT_VERSION},
        "created_at": datetime.now(timezone.utc).isoformat(),
        "session": str(session),
        "status": status,
        "materialized": materialized,
        "reasons": reasons,
        "parameters": {
            "model": str(model),
            "language": args.language,
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest() if prompt else None,
            "max_context": args.max_context,
            "duration_ms": args.duration_ms,
            "asr_mode": args.asr_mode,
            "asr_window_sec": args.asr_window_sec,
            "asr_overlap_sec": args.asr_overlap_sec,
            "mic_audio_prep": args.mic_audio_prep,
            "remote_audio_prep": args.remote_audio_prep,
        },
        "inputs": {
            "live_report": "derived/live/live_pipeline_report.json" if live_report else None,
            "chunks_jsonl": "derived/live/chunks.jsonl" if chunks else None,
        },
        "outputs": outputs,
        "notes": [
            "Raw batch ASR cache is written only when live chunks are batch-compatible.",
            "A not_eligible report is a safe fallback, not a pipeline failure.",
        ],
    }
    if materialized:
        payload["rows_by_source"] = rows_by_source
        payload["used_live_json_by_source"] = used_json_by_source
    write_json(report_path, payload)
    print(f"live_asr_cache_report: {report_path}")
    print(f"status: {status}")
    if reasons:
        print("reasons: " + ", ".join(reasons))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
