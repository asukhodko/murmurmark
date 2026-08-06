#!/usr/bin/env python3
"""Build an audit-only anonymous speaker map over authoritative remote audio.

The selected dialogue remains untouched.  Evidence is derived from existing
remote utterance boundaries, clustered locally, and published only when the
session-level stability gates pass.  Weak evidence falls back to aggregate
``Colleagues``.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
import sys
import warnings
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
from sklearn.cluster import AgglomerativeClustering
from sklearn.metrics import adjusted_rand_score

import authoritative_asr_cache as cache
from murmurmark_resource_policy import apply_resource_policy, resolve_resource_policy


SCRIPT_VERSION = "0.1.0"
REPORT_SCHEMA = "murmurmark.remote_speaker_evidence_report/v1"
INTERVAL_SCHEMA = "murmurmark.remote_speaker_interval/v1"
MAP_SCHEMA = "murmurmark.remote_speaker_map/v1"
ATTRIBUTION_SCHEMA = "murmurmark.remote_utterance_attribution/v1"
RICH_SCHEMA = "murmurmark.transcript_rich_shadow/v1"
FIXTURE_SCHEMA = "murmurmark.remote_speaker_embedding_fixture/v1"
DEFAULT_OUT_DIR = "derived/audit/remote-speaker-evidence-v1"
AUTO_PROFILES = (
    "reviewed_v1",
    "order_repair_v1",
    "local_recall_repair_v1",
    "agent_reviewed_v1",
    "audit_cleanup_v7",
    "suggested_review_v1",
    "audit_cleanup_v6",
    "audit_cleanup_v5",
    "audit_cleanup_v4",
    "audit_cleanup_v3",
    "audit_cleanup_v2",
    "audit_cleanup_v1",
    "shadow_v2",
    "current",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a fail-open anonymous remote-speaker evidence map."
    )
    parser.add_argument("session", type=Path)
    parser.add_argument("--profile", default="auto")
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--model-path", type=Path)
    parser.add_argument("--embedding-fixture", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--min-unit-sec", type=float, default=1.0)
    parser.add_argument("--max-unit-sec", type=float, default=12.0)
    parser.add_argument("--max-units", type=int, default=400)
    parser.add_argument("--cluster-distance", type=float, default=0.20)
    parser.add_argument("--min-cluster-units", type=int, default=10)
    parser.add_argument("--min-cluster-sec", type=float, default=60.0)
    parser.add_argument("--min-cluster-span-sec", type=float, default=60.0)
    parser.add_argument("--min-cluster-cohesion", type=float, default=0.85)
    parser.add_argument("--min-assignment-similarity", type=float, default=0.72)
    parser.add_argument("--min-assignment-margin", type=float, default=0.02)
    parser.add_argument("--min-published-speech-ratio", type=float, default=0.55)
    parser.add_argument("--min-chunk-consistency", type=float, default=0.80)
    parser.add_argument("--chunk-sec", type=float, default=600.0)
    parser.add_argument("--chunk-merge-distance", type=float, default=0.10)
    parser.add_argument("--overlap-ambiguity-sec", type=float, default=0.50)
    parser.add_argument(
        "--resource-profile",
        choices=("background", "opportunistic", "performance"),
        default="background",
    )
    parser.add_argument("--max-compute-threads", type=int, default=4)
    parser.add_argument("--progress", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()
    if args.min_unit_sec <= 0 or args.max_unit_sec < args.min_unit_sec:
        parser.error("invalid evidence-unit duration range")
    if args.max_units <= 0 or args.min_cluster_units < 2:
        parser.error("unit limits must be positive")
    if args.min_cluster_sec <= 0 or args.min_cluster_span_sec <= 0:
        parser.error("cluster duration limits must be positive")
    if not 0 < args.cluster_distance < 1:
        parser.error("cluster distance must be between 0 and 1")
    if not 0 <= args.min_cluster_cohesion <= 1:
        parser.error("min-cluster-cohesion must be between 0 and 1")
    if not 0 <= args.min_assignment_similarity <= 1:
        parser.error("min-assignment-similarity must be between 0 and 1")
    if not 0 <= args.min_assignment_margin < 1:
        parser.error("assignment margin must be between 0 and 1")
    if not 0 <= args.min_published_speech_ratio <= 1:
        parser.error("min-published-speech-ratio must be between 0 and 1")
    if not 0 <= args.min_chunk_consistency <= 1:
        parser.error("min-chunk-consistency must be between 0 and 1")
    if args.chunk_sec <= 0:
        parser.error("chunk-sec must be positive")
    if not 0 < args.chunk_merge_distance < 1:
        parser.error("chunk-merge-distance must be between 0 and 1")
    if args.overlap_ambiguity_sec < 0 or args.max_compute_threads <= 0:
        parser.error("overlap threshold must be non-negative and compute-thread limit positive")
    return args


def progress(args: argparse.Namespace, message: str) -> None:
    if args.progress:
        print(f"remote_speakers: {message}", flush=True)


def suffix(profile: str) -> str:
    return "" if profile == "current" else f".{profile}"


def read_json(path: Path) -> dict[str, Any]:
    return cache.read_json(path) or {}


def resolve_profile(session: Path, requested: str) -> tuple[str, Path]:
    resolved = session / "derived/transcript-simple/whisper-cpp/resolved"
    if requested != "auto":
        path = resolved / f"clean_dialogue{suffix(requested)}.json"
        return requested, path

    handoff = read_json(session / "derived/pipeline-run/authoritative_handoff.json")
    selected = handoff.get("selected_transcript_profile")
    if isinstance(selected, str) and selected:
        path = resolved / f"clean_dialogue{suffix(selected)}.json"
        if path.is_file():
            return selected, path

    for profile in AUTO_PROFILES:
        path = resolved / f"clean_dialogue{suffix(profile)}.json"
        if path.is_file():
            return profile, path
    return "current", resolved / "clean_dialogue.json"


def relative(path: Path, session: Path) -> str:
    try:
        return str(path.resolve().relative_to(session.resolve()))
    except ValueError:
        return str(path.resolve())


def fingerprint(path: Path, session: Path | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"exists": path.is_file()}
    result["path"] = relative(path, session) if session is not None else str(path.expanduser())
    if path.is_file():
        result.update(cache.file_fingerprint(path, include_path=False))
    return result


def fingerprint_without_path(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"exists": False}
    return {"exists": True, **cache.file_fingerprint(path, include_path=False)}


def implementation_provenance() -> dict[str, Any]:
    return {
        "script": Path(__file__).name,
        "version": SCRIPT_VERSION,
        "fingerprint": fingerprint_without_path(Path(__file__).resolve()),
    }


def stable_resource_policy(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": report.get("schema"),
        "profile": report.get("profile"),
        "nice_requested": report.get("nice_requested"),
        "darwin_background_requested": report.get("darwin_background_requested"),
        "max_compute_threads": report.get("max_compute_threads"),
        "thread_environment": report.get("thread_environment") or {},
    }


def canonical_json_bytes(payload: Any) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def write_json(path: Path, payload: Any) -> None:
    cache.atomic_write_bytes(path, canonical_json_bytes(payload))


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    content = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
    cache.atomic_write_bytes(path, content.encode("utf-8"))


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def normalize(vector: np.ndarray) -> np.ndarray:
    value = np.asarray(vector, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(value))
    if not math.isfinite(norm) or norm <= 1e-9:
        raise ValueError("zero_or_invalid_embedding")
    return value / norm


def stable_sample(rows: list[dict[str, Any]], maximum: int) -> list[dict[str, Any]]:
    if len(rows) <= maximum:
        return rows
    indices = np.linspace(0, len(rows) - 1, maximum, dtype=int)
    return [rows[int(index)] for index in indices]


def remote_audio_path(session: Path) -> Path:
    prepared = session / "derived/asr/remote.wav"
    if prepared.is_file():
        return prepared
    return session / "audio/remote/000001.caf"


def evidence_units(
    utterances: list[dict[str, Any]], args: argparse.Namespace
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    accepted: list[dict[str, Any]] = []
    rejected: dict[str, str] = {}
    for utterance in utterances:
        if utterance.get("role") != "remote":
            continue
        utterance_id = str(utterance.get("id") or "")
        if not utterance_id:
            continue
        try:
            start = float(utterance["start"])
            end = float(utterance["end"])
        except (KeyError, TypeError, ValueError):
            rejected[utterance_id] = "invalid_interval"
            continue
        duration = end - start
        quality = utterance.get("quality") if isinstance(utterance.get("quality"), dict) else {}
        if quality.get("needs_review") is True:
            rejected[utterance_id] = "source_needs_review"
        elif duration < args.min_unit_sec:
            rejected[utterance_id] = "too_short_for_voice_evidence"
        elif duration > args.max_unit_sec:
            rejected[utterance_id] = "too_long_for_single_speaker_evidence"
        elif not str(utterance.get("text") or "").strip():
            rejected[utterance_id] = "empty_text"
        else:
            accepted.append(
                {
                    "utterance_id": utterance_id,
                    "start": round(start, 6),
                    "end": round(end, 6),
                    "duration_sec": round(duration, 6),
                }
            )
    sampled = stable_sample(accepted, args.max_units)
    sampled_ids = {row["utterance_id"] for row in sampled}
    for row in accepted:
        if row["utterance_id"] not in sampled_ids:
            rejected[row["utterance_id"]] = "bounded_unit_limit"
    return sampled, rejected


class EmbeddingBackend:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.status = "unavailable"
        self.reason: str | None = None
        self.provenance: dict[str, Any] = {
            "method": "resemblyzer_dvector",
            "runtime": {"python": sys.version.split()[0]},
        }
        self.fixture: dict[str, list[float]] | None = None
        self.encoder: Any = None
        self.preprocess: Any = None

        if args.embedding_fixture is not None:
            fixture_path = args.embedding_fixture.expanduser().resolve()
            fixture = read_json(fixture_path)
            if fixture.get("schema") != FIXTURE_SCHEMA or not isinstance(fixture.get("embeddings"), dict):
                self.reason = "invalid_embedding_fixture"
                return
            self.fixture = fixture["embeddings"]
            self.status = "ready"
            self.provenance = {
                "method": "deterministic_fixture",
                "fixture": fingerprint_without_path(fixture_path),
                "runtime": {"python": sys.version.split()[0]},
            }
            return

        try:
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", message="pkg_resources is deprecated as an API.*")
                import resemblyzer
                from resemblyzer import VoiceEncoder, preprocess_wav
        except (ImportError, ModuleNotFoundError) as error:
            self.reason = f"resemblyzer_unavailable:{type(error).__name__}"
            return

        default_model = Path(resemblyzer.__file__).resolve().with_name("pretrained.pt")
        model = (args.model_path or default_model).expanduser().resolve()
        if not model.is_file():
            self.reason = "speaker_model_missing"
            self.provenance["model"] = fingerprint_without_path(model)
            return
        try:
            self.encoder = VoiceEncoder(device="cpu", verbose=False, weights_fpath=model)
        except Exception as error:  # model/runtime failures are intentionally fail-open
            self.reason = f"speaker_model_load_failed:{type(error).__name__}"
            self.provenance["model"] = fingerprint_without_path(model)
            return
        self.preprocess = preprocess_wav
        self.status = "ready"
        self.provenance = {
            "method": "resemblyzer_dvector",
            "package_version": importlib.metadata.version("resemblyzer"),
            "model": fingerprint_without_path(model),
            "license": "Apache-2.0",
            "runtime": {
                "python": sys.version.split()[0],
                "numpy": np.__version__,
            },
        }

    def embed_fixture(self, unit: dict[str, Any]) -> np.ndarray:
        assert self.fixture is not None
        value = self.fixture.get(unit["utterance_id"])
        if not isinstance(value, list):
            raise ValueError("fixture_embedding_missing")
        return normalize(np.asarray(value, dtype=np.float32))

    def embed_audio(self, audio: sf.SoundFile, unit: dict[str, Any]) -> np.ndarray:
        assert self.encoder is not None and self.preprocess is not None
        start_frame = max(0, int(round(unit["start"] * audio.samplerate)))
        end_frame = min(len(audio), int(round(unit["end"] * audio.samplerate)))
        if end_frame <= start_frame:
            raise ValueError("empty_audio_slice")
        audio.seek(start_frame)
        waveform = audio.read(end_frame - start_frame, dtype="float32", always_2d=True).mean(axis=1)
        prepared = self.preprocess(waveform, source_sr=audio.samplerate)
        if len(prepared) < 16_000:
            raise ValueError("insufficient_voiced_audio")
        return normalize(self.encoder.embed_utterance(prepared))


def compute_embeddings(
    backend: EmbeddingBackend,
    audio_path: Path,
    units: list[dict[str, Any]],
    args: argparse.Namespace,
) -> tuple[list[dict[str, Any]], np.ndarray, dict[str, str]]:
    kept: list[dict[str, Any]] = []
    embeddings: list[np.ndarray] = []
    rejected: dict[str, str] = {}
    audio: sf.SoundFile | None = None
    try:
        if backend.fixture is None:
            audio = sf.SoundFile(str(audio_path))
        for index, unit in enumerate(units, start=1):
            try:
                embedding = (
                    backend.embed_fixture(unit)
                    if backend.fixture is not None
                    else backend.embed_audio(audio, unit)  # type: ignore[arg-type]
                )
            except Exception as error:
                rejected[unit["utterance_id"]] = f"embedding_failed:{type(error).__name__}"
                continue
            kept.append(unit)
            embeddings.append(embedding)
            if args.progress and (index == len(units) or index % 25 == 0):
                progress(args, f"embedded {index}/{len(units)} evidence units")
    finally:
        if audio is not None:
            audio.close()
    if not embeddings:
        return kept, np.empty((0, 0), dtype=np.float32), rejected
    dimensions = {len(value) for value in embeddings}
    if len(dimensions) != 1:
        return [], np.empty((0, 0), dtype=np.float32), {
            **rejected,
            **{row["utterance_id"]: "embedding_dimension_mismatch" for row in kept},
        }
    return kept, np.stack(embeddings), rejected


def cluster(embeddings: np.ndarray, distance: float) -> np.ndarray:
    if len(embeddings) == 0:
        return np.empty(0, dtype=int)
    if len(embeddings) == 1:
        return np.zeros(1, dtype=int)
    return AgglomerativeClustering(
        n_clusters=None,
        distance_threshold=distance,
        metric="cosine",
        linkage="average",
    ).fit_predict(embeddings)


def centroid(embeddings: np.ndarray, indices: np.ndarray) -> np.ndarray:
    return normalize(embeddings[indices].mean(axis=0))


def cluster_stats(
    labels: np.ndarray,
    embeddings: np.ndarray,
    units: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for label in sorted({int(value) for value in labels}):
        indices = np.flatnonzero(labels == label)
        center = centroid(embeddings, indices)
        similarities = embeddings[indices] @ center
        starts = [units[index]["start"] for index in indices]
        ends = [units[index]["end"] for index in indices]
        rows.append(
            {
                "cluster": label,
                "unit_count": int(len(indices)),
                "speech_sec": round(sum(units[index]["duration_sec"] for index in indices), 6),
                "first_start": round(min(starts), 6),
                "last_end": round(max(ends), 6),
                "span_sec": round(max(ends) - min(starts), 6),
                "cohesion_median": round(float(np.median(similarities)), 6),
                "cohesion_p10": round(float(np.quantile(similarities, 0.10)), 6),
                "centroid": center,
                "indices": indices,
            }
        )
    return rows


def is_major(row: dict[str, Any], args: argparse.Namespace) -> bool:
    return bool(
        row["unit_count"] >= args.min_cluster_units
        and row["speech_sec"] >= args.min_cluster_sec
        and row["span_sec"] >= args.min_cluster_span_sec
        and row["cohesion_median"] >= args.min_cluster_cohesion
    )


def canonical_partition(labels: np.ndarray, units: list[dict[str, Any]]) -> list[int]:
    first: dict[int, float] = {}
    for index, label in enumerate(labels):
        first[int(label)] = min(first.get(int(label), float("inf")), float(units[index]["start"]))
    order = {label: index for index, label in enumerate(sorted(first, key=lambda value: (first[value], value)))}
    return [order[int(value)] for value in labels]


def reverse_order_ari(embeddings: np.ndarray, labels: np.ndarray, distance: float) -> float:
    if len(labels) < 2:
        return 1.0
    reversed_labels = cluster(embeddings[::-1], distance)[::-1]
    return round(float(adjusted_rand_score(labels, reversed_labels)), 6)


def chunk_replay_labels(
    embeddings: np.ndarray,
    units: list[dict[str, Any]],
    distance: float,
    merge_distance: float,
    chunk_sec: float,
) -> np.ndarray:
    if len(units) < 2:
        return np.zeros(len(units), dtype=int)
    buckets: dict[int, list[int]] = defaultdict(list)
    for index, unit in enumerate(units):
        buckets[int(float(unit["start"]) // chunk_sec)].append(index)

    local_centroids: list[np.ndarray] = []
    memberships: list[list[int]] = []
    for bucket in sorted(buckets):
        indices = np.asarray(buckets[bucket], dtype=int)
        local_labels = cluster(embeddings[indices], distance)
        for local_label in sorted({int(value) for value in local_labels}):
            members = indices[np.flatnonzero(local_labels == local_label)].tolist()
            memberships.append(members)
            local_centroids.append(centroid(embeddings, np.asarray(members, dtype=int)))

    merged = cluster(np.stack(local_centroids), merge_distance)
    result = np.empty(len(units), dtype=int)
    for local_index, members in enumerate(memberships):
        for member in members:
            result[member] = int(merged[local_index])
    return result


def chunk_consistency(
    embeddings: np.ndarray,
    units: list[dict[str, Any]],
    labels: np.ndarray,
    args: argparse.Namespace,
) -> dict[str, Any]:
    replay = chunk_replay_labels(
        embeddings,
        units,
        args.cluster_distance,
        args.chunk_merge_distance,
        args.chunk_sec,
    )
    all_cluster_ari = (
        1.0 if len(labels) < 2 else round(float(adjusted_rand_score(labels, replay)), 6)
    )
    whole_stats = cluster_stats(labels, embeddings, units)
    replay_stats = cluster_stats(replay, embeddings, units)
    whole_major = {int(row["cluster"]) for row in whole_stats if is_major(row, args)}
    replay_major = {int(row["cluster"]) for row in replay_stats if is_major(row, args)}
    whole_indices = [index for index, value in enumerate(labels) if int(value) in whole_major]
    shared_indices = [
        index
        for index in whole_indices
        if int(replay[index]) in replay_major
    ]
    major_coverage = len(shared_indices) / len(whole_indices) if whole_indices else 0.0
    if len(shared_indices) < 2:
        major_ari = 0.0
    else:
        whole_values = [int(labels[index]) for index in shared_indices]
        replay_values = [int(replay[index]) for index in shared_indices]
        if len(set(whole_values)) == len(set(replay_values)) == 1:
            major_ari = 1.0
        else:
            major_ari = float(adjusted_rand_score(whole_values, replay_values))
    return {
        "major_cluster_ari": round(major_ari, 6),
        "major_unit_coverage": round(major_coverage, 6),
        "whole_major_clusters": len(whole_major),
        "replay_major_clusters": len(replay_major),
        "major_cluster_count_match": len(whole_major) == len(replay_major),
        "all_cluster_ari": all_cluster_ari,
    }


def overlap_flags(utterances: list[dict[str, Any]], threshold: float) -> dict[str, list[str]]:
    remote = [row for row in utterances if row.get("role") == "remote" and row.get("id")]
    remote.sort(key=lambda row: (float(row.get("start", 0)), float(row.get("end", 0)), str(row["id"])))
    overlaps: dict[str, list[str]] = defaultdict(list)
    for index, left in enumerate(remote):
        left_end = float(left.get("end", 0))
        for right in remote[index + 1 :]:
            right_start = float(right.get("start", 0))
            if right_start >= left_end:
                break
            duration = min(left_end, float(right.get("end", 0))) - right_start
            if duration >= threshold:
                overlaps[str(left["id"])].append(str(right["id"]))
                overlaps[str(right["id"])].append(str(left["id"]))
    return overlaps


def public_cluster_row(row: dict[str, Any], speaker_id: str | None) -> dict[str, Any]:
    return {
        "cluster": row["cluster"],
        "speaker_id": speaker_id,
        "unit_count": row["unit_count"],
        "speech_sec": row["speech_sec"],
        "first_start": row["first_start"],
        "last_end": row["last_end"],
        "span_sec": row["span_sec"],
        "cohesion_median": row["cohesion_median"],
        "cohesion_p10": row["cohesion_p10"],
    }


def build_assignments(
    utterances: list[dict[str, Any]],
    units: list[dict[str, Any]],
    embeddings: np.ndarray,
    labels: np.ndarray,
    stats: list[dict[str, Any]],
    rejected: dict[str, str],
    overlaps: dict[str, list[str]],
    publish: bool,
    args: argparse.Namespace,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    major = [row for row in stats if is_major(row, args)]
    major.sort(key=lambda row: (row["first_start"], row["cluster"]))
    speaker_for_cluster = {
        int(row["cluster"]): f"remote_speaker_{index:02d}" for index, row in enumerate(major, start=1)
    }
    centroids = {int(row["cluster"]): row["centroid"] for row in major}
    unit_by_id = {row["utterance_id"]: index for index, row in enumerate(units)}
    provisional: dict[str, dict[str, Any]] = {}

    for utterance in utterances:
        if utterance.get("role") != "remote" or not utterance.get("id"):
            continue
        utterance_id = str(utterance["id"])
        index = unit_by_id.get(utterance_id)
        if index is None:
            provisional[utterance_id] = {
                "speaker_id": None,
                "status": "aggregate",
                "reason": rejected.get(utterance_id, "not_an_embedding_unit"),
                "similarity": None,
                "margin": None,
            }
            continue
        label = int(labels[index])
        speaker_id = speaker_for_cluster.get(label)
        if not publish:
            reason = "session_publish_gate_failed"
        elif speaker_id is None:
            reason = "minor_or_unstable_cluster"
        else:
            similarity = float(embeddings[index] @ centroids[label])
            alternatives = [float(embeddings[index] @ value) for key, value in centroids.items() if key != label]
            margin = similarity - max(alternatives) if alternatives else similarity
            if similarity < args.min_assignment_similarity:
                reason = "low_cluster_similarity"
            elif margin < args.min_assignment_margin:
                reason = "low_cluster_margin"
            else:
                provisional[utterance_id] = {
                    "speaker_id": speaker_id,
                    "status": "attributed",
                    "reason": "stable_anonymous_cluster",
                    "similarity": round(similarity, 6),
                    "margin": round(margin, 6),
                    "cluster": label,
                }
                continue
        provisional[utterance_id] = {
            "speaker_id": None,
            "status": "aggregate",
            "reason": reason,
            "similarity": None,
            "margin": None,
            "cluster": label,
        }

    # Determine every conflict before mutating provisional assignments so both
    # sides of a double-talk interval fail open symmetrically.
    conflicting_utterance_ids: set[str] = set()
    for utterance_id, related in overlaps.items():
        current = provisional.get(utterance_id)
        if not current or current.get("speaker_id") is None:
            continue
        for other in related:
            other_speaker = provisional.get(other, {}).get("speaker_id")
            if other_speaker not in {None, current["speaker_id"]}:
                conflicting_utterance_ids.update({utterance_id, other})

    for utterance_id in conflicting_utterance_ids:
        provisional[utterance_id].update(
            {
                "speaker_id": None,
                "status": "aggregate",
                "reason": "possible_remote_double_talk",
            }
        )

    attribution_rows: list[dict[str, Any]] = []
    interval_rows: list[dict[str, Any]] = []
    for utterance in utterances:
        if utterance.get("role") != "remote" or not utterance.get("id"):
            continue
        utterance_id = str(utterance["id"])
        assignment = provisional[utterance_id]
        base = {
            "utterance_id": utterance_id,
            "start": round(float(utterance.get("start", 0)), 6),
            "end": round(float(utterance.get("end", 0)), 6),
            "speaker_id": assignment.get("speaker_id"),
            "speaker_label": assignment.get("speaker_id") or "Colleagues",
            "status": assignment["status"],
            "reason": assignment["reason"],
            "confidence": {
                "cluster_similarity": assignment.get("similarity"),
                "nearest_cluster_margin": assignment.get("margin"),
            },
            "overlap_utterance_ids": sorted(overlaps.get(utterance_id, [])),
        }
        attribution_rows.append({"schema": ATTRIBUTION_SCHEMA, **base})
        interval_rows.append({"schema": INTERVAL_SCHEMA, **base})

    speaker_rows = [
        public_cluster_row(row, speaker_for_cluster.get(int(row["cluster"])))
        for row in stats
        if int(row["cluster"]) in speaker_for_cluster
    ]
    published_ids = Counter(
        row["speaker_id"] for row in attribution_rows if isinstance(row.get("speaker_id"), str)
    )
    for row in speaker_rows:
        row["published_utterance_count"] = published_ids.get(row["speaker_id"], 0)
    return attribution_rows, interval_rows, speaker_rows


def markdown_transcript(
    dialogue: dict[str, Any], attributions: list[dict[str, Any]], profile: str
) -> str:
    by_id = {row["utterance_id"]: row for row in attributions}
    lines = [
        "# Rich Transcript Shadow",
        "",
        "This is audit-only anonymous speaker evidence. The selected transcript remains authoritative.",
        "",
        f"Source profile: `{profile}`",
        "",
    ]
    for utterance in dialogue.get("utterances") or []:
        if not isinstance(utterance, dict):
            continue
        start = max(0, int(float(utterance.get("start", 0))))
        minutes, seconds = divmod(start, 60)
        role = str(utterance.get("role") or "")
        if role == "remote":
            row = by_id.get(str(utterance.get("id") or ""), {})
            label = str(row.get("speaker_label") or "Colleagues")
            suffix_value = "" if row.get("speaker_id") else f" [{row.get('reason', 'aggregate')}]"
        else:
            label = str(utterance.get("speaker_label") or "Me")
            suffix_value = ""
        lines.extend(
            [
                f"## {minutes:02d}:{seconds:02d} {label}{suffix_value}",
                "",
                str(utterance.get("text") or ""),
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def report_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    stability = report["stability"]
    lines = [
        "# Remote Speaker Evidence Map v1",
        "",
        f"- Status: `{report['status']}`",
        f"- Decision: `{report['decision']}`",
        f"- Source profile: `{report['source']['profile']}`",
        f"- Backend: `{report['model'].get('method')}`",
        f"- Remote utterances: `{summary['remote_utterances']}`",
        f"- Evidence units: `{summary['evidence_units']}`",
        f"- Anonymous speakers published: `{summary['published_speakers']}`",
        f"- Published speech ratio: `{summary['published_speech_ratio']:.6f}`",
        f"- Aggregate utterances: `{summary['aggregate_utterances']}`",
        f"- Reverse-order ARI: `{stability['reverse_order_ari']:.6f}`",
        f"- Chunk replay ARI: `{stability['chunk_replay_ari']:.6f}`",
        "",
        "## Safety",
        "",
        "The selected dialogue, Evidence Handoff v2 and guarded export are unchanged. Anonymous IDs",
        "are session-local. Missing or weak evidence remains aggregate `Colleagues`.",
    ]
    if report.get("reasons"):
        lines.extend(["", "## Reasons", ""])
        lines.extend(f"- `{reason}`" for reason in report["reasons"])
    return "\n".join(lines) + "\n"


def fallback_outputs(
    session: Path,
    out_dir: Path,
    profile: str,
    dialogue_path: Path,
    dialogue: dict[str, Any],
    audio_path: Path,
    backend: EmbeddingBackend,
    resource: dict[str, Any],
    reason: str,
    raw_before: dict[str, Any],
) -> dict[str, Any]:
    utterances = dialogue.get("utterances") if isinstance(dialogue.get("utterances"), list) else []
    attribution_rows: list[dict[str, Any]] = []
    interval_rows: list[dict[str, Any]] = []
    for utterance in utterances:
        if not isinstance(utterance, dict) or utterance.get("role") != "remote" or not utterance.get("id"):
            continue
        row = {
            "utterance_id": str(utterance["id"]),
            "start": round(float(utterance.get("start", 0)), 6),
            "end": round(float(utterance.get("end", 0)), 6),
            "speaker_id": None,
            "speaker_label": "Colleagues",
            "status": "aggregate",
            "reason": reason,
            "confidence": {"cluster_similarity": None, "nearest_cluster_margin": None},
            "overlap_utterance_ids": [],
        }
        attribution_rows.append({"schema": ATTRIBUTION_SCHEMA, **row})
        interval_rows.append({"schema": INTERVAL_SCHEMA, **row})
    raw_after = fingerprint(session / "audio/remote/000001.caf", session)
    report = {
        "schema": REPORT_SCHEMA,
        "version": SCRIPT_VERSION,
        "status": "fallback_aggregate",
        "decision": "DO_NOT_PUBLISH",
        "reasons": [reason],
        "implementation": implementation_provenance(),
        "source": {
            "session_id": session.name,
            "profile": profile,
            "dialogue": fingerprint(dialogue_path, session),
            "remote_audio": fingerprint(audio_path, session),
            "raw_remote_before": raw_before,
            "raw_remote_after": raw_after,
        },
        "model": backend.provenance,
        "parameters": {},
        "resource_policy": stable_resource_policy(resource),
        "stability": {"reverse_order_ari": 0.0, "chunk_replay_ari": 0.0},
        "summary": {
            "remote_utterances": len(attribution_rows),
            "evidence_units": 0,
            "clusters_total": 0,
            "major_clusters": 0,
            "published_speakers": 0,
            "published_utterances": 0,
            "aggregate_utterances": len(attribution_rows),
            "published_speech_sec": 0.0,
            "remote_speech_sec": round(
                sum(row["end"] - row["start"] for row in attribution_rows), 6
            ),
            "published_speech_ratio": 0.0,
        },
        "gates": {"publish_session_speaker_map": False},
        "safety": {
            "selected_dialogue_unchanged": dialogue_path.is_file(),
            "raw_remote_unchanged": raw_before == raw_after,
            "aggregate_fail_open": True,
        },
    }
    speaker_map = {
        "schema": MAP_SCHEMA,
        "session_id": session.name,
        "status": "fallback_aggregate",
        "speakers": [],
        "aggregate_label": "Colleagues",
    }
    rich = {
        "schema": RICH_SCHEMA,
        "status": "audit_only_fallback",
        "source_profile": profile,
        "source_dialogue": fingerprint(dialogue_path, session),
        "utterances": utterances,
        "remote_speaker_attributions": attribution_rows,
    }
    write_jsonl(out_dir / "speaker_intervals.jsonl", interval_rows)
    write_jsonl(out_dir / "utterance_attribution.jsonl", attribution_rows)
    write_json(out_dir / "speaker_map.json", speaker_map)
    write_json(out_dir / "transcript.rich.shadow.json", rich)
    cache.atomic_write_bytes(
        out_dir / "transcript.rich.shadow.md", markdown_transcript(dialogue, attribution_rows, profile).encode("utf-8")
    )
    write_json(out_dir / "report.json", report)
    cache.atomic_write_bytes(out_dir / "report.md", report_markdown(report).encode("utf-8"))
    write_json(
        out_dir / "artifact_manifest.json",
        {
            "schema": "murmurmark.remote_speaker_evidence_artifact_manifest/v1",
            "session_id": session.name,
            "artifacts": {
                name: sha256_bytes((out_dir / name).read_bytes())
                for name in (
                    "speaker_intervals.jsonl",
                    "utterance_attribution.jsonl",
                    "speaker_map.json",
                    "transcript.rich.shadow.json",
                    "transcript.rich.shadow.md",
                    "report.json",
                    "report.md",
                )
            },
        },
    )
    return report


def main() -> int:
    args = parse_args()
    session = args.session.expanduser().resolve()
    out_dir = (args.out_dir.expanduser().resolve() if args.out_dir else session / DEFAULT_OUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    policy = resolve_resource_policy(args.resource_profile, args.max_compute_threads)
    resource = apply_resource_policy(policy)

    profile, dialogue_path = resolve_profile(session, args.profile)
    dialogue = read_json(dialogue_path)
    audio_path = remote_audio_path(session)
    raw_path = session / "audio/remote/000001.caf"
    raw_before = fingerprint(raw_path, session)
    backend = EmbeddingBackend(args)

    if not dialogue_path.is_file() or not isinstance(dialogue.get("utterances"), list):
        report = fallback_outputs(
            session, out_dir, profile, dialogue_path, dialogue, audio_path, backend, resource,
            "selected_dialogue_missing_or_invalid", raw_before,
        )
        print(f"remote_speakers: {report['status']} ({report['reasons'][0]})")
        return 0
    if not audio_path.is_file():
        report = fallback_outputs(
            session, out_dir, profile, dialogue_path, dialogue, audio_path, backend, resource,
            "authoritative_remote_audio_missing", raw_before,
        )
        print(f"remote_speakers: {report['status']} ({report['reasons'][0]})")
        return 0
    if backend.status != "ready":
        report = fallback_outputs(
            session, out_dir, profile, dialogue_path, dialogue, audio_path, backend, resource,
            backend.reason or "speaker_backend_unavailable", raw_before,
        )
        print(f"remote_speakers: {report['status']} ({report['reasons'][0]})")
        return 0

    source_before = dialogue_path.read_bytes()
    utterances = dialogue["utterances"]
    units, rejected = evidence_units(utterances, args)
    progress(args, f"profile={profile} units={len(units)} backend={backend.provenance['method']}")
    units, embeddings, embedding_rejected = compute_embeddings(backend, audio_path, units, args)
    rejected.update(embedding_rejected)
    if len(units) < 2:
        report = fallback_outputs(
            session, out_dir, profile, dialogue_path, dialogue, audio_path, backend, resource,
            "insufficient_speaker_evidence", raw_before,
        )
        print(f"remote_speakers: {report['status']} ({report['reasons'][0]})")
        return 0

    labels = cluster(embeddings, args.cluster_distance)
    stats = cluster_stats(labels, embeddings, units)
    major = [row for row in stats if is_major(row, args)]
    reverse_ari = reverse_order_ari(embeddings, labels, args.cluster_distance)
    chunk = chunk_consistency(embeddings, units, labels, args)
    evidence_speech = sum(row["duration_sec"] for row in units)
    major_speech = sum(row["speech_sec"] for row in major)
    major_ratio = major_speech / evidence_speech if evidence_speech else 0.0
    gates = {
        "backend_ready": True,
        "major_cluster_present": bool(major),
        "published_speech_ratio": major_ratio >= args.min_published_speech_ratio,
        "reverse_order_stable": reverse_ari >= 0.99,
        "chunk_replay_stable": bool(
            chunk["major_cluster_ari"] >= args.min_chunk_consistency
            and chunk["major_unit_coverage"] >= 0.70
            and chunk["major_cluster_count_match"]
        ),
    }
    publish = all(gates.values())
    overlaps = overlap_flags(utterances, args.overlap_ambiguity_sec)
    attributions, intervals, speaker_rows = build_assignments(
        utterances, units, embeddings, labels, stats, rejected, overlaps, publish, args
    )

    raw_after = fingerprint(raw_path, session)
    selected_unchanged = dialogue_path.read_bytes() == source_before
    raw_unchanged = raw_before == raw_after
    if not selected_unchanged or not raw_unchanged:
        reason = "input_changed_during_run"
        report = fallback_outputs(
            session, out_dir, profile, dialogue_path, dialogue, audio_path, backend, resource,
            reason, raw_before,
        )
        print(f"remote_speakers: {report['status']} ({reason})")
        return 0

    published = [row for row in attributions if row.get("speaker_id")]
    published_speech = sum(float(row["end"]) - float(row["start"]) for row in published)
    all_remote = [row for row in attributions]
    remote_speech = sum(float(row["end"]) - float(row["start"]) for row in all_remote)
    reasons = [name for name, passed in gates.items() if not passed]
    decision = "PUBLISH_AUDIT_EVIDENCE" if publish else "DO_NOT_PUBLISH"
    report = {
        "schema": REPORT_SCHEMA,
        "version": SCRIPT_VERSION,
        "status": "completed" if publish else "completed_fail_open",
        "decision": decision,
        "reasons": reasons,
        "implementation": implementation_provenance(),
        "source": {
            "session_id": session.name,
            "profile": profile,
            "dialogue": fingerprint(dialogue_path, session),
            "remote_audio": fingerprint(audio_path, session),
            "raw_remote_before": raw_before,
            "raw_remote_after": raw_after,
        },
        "model": backend.provenance,
        "parameters": {
            "min_unit_sec": args.min_unit_sec,
            "max_unit_sec": args.max_unit_sec,
            "max_units": args.max_units,
            "cluster_distance": args.cluster_distance,
            "min_cluster_units": args.min_cluster_units,
            "min_cluster_sec": args.min_cluster_sec,
            "min_cluster_span_sec": args.min_cluster_span_sec,
            "min_cluster_cohesion": args.min_cluster_cohesion,
            "min_assignment_similarity": args.min_assignment_similarity,
            "min_assignment_margin": args.min_assignment_margin,
            "min_published_speech_ratio": args.min_published_speech_ratio,
            "min_chunk_consistency": args.min_chunk_consistency,
            "chunk_sec": args.chunk_sec,
            "chunk_merge_distance": args.chunk_merge_distance,
            "overlap_ambiguity_sec": args.overlap_ambiguity_sec,
        },
        "resource_policy": stable_resource_policy(resource),
        "stability": {
            "reverse_order_ari": reverse_ari,
            "chunk_replay_ari": chunk["major_cluster_ari"],
            "chunk_replay_major_unit_coverage": chunk["major_unit_coverage"],
            "chunk_replay_major_cluster_count_match": chunk["major_cluster_count_match"],
            "chunk_replay_whole_major_clusters": chunk["whole_major_clusters"],
            "chunk_replay_major_clusters": chunk["replay_major_clusters"],
            "chunk_replay_all_cluster_ari": chunk["all_cluster_ari"],
            "boundary_source": "selected_remote_utterance",
            "boundary_shift_sec": 0.0,
        },
        "clusters": [
            public_cluster_row(
                row,
                next(
                    (
                        speaker["speaker_id"]
                        for speaker in speaker_rows
                        if speaker["cluster"] == row["cluster"]
                    ),
                    None,
                )
                if publish
                else None,
            )
            for row in stats
        ],
        "summary": {
            "remote_utterances": len(all_remote),
            "evidence_units": len(units),
            "clusters_total": len(stats),
            "major_clusters": len(major),
            "published_speakers": len(speaker_rows) if publish else 0,
            "published_utterances": len(published),
            "aggregate_utterances": len(all_remote) - len(published),
            "published_speech_sec": round(published_speech, 6),
            "remote_speech_sec": round(remote_speech, 6),
            "published_speech_ratio": round(published_speech / remote_speech, 6) if remote_speech else 0.0,
            "major_evidence_speech_ratio": round(major_ratio, 6),
            "possible_remote_double_talk_utterances": sum(
                row["reason"] == "possible_remote_double_talk" for row in attributions
            ),
        },
        "gates": {**gates, "publish_session_speaker_map": publish},
        "safety": {
            "selected_dialogue_unchanged": selected_unchanged,
            "raw_remote_unchanged": raw_unchanged,
            "aggregate_fail_open": True,
            "selected_transcript_mutated": False,
            "evidence_handoff_mutated": False,
            "guarded_export_mutated": False,
        },
    }

    speaker_map = {
        "schema": MAP_SCHEMA,
        "session_id": session.name,
        "status": "published_audit_only" if publish else "fallback_aggregate",
        "scope": "session_local_anonymous",
        "speakers": speaker_rows if publish else [],
        "aggregate_label": "Colleagues",
        "model": backend.provenance,
        "parameters": report["parameters"],
    }
    rich = {
        "schema": RICH_SCHEMA,
        "status": "audit_only",
        "source_profile": profile,
        "source_dialogue": fingerprint(dialogue_path, session),
        "utterances": utterances,
        "remote_speaker_attributions": attributions,
        "speaker_map": speaker_rows if publish else [],
    }

    write_jsonl(out_dir / "speaker_intervals.jsonl", intervals)
    write_jsonl(out_dir / "utterance_attribution.jsonl", attributions)
    write_json(out_dir / "speaker_map.json", speaker_map)
    write_json(out_dir / "transcript.rich.shadow.json", rich)
    cache.atomic_write_bytes(
        out_dir / "transcript.rich.shadow.md",
        markdown_transcript(dialogue, attributions, profile).encode("utf-8"),
    )
    write_json(out_dir / "report.json", report)
    cache.atomic_write_bytes(out_dir / "report.md", report_markdown(report).encode("utf-8"))

    stable_hashes = {
        name: sha256_bytes((out_dir / name).read_bytes())
        for name in (
            "speaker_intervals.jsonl",
            "utterance_attribution.jsonl",
            "speaker_map.json",
            "transcript.rich.shadow.json",
            "transcript.rich.shadow.md",
            "report.json",
            "report.md",
        )
    }
    write_json(
        out_dir / "artifact_manifest.json",
        {
            "schema": "murmurmark.remote_speaker_evidence_artifact_manifest/v1",
            "session_id": session.name,
            "artifacts": stable_hashes,
        },
    )
    print(
        f"remote_speakers: status={report['status']} decision={decision} "
        f"speakers={report['summary']['published_speakers']} "
        f"coverage={report['summary']['published_speech_ratio']:.3f}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
