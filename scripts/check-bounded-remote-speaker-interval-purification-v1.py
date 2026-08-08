#!/usr/bin/env python3
"""Check Bounded Remote Speaker Interval Purification v1 contracts."""

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
EVALUATOR = ROOT / "scripts/evaluate-bounded-remote-speaker-interval-purification-v1.py"
POLICY = ROOT / "policies/bounded-remote-speaker-interval-purification-v1.json"
TRACKED = ROOT / "docs/testing/bounded-remote-speaker-interval-purification-v1-manifest.json"
REAL_OUT = ROOT / "sessions/_reports/bounded-remote-speaker-interval-purification-v1"


def load_evaluator() -> Any:
    spec = importlib.util.spec_from_file_location("bounded_interval_v1", EVALUATOR)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


EVAL = load_evaluator()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def assert_public_safe(value: Any) -> None:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True)
    for marker in ("/Users/", "/home/", '"text"', '"embedding"', "private/", "reference_speaker_"):
        assert marker not in encoded, marker


def fixture_row(cause: str = "interval_boundary_or_mixed_speech") -> dict[str, Any]:
    return {
        "item_id": "fixture-item",
        "session_id": "fixture-session",
        "start": 1.0,
        "end": 2.0,
        "word_ids": ["word-1"],
        "classification": {"primary_cause": cause},
        "audio": {
            "sha256": "source",
            "speech_supported": cause != "insufficient_audio_evidence",
        },
        "shadow_audio_start": 0.5,
        "shadow_audio_end": 2.5,
    }


def check_policy() -> dict[str, Any]:
    policy = EVAL.load_policy(POLICY)
    assert policy["source"]["expected_items"] == 278
    assert policy["source"]["expected_words"] == 851
    assert policy["source"]["expected_interval_failure_items"] == 93
    assert policy["source"]["expected_interval_failure_seconds"] == 201.273504
    assert policy["candidate"]["id"] == "word_span_guard_80ms_v1"
    assert policy["candidate"]["parameter_search_allowed"] is False
    assert policy["identity"]["minimum_similarity"] == 0.5
    assert policy["identity"]["minimum_margin"] == 0.3
    assert set(policy["decision"]["allowed_outcomes"]) == EVAL.ALLOWED_OUTCOMES
    return policy


def check_candidate_rules(policy: dict[str, Any]) -> None:
    row = fixture_row()
    context = [
        {"word_id": "left", "start": 0.7, "end": 0.95, "speaker_id": "remote_speaker_01"},
        {"word_id": "right", "start": 2.2, "end": 2.4, "speaker_id": "remote_speaker_01"},
    ]
    bounded = EVAL.candidate_bounds(row, context, policy)
    assert bounded["status"] == "materialize"
    assert bounded["candidate_start"] == 0.97
    assert bounded["candidate_end"] == 2.08

    overlap = context + [
        {"word_id": "overlap", "start": 1.4, "end": 1.7, "speaker_id": "remote_speaker_02"}
    ]
    assert EVAL.candidate_bounds(row, overlap, policy)["reason"] == "known_context_overlaps_word_span"

    short = fixture_row()
    short["end"] = 1.2
    assert EVAL.candidate_bounds(short, [], policy)["reason"] == "word_span_too_short"

    insufficient = fixture_row("insufficient_audio_evidence")
    assert EVAL.candidate_bounds(insufficient, [], policy)["reason"] == "insufficient_audio_evidence"


def check_audio_materialization(policy: dict[str, Any]) -> None:
    with tempfile.TemporaryDirectory(prefix="_bounded-interval-audio-", dir=ROOT / "sessions") as temporary:
        root = Path(temporary)
        source = root / "source.wav"
        sample_rate = 16_000
        values = np.linspace(-0.2, 0.2, sample_rate * 2, dtype=np.float32)
        sf.write(source, values, sample_rate, subtype="PCM_16")
        first = root / "first.wav"
        second = root / "second.wav"
        row_one = EVAL.write_candidate_audio(source, first, 0.25, 1.25, policy["candidate"]["output_subtype"])
        row_two = EVAL.write_candidate_audio(source, second, 0.25, 1.25, policy["candidate"]["output_subtype"])
        assert row_one["frames"] == sample_rate
        assert row_one["sha256"] == row_two["sha256"]
        assert row_one["duration_sec"] == 1.0


