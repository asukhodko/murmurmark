#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
from argparse import Namespace
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts/audit-capture-continuity.py"
QUALITY_SCRIPT = REPO_ROOT / "scripts/report-session-quality.py"
OUTCOME_SCRIPT = REPO_ROOT / "scripts/evaluate-outcome.py"


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load capture continuity audit")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_session(session: Path, *, with_restart_gap: bool = True) -> None:
    sample_rate = 16000
    # Keep the fixture meeting-sized so a sub-second restart gap remains a
    # warning instead of crossing the report's severe gap-ratio threshold.
    duration = 200.0
    session.joinpath("audio/mic").mkdir(parents=True)
    session.joinpath("audio/remote").mkdir(parents=True)
    samples = int(sample_rate * duration)
    timeline = np.arange(samples, dtype=np.float32) / sample_rate
    base = (0.02 * np.sin(2 * np.pi * 220 * timeline)).astype(np.float32)
    mic = base.copy()
    remote = base.copy()
    if with_restart_gap:
        mic[int(99.4 * sample_rate) : int(100.2 * sample_rate)] = 0.0
        remote[int(99.45 * sample_rate) : int(100.15 * sample_rate)] = 0.0
    sf.write(session / "audio/mic/000001.caf", mic, sample_rate, format="CAF", subtype="PCM_16")
    sf.write(session / "audio/remote/000001.caf", remote, sample_rate, format="CAF", subtype="PCM_16")
    manifest = {
        "schema": "murmurmark.session/v1",
        "created_at": "2026-08-07T12:00:00Z",
        "health": {
            "actual_duration_sec": duration,
            "screen_capture_restart_count": 1 if with_restart_gap else 0,
            "capture_complete": not with_restart_gap,
            "capture_gap_seconds": 0.8 if with_restart_gap else 0.0,
            "capture_gaps": (
                [
                    {
                        "start_sec": 99.4,
                        "end_sec": 100.2,
                        "duration_sec": 0.8,
                        "sources": ["mic", "remote"],
                        "evidence": "writer_inserted_timeline_silence",
                        "captured_audio": False,
                    }
                ]
                if with_restart_gap
                else []
            ),
        },
        "files": {
            "mic": [{"path": "audio/mic/000001.caf"}],
            "remote": [{"path": "audio/remote/000001.caf"}],
        },
    }
    session.joinpath("session.json").write_text(json.dumps(manifest), encoding="utf-8")
    events: list[dict[str, Any]] = []
    if with_restart_gap:
        phases = [
            ("requested", None, 1_000_000_000),
            ("old_stream_already_stopped", None, 1_001_000_000),
            ("start_requested", None, 1_003_000_000),
            ("start_completed", None, 1_050_000_000),
            ("terminal", None, 1_051_000_000),
            ("first_callback", "mic", 1_060_000_000),
            ("first_committed_pcm", "mic", 1_061_000_000),
            ("first_callback", "remote", 1_062_000_000),
            ("first_committed_pcm", "remote", 1_063_000_000),
        ]
        for sequence, (phase, source, monotonic_ns) in enumerate(phases, start=1):
            row: dict[str, Any] = {
                "type": "capture.restart_provenance",
                "t": "2026-08-07T12:01:39.500Z",
                "sequence": sequence,
                "monotonic_ns": monotonic_ns,
                "attempt_id": 1,
                "reason": "stream_stopped",
                "phase": phase,
            }
            if phase == "requested":
                row["session_offset_sec"] = 99.5
                row["expected_sources"] = ["mic", "remote"]
            if phase == "terminal":
                row["terminal_status"] = "started"
            if source:
                row["source"] = source
            if phase == "first_committed_pcm":
                row["captured_audio"] = True
                row["gap_frames"] = 12800
            events.append(row)
        events.append(
            {
                "type": "capture.restarted",
                "t": "2026-08-07T12:01:40Z",
                "sequence": len(events) + 1,
                "monotonic_ns": 1_064_000_000,
                "attempt_id": 1,
                "reason": "stream_stopped",
                "restart_count": 1,
            }
        )
    session.joinpath("events.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in events),
        encoding="utf-8",
    )


