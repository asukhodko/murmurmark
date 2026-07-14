#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def load_module():
    path = Path(__file__).with_name("report-live-corpus-gates.py")
    spec = importlib.util.spec_from_file_location("murmurmark_report_live_corpus_gates", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def profile_summary(module, *, runtime_remote: float = 2.0) -> dict[str, object]:
    baseline = module.RUNTIME_CAUSAL_TARGET_ME_BASELINE_PROFILE_POLICY
    runtime = module.RUNTIME_CAUSAL_TARGET_ME_DIRECT_PROFILE_POLICY
    summary: dict[str, object] = {}
    for policy, passed, comparable_passed, non_passing, comparable, missing, remote in (
        (baseline, 1, 1, 15, 15, 100.0, 2.0),
        (runtime, 0, 1, 18, 15, 60.0, runtime_remote),
    ):
        base = f"real_live_target_me_shadow_profile_{policy}"
        summary[f"{base}_evaluated_session_count"] = 3
        summary[f"{base}_all_parity_gates_passed_session_count"] = passed
        summary[f"{base}_comparable_all_parity_gates_passed_session_count"] = comparable_passed
        summary[f"{base}_non_passing_gate_count"] = non_passing
        summary[f"{base}_comparable_non_passing_gate_count"] = comparable
        summary[f"{base}_live_missing_me_seconds"] = missing
        summary[f"{base}_live_suspected_remote_leak_in_me_seconds"] = remote
        summary[f"{base}_live_contentful_role_constrained_order_mismatch_count"] = 1
        summary[f"{base}_live_blocking_contentful_role_constrained_order_mismatch_count"] = 0
        summary[f"{base}_live_advisory_contentful_role_constrained_order_mismatch_count"] = 1
        summary[f"{base}_causal_pre_stop_direct_profile_candidate_count"] = 1 if policy == runtime else 0
    return summary


def main() -> int:
    module = load_module()
    runtime = module.RUNTIME_CAUSAL_TARGET_ME_DIRECT_PROFILE_POLICY
    baseline = module.RUNTIME_CAUSAL_TARGET_ME_BASELINE_PROFILE_POLICY
    boundary_retime = module.BASELINE_LIVE_BOUNDARY_SPLIT_RETIME_PROFILE_POLICY
    assert boundary_retime in module.TARGET_ME_SHADOW_PROFILE_POLICIES
    speaker_only = module.BASELINE_LIVE_BOUNDARY_SPLIT_RETIME_SPEAKER_ONLY_PROFILE_POLICY
    assert speaker_only in module.TARGET_ME_SHADOW_PROFILE_POLICIES

    root = Path("/tmp/murmurmark-live-corpus")
    assert module.evidence_scope(root / "2026-07-10_16-00-29", root) == "real_meeting"
    assert module.evidence_scope(root / "2026-07-10_16-00-29-live", root) == "real_meeting"
    assert module.evidence_scope(root / "2026-07-10_16-00-29-live-soak", root) == "diagnostic"
    assert module.evidence_scope(root / "_live-preview-smoke", root) == "diagnostic"

    extracted = module.target_me_shadow_profile_metric_values(
        {
            f"live_target_me_shadow_profile_{runtime}_causal_pre_stop_direct_profile_candidate_count": 36,
            (
                "live_target_me_shadow_profile_"
                f"{module.RUNTIME_CAUSAL_TARGET_ME_REMOTE_ENERGY_PROFILE_POLICY}_"
                "causal_pre_stop_remote_energy_profile_candidate_count"
            ): 23,
        }
    )
    assert (
        extracted[f"live_target_me_shadow_profile_{runtime}_causal_pre_stop_direct_profile_candidate_count"]
        == 36
    ), extracted
    energy_metric = (
        "live_target_me_shadow_profile_"
        f"{module.RUNTIME_CAUSAL_TARGET_ME_REMOTE_ENERGY_PROFILE_POLICY}_"
        "causal_pre_stop_remote_energy_profile_candidate_count"
    )
    assert extracted[energy_metric] == 23, extracted

    diagnostics = module.target_me_shadow_profile_diagnostics(profile_summary(module), "real")
    selected = diagnostics.get("best_live_implementable_profile") or {}
    assert selected.get("policy") == runtime, selected
    assert selected.get("all_parity_gates_passed_session_count") == 0, selected
    assert selected.get("comparable_all_parity_gates_passed_session_count") == 1, selected
    assert selected.get("non_passing_gate_count") == 18, selected
    assert selected.get("comparable_non_passing_gate_count") == 15, selected

    unsafe = module.target_me_shadow_profile_diagnostics(
        profile_summary(module, runtime_remote=8.0),
        "real",
    )
    unsafe_selected = unsafe.get("best_live_implementable_profile") or {}
    assert unsafe_selected.get("policy") == baseline, unsafe_selected

    row_metrics: dict[str, object] = {}
    for policy, f1, matched, missing in (
        (baseline, 0.8, 80, 100.0),
        (runtime, 0.82, 82, 60.0),
    ):
        base = f"live_target_me_shadow_profile_{policy}"
        row_metrics[f"{base}_non_passing_gate_count"] = 1
        row_metrics[f"{base}_live_batch_token_f1"] = f1
        row_metrics[f"{base}_live_dialogue_token_count"] = 100
        row_metrics[f"{base}_batch_dialogue_token_count"] = 100
        row_metrics[f"{base}_matched_dialogue_token_count"] = matched
        row_metrics[f"{base}_live_missing_me_seconds"] = missing
        row_metrics[f"{base}_live_suspected_remote_leak_in_me_seconds"] = 2.0
        row_metrics[f"{base}_live_blocking_contentful_role_constrained_order_mismatch_count"] = 0
        row_metrics[f"{base}_live_advisory_contentful_role_constrained_order_mismatch_count"] = 1
    row_metrics[
        f"live_target_me_shadow_profile_{runtime}_causal_pre_stop_direct_profile_candidate_count"
    ] = 1
    no_regression = module.runtime_profile_no_regression([{"session": "fixture", "metrics": row_metrics}])
    assert no_regression.get("pre_stop_runtime_evidence_session_count") == 1, no_regression

    unlock_args = {
        "remaining_gap": {},
        "order_risk_triage": {
            "status": "ok",
            "item_count": 1,
            "blocking_count": 1,
            "boundary_retime_candidate_count": 1,
        },
        "speaker_boundary_lab": {},
        "mixed_voice_coverage_lab": {},
        "tight_voice_remote_guard_lab": {},
        "same_session_voice_disambiguation_lab": {},
        "local_island_split": {},
        "live_only_local_island": {},
        "timing_gap": {},
    }
    missing_oracles = module.live_next_unlock_report(summary={}, **unlock_args)
    missing_retime = missing_oracles["boundary_order_retime_oracle"]
    missing_split = missing_oracles["boundary_order_split_retime_oracle"]
    assert missing_retime["status"] == "not_evaluated", missing_retime
    assert missing_retime["missing_me_seconds"] is None, missing_retime
    assert missing_retime["retimed_turn_count"] is None, missing_retime
    assert missing_split["status"] == "not_evaluated", missing_split
    assert missing_split["contentful_order_mismatch_count"] is None, missing_split
    boundary_action = next(
        row for row in missing_oracles["next_actions"] if row.get("id") == "repair_live_boundary_retime_order_risk"
    )
    assert boundary_action["oracle_evidence"]["status"] == "not_evaluated", boundary_action
    assert boundary_action["split_oracle_evidence"]["missing_me_delta_seconds"] is None, boundary_action

    evaluated_summary = {
        "real_live_boundary_order_retime_oracle_profile_missing_me_seconds": 0.0,
        "real_live_boundary_order_retime_oracle_profile_missing_me_delta_vs_best_live_implementable_seconds": 0.0,
        "real_live_boundary_order_retime_oracle_profile_remote_leak_seconds": 0.0,
        "real_live_boundary_order_retime_oracle_profile_contentful_order_mismatch_count": 0,
        "real_live_boundary_order_retime_oracle_profile_retimed_turn_count": 0,
        "real_live_boundary_order_retime_oracle_profile_retimed_trimmed_seconds": 0.0,
        "real_live_boundary_order_split_retime_oracle_profile_missing_me_seconds": 0.0,
        "real_live_boundary_order_split_retime_oracle_profile_missing_me_delta_vs_best_live_implementable_seconds": 0.0,
        "real_live_boundary_order_split_retime_oracle_profile_remote_leak_seconds": 0.0,
        "real_live_boundary_order_split_retime_oracle_profile_contentful_order_mismatch_count": 0,
        "real_live_boundary_order_split_retime_oracle_profile_retimed_turn_count": 0,
        "real_live_boundary_order_split_retime_oracle_profile_retimed_trimmed_seconds": 0.0,
        "real_live_boundary_order_split_retime_oracle_profile_preserved_prefix_count": 0,
        "real_live_boundary_order_split_retime_oracle_profile_preserved_prefix_seconds": 0.0,
    }
    evaluated_oracles = module.live_next_unlock_report(summary=evaluated_summary, **unlock_args)
    assert evaluated_oracles["boundary_order_retime_oracle"]["status"] == "evaluated", evaluated_oracles
    assert evaluated_oracles["boundary_order_retime_oracle"]["missing_me_seconds"] == 0.0, evaluated_oracles
    assert evaluated_oracles["boundary_order_split_retime_oracle"]["status"] == "evaluated", evaluated_oracles

    assert not module.target_me_shadow_profile_row(
        {"profiles": [{"policy": "not-run", "evaluated_session_count": 0, "live_missing_me_seconds": 0.0}]},
        "not-run",
    )
    evaluated_row = module.target_me_shadow_profile_row(
        {"profiles": [{"policy": "run", "evaluated_session_count": 1, "live_missing_me_seconds": 0.0}]},
        "run",
    )
    assert evaluated_row.get("policy") == "run", evaluated_row

    print("live profile selection checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