def check_terminal_decisions() -> None:
    passed = {"material": True, "precision": True}
    invariants = {"conservation": True, "shadow": True}
    assert EVAL.choose_decision(passed, invariants) == "ADVANCE_PURIFIED_SHADOW_CANDIDATE"
    assert EVAL.choose_decision({**passed, "material": False}, invariants) == "DO_NOT_ADVANCE_INTERVAL_PURIFICATION"
    assert EVAL.choose_decision(passed, {**invariants, "conservation": False}) == "EVIDENCE_BOUND"


def check_replay_tamper() -> None:
    with tempfile.TemporaryDirectory(prefix="_bounded-interval-replay-", dir=ROOT / "sessions") as temporary:
        out = Path(temporary)
        expected = {
            "private/item_comparison.jsonl": EVAL.jsonl_bytes([{"item_id": "fixture"}]),
            "report.json": EVAL.pretty_json({"decision": "DO_NOT_ADVANCE_INTERVAL_PURIFICATION"}),
        }
        for name, payload in expected.items():
            EVAL.atomic_write(out / name, payload)
        assert EVAL.verify_output_payloads(out, expected) == []
        (out / "report.json").write_text("tampered\n", encoding="utf-8")
        assert EVAL.verify_output_payloads(out, expected) == ["report.json"]


def check_fail_closed(policy: dict[str, Any]) -> None:
    with tempfile.TemporaryDirectory(prefix="_bounded-interval-policy-", dir=ROOT / "sessions") as temporary:
        root = Path(temporary)
        mutated = json.loads(json.dumps(policy))
        mutated["source"]["artifacts"][0]["path"] = "missing-source.json"
        path = root / "policy.json"
        write_json(path, mutated)
        result = subprocess.run(
            [str(PYTHON), str(EVALUATOR), "preflight", "--policy", str(path)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 2
        assert "required_artifact_missing" in result.stderr


def check_real_outputs() -> None:
    if not TRACKED.is_file() or not REAL_OUT.is_dir():
        print("local bounded interval outputs unavailable; real verification skipped")
        return
    tracked = read_json(TRACKED)
    report = read_json(REAL_OUT / "bounded_remote_speaker_interval_purification_report.json")
    replay = read_json(REAL_OUT / "replay_report.json")
    assert tracked["schema"] == EVAL.TRACKED_SCHEMA
    assert tracked["decision"] == report["decision"] == "DO_NOT_ADVANCE_INTERVAL_PURIFICATION"
    assert report["scope"]["items"] == 278
    assert report["scope"]["words"] == 851
    assert report["scope"]["interval_failure_items"] == 93
    assert report["candidate"]["materialized_items"] == 50
    assert report["comparison"]["newly_accepted_items"] == 2
    assert report["comparison"]["removed_control_acceptances"] == 4
    assert report["comparison"]["candidate_evidence"]["structural_one_to_one"]["precision"] == 1.0
    assert report["comparison"]["candidate_evidence"]["independent_machine_reference"]["precision"] == 0.967742
    assert report["comparison"]["new_reference_error_words"] == 1
    assert all(report["invariants"].values())
    assert replay["byte_identical"] is True
    assert report["safety"]["production_mutated"] is False
    assert_public_safe(report)
    assert_public_safe(tracked)


def main() -> int:
    policy = check_policy()
    check_candidate_rules(policy)
    check_audio_materialization(policy)
    check_terminal_decisions()
    check_replay_tamper()
    check_fail_closed(policy)
    check_real_outputs()
    print("bounded remote speaker interval purification v1 checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
