#!/usr/bin/env python3
"""Check Session-Local Remote Speaker Enrollment Hardening v1."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
EVALUATOR = ROOT / "scripts/evaluate-session-local-remote-speaker-enrollment-hardening-v1.py"
POLICY = ROOT / "policies/session-local-remote-speaker-enrollment-hardening-v1.json"
OUT = ROOT / "sessions/_reports/session-local-remote-speaker-enrollment-hardening-v1"
TRACKED = ROOT / "docs/testing/session-local-remote-speaker-enrollment-hardening-v1-manifest.json"


def fail(message: str) -> None:
    raise SystemExit(f"session-local enrollment hardening check failed: {message}")


def load_module():
    specification = importlib.util.spec_from_file_location("enrollment_hardening_v1", EVALUATOR)
    if specification is None or specification.loader is None:
        fail("cannot import evaluator")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def unit_candidate(module) -> None:
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    x = module.normalize(np.asarray([1.0, 0.0, 0.0], dtype=np.float32))
    y = module.normalize(np.asarray([0.8, 0.6, 0.0], dtype=np.float32))
    impostor = module.normalize(np.asarray([0.0, 1.0, 0.0], dtype=np.float32))
    data = {
        "groups": {
            ("fixture", "speaker_a"): [("a0", x), ("a1", y)],
            ("fixture", "speaker_b"): [("b0", impostor), ("b1", impostor)],
        },
    }
    data["control_centers"] = module.control_centers(data["groups"])
    rows, centers, payload = module.build_candidate_centroids(data, policy)
    if len(rows) != 2 or len(centers["fixture"]) != 2:
        fail("candidate profile cardinality")
    row = next(value for value in rows if value["speaker_id"] == "speaker_a")
    weights = [value["normalized_weight"] for value in row["exemplars"]]
    if not weights[0] > weights[1] or not np.isclose(sum(weights), 1.0, atol=1e-8):
        fail("contrastive weights do not prefer the separated exemplar")
    if payload.get("target_item_evidence_read") is not False:
        fail("candidate provenance permits target evidence")
    second_rows, second_centers, second_payload = module.build_candidate_centroids(data, policy)
    if module.jsonl_bytes(rows) != module.jsonl_bytes(second_rows) or module.pretty_json(payload) != module.pretty_json(second_payload):
        fail("candidate is not deterministic")
    if module.embedding_digest(centers["fixture"]["speaker_a"]) != module.embedding_digest(second_centers["fixture"]["speaker_a"]):
        fail("candidate centroid is not deterministic")


def unit_fallback_and_abstention(module) -> None:
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    left = module.normalize(np.asarray([1.0, 0.0], dtype=np.float32))
    right = module.normalize(np.asarray([-0.8, 0.6], dtype=np.float32))
    data = {"groups": {("fixture", "speaker_a"): [("a0", left), ("a1", right)]}}
    data["control_centers"] = module.control_centers(data["groups"])
    rows, _centers, _payload = module.build_candidate_centroids(data, policy)
    if rows[0]["status"] != "control_fallback" or rows[0]["centroid_changed"]:
        fail("zero-reliability profile did not fail open to control")
    result = module.classify(left, {"speaker_a": left}, ["speaker_a", "speaker_b"], policy)
    if result["speaker_id"] is not None or result["reason"] != "incomplete_enrollment":
        fail("incomplete enrollment was force-classified")


def real_result(module) -> None:
    if not (OUT / "session_local_remote_speaker_enrollment_hardening_report.json").is_file():
        return
    report = module.read_json(OUT / "session_local_remote_speaker_enrollment_hardening_report.json")
    if report.get("decision") != "DO_NOT_ADVANCE_ENROLLMENT_HARDENING":
        fail("unexpected real terminal outcome")
    scope = report.get("scope") or {}
    if (scope.get("items"), scope.get("words"), scope.get("enrollment_failure_items")) != (278, 851, 83):
        fail("real frozen scope changed")
    if not all((report.get("invariants") or {}).values()):
        fail("real invariant failed")
    comparison = report.get("comparison") or {}
    if comparison.get("newly_accepted_items") != 11 or comparison.get("removed_control_acceptances") != 5:
        fail("real comparison drifted")
    if (report.get("gates") or {}).get("no_removed_control_acceptance") is not False:
        fail("real unsafe removal gate unexpectedly passed")
    manifest = module.read_json(TRACKED)
    if manifest.get("decision") != report.get("decision") or manifest.get("replay_verified") is not True:
        fail("tracked manifest does not freeze the real result")


def fail_closed_policy() -> None:
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    policy["source"]["artifacts"][0]["sha256"] = "0" * 64
    with tempfile.TemporaryDirectory(prefix="murmurmark-enrollment-policy-") as temporary:
        path = Path(temporary) / "policy.json"
        path.write_text(json.dumps(policy), encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(EVALUATOR), "preflight", "--policy", str(path)],
            cwd=ROOT, text=True, capture_output=True, check=False,
        )
    if result.returncode == 0 or "source_hash_mismatch" not in result.stderr:
        fail("tampered source hash did not fail closed")


def replay_tamper() -> None:
    if not (OUT / "replay_report.json").is_file():
        return
    with tempfile.TemporaryDirectory(prefix="murmurmark-enrollment-replay-") as temporary:
        destination = Path(temporary) / "out"
        shutil.copytree(OUT, destination)
        report = destination / "session_local_remote_speaker_enrollment_hardening_report.json"
        report.write_bytes(report.read_bytes() + b"\n")
        result = subprocess.run(
            [sys.executable, str(EVALUATOR), "replay", "--out-dir", str(destination)],
            cwd=ROOT, text=True, capture_output=True, check=False,
        )
    if result.returncode == 0 or "deterministic_replay_mismatch" not in result.stderr:
        fail("tampered replay output did not fail closed")


def public_privacy() -> None:
    paths = [
        OUT / "input_manifest.public.json",
        OUT / "session_local_remote_speaker_enrollment_hardening_report.json",
        OUT / "session_local_remote_speaker_enrollment_hardening_report.md",
        OUT / "replay_report.json",
        TRACKED,
    ]
    for path in paths:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for marker in ("/Users/", '"embedding":', "PRIVATE_REVIEWER_NAME_SENTINEL"):
            if marker in text:
                fail(f"private marker in public artifact: {path.name}:{marker}")


def main() -> int:
    module = load_module()
    module.validate_policy(json.loads(POLICY.read_text(encoding="utf-8")))
    unit_candidate(module)
    unit_fallback_and_abstention(module)
    fail_closed_policy()
    replay_tamper()
    public_privacy()
    real_result(module)
    print("session-local remote speaker enrollment hardening v1 checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
