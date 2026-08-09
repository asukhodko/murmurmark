#!/usr/bin/env python3
"""Deterministic checks for session-local homogeneous enrollment mining v1."""

from __future__ import annotations

import importlib.util
import hashlib
import json
from pathlib import Path
import sys

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/mine-session-local-homogeneous-remote-speaker-enrollment-v1.py"
MANIFEST = ROOT / "docs/testing/session-local-homogeneous-remote-speaker-enrollment-mining-v1-manifest.json"


def load_module():
    spec = importlib.util.spec_from_file_location("homogeneous_enrollment_v1", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load homogeneous enrollment module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def unit(value: list[float]) -> np.ndarray:
    vector = np.asarray(value, dtype=np.float64)
    return vector / np.linalg.norm(vector)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    module = load_module()
    policy = module.load_policy(module.DEFAULT_POLICY)
    assert module.spread_indices(20, 4) == [0, 6, 13, 19]
    assert policy["mining"]["target_text_read"] is False
    assert policy["decision"]["production_promotion_allowed"] is False

    rows = [
        {
            "key": f"a{index}",
            "utterance_id": f"utt_{index}",
            "start": float(index * 40),
            "end": float(index * 40 + 4),
        }
        for index in range(4)
    ]
    ecapa = {
        "a0": unit([1.0, 0.00, 0.0]),
        "a1": unit([1.0, 0.02, 0.0]),
        "a2": unit([1.0, -0.02, 0.0]),
        "a3": unit([1.0, 0.01, 0.0]),
    }
    wavlm = {
        "a0": unit([0.0, 1.0, 0.00]),
        "a1": unit([0.0, 1.0, 0.01]),
        "a2": unit([0.0, 1.0, -0.01]),
        "a3": unit([0.0, 1.0, 0.02]),
    }
    keys, details = module.choose_joint_clique(rows, ecapa, wavlm, policy)
    assert len(keys) == 4
    assert details["reason"] == "largest_joint_pairwise_clique"

    wavlm["a3"] = unit([1.0, 0.0, 0.0])
    keys, _ = module.choose_joint_clique(rows, ecapa, wavlm, policy)
    assert len(keys) == 3

    centroids = {
        "ecapa": {
            ("session", "remote_speaker_01"): unit([1.0, 0.0]),
            ("session", "remote_speaker_02"): unit([0.0, 1.0]),
        },
        "wavlm": {
            ("session", "remote_speaker_01"): unit([1.0, 0.0]),
            ("session", "remote_speaker_02"): unit([0.0, 1.0]),
        },
    }
    target = {"ecapa": {"item": unit([1.0, 0.01])}, "wavlm": {"item": unit([1.0, 0.01])}}
    decision = module.classify_target(
        {"key": "item", "session_id": "session"}, target, centroids, policy
    )
    assert decision["prediction"] == "remote_speaker_01"

    target["wavlm"]["item"] = unit([0.0, 1.0])
    decision = module.classify_target(
        {"key": "item", "session_id": "session"}, target, centroids, policy
    )
    assert decision["prediction"] is None
    assert decision["reason"] == "insufficient_or_conflicting_model_evidence"

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["decision"] == "KEEP_EXISTING_ENROLLMENT"
    assert manifest["scope"]["qualified_profiles"] == 9
    assert manifest["development"]["preserved_confirmed_v1_additive_gains"] == 0
    assert manifest["development"]["new_false_identity_items"] == 4
    assert manifest["replay_verified"] is True
    for row in manifest["artifacts"].values():
        path = ROOT / row["path"]
        assert path.is_file()
        assert path.stat().st_size == row["bytes"]
        assert sha256(path) == row["sha256"]

    print("session-local homogeneous remote speaker enrollment mining v1 checks ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
