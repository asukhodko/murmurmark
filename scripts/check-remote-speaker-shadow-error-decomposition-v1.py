#!/usr/bin/env python3
"""Check Remote Speaker Shadow Error Decomposition v1 contracts."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf


ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv/bin/python"
ANALYZER = ROOT / "scripts/analyze-remote-speaker-shadow-errors-v1.py"
POLICY = ROOT / "policies/remote-speaker-shadow-error-decomposition-v1.json"
TRACKED = ROOT / "docs/testing/remote-speaker-shadow-error-decomposition-v1-manifest.json"
REAL_OUT = ROOT / "sessions/_reports/remote-speaker-shadow-error-decomposition-v1"


def load_analyzer() -> Any:
    spec = importlib.util.spec_from_file_location("shadow_error_decomposition_v1", ANALYZER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ANALYSIS = load_analyzer()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict), path
    return value


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def assert_public_safe(value: Any) -> None:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True)
    for marker in ("/Users/", "/home/", '"text"', '"embedding"', "reference_speaker_", "private/"):
        assert marker not in encoded, marker


def check_policy() -> dict[str, Any]:
    policy = ANALYSIS.load_policy(POLICY)
    assert policy["source"]["expected_items"] == 278
    assert policy["source"]["expected_words"] == 851
    assert policy["source"]["expected_accepted_items"] == 68
    assert policy["source"]["expected_abstentions"] == 210
    assert policy["source"]["expected_embedding_failures"] == 2
    assert policy["source"]["expected_independent_reference_wrong_words"] == 4
    assert policy["measurement"]["identity"] == {
        "minimum_similarity": 0.5,
        "minimum_margin": 0.3,
    }
    assert set(policy["decision"]["allowed_outcomes"]) == ANALYSIS.ALLOWED_OUTCOMES
    return policy


def check_audio_and_identity_fixture(policy: dict[str, Any]) -> None:
    with tempfile.TemporaryDirectory(prefix="_shadow-error-audio-", dir=ROOT / "sessions") as temporary:
        root = Path(temporary)
        sample_rate = 16_000
        time = np.arange(sample_rate * 2, dtype=np.float32) / sample_rate
        speech_like = (0.2 * np.sin(2 * np.pi * 220 * time)).astype(np.float32)
        clip = root / "speech.wav"
        sf.write(clip, speech_like, sample_rate, subtype="PCM_16")
        item = {
            "item_id": "fixture-item",
            "start": 0.4,
            "end": 1.1,
            "audio": {
                "path": str(clip.relative_to(ROOT)),
                "start": 0.0,
                "end": 2.0,
                "sha256": ANALYSIS.sha256(clip),
            },
        }
        metrics = ANALYSIS.audio_metrics(item, policy)
        assert metrics["speech_supported"] is True
        assert metrics["active_frame_ratio"] > 0

        silent = root / "silent.wav"
        sf.write(silent, np.zeros(sample_rate, dtype=np.float32), sample_rate, subtype="PCM_16")
        item["audio"] = {
            "path": str(silent.relative_to(ROOT)),
            "start": 0.0,
            "end": 1.0,
            "sha256": ANALYSIS.sha256(silent),
        }
        item["start"], item["end"] = 0.2, 0.7
        assert ANALYSIS.audio_metrics(item, policy)["speech_supported"] is False

    centers = {
        "speaker_01": np.asarray([1.0, 0.0], dtype=np.float32),
        "speaker_02": np.asarray([0.0, 1.0], dtype=np.float32),
    }
    accepted = ANALYSIS.classify_embedding(
        ANALYSIS.normalize(np.asarray([1.0, 0.05], dtype=np.float32)),
        centers,
        sorted(centers),
        policy,
    )
    assert accepted["speaker_id"] == "speaker_01"
    abstained = ANALYSIS.classify_embedding(
        ANALYSIS.normalize(np.asarray([1.0, 1.0], dtype=np.float32)),
        centers,
        sorted(centers),
        policy,
    )
    assert abstained["speaker_id"] is None
    assert abstained["reason"] == "open_set_abstention"


def failure_row(cause: str, seconds: float, *, truth: bool = False) -> dict[str, Any]:
    return {
        "failure_scope": True,
        "coverage_weight_sec": seconds,
        "classification": {"primary_cause": cause},
        "reference": {"outcome_available": truth},
    }


def check_terminal_decisions(policy: dict[str, Any]) -> None:
    rows = [failure_row("interval_boundary_or_mixed_speech", 2.0) for _ in range(8)]
    rows += [failure_row("enrollment_instability", 1.0) for _ in range(2)]
    axes = ANALYSIS.axis_rows(rows, policy)
    outcome, evidence = ANALYSIS.choose_decision(
        rows, axes, {"fixture": True}, policy
    )
    assert outcome == "ADVANCE_INTERVAL_PURIFICATION"
    assert evidence["dominant"] is True

    no_truth = [failure_row("evidence_bound", 1.0) for _ in range(10)]
    axes = ANALYSIS.axis_rows(no_truth, policy)
    outcome, evidence = ANALYSIS.choose_decision(
        no_truth, axes, {"fixture": True}, policy
    )
    assert outcome == "EVIDENCE_BOUND"
    assert evidence["explained_failure_item_ratio"] == 0.0


def check_replay_fixture() -> None:
    with tempfile.TemporaryDirectory(prefix="_shadow-error-replay-", dir=ROOT / "sessions") as temporary:
        out = Path(temporary)
        bundle = {
            "items": [{"schema": ANALYSIS.ITEM_SCHEMA, "item_id": "fixture"}],
            "enrollment": [{"schema": ANALYSIS.ENROLLMENT_SCHEMA, "speaker_id": "speaker_01"}],
            "explanations": [{"schema": ANALYSIS.REFERENCE_SCHEMA, "type": "fixture"}],
            "report": {"schema": ANALYSIS.REPORT_SCHEMA, "decision": "EVIDENCE_BOUND"},
            "markdown": b"# Fixture\n",
        }
        ANALYSIS.write_bundle(out, bundle)
        replay = ANALYSIS.replay_bundle(out, bundle, {"source_fingerprint": "fixture"})
        assert replay["byte_identical"] is True
        (out / "private/item_error_decomposition.jsonl").write_text("tampered\n", encoding="utf-8")
        try:
            ANALYSIS.replay_bundle(out, bundle, {"source_fingerprint": "fixture"})
        except ANALYSIS.DecompositionError as error:
            assert str(error) == "deterministic_replay_mismatch"
        else:
            raise AssertionError("tampered replay unexpectedly passed")


def check_fail_closed(policy: dict[str, Any]) -> None:
    with tempfile.TemporaryDirectory(prefix="_shadow-error-policy-", dir=ROOT / "sessions") as temporary:
        root = Path(temporary)
        mutated = json.loads(json.dumps(policy))
        mutated["source"]["artifacts"][0]["path"] = "missing-source.json"
        path = root / "policy.json"
        write_json(path, mutated)
        result = subprocess.run(
            [str(PYTHON), str(ANALYZER), "preflight", "--policy", str(path)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 2
        assert "required_artifact_missing" in result.stderr


def check_real_outputs() -> None:
    if not TRACKED.is_file() or not REAL_OUT.is_dir():
        print("local shadow error decomposition outputs unavailable; real verification skipped")
        return
    tracked = read_json(TRACKED)
    report = read_json(REAL_OUT / "remote_speaker_shadow_error_decomposition_report.json")
    replay = read_json(REAL_OUT / "replay_report.json")
    assert tracked["schema"] == ANALYSIS.TRACKED_SCHEMA
    assert tracked["decision"] == report["decision"] == "ADVANCE_INTERVAL_PURIFICATION"
    assert tracked["scope"]["items"] == 278
    assert tracked["scope"]["words"] == 851
    assert tracked["replay_verified"] is True
    assert replay["byte_identical"] is True
    assert all(report["invariants"].values())
    assert report["technical_axes"][0]["axis"] == "interval_purification"
    assert report["decision_evidence"]["dominant"] is True
    assert report["reference"]["independent_reference_mismatch_words"] == 4
    assert report["embedding_failures"]["items"] == 2
    assert report["safety"]["production_mutated"] is False
    assert_public_safe(report)
    assert_public_safe(tracked)


def main() -> int:
    policy = check_policy()
    check_audio_and_identity_fixture(policy)
    check_terminal_decisions(policy)
    check_replay_fixture()
    check_fail_closed(policy)
    check_real_outputs()
    print("remote speaker shadow error decomposition v1 checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
