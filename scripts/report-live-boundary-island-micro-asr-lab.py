#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_VERSION = "0.3.0"
SCHEMA = "murmurmark.live_boundary_island_micro_asr_lab/v1"
DEFAULT_MODEL = "~/.local/share/murmurmark/models/whisper.cpp/ggml-large-v3-q5_0.bin"
TOKEN_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё_./+-]+")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run a diagnostic micro-ASR lab for live boundary-island candidates. "
            "The lab writes evidence only and never changes live drafts or batch transcripts."
        )
    )
    parser.add_argument("--report", type=Path, default=Path("sessions/_reports/live-pipeline/live_corpus_gates_report.json"))
    parser.add_argument("--sessions-root", type=Path, default=Path("sessions"))
    parser.add_argument("--out-dir", type=Path, default=Path("sessions/_reports/live-pipeline"))
    parser.add_argument("--model", type=Path, default=Path(DEFAULT_MODEL))
    parser.add_argument("--language", default="ru")
    parser.add_argument("--whisper-cli", default="whisper-cli")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--max-candidates", type=int, default=10)
    parser.add_argument(
        "--candidate-source",
        choices=("design-lab", "live-only", "blocker-analysis"),
        default="design-lab",
        help=(
            "design-lab uses batch-informed design examples; live-only selects candidates "
            "from live comparison rows using only live/audio evidence; blocker-analysis uses "
            "capture-safe local recall blocker rows from the live corpus report."
        ),
    )
    parser.add_argument(
        "--source-scope",
        choices=("live", "batch-reference", "both"),
        default="both",
        help="live uses live chunk wavs; batch-reference uses full-session preprocessed mic as a diagnostic reference.",
    )
    parser.add_argument("--force", action="store_true", help="Rerun whisper-cli even when cached micro-ASR JSON exists.")
    return parser.parse_args()


def output_paths(out_dir: Path, candidate_source: str) -> dict[str, Path]:
    if candidate_source == "live-only":
        stem = "live_boundary_micro_asr_live_candidates_lab"
    elif candidate_source == "blocker-analysis":
        stem = "live_duplicate_heavy_micro_asr_lab"
    else:
        stem = "live_boundary_island_micro_asr_lab"
    return {
        "attempts_jsonl": out_dir / f"{stem}_attempts.jsonl",
        "report_json": out_dir / f"{stem}.json",
        "report_md": out_dir / f"{stem}.md",
    }


def load_transcribe_bridge() -> Any:
    path = Path(__file__).resolve().parent / "transcribe-simple-whispercpp.py"
    spec = importlib.util.spec_from_file_location("murmurmark_transcribe_bridge_for_live_boundary_lab", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import transcribe bridge: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
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
            file.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def clean_text(text: str) -> str:
    return " ".join(str(text or "").strip().split())


def normalize(text: str) -> str:
    return " ".join(token.lower().replace("ё", "е") for token in TOKEN_RE.findall(str(text or "")))


def tokens(text: str) -> list[str]:
    return normalize(text).split()


def bag_recall(source: list[str], target: list[str]) -> float:
    if not source:
        return 0.0
    remaining: dict[str, int] = {}
    for token in target:
        remaining[token] = remaining.get(token, 0) + 1
    matched = 0
    for token in source:
        count = remaining.get(token, 0)
        if count <= 0:
            continue
        matched += 1
        remaining[token] = count - 1
    return matched / len(source)


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def run(command: list[str], log_path: Path) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log:
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=False,
        )
    return completed.returncode


def load_chunks(session: Path) -> list[dict[str, Any]]:
    chunks_path = session / "derived/live/chunks.jsonl"
    rows = read_jsonl(chunks_path)
    chunks: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        chunks.append(row)
    chunks.sort(key=lambda item: safe_float(item.get("start_sec")))
    return chunks


def live_chunk_sources(session: Path, start_sec: float, end_sec: float) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for chunk in load_chunks(session):
        mic = chunk.get("mic") if isinstance(chunk.get("mic"), dict) else {}
        wav_rel = mic.get("wav")
        if not wav_rel:
            continue
        clip_start = safe_float(mic.get("clip_start_sec"), safe_float(chunk.get("clip_start_sec")))
        clip_end = safe_float(mic.get("clip_end_sec"), safe_float(chunk.get("clip_end_sec")))
        if clip_end <= start_sec or clip_start >= end_sec:
            continue
        path = session / str(wav_rel)
        if not path.exists():
            continue
        sources.append(
            {
                "source_scope": "live",
                "source_label": f"live_chunk_{safe_int(chunk.get('index')):06d}_mic",
                "source": path,
                "source_start_sec": clip_start,
                "source_end_sec": clip_end,
                "chunk_index": chunk.get("index"),
                "chunk_start_sec": chunk.get("start_sec"),
                "chunk_end_sec": chunk.get("end_sec"),
                "remote_text": live_remote_text_for_interval(session, chunk, start_sec, end_sec),
            }
        )
    return sources


