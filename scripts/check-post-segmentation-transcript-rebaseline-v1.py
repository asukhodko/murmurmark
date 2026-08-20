#!/usr/bin/env python3
"""Regression checks for the read-only post-segmentation transcript rebaseline."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REPORTER = ROOT / "scripts/report-post-segmentation-transcript-rebaseline-v1.py"
BASE_POLICY = ROOT / "policies/post-segmentation-transcript-rebaseline-v1.json"


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical(value))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def artifact(session: Path, path: Path) -> dict[str, Any]:
    return {
        "path": str(path.relative_to(session)),
        "exists": True,
        "bytes": path.stat().st_size,
        "sha256": sha(path),
    }


def run(*arguments: str, expected: int = 0) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        [sys.executable, str(REPORTER), *arguments],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != expected:
        raise AssertionError(
            f"unexpected exit {completed.returncode}, expected {expected}:\n"
            f"stdout={completed.stdout}\nstderr={completed.stderr}"
        )
    return completed


def build_session(root: Path, name: str, speakers: int, fallback: bool = False) -> Path:
    session = root / name
    aggregate = session / "derived/transcript-simple/whisper-cpp/resolved/transcript.reviewed_v1.md"
    dialogue = session / "derived/transcript-simple/whisper-cpp/resolved/clean_dialogue.reviewed_v1.json"
    raw_mic = session / "audio/mic/000001.caf"
    raw_remote = session / "audio/remote/000001.caf"
    aggregate.parent.mkdir(parents=True, exist_ok=True)
    raw_mic.parent.mkdir(parents=True, exist_ok=True)
    raw_remote.parent.mkdir(parents=True, exist_ok=True)
    aggregate.write_text("# aggregate fixture\n", encoding="utf-8")
    write_json(dialogue, {"utterances": []})
    raw_mic.write_bytes(b"fixed raw mic")
    raw_remote.write_bytes(b"fixed raw remote")
    write_json(
        session / "session.json",
        {
            "schema": "murmurmark.session/v1",
            "status": "completed",
            "health": {"summary": "ok", "actual_duration_sec": 120.0},
        },
    )
    write_json(
        session / "derived/audit/capture-continuity/capture_continuity_report.json",
        {
            "schema": "murmurmark.capture_continuity/v1",
            "status": "ok",
            "capture_duration_sec": 120.0,
            "observed_gap_count": 0,
            "observed_gap_seconds": 0.0,
        },
    )
    write_json(
        session / "derived/audit/order/transcript_order_audit.json",
        {
            "schema": "murmurmark.transcript_order_audit/v1",
            "summary": {
                "probable_order_risk_count": 0,
                "probable_order_risk_seconds": 0.0,
                "needs_review_count": 0,
                "needs_review_seconds": 0.0,
                "blocking_order_risk": False,
            },
        },
    )
    write_json(
        session / "derived/readiness/review-plan/review_plan.json",
        {"schema": "murmurmark.review_plan/v1", "clusters": [], "summary": {}},
    )
    write_json(
        session / "derived/readiness/review-plan/review_decisions_progress.json",
        {
            "schema": "murmurmark.review_decisions_progress/v1",
            "summary": {"remaining": 0, "remaining_seconds": 0.0},
        },
    )
    write_json(
        session / "derived/readiness/session_readiness.json",
        {
            "schema": "murmurmark.session_readiness/v1",
            "selected_profile": "reviewed_v1",
            "verdict": "usable_with_review",
            "use_gate": "review_first",
            "metrics": {"manual_review_queue_rows": 0, "manual_review_queue_seconds": 0.0},
        },
    )
    if fallback:
        provisional_md = (
            session
            / "derived/transcript-rich/speaker-resolved-default-v1/provisional/transcript.provisional.md"
        )
        provisional_json = provisional_md.with_suffix(".json")
        provisional_md.parent.mkdir(parents=True, exist_ok=True)
        provisional_md.write_text(
            "# Speaker view\n\n> [!WARNING]\n> **Speaker attribution is provisional.**\n",
            encoding="utf-8",
        )
        write_json(provisional_json, {"schema": "murmurmark.provisional_speaker_transcript/v1"})
        write_json(
            session / "derived/transcript-rich/speaker-resolved-default-v1/provisional/selection.json",
            {
                "schema": "murmurmark.provisional_speaker_transcript_selection/v1",
                "state": "provisional",
                "selected_profile": "reviewed_v1",
                "selected_speaker_profile": "remote_speaker_provisional_v1",
                "selected_transcript": artifact(session, provisional_md),
                "aggregate_transcript": artifact(session, aggregate),
                "summary": {
                    "speaker_clusters": speakers,
                    "remote_speech_sec": 80.0,
                    "attributed_remote_speech_sec": 40.0,
                    "attributed_remote_speech_ratio": 0.5,
                },
            },
        )
        selection = {
            "schema": "murmurmark.speaker_resolved_transcript_selection/v1",
            "state": "fallback",
            "selected_profile": "reviewed_v1",
            "selected_speaker_profile": "aggregate_colleagues",
            "fallback_reason": "fixture_low_coverage",
            "semantic_fingerprint": f"fixture-{name}",
            "selected_transcript": artifact(session, aggregate),
            "aggregate_transcript": artifact(session, aggregate),
            "selected_dialogue": artifact(session, dialogue),
            "gates": {
                "aggregate_unchanged": True,
                "current_profile": True,
                "exact_aggregate_fallback": True,
                "speaker_evidence_promoted": False,
            },
        }
    else:
        coverage_dir = (
            session
            / "derived/transcript-rich/speaker-resolved-default-v1/evidence/fixture/remote-speaker-coverage-v3"
        )
        rich_md = coverage_dir / "transcript.rich.shadow.md"
        rich_json = coverage_dir / "transcript.rich.shadow.json"
        coverage = coverage_dir / "report.json"
        speaker_map = coverage_dir / "speaker_map.json"
        coverage_dir.mkdir(parents=True, exist_ok=True)
        rich_md.write_text("# attributed fixture\n", encoding="utf-8")
        write_json(rich_json, {"utterances": []})
        write_json(speaker_map, {"speakers": []})
        write_json(
            coverage,
            {
                "schema": "murmurmark.remote_speaker_coverage_report/v3",
                "decision": "PUBLISH_EVIDENCE",
                "summary": {
                    "published_speakers": speakers,
                    "remote_speech_sec": 100.0,
                    "remote_words": 200,
                    "remaining_unknown_seconds": 10.0,
                    "remaining_unknown_words": 20,
                    "attributable_remote_speech_ratio": 0.9,
                    "internal_change_utterances": 1 if speakers > 1 else 0,
                },
                "unknown_causes": [
                    {"cause": "embedding_unavailable", "seconds": 10.0, "words": 20}
                ],
                "gates": {
                    "selected_text_unchanged": True,
                    "word_conservation": True,
                    "word_timestamps_unchanged": True,
                    "me_unchanged": True,
                    "timestamp_order": True,
                    "remote_overlap_preserved": True,
                },
            },
        )
        selection = {
            "schema": "murmurmark.speaker_resolved_transcript_selection/v1",
            "state": "selected",
            "selected_profile": "reviewed_v1",
            "selected_speaker_profile": "remote_speaker_coverage_v3",
            "semantic_fingerprint": f"fixture-{name}",
            "selected_transcript": artifact(session, rich_md),
            "aggregate_transcript": artifact(session, aggregate),
            "selected_dialogue": artifact(session, dialogue),
            "coverage_report": artifact(session, coverage),
            "rich_transcript": artifact(session, rich_json),
            "gates": {
                "aggregate_unchanged": True,
                "current_profile": True,
                "exact_aggregate_fallback": False,
                "speaker_evidence_promoted": True,
            },
        }
    write_json(session / "derived/transcript-rich/speaker-resolved-default-v1/selection.json", selection)
    return session


def main() -> int:
    with tempfile.TemporaryDirectory(prefix=".post-segmentation-rebaseline-", dir=ROOT) as raw:
        fixture = Path(raw)
        sessions = fixture / "sessions"
        first = build_session(sessions, "2026-08-19_10-00-00", 1)
        second = build_session(sessions, "2026-08-19_11-00-00", 3)
        third = build_session(sessions, "2026-08-19_12-00-00", 2, fallback=True)
        raw_hashes = {
            path: sha(path)
            for session in (first, second, third)
            for path in (session / "audio/mic/000001.caf", session / "audio/remote/000001.caf")
        }
        policy = json.loads(BASE_POLICY.read_text(encoding="utf-8"))
        controls = fixture / "controls"
        controls.mkdir(parents=True)
        control_payloads = {
            "boundary_minority_terminal_report": {"decision": "KEEP_COVERAGE_V3"},
            "coverage_v3_frozen_baseline": {
                "decision": "PROMOTE",
                "summary": {"remote_speech_sec": 1000.0, "remaining_unknown_seconds": 60.0},
            },
            "transcript_perfection_baseline": {"decision": "BASELINE_ESTABLISHED"},
        }
        for index, control in enumerate(policy["controls"], start=1):
            path = controls / f"control_{index:02d}.json"
            write_json(path, control_payloads.get(control["id"], {"fixture": control["id"]}))
            control["path"] = str(path.relative_to(ROOT))
            control["sha256"] = sha(path)
        policy["discovery"].update(
            {
                "minimum_session_name": "2026-08-19_00-00-00",
                "minimum_sessions": 3,
                "maximum_sessions": 3,
            }
        )
        policy_path = fixture / "policy.json"
        write_json(policy_path, policy)
        out = fixture / "out"
        snapshot = fixture / "snapshot.json"
        common = (
            "all",
            "--sessions-root",
            str(sessions),
            "--policy",
            str(policy_path),
            "--out-dir",
            str(out),
            "--snapshot",
            str(snapshot),
        )
        run(*common, "--refresh", "--write-snapshot")
        report = json.loads((out / "post_segmentation_rebaseline_report.json").read_text())
        assert report["decision"] == "REBASELINE_ESTABLISHED"
        assert report["summary"]["included_sessions"] == 3
        assert report["summary"]["strict_rich_sessions"] == 2
        assert report["summary"]["provisional_sessions"] == 1
        assert report["next_priority"]["axis"] == "remote_unknown_evidence"
        assert report["gates"]["read_surfaces_coherent"] is True
        assert report["gates"]["capture_complete"] is True
        public_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in out.iterdir()
            if path.is_file()
        )
        assert "2026-08-19_10-00-00" not in public_text
        assert str(fixture) not in public_text
        assert "fixed raw" not in public_text
        run(*common, "--verify-existing")
        assert all(sha(path) == expected for path, expected in raw_hashes.items())

        aggregate = first / "derived/transcript-simple/whisper-cpp/resolved/transcript.reviewed_v1.md"
        aggregate.write_text("changed fixture\n", encoding="utf-8")
        run(*common, "--verify-existing", expected=2)
        assert all(sha(path) == expected for path, expected in raw_hashes.items())

    swift = (ROOT / "Sources/MurmurMarkCLI/MurmurMarkCLI.swift").read_text(encoding="utf-8")
    assert 'case "post-segmentation-rebaseline"' in swift
    print("post-segmentation transcript rebaseline checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
