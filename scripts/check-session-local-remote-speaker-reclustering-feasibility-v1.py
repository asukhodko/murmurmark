#!/usr/bin/env python3
"""Checks for session-local label-independent remote speaker re-clustering."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
EVALUATOR = ROOT / "scripts/evaluate-session-local-remote-speaker-reclustering-feasibility-v1.py"
POLICY = ROOT / "policies/session-local-remote-speaker-reclustering-feasibility-v1.json"
OUT = ROOT / "sessions/_reports/session-local-remote-speaker-reclustering-feasibility-v1"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def load_module():
    spec = importlib.util.spec_from_file_location("reclustering_v1", EVALUATOR)
    require(spec is not None and spec.loader is not None, "cannot load evaluator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    module = load_module()
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    require(policy["state"] == "frozen_before_label_and_direct_truth_evaluation", "policy is not frozen")
    require(sum(row["cluster_count"] for row in policy["scope"]["sessions"]) == 14, "topology changed")
    require(policy["clustering"]["cluster_count_tuning_allowed"] is False, "cluster tuning enabled")
    require(policy["evaluation"]["post_hoc_tuning_allowed"] is False, "post-hoc tuning enabled")

    rows = [{"key": f"w{index}", "start": float(index)} for index in range(6)]
    vectors = np.asarray(
        [[1.0, 0.0], [0.99, 0.01], [0.98, 0.02], [0.0, 1.0], [0.01, 0.99], [0.02, 0.98]],
        dtype=np.float64,
    )
    first = module.canonicalize_labels(module.cluster_vectors(vectors, 2), rows)
    second = module.canonicalize_labels(module.cluster_vectors(vectors, 2), rows)
    require(first.tolist() == [0, 0, 0, 1, 1, 1], "synthetic clusters are wrong")
    require(first.tolist() == second.tolist(), "clustering is not deterministic")
    require(module.forbidden_key_paths({"rows": [{"speaker_id": "leak"}]}) == ["rows[0].speaker_id"], "leak detector failed")

    assignments = [
        {"key": f"w{index}", "ecapa_cluster": 0 if index < 3 else 1}
        for index in range(6)
    ]
    labels = {
        f"w{index}": {"eligible": True, "speaker_id": "remote_speaker_01" if index < 3 else "remote_speaker_02"}
        for index in range(6)
    }
    mapping = module.map_clusters(assignments, labels, ["remote_speaker_01", "remote_speaker_02"], "ecapa_cluster")
    require(mapping["purity"] == 1.0 and not mapping["ambiguous_clusters"], "pure mapping fixture failed")

    report_path = OUT / "session_local_remote_speaker_reclustering_report.json"
    freeze_path = OUT / "freeze_manifest.json"
    replay_path = OUT / "replay_report.json"
    require(report_path.is_file() and freeze_path.is_file() and replay_path.is_file(), "real corpus artifacts missing")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    replay = json.loads(replay_path.read_text(encoding="utf-8"))
    require(report["schema"] == module.REPORT_SCHEMA, "report schema changed")
    require(report["decision"] == "EMBEDDING_GEOMETRY_BOUND", "unexpected terminal outcome")
    require(report["scope"]["sessions"] == 6 and report["scope"]["development_items"] == 33, "scope changed")
    require(report["safety"]["coverage_v3_accepts_preserved"] == 68, "Coverage v3 accepts changed")
    require(report["safety"]["production_guards_verified"] == 355, "production guards changed")
    require(report["safety"]["selected_transcript_mutated"] is False, "selected transcript mutated")
    require(report["safety"]["raw_audio_mutated"] is False, "raw audio mutated")
    require(freeze["labels_read"] is False and freeze["direct_truth_read"] is False, "freeze leaked labels")
    require(replay["verified"] is True, "replay is not verified")
    tracked = json.loads(
        (ROOT / "docs/testing/session-local-remote-speaker-reclustering-feasibility-v1-manifest.json").read_text(
            encoding="utf-8"
        )
    )
    for expected in tracked["artifacts"].values():
        path = ROOT / expected["path"]
        require(path.is_file(), f"tracked artifact missing: {expected['path']}")
        require(path.stat().st_size == expected["bytes"], f"tracked artifact size changed: {expected['path']}")
        require(module.sha256(path) == expected["sha256"], f"tracked artifact hash changed: {expected['path']}")
    frozen = json.loads((OUT / "private/reclustering_pack.frozen.json").read_text(encoding="utf-8"))
    require(not module.forbidden_key_paths(frozen), "frozen pack contains label leakage")

    before = module.sha256(report_path)
    completed = subprocess.run(
        [sys.executable, str(EVALUATOR), "replay"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    require(completed.returncode == 0, completed.stderr or completed.stdout)
    require(module.sha256(report_path) == before, "replay mutated the report")

    cli = (ROOT / "Sources/MurmurMarkCLI/MurmurMarkCLI.swift").read_text(encoding="utf-8")
    require("remote-reclustering-v1" in cli, "CLI command missing")
    contract = (ROOT / "docs/contracts/session-local-remote-speaker-reclustering-feasibility-v1.md").read_text(encoding="utf-8")
    runbook = (ROOT / "docs/runbooks/session-local-remote-speaker-reclustering-feasibility-v1.md").read_text(encoding="utf-8")
    require("EMBEDDING_GEOMETRY_BOUND" in contract, "contract omits terminal outcome")
    require("remote-reclustering-v1" in runbook, "runbook omits CLI command")
    print("session-local remote speaker reclustering feasibility v1 checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
