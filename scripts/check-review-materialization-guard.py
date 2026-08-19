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
    with tempfile.TemporaryDirectory(prefix="murmurmark-cumulative-review-") as raw_root:
        session_path = Path(raw_root)
        decisions_path = session_path / "derived/readiness/review-plan/review_decisions.jsonl"
        decisions_path.parent.mkdir(parents=True)
        decisions_path.write_text(
            json.dumps(
                {
                    "input_profile": "audit_cleanup_v2",
                    "source": "transcript_order",
                    "source_audit_id": "order_0001",
                    "status": "reviewed",
                    "decision": "keep_me",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        assert readiness.review_resolved_transcript_order_ids(session_path, "reviewed_v1") == {"order_0001"}
        assert readiness.review_resolved_transcript_order_ids(session_path, "audit_cleanup_v1") == set()
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

    unstable_music = {
        "id": "utt_micro_music",
        "start": 172.19,
        "end": 172.64,
        "role": "me",
        "text": "ВООДУШЕВЛЯЮЩАЯ МУЗЫКА",
        "quality": {
            "needs_review": False,
            "repair": {
                "action": "micro_reasr",
                "island_start_ms": 172190,
                "island_end_ms": 172640,
                "micro_reasr": {
                    "status": "ok",
                    "source_label": "clean_local_fir",
                    "raw_text": "ВООДУШЕВЛЯЮЩАЯ МУЗЫКА",
                    "attempts": [
                        {
                            "status": "ok",
                            "source_label": "clean_local_fir",
                            "raw_text": "ВООДУШЕВЛЯЮЩАЯ МУЗЫКА",
                        },
                        {
                            "status": "ok",
                            "source_label": "role_masked_for_asr",
                            "raw_text": "ВООДУШЕВЛЯЮЩАЯ МУЗЫКА",
                        },
                        {"status": "ok", "source_label": "raw_for_asr", "raw_text": "Вот."},
                    ],
                },
            },
        },
    }
    music_review = readiness.unstable_successful_micro_asr(unstable_music)
    assert music_review is not None
    assert "implausible_short_island_speech_rate" in music_review["reasons"]
    assert "short_island_source_disagreement" in music_review["reasons"]
    music_row = readiness.compact_transcript_text_utterance(
        {"session_id": "fixture", "session": "/tmp/fixture"},
        unstable_music,
        input_profile="reviewed_v1",
        micro_selection_review=music_review,
    )
    assert "drop_me" in music_row["allowed_decisions"]
    assert music_row["review_features"]["unstable_micro_asr_success"] is True
    music_snapshot = lane.row_feature_snapshot(music_row)
    assert music_snapshot["unstable_micro_asr_success"] is True
    assert "short_island_source_disagreement" in music_snapshot["micro_asr_selection_review_reasons"]
    no_audio_decision = lane.suggested_decision_for_group([music_row], {}, {})
    assert no_audio_decision[0] == "needs_review", no_audio_decision
    assert "independent audio-judge" in no_audio_decision[2], no_audio_decision

    unstable_baseline = json.loads(json.dumps(unstable_music))
    unstable_baseline["id"] = "utt_micro_baseline"
    unstable_baseline["start"] = 648.2
    unstable_baseline["end"] = 649.04
    unstable_baseline["text"] = "Тарак подал крылья."
    baseline_repair = unstable_baseline["quality"]["repair"]
    baseline_repair["island_start_ms"] = 648200
    baseline_repair["island_end_ms"] = 649040
    baseline_micro = baseline_repair["micro_reasr"]
    baseline_micro["source_label"] = "current_clean_local_fir"
    baseline_micro["raw_text"] = "Тарак подал крылья."
    baseline_micro["attempts"] = [
        {
            "status": "ok",
            "source_label": "current_clean_local_fir",
            "rows": [{"text": "Тарак подал крылья."}],
        },
        {"status": "ok", "source_label": "clean_local_fir", "raw_text": "Да."},
        {"status": "ok", "source_label": "role_masked_for_asr", "raw_text": "Да."},
        {"status": "ok", "source_label": "raw_for_asr", "raw_text": "Так."},
    ]
    baseline_review = readiness.unstable_successful_micro_asr(unstable_baseline)
    assert baseline_review is not None
    assert "baseline_only_selection_without_canonical_support" in baseline_review["reasons"]
    assert "short_island_source_disagreement" in baseline_review["reasons"]

    stable_micro = json.loads(json.dumps(unstable_baseline))
    stable_micro["id"] = "utt_micro_stable"
    stable_micro["start"] = 10.0
    stable_micro["end"] = 10.7
    stable_micro["text"] = "Да."
    stable_repair = stable_micro["quality"]["repair"]
    stable_repair["island_start_ms"] = 10000
    stable_repair["island_end_ms"] = 10700
    stable_meta = stable_repair["micro_reasr"]
    stable_meta["source_label"] = "current_clean_local_fir"
    stable_meta["raw_text"] = "Да."
    stable_meta["attempts"] = [
        {
            "status": "ok",
            "source_label": source,
            "raw_text": "Да.",
        }
        for source in ("clean_local_fir", "raw_for_asr", "role_masked_for_asr")
    ]
    assert readiness.unstable_successful_micro_asr(stable_micro) is None

    unstable_remote_item = {
        "source_reasons": ["review_lane:check_transcript_text", "transcript_text_needs_review"],
        "review_features": music_row["review_features"],
        "utterances": [
            {
                "id": "utt_micro_music",
                "role": "me",
                "source_track": "mic",
                "start": 172.19,
                "end": 172.64,
                "text": "ВООДУШЕВЛЯЮЩАЯ МУЗЫКА",
            }
        ],
    }
    remote_transcripts = {
        "mic_clean": {"text": "только колобок мы просто переехали", "segments": [], "no_speech_prob": 0.2},
        "remote": {"text": "только колобок мы просто переехали", "segments": [], "no_speech_prob": 0.1},
    }
    remote_metrics = stronger.source_metrics(
        remote_transcripts,
        "ВООДУШЕВЛЯЮЩАЯ МУЗЫКА",
        "только колобок мы просто переехали",
    )
    remote_artifact = stronger.classify_item(
        unstable_remote_item,
        None,
        remote_transcripts,
        remote_metrics,
        {
            "coverage_ratio": 1.0,
            "silence_ratio": 0.0,
            "local_active_ratio": 0.0,
            "remote_only_ratio": 1.0,
            "double_talk_ratio": 0.0,
        },
    )
    assert remote_artifact["label"] == "confirm_remote_duplicate", remote_artifact
    assert remote_artifact["confidence"] >= 0.88, remote_artifact
    assert remote_artifact["scores"]["unstable_micro_remote_artifact"] is True

    local_transcripts = {
        "mic_clean": {"text": "ну что тогда побежали", "segments": [], "no_speech_prob": 0.1},
        "remote": {"text": "", "segments": [], "no_speech_prob": 0.9},
    }
    local_metrics = stronger.source_metrics(local_transcripts, "Ну что, тогда побежали.", "")
    local_item = json.loads(json.dumps(unstable_remote_item))
    local_item["utterances"][0]["text"] = "Ну что, тогда побежали."
    local_result = stronger.classify_item(
        local_item,
        None,
        local_transcripts,
        local_metrics,
        {
            "coverage_ratio": 1.0,
            "silence_ratio": 0.0,
            "local_active_ratio": 1.0,
            "remote_only_ratio": 0.0,
            "double_talk_ratio": 0.0,
        },
    )
    assert local_result["label"] == "confirm_me", local_result

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
        "session_id": "fixture",
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

    false_candidate_judge = {
        "id": "fwj_false_local_recall",
        "source_pack_item_id": "local_recall_0001",
        "session_id": "fixture",
        "utterance_ids": ["live_candidate_1"],
        "interval": {"start": 0.0, "end": 3.0},
        "classification": {"label": "confirm_remote_duplicate", "confidence": 0.95},
    }
    suggestion = lane.suggested_decision_for_group([raw], {"fixture": [false_candidate_judge]}, {})
    assert suggestion[0] == "skip", suggestion
    assert "false local-recall candidate" in suggestion[2], suggestion
    false_candidate_item = {
        **raw,
        "suggested_decision": suggestion[0],
        "suggested_decision_confidence": suggestion[1],
        "suggested_decision_reason": suggestion[2],
    }
    assert lane.answer_for_item(false_candidate_item, suggested=True) == "s", false_candidate_item
    deferred_skip = {**false_candidate_item, "suggested_decision_reason": "leave for later"}
    assert lane.answer_for_item(deferred_skip, suggested=True) == ".", deferred_skip

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
        "classification": {
            "label": "confirm_timing_or_doubletalk",
            "confidence": 0.92,
            "scores": {
                "best_me_similarity": 0.95,
                "speaker_state_local_active_ratio": 1.0,
                "speaker_state_double_talk_ratio": 1.0,
                "mic_content_tokens": 8,
            },
        },
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
    assert suggestion[0] == "keep_me", suggestion

    target_remote_like = {
        **target_absent,
        "id": "tme_voice_remote_like",
        "classification": {"label": "target_me_absent_remote_like", "confidence": 0.90},
        "impact": {"category": "new_drop_evidence"},
    }
    suggestion = lane.suggested_decision_for_group(
        [audio_row],
        {"fixture": [stronger_keep]},
        {"fixture": [target_remote_like]},
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
            {
                "id": "utt_pending_me",
                "start": 5.0,
                "end": 6.5,
                "role": "me",
                "source_track": "mic",
                "text": "Это содержательная локальная реплика.",
                "quality": {"needs_review": True, "renumbered_from": "utt_pending_me_old"},
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
        local_recall_skip = {
            "schema": "murmurmark.review_decision/v1",
            "session_id": session.name,
            "input_profile": "reviewed_v1",
            "source": "local_recall",
            "source_audit_id": "local_recall_0001",
            "cluster_id": "local_recall_cluster",
            "label": "lost_me",
            "review_action": "check_local_recall",
            "decision": "skip",
            "status": "reviewed",
            "utterance_ids": ["audit_only_candidate"],
            "interval": {"start": 6.0, "end": 7.0, "duration_sec": 1.0},
            "text": [],
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
            "\n".join(
                json.dumps(row, ensure_ascii=False)
                for row in (order_row, audio_row, local_recall_skip, stale_audio_row)
            )
            + "\n",
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
        assert reviewed_by_id["utt_pending_me"]["quality"]["needs_review"] is True, reviewed_by_id["utt_pending_me"]
        report = json.loads(
            (
                session
                / "derived/transcript-simple/whisper-cpp/review-decisions/review_decisions_report.reviewed_v1.json"
            ).read_text(encoding="utf-8")
        )
        assert report["input_profile"] == "audit_cleanup_v2", report
        assert report["coverage"]["complete"] is True, report
        assert report["summary"]["applied_decision_rows"] == 3, report
        assert report["summary"]["compatible_out_of_scope_decision_rows"] == 2, report
        assert report["summary"]["ignored_out_of_scope_decision_rows"] == 1, report
        assert "compatible_out_of_scope_review_decisions_applied" in report["gates"]["warnings"], report
        assert "out_of_scope_review_decisions_ignored" in report["gates"]["warnings"], report

        mandatory, _ = readiness.build_review_queue_details(
            [
                {
                    "session_id": session.name,
                    "session": str(session),
                    "selected_profile": "reviewed_v1",
                    "use_gate": "ready_for_notes",
                    "export_blockers": ["full_transcript_needs_review_required"],
                    "transcript_review_burden_sec": 0.0,
                }
            ],
            40,
        )
        pending_rows = [
            row
            for row in mandatory
            if row.get("source") == "transcript_text"
            and row.get("utterance_ids") == ["utt_pending_me"]
        ]
        assert len(pending_rows) == 1, mandatory
        pending_row = pending_rows[0]
        assert pending_row["input_profile"] == "reviewed_v1", pending_row
        assert pending_row["review_features"]["renumbered_from"] == "utt_pending_me_old", pending_row

        pending_decision = {
            **pending_row,
            "schema": "murmurmark.review_decision/v1",
            "decision": "keep_me",
            "status": "reviewed",
        }
        pending_template_row = {**pending_decision, "decision": "todo", "status": "todo"}
        pending_decisions = Path(temp_dir) / "pending_review_decisions.jsonl"
        pending_template = Path(temp_dir) / "pending_review_decisions.template.jsonl"
        pending_decisions.write_text(
            json.dumps(pending_decision, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        pending_template.write_text(
            json.dumps(pending_template_row, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        reapplied = subprocess.run(
            [
                sys.executable,
                str(Path(apply.__file__)),
                str(session),
                "--decisions",
                str(pending_decisions),
                "--review-template",
                str(pending_template),
                "--input-profile",
                "auto",
                "--output-profile",
                "reviewed_v1",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        assert reapplied.returncode == 0, (reapplied.stdout, reapplied.stderr)
        reapplied_dialogue = json.loads(
            (resolved / "clean_dialogue.reviewed_v1.json").read_text(encoding="utf-8")
        )
        reapplied_by_id = {row["id"]: row for row in reapplied_dialogue["utterances"]}
        assert reapplied_by_id["utt_pending_me"]["quality"]["needs_review"] is False, reapplied_by_id["utt_pending_me"]
        reapplied_report = json.loads(
            (
                session
                / "derived/transcript-simple/whisper-cpp/review-decisions/review_decisions_report.reviewed_v1.json"
            ).read_text(encoding="utf-8")
        )
        assert reapplied_report["input_profile"] == "reviewed_v1", reapplied_report

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

        harmful_metrics = {
            "audit_harmful_seconds_after": 2.03,
            "remote_duplicate_in_me_seconds": 0.0,
            "audio_review_remote_leak_probable_error_seconds": 0.0,
            "audio_review_probable_error_seconds": 0.0,
            "remote_forbidden_status": "ok",
        }
        assert outcome.harmful_remote_evidence(
            harmful_metrics, "audit_cleanup_v2"
        )["seconds"] == 2.03
        reviewed_harmful = outcome.harmful_remote_evidence(
            harmful_metrics, "reviewed_v1"
        )
        assert reviewed_harmful["seconds"] == 0.0, reviewed_harmful

        summary = outcome.build_outcome_summary(
            outcome="review_first",
            export_status="blocked_until_review",
            next_command="murmurmark status fixture",
            readiness=explained_readiness,
            metrics={"review_burden_sec": 0.0},
            gates=[],
            review_plan=outcome_plan,
            outputs={},
            speaker={"state": "fallback"},
        )
        assert "no actionable review queue remains" in summary["headline"], summary

    print("review materialization guard checks ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
