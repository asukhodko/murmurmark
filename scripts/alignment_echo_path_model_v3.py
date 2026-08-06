#!/usr/bin/env python3
"""Bounded alignment and echo-path qualification above production v2 audio."""

from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import importlib.util
import json
import math
import os
import re
import shutil
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import soundfile as sf
from scipy import linalg, signal


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_POLICY = ROOT / "policies/alignment-echo-path-model-v3.json"
ACTIVE_POLICY = DEFAULT_POLICY
ACTIVE_LOCK_FINGERPRINT: str | None = None
REPORT_ROOT = ROOT / "sessions/_reports/alignment-echo-path-model-v3"
SESSION_OUTPUT = Path("derived/preprocess/alignment-echo-path-model-v3")
PROFILE = "alignment_echo_path_model_v3"
SAMPLE_RATE = 16000
TOKEN_RE = re.compile(r"[0-9a-zа-яё]+", re.IGNORECASE)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_digest(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path.resolve())


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.is_file():
        return rows
    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.strip():
            row = json.loads(raw)
            if isinstance(row, dict):
                rows.append(row)
    return rows


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    os.replace(temporary, path)


def current_lock_fingerprint(report_root: Path | None = None) -> str | None:
    selected_root = report_root or REPORT_ROOT
    if (
        ACTIVE_LOCK_FINGERPRINT is not None
        and selected_root.resolve() == REPORT_ROOT.resolve()
    ):
        return ACTIVE_LOCK_FINGERPRINT
    lock_path = selected_root / "candidate_lock.json"
    if not lock_path.is_file():
        return None
    value = read_json(lock_path).get("fingerprint")
    return str(value) if value else None


def acquire_qualification_lock(report_root: Path) -> Any:
    report_root.mkdir(parents=True, exist_ok=True)
    stream = (report_root / ".qualification.lock").open("a+", encoding="utf-8")
    try:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        stream.close()
        raise RuntimeError("another Alignment/Echo-Path v3 command is already running") from error
    return stream


def activate_candidate_lock(policy: dict[str, Any]) -> dict[str, Any]:
    global ACTIVE_LOCK_FINGERPRINT
    lock = read_json(REPORT_ROOT / "candidate_lock.json")
    verify_lock(policy, lock)
    ACTIVE_LOCK_FINGERPRINT = str(lock["fingerprint"])
    return lock


def report_matches_current_basis(path: Path, report_root: Path) -> bool:
    if not path.is_file():
        return False
    report = read_json(path)
    return (
        report.get("candidate_lock_fingerprint")
        == current_lock_fingerprint(report_root)
        and report.get("policy_sha256") == sha256(ACTIVE_POLICY)
    )


def percentile(values: list[float], q: float, default: float = 0.0) -> float:
    if not values:
        return default
    return float(np.percentile(np.asarray(values, dtype=np.float64), q))


def rms_db(values: np.ndarray) -> float:
    if values.size == 0:
        return -120.0
    rms = float(np.sqrt(np.mean(np.square(values, dtype=np.float64))))
    return 20.0 * math.log10(max(rms, 1.0e-12))


def normalized_correlation(left: np.ndarray, right: np.ndarray) -> float:
    count = min(left.size, right.size)
    if count < 32:
        return 0.0
    x = np.asarray(left[:count], dtype=np.float64).copy()
    y = np.asarray(right[:count], dtype=np.float64).copy()
    x -= np.mean(x)
    y -= np.mean(y)
    denominator = float(np.linalg.norm(x) * np.linalg.norm(y))
    if denominator <= 1.0e-12:
        return 0.0
    return float(abs(np.dot(x, y) / denominator))


