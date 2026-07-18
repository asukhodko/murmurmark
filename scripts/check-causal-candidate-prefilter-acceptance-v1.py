#!/usr/bin/env python3
"""Validate the completed candidate-prefilter experiment and its binary decision."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


SCHEMA = "murmurmark.causal_candidate_prefilter_acceptance/v1"
EXPECTED_ROUTES = {
    "cheap_reject": 48,
    "expensive_candidate": 159,
    "unresolved": 576,
}
EXPECTED_BLOCKERS = {
    "accepted_negative_controls_zero",
    "all_holdout_runtime_gates_pass",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=Path(
            "sessions/_reports/live-pipeline/"
            "causal-candidate-coverage-cheap-negative-prefilter-v1"
        ),
    )
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    report_dir = args.report_dir.expanduser().resolve()
    report = read_json(report_dir / "coverage_report_v1.json")
    decision = read_json(report_dir / "promotion_decision.json")
    coverage = report.get("coverage") if isinstance(report.get("coverage"), dict) else {}
    expensive = (
        report.get("expensive_stage")
        if isinstance(report.get("expensive_stage"), dict)
        else {}
    )
    fixed = (
        report.get("fixed_recoveries")
        if isinstance(report.get("fixed_recoveries"), dict)
        else {}
    )
    runtime = report.get("runtime") if isinstance(report.get("runtime"), dict) else {}
    regression = (
        report.get("no_regression")
        if isinstance(report.get("no_regression"), dict)
        else {}
    )
    hard_checks = report.get("hard_checks") if isinstance(report.get("hard_checks"), dict) else {}
    expected_decision = (
        "READY_FOR_PROMOTION_RECONSIDERATION"
        if hard_checks and all(hard_checks.values())
        else "DO_NOT_PROMOTE"
    )
    checks = {
        "report_schema": report.get("schema")
        == "murmurmark.causal_candidate_prefilter_report/v1",
        "decision_schema": decision.get("schema")
        == "murmurmark.causal_candidate_prefilter_promotion_decision/v1",
        "immutable_corpus_rows": (report.get("immutable_baseline") or {}).get(
            "corpus_row_count"
        )
        == 963,
        "immutable_input_files": (report.get("immutable_baseline") or {}).get(
            "input_file_count"
        )
        == 832,
        "eligible_routes_complete": coverage.get("decision_count") == 783
        and coverage.get("route_counts") == EXPECTED_ROUTES,
        "causal_contract_valid": not coverage.get("invalid_contract_rows"),
        "context_is_strictly_past_only": not coverage.get("future_context_rows"),
        "cheap_reject_preserves_genuine_double_talk": not coverage.get(
            "cheap_reject_genuine_double_talk_rows"
        ),
        "fixed_recoveries_preserved": fixed.get("recovered_count") == 4
        and abs(float(fixed.get("recovered_seconds") or 0.0) - 11.56) <= 0.001,
        "all_accepted_candidates_evaluated": expensive.get(
            "accepted_without_evaluation_count"
        )
        == 0,
        "negative_blocker_exposed": expensive.get("accepted_negative_control_count") == 1
        and expensive.get("accepted_frozen_negative_control_count") == 0
        and expensive.get("accepted_posthoc_negative_count") == 1,
        "runtime_blocker_exposed": runtime.get("passed_session_count") == 0
        and runtime.get("session_count") == 3
        and float(runtime.get("runtime_p95_max_sec") or 0.0) > 30.0
        and float(runtime.get("final_lag_max_sec", 999.0)) <= 0.001,
        "timeout_fail_open_observed": int(runtime.get("timeout_count") or 0) > 0,
        "no_regression": regression.get("status") == "passed",
        "binary_decision_matches_gates": decision.get("decision") == expected_decision,
        "known_blockers_exact": set(decision.get("blockers") or []) == EXPECTED_BLOCKERS,
        "normal_preview_isolated": report.get("normal_preview_connected") is False,
        "batch_authoritative": report.get("batch_authoritative") is True,
        "promotion_stays_blocked": report.get("promotion_allowed") is False,
    }
    payload = {
        "schema": SCHEMA,
        "created_at": datetime.now(UTC).isoformat(),
        "status": "passed" if all(checks.values()) else "failed",
        "checks": checks,
        "decision": decision.get("decision"),
        "blockers": decision.get("blockers") or [],
        "report": str(report_dir / "coverage_report_v1.json"),
    }
    write_json(report_dir / "acceptance_report_v1.json", payload)
    print(f"causal candidate prefilter acceptance: {payload['status']}")
    print(f"decision: {payload['decision']}")
    print(f"checks: {sum(checks.values())}/{len(checks)}")
    return 0 if payload["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
