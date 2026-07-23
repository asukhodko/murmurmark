#!/usr/bin/env python3
"""Regression checks for audit-only versus materialized local-recall review rows."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace


def load_module(filename: str, name: str):
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    lane = load_module("build-review-lane-pack.py", "murmurmark_review_lane_materialization")
    apply = load_module("apply-review-decisions.py", "murmurmark_apply_review_materialization")

    raw = {
        "source": "local_recall",
        "source_audit_id": "local_recall_0001",
        "label": "lost_me",
        "review_lane": "check_local_recall",
        "suggested_decision": "keep_me",
        "suggested_decision_confidence": "high",
        "allowed_decisions": ["keep_me", "needs_review", "skip"],
        # Audit candidates have an id, but are not transcript utterances until
        # the local-recall repair profile materializes them.
        "me_utterance_ids": ["live_candidate_1"],
        "utterance_ids": ["live_candidate_1"],
    }
    assert lane.requires_materialized_local_recall([raw]) is True
    suggestion = lane.suggested_decision_for_group([raw], {}, {})
    assert suggestion[0] == "needs_review", suggestion
    assert "materialized" in suggestion[2], suggestion

    normalized = apply.normalize_decision({**raw, "decision": "keep_me"})
    assert normalized.get("_invalid") is True, normalized
    assert normalized.get("_invalid_reason") == "keep_me_requires_materialized_local_recall_utterance", normalized
    assert apply.obsolete_audit_only_local_recall_keep({**raw, "decision": "keep_me"}) is True

    materialized = {
        **raw,
        "source": "local_recall_repair",
        "label": "local_recall_repair_inserted",
        "me_utterance_ids": ["local_recall_repair_v1_local_recall_0001"],
        "utterance_ids": ["local_recall_repair_v1_local_recall_0001"],
    }
    assert lane.requires_materialized_local_recall([materialized]) is False
    suggestion = lane.suggested_decision_for_group([materialized], {}, {})
    assert suggestion[0] == "keep_me", suggestion
    normalized = apply.normalize_decision({**materialized, "decision": "keep_me"})
    assert not normalized.get("_invalid"), normalized
    assert apply.obsolete_audit_only_local_recall_keep({**materialized, "decision": "keep_me"}) is False

    workspace_apply = load_module(
        "apply-review-workspace-decisions.py",
        "murmurmark_apply_workspace_materialization",
    )
    template_row = {
        **raw,
        "session_id": "session",
        "cluster_id": "local_1",
        "interval": {"start": 1.0, "end": 2.0},
        "decision": "todo",
        "status": "todo",
    }
    stale_keep = {**template_row, "decision": "keep_me", "status": "reviewed"}
    unrelated_review = {
        "session_id": "session",
        "cluster_id": "audio_1",
        "interval": {"start": 3.0, "end": 4.0},
        "label": "timing_overlap",
        "source": "audio_review",
        "decision": "keep_me",
        "status": "reviewed",
        "utterance_ids": ["utt_1"],
    }
    merged = workspace_apply.merge_existing([template_row], [stale_keep, unrelated_review])
    assert merged[0]["decision"] == "todo", merged
    assert any(row.get("cluster_id") == "audio_1" for row in merged), merged

    merge_modules = [
        workspace_apply,
        lane,
        load_module("build-review-workspace.py", "murmurmark_build_workspace_fresh_todo"),
        load_module("apply-review-lane-pack-decisions.py", "murmurmark_apply_lane_fresh_todo"),
        load_module("report-review-decisions-progress.py", "murmurmark_review_progress_fresh_todo"),
        load_module("review-decisions-cli.py", "murmurmark_review_cli_fresh_todo"),
    ]
    fresh_template = {
        "session_id": "session",
        "cluster_id": "fresh_1",
        "interval": {"start": 5.0, "end": 6.0},
        "label": "timing_overlap",
        "source": "audio_review",
        "source_audit_id": "fresh_audit_id",
        "decision": "todo",
        "status": "todo",
        "utterance_ids": ["utt_2"],
    }
    stale_todo = {**fresh_template, "source_audit_id": "stale_audit_id"}
    reviewed = {
        **stale_todo,
        "decision": "keep_me",
        "status": "reviewed",
        "reviewer": "test",
    }
    for merge_module in merge_modules:
        refreshed = merge_module.merge_existing([fresh_template], [stale_todo])
        assert refreshed[0]["source_audit_id"] == "fresh_audit_id", (
            merge_module.__name__,
            refreshed,
        )
        preserved = merge_module.merge_existing([fresh_template], [reviewed])
        assert preserved[0]["decision"] == "keep_me", (merge_module.__name__, preserved)
        assert preserved[0]["reviewer"] == "test", (merge_module.__name__, preserved)

    with tempfile.TemporaryDirectory(prefix="murmurmark-empty-review-scope-") as temp_dir:
        empty_template = Path(temp_dir) / "review_decisions.template.jsonl"
        empty_template.write_text("", encoding="utf-8")
        template_rows, template_path = apply.template_for_session(
            SimpleNamespace(review_template=empty_template, decisions=Path(temp_dir) / "review_decisions.jsonl"),
            Path(temp_dir) / "session",
        )
        assert template_rows == [], template_rows
        assert template_path == empty_template, template_path
        coverage = apply.review_coverage([stale_todo], [], empty_template, False)
        assert coverage["status"] == "complete_empty_scope", coverage
        assert coverage["complete"] is True, coverage
        assert coverage["allowed"] is True, coverage
        missing_template = Path(temp_dir) / "missing.template.jsonl"
        missing_coverage = apply.review_coverage([stale_todo], [], missing_template, False)
        assert missing_coverage["status"] == "missing_template_scope", missing_coverage
        assert missing_coverage["allowed"] is False, missing_coverage

    quality = load_module("report-session-quality.py", "murmurmark_report_materialization")
    with tempfile.TemporaryDirectory(prefix="murmurmark-review-materialization-") as temp_dir:
        session = Path(temp_dir) / "session"
        repair_dir = session / "derived/transcript-simple/whisper-cpp/local-recall-repair"
        resolved_dir = session / "derived/transcript-simple/whisper-cpp/resolved"
        review_dir = session / "derived/transcript-simple/whisper-cpp/review-decisions"
        audit_dir = session / "derived/audit/local-recall"
        repair_dir.mkdir(parents=True)
        resolved_dir.mkdir(parents=True)
        review_dir.mkdir(parents=True)
        audit_dir.mkdir(parents=True)
        patch = {
            "status": "applied",
            "source_item_id": "lost_1",
            "utterance": {
                "id": "repair_1",
                "start": 10.0,
                "end": 12.0,
                "quality": {"needs_review": True},
                "source": {"kind": "local_recall_repair"},
            },
        }
        (repair_dir / "local_recall_repair_patches.local_recall_repair_v1.jsonl").write_text(
            json.dumps(patch) + "\n",
            encoding="utf-8",
        )
        (audit_dir / "local_recall_items.jsonl").write_text(
            json.dumps({"item_id": "lost_1", "label": "possible_lost_me", "duration_sec": 2.0}) + "\n",
            encoding="utf-8",
        )
        dialogue_path = resolved_dir / "clean_dialogue.agent_reviewed_v1.json"
        dialogue_path.write_text(json.dumps({"utterances": [patch["utterance"]]}), encoding="utf-8")
        (review_dir / "review_decisions_report.agent_reviewed_v1.json").write_text(
            json.dumps({"input_profile": "local_recall_repair_v1"}),
            encoding="utf-8",
        )
        base_metrics = {
            "local_recall_possible_lost_me_count": 1,
            "local_recall_possible_lost_me_seconds": 2.0,
            "local_recall_needs_review_count": 1,
            "local_recall_needs_review_seconds": 0.6,
            "local_recall_meaningful_review_seconds": 2.6,
        }
        repair_report = {"summary": {"applied_repairs": 1}, "gates": {"passed": True}}
        review_report = {"input_profile": "local_recall_repair_v1"}
        reconciled = quality.reconcile_materialized_local_recall(
            base_metrics,
            session,
            "agent_reviewed_v1",
            repair_report,
            review_report,
        )
        assert reconciled["local_recall_possible_lost_me_count"] == 0, reconciled
        assert reconciled["local_recall_needs_review_count"] == 2, reconciled
        assert reconciled["local_recall_repair_open_items"] == 1, reconciled
        assert reconciled["local_recall_meaningful_review_seconds"] == 2.6, reconciled
        assert quality.non_actionable_review_blockers(
            {
                "use_gate": "review_first",
                "review_blockers": ["risk:local_recall_possible_lost_me"],
                "pipeline_status": "complete",
                "review_scope_complete": True,
                "review_scope_remaining_seconds": 0.0,
                **reconciled,
            }
        ) == []

        closed = dict(patch["utterance"])
        closed["quality"] = {"needs_review": False}
        dialogue_path.write_text(json.dumps({"utterances": [closed]}), encoding="utf-8")
        reconciled = quality.reconcile_materialized_local_recall(
            base_metrics,
            session,
            "agent_reviewed_v1",
            repair_report,
            review_report,
        )
        assert reconciled["local_recall_repair_open_items"] == 0, reconciled
        assert reconciled["local_recall_repair_closed_items"] == 1, reconciled
        assert reconciled["local_recall_meaningful_review_seconds"] == 0.6, reconciled

    print("review materialization guard checks ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