def live_remote_text_for_interval(session: Path, chunk: dict[str, Any], start_sec: float, end_sec: float) -> str:
    remote = chunk.get("remote") if isinstance(chunk.get("remote"), dict) else {}
    json_path = remote.get("asr", {}).get("json") if isinstance(remote.get("asr"), dict) else None
    if not json_path:
        return clean_text(str(remote.get("text") or ""))
    path = Path(str(json_path))
    if not path.is_absolute():
        path = session / path
    if not path.exists():
        return clean_text(str(remote.get("text") or ""))
    bridge = load_transcribe_bridge()
    clip_start_ms = int(round(safe_float(remote.get("clip_start_sec"), safe_float(chunk.get("clip_start_sec"))) * 1000))
    text, _rows = bridge.read_micro_reasr_text(
        path,
        clip_start_ms,
        int(round(start_sec * 1000)),
        int(round(end_sec * 1000)),
    )
    return text or clean_text(str(remote.get("text") or ""))


def batch_reference_sources(session: Path, remote_text: str) -> list[dict[str, Any]]:
    audio_dir = session / "derived/preprocess/audio"
    preferred = [
        ("batch_clean_local_fir", audio_dir / "mic_clean_local_fir.wav"),
        ("batch_raw_for_asr", audio_dir / "mic_raw_for_asr.wav"),
        ("batch_role_masked_for_asr", audio_dir / "mic_role_masked_for_asr.wav"),
    ]
    rows: list[dict[str, Any]] = []
    for label, path in preferred:
        if path.exists():
            rows.append(
                {
                    "source_scope": "batch_reference",
                    "source_label": label,
                    "source": path,
                    "source_start_sec": 0.0,
                    "source_end_sec": None,
                    "remote_text": remote_text,
                }
            )
    return rows


def select_candidates(report: dict[str, Any], max_candidates: int) -> list[dict[str, Any]]:
    design = report.get("live_online_speaker_boundary_evidence_design_lab")
    design_examples = design.get("examples") if isinstance(design, dict) and isinstance(design.get("examples"), list) else []
    split = report.get("live_local_island_split_lab")
    split_examples = split.get("examples") if isinstance(split, dict) and isinstance(split.get("examples"), list) else []
    split_by_key = {
        (str(row.get("session") or ""), str(row.get("batch_id") or "")): row
        for row in split_examples
        if isinstance(row, dict)
    }
    rows: list[dict[str, Any]] = []
    for item in design_examples:
        if not isinstance(item, dict) or item.get("design_unit") != "boundary_island_micro_asr":
            continue
        key = (str(item.get("session") or ""), str(item.get("batch_id") or ""))
        split_row = split_by_key.get(key)
        islands = split_row.get("local_island_examples") if isinstance(split_row, dict) else None
        if not isinstance(islands, list) or not islands:
            continue
        rows.append({**item, "local_island_examples": islands, "split_row": split_row})
    rows.sort(
        key=lambda row: (
            safe_int(row.get("priority"), 99),
            -safe_float(row.get("duration_sec")),
            str(row.get("session") or ""),
            safe_float(row.get("start")),
        )
    )
    return rows[:max_candidates]


def live_only_candidate_features(row: dict[str, Any]) -> dict[str, Any]:
    text = clean_text(str(row.get("text") or ""))
    start_sec = safe_float(row.get("start"))
    end_sec = safe_float(row.get("end"))
    duration_sec = safe_float(row.get("duration_sec"), end_sec - start_sec)
    if duration_sec <= 0 and end_sec > start_sec:
        duration_sec = end_sec - start_sec
    rescue_policies = row.get("rescue_policy_candidates")
    if not isinstance(rescue_policies, list):
        rescue_policies = []
    return {
        "text": text,
        "token_count": len(tokens(text)),
        "start": round(start_sec, 3),
        "end": round(end_sec, 3),
        "duration_sec": round(duration_sec, 3),
        "known_hallucination": bool(row.get("known_hallucination")),
        "segment_gate_reason": str(row.get("segment_gate_reason") or ""),
        "segment_gate_unique_token_count": safe_int(row.get("segment_gate_unique_token_count")),
        "segment_gate_mic_token_recall_in_overlapping_remote": round(
            safe_float(row.get("segment_gate_mic_token_recall_in_overlapping_remote")),
            6,
        ),
        "segment_gate_overlapping_remote_token_recall_in_mic": round(
            safe_float(row.get("segment_gate_overlapping_remote_token_recall_in_mic")),
            6,
        ),
        "audio_mic_minus_remote_rms_db": round(safe_float(row.get("audio_mic_minus_remote_rms_db")), 3),
        "audio_mic_remote_zero_lag_abs_corr": round(safe_float(row.get("audio_mic_remote_zero_lag_abs_corr")), 6),
        "rescue_policy_candidates": [str(value) for value in rescue_policies],
    }


