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

from review_audio_evidence import read_queue_snapshot, digest


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
    readiness = load("report-operational-readiness")
    planner = load("build-review-plan")
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
        legacy = {**snapshot, "files": [item for item in snapshot["files"]
                                       if not item["path"].endswith("build-review-plan.py")]}
        legacy["fingerprint"] = digest({"files": legacy["files"], "items": legacy["items"]})
        progress.write_json(plan / "review_decisions_progress.json", {**report, "queue_snapshot": legacy})
        assert read_queue_snapshot(session) is None, "truncated legacy producers must not look current"
        progress.write_json(plan / "review_decisions_progress.json", report)
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
    with tempfile.TemporaryDirectory(prefix="murmurmark-review-limits-") as directory:
        session = Path(directory)
        dialogue = session / "derived/transcript-simple/whisper-cpp/resolved/clean_dialogue.reviewed_v1.json"
        dialogue.parent.mkdir(parents=True)
        # Disjoint intervals exceed BOTH legacy limits (40 rows, 80 clusters).
        utterances = [{"id": f"remote_{index:03d}", "role": "remote", "text": f"Words {index}.",
                       "start": index * 10.0, "end": index * 10.0 + 2.0,
                       "quality": {"needs_review": True}} for index in range(105)]
        dialogue.write_text(json.dumps({"utterances": utterances}))
        burdens = [{"session_id": "fixture", "session": str(session), "selected_profile": "reviewed_v1",
                    "use_gate": "ready_for_notes", "export_blockers": [], "transcript_review_burden_sec": 0}]
        complete, excluded = readiness.build_review_queue_details(burdens, 40)
        assert len(complete) == 105 and not excluded
        for limit in (0, 1, 40, 1000):
            assert readiness.build_review_queue_details(burdens, limit) == (complete, excluded)
            preview = readiness.select_review_queue(complete, limit)
            assert len(preview) == min(limit, 105)
        for limit in (0, 1, 80, 1000):
            args = SimpleNamespace(merge_gap_sec=4, listen_padding_sec=2, max_clusters=limit,
                                   operational_readiness=session / "readiness.json")
            plan = planner.build_plan({"review_queue": complete, "session_review_burden": burdens}, args)
            rows = planner.decision_template_rows(plan)
            assert len(rows) == plan["summary"]["cluster_count"] == 105
            template, decisions = session / "template.jsonl", session / "decisions.jsonl"
            planner.write_jsonl(template, rows)
            planner.write_jsonl(decisions, [{**row, "decision": "skip", "review_source": "manual"}
                                           for row in rows[:40]])
            snapshot = progress.build_report(SimpleNamespace(template=template, decisions=decisions))["queue_snapshot"]
            assert snapshot["unresolved_rows"] == 65, snapshot
            assert snapshot["interval_sum_seconds"] == snapshot["interval_union_seconds"] == 130
            markdown = session / "plan.md"
            planner.write_markdown(markdown, plan)
            assert f"Showing {min(limit, 105)} of 105 clusters." in markdown.read_text()
    print("review queue snapshot checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
