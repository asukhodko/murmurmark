#!/usr/bin/env python3
"""Regression checks for audit-only versus materialized local-recall review rows."""

from __future__ import annotations

import importlib.util
import json
import subprocess
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
    outcome = load_module("evaluate-outcome.py", "murmurmark_evaluate_outcome_materialization")
    readiness = load_module(
        "report-operational-readiness.py",
        "murmurmark_operational_readiness_micro_fallback",
    )
    stronger = load_module(
        "audit-stronger-audio-judge.py",
        "murmurmark_stronger_audio_micro_fallback",
    )

    swift_source = (
        Path(__file__).parents[1] / "Sources/MurmurMarkCLI/MurmurMarkCLI.swift"
    ).read_text(encoding="utf-8")
    assert "private static func convergeSuggestedReview(" in swift_source
    assert "let maxAdditionalPasses = 7" in swift_source
    assert 'let closed = closure["closed_by_suggestions"]' in swift_source
    assert 'lanePack.lastPathComponent.contains("check_transcript_text")' in swift_source

    unsupported_fallback = {
        "id": "utt_micro_empty",
        "start": 0.0,
        "end": 6.5,
        "role": "me",
        "text": "Пар",
        "quality": {
            "needs_review": True,
            "role_confidence": 0.55,
            "repair": {
                "micro_reasr": {
                    "status": "failed",
                    "reason": "empty_micro_text",
                    "attempts": [
                        {"status": "failed", "reason": "empty_micro_text", "source_label": source}
                        for source in ("clean_local_fir", "raw_for_asr", "role_masked_for_asr")
                    ],
                }
            },
        },
    }
    assert readiness.unsupported_micro_asr_fallback(unsupported_fallback) is True
    incomplete_evidence = json.loads(json.dumps(unsupported_fallback))
    incomplete_evidence["quality"]["repair"]["micro_reasr"]["attempts"].pop()
    assert readiness.unsupported_micro_asr_fallback(incomplete_evidence) is False
    transcript_text_row = readiness.compact_transcript_text_utterance(
        {"session_id": "fixture", "session": "/tmp/fixture"},
        unsupported_fallback,
        input_profile="reviewed_v1",
        allow_drop=True,
    )
    assert transcript_text_row["input_profile"] == "reviewed_v1"
    assert transcript_text_row["review_lane"] == "check_transcript_text"
    assert "drop_me" in transcript_text_row["allowed_decisions"]
    assert transcript_text_row["review_features"]["unsupported_micro_asr_fallback"] is True
    snapshot = lane.row_feature_snapshot(transcript_text_row)
    assert snapshot["unsupported_micro_asr_fallback"] is True
    assert snapshot["micro_asr_reason"] == "empty_micro_text"
    judge_item = {
        "source_reasons": ["review_lane:check_transcript_text", "transcript_text_needs_review"],
        "review_features": snapshot,
        "utterances": [
            {
                "id": "utt_micro_empty",
                "role": "me",
                "source_track": "mic",
                "start": 0.0,
                "end": 6.5,
                "text": "Пар",
            }
        ],
    }
    hallucination_result = {
        "text": "Продолжение следует...",
        "segments": [],
        "no_speech_prob": 0.75,
    }
    transcripts = {
        source: dict(hallucination_result)
        for source in ("mic_role_masked", "mic_clean", "mic_raw", "remote")
    }
    metrics = stronger.source_metrics(transcripts, "Пар", "")
    classification = stronger.classify_item(
        judge_item,
        None,
        transcripts,
        metrics,
        {
            "coverage_ratio": 1.0,
            "silence_ratio": 1.0,
            "local_active_ratio": 0.0,
            "remote_only_ratio": 0.0,
            "double_talk_ratio": 0.0,
        },
    )
    assert classification["label"] == "confirm_asr_noise", classification
    assert classification["confidence"] >= 0.90, classification
    assert classification["scores"]["mic_sources_empty_or_hallucinated"] is True
    unmarked_item = json.loads(json.dumps(judge_item))
    unmarked_item["review_features"] = {}
    classification = stronger.classify_item(
        unmarked_item,
        None,
        transcripts,
        metrics,
        {
            "coverage_ratio": 1.0,
            "silence_ratio": 1.0,
            "local_active_ratio": 0.0,
            "remote_only_ratio": 0.0,
            "double_talk_ratio": 0.0,
        },
    )
    assert classification["label"] == "uncertain", classification
    stronger_noise = {
        "id": "fwj_micro_empty",
        "source_pack_item_id": transcript_text_row["source_audit_id"],
        "session_id": "fixture",
        "utterance_ids": ["utt_micro_empty"],
        "interval": {"start": 0.0, "end": 6.5},
        "classification": {"label": "confirm_asr_noise", "confidence": 0.92},
    }
    suggestion = lane.suggested_decision_for_group(
        [transcript_text_row],
        {"fixture": [stronger_noise]},
        {},
    )
    assert suggestion[0] == "drop_me", suggestion

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

    audio_row = {
        "session_id": "fixture",
        "source": "audio_review",
        "source_audit_id": "arp_voice_conflict",
        "review_lane": "classify_audio",
        "allowed_decisions": ["drop_me", "keep_me", "needs_review", "skip"],
        "utterance_ids": ["utt_voice_me", "utt_voice_remote"],
        "me_utterance_ids": ["utt_voice_me"],
        "remote_utterance_ids": ["utt_voice_remote"],
        "interval": {"start": 10.0, "end": 12.0},
    }
    stronger_keep = {
        "id": "fwj_voice_conflict",
        "source_pack_item_id": "arp_voice_conflict",
        "session_id": "fixture",
        "utterance_ids": ["utt_voice_me", "utt_voice_remote"],
        "interval": {"start": 10.0, "end": 12.0},
        "classification": {"label": "confirm_timing_or_doubletalk", "confidence": 0.92},
    }
    target_absent = {
        "id": "tme_voice_conflict",
        "source_pack_item_id": "arp_voice_conflict",
        "session_id": "fixture",
        "utterance_ids": ["utt_voice_me", "utt_voice_remote"],
        "interval": {"start": 10.0, "end": 12.0},
        "classification": {"label": "target_me_absent", "confidence": 0.70},
        "impact": {"category": "not_actionable"},
    }
    suggestion = lane.suggested_decision_for_group(
        [audio_row],
        {"fixture": [stronger_keep]},
        {"fixture": [target_absent]},
    )
    assert suggestion[0] == "needs_review", suggestion
    assert "does not confirm the local speaker" in suggestion[2], suggestion

    target_confirmed = {
        **target_absent,
        "id": "tme_voice_confirmed",
        "classification": {"label": "target_me_confirmed", "confidence": 0.94},
        "impact": {"category": "new_keep_evidence"},
    }
    suggestion = lane.suggested_decision_for_group(
        [audio_row],
        {"fixture": [stronger_keep]},
        {"fixture": [target_confirmed]},
    )
    assert suggestion[0] == "keep_me", suggestion

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
    plan = load_module("build-review-plan.py", "murmurmark_review_plan_local_recall_choices")
    assert plan.output_allowed_decisions(raw) == ["needs_review", "skip"]
    assert plan.output_allowed_decisions(materialized) == raw["allowed_decisions"]
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
        "review_suggested_decision": "keep_me",
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
        assert preserved[0]["review_suggested_decision"] == "keep_me", (
            merge_module.__name__,
            preserved,
        )
        assert preserved[0]["source_audit_id"] == "fresh_audit_id", (
            merge_module.__name__,
            preserved,
        )
        assert preserved[0]["interval"] == fresh_template["interval"], (
            merge_module.__name__,
            preserved,
        )

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

    with tempfile.TemporaryDirectory(prefix="murmurmark-cumulative-review-") as temp_dir:
        session = Path(temp_dir) / "cumulative-review-session"
        resolved = session / "derived/transcript-simple/whisper-cpp/resolved"
        resolved.mkdir(parents=True)
        utterances = [
            {
                "id": "utt_order_me",
                "start": 1.0,
                "end": 2.0,
                "role": "me",
                "source_track": "mic",
                "text": "Проверяю порядок.",
                "quality": {"needs_review": True},
            },
            {
                "id": "utt_order_remote",
                "start": 1.5,
                "end": 2.5,
                "role": "remote",
                "source_track": "remote",
                "text": "Порядок подтвержден.",
                "quality": {"needs_review": False},
            },
            {
                "id": "utt_audio_me",
                "start": 3.0,
                "end": 4.0,
                "role": "me",
                "source_track": "mic",
                "text": "Это моя реплика.",
                "quality": {"needs_review": True},
            },
        ]
        (resolved / "clean_dialogue.audit_cleanup_v2.json").write_text(
            json.dumps({"schema": "murmurmark.clean_dialogue/v1", "utterances": utterances}, ensure_ascii=False),
            encoding="utf-8",
        )
        (resolved / "quality_report.audit_cleanup_v2.json").write_text(
            json.dumps({"schema": "murmurmark.simple_transcript_quality/v1", "utterances": len(utterances)}),
            encoding="utf-8",
        )
        order_row = {
            "schema": "murmurmark.review_decision/v1",
            "session_id": session.name,
            "input_profile": "audit_cleanup_v2",
            "source": "transcript_order",
            "source_audit_id": "order_0001",
            "cluster_id": "order_cluster",
            "label": "probable_order_risk",
            "review_action": "check_transcript_order",
            "decision": "keep_me",
            "status": "reviewed",
            "me_utterance_ids": ["utt_order_me"],
            "remote_utterance_ids": ["utt_order_remote"],
            "utterance_ids": ["utt_order_me", "utt_order_remote"],
            "interval": {"start": 1.5, "end": 2.0, "duration_sec": 0.5},
            "text": [utterances[0], utterances[1]],
        }
        audio_row = {
            "schema": "murmurmark.review_decision/v1",
            "session_id": session.name,
            "input_profile": "reviewed_v1",
            "source": "audio_review",
            "source_audit_id": "arp_0001",
            "cluster_id": "audio_cluster",
            "label": "uncertain",
            "review_action": "classify_audio",
            "decision": "keep_me",
            "status": "reviewed",
            "me_utterance_ids": ["utt_audio_me"],
            "utterance_ids": ["utt_audio_me"],
            "interval": {"start": 3.0, "end": 4.0, "duration_sec": 1.0},
            "text": [utterances[2]],
        }
        stale_audio_row = {
            **audio_row,
            "source_audit_id": "arp_stale",
            "cluster_id": "stale_audio_cluster",
            "utterance_ids": ["utt_order_me"],
            "me_utterance_ids": ["utt_order_me"],
            "interval": {"start": 1.0, "end": 2.0, "duration_sec": 1.0},
            "text": [{**utterances[0], "text": "Старый текст реплики."}],
        }
        decisions = Path(temp_dir) / "review_decisions.jsonl"
        decisions.write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in (order_row, audio_row, stale_audio_row)) + "\n",
            encoding="utf-8",
        )
        template = Path(temp_dir) / "review_decisions.template.jsonl"
        template.write_text(
            json.dumps(
                {
                    **order_row,
                    "cluster_id": "regenerated_order_cluster",
                    "decision": "todo",
                    "status": "todo",
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        completed = subprocess.run(
            [
                sys.executable,
                str(Path(apply.__file__)),
                str(session),
                "--decisions",
                str(decisions),
                "--review-template",
                str(template),
                "--input-profile",
                "auto",
                "--output-profile",
                "reviewed_v1",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        assert completed.returncode == 0, (completed.stdout, completed.stderr)
        reviewed = json.loads((resolved / "clean_dialogue.reviewed_v1.json").read_text(encoding="utf-8"))
        reviewed_by_id = {row["id"]: row for row in reviewed["utterances"]}
        assert reviewed_by_id["utt_order_me"]["quality"]["needs_review"] is False, reviewed_by_id["utt_order_me"]
        assert reviewed_by_id["utt_audio_me"]["quality"]["needs_review"] is False, reviewed_by_id["utt_audio_me"]
        report = json.loads(
            (
                session
                / "derived/transcript-simple/whisper-cpp/review-decisions/review_decisions_report.reviewed_v1.json"
            ).read_text(encoding="utf-8")
        )
        assert report["input_profile"] == "audit_cleanup_v2", report
        assert report["coverage"]["complete"] is True, report
        assert report["summary"]["applied_decision_rows"] == 2, report
        assert report["summary"]["compatible_out_of_scope_decision_rows"] == 1, report
        assert report["summary"]["ignored_out_of_scope_decision_rows"] == 1, report
        assert "compatible_out_of_scope_review_decisions_applied" in report["gates"]["warnings"], report
        assert "out_of_scope_review_decisions_ignored" in report["gates"]["warnings"], report

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

        export_only_row = {
            "use_gate": "ready_for_notes",
            "review_blockers": [],
            "export_blockers": ["full_transcript_review_required"],
            "pipeline_status": "complete",
            "review_scope_complete": True,
            "review_scope_remaining_seconds": 0.0,
            "local_recall_repair_open_items": 0,
        }
        non_actionable = quality.non_actionable_review_blockers(export_only_row)
        assert len(non_actionable) == 1, non_actionable
        assert non_actionable[0]["blockers"] == ["full_transcript_review_required"], non_actionable
        next_commands = quality.readiness_next_commands(session, export_only_row)
        assert [item["id"] for item in next_commands] == [
            "status_session",
            "report_session",
            "open_readiness",
        ], next_commands
        assert not any("review workspace" in item["command"] for item in next_commands), next_commands

        explained_readiness = {
            **export_only_row,
            "verdict": "usable_with_review",
            "selected_profile": "reviewed_v1",
            "risk_flags": [],
            "outputs": {},
            "non_actionable_blockers": non_actionable,
            "metrics": {
                "local_only_island_recall": 0.74,
                "local_recall_recommended_next_step": "local_recall_risk_explained",
            },
        }
        gates = outcome.evaluate_gates(session, explained_readiness, {"status": "passed"})
        recall_gate = next(item for item in gates if item["id"] == "local_recall")
        assert recall_gate["status"] == "pass", recall_gate
        assert recall_gate["audit_explained"] is True, recall_gate
        outcome_plan = outcome.build_review_plan(session, explained_readiness, "ready_for_notes")
        assert outcome_plan["lanes"] == [], outcome_plan
        assert outcome_plan["summary"]["reason"] == "actionable_review_scope_exhausted", outcome_plan

    print("review materialization guard checks ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
