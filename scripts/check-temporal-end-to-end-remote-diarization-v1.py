#!/usr/bin/env python3
"""Checks for the frozen temporal remote-speaker diarization qualification."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
EVALUATOR = ROOT / "scripts/evaluate-temporal-end-to-end-remote-diarization-v1.py"
POLICY = ROOT / "policies/temporal-end-to-end-remote-diarization-qualification-v1.json"
OUT = ROOT / "sessions/_reports/temporal-end-to-end-remote-diarization-qualification-v1"
TRACKED = ROOT / "docs/testing/temporal-end-to-end-remote-diarization-qualification-v1-manifest.json"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def load_module():
    spec = importlib.util.spec_from_file_location("temporal_remote_diarization_v1", EVALUATOR)
    require(spec is not None and spec.loader is not None, "cannot load evaluator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    module = load_module()
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    require(policy["state"] == "frozen_before_coverage_and_direct_truth_evaluation", "policy changed")
    require(policy["candidate"]["id"] == "dia_community1_temporal_vbx_v1", "candidate changed")
    require(policy["candidate"]["model_sha256"] == "f23f04aa9d0f6b8b0a28de016d226dcbe92d7461a6e58045401acfbed623838a", "model changed")
    require(policy["runtime"]["offline"] is True and policy["runtime"]["device"] == "cpu", "runtime changed")
    require(policy["algorithm"]["speaker_count_strategy"] == "inferred_unbounded_vbx", "speaker-count strategy changed")
    require(policy["algorithm"]["speaker_count_from_truth"] is False, "truth controls speaker count")
    require(policy["algorithm"]["truth_guided_tuning_allowed"] is False, "truth-guided tuning enabled")
    require(policy["evaluation"]["post_hoc_tuning_allowed"] is False, "post-hoc tuning enabled")
    require(policy["decision"]["production_promotion_allowed"] is False, "production promotion enabled")

    spans = [
        {"start": 0.0, "end": 2.0, "candidate_cluster": 0},
        {"start": 1.0, "end": 3.0, "candidate_cluster": 1},
    ]
    assignment = module.window_assignment(spans, 0.0, 3.0, 0.25)
    require(assignment == {"cluster": 0, "coverage_ratio": 0.666667, "dominance_margin": 0.0}, "window assignment changed")
    require(module.activity_jaccard(spans, spans) == 1.0, "activity identity failed")
    require(module.concurrent_seconds(spans) == 1.0, "overlap accounting failed")
    require(
        module.forbidden_key_paths({"sessions": [{"speaker_id": "leak"}]}) == ["sessions[0].speaker_id"],
        "pre-freeze label leak detector failed",
    )

    report_path = OUT / "temporal_remote_diarization_report.json"
    freeze_path = OUT / "freeze_manifest.json"
    replay_path = OUT / "replay_report.json"
    frozen_path = OUT / "private/candidate_pack.frozen.json"
    require(report_path.is_file() and freeze_path.is_file() and replay_path.is_file(), "real corpus artifacts missing")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    replay = json.loads(replay_path.read_text(encoding="utf-8"))
    frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
    require(report["schema"] == module.REPORT_SCHEMA, "report schema changed")
    require(report["decision"] == "KEEP_EXPLICIT_UNKNOWN", "unexpected outcome")
    require(report["scope"] == {"development_items": 33, "profiles": 14, "sessions": 6, "windows": 347}, "scope changed")
    require(report["temporal"]["values"]["minimum_temporal_stability_ari"] == 0.814301, "temporal stability changed")
    require(report["temporal"]["values"]["minimum_activity_jaccard"] == 0.972946, "activity stability changed")
    require(report["mapping"]["values"]["exact_speaker_count_sessions"] == 0, "speaker-count result changed")
    require(report["mapping"]["values"]["ambiguous_clusters"] == 3, "mapping ambiguity changed")
    require(report["boundaries"]["values"]["minimum_remote_interval_duration_recall"] == 0.598626, "duration recall changed")
    require(report["boundaries"]["values"]["minimum_remote_interval_center_recall"] == 0.701613, "center recall changed")
    require(report["direct_truth"]["preserved_confirmed_v1_additive_gains"] == 2, "confirmed gains changed")
    require(report["direct_truth"]["new_false_identity_items"] == 7, "false identities changed")
    require(report["direct_truth"]["lost_correct_control_identity_items"] == 1, "control losses changed")
    require(report["direct_truth"]["unsafe_fail_closed_accepts"] == 9, "unsafe accepts changed")
    require(report["safety"]["coverage_v3_accepts_preserved"] == 68, "Coverage v3 changed")
    require(report["safety"]["production_guards_verified"] == 355, "production guards changed")
    require(report["safety"]["selected_transcript_mutated"] is False, "selected transcript mutated")
    require(report["safety"]["raw_audio_mutated"] is False, "raw audio mutated")
    require(freeze["labels_read"] is False and freeze["direct_truth_read"] is False, "freeze leaked truth")
    require(not module.forbidden_key_paths(frozen), "frozen candidate pack contains label leakage")
    require(replay["verified"] is True, "replay is not verified")

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
    require("remote-temporal-diarization-v1" in cli, "CLI command missing")
    contract = (ROOT / "docs/contracts/temporal-end-to-end-remote-diarization-qualification-v1.md").read_text(encoding="utf-8")
    runbook = (ROOT / "docs/runbooks/temporal-end-to-end-remote-diarization-qualification-v1.md").read_text(encoding="utf-8")
    require("production" in contract.lower() and "forbidden" in contract.lower(), "contract omits promotion boundary")
    require("remote-temporal-diarization-v1" in runbook, "runbook omits CLI command")
    print("temporal end-to-end remote diarization v1 checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
