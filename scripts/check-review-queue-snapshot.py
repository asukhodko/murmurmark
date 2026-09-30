#!/usr/bin/env python3
"""Queue accounting: unresolved answers, overlaps, unknowns and stale inputs."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

from review_audio_evidence import read_queue_snapshot


def load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main():
    progress = load("report-review-decisions-progress")
    quality = load("report-session-quality")
    outcome = load("evaluate-outcome")
    reconcile = load("reconcile-session-state")
    with tempfile.TemporaryDirectory(prefix="murmurmark-review-queue-") as directory:
        session = Path(directory)
        (session / "session.json").write_text("{}\n")
        plan = session / "derived/readiness/review-plan"
        plan.mkdir(parents=True)
        template, decisions = plan / "review_decisions.template.jsonl", plan / "review_decisions.jsonl"
        rows = [
            {"session_id": "fixture", "source": "audio_review", "utterance_ids": ["a"],
             "interval": {"start": 10, "end": 20}, "decision": "todo"},
            {"session_id": "fixture", "source": "transcript_order", "utterance_ids": ["b"],
             "interval": {"start": 15, "end": 25}, "decision": "needs_review"},
            {"session_id": "fixture", "source": "transcript_text", "utterance_ids": ["c"],
             "decision": "keep_me", "review_source": "workspace_suggested_answers"},
            {"session_id": "fixture", "source": "audio_review", "utterance_ids": ["d"],
             "interval": {"start": 40, "end": 50}, "decision": "keep_me", "review_source": "manual"},
        ]
        template.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
        report = progress.build_report(SimpleNamespace(template=template, decisions=decisions))
        progress.write_json(plan / "review_decisions_progress.json", report)
        snapshot = read_queue_snapshot(session)
        assert snapshot and snapshot["unresolved_rows"] == 3
        assert snapshot["known_interval_sum_seconds"] == 20
        assert snapshot["known_interval_union_seconds"] == 15
        assert snapshot["interval_union_seconds"] is None
        assert snapshot["unknown_duration_rows"] == 1
        assert quality.read_review_progress(session)["queue_snapshot"] == snapshot
        out = outcome.build_review_plan(session, None, "blocked")
        assert out["queue_snapshot"] == snapshot
        assert out["summary"]["estimated_seconds"] is None
        assert out["summary"]["unknown_duration_lanes"] == 1
        out = outcome.build_review_plan(session, {"non_actionable_blockers": ["missing_capture"]}, "blocked")
        assert out["queue_snapshot"] == snapshot
        for target in ("readiness/session_readiness.json", "outcome/outcome.json"):
            progress.write_json(session / "derived" / target, {"review_queue_snapshot": snapshot})
        checks = reconcile.verify_consistency(session)["checks"]
        assert checks["review_queue_snapshot_current"] and checks["review_queue_snapshot_agrees"]
        binary = Path(__file__).resolve().parents[1] / ".build/debug/murmurmark"
        command = [str(binary), "outcome", str(session)]
        cli = subprocess.run(command, capture_output=True, text=True, check=True)
        assert "3 tasks; interval_sum=unknown; unique_audio=unknown; unknown_durations=1" in cli.stdout, cli.stdout
        progress.write_json(session / "derived/outcome/outcome.json", {"review_queue_snapshot": {}})
        assert not reconcile.verify_consistency(session)["checks"]["review_queue_snapshot_agrees"]
        # Content changes invalidate even if a timestamp is preserved elsewhere.
        decisions.write_text(json.dumps({**rows[0], "decision": "skip", "review_source": "manual"}) + "\n")
        assert read_queue_snapshot(session) is None
        assert quality.read_review_progress(session) == {}
        assert not reconcile.verify_consistency(session)["checks"]["review_queue_snapshot_current"]
        progress.write_json(session / "derived/outcome/outcome.json", {"review_queue_snapshot": snapshot})
        cli = subprocess.run(command, capture_output=True, text=True, check=True)
        assert "review_queue: stale" in cli.stdout and "3 tasks" not in cli.stdout, cli.stdout
        rows[2]["interval"] = {"start": 50, "end": 55}
        template.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
        report = progress.build_report(SimpleNamespace(template=template, decisions=decisions))
        snapshot = report["queue_snapshot"]
        assert snapshot["unresolved_rows"] == 2
        assert snapshot["interval_union_seconds"] == snapshot["interval_sum_seconds"] == 15
        assert snapshot["duration_complete"] is True
    print("review queue snapshot checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
