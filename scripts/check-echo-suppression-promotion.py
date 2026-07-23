#!/usr/bin/env python3
"""Unit checks for conservative Echo Suppression Promotion behavior."""

from __future__ import annotations

import argparse
import importlib.util
import json
import tempfile
from pathlib import Path

import numpy as np
from scipy.io import wavfile


ROOT = Path(__file__).resolve().parent.parent


def load_module():
    path = ROOT / "scripts/echo-suppression-promotion-v1.py"
    spec = importlib.util.spec_from_file_location("echo_suppression_promotion_v1", path)
    if spec is None or spec.loader is None:
        raise SystemExit("cannot load echo suppression promotion module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def role_and_gate_checks(module) -> None:
    sample_rate = 16_000
    raw = np.ones(sample_rate * 4, dtype=np.float32) * 0.2
    raw[sample_rate * 3 :] = 0.0
    clean = np.ones_like(raw) * 0.05
    rows = [
        {"start": 0.0, "end": 1.0, "state": "local_only"},
        {"start": 1.0, "end": 2.0, "state": "remote_only"},
        {"start": 2.0, "end": 3.0, "state": "double_talk"},
        {"start": 3.0, "end": 4.0, "state": "silence"},
    ]
    output = module.canonical_role_audio(
        raw_mic=raw,
        engine_native=clean,
        state_rows=rows,
        sample_rate=sample_rate,
    )
    require(np.allclose(output[:sample_rate], 0.2), "local-only audio must come from raw mic")
    require(np.allclose(output[sample_rate : sample_rate * 2], 0.05), "remote-only audio must use candidate")
    require(np.allclose(output[sample_rate * 2 : sample_rate * 3], 0.05), "double-talk must use candidate")
    require(np.allclose(output[sample_rate * 3 :], 0.0), "silence must stay silent")
    remote_only_gate = module.canonical_role_audio(
        raw_mic=raw,
        engine_native=clean,
        state_rows=rows,
        sample_rate=sample_rate,
        replace_double_talk=False,
    )
    require(
        np.allclose(remote_only_gate[sample_rate * 2 : sample_rate * 3], 0.2),
        "remote-only gate must preserve baseline double-talk",
    )

    with tempfile.TemporaryDirectory(prefix="murmurmark-echo-baseline-") as temporary:
        output_root = Path(temporary)
        baseline_payload = module.candidate_from_audio(
            key=module.BASELINE,
            source="fixture",
            engine_native=clean,
            canonical={
                "mic": raw,
                "baseline_asr": raw,
                "aligned_remote": np.zeros_like(raw),
                "state_rows": rows,
                "timeline": {
                    "schema": "murmurmark.echo.timeline_contract/v1",
                    "aligned_remote_sha256": "fixture",
                },
            },
            output_root=output_root,
        )
        _, baseline_audio = wavfile.read(
            output_root / baseline_payload["outputs"]["canonical_mic_for_asr"]
        )
        require(
            np.allclose(baseline_audio, raw),
            "laboratory baseline must be the actual role-aware ASR input",
        )

    safe_payload = {
        "metadata": {
            "engine_runtime_sec": 10.0,
            "baseline_engine_runtime_sec": 9.0,
        },
        "metrics": {
            "finite": True,
            "peak": 0.5,
            "clipped_ratio": 0.0,
            "baseline_peak": 0.5,
            "baseline_clipped_ratio": 0.0,
            "protected_speech_seconds": 3.0,
            "active_protection_ratio": 1.0,
            "remote_only": {
                "seconds": 2.0,
                "remote_correlation": 0.01,
                "baseline_remote_correlation": 0.2,
                "energy_delta_db": -4.0,
            },
            "local_only": {
                "seconds": 2.0,
                "waveform_correlation_to_baseline": 1.0,
                "energy_delta_db": 0.0,
            },
            "double_talk": {
                "seconds": 1.0,
                "waveform_correlation_to_baseline": 1.0,
                "energy_delta_db": 0.0,
            },
        }
    }
    probe = {
        "remote_probe_seconds_baseline": 8.0,
        "remote_probe_reduction_ratio": 0.5,
        "local_token_recall": 1.0,
        "double_talk_token_recall": 1.0,
        "opening_token_recall": 1.0,
        "protected_local_token_recall": 1.0,
        "protected_local_evidence_retention": 1.0,
        "chronology_token_recall": 1.0,
    }
    first = module.candidate_gates(module.COVERAGE, safe_payload, probe, "speaker_playback")
    second = module.candidate_gates(module.COVERAGE, safe_payload, probe, "speaker_playback")
    require(first == second and first["passed"] is True, "safe gates must be deterministic")
    require(
        module.audio_candidate_gates(module.COVERAGE, safe_payload, "speaker_playback")["passed"]
        is True,
        "safe candidate must pass before bounded ASR",
    )
    require(
        module.selected_candidate({module.COVERAGE: first}, "headphones_or_low_leak")[0] == module.BASELINE,
        "low-leak mode must keep local_fir",
    )
    silence_cheat = json.loads(json.dumps(safe_payload))
    silence_cheat["metrics"]["active_protection_ratio"] = 0.1
    blocked = module.candidate_gates(module.COVERAGE, silence_cheat, probe, "speaker_playback")
    require(blocked["passed"] is False, "silence/attenuation cheat must fail")
    protected_loss = dict(probe)
    protected_loss["protected_local_token_recall"] = 0.8
    blocked = module.candidate_gates(
        module.COVERAGE,
        safe_payload,
        protected_loss,
        "speaker_playback",
    )
    require(blocked["passed"] is False, "protected local loss must block promotion")


def evidence_probe_checks(module) -> None:
    require(
        module.subtract_remote_tokens(
            "передавай ему привет ладно давай",
            "передавай ему привет",
        )
        == "ладно давай",
        "protected evidence must exclude authoritative remote tokens",
    )
    with tempfile.TemporaryDirectory(prefix="murmurmark-echo-evidence-") as temporary:
        session = Path(temporary)
        local_path = session / "derived/audit/local-recall/local_recall_items.jsonl"
        order_path = session / "derived/audit/order/transcript_order_items.jsonl"
        local_path.parent.mkdir(parents=True)
        order_path.parent.mkdir(parents=True)
        local_path.write_text(
            json.dumps(
                {
                    "item_id": "local_1",
                    "label": "possible_lost_me",
                    "confidence": 0.9,
                    "start_sec": 10.0,
                    "end_sec": 12.0,
                    "parent_text": "Нужно сохранить мою реплику",
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        order_path.write_text(
            json.dumps(
                {
                    "item_id": "order_1",
                    "label": "likely_timing_overlap",
                    "confidence": 0.8,
                    "interval": {"start": 20.0, "end": 21.0},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        windows = module.evidence_probe_windows(session, duration=30.0, limit=2)
        by_category = {row["category"]: row for row in windows}
        require("protected_local" in by_category, "local-recall evidence must become a probe")
        require("chronology_risk" in by_category, "order evidence must become a probe")
        require(
            by_category["protected_local"]["reference_text"]
            == "Нужно сохранить мою реплику",
            "protected probe must preserve its textual reference",
        )
        require(
            by_category["chronology_risk"]["start"] == 19.5,
            "order interval must receive bounded context",
        )


def nonlinear_echo_check(module) -> None:
    sample_rate = 16_000
    random = np.random.default_rng(7)
    remote = random.normal(0.0, 0.12, sample_rate * 2).astype(np.float32)
    baseline = np.zeros_like(remote)
    baseline[:sample_rate] = (
        0.45 * remote[:sample_rate] + 1.8 * remote[:sample_rate] ** 3
    )
    baseline[sample_rate:] = random.normal(0.0, 0.08, sample_rate).astype(np.float32)
    candidate = baseline.copy()
    candidate[:sample_rate] = 0.0
    rows = [
        {"start": 0.0, "end": 1.0, "state": "remote_only"},
        {"start": 1.0, "end": 2.0, "state": "local_only"},
    ]
    metrics = module.audio_metrics(
        candidate=candidate,
        baseline=baseline,
        aligned_remote=remote,
        state_rows=rows,
    )
    result = module.audio_candidate_gates(
        module.OFFLINE,
        {"metrics": metrics},
        "speaker_playback",
    )
    require(result["passed"] is True, "nonlinear remote residue should pass when local audio is intact")
    damaged = candidate.copy()
    damaged[sample_rate:] = 0.0
    damaged_metrics = module.audio_metrics(
        candidate=damaged,
        baseline=baseline,
        aligned_remote=remote,
        state_rows=rows,
    )
    damaged_result = module.audio_candidate_gates(
        module.OFFLINE,
        {"metrics": damaged_metrics},
        "speaker_playback",
    )
    require(damaged_result["passed"] is False, "local deletion must never pass as echo removal")


def policy_checks(module) -> None:
    with tempfile.TemporaryDirectory(prefix="murmurmark-echo-policy-") as temporary:
        session = Path(temporary) / "session"
        audio = session / "derived/preprocess/audio"
        audio.mkdir(parents=True)
        baseline = np.linspace(-0.2, 0.2, 1_600, dtype=np.float32)
        wrong = np.zeros_like(baseline)
        wavfile.write(audio / "mic_role_masked_for_asr.wav", 16_000, baseline)
        wavfile.write(audio / "mic_for_asr.wav", 16_000, wrong)
        policy = Path(temporary) / "policy.json"
        policy.write_text(
            json.dumps(
                {
                    "schema": module.POLICY_SCHEMA,
                    "decision": "DO_NOT_PROMOTE",
                    "candidate": None,
                    "fallback": module.BASELINE,
                }
            ),
            encoding="utf-8",
        )
        result = module.apply_policy(argparse.Namespace(session=session, policy=policy))
        require(result["selected"] == module.BASELINE, "DO_NOT_PROMOTE must select baseline")
        require(
            module.sha256(audio / "mic_for_asr.wav")
            == module.sha256(audio / "mic_role_masked_for_asr.wav"),
            "fallback must restore canonical local_fir ASR input",
        )

        policy.write_text(
            json.dumps(
                {
                    "schema": module.POLICY_SCHEMA,
                    "decision": "PROMOTE_ECHO_SUPPRESSION_V1",
                    "candidate": module.COVERAGE,
                    "corpus_report": "missing.json",
                    "corpus_report_sha256": "deadbeef",
                }
            ),
            encoding="utf-8",
        )
        stale = module.apply_policy(argparse.Namespace(session=session, policy=policy))
        require(stale["selected"] == module.BASELINE, "stale policy evidence must fail open")
        require(stale["stale_evidence"] is True, "stale evidence must be explicit")

        corpus = Path(temporary) / "corpus.json"
        corpus.write_text(
            json.dumps(
                {
                    "promotion": {
                        "decision": "PROMOTE_ECHO_SUPPRESSION_V1",
                        "candidate": module.COVERAGE,
                    }
                }
            ),
            encoding="utf-8",
        )
        policy.write_text(
            json.dumps(
                {
                    "schema": module.POLICY_SCHEMA,
                    "decision": "PROMOTE_ECHO_SUPPRESSION_V1",
                    "candidate": module.COVERAGE,
                    "corpus_report": str(corpus),
                    "corpus_report_sha256": module.sha256(corpus),
                }
            ),
            encoding="utf-8",
        )
        candidate_path = (
            session
            / "derived/preprocess/echo-promotion-v1/candidates"
            / module.COVERAGE
            / "mic_for_asr.wav"
        )
        candidate_path.parent.mkdir(parents=True)
        promoted = baseline * 0.5
        wavfile.write(candidate_path, 16_000, promoted)
        safe_metrics = {
            "finite": True,
            "peak": 0.5,
            "clipped_ratio": 0.0,
            "baseline_peak": 0.5,
            "baseline_clipped_ratio": 0.0,
            "protected_speech_seconds": 1.0,
            "active_protection_ratio": 1.0,
            "remote_only": {
                "seconds": 1.0,
                "remote_correlation": 0.01,
                "baseline_remote_correlation": 0.2,
                "energy_delta_db": -4.0,
            },
            "local_only": {
                "seconds": 1.0,
                "waveform_correlation_to_baseline": 1.0,
                "energy_delta_db": 0.0,
            },
            "double_talk": {
                "seconds": 1.0,
                "waveform_correlation_to_baseline": 1.0,
                "energy_delta_db": 0.0,
            },
        }
        original_mode = module.acoustic_mode
        original_inputs = module.canonical_inputs
        original_materialize = module.materialize_candidates
        try:
            module.acoustic_mode = lambda _session: {"mode": "speaker_playback"}
            module.canonical_inputs = lambda *_args, **_kwargs: {}
            module.materialize_candidates = lambda **_kwargs: (
                {
                    module.BASELINE: {},
                    module.COVERAGE: {
                        "metrics": safe_metrics,
                        "fingerprints": {},
                    },
                },
                {},
            )
            result = module.apply_policy(argparse.Namespace(session=session, policy=policy))
        finally:
            module.acoustic_mode = original_mode
            module.canonical_inputs = original_inputs
            module.materialize_candidates = original_materialize
        require(result["selected"] == module.COVERAGE, "valid policy must materialize its candidate")
        require(result["candidate_applied"] is True, "valid candidate must replace ASR input")
        require(
            module.sha256(audio / "mic_for_asr.wav") == module.sha256(candidate_path),
            "production selection must copy the materialized candidate",
        )

        original_mode = module.acoustic_mode
        original_inputs = module.canonical_inputs
        original_materialize = module.materialize_candidates
        try:
            module.acoustic_mode = lambda _session: {"mode": "speaker_playback"}
            module.canonical_inputs = lambda *_args, **_kwargs: {}

            def fail_materialization(**_kwargs):
                raise RuntimeError("fixture helper failure")

            module.materialize_candidates = fail_materialization
            failed = module.apply_policy(argparse.Namespace(session=session, policy=policy))
        finally:
            module.acoustic_mode = original_mode
            module.canonical_inputs = original_inputs
            module.materialize_candidates = original_materialize
        require(failed["selected"] == module.BASELINE, "helper failure must fail open")
        require(failed["candidate_applied"] is False, "helper failure must not touch the candidate")
        require(
            module.sha256(audio / "mic_for_asr.wav")
            == module.sha256(audio / "mic_role_masked_for_asr.wav"),
            "helper failure must restore local_fir",
        )


def corpus_metric_checks(module) -> None:
    candidate = {
        "asr_probe": {
            "remote_probe_seconds_baseline": 10.0,
            "remote_probe_seconds_candidate": 5.0,
            "local_token_recall": 1.0,
            "double_talk_token_recall": 1.0,
            "protected_local_token_recall": 1.0,
            "protected_local_evidence_retention": 1.0,
            "chronology_token_recall": 1.0,
        },
        "metadata": {"engine_runtime_sec": 10.0},
        "full_shadow": {
            "status": "completed",
            "passed": True,
            "determinism": {"replay_verified": True},
            "metrics": {
                "remote_duplicate_in_me_seconds_baseline": 20.0,
                "remote_duplicate_in_me_seconds_candidate": 10.0,
                "remote_caused_review_seconds_baseline": 20.0,
                "remote_caused_review_seconds_candidate": 10.0,
            },
        },
    }
    rows = [
        {
            "candidates": {
                module.BASELINE: {"metadata": {"engine_runtime_sec": 9.0}},
                module.COVERAGE: candidate,
            },
            "decisions": {module.COVERAGE: {"applicable": True, "passed": True}},
        }
    ]
    metrics = module.corpus_candidate_metrics(rows, module.COVERAGE)
    require(metrics["remote_probe_reduction_ratio"] == 0.5, "probe reduction aggregation changed")
    require(
        metrics["full_shadow_remote_duplicate_reduction_ratio"] == 0.5,
        "full-shadow duplicate reduction aggregation changed",
    )
    require(metrics["remote_caused_review_reduction_ratio"] == 0.5, "review reduction aggregation changed")
    require(metrics["full_shadow_all_passed"] is True, "full-shadow no-regression gate changed")
    require(metrics["full_shadow_deterministic"] is True, "full-shadow replay gate changed")


def main() -> int:
    module = load_module()
    role_and_gate_checks(module)
    evidence_probe_checks(module)
    nonlinear_echo_check(module)
    policy_checks(module)
    corpus_metric_checks(module)
    print("echo suppression promotion checks ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
