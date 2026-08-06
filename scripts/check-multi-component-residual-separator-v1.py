#!/usr/bin/env python3
"""Deterministic contract checks for the four-stem separator qualification."""

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

import multi_component_residual_separator_v1 as SEPARATOR  # noqa: E402


def load_controller() -> Any:
    path = ROOT / "scripts/multi-component-residual-separator-v1.py"
    spec = importlib.util.spec_from_file_location("murmurmark_multi_component_check", path)
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


def summary(value: float) -> dict[str, float | int]:
    return {"count": 1, "min": value, "p05": value, "median": value, "p95": value, "max": value}


def policy_checks() -> None:
    policy = CORE.load_policy(ROOT / "policies/multi-component-residual-separator-v1.json")
    contract = policy["decomposition_contract"]
    require(
        contract["stems"] == ["target_me", "remote_echo", "other_local", "unexplained_residual"],
        "four-stem contract changed",
    )
    require(contract["mixture_consistency"] == "other_local_is_exact_remainder", "mixture consistency changed")
    require(contract["outside_selected_samples"] == "sample_exact_production_v2", "fallback changed")
    require(policy["post_asr_cleanup_promotion_credit"] == 0, "post-ASR cleanup received credit")
    require(policy["data_isolation"]["hard_audio_access_before_locked_dev_pass"] is False, "hard access became permissive")
    require(policy["data_isolation"]["threshold_tuning_on_hard_or_sealed"] is False, "hard/sealed tuning became allowed")
    trainable = [row for row in policy["candidate_ladder"] if row.get("promotion_eligible")]
    require([row["id"] for row in trainable] == ["four_stem_film_gru_v1"], "bounded ladder changed")


def separator_checks() -> None:
    import torch

    SEPARATOR.configure_determinism(73)
    timeline = np.arange(SEPARATOR.CLIP_SAMPLES, dtype=np.float32) / SEPARATOR.SAMPLE_RATE
    target = (0.05 * np.sin(2.0 * np.pi * 211.0 * timeline)).astype(np.float32)
    other = (0.04 * np.sin(2.0 * np.pi * 347.0 * timeline)).astype(np.float32)
    echo = (0.03 * np.sin(2.0 * np.pi * 503.0 * timeline)).astype(np.float32)
    residual = (0.005 * np.sin(2.0 * np.pi * 743.0 * timeline)).astype(np.float32)
    mixture = torch.from_numpy((target + other + echo + residual)[None])
    echo_tensor = torch.from_numpy(echo[None])
    query = torch.nn.functional.normalize(torch.tensor([[1.0, 0.0, 0.0, 0.0]]), dim=-1)
    model = SEPARATOR.build_model(enrollment_dim=4, hidden_size=12, layers=1)
    window = torch.from_numpy(SEPARATOR.analysis_window())
    with torch.no_grad():
        stems = SEPARATOR.apply_model(model, mixture, echo_tensor, query, window)
    reconstruction = stems["target_me"] + stems["remote_echo"] + stems["other_local"] + stems["unexplained_residual"]
    require(torch.max(torch.abs(reconstruction - mixture)).item() <= 1.0e-7, "separator does not conserve mixture")
    require(torch.max(torch.abs(stems["target_me"])).item() <= 1.0e-7, "zero initialization changed target")
    require(torch.max(torch.abs(stems["unexplained_residual"])).item() <= 1.0e-7, "zero initialization changed residual")
    require(torch.equal(stems["remote_echo"], echo_tensor), "frozen echo hint changed")

    with tempfile.TemporaryDirectory(prefix="murmurmark-four-stem-") as temporary:
        checkpoint = Path(temporary) / "separator.pt"
        metadata = {"enrollment_dim": 4, "hidden_size": 12, "layers": 1, "mask_limit": 4.0}
        SEPARATOR.save_checkpoint(checkpoint, model, metadata)
        loaded, observed = SEPARATOR.load_checkpoint(checkpoint)
        require(observed == metadata, "checkpoint metadata changed")
        require(SEPARATOR.model_state_fingerprint(model) == SEPARATOR.model_state_fingerprint(loaded), "checkpoint weights changed")


def batch_checks() -> None:
    waveforms = np.zeros((1, 5, SEPARATOR.CLIP_SAMPLES), dtype=np.float32)
    waveforms[0, 0] = 1.0
    waveforms[0, 1] = 0.2
    waveforms[0, 2] = 0.3
    waveforms[0, 3] = 0.4
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
    vectors = {
        "target.npy": np.asarray([1.0, 0.0], dtype=np.float32),
        "other.npy": np.asarray([0.0, 1.0], dtype=np.float32),
    }
    mixture, echo, target, other, residual, enrollment, metadata = CORE.prepare_batch(
        waveforms, rows, [0], vectors
    )
    require(mixture.shape == (2, SEPARATOR.CLIP_SAMPLES), "paired mixture shape changed")
    require(np.allclose(target[0], 0.2) and np.allclose(target[1], 0.3), "paired target order changed")
    require(np.allclose(other[0], 0.3) and np.allclose(other[1], 0.2), "other-local complement changed")
    require(np.allclose(echo, 0.4) and np.allclose(residual, 0.1), "shared stems changed")
    require(not np.array_equal(enrollment[0], enrollment[1]), "paired enrollment collapsed")
    require([row["query_role"] for row in metadata] == ["target_me", "other_local_speech"], "paired role order changed")


