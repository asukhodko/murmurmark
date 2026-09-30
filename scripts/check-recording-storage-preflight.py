#!/usr/bin/env python3
"""Exercise recording-start cleanup only on synthetic sessions."""

from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace


SCRIPTS = Path(__file__).resolve().parent


def load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


preflight = load("recording_storage_preflight", "preflight-recording-storage.py")
fixtures = load("compaction_fixtures", "check-derived-compaction.py")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="murmurmark-storage-preflight-") as temporary:
        root = Path(temporary)
        sessions = root / "sessions"
        pinned = fixtures.write_session(root, "2026-01-01_10-00-00")
        blocked = fixtures.write_session(root, "2026-01-02_10-00-00")
        ready = fixtures.write_session(root, "2026-01-03_10-00-00")
        active = fixtures.write_session(root, "2026-01-04_10-00-00")
        debug = fixtures.write_session(root, "2026-01-05_10-00-00")
        recent = fixtures.write_session(root, "2026-12-01_10-00-00")
        external = fixtures.write_session(root / "external", "2026-01-06_10-00-00")
        (sessions / external.name).symlink_to(external, target_is_directory=True)
        recent_manifest = json.loads((recent / "session.json").read_text(encoding="utf-8"))
        recent_manifest["ended_at"] = "2026-12-01T10:00:00Z"
        fixtures.write_json(recent / "session.json", recent_manifest)
        (active / "session.lock").write_text("active\n", encoding="utf-8")
        fixtures.write_json(
            debug / "derived/meeting-lifecycle/state.json",
            {"keep_debug_artifacts": True},
        )
        fixtures.write_json(
            sessions / "_reports/pins/pinned_sessions.json",
            {"sessions": [pinned.name]},
        )
        outcome_path = ready / "derived/outcome/outcome.json"
        outcome = json.loads(outcome_path.read_text(encoding="utf-8"))
        outcome.update(
            {
                "verdict": "good",
                "readiness": {"transcript": "ready", "export": "allowed"},
            }
        )
        outcome["summary"]["export_blockers"] = []
        fixtures.write_json(outcome_path, outcome)
        review_outcome_path = blocked / "derived/outcome/outcome.json"
        review_outcome = json.loads(review_outcome_path.read_text(encoding="utf-8"))
        review_outcome.update(
            {
                "verdict": "usable_with_review",
                "readiness": {"transcript": "ready", "export": "allowed"},
            }
        )
        review_outcome["summary"]["export_blockers"] = []
        fixtures.write_json(review_outcome_path, review_outcome)

        protected_files = [
            pinned / "audio/mic/000001.caf",
            pinned / "derived/preprocess/audio/mic_for_asr.wav",
            active / "audio/mic/000001.caf",
            active / "derived/preprocess/audio/mic_for_asr.wav",
            debug / "audio/mic/000001.caf",
            debug / "derived/preprocess/audio/mic_for_asr.wav",
            recent / "audio/mic/000001.caf",
            recent / "derived/preprocess/audio/mic_for_asr.wav",
            external / "audio/mic/000001.caf",
            external / "derived/preprocess/audio/mic_for_asr.wav",
        ]
        candidates = [
            blocked / "derived/preprocess/audio/mic_for_asr.wav",
            blocked / "derived/live/chunks/000001.caf",
            ready / "derived/preprocess/audio/mic_for_asr.wav",
            ready / "derived/live/chunks/000001.caf",
            ready / "audio/mic/000001.caf",
            ready / "audio/remote/000001.caf",
        ]
        emergency_candidates = [
            blocked / "audio/mic/000001.caf",
            blocked / "audio/remote/000001.caf",
        ]
        sizes = {path: path.stat().st_size for path in candidates + emergency_candidates}

        def usage(_path: Path):
            reclaimed = sum(size for path, size in sizes.items() if not path.exists())
            return SimpleNamespace(total=50_000, free=10_000 + reclaimed)

        capture = sessions / "2026-09-18_10-00-00"
        pins, _ = preflight.compaction.discover_pins(sessions, [])
        assert pinned.name in pins
        assert {path.name for path in preflight.old_sessions(sessions, capture, pins)} == {
            blocked.name, ready.name
        }
        assert preflight.run_preflight(
            capture, sessions, usage=usage, trigger_bytes=9_000, target_bytes=23_000
        ) == 0
        assert all(path.exists() for path in candidates)

        result = preflight.run_preflight(
            capture, sessions, usage=usage, trigger_bytes=15_000, target_bytes=23_000
        )
        report_path = sessions / "_reports/retention-compaction/recording_storage_preflight.json"
        assert result == 0, report_path.read_text(encoding="utf-8")
        assert all(path.exists() for path in protected_files)
        assert all(not path.exists() for path in candidates)
        assert (blocked / "audio/mic/000001.caf").is_file()
        for session in (pinned, blocked, ready, active, debug, recent):
            assert (session / "derived/transcript-simple/whisper-cpp/resolved/transcript.reviewed_v1.md").is_file()
        report = json.loads(
            (sessions / "_reports/retention-compaction/recording_storage_preflight.json").read_text(
                encoding="utf-8"
            )
        )
        assert report["free_after_bytes"] >= report["target_bytes"]
        assert {row["session_id"] for row in report["sessions"] if row["status"] == "applied"} == {
            blocked.name, ready.name
        }
        assert not (active / "derived/retention/derived_compaction.json").exists()
        assert any(row["mode"] == "transcript_only" for row in report["sessions"])

        assert preflight.run_preflight(
            capture, sessions, usage=usage, trigger_bytes=24_000, target_bytes=25_000
        ) == 0
        assert not (blocked / "audio/mic/000001.caf").exists()
        assert not (blocked / "audio/remote/000001.caf").exists()
        assert (blocked / "derived/transcript-simple/whisper-cpp/resolved/transcript.reviewed_v1.md").is_file()
        emergency_report = json.loads(report_path.read_text(encoding="utf-8"))
        assert any(row.get("emergency") is True for row in emergency_report["sessions"])

        assert preflight.run_preflight(
            capture, sessions, usage=usage, trigger_bytes=26_000, target_bytes=30_000
        ) == 2
        assert all(path.exists() for path in protected_files)
        assert preflight.run_preflight(
            root / "other/recording", sessions, usage=usage,
            trigger_bytes=26_000, target_bytes=30_000,
        ) == 2
        assert all(path.exists() for path in protected_files)

    print("recording storage preflight checks ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
