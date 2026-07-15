#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
from scipy import signal


def load_module(filename: str, name: str) -> ModuleType:
    scripts = Path(__file__).resolve().parent
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    path = scripts / filename
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def source_selection(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": "selection_000002_0001",
        "session": "fixture",
        "chunk_index": 2,
        "start": 35.0,
        "end": 39.0,
        "duration_sec": 4.0,
        "text": "проверить локальную реплику пользователя",
        "checks": {
            "speaker_supported": True,
            "recording_time_committed_pcm": True,
            "timeline_causal": True,
            "selection_does_not_use_batch": True,
            "past_only_enrollment": True,
            "past_enrollment_ready": True,
            "source_text_contentful": True,
            "supported_duration": True,
            "not_already_published": True,
        },
        "recording_time_evidence": {"status": "passed"},
        "remote_audio_guard": {"remote_db": -30.0},
        "speaker_evidence": {"scores": {"positive": 0.9}},
        "source_evaluation": {"segment_index": 1},
        "batch_text": "evaluation-only value",
    }
    row.update(overrides)
    return row


def accepted_candidate(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": "remote_active_fixture",
        "status": "accepted",
        "chunk_index": 2,
        "start": 35.0,
        "end": 39.0,
        "text": "проверить локальную реплику пользователя",
        "timeline_causal": True,
        "used_batch_fields_for_selection": False,
        "selection_mode": "recording_time_causal_remote_active_separation_v1",
        "recording_time_evidence": {"status": "passed", "source": "committed_pcm"},
        "residual_audio_guard": {"status": "passed", "method": "causal_fir_v1"},
        "past_training_evidence": {"status": "passed", "past_only": True},
        "remote_active_guard": {"status": "passed"},
        "remote_text_guard": {"status": "passed"},
        "target_me_evidence": {"status": "passed"},
        "remote_forbidden_matches": [],
        "publication_allowed": False,
        "promotion_allowed": False,
        "batch_authoritative": True,
    }
    row.update(overrides)
    return row


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def check_causal_selection(module: ModuleType) -> None:
    selected, decisions = module.selection_decisions([source_selection()])
    assert len(selected) == 1 and decisions[0]["status"] == "selected", decisions
    assert decisions[0]["used_batch_fields_for_selection"] is False, decisions

    mutated = source_selection(batch_text="completely different", batch_start=-999.0)
    selected_mutated, decisions_mutated = module.selection_decisions([mutated])
    assert len(selected_mutated) == 1, decisions_mutated
    assert decisions_mutated[0]["checks"] == decisions[0]["checks"], decisions_mutated

    quiet = source_selection(remote_audio_guard={"remote_db": -90.0})
    quiet_selected, quiet_decisions = module.selection_decisions([quiet])
    assert not quiet_selected, quiet_decisions
    assert "remote_audio_active" in quiet_decisions[0]["reasons"], quiet_decisions

    unsupported = source_selection(
        checks={**source_selection()["checks"], "speaker_supported": False}
    )
    unsupported_selected, unsupported_decisions = module.selection_decisions([unsupported])
    assert not unsupported_selected, unsupported_decisions
    assert "speaker_supported" in unsupported_decisions[0]["reasons"], unsupported_decisions


