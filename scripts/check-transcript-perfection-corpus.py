#!/usr/bin/env python3
"""Smoke, integrity and determinism checks for Transcript Perfection Corpus v1."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
REPORTER = ROOT / "scripts/report-transcript-perfection-corpus.py"
DEFAULT_MANIFEST = ROOT / "docs/testing/transcript-perfection-corpus-v1-manifest.json"
DEFAULT_OUT = ROOT / "sessions/_reports/transcript-perfection-corpus-v1"


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source(path: Path, source_id: str, dimensions: list[str], schema: str | None = None, line_schema: str | None = None) -> dict[str, object]:
    row: dict[str, object] = {
        "bytes": path.stat().st_size,
        "dimensions": dimensions,
        "evidence_level": "synthetic_fixture",
        "id": source_id,
        "path": str(path.relative_to(ROOT)),
        "required": True,
        "sha256": sha256(path),
    }
    if schema:
        row["expected_schema"] = schema
    if line_schema:
        row["expected_line_schema"] = line_schema
    return row


def build_fixture(root: Path) -> Path:
    files = root / "sources"
    audio_baseline = files / "audio-baseline.json"
    write_json(
        audio_baseline,
        {
            "schema": "murmurmark.residual_audio_arbitration_baseline/v1",
            "queue": {"item_count": 2, "seconds": 5.0},
        },
    )
    recall_baseline = files / "recall-baseline.json"
    write_json(
        recall_baseline,
        {
            "schema": "murmurmark.residual_local_recall_baseline/v1",
            "excluded_queues": {"transcript_order": {"item_count": 2, "seconds": 4.0}},
        },
    )
    chronology = files / "chronology.jsonl"
    write_jsonl(
        chronology,
        [
            {
                "schema": "murmurmark.speaker_mode_risk/v1",
                "session_id": f"fixture-{index}",
                "interval": {"duration_sec": 2.0},
            }
            for index in range(2)
        ],
    )
    payloads: dict[str, tuple[Path, dict[str, object], list[str]]] = {
        "remote_speaker_coverage_v3": (
            files / "remote.json",
            {
                "schema": "murmurmark.remote_speaker_coverage_corpus_report/v3",
                "decision": "PROMOTE",
                "gates": {"all_word_conservation": True, "all_timestamp_order": True},
                "summary": {
                    "attributable_remote_speech_ratio": 0.75,
                    "attributed_speech_sec": 75.0,
                    "attributed_words": 75,
                    "remote_speech_sec": 100.0,
                    "remote_words": 100,
                    "sessions": 2,
                },
                "reference_evaluation": {
                    "attributed_only": {
                        "bcubed": {"f1": 0.96},
                        "pairwise": {"precision": 0.96},
                    }
                },
            },
            ["recognized_words", "remote_speaker_turns"],
        ),
        "remote_speaker_coverage_v3_manifest": (
            files / "remote-manifest.json",
            {"schema": "murmurmark.remote_speaker_coverage_frozen_manifest/v3", "decision": "PROMOTE"},
            ["recognized_words", "remote_speaker_turns"],
        ),
        "residual_audio_arbitration_v1": (
            files / "audio.json",
            {
                "schema": "murmurmark.residual_audio_arbitration_corpus_report/v1",
                "baseline_manifest_sha256": sha256(audio_baseline),
                "gates": {"scientifically_complete": True, "hard_failures": []},
                "summary": {
                    "frozen_queue_items": 2,
                    "frozen_queue_seconds": 5.0,
                    "remaining_items": 2,
                    "remaining_seconds": 5.0,
                },
                "sessions": [
                    {"summary": {"remaining_seconds": 2.0}},
                    {"summary": {"remaining_seconds": 3.0}},
                ],
            },
            ["me_remote_roles", "overlap", "remote_leakage"],
        ),
        "residual_local_recall_v1": (
            files / "recall.json",
            {
                "schema": "murmurmark.residual_local_recall_corpus_report/v1",
                "baseline_sha256": sha256(recall_baseline),
                "decision": "PROMOTE_RESIDUAL_LOCAL_RECALL_V1",
                "gates": {"passed": True},
                "summary": {"closed_items": 2, "remaining_items": 1, "remaining_seconds": 1.0},
                "sessions": [{"summary": {"remaining_seconds": 1.0}}],
            },
            ["missing_me"],
        ),
        "speaker_mode_profile_baseline": (
            files / "profile.json",
            {
                "schema": "murmurmark.speaker_mode_profile_baseline/v1",
                "queues": {"chronology": {"items": 2, "seconds": 4.0}},
            },
            ["chronology", "me_remote_roles", "overlap"],
        ),
        "acoustic_mode_corpus": (
            files / "acoustic.json",
            {
                "schema": "murmurmark.acoustic_mode_corpus_report/v1",
                "gates": {"passed": True},
                "summary": {
                    "session_count": 3,
                    "labeled_sessions": 2,
                    "labeled_matches": 2,
                    "by_mode": {"speaker_playback": 1, "headphones_or_low_leak": 1, "uncertain": 1},
                },
            },
            ["acoustic_modes"],
        ),
        "speaker_preserving_neural_echo_v2_17": (
            files / "echo.json",
            {
                "schema": "murmurmark.speaker_preserving_neural_echo_corpus_report/v2.17",
                "passed": True,
                "checks": {"word_safety": True, "local_safety": True},
                "aggregate": {
                    "candidate_sessions": 1,
                    "fallback_sessions": 1,
                    "remote_supported_reduction_sec": 3.0,
                },
            },
            ["me_remote_roles", "missing_me", "remote_leakage", "acoustic_modes"],
        ),
        "speaker_preserving_neural_echo_v2_17_decision": (
            files / "echo-decision.json",
            {
                "schema": "murmurmark.speaker_preserving_neural_echo_promotion_decision/v2.17",
                "decision": "PROMOTE_SPEAKER_PRESERVING_NEURAL_ECHO_V2",
            },
            ["me_remote_roles", "missing_me", "remote_leakage"],
        ),
        "authoritative_boundary_v1": (
            files / "boundary.json",
            {
                "schema": "murmurmark.authoritative_boundary_corpus_report/v1",
                "decision": "PROMOTE_AUTHORITATIVE_BOUNDARY_V1",
                "gates": {"passed": True},
                "summary": {"closed_items": 10},
            },
            ["chronology", "me_remote_roles", "overlap", "missing_me"],
        ),
        "lexical_accuracy_reference_corpus_v1": (
            files / "lexical.json",
            {
                "schema": "murmurmark.lexical_accuracy_reference_frozen_manifest/v1",
                "decision": "REFERENCE_INSUFFICIENT",
                "gates": {
                    "exact_generated_reference_present": True,
                    "weak_references_excluded_from_correctness": True,
                    "real_meeting_lexical_baseline_ready": False,
                },
                "summary": {
                    "exact_subset": {
                        "reference_words": 67,
                        "hypothesis_words": 67,
                        "word_errors": 0,
                        "wer": 0.0,
                        "substitutions": 0,
                        "deletions": 0,
                        "insertions": 0,
                        "reference_characters": 495,
                        "character_errors": 0,
                        "cer": 0.0,
                    },
                    "human_reviewed_real_sessions": 0,
                },
            },
            ["recognized_words"],
        ),
        "remote_speaker_residual_reference_corpus_v1": (
            files / "remote-reference.json",
            {
                "schema": "murmurmark.remote_speaker_residual_reference_corpus_report/v1",
                "decision": "REFERENCE_INSUFFICIENT",
                "summary": {
                    "review_items": 12,
                    "reviewed_items": 0,
                    "wavlm_proposal_words": 10,
                    "direct_reference_proposal_words": 0,
                    "candidate_precision": None,
                },
                "gates": {
                    "six_session_scope": True,
                    "review_item_count_exact": True,
                    "all_residual_words_once": True,
                    "residual_scope_seconds_exact": True,
                    "referenceable_word_seconds_exact": True,
                    "unaligned_residual_seconds_accounted": True,
                    "wavlm_proposal_words_exact": True,
                    "wavlm_proposal_seconds_exact": True,
                    "blind_prediction_separation": True,
                    "raw_audio_unchanged": True,
                    "selected_transcript_unchanged": True,
                    "reviewed_all_proposals": False,
                    "direct_reference_all_proposals": False,
                    "minimum_attributable_proposals": False,
                    "candidate_precision": False,
                },
            },
            ["remote_speaker_turns"],
        ),
        "controlled_remote_speaker_truth_lab_v1": (
            files / "remote-truth-lab.json",
            {
                "schema": "murmurmark.controlled_remote_speaker_truth_lab_report/v1",
                "decision": "DO_NOT_ADVANCE",
                "corpus": {"scenario_count": 8},
                "safety": {
                    "audit_only": True,
                    "real_transcript_changed": False,
                    "coverage_v3_changed": False,
                    "primary_asr_changed": False,
                    "echo_guard_changed": False,
                    "synthetic_labels_promoted": False,
                },
                "evaluation": {
                    "track_decisions": {
                        "coverage_v3_topology": {"decision": "CONTROL_QUALIFIED"},
                        "wavlm_open_set_candidate": {"decision": "DO_NOT_ADVANCE"},
                    },
                    "coverage_v3_topology": {
                        "hard": {
                            "bcubed": {"f1": 0.983505},
                            "pairwise": {"precision": 1.0},
                        }
                    },
                    "wavlm_open_set_candidate": {
                        "hard": {
                            "bcubed": {"f1": 0.834325},
                            "pairwise": {"precision": 0.95092},
                            "open_set_false_attributions": 2,
                        }
                    },
                },
                "gates": {
                    "minimum_anonymous_enrolled_speakers": True,
                    "source_stem_reconstruction_exact": True,
                    "session_disjoint_splits": True,
                    "hard_split_untuned": True,
                    "all_words_conserved": True,
                    "direct_truth_coverage": True,
                    "mixed_words_fail_closed": True,
                    "public_artifacts_private_safe": True,
                    "synthetic_evidence_not_promoted": True,
                    "wavlm_candidate_held_out_bcubed_f1": False,
                    "wavlm_candidate_held_out_pairwise_precision": False,
                    "wavlm_candidate_boundary_recall": False,
                    "wavlm_candidate_zero_open_set_false_attribution": False,
                },
            },
            ["remote_speaker_turns"],
        ),
        "duration_aware_remote_speaker_attribution_v2": (
            files / "remote-duration-v2.json",
            {
                "schema": "murmurmark.duration_aware_remote_speaker_attribution_report/v2",
                "decision": "DO_NOT_PROMOTE_TOPOLOGY",
                "selected_topology": "conservative_resemblyzer_wavlm_fusion",
                "hard_v2": {
                    "decision_open_count": 1,
                    "used_for_selection": False,
                },
                "hard_v2_metrics": {
                    "bcubed": {"f1": 0.499381},
                    "pairwise": {"precision": 1.0},
                    "known_attribution_recall": 0.551402,
                    "boundary_recall": 0.321429,
                    "open_set_false_attributions": 0,
                },
                "coverage_v3_control_metrics": {
                    "bcubed": {"f1": 0.389824},
                    "pairwise": {"precision": 1.0},
                    "known_attribution_recall": 0.439252,
                    "boundary_recall": 0.214286,
                    "open_set_false_attributions": 0,
                },
                "gates": {
                    "word_conservation": True,
                    "direct_truth_coverage": True,
                    "mixed_words_fail_closed": True,
                    "production_boundaries_unchanged": True,
                    "hard_v2_not_used_for_selection": True,
                    "bcubed_f1": False,
                    "boundary_recall": False,
                    "known_speaker_recall": False,
                    "pairwise_precision": True,
                    "zero_open_set_false_attribution": True,
                    "coverage_v3_control_non_regression": True,
                },
            },
            ["remote_speaker_turns"],
        ),
        "segment_context_remote_speaker_attribution_v1": (
            files / "remote-segment-context-v1.json",
            {
                "schema": "murmurmark.segment_context_remote_speaker_attribution_report/v1",
                "decision": "DO_NOT_PROMOTE_SEGMENT_CONTEXT",
                "selected_topology": "conservative_dual_backend_context_fusion",
                "hard_v3": {
                    "decision_open_count": 1,
                    "used_for_selection": False,
                },
                "hard_v3_metrics": {
                    "bcubed": {"f1": 0.475586},
                    "pairwise": {"precision": 0.966418},
                    "known_speaker_recall": 0.445087,
                    "boundary_recall": 0.0,
                    "open_set_false_attributions": 2,
                },
                "coverage_v3_control_metrics": {
                    "bcubed": {"f1": 0.39759},
                    "pairwise": {"precision": 0.943249},
                    "known_speaker_recall": 0.439306,
                    "boundary_recall": 0.15,
                    "open_set_false_attributions": 0,
                },
                "gates": {
                    "word_conservation": True,
                    "direct_truth_coverage": True,
                    "mixed_words_fail_closed": True,
                    "production_boundaries_unchanged": True,
                    "hard_v3_not_used_for_selection": True,
                    "bcubed_f1": False,
                    "boundary_recall": False,
                    "known_speaker_recall": False,
                    "pairwise_precision": False,
                    "zero_open_set_false_attribution": False,
                    "coverage_v3_control_non_regression": False,
                },
            },
            ["remote_speaker_turns"],
        ),
        "remote_speaker_attribution_error_decomposition_v1": (
            files / "remote-speaker-attribution-error-decomposition-v1.json",
            {
                "schema": "murmurmark.remote_speaker_attribution_error_decomposition_report/v1",
                "decision": "ADVANCE_STRONGER_SPEAKER_IDENTITY",
                "scope": {
                    "diagnostic_only": True,
                    "production_candidate_selected": False,
                    "synthetic_labels_exported_to_real_sessions": False,
                },
                "invariants": {
                    "all_words_conserved": True,
                    "full_oracle_bcubed": True,
                    "full_oracle_boundary_recall": True,
                    "full_oracle_known_recall": True,
                    "full_oracle_mixed_safe": True,
                    "full_oracle_open_set_safe": True,
                    "full_oracle_pairwise_precision": True,
                    "hard_sets_not_reopened": True,
                    "production_guards_frozen": True,
                },
                "aggregate_primary": {
                    "current": {
                        "word_count": 393,
                        "boundary_count": 64,
                        "known_speaker_recall": 0.571006,
                        "boundary_recall": 0.421875,
                    },
                    "oracle_boundaries_current_identity": {
                        "known_speaker_recall": 0.627219,
                    },
                    "current_boundaries_oracle_identity": {
                        "known_speaker_recall": 0.934911,
                        "boundary_recall": 0.75,
                    },
                    "overlap_open_set_oracle": {
                        "open_set_false_attributions": 0,
                    },
                },
                "routing_evidence": {
                    "axis_gains": {
                        "segmentation": 0.063882,
                        "speaker_identity": 0.351382,
                        "overlap_open_set": 0.036364,
                    }
                },
                "production_changed": False,
            },
            ["remote_speaker_turns"],
        ),
        "stronger_remote_speaker_identity_backend_qualification_v1": (
            files / "remote-speaker-identity-backend-qualification-v1.json",
            {
                "schema": "murmurmark.stronger_remote_speaker_identity_backend_qualification_report/v1",
                "decision": "PROMOTE_LAB_IDENTITY_CANDIDATE",
                "selected_candidate_id": "speechbrain_ecapa_voxceleb_candidate",
                "hard_v4_open_count": 1,
                "promotion_gates": {
                    "boundary_no_regression": True,
                    "exact_word_conservation": True,
                    "minimum_bcubed_f1": True,
                    "minimum_known_speaker_recall": True,
                    "minimum_pairwise_precision": True,
                    "mixed_fail_closed": True,
                    "single_candidate": True,
                    "zero_open_set_false_attribution": True,
                },
                "hard_v4": {
                    "control": {
                        "metrics": {
                            "known_attribution_recall": 0.0,
                        }
                    },
                    "candidate": {
                        "metrics": {
                            "bcubed": {"f1": 0.948042},
                            "pairwise": {"precision": 1.0},
                            "known_attribution_recall": 0.947368,
                            "boundary_recall": 0.565217,
                            "open_set_false_attributions": 0,
                        }
                    },
                },
                "safety": {
                    "production_mutated": False,
                    "coverage_v3_mutated": False,
                    "synthetic_identity_transferred_to_real_sessions": False,
                },
            },
            ["remote_speaker_turns"],
        ),
        "ecapa_remote_speaker_shadow_qualification_v1": (
            files / "ecapa-remote-speaker-shadow-qualification-v1.json",
            {
                "schema": "murmurmark.ecapa_remote_speaker_shadow_qualification_report/v1",
                "decision": "DO_NOT_PROMOTE_REAL_IDENTITY",
                "technical_status": "FAIL",
                "reference_status": "INSUFFICIENT",
                "summary": {
                    "recovered_words": 156,
                    "recovered_word_ratio": 0.183314,
                    "recovered_seconds": 211.099681,
                    "recovered_seconds_ratio": 0.352868,
                },
                "evidence": {
                    "independent_machine_reference": {"precision": 0.878788},
                    "human_reviewed": {"evaluated_proposal_words": 0},
                },
                "technical_gates": {
                    "boundary_and_chronology_no_regression": True,
                    "deterministic_replay": True,
                    "exact_word_and_timestamp_conservation": True,
                    "existing_labels_unchanged": True,
                    "minimum_independent_reference_precision": False,
                    "minimum_recovered_seconds_ratio": True,
                    "minimum_recovered_word_ratio": False,
                    "minimum_structural_one_to_one_precision": True,
                    "runtime_bounded": True,
                    "zero_reviewed_false_attributions": True,
                },
                "safety": {
                    "production_mutated": False,
                    "coverage_v3_mutated": False,
                    "selected_transcript_mutated": False,
                    "human_names_inferred": False,
                    "cross_session_voice_linking": False,
                },
            },
            ["remote_speaker_turns"],
        ),
        "remote_speaker_shadow_error_decomposition_v1": (
            files / "remote-speaker-shadow-error-decomposition-v1.json",
            {
                "schema": "murmurmark.remote_speaker_shadow_error_decomposition_report/v1",
                "decision": "ADVANCE_INTERVAL_PURIFICATION",
                "scope": {
                    "failure_items": 214,
                    "failure_seconds": 392.415726,
                },
                "technical_axes": [
                    {
                        "axis": "interval_purification",
                        "items": 93,
                        "seconds": 201.273504,
                        "item_ratio": 0.434579,
                        "seconds_ratio": 0.512909,
                        "material_score": 0.434579,
                    }
                ],
                "decision_evidence": {
                    "top_axis": "interval_purification",
                    "axis_dominance_margin": 0.128982,
                    "dominant": True,
                },
                "invariants": {
                    "all_items_accounted_once": True,
                    "all_words_accounted_once": True,
                    "deterministic_analysis": True,
                },
                "safety": {
                    "diagnostic_only": True,
                    "production_mutated": False,
                    "coverage_v3_mutated": False,
                    "selected_transcript_mutated": False,
                    "thresholds_tuned": False,
                },
            },
            ["remote_speaker_turns"],
        ),
        "bounded_remote_speaker_interval_purification_v1": (
            files / "bounded-remote-speaker-interval-purification-v1.json",
            {
                "schema": "murmurmark.bounded_remote_speaker_interval_purification_report/v1",
                "decision": "DO_NOT_ADVANCE_INTERVAL_PURIFICATION",
                "scope": {
                    "items": 278,
                    "words": 851,
                    "interval_failure_items": 93,
                    "interval_failure_seconds": 201.273504,
                },
                "candidate": {
                    "materialized_items": 50,
                },
                "comparison": {
                    "newly_accepted_items": 2,
                    "newly_accepted_seconds": 4.154556,
                    "new_reference_error_words": 1,
                    "candidate_evidence": {
                        "independent_machine_reference": {"precision": 0.967742},
                    },
                },
                "invariants": {
                    "all_items_accounted_once": True,
                    "all_words_accounted_once": True,
                    "interval_scope_frozen": True,
                },
                "safety": {
                    "shadow_only": True,
                    "production_mutated": False,
                    "coverage_v3_mutated": False,
                    "selected_transcript_mutated": False,
                    "enrollment_mutated": False,
                    "thresholds_tuned": False,
                },
            },
            ["remote_speaker_turns"],
        ),
        "session_local_remote_speaker_enrollment_hardening_v1": (
            files / "session-local-remote-speaker-enrollment-hardening-v1.json",
            {
                "schema": "murmurmark.session_local_remote_speaker_enrollment_hardening_report/v1",
                "decision": "DO_NOT_ADVANCE_ENROLLMENT_HARDENING",
                "scope": {
                    "items": 278,
                    "words": 851,
                    "enrollment_failure_items": 83,
                    "enrollment_failure_seconds": 119.920926,
                },
                "candidate": {
                    "changed_profiles": 10,
                },
                "comparison": {
                    "newly_accepted_items": 11,
                    "newly_accepted_seconds": 44.694004,
                    "removed_control_acceptances": 5,
                    "new_reference_error_words": 0,
                },
                "invariants": {
                    "all_items_accounted_once": True,
                    "all_words_accounted_once": True,
                    "enrollment_scope_frozen": True,
                    "candidate_uses_enrollment_only": True,
                },
                "safety": {
                    "shadow_only": True,
                    "production_mutated": False,
                    "coverage_v3_mutated": False,
                    "selected_transcript_mutated": False,
                    "item_embeddings_mutated": False,
                    "thresholds_tuned": False,
                },
            },
            ["remote_speaker_turns"],
        ),
    }
    sources: list[dict[str, object]] = []
    for source_id, (path, payload, dimensions) in payloads.items():
        write_json(path, payload)
        sources.append(source(path, source_id, dimensions, str(payload["schema"])))
    sources.extend(
        [
            source(audio_baseline, "residual_audio_arbitration_v1_baseline", ["me_remote_roles", "overlap", "remote_leakage"], "murmurmark.residual_audio_arbitration_baseline/v1"),
            source(recall_baseline, "residual_local_recall_v1_baseline", ["missing_me", "chronology"], "murmurmark.residual_local_recall_baseline/v1"),
            source(chronology, "chronology_risk_queue", ["chronology", "overlap"], line_schema="murmurmark.speaker_mode_risk/v1"),
        ]
    )
    manifest = root / "manifest.json"
    write_json(
        manifest,
        {
            "schema": "murmurmark.transcript_perfection_manifest/v1",
            "dimensions": [
                "recognized_words",
                "chronology",
                "me_remote_roles",
                "remote_speaker_turns",
                "overlap",
                "missing_me",
                "remote_leakage",
                "acoustic_modes",
            ],
            "policy": {
                "abstention_is_not_correctness": True,
                "aggregate_quality_score": False,
                "missing_reference_status": "not_measured",
                "operational_perfection": "correct_supported_result_plus_explicit_unknown",
                "ranking_formula": "fixture",
            },
            "sources": sources,
        },
    )
    return manifest


def run(manifest: Path, out: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(REPORTER), "all", "--manifest", str(manifest), "--out-dir", str(out)],
        check=False,
        capture_output=True,
        text=True,
    )


def main() -> int:
    runbook = (ROOT / "docs/runbooks/transcript-perfection-corpus.md").read_text(encoding="utf-8")
    assert "next_goal: Remote Speaker Direct Truth Seed v1" in runbook
    assert "next_goal: Session-Local Remote Speaker Enrollment Hardening v1" not in runbook
    with tempfile.TemporaryDirectory(prefix=".transcript-perfection-fixture-", dir=ROOT) as temporary:
        root = Path(temporary)
        manifest = build_fixture(root)
        out = root / "out"
        result = run(manifest, out)
        assert result.returncode == 0, result.stdout + result.stderr
        report = json.loads((out / "transcript_perfection_corpus_report.json").read_text())
        assert report["decision"] == "BASELINE_ESTABLISHED"
        assert report["summary"]["verified_sources"] == 23
        assert report["summary"]["aggregate_quality_score"] is None
        assert report["summary"]["aggregate_residual_seconds"] is None
        words = next(row for row in report["dimensions"] if row["id"] == "recognized_words")
        assert words["correctness_status"] == "bounded_exact_subset_only"
        assert words["metrics"]["exact_subset_wer"] == 0.0
        assert report["residuals"][0]["class"] == "unknown_remote_speaker"
        assert report["next_goal"]["id"] == "remote-speaker-direct-truth-seed-v1"
        assert report["next_goal"]["selected_residual_class"] == "unknown_remote_speaker"
        assert report["lexical_prerequisite"]["id"] == "human-reviewed-lexical-seed-v1"
        assert report["lexical_prerequisite"]["status"] == "external_evidence_required"
        snapshot = json.loads((out / "input_manifest.json").read_text())
        assert all(not Path(str(row["path"])).is_absolute() for row in snapshot["sources"])
        assert all("text" not in row and "speaker_name" not in row for row in snapshot["sources"])
        first_report = (out / "transcript_perfection_corpus_report.json").read_bytes()
        verify = subprocess.run(
            [sys.executable, str(REPORTER), "all", "--manifest", str(manifest), "--out-dir", str(out), "--verify-existing"],
            check=False,
            capture_output=True,
            text=True,
        )
        assert verify.returncode == 0, verify.stdout + verify.stderr
        assert (out / "transcript_perfection_corpus_report.json").read_bytes() == first_report

        source_path = root / "sources/remote.json"
        source_path.write_text(source_path.read_text() + "\n", encoding="utf-8")
        stale_out = root / "stale-out"
        stale = run(manifest, stale_out)
        assert stale.returncode == 2
        stale_report = json.loads((stale_out / "transcript_perfection_corpus_report.json").read_text())
        assert stale_report["decision"] == "INVALID_INPUTS"
        assert stale_report["release"]["ready"] is False

    default_sources_exist = all((ROOT / row["path"]).is_file() for row in json.loads(DEFAULT_MANIFEST.read_text())["sources"])
    if default_sources_exist and DEFAULT_OUT.is_dir():
        verify = subprocess.run(
            [sys.executable, str(REPORTER), "all", "--verify-existing"],
            check=False,
            capture_output=True,
            text=True,
        )
        assert verify.returncode == 0, verify.stdout + verify.stderr
    else:
        print("local transcript perfection corpus verification skipped: frozen session reports unavailable")
    print("transcript perfection corpus checks ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
