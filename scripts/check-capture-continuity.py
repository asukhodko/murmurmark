#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
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


def write_session(session: Path) -> None:
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
    mic[int(99.4 * sample_rate) : int(100.2 * sample_rate)] = 0.0
    remote[int(99.45 * sample_rate) : int(100.15 * sample_rate)] = 0.0
    sf.write(session / "audio/mic/000001.caf", mic, sample_rate, format="CAF", subtype="PCM_16")
    sf.write(session / "audio/remote/000001.caf", remote, sample_rate, format="CAF", subtype="PCM_16")
    manifest = {
        "schema": "murmurmark.session/v1",
        "created_at": "2026-08-07T12:00:00Z",
        "health": {"actual_duration_sec": duration, "screen_capture_restart_count": 1},
        "files": {
            "mic": [{"path": "audio/mic/000001.caf"}],
            "remote": [{"path": "audio/remote/000001.caf"}],
        },
    }
    session.joinpath("session.json").write_text(json.dumps(manifest), encoding="utf-8")
    session.joinpath("events.jsonl").write_text(
        json.dumps(
            {
                "type": "capture.restarted",
                "t": "2026-08-07T12:01:40Z",
                "reason": "stream_stopped",
                "restart_count": 1,
            }
        )
        + "\n",
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
        assert report["status"] == "warning", report
        assert report["screen_capture_restart_count"] == 1, report
        assert report["observed_gap_count"] == 1, report
        assert 0.79 <= report["observed_gap_seconds"] <= 0.81, report
        assert report["gaps"][0]["sources"] == ["mic", "remote"], report
        assert report["partial_recommended"] is False, report
        report_path = session / "derived/audit/capture-continuity/capture_continuity_report.json"
        report_path.parent.mkdir(parents=True)
        report_path.write_text(json.dumps(report), encoding="utf-8")
        manifest = json.loads((session / "session.json").read_text(encoding="utf-8"))
        metrics = quality.capture_continuity_metrics(session, manifest)
        assert metrics["capture_continuity_status"] == "warning", metrics
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
        assert gate["status"] == "pass" and gate["blocking"] is False, gate
        readiness["metrics"] = dict(readiness["metrics"])
        readiness["metrics"]["capture_continuity_status"] = "partial_recommended"
        readiness["metrics"]["capture_continuity_partial_recommended"] = True
        severe_gates = outcome.evaluate_gates(session, readiness, {"status": "passed"})
        severe_gate = next(row for row in severe_gates if row.get("id") == "capture_continuity")
        assert severe_gate["status"] == "review" and severe_gate["blocking"] is True, severe_gate
    print("capture continuity checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
