#!/usr/bin/env python3
"""Deterministic fixture checks for Reference-Conditioned Target-Me Separation v1."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent


def load_module() -> Any:
    path = ROOT / "scripts/reference-conditioned-target-me-separation-v1.py"
    spec = importlib.util.spec_from_file_location("murmurmark_reference_conditioned_v1", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CORE = load_module()
sys.path.insert(0, str(ROOT / "scripts"))
import reference_conditioned_separator_v1 as SEPARATOR  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def artifact(path: Path, relative_to: Path) -> dict[str, Any]:
    return {
        "path": str(path.relative_to(relative_to)),
        "bytes": path.stat().st_size,
        "sha256": CORE.sha256(path),
    }


def build_fixture(root: Path) -> tuple[Path, Path, Path]:
    corpus_root = root / "sessions/_reports/controlled"
    corpus_root.mkdir(parents=True)
    audio_path = corpus_root / "examples/train/example.raw"
    audio_path.parent.mkdir(parents=True)
    audio_path.write_bytes(b"deterministic-controlled-audio")
    audio = artifact(audio_path, corpus_root)

    rows = []
    cases = (
        ("train", "synthetic_double_talk"),
        ("train", "measured_local_target"),
        ("train", "measured_remote_echo"),
        ("dev", "synthetic_double_talk"),
        ("hard_test", "measured_double_talk"),
    )
    for index, (split, kind) in enumerate(cases):
        rows.append(
            {
                "schema": "murmurmark.controlled_echo_supervision_item/v1",
                "clip_id": f"fixture-{index}",
                "split": split,
                "kind": kind,
                "duration_sec": 4.0,
                "audio": audio,
            }
        )
    manifest = corpus_root / "supervision_manifest.jsonl"
    manifest.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )

    production = root / "policies/production.json"
    write_json(
        production,
        {
            "decision": "PROMOTE_SPEAKER_PRESERVING_NEURAL_ECHO_V2",
            "selected_profile": "speaker_preserving_neural_echo_v2",
            "corpus_fingerprint": "sealed-fixture",
        },
    )
    frozen = corpus_root / "frozen_corpus.json"
    split_manifest = corpus_root / "split_manifest.json"
    corpus_decision = corpus_root / "corpus_decision.json"
    replay_report = corpus_root / "replay_report.json"
    write_json(frozen, {"status": "frozen"})
    write_json(split_manifest, {"status": "frozen"})
    write_json(
        corpus_decision,
        {"decision": "READY_FOR_ADAPTATION", "fingerprint": "controlled-fixture"},
    )
    write_json(replay_report, {"status": "passed", "matched_files": 1})

    sealed_root = root / "sessions/_reports/sealed"
    sealed_report = sealed_root / "corpus_report.json"
    sealed_decision = sealed_root / "promotion_decision.json"
    sealed_manifest = sealed_root / "evaluation_manifest.json"
    write_json(
        sealed_report,
        {
            "passed": True,
            "promotion": {"decision": "PROMOTE_SPEAKER_PRESERVING_NEURAL_ECHO_V2"},
            "corpus_fingerprint": "sealed-fixture",
        },
    )
    write_json(
        sealed_decision,
        {
            "decision": "PROMOTE_SPEAKER_PRESERVING_NEURAL_ECHO_V2",
            "corpus_fingerprint": "sealed-fixture",
        },
    )
    write_json(
        sealed_manifest,
        {"fingerprint": "sealed-fixture", "basis": {"sessions": [{"session_id": "one"}]}},
    )

    model_root = root / "models/target-me"
    model_root.mkdir(parents=True)
    model_file = model_root / "model.bin"
    model_file.write_bytes(b"fixture-target-me-model")
    import numpy as np

    enrollment_root = root / "sessions/_reports/enrollment"
    enrollment_card = enrollment_root / "card.json"
    enrollment_vector = enrollment_root / "vector.npy"
    write_json(
        enrollment_card,
        {"backend": "fixture_xvector", "train_local_embeddings": 2},
    )
    enrollment_root.mkdir(parents=True, exist_ok=True)
    np.save(enrollment_vector, np.full(4, 0.5, dtype=np.float32))

    policy_path = root / "policies/reference-conditioned-target-me-separation-v1.json"
    policy = {
        "schema": CORE.SCHEMA,
        "profile": "reference_conditioned_target_me_separation_v1",
        "status": "preflight_locked",
        "production_baseline": {
            "profile": "speaker_preserving_neural_echo_v2",
            "policy": str(production.relative_to(root)),
            "policy_sha256": CORE.sha256(production),
            "decision": "PROMOTE_SPEAKER_PRESERVING_NEURAL_ECHO_V2",
            "corpus_fingerprint": "sealed-fixture",
            "fallback": "byte_exact_speaker_preserving_neural_echo_v2",
        },
        "controlled_supervision": {
            "frozen_corpus": str(frozen.relative_to(root)),
            "frozen_corpus_sha256": CORE.sha256(frozen),
            "split_manifest": str(split_manifest.relative_to(root)),
            "split_manifest_sha256": CORE.sha256(split_manifest),
            "supervision_manifest": str(manifest.relative_to(root)),
            "supervision_manifest_sha256": CORE.sha256(manifest),
            "corpus_decision": str(corpus_decision.relative_to(root)),
            "corpus_decision_sha256": CORE.sha256(corpus_decision),
            "corpus_fingerprint": "controlled-fixture",
            "replay_report": str(replay_report.relative_to(root)),
            "replay_report_sha256": CORE.sha256(replay_report),
            "replay_matched_files": 1,
        },
        "sealed_evaluation": {
            "corpus_report": str(sealed_report.relative_to(root)),
            "corpus_report_sha256": CORE.sha256(sealed_report),
            "promotion_decision": str(sealed_decision.relative_to(root)),
            "promotion_decision_sha256": CORE.sha256(sealed_decision),
            "evaluation_manifest": str(sealed_manifest.relative_to(root)),
            "evaluation_manifest_sha256": CORE.sha256(sealed_manifest),
            "session_count": 1,
        },
        "models": {
            "target_me_encoder": {
                "model_id": "fixture",
                "local_path": str(model_root),
                "files": {"model.bin": CORE.sha256(model_file)},
            }
        },
        "reference_enrollment": {
            "backend": "fixture_xvector",
            "source_split": "train",
            "source_embedding_count": 2,
            "dimension": 4,
            "card": str(enrollment_card.relative_to(root)),
            "card_sha256": CORE.sha256(enrollment_card),
            "vector": str(enrollment_vector.relative_to(root)),
            "vector_sha256": CORE.sha256(enrollment_vector),
        },
        "preflight_gates": {
            "controlled_decision": "READY_FOR_ADAPTATION",
            "replay_status": "passed",
            "required_core_modules": [],
            "optional_toolkits": [],
            "minimum_train_synthetic_double_talk": 1,
            "minimum_dev_synthetic_double_talk": 1,
            "minimum_train_measured_local_target": 1,
            "minimum_train_measured_remote_echo": 1,
            "minimum_hard_measured_double_talk": 1,
        },
    }
    write_json(policy_path, policy)
    return policy_path, audio_path, model_file


def run_fixture_checks() -> None:
    with tempfile.TemporaryDirectory(prefix="murmurmark-reference-conditioned-") as temporary:
        root = Path(temporary)
        policy, audio, model = build_fixture(root)
        output = root / "output"
        raw_before = CORE.sha256(audio)
        first = CORE.run_preflight(
            repo_root=root,
            policy_path=policy,
            output_dir=output,
            verify_audio="all",
            sample_artifacts=1,
        )
        second = CORE.run_preflight(
            repo_root=root,
            policy_path=policy,
            output_dir=output,
            verify_audio="all",
            sample_artifacts=1,
        )
        require(first["decision"] == CORE.READY, "valid fixture did not pass preflight")
        require(first["fingerprint"] == second["fingerprint"], "preflight fingerprint is unstable")
        require(CORE.sha256(audio) == raw_before, "preflight changed frozen audio")
        require((output / "frozen_inputs.json").is_file(), "frozen inputs were not written")
        require((output / "preflight_report.md").is_file(), "Markdown report was not written")

        audio.write_bytes(b"changed-controlled-audio")
        changed_audio = CORE.run_preflight(
            repo_root=root,
            policy_path=policy,
            output_dir=output,
            verify_audio="all",
            sample_artifacts=1,
        )
        require(changed_audio["decision"] == CORE.BLOCKED, "changed audio did not block preflight")
        require(
            "controlled_audio_verification" in changed_audio["blockers"],
            "changed audio blocker is not explicit",
        )

        audio.write_bytes(b"deterministic-controlled-audio")
        model.unlink()
        missing_model = CORE.run_preflight(
            repo_root=root,
            policy_path=policy,
            output_dir=output,
            verify_audio="all",
            sample_artifacts=1,
        )
        require(missing_model["decision"] == CORE.BLOCKED, "missing model did not block preflight")
        require("pinned_models" in missing_model["blockers"], "missing model blocker is not explicit")


def policy_checks() -> None:
    policy_path = ROOT / "policies/reference-conditioned-target-me-separation-v1.json"
    policy = CORE.read_json(policy_path)
    require(policy.get("schema") == CORE.SCHEMA, "unexpected reference-conditioned policy schema")
    require(policy.get("status") == "preflight_locked", "reference-conditioned policy must be locked")
    require(
        policy.get("audio_contract", {}).get("stems")
        == ["target_me", "remote_echo", "other_local"],
        "three-stem contract changed",
    )
    require(
        policy.get("audio_contract", {}).get("post_asr_cleanup_promotion_credit") == 0,
        "post-ASR cleanup received promotion credit",
    )
    require(
        policy.get("production_baseline", {}).get("fallback")
        == "byte_exact_speaker_preserving_neural_echo_v2",
        "production fallback changed",
    )
    require(
        policy.get("sealed_evaluation", {}).get("threshold_tuning_use") == "forbidden",
        "sealed corpus can be used for threshold tuning",
    )
    require(
        policy.get("training_cache", {}).get("train_fingerprint")
        == "6262fdbe9414bc7141e5769eb67d44a1f168a661e3b6b0bf121496894ae5c013",
        "frozen train cache changed",
    )
    require(
        policy.get("train_dev_candidate", {}).get("model_kinds")
        == ["synthetic_double_talk", "measured_remote_echo", "local_remote_negative"],
        "train/dev model routing changed",
    )


def oracle_mask_checks() -> None:
    import numpy as np

    timeline = np.arange(64_000, dtype=np.float64) / 16_000.0
    target = 0.08 * np.sin(2.0 * np.pi * 233.0 * timeline)
    echo = 0.05 * np.sin(2.0 * np.pi * 617.0 * timeline + 0.2)
    mixture = target + echo
    stems = CORE.ideal_mask_separate(
        mixture,
        target,
        echo,
        family="ideal_complex_mask",
    )
    reconstruction = stems["target_me"] + stems["remote_echo"] + stems["other_local"]
    require(
        float(np.max(np.abs(mixture - reconstruction))) <= 1.0e-12,
        "three-stem oracle does not conserve the mixture",
    )
    require(
        CORE.snr_db(target, stems["target_me"]) >= 50.0,
        "ideal complex mask has an unexpectedly low target ceiling",
    )
    try:
        CORE.ideal_mask_separate(mixture, target, echo, family="unknown")
    except ValueError:
        pass
    else:
        raise SystemExit("unknown oracle family was accepted")


def separator_contract_checks() -> None:
    import numpy as np
    import torch

    SEPARATOR.configure_determinism(17)
    timeline = np.arange(SEPARATOR.CLIP_SAMPLES, dtype=np.float32) / SEPARATOR.SAMPLE_RATE
    target = (0.05 * np.sin(2.0 * np.pi * 191.0 * timeline)).astype(np.float32)
    echo = (0.03 * np.sin(2.0 * np.pi * 479.0 * timeline)).astype(np.float32)
    mixture = torch.from_numpy((target + echo)[None])
    remote = torch.from_numpy(echo[None])
    enrollment = torch.nn.functional.normalize(torch.ones((1, 8)), dim=-1)
    window = torch.from_numpy(SEPARATOR.analysis_window())
    model = SEPARATOR.build_model(enrollment_dim=8, hidden_size=12, layers=1)
    with torch.no_grad():
        stems = SEPARATOR.apply_model(model, mixture, remote, enrollment, window)
    reconstruction = stems["target_me"] + stems["remote_echo"] + stems["other_local"]
    require(
        torch.max(torch.abs(reconstruction - mixture)).item() <= 1.0e-7,
        "separator does not conserve the mixture",
    )
    require(
        torch.max(torch.abs(stems["target_me"])).item() <= 1.0e-7,
        "zero-initialized target mask changed",
    )
    require(
        torch.max(torch.abs(stems["remote_echo"])).item() <= 1.0e-7,
        "zero-initialized echo mask changed",
    )
    with tempfile.TemporaryDirectory(prefix="murmurmark-separator-checkpoint-") as temporary:
        checkpoint = Path(temporary) / "model.pt"
        metadata = {
            "enrollment_dim": 8,
            "hidden_size": 12,
            "layers": 1,
            "marker": "fixture",
        }
        SEPARATOR.save_checkpoint(checkpoint, model, metadata)
        loaded, observed = SEPARATOR.load_checkpoint(checkpoint)
        require(observed == metadata, "separator checkpoint metadata changed")
        require(
            CORE.model_state_fingerprint(model) == CORE.model_state_fingerprint(loaded),
            "separator checkpoint weights changed",
        )


def semantic_attribution_checks() -> None:
    import numpy as np

    timeline = np.arange(16_000, dtype=np.float64) / 16_000.0
    target_me = 0.06 * np.sin(2.0 * np.pi * 211.0 * timeline)
    remote_echo = 0.04 * np.sin(2.0 * np.pi * 503.0 * timeline)
    other_speaker = 0.05 * np.sin(2.0 * np.pi * 347.0 * timeline)
    mixture = target_me + remote_echo + other_speaker
    semantically_wrong_target = other_speaker
    semantically_wrong_other = target_me
    reconstruction = semantically_wrong_target + remote_echo + semantically_wrong_other
    require(
        float(np.max(np.abs(mixture - reconstruction))) <= 1.0e-12,
        "swapped stems should still demonstrate exact mixture conservation",
    )
    require(
        CORE.snr_db(target_me, semantically_wrong_target) < 3.0,
        "semantic swap unexpectedly preserved Target-Me",
    )


def rejected_attempt_checks() -> None:
    with tempfile.TemporaryDirectory(prefix="murmurmark-rejected-candidate-") as temporary:
        attempt = Path(temporary) / "attempt"
        attempt.mkdir()
        checkpoint = attempt / "separator.pt"
        checkpoint.write_bytes(b"deterministic-rejected-checkpoint")
        write_json(
            attempt / "train_dev_report.json",
            {
                "decision": "DEV_CANDIDATE_REJECTED",
                "fingerprint": "attempt-fixture",
                "contract_fingerprint": "contract-fixture",
                "hard_test_opened": False,
                "train": {"epochs": 2, "steps": 4},
                "dev": {
                    "aggregate": {
                        "synthetic_double_talk": {
                            "metrics": {
                                "target_snr_db": {"median": 10.0},
                                "target_snr_improvement_db": {"median": 4.0},
                                "echo_snr_db": {"median": 7.0},
                            }
                        }
                    }
                },
                "model": {
                    "sha256": CORE.sha256(checkpoint),
                    "state_fingerprint": "state-fixture",
                },
                "checks": [{"passed": True}, {"passed": False}],
                "blockers": ["synthetic_target_snr_db_median"],
            },
        )
        summary = CORE.summarize_train_dev_attempt(attempt, Path(temporary))
        require(summary["decision"] == "DEV_CANDIDATE_REJECTED", "attempt decision changed")
        require(summary["passed_gate_count"] == 1, "attempt gate count changed")
        checkpoint.write_bytes(b"changed-rejected-checkpoint")
        try:
            CORE.summarize_train_dev_attempt(attempt, Path(temporary))
        except RuntimeError:
            pass
        else:
            raise SystemExit("changed rejected checkpoint was accepted")


def cache_routing_checks() -> None:
    import numpy as np

    waveforms = np.zeros((3, 4, 64_000), dtype=np.float16)
    waveforms[0, 0] = 0.5
    waveforms[0, 2] = 0.4
    waveforms[0, 3] = 0.1
    waveforms[1, 0] = 0.2
    waveforms[1, 3] = 0.3
    waveforms[2, 0] = 0.6
    waveforms[2, 2] = 0.6
    kinds = np.asarray(
        [
            CORE.V2_CORE.KIND_IDS["synthetic_double_talk"],
            CORE.V2_CORE.KIND_IDS["measured_remote_echo"],
            CORE.V2_CORE.KIND_IDS["local_remote_negative"],
        ],
        dtype=np.uint8,
    )
    mixture, _, _, sources, names = CORE.prepare_cached_batch(
        waveforms, kinds, [0, 1, 2]
    )
    require(
        names
        == ["synthetic_double_talk", "measured_remote_echo", "local_remote_negative"],
        "cache kind routing changed",
    )
    tolerance = 1.0e-3
    require(np.allclose(mixture[0], 0.6, atol=tolerance), "cache did not reconstruct mic mixture")
    require(np.allclose(sources[0, 0], 0.4, atol=tolerance), "synthetic target route changed")
    require(np.allclose(sources[0, 1], 0.2, atol=tolerance), "synthetic echo route changed")
    require(np.allclose(sources[1, 0], 0.0, atol=tolerance), "remote-only target is nonzero")
    require(np.allclose(sources[1, 1], 0.5, atol=tolerance), "remote-only echo route changed")
    require(np.allclose(sources[2, 0], 0.6, atol=tolerance), "local negative target route changed")
    require(np.allclose(sources[2, 1], 0.0, atol=tolerance), "local negative echo is nonzero")


def main() -> int:
    policy_checks()
    oracle_mask_checks()
    separator_contract_checks()
    semantic_attribution_checks()
    rejected_attempt_checks()
    cache_routing_checks()
    run_fixture_checks()
    print("reference-conditioned target-me separation v1 checks ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