def main() -> int:
    module = load_module(SCRIPT, "murmurmark_capture_continuity")
    quality = load_module(QUALITY_SCRIPT, "murmurmark_capture_continuity_quality")
    outcome = load_module(OUTCOME_SCRIPT, "murmurmark_capture_continuity_outcome")
    with tempfile.TemporaryDirectory(prefix="murmurmark-capture-continuity-") as temporary:
        session = Path(temporary) / "session"
        write_session(session)
        args = Namespace(
            session=session,
            out_dir=None,
            search_before_sec=2.5,
            search_after_sec=1.5,
            zero_threshold=1e-12,
            min_gap_sec=0.05,
        )
        report = module.analyze(args)
        assert report["status"] == "capture_incomplete", report
        assert report["source"] == "session_manifest", report
        assert report["capture_complete"] is False, report
        assert report["terminal_completeness_gate"] == "review", report
        assert report["screen_capture_restart_count"] == 1, report
        assert report["observed_gap_count"] == 1, report
        assert 0.79 <= report["observed_gap_seconds"] <= 0.81, report
        assert report["gaps"][0]["sources"] == ["mic", "remote"], report
        assert report["gaps"][0]["captured_audio"] is False, report
        assert report["partial_recommended"] is False, report
        assert report["restart_provenance_status"] == "complete", report
        assert report["restart_latency"]["max_software_idle_ms"] == 2.0, report
        report_path = session / "derived/audit/capture-continuity/capture_continuity_report.json"
        report_path.parent.mkdir(parents=True)
        report_path.write_text(json.dumps(report), encoding="utf-8")
        transcript_path = (
            session
            / "derived/transcript-simple/whisper-cpp/resolved/transcript.md"
        )
        transcript_path.parent.mkdir(parents=True)
        transcript_path.write_text("# Transcript\n\n## 00:00 Me\n\nПроверка.\n", encoding="utf-8")
        binary = REPO_ROOT / ".build/debug/murmurmark"
        if binary.exists():
            rendered = subprocess.run(
                [
                    str(binary),
                    "transcript",
                    str(session),
                    "--profile",
                    "current",
                    "--cat",
                ],
                cwd=REPO_ROOT,
                check=True,
                text=True,
                capture_output=True,
            )
            assert rendered.stdout.startswith("> [!WARNING]\n> capture is incomplete"), rendered.stdout
            assert "# Transcript" in rendered.stdout, rendered.stdout
            assert "warning: capture is incomplete" in rendered.stderr, rendered.stderr
        manifest = json.loads((session / "session.json").read_text(encoding="utf-8"))
        metrics = quality.capture_continuity_metrics(session, manifest)
        assert metrics["capture_continuity_status"] == "capture_incomplete", metrics
        assert metrics["capture_continuity_complete"] is False, metrics
        assert metrics["capture_continuity_gap_count"] == 1, metrics
        readiness = {
            "selected_profile": "reviewed_v1",
            "pipeline_status": "complete",
            "verdict": "good",
            "use_gate": "ready_for_notes",
            "metrics": metrics | {
                "meeting_duration_sec": 200.0,
                "review_burden_sec": 0.0,
                "review_burden_ratio": 0.0,
                "local_only_island_recall": 1.0,
            },
            "outputs": {},
            "risk_flags": [],
            "export_blockers": [],
        }
        gates = outcome.evaluate_gates(session, readiness, {"status": "passed"})
        gate = next(row for row in gates if row.get("id") == "capture_continuity")
        assert gate["status"] == "review" and gate["blocking"] is True, gate

        single_source = Path(temporary) / "single-source"
        single_source.mkdir()
        single_source_events = [
            {
                "type": "capture.restart_provenance",
                "sequence": index,
                "monotonic_ns": monotonic_ns,
                "attempt_id": 1,
                "reason": "stream_stopped",
                "phase": phase,
                **({"expected_sources": ["remote"]} if phase == "requested" else {}),
                **({"source": "remote"} if source else {}),
                **({"terminal_status": "started"} if phase == "terminal" else {}),
                **({"captured_audio": True} if phase == "first_committed_pcm" else {}),
            }
            for index, (phase, source, monotonic_ns) in enumerate(
                [
                    ("requested", None, 1_000_000_000),
                    ("old_stream_already_stopped", None, 1_001_000_000),
                    ("start_requested", None, 1_002_000_000),
                    ("start_completed", None, 1_010_000_000),
                    ("terminal", None, 1_011_000_000),
                    ("first_callback", "remote", 1_012_000_000),
                    ("first_committed_pcm", "remote", 1_013_000_000),
                ],
                start=1,
            )
        ]
        single_source.joinpath("events.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in single_source_events),
            encoding="utf-8",
        )
        provenance_status, attempts = module.restart_provenance(single_source)
        assert provenance_status == "complete", attempts
        assert attempts[0]["expected_sources"] == ["remote"], attempts

        complete_session = Path(temporary) / "complete-session"
        write_session(complete_session, with_restart_gap=False)
        complete_args = Namespace(
            session=complete_session,
            out_dir=None,
            search_before_sec=2.5,
            search_after_sec=1.5,
            zero_threshold=1e-12,
            min_gap_sec=0.05,
        )
        complete_report = module.analyze(complete_args)
        assert complete_report["status"] == "ok", complete_report
        assert complete_report["capture_complete"] is True, complete_report
        assert complete_report["terminal_completeness_gate"] == "pass", complete_report
    print("capture continuity checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