def is_live_only_candidate(features: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if features["known_hallucination"]:
        reasons.append("known_hallucination")
    if features["segment_gate_reason"] != "segment_has_local_tokens_not_seen_in_overlapping_remote":
        reasons.append("segment_gate_reason_not_local_unique")
    if features["segment_gate_unique_token_count"] < 4:
        reasons.append("too_few_unique_tokens")
    if features["token_count"] < 2:
        reasons.append("too_few_text_tokens")
    if not (2.0 <= features["duration_sec"] <= 8.0):
        reasons.append("duration_outside_live_only_range")
    if features["segment_gate_mic_token_recall_in_overlapping_remote"] > 0.20:
        reasons.append("mic_tokens_overlap_remote_too_much")
    if features["segment_gate_overlapping_remote_token_recall_in_mic"] > 0.20:
        reasons.append("remote_tokens_overlap_mic_too_much")
    if features["audio_mic_remote_zero_lag_abs_corr"] > 0.025:
        reasons.append("mic_remote_corr_too_high")
    if features["audio_mic_minus_remote_rms_db"] < -14.0:
        reasons.append("mic_too_quiet_vs_remote")
    policies = set(features["rescue_policy_candidates"])
    if not ({"audio_low_corr_text_guard_v1", "audio_low_coherence_v1"} & policies):
        reasons.append("missing_low_corr_or_low_coherence_evidence")
    return not reasons, reasons


def select_live_only_candidates(sessions_root: Path, max_candidates: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    comparison_paths = sorted(sessions_root.glob("20??-??-??_*/derived/live/live_batch_comparison.json"))
    for comparison_path in comparison_paths:
        session = comparison_path.parents[2]
        report = read_json(comparison_path)
        risk_examples = report.get("risk_examples") if isinstance(report.get("risk_examples"), dict) else {}
        rows = (
            risk_examples.get("suppressed_mic_asr_segments")
            if isinstance(risk_examples.get("suppressed_mic_asr_segments"), list)
            else []
        )
        for row_index, row in enumerate(rows, start=1):
            if not isinstance(row, dict):
                continue
            features = live_only_candidate_features(row)
            accepted, reject_reasons = is_live_only_candidate(features)
            context = {
                "candidate_source": "live-only",
                "used_batch_fields_for_selection": False,
                "session": session.name,
                "source_comparison": str(comparison_path),
                "row_index": row_index,
                "chunk_index": row.get("chunk_index"),
                "selection_features": features,
            }
            if not accepted:
                rejected.append({**context, "reject_reasons": reject_reasons})
                continue
            start_sec = safe_float(row.get("start"))
            end_sec = safe_float(row.get("end"))
            text = features["text"]
            candidates.append(
                {
                    **context,
                    "priority": 50,
                    "batch_id": None,
                    "start": round(start_sec, 3),
                    "end": round(end_sec, 3),
                    "duration_sec": round(end_sec - start_sec, 3),
                    "text": text,
                    "local_island_examples": [
                        {
                            "start": round(start_sec, 3),
                            "end": round(end_sec, 3),
                            "text": text,
                            "source": "live_suppressed_mic_segment",
                            "chunk_index": row.get("chunk_index"),
                            "source_row_index": row_index,
                        }
                    ],
                    "split_row": None,
                    "source_row": row,
                }
            )
    candidates.sort(
        key=lambda row: (
            -safe_float((row.get("selection_features") or {}).get("segment_gate_unique_token_count")),
            -safe_float(row.get("duration_sec")),
            str(row.get("session") or ""),
            safe_float(row.get("start")),
        )
    )
    return candidates[:max_candidates], rejected


def select_blocker_analysis_candidates(report: dict[str, Any], max_candidates: int) -> list[dict[str, Any]]:
    analysis = report.get("capture_safe_candidate_local_recall_blocker_analysis")
    examples = analysis.get("examples") if isinstance(analysis, dict) and isinstance(analysis.get("examples"), list) else []
    candidates: list[dict[str, Any]] = []
    for item_index, item in enumerate(examples, start=1):
        if not isinstance(item, dict):
            continue
        blocker = item.get("blocker") if isinstance(item.get("blocker"), dict) else {}
        if blocker.get("label") != "duplicate_heavy_mixed_needs_token_split":
            continue
        item_start = safe_float(item.get("start"))
        item_end = safe_float(item.get("end"), item_start)
        evidence_rows = item.get("suppressed_evidence") if isinstance(item.get("suppressed_evidence"), list) else []
        islands: list[dict[str, Any]] = []
        for evidence_index, evidence in enumerate(evidence_rows, start=1):
            if not isinstance(evidence, dict):
                continue
            start = max(item_start, safe_float(evidence.get("start")))
            end = min(item_end, safe_float(evidence.get("end"), start))
            if end <= start:
                continue
            islands.append(
                {
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "text": clean_text(str(evidence.get("text") or "")),
                    "source": "blocker_suppressed_mic_evidence",
                    "chunk_index": evidence.get("chunk_index"),
                    "source_row_index": evidence_index,
                    "segment_gate_reason": evidence.get("segment_gate_reason"),
                    "segment_gate_unique_token_count": evidence.get("segment_gate_unique_token_count"),
                    "segment_gate_mic_token_recall_in_overlapping_remote": evidence.get(
                        "segment_gate_mic_token_recall_in_overlapping_remote"
                    ),
                    "audio_mic_minus_remote_rms_db": evidence.get("audio_mic_minus_remote_rms_db"),
                    "audio_mic_remote_zero_lag_abs_corr": evidence.get("audio_mic_remote_zero_lag_abs_corr"),
                    "rescue_policy_candidates": evidence.get("rescue_policy_candidates") or [],
                }
            )
        if not islands:
            continue
        candidates.append(
            {
                "candidate_source": "blocker-analysis",
                "used_batch_fields_for_selection": True,
                "priority": 10,
                "session": item.get("session"),
                "batch_id": item.get("batch_id"),
                "start": item.get("start"),
                "end": item.get("end"),
                "duration_sec": item.get("duration_sec"),
                "text": clean_text(str(item.get("text") or "")),
                "local_island_examples": islands,
                "selection_features": {
                    "blocker_label": blocker.get("label"),
                    "blocker_reason": blocker.get("reason"),
                    "duplicate_overlap_seconds": blocker.get("duplicate_overlap_seconds"),
                    "local_overlap_seconds": blocker.get("local_overlap_seconds"),
                    "remote_risk_overlap_seconds": blocker.get("remote_risk_overlap_seconds"),
                    "max_unique_tokens": blocker.get("max_unique_tokens"),
                    "max_mic_token_recall_in_overlapping_remote": blocker.get(
                        "max_mic_token_recall_in_overlapping_remote"
                    ),
                    "min_audio_mic_remote_zero_lag_abs_corr": blocker.get(
                        "min_audio_mic_remote_zero_lag_abs_corr"
                    ),
                    "max_audio_mic_minus_remote_rms_db": blocker.get("max_audio_mic_minus_remote_rms_db"),
                    "source_example_index": item_index,
                },
                "source_item": item,
            }
        )
    candidates.sort(
        key=lambda row: (
            -safe_float((row.get("selection_features") or {}).get("duplicate_overlap_seconds")),
            -safe_float(row.get("duration_sec")),
            str(row.get("session") or ""),
            safe_float(row.get("start")),
        )
    )
    return candidates[:max_candidates]


def output_stem(*, candidate_index: int, island_index: int, source_label: str, window_label: str) -> str:
    safe_label = re.sub(r"[^0-9A-Za-z_.-]+", "_", source_label)
    safe_window = re.sub(r"[^0-9A-Za-z_.-]+", "_", window_label)
    return f"candidate_{candidate_index:03d}_island_{island_index:02d}_{safe_label}_{safe_window}"


def run_micro_asr_attempt(
    *,
    bridge: Any,
    source: dict[str, Any],
    micro_dir: Path,
    whisper_cli: str,
    model: Path,
    language: str,
    threads: int,
    candidate_index: int,
    island_index: int,
    start_sec: float,
    end_sec: float,
    window_label: str,
    before_sec: float,
    after_sec: float,
    leading_silence_ms: int,
    remote_text: str,
    force: bool,
) -> tuple[str, dict[str, Any]]:
    source_path = Path(source["source"])
    source_start_sec = safe_float(source.get("source_start_sec"))
    source_end = source.get("source_end_sec")
    source_end_sec = safe_float(source_end) if source_end is not None else None
    recognition_start_sec = max(source_start_sec, start_sec - before_sec)
    recognition_end_sec = end_sec + after_sec
    if source_end_sec is not None:
        recognition_end_sec = min(source_end_sec, recognition_end_sec)
    if recognition_end_sec <= recognition_start_sec:
        return "", {
            "status": "failed",
            "reason": "empty_window_after_source_bounds",
            "source_label": source.get("source_label"),
            "window_label": window_label,
        }

    target_start_ms = int(round(start_sec * 1000))
    target_end_ms = int(round(end_sec * 1000))
    selection_start_ms = target_start_ms
    selection_end_ms = target_end_ms
    source_relative_start_sec = max(0.0, recognition_start_sec - source_start_sec)
    duration_sec = max(0.001, recognition_end_sec - recognition_start_sec)
    stem = output_stem(
        candidate_index=candidate_index,
        island_index=island_index,
        source_label=str(source.get("source_label") or "source"),
        window_label=window_label,
    )
    wav_path = micro_dir / f"{stem}.wav"
    output_base = micro_dir / stem
    json_path = output_base.with_suffix(".json")

    if force or not json_path.exists():
        ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
        ffmpeg_command = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{source_relative_start_sec:.3f}",
            "-t",
            f"{duration_sec:.3f}",
            "-i",
            str(source_path),
            *(
                ["-af", f"adelay={leading_silence_ms}:all=1"]
                if leading_silence_ms > 0
                else []
            ),
            "-ar",
            "16000",
            "-ac",
            "1",
            str(wav_path),
        ]
        ffmpeg_returncode = run(ffmpeg_command, output_base.with_suffix(".ffmpeg.log"))
        if ffmpeg_returncode != 0:
            return "", {
                "status": "failed",
                "reason": "ffmpeg_failed",
                "returncode": ffmpeg_returncode,
                "source": str(source_path),
                "source_label": source.get("source_label"),
                "window_label": window_label,
                "ffmpeg_log": str(output_base.with_suffix(".ffmpeg.log")),
            }
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
            str(wav_path),
        ]
        returncode = run(command, output_base.with_suffix(".run.log"))
        if returncode != 0:
            return "", {
                "status": "failed",
                "reason": "whisper_cli_failed",
                "returncode": returncode,
                "source": str(source_path),
                "source_label": source.get("source_label"),
                "window_label": window_label,
                "run_log": str(output_base.with_suffix(".run.log")),
            }
    if not json_path.exists():
        return "", {
            "status": "failed",
            "reason": "micro_json_not_found",
            "source": str(source_path),
            "source_label": source.get("source_label"),
            "window_label": window_label,
        }

    global_offset_ms = int(round(recognition_start_sec * 1000)) - leading_silence_ms
    text, rows = bridge.read_micro_reasr_text(json_path, global_offset_ms, selection_start_ms, selection_end_ms)
    score = bridge.score_micro_reasr_text(
        text,
        rows,
        target_start_ms,
        target_end_ms,
        remote_context_text=remote_text,
    )
    return text, {
        "status": "ok" if text else "empty",
        "source_scope": source.get("source_scope"),
        "source_label": source.get("source_label"),
        "source": str(source_path),
        "chunk_index": source.get("chunk_index"),
        "remote_text": remote_text,
        "window_label": window_label,
        "recognition_start_sec": round(recognition_start_sec, 3),
        "recognition_end_sec": round(recognition_end_sec, 3),
        "selection_start_sec": round(start_sec, 3),
        "selection_end_sec": round(end_sec, 3),
        "leading_silence_ms": leading_silence_ms,
        "text": text,
        "score": round(float(score), 6),
        "remote_similarity": round(float(bridge.text_similarity(text, remote_text)), 6) if remote_text else 0.0,
        "json": str(json_path),
        "wav": str(wav_path),
        "rows": rows,
    }


