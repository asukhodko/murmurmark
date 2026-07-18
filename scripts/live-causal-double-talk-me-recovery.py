#!/usr/bin/env python3
"""Recover causal Me speech during remote-active intervals in a shadow profile.

The selector uses only committed live PCM, live ASR, and Target-Me enrollment that ended before
the candidate interval. Authoritative batch artifacts are intentionally unavailable here; they are
used only by the corpus reporter.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any, Protocol

import numpy as np
import soundfile as sf
from scipy import signal


_PREFILTER_PATH = Path(__file__).with_name("causal_candidate_prefilter.py")
_PREFILTER_SPEC = importlib.util.spec_from_file_location(
    "murmurmark_causal_candidate_prefilter", _PREFILTER_PATH
)
if _PREFILTER_SPEC is None or _PREFILTER_SPEC.loader is None:
    raise RuntimeError(f"cannot load helper: {_PREFILTER_PATH}")
_PREFILTER = importlib.util.module_from_spec(_PREFILTER_SPEC)
sys.modules[_PREFILTER_SPEC.name] = _PREFILTER
_PREFILTER_SPEC.loader.exec_module(_PREFILTER)
cheap_prefilter_fingerprint = _PREFILTER.decision_set_fingerprint
cheap_prefilter_route_rows = _PREFILTER.route_rows


SCHEMA = "murmurmark.causal_double_talk_me_recovery/v1"
SCRIPT_VERSION = "1.2.0"
PROFILE = "causal_double_talk_me_recovery_v1"
OUTPUT_RELATIVE = Path("derived/live/causal-double-talk-me-recovery-v1")
SAMPLE_RATE = 16_000
LEADING_SILENCE_SEC = 0.40
MIN_ASR_SCORE = 0.58
MIN_SOURCE_ALIGNMENT = 0.22
MAX_REMOTE_TEXT_SIMILARITY = 0.24
MAX_REMOTE_TOKEN_RECALL = 0.20
MAX_REMOTE_AUDIO_STRENGTH = 0.30
MIN_TEXT_CONSENSUS = 0.45
MIN_INDEPENDENT_FAMILIES = 2
MIN_TRAINING_SEC = 4.0
MAX_TRAINING_SEC = 45.0
REMOTE_ACTIVE_MIN_DB = -65.0
EPSILON = 1.0e-12
FIR_CONFIGS = (
    ("fir_40ms_reg_1e2", 640, 1.0e-2),
    ("fir_80ms_reg_2e2", 1_280, 2.0e-2),
    ("fir_160ms_reg_5e2", 2_560, 5.0e-2),
)
RUNTIME_VIEW_METHODS = {"hybrid_ratio_mask_strict"}
RUNTIME_MAX_GROUPS_PER_CHUNK = 1
REMOTE_CONTENT_STOPWORDS = {
    "а", "бы", "в", "вот", "да", "для", "же", "и", "как", "мы", "на", "не",
    "но", "ну", "он", "она", "по", "с", "так", "там", "то", "у", "что", "это",
}


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip():
            continue
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
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def merge_rows(
    previous: list[dict[str, Any]],
    current: list[dict[str, Any]],
    *,
    key: str,
) -> list[dict[str, Any]]:
    merged = {
        str(row.get(key)): row
        for row in previous
        if row.get(key) is not None
    }
    for row in current:
        if row.get(key) is not None:
            merged[str(row.get(key))] = row
    return sorted(
        merged.values(),
        key=lambda row: (
            safe_int(row.get("chunk_index")),
            safe_float(row.get("start")),
            safe_float(row.get("end")),
            str(row.get(key) or ""),
        ),
    )


def load_script(filename: str, module_name: str) -> ModuleType:
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load helper: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def interval_overlap(start: float, end: float, other_start: float, other_end: float) -> float:
    return max(0.0, min(end, other_end) - max(start, other_start))


def stable_id(session: str, chunk_index: int, start: float, end: float) -> str:
    digest = hashlib.sha256(
        f"{session}:{chunk_index}:{start:.3f}:{end:.3f}:{PROFILE}".encode("utf-8")
    ).hexdigest()[:16]
    return f"causal_double_talk_{digest}"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def resolve_path(session: Path, value: Any) -> Path | None:
    if not value:
        return None
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = session / path
    return path.resolve() if path.exists() else None


def allowed_selection_ids(manifest: Path | None, session_name: str) -> set[str] | None:
    if manifest is None:
        return None
    ids: set[str] = set()
    for row in read_jsonl(manifest):
        if row.get("session") != session_name:
            continue
        for selection in row.get("causal_selection") or []:
            if isinstance(selection, dict) and selection.get("id"):
                ids.add(str(selection["id"]))
    return ids


def selection_contract(row: dict[str, Any]) -> dict[str, bool]:
    checks = row.get("checks") if isinstance(row.get("checks"), dict) else {}
    recording = row.get("recording_time_evidence") if isinstance(row.get("recording_time_evidence"), dict) else {}
    remote_guard = row.get("remote_audio_guard") if isinstance(row.get("remote_audio_guard"), dict) else {}
    duration = safe_float(row.get("duration_sec"), safe_float(row.get("end")) - safe_float(row.get("start")))
    return {
        "timeline_causal": row.get("timeline_causal") is True and checks.get("timeline_causal") is True,
        "selection_does_not_use_batch": row.get("used_batch_fields_for_selection") is False
        and checks.get("selection_does_not_use_batch") is True,
        "recording_time_committed_pcm": checks.get("recording_time_committed_pcm") is True,
        "recording_time_evidence": recording.get("status") == "passed",
        "past_only_enrollment": checks.get("past_only_enrollment") is True,
        "source_text_contentful": checks.get("source_text_contentful") is True,
        "supported_duration": 0.35 <= duration <= 30.0,
        "not_already_published": checks.get("not_already_published") is True,
        "remote_audio_active": safe_float(remote_guard.get("remote_db"), -120.0) > REMOTE_ACTIVE_MIN_DB,
    }


def eligible_selections(
    rows: list[dict[str, Any]], allowed_ids: set[str] | None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    eligible: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    for row in rows:
        if allowed_ids is not None and str(row.get("id")) not in allowed_ids:
            continue
        contract = selection_contract(row)
        status = "selected" if all(contract.values()) else "rejected"
        decision = {
            "schema": SCHEMA,
            "kind": "causal_double_talk_source_selection",
            "id": row.get("id"),
            "chunk_index": row.get("chunk_index"),
            "start": row.get("start"),
            "end": row.get("end"),
            "duration_sec": row.get("duration_sec"),
            "text": row.get("text"),
            "status": status,
            "checks": contract,
            "reasons": [name for name, passed in contract.items() if not passed],
            "speaker_evidence": row.get("speaker_evidence") or {},
            "remote_audio_guard": row.get("remote_audio_guard") or {},
            "recording_time_evidence": row.get("recording_time_evidence") or {},
            "source_evaluation": row.get("source_evaluation") or {},
            "timeline_causal": True,
            "used_batch_fields_for_selection": False,
        }
        decisions.append(decision)
        if status == "selected":
            eligible.append({**row, "source_selection_id": row.get("id")})
    return eligible, decisions


def runtime_selection_supported(row: dict[str, Any]) -> bool:
    checks = row.get("checks") if isinstance(row.get("checks"), dict) else {}
    speaker = row.get("speaker_evidence") if isinstance(row.get("speaker_evidence"), dict) else {}
    scores = speaker.get("scores") if isinstance(speaker.get("scores"), dict) else {}
    return bool(
        checks.get("past_enrollment_ready") is True
        and (
            checks.get("speaker_supported") is True
            or safe_float(scores.get("target"), -1.0) >= 0.10
        )
    )


def runtime_selection_scope(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(
        rows,
        key=lambda row: (safe_int(row.get("chunk_index")), safe_float(row.get("start"))),
    )
    supported_ids = {
        str(row.get("id")) for row in ordered if runtime_selection_supported(row)
    }
    result: list[dict[str, Any]] = []
    for index, row in enumerate(ordered):
        include = str(row.get("id")) in supported_ids
        if not include:
            for neighbor_index in (index - 1, index + 1):
                if not 0 <= neighbor_index < len(ordered):
                    continue
                neighbor = ordered[neighbor_index]
                if str(neighbor.get("id")) not in supported_ids:
                    continue
                if safe_int(neighbor.get("chunk_index")) != safe_int(row.get("chunk_index")):
                    continue
                gap = max(
                    safe_float(row.get("start")) - safe_float(neighbor.get("end")),
                    safe_float(neighbor.get("start")) - safe_float(row.get("end")),
                )
                if gap <= 2.5:
                    include = True
                    break
        if include:
            result.append(row)
    return result


def runtime_group_priority(group: list[dict[str, Any]]) -> tuple[int, int, float, float]:
    boundary_bridge = any(
        (row.get("source_window") or {}).get("mode") == "causal_boundary_bridge"
        for row in group
    )
    speaker_supported = any(
        (row.get("checks") or {}).get("speaker_supported") is True for row in group
    )
    target_score = max(
        (
            safe_float(((row.get("speaker_evidence") or {}).get("scores") or {}).get("target"), -1.0)
            for row in group
        ),
        default=-1.0,
    )
    return (
        1 if boundary_bridge else 0,
        1 if speaker_supported else 0,
        target_score,
        -safe_float(group[0].get("start")),
    )


def group_selections(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    expanded: list[dict[str, Any]] = []
    originals = sorted(
        rows,
        key=lambda item: (safe_int(item.get("chunk_index")), safe_float(item.get("start"))),
    )
    for row in originals:
        start = safe_float(row.get("start"))
        end = safe_float(row.get("end"), start)
        if end - start <= 12.0:
            expanded.append(row)
            continue
        window_start = start
        window_index = 0
        while window_start < end - 0.35:
            window_end = min(end, window_start + 8.0)
            expanded.append(
                {
                    **row,
                    "start": round(window_start, 3),
                    "end": round(window_end, 3),
                    "duration_sec": round(window_end - window_start, 3),
                    "source_window": {
                        "mode": "causal_long_selection_window",
                        "index": window_index,
                        "original_start": round(start, 3),
                        "original_end": round(end, 3),
                    },
                }
            )
            if window_end >= end - EPSILON:
                break
            window_start += 6.0
            window_index += 1
    groups: list[list[dict[str, Any]]] = []
    for row in expanded:
        if not groups:
            groups.append([row])
            continue
        current = groups[-1]
        previous = current[-1]
        duration = safe_float(row.get("end")) - safe_float(current[0].get("start"))
        if (
            safe_int(row.get("chunk_index")) == safe_int(previous.get("chunk_index"))
            and safe_float(row.get("start")) - safe_float(previous.get("end")) <= 0.30
            and duration <= 12.0
        ):
            current.append(row)
        else:
            groups.append([row])
    for previous, following in zip(originals, originals[1:]):
        if safe_int(previous.get("chunk_index")) != safe_int(following.get("chunk_index")):
            continue
        previous_start = safe_float(previous.get("start"))
        previous_end = safe_float(previous.get("end"), previous_start)
        following_start = safe_float(following.get("start"))
        following_end = safe_float(following.get("end"), following_start)
        gap = following_start - previous_end
        union_duration = following_end - previous_start
        if gap > 2.5 or (gap <= 0.30 and union_duration <= 12.0):
            continue
        center = (previous_end + following_start) / 2.0
        bridge_start = max(previous_start, center - 3.5)
        bridge_end = min(following_end, center + 3.5)
        if bridge_end - bridge_start < 1.0:
            continue
        groups.append(
            [
                {
                    **previous,
                    "start": round(bridge_start, 3),
                    "end": round(bridge_end, 3),
                    "duration_sec": round(bridge_end - bridge_start, 3),
                    "text": clean_text(f"{previous.get('text') or ''} {following.get('text') or ''}"),
                    "_source_selection_ids": [
                        previous.get("source_selection_id"),
                        following.get("source_selection_id"),
                    ],
                    "source_window": {
                        "mode": "causal_boundary_bridge",
                        "gap_sec": round(gap, 3),
                        "previous_end": round(previous_end, 3),
                        "following_start": round(following_start, 3),
                    },
                }
            ]
        )
    unique: dict[tuple[int, float, float], list[dict[str, Any]]] = {}
    for group in groups:
        key = (
            safe_int(group[0].get("chunk_index")),
            round(safe_float(group[0].get("start")), 3),
            round(safe_float(group[-1].get("end")), 3),
        )
        unique.setdefault(key, group)
    groups = list(unique.values())
    groups.sort(
        key=lambda group: (
            safe_int(group[0].get("chunk_index")),
            safe_float(group[0].get("start")),
            safe_float(group[-1].get("end")),
        )
    )
    return groups


def training_row_is_remote_dominant(row: dict[str, Any], progressive: ModuleType) -> bool:
    audio = row.get("audio") if isinstance(row.get("audio"), dict) else {}
    duration = safe_float(row.get("duration_sec"), safe_float(row.get("end")) - safe_float(row.get("start")))
    similarity = safe_float(progressive.asr_text_similarity(row.get("text"), row.get("remote_text")))
    return bool(
        row.get("timeline_causal") is True
        and row.get("used_batch_fields_for_selection") is False
        and row.get("classification") == "not_supported"
        and 0.8 <= duration <= 12.0
        and safe_float(audio.get("remote_db"), -120.0) >= -55.0
        and safe_float(audio.get("mic_minus_remote_db"), 99.0) <= -4.0
        and (similarity >= 0.16 or safe_float(audio.get("corr")) >= 0.06)
    )


@dataclass
class CausalEchoModel:
    target_start: float
    training_rows: list[dict[str, Any]]
    training_seconds: float
    delay_samples: int
    delay_evidence: dict[str, Any]
    fir_filters: dict[str, np.ndarray]
    spectral_transfer: np.ndarray


def build_echo_model(
    *,
    target_start: float,
    evaluations: list[dict[str, Any]],
    audio: Any,
    progressive: ModuleType,
    separation: ModuleType,
    fir_configs: tuple[tuple[str, int, float], ...] = FIR_CONFIGS,
) -> CausalEchoModel | None:
    rows = [
        row for row in evaluations
        if safe_float(row.get("end")) <= target_start + 1.0e-6
        and training_row_is_remote_dominant(row, progressive)
    ]
    rows.sort(key=lambda row: safe_float(row.get("end")), reverse=True)
    windows: list[tuple[np.ndarray, np.ndarray]] = []
    selected: list[dict[str, Any]] = []
    seconds = 0.0
    for row in rows:
        pair = audio.pair(
            safe_int(row.get("chunk_index")), safe_float(row.get("start")), safe_float(row.get("end"))
        )
        if pair is None:
            continue
        windows.append(pair)
        selected.append(row)
        seconds += min(pair[0].size, pair[1].size) / SAMPLE_RATE
        if seconds >= MAX_TRAINING_SEC:
            break
    if seconds < MIN_TRAINING_SEC:
        return None
    delay, delay_evidence = separation.estimate_delay(windows)
    fir_filters = {
        name: separation.fit_fir(windows, delay, taps=taps, regularization=regularization)
        for name, taps, regularization in fir_configs
    }
    return CausalEchoModel(
        target_start=target_start,
        training_rows=sorted(selected, key=lambda row: safe_float(row.get("start"))),
        training_seconds=seconds,
        delay_samples=delay,
        delay_evidence=delay_evidence,
        fir_filters=fir_filters,
        spectral_transfer=separation.fit_spectral_transfer(windows, delay, regularization=2.0e-2),
    )


def ratio_mask_residual(mic: np.ndarray, echo_hat: np.ndarray, *, strength: float) -> np.ndarray:
    count = min(mic.size, echo_hat.size)
    source = np.asarray(mic[:count], dtype=np.float64)
    echo = np.asarray(echo_hat[:count], dtype=np.float64)
    _, _, source_stft = signal.stft(
        source, fs=SAMPLE_RATE, nperseg=512, noverlap=384, boundary="zeros", padded=True
    )
    _, _, echo_stft = signal.stft(
        echo, fs=SAMPLE_RATE, nperseg=512, noverlap=384, boundary="zeros", padded=True
    )
    frames = min(source_stft.shape[1], echo_stft.shape[1])
    source_stft = source_stft[:, :frames]
    echo_stft = echo_stft[:, :frames]
    source_power = np.abs(source_stft) ** 2
    echo_power = np.abs(echo_stft) ** 2
    local_power = np.maximum(source_power - strength * echo_power, 0.03 * source_power)
    gain = np.sqrt(local_power / (source_power + EPSILON))
    gain = signal.medfilt2d(gain, kernel_size=(3, 3))
    gain = np.clip(gain, 0.10, 1.0)
    _, residual = signal.istft(
        source_stft * gain, fs=SAMPLE_RATE, nperseg=512, noverlap=384, boundary=True
    )
    if residual.size < count:
        residual = np.pad(residual, (0, count - residual.size))
    return np.asarray(residual[:count], dtype=np.float64)


def residual_views(
    model: CausalEchoModel,
    remote: np.ndarray,
    mic: np.ndarray,
    separation: ModuleType,
    *,
    view_profile: str = "full",
) -> list[dict[str, Any]]:
    count = min(remote.size, mic.size)
    remote = remote[:count]
    source = mic[:count].astype(np.float64)
    aligned = separation.shift_reference(remote, model.delay_samples).astype(np.float64)
    views: list[dict[str, Any]] = []
    fir_echoes: dict[str, np.ndarray] = {}
    fir_configs = (
        (next(config for config in FIR_CONFIGS if config[0] == "fir_80ms_reg_2e2"),)
        if view_profile == "runtime"
        else FIR_CONFIGS
    )
    for name, _, _ in fir_configs:
        echo_hat = signal.lfilter(model.fir_filters[name], [1.0], aligned)
        fir_echoes[name] = echo_hat
        views.append({"method": name, "family": "fir", "residual": source - echo_hat, "echo_hat": echo_hat})
    spectral_echo = separation.apply_spectral_transfer(aligned, model.spectral_transfer)
    views.append(
        {"method": "spectral_projection", "family": "spectral", "residual": source - spectral_echo, "echo_hat": spectral_echo}
    )
    hybrid_echo = 0.65 * fir_echoes["fir_80ms_reg_2e2"] + 0.35 * spectral_echo
    hybrid = source - hybrid_echo
    views.append({"method": "hybrid_fir_spectral", "family": "hybrid", "residual": hybrid, "echo_hat": hybrid_echo})
    ratio_configs = (("strict", 1.20),) if view_profile == "runtime" else (("mild", 0.85), ("strict", 1.20))
    for suffix, strength in ratio_configs:
        residual = ratio_mask_residual(hybrid, hybrid_echo, strength=strength)
        views.append(
            {
                "method": f"hybrid_ratio_mask_{suffix}",
                "family": "ratio_mask",
                "residual": residual,
                "echo_hat": source - residual,
            }
        )
    return views


class PastTargetVoice:
    def __init__(self, session: Path, progressive: ModuleType) -> None:
        self.session = session
        self.progressive = progressive
        self.rows = read_jsonl(session / "derived/live/causal-target-me/enrollment.jsonl")
        self.backend, self.backend_status = progressive.load_default_backend()
        self.seed_vectors: list[tuple[dict[str, Any], np.ndarray]] = []
        self.clip_cache: dict[str, tuple[np.ndarray | None, dict[str, Any]]] = {}
        if self.backend is not None:
            for row in self.rows:
                if row.get("accepted") is not True or row.get("kind") not in {"positive_seed", "negative_seed"}:
                    continue
                clip = resolve_path(session, row.get("clip"))
                if clip is None:
                    continue
                vector, info = self.backend.embed(clip)
                if vector is not None:
                    self.seed_vectors.append((row, vector))

    def model(self, cutoff: float) -> tuple[dict[str, Any] | None, dict[str, float] | None, dict[str, Any]]:
        positives = [
            vector for row, vector in self.seed_vectors
            if row.get("kind") == "positive_seed" and safe_float(row.get("end")) <= cutoff + 1.0e-6
        ]
        negatives = [
            vector for row, vector in self.seed_vectors
            if row.get("kind") == "negative_seed" and safe_float(row.get("end")) <= cutoff + 1.0e-6
        ]
        evidence = {
            "mode": "past_only_target_me_enrollment",
            "cutoff_sec": round(cutoff, 3),
            "positive_seed_count": len(positives),
            "negative_seed_count": len(negatives),
            "backend": self.backend_status,
            "past_only": True,
        }
        if len(positives) < 3 or len(negatives) < 3:
            return None, None, evidence
        positive_centroid = self.progressive.make_centroid(positives)
        negative_centroid = self.progressive.make_centroid(negatives)
        model = {"positive": positive_centroid, "negative": negative_centroid}

        def score(vector: np.ndarray) -> dict[str, float]:
            positive = self.progressive.cosine(vector, positive_centroid)
            negative = self.progressive.cosine(vector, negative_centroid)
            return {"positive": positive, "negative": negative, "target": positive - negative}

        positive_scores = [score(vector) for vector in positives]
        negative_scores = [score(vector) for vector in negatives]
        thresholds = {
            "target": (
                self.progressive.percentile([row["target"] for row in positive_scores], 10)
                + self.progressive.percentile([row["target"] for row in negative_scores], 90)
            ) / 2.0,
            "positive": max(
                0.0,
                self.progressive.percentile([row["positive"] for row in positive_scores], 10) - 0.05,
            ),
            "negative_max": self.progressive.percentile(
                [row["positive"] for row in negative_scores], 90
            ) + 0.05,
        }
        return model, thresholds, evidence

    def evaluate(self, wav: Path, cutoff: float) -> dict[str, Any]:
        model, thresholds, evidence = self.model(cutoff)
        if self.backend is None:
            return {**evidence, "status": "rejected", "reason": "backend_unavailable"}
        if model is None or thresholds is None:
            return {**evidence, "status": "rejected", "reason": "insufficient_past_enrollment"}
        key = str(wav)
        if key not in self.clip_cache:
            self.clip_cache[key] = self.backend.embed(wav)
        vector, info = self.clip_cache[key]
        if vector is None:
            return {**evidence, "status": "rejected", "reason": "embedding_failed", "embedding_info": info}
        positive = self.progressive.cosine(vector, model["positive"])
        negative = self.progressive.cosine(vector, model["negative"])
        scores = {"positive": positive, "negative": negative, "target": positive - negative}
        passed = bool(
            scores["target"] >= thresholds["target"]
            and scores["positive"] >= thresholds["positive"]
            and scores["negative"] <= thresholds["negative_max"]
        )
        return {
            **evidence,
            "status": "passed" if passed else "rejected",
            "reason": "target_me_supported" if passed else "target_me_threshold_failed",
            "scores": {key: round(value, 6) for key, value in scores.items()},
            "thresholds": {key: round(value, 6) for key, value in thresholds.items()},
            "embedding_info": info,
        }


def localize_with_target_voice(
    *,
    residual: np.ndarray,
    extraction_start: float,
    source_start: float,
    source_end: float,
    enrollment_cutoff: float,
    voice: PastTargetVoice,
    output: Path,
    candidate_id: str,
) -> tuple[float, float, dict[str, Any]]:
    model, thresholds, enrollment = voice.model(enrollment_cutoff)
    if model is None or thresholds is None:
        return source_start, source_end, {
            "status": "not_available",
            "reason": "insufficient_past_target_me_enrollment",
            "enrollment": enrollment,
        }
    duration = source_end - source_start
    if duration < 2.0:
        return source_start, source_end, {
            "status": "not_needed",
            "reason": "source_interval_too_short",
            "enrollment": enrollment,
        }
    window_sec = min(2.4, duration)
    hop_sec = 0.8
    starts: list[float] = []
    cursor = source_start
    while cursor + window_sec <= source_end + EPSILON:
        starts.append(cursor)
        cursor += hop_sec
    final_start = max(source_start, source_end - window_sec)
    if not starts or final_start - starts[-1] >= 0.20:
        starts.append(final_start)
    probes: list[dict[str, Any]] = []
    for index, window_start in enumerate(starts, start=1):
        window_end = min(source_end, window_start + window_sec)
        local_start = max(0, int(round((window_start - extraction_start) * SAMPLE_RATE)))
        local_end = min(residual.size, int(round((window_end - extraction_start) * SAMPLE_RATE)))
        if local_end - local_start < int(0.8 * SAMPLE_RATE):
            continue
        wav = output / "localization" / f"{candidate_id}_{index:02d}.wav"
        wav.parent.mkdir(parents=True, exist_ok=True)
        sf.write(
            wav,
            np.clip(residual[local_start:local_end], -0.999, 0.999),
            SAMPLE_RATE,
            subtype="PCM_16",
        )
        evidence = voice.evaluate(wav, enrollment_cutoff)
        probes.append(
            {
                "start": round(window_start, 3),
                "end": round(window_end, 3),
                "duration_sec": round(window_end - window_start, 3),
                "status": evidence.get("status"),
                "target_me": evidence,
                "wav": str(wav),
            }
        )
    passed = [row for row in probes if row.get("status") == "passed"]
    if not passed:
        return source_start, source_end, {
            "status": "not_localized",
            "reason": "no_target_me_supported_sliding_window",
            "enrollment": enrollment,
            "probes": probes,
        }
    clusters: list[list[dict[str, Any]]] = []
    for row in passed:
        if clusters and safe_float(row.get("start")) <= safe_float(clusters[-1][-1].get("end")) + 0.05:
            clusters[-1].append(row)
        else:
            clusters.append([row])
    selected_cluster = max(
        clusters,
        key=lambda cluster: (
            float(
                np.mean(
                    [safe_float((row.get("target_me") or {}).get("scores", {}).get("target")) for row in cluster]
                )
            ),
            safe_float(cluster[-1].get("end")) - safe_float(cluster[0].get("start")),
        ),
    )
    localized_start = max(source_start, safe_float(selected_cluster[0].get("start")) - 0.20)
    localized_end = min(source_end, safe_float(selected_cluster[-1].get("end")) + 0.30)
    return localized_start, localized_end, {
        "status": "localized",
        "reason": "past_target_me_supported_sliding_windows",
        "enrollment": enrollment,
        "probes": probes,
        "selected_probe_count": len(selected_cluster),
        "selected_start": round(localized_start, 3),
        "selected_end": round(localized_end, 3),
    }


class MicroASR(Protocol):
    def run(self, wav: Path, output_base: Path, *, force: bool) -> dict[str, Any]: ...


class FasterWhisperMicroASR:
    def __init__(self, model_path: Path, *, language: str, device: str, compute_type: str) -> None:
        self.model_path = model_path.expanduser().resolve()
        self.language = language
        self.device = device
        self.compute_type = compute_type
        self._model: Any | None = None

    def model(self) -> Any:
        if self._model is None:
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            from faster_whisper import WhisperModel

            self._model = WhisperModel(
                str(self.model_path), device=self.device, compute_type=self.compute_type
            )
        return self._model

    def run(self, wav: Path, output_base: Path, *, force: bool) -> dict[str, Any]:
        json_path = output_base.with_suffix(".json")
        txt_path = output_base.with_suffix(".txt")
        wav_sha256 = file_sha256(wav)
        if not force and json_path.exists() and txt_path.exists():
            payload = read_json(json_path)
            if payload.get("wav_sha256") == wav_sha256:
                return {
                    "status": payload.get("status", "passed"),
                    "text": clean_text(txt_path.read_text(encoding="utf-8", errors="ignore")),
                    "score": safe_float(payload.get("score")),
                    "json": str(json_path),
                    "rows": payload.get("rows") or [],
                    "cache_hit": True,
                }
        output_base.parent.mkdir(parents=True, exist_ok=True)
        try:
            segments, info = self.model().transcribe(
                str(wav),
                language=self.language,
                beam_size=1,
                best_of=1,
                temperature=0.0,
                vad_filter=False,
                condition_on_previous_text=False,
                word_timestamps=False,
            )
            rows: list[dict[str, Any]] = []
            for segment in segments:
                text = clean_text(getattr(segment, "text", ""))
                if not text:
                    continue
                avg_logprob = safe_float(getattr(segment, "avg_logprob", None), -10.0)
                rows.append(
                    {
                        "start_sec": round(safe_float(getattr(segment, "start", None)), 3),
                        "end_sec": round(safe_float(getattr(segment, "end", None)), 3),
                        "text": text,
                        "score": round(min(1.0, max(0.0, math.exp(avg_logprob))), 6),
                        "avg_logprob": round(avg_logprob, 6),
                        "no_speech_prob": round(
                            safe_float(getattr(segment, "no_speech_prob", None)), 6
                        ),
                    }
                )
        except Exception as error:
            return {
                "status": "failed",
                "reason": "faster_whisper_failed",
                "error": str(error),
                "text": "",
                "score": 0.0,
                "rows": [],
                "cache_hit": False,
            }
        text = clean_text(" ".join(str(row.get("text") or "") for row in rows))
        score = float(np.mean([safe_float(row.get("score")) for row in rows])) if rows else 0.0
        payload = {
            "schema": SCHEMA,
            "status": "passed",
            "backend": "faster_whisper_local_large_v3",
            "model": str(self.model_path),
            "wav_sha256": wav_sha256,
            "language": getattr(info, "language", self.language),
            "score": round(score, 6),
            "rows": rows,
        }
        write_json(json_path, payload)
        txt_path.write_text(text + ("\n" if text else ""), encoding="utf-8")
        return {
            "status": "passed",
            "text": text,
            "score": round(score, 6),
            "json": str(json_path),
            "rows": rows,
            "cache_hit": False,
        }


class WhisperCppCPUMicroASR:
    def __init__(
        self,
        model_path: Path,
        *,
        language: str,
        whisper_cli: str,
        progressive: ModuleType,
    ) -> None:
        self.model_path = model_path.expanduser().resolve()
        self.language = language
        self.whisper_cli = shutil.which(whisper_cli) or whisper_cli
        self.progressive = progressive

    def run(self, wav: Path, output_base: Path, *, force: bool) -> dict[str, Any]:
        json_path = output_base.with_suffix(".json")
        txt_path = output_base.with_suffix(".txt")
        metadata_path = output_base.with_suffix(".cache.json")
        wav_sha256 = file_sha256(wav)
        cache_contract = {
            "backend": "whisper_cpp_cpu_large_v3_q5_0",
            "wav_sha256": wav_sha256,
            "model": str(self.model_path),
            "language": self.language,
            "threads": 6,
            "beam_size": 1,
            "best_of": 1,
        }
        if not force and json_path.exists() and txt_path.exists():
            metadata = read_json(metadata_path)
            payload = read_json(json_path)
            transcription = payload.get("transcription")
            if (
                all(metadata.get(key) == value for key, value in cache_contract.items())
                and isinstance(transcription, list)
            ):
                return {
                    "status": "passed",
                    "text": clean_text(txt_path.read_text(encoding="utf-8", errors="ignore")),
                    "score": round(self.progressive.token_average_probability(json_path), 6),
                    "json": str(json_path),
                    "rows": self.progressive.asr_rows(json_path),
                    "cache_hit": True,
                }
        if not self.model_path.is_file():
            return {"status": "failed", "reason": "model_missing", "text": "", "score": 0.0, "rows": []}
        if not Path(self.whisper_cli).exists() and shutil.which(self.whisper_cli) is None:
            return {"status": "failed", "reason": "whisper_cli_missing", "text": "", "score": 0.0, "rows": []}
        output_base.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [
                self.whisper_cli,
                "--model", str(self.model_path),
                "--language", self.language,
                "--threads", "6",
                "--max-context", "0",
                "--beam-size", "1",
                "--best-of", "1",
                "--temperature", "0",
                "--no-fallback",
                "--no-gpu",
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
        text = clean_text(txt_path.read_text(encoding="utf-8", errors="ignore")) if txt_path.exists() else ""
        rows = self.progressive.asr_rows(json_path if json_path.exists() else None)
        score = round(self.progressive.token_average_probability(json_path if json_path.exists() else None), 6)
        if result.returncode != 0:
            return {
                "status": "failed",
                "reason": "whisper_cpp_cpu_failed",
                "returncode": result.returncode,
                "stderr_tail": result.stderr[-1000:],
                "text": "",
                "score": 0.0,
                "rows": [],
                "cache_hit": False,
            }
        write_json(metadata_path, {"schema": SCHEMA, **cache_contract})
        return {
            "status": "passed",
            "text": text,
            "score": score,
            "json": str(json_path),
            "rows": rows,
            "cache_hit": False,
        }


def remote_text_for_interval(
    session: Path,
    chunk: dict[str, Any],
    start: float,
    end: float,
    progressive: ModuleType,
) -> str:
    remote = chunk.get("remote") if isinstance(chunk.get("remote"), dict) else {}
    rows = progressive.read_asr_segments(remote, session)
    selected = [
        row for row in rows
        if interval_overlap(start - 0.25, end + 0.25, safe_float(row.get("start")), safe_float(row.get("end"))) > 0
    ]
    return clean_text(" ".join(str(row.get("text") or "") for row in selected))


def text_evidence(
    *,
    text: str,
    source_text: str,
    remote_text: str,
    progressive: ModuleType,
) -> dict[str, Any]:
    text_tokens = progressive.tokens(text)
    source_tokens = progressive.tokens(source_text)
    remote_tokens = progressive.tokens(remote_text)
    source_alignment = max(
        progressive.bag_recall(source_tokens, text_tokens),
        progressive.bag_recall(text_tokens, source_tokens),
    ) if text_tokens and source_tokens else 0.0
    remote_similarity = safe_float(progressive.asr_text_similarity(text, remote_text)) if remote_text else 0.0
    remote_recall = progressive.bag_recall(remote_tokens, text_tokens) if remote_tokens else 0.0
    remote_matches = progressive.bag_match_count(remote_tokens, text_tokens) if remote_tokens else 0
    source_content = {
        token for token in source_tokens if len(token) >= 3
    }
    forbidden = {
        token for token in remote_tokens
        if len(token) >= 3 and token not in source_content and token not in REMOTE_CONTENT_STOPWORDS
    }
    forbidden_matches = sorted(forbidden & set(text_tokens))
    lexically_remote_free = bool(
        remote_matches == 0
        and remote_recall <= EPSILON
        and not forbidden_matches
        and remote_similarity <= 0.35
    )
    strict_passed = bool(
        remote_similarity <= MAX_REMOTE_TEXT_SIMILARITY
        and not (remote_recall > MAX_REMOTE_TOKEN_RECALL and remote_matches >= 1)
        and not forbidden_matches
    )
    passed = strict_passed or lexically_remote_free
    return {
        "source_alignment": round(source_alignment, 6),
        "remote_similarity": round(remote_similarity, 6),
        "remote_token_recall": round(remote_recall, 6),
        "remote_matched_token_count": remote_matches,
        "remote_forbidden_matches": forbidden_matches,
        "remote_guard_status": "passed" if passed else "rejected",
        "remote_guard_mode": (
            "strict_similarity_and_token_forbiddance"
            if strict_passed
            else "zero_remote_content_token_match"
            if lexically_remote_free
            else "rejected"
        ),
    }


def select_asr_text(
    result: dict[str, Any], selection_start: float, selection_end: float
) -> tuple[str, float, int]:
    rows = [
        row for row in result.get("rows") or []
        if isinstance(row, dict)
        and selection_start - EPSILON
        <= (safe_float(row.get("start_sec")) + safe_float(row.get("end_sec"))) / 2.0
        <= selection_end + EPSILON
    ]
    text = clean_text(" ".join(str(row.get("text") or "") for row in rows))
    scores = [safe_float(row.get("score")) for row in rows if safe_float(row.get("score")) > 0]
    score = float(np.mean(scores)) if scores else safe_float(result.get("score"))
    return text, score, len(rows)


def consensus_cluster(views: list[dict[str, Any]], progressive: ModuleType) -> list[dict[str, Any]]:
    usable = [row for row in views if row.get("view_status") == "eligible"]
    clusters: list[list[dict[str, Any]]] = []
    for row in sorted(usable, key=lambda item: (-safe_float(item.get("asr_score")), str(item.get("method")))):
        destination: list[dict[str, Any]] | None = None
        best_similarity = 0.0
        for cluster in clusters:
            similarity = max(
                safe_float(progressive.asr_text_similarity(row.get("text"), item.get("text")))
                for item in cluster
            )
            if similarity >= MIN_TEXT_CONSENSUS and similarity > best_similarity:
                destination = cluster
                best_similarity = similarity
        if destination is None:
            clusters.append([row])
        else:
            destination.append(row)
    ranked: list[dict[str, Any]] = []
    for cluster in clusters:
        families = sorted({str(row.get("family")) for row in cluster})
        methods = sorted({str(row.get("method")) for row in cluster})
        speaker_families = sorted(
            {str(row.get("family")) for row in cluster if (row.get("target_me") or {}).get("status") == "passed"}
        )
        representative = max(cluster, key=lambda row: safe_float(row.get("asr_score")))
        ranked.append(
            {
                "family_count": len(families),
                "families": families,
                "methods": methods,
                "speaker_family_count": len(speaker_families),
                "speaker_families": speaker_families,
                "member_count": len(cluster),
                "member_ids": [row.get("view_id") for row in cluster],
                "text": representative.get("text"),
                "representative_view_id": representative.get("view_id"),
                "mean_asr_score": round(float(np.mean([safe_float(row.get("asr_score")) for row in cluster])), 6),
            }
        )
    ranked.sort(
        key=lambda row: (
            safe_int(row.get("speaker_family_count")),
            safe_int(row.get("family_count")),
            safe_float(row.get("mean_asr_score")),
        ),
        reverse=True,
    )
    return ranked


def classify_rejection(views: list[dict[str, Any]], reasons: list[str]) -> str:
    if any((row.get("text_evidence") or {}).get("remote_guard_status") == "rejected" for row in views):
        return "probable_remote_leak"
    if not any(len(clean_text(row.get("text")).split()) >= 2 for row in views):
        return "probable_asr_noise"
    if "insufficient_causal_echo_training" in reasons or "insufficient_past_target_me_enrollment" in reasons:
        return "insufficient_evidence"
    if any(safe_float((row.get("audio_metrics") or {}).get("after", {}).get("remote_strength"), 1.0) <= 0.12 for row in views):
        return "probable_timing_overlap"
    return "insufficient_evidence"


def deduplicate_candidates(
    candidates: list[dict[str, Any]], progressive: ModuleType
) -> list[dict[str, Any]]:
    accepted = [row for row in candidates if row.get("status") == "accepted"]
    accepted.sort(
        key=lambda row: (
            safe_int((row.get("consensus") or {}).get("family_count")),
            safe_float((row.get("consensus") or {}).get("mean_asr_score")),
            safe_float(row.get("duration_sec")),
        ),
        reverse=True,
    )
    kept: list[dict[str, Any]] = []
    superseded: dict[str, str] = {}
    for row in accepted:
        duplicate = next(
            (
                other
                for other in kept
                if interval_overlap(
                    safe_float(row.get("start")),
                    safe_float(row.get("end")),
                    safe_float(other.get("start")),
                    safe_float(other.get("end")),
                )
                >= 0.50
                * min(safe_float(row.get("duration_sec")), safe_float(other.get("duration_sec")))
                and safe_float(progressive.asr_text_similarity(row.get("text"), other.get("text"))) >= 0.65
            ),
            None,
        )
        if duplicate is None:
            kept.append(row)
        else:
            superseded[str(row.get("id"))] = str(duplicate.get("id"))
    result: list[dict[str, Any]] = []
    for row in candidates:
        target = superseded.get(str(row.get("id")))
        if target and row.get("status") == "accepted":
            result.append(
                {
                    **row,
                    "status": "rejected",
                    "outcome": "rejected",
                    "classification": "probable_timing_overlap",
                    "reasons": ["duplicate_causal_candidate"],
                    "superseded_by": target,
                }
            )
        else:
            result.append(row)
    return result


def process_group(
    *,
    session: Path,
    group: list[dict[str, Any]],
    chunks: dict[int, dict[str, Any]],
    evaluations: list[dict[str, Any]],
    audio: Any,
    voice: PastTargetVoice,
    separation: ModuleType,
    progressive: ModuleType,
    output: Path,
    model_path: str,
    language: str,
    whisper_cli: str,
    force: bool,
    micro_asr: MicroASR,
    view_profile: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    chunk_index = safe_int(group[0].get("chunk_index"))
    start = safe_float(group[0].get("start"))
    end = safe_float(group[-1].get("end"), start)
    source_start = start
    source_end = end
    candidate_id = stable_id(session.name, chunk_index, start, end)
    source_text = clean_text(" ".join(str(row.get("text") or "") for row in group))
    source_selection_ids: list[Any] = []
    for row in group:
        values = row.get("_source_selection_ids") or [row.get("source_selection_id")]
        for value in values:
            if value and value not in source_selection_ids:
                source_selection_ids.append(value)
    base = {
        "schema": SCHEMA,
        "id": candidate_id,
        "session": session.name,
        "chunk_index": chunk_index,
        "start": round(start, 3),
        "end": round(end, 3),
        "duration_sec": round(max(0.0, end - start), 3),
        "source_start": round(source_start, 3),
        "source_end": round(source_end, 3),
        "source_selection_ids": source_selection_ids,
        "source_windows": [row.get("source_window") for row in group if row.get("source_window")],
        "source_text": source_text,
        "timeline_causal": True,
        "used_batch_fields_for_selection": False,
        "batch_authoritative": True,
        "publication_allowed": False,
        "promotion_allowed": False,
    }
    voice_model, _, voice_enrollment = voice.model(source_start)
    if voice_model is None:
        return {
            **base,
            "status": "rejected",
            "classification": "insufficient_evidence",
            "reasons": ["insufficient_past_target_me_enrollment"],
            "text": "",
            "view_count": 0,
            "past_target_me_enrollment": voice_enrollment,
        }, []
    model = build_echo_model(
        target_start=start,
        evaluations=evaluations,
        audio=audio,
        progressive=progressive,
        separation=separation,
        fir_configs=(
            (next(config for config in FIR_CONFIGS if config[0] == "fir_80ms_reg_2e2"),)
            if view_profile == "runtime"
            else FIR_CONFIGS
        ),
    )
    if model is None:
        return {
            **base,
            "status": "rejected",
            "classification": "insufficient_evidence",
            "reasons": ["insufficient_causal_echo_training"],
            "text": "",
            "view_count": 0,
        }, []
    chunk = chunks.get(chunk_index) or {}
    mic_source = chunk.get("mic") if isinstance(chunk.get("mic"), dict) else {}
    extraction_start = max(safe_float(mic_source.get("clip_start_sec")), start - 0.60)
    extraction_end = min(safe_float(mic_source.get("clip_end_sec"), end + 0.40), end + 0.40)
    pair = audio.pair(chunk_index, extraction_start, extraction_end)
    if pair is None:
        return {
            **base,
            "status": "rejected",
            "classification": "insufficient_evidence",
            "reasons": ["committed_pcm_extract_failed"],
            "text": "",
            "view_count": 0,
        }, []
    remote, mic = pair
    generated_views = residual_views(
        model,
        remote,
        mic,
        separation,
        view_profile=view_profile,
    )
    if view_profile == "runtime":
        generated_views = [
            row for row in generated_views if row.get("method") in RUNTIME_VIEW_METHODS
        ]
    strict_ratio = next(
        (row for row in generated_views if row.get("method") == "hybrid_ratio_mask_strict"),
        None,
    )
    localization: dict[str, Any] = {"status": "not_available", "reason": "strict_ratio_view_missing"}
    if strict_ratio is not None:
        start, end, localization = localize_with_target_voice(
            residual=np.asarray(strict_ratio["residual"], dtype=np.float32),
            extraction_start=extraction_start,
            source_start=source_start,
            source_end=source_end,
            enrollment_cutoff=source_start,
            voice=voice,
            output=output,
            candidate_id=candidate_id,
        )
    if abs(start - source_start) > 0.05 or abs(end - source_end) > 0.05:
        base.update(
            {
                "start": round(start, 3),
                "end": round(end, 3),
                "duration_sec": round(max(0.0, end - start), 3),
            }
        )
        extraction_start = max(safe_float(mic_source.get("clip_start_sec")), start - 0.60)
        extraction_end = min(safe_float(mic_source.get("clip_end_sec"), end + 0.40), end + 0.40)
        localized_pair = audio.pair(chunk_index, extraction_start, extraction_end)
        if localized_pair is not None:
            remote, mic = localized_pair
            generated_views = residual_views(
                model,
                remote,
                mic,
                separation,
                view_profile=view_profile,
            )
            if view_profile == "runtime":
                generated_views = [
                    row for row in generated_views if row.get("method") in RUNTIME_VIEW_METHODS
                ]
    remote_text = remote_text_for_interval(session, chunk, start, end, progressive)
    view_rows: list[dict[str, Any]] = []
    for view in generated_views:
        method = str(view["method"])
        family = str(view["family"])
        residual = np.asarray(view["residual"], dtype=np.float32)
        echo_hat = np.asarray(view["echo_hat"], dtype=np.float32)
        metrics = separation.evaluate_method(method, remote, mic, residual, echo_hat)
        wav = output / "residual_audio" / f"{candidate_id}_{method}.wav"
        wav.parent.mkdir(parents=True, exist_ok=True)
        sf.write(
            wav,
            np.clip(
                np.concatenate(
                    [np.zeros(int(LEADING_SILENCE_SEC * SAMPLE_RATE), dtype=np.float32), residual]
                ),
                -0.999,
                0.999,
            ),
            SAMPLE_RATE,
            subtype="PCM_16",
        )
        output_base = output / "micro_asr" / f"{candidate_id}_{method}"
        output_base.parent.mkdir(parents=True, exist_ok=True)
        result = micro_asr.run(wav, output_base, force=force)
        selection_start = LEADING_SILENCE_SEC + (start - extraction_start)
        selection_end = LEADING_SILENCE_SEC + (end - extraction_start)
        text, asr_score, selected_rows = select_asr_text(result, selection_start, selection_end)
        text_features = text_evidence(
            text=text,
            source_text=source_text,
            remote_text=remote_text,
            progressive=progressive,
        )
        local_start = max(0, int(round((start - extraction_start) * SAMPLE_RATE)))
        local_end = min(residual.size, int(round((end - extraction_start) * SAMPLE_RATE)))
        voice_wav = output / "speaker_audio" / f"{candidate_id}_{method}.wav"
        voice_wav.parent.mkdir(parents=True, exist_ok=True)
        sf.write(
            voice_wav,
            np.clip(residual[local_start:local_end], -0.999, 0.999),
            SAMPLE_RATE,
            subtype="PCM_16",
        )
        target_me = voice.evaluate(voice_wav, source_start)
        reasons: list[str] = []
        if metrics.get("status") != "passed":
            reasons.append("residual_audio_guard")
        if result.get("status") != "passed":
            reasons.append(str(result.get("reason") or "micro_asr_failed"))
        if len(progressive.tokens(text)) < 2:
            reasons.append("micro_asr_text_too_short")
        if asr_score < MIN_ASR_SCORE:
            reasons.append("low_micro_asr_score")
        if safe_float(text_features.get("source_alignment")) < MIN_SOURCE_ALIGNMENT:
            reasons.append("low_live_source_alignment")
        if text_features.get("remote_guard_status") != "passed":
            reasons.append("remote_forbidden_text_guard")
        if target_me.get("status") != "passed":
            reasons.append(str(target_me.get("reason") or "target_me_not_supported"))
        view_rows.append(
            {
                **base,
                "kind": "causal_double_talk_residual_view",
                "view_id": f"{candidate_id}:{method}",
                "method": method,
                "family": family,
                "view_status": "eligible" if not reasons else "rejected",
                "reasons": reasons,
                "text": text,
                "remote_text": remote_text,
                "asr_status": result.get("status"),
                "asr_score": round(asr_score, 6),
                "selected_asr_row_count": selected_rows,
                "asr_json": result.get("json"),
                "wav": str(wav),
                "speaker_wav": str(voice_wav),
                "audio_metrics": metrics,
                "text_evidence": text_features,
                "target_me": target_me,
            }
        )
    clusters = consensus_cluster(view_rows, progressive)
    best = clusters[0] if clusters else {}
    representative = next(
        (row for row in view_rows if row.get("view_id") == best.get("representative_view_id")),
        {},
    )
    multi_family = bool(
        safe_int(best.get("family_count")) >= MIN_INDEPENDENT_FAMILIES
        and safe_int(best.get("speaker_family_count")) >= MIN_INDEPENDENT_FAMILIES
    )
    representative_text = representative.get("text_evidence") or {}
    representative_audio = representative.get("audio_metrics") or {}
    strong_single_family = bool(
        safe_int(best.get("family_count")) >= 1
        and safe_int(best.get("speaker_family_count")) >= 1
        and safe_float(representative.get("asr_score")) >= 0.72
        and safe_float(representative_text.get("source_alignment")) >= 0.45
        and representative_text.get("remote_guard_status") == "passed"
        and not representative_text.get("remote_forbidden_matches")
        and safe_float((representative_audio.get("after") or {}).get("remote_strength"), 1.0) <= 0.24
        and (representative.get("target_me") or {}).get("status") == "passed"
    )
    accepted = multi_family or strong_single_family
    reasons: list[str] = []
    if not clusters:
        reasons.append("no_view_passed_strict_guards")
    elif safe_int(best.get("family_count")) < MIN_INDEPENDENT_FAMILIES and not strong_single_family:
        reasons.append("insufficient_independent_residual_consensus")
    elif safe_int(best.get("speaker_family_count")) < MIN_INDEPENDENT_FAMILIES and not strong_single_family:
        reasons.append("insufficient_independent_target_me_support")
    if not voice.model(source_start)[0]:
        reasons.append("insufficient_past_target_me_enrollment")
        accepted = False
    classification = "genuine_double_talk" if accepted else classify_rejection(view_rows, reasons)
    candidate = {
        **base,
        "kind": "causal_double_talk_me_candidate",
        "status": "accepted" if accepted else "rejected",
        "outcome": "accepted" if accepted else "rejected",
        "classification": classification,
        "reasons": reasons,
        "text": clean_text(best.get("text")),
        "remote_text": remote_text,
        "target_voice_localization": localization,
        "consensus": best,
        "consensus_clusters": clusters,
        "view_count": len(view_rows),
        "eligible_view_count": sum(row.get("view_status") == "eligible" for row in view_rows),
        "independent_evidence": {
            "acceptance_mode": (
                "multi_residual_family_consensus"
                if multi_family
                else "single_residual_plus_independent_voice_asr"
                if strong_single_family
                else "rejected"
            ),
            "residual_family_count": safe_int(best.get("family_count")),
            "target_me_family_count": safe_int(best.get("speaker_family_count")),
            "local_asr_consensus": bool(
                safe_int(best.get("family_count")) >= MIN_INDEPENDENT_FAMILIES
                or strong_single_family
            ),
            "remote_text_forbiddance": True,
            "remote_audio_forbiddance": True,
        },
        "causal_echo_training": {
            "status": "passed",
            "seconds": round(model.training_seconds, 3),
            "row_count": len(model.training_rows),
            "latest_end_sec": round(max(safe_float(row.get("end")) for row in model.training_rows), 3),
            "target_start_sec": round(start, 3),
            "past_only": all(safe_float(row.get("end")) <= start + 1.0e-6 for row in model.training_rows),
            "delay": model.delay_evidence,
        },
        "selection_mode": "recording_time_causal_double_talk_me_recovery_v1",
    }
    return candidate, view_rows


def render_report(state: dict[str, Any], candidates: list[dict[str, Any]]) -> str:
    lines = [
        "# Causal Double-Talk Me Recovery v1",
        "",
        f"- Status: `{state.get('status')}`",
        f"- Candidates: `{state.get('candidate_count')}`",
        f"- Accepted: `{state.get('accepted_candidate_count')}` / `{state.get('accepted_candidate_seconds')}` sec",
        f"- Timeline causal: `{str(state.get('timeline_causal')).lower()}`",
        f"- Batch fields used for selection: `{str(state.get('used_batch_fields_for_selection')).lower()}`",
        f"- Batch authoritative: `{str(state.get('batch_authoritative')).lower()}`",
        "",
        "## Outcomes",
        "",
    ]
    for row in candidates:
        lines.append(
            f"- `{row.get('start'):.3f}-{row.get('end'):.3f}` "
            f"`{row.get('status')}` `{row.get('classification')}`: "
            f"{clean_text(row.get('text')) or clean_text(row.get('source_text')) or '(no text)'}"
        )
        if row.get("reasons"):
            lines.append(f"  Reasons: `{', '.join(str(value) for value in row.get('reasons') or [])}`")
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the causal double-talk Me recovery shadow.")
    parser.add_argument("session", type=Path)
    parser.add_argument(
        "--corpus-rows",
        type=Path,
        help="Optional fixed-corpus JSONL; limits processing to its causal selection IDs.",
    )
    parser.add_argument(
        "--source-selection",
        type=Path,
        help="Causal selection JSONL. Defaults to the canonical local-island selection.",
    )
    parser.add_argument(
        "--evaluations",
        type=Path,
        help="Recording-time Target-Me evaluations JSONL.",
    )
    parser.add_argument(
        "--through-chunk-index",
        type=int,
        default=0,
        help="Use only closed current/past chunks through this index.",
    )
    parser.add_argument(
        "--from-chunk-index",
        type=int,
        default=1,
        help="Process groups from this closed chunk and merge with prior output.",
    )
    parser.add_argument(
        "--model",
        default=str(Path.home() / ".local/share/murmurmark/models/whisper.cpp/ggml-large-v3-q5_0.bin"),
    )
    parser.add_argument("--language", default="ru")
    parser.add_argument("--whisper-cli", default=os.environ.get("WHISPER_CLI", "whisper-cli"))
    parser.add_argument(
        "--faster-whisper-model",
        type=Path,
        default=Path(
            os.environ.get(
                "MURMURMARK_FASTER_WHISPER_MODEL",
                str(Path.home() / ".local/share/murmurmark/models/faster-whisper/large-v3"),
            )
        ),
    )
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--compute-type", default="int8")
    parser.add_argument(
        "--micro-asr-backend",
        choices=("faster-whisper", "whisper-cpp-cpu"),
        default="faster-whisper",
    )
    parser.add_argument("--view-profile", choices=("full", "runtime"), default="full")
    parser.add_argument("--runtime-shadow", action="store_true")
    parser.add_argument(
        "--cheap-prefilter-v1",
        action="store_true",
        help="Route every eligible source row before expensive residual and micro-ASR work.",
    )
    parser.add_argument(
        "--cheap-prefilter-decision-only",
        action="store_true",
        help="Write routing evidence without starting residual, Target-Me or micro-ASR work.",
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--output-dir", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    session = args.session.expanduser().resolve()
    if not session.exists():
        raise SystemExit(f"session not found: {session}")
    output = args.output_dir.expanduser().resolve() if args.output_dir else session / OUTPUT_RELATIVE
    output.mkdir(parents=True, exist_ok=True)
    progressive = load_script("live-progressive-target-me.py", "murmurmark_cdt_progressive")
    separation = load_script("live-causal-remote-active-me-separation.py", "murmurmark_cdt_separation")

    selection_path = (
        args.source_selection.expanduser().resolve()
        if args.source_selection
        else session / "derived/live/causal-local-island-micro-asr-v2/selection.jsonl"
    )
    evaluations_path = (
        args.evaluations.expanduser().resolve()
        if args.evaluations
        else session / "derived/live/causal-target-me/evaluations.jsonl"
    )
    chunks_path = session / "derived/live/chunks.jsonl"
    selections = read_jsonl(selection_path)
    evaluations = read_jsonl(evaluations_path)
    chunks_rows = read_jsonl(chunks_path)
    if args.through_chunk_index > 0:
        selections = [
            row
            for row in selections
            if safe_int(row.get("chunk_index")) <= args.through_chunk_index
        ]
        evaluations = [
            row
            for row in evaluations
            if safe_int(row.get("chunk_index")) <= args.through_chunk_index
        ]
        chunks_rows = [
            row
            for row in chunks_rows
            if safe_int(row.get("index")) <= args.through_chunk_index
        ]
    chunks = {safe_int(row.get("index")): row for row in chunks_rows}
    allowed = allowed_selection_ids(args.corpus_rows, session.name)
    selected, decisions = eligible_selections(selections, allowed)
    contract_eligible_count = len(selected)
    cheap_prefilter_decisions: list[dict[str, Any]] = []
    runtime_prefilter_rejected_count = 0
    if args.cheap_prefilter_v1:
        remote_text_by_id = {
            str(row.get("id")): remote_text_for_interval(
                session,
                chunks.get(safe_int(row.get("chunk_index"))) or {},
                safe_float(row.get("start")),
                safe_float(row.get("end")),
                progressive,
            )
            for row in selected
        }
        selected, cheap_prefilter_decisions = cheap_prefilter_route_rows(
            selected,
            remote_text_by_id=remote_text_by_id,
            session=session.name,
        )
        runtime_prefilter_rejected_count = contract_eligible_count - len(selected)
    elif args.runtime_shadow:
        processable = runtime_selection_scope(selected)
        runtime_prefilter_rejected_count = len(selected) - len(processable)
        selected = processable
    process_selected = (
        []
        if args.cheap_prefilter_decision_only
        else [
            row
            for row in selected
            if safe_int(row.get("chunk_index")) >= max(1, args.from_chunk_index)
        ]
    )
    groups = group_selections(process_selected)
    runtime_budget_skipped_group_count = 0
    if args.runtime_shadow:
        limited: list[list[dict[str, Any]]] = []
        chunk_indexes = sorted({safe_int(group[0].get("chunk_index")) for group in groups})
        for chunk_index in chunk_indexes:
            chunk_groups = [
                group for group in groups if safe_int(group[0].get("chunk_index")) == chunk_index
            ]
            chunk_groups.sort(key=runtime_group_priority, reverse=True)
            limited.extend(chunk_groups[:RUNTIME_MAX_GROUPS_PER_CHUNK])
            runtime_budget_skipped_group_count += max(
                0, len(chunk_groups) - RUNTIME_MAX_GROUPS_PER_CHUNK
            )
        groups = sorted(
            limited,
            key=lambda group: (
                safe_int(group[0].get("chunk_index")),
                safe_float(group[0].get("start")),
            ),
        )
    audio = separation.AudioStore(session, chunks)
    voice = PastTargetVoice(session, progressive) if groups else None
    if args.micro_asr_backend == "whisper-cpp-cpu":
        micro_asr: MicroASR = WhisperCppCPUMicroASR(
            Path(args.model),
            language=args.language,
            whisper_cli=args.whisper_cli,
            progressive=progressive,
        )
        micro_asr_state = {
            "backend": "whisper_cpp_cpu_large_v3_q5_0",
            "model": str(Path(args.model).expanduser()),
            "threads": 6,
            "gpu": False,
        }
    else:
        micro_asr = FasterWhisperMicroASR(
            args.faster_whisper_model,
            language=args.language,
            device=args.device,
            compute_type=args.compute_type,
        )
        micro_asr_state = {
            "backend": "faster_whisper_local_large_v3",
            "model": str(args.faster_whisper_model.expanduser()),
            "device": args.device,
            "compute_type": args.compute_type,
        }

    candidates: list[dict[str, Any]] = []
    views: list[dict[str, Any]] = []
    for group in groups:
        candidate, candidate_views = process_group(
            session=session,
            group=group,
            chunks=chunks,
            evaluations=evaluations,
            audio=audio,
            voice=voice,
            separation=separation,
            progressive=progressive,
            output=output,
            model_path=args.model,
            language=args.language,
            whisper_cli=args.whisper_cli,
            force=args.force,
            micro_asr=micro_asr,
            view_profile=args.view_profile,
        )
        candidates.append(candidate)
        views.extend(candidate_views)

    candidates = deduplicate_candidates(candidates, progressive)
    if args.runtime_shadow and not args.force:
        decisions = merge_rows(
            read_jsonl(output / "source_decisions.jsonl"),
            decisions,
            key="id",
        )
        views = merge_rows(
            read_jsonl(output / "residual_views.jsonl"),
            views,
            key="view_id",
        )
        candidates = merge_rows(
            read_jsonl(output / "candidates.jsonl"),
            candidates,
            key="id",
        )
        if args.cheap_prefilter_v1:
            cheap_prefilter_decisions = merge_rows(
                read_jsonl(output / "cheap_prefilter_decisions.jsonl"),
                cheap_prefilter_decisions,
                key="id",
            )

    accepted = [row for row in candidates if row.get("status") == "accepted"]
    classifications: dict[str, int] = {}
    for row in candidates:
        key = str(row.get("classification") or "unknown")
        classifications[key] = classifications.get(key, 0) + 1
    state = {
        "schema": SCHEMA,
        "generator": {"name": "live-causal-double-talk-me-recovery", "version": SCRIPT_VERSION},
        "created_at": now_iso(),
        "status": "completed",
        "profile": PROFILE,
        "session": session.name,
        "source_selection_count": len(selections),
        "eligible_source_selection_count": contract_eligible_count,
        "expensive_source_selection_count": len(selected),
        "runtime_prefilter_rejected_count": runtime_prefilter_rejected_count,
        "runtime_budget_skipped_group_count": runtime_budget_skipped_group_count,
        "source_group_count": len(groups),
        "processed_from_chunk_index": max(1, args.from_chunk_index),
        "candidate_count": len(candidates),
        "accepted_candidate_count": len(accepted),
        "accepted_candidate_seconds": round(sum(safe_float(row.get("duration_sec")) for row in accepted), 3),
        "residual_view_count": len(views),
        "classifications": classifications,
        "target_me_backend": (
            voice.backend_status
            if voice is not None
            else (read_json(output / "state.json").get("target_me_backend") or {"selected": "not_loaded_no_new_groups"})
        ),
        "micro_asr": micro_asr_state,
        "view_profile": args.view_profile,
        "runtime_shadow": args.runtime_shadow,
        "cheap_prefilter_v1": args.cheap_prefilter_v1,
        "cheap_prefilter_decision_only": args.cheap_prefilter_decision_only,
        "cheap_prefilter": {
            "decision_count": len(cheap_prefilter_decisions),
            "decision_fingerprint_sha256": (
                cheap_prefilter_fingerprint(cheap_prefilter_decisions)
                if cheap_prefilter_decisions
                else None
            ),
            "routes": {
                route: sum(row.get("route") == route for row in cheap_prefilter_decisions)
                for route in ("cheap_reject", "expensive_candidate", "unresolved")
            },
            "same_logic_offline_and_runtime": True,
            "evaluation_fields_used": False,
        },
        "through_chunk_index": args.through_chunk_index or max(chunks, default=0),
        "source_selection": str(selection_path),
        "recording_time_evaluations": str(evaluations_path),
        "causal_input_contract": {
            "timeline_causal": True,
            "selection_uses_committed_pcm": True,
            "selection_uses_live_asr_only": True,
            "past_only_target_me_enrollment": True,
            "batch_fields_forbidden": True,
        },
        "thresholds": {
            "min_asr_score": MIN_ASR_SCORE,
            "min_source_alignment": MIN_SOURCE_ALIGNMENT,
            "max_remote_text_similarity": MAX_REMOTE_TEXT_SIMILARITY,
            "max_remote_token_recall": MAX_REMOTE_TOKEN_RECALL,
            "max_remote_audio_strength": MAX_REMOTE_AUDIO_STRENGTH,
            "min_text_consensus": MIN_TEXT_CONSENSUS,
            "min_independent_families": MIN_INDEPENDENT_FAMILIES,
        },
        "timeline_causal": True,
        "used_batch_fields_for_selection": False,
        "batch_authoritative": True,
        "publication_allowed": False,
        "promotion_allowed": False,
        "outputs": {
            "source_decisions": str(output / "source_decisions.jsonl"),
            "cheap_prefilter_decisions": str(output / "cheap_prefilter_decisions.jsonl"),
            "residual_views": str(output / "residual_views.jsonl"),
            "candidates": str(output / "candidates.jsonl"),
            "report": str(output / "report.md"),
        },
    }
    write_jsonl(output / "source_decisions.jsonl", decisions)
    if args.cheap_prefilter_v1:
        write_jsonl(output / "cheap_prefilter_decisions.jsonl", cheap_prefilter_decisions)
    write_jsonl(output / "residual_views.jsonl", views)
    write_jsonl(output / "candidates.jsonl", candidates)
    write_json(output / "state.json", state)
    (output / "report.md").write_text(render_report(state, candidates), encoding="utf-8")
    print(f"causal double-talk recovery: {output / 'state.json'}")
    print(f"candidates: {len(candidates)}")
    print(f"accepted: {len(accepted)} ({state['accepted_candidate_seconds']:.3f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
