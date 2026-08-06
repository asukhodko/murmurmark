#!/usr/bin/env python3
"""Deterministic DSP and fail-open checks for Alignment/Echo-Path v3."""

from __future__ import annotations

import copy
import json
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy import signal

import alignment_echo_path_model_v3 as v3


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def write_wav(path: Path, values: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, values, v3.SAMPLE_RATE, subtype="PCM_16")


def delay_and_path_checks() -> None:
    rng = np.random.default_rng(20260806)
    reference = rng.normal(0.0, 0.12, 4 * v3.SAMPLE_RATE)
    reference_before = reference.copy()
    for expected in (173, -211):
        microphone = v3.extract_aligned(reference, 0, reference.size, expected)
        microphone_before = microphone.copy()
        observed, confidence = v3.estimate_delay_samples(
            microphone, reference, int(0.12 * v3.SAMPLE_RATE)
        )
        require(abs(observed - expected) <= 1, f"delay sign mismatch: {expected=} {observed=}")
        require(confidence > 0.9, f"delay confidence too low: {confidence}")
        require(np.array_equal(microphone, microphone_before), "delay estimator mutated mic")
        require(np.array_equal(reference, reference_before), "delay estimator mutated remote")

    impulse = np.zeros(320)
    impulse[91] = 0.48
    impulse[177] = -0.12
    target = signal.lfilter(impulse, [1.0], reference)
    target_before = target.copy()
    fitted = v3.fit_fir(reference, target, 256, 0.001)
    require(np.array_equal(reference, reference_before), "FIR fit mutated remote")
    require(np.array_equal(target, target_before), "FIR fit mutated target")
    residual = target - signal.lfilter(fitted, [1.0], reference)
    require(v3.rms_db(target) - v3.rms_db(residual) > 30.0, "FIR path recovery regressed")

    correlation_left = reference.copy()
    correlation_right = target.copy()
    v3.normalized_correlation(correlation_left, correlation_right)
    require(np.array_equal(correlation_left, reference), "correlation mutated left input")
    require(np.array_equal(correlation_right, target), "correlation mutated right input")


def nonlinear_ladder_check(policy: dict[str, object]) -> None:
    rng = np.random.default_rng(73)
    reference = signal.lfilter(
        [1.0], [1.0, -0.65], rng.normal(0.0, 0.14, 7 * v3.SAMPLE_RATE)
    )
    nonlinear = reference * np.abs(reference)
    target = 0.18 * reference + 2.2 * nonlinear
    context = 4 * v3.SAMPLE_RATE
    validation = int(0.75 * v3.SAMPLE_RATE)
    model, attempts = v3.choose_model(
        fit_reference=reference[:context],
        fit_target=target[:context],
        validation_reference=reference[context : context + validation],
        validation_target=target[context : context + validation],
        history_reference=reference[: context + validation],
        validation_offset=context,
        policy=policy,
    )
    require(model is not None, "nonlinear synthetic path produced no safe model")
    require(any(row.get("family") == "hammerstein" for row in attempts), "nonlinear rung was not evaluated")
    require(len(model["bases"]) > 1, "strong nonlinear path did not select Hammerstein rung")


def session_fail_open_check(policy: dict[str, object]) -> None:
    policy = copy.deepcopy(policy)
    policy["candidate_ladder"][3]["maximum_energy_reduction_db"] = 50.0
    rng = np.random.default_rng(97)
    duration_sec = 22
    count = duration_sec * v3.SAMPLE_RATE
    remote = signal.lfilter(
        [1.0], [1.0, -0.72], rng.normal(0.0, 0.12, count)
    )
    delay = 137
    aligned = v3.extract_aligned(remote, 0, count, delay)
    first_path = signal.lfilter([0.38, 0.15, -0.07], [1.0], aligned)
    second_path = signal.lfilter([0.24, -0.11, 0.06, 0.03], [1.0], aligned)
    echo = first_path.copy()
    echo[12 * v3.SAMPLE_RATE :] = second_path[12 * v3.SAMPLE_RATE :]
    baseline = echo + rng.normal(0.0, 0.001, count)
    local = 0.2 * np.sin(2.0 * np.pi * 233.0 * np.arange(4 * v3.SAMPLE_RATE) / v3.SAMPLE_RATE)
    baseline[: local.size] = local
    raw = echo.copy()
    raw[: local.size] += local
    states: list[dict[str, object]] = []
    for start in range(0, duration_sec, 2):
        state = "local_only" if start < 4 else "remote_only"
        states.append(
            {
                "start": float(start),
                "end": float(start + 2),
                "state": state,
                "confidence": 0.9,
                "delay_ms": delay * 1000.0 / v3.SAMPLE_RATE,
                "remote_db": -20.0,
                "mic_db": -30.0,
            }
        )
    with tempfile.TemporaryDirectory(prefix="murmurmark-v3-") as temporary:
        session = Path(temporary) / "synthetic-session"
        write_wav(session / "derived/preprocess/audio/mic_for_asr.wav", baseline)
        write_wav(session / "derived/preprocess/audio/remote_for_aec.wav", remote)
        write_wav(session / "derived/asr/mic.wav", raw)
        write_jsonl(session / "derived/preprocess/echo/speaker_state.jsonl", states)
        (session / "session.json").write_text("{}\n", encoding="utf-8")
        first = v3.process_session(
            session, policy, stage="synthetic", refresh=True
        )
        candidate = v3.load_pcm16_16k(
            session / v3.SESSION_OUTPUT / "selected_clean_mic_pcm16.wav"
        )
        source = v3.load_pcm16_16k(
            session / "derived/preprocess/audio/mic_for_asr.wav"
        )
        require(first["summary"]["changed_seconds"] > 1.0, "safe remote-only path was not changed")
        require(first["summary"]["outside_eligible_changed_samples"] == 0, "candidate escaped eligible mask")
        require(np.array_equal(candidate[: 4 * v3.SAMPLE_RATE], source[: 4 * v3.SAMPLE_RATE]), "local-only audio changed")
        first_hash = v3.sha256(session / v3.SESSION_OUTPUT / "selected_clean_mic_pcm16.wav")
        second = v3.process_session(
            session, policy, stage="synthetic", refresh=True
        )
        second_hash = v3.sha256(session / v3.SESSION_OUTPUT / "selected_clean_mic_pcm16.wav")
        require(first_hash == second_hash, "candidate is not deterministic")
        require(first["summary"] == second["summary"], "deterministic summary changed")

        no_remote_policy = copy.deepcopy(policy)
        no_remote_policy["audio_contract"]["eligible_states"] = []
        fallback = v3.process_session(
            session, no_remote_policy, stage="synthetic-fallback", refresh=True
        )
        fallback_audio = v3.load_pcm16_16k(
            session / v3.SESSION_OUTPUT / "selected_clean_mic_pcm16.wav"
        )
        require(fallback["status"] == "exact_fallback", "empty evidence did not fail open")
        require(np.array_equal(fallback_audio, source), "exact fallback is not bit-exact")


