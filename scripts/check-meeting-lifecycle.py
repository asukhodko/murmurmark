#!/usr/bin/env python3
"""Exercise the one-command meeting supervisor without audio or ASR."""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SUPERVISOR = ROOT / "scripts" / "run-meeting-lifecycle.py"


FAKE_CLI = r'''#!/usr/bin/env python3
import json
import os
import signal
import sys
import time
from pathlib import Path


def write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def session_from(args):
    for value in reversed(args):
        candidate = Path(value)
        if (candidate / "session.json").is_file():
            return candidate.resolve()
    raise SystemExit("session argument missing")


def artifacts(session, ready, auto_rows):
    transcript = session / "derived/transcript-simple/whisper-cpp/resolved/transcript.fixture.md"
    dialogue = session / "derived/transcript-simple/whisper-cpp/resolved/clean_dialogue.fixture.json"
    notes = session / "derived/synthesis-simple/extractive/notes.fixture.md"
    verdict = session / "derived/synthesis-simple/extractive/quality_verdict.fixture.md"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    notes.parent.mkdir(parents=True, exist_ok=True)
    transcript.write_text("# Transcript\n", encoding="utf-8")
    notes.write_text("# Notes\n", encoding="utf-8")
    verdict.write_text("# Verdict\n", encoding="utf-8")
    write(dialogue, {
        "schema": "murmurmark.clean_dialogue/v1",
        "session": str(session),
        "utterances": [] if ready else [
            {
                "id": "utt_fixture_1",
                "role": "me",
                "start": 0.25,
                "end": 1.25,
                "text": "fixture text must not enter lifecycle events",
                "quality": {"needs_review": True, "decision_reason": "fixture_review"},
            },
            {
                "id": "utt_fixture_2",
                "role": "me",
                "start": 1.25,
                "end": 2.0,
                "text": "second private fixture",
                "quality": {"needs_review": True, "decision_reason": "fixture_review"},
            },
        ],
    })
    metrics = {
        "review_scope_remaining_rows": 0 if ready else 2,
        "transcript_review_burden_sec": 0.0 if ready else 4.5,
        "suggested_closure_auto_rows": auto_rows,
        "suggested_closure_actionable_rows": auto_rows,
        "needs_review_count": 0 if ready else 2,
    }
    readiness = {
        "schema": "murmurmark.session_readiness/v1",
        "generated_at": str(time.time_ns()),
        "use_gate": "ready_for_notes" if ready else "review_first",
        "selected_profile": "fixture",
        "verdict": "good" if ready else "usable_with_review",
        "review_blockers": [] if ready else ["fixture_review"],
        "export_blockers": [] if ready else ["fixture_review"],
        "metrics": metrics,
        "recommended_next": "touch SHOULD_NOT_RUN",
    }
    outcome = {
        "schema": "murmurmark.outcome/v1",
        "generated_at": str(time.time_ns()),
        "outcome": "ready_for_notes" if ready else "review_first",
        "selected_profile": "fixture",
        "verdict": "good" if ready else "usable_with_review",
        "export_status": "not_exported" if ready else "blocked_until_review",
        "summary": {"can_export": ready, "can_read_notes": True},
        "metrics": metrics,
        "outputs": {
            "transcript": {"path": str(transcript.relative_to(session)), "exists": True},
            "clean_dialogue": {"path": str(dialogue.relative_to(session)), "exists": True},
            "notes": {"path": str(notes.relative_to(session)), "exists": True},
            "quality_verdict": {"path": str(verdict.relative_to(session)), "exists": True},
        },
    }
    write(session / "derived/readiness/session_readiness.json", readiness)
    write(session / "derived/outcome/outcome.json", outcome)


args = sys.argv[1:]
command = args[0]
session = session_from(args)
scenario = os.environ.get("FAKE_MEETING_SCENARIO", "review")
with (session / "fake-cli.log").open("a", encoding="utf-8") as handle:
    handle.write(" ".join(args) + "\n")

if command == "inspect":
    raise SystemExit(0)
if command == "process":
    if scenario == "process_failed":
        raise SystemExit(9)
    if os.environ.get("FAKE_MEETING_SLEEP_PROCESS") == "1":
        signal_count = os.environ.get("FAKE_MEETING_SIGNAL_COUNT_FILE")
        signal_ready = os.environ.get("FAKE_MEETING_SIGNAL_READY_FILE")

        def handle_interrupt(signum, _frame):
            if signal_count:
                with Path(signal_count).open("a", encoding="utf-8") as handle:
                    handle.write(f"{signum}\n")
            raise SystemExit(128 + signum)

        signal.signal(signal.SIGINT, handle_interrupt)
        signal.signal(signal.SIGTERM, handle_interrupt)
        if signal_ready:
            Path(signal_ready).write_text("ready\n", encoding="utf-8")
        time.sleep(30)
    if scenario == "stale_process":
        raise SystemExit(0)
    if scenario == "checkpoint_reuse":
        runs = session / "derived/pipeline-run/authoritative_handoff_runs.jsonl"
        runs.parent.mkdir(parents=True, exist_ok=True)
        with runs.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"mode": "checkpoint_reuse", "at": time.time_ns()}) + "\n")
        raise SystemExit(0)
    artifacts(session, ready=False, auto_rows=0)
    write(
        session / "derived/pipeline-run/pipeline_run_report.json",
        {
            "schema": "murmurmark.session_pipeline_run/v1",
            "session": str(session),
            "status": "passed",
            "phase": "authoritative_handoff",
            "performance": {"deferred_stages_pending": ["fixture_enrich"]},
        },
    )
    raise SystemExit(0)
if command == "enrich":
    if scenario == "enrich_failed":
        raise SystemExit(7)
    if scenario == "enrich_slow":
        state_path = session / "derived/pipeline-run/pipeline_run_state.json"
        state = {
            "schema": "murmurmark.pipeline_run_state/v1",
            "status": "running",
            "phase": "deferred_enrichment",
            "message": "pipeline_started",
        }
        write(state_path, state)

        def interrupt_enrich(signum, _frame):
            state["status"] = "interrupted"
            state["message"] = "pipeline_finished"
            write(state_path, state)
            raise SystemExit(128 + signum)

        signal.signal(signal.SIGINT, interrupt_enrich)
        time.sleep(30)
    if scenario == "mutate_raw":
        with (session / "audio/mic/000001.caf").open("ab") as handle:
            handle.write(b"changed")
    report_path = session / "derived/pipeline-run/pipeline_run_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["phase"] = "full"
    report["performance"]["deferred_stages_pending"] = []
    write(report_path, report)
    write(
        session / "derived/pipeline-run/deferred_enrichment_report.json",
        {
            "schema": "murmurmark.session_pipeline_run/v1",
            "status": "passed",
            "phase": "deferred",
            "session": str(session),
        },
    )
    write(
        session / "derived/pipeline-run/authoritative_handoff.json",
        {"deferred_enrichment": {"status": "completed"}},
    )
    raise SystemExit(0)
if command == "outcome":
    if scenario == "stale_refresh":
        raise SystemExit(0)
    applied = (session / "fixture-applied").exists()
    auto_rows = 1 if scenario in {"ready", "stale_finish"} and not applied else 0
    artifacts(session, ready=applied, auto_rows=auto_rows)
    raise SystemExit(0)
if command == "review" and "preview" in args:
    auto_rows = 1 if scenario in {"ready", "stale_finish"} else 0
    artifacts(session, ready=False, auto_rows=auto_rows)
    write(
        session / "derived/readiness/review-plan/review_workspace_apply_report.json",
        {
            "schema": "murmurmark.review_workspace_apply_report/v1",
            "generated_at": str(time.time_ns()),
            "dry_run": True,
            "answers_source": "suggested",
            "suggested_closure": {"closed_by_suggestions": {"rows": auto_rows}},
        },
    )
    raise SystemExit(0)
if command == "review" and "apply" in args:
    (session / "fixture-applied").write_text("yes\n", encoding="utf-8")
    artifacts(session, ready=True, auto_rows=0)
    write(
        session / "derived/readiness/review-plan/review_workspace_apply_report.json",
        {
            "schema": "murmurmark.review_workspace_apply_report/v1",
            "generated_at": str(time.time_ns()),
            "dry_run": False,
            "answers_source": "suggested",
            "suggested_closure": {"closed_by_suggestions": {"rows": 1}},
        },
    )
    raise SystemExit(0)
if command == "finish":
    manifest = Path.cwd() / "exports/private" / session.name / "export_manifest.json"
    if scenario == "stale_finish":
        blocked = Path.cwd() / "exports/private" / f"{session.name}.export_blocked.json"
        write(blocked, {"schema": "murmurmark.export_manifest/v1", "status": "blocked"})
        raise SystemExit(0)
    session_payload = json.loads((session / "session.json").read_text(encoding="utf-8"))
    outcome = json.loads((session / "derived/outcome/outcome.json").read_text(encoding="utf-8"))
    write(manifest, {
        "schema": "murmurmark.export_manifest/v1",
        "status": "exported",
        "session_id": session_payload.get("session_id", session.name),
        "session": str(session),
        "selected_profile": outcome.get("selected_profile"),
        "blockers": [],
        "created_at": str(time.time()),
    })
    raise SystemExit(0)
raise SystemExit(0)
'''