def classify_best_attempt(
    *,
    best: dict[str, Any] | None,
    batch_text: str,
    island_text: str,
    remote_text: str,
    bridge: Any,
) -> dict[str, Any]:
    if not best or best.get("status") != "ok":
        return {
            "label": "no_micro_asr_candidate",
            "publication_ready": False,
            "reason": "no successful non-empty micro-ASR attempt",
        }
    text = str(best.get("text") or "")
    batch_tokens = tokens(batch_text)
    micro_recall = bag_recall(batch_tokens, tokens(text))
    island_recall = bag_recall(batch_tokens, tokens(island_text))
    remote_similarity = float(best.get("remote_similarity") or 0.0)
    remote_recall = bag_recall(tokens(remote_text), tokens(text)) if remote_text else 0.0
    score = safe_float(best.get("score"))
    if remote_similarity > 0.42 or remote_recall > 0.50:
        label = "blocked_remote_similarity"
        reason = "micro-ASR text is too similar to overlapping remote text"
    elif score < 0.68:
        label = "blocked_low_micro_score"
        reason = "micro-ASR score is below diagnostic threshold"
    elif micro_recall <= island_recall + 0.05:
        label = "blocked_no_alignment_gain"
        reason = "micro-ASR does not materially improve token recall over existing island text"
    elif micro_recall < 0.30:
        label = "blocked_low_batch_alignment"
        reason = "micro-ASR still has weak alignment with the missing batch Me row"
    else:
        label = "micro_asr_alignment_candidate"
        reason = "micro-ASR improves local island alignment while staying remote-dissimilar"
    return {
        "label": label,
        "publication_ready": False,
        "reason": reason,
        "batch_token_recall": round(micro_recall, 6),
        "existing_island_batch_token_recall": round(island_recall, 6),
        "remote_text_recall_in_micro": round(remote_recall, 6),
        "remote_similarity": round(remote_similarity, 6),
        "score": round(score, 6),
    }


