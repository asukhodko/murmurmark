#!/usr/bin/env python3
"""Freeze and evaluate a label-independent remote speaker boundary candidate."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import importlib.metadata
import itertools
import json
import math
import os
from pathlib import Path
import shutil
import sys
from typing import Any, Iterable
import warnings

import numpy as np
import soundfile as sf
from sklearn.cluster import AgglomerativeClustering
from sklearn.metrics import adjusted_rand_score


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = ROOT / "policies/remote-speaker-boundary-minority-v1.json"
DEFAULT_OUT = ROOT / "sessions/_reports/remote-speaker-boundary-minority-v1"
POLICY_SCHEMA = "murmurmark.remote_speaker_boundary_minority_policy/v1"
FREEZE_SCHEMA = "murmurmark.remote_speaker_boundary_minority_freeze/v1"
FEATURE_SCHEMA = "murmurmark.remote_speaker_boundary_feature/v1"
SEGMENT_SCHEMA = "murmurmark.remote_speaker_boundary_segment/v1"
REPORT_SCHEMA = "murmurmark.remote_speaker_boundary_minority_report/v1"
REPLAY_SCHEMA = "murmurmark.remote_speaker_boundary_minority_replay/v1"
MANIFEST_SCHEMA = "murmurmark.remote_speaker_boundary_minority_manifest/v1"
VERSION = "0.1.0"


class BoundaryError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode()


def pretty(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + "\n").encode()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(content)
    os.replace(temporary, path)


def write_json(path: Path, value: Any) -> None:
    atomic_write(path, pretty(value))


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    atomic_write(path, b"".join(canonical(row) for row in rows))


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BoundaryError(f"invalid_json:{portable(path)}") from error
    if not isinstance(value, dict):
        raise BoundaryError(f"json_object_required:{portable(path)}")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError("row is not an object")
            rows.append(value)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        raise BoundaryError(f"invalid_jsonl:{portable(path)}") from error
    return rows


def resolve(raw: str | Path) -> Path:
    path = Path(raw).expanduser()
    return path.resolve() if path.is_absolute() else (ROOT / path).resolve()


def portable(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve()))
    except ValueError:
        return f"external/{path.name}"


def artifact(path: Path) -> dict[str, Any]:
    return {"path": portable(path), "bytes": path.stat().st_size, "sha256": sha256(path)}


def stable_id(prefix: str, *values: Any) -> str:
    return f"{prefix}_{sha256_bytes(canonical(list(values)))[:16]}"


def load_policy(path: Path) -> dict[str, Any]:
    policy = read_json(path)
    if policy.get("schema") != POLICY_SCHEMA:
        raise BoundaryError("policy_schema_invalid")
    if policy.get("state") != "development_then_frozen_terminal_evaluation":
        raise BoundaryError("policy_state_invalid")
    candidate = policy.get("candidate") or {}
    if candidate.get("truth_identity_used_by_inference") is not False:
        raise BoundaryError("truth_identity_must_be_forbidden")
    if candidate.get("human_names_used_by_inference") is not False:
        raise BoundaryError("human_names_must_be_forbidden")
    if candidate.get("text_used_by_inference") is not False:
        raise BoundaryError("text_must_be_forbidden")
    if candidate.get("post_freeze_tuning_allowed") is not False:
        raise BoundaryError("post_freeze_tuning_must_be_forbidden")
    allowed = {"PROMOTE_SEGMENTATION", "KEEP_COVERAGE_V3", "EVIDENCE_BOUND"}
    if set((policy.get("decision") or {}).get("allowed_outcomes") or []) != allowed:
        raise BoundaryError("terminal_outcomes_changed")
    for source in (policy.get("sources") or {}).values():
        if isinstance(source, dict) and source.get("path"):
            path_value = resolve(str(source["path"]))
            if not path_value.is_file() or sha256(path_value) != source.get("sha256"):
                raise BoundaryError(f"frozen_source_changed:{portable(path_value)}")
    return policy


def normalize(vector: np.ndarray) -> np.ndarray:
    value = np.asarray(vector, dtype=np.float64).reshape(-1)
    norm = float(np.linalg.norm(value))
    if not math.isfinite(norm) or norm <= 1e-12:
        raise BoundaryError("invalid_signature")
    return value / norm


def cosine(left: np.ndarray, right: np.ndarray) -> float:
    return float(np.clip(np.dot(normalize(left), normalize(right)), -1.0, 1.0))


class AudioReader:
    def __init__(self, path: Path):
        self.path = path
        self.source = sf.SoundFile(path)
        self.sample_rate = int(self.source.samplerate)

    def close(self) -> None:
        self.source.close()

    def read(self, start: float, end: float) -> np.ndarray:
        first = max(0, int(round(start * self.sample_rate)))
        last = min(len(self.source), int(round(end * self.sample_rate)))
        if last <= first:
            return np.zeros(0, dtype=np.float32)
        self.source.seek(first)
        return self.source.read(last - first, dtype="float32", always_2d=True).mean(axis=1)


class SegmentSpeakerBackend:
    def __init__(self):
        try:
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", message="pkg_resources is deprecated as an API.*")
                import resemblyzer
                from resemblyzer import VoiceEncoder, preprocess_wav
        except (ImportError, ModuleNotFoundError) as error:
            raise BoundaryError(f"resemblyzer_runtime_missing:{type(error).__name__}") from error
        model = Path(resemblyzer.__file__).resolve().with_name("pretrained.pt")
        if not model.is_file():
            raise BoundaryError("resemblyzer_model_missing")
        self.encoder = VoiceEncoder(device="cpu", verbose=False, weights_fpath=model)
        self.preprocess = preprocess_wav
        self.provenance = {
            "backend": "resemblyzer",
            "package_version": importlib.metadata.version("resemblyzer"),
            "model_sha256": sha256(model),
        }
        self.cache: dict[tuple[str, float, float], np.ndarray | None] = {}

    def embed(self, path: Path, start: float, end: float) -> np.ndarray | None:
        cache_key = (str(path.resolve()), round(start, 6), round(end, 6))
        if cache_key in self.cache:
            return self.cache[cache_key]
        with sf.SoundFile(path) as source:
            first = max(0, int(round(start * source.samplerate)))
            last = min(len(source), int(round(end * source.samplerate)))
            if last <= first:
                return None
            source.seek(first)
            values = source.read(last - first, dtype="float32", always_2d=True).mean(axis=1)
            sample_rate = int(source.samplerate)
        minimum = int(round(1.2 * sample_rate))
        if len(values) < minimum:
            missing = minimum - len(values)
            values = np.pad(values, (missing // 2, missing - missing // 2))
        if not values.size or float(np.sqrt(np.mean(np.square(values), dtype=np.float64))) < 1e-7:
            self.cache[cache_key] = None
            return None
        prepared = self.preprocess(values, source_sr=sample_rate)
        if len(prepared) < 16_000:
            repeats = int(math.ceil(16_000 / max(1, len(prepared))))
            prepared = np.tile(prepared, repeats)[:16_000]
        result = normalize(self.encoder.embed_utterance(prepared))
        self.cache[cache_key] = result
        return result


_SEGMENT_BACKEND: SegmentSpeakerBackend | None = None


def segment_backend() -> SegmentSpeakerBackend:
    global _SEGMENT_BACKEND
    if _SEGMENT_BACKEND is None:
        _SEGMENT_BACKEND = SegmentSpeakerBackend()
    return _SEGMENT_BACKEND


def spectral_signature(values: np.ndarray, sample_rate: int, config: dict[str, Any]) -> np.ndarray | None:
    minimum = int(round(float(config["minimum_word_audio_sec"]) * sample_rate))
    if values.size < minimum:
        return None
    maximum = int(round(float(config["maximum_word_audio_sec"]) * sample_rate))
    if values.size > maximum:
        center = values.size // 2
        half = maximum // 2
        values = values[max(0, center - half) : max(0, center - half) + maximum]
    values = np.asarray(values, dtype=np.float64)
    values -= float(np.mean(values))
    rms = float(np.sqrt(np.mean(np.square(values))))
    if not math.isfinite(rms) or rms < 1e-6:
        return None
    values /= rms
    frame = max(64, int(round(float(config["frame_sec"]) * sample_rate)))
    hop = max(16, int(round(float(config["hop_sec"]) * sample_rate)))
    if values.size < frame:
        values = np.pad(values, (0, frame - values.size))
    count = 1 + max(0, (values.size - frame) // hop)
    window = np.hanning(frame)
    spectra = np.stack(
        [np.abs(np.fft.rfft(values[index * hop : index * hop + frame] * window)) for index in range(count)]
    )
    frequencies = np.fft.rfftfreq(frame, 1.0 / sample_rate)
    keep = (frequencies >= float(config["frequency_low_hz"])) & (
        frequencies <= min(float(config["frequency_high_hz"]), sample_rate / 2)
    )
    spectra = spectra[:, keep]
    if not spectra.size:
        return None
    bands = int(config["spectrum_bands"])
    chunks = np.array_split(np.arange(spectra.shape[1]), bands)
    compressed = np.stack([np.log1p(np.mean(spectra[:, indices], axis=1)) for indices in chunks if len(indices)], axis=1)
    mean = np.mean(compressed, axis=0)
    spread = np.std(compressed, axis=0)
    zcr = float(np.mean(np.abs(np.diff(np.signbit(values)).astype(np.float64))))
    signature = np.concatenate([mean, spread, np.asarray([math.log(rms + 1e-12), zcr])])
    return normalize(signature)


def word_signatures(audio: Path, words: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, np.ndarray]:
    reader = AudioReader(audio)
    result: dict[str, np.ndarray] = {}
    try:
        for row in words:
            start = float(row["start"])
            end = float(row["end"])
            values = reader.read(start, end)
            signature = spectral_signature(values, reader.sample_rate, config)
            if signature is not None:
                result[str(row["word_id"])] = signature
    finally:
        reader.close()
    return result


def rms(values: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(values), dtype=np.float64))) if values.size else 0.0


def boundary_energy_profile(
    audio: AudioReader, left: dict[str, Any], right: dict[str, Any]
) -> dict[str, Any]:
    gap = float(right["start"]) - float(left["end"])
    if gap <= 0:
        return {
            "energy_valley": 0.0,
            "left_speech_active": False,
            "right_speech_active": False,
            "boundary_silence_supported": False,
        }
    center = (float(left["end"]) + float(right["start"])) / 2
    valley = audio.read(center - min(0.08, gap / 2), center + min(0.08, gap / 2))
    left_audio = audio.read(max(float(left["start"]), float(left["end"]) - 0.12), float(left["end"]))
    right_audio = audio.read(float(right["start"]), min(float(right["end"]), float(right["start"]) + 0.12))
    left_rms = rms(left_audio)
    right_rms = rms(right_audio)
    valley_rms = rms(valley)
    speech = max(left_rms, right_rms, 1e-9)
    valley_score = float(np.clip(1.0 - valley_rms / speech, 0.0, 1.0))
    return {
        "energy_valley": round(valley_score, 6),
        "left_speech_active": left_rms >= 1e-5,
        "right_speech_active": right_rms >= 1e-5,
        "boundary_silence_supported": bool(
            left_rms >= 1e-5 and right_rms >= 1e-5 and valley_score >= 0.65 and gap >= 0.06
        ),
    }


def energy_valley(audio: AudioReader, left: dict[str, Any], right: dict[str, Any]) -> float:
    return float(boundary_energy_profile(audio, left, right)["energy_valley"])


def sanitized_words(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for index, row in enumerate(rows):
        try:
            start = float(row["start"])
            end = float(row["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if end <= start:
            continue
        result.append(
            {
                "word_id": str(row.get("word_id") or f"word_{index:06d}"),
                "utterance_id": str(row.get("utterance_id") or row.get("event_id") or "timeline"),
                "start": start,
                "end": end,
                "source_order": index,
            }
        )
    return result


def boundary_features(
    alias: str,
    audio_path: Path,
    words: list[dict[str, Any]],
    signatures: dict[str, np.ndarray],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    reader = AudioReader(audio_path)
    rows: list[dict[str, Any]] = []
    maximum_gap = float(config["maximum_adjacent_gap_sec"])
    try:
        for index in range(len(words) - 1):
            left, right = words[index], words[index + 1]
            gap = float(right["start"]) - float(left["end"])
            if gap < -0.02 or gap > maximum_gap:
                continue
            left_signature = signatures.get(str(left["word_id"]))
            right_signature = signatures.get(str(right["word_id"]))
            distance = None
            if left_signature is not None and right_signature is not None:
                distance = round(1.0 - cosine(left_signature, right_signature), 6)
            internal: list[float] = []
            if index > 0:
                previous = signatures.get(str(words[index - 1]["word_id"]))
                if previous is not None and left_signature is not None:
                    internal.append(1.0 - cosine(previous, left_signature))
            if index + 2 < len(words):
                following = signatures.get(str(words[index + 2]["word_id"]))
                if following is not None and right_signature is not None:
                    internal.append(1.0 - cosine(right_signature, following))
            neighbor_distance = float(np.median(internal)) if internal else 0.0
            contrast = round(distance - neighbor_distance, 6) if distance is not None else None
            time_value = round((float(left["end"]) + float(right["start"])) / 2, 6)
            energy = boundary_energy_profile(reader, left, right)
            rows.append(
                {
                    "schema": FEATURE_SCHEMA,
                    "boundary_id": stable_id("rsb", alias, left["word_id"], right["word_id"]),
                    "source_alias": alias,
                    "time": time_value,
                    "left_word_id": left["word_id"],
                    "right_word_id": right["word_id"],
                    "gap_sec": round(max(0.0, gap), 6),
                    "spectral_change_distance": distance,
                    "local_neighbor_distance": round(neighbor_distance, 6),
                    "change_contrast": contrast,
                    **energy,
                    "utterance_boundary": left["utterance_id"] != right["utterance_id"],
                    "evidence_available": distance is not None,
                }
            )
    finally:
        reader.close()
    return rows


def classify_boundary(row: dict[str, Any], parameters: dict[str, float]) -> bool:
    gap = float(row.get("gap_sec") or 0.0)
    if gap >= float(parameters["strong_pause_sec"]):
        return True
    distance = row.get("spectral_change_distance")
    contrast = row.get("change_contrast")
    if distance is None or contrast is None:
        return False
    acoustic = (
        float(distance) >= float(parameters["minimum_change_distance"])
        and float(contrast) >= float(parameters["minimum_change_contrast"])
    )
    valley_supported = bool(row.get("utterance_boundary") and row.get("boundary_silence_supported"))
    return acoustic or valley_supported


def truth_by_pair(words: list[dict[str, Any]]) -> dict[tuple[str, str], bool]:
    result = {}
    for left, right in zip(words, words[1:]):
        if float(right["start"]) < float(left["end"]) - 0.02:
            continue
        result[(str(left["word_id"]), str(right["word_id"]))] = (
            str(left.get("speaker_id")) != str(right.get("speaker_id"))
        )
    return result


def boundary_metrics(rows: list[dict[str, Any]], truth: dict[tuple[str, str], bool]) -> dict[str, Any]:
    tp = fp = fn = tn = 0
    for row in rows:
        key = (str(row["left_word_id"]), str(row["right_word_id"]))
        if key not in truth:
            continue
        predicted = bool(row.get("predicted_boundary"))
        expected = truth[key]
        if predicted and expected:
            tp += 1
        elif predicted:
            fp += 1
        elif expected:
            fn += 1
        else:
            tn += 1
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "true_negative": tn,
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "f1": round(f1, 6),
    }


def choose_parameters(policy: dict[str, Any], features: list[dict[str, Any]], truth: dict[tuple[str, str], bool]) -> tuple[dict[str, float], dict[str, Any]]:
    search = policy["candidate"]["threshold_search"]
    minimum_precision = float(search["minimum_development_precision"])
    trials = []
    for distance, contrast, pause in itertools.product(
        search["minimum_change_distance"], search["minimum_change_contrast"], search["strong_pause_sec"]
    ):
        parameters = {
            "minimum_change_distance": float(distance),
            "minimum_change_contrast": float(contrast),
            "strong_pause_sec": float(pause),
        }
        rows = [{**row, "predicted_boundary": classify_boundary(row, parameters)} for row in features]
        metrics = boundary_metrics(rows, truth)
        trials.append({"parameters": parameters, "metrics": metrics})
    eligible = [row for row in trials if row["metrics"]["precision"] >= minimum_precision]
    pool = eligible or trials
    selected = max(
        pool,
        key=lambda row: (
            row["metrics"]["f1"],
            row["metrics"]["recall"],
            row["metrics"]["precision"],
            row["parameters"]["minimum_change_distance"],
            row["parameters"]["minimum_change_contrast"],
            row["parameters"]["strong_pause_sec"],
        ),
    )
    return dict(selected["parameters"]), {
        "trial_count": len(trials),
        "eligible_trial_count": len(eligible),
        "selection": "maximum_f1_under_precision_floor",
        "selected_metrics": selected["metrics"],
    }


def build_segments(
    alias: str,
    audio_path: Path,
    words: list[dict[str, Any]],
    signatures: dict[str, np.ndarray],
    features: list[dict[str, Any]],
    parameters: dict[str, float],
    config: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, str], dict[str, Any]]:
    cuts = {
        (str(row["left_word_id"]), str(row["right_word_id"]))
        for row in features
        if classify_boundary(row, parameters)
    }
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for index, word in enumerate(words):
        current.append(word)
        if index == len(words) - 1 or (str(word["word_id"]), str(words[index + 1]["word_id"])) in cuts:
            groups.append(current)
            current = []
    vectors: list[np.ndarray] = []
    vector_group_indices: list[int] = []
    group_vectors: dict[int, np.ndarray] = {}
    within_segment_distances: list[float] = []
    backend = segment_backend()
    for index, group in enumerate(groups):
        start = float(group[0]["start"])
        end = float(group[-1]["end"])
        vector = backend.embed(audio_path, start, end)
        if vector is not None:
            group_vectors[index] = vector
            vectors.append(vector)
            vector_group_indices.append(index)
        calibration = config.get("adaptive_cluster_calibration") or {}
        minimum_half = float(calibration.get("minimum_half_sec") or 1.2)
        if calibration.get("enabled") and end - start >= 2 * minimum_half:
            midpoint = (start + end) / 2
            left = backend.embed(audio_path, start, midpoint)
            right = backend.embed(audio_path, midpoint, end)
            if left is not None and right is not None:
                within_segment_distances.append(1.0 - cosine(left, right))
    base_threshold = float(config["cluster_distance_threshold"])
    cluster_threshold = base_threshold
    calibration = config.get("adaptive_cluster_calibration") or {}
    if calibration.get("enabled") and within_segment_distances:
        quantile = float(config.get("adaptive_cluster_quantile") or calibration.get("quantile") or 0.75)
        margin = float(config.get("adaptive_cluster_margin") or calibration.get("margin") or 0.05)
        maximum = float(config.get("adaptive_cluster_maximum") or calibration.get("maximum_threshold") or 0.45)
        estimated = float(np.quantile(np.asarray(within_segment_distances), quantile)) + margin
        cluster_threshold = min(maximum, max(base_threshold, estimated))
    labels: dict[int, int] = {}
    if vectors:
        if len(vectors) == 1:
            raw = np.zeros(1, dtype=np.int64)
        else:
            raw = AgglomerativeClustering(
                n_clusters=None,
                distance_threshold=cluster_threshold,
                metric="cosine",
                linkage="average",
            ).fit_predict(np.stack(vectors))
        first_by_label = {
            int(label): min(vector_group_indices[pos] for pos, value in enumerate(raw) if int(value) == int(label))
            for label in set(int(value) for value in raw)
        }
        canonical_labels = {label: rank for rank, label in enumerate(sorted(first_by_label, key=first_by_label.get), 1)}
        labels = {group_index: canonical_labels[int(raw[position])] for position, group_index in enumerate(vector_group_indices)}
    cluster_sizes = Counter(labels.values())
    minimum_segments = int(config["minimum_cluster_segments"])
    segments = []
    word_partition: dict[str, str] = {}
    for index, group in enumerate(groups, 1):
        label = labels.get(index - 1)
        cluster_id = f"cluster_{label:03d}" if label is not None else None
        supported = label is not None and cluster_sizes[label] >= minimum_segments
        partition = cluster_id if supported else f"unknown_{index:04d}"
        for word in group:
            word_partition[str(word["word_id"])] = partition
        vector = group_vectors.get(index - 1)
        segments.append(
            {
                "schema": SEGMENT_SCHEMA,
                "segment_id": stable_id("rss", alias, index, group[0]["word_id"], group[-1]["word_id"]),
                "source_alias": alias,
                "start": round(float(group[0]["start"]), 6),
                "end": round(float(group[-1]["end"]), 6),
                "word_ids": [str(row["word_id"]) for row in group],
                "word_count": len(group),
                "anonymous_partition": partition,
                "candidate_cluster_id": cluster_id if supported else None,
                "status": "anonymous_cluster" if supported else "unknown",
                "signature_sha256": sha256_bytes(np.asarray(vector, dtype="<f4").tobytes()) if vector is not None else None,
            }
        )
    diagnostics = {
        "base_cluster_distance_threshold": round(base_threshold, 6),
        "effective_cluster_distance_threshold": round(cluster_threshold, 6),
        "within_segment_samples": len(within_segment_distances),
        "within_segment_distance_p50": round(float(np.quantile(within_segment_distances, 0.5)), 6)
        if within_segment_distances
        else None,
        "within_segment_distance_p90": round(float(np.quantile(within_segment_distances, 0.9)), 6)
        if within_segment_distances
        else None,
        "cluster_count": len(set(word_partition.values())),
        "unknown_segments": sum(row["status"] == "unknown" for row in segments),
    }
    return segments, word_partition, diagnostics


def conservation_metrics(
    words: list[dict[str, Any]], segments: list[dict[str, Any]], partition: dict[str, str]
) -> dict[str, Any]:
    expected = [str(row["word_id"]) for row in words]
    by_id = {str(row["word_id"]): row for row in words}
    observed = [str(word_id) for segment in segments for word_id in segment.get("word_ids") or []]
    timing_exact = all(
        segment.get("word_ids")
        and str(segment["word_ids"][0]) in by_id
        and str(segment["word_ids"][-1]) in by_id
        and float(segment["start"])
        == round(float(by_id[str(segment["word_ids"][0])]["start"]), 6)
        and float(segment["end"])
        == round(float(by_id[str(segment["word_ids"][-1])]["end"]), 6)
        for segment in segments
    )
    ids_exact = expected == observed
    partition_exact = set(partition) == set(expected)
    return {
        "expected_words": len(expected),
        "observed_words": len(observed),
        "word_ids_and_order_exact": ids_exact,
        "segment_boundary_timing_exact": timing_exact,
        "partition_coverage_exact": partition_exact,
        "score": 1.0 if ids_exact and timing_exact and partition_exact else 0.0,
    }


def unknown_metrics(segments: list[dict[str, Any]]) -> dict[str, Any]:
    total_words = sum(int(row.get("word_count") or 0) for row in segments)
    total_seconds = sum(max(0.0, float(row["end"]) - float(row["start"])) for row in segments)
    unknown = [row for row in segments if row.get("status") == "unknown"]
    unknown_words = sum(int(row.get("word_count") or 0) for row in unknown)
    unknown_seconds = sum(max(0.0, float(row["end"]) - float(row["start"])) for row in unknown)
    return {
        "segments": len(unknown),
        "words": unknown_words,
        "seconds": round(unknown_seconds, 6),
        "segment_ratio": round(len(unknown) / max(1, len(segments)), 6),
        "word_ratio": round(unknown_words / max(1, total_words), 6),
        "second_ratio": round(unknown_seconds / max(total_seconds, 1e-9), 6),
    }


def timing_shift_stability(
    alias: str,
    audio_path: Path,
    words: list[dict[str, Any]],
    baseline_features: list[dict[str, Any]],
    baseline_partition: dict[str, str],
    parameters: dict[str, float],
    config: dict[str, Any],
) -> dict[str, Any]:
    baseline_boundaries = {
        (str(row["left_word_id"]), str(row["right_word_id"])): bool(row.get("predicted_boundary"))
        for row in baseline_features
    }
    word_ids = [str(row["word_id"]) for row in words]
    cases = []
    for offset in (-0.08, 0.08):
        shifted = [
            {
                **row,
                "start": max(0.0, float(row["start"]) + offset),
                "end": max(0.001, float(row["end"]) + offset),
            }
            for row in words
        ]
        signatures = word_signatures(audio_path, shifted, config)
        features = boundary_features(f"{alias}_shift", audio_path, shifted, signatures, config)
        classified = [
            {**row, "predicted_boundary": classify_boundary(row, parameters)} for row in features
        ]
        _, partition, _ = build_segments(
            f"{alias}_shift", audio_path, shifted, signatures, classified, parameters, config
        )
        shifted_boundaries = {
            (str(row["left_word_id"]), str(row["right_word_id"])): bool(row.get("predicted_boundary"))
            for row in classified
        }
        common = sorted(set(baseline_boundaries) & set(shifted_boundaries))
        agreement = (
            sum(baseline_boundaries[key] == shifted_boundaries[key] for key in common) / len(common)
            if common
            else 1.0
        )
        common_words = [word_id for word_id in word_ids if word_id in baseline_partition and word_id in partition]
        ari = (
            float(
                adjusted_rand_score(
                    [baseline_partition[word_id] for word_id in common_words],
                    [partition[word_id] for word_id in common_words],
                )
            )
            if common_words
            else 1.0
        )
        cases.append(
            {
                "offset_sec": offset,
                "boundary_pairs": len(common),
                "boundary_agreement": round(agreement, 6),
                "partition_words": len(common_words),
                "partition_adjusted_rand": round(ari, 6),
            }
        )
    return {
        "cases": cases,
        "minimum_boundary_agreement": min(row["boundary_agreement"] for row in cases),
        "minimum_partition_adjusted_rand": min(row["partition_adjusted_rand"] for row in cases),
    }


def bcubed_metrics(truth: list[str], predicted: list[str]) -> dict[str, float]:
    if not truth:
        return {"precision": 0.0, "recall": 0.0, "f1": 0.0}
    truth_counts = Counter(truth)
    predicted_counts = Counter(predicted)
    intersections = Counter(zip(truth, predicted))
    precision = sum(intersections[(left, right)] / predicted_counts[right] for left, right in zip(truth, predicted)) / len(truth)
    recall = sum(intersections[(left, right)] / truth_counts[left] for left, right in zip(truth, predicted)) / len(truth)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": round(precision, 6), "recall": round(recall, 6), "f1": round(f1, 6)}


def pairwise_metrics(truth: list[str], predicted: list[str]) -> dict[str, Any]:
    truth_counts = Counter(truth)
    predicted_counts = Counter(predicted)
    intersections = Counter(zip(truth, predicted))
    pairs = lambda count: count * (count - 1) // 2
    tp = sum(pairs(count) for count in intersections.values())
    predicted_pairs = sum(pairs(count) for count in predicted_counts.values())
    truth_pairs = sum(pairs(count) for count in truth_counts.values())
    fp = predicted_pairs - tp
    fn = truth_pairs - tp
    precision = tp / predicted_pairs if predicted_pairs else 1.0
    recall = tp / truth_pairs if truth_pairs else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "true_positive_pairs": tp,
        "false_positive_pairs": fp,
        "false_negative_pairs": fn,
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "f1": round(f1, 6),
    }


def partition_metrics(
    truth_words: list[dict[str, Any]],
    partition: dict[str, str],
    minority_share: float,
) -> dict[str, Any]:
    usable = [
        row
        for row in truth_words
        if row.get("speaker_id") and not row.get("overlap_word_ids") and str(row["word_id"]) in partition
    ]
    truth = [str(row["speaker_id"]) for row in usable]
    predicted = [partition[str(row["word_id"])] for row in usable]
    truth_counts = Counter(truth)
    cluster_truth: dict[str, Counter[str]] = defaultdict(Counter)
    for expected, actual in zip(truth, predicted):
        cluster_truth[actual][expected] += 1
    dominant = {cluster: counts.most_common(1)[0][0] for cluster, counts in cluster_truth.items()}
    minority = {speaker for speaker, count in truth_counts.items() if count / max(1, len(truth)) <= minority_share}
    minority_words = sum(truth_counts[speaker] for speaker in minority)
    recovered = sum(
        1 for expected, actual in zip(truth, predicted) if expected in minority and dominant.get(actual) == expected
    )
    return {
        "word_count": len(truth),
        "true_speakers": len(truth_counts),
        "predicted_partitions": len(set(predicted)),
        "speaker_count_ratio": round(len(set(predicted)) / max(1, len(truth_counts)), 6),
        "bcubed": bcubed_metrics(truth, predicted),
        "pairwise": pairwise_metrics(truth, predicted),
        "minority_speakers": len(minority),
        "minority_words": minority_words,
        "minority_separated_words": recovered,
        "minority_speaker_recall": round(recovered / minority_words, 6) if minority_words else None,
    }


def controlled_root(policy: dict[str, Any]) -> Path:
    manifest = resolve(policy["sources"]["controlled_truth_manifest"]["path"])
    return manifest.parent / "sessions"


def controlled_scenarios(policy: dict[str, Any], splits: set[str]) -> list[dict[str, Any]]:
    manifest_path = resolve(policy["sources"]["controlled_truth_manifest"]["path"])
    manifest = read_json(manifest_path)
    root = controlled_root(policy)
    rows = []
    for summary in manifest.get("scenario_summaries") or []:
        if str(summary.get("split")) not in splits:
            continue
        scenario_id = str(summary["scenario_id"])
        base = root / str(summary["split"]) / scenario_id
        audio = base / "mixture.wav"
        words = base / "truth_words.jsonl"
        boundaries = base / "truth_boundaries.jsonl"
        for path in (audio, words, boundaries):
            expected = (manifest.get("artifacts") or {}).get(str(path.relative_to(manifest_path.parent)))
            if not path.is_file() or expected != sha256(path):
                raise BoundaryError(f"controlled_artifact_changed:{scenario_id}:{path.name}")
        rows.append({"scenario_id": scenario_id, "split": summary["split"], "audio": audio, "words": words, "boundaries": boundaries})
    return rows


def prepare_controlled(policy: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[tuple[str, str], bool], list[dict[str, Any]]]:
    splits = set(policy["development"]["controlled_splits"])
    development_features = []
    development_truth: dict[tuple[str, str], bool] = {}
    provenance = []
    for source in controlled_scenarios(policy, splits):
        truth_words = read_jsonl(source["words"])
        inference_words = sanitized_words(truth_words)
        signatures = word_signatures(source["audio"], inference_words, policy["candidate"])
        features = boundary_features(source["scenario_id"], source["audio"], inference_words, signatures, policy["candidate"])
        if source["split"] == policy["development"]["threshold_selection_split"]:
            development_features.extend(features)
            development_truth.update(truth_by_pair(truth_words))
        provenance.append(
            {
                "source_alias": stable_id("development", source["scenario_id"]),
                "split": source["split"],
                "words": len(inference_words),
                "features": len(features),
                "audio_sha256": sha256(source["audio"]),
                "word_timing_sha256": sha256(source["words"]),
            }
        )
    return development_features, development_truth, provenance


def choose_cluster_threshold(
    policy: dict[str, Any], parameters: dict[str, float]
) -> tuple[dict[str, float], dict[str, Any]]:
    split = str(policy["development"]["threshold_selection_split"])
    bundles = []
    for source in controlled_scenarios(policy, {split}):
        truth_words = read_jsonl(source["words"])
        inference_words = sanitized_words(truth_words)
        signatures = word_signatures(source["audio"], inference_words, policy["candidate"])
        features = boundary_features(
            source["scenario_id"], source["audio"], inference_words, signatures, policy["candidate"]
        )
        classified = [
            {**row, "predicted_boundary": classify_boundary(row, parameters)} for row in features
        ]
        bundles.append(
            (
                source["scenario_id"],
                source["audio"],
                truth_words,
                inference_words,
                signatures,
                classified,
            )
        )
    trials = []
    calibration = policy["candidate"]["adaptive_cluster_calibration"]
    combinations = itertools.product(
        policy["candidate"]["cluster_distance_threshold_search"],
        calibration["quantile_search"],
        calibration["margin_search"],
        calibration["maximum_threshold_search"],
    )
    for threshold, quantile, margin, maximum in combinations:
        config = {
            **policy["candidate"],
            "cluster_distance_threshold": float(threshold),
            "adaptive_cluster_quantile": float(quantile),
            "adaptive_cluster_margin": float(margin),
            "adaptive_cluster_maximum": float(maximum),
        }
        truth_all: list[str] = []
        predicted_all: list[str] = []
        ratios = []
        effective_thresholds = []
        for scenario_id, audio_path, truth_words, inference_words, signatures, classified in bundles:
            _, partition, diagnostics = build_segments(
                scenario_id,
                audio_path,
                inference_words,
                signatures,
                classified,
                parameters,
                config,
            )
            usable = [
                row
                for row in truth_words
                if row.get("speaker_id")
                and not row.get("overlap_word_ids")
                and str(row["word_id"]) in partition
            ]
            expected = [str(row["speaker_id"]) for row in usable]
            actual = [f"{scenario_id}:{partition[str(row['word_id'])]}" for row in usable]
            truth_all.extend(expected)
            predicted_all.extend(actual)
            ratios.append(len(set(actual)) / max(1, len(set(expected))))
            effective_thresholds.append(diagnostics["effective_cluster_distance_threshold"])
        bcubed = bcubed_metrics(truth_all, predicted_all)
        pairwise = pairwise_metrics(truth_all, predicted_all)
        mean_ratio = float(np.mean(ratios)) if ratios else 0.0
        trials.append(
            {
                "parameters": {
                    "cluster_distance_threshold": float(threshold),
                    "adaptive_cluster_quantile": float(quantile),
                    "adaptive_cluster_margin": float(margin),
                    "adaptive_cluster_maximum": float(maximum),
                },
                "bcubed": bcubed,
                "pairwise": pairwise,
                "mean_speaker_count_ratio": round(mean_ratio, 6),
                "mean_effective_threshold": round(float(np.mean(effective_thresholds)), 6),
            }
        )
    selected = max(
        trials,
        key=lambda row: (
            row["bcubed"]["f1"],
            row["pairwise"]["precision"],
            -abs(row["mean_speaker_count_ratio"] - 1.0),
            row["parameters"]["cluster_distance_threshold"],
            row["parameters"]["adaptive_cluster_quantile"],
            row["parameters"]["adaptive_cluster_margin"],
            row["parameters"]["adaptive_cluster_maximum"],
        ),
    )
    return dict(selected["parameters"]), {
        "trial_count": len(trials),
        "selection": "maximum_development_bcubed_then_pairwise_precision",
        "selected": selected,
    }


def selected_real_source(policy: dict[str, Any]) -> tuple[dict[str, Any], Path, Path, Path, dict[str, Any]]:
    registry_path = resolve(policy["sources"]["real_reference_registry"]["path"])
    registry = read_json(registry_path)
    target = str(policy["sources"]["terminal_real_reference_parsed_sha256"])
    row = next((value for value in registry.get("sources") or [] if (value.get("parsed") or {}).get("sha256") == target), None)
    if row is None:
        raise BoundaryError("terminal_real_reference_not_registered")
    session = ROOT / "sessions" / str(row["session"])
    selection_path = session / "derived/transcript-rich/speaker-resolved-default-v1/selection.json"
    selection = read_json(selection_path)
    if selection.get("state") != "selected":
        raise BoundaryError("terminal_real_selection_not_selected")

    def selected_path(key: str) -> Path:
        expected = selection.get(key) or {}
        path = session / str(expected.get("path") or "")
        if not path.is_file() or path.stat().st_size != int(expected.get("bytes") or -1) or sha256(path) != expected.get("sha256"):
            raise BoundaryError(f"terminal_selected_artifact_changed:{key}")
        return path

    rich_path = selected_path("rich_transcript")
    coverage_path = selected_path("coverage_report")
    rich = read_json(rich_path)
    coverage = read_json(coverage_path)
    remote_row = (coverage.get("source") or {}).get("remote_audio") or {}
    remote_audio = session / str(remote_row.get("path") or "")
    if not remote_audio.is_file() or remote_audio.stat().st_size != int(remote_row.get("bytes") or -1) or sha256(remote_audio) != remote_row.get("sha256"):
        raise BoundaryError("terminal_real_remote_audio_changed")
    parsed = registry_path.parent / "sources" / str(row["source_id"]) / "parsed.json"
    if not parsed.is_file() or sha256(parsed) != target:
        raise BoundaryError("terminal_real_reference_changed")
    return row, session, rich_path, remote_audio, {"selection": selection_path, "coverage": coverage_path, "parsed": parsed, "rich": rich}


def real_words(rich: dict[str, Any]) -> list[dict[str, Any]]:
    utterance_order = {
        str(row.get("id")): index
        for index, row in enumerate(rich.get("utterances") or [])
        if row.get("role") == "remote" and row.get("id")
    }
    rows = [row for row in rich.get("remote_word_attributions") or [] if str(row.get("utterance_id")) in utterance_order]
    rows.sort(key=lambda row: (utterance_order[str(row["utterance_id"])], float(row.get("start") or 0), str(row.get("word_id") or "")))
    return sanitized_words(rows)


def action_prepare(policy_path: Path, policy: dict[str, Any], out: Path) -> int:
    shutil.rmtree(out / "private", ignore_errors=True)
    for name in ("freeze_manifest.json", "report.json", "report.md", "replay_report.json", "artifact_manifest.json"):
        (out / name).unlink(missing_ok=True)
    dev_features, dev_truth, development_sources = prepare_controlled(policy)
    parameters, tuning = choose_parameters(policy, dev_features, dev_truth)
    cluster_parameters, cluster_tuning = choose_cluster_threshold(policy, parameters)
    real_row, session, rich_path, remote_audio, selected = selected_real_source(policy)
    rich = selected["rich"]
    words = real_words(rich)
    signatures = word_signatures(remote_audio, words, policy["candidate"])
    features = boundary_features("terminal_real", remote_audio, words, signatures, policy["candidate"])
    classified = [{**row, "predicted_boundary": classify_boundary(row, parameters)} for row in features]
    candidate_config = {**policy["candidate"], **cluster_parameters}
    segments, partition, segment_diagnostics = build_segments(
        "terminal_real", remote_audio, words, signatures, classified, parameters, candidate_config
    )
    segment_diagnostics = {
        **segment_diagnostics,
        "conservation": conservation_metrics(words, segments, partition),
        "unknown": unknown_metrics(segments),
        "timing_shift_stability": timing_shift_stability(
            "terminal_real",
            remote_audio,
            words,
            classified,
            partition,
            parameters,
            candidate_config,
        ),
    }
    input_manifest = {
        "schema": "murmurmark.remote_speaker_boundary_minority_input/v1",
        "version": VERSION,
        "policy": artifact(policy_path),
        "development_sources": development_sources,
        "development_truth_read": True,
        "terminal_controlled_truth_read": False,
        "terminal_real_reference_read": False,
        "terminal_real": {
            "session_id": session.name,
            "selection": artifact(selected["selection"]),
            "rich": artifact(rich_path),
            "coverage": artifact(selected["coverage"]),
            "remote_audio": artifact(remote_audio),
            "reference_parsed_sha256": (real_row.get("parsed") or {}).get("sha256"),
            "reference_content_read": False,
        },
        "production_guards": [
            artifact(selected["selection"]), artifact(rich_path), artifact(selected["coverage"]), artifact(remote_audio)
        ],
    }
    candidate = {
        "schema": "murmurmark.remote_speaker_boundary_minority_candidate/v1",
        "candidate_id": policy["candidate"]["id"],
        "parameters": parameters,
        "tuning": {"boundary": tuning, "clustering": cluster_tuning},
        **cluster_parameters,
        "minimum_cluster_segments": policy["candidate"]["minimum_cluster_segments"],
        "inference_fields": ["word_id", "utterance_id", "start", "end", "audio_pcm"],
        "forbidden_inference_fields": ["text", "speaker_id", "speaker_label", "human_name", "truth_outcome"],
        "post_freeze_tuning_allowed": False,
    }
    write_json(out / "private/input_manifest.json", input_manifest)
    write_json(out / "private/candidate.pending.json", candidate)
    write_jsonl(out / "private/real_boundary_features.pending.jsonl", classified)
    write_jsonl(out / "private/real_segments.pending.jsonl", segments)
    write_json(out / "private/real_segment_diagnostics.pending.json", segment_diagnostics)
    write_json(
        out / "private/real_word_partition.pending.json",
        {"schema": "murmurmark.remote_speaker_boundary_word_partition/v1", "partition": partition},
    )
    print(f"prepared: development_features={len(dev_features)} real_words={len(words)} real_segments={len(segments)}")
    print(f"parameters: {json.dumps(parameters, sort_keys=True)}")
    return 0


def frozen_paths(out: Path) -> list[Path]:
    return [
        out / "private/input_manifest.json",
        out / "private/candidate.frozen.json",
        out / "private/real_boundary_features.frozen.jsonl",
        out / "private/real_segments.frozen.jsonl",
        out / "private/real_segment_diagnostics.frozen.json",
        out / "private/real_word_partition.frozen.json",
    ]


def action_freeze(policy_path: Path, policy: dict[str, Any], out: Path) -> int:
    pairs = [
        (out / "private/candidate.pending.json", out / "private/candidate.frozen.json"),
        (out / "private/real_boundary_features.pending.jsonl", out / "private/real_boundary_features.frozen.jsonl"),
        (out / "private/real_segments.pending.jsonl", out / "private/real_segments.frozen.jsonl"),
        (
            out / "private/real_segment_diagnostics.pending.json",
            out / "private/real_segment_diagnostics.frozen.json",
        ),
        (out / "private/real_word_partition.pending.json", out / "private/real_word_partition.frozen.json"),
    ]
    if not (out / "private/input_manifest.json").is_file() or any(not source.is_file() for source, _ in pairs):
        raise BoundaryError("prepare_must_run_before_freeze")
    if (out / "report.json").exists():
        raise BoundaryError("terminal_evaluation_exists; rerun prepare before freeze")
    for source, target in pairs:
        atomic_write(target, source.read_bytes())
    manifest = {
        "schema": FREEZE_SCHEMA,
        "version": VERSION,
        "state": "frozen_before_terminal_evaluation",
        "policy": artifact(policy_path),
        "implementation": artifact(Path(__file__).resolve()),
        "artifacts": [artifact(path) for path in frozen_paths(out)],
        "terminal_controlled_truth_read": False,
        "terminal_real_reference_read": False,
        "post_freeze_tuning_allowed": False,
        "production_mutations": 0,
    }
    write_json(out / "freeze_manifest.json", manifest)
    print(f"frozen: {sha256(out / 'private/candidate.frozen.json')}")
    return 0


def verify_freeze(policy_path: Path, out: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = read_json(out / "freeze_manifest.json")
    if manifest.get("schema") != FREEZE_SCHEMA or manifest.get("state") != "frozen_before_terminal_evaluation":
        raise BoundaryError("freeze_manifest_invalid")
    if manifest.get("policy", {}).get("sha256") != sha256(policy_path):
        raise BoundaryError("frozen_policy_changed")
    implementation = manifest.get("implementation") or {}
    if implementation.get("sha256") != sha256(Path(__file__).resolve()):
        raise BoundaryError("frozen_implementation_changed")
    for row in manifest.get("artifacts") or []:
        path = resolve(str(row["path"]))
        if not path.is_file() or path.stat().st_size != int(row["bytes"]) or sha256(path) != row["sha256"]:
            raise BoundaryError(f"frozen_artifact_changed:{row['path']}")
    candidate = read_json(out / "private/candidate.frozen.json")
    return manifest, candidate


def evaluate_controlled_terminal(policy: dict[str, Any], candidate: dict[str, Any], out: Path) -> dict[str, Any]:
    scenarios = []
    totals = Counter()
    all_truth: list[str] = []
    all_predicted: list[str] = []
    minority_weighted = [0, 0]
    conservation_scores: list[float] = []
    shift_boundary_agreements: list[float] = []
    shift_partition_agreements: list[float] = []
    unknown_words = 0
    total_words = 0
    for source in controlled_scenarios(policy, {policy["terminal"]["controlled_split"]}):
        truth_words = read_jsonl(source["words"])
        inference_words = sanitized_words(truth_words)
        signatures = word_signatures(source["audio"], inference_words, policy["candidate"])
        features = boundary_features(source["scenario_id"], source["audio"], inference_words, signatures, policy["candidate"])
        classified = [{**row, "predicted_boundary": classify_boundary(row, candidate["parameters"])} for row in features]
        candidate_config = {
            **policy["candidate"],
            "cluster_distance_threshold": float(candidate["cluster_distance_threshold"]),
            "adaptive_cluster_quantile": float(candidate["adaptive_cluster_quantile"]),
            "adaptive_cluster_margin": float(candidate["adaptive_cluster_margin"]),
            "adaptive_cluster_maximum": float(candidate["adaptive_cluster_maximum"]),
        }
        segments, partition, segment_diagnostics = build_segments(
            source["scenario_id"],
            source["audio"],
            inference_words,
            signatures,
            classified,
            candidate["parameters"],
            candidate_config,
        )
        boundary = boundary_metrics(classified, truth_by_pair(truth_words))
        partition_result = partition_metrics(truth_words, partition, float(policy["terminal"]["minority_max_word_share"]))
        conservation = conservation_metrics(inference_words, segments, partition)
        unknown = unknown_metrics(segments)
        stability = timing_shift_stability(
            source["scenario_id"],
            source["audio"],
            inference_words,
            classified,
            partition,
            candidate["parameters"],
            candidate_config,
        )
        for key in ("true_positive", "false_positive", "false_negative", "true_negative"):
            totals[key] += boundary[key]
        usable = [row for row in truth_words if row.get("speaker_id") and not row.get("overlap_word_ids") and str(row["word_id"]) in partition]
        all_truth.extend(str(row["speaker_id"]) for row in usable)
        all_predicted.extend(f"{source['scenario_id']}:{partition[str(row['word_id'])]}" for row in usable)
        minority_weighted[0] += int(partition_result["minority_separated_words"])
        minority_weighted[1] += int(partition_result["minority_words"])
        conservation_scores.append(float(conservation["score"]))
        shift_boundary_agreements.append(float(stability["minimum_boundary_agreement"]))
        shift_partition_agreements.append(float(stability["minimum_partition_adjusted_rand"]))
        unknown_words += int(unknown["words"])
        total_words += sum(int(row.get("word_count") or 0) for row in segments)
        scenarios.append(
            {
                "source_alias": stable_id("hard", source["scenario_id"]),
                "words": len(truth_words),
                "segments": len(segments),
                "segmentation_diagnostics": segment_diagnostics,
                "conservation": conservation,
                "unknown": unknown,
                "timing_shift_stability": stability,
                "boundary": boundary,
                "partition": partition_result,
            }
        )
    precision = totals["true_positive"] / max(1, totals["true_positive"] + totals["false_positive"])
    recall = totals["true_positive"] / max(1, totals["true_positive"] + totals["false_negative"])
    return {
        "scenarios": scenarios,
        "boundary": {
            **dict(totals),
            "precision": round(precision, 6),
            "recall": round(recall, 6),
            "f1": round(2 * precision * recall / (precision + recall), 6) if precision + recall else 0.0,
        },
        "bcubed": bcubed_metrics(all_truth, all_predicted),
        "pairwise": pairwise_metrics(all_truth, all_predicted),
        "minority_speaker_recall": round(minority_weighted[0] / minority_weighted[1], 6) if minority_weighted[1] else None,
        "unknown_word_ratio": round(unknown_words / max(1, total_words), 6),
        "timing_shift_stability": {
            "minimum_boundary_agreement": min(shift_boundary_agreements, default=1.0),
            "minimum_partition_adjusted_rand": min(shift_partition_agreements, default=1.0),
        },
        "word_conservation": min(conservation_scores, default=0.0),
    }


def overlap(start: float, end: float, left: float, right: float) -> float:
    return max(0.0, min(end, right) - max(start, left))


def real_reference_metrics(policy: dict[str, Any], out: Path) -> dict[str, Any]:
    real_row, _, _, _, selected = selected_real_source(policy)
    parsed = read_json(selected["parsed"])
    evaluation = selected["parsed"].parents[2] / "evaluations" / str(real_row["source_id"]) / "evaluation.json"
    if not evaluation.is_file():
        raise BoundaryError("terminal_real_alignment_missing")
    evaluation_payload = read_json(evaluation)
    offset = float(evaluation_payload["offset_sec"])
    reference = [row for row in parsed.get("entries") or [] if row.get("role") == "remote"]
    reference.sort(key=lambda row: (float(row["start"]), float(row["end"]), str(row.get("speaker") or "")))
    truth_boundaries = []
    evaluable_pairs = []
    for left, right in zip(reference, reference[1:]):
        left_end = float(left["end"]) + offset
        right_start = float(right["start"]) + offset
        if right_start < left_end - 0.05 or right_start - left_end > 4.0:
            continue
        changed = str(left.get("speaker")) != str(right.get("speaker"))
        time_value = (left_end + right_start) / 2
        evaluable_pairs.append((time_value, changed, str(left.get("speaker")), str(right.get("speaker"))))
        if changed:
            truth_boundaries.append(time_value)
    features = read_jsonl(out / "private/real_boundary_features.frozen.jsonl")
    predicted = sorted(float(row["time"]) for row in features if row.get("predicted_boundary"))
    tolerance = float(policy["terminal"]["boundary_tolerance_sec"])
    used: set[int] = set()
    matched_truth = 0
    for truth_time in truth_boundaries:
        candidates = [(abs(value - truth_time), index) for index, value in enumerate(predicted) if index not in used and abs(value - truth_time) <= tolerance]
        if candidates:
            _, index = min(candidates)
            used.add(index)
            matched_truth += 1
    reference_start = min(float(row["start"]) + offset for row in reference)
    reference_end = max(float(row["end"]) + offset for row in reference)
    scoped_predicted = [value for value in predicted if reference_start <= value <= reference_end]
    true_positive = len([index for index in used if reference_start <= predicted[index] <= reference_end])
    precision = true_positive / len(scoped_predicted) if scoped_predicted else 1.0
    recall = matched_truth / len(truth_boundaries) if truth_boundaries else 1.0

    speaker_words = Counter()
    for row in reference:
        speaker_words[str(row.get("speaker"))] += len(str(row.get("text") or "").split())
    total_words = sum(speaker_words.values())
    minority = {speaker for speaker, count in speaker_words.items() if count / max(1, total_words) <= float(policy["terminal"]["minority_max_word_share"])}
    minority_boundaries = [time_value for time_value, changed, left, right in evaluable_pairs if changed and (left in minority or right in minority)]
    minority_matched = sum(any(abs(value - truth_time) <= tolerance for value in predicted) for truth_time in minority_boundaries)

    segments = read_jsonl(out / "private/real_segments.frozen.jsonl")
    segment_diagnostics = read_json(out / "private/real_segment_diagnostics.frozen.json")
    aligned_truth: list[str] = []
    aligned_predicted: list[str] = []
    cluster_truth: dict[str, Counter[str]] = defaultdict(Counter)
    for row in reference:
        start = float(row["start"]) + offset
        end = float(row["end"]) + offset
        candidates = [(overlap(start, end, float(segment["start"]), float(segment["end"])), segment) for segment in segments]
        amount, segment = max(candidates, key=lambda value: value[0], default=(0.0, None))
        if amount <= 0 or segment is None:
            continue
        weight = max(1, len(str(row.get("text") or "").split()))
        truth = str(row.get("speaker"))
        predicted_label = str(segment["anonymous_partition"])
        aligned_truth.extend([truth] * weight)
        aligned_predicted.extend([predicted_label] * weight)
        cluster_truth[predicted_label][truth] += weight
    dominant = {cluster: counts.most_common(1)[0][0] for cluster, counts in cluster_truth.items()}
    minority_words = sum(speaker_words[speaker] for speaker in minority)
    minority_separated = sum(
        1 for truth, predicted_label in zip(aligned_truth, aligned_predicted) if truth in minority and dominant.get(predicted_label) == truth
    )
    return {
        "trust_grade": real_row["trust_grade"],
        "reference_speakers": len(speaker_words),
        "candidate_partitions": len(set(aligned_predicted)),
        "speaker_count_ratio": round(len(set(aligned_predicted)) / max(1, len(speaker_words)), 6),
        "boundary": {
            "reference_boundaries": len(truth_boundaries),
            "candidate_boundaries": len(scoped_predicted),
            "matched_boundaries": matched_truth,
            "precision": round(precision, 6),
            "recall": round(recall, 6),
            "f1": round(2 * precision * recall / (precision + recall), 6) if precision + recall else 0.0,
        },
        "minority_boundary_count": len(minority_boundaries),
        "minority_boundary_recall": round(minority_matched / len(minority_boundaries), 6) if minority_boundaries else None,
        "bcubed": bcubed_metrics(aligned_truth, aligned_predicted),
        "pairwise": pairwise_metrics(aligned_truth, aligned_predicted),
        "minority_reference_speakers": len(minority),
        "minority_reference_words": minority_words,
        "minority_separated_words": minority_separated,
        "minority_speaker_recall": round(minority_separated / minority_words, 6) if minority_words else None,
        "coverage_v3_baseline": (evaluation_payload.get("metrics") or {}),
        "unknown": segment_diagnostics["unknown"],
        "timing_shift_stability": segment_diagnostics["timing_shift_stability"],
        "conservation": segment_diagnostics["conservation"],
        "word_conservation": float(segment_diagnostics["conservation"]["score"]),
    }


def production_guards_unchanged(out: Path) -> bool:
    manifest = read_json(out / "private/input_manifest.json")
    for row in manifest.get("production_guards") or []:
        path = resolve(str(row["path"]))
        if not path.is_file() or path.stat().st_size != int(row["bytes"]) or sha256(path) != row["sha256"]:
            return False
    return True


def build_report(policy: dict[str, Any], candidate: dict[str, Any], controlled: dict[str, Any], real: dict[str, Any], guards: bool) -> dict[str, Any]:
    gates = policy["gates"]
    hard_gates = {
        "boundary_precision": controlled["boundary"]["precision"] >= float(gates["minimum_hard_boundary_precision"]),
        "boundary_recall": controlled["boundary"]["recall"] >= float(gates["minimum_hard_boundary_recall"]),
        "bcubed_f1": controlled["bcubed"]["f1"] >= float(gates["minimum_hard_bcubed_f1"]),
        "pairwise_precision": controlled["pairwise"]["precision"] >= float(gates["minimum_hard_pairwise_precision"]),
        "minority_recall": controlled["minority_speaker_recall"] is not None and controlled["minority_speaker_recall"] >= float(gates["minimum_hard_minority_recall"]),
        "timing_shift_boundary_agreement": controlled["timing_shift_stability"]["minimum_boundary_agreement"]
        >= float(gates["minimum_hard_timing_shift_boundary_agreement"]),
        "timing_shift_partition_adjusted_rand": controlled["timing_shift_stability"]["minimum_partition_adjusted_rand"]
        >= float(gates["minimum_hard_timing_shift_partition_adjusted_rand"]),
        "unknown_word_ratio": controlled["unknown_word_ratio"] <= float(gates["maximum_hard_unknown_word_ratio"]),
        "word_conservation": controlled["word_conservation"] == float(gates["required_word_conservation"]),
    }
    real_gates = {
        "boundary_precision": real["boundary"]["precision"] >= float(gates["minimum_real_boundary_precision"]),
        "boundary_recall": real["boundary"]["recall"] >= float(gates["minimum_real_boundary_recall"]),
        "minority_boundary_recall": real["minority_boundary_recall"] is not None and real["minority_boundary_recall"] >= float(gates["minimum_real_minor_boundary_recall"]),
        "speaker_count_floor": real["speaker_count_ratio"] >= float(gates["minimum_real_speaker_count_ratio"]),
        "speaker_count_ceiling": real["speaker_count_ratio"] <= float(gates["maximum_real_speaker_count_ratio"]),
        "timing_shift_boundary_agreement": real["timing_shift_stability"]["minimum_boundary_agreement"]
        >= float(gates["minimum_real_timing_shift_boundary_agreement"]),
        "timing_shift_partition_adjusted_rand": real["timing_shift_stability"]["minimum_partition_adjusted_rand"]
        >= float(gates["minimum_real_timing_shift_partition_adjusted_rand"]),
        "unknown_word_ratio": real["unknown"]["word_ratio"] <= float(gates["maximum_real_unknown_word_ratio"]),
        "word_conservation": real["word_conservation"] == float(gates["required_word_conservation"]),
    }
    safety = {
        "production_guards_unchanged": guards,
        "production_mutations": 0,
        "candidate_frozen_before_terminal": True,
        "truth_identity_used_by_inference": False,
        "forced_identity": False,
        "public_private_content": False,
    }
    hard_passed = all(hard_gates.values())
    real_passed = all(real_gates.values())
    human_real = real["trust_grade"] == "human_reviewed"
    if not guards:
        decision = "EVIDENCE_BOUND"
        reason = "production guard verification failed"
    elif not hard_passed or not real_passed:
        decision = "KEEP_COVERAGE_V3"
        reason = "the frozen candidate did not pass controlled-hard and operational diagnostic gates"
    elif policy["decision"]["promotion_requires_human_reviewed_real_boundary_truth"] and not human_real:
        decision = "EVIDENCE_BOUND"
        reason = "candidate gates passed, but real boundary truth is an independent machine diagnostic"
    else:
        decision = "PROMOTE_SEGMENTATION"
        reason = "all frozen controlled, real, conservation and safety gates passed"
    return {
        "schema": REPORT_SCHEMA,
        "version": VERSION,
        "decision": decision,
        "reason": reason,
        "candidate": {
            "id": candidate["candidate_id"],
            "parameters": candidate["parameters"],
            "cluster_distance_threshold": candidate["cluster_distance_threshold"],
            "adaptive_cluster_quantile": candidate["adaptive_cluster_quantile"],
            "adaptive_cluster_margin": candidate["adaptive_cluster_margin"],
            "adaptive_cluster_maximum": candidate["adaptive_cluster_maximum"],
        },
        "development": candidate["tuning"],
        "terminal": {
            "controlled_hard": controlled,
            "real_diagnostic": real,
        },
        "gates": {"controlled_hard": hard_gates, "real_diagnostic": real_gates, "safety": safety},
        "safety": {
            "coverage_v3_unchanged": guards,
            "selected_transcript_unchanged": guards,
            "raw_audio_unchanged": guards,
            "primary_asr_unchanged": True,
            "echo_guard_unchanged": True,
            "shadow_only": True,
            "production_promoted": False,
            "candidate_qualified_for_integration": decision == "PROMOTE_SEGMENTATION",
        },
        "privacy": {
            "session_ids": "private_only",
            "speech_text": "private_only",
            "speaker_names": "private_only",
            "absolute_paths": False,
        },
        "next_action": (
            "integrate_frozen_boundary_layer_before_anonymous_identity"
            if decision == "PROMOTE_SEGMENTATION"
            else "keep_coverage_v3_and_rebaseline_transcript"
            if decision == "KEEP_COVERAGE_V3"
            else "obtain_human_reviewed_real_boundary_truth_or_stop_at_evidence_limit"
        ),
    }


def markdown(report: dict[str, Any]) -> str:
    hard = report["terminal"]["controlled_hard"]
    real = report["terminal"]["real_diagnostic"]
    return "\n".join(
        [
            "# Remote Speaker Boundary and Minority-Voice Segmentation v1",
            "",
            f"Decision: `{report['decision']}`",
            "",
            report["reason"],
            "",
            "## Controlled Hard",
            "",
            f"- Boundary precision: `{hard['boundary']['precision']:.6f}`",
            f"- Boundary recall: `{hard['boundary']['recall']:.6f}`",
            f"- B-cubed F1: `{hard['bcubed']['f1']:.6f}`",
            f"- Pairwise precision: `{hard['pairwise']['precision']:.6f}`",
            f"- Minority recall: `{hard['minority_speaker_recall']}`",
            "",
            "## Real Diagnostic",
            "",
            f"- Boundary precision: `{real['boundary']['precision']:.6f}`",
            f"- Boundary recall: `{real['boundary']['recall']:.6f}`",
            f"- Candidate/reference speaker-count ratio: `{real['speaker_count_ratio']:.6f}`",
            f"- Minority boundary recall: `{real['minority_boundary_recall']}`",
            "",
            "The real reference is diagnostic unless independently human-reviewed.",
            "Coverage v3, selected transcripts, raw audio, ASR and Echo Guard remain unchanged.",
            "Speaker names, speech text, session IDs and item-level evidence remain private.",
            "",
        ]
    )


def public_manifest(policy_path: Path, out: Path, report: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": MANIFEST_SCHEMA,
        "version": VERSION,
        "decision": report["decision"],
        "policy_sha256": sha256(policy_path),
        "freeze_sha256": sha256(out / "freeze_manifest.json"),
        "candidate_sha256": sha256(out / "private/candidate.frozen.json"),
        "report_sha256": sha256(out / "report.json"),
        "implementation": artifact(Path(__file__).resolve()),
        "production_mutations": 0,
    }


def action_evaluate(policy_path: Path, policy: dict[str, Any], out: Path, write_manifest: Path | None) -> int:
    _, candidate = verify_freeze(policy_path, out)
    controlled = evaluate_controlled_terminal(policy, candidate, out)
    real = real_reference_metrics(policy, out)
    report = build_report(policy, candidate, controlled, real, production_guards_unchanged(out))
    write_json(out / "private/controlled_terminal_evaluation.json", controlled)
    write_json(out / "private/real_terminal_evaluation.json", real)
    write_json(out / "report.json", report)
    atomic_write(out / "report.md", markdown(report).encode())
    manifest = public_manifest(policy_path, out, report)
    write_json(out / "artifact_manifest.json", manifest)
    if write_manifest:
        write_json(write_manifest, manifest)
    print(f"decision: {report['decision']}")
    print(f"hard_boundary_f1: {controlled['boundary']['f1']}")
    print(f"real_boundary_f1: {real['boundary']['f1']}")
    print(f"real_speaker_count_ratio: {real['speaker_count_ratio']}")
    return 0


def action_replay(policy_path: Path, policy: dict[str, Any], out: Path) -> int:
    before = {path: path.read_bytes() for path in [out / "report.json", out / "report.md", out / "artifact_manifest.json"] if path.is_file()}
    if len(before) != 3:
        raise BoundaryError("evaluate_must_run_before_replay")
    action_evaluate(policy_path, policy, out, None)
    after = {path: path.read_bytes() for path in before}
    exact = before == after
    payload = {
        "schema": REPLAY_SCHEMA,
        "byte_exact": exact,
        "artifacts": {path.name: sha256_bytes(after[path]) for path in sorted(after)},
        "production_guards_unchanged": production_guards_unchanged(out),
    }
    write_json(out / "replay_report.json", payload)
    print(f"replay: {'byte-exact' if exact else 'mismatch'}")
    return 0 if exact else 2


def action_status(out: Path) -> int:
    report_path = out / "report.json"
    if not report_path.is_file():
        state = "frozen" if (out / "freeze_manifest.json").is_file() else "prepared" if (out / "private/candidate.pending.json").is_file() else "missing"
        print(f"status: {state}")
        return 0 if state != "missing" else 2
    report = read_json(report_path)
    print(f"decision: {report['decision']}")
    print(f"reason: {report['reason']}")
    print(f"next_action: {report['next_action']}")
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Evaluate frozen remote speaker boundary and minority-voice segmentation.")
    result.add_argument("action", choices=["prepare", "freeze", "evaluate", "replay", "status", "all"])
    result.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    result.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    result.add_argument("--write-manifest", type=Path)
    return result


def main() -> int:
    args = parser().parse_args()
    policy_path = args.policy.expanduser().resolve()
    out = args.out_dir.expanduser().resolve()
    write_manifest = args.write_manifest.expanduser().resolve() if args.write_manifest else None
    policy = load_policy(policy_path)
    if args.action in {"prepare", "all"}:
        action_prepare(policy_path, policy, out)
    if args.action in {"freeze", "all"}:
        action_freeze(policy_path, policy, out)
    if args.action in {"evaluate", "all"}:
        action_evaluate(policy_path, policy, out, write_manifest)
    if args.action in {"replay", "all"}:
        return action_replay(policy_path, policy, out)
    if args.action == "status":
        return action_status(out)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (BoundaryError, OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2)