def write_session(root: Path, name: str, partial: bool = False, warning: bool = False) -> Path:
    session = root / "sessions" / name
    mic = session / "audio/mic/000001.caf"
    remote = session / "audio/remote/000001.caf"
    mic.parent.mkdir(parents=True)
    remote.parent.mkdir(parents=True)
    mic.write_bytes(b"mic-fixture" * 32)
    remote.write_bytes(b"remote-fixture" * 32)
    health = {
        "actual_duration_sec": 2.0,
        "explicit_stop": not partial,
        "partial": partial,
        "summary": "partial" if partial else ("warning" if warning else "ok"),
        "warnings": ["fixture capture warning"] if warning else [],
        "tracks": {
            "mic": {"frames": 96000, "duration_sec": 2.0},
            "remote": {"frames": 96000, "duration_sec": 2.0},
        },
    }
    manifest = {
        "schema": "murmurmark.session/v1",
        "status": "partial" if partial else ("completed_with_warnings" if warning else "completed"),
        "health": health,
        "files": {
            "mic": [{"path": "audio/mic/000001.caf"}],
            "remote": [{"path": "audio/remote/000001.caf"}],
        },
    }
    (session / "session.json").write_text(json.dumps(manifest), encoding="utf-8")
    return session


def run_supervisor(
    root: Path,
    session: Path,
    fake: Path,
    scenario: str,
    *extra: str,
    env_extra: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["FAKE_MEETING_SCENARIO"] = scenario
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [
            sys.executable,
            str(SUPERVISOR),
            str(session),
            "--murmurmark-bin",
            str(fake),
            *extra,
        ],
        cwd=root,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def report(session: Path) -> dict:
    path = session / "derived/meeting-lifecycle/report.json"
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def seed_processed_artifacts(session: Path, *, deferred_complete: bool = False) -> None:
    transcript = session / "derived/transcript-simple/whisper-cpp/resolved/transcript.fixture.md"
    dialogue = session / "derived/transcript-simple/whisper-cpp/resolved/clean_dialogue.fixture.json"
    notes = session / "derived/synthesis-simple/extractive/notes.fixture.md"
    verdict = session / "derived/synthesis-simple/extractive/quality_verdict.fixture.md"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    notes.parent.mkdir(parents=True, exist_ok=True)
    transcript.write_text("# Transcript\n", encoding="utf-8")
    notes.write_text("# Notes\n", encoding="utf-8")
    verdict.write_text("# Verdict\n", encoding="utf-8")
    write_json(
        dialogue,
        {
            "schema": "murmurmark.clean_dialogue/v1",
            "session": str(session),
            "utterances": [
                {
                    "id": "utt_fixture_1",
                    "role": "me",
                    "start": 0.25,
                    "end": 1.25,
                    "quality": {"needs_review": True, "decision_reason": "fixture_review"},
                },
                {
                    "id": "utt_fixture_2",
                    "role": "me",
                    "start": 1.25,
                    "end": 2.0,
                    "quality": {"needs_review": True, "decision_reason": "fixture_review"},
                },
            ],
        },
    )
    metrics = {
        "review_scope_remaining_rows": 2,
        "transcript_review_burden_sec": 4.5,
        "suggested_closure_auto_rows": 0,
        "needs_review_count": 2,
    }
    write_json(
        session / "derived/readiness/session_readiness.json",
        {
            "schema": "murmurmark.session_readiness/v1",
            "generated_at": "seed",
            "use_gate": "review_first",
            "selected_profile": "fixture",
            "verdict": "usable_with_review",
            "review_blockers": ["fixture_review"],
            "export_blockers": ["fixture_review"],
            "metrics": metrics,
        },
    )
    write_json(
        session / "derived/outcome/outcome.json",
        {
            "schema": "murmurmark.outcome/v1",
            "generated_at": "seed",
            "outcome": "review_first",
            "selected_profile": "fixture",
            "verdict": "usable_with_review",
            "summary": {"can_export": False, "can_read_notes": True},
            "metrics": metrics,
            "outputs": {
                "transcript": {"path": str(transcript.relative_to(session)), "exists": True},
                "clean_dialogue": {"path": str(dialogue.relative_to(session)), "exists": True},
                "notes": {"path": str(notes.relative_to(session)), "exists": True},
                "quality_verdict": {"path": str(verdict.relative_to(session)), "exists": True},
            },
        },
    )
    write_json(
        session / "derived/pipeline-run/pipeline_run_report.json",
        {
            "schema": "murmurmark.session_pipeline_run/v1",
            "session": str(session.resolve()),
            "status": "passed",
            "phase": "handoff",
            "performance": {"deferred_stages_pending": ["fixture_enrich"]},
        },
    )
    if deferred_complete:
        write_json(
            session / "derived/pipeline-run/deferred_enrichment_report.json",
            {
                "schema": "murmurmark.session_pipeline_run/v1",
                "session": str(session.resolve()),
                "status": "passed",
                "phase": "deferred",
            },
        )
        write_json(
            session / "derived/pipeline-run/authoritative_handoff.json",
            {"deferred_enrichment": {"status": "completed"}},
        )


def wait_for_process_action(session: Path, process: subprocess.Popen[str]) -> None:
    state_path = session / "derived/meeting-lifecycle/state.json"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and process.poll() is None:
        if state_path.is_file():
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            if payload.get("current_action") == "process":
                return
        time.sleep(0.05)
    raise AssertionError("supervisor did not enter process action")


def wait_for_path(path: Path, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if path.is_file():
            return
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise AssertionError((process.returncode, stdout, stderr))
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {path}")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="murmurmark-meeting-lifecycle-") as temporary:
        root = Path(temporary)
        fake = root / "fake-murmurmark"
        fake.write_text(FAKE_CLI, encoding="utf-8")
        fake.chmod(0o755)

        ready_session = write_session(root, "ready")
        ready_run = run_supervisor(root, ready_session, fake, "ready")
        assert ready_run.returncode == 0, (ready_run.stdout, ready_run.stderr)
        ready_report = report(ready_session)
        assert ready_report["result"] == "ready"
        assert ready_report["raw"]["preserved"] is True
        assert ready_report["resume_available"] is False
        assert ready_report["elapsed_sec"]["capture"] == 2.0
        assert ready_report["elapsed_sec"]["capture_finalize"] == 0.0
        assert ready_report["elapsed_sec"]["authoritative_process"] >= 0
        assert ready_report["elapsed_sec"]["enrichment"] >= 0
        assert ready_report["elapsed_sec"]["total_after_stop"] >= 0
        assert ready_report["actions"]["review_suggested_apply"]["status"] == "passed"
        assert ready_report["actions"]["finish"]["status"] == "passed"
        assert ready_report["derived_compaction"]["status"] == "not_attempted"
        assert not (root / "SHOULD_NOT_RUN").exists()
        ready_state = json.loads(
            (ready_session / "derived/meeting-lifecycle/state.json").read_text(encoding="utf-8")
        )
        ready_next = json.loads(
            (ready_session / "derived/meeting-lifecycle/next_action.json").read_text(encoding="utf-8")
        )
        ready_events = [
            json.loads(line)
            for line in (ready_session / "derived/meeting-lifecycle/events.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
            if line
        ]
        assert ready_state["schema"] == "murmurmark.meeting_lifecycle_state/v1"
        assert ready_state["transition_count"] <= 16
        assert ready_next["schema"] == "murmurmark.meeting_next_action/v1"
        assert ready_next["decision"] == "terminal" and ready_next["action"] == "complete"
        assert ready_report["next"]["status"] == "complete"
        assert ready_report["manual_decisions"]["total"] == 0
        assert all(row["schema"] == "murmurmark.meeting_lifecycle_event/v1" for row in ready_events)
        assert {row["event"] for row in ready_events} >= {
            "lifecycle_started",
            "action_started",
            "action_passed",
            "raw_inputs_frozen",
            "raw_inputs_verified",
            "lifecycle_completed",
        }
        assert "fixture text must not enter lifecycle events" not in (
            ready_session / "derived/meeting-lifecycle/events.jsonl"
        ).read_text(encoding="utf-8")
        for action_state in ready_report["actions"].values():
            command = action_state.get("command")
            if not isinstance(command, list):
                continue
            assert not {"--full", "--force-asr", "--allow-partial"}.intersection(command)
            assert action_state.get("reason")
        log_before = (ready_session / "fake-cli.log").read_text(encoding="utf-8")
        repeat = run_supervisor(root, ready_session, fake, "ready")
        assert repeat.returncode == 0
        assert (ready_session / "fake-cli.log").read_text(encoding="utf-8") == log_before

        timing_session = write_session(root, "timing")
        timing_run = run_supervisor(
            root,
            timing_session,
            fake,
            "review",
            "--record-elapsed-sec",
            "2.75",
        )
        assert timing_run.returncode == 0, (timing_run.stdout, timing_run.stderr)
        timing_report = report(timing_session)
        assert timing_report["elapsed_sec"]["capture"] == 2.0
        assert timing_report["elapsed_sec"]["capture_finalize"] == 0.75
        assert timing_report["elapsed_sec"]["total_after_stop"] >= 0.75

        debug_session = write_session(root, "keep-debug")
        debug_run = run_supervisor(
            root,
            debug_session,
            fake,
            "ready",
            "--keep-debug-artifacts",
        )
        assert debug_run.returncode == 0, (debug_run.stdout, debug_run.stderr)
        debug_state = json.loads(
            (debug_session / "derived/meeting-lifecycle/state.json").read_text(encoding="utf-8")
        )
        assert debug_state["keep_debug_artifacts"] is True
        assert debug_state["resume_command"].endswith(" --keep-debug-artifacts")
        assert report(debug_session)["derived_compaction"]["status"] == "skipped_debug"
        debug_log = (debug_session / "fake-cli.log").read_text(encoding="utf-8")
        assert f"finish {debug_session.resolve()} --keep-debug-artifacts" in debug_log

        cli_value = os.environ.get("MURMURMARK_BIN")
        if cli_value:
            cli_bin = Path(cli_value).resolve()
            assert cli_bin.is_file() and os.access(cli_bin, os.X_OK), cli_bin
            cli_env = os.environ.copy()
            cli_env["MURMURMARK_HOME"] = str(ROOT)
            cli_env["MURMURMARK_PYTHON"] = sys.executable
            report_before = (ready_session / "derived/meeting-lifecycle/report.json").read_bytes()
            events_before = (ready_session / "derived/meeting-lifecycle/events.jsonl").read_bytes()
            cli_resume = subprocess.run(
                [str(cli_bin), "meeting", "--resume", str(ready_session)],
                cwd=root,
                env=cli_env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            assert cli_resume.returncode == 0, (cli_resume.stdout, cli_resume.stderr)
            assert "meeting:" in cli_resume.stdout
            assert "raw_capture: preserved" in cli_resume.stdout
            assert (ready_session / "fake-cli.log").read_text(encoding="utf-8") == log_before
            assert (ready_session / "derived/meeting-lifecycle/report.json").read_bytes() == report_before
            assert (ready_session / "derived/meeting-lifecycle/events.jsonl").read_bytes() == events_before

        review_session = write_session(root, "review")
        review_run = run_supervisor(root, review_session, fake, "review", "--max-transitions", "9")
        assert review_run.returncode == 0, (review_run.stdout, review_run.stderr)
        review_report = report(review_session)
        assert review_report["result"] == "ready_with_review"
        assert review_report["unresolved_review"]["count"] == 2
        assert review_report["unresolved_review"]["seconds"] == 4.5
        assert review_report["unresolved_review"]["blockers"] == ["fixture_review"]
        assert review_report["export"]["blockers"] == ["fixture_review"]
        assert review_report["reason"] == "structured_review_gate_remains"
        assert review_report["actions"]["review_suggested_apply"]["status"] == "skipped"
        assert review_report["actions"]["finish"]["status"] == "skipped"
        assert review_report["manual_decisions"]["total"] == 2
        assert review_report["next"]["status"] == "human_decision_required"
        review_next = json.loads(
            (review_session / "derived/meeting-lifecycle/next_action.json").read_text(encoding="utf-8")
        )
        assert review_next["decision"] == "human" and review_next["command"] is None
        assert all("text" not in item for item in review_report["manual_decisions"]["items"])
        if cli_value:
            cli_bin = Path(cli_value).resolve()
            cli_env = os.environ.copy()
            cli_env["MURMURMARK_HOME"] = str(ROOT)
            cli_env["MURMURMARK_PYTHON"] = sys.executable
            cli_status = subprocess.run(
                [str(cli_bin), "status", str(review_session)],
                cwd=root,
                env=cli_env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            assert cli_status.returncode == 0, (cli_status.stdout, cli_status.stderr)
            assert "status: human_decision_required" in cli_status.stdout
            assert "manual_decisions: 2 items / 4.50s" in cli_status.stdout
            assert "next: human_decision_required" in cli_status.stdout
            assert f"next: murmurmark status {review_session}" not in cli_status.stdout
            cli_next = subprocess.run(
                [str(cli_bin), "next", str(review_session)],
                cwd=root,
                env=cli_env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            assert cli_next.returncode == 0, (cli_next.stdout, cli_next.stderr)
            assert "status: human_decision_required" in cli_next.stdout
            assert "source: meeting_lifecycle" in cli_next.stdout
            assert "manual_decisions: 2 items / 4.50s" in cli_next.stdout
            assert "non_actionable_review_blocker" not in cli_next.stdout

        warning_session = write_session(root, "warning", warning=True)
        warning_run = run_supervisor(root, warning_session, fake, "review")
        assert warning_run.returncode == 0, (warning_run.stdout, warning_run.stderr)
        warning_report = report(warning_session)
        assert warning_report["actions"]["capture_validate"]["status"] == "passed"
        assert "capture: fixture capture warning" in warning_report["warnings"]

        partial_session = write_session(root, "partial", partial=True)
        partial_run = run_supervisor(root, partial_session, fake, "review")
        assert partial_run.returncode == 2
        assert report(partial_session)["result"] == "failed"
        assert not (partial_session / "fake-cli.log").exists()

        failed_session = write_session(root, "process-failed")
        failed_run = run_supervisor(root, failed_session, fake, "process_failed")
        assert failed_run.returncode == 2
        assert report(failed_session)["result"] == "failed"
        assert report(failed_session)["actions"]["process"]["status"] == "failed_hard"
        assert report(failed_session)["resume_available"] is True
        for _ in range(2):
            failed_retry = run_supervisor(root, failed_session, fake, "process_failed", "--resume")
            assert failed_retry.returncode == 2
        failed_log = (failed_session / "fake-cli.log").read_text(encoding="utf-8")
        exhausted = run_supervisor(root, failed_session, fake, "process_failed", "--resume")
        assert exhausted.returncode == 2
        assert report(failed_session)["reason"] == "hard_action_failed:process"
        assert report(failed_session)["resume_available"] is False
        assert (failed_session / "fake-cli.log").read_text(encoding="utf-8") == failed_log

        stale_process_session = write_session(root, "stale-process")
        seed_processed_artifacts(stale_process_session)
        stale_process_run = run_supervisor(root, stale_process_session, fake, "stale_process")
        assert stale_process_run.returncode == 2
        stale_process_report = report(stale_process_session)
        assert stale_process_report["actions"]["process"]["status"] == "failed_hard"
        assert "stale artifacts" in stale_process_report["actions"]["process"]["error"]

        checkpoint_session = write_session(root, "checkpoint-reuse")
        seed_processed_artifacts(checkpoint_session, deferred_complete=True)
        checkpoint_run = run_supervisor(root, checkpoint_session, fake, "checkpoint_reuse")
        assert checkpoint_run.returncode == 0, (checkpoint_run.stdout, checkpoint_run.stderr)
        checkpoint_report = report(checkpoint_session)
        assert checkpoint_report["result"] == "ready_with_review"
        assert checkpoint_report["actions"]["process"]["status"] == "passed"
        assert checkpoint_report["actions"]["enrich"]["status"] == "skipped"
        assert "checkpoint_reuse" in (
            checkpoint_session / "derived/pipeline-run/authoritative_handoff_runs.jsonl"
        ).read_text(encoding="utf-8")

        optional_session = write_session(root, "optional-failure")
        optional_run = run_supervisor(root, optional_session, fake, "enrich_failed")
        assert optional_run.returncode == 0, (optional_run.stdout, optional_run.stderr)
        optional_report = report(optional_session)
        assert optional_report["result"] == "ready_with_review"
        assert optional_report["actions"]["enrich"]["status"] == "failed_soft"
        assert any(item.startswith("enrich:") for item in optional_report["warnings"])

        budget_skip_session = write_session(root, "budget-skip")
        budget_skip_run = run_supervisor(
            root,
            budget_skip_session,
            fake,
            "review",
            "--post-stop-budget-ratio",
            "0",
        )
        assert budget_skip_run.returncode == 0, (
            budget_skip_run.stdout,
            budget_skip_run.stderr,
        )
        budget_skip_report = report(budget_skip_session)
        assert budget_skip_report["actions"]["enrich"]["status"] == "deferred_budget_exhausted"
        assert budget_skip_report["deferred_work"]["status"] == "deferred_budget_exhausted"
        assert budget_skip_report["deferred_work"]["blocking"] is False
        assert budget_skip_report["budgets"]["status"] == "enrichment_deferred_budget_exhausted"
        assert budget_skip_report["raw"]["preserved"] is True
        assert " enrich " not in f" {(budget_skip_session / 'fake-cli.log').read_text(encoding='utf-8')} "

        budget_timeout_session = write_session(root, "budget-timeout")
        budget_timeout_run = run_supervisor(
            root,
            budget_timeout_session,
            fake,
            "enrich_slow",
            "--post-stop-budget-ratio",
            "100",
            "--max-enrichment-budget-sec",
            "0.2",
        )
        assert budget_timeout_run.returncode == 0, (
            budget_timeout_run.stdout,
            budget_timeout_run.stderr,
        )
        budget_timeout_report = report(budget_timeout_session)
        assert budget_timeout_report["actions"]["enrich"]["status"] == "deferred_budget_exhausted"
        assert budget_timeout_report["deferred_work"]["status"] == "deferred_budget_exhausted"
        assert budget_timeout_report["budgets"]["enrichment_budget_sec"] <= 0.2
        assert budget_timeout_report["raw"]["preserved"] is True
        assert "Traceback" not in budget_timeout_run.stderr
        budget_pipeline_state_path = (
            budget_timeout_session / "derived/pipeline-run/pipeline_run_state.json"
        )
        budget_pipeline_state = json.loads(
            budget_pipeline_state_path.read_text(encoding="utf-8")
        )
        assert budget_pipeline_state["status"] == "deferred_budget_exhausted"
        assert budget_pipeline_state["message"] == "deferred_enrichment_budget_exhausted"

        if cli_value:
            budget_pipeline_state["status"] = "interrupted"
            budget_pipeline_state["message"] = "pipeline_finished"
            write_json(budget_pipeline_state_path, budget_pipeline_state)
            lifecycle_report_path = budget_timeout_session / "derived/meeting-lifecycle/report.json"
            lifecycle_mtime = lifecycle_report_path.stat().st_mtime
            os.utime(budget_pipeline_state_path, (lifecycle_mtime - 1, lifecycle_mtime - 1))
            status_run = subprocess.run(
                [str(cli_bin), "status", str(budget_timeout_session)],
                cwd=root,
                env=cli_env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            assert status_run.returncode == 0, (status_run.stdout, status_run.stderr)
            assert "status: process_interrupted" not in status_run.stdout
            assert "can_read_notes: true" in status_run.stdout

        stale_finish_session = write_session(root, "stale-finish")
        stale_manifest = root / "exports/private" / stale_finish_session.name / "export_manifest.json"
        write_json(
            stale_manifest,
            {
                "schema": "murmurmark.export_manifest/v1",
                "status": "exported",
                "session_id": stale_finish_session.name,
                "session": str(stale_finish_session.resolve()),
                "selected_profile": "fixture",
                "blockers": [],
                "created_at": "old",
            },
        )
        stale_finish_run = run_supervisor(root, stale_finish_session, fake, "stale_finish")
        assert stale_finish_run.returncode == 0, (
            stale_finish_run.stdout,
            stale_finish_run.stderr,
        )
        stale_finish_report = report(stale_finish_session)
        assert stale_finish_report["result"] == "ready_with_review"
        assert stale_finish_report["reason"] == "guarded_export_not_completed"
        assert stale_finish_report["actions"]["finish"]["status"] == "failed_soft"
        assert stale_finish_report["export"]["status"] == "failed"
        assert stale_finish_report["export"]["manifest"] is None
        assert "stale export manifest" in stale_finish_report["actions"]["finish"]["error"]
        assert stale_finish_report["next"]["status"] == "action_required"
        assert stale_finish_report["next"]["command"].startswith("murmurmark finish ")

        stale_refresh_session = write_session(root, "stale-refresh")
        stale_refresh_run = run_supervisor(root, stale_refresh_session, fake, "stale_refresh")
        assert stale_refresh_run.returncode == 0, (
            stale_refresh_run.stdout,
            stale_refresh_run.stderr,
        )
        stale_refresh_report = report(stale_refresh_session)
        assert stale_refresh_report["result"] == "ready_with_review"
        assert stale_refresh_report["actions"]["refresh_after_enrich"]["status"] == "failed_soft"
        assert stale_refresh_report["actions"]["review_suggested_preview"]["status"] == "skipped"
        assert stale_refresh_report["actions"]["refresh_after_review"]["status"] == "failed_soft"
        assert stale_refresh_report["actions"]["finish"]["status"] == "skipped"
        assert "outcome refresh left stale" in stale_refresh_report["actions"]["refresh_after_enrich"]["error"]

        mutate_session = write_session(root, "mutate")
        mutate_run = run_supervisor(root, mutate_session, fake, "mutate_raw")
        assert mutate_run.returncode == 2
        mutate_report = report(mutate_session)
        assert mutate_report["result"] == "failed"
        assert mutate_report["raw"]["preserved"] is False

        interrupted_session = write_session(root, "interrupted")
        env = os.environ.copy()
        env["FAKE_MEETING_SCENARIO"] = "review"
        env["FAKE_MEETING_SLEEP_PROCESS"] = "1"
        signal_count = root / "meeting-signal-count.txt"
        signal_ready = root / "meeting-signal-ready.txt"
        env["FAKE_MEETING_SIGNAL_COUNT_FILE"] = str(signal_count)
        env["FAKE_MEETING_SIGNAL_READY_FILE"] = str(signal_ready)
        process = subprocess.Popen(
            [
                sys.executable,
                str(SUPERVISOR),
                str(interrupted_session),
                "--murmurmark-bin",
                str(fake),
            ],
            cwd=root,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        wait_for_process_action(interrupted_session, process)
        wait_for_path(signal_ready, process)
        process.send_signal(signal.SIGINT)
        process.send_signal(signal.SIGINT)
        stdout, stderr = process.communicate(timeout=20)
        assert process.returncode == 130, (stdout, stderr)
        assert "Traceback" not in stderr, stderr
        assert signal_count.read_text(encoding="utf-8").splitlines() == [str(signal.SIGINT)]
        assert report(interrupted_session)["result"] == "interrupted"
        assert report(interrupted_session)["resume_available"] is True
        assert report(interrupted_session)["next"]["action"] == "resume"

        implicit_resume = run_supervisor(root, interrupted_session, fake, "review")
        assert implicit_resume.returncode == 2
        assert "resume explicitly" in implicit_resume.stderr
        assert report(interrupted_session)["result"] == "interrupted"

        limited_session = root / "sessions" / "limited"
        shutil.copytree(interrupted_session, limited_session)
        limited_state_path = limited_session / "derived/meeting-lifecycle/state.json"
        limited_state = json.loads(limited_state_path.read_text(encoding="utf-8"))
        limited_state["transition_count"] = 9
        limited_state_path.write_text(json.dumps(limited_state), encoding="utf-8")
        limited_run = run_supervisor(
            root,
            limited_session,
            fake,
            "review",
            "--resume",
            "--max-transitions",
            "9",
        )
        assert limited_run.returncode == 2
        assert report(limited_session)["reason"] == "transition_limit_exceeded"

        resumed = run_supervisor(root, interrupted_session, fake, "review", "--resume")
        assert resumed.returncode == 0, (resumed.stdout, resumed.stderr)
        assert report(interrupted_session)["result"] == "ready_with_review"

        locked_session = write_session(root, "locked")
        lock_path = locked_session / "derived/meeting-lifecycle/lifecycle.lock"
        lock_path.parent.mkdir(parents=True)
        with lock_path.open("a+", encoding="utf-8") as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked_run = run_supervisor(root, locked_session, fake, "review")
            assert locked_run.returncode == 3

        stale_session = write_session(root, "stale-terminal")
        stale_ready = run_supervisor(root, stale_session, fake, "ready")
        assert stale_ready.returncode == 0
        stale_log = (stale_session / "fake-cli.log").read_text(encoding="utf-8")
        with (stale_session / "audio/mic/000001.caf").open("ab") as handle:
            handle.write(b"changed-after-completion")
        stale_repeat = run_supervisor(root, stale_session, fake, "ready")
        assert stale_repeat.returncode == 2
        assert report(stale_session)["reason"] == "raw_capture_changed_after_completion"
        assert (stale_session / "fake-cli.log").read_text(encoding="utf-8") == stale_log

        refreshed_session = write_session(root, "refresh-terminal-handoff")
        refreshed_ready = run_supervisor(root, refreshed_session, fake, "ready")
        assert refreshed_ready.returncode == 0
        refreshed_log = (refreshed_session / "fake-cli.log").read_text(encoding="utf-8")
        artifacts_root = refreshed_session / "derived/transcript-simple/whisper-cpp/resolved"
        transcript_v2 = artifacts_root / "transcript.fixture_v2.md"
        dialogue_v2 = artifacts_root / "clean_dialogue.fixture_v2.json"
        transcript_v2.write_text("# Refreshed transcript\n", encoding="utf-8")
        write_json(
            dialogue_v2,
            {
                "schema": "murmurmark.clean_dialogue/v1",
                "session": str(refreshed_session),
                "utterances": [
                    {
                        "id": "utt_late_review",
                        "role": "me",
                        "start": 1.0,
                        "end": 1.5,
                        "text": "must remain outside lifecycle events",
                        "quality": {
                            "needs_review": True,
                            "decision_reason": "late_review_residual",
                        },
                    }
                ],
            },
        )
        outcome_path = refreshed_session / "derived/outcome/outcome.json"
        outcome_payload = json.loads(outcome_path.read_text(encoding="utf-8"))
        outcome_payload["selected_profile"] = "fixture_v2"
        outcome_payload["summary"]["can_export"] = False
        outcome_payload["outputs"]["transcript"] = {
            "path": str(transcript_v2.relative_to(refreshed_session)),
            "exists": True,
        }
        outcome_payload["outputs"]["clean_dialogue"] = {
            "path": str(dialogue_v2.relative_to(refreshed_session)),
            "exists": True,
        }
        write_json(outcome_path, outcome_payload)
        readiness_path = refreshed_session / "derived/readiness/session_readiness.json"
        readiness_payload = json.loads(readiness_path.read_text(encoding="utf-8"))
        readiness_payload["selected_profile"] = "fixture_v2"
        readiness_payload["metrics"]["needs_review_count"] = 1
        readiness_payload["metrics"]["transcript_review_burden_sec"] = 0.5
        readiness_payload["export_blockers"] = ["full_transcript_review_required"]
        write_json(readiness_path, readiness_payload)
        refreshed_repeat = run_supervisor(root, refreshed_session, fake, "ready")
        assert refreshed_repeat.returncode == 0, (refreshed_repeat.stdout, refreshed_repeat.stderr)
        refreshed_report = report(refreshed_session)
        assert refreshed_report["selected_profile"] == "fixture_v2"
        assert refreshed_report["transcript"].endswith("transcript.fixture_v2.md")
        assert refreshed_report["manual_decisions"]["total"] == 1
        assert refreshed_report["next"]["status"] == "human_decision_required"
        assert (refreshed_session / "fake-cli.log").read_text(encoding="utf-8") == refreshed_log
        assert "must remain outside lifecycle events" not in (
            refreshed_session / "derived/meeting-lifecycle/events.jsonl"
        ).read_text(encoding="utf-8")

    print("meeting lifecycle checks passed")


if __name__ == "__main__":
    main()
