#!/usr/bin/env python3
"""Deterministic checks for Speaker-Preserving Echo Adaptation Corpus v1."""

from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path
from typing import Any

import numpy as np

import speaker_preserving_echo_corpus as core


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = (
    ROOT / "policies/speaker-preserving-echo-adaptation-corpus-v1.json"
)


def load_builder() -> Any:
    path = ROOT / "scripts/build-speaker-preserving-echo-adaptation-corpus-v1.py"
    spec = importlib.util.spec_from_file_location("echo_adaptation_builder", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BUILDER = load_builder()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def test_policy() -> dict[str, Any]:
    policy = core.read_json(POLICY_PATH)
    hard_sessions = set(policy["hard_test"]["sessions"])
    require(
        policy["hard_test"]["training_forbidden"] is True,
        "hard test must be training-forbidden",
    )
    require(
        policy["materialization"]["normalization_forbidden"] is True,
        "normalization must remain forbidden",
    )
    require(
        policy["materialization"]["measured_double_talk_as_target_forbidden"]
        is True,
        "measured double-talk must not become a clean target",
    )
    require(
        policy["privacy"]["network_allowed"] is False,
        "corpus processing must stay offline",
    )
    require(
        float(policy["intervals"]["minimum_state_confidence"]) == 0.85,
        "frozen confidence threshold changed",
    )
    require(
        set(policy["hard_test"]["evidence"]) == hard_sessions,
        "every hard-test session must have frozen evidence",
    )
    for session, evidence in policy["hard_test"]["evidence"].items():
        require(
            bool(evidence.get("path")),
            f"hard-test evidence path missing: {session}",
        )
        require(
            len(str(evidence.get("sha256") or "")) == 64,
            f"hard-test evidence SHA-256 missing: {session}",
        )
    require(
        len(str(policy["split"].get("assignment_seed") or "")) == 64,
        "split assignment seed must be explicit and immutable",
    )
    return policy


def test_splits(policy: dict[str, Any]) -> None:
    hard = set(policy["hard_test"]["sessions"])
    sessions = [
        {
            "session": session,
            "acoustic_mode": {"mode": "speaker_playback"},
        }
        for session in sorted(hard)
    ]
    sessions.extend(
        [
            {
                "session": "speaker-train-a",
                "acoustic_mode": {"mode": "speaker_playback"},
            },
            {
                "session": "speaker-train-b",
                "acoustic_mode": {"mode": "speaker_playback"},
            },
            {
                "session": "speaker-dev",
                "acoustic_mode": {"mode": "speaker_playback"},
            },
            {
                "session": "headphones-a",
                "acoustic_mode": {"mode": "headphones_or_low_leak"},
            },
            {
                "session": "headphones-b",
                "acoustic_mode": {"mode": "headphones_or_low_leak"},
            },
            {
                "session": "no-speech",
                "acoustic_mode": {"mode": "no_speech"},
            },
        ]
    )
    split_seed = policy["split"]["assignment_seed"]
    first = core.assign_session_splits(sessions, policy, split_seed)
    second = core.assign_session_splits(
        list(reversed(sessions)),
        policy,
        split_seed,
    )
    require(first == second, "split assignment must ignore source row order")
    report = core.validate_splits(first, policy)
    require(report["passed"], "valid session-disjoint fixture must pass")
    observed_hard = {
        row["session"] for row in first if row["split"] == "hard_test"
    }
    require(observed_hard == hard, "hard sessions must be isolated exactly")

    leaked = list(first) + [
        {
            "session": first[0]["session"],
            "acoustic_mode": first[0]["acoustic_mode"],
            "split": "train",
        }
    ]
    leaked_report = core.validate_splits(leaked, policy)
    require(
        leaked_report["gates"]["session_disjoint"] is False,
        "session leakage must fail",
    )


def test_role_evidence() -> None:
    utterances = [
        {
            "id": "me-1",
            "start": 0.0,
            "end": 1.5,
            "role": "me",
            "text": "confirmed local",
            "quality": {"role_confidence": 0.95, "needs_review": False},
        },
        {
            "id": "me-review",
            "start": 1.5,
            "end": 2.0,
            "role": "me",
            "text": "uncertain",
            "quality": {"role_confidence": 0.95, "needs_review": True},
        },
    ]
    evidence = core.role_evidence(0.0, 2.0, utterances, "me", 0.9)
    require(
        abs(float(evidence["coverage_ratio"]) - 0.75) < 1.0e-9,
        "review text must not count as confirmed local coverage",
    )
    require(
        evidence["utterances"][0]["text"]["sha256"]
        == core.text_evidence("confirmed local")["sha256"],
        "text provenance must use hashes",
    )
    require(
        "confirmed local" not in json.dumps(evidence),
        "work text must not be copied into evidence",
    )


def test_state_gates(policy: dict[str, Any]) -> None:
    role = {"coverage_ratio": 1.0, "utterances": []}
    absent = {"coverage_ratio": 0.0, "utterances": []}
    remote_row = {
        "start": 0.0,
        "end": 2.0,
        "state": "remote_only",
        "confidence": 0.8,
        "remote_db": -30.0,
        "mic_db": -35.0,
    }
    reasons = BUILDER.preliminary_state_reasons(
        row=remote_row,
        category="remote_only",
        split="train",
        acoustic_mode="speaker_playback",
        me_evidence=absent,
        remote_evidence=role,
        policy=policy,
    )
    require(
        "state_confidence_below_threshold" in reasons,
        "low-confidence remote-only must not become supervision",
    )

    local_row = {
        "start": 0.0,
        "end": 2.0,
        "state": "local_only",
        "confidence": 0.9,
        "remote_db": -65.0,
        "mic_db": -30.0,
    }
    local_reasons = BUILDER.preliminary_state_reasons(
        row=local_row,
        category="local_only",
        split="train",
        acoustic_mode="speaker_playback",
        me_evidence=role,
        remote_evidence=absent,
        policy=policy,
    )
    require(not local_reasons, f"clean local fixture rejected: {local_reasons}")


def test_audio_and_synthetic(policy: dict[str, Any]) -> None:
    sample_count = 2 * core.SAMPLE_RATE
    time = np.arange(sample_count, dtype=np.float32) / core.SAMPLE_RATE
    target = (0.08 * np.sin(2.0 * np.pi * 440.0 * time)).astype(np.float32)
    echo = (0.03 * np.sin(2.0 * np.pi * 730.0 * time)).astype(np.float32)
    first, first_report = core.make_synthetic_pair(
        target,
        echo,
        gain_db=-3.0,
        maximum_peak=0.995,
    )
    second, second_report = core.make_synthetic_pair(
        target,
        echo,
        gain_db=-3.0,
        maximum_peak=0.995,
    )
    require(first is not None and second is not None, "safe pair must materialize")
    require(
        np.array_equal(first["mixture"], second["mixture"]),
        "synthetic materialization must be deterministic",
    )
    require(first_report == second_report, "synthetic reports must be stable")
    require(
        first_report["samples"] == sample_count,
        "synthetic pair must preserve exact duration",
    )
    require(
        float(first_report["reconstruction_max_abs_error"]) <= 1.0e-6,
        "synthetic mixture must reconstruct exactly",
    )
    require(
        np.array_equal(first["target"], target),
        "target must not be normalized or attenuated",
    )

    clipped, clipped_report = core.make_synthetic_pair(
        np.full(sample_count, 0.99, dtype=np.float32),
        np.full(sample_count, 0.2, dtype=np.float32),
        gain_db=0.0,
        maximum_peak=0.995,
    )
    require(clipped is None, "clipping must fail closed")
    require(
        clipped_report["reason"] == "would_clip_without_normalization",
        "clipping rejection must explain why normalization was not used",
    )

    metrics = core.audio_metrics(
        np.array([np.nan, 0.0], dtype=np.float32),
        np.zeros(2, dtype=np.float32),
    )
    require(metrics["finite"] is False, "NaN audio must be rejected")


def test_hash_tamper() -> None:
    with tempfile.TemporaryDirectory(prefix="murmurmark-echo-corpus-check-") as tmp:
        path = Path(tmp) / "input.bin"
        path.write_bytes(b"frozen")
        expected = core.sha256(path)
        BUILDER.verify_expected_file(path, expected)
        path.write_bytes(b"changed")
        try:
            BUILDER.verify_expected_file(path, expected)
        except RuntimeError as error:
            require(
                "frozen input changed" in str(error),
                "hash failure must be explicit",
            )
        else:
            raise AssertionError("tampered frozen input must fail")


def test_coverage_mapping(policy: dict[str, Any]) -> None:
    supervision: list[dict[str, Any]] = []
    for split, local, remote, synthetic in (
        ("train", 120.0, 120.0, 60.0),
        ("dev", 30.0, 30.0, 16.0),
    ):
        supervision.extend(
            [
                {
                    "status": "materialized",
                    "kind": "measured_local_reference",
                    "split": split,
                    "audio": {"duration_sec": local},
                },
                {
                    "status": "materialized",
                    "kind": "measured_remote_echo",
                    "split": split,
                    "audio": {"duration_sec": remote},
                },
                {
                    "status": "materialized",
                    "kind": "synthetic_pair",
                    "split": split,
                    "report": {"duration_sec": synthetic},
                },
            ]
        )
    hard_rows = [
        {
            "session": "hard-a",
            "start": 0.0,
            "end": 4.0,
            "status": "included",
            "category": "double_talk",
        },
        {
            "session": "hard-b",
            "start": 0.0,
            "end": 4.0,
            "status": "included",
            "category": "double_talk",
        },
    ]
    hard_rows.extend(
        {
            "session": "hard-a" if index % 2 == 0 else "hard-b",
            "start": 10.0 + index,
            "end": 11.0 + index,
            "status": "included",
            "category": category,
        }
        for category, count in (
            ("protected_local", 4),
            ("chronology_boundary", 4),
            ("opening_ack", 2),
        )
        for index in range(count)
    )
    split_report = {
        "gates": {
            "session_disjoint": True,
            "hard_test_exact": True,
            "minimum_train_sessions": True,
            "minimum_dev_sessions": True,
            "minimum_hard_test_sessions": True,
            "minimum_train_speaker_playback_sessions": True,
            "minimum_dev_speaker_playback_sessions": True,
        }
    }
    report = BUILDER.coverage_report(
        supervision,
        hard_rows,
        split_report,
        policy,
    )
    require(report["passed"], f"threshold boundary fixture failed: {report}")


def test_existing_result_if_present() -> None:
    output = (
        ROOT / "sessions/_reports/speaker-preserving-echo-adaptation-corpus-v1"
    )
    decision_path = output / "corpus_decision.json"
    replay_path = output / "replay_report.json"
    if not decision_path.exists():
        return
    decision = core.read_json(decision_path)
    require(
        decision["authoritative_pipeline_changed"] is False,
        "real corpus must not change production",
    )
    require(
        decision["training_performed"] is False,
        "corpus goal must not train",
    )
    require(
        decision["decision"] in {"READY_FOR_ADAPTATION", "DO_NOT_TRAIN"},
        "real corpus decision is invalid",
    )
    if replay_path.exists():
        replay = core.read_json(replay_path)
        require(replay["passed"] is True, "real corpus replay must pass")


def main() -> int:
    policy = test_policy()
    test_splits(policy)
    test_role_evidence()
    test_state_gates(policy)
    test_audio_and_synthetic(policy)
    test_hash_tamper()
    test_coverage_mapping(policy)
    test_existing_result_if_present()
    print("speaker-preserving echo adaptation corpus checks ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
