#!/usr/bin/env python3
"""Regression checks for the fingerprint-bound terminal-gate instrument."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv/bin/python"
SCRIPT = ROOT / "scripts/report-speaker-resolved-terminal-gate-v1.py"


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(arguments: list[str], expected: int = 0) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        [str(PYTHON), str(SCRIPT), *arguments],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != expected:
        raise AssertionError(
            f"unexpected exit {completed.returncode}, expected {expected}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return completed


def fixtures(root: Path, ready: bool) -> tuple[Path, Path, Path]:
    reports = root / "reports"
    rebaseline_input = reports / "rebaseline-input.json"
    write_json(rebaseline_input, {"schema": "fixture.rebaseline_input/v1", "version": 1})
    unknown_input = reports / "private/input_manifest.json"
    write_json(
        unknown_input,
        {
            "schema": "fixture.remote_unknown_input/v1",
            "rebaseline_manifest": {
                "path": str(rebaseline_input),
                "exists": True,
                "bytes": rebaseline_input.stat().st_size,
                "sha256": sha256(rebaseline_input),
            },
        },
    )
    chronology_input = reports / "chronology-private/input_manifest.json"
    chronology_source = reports / "chronology-source.json"
    chronology_empty = reports / "chronology-empty.jsonl"
    write_json(chronology_source, {"schema": "fixture.chronology_source/v1"})
    chronology_empty.write_bytes(b"")
    write_json(
        chronology_input,
        {
            "schema": "murmurmark.speaker_bounded_chronology_arbitration_input/v1",
            "policy": {
                "path": str(chronology_source),
                "exists": True,
                "bytes": chronology_source.stat().st_size,
                "sha256": sha256(chronology_source),
            },
            "implementation": {
                "path": str(chronology_source),
                "exists": True,
                "bytes": chronology_source.stat().st_size,
                "sha256": sha256(chronology_source),
            },
            "rebaseline_manifest": {
                "path": str(rebaseline_input),
                "exists": True,
                "bytes": rebaseline_input.stat().st_size,
                "sha256": sha256(rebaseline_input),
            },
            "sessions": [
                {
                    "alias": "session_01",
                    "artifacts": {
                        "order_items": {
                            "path": str(chronology_empty),
                            "exists": True,
                            "bytes": 0,
                            "sha256": sha256(chronology_empty),
                        }
                    },
                }
            ],
        },
    )
    localization_input = reports / "localization-private/input_manifest.json"
    localization_source = reports / "localization-source.json"
    localization_frozen = reports / "localization-private/frozen_items.jsonl"
    localization_clip = reports / "localization-private/clip.wav"
    localization_model = reports / "localization-private/model_identity.json"
    localization_model_dir = reports / "localization-private/model"
    localization_decodes = reports / "localization-private/word_decodes.jsonl"
    write_json(localization_source, {"schema": "fixture.localization_source/v1"})
    localization_frozen.parent.mkdir(parents=True, exist_ok=True)
    localization_frozen.write_bytes(b"")
    localization_clip.write_bytes(b"audio")
    localization_decodes.write_bytes(b"")
    localization_model_dir.mkdir()
    model_file = localization_model_dir / "model.bin"
    model_file.write_bytes(b"model")
    write_json(
        localization_model,
        {
            "schema": "fixture.model_identity/v1",
            "sha256": "model",
            "signature": [
                {
                    "path": "model.bin",
                    "bytes": model_file.stat().st_size,
                    "mtime_ns": model_file.stat().st_mtime_ns,
                }
            ],
        },
    )

    def artifact(path: Path) -> dict[str, Any]:
        return {
            "path": str(path), "exists": True, "bytes": path.stat().st_size,
            "sha256": sha256(path),
        }

    write_json(
        localization_input,
        {
            "schema": "murmurmark.word_level_chronology_localization_input/v1",
            "policy": artifact(localization_source),
            "implementation": artifact(localization_source),
            "frozen_items": artifact(localization_frozen),
            "upstream": {
                "upstream_report": artifact(localization_source),
                "upstream_private_items": artifact(localization_frozen),
                "upstream_input_manifest": artifact(chronology_input),
            },
            "model": {
                "path": str(localization_model_dir),
                "available": True,
                "sha256": "model",
                "identity": artifact(localization_model),
            },
            "clip_identities": [
                {
                    "alias": "session_01", "item_id": "order_0001",
                    "clips": {"mic_clean": artifact(localization_clip), "remote": artifact(localization_clip)},
                }
            ],
        },
    )
    values = {
        "post": {
            "schema": "murmurmark.post_segmentation_transcript_rebaseline_report/v1",
            "decision": "REBASELINE_ESTABLISHED",
            "dimensions": {
                "capture_completeness": {"gap_seconds": 0.0 if ready else 0.4},
                "overlap_and_chronology": {"chronology_seconds": 0.0 if ready else 2.0},
                "remote_speaker_topology": {
                    "published_speakers": 3,
                    "status": "measured_with_direct_truth" if ready else "measured_without_human_count_truth",
                },
                "review_burden": {"remaining_rows": 0 if ready else 2, "remaining_seconds": 0.0 if ready else 8.0},
            },
            "summary": {
                "capture_seconds": 1000.0,
                "included_sessions": 6,
                "strict_rich_sessions": 5,
                "provisional_sessions": 1,
                "aggregate_only_sessions": 0,
                "unknown_remote_words_coverage_v3": 2 if ready else 30,
                "unknown_remote_words_ratio": 0.01 if ready else 0.06,
                "unknown_remote_seconds_coverage_v3": 3.0 if ready else 70.0,
                "unknown_remote_seconds_ratio": 0.01 if ready else 0.07,
            },
            "gates": {"word_order_role_conserved": True, "read_surfaces_coherent": True},
        },
        "capture": {
            "schema": "murmurmark.capture_continuity_loss_closure_report/v1",
            "decision": "EVIDENCE_BOUND",
            "no_restart_soak": {"capture_complete": True},
            "controlled_restart": {"capture_complete": ready},
        },
        "echo": {
            "schema": "murmurmark.speaker_preserving_neural_echo_corpus_report/v2.17",
            "promotion": {"decision": "PROMOTE_SPEAKER_PRESERVING_NEURAL_ECHO_V2"},
            "checks": {"candidate_local_exact": True, "fallbacks_exact": True},
        },
        "local": {
            "schema": "murmurmark.residual_local_recall_corpus_report/v1",
            "decision": "PROMOTE_RESIDUAL_LOCAL_RECALL_V1",
            "summary": {"remaining_items": 0 if ready else 1, "remaining_seconds": 0.0 if ready else 1.2},
        },
        "lexical": {
            "schema": "murmurmark.human_reviewed_lexical_seed_report/v1",
            "decision": "REFERENCE_READY" if ready else "REVIEW_REQUIRED",
            "summary": {
                "answered_slots": 28 if ready else 0,
                "remaining_slots": 0 if ready else 28,
                "reference_words": 100 if ready else 0,
            },
            "metrics": {"overall": {"wer": 0.1, "cer": 0.04, "domain_terms": {"accuracy": 0.9}}} if ready else {},
        },
        "truth": {
            "schema": "murmurmark.remote_speaker_disjoint_truth_report/v2",
            "decision": "DIRECT_TRUTH_V2_READY",
            "gates": {"all_primary_answers": True, "all_repeat_answers": True, "repeat_consistency": True},
        },
        "unknown": {
            "schema": "murmurmark.remote_unknown_evidence_recovery_corpus_report/v1",
            "decision": "EVIDENCE_BOUND",
            "summary": {"frozen": {"remaining_unknown_words": 20}},
            "inputs": {
                "manifest": "private/input_manifest.json",
                "manifest_sha256": sha256(unknown_input),
            },
        },
        "chronology": {
            "schema": "murmurmark.speaker_bounded_chronology_arbitration_report/v1",
            "decision": "PROMOTE_CHRONOLOGY_EVIDENCE_ARBITRATION_V1",
            "summary": {
                "frozen_items": 4,
                "frozen_seconds": 2.0,
                "closed_items": 4 if ready else 2,
                "closed_seconds": 2.0 if ready else 1.0,
                "remaining_items": 0 if ready else 2,
                "remaining_seconds": 0.0 if ready else 1.0,
            },
            "inputs": {
                "manifest": "chronology-private/input_manifest.json",
                "manifest_sha256": sha256(chronology_input),
            },
        },
        "localization": {
            "schema": "murmurmark.word_level_chronology_localization_report/v1",
            "decision": "PROMOTE_WORD_LEVEL_CHRONOLOGY_LOCALIZATION_V1",
            "summary": {
                "frozen_items": 2,
                "frozen_seconds": 1.0,
                "closed_items": 2 if ready else 1,
                "closed_seconds": 1.0 if ready else 0.5,
                "remaining_items": 0 if ready else 1,
                "remaining_seconds": 0.0 if ready else 0.5,
            },
            "chronology": {
                "final_remaining_items": 0 if ready else 1,
                "final_remaining_seconds": 0.0 if ready else 0.5,
            },
            "inputs": {
                "manifest": "localization-private/input_manifest.json",
                "manifest_sha256": sha256(localization_input),
                "word_decodes": "localization-private/word_decodes.jsonl",
                "word_decodes_sha256": sha256(localization_decodes),
            },
        },
        "publication": {
            "schema": "murmurmark.speaker_resolved_transcript_default_corpus/v1",
            "decision": "PROMOTE",
            "gates": {"all_session_gates": True, "deterministic_replay": True},
        },
    }
    for identifier, value in values.items():
        write_json(reports / f"{identifier}.json", value)
    source_specs = [
        ("post_segmentation_rebaseline", "post", values["post"]["schema"]),
        ("capture_continuity_closure", "capture", values["capture"]["schema"]),
        ("speaker_preserving_echo", "echo", values["echo"]["schema"]),
        ("residual_local_recall", "local", values["local"]["schema"]),
        ("human_lexical_seed", "lexical", values["lexical"]["schema"]),
        ("remote_direct_truth", "truth", values["truth"]["schema"]),
        ("remote_unknown_recovery", "unknown", values["unknown"]["schema"]),
        ("chronology_arbitration", "chronology", values["chronology"]["schema"]),
        ("chronology_localization", "localization", values["localization"]["schema"]),
        ("speaker_resolved_publication", "publication", values["publication"]["schema"]),
    ]
    policy = {
        "schema": "murmurmark.speaker_resolved_terminal_gate_policy/v1",
        "version": 1,
        "sources": [
            {"id": identifier, "path": f"reports/{name}.json", "schema": schema}
            for identifier, name, schema in source_specs
        ],
        "dimensions": [
            "durable_capture", "target_me_preservation", "lexical_accuracy",
            "chronology_and_conservation", "remote_speaker_attribution", "explicit_unknown",
            "review_burden", "speaker_resolved_publication",
        ],
        "thresholds": {
            "maximum_capture_gap_seconds": 0.0,
            "maximum_remaining_local_recall_seconds": 0.0,
            "maximum_wer": 0.15,
            "maximum_cer": 0.08,
            "minimum_domain_term_accuracy": 0.9,
            "maximum_chronology_review_seconds": 0.0,
            "maximum_unknown_word_ratio": 0.05,
            "maximum_unknown_seconds_ratio": 0.05,
            "maximum_review_burden_ratio": 0.03,
            "minimum_fresh_sessions": 6,
        },
        "required_decisions": {
            "post_segmentation_rebaseline": "REBASELINE_ESTABLISHED",
            "speaker_preserving_echo": "PROMOTE_SPEAKER_PRESERVING_NEURAL_ECHO_V2",
            "residual_local_recall": "PROMOTE_RESIDUAL_LOCAL_RECALL_V1",
            "human_lexical_seed": "REFERENCE_READY",
            "remote_direct_truth": "DIRECT_TRUTH_V2_READY",
            "speaker_resolved_publication": "PROMOTE",
        },
        "privacy": {
            "public_session_ids": False,
            "public_absolute_paths": False,
            "public_speech_text": False,
            "public_speaker_names": False,
            "private_manifest_under_sessions": True,
        },
        "safety": {
            "read_only_sources": True,
            "raw_audio_mutation": False,
            "selected_transcript_mutation": False,
            "coverage_v3_mutation": False,
            "primary_asr_mutation": False,
            "echo_guard_mutation": False,
            "human_answer_mutation": False,
            "aggregate_quality_score": False,
            "cloud_inference": False,
        },
    }
    policy_path = root / "policy.json"
    out = root / "out"
    snapshot = root / "snapshot.json"
    write_json(policy_path, policy)
    return policy_path, out, snapshot


def exercise(root: Path, ready: bool) -> None:
    policy, out, snapshot = fixtures(root, ready)
    base = ["--policy", str(policy), "--out-dir", str(out), "--snapshot", str(snapshot)]
    run(["preflight", *base])
    run(["freeze", *base])
    run(["evaluate", "--write-snapshot", *base])
    report_path = out / "speaker_resolved_terminal_gate_report.json"
    report = read_json(report_path)
    assert report["decision"] == "TERMINAL_GATE_INSTRUMENT_READY"
    assert report["product_decision"] == ("READY" if ready else "NOT_READY")
    assert report["aggregate_quality_score"] is None
    assert len(report["dimensions"]) == 8
    assert report["next_command"] == (None if ready else "murmurmark corpus lexical-seed-v1 review")
    public = report_path.read_text(encoding="utf-8")
    assert str(root) not in public
    assert "private utterance" not in public
    run(["replay", "--write-snapshot", *base])

    rebaseline_input = root / "reports/rebaseline-input.json"
    original_rebaseline = rebaseline_input.read_bytes()
    write_json(rebaseline_input, {"schema": "fixture.rebaseline_input/v1", "version": 2})
    run(["evaluate", *base], expected=2)
    transitive_stale = read_json(report_path)
    assert transitive_stale["decision"] == "EVIDENCE_INCOMPLETE"
    states = {item["id"]: item for item in transitive_stale["dimensions"]}
    assert states["explicit_unknown"]["state"] == "not_measured"
    assert states["target_me_preservation"]["state"] == ("pass" if ready else "bounded")
    assert any(
        "upstream_rebaseline_manifest_stale" in blocker
        for blocker in states["explicit_unknown"]["blockers"]
    )
    rebaseline_input.write_bytes(original_rebaseline)
    run(["evaluate", *base])

    localization_decodes = root / "reports/localization-private/word_decodes.jsonl"
    original_decodes = localization_decodes.read_bytes()
    localization_decodes.write_bytes(b"stale")
    run(["evaluate", *base], expected=2)
    decode_stale = read_json(report_path)
    chronology = next(
        item for item in decode_stale["dimensions"] if item["id"] == "chronology_and_conservation"
    )
    assert chronology["state"] == "not_measured"
    assert any("word_decodes_stale" in blocker for blocker in chronology["blockers"])
    localization_decodes.write_bytes(original_decodes)
    run(["evaluate", *base])

    model_file = root / "reports/localization-private/model/model.bin"
    model_bytes = model_file.read_bytes()
    model_stat = model_file.stat()
    model_file.write_bytes(b"changed-model")
    run(["evaluate", *base], expected=2)
    model_stale = read_json(report_path)
    chronology = next(
        item for item in model_stale["dimensions"] if item["id"] == "chronology_and_conservation"
    )
    assert chronology["state"] == "not_measured"
    assert any("model_files_stale" in blocker for blocker in chronology["blockers"])
    model_file.write_bytes(model_bytes)
    os.utime(model_file, ns=(model_stat.st_atime_ns, model_stat.st_mtime_ns))
    run(["evaluate", *base])

    source = root / "reports/post.json"
    payload = read_json(source)
    payload["private"] = "private utterance"
    write_json(source, payload)
    run(["evaluate", *base], expected=2)
    stale = read_json(report_path)
    assert stale["decision"] == "EVIDENCE_INCOMPLETE"
    states = {item["id"]: item["state"] for item in stale["dimensions"]}
    assert states["durable_capture"] == "not_measured"
    assert states["chronology_and_conservation"] == "not_measured"
    assert states["remote_speaker_attribution"] == "not_measured"
    assert states["review_burden"] == "not_measured"
    assert states["speaker_resolved_publication"] == "not_measured"
    assert states["target_me_preservation"] == ("pass" if ready else "bounded")
    assert "private utterance" not in report_path.read_text(encoding="utf-8")


def malformed_policy(root: Path) -> None:
    policy, out, snapshot = fixtures(root, ready=True)
    payload = read_json(policy)
    payload["sources"] = payload["sources"][:-1]
    write_json(policy, payload)
    run([
        "preflight", "--policy", str(policy), "--out-dir", str(out),
        "--snapshot", str(snapshot),
    ], expected=2)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="murmurmark-terminal-gate-v1-") as raw:
        root = Path(raw)
        exercise(root / "not-ready", ready=False)
        exercise(root / "ready", ready=True)
        malformed_policy(root / "malformed")
    swift = (ROOT / "Sources/MurmurMarkCLI/MurmurMarkCLI.swift").read_text(encoding="utf-8")
    assert 'case "terminal-gate-v1"' in swift
    assert "report-speaker-resolved-terminal-gate-v1.py" in swift
    print("speaker-resolved terminal gate v1 checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
