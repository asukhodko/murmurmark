#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = "murmurmark.live_pipeline_report/v1"
SCRIPT_VERSION = "0.1.0"
KNOWN_HALLUCINATIONS = {
    "редактор субтитров",
    "продолжение следует",
    "спасибо за просмотр",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Shadow near-realtime worker for closed MurmurMark live segments.")
    parser.add_argument("session", type=Path)
    parser.add_argument("--model", type=Path, default=Path.home() / ".local/share/murmurmark/models/whisper.cpp/ggml-large-v3-q5_0.bin")
    parser.add_argument("--language", default="ru")
    parser.add_argument("--whisper-cli", default=os.environ.get("WHISPER_CLI", "whisper-cli"))
    parser.add_argument("--poll-sec", type=float, default=2.0)
    parser.add_argument("--idle-after-session-json-sec", type=float, default=6.0)
    parser.add_argument("--commit-delay-sec", type=float, default=10.0)
    parser.add_argument("--max-segments", type=int, default=0, help="Debug limit. 0 means no limit.")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
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
    except OSError:
        return []
    return rows


def rel(path: Path, session: Path) -> str:
    try:
        return str(path.relative_to(session))
    except ValueError:
        return str(path)


def fmt_time(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def clean_text(text: str) -> str:
    return " ".join(text.replace("\n", " ").split()).strip()


def is_hallucination(text: str) -> bool:
    normalized = clean_text(text).lower().strip(".!?, ")
    return normalized in KNOWN_HALLUCINATIONS


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)


def audio_filter(source_name: str) -> tuple[str, str]:
    if source_name == "mic":
        return "speech", "highpass=f=100,lowpass=f=7600,alimiter=limit=0.98"
    return "loudnorm", "highpass=f=80,lowpass=f=7800,loudnorm=I=-20:LRA=9:TP=-2,alimiter=limit=0.98"


def convert_to_wav(source: Path, destination: Path, source_name: str) -> tuple[bool, str]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    prep_name, filters = audio_filter(source_name)
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-af",
        filters,
        "-ar",
        "16000",
        "-ac",
        "1",
        str(destination),
    ]
    result = run(command)
    return result.returncode == 0 and destination.exists() and destination.stat().st_size > 44, prep_name


def transcribe(wav: Path, output_base: Path, args: argparse.Namespace) -> dict[str, Any]:
    if not args.model.exists():
        return {
            "status": "skipped",
            "reason": "model_missing",
            "model": str(args.model),
            "text": "",
        }
    if shutil.which(args.whisper_cli) is None:
        return {
            "status": "skipped",
            "reason": "whisper_cli_missing",
            "whisper_cli": args.whisper_cli,
            "text": "",
        }
    output_base.parent.mkdir(parents=True, exist_ok=True)
    command = [
        args.whisper_cli,
        "--model",
        str(args.model),
        "--language",
        args.language,
        "--threads",
        "4",
        "--max-context",
        "0",
        "--output-txt",
        "--output-json",
        "--output-json-full",
        "--output-file",
        str(output_base),
        "--no-prints",
        "--log-score",
        "--suppress-nst",
        "--suppress-regex",
        "^(Редактор субтитров|Продолжение следует|Спасибо за просмотр|Субтитры.*)$",
        "--file",
        str(wav),
    ]
    started = time.monotonic()
    result = run(command)
    elapsed = round(time.monotonic() - started, 3)
    txt_path = output_base.with_suffix(".txt")
    text = clean_text(txt_path.read_text(encoding="utf-8", errors="ignore")) if txt_path.exists() else ""
    status = "passed" if result.returncode == 0 else "failed"
    if is_hallucination(text):
        text = ""
    return {
        "status": status,
        "elapsed_sec": elapsed,
        "text": text,
        "json": str(output_base.with_suffix(".json")) if output_base.with_suffix(".json").exists() else None,
        "stderr_tail": result.stderr[-1000:] if result.returncode != 0 else "",
    }


