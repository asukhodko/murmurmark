#!/usr/bin/env python3
"""Check that primary ASR and repair progress remain distinguishable."""

from __future__ import annotations

import importlib.util
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


PIPELINE = load_module(
    "murmurmark_pipeline_progress_check",
    SCRIPTS / "run-session-pipeline.py",
)


def write_chunk_report(
    session: Path,
    *,
    track: str = "mic",
    completed: int,
    total: int,
) -> None:
    path = (
        session
        / f"derived/transcript-simple/whisper-cpp/raw/chunks/{track}/chunk_cache_report.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    chunks = [
        {
            "status": "transcribed" if index < completed else "missing",
            "hard_start_ms": index * 1_000,
            "hard_end_ms": (index + 1) * 1_000,
        }
        for index in range(total)
    ]
    path.write_text(
        json.dumps(
            {
                "track": track,
                "status": "completed" if completed == total else "running",
                "chunks_total": total,
                "chunks_completed": completed,
                "chunks_reused": 0,
                "chunks_transcribed": completed,
                "total_sec": float(total),
                "completed_hard_sec": float(completed),
                "chunks": chunks,
            }
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="murmurmark-transcribe-progress-") as raw_root:
        session = Path(raw_root) / "session"
        preparing = PIPELINE.transcribe_stage_progress(session, active=True)
        assert preparing is not None
        assert preparing["stage"] == "preparing_audio_or_asr_cache"

        write_chunk_report(session, completed=2, total=5)
        primary = PIPELINE.transcribe_stage_progress(session, active=True)
        assert primary is not None
        assert primary["schema"] == "murmurmark.transcribe_stage_observation/v1"
        assert primary["stage"] == "primary_asr"
        assert primary["status"] == "running"
        assert primary["items_completed"] == 2
        assert primary["items_total"] == 10
        assert primary["remaining_audio_sec"] == 8.0
        observed = PIPELINE.transcribe_chunk_progress(session)
        assert observed is not None
        assert {row["track"] for row in observed["tracks"]} == {"mic", "remote"}

        write_chunk_report(session, completed=5, total=5)
        still_primary = PIPELINE.transcribe_stage_progress(session, active=True)
        assert still_primary is not None
        assert still_primary["stage"] == "primary_asr"
        assert still_primary["items_completed"] == 5
        assert still_primary["items_total"] == 10

        write_chunk_report(session, track="remote", completed=5, total=5)
        repair = PIPELINE.transcribe_stage_progress(session, active=True)
        assert repair is not None
        assert repair["status"] == "running"
        assert repair["stage"] == "post_primary_timeline_and_micro_asr"
        assert repair["primary_asr_status"] == "completed"

        checkpoint = PIPELINE.checkpoint_progress_for_step(
            step_name="transcribe_current",
            session=session,
            report_path=session / "derived/pipeline-run/pipeline_run_report.json",
            processing_lease={
                "schema": "murmurmark.processing_lease/v1",
                "status": "acquired",
                "capture_blocked": False,
            },
        )
        assert checkpoint["asr_stage"]["stage"] == "post_primary_timeline_and_micro_asr"
        assert checkpoint["processing_lease"]["status"] == "acquired"
        assert checkpoint["processing_lease"]["capture_blocked"] is False

        resolved = session / "derived/transcript-simple/whisper-cpp/resolved"
        resolved.mkdir(parents=True, exist_ok=True)
        (resolved / "transcript.md").write_text("# Transcript\n", encoding="utf-8")
        (resolved / "transcribe_simple_report.json").write_text("{}\n", encoding="utf-8")
        completed = PIPELINE.transcribe_stage_progress(session, active=False)
        assert completed is not None
        assert completed["status"] == "completed"
        assert completed["stage"] == "completed"

    transcriber = SCRIPTS / "transcribe-simple-whispercpp.py"
    policy = json.loads(
        (ROOT / "policies/speaker-preserving-neural-echo-v2-17.json").read_text(
            encoding="utf-8"
        )
    )
    assert hashlib.sha256(transcriber.read_bytes()).hexdigest() == policy[
        "transcriber_runtime_sha256"
    ]

    swift_source = (ROOT / "Sources/MurmurMarkCLI/MurmurMarkCLI.swift").read_text(
        encoding="utf-8"
    )
    assert "primary_asr_chunks:" in swift_source
    assert "asr_stage:" in swift_source
    print("transcribe progress checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
