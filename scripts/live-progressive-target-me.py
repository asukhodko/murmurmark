#!/usr/bin/env python3
from __future__ import annotations

import argparse
import difflib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np
from scipy.io import wavfile


SCHEMA = "murmurmark.live_progressive_target_me/v1"
SCRIPT_VERSION = "0.5.0"
EPSILON = 1.0e-12
TOKEN_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9_+-]+")
KNOWN_HALLUCINATIONS = {
    "редактор субтитров",
    "продолжение следует",
    "спасибо за просмотр",
}
MicroRunner = Callable[[Path, Path, str, str, str], dict[str, Any]]


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


def tokens(value: Any) -> list[str]:
    return [match.group(0).casefold() for match in TOKEN_RE.finditer(clean_text(value))]


def is_hallucination(value: Any) -> bool:
    normalized = clean_text(value).casefold().strip(".!?, ")
    return normalized in KNOWN_HALLUCINATIONS or normalized.startswith("субтитры")


def bag_recall(source: list[str], target: list[str]) -> float:
    if not source:
        return 0.0
    remaining: dict[str, int] = {}
    for token in target:
        remaining[token] = remaining.get(token, 0) + 1
    matched = 0
    for token in source:
        if remaining.get(token, 0) > 0:
            matched += 1
            remaining[token] -= 1
    return matched / len(source)


def bag_match_count(source: list[str], target: list[str]) -> int:
    remaining: dict[str, int] = {}
    for token in target:
        remaining[token] = remaining.get(token, 0) + 1
    matched = 0
    for token in source:
        if remaining.get(token, 0) > 0:
            matched += 1
            remaining[token] -= 1
    return matched


def token_related(left: str, right: str) -> bool:
    if left == right:
        return True
    if min(len(left), len(right)) < 5:
        return False
    return len(os.path.commonprefix([left, right])) >= 5


def trim_remote_context_prefix(text: str, remote_text: str) -> tuple[str, dict[str, Any]]:
    matches = list(TOKEN_RE.finditer(text))
    remote_tokens = tokens(remote_text)
    if len(matches) < 4 or not remote_tokens:
        return text, {"status": "not_applied", "reason": "insufficient_tokens"}
    max_prefix = min(6, len(matches) - 3)
    selected = 0
    for prefix_length in range(1, max_prefix + 1):
        prefix = [match.group(0).casefold() for match in matches[:prefix_length]]
        related = [
            token
            for token in prefix
            if any(token_related(token, remote_token) for remote_token in remote_tokens)
        ]
        coverage = len(related) / prefix_length
        has_long_related = any(len(token) >= 5 for token in related)
        strong_single = prefix_length == 1 and len(prefix[0]) >= 5 and coverage == 1.0
        strong_multi = prefix_length >= 2 and coverage >= 0.75 and has_long_related
        if strong_single or strong_multi:
            selected = prefix_length
    if selected == 0:
        return text, {"status": "not_applied", "reason": "no_remote_like_prefix"}
    trimmed = text[matches[selected - 1].end() :].lstrip(" \t,.;:!?-—")
    if len(tokens(trimmed)) < 3:
        return text, {"status": "not_applied", "reason": "trim_would_leave_too_little_text"}
    return clean_text(trimmed), {
        "status": "applied",
        "reason": "remote_context_prefix_before_confirmed_local_gap",
        "removed_token_count": selected,
        "removed_text": clean_text(text[: matches[selected - 1].end()]),
        "original_text": text,
        "trimmed_text": clean_text(trimmed),
    }


def text_similarity(left: Any, right: Any) -> float:
    left_tokens = tokens(left)
    right_tokens = tokens(right)
    return max(bag_recall(left_tokens, right_tokens), bag_recall(right_tokens, left_tokens))


def asr_text_similarity(left: Any, right: Any) -> float:
    left_norm = " ".join(tokens(left))
    right_norm = " ".join(tokens(right))
    if not left_norm or not right_norm:
        return 0.0

    def ngrams(value: str, size: int = 3) -> set[str]:
        compact = value.replace(" ", "")
        if len(compact) <= size:
            return {compact} if compact else set()
        return {compact[index : index + size] for index in range(len(compact) - size + 1)}

    def jaccard(left_values: set[str], right_values: set[str]) -> float:
        return len(left_values & right_values) / len(left_values | right_values) if left_values and right_values else 0.0

    return max(
        difflib.SequenceMatcher(None, left_norm, right_norm).ratio(),
        jaccard(set(left_norm.split()), set(right_norm.split())),
        jaccard(ngrams(left_norm), ngrams(right_norm)),
    )


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def rewrite_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def read_asr_segments(source: dict[str, Any], session: Path | None = None) -> list[dict[str, Any]]:
    asr = source.get("asr") if isinstance(source.get("asr"), dict) else {}
    json_path = Path(str(asr.get("json"))) if asr.get("json") else None
    if json_path is not None and not json_path.is_absolute() and session is not None:
        json_path = session / json_path
    if json_path is None or not json_path.exists():
        return []
    data = read_json(json_path) or {}
    clip_start = safe_float(source.get("clip_start_sec"))
    hard_start = safe_float(source.get("hard_start_sec"))
    hard_end = safe_float(source.get("hard_end_sec"), float("inf"))
    result: list[dict[str, Any]] = []
    for row in data.get("transcription") or []:
        if not isinstance(row, dict):
            continue
        offsets = row.get("offsets") if isinstance(row.get("offsets"), dict) else {}
        start = clip_start + safe_float(offsets.get("from")) / 1000.0
        end = clip_start + safe_float(offsets.get("to"), safe_float(offsets.get("from"))) / 1000.0
        center = (start + end) / 2.0
        text = clean_text(row.get("text"))
        if not text or is_hallucination(text) or not (hard_start <= center < hard_end):
            continue
        result.append(
            {
                "start": round(start, 3),
                "end": round(max(start, end), 3),
                "duration_sec": round(max(0.0, end - start), 3),
                "text": text,
                "tokens": tokens(text),
            }
        )
    return result


