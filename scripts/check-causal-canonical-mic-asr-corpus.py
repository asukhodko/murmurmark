#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORTER = ROOT / "scripts/report-causal-canonical-mic-asr-corpus.py"


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")


def fixture_session(root: Path, index: int) -> Path:
    session = root / f"session-{index}"
    for track in ("mic", "remote"):
        raw = session / f"audio/{track}/000001.caf"
        raw.parent.mkdir(parents=True, exist_ok=True)
        raw.write_bytes(f"{track}-{index}".encode("ascii"))
        canonical = session / f"derived/asr/{track}.wav"
        canonical.parent.mkdir(parents=True, exist_ok=True)
        canonical.write_bytes(f"canonical-{track}-{index}".encode("ascii"))
    write_json(session / "session.json", {"schema": "murmurmark.session/v1"})
    write_json(
        session / "derived/preprocess/echo/local_fir_report.json",
        {"schema": "murmurmark.local_fir_report/v1"},
    )
    write_json(
        session / "derived/preprocess/echo/echo_suppression_selection.json",
        {"schema": "murmurmark.echo_suppression_selection/v1", "selected": "local_fir_role_masked"},
    )
    write_json(
        session / "derived/pipeline-run/pipeline_run_report.json",
        {
            "schema": "murmurmark.pipeline_run_report/v1",
            "steps": [
                {"name": "echo_preprocess", "duration_sec": 30.0},
                {"name": "speaker_preserving_neural_echo_v2_prepare", "duration_sec": 2.0},
                {"name": "transcribe_current", "duration_sec": 60.0},
            ],
        },
    )
    report = session / "derived/experiments/live-shadow-v1/authoritative-mic-asr/report.json"
    write_json(
        report,
        {
            "schema": "murmurmark.causal_canonical_mic_asr_report/v1",
            "selected_profile": "local_fir_role_masked",
            "lineage": {
                "minimum_future_context": {"kind": "session_end", "bounded_sec": None}
            },
            "prefix_probe": {
                "status": "completed",
                "rows": [
                    {"lookahead_sec": 5, "exact": False},
                    {"lookahead_sec": 30, "exact": False},
                    {"lookahead_sec": 120, "exact": False},
                ],
            },
            "candidate": {"recording_time_evidence": False},
            "summary": {
                "windows_total": 10,
                "exact_windows": 0,
                "total_hard_sec": 600.0,
                "exact_hard_sec": 0.0,
                "exact_hard_ratio": 0.0,
                "proofs_published": 0,
            },
            "safety": {"raw_capture_unchanged": True},
        },
    )
    return session


def invoke(sessions: list[Path], output: Path, manifest: Path, *, refresh: bool = False) -> dict:
    command = [
        sys.executable,
        str(REPORTER),
        *map(str, sessions),
        "--output",
        str(output),
        "--frozen-manifest",
        str(manifest),
    ]
    if refresh:
        command.append("--refresh-manifest")
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return json.loads(
        (output / "causal_canonical_mic_asr_corpus_report.json").read_text(encoding="utf-8")
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="murmurmark-causal-mic-corpus-") as temporary:
        root = Path(temporary)
        sessions = [fixture_session(root, index) for index in range(1, 4)]
        output = root / "report"
        manifest = root / "manifest.json"
        report = invoke(sessions, output, manifest, refresh=True)
        assert report["decision"]["status"] == "DO_NOT_PROMOTE", report
        assert report["summary"]["sessions"] == 3, report
        assert report["summary"]["frozen_inputs_matching_sessions"] == 3, report
        assert report["summary"]["exact_windows"] == 0, report
        assert report["decision"]["gates"]["raw_capture_integrity"] is True, report
        assert report["decision"]["gates"]["bounded_future_context_proven"] is False, report
        assert manifest.is_file()

        (sessions[0] / "audio/mic/000001.caf").write_bytes(b"changed")
        changed = invoke(sessions, output, manifest)
        assert changed["summary"]["frozen_inputs_matching_sessions"] == 2, changed
        assert "inputs_changed_from_frozen_manifest" in changed["sessions"][0]["reasons"], changed

    print("causal canonical mic ASR corpus checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
