#!/usr/bin/env python3
"""Build and verify the private Target-Me Identifiability Corpus v1."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import shutil
import sys
import tarfile
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

# Native libraries read these limits during import, before main() can run.
os.environ.setdefault("OMP_NUM_THREADS", "4")
os.environ.setdefault("MKL_NUM_THREADS", "4")
os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "4")
os.environ.setdefault("HF_HUB_OFFLINE", "1")

import numpy as np
import soundfile as sf
from scipy import signal
from scipy.io import wavfile


ROOT = Path(__file__).resolve().parent.parent
POLICY_PATH = ROOT / "policies/target-me-identifiability-corpus-v1.json"
OUTPUT_ROOT = ROOT / "sessions/_reports/target-me-identifiability-corpus-v1"
SCRIPT_VERSION = "0.1.1"
POLICY_SCHEMA = "murmurmark.target_me_identifiability_policy/v1"
SOURCE_SCHEMA = "murmurmark.target_me_identifiability_source/v1"
SPEAKER_SCHEMA = "murmurmark.target_me_identifiability_speaker/v1"
ITEM_SCHEMA = "murmurmark.target_me_identifiability_item/v1"
QUERY_SCHEMA = "murmurmark.target_me_identifiability_query/v1"
ENROLLMENT_SCHEMA = "murmurmark.target_me_identifiability_enrollment/v1"
ORACLE_SCHEMA = "murmurmark.target_me_identifiability_oracle/v1"
REPLAY_SCHEMA = "murmurmark.target_me_identifiability_replay/v1"
DECISION_SCHEMA = "murmurmark.target_me_identifiability_decision/v1"
READY = "READY_FOR_TARGET_CONDITIONED_TRAINING"
DO_NOT_TRAIN = "DO_NOT_TRAIN_TARGET_ME_IDENTIFIABILITY_V1"
EPSILON = 1.0e-12


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def md5(path: Path) -> str:
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return payload


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise RuntimeError(f"invalid JSONL row at {path}:{line_number}")
            rows.append(payload)
    return rows


def relative(path: Path, root: Path = ROOT) -> str:
    return str(path.resolve().relative_to(root.resolve()))


def artifact(path: Path, root: Path) -> dict[str, Any]:
    return {
        "path": str(path.resolve().relative_to(root.resolve())),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
    }


def resolve_artifact(root: Path, descriptor: dict[str, Any]) -> Path:
    path = root / str(descriptor.get("path") or "")
    if not path.is_file():
        raise RuntimeError(f"missing artifact: {path}")
    if path.stat().st_size != int(descriptor.get("bytes") or -1):
        raise RuntimeError(f"artifact size changed: {path}")
    if sha256(path) != str(descriptor.get("sha256") or ""):
        raise RuntimeError(f"artifact SHA-256 changed: {path}")
    return path


def check(name: str, observed: Any, expected: Any) -> dict[str, Any]:
    return {
        "name": name,
        "observed": observed,
        "expected": expected,
        "passed": observed == expected,
    }


def minimum_check(name: str, observed: float, minimum: float) -> dict[str, Any]:
    return {
        "name": name,
        "observed": round(float(observed), 9),
        "minimum": round(float(minimum), 9),
        "passed": float(observed) >= float(minimum),
    }


def maximum_check(name: str, observed: float, maximum: float) -> dict[str, Any]:
    return {
        "name": name,
        "observed": round(float(observed), 9),
        "maximum": round(float(maximum), 9),
        "passed": float(observed) <= float(maximum),
    }


def load_policy(path: Path) -> dict[str, Any]:
    policy = read_json(path)
    if policy.get("schema") != POLICY_SCHEMA:
        raise RuntimeError(f"unexpected policy schema: {path}")
    return policy


def verify_file(path: Path, expected_sha256: str) -> dict[str, Any]:
    observed = sha256(path) if path.is_file() else None
    return {
        "path": relative(path) if path.is_absolute() and path.is_relative_to(ROOT) else str(path),
        "expected_sha256": expected_sha256,
        "observed_sha256": observed,
        "passed": observed == expected_sha256,
    }


def verify_static_inputs(policy: dict[str, Any]) -> dict[str, Any]:
    controlled = policy["controlled_supervision"]
    checks = []
    for key in (
        "frozen_corpus",
        "split_manifest",
        "supervision_manifest",
        "corpus_decision",
        "replay_report",
    ):
        checks.append(verify_file(ROOT / controlled[key], controlled[f"{key}_sha256"]))

    production = policy["production_baseline"]
    checks.append(verify_file(ROOT / production["policy"], production["policy_sha256"]))

    model = policy["target_encoder"]
    model_root = Path(model["local_path"]).expanduser()
    for name, expected in sorted(model["files"].items()):
        path = model_root / name
        observed = sha256(path) if path.is_file() else None
        checks.append(
            {
                "path": f"target_encoder/{name}",
                "expected_sha256": expected,
                "observed_sha256": observed,
                "passed": observed == expected,
            }
        )

    corpus_decision = read_json(ROOT / controlled["corpus_decision"])
    replay = read_json(ROOT / controlled["replay_report"])
    production_policy = read_json(ROOT / production["policy"])
    frozen = read_json(ROOT / controlled["frozen_corpus"])
    immutable_checked = 0
    immutable_changed: list[str] = []
    for capture in frozen.get("captures", []):
        session = ROOT / "sessions" / str(capture.get("session") or "")
        for descriptor in capture.get("immutable_files", []):
            path = session / str(descriptor.get("path") or "")
            immutable_checked += 1
            if (
                not path.is_file()
                or path.stat().st_size != int(descriptor.get("bytes") or -1)
                or sha256(path) != str(descriptor.get("sha256") or "")
            ):
                immutable_changed.append(
                    f"{capture.get('session_id')}:{descriptor.get('path')}"
                )
    semantic = [
        check("controlled_decision", corpus_decision.get("decision"), controlled["decision"]),
        check("controlled_fingerprint", corpus_decision.get("fingerprint"), controlled["fingerprint"]),
        check("controlled_replay", replay.get("status"), "passed"),
        check("controlled_replay_files", replay.get("matched_files"), controlled["matched_files"]),
        check("production_decision", production_policy.get("decision"), production["decision"]),
        check(
            "production_profile",
            production_policy.get("selected_profile"),
            production["selected_profile"],
        ),
        check(
            "production_fingerprint",
            production_policy.get("corpus_fingerprint"),
            production["corpus_fingerprint"],
        ),
        check("controlled_immutable_files_changed", len(immutable_changed), 0),
    ]
    passed = all(row["passed"] for row in checks) and all(row["passed"] for row in semantic)
    return {
        "schema": "murmurmark.target_me_identifiability_preflight/v1",
        "policy_sha256": sha256(POLICY_PATH),
        "files": checks,
        "semantic": semantic,
        "controlled_immutable_files": {
            "checked": immutable_checked,
            "changed": immutable_changed,
        },
        "passed": passed,
    }


def safe_extract(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with tarfile.open(archive, "r:gz") as handle:
        for member in handle.getmembers():
            target = (destination / member.name).resolve()
            if target != root and root not in target.parents:
                raise RuntimeError(f"unsafe archive member: {member.name}")
        handle.extractall(destination, filter="data")


def prepare_sources(policy: dict[str, Any], output: Path, *, refresh: bool) -> dict[str, Any]:
    source = policy["public_speech"]
    source_root = ROOT / source["root"]
    archives_root = source_root / "archives"
    extracted_root = source_root / "extracted"
    archive_rows: list[dict[str, Any]] = []
    for name, contract in sorted(source["archives"].items()):
        path = archives_root / name
        if not path.is_file():
            raise RuntimeError(
                f"missing public speech archive: {path}\n"
                f"download: curl -fL --retry 3 {contract['url']} -o {path}"
            )
        observed_md5 = md5(path)
        if observed_md5 != contract["md5"]:
            raise RuntimeError(f"public speech archive MD5 changed: {path}")
        archive_rows.append(
            {
                "name": name,
                "url": contract["url"],
                "md5": observed_md5,
                "sha256": sha256(path),
                "bytes": path.stat().st_size,
                "split_uses": contract["split_uses"],
            }
        )

    preparation_path = source_root / "source_preparation.json"
    expected_basis = {
        "schema": "murmurmark.target_me_identifiability_source_preparation/v1",
        "dataset_id": source["dataset_id"],
        "archives": archive_rows,
    }
    existing = read_json(preparation_path) if preparation_path.is_file() else {}
    if refresh or existing.get("basis") != expected_basis or not extracted_root.is_dir():
        temporary = source_root / f".extracting-{os.getpid()}"
        shutil.rmtree(temporary, ignore_errors=True)
        temporary.mkdir(parents=True)
        for row in archive_rows:
            safe_extract(archives_root / row["name"], temporary)
        replacement = source_root / ".extracted-old"
        shutil.rmtree(replacement, ignore_errors=True)
        if extracted_root.exists():
            extracted_root.rename(replacement)
        temporary.rename(extracted_root)
        shutil.rmtree(replacement, ignore_errors=True)

    subsets: dict[str, dict[str, Any]] = {}
    for subset in ("train-clean-5", "dev-clean-2"):
        path = extracted_root / "LibriSpeech" / subset
        files = sorted(path.rglob("*.flac")) if path.is_dir() else []
        if not files:
            raise RuntimeError(f"extracted subset is empty: {path}")
        subsets[subset] = {
            "root": relative(path),
            "flac_files": len(files),
            "speakers": len({item.relative_to(path).parts[0] for item in files}),
            "duration_sec": round(sum(float(sf.info(item).duration) for item in files), 6),
        }

    license_path = extracted_root / "LibriSpeech" / "LICENSE.TXT"
    if not license_path.is_file():
        raise RuntimeError(f"LibriSpeech license file missing: {license_path}")
    payload = {
        "schema": "murmurmark.target_me_identifiability_source_preparation/v1",
        "status": "ready",
        "basis": expected_basis,
        "license": {
            "spdx": source["license"],
            "landing_page": source["landing_page"],
            "license_url": source["license_url"],
            "artifact": {
                "path": relative(license_path),
                "bytes": license_path.stat().st_size,
                "sha256": sha256(license_path),
            },
        },
        "subsets": subsets,
    }
    write_json(preparation_path, payload)
    return payload


@dataclass(frozen=True)
class PublicSpeaker:
    speaker_id: str
    split: str
    subset: str
    enrollment_files: tuple[Path, ...]
    mixture_files: tuple[Path, ...]
    enrollment_duration_sec: float
    mixture_duration_sec: float


def group_public_files(subset_root: Path) -> dict[str, list[Path]]:
    grouped: dict[str, list[Path]] = defaultdict(list)
    for path in sorted(subset_root.rglob("*.flac")):
        speaker = path.relative_to(subset_root).parts[0]
        grouped[speaker].append(path)
    return grouped


def reserve_enrollment_files(
    files: Sequence[Path], minimum_sec: float, minimum_files: int
) -> tuple[tuple[Path, ...], tuple[Path, ...], float, float]:
    enrollment: list[Path] = []
    mixture: list[Path] = []
    duration = 0.0
    for path in sorted(files):
        if len(enrollment) < minimum_files or duration < minimum_sec:
            enrollment.append(path)
            duration += float(sf.info(path).duration)
        else:
            mixture.append(path)
    mixture_duration = sum(float(sf.info(path).duration) for path in mixture)
    return tuple(enrollment), tuple(mixture), duration, mixture_duration


def select_public_speakers(policy: dict[str, Any]) -> list[PublicSpeaker]:
    source_root = ROOT / policy["public_speech"]["root"] / "extracted/LibriSpeech"
    enrollment = policy["enrollment"]
    plans: list[PublicSpeaker] = []
    used: set[str] = set()
    for split in ("train", "dev", "hard"):
        contract = policy["splits"][split]
        subset = str(contract["dataset_subset"])
        grouped = group_public_files(source_root / subset)
        required_per_speaker = (
            float(contract["minimum_full_mixture_sec"])
            / int(contract["non_target_speakers"])
        )
        candidates: list[tuple[float, str, tuple[Path, ...], tuple[Path, ...], float]] = []
        for speaker_id, files in grouped.items():
            if speaker_id in used:
                continue
            reserved, mixture, reserved_sec, mixture_sec = reserve_enrollment_files(
                files,
                float(enrollment["minimum_source_sec_per_speaker"]),
                int(enrollment["minimum_source_files_per_speaker"]),
            )
            if mixture_sec >= required_per_speaker + 8.0:
                candidates.append((mixture_sec, speaker_id, reserved, mixture, reserved_sec))
        candidates.sort(key=lambda row: (-row[0], row[1]))
        required = int(contract["non_target_speakers"])
        if len(candidates) < required:
            raise RuntimeError(
                f"subset {subset} has only {len(candidates)} speakers with enough material for {split}"
            )
        for mixture_sec, speaker_id, reserved, mixture, reserved_sec in candidates[:required]:
            used.add(speaker_id)
            plans.append(
                PublicSpeaker(
                    speaker_id=f"slr31_{speaker_id}",
                    split=split,
                    subset=subset,
                    enrollment_files=reserved,
                    mixture_files=mixture,
                    enrollment_duration_sec=reserved_sec,
                    mixture_duration_sec=mixture_sec,
                )
            )
    return plans


def rms(values: np.ndarray) -> float:
    audio = np.asarray(values, dtype=np.float64)
    return float(np.sqrt(np.mean(np.square(audio)))) if audio.size else 0.0


def snr_db(reference: np.ndarray, estimate: np.ndarray) -> float:
    expected = np.asarray(reference, dtype=np.float64)
    observed = np.asarray(estimate, dtype=np.float64)
    noise = expected - observed
    signal_power = max(float(np.mean(expected * expected)), 1.0e-30)
    noise_power = max(float(np.mean(noise * noise)), 1.0e-30)
    return float(10.0 * np.log10(signal_power / noise_power))


def cosine(left: np.ndarray, right: np.ndarray) -> float:
    a = np.asarray(left, dtype=np.float64).reshape(-1)
    b = np.asarray(right, dtype=np.float64).reshape(-1)
    denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(np.dot(a, b) / denominator) if denominator > EPSILON else 0.0


def read_audio_16k(path: Path) -> np.ndarray:
    values, sample_rate = sf.read(path, dtype="float32", always_2d=False)
    if sample_rate != 16_000:
        raise RuntimeError(f"expected 16 kHz audio: {path} ({sample_rate})")
    audio = np.asarray(values, dtype=np.float32)
    if audio.ndim == 2:
        audio = np.mean(audio, axis=1, dtype=np.float32)
    if audio.ndim != 1 or not audio.size or not np.all(np.isfinite(audio)):
        raise RuntimeError(f"invalid mono audio: {path}")
    return audio


def write_wave(path: Path, values: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # libsndfile adds a wall-clock timestamp to FLOAT WAV PEAK chunks.  scipy's
    # writer preserves the same float32 samples without volatile metadata.
    wavfile.write(path, 16_000, np.asarray(values, dtype=np.float32))


def normalized_median(vectors: Sequence[np.ndarray]) -> np.ndarray:
    if not vectors:
        raise RuntimeError("cannot build an empty enrollment")
    matrix = np.vstack([np.asarray(row, dtype=np.float64).reshape(-1) for row in vectors])
    value = np.median(matrix, axis=0)
    norm = float(np.linalg.norm(value))
    if not np.all(np.isfinite(value)) or norm <= EPSILON:
        raise RuntimeError("invalid enrollment centroid")
    return np.asarray(value / norm, dtype=np.float32)


def load_target_backend(policy: dict[str, Any]) -> Any:
    path = ROOT / "scripts/audit-target-me.py"
    spec = importlib.util.spec_from_file_location("murmurmark_identifiability_target_me", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load Target-Me backend: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    backend = module.WavLMXVectorBackend(Path(policy["target_encoder"]["local_path"]).expanduser())
    ready, reason = backend.ready()
    if not ready:
        raise RuntimeError(reason)
    return backend


def audio_chunks(values: np.ndarray, samples: int = 64_000) -> list[np.ndarray]:
    audio = np.asarray(values, dtype=np.float32)
    chunks: list[np.ndarray] = []
    for start in range(0, max(0, audio.size - samples + 1), samples):
        chunk = audio[start : start + samples]
        if chunk.size == samples and rms(chunk) >= 5.0e-4:
            chunks.append(chunk)
    if not chunks and audio.size >= int(0.5 * 16_000) and rms(audio) >= 5.0e-4:
        chunks.append(audio)
    return chunks


def embedding_centroid(backend: Any, values: np.ndarray) -> tuple[np.ndarray, int]:
    chunks = audio_chunks(values)
    vectors = backend.embed_audio_batch(chunks, batch_size=4)
    valid = [row for row in vectors if row is not None]
    if len(valid) < 2:
        raise RuntimeError(f"enrollment produced only {len(valid)} valid embeddings")
    return normalized_median(valid), len(valid)


def controlled_rows(policy: dict[str, Any]) -> dict[str, dict[str, list[dict[str, Any]]]]:
    contract = policy["controlled_supervision"]
    root = ROOT / contract["root"]
    grouped: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    split_map = {"train": "train", "dev": "dev", "hard_test": "hard"}
    for row in read_jsonl(ROOT / contract["supervision_manifest"]):
        split = split_map.get(str(row.get("split") or ""))
        kind = str(row.get("kind") or "")
        descriptor = row.get("audio") if isinstance(row.get("audio"), dict) else None
        if split is None or descriptor is None:
            continue
        path = resolve_artifact(root, descriptor)
        audio = read_audio_16k(path)
        if kind in {"measured_local_target", "opening_backchannel", "measured_remote_echo"} and rms(audio) < 5.0e-4:
            continue
        copied = dict(row)
        copied["_path"] = path
        copied["_descriptor"] = descriptor
        copied["_rms"] = rms(audio)
        grouped[split][kind].append(copied)
    for kinds in grouped.values():
        for rows in kinds.values():
            rows.sort(key=lambda row: str(row.get("clip_id") or row.get("item_id") or ""))
    return grouped


def source_file_descriptor(path: Path) -> dict[str, Any]:
    info = sf.info(path)
    return {
        "path": relative(path),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
        "sample_rate": int(info.samplerate),
        "frames": int(info.frames),
        "duration_sec": round(float(info.duration), 6),
    }


def concatenate_audio(paths: Sequence[Path], *, maximum_samples: int | None = None) -> np.ndarray:
    parts: list[np.ndarray] = []
    total = 0
    for path in paths:
        values = read_audio_16k(path)
        if maximum_samples is not None and total + values.size > maximum_samples:
            values = values[: maximum_samples - total]
        if values.size:
            parts.append(values)
            total += values.size
        if maximum_samples is not None and total >= maximum_samples:
            break
    if not parts:
        raise RuntimeError("cannot concatenate empty audio source")
    return np.concatenate(parts).astype(np.float32, copy=False)


def concatenate_controlled(rows: Sequence[dict[str, Any]]) -> np.ndarray:
    if not rows:
        raise RuntimeError("controlled source has no accepted rows")
    return np.concatenate([read_audio_16k(row["_path"]) for row in rows]).astype(np.float32)


def stream_descriptor(
    *,
    stage: Path,
    path: Path,
    values: np.ndarray,
    source_rows: Sequence[dict[str, Any]],
    source_kind: str,
) -> dict[str, Any]:
    write_wave(path, values)
    sources: list[dict[str, Any]] = []
    for row in source_rows:
        if "_path" in row:
            source = source_file_descriptor(row["_path"])
            source["source_id"] = str(row.get("clip_id") or row.get("item_id") or "")
        else:
            source = source_file_descriptor(Path(row["path"]))
            source["source_id"] = str(row.get("source_id") or Path(row["path"]).stem)
        sources.append(source)
    return {
        "kind": source_kind,
        "artifact": artifact(path, stage),
        "samples": int(values.size),
        "duration_sec": round(values.size / 16_000.0, 6),
        "source_files": sources,
        "source_fingerprint": digest_json(sources),
    }


def build_source_streams(
    *,
    policy: dict[str, Any],
    stage: Path,
    speakers: Sequence[PublicSpeaker],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], Any]:
    controlled = controlled_rows(policy)
    backend = load_target_backend(policy)
    split_streams: dict[str, Any] = {}
    enrollment_rows: list[dict[str, Any]] = []
    speaker_rows: list[dict[str, Any]] = []
    public_by_split: dict[str, list[PublicSpeaker]] = defaultdict(list)
    for speaker in speakers:
        public_by_split[speaker.split].append(speaker)

    for split in ("train", "dev", "hard"):
        target_rows = controlled[split]["measured_local_target"]
        if len(target_rows) < 6:
            raise RuntimeError(f"not enough controlled Target-Me rows in {split}")
        target_enrollment_rows = target_rows[:3]
        target_mixture_rows = target_rows[3:]
        opening_rows = controlled[split]["opening_backchannel"]
        remote_rows = controlled[split]["measured_remote_echo"]
        noise_rows = controlled[split]["keyboard_noise"] + controlled[split]["silence_background"]
        if not opening_rows or not remote_rows or not noise_rows:
            raise RuntimeError(f"controlled split {split} lacks a required source family")

        target_enrollment_audio = concatenate_controlled(target_enrollment_rows)
        target_mixture_audio = concatenate_controlled(target_mixture_rows)
        opening_audio = concatenate_controlled(opening_rows)
        remote_audio = concatenate_controlled(remote_rows)
        noise_audio = concatenate_controlled(noise_rows)
        target_root = stage / "source_streams" / split / "private_target_me_v1"
        target_enrollment_path = target_root / "enrollment.wav"
        target_vector_path = stage / "enrollments" / split / "private_target_me_v1.npy"
        target_stream = stream_descriptor(
            stage=stage,
            path=target_root / "mixture_stream.wav",
            values=target_mixture_audio,
            source_rows=target_mixture_rows,
            source_kind="target_me_mixture",
        )
        target_enrollment_stream = stream_descriptor(
            stage=stage,
            path=target_enrollment_path,
            values=target_enrollment_audio,
            source_rows=target_enrollment_rows,
            source_kind="target_me_enrollment",
        )
        opening_stream = stream_descriptor(
            stage=stage,
            path=target_root / "opening_backchannel.wav",
            values=opening_audio,
            source_rows=opening_rows,
            source_kind="target_me_opening_backchannel",
        )
        remote_stream = stream_descriptor(
            stage=stage,
            path=stage / "source_streams" / split / "remote_echo.wav",
            values=remote_audio,
            source_rows=remote_rows,
            source_kind="measured_remote_echo",
        )
        noise_stream = stream_descriptor(
            stage=stage,
            path=stage / "source_streams" / split / "other_local_noise.wav",
            values=noise_audio,
            source_rows=noise_rows,
            source_kind="measured_keyboard_background",
        )
        target_vector, target_embedding_count = embedding_centroid(backend, target_enrollment_audio)
        target_vector_path.parent.mkdir(parents=True, exist_ok=True)
        np.save(target_vector_path, target_vector, allow_pickle=False)
        target_enrollment_id = f"{split}:private_target_me_v1"
        enrollment_rows.append(
            {
                "schema": ENROLLMENT_SCHEMA,
                "enrollment_id": target_enrollment_id,
                "split": split,
                "speaker_id": "private_target_me_v1",
                "privacy_class": "private_user_voice",
                "redistribution": "forbidden",
                "source": target_enrollment_stream,
                "vector": artifact(target_vector_path, stage),
                "embedding_count": target_embedding_count,
                "backend": policy["target_encoder"]["backend"],
            }
        )
        speaker_rows.append(
            {
                "schema": SPEAKER_SCHEMA,
                "speaker_id": "private_target_me_v1",
                "split": split,
                "role": "target_me",
                "identity_cross_split": "declared_fixed_target",
                "source_cross_split": False,
                "enrollment_id": target_enrollment_id,
                "license": "private",
                "redistribution": "forbidden",
            }
        )

        other_streams: dict[str, Any] = {}
        for speaker in sorted(public_by_split[split], key=lambda row: row.speaker_id):
            rows_required = math.ceil(
                int(policy["splits"][split]["full_mixture_rows"])
                / int(policy["splits"][split]["non_target_speakers"])
            )
            required_samples = (rows_required + 2) * int(policy["audio"]["clip_samples"])
            selected_mixture: list[Path] = []
            selected_samples = 0
            for path in speaker.mixture_files:
                selected_mixture.append(path)
                selected_samples += int(sf.info(path).frames)
                if selected_samples >= required_samples:
                    break
            if selected_samples < required_samples:
                raise RuntimeError(f"speaker {speaker.speaker_id} has too little mixture audio")
            public_rows = [
                {"path": str(path), "source_id": path.stem}
                for path in selected_mixture
            ]
            enrollment_public_rows = [
                {"path": str(path), "source_id": path.stem}
                for path in speaker.enrollment_files
            ]
            mixture_audio = concatenate_audio(selected_mixture, maximum_samples=required_samples)
            enrollment_audio = concatenate_audio(speaker.enrollment_files, maximum_samples=30 * 16_000)
            public_root = stage / "source_streams" / split / speaker.speaker_id
            mixture_stream = stream_descriptor(
                stage=stage,
                path=public_root / "mixture_stream.wav",
                values=mixture_audio,
                source_rows=public_rows,
                source_kind="other_local_speech",
            )
            enrollment_stream = stream_descriptor(
                stage=stage,
                path=public_root / "enrollment.wav",
                values=enrollment_audio,
                source_rows=enrollment_public_rows,
                source_kind="other_local_enrollment",
            )
            vector, embedding_count = embedding_centroid(backend, enrollment_audio)
            vector_path = stage / "enrollments" / split / f"{speaker.speaker_id}.npy"
            vector_path.parent.mkdir(parents=True, exist_ok=True)
            np.save(vector_path, vector, allow_pickle=False)
            enrollment_id = f"{split}:{speaker.speaker_id}"
            enrollment_rows.append(
                {
                    "schema": ENROLLMENT_SCHEMA,
                    "enrollment_id": enrollment_id,
                    "split": split,
                    "speaker_id": speaker.speaker_id,
                    "privacy_class": "public_licensed_speech",
                    "redistribution": "CC-BY-4.0",
                    "source": enrollment_stream,
                    "vector": artifact(vector_path, stage),
                    "embedding_count": embedding_count,
                    "backend": policy["target_encoder"]["backend"],
                }
            )
            speaker_rows.append(
                {
                    "schema": SPEAKER_SCHEMA,
                    "speaker_id": speaker.speaker_id,
                    "split": split,
                    "role": "non_target_other_local",
                    "identity_cross_split": False,
                    "source_cross_split": False,
                    "dataset": policy["public_speech"]["dataset_id"],
                    "dataset_subset": speaker.subset,
                    "enrollment_id": enrollment_id,
                    "license": policy["public_speech"]["license"],
                    "redistribution": "allowed_with_attribution",
                    "mixture_source_sec": mixture_stream["duration_sec"],
                    "enrollment_source_sec": enrollment_stream["duration_sec"],
                }
            )
            other_streams[speaker.speaker_id] = mixture_stream

        split_streams[split] = {
            "target": target_stream,
            "opening": opening_stream,
            "remote": remote_stream,
            "noise": noise_stream,
            "other": other_streams,
        }
    return split_streams, speaker_rows, enrollment_rows, backend


def stream_audio(stage: Path, descriptor: dict[str, Any]) -> np.ndarray:
    return read_audio_16k(resolve_artifact(stage, descriptor["artifact"]))


def valid_offsets(values: np.ndarray, samples: int, *, minimum_rms: float = 5.0e-4) -> list[int]:
    offsets = [
        start
        for start in range(0, max(0, values.size - samples + 1), samples)
        if rms(values[start : start + samples]) >= minimum_rms
    ]
    if not offsets:
        raise RuntimeError("source stream has no speech-bearing fixed windows")
    return offsets


def other_local_path(values: np.ndarray, path_id: str, seed: int) -> np.ndarray:
    source = np.asarray(values, dtype=np.float64)
    random = np.random.default_rng(seed)
    if path_id == "nearfield_direct_v1":
        cutoff = 6_500.0
        reflections = ((0.004, 0.09), (0.011, -0.04))
    elif path_id == "office_reflective_v1":
        cutoff = 5_500.0
        reflections = ((0.006, 0.16), (0.017, 0.10), (0.031, -0.05))
    elif path_id == "offaxis_soft_v1":
        cutoff = 3_800.0
        reflections = ((0.009, 0.12), (0.024, 0.05))
    else:
        raise RuntimeError(f"unknown other-local path: {path_id}")
    lowpass = signal.firwin(65, cutoff, fs=16_000)
    filtered = signal.lfilter(lowpass, [1.0], source)
    impulse = np.zeros(int(0.04 * 16_000) + 1, dtype=np.float64)
    impulse[0] = 1.0
    jitter = float(random.uniform(0.98, 1.02))
    for delay_sec, gain in reflections:
        impulse[int(round(delay_sec * 16_000))] += gain * jitter
    rendered = signal.lfilter(impulse, [1.0], filtered)
    return np.asarray(rendered, dtype=np.float32)


def gain_for_rms(values: np.ndarray, desired_rms: float) -> float:
    observed = rms(values)
    return float(desired_rms / observed) if observed > EPSILON else 0.0


def source_slice(stage: Path, source: dict[str, Any]) -> np.ndarray:
    values = read_audio_16k(resolve_artifact(stage, source["stream"]))
    start = int(source["offset_samples"])
    samples = int(source["samples"])
    end = start + samples
    if start < 0 or end > values.size:
        raise RuntimeError(f"source slice exceeds stream: {source}")
    return np.asarray(values[start:end], dtype=np.float32)


def render_from_descriptor(stage: Path, row: dict[str, Any]) -> dict[str, np.ndarray]:
    sources = row["sources"]
    rendering = row["rendering"]
    target = source_slice(stage, sources["target_me"])
    remote = source_slice(stage, sources["remote_echo"])
    other = source_slice(stage, sources["other_local_speech"])
    noise = source_slice(stage, sources["other_local_noise"])
    other = other_local_path(other, rendering["other_local_path"], int(rendering["seed"]))
    gains = rendering["gains_linear"]
    components = rendering["components"]
    stems = {
        "target_me": target * float(gains["target_me"]) if components["target_me"] else np.zeros_like(target),
        "remote_echo": remote * float(gains["remote_echo"]) if components["remote_echo"] else np.zeros_like(remote),
        "other_local_speech": other * float(gains["other_local_speech"]) if components["other_local_speech"] else np.zeros_like(other),
        "other_local_noise": noise * float(gains["other_local_noise"]) if components["other_local_noise"] else np.zeros_like(noise),
    }
    common = float(rendering["common_scale"])
    stems = {key: np.asarray(value * common, dtype=np.float32) for key, value in stems.items()}
    mixture = np.asarray(sum(stems.values()), dtype=np.float32)
    return {"mixture": mixture, **stems}


def item_sources(
    *,
    stage: Path,
    streams: dict[str, Any],
    speaker_id: str,
    family: str,
    row_index: int,
    speaker_index: int,
    clip_samples: int,
    offset_cache: dict[str, list[int]],
) -> dict[str, Any]:
    target_stream = streams["opening"] if family == "opening_backchannel" else streams["target"]
    selected = {
        "target_me": target_stream,
        "remote_echo": streams["remote"],
        "other_local_speech": streams["other"][speaker_id],
        "other_local_noise": streams["noise"],
    }
    result: dict[str, Any] = {}
    for key, descriptor in selected.items():
        cache_key = f"{descriptor['artifact']['path']}:{clip_samples}"
        if cache_key not in offset_cache:
            offset_cache[cache_key] = valid_offsets(stream_audio(stage, descriptor), clip_samples)
        offsets = offset_cache[cache_key]
        selection = speaker_index if key == "other_local_speech" else row_index
        offset = offsets[selection % len(offsets)]
        result[key] = {
            "stream": descriptor["artifact"],
            "stream_kind": descriptor["kind"],
            "offset_samples": offset,
            "samples": clip_samples,
        }
    return result


def rendering_descriptor(
    *,
    policy: dict[str, Any],
    stage: Path,
    sources: dict[str, Any],
    family: str,
    seed: int,
    index: int,
    components: dict[str, bool],
) -> dict[str, Any]:
    contract = policy["rendering"]
    target = source_slice(stage, sources["target_me"])
    remote = source_slice(stage, sources["remote_echo"])
    other_path_id = contract["other_local_paths"][index % len(contract["other_local_paths"])]
    other = other_local_path(
        source_slice(stage, sources["other_local_speech"]), other_path_id, seed
    )
    noise = source_slice(stage, sources["other_local_noise"])
    reference_rms = max(rms(target), 1.0e-4)
    target_db = float(contract["target_gain_db"][index % len(contract["target_gain_db"])])
    remote_db = float(contract["remote_relative_db"][index % len(contract["remote_relative_db"])])
    other_db = float(contract["other_relative_db"][index % len(contract["other_relative_db"])])
    noise_db = float(contract["noise_relative_db"][index % len(contract["noise_relative_db"])])
    if family == "quiet_target_me":
        target_db = -15.0
    if family == "quiet_other_local":
        other_db = -15.0
    if family == "keyboard_background":
        noise_db = -12.0
    gains = {
        "target_me": gain_for_rms(target, reference_rms * 10.0 ** (target_db / 20.0)),
        "remote_echo": gain_for_rms(remote, reference_rms * 10.0 ** (remote_db / 20.0)),
        "other_local_speech": gain_for_rms(other, reference_rms * 10.0 ** (other_db / 20.0)),
        "other_local_noise": gain_for_rms(noise, reference_rms * 10.0 ** (noise_db / 20.0)),
    }
    preliminary = (
        (target * gains["target_me"] if components["target_me"] else 0.0)
        + (remote * gains["remote_echo"] if components["remote_echo"] else 0.0)
        + (other * gains["other_local_speech"] if components["other_local_speech"] else 0.0)
        + (noise * gains["other_local_noise"] if components["other_local_noise"] else 0.0)
    )
    peak = float(np.max(np.abs(preliminary))) if np.size(preliminary) else 0.0
    common_scale = min(1.0, float(policy["audio"]["peak_limit"]) / max(peak, EPSILON))
    return {
        "seed": seed,
        "other_local_path": other_path_id,
        "family": family,
        "components": components,
        "gains_db": {
            "target_me": target_db,
            "remote_echo": remote_db,
            "other_local_speech": other_db,
            "other_local_noise": noise_db,
        },
        "gains_linear": gains,
        "common_scale": common_scale,
        "peak_before_common_scale": peak,
    }


def write_item_audio(stage: Path, item_id: str, split: str, rendered: dict[str, np.ndarray]) -> dict[str, Any]:
    root = stage / "audio" / split / item_id
    result: dict[str, Any] = {}
    for kind, values in rendered.items():
        path = root / f"{kind}.wav"
        write_wave(path, values)
        result[kind] = artifact(path, stage)
    return result


def make_item(
    *,
    policy: dict[str, Any],
    stage: Path,
    split: str,
    family: str,
    speaker_id: str,
    sources: dict[str, Any],
    seed: int,
    index: int,
    components: dict[str, bool],
    usage: str,
) -> dict[str, Any]:
    rendering = rendering_descriptor(
        policy=policy,
        stage=stage,
        sources=sources,
        family=family,
        seed=seed,
        index=index,
        components=components,
    )
    identity = {
        "split": split,
        "family": family,
        "speaker_id": speaker_id,
        "sources": sources,
        "rendering": rendering,
        "usage": usage,
    }
    item_id = digest_json(identity)[:24]
    row = {
        "schema": ITEM_SCHEMA,
        "item_id": item_id,
        "split": split,
        "family": family,
        "usage": usage,
        "duration_sec": float(policy["audio"]["clip_duration_sec"]),
        "target_speaker_id": "private_target_me_v1",
        "other_local_speaker_id": speaker_id,
        "sources": sources,
        "rendering": rendering,
    }
    rendered = render_from_descriptor(stage, row)
    row["audio"] = write_item_audio(stage, item_id, split, rendered)
    reconstruction = (
        rendered["target_me"]
        + rendered["remote_echo"]
        + rendered["other_local_speech"]
        + rendered["other_local_noise"]
    )
    row["metrics"] = {
        "peak": round(float(np.max(np.abs(rendered["mixture"]))), 9),
        "reconstruction_max_abs_error": round(
            float(np.max(np.abs(rendered["mixture"] - reconstruction))), 12
        ),
        "finite": bool(all(np.all(np.isfinite(values)) for values in rendered.values())),
    }
    return row


def enrollment_map(rows: Sequence[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    return {(str(row["split"]), str(row["speaker_id"])): row for row in rows}


def build_queries(
    *, items: Sequence[dict[str, Any]], enrollments: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    lookup = enrollment_map(enrollments)
    rows: list[dict[str, Any]] = []
    for item in items:
        split = str(item["split"])
        target = lookup[(split, "private_target_me_v1")]
        other = lookup[(split, str(item["other_local_speaker_id"]))]
        for role, correct, wrong, expected in (
            ("target_me", target, other, "target_me"),
            ("other_local_speech", other, target, "other_local_speech"),
        ):
            rows.append(
                {
                    "schema": QUERY_SCHEMA,
                    "query_id": digest_json([item["item_id"], role])[:24],
                    "item_id": item["item_id"],
                    "split": split,
                    "query_role": role,
                    "query_speaker_id": correct["speaker_id"],
                    "correct_enrollment_id": correct["enrollment_id"],
                    "correct_enrollment": correct["vector"],
                    "wrong_enrollment_id": wrong["enrollment_id"],
                    "wrong_enrollment": wrong["vector"],
                    "mixture": item["audio"]["mixture"],
                    "expected_target_kind": expected,
                    "expected_target": item["audio"][expected],
                    "speaker_present": bool(item["rendering"]["components"][expected]),
                }
            )
    return rows


def generate_items(
    *,
    policy: dict[str, Any],
    stage: Path,
    split_streams: dict[str, Any],
    speaker_rows: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_split: dict[str, list[str]] = defaultdict(list)
    for row in speaker_rows:
        if row["role"] == "non_target_other_local":
            by_split[str(row["split"])].append(str(row["speaker_id"]))
    offset_cache: dict[str, list[int]] = {}
    items: list[dict[str, Any]] = []
    split_offsets = {"train": 0, "dev": 1_000_000, "hard": 2_000_000}
    base_seed = int(policy["rendering"]["seed"])
    clip_samples = int(policy["audio"]["clip_samples"])
    all_components = {
        "target_me": True,
        "remote_echo": True,
        "other_local_speech": True,
        "other_local_noise": True,
    }
    control_components = {
        "target_only": {"target_me": True, "remote_echo": False, "other_local_speech": False, "other_local_noise": False},
        "remote_only": {"target_me": False, "remote_echo": True, "other_local_speech": False, "other_local_noise": False},
        "other_speaker_only": {"target_me": False, "remote_echo": False, "other_local_speech": True, "other_local_noise": False},
        "target_remote": {"target_me": True, "remote_echo": True, "other_local_speech": False, "other_local_noise": False},
        "target_other": {"target_me": True, "remote_echo": False, "other_local_speech": True, "other_local_noise": False},
    }
    for split in ("train", "dev", "hard"):
        speakers = sorted(by_split[split])
        streams = split_streams[split]
        families = list(policy["rendering"]["families"])
        speaker_counts: Counter[str] = Counter()
        full_count = int(policy["splits"][split]["full_mixture_rows"])
        first_sources: dict[str, dict[str, Any]] = {}
        for index in range(full_count):
            speaker = speakers[index % len(speakers)]
            family = families[index % len(families)]
            sources = item_sources(
                stage=stage,
                streams=streams,
                speaker_id=speaker,
                family=family,
                row_index=index,
                speaker_index=speaker_counts[speaker],
                clip_samples=clip_samples,
                offset_cache=offset_cache,
            )
            speaker_counts[speaker] += 1
            first_sources.setdefault(speaker, sources)
            seed = base_seed + split_offsets[split] + index
            items.append(
                make_item(
                    policy=policy,
                    stage=stage,
                    split=split,
                    family=family,
                    speaker_id=speaker,
                    sources=sources,
                    seed=seed,
                    index=index,
                    components=dict(all_components),
                    usage="full_three_source",
                )
            )
        for speaker_index, speaker in enumerate(speakers):
            sources = first_sources[speaker]
            for control_index, (family, components) in enumerate(control_components.items()):
                index = full_count + speaker_index * len(control_components) + control_index
                seed = base_seed + split_offsets[split] + index
                items.append(
                    make_item(
                        policy=policy,
                        stage=stage,
                        split=split,
                        family=family,
                        speaker_id=speaker,
                        sources=sources,
                        seed=seed,
                        index=index,
                        components=dict(components),
                        usage="identity_control",
                    )
                )
    return sorted(items, key=lambda row: (row["split"], row["item_id"]))


def source_path_sets(split_streams: dict[str, Any]) -> tuple[dict[str, set[str]], dict[str, dict[str, set[str]]]]:
    by_split: dict[str, set[str]] = defaultdict(set)
    enrollment_and_mixture: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: {"enrollment": set(), "mixture": set()}
    )
    for split, streams in split_streams.items():
        for name in ("target", "opening", "remote", "noise"):
            for row in streams[name]["source_files"]:
                by_split[split].add(str(row["path"]))
        enrollment_and_mixture[f"{split}:private_target_me_v1"]["mixture"].update(
            str(row["path"]) for row in streams["target"]["source_files"]
        )
        for speaker_id, descriptor in streams["other"].items():
            paths = {str(row["path"]) for row in descriptor["source_files"]}
            by_split[split].update(paths)
            enrollment_and_mixture[f"{split}:{speaker_id}"]["mixture"].update(paths)
    return by_split, enrollment_and_mixture


def attach_enrollment_source_sets(
    enrollment_and_mixture: dict[str, dict[str, set[str]]],
    enrollments: Sequence[dict[str, Any]],
) -> None:
    for row in enrollments:
        key = f"{row['split']}:{row['speaker_id']}"
        enrollment_and_mixture[key]["enrollment"].update(
            str(source["path"]) for source in row["source"]["source_files"]
        )


def source_overlap_count(by_split: dict[str, set[str]]) -> int:
    owners: dict[str, list[str]] = defaultdict(list)
    for split, paths in by_split.items():
        for path in paths:
            owners[path].append(split)
    return sum(1 for splits in owners.values() if len(set(splits)) > 1)


def non_target_identity_overlap(speakers: Sequence[dict[str, Any]]) -> int:
    owners: dict[str, set[str]] = defaultdict(set)
    for row in speakers:
        if row["role"] == "non_target_other_local":
            owners[str(row["speaker_id"])].add(str(row["split"]))
    return sum(1 for splits in owners.values() if len(splits) > 1)


def enrollment_mixture_overlap(
    source_sets: dict[str, dict[str, set[str]]]
) -> int:
    return sum(
        len(value["enrollment"] & value["mixture"])
        for value in source_sets.values()
    )


def probe_enrollment_margins(
    *,
    stage: Path,
    split_streams: dict[str, Any],
    enrollments: Sequence[dict[str, Any]],
    backend: Any,
) -> list[dict[str, Any]]:
    lookup = enrollment_map(enrollments)
    result: list[dict[str, Any]] = []

    def probe_vector(descriptor: dict[str, Any]) -> np.ndarray:
        values = stream_audio(stage, descriptor)
        chunks = audio_chunks(values)[:3]
        vectors = backend.embed_audio_batch(chunks, batch_size=3)
        valid = [row for row in vectors if row is not None]
        if not valid:
            raise RuntimeError(f"speaker probe failed: {descriptor['artifact']['path']}")
        return normalized_median(valid)

    for split in ("train", "dev", "hard"):
        target_enrollment = np.load(
            resolve_artifact(stage, lookup[(split, "private_target_me_v1")]["vector"]),
            allow_pickle=False,
        )
        target_probe = probe_vector(split_streams[split]["target"])
        for speaker_id, descriptor in sorted(split_streams[split]["other"].items()):
            other_enrollment = np.load(
                resolve_artifact(stage, lookup[(split, speaker_id)]["vector"]),
                allow_pickle=False,
            )
            other_probe = probe_vector(descriptor)
            for role, probe, correct, wrong in (
                ("target_me", target_probe, target_enrollment, other_enrollment),
                ("other_local_speech", other_probe, other_enrollment, target_enrollment),
            ):
                correct_similarity = cosine(probe, correct)
                wrong_similarity = cosine(probe, wrong)
                result.append(
                    {
                        "schema": "murmurmark.target_me_identifiability_enrollment_probe/v1",
                        "split": split,
                        "speaker_id": "private_target_me_v1" if role == "target_me" else speaker_id,
                        "opposing_speaker_id": speaker_id if role == "target_me" else "private_target_me_v1",
                        "role": role,
                        "correct_similarity": round(correct_similarity, 9),
                        "wrong_similarity": round(wrong_similarity, 9),
                        "margin": round(correct_similarity - wrong_similarity, 9),
                    }
                )
    return result


def exact_zero_snr(reference: np.ndarray, estimate: np.ndarray) -> float:
    if float(np.max(np.abs(reference - estimate))) <= 1.0e-8:
        return 120.0
    return snr_db(reference, estimate)


def replay_sources(stage: Path, split_streams: dict[str, Any]) -> tuple[int, int, float]:
    matched = 0
    changed = 0
    minimum_snr = 120.0
    descriptors: list[dict[str, Any]] = []
    for streams in split_streams.values():
        descriptors.extend(streams[name] for name in ("target", "opening", "remote", "noise"))
        descriptors.extend(streams["other"].values())
    seen: set[str] = set()
    for descriptor in descriptors:
        key = str(descriptor["artifact"]["path"])
        if key in seen:
            continue
        seen.add(key)
        source_values = concatenate_audio(
            [ROOT / str(row["path"]) for row in descriptor["source_files"]],
            maximum_samples=int(descriptor["samples"]),
        )
        observed = stream_audio(stage, descriptor)
        score = exact_zero_snr(source_values, observed)
        minimum_snr = min(minimum_snr, score)
        if score >= 80.0:
            matched += 1
        else:
            changed += 1
    return matched, changed, minimum_snr


def replay_items(stage: Path, items: Sequence[dict[str, Any]]) -> tuple[int, int, float, float]:
    matched = 0
    changed = 0
    minimum_snr = 120.0
    maximum_error = 0.0
    for row in items:
        rendered = render_from_descriptor(stage, row)
        for kind, expected in rendered.items():
            observed = read_audio_16k(resolve_artifact(stage, row["audio"][kind]))
            score = exact_zero_snr(expected, observed)
            error = float(np.max(np.abs(expected - observed)))
            minimum_snr = min(minimum_snr, score)
            maximum_error = max(maximum_error, error)
            if score >= 80.0 or error <= 1.0e-7:
                matched += 1
            else:
                changed += 1
    return matched, changed, minimum_snr, maximum_error


def evaluate_corpus(
    *,
    policy: dict[str, Any],
    stage: Path,
    static: dict[str, Any],
    split_streams: dict[str, Any],
    speakers: Sequence[dict[str, Any]],
    enrollments: Sequence[dict[str, Any]],
    items: Sequence[dict[str, Any]],
    queries: Sequence[dict[str, Any]],
    backend: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    by_split, enrollment_sources = source_path_sets(split_streams)
    attach_enrollment_source_sets(enrollment_sources, enrollments)
    non_target_counts = Counter(
        str(row["split"])
        for row in speakers
        if row["role"] == "non_target_other_local"
    )
    full_seconds = Counter()
    family_coverage: dict[str, set[str]] = defaultdict(set)
    clipped = 0
    finite_failures = 0
    reconstruction_max = 0.0
    seeds_by_split: dict[str, set[int]] = defaultdict(set)
    seed_owners: dict[int, set[str]] = defaultdict(set)
    for row in items:
        split = str(row["split"])
        family_coverage[split].add(str(row["family"]))
        if row["usage"] == "full_three_source":
            full_seconds[split] += float(row["duration_sec"])
        clipped += int(float(row["metrics"]["peak"]) > float(policy["audio"]["peak_limit"]) + 1.0e-7)
        finite_failures += int(not row["metrics"]["finite"])
        reconstruction_max = max(
            reconstruction_max, float(row["metrics"]["reconstruction_max_abs_error"])
        )
        seed = int(row["rendering"]["seed"])
        seeds_by_split[split].add(seed)
        seed_owners[seed].add(split)

    query_count = Counter(str(row["item_id"]) for row in queries)
    missing_query_controls = sum(1 for row in items if query_count[row["item_id"]] != 2)
    queries_by_item: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in queries:
        queries_by_item[str(row["item_id"])].append(row)
    failed_swaps = 0
    for item in items:
        if not (
            item["rendering"]["components"]["target_me"]
            and item["rendering"]["components"]["other_local_speech"]
        ):
            continue
        pair = queries_by_item[item["item_id"]]
        if len(pair) != 2:
            failed_swaps += 1
            continue
        mixture_hashes = {row["mixture"]["sha256"] for row in pair}
        target_hashes = {row["expected_target"]["sha256"] for row in pair}
        enrollment_hashes = {row["correct_enrollment"]["sha256"] for row in pair}
        if len(mixture_hashes) != 1 or len(target_hashes) != 2 or len(enrollment_hashes) != 2:
            failed_swaps += 1

    required_families = set(policy["rendering"]["families"]) | set(
        policy["rendering"]["control_families"]
    )
    missing_families = {
        split: sorted(required_families - family_coverage[split])
        for split in ("train", "dev", "hard")
    }
    seed_overlap = sum(1 for owners in seed_owners.values() if len(owners) > 1)
    enrollment_overlap = enrollment_mixture_overlap(enrollment_sources)
    path_overlap = source_overlap_count(by_split)
    identity_overlap = non_target_identity_overlap(speakers)
    probes = probe_enrollment_margins(
        stage=stage,
        split_streams=split_streams,
        enrollments=enrollments,
        backend=backend,
    )
    median_margin = float(np.median([float(row["margin"]) for row in probes])) if probes else -1.0
    source_matched, source_changed, source_snr = replay_sources(stage, split_streams)
    item_matched, item_changed, item_snr, item_error = replay_items(stage, items)
    replay = {
        "schema": REPLAY_SCHEMA,
        "status": "passed" if source_changed == 0 and item_changed == 0 else "failed",
        "source_streams_matched": source_matched,
        "source_streams_changed": source_changed,
        "item_audio_files_matched": item_matched,
        "item_audio_files_changed": item_changed,
        "matched_files": source_matched + item_matched,
        "changed_files": source_changed + item_changed,
        "minimum_source_stream_snr_db": round(source_snr, 6),
        "minimum_item_audio_snr_db": round(item_snr, 6),
        "maximum_item_audio_error": round(item_error, 12),
    }

    gates: list[dict[str, Any]] = [
        check("static_inputs", static["passed"], True),
        maximum_check("non_target_speaker_overlap", identity_overlap, policy["gates"]["non_target_speaker_overlap_max"]),
        maximum_check("source_file_overlap", path_overlap, policy["gates"]["source_file_overlap_max"]),
        maximum_check("enrollment_mixture_overlap", enrollment_overlap, policy["gates"]["enrollment_mixture_overlap_max"]),
        maximum_check("render_seed_overlap", seed_overlap, policy["gates"]["render_seed_overlap_max"]),
        maximum_check("missing_query_controls", missing_query_controls, policy["gates"]["missing_query_controls_max"]),
        maximum_check("failed_enrollment_swaps", failed_swaps, policy["gates"]["failed_enrollment_swaps_max"]),
        maximum_check("reconstruction_max_abs_error", reconstruction_max, policy["gates"]["reconstruction_max_abs_error"]),
        minimum_check("source_replay_snr_db", min(source_snr, item_snr), policy["gates"]["source_replay_snr_db_min"]),
        maximum_check("clipped_items", clipped, policy["gates"]["clipped_items_max"]),
        maximum_check("non_finite_items", finite_failures, 0),
        maximum_check("changed_controlled_files", len(static["controlled_immutable_files"]["changed"]), policy["gates"]["changed_controlled_files_max"]),
        maximum_check("changed_raw_caf_files", len([row for row in static["controlled_immutable_files"]["changed"] if row.endswith(".caf")]), policy["gates"]["changed_raw_caf_files_max"]),
        maximum_check("replay_changed_files", replay["changed_files"], 0),
        minimum_check("enrollment_similarity_margin_median", median_margin, policy["enrollment"]["minimum_embedding_similarity_margin_median"]),
    ]
    for split in ("train", "dev", "hard"):
        gates.extend(
            [
                minimum_check(
                    f"{split}_non_target_speakers",
                    non_target_counts[split],
                    policy["gates"]["minimum_non_target_speakers"][split],
                ),
                minimum_check(
                    f"{split}_full_mixture_sec",
                    full_seconds[split],
                    policy["gates"]["minimum_full_mixture_sec"][split],
                ),
                check(f"{split}_family_coverage", missing_families[split], []),
            ]
        )
    oracle = {
        "schema": ORACLE_SCHEMA,
        "passed": all(row["passed"] for row in gates),
        "gates": gates,
        "coverage": {
            "non_target_speakers": dict(sorted(non_target_counts.items())),
            "full_mixture_sec": {key: round(value, 3) for key, value in sorted(full_seconds.items())},
            "families": {key: sorted(value) for key, value in sorted(family_coverage.items())},
            "missing_families": missing_families,
            "items": len(items),
            "queries": len(queries),
            "enrollments": len(enrollments),
        },
        "isolation": {
            "non_target_speaker_overlap": identity_overlap,
            "source_file_overlap": path_overlap,
            "enrollment_mixture_overlap": enrollment_overlap,
            "render_seed_overlap": seed_overlap,
        },
        "audio": {
            "reconstruction_max_abs_error": round(reconstruction_max, 12),
            "clipped_items": clipped,
            "non_finite_items": finite_failures,
        },
        "query_controls": {
            "missing": missing_query_controls,
            "failed_swaps": failed_swaps,
        },
        "enrollment_probes": probes,
        "enrollment_similarity_margin_median": round(median_margin, 9),
    }
    return oracle, replay


def manifest_fingerprint(paths: Sequence[Path]) -> dict[str, Any]:
    rows = [
        {"name": path.name, "bytes": path.stat().st_size, "sha256": sha256(path)}
        for path in paths
    ]
    return {"files": rows, "fingerprint": digest_json(rows)}


def write_decision_markdown(path: Path, decision: dict[str, Any], oracle: dict[str, Any]) -> None:
    failures = [row["name"] for row in oracle["gates"] if not row["passed"]]
    lines = [
        "# Target-Me Identifiability Corpus v1",
        "",
        f"Decision: **{decision['decision']}**",
        "",
        f"Fingerprint: `{decision['fingerprint']}`",
        "",
        f"- Corpus items: `{oracle['coverage']['items']}`.",
        f"- Query controls: `{oracle['coverage']['queries']}`.",
        f"- Enrollment controls: `{oracle['coverage']['enrollments']}`.",
        f"- Non-target speakers: `{oracle['coverage']['non_target_speakers']}`.",
        f"- Full-mixture seconds: `{oracle['coverage']['full_mixture_sec']}`.",
        f"- Enrollment margin median: `{oracle['enrollment_similarity_margin_median']}`.",
        f"- Failed gates: `{failures}`.",
        "",
        "This result authorizes evidence for a later separator experiment only. Production remains",
        "Speaker-Preserving Neural Echo v2, and no model was trained by this corpus build.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def current_publication(output: Path) -> Path | None:
    pointer = output / "current.json"
    if not pointer.is_file():
        return None
    payload = read_json(pointer)
    path = output / str(payload.get("publication") or "")
    return path if path.is_dir() else None


def verify_publication(
    publication: Path, policy: dict[str, Any] | None = None
) -> dict[str, Any]:
    decision = read_json(publication / "corpus_decision.json")
    oracle = read_json(publication / "oracle_report.json")
    replay = read_json(publication / "replay_report.json")
    manifest = read_json(publication / "publication_manifest.json")
    manifest_rows = manifest.get("files", [])
    changed: list[str] = []
    for row in manifest_rows:
        path = publication / str(row["path"])
        if (
            not path.is_file()
            or path.stat().st_size != int(row["bytes"])
            or sha256(path) != str(row["sha256"])
        ):
            changed.append(str(row["path"]))
    listed_paths = {str(row.get("path") or "") for row in manifest_rows}
    actual_paths = {
        str(path.relative_to(publication))
        for path in publication.rglob("*")
        if path.is_file() and path.name != "publication_manifest.json"
    }
    basis = decision.get("basis") if isinstance(decision.get("basis"), dict) else {}
    generator = basis.get("generator") if isinstance(basis.get("generator"), dict) else {}
    basis_manifests = (
        basis.get("manifests") if isinstance(basis.get("manifests"), dict) else {}
    )
    basis_rows = basis_manifests.get("files", [])
    basis_files_match = all(
        (publication / str(row.get("name") or "")).is_file()
        and (publication / str(row.get("name") or "")).stat().st_size
        == int(row.get("bytes") or -1)
        and sha256(publication / str(row.get("name") or ""))
        == str(row.get("sha256") or "")
        for row in basis_rows
    )
    static = verify_static_inputs(policy) if policy is not None else None
    expected_fingerprint = digest_json(basis)
    passed = (
        not changed
        and listed_paths == actual_paths
        and manifest.get("tree_fingerprint") == digest_json(manifest_rows)
        and basis_manifests.get("fingerprint") == digest_json(basis_rows)
        and basis_files_match
        and decision.get("schema") == DECISION_SCHEMA
        and bool(generator.get("name"))
        and bool(generator.get("version"))
        and decision.get("fingerprint") == expected_fingerprint
        and decision.get("fingerprint") == publication.name
        and manifest.get("fingerprint") == decision.get("fingerprint")
        and manifest.get("schema") == "murmurmark.target_me_identifiability_publication/v1"
        and oracle.get("schema") == ORACLE_SCHEMA
        and replay.get("schema") == REPLAY_SCHEMA
        and (static is None or static.get("passed") is True)
        and (policy is None or basis.get("policy_sha256") == sha256(POLICY_PATH))
        and decision.get("decision") in {READY, DO_NOT_TRAIN}
        and bool(oracle.get("passed")) == (decision.get("decision") == READY)
        and replay.get("status") == "passed"
    )
    return {
        "schema": "murmurmark.target_me_identifiability_publication_verification/v1",
        "publication": str(publication),
        "decision": decision.get("decision"),
        "fingerprint": decision.get("fingerprint"),
        "files_checked": len(manifest_rows),
        "changed_files": changed,
        "tree_complete": listed_paths == actual_paths,
        "static_inputs_passed": None if static is None else bool(static.get("passed")),
        "passed": passed,
    }


def build_corpus(policy: dict[str, Any], output: Path, *, refresh: bool) -> dict[str, Any]:
    existing = current_publication(output)
    if existing is not None and not refresh:
        verification = verify_publication(existing, policy)
        if verification["passed"]:
            return verification
    static = verify_static_inputs(policy)
    if not static["passed"]:
        raise RuntimeError("Target-Me corpus preflight failed")
    preparation = prepare_sources(policy, output, refresh=False)
    speakers = select_public_speakers(policy)
    output.mkdir(parents=True, exist_ok=True)
    stage = output / f".staging-{os.getpid()}"
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    try:
        split_streams, speaker_rows, enrollment_rows, backend = build_source_streams(
            policy=policy,
            stage=stage,
            speakers=speakers,
        )
        items = generate_items(
            policy=policy,
            stage=stage,
            split_streams=split_streams,
            speaker_rows=speaker_rows,
        )
        queries = build_queries(items=items, enrollments=enrollment_rows)
        source_manifest = {
            "schema": SOURCE_SCHEMA,
            "generator": {
                "name": Path(__file__).name,
                "version": SCRIPT_VERSION,
            },
            "policy_sha256": sha256(POLICY_PATH),
            "public_source_preparation": preparation,
            "static_inputs": static,
            "streams": split_streams,
        }
        split_manifest = {
            "schema": "murmurmark.target_me_identifiability_split/v1",
            "target_identity_policy": {
                "speaker_id": policy["enrollment"]["target_identity"],
                "identity_cross_split": True,
                "source_cross_split": False,
            },
            "splits": {
                split: {
                    "non_target_speakers": sorted(
                        row["speaker_id"]
                        for row in speaker_rows
                        if row["split"] == split and row["role"] == "non_target_other_local"
                    ),
                    "items": sum(1 for row in items if row["split"] == split),
                    "queries": sum(1 for row in queries if row["split"] == split),
                }
                for split in ("train", "dev", "hard")
            },
        }
        write_json(stage / "source_manifest.json", source_manifest)
        write_json(stage / "split_manifest.json", split_manifest)
        write_json(stage / "speaker_manifest.json", {"schema": SPEAKER_SCHEMA, "speakers": speaker_rows})
        write_jsonl(stage / "enrollment_manifest.jsonl", enrollment_rows)
        write_jsonl(stage / "item_manifest.jsonl", items)
        write_jsonl(stage / "query_manifest.jsonl", queries)
        oracle, replay = evaluate_corpus(
            policy=policy,
            stage=stage,
            static=static,
            split_streams=split_streams,
            speakers=speaker_rows,
            enrollments=enrollment_rows,
            items=items,
            queries=queries,
            backend=backend,
        )
        write_json(stage / "oracle_report.json", oracle)
        write_json(stage / "replay_report.json", replay)
        privacy = {
            "schema": "murmurmark.target_me_identifiability_privacy_licensing/v1",
            "processing": "local_only",
            "network_used_by_builder": False,
            "contains_private_user_voice": True,
            "redistribution": "forbidden_as_combined_corpus",
            "public_other_local_source": {
                "dataset": policy["public_speech"]["dataset_id"],
                "license": policy["public_speech"]["license"],
                "landing_page": policy["public_speech"]["landing_page"],
            },
            "tracked_audio_allowed": False,
            "meeting_text_in_reports": False,
        }
        write_json(stage / "privacy_licensing_manifest.json", privacy)
        data_card = {
            "schema": "murmurmark.target_me_identifiability_data_card/v1",
            "purpose": "prove speaker-query identifiability before target-conditioned training",
            "language_scope": {
                "target_me": "Russian controlled phrases",
                "non_target": "English read speech",
                "known_limit": "language-matched Russian non-target speech remains a later robustness extension",
            },
            "target_identity_count": 1,
            "target_identity_cross_split": "intentional_fixed_production_query",
            "non_target_identity_count": len(
                {row["speaker_id"] for row in speaker_rows if row["role"] == "non_target_other_local"}
            ),
            "paired_enrollment_swap_supervision": True,
            "training_performed": False,
            "production_changed": False,
            "production_profile": policy["production_baseline"]["selected_profile"],
        }
        write_json(stage / "data_card.json", data_card)
        basis_paths = [
            stage / "source_manifest.json",
            stage / "split_manifest.json",
            stage / "speaker_manifest.json",
            stage / "enrollment_manifest.jsonl",
            stage / "item_manifest.jsonl",
            stage / "query_manifest.jsonl",
            stage / "oracle_report.json",
            stage / "replay_report.json",
            stage / "privacy_licensing_manifest.json",
            stage / "data_card.json",
        ]
        basis = {
            "schema": DECISION_SCHEMA,
            "generator": {
                "name": Path(__file__).name,
                "version": SCRIPT_VERSION,
            },
            "policy_sha256": sha256(POLICY_PATH),
            "manifests": manifest_fingerprint(basis_paths),
            "oracle_passed": oracle["passed"],
            "replay_status": replay["status"],
            "training_performed": False,
            "production_changed": False,
        }
        decision = {
            "schema": DECISION_SCHEMA,
            "decision": READY if oracle["passed"] and replay["status"] == "passed" else DO_NOT_TRAIN,
            "fingerprint": digest_json(basis),
            "basis": basis,
            "failed_gates": [row["name"] for row in oracle["gates"] if not row["passed"]],
            "training_performed": False,
            "production_changed": False,
            "production_profile": policy["production_baseline"]["selected_profile"],
        }
        write_json(stage / "corpus_decision.json", decision)
        write_decision_markdown(stage / "corpus_decision.md", decision, oracle)
        publication_files: list[dict[str, Any]] = []
        for path in sorted(item for item in stage.rglob("*") if item.is_file()):
            publication_files.append(artifact(path, stage))
        publication_manifest = {
            "schema": "murmurmark.target_me_identifiability_publication/v1",
            "fingerprint": decision["fingerprint"],
            "files": publication_files,
            "tree_fingerprint": digest_json(publication_files),
        }
        write_json(stage / "publication_manifest.json", publication_manifest)
        publication = output / "published" / decision["fingerprint"]
        publication.parent.mkdir(parents=True, exist_ok=True)
        if publication.exists():
            existing_verification = verify_publication(publication, policy)
            if not existing_verification["passed"]:
                raise RuntimeError(f"existing publication is invalid: {publication}")
            shutil.rmtree(stage)
        else:
            stage.rename(publication)
        pointer = {
            "schema": "murmurmark.target_me_identifiability_current/v1",
            "fingerprint": decision["fingerprint"],
            "decision": decision["decision"],
            "publication": str(publication.relative_to(output)),
        }
        pointer_tmp = output / ".current.json.tmp"
        write_json(pointer_tmp, pointer)
        pointer_tmp.replace(output / "current.json")
        return verify_publication(publication, policy)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def main() -> int:
    args = parser().parse_args()
    policy_path = args.policy.resolve()
    output = args.output.resolve()
    global POLICY_PATH
    POLICY_PATH = policy_path
    policy = load_policy(policy_path)
    try:
        os.nice(20)
    except OSError:
        pass
    if args.command == "prepare":
        result = prepare_sources(policy, output, refresh=bool(args.refresh))
    elif args.command == "build":
        result = build_corpus(policy, output, refresh=bool(args.refresh))
    elif args.command == "verify":
        publication = current_publication(output)
        if publication is None:
            raise RuntimeError(f"no current Target-Me corpus publication under {output}")
        result = verify_publication(publication, policy)
    else:
        prepare_sources(policy, output, refresh=bool(args.refresh))
        result = build_corpus(policy, output, refresh=bool(args.refresh))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("passed", result.get("status") == "ready") else 1


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--policy", type=Path, default=POLICY_PATH)
    value.add_argument("--output", type=Path, default=OUTPUT_ROOT)
    sub = value.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--refresh", action="store_true")
    build = sub.add_parser("build")
    build.add_argument("--refresh", action="store_true")
    sub.add_parser("verify")
    all_command = sub.add_parser("all")
    all_command.add_argument("--refresh", action="store_true")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