def classify_best_live_only_attempt(
    *,
    best: dict[str, Any] | None,
    source_text: str,
    remote_text: str,
    bridge: Any,
) -> dict[str, Any]:
    if not best or best.get("status") != "ok":
        return {
            "label": "no_micro_asr_candidate",
            "publication_ready": False,
            "reason": "no successful non-empty live-only micro-ASR attempt",
        }
    text = str(best.get("text") or "")
    source_recall = bag_recall(tokens(source_text), tokens(text))
    micro_source_recall = bag_recall(tokens(text), tokens(source_text))
    remote_similarity = float(best.get("remote_similarity") or 0.0)
    remote_recall = bag_recall(tokens(remote_text), tokens(text)) if remote_text else 0.0
    score = safe_float(best.get("score"))
    if remote_similarity > 0.30 or remote_recall > 0.10:
        label = "blocked_remote_similarity"
        reason = "live-only micro-ASR text is too similar to overlapping remote text"
    elif score < 0.68:
        label = "blocked_low_micro_score"
        reason = "micro-ASR score is below live-only threshold"
    elif source_recall < 0.25 and micro_source_recall < 0.25:
        label = "blocked_low_live_source_alignment"
        reason = "micro-ASR text does not align with the live suppressed-mic source text"
    else:
        label = "micro_asr_live_only_alignment_candidate"
        reason = "live-only micro-ASR has local-source support and low remote similarity"
    return {
        "label": label,
        "publication_ready": False,
        "reason": reason,
        "used_batch_fields_for_selection": False,
        "source_text_token_recall": round(source_recall, 6),
        "micro_text_token_recall_in_source": round(micro_source_recall, 6),
        "remote_text_recall_in_micro": round(remote_recall, 6),
        "remote_similarity": round(remote_similarity, 6),
        "score": round(score, 6),
    }


