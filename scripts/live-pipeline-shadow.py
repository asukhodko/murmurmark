#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from scipy import linalg, signal
from scipy.io import wavfile


SCHEMA = "murmurmark.live_pipeline_report/v1"
SCRIPT_VERSION = "0.2.0"
EPSILON = 1.0e-12
KNOWN_HALLUCINATIONS = {
    "редактор субтитров",
    "продолжение следует",
    "спасибо за просмотр",
}
TOKEN_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9_+-]+")


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


def rewrite_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


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


def tokens(text: str) -> list[str]:
    return [match.group(0).lower() for match in TOKEN_RE.finditer(text)]


def bag_recall(source_tokens: list[str], target_tokens: list[str]) -> float | None:
    if not source_tokens:
        return None
    target = {}
    for token in target_tokens:
        target[token] = target.get(token, 0) + 1
    matched = 0
    for token in source_tokens:
        if target.get(token, 0) > 0:
            matched += 1
            target[token] -= 1
    return matched / max(1, len(source_tokens))


def is_hallucination(text: str) -> bool:
    normalized = clean_text(text).lower().strip(".!?, ")
    return normalized in KNOWN_HALLUCINATIONS or normalized.startswith("субтитры")


def apply_live_role_gate(record: dict[str, Any]) -> None:
    mic = record.get("mic")
    remote = record.get("remote")
    if not isinstance(mic, dict) or not isinstance(remote, dict):
        return
    mic_text = clean_text(str(mic.get("text") or ""))
    remote_text = clean_text(str(remote.get("text") or ""))
    mic_tokens = tokens(mic_text)
    remote_tokens = tokens(remote_text)
    if len(mic_tokens) < 3 or len(remote_tokens) < 3:
        mic["live_role_gate"] = {"status": "passed", "reason": "too_short_for_duplicate_gate"}
        return
    mic_in_remote = bag_recall(mic_tokens, remote_tokens) or 0.0
    remote_in_mic = bag_recall(remote_tokens, mic_tokens) or 0.0
    duplicate_score = max(mic_in_remote, remote_in_mic)
    if duplicate_score >= 0.62:
        mic["raw_text_before_role_gate"] = mic_text
        mic["text"] = ""
        mic["live_role_gate"] = {
            "status": "suppressed",
            "reason": "mic_text_duplicates_remote_text",
            "duplicate_score": round(duplicate_score, 6),
            "mic_token_recall_in_remote": round(mic_in_remote, 6),
            "remote_token_recall_in_mic": round(remote_in_mic, 6),
        }
    else:
        mic["live_role_gate"] = {
            "status": "passed",
            "reason": "mic_text_not_duplicate_remote_text",
            "duplicate_score": round(duplicate_score, 6),
            "mic_token_recall_in_remote": round(mic_in_remote, 6),
            "remote_token_recall_in_mic": round(remote_in_mic, 6),
        }


def apply_adjacent_boundary_gate(previous: dict[str, Any] | None, current: dict[str, Any]) -> None:
    if previous is None:
        return
    for source in ("mic", "remote"):
        previous_source = previous.get(source)
        current_source = current.get(source)
        if not isinstance(previous_source, dict) or not isinstance(current_source, dict):
            continue
        previous_text = clean_text(str(previous_source.get("text") or ""))
        current_text = clean_text(str(current_source.get("text") or ""))
        previous_tokens = tokens(previous_text)
        current_tokens = tokens(current_text)
        if len(previous_tokens) < 3 or len(current_tokens) < 2:
            current_source["live_boundary_gate"] = {"status": "passed", "reason": "too_short_for_boundary_gate"}
            continue
        current_in_previous = bag_recall(current_tokens, previous_tokens) or 0.0
        previous_in_current = bag_recall(previous_tokens, current_tokens) or 0.0
        duplicate_score = max(current_in_previous, previous_in_current)
        if current_in_previous >= 0.80 or (duplicate_score >= 0.88 and len(current_tokens) <= len(previous_tokens) + 2):
            current_source["raw_text_before_boundary_gate"] = current_text
            current_source["text"] = ""
            current_source["live_boundary_gate"] = {
                "status": "suppressed",
                "reason": "adjacent_chunk_duplicate",
                "duplicate_score": round(duplicate_score, 6),
                "current_token_recall_in_previous": round(current_in_previous, 6),
                "previous_token_recall_in_current": round(previous_in_current, 6),
            }
        else:
            current_source["live_boundary_gate"] = {
                "status": "passed",
                "reason": "not_adjacent_duplicate",
                "duplicate_score": round(duplicate_score, 6),
                "current_token_recall_in_previous": round(current_in_previous, 6),
                "previous_token_recall_in_current": round(previous_in_current, 6),
            }


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


