#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORTER = ROOT / "scripts/report-canonical-live-asr-corpus.py"


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")


def fixture_session(root: Path, index: int) -> Path:
    session = root / f"session-{index}"
    for track in ("mic", "remote"):
        path = session / f"audio/{track}/000001.caf"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"{track}-{index}".encode("ascii"))
    write_json(
        session / "session.json",
        {"schema": "murmurmark.session/v1", "health": {"actual_duration_sec": 60.0}},
    )
    producer = session / "derived/experiments/live-shadow-v1/authoritative-asr"
    write_json(
        producer / "report.json",
        {
            "schema": "murmurmark.canonical_live_asr_producer_report/v1",
            "status": "completed_replay",
            "progress": {
                "chunks_completed": 1,
                "chunks_expected": 1,
                "proven_sec": 60.0,
                "decode_elapsed_sec": 10.0,
            },
        },
    )
    (producer / "chunks.jsonl").write_text(
        json.dumps(
            {
                "schema": "murmurmark.authoritative_live_asr_window/v1",
                "index": 1,
                "provenance": "historical_replay",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    write_json(
        session / "derived/live/live_asr_cache_report.json",
        {
            "schema": "murmurmark.live_asr_cache_report/v1",
            "verify_only": True,
            "verified_tracks": ["remote"],
            "fallback_tracks": ["mic"],
            "track_compatibility": {"remote": {"eligible": True}},
        },
    )
    return session


def invoke(sessions: list[Path], output: Path, manifest: Path, *, refresh: bool = False) -> dict:
    model = output.parent / "model.bin"
    whisper = output.parent / "whisper-cli"
    model.write_bytes(b"model")
    whisper.write_bytes(b"binary")
    command = [
        sys.executable,
        str(REPORTER),
        *map(str, sessions),
        "--output",
        str(output),
        "--frozen-manifest",
        str(manifest),
        "--model",
        str(model),
        "--whisper-cli",
        str(whisper),
    ]
    if refresh:
        command.append("--refresh-manifest")
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL)
    return json.loads((output / "canonical_live_asr_corpus_report.json").read_text(encoding="utf-8"))


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="murmurmark-canonical-live-corpus-") as temporary:
        root = Path(temporary)
        sessions = [fixture_session(root, index) for index in range(1, 4)]
        output = root / "report"
        manifest = root / "manifest.json"
        report = invoke(sessions, output, manifest, refresh=True)
        assert report["decision"]["status"] == "DO_NOT_PROMOTE", report
        assert report["summary"]["strict_remote_verified_sessions"] == 3, report
        assert report["summary"]["recording_time_sessions"] == 0, report
        assert report["summary"]["frozen_inputs_matching_sessions"] == 3, report
        assert manifest.is_file()

        (sessions[0] / "audio/remote/000001.caf").write_bytes(b"changed")
        changed = invoke(sessions, output, manifest)
        assert changed["summary"]["frozen_inputs_matching_sessions"] == 2, changed
        assert "raw_sha256_changed_from_frozen_manifest" in changed["sessions"][0]["reasons"], changed

    print("canonical live ASR corpus checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
