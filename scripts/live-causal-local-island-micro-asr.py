#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np


SCHEMA = "murmurmark.live_causal_local_island_micro_asr/v2"
SCRIPT_VERSION = "2.1.0"
BASELINE_PROFILE = (
    "online_live_me_remote_overlap_filter_live_boundary_split_retime_causal_remote_energy_v1"
)
OUTPUT_RELATIVE = Path("derived/live/causal-local-island-micro-asr-v2")
REMOTE_GUARD_SEC = 0.10
GROUP_GAP_SEC = 0.75
MIN_ISLAND_SEC = 0.50
MAX_ISLAND_SEC = 12.0
MIN_MICRO_ASR_SCORE = 0.68
MIN_SOURCE_ALIGNMENT = 0.25
MAX_REMOTE_SIMILARITY = 0.30
MIN_SOURCE_TOKEN_COUNT = 4
EPSILON = 1.0e-9


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def clean_text(value: Any) -> str:
    return " ".join(str(value or "").replace("\n", " ").split()).strip()


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
    return rows


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def interval_overlap(start: float, end: float, other_start: float, other_end: float) -> float:
    return max(0.0, min(end, other_end) - max(start, other_start))


def stable_id(session: str, chunk_index: int, start: float, end: float) -> str:
    digest = hashlib.sha256(
        f"{session}\0{chunk_index}\0{start:.3f}\0{end:.3f}".encode("utf-8")
    ).hexdigest()[:16]
    return f"live_causal_local_island_v2_{digest}"


