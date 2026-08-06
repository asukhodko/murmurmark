#!/usr/bin/env python3
"""Qualify the pinned SepFormer as a four-stem Target-Me adapter on frozen train/dev."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import resource
import shutil
import socket
import sys
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
import soundfile as sf
from scipy import signal


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from murmurmark_resource_policy import apply_resource_policy, resolve_resource_policy  # noqa: E402


SCHEMA = "murmurmark.sepformer_four_stem_target_me_qualification_policy/v1"
PROFILE = "sepformer_four_stem_target_me_qualification_v1"
READY = "READY_FOR_STRONGER_SEPARATOR_HARD_TEST"
REJECTED = "DO_NOT_ADVANCE_STRONGER_SEPARATOR"
RESOURCE_LIMIT = "CURRENT_RESOURCE_LIMIT_REACHED"
TRAIN_LOCKED = "TRAIN_CALIBRATION_LOCKED"
TRAIN_REJECTED = "TRAIN_CALIBRATION_REJECTED"
TRAIN_RESOURCE_LIMIT = "TRAIN_RESOURCE_LIMIT_REACHED"
DEV_LOCKED = "DEV_CANDIDATE_LOCKED"
DEV_REJECTED = "DEV_CANDIDATE_REJECTED"
DEV_RESOURCE_LIMIT = "DEV_RESOURCE_LIMIT_REACHED"
EPSILON = 1.0e-10


def load_script(name: str, filename: str) -> Any:
    path = ROOT / "scripts" / filename
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


TARGET = load_script("murmurmark_target_corpus_v1", "target-me-identifiability-corpus-v1.py")


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


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(temporary, path)


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        for row in rows:
            handle.write(canonical_json(row) + "\n")
        temporary = Path(handle.name)
    os.replace(temporary, path)


def write_npy(path: Path, values: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
        np.save(handle, values, allow_pickle=False)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def with_fingerprint(value: dict[str, Any]) -> dict[str, Any]:
    result = dict(value)
    result["fingerprint"] = digest_json(result)
    return result


def fingerprint_valid(value: dict[str, Any]) -> bool:
    expected = value.get("fingerprint")
    body = {key: item for key, item in value.items() if key != "fingerprint"}
    return isinstance(expected, str) and expected == digest_json(body)


def stable_report_fingerprint_valid(value: dict[str, Any]) -> bool:
    expected = value.get("fingerprint")
    body = {key: item for key, item in value.items() if key not in {"fingerprint", "runtime"}}
    return isinstance(expected, str) and expected == digest_json(body)


def display_path(path: Path) -> str:
    resolved = path.expanduser().resolve()
    try:
        return str(resolved.relative_to(ROOT))
    except ValueError:
        pass
    try:
        return "~/" + str(resolved.relative_to(Path.home().resolve()))
    except ValueError:
        return str(resolved)


def resolve_path(value: str) -> Path:
    path = Path(os.path.expandvars(os.path.expanduser(value)))
    return path if path.is_absolute() else ROOT / path


def checked(name: str, observed: Any, expected: Any) -> dict[str, Any]:
    return {"name": name, "observed": observed, "expected": expected, "passed": observed == expected}


def threshold(
    name: str,
    observed: float,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> dict[str, Any]:
    if (minimum is None) == (maximum is None):
        raise ValueError("exactly one threshold direction is required")
    passed = observed >= minimum if minimum is not None else observed <= maximum
    row: dict[str, Any] = {"name": name, "observed": round(float(observed), 6), "passed": bool(passed)}
    row["minimum" if minimum is not None else "maximum"] = minimum if minimum is not None else maximum
    return row


def load_policy(path: Path) -> dict[str, Any]:
    policy = read_json(path)
    if policy.get("schema") != SCHEMA or policy.get("profile") != PROFILE:
        raise RuntimeError("unexpected SepFormer qualification policy")
    return policy


def verify_descriptor(descriptor: dict[str, Any]) -> dict[str, Any]:
    path = resolve_path(str(descriptor["path"]))
    observed = sha256(path) if path.is_file() else None
    row: dict[str, Any] = {
        "path": display_path(path),
        "expected_sha256": descriptor["sha256"],
        "observed_sha256": observed,
        "passed": observed == descriptor["sha256"],
    }
    required = descriptor.get("required_decision")
    if required is not None:
        decision = read_json(path).get("decision") if path.is_file() else None
        row.update({"required_decision": required, "observed_decision": decision})
        row["passed"] = row["passed"] and decision == required
    return row


def artifact(path: Path, root: Path) -> dict[str, Any]:
    return {
        "path": str(path.resolve().relative_to(root.resolve())),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
    }


def read_audio(path: Path) -> np.ndarray:
    values, sample_rate = sf.read(path, dtype="float32", always_2d=False)
    if np.asarray(values).ndim == 2:
        values = np.mean(values, axis=1)
    audio = np.asarray(values, dtype=np.float32).reshape(-1)
    if sample_rate != 16_000:
        divisor = math.gcd(int(sample_rate), 16_000)
        audio = signal.resample_poly(audio, 16_000 // divisor, int(sample_rate) // divisor).astype(np.float32)
    return audio


def write_audio(path: Path, values: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, np.asarray(values, dtype=np.float32), 16_000, subtype="FLOAT")


def rms(values: np.ndarray) -> float:
    audio = np.asarray(values, dtype=np.float64)
    return float(np.sqrt(np.mean(np.square(audio)))) if audio.size else 0.0


def snr_db(reference: np.ndarray, estimate: np.ndarray) -> float:
    expected = np.asarray(reference, dtype=np.float64)
    observed = np.asarray(estimate, dtype=np.float64)
    numerator = float(np.sum(np.square(expected)))
    denominator = float(np.sum(np.square(expected - observed)))
    if numerator <= EPSILON:
        return 0.0
    return float(10.0 * math.log10((numerator + EPSILON) / (denominator + EPSILON)))


def cosine(left: np.ndarray, right: np.ndarray) -> float:
    a = np.asarray(left, dtype=np.float64).reshape(-1)
    b = np.asarray(right, dtype=np.float64).reshape(-1)
    denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(np.dot(a, b) / denominator) if denominator > EPSILON else -1.0


def stats(values: Sequence[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "min": None, "p05": None, "median": None, "p95": None, "max": None}
    array = np.asarray(values, dtype=np.float64)
    return {
        "count": int(array.size),
        "min": round(float(np.min(array)), 6),
        "p05": round(float(np.quantile(array, 0.05)), 6),
        "median": round(float(np.median(array)), 6),
        "p95": round(float(np.quantile(array, 0.95)), 6),
        "max": round(float(np.max(array)), 6),
    }


def split_speakers(policy: dict[str, Any]) -> dict[str, set[str]]:
    return {
        split: set(details["speakers"])
        for split, details in policy["corpus"]["splits"].items()
    }


def model_checks(policy: dict[str, Any]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for model in policy["models"].values():
        root = resolve_path(str(model["model_dir"]))
        for name, expected in sorted(model["files"].items()):
            path = root / name
            observed = sha256(path) if path.is_file() else None
            checks.append(
                {
                    "path": display_path(path),
                    "expected_sha256": expected,
                    "observed_sha256": observed,
                    "passed": observed == expected,
                }
            )
    runtime = resolve_path(policy["models"]["separator"]["runtime_dir"])
    checks.append(checked("separator_runtime_present", runtime.is_dir(), True))
    return checks


def run_freeze(*, policy_path: Path, output_dir: Path) -> dict[str, Any]:
    policy = load_policy(policy_path)
    source_checks = [verify_descriptor(value) for value in policy["sources"].values()]
    models = model_checks(policy)
    speakers = split_speakers(policy)
    overlap = {
        "train_dev": sorted(speakers["train"] & speakers["dev"]),
        "train_future_hard": sorted(speakers["train"] & speakers["future_hard"]),
        "dev_future_hard": sorted(speakers["dev"] & speakers["future_hard"]),
    }
    source_root = resolve_path(policy["corpus"]["public_source_root"])
    availability: dict[str, list[str]] = {}
    for split in ("train", "dev"):
        subset = source_root / policy["corpus"]["splits"][split]["subset"]
        availability[split] = [
            speaker
            for speaker in sorted(speakers[split])
            if not (subset / speaker.removeprefix("slr31_")).is_dir()
        ]
    checks = source_checks + models + [
        checked("split_disjoint", overlap, {key: [] for key in overlap}),
        checked("train_dev_sources_available", availability, {"train": [], "dev": []}),
        checked("future_hard_access", policy["corpus"]["splits"]["future_hard"]["access"], "forbidden"),
        checked("production_publication", policy["production_publication"], "forbidden"),
        checked("direct_asr_access", policy["direct_asr_access"], False),
        checked("post_asr_cleanup_credit", policy["post_asr_cleanup_promotion_credit"], 0),
    ]
    report = with_fingerprint(
        {
            "schema": "murmurmark.sepformer_four_stem_frozen_inputs/v1",
            "profile": PROFILE,
            "policy_path": display_path(policy_path),
            "policy_sha256": sha256(policy_path),
            "passed": all(row["passed"] for row in checks),
            "checks": checks,
            "speaker_overlap": overlap,
            "missing_train_dev_speakers": availability,
            "future_hard_files_read": 0,
            "hard_or_sealed_opened": False,
            "production_changed": False,
        }
    )
    write_json(output_dir / "frozen_inputs.json", report)
    return report


def copy_base_stream(source: Path, destination: Path, stage: Path, kind: str) -> dict[str, Any]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    return {
        "kind": kind,
        "artifact": artifact(destination, stage),
        "source_files": [{"path": display_path(source), "sha256": sha256(source)}],
    }


def public_speaker_plan(policy: dict[str, Any], split: str, speaker_id: str) -> dict[str, Any]:
    subset = policy["corpus"]["splits"][split]["subset"]
    numeric_id = speaker_id.removeprefix("slr31_")
    root = resolve_path(policy["corpus"]["public_source_root"]) / subset / numeric_id
    files = sorted(root.rglob("*.flac"))
    enrollment, mixture, enrollment_sec, mixture_sec = TARGET.reserve_enrollment_files(
        files,
        float(policy["corpus"]["enrollment_min_sec"]),
        int(policy["corpus"]["enrollment_min_files"]),
    )
    required_samples = (
        int(policy["corpus"]["full_rows_per_speaker"]) + 2
    ) * int(policy["corpus"]["clip_samples"])
    selected: list[Path] = []
    selected_samples = 0
    for path in mixture:
        selected.append(path)
        selected_samples += read_audio(path).size
        if selected_samples >= required_samples:
            break
    if enrollment_sec < float(policy["corpus"]["enrollment_min_sec"]) or selected_samples < required_samples:
        raise RuntimeError(f"speaker {speaker_id} has insufficient disjoint material")
    return {
        "speaker_id": speaker_id,
        "split": split,
        "subset": subset,
        "enrollment_files": enrollment,
        "mixture_files": tuple(selected),
        "enrollment_sec": enrollment_sec,
        "mixture_sec": mixture_sec,
        "required_samples": required_samples,
    }


def source_rows(paths: Sequence[Path]) -> list[dict[str, Any]]:
    return [
        {
            "path": display_path(path),
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
            "source_id": path.stem,
        }
        for path in paths
    ]


def concatenate(paths: Sequence[Path], maximum_samples: int | None = None) -> np.ndarray:
    parts: list[np.ndarray] = []
    total = 0
    for path in paths:
        values = read_audio(path)
        if maximum_samples is not None and total + values.size > maximum_samples:
            values = values[: maximum_samples - total]
        if values.size:
            parts.append(values)
            total += values.size
        if maximum_samples is not None and total >= maximum_samples:
            break
    if not parts:
        raise RuntimeError("cannot concatenate an empty source")
    return np.concatenate(parts).astype(np.float32, copy=False)


def stream_descriptor(
    *, stage: Path, path: Path, values: np.ndarray, paths: Sequence[Path], kind: str
) -> dict[str, Any]:
    write_audio(path, values)
    return {
        "kind": kind,
        "artifact": artifact(path, stage),
        "source_files": source_rows(paths),
        "duration_sec": round(values.size / 16_000.0, 6),
    }


def build_streams(
    policy: dict[str, Any], stage: Path
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    base = resolve_path(policy["corpus"]["base_publication"])
    backend = TARGET.load_target_backend(read_json(ROOT / "policies/target-me-identifiability-corpus-v1.json"))
    streams: dict[str, Any] = {}
    speakers: list[dict[str, Any]] = []
    enrollments: list[dict[str, Any]] = []
    for split in ("train", "dev"):
        base_split = base / "source_streams" / split
        target_root = stage / "source_streams" / split / "private_target_me_v1"
        target = copy_base_stream(
            base_split / "private_target_me_v1/mixture_stream.wav",
            target_root / "mixture_stream.wav",
            stage,
            "target_me_mixture",
        )
        opening = copy_base_stream(
            base_split / "private_target_me_v1/opening_backchannel.wav",
            target_root / "opening_backchannel.wav",
            stage,
            "target_me_opening_backchannel",
        )
        target_enrollment_audio = target_root / "enrollment.wav"
        copy_base_stream(
            base_split / "private_target_me_v1/enrollment.wav",
            target_enrollment_audio,
            stage,
            "target_me_enrollment",
        )
        remote = copy_base_stream(
            base_split / "remote_echo.wav",
            stage / "source_streams" / split / "remote_echo.wav",
            stage,
            "measured_remote_echo",
        )
        noise = copy_base_stream(
            base_split / "other_local_noise.wav",
            stage / "source_streams" / split / "other_local_noise.wav",
            stage,
            "measured_local_background",
        )
        target_vector_source = base / "enrollments" / split / "private_target_me_v1.npy"
        target_vector = stage / "enrollments" / split / "private_target_me_v1.npy"
        target_vector.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(target_vector_source, target_vector)
        target_enrollment_id = f"{split}:private_target_me_v1"
        enrollments.append(
            {
                "enrollment_id": target_enrollment_id,
                "split": split,
                "speaker_id": "private_target_me_v1",
                "vector": artifact(target_vector, stage),
                "source": artifact(target_enrollment_audio, stage),
                "backend": "wavlm_xvector_v0",
                "privacy": "private_local_only",
            }
        )
        speakers.append(
            {
                "speaker_id": "private_target_me_v1",
                "split": split,
                "role": "target_me",
                "enrollment_id": target_enrollment_id,
            }
        )
        other: dict[str, Any] = {}
        for speaker_id in policy["corpus"]["splits"][split]["speakers"]:
            plan = public_speaker_plan(policy, split, speaker_id)
            mixture_audio = concatenate(plan["mixture_files"], int(plan["required_samples"]))
            enrollment_audio = concatenate(plan["enrollment_files"], 30 * 16_000)
            public_root = stage / "source_streams" / split / speaker_id
            mixture_stream = stream_descriptor(
                stage=stage,
                path=public_root / "mixture_stream.wav",
                values=mixture_audio,
                paths=plan["mixture_files"],
                kind="other_local_speech",
            )
            enrollment_path = public_root / "enrollment.wav"
            stream_descriptor(
                stage=stage,
                path=enrollment_path,
                values=enrollment_audio,
                paths=plan["enrollment_files"],
                kind="other_local_enrollment",
            )
            vector, embedding_count = TARGET.embedding_centroid(backend, enrollment_audio)
            vector_path = stage / "enrollments" / split / f"{speaker_id}.npy"
            write_npy(vector_path, np.asarray(vector, dtype=np.float64))
            enrollment_id = f"{split}:{speaker_id}"
            enrollments.append(
                {
                    "enrollment_id": enrollment_id,
                    "split": split,
                    "speaker_id": speaker_id,
                    "vector": artifact(vector_path, stage),
                    "source": artifact(enrollment_path, stage),
                    "embedding_count": embedding_count,
                    "backend": "wavlm_xvector_v0",
                    "privacy": "public_cc_by_4_0",
                }
            )
            speakers.append(
                {
                    "speaker_id": speaker_id,
                    "split": split,
                    "role": "other_local",
                    "enrollment_id": enrollment_id,
                    "subset": plan["subset"],
                    "enrollment_files": source_rows(plan["enrollment_files"]),
                    "mixture_files": source_rows(plan["mixture_files"]),
                }
            )
            other[speaker_id] = mixture_stream
        streams[split] = {"target": target, "opening": opening, "remote": remote, "noise": noise, "other": other}
    return streams, speakers, enrollments


def stream_audio(stage: Path, descriptor: dict[str, Any]) -> np.ndarray:
    return read_audio(stage / descriptor["artifact"]["path"])


def valid_offsets(stage: Path, descriptor: dict[str, Any], samples: int) -> list[int]:
    values = stream_audio(stage, descriptor)
    offsets = [
        start
        for start in range(0, max(0, values.size - samples + 1), samples)
        if rms(values[start : start + samples]) >= 5.0e-4
    ]
    if not offsets:
        raise RuntimeError(f"no speech-bearing windows in {descriptor['artifact']['path']}")
    return offsets


def source_slice(stage: Path, descriptor: dict[str, Any], offset: int, samples: int) -> np.ndarray:
    values = stream_audio(stage, descriptor)
    result = values[offset : offset + samples]
    if result.size != samples:
        raise RuntimeError("source slice exceeds stream")
    return np.asarray(result, dtype=np.float32)


FAMILY_COMPONENTS: dict[str, dict[str, bool]] = {
    "ordinary_double_talk": {"target_me": True, "remote_echo": True, "other_local": True, "unexplained_residual": True},
    "quiet_target_me": {"target_me": True, "remote_echo": True, "other_local": True, "unexplained_residual": True},
    "quiet_other_local": {"target_me": True, "remote_echo": True, "other_local": True, "unexplained_residual": True},
    "target_absent_query": {"target_me": False, "remote_echo": True, "other_local": True, "unexplained_residual": True},
    "nearby_speaker": {"target_me": True, "remote_echo": True, "other_local": True, "unexplained_residual": True},
    "opening_backchannel": {"target_me": True, "remote_echo": True, "other_local": True, "unexplained_residual": True},
    "keyboard_background": {"target_me": True, "remote_echo": True, "other_local": True, "unexplained_residual": True},
    "office_noise": {"target_me": True, "remote_echo": True, "other_local": True, "unexplained_residual": True},
    "remote_only": {"target_me": False, "remote_echo": True, "other_local": False, "unexplained_residual": False},
    "target_only": {"target_me": True, "remote_echo": False, "other_local": False, "unexplained_residual": False},
    "other_speaker_only": {"target_me": False, "remote_echo": False, "other_local": True, "unexplained_residual": False},
    "target_remote": {"target_me": True, "remote_echo": True, "other_local": False, "unexplained_residual": False},
    "target_other": {"target_me": True, "remote_echo": False, "other_local": True, "unexplained_residual": False},
}


def gain_for_rms(values: np.ndarray, desired: float) -> float:
    observed = rms(values)
    return float(desired / observed) if observed > EPSILON else 0.0


def make_item(
    *,
    policy: dict[str, Any],
    stage: Path,
    split: str,
    speaker_id: str,
    family: str,
    usage: str,
    index: int,
    streams: dict[str, Any],
    offsets: dict[str, list[int]],
) -> dict[str, Any]:
    samples = int(policy["corpus"]["clip_samples"])
    target_descriptor = streams["opening"] if family == "opening_backchannel" else streams["target"]
    descriptors = {
        "target_me": target_descriptor,
        "remote_echo": streams["remote"],
        "other_local": streams["other"][speaker_id],
        "unexplained_residual": streams["noise"],
    }
    source_audio: dict[str, np.ndarray] = {}
    source_meta: dict[str, Any] = {}
    for position, (name, descriptor) in enumerate(descriptors.items()):
        key = descriptor["artifact"]["path"]
        choices = offsets[key]
        offset = choices[(index + position * 3) % len(choices)]
        source_audio[name] = source_slice(stage, descriptor, offset, samples)
        source_meta[name] = {"stream": descriptor["artifact"], "offset_samples": offset, "samples": samples}
    path_id = ("nearfield_direct_v1", "office_reflective_v1", "offaxis_soft_v1")[index % 3]
    source_audio["other_local"] = TARGET.other_local_path(
        source_audio["other_local"], path_id, int(policy["corpus"]["render_seed"]) + index
    )
    components = dict(FAMILY_COMPONENTS[family])
    active_rms = [rms(source_audio[name]) for name, active in components.items() if active]
    reference = max(active_rms or [1.0e-4])
    target_db = (-6.0, -3.0, 0.0)[index % 3]
    remote_db = (-3.0, 0.0, 3.0)[index % 3]
    other_db = (-6.0, -3.0, 0.0)[index % 3]
    noise_db = (-24.0, -18.0)[index % 2]
    if family == "quiet_target_me":
        target_db = -15.0
    elif family == "quiet_other_local":
        other_db = -15.0
    elif family == "nearby_speaker":
        other_db = 3.0
    elif family == "keyboard_background":
        noise_db = -12.0
    elif family == "office_noise":
        noise_db = -9.0
    gains_db = {
        "target_me": target_db,
        "remote_echo": remote_db,
        "other_local": other_db,
        "unexplained_residual": noise_db,
    }
    gains = {
        name: gain_for_rms(source_audio[name], reference * 10.0 ** (gains_db[name] / 20.0))
        for name in source_audio
    }
    stems = {
        name: source_audio[name] * gains[name] if components[name] else np.zeros(samples, dtype=np.float32)
        for name in source_audio
    }
    preliminary = sum(stems.values())
    peak = float(np.max(np.abs(preliminary))) if preliminary.size else 0.0
    common_scale = min(1.0, float(policy["corpus"]["peak_limit"]) / max(peak, EPSILON))
    stems = {name: np.asarray(values * common_scale, dtype=np.float32) for name, values in stems.items()}
    mixture = np.asarray(sum(stems.values()), dtype=np.float32)
    local_mixture = np.asarray(mixture - stems["remote_echo"], dtype=np.float32)
    identity = {
        "split": split,
        "speaker_id": speaker_id,
        "family": family,
        "usage": usage,
        "index": index,
        "sources": source_meta,
        "gains_db": gains_db,
        "path_id": path_id,
    }
    item_id = digest_json(identity)[:24]
    audio_root = stage / "audio" / split / item_id
    audio_values = {"mixture": mixture, "local_mixture": local_mixture, **stems}
    audio: dict[str, Any] = {}
    for name, values in audio_values.items():
        path = audio_root / f"{name}.wav"
        write_audio(path, values)
        audio[name] = artifact(path, stage)
    reconstruction = stems["target_me"] + stems["remote_echo"] + stems["other_local"] + stems["unexplained_residual"]
    return {
        "schema": "murmurmark.sepformer_four_stem_item/v1",
        "item_id": item_id,
        "split": split,
        "speaker_id": speaker_id,
        "family": family,
        "usage": usage,
        "target_present": components["target_me"],
        "other_local_present": components["other_local"],
        "components": components,
        "sources": source_meta,
        "rendering": {
            "seed": int(policy["corpus"]["render_seed"]) + index,
            "gains_db": gains_db,
            "gains_linear": gains,
            "common_scale": common_scale,
            "other_local_path": path_id,
        },
        "audio": audio,
        "metrics": {
            "finite": all(np.isfinite(values).all() for values in audio_values.values()),
            "peak": round(float(np.max(np.abs(mixture))), 9),
            "reconstruction_max_abs_error": round(float(np.max(np.abs(mixture - reconstruction))), 12),
        },
    }


def corpus_inventory(root: Path) -> list[dict[str, Any]]:
    return [
        {"path": str(path.relative_to(root)), "bytes": path.stat().st_size, "sha256": sha256(path)}
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != "corpus_manifest.json"
    ]


def verify_corpus(output_dir: Path) -> dict[str, Any]:
    corpus = output_dir / "corpus"
    manifest = read_json(corpus / "corpus_manifest.json")
    inventory = corpus_inventory(corpus)
    policy_path = ROOT / "policies/sepformer-four-stem-target-me-qualification-v1.json"
    policy = load_policy(policy_path)
    items = read_jsonl(corpus / "item_manifest.jsonl")
    speakers = read_jsonl(corpus / "speaker_manifest.jsonl")
    source_overlap = 0
    forbidden_source_paths: list[str] = []
    future_ids = set(policy["corpus"]["splits"]["future_hard"]["speakers"])
    for row in speakers:
        enrollment_paths = {item["path"] for item in row.get("enrollment_files", [])}
        mixture_paths = {item["path"] for item in row.get("mixture_files", [])}
        source_overlap += len(enrollment_paths & mixture_paths)
        for path in enrollment_paths | mixture_paths:
            if "dev-clean-2" in path or any(speaker_id in path for speaker_id in future_ids):
                forbidden_source_paths.append(path)
    expected_items = {
        split: len(details["speakers"])
        * (
            int(policy["corpus"]["full_rows_per_speaker"])
            + int(policy["corpus"]["identity_controls_per_speaker"])
        )
        for split, details in policy["corpus"]["splits"].items()
        if split in {"train", "dev"}
    }
    item_counts = Counter(str(row["split"]) for row in items)
    item_speakers = {
        split: sorted({str(row["speaker_id"]) for row in items if row["split"] == split})
        for split in ("train", "dev")
    }
    item_families = {
        split: sorted({str(row["family"]) for row in items if row["split"] == split})
        for split in ("train", "dev")
    }
    required_families = sorted(set(policy["corpus"]["families"] + policy["corpus"]["identity_controls"]))
    reconstruction_max = max(float(row["metrics"]["reconstruction_max_abs_error"]) for row in items)
    peak_max = max(float(row["metrics"]["peak"]) for row in items)
    non_finite = sum(not bool(row["metrics"]["finite"]) for row in items)
    checks = [
        checked("manifest_fingerprint", fingerprint_valid(manifest), True),
        checked("inventory_fingerprint", digest_json(inventory), manifest.get("inventory_fingerprint")),
        checked("policy_sha256", manifest.get("policy_sha256"), sha256(policy_path)),
        checked("item_counts", dict(item_counts), expected_items),
        checked(
            "split_speakers",
            item_speakers,
            {split: sorted(policy["corpus"]["splits"][split]["speakers"]) for split in ("train", "dev")},
        ),
        checked("family_coverage", item_families, {"train": required_families, "dev": required_families}),
        threshold("reconstruction_max_abs_error", reconstruction_max, maximum=1.0e-5),
        threshold("peak_max", peak_max, maximum=float(policy["corpus"]["peak_limit"]) + 1.0e-6),
        checked("non_finite_items", non_finite, 0),
        checked("enrollment_mixture_file_overlap", source_overlap, 0),
        checked("forbidden_source_paths", sorted(set(forbidden_source_paths)), []),
        checked("future_hard_files_read", manifest.get("future_hard_files_read"), 0),
        checked("hard_or_sealed_opened", manifest.get("hard_or_sealed_opened"), False),
    ]
    report = with_fingerprint(
        {
            "schema": "murmurmark.sepformer_four_stem_corpus_verification/v1",
            "profile": PROFILE,
            "passed": all(row["passed"] for row in checks),
            "checks": checks,
            "corpus_fingerprint": manifest.get("fingerprint"),
        }
    )
    write_json(output_dir / "corpus_verification.json", report)
    return {"passed": report["passed"], "checks": checks, "manifest": manifest, "report": report}


def run_materialize(*, policy_path: Path, output_dir: Path) -> dict[str, Any]:
    frozen = read_json(output_dir / "frozen_inputs.json")
    if not frozen.get("passed") or frozen.get("policy_sha256") != sha256(policy_path):
        raise RuntimeError("materialization requires passing frozen inputs")
    existing = output_dir / "corpus/corpus_manifest.json"
    if existing.is_file():
        verification = verify_corpus(output_dir)
        if not verification["passed"]:
            raise RuntimeError("existing materialized corpus changed")
        return verification["manifest"]
    policy = load_policy(policy_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".corpus-stage-", dir=output_dir))
    try:
        streams, speakers, enrollments = build_streams(policy, stage)
        offsets: dict[str, list[int]] = {}
        for split_streams in streams.values():
            for descriptor in [
                split_streams["target"], split_streams["opening"], split_streams["remote"], split_streams["noise"],
                *split_streams["other"].values(),
            ]:
                key = descriptor["artifact"]["path"]
                offsets[key] = valid_offsets(stage, descriptor, int(policy["corpus"]["clip_samples"]))
        items: list[dict[str, Any]] = []
        split_offset = {"train": 0, "dev": 1_000_000}
        for split in ("train", "dev"):
            families = list(policy["corpus"]["families"])
            controls = list(policy["corpus"]["identity_controls"])
            for speaker_index, speaker_id in enumerate(policy["corpus"]["splits"][split]["speakers"]):
                for family_index, family in enumerate(families):
                    index = split_offset[split] + speaker_index * 100 + family_index
                    items.append(
                        make_item(
                            policy=policy,
                            stage=stage,
                            split=split,
                            speaker_id=speaker_id,
                            family=family,
                            usage="full_family",
                            index=index,
                            streams=streams[split],
                            offsets=offsets,
                        )
                    )
                for control_index, family in enumerate(controls):
                    index = split_offset[split] + speaker_index * 100 + 50 + control_index
                    items.append(
                        make_item(
                            policy=policy,
                            stage=stage,
                            split=split,
                            speaker_id=speaker_id,
                            family=family,
                            usage="identity_control",
                            index=index,
                            streams=streams[split],
                            offsets=offsets,
                        )
                    )
        items.sort(key=lambda row: (row["split"], row["item_id"]))
        write_jsonl(stage / "item_manifest.jsonl", items)
        write_jsonl(stage / "speaker_manifest.jsonl", speakers)
        write_jsonl(stage / "enrollment_manifest.jsonl", enrollments)
        split_summary: dict[str, Any] = {}
        for split in ("train", "dev"):
            selected = [row for row in items if row["split"] == split]
            split_summary[split] = {
                "items": len(selected),
                "speakers": sorted({row["speaker_id"] for row in selected}),
                "families": sorted({row["family"] for row in selected}),
                "duration_sec": round(len(selected) * float(policy["corpus"]["clip_duration_sec"]), 3),
            }
        inventory = corpus_inventory(stage)
        manifest = with_fingerprint(
            {
                "schema": "murmurmark.sepformer_four_stem_corpus/v1",
                "profile": PROFILE,
                "policy_sha256": sha256(policy_path),
                "frozen_inputs_fingerprint": frozen["fingerprint"],
                "splits": split_summary,
                "items": len(items),
                "enrollments": len(enrollments),
                "inventory_fingerprint": digest_json(inventory),
                "inventory": inventory,
                "source_file_fingerprint": digest_json(
                    sorted(
                        (file["path"], file["sha256"])
                        for row in speakers
                        for key in ("enrollment_files", "mixture_files")
                        for file in row.get(key, [])
                    )
                ),
                "future_hard_files_read": 0,
                "hard_or_sealed_opened": False,
                "ordinary_meetings_read": 0,
                "production_changed": False,
            }
        )
        write_json(stage / "corpus_manifest.json", manifest)
        os.replace(stage, output_dir / "corpus")
        return manifest
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def block_network() -> list[str]:
    attempts: list[str] = []
    original_socket = socket.socket

    class OfflineSocket(original_socket):
        def connect(self, address: Any) -> Any:
            attempts.append(str(address))
            raise OSError("network disabled by MurmurMark qualification")

        def connect_ex(self, address: Any) -> int:
            attempts.append(str(address))
            return 101

    socket.socket = OfflineSocket
    socket.create_connection = lambda address, *args, **kwargs: (_ for _ in ()).throw(
        OSError(f"network disabled by MurmurMark qualification: {address}")
    )
    return attempts


def load_separator(policy: dict[str, Any]) -> Any:
    runtime = resolve_path(policy["models"]["separator"]["runtime_dir"])
    sys.path.insert(0, str(runtime))
    os.environ.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "TOKENIZERS_PARALLELISM": "false"})
    import torch
    from speechbrain.inference.separation import SepformerSeparation

    torch.set_num_threads(4)
    torch.use_deterministic_algorithms(True)
    model_dir = resolve_path(policy["models"]["separator"]["model_dir"])
    return SepformerSeparation.from_hparams(source=str(model_dir), savedir=str(model_dir), run_opts={"device": "cpu"})


def recover_stem_scale(raw_stems: np.ndarray, local_mixture: np.ndarray) -> tuple[np.ndarray, list[float]]:
    up = np.asarray(raw_stems, dtype=np.float64)
    mixture = np.asarray(local_mixture, dtype=np.float64).reshape(-1)
    if up.shape != (2, mixture.size):
        raise ValueError("scale recovery requires two sample-aligned stems")
    coefficients, *_ = np.linalg.lstsq(up.T, mixture, rcond=None)
    scaled = coefficients[:, None] * up
    if not np.isfinite(scaled).all():
        raise RuntimeError("non-finite SepFormer output")
    return np.asarray(scaled, dtype=np.float32), [float(value) for value in coefficients]


def separate_item(model: Any, local_mixture: np.ndarray) -> tuple[np.ndarray, list[float]]:
    import torch

    down = signal.resample_poly(local_mixture, 1, 2).astype(np.float32)
    with torch.inference_mode():
        output = model.separate_batch(torch.from_numpy(down)[None])
    raw = output.detach().cpu().numpy()[0].T.astype(np.float64)
    up = np.stack([signal.resample_poly(stem, 2, 1) for stem in raw])
    samples = local_mixture.size
    if up.shape[1] < samples:
        up = np.pad(up, ((0, 0), (0, samples - up.shape[1])))
    up = up[:, :samples]
    return recover_stem_scale(up, local_mixture)


def load_item_audio(corpus: Path, row: dict[str, Any], name: str) -> np.ndarray:
    path = corpus / row["audio"][name]["path"]
    if sha256(path) != row["audio"][name]["sha256"]:
        raise RuntimeError(f"changed item audio: {path}")
    return read_audio(path)


def valid_cache_item(
    *, path: Path, meta_path: Path, item_id: str, split: str, samples: int
) -> tuple[bool, dict[str, Any] | None]:
    if not path.is_file() or not meta_path.is_file():
        return False, None
    try:
        meta = read_json(meta_path)
        values = np.load(path, mmap_mode="r")
        runtime = meta.get("runtime") or {}
        inference_sec = runtime.get("inference_sec")
        valid = bool(
            stable_report_fingerprint_valid(meta)
            and meta.get("item_id") == item_id
            and meta.get("split") == split
            and meta.get("sha256") == sha256(path)
            and list(values.shape) == [2, samples]
            and np.isfinite(values).all()
            and isinstance(inference_sec, (int, float))
            and math.isfinite(float(inference_sec))
            and float(inference_sec) >= 0.0
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False, None
    return valid, meta if valid else None


def cache_split(
    *,
    policy: dict[str, Any],
    policy_sha256: str,
    output_dir: Path,
    split: str,
    rows: list[dict[str, Any]],
) -> tuple[dict[str, Any], float, list[str]]:
    cache_root = output_dir / split / "separator-cache"
    cache_root.mkdir(parents=True, exist_ok=True)
    corpus = output_dir / "corpus"
    samples = int(policy["corpus"]["clip_samples"])
    pending = []
    for row in rows:
        item_id = row["item_id"]
        valid, _ = valid_cache_item(
            path=cache_root / f"{item_id}.npy",
            meta_path=cache_root / f"{item_id}.json",
            item_id=item_id,
            split=split,
            samples=samples,
        )
        if not valid:
            pending.append(row)
    attempts = block_network()
    if pending:
        model = load_separator(policy)
        for index, row in enumerate(pending, 1):
            local = load_item_audio(corpus, row, "local_mixture")
            item_started = time.monotonic()
            stems, coefficients = separate_item(model, local)
            inference_sec = time.monotonic() - item_started
            path = cache_root / f"{row['item_id']}.npy"
            write_npy(path, stems)
            stable = {
                "schema": "murmurmark.sepformer_four_stem_cache_item/v1",
                "item_id": row["item_id"],
                "split": split,
                "shape": list(stems.shape),
                "sha256": sha256(path),
                "coefficients": [round(value, 9) for value in coefficients],
                "finite": bool(np.isfinite(stems).all()),
            }
            meta = with_fingerprint(stable)
            meta["runtime"] = {"inference_sec": round(inference_sec, 6)}
            meta["fingerprint"] = digest_json(stable)
            write_json(cache_root / f"{row['item_id']}.json", meta)
            if index == 1 or index % 10 == 0 or index == len(pending):
                print(f"[{split}] SepFormer {index}/{len(pending)} new items", flush=True)
    entries: list[dict[str, Any]] = []
    total_inference_sec = 0.0
    for row in rows:
        path = cache_root / f"{row['item_id']}.npy"
        meta_path = cache_root / f"{row['item_id']}.json"
        valid, meta = valid_cache_item(
            path=path,
            meta_path=meta_path,
            item_id=row["item_id"],
            split=split,
            samples=samples,
        )
        if not valid or meta is None:
            raise RuntimeError(f"invalid separator cache item: {row['item_id']}")
        total_inference_sec += float(meta["runtime"]["inference_sec"])
        entries.append({"item_id": row["item_id"], "path": display_path(path), "sha256": meta["sha256"]})
    manifest = with_fingerprint(
        {
            "schema": "murmurmark.sepformer_four_stem_cache/v1",
            "split": split,
            "policy_sha256": policy_sha256,
            "items": entries,
            "items_fingerprint": digest_json(entries),
            "network_attempts": attempts,
        }
    )
    write_json(output_dir / split / "separator_cache_manifest.json", manifest)
    return manifest, total_inference_sec, attempts


def enrollment_vectors(corpus: Path) -> dict[tuple[str, str], np.ndarray]:
    result: dict[tuple[str, str], np.ndarray] = {}
    for row in read_jsonl(corpus / "enrollment_manifest.jsonl"):
        path = corpus / row["vector"]["path"]
        if sha256(path) != row["vector"]["sha256"]:
            raise RuntimeError(f"changed enrollment: {path}")
        vector = np.asarray(np.load(path), dtype=np.float64).reshape(-1)
        vector /= max(float(np.linalg.norm(vector)), EPSILON)
        result[(str(row["split"]), str(row["speaker_id"]))] = vector
    return result


def embed_split(
    *, policy: dict[str, Any], output_dir: Path, split: str, rows: list[dict[str, Any]]
) -> dict[str, Any]:
    path = output_dir / split / "stem_embeddings.npy"
    valid_path = output_dir / split / "stem_embeddings_valid.npy"
    manifest_path = output_dir / split / "stem_embeddings_manifest.json"
    if path.is_file() and valid_path.is_file() and manifest_path.is_file():
        manifest = read_json(manifest_path)
        if (
            fingerprint_valid(manifest)
            and manifest.get("embeddings_sha256") == sha256(path)
            and manifest.get("valid_sha256") == sha256(valid_path)
        ):
            return manifest
        raise RuntimeError(f"changed {split} stem embeddings")
    backend = TARGET.load_target_backend(read_json(ROOT / "policies/target-me-identifiability-corpus-v1.json"))
    cache_root = output_dir / split / "separator-cache"
    embeddings: list[np.ndarray] = []
    valid: list[bool] = []
    for offset in range(0, len(rows), 4):
        batch_rows = rows[offset : offset + 4]
        audio_rows = [
            np.asarray(np.load(cache_root / f"{row['item_id']}.npy"), dtype=np.float32)[stem]
            for row in batch_rows
            for stem in (0, 1)
        ]
        vectors = backend.embed_audio_batch(audio_rows, batch_size=4)
        for vector in vectors:
            if vector is None:
                embeddings.append(np.zeros(512, dtype=np.float64))
                valid.append(False)
            else:
                embeddings.append(np.asarray(vector, dtype=np.float64))
                valid.append(True)
        print(f"[{split}] WavLM {min(offset + 4, len(rows))}/{len(rows)}", flush=True)
    matrix = np.asarray(embeddings, dtype=np.float64).reshape(len(rows), 2, -1)
    validity = np.asarray(valid, dtype=np.bool_).reshape(len(rows), 2)
    write_npy(path, matrix)
    write_npy(valid_path, validity)
    manifest = with_fingerprint(
        {
            "schema": "murmurmark.sepformer_four_stem_embeddings/v1",
            "split": split,
            "items": [row["item_id"] for row in rows],
            "shape": list(matrix.shape),
            "valid_count": int(np.count_nonzero(validity)),
            "embeddings_sha256": sha256(path),
            "valid_sha256": sha256(valid_path),
            "model_files": policy["models"]["speaker_encoder"]["files"],
        }
    )
    write_json(manifest_path, manifest)
    return manifest


def evaluate_row(
    *, corpus: Path, row: dict[str, Any], stems: np.ndarray, embeddings: np.ndarray, valid: np.ndarray,
    enrollments: dict[tuple[str, str], np.ndarray], thresholds: dict[str, float] | None,
) -> dict[str, Any]:
    split = str(row["split"])
    target_query = enrollments[(split, "private_target_me_v1")]
    other_query = enrollments[(split, str(row["speaker_id"]))]
    similarities = {
        "target": [cosine(embeddings[index], target_query) if valid[index] else -1.0 for index in (0, 1)],
        "other": [cosine(embeddings[index], other_query) if valid[index] else -1.0 for index in (0, 1)],
    }
    orientation = [
        similarities["target"][0] + similarities["other"][1],
        similarities["target"][1] + similarities["other"][0],
    ]
    target_index = 0 if orientation[0] >= orientation[1] else 1
    other_index = 1 - target_index
    target_argmax = int(np.argmax(similarities["target"]))
    other_argmax = int(np.argmax(similarities["other"]))
    pair_margin = abs(orientation[0] - orientation[1])
    presence_margin = max(similarities["target"]) - max(similarities["other"])
    target_estimate = np.asarray(stems[target_index], dtype=np.float64)
    other_estimate = np.asarray(stems[other_index], dtype=np.float64)
    mixture = load_item_audio(corpus, row, "mixture").astype(np.float64)
    local = load_item_audio(corpus, row, "local_mixture").astype(np.float64)
    remote = load_item_audio(corpus, row, "remote_echo").astype(np.float64)
    target = load_item_audio(corpus, row, "target_me").astype(np.float64)
    other = load_item_audio(corpus, row, "other_local").astype(np.float64)
    residual_truth = load_item_audio(corpus, row, "unexplained_residual").astype(np.float64)
    residual = local - target_estimate - other_estimate
    reconstruction = target_estimate + remote + other_estimate + residual
    alternate_target_snr = snr_db(target, other_estimate) if row["target_present"] else None
    target_snr = snr_db(target, target_estimate) if row["target_present"] else None
    oracle_index = None
    assignment_correct = None
    if row["target_present"]:
        stem_snrs = [snr_db(target, np.asarray(stems[index], dtype=np.float64)) for index in (0, 1)]
        oracle_index = int(np.argmax(stem_snrs))
        assignment_correct = target_index == oracle_index
    evidence_valid = bool(valid[0] and valid[1] and target_argmax != other_argmax)
    selected = False
    if thresholds is not None:
        selected = bool(
            evidence_valid
            and pair_margin >= thresholds["paired_cosine_margin"]
            and presence_margin >= thresholds["target_presence_margin"]
        )
    published = target_estimate.astype(np.float32) if selected else mixture.astype(np.float32, copy=True)
    fallback_exact = selected or np.array_equal(published, mixture.astype(np.float32))
    absent_attenuation = None
    if not row["target_present"]:
        absent_attenuation = 10.0 * math.log10(
            (float(np.sum(np.square(local))) + EPSILON)
            / (float(np.sum(np.square(target_estimate))) + EPSILON)
        )
    return {
        "schema": "murmurmark.sepformer_four_stem_evaluation_row/v1",
        "item_id": row["item_id"],
        "split": split,
        "speaker_id": row["speaker_id"],
        "family": row["family"],
        "usage": row["usage"],
        "target_present": row["target_present"],
        "other_local_present": row["other_local_present"],
        "similarities": {key: [round(value, 9) for value in values] for key, values in similarities.items()},
        "paired_cosine_margin": round(pair_margin, 9),
        "target_presence_margin": round(presence_margin, 9),
        "target_stem_index": target_index,
        "other_stem_index": other_index,
        "target_query_argmax": target_argmax,
        "other_query_argmax": other_argmax,
        "query_collapsed": target_argmax == other_argmax,
        "oracle_target_stem_index": oracle_index,
        "assignment_correct": assignment_correct,
        "selected": selected,
        "metrics": {
            "target_snr_db": round(target_snr, 6) if target_snr is not None else None,
            "target_baseline_snr_db": round(snr_db(target, local), 6) if row["target_present"] else None,
            "target_snr_improvement_db": round(target_snr - snr_db(target, local), 6) if target_snr is not None else None,
            "paired_query_margin_db": round(target_snr - alternate_target_snr, 6) if target_snr is not None else None,
            "other_local_snr_db": round(snr_db(other, other_estimate), 6) if row["other_local_present"] else None,
            "unexplained_residual_snr_db": round(snr_db(residual_truth, residual), 6) if rms(residual_truth) > EPSILON else None,
            "absent_query_attenuation_db": round(absent_attenuation, 6) if absent_attenuation is not None else None,
            "reconstruction_max_abs_error": round(float(np.max(np.abs(mixture - reconstruction))), 12),
            "candidate_peak": round(float(max(np.max(np.abs(target_estimate)), np.max(np.abs(other_estimate)), np.max(np.abs(residual)))), 9),
            "finite": bool(np.isfinite(reconstruction).all()),
            "fallback_exact": bool(fallback_exact),
        },
    }


def aggregate_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    metric_names = (
        "target_snr_db", "target_baseline_snr_db", "target_snr_improvement_db", "paired_query_margin_db",
        "other_local_snr_db", "unexplained_residual_snr_db", "absent_query_attenuation_db",
        "reconstruction_max_abs_error", "candidate_peak",
    )
    metrics = {
        name: stats([float(row["metrics"][name]) for row in rows if row["metrics"].get(name) is not None])
        for name in metric_names
    }
    families: dict[str, Any] = {}
    for family in sorted({row["family"] for row in rows}):
        selected = [row for row in rows if row["family"] == family]
        families[family] = {
            "items": len(selected),
            "target_snr_db": stats([float(row["metrics"]["target_snr_db"]) for row in selected if row["metrics"].get("target_snr_db") is not None]),
            "selected": sum(bool(row["selected"]) for row in selected),
        }
    assignable = [row for row in rows if row["target_present"] and row["other_local_present"]]
    return {
        "items": len(rows),
        "metrics": metrics,
        "families": families,
        "wavlm_assignment_accuracy": round(
            sum(row["assignment_correct"] is True for row in assignable) / max(1, len(assignable)), 9
        ),
        "query_collapse_rate": round(sum(row["query_collapsed"] for row in assignable) / max(1, len(assignable)), 9),
        "selected_items": sum(bool(row["selected"]) for row in rows),
        "selected_rate": round(sum(bool(row["selected"]) for row in rows) / max(1, len(rows)), 9),
        "clipped_outputs": sum(float(row["metrics"]["candidate_peak"]) > 1.0 for row in rows),
        "non_finite_outputs": sum(not bool(row["metrics"]["finite"]) for row in rows),
        "exact_fallback_failures": sum(not bool(row["metrics"]["fallback_exact"]) for row in rows),
    }


def evaluate_split(
    *,
    policy: dict[str, Any],
    policy_sha256: str,
    output_dir: Path,
    split: str,
    thresholds: dict[str, float] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any], float, list[str]]:
    corpus = output_dir / "corpus"
    rows = [row for row in read_jsonl(corpus / "item_manifest.jsonl") if row["split"] == split]
    cache_manifest, inference_sec, attempts = cache_split(
        policy=policy,
        policy_sha256=policy_sha256,
        output_dir=output_dir,
        split=split,
        rows=rows,
    )
    embedding_manifest = embed_split(policy=policy, output_dir=output_dir, split=split, rows=rows)
    embedding_values = np.asarray(np.load(output_dir / split / "stem_embeddings.npy"), dtype=np.float64)
    valid_values = np.asarray(np.load(output_dir / split / "stem_embeddings_valid.npy"), dtype=np.bool_)
    vectors = enrollment_vectors(corpus)
    evaluated = [
        evaluate_row(
            corpus=corpus,
            row=row,
            stems=np.asarray(np.load(output_dir / split / "separator-cache" / f"{row['item_id']}.npy"), dtype=np.float32),
            embeddings=embedding_values[index],
            valid=valid_values[index],
            enrollments=vectors,
            thresholds=thresholds,
        )
        for index, row in enumerate(rows)
    ]
    aggregate = aggregate_rows(evaluated)
    aggregate["cache_fingerprint"] = cache_manifest["fingerprint"]
    aggregate["embedding_fingerprint"] = embedding_manifest["fingerprint"]
    return evaluated, aggregate, inference_sec, attempts


def calibrate_thresholds(policy: dict[str, Any], rows: list[dict[str, Any]]) -> tuple[dict[str, float], dict[str, Any]]:
    paired = [row for row in rows if row["target_present"] and row["other_local_present"]]
    correct_margins = [float(row["paired_cosine_margin"]) for row in paired if row["assignment_correct"] is True]
    positives = [float(row["target_presence_margin"]) for row in rows if row["target_present"]]
    negatives = [float(row["target_presence_margin"]) for row in rows if not row["target_present"]]
    contract = policy["calibration"]
    paired_threshold = max(
        float(contract["minimum_paired_cosine_margin"]),
        float(np.quantile(correct_margins, float(contract["paired_margin_quantile"]))) if correct_margins else 1.0,
    )
    positive_floor = float(np.quantile(positives, float(contract["target_presence_positive_quantile"]))) if positives else -1.0
    negative_ceiling = float(np.quantile(negatives, float(contract["target_presence_negative_quantile"]))) if negatives else 1.0
    presence_threshold = max(
        float(contract["minimum_target_presence_margin"]),
        (positive_floor + negative_ceiling) / 2.0,
    )
    thresholds = {
        "paired_cosine_margin": round(paired_threshold, 9),
        "target_presence_margin": round(presence_threshold, 9),
    }
    assignment_error = 1.0 - sum(row["assignment_correct"] is True for row in paired) / max(1, len(paired))
    collapse = sum(row["query_collapsed"] for row in paired) / max(1, len(paired))
    false_accept = sum(value >= presence_threshold for value in negatives) / max(1, len(negatives))
    false_reject = sum(value < presence_threshold for value in positives) / max(1, len(positives))
    evidence = {
        "paired_rows": len(paired),
        "correct_paired_rows": len(correct_margins),
        "positive_presence_rows": len(positives),
        "negative_presence_rows": len(negatives),
        "positive_presence_floor": round(positive_floor, 9),
        "negative_presence_ceiling": round(negative_ceiling, 9),
        "assignment_error_rate": round(assignment_error, 9),
        "query_collapse_rate": round(collapse, 9),
        "presence_false_accept_rate": round(false_accept, 9),
        "presence_false_reject_rate": round(false_reject, 9),
        "paired_margin": stats([float(row["paired_cosine_margin"]) for row in paired]),
        "target_presence_positive": stats(positives),
        "target_presence_negative": stats(negatives),
    }
    return thresholds, evidence


def peak_rss_mb() -> float:
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return float(raw / (1024.0 * 1024.0 if sys.platform == "darwin" else 1024.0))


def verify_immutable_report(path: Path, expected_policy_sha: str) -> dict[str, Any]:
    report = read_json(path)
    if not stable_report_fingerprint_valid(report) or report.get("policy_sha256") != expected_policy_sha:
        raise RuntimeError(f"immutable report changed: {path}")
    return report


def run_calibrate_train(*, policy_path: Path, output_dir: Path) -> dict[str, Any]:
    existing = output_dir / "train/calibration_lock.json"
    if existing.is_file():
        return verify_immutable_report(existing, sha256(policy_path))
    policy = load_policy(policy_path)
    verification = verify_corpus(output_dir)
    if not verification["passed"]:
        raise RuntimeError("train calibration requires an intact corpus")
    started = time.monotonic()
    resource_report = apply_resource_policy(resolve_resource_policy("background", 4))
    rows, aggregate, inference_sec, attempts = evaluate_split(
        policy=policy,
        policy_sha256=sha256(policy_path),
        output_dir=output_dir,
        split="train",
        thresholds=None,
    )
    thresholds, evidence = calibrate_thresholds(policy, rows)
    calibrated_rows = []
    for row in rows:
        copied = dict(row)
        copied["selected"] = bool(
            not row["query_collapsed"]
            and row["paired_cosine_margin"] >= thresholds["paired_cosine_margin"]
            and row["target_presence_margin"] >= thresholds["target_presence_margin"]
        )
        calibrated_rows.append(copied)
    aggregate = aggregate_rows(calibrated_rows)
    quality_checks = [
        threshold(
            "assignment_error_rate",
            evidence["assignment_error_rate"],
            maximum=float(policy["calibration"]["maximum_train_assignment_error_rate"]),
        ),
        threshold(
            "query_collapse_rate",
            evidence["query_collapse_rate"],
            maximum=float(policy["calibration"]["maximum_train_query_collapse_rate"]),
        ),
        threshold("presence_margin_separation", evidence["positive_presence_floor"] - evidence["negative_presence_ceiling"], minimum=0.0),
    ]
    resource_checks = [
        threshold("network_attempts", len(attempts), maximum=0),
        threshold("nice", resource_report.get("nice_after") or 0, minimum=float(policy["gates"]["resource"]["nice_min"])),
        threshold("compute_threads", resource_report["max_compute_threads"], maximum=float(policy["gates"]["resource"]["torch_threads_max"])),
        threshold("peak_rss_mb", peak_rss_mb(), maximum=float(policy["gates"]["resource"]["peak_rss_mb_max"])),
        threshold("train_inference_sec", inference_sec, maximum=float(policy["gates"]["resource"]["train_inference_sec_max"])),
    ]
    checks = quality_checks + resource_checks
    if not all(row["passed"] for row in resource_checks):
        decision = TRAIN_RESOURCE_LIMIT
    else:
        decision = TRAIN_LOCKED if all(row["passed"] for row in quality_checks) else TRAIN_REJECTED
    write_jsonl(output_dir / "train/train_rows.jsonl", calibrated_rows)
    stable = {
        "schema": "murmurmark.sepformer_four_stem_train_calibration/v1",
        "profile": PROFILE,
        "decision": decision,
        "policy_sha256": sha256(policy_path),
        "corpus_fingerprint": verification["manifest"]["fingerprint"],
        "thresholds": thresholds,
        "calibration_evidence": evidence,
        "aggregate": aggregate,
        "checks": [{key: value for key, value in row.items()} for row in checks if row["name"] not in {"train_inference_sec", "peak_rss_mb"}],
        "blockers": [row["name"] for row in checks if not row["passed"]],
        "separator_cache_sha256": sha256(output_dir / "train/separator_cache_manifest.json"),
        "stem_embeddings_sha256": sha256(output_dir / "train/stem_embeddings_manifest.json"),
        "train_rows_sha256": sha256(output_dir / "train/train_rows.jsonl"),
        "future_hard_files_read": 0,
        "hard_or_sealed_opened": False,
        "direct_asr_opened": False,
        "production_changed": False,
    }
    report = with_fingerprint(stable)
    report["runtime"] = {
        "wall_sec": round(time.monotonic() - started, 3),
        "separator_inference_sec": round(inference_sec, 3),
        "peak_rss_mb": round(peak_rss_mb(), 3),
        "resource_policy": resource_report,
        "checks": [row for row in resource_checks if row["name"] in {"train_inference_sec", "peak_rss_mb"}],
    }
    report["fingerprint"] = digest_json(stable)
    write_json(existing, report)
    return report


def dev_checks(policy: dict[str, Any], aggregate: dict[str, Any], inference_sec: float) -> list[dict[str, Any]]:
    gates = policy["gates"]["dev"]
    metrics = aggregate["metrics"]
    families = aggregate["families"]
    return [
        threshold("target_me_snr_db_median", metrics["target_snr_db"]["median"] or -100.0, minimum=float(gates["target_me_snr_db_median_min"])),
        threshold("target_me_improvement_db_median", metrics["target_snr_improvement_db"]["median"] or -100.0, minimum=float(gates["target_me_improvement_db_median_min"])),
        threshold("other_local_snr_db_median", metrics["other_local_snr_db"]["median"] or -100.0, minimum=float(gates["other_local_snr_db_median_min"])),
        threshold("unexplained_residual_snr_db_median", metrics["unexplained_residual_snr_db"]["median"] or -100.0, minimum=float(gates["unexplained_residual_snr_db_median_min"])),
        threshold("paired_query_margin_db_median", metrics["paired_query_margin_db"]["median"] or -100.0, minimum=float(gates["paired_query_margin_db_median_min"])),
        threshold("wavlm_assignment_accuracy", aggregate["wavlm_assignment_accuracy"], minimum=float(gates["wavlm_assignment_accuracy_min"])),
        threshold("query_collapse_rate", aggregate["query_collapse_rate"], maximum=float(gates["query_collapse_rate_max"])),
        threshold("absent_query_attenuation_db_median", metrics["absent_query_attenuation_db"]["median"] or -100.0, minimum=float(gates["absent_query_attenuation_db_median_min"])),
        threshold("quiet_target_me_snr_db_median", families["quiet_target_me"]["target_snr_db"]["median"] or -100.0, minimum=float(gates["quiet_target_me_snr_db_median_min"])),
        threshold("opening_backchannel_snr_db_median", families["opening_backchannel"]["target_snr_db"]["median"] or -100.0, minimum=float(gates["opening_backchannel_snr_db_median_min"])),
        threshold("ordinary_double_talk_snr_db_median", families["ordinary_double_talk"]["target_snr_db"]["median"] or -100.0, minimum=float(gates["ordinary_double_talk_snr_db_median_min"])),
        threshold("reconstruction_max_abs_error", metrics["reconstruction_max_abs_error"]["max"] or 0.0, maximum=float(gates["reconstruction_max_abs_error_max"])),
        threshold("clipped_outputs", aggregate["clipped_outputs"], maximum=float(gates["clipped_outputs_max"])),
        threshold("non_finite_outputs", aggregate["non_finite_outputs"], maximum=float(gates["non_finite_outputs_max"])),
        threshold("exact_fallback_failures", aggregate["exact_fallback_failures"], maximum=float(gates["exact_fallback_failures_max"])),
        threshold("dev_inference_sec", inference_sec, maximum=float(gates["dev_inference_sec_max"])),
        checked("family_coverage", sorted(families), sorted(policy["corpus"]["families"] + policy["corpus"]["identity_controls"])),
    ]


def run_evaluate_dev(*, policy_path: Path, output_dir: Path) -> dict[str, Any]:
    existing = output_dir / "dev/dev_report.json"
    if existing.is_file():
        return verify_immutable_report(existing, sha256(policy_path))
    policy = load_policy(policy_path)
    calibration = verify_immutable_report(output_dir / "train/calibration_lock.json", sha256(policy_path))
    if calibration.get("decision") != TRAIN_LOCKED:
        raise RuntimeError("dev access denied by rejected train calibration")
    access = with_fingerprint(
        {
            "schema": "murmurmark.sepformer_four_stem_dev_access/v1",
            "policy_sha256": sha256(policy_path),
            "calibration_fingerprint": calibration["fingerprint"],
            "corpus_fingerprint": calibration["corpus_fingerprint"],
            "future_hard_access": False,
            "sealed_access": False,
            "direct_asr_access": False,
        }
    )
    write_json(output_dir / "dev/access.json", access)
    started = time.monotonic()
    resource_report = apply_resource_policy(resolve_resource_policy("background", 4))
    rows, aggregate, inference_sec, attempts = evaluate_split(
        policy=policy,
        policy_sha256=sha256(policy_path),
        output_dir=output_dir,
        split="dev",
        thresholds={key: float(value) for key, value in calibration["thresholds"].items()},
    )
    quality_checks = dev_checks(policy, aggregate, inference_sec)
    resource_checks = [
        next(row for row in quality_checks if row["name"] == "dev_inference_sec"),
        threshold("network_attempts", len(attempts), maximum=0),
        threshold("nice", resource_report.get("nice_after") or 0, minimum=float(policy["gates"]["resource"]["nice_min"])),
        threshold("compute_threads", resource_report["max_compute_threads"], maximum=float(policy["gates"]["resource"]["torch_threads_max"])),
        threshold("peak_rss_mb", peak_rss_mb(), maximum=float(policy["gates"]["resource"]["peak_rss_mb_max"])),
    ]
    quality_checks = [row for row in quality_checks if row["name"] != "dev_inference_sec"]
    checks = quality_checks + resource_checks
    if not all(row["passed"] for row in resource_checks):
        decision = DEV_RESOURCE_LIMIT
    else:
        decision = DEV_LOCKED if all(row["passed"] for row in quality_checks) else DEV_REJECTED
    write_jsonl(output_dir / "dev/dev_rows.jsonl", rows)
    stable_checks = [row for row in checks if row["name"] not in {"dev_inference_sec", "peak_rss_mb"}]
    stable = {
        "schema": "murmurmark.sepformer_four_stem_dev/v1",
        "profile": PROFILE,
        "decision": decision,
        "policy_sha256": sha256(policy_path),
        "access_fingerprint": access["fingerprint"],
        "calibration_fingerprint": calibration["fingerprint"],
        "thresholds": calibration["thresholds"],
        "aggregate": aggregate,
        "checks": stable_checks,
        "blockers": [row["name"] for row in checks if not row["passed"]],
        "separator_cache_sha256": sha256(output_dir / "dev/separator_cache_manifest.json"),
        "stem_embeddings_sha256": sha256(output_dir / "dev/stem_embeddings_manifest.json"),
        "dev_rows_sha256": sha256(output_dir / "dev/dev_rows.jsonl"),
        "future_hard_files_read": 0,
        "hard_or_sealed_opened": False,
        "direct_asr_opened": False,
        "production_changed": False,
    }
    report = with_fingerprint(stable)
    report["runtime"] = {
        "wall_sec": round(time.monotonic() - started, 3),
        "separator_inference_sec": round(inference_sec, 3),
        "peak_rss_mb": round(peak_rss_mb(), 3),
        "resource_policy": resource_report,
        "checks": [row for row in resource_checks if row["name"] in {"dev_inference_sec", "peak_rss_mb"}],
    }
    report["fingerprint"] = digest_json(stable)
    write_json(existing, report)
    return report


def decision_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# SepFormer Four-Stem Target-Me Qualification v1",
        "",
        f"Decision: `{report['decision']}`",
        f"Limiting stage: `{report['limiting_stage']}`",
        f"Production changed: `{str(report['production_changed']).lower()}`",
        f"Future-hard opened: `{str(report['future_hard_opened']).lower()}`",
        f"Direct ASR opened: `{str(report['direct_asr_opened']).lower()}`",
        "",
        "## Blockers",
        "",
    ]
    lines.extend(f"- `{value}`" for value in report["blockers"] or ["none"])
    stages = report.get("stage_results") or {}
    train = stages.get("train") or {}
    dev = stages.get("dev") or {}
    lines.extend(("", "## Frozen Evidence", ""))
    lines.append(f"- train: `{train.get('decision') or 'missing'}`")
    lines.append(f"- dev: `{dev.get('decision') or 'not_opened'}`")
    if train.get("calibration_evidence"):
        evidence = train["calibration_evidence"]
        lines.append(f"- train assignment error: `{evidence.get('assignment_error_rate')}`")
        lines.append(f"- train query collapse: `{evidence.get('query_collapse_rate')}`")
    if dev.get("aggregate"):
        aggregate = dev["aggregate"]
        metrics = aggregate["metrics"]
        lines.append(f"- dev Target-Me SNR median: `{metrics['target_snr_db']['median']}` dB")
        lines.append(f"- dev improvement median: `{metrics['target_snr_improvement_db']['median']}` dB")
        lines.append(f"- dev other-local SNR median: `{metrics['other_local_snr_db']['median']}` dB")
        lines.append(f"- dev residual SNR median: `{metrics['unexplained_residual_snr_db']['median']}` dB")
        lines.append(f"- dev WavLM assignment accuracy: `{aggregate['wavlm_assignment_accuracy']}`")
        lines.append(f"- dev query collapse: `{aggregate['query_collapse_rate']}`")
    lines.extend(("", "Speaker-Preserving Neural Echo v2.17 remains the exact production fallback.", ""))
    return "\n".join(lines)


def terminal_decision_for(calibration_decision: str | None, dev_decision: str | None) -> str:
    if calibration_decision == TRAIN_RESOURCE_LIMIT or dev_decision == DEV_RESOURCE_LIMIT:
        return RESOURCE_LIMIT
    if calibration_decision == TRAIN_REJECTED or dev_decision == DEV_REJECTED:
        return REJECTED
    if calibration_decision == TRAIN_LOCKED and dev_decision == DEV_LOCKED:
        return READY
    return RESOURCE_LIMIT


def run_decide(*, policy_path: Path, output_dir: Path) -> dict[str, Any]:
    policy = load_policy(policy_path)
    frozen = read_json(output_dir / "frozen_inputs.json")
    calibration_path = output_dir / "train/calibration_lock.json"
    dev_path = output_dir / "dev/dev_report.json"
    calibration = read_json(calibration_path) if calibration_path.is_file() else {}
    dev = read_json(dev_path) if dev_path.is_file() else {}
    if not frozen.get("passed"):
        decision, limiting, blockers = RESOURCE_LIMIT, "frozen_inputs", ["frozen_inputs"]
    elif calibration.get("decision") == TRAIN_RESOURCE_LIMIT:
        decision, limiting = RESOURCE_LIMIT, "train_resource"
        blockers = list(calibration.get("blockers") or ["train_resource"])
    elif calibration.get("decision") == TRAIN_REJECTED:
        decision, limiting = REJECTED, "train_calibration"
        blockers = list(calibration.get("blockers") or [])
    elif calibration.get("decision") != TRAIN_LOCKED:
        decision, limiting, blockers = RESOURCE_LIMIT, "train_execution", ["missing_train_calibration"]
    elif dev.get("decision") == DEV_RESOURCE_LIMIT:
        decision, limiting, blockers = RESOURCE_LIMIT, "dev_resource", list(dev.get("blockers") or ["dev_resource"])
    elif dev.get("decision") == DEV_REJECTED:
        decision, limiting, blockers = REJECTED, "dev", list(dev.get("blockers") or [])
    elif dev.get("decision") == DEV_LOCKED:
        decision, limiting, blockers = READY, "dev", []
    else:
        decision, limiting, blockers = RESOURCE_LIMIT, "dev_execution", ["missing_dev_report"]
    report = with_fingerprint(
        {
            "schema": "murmurmark.sepformer_four_stem_decision/v1",
            "profile": PROFILE,
            "decision": decision,
            "limiting_stage": limiting,
            "blockers": blockers,
            "policy_sha256": sha256(policy_path),
            "frozen_inputs_fingerprint": frozen.get("fingerprint"),
            "calibration_fingerprint": calibration.get("fingerprint"),
            "dev_fingerprint": dev.get("fingerprint"),
            "future_hard_opened": False,
            "sealed_opened": False,
            "direct_asr_opened": False,
            "production_changed": False,
            "production_fallback": "speaker_preserving_neural_echo_v2_17",
            "post_asr_cleanup_promotion_credit": 0,
            "stage_results": {
                "train": {
                    "decision": calibration.get("decision"),
                    "blockers": calibration.get("blockers") or [],
                    "thresholds": calibration.get("thresholds"),
                    "calibration_evidence": calibration.get("calibration_evidence"),
                    "aggregate": calibration.get("aggregate"),
                },
                "dev": {
                    "decision": dev.get("decision"),
                    "blockers": dev.get("blockers") or [],
                    "thresholds": dev.get("thresholds"),
                    "aggregate": dev.get("aggregate"),
                },
            },
        }
    )
    write_json(output_dir / "decision.json", report)
    (output_dir / "decision.md").write_text(decision_markdown(report), encoding="utf-8")
    return report


def run_verify(*, policy_path: Path, output_dir: Path) -> dict[str, Any]:
    policy = load_policy(policy_path)
    frozen = read_json(output_dir / "frozen_inputs.json")
    corpus = verify_corpus(output_dir)
    calibration = verify_immutable_report(output_dir / "train/calibration_lock.json", sha256(policy_path))
    decision = read_json(output_dir / "decision.json")
    dev_path = output_dir / "dev/dev_report.json"
    dev = verify_immutable_report(dev_path, sha256(policy_path)) if dev_path.is_file() else {}
    expected_decision = terminal_decision_for(calibration.get("decision"), dev.get("decision"))
    current_sources = [verify_descriptor(value) for value in policy["sources"].values()]
    current_models = model_checks(policy)
    checks = [
        checked("frozen_inputs_fingerprint", fingerprint_valid(frozen), True),
        checked("frozen_inputs_passed", frozen.get("passed"), True),
        checked("frozen_policy_sha256", frozen.get("policy_sha256"), sha256(policy_path)),
        checked("current_source_hashes", all(row["passed"] for row in current_sources), True),
        checked("current_model_hashes", all(row["passed"] for row in current_models), True),
        checked("corpus_integrity", corpus["passed"], True),
        checked("calibration_fingerprint", stable_report_fingerprint_valid(calibration), True),
        checked("dev_fingerprint", not dev or stable_report_fingerprint_valid(dev), True),
        checked("decision_fingerprint", fingerprint_valid(decision), True),
        checked("terminal_decision", decision.get("decision"), expected_decision),
        checked("future_hard_not_opened", decision.get("future_hard_opened"), False),
        checked("sealed_not_opened", decision.get("sealed_opened"), False),
        checked("direct_asr_not_opened", decision.get("direct_asr_opened"), False),
        checked("production_unchanged", decision.get("production_changed"), False),
        checked("production_policy_unchanged", sha256(resolve_path(policy["sources"]["production_policy"]["path"])), policy["sources"]["production_policy"]["sha256"]),
        checked("post_asr_cleanup_credit", decision.get("post_asr_cleanup_promotion_credit"), 0),
        checked("hard_access_marker_absent", (output_dir / "hard/access.json").exists(), False),
        checked("sealed_access_marker_absent", (output_dir / "sealed/access.json").exists(), False),
    ]
    report = with_fingerprint(
        {
            "schema": "murmurmark.sepformer_four_stem_verification/v1",
            "profile": PROFILE,
            "passed": all(row["passed"] for row in checks),
            "decision": decision.get("decision"),
            "checks": checks,
        }
    )
    write_json(output_dir / "verification_report.json", report)
    if not report["passed"]:
        raise RuntimeError("SepFormer four-stem qualification verification failed")
    return report


def write_resource_limit(policy_path: Path, output_dir: Path, error: BaseException) -> dict[str, Any]:
    frozen_path = output_dir / "frozen_inputs.json"
    frozen = read_json(frozen_path) if frozen_path.is_file() else {}
    report = with_fingerprint(
        {
            "schema": "murmurmark.sepformer_four_stem_decision/v1",
            "profile": PROFILE,
            "decision": RESOURCE_LIMIT,
            "limiting_stage": "execution",
            "blockers": [f"{type(error).__name__}:{error}"],
            "policy_sha256": sha256(policy_path),
            "frozen_inputs_fingerprint": frozen.get("fingerprint"),
            "calibration_fingerprint": None,
            "dev_fingerprint": None,
            "future_hard_opened": False,
            "sealed_opened": False,
            "direct_asr_opened": False,
            "production_changed": False,
            "production_fallback": "speaker_preserving_neural_echo_v2_17",
            "post_asr_cleanup_promotion_credit": 0,
        }
    )
    write_json(output_dir / "decision.json", report)
    (output_dir / "decision.md").write_text(decision_markdown(report), encoding="utf-8")
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("freeze", "materialize", "calibrate-train", "evaluate-dev", "decide", "verify", "run"),
    )
    parser.add_argument(
        "--policy",
        type=Path,
        default=ROOT / "policies/sepformer-four-stem-target-me-qualification-v1.json",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "sessions/_reports/sepformer-four-stem-target-me-qualification-v1",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    policy_path = args.policy.resolve()
    output_dir = args.output_dir.resolve()
    try:
        if args.command == "freeze":
            report = run_freeze(policy_path=policy_path, output_dir=output_dir)
        elif args.command == "materialize":
            report = run_materialize(policy_path=policy_path, output_dir=output_dir)
        elif args.command == "calibrate-train":
            report = run_calibrate_train(policy_path=policy_path, output_dir=output_dir)
        elif args.command == "evaluate-dev":
            report = run_evaluate_dev(policy_path=policy_path, output_dir=output_dir)
        elif args.command == "decide":
            report = run_decide(policy_path=policy_path, output_dir=output_dir)
        elif args.command == "verify":
            report = run_verify(policy_path=policy_path, output_dir=output_dir)
        else:
            run_freeze(policy_path=policy_path, output_dir=output_dir)
            run_materialize(policy_path=policy_path, output_dir=output_dir)
            calibration = run_calibrate_train(policy_path=policy_path, output_dir=output_dir)
            if calibration.get("decision") == TRAIN_LOCKED:
                run_evaluate_dev(policy_path=policy_path, output_dir=output_dir)
            report = run_decide(policy_path=policy_path, output_dir=output_dir)
            dev_path = output_dir / "dev/dev_report.json"
            dev = read_json(dev_path) if dev_path.is_file() else {}
            total = float(calibration.get("runtime", {}).get("separator_inference_sec", 0.0))
            total += float(dev.get("runtime", {}).get("separator_inference_sec", 0.0))
            if total > float(load_policy(policy_path)["gates"]["resource"]["total_runtime_sec_max"]):
                raise RuntimeError(f"total separator inference {total:.3f}s exceeded locked budget")
            run_verify(policy_path=policy_path, output_dir=output_dir)
    except (OSError, RuntimeError, ValueError, ImportError) as error:
        if args.command == "run":
            report = write_resource_limit(policy_path, output_dir, error)
        else:
            raise
    print(
        json.dumps(
            {
                "decision": report.get("decision"),
                "fingerprint": report.get("fingerprint"),
                "output_dir": display_path(output_dir),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
