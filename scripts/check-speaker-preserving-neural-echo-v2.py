#!/usr/bin/env python3
"""Fast deterministic checks for Speaker-Preserving Neural Echo v2."""

from __future__ import annotations

import json
import importlib.util
import re
import sys
import tempfile
import wave
from pathlib import Path
from typing import Any

import numpy as np


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import speaker_preserving_neural_echo_v2 as CORE  # noqa: E402
import speaker_preserving_echo_arbitration as ARBITER  # noqa: E402
import speaker_preserving_echo_hypothesis_bank as BANK  # noqa: E402


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


V27 = load_module(
    ROOT / "scripts/speaker-preserving-neural-echo-v2-7.py",
    "murmurmark_check_spne_v27",
)
V28 = load_module(
    ROOT / "scripts/speaker-preserving-neural-echo-v2-8.py",
    "murmurmark_check_spne_v28",
)
V29_SHADOW = load_module(
    ROOT / "scripts/speaker-preserving-echo-full-shadow-v2-9.py",
    "murmurmark_check_spne_v29_shadow",
)
V29 = load_module(
    ROOT / "scripts/speaker-preserving-neural-echo-v2-9.py",
    "murmurmark_check_spne_v29",
)
V210 = load_module(
    ROOT / "scripts/speaker-preserving-neural-echo-v2-10.py",
    "murmurmark_check_spne_v210",
)
V211 = load_module(
    ROOT / "scripts/speaker-preserving-neural-echo-v2-11.py",
    "murmurmark_check_spne_v211",
)
V213 = load_module(
    ROOT / "scripts/speaker-preserving-neural-echo-v2-13.py",
    "murmurmark_check_spne_v213",
)
V214_AUDIO = load_module(
    ROOT / "scripts/speaker-preserving-neural-echo-v2-14-audio.py",
    "murmurmark_check_spne_v214_audio",
)
V214 = load_module(
    ROOT / "scripts/speaker-preserving-neural-echo-v2-14.py",
    "murmurmark_check_spne_v214",
)
V215 = load_module(
    ROOT / "scripts/speaker-preserving-neural-echo-v2-15.py",
    "murmurmark_check_spne_v215",
)
V215_EVALUATION = load_module(
    ROOT / "scripts/evaluate-speaker-preserving-neural-echo-v2-15.py",
    "murmurmark_check_spne_v215_evaluation",
)
V216_EVALUATION = load_module(
    ROOT / "scripts/evaluate-speaker-preserving-neural-echo-v2-16.py",
    "murmurmark_check_spne_v216_evaluation",
)
CACHE_SEED = load_module(
    ROOT / "scripts/seed-speaker-preserving-neural-echo-v2-16-cache.py",
    "murmurmark_check_spne_v216_cache_seed",
)
TRANSCRIBE = load_module(
    ROOT / "scripts/transcribe-simple-whispercpp.py",
    "murmurmark_check_spne_transcriber",
)
SYNTHESIS = load_module(
    ROOT / "scripts/synthesize-simple-extractive.py",
    "murmurmark_check_spne_synthesis",
)
PRODUCTION = load_module(
    ROOT / "scripts/apply-speaker-preserving-neural-echo-v2.py",
    "murmurmark_check_spne_v2_production",
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def policy_checks() -> None:
    policy_path = ROOT / "policies/speaker-preserving-neural-echo-v2.json"
    policy = CORE.read_json(policy_path)
    require(
        policy.get("schema") == "murmurmark.speaker_preserving_neural_echo_policy/v2",
        "unexpected v2 policy schema",
    )
    require(policy.get("status") == "pre_hard_locked", "hard-test policy must be locked")
    require(
        policy.get("audio_contract", {}).get("post_asr_cleanup_promotion_credit") == 0,
        "post-ASR cleanup must receive zero promotion credit",
    )
    require(
        policy.get("source", {}).get("fallback") == "local_fir_role_masked",
        "fail-open fallback changed",
    )
    candidate_ids = {
        str(row.get("id"))
        for row in policy.get("candidate_matrix", [])
        if isinstance(row, dict)
    }
    require(
        candidate_ids
        == {
            "magnitude_h96_l1",
            "complex_h128_l1",
            "echo_mapper_h128_l1",
            "echo_mapper_h160_l2",
        },
        "candidate matrix differs from the frozen policy",
    )
    corpus = ROOT / "sessions/_reports/controlled-echo-supervision-v1/frozen_corpus.json"
    if corpus.is_file():
        require(
            CORE.verify_policy_sources(ROOT, policy_path)["passed"],
            "private frozen source verification failed",
        )


def hard_seal_check() -> None:
    with tempfile.TemporaryDirectory(prefix="murmurmark-spne-v2-") as temporary:
        manifest = Path(temporary) / "manifest.jsonl"
        manifest.write_text(
            json.dumps(
                {
                    "split": "hard_test",
                    "kind": "synthetic_double_talk",
                    "item_id": "sealed",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        try:
            CORE.read_manifest_rows(manifest, "hard_test")
        except RuntimeError as error:
            require("sealed" in str(error), "hard split rejection must be explicit")
        else:
            raise SystemExit("development loader opened the hard-test split")


class OracleEchoMapper:
    family = "echo_mapper"

    def __call__(self, features: Any, gate: Any) -> Any:
        import torch

        shape = (*features.shape[:-1], CORE.FREQUENCY_BINS)
        return torch.complex(torch.ones(shape), torch.zeros(shape)).to(features.device)


def causal_audio_checks() -> None:
    import torch

    CORE.configure_determinism(41)
    timeline = np.arange(CORE.CLIP_SAMPLES, dtype=np.float32) / CORE.SAMPLE_RATE
    local = (0.08 * np.sin(2.0 * np.pi * 227.0 * timeline)).astype(np.float32)
    remote = (0.04 * np.sin(2.0 * np.pi * 443.0 * timeline)).astype(np.float32)
    window = torch.from_numpy(CORE.analysis_window())
    model = CORE.build_model("complex_mask", 24, 1)
    with torch.no_grad():
        bypass, _ = CORE.apply_model(
            model,
            torch.from_numpy(local[None]),
            torch.zeros((1, CORE.CLIP_SAMPLES)),
            window,
        )
    require(
        float(torch.max(torch.abs(bypass - torch.from_numpy(local[None])))) <= 1.0e-6,
        "local-only causal bypass changed the waveform",
    )

    mixture = local + remote
    with torch.no_grad():
        clean, _ = CORE.apply_model(
            OracleEchoMapper(),
            torch.from_numpy(mixture[None]),
            torch.from_numpy(remote[None]),
            window,
            torch.from_numpy(remote[None]),
        )
    require(
        CORE.snr_db(local, clean.numpy()[0]) >= 90.0,
        "oracle residual mapper did not preserve local speech",
    )


def checkpoint_check() -> None:
    import torch

    CORE.configure_determinism(53)
    model = CORE.build_model("echo_mapper", 16, 1)
    with tempfile.TemporaryDirectory(prefix="murmurmark-spne-v2-checkpoint-") as temporary:
        path = Path(temporary) / "model.pt"
        metadata = {
            "family": "echo_mapper",
            "hidden_size": 16,
            "layers": 1,
            "seed": 53,
        }
        CORE.save_checkpoint(path, model, metadata)
        loaded, observed = CORE.load_checkpoint(path)
        require(observed == metadata, "checkpoint metadata replay changed")
        for expected, actual in zip(model.state_dict().values(), loaded.state_dict().values()):
            require(torch.equal(expected, actual), "checkpoint weights did not replay exactly")


def improvement_only_arbitration_checks() -> None:
    timeline = np.arange(CORE.CLIP_SAMPLES, dtype=np.float32) / CORE.SAMPLE_RATE
    local = (0.05 * np.sin(2.0 * np.pi * 211.0 * timeline)).astype(np.float32)
    remote = (
        0.04 * np.sin(2.0 * np.pi * 437.0 * timeline)
        + 0.02 * np.sin(2.0 * np.pi * 881.0 * timeline)
    ).astype(np.float32)
    baseline = local + remote

    selected, decision = ARBITER.arbitrate(
        baseline=baseline,
        candidate=local,
        remote=remote,
    )
    require(decision["selected"] == "candidate", "lower-coherence candidate was rejected")
    require(np.array_equal(selected, local), "accepted candidate changed")

    selected, decision = ARBITER.arbitrate(
        baseline=baseline,
        candidate=baseline + remote,
        remote=remote,
    )
    require(decision["selected"] == "baseline", "higher-coherence candidate was accepted")
    require(np.array_equal(selected, baseline), "rejection did not return exact baseline")

    malformed = np.full_like(baseline, np.nan)
    selected, decision = ARBITER.arbitrate(
        baseline=baseline,
        candidate=malformed,
        remote=remote,
    )
    require(decision["fail_open"], "non-finite candidate did not fail open")
    require(np.array_equal(selected, baseline), "non-finite candidate changed baseline")


def hypothesis_bank_checks() -> None:
    timeline = np.arange(CORE.CLIP_SAMPLES, dtype=np.float32) / CORE.SAMPLE_RATE
    local = (0.05 * np.sin(2.0 * np.pi * 223.0 * timeline)).astype(np.float32)
    remote = (
        0.04 * np.sin(2.0 * np.pi * 467.0 * timeline)
        + 0.015 * np.sin(2.0 * np.pi * 929.0 * timeline)
    ).astype(np.float32)
    echo_estimate = 0.5 * remote
    baseline = local + remote
    selected, decision = BANK.select(
        baseline=baseline,
        echo_estimate=echo_estimate,
        remote=remote,
        neural_candidate=baseline + remote,
    )
    require(
        decision["selected"] == "additional_fir_1.75",
        f"physical bank selected {decision['selected']}",
    )
    require(
        ARBITER.remote_coherence(selected, remote)
        < ARBITER.remote_coherence(baseline, remote),
        "physical bank did not reduce remote coherence",
    )

    selected, decision = BANK.select(
        baseline=local,
        echo_estimate=np.zeros_like(local),
        remote=np.zeros_like(local),
        neural_candidate=local + 0.01,
    )
    require(decision["selected"] == "baseline", "remote-inactive input was modified")
    require(np.array_equal(selected, local), "remote-inactive fallback is not exact")

    selected, decision = BANK.select(
        baseline=local,
        echo_estimate=np.zeros(local.size - 1, dtype=np.float32),
        remote=np.zeros_like(local),
    )
    require(decision["fail_open"], "malformed echo estimate did not fail open")
    require(np.array_equal(selected, local), "malformed input changed baseline")


def v27_segmentation_stability_check() -> None:
    remote = [{"start": 1.0, "end": 3.0, "text": "один два три", "tokens": ["один", "два", "три"]}]
    unsplit = [{"start": 1.0, "end": 3.0, "text": "один два три", "tokens": ["один", "два", "три"]}]
    split = [
        {"start": 1.0, "end": 1.8, "text": "один", "tokens": ["один"]},
        {"start": 1.8, "end": 3.0, "text": "два три", "tokens": ["два", "три"]},
    ]
    states = [{"start": 1.0, "end": 3.0, "state": "remote_only"}]
    left = V27.remote_supported_burden(unsplit, remote, states)
    right = V27.remote_supported_burden(split, remote, states)
    require(
        left["matched_tokens"] == right["matched_tokens"] == 3,
        "remote-supported burden depends on mic segment boundaries",
    )
    require(
        left["seconds"] == right["seconds"] == 2.0,
        "remote-supported seconds depend on mic segment boundaries",
    )


def v27_exact_audio_and_rollback_checks() -> None:
    baseline = np.arange(-500, 500, dtype=np.int16)
    selected = [
        {
            "start": 0.02,
            "end": 0.03,
            "attenuation_db": -6.0,
        }
    ]
    candidate, evidence = V27.materialize_pcm16(baseline, selected, fade_sec=0.001)
    changed = candidate != baseline
    require(evidence["outside_selected_changed_samples"] == 0, "v2.7 changed unselected audio")
    require(np.any(changed), "v2.7 synthetic attenuation changed no samples")
    require(np.array_equal(candidate[:320], baseline[:320]), "v2.7 changed prefix audio")
    require(np.array_equal(candidate[480:], baseline[480:]), "v2.7 changed suffix audio")

    with tempfile.TemporaryDirectory(prefix="murmurmark-spne-v27-rollback-") as temporary:
        session = Path(temporary)
        raw = session / "derived/transcript-simple/whisper-cpp/raw"
        raw.mkdir(parents=True)
        (raw / "mic.json").write_text(
            json.dumps(
                {
                    "transcription": [
                        {"text": " локальная фраза", "offsets": {"from": 10000, "to": 12000}}
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        chunks = V27.reviewed_regression_chunks(
            session=session,
            regressions=[{"start": 10.0, "end": 12.0}],
            decisions=[{"chunk_index": 7, "hard_start_sec": 0.0, "hard_end_sec": 60.0}],
            selected=[{"diagnostic_chunk": 7}],
        )
        require(chunks == [7], "reviewed-Me regression did not roll back its ASR chunk")


def v27_me_guard_source_checks() -> None:
    with tempfile.TemporaryDirectory(prefix="murmurmark-spne-v27-guard-") as temporary:
        session = Path(temporary)
        resolved = session / "derived/transcript-simple/whisper-cpp/resolved"
        resolved.mkdir(parents=True)
        shadow = resolved / "clean_dialogue.shadow_v2.json"
        shadow.write_text("{}\n", encoding="utf-8")
        require(
            V27.me_guard_dialogue_path(session) == shadow,
            "v2.7 cannot use the baseline shadow dialogue as a fail-safe Me guard",
        )
        agent = resolved / "clean_dialogue.agent_reviewed_v1.json"
        agent.write_text("{}\n", encoding="utf-8")
        require(
            V27.me_guard_dialogue_path(session) == agent,
            "v2.7 did not prefer the agent-reviewed Me guard",
        )
        reviewed = resolved / "clean_dialogue.reviewed_v1.json"
        reviewed.write_text("{}\n", encoding="utf-8")
        require(
            V27.me_guard_dialogue_path(session) == reviewed,
            "v2.7 did not prefer the human-reviewed Me guard",
        )


def v28_fail_open_selector_checks() -> None:
    require(
        all(V28.full_shadow_checks({"passed": True, "gates": {"safe": True}}).values()),
        "v2.8 rejected a passing full shadow",
    )
    require(
        not all(
            V28.full_shadow_checks(
                {"passed": False, "gates": {"local_recall_preserved": False}}
            ).values()
        ),
        "v2.8 accepted a regressing full shadow",
    )
    with tempfile.TemporaryDirectory(prefix="murmurmark-spne-v28-fallback-") as temporary:
        session = Path(temporary)
        baseline = session / "derived/asr/mic.wav"
        baseline.parent.mkdir(parents=True)
        baseline.write_bytes(b"exact-local-fir-baseline")
        output = session / "derived/preprocess/speaker-preserving-neural-echo-v2-8"
        report = V28.fail_open(
            session=session,
            output=output,
            baseline=baseline,
            reason="synthetic_failure",
            details={},
            basis={},
        )
        selected = output / "selected_clean_mic_pcm16.wav"
        require(report["exact_fallback"], "v2.8 did not report exact fallback")
        require(
            selected.read_bytes() == baseline.read_bytes(),
            "v2.8 fallback differs from the local-FIR baseline",
        )


def v29_profile_matched_verdict_checks() -> None:
    V29.verify_policy(ROOT / "policies/speaker-preserving-neural-echo-v2-9.json")
    with tempfile.TemporaryDirectory(prefix="murmurmark-spne-v29-verdict-") as temporary:
        session = Path(temporary)
        resolved = session / "derived/transcript-simple/whisper-cpp/resolved"
        resolved.mkdir(parents=True)
        (resolved / "clean_dialogue.shadow_v2.json").write_text(
            json.dumps(
                {
                    "schema": "murmurmark.clean_dialogue/v1",
                    "utterances": [
                        {
                            "id": "utt_000001",
                            "role": "Me",
                            "start": 1.0,
                            "end": 2.0,
                            "text": "проверка",
                            "quality": {"needs_review": False},
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (resolved / "quality_report.shadow_v2.json").write_text(
            json.dumps(
                {
                    "utterances": 1,
                    "needs_review_count": 0,
                    "cross_role_overlap_gt2_count": 0,
                    "cross_role_overlap_gt2_seconds": 0.0,
                    "remote_duplicate_in_me_seconds": 0.0,
                    "unrepaired_long_mic_crossings_count": 0,
                    "golden_phrase_fail_count": 0,
                    "local_only_island_recall": 1.0,
                }
            ),
            encoding="utf-8",
        )
        (resolved / "overlaps.shadow_v2.json").write_text(
            json.dumps({"schema": "murmurmark.overlaps/v1", "overlaps": []}),
            encoding="utf-8",
        )
        (resolved / "repair_comparison.json").write_text(
            json.dumps({"passed": True}), encoding="utf-8"
        )
        ordinary = session / "derived/synthesis-simple/extractive/quality_verdict.json"
        ordinary.parent.mkdir(parents=True)
        ordinary.write_text(
            json.dumps(
                {
                    "verdict": "usable_with_review",
                    "selected_transcript_profile": "reviewed_v1",
                }
            ),
            encoding="utf-8",
        )
        destination = session / "derived/preprocess/v29/baseline_shadow_v2_verdict.json"
        destination.parent.mkdir(parents=True)
        payload = V29_SHADOW.baseline_shadow_verdict(session, destination)
        require(
            payload["selected_transcript_profile"] == "shadow_v2",
            "v2.9 compared against a non-shadow baseline profile",
        )
        require(payload["verdict"] == "good", "v2.9 shadow verdict fixture regressed")
        require(
            payload["selected_transcript_profile"]
            != json.loads(ordinary.read_text())["selected_transcript_profile"],
            "v2.9 reused the globally selected reviewed profile",
        )


def production_publication_rollback_checks() -> None:
    require(
        PRODUCTION.baseline_name("transcript.shadow_v2.md")
        == "transcript.local_fir_role_masked.md",
        "production baseline profile naming changed",
    )
    with tempfile.TemporaryDirectory(prefix="murmurmark-spne-v2-publish-") as temporary:
        resolved = Path(temporary) / "resolved"
        resolved.mkdir(parents=True)
        expected = {}
        for index, name in enumerate((*PRODUCTION.SHADOW_FILES, "repair_comparison.json")):
            value = f"baseline-{index}\n".encode()
            (resolved / name).write_bytes(value)
            expected[name] = value
        PRODUCTION.snapshot_baseline(resolved)
        for name in expected:
            (resolved / name).write_text("candidate\n", encoding="utf-8")
        PRODUCTION.snapshot_baseline(resolved)
        PRODUCTION.restore_baseline(resolved)
        require(
            all((resolved / name).read_bytes() == value for name, value in expected.items()),
            "production rollback did not restore every baseline shadow artifact",
        )
    with tempfile.TemporaryDirectory(prefix="murmurmark-spne-v2-audio-publish-") as temporary:
        session = Path(temporary) / "session"
        output = session / "derived/preprocess/speaker-preserving-neural-echo-v2"
        expected_audio = {}
        for index, relative_path in enumerate(PRODUCTION.CANONICAL_AUDIO_FILES):
            path = session / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            value = f"baseline-audio-{index}\n".encode()
            path.write_bytes(value)
            expected_audio[relative_path] = value
        require(
            PRODUCTION.exact_fallback_is_active(session, output),
            "fresh production baseline was not recognized as the exact fallback",
        )
        snapshot = PRODUCTION.snapshot_audio_baseline(session, output)
        require(
            set(snapshot) == set(PRODUCTION.CANONICAL_AUDIO_FILES),
            "production audio snapshot is incomplete",
        )
        for relative_path in expected_audio:
            (session / relative_path).write_bytes(b"candidate\n")
        PRODUCTION.snapshot_audio_baseline(session, output)
        PRODUCTION.restore_audio_baseline(session, output)
        require(
            all(
                (session / relative_path).read_bytes() == value
                for relative_path, value in expected_audio.items()
            ),
            "production rollback did not restore every canonical audio artifact",
        )
        resolved = session / "derived/transcript-simple/whisper-cpp/resolved"
        resolved.mkdir(parents=True)
        expected_resolved = {}
        for index, name in enumerate((*PRODUCTION.SHADOW_FILES, "repair_comparison.json")):
            value = f"baseline-resolved-{index}\n".encode()
            (resolved / name).write_bytes(value)
            expected_resolved[name] = value
        PRODUCTION.snapshot_baseline(resolved)
        PRODUCTION.snapshot_audio_baseline(session, output)
        PRODUCTION.write_json(
            output / PRODUCTION.TRANSACTION_NAME,
            {
                "schema": PRODUCTION.TRANSACTION_SCHEMA,
                "state": "publishing",
            },
        )
        for name in expected_resolved:
            (resolved / name).write_bytes(b"partial-candidate\n")
        for relative_path in expected_audio:
            (session / relative_path).write_bytes(b"partial-candidate\n")
        recovered = PRODUCTION.recover_incomplete_publication(session, output)
        require(
            recovered is not None and recovered.get("state") == "recovered_on_next_run",
            "production did not recover an interrupted publication",
        )
        require(
            all(
                (resolved / name).read_bytes() == value
                for name, value in expected_resolved.items()
            )
            and all(
                (session / relative_path).read_bytes() == value
                for relative_path, value in expected_audio.items()
            ),
            "interrupted publication recovery was not exact",
        )
        PRODUCTION.snapshot_baseline(resolved)
        PRODUCTION.snapshot_audio_baseline(session, output)
        published = {}
        for name in expected_resolved:
            destination = resolved / name
            destination.write_bytes(b"committed-candidate\n")
            published[name] = PRODUCTION.fingerprint(destination, session)
        for relative_path in expected_audio:
            destination = session / relative_path
            destination.write_bytes(b"committed-candidate\n")
            published[relative_path] = PRODUCTION.fingerprint(destination, session)
        PRODUCTION.write_json(
            output / PRODUCTION.TRANSACTION_NAME,
            {
                "schema": PRODUCTION.TRANSACTION_SCHEMA,
                "state": "committed",
                "published": published,
            },
        )
        require(
            not PRODUCTION.exact_fallback_is_active(session, output),
            "committed candidate was mislabeled as the exact fallback",
        )
        require(
            PRODUCTION.restore_committed_publication(
                session, output, "fixture_policy_incompatible"
            ),
            "active committed candidate was not recognized",
        )
        require(
            all(
                (resolved / name).read_bytes() == value
                for name, value in expected_resolved.items()
            )
            and all(
                (session / relative_path).read_bytes() == value
                for relative_path, value in expected_audio.items()
            ),
            "committed candidate did not fail open to the exact baseline",
        )
        require(
            PRODUCTION.exact_fallback_is_active(session, output),
            "restored production baseline was not recognized as exact",
        )
        fresh_audio = {}
        for index, relative_path in enumerate(PRODUCTION.CANONICAL_AUDIO_FILES):
            value = f"fresh-local-fir-{index}\n".encode()
            (session / relative_path).write_bytes(value)
            fresh_audio[relative_path] = value
        for index, name in enumerate((*PRODUCTION.SHADOW_FILES, "repair_comparison.json")):
            (resolved / name).write_bytes(f"stale-candidate-{index}\n".encode())
        prepared = PRODUCTION.prepare_primary_asr_baseline(
            session, output, fresh_preprocess=True
        )
        require(
            prepared["action"] == "snapshotted_fresh_local_fir",
            "fresh Echo Guard output was not established as the primary-ASR baseline",
        )
        require(
            all(
                (
                    output
                    / "baseline-local-fir-role-masked"
                    / relative_path
                ).read_bytes()
                == value
                for relative_path, value in fresh_audio.items()
            ),
            "fresh local-FIR audio did not replace the stale fallback snapshot",
        )
        require(
            all(
                not (resolved / PRODUCTION.baseline_name(name)).exists()
                for name in (*PRODUCTION.SHADOW_FILES, "repair_comparison.json")
            ),
            "fresh preprocessing retained stale resolved baseline snapshots",
        )
        fresh_resolved = {}
        for index, name in enumerate((*PRODUCTION.SHADOW_FILES, "repair_comparison.json")):
            value = f"fresh-resolved-{index}\n".encode()
            (resolved / name).write_bytes(value)
            fresh_resolved[name] = value
        PRODUCTION.snapshot_baseline(resolved)
        require(
            all(
                (resolved / PRODUCTION.baseline_name(name)).read_bytes() == value
                for name, value in fresh_resolved.items()
            ),
            "post-ASR snapshots did not use the fresh local-FIR transcript",
        )


def v210_outcome_gate_profile_checks() -> None:
    with tempfile.TemporaryDirectory(prefix="murmurmark-spne-v210-gates-") as temporary:
        session = Path(temporary) / "fixture-session"
        resolved = session / "resolved"
        resolved.mkdir(parents=True)
        current_transcript = resolved / "current.md"
        shadow_transcript = resolved / "shadow.md"
        current_transcript.write_text("baseline\n", encoding="utf-8")
        shadow_transcript.write_text("candidate\n", encoding="utf-8")
        current = {
            "quality": {
                "unrepaired_long_mic_crossings_count": 0,
                "micro_reasr_success_count": 10,
                "local_only_island_recall": 1.0,
                "needs_review_count": 1,
                "cross_role_overlap_gt2_seconds": 4.0,
                "remote_duplicate_in_me_seconds": 5.0,
                "golden_phrase_fail_count": 0,
            },
            "paths": {"transcript": str(current_transcript)},
        }
        shadow = {
            "quality": {
                **current["quality"],
                "micro_reasr_success_count": 9,
            },
            "paths": {"transcript": str(shadow_transcript)},
        }
        TRANSCRIBE.write_repair_comparison(
            session=session,
            resolved_dir=resolved,
            current_output=current,
            shadow_output=shadow,
        )
        ordinary = json.loads((resolved / "repair_comparison.json").read_text())
        require(not ordinary["passed"], "default comparison ignored micro-ASR regression")
        require("gate_profile" not in ordinary, "default comparison output shape changed")
        TRANSCRIBE.write_repair_comparison(
            session=session,
            resolved_dir=resolved,
            current_output=current,
            shadow_output=shadow,
            gate_profile="speaker_preserving_echo_v2",
        )
        outcome = json.loads((resolved / "repair_comparison.json").read_text())
        require(outcome["passed"], "outcome gate profile retained a process-only failure")
        require(
            "remote_duplicate_in_me_seconds" in outcome["no_regression_gates"],
            "outcome gate profile omitted remote-like Me preservation",
        )


def v210_chronology_rollback_checks() -> None:
    with tempfile.TemporaryDirectory(prefix="murmurmark-spne-v210-rollback-") as temporary:
        session = Path(temporary)
        baseline = (
            session
            / "derived/transcript-simple/whisper-cpp/resolved/overlaps.shadow_v2.json"
        )
        baseline.parent.mkdir(parents=True)
        baseline.write_text(
            json.dumps(
                {"overlaps": [{"start": 10.0, "end": 13.0, "duration_sec": 3.0}]}
            ),
            encoding="utf-8",
        )
        stage = session / "candidate-stage"
        candidate = (
            stage
            / "derived/transcript-simple/whisper-cpp/resolved/overlaps.shadow_v2.json"
        )
        candidate.parent.mkdir(parents=True)
        candidate.write_text(
            json.dumps(
                {"overlaps": [{"start": 70.0, "end": 73.0, "duration_sec": 3.0}]}
            ),
            encoding="utf-8",
        )
        output = V210.output_root(session)
        output.mkdir(parents=True)
        (output / "selected_windows.jsonl").write_text(
            json.dumps({"diagnostic_chunk": 2}) + "\n", encoding="utf-8"
        )
        (output / "diagnostic_chunk_decisions.jsonl").write_text(
            json.dumps(
                {
                    "chunk_index": 2,
                    "hard_start_sec": 60.0,
                    "hard_end_sec": 120.0,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        chunks, evidence = V210.chronology_rollback_chunks(
            session,
            {},
            {
                "stage": str(stage),
                "gates": {
                    "chronology_not_worse": False,
                    "verdict_not_worse": False,
                },
            },
        )
        require(chunks == [2], "v2.10 did not localize candidate-only overlap")
        require(evidence[0]["duration_sec"] == 3.0, "v2.10 rollback evidence changed")


def v211_local_island_rollback_checks() -> None:
    with tempfile.TemporaryDirectory(prefix="murmurmark-spne-v211-rollback-") as temporary:
        session = Path(temporary)
        baseline = (
            session
            / "derived/transcript-simple/whisper-cpp/resolved/"
            "timeline_repair_examples.shadow_v2.jsonl"
        )
        baseline.parent.mkdir(parents=True)
        baseline.write_text(
            json.dumps(
                {
                    "parent_candidate_id": "cand_mic_1",
                    "children": [
                        {
                            "start_ms": 70000,
                            "end_ms": 73000,
                            "text": "сохранить локальную фразу",
                        }
                    ],
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        stage = session / "candidate-stage"
        candidate = (
            stage
            / "derived/transcript-simple/whisper-cpp/resolved/"
            "timeline_repair_examples.shadow_v2.jsonl"
        )
        candidate.parent.mkdir(parents=True)
        candidate.write_text(
            json.dumps({"parent_candidate_id": "cand_mic_2", "children": []})
            + "\n",
            encoding="utf-8",
        )
        output = V211.output_root(session)
        output.mkdir(parents=True)
        (output / "selected_windows.jsonl").write_text(
            json.dumps({"diagnostic_chunk": 2}) + "\n", encoding="utf-8"
        )
        (output / "diagnostic_chunk_decisions.jsonl").write_text(
            json.dumps(
                {
                    "chunk_index": 2,
                    "hard_start_sec": 60.0,
                    "hard_end_sec": 120.0,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        chunks, evidence = V211.outcome_rollback_chunks(
            session,
            {},
            {
                "stage": str(stage),
                "gates": {
                    "local_recall_preserved": False,
                    "verdict_not_worse": False,
                },
            },
        )
        require(chunks == [2], "v2.11 did not localize the lost local island")
        require(
            evidence[0]["type"] == "lost_recovered_local_island",
            "v2.11 local-island rollback provenance changed",
        )
        blocked, _ = V211.outcome_rollback_chunks(
            session,
            {},
            {
                "stage": str(stage),
                "gates": {
                    "local_recall_preserved": False,
                    "remote_content_unchanged": False,
                },
            },
        )
        require(not blocked, "v2.11 masked an unrelated outcome regression")


def v213_iterative_outcome_mapping_checks() -> None:
    with tempfile.TemporaryDirectory(prefix="murmurmark-spne-v213-rollback-") as temporary:
        session = Path(temporary)
        resolved = session / "derived/transcript-simple/whisper-cpp/resolved"
        resolved.mkdir(parents=True)
        (resolved / "overlaps.shadow_v2.json").write_text(
            json.dumps(
                {"overlaps": [{"start": 10.0, "end": 13.0, "duration_sec": 3.0}]}
            ),
            encoding="utf-8",
        )
        (resolved / "clean_dialogue.shadow_v2.json").write_text(
            json.dumps(
                {
                    "utterances": [
                        {
                            "id": "baseline_review",
                            "role": "me",
                            "start": 20.0,
                            "end": 21.0,
                            "quality": {"needs_review": True},
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        stage = session / "candidate-stage"
        candidate_resolved = stage / "derived/transcript-simple/whisper-cpp/resolved"
        candidate_resolved.mkdir(parents=True)
        (candidate_resolved / "overlaps.shadow_v2.json").write_text(
            json.dumps(
                {
                    "overlaps": [
                        {"start": 9.98, "end": 13.04, "duration_sec": 3.06}
                    ]
                }
            ),
            encoding="utf-8",
        )
        (candidate_resolved / "clean_dialogue.shadow_v2.json").write_text(
            json.dumps(
                {
                    "utterances": [
                        {
                            "id": "baseline_review_copy",
                            "role": "me",
                            "start": 20.0,
                            "end": 21.0,
                            "quality": {"needs_review": True},
                        },
                        {
                            "id": "candidate_only_review",
                            "role": "me",
                            "start": 66.0,
                            "end": 67.0,
                            "text": "как",
                            "quality": {"needs_review": True},
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
        output = V213.output_root(session)
        output.mkdir(parents=True)
        (output / "selected_windows.jsonl").write_text(
            "".join(
                json.dumps({"diagnostic_chunk": index}) + "\n"
                for index in (1, 2)
            ),
            encoding="utf-8",
        )
        (output / "diagnostic_chunk_decisions.jsonl").write_text(
            "".join(
                json.dumps(row) + "\n"
                for row in (
                    {"chunk_index": 1, "hard_start_sec": 0.0, "hard_end_sec": 60.0},
                    {"chunk_index": 2, "hard_start_sec": 70.0, "hard_end_sec": 120.0},
                )
            ),
            encoding="utf-8",
        )
        chunks, evidence = V213.outcome_rollback_chunks(
            session,
            {},
            {
                "stage": str(stage),
                "gates": {
                    "chronology_not_worse": False,
                    "needs_review_not_worse": False,
                    "verdict_not_worse": False,
                },
            },
        )
        require(chunks == [1, 2], "v2.13 did not map every outcome regression")
        kinds = {row["type"] for row in evidence}
        require(
            kinds
            == {
                "candidate_extended_cross_role_overlap",
                "candidate_only_needs_review_utterance",
            },
            "v2.13 rollback evidence classes changed",
        )
        review = next(
            row
            for row in evidence
            if row["type"] == "candidate_only_needs_review_utterance"
        )
        require(
            review["diagnostic_chunks"] == [2],
            "v2.13 did not apply the frozen 5 second ASR context",
        )


def v214_local_island_and_window_rollback_checks() -> None:
    with tempfile.TemporaryDirectory(prefix="murmurmark-spne-v214-rollback-") as temporary:
        session = Path(temporary)
        baseline_resolved = session / "derived/transcript-simple/whisper-cpp/resolved"
        candidate_stage = session / "candidate-stage"
        candidate_resolved = (
            candidate_stage / "derived/transcript-simple/whisper-cpp/resolved"
        )
        baseline_resolved.mkdir(parents=True)
        candidate_resolved.mkdir(parents=True)
        name = "timeline_repair_examples.shadow_v2.jsonl"
        baseline_parent = {
            "parent_candidate_id": "baseline",
            "local_islands": [[10000, 11000]],
            "children": [
                {
                    "start_ms": 10000,
                    "end_ms": 11000,
                    "text": "сохранённая локальная фраза",
                }
            ],
        }
        candidate_parent = {
            "parent_candidate_id": "candidate",
            "local_islands": [[10000, 11000], [20000, 21000]],
            "children": [
                {
                    "start_ms": 10000,
                    "end_ms": 11000,
                    "text": "сохранённая локальная фраза",
                }
            ],
        }
        (baseline_resolved / name).write_text(
            json.dumps(baseline_parent) + "\n", encoding="utf-8"
        )
        (candidate_resolved / name).write_text(
            json.dumps(candidate_parent) + "\n", encoding="utf-8"
        )
        for resolved in (baseline_resolved, candidate_resolved):
            (resolved / "overlaps.shadow_v2.json").write_text(
                '{"overlaps": []}\n', encoding="utf-8"
            )
            (resolved / "clean_dialogue.shadow_v2.json").write_text(
                '{"utterances": []}\n', encoding="utf-8"
            )

        output = V214.output_root(session)
        output.mkdir(parents=True)
        windows = [
            {
                "proposal_id": "far_same_chunk",
                "diagnostic_chunk": 1,
                "start": 0.0,
                "end": 1.0,
            },
            {
                "proposal_id": "near_before",
                "diagnostic_chunk": 1,
                "start": 16.5,
                "end": 17.5,
            },
            {
                "proposal_id": "near_after",
                "diagnostic_chunk": 1,
                "start": 23.5,
                "end": 24.5,
            },
        ]
        (output / "selected_windows.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in windows), encoding="utf-8"
        )
        rollback, evidence = V214.outcome_rollback_windows(
            session,
            {},
            {
                "stage": str(candidate_stage),
                "gates": {
                    "chronology_not_worse": True,
                    "local_recall_preserved": False,
                    "needs_review_not_worse": True,
                    "verdict_not_worse": True,
                },
            },
        )
        require(
            rollback == ["near_after", "near_before"],
            "v2.14 rolled back a whole ASR chunk instead of exact windows",
        )
        require(
            len(evidence) == 1
            and evidence[0]["type"] == "candidate_unrecovered_local_island",
            "v2.14 did not detect a candidate-only unrecovered local island",
        )
        require(
            evidence[0]["diagnostic_chunks"] == [1],
            "v2.14 rollback lost diagnostic provenance",
        )


def v214_identity_bounded_asr_checks() -> None:
    selected = [{"start": 10.0, "end": 10.5}]
    intervals = V214_AUDIO.build_influence_intervals(selected)
    require(intervals == [(7.0, 13.5)], "v2.14 ASR influence interval changed")

    def row(start: float, end: float, text: str) -> dict[str, Any]:
        return {
            "offsets": {"from": int(start * 1000), "to": int(end * 1000)},
            "text": text,
        }

    merged = V214_AUDIO.splice_identity_bounded_rows(
        baseline_rows=[
            row(8.0, 9.0, "baseline inside"),
            row(20.0, 21.0, "baseline outside"),
        ],
        candidate_rows=[
            row(9.0, 10.0, "candidate inside"),
            row(50.0, 51.0, "candidate outside"),
        ],
        influence_intervals=intervals,
    )
    texts = [item["text"] for item in merged]
    require(
        texts == ["candidate inside", "baseline outside"],
        "v2.14 ASR splice changed text outside the modified audio window",
    )
    filtered = V214_AUDIO.filter_selected_windows(
        [
            {"proposal_id": "keep", "start": 0.0, "end": 0.5},
            {"proposal_id": "drop", "start": 0.5, "end": 1.0},
        ],
        {"drop"},
    )
    require(
        [item["proposal_id"] for item in filtered] == ["keep"],
        "v2.14 excluded proposal did not change the reused audio selection",
    )


def v215_applicability_gate_checks() -> None:
    require(
        V215.applicability_classification({"status": "candidate"})
        == "applicable_candidate",
        "v2.15 candidate applicability changed",
    )
    require(
        V215.applicability_classification(
            {
                "status": "fallback",
                "source_runtime": {"reason": "no_asr_audited_improvement"},
            }
        )
        == "not_applicable_exact_fallback",
        "v2.15 no-benefit fallback is not explicitly classified",
    )
    require(
        V215.applicability_classification(
            {
                "status": "fallback",
                "source_runtime": {"reason": "final_development_gates_failed"},
            }
        )
        == "safety_exact_fallback",
        "v2.15 safety fallback was mislabeled as non-applicable",
    )
    rows = [
        {
            "expected_mode": "speaker_playback",
            "status": "fallback",
            "applicability": "not_applicable_exact_fallback",
            "passed": True,
            "exact_fallback": True,
            "remote_supported_reduction_sec": 0.0,
            "remote_supported_token_reduction": 0,
            "local_retention_ratio": None,
            "selector_runtime_factor": 0.1,
        },
        {
            "expected_mode": "speaker_playback",
            "status": "fallback",
            "applicability": "not_applicable_exact_fallback",
            "passed": True,
            "exact_fallback": True,
            "remote_supported_reduction_sec": 0.0,
            "remote_supported_token_reduction": 0,
            "local_retention_ratio": None,
            "selector_runtime_factor": 0.1,
        },
        {
            "expected_mode": "headphones_or_low_leak",
            "status": "fallback",
            "applicability": "not_applicable_exact_fallback",
            "passed": True,
            "exact_fallback": True,
            "remote_supported_reduction_sec": 0.0,
            "remote_supported_token_reduction": 0,
            "local_retention_ratio": None,
            "selector_runtime_factor": 0.1,
        },
    ]
    hard_checks, _ = V215_EVALUATION.aggregate_checks(
        rows,
        {
            "speaker_sessions_classified_min": 2,
            "headphones_exact_fallback_required": True,
            "selector_runtime_factor_max": 1.5,
            "post_asr_cleanup_promotion_credit": 0,
        },
    )
    require(
        all(hard_checks.values()),
        "v2.15 hard safety gate incorrectly requires utility on every hard session",
    )
    corpus_checks, _ = V215_EVALUATION.aggregate_checks(
        rows,
        {
            "speaker_candidate_sessions_min": 2,
            "remote_reduction_sec_min": 5.0,
            "remote_token_reduction_min": 6,
            "headphones_exact_fallback_required": True,
            "selector_runtime_factor_max": 1.5,
            "post_asr_cleanup_promotion_credit": 0,
        },
    )
    require(
        not all(corpus_checks.values()),
        "v2.15 applicability fallback received corpus utility credit",
    )


def v216_safety_fallback_credit_checks() -> None:
    rows = [
        {
            "expected_mode": "speaker_playback",
            "status": "fallback",
            "applicability": "safety_exact_fallback",
            "passed": True,
            "exact_fallback": True,
            "remote_supported_reduction_sec": 0.0,
            "remote_supported_token_reduction": 0,
            "local_retention_ratio": None,
            "selector_runtime_factor": 0.1,
        }
    ]
    hard_checks, hard_aggregate = V216_EVALUATION.aggregate_checks(
        rows,
        {
            "speaker_terminal_dispositions_min": 1,
            "selector_runtime_factor_max": 1.5,
            "post_asr_cleanup_promotion_credit": 0,
        },
    )
    require(
        all(hard_checks.values()),
        "v2.16 rejected an exact safety fallback at the hard safety gate",
    )
    require(
        hard_aggregate["safety_fallback_speaker_sessions"] == 1,
        "v2.16 lost the safety-fallback disposition",
    )
    corpus_checks, corpus_aggregate = V216_EVALUATION.aggregate_checks(
        rows,
        {
            "speaker_candidate_sessions_min": 1,
            "remote_reduction_sec_min": 0.1,
            "remote_token_reduction_min": 1,
            "selector_runtime_factor_max": 1.5,
            "post_asr_cleanup_promotion_credit": 0,
        },
    )
    require(
        not all(corpus_checks.values())
        and corpus_aggregate["candidate_sessions"] == 0,
        "v2.16 safety fallback received corpus utility credit",
    )


def v216_cache_seed_checks() -> None:
    with tempfile.TemporaryDirectory(prefix="murmurmark-spne-v216-cache-") as temporary:
        session = Path(temporary) / "fixture-session"
        source_cache = (
            session
            / "derived/preprocess/speaker-preserving-neural-echo-v2-11/diagnostic-asr-cache"
        )
        model_sha = "a" * 64
        pcm = (np.arange(1600, dtype=np.int16) - 800).tobytes()
        basis = {
            "clip_sha256": CACHE_SEED.hashlib.sha256(pcm).hexdigest(),
            "model_sha256": model_sha,
            "language": "ru",
            "max_context": 0,
            "threads": 6,
        }
        key = CACHE_SEED.stable_digest(basis)
        entry = source_cache / key
        entry.mkdir(parents=True)
        with wave.open(str(entry / "clip.wav"), "wb") as destination:
            destination.setnchannels(1)
            destination.setsampwidth(2)
            destination.setframerate(16000)
            destination.writeframes(pcm)
        (entry / "cache.json").write_text(
            json.dumps(
                {
                    "schema": "murmurmark.spne_v24_chunk_asr_cache/v1",
                    "basis": basis,
                }
            ),
            encoding="utf-8",
        )
        (entry / "result.json").write_text(
            '{"transcription": []}\n', encoding="utf-8"
        )
        invalid = source_cache / "invalid"
        invalid.mkdir()
        (invalid / "cache.json").write_text("{}\n", encoding="utf-8")
        first = CACHE_SEED.seed_session(session, model_sha256=model_sha)
        second = CACHE_SEED.seed_session(session, model_sha256=model_sha)
        require(first == second, "v2.16 cache seed report is not idempotent")
        require(
            first["validated_unique_entry_count"] == 1
            and first["materialized_entry_count"] == 1
            and first["rejected_entry_count"] == 1,
            "v2.16 cache seed did not separate valid and invalid entries",
        )
        target = (
            session
            / "derived/preprocess/speaker-preserving-neural-echo-v2-15/diagnostic-asr-cache"
            / key
        )
        require(
            (target / "cache.json").is_file()
            and (target / "result.json").is_file()
            and not (target / "clip.wav").exists(),
            "v2.16 cache seed copied audio or omitted required JSON",
        )


def micro_reasr_content_cache_checks() -> None:
    with tempfile.TemporaryDirectory(prefix="murmurmark-micro-cache-") as temporary:
        root = Path(temporary)
        suffix = "_0000001000_0000002000_0000001000_0000002000_sil0400"
        old = root / f"micro_shadow_v2_normal_role_masked_for_asr_srcold{suffix}"
        new = root / f"micro_shadow_v2_normal_role_masked_for_asr_srcnew{suffix}"
        different = root / f"micro_shadow_v2_normal_role_masked_for_asr_srcdifferent{suffix}"
        old.with_suffix(".wav").write_bytes(b"RIFF-identical-prepared-pcm")
        new.with_suffix(".wav").write_bytes(b"RIFF-identical-prepared-pcm")
        different.with_suffix(".wav").write_bytes(b"RIFF-different-prepared-pcm")
        old.with_suffix(".json").write_text('{"transcription": []}\n', encoding="utf-8")
        different.with_suffix(".json").write_text(
            '{"transcription": [{"text": "wrong"}]}\n', encoding="utf-8"
        )
        execution = TRANSCRIBE.reuse_identical_micro_reasr(
            micro_dir=root,
            slice_wav=new.with_suffix(".wav"),
            output_base=new,
            cache_glob=f"micro_shadow_v2_normal_role_masked_for_asr_src*{suffix}.json",
        )
        require(
            execution is not None and execution.get("mode") == "slice_content_cache",
            "byte-identical micro-ASR result was not reused",
        )
        require(
            json.loads(new.with_suffix(".json").read_text())["transcription"] == [],
            "micro-ASR cache reused a non-identical prepared slice",
        )


def production_pipeline_order_checks() -> None:
    source = (ROOT / "scripts/run-session-pipeline.py").read_text(encoding="utf-8")
    step_names = re.findall(r'\bstep\(\s*"([^"]+)"', source)
    required = (
        "echo_suppression_policy",
        "export_asr_audio",
        "speaker_preserving_neural_echo_v2_prepare",
        "transcribe_current",
        "speaker_preserving_neural_echo_v2",
    )
    positions = {name: step_names.index(name) for name in required}
    require(
        list(positions.values()) == sorted(positions.values()),
        "production pipeline does not preserve export -> baseline -> primary ASR -> selection order",
    )
    prepare_start = source.index(
        'step(\n            "speaker_preserving_neural_echo_v2_prepare"'
    )
    prepare_end = source.index(
        'step(\n            "materialize_live_asr_cache"', prepare_start
    )
    prepare_block = source[prepare_start:prepare_end]
    require(
        "enabled=not args.skip_transcription" in prepare_block
        and "not args.skip_preprocess" not in prepare_block,
        "baseline preparation must run before ASR when preprocessing is reused",
    )
    require(
        '*([] if args.skip_preprocess else ["--fresh-preprocess"])' in prepare_block,
        "fresh-preprocess marker is not coupled to the Echo Guard rebuild",
    )


def promoted_fallback_compatibility_checks() -> None:
    with tempfile.TemporaryDirectory(prefix="murmurmark-spne-frozen-input-") as temporary:
        session = Path(temporary) / "session"
        key = "derived/preprocess/audio/mic_role_masked_for_asr.wav"
        canonical = session / key
        canonical.parent.mkdir(parents=True)
        canonical.write_bytes(b"promoted-candidate")
        output = session / "derived/preprocess/speaker-preserving-neural-echo-v2"
        fallback = output / "baseline-local-fir-role-masked" / key
        fallback.parent.mkdir(parents=True)
        fallback.write_bytes(b"frozen-local-fir")
        PRODUCTION.write_json(
            output / "production_selection_report.json",
            {
                "status": "candidate",
                "selected_profile": "speaker_preserving_neural_echo_v2",
                "policy_checks": {"policy": True},
            },
        )
        PRODUCTION.write_json(
            output / "publication_transaction.json",
            {
                "state": "committed",
                "published": {
                    key: {
                        "sha256": PRODUCTION.sha256(canonical),
                    }
                },
            },
        )
        frozen = {
            "path": str(canonical),
            "size": fallback.stat().st_size,
            "sha256": PRODUCTION.sha256(fallback),
        }
        require(
            SYNTHESIS.frozen_artifact_tree_matches(frozen),
            "promoted canonical audio hid its exact frozen FIR fallback",
        )
        fallback.write_bytes(b"changed-fallback")
        require(
            not SYNTHESIS.frozen_artifact_tree_matches(frozen),
            "changed FIR fallback was accepted as frozen evidence",
        )


def main() -> int:
    policy_checks()
    hard_seal_check()
    causal_audio_checks()
    checkpoint_check()
    improvement_only_arbitration_checks()
    hypothesis_bank_checks()
    v27_segmentation_stability_check()
    v27_exact_audio_and_rollback_checks()
    v27_me_guard_source_checks()
    v28_fail_open_selector_checks()
    v29_profile_matched_verdict_checks()
    v210_outcome_gate_profile_checks()
    v210_chronology_rollback_checks()
    v211_local_island_rollback_checks()
    v213_iterative_outcome_mapping_checks()
    v214_local_island_and_window_rollback_checks()
    v214_identity_bounded_asr_checks()
    v215_applicability_gate_checks()
    v216_safety_fallback_credit_checks()
    v216_cache_seed_checks()
    micro_reasr_content_cache_checks()
    production_publication_rollback_checks()
    production_pipeline_order_checks()
    promoted_fallback_compatibility_checks()
    print("speaker-preserving neural echo v2 checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