def gate_checks() -> None:
    families = [
        "ordinary_double_talk", "quiet_target_me", "quiet_other_local", "keyboard_background",
        "opening_backchannel", "target_only", "remote_only", "other_speaker_only", "target_remote", "target_other",
    ]
    aggregate = {
        "metrics": {
            "other_local_snr_db": summary(12.0),
            "paired_query_margin_db": summary(6.0),
            "absent_query_attenuation_db": summary(18.0),
            "unexplained_residual_snr_db": summary(10.0),
            "remote_echo_snr_db": summary(100.0),
            "reconstruction_max_abs_error": summary(0.0),
        },
        "roles": {
            "target_me": {"target_snr_db": summary(12.0), "target_snr_improvement_db": summary(5.0)},
            "other_local_speech": {"target_snr_db": summary(12.0), "target_snr_improvement_db": summary(5.0)},
        },
        "families": {family: {"target_snr_db": summary(12.0)} for family in families},
        "query_collapse_rate": 0.0,
        "clipped_outputs": 0,
        "non_finite_outputs": 0,
    }
    policy = CORE.load_policy(ROOT / "policies/multi-component-residual-separator-v1.json")
    checks = CORE.selection_checks(aggregate, policy["gates"]["dev"], families, runtime_sec=10.0, hard=False)
    require(all(row["passed"] for row in checks), "passing four-stem fixture was rejected")
    slower_checks = CORE.selection_checks(
        aggregate,
        policy["gates"]["dev"],
        families,
        runtime_sec=20.0,
        hard=False,
    )
    require(
        CORE.stable_selection_checks(checks) == CORE.stable_selection_checks(slower_checks),
        "wall time changed deterministic model-quality evidence",
    )
    require(
        any(row["name"] == "runtime_sec" for row in checks),
        "runtime stopped being an acceptance gate",
    )
    aggregate["roles"]["target_me"]["target_snr_db"] = summary(2.0)
    checks = CORE.selection_checks(aggregate, policy["gates"]["dev"], families, runtime_sec=10.0, hard=False)
    require(any(row["name"] == "target_me_snr_db_median" and not row["passed"] for row in checks), "weak Target-Me output passed")


def hard_access_checks() -> None:
    policy_path = ROOT / "policies/multi-component-residual-separator-v1.json"
    with tempfile.TemporaryDirectory(prefix="murmurmark-four-stem-lock-") as temporary:
        output = Path(temporary)
        candidate = output / "train-dev"
        candidate.mkdir(parents=True)
        model = SEPARATOR.build_model(enrollment_dim=4, hidden_size=8, layers=1)
        checkpoint = candidate / "separator.pt"
        SEPARATOR.save_checkpoint(checkpoint, model, {"enrollment_dim": 4, "hidden_size": 8, "layers": 1, "mask_limit": 4.0})
        report = {"decision": CORE.DEV_REJECTED, "fingerprint": "rejected-fixture"}
        (candidate / "train_dev_report.json").write_text(json.dumps(report), encoding="utf-8")
        lock = {
            "schema": "murmurmark.multi_component_residual_separator_candidate_lock/v1",
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
            require("access denied" in str(error), "rejected lock failed for wrong reason")
        else:
            raise SystemExit("rejected candidate opened hard-test")
        require(not (output / "hard_access.json").exists(), "rejected candidate consumed hard access")


def frozen_result_checks() -> None:
    root = ROOT / "sessions/_reports/multi-component-residual-separator-v1"
    decision_path = root / "decision.json"
    if not decision_path.is_file():
        return
    decision = CORE.read_json(decision_path)
    require(decision["decision"] in {CORE.PROMOTE, CORE.STRONGER, CORE.RESOURCE_LIMIT}, "invalid terminal decision")
    require(decision["production_changed"] is False, "qualification changed production")
    if decision["train_dev_decision"] == CORE.DEV_REJECTED:
        require(not (root / "hard_access.json").exists(), "dev-rejected result opened hard")
        require(not (root / "sealed_access.json").exists(), "dev-rejected result opened sealed")


def main() -> int:
    policy_checks()
    separator_checks()
    batch_checks()
    gate_checks()
    hard_access_checks()
    frozen_result_checks()
    print("multi-component residual separator v1 checks ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
