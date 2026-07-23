#!/usr/bin/env python3
"""Evaluate and safely select Timeline-Correct Hybrid AEC candidates.

The promotion lab is intentionally separate from authoritative preprocessing.
It can only replace ``mic_for_asr.wav`` through ``apply-policy`` after a frozen
corpus report explicitly says PROMOTE and all session fingerprints still match.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from scipy import signal
from scipy.io import wavfile

from echo_promotion_timeline import (
    SIGN_CONVENTION,
    align_remote_curve,
    estimate_delay_rows,
    timeline_contract,
)


VERSION = "0.3.0"
PROFILE = "echo_suppression_promotion_v1"
SESSION_SCHEMA = "murmurmark.echo_suppression_promotion_session/v1"
CORPUS_SCHEMA = "murmurmark.echo_suppression_promotion_corpus/v1"
POLICY_SCHEMA = "murmurmark.echo_suppression_production_policy/v1"
SELECTION_SCHEMA = "murmurmark.echo_suppression_selection/v1"
BASELINE = "local_fir_role_masked"
COVERAGE = "coverage_v2_remote_gate_local_fir"
WEBRTC = "webrtc_aec3_aligned_v1"
SPEEX = "speex_mdf_aligned_v1"
OFFLINE = "offline_aec_v2_best_nonlinear_v1"
WEBRTC_GATE = "webrtc_aec3_coverage_gate_v1"
CANDIDATE_ORDER = (BASELINE, COVERAGE, WEBRTC, SPEEX, OFFLINE, WEBRTC_GATE)
REMOTE_STATES = {"remote_only", "remote_only_correlation", "remote_only_level"}
DOUBLE_TALK_STATES = {"double_talk", "double_talk_correlation"}
LOCAL_STATES = {"local_only"}
SILENCE_STATES = {"silence"}
KNOWN_HALLUCINATIONS = (
    re.compile(r"^\s*продолжение следует\s*[.!?…-]*\s*$", re.IGNORECASE),
    re.compile(r"^\s*субтитры.*$", re.IGNORECASE),
    re.compile(r"^\s*редактор субтитров.*$", re.IGNORECASE),
)
TOKEN_RE = re.compile(r"[\wёЁ]+", re.UNICODE)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Echo Suppression Promotion v1 laboratory and policy.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    session_parser = subparsers.add_parser("session", help="materialize and evaluate one session")
    session_parser.add_argument("session", type=Path)
    session_parser.add_argument("--refresh", action="store_true")
    session_parser.add_argument("--asr-probes", action="store_true")
    session_parser.add_argument("--full-shadow", action="store_true")
    session_parser.add_argument("--max-windows-per-class", type=int, default=2)
    session_parser.add_argument("--faster-whisper-model", type=Path)
    session_parser.add_argument("--whisper-model", type=Path)
    session_parser.add_argument("--candidate", action="append", choices=CANDIDATE_ORDER[1:])

    corpus_parser = subparsers.add_parser("corpus", help="freeze and aggregate representative sessions")
    corpus_parser.add_argument("sessions", nargs="+", type=Path)
    corpus_parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("sessions/_reports/echo-suppression-promotion-v1"),
    )
    corpus_parser.add_argument("--run", action="store_true")
    corpus_parser.add_argument("--refresh", action="store_true")
    corpus_parser.add_argument("--asr-probes", action="store_true")
    corpus_parser.add_argument("--full-shadow", action="store_true")
    corpus_parser.add_argument("--faster-whisper-model", type=Path)
    corpus_parser.add_argument("--whisper-model", type=Path)

    policy_parser = subparsers.add_parser("apply-policy", help="apply the one automatic production policy")
    policy_parser.add_argument("session", type=Path)
    policy_parser.add_argument(
        "--policy",
        type=Path,
        default=Path("policies/echo-suppression-production-v1.json"),
    )
    return parser.parse_args()


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


def write_json(path: Path, payload: dict[str, Any]) -> None:
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


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint(path: Path, root: Path) -> dict[str, Any]:
    return {
        "path": relative(path, root),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
    }


def relative(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path.resolve())


def read_wav(path: Path, target_rate: int = 16_000) -> np.ndarray:
    sample_rate, data = wavfile.read(path)
    if data.ndim > 1:
        data = data.mean(axis=1)
    if np.issubdtype(data.dtype, np.integer):
        info = np.iinfo(data.dtype)
        data = data.astype(np.float32) / float(max(abs(info.min), info.max))
    else:
        data = data.astype(np.float32)
    data = np.nan_to_num(data)
    if sample_rate != target_rate:
        divisor = math.gcd(int(sample_rate), target_rate)
        data = signal.resample_poly(data, target_rate // divisor, int(sample_rate) // divisor)
    return np.asarray(data, dtype=np.float32)


def write_wav(path: Path, audio: np.ndarray, sample_rate: int = 16_000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    wavfile.write(path, sample_rate, np.clip(audio, -1.0, 1.0).astype(np.float32))


def speech_band(audio: np.ndarray, sample_rate: int = 16_000) -> np.ndarray:
    sos = signal.butter(
        4,
        [100.0 / (sample_rate / 2.0), 7_600.0 / (sample_rate / 2.0)],
        btype="bandpass",
        output="sos",
    )
    return signal.sosfilt(sos, audio).astype(np.float32)


def state_category(row: dict[str, Any]) -> str:
    state = str(row.get("state") or "")
    if state in REMOTE_STATES:
        return "remote_only"
    if state in DOUBLE_TALK_STATES:
        return "double_talk"
    if state in LOCAL_STATES:
        return "local_only"
    if state in SILENCE_STATES:
        return "silence"
    return "uncertain"


def bounds(row: dict[str, Any], sample_rate: int, count: int) -> tuple[int, int]:
    start = max(0, int(round(float(row.get("start", row.get("start_sec", 0.0))) * sample_rate)))
    end = min(count, int(round(float(row.get("end", row.get("end_sec", 0.0))) * sample_rate)))
    return start, max(start, end)


def canonical_role_audio(
    *,
    raw_mic: np.ndarray,
    engine_native: np.ndarray,
    state_rows: list[dict[str, Any]],
    sample_rate: int = 16_000,
    replace_double_talk: bool = True,
) -> np.ndarray:
    count = min(raw_mic.size, engine_native.size)
    output = raw_mic[:count].copy()
    covered = np.zeros(count, dtype=bool)
    for row in state_rows:
        start, end = bounds(row, sample_rate, count)
        if end <= start:
            continue
        category = state_category(row)
        if category in {"silence", "local_only"}:
            output[start:end] = raw_mic[start:end]
        elif category == "remote_only" or (
            category == "double_talk" and replace_double_talk
        ):
            output[start:end] = engine_native[start:end]
        else:
            output[start:end] = raw_mic[start:end]
        covered[start:end] = True
    output[~covered] = raw_mic[:count][~covered]
    return output


def hybrid_remote_only_audio(
    baseline_clean: np.ndarray,
    aggressive_clean: np.ndarray,
    state_rows: list[dict[str, Any]],
    sample_rate: int = 16_000,
) -> np.ndarray:
    count = min(baseline_clean.size, aggressive_clean.size)
    output = baseline_clean[:count].copy()
    for row in state_rows:
        if state_category(row) != "remote_only":
            continue
        start, end = bounds(row, sample_rate, count)
        output[start:end] = aggressive_clean[start:end]
    return output


def rms(audio: np.ndarray) -> float:
    if audio.size == 0:
        return 0.0
    return math.sqrt(float(np.mean(np.asarray(audio, dtype=np.float64) ** 2)) + 1.0e-12)


def rms_db(audio: np.ndarray) -> float:
    return 20.0 * math.log10(rms(audio) + 1.0e-12)


def normalized_corr(left: np.ndarray, right: np.ndarray) -> float:
    count = min(left.size, right.size)
    if count < 2:
        return 0.0
    a = left[:count].astype(np.float64) - float(np.mean(left[:count]))
    b = right[:count].astype(np.float64) - float(np.mean(right[:count]))
    denominator = math.sqrt(float(np.dot(a, a) * np.dot(b, b))) + 1.0e-12
    return float(np.dot(a, b) / denominator)


def collect_category(audio: np.ndarray, rows: list[dict[str, Any]], category: str) -> np.ndarray:
    parts: list[np.ndarray] = []
    for row in rows:
        if state_category(row) != category:
            continue
        start, end = bounds(row, 16_000, audio.size)
        if end > start:
            parts.append(audio[start:end])
    return np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)


def audio_metrics(
    *,
    candidate: np.ndarray,
    baseline: np.ndarray,
    aligned_remote: np.ndarray,
    state_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    count = min(candidate.size, baseline.size, aligned_remote.size)
    candidate = candidate[:count]
    baseline = baseline[:count]
    aligned_remote = aligned_remote[:count]
    result: dict[str, Any] = {
        "finite": bool(np.all(np.isfinite(candidate))),
        "peak": round(float(np.max(np.abs(candidate))) if candidate.size else 0.0, 6),
        "clipped_ratio": round(float(np.mean(np.abs(candidate) >= 0.999)) if candidate.size else 0.0, 9),
        "baseline_peak": round(float(np.max(np.abs(baseline))) if baseline.size else 0.0, 6),
        "baseline_clipped_ratio": round(
            float(np.mean(np.abs(baseline) >= 0.999)) if baseline.size else 0.0,
            9,
        ),
        "duration_sec": round(count / 16_000.0, 3),
    }
    for category in ("remote_only", "local_only", "double_talk", "silence"):
        candidate_part = collect_category(candidate, state_rows, category)
        baseline_part = collect_category(baseline, state_rows, category)
        remote_part = collect_category(aligned_remote, state_rows, category)
        result[category] = {
            "seconds": round(candidate_part.size / 16_000.0, 3),
            "candidate_rms_db": round(rms_db(candidate_part), 3),
            "baseline_rms_db": round(rms_db(baseline_part), 3),
            "energy_delta_db": round(rms_db(candidate_part) - rms_db(baseline_part), 3),
            "waveform_correlation_to_baseline": round(abs(normalized_corr(candidate_part, baseline_part)), 6),
            "remote_correlation": round(abs(normalized_corr(candidate_part, remote_part)), 6),
            "baseline_remote_correlation": round(abs(normalized_corr(baseline_part, remote_part)), 6),
        }
    local_seconds = result["local_only"]["seconds"]
    double_seconds = result["double_talk"]["seconds"]
    protected_seconds = float(local_seconds) + float(double_seconds)
    result["protected_speech_seconds"] = round(protected_seconds, 3)
    result["active_protection_ratio"] = round(
        (
            float(result["local_only"]["waveform_correlation_to_baseline"]) * float(local_seconds)
            + float(result["double_talk"]["waveform_correlation_to_baseline"]) * float(double_seconds)
        )
        / max(protected_seconds, 1.0e-9),
        6,
    )
    return result


def acoustic_mode(session: Path) -> dict[str, Any]:
    paths = (
        session / "derived/audit/speaker-mode-hardening-v1/acoustic_mode_report.json",
        session / "derived/preprocess/echo/local_fir_report.json",
    )
    for path in paths:
        payload = read_json(path)
        if path.name == "local_fir_report.json":
            payload = payload.get("acoustic_mode") if isinstance(payload.get("acoustic_mode"), dict) else {}
        if payload.get("mode"):
            context_tags = payload.get("context_tags") if isinstance(payload.get("context_tags"), list) else []
            mode = "no_speech" if "verified_no_speech" in context_tags else payload.get("mode")
            return {
                "mode": mode,
                "confidence": payload.get("confidence"),
                "source": relative(path, session),
            }
    return {"mode": "uncertain", "confidence": 0.0, "source": None}


def canonical_inputs(session: Path, output_root: Path, refresh: bool) -> dict[str, Any]:
    canonical_dir = output_root / "canonical"
    mic_path = session / "derived/preprocess/audio/mic_raw_for_asr.wav"
    remote_path = session / "derived/preprocess/audio/remote_for_aec.wav"
    state_path = session / "derived/preprocess/echo/speaker_state.jsonl"
    local_clean_path = session / "derived/preprocess/audio/mic_clean_local_fir.wav"
    local_role_path = session / "derived/preprocess/audio/mic_role_masked_for_asr.wav"
    required = (mic_path, remote_path, state_path, local_clean_path, local_role_path)
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise RuntimeError(f"missing local_fir preprocessing artifacts: {', '.join(missing)}")

    output_mic = canonical_dir / "mic.wav"
    output_remote = canonical_dir / "remote.wav"
    output_aligned = canonical_dir / "remote_aligned.wav"
    delay_path = canonical_dir / "delay_curve.jsonl"
    contract_path = canonical_dir / "timeline_contract.json"
    source_fingerprint = {
        "mic": fingerprint(mic_path, session),
        "remote": fingerprint(remote_path, session),
        "speaker_state": fingerprint(state_path, session),
        "local_fir_clean": fingerprint(local_clean_path, session),
        "local_fir_role_masked": fingerprint(local_role_path, session),
    }
    timeline_code_sha256 = sha256(Path(__file__).with_name("echo_promotion_timeline.py"))
    existing = read_json(contract_path)
    if (
        not refresh
        and output_mic.exists()
        and output_remote.exists()
        and output_aligned.exists()
        and existing.get("input_fingerprint") == source_fingerprint
        and existing.get("timeline_code_sha256") == timeline_code_sha256
    ):
        return {
            "mic": read_wav(output_mic),
            "remote": read_wav(output_remote),
            "aligned_remote": read_wav(output_aligned),
            "state_rows": read_jsonl(state_path),
            "local_clean": speech_band(read_wav(local_clean_path)),
            "baseline_asr": read_wav(local_role_path),
            "timeline": existing,
            "source_fingerprint": source_fingerprint,
        }

    mic = speech_band(read_wav(mic_path))
    remote = speech_band(read_wav(remote_path))
    count = min(mic.size, remote.size)
    mic = mic[:count]
    remote = remote[:count]
    delay_rows = estimate_delay_rows(remote, mic, 16_000)
    aligned = align_remote_curve(remote, 16_000, delay_rows)
    contract = timeline_contract(
        sample_rate=16_000,
        delay_rows=delay_rows,
        source="canonical speech-band mic/remote",
        estimator="gcc_phat_signed_windowed_median_v1",
    )
    contract["input_fingerprint"] = source_fingerprint
    contract["timeline_code_sha256"] = timeline_code_sha256
    contract["aligned_remote_sha256"] = None
    write_wav(output_mic, mic)
    write_wav(output_remote, remote)
    write_wav(output_aligned, aligned)
    contract["aligned_remote_sha256"] = sha256(output_aligned)
    write_jsonl(delay_path, delay_rows)
    write_json(contract_path, contract)
    return {
        "mic": mic,
        "remote": remote,
        "aligned_remote": aligned,
        "state_rows": read_jsonl(state_path),
        "local_clean": speech_band(read_wav(local_clean_path)),
        "baseline_asr": read_wav(local_role_path),
        "timeline": contract,
        "source_fingerprint": source_fingerprint,
    }


def ensure_helper(root: Path, name: str) -> Path:
    helper = root / f".build/tools/{name}"
    source_paths = {
        "murmurmark-aec-webrtc": [root / "tools/murmurmark-aec-webrtc/src/main.rs"],
        "murmurmark-aec-speexdsp": [root / "tools/murmurmark-aec-speexdsp.c"],
    }[name]
    stale = not helper.exists() or any(path.stat().st_mtime > helper.stat().st_mtime for path in source_paths)
    if stale:
        script = root / (
            "scripts/build-webrtc-apm-helper.sh"
            if name.endswith("webrtc")
            else "scripts/build-speexdsp-helper.sh"
        )
        subprocess.run([str(script), str(helper)], check=True)
    return helper


def run_external_candidate(
    *,
    root: Path,
    helper_name: str,
    mic_path: Path,
    remote_path: Path,
    output_path: Path,
) -> None:
    helper = ensure_helper(root, helper_name)
    command = [str(helper), str(mic_path), str(remote_path), str(output_path)]
    if helper_name.endswith("webrtc"):
        command.append("0")
    else:
        command.extend(["20", "500", "0"])
    subprocess.run(command, check=True)


def prepare_offline_stage(
    session: Path,
    output_root: Path,
    canonical: dict[str, Any],
    refresh: bool,
    *,
    stage_name: str,
    candidate_configs: tuple[str, ...] = (),
) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    root = Path(__file__).resolve().parent.parent
    stage = output_root / stage_name
    stage_audio = stage / "derived/preprocess/audio"
    stage_echo = stage / "derived/preprocess/echo"
    report_path = stage_echo / "offline_aec_v2_report.json"
    input_fingerprint = canonical["source_fingerprint"]
    lab_code_sha256 = sha256(root / "scripts/echo-guard-offline-aec-v2-lab.py")
    configuration = {
        "candidate_configs": list(candidate_configs),
        "inputs_preconditioned": True,
    }
    metadata_path = stage / "stage_input.json"
    existing = read_json(metadata_path)
    if (
        refresh
        or not report_path.exists()
        or existing.get("input_fingerprint") != input_fingerprint
        or existing.get("lab_code_sha256") != lab_code_sha256
        or existing.get("configuration") != configuration
    ):
        if stage.exists():
            shutil.rmtree(stage)
        stage_audio.mkdir(parents=True, exist_ok=True)
        stage_echo.mkdir(parents=True, exist_ok=True)
        write_wav(stage_audio / "mic_raw_for_asr.wav", canonical["mic"])
        write_wav(stage_audio / "remote_for_aec.wav", canonical["aligned_remote"])
        write_wav(stage_audio / "mic_clean_local_fir.wav", canonical["local_clean"])
        source_state = session / "derived/preprocess/echo/speaker_state.jsonl"
        shutil.copy2(source_state, stage_echo / "speaker_state.jsonl")
        started = time.monotonic()
        command = [
            sys.executable,
            str(root / "scripts/echo-guard-offline-aec-v2-lab.py"),
            str(stage),
            "--sample-rate",
            "16000",
            "--out-dir",
            str(stage_echo),
            "--inputs-preconditioned",
        ]
        for key in candidate_configs:
            command.extend(["--candidate-config", key])
        subprocess.run(command, check=True)
        write_json(
            metadata_path,
            {
                "input_fingerprint": input_fingerprint,
                "lab_code_sha256": lab_code_sha256,
                "configuration": configuration,
                "engine_runtime_sec": round(time.monotonic() - started, 3),
            },
        )
    return stage, read_json(report_path), read_json(metadata_path)


def candidate_from_audio(
    *,
    key: str,
    source: str,
    engine_native: np.ndarray,
    canonical: dict[str, Any],
    output_root: Path,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    candidate_dir = output_root / "candidates" / key
    native_path = candidate_dir / "engine_native.wav"
    asr_path = candidate_dir / "mic_for_asr.wav"
    baseline_role = canonical["baseline_asr"]
    count = min(engine_native.size, canonical["mic"].size, baseline_role.size)
    engine_native = engine_native[:count]
    canonical_asr = (
        baseline_role[:count].copy()
        if key == BASELINE
        else canonical_role_audio(
            raw_mic=baseline_role[:count],
            engine_native=engine_native,
            state_rows=canonical["state_rows"],
            replace_double_talk=key not in {COVERAGE, WEBRTC_GATE},
        )
    )
    write_wav(native_path, engine_native)
    write_wav(asr_path, canonical_asr)
    metrics = audio_metrics(
        candidate=canonical_asr,
        baseline=baseline_role[:count],
        aligned_remote=canonical["aligned_remote"][:count],
        state_rows=canonical["state_rows"],
    )
    payload = {
        "schema": "murmurmark.echo_suppression_candidate/v1",
        "candidate": key,
        "source": source,
        "timeline": {
            "schema": canonical["timeline"].get("schema"),
            "sign_convention": SIGN_CONVENTION,
            "aligned_remote_sha256": canonical["timeline"].get("aligned_remote_sha256"),
        },
        "outputs": {
            "engine_native": relative(native_path, output_root),
            "canonical_mic_for_asr": relative(asr_path, output_root),
        },
        "fingerprints": {
            "engine_native": fingerprint(native_path, output_root),
            "canonical_mic_for_asr": fingerprint(asr_path, output_root),
        },
        "metrics": metrics,
        "metadata": metadata or {},
    }
    write_json(candidate_dir / "metrics.json", payload)
    return payload


def materialize_candidates(
    *,
    session: Path,
    output_root: Path,
    canonical: dict[str, Any],
    refresh: bool,
    requested: set[str],
    benchmark_baseline: bool = True,
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    repo_root = Path(__file__).resolve().parent.parent
    rows: dict[str, dict[str, Any]] = {}
    failures: dict[str, str] = {}
    rows[BASELINE] = candidate_from_audio(
        key=BASELINE,
        source="derived/preprocess/audio/mic_role_masked_for_asr.wav",
        engine_native=canonical["local_clean"],
        canonical=canonical,
        output_root=output_root,
        metadata={
            "engine_runtime_sec": (
                benchmark_local_fir(
                    session,
                    output_root,
                    canonical["source_fingerprint"],
                    refresh,
                )
                if benchmark_baseline
                else baseline_echo_runtime(session)
            ),
            "pipeline_echo_preprocess_runtime_sec": baseline_echo_runtime(session),
        },
    )

    canonical_dir = output_root / "canonical"
    external = {
        WEBRTC: "murmurmark-aec-webrtc",
        SPEEX: "murmurmark-aec-speexdsp",
    }
    for key, helper_name in external.items():
        if key not in requested and not (key == WEBRTC and WEBRTC_GATE in requested):
            continue
        candidate_dir = output_root / "candidates" / key
        native_path = candidate_dir / "engine_native.wav"
        try:
            previous = read_json(candidate_dir / "metrics.json")
            engine_runtime = (previous.get("metadata") or {}).get("engine_runtime_sec")
            if refresh or not native_path.exists():
                candidate_dir.mkdir(parents=True, exist_ok=True)
                started = time.monotonic()
                run_external_candidate(
                    root=repo_root,
                    helper_name=helper_name,
                    mic_path=canonical_dir / "mic.wav",
                    remote_path=canonical_dir / "remote_aligned.wav",
                    output_path=native_path,
                )
                engine_runtime = round(time.monotonic() - started, 3)
            native = read_wav(native_path)
            rows[key] = candidate_from_audio(
                key=key,
                source=f"{helper_name}: canonical aligned remote, AEC only",
                engine_native=native,
                canonical=canonical,
                output_root=output_root,
                metadata={"engine_runtime_sec": engine_runtime},
            )
        except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
            failures[key] = f"{type(error).__name__}: {error}"

    if COVERAGE in requested:
        try:
            stage, report, stage_metadata = prepare_offline_stage(
                session,
                output_root,
                canonical,
                refresh,
                stage_name="_coverage_stage",
                candidate_configs=("nonlinear_tail160_remote_floor",),
            )
            stage_audio = stage / "derived/preprocess/audio"
            path = stage_audio / f"mic_clean_offline_aec_v2_{COVERAGE}.wav"
            if not path.exists():
                failures[COVERAGE] = f"offline source missing: {path}"
            else:
                rows[COVERAGE] = candidate_from_audio(
                    key=COVERAGE,
                    source=f"offline_aec_v2:{COVERAGE}:bounded_remote_floor_only",
                    engine_native=read_wav(path),
                    canonical=canonical,
                    output_root=output_root,
                    metadata={
                        "offline_report": report.get("summary"),
                        "source_candidate": COVERAGE,
                        "engine_runtime_sec": stage_metadata.get("engine_runtime_sec"),
                        "bounded_candidate_configs": ["nonlinear_tail160_remote_floor"],
                    },
                )
        except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
            failures[COVERAGE] = f"{type(error).__name__}: {error}"

    if OFFLINE in requested:
        try:
            stage, report, stage_metadata = prepare_offline_stage(
                session,
                output_root,
                canonical,
                refresh,
                stage_name="_offline_stage",
            )
            stage_audio = stage / "derived/preprocess/audio"
            candidate_rows = read_jsonl(stage / "derived/preprocess/echo/offline_aec_v2_candidates.jsonl")
            nonlinear_rows = [
                row
                for row in candidate_rows
                if str(row.get("candidate", "")).startswith("nonlinear_")
                and row.get("promotion_decision") == "shadow_candidate_passed_gates"
            ]
            if not nonlinear_rows:
                nonlinear_rows = [
                    row for row in candidate_rows if str(row.get("candidate", "")).startswith("nonlinear_")
                ]
            nonlinear_rows.sort(key=lambda row: float(row.get("score") or -1.0e9), reverse=True)
            sources: list[tuple[str, str, dict[str, Any]]] = []
            if nonlinear_rows:
                source_key = str(nonlinear_rows[0]["candidate"])
                sources.append(
                    (
                        OFFLINE,
                        source_key,
                        {
                            "offline_report": report.get("summary"),
                            "source_candidate": source_key,
                            "engine_runtime_sec": stage_metadata.get("engine_runtime_sec"),
                        },
                    )
                )
            for key, source_key, metadata in sources:
                path = stage_audio / f"mic_clean_offline_aec_v2_{source_key}.wav"
                if not path.exists():
                    failures[key] = f"offline source missing: {path}"
                    continue
                rows[key] = candidate_from_audio(
                    key=key,
                    source=f"offline_aec_v2:{source_key}",
                    engine_native=read_wav(path),
                    canonical=canonical,
                    output_root=output_root,
                    metadata=metadata,
                )
        except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
            failures[OFFLINE] = f"{type(error).__name__}: {error}"

    if WEBRTC_GATE in requested and WEBRTC in rows:
        native_metrics = rows[WEBRTC]["metrics"]
        local = native_metrics["local_only"]
        double = native_metrics["double_talk"]
        stage_safe = (
            rows[WEBRTC]["metrics"]["finite"]
            and rows[WEBRTC]["metrics"]["peak"] < 0.9999
            and float(local["energy_delta_db"]) >= -3.0
            and float(double["energy_delta_db"]) >= -6.0
        )
        if stage_safe:
            webrtc_native = read_wav(output_root / "candidates" / WEBRTC / "engine_native.wav")
            hybrid = hybrid_remote_only_audio(
                canonical["local_clean"],
                webrtc_native,
                canonical["state_rows"],
            )
            rows[WEBRTC_GATE] = candidate_from_audio(
                key=WEBRTC_GATE,
                source="remote-only gate over webrtc_aec3_aligned_v1",
                engine_native=hybrid,
                canonical=canonical,
                output_root=output_root,
                metadata={
                    "stage_safe": True,
                    "stage_metrics": native_metrics,
                    "engine_runtime_sec": (rows[WEBRTC].get("metadata") or {}).get("engine_runtime_sec"),
                },
            )
        else:
            failures[WEBRTC_GATE] = "webrtc_stage_failed_local_or_doubletalk_safety"
    return rows, failures


def tokens(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_RE.findall(text) if len(token) > 1]


def token_precision(reference: str, candidate: str) -> float:
    expected = Counter(tokens(reference))
    actual = Counter(tokens(candidate))
    total = sum(actual.values())
    if total == 0:
        return 0.0
    return sum(min(count, expected.get(token, 0)) for token, count in actual.items()) / total


def token_recall(reference: str, candidate: str) -> float:
    expected = Counter(tokens(reference))
    actual = Counter(tokens(candidate))
    total = sum(expected.values())
    if total == 0:
        return 1.0
    return sum(min(count, actual.get(token, 0)) for token, count in expected.items()) / total


def subtract_remote_tokens(text: str, remote_text: str) -> str:
    remaining = Counter(tokens(remote_text))
    local_tokens: list[str] = []
    for token in tokens(text):
        if remaining[token] > 0:
            remaining[token] -= 1
        else:
            local_tokens.append(token)
    return " ".join(local_tokens)


def merge_state_intervals(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda item: float(item.get("start", 0.0))):
        category = state_category(row)
        start = float(row.get("start", 0.0))
        end = float(row.get("end", start))
        score = float(row.get("remote_db" if category == "remote_only" else "mic_db", -120.0))
        if merged and merged[-1]["category"] == category and start <= float(merged[-1]["end"]) + 0.01:
            merged[-1]["end"] = end
            merged[-1]["score"] = max(float(merged[-1]["score"]), score)
        else:
            merged.append({"category": category, "start": start, "end": end, "score": score})
    return merged


def select_probe_windows(rows: list[dict[str, Any]], limit: int, duration: float) -> list[dict[str, Any]]:
    intervals = merge_state_intervals(rows)
    selected: list[dict[str, Any]] = []
    for category in ("remote_only", "local_only", "double_talk", "silence"):
        candidates = [row for row in intervals if row["category"] == category]
        candidates.sort(key=lambda row: (float(row["score"]), float(row["end"]) - float(row["start"])), reverse=True)
        for index, row in enumerate(candidates[:limit]):
            center = (float(row["start"]) + float(row["end"])) / 2.0
            start = max(float(row["start"]), center - 4.0)
            end = min(float(row["end"]), center + 4.0, duration)
            if end - start < 0.5:
                continue
            selected.append(
                {
                    "id": f"{category}_{index + 1:02d}",
                    "category": category,
                    "start": round(start, 3),
                    "end": round(end, 3),
                }
            )
    local_opening = next(
        (row for row in intervals if row["category"] == "local_only" and float(row["start"]) <= 15.0),
        None,
    )
    if local_opening:
        selected.append(
            {
                "id": "opening_local",
                "category": "opening_local",
                "start": round(float(local_opening["start"]), 3),
                "end": round(min(float(local_opening["end"]), float(local_opening["start"]) + 8.0), 3),
            }
        )
    unique: dict[tuple[str, float, float], dict[str, Any]] = {}
    for row in selected:
        unique[(row["category"], row["start"], row["end"])] = row
    return list(unique.values())


def risk_probe_windows(session: Path, duration: float, limit: int = 4) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    remote_forbidden = read_jsonl(
        session / "derived/audit/remote-forbidden/remote_forbidden_evidence.jsonl"
    )
    for row in remote_forbidden:
        metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
        decision = row.get("decision") if isinstance(row.get("decision"), dict) else {}
        overlap = float(metrics.get("local_fir_remote_token_overlap") or 0.0)
        if overlap < 0.25 and decision.get("action") not in {"suggest_drop", "quarantine"}:
            continue
        interval = row.get("interval") if isinstance(row.get("interval"), dict) else {}
        candidates.append(
            {
                "source": "remote_forbidden",
                "source_artifact": relative(
                    session / "derived/audit/remote-forbidden/remote_forbidden_evidence.jsonl",
                    session,
                ),
                "source_row_id": row.get("id"),
                "priority": 100 + overlap * 10,
                "start": float(interval.get("start") or 0.0),
                "end": float(interval.get("end") or 0.0),
            }
        )
    audio_review = read_jsonl(
        session / "derived/audit/audio-review-pack/audio_review_audit.jsonl"
    )
    for row in audio_review:
        classification = (
            row.get("classification") if isinstance(row.get("classification"), dict) else {}
        )
        label = str(classification.get("label") or classification.get("verdict") or "")
        if label not in {
            "remote_duplicate",
            "probable_remote_duplicate",
            "remote_leak",
            "probable_remote_leak",
            "asr_noise",
            "probable_transcript_error",
        }:
            continue
        interval = row.get("interval") if isinstance(row.get("interval"), dict) else {}
        candidates.append(
            {
                "source": "audio_review",
                "source_artifact": relative(
                    session / "derived/audit/audio-review-pack/audio_review_audit.jsonl",
                    session,
                ),
                "source_row_id": row.get("id"),
                "priority": 90 + float(classification.get("confidence") or 0.0) * 10,
                "start": float(interval.get("start") or 0.0),
                "end": float(interval.get("end") or 0.0),
            }
        )
    group_rows = read_jsonl(session / "derived/audit/group-overlaps/group_overlap_audit.jsonl")
    for row in group_rows:
        classification = (
            row.get("classification") if isinstance(row.get("classification"), dict) else {}
        )
        label = str(classification.get("label") or "")
        if label not in {"probable_duplicate", "probable_remote_leak", "probable_asr_noise"}:
            continue
        interval = row.get("interval") if isinstance(row.get("interval"), dict) else {}
        candidates.append(
            {
                "source": "group_overlap",
                "source_artifact": relative(
                    session / "derived/audit/group-overlaps/group_overlap_audit.jsonl",
                    session,
                ),
                "source_row_id": row.get("id"),
                "priority": 80 + float(classification.get("confidence") or 0.0) * 10,
                "start": float(interval.get("start") or 0.0),
                "end": float(interval.get("end") or 0.0),
            }
        )
    selected: list[dict[str, Any]] = []
    for row in sorted(candidates, key=lambda item: item["priority"], reverse=True):
        start = max(0.0, float(row["start"]) - 0.5)
        end = min(duration, float(row["end"]) + 0.5, start + 10.0)
        if end - start < 0.5:
            continue
        if any(min(end, item["end"]) - max(start, item["start"]) >= 0.5 for item in selected):
            continue
        selected.append(
            {
                "id": f"remote_risk_{len(selected) + 1:02d}",
                "category": "remote_risk",
                "start": round(start, 3),
                "end": round(end, 3),
                "source": row["source"],
                "source_artifact": row["source_artifact"],
                "source_row_id": row["source_row_id"],
            }
        )
        if len(selected) >= limit:
            break
    return selected


def evidence_interval(row: dict[str, Any]) -> tuple[float, float] | None:
    interval = row.get("interval") if isinstance(row.get("interval"), dict) else {}
    pairs = (
        (row.get("start_sec"), row.get("end_sec")),
        (interval.get("start"), interval.get("end")),
        (row.get("start"), row.get("end")),
        (row.get("parent_start_sec"), row.get("parent_end_sec")),
    )
    for start_value, end_value in pairs:
        try:
            start = float(start_value)
            end = float(end_value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(start) and math.isfinite(end) and end > start:
            return start, end
    return None


def evidence_probe_windows(
    session: Path,
    duration: float,
    limit: int = 4,
) -> list[dict[str, Any]]:
    sources = (
        (
            "protected_local",
            session / "derived/audit/local-recall/local_recall_items.jsonl",
        ),
        (
            "chronology_risk",
            session / "derived/audit/order/transcript_order_items.jsonl",
        ),
    )
    selected: list[dict[str, Any]] = []
    for category, path in sources:
        candidates: list[dict[str, Any]] = []
        for row in read_jsonl(path):
            interval = evidence_interval(row)
            if interval is None:
                continue
            confidence = float(row.get("confidence") or 0.0)
            expected_text = str(
                row.get("parent_text")
                or row.get("text")
                or row.get("utterance_text")
                or ""
            ).strip()
            priority = confidence * 100.0
            if row.get("parent_has_work_marker") is True:
                priority += 20.0
            if category == "protected_local" and expected_text:
                priority += min(len(tokens(expected_text)), 12)
            candidates.append(
                {
                    "start": interval[0],
                    "end": interval[1],
                    "priority": priority,
                    "source_row_id": row.get("item_id") or row.get("id"),
                    "expected_text": expected_text,
                    "label": row.get("label"),
                }
            )
        category_rows: list[dict[str, Any]] = []
        for row in sorted(candidates, key=lambda item: item["priority"], reverse=True):
            start = max(0.0, float(row["start"]) - 0.5)
            end = min(duration, float(row["end"]) + 0.5)
            if end - start > 16.0:
                center = (start + end) / 2.0
                start = max(0.0, center - 8.0)
                end = min(duration, center + 8.0)
            if end - start < 0.5:
                continue
            if any(
                min(end, item["end"]) - max(start, item["start"]) >= 0.5
                for item in category_rows
            ):
                continue
            category_rows.append(
                {
                    "id": f"{category}_{len(category_rows) + 1:02d}",
                    "category": category,
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "source": category.replace("_risk", "").replace("_local", ""),
                    "source_artifact": relative(path, session),
                    "source_row_id": row["source_row_id"],
                    "source_label": row["label"],
                    "reference_text": row["expected_text"] or None,
                }
            )
            if len(category_rows) >= limit:
                break
        selected.extend(category_rows)
    return selected


def transcribe_clip(model: Any, path: Path) -> str:
    segments, _ = model.transcribe(
        str(path),
        language="ru",
        beam_size=1,
        best_of=1,
        temperature=0.0,
        condition_on_previous_text=False,
        vad_filter=False,
        word_timestamps=True,
    )
    text = " ".join(segment.text.strip() for segment in segments).strip()
    if any(pattern.match(text) for pattern in KNOWN_HALLUCINATIONS):
        return ""
    return text


def run_asr_probes(
    *,
    session: Path,
    output_root: Path,
    canonical: dict[str, Any],
    candidates: dict[str, dict[str, Any]],
    model_path: Path,
    max_windows_per_class: int,
    refresh: bool,
    windows_override: list[dict[str, Any]] | None = None,
    candidate_keys: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    try:
        from faster_whisper import WhisperModel
    except ImportError as error:
        return {"status": "unavailable", "reason": f"faster_whisper_import_failed:{error}"}
    if not model_path.exists():
        return {"status": "unavailable", "reason": f"model_missing:{model_path}"}
    selected_candidates = (
        {
            key: payload
            for key, payload in candidates.items()
            if key in candidate_keys
        }
        if candidate_keys is not None
        else candidates
    )
    if BASELINE not in selected_candidates:
        raise RuntimeError("bounded ASR candidate selection must include baseline")
    candidate_fingerprints = {
        key: payload["fingerprints"]["canonical_mic_for_asr"]["sha256"]
        for key, payload in selected_candidates.items()
    }
    duration = min(canonical["mic"].size, canonical["remote"].size) / 16_000.0
    if windows_override is None:
        windows = select_probe_windows(
            canonical["state_rows"],
            max_windows_per_class,
            duration,
        )
        windows.extend(risk_probe_windows(session, duration))
        windows.extend(
            evidence_probe_windows(session, duration, max_windows_per_class)
        )
    else:
        windows = list(windows_override)
    probe_plan_fingerprint = hashlib.sha256(
        json.dumps(windows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    report_path = output_root / "asr_probe_report.json"
    existing = read_json(report_path)
    if (
        not refresh
        and existing.get("status") == "completed"
        and existing.get("model") == str(model_path)
        and existing.get("candidate_fingerprints") == candidate_fingerprints
        and existing.get("max_windows_per_class") == max_windows_per_class
        and existing.get("probe_plan_fingerprint") == probe_plan_fingerprint
    ):
        return existing
    model = WhisperModel(str(model_path), device="cpu", compute_type="int8", local_files_only=True)
    clips_dir = output_root / "asr-probes"
    rows: list[dict[str, Any]] = []
    audio_by_candidate = {
        key: read_wav(output_root / "candidates" / key / "mic_for_asr.wav")
        for key in selected_candidates
    }
    for window in windows:
        start = int(round(float(window["start"]) * 16_000))
        end = int(round(float(window["end"]) * 16_000))
        remote_path = clips_dir / f"{window['id']}_remote.wav"
        baseline_path = clips_dir / f"{window['id']}_{BASELINE}.wav"
        write_wav(remote_path, canonical["remote"][start:end])
        write_wav(baseline_path, audio_by_candidate[BASELINE][start:end])
        remote_text = transcribe_clip(model, remote_path)
        baseline_text = transcribe_clip(model, baseline_path)
        reference_text = str(window.get("reference_text") or "")
        baseline_nonremote_text = subtract_remote_tokens(baseline_text, remote_text)
        evidence_nonremote_text = subtract_remote_tokens(reference_text, remote_text)
        baseline_reference_recall = (
            round(token_recall(evidence_nonremote_text, baseline_text), 6)
            if evidence_nonremote_text
            else None
        )
        candidate_rows: dict[str, Any] = {}
        for key, audio in audio_by_candidate.items():
            path = clips_dir / f"{window['id']}_{key}.wav"
            if key != BASELINE:
                write_wav(path, audio[start:end])
            else:
                path = baseline_path
            text = baseline_text if key == BASELINE else transcribe_clip(model, path)
            candidate_rows[key] = {
                "text": text,
                "remote_token_precision": round(token_precision(remote_text, text), 6),
                "baseline_local_token_recall": round(token_recall(baseline_text, text), 6),
                "baseline_nonremote_token_recall": (
                    round(token_recall(baseline_nonremote_text, text), 6)
                    if baseline_nonremote_text
                    else None
                ),
                "evidence_token_recall": (
                    round(token_recall(evidence_nonremote_text, text), 6)
                    if evidence_nonremote_text
                    else None
                ),
                "path": relative(path, output_root),
            }
        rows.append(
            {
                **window,
                "duration_sec": round(float(window["end"]) - float(window["start"]), 3),
                "remote_text": remote_text,
                "baseline_text": baseline_text,
                "baseline_nonremote_text": baseline_nonremote_text or None,
                "reference_text": reference_text or None,
                "evidence_nonremote_text": evidence_nonremote_text or None,
                "baseline_evidence_token_recall": baseline_reference_recall,
                "candidates": candidate_rows,
            }
        )

    summaries: dict[str, Any] = {}
    for key in selected_candidates:
        remote_rows = [
            row for row in rows if row["category"] in {"remote_only", "remote_risk"}
        ]
        local_rows = [row for row in rows if row["category"] in {"local_only", "opening_local"}]
        double_rows = [row for row in rows if row["category"] == "double_talk"]
        protected_rows = [
            row
            for row in rows
            if row["category"] == "protected_local"
            and tokens(str(row.get("baseline_nonremote_text") or ""))
        ]
        protected_evidence_rows = [
            row
            for row in rows
            if row["category"] == "protected_local"
            and row.get("baseline_evidence_token_recall") is not None
            and float(row["baseline_evidence_token_recall"]) > 0.0
        ]
        chronology_rows = [
            row
            for row in rows
            if row["category"] == "chronology_risk"
            and tokens(str(row.get("baseline_nonremote_text") or ""))
        ]
        baseline_remote_seconds = sum(
            float(row["duration_sec"])
            for row in remote_rows
            if float(row["candidates"][BASELINE]["remote_token_precision"]) >= 0.25
        )
        candidate_remote_seconds = sum(
            float(row["duration_sec"])
            for row in remote_rows
            if float(row["candidates"][key]["remote_token_precision"]) >= 0.25
        )
        summaries[key] = {
            "remote_probe_seconds_baseline": round(baseline_remote_seconds, 3),
            "remote_probe_seconds_candidate": round(candidate_remote_seconds, 3),
            "remote_probe_reduction_ratio": round(
                (baseline_remote_seconds - candidate_remote_seconds) / max(baseline_remote_seconds, 1.0e-9),
                6,
            )
            if baseline_remote_seconds > 0
            else None,
            "local_token_recall": average(
                [float(row["candidates"][key]["baseline_local_token_recall"]) for row in local_rows]
            ),
            "double_talk_token_recall": average(
                [float(row["candidates"][key]["baseline_local_token_recall"]) for row in double_rows]
            ),
            "opening_token_recall": average(
                [
                    float(row["candidates"][key]["baseline_local_token_recall"])
                    for row in rows
                    if row["category"] == "opening_local"
                ]
            ),
            "protected_local_token_recall": min(
                (
                    float(row["candidates"][key]["baseline_local_token_recall"])
                    if row["candidates"][key]["baseline_nonremote_token_recall"] is None
                    else float(row["candidates"][key]["baseline_nonremote_token_recall"])
                    for row in protected_rows
                ),
                default=None,
            ),
            "protected_local_evidence_retention": min(
                (
                    min(
                        1.0,
                        float(row["candidates"][key]["evidence_token_recall"])
                        / float(row["baseline_evidence_token_recall"]),
                    )
                    for row in protected_evidence_rows
                ),
                default=None,
            ),
            "chronology_token_recall": min(
                (
                    float(row["candidates"][key]["baseline_local_token_recall"])
                    if row["candidates"][key]["baseline_nonremote_token_recall"] is None
                    else float(row["candidates"][key]["baseline_nonremote_token_recall"])
                    for row in chronology_rows
                ),
                default=None,
            ),
            "protected_local_probe_count": len(protected_rows),
            "protected_local_evidence_probe_count": len(protected_evidence_rows),
            "chronology_probe_count": len(chronology_rows),
        }
        write_json(output_root / "candidates" / key / "asr_probe.json", summaries[key])
    payload = {
        "schema": "murmurmark.echo_suppression_asr_probe/v1",
        "status": "completed",
        "model": str(model_path),
        "candidate_fingerprints": candidate_fingerprints,
        "max_windows_per_class": max_windows_per_class,
        "probe_plan_fingerprint": probe_plan_fingerprint,
        "windows": rows,
        "candidate_summaries": summaries,
    }
    write_json(report_path, payload)
    return payload


def average(values: list[float]) -> float | None:
    return None if not values else round(sum(values) / len(values), 6)


def audio_candidate_gates(
    key: str,
    payload: dict[str, Any],
    mode: str,
) -> dict[str, Any]:
    if key == BASELINE:
        return {
            "passed": True,
            "applicable": True,
            "gates": {"baseline_reference": True},
            "reasons": [],
        }
    metrics = payload["metrics"]
    baseline_peak = float(metrics.get("baseline_peak") or 0.0)
    baseline_clipped_ratio = float(metrics.get("baseline_clipped_ratio") or 0.0)
    gates = {
        "finite": metrics["finite"] is True,
        "not_clipped": float(metrics["peak"]) <= max(0.9999, baseline_peak + 0.000001)
        and float(metrics["clipped_ratio"])
        <= baseline_clipped_ratio + max(0.0000001, baseline_clipped_ratio * 0.05),
        "local_waveform_preserved": float(metrics["local_only"]["waveform_correlation_to_baseline"]) >= 0.995
        if float(metrics["local_only"]["seconds"]) > 0
        else True,
        "local_energy_preserved": float(metrics["local_only"]["energy_delta_db"]) >= -0.25
        if float(metrics["local_only"]["seconds"]) > 0
        else True,
        "doubletalk_energy_preserved": float(metrics["double_talk"]["energy_delta_db"]) >= -1.5
        if float(metrics["double_talk"]["seconds"]) > 0
        else True,
        "remote_audio_improved": (
            float(metrics["remote_only"]["remote_correlation"])
            <= max(float(metrics["remote_only"]["baseline_remote_correlation"]) * 0.95, 0.005)
            or float(metrics["remote_only"]["energy_delta_db"]) <= -1.0
        )
        if float(metrics["remote_only"]["seconds"]) > 0
        else True,
        "not_silence_cheat": float(metrics["active_protection_ratio"]) >= 0.99
        if float(metrics["protected_speech_seconds"]) > 0
        else True,
    }
    applicable = mode not in {"headphones_or_low_leak", "no_speech", "uncertain"}
    passed = applicable and all(gates.values())
    reasons = [name for name, value in gates.items() if not value]
    if not applicable and key != BASELINE:
        reasons.append(f"acoustic_mode_{mode}_uses_baseline")
    return {
        "passed": passed,
        "applicable": applicable,
        "gates": gates,
        "reasons": reasons,
    }


def runtime_gate_passes(payload: dict[str, Any]) -> bool:
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    candidate_runtime = metadata.get("engine_runtime_sec")
    baseline_runtime = metadata.get("baseline_engine_runtime_sec")
    return (
        candidate_runtime is not None
        and baseline_runtime is not None
        and float(baseline_runtime) > 0.0
        and float(candidate_runtime) / float(baseline_runtime) <= 1.25
    )


def candidate_gates(
    key: str,
    payload: dict[str, Any],
    asr_summary: dict[str, Any] | None,
    mode: str,
) -> dict[str, Any]:
    audio = audio_candidate_gates(key, payload, mode)
    if key == BASELINE:
        return audio
    gates = dict(audio["gates"])
    gates["ordinary_audio_runtime_overhead_lte_25pct"] = runtime_gate_passes(payload)
    if asr_summary is None:
        gates.update(
            {
                "asr_probe_available": False,
                "local_asr_recall": False,
                "doubletalk_asr_recall": False,
                "opening_asr_recall": False,
                "protected_local_asr_recall": False,
                "protected_local_evidence_retained": False,
                "chronology_asr_recall": False,
                "remote_asr_improved": False,
            }
        )
    else:
        local_recall = asr_summary.get("local_token_recall")
        double_recall = asr_summary.get("double_talk_token_recall")
        opening_recall = asr_summary.get("opening_token_recall")
        protected_recall = asr_summary.get("protected_local_token_recall")
        protected_evidence_retention = asr_summary.get(
            "protected_local_evidence_retention"
        )
        chronology_recall = asr_summary.get("chronology_token_recall")
        baseline_remote = float(asr_summary.get("remote_probe_seconds_baseline") or 0.0)
        reduction = asr_summary.get("remote_probe_reduction_ratio")
        gates.update(
            {
                "asr_probe_available": True,
                "local_asr_recall": local_recall is None or float(local_recall) >= 0.99,
                "doubletalk_asr_recall": double_recall is None or float(double_recall) >= 0.99,
                "opening_asr_recall": opening_recall is None or float(opening_recall) >= 0.99,
                "protected_local_asr_recall": protected_recall is None
                or float(protected_recall) >= 0.99,
                "protected_local_evidence_retained": protected_evidence_retention is None
                or float(protected_evidence_retention) >= 0.99,
                "chronology_asr_recall": chronology_recall is None
                or float(chronology_recall) >= 0.99,
                "remote_asr_improved": (
                    baseline_remote <= 0.0 or (reduction is not None and float(reduction) >= 0.10)
                ),
            }
        )
    applicable = bool(audio["applicable"])
    passed = applicable and all(gates.values())
    reasons = [name for name, value in gates.items() if not value]
    if not applicable and key != BASELINE:
        reasons.append(f"acoustic_mode_{mode}_uses_baseline")
    return {
        "passed": passed,
        "applicable": applicable,
        "gates": gates,
        "reasons": reasons,
    }


def selected_candidate(decisions: dict[str, dict[str, Any]], mode: str) -> tuple[str, str]:
    if mode in {"headphones_or_low_leak", "no_speech", "uncertain"}:
        return BASELINE, f"acoustic_mode_{mode}_baseline"
    for key in (COVERAGE, WEBRTC_GATE, WEBRTC, SPEEX, OFFLINE):
        if decisions.get(key, {}).get("passed") is True:
            return key, "first_bounded_candidate_passing_all_session_gates"
    return BASELINE, "no_candidate_passed_all_session_gates"


def raw_freeze(session: Path, source_fingerprint: dict[str, Any]) -> dict[str, Any]:
    paths = [
        session / "session.json",
        session / "audio/mic/000001.caf",
        session / "audio/remote/000001.caf",
    ]
    frozen = [fingerprint(path, session) for path in paths if path.exists()]
    evidence_paths = (
        session / "derived/audit/remote-forbidden/remote_forbidden_evidence.jsonl",
        session / "derived/audit/audio-review-pack/audio_review_audit.jsonl",
        session / "derived/audit/group-overlaps/group_overlap_audit.jsonl",
        session / "derived/audit/local-recall/local_recall_items.jsonl",
        session / "derived/audit/order/transcript_order_items.jsonl",
        session / "derived/transcript-simple/whisper-cpp/resolved/clean_dialogue.shadow_v2.json",
        session / "derived/transcript-simple/whisper-cpp/resolved/quality_report.shadow_v2.json",
    )
    return {
        "schema": "murmurmark.echo_suppression_freeze/v1",
        "session": session.name,
        "raw": frozen,
        "derived_inputs": source_fingerprint,
        "risk_evidence": [
            fingerprint(path, session)
            for path in evidence_paths
            if path.exists()
        ],
    }


def current_quality(session: Path) -> dict[str, Any]:
    paths = (
        session / "derived/transcript-simple/whisper-cpp/resolved/quality_report.local_speech_completion_v2.json",
        session / "derived/transcript-simple/whisper-cpp/resolved/quality_report.shadow_v2.json",
        session / "derived/transcript-simple/whisper-cpp/resolved/quality_report.json",
    )
    for path in paths:
        payload = read_json(path)
        if payload:
            return {
                "path": relative(path, session),
                "remote_duplicate_in_me_seconds": payload.get("remote_duplicate_in_me_seconds"),
                "needs_review_count": payload.get("needs_review_count"),
                "cross_role_overlap_gt2_seconds": payload.get("cross_role_overlap_gt2_seconds"),
                "local_only_island_recall": payload.get("local_only_island_recall"),
                "golden_phrase_fail_count": payload.get("golden_phrase_fail_count"),
                "unrepaired_long_mic_crossings_count": payload.get("unrepaired_long_mic_crossings_count"),
            }
    return {}


def baseline_echo_runtime(session: Path) -> float | None:
    report = read_json(session / "derived/pipeline-run/pipeline_run_report.json")
    steps = report.get("steps") if isinstance(report.get("steps"), list) else []
    for row in steps:
        if row.get("name") == "echo_preprocess" and row.get("status") in {"passed", "passed_with_warnings"}:
            try:
                return float(row.get("duration_sec"))
            except (TypeError, ValueError):
                return None
    return None


def benchmark_local_fir(
    session: Path,
    output_root: Path,
    source_fingerprint: dict[str, Any],
    refresh: bool,
) -> float | None:
    repo_root = Path(__file__).resolve().parent.parent
    benchmark_dir = output_root / "benchmark"
    metadata_path = benchmark_dir / "local_fir_runtime.json"
    script_path = repo_root / "scripts/echo-guard-session-local-fir.py"
    cache_key = {
        "source_fingerprint": source_fingerprint,
        "script_sha256": sha256(script_path),
    }
    existing = read_json(metadata_path)
    if (
        not refresh
        and existing.get("cache_key") == cache_key
        and existing.get("status") == "completed"
    ):
        try:
            return float(existing["runtime_sec"])
        except (KeyError, TypeError, ValueError):
            pass
    benchmark_dir.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(script_path),
        str(session),
        "--output-clean",
        str(benchmark_dir / "unused_clean.wav"),
        "--output-echo",
        str(benchmark_dir / "unused_echo.wav"),
        "--report",
        str(benchmark_dir / "local_fir_report.json"),
        "--segments",
        str(benchmark_dir / "unused_segments.jsonl"),
        "--metrics-only",
    ]
    started = time.monotonic()
    try:
        subprocess.run(command, check=True)
    except (OSError, subprocess.CalledProcessError) as error:
        write_json(
            metadata_path,
            {
                "status": "failed",
                "cache_key": cache_key,
                "reason": f"{type(error).__name__}:{error}",
            },
        )
        return None
    runtime = round(time.monotonic() - started, 3)
    write_json(
        metadata_path,
        {
            "status": "completed",
            "cache_key": cache_key,
            "runtime_sec": runtime,
        },
    )
    return runtime


def safe_link(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.unlink(missing_ok=True)
    destination.symlink_to(source.resolve())


def copy_remote_asr_cache(session: Path, stage: Path) -> str | None:
    source = session / "derived/transcript-simple/whisper-cpp/raw"
    destination = stage / "derived/transcript-simple/whisper-cpp/raw"
    if not source.exists():
        return None
    destination.mkdir(parents=True, exist_ok=True)
    for path in source.glob("remote.*"):
        if path.is_file():
            shutil.copy2(path, destination / path.name)
    chunks = source / "chunks/remote"
    if chunks.exists():
        safe_link(chunks, destination / "chunks/remote")
    metadata = read_json(source / "remote.meta.json")
    prompt = metadata.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return None
    prompt_path = stage / "derived/preprocess/echo-promotion-v1/full-shadow-prompt.txt"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(prompt, encoding="utf-8")
    return str(prompt_path)


def dialogue_remote_fingerprint(path: Path) -> str | None:
    payload = read_json(path)
    utterances = payload.get("utterances") if isinstance(payload.get("utterances"), list) else []
    if not utterances:
        return None
    rows = [
        {
            "start": row.get("start_ms", row.get("start")),
            "end": row.get("end_ms", row.get("end")),
            "text": row.get("text"),
        }
        for row in utterances
        if str(row.get("role") or "").lower() in {"colleagues", "remote"}
    ]
    return hashlib.sha256(
        json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def evidence_ids_valid(stage: Path) -> bool:
    synthesis = read_json(stage / "derived/synthesis-simple/extractive/evidence_notes.json")
    selected = synthesis.get("selected") if isinstance(synthesis.get("selected"), dict) else {}
    dialogue = read_json(
        stage / "derived/transcript-simple/whisper-cpp/resolved/clean_dialogue.shadow_v2.json"
    )
    utterances = dialogue.get("utterances") if isinstance(dialogue.get("utterances"), list) else []
    valid_ids = {str(row.get("id")) for row in utterances}
    referenced: set[str] = set()

    def visit(value: Any, parent_key: str = "") -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                visit(item, str(key))
        elif isinstance(value, list):
            if "utterance" in parent_key and all(not isinstance(item, (dict, list)) for item in value):
                referenced.update(str(item) for item in value)
            else:
                for item in value:
                    visit(item, parent_key)
        elif "utterance" in parent_key and value is not None:
            referenced.add(str(value))

    visit(selected)
    return referenced.issubset(valid_ids)


def verdict_rank(value: Any) -> int:
    return {
        "good": 0,
        "usable_with_review": 1,
        "risky": 2,
        "failed": 3,
    }.get(str(value or ""), 4)


def full_shadow_stage(
    *,
    session: Path,
    output_root: Path,
    candidate: str,
    whisper_model: Path,
    refresh: bool,
) -> dict[str, Any]:
    repo_root = Path(__file__).resolve().parent.parent
    candidate_dir = output_root / "candidates" / candidate
    candidate_audio = candidate_dir / "mic_for_asr.wav"
    stage = candidate_dir / "full-shadow-session"
    report_path = candidate_dir / "full_shadow_report.json"
    input_sha = sha256(candidate_audio)
    existing = read_json(report_path)
    if (
        not refresh
        and existing.get("input_sha256") == input_sha
        and existing.get("status") == "completed"
    ):
        return existing

    if stage.exists():
        shutil.rmtree(stage)
    (stage / "audio/mic").mkdir(parents=True, exist_ok=True)
    (stage / "audio/remote").mkdir(parents=True, exist_ok=True)
    safe_link(candidate_audio, stage / "audio/mic/000001.wav")
    remote_source = session / "audio/remote/000001.caf"
    safe_link(remote_source, stage / "audio/remote/000001.caf")
    manifest = read_json(session / "session.json")
    mic_rows = ((manifest.get("files") or {}).get("mic") or [])
    if mic_rows:
        mic_rows[0]["path"] = "audio/mic/000001.wav"
        mic_rows[0]["channels"] = 1
        mic_rows[0]["sample_rate"] = 16_000
        mic_rows[0]["bytes"] = candidate_audio.stat().st_size
        mic_rows[0]["frames"] = read_wav(candidate_audio).size
    write_json(stage / "session.json", manifest)
    for optional in ("events.jsonl", "pipeline_job.json"):
        source = session / optional
        if source.exists():
            shutil.copy2(source, stage / optional)

    stage_audio = stage / "derived/preprocess/audio"
    stage_echo = stage / "derived/preprocess/echo"
    stage_audio.mkdir(parents=True, exist_ok=True)
    stage_echo.mkdir(parents=True, exist_ok=True)
    safe_link(candidate_audio, stage_audio / "mic_role_masked_for_asr.wav")
    safe_link(candidate_audio, stage_audio / "mic_clean_local_fir.wav")
    safe_link(output_root / "canonical/mic.wav", stage_audio / "mic_raw_for_asr.wav")
    safe_link(output_root / "canonical/remote_aligned.wav", stage_audio / "remote_for_aec.wav")
    shutil.copy2(
        session / "derived/preprocess/echo/speaker_state.jsonl",
        stage_echo / "speaker_state.jsonl",
    )
    prompt_path = copy_remote_asr_cache(session, stage)
    murmurmark_bin = repo_root / ".build/debug/murmurmark"
    if not murmurmark_bin.exists():
        resolved = shutil.which("murmurmark")
        if not resolved:
            raise RuntimeError("murmurmark executable not found for full shadow")
        murmurmark_bin = Path(resolved)
    command = [
        sys.executable,
        str(repo_root / "scripts/transcribe-simple-whispercpp.py"),
        str(stage),
        "--model",
        str(whisper_model),
        "--language",
        "ru",
        "--repair-profile",
        "shadow_v2",
        "--track-workers",
        "2",
        "--threads",
        "6",
        "--micro-asr-workers",
        "4",
        "--murmurmark-bin",
        str(murmurmark_bin),
    ]
    if prompt_path:
        command.extend(["--prompt-file", prompt_path])
    started = time.monotonic()
    subprocess.run(command, check=True)
    transcription_runtime = time.monotonic() - started
    subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts/synthesize-simple-extractive.py"),
            str(stage),
            "--transcript-profile",
            "shadow_v2",
        ],
        check=True,
    )

    candidate_quality_path = (
        stage / "derived/transcript-simple/whisper-cpp/resolved/quality_report.shadow_v2.json"
    )
    candidate_quality = read_json(candidate_quality_path)
    baseline_path = (
        session / "derived/transcript-simple/whisper-cpp/resolved/quality_report.shadow_v2.json"
    )
    baseline_quality = read_json(baseline_path)
    baseline_remote_seconds = float(baseline_quality.get("remote_duplicate_in_me_seconds") or 0.0)
    candidate_remote_seconds = float(candidate_quality.get("remote_duplicate_in_me_seconds") or 0.0)
    baseline_review_seconds = baseline_remote_seconds
    candidate_review_seconds = candidate_remote_seconds
    baseline_dialogue = (
        session / "derived/transcript-simple/whisper-cpp/resolved/clean_dialogue.shadow_v2.json"
    )
    candidate_dialogue = (
        stage / "derived/transcript-simple/whisper-cpp/resolved/clean_dialogue.shadow_v2.json"
    )
    transcript_path = (
        stage / "derived/transcript-simple/whisper-cpp/resolved/transcript.shadow_v2.md"
    )
    overlaps_path = (
        stage / "derived/transcript-simple/whisper-cpp/resolved/overlaps.shadow_v2.json"
    )
    notes_path = stage / "derived/synthesis-simple/extractive/notes.md"
    verdict_path = stage / "derived/synthesis-simple/extractive/quality_verdict.json"
    baseline_verdict_path = (
        session / "derived/synthesis-simple/extractive/quality_verdict.json"
    )
    candidate_verdict = read_json(verdict_path)
    baseline_verdict = read_json(baseline_verdict_path)
    remote_unchanged = dialogue_remote_fingerprint(baseline_dialogue) == dialogue_remote_fingerprint(
        candidate_dialogue
    )
    gates = {
        "unrepaired_crossings_zero": int(candidate_quality.get("unrepaired_long_mic_crossings_count") or 0) == 0,
        "golden_failures_zero": int(candidate_quality.get("golden_phrase_fail_count") or 0) == 0,
        "local_recall_preserved": float(candidate_quality.get("local_only_island_recall") or 0.0)
        >= float(baseline_quality.get("local_only_island_recall") or 0.0),
        "chronology_not_worse": float(candidate_quality.get("cross_role_overlap_gt2_seconds") or 0.0)
        <= float(baseline_quality.get("cross_role_overlap_gt2_seconds") or 0.0),
        "remote_content_unchanged": remote_unchanged,
        "notes_evidence_valid": evidence_ids_valid(stage),
        "verdict_not_worse": baseline_verdict.get("verdict")
        in {"good", "usable_with_review", "risky", "failed"}
        and verdict_rank(candidate_verdict.get("verdict"))
        <= verdict_rank(baseline_verdict.get("verdict")),
        "guarded_export_inputs_complete": all(
            path.exists()
            for path in (candidate_dialogue, transcript_path, notes_path, verdict_path)
        ),
    }
    output_fingerprints = {
        name: fingerprint(path, session)
        for name, path in {
            "dialogue": candidate_dialogue,
            "transcript": transcript_path,
            "quality": candidate_quality_path,
            "overlaps": overlaps_path,
            "notes": notes_path,
            "verdict": verdict_path,
        }.items()
        if path.exists()
    }
    replay_verified = (
        existing.get("output_fingerprints") == output_fingerprints
        if existing.get("input_sha256") == input_sha and existing.get("status") == "completed"
        else None
    )
    payload = {
        "schema": "murmurmark.echo_suppression_full_shadow/v1",
        "status": "completed",
        "candidate": candidate,
        "input_sha256": input_sha,
        "stage": relative(stage, session),
        "outputs": {
            "transcript": relative(transcript_path, session),
            "quality": relative(candidate_quality_path, session),
            "overlaps": relative(overlaps_path, session),
            "notes": relative(notes_path, session),
            "verdict": relative(verdict_path, session),
        },
        "output_fingerprints": output_fingerprints,
        "determinism": {
            "previous_completed_same_input": replay_verified is not None,
            "replay_verified": replay_verified,
        },
        "metrics": {
            "remote_duplicate_in_me_seconds_baseline": round(baseline_remote_seconds, 3),
            "remote_duplicate_in_me_seconds_candidate": round(candidate_remote_seconds, 3),
            "remote_duplicate_reduction_ratio": round(
                (baseline_remote_seconds - candidate_remote_seconds)
                / max(baseline_remote_seconds, 1.0e-9),
                6,
            )
            if baseline_remote_seconds > 0
            else None,
            "remote_caused_review_seconds_baseline": round(baseline_review_seconds, 3),
            "remote_caused_review_seconds_candidate": round(candidate_review_seconds, 3),
            "remote_caused_review_reduction_ratio": round(
                (baseline_review_seconds - candidate_review_seconds)
                / max(baseline_review_seconds, 1.0e-9),
                6,
            )
            if baseline_review_seconds > 0
            else None,
            "local_only_island_recall_baseline": baseline_quality.get("local_only_island_recall"),
            "local_only_island_recall_candidate": candidate_quality.get("local_only_island_recall"),
            "cross_role_overlap_gt2_seconds_baseline": baseline_quality.get("cross_role_overlap_gt2_seconds"),
            "cross_role_overlap_gt2_seconds_candidate": candidate_quality.get("cross_role_overlap_gt2_seconds"),
            "needs_review_count_baseline": baseline_quality.get("needs_review_count"),
            "needs_review_count_candidate": candidate_quality.get("needs_review_count"),
            "transcription_runtime_sec": round(transcription_runtime, 3),
            "baseline_verdict": baseline_verdict.get("verdict"),
            "candidate_verdict": candidate_verdict.get("verdict"),
        },
        "gates": gates,
        "passed": all(gates.values()),
    }
    write_json(report_path, payload)
    return payload


def render_session(payload: dict[str, Any]) -> str:
    lines = [
        "# Echo Suppression Promotion v1",
        "",
        f"- Session: `{payload['session']}`",
        f"- Acoustic mode: `{payload['acoustic_mode']['mode']}`",
        f"- Selected shadow candidate: `{payload['selection']['candidate']}`",
        f"- Reason: `{payload['selection']['reason']}`",
        f"- Authoritative mic changed: `false`",
        "",
        "## Candidates",
        "",
        "| Candidate | Session gate | Remote corr | Local corr | Double-talk dB | ASR remote reduction |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for key in CANDIDATE_ORDER:
        candidate = payload["candidates"].get(key)
        if not candidate:
            continue
        metrics = candidate["metrics"]
        asr = candidate.get("asr_probe") or {}
        decision = payload["decisions"].get(key) or {}
        lines.append(
            f"| `{key}` | `{decision.get('passed')}` | "
            f"{metrics['remote_only']['remote_correlation']:.4f} | "
            f"{metrics['local_only']['waveform_correlation_to_baseline']:.4f} | "
            f"{metrics['double_talk']['energy_delta_db']:.2f} | "
            f"{format_number(asr.get('remote_probe_reduction_ratio'))} |"
        )
    if payload["failures"]:
        lines.extend(["", "## Fail-open Candidates", ""])
        for key, reason in sorted(payload["failures"].items()):
            lines.append(f"- `{key}`: `{reason}`")
    if payload.get("skipped_candidates"):
        lines.extend(["", "## Not Applicable Candidates", ""])
        for key, reason in sorted(payload["skipped_candidates"].items()):
            lines.append(f"- `{key}`: `{reason}`")
    lines.extend(
        [
            "",
            "The session result is shadow evidence. Production selection remains controlled by the frozen corpus policy.",
        ]
    )
    return "\n".join(lines) + "\n"


def format_number(value: Any) -> str:
    try:
        return f"{float(value):.3f}"
    except (TypeError, ValueError):
        return ""


def run_session(args: argparse.Namespace) -> dict[str, Any]:
    started = time.monotonic()
    session = args.session.resolve()
    output_root = session / "derived/preprocess/echo-promotion-v1"
    output_root.mkdir(parents=True, exist_ok=True)
    previous_report = read_json(output_root / "session_report.json")
    canonical = canonical_inputs(session, output_root, bool(args.refresh))
    mode_info = acoustic_mode(session)
    mode = str(mode_info.get("mode") or "uncertain")
    requested = set(args.candidate or CANDIDATE_ORDER[1:])
    skipped_candidates: dict[str, str] = {}
    if mode in {"headphones_or_low_leak", "no_speech", "uncertain"}:
        skipped_candidates = {
            key: f"acoustic_mode_{mode}_uses_baseline"
            for key in sorted(requested)
        }
        requested = set()
    candidates, failures = materialize_candidates(
        session=session,
        output_root=output_root,
        canonical=canonical,
        refresh=bool(args.refresh),
        requested=requested,
    )
    baseline_runtime = (
        candidates.get(BASELINE, {}).get("metadata", {}).get("engine_runtime_sec")
    )
    for key, candidate in candidates.items():
        if key == BASELINE:
            continue
        candidate.setdefault("metadata", {})["baseline_engine_runtime_sec"] = baseline_runtime
        write_json(output_root / "candidates" / key / "metrics.json", candidate)
    probe_report: dict[str, Any] = {}
    if args.asr_probes and requested:
        probe_candidates = {
            key: candidate
            for key, candidate in candidates.items()
            if key == BASELINE
            or (
                audio_candidate_gates(key, candidate, mode)["passed"] is True
                and runtime_gate_passes(candidate)
            )
        }
        if any(key != BASELINE for key in probe_candidates):
            model_path = args.faster_whisper_model or Path(
                os.environ.get(
                    "MURMURMARK_FASTER_WHISPER_MODEL",
                    str(Path.home() / ".local/share/murmurmark/models/faster-whisper/large-v3"),
                )
            )
            probe_report = run_asr_probes(
                session=session,
                output_root=output_root,
                canonical=canonical,
                candidates=probe_candidates,
                model_path=model_path.expanduser(),
                max_windows_per_class=int(args.max_windows_per_class),
                refresh=bool(args.refresh),
            )
        else:
            probe_report = {
                "status": "not_applicable",
                "reason": "no_nonbaseline_candidate_passed_audio_gates",
                "candidate_summaries": {},
            }
    elif args.asr_probes:
        probe_report = {
            "status": "not_applicable",
            "reason": f"acoustic_mode_{mode}_uses_baseline",
            "candidate_summaries": {},
        }
    summaries = probe_report.get("candidate_summaries") if isinstance(probe_report, dict) else {}
    decisions: dict[str, dict[str, Any]] = {}
    for key, candidate in candidates.items():
        asr_summary = summaries.get(key) if isinstance(summaries, dict) else None
        candidate["asr_probe"] = asr_summary
        decisions[key] = candidate_gates(key, candidate, asr_summary, mode)
        write_json(output_root / "candidates" / key / "decision.json", decisions[key])
    selected, reason = selected_candidate(decisions, mode)
    full_shadow: dict[str, Any] = {"status": "not_requested"}
    if args.full_shadow:
        if selected == BASELINE:
            full_shadow = {
                "status": "not_applicable",
                "reason": "no_nonbaseline_candidate_passed_cheap_and_asr_gates",
            }
        else:
            whisper_model = (
                args.whisper_model
                or Path.home()
                / ".local/share/murmurmark/models/whisper.cpp/ggml-large-v3-q5_0.bin"
            ).expanduser()
            if not whisper_model.exists():
                full_shadow = {
                    "status": "unavailable",
                    "reason": f"whisper_model_missing:{whisper_model}",
                }
            else:
                try:
                    full_shadow = full_shadow_stage(
                        session=session,
                        output_root=output_root,
                        candidate=selected,
                        whisper_model=whisper_model,
                        refresh=bool(args.refresh),
                    )
                except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
                    full_shadow = {
                        "status": "failed_open",
                        "reason": f"{type(error).__name__}:{error}",
                    }
            candidates[selected]["full_shadow"] = full_shadow
            write_json(output_root / "candidates" / selected / "metrics.json", candidates[selected])
    selected_path = output_root / "candidates" / selected / "mic_for_asr.wav"
    selected_copy = output_root / "selected_mic_for_asr.wav"
    shutil.copy2(selected_path, selected_copy)
    freeze = raw_freeze(session, canonical["source_fingerprint"])
    write_json(output_root / "freeze_manifest.json", freeze)
    payload = {
        "schema": SESSION_SCHEMA,
        "generator": {"name": "echo-suppression-promotion-v1", "version": VERSION},
        "session": session.name,
        "profile": PROFILE,
        "mode": "shadow_only",
        "authoritative_pipeline_changed": False,
        "timeline_contract": canonical["timeline"],
        "acoustic_mode": mode_info,
        "freeze": freeze,
        "baseline_quality": current_quality(session),
        "candidates": candidates,
        "decisions": decisions,
        "failures": failures,
        "skipped_candidates": skipped_candidates,
        "selection": {
            "candidate": selected,
            "reason": reason,
            "path": relative(selected_copy, session),
            "fingerprint": fingerprint(selected_copy, session),
        },
        "asr_probe": {
            "status": probe_report.get("status", "not_requested"),
            "path": "derived/preprocess/echo-promotion-v1/asr_probe_report.json"
            if probe_report
            else None,
        },
        "full_shadow": full_shadow,
        "runtime_sec": round(time.monotonic() - started, 3),
    }
    decision_basis = {
        "freeze": freeze,
        "timeline_sha256": sha256(output_root / "canonical/timeline_contract.json"),
        "candidate_fingerprints": {
            key: value["fingerprints"]
            for key, value in sorted(candidates.items())
        },
        "decisions": decisions,
        "selection": payload["selection"]["candidate"],
    }
    payload["decision_fingerprint"] = hashlib.sha256(
        json.dumps(decision_basis, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    same_frozen_inputs = (
        previous_report.get("freeze") == freeze
        if previous_report.get("schema") == SESSION_SCHEMA
        else False
    )
    payload["determinism"] = {
        "previous_report_present": previous_report.get("schema") == SESSION_SCHEMA,
        "same_frozen_inputs": same_frozen_inputs,
        "previous_decision_fingerprint": previous_report.get("decision_fingerprint"),
        "replay_verified": (
            previous_report.get("decision_fingerprint") == payload["decision_fingerprint"]
            if same_frozen_inputs
            else None
        ),
    }
    write_json(output_root / "session_report.json", payload)
    (output_root / "session_report.md").write_text(render_session(payload), encoding="utf-8")
    print(f"echo_promotion_session_report: {output_root / 'session_report.json'}")
    print(f"selected_shadow_candidate: {selected}")
    print("authoritative_pipeline_changed: false")
    return payload


def corpus_candidate_metrics(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    available = [row for row in rows if key in row.get("candidates", {})]
    applicable = [
        row
        for row in available
        if row.get("decisions", {}).get(key, {}).get("applicable") is True
    ]
    passed = [
        row
        for row in applicable
        if row.get("decisions", {}).get(key, {}).get("passed") is True
    ]
    baseline_remote = sum(
        float((row["candidates"][key].get("asr_probe") or {}).get("remote_probe_seconds_baseline") or 0.0)
        for row in applicable
    )
    candidate_remote = sum(
        float((row["candidates"][key].get("asr_probe") or {}).get("remote_probe_seconds_candidate") or 0.0)
        for row in applicable
    )
    local_recalls = [
        float(value)
        for row in applicable
        if (value := (row["candidates"][key].get("asr_probe") or {}).get("local_token_recall")) is not None
    ]
    double_recalls = [
        float(value)
        for row in applicable
        if (value := (row["candidates"][key].get("asr_probe") or {}).get("double_talk_token_recall")) is not None
    ]
    protected_recalls = [
        float(value)
        for row in applicable
        if (value := (row["candidates"][key].get("asr_probe") or {}).get("protected_local_token_recall"))
        is not None
    ]
    protected_evidence_retentions = [
        float(value)
        for row in applicable
        if (
            value := (row["candidates"][key].get("asr_probe") or {}).get(
                "protected_local_evidence_retention"
            )
        )
        is not None
    ]
    chronology_recalls = [
        float(value)
        for row in applicable
        if (value := (row["candidates"][key].get("asr_probe") or {}).get("chronology_token_recall"))
        is not None
    ]
    full_shadow = [
        row["candidates"][key]["full_shadow"]
        for row in applicable
        if isinstance(row["candidates"][key].get("full_shadow"), dict)
        and row["candidates"][key]["full_shadow"].get("status") == "completed"
    ]
    full_baseline_remote = sum(
        float((item.get("metrics") or {}).get("remote_duplicate_in_me_seconds_baseline") or 0.0)
        for item in full_shadow
    )
    full_candidate_remote = sum(
        float((item.get("metrics") or {}).get("remote_duplicate_in_me_seconds_candidate") or 0.0)
        for item in full_shadow
    )
    full_baseline_review = sum(
        float((item.get("metrics") or {}).get("remote_caused_review_seconds_baseline") or 0.0)
        for item in full_shadow
    )
    full_candidate_review = sum(
        float((item.get("metrics") or {}).get("remote_caused_review_seconds_candidate") or 0.0)
        for item in full_shadow
    )
    runtime_ratios: list[float] = []
    for row in applicable:
        baseline_runtime = (row["candidates"].get(BASELINE, {}).get("metadata") or {}).get(
            "engine_runtime_sec"
        )
        candidate_runtime = (row["candidates"][key].get("metadata") or {}).get("engine_runtime_sec")
        if baseline_runtime is None or candidate_runtime is None or float(baseline_runtime) <= 0:
            continue
        runtime_ratios.append(float(candidate_runtime) / float(baseline_runtime))
    return {
        "available_sessions": len(available),
        "applicable_sessions": len(applicable),
        "passing_sessions": len(passed),
        "all_applicable_sessions_passed": bool(applicable) and len(passed) == len(applicable),
        "remote_probe_seconds_baseline": round(baseline_remote, 3),
        "remote_probe_seconds_candidate": round(candidate_remote, 3),
        "remote_probe_reduction_ratio": round(
            (baseline_remote - candidate_remote) / max(baseline_remote, 1.0e-9),
            6,
        )
        if baseline_remote > 0
        else None,
        "confirmed_local_recall": min(local_recalls) if local_recalls else None,
        "double_talk_recall": min(double_recalls) if double_recalls else None,
        "protected_local_recall": min(protected_recalls) if protected_recalls else None,
        "protected_local_evidence_retention": min(protected_evidence_retentions)
        if protected_evidence_retentions
        else None,
        "chronology_probe_recall": min(chronology_recalls) if chronology_recalls else None,
        "full_shadow_sessions": len(full_shadow),
        "full_shadow_all_passed": bool(full_shadow) and all(item.get("passed") is True for item in full_shadow),
        "full_shadow_deterministic": bool(full_shadow)
        and all((item.get("determinism") or {}).get("replay_verified") is True for item in full_shadow),
        "full_shadow_remote_duplicate_reduction_ratio": round(
            (full_baseline_remote - full_candidate_remote) / max(full_baseline_remote, 1.0e-9),
            6,
        )
        if full_baseline_remote > 0
        else None,
        "remote_caused_review_reduction_ratio": round(
            (full_baseline_review - full_candidate_review) / max(full_baseline_review, 1.0e-9),
            6,
        )
        if full_baseline_review > 0
        else None,
        "max_audio_runtime_ratio": max(runtime_ratios) if runtime_ratios else None,
        "failed_sessions": [
            {
                "session": row.get("session"),
                "reasons": row.get("decisions", {}).get(key, {}).get("reasons") or [],
            }
            for row in applicable
            if row.get("decisions", {}).get(key, {}).get("passed") is not True
        ],
    }


def render_corpus(payload: dict[str, Any]) -> str:
    lines = [
        "# Echo Suppression Promotion v1 Corpus Report",
        "",
        f"- Decision: `{payload['promotion']['decision']}`",
        f"- Reason: `{payload['promotion']['reason']}`",
        f"- Candidate: `{payload['promotion'].get('candidate')}`",
        f"- Frozen sessions: `{payload['summary']['sessions']}`",
        "",
        "## Candidate Matrix",
        "",
        "| Candidate | Applicable | Passing | Remote probe reduction | Local recall | Double-talk recall | Full shadow |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for key, metrics in payload["candidate_metrics"].items():
        lines.append(
            f"| `{key}` | {metrics['applicable_sessions']} | {metrics['passing_sessions']} | "
            f"{format_number(metrics['remote_probe_reduction_ratio'])} | "
            f"{format_number(metrics['confirmed_local_recall'])} | "
            f"{format_number(metrics['double_talk_recall'])} | {metrics['full_shadow_sessions']} |"
        )
        for failure in metrics.get("failed_sessions") or []:
            reasons = ", ".join(failure.get("reasons") or [])
            lines.append(f"  - `{failure.get('session')}`: `{reasons}`")
    lines.extend(["", "## Gate", ""])
    for key, value in payload["promotion"]["gates"].items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(
        [
            "",
            "A DO_NOT_PROMOTE result keeps `local_fir` as the production policy. The next audio hypothesis is Neural Residual Echo Suppression v1.",
        ]
    )
    return "\n".join(lines) + "\n"


def run_corpus(args: argparse.Namespace) -> dict[str, Any]:
    sessions = [path.resolve() for path in args.sessions]
    rows: list[dict[str, Any]] = []
    for session in sessions:
        if args.run:
            session_args = argparse.Namespace(
                session=session,
                refresh=args.refresh,
                asr_probes=args.asr_probes,
                full_shadow=args.full_shadow,
                max_windows_per_class=2,
                faster_whisper_model=args.faster_whisper_model,
                whisper_model=args.whisper_model,
                candidate=None,
            )
            rows.append(run_session(session_args))
        else:
            payload = read_json(session / "derived/preprocess/echo-promotion-v1/session_report.json")
            if payload:
                rows.append(payload)
            else:
                rows.append({"session": session.name, "status": "missing_session_report"})
    candidate_metrics = {
        key: corpus_candidate_metrics(rows, key)
        for key in CANDIDATE_ORDER[1:]
    }
    best_observed = max(
        CANDIDATE_ORDER[1:],
        key=lambda key: (
            int(candidate_metrics[key]["applicable_sessions"]),
            float(candidate_metrics[key]["remote_probe_reduction_ratio"] or -1.0),
            -CANDIDATE_ORDER.index(key),
        ),
    )
    eligible: list[tuple[str, dict[str, Any]]] = []
    for key in CANDIDATE_ORDER[1:]:
        metrics = candidate_metrics[key]
        if (
            metrics["all_applicable_sessions_passed"]
            and metrics["remote_probe_reduction_ratio"] is not None
            and float(metrics["remote_probe_reduction_ratio"]) >= 0.25
            and metrics["confirmed_local_recall"] is not None
            and float(metrics["confirmed_local_recall"]) >= 0.99
            and (metrics["double_talk_recall"] is None or float(metrics["double_talk_recall"]) >= 0.99)
            and (
                metrics["protected_local_recall"] is None
                or float(metrics["protected_local_recall"]) >= 0.99
            )
            and (
                metrics["protected_local_evidence_retention"] is None
                or float(metrics["protected_local_evidence_retention"]) >= 0.99
            )
            and (
                metrics["chronology_probe_recall"] is None
                or float(metrics["chronology_probe_recall"]) >= 0.99
            )
        ):
            eligible.append((key, metrics))
    eligible.sort(
        key=lambda item: (
            -float(item[1]["remote_probe_reduction_ratio"] or 0.0),
            CANDIDATE_ORDER.index(item[0]),
        )
    )
    finalist = eligible[0][0] if eligible else None
    evaluation_candidate = finalist or best_observed
    evaluation_metrics = candidate_metrics[evaluation_candidate]
    gates = {
        "all_session_reports_present": len(rows) == len(sessions)
        and all(row.get("schema") == SESSION_SCHEMA for row in rows),
        "candidate_passes_all_applicable_sessions": finalist is not None,
        "asr_remote_duplicate_or_leak_seconds_down_25pct": float(
            evaluation_metrics.get("remote_probe_reduction_ratio") or 0.0
        )
        >= 0.25,
        "remote_caused_review_seconds_down_15pct": finalist is not None
        and evaluation_metrics.get("remote_caused_review_reduction_ratio") is not None
        and float(evaluation_metrics["remote_caused_review_reduction_ratio"]) >= 0.15,
        "confirmed_local_recall_99pct": float(
            evaluation_metrics.get("confirmed_local_recall") or 0.0
        )
        >= 0.99,
        "doubletalk_recall_no_regression": (
            evaluation_metrics.get("double_talk_recall") is None
            or float(evaluation_metrics["double_talk_recall"]) >= 0.99
        ),
        "protected_local_recall_no_regression": (
            evaluation_metrics.get("protected_local_recall") is None
            or float(evaluation_metrics["protected_local_recall"]) >= 0.99
        )
        and (
            evaluation_metrics.get("protected_local_evidence_retention") is None
            or float(evaluation_metrics["protected_local_evidence_retention"]) >= 0.99
        ),
        "chronology_probe_recall_no_regression": (
            evaluation_metrics.get("chronology_probe_recall") is None
            or float(evaluation_metrics["chronology_probe_recall"]) >= 0.99
        ),
        "full_shadow_no_regression_available": finalist is not None
        and int(evaluation_metrics.get("full_shadow_sessions") or 0)
        == int(evaluation_metrics.get("applicable_sessions") or 0)
        and evaluation_metrics.get("full_shadow_all_passed") is True
        and evaluation_metrics.get("full_shadow_deterministic") is True,
        "ordinary_runtime_overhead_lte_25pct": evaluation_metrics.get(
            "max_audio_runtime_ratio"
        )
        is not None
        and float(evaluation_metrics["max_audio_runtime_ratio"]) <= 1.25,
        "deterministic_replay": all(
            bool(row.get("decision_fingerprint"))
            and (row.get("determinism") or {}).get("replay_verified") is True
            for row in rows
        ),
    }
    promote = all(gates.values())
    failed_gates = [key for key, value in gates.items() if not value]
    decision = "PROMOTE_ECHO_SUPPRESSION_V1" if promote else "DO_NOT_PROMOTE"
    if promote:
        reason = "all_promotion_gates_passed"
    elif finalist is None:
        best_metrics = candidate_metrics[best_observed]
        reason = (
            "no_candidate_passed_all_applicable_sessions:"
            f"{best_observed}_passed_{best_metrics['passing_sessions']}"
            f"_of_{best_metrics['applicable_sessions']}"
        )
    else:
        reason = ",".join(failed_gates)
    freeze_rows = [
        {
            "session": row.get("session"),
            "freeze": row.get("freeze"),
            "decision_fingerprint": row.get("decision_fingerprint"),
        }
        for row in rows
    ]
    payload = {
        "schema": CORPUS_SCHEMA,
        "generator": {"name": "echo-suppression-promotion-v1", "version": VERSION},
        "profile": PROFILE,
        "summary": {
            "sessions": len(sessions),
            "reports_found": sum(1 for row in rows if row.get("schema") == SESSION_SCHEMA),
            "acoustic_modes": dict(
                sorted(Counter(str((row.get("acoustic_mode") or {}).get("mode") or "missing") for row in rows).items())
            ),
        },
        "frozen_corpus": freeze_rows,
        "candidate_metrics": candidate_metrics,
        "promotion": {
            "decision": decision,
            "candidate": finalist if promote else None,
            "finalist": finalist,
            "reason": reason,
            "gates": gates,
            "failed_gates": failed_gates,
            "fallback": BASELINE,
            "next_hypothesis": None if promote else "neural_residual_echo_suppression_v1",
            "best_observed_candidate": best_observed,
        },
        "sessions": rows,
    }
    args.out_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.out_dir / "echo_suppression_promotion_corpus_report.json"
    md_path = args.out_dir / "echo_suppression_promotion_corpus_report.md"
    existing = read_json(json_path)
    decision_basis = {
        "frozen_corpus": freeze_rows,
        "candidate_metrics": candidate_metrics,
        "promotion": payload["promotion"],
    }
    payload["decision_fingerprint"] = hashlib.sha256(
        json.dumps(decision_basis, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    same_frozen_corpus = (
        existing.get("frozen_corpus") == freeze_rows
        if existing.get("schema") == CORPUS_SCHEMA
        else False
    )
    payload["determinism"] = {
        "previous_report_present": existing.get("schema") == CORPUS_SCHEMA,
        "same_frozen_corpus": same_frozen_corpus,
        "previous_decision_fingerprint": existing.get("decision_fingerprint"),
        "replay_verified": (
            existing.get("decision_fingerprint") == payload["decision_fingerprint"]
            if same_frozen_corpus and existing.get("decision_fingerprint")
            else None
        ),
    }
    write_json(json_path, payload)
    md_path.write_text(render_corpus(payload), encoding="utf-8")
    freeze_path = args.out_dir / "frozen_corpus.json"
    write_json(
        freeze_path,
        {
            "schema": "murmurmark.echo_suppression_frozen_corpus/v1",
            "profile": PROFILE,
            "sessions": freeze_rows,
        },
    )
    print(f"echo_promotion_corpus_report: {json_path}")
    print(f"promotion_decision: {decision}")
    print(f"finalist: {finalist}")
    return payload


def apply_policy(args: argparse.Namespace) -> dict[str, Any]:
    session = args.session.resolve()
    policy = read_json(args.policy.resolve())
    repo_root = Path(__file__).resolve().parent.parent
    output = session / "derived/preprocess/echo/echo_suppression_selection.json"
    baseline_path = session / "derived/preprocess/audio/mic_role_masked_for_asr.wav"
    mic_for_asr = session / "derived/preprocess/audio/mic_for_asr.wav"
    reason = "policy_missing_or_invalid"
    selected = BASELINE
    applied = False
    stale = False
    session_audio_gate: dict[str, Any] | None = None
    candidate_fingerprint: dict[str, Any] | None = None
    candidate_failure: str | None = None
    if policy.get("schema") == POLICY_SCHEMA:
        if policy.get("decision") != "PROMOTE_ECHO_SUPPRESSION_V1":
            reason = "corpus_policy_not_promoted"
        else:
            selected = str(policy.get("candidate") or BASELINE)
            if selected not in CANDIDATE_ORDER[1:]:
                reason = "policy_candidate_invalid"
                stale = True
                selected = BASELINE
            corpus_path = Path(str(policy.get("corpus_report") or ""))
            if not corpus_path.is_absolute():
                corpus_path = repo_root / corpus_path
            corpus = read_json(corpus_path)
            expected_sha = str(policy.get("corpus_report_sha256") or "")
            if selected == BASELINE:
                pass
            elif not corpus_path.exists() or sha256(corpus_path) != expected_sha:
                reason = "stale_or_missing_corpus_report"
                stale = True
                selected = BASELINE
            elif (
                corpus.get("promotion", {}).get("decision") != "PROMOTE_ECHO_SUPPRESSION_V1"
                or corpus.get("promotion", {}).get("candidate") != selected
            ):
                reason = "corpus_candidate_mismatch"
                stale = True
                selected = BASELINE
            else:
                mode = str(acoustic_mode(session).get("mode") or "uncertain")
                if mode in {"headphones_or_low_leak", "no_speech", "uncertain"}:
                    reason = f"acoustic_mode_{mode}_baseline"
                    selected = BASELINE
                else:
                    try:
                        output_root = session / "derived/preprocess/echo-promotion-v1"
                        canonical = canonical_inputs(session, output_root, refresh=False)
                        materialized, failures = materialize_candidates(
                            session=session,
                            output_root=output_root,
                            canonical=canonical,
                            refresh=False,
                            requested={selected},
                            benchmark_baseline=False,
                        )
                        candidate = materialized.get(selected)
                        candidate_failure = failures.get(selected)
                        if candidate is None:
                            reason = "candidate_materialization_failed"
                            selected = BASELINE
                        else:
                            session_audio_gate = audio_candidate_gates(selected, candidate, mode)
                            candidate_path = output_root / "candidates" / selected / "mic_for_asr.wav"
                            if session_audio_gate["passed"] is True and candidate_path.exists():
                                shutil.copy2(candidate_path, mic_for_asr)
                                candidate_fingerprint = fingerprint(candidate_path, session)
                                reason = "promoted_candidate_materialized_and_audio_gates_passed"
                                applied = True
                            else:
                                reason = "promoted_candidate_session_audio_gate_failed"
                                selected = BASELINE
                    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
                        candidate_failure = f"{type(error).__name__}:{error}"
                        reason = "candidate_materialization_failed_open"
                        selected = BASELINE
    if selected == BASELINE and baseline_path.exists():
        # local_fir already copied this path in normal preprocessing. Re-copying
        # makes resume deterministic if a stale experimental run touched it.
        if not mic_for_asr.exists() or sha256(mic_for_asr) != sha256(baseline_path):
            shutil.copy2(baseline_path, mic_for_asr)
    payload = {
        "schema": SELECTION_SCHEMA,
        "policy": relative(args.policy.resolve(), Path(__file__).resolve().parent.parent),
        "policy_decision": policy.get("decision"),
        "selected": selected,
        "reason": reason,
        "candidate_applied": applied,
        "stale_evidence": stale,
        "session_audio_gate": session_audio_gate,
        "candidate_fingerprint": candidate_fingerprint,
        "candidate_failure": candidate_failure,
        "fallback": BASELINE,
        "batch_authoritative": True,
        "mic_for_asr": relative(mic_for_asr, session) if mic_for_asr.exists() else None,
        "mic_for_asr_sha256": sha256(mic_for_asr) if mic_for_asr.exists() else None,
    }
    write_json(output, payload)
    print(f"echo_suppression_selection: {output}")
    print(f"selected: {selected}")
    print(f"reason: {reason}")
    return payload


def main() -> int:
    args = parse_args()
    if args.command == "session":
        run_session(args)
    elif args.command == "corpus":
        run_corpus(args)
    else:
        apply_policy(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