def text_inside_hard_window(json_path: Path | None, clip_start_sec: float, hard_start_sec: float, hard_end_sec: float) -> str | None:
    if json_path is None or not json_path.exists():
        return None
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    parts: list[str] = []
    for row in data.get("transcription") or []:
        if not isinstance(row, dict):
            continue
        offsets = row.get("offsets") or {}
        local_start = int(offsets.get("from") or 0)
        local_end = int(offsets.get("to") or local_start)
        global_start = clip_start_sec + local_start / 1000.0
        global_end = clip_start_sec + local_end / 1000.0
        center = (global_start + global_end) / 2.0
        if hard_start_sec <= center < hard_end_sec:
            text = clean_text(str(row.get("text") or ""))
            if text and not is_hallucination(text):
                parts.append(text)
    return clean_text(" ".join(parts))


def grouped_segments(rows: list[dict[str, Any]]) -> dict[int, dict[str, dict[str, Any]]]:
    grouped: dict[int, dict[str, dict[str, Any]]] = {}
    for row in rows:
        if row.get("closed") is not True:
            continue
        source = str(row.get("source") or "")
        if source not in {"mic", "remote"}:
            continue
        try:
            index = int(row.get("index"))
        except (TypeError, ValueError):
            continue
        grouped.setdefault(index, {})[source] = row
    return grouped


def write_draft(session: Path, chunks: list[dict[str, Any]], commit_delay_sec: float) -> None:
    draft = session / "derived/live/transcript.draft.md"
    lines = [
        "# Live Draft Transcript",
        "",
        "Shadow near-realtime transcript. The batch pipeline remains authoritative.",
        "",
    ]
    if not chunks:
        lines += ["_Waiting for closed audio segments._", ""]
    max_end = max(float(chunk.get("end_sec") or 0.0) for chunk in chunks) if chunks else 0.0
    for chunk in sorted(chunks, key=lambda item: int(item.get("index") or 0)):
        provisional = max_end - float(chunk.get("end_sec") or 0.0) < commit_delay_sec
        marker = " provisional" if provisional else ""
        lines.append(f"## {fmt_time(float(chunk.get('start_sec') or 0.0))}{marker}")
        lines.append("")
        mic = clean_text(str((chunk.get("mic") or {}).get("text") or ""))
        remote = clean_text(str((chunk.get("remote") or {}).get("text") or ""))
        if mic:
            lines += ["**Me draft**", "", mic, ""]
        if remote:
            lines += ["**Colleagues draft**", "", remote, ""]
        if not mic and not remote:
            lines += ["_No speech decoded in this segment._", ""]
    draft.parent.mkdir(parents=True, exist_ok=True)
    draft.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def process_segment(session: Path, index: int, pair: dict[str, dict[str, Any]], args: argparse.Namespace) -> dict[str, Any]:
    chunk_dir = session / "derived/live/chunks" / f"{index:06d}"
    start_sec = min(float(pair[source].get("start_sec") or 0.0) for source in pair)
    end_sec = max(float(pair[source].get("end_sec") or 0.0) for source in pair)
    record: dict[str, Any] = {
        "schema": "murmurmark.live_chunk/v1",
        "index": index,
        "start_sec": round(start_sec, 3),
        "end_sec": round(end_sec, 3),
        "duration_sec": round(end_sec - start_sec, 3),
        "clip_start_sec": round(min(float(pair[source].get("clip_start_sec") or pair[source].get("start_sec") or 0.0) for source in pair), 3),
        "clip_end_sec": round(max(float(pair[source].get("clip_end_sec") or pair[source].get("end_sec") or 0.0) for source in pair), 3),
        "created_at": utc_now(),
        "mic": {},
        "remote": {},
    }
    for source in ("mic", "remote"):
        source_path = session / str(pair[source].get("path"))
        wav_path = chunk_dir / f"{source}.wav"
        hard_start_sec = float(pair[source].get("start_sec") or 0.0)
        hard_end_sec = float(pair[source].get("end_sec") or hard_start_sec)
        clip_start_sec = float(pair[source].get("clip_start_sec") or hard_start_sec)
        clip_end_sec = float(pair[source].get("clip_end_sec") or hard_end_sec)
        ok, prep_name = convert_to_wav(source_path, wav_path, source)
        source_record: dict[str, Any] = {
            "input": rel(source_path, session),
            "wav": rel(wav_path, session) if ok else None,
            "audio_prep": prep_name,
            "hard_start_sec": round(hard_start_sec, 3),
            "hard_end_sec": round(hard_end_sec, 3),
            "clip_start_sec": round(clip_start_sec, 3),
            "clip_end_sec": round(clip_end_sec, 3),
            "preprocess_status": "passed" if ok else "failed",
        }
        if ok:
            asr = transcribe(wav_path, chunk_dir / source, args)
            source_record["asr"] = asr
            asr_json = Path(str(asr.get("json"))) if asr.get("json") else None
            source_record["text"] = text_inside_hard_window(
                asr_json,
                clip_start_sec=clip_start_sec,
                hard_start_sec=hard_start_sec,
                hard_end_sec=hard_end_sec,
            ) or asr.get("text", "")
        record[source] = source_record
    write_json(chunk_dir / "chunk.json", record)
    append_jsonl(session / "derived/live/chunks.jsonl", record)
    return record


