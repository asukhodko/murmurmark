#!/usr/bin/env python3
"""Build a deterministic scorecard over MurmurMark's frozen transcript evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_SCHEMA = "murmurmark.transcript_perfection_manifest/v1"
REPORT_SCHEMA = "murmurmark.transcript_perfection_corpus_report/v1"
RESIDUAL_SCHEMA = "murmurmark.transcript_perfection_residual/v1"
DEFAULT_MANIFEST = ROOT / "docs/testing/transcript-perfection-corpus-v1-manifest.json"
DEFAULT_OUT = ROOT / "sessions/_reports/transcript-perfection-corpus-v1"
DIMENSIONS = (
    "recognized_words",
    "chronology",
    "me_remote_roles",
    "remote_speaker_turns",
    "overlap",
    "missing_me",
    "remote_leakage",
    "acoustic_modes",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify frozen transcript evidence and rank the measured residual classes."
    )
    parser.add_argument("scope", nargs="?", default="all", choices=["all"])
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--verify-existing",
        action="store_true",
        help="Verify that existing outputs exactly match a fresh deterministic build.",
    )
    return parser.parse_args()


def canonical_json(payload: Any) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def canonical_jsonl(rows: list[dict[str, Any]]) -> bytes:
    return b"".join(
        (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8") for row in rows
    )


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"expected JSON objects: {path}")
    return rows


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def portable_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve()))
    except ValueError as error:
        raise ValueError(f"path is outside repository: {path}") from error


def validate_manifest(manifest: dict[str, Any]) -> None:
    if manifest.get("schema") != MANIFEST_SCHEMA:
        raise ValueError(f"unsupported manifest schema: {manifest.get('schema')}")
    if tuple(manifest.get("dimensions") or []) != DIMENSIONS:
        raise ValueError("manifest dimensions do not match transcript_perfection/v1")
    policy = manifest.get("policy") or {}
    if policy.get("aggregate_quality_score") is not False:
        raise ValueError("aggregate quality score must remain disabled")
    if policy.get("abstention_is_not_correctness") is not True:
        raise ValueError("manifest must preserve abstention visibility")
    sources = manifest.get("sources") or []
    if not sources:
        raise ValueError("manifest has no sources")
    ids = [str(row.get("id") or "") for row in sources]
    if not all(ids) or len(ids) != len(set(ids)):
        raise ValueError("manifest source ids must be non-empty and unique")
    for row in sources:
        path = Path(str(row.get("path") or ""))
        if not str(path) or path.is_absolute() or ".." in path.parts:
            raise ValueError(f"source path must be portable: {path}")
        if not set(row.get("dimensions") or []).issubset(DIMENSIONS):
            raise ValueError(f"unknown source dimension: {row.get('id')}")
        if not isinstance(row.get("sha256"), str) or len(row["sha256"]) != 64:
            raise ValueError(f"invalid source sha256: {row.get('id')}")


def verify_sources(manifest: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    verified: list[dict[str, Any]] = []
    payloads: dict[str, Any] = {}
    failures: list[str] = []
    for source in manifest["sources"]:
        source_id = str(source["id"])
        path = ROOT / str(source["path"])
        row: dict[str, Any] = {
            "id": source_id,
            "path": str(source["path"]),
            "required": bool(source.get("required", True)),
            "status": "verified",
        }
        if not path.is_file():
            row["status"] = "missing"
            if row["required"]:
                failures.append(f"missing:{source_id}")
            verified.append(row)
            continue
        actual_bytes = path.stat().st_size
        actual_sha = sha256(path)
        row.update({"bytes": actual_bytes, "sha256": actual_sha})
        if actual_bytes != int(source["bytes"]):
            row["status"] = "size_mismatch"
        if actual_sha != source["sha256"]:
            row["status"] = "sha256_mismatch"
        try:
            if path.suffix == ".jsonl":
                payload: Any = read_jsonl(path)
                expected_line_schema = source.get("expected_line_schema")
                if expected_line_schema and any(item.get("schema") != expected_line_schema for item in payload):
                    row["status"] = "schema_mismatch"
            else:
                payload = read_json(path)
                expected_schema = source.get("expected_schema")
                if expected_schema and payload.get("schema") != expected_schema:
                    row["status"] = "schema_mismatch"
            payloads[source_id] = payload
        except (ValueError, json.JSONDecodeError) as error:
            row["status"] = "invalid_json"
            row["error"] = str(error)
        if row["status"] != "verified" and row["required"]:
            failures.append(f"{row['status']}:{source_id}")
        verified.append(row)
    return verified, payloads, failures


def semantic_gates(
    payloads: dict[str, Any], verified: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[str]]:
    checks: list[dict[str, Any]] = []
    source_sha = {row["id"]: row.get("sha256") for row in verified}

    def add(source: str, gate: str, passed: bool, observed: Any) -> None:
        checks.append({"source": source, "gate": gate, "passed": bool(passed), "observed": observed})

    remote = payloads.get("remote_speaker_coverage_v3") or {}
    add("remote_speaker_coverage_v3", "decision", remote.get("decision") == "PROMOTE", remote.get("decision"))
    add(
        "remote_speaker_coverage_v3",
        "all_source_gates",
        bool(remote.get("gates")) and all(value is True for value in remote.get("gates", {}).values()),
        remote.get("gates"),
    )
    remote_manifest = payloads.get("remote_speaker_coverage_v3_manifest") or {}
    add(
        "remote_speaker_coverage_v3_manifest",
        "decision",
        remote_manifest.get("decision") == "PROMOTE",
        remote_manifest.get("decision"),
    )

    audio = payloads.get("residual_audio_arbitration_v1") or {}
    add("residual_audio_arbitration_v1", "scientifically_complete", audio.get("gates", {}).get("scientifically_complete") is True, audio.get("gates"))
    add("residual_audio_arbitration_v1", "no_hard_failures", not audio.get("gates", {}).get("hard_failures"), audio.get("gates", {}).get("hard_failures"))
    audio_baseline = payloads.get("residual_audio_arbitration_v1_baseline") or {}
    add(
        "residual_audio_arbitration_v1",
        "baseline_lineage",
        audio.get("baseline_manifest_sha256") == source_sha.get("residual_audio_arbitration_v1_baseline"),
        audio.get("baseline_manifest_sha256"),
    )
    add(
        "residual_audio_arbitration_v1_baseline",
        "queue_identity",
        int(audio_baseline.get("queue", {}).get("item_count", -1)) == int(audio.get("summary", {}).get("frozen_queue_items", -2))
        and abs(float(audio_baseline.get("queue", {}).get("seconds", -1)) - float(audio.get("summary", {}).get("frozen_queue_seconds", -2))) < 0.001,
        audio_baseline.get("queue"),
    )

    recall = payloads.get("residual_local_recall_v1") or {}
    add("residual_local_recall_v1", "decision", recall.get("decision") == "PROMOTE_RESIDUAL_LOCAL_RECALL_V1", recall.get("decision"))
    add("residual_local_recall_v1", "source_gates", recall.get("gates", {}).get("passed") is True, recall.get("gates"))
    recall_baseline = payloads.get("residual_local_recall_v1_baseline") or {}
    add(
        "residual_local_recall_v1",
        "baseline_lineage",
        recall.get("baseline_sha256") == source_sha.get("residual_local_recall_v1_baseline"),
        recall.get("baseline_sha256"),
    )

    profile = payloads.get("speaker_mode_profile_baseline") or {}
    chronology = payloads.get("chronology_risk_queue") or []
    chronology_seconds = round(sum(float(row.get("interval", {}).get("duration_sec") or 0) for row in chronology), 6)
    expected_chronology = profile.get("queues", {}).get("chronology", {})
    add(
        "chronology_risk_queue",
        "queue_identity",
        len(chronology) == int(expected_chronology.get("items", -1))
        and abs(chronology_seconds - float(expected_chronology.get("seconds", -1))) < 0.001,
        {"items": len(chronology), "seconds": chronology_seconds},
    )
    excluded_chronology = recall_baseline.get("excluded_queues", {}).get("transcript_order", {})
    add(
        "residual_local_recall_v1_baseline",
        "chronology_isolation",
        int(excluded_chronology.get("item_count", -1)) == len(chronology)
        and abs(float(excluded_chronology.get("seconds", -1)) - chronology_seconds) < 0.001,
        excluded_chronology,
    )

    acoustic = payloads.get("acoustic_mode_corpus") or {}
    acoustic_summary = acoustic.get("summary", {})
    add("acoustic_mode_corpus", "source_gates", acoustic.get("gates", {}).get("passed") is True, acoustic.get("gates"))
    add(
        "acoustic_mode_corpus",
        "labeled_match_conservation",
        int(acoustic_summary.get("labeled_matches", -1)) == int(acoustic_summary.get("labeled_sessions", -2)),
        acoustic_summary,
    )

    echo = payloads.get("speaker_preserving_neural_echo_v2_17") or {}
    echo_decision = payloads.get("speaker_preserving_neural_echo_v2_17_decision") or {}
    add("speaker_preserving_neural_echo_v2_17", "source_gates", echo.get("passed") is True and all(value is True for value in echo.get("checks", {}).values()), echo.get("checks"))
    add(
        "speaker_preserving_neural_echo_v2_17_decision",
        "decision",
        echo_decision.get("decision") == "PROMOTE_SPEAKER_PRESERVING_NEURAL_ECHO_V2",
        echo_decision.get("decision"),
    )

    boundary = payloads.get("authoritative_boundary_v1") or {}
    add("authoritative_boundary_v1", "decision", boundary.get("decision") == "PROMOTE_AUTHORITATIVE_BOUNDARY_V1", boundary.get("decision"))
    add("authoritative_boundary_v1", "source_gates", boundary.get("gates", {}).get("passed") is True, boundary.get("gates"))

    lexical = payloads.get("lexical_accuracy_reference_corpus_v1") or {}
    add(
        "lexical_accuracy_reference_corpus_v1",
        "scientific_decision",
        lexical.get("decision") in {"LEXICAL_BASELINE_ESTABLISHED", "REFERENCE_INSUFFICIENT"},
        lexical.get("decision"),
    )
    add(
        "lexical_accuracy_reference_corpus_v1",
        "exact_generated_reference",
        lexical.get("gates", {}).get("exact_generated_reference_present") is True,
        lexical.get("gates"),
    )
    add(
        "lexical_accuracy_reference_corpus_v1",
        "weak_reference_isolation",
        lexical.get("gates", {}).get("weak_references_excluded_from_correctness") is True,
        lexical.get("gates"),
    )

    remote_reference = payloads.get("remote_speaker_residual_reference_corpus_v1") or {}
    reference_gates = remote_reference.get("gates") or {}
    structural_reference_gates = (
        "six_session_scope",
        "review_item_count_exact",
        "all_residual_words_once",
        "residual_scope_seconds_exact",
        "referenceable_word_seconds_exact",
        "unaligned_residual_seconds_accounted",
        "wavlm_proposal_words_exact",
        "wavlm_proposal_seconds_exact",
        "blind_prediction_separation",
        "raw_audio_unchanged",
        "selected_transcript_unchanged",
    )
    add(
        "remote_speaker_residual_reference_corpus_v1",
        "scientific_decision",
        remote_reference.get("decision") in {"REFERENCE_READY", "REFERENCE_INSUFFICIENT"},
        remote_reference.get("decision"),
    )
    add(
        "remote_speaker_residual_reference_corpus_v1",
        "frozen_pack_integrity",
        all(reference_gates.get(name) is True for name in structural_reference_gates),
        {name: reference_gates.get(name) for name in structural_reference_gates},
    )

    truth_lab = payloads.get("controlled_remote_speaker_truth_lab_v1") or {}
    truth_lab_gates = truth_lab.get("gates") or {}
    structural_truth_gates = (
        "minimum_anonymous_enrolled_speakers",
        "source_stem_reconstruction_exact",
        "session_disjoint_splits",
        "hard_split_untuned",
        "all_words_conserved",
        "direct_truth_coverage",
        "mixed_words_fail_closed",
        "public_artifacts_private_safe",
        "synthetic_evidence_not_promoted",
    )
    add(
        "controlled_remote_speaker_truth_lab_v1",
        "scientific_decision",
        truth_lab.get("decision") in {"LAB_READY", "DO_NOT_ADVANCE"},
        truth_lab.get("decision"),
    )
    add(
        "controlled_remote_speaker_truth_lab_v1",
        "exact_truth_and_safety",
        all(truth_lab_gates.get(name) is True for name in structural_truth_gates),
        {name: truth_lab_gates.get(name) for name in structural_truth_gates},
    )

    duration_v2 = payloads.get("duration_aware_remote_speaker_attribution_v2") or {}
    duration_gates = duration_v2.get("gates") or {}
    duration_structural_gates = (
        "word_conservation",
        "direct_truth_coverage",
        "mixed_words_fail_closed",
        "production_boundaries_unchanged",
        "hard_v2_not_used_for_selection",
    )
    add(
        "duration_aware_remote_speaker_attribution_v2",
        "scientific_decision",
        duration_v2.get("decision") in {"PROMOTE_LAB_CANDIDATE", "DO_NOT_PROMOTE_TOPOLOGY"},
        duration_v2.get("decision"),
    )
    add(
        "duration_aware_remote_speaker_attribution_v2",
        "blind_hard_v2_integrity",
        all(duration_gates.get(name) is True for name in duration_structural_gates)
        and duration_v2.get("hard_v2", {}).get("decision_open_count") == 1
        and duration_v2.get("hard_v2", {}).get("used_for_selection") is False,
        {name: duration_gates.get(name) for name in duration_structural_gates},
    )

    segment_context = payloads.get("segment_context_remote_speaker_attribution_v1") or {}
    segment_gates = segment_context.get("gates") or {}
    segment_structural_gates = (
        "word_conservation",
        "direct_truth_coverage",
        "mixed_words_fail_closed",
        "production_boundaries_unchanged",
        "hard_v3_not_used_for_selection",
    )
    add(
        "segment_context_remote_speaker_attribution_v1",
        "scientific_decision",
        segment_context.get("decision")
        in {"PROMOTE_LAB_CANDIDATE", "DO_NOT_PROMOTE_SEGMENT_CONTEXT"},
        segment_context.get("decision"),
    )
    add(
        "segment_context_remote_speaker_attribution_v1",
        "blind_hard_v3_integrity",
        all(segment_gates.get(name) is True for name in segment_structural_gates)
        and segment_context.get("hard_v3", {}).get("decision_open_count") == 1
        and segment_context.get("hard_v3", {}).get("used_for_selection") is False,
        {name: segment_gates.get(name) for name in segment_structural_gates},
    )

    decomposition = payloads.get("remote_speaker_attribution_error_decomposition_v1") or {}
    allowed_decomposition_decisions = {
        "ADVANCE_DEDICATED_SEGMENTATION",
        "ADVANCE_STRONGER_SPEAKER_IDENTITY",
        "ADVANCE_OVERLAP_OPEN_SET_MODEL",
        "CURRENT_LOCAL_ATTRIBUTION_LIMIT",
    }
    decomposition_invariants = decomposition.get("invariants") or {}
    add(
        "remote_speaker_attribution_error_decomposition_v1",
        "diagnostic_decision",
        decomposition.get("decision") in allowed_decomposition_decisions,
        decomposition.get("decision"),
    )
    add(
        "remote_speaker_attribution_error_decomposition_v1",
        "frozen_oracle_integrity",
        bool(decomposition_invariants)
        and all(value is True for value in decomposition_invariants.values())
        and decomposition.get("production_changed") is False
        and decomposition.get("scope", {}).get("production_candidate_selected") is False
        and decomposition.get("scope", {}).get("synthetic_labels_exported_to_real_sessions") is False,
        decomposition_invariants,
    )

    identity = payloads.get("stronger_remote_speaker_identity_backend_qualification_v1") or {}
    identity_gates = identity.get("promotion_gates") or {}
    add(
        "stronger_remote_speaker_identity_backend_qualification_v1",
        "qualification_decision",
        identity.get("decision")
        in {"PROMOTE_LAB_IDENTITY_CANDIDATE", "DO_NOT_PROMOTE_IDENTITY_BACKEND"},
        identity.get("decision"),
    )
    add(
        "stronger_remote_speaker_identity_backend_qualification_v1",
        "one_shot_hard_v4_integrity",
        identity_gates.get("exact_word_conservation") is True
        and identity_gates.get("single_candidate") is True
        and identity.get("hard_v4_open_count") == 1
        and identity.get("safety", {}).get("production_mutated") is False
        and identity.get("safety", {}).get("coverage_v3_mutated") is False
        and identity.get("safety", {}).get("synthetic_identity_transferred_to_real_sessions")
        is False,
        {"gates": identity_gates, "open_count": identity.get("hard_v4_open_count")},
    )

    shadow = payloads.get("ecapa_remote_speaker_shadow_qualification_v1") or {}
    shadow_technical = shadow.get("technical_gates") or {}
    shadow_safety = shadow.get("safety") or {}
    add(
        "ecapa_remote_speaker_shadow_qualification_v1",
        "terminal_decision",
        shadow.get("decision")
        in {
            "PROMOTE_REAL_IDENTITY_CANDIDATE",
            "DO_NOT_PROMOTE_REAL_IDENTITY",
            "REFERENCE_INSUFFICIENT",
        },
        shadow.get("decision"),
    )
    add(
        "ecapa_remote_speaker_shadow_qualification_v1",
        "shadow_integrity",
        shadow_technical.get("exact_word_and_timestamp_conservation") is True
        and shadow_technical.get("existing_labels_unchanged") is True
        and shadow_technical.get("boundary_and_chronology_no_regression") is True
        and shadow_technical.get("deterministic_replay") is True
        and shadow_safety.get("production_mutated") is False
        and shadow_safety.get("coverage_v3_mutated") is False
        and shadow_safety.get("selected_transcript_mutated") is False
        and shadow_safety.get("human_names_inferred") is False
        and shadow_safety.get("cross_session_voice_linking") is False,
        {"technical_gates": shadow_technical, "safety": shadow_safety},
    )

    failures = [f"semantic_gate:{row['source']}:{row['gate']}" for row in checks if not row["passed"]]
    return checks, failures


def residual_score(severity: float, seconds: float, evidence: float, repairability: float, sessions: int) -> float:
    score = (
        severity
        * (1.0 + math.log1p(max(0.0, seconds)))
        * (0.5 + 0.5 * evidence)
        * (0.4 + 0.6 * repairability)
        * (1.0 + 0.15 * math.log1p(max(0, sessions)))
    )
    return round(score, 6)


def build_residuals(payloads: dict[str, Any]) -> list[dict[str, Any]]:
    remote = payloads["remote_speaker_coverage_v3"]
    remote_summary = remote["summary"]
    remote_seconds = round(float(remote_summary["remote_speech_sec"]) - float(remote_summary["attributed_speech_sec"]), 6)
    remote_words = int(remote_summary["remote_words"]) - int(remote_summary["attributed_words"])

    audio = payloads["residual_audio_arbitration_v1"]
    audio_summary = audio["summary"]
    audio_sessions = sum(1 for row in audio.get("sessions", []) if float(row.get("summary", {}).get("remaining_seconds") or 0) > 0)

    chronology = payloads["chronology_risk_queue"]
    chronology_seconds = round(sum(float(row.get("interval", {}).get("duration_sec") or 0) for row in chronology), 6)
    chronology_sessions = len({str(row.get("session_id")) for row in chronology})

    recall = payloads["residual_local_recall_v1"]
    recall_summary = recall["summary"]
    recall_sessions = sum(1 for row in recall.get("sessions", []) if float(row.get("summary", {}).get("remaining_seconds") or 0) > 0)

    specs = [
        {
            "class": "unknown_remote_speaker",
            "dimension": "remote_speaker_turns",
            "item_count": remote_words,
            "item_unit": "words",
            "seconds": remote_seconds,
            "sessions": int(remote_summary["sessions"]),
            "severity": 4.0,
            "evidence_strength": 1.0,
            "repairability": 0.8,
            "confidence": "high",
            "source_ids": ["remote_speaker_coverage_v3"],
            "reason": "remote words are preserved but 6.0688% of remote speech lacks supported speaker attribution",
        },
        {
            "class": "ambiguous_me_audio_evidence",
            "dimension": "me_remote_roles",
            "item_count": int(audio_summary["remaining_items"]),
            "item_unit": "review_rows",
            "seconds": float(audio_summary["remaining_seconds"]),
            "sessions": audio_sessions,
            "severity": 5.0,
            "evidence_strength": 0.95,
            "repairability": 0.35,
            "confidence": "high_queue_identity_low_classification_confidence",
            "source_ids": ["residual_audio_arbitration_v1"],
            "reason": "frozen Me/remote overlap rows remain unresolved after the current local evidence ceiling",
        },
        {
            "class": "chronology_conflict",
            "dimension": "chronology",
            "item_count": len(chronology),
            "item_unit": "review_rows",
            "seconds": chronology_seconds,
            "sessions": chronology_sessions,
            "severity": 4.5,
            "evidence_strength": 0.95,
            "repairability": 0.75,
            "confidence": "high",
            "source_ids": ["chronology_risk_queue"],
            "reason": "speaker-bounded ordering conflicts remain explicit because current repair cannot split losslessly",
        },
        {
            "class": "missing_me_uncertainty",
            "dimension": "missing_me",
            "item_count": int(recall_summary["remaining_items"]),
            "item_unit": "review_rows",
            "seconds": float(recall_summary["remaining_seconds"]),
            "sessions": recall_sessions,
            "severity": 5.0,
            "evidence_strength": 0.95,
            "repairability": 0.65,
            "confidence": "high_queue_identity_low_recovery_confidence",
            "source_ids": ["residual_local_recall_v1"],
            "reason": "local-only evidence exists but is insufficient for safe insertion or dismissal",
        },
    ]
    rows: list[dict[str, Any]] = []
    for spec in specs:
        row = {
            "schema": RESIDUAL_SCHEMA,
            **spec,
            "rank_score": residual_score(
                float(spec["severity"]),
                float(spec["seconds"]),
                float(spec["evidence_strength"]),
                float(spec["repairability"]),
                int(spec["sessions"]),
            ),
        }
        rows.append(row)
    rows.sort(key=lambda row: (-float(row["rank_score"]), str(row["class"])))
    for index, row in enumerate(rows, start=1):
        row["rank"] = index
    return rows


def build_dimensions(payloads: dict[str, Any], residuals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    residual_by_class = {row["class"]: row for row in residuals}
    remote = payloads["remote_speaker_coverage_v3"]
    remote_summary = remote["summary"]
    reference = remote.get("reference_evaluation", {}).get("attributed_only", {})
    audio_summary = payloads["residual_audio_arbitration_v1"]["summary"]
    recall_summary = payloads["residual_local_recall_v1"]["summary"]
    chronology = residual_by_class["chronology_conflict"]
    acoustic = payloads["acoustic_mode_corpus"]["summary"]
    echo = payloads["speaker_preserving_neural_echo_v2_17"]["aggregate"]
    boundary = payloads["authoritative_boundary_v1"]["summary"]
    lexical = payloads["lexical_accuracy_reference_corpus_v1"]
    lexical_summary = lexical["summary"]
    exact_subset = lexical_summary["exact_subset"]
    lexical_ready = lexical["decision"] == "LEXICAL_BASELINE_ESTABLISHED"
    remote_reference = payloads["remote_speaker_residual_reference_corpus_v1"]
    remote_reference_summary = remote_reference["summary"]
    remote_reference_ready = remote_reference["decision"] == "REFERENCE_READY"
    truth_lab = payloads["controlled_remote_speaker_truth_lab_v1"]
    truth_tracks = truth_lab["evaluation"]["track_decisions"]
    truth_control = truth_lab["evaluation"]["coverage_v3_topology"]["hard"]
    truth_candidate = truth_lab["evaluation"]["wavlm_open_set_candidate"]["hard"]
    duration_v2 = payloads["duration_aware_remote_speaker_attribution_v2"]
    duration_hard = duration_v2["hard_v2_metrics"]
    duration_control = duration_v2["coverage_v3_control_metrics"]
    segment_context = payloads["segment_context_remote_speaker_attribution_v1"]
    segment_hard = segment_context["hard_v3_metrics"]
    segment_control = segment_context["coverage_v3_control_metrics"]
    decomposition = payloads["remote_speaker_attribution_error_decomposition_v1"]
    decomposition_current = decomposition["aggregate_primary"]["current"]
    decomposition_boundary = decomposition["aggregate_primary"]["oracle_boundaries_current_identity"]
    decomposition_identity = decomposition["aggregate_primary"]["current_boundaries_oracle_identity"]
    decomposition_special = decomposition["aggregate_primary"]["overlap_open_set_oracle"]
    identity = payloads["stronger_remote_speaker_identity_backend_qualification_v1"]
    identity_candidate = identity["hard_v4"]["candidate"]["metrics"]
    identity_control = identity["hard_v4"]["control"]["metrics"]
    identity_shadow = payloads["ecapa_remote_speaker_shadow_qualification_v1"]
    identity_shadow_summary = identity_shadow["summary"]
    identity_shadow_evidence = identity_shadow["evidence"]

    return [
        {
            "id": "recognized_words",
            "status": "measured" if lexical_ready else "partial",
            "correctness_status": "real_meeting_baseline_passed" if lexical_ready else "bounded_exact_subset_only",
            "coverage_status": "real_meeting_reference_complete" if lexical_ready else "real_meeting_reference_insufficient",
            "reference_level": "human_reviewed_real_meetings" if lexical_ready else "exact_generated_plus_diagnostic_weak",
            "source_ids": ["remote_speaker_coverage_v3", "lexical_accuracy_reference_corpus_v1"],
            "metrics": {
                "remote_words": int(remote_summary["remote_words"]),
                "remote_words_conserved": bool(remote["gates"]["all_word_conservation"]),
                "exact_subset_words": int(exact_subset["reference_words"]),
                "exact_subset_wer": exact_subset["wer"],
                "exact_subset_cer": exact_subset["cer"],
                "human_reviewed_real_sessions": int(lexical_summary["human_reviewed_real_sessions"]),
                "real_meeting_lexical_error_rate": exact_subset["wer"] if lexical_ready else None,
            },
            "residual_classes": [],
            "note": (
                "The exact generated subset is measured, and weak references remain diagnostic. "
                "No human-reviewed real-meeting word reference is frozen."
                if not lexical_ready
                else "The required human-reviewed real-meeting lexical baseline is frozen."
            ),
        },
        {
            "id": "chronology",
            "status": "measured_with_residual",
            "correctness_status": "supported_subset_passed",
            "coverage_status": "frozen_residual_visible",
            "reference_level": "frozen_real_session_risk_queue",
            "source_ids": ["authoritative_boundary_v1", "chronology_risk_queue"],
            "metrics": {
                "boundary_closed_items": int(boundary["closed_items"]),
                "residual_items": int(chronology["item_count"]),
                "residual_seconds": float(chronology["seconds"]),
            },
            "residual_classes": ["chronology_conflict"],
        },
        {
            "id": "me_remote_roles",
            "status": "measured_with_residual",
            "correctness_status": "safe_abstention",
            "coverage_status": "frozen_residual_visible",
            "reference_level": "audio_state_text_arbitration",
            "source_ids": ["residual_audio_arbitration_v1", "speaker_preserving_neural_echo_v2_17"],
            "metrics": {
                "remaining_items": int(audio_summary["remaining_items"]),
                "remaining_seconds": float(audio_summary["remaining_seconds"]),
                "pre_asr_local_retention_floor": 1.0,
            },
            "residual_classes": ["ambiguous_me_audio_evidence"],
        },
        {
            "id": "remote_speaker_turns",
            "status": "measured_with_unknown",
            "correctness_status": "attributed_subset_passed",
            "coverage_status": (
                "explicit_unknown_with_direct_residual_reference"
                if remote_reference_ready
                else "explicit_unknown_reference_insufficient"
            ),
            "reference_level": (
                "human_reviewed_blind_residual_reference"
                if remote_reference_ready
                else "blind_residual_pack_without_independent_truth"
            ),
            "source_ids": [
                "remote_speaker_coverage_v3",
                "remote_speaker_coverage_v3_manifest",
                "remote_speaker_residual_reference_corpus_v1",
                "controlled_remote_speaker_truth_lab_v1",
                "duration_aware_remote_speaker_attribution_v2",
                "segment_context_remote_speaker_attribution_v1",
                "remote_speaker_attribution_error_decomposition_v1",
                "stronger_remote_speaker_identity_backend_qualification_v1",
                "ecapa_remote_speaker_shadow_qualification_v1",
            ],
            "metrics": {
                "attributable_speech_ratio": float(remote_summary["attributable_remote_speech_ratio"]),
                "attributed_bcubed_f1": float(reference.get("bcubed", {}).get("f1") or 0),
                "attributed_pairwise_precision": float(reference.get("pairwise", {}).get("precision") or 0),
                "unknown_seconds": float(residual_by_class["unknown_remote_speaker"]["seconds"]),
                "unknown_words": int(residual_by_class["unknown_remote_speaker"]["item_count"]),
                "residual_reference_decision": remote_reference["decision"],
                "residual_reference_items": int(remote_reference_summary["review_items"]),
                "residual_reference_reviewed_items": int(remote_reference_summary["reviewed_items"]),
                "wavlm_proposal_words": int(remote_reference_summary["wavlm_proposal_words"]),
                "direct_reference_proposal_words": int(
                    remote_reference_summary["direct_reference_proposal_words"]
                ),
                "candidate_precision": remote_reference_summary["candidate_precision"],
                "truth_lab_decision": truth_lab["decision"],
                "truth_lab_control_decision": truth_tracks["coverage_v3_topology"]["decision"],
                "truth_lab_control_bcubed_f1": float(truth_control["bcubed"]["f1"]),
                "truth_lab_control_pairwise_precision": float(
                    truth_control["pairwise"]["precision"]
                ),
                "truth_lab_candidate_decision": truth_tracks["wavlm_open_set_candidate"]["decision"],
                "truth_lab_candidate_bcubed_f1": float(truth_candidate["bcubed"]["f1"]),
                "truth_lab_candidate_pairwise_precision": float(
                    truth_candidate["pairwise"]["precision"]
                ),
                "truth_lab_candidate_open_set_false_attributions": int(
                    truth_candidate["open_set_false_attributions"]
                ),
                "duration_v2_decision": duration_v2["decision"],
                "duration_v2_selected_topology": duration_v2["selected_topology"],
                "duration_v2_hard_bcubed_f1": float(duration_hard["bcubed"]["f1"]),
                "duration_v2_hard_pairwise_precision": float(
                    duration_hard["pairwise"]["precision"]
                ),
                "duration_v2_hard_known_speaker_recall": float(
                    duration_hard["known_attribution_recall"]
                ),
                "duration_v2_hard_boundary_recall": float(duration_hard["boundary_recall"]),
                "duration_v2_hard_open_set_false_attributions": int(
                    duration_hard["open_set_false_attributions"]
                ),
                "duration_v2_control_known_speaker_recall": float(
                    duration_control["known_attribution_recall"]
                ),
                "segment_context_decision": segment_context["decision"],
                "segment_context_selected_topology": segment_context["selected_topology"],
                "segment_context_hard_bcubed_f1": float(segment_hard["bcubed"]["f1"]),
                "segment_context_hard_pairwise_precision": float(
                    segment_hard["pairwise"]["precision"]
                ),
                "segment_context_hard_known_speaker_recall": float(
                    segment_hard["known_speaker_recall"]
                ),
                "segment_context_hard_boundary_recall": float(
                    segment_hard["boundary_recall"]
                ),
                "segment_context_hard_open_set_false_attributions": int(
                    segment_hard["open_set_false_attributions"]
                ),
                "segment_context_control_known_speaker_recall": float(
                    segment_control["known_speaker_recall"]
                ),
                "error_decomposition_decision": decomposition["decision"],
                "error_decomposition_words": int(decomposition_current["word_count"]),
                "error_decomposition_boundaries": int(decomposition_current["boundary_count"]),
                "error_decomposition_current_known_speaker_recall": float(
                    decomposition_current["known_speaker_recall"]
                ),
                "error_decomposition_current_boundary_recall": float(
                    decomposition_current["boundary_recall"]
                ),
                "error_decomposition_boundary_oracle_known_speaker_recall": float(
                    decomposition_boundary["known_speaker_recall"]
                ),
                "error_decomposition_identity_oracle_known_speaker_recall": float(
                    decomposition_identity["known_speaker_recall"]
                ),
                "error_decomposition_identity_oracle_boundary_recall": float(
                    decomposition_identity["boundary_recall"]
                ),
                "error_decomposition_special_oracle_false_attributions": int(
                    decomposition_special["open_set_false_attributions"]
                ),
                "error_decomposition_axis_gains": decomposition["routing_evidence"]["axis_gains"],
                "identity_backend_qualification_decision": identity["decision"],
                "identity_backend_selected_candidate": identity["selected_candidate_id"],
                "identity_hard_v4_bcubed_f1": float(identity_candidate["bcubed"]["f1"]),
                "identity_hard_v4_pairwise_precision": float(
                    identity_candidate["pairwise"]["precision"]
                ),
                "identity_hard_v4_known_speaker_recall": float(
                    identity_candidate["known_attribution_recall"]
                ),
                "identity_hard_v4_boundary_recall": float(identity_candidate["boundary_recall"]),
                "identity_hard_v4_open_set_false_attributions": int(
                    identity_candidate["open_set_false_attributions"]
                ),
                "identity_control_hard_v4_known_speaker_recall": float(
                    identity_control["known_attribution_recall"]
                ),
                "ecapa_real_shadow_decision": identity_shadow["decision"],
                "ecapa_real_shadow_recovered_words": int(
                    identity_shadow_summary["recovered_words"]
                ),
                "ecapa_real_shadow_recovered_word_ratio": float(
                    identity_shadow_summary["recovered_word_ratio"]
                ),
                "ecapa_real_shadow_recovered_seconds": float(
                    identity_shadow_summary["recovered_seconds"]
                ),
                "ecapa_real_shadow_recovered_seconds_ratio": float(
                    identity_shadow_summary["recovered_seconds_ratio"]
                ),
                "ecapa_real_shadow_independent_precision": identity_shadow_evidence[
                    "independent_machine_reference"
                ]["precision"],
                "ecapa_real_shadow_human_reviewed_words": int(
                    identity_shadow_evidence["human_reviewed"]["evaluated_proposal_words"]
                ),
            },
            "residual_classes": ["unknown_remote_speaker"],
        },
        {
            "id": "overlap",
            "status": "measured_with_residual",
            "correctness_status": "safe_abstention",
            "coverage_status": "shared_frozen_audio_residual",
            "reference_level": "speaker_bounded_audio_review",
            "source_ids": ["residual_audio_arbitration_v1", "chronology_risk_queue"],
            "metrics": {
                "ambiguous_audio_seconds": float(audio_summary["remaining_seconds"]),
                "chronology_overlap_seconds": float(chronology["seconds"]),
            },
            "residual_classes": ["ambiguous_me_audio_evidence", "chronology_conflict"],
            "note": "Residual classes overlap semantically and are not summed.",
        },
        {
            "id": "missing_me",
            "status": "measured_with_residual",
            "correctness_status": "safe_abstention",
            "coverage_status": "frozen_residual_visible",
            "reference_level": "local_only_audio_state_evidence",
            "source_ids": ["residual_local_recall_v1", "speaker_preserving_neural_echo_v2_17"],
            "metrics": {
                "closed_items": int(recall_summary["closed_items"]),
                "remaining_items": int(recall_summary["remaining_items"]),
                "remaining_seconds": float(recall_summary["remaining_seconds"]),
            },
            "residual_classes": ["missing_me_uncertainty"],
        },
        {
            "id": "remote_leakage",
            "status": "measured_with_residual",
            "correctness_status": "promoted_pre_asr_subset_passed",
            "coverage_status": "fallback_and_ambiguous_rows_visible",
            "reference_level": "sealed_pre_asr_corpus_plus_audio_arbitration",
            "source_ids": ["speaker_preserving_neural_echo_v2_17", "residual_audio_arbitration_v1"],
            "metrics": {
                "pre_asr_candidate_sessions": int(echo["candidate_sessions"]),
                "exact_fallback_sessions": int(echo["fallback_sessions"]),
                "remote_supported_reduction_seconds": float(echo["remote_supported_reduction_sec"]),
                "ambiguous_me_audio_seconds": float(audio_summary["remaining_seconds"]),
            },
            "residual_classes": ["ambiguous_me_audio_evidence"],
        },
        {
            "id": "acoustic_modes",
            "status": "measured",
            "correctness_status": "labeled_subset_passed",
            "coverage_status": "explicit_uncertain_allowed",
            "reference_level": "labeled_real_sessions",
            "source_ids": ["acoustic_mode_corpus", "speaker_preserving_neural_echo_v2_17"],
            "metrics": {
                "sessions": int(acoustic["session_count"]),
                "labeled_sessions": int(acoustic["labeled_sessions"]),
                "labeled_matches": int(acoustic["labeled_matches"]),
                "by_mode": acoustic["by_mode"],
            },
            "residual_classes": [],
        },
    ]


def markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Transcript Perfection Corpus v1",
        "",
        f"Decision: `{report['decision']}`  ",
        f"Frozen inputs: `{report['summary']['verified_sources']}/{report['summary']['required_sources']}` verified  ",
        f"Release ready: `{'yes' if report['release']['ready'] else 'no'}`",
        "",
        "## Dimension Scorecard",
        "",
        "| Dimension | Status | Correctness | Coverage |",
        "|---|---|---|---|",
    ]
    for row in report["dimensions"]:
        lines.append(
            f"| `{row['id']}` | `{row['status']}` | `{row['correctness_status']}` | `{row['coverage_status']}` |"
        )
    lines.extend(
        [
            "",
            "`recognized_words` now measures an exact generated subset. Real-meeting lexical correctness",
            "remains reference-insufficient until the human-reviewed 1x1/group and acoustic gates pass.",
            "",
            "## Actionable Residuals",
            "",
            "Residual seconds come from different frozen scopes and must not be added together.",
            "",
            "| Rank | Class | Dimension | Items | Seconds | Sessions | Score |",
            "|---:|---|---|---:|---:|---:|---:|",
        ]
    )
    for row in report["residuals"]:
        lines.append(
            f"| {row['rank']} | `{row['class']}` | `{row['dimension']}` | {row['item_count']} {row['item_unit']} | "
            f"{float(row['seconds']):.3f} | {row['sessions']} | {float(row['rank_score']):.3f} |"
        )
    lines.extend(
        [
            "",
            "## Evidence Gaps",
            "",
        ]
    )
    for row in report["evidence_gaps"]:
        lines.append(f"- `{row['dimension']}`: {row['reason']}")
    next_goal = report["next_goal"]
    lexical_prerequisite = report["lexical_prerequisite"]
    lines.extend(
        [
            "",
            "## External Prerequisite",
            "",
            f"**{lexical_prerequisite['title']}** (`{lexical_prerequisite['status']}`)",
            "",
            lexical_prerequisite["rationale"],
            "",
            "## Next Goal",
            "",
            f"**{next_goal['title']}**",
            "",
            next_goal["rationale"],
            "",
            "The next engineering goal must preserve words, timestamps, attributed precision and exact",
            "aggregate fallback while reducing its named frozen residual.",
            "",
            "## Release Blockers",
            "",
        ]
    )
    lines.extend(f"- `{blocker}`" for blocker in report["release"]["blockers"])
    return "\n".join(lines) + "\n"


def build_report(manifest: dict[str, Any], verified: list[dict[str, Any]], payloads: dict[str, Any], semantic_checks: list[dict[str, Any]], failures: list[str]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    residuals = build_residuals(payloads) if not failures else []
    dimensions = build_dimensions(payloads, residuals) if not failures else [
        {
            "id": dimension,
            "status": "not_measured",
            "correctness_status": "not_measured",
            "coverage_status": "invalid_or_stale_inputs",
            "source_ids": [],
            "metrics": {},
            "residual_classes": [],
        }
        for dimension in DIMENSIONS
    ]
    required_sources = sum(1 for row in verified if row["required"])
    verified_sources = sum(1 for row in verified if row["required"] and row["status"] == "verified")
    decision = "BASELINE_ESTABLISHED" if not failures else "INVALID_INPUTS"
    remote_reference_ready = (
        (payloads.get("remote_speaker_residual_reference_corpus_v1") or {}).get("decision")
        == "REFERENCE_READY"
    )
    release_blockers = [
        "recognized_words.real_meeting_lexical_reference_insufficient",
        "remote_speaker_turns.unknown_remote_speaker",
        "me_remote_roles.ambiguous_me_audio_evidence",
        "chronology.chronology_conflict",
        "missing_me.missing_me_uncertainty",
    ]
    if not remote_reference_ready:
        release_blockers.insert(2, "remote_speaker_turns.residual_reference_insufficient")
    if (
        (payloads.get("ecapa_remote_speaker_shadow_qualification_v1") or {}).get("decision")
        != "PROMOTE_REAL_IDENTITY_CANDIDATE"
    ):
        release_blockers.insert(2, "remote_speaker_turns.ecapa_shadow_not_promoted")
    report = {
        "schema": REPORT_SCHEMA,
        "generator": {"name": "report-transcript-perfection-corpus", "version": "0.8.0", "mode": "deterministic_offline"},
        "decision": decision,
        "manifest": {
            "path": portable_path(Path(str(manifest["_path"]))),
            "sha256": manifest["_sha256"],
        },
        "policy": manifest["policy"],
        "input_integrity": {
            "passed": not failures,
            "failures": failures,
            "sources": verified,
            "semantic_checks": semantic_checks,
        },
        "summary": {
            "required_sources": required_sources,
            "verified_sources": verified_sources,
            "dimensions": len(DIMENSIONS),
            "fully_measured_dimensions": sum(1 for row in dimensions if row["status"] == "measured"),
            "partially_or_residual_measured_dimensions": sum(1 for row in dimensions if row["status"] in {"partial", "measured_with_residual", "measured_with_unknown"}),
            "not_measured_correctness_dimensions": sum(1 for row in dimensions if row["correctness_status"] == "not_measured"),
            "bounded_correctness_dimensions": sum(1 for row in dimensions if row["correctness_status"] == "bounded_exact_subset_only"),
            "actionable_residual_classes": len(residuals),
            "aggregate_quality_score": None,
            "aggregate_residual_seconds": None,
        },
        "dimensions": dimensions,
        "residuals": residuals,
        "evidence_gaps": [
            {
                "dimension": "recognized_words",
                "status": "reference_insufficient",
                "reason": "the exact generated subset is 67 words at WER/CER 0, but no human-reviewed real-meeting word reference exists",
                "next_evidence": "freeze two private human-reviewed real meetings covering 1x1, group, Me, remote, speaker playback and headphones/low-leak",
            },
            {
                "dimension": "remote_speaker_turns",
                "status": "reference_insufficient",
                "reason": (
                    "the blind 851-word residual pack is frozen, but none of its 53 WavLM proposals "
                    "has independent human-reviewed truth; ECAPA passed the disjoint hard-v4, then "
                    "recovered 156 words and 211.099681 seconds in real-session shadow, but missed the "
                    "frozen 20% word gate and reached only 0.878788 precision on the available coarse "
                    "independent machine reference"
                ),
                "next_evidence": (
                    "decompose the 68 accepted ECAPA items into interval purity, enrollment, short/silent "
                    "audio, reference granularity and identity errors before choosing another model or fusion rule"
                ),
            },
            {
                "dimension": "local_mic_multi_speaker",
                "status": "not_measured",
                "reason": "no real labeled multi-person local-mic scenario exists",
                "next_evidence": "open only after the scenario occurs and a consented labeled corpus exists",
            },
        ],
        "release": {
            "ready": False,
            "blockers": release_blockers if not failures else ["input_integrity"] + failures,
        },
        "lexical_prerequisite": {
            "id": "human-reviewed-lexical-seed-v1",
            "title": "Human-Reviewed Lexical Seed v1",
            "status": "external_evidence_required",
            "rationale": (
                "Two private human-reviewed real meetings are still required before real-meeting "
                "lexical correctness can be measured. This prerequisite is not an autonomous "
                "engineering goal and does not displace the highest measured residual."
            ),
        },
        "next_goal": {
            "id": "remote-speaker-shadow-error-decomposition-v1" if not failures else "restore-input-integrity",
            "title": "Remote Speaker Shadow Error Decomposition v1" if not failures else "Restore Input Integrity",
            "selected_residual_class": "unknown_remote_speaker" if not failures else None,
            "rationale": (
                "The frozen ECAPA real-session shadow failed the 20% word and 0.99 independent-reference "
                "precision gates while recovering substantial seconds. Decompose those failures with the "
                "existing frozen artifacts before investing in another identity backend or fusion rule; "
                "Coverage v3 remains authoritative."
                if not failures
                else "Input integrity must be restored before selecting an engineering goal."
            ),
        },
        "gates": {
            "all_required_sources_verified": not any(row["required"] and row["status"] != "verified" for row in verified),
            "all_source_contracts_preserved": not any(not row["passed"] for row in semantic_checks),
            "all_dimensions_explicit": len(dimensions) == len(DIMENSIONS),
            "missing_reference_not_passed": any(
                row["id"] == "recognized_words"
                and row["correctness_status"] in {"bounded_exact_subset_only", "real_meeting_baseline_passed"}
                for row in dimensions
            ),
            "abstention_visible": bool(manifest["policy"]["abstention_is_not_correctness"]),
            "aggregate_score_disabled": manifest["policy"]["aggregate_quality_score"] is False,
        },
    }
    report["gates"]["passed"] = all(report["gates"].values())
    return report, residuals


def output_bytes(report: dict[str, Any], residuals: list[dict[str, Any]], manifest: dict[str, Any]) -> dict[str, bytes]:
    manifest_snapshot = {key: value for key, value in manifest.items() if not key.startswith("_")}
    return {
        "input_manifest.json": canonical_json(manifest_snapshot),
        "transcript_perfection_corpus_report.json": canonical_json(report),
        "transcript_perfection_corpus_report.md": markdown(report).encode("utf-8"),
        "residual_ranking.jsonl": canonical_jsonl(residuals),
    }


def main() -> int:
    args = parse_args()
    manifest_path = args.manifest.resolve()
    manifest = read_json(manifest_path)
    validate_manifest(manifest)
    manifest["_path"] = str(manifest_path)
    manifest["_sha256"] = sha256(manifest_path)
    verified, payloads, failures = verify_sources(manifest)
    semantic_checks: list[dict[str, Any]] = []
    if not failures:
        semantic_checks, semantic_failures = semantic_gates(payloads, verified)
        failures.extend(semantic_failures)
    report, residuals = build_report(manifest, verified, payloads, semantic_checks, failures)
    outputs = output_bytes(report, residuals, manifest)
    if args.verify_existing:
        mismatches = [name for name, content in outputs.items() if not (args.out_dir / name).is_file() or (args.out_dir / name).read_bytes() != content]
        if mismatches:
            print("transcript perfection outputs are missing or stale: " + ", ".join(mismatches), file=sys.stderr)
            return 2
    else:
        args.out_dir.mkdir(parents=True, exist_ok=True)
        for name, content in outputs.items():
            (args.out_dir / name).write_bytes(content)
    print(f"decision: {report['decision']}")
    print(f"sources: {report['summary']['verified_sources']}/{report['summary']['required_sources']} verified")
    print(f"release_ready: {str(report['release']['ready']).lower()}")
    if residuals:
        winner = residuals[0]
        print(f"largest_actionable_residual: {winner['class']} ({float(winner['seconds']):.3f}s)")
        print(f"next_goal: {report['next_goal']['title']}")
    print(f"report: {portable_path(args.out_dir / 'transcript_perfection_corpus_report.md')}")
    return 0 if report["gates"]["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