def check_synthetic_separation(module: ModuleType) -> None:
    rng = np.random.default_rng(42)
    remote_train = signal.lfilter([1.0], [1.0, -0.92], rng.normal(0.0, 0.08, 48_000))
    echo_filter = np.zeros(1_280, dtype=np.float64)
    echo_filter[120] = 0.75
    echo_filter[460] = 0.18
    mic_train = signal.lfilter(echo_filter, [1.0], remote_train)
    fir = module.fit_fir([(remote_train, mic_train)], 0)
    spectral = module.fit_spectral_transfer([(remote_train, mic_train)], 0)
    model = module.SeparationModel(
        chunk_index=2,
        training_rows=[],
        training_seconds=3.0,
        delay_samples=0,
        delay_evidence={"status": "fixture"},
        fir=fir,
        spectral_transfer=spectral,
    )
    remote = signal.lfilter([1.0], [1.0, -0.92], rng.normal(0.0, 0.08, 32_000))
    local = signal.lfilter([1.0, -0.7], [1.0], rng.normal(0.0, 0.025, 32_000))
    echo = signal.lfilter(echo_filter, [1.0], remote)
    mic = echo + local
    residuals = module.method_residuals(model, remote, mic)
    metrics = [
        module.evaluate_method(name, remote, mic, residual, estimate)
        for name, (residual, estimate) in residuals.items()
    ]
    best = max(metrics, key=lambda row: float(row["projection_reduction_db"]))
    assert best["projection_reduction_db"] >= 3.0, metrics
    assert best["after"]["remote_strength"] < best["before"]["remote_strength"], metrics
    assert best["after"]["finite"] is True, metrics


def check_shadow_contract_and_isolation(compare: ModuleType) -> None:
    policy = compare.CAUSAL_REMOTE_ACTIVE_ME_SEPARATION_V1_PROFILE_POLICY
    baseline = compare.CAUSAL_LOCAL_ISLAND_MICRO_ASR_V2_PROFILE_POLICY
    assert policy in compare.MATERIALIZED_TARGET_ME_SHADOW_POLICIES
    assert policy not in compare.DEFAULT_TARGET_ME_SHADOW_POLICIES
    assert policy in compare.LIVE_ME_REMOTE_OVERLAP_FILTER_SHADOW_PROFILE_POLICIES
    assert policy in compare.LIVE_ME_REMOTE_OVERLAP_FILTER_NO_TARGET_PROFILE_POLICIES
    assert policy in compare.CAUSAL_LOCAL_ISLAND_MICRO_ASR_V2_PROFILE_POLICIES

    with tempfile.TemporaryDirectory(prefix="murmurmark-remote-active-shadow-") as root:
        session = Path(root) / "session"
        candidate_path = session / "derived/live/causal-remote-active-me-separation-v1/candidates.jsonl"
        write_jsonl(candidate_path, [accepted_candidate()])
        turns, rejected = compare.causal_remote_active_me_separation_v1_shadow_turns(session)
        assert len(turns) == 1 and not rejected, (turns, rejected)
        assert turns[0]["causal_remote_active_me_separation_v1_shadow"] is True, turns

        unsafe = accepted_candidate(remote_text_guard={"status": "rejected"})
        write_jsonl(candidate_path, [unsafe])
        unsafe_turns, unsafe_rejected = compare.causal_remote_active_me_separation_v1_shadow_turns(session)
        assert not unsafe_turns, unsafe_turns
        assert "remote_text_guard" in unsafe_rejected[0]["failed_checks"], unsafe_rejected

        old_dir = session / "derived/live/target-me-shadow" / baseline
        old_dir.mkdir(parents=True)
        old_json = old_dir / "draft.json"
        old_md = old_dir / "draft.md"
        old_json.write_bytes(b"baseline-json-byte-stable\n")
        old_md.write_bytes(b"baseline-md-byte-stable\n")
        before = (old_json.read_bytes(), old_md.read_bytes())
        outputs = compare.write_target_me_shadow_drafts(
            session=session,
            live_turns_rows=[],
            suppressed_mic_asr_segments=[],
            target_me_rows=[],
            target_me_turns_by_policy={},
            persistent_target_me_rows=[],
            batch_utterances=[],
            metrics={},
            policies=(baseline, policy),
            write_policies={policy},
        )
        assert (old_json.read_bytes(), old_md.read_bytes()) == before
        assert policy in outputs and (session / outputs[policy]["draft_json"]).exists(), outputs


