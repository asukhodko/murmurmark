#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SYNTHESIZE = REPO_ROOT / "scripts/synthesize-simple-extractive.py"
PROFILE = "audit_cleanup_v2"


def load_stabilization_check() -> Any:
    path = REPO_ROOT / "scripts/check-current-pipeline-stabilization.py"
    spec = importlib.util.spec_from_file_location("murmurmark_pipeline_stabilization", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_fixture(session: Path) -> None:
    mic_path = session / "audio/mic/000001.caf"
    remote_path = session / "audio/remote/000001.caf"
    mic_path.parent.mkdir(parents=True, exist_ok=True)
    remote_path.parent.mkdir(parents=True, exist_ok=True)
    mic_path.write_bytes(b"mic-capture-present")
    remote_path.write_bytes(b"remote-capture-present")

    write_json(
        session / "session.json",
        {
            "schema": "murmurmark.session/v1",
            "status": "completed_with_warnings",
            "health": {
                "actual_duration_sec": 120.0,
                "explicit_stop": True,
                "partial": False,
                "screen_capture_restart_count": 0,
                "summary": "warning",
                "warnings": ["remote track appears silent or almost silent (RMS -inf dB)"],
                "tracks": {
                    "mic": {"duration_sec": 120.0, "empty": False},
                    "remote": {"duration_sec": 120.0, "empty": False},
                },
            },
            "files": {
                "mic": [{"path": "audio/mic/000001.caf"}],
                "remote": [{"path": "audio/remote/000001.caf"}],
            },
        },
    )

    resolved = session / "derived/transcript-simple/whisper-cpp/resolved"
    suffix = f".{PROFILE}"
    write_json(
        resolved / f"clean_dialogue{suffix}.json",
        {"schema": "murmurmark.clean_dialogue/v1", "session": session.name, "utterances": []},
    )
    write_json(
        resolved / f"quality_report{suffix}.json",
        {
            "schema": "murmurmark.simple_transcript_quality/v1",
            "utterances": 0,
            "needs_review_count": 0,
            "local_only_island_recall": 1.0,
        },
    )
    write_json(
        resolved / f"overlaps{suffix}.json",
        {"schema": "murmurmark.transcript_overlaps/v1", "session": session.name, "overlaps": []},
    )
    write_json(
        resolved.parent / "audit-cleanup" / f"audit_cleanup_report{suffix}.json",
        {"schema": "murmurmark.audit_cleanup_report/v1", "gates": {"passed": True}, "summary": {}},
    )
    write_json(
        resolved / "transcribe_simple_report.json",
        {
            "schema": "murmurmark.transcribe_simple_report/v1",
            "utterances": 0,
            "dropped_segments": 4,
            "dropped_by_reason": {"known_hallucination": 4},
        },
    )
    write_json(
        session / "derived/preprocess/echo/local_fir_report.json",
        {
            "schema": "murmurmark.local_fir_report/v1",
            "summary": {"mic_floor_db": -58.0, "remote_floor_db": -120.0},
            "metrics": {"finite": True, "max_abs_clean": 0.2},
        },
    )
    write_json(
        session / "derived/audit/local-recall/local_recall_audit.json",
        {
            "schema": "murmurmark.local_recall_audit/v1",
            "summary": {
                "possible_lost_me_count": 0,
                "possible_lost_me_seconds": 0.0,
                "needs_review_count": 0,
                "needs_review_seconds": 0.0,
                "independent_live_me_evidence_count": 0,
            },
        },
    )
    write_json(
        session / "derived/transcript-simple/whisper-cpp/raw/chunk_rebuild_check.json",
        {
            "schema": "murmurmark.whisper_cpp_chunk_rebuild_check/v1",
            "status": "passed",
            "tracks": [
                {"track": "mic", "status": "pass"},
                {"track": "remote", "status": "pass"},
            ],
        },
    )


def run_synthesis(session: Path) -> None:
    subprocess.run(
        [sys.executable, str(SYNTHESIZE), str(session), "--transcript-profile", PROFILE],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="murmurmark-no-speech-") as raw_root:
        session = Path(raw_root) / "sessions/no-show"
        build_fixture(session)
        run_synthesis(session)

        out_dir = session / "derived/synthesis-simple/extractive"
        canonical = read_json(out_dir / "quality_verdict.json")
        profile = read_json(out_dir / f"quality_verdict.{PROFILE}.json")
        evidence = read_json(out_dir / "no_speech_evidence.json")
        notes = (out_dir / f"notes.{PROFILE}.md").read_text(encoding="utf-8")
        assert canonical["verdict"] == "good", canonical
        assert canonical["session_classification"] == "verified_no_speech", canonical
        assert profile["session_classification"] == "verified_no_speech", profile
        assert evidence["status"] == "verified_no_speech", evidence
        assert not any(row.get("type") == "empty_transcript" for row in canonical["risk_items"]), canonical
        assert "No speech was detected" in notes, notes
        stabilization = load_stabilization_check()
        assert stabilization.has_verified_no_speech(
            session,
            {"session_classification": "verified_no_speech"},
        )

        local_fir_path = session / "derived/preprocess/echo/local_fir_report.json"
        broken_local_fir = read_json(local_fir_path)
        broken_local_fir["summary"]["mic_floor_db"] = -120.0
        broken_local_fir["metrics"]["max_abs_clean"] = 0.0
        write_json(local_fir_path, broken_local_fir)
        run_synthesis(session)

        canonical = read_json(out_dir / "quality_verdict.json")
        profile = read_json(out_dir / f"quality_verdict.{PROFILE}.json")
        evidence = read_json(out_dir / "no_speech_evidence.json")
        assert canonical["verdict"] == "failed", canonical
        assert profile["verdict"] == "failed", profile
        assert canonical["session_classification"] == "unverified_empty_transcript", canonical
        assert evidence["status"] == "unverified_empty_transcript", evidence
        assert "mic_acoustic_liveness" in evidence["failures"], evidence
        assert any(row.get("type") == "empty_transcript" for row in canonical["risk_items"]), canonical
        assert not stabilization.has_verified_no_speech(
            session,
            {"session_classification": "verified_no_speech"},
        )

    print("no-speech outcome check ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
