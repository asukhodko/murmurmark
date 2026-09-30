#!/usr/bin/env python3
"""Focused checks for enrichment reconciliation and review decision rebasing."""

from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str, filename: str) -> Any:
    path = ROOT / "scripts" / filename
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


WORKSPACE = load_script("murmurmark_review_workspace_coherence", "build-review-workspace.py")
QUALITY = load_script("murmurmark_session_quality_coherence", "report-session-quality.py")
RECONCILE = load_script("murmurmark_state_reconciliation_coherence", "reconcile-session-state.py")
OUTCOME = load_script("murmurmark_outcome_coherence", "evaluate-outcome.py")


def review_row(
    *,
    cluster: str,
    utterance: str,
    text: str = "Проверить алерты",
    decision: str = "todo",
) -> dict[str, Any]:
    return {
        "session_id": "fixture",
        "source": "transcript_text",
        "cluster_id": cluster,
        "review_action": "confirm_me",
        "label": "local_recall_needs_review",
        "utterance_ids": [utterance],
        "me_utterance_ids": [utterance],
        "remote_utterance_ids": [],
        "interval": {"start": 10.0, "end": 12.0},
        "text": [{"id": utterance, "role": "me", "text": text}],
        "allowed_decisions": ["keep_me", "drop_me", "needs_review"],
        "decision": decision,
        "status": "reviewed" if decision != "todo" else "todo",
        "input_profile": "reviewed_v1",
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def main() -> None:
    existing = review_row(cluster="old", utterance="utt_old", decision="keep_me")
    same_semantics = review_row(cluster="new", utterance="utt_new")
    merged, report = WORKSPACE.merge_existing_with_report([same_semantics], [existing])
    assert merged[0]["decision"] == "keep_me", merged
    assert merged[0]["review_rebase"]["match"] == "semantic_interval_text", merged
    assert report["carried_rows"] == 1 and report["unmatched_closed_rows"] == 0, report

    changed_text = review_row(cluster="newer", utterance="utt_newer", text="Обсудить бюджет")
    rejected, rejected_report = WORKSPACE.merge_existing_with_report([changed_text], [existing])
    assert rejected[0]["decision"] == "todo", rejected
    assert rejected_report["carried_rows"] == 0 and rejected_report["unmatched_closed_rows"] == 1

    duplicated_targets, duplicate_report = WORKSPACE.merge_existing_with_report(
        [same_semantics, dict(same_semantics)],
        [existing],
    )
    assert [row["decision"] for row in duplicated_targets].count("keep_me") == 1
    assert duplicate_report["carried_rows"] == 1

    agreeing_history = [dict(existing), dict(existing)]
    carried_from_history, history_report = WORKSPACE.merge_existing_with_report(
        [same_semantics],
        agreeing_history,
    )
    assert carried_from_history[0]["decision"] == "keep_me", carried_from_history
    assert history_report["carried_rows"] == 1, history_report

    duplicate_history_targets, duplicate_history_report = WORKSPACE.merge_existing_with_report(
        [same_semantics, dict(same_semantics)],
        agreeing_history,
    )
    assert [row["decision"] for row in duplicate_history_targets].count("keep_me") == 1
    assert duplicate_history_report["carried_rows"] == 1

    conflicting_history = [dict(existing), dict(existing, decision="drop_me")]
    rejected_conflict, conflict_report = WORKSPACE.merge_existing_with_report(
        [same_semantics],
        conflicting_history,
    )
    assert rejected_conflict[0]["decision"] == "todo", rejected_conflict
    assert conflict_report["carried_rows"] == 0, conflict_report

    assert WORKSPACE.rebase_candidate_rows([], agreeing_history, [], enabled=True) == agreeing_history
    assert WORKSPACE.rebase_candidate_rows([], agreeing_history, [], enabled=False) == []

    history, added = WORKSPACE.archive_closed_decisions([], [existing], archived_at="fixture")
    assert added == 1 and len(history) == 1
    repeated_history, repeated_added = WORKSPACE.archive_closed_decisions(
        history,
        [existing],
        archived_at="fixture-later",
    )
    assert repeated_added == 0 and repeated_history == history

    progress_summary = {
        "total": 7,
        "reviewed": 3,
        "remaining": 4,
        "remaining_seconds": 12.3456,
    }
    metrics = QUALITY.review_progress_metrics(progress_summary)
    assert metrics["manual_review_queue_rows"] == 4
    assert metrics["manual_review_queue_seconds"] == 12.346
    assert metrics["review_scope_closed_rows"] == 3
    assert metrics["manual_review_queue_source"] == "review_decisions_progress"

    with tempfile.TemporaryDirectory(prefix="murmurmark-review-progress-order-") as temporary:
        session = Path(temporary) / "fixture"
        plan = session / "derived/readiness/review-plan"
        plan.mkdir(parents=True)
        decisions = [
            {
                "source": "transcript_order",
                "review_lane": "check_transcript_order",
                "label": "probable_order_risk",
                "decision": "keep_me",
                "review_source": "manual",
                "status": "reviewed",
                "interval": {"start": 1.0, "end": 50.0, "duration_sec": 49.0},
            },
            {
                "source": "transcript_order",
                "review_lane": "check_transcript_order",
                "label": "transcript_order_needs_review",
                "decision": "skip",
                "status": "reviewed",
                "interval": {"start": 60.0, "end": 108.91, "duration_sec": 48.91},
            },
        ]
        (plan / "review_decisions.jsonl").write_text(
            "\n".join(json.dumps(row) for row in decisions) + "\n",
            encoding="utf-8",
        )
        progress = {
            "summary": progress_summary,
            "by_lane": [
                {
                    "review_lane": "check_transcript_order",
                    "total": 2,
                    "reviewed": 2,
                    "remaining": 0,
                    "seconds": 97.91,
                    "remaining_seconds": 0.0,
                    "decisions": {"keep_me": 1, "skip": 1},
                }
            ],
            "errors": [],
        }
        order_metrics = QUALITY.review_progress_transcript_order_metrics(session, progress)
        assert order_metrics["transcript_order_review_seconds"] == 0.0, order_metrics
        assert order_metrics["transcript_order_blocking_order_risk"] is False, order_metrics
        assert order_metrics["transcript_order_reconciliation_source"] == "review_decisions_progress"

        decisions[0]["decision"] = "needs_review"
        decisions[0]["status"] = "todo"
        (plan / "review_decisions.jsonl").write_text(
            "\n".join(json.dumps(row) for row in decisions) + "\n",
            encoding="utf-8",
        )
        progress["by_lane"][0].update(
            {
                "reviewed": 1,
                "remaining": 1,
                "remaining_seconds": 49.0,
                "decisions": {"skip": 1, "needs_review": 1},
            }
        )
        unresolved_order = QUALITY.review_progress_transcript_order_metrics(session, progress)
        assert unresolved_order["transcript_order_review_seconds"] == 49.0, unresolved_order
        assert unresolved_order["transcript_order_blocking_order_risk"] is True, unresolved_order

    burden_row = {
        "meeting_duration_sec": 1000.0,
        "audio_review_stronger_judge_seconds": 6.39,
        "transcript_order_review_seconds": 97.91,
        "review_scope_remaining_seconds": 4.55,
        "manual_review_queue_source": "review_decisions_progress",
        "manual_review_queue_seconds": 4.55,
        "risk_flags": ["partial_review_scope"],
        "needs_review_count": 2,
        "notes_needs_review_count": 0,
        "selected_profile": "reviewed_v1",
        "pipeline_status": "complete",
        "verdict": "usable_with_review",
    }
    QUALITY.add_use_gate(burden_row)
    assert burden_row["transcript_review_burden_sec"] == 4.55, burden_row
    assert burden_row["review_burden_sec"] == 4.55, burden_row
    stale_closure = {
        "answers_source": "suggested",
        "dry_run": False,
        "summary": {"total_rows": 7},
        "suggested_closure": {
            "status": "manual_review_required",
            "generated_suggestions": {"rows": 4, "seconds": 12.346},
            "closed_by_suggestions": {"rows": 0, "seconds": 0.0},
            "remaining_manual_queue": {"rows": 4, "seconds": 12.346},
        },
    }
    zero_reviewed_progress = {
        "total": 25,
        "reviewed": 0,
        "remaining": 25,
        "remaining_seconds": 53.56,
    }
    stale_metrics = QUALITY.suggested_closure_metrics(
        stale_closure,
        zero_reviewed_progress,
    )
    assert stale_metrics["suggested_closure_report_stale"] is True
    assert stale_metrics["suggested_closure_status"] == "stale_review_queue"
    assert stale_metrics["suggested_closure_manual_remaining_rows"] == 25
    assert stale_metrics["suggested_closure_manual_remaining_seconds"] == 53.56
    assert stale_metrics["suggested_closure_auto_rows"] == 0
    missing_review_counts = QUALITY.risk_flags(
        {
            "selected_profile": "reviewed_v1",
            "review_scope_complete": False,
            "review_scope_remaining_seconds": 1.0,
        }
    )
    assert "review_decisions_gates_failed" in missing_review_counts

    with tempfile.TemporaryDirectory(prefix="murmurmark-enrichment-coherence-") as temporary:
        session = Path(temporary) / "fixture"
        queue_metrics = {
            "suggested_closure_manual_remaining_rows": 4,
            "suggested_closure_manual_remaining_seconds": 12.346,
            "manual_review_queue_rows": 4,
            "manual_review_queue_seconds": 12.346,
        }
        write_json(
            session / "derived/readiness/session_readiness.json",
            {"selected_profile": "reviewed_v1", "metrics": queue_metrics},
        )
        write_json(
            session / "derived/outcome/outcome.json",
            {"selected_profile": "reviewed_v1", "metrics": queue_metrics},
        )
        write_json(
            session / "derived/transcript-rich/speaker-resolved-default-v1/selection.json",
            {"selected_profile": "reviewed_v1", "gates": {"current_profile": True}},
        )
        write_json(
            session / "derived/readiness/review-plan/review_decisions_progress.json",
            {"summary": progress_summary},
        )
        write_json(
            session / "derived/readiness/review-plan/review_workspace_apply_report.json",
            stale_closure,
        )
        outcome_metrics = OUTCOME.suggested_review_metrics(session)
        assert outcome_metrics["suggested_closure_report_stale"] is False
        assert outcome_metrics["suggested_closure_manual_remaining_rows"] == 4

        write_json(
            session / "derived/readiness/review-plan/review_decisions_progress.json",
            {"summary": zero_reviewed_progress},
        )
        stale_outcome_metrics = OUTCOME.suggested_review_metrics(session)
        assert stale_outcome_metrics["suggested_closure_report_stale"] is True
        assert stale_outcome_metrics["suggested_closure_manual_remaining_rows"] == 25
        write_json(
            session / "derived/readiness/review-plan/review_decisions_progress.json",
            {"summary": progress_summary},
        )
        consistency = RECONCILE.verify_consistency(session)
        assert consistency["passed"] is True, consistency

        canonical_only = {
            "manual_review_queue_rows": 4,
            "manual_review_queue_seconds": 12.346,
        }
        write_json(
            session / "derived/readiness/session_readiness.json",
            {"selected_profile": "reviewed_v1", "metrics": canonical_only},
        )
        write_json(
            session / "derived/outcome/outcome.json",
            {"selected_profile": "reviewed_v1", "metrics": canonical_only},
        )
        assert RECONCILE.verify_consistency(session)["passed"] is True

        broken_outcome = {"selected_profile": "reviewed_v1", "metrics": dict(canonical_only)}
        broken_outcome["metrics"]["manual_review_queue_seconds"] = 99.0
        write_json(session / "derived/outcome/outcome.json", broken_outcome)
        inconsistent = RECONCILE.verify_consistency(session)
        assert inconsistent["passed"] is False
        assert inconsistent["checks"]["review_seconds_agree"] is False

        primary_path = session / "derived/pipeline-run/pipeline_run_report.json"
        deferred_path = primary_path.with_name("deferred_enrichment_report.json")
        write_json(primary_path, {"phase": "fast", "status": "passed"})
        for status in ("interrupted", "failed", "deferred_budget_exhausted"):
            write_json(deferred_path, {"phase": "deferred", "status": status})
            path, primary, optional = OUTCOME.pipeline_inputs(session, deferred_path)
            assert path == primary_path and primary["status"] == "passed"
            assert optional["status"] == status
        write_json(primary_path, {"phase": "fast", "status": "failed"})
        assert OUTCOME.pipeline_inputs(session, deferred_path)[1]["status"] == "failed"
        primary_path.unlink()
        assert OUTCOME.pipeline_inputs(session, deferred_path)[1]["status"] != "passed"
        write_json(deferred_path, {"phase": "deferred", "status": "passed"})
        assert OUTCOME.pipeline_inputs(session, deferred_path)[1]["status"] == "failed"

    print("enrichment coherence checks passed")


if __name__ == "__main__":
    main()