def classify_best_blocker_attempt(
    *,
    best: dict[str, Any] | None,
    batch_text: str,
    source_text: str,
    remote_text: str,
    bridge: Any,
) -> dict[str, Any]:
    if not best or best.get("status") != "ok":
        return {
            "label": "no_micro_asr_candidate",
            "publication_ready": False,
            "reason": "no successful non-empty duplicate-heavy micro-ASR attempt",
        }
    text = str(best.get("text") or "")
    batch_recall = bag_recall(tokens(batch_text), tokens(text))
    source_recall = bag_recall(tokens(source_text), tokens(text))
    micro_source_recall = bag_recall(tokens(text), tokens(source_text))
    remote_similarity = float(best.get("remote_similarity") or 0.0)
    source_remote_similarity = float(bridge.text_similarity(source_text, remote_text)) if remote_text else 0.0
    remote_recall = bag_recall(tokens(remote_text), tokens(text)) if remote_text else 0.0
    score = safe_float(best.get("score"))
    similarity_improvement = source_remote_similarity - remote_similarity
    if score < 0.68:
        label = "blocked_low_micro_score"
        reason = "micro-ASR score is below duplicate-heavy threshold"
    elif batch_recall < 0.25 and source_recall < 0.35:
        label = "blocked_low_local_alignment"
        reason = "micro-ASR text does not align enough with batch or suppressed mic source"
    elif remote_similarity > 0.38 and remote_recall > 0.30 and similarity_improvement < 0.12:
        label = "blocked_still_remote_similar"
        reason = "micro-ASR text remains too similar to overlapping remote text"
    elif similarity_improvement < 0.05 and remote_recall > 0.15:
        label = "blocked_no_remote_similarity_improvement"
        reason = "micro-ASR does not reduce remote similarity enough"
    else:
        label = "micro_asr_duplicate_heavy_split_candidate"
        reason = "micro-ASR gives local-aligned text with better remote separation than the original mixed segment"
    return {
        "label": label,
        "publication_ready": False,
        "reason": reason,
        "used_batch_fields_for_selection": True,
        "batch_token_recall": round(batch_recall, 6),
        "source_text_token_recall": round(source_recall, 6),
        "micro_text_token_recall_in_source": round(micro_source_recall, 6),
        "remote_text_recall_in_micro": round(remote_recall, 6),
        "remote_similarity": round(remote_similarity, 6),
        "source_remote_similarity": round(source_remote_similarity, 6),
        "remote_similarity_improvement": round(similarity_improvement, 6),
        "score": round(score, 6),
    }