def stage_lock_check() -> None:
    with tempfile.TemporaryDirectory(prefix="murmurmark-v3-stage-lock-") as temporary:
        report_root = Path(temporary)
        v3.require_stage_unlocked("development", report_root)
        (report_root / "candidate_lock.json").write_text(
            '{"fingerprint": "test-lock"}\n', encoding="utf-8"
        )
        try:
            v3.require_stage_unlocked("hard", report_root)
        except RuntimeError as error:
            require("locked" in str(error), "hard-stage lock has no explicit reason")
        else:
            raise AssertionError("hard stage opened without development reports")

        (report_root / "controlled_dev_report.json").write_text(
            json.dumps(
                {
                    "passed": True,
                    "candidate_lock_fingerprint": "test-lock",
                    "policy_sha256": v3.sha256(v3.ACTIVE_POLICY),
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (report_root / "development").mkdir()
        development_report = report_root / "development/corpus_report.json"
        report_basis = {
            "candidate_lock_fingerprint": "test-lock",
            "policy_sha256": v3.sha256(v3.ACTIVE_POLICY),
        }
        development_report.write_text(
            json.dumps({**report_basis, "passed": False}) + "\n",
            encoding="utf-8",
        )
        try:
            v3.require_stage_unlocked("hard", report_root)
        except RuntimeError as error:
            require("did not pass" in str(error), "failed development gate has no reason")
        else:
            raise AssertionError("hard stage opened after a failed development gate")

        development_report.write_text(
            json.dumps({**report_basis, "passed": True}) + "\n",
            encoding="utf-8",
        )
        v3.require_stage_unlocked("hard", report_root)
        (report_root / "candidate_lock.json").write_text(
            '{"fingerprint": "changed-lock"}\n', encoding="utf-8"
        )
        try:
            v3.require_stage_unlocked("hard", report_root)
        except RuntimeError as error:
            require("stale" in str(error), "stale prerequisite has no explicit reason")
        else:
            raise AssertionError("stale development reports opened hard stage")
        (report_root / "candidate_lock.json").write_text(
            '{"fingerprint": "test-lock"}\n', encoding="utf-8"
        )
        try:
            v3.require_stage_unlocked("sealed", report_root)
        except RuntimeError as error:
            require("locked" in str(error), "sealed-stage lock has no explicit reason")
        else:
            raise AssertionError("sealed stage opened without hard reports")

        policy = {
            "sets": {
                "hard": ["hard-session"],
                "sealed": ["sealed-session"],
            }
        }
        v3.require_session_unlocked(
            Path("hard-session"), "development", policy, report_root
        )

        try:
            v3.require_session_unlocked(
                Path("sealed-session"), "development", policy, report_root
            )
        except RuntimeError as error:
            require("sealed" in str(error), "sealed session bypass lacks explicit reason")
        else:
            raise AssertionError("sealed session opened through development alias")


def process_lock_check() -> None:
    with tempfile.TemporaryDirectory(prefix="murmurmark-v3-process-lock-") as temporary:
        report_root = Path(temporary)
        first = v3.acquire_qualification_lock(report_root)
        try:
            try:
                v3.acquire_qualification_lock(report_root)
            except RuntimeError as error:
                require(
                    "already running" in str(error),
                    "concurrent qualification has no explicit reason",
                )
            else:
                raise AssertionError("concurrent qualification acquired the report lock")
        finally:
            first.close()

        second = v3.acquire_qualification_lock(report_root)
        second.close()


def main() -> int:
    policy = v3.read_json(v3.DEFAULT_POLICY)
    require(
        policy.get("schema") == "murmurmark.alignment_echo_path_model_policy/v3",
        "unexpected policy schema",
    )
    delay_and_path_checks()
    nonlinear_ladder_check(policy)
    session_fail_open_check(policy)
    stage_lock_check()
    process_lock_check()
    print("alignment and echo-path model v3 checks ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
