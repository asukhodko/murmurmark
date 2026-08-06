#!/usr/bin/env python3
"""Deterministic checks for SepFormer Four-Stem Target-Me Qualification v1."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parent.parent


def load() -> object:
    path = ROOT / "scripts/sepformer-four-stem-target-me-qualification-v1.py"
    spec = importlib.util.spec_from_file_location("murmurmark_sepformer_four_stem_v1", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CORE = load()


def require(value: bool, message: str) -> None:
    if not value:
        raise SystemExit(message)


def policy_checks() -> None:
    policy = CORE.load_policy(ROOT / "policies/sepformer-four-stem-target-me-qualification-v1.json")
    splits = policy["corpus"]["splits"]
    train = set(splits["train"]["speakers"])
    dev = set(splits["dev"]["speakers"])
    future = set(splits["future_hard"]["speakers"])
    require(len(train) == 12 and len(dev) == 4 and len(future) == 4, "unexpected speaker plan")
    require(not train & dev and not train & future and not dev & future, "speaker splits overlap")
    require(splits["future_hard"]["access"] == "forbidden", "future-hard access is not forbidden")
    require(policy["production_publication"] == "forbidden", "production publication enabled")
    require(policy["direct_asr_access"] is False, "direct ASR enabled")
    require(policy["post_asr_cleanup_promotion_credit"] == 0, "post-ASR cleanup received credit")
    require(policy["adapter"]["selectable_output_on_uncertainty"] == "exact_production_v2_input", "fallback changed")


def scale_recovery_checks() -> None:
    samples = np.arange(800, dtype=np.float64) / 800.0
    first = np.sin(2.0 * np.pi * 17.0 * samples)
    second = np.cos(2.0 * np.pi * 29.0 * samples)
    mixture = 0.4 * first - 0.2 * second + 0.01 * np.sin(2.0 * np.pi * 3.0 * samples)
    scaled, coefficients = CORE.recover_stem_scale(np.stack([first, second]), mixture)
    residual = mixture - scaled[0] - scaled[1]
    reconstruction = scaled[0] + scaled[1] + residual
    require(np.max(np.abs(reconstruction - mixture)) <= 1.0e-7, "exact remainder reconstruction failed")
    require(np.allclose(coefficients, [0.4, -0.2], atol=1.0e-3), "least-squares scale recovery changed")
    try:
        CORE.recover_stem_scale(np.zeros((1, 800)), mixture)
    except ValueError:
        pass
    else:
        raise SystemExit("invalid stem shape was accepted")


def calibration_checks() -> None:
    policy = CORE.load_policy(ROOT / "policies/sepformer-four-stem-target-me-qualification-v1.json")
    rows = []
    for index in range(10):
        rows.append(
            {
                "target_present": True,
                "other_local_present": True,
                "assignment_correct": True,
                "query_collapsed": False,
                "paired_cosine_margin": 0.3 + index * 0.01,
                "target_presence_margin": 0.4 + index * 0.01,
            }
        )
    for index in range(10):
        rows.append(
            {
                "target_present": False,
                "other_local_present": True,
                "assignment_correct": None,
                "query_collapsed": False,
                "paired_cosine_margin": 0.2,
                "target_presence_margin": -0.4 - index * 0.01,
            }
        )
    thresholds, evidence = CORE.calibrate_thresholds(policy, rows)
    require(thresholds["paired_cosine_margin"] >= 0.3, "paired threshold ignored train evidence")
    require(thresholds["target_presence_margin"] >= 0.0, "presence threshold violated contract")
    require(evidence["assignment_error_rate"] == 0.0, "assignment error calculation changed")
    require(evidence["presence_false_accept_rate"] == 0.0, "negative presence rows were accepted")


def fallback_checks() -> None:
    with tempfile.TemporaryDirectory(prefix="murmurmark-sepformer-row-") as temporary:
        corpus = Path(temporary)
        values = {
            "target_me": np.full(8000, 0.01, dtype=np.float32),
            "remote_echo": np.full(8000, 0.02, dtype=np.float32),
            "other_local": np.full(8000, -0.01, dtype=np.float32),
            "unexplained_residual": np.zeros(8000, dtype=np.float32),
        }
        values["mixture"] = sum(values.values()).astype(np.float32)
        values["local_mixture"] = (values["mixture"] - values["remote_echo"]).astype(np.float32)
        audio = {}
        for name, value in values.items():
            path = corpus / f"{name}.wav"
            CORE.write_audio(path, value)
            audio[name] = CORE.artifact(path, corpus)
        row = {
            "item_id": "fixture",
            "split": "train",
            "speaker_id": "other",
            "family": "ordinary_double_talk",
            "usage": "fixture",
            "target_present": True,
            "other_local_present": True,
            "audio": audio,
        }
        target_query = np.array([1.0, 0.0])
        other_query = np.array([0.0, 1.0])
        result = CORE.evaluate_row(
            corpus=corpus,
            row=row,
            stems=np.stack([values["target_me"], values["other_local"]]),
            embeddings=np.stack([target_query, other_query]),
            valid=np.array([True, True]),
            enrollments={("train", "private_target_me_v1"): target_query, ("train", "other"): other_query},
            thresholds={"paired_cosine_margin": 10.0, "target_presence_margin": 10.0},
        )
        require(result["selected"] is False, "weak evidence selected candidate")
        require(result["metrics"]["fallback_exact"] is True, "fallback was not sample-exact")


def stable_fingerprint_checks() -> None:
    body = {"schema": "fixture", "policy_sha256": "abc", "decision": "ok"}
    report = {**body, "fingerprint": CORE.digest_json(body), "runtime": {"wall_sec": 1.0}}
    require(CORE.stable_report_fingerprint_valid(report), "runtime broke stable fingerprint")
    report["decision"] = "changed"
    require(not CORE.stable_report_fingerprint_valid(report), "changed stable report passed")


def resumable_cache_checks() -> None:
    with tempfile.TemporaryDirectory(prefix="murmurmark-sepformer-cache-") as temporary:
        root = Path(temporary)
        path = root / "fixture.npy"
        meta_path = root / "fixture.json"
        values = np.ones((2, 8), dtype=np.float32)
        CORE.write_npy(path, values)
        stable = {
            "schema": "murmurmark.sepformer_four_stem_cache_item/v1",
            "item_id": "fixture",
            "split": "train",
            "shape": [2, 8],
            "sha256": CORE.sha256(path),
            "coefficients": [1.0, 1.0],
            "finite": True,
        }
        report = CORE.with_fingerprint(stable)
        report["runtime"] = {"inference_sec": 1.25}
        report["fingerprint"] = CORE.digest_json(stable)
        CORE.write_json(meta_path, report)
        valid, loaded = CORE.valid_cache_item(
            path=path, meta_path=meta_path, item_id="fixture", split="train", samples=8
        )
        require(valid and loaded is not None, "resumable cache item was rejected")
        report.pop("runtime")
        CORE.write_json(meta_path, report)
        valid, _ = CORE.valid_cache_item(
            path=path, meta_path=meta_path, item_id="fixture", split="train", samples=8
        )
        require(not valid, "cache item without cumulative runtime was accepted")


def terminal_decision_checks() -> None:
    require(
        CORE.terminal_decision_for(CORE.TRAIN_LOCKED, CORE.DEV_LOCKED) == CORE.READY,
        "locked dev did not authorize hard test",
    )
    require(
        CORE.terminal_decision_for(CORE.TRAIN_REJECTED, None) == CORE.REJECTED,
        "quality rejection became a resource limit",
    )
    require(
        CORE.terminal_decision_for(CORE.TRAIN_RESOURCE_LIMIT, None) == CORE.RESOURCE_LIMIT,
        "train resource limit became a quality rejection",
    )
    require(
        CORE.terminal_decision_for(CORE.TRAIN_LOCKED, CORE.DEV_RESOURCE_LIMIT) == CORE.RESOURCE_LIMIT,
        "dev resource limit became a quality rejection",
    )


def frozen_result_checks() -> None:
    output = ROOT / "sessions/_reports/sepformer-four-stem-target-me-qualification-v1"
    corpus_manifest = output / "corpus/corpus_manifest.json"
    if corpus_manifest.is_file():
        manifest = CORE.read_json(corpus_manifest)
        require(manifest["future_hard_files_read"] == 0, "materialization opened future-hard")
        require(manifest["hard_or_sealed_opened"] is False, "materialization opened protected data")
        require(manifest["splits"]["train"]["items"] == 180, "unexpected frozen train count")
        require(manifest["splits"]["dev"]["items"] == 60, "unexpected frozen dev count")
    decision_path = output / "decision.json"
    if decision_path.is_file():
        decision = CORE.read_json(decision_path)
        require(decision["decision"] in {CORE.READY, CORE.REJECTED, CORE.RESOURCE_LIMIT}, "invalid decision")
        require(decision["production_changed"] is False, "qualification changed production")
        require(decision["future_hard_opened"] is False, "qualification opened future-hard")
        require(decision["direct_asr_opened"] is False, "qualification opened direct ASR")


def main() -> int:
    policy_checks()
    scale_recovery_checks()
    calibration_checks()
    fallback_checks()
    stable_fingerprint_checks()
    resumable_cache_checks()
    terminal_decision_checks()
    frozen_result_checks()
    print("SepFormer four-stem Target-Me qualification v1 checks ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
