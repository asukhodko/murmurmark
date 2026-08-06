#!/usr/bin/env python3
"""Fast contract checks for stronger separator prerequisite work."""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts/stronger-offline-target-speaker-separator-prerequisites-v1.py"
POLICY = ROOT / "policies/stronger-offline-target-speaker-separator-prerequisites-v1.json"
OUTPUT = ROOT / "sessions/_reports/stronger-offline-target-speaker-separator-prerequisites-v1"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def load_module():
    spec = importlib.util.spec_from_file_location("stronger_separator_prerequisites", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load stronger separator prerequisite script")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    module = load_module()
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    require(policy["schema"] == module.SCHEMA, "unexpected policy schema")
    require(policy["profile"] == module.PROFILE, "unexpected profile")
    require(sum(row["status"] == "selected" for row in policy["backbone_shortlist"]) == 1, "expected one selected backbone")
    require(policy["selected_backbone"]["id"] == "speechbrain_sepformer_libri2mix", "unexpected selected backbone")
    require(policy["backbone_shortlist"][0]["license"] == "Apache-2.0", "selected license is not pinned")
    require(policy["hard_or_sealed_access"] is False, "hard or sealed access enabled")
    require(policy["training_performed"] is False, "training enabled in prerequisite stage")
    require(policy["post_asr_cleanup_promotion_credit"] == 0, "post-ASR credit changed")
    require(policy["four_stem_adapter"]["direct_asr_before_dev_pass"] is False, "direct ASR opened early")
    require(policy["production_publication"] == "forbidden", "production publication opened")

    sha_pattern = re.compile(r"^[0-9a-f]{64}$")
    for expected in policy["selected_backbone"]["model_files"].values():
        require(bool(sha_pattern.fullmatch(expected)), "invalid model SHA-256")
    for expected in policy["selected_backbone"]["runtime_wheels"].values():
        require(bool(sha_pattern.fullmatch(expected)), "invalid runtime wheel SHA-256")

    speakers = module.split_speakers(policy)
    overlap = module.split_overlap(speakers)
    require(all(not values for values in overlap.values()), "expanded speakers overlap across splits")

    samples = np.linspace(-0.05, 0.05, 800, dtype=np.float64)
    stems = np.stack((samples * 0.6, np.roll(samples, 7) * 0.4))
    mixture = 0.7 * stems[0] + 1.2 * stems[1] + 0.001 * np.sin(np.arange(samples.size))
    adapter = module.least_squares_adapter(stems, mixture)
    require(np.max(np.abs(adapter["reconstruction"] - mixture)) <= 1.0e-12, "adapter is not mixture-consistent")
    require(np.isfinite(adapter["target_me"]).all(), "adapter target is non-finite")

    sample = module.with_fingerprint({"schema": "test", "value": 1})
    require(module.fingerprint_valid(sample), "valid fingerprint rejected")
    sample["value"] = 2
    require(not module.fingerprint_valid(sample), "mutated fingerprint accepted")

    if (OUTPUT / "decision.json").is_file():
        verification = module.run_verify(policy_path=POLICY, output_dir=OUTPUT)
        require(verification["passed"], "local prerequisite verification failed")
        decision = json.loads((OUTPUT / "decision.json").read_text(encoding="utf-8"))
        require(
            decision["decision"] in policy["terminal_decisions"],
            "local prerequisite decision is not terminal",
        )

    print("stronger offline target-speaker separator prerequisite checks ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
