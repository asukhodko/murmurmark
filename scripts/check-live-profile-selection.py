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
    return summary


def main() -> int:
    module = load_module()
    runtime = module.RUNTIME_CAUSAL_TARGET_ME_DIRECT_PROFILE_POLICY
    baseline = module.RUNTIME_CAUSAL_TARGET_ME_BASELINE_PROFILE_POLICY

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

    print("live profile selection checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
