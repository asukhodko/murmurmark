#!/usr/bin/env python3
"""Build and evaluate the segmentation-stable Target-Me echo suppressor v2.7."""

from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import importlib.util
import json
import math
import os
import shutil
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import soundfile as sf
from scipy import signal


ROOT = Path(__file__).resolve().parent.parent
POLICY_PATH = ROOT / "policies/speaker-preserving-neural-echo-v2-7.json"
OUTPUT = ROOT / "sessions/_reports/speaker-preserving-neural-echo-v2-7"
ENROLLMENT_ROOT = (
    ROOT / "sessions/_reports/speaker-preserving-neural-echo-v2-4/enrollment"
)
WHISPER_MODEL = (
    Path.home() / ".local/share/murmurmark/models/whisper.cpp/ggml-large-v3-q5_0.bin"
)
SAMPLE_RATE = 16_000
EPS = 1.0e-9
LOCAL_STATES = {"local_only", "double_talk", "double_talk_correlation"}


class SessionRunBusy(RuntimeError):
    """Raised when another v2.7 evaluator owns the same session output."""


@contextmanager
def exclusive_session_run(output: Path) -> Iterable[None]:
    output.mkdir(parents=True, exist_ok=True)
    lock_path = output / "run.lock"
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            handle.seek(0)
            holder = handle.read().strip() or "unknown holder"
            raise SessionRunBusy(holder) from error
        handle.seek(0)
        handle.truncate()
        handle.write(
            json.dumps(
                {"pid": os.getpid(), "started_unix_sec": round(time.time(), 3)},
                sort_keys=True,
            )
            + "\n"
        )
        handle.flush()
        os.fsync(handle.fileno())
        yield
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


sys.path.insert(0, str(ROOT / "scripts"))
TARGET_ME = load_module(ROOT / "scripts/audit-target-me.py", "murmurmark_spne_v27_target_me")
TRANSCRIBER = load_module(
    ROOT / "scripts/transcribe-simple-whispercpp.py", "murmurmark_spne_v27_transcriber"
)
METRICS = load_module(
    ROOT / "scripts/report-speaker-preserving-neural-echo-v2-3-corpus.py",
    "murmurmark_spne_v27_legacy_metrics",
)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--policy", type=Path, default=POLICY_PATH)
    value.add_argument("--output", type=Path, default=OUTPUT)
    value.add_argument("--whisper-model", type=Path, default=WHISPER_MODEL)
    sub = value.add_subparsers(dest="command", required=True)
    enrollment = sub.add_parser("build-enrollment")
    enrollment.add_argument("--refresh", action="store_true")
    run = sub.add_parser("run")
    run.add_argument("session", type=Path)
    run.add_argument("--refresh", action="store_true")
    run.add_argument("--proposal-only", action="store_true")
    verify = sub.add_parser("verify")
    verify.add_argument("session", type=Path)
    return value


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return rows
    for line in lines:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


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


def seed_prior_diagnostic_cache(session: Path, destination: Path) -> dict[str, Any]:
    """Reuse content-addressed ASR clips produced by earlier candidate revisions."""

    destination.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for profile in (
        "speaker-preserving-neural-echo-v2-4",
        "speaker-preserving-neural-echo-v2-6",
    ):
        source = session / "derived/preprocess" / profile / "diagnostic-asr-cache"
        linked = 0
        if source.is_dir():
            for cache_entry in source.iterdir():
                if not cache_entry.is_dir():
                    continue
                target = destination / cache_entry.name
                if target.exists():
                    continue
                try:
                    shutil.copytree(cache_entry, target, copy_function=os.link)
                except OSError:
                    shutil.rmtree(target, ignore_errors=True)
                    shutil.copytree(cache_entry, target, copy_function=shutil.copy2)
                linked += 1
        rows.append({"profile": profile, "seeded_entries": linked})
    return {
        "mode": "content_addressed_hardlink_or_copy",
        "seeded_entries": sum(int(row["seeded_entries"]) for row in rows),
        "sources": rows,
    }


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def stable_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def relative(path: Path, root: Path = ROOT) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path.resolve())


def fingerprint(path: Path, root: Path = ROOT) -> dict[str, Any]:
    return {
        "path": relative(path, root),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
    }


def normalized_median(matrix: np.ndarray) -> np.ndarray:
    vector = np.median(np.asarray(matrix, dtype=np.float64), axis=0)
    norm = float(np.linalg.norm(vector))
    if not np.isfinite(norm) or norm <= EPS:
        raise RuntimeError("cannot build a normalized enrollment centroid")
    return (vector / norm).astype(np.float64)


def cosine(left: np.ndarray | None, right: np.ndarray | None) -> float:
    if left is None or right is None:
        return float("nan")
    denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
    if denominator <= EPS:
        return float("nan")
    return float(np.dot(left, right) / denominator)


def calibrated_remote_score(values: dict[str, Any], classifier: dict[str, Any]) -> float:
    features = [float(values[key]) for key in classifier["features"]]
    mean = [float(value) for value in classifier["mean"]]
    scale = [float(value) for value in classifier["scale"]]
    coefficients = [float(value) for value in classifier["coefficients"]]
    if not (
        len(features) == len(mean) == len(scale) == len(coefficients)
        and all(math.isfinite(value) for value in features + mean + scale + coefficients)
        and all(value > 0.0 for value in scale)
    ):
        return float("nan")
    logit = float(classifier["intercept"]) + sum(
        coefficient * (feature - center) / spread
        for feature, center, spread, coefficient in zip(
            features, mean, scale, coefficients, strict=True
        )
    )
    if logit >= 0:
        return 1.0 / (1.0 + math.exp(-logit))
    exp_logit = math.exp(logit)
    return exp_logit / (1.0 + exp_logit)


def verify_policy(path: Path) -> dict[str, Any]:
    policy = read_json(path)
    if policy.get("schema") != "murmurmark.speaker_preserving_neural_echo_policy/v2.7":
        raise RuntimeError("unexpected v2.7 policy schema")
    source = policy["source"]
    checks: dict[str, Any] = {}
    for path_key, hash_key in (
        ("controlled_corpus", "controlled_corpus_sha256"),
        ("split_manifest", "split_manifest_sha256"),
        ("supervision_manifest", "supervision_manifest_sha256"),
    ):
        artifact = ROOT / str(source[path_key])
        observed = sha256(artifact) if artifact.is_file() else None
        checks[path_key] = {
            "path": relative(artifact),
            "expected": source[hash_key],
            "observed": observed,
            "passed": observed == source[hash_key],
        }
    model = policy["models"]["wavlm"]
    model_root = Path(str(model["path"])).expanduser()
    for name, expected in model["files"].items():
        artifact = model_root / name
        observed = sha256(artifact) if artifact.is_file() else None
        checks[f"wavlm/{name}"] = {
            "path": str(artifact),
            "expected": expected,
            "observed": observed,
            "passed": observed == expected,
        }
    checks["zero_post_asr_cleanup_credit"] = {
        "passed": policy["audio_contract"].get("post_asr_cleanup_promotion_credit") == 0
    }
    checks["candidate_is_primary_asr_input"] = {
        "passed": policy["audio_contract"].get("candidate_audio_is_primary_whisper_input")
        is True
    }
    if not all(bool(row.get("passed")) for row in checks.values()):
        raise RuntimeError(f"v2.7 policy verification failed: {checks}")
    return {"policy": policy, "checks": checks, "passed": True}


def read_audio(path: Path, *, dtype: str = "float32") -> tuple[np.ndarray, int]:
    values, sample_rate = sf.read(path, dtype=dtype, always_2d=True)
    mono = values.mean(axis=1) if values.shape[1] > 1 else values[:, 0]
    return np.asarray(mono), int(sample_rate)


