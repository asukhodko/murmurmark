#!/usr/bin/env python3
from __future__ import annotations

import json
import importlib.util
import sys
import tempfile
from pathlib import Path

import review_profile_lineage as lineage


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="murmurmark-review-lineage-") as root_value:
        session = Path(root_value) / "session"
        resolved = session / "derived/transcript-simple/whisper-cpp/resolved"
        dialogue = resolved / "clean_dialogue.audit_cleanup_v2.json"
        quality = resolved / "quality_report.audit_cleanup_v2.json"
        write_json(dialogue, {"utterances": [{"id": "utt_1", "text": "hello"}]})
        write_json(quality, {"utterances": 1})

        report = {
            "lineage": lineage.build_lineage(
                session=session,
                input_profile="audit_cleanup_v2",
                output_profile="reviewed_v1",
                dialogue_path=dialogue,
                quality_path=quality,
                previous_report=None,
            )
        }
        assert lineage.review_profile_is_current(session, report)

        write_json(dialogue, {"utterances": []})
        assert not lineage.review_profile_is_current(session, report)

        legacy = {"schema": "murmurmark.review_decisions_report/v1"}
        assert lineage.review_profile_is_current(session, legacy)
        write_json(
            resolved / "transcribe_simple_report.json",
            {
                "schema": "murmurmark.transcribe_simple_report/v1",
                "generator": {"name": "transcribe-simple-whispercpp", "version": "0.3.1"},
            },
        )
        assert not lineage.review_profile_is_current(session, legacy)

        write_json(dialogue, {"utterances": [{"id": "utt_1", "text": "hello"}]})
        current_report = {
            "lineage": lineage.build_lineage(
                session=session,
                input_profile="audit_cleanup_v2",
                output_profile="reviewed_v1",
                dialogue_path=dialogue,
                quality_path=quality,
                previous_report=None,
            )
        }
        carried = lineage.build_lineage(
            session=session,
            input_profile="reviewed_v1",
            output_profile="reviewed_v1",
            dialogue_path=resolved / "clean_dialogue.reviewed_v1.json",
            quality_path=resolved / "quality_report.reviewed_v1.json",
            previous_report=current_report,
        )
        assert carried == current_report["lineage"]

        workspace_path = Path(__file__).with_name("build-review-workspace.py")
        spec = importlib.util.spec_from_file_location("build_review_workspace", workspace_path)
        assert spec and spec.loader
        workspace = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = workspace
        spec.loader.exec_module(workspace)
        old = {
            "session_id": "session",
            "source": "transcript_text",
            "review_action": "check_transcript_text",
            "label": "transcript_text_needs_review",
            "utterance_ids": ["utt_000004"],
            "interval": {"start": 18.02, "end": 27.66},
            "text": [{"source_track": "remote", "text": "Same speech"}],
            "decision": "needs_review",
            "status": "reviewed",
        }
        renumbered = {
            **old,
            "utterance_ids": ["utt_000001"],
            "decision": "todo",
            "status": "todo",
            "allowed_decisions": ["needs_review", "skip"],
        }
        migrated = workspace.merge_existing([renumbered], [old])
        assert migrated[0]["decision"] == "needs_review"
        changed = {**renumbered, "text": [{"source_track": "remote", "text": "Changed speech"}]}
        assert workspace.merge_existing([changed], [old])[0]["decision"] == "todo"

        apply_workspace_path = Path(__file__).with_name("apply-review-workspace-decisions.py")
        apply_spec = importlib.util.spec_from_file_location("apply_review_workspace", apply_workspace_path)
        assert apply_spec and apply_spec.loader
        apply_workspace = importlib.util.module_from_spec(apply_spec)
        sys.modules[apply_spec.name] = apply_workspace
        apply_spec.loader.exec_module(apply_workspace)
        migrated = apply_workspace.merge_existing([renumbered], [old])
        assert len(migrated) == 1
        assert migrated[0]["decision"] == "needs_review"

        cross_source_old = {
            **old,
            "source": "audio_review",
            "review_action": "confirm_drop_or_keep_me",
            "label": "uncertain",
            "decision": "keep_me",
        }
        cross_source_new = {
            **renumbered,
            "source": "transcript_text",
            "review_action": "check_transcript_text",
            "label": "transcript_text_needs_review",
            "decision": "todo",
            "allowed_decisions": ["drop_me", "keep_me", "needs_review", "skip"],
        }
        assert workspace.merge_existing([cross_source_new], [cross_source_old])[0]["decision"] == "keep_me"
        assert apply_workspace.merge_existing([cross_source_new], [cross_source_old])[0]["decision"] == "keep_me"

        apply_review_path = Path(__file__).with_name("apply-review-decisions.py")
        review_spec = importlib.util.spec_from_file_location("apply_review_decisions", apply_review_path)
        assert review_spec and review_spec.loader
        apply_review = importlib.util.module_from_spec(review_spec)
        sys.modules[review_spec.name] = apply_review
        review_spec.loader.exec_module(apply_review)
        reviewed_dialogue = resolved / "clean_dialogue.reviewed_v1.json"
        reviewed_quality = resolved / "quality_report.reviewed_v1.json"
        write_json(reviewed_dialogue, {"utterances": []})
        write_json(reviewed_quality, {"utterances": 0})
        review_dir = session / "derived/transcript-simple/whisper-cpp/review-decisions"
        write_json(
            review_dir / "review_decisions_report.reviewed_v1.json",
            {
                "input_profile": "reviewed_v1",
                "output_profile": "reviewed_v1",
                "gates": {"passed": True},
            },
        )
        chosen = apply_review.review_input_profile(
            session,
            "reviewed_v1",
            [{"input_profile": "reviewed_v1"}],
            [{"input_profile": "reviewed_v1"}],
        )
        assert chosen == "audit_cleanup_v2"

        residual_row = {
            "session_id": "session",
            "source_audit_id": "transcript_text:utt_2",
            "utterance_ids": ["utt_2"],
            "interval": {"start": 2.0, "end": 3.0, "duration_sec": 1.0},
            "label": "transcript_text_needs_review",
            "decision": "todo",
        }
        residual_template = session / "derived/readiness/review-plan/review_decisions.template.jsonl"
        residual_template.parent.mkdir(parents=True, exist_ok=True)
        residual_template.write_text(json.dumps(residual_row) + "\n", encoding="utf-8")
        coverage = apply_review.review_coverage(
            [residual_row],
            [residual_row],
            residual_template,
            allow_partial_review=True,
        )
        assert coverage["status"] == "incomplete"
        assert coverage["allowed"] is False
        promoted = apply_review.allow_compatible_partial_coverage(
            coverage,
            compatible_decision_rows=2,
            compatible_applied_rows=2,
            invalid_decision_rows=0,
            rejected_decision_rows=0,
            conflict_count=0,
        )
        assert promoted["status"] == "partial_allowed_from_compatible_decisions"
        assert promoted["allowed"] is True
        assert promoted["partial_allowed"] is True
        blocked = apply_review.allow_compatible_partial_coverage(
            coverage,
            compatible_decision_rows=2,
            compatible_applied_rows=2,
            invalid_decision_rows=0,
            rejected_decision_rows=1,
            conflict_count=0,
        )
        assert blocked["allowed"] is False

    print("review profile lineage checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