def markdown_report(report: dict[str, Any]) -> str:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    candidate_source = summary.get("candidate_source") or "design-lab"
    lines = [
        "# Live Boundary-Island Micro-ASR Lab",
        "",
        "Diagnostic only. This lab never changes live drafts, batch transcripts or promotion gates.",
        "",
        f"- status: `{report.get('status')}`",
        f"- candidate source: `{candidate_source}`",
        f"- used batch fields for selection: `{summary.get('used_batch_fields_for_selection')}`",
        f"- candidates: {summary.get('candidate_count', 0)}",
        f"- islands: {summary.get('island_count', 0)}",
        f"- attempts: {summary.get('attempt_count', 0)}",
        f"- alignment candidates: {summary.get('alignment_candidate_count', 0)} / {summary.get('alignment_candidate_seconds', 0.0)} sec",
        f"- publication-ready seconds now: {summary.get('publication_ready_seconds', 0.0)}",
        "",
        "| Session | Batch id | Island | Best label | Source | Score | Source recall | Remote sim | Text |",
        "| --- | --- | ---: | --- | --- | ---: | ---: | ---: | --- |",
    ]
    for row in report.get("items") or []:
        if not isinstance(row, dict):
            continue
        best = row.get("best_attempt") if isinstance(row.get("best_attempt"), dict) else {}
        decision = row.get("decision") if isinstance(row.get("decision"), dict) else {}
        text = clean_text(str(best.get("text") or "")).replace("|", "\\|")
        lines.append(
            f"| `{row.get('session')}` | `{row.get('batch_id')}` | {row.get('island_index')} "
            f"| `{decision.get('label')}` | `{best.get('source_label')}` / `{best.get('window_label')}` "
            f"| {safe_float(best.get('score')):.3f} "
            f"| {safe_float(decision.get('source_text_token_recall', decision.get('batch_token_recall'))):.3f} "
            f"| {safe_float(best.get('remote_similarity')):.3f} "
            f"| {text[:120]} |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    report_path = args.report
    if args.candidate_source in {"design-lab", "blocker-analysis"} and not report_path.exists():
        print(f"report not found: {report_path}", file=sys.stderr)
        return 2
    model = args.model.expanduser()
    if not model.exists():
        print(f"model not found: {model}", file=sys.stderr)
        return 2
    whisper_cli = shutil.which(args.whisper_cli) or args.whisper_cli
    if shutil.which(args.whisper_cli) is None and not Path(args.whisper_cli).exists():
        print(f"whisper-cli not found: {args.whisper_cli}", file=sys.stderr)
        return 2

    bridge = load_transcribe_bridge()
    corpus_report = read_json(report_path) if report_path.exists() else {}
    rejected_candidates: list[dict[str, Any]] = []
    if args.candidate_source == "live-only":
        candidates, rejected_candidates = select_live_only_candidates(args.sessions_root, args.max_candidates)
    elif args.candidate_source == "blocker-analysis":
        candidates = select_blocker_analysis_candidates(corpus_report, args.max_candidates)
    else:
        candidates = select_candidates(corpus_report, args.max_candidates)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    paths = output_paths(args.out_dir, args.candidate_source)
    attempts_jsonl = paths["attempts_jsonl"]
    report_json = paths["report_json"]
    report_md = paths["report_md"]
    effective_source_scope = "live" if args.candidate_source == "live-only" else args.source_scope

    windows = [
        ("tight", 0.3, 0.3),
        ("normal", 0.9, 0.7),
        ("wide", 1.6, 1.0),
    ]
    all_attempts: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []

    for candidate_index, candidate in enumerate(candidates, start=1):
        session_name = str(candidate.get("session") or "")
        session = args.sessions_root / session_name
        batch_text = str(candidate.get("text") or "")
        islands = candidate.get("local_island_examples") if isinstance(candidate.get("local_island_examples"), list) else []
        for island_index, island in enumerate(islands, start=1):
            if not isinstance(island, dict):
                continue
            start_sec = safe_float(island.get("start"))
            end_sec = safe_float(island.get("end"))
            if end_sec <= start_sec:
                continue
            island_text = str(island.get("text") or "")
            sources: list[dict[str, Any]] = []
            live_sources = live_chunk_sources(session, start_sec, end_sec)
            candidate_remote_text = clean_text(
                " ".join(str(row.get("remote_text") or "") for row in live_sources if isinstance(row, dict))
            )
            if effective_source_scope in {"live", "both"}:
                sources.extend(live_sources)
            if effective_source_scope in {"batch-reference", "both"}:
                sources.extend(batch_reference_sources(session, candidate_remote_text))
            island_attempts: list[dict[str, Any]] = []
            for source in sources:
                remote_text = str(source.get("remote_text") or "")
                for window_label, before_sec, after_sec in windows:
                    text, meta = run_micro_asr_attempt(
                        bridge=bridge,
                        source=source,
                        micro_dir=session / "derived/live/boundary-island-micro-asr-lab/micro_asr",
                        whisper_cli=whisper_cli,
                        model=model,
                        language=args.language,
                        threads=args.threads,
                        candidate_index=candidate_index,
                        island_index=island_index,
                        start_sec=start_sec,
                        end_sec=end_sec,
                        window_label=window_label,
                        before_sec=before_sec,
                        after_sec=after_sec,
                        leading_silence_ms=400,
                        remote_text=remote_text,
                        force=args.force,
                    )
                    meta.update(
                        {
                            "candidate_index": candidate_index,
                            "island_index": island_index,
                            "session": session_name,
                            "batch_id": candidate.get("batch_id"),
                            "batch_text": batch_text,
                            "existing_island_text": island_text,
                        }
                    )
                    if text:
                        meta["batch_token_recall"] = round(bag_recall(tokens(batch_text), tokens(text)), 6)
                        meta["existing_island_batch_token_recall"] = round(
                            bag_recall(tokens(batch_text), tokens(island_text)),
                            6,
                        )
                    island_attempts.append(meta)
                    all_attempts.append(meta)
            successful = [row for row in island_attempts if row.get("status") == "ok" and row.get("text")]
            successful.sort(
                key=lambda row: (
                    safe_float(row.get("batch_token_recall")),
                    -safe_float(row.get("remote_similarity")),
                    safe_float(row.get("score")),
                ),
                reverse=True,
            )
            live_successful = [row for row in successful if row.get("source_scope") == "live"]
            reference_successful = [row for row in successful if row.get("source_scope") == "batch_reference"]
            best_live = live_successful[0] if live_successful else None
            best_reference = reference_successful[0] if reference_successful else None
            best = best_live or best_reference or (successful[0] if successful else None)
            remote_text = str(best.get("remote_text") or "") if isinstance(best, dict) else ""
            if args.candidate_source == "live-only":
                decision = classify_best_live_only_attempt(
                    best=best,
                    source_text=island_text or batch_text,
                    remote_text=remote_text,
                    bridge=bridge,
                )
            elif args.candidate_source == "blocker-analysis":
                decision = classify_best_blocker_attempt(
                    best=best,
                    batch_text=batch_text,
                    source_text=island_text,
                    remote_text=remote_text,
                    bridge=bridge,
                )
            else:
                decision = classify_best_attempt(
                    best=best,
                    batch_text=batch_text,
                    island_text=island_text,
                    remote_text=remote_text,
                    bridge=bridge,
                )
            items.append(
                {
                    "schema": "murmurmark.live_boundary_island_micro_asr_item/v1",
                    "candidate_source": args.candidate_source,
                    "used_batch_fields_for_selection": args.candidate_source != "live-only",
                    "selection_features": candidate.get("selection_features"),
                    "candidate_index": candidate_index,
                    "island_index": island_index,
                    "session": session_name,
                    "batch_id": candidate.get("batch_id"),
                    "start": start_sec,
                    "end": end_sec,
                    "duration_sec": round(end_sec - start_sec, 3),
                    "batch_text": batch_text,
                    "existing_island_text": island_text,
                    "best_attempt": best,
                    "best_live_attempt": best_live,
                    "best_reference_attempt": best_reference,
                    "decision": decision,
                    "attempt_count": len(island_attempts),
                    "successful_attempt_count": len(successful),
                }
            )

    alignment_candidates = [
        row for row in items
        if isinstance(row.get("decision"), dict)
        and row["decision"].get("label") in {
            "micro_asr_alignment_candidate",
            "micro_asr_live_only_alignment_candidate",
            "micro_asr_duplicate_heavy_split_candidate",
        }
    ]
    summary = {
        "candidate_count": len(candidates),
        "island_count": len(items),
        "attempt_count": len(all_attempts),
        "successful_attempt_count": sum(1 for row in all_attempts if row.get("status") == "ok"),
        "alignment_candidate_count": len(alignment_candidates),
        "alignment_candidate_seconds": round(sum(safe_float(row.get("duration_sec")) for row in alignment_candidates), 3),
        "publication_ready_seconds": 0.0,
        "promotion_allowed": False,
        "candidate_source": args.candidate_source,
        "source_scope": args.source_scope,
        "effective_source_scope": effective_source_scope,
        "rejected_candidate_count": len(rejected_candidates),
        "used_batch_fields_for_selection": args.candidate_source != "live-only",
    }
    payload = {
        "schema": SCHEMA,
        "generator": {"name": "report-live-boundary-island-micro-asr-lab", "version": SCRIPT_VERSION},
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "ok" if items else "no_boundary_island_candidates",
        "promotion_allowed": False,
        "interpretation": (
            "diagnostic only: micro-decodes local islands from remaining live gaps. "
            "It can identify a future shadow-profile candidate, but cannot publish live text by itself."
        ),
        "inputs": {
            "live_corpus_gates_report": str(report_path) if report_path.exists() else None,
            "sessions_root": str(args.sessions_root),
            "model": str(model),
            "whisper_cli": whisper_cli,
        },
        "summary": summary,
        "items": items,
        "rejected_candidates": rejected_candidates[:200],
        "outputs": {
            "report_json": str(report_json),
            "attempts_jsonl": str(attempts_jsonl),
            "report_markdown": str(report_md),
        },
    }
    write_json(report_json, payload)
    write_jsonl(attempts_jsonl, all_attempts)
    report_md.write_text(markdown_report(payload), encoding="utf-8")
    print(f"status: {payload['status']}")
    print(f"report: {report_json}")
    print(f"attempts: {attempts_jsonl}")
    print(f"alignment_candidate_count: {summary['alignment_candidate_count']}")
    print(f"alignment_candidate_seconds: {summary['alignment_candidate_seconds']}")
    print("promotion_allowed: false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
