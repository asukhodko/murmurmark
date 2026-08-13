#!/usr/bin/env python3
"""Fast contract checks for the one-shot disjoint speaker-model qualification."""

from __future__ import annotations

import importlib.util
import inspect
import hashlib
import json
from pathlib import Path
import sys
import tempfile

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
EVALUATOR = ROOT / "scripts/evaluate-disjoint-remote-speaker-model-v1.py"
POLICY = ROOT / "policies/disjoint-remote-speaker-model-qualification-v1.json"
TRACKED_MANIFEST = ROOT / "docs/testing/disjoint-remote-speaker-model-qualification-v1-manifest.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def load_evaluator():
    spec = importlib.util.spec_from_file_location("murmurmark_disjoint_model_check", EVALUATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def replay_fixture(module, directory: Path) -> None:
    truth_v2 = {
        "items": 72,
        "repeat": {"compared": 12, "matches": 12, "determinism": 1.0},
        "positive_items": 21,
        "special_items": 51,
        "correct_identity_items": 3,
        "attributed_precision": 1.0,
        "attributed_recall": 0.142857,
        "counts": {"correct_identity": 3},
        "bcubed": {"f1": 0.25},
        "speaker_count": {"mean_absolute_error": 0.5},
    }
    truth_v1 = {
        "items": 33,
        "lost_correct_control_identity_items": 0,
        "new_false_identity_items": 0,
    }
    controlled = {
        "candidate": {"word_count": 71, "bcubed": {"f1": 0.98}, "boundary_recall": 1.0},
        "coverage_v3_control": {"bcubed": {"f1": 0.98}, "boundary_recall": 1.0},
    }
    core = {
        "schema": module.CORE_SCHEMA,
        "decision": "KEEP_COVERAGE_V3",
        "candidate": {
            "id": "fixture_candidate",
            "model_id": "fixture/model",
            "model_revision": "0" * 40,
        },
        "calibration": {
            "source": "controlled_dev_only",
            "thresholds": {"minimum_similarity": 0.8, "minimum_margin": 0.1},
        },
        "truth_v2": truth_v2,
        "truth_v1_control": truth_v1,
        "controlled_corpus": controlled,
        "gates": {"fixture": False},
        "invariants": {"fixture": True},
        "failed_gates": ["fixture"],
        "failed_invariants": [],
        "safety": {"production_promoted": False},
    }
    rows = [{"scope": "fixture", "result": "safe_abstention"}]
    private = directory / "private"
    private.mkdir(parents=True)
    module.write_json(private / "candidate_pack.frozen.json", {"schema": module.PACK_SCHEMA})
    module.write_json(private / "evaluation_core.json", core)
    module.write_jsonl(private / "item_evaluation.jsonl", rows)
    module.write_json(
        directory / "disjoint_remote_speaker_model_qualification_report.json",
        module.public_report(core, replay_verified=False),
    )
    original = module.build_core
    module.build_core = lambda _policy, _out: (core, rows)
    try:
        assert module.action_replay({}, directory) == 0
        first = (directory / "replay_report.json").read_bytes()
        assert module.action_replay({}, directory) == 0
        assert (directory / "replay_report.json").read_bytes() == first
        report = module.read_json(directory / "disjoint_remote_speaker_model_qualification_report.json")
        assert report["replay_verified"] is True
    finally:
        module.build_core = original


def main() -> int:
    module = load_evaluator()
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    assert policy["schema"] == module.POLICY_SCHEMA
    assert policy["candidate"]["id"] == "3dspeaker_eres2netv2_common"
    assert policy["candidate"]["license"] == "Apache-2.0"
    assert policy["decision"]["production_promotion_allowed"] is False
    assert policy["decision"]["auto_selection_allowed"] is False
    assert policy["calibration"]["disjoint_truth_v2_tuning_allowed"] is False
    assert policy["calibration"]["post_unseal_tuning_allowed"] is False

    prepare_source = inspect.getsource(module.action_prepare)
    assert "truth_v2_answers" not in prepare_source
    assert "truth_v2_slot_map" not in prepare_source
    assert "evaluate_truth_v2" not in prepare_source

    centroids = {
        "remote_speaker_01": np.asarray([1.0, 0.0]),
        "remote_speaker_02": np.asarray([0.0, 1.0]),
    }
    embeddings = {
        "full": np.asarray([1.0, 0.0]),
        "window_1": np.asarray([1.0, 0.0]),
        "window_2": np.asarray([1.0, 0.0]),
    }
    target = {"full_key": "full", "window_keys": ["window_1", "window_2"]}
    accepted = module.classify_target(
        target,
        embeddings,
        centroids,
        {"minimum_similarity": 0.8, "minimum_margin": 0.2},
        policy,
    )
    assert accepted["prediction"] == "remote_speaker_01"
    embeddings["window_2"] = np.asarray([0.0, 1.0])
    mixed = module.classify_target(
        target,
        embeddings,
        centroids,
        {"minimum_similarity": 0.8, "minimum_margin": 0.2},
        policy,
    )
    assert mixed["prediction"] == "mixed"
    assert module.forbidden_candidate_keys({"nested": {"truth": "hidden"}}) == ["nested.truth"]

    with tempfile.TemporaryDirectory(prefix="murmurmark-disjoint-model-check-") as temporary:
        replay_fixture(module, Path(temporary))

    public_report = ROOT / (
        "sessions/_reports/disjoint-remote-speaker-model-qualification-v1/"
        "disjoint_remote_speaker_model_qualification_report.json"
    )
    if public_report.is_file():
        payload = public_report.read_text(encoding="utf-8")
        assert "/Users/" not in payload
        assert "sessions/20" not in payload
        report = json.loads(payload)
        assert report["schema"] == module.REPORT_SCHEMA
        assert report["replay_verified"] is True
        assert report["safety"]["production_promoted"] is False
        manifest = json.loads(TRACKED_MANIFEST.read_text(encoding="utf-8"))
        assert manifest["decision"] == "KEEP_COVERAGE_V3"
        assert manifest["production_promotion_allowed"] is False
        for expected in manifest["artifacts"].values():
            path = ROOT / expected["path"]
            assert path.is_file()
            assert path.stat().st_size == expected["bytes"]
            assert sha256(path) == expected["sha256"]

    print("disjoint remote speaker model qualification v1 checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
