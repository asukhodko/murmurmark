#!/usr/bin/env python3
"""Stage observation is bounded, run-scoped, and never claims decode counts."""

from pathlib import Path
import importlib.util
import sys
import tempfile
from unittest.mock import patch

from transcribe_observation import Observer


def main():
    with tempfile.TemporaryDirectory() as directory:
        session = Path(directory)
        root = session / "derived/transcript-simple/whisper-cpp"
        previous = root / "timeline-repair-shadow_v2/micro_reasr/stale.run.log"
        previous.parent.mkdir(parents=True)
        previous.write_text("old run")
        now = [100.0]
        observer = Observer(session, clock=lambda: now[0])
        now[0] = 102.0
        observer.poll()
        assert observer.report()["last_observed_activity"] == "preparing_or_cache_validation"
        assert not observer.report()["changed_artifacts_by_activity"]
        for phase, name in (("primary_asr", "raw/chunks/mic/chunk_cache_report.json"),
                            ("micro_current", "timeline-repair/micro_reasr/a.wav"),
                            ("micro_shadow", "timeline-repair-shadow_v2/micro_reasr/b.run.log"),
                            ("publication", "resolved/transcribe_simple_report.json")):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture")
            now[0] += 2
            observer.poll()
            assert observer.report()["last_observed_activity"] == phase
        now[0] += 1
        report = observer.report(final=True)
        assert report["elapsed_sec"] == 11
        assert sum(w["observed_window_sec"] for w in report["windows"]) == 11
        assert report["model_invocations"] is None and report["micro_cache_hits"] is None
        assert report["changed_artifacts_by_activity"] == {p: 1 for p in ("primary_asr", "micro_current", "micro_shadow", "publication")}
        assert Observer(session).report()["changed_artifacts_by_activity"] == {}
        with patch.object(Path, "stat", side_effect=PermissionError("denied")):
            failed = Observer(session)
        assert failed.report()["status"] == "unavailable"
        assert previous.read_text() == "old run"
        pipeline_path = Path(__file__).with_name("run-session-pipeline.py")
        spec = importlib.util.spec_from_file_location("pipeline_observation_test", pipeline_path)
        pipeline = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pipeline)
        result = pipeline.run_step(
            {"name": "transcribe_current", "enabled": True,
             "command": [sys.executable, "-c", "pass"]}, session, False,
            progress_interval_sec=60, session=session, report_path=session / "report.json",
            pipeline_started_at="fixture", pipeline_phase=pipeline.HANDOFF_PHASE,
        )
        assert result["status"] == "passed"
        assert result["asr_compute_observation"]["changed_artifacts_by_activity"] == {}
        assert result["asr_compute_observation"]["model_invocations"] is None
    print("transcribe observation checks passed")


if __name__ == "__main__":
    main()
