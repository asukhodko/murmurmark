#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = "murmurmark.live_asr_cache_report/v1"
SCRIPT_VERSION = "0.3.0"
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
    parser.add_argument("--whisper-cli", default="whisper-cli")
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


def raw_meta_path(output_base: Path) -> Path:
    return output_base.with_suffix(".meta.json")


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


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def content_sha256(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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


def chunk_record_from_live(session: Path, chunk: dict[str, Any], source: str, chunk_dir: Path) -> dict[str, Any] | None:
    source_record = chunk.get(source)
    if not isinstance(source_record, dict):
        return None
    json_path = source_json_path(session, source_record)
    if json_path is None:
        return None
    index = int(chunk.get("index") or 0)
    hard_start_sec = float(source_record.get("hard_start_sec") or chunk.get("start_sec") or 0.0)
    hard_end_sec = float(source_record.get("hard_end_sec") or chunk.get("end_sec") or hard_start_sec)
    clip_start_sec = float(source_record.get("clip_start_sec") or chunk.get("clip_start_sec") or hard_start_sec)
    clip_end_sec = float(source_record.get("clip_end_sec") or chunk.get("clip_end_sec") or hard_end_sec)
    hard_start_ms = int(round(hard_start_sec * 1000))
    hard_end_ms = int(round(hard_end_sec * 1000))
    seek_ms = int(round(clip_start_sec * 1000))
    clip_end_ms = int(round(clip_end_sec * 1000))
    chunk_base = chunk_dir / f"{index:04d}_{hard_start_ms // 1000:06d}s"
    return {
        "index": index,
        "status": "reused",
        "source": "live_asr_cache",
        "hard_start_ms": hard_start_ms,
        "hard_end_ms": hard_end_ms,
        "seek_ms": seek_ms,
        "clip_duration_ms": max(1, clip_end_ms - seek_ms),
        "wav": str(Path(str(source_record.get("wav") or ""))),
        "json": str(chunk_base.with_suffix(".json")),
        "meta": str(raw_meta_path(chunk_base)),
        "live_json": rel(json_path, session),
        "live_chunk_index": index,
    }


def materialize_chunk_cache(
    *,
    session: Path,
    chunks: list[dict[str, Any]],
    source: str,
    raw_dir: Path,
    raw_cache_config: dict[str, Any],
    source_duration_ms: int,
    total_ms: int,
    window_sec: int,
    overlap_sec: int,
) -> tuple[list[dict[str, Any]], str]:
    chunk_dir = raw_dir / "chunks" / source
    chunk_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    for chunk in sorted(chunks, key=lambda row: int(row.get("index") or 0)):
        record = chunk_record_from_live(session, chunk, source, chunk_dir)
        if record is None:
            continue
        source_record = chunk.get(source)
        assert isinstance(source_record, dict)
        json_path = source_json_path(session, source_record)
        assert json_path is not None
        data = read_json(json_path)
        if data is None:
            continue
        chunk_base = Path(record["json"]).with_suffix("")
        write_json(chunk_base.with_suffix(".json"), data)
        write_text_sidecars(chunk_base, data.get("transcription") if isinstance(data.get("transcription"), list) else [])
        chunk_meta = {
            "schema": "murmurmark.whisper_cpp_chunk_cache/v1",
            "generator": {"name": "materialize-live-asr-cache", "version": SCRIPT_VERSION},
            "raw_cache": raw_cache_config,
            "source_audio": {
                "kind": "live_asr_cache",
                "live_json": rel(json_path, session),
                "live_wav": rel(Path(str(source_record.get("wav") or "")), session)
                if source_record.get("wav")
                else None,
            },
            "window": {
                "index": record["index"],
                "hard_start_ms": record["hard_start_ms"],
                "hard_end_ms": record["hard_end_ms"],
                "seek_ms": record["seek_ms"],
                "clip_end_ms": record["seek_ms"] + record["clip_duration_ms"],
                "clip_duration_ms": record["clip_duration_ms"],
            },
        }
        write_json(raw_meta_path(chunk_base), chunk_meta)
        records.append(record)
    completed_hard_ms = sum(
        max(0, int(item.get("hard_end_ms") or 0) - int(item.get("hard_start_ms") or 0))
        for item in records
    )
    remaining_ms = max(0, total_ms - completed_hard_ms)
    report = {
        "schema": "murmurmark.whisper_cpp_chunk_cache_report/v1",
        "generator": {"name": "materialize-live-asr-cache", "version": SCRIPT_VERSION},
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "status": "completed" if records else "empty",
        "track": source,
        "output_json": str(raw_dir / f"{source}.json"),
        "source_audio": {"kind": "live_asr_cache", "live_chunks": "derived/live/chunks.jsonl"},
        "source_duration_ms": source_duration_ms,
        "limited_duration_ms": total_ms,
        "window_sec": window_sec,
        "overlap_sec": overlap_sec,
        "chunks_total": len(records),
        "chunks_completed": len(records),
        "chunks_missing": 0,
        "chunks_reused": len(records),
        "chunks_transcribed": 0,
        "completed_hard_ms": completed_hard_ms,
        "completed_hard_sec": round(completed_hard_ms / 1000.0, 3),
        "total_sec": round(total_ms / 1000.0, 3),
        "remaining_sec": round(remaining_ms / 1000.0, 3),
        "reused_sec": round(completed_hard_ms / 1000.0, 3),
        "transcribed_sec": 0.0,
        "completed_ratio": round(completed_hard_ms / total_ms, 6) if total_ms > 0 else None,
        "chunks": records,
        "notes": [
            "These chunks were materialized from live ASR output.",
            "They are accepted only when check-asr-chunk-cache proves raw JSON rebuild parity.",
        ],
    }
    report_path = chunk_dir / "chunk_cache_report.json"
    write_json(report_path, report)
    return records, rel(report_path, session)


def compatibility_settings(
    *,
    source: str,
    model: Path,
    language: str,
    max_context: int,
    prompt: str | None,
    asr_mode: str,
    asr_window_sec: int,
    asr_overlap_sec: int,
    audio_prep: str,
) -> dict[str, Any]:
    return {
        "schema": "murmurmark.live_batch_asr_compatibility_settings/v1",
        "track": source,
        "model": str(model),
        "language": language,
        "max_context": max_context,
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest() if prompt else None,
        "asr_mode": asr_mode,
        "asr_window_sec": asr_window_sec,
        "asr_overlap_sec": asr_overlap_sec,
        "audio_prep": audio_prep,
        "output_json_full": True,
        "log_score": True,
        "suppress_nst": True,
        "suppress_regex": r"^(Редактор субтитров|Продолжение следует|Спасибо за просмотр|Субтитры.*)$",
        "timestamp_reconciliation": "hard_window_center_v1",
    }


def source_compatibility_reasons(
    *,
    session: Path,
    chunks: list[dict[str, Any]],
    source: str,
    prep: str,
    global_reasons: list[str],
    model: Path,
    whisper_cli: Path | None,
    language: str,
    max_context: int,
    prompt: str | None,
    asr_mode: str,
    asr_window_sec: int,
    asr_overlap_sec: int,
    force: bool,
) -> tuple[list[str], dict[str, Any]]:
    reasons = list(global_reasons)
    evidence: dict[str, Any] = {
        "source_audio_identity": "unproven",
        "model_identity": "unproven",
        "whisper_cli_identity": "unproven",
        "settings_identity": "unproven",
    }
    if not model.exists():
        reasons.append("model_file_missing")
    if whisper_cli is None or not whisper_cli.exists():
        reasons.append("whisper_cli_missing")
    if existing_raw_cache(session, source) and not force:
        reasons.append("raw_cache_already_exists")
    expected_settings = compatibility_settings(
        source=source,
        model=model,
        language=language,
        max_context=max_context,
        prompt=prompt,
        asr_mode=asr_mode,
        asr_window_sec=asr_window_sec,
        asr_overlap_sec=asr_overlap_sec,
        audio_prep=prep,
    )
    expected_settings_sha = content_sha256(expected_settings)
    model_sha: str | None = None
    whisper_sha: str | None = None
    for chunk in chunks:
        index = int(chunk.get("index") or 0)
        source_record = chunk.get(source)
        if not isinstance(source_record, dict):
            reasons.append(f"live_record_missing:{index}")
            break
        if source_record.get("audio_prep") != prep:
            reasons.append(f"audio_prep_mismatch:{index}")
            break
        json_path = source_json_path(session, source_record)
        if json_path is None:
            reasons.append(f"asr_json_missing:{index}")
            break
        asr_payload = read_json(json_path)
        if asr_payload is None:
            reasons.append(f"asr_json_invalid:{index}")
            break
        params = asr_payload.get("params") if isinstance(asr_payload.get("params"), dict) else {}
        if str(params.get("model") or "") != str(model):
            reasons.append(f"model_path_mismatch:{index}")
        if str(params.get("language") or "") != language:
            reasons.append(f"language_mismatch:{index}")

        proof = source_record.get("batch_cache_compatibility")
        if not isinstance(proof, dict) or proof.get("schema") != "murmurmark.live_batch_asr_compatibility/v1":
            reasons.extend(
                [
                    "source_audio_identity_unproven",
                    "model_hash_unproven",
                    "whisper_cli_hash_unproven",
                    "asr_settings_identity_unproven",
                ]
            )
            break
        if model_sha is None:
            model_sha = sha256_file(model) if model.exists() else None
        if whisper_sha is None:
            whisper_sha = sha256_file(whisper_cli) if whisper_cli is not None and whisper_cli.exists() else None
        if proof.get("model_sha256") != model_sha:
            reasons.append(f"model_hash_mismatch:{index}")
        else:
            evidence["model_identity"] = "sha256_match"
        if proof.get("whisper_cli_sha256") != whisper_sha:
            reasons.append(f"whisper_cli_hash_mismatch:{index}")
        else:
            evidence["whisper_cli_identity"] = "sha256_match"
        if proof.get("settings_sha256") != expected_settings_sha:
            reasons.append(f"asr_settings_identity_mismatch:{index}")
        else:
            evidence["settings_identity"] = "sha256_match"
        prepared_sha = proof.get("prepared_audio_sha256")
        prepared_raw = source_record.get("asr_wav") or source_record.get("wav")
        prepared_path = Path(str(prepared_raw)) if prepared_raw else None
        if prepared_path is not None and not prepared_path.is_absolute():
            prepared_path = session / prepared_path
        if proof.get("source_kind") != "exact_batch_prepared_window":
            reasons.append(f"source_audio_contract_mismatch:{index}")
        elif not isinstance(prepared_sha, str) or not prepared_sha:
            reasons.append(f"source_audio_identity_unproven:{index}")
        elif prepared_path is None or not prepared_path.exists():
            reasons.append(f"prepared_audio_missing:{index}")
        elif sha256_file(prepared_path) != prepared_sha:
            reasons.append(f"prepared_audio_hash_mismatch:{index}")
        else:
            evidence["source_audio_identity"] = "exact_batch_prepared_window_sha256_match"
    return sorted(set(reasons)), {
        **evidence,
        "settings_sha256": expected_settings_sha,
        "settings": expected_settings,
    }


def existing_raw_cache(session: Path, source: str | None = None) -> bool:
    raw_dir = session / "derived/transcript-simple/whisper-cpp/raw"
    if source is not None:
        return (raw_dir / f"{source}.json").exists()
    return (raw_dir / "mic.json").exists() or (raw_dir / "remote.json").exists()


def live_duration_ms(chunks: list[dict[str, Any]], source: str) -> tuple[int, int]:
    max_hard_end = 0
    max_clip_end = 0
    for chunk in chunks:
        source_record = chunk.get(source)
        if not isinstance(source_record, dict):
            continue
        hard_end_sec = float(source_record.get("hard_end_sec") or chunk.get("end_sec") or 0.0)
        clip_end_sec = float(source_record.get("clip_end_sec") or chunk.get("clip_end_sec") or hard_end_sec)
        max_hard_end = max(max_hard_end, int(round(hard_end_sec * 1000)))
        max_clip_end = max(max_clip_end, int(round(clip_end_sec * 1000)))
    return max_hard_end, max(max_clip_end, max_hard_end)


def main() -> int:
    args = parse_args()
    session = args.session.expanduser().resolve()
    model = expand_path(args.model)
    report_path = session / "derived/live/live_asr_cache_report.json"
    live_report = read_json(session / "derived/live/live_pipeline_report.json")
    chunks = read_jsonl(session / "derived/live/chunks.jsonl")
    prompt = read_prompt(args.prompt_file)
    global_reasons: list[str] = []
    if live_report is None:
        global_reasons.append("live_report_missing")
    elif live_report.get("status") != "completed":
        global_reasons.append("live_pipeline_not_completed")
    global_reasons.extend(check_geometry(chunks, args.asr_window_sec, args.asr_overlap_sec))
    global_reasons = sorted(set(global_reasons))
    whisper_raw = shutil.which(args.whisper_cli) or args.whisper_cli
    whisper_cli = Path(whisper_raw).expanduser().resolve() if whisper_raw else None
    expected_prep = {"mic": args.mic_audio_prep, "remote": args.remote_audio_prep}
    compatibility: dict[str, dict[str, Any]] = {}
    for source, prep in expected_prep.items():
        source_reasons, evidence = source_compatibility_reasons(
            session=session,
            chunks=chunks,
            source=source,
            prep=prep,
            global_reasons=global_reasons,
            model=model,
            whisper_cli=whisper_cli,
            language=args.language,
            max_context=args.max_context,
            prompt=prompt,
            asr_mode=args.asr_mode,
            asr_window_sec=args.asr_window_sec,
            asr_overlap_sec=args.asr_overlap_sec,
            force=args.force,
        )
        compatibility[source] = {
            "eligible": not source_reasons,
            "decision": "reuse" if not source_reasons else "batch_fallback",
            "reasons": source_reasons,
            "evidence": evidence,
        }
    raw_dir = session / "derived/transcript-simple/whisper-cpp/raw"
    outputs: dict[str, Any] = {}
    rows_by_source: dict[str, int] = {}
    used_json_by_source: dict[str, list[str]] = {}
    chunk_reports_by_source: dict[str, str] = {}
    chunk_records_by_source: dict[str, dict[str, Any]] = {}
    materialized_tracks: list[str] = []
    for source, prep in expected_prep.items():
        if not compatibility[source]["eligible"]:
            continue
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
        hard_duration_ms, source_duration_ms = live_duration_ms(chunks, source)
        limited_duration_ms = min(hard_duration_ms, args.duration_ms) if args.duration_ms > 0 else hard_duration_ms
        chunk_records, chunk_report = materialize_chunk_cache(
            session=session,
            chunks=chunks,
            source=source,
            raw_dir=raw_dir,
            raw_cache_config=meta,
            source_duration_ms=source_duration_ms,
            total_ms=limited_duration_ms,
            window_sec=args.asr_window_sec,
            overlap_sec=args.asr_overlap_sec,
        )
        chunk_reports_by_source[source] = chunk_report
        chunk_records_by_source[source] = {
            "chunks_total": len(chunk_records),
            "chunks_completed": len(chunk_records),
        }
        outputs[source] = rel(output_base.with_suffix(".json"), session)
        outputs[f"{source}_chunk_report"] = chunk_report
        materialized_tracks.append(source)

    fallback_tracks = [source for source in expected_prep if source not in materialized_tracks]
    materialized = bool(materialized_tracks)
    if len(materialized_tracks) == len(expected_prep):
        status = "materialized"
    elif materialized_tracks:
        status = "partially_materialized"
    else:
        status = "not_eligible"
    reasons = sorted(
        {
            f"{source}:{reason}"
            for source, decision in compatibility.items()
            for reason in decision["reasons"]
        }
    )
    payload = {
        "schema": SCHEMA,
        "generator": {"name": "materialize-live-asr-cache", "version": SCRIPT_VERSION},
        "created_at": datetime.now(timezone.utc).isoformat(),
        "session": str(session),
        "status": status,
        "materialized": materialized,
        "materialized_tracks": materialized_tracks,
        "fallback_tracks": fallback_tracks,
        "reasons": reasons,
        "track_compatibility": compatibility,
        "parameters": {
            "model": str(model),
            "model_sha256": sha256_file(model) if model.exists() else None,
            "whisper_cli": str(whisper_cli) if whisper_cli is not None else None,
            "whisper_cli_sha256": sha256_file(whisper_cli) if whisper_cli is not None and whisper_cli.exists() else None,
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
    if materialized_tracks:
        payload["rows_by_source"] = rows_by_source
        payload["used_live_json_by_source"] = used_json_by_source
        payload["chunk_reports_by_source"] = chunk_reports_by_source
        payload["chunk_records_by_source"] = chunk_records_by_source
    write_json(report_path, payload)
    print(f"live_asr_cache_report: {report_path}")
    print(f"status: {status}")
    if reasons:
        print("reasons: " + ", ".join(reasons))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
