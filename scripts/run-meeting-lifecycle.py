#!/usr/bin/env python3
"""Run the bounded post-capture lifecycle for `murmurmark meeting`."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shlex
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


STATE_SCHEMA = "murmurmark.meeting_lifecycle_state/v1"
NEXT_SCHEMA = "murmurmark.meeting_next_action/v1"
EVENT_SCHEMA = "murmurmark.meeting_lifecycle_event/v1"
REPORT_SCHEMA = "murmurmark.meeting_lifecycle_report/v1"
GENERATOR = {"name": "run-meeting-lifecycle", "version": "0.1.0"}
ACTION_ORDER = (
    "capture_validate",
    "inspect",
    "process",
    "enrich",
    "refresh_after_enrich",
    "review_suggested_preview",
    "review_suggested_apply",
    "refresh_after_review",
    "finish",
)
TERMINAL_ACTION_STATUSES = {"passed", "skipped", "failed_soft"}
MAX_ACTION_ATTEMPTS = 3


class LifecycleError(RuntimeError):
    pass


class LockBusyError(LifecycleError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def rounded(value: float) -> float:
    return round(max(0.0, float(value)), 3)


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def display_path(path: Path) -> str:
    absolute = path.resolve()
    try:
        return str(absolute.relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(absolute)


def session_file(session: Path, value: Any) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = Path(value)
    return candidate if candidate.is_absolute() else session / candidate


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def raw_manifest(session: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    files = manifest.get("files") if isinstance(manifest.get("files"), dict) else {}
    rows: list[dict[str, Any]] = []
    for source in ("mic", "remote"):
        entries = files.get(source) if isinstance(files.get(source), list) else []
        if not entries:
            raise LifecycleError(f"session manifest has no raw {source} files")
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                raise LifecycleError(f"session manifest has an invalid raw {source} entry")
            relative = entry["path"]
            path = session / relative
            if not path.is_file():
                raise LifecycleError(f"raw {source} file is missing: {relative}")
            stat = path.stat()
            rows.append(
                {
                    "source": source,
                    "path": relative,
                    "bytes": stat.st_size,
                    "sha256": hash_file(path),
                }
            )
    return rows


def same_raw_manifest(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> bool:
    fields = ("source", "path", "bytes", "sha256")
    normalized_before = sorted(tuple(row.get(field) for field in fields) for row in before)
    normalized_after = sorted(tuple(row.get(field) for field in fields) for row in after)
    return normalized_before == normalized_after


def default_action_state() -> dict[str, Any]:
    return {"status": "pending", "attempts": 0, "duration_sec": 0.0}


def new_state(session: Path, record_elapsed_sec: float | None) -> dict[str, Any]:
    return {
        "schema": STATE_SCHEMA,
        "generator": GENERATOR,
        "session": display_path(session),
        "status": "running",
        "started_at": now_iso(),
        "updated_at": now_iso(),
        "record_command_elapsed_sec": rounded(record_elapsed_sec or 0.0),
        "capture_elapsed_sec": 0.0,
        "capture_finalize_elapsed_sec": 0.0,
        "current_action": None,
        "next_action": "capture_validate",
        "transition_count": 0,
        "actions": {action: default_action_state() for action in ACTION_ORDER},
        "raw_inputs": [],
        "warnings": [],
        "resume_command": resume_command(session),
    }


def resume_command(session: Path) -> str:
    return f"murmurmark meeting --resume {shlex.quote(display_path(session))}"


def ensure_state_shape(state: dict[str, Any], session: Path) -> dict[str, Any]:
    if state.get("schema") != STATE_SCHEMA:
        raise LifecycleError("incompatible meeting lifecycle state schema")
    actions = state.setdefault("actions", {})
    if not isinstance(actions, dict):
        raise LifecycleError("invalid meeting lifecycle action state")
    for action in ACTION_ORDER:
        current = actions.get(action)
        if not isinstance(current, dict):
            actions[action] = default_action_state()
    state["session"] = display_path(session)
    state["resume_command"] = resume_command(session)
    state.setdefault("warnings", [])
    state.setdefault("transition_count", 0)
    state.setdefault("record_command_elapsed_sec", state.get("capture_elapsed_sec", 0.0))
    state.setdefault("capture_finalize_elapsed_sec", 0.0)
    return state


def recover_state_for_resume(state: dict[str, Any]) -> None:
    for action, action_state in state["actions"].items():
        status = action_state.get("status")
        if status in {"running", "interrupted"}:
            action_state["status"] = "pending"
            action_state["error"] = None
        elif status == "failed_hard" and action != "capture_validate":
            if int(action_state.get("attempts") or 0) < MAX_ACTION_ATTEMPTS:
                action_state["status"] = "pending"
                action_state["error"] = None
    state["status"] = "running"
    state["current_action"] = None
    state["updated_at"] = now_iso()


@contextmanager
def lifecycle_lock(path: Path, session: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+", encoding="utf-8")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            handle.seek(0)
            owner = handle.read().strip()
            suffix = f" ({owner})" if owner else ""
            raise LockBusyError(f"meeting lifecycle is already running for this session{suffix}") from error
        handle.seek(0)
        handle.truncate()
        handle.write(f"pid={os.getpid()} session={session} acquired_at={now_iso()}\n")
        handle.flush()
        os.fsync(handle.fileno())
        yield
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


class InterruptController:
    def __init__(self) -> None:
        self.requested = False
        self.signal_number: int | None = None
        self.child: subprocess.Popen[Any] | None = None

    def install(self) -> None:
        signal.signal(signal.SIGINT, self._handle)
        signal.signal(signal.SIGTERM, self._handle)

    def _handle(self, signum: int, _frame: Any) -> None:
        if self.requested:
            return
        self.requested = True
        self.signal_number = signum
        child = self.child
        if child is not None and child.poll() is None:
            try:
                child.send_signal(signum)
            except ProcessLookupError:
                pass


class MeetingLifecycle:
    def __init__(
        self,
        session: Path,
        murmurmark_bin: Path,
        max_transitions: int,
        record_elapsed_sec: float | None,
        resume: bool,
    ) -> None:
        self.session = session.resolve()
        self.murmurmark_bin = murmurmark_bin.resolve()
        self.max_transitions = max_transitions
        self.record_elapsed_sec = record_elapsed_sec
        self.resume = resume
        self.root = self.session / "derived" / "meeting-lifecycle"
        self.state_path = self.root / "state.json"
        self.next_path = self.root / "next_action.json"
        self.events_path = self.root / "events.jsonl"
        self.report_path = self.root / "report.json"
        self.report_md_path = self.root / "report.md"
        self.lock_path = self.root / "lifecycle.lock"
        self.interrupts = InterruptController()
        self.state: dict[str, Any] = {}

    def run(self) -> int:
        if not self.session.is_dir():
            raise LifecycleError(f"session directory not found: {self.session}")
        if not self.murmurmark_bin.is_file() or not os.access(self.murmurmark_bin, os.X_OK):
            raise LifecycleError(f"murmurmark executable not found: {self.murmurmark_bin}")

        with lifecycle_lock(self.lock_path, self.session):
            self.interrupts.install()
            existing = read_json(self.state_path)
            if existing is None:
                self.state = new_state(self.session, self.record_elapsed_sec)
                self.event("lifecycle_started", resume=False)
            else:
                self.state = ensure_state_shape(existing, self.session)
                if self.state.get("status") in {"ready", "ready_with_review"}:
                    report = read_json(self.report_path)
                    if report:
                        raw_preserved, _ = self.verify_raw_preserved(emit_event=False)
                        if not raw_preserved:
                            return self.finish_failed("raw_capture_changed_after_completion")
                        print_summary(report)
                        return 0
                if self.resume:
                    recover_state_for_resume(self.state)
                    self.event("lifecycle_resumed", resume=True)
                else:
                    raise LifecycleError(
                        f"meeting lifecycle is {self.state.get('status')!r}; "
                        f"resume explicitly with `{resume_command(self.session)}`"
                    )
            self.save_state()

            while True:
                if self.interrupts.requested:
                    return self.finish_interrupted()

                action, reason = self.choose_next_action()
                self.write_next_action(action, reason)
                if action == "complete":
                    return self.finish_complete()
                if action == "fail":
                    return self.finish_failed(reason)
                if int(self.state.get("transition_count") or 0) >= self.max_transitions:
                    self.state["failure_reason"] = "transition_limit_exceeded"
                    return self.finish_failed("transition_limit_exceeded")
                if action.startswith("skip:"):
                    self.skip_action(action.removeprefix("skip:"), reason)
                    continue

                result = self.execute_action(action, reason)
                if result == "interrupted":
                    return self.finish_interrupted()
                if result == "failed_hard":
                    return self.finish_failed(f"action_failed:{action}")

    def choose_next_action(self) -> tuple[str, str]:
        for action in ACTION_ORDER:
            status = self.state["actions"][action].get("status")
            if status in TERMINAL_ACTION_STATUSES:
                continue
            if status == "failed_hard":
                return "fail", f"hard_action_failed:{action}"
            if int(self.state["actions"][action].get("attempts") or 0) >= MAX_ACTION_ATTEMPTS:
                return "fail", f"action_attempt_limit_reached:{action}"

            if action == "enrich" and self.deferred_is_complete():
                return f"skip:{action}", "structured checkpoint proves deferred enrichment is complete"
            if action == "review_suggested_preview":
                if self.state["actions"]["refresh_after_enrich"].get("status") != "passed":
                    return f"skip:{action}", "structured refresh after enrichment did not pass"
                if not self.review_is_required():
                    return f"skip:{action}", "structured readiness has no review gate"
            if action == "review_suggested_apply":
                preview_status = self.state["actions"]["review_suggested_preview"].get("status")
                if preview_status != "passed":
                    return f"skip:{action}", "current suggested-review preview did not pass"
                if self.safe_suggested_rows() <= 0:
                    return f"skip:{action}", "no safe suggested review rows are available"
            if action == "finish":
                if self.state["actions"]["refresh_after_review"].get("status") != "passed":
                    return f"skip:{action}", "final structured refresh did not pass"
                if not self.export_is_allowed():
                    return f"skip:{action}", "structured outcome does not allow guarded export"
            return action, self.action_reason(action)
        return "complete", "all allowlisted lifecycle actions are terminal"

    def action_reason(self, action: str) -> str:
        reasons = {
            "capture_validate": "validate finalized durable capture and freeze raw identities",
            "inspect": "run the existing capture/session inspection gate",
            "process": "produce the authoritative batch transcript with the ordinary process path",
            "enrich": "run optional local evidence enrichment after authoritative handoff",
            "refresh_after_enrich": "refresh structured readiness and outcome after enrichment",
            "review_suggested_preview": "compute the conservative suggested-review remainder",
            "review_suggested_apply": "apply only rows accepted by existing safe suggestion gates",
            "refresh_after_review": "refresh structured readiness and outcome after suggested review",
            "finish": "create a guarded export and retention plan because export is allowed",
        }
        return reasons[action]

    def command_for(self, action: str) -> list[str] | None:
        session = str(self.session)
        base = str(self.murmurmark_bin)
        commands = {
            "inspect": [base, "inspect", session],
            "process": [base, "process", session, "--skip-build"],
            "enrich": [base, "enrich", session],
            "refresh_after_enrich": [base, "outcome", session, "--refresh"],
            "review_suggested_preview": [base, "review", "suggested", "preview", session],
            "review_suggested_apply": [base, "review", "suggested", "apply", session],
            "refresh_after_review": [base, "outcome", session, "--refresh"],
            "finish": [base, "finish", session],
        }
        return commands.get(action)

    def execute_action(self, action: str, reason: str) -> str:
        action_state = self.state["actions"][action]
        action_state["status"] = "running"
        action_state["attempts"] = int(action_state.get("attempts") or 0) + 1
        action_state["started_at"] = now_iso()
        action_state["reason"] = reason
        action_state["error"] = None
        if action == "process":
            action_state["pipeline_report_before"] = self.file_identity(self.pipeline_report_path())
            action_state["handoff_runs_before"] = self.file_identity(self.authoritative_handoff_runs_path())
        elif action in {"refresh_after_enrich", "refresh_after_review"}:
            action_state["outcome_before"] = self.file_identity(self.outcome_path())
            action_state["readiness_before"] = self.file_identity(self.readiness_path())
        elif action == "finish":
            action_state["export_manifest_before"] = self.file_identity(self.export_manifest_path())
        self.state["current_action"] = action
        self.state["transition_count"] = int(self.state.get("transition_count") or 0) + 1
        self.save_state()
        self.event("action_started", action=action, reason=reason, attempt=action_state["attempts"])
        print(f"[meeting] {action}", flush=True)
        started = time.monotonic()

        try:
            if action == "capture_validate":
                self.validate_capture()
                return_code = 0
                interrupted = False
            else:
                command = self.command_for(action)
                if command is None:
                    raise LifecycleError(f"action has no allowlisted command: {action}")
                return_code, interrupted = self.run_command(command)
                if interrupted:
                    action_state["status"] = "interrupted"
                    action_state["finished_at"] = now_iso()
                    action_state["duration_sec"] = rounded(time.monotonic() - started)
                    self.save_state()
                    self.event("action_interrupted", action=action, returncode=return_code)
                    return "interrupted"
                if return_code != 0:
                    raise LifecycleError(f"command exited with {return_code}")
                self.validate_postcondition(action)
        except Exception as error:  # noqa: BLE001 - all failures must be journaled
            duration = rounded(time.monotonic() - started)
            hard = action in {"capture_validate", "inspect", "process"}
            action_state["status"] = "failed_hard" if hard else "failed_soft"
            action_state["finished_at"] = now_iso()
            action_state["duration_sec"] = duration
            action_state["error"] = str(error)
            self.state["current_action"] = None
            if not hard:
                self.state.setdefault("warnings", []).append(f"{action}: {error}")
            self.save_state()
            self.event(
                "action_failed",
                action=action,
                severity="hard" if hard else "soft",
                error=str(error),
                duration_sec=duration,
            )
            return action_state["status"]

        duration = rounded(time.monotonic() - started)
        action_state["status"] = "passed"
        action_state["finished_at"] = now_iso()
        action_state["duration_sec"] = duration
        action_state["returncode"] = return_code
        self.state["current_action"] = None
        self.save_state()
        self.event("action_passed", action=action, duration_sec=duration)
        return "passed"

    def run_command(self, command: list[str]) -> tuple[int, bool]:
        self.state["actions"][self.state["current_action"]]["command"] = command
        self.save_state()
        # Isolate each allowlisted action from the terminal's foreground process group.
        # The supervisor receives Ctrl-C and forwards it exactly once to the action.
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, start_new_session=True)
        self.interrupts.child = process
        interrupted_at: float | None = None
        terminate_sent = False
        kill_sent = False
        try:
            while process.poll() is None:
                if self.interrupts.requested:
                    interrupted_at = interrupted_at or time.monotonic()
                    elapsed = time.monotonic() - interrupted_at
                    if elapsed > 15 and not kill_sent:
                        process.kill()
                        kill_sent = True
                    elif elapsed > 10 and not terminate_sent:
                        process.terminate()
                        terminate_sent = True
                time.sleep(0.1)
            return int(process.returncode or 0), self.interrupts.requested
        finally:
            self.interrupts.child = None

    def validate_capture(self) -> None:
        manifest_path = self.session / "session.json"
        manifest = read_json(manifest_path)
        if manifest is None:
            raise LifecycleError("session.json is missing or invalid")
        current = raw_manifest(self.session, manifest)
        frozen = self.state.get("raw_inputs")
        if isinstance(frozen, list) and frozen:
            if not same_raw_manifest(frozen, current):
                raise LifecycleError("raw capture hash mismatch before processing")
        else:
            self.state["raw_inputs"] = current
        self.event("raw_inputs_frozen", files=len(current))

        health = manifest.get("health") if isinstance(manifest.get("health"), dict) else {}
        if manifest.get("status") not in {"completed", "completed_with_warnings"}:
            raise LifecycleError(
                f"capture status is {manifest.get('status')!r}, expected a completed session"
            )
        if health.get("partial") is True:
            raise LifecycleError("capture is marked partial")
        if health.get("summary") not in {None, "ok", "warning"}:
            raise LifecycleError(f"capture health is {health.get('summary')!r}")
        if health.get("explicit_stop") is not True:
            raise LifecycleError("capture did not end through an explicit stop or requested duration")
        tracks = health.get("tracks") if isinstance(health.get("tracks"), dict) else {}
        track_durations: list[float] = []
        for source in ("mic", "remote"):
            track = tracks.get(source) if isinstance(tracks.get(source), dict) else {}
            if int(track.get("frames") or 0) <= 0 or float(track.get("duration_sec") or 0.0) <= 0:
                raise LifecycleError(f"raw {source} track is empty")
            track_durations.append(float(track.get("duration_sec") or 0.0))
        actual_duration = float(health.get("actual_duration_sec") or 0.0)
        if actual_duration <= 0 and track_durations:
            actual_duration = max(track_durations)
        record_elapsed = float(self.state.get("record_command_elapsed_sec") or 0.0)
        self.state["capture_elapsed_sec"] = rounded(actual_duration)
        self.state["capture_finalize_elapsed_sec"] = rounded(
            max(0.0, record_elapsed - actual_duration) if record_elapsed > 0 else 0.0
        )
        capture_warnings = health.get("warnings") if isinstance(health.get("warnings"), list) else []
        for warning in capture_warnings:
            text = str(warning).strip()
            if text:
                self.state.setdefault("warnings", []).append(f"capture: {text}")

    def validate_postcondition(self, action: str) -> None:
        if action == "process":
            report = read_json(self.pipeline_report_path())
            if (
                report is None
                or report.get("schema") != "murmurmark.session_pipeline_run/v1"
                or report.get("status") != "passed"
            ):
                raise LifecycleError("authoritative pipeline report is missing or not passed")
            action_state = self.state["actions"]["process"]
            report_changed = self.file_changed(
                action_state.get("pipeline_report_before"), self.pipeline_report_path()
            )
            handoff_run_added = self.file_changed(
                action_state.get("handoff_runs_before"), self.authoritative_handoff_runs_path()
            )
            if not report_changed and not handoff_run_added:
                raise LifecycleError(
                    "authoritative process left stale artifacts and no cache-reuse provenance"
                )
            outcome = read_json(self.outcome_path())
            if outcome is None:
                raise LifecycleError("authoritative outcome.json was not produced")
            transcript = self.output_path(outcome, "transcript")
            if transcript is None or not transcript.is_file():
                raise LifecycleError("authoritative transcript was not produced")
        elif action == "enrich":
            if not self.deferred_is_complete():
                raise LifecycleError("deferred enrichment did not reach a completed checkpoint")
        elif action in {"refresh_after_enrich", "refresh_after_review"}:
            outcome = read_json(self.outcome_path())
            readiness = read_json(self.readiness_path())
            if outcome is None or outcome.get("schema") != "murmurmark.outcome/v1":
                raise LifecycleError("outcome refresh did not produce a compatible outcome.json")
            if readiness is None or readiness.get("schema") != "murmurmark.session_readiness/v1":
                raise LifecycleError("outcome refresh did not produce compatible readiness")
            action_state = self.state["actions"][action]
            outcome_changed = self.file_changed(action_state.get("outcome_before"), self.outcome_path())
            readiness_changed = self.file_changed(
                action_state.get("readiness_before"), self.readiness_path()
            )
            if not outcome_changed and not readiness_changed:
                raise LifecycleError("outcome refresh left stale structured artifacts unchanged")
            outcome_profile = str(outcome.get("selected_profile") or "")
            readiness_profile = str(readiness.get("selected_profile") or "")
            if outcome_profile and readiness_profile and outcome_profile != readiness_profile:
                raise LifecycleError("outcome and readiness selected profiles do not match")
        elif action == "finish":
            self.validate_fresh_export_manifest()

    def validate_fresh_export_manifest(self) -> None:
        path = self.export_manifest_path()
        manifest = read_json(path)
        if manifest is None:
            raise LifecycleError("guarded finish did not produce a valid export manifest")
        if manifest.get("schema") != "murmurmark.export_manifest/v1":
            raise LifecycleError("guarded finish produced an incompatible export manifest")
        if manifest.get("status") not in {"exported", "exported_with_warnings"}:
            raise LifecycleError("guarded finish did not produce a successful export manifest")
        if string_list(manifest.get("blockers")):
            raise LifecycleError("guarded finish export manifest contains blockers")

        session_manifest = read_json(self.session / "session.json") or {}
        expected_session_id = str(session_manifest.get("session_id") or self.session.name)
        manifest_session_id = str(manifest.get("session_id") or "")
        if not manifest_session_id or manifest_session_id != expected_session_id:
            raise LifecycleError("guarded finish export manifest belongs to another session")
        manifest_session = manifest.get("session")
        if isinstance(manifest_session, str) and manifest_session.strip():
            candidate = Path(manifest_session)
            if not candidate.is_absolute():
                candidate = Path.cwd() / candidate
            if candidate.resolve() != self.session:
                raise LifecycleError("guarded finish export manifest path does not match the session")

        outcome = read_json(self.outcome_path()) or {}
        summary = outcome.get("summary") if isinstance(outcome.get("summary"), dict) else {}
        expected_profile = str(
            outcome.get("selected_profile") or summary.get("selected_profile") or ""
        )
        manifest_profile = str(manifest.get("selected_profile") or "")
        if expected_profile and expected_profile != "unknown" and manifest_profile != expected_profile:
            raise LifecycleError("guarded finish export profile does not match the selected transcript")

        after = self.file_identity(path)
        before = self.state["actions"]["finish"].get("export_manifest_before")
        if not after:
            raise LifecycleError("guarded finish export manifest disappeared")
        if isinstance(before, dict) and before.get("sha256") == after.get("sha256"):
            raise LifecycleError("guarded finish left a stale export manifest unchanged")

    def file_identity(self, path: Path) -> dict[str, Any] | None:
        if not path.is_file():
            return None
        stat = path.stat()
        return {
            "path": display_path(path),
            "bytes": stat.st_size,
            "sha256": hash_file(path),
        }

    def file_changed(self, before: Any, path: Path) -> bool:
        after = self.file_identity(path)
        if not after:
            return False
        return not isinstance(before, dict) or before.get("sha256") != after.get("sha256")

    def skip_action(self, action: str, reason: str) -> None:
        action_state = self.state["actions"][action]
        action_state["status"] = "skipped"
        action_state["reason"] = reason
        action_state["finished_at"] = now_iso()
        self.state["transition_count"] = int(self.state.get("transition_count") or 0) + 1
        self.save_state()
        self.event("action_skipped", action=action, reason=reason)

    def deferred_is_complete(self) -> bool:
        report = read_json(self.pipeline_report_path())
        if report is not None and report.get("status") == "passed":
            performance = report.get("performance") if isinstance(report.get("performance"), dict) else {}
            pending = performance.get("deferred_stages_pending")
            if report.get("phase") == "full" and isinstance(pending, list) and not pending:
                return True

        deferred_report = read_json(self.deferred_report_path())
        handoff = read_json(self.authoritative_handoff_path())
        deferred = handoff.get("deferred_enrichment") if isinstance(handoff, dict) else None
        return bool(
            deferred_report
            and deferred_report.get("schema") == "murmurmark.session_pipeline_run/v1"
            and deferred_report.get("status") == "passed"
            and isinstance(deferred, dict)
            and deferred.get("status") == "completed"
        )

    def review_is_required(self) -> bool:
        readiness = read_json(self.readiness_path()) or {}
        gate = str(readiness.get("use_gate") or "")
        blockers = readiness.get("review_blockers")
        return gate == "review_first" or gate.endswith("_review_first") or bool(blockers)

    def safe_suggested_rows(self) -> int:
        readiness = read_json(self.readiness_path()) or {}
        metrics = readiness.get("metrics") if isinstance(readiness.get("metrics"), dict) else {}
        value = metrics.get("suggested_closure_auto_rows")
        if isinstance(value, (int, float)) and int(value) > 0:
            return int(value)
        return 0

    def export_is_allowed(self) -> bool:
        outcome = read_json(self.outcome_path()) or {}
        summary = outcome.get("summary") if isinstance(outcome.get("summary"), dict) else {}
        return summary.get("can_export") is True

    def pipeline_report_path(self) -> Path:
        return self.session / "derived" / "pipeline-run" / "pipeline_run_report.json"

    def deferred_report_path(self) -> Path:
        return self.session / "derived" / "pipeline-run" / "deferred_enrichment_report.json"

    def authoritative_handoff_path(self) -> Path:
        return self.session / "derived" / "pipeline-run" / "authoritative_handoff.json"

    def authoritative_handoff_runs_path(self) -> Path:
        return self.session / "derived" / "pipeline-run" / "authoritative_handoff_runs.jsonl"

    def readiness_path(self) -> Path:
        return self.session / "derived" / "readiness" / "session_readiness.json"

    def outcome_path(self) -> Path:
        return self.session / "derived" / "outcome" / "outcome.json"

    def export_manifest_path(self) -> Path:
        return Path.cwd() / "exports" / "private" / self.session.name / "export_manifest.json"

    def output_path(self, outcome: dict[str, Any], key: str) -> Path | None:
        outputs = outcome.get("outputs") if isinstance(outcome.get("outputs"), dict) else {}
        item = outputs.get(key) if isinstance(outputs.get(key), dict) else {}
        return session_file(self.session, item.get("path"))

    def write_next_action(self, action: str, reason: str) -> None:
        actual = action.removeprefix("skip:")
        command = self.command_for(actual)
        terminal = action in {"complete", "fail"}
        payload = {
            "schema": NEXT_SCHEMA,
            "generator": GENERATOR,
            "generated_at": now_iso(),
            "session": display_path(self.session),
            "action": actual,
            "decision": "skip" if action.startswith("skip:") else ("terminal" if terminal else "run"),
            "reason": reason,
            "allowlisted": actual in ACTION_ORDER or terminal,
            "command": command,
        }
        self.state["next_action"] = payload["action"]
        self.state["updated_at"] = now_iso()
        write_json(self.next_path, payload)
        self.save_state()

    def save_state(self) -> None:
        self.state["updated_at"] = now_iso()
        write_json(self.state_path, self.state)

    def event(self, event_type: str, **fields: Any) -> None:
        append_jsonl(
            self.events_path,
            {
                "schema": EVENT_SCHEMA,
                "timestamp": now_iso(),
                "event": event_type,
                "session": display_path(self.session),
                **fields,
            },
        )

    def finish_interrupted(self) -> int:
        self.state["status"] = "interrupted"
        self.state["current_action"] = None
        self.state["interrupted_signal"] = self.interrupts.signal_number
        self.save_state()
        self.event("lifecycle_interrupted", signal=self.interrupts.signal_number)
        report = self.build_report(forced_result="interrupted", reason="processing_interrupted")
        self.write_report(report)
        print_summary(report)
        return 130

    def finish_failed(self, reason: str) -> int:
        self.state["status"] = "failed"
        self.state["failure_reason"] = reason
        self.state["current_action"] = None
        self.save_state()
        self.event("lifecycle_failed", reason=reason)
        report = self.build_report(forced_result="failed", reason=reason)
        self.write_report(report)
        print_summary(report)
        return 2

    def finish_complete(self) -> int:
        report = self.build_report()
        self.state["status"] = report["result"]
        self.state["current_action"] = None
        self.state["next_action"] = "complete"
        self.state["finished_at"] = now_iso()
        self.save_state()
        self.event("lifecycle_completed", result=report["result"])
        self.write_report(report)
        print_summary(report)
        return 0 if report["result"] in {"ready", "ready_with_review"} else 2

    def build_report(self, forced_result: str | None = None, reason: str | None = None) -> dict[str, Any]:
        outcome = read_json(self.outcome_path()) or {}
        readiness = read_json(self.readiness_path()) or {}
        metrics = readiness.get("metrics") if isinstance(readiness.get("metrics"), dict) else {}
        summary = outcome.get("summary") if isinstance(outcome.get("summary"), dict) else {}
        transcript = self.output_path(outcome, "transcript")
        notes = self.output_path(outcome, "notes")
        verdict_path = self.output_path(outcome, "quality_verdict")
        raw_preserved, raw_after = self.verify_raw_preserved()
        export_manifest = read_json(self.export_manifest_path())
        actions = self.state.get("actions") if isinstance(self.state.get("actions"), dict) else {}
        finish_state = actions.get("finish") if isinstance(actions.get("finish"), dict) else {}
        export_succeeded = bool(
            export_manifest
            and finish_state.get("status") == "passed"
            and export_manifest.get("status") in {"exported", "exported_with_warnings"}
        )

        result = forced_result
        if result is None:
            if not raw_preserved or transcript is None or not transcript.is_file():
                result = "failed"
                reason = reason or ("raw_capture_changed" if not raw_preserved else "authoritative_transcript_missing")
            elif outcome.get("outcome") in {"blocked", "failed"}:
                result = "failed"
                reason = reason or f"outcome:{outcome.get('outcome')}"
            elif export_succeeded:
                result = "ready"
            else:
                result = "ready_with_review"
                if summary.get("can_export") is True:
                    reason = reason or "guarded_export_not_completed"

        review_blockers = string_list(readiness.get("review_blockers"))
        export_blockers = string_list(summary.get("export_blockers"))
        if not export_blockers:
            export_blockers = string_list(readiness.get("export_blockers"))
        if result == "ready_with_review" and reason is None:
            reason = (
                "structured_review_gate_remains"
                if review_blockers or export_blockers
                else "review_or_export_follow_up_remains"
            )

        unresolved_count = first_number(
            metrics,
            "review_scope_remaining_rows",
            "suggested_closure_manual_remaining_rows",
            "needs_review_count",
        )
        unresolved_seconds = first_number(
            metrics,
            "transcript_review_burden_sec",
            "review_scope_remaining_seconds",
            "suggested_closure_manual_remaining_seconds",
            "review_burden_sec",
        )
        action_times = {
            action: rounded(float(value.get("duration_sec") or 0.0))
            for action, value in actions.items()
            if isinstance(value, dict)
        }
        supervisor_elapsed = rounded(sum(action_times.values()))
        capture_elapsed = rounded(float(self.state.get("capture_elapsed_sec") or 0.0))
        capture_finalize_elapsed = rounded(float(self.state.get("capture_finalize_elapsed_sec") or 0.0))
        postprocess_elapsed = rounded(capture_finalize_elapsed + supervisor_elapsed)
        resumable = result == "interrupted" or any(
            action != "capture_validate"
            and isinstance(value, dict)
            and value.get("status") == "failed_hard"
            and int(value.get("attempts") or 0) < MAX_ACTION_ATTEMPTS
            for action, value in actions.items()
        )
        warnings = list(dict.fromkeys(str(item) for item in self.state.get("warnings", []) if str(item)))
        if not raw_preserved:
            warnings.append("raw capture SHA-256 identities changed")

        return {
            "schema": REPORT_SCHEMA,
            "generator": GENERATOR,
            "generated_at": now_iso(),
            "session": display_path(self.session),
            "result": result,
            "reason": reason,
            "transcript": display_path(transcript) if transcript and transcript.is_file() else None,
            "notes": display_path(notes) if notes and notes.is_file() else None,
            "verdict": outcome.get("verdict") or readiness.get("verdict"),
            "verdict_path": display_path(verdict_path) if verdict_path and verdict_path.is_file() else None,
            "selected_profile": outcome.get("selected_profile") or readiness.get("selected_profile"),
            "unresolved_review": {
                "count": int(unresolved_count or 0),
                "seconds": rounded(float(unresolved_seconds or 0.0)),
                "blockers": review_blockers,
            },
            "export": {
                "status": (
                    export_manifest.get("status")
                    if export_succeeded
                    else (
                        "failed"
                        if finish_state.get("status") == "failed_soft"
                        else outcome.get("export_status", "not_attempted")
                    )
                ),
                "manifest": display_path(self.export_manifest_path()) if export_succeeded else None,
                "blockers": export_blockers,
            },
            "raw": {
                "preserved": raw_preserved,
                "before": self.state.get("raw_inputs", []),
                "after": raw_after,
            },
            "elapsed_sec": {
                "capture": capture_elapsed,
                "capture_finalize": capture_finalize_elapsed,
                "authoritative_process": action_times.get("process", 0.0),
                "enrichment": action_times.get("enrich", 0.0),
                "postprocessing": postprocess_elapsed,
                "supervisor_actions": supervisor_elapsed,
                "total_after_stop": postprocess_elapsed,
                "total": rounded(capture_elapsed + postprocess_elapsed),
                "actions": action_times,
            },
            "actions": actions,
            "warnings": warnings,
            "journal": display_path(self.events_path),
            "state": display_path(self.state_path),
            "resume_command": resume_command(self.session),
            "resume_available": resumable,
        }

    def verify_raw_preserved(self, *, emit_event: bool = True) -> tuple[bool, list[dict[str, Any]]]:
        manifest = read_json(self.session / "session.json")
        before = self.state.get("raw_inputs")
        if manifest is None or not isinstance(before, list) or not before:
            return False, []
        try:
            after = raw_manifest(self.session, manifest)
        except LifecycleError:
            return False, []
        preserved = same_raw_manifest(before, after)
        if emit_event:
            self.event("raw_inputs_verified", preserved=preserved, files=len(after))
        return preserved, after

    def write_report(self, report: dict[str, Any]) -> None:
        write_json(self.report_path, report)
        unresolved = report["unresolved_review"]
        elapsed = report["elapsed_sec"]
        lines = [
            "# Meeting Lifecycle",
            "",
            f"- Result: `{report['result']}`",
            f"- Session: `{report['session']}`",
            f"- Transcript: `{report.get('transcript') or 'not available'}`",
            f"- Notes: `{report.get('notes') or 'not available'}`",
            f"- Verdict: `{report.get('verdict') or 'unknown'}`",
            f"- Unresolved review: `{unresolved['count']}` items / `{unresolved['seconds']:.3f}s`",
            f"- Export: `{report['export']['status']}`",
            f"- Raw preserved: `{str(report['raw']['preserved']).lower()}`",
            f"- Capture: `{elapsed['capture']:.3f}s`",
            f"- Capture finalization: `{elapsed['capture_finalize']:.3f}s`",
            f"- Authoritative process: `{elapsed['authoritative_process']:.3f}s`",
            f"- Enrichment: `{elapsed['enrichment']:.3f}s`",
            f"- Total after stop: `{elapsed['total_after_stop']:.3f}s`",
        ]
        if report.get("reason"):
            lines.append(f"- Reason: `{report['reason']}`")
        if unresolved.get("blockers"):
            lines.append(f"- Review blockers: `{', '.join(unresolved['blockers'])}`")
        if report["export"].get("blockers"):
            lines.append(f"- Export blockers: `{', '.join(report['export']['blockers'])}`")
        if report.get("resume_available"):
            lines += ["", "Resume:", "", f"```bash\n{report['resume_command']}\n```"]
        self.report_md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def first_number(payload: dict[str, Any], *keys: str) -> float | int | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, (int, float)):
            return value
    return None


def string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(item.strip() for item in value if isinstance(item, str) and item.strip()))


def print_summary(report: dict[str, Any]) -> None:
    unresolved = report.get("unresolved_review") if isinstance(report.get("unresolved_review"), dict) else {}
    export = report.get("export") if isinstance(report.get("export"), dict) else {}
    raw = report.get("raw") if isinstance(report.get("raw"), dict) else {}
    elapsed = report.get("elapsed_sec") if isinstance(report.get("elapsed_sec"), dict) else {}
    print("")
    print(f"SESSION=\"{report.get('session')}\"")
    print("meeting:")
    print(f"  result: {report.get('result')}")
    print(f"  transcript: {report.get('transcript') or 'not_available'}")
    print(f"  notes: {report.get('notes') or 'not_available'}")
    print(f"  verdict: {report.get('verdict') or 'unknown'}")
    print(f"  unresolved: {int(unresolved.get('count') or 0)} items / {float(unresolved.get('seconds') or 0.0):.3f}s")
    print(f"  export: {export.get('status') or 'not_attempted'}")
    print(f"  raw_capture: {'preserved' if raw.get('preserved') is True else 'changed'}")
    print(
        "  elapsed: "
        f"capture={float(elapsed.get('capture') or 0.0):.1f}s "
        f"capture_finalize={float(elapsed.get('capture_finalize') or 0.0):.1f}s "
        f"authoritative_process={float(elapsed.get('authoritative_process') or 0.0):.1f}s "
        f"enrichment={float(elapsed.get('enrichment') or 0.0):.1f}s "
        f"total_after_stop={float(elapsed.get('total_after_stop') or elapsed.get('postprocessing') or 0.0):.1f}s"
    )
    if report.get("reason"):
        print(f"  reason: {report.get('reason')}")
    blockers = string_list(unresolved.get("blockers"))
    if blockers:
        print(f"  review_blockers: {', '.join(blockers)}")
    export_blockers = string_list(export.get("blockers"))
    if export_blockers:
        print(f"  export_blockers: {', '.join(export_blockers)}")
    if report.get("resume_available"):
        print(f"  resume: {report.get('resume_command')}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path)
    parser.add_argument("--murmurmark-bin", type=Path, required=True)
    parser.add_argument("--record-elapsed-sec", "--capture-elapsed-sec", dest="record_elapsed_sec", type=float)
    parser.add_argument("--max-transitions", type=int, default=16)
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.max_transitions < len(ACTION_ORDER):
            raise LifecycleError(f"--max-transitions must be at least {len(ACTION_ORDER)}")
        lifecycle = MeetingLifecycle(
            session=args.session,
            murmurmark_bin=args.murmurmark_bin,
            max_transitions=args.max_transitions,
            record_elapsed_sec=args.record_elapsed_sec,
            resume=args.resume,
        )
        return lifecycle.run()
    except LockBusyError as error:
        print(f"error: {error}", file=sys.stderr)
        print(f"resume: {resume_command(args.session.resolve())}", file=sys.stderr)
        return 3
    except LifecycleError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
