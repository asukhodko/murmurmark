#!/usr/bin/env python3
"""Deterministic contract checks for Target-Me speaker-query separation v2."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

import numpy as np


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import reference_conditioned_separator_v2 as SEPARATOR  # noqa: E402


def load_controller() -> Any:
    path = ROOT / "scripts/reference-conditioned-target-me-separation-v2.py"
    spec = importlib.util.spec_from_file_location("murmurmark_reference_conditioned_v2_check", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CORE = load_controller()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def policy_checks() -> None:
    policy = CORE.load_policy(ROOT / "policies/reference-conditioned-target-me-separation-v2.json")
    require(policy["identifiability_corpus"]["fingerprint"] == "530cb0fd23503884d438bc24be10fff45610da1fb8fe710aad1b6b6cd992b2ce", "READY corpus fingerprint changed")
    require(policy["data_isolation"]["hard_audio_access_before_candidate_lock"] is False, "hard access became permissive")
    require(policy["data_isolation"]["threshold_tuning_on_hard_or_sealed"] is False, "hard/sealed tuning became allowed")
    require(policy["audio_contract"]["post_asr_cleanup_promotion_credit"] == 0, "post-ASR cleanup received credit")
    require(policy["production_baseline"]["fallback"] == "byte_exact_speaker_preserving_neural_echo_v2", "fallback changed")
    require(
        policy["candidate"]["dev_gates"]["target_me_snr_db_median_min"] == 12.0,
        "Target-Me dev gate changed",
    )
    require(
        policy["candidate"]["dev_gates"]["paired_query_margin_db_median_min"] == 3.0,
        "paired-query dev gate changed",
    )


def separator_checks() -> None:
    import torch

    SEPARATOR.configure_determinism(41)
    timeline = np.arange(SEPARATOR.CLIP_SAMPLES, dtype=np.float32) / SEPARATOR.SAMPLE_RATE
    target = (0.05 * np.sin(2.0 * np.pi * 211.0 * timeline)).astype(np.float32)
    other = (0.04 * np.sin(2.0 * np.pi * 347.0 * timeline)).astype(np.float32)
    echo = (0.03 * np.sin(2.0 * np.pi * 503.0 * timeline)).astype(np.float32)
    mixture = torch.from_numpy((target + other + echo)[None])
    echo_tensor = torch.from_numpy(echo[None])
    query_a = torch.nn.functional.normalize(torch.tensor([[1.0, 0.0, 0.0, 0.0]]), dim=-1)
    query_b = torch.nn.functional.normalize(torch.tensor([[0.0, 1.0, 0.0, 0.0]]), dim=-1)
    model = SEPARATOR.build_model(enrollment_dim=4, hidden_size=12, layers=1)
    window = torch.from_numpy(SEPARATOR.analysis_window())
    with torch.no_grad():
        first = SEPARATOR.apply_model(model, mixture, echo_tensor, query_a, window)
        second = SEPARATOR.apply_model(model, mixture, echo_tensor, query_b, window)
    for stems in (first, second):
        reconstruction = stems["query_target"] + stems["remote_echo"] + stems["other_local"]
        require(torch.max(torch.abs(reconstruction - mixture)).item() <= 1.0e-7, "separator does not conserve the mixture")
        require(torch.max(torch.abs(stems["query_target"])).item() <= 1.0e-7, "zero initialization changed target audio")

    with tempfile.TemporaryDirectory(prefix="murmurmark-target-query-v2-") as temporary:
        checkpoint = Path(temporary) / "separator.pt"
        metadata = {"enrollment_dim": 4, "hidden_size": 12, "layers": 1, "mask_limit": 4.0, "marker": "fixture"}
        SEPARATOR.save_checkpoint(checkpoint, model, metadata)
        loaded, observed = SEPARATOR.load_checkpoint(checkpoint)
        require(observed == metadata, "checkpoint metadata changed")
        require(SEPARATOR.model_state_fingerprint(model) == SEPARATOR.model_state_fingerprint(loaded), "checkpoint weights changed")


def batch_pair_checks() -> None:
    waveforms = np.zeros((1, 5, SEPARATOR.CLIP_SAMPLES), dtype=np.float32)
    waveforms[0, 0] = 0.7
    waveforms[0, 1] = 0.2
    waveforms[0, 2] = 0.3
    waveforms[0, 3] = 0.1
    waveforms[0, 4] = 0.1
    rows = [{
        "item_id": "pair",
        "family": "ordinary_double_talk",
        "usage": "full_three_source",
        "queries": {
            "target_me": {"speaker_present": True, "enrollment": {"path": "target.npy"}},
            "other_local_speech": {"speaker_present": True, "enrollment": {"path": "other.npy"}},
        },
    }]
    vectors = {"target.npy": np.asarray([1.0, 0.0], dtype=np.float32), "other.npy": np.asarray([0.0, 1.0], dtype=np.float32)}
    mixture, echo, target, enrollment, metadata = CORE.prepare_item_batch(waveforms, rows, [0], vectors)
    require(mixture.shape == (2, SEPARATOR.CLIP_SAMPLES), "paired mixture shape changed")
    require(np.allclose(target[0], 0.2) and np.allclose(target[1], 0.3), "paired targets were swapped")
    require(np.allclose(echo, 0.1), "paired echo accounting changed")
    require(not np.array_equal(enrollment[0], enrollment[1]), "paired enrollment collapsed")
    require([row["query_role"] for row in metadata] == ["target_me", "other_local_speech"], "paired query order changed")


def gate_checks() -> None:
    families = [
        "ordinary_double_talk", "quiet_target_me", "quiet_other_local", "keyboard_background",
        "opening_backchannel", "target_only", "remote_only", "other_speaker_only", "target_remote", "target_other",
    ]
    summary = lambda value: {"count": 1, "min": value, "p05": value, "median": value, "p95": value, "max": value}
    aggregate = {
        "metrics": {
            "paired_query_margin_db": summary(5.0),
            "absent_query_attenuation_db": summary(18.0),
            "remote_only_attenuation_db": summary(20.0),
            "reconstruction_max_abs_error": summary(0.0),
            "remote_echo_snr_db": summary(100.0),
        },
        "roles": {
            "target_me": {"target_snr_db": summary(13.0), "target_snr_improvement_db": summary(4.0)},
            "other_local_speech": {"target_snr_db": summary(13.0), "target_snr_improvement_db": summary(4.0)},
        },
        "families": {family: {"target_snr_db": summary(13.0)} for family in families},
        "query_collapse_rate": 0.0,
        "clipped_outputs": 0,
        "non_finite_outputs": 0,
    }
    policy = CORE.load_policy(ROOT / "policies/reference-conditioned-target-me-separation-v2.json")
    checks = CORE.selection_checks(aggregate, policy["candidate"]["dev_gates"], families, hard=False)
    require(all(row["passed"] for row in checks), "passing speaker-query fixture was rejected")
    aggregate["roles"]["target_me"]["target_snr_db"] = summary(2.0)
    checks = CORE.selection_checks(aggregate, policy["candidate"]["dev_gates"], families, hard=False)
    require(any(row["name"] == "target_me_snr_db_median" and not row["passed"] for row in checks), "weak Target-Me output passed")


def hard_access_checks() -> None:
    policy_path = ROOT / "policies/reference-conditioned-target-me-separation-v2.json"
    with tempfile.TemporaryDirectory(prefix="murmurmark-hard-lock-v2-") as temporary:
        output = Path(temporary)
        candidate = output / "train-dev"
        candidate.mkdir(parents=True)
        model = SEPARATOR.build_model(enrollment_dim=4, hidden_size=8, layers=1)
        checkpoint = candidate / "separator.pt"
        SEPARATOR.save_checkpoint(checkpoint, model, {"enrollment_dim": 4, "hidden_size": 8, "layers": 1, "mask_limit": 4.0})
        report = {"decision": CORE.DEV_REJECTED, "fingerprint": "rejected-fixture"}
        (candidate / "train_dev_report.json").write_text(json.dumps(report), encoding="utf-8")
        lock = {
            "schema": "murmurmark.reference_conditioned_target_me_candidate_lock/v2",
            "decision": CORE.DEV_REJECTED,
            "candidate_id": "fixture",
            "candidate_fingerprint": report["fingerprint"],
            "policy_sha256": CORE.sha256(policy_path),
            "corpus_fingerprint": "fixture",
            "model_state_fingerprint": SEPARATOR.model_state_fingerprint(model),
            "checkpoint_sha256": CORE.sha256(checkpoint),
            "hard_test_access_authorized": False,
            "sealed_access_authorized": False,
        }
        lock["fingerprint"] = CORE.digest_json(lock)
        (candidate / "candidate_lock.json").write_text(json.dumps(lock), encoding="utf-8")
        try:
            CORE.run_hard_test(policy_path=policy_path, output_dir=output)
        except RuntimeError as error:
            require("access denied" in str(error), "rejected lock failed for the wrong reason")
        else:
            raise SystemExit("rejected candidate opened hard-test")
        require(not (output / "hard_access.json").exists(), "rejected candidate consumed hard access")


def frozen_result_checks() -> None:
    root = ROOT / "sessions/_reports/reference-conditioned-target-me-separation-v2"
    decision_path = root / "decision.json"
    if not decision_path.is_file():
        return
    decision = CORE.read_json(decision_path)
    require(decision["decision"] in {CORE.PROMOTE, CORE.DO_NOT_PROMOTE}, "invalid frozen v2 decision")
    require(decision["post_asr_cleanup_credit"] == 0, "frozen result credits post-ASR cleanup")
    if decision["decision"] == CORE.DO_NOT_PROMOTE:
        require(decision["production_unchanged"] is True, "rejected experiment changed production")
        require(not (root / "hard_access.json").exists(), "dev-rejected frozen result opened hard")


def main() -> int:
    policy_checks()
    separator_checks()
    batch_pair_checks()
    gate_checks()
    hard_access_checks()
    frozen_result_checks()
    print("reference-conditioned target-me separation v2 checks ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