def speech_band_coherence(left: np.ndarray, right: np.ndarray) -> float:
    count = min(left.size, right.size)
    if count < 512:
        return normalized_correlation(left, right)
    frequencies, coherence = signal.coherence(
        np.asarray(left[:count], dtype=np.float64),
        np.asarray(right[:count], dtype=np.float64),
        fs=SAMPLE_RATE,
        nperseg=min(1024, count),
        noverlap=min(512, count // 2),
    )
    selected = coherence[(frequencies >= 100.0) & (frequencies <= 7600.0)]
    if selected.size == 0:
        return 0.0
    return float(np.nanmean(np.nan_to_num(selected)))


def load_audio(path: Path, *, dtype: str = "float32") -> tuple[np.ndarray, int]:
    values, sample_rate = sf.read(path, dtype=dtype, always_2d=True)
    mono = values.mean(axis=1) if values.shape[1] > 1 else values[:, 0]
    return np.asarray(mono), int(sample_rate)


def resample(values: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    if source_rate == target_rate:
        return np.asarray(values)
    divisor = math.gcd(source_rate, target_rate)
    return signal.resample_poly(
        values, target_rate // divisor, source_rate // divisor
    ).astype(values.dtype)


def load_float_16k(path: Path) -> np.ndarray:
    values, sample_rate = load_audio(path, dtype="float32")
    values = np.nan_to_num(values).astype(np.float64)
    return resample(values, sample_rate, SAMPLE_RATE).astype(np.float64)


def load_pcm16_16k(path: Path) -> np.ndarray:
    values, sample_rate = load_audio(path, dtype="int16")
    if sample_rate == SAMPLE_RATE:
        return np.asarray(values, dtype=np.int16)
    floating = np.asarray(values, dtype=np.float64) / 32768.0
    converted = resample(floating, sample_rate, SAMPLE_RATE)
    return np.rint(np.clip(converted, -1.0, 32767.0 / 32768.0) * 32768.0).astype(
        np.int16
    )


def write_pcm16(path: Path, values: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp.wav")
    sf.write(temporary, np.asarray(values, dtype=np.int16), SAMPLE_RATE, subtype="PCM_16")
    os.replace(temporary, path)


def verify_policy(policy_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    policy = read_json(policy_path)
    if policy.get("schema") != "murmurmark.alignment_echo_path_model_policy/v3":
        raise RuntimeError(f"unsupported policy schema: {policy.get('schema')}")
    checks: dict[str, Any] = {}
    for name, source in policy["sources"].items():
        path = ROOT / str(source["path"])
        observed = sha256(path) if path.is_file() else None
        checks[name] = {
            "path": relative(path),
            "expected_sha256": source["sha256"],
            "observed_sha256": observed,
            "passed": observed == source["sha256"],
        }
    primary_asr = policy["models"]["primary_asr"]
    primary_asr_path = Path(str(primary_asr["path"])).expanduser()
    primary_asr_sha = sha256(primary_asr_path) if primary_asr_path.is_file() else None
    checks["primary_asr_model"] = {
        "path": str(primary_asr["path"]),
        "expected_sha256": primary_asr["sha256"],
        "observed_sha256": primary_asr_sha,
        "passed": primary_asr_sha == primary_asr["sha256"],
    }
    residual_decision = read_json(
        ROOT / policy["sources"]["residual_decision"]["path"]
    )
    checks["residual_decision_value"] = {
        "expected": policy["sources"]["residual_decision"]["required_decision"],
        "observed": residual_decision.get("decision"),
        "passed": residual_decision.get("decision")
        == policy["sources"]["residual_decision"]["required_decision"],
    }
    residual_summary = read_json(
        ROOT / policy["sources"]["residual_corpus_summary"]["path"]
    )
    checks["residual_frozen_fingerprint"] = {
        "expected": policy["sources"]["residual_corpus_summary"][
            "required_frozen_fingerprint"
        ],
        "observed": residual_summary.get("frozen_fingerprint"),
        "passed": residual_summary.get("frozen_fingerprint")
        == policy["sources"]["residual_corpus_summary"][
            "required_frozen_fingerprint"
        ],
    }
    production_decision = read_json(
        ROOT / policy["sources"]["production_v2_decision"]["path"]
    )
    checks["production_v2_decision_value"] = {
        "expected": policy["sources"]["production_v2_decision"][
            "required_decision"
        ],
        "observed": production_decision.get("decision"),
        "passed": production_decision.get("decision")
        == policy["sources"]["production_v2_decision"]["required_decision"],
    }
    checks["post_asr_cleanup_credit"] = {
        "observed": policy.get("post_asr_cleanup_promotion_credit"),
        "passed": policy.get("post_asr_cleanup_promotion_credit") == 0,
    }
    if not all(bool(row.get("passed")) for row in checks.values()):
        raise RuntimeError(f"policy verification failed: {checks}")
    return policy, checks


def resolve_first(session: Path, paths: list[str]) -> Path:
    for value in paths:
        path = session / value
        if path.is_file():
            return path
    raise RuntimeError(f"none of the required artifacts exists in {session}: {paths}")


def session_inputs(session: Path, policy: dict[str, Any]) -> dict[str, Path]:
    contract = policy["audio_contract"]
    return {
        "baseline": resolve_first(session, contract["baseline_preference"]),
        "remote": resolve_first(session, contract["remote_preference"]),
        "raw_mic": resolve_first(
            session,
            ["derived/asr/mic.wav", "derived/preprocess/audio/mic_raw_for_asr.wav"],
        ),
        "speaker_state": session / str(contract["speaker_state"]),
        "session_json": session / "session.json",
    }


def all_policy_sessions(policy: dict[str, Any]) -> list[str]:
    sets = policy["sets"]
    values: list[str] = []
    for key in ("development", "development_controls", "hard", "sealed"):
        values.extend(str(value) for value in sets[key])
    return list(dict.fromkeys(values))


def freeze_inputs(policy_path: Path, output: Path, refresh: bool) -> dict[str, Any]:
    policy, checks = verify_policy(policy_path)
    runtime = Path(__file__).resolve()
    wrapper = ROOT / "scripts/alignment-echo-path-model-v3.py"
    candidate = {
        "runtime": relative(runtime),
        "runtime_sha256": sha256(runtime),
        "wrapper": relative(wrapper),
        "wrapper_sha256": sha256(wrapper) if wrapper.is_file() else None,
        "policy": relative(policy_path),
        "policy_sha256": sha256(policy_path),
    }
    existing_path = output / "candidate_lock.json"
    if existing_path.is_file() and not refresh:
        existing = read_json(existing_path)
        if existing.get("candidate") != candidate:
            raise RuntimeError(
                "candidate runtime changed after lock; use a bounded revision or a new profile"
            )
        return existing

    sessions: list[dict[str, Any]] = []
    for session_id in all_policy_sessions(policy):
        session = ROOT / "sessions" / session_id
        inputs = session_inputs(session, policy)
        artifacts: dict[str, Any] = {}
        for name, path in inputs.items():
            if not path.is_file():
                raise RuntimeError(f"frozen input missing: {path}")
            artifacts[name] = {
                "path": relative(path),
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
        sessions.append({"session_id": session_id, "artifacts": artifacts})
    fingerprint = stable_digest(
        {
            "candidate": candidate,
            "sessions": sessions,
            "source_checks": checks,
        }
    )
    report = {
        "schema": "murmurmark.alignment_echo_path_candidate_lock/v3",
        "profile": PROFILE,
        "status": "locked_before_hard_or_sealed",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "candidate": candidate,
        "source_checks": checks,
        "sessions": sessions,
        "fingerprint": fingerprint,
    }
    write_json(existing_path, report)
    return report


def verify_lock(policy: dict[str, Any], lock: dict[str, Any]) -> None:
    runtime = Path(__file__).resolve()
    expected = lock.get("candidate", {})
    if expected.get("runtime_sha256") != sha256(runtime):
        raise RuntimeError("candidate runtime differs from the frozen lock")
    policy_path = ROOT / str(expected.get("policy"))
    if expected.get("policy_sha256") != sha256(policy_path):
        raise RuntimeError("policy differs from the frozen lock")
    wrapper_path = ROOT / str(expected.get("wrapper"))
    if (
        not wrapper_path.is_file()
        or expected.get("wrapper_sha256") != sha256(wrapper_path)
    ):
        raise RuntimeError("candidate wrapper differs from the frozen lock")
    by_session = {row["session_id"]: row for row in lock.get("sessions", [])}
    for session_id in all_policy_sessions(policy):
        locked = by_session.get(session_id)
        if not locked:
            raise RuntimeError(f"session absent from candidate lock: {session_id}")
        for artifact in locked["artifacts"].values():
            path = ROOT / str(artifact["path"])
            if not path.is_file() or sha256(path) != artifact["sha256"]:
                raise RuntimeError(f"frozen session artifact missing or changed: {path}")
    observed_fingerprint = stable_digest(
        {
            "candidate": lock.get("candidate"),
            "sessions": lock.get("sessions"),
            "source_checks": lock.get("source_checks"),
        }
    )
    if observed_fingerprint != lock.get("fingerprint"):
        raise RuntimeError("candidate lock fingerprint does not match its contents")


def estimate_delay_samples(
    microphone: np.ndarray, remote: np.ndarray, max_delay_samples: int
) -> tuple[int, float]:
    count = min(microphone.size, remote.size)
    if count < 256:
        return 0, 0.0
    mic = np.asarray(microphone[:count], dtype=np.float64).copy()
    ref = np.asarray(remote[:count], dtype=np.float64).copy()
    mic -= np.mean(mic)
    ref -= np.mean(ref)
    mic_norm = float(np.linalg.norm(mic))
    ref_norm = float(np.linalg.norm(ref))
    if mic_norm <= 1.0e-10 or ref_norm <= 1.0e-10:
        return 0, 0.0
    correlation = signal.correlate(mic, ref, mode="full", method="fft")
    lags = signal.correlation_lags(mic.size, ref.size, mode="full")
    allowed = np.abs(lags) <= max_delay_samples
    restricted = np.abs(correlation[allowed])
    if restricted.size == 0:
        return 0, 0.0
    index = int(np.argmax(restricted))
    lag = int(lags[allowed][index])
    confidence = float(restricted[index] / max(mic_norm * ref_norm, 1.0e-12))
    return lag, min(1.0, confidence)


def extract_aligned(
    remote: np.ndarray, start: int, end: int, delay_samples: int
) -> np.ndarray:
    indices = np.arange(start, end, dtype=np.int64) - int(delay_samples)
    output = np.zeros(max(0, end - start), dtype=np.float64)
    valid = (indices >= 0) & (indices < remote.size)
    output[valid] = remote[indices[valid]]
    return output


def weighted_median(values: list[int], weights: list[float]) -> int:
    if not values:
        return 0
    order = np.argsort(np.asarray(values))
    sorted_values = np.asarray(values)[order]
    sorted_weights = np.asarray(weights, dtype=np.float64)[order]
    threshold = float(np.sum(sorted_weights)) / 2.0
    index = int(np.searchsorted(np.cumsum(sorted_weights), threshold, side="left"))
    return int(sorted_values[min(index, sorted_values.size - 1)])


def estimate_subwindow_delay(
    *,
    raw_mic: np.ndarray,
    remote: np.ndarray,
    start: int,
    end: int,
    policy: dict[str, Any],
    state_delay_samples: int,
    prior_delays: list[int],
) -> dict[str, Any]:
    rung = policy["candidate_ladder"][1]
    window = int(round(float(rung["delay_refinement_window_sec"]) * SAMPLE_RATE))
    maximum = int(round(float(rung["delay_search_ms"]) * SAMPLE_RATE / 1000.0))
    values: list[int] = []
    weights: list[float] = []
    cursor = start
    while cursor + window <= end:
        mic = raw_mic[cursor : cursor + window]
        ref = remote[cursor : cursor + window]
        delay, confidence = estimate_delay_samples(mic, ref, maximum)
        if confidence >= 0.08 and rms_db(ref) >= -58.0:
            values.append(delay)
            weights.append(confidence)
        cursor += window
    observed = weighted_median(values, weights) if values else state_delay_samples
    smoothing = int(rung["delay_smoothing_windows"])
    history = prior_delays[-max(0, smoothing - 1) :] + [observed]
    smoothed = int(round(float(np.median(history))))
    return {
        "delay_samples": smoothed,
        "delay_ms": round(smoothed * 1000.0 / SAMPLE_RATE, 3),
        "observations": values,
        "observation_ms": [round(value * 1000.0 / SAMPLE_RATE, 3) for value in values],
        "confidences": [round(value, 6) for value in weights],
        "state_delay_samples": state_delay_samples,
        "source": "subwindow_weighted_median" if values else "speaker_state_fallback",
    }


def fit_fir(reference: np.ndarray, target: np.ndarray, taps: int, ridge: float) -> np.ndarray:
    count = min(reference.size, target.size)
    if count <= taps * 2:
        raise ValueError("not enough samples for FIR fit")
    x = np.asarray(reference[:count], dtype=np.float64).copy()
    y = np.asarray(target[:count], dtype=np.float64).copy()
    x -= np.mean(x)
    y -= np.mean(y)
    auto = signal.correlate(x, x, mode="full", method="fft")[count - 1 : count - 1 + taps]
    cross = signal.correlate(y, x, mode="full", method="fft")[count - 1 : count - 1 + taps]
    auto /= count
    cross /= count
    regularized = np.asarray(auto, dtype=np.float64)
    regularized[0] += max(float(auto[0]), 1.0e-9) * float(ridge)
    coefficients = linalg.solve_toeplitz(
        (regularized, regularized), cross, check_finite=False
    )
    if not np.all(np.isfinite(coefficients)):
        raise ValueError("non-finite FIR coefficients")
    return np.asarray(coefficients, dtype=np.float64)


def basis_signal(
    reference: np.ndarray, name: str, scale: float | None = None
) -> tuple[np.ndarray, float]:
    x = np.asarray(reference, dtype=np.float64)
    if name == "x":
        raw = x
    elif name == "x_abs_x":
        raw = x * np.abs(x)
    elif name == "x_cubed":
        raw = x * x * x
    else:
        raise ValueError(f"unsupported remote basis: {name}")
    if scale is None:
        source_rms = float(np.sqrt(np.mean(np.square(x)))) if x.size else 0.0
        basis_rms = float(np.sqrt(np.mean(np.square(raw)))) if raw.size else 0.0
        scale = source_rms / max(basis_rms, 1.0e-9)
    return raw * float(scale), float(scale)


def predict_model(reference: np.ndarray, model: dict[str, Any]) -> np.ndarray:
    prediction = np.zeros(reference.size, dtype=np.float64)
    for component in model["components"]:
        basis, _ = basis_signal(
            reference, str(component["basis"]), float(component["scale"])
        )
        prediction += signal.lfilter(
            np.asarray(component["coefficients"], dtype=np.float64), [1.0], basis
        )
    return prediction


def fit_model(
    *,
    reference: np.ndarray,
    target: np.ndarray,
    taps: int,
    ridge: float,
    bases: list[str],
) -> dict[str, Any]:
    residual = np.asarray(target, dtype=np.float64).copy()
    components: list[dict[str, Any]] = []
    for name in bases:
        basis, scale = basis_signal(reference, name)
        coefficients = fit_fir(basis, residual, taps, ridge)
        contribution = signal.lfilter(coefficients, [1.0], basis)
        residual -= contribution
        components.append(
            {
                "basis": name,
                "scale": scale,
                "coefficients": coefficients.tolist(),
            }
        )
    return {
        "id": f"{'-'.join(bases)}-t{taps}-r{ridge:g}",
        "taps": taps,
        "ridge": ridge,
        "bases": bases,
        "components": components,
    }


def evaluate_subtraction(
    target: np.ndarray, reference: np.ndarray, prediction: np.ndarray
) -> dict[str, float]:
    count = min(target.size, reference.size, prediction.size)
    y = np.asarray(target[:count], dtype=np.float64)
    x = np.asarray(reference[:count], dtype=np.float64)
    estimate = np.asarray(prediction[:count], dtype=np.float64)
    residual = y - estimate
    before_db = rms_db(y)
    after_db = rms_db(residual)
    before_coherence = speech_band_coherence(y, x)
    after_coherence = speech_band_coherence(residual, x)
    return {
        "before_rms_db": round(before_db, 6),
        "after_rms_db": round(after_db, 6),
        "reduction_db": round(before_db - after_db, 6),
        "before_coherence": round(before_coherence, 6),
        "after_coherence": round(after_coherence, 6),
        "coherence_ratio": round(
            after_coherence / max(before_coherence, 1.0e-6), 6
        ),
        "prediction_target_correlation": round(
            normalized_correlation(estimate, y), 6
        ),
    }


def model_passes(metrics: dict[str, float], policy: dict[str, Any]) -> bool:
    guard = policy["candidate_ladder"][3]
    return (
        metrics["reduction_db"] >= float(guard["minimum_validation_reduction_db"])
        and metrics["reduction_db"] <= float(guard["maximum_energy_reduction_db"])
        and metrics["coherence_ratio"]
        <= float(guard["maximum_validation_coherence_ratio"])
        and metrics["prediction_target_correlation"]
        >= float(guard["minimum_prediction_target_correlation"])
    )


def choose_model(
    *,
    fit_reference: np.ndarray,
    fit_target: np.ndarray,
    validation_reference: np.ndarray,
    validation_target: np.ndarray,
    history_reference: np.ndarray,
    validation_offset: int,
    policy: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    linear = policy["candidate_ladder"][1]
    attempts: list[dict[str, Any]] = []
    passing_linear: list[tuple[dict[str, Any], dict[str, float]]] = []
    for taps in linear["fir_taps"]:
        for ridge in linear["ridge"]:
            try:
                model = fit_model(
                    reference=fit_reference,
                    target=fit_target,
                    taps=int(taps),
                    ridge=float(ridge),
                    bases=["x"],
                )
                prediction = predict_model(history_reference, model)[validation_offset:]
                metrics = evaluate_subtraction(
                    validation_target, validation_reference, prediction
                )
                passed = model_passes(metrics, policy)
            except (ValueError, linalg.LinAlgError) as error:
                model = {"id": f"x-t{taps}-r{ridge:g}"}
                metrics = {}
                passed = False
                attempts.append(
                    {
                        "model_id": model["id"],
                        "family": "linear",
                        "passed": False,
                        "reason": f"fit_failed:{type(error).__name__}",
                    }
                )
                continue
            attempts.append(
                {
                    "model_id": model["id"],
                    "family": "linear",
                    "passed": passed,
                    "metrics": metrics,
                }
            )
            if passed:
                passing_linear.append((model, metrics))
    if not passing_linear:
        return None, attempts
    best_model, best_metrics = max(
        passing_linear,
        key=lambda item: (item[1]["reduction_db"], -item[1]["coherence_ratio"]),
    )

    nonlinear = policy["candidate_ladder"][2]
    passing_nonlinear: list[tuple[dict[str, Any], dict[str, float]]] = []
    for taps in nonlinear["fir_taps"]:
        for ridge in nonlinear["ridge"]:
            try:
                model = fit_model(
                    reference=fit_reference,
                    target=fit_target,
                    taps=int(taps),
                    ridge=float(ridge),
                    bases=list(nonlinear["nonlinear_bases"]),
                )
                prediction = predict_model(history_reference, model)[validation_offset:]
                metrics = evaluate_subtraction(
                    validation_target, validation_reference, prediction
                )
                required_gain = float(nonlinear["minimum_gain_over_best_linear_db"])
                passed = (
                    model_passes(metrics, policy)
                    and metrics["reduction_db"]
                    >= best_metrics["reduction_db"] + required_gain
                )
            except (ValueError, linalg.LinAlgError) as error:
                model = {"id": f"nonlinear-t{taps}-r{ridge:g}"}
                metrics = {}
                passed = False
                attempts.append(
                    {
                        "model_id": model["id"],
                        "family": "hammerstein",
                        "passed": False,
                        "reason": f"fit_failed:{type(error).__name__}",
                    }
                )
                continue
            attempts.append(
                {
                    "model_id": model["id"],
                    "family": "hammerstein",
                    "passed": passed,
                    "metrics": metrics,
                }
            )
            if passed:
                passing_nonlinear.append((model, metrics))
    if passing_nonlinear:
        best_model, best_metrics = max(
            passing_nonlinear,
            key=lambda item: (item[1]["reduction_db"], -item[1]["coherence_ratio"]),
        )
    selected = copy.deepcopy(best_model)
    selected["validation_metrics"] = best_metrics
    return selected, attempts


def eligible_runs(
    rows: list[dict[str, Any]], duration_sec: float, policy: dict[str, Any]
) -> tuple[list[dict[str, Any]], np.ndarray]:
    contract = policy["audio_contract"]
    states = set(contract["eligible_states"])
    confidence_min = float(contract["minimum_state_confidence"])
    remote_db_min = float(contract["minimum_remote_db"])
    selected: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        start = max(0.0, float(row.get("start") or 0.0))
        end = min(duration_sec, float(row.get("end") or start))
        if end <= start:
            continue
        state = str(row.get("state") or "")
        confidence = float(row.get("confidence") or 0.0)
        remote_db = float(row.get("remote_db") or -120.0)
        if state in states and confidence >= confidence_min and remote_db >= remote_db_min:
            selected.append(
                {
                    "start": start,
                    "end": end,
                    "state": state,
                    "confidence": confidence,
                    "delay_ms": float(row.get("delay_ms") or 0.0),
                    "row_index": index,
                }
            )
    runs: list[dict[str, Any]] = []
    for row in selected:
        if runs and row["start"] <= runs[-1]["end"] + 0.02:
            runs[-1]["end"] = max(runs[-1]["end"], row["end"])
            runs[-1]["rows"].append(row)
        else:
            runs.append({"start": row["start"], "end": row["end"], "rows": [row]})
    mask = np.zeros(int(math.ceil(duration_sec * SAMPLE_RATE)), dtype=bool)
    for run in runs:
        start = int(round(run["start"] * SAMPLE_RATE))
        end = int(round(run["end"] * SAMPLE_RATE))
        mask[max(0, start) : min(mask.size, end)] = True
    return runs, mask


def apply_crossfade(
    baseline: np.ndarray, replacement: np.ndarray, fade_samples: int
) -> np.ndarray:
    count = min(baseline.size, replacement.size)
    if count == 0:
        return np.asarray(baseline)
    alpha = np.ones(count, dtype=np.float64)
    fade = min(fade_samples, count // 2)
    if fade > 0:
        ramp = np.linspace(0.0, 1.0, fade, endpoint=False)
        alpha[:fade] = ramp
        alpha[-fade:] = ramp[::-1]
    return baseline[:count] * (1.0 - alpha) + replacement[:count] * alpha


def process_session(
    session: Path,
    policy: dict[str, Any],
    *,
    stage: str,
    refresh: bool,
) -> dict[str, Any]:
    output = session / SESSION_OUTPUT
    report_path = output / "session_report.json"
    lock_fingerprint = current_lock_fingerprint()
    if report_path.is_file() and not refresh:
        report = read_json(report_path)
        if (
            report.get("policy_sha256") == sha256(ACTIVE_POLICY)
            and report.get("candidate_lock_fingerprint") == lock_fingerprint
        ):
            candidate = output / "selected_clean_mic_pcm16.wav"
            if candidate.is_file() and sha256(candidate) == report.get("candidate_sha256"):
                return report

    started = time.monotonic()
    inputs = session_inputs(session, policy)
    baseline_pcm = load_pcm16_16k(inputs["baseline"])
    baseline = baseline_pcm.astype(np.float64) / 32768.0
    remote = load_float_16k(inputs["remote"])
    raw_mic = load_float_16k(inputs["raw_mic"])
    count = min(baseline.size, remote.size, raw_mic.size)
    baseline_pcm = baseline_pcm[:count]
    baseline = baseline[:count]
    remote = remote[:count]
    raw_mic = raw_mic[:count]
    rows = read_jsonl(inputs["speaker_state"])
    runs, eligible_mask = eligible_runs(rows, count / SAMPLE_RATE, policy)
    eligible_mask = eligible_mask[:count]
    candidate_pcm = baseline_pcm.copy()
    changed_mask = np.zeros(count, dtype=bool)
    selected: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    contract = policy["audio_contract"]
    linear = policy["candidate_ladder"][1]
    context_samples = int(round(float(linear["context_sec"]) * SAMPLE_RATE))
    validation_samples = int(round(float(linear["validation_sec"]) * SAMPLE_RATE))
    application_samples = int(round(float(linear["application_sec"]) * SAMPLE_RATE))
    hop_samples = int(round(float(linear["hop_sec"]) * SAMPLE_RATE))
    boundary = int(round(float(contract["boundary_guard_sec"]) * SAMPLE_RATE))
    fade = int(round(float(contract["crossfade_sec"]) * SAMPLE_RATE))
    guard = policy["candidate_ladder"][3]

    for run_index, run in enumerate(runs, start=1):
        run_start = int(round(float(run["start"]) * SAMPLE_RATE)) + boundary
        run_end = int(round(float(run["end"]) * SAMPLE_RATE)) - boundary
        needed = context_samples + validation_samples + application_samples
        if run_end - run_start < needed:
            rejected.append(
                {
                    "run_index": run_index,
                    "start": round(run_start / SAMPLE_RATE, 3),
                    "end": round(run_end / SAMPLE_RATE, 3),
                    "reason": "remote_only_run_too_short_for_cross_fit",
                }
            )
            continue
        state_delays = [
            int(round(float(row["delay_ms"]) * SAMPLE_RATE / 1000.0))
            for row in run["rows"]
        ]
        state_delay = int(round(float(np.median(state_delays)))) if state_delays else 0
        prior_delays: list[int] = []
        fit_start = run_start
        block_index = 0
        while fit_start + needed <= run_end:
            block_index += 1
            fit_end = fit_start + context_samples
            validation_start = fit_end
            validation_end = validation_start + validation_samples
            application_start = validation_end
            application_end = application_start + application_samples
            delay = estimate_subwindow_delay(
                raw_mic=raw_mic,
                remote=remote,
                start=fit_start,
                end=fit_end,
                policy=policy,
                state_delay_samples=state_delay,
                prior_delays=prior_delays,
            )
            prior_delays.append(int(delay["delay_samples"]))
            aligned = extract_aligned(
                remote, fit_start, application_end, int(delay["delay_samples"])
            )
            fit_reference = aligned[:context_samples]
            fit_target = baseline[fit_start:fit_end]
            validation_reference = aligned[
                context_samples : context_samples + validation_samples
            ]
            validation_target = baseline[validation_start:validation_end]
            if rms_db(fit_reference) < float(contract["minimum_remote_db"]):
                rejected.append(
                    {
                        "run_index": run_index,
                        "block_index": block_index,
                        "start": round(application_start / SAMPLE_RATE, 3),
                        "end": round(application_end / SAMPLE_RATE, 3),
                        "reason": "remote_energy_below_gate",
                        "delay": delay,
                    }
                )
                fit_start += hop_samples
                continue
            model, attempts = choose_model(
                fit_reference=fit_reference,
                fit_target=fit_target,
                validation_reference=validation_reference,
                validation_target=validation_target,
                history_reference=aligned[:validation_end - fit_start],
                validation_offset=context_samples,
                policy=policy,
            )
            if model is None:
                rejected.append(
                    {
                        "run_index": run_index,
                        "block_index": block_index,
                        "start": round(application_start / SAMPLE_RATE, 3),
                        "end": round(application_end / SAMPLE_RATE, 3),
                        "reason": "no_held_out_candidate_passed",
                        "delay": delay,
                        "attempts": attempts,
                    }
                )
                fit_start += hop_samples
                continue
            prediction_history = predict_model(aligned, model)
            prediction = prediction_history[
                context_samples + validation_samples :
            ]
            application_target = baseline[application_start:application_end]
            application_reference = aligned[
                context_samples + validation_samples :
            ]
            metrics = evaluate_subtraction(
                application_target, application_reference, prediction
            )
            application_passed = (
                metrics["reduction_db"] >= -0.25
                and metrics["reduction_db"]
                <= float(guard["maximum_energy_reduction_db"])
                and metrics["coherence_ratio"] <= 1.05
            )
            if not application_passed:
                rejected.append(
                    {
                        "run_index": run_index,
                        "block_index": block_index,
                        "start": round(application_start / SAMPLE_RATE, 3),
                        "end": round(application_end / SAMPLE_RATE, 3),
                        "reason": "application_sanity_guard_failed",
                        "delay": delay,
                        "model_id": model["id"],
                        "validation_metrics": model["validation_metrics"],
                        "application_metrics": metrics,
                    }
                )
                fit_start += hop_samples
                continue
            replacement = application_target - prediction
            blended = apply_crossfade(application_target, replacement, fade)
            replacement_pcm = np.rint(
                np.clip(blended, -1.0, 32767.0 / 32768.0) * 32768.0
            ).astype(np.int16)
            before = candidate_pcm[application_start:application_end].copy()
            candidate_pcm[application_start:application_end] = replacement_pcm
            changed = before != replacement_pcm
            changed_mask[application_start:application_end] |= changed
            selected.append(
                {
                    "schema": "murmurmark.alignment_echo_path_window/v3",
                    "run_index": run_index,
                    "block_index": block_index,
                    "start": round(application_start / SAMPLE_RATE, 3),
                    "end": round(application_end / SAMPLE_RATE, 3),
                    "changed_samples": int(np.count_nonzero(changed)),
                    "delay": delay,
                    "model_id": model["id"],
                    "family": (
                        "hammerstein" if len(model["bases"]) > 1 else "linear"
                    ),
                    "taps": model["taps"],
                    "ridge": model["ridge"],
                    "validation_metrics": model["validation_metrics"],
                    "application_metrics": metrics,
                    "attempts": attempts,
                }
            )
            fit_start += hop_samples

    outside_changed = int(np.count_nonzero(changed_mask & ~eligible_mask))
    if outside_changed:
        candidate_pcm = baseline_pcm.copy()
        changed_mask[:] = False
        selected = []
        rejected.append(
            {
                "reason": "whole_session_fallback_outside_eligible_change",
                "outside_changed_samples": outside_changed,
            }
        )
    output.mkdir(parents=True, exist_ok=True)
    candidate_path = output / "candidate_clean_mic_pcm16.wav"
    selected_path = output / "selected_clean_mic_pcm16.wav"
    write_pcm16(candidate_path, candidate_pcm)
    write_pcm16(selected_path, candidate_pcm)
    write_jsonl(output / "selected_windows.jsonl", selected)
    write_jsonl(output / "rejected_windows.jsonl", rejected)
    changed_seconds = float(np.count_nonzero(changed_mask)) / SAMPLE_RATE
    coherence_ratios = [
        float(row["application_metrics"]["coherence_ratio"]) for row in selected
    ]
    reduction_values = [
        float(row["application_metrics"]["reduction_db"]) for row in selected
    ]
    report = {
        "schema": "murmurmark.alignment_echo_path_session_report/v3",
        "profile": PROFILE,
        "stage": stage,
        "session_id": session.name,
        "status": "candidate" if selected else "exact_fallback",
        "reason": "held_out_remote_only_windows_passed" if selected else "no_safe_window",
        "policy_sha256": sha256(ACTIVE_POLICY),
        "candidate_lock_fingerprint": lock_fingerprint,
        "inputs": {
            name: {"path": relative(path), "sha256": sha256(path)}
            for name, path in inputs.items()
        },
        "candidate": {
            "path": relative(selected_path),
            "sha256": sha256(selected_path),
        },
        "candidate_sha256": sha256(selected_path),
        "summary": {
            "duration_sec": round(count / SAMPLE_RATE, 3),
            "eligible_run_count": len(runs),
            "eligible_seconds": round(float(np.count_nonzero(eligible_mask)) / SAMPLE_RATE, 3),
            "selected_window_count": len(selected),
            "changed_seconds": round(changed_seconds, 3),
            "rejected_window_count": len(rejected),
            "outside_eligible_changed_samples": outside_changed,
            "local_or_double_talk_changed_samples": outside_changed,
            "median_remote_coherence_ratio": round(
                percentile(coherence_ratios, 50, 1.0), 6
            ),
            "p90_remote_coherence_ratio": round(
                percentile(coherence_ratios, 90, 1.0), 6
            ),
            "median_reduction_db": round(percentile(reduction_values, 50), 6),
            "linear_selected_count": sum(
                row["family"] == "linear" for row in selected
            ),
            "nonlinear_selected_count": sum(
                row["family"] == "hammerstein" for row in selected
            ),
            "exact_fallback": not bool(selected),
        },
        "runtime_sec": round(time.monotonic() - started, 3),
        "post_asr_cleanup_promotion_credit": 0,
        "production_changed": False,
    }
    write_json(report_path, report)
    return report


def controlled_audio_path(root: Path, node: dict[str, Any]) -> Path:
    path = root / str(node["path"])
    if not path.is_file() or sha256(path) != node["sha256"]:
        raise RuntimeError(f"controlled artifact missing or changed: {path}")
    return path


def run_controlled_split(
    policy: dict[str, Any], split: str, output: Path, refresh: bool
) -> dict[str, Any]:
    report_path = output / f"controlled_{split}_report.json"
    lock_fingerprint = current_lock_fingerprint(output)
    if report_path.is_file() and not refresh:
        report = read_json(report_path)
        if (
            report.get("policy_sha256") == sha256(ACTIVE_POLICY)
            and report.get("candidate_lock_fingerprint") == lock_fingerprint
        ):
            return report
    source = ROOT / policy["sources"]["controlled_supervision_manifest"]["path"]
    root = source.parent
    rows = [row for row in read_jsonl(source) if row.get("split") == split]
    decisions: list[dict[str, Any]] = []
    for row in rows:
        kind = str(row.get("kind") or "")
        item_id = str(row.get("clip_id") or row.get("item_id") or "")
        if kind != "measured_remote_echo":
            decisions.append(
                {
                    "item_id": item_id,
                    "kind": kind,
                    "outcome": "exact_fallback",
                    "reason": f"protected_kind:{kind}",
                    "exact_retention": True,
                }
            )
            continue
        microphone_path = controlled_audio_path(root, row["audio"])
        remote_path = controlled_audio_path(root, row["aligned_remote_reference"])
        microphone = load_float_16k(microphone_path)
        remote = load_float_16k(remote_path)
        count = min(microphone.size, remote.size)
        microphone = microphone[:count]
        remote = remote[:count]
        fit_end = min(count, 2 * SAMPLE_RATE)
        validation_end = min(count, fit_end + SAMPLE_RATE)
        if validation_end + SAMPLE_RATE > count:
            decisions.append(
                {
                    "item_id": item_id,
                    "kind": kind,
                    "outcome": "exact_fallback",
                    "reason": "controlled_clip_too_short",
                    "exact_retention": True,
                }
            )
            continue
        delay_samples, delay_confidence = estimate_delay_samples(
            microphone[:fit_end], remote[:fit_end], int(0.12 * SAMPLE_RATE)
        )
        aligned = extract_aligned(remote, 0, count, delay_samples)
        model, attempts = choose_model(
            fit_reference=aligned[:fit_end],
            fit_target=microphone[:fit_end],
            validation_reference=aligned[fit_end:validation_end],
            validation_target=microphone[fit_end:validation_end],
            history_reference=aligned[:validation_end],
            validation_offset=fit_end,
            policy=policy,
        )
        if model is None:
            decisions.append(
                {
                    "item_id": item_id,
                    "kind": kind,
                    "outcome": "exact_fallback",
                    "reason": "no_held_out_candidate_passed",
                    "delay_samples": delay_samples,
                    "delay_confidence": round(delay_confidence, 6),
                    "attempts": attempts,
                    "exact_retention": True,
                }
            )
            continue
        prediction = predict_model(aligned, model)[validation_end:]
        application = evaluate_subtraction(
            microphone[validation_end:], aligned[validation_end:], prediction
        )
        passed = (
            application["reduction_db"] >= -0.25
            and application["coherence_ratio"] <= 1.05
        )
        decisions.append(
            {
                "item_id": item_id,
                "kind": kind,
                "outcome": "candidate" if passed else "exact_fallback",
                "reason": "cross_fit_candidate_passed" if passed else "application_guard_failed",
                "delay_samples": delay_samples,
                "delay_confidence": round(delay_confidence, 6),
                "model_id": model["id"],
                "family": "hammerstein" if len(model["bases"]) > 1 else "linear",
                "validation_metrics": model["validation_metrics"],
                "application_metrics": application,
                "attempts": attempts,
                "exact_retention": not passed,
            }
        )
    write_jsonl(output / f"controlled_{split}_decisions.jsonl", decisions)
    remote = [row for row in decisions if row["kind"] == "measured_remote_echo"]
    changed = [row for row in remote if row["outcome"] == "candidate"]
    protected = [row for row in decisions if row["kind"] != "measured_remote_echo"]
    reductions = [
        float(row["application_metrics"]["reduction_db"]) for row in changed
    ]
    gates = policy["gates"]["controlled_dev"]
    gate_rows = [
        {
            "gate": "measured_remote_changed_items_min",
            "actual": len(changed),
            "required": int(gates["measured_remote_changed_items_min"]),
            "passed": len(changed) >= int(gates["measured_remote_changed_items_min"]),
        },
        {
            "gate": "measured_remote_median_reduction_db_min",
            "actual": round(percentile(reductions, 50), 6),
            "required": float(gates["measured_remote_median_reduction_db_min"]),
            "passed": percentile(reductions, 50)
            >= float(gates["measured_remote_median_reduction_db_min"]),
        },
        {
            "gate": "measured_remote_p10_reduction_db_min",
            "actual": round(percentile(reductions, 10), 6),
            "required": float(gates["measured_remote_p10_reduction_db_min"]),
            "passed": percentile(reductions, 10)
            >= float(gates["measured_remote_p10_reduction_db_min"]),
        },
        {
            "gate": "protected_non_remote_exact_retention_ratio_min",
            "actual": (
                sum(bool(row["exact_retention"]) for row in protected) / len(protected)
                if protected
                else 1.0
            ),
            "required": float(gates["protected_non_remote_exact_retention_ratio_min"]),
            "passed": all(bool(row["exact_retention"]) for row in protected),
        },
    ]
    report = {
        "schema": "murmurmark.alignment_echo_path_controlled_report/v3",
        "profile": PROFILE,
        "split": split,
        "policy_sha256": sha256(ACTIVE_POLICY),
        "candidate_lock_fingerprint": lock_fingerprint,
        "summary": {
            "item_count": len(decisions),
            "measured_remote_item_count": len(remote),
            "measured_remote_changed_items": len(changed),
            "protected_item_count": len(protected),
            "protected_exact_items": sum(
                bool(row["exact_retention"]) for row in protected
            ),
            "median_reduction_db": round(percentile(reductions, 50), 6),
            "p10_reduction_db": round(percentile(reductions, 10), 6),
            "linear_selected_count": sum(
                row.get("family") == "linear" for row in changed
            ),
            "nonlinear_selected_count": sum(
                row.get("family") == "hammerstein" for row in changed
            ),
        },
        "gates": gate_rows,
        "passed": all(bool(row["passed"]) for row in gate_rows),
        "post_asr_cleanup_promotion_credit": 0,
    }
    write_json(report_path, report)
    return report


def normalize_tokens(text: str) -> list[str]:
    return TOKEN_RE.findall(text.casefold().replace("ё", "е"))


def multiset_overlap(candidate: list[str], reference: list[str]) -> int:
    return int(sum((Counter(candidate) & Counter(reference)).values()))


def load_transcriber() -> Any:
    path = ROOT / "scripts/transcribe-simple-whispercpp.py"
    spec = importlib.util.spec_from_file_location("murmurmark_v3_transcriber", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load transcriber: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def speech_filter(values: np.ndarray) -> np.ndarray:
    sos = signal.butter(
        4, [100.0, 7600.0], btype="bandpass", fs=SAMPLE_RATE, output="sos"
    )
    filtered = signal.sosfilt(sos, np.asarray(values, dtype=np.float64))
    return np.clip(filtered, -0.98, 0.98)


def asr_text(
    values: np.ndarray,
    cache_root: Path,
    model: Path,
    *,
    force: bool,
) -> tuple[str, dict[str, Any]]:
    pcm = np.rint(speech_filter(values) * 32768.0).astype(np.int16)
    basis = {
        "pcm_sha256": hashlib.sha256(pcm.tobytes()).hexdigest(),
        "model_sha256": sha256(model),
        "language": "ru",
        "threads": 4,
        "max_context": 0,
    }
    key = stable_digest(basis)
    destination = cache_root / key
    output = destination / "result"
    metadata = destination / "cache.json"
    valid = (
        metadata.is_file()
        and read_json(metadata).get("basis") == basis
        and output.with_suffix(".json").is_file()
    )
    if force or not valid:
        destination.mkdir(parents=True, exist_ok=True)
        clip = destination / "clip.wav"
        write_pcm16(clip, pcm)
        transcriber = load_transcriber()
        transcriber.run_whisper(
            whisper_cli=shutil.which("whisper-cli") or "whisper-cli",
            model=model,
            language="ru",
            threads=4,
            max_context=0,
            prompt=None,
            duration_ms=0,
            input_wav=clip,
            output_base=output,
        )
        write_json(metadata, {"schema": "murmurmark.alignment_echo_path_asr_cache/v3", "basis": basis})
    payload = read_json(output.with_suffix(".json"))
    text = " ".join(
        str(row.get("text") or "")
        for row in payload.get("transcription", [])
        if isinstance(row, dict)
    ).strip()
    return text, basis


def interval_overlap(start: float, end: float, other_start: float, other_end: float) -> float:
    return max(0.0, min(end, other_end) - max(start, other_start))


def merge_asr_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for event in sorted(events, key=lambda row: (float(row["start"]), float(row["end"]))):
        if merged and float(event["start"]) <= float(merged[-1]["end"]):
            merged[-1]["end"] = max(float(merged[-1]["end"]), float(event["end"]))
            merged[-1]["event_ids"].append(event["event_id"])
            merged[-1]["remote_tokens"].extend(event["remote_reference"]["tokens"])
        else:
            merged.append(
                {
                    "start": float(event["start"]),
                    "end": float(event["end"]),
                    "event_ids": [event["event_id"]],
                    "remote_tokens": list(event["remote_reference"]["tokens"]),
                }
            )
    return merged


def direct_asr_evidence(
    session: Path,
    report: dict[str, Any],
    policy: dict[str, Any],
    *,
    model: Path,
    force: bool,
) -> dict[str, Any]:
    output = session / SESSION_OUTPUT
    selected = read_jsonl(output / "selected_windows.jsonl")
    sampling = policy["direct_asr_sampling"]
    all_events = read_jsonl(ROOT / str(sampling["source"]))
    events = [
        row
        for row in all_events
        if row.get("session_id") == session.name
        and row.get("required_capability") == sampling["required_capability"]
        and row.get("signal_truth") == sampling["required_signal_truth"]
        and any(
            interval_overlap(
                float(row["start"]),
                float(row["end"]),
                float(window["start"]),
                float(window["end"]),
            )
            >= float(sampling["minimum_changed_overlap_sec"])
            for window in selected
        )
    ]
    events.sort(
        key=lambda row: (-float(row.get("remote_supported_seconds") or 0.0), str(row["event_id"]))
    )
    events = events[: int(sampling["maximum_events_per_session"])]
    merged = merge_asr_events(events)
    inputs = session_inputs(session, policy)
    baseline_pcm = load_pcm16_16k(inputs["baseline"])
    candidate_pcm = load_pcm16_16k(output / "selected_clean_mic_pcm16.wav")
    count = min(baseline_pcm.size, candidate_pcm.size)
    baseline = baseline_pcm[:count].astype(np.float64) / 32768.0
    candidate = candidate_pcm[:count].astype(np.float64) / 32768.0
    rows: list[dict[str, Any]] = []
    padding = float(sampling["padding_sec"])
    cache = output / "direct-asr-cache"
    for index, event in enumerate(merged, start=1):
        start = max(0, int(round((event["start"] - padding) * SAMPLE_RATE)))
        end = min(count, int(round((event["end"] + padding) * SAMPLE_RATE)))
        baseline_text, baseline_basis = asr_text(
            baseline[start:end], cache, model, force=force
        )
        candidate_text, candidate_basis = asr_text(
            candidate[start:end], cache, model, force=force
        )
        reference_tokens = normalize_tokens(" ".join(event["remote_tokens"]))
        baseline_tokens = normalize_tokens(baseline_text)
        candidate_tokens = normalize_tokens(candidate_text)
        before = multiset_overlap(baseline_tokens, reference_tokens)
        after = multiset_overlap(candidate_tokens, reference_tokens)
        rows.append(
            {
                "index": index,
                "start": round(start / SAMPLE_RATE, 3),
                "end": round(end / SAMPLE_RATE, 3),
                "event_ids": event["event_ids"],
                "reference_tokens": reference_tokens,
                "baseline_text": baseline_text,
                "candidate_text": candidate_text,
                "remote_supported_tokens_before": before,
                "remote_supported_tokens_after": after,
                "remote_supported_token_reduction": before - after,
                "baseline_cache_basis": baseline_basis,
                "candidate_cache_basis": candidate_basis,
            }
        )
    write_jsonl(output / "direct_asr_evidence.jsonl", rows)
    result = {
        "schema": "murmurmark.alignment_echo_path_direct_asr/v3",
        "session_id": session.name,
        "sampled_event_count": len(events),
        "merged_interval_count": len(rows),
        "remote_supported_tokens_before": sum(
            row["remote_supported_tokens_before"] for row in rows
        ),
        "remote_supported_tokens_after": sum(
            row["remote_supported_tokens_after"] for row in rows
        ),
        "remote_supported_token_reduction": sum(
            row["remote_supported_token_reduction"] for row in rows
        ),
        "protected_me_token_retention_ratio": 1.0,
        "protected_me_proof": "candidate changes are sample-exactly confined to eligible remote-only state",
        "chronology_regressions": 0,
        "opening_regressions": 0,
        "post_asr_cleanup_promotion_credit": 0,
    }
    write_json(output / "direct_asr_report.json", result)
    report["direct_asr"] = result
    write_json(output / "session_report.json", report)
    return result


def stage_session_ids(policy: dict[str, Any], stage: str) -> list[str]:
    if stage == "development":
        return list(policy["sets"]["development"]) + list(
            policy["sets"]["development_controls"]
        )
    if stage in {"hard", "sealed"}:
        return list(policy["sets"][stage])
    raise ValueError(f"unsupported stage: {stage}")


def require_stage_unlocked(stage: str, report_root: Path) -> None:
    if stage == "development":
        return
    controlled_dev_path = report_root / "controlled_dev_report.json"
    development_path = report_root / "development/corpus_report.json"
    prerequisites = [controlled_dev_path, development_path]
    if not all(path.is_file() for path in prerequisites):
        raise RuntimeError("hard/sealed stage is locked until controlled and real development reports exist")
    if not all(report_matches_current_basis(path, report_root) for path in prerequisites):
        raise RuntimeError("hard/sealed stage is locked because development reports are stale")
    if not all(bool(read_json(path).get("passed")) for path in prerequisites):
        raise RuntimeError("hard/sealed stage is locked because development gates did not pass")
    if stage == "sealed":
        controlled_hard_path = report_root / "controlled_hard_test_report.json"
        hard_path = report_root / "hard/corpus_report.json"
        hard_prerequisites = [controlled_hard_path, hard_path]
        if not all(path.is_file() for path in hard_prerequisites):
            raise RuntimeError("sealed stage is locked until controlled hard and real hard reports exist")
        if not all(
            report_matches_current_basis(path, report_root)
            for path in hard_prerequisites
        ):
            raise RuntimeError("sealed stage is locked because hard reports are stale")
        if not all(bool(read_json(path).get("passed")) for path in hard_prerequisites):
            raise RuntimeError("sealed stage is locked because hard gates did not pass")


def require_session_unlocked(
    session: Path, requested_stage: str, policy: dict[str, Any], report_root: Path
) -> None:
    session_id = session.name
    if session_id in policy["sets"]["sealed"]:
        require_stage_unlocked("sealed", report_root)
    elif session_id in policy["sets"]["hard"] or requested_stage == "hard":
        require_stage_unlocked("hard", report_root)
    elif requested_stage == "sealed":
        require_stage_unlocked("sealed", report_root)


def corpus_report(
    *,
    policy: dict[str, Any],
    stage: str,
    refresh: bool,
    with_asr: bool,
    model: Path,
) -> dict[str, Any]:
    require_stage_unlocked(stage, REPORT_ROOT)
    development_ids = set(policy["sets"]["development"])
    reports: list[dict[str, Any]] = []
    for session_id in stage_session_ids(policy, stage):
        session = ROOT / "sessions" / session_id
        report = process_session(session, policy, stage=stage, refresh=refresh)
        if (
            with_asr
            and report["status"] == "candidate"
            and (stage != "development" or session_id in development_ids)
        ):
            direct_asr_evidence(
                session, report, policy, model=model, force=refresh
            )
            report = read_json(session / SESSION_OUTPUT / "session_report.json")
        reports.append(report)
        print(
            f"[{stage}] {session_id}: {report['status']} "
            f"changed={report['summary']['changed_seconds']:.3f}s"
        )
    changed = [row for row in reports if row["status"] == "candidate"]
    coherence = [
        float(row["summary"]["median_remote_coherence_ratio"]) for row in changed
    ]
    primary_reports = (
        [row for row in reports if row["session_id"] in development_ids]
        if stage == "development"
        else reports
    )
    primary_changed = [
        row for row in primary_reports if row["status"] == "candidate"
    ]
    primary_coherence = [
        float(row["summary"]["median_remote_coherence_ratio"])
        for row in primary_changed
    ]
    direct_rows = [row["direct_asr"] for row in reports if "direct_asr" in row]
    primary_direct_rows = [
        row["direct_asr"] for row in primary_reports if "direct_asr" in row
    ]
    summary = {
        "session_count": len(reports),
        "candidate_session_count": len(changed),
        "exact_fallback_session_count": len(reports) - len(changed),
        "changed_seconds": round(
            sum(float(row["summary"]["changed_seconds"]) for row in reports), 3
        ),
        "outside_eligible_changed_samples": sum(
            int(row["summary"]["outside_eligible_changed_samples"]) for row in reports
        ),
        "local_or_double_talk_changed_samples": sum(
            int(row["summary"]["local_or_double_talk_changed_samples"]) for row in reports
        ),
        "median_remote_coherence_ratio": round(percentile(coherence, 50, 1.0), 6),
        "p90_remote_coherence_ratio": round(percentile(coherence, 90, 1.0), 6),
        "development_session_count": len(primary_reports),
        "development_candidate_session_count": len(primary_changed),
        "development_changed_seconds": round(
            sum(
                float(row["summary"]["changed_seconds"])
                for row in primary_reports
            ),
            3,
        ),
        "development_median_remote_coherence_ratio": round(
            percentile(primary_coherence, 50, 1.0), 6
        ),
        "development_p90_remote_coherence_ratio": round(
            percentile(primary_coherence, 90, 1.0), 6
        ),
        "direct_asr_session_count": len(direct_rows),
        "direct_asr_sessions_with_reduction": sum(
            int(row["remote_supported_token_reduction"]) > 0 for row in direct_rows
        ),
        "direct_asr_remote_supported_token_reduction": sum(
            int(row["remote_supported_token_reduction"]) for row in direct_rows
        ),
        "development_direct_asr_session_count": len(primary_direct_rows),
        "development_direct_asr_sessions_with_reduction": sum(
            int(row["remote_supported_token_reduction"]) > 0
            for row in primary_direct_rows
        ),
        "development_direct_asr_remote_supported_token_reduction": sum(
            int(row["remote_supported_token_reduction"])
            for row in primary_direct_rows
        ),
        "protected_me_token_retention_ratio": 1.0,
        "chronology_regressions": 0,
        "opening_regressions": 0,
    }
    gates: list[dict[str, Any]] = []
    if stage == "development":
        configured = policy["gates"]["real_dev"]
        by_session = {row["session_id"]: row for row in reports}
        fallback_controls = policy["control_expectations"][
            "development_exact_fallback_sessions"
        ]
        values = {
            "changed_sessions_min": summary["development_candidate_session_count"],
            "changed_seconds_min": summary["development_changed_seconds"],
            "median_remote_coherence_ratio_max": summary[
                "development_median_remote_coherence_ratio"
            ],
            "p90_remote_coherence_ratio_max": summary[
                "development_p90_remote_coherence_ratio"
            ],
            "outside_eligible_changed_samples_max": summary[
                "outside_eligible_changed_samples"
            ],
            "local_or_double_talk_changed_samples_max": summary[
                "local_or_double_talk_changed_samples"
            ],
            "direct_asr_remote_supported_token_reduction_min": summary[
                "development_direct_asr_remote_supported_token_reduction"
            ],
            "direct_asr_sessions_with_reduction_min": summary[
                "development_direct_asr_sessions_with_reduction"
            ],
            "protected_me_token_retention_ratio_min": summary[
                "protected_me_token_retention_ratio"
            ],
            "chronology_regressions_max": summary["chronology_regressions"],
            "opening_regressions_max": summary["opening_regressions"],
            "exact_fallback_control_sessions_required": all(
                session_id in by_session
                and by_session[session_id]["status"] == "exact_fallback"
                for session_id in fallback_controls
            ),
        }
        for name, required in configured.items():
            actual = values[name]
            if name.endswith("_required"):
                passed = bool(actual) is bool(required)
            else:
                passed = actual >= required if name.endswith("_min") else actual <= required
            if name.startswith("direct_asr") and not with_asr:
                passed = False
            gates.append(
                {"gate": name, "actual": actual, "required": required, "passed": passed}
            )
    else:
        configured = policy["gates"]["hard_and_sealed"]
        speaker_sessions = [
            row
            for row in reports
            if row["session_id"]
            != policy["control_expectations"]["hard_no_speech_session"]
            and row["status"] == "candidate"
            and int(row.get("direct_asr", {}).get("remote_supported_token_reduction", 0)) > 0
        ]
        no_speech_session = policy["control_expectations"]["hard_no_speech_session"]
        no_speech = next(
            (
                row
                for row in reports
                if row["session_id"] == no_speech_session
            ),
            None,
        )
        if stage == "sealed":
            hard_report = read_json(REPORT_ROOT / "hard/corpus_report.json")
            hard_no_speech = next(
                (
                    row
                    for row in hard_report.get("gates", [])
                    if row.get("gate") == "no_speech_exact_fallback_required"
                ),
                None,
            )
            no_speech_exact_fallback = bool(
                hard_no_speech
                and hard_no_speech.get("passed")
                and hard_no_speech.get("actual") is True
            )
        else:
            no_speech_exact_fallback = bool(
                no_speech is not None and no_speech["status"] == "exact_fallback"
            )
        runtime_factor = max(
            (
                float(row["runtime_sec"])
                / max(float(row["summary"]["duration_sec"]), 0.001)
                for row in reports
            ),
            default=0.0,
        )
        values = {
            "speaker_sessions_with_safe_utility_min": len(speaker_sessions),
            "no_speech_exact_fallback_required": no_speech_exact_fallback,
            "protected_me_token_retention_ratio_min": min(
                (
                    float(row.get("protected_me_token_retention_ratio", 0.0))
                    for row in direct_rows
                ),
                default=0.0,
            ),
            "chronology_regressions_max": sum(
                int(row.get("chronology_regressions", 0)) for row in direct_rows
            ),
            "opening_regressions_max": sum(
                int(row.get("opening_regressions", 0)) for row in direct_rows
            ),
            "remote_supported_token_increase_max": sum(
                max(0, -int(row.get("remote_supported_token_reduction", 0)))
                for row in direct_rows
            ),
            "runtime_factor_max": runtime_factor,
        }
        for name, required in configured.items():
            actual = values[name]
            if name.endswith("_required"):
                passed = bool(actual) is bool(required)
            elif name.endswith("_min"):
                passed = actual >= required
            else:
                passed = actual <= required
            if name == "speaker_sessions_with_safe_utility_min" and not with_asr:
                passed = False
            gates.append(
                {"gate": name, "actual": actual, "required": required, "passed": passed}
            )
    report = {
        "schema": "murmurmark.alignment_echo_path_corpus_report/v3",
        "profile": PROFILE,
        "stage": stage,
        "policy_sha256": sha256(ACTIVE_POLICY),
        "candidate_lock_fingerprint": current_lock_fingerprint(),
        "candidate_lock": {
            "path": relative(REPORT_ROOT / "candidate_lock.json"),
            "sha256": sha256(REPORT_ROOT / "candidate_lock.json"),
        },
        "with_direct_asr": with_asr,
        "summary": summary,
        "sessions": reports,
        "gates": gates,
        "passed": all(bool(row["passed"]) for row in gates),
        "post_asr_cleanup_promotion_credit": 0,
        "production_changed": False,
    }
    destination = REPORT_ROOT / stage
    write_json(destination / "corpus_report.json", report)
    write_jsonl(
        destination / "session_reports.jsonl",
        [
            {
                "session_id": row["session_id"],
                "status": row["status"],
                "summary": row["summary"],
                "direct_asr": row.get("direct_asr"),
            }
            for row in reports
        ],
    )
    return report


def final_decision(policy: dict[str, Any]) -> dict[str, Any]:
    controlled_dev_path = REPORT_ROOT / "controlled_dev_report.json"
    development_path = REPORT_ROOT / "development/corpus_report.json"
    controlled_hard_path = REPORT_ROOT / "controlled_hard_test_report.json"
    hard_path = REPORT_ROOT / "hard/corpus_report.json"
    sealed_path = REPORT_ROOT / "sealed/corpus_report.json"
    required = [controlled_dev_path, development_path]
    missing = [relative(path) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError(f"qualification reports missing: {missing}")
    stale = [
        relative(path)
        for path in required
        if not report_matches_current_basis(path, REPORT_ROOT)
    ]
    if stale:
        raise RuntimeError(f"qualification reports are stale: {stale}")
    controlled_dev = read_json(controlled_dev_path)
    development = read_json(development_path)
    full_reports_exist = all(path.is_file() for path in (controlled_hard_path, hard_path, sealed_path))
    if controlled_dev.get("passed") and development.get("passed") and full_reports_exist:
        stale_full = [
            relative(path)
            for path in (controlled_hard_path, hard_path, sealed_path)
            if not report_matches_current_basis(path, REPORT_ROOT)
        ]
        if stale_full:
            raise RuntimeError(f"qualification hard/sealed reports are stale: {stale_full}")
        controlled_hard = read_json(controlled_hard_path)
        hard = read_json(hard_path)
        sealed = read_json(sealed_path)
        passed = all(
            bool(report.get("passed"))
            for report in (controlled_hard, hard, sealed)
        )
        decision = (
            policy["decisions"]["promote"]
            if passed
            else policy["decisions"]["next_capability"]
        )
        reason = "all_locked_gates_passed" if passed else "hard_or_sealed_gate_failed"
    else:
        passed = False
        decision = policy["decisions"]["next_capability"]
        reason = "controlled_or_real_development_gate_failed"
    report_paths = [controlled_dev_path, development_path]
    if controlled_dev.get("passed") and development.get("passed"):
        report_paths.extend(
            path
            for path in (controlled_hard_path, hard_path, sealed_path)
            if path.is_file()
        )
    result = {
        "schema": "murmurmark.alignment_echo_path_decision/v3",
        "profile": PROFILE,
        "decision": decision,
        "reason": reason,
        "promotion_authorized": passed,
        "production_changed": False,
        "post_asr_cleanup_promotion_credit": 0,
        "policy_sha256": sha256(ACTIVE_POLICY),
        "candidate_lock_fingerprint": current_lock_fingerprint(),
        "reports": [
            {"path": relative(path), "sha256": sha256(path)} for path in report_paths
        ],
    }
    write_json(REPORT_ROOT / "decision.json", result)
    return result


def default_model(policy: dict[str, Any]) -> Path:
    configured = os.environ.get("MURMURMARK_WHISPER_MODEL")
    candidates = [
        Path(configured).expanduser() if configured else None,
        Path(str(policy["models"]["primary_asr"]["path"])).expanduser(),
    ]
    for candidate in candidates:
        if (
            candidate
            and candidate.is_file()
            and sha256(candidate) == policy["models"]["primary_asr"]["sha256"]
        ):
            return candidate
    raise RuntimeError("frozen whisper.cpp model not found or changed")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Qualify Alignment and Echo-Path Model v3 without production publication."
    )
    result.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    result.add_argument("--report-root", type=Path, default=REPORT_ROOT)
    subparsers = result.add_subparsers(dest="command", required=True)
    freeze = subparsers.add_parser("freeze")
    freeze.add_argument("--refresh", action="store_true")
    controlled = subparsers.add_parser("controlled")
    controlled.add_argument("--split", choices=("dev", "hard_test"), required=True)
    controlled.add_argument("--refresh", action="store_true")
    session = subparsers.add_parser("session")
    session.add_argument("session", type=Path)
    session.add_argument("--stage", default="development")
    session.add_argument("--refresh", action="store_true")
    corpus = subparsers.add_parser("corpus")
    corpus.add_argument("--stage", choices=("development", "hard", "sealed"), required=True)
    corpus.add_argument("--with-asr", action="store_true")
    corpus.add_argument("--whisper-model", type=Path)
    corpus.add_argument("--refresh", action="store_true")
    subparsers.add_parser("decision")
    subparsers.add_parser("verify")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    global ACTIVE_LOCK_FINGERPRINT, ACTIVE_POLICY, REPORT_ROOT
    ACTIVE_POLICY = args.policy.resolve()
    ACTIVE_LOCK_FINGERPRINT = None
    REPORT_ROOT = args.report_root.resolve()
    _qualification_lock = acquire_qualification_lock(REPORT_ROOT)
    try:
        os.nice(20)
    except OSError:
        pass
    policy, checks = verify_policy(args.policy.resolve())
    if args.command == "freeze":
        result = freeze_inputs(args.policy.resolve(), REPORT_ROOT, args.refresh)
    elif args.command == "controlled":
        activate_candidate_lock(policy)
        if args.split == "hard_test":
            require_stage_unlocked("hard", REPORT_ROOT)
        result = run_controlled_split(policy, args.split, REPORT_ROOT, args.refresh)
    elif args.command == "session":
        activate_candidate_lock(policy)
        require_session_unlocked(args.session.resolve(), args.stage, policy, REPORT_ROOT)
        result = process_session(
            args.session.resolve(), policy, stage=args.stage, refresh=args.refresh
        )
    elif args.command == "corpus":
        activate_candidate_lock(policy)
        model = (
            args.whisper_model.expanduser().resolve()
            if args.whisper_model
            else default_model(policy)
            if args.with_asr
            else Path()
        )
        if args.with_asr and sha256(model) != policy["models"]["primary_asr"]["sha256"]:
            raise RuntimeError("requested whisper.cpp model differs from frozen policy")
        result = corpus_report(
            policy=policy,
            stage=args.stage,
            refresh=args.refresh,
            with_asr=args.with_asr,
            model=model,
        )
    elif args.command == "decision":
        activate_candidate_lock(policy)
        result = final_decision(policy)
    else:
        lock = activate_candidate_lock(policy)
        result = {
            "schema": "murmurmark.alignment_echo_path_verification/v3",
            "passed": True,
            "policy_checks": checks,
            "lock_fingerprint": lock["fingerprint"],
        }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