def audio_16k_float(path: Path) -> np.ndarray:
    values, sample_rate = read_audio(path, dtype="float32")
    values = np.nan_to_num(values).astype(np.float32)
    if sample_rate != SAMPLE_RATE:
        divisor = math.gcd(sample_rate, SAMPLE_RATE)
        values = signal.resample_poly(
            values, SAMPLE_RATE // divisor, sample_rate // divisor
        ).astype(np.float32)
    return values


def supervision_rows(policy: dict[str, Any]) -> list[dict[str, Any]]:
    source = ROOT / policy["source"]["supervision_manifest"]
    allowed = set(policy["enrollment"]["allowed_kinds"])
    rows = [
        row
        for row in read_jsonl(source)
        if row.get("split") == policy["enrollment"]["allowed_split"]
        and row.get("kind") in allowed
    ]
    rows.sort(key=lambda row: str(row.get("clip_id")))
    expected = int(policy["enrollment"]["expected_items"])
    if len(rows) != expected:
        raise RuntimeError(f"expected {expected} train enrollment rows, got {len(rows)}")
    return rows


def verify_enrollment_manifest(output: Path, policy_sha: str) -> dict[str, Any]:
    manifest = read_json(output / "enrollment_manifest.json")
    if manifest.get("schema") != "murmurmark.target_me_controlled_enrollment/v2.4":
        return {}
    if manifest.get("policy_sha256") != policy_sha:
        return {}
    for item in manifest.get("artifacts", {}).values():
        path = ROOT / str(item.get("path"))
        if not path.is_file() or sha256(path) != item.get("sha256"):
            return {}
    return manifest


def build_enrollment(args: argparse.Namespace, verification: dict[str, Any]) -> dict[str, Any]:
    policy = verification["policy"]
    destination = args.output / "enrollment"
    policy_sha = sha256(args.policy)
    if not args.refresh:
        existing = verify_enrollment_manifest(destination, policy_sha)
        if existing:
            return existing

    rows = supervision_rows(policy)
    supervision_root = (ROOT / policy["source"]["supervision_manifest"]).parent
    audio_rows: list[np.ndarray] = []
    provenance: list[dict[str, Any]] = []
    for row in rows:
        audio = row.get("audio") if isinstance(row.get("audio"), dict) else {}
        path = supervision_root / str(audio.get("path"))
        observed = sha256(path) if path.is_file() else None
        if observed != audio.get("sha256"):
            raise RuntimeError(f"controlled enrollment clip missing or changed: {path}")
        values = audio_16k_float(path)
        audio_rows.append(values)
        provenance.append(
            {
                "clip_id": row["clip_id"],
                "kind": row["kind"],
                "session_id": row["session_id"],
                "split": row["split"],
                "audio": fingerprint(path),
            }
        )

    wavlm_path = Path(str(policy["models"]["wavlm"]["path"])).expanduser()
    wavlm = TARGET_ME.WavLMXVectorBackend(wavlm_path)
    resemblyzer = TARGET_ME.ResemblyzerDVectorBackend()
    started = time.monotonic()
    wavlm_embeddings = wavlm.embed_audio_batch(audio_rows, batch_size=8)
    resemblyzer_embeddings = resemblyzer.embed_audio_batch(audio_rows)
    valid_wavlm = [vector for vector in wavlm_embeddings if vector is not None]
    valid_resemblyzer = [vector for vector in resemblyzer_embeddings if vector is not None]
    minimum = policy["enrollment"]["minimum_valid_items"]
    if len(valid_wavlm) < int(minimum["wavlm"]):
        raise RuntimeError(
            f"WavLM enrollment produced {len(valid_wavlm)} valid embeddings"
        )
    if len(valid_resemblyzer) < int(minimum["resemblyzer"]):
        raise RuntimeError(
            f"Resemblyzer enrollment produced {len(valid_resemblyzer)} valid embeddings"
        )
    wavlm_matrix = np.vstack(valid_wavlm)
    resemblyzer_matrix = np.vstack(valid_resemblyzer)
    wavlm_centroid = normalized_median(wavlm_matrix)
    resemblyzer_centroid = normalized_median(resemblyzer_matrix)

    destination.mkdir(parents=True, exist_ok=True)
    artifacts = {
        "wavlm_embeddings": destination / "wavlm_embeddings.npy",
        "wavlm_centroid": destination / "wavlm_centroid.npy",
        "resemblyzer_embeddings": destination / "resemblyzer_embeddings.npy",
        "resemblyzer_centroid": destination / "resemblyzer_centroid.npy",
        "items": destination / "enrollment_items.jsonl",
    }
    np.save(artifacts["wavlm_embeddings"], wavlm_matrix, allow_pickle=False)
    np.save(artifacts["wavlm_centroid"], wavlm_centroid, allow_pickle=False)
    np.save(artifacts["resemblyzer_embeddings"], resemblyzer_matrix, allow_pickle=False)
    np.save(artifacts["resemblyzer_centroid"], resemblyzer_centroid, allow_pickle=False)
    write_jsonl(artifacts["items"], provenance)
    payload = {
        "schema": "murmurmark.target_me_controlled_enrollment/v2.4",
        "status": "ready",
        "policy_sha256": policy_sha,
        "split": policy["enrollment"]["allowed_split"],
        "kinds": policy["enrollment"]["allowed_kinds"],
        "items": len(rows),
        "valid_items": {
            "wavlm": len(valid_wavlm),
            "resemblyzer": len(valid_resemblyzer),
        },
        "missing_embedding_clip_ids": {
            "wavlm": [
                rows[index]["clip_id"]
                for index, vector in enumerate(wavlm_embeddings)
                if vector is None
            ],
            "resemblyzer": [
                rows[index]["clip_id"]
                for index, vector in enumerate(resemblyzer_embeddings)
                if vector is None
            ],
        },
        "source_fingerprint": stable_digest(provenance),
        "models": policy["models"],
        "runtime_sec": round(time.monotonic() - started, 3),
        "artifacts": {key: fingerprint(path) for key, path in artifacts.items()},
    }
    write_json(destination / "enrollment_manifest.json", payload)
    return payload


def enrollment_vectors(args: argparse.Namespace) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    policy = read_json(args.policy)
    manifest_path = ENROLLMENT_ROOT / "enrollment_manifest.json"
    expected_sha = str(policy.get("enrollment", {}).get("source_manifest_sha256") or "")
    if not manifest_path.is_file() or sha256(manifest_path) != expected_sha:
        raise RuntimeError("frozen v2.4 controlled enrollment missing or changed")
    manifest = read_json(manifest_path)
    for item in manifest.get("artifacts", {}).values():
        path = ROOT / str(item.get("path") or "")
        if not path.is_file() or sha256(path) != item.get("sha256"):
            raise RuntimeError(f"frozen enrollment artifact missing or changed: {path}")
    wavlm = np.load(ENROLLMENT_ROOT / "wavlm_centroid.npy", allow_pickle=False)
    resemblyzer = np.load(
        ENROLLMENT_ROOT / "resemblyzer_centroid.npy", allow_pickle=False
    )
    return wavlm, resemblyzer, manifest


def me_guard_dialogue_path(session: Path) -> Path:
    resolved = session / "derived/transcript-simple/whisper-cpp/resolved"
    for name in (
        "clean_dialogue.reviewed_v1.json",
        "clean_dialogue.agent_reviewed_v1.json",
        "clean_dialogue.shadow_v2.json",
    ):
        path = resolved / name
        if path.is_file():
            return path
    return resolved / "clean_dialogue.shadow_v2.json"