def write_report(session: Path, status: str, chunks: list[dict[str, Any]], rows: list[dict[str, Any]], args: argparse.Namespace) -> None:
    captured = max((float(row.get("end_sec") or 0.0) for row in rows), default=0.0)
    processed = max((float(row.get("end_sec") or 0.0) for row in chunks), default=0.0)
    report = {
        "schema": SCHEMA,
        "generator": {"name": "live-pipeline-shadow", "version": SCRIPT_VERSION},
        "status": status,
        "updated_at": utc_now(),
        "session": str(session),
        "mode": "near_realtime_shadow",
        "batch_authoritative": True,
        "promotion_allowed": False,
        "current_worker": "live-pipeline-shadow",
        "current_stage": status,
        "parameters": {
            "commit_delay_sec": args.commit_delay_sec,
            "language": args.language,
            "model": str(args.model),
        },
        "progress": {
            "captured_sec": round(captured, 3),
            "preprocessed_sec": round(processed, 3),
            "asr_sec": round(processed, 3),
            "processed_sec": round(processed, 3),
            "draft_sec": round(processed, 3),
            "live_lag_sec": round(max(0.0, captured - processed), 3),
            "chunks_processed": len(chunks),
            "segments_seen": len(rows),
        },
        "outputs": {
            "draft_transcript": "derived/live/transcript.draft.md",
            "chunks_jsonl": "derived/live/chunks.jsonl",
            "segments_jsonl": "derived/live/segments.jsonl",
        },
        "recommended_next": "murmurmark process " + str(session),
    }
    write_json(session / "derived/live/live_pipeline_report.json", report)
    write_json(session / "derived/live/live_pipeline_state.json", {
        "schema": "murmurmark.live_pipeline_state/v1",
        "status": status,
        "updated_at": utc_now(),
        "draft_transcript": "derived/live/transcript.draft.md",
        "report": "derived/live/live_pipeline_report.json",
        "live_lag_sec": report["progress"]["live_lag_sec"],
    })


def main() -> int:
    args = parse_args()
    session = args.session.expanduser().resolve()
    segments_path = session / "derived/live/segments.jsonl"
    processed: set[int] = set()
    chunks: list[dict[str, Any]] = []
    last_new_work = time.monotonic()
    write_draft(session, chunks, args.commit_delay_sec)
    write_report(session, "running", chunks, [], args)

    while True:
        rows = read_jsonl(segments_path)
        grouped = grouped_segments(rows)
        ready_indexes = sorted(index for index, pair in grouped.items() if {"mic", "remote"} <= set(pair))
        for index in ready_indexes:
            if index in processed:
                continue
            if args.max_segments and len(processed) >= args.max_segments:
                break
            chunk = process_segment(session, index, grouped[index], args)
            processed.add(index)
            chunks.append(chunk)
            write_draft(session, chunks, args.commit_delay_sec)
            write_report(session, "running", chunks, rows, args)
            last_new_work = time.monotonic()

        session_finished = (session / "session.json").exists()
        all_ready_done = all(index in processed for index in ready_indexes)
        idle_after_finish = session_finished and all_ready_done and (time.monotonic() - last_new_work >= args.idle_after_session_json_sec)
        if idle_after_finish or (args.max_segments and len(processed) >= args.max_segments):
            status = "completed" if session_finished else "stopped_by_limit"
            write_draft(session, chunks, args.commit_delay_sec)
            write_report(session, status, chunks, rows, args)
            return 0

        write_report(session, "running", chunks, rows, args)
        time.sleep(max(0.2, args.poll_sec))


if __name__ == "__main__":
    raise SystemExit(main())