def read_wav_float(path: Path) -> tuple[int, np.ndarray]:
    sample_rate, data = wavfile.read(path)
    if data.ndim > 1:
        data = data.mean(axis=1)
    if np.issubdtype(data.dtype, np.integer):
        info = np.iinfo(data.dtype)
        scale = float(max(abs(info.min), info.max))
        audio = data.astype(np.float32) / scale
    else:
        audio = data.astype(np.float32)
    return sample_rate, np.nan_to_num(audio)


def write_wav_float(path: Path, sample_rate: int, audio: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    wavfile.write(path, sample_rate, np.clip(audio, -1.0, 1.0).astype(np.float32))


def rms_db(audio: np.ndarray) -> float:
    rms = float(np.sqrt(np.mean(np.square(audio), dtype=np.float64) + EPSILON))
    return 20.0 * np.log10(rms + EPSILON)


def normalized_corr(left: np.ndarray, right: np.ndarray) -> float:
    a = np.asarray(left, dtype=np.float64) - float(np.mean(left))
    b = np.asarray(right, dtype=np.float64) - float(np.mean(right))
    return float(np.dot(a, b) / (np.sqrt(np.dot(a, a) * np.dot(b, b)) + EPSILON))


def shift_reference(remote: np.ndarray, delay_samples: int) -> np.ndarray:
    shifted = np.zeros_like(remote)
    if delay_samples > 0:
        shifted[delay_samples:] = remote[:-delay_samples]
    elif delay_samples < 0:
        lead = -delay_samples
        shifted[:-lead] = remote[lead:]
    else:
        shifted[:] = remote
    return shifted


def estimate_delay_samples(mic: np.ndarray, remote: np.ndarray, sample_rate: int) -> tuple[int, float]:
    min_lag = int(round(-0.25 * sample_rate))
    max_lag = int(round(0.80 * sample_rate))
    count = min(mic.size, remote.size)
    if count < sample_rate // 4:
        return 0, 0.0
    mic_data = mic[:count].astype(np.float64) - float(np.mean(mic[:count]))
    remote_data = remote[:count].astype(np.float64) - float(np.mean(remote[:count]))
    corr = signal.correlate(mic_data, remote_data, mode="full", method="fft")
    lags = signal.correlation_lags(mic_data.size, remote_data.size, mode="full")
    mask = (lags >= min_lag) & (lags <= max_lag)
    if not np.any(mask):
        return 0, 0.0
    limited = np.abs(corr[mask])
    limited_lags = lags[mask]
    best_index = int(np.argmax(limited))
    peak = float(limited[best_index])
    denom = float(np.sqrt(np.dot(mic_data, mic_data) * np.dot(remote_data, remote_data)) + EPSILON)
    return int(limited_lags[best_index]), peak / denom


def fit_local_fir(remote_fit: np.ndarray, mic_fit: np.ndarray, taps: int, regularization: float) -> np.ndarray:
    x = remote_fit.astype(np.float64) - float(np.mean(remote_fit))
    y = mic_fit.astype(np.float64) - float(np.mean(mic_fit))
    corr_xx = signal.correlate(x, x, mode="full", method="fft")
    corr_yx = signal.correlate(y, x, mode="full", method="fft")
    center = x.size - 1
    r_xx = corr_xx[center : center + taps]
    p_yx = corr_yx[center : center + taps]
    if r_xx.size < taps or p_yx.size < taps or float(r_xx[0]) <= EPSILON:
        return np.zeros(taps, dtype=np.float64)
    toeplitz_col = r_xx.copy()
    toeplitz_col[0] += max(float(r_xx[0]) * regularization, EPSILON)
    try:
        fir = linalg.solve_toeplitz((toeplitz_col, toeplitz_col), p_yx, check_finite=False)
    except Exception:
        return np.zeros(taps, dtype=np.float64)
    return np.asarray(fir, dtype=np.float64)


def live_echo_guard(mic_wav: Path, remote_wav: Path, output_wav: Path) -> dict[str, Any]:
    if not mic_wav.exists() or not remote_wav.exists():
        return {"status": "skipped", "reason": "missing_wav"}
    mic_rate, mic = read_wav_float(mic_wav)
    remote_rate, remote = read_wav_float(remote_wav)
    if mic_rate != remote_rate:
        return {"status": "skipped", "reason": "sample_rate_mismatch", "mic_rate": mic_rate, "remote_rate": remote_rate}
    count = min(mic.size, remote.size)
    if count < int(round(mic_rate * 1.0)):
        return {"status": "skipped", "reason": "too_short"}
    mic = mic[:count]
    remote = remote[:count]
    mic_db = rms_db(mic)
    remote_db = rms_db(remote)
    if remote_db < -55.0 or mic_db < -65.0:
        return {
            "status": "skipped",
            "reason": "inactive_audio",
            "mic_db": round(mic_db, 3),
            "remote_db": round(remote_db, 3),
        }
    delay_samples, delay_corr = estimate_delay_samples(mic, remote, mic_rate)
    remote_aligned = shift_reference(remote, delay_samples)
    before_corr = abs(normalized_corr(remote_aligned, mic))
    if before_corr < 0.08 and delay_corr < 0.08:
        return {
            "status": "skipped",
            "reason": "weak_remote_similarity",
            "mic_db": round(mic_db, 3),
            "remote_db": round(remote_db, 3),
            "delay_samples": delay_samples,
            "delay_corr": round(delay_corr, 6),
            "remote_similarity_before": round(before_corr, 6),
        }
    taps = max(1, int(round(mic_rate * 0.080)))
    fir = fit_local_fir(remote_aligned, mic, taps=taps, regularization=1.0e-2)
    echo_hat = signal.lfilter(fir, [1.0], remote_aligned.astype(np.float64))
    strength = 0.85
    clean = mic.astype(np.float64) - strength * echo_hat
    after_corr = abs(normalized_corr(remote_aligned, clean))
    before_power = float(np.mean(mic.astype(np.float64) ** 2) + EPSILON)
    after_power = float(np.mean(clean.astype(np.float64) ** 2) + EPSILON)
    peak = float(np.max(np.abs(clean))) if clean.size else 0.0
    reduction_db = 10.0 * np.log10(before_power / after_power)
    accepted = (
        np.all(np.isfinite(clean))
        and peak <= 1.25
        and after_corr <= max(before_corr * 0.95, 0.10)
        and after_power <= before_power * 1.25
    )
    report = {
        "schema": "murmurmark.live_echo_guard/v1",
        "status": "accepted" if accepted else "rejected",
        "reason": "accepted" if accepted else "quality_gate_rejected",
        "mic_db": round(mic_db, 3),
        "remote_db": round(remote_db, 3),
        "delay_samples": delay_samples,
        "delay_ms": round(delay_samples * 1000.0 / max(mic_rate, 1), 3),
        "delay_corr": round(delay_corr, 6),
        "remote_similarity_before": round(before_corr, 6),
        "remote_similarity_after": round(after_corr, 6),
        "estimated_reduction_db": round(float(reduction_db), 3),
        "strength": strength,
        "taps": taps,
    }
    if accepted:
        write_wav_float(output_wav, mic_rate, clean)
        report["output"] = str(output_wav)
    return report


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
    converted: dict[str, Path] = {}
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
            converted[source] = wav_path
        record[source] = source_record
    if {"mic", "remote"} <= set(converted):
        clean_wav = chunk_dir / "mic.live_echo_guard.wav"
        guard_report = live_echo_guard(converted["mic"], converted["remote"], clean_wav)
        record["mic"]["live_echo_guard"] = {
            **{key: value for key, value in guard_report.items() if key != "output"},
            "output": rel(clean_wav, session) if clean_wav.exists() else None,
        }
        if guard_report.get("status") == "accepted" and clean_wav.exists():
            record["mic"]["asr_wav"] = rel(clean_wav, session)
            converted["mic"] = clean_wav
        else:
            record["mic"]["asr_wav"] = record["mic"].get("wav")
    for source in ("mic", "remote"):
        source_record = record[source]
        wav_for_asr = converted.get(source)
        if wav_for_asr:
            asr = transcribe(wav_for_asr, chunk_dir / source, args)
            source_record["asr"] = asr
            asr_json = Path(str(asr.get("json"))) if asr.get("json") else None
            source_record["text"] = text_inside_hard_window(
                asr_json,
                clip_start_sec=float(source_record.get("clip_start_sec") or 0.0),
                hard_start_sec=float(source_record.get("hard_start_sec") or 0.0),
                hard_end_sec=float(source_record.get("hard_end_sec") or 0.0),
            ) or asr.get("text", "")
    apply_live_role_gate(record)
    write_json(chunk_dir / "chunk.json", record)
    return record


def write_chunks(session: Path, chunks: list[dict[str, Any]]) -> None:
    rewrite_jsonl(session / "derived/live/chunks.jsonl", chunks)
    for chunk in chunks:
        try:
            index = int(chunk.get("index") or 0)
        except (TypeError, ValueError):
            continue
        if index > 0:
            write_json(session / "derived/live/chunks" / f"{index:06d}" / "chunk.json", chunk)


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
            apply_adjacent_boundary_gate(chunks[-1] if chunks else None, chunk)
            chunks.append(chunk)
            write_chunks(session, chunks)
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
