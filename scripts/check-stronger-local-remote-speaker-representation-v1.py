#!/usr/bin/env python3
"""Checks for the frozen WeSpeaker remote-speaker representation qualification."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
EVALUATOR = ROOT / "scripts/evaluate-stronger-local-remote-speaker-representation-v1.py"
POLICY = ROOT / "policies/stronger-local-remote-speaker-representation-qualification-v1.json"
OUT = ROOT / "sessions/_reports/stronger-local-remote-speaker-representation-qualification-v1"
TRACKED = ROOT / "docs/testing/stronger-local-remote-speaker-representation-qualification-v1-manifest.json"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def load_module():
    spec = importlib.util.spec_from_file_location("stronger_representation_v1", EVALUATOR)
    require(spec is not None and spec.loader is not None, "cannot load evaluator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    module = load_module()
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    require(policy["state"] == "frozen_before_direct_truth_evaluation", "policy is not frozen")
    require(policy["candidate"]["id"] == "wespeaker_resnet34_lm_onnx", "candidate changed")
    require(policy["candidate"]["model_sha256"] == "7bb2f06e9df17cdf1ef14ee8a15ab08ed28e8d0ef5054ee135741560df2ec068", "model changed")
    require(policy["candidate"]["license"] == "Apache-2.0", "license changed")
    require(sum(row["cluster_count"] for row in policy["scope"]["sessions"]) == 14, "topology changed")
    require(policy["clustering"]["cluster_count_tuning_allowed"] is False, "cluster tuning enabled")
    require(policy["evaluation"]["post_hoc_tuning_allowed"] is False, "post-hoc tuning enabled")
    require(policy["decision"]["production_promotion_allowed"] is False, "production promotion enabled")

    rows = [{"key": f"w{index}", "start": float(index)} for index in range(6)]
    vectors = np.asarray(
        [[1.0, 0.0], [0.99, 0.01], [0.98, 0.02], [0.0, 1.0], [0.01, 0.99], [0.02, 0.98]],
        dtype=np.float64,
    )
    first = module.BASE.canonicalize_labels(module.BASE.cluster_vectors(vectors, 2), rows)
    second = module.BASE.canonicalize_labels(module.BASE.cluster_vectors(vectors, 2), rows)
    require(first.tolist() == [0, 0, 0, 1, 1, 1], "synthetic candidate clusters are wrong")
    require(first.tolist() == second.tolist(), "candidate clustering is not deterministic")
    require(
        module.forbidden_key_paths({"rows": [{"speaker_id": "leak"}]}) == ["rows[0].speaker_id"],
        "label leak detector failed",
    )

    assignments = [
        {"key": f"w{index}", "candidate_cluster": 0 if index < 3 else 1}
        for index in range(6)
    ]
    labels = {
        f"w{index}": {
            "eligible": True,
            "speaker_id": "remote_speaker_01" if index < 3 else "remote_speaker_02",
        }
        for index in range(6)
    }
    mapping = module.BASE.map_clusters(
        assignments, labels, ["remote_speaker_01", "remote_speaker_02"], "candidate_cluster"
    )
    require(mapping["purity"] == 1.0 and not mapping["ambiguous_clusters"], "pure mapping fixture failed")

    report_path = OUT / "stronger_local_remote_speaker_representation_report.json"
    freeze_path = OUT / "freeze_manifest.json"
    replay_path = OUT / "replay_report.json"
    require(report_path.is_file() and freeze_path.is_file() and replay_path.is_file(), "real corpus artifacts missing")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    replay = json.loads(replay_path.read_text(encoding="utf-8"))
    require(report["schema"] == module.REPORT_SCHEMA, "report schema changed")
    require(report["decision"] == "KEEP_EXPLICIT_UNKNOWN", "unexpected terminal outcome")
    require(report["scope"] == {"development_items": 33, "profiles": 14, "sessions": 6, "windows": 347}, "scope changed")
    require(report["geometry"]["values"]["minimum_candidate_stability_ari"] == 0.442394, "geometry changed")
    require(report["direct_truth"]["preserved_confirmed_v1_additive_gains"] == 3, "confirmed gains changed")
    require(report["direct_truth"]["new_false_identity_items"] == 12, "false identity result changed")
    require(report["direct_truth"]["lost_correct_control_identity_items"] == 0, "control result changed")
    require(report["safety"]["coverage_v3_accepts_preserved"] == 68, "Coverage v3 changed")
    require(report["safety"]["production_guards_verified"] == 355, "production guards changed")
    require(report["safety"]["selected_transcript_mutated"] is False, "selected transcript mutated")
    require(report["safety"]["raw_audio_mutated"] is False, "raw audio mutated")
    require(freeze["labels_read"] is False and freeze["direct_truth_read"] is False, "freeze leaked labels")
    require(replay["verified"] is True, "replay is not verified")
    frozen = json.loads((OUT / "private/candidate_pack.frozen.json").read_text(encoding="utf-8"))
    require(not module.forbidden_key_paths(frozen), "frozen candidate pack contains label leakage")

    tracked = json.loads(TRACKED.read_text(encoding="utf-8"))
    for expected in tracked["artifacts"].values():
        path = ROOT / expected["path"]
        require(path.is_file(), f"tracked artifact missing: {expected['path']}")
        require(path.stat().st_size == expected["bytes"], f"tracked artifact size changed: {expected['path']}")
        require(module.sha256(path) == expected["sha256"], f"tracked artifact hash changed: {expected['path']}")

    before = module.sha256(report_path)
    completed = subprocess.run(
        [sys.executable, str(EVALUATOR), "replay"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    require(completed.returncode == 0, completed.stderr or completed.stdout)
    require(module.sha256(report_path) == before, "replay mutated the report")

    cli = (ROOT / "Sources/MurmurMarkCLI/MurmurMarkCLI.swift").read_text(encoding="utf-8")
    require("remote-representation-v1" in cli, "CLI command missing")
    contract = (ROOT / "docs/contracts/stronger-local-remote-speaker-representation-qualification-v1.md").read_text(encoding="utf-8")
    runbook = (ROOT / "docs/runbooks/stronger-local-remote-speaker-representation-qualification-v1.md").read_text(encoding="utf-8")
    require("KEEP_EXPLICIT_UNKNOWN" in contract, "contract omits terminal outcome")
    require("remote-representation-v1" in runbook, "runbook omits CLI command")
    print("stronger local remote speaker representation v1 checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