def session_paths(session: Path) -> dict[str, Path]:
    transcript = session / "derived/transcript-simple/whisper-cpp"
    return {
        "baseline_audio": session / "derived/asr/mic.wav",
        "remote_audio": session / "derived/preprocess/audio/remote_for_aec.wav",
        "speaker_state": session / "derived/preprocess/echo/speaker_state.jsonl",
        "baseline_mic_asr": transcript / "raw/mic.json",
        "baseline_remote_asr": transcript / "raw/remote.json",
        "baseline_prepared": transcript / "prepared-audio/mic_speech.wav",
        "reviewed_dialogue": me_guard_dialogue_path(session),
    }


def local_intervals(states: list[dict[str, Any]]) -> list[tuple[float, float]]:
    return [
        (float(row.get("start") or 0.0), float(row.get("end") or 0.0))
        for row in states
        if str(row.get("state") or "") in LOCAL_STATES
    ]


def intersects(interval: tuple[float, float], other: tuple[float, float]) -> bool:
    return min(interval[1], other[1]) > max(interval[0], other[0])


def rms_db(values: np.ndarray) -> float:
    rms = float(np.sqrt(np.mean(np.square(values, dtype=np.float64)))) if values.size else 0.0
    return 20.0 * math.log10(max(rms, 1.0e-12))