def load_progressive_module() -> Any:
    path = Path(__file__).with_name("live-progressive-target-me.py")
    spec = importlib.util.spec_from_file_location("murmurmark_causal_local_island_backend", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def recording_time_segment_evidence(
    *,
    chunk: dict[str, Any],
    segments_by_key: dict[tuple[str, int], dict[str, Any]],
    session_ended_at: datetime | None,
) -> dict[str, Any]:
    index = safe_int(chunk.get("index"))
    mic = segments_by_key.get(("mic", index)) or {}
    remote = segments_by_key.get(("remote", index)) or {}
    rows = [mic, remote]
    sources_present = all(rows)
    closed = sources_present and all(row.get("closed") is True for row in rows)
    committed_pcm = sources_present and all(
        row.get("provenance") == "recording_time_committed_pcm"
        or row.get("materialized_from_raw_commit") is True
        for row in rows
    )
    chunk_created_at = parse_datetime(chunk.get("created_at"))
    pre_stop = bool(
        chunk_created_at is not None
        and session_ended_at is not None
        and chunk_created_at <= session_ended_at
    )
    checks = {
        "paired_sources_present": sources_present,
        "segments_closed": closed,
        "committed_pcm_or_raw_commit_materialization": committed_pcm,
    }
    materialization_mode = (
        "pre_stop_committed_pcm"
        if pre_stop
        else "causal_replay_from_committed_raw"
        if committed_pcm
        else "unproven"
    )
    return {
        "status": "passed" if all(checks.values()) else "rejected",
        "checks": checks,
        "materialization_mode": materialization_mode,
        "chunk_created_before_stop": pre_stop,
        "chunk_index": index,
        "chunk_created_at": chunk.get("created_at"),
        "session_ended_at": session_ended_at.isoformat() if session_ended_at else None,
        "mic_segment": {
            key: mic.get(key)
            for key in ("index", "start_sec", "end_sec", "closed", "provenance", "materialized_from_raw_commit")
        },
        "remote_segment": {
            key: remote.get(key)
            for key in ("index", "start_sec", "end_sec", "closed", "provenance", "materialized_from_raw_commit")
        },
        "used_batch_fields_for_selection": False,
    }


def audio_guard_from_evaluation(row: dict[str, Any], progressive: Any) -> dict[str, Any]:
    features = row.get("audio") if isinstance(row.get("audio"), dict) else {}
    return progressive.remote_audio_guard(features)


def overlaps_remote(
    start: float,
    end: float,
    remote_segments: list[dict[str, Any]],
    *,
    guard_sec: float = REMOTE_GUARD_SEC,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for remote in remote_segments:
        remote_start = safe_float(remote.get("start")) - guard_sec
        remote_end = safe_float(remote.get("end"), remote_start) + guard_sec
        overlap = interval_overlap(start, end, remote_start, remote_end)
        if overlap > EPSILON:
            rows.append(
                {
                    "start": remote.get("start"),
                    "end": remote.get("end"),
                    "text": remote.get("text"),
                    "guarded_overlap_sec": round(overlap, 3),
                }
            )
    return rows


def covered_by_existing_me(
    row: dict[str, Any],
    existing_me: list[dict[str, Any]],
    progressive: Any,
) -> dict[str, Any] | None:
    start = safe_float(row.get("start"))
    end = safe_float(row.get("end"), start)
    duration = max(EPSILON, end - start)
    source_text = clean_text(row.get("text"))
    for turn in existing_me:
        turn_start = safe_float(turn.get("start"))
        turn_end = safe_float(turn.get("end"), turn_start)
        overlap = interval_overlap(start, end, turn_start, turn_end)
        overlap_ratio = overlap / duration
        similarity = (
            progressive.asr_text_similarity(source_text, turn.get("text"))
            if source_text and clean_text(turn.get("text"))
            else 0.0
        )
        near = interval_overlap(start - 1.0, end + 1.0, turn_start, turn_end) > 0.0
        if overlap_ratio >= 0.65 or (near and similarity >= 0.70):
            return {
                "turn_id": turn.get("id"),
                "start": turn.get("start"),
                "end": turn.get("end"),
                "text": turn.get("text"),
                "overlap_sec": round(overlap, 3),
                "overlap_ratio": round(overlap_ratio, 6),
                "text_similarity": round(similarity, 6),
            }
    return None


def existing_me_through_chunk(
    rows: list[dict[str, Any]],
    chunk_index: int,
) -> list[dict[str, Any]]:
    """Keep runtime deduplication causal when a worker sees several closed chunks."""
    return [
        row
        for row in rows
        if safe_int(row.get("chunk_index"), chunk_index) <= chunk_index
    ]


def select_strict_evaluations(
    *,
    session: Path,
    evaluations: list[dict[str, Any]],
    chunks: dict[int, dict[str, Any]],
    segments_by_key: dict[tuple[str, int], dict[str, Any]],
    existing_me: list[dict[str, Any]],
    progressive: Any,
    causal_existing_cutoff: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    session_meta = read_json(session / "session.json")
    ended_at = parse_datetime(session_meta.get("ended_at"))
    selected: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    remote_cache: dict[int, list[dict[str, Any]]] = {}
    for source_index, row in enumerate(evaluations, start=1):
        chunk_index = safe_int(row.get("chunk_index"))
        chunk = chunks.get(chunk_index) or {}
        start = safe_float(row.get("start"))
        end = safe_float(row.get("end"), start)
        duration = max(0.0, end - start)
        enrollment = row.get("enrollment") if isinstance(row.get("enrollment"), dict) else {}
        timeline = recording_time_segment_evidence(
            chunk=chunk,
            segments_by_key=segments_by_key,
            session_ended_at=ended_at,
        )
        if chunk_index not in remote_cache:
            remote_source = chunk.get("remote") if isinstance(chunk.get("remote"), dict) else {}
            remote_cache[chunk_index] = progressive.read_asr_segments(remote_source, session)
        remote_overlap = overlaps_remote(start, end, remote_cache[chunk_index])
        audio_guard = audio_guard_from_evaluation(row, progressive)
        existing = covered_by_existing_me(
            row,
            (
                existing_me_through_chunk(existing_me, chunk_index)
                if causal_existing_cutoff
                else existing_me
            ),
            progressive,
        )
        checks = {
            "speaker_supported": row.get("classification") == "causal_target_me_supported",
            "timeline_causal": row.get("timeline_causal") is True,
            "selection_does_not_use_batch": row.get("used_batch_fields_for_selection") is False,
            "supported_duration": MIN_ISLAND_SEC <= duration <= MAX_ISLAND_SEC,
            "past_only_enrollment": enrollment.get("mode") == "past_chunks_only",
            "past_enrollment_ready": safe_int(enrollment.get("positive_seed_count")) >= 3
            and safe_int(enrollment.get("negative_seed_count")) >= 3,
            "enrollment_cutoff_causal": safe_float(enrollment.get("cutoff_sec"), start) <= start + 0.01,
            "recording_time_committed_pcm": timeline.get("status") == "passed",
            "remote_audio_guard_passed": audio_guard.get("status") == "passed",
            "remote_audio_strictly_quiet": safe_float(audio_guard.get("remote_db"), 0.0)
            <= safe_float(progressive.REMOTE_AUDIO_QUIET_MAX_DB, -65.0),
            "remote_asr_guard_clear": not remote_overlap,
            "not_already_published": existing is None,
            "source_text_present": bool(clean_text(row.get("text"))),
            "source_text_contentful": len(progressive.tokens(row.get("text")))
            >= MIN_SOURCE_TOKEN_COUNT,
        }
        rejected = [name for name, passed in checks.items() if not passed]
        decision = {
            "schema": SCHEMA,
            "kind": "strict_island_selection",
            "id": f"selection_{chunk_index:06d}_{source_index:04d}",
            "session": session.name,
            "chunk_index": chunk_index,
            "start": round(start, 3),
            "end": round(end, 3),
            "duration_sec": round(duration, 3),
            "text": clean_text(row.get("text")),
            "status": "selected" if not rejected else "rejected",
            "reasons": rejected,
            "checks": checks,
            "speaker_evidence": {
                "scores": row.get("scores") or {},
                "thresholds": row.get("thresholds") or {},
                "enrollment": enrollment,
            },
            "recording_time_evidence": timeline,
            "remote_audio_guard": audio_guard,
            "remote_asr_overlap": remote_overlap,
            "existing_live_turn": existing,
            "source_evaluation": {
                "segment_index": row.get("segment_index"),
                "segment_gate_status": row.get("segment_gate_status"),
                "chunk_role_gate_status": row.get("chunk_role_gate_status"),
            },
            "used_batch_fields_for_selection": False,
            "timeline_causal": True,
        }
        decisions.append(decision)
        if not rejected:
            selected.append({**row, "strict_selection": decision})
    return dedupe_selected_evaluations(selected, progressive), decisions


def dedupe_selected_evaluations(rows: list[dict[str, Any]], progressive: Any) -> list[dict[str, Any]]:
    ranked = sorted(
        rows,
        key=lambda row: (
            -safe_float((row.get("scores") or {}).get("target")),
            -safe_float((row.get("audio") or {}).get("mic_minus_remote_db")),
            safe_float(row.get("start")),
        ),
    )
    kept: list[dict[str, Any]] = []
    for row in ranked:
        start = safe_float(row.get("start"))
        end = safe_float(row.get("end"), start)
        duration = max(EPSILON, end - start)
        duplicate = False
        for existing in kept:
            other_start = safe_float(existing.get("start"))
            other_end = safe_float(existing.get("end"), other_start)
            overlap = interval_overlap(start, end, other_start, other_end)
            ratio = overlap / min(duration, max(EPSILON, other_end - other_start))
            similarity = progressive.asr_text_similarity(row.get("text"), existing.get("text"))
            if ratio >= 0.75 and similarity >= 0.55:
                duplicate = True
                break
        if not duplicate:
            kept.append(row)
    return sorted(kept, key=lambda row: (safe_float(row.get("start")), safe_int(row.get("chunk_index"))))


def group_selected_evaluations(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    by_chunk: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        by_chunk.setdefault(safe_int(row.get("chunk_index")), []).append(row)
    groups: list[list[dict[str, Any]]] = []
    for chunk_index in sorted(by_chunk):
        current: list[dict[str, Any]] = []
        for row in sorted(by_chunk[chunk_index], key=lambda item: safe_float(item.get("start"))):
            if not current:
                current = [row]
                continue
            gap = safe_float(row.get("start")) - safe_float(current[-1].get("end"))
            combined_duration = safe_float(row.get("end")) - safe_float(current[0].get("start"))
            if gap <= GROUP_GAP_SEC and combined_duration <= MAX_ISLAND_SEC:
                current.append(row)
            else:
                groups.append(current)
                current = [row]
        if current:
            groups.append(current)
    return sorted(groups, key=lambda group: (safe_float(group[0].get("start")), safe_int(group[0].get("chunk_index"))))


class MicroASRMaterializer:
    def __init__(
        self,
        *,
        session: Path,
        chunks: dict[int, dict[str, Any]],
        existing_me: list[dict[str, Any]],
        progressive: Any,
        model: str,
        language: str,
        whisper_cli: str,
        force: bool,
        causal_existing_cutoff: bool = False,
        runner: Callable[[Path, Path, str, str, str], dict[str, Any]] | None = None,
    ) -> None:
        self.session = session
        self.chunks = chunks
        self.existing_me = existing_me
        self.progressive = progressive
        self.model = model
        self.language = language
        self.whisper_cli = whisper_cli
        self.force = force
        self.causal_existing_cutoff = causal_existing_cutoff
        self.runner = runner or progressive.default_micro_runner
        self.output = session / OUTPUT_RELATIVE
        self.audio_cache: dict[str, tuple[int, np.ndarray]] = {}
        self.accepted: list[dict[str, Any]] = []

    def audio(self, path: Path) -> tuple[int, np.ndarray] | None:
        key = str(path)
        if key not in self.audio_cache:
            try:
                self.audio_cache[key] = self.progressive.read_wav_float(path)
            except (OSError, ValueError):
                return None
        return self.audio_cache[key]

    def resolve_source(self, source: dict[str, Any], *, mic: bool) -> tuple[Path | None, float]:
        return self.progressive.resolve_audio(self.session, source, mic=mic)

    def slice_audio(
        self,
        source: dict[str, Any],
        *,
        mic: bool,
        start: float,
        end: float,
    ) -> tuple[int | None, np.ndarray | None]:
        path, clip_start = self.resolve_source(source, mic=mic)
        if path is None:
            return None, None
        loaded = self.audio(path)
        if loaded is None:
            return None, None
        rate, data = loaded
        local_start = max(0, int(round((start - clip_start) * rate)))
        local_end = min(data.size, int(round((end - clip_start) * rate)))
        if local_end <= local_start:
            return rate, np.asarray([], dtype=np.float32)
        return rate, data[local_start:local_end]

    def audio_features(
        self,
        mic: dict[str, Any],
        remote: dict[str, Any],
        start: float,
        end: float,
    ) -> dict[str, Any]:
        mic_rate, mic_audio = self.slice_audio(mic, mic=True, start=start, end=end)
        remote_rate, remote_audio = self.slice_audio(remote, mic=False, start=start, end=end)
        if mic_rate is None or remote_rate != mic_rate or mic_audio is None or remote_audio is None:
            return {"mic_db": None, "remote_db": None, "mic_minus_remote_db": None, "corr": None}
        count = min(mic_audio.size, remote_audio.size)
        mic_audio = mic_audio[:count]
        remote_audio = remote_audio[:count]
        mic_db = self.progressive.rms_db(mic_audio)
        remote_db = self.progressive.rms_db(remote_audio)
        corr = self.progressive.zero_lag_abs_corr(mic_audio, remote_audio)
        return {
            "mic_db": round(mic_db, 3),
            "remote_db": round(remote_db, 3),
            "mic_minus_remote_db": round(mic_db - remote_db, 3),
            "corr": round(corr, 6) if corr is not None else None,
        }

    def write_clip(
        self,
        source: dict[str, Any],
        *,
        start: float,
        end: float,
        path: Path,
        leading_silence_sec: float,
    ) -> bool:
        rate, data = self.slice_audio(source, mic=True, start=start, end=end)
        if rate is None or data is None or data.size < max(160, int(0.25 * rate)):
            return False
        if leading_silence_sec > 0.0:
            silence = np.zeros(int(round(rate * leading_silence_sec)), dtype=np.float32)
            data = np.concatenate([silence, data])
        self.progressive.write_wav_float(path, rate, data)
        return True

    def cached_or_run(self, wav: Path, output_base: Path) -> dict[str, Any]:
        json_path = output_base.with_suffix(".json")
        txt_path = output_base.with_suffix(".txt")
        if not self.force and json_path.exists() and txt_path.exists():
            text = clean_text(txt_path.read_text(encoding="utf-8", errors="ignore"))
            return {
                "status": "passed",
                "text": text,
                "score": round(self.progressive.token_average_probability(json_path), 6),
                "json": str(json_path),
                "rows": self.progressive.asr_rows(json_path),
                "cache_hit": True,
            }
        result = self.runner(wav, output_base, self.model, self.language, self.whisper_cli)
        result["cache_hit"] = False
        return result

    def materialize(self, group: list[dict[str, Any]], group_index: int) -> dict[str, Any]:
        chunk_index = safe_int(group[0].get("chunk_index"))
        chunk = self.chunks.get(chunk_index) or {}
        mic = chunk.get("mic") if isinstance(chunk.get("mic"), dict) else {}
        remote = chunk.get("remote") if isinstance(chunk.get("remote"), dict) else {}
        source_start = safe_float(group[0].get("start"))
        source_end = safe_float(group[-1].get("end"), source_start)
        source_text = clean_text(" ".join(str(row.get("text") or "") for row in group))
        remote_segments = self.progressive.read_asr_segments(remote, self.session)
        nearby_remote = [
            row
            for row in remote_segments
            if interval_overlap(source_start - 1.0, source_end + 1.0, safe_float(row.get("start")), safe_float(row.get("end")))
            > 0.0
        ]
        remote_text = clean_text(" ".join(str(row.get("text") or "") for row in nearby_remote))
        extraction_start = max(safe_float(mic.get("clip_start_sec")), source_start - 1.0)
        extraction_end = min(
            safe_float(mic.get("clip_end_sec"), source_end + 0.8),
            source_end + 0.8,
        )
        leading_silence_sec = 0.4
        item_id = stable_id(self.session.name, chunk_index, source_start, source_end)
        wav = self.output / "micro_asr" / f"{item_id}.wav"
        output_base = self.output / "micro_asr" / item_id
        extracted = self.write_clip(
            mic,
            start=extraction_start,
            end=extraction_end,
            path=wav,
            leading_silence_sec=leading_silence_sec,
        )
        result = (
            self.cached_or_run(wav, output_base)
            if extracted
            else {"status": "failed", "reason": "clip_extract_failed", "rows": [], "score": 0.0}
        )
        selection_start = leading_silence_sec + source_start - extraction_start
        selection_end = leading_silence_sec + source_end - extraction_start
        selected_rows = [
            row
            for row in result.get("rows") or []
            if isinstance(row, dict)
            and selection_start - EPSILON
            <= (safe_float(row.get("start_sec")) + safe_float(row.get("end_sec"))) / 2.0
            <= selection_end + EPSILON
        ]
        text = clean_text(" ".join(str(row.get("text") or "") for row in selected_rows))
        row_scores = [safe_float(row.get("score")) for row in selected_rows if safe_float(row.get("score")) > 0.0]
        score = float(np.mean(row_scores)) if row_scores else 0.0
        source_alignment = max(
            self.progressive.bag_recall(self.progressive.tokens(source_text), self.progressive.tokens(text)),
            self.progressive.bag_recall(self.progressive.tokens(text), self.progressive.tokens(source_text)),
        )
        remote_similarity = (
            self.progressive.asr_text_similarity(text, remote_text) if text and remote_text else 0.0
        )
        remote_tokens = self.progressive.tokens(remote_text)
        text_tokens = self.progressive.tokens(text)
        remote_recall = self.progressive.bag_recall(remote_tokens, text_tokens) if remote_tokens else 0.0
        remote_matches = self.progressive.bag_match_count(remote_tokens, text_tokens) if remote_tokens else 0
        features = self.audio_features(mic, remote, source_start, source_end)
        remote_guard = self.progressive.remote_audio_guard(features)
        strict_remote_free_guard = {
            "status": (
                "passed"
                if features.get("remote_db") is not None
                and safe_float(features.get("remote_db"))
                <= safe_float(self.progressive.REMOTE_AUDIO_QUIET_MAX_DB, -65.0)
                else "rejected"
            ),
            "remote_db": features.get("remote_db"),
            "remote_quiet_max_db": self.progressive.REMOTE_AUDIO_QUIET_MAX_DB,
            "used_batch_fields_for_selection": False,
        }
        existing = covered_by_existing_me(
            {"start": source_start, "end": source_end, "text": text},
            (
                existing_me_through_chunk(self.existing_me, chunk_index)
                if self.causal_existing_cutoff
                else self.existing_me
            )
            + self.accepted,
            self.progressive,
        )
        reasons: list[str] = []
        if result.get("status") != "passed":
            reasons.append(str(result.get("reason") or "micro_asr_failed"))
        if not selected_rows or not text:
            reasons.append("no_micro_asr_rows_inside_causal_island")
        if len(text_tokens) < 2:
            reasons.append("micro_asr_text_too_short")
        if score < MIN_MICRO_ASR_SCORE:
            reasons.append("low_micro_asr_score")
        if source_alignment < MIN_SOURCE_ALIGNMENT:
            reasons.append("low_live_source_alignment")
        if remote_guard.get("status") != "passed":
            reasons.append("remote_audio_guard_failed")
        if strict_remote_free_guard.get("status") != "passed":
            reasons.append("strict_remote_free_audio_guard_failed")
        if remote_similarity > MAX_REMOTE_SIMILARITY or (remote_recall > 0.10 and remote_matches > 1):
            reasons.append("remote_text_similarity_guard")
        if existing is not None:
            reasons.append("already_published_or_duplicate_candidate")
        candidate = {
            "schema": SCHEMA,
            "created_at": now_iso(),
            "kind": "causal_local_island_micro_asr_candidate",
            "id": item_id,
            "session": self.session.name,
            "chunk_index": chunk_index,
            "group_index": group_index,
            "start": round(source_start, 3),
            "end": round(source_end, 3),
            "duration_sec": round(max(0.0, source_end - source_start), 3),
            "source_start": round(source_start, 3),
            "source_end": round(source_end, 3),
            "source_text": source_text,
            "remote_text": remote_text,
            "text": text,
            "status": "accepted" if not reasons else "rejected",
            "outcome": "accepted" if not reasons else "rejected",
            "reasons": reasons,
            "score": round(score, 6),
            "source_alignment": round(source_alignment, 6),
            "remote_similarity": round(remote_similarity, 6),
            "remote_text_recall_in_micro": round(remote_recall, 6),
            "remote_text_matched_token_count": remote_matches,
            "source_segment_count": len(group),
            "source_evaluation_ids": [
                (row.get("strict_selection") or {}).get("id") for row in group
            ],
            "speaker_evidence": [
                {
                    "scores": row.get("scores") or {},
                    "thresholds": row.get("thresholds") or {},
                    "enrollment": row.get("enrollment") or {},
                }
                for row in group
            ],
            "recording_time_evidence": (group[0].get("strict_selection") or {}).get("recording_time_evidence"),
            "remote_audio_guard": remote_guard,
            "strict_remote_free_guard": strict_remote_free_guard,
            "remote_asr_guard": {
                "status": "passed",
                "guard_sec": REMOTE_GUARD_SEC,
                "overlap_count": 0,
            },
            "existing_live_turn": existing,
            "wav": str(wav),
            "asr_json": result.get("json"),
            "cache_hit": result.get("cache_hit") is True,
            "extraction_start": round(extraction_start, 3),
            "extraction_end": round(extraction_end, 3),
            "selection_local_start_sec": round(selection_start, 3),
            "selection_local_end_sec": round(selection_end, 3),
            "selected_asr_row_count": len(selected_rows),
            "used_batch_fields_for_selection": False,
            "timeline_causal": True,
            "selection_mode": "recording_time_causal_local_island_v2",
            "publication_allowed": False,
            "promotion_allowed": False,
            "batch_authoritative": True,
        }
        if candidate["status"] == "accepted":
            self.accepted.append(candidate)
        return candidate


def render_markdown(state: dict[str, Any], candidates: list[dict[str, Any]]) -> str:
    lines = [
        "# Causal Local-Island Micro-ASR v2",
        "",
        f"Status: `{state.get('status')}`",
        f"Selected strict islands: `{state.get('selected_island_count')}`",
        f"Micro-ASR groups: `{state.get('candidate_count')}`",
        f"Accepted: `{state.get('accepted_candidate_count')}` / `{state.get('accepted_candidate_seconds')}s`",
        "Batch authoritative: `true`",
        "Promotion allowed: `false`",
        "",
        "## Outcomes",
        "",
    ]
    for row in candidates:
        lines.append(
            f"- `{row.get('status')}` `{row.get('start')}..{row.get('end')}s` "
            f"chunk `{row.get('chunk_index')}`: {clean_text(row.get('text')) or '(no text)'}"
        )
        if row.get("reasons"):
            lines.append(f"  reasons: `{', '.join(str(value) for value in row.get('reasons') or [])}`")
    lines.extend(
        [
            "",
            "Candidates were selected without batch text or batch timing. Batch may evaluate this shadow later.",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Materialize bounded causal micro-ASR only for recording-time remote-free local islands."
    )
    parser.add_argument("session", type=Path)
    parser.add_argument("--baseline-profile", default=BASELINE_PROFILE)
    parser.add_argument(
        "--model",
        default=str(Path.home() / ".local/share/murmurmark/models/whisper.cpp/ggml-large-v3-q5_0.bin"),
    )
    parser.add_argument("--language", default="ru")
    parser.add_argument("--whisper-cli", default=os.environ.get("WHISPER_CLI", "whisper-cli"))
    parser.add_argument("--max-groups", type=int, default=120)
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--through-chunk-index",
        type=int,
        default=0,
        help="Recording-time cutoff. Zero keeps the historical full replay behavior.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Optional isolated output directory used by the recording-time runtime profile.",
    )
    parser.add_argument(
        "--existing-me-json",
        type=Path,
        help="Optional {turns:[...]} live-only baseline used for causal duplicate checks.",
    )
    parser.add_argument(
        "--runtime-shadow",
        action="store_true",
        help="Mark this materialization as explicit-only recording-time runtime evidence.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    session = args.session.expanduser().resolve()
    progressive = load_progressive_module()
    evaluations = read_jsonl(session / "derived/live/causal-target-me/evaluations.jsonl")
    if args.through_chunk_index > 0:
        evaluations = [
            row
            for row in evaluations
            if safe_int(row.get("chunk_index")) <= args.through_chunk_index
        ]
    chunk_paths = sorted((session / "derived/live/chunks").glob("*/chunk.json"))
    chunks = {
        safe_int(row.get("index")): row
        for row in (read_json(path) for path in chunk_paths)
        if row
        and (
            args.through_chunk_index <= 0
            or safe_int(row.get("index")) <= args.through_chunk_index
        )
    }
    segment_rows = read_jsonl(session / "derived/live/segments.jsonl")
    if args.through_chunk_index > 0:
        segment_rows = [
            row
            for row in segment_rows
            if safe_int(row.get("index")) <= args.through_chunk_index
        ]
    segments_by_key = {
        (str(row.get("source") or ""), safe_int(row.get("index"))): row
        for row in segment_rows
        if row.get("source") in {"mic", "remote"}
    }
    baseline_path = session / "derived/live/target-me-shadow" / args.baseline_profile / "draft.json"
    baseline = (
        read_json(args.existing_me_json.expanduser().resolve())
        if args.existing_me_json
        else read_json(baseline_path)
    )
    existing_me = [
        row
        for row in baseline.get("turns") or []
        if isinstance(row, dict) and row.get("role") == "Me"
    ]
    selected, decisions = select_strict_evaluations(
        session=session,
        evaluations=evaluations,
        chunks=chunks,
        segments_by_key=segments_by_key,
        existing_me=existing_me,
        progressive=progressive,
        causal_existing_cutoff=args.runtime_shadow,
    )
    groups = group_selected_evaluations(selected)
    if args.max_groups > 0:
        groups = groups[: args.max_groups]
    materializer = MicroASRMaterializer(
        session=session,
        chunks=chunks,
        existing_me=existing_me,
        progressive=progressive,
        model=args.model,
        language=args.language,
        whisper_cli=args.whisper_cli,
        force=args.force,
        causal_existing_cutoff=args.runtime_shadow,
    )
    output = (
        args.output_dir.expanduser().resolve()
        if args.output_dir
        else session / OUTPUT_RELATIVE
    )
    materializer.output = output
    candidates = [
        materializer.materialize(group, index)
        for index, group in enumerate(groups, start=1)
    ]
    accepted = [row for row in candidates if row.get("status") == "accepted"]
    try:
        output_relative = output.relative_to(session)
    except ValueError:
        output_relative = output
    state = {
        "schema": SCHEMA,
        "generator": {"name": "live-causal-local-island-micro-asr", "version": SCRIPT_VERSION},
        "created_at": now_iso(),
        "status": "completed",
        "session": session.name,
        "baseline_profile": args.baseline_profile,
        "selection_mode": "recording_time_causal_local_island_v2",
        "runtime_shadow": args.runtime_shadow,
        "through_chunk_index": args.through_chunk_index or None,
        "evaluated_segment_count": len(evaluations),
        "selected_island_count": len(selected),
        "candidate_count": len(candidates),
        "accepted_candidate_count": len(accepted),
        "accepted_candidate_seconds": round(sum(safe_float(row.get("duration_sec")) for row in accepted), 3),
        "rejected_candidate_count": len(candidates) - len(accepted),
        "used_batch_fields_for_selection": False,
        "timeline_causal": True,
        "batch_authoritative": True,
        "promotion_allowed": False,
        "thresholds": {
            "remote_guard_sec": REMOTE_GUARD_SEC,
            "group_gap_sec": GROUP_GAP_SEC,
            "min_island_sec": MIN_ISLAND_SEC,
            "max_island_sec": MAX_ISLAND_SEC,
            "min_micro_asr_score": MIN_MICRO_ASR_SCORE,
            "min_source_alignment": MIN_SOURCE_ALIGNMENT,
            "max_remote_similarity": MAX_REMOTE_SIMILARITY,
            "min_source_token_count": MIN_SOURCE_TOKEN_COUNT,
            "strict_remote_quiet_max_db": progressive.REMOTE_AUDIO_QUIET_MAX_DB,
        },
        "outputs": {
            "selection": str(output_relative / "selection.jsonl"),
            "candidates": str(output_relative / "candidates.jsonl"),
            "report": str(output_relative / "report.md"),
        },
    }
    write_jsonl(output / "selection.jsonl", decisions)
    write_jsonl(output / "candidates.jsonl", candidates)
    write_json(output / "state.json", state)
    (output / "report.md").write_text(render_markdown(state, candidates), encoding="utf-8")
    print(f"status: {state['status']}")
    print(f"selected_islands: {state['selected_island_count']}")
    print(f"candidates: {state['candidate_count']}")
    print(f"accepted: {state['accepted_candidate_count']}")
    print(f"accepted_seconds: {state['accepted_candidate_seconds']}")
    print(f"report: {output / 'state.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