def resolve_audio(session: Path, source: dict[str, Any], *, mic: bool) -> tuple[Path | None, float]:
    value = source.get("asr_wav") if mic else source.get("wav")
    value = value or source.get("wav") or source.get("input")
    if not value:
        return None, 0.0
    path = Path(str(value))
    if not path.is_absolute():
        path = session / path
    if not path.exists():
        return None, 0.0
    return path, safe_float(source.get("clip_start_sec"))


def read_wav_float(path: Path) -> tuple[int, np.ndarray]:
    rate, data = wavfile.read(path)
    if data.ndim > 1:
        data = data.mean(axis=1)
    if np.issubdtype(data.dtype, np.integer):
        info = np.iinfo(data.dtype)
        data = data.astype(np.float32) / float(max(abs(info.min), info.max))
    else:
        data = data.astype(np.float32)
    return int(rate), np.nan_to_num(data)


def write_wav_float(path: Path, rate: int, data: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    wavfile.write(path, rate, np.clip(data, -1.0, 1.0).astype(np.float32))


def rms_db(data: np.ndarray) -> float:
    if data.size == 0:
        return -120.0
    rms = float(np.sqrt(np.mean(np.square(data), dtype=np.float64) + EPSILON))
    return 20.0 * np.log10(rms + EPSILON)


def zero_lag_abs_corr(left: np.ndarray, right: np.ndarray) -> float | None:
    count = min(left.size, right.size)
    if count < 160:
        return None
    a = left[:count].astype(np.float64)
    b = right[:count].astype(np.float64)
    a -= float(np.mean(a))
    b -= float(np.mean(b))
    denominator = float(np.sqrt(np.sum(a * a) * np.sum(b * b)))
    if denominator <= EPSILON:
        return None
    return abs(float(np.sum(a * b) / denominator))


def make_centroid(vectors: list[np.ndarray]) -> np.ndarray | None:
    if not vectors:
        return None
    centroid = np.median(np.vstack(vectors), axis=0)
    norm = float(np.linalg.norm(centroid))
    return centroid / norm if norm > EPSILON else None


def cosine(left: np.ndarray | None, right: np.ndarray | None) -> float:
    if left is None or right is None:
        return 0.0
    return float(np.dot(left, right) / (np.linalg.norm(left) * np.linalg.norm(right) + EPSILON))


def percentile(values: list[float], q: float) -> float:
    return float(np.percentile(np.asarray(values, dtype=np.float64), q)) if values else 0.0


def token_average_probability(path: Path | None) -> float:
    data = read_json(path) if path else None
    values: list[float] = []
    for row in (data or {}).get("transcription") or []:
        if not isinstance(row, dict):
            continue
        for token in row.get("tokens") or []:
            if not isinstance(token, dict):
                continue
            probability = token.get("p")
            if isinstance(probability, (int, float)) and probability >= 0:
                values.append(float(probability))
    return float(np.mean(values)) if values else 0.0


def asr_rows(path: Path | None) -> list[dict[str, Any]]:
    data = read_json(path) if path else None
    result: list[dict[str, Any]] = []
    for row in (data or {}).get("transcription") or []:
        if not isinstance(row, dict):
            continue
        offsets = row.get("offsets") if isinstance(row.get("offsets"), dict) else {}
        start = safe_float(offsets.get("from")) / 1000.0
        end = safe_float(offsets.get("to"), safe_float(offsets.get("from"))) / 1000.0
        text = clean_text(row.get("text"))
        probabilities = [
            safe_float(token.get("p"))
            for token in row.get("tokens") or []
            if isinstance(token, dict) and isinstance(token.get("p"), (int, float))
        ]
        if text and not is_hallucination(text):
            result.append(
                {
                    "start_sec": round(start, 3),
                    "end_sec": round(max(start, end), 3),
                    "text": text,
                    "score": round(float(np.mean(probabilities)), 6) if probabilities else 0.0,
                }
            )
    return result


def default_micro_runner(
    wav: Path,
    output_base: Path,
    model: str,
    language: str,
    whisper_cli: str,
) -> dict[str, Any]:
    if not Path(model).expanduser().exists():
        return {"status": "skipped", "reason": "model_missing", "text": "", "score": 0.0}
    executable = shutil.which(whisper_cli) or (whisper_cli if Path(whisper_cli).exists() else None)
    if not executable:
        return {"status": "skipped", "reason": "whisper_cli_missing", "text": "", "score": 0.0}
    output_base.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            executable,
            "--model", str(Path(model).expanduser()),
            "--language", language,
            "--threads", "4",
            "--max-context", "0",
            "--output-txt",
            "--output-json",
            "--output-json-full",
            "--output-file", str(output_base),
            "--no-prints",
            "--log-score",
            "--suppress-nst",
            "--suppress-regex", "^(Редактор субтитров|Продолжение следует|Спасибо за просмотр|Субтитры.*)$",
            "--file", str(wav),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    txt_path = output_base.with_suffix(".txt")
    json_path = output_base.with_suffix(".json")
    text = clean_text(txt_path.read_text(encoding="utf-8", errors="ignore")) if txt_path.exists() else ""
    if is_hallucination(text):
        text = ""
    return {
        "status": "passed" if result.returncode == 0 else "failed",
        "text": text,
        "score": round(token_average_probability(json_path if json_path.exists() else None), 6),
        "json": str(json_path) if json_path.exists() else None,
        "rows": asr_rows(json_path if json_path.exists() else None),
        "stderr_tail": result.stderr[-1000:] if result.returncode else "",
    }


def load_default_backend() -> tuple[Any | None, dict[str, Any]]:
    path = Path(__file__).with_name("audit-target-me.py")
    try:
        spec = importlib.util.spec_from_file_location("murmurmark_live_progressive_target_me_backend", path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"cannot import {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        backend = module.ResemblyzerDVectorBackend()
        ready, reason = backend.ready()
        if not ready:
            return None, {"selected": backend.method, "ready": False, "reason": reason}
        return backend, {"selected": backend.method, "ready": True, "reason": "ok"}
    except Exception as error:
        return None, {"selected": "none", "ready": False, "reason": str(error)}


def load_replay_segment_gate() -> Callable[[Path, dict[str, Any]], dict[str, Any]] | None:
    """Load the current segment gate only for replaying historical chunk artifacts."""
    path = Path(__file__).with_name("live-pipeline-shadow.py")
    try:
        spec = importlib.util.spec_from_file_location("murmurmark_live_progressive_replay_gate", path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        gate = getattr(module, "segment_level_role_rescue", None)
        return gate if callable(gate) else None
    except Exception:
        return None


class ProgressiveTargetMeShadow:
    def __init__(
        self,
        session: Path,
        *,
        model: str,
        language: str,
        whisper_cli: str,
        backend: Any | None = None,
        micro_runner: MicroRunner | None = None,
        max_seeds: int = 48,
    ) -> None:
        self.session = session.resolve()
        self.model_path = model
        self.language = language
        self.whisper_cli = whisper_cli
        self.max_seeds = max(6, max_seeds)
        if backend is None:
            self.backend, self.backend_status = load_default_backend()
        else:
            self.backend = backend
            self.backend_status = {"selected": getattr(backend, "method", "injected"), "ready": True, "reason": "injected"}
        self.micro_runner = micro_runner or default_micro_runner
        self.positive_seeds: list[tuple[dict[str, Any], np.ndarray]] = []
        self.negative_seeds: list[tuple[dict[str, Any], np.ndarray]] = []
        self.enrollment_rows: list[dict[str, Any]] = []
        self.evaluations: list[dict[str, Any]] = []
        self.candidates: list[dict[str, Any]] = []
        self.audio_cache: dict[str, tuple[int, np.ndarray]] = {}
        self.out_dir = self.session / "derived/live/causal-target-me"
        self.persist(status="ready" if self.backend else "skipped_backend_unavailable")

    def audio(self, path: Path | None) -> tuple[int | None, np.ndarray | None]:
        if path is None:
            return None, None
        key = str(path)
        if key not in self.audio_cache:
            try:
                self.audio_cache[key] = read_wav_float(path)
            except (OSError, ValueError):
                return None, None
        return self.audio_cache[key]

    def audio_slice(
        self,
        source: dict[str, Any],
        *,
        mic: bool,
        start: float,
        end: float,
    ) -> tuple[int | None, np.ndarray | None, Path | None]:
        path, clip_start = resolve_audio(self.session, source, mic=mic)
        rate, data = self.audio(path)
        if rate is None or data is None:
            return None, None, path
        local_start = max(0, int(round((start - clip_start) * rate)))
        local_end = min(data.size, int(round((end - clip_start) * rate)))
        if local_end <= local_start:
            return rate, np.asarray([], dtype=np.float32), path
        return rate, data[local_start:local_end], path

    def write_clip(
        self,
        source: dict[str, Any],
        *,
        mic: bool,
        start: float,
        end: float,
        path: Path,
        leading_silence_sec: float = 0.0,
    ) -> bool:
        rate, data, _ = self.audio_slice(source, mic=mic, start=start, end=end)
        if rate is None or data is None or data.size < max(160, int(0.25 * rate)):
            return False
        if leading_silence_sec > 0:
            data = np.concatenate([np.zeros(int(round(rate * leading_silence_sec)), dtype=np.float32), data])
        write_wav_float(path, rate, data)
        return True

    def overlapping_remote(self, remote_segments: list[dict[str, Any]], start: float, end: float) -> list[dict[str, Any]]:
        return [
            row
            for row in remote_segments
            if min(end + 1.0, safe_float(row.get("end"))) - max(start - 1.0, safe_float(row.get("start"))) > 0
        ]

    def segment_audio_features(
        self,
        mic: dict[str, Any],
        remote: dict[str, Any],
        start: float,
        end: float,
    ) -> dict[str, Any]:
        mic_rate, mic_audio, _ = self.audio_slice(mic, mic=True, start=start, end=end)
        remote_rate, remote_audio, _ = self.audio_slice(remote, mic=False, start=start, end=end)
        if mic_rate is None or remote_rate != mic_rate or mic_audio is None or remote_audio is None:
            return {"mic_db": None, "remote_db": None, "mic_minus_remote_db": None, "corr": None}
        count = min(mic_audio.size, remote_audio.size)
        mic_audio = mic_audio[:count]
        remote_audio = remote_audio[:count]
        mic_db = rms_db(mic_audio)
        remote_db = rms_db(remote_audio)
        correlation = zero_lag_abs_corr(mic_audio, remote_audio)
        return {
            "mic_db": round(mic_db, 3),
            "remote_db": round(remote_db, 3),
            "mic_minus_remote_db": round(mic_db - remote_db, 3),
            "corr": round(correlation, 6) if correlation is not None else None,
        }

    def embedding_for(
        self,
        source: dict[str, Any],
        *,
        mic: bool,
        start: float,
        end: float,
        label: str,
        chunk_index: int,
    ) -> tuple[np.ndarray | None, dict[str, Any], Path]:
        clip = self.out_dir / "clips" / label / f"{chunk_index:06d}_{start:.3f}_{end:.3f}.wav"
        if not self.write_clip(source, mic=mic, start=max(0.0, start - 0.1), end=end + 0.1, path=clip):
            return None, {"error": "clip_extract_failed"}, clip
        if self.backend is None:
            return None, {"error": "backend_unavailable"}, clip
        embedding, info = self.backend.embed(clip)
        return embedding, info, clip

    def model(self) -> tuple[dict[str, Any] | None, dict[str, float] | None]:
        positives = [vector for _, vector in self.positive_seeds]
        negatives = [vector for _, vector in self.negative_seeds]
        if len(positives) < 3 or len(negatives) < 3:
            return None, None
        positive_centroid = make_centroid(positives)
        negative_centroid = make_centroid(negatives)
        model = {"positive": positive_centroid, "negative": negative_centroid}

        def score(vector: np.ndarray) -> dict[str, float]:
            positive = cosine(vector, positive_centroid)
            negative = cosine(vector, negative_centroid)
            return {"positive": positive, "negative": negative, "target": positive - negative}

        positive_scores = [score(vector) for vector in positives]
        negative_scores = [score(vector) for vector in negatives]
        thresholds = {
            "target": (percentile([row["target"] for row in positive_scores], 10) + percentile([row["target"] for row in negative_scores], 90)) / 2.0,
            "positive": max(0.0, percentile([row["positive"] for row in positive_scores], 10) - 0.05),
            "negative_max": percentile([row["positive"] for row in negative_scores], 90) + 0.05,
        }
        return model, thresholds

    def score(self, vector: np.ndarray, model: dict[str, Any]) -> dict[str, float]:
        positive = cosine(vector, model.get("positive"))
        negative = cosine(vector, model.get("negative"))
        return {"target": positive - negative, "positive": positive, "negative": negative}

    def evaluate_segments(
        self,
        chunk_index: int,
        mic: dict[str, Any],
        remote: dict[str, Any],
        mic_segments: list[dict[str, Any]],
        remote_segments: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        model, thresholds = self.model()
        accepted: list[dict[str, Any]] = []
        chunk_gate = mic.get("live_role_gate") if isinstance(mic.get("live_role_gate"), dict) else {}
        segment_gate = mic.get("live_segment_role_gate") if isinstance(mic.get("live_segment_role_gate"), dict) else {}
        gate_rows: list[tuple[str, dict[str, Any]]] = []
        for status, key in (("kept", "kept_segments"), ("suppressed", "suppressed_segments")):
            for gate_row in segment_gate.get(key) or []:
                if isinstance(gate_row, dict):
                    gate_rows.append((status, gate_row))
        for segment_index, row in enumerate(mic_segments, start=1):
            start = safe_float(row.get("start"))
            end = safe_float(row.get("end"), start)
            remote_rows = self.overlapping_remote(remote_segments, start, end)
            remote_text = clean_text(" ".join(str(item.get("text") or "") for item in remote_rows))
            remote_similarity = text_similarity(row.get("text"), remote_text) if remote_text else 0.0
            source_text = clean_text(row.get("text"))
            features = self.segment_audio_features(mic, remote, start, end)
            segment_gate_status = next(
                (
                    status
                    for status, gate_row in gate_rows
                    if abs(safe_float(gate_row.get("start")) - start) <= 0.05
                    and abs(safe_float(gate_row.get("end")) - end) <= 0.05
                ),
                "unknown",
            )
            evaluation: dict[str, Any] = {
                "schema": SCHEMA,
                "created_at": now_iso(),
                "kind": "segment_evaluation",
                "chunk_index": chunk_index,
                "segment_index": segment_index,
                "start": round(start, 3),
                "end": round(end, 3),
                "duration_sec": round(max(0.0, end - start), 3),
                "text": source_text,
                "remote_text": remote_text,
                "used_batch_fields_for_selection": False,
                "timeline_causal": True,
                "enrollment": {
                    "mode": "past_chunks_only",
                    "positive_seed_count": len(self.positive_seeds),
                    "negative_seed_count": len(self.negative_seeds),
                    "cutoff_sec": round(start, 3),
                },
                "audio": features,
                "chunk_role_gate_status": chunk_gate.get("status"),
                "segment_gate_status": segment_gate_status,
            }
            reasons: list[str] = []
            if model is None or thresholds is None:
                reasons.append("insufficient_past_enrollment")
            if len(tokens(source_text)) < 2 or end - start < 0.5 or end - start > 12.0:
                reasons.append("unsupported_segment_shape")
            if text_similarity(source_text, remote_text) > 0.75:
                reasons.append("live_text_too_close_to_remote")
            corr = features.get("corr")
            if corr is not None and safe_float(corr) > 0.55:
                reasons.append("audio_too_correlated_with_remote")
            embedding = None
            info: dict[str, Any] = {}
            clip: Path | None = None
            if not reasons or reasons == ["insufficient_past_enrollment"]:
                embedding, info, clip = self.embedding_for(
                    mic,
                    mic=True,
                    start=start,
                    end=end,
                    label="candidate",
                    chunk_index=chunk_index,
                )
                if embedding is None:
                    reasons.append(info.get("error") or "embedding_failed")
            scores: dict[str, float] = {}
            if embedding is not None and model is not None and thresholds is not None:
                scores = self.score(embedding, model)
                if scores["target"] < thresholds["target"]:
                    reasons.append("below_target_similarity_threshold")
                if scores["positive"] < thresholds["positive"]:
                    reasons.append("below_positive_similarity_threshold")
                if scores["negative"] > thresholds["negative_max"]:
                    reasons.append("too_close_to_remote_negative")
            evaluation["embedding_info"] = info
            evaluation["clip"] = str(clip) if clip else None
            evaluation["scores"] = {key: round(value, 6) for key, value in scores.items()}
            evaluation["thresholds"] = {key: round(value, 6) for key, value in (thresholds or {}).items()}
            evaluation["reasons"] = reasons
            evaluation["classification"] = "causal_target_me_supported" if not reasons else "not_supported"
            self.evaluations.append(evaluation)
            if not reasons:
                accepted.append(evaluation)
        return accepted

    def group_segments(self, rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        groups: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] = []
        for row in sorted(rows, key=lambda item: safe_float(item.get("start"))):
            if not current:
                current = [row]
                continue
            gap = safe_float(row.get("start")) - safe_float(current[-1].get("end"))
            duration = safe_float(row.get("end")) - safe_float(current[0].get("start"))
            if gap <= 0.75 and duration <= 12.0:
                current.append(row)
            else:
                groups.append(current)
                current = [row]
        if current:
            groups.append(current)
        # Progressive speaker evidence is a recovery path. The chunk-level
        # duplicate gate currently suppresses the whole mic chunk even when
        # its segment gate found a safe local island. Recover groups only from
        # such unpublished chunks; passed chunks already have a published Me
        # turn and a second wider interval can damage timeline order.
        groups = [
            group
            for group in groups
            if any(row.get("chunk_role_gate_status") == "suppressed" for row in group)
        ]
        groups.sort(
            key=lambda group: (
                -sum(1 for row in group if row.get("segment_gate_status") == "suppressed"),
                -(safe_float(group[-1].get("end")) - safe_float(group[0].get("start"))),
                safe_float(group[0].get("start")),
            )
        )
        return groups[:2]

    def localize_remote_free_interval(
        self,
        *,
        chunk_index: int,
        group_index: int,
        mic: dict[str, Any],
        remote_segments: list[dict[str, Any]],
        start: float,
        end: float,
    ) -> tuple[float, float, dict[str, Any]]:
        overlaps = sorted(
            (
                max(start, safe_float(row.get("start"))),
                min(end, safe_float(row.get("end"), safe_float(row.get("start")))),
            )
            for row in remote_segments
            if min(end, safe_float(row.get("end"))) - max(start, safe_float(row.get("start"))) > 0
        )
        if not overlaps:
            return start, end, {"status": "not_needed", "reason": "no_remote_overlap"}

        guard_sec = 0.10
        merged: list[tuple[float, float]] = []
        for overlap_start, overlap_end in overlaps:
            overlap_start = max(start, overlap_start - guard_sec)
            overlap_end = min(end, overlap_end + guard_sec)
            if merged and overlap_start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], overlap_end))
            else:
                merged.append((overlap_start, overlap_end))
        gaps: list[tuple[float, float]] = []
        cursor = start
        for overlap_start, overlap_end in merged:
            if overlap_start - cursor >= 0.60:
                gaps.append((cursor, overlap_start))
            cursor = max(cursor, overlap_end)
        if end - cursor >= 0.60:
            gaps.append((cursor, end))

        model, thresholds = self.model()
        def score_interval(
            interval_start: float,
            interval_end: float,
            *,
            label: str,
        ) -> dict[str, Any]:
            vector, info, clip = self.embedding_for(
                mic,
                mic=True,
                start=interval_start,
                end=interval_end,
                label=label,
                chunk_index=chunk_index,
            )
            scores = self.score(vector, model) if vector is not None and model is not None else {}
            accepted = bool(
                vector is not None
                and thresholds is not None
                and scores.get("target", -1.0) >= thresholds["target"]
                and scores.get("positive", -1.0) >= thresholds["positive"]
                and scores.get("negative", 2.0) <= thresholds["negative_max"]
            )
            return {
                "start": round(interval_start, 3),
                "end": round(interval_end, 3),
                "duration_sec": round(interval_end - interval_start, 3),
                "accepted": accepted,
                "scores": {key: round(value, 6) for key, value in scores.items()},
                "embedding_info": info,
                "clip": str(clip),
            }

        scored = [
            score_interval(
                gap_start,
                gap_end,
                label=f"boundary_{group_index:02d}_{gap_index:02d}",
            )
            for gap_index, (gap_start, gap_end) in enumerate(gaps, start=1)
        ]
        accepted_gaps = [row for row in scored if row.get("accepted")]
        if accepted_gaps:
            selected = max(
                accepted_gaps,
                key=lambda row: (
                    safe_float((row.get("scores") or {}).get("target"), -1.0),
                    safe_float(row.get("duration_sec")),
                ),
            )
            return safe_float(selected.get("start")), safe_float(selected.get("end")), {
                "status": "localized",
                "reason": "past_target_voice_in_remote_free_gap",
                "remote_intervals": [[round(left, 3), round(right, 3)] for left, right in merged],
                "gaps": scored,
                "selected": selected,
            }

        duration = end - start
        window_sec = min(2.0, duration)
        starts: list[float] = []
        cursor = start
        while window_sec >= 0.80 and cursor + window_sec <= end + 1.0e-6:
            starts.append(cursor)
            cursor += 1.0
        final_start = end - window_sec
        if window_sec >= 0.80 and (not starts or final_start - starts[-1] >= 0.25):
            starts.append(final_start)
        sliding = [
            score_interval(
                window_start,
                min(end, window_start + window_sec),
                label=f"sliding_{group_index:02d}_{window_index:02d}",
            )
            for window_index, window_start in enumerate(starts, start=1)
        ]
        accepted_windows = [row for row in sliding if row.get("accepted")]
        clusters: list[dict[str, Any]] = []
        for row in accepted_windows:
            if clusters and safe_float(row.get("start")) <= safe_float(clusters[-1].get("end")) + 0.05:
                clusters[-1]["end"] = max(safe_float(clusters[-1].get("end")), safe_float(row.get("end")))
                clusters[-1]["windows"].append(row)
            else:
                clusters.append(
                    {
                        "start": safe_float(row.get("start")),
                        "end": safe_float(row.get("end")),
                        "windows": [row],
                    }
                )
        for cluster in clusters:
            cluster["duration_sec"] = round(safe_float(cluster.get("end")) - safe_float(cluster.get("start")), 3)
            cluster["mean_target_score"] = round(
                float(
                    np.mean(
                        [
                            safe_float((row.get("scores") or {}).get("target"))
                            for row in cluster.get("windows") or []
                        ]
                    )
                ),
                6,
            )
        if clusters:
            selected_cluster = max(
                clusters,
                key=lambda row: (safe_float(row.get("mean_target_score")), safe_float(row.get("duration_sec"))),
            )
            return safe_float(selected_cluster.get("start")), safe_float(selected_cluster.get("end")), {
                "status": "localized",
                "reason": "past_target_voice_sliding_window",
                "remote_intervals": [[round(left, 3), round(right, 3)] for left, right in merged],
                "gaps": scored,
                "sliding_windows": sliding,
                "selected": selected_cluster,
            }
        return start, end, {
            "status": "rejected",
            "reason": "no_target_supported_remote_free_or_sliding_window",
            "remote_intervals": [[round(left, 3), round(right, 3)] for left, right in merged],
            "gaps": scored,
            "sliding_windows": sliding,
        }

    def micro_candidate(
        self,
        chunk_index: int,
        group_index: int,
        group: list[dict[str, Any]],
        mic: dict[str, Any],
        remote_segments: list[dict[str, Any]],
    ) -> dict[str, Any]:
        source_start = safe_float(group[0].get("start"))
        source_end = safe_float(group[-1].get("end"), source_start)
        start, end, localization = self.localize_remote_free_interval(
            chunk_index=chunk_index,
            group_index=group_index,
            mic=mic,
            remote_segments=remote_segments,
            start=source_start,
            end=source_end,
        )
        source_text = clean_text(" ".join(str(row.get("text") or "") for row in group))
        remote_text = clean_text(" ".join(str(row.get("remote_text") or "") for row in group))
        wav = self.out_dir / "micro_asr" / f"chunk_{chunk_index:06d}_{group_index:02d}.wav"
        output_base = self.out_dir / "micro_asr" / f"chunk_{chunk_index:06d}_{group_index:02d}"
        extraction_start = max(0.0, start - 1.6)
        leading_silence_sec = 0.4
        extracted = self.write_clip(
            mic,
            mic=True,
            start=extraction_start,
            end=end + 1.0,
            path=wav,
            leading_silence_sec=leading_silence_sec,
        )
        result = (
            self.micro_runner(wav, output_base, self.model_path, self.language, self.whisper_cli)
            if extracted
            else {"status": "failed", "reason": "clip_extract_failed", "text": "", "score": 0.0}
        )
        result_rows = result.get("rows") if isinstance(result.get("rows"), list) else []
        selection_start = leading_silence_sec + (start - extraction_start)
        selection_end = leading_silence_sec + (end - extraction_start)
        selected_rows = [
            row
            for row in result_rows
            if isinstance(row, dict)
            and selection_start <= (safe_float(row.get("start_sec")) + safe_float(row.get("end_sec"))) / 2.0 <= selection_end
        ]
        text = clean_text(" ".join(str(row.get("text") or "") for row in selected_rows))
        if not text:
            text = clean_text(result.get("text"))
        prefix_trim: dict[str, Any] = {"status": "not_applied", "reason": "not_remote_free_gap"}
        if localization.get("reason") == "past_target_voice_in_remote_free_gap":
            text, prefix_trim = trim_remote_context_prefix(text, remote_text)
        selected_scores = [safe_float(row.get("score")) for row in selected_rows if safe_float(row.get("score")) > 0]
        score = float(np.mean(selected_scores)) if selected_scores else safe_float(result.get("score"))
        source_recall = bag_recall(tokens(source_text), tokens(text))
        micro_source_recall = bag_recall(tokens(text), tokens(source_text))
        remote_similarity = asr_text_similarity(text, remote_text) if remote_text else 0.0
        remote_tokens = tokens(remote_text)
        micro_tokens = tokens(text)
        remote_recall = bag_recall(remote_tokens, micro_tokens) if remote_text else 0.0
        remote_match_count = bag_match_count(remote_tokens, micro_tokens) if remote_text else 0
        reasons: list[str] = []
        if localization.get("status") == "rejected":
            reasons.append(str(localization.get("reason") or "remote_free_localization_failed"))
        if result.get("status") != "passed" or not text:
            reasons.append(result.get("reason") or "micro_asr_failed")
        if score < 0.68:
            reasons.append("low_micro_asr_score")
        if source_recall < 0.25 and micro_source_recall < 0.25:
            reasons.append("low_live_source_alignment")
        remote_free_small_overlap = bool(
            localization.get("reason") == "past_target_voice_in_remote_free_gap"
            and remote_match_count <= 2
        )
        if remote_similarity > 0.30 or (remote_recall > 0.10 and not remote_free_small_overlap):
            reasons.append("remote_similarity_guard")
        return {
            "schema": SCHEMA,
            "created_at": now_iso(),
            "kind": "micro_asr_candidate",
            "id": f"live_runtime_causal_target_me_{chunk_index:06d}_{group_index:02d}",
            "chunk_index": chunk_index,
            "start": round(start, 3),
            "end": round(end, 3),
            "duration_sec": round(max(0.0, end - start), 3),
            "source_start": round(source_start, 3),
            "source_end": round(source_end, 3),
            "source_text": source_text,
            "remote_text": remote_text,
            "text": text,
            "status": "accepted" if not reasons else "rejected",
            "reasons": reasons,
            "score": round(score, 6),
            "source_text_token_recall": round(source_recall, 6),
            "micro_text_token_recall_in_source": round(micro_source_recall, 6),
            "remote_similarity": round(remote_similarity, 6),
            "remote_text_recall_in_micro": round(remote_recall, 6),
            "remote_text_matched_token_count": remote_match_count,
            "remote_free_small_token_overlap_allowed": remote_free_small_overlap,
            "enrollment": group[0].get("enrollment"),
            "speaker_scores": group[0].get("scores"),
            "used_batch_fields_for_selection": False,
            "timeline_causal": True,
            "publication_allowed": False,
            "batch_authoritative": True,
            "wav": str(wav),
            "asr_json": result.get("json"),
            "selection_local_start_sec": round(selection_start, 3),
            "selection_local_end_sec": round(selection_end, 3),
            "selected_asr_row_count": len(selected_rows),
            "remote_free_localization": localization,
            "remote_context_prefix_trim": prefix_trim,
        }

    def add_seed(
        self,
        *,
        kind: str,
        chunk_index: int,
        row: dict[str, Any],
        source: dict[str, Any],
        mic: bool,
        evidence: dict[str, Any],
    ) -> None:
        start = safe_float(row.get("start"))
        end = safe_float(row.get("end"), start)
        embedding, info, clip = self.embedding_for(
            source,
            mic=mic,
            start=start,
            end=end,
            label=f"seed_{kind}",
            chunk_index=chunk_index,
        )
        item = {
            "schema": SCHEMA,
            "created_at": now_iso(),
            "kind": f"{kind}_seed",
            "chunk_index": chunk_index,
            "start": round(start, 3),
            "end": round(end, 3),
            "duration_sec": round(max(0.0, end - start), 3),
            "text": clean_text(row.get("text")),
            "accepted": embedding is not None,
            "clip": str(clip),
            "embedding_info": info,
            "evidence": evidence,
            "used_batch_fields_for_selection": False,
            "timeline_causal": True,
        }
        self.enrollment_rows.append(item)
        if embedding is None:
            return
        bucket = self.positive_seeds if kind == "positive" else self.negative_seeds
        bucket.append((item, embedding))
        if len(bucket) > self.max_seeds:
            del bucket[:-self.max_seeds]

    def enroll_after_chunk(
        self,
        chunk_index: int,
        mic: dict[str, Any],
        remote: dict[str, Any],
        mic_segments: list[dict[str, Any]],
        remote_segments: list[dict[str, Any]],
    ) -> None:
        chunk_gate = mic.get("live_role_gate") if isinstance(mic.get("live_role_gate"), dict) else {}
        segment_gate = mic.get("live_segment_role_gate") if isinstance(mic.get("live_segment_role_gate"), dict) else {}
        kept_gate_rows = segment_gate.get("kept_segments") if isinstance(segment_gate.get("kept_segments"), list) else []
        for row in mic_segments:
            start = safe_float(row.get("start"))
            end = safe_float(row.get("end"), start)
            duration = end - start
            remote_rows = self.overlapping_remote(remote_segments, start, end)
            remote_text = clean_text(" ".join(str(item.get("text") or "") for item in remote_rows))
            remote_similarity = text_similarity(row.get("text"), remote_text) if remote_text else 0.0
            features = self.segment_audio_features(mic, remote, start, end)
            corr = features.get("corr")
            strong_audio = (
                safe_float(features.get("mic_db"), -120.0) >= -60.0
                and (
                    safe_float(features.get("remote_db"), -120.0) <= -48.0
                    or (corr is not None and safe_float(corr) <= 0.25)
                )
            )
            segment_gate_kept = any(
                abs(safe_float(item.get("start")) - start) <= 0.05
                and abs(safe_float(item.get("end")) - end) <= 0.05
                for item in kept_gate_rows
                if isinstance(item, dict)
            )
            reasons: list[str] = []
            if not 1.2 <= duration <= 12.0:
                reasons.append("unsupported_duration")
            if len(tokens(row.get("text"))) < 2:
                reasons.append("too_few_tokens")
            if remote_similarity > 0.25:
                reasons.append("text_too_close_to_remote_for_seed")
            if not strong_audio:
                reasons.append("weak_local_audio_separation")
            if not segment_gate_kept:
                reasons.append("segment_gate_did_not_keep_local_island")
            if reasons:
                self.enrollment_rows.append(
                    {
                        "schema": SCHEMA,
                        "created_at": now_iso(),
                        "kind": "positive_seed_probe",
                        "chunk_index": chunk_index,
                        "start": round(start, 3),
                        "end": round(end, 3),
                        "duration_sec": round(max(0.0, duration), 3),
                        "text": clean_text(row.get("text")),
                        "accepted": False,
                        "reject_reasons": reasons,
                        "evidence": {
                            "audio": features,
                            "chunk_role_gate_status": chunk_gate.get("status"),
                            "segment_gate_kept": segment_gate_kept,
                            "remote_text": remote_text,
                            "remote_similarity": round(remote_similarity, 6),
                        },
                        "used_batch_fields_for_selection": False,
                        "timeline_causal": True,
                    }
                )
                continue
            self.add_seed(
                kind="positive",
                chunk_index=chunk_index,
                row=row,
                source=mic,
                mic=True,
                evidence={
                    "selection": "kept_text_distinct_low_correlation_local_mic",
                    "audio": features,
                    "remote_similarity": round(remote_similarity, 6),
                },
            )
        for row in remote_segments:
            start = safe_float(row.get("start"))
            end = safe_float(row.get("end"), start)
            if not (1.2 <= end - start <= 12.0 and len(tokens(row.get("text"))) >= 2):
                continue
            _, audio, _ = self.audio_slice(remote, mic=False, start=start, end=end)
            if audio is None or rms_db(audio) < -60.0:
                continue
            self.add_seed(
                kind="negative",
                chunk_index=chunk_index,
                row=row,
                source=remote,
                mic=False,
                evidence={"selection": "closed_remote_asr_segment"},
            )

    def process_chunk(self, record: dict[str, Any]) -> dict[str, Any]:
        chunk_index = safe_int(record.get("index"))
        mic = record.get("mic") if isinstance(record.get("mic"), dict) else {}
        remote = record.get("remote") if isinstance(record.get("remote"), dict) else {}
        if self.backend is None or not mic or not remote:
            result = {
                "schema": SCHEMA,
                "status": "skipped",
                "reason": "backend_or_source_unavailable",
                "batch_authoritative": True,
                "promotion_allowed": False,
            }
            mic["causal_target_me_shadow"] = result
            self.persist(status="skipped_backend_unavailable")
            return result
        mic_segments = read_asr_segments(mic, self.session)
        remote_segments = read_asr_segments(remote, self.session)
        accepted_segments = self.evaluate_segments(chunk_index, mic, remote, mic_segments, remote_segments)
        chunk_candidates: list[dict[str, Any]] = []
        for group_index, group in enumerate(self.group_segments(accepted_segments), start=1):
            candidate = self.micro_candidate(
                chunk_index,
                group_index,
                group,
                mic,
                remote_segments,
            )
            self.candidates.append(candidate)
            if candidate.get("status") == "accepted":
                chunk_candidates.append(candidate)
        self.enroll_after_chunk(chunk_index, mic, remote, mic_segments, remote_segments)
        result = {
            "schema": SCHEMA,
            "created_at": now_iso(),
            "status": "candidate" if chunk_candidates else "no_candidate",
            "mode": "past_chunks_only",
            "backend": self.backend_status,
            "evaluated_segment_count": len(mic_segments),
            "speaker_supported_segment_count": len(accepted_segments),
            "accepted_micro_asr_candidate_count": len(chunk_candidates),
            "candidates": chunk_candidates,
            "enrollment": {
                "positive_seed_count": len(self.positive_seeds),
                "negative_seed_count": len(self.negative_seeds),
            },
            "used_batch_fields_for_selection": False,
            "timeline_causal": True,
            "batch_authoritative": True,
            "promotion_allowed": False,
        }
        mic["causal_target_me_shadow"] = result
        self.persist(status="running")
        return result

    def persist(self, *, status: str) -> None:
        rewrite_jsonl(self.out_dir / "enrollment.jsonl", self.enrollment_rows)
        rewrite_jsonl(self.out_dir / "evaluations.jsonl", self.evaluations)
        rewrite_jsonl(self.out_dir / "candidates.jsonl", self.candidates)
        accepted = [row for row in self.candidates if row.get("status") == "accepted"]
        write_json(
            self.out_dir / "state.json",
            {
                "schema": SCHEMA,
                "generator": {"name": "live-progressive-target-me", "version": SCRIPT_VERSION},
                "updated_at": now_iso(),
                "status": status,
                "mode": "past_chunks_only",
                "backend": self.backend_status,
                "positive_seed_count": len(self.positive_seeds),
                "negative_seed_count": len(self.negative_seeds),
                "evaluated_segment_count": len(self.evaluations),
                "candidate_count": len(self.candidates),
                "accepted_candidate_count": len(accepted),
                "accepted_candidate_seconds": round(
                    sum(safe_float(row.get("duration_sec")) for row in accepted),
                    3,
                ),
                "used_batch_fields_for_selection": False,
                "timeline_causal": True,
                "batch_authoritative": True,
                "promotion_allowed": False,
                "outputs": {
                    "enrollment": "derived/live/causal-target-me/enrollment.jsonl",
                    "evaluations": "derived/live/causal-target-me/evaluations.jsonl",
                    "candidates": "derived/live/causal-target-me/candidates.jsonl",
                },
            },
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay causal Target-Me shadow over existing live chunk artifacts.")
    parser.add_argument("session", type=Path)
    parser.add_argument("--model", default=str(Path.home() / ".local/share/murmurmark/models/whisper.cpp/ggml-large-v3-q5_0.bin"))
    parser.add_argument("--language", default="ru")
    parser.add_argument("--whisper-cli", default=os.environ.get("WHISPER_CLI", "whisper-cli"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    session = args.session.expanduser().resolve()
    manager = ProgressiveTargetMeShadow(
        session,
        model=args.model,
        language=args.language,
        whisper_cli=args.whisper_cli,
    )
    replay_segment_gate = load_replay_segment_gate()
    chunk_paths = sorted((session / "derived/live/chunks").glob("*/chunk.json"))
    chunks: list[dict[str, Any]] = []
    for path in chunk_paths:
        chunk = read_json(path)
        if not chunk:
            continue
        mic = chunk.get("mic") if isinstance(chunk.get("mic"), dict) else {}
        if replay_segment_gate is not None:
            # Historical chunks predate the always-on segment gate. Recompute
            # only its evidence; keep the original draft text untouched.
            mic["live_segment_role_gate"] = replay_segment_gate(session, chunk)
        manager.process_chunk(chunk)
        write_json(path, chunk)
        chunks.append(chunk)
    rewrite_jsonl(session / "derived/live/chunks.jsonl", chunks)
    manager.persist(status="completed")
    state = read_json(manager.out_dir / "state.json") or {}
    print(f"status: {state.get('status')}")
    print(f"positive_seeds: {state.get('positive_seed_count')}")
    print(f"negative_seeds: {state.get('negative_seed_count')}")
    print(f"accepted_candidates: {state.get('accepted_candidate_count')}")
    print(f"accepted_candidate_seconds: {state.get('accepted_candidate_seconds')}")
    print(f"report: {manager.out_dir / 'state.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