def check_reconciliation_rule(reconciliation: ModuleType) -> None:
    item = {
        "same_source": False,
        "same_chunk": True,
        "source_pair": "mic_causal_remote_active_me_separation_v1_shadow->remote_segment",
        "role_pair": "Me->Colleagues",
        "role_mismatch_in_pair": False,
        "batch_start_delta_sec": -7.0,
        "previous_live_inside_own_batch_interval": False,
        "current_live_inside_own_batch_interval": True,
        "previous": {
            "live_id": "remote_active_fixture",
            "source": "mic_causal_remote_active_me_separation_v1_shadow",
            "role": "Me",
            "start": 100.0,
            "end": 106.0,
            "batch_start": 103.0,
            "score_margin": 0.23,
            "plausible_match_count": 2,
        },
        "current": {
            "live_id": "remote_fixture",
            "source": "remote_segment",
            "role": "Colleagues",
            "start": 112.0,
            "end": 117.0,
            "batch_start": 95.0,
            "score_margin": 0.30,
            "plausible_match_count": 9,
        },
    }
    result = reconciliation.classify_order_risk(
        item,
        session="fixture",
        profile="fixture-profile",
    )
    assert result["classification"] == "matcher_temporal_false_positive", result
    assert result["disposition"] == "advisory", result
    assert result["shadow_repair_required"] is False, result


def check_reporter_outcomes(reporter: ModuleType) -> None:
    source = {
        "id": "scope_1",
        "session": "fixture",
        "evaluation_reference": {"start": 35.5, "end": 38.0, "duration_sec": 2.5},
    }
    draft = {
        **accepted_candidate(),
        "role": "Me",
        "causal_remote_active_me_separation_v1_shadow": True,
    }
    accepted = reporter.outcome_for_row(
        source,
        scope="primary_remote_active",
        draft_turns=[draft],
        selection_rows=[],
        residual_rows=[],
        candidate_rows=[accepted_candidate()],
    )
    assert accepted["outcome"] == "accepted", accepted

    rejected = reporter.outcome_for_row(
        source,
        scope="primary_remote_active",
        draft_turns=[],
        selection_rows=[{"id": "selection", "start": 35.0, "end": 39.0, "status": "selected"}],
        residual_rows=[{"id": "residual", "start": 35.0, "end": 39.0, "status": "rejected"}],
        candidate_rows=[],
    )
    assert rejected["outcome"] == "rejected", rejected


def check_corpus_registration(corpus: ModuleType, compare: ModuleType) -> None:
    policy = compare.CAUSAL_REMOTE_ACTIVE_ME_SEPARATION_V1_PROFILE_POLICY
    assert policy == corpus.CAUSAL_REMOTE_ACTIVE_ME_SEPARATION_V1_PROFILE_POLICY
    assert policy in corpus.TARGET_ME_SHADOW_PROFILE_POLICIES
    for metric in (
        "causal_remote_active_me_separation_v1_added_turn_count",
        "causal_remote_active_me_separation_v1_added_turn_seconds",
        "causal_remote_active_me_separation_v1_rejected_turn_count",
    ):
        assert metric in corpus.TARGET_ME_SHADOW_PROFILE_METRICS, metric


def main() -> int:
    separator = load_module(
        "live-causal-remote-active-me-separation.py",
        "murmurmark_check_remote_active_separator",
    )
    compare = load_module("compare-live-batch.py", "murmurmark_check_remote_active_compare")
    corpus = load_module("report-live-corpus-gates.py", "murmurmark_check_remote_active_corpus")
    reporter = load_module(
        "report-live-causal-remote-active-me-separation-v1.py",
        "murmurmark_check_remote_active_reporter",
    )
    reconciliation = load_module(
        "live_order_role_reconciliation.py",
        "murmurmark_check_remote_active_reconciliation",
    )
    check_causal_selection(separator)
    check_synthetic_separation(separator)
    check_shadow_contract_and_isolation(compare)
    check_reconciliation_rule(reconciliation)
    check_reporter_outcomes(reporter)
    check_corpus_registration(corpus, compare)
    print("live causal remote-active Me separation v1 checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
