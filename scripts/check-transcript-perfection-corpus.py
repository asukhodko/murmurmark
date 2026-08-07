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
    with tempfile.TemporaryDirectory(prefix=".transcript-perfection-fixture-", dir=ROOT) as temporary:
        root = Path(temporary)
        manifest = build_fixture(root)
        out = root / "out"
        result = run(manifest, out)
        assert result.returncode == 0, result.stdout + result.stderr
        report = json.loads((out / "transcript_perfection_corpus_report.json").read_text())
        assert report["decision"] == "BASELINE_ESTABLISHED"
        assert report["summary"]["verified_sources"] == 12
        assert report["summary"]["aggregate_quality_score"] is None
        assert report["summary"]["aggregate_residual_seconds"] is None
        words = next(row for row in report["dimensions"] if row["id"] == "recognized_words")
        assert words["correctness_status"] == "not_measured"
        assert report["residuals"][0]["class"] == "unknown_remote_speaker"
        assert report["next_goal"]["id"] == "remote-speaker-residual-evidence-v4"
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
