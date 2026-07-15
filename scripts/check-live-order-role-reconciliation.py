#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

from live_order_role_reconciliation import build_reconciliation, classify_order_risk


def load_corpus_module():
    path = Path(__file__).with_name("report-live-corpus-gates.py")
    spec = importlib.util.spec_from_file_location("murmurmark_report_live_corpus_gates_reconciliation", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def risk_row(
    *,
    previous_source: str = "mic_segment",
    previous_role: str = "Me",
    current_source: str = "remote_segment",
    current_role: str = "Colleagues",
    previous_start: float = 10.0,
    previous_end: float = 14.0,
    current_start: float = 12.0,
    current_end: float = 16.0,
    previous_batch_start: float = 20.0,
    current_batch_start: float = 10.0,
    previous_margin: float = 0.8,
    current_margin: float = 0.8,
    previous_plausible: int = 1,
    current_plausible: int = 1,
    ambiguous: bool = False,
    previous_inside: bool = False,
    current_inside: bool = True,
) -> dict[str, Any]:
    same_source = previous_source == current_source
    return {
        "previous_live_id": "live_prev",
        "current_live_id": "live_curr",
        "same_source": same_source,
        "same_chunk": True,
        "source_pair": f"{previous_source}->{current_source}",
        "role_pair": f"{previous_role}->{current_role}",
        "role_mismatch_in_pair": False,
        "match_ambiguity": "ambiguous" if ambiguous else "unambiguous",
        "previous_live_start": previous_start,
        "current_live_start": current_start,
        "previous_batch_start": previous_batch_start,
        "current_batch_start": current_batch_start,
        "batch_start_delta_sec": current_batch_start - previous_batch_start,
        "live_start_delta_sec": current_start - previous_start,
        "previous_live_inside_own_batch_interval": previous_inside,
        "current_live_inside_own_batch_interval": current_inside,
        "previous": {
            "live_id": "live_prev",
            "source": previous_source,
            "role": previous_role,
            "start": previous_start,
            "end": previous_end,
            "text": "предыдущая содержательная реплика",
            "batch_id": "utt_prev",
            "batch_start": previous_batch_start,
            "score_margin": previous_margin,
            "plausible_match_count": previous_plausible,
            "turn_content_token_count": 4,
            "ambiguous_match": ambiguous,
        },
        "current": {
            "live_id": "live_curr",
            "source": current_source,
            "role": current_role,
            "start": current_start,
            "end": current_end,
            "text": "текущая содержательная реплика",
            "batch_id": "utt_curr",
            "batch_start": current_batch_start,
            "score_margin": current_margin,
            "plausible_match_count": current_plausible,
            "turn_content_token_count": 4,
            "ambiguous_match": ambiguous,
        },
    }


def previous(label: str = "needs_review_order_risk", severity: str = "blocking") -> dict[str, Any]:
    return {"label": label, "severity": severity, "confidence": "medium", "reason": "fixture"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check live order/role reconciliation rules and corpus evidence.")
    parser.add_argument("--corpus-report", type=Path)
    return parser.parse_args()


def check_corpus_report(path: Path) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    reconciliation = payload.get("live_order_role_reconciliation") or {}
    scope = reconciliation.get("scope") or {}
    classification = reconciliation.get("classification") or {}
    gate = reconciliation.get("effective_parity_gate") or {}
    promotion = reconciliation.get("promotion") or {}
    assert reconciliation.get("status") == "passed", reconciliation
    assert scope.get("candidate_session_count") == 7, scope
    assert scope.get("objective_scope_matched") is True, scope
    assert classification.get("previous_blocking_count") >= 15, classification
    assert classification.get("resolved_previous_blocking_count") == classification.get(
        "previous_blocking_count"
    ), classification
    assert classification.get("effective_blocking_count") == 0, classification
    assert classification.get("stable_classification_count") == classification.get("item_count"), classification
    assert classification.get("unresolved_count") == 0, classification
    assert gate.get("status") == "passed", gate
    assert "order_risk" in (gate.get("candidate_raw_blocking_dimensions") or []), gate
    assert "order_risk" not in (gate.get("candidate_blocking_dimensions") or []), gate
    assert promotion.get("allowed") is False, promotion

    per_session = reconciliation.get("per_session_no_regression") or []
    assert len(per_session) == 7, per_session
    selected_profile = str(reconciliation.get("selected_shadow_profile") or "")
    assert selected_profile, reconciliation
    sessions_root = path.resolve().parents[2]
    total_previous = 0
    total_effective = 0
    for row in per_session:
        assert row.get("status") == "passed", row
        assert row.get("selected_shadow_profile") == selected_profile, row
        no_regression = row.get("no_regression") or {}
        assert no_regression.get("turn_mutation_count") == 0, row
        for metric in (
            "missing_me_seconds",
            "remote_like_me_seconds",
            "live_batch_token_f1",
            "review_burden_sec",
            "review_burden_ratio",
        ):
            values = no_regression.get(metric) or {}
            assert values.get("before") == values.get("after"), (metric, row)
            assert values.get("delta") == 0.0, (metric, row)

        session_counts = row.get("classification") or {}
        total_previous += int(session_counts.get("previous_blocking_count") or 0)
        total_effective += int(session_counts.get("effective_blocking_count") or 0)
        comparison_path = (
            sessions_root
            / str(row.get("session") or "")
            / "derived/live/live_batch_comparison.json"
        )
        comparison = json.loads(comparison_path.read_text(encoding="utf-8"))
        profile = (((comparison.get("shadow_profiles") or {}).get("target_me") or {}).get(selected_profile) or {})
        profile_reconciliation = profile.get("order_role_reconciliation") or {}
        assert profile_reconciliation.get("previous_blocking_count") == session_counts.get(
            "previous_blocking_count"
        ), (comparison_path, profile_reconciliation, session_counts)
        assert profile_reconciliation.get("blocking_count") == session_counts.get("effective_blocking_count"), (
            comparison_path,
            profile_reconciliation,
            session_counts,
        )
        order_gate = next(
            gate_row
            for gate_row in ((profile.get("parity_gates") or {}).get("gates") or [])
            if gate_row.get("name") == "order_risk"
        )
        assert order_gate.get("status") == "passed", (comparison_path, order_gate)
        assert profile.get("promotion_allowed") is False, (comparison_path, profile)
    assert total_previous == classification.get("previous_blocking_count"), total_previous
    assert total_effective == 0, total_effective


def main() -> int:
    args = parse_args()
    cross_source = classify_order_risk(
        risk_row(),
        session="fixture",
        profile="shadow",
        previous_classification=previous("cross_source_order_risk"),
    )
    assert cross_source["classification"] == "causal_cross_source_overlap", cross_source
    assert cross_source["disposition"] == "advisory", cross_source
    assert cross_source["machine_evidence"]["source_role"]["pair_consistent"] is True, cross_source

    same_source = classify_order_risk(
        risk_row(
            previous_source="remote_segment",
            previous_role="Colleagues",
            current_source="remote_segment",
            current_role="Colleagues",
        ),
        session="fixture",
        profile="shadow",
        previous_classification=previous(),
    )
    assert same_source["classification"] == "causal_same_source_overlap_context", same_source

    far_match = classify_order_risk(
        risk_row(
            previous_source="remote_segment",
            previous_role="Colleagues",
            current_source="remote_segment",
            current_role="Colleagues",
            previous_start=1000.0,
            previous_end=1001.0,
            current_start=1002.0,
            current_end=1003.0,
            previous_batch_start=1000.0,
            current_batch_start=100.0,
            current_margin=0.2,
            current_plausible=20,
        ),
        session="fixture",
        profile="shadow",
        previous_classification=previous("same_source_timeline_reorder_candidate"),
    )
    assert far_match["classification"] == "matcher_temporal_false_positive", far_match

    ambiguous_boundary = classify_order_risk(
        risk_row(
            previous_end=11.0,
            current_start=11.4,
            current_end=13.0,
            previous_batch_start=40.0,
            current_batch_start=12.0,
            current_margin=0.1,
            current_plausible=2,
            ambiguous=True,
            current_inside=False,
        ),
        session="fixture",
        profile="shadow",
        previous_classification=previous("boundary_retime_candidate"),
    )
    assert ambiguous_boundary["classification"] == "matcher_ambiguous_reference", ambiguous_boundary

    role_conflict = classify_order_risk(
        risk_row(previous_role="Colleagues"),
        session="fixture",
        profile="shadow",
        previous_classification=previous(),
    )
    assert role_conflict["classification"] == "real_role_conflict", role_conflict
    assert role_conflict["shadow_repair_required"] is True, role_conflict
    assert role_conflict["disposition"] == "blocking", role_conflict

    causal_shadow_source = classify_order_risk(
        risk_row(
            current_source="mic_runtime_causal_target_me_micro_asr_shadow",
            current_role="Me",
        ),
        session="fixture",
        profile="shadow",
        previous_classification=previous("cross_source_order_risk"),
    )
    assert causal_shadow_source["classification"] == "causal_cross_source_overlap", causal_shadow_source
    assert causal_shadow_source["disposition"] == "advisory", causal_shadow_source
    assert causal_shadow_source["machine_evidence"]["source_role"]["current"]["expected_role"] == "Me", (
        causal_shadow_source
    )

    timeline_conflict = classify_order_risk(
        risk_row(
            previous_start=10.0,
            previous_end=12.0,
            current_start=14.0,
            current_end=16.0,
            previous_batch_start=13.0,
            current_batch_start=11.0,
            previous_inside=True,
            current_inside=True,
        ),
        session="fixture",
        profile="shadow",
        previous_classification=previous(),
    )
    assert timeline_conflict["classification"] == "real_timeline_conflict", timeline_conflict
    assert timeline_conflict["shadow_repair_required"] is True, timeline_conflict

    far_match_fixture = risk_row(
        previous_source="remote_segment",
        previous_role="Colleagues",
        current_source="remote_segment",
        current_role="Colleagues",
        previous_start=1000.0,
        previous_end=1001.0,
        current_start=1002.0,
        current_end=1003.0,
        previous_batch_start=1000.0,
        current_batch_start=100.0,
        current_margin=0.2,
        current_plausible=20,
    )
    summary = build_reconciliation(
        [risk_row(), far_match_fixture],
        session="fixture",
        profile="shadow",
        previous_classifier=lambda _row: previous(),
    )
    assert summary["previous_blocking_count"] == 2, summary
    assert summary["blocking_count"] == 0, summary
    assert summary["resolved_previous_blocking_count"] == 2, summary
    assert summary["status"] == "passed", summary

    corpus = load_corpus_module()
    parity_dimensions = {key: {"status": "passed"} for key in corpus.PARITY_DIMENSIONS}
    parity_dimensions["order_risk"] = {"status": "warning"}
    counts, issue_sessions, resolutions = corpus.summarize_evidence_informed_candidate_dimensions(
        [{"session": "fixture", "parity_dimensions": parity_dimensions}],
        {
            "examples": [
                {
                    "session": "fixture",
                    "severity": "advisory",
                    "classification": "causal_cross_source_overlap",
                }
            ]
        },
    )
    assert counts["order_risk"]["passed"] == 1, counts
    assert not issue_sessions.get("order_risk"), issue_sessions
    assert resolutions["fixture"]["raw_status"] == "warning", resolutions
    assert resolutions["fixture"]["effective_status"] == "passed", resolutions

    if args.corpus_report:
        check_corpus_report(args.corpus_report)

    print("live order/role reconciliation checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