def propose_windows(
    *,
    session: Path,
    policy: dict[str, Any],
    wavlm_centroid: np.ndarray,
    resemblyzer_centroid: np.ndarray,
    output: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    paths = session_paths(session)
    mic = audio_16k_float(paths["baseline_audio"])
    remote = audio_16k_float(paths["remote_audio"])
    count = min(mic.size, remote.size)
    mic, remote = mic[:count], remote[:count]
    states = read_jsonl(paths["speaker_state"])
    local = local_intervals(states)
    contract = policy["audio_contract"]
    gate = policy["proposal_gate"]
    window_sec = float(contract["window_sec"])
    window_samples = int(round(window_sec * SAMPLE_RATE))
    guard = float(contract["local_state_guard_sec"])
    allowed_states = set(gate["states"])

    candidates: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for state_index, row in enumerate(states):
        state = str(row.get("state") or "")
        if state not in allowed_states:
            continue
        row_start = float(row.get("start") or 0.0)
        row_end = min(float(row.get("end") or row_start), count / SAMPLE_RATE)
        sub_index = 0
        start = row_start
        while start + window_sec <= row_end + 1.0e-6:
            end = start + window_sec
            item = {
                "start": round(start, 6),
                "end": round(end, 6),
                "state": state,
                "state_index": state_index,
                "sub_index": sub_index,
            }
            if any(intersects((start - guard, end + guard), interval) for interval in local):
                item["rejected_reason"] = "local_state_guard"
                rejected.append(item)
            else:
                first = int(round(start * SAMPLE_RATE))
                mic_piece = mic[first : first + window_samples]
                remote_piece = remote[first : first + window_samples]
                if mic_piece.size != window_samples or remote_piece.size != window_samples:
                    item["rejected_reason"] = "short_audio"
                    rejected.append(item)
                else:
                    item["mic_audio"] = mic_piece
                    item["remote_audio"] = remote_piece
                    item["mic_rms_db"] = rms_db(mic_piece)
                    item["remote_rms_db"] = rms_db(remote_piece)
                    candidates.append(item)
            start += window_sec
            sub_index += 1

    wavlm_path = Path(str(policy["models"]["wavlm"]["path"])).expanduser()
    wavlm = TARGET_ME.WavLMXVectorBackend(wavlm_path)
    resemblyzer = TARGET_ME.ResemblyzerDVectorBackend()
    mic_rows = [row["mic_audio"] for row in candidates]
    remote_rows = [row["remote_audio"] for row in candidates]
    wavlm_mic = wavlm.embed_audio_batch(mic_rows, batch_size=16)
    wavlm_remote = wavlm.embed_audio_batch(remote_rows, batch_size=16)
    resemblyzer_mic = resemblyzer.embed_audio_batch(mic_rows)
    resemblyzer_remote = resemblyzer.embed_audio_batch(remote_rows)

    accepted: list[dict[str, Any]] = []
    for index, item in enumerate(candidates):
        wm, wr = wavlm_mic[index], wavlm_remote[index]
        rm, rr = resemblyzer_mic[index], resemblyzer_remote[index]
        public = {key: value for key, value in item.items() if not key.endswith("_audio")}
        public.update(
            {
                "wavlm_target_me_similarity": cosine(wm, wavlm_centroid),
                "wavlm_mic_remote_similarity": cosine(wm, wr),
                "resemblyzer_target_me_similarity": cosine(rm, resemblyzer_centroid),
                "resemblyzer_mic_remote_similarity": cosine(rm, rr),
            }
        )
        public["wavlm_target_remote_margin"] = (
            public["wavlm_target_me_similarity"] - public["wavlm_mic_remote_similarity"]
        )
        public["resemblyzer_target_remote_margin"] = (
            public["resemblyzer_target_me_similarity"]
            - public["resemblyzer_mic_remote_similarity"]
        )
        checks = {
            "finite_embeddings": all(
                math.isfinite(float(public[key]))
                for key in (
                    "wavlm_target_me_similarity",
                    "wavlm_mic_remote_similarity",
                    "resemblyzer_target_me_similarity",
                    "resemblyzer_mic_remote_similarity",
                )
            ),
            "mic_rms": public["mic_rms_db"] >= float(gate.get("mic_rms_db_min", -65.0)),
            "remote_rms": public["remote_rms_db"]
            >= float(gate.get("remote_rms_db_min", -55.0)),
            "resemblyzer_target": public["resemblyzer_target_me_similarity"]
            <= float(gate["resemblyzer_target_me_similarity_max"]),
            "resemblyzer_remote": public["resemblyzer_mic_remote_similarity"]
            >= float(gate["resemblyzer_mic_remote_similarity_min"]),
            "wavlm_target": public["wavlm_target_me_similarity"]
            <= float(gate["wavlm_target_me_similarity_max"]),
            "wavlm_remote": public["wavlm_mic_remote_similarity"]
            >= float(gate["wavlm_mic_remote_similarity_min"]),
            "wavlm_margin": public["wavlm_target_remote_margin"]
            <= float(gate["wavlm_target_remote_margin_max"]),
        }
        classifier = gate["train_calibrated_classifier"]
        public["train_calibrated_remote_score"] = calibrated_remote_score(
            public, classifier
        )
        base_passed = all(
            checks[key]
            for key in ("finite_embeddings", "mic_rms", "remote_rms")
        )
        strict_passed = all(
            checks[key]
            for key in (
                "resemblyzer_target",
                "resemblyzer_remote",
                "wavlm_target",
                "wavlm_remote",
                "wavlm_margin",
            )
        )
        target_guard_passed = checks["wavlm_target"] or checks["resemblyzer_target"]
        classifier_passed = (
            math.isfinite(public["train_calibrated_remote_score"])
            and public["train_calibrated_remote_score"]
            >= float(classifier["score_min"])
            and target_guard_passed
        )
        public["proposal_gate_mode"] = (
            "strict"
            if strict_passed
            else "train_calibrated_classifier" if classifier_passed else None
        )
        public["checks"] = checks
        public["proposal_id"] = hashlib.sha256(
            f"{session.name}:{public['start']:.6f}:{public['end']:.6f}".encode()
        ).hexdigest()[:20]
        if base_passed and (strict_passed or classifier_passed):
            public["status"] = "proposed"
            accepted.append(public)
        else:
            public["status"] = "rejected"
            public["rejected_reason"] = "proposal_gate"
            rejected.append(public)

    write_jsonl(output / "proposed_windows.jsonl", accepted)
    write_jsonl(output / "rejected_windows.jsonl", rejected)
    return accepted, rejected


def proposal_basis(
    *,
    policy: dict[str, Any],
    enrollment_manifest_sha256: str,
    paths: dict[str, Path],
) -> dict[str, Any]:
    return {
        "schema": "murmurmark.speaker_preserving_neural_echo_proposal_basis/v2.7",
        "enrollment_manifest_sha256": enrollment_manifest_sha256,
        "proposal_gate": policy["proposal_gate"],
        "audio_contract": {
            key: policy["audio_contract"][key]
            for key in ("sample_rate", "window_sec", "local_state_guard_sec")
        },
        "inputs": {
            key: fingerprint(paths[key])
            for key in ("baseline_audio", "remote_audio", "speaker_state")
        },
    }


def cached_proposals(
    *,
    session: Path,
    policy: dict[str, Any],
    wavlm_centroid: np.ndarray,
    resemblyzer_centroid: np.ndarray,
    enrollment_manifest_sha256: str,
    output: Path,
    refresh: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    paths = session_paths(session)
    basis = proposal_basis(
        policy=policy,
        enrollment_manifest_sha256=enrollment_manifest_sha256,
        paths=paths,
    )
    manifest_path = output / "proposal_manifest.json"
    proposed_path = output / "proposed_windows.jsonl"
    rejected_path = output / "rejected_windows.jsonl"
    manifest = read_json(manifest_path)
    if (
        not refresh
        and manifest.get("basis") == basis
        and proposed_path.is_file()
        and rejected_path.is_file()
        and manifest.get("proposed_windows_sha256") == sha256(proposed_path)
        and manifest.get("rejected_windows_sha256") == sha256(rejected_path)
    ):
        return read_jsonl(proposed_path), read_jsonl(rejected_path)
    proposed, rejected = propose_windows(
        session=session,
        policy=policy,
        wavlm_centroid=wavlm_centroid,
        resemblyzer_centroid=resemblyzer_centroid,
        output=output,
    )
    write_json(
        manifest_path,
        {
            "schema": "murmurmark.speaker_preserving_neural_echo_proposal_manifest/v2.7",
            "basis": basis,
            "proposed_windows": len(proposed),
            "rejected_windows": len(rejected),
            "proposed_windows_sha256": sha256(proposed_path),
            "rejected_windows_sha256": sha256(rejected_path),
        },
    )
    return proposed, rejected


def merged_gain_intervals(windows: list[dict[str, Any]]) -> list[tuple[int, int, float]]:
    grouped: dict[float, list[tuple[int, int]]] = {}
    for row in windows:
        gain = 10.0 ** (float(row["attenuation_db"]) / 20.0)
        start = int(round(float(row["start"]) * SAMPLE_RATE))
        end = int(round(float(row["end"]) * SAMPLE_RATE))
        grouped.setdefault(gain, []).append((start, end))
    result: list[tuple[int, int, float]] = []
    for gain, intervals in grouped.items():
        merged: list[tuple[int, int]] = []
        for start, end in sorted(intervals):
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        result.extend((start, end, gain) for start, end in merged)
    return sorted(result)


def materialize_pcm16(
    baseline: np.ndarray,
    windows: list[dict[str, Any]],
    fade_sec: float,
) -> tuple[np.ndarray, dict[str, Any]]:
    if baseline.dtype != np.int16:
        raise RuntimeError("baseline PCM must be int16")
    envelope = np.ones(baseline.size, dtype=np.float32)
    fade_samples = int(round(fade_sec * SAMPLE_RATE))
    intervals = merged_gain_intervals(windows)
    for raw_start, raw_end, gain in intervals:
        start = max(0, raw_start)
        end = min(baseline.size, raw_end)
        if end <= start:
            continue
        local = np.full(end - start, gain, dtype=np.float32)
        fade = min(fade_samples, (end - start) // 2)
        if fade:
            phase = np.linspace(0.0, math.pi / 2.0, fade, endpoint=False)
            ramp = (1.0 - np.sin(phase) ** 2 * (1.0 - gain)).astype(np.float32)
            local[:fade] = ramp
            local[-fade:] = ramp[::-1]
        envelope[start:end] = np.minimum(envelope[start:end], local)
    candidate = np.rint(baseline.astype(np.float64) * envelope).clip(-32768, 32767).astype(np.int16)
    changed = candidate != baseline
    outside = np.zeros_like(changed)
    for start, end, _ in intervals:
        outside[max(0, start) : min(baseline.size, end)] = True
    return candidate, {
        "selected_windows": len(windows),
        "selected_seconds": round(
            sum(float(row["end"]) - float(row["start"]) for row in windows), 3
        ),
        "merged_intervals": len(intervals),
        "changed_samples": int(np.count_nonzero(changed)),
        "outside_selected_changed_samples": int(np.count_nonzero(changed & ~outside)),
        "peak": int(np.max(np.abs(candidate.astype(np.int32)))) if candidate.size else 0,
        "clipped_sample_ratio": round(
            float(np.mean(np.abs(candidate.astype(np.int32)) >= 32767)) if candidate.size else 0.0,
            9,
        ),
    }


def write_pcm16(path: Path, values: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp.wav")
    sf.write(temporary, values, SAMPLE_RATE, subtype="PCM_16")
    os.replace(temporary, path)


def prepare_speech(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    subprocess_command = [
        shutil.which("ffmpeg") or "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-af",
        "highpass=f=100,lowpass=f=7600,alimiter=limit=0.98",
        "-ar",
        str(SAMPLE_RATE),
        "-ac",
        "1",
        str(destination),
    ]
    TRANSCRIBER.run(subprocess_command)


def rows_in_hard_window(rows: list[dict[str, Any]], start: float, end: float) -> list[dict[str, Any]]:
    return [row for row in rows if start <= (float(row["start"]) + float(row["end"])) / 2 < end]


def transcribe_clip_cached(
    *,
    prepared: np.ndarray,
    spec: dict[str, int],
    cache_root: Path,
    whisper_model: Path,
    force: bool,
    audio_origin_ms: int = 0,
) -> list[dict[str, Any]]:
    relative_seek_ms = int(spec["seek_ms"]) - int(audio_origin_ms)
    relative_end_ms = int(spec["clip_end_ms"]) - int(audio_origin_ms)
    if relative_seek_ms < 0 or relative_end_ms <= relative_seek_ms:
        raise RuntimeError("invalid diagnostic ASR audio origin")
    seek = int(round(relative_seek_ms * SAMPLE_RATE / 1000.0))
    end = int(round(relative_end_ms * SAMPLE_RATE / 1000.0))
    clip = prepared[seek:end]
    if clip.dtype == np.int16:
        pcm = np.asarray(clip, dtype=np.int16)
    else:
        pcm = np.rint(np.clip(clip, -1.0, 32767.0 / 32768.0) * 32768.0).astype(
            np.int16
        )
    basis = {
        "clip_sha256": hashlib.sha256(pcm.tobytes()).hexdigest(),
        "model_sha256": sha256(whisper_model),
        "language": "ru",
        "max_context": 0,
        "threads": 6,
    }
    key = stable_digest(basis)
    destination = cache_root / key
    output_base = destination / "result"
    metadata = destination / "cache.json"
    if force or read_json(metadata).get("basis") != basis or not output_base.with_suffix(".json").is_file():
        destination.mkdir(parents=True, exist_ok=True)
        clip_path = destination / "clip.wav"
        write_pcm16(clip_path, pcm)
        TRANSCRIBER.run_whisper(
            whisper_cli=shutil.which("whisper-cli") or "whisper-cli",
            model=whisper_model,
            language="ru",
            threads=6,
            max_context=0,
            prompt=None,
            duration_ms=0,
            input_wav=clip_path,
            output_base=output_base,
        )
        write_json(metadata, {"schema": "murmurmark.spne_v27_chunk_asr_cache/v1", "basis": basis})
    payload = read_json(output_base.with_suffix(".json"))
    rows: list[dict[str, Any]] = []
    for row in payload.get("transcription", []):
        if not isinstance(row, dict):
            continue
        shifted = TRANSCRIBER.shift_transcription_row(row, int(spec["seek_ms"]))
        offsets = shifted.get("offsets") if isinstance(shifted.get("offsets"), dict) else {}
        center = (float(offsets.get("from") or 0.0) + float(offsets.get("to") or 0.0)) / 2.0
        if spec["hard_start_ms"] <= center < spec["hard_end_ms"]:
            rows.append(shifted)
    return rows


def asr_dict_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    for row in rows:
        offsets = row.get("offsets") if isinstance(row.get("offsets"), dict) else {}
        text = str(row.get("text") or "").strip()
        observed = METRICS.tokens(text)
        start = float(offsets.get("from") or 0.0) / 1000.0
        end = float(offsets.get("to") or 0.0) / 1000.0
        if end > start and observed and not METRICS.is_hallucination(text):
            converted.append({"start": start, "end": end, "text": text, "tokens": observed})
    return converted


def remote_supported_burden(
    mic: list[dict[str, Any]],
    remote: list[dict[str, Any]],
    states: list[dict[str, Any]],
    *,
    padding: float = 1.0,
) -> dict[str, Any]:
    """Measure remote words present in mic without depending on mic segmentation.

    Each authoritative remote segment is the fixed unit. Mic segment boundaries may
    move after filtering, but the multiset of remote-supported words in its temporal
    neighbourhood remains comparable.
    """

    matched_tokens = 0
    reference_tokens = 0
    supported_seconds = 0.0
    examples: list[dict[str, Any]] = []
    considered_segments = 0
    for reference in remote:
        ratios = METRICS.state_ratios(states, reference["start"], reference["end"])
        remote_ratio = sum(
            ratios.get(key, 0.0) for key in METRICS.REMOTE_STATES
        )
        if remote_ratio < 0.5:
            continue
        expected = list(reference["tokens"])
        if not expected:
            continue
        nearby = [
            row
            for row in mic
            if METRICS.overlap(reference, row, padding=padding) > 0.0
        ]
        observed = [token for row in nearby for token in row["tokens"]]
        matched, total = METRICS.counter_recall(expected, observed)
        considered_segments += 1
        matched_tokens += matched
        reference_tokens += total
        duration = max(float(reference["end"]) - float(reference["start"]), 0.0)
        supported_seconds += duration * matched / max(total, 1)
        if matched:
            examples.append(
                {
                    "start": round(float(reference["start"]), 3),
                    "end": round(float(reference["end"]), 3),
                    "matched_tokens": matched,
                    "reference_tokens": total,
                    "remote_state_ratio": round(remote_ratio, 6),
                    "reference_text_sha256": hashlib.sha256(
                        str(reference["text"]).encode()
                    ).hexdigest(),
                }
            )
    return {
        "seconds": round(supported_seconds, 3),
        "segments": considered_segments,
        "matched_tokens": matched_tokens,
        "reference_tokens": reference_tokens,
        "match_ratio": round(matched_tokens / max(reference_tokens, 1), 6),
        "metric": "remote_reference_token_support_v1",
        "examples": examples[:30],
    }


def reviewed_retention(
    dialogue: dict[str, Any], candidate: list[dict[str, Any]]
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    matched = total = 0
    for utterance in dialogue.get("utterances", []):
        if not isinstance(utterance, dict) or str(utterance.get("role") or "").lower() != "me":
            continue
        expected = METRICS.tokens(str(utterance.get("text") or ""))
        interval = {
            "start": float(utterance.get("start") or 0.0),
            "end": float(utterance.get("end") or utterance.get("start") or 0.0),
        }
        nearby = [row for row in candidate if METRICS.overlap(interval, row, padding=1.0) > 0.0]
        observed = [token for row in nearby for token in row["tokens"]]
        row_matched, row_total = METRICS.counter_recall(expected, observed)
        matched += row_matched
        total += row_total
        rows.append(
            {
                "utterance_id": str(utterance.get("id") or ""),
                "start": interval["start"],
                "end": interval["end"],
                "matched_tokens": row_matched,
                "expected_tokens": row_total,
                "text_sha256": hashlib.sha256(str(utterance.get("text") or "").encode()).hexdigest(),
            }
        )
    return {
        "matched_tokens": matched,
        "expected_tokens": total,
        "ratio": round(matched / max(total, 1), 6),
        "utterances": rows,
    }


def compare_reviewed(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    base = {row["utterance_id"]: row for row in baseline["utterances"]}
    regressions = []
    for row in candidate["utterances"]:
        previous = base.get(row["utterance_id"])
        if previous and row["matched_tokens"] < previous["matched_tokens"]:
            regressions.append(
                {
                    "utterance_id": row["utterance_id"],
                    "start": row["start"],
                    "end": row["end"],
                    "baseline_matched_tokens": previous["matched_tokens"],
                    "candidate_matched_tokens": row["matched_tokens"],
                    "expected_tokens": row["expected_tokens"],
                    "text_sha256": row["text_sha256"],
                }
            )
    return {
        "baseline_matched_tokens": baseline["matched_tokens"],
        "candidate_matched_tokens": candidate["matched_tokens"],
        "aggregate_delta_tokens": candidate["matched_tokens"] - baseline["matched_tokens"],
        "regression_count": len(regressions),
        "regressions": regressions,
    }


def reviewed_regression_chunks(
    *,
    session: Path,
    regressions: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    selected: list[dict[str, Any]],
) -> list[int]:
    """Find changed ASR chunks whose baseline rows carried lost reviewed Me words."""

    baseline = METRICS.asr_segments(session_paths(session)["baseline_mic_asr"])
    selected_chunks = {int(row["diagnostic_chunk"]) for row in selected}
    bounds = {
        int(row["chunk_index"]): (
            float(row["hard_start_sec"]),
            float(row["hard_end_sec"]),
        )
        for row in decisions
        if int(row["chunk_index"]) in selected_chunks
    }
    affected: set[int] = set()
    for regression in regressions:
        interval = {
            "start": float(regression.get("start") or 0.0),
            "end": float(regression.get("end") or regression.get("start") or 0.0),
        }
        carrying_rows = [
            row for row in baseline if METRICS.overlap(interval, row, padding=1.0) > 0.0
        ]
        for row in carrying_rows:
            center = (float(row["start"]) + float(row["end"])) / 2.0
            for chunk_index, (start, end) in bounds.items():
                if start <= center < end:
                    affected.add(chunk_index)
    return sorted(affected)


def diagnostic_metrics(
    *,
    baseline_rows: list[dict[str, Any]],
    candidate_rows: list[dict[str, Any]],
    remote_rows: list[dict[str, Any]],
    states: list[dict[str, Any]],
    dialogue: dict[str, Any],
    start: float,
    end: float,
) -> dict[str, Any]:
    baseline = rows_in_hard_window(baseline_rows, start, end)
    candidate = rows_in_hard_window(candidate_rows, start, end)
    remote = rows_in_hard_window(remote_rows, start, end)
    local = METRICS.local_retention(baseline, candidate, states)
    before = remote_supported_burden(baseline, remote, states)
    after = remote_supported_burden(candidate, remote, states)
    relevant_dialogue = {
        "utterances": [
            row
            for row in dialogue.get("utterances", [])
            if str(row.get("role") or "").lower() == "me"
            and start <= (float(row.get("start") or 0.0) + float(row.get("end") or 0.0)) / 2 < end
        ]
    }
    reviewed_baseline = reviewed_retention(relevant_dialogue, baseline)
    reviewed_candidate = reviewed_retention(relevant_dialogue, candidate)
    reviewed = compare_reviewed(reviewed_baseline, reviewed_candidate)
    return {
        "local_retention": local,
        "remote_like_before_sec": before["seconds"],
        "remote_like_after_sec": after["seconds"],
        "remote_like_reduction_sec": round(before["seconds"] - after["seconds"], 3),
        "remote_supported_tokens_before": before["matched_tokens"],
        "remote_supported_tokens_after": after["matched_tokens"],
        "remote_supported_token_reduction": (
            before["matched_tokens"] - after["matched_tokens"]
        ),
        "reviewed_me": reviewed,
    }


def diagnostic_select(
    *,
    args: argparse.Namespace,
    policy: dict[str, Any],
    session: Path,
    baseline_pcm: np.ndarray,
    proposals: list[dict[str, Any]],
    output: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    paths = session_paths(session)
    baseline_rows = METRICS.asr_segments(paths["baseline_mic_asr"])
    remote_rows = METRICS.asr_segments(paths["baseline_remote_asr"])
    states = read_jsonl(paths["speaker_state"])
    dialogue = read_json(paths["reviewed_dialogue"])
    duration_ms = int(round(baseline_pcm.size / SAMPLE_RATE * 1000.0))
    guard = policy["diagnostic_asr_guard"]
    specs = TRANSCRIBER.build_window_specs(
        source_duration_ms=duration_ms,
        duration_ms=0,
        window_sec=int(guard["window_sec"]),
        overlap_sec=int(guard["overlap_sec"]),
    )
    by_index: dict[int, list[dict[str, Any]]] = {}
    for row in proposals:
        index = int(float(row["start"]) // int(guard["window_sec"])) + 1
        by_index.setdefault(index, []).append(row)

    cache_root = output / "diagnostic-asr-cache"
    workspace = output / "diagnostic-work" / str(os.getpid())
    workspace.mkdir(parents=True, exist_ok=True)

    def evaluate_chunk(item: tuple[int, list[dict[str, Any]]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        index, rows = item
        if index < 1 or index > len(specs):
            return ({}, [])
        spec = specs[index - 1]
        start = spec["hard_start_ms"] / 1000.0
        end = spec["hard_end_ms"] / 1000.0
        attempts: list[dict[str, Any]] = []
        chosen: float | None = None

        def evaluate(attenuation: float) -> dict[str, Any]:
            proposed = [dict(row, attenuation_db=float(attenuation)) for row in rows]
            candidate, audio = materialize_pcm16(
                baseline_pcm, proposed, float(policy["audio_contract"]["fade_sec"])
            )
            candidate_path = workspace / f"chunk_{index:04d}_{abs(int(attenuation)):02d}db.wav"
            prepared_path = workspace / f"chunk_{index:04d}_{abs(int(attenuation)):02d}db.speech.wav"
            clip_start = int(round(spec["seek_ms"] * SAMPLE_RATE / 1000.0))
            clip_end = int(round(spec["clip_end_ms"] * SAMPLE_RATE / 1000.0))
            write_pcm16(candidate_path, candidate[clip_start:clip_end])
            prepare_speech(candidate_path, prepared_path)
            prepared, prepared_rate = read_audio(prepared_path, dtype="int16")
            if prepared_rate != SAMPLE_RATE:
                raise RuntimeError("unexpected prepared candidate sample rate")
            raw_rows = transcribe_clip_cached(
                prepared=np.asarray(prepared, dtype=np.int16),
                spec=spec,
                cache_root=cache_root,
                whisper_model=args.whisper_model,
                force=args.refresh,
                audio_origin_ms=int(spec["seek_ms"]),
            )
            candidate_rows = asr_dict_rows(raw_rows)
            metrics = diagnostic_metrics(
                baseline_rows=baseline_rows,
                candidate_rows=candidate_rows,
                remote_rows=remote_rows,
                states=states,
                dialogue=dialogue,
                start=start,
                end=end,
            )
            checks = {
                "local_retention": metrics["local_retention"]["ratio"]
                >= float(guard["local_token_retention_ratio_min"]),
                "remote_reduction": metrics["remote_like_reduction_sec"]
                >= float(guard["remote_like_reduction_sec_min"]),
                "remote_no_increase": metrics["remote_like_after_sec"]
                <= metrics["remote_like_before_sec"]
                + float(guard["remote_like_increase_sec_max"]),
                "remote_token_reduction": metrics[
                    "remote_supported_token_reduction"
                ]
                >= int(guard["remote_supported_token_reduction_min"]),
                "reviewed_me_no_regression": metrics["reviewed_me"]["regression_count"]
                <= int(guard.get("reviewed_me_token_regressions_max", 0)),
                "outside_selected_exact": audio["outside_selected_changed_samples"] == 0,
            }
            return {
                "attenuation_db": attenuation,
                "metrics": metrics,
                "checks": checks,
                "passed": all(checks.values()),
                "candidate_sha256": sha256(candidate_path),
                "prepared_sha256": sha256(prepared_path),
            }

        levels = [float(value) for value in guard["attenuation_levels_db"]]
        strongest = min(levels)
        screening = evaluate(strongest)
        attempts.append(screening)
        remote_screen_passed = (
            screening["checks"]["remote_reduction"]
            and screening["checks"]["remote_no_increase"]
        )
        if remote_screen_passed:
            remaining = sorted(
                (value for value in levels if value != strongest), reverse=True
            )
            workers = max(1, int(guard.get("attenuation_workers", 1)))
            if workers == 1 or len(remaining) <= 1:
                attempts.extend(evaluate(attenuation) for attenuation in remaining)
            else:
                with ThreadPoolExecutor(
                    max_workers=min(workers, len(remaining))
                ) as executor:
                    attempts.extend(executor.map(evaluate, remaining))
            passing = [attempt for attempt in attempts if attempt["passed"]]
            if passing:
                winner = max(
                    passing,
                    key=lambda attempt: (
                        float(attempt["metrics"]["remote_like_reduction_sec"]),
                        float(attempt["attenuation_db"]),
                    ),
                )
                chosen = float(winner["attenuation_db"])
        accepted = chosen is not None
        decision = {
            "chunk_index": index,
            "hard_start_sec": start,
            "hard_end_sec": end,
            "proposal_count": len(rows),
            "proposal_ids": [row["proposal_id"] for row in rows],
            "attempts": attempts,
            "accepted": accepted,
            "selected_attenuation_db": chosen,
            "reason": (
                "maximum_safe_remote_reduction"
                if accepted
                else "no_safe_asr_improvement"
            ),
        }
        selected_rows = (
            [dict(row, attenuation_db=chosen, diagnostic_chunk=index) for row in rows]
            if accepted
            else []
        )
        return decision, selected_rows

    items = sorted(by_index.items())
    chunk_workers = max(1, int(guard.get("chunk_workers", 1)))
    if chunk_workers == 1 or len(items) <= 1:
        results = [evaluate_chunk(item) for item in items]
    else:
        with ThreadPoolExecutor(
            max_workers=min(chunk_workers, len(items))
        ) as executor:
            results = list(executor.map(evaluate_chunk, items))
    decisions = [decision for decision, _ in results if decision]
    selected = [row for _, rows in results for row in rows]
    write_jsonl(output / "diagnostic_chunk_decisions.jsonl", decisions)
    write_jsonl(output / "selected_windows.jsonl", selected)
    return selected, decisions


def transcribe_final(
    *,
    args: argparse.Namespace,
    session: Path,
    prepared_path: Path,
    selected: list[dict[str, Any]],
    output: Path,
) -> Path:
    paths = session_paths(session)
    baseline_payload = read_json(paths["baseline_mic_asr"])
    baseline_rows = [row for row in baseline_payload.get("transcription", []) if isinstance(row, dict)]
    prepared, sample_rate = read_audio(prepared_path, dtype="int16")
    if sample_rate != SAMPLE_RATE:
        raise RuntimeError("unexpected final prepared sample rate")
    duration_ms = int(round(prepared.size / SAMPLE_RATE * 1000.0))
    guard = read_json(args.policy)["diagnostic_asr_guard"]
    specs = TRANSCRIBER.build_window_specs(
        source_duration_ms=duration_ms,
        duration_ms=0,
        window_sec=int(guard["window_sec"]),
        overlap_sec=int(guard["overlap_sec"]),
    )
    changed = {int(row["diagnostic_chunk"]) for row in selected}
    rows: list[dict[str, Any]] = []
    cache_root = output / "diagnostic-asr-cache"
    chunk_records: list[dict[str, Any]] = []
    for spec in specs:
        index = int(spec["index"])
        if index in changed:
            current = transcribe_clip_cached(
                prepared=np.asarray(prepared, dtype=np.int16),
                spec=spec,
                cache_root=cache_root,
                whisper_model=args.whisper_model,
                force=args.refresh,
            )
            status = "transcribed_or_cache"
        else:
            current = []
            for row in baseline_rows:
                offsets = row.get("offsets") if isinstance(row.get("offsets"), dict) else {}
                center = (float(offsets.get("from") or 0.0) + float(offsets.get("to") or 0.0)) / 2.0
                if spec["hard_start_ms"] <= center < spec["hard_end_ms"]:
                    current.append(copy.deepcopy(row))
            status = "bit_exact_baseline_reuse"
        rows.extend(current)
        chunk_records.append({"index": index, "status": status, "rows": len(current)})
    payload = copy.deepcopy(baseline_payload)
    payload["transcription"] = sorted(
        rows,
        key=lambda row: (
            float((row.get("offsets") or {}).get("from") or 0.0),
            float((row.get("offsets") or {}).get("to") or 0.0),
        ),
    )
    payload.setdefault("params", {})
    payload["params"]["murmurmark_source_audio"] = str(prepared_path)
    payload["params"]["murmurmark_echo_profile"] = "speaker_preserving_neural_echo_v2_7"
    destination = output / "direct-asr/raw/mic.json"
    write_json(destination, payload)
    TRANSCRIBER.write_whisper_text_sidecars(destination.with_suffix(""))
    write_json(
        output / "direct-asr/chunk_report.json",
        {
        "schema": "murmurmark.speaker_preserving_neural_echo_chunk_asr/v2.7",
            "changed_chunks": sorted(changed),
            "chunks": chunk_records,
            "candidate_audio_is_primary_whisper_input": True,
        },
    )
    return destination


def final_metrics(session: Path, candidate_asr: Path) -> dict[str, Any]:
    paths = session_paths(session)
    baseline = METRICS.asr_segments(paths["baseline_mic_asr"])
    candidate = METRICS.asr_segments(candidate_asr)
    remote = METRICS.asr_segments(paths["baseline_remote_asr"])
    states = read_jsonl(paths["speaker_state"])
    dialogue = read_json(paths["reviewed_dialogue"])
    local = METRICS.local_retention(baseline, candidate, states)
    before = remote_supported_burden(baseline, remote, states)
    after = remote_supported_burden(candidate, remote, states)
    reviewed_baseline = reviewed_retention(dialogue, baseline)
    reviewed_candidate = reviewed_retention(dialogue, candidate)
    return {
        "local_retention": local,
        "remote_like_before": before,
        "remote_like_after": after,
        "remote_like_reduction_sec": round(before["seconds"] - after["seconds"], 3),
        "remote_supported_token_reduction": (
            before["matched_tokens"] - after["matched_tokens"]
        ),
        "reviewed_me": compare_reviewed(reviewed_baseline, reviewed_candidate),
        "segment_counts": {
            "baseline_mic": len(baseline),
            "candidate_mic": len(candidate),
            "remote": len(remote),
        },
    }


def fallback(
    *, session: Path, output: Path, reason: str, basis: dict[str, Any], details: Any = None
) -> dict[str, Any]:
    baseline = session_paths(session)["baseline_audio"]
    candidate = output / "candidate_clean_mic_pcm16.wav"
    output.mkdir(parents=True, exist_ok=True)
    if baseline.is_file():
        shutil.copy2(baseline, candidate)
    payload = {
        "schema": "murmurmark.speaker_preserving_neural_echo_runtime/v2.7",
        "status": "fallback",
        "reason": reason,
        "details": details,
        "basis": basis,
        "fallback": "local_fir_role_masked",
        "output": fingerprint(candidate, session) if candidate.is_file() else None,
    }
    write_json(output / "runtime_report.json", payload)
    return payload


def run_session(args: argparse.Namespace) -> dict[str, Any]:
    session = args.session.resolve()
    output = session / "derived/preprocess/speaker-preserving-neural-echo-v2-7"
    try:
        with exclusive_session_run(output):
            return run_session_locked(args, session=session, output=output)
    except SessionRunBusy as error:
        return {
            "schema": "murmurmark.speaker_preserving_neural_echo_runtime/v2.7",
            "status": "busy",
            "reason": "session_run_already_active",
            "session": session.name,
            "lock_holder": str(error),
        }


def run_session_locked(
    args: argparse.Namespace, *, session: Path, output: Path
) -> dict[str, Any]:
    cache_seed = seed_prior_diagnostic_cache(session, output / "diagnostic-asr-cache")
    verification = verify_policy(args.policy)
    policy = verification["policy"]
    wavlm_centroid, resemblyzer_centroid, enrollment = enrollment_vectors(args)
    paths = session_paths(session)
    required = [
        "baseline_audio",
        "remote_audio",
        "speaker_state",
        "baseline_mic_asr",
        "baseline_remote_asr",
        "reviewed_dialogue",
    ]
    missing = [key for key in required if not paths[key].is_file()]
    basis = {
        "policy": fingerprint(args.policy),
        "runtime": fingerprint(Path(__file__)),
        "enrollment_manifest_sha256": sha256(ENROLLMENT_ROOT / "enrollment_manifest.json"),
        "inputs": {key: fingerprint(paths[key], session) for key in required if paths[key].is_file()},
        "whisper_model": fingerprint(args.whisper_model),
    }
    existing = read_json(output / "runtime_report.json")
    if (
        not args.refresh
        and existing.get("basis") == basis
        and existing.get("status") in {"candidate", "fallback"}
    ):
        return existing
    if missing:
        return fallback(session=session, output=output, reason="missing_inputs", basis=basis, details=missing)
    baseline_pcm, sample_rate = read_audio(paths["baseline_audio"], dtype="int16")
    if sample_rate != SAMPLE_RATE:
        return fallback(
            session=session,
            output=output,
            reason="baseline_not_pcm16_16k",
            basis=basis,
            details={"sample_rate": sample_rate, "dtype": str(baseline_pcm.dtype)},
        )
    baseline_pcm = np.asarray(baseline_pcm, dtype=np.int16)
    started = time.monotonic()
    try:
        proposals, rejected = cached_proposals(
            session=session,
            policy=policy,
            wavlm_centroid=wavlm_centroid,
            resemblyzer_centroid=resemblyzer_centroid,
            enrollment_manifest_sha256=basis["enrollment_manifest_sha256"],
            output=output,
            refresh=args.refresh,
        )
        if args.proposal_only:
            payload = {
                "schema": "murmurmark.speaker_preserving_neural_echo_runtime/v2.7",
                "status": "proposal_only",
                "basis": basis,
                "proposed_windows": len(proposals),
                "rejected_windows": len(rejected),
                "enrollment": enrollment,
            }
            write_json(output / "runtime_report.json", payload)
            return payload
        selected, decisions = diagnostic_select(
            args=args,
            policy=policy,
            session=session,
            baseline_pcm=baseline_pcm,
            proposals=proposals,
            output=output,
        )
        if not selected:
            return fallback(
                session=session,
                output=output,
                reason="no_asr_audited_improvement",
                basis=basis,
                details={"proposed_windows": len(proposals), "diagnostic_chunks": len(decisions)},
            )
        candidate_path = output / "candidate_clean_mic_pcm16.wav"
        prepared_path = output / "prepared_mic_speech.wav"
        gates = policy["development_gates"]
        safety_rollbacks: list[dict[str, Any]] = []
        while True:
            candidate_pcm, audio = materialize_pcm16(
                baseline_pcm, selected, float(policy["audio_contract"]["fade_sec"])
            )
            write_pcm16(candidate_path, candidate_pcm)
            prepare_speech(candidate_path, prepared_path)
            candidate_asr = transcribe_final(
                args=args,
                session=session,
                prepared_path=prepared_path,
                selected=selected,
                output=output,
            )
            metrics = final_metrics(session, candidate_asr)
            regressions = list(metrics["reviewed_me"].get("regressions") or [])
            if len(regressions) <= int(gates["reviewed_me_token_regressions_max"]):
                break
            rollback_chunks = reviewed_regression_chunks(
                session=session,
                regressions=regressions,
                decisions=decisions,
                selected=selected,
            )
            if not rollback_chunks:
                break
            removed = [
                row for row in selected if int(row["diagnostic_chunk"]) in rollback_chunks
            ]
            safety_rollbacks.append(
                {
                    "iteration": len(safety_rollbacks) + 1,
                    "reason": "reviewed_me_asr_regression",
                    "chunk_indices": rollback_chunks,
                    "removed_window_ids": [row["proposal_id"] for row in removed],
                    "regressions": regressions,
                }
            )
            selected = [
                row for row in selected if int(row["diagnostic_chunk"]) not in rollback_chunks
            ]
            if not selected:
                break
        write_jsonl(output / "selected_windows.jsonl", selected)
        write_jsonl(output / "safety_rollbacks.jsonl", safety_rollbacks)
        checks = {
            "local_retention": metrics["local_retention"]["ratio"]
            >= float(gates["final_local_token_retention_ratio_min"]),
            "opening_retention": metrics["local_retention"]["opening_ratio"]
            >= float(gates["opening_token_retention_ratio_min"]),
            "remote_no_increase": metrics["remote_like_after"]["seconds"]
            <= metrics["remote_like_before"]["seconds"]
            + float(gates["remote_like_seconds_increase_max"]),
            "remote_reduction": metrics["remote_like_reduction_sec"]
            >= float(gates["remote_like_seconds_reduction_min"]),
            "remote_token_reduction": metrics[
                "remote_supported_token_reduction"
            ]
            >= int(gates["remote_supported_token_reduction_min"]),
            "reviewed_me_no_regression": metrics["reviewed_me"]["regression_count"]
            <= int(gates["reviewed_me_token_regressions_max"]),
            "outside_selected_exact": audio["outside_selected_changed_samples"] == 0,
            "clipping": audio["clipped_sample_ratio"]
            <= float(gates["clipped_sample_ratio_max"]),
        }
        if not all(checks.values()):
            return fallback(
                session=session,
                output=output,
                reason="final_development_gates_failed",
                basis=basis,
                details={"checks": checks, "metrics": metrics, "audio": audio},
            )
        payload = {
            "schema": "murmurmark.speaker_preserving_neural_echo_runtime/v2.7",
            "status": "candidate",
            "candidate": policy["candidate_revision"],
            "basis": basis,
            "enrollment": {
                "manifest_sha256": basis["enrollment_manifest_sha256"],
                "items": enrollment["items"],
                "split": enrollment["split"],
            },
            "proposal": {
                "proposed_windows": len(proposals),
                "rejected_windows": len(rejected),
            },
            "diagnostic": {
                "chunks": len(decisions),
                "accepted_chunks": len([row for row in decisions if row["accepted"]]),
                "selected_windows": len(selected),
                "safety_rollback_count": len(safety_rollbacks),
                "safety_rollback_chunks": sorted(
                    {
                        chunk
                        for row in safety_rollbacks
                        for chunk in row["chunk_indices"]
                    }
                ),
                "cache_seed": cache_seed,
            },
            "audio": audio,
            "metrics": metrics,
            "checks": checks,
            "candidate_audio_is_primary_whisper_input": True,
            "post_asr_cleanup_promotion_credit": 0,
            "runtime_sec": round(time.monotonic() - started, 3),
            "output": {
                "candidate": fingerprint(candidate_path, session),
                "prepared": fingerprint(prepared_path, session),
                "direct_asr": fingerprint(candidate_asr, session),
            },
        }
        write_json(output / "runtime_report.json", payload)
        return payload
    except Exception as error:
        return fallback(
            session=session,
            output=output,
            reason="runtime_failure",
            basis=basis,
            details={"type": type(error).__name__, "message": str(error)},
        )
    finally:
        workspace = output / "diagnostic-work" / str(os.getpid())
        shutil.rmtree(workspace, ignore_errors=True)
        try:
            workspace.parent.rmdir()
        except OSError:
            pass


def verify_session(args: argparse.Namespace) -> dict[str, Any]:
    session = args.session.resolve()
    output = session / "derived/preprocess/speaker-preserving-neural-echo-v2-7"
    report = read_json(output / "runtime_report.json")
    candidate = output / "candidate_clean_mic_pcm16.wav"
    checks = {
        "report_schema": report.get("schema")
        == "murmurmark.speaker_preserving_neural_echo_runtime/v2.7",
        "terminal_status": report.get("status") in {"candidate", "fallback"},
        "candidate_exists": candidate.is_file(),
        "candidate_hash": candidate.is_file()
        and report.get("output", {}).get("candidate", report.get("output", {})).get("sha256")
        == sha256(candidate),
    }
    payload = {
        "schema": "murmurmark.speaker_preserving_neural_echo_verification/v2.7",
        "session": session.name,
        "checks": checks,
        "passed": all(checks.values()),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return payload


def main() -> int:
    args = parser().parse_args()
    if not args.policy.is_absolute():
        args.policy = (ROOT / args.policy).resolve() if not args.policy.exists() else args.policy.resolve()
    args.output = args.output.expanduser().resolve()
    args.whisper_model = args.whisper_model.expanduser().resolve()
    if args.command == "build-enrollment":
        payload = build_enrollment(args, verify_policy(args.policy))
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.command == "run":
        payload = run_session(args)
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if payload.get("status") in {"candidate", "fallback", "proposal_only"} else 1
    payload = verify_session(args)
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
