#!/usr/bin/env python3
"""Evaluate Microsoft DEC as a fail-open residual echo suppressor.

This is a shadow laboratory. It cannot modify authoritative preprocessing or
promote a candidate without a corpus-wide decision.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from neural_residual_echo import (  # noqa: E402
    AECMOSNoScenario,
    AECMOS_SHA256,
    InferenceRequest,
    MicrosoftDECAdapter,
    MODEL_SHA256,
    SAMPLE_RATE,
    run_fail_open,
    sha256,
)


VERSION = "0.1.0"
PROFILE = "neural_residual_echo_v1"
PRIMARY = "ms_dec_local_fir_v1"
CONTROL = "ms_dec_raw_mic_control_v1"
SESSION_SCHEMA = "murmurmark.neural_residual_echo_session/v1"
CORPUS_SCHEMA = "murmurmark.neural_residual_echo_corpus/v1"
FROZEN_SCHEMA = "murmurmark.neural_residual_echo_frozen_corpus/v1"
DEFAULT_MODEL_DIR = (
    Path.home() / ".local/share/murmurmark/models/neural-residual-echo-v1"
)
DEFAULT_ASR_MODEL = (
    Path.home() / ".local/share/murmurmark/models/faster-whisper/large-v3"
)
DEFAULT_WHISPER_MODEL = (
    Path.home()
    / ".local/share/murmurmark/models/whisper.cpp/ggml-large-v3-q5_0.bin"
)
OLD_REPORT = (
    REPO_ROOT
    / "sessions/_reports/echo-suppression-promotion-v1/"
    "echo_suppression_promotion_corpus_report.json"
)
DEFAULT_CORPUS_OUT = (
    REPO_ROOT / "sessions/_reports/neural-residual-echo-v1"
)
HARD_COUNTEREXAMPLES = (
    "2026-07-20_15-15-26-live",
    "2026-07-20_16-30-42-live",
)
NON_APPLICABLE_MODES = {"headphones_or_low_leak", "no_speech", "uncertain"}
LOCAL_GATES = (
    "local_asr_recall",
    "doubletalk_asr_recall",
    "opening_asr_recall",
    "protected_local_asr_recall",
    "protected_local_evidence_retained",
    "chronology_asr_recall",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Neural Residual Echo Suppression v1 shadow laboratory."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="verify pinned local models")
    add_model_arguments(doctor)
    doctor.add_argument("--manifest", type=Path)

    session = subparsers.add_parser("session", help="evaluate one frozen session")
    session.add_argument("session", type=Path)
    add_model_arguments(session)
    session.add_argument("--refresh", action="store_true")
    session.add_argument("--asr-probes", action="store_true")
    session.add_argument("--determinism-check", action="store_true")
    session.add_argument("--max-windows-per-class", type=int, default=2)

    corpus = subparsers.add_parser(
        "corpus",
        help="evaluate the frozen corpus, hard counterexamples first",
    )
    corpus.add_argument("sessions", nargs="*", type=Path)
    add_model_arguments(corpus)
    corpus.add_argument("--out-dir", type=Path, default=DEFAULT_CORPUS_OUT)
    corpus.add_argument("--run", action="store_true")
    corpus.add_argument("--refresh", action="store_true")
    corpus.add_argument("--max-windows-per-class", type=int, default=2)
    return parser.parse_args()


def add_model_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--model-dir",
        type=Path,
        default=Path(
            os.environ.get("MURMURMARK_NEURAL_ECHO_MODEL_DIR", DEFAULT_MODEL_DIR)
        ),
    )
    parser.add_argument("--faster-whisper-model", type=Path, default=DEFAULT_ASR_MODEL)
    parser.add_argument("--whisper-model", type=Path, default=DEFAULT_WHISPER_MODEL)


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


OLD = load_module(
    SCRIPT_DIR / "echo-suppression-promotion-v1.py",
    "echo_suppression_promotion_v1_for_neural",
)


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def stable_fingerprint(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def relative(path: Path, root: Path = REPO_ROOT) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path.resolve())


def resolve_session(path: Path) -> Path:
    expanded = path.expanduser()
    if not expanded.is_absolute():
        expanded = REPO_ROOT / expanded
    resolved = expanded.resolve()
    if not (resolved / "session.json").exists():
        raise RuntimeError(f"session.json not found: {resolved}")
    return resolved


def old_corpus_report() -> dict[str, Any]:
    payload = read_json(OLD_REPORT)
    if payload.get("schema") != OLD.CORPUS_SCHEMA:
        raise RuntimeError(f"frozen source report is missing or incompatible: {OLD_REPORT}")
    return payload


def frozen_rows() -> dict[str, dict[str, Any]]:
    return {
        str(row["session"]): row
        for row in old_corpus_report().get("sessions", [])
        if isinstance(row, dict) and row.get("session")
    }


def verify_fingerprint(path: Path, expected: dict[str, Any]) -> dict[str, Any]:
    exists = path.exists()
    observed_sha = sha256(path) if exists else None
    observed_bytes = path.stat().st_size if exists else None
    return {
        "path": str(expected.get("path") or ""),
        "exists": exists,
        "expected_sha256": expected.get("sha256"),
        "observed_sha256": observed_sha,
        "expected_bytes": expected.get("bytes"),
        "observed_bytes": observed_bytes,
        "passed": (
            exists
            and observed_sha == expected.get("sha256")
            and observed_bytes == expected.get("bytes")
        ),
    }


def verify_freeze(session: Path, source_row: dict[str, Any]) -> dict[str, Any]:
    freeze = source_row.get("freeze") if isinstance(source_row.get("freeze"), dict) else {}
    checks: list[dict[str, Any]] = []
    for group in ("raw", "risk_evidence"):
        for expected in freeze.get(group, []):
            if isinstance(expected, dict) and expected.get("path"):
                row = verify_fingerprint(session / str(expected["path"]), expected)
                row["group"] = group
                checks.append(row)
    derived = freeze.get("derived_inputs")
    if isinstance(derived, dict):
        for name, expected in derived.items():
            if isinstance(expected, dict) and expected.get("path"):
                row = verify_fingerprint(session / str(expected["path"]), expected)
                row["group"] = "derived_inputs"
                row["name"] = name
                checks.append(row)
    return {
        "schema": "murmurmark.neural_residual_echo_freeze_check/v1",
        "source_report": relative(OLD_REPORT),
        "source_decision_fingerprint": source_row.get("decision_fingerprint"),
        "session": session.name,
        "checks": checks,
        "passed": bool(checks) and all(row["passed"] for row in checks),
    }


def verify_models(model_dir: Path, manifest_path: Path | None = None) -> dict[str, Any]:
    command = [
        sys.executable,
        str(SCRIPT_DIR / "bootstrap-neural-residual-echo-v1.py"),
        "--model-dir",
        str(model_dir.expanduser()),
    ]
    if manifest_path is not None:
        command.extend(["--manifest", str(manifest_path)])
    completed = subprocess.run(
        command,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        payload = {
            "passed": False,
            "reason": f"bootstrap_invalid_output:{completed.stderr.strip()}",
        }
    if manifest_path is not None and not manifest_path.exists():
        write_json(manifest_path, payload)
    return payload


def model_paths(model_dir: Path) -> tuple[Path, Path]:
    directory = model_dir.expanduser().resolve()
    return (
        directory / "dec-baseline-model-icassp2022.onnx",
        directory / "aecmos-16k-no-scenarios.onnx",
    )


def model_hashes_valid(model_dir: Path) -> bool:
    model, aecmos = model_paths(model_dir)
    return (
        model.exists()
        and aecmos.exists()
        and sha256(model) == MODEL_SHA256
        and sha256(aecmos) == AECMOS_SHA256
    )


def candidate_audio_hash(audio: np.ndarray) -> str:
    return hashlib.sha256(
        np.asarray(audio, dtype="<f4").tobytes(order="C")
    ).hexdigest()


def current_code_fingerprints() -> dict[str, str]:
    return {
        path.name: sha256(path)
        for path in (
            Path(__file__),
            SCRIPT_DIR / "neural_residual_echo.py",
            SCRIPT_DIR / "bootstrap-neural-residual-echo-v1.py",
            SCRIPT_DIR / "echo-suppression-promotion-v1.py",
            SCRIPT_DIR / "echo_promotion_timeline.py",
        )
    }


def reusable_session_report(
    session: Path,
    source_row: dict[str, Any],
) -> dict[str, Any] | None:
    output_root = session / "derived/preprocess/neural-residual-echo-v1"
    report = read_json(output_root / "session_decision.json")
    manifest = read_json(output_root / "inference_manifest.json")
    integrity = read_json(output_root / "integrity_report.json")
    bounded = read_json(output_root / "bounded_asr_report.json")
    if (
        report.get("schema") != SESSION_SCHEMA
        or report.get("status") != "evaluated"
        or report.get("model_sha256") != MODEL_SHA256
        or report.get("freeze_passed") is not True
        or any(
            manifest.get("code", {}).get(name) != digest
            for name, digest in current_code_fingerprints().items()
            if name != Path(__file__).name
        )
        or bounded.get("status") != "completed"
        or integrity.get("candidates", {})
        .get(PRIMARY, {})
        .get("determinism", {})
        .get("passed")
        is not True
        or verify_freeze(session, source_row).get("passed") is not True
    ):
        return None
    return report


def probe_windows(
    session: Path,
    canonical: dict[str, Any],
    limit: int,
) -> list[dict[str, Any]]:
    duration = min(canonical["mic"].size, canonical["remote"].size) / SAMPLE_RATE
    rows = OLD.select_probe_windows(canonical["state_rows"], limit, duration)
    rows.extend(OLD.risk_probe_windows(session, duration))
    rows.extend(OLD.evidence_probe_windows(session, duration, limit))
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        key = str(row.get("id") or stable_fingerprint(row))
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def counterexample_probe_windows(
    session: Path,
    canonical: dict[str, Any],
    limit: int,
) -> list[dict[str, Any]]:
    """Keep the mandatory first-stage ASR plan narrow and evidence-driven."""
    duration = min(canonical["mic"].size, canonical["remote"].size) / SAMPLE_RATE
    rows = [
        row
        for row in OLD.select_probe_windows(canonical["state_rows"], 1, duration)
        if row.get("category") in {"double_talk", "opening_local"}
    ]
    rows.extend(OLD.risk_probe_windows(session, duration, limit=2))
    rows.extend(OLD.evidence_probe_windows(session, duration, max(1, limit)))
    result: list[dict[str, Any]] = []
    seen: set[tuple[Any, Any, Any]] = set()
    for row in rows:
        key = (row.get("category"), row.get("start"), row.get("end"))
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def aecmos_secondary(
    *,
    estimator: AECMOSNoScenario,
    windows: list[dict[str, Any]],
    farend: np.ndarray,
    mic: np.ndarray,
    enhanced: np.ndarray,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for window in windows:
        start = max(0, int(round(float(window["start"]) * SAMPLE_RATE)))
        end = min(
            farend.size,
            mic.size,
            enhanced.size,
            int(round(float(window["end"]) * SAMPLE_RATE)),
        )
        if end <= start:
            continue
        try:
            score = estimator.score(
                farend[start:end],
                mic[start:end],
                enhanced[start:end],
            )
            rows.append(
                {
                    "id": window.get("id"),
                    "category": window.get("category"),
                    "start": window.get("start"),
                    "end": window.get("end"),
                    "status": "completed",
                    **score,
                }
            )
        except Exception as error:
            rows.append(
                {
                    "id": window.get("id"),
                    "category": window.get("category"),
                    "start": window.get("start"),
                    "end": window.get("end"),
                    "status": "failed",
                    "reason": f"{type(error).__name__}:{error}",
                }
            )
    completed = [row for row in rows if row["status"] == "completed"]
    return {
        "role": "secondary_non_gating_metric",
        "status": "completed" if completed else "unavailable",
        "windows": rows,
        "mean_echo_mos": OLD.average([float(row["echo_mos"]) for row in completed]),
        "mean_degradation_mos": OLD.average(
            [float(row["degradation_mos"]) for row in completed]
        ),
    }


def protected_report(
    asr_report: dict[str, Any],
    candidate: str,
) -> dict[str, Any]:
    categories = {
        "local_only",
        "opening_local",
        "double_talk",
        "protected_local",
        "chronology_risk",
    }
    rows: list[dict[str, Any]] = []
    for window in asr_report.get("windows", []):
        if not isinstance(window, dict) or window.get("category") not in categories:
            continue
        candidate_rows = (
            window.get("candidates")
            if isinstance(window.get("candidates"), dict)
            else {}
        )
        candidate_row = candidate_rows.get(candidate)
        if not isinstance(candidate_row, dict):
            continue
        recall_values = [
            candidate_row.get("baseline_local_token_recall"),
            candidate_row.get("baseline_nonremote_token_recall"),
            candidate_row.get("evidence_token_recall"),
        ]
        relevant = [float(value) for value in recall_values if value is not None]
        rows.append(
            {
                "id": window.get("id"),
                "category": window.get("category"),
                "start": window.get("start"),
                "end": window.get("end"),
                "baseline_text": window.get("baseline_text"),
                "reference_text": window.get("reference_text"),
                "candidate_text": candidate_row.get("text"),
                "recall": min(relevant) if relevant else None,
                "passed": not relevant or min(relevant) >= 0.99,
            }
        )
    summary = (
        asr_report.get("candidate_summaries", {}).get(candidate, {})
        if isinstance(asr_report.get("candidate_summaries"), dict)
        else {}
    )
    summary_gates = {
        "local_recall_99pct": summary.get("local_token_recall") is None
        or float(summary["local_token_recall"]) >= 0.99,
        "doubletalk_recall_99pct": summary.get("double_talk_token_recall") is None
        or float(summary["double_talk_token_recall"]) >= 0.99,
        "opening_recall_99pct": summary.get("opening_token_recall") is None
        or float(summary["opening_token_recall"]) >= 0.99,
        "protected_recall_99pct": summary.get("protected_local_token_recall") is None
        or float(summary["protected_local_token_recall"]) >= 0.99,
        "protected_evidence_99pct": summary.get(
            "protected_local_evidence_retention"
        )
        is None
        or float(summary["protected_local_evidence_retention"]) >= 0.99,
        "chronology_recall_99pct": summary.get("chronology_token_recall") is None
        or float(summary["chronology_token_recall"]) >= 0.99,
        "each_protected_window_99pct": all(row["passed"] for row in rows),
    }
    return {
        "schema": "murmurmark.neural_residual_echo_protected_local/v1",
        "candidate": candidate,
        "summary": summary,
        "windows": rows,
        "gates": summary_gates,
        "passed": all(summary_gates.values()),
    }


def empty_asr_report(reason: str) -> dict[str, Any]:
    return {
        "schema": "murmurmark.neural_residual_echo_bounded_asr/v1",
        "status": "unavailable",
        "reason": reason,
        "windows": [],
        "candidate_summaries": {},
    }


def session_gate(
    *,
    key: str,
    payload: dict[str, Any],
    inference: dict[str, Any],
    asr_report: dict[str, Any],
    mode: str,
    freeze_passed: bool,
) -> dict[str, Any]:
    summary = (
        asr_report.get("candidate_summaries", {}).get(key)
        if isinstance(asr_report.get("candidate_summaries"), dict)
        else None
    )
    base = OLD.candidate_gates(key, payload, summary, mode)
    metadata = (
        payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    )
    candidate_runtime = metadata.get("engine_runtime_sec")
    baseline_runtime = metadata.get("baseline_engine_runtime_sec")
    incremental_runtime_ratio = (
        float(candidate_runtime) / float(baseline_runtime)
        if candidate_runtime is not None
        and baseline_runtime is not None
        and float(baseline_runtime) > 0.0
        else None
    )
    baseline_remote = float((summary or {}).get("remote_probe_seconds_baseline") or 0.0)
    reduction = (summary or {}).get("remote_probe_reduction_ratio")
    gates = dict(base["gates"])
    gates["ordinary_audio_runtime_overhead_lte_25pct"] = (
        incremental_runtime_ratio is not None
        and incremental_runtime_ratio <= 0.25
    )
    gates.update(
        {
            "frozen_inputs_unchanged": freeze_passed,
            "inference_completed_without_fallback": inference.get("status")
            == "completed",
            "remote_risk_seconds_down_25pct": baseline_remote <= 0.0
            or (reduction is not None and float(reduction) >= 0.25),
        }
    )
    applicable = mode not in NON_APPLICABLE_MODES
    passed = applicable and all(gates.values())
    reasons = [name for name, value in gates.items() if not value]
    if not applicable:
        reasons.append(f"acoustic_mode_{mode}_uses_baseline")
    local_preservation = all(gates.get(name, False) for name in LOCAL_GATES)
    return {
        "candidate": key,
        "applicable": applicable,
        "passed": passed,
        "local_preservation_passed": local_preservation,
        "gates": gates,
        "reasons": reasons,
        "incremental_runtime_ratio": round(incremental_runtime_ratio, 6)
        if incremental_runtime_ratio is not None
        else None,
    }


def rejudge_cached_session(
    session: Path,
    source_row: dict[str, Any],
) -> dict[str, Any] | None:
    report = reusable_session_report(session, source_row)
    if report is None:
        return None
    output_root = session / "derived/preprocess/neural-residual-echo-v1"
    inference_manifest = read_json(output_root / "inference_manifest.json")
    bounded = read_json(output_root / "bounded_asr_report.json")
    freeze = verify_freeze(session, source_row)
    candidates = {
        key: read_json(output_root / "candidates" / key / "metrics.json")
        for key in (PRIMARY, CONTROL)
    }
    inference_rows = (
        inference_manifest.get("candidates")
        if isinstance(inference_manifest.get("candidates"), dict)
        else {}
    )
    mode = str(report.get("mode") or "uncertain")
    decisions = {
        key: session_gate(
            key=key,
            payload=candidates[key],
            inference=inference_rows.get(key, {}),
            asr_report=bounded,
            mode=mode,
            freeze_passed=bool(freeze["passed"]),
        )
        for key in (PRIMARY, CONTROL)
    }
    protected = protected_report(bounded, PRIMARY)
    decision_basis = {
        "session": session.name,
        "mode": mode,
        "status": "evaluated",
        "freeze_passed": freeze["passed"],
        "primary": decisions[PRIMARY],
        "control": decisions[CONTROL],
        "primary_audio_sha256": candidates[PRIMARY]["fingerprints"][
            "canonical_mic_for_asr"
        ]["sha256"],
        "control_audio_sha256": candidates[CONTROL]["fingerprints"][
            "canonical_mic_for_asr"
        ]["sha256"],
        "model_sha256": MODEL_SHA256,
        "bounded_asr_plan": bounded.get("probe_plan_fingerprint"),
    }
    updated = {
        **report,
        **decision_basis,
        "promotion_eligible": decisions[PRIMARY]["passed"],
        "protected_local": protected,
        "evaluation_code_sha256": sha256(Path(__file__)),
        "decision_fingerprint": stable_fingerprint(decision_basis),
    }
    write_json(output_root / "session_decision.json", updated)
    return updated


def write_skipped_session(
    *,
    session: Path,
    source_row: dict[str, Any],
    reason: str,
    model_manifest: dict[str, Any],
) -> dict[str, Any]:
    output_root = session / "derived/preprocess/neural-residual-echo-v1"
    freeze = verify_freeze(session, source_row)
    mode = (
        source_row.get("acoustic_mode", {}).get("mode")
        if isinstance(source_row.get("acoustic_mode"), dict)
        else "uncertain"
    )
    write_json(output_root / "model_manifest.json", model_manifest)
    write_json(output_root / "freeze_manifest.json", freeze)
    decision_basis = {
        "session": session.name,
        "mode": mode,
        "status": "skipped",
        "reason": reason,
        "freeze_passed": freeze["passed"],
        "selected": OLD.BASELINE,
    }
    payload = {
        "schema": SESSION_SCHEMA,
        "profile": PROFILE,
        "generator": {"script": Path(__file__).name, "version": VERSION},
        **decision_basis,
        "promotion_eligible": False,
        "authoritative_pipeline_changed": False,
        "evaluation_code_sha256": sha256(Path(__file__)),
        "decision_fingerprint": stable_fingerprint(decision_basis),
    }
    write_json(output_root / "session_decision.json", payload)
    return payload


def evaluate_session(
    *,
    session: Path,
    source_row: dict[str, Any],
    args: argparse.Namespace,
    model_manifest: dict[str, Any],
    asr_probes: bool,
    determinism_check: bool,
) -> dict[str, Any]:
    output_root = session / "derived/preprocess/neural-residual-echo-v1"
    output_root.mkdir(parents=True, exist_ok=True)
    write_json(output_root / "model_manifest.json", model_manifest)
    freeze = verify_freeze(session, source_row)
    write_json(output_root / "freeze_manifest.json", freeze)
    mode = (
        source_row.get("acoustic_mode", {}).get("mode")
        if isinstance(source_row.get("acoustic_mode"), dict)
        else "uncertain"
    )
    if not freeze["passed"]:
        return write_skipped_session(
            session=session,
            source_row=source_row,
            reason="frozen_input_mismatch_fail_open",
            model_manifest=model_manifest,
        )
    if mode in NON_APPLICABLE_MODES:
        return write_skipped_session(
            session=session,
            source_row=source_row,
            reason=f"acoustic_mode_{mode}_keeps_exact_baseline",
            model_manifest=model_manifest,
        )
    if not model_manifest.get("passed") or not model_hashes_valid(args.model_dir):
        return write_skipped_session(
            session=session,
            source_row=source_row,
            reason="model_verification_failed_exact_baseline",
            model_manifest=model_manifest,
        )

    started = time.monotonic()
    canonical = OLD.canonical_inputs(session, output_root, bool(args.refresh))
    baseline_runtime = OLD.baseline_echo_runtime(session)
    if baseline_runtime is None:
        baseline_runtime = OLD.benchmark_local_fir(
            session,
            output_root,
            canonical["source_fingerprint"],
            bool(args.refresh),
        )
    baseline = OLD.candidate_from_audio(
        key=OLD.BASELINE,
        source="derived/preprocess/audio/mic_role_masked_for_asr.wav",
        engine_native=canonical["local_clean"],
        canonical=canonical,
        output_root=output_root,
        metadata={"engine_runtime_sec": baseline_runtime},
    )
    model_path, aecmos_path = model_paths(args.model_dir)
    suppressor = MicrosoftDECAdapter(model_path)
    estimator = AECMOSNoScenario(aecmos_path)
    candidates: dict[str, dict[str, Any]] = {OLD.BASELINE: baseline}
    inference_rows: dict[str, dict[str, Any]] = {}
    native_audio: dict[str, np.ndarray] = {}
    source_audio = {
        PRIMARY: canonical["local_clean"],
        CONTROL: canonical["mic"],
    }
    for key, source in source_audio.items():
        selected_native, inference = run_fail_open(
            suppressor,
            InferenceRequest(
                mic=source,
                farend=canonical["aligned_remote"],
                sample_rate=SAMPLE_RATE,
            ),
            baseline=canonical["baseline_asr"],
            integrity_reference=source,
        )
        if determinism_check and inference["status"] == "completed":
            replay, replay_meta = run_fail_open(
                MicrosoftDECAdapter(model_path),
                InferenceRequest(
                    mic=source,
                    farend=canonical["aligned_remote"],
                    sample_rate=SAMPLE_RATE,
                ),
                baseline=canonical["baseline_asr"],
                integrity_reference=source,
            )
            inference["determinism"] = {
                "checked": True,
                "first_sha256": candidate_audio_hash(selected_native),
                "replay_sha256": candidate_audio_hash(replay),
                "replay_status": replay_meta["status"],
                "passed": np.array_equal(selected_native, replay),
            }
        else:
            inference["determinism"] = {
                "checked": False,
                "passed": None,
            }
        runtime = inference.get("runtime_sec")
        payload = OLD.candidate_from_audio(
            key=key,
            source=(
                "mic_clean_local_fir + canonical aligned remote"
                if key == PRIMARY
                else "canonical raw mic + canonical aligned remote"
            ),
            engine_native=selected_native,
            canonical=canonical,
            output_root=output_root,
            metadata={
                "engine_runtime_sec": runtime,
                "baseline_engine_runtime_sec": baseline_runtime,
                "fail_open": inference["fail_open"],
                "model_sha256": MODEL_SHA256,
            },
        )
        candidates[key] = payload
        inference_rows[key] = inference
        native_audio[key] = selected_native

    is_hard_counterexample = session.name in HARD_COUNTEREXAMPLES
    windows = (
        counterexample_probe_windows(
            session,
            canonical,
            int(args.max_windows_per_class),
        )
        if is_hard_counterexample
        else probe_windows(session, canonical, int(args.max_windows_per_class))
    )
    secondary: dict[str, Any] = {}
    for key, source in source_audio.items():
        if inference_rows[key]["status"] != "completed":
            secondary[key] = {
                "status": "skipped",
                "reason": "primary_inference_failed_open",
                "role": "secondary_non_gating_metric",
            }
            continue
        secondary[key] = aecmos_secondary(
            estimator=estimator,
            windows=windows,
            farend=canonical["aligned_remote"],
            mic=source,
            enhanced=native_audio[key],
        )

    if asr_probes:
        asr_report = OLD.run_asr_probes(
            session=session,
            output_root=output_root,
            canonical=canonical,
            candidates=candidates,
            model_path=args.faster_whisper_model.expanduser().resolve(),
            max_windows_per_class=int(args.max_windows_per_class),
            refresh=bool(args.refresh),
            windows_override=windows if is_hard_counterexample else None,
            candidate_keys=(
                (OLD.BASELINE, PRIMARY)
                if is_hard_counterexample
                else None
            ),
        )
        if asr_report.get("status") != "completed":
            asr_report = empty_asr_report(
                str(asr_report.get("reason") or "bounded_asr_unavailable")
            )
    else:
        asr_report = empty_asr_report("asr_probes_not_requested")
    write_json(output_root / "bounded_asr_report.json", asr_report)
    protected = protected_report(asr_report, PRIMARY)
    write_json(output_root / "protected_local_report.json", protected)

    decisions = {
        key: session_gate(
            key=key,
            payload=candidates[key],
            inference=inference_rows[key],
            asr_report=asr_report,
            mode=str(mode),
            freeze_passed=bool(freeze["passed"]),
        )
        for key in (PRIMARY, CONTROL)
    }
    code_fingerprints = current_code_fingerprints()
    inference_manifest = {
        "schema": "murmurmark.neural_residual_echo_inference_manifest/v1",
        "profile": PROFILE,
        "session": session.name,
        "inputs": canonical["source_fingerprint"],
        "timeline": canonical["timeline"],
        "models": {
            "suppressor_sha256": MODEL_SHA256,
            "secondary_aecmos_sha256": AECMOS_SHA256,
        },
        "parameters": {
            "sample_rate": SAMPLE_RATE,
            "primary": PRIMARY,
            "control": CONTROL,
            "normalization": "none",
            "fail_open": "exact_local_fir_role_masked",
        },
        "code": code_fingerprints,
        "candidates": inference_rows,
        "secondary_aecmos": secondary,
    }
    write_json(output_root / "inference_manifest.json", inference_manifest)
    write_json(
        output_root / "integrity_report.json",
        {
            "schema": "murmurmark.neural_residual_echo_integrity/v1",
            "freeze": freeze,
            "candidates": {
                key: {
                    "status": inference_rows[key]["status"],
                    "reason": inference_rows[key]["reason"],
                    "fail_open": inference_rows[key]["fail_open"],
                    "integrity": inference_rows[key]["integrity"],
                    "fallback_integrity": inference_rows[key][
                        "fallback_integrity"
                    ],
                    "determinism": inference_rows[key]["determinism"],
                    "audio_fingerprints": candidates[key]["fingerprints"],
                }
                for key in (PRIMARY, CONTROL)
            },
        },
    )
    raw_after = verify_freeze(session, source_row)
    decision_basis = {
        "session": session.name,
        "mode": mode,
        "status": "evaluated",
        "freeze_passed": freeze["passed"] and raw_after["passed"],
        "primary": decisions[PRIMARY],
        "control": decisions[CONTROL],
        "primary_audio_sha256": candidates[PRIMARY]["fingerprints"][
            "canonical_mic_for_asr"
        ]["sha256"],
        "control_audio_sha256": candidates[CONTROL]["fingerprints"][
            "canonical_mic_for_asr"
        ]["sha256"],
        "model_sha256": MODEL_SHA256,
        "bounded_asr_plan": asr_report.get("probe_plan_fingerprint"),
    }
    payload = {
        "schema": SESSION_SCHEMA,
        "profile": PROFILE,
        "generator": {"script": Path(__file__).name, "version": VERSION},
        **decision_basis,
        "promotion_eligible": decisions[PRIMARY]["passed"],
        "selection": {
            "candidate": OLD.BASELINE,
            "reason": "shadow_lab_never_changes_production_without_corpus_promotion",
            "fallback_sha256": canonical["source_fingerprint"][
                "local_fir_role_masked"
            ]["sha256"],
        },
        "protected_local": protected,
        "runtime_sec": round(time.monotonic() - started, 3),
        "authoritative_pipeline_changed": False,
        "decision_fingerprint": stable_fingerprint(decision_basis),
    }
    write_json(output_root / "session_decision.json", payload)
    return payload


def corpus_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Neural Residual Echo Suppression v1",
        "",
        f"Decision: **{report['promotion']['decision']}**",
        "",
        f"Frozen sessions: {report['summary']['sessions']}",
        f"Evaluated speaker-playback sessions: {report['summary']['evaluated']}",
        f"Skipped with explicit reason: {report['summary']['skipped']}",
        "",
        "## Gates",
        "",
    ]
    for key, value in report["promotion"]["gates"].items():
        lines.append(f"- {'PASS' if value else 'FAIL'}: `{key}`")
    lines.extend(["", "## Sessions", ""])
    for row in report["sessions"]:
        primary = row.get("primary") if isinstance(row.get("primary"), dict) else {}
        lines.append(
            f"- `{row['session']}`: {row.get('status')} / "
            f"{'pass' if primary.get('passed') else 'baseline'}"
            f" ({row.get('reason') or ', '.join(primary.get('reasons') or []) or 'ok'})"
        )
    lines.extend(
        [
            "",
            "## Conclusion",
            "",
            report["promotion"]["reason"],
            "",
            "Production remains `local_fir_role_masked`; this laboratory has no apply path.",
            "",
        ]
    )
    return "\n".join(lines)


def full_shadow_gates(
    reports: list[dict[str, Any]],
) -> tuple[dict[str, bool], dict[str, Any]]:
    if not reports:
        return {
            "full_shadow_available": False,
            "remote_caused_review_seconds_down_15pct": False,
            "remote_content_preserved": False,
            "verdict_notes_export_preserved": False,
        }, {}
    baseline = sum(
        float(row.get("baseline_remote_duplicate_in_me_seconds") or 0.0)
        for row in reports
    )
    candidate = sum(
        float(row.get("candidate_remote_duplicate_in_me_seconds") or 0.0)
        for row in reports
    )
    reduction = (baseline - candidate) / max(baseline, 1.0e-9) if baseline else 0.0
    gates = {
        "full_shadow_available": all(row.get("status") == "completed" for row in reports),
        "remote_caused_review_seconds_down_15pct": baseline > 0.0
        and reduction >= 0.15,
        "remote_content_preserved": all(
            row.get("gates", {}).get("remote_dialogue_unchanged") is True
            for row in reports
        ),
        "verdict_notes_export_preserved": all(
            all(
                value is True
                for name, value in row.get("gates", {}).items()
                if name
                in {
                    "verdict_not_worse",
                    "evidence_ids_valid",
                    "notes_exist",
                    "transcript_exists",
                    "overlaps_exist",
                }
            )
            for row in reports
        ),
    }
    return gates, {
        "baseline_remote_caused_review_seconds": round(baseline, 3),
        "candidate_remote_caused_review_seconds": round(candidate, 3),
        "reduction_ratio": round(reduction, 6),
    }


def run_corpus(args: argparse.Namespace) -> dict[str, Any]:
    source_report = old_corpus_report()
    source_rows = frozen_rows()
    if args.sessions:
        selected = [resolve_session(path) for path in args.sessions]
    else:
        selected = [
            (REPO_ROOT / "sessions" / name).resolve()
            for name in source_rows
        ]
    unknown = [session.name for session in selected if session.name not in source_rows]
    if unknown:
        raise RuntimeError(
            "sessions are not in the frozen promotion corpus: " + ", ".join(unknown)
        )
    out_dir = args.out_dir.expanduser()
    if not out_dir.is_absolute():
        out_dir = REPO_ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    model_manifest = verify_models(
        args.model_dir,
        out_dir / "model_manifest.json",
    )
    frozen = {
        "schema": FROZEN_SCHEMA,
        "profile": PROFILE,
        "source": {
            "report": relative(OLD_REPORT),
            "report_sha256": sha256(OLD_REPORT),
            "decision_fingerprint": source_report.get("decision_fingerprint"),
            "decision": source_report.get("promotion", {}).get("decision"),
        },
        "hard_counterexamples": list(HARD_COUNTEREXAMPLES),
        "sessions": [
            {
                "session": session.name,
                "acoustic_mode": source_rows[session.name].get("acoustic_mode"),
                "freeze": source_rows[session.name].get("freeze"),
            }
            for session in selected
        ],
    }
    write_json(out_dir / "frozen_corpus.json", frozen)
    if not args.run:
        return frozen

    previous = read_json(out_dir / "neural_residual_echo_corpus_report.json")
    ordered = sorted(
        selected,
        key=lambda session: (
            0 if session.name in HARD_COUNTEREXAMPLES else 1,
            session.name,
        ),
    )
    reports: dict[str, dict[str, Any]] = {}
    for session in ordered:
        if session.name not in HARD_COUNTEREXAMPLES:
            continue
        cached = (
            None
            if args.refresh
            else rejudge_cached_session(session, source_rows[session.name])
        )
        reports[session.name] = cached or evaluate_session(
            session=session,
            source_row=source_rows[session.name],
            args=args,
            model_manifest=model_manifest,
            asr_probes=True,
            determinism_check=True,
        )
    hard_local_passed = all(
        reports.get(name, {}).get("primary", {}).get(
            "local_preservation_passed"
        )
        is True
        for name in HARD_COUNTEREXAMPLES
        if name in {session.name for session in selected}
    )
    for session in ordered:
        if session.name in reports:
            continue
        source_row = source_rows[session.name]
        mode = source_row.get("acoustic_mode", {}).get("mode")
        if mode in NON_APPLICABLE_MODES:
            reports[session.name] = write_skipped_session(
                session=session,
                source_row=source_row,
                reason=f"acoustic_mode_{mode}_keeps_exact_baseline",
                model_manifest=model_manifest,
            )
        elif not hard_local_passed:
            reports[session.name] = write_skipped_session(
                session=session,
                source_row=source_row,
                reason="not_run_due_mandatory_counterexample_local_loss",
                model_manifest=model_manifest,
            )
        else:
            cached = (
                None
                if args.refresh
                else rejudge_cached_session(session, source_row)
            )
            reports[session.name] = cached or evaluate_session(
                session=session,
                source_row=source_row,
                args=args,
                model_manifest=model_manifest,
                asr_probes=True,
                determinism_check=True,
            )

    speaker_reports = [
        report
        for report in reports.values()
        if report.get("mode") == "speaker_playback"
        and report.get("status") == "evaluated"
    ]
    summaries = [
        report.get("primary", {})
        for report in speaker_reports
        if isinstance(report.get("primary"), dict)
    ]
    bounded_reports = [
        read_json(
            REPO_ROOT
            / "sessions"
            / str(report["session"])
            / "derived/preprocess/neural-residual-echo-v1/bounded_asr_report.json"
        )
        for report in speaker_reports
    ]
    baseline_remote = 0.0
    candidate_remote = 0.0
    local_values: list[float] = []
    protected_values: list[float] = []
    chronology_values: list[float] = []
    double_values: list[float] = []
    opening_values: list[float] = []
    runtime_ratios: list[float] = []
    for bounded in bounded_reports:
        summary = bounded.get("candidate_summaries", {}).get(PRIMARY, {})
        baseline_remote += float(summary.get("remote_probe_seconds_baseline") or 0.0)
        candidate_remote += float(summary.get("remote_probe_seconds_candidate") or 0.0)
        for target, key in (
            (local_values, "local_token_recall"),
            (protected_values, "protected_local_token_recall"),
            (chronology_values, "chronology_token_recall"),
            (double_values, "double_talk_token_recall"),
            (opening_values, "opening_token_recall"),
        ):
            if summary.get(key) is not None:
                target.append(float(summary[key]))
    for row in summaries:
        if row.get("incremental_runtime_ratio") is not None:
            runtime_ratios.append(float(row["incremental_runtime_ratio"]))
    remote_reduction = (
        (baseline_remote - candidate_remote) / baseline_remote
        if baseline_remote > 0.0
        else 0.0
    )
    cheap_gates = {
        "all_frozen_sessions_explained": len(reports) == len(selected),
        "model_and_runtime_available": model_manifest.get("passed") is True,
        "hard_counterexamples_preserve_local_speech": hard_local_passed,
        "all_applicable_sessions_pass": bool(summaries)
        and all(row.get("passed") is True for row in summaries),
        "asr_visible_remote_risk_seconds_down_25pct": baseline_remote > 0.0
        and remote_reduction >= 0.25,
        "confirmed_local_recall_99pct": not local_values
        or min(local_values) >= 0.99,
        "protected_local_recall_99pct": not protected_values
        or min(protected_values) >= 0.99,
        "chronology_no_regression": not chronology_values
        or min(chronology_values) >= 0.99,
        "doubletalk_no_regression": not double_values
        or min(double_values) >= 0.99,
        "opening_no_regression": not opening_values
        or min(opening_values) >= 0.99,
        "deterministic_candidate_audio": all(
            read_json(
                REPO_ROOT
                / "sessions"
                / str(report["session"])
                / "derived/preprocess/neural-residual-echo-v1/integrity_report.json"
            )
            .get("candidates", {})
            .get(PRIMARY, {})
            .get("determinism", {})
            .get("passed")
            is True
            for report in speaker_reports
        ),
        "ordinary_runtime_overhead_lte_25pct": bool(runtime_ratios)
        and max(runtime_ratios) <= 0.25,
    }

    full_shadow_reports: list[dict[str, Any]] = []
    if all(cheap_gates.values()):
        for report in speaker_reports:
            session = REPO_ROOT / "sessions" / str(report["session"])
            output_root = (
                session / "derived/preprocess/neural-residual-echo-v1"
            )
            full_shadow_reports.append(
                OLD.full_shadow_stage(
                    session=session,
                    output_root=output_root,
                    candidate=PRIMARY,
                    whisper_model=args.whisper_model.expanduser().resolve(),
                    refresh=bool(args.refresh),
                )
            )
    shadow_gates, review_metrics = full_shadow_gates(full_shadow_reports)
    gates = {**cheap_gates, **shadow_gates}
    decision = (
        "PROMOTE_NEURAL_RESIDUAL_ECHO_V1"
        if all(gates.values())
        else "DO_NOT_PROMOTE"
    )
    failed = [name for name, passed in gates.items() if not passed]
    reason = (
        "primary_candidate_passed_all_frozen_corpus_gates"
        if decision == "PROMOTE_NEURAL_RESIDUAL_ECHO_V1"
        else "failed_gates:" + ",".join(failed)
    )
    session_rows = [reports[session.name] for session in ordered]
    decision_basis = {
        "frozen_source_sha256": frozen["source"]["report_sha256"],
        "model_sha256": MODEL_SHA256,
        "sessions": [
            {
                "session": row["session"],
                "status": row.get("status"),
                "reason": row.get("reason"),
                "decision_fingerprint": row.get("decision_fingerprint"),
            }
            for row in session_rows
        ],
        "gates": gates,
        "metrics": {
            "baseline_remote_risk_seconds": round(baseline_remote, 3),
            "candidate_remote_risk_seconds": round(candidate_remote, 3),
            "remote_risk_reduction_ratio": round(remote_reduction, 6),
            "minimum_local_recall": min(local_values) if local_values else None,
            "minimum_protected_recall": min(protected_values)
            if protected_values
            else None,
            "minimum_chronology_recall": min(chronology_values)
            if chronology_values
            else None,
            "maximum_incremental_runtime_ratio": max(runtime_ratios)
            if runtime_ratios
            else None,
            **review_metrics,
        },
        "decision": decision,
        "reason": reason,
    }
    fingerprint = stable_fingerprint(decision_basis)
    previous_fingerprint = previous.get("decision_fingerprint")
    report = {
        "schema": CORPUS_SCHEMA,
        "profile": PROFILE,
        "generator": {"script": Path(__file__).name, "version": VERSION},
        "frozen_corpus": relative(out_dir / "frozen_corpus.json"),
        "summary": {
            "sessions": len(selected),
            "evaluated": len(speaker_reports),
            "skipped": len(selected) - len(speaker_reports),
            "hard_counterexamples": len(
                [name for name in HARD_COUNTEREXAMPLES if name in reports]
            ),
        },
        "sessions": session_rows,
        "metrics": decision_basis["metrics"],
        "full_shadow": full_shadow_reports,
        "promotion": {
            "decision": decision,
            "candidate": PRIMARY if decision.startswith("PROMOTE") else None,
            "fallback": OLD.BASELINE,
            "gates": gates,
            "failed_gates": failed,
            "reason": reason,
            "next_hypothesis": (
                "production_shadow_policy_and_new_session_validation"
                if decision.startswith("PROMOTE")
                else "model_or_data_adaptation_required"
            ),
        },
        "determinism": {
            "candidate_audio_replay": cheap_gates[
                "deterministic_candidate_audio"
            ],
            "previous_decision_fingerprint": previous_fingerprint,
            "decision_replay_matches": (
                previous_fingerprint == fingerprint
                if previous_fingerprint is not None
                else None
            ),
        },
        "decision_fingerprint": fingerprint,
        "authoritative_pipeline_changed": False,
    }
    write_json(out_dir / "neural_residual_echo_corpus_report.json", report)
    (out_dir / "neural_residual_echo_corpus_report.md").write_text(
        corpus_markdown(report),
        encoding="utf-8",
    )
    return report


def main() -> int:
    args = parse_args()
    if args.command == "doctor":
        manifest = verify_models(args.model_dir, args.manifest)
        print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if manifest.get("passed") else 2
    if args.command == "session":
        session = resolve_session(args.session)
        rows = frozen_rows()
        if session.name not in rows:
            raise RuntimeError(
                f"session is not in frozen corpus: {session.name}"
            )
        output_root = session / "derived/preprocess/neural-residual-echo-v1"
        manifest = verify_models(args.model_dir, output_root / "model_manifest.json")
        report = evaluate_session(
            session=session,
            source_row=rows[session.name],
            args=args,
            model_manifest=manifest,
            asr_probes=bool(args.asr_probes),
            determinism_check=bool(args.determinism_check),
        )
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.command == "corpus":
        report = run_corpus(args)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    raise RuntimeError(f"unsupported command: {args.command}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2)
