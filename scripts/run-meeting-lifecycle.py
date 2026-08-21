#!/usr/bin/env python3
"""Run the bounded post-capture lifecycle for `murmurmark meeting`."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
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
GENERATOR = {"name": "run-meeting-lifecycle", "version": "0.1.4"}
DEFAULT_POST_STOP_BUDGET_RATIO = 1.0
DEFAULT_MAX_ENRICHMENT_BUDGET_SEC = 1800.0
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
TERMINAL_ACTION_STATUSES = {
    "passed",
    "skipped",
    "failed_soft",
    "deferred_budget_exhausted",
}
MAX_ACTION_ATTEMPTS = 3
MAX_MANUAL_DECISION_ITEMS = 100
REVIEW_APPLY_REPORT_SCHEMA = "murmurmark.review_workspace_apply_report/v1"
REMEDIATION_PREFIXES = (
    "murmurmark review ",
    "murmurmark meeting --resume ",
    "murmurmark process ",
    "murmurmark finish ",
    "murmurmark repair ",
    "murmurmark cleanup ",
)


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


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(value)
    except (OSError, json.JSONDecodeError):
        return []
    return rows


def review_row_key(row: dict[str, Any]) -> str:
    stable_id = str(row.get("source") or row.get("cluster_id") or "").strip()
    utterance_ids = row.get("utterance_ids")
    utterance_key = ",".join(str(value) for value in utterance_ids) if isinstance(utterance_ids, list) else ""
    interval = row.get("interval") if isinstance(row.get("interval"), dict) else {}
    return (
        f"review:{row.get('session_id') or ''}:{stable_id}:{utterance_key}:"
        f"{interval.get('start')}:{interval.get('end')}:{row.get('label')}"
    )


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
    return str(path.resolve())


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


def new_state(
    session: Path,
    record_elapsed_sec: float | None,
    keep_debug_artifacts: bool,
    post_stop_budget_ratio: float,
    max_enrichment_budget_sec: float,
) -> dict[str, Any]:
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
        "keep_debug_artifacts": keep_debug_artifacts,
        "budget_policy": {
            "post_stop_budget_ratio": post_stop_budget_ratio,
            "max_enrichment_budget_sec": max_enrichment_budget_sec,
        },
        "current_action": None,
        "next_action": "capture_validate",
        "transition_count": 0,
        "cumulative_transition_count": 0,
        "actions": {action: default_action_state() for action in ACTION_ORDER},
        "raw_inputs": [],
        "warnings": [],
        "resume_command": resume_command(session, keep_debug_artifacts),
    }


def resume_command(session: Path, keep_debug_artifacts: bool = False) -> str:
    command = f"murmurmark meeting --resume {shlex.quote(display_path(session))}"
    return f"{command} --keep-debug-artifacts" if keep_debug_artifacts else command


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
    state.setdefault("keep_debug_artifacts", False)
    state["resume_command"] = resume_command(session, bool(state["keep_debug_artifacts"]))
    state.setdefault("warnings", [])
    state.setdefault("transition_count", 0)
    state.setdefault("cumulative_transition_count", 0)
    state.setdefault("record_command_elapsed_sec", state.get("capture_elapsed_sec", 0.0))
    state.setdefault("capture_finalize_elapsed_sec", 0.0)
    state.setdefault(
        "budget_policy",
        {
            "post_stop_budget_ratio": DEFAULT_POST_STOP_BUDGET_RATIO,
            "max_enrichment_budget_sec": DEFAULT_MAX_ENRICHMENT_BUDGET_SEC,
        },
    )
    return state


def recover_state_for_resume(state: dict[str, Any], *, retry_deferred: bool = False) -> None:
    previous_transitions = int(state.get("transition_count") or 0)
    state["cumulative_transition_count"] = (
        int(state.get("cumulative_transition_count") or 0) + previous_transitions
    )
    state["transition_count"] = 0
    reset_downstream = False
    for action in ACTION_ORDER:
        action_state = state["actions"][action]
        status = action_state.get("status")
        if retry_deferred and action == "enrich" and status in {
            "deferred_budget_exhausted",
            "failed_soft",
            "skipped",
        }:
            action_state["status"] = "pending"
            action_state["error"] = None
            action_state["reason"] = "explicit resume retries incomplete deferred enrichment"
            reset_downstream = True
            continue
        if reset_downstream:
            action_state["status"] = "pending"
            action_state["error"] = None
            action_state["reason"] = "upstream deferred enrichment is being retried"
            continue
        if status in {"running", "interrupted"}:
            action_state["status"] = "pending"
            action_state["error"] = None
        elif status == "failed_hard" and action != "capture_validate":
            if int(action_state.get("attempts") or 0) < MAX_ACTION_ATTEMPTS:
                action_state["status"] = "pending"
                action_state["error"] = None
    state["status"] = "running"
    state["failure_reason"] = None
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
        keep_debug_artifacts: bool,
        post_stop_budget_ratio: float,
        max_enrichment_budget_sec: float,
    ) -> None:
        self.session = session.resolve()
        self.murmurmark_bin = murmurmark_bin.resolve()
        self.max_transitions = max_transitions
        self.record_elapsed_sec = record_elapsed_sec
        self.resume = resume
        self.keep_debug_artifacts = keep_debug_artifacts
        self.post_stop_budget_ratio = post_stop_budget_ratio
        self.max_enrichment_budget_sec = max_enrichment_budget_sec
        self.project_home = Path(os.environ.get("MURMURMARK_HOME") or Path.cwd()).resolve()
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
                self.state = new_state(
                    self.session,
                    self.record_elapsed_sec,
                    self.keep_debug_artifacts,
                    self.post_stop_budget_ratio,
                    self.max_enrichment_budget_sec,
                )
                self.state["project_home"] = str(self.project_home)
                self.event("lifecycle_started", resume=False)
            else:
                stored_home = existing.get("project_home")
                if isinstance(stored_home, str) and stored_home:
                    self.project_home = Path(stored_home).resolve()
                self.state = ensure_state_shape(existing, self.session)
                self.state["project_home"] = str(self.project_home)
                if self.keep_debug_artifacts:
                    self.state["keep_debug_artifacts"] = True
                    self.state["resume_command"] = resume_command(self.session, True)
                if self.state.get("status") in {"ready", "ready_with_review"}:
                    report = read_json(self.report_path)
                    if report:
                        raw_preserved, _ = self.verify_raw_preserved(emit_event=False)
                        if not raw_preserved and not self.raw_intentionally_archived():
                            return self.finish_failed("raw_capture_changed_after_completion")
                        refreshed = self.build_report(emit_raw_event=False)
                        if report_freshness_key(refreshed) != report_freshness_key(report):
                            self.state["status"] = refreshed["result"]
                            self.state["next_action"] = refreshed["next"]["action"]
                            self.state["finished_at"] = now_iso()
                            self.save_state()
                            self.write_report(refreshed)
                            self.write_final_next_action(refreshed)
                            self.event(
                                "lifecycle_report_refreshed",
                                result=refreshed["result"],
                                selected_profile=refreshed.get("selected_profile"),
                            )
                            report = refreshed
                        if not self.resume or self.deferred_is_complete():
                            print_summary(report)
                            return 0
                if self.resume:
                    self.state["budget_policy"] = {
                        "post_stop_budget_ratio": self.post_stop_budget_ratio,
                        "max_enrichment_budget_sec": self.max_enrichment_budget_sec,
                    }
                    recover_state_for_resume(
                        self.state,
                        retry_deferred=not self.deferred_is_complete(),
                    )
                    self.event("lifecycle_resumed", resume=True)
                else:
                    raise LifecycleError(
                        f"meeting lifecycle is {self.state.get('status')!r}; "
                        f"resume explicitly with `{self.state['resume_command']}`"
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
            if action == "enrich" and self.enrichment_budget_remaining_sec() <= 0:
                return f"skip:{action}", "post-stop enrichment budget is exhausted"
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
            "refresh_after_enrich": (
                "refresh readiness, current-profile speaker evidence and outcome after enrichment"
            ),
            "review_suggested_preview": "compute the conservative suggested-review remainder",
            "review_suggested_apply": "apply only rows accepted by existing safe suggestion gates",
            "refresh_after_review": (
                "refresh readiness, current-profile speaker evidence and outcome after suggested review"
            ),
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
            "refresh_after_enrich": [base, "report", session],
            "review_suggested_preview": [base, "review", "suggested", "preview", session],
            "review_suggested_apply": [base, "review", "suggested", "apply", session],
            "refresh_after_review": [base, "report", session],
            "finish": [base, "finish", session]
            + (["--keep-debug-artifacts"] if self.state.get("keep_debug_artifacts") else []),
        }
        return commands.get(action)

    def execute_action(self, action: str, reason: str) -> str:
        action_state = self.state["actions"][action]
        action_state["status"] = "running"
        action_state["attempts"] = int(action_state.get("attempts") or 0) + 1
        action_state["started_at"] = now_iso()
        action_state["reason"] = reason
        action_state["error"] = None
        if action == "enrich":
            action_state["budget_remaining_before_sec"] = rounded(
                self.enrichment_budget_remaining_sec()
            )
        if action == "process":
            action_state["pipeline_report_before"] = self.file_identity(self.pipeline_report_path())
            action_state["handoff_runs_before"] = self.file_identity(self.authoritative_handoff_runs_path())
        elif action in {"review_suggested_preview", "review_suggested_apply"}:
            action_state["review_apply_report_before"] = self.file_identity(
                self.review_apply_report_path()
            )
            if action == "review_suggested_apply":
                action_state["reviewed_decisions_before"] = self.reviewed_decision_count()
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
                timeout_sec = (
                    self.enrichment_budget_remaining_sec() if action == "enrich" else None
                )
                extra_env = None
                if action == "enrich" and timeout_sec is not None:
                    extra_env = {
                        "MURMURMARK_DEFERRED_BOUNDED": "1",
                        "MURMURMARK_DEFERRED_BUDGET_SEC": f"{timeout_sec:.6f}",
                    }
                elif action in {"review_suggested_preview", "review_suggested_apply"}:
                    # The lifecycle already spent its bounded enrichment window.
                    # Keep automatic review refresh cache-only so a new local
                    # faster-whisper batch cannot silently extend post-stop time.
                    extra_env = {"MURMURMARK_TARGETED_JUDGE_COMPUTE": "0"}
                return_code, interrupted, timed_out = self.run_command(
                    command,
                    timeout_sec=timeout_sec,
                    extra_env=extra_env,
                )
                if interrupted:
                    action_state["status"] = "interrupted"
                    action_state["finished_at"] = now_iso()
                    action_state["duration_sec"] = rounded(time.monotonic() - started)
                    self.save_state()
                    self.event("action_interrupted", action=action, returncode=return_code)
                    return "interrupted"
                if timed_out:
                    duration = rounded(time.monotonic() - started)
                    self.mark_deferred_pipeline_budget_exhausted()
                    action_state["status"] = "deferred_budget_exhausted"
                    action_state["finished_at"] = now_iso()
                    action_state["duration_sec"] = duration
                    action_state["returncode"] = return_code
                    action_state["error"] = None
                    action_state["reason"] = "post-stop enrichment budget exhausted during execution"
                    self.state["current_action"] = None
                    self.save_state()
                    self.event(
                        "action_deferred_budget_exhausted",
                        action=action,
                        duration_sec=duration,
                    )
                    return "deferred_budget_exhausted"
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

    def run_command(
        self,
        command: list[str],
        *,
        timeout_sec: float | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> tuple[int, bool, bool]:
        self.state["actions"][self.state["current_action"]]["command"] = command
        self.save_state()
        # Isolate each allowlisted action from the terminal's foreground process group.
        # The supervisor receives Ctrl-C and forwards it exactly once to the action.
        environment = os.environ.copy()
        if extra_env:
            environment.update(extra_env)
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            env=environment,
        )
        self.interrupts.child = process
        process_start = time.monotonic()
        interrupted_at: float | None = None
        timed_out_at: float | None = None
        graceful_timeout_sent = False
        terminate_sent = False
        kill_sent = False
        timed_out = False
        try:
            while process.poll() is None:
                now = time.monotonic()
                if (
                    timeout_sec is not None
                    and timeout_sec > 0
                    and timed_out_at is None
                    and now - process_start >= timeout_sec
                ):
                    timed_out = True
                    timed_out_at = now
                if self.interrupts.requested:
                    interrupted_at = interrupted_at or now
                    elapsed = now - interrupted_at
                    if elapsed > 15 and not kill_sent:
                        self.signal_process_group(process, signal.SIGKILL)
                        kill_sent = True
                    elif elapsed > 10 and not terminate_sent:
                        self.signal_process_group(process, signal.SIGTERM)
                        terminate_sent = True
                elif timed_out_at is not None:
                    elapsed = now - timed_out_at
                    if not graceful_timeout_sent:
                        # SIGINT lets run-session-pipeline persist an interrupted checkpoint.
                        self.signal_process_group(process, signal.SIGINT)
                        graceful_timeout_sent = True
                    elif elapsed > 10 and not terminate_sent:
                        self.signal_process_group(process, signal.SIGTERM)
                        terminate_sent = True
                    elif elapsed > 15 and not kill_sent:
                        self.signal_process_group(process, signal.SIGKILL)
                        kill_sent = True
                time.sleep(0.1)
            return int(process.returncode or 0), self.interrupts.requested, timed_out
        finally:
            self.interrupts.child = None

    @staticmethod
    def signal_process_group(process: subprocess.Popen[Any], signum: int) -> None:
        try:
            os.killpg(process.pid, signum)
        except ProcessLookupError:
            pass

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
        elif action in {"review_suggested_preview", "review_suggested_apply"}:
            report = read_json(self.review_apply_report_path())
            expected_dry_run = action == "review_suggested_preview"
            if report is None or report.get("schema") != REVIEW_APPLY_REPORT_SCHEMA:
                raise LifecycleError("suggested review did not produce a compatible apply report")
            if report.get("answers_source") != "suggested":
                raise LifecycleError("suggested review apply report has the wrong answers source")
            if report.get("dry_run") is not expected_dry_run:
                raise LifecycleError("suggested review apply report has the wrong dry-run state")
            action_state = self.state["actions"][action]
            if not self.file_changed(
                action_state.get("review_apply_report_before"), self.review_apply_report_path()
            ):
                raise LifecycleError("suggested review left a stale apply report unchanged")
            closure = report.get("suggested_closure")
            if not isinstance(closure, dict):
                raise LifecycleError("suggested review apply report has no closure evidence")
            if action == "review_suggested_apply":
                closed = (
                    closure.get("closed_by_suggestions")
                    if isinstance(closure.get("closed_by_suggestions"), dict)
                    else {}
                )
                action_state = self.state["actions"][action]
                reviewed_before = int(action_state.get("reviewed_decisions_before") or 0)
                reviewed_after = self.reviewed_decision_count()
                action_state["reviewed_decisions_after"] = reviewed_after
                if reviewed_after < reviewed_before:
                    raise LifecycleError("suggested review removed previously reviewed decisions")
                if int(closed.get("rows") or 0) <= 0 and reviewed_after <= reviewed_before:
                    raise LifecycleError("suggested review apply closed no safe rows")
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
        budget_exhausted = action == "enrich" and "budget" in reason
        action_state["status"] = (
            "deferred_budget_exhausted" if budget_exhausted else "skipped"
        )
        action_state["reason"] = reason
        action_state["finished_at"] = now_iso()
        self.state["transition_count"] = int(self.state.get("transition_count") or 0) + 1
        self.save_state()
        self.event(
            "action_deferred_budget_exhausted" if budget_exhausted else "action_skipped",
            action=action,
            reason=reason,
        )

    def budget_policy(self) -> tuple[float, float]:
        policy = self.state.get("budget_policy")
        if not isinstance(policy, dict):
            policy = {}
        ratio = max(
            0.0,
            float(policy.get("post_stop_budget_ratio") or 0.0),
        )
        maximum = max(
            0.0,
            float(policy.get("max_enrichment_budget_sec") or 0.0),
        )
        return ratio, maximum

    def required_elapsed_before_enrichment_sec(self) -> float:
        actions = self.state.get("actions") if isinstance(self.state.get("actions"), dict) else {}
        elapsed = float(self.state.get("capture_finalize_elapsed_sec") or 0.0)
        for action in ACTION_ORDER:
            if action == "enrich":
                break
            action_state = actions.get(action) if isinstance(actions.get(action), dict) else {}
            elapsed += float(action_state.get("duration_sec") or 0.0)
        return max(0.0, elapsed)

    def enrichment_budget_remaining_sec(self) -> float:
        ratio, maximum = self.budget_policy()
        capture = max(0.0, float(self.state.get("capture_elapsed_sec") or 0.0))
        total_budget = capture * ratio
        remaining = max(0.0, total_budget - self.required_elapsed_before_enrichment_sec())
        return min(remaining, maximum)

    def budget_report(self, total_after_stop_sec: float) -> dict[str, Any]:
        ratio, maximum = self.budget_policy()
        capture = max(0.0, float(self.state.get("capture_elapsed_sec") or 0.0))
        total_budget = capture * ratio
        actions = self.state.get("actions") if isinstance(self.state.get("actions"), dict) else {}
        enrich = actions.get("enrich") if isinstance(actions.get("enrich"), dict) else {}
        enrich_status = str(enrich.get("status") or "pending")
        reason: str | None = None
        status = "within_budget"
        if enrich_status == "deferred_budget_exhausted":
            status = "enrichment_deferred_budget_exhausted"
            reason = str(enrich.get("reason") or "post-stop enrichment budget exhausted")
        elif total_budget > 0 and total_after_stop_sec > total_budget:
            status = "required_work_exceeded_budget"
            reason = "required authoritative or bounded follow-up work exceeded the post-stop budget"
        return {
            "post_stop_budget_ratio": ratio,
            "post_stop_budget_sec": rounded(total_budget),
            "max_enrichment_budget_sec": rounded(maximum),
            "required_before_enrichment_sec": rounded(
                self.required_elapsed_before_enrichment_sec()
            ),
            "enrichment_budget_sec": rounded(
                float(enrich.get("budget_remaining_before_sec") or 0.0)
            ),
            "consumed_after_stop_sec": rounded(total_after_stop_sec),
            "remaining_after_stop_sec": rounded(max(0.0, total_budget - total_after_stop_sec)),
            "status": status,
            "reason": reason,
        }

    def deferred_work_report(self) -> dict[str, Any]:
        actions = self.state.get("actions") if isinstance(self.state.get("actions"), dict) else {}
        enrich = actions.get("enrich") if isinstance(actions.get("enrich"), dict) else {}
        action_status = str(enrich.get("status") or "pending")
        reason = str(enrich.get("reason") or "") or None
        if self.deferred_is_complete() or action_status == "passed":
            status = "completed"
            reason = reason or "deferred enrichment completed"
        elif action_status == "deferred_budget_exhausted":
            status = "deferred_budget_exhausted"
            reason = reason or "post-stop enrichment budget exhausted"
        elif action_status == "failed_soft":
            status = "failed_soft"
            reason = str(enrich.get("error") or reason or "optional enrichment failed")
        elif action_status == "skipped":
            status = "completed" if "complete" in str(reason or "") else "skipped"
        else:
            status = action_status
        return {
            "status": status,
            "reason": reason,
            "blocking": False,
            "command": f"murmurmark enrich {shlex.quote(display_path(self.session))}",
        }

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
        metrics = readiness.get("metrics") if isinstance(readiness.get("metrics"), dict) else {}
        blockers = string_list(readiness.get("review_blockers"))
        export_blockers = string_list(readiness.get("export_blockers"))
        review_metric_keys = (
            "needs_review_count",
            "review_scope_remaining_rows",
            "transcript_review_burden_sec",
            "review_scope_remaining_seconds",
            "suggested_closure_generated_rows",
            "suggested_closure_actionable_rows",
            "suggested_closure_auto_rows",
            "suggested_closure_manual_remaining_rows",
        )
        return bool(
            gate == "review_first"
            or gate.endswith("_review_first")
            or blockers
            or any("review" in blocker for blocker in export_blockers)
            or any(float(metrics.get(key) or 0.0) > 0 for key in review_metric_keys)
        )

    def safe_suggested_rows(self) -> int:
        report = read_json(self.review_apply_report_path()) or {}
        closure = (
            report.get("suggested_closure")
            if isinstance(report.get("suggested_closure"), dict)
            else {}
        )
        closed = (
            closure.get("closed_by_suggestions")
            if isinstance(closure.get("closed_by_suggestions"), dict)
            else {}
        )
        if report.get("answers_source") == "suggested" and report.get("dry_run") is True:
            preview_rows = closed.get("rows")
            if isinstance(preview_rows, (int, float)) and int(preview_rows) > 0:
                return int(preview_rows)
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

    def mark_deferred_pipeline_budget_exhausted(self) -> None:
        path = self.session / "derived" / "pipeline-run" / "pipeline_run_state.json"
        payload = read_json(path)
        if (
            payload is None
            or payload.get("schema") != "murmurmark.pipeline_run_state/v1"
            or payload.get("phase") != "deferred_enrichment"
        ):
            return
        payload["status"] = "deferred_budget_exhausted"
        payload["updated_at"] = now_iso()
        payload["message"] = "deferred_enrichment_budget_exhausted"
        payload["resume_command"] = f"murmurmark enrich {display_path(self.session)}"
        payload["safe_interrupt"] = True
        write_json(path, payload)

    def authoritative_handoff_path(self) -> Path:
        return self.session / "derived" / "pipeline-run" / "authoritative_handoff.json"

    def authoritative_handoff_runs_path(self) -> Path:
        return self.session / "derived" / "pipeline-run" / "authoritative_handoff_runs.jsonl"

    def readiness_path(self) -> Path:
        return self.session / "derived" / "readiness" / "session_readiness.json"

    def outcome_path(self) -> Path:
        return self.session / "derived" / "outcome" / "outcome.json"

    def review_apply_report_path(self) -> Path:
        return self.session / "derived" / "readiness" / "review-plan" / "review_workspace_apply_report.json"

    def review_progress_path(self) -> Path:
        return self.session / "derived" / "readiness" / "review-plan" / "review_decisions_progress.json"

    def review_decisions_path(self) -> Path:
        return self.session / "derived" / "readiness" / "review-plan" / "review_decisions.jsonl"

    def reviewed_decision_count(self) -> int:
        try:
            decisions = read_jsonl(self.review_decisions_path())
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return 0
        return sum(
            1
            for decision in decisions
            if str(decision.get("status") or "").strip().lower() == "reviewed"
            and str(decision.get("decision") or "").strip().lower()
            not in {"", "todo", "skip", "needs_review"}
        )

    def review_decisions_template_path(self) -> Path:
        return self.session / "derived" / "readiness" / "review-plan" / "review_decisions.template.jsonl"

    def review_decisions_report_path(self, profile: str) -> Path:
        return (
            self.session
            / "derived"
            / "transcript-simple"
            / "whisper-cpp"
            / "review-decisions"
            / f"review_decisions_report.{profile}.json"
        )

    def export_manifest_path(self) -> Path:
        return self.project_home / "exports" / "private" / self.session.name / "export_manifest.json"

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
        self.write_final_next_action(report)
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
        self.write_final_next_action(report)
        print_summary(report)
        return 2

    def finish_complete(self) -> int:
        report = self.build_report()
        self.state["status"] = report["result"]
        self.state["current_action"] = None
        self.state["next_action"] = report["next"]["action"]
        self.state["finished_at"] = now_iso()
        self.save_state()
        self.event("lifecycle_completed", result=report["result"])
        self.write_report(report)
        self.write_final_next_action(report)
        print_summary(report)
        return 0 if report["result"] in {"ready", "ready_with_review"} else 2

    def build_report(
        self,
        forced_result: str | None = None,
        reason: str | None = None,
        *,
        emit_raw_event: bool = True,
    ) -> dict[str, Any]:
        outcome = read_json(self.outcome_path()) or {}
        readiness = read_json(self.readiness_path()) or {}
        metrics = readiness.get("metrics") if isinstance(readiness.get("metrics"), dict) else {}
        summary = outcome.get("summary") if isinstance(outcome.get("summary"), dict) else {}
        transcript = self.output_path(outcome, "transcript")
        notes = self.output_path(outcome, "notes")
        verdict_path = self.output_path(outcome, "quality_verdict")
        raw_preserved, raw_after = self.verify_raw_preserved(emit_event=emit_raw_event)
        export_manifest = read_json(self.export_manifest_path())
        compaction_path = self.session / "derived/retention/derived_compaction.json"
        compaction_manifest = read_json(compaction_path) or {}
        compaction_application = (
            compaction_manifest.get("application")
            if isinstance(compaction_manifest.get("application"), dict)
            else {}
        )
        compaction_verification = (
            compaction_manifest.get("verification")
            if isinstance(compaction_manifest.get("verification"), dict)
            else {}
        )
        raw_archived = self.raw_intentionally_archived(compaction_manifest)
        raw_acceptable = raw_preserved or raw_archived
        actions = self.state.get("actions") if isinstance(self.state.get("actions"), dict) else {}
        finish_state = actions.get("finish") if isinstance(actions.get("finish"), dict) else {}
        current_profile = str(outcome.get("selected_profile") or readiness.get("selected_profile") or "")
        export_succeeded = bool(
            export_manifest
            and finish_state.get("status") == "passed"
            and export_manifest.get("status") in {"exported", "exported_with_warnings"}
            and (
                not current_profile
                or str(export_manifest.get("selected_profile") or "") == current_profile
            )
        )

        result = forced_result
        if result is None:
            if not raw_acceptable or transcript is None or not transcript.is_file():
                result = "failed"
                reason = reason or (
                    "raw_capture_changed"
                    if not raw_acceptable
                    else "authoritative_transcript_missing"
                )
            elif outcome.get("outcome") == "failed":
                result = "failed"
                reason = reason or f"outcome:{outcome.get('outcome')}"
            elif outcome.get("outcome") == "blocked":
                result = "ready_with_review"
                reason = reason or "structured_review_gate_remains"
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

        manual_decisions = self.manual_decision_items(outcome)
        if (
            manual_decisions.get("source") == display_path(self.review_progress_path())
            and manual_decisions.get("status") == "required"
        ):
            unresolved_count = int(manual_decisions["total"])
            unresolved_seconds = float(manual_decisions.get("remaining_seconds") or 0.0)
        else:
            unresolved_count = max_number(
                metrics,
                "review_scope_remaining_rows",
                "suggested_closure_manual_remaining_rows",
                "needs_review_count",
            )
            unresolved_seconds = max_number(
                metrics,
                "transcript_review_burden_sec",
                "review_scope_remaining_seconds",
                "suggested_closure_manual_remaining_seconds",
                "review_burden_sec",
            )
            unresolved_count = max(int(unresolved_count or 0), int(manual_decisions["total"]))
        action_times = {
            action: rounded(float(value.get("duration_sec") or 0.0))
            for action, value in actions.items()
            if isinstance(value, dict)
        }
        supervisor_elapsed = rounded(sum(action_times.values()))
        capture_elapsed = rounded(float(self.state.get("capture_elapsed_sec") or 0.0))
        capture_finalize_elapsed = rounded(float(self.state.get("capture_finalize_elapsed_sec") or 0.0))
        postprocess_elapsed = rounded(capture_finalize_elapsed + supervisor_elapsed)
        budgets = self.budget_report(postprocess_elapsed)
        deferred_work = self.deferred_work_report()
        resumable = result == "interrupted" or any(
            action != "capture_validate"
            and isinstance(value, dict)
            and value.get("status") == "failed_hard"
            and int(value.get("attempts") or 0) < MAX_ACTION_ATTEMPTS
            for action, value in actions.items()
        )
        warnings = list(dict.fromkeys(str(item) for item in self.state.get("warnings", []) if str(item)))
        if not raw_acceptable:
            warnings.append("raw capture SHA-256 identities changed")

        next_step = self.final_next_step(
            result=result,
            reason=reason,
            outcome=outcome,
            readiness=readiness,
            resume_available=resumable,
            manual_decisions=manual_decisions,
        )

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
            "selected_speaker_profile": outcome.get("selected_speaker_profile") or "aggregate_colleagues",
            "speaker_resolution": outcome.get("speaker_resolution") or {
                "state": "fallback",
                "selected_speaker_profile": "aggregate_colleagues",
                "fallback_reason": "outcome_speaker_resolution_missing",
            },
            "keep_debug_artifacts": bool(self.state.get("keep_debug_artifacts")),
            "unresolved_review": {
                "count": int(unresolved_count or 0),
                "seconds": rounded(float(unresolved_seconds or 0.0)),
                "blockers": review_blockers,
            },
            "manual_decisions": manual_decisions,
            "budgets": budgets,
            "deferred_work": deferred_work,
            "next": next_step,
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
            "derived_compaction": {
                "status": (
                    compaction_manifest.get("status")
                    if compaction_manifest
                    else (
                        "skipped_debug"
                        if self.state.get("keep_debug_artifacts")
                        else "not_attempted"
                    )
                ),
                "manifest": display_path(compaction_path) if compaction_manifest else None,
                "deleted_files": int(compaction_application.get("deleted_files") or 0),
                "deleted_bytes": int(compaction_application.get("deleted_bytes") or 0),
                "verification_passed": (
                    compaction_verification.get("passed")
                    if compaction_verification
                    else None
                ),
                "keep_debug_artifacts": bool(self.state.get("keep_debug_artifacts")),
            },
            "raw": {
                "preserved": raw_preserved,
                "archived": raw_archived,
                "acceptable": raw_acceptable,
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
            "resume_command": resume_command(
                self.session,
                bool(self.state.get("keep_debug_artifacts")),
            ),
            "resume_available": resumable,
        }

    def manual_decision_items(self, outcome: dict[str, Any]) -> dict[str, Any]:
        clean_dialogue = self.output_path(outcome, "clean_dialogue")
        payload = read_json(clean_dialogue) if clean_dialogue is not None else None
        utterances = payload.get("utterances") if isinstance(payload, dict) else []
        rows: list[dict[str, Any]] = []
        if isinstance(utterances, list):
            for utterance in utterances:
                if not isinstance(utterance, dict):
                    continue
                quality = utterance.get("quality") if isinstance(utterance.get("quality"), dict) else {}
                if quality.get("needs_review") is not True:
                    continue
                role = str(utterance.get("role") or "unknown").lower()
                rows.append(
                    {
                        "utterance_id": str(utterance.get("id") or "unknown"),
                        "role": role,
                        "start": rounded(float(utterance.get("start") or 0.0)),
                        "end": rounded(float(utterance.get("end") or utterance.get("start") or 0.0)),
                        "reason": str(quality.get("decision_reason") or "needs_review"),
                        "allowed_decisions": (
                            ["keep_me", "drop_me", "needs_review"]
                            if role == "me"
                            else ["keep", "needs_review"]
                        ),
                    }
                )
        rows.sort(key=lambda row: (row["start"], row["end"], row["utterance_id"]))
        completed_review = self.completed_review_decisions(outcome)
        if completed_review is not None:
            return {
                "schema": "murmurmark.meeting_manual_decisions/v1",
                "source": display_path(self.review_progress_path()),
                "status": "complete",
                "total": 0,
                "listed": 0,
                "truncated": False,
                "items": [],
                "residual_quality_flag_count": len(rows),
                "completion": completed_review,
            }
        progress = read_json(self.review_progress_path())
        progress_summary = (
            progress.get("summary")
            if isinstance(progress, dict) and isinstance(progress.get("summary"), dict)
            else {}
        )
        pending_rows: list[dict[str, Any]] = []
        decisions_path = self.review_decisions_path()
        template_path = self.review_decisions_template_path()
        if (
            progress.get("schema") == "murmurmark.review_decisions_progress/v1"
            if isinstance(progress, dict)
            else False
        ) and template_path.is_file() and int(progress_summary.get("invalid_rows") or 0) == 0:
            try:
                templates = read_jsonl(template_path)
                reviewed_by_key = {
                    review_row_key(decision): decision
                    for decision in read_jsonl(decisions_path)
                    if str(decision.get("decision") or "todo") != "todo"
                }
                for template in templates:
                    decision = dict(template)
                    reviewed = reviewed_by_key.get(review_row_key(template))
                    if reviewed:
                        decision.update({
                            key: reviewed[key]
                            for key in ("decision", "status", "reviewer", "notes")
                            if key in reviewed
                        })
                    if str(decision.get("decision") or decision.get("status") or "todo") != "todo":
                        continue
                    interval = decision.get("interval") if isinstance(decision.get("interval"), dict) else {}
                    utterance_ids = decision.get("utterance_ids")
                    pending_rows.append(
                        {
                            "review_id": str(
                                decision.get("source_audit_id")
                                or decision.get("cluster_id")
                                or "unknown"
                            ),
                            "review_lane": str(decision.get("review_lane") or "unknown"),
                            "start": rounded(float(interval.get("start") or 0.0)),
                            "end": rounded(float(interval.get("end") or interval.get("start") or 0.0)),
                            "reason": str(
                                decision.get("suggested_decision_reason")
                                or decision.get("label")
                                or "needs_review"
                            ),
                            "allowed_decisions": [
                                str(value)
                                for value in decision.get("allowed_decisions", [])
                                if isinstance(value, str)
                            ],
                            "utterance_ids": [
                                str(value)
                                for value in utterance_ids
                                if isinstance(value, str)
                            ] if isinstance(utterance_ids, list) else [],
                        }
                    )
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                pending_rows = []
        expected_pending = int(progress_summary.get("remaining") or 0)
        if pending_rows and len(pending_rows) == expected_pending:
            pending_rows.sort(key=lambda row: (row["start"], row["end"], row["review_id"]))
            return {
                "schema": "murmurmark.meeting_manual_decisions/v1",
                "source": display_path(self.review_progress_path()),
                "status": "required",
                "total": len(pending_rows),
                "listed": min(len(pending_rows), MAX_MANUAL_DECISION_ITEMS),
                "truncated": len(pending_rows) > MAX_MANUAL_DECISION_ITEMS,
                "items": pending_rows[:MAX_MANUAL_DECISION_ITEMS],
                "remaining_seconds": rounded(float(progress_summary.get("remaining_seconds") or 0.0)),
                "residual_quality_flag_count": len(rows),
            }
        total = len(rows)
        return {
            "schema": "murmurmark.meeting_manual_decisions/v1",
            "source": display_path(clean_dialogue) if clean_dialogue and clean_dialogue.is_file() else None,
            "status": "required" if total else "none",
            "total": total,
            "listed": min(total, MAX_MANUAL_DECISION_ITEMS),
            "truncated": total > MAX_MANUAL_DECISION_ITEMS,
            "items": rows[:MAX_MANUAL_DECISION_ITEMS],
            "residual_quality_flag_count": total,
        }

    def completed_review_decisions(self, outcome: dict[str, Any]) -> dict[str, Any] | None:
        profile = str(outcome.get("selected_profile") or "")
        if not profile:
            return None
        progress = read_json(self.review_progress_path())
        report_path = self.review_decisions_report_path(profile)
        report = read_json(report_path)
        if (
            not isinstance(progress, dict)
            or progress.get("schema") != "murmurmark.review_decisions_progress/v1"
            or not isinstance(report, dict)
            or report.get("schema") != "murmurmark.review_decisions_report/v1"
        ):
            return None
        progress_summary = (
            progress.get("summary") if isinstance(progress.get("summary"), dict) else {}
        )
        report_summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
        gates = report.get("gates") if isinstance(report.get("gates"), dict) else {}
        report_profile = str(
            report.get("output_profile") or report_summary.get("output_profile") or ""
        )
        progress_total = int(progress_summary.get("total") or 0)
        report_total = int(report_summary.get("decision_rows") or 0)
        if not (
            report_profile == profile
            and gates.get("passed") is True
            and progress_summary.get("ready_for_batch_apply") is True
            and int(progress_summary.get("remaining") or 0) == 0
            and int(progress_summary.get("invalid_rows") or 0) == 0
            and int(report_summary.get("pending_decision_rows") or 0) == 0
            and int(report_summary.get("rejected_decision_rows") or 0) == 0
            and int(report_summary.get("conflict_count") or 0) == 0
            and report_summary.get("review_scope_complete") is True
            and progress_total > 0
            and progress_total == report_total
            and int(progress_summary.get("reviewed") or 0) == progress_total
        ):
            return None
        return {
            "profile": profile,
            "progress": display_path(self.review_progress_path()),
            "application_report": display_path(report_path),
            "reviewed_rows": progress_total,
            "remaining_rows": 0,
        }

    def final_next_step(
        self,
        *,
        result: str,
        reason: str | None,
        outcome: dict[str, Any],
        readiness: dict[str, Any],
        resume_available: bool,
        manual_decisions: dict[str, Any],
    ) -> dict[str, Any]:
        if result == "interrupted" and resume_available:
            command = resume_command(self.session, bool(self.state.get("keep_debug_artifacts")))
            return {"status": "action_required", "action": "resume", "command": command, "reason": reason}
        if result == "failed":
            command = (
                resume_command(self.session, bool(self.state.get("keep_debug_artifacts")))
                if resume_available
                else None
            )
            return {
                "status": "action_required" if command else "terminal_failure",
                "action": "resume" if command else "failed",
                "command": command,
                "reason": reason,
            }
        commands = remediation_commands(readiness, outcome)
        if commands:
            return {
                "status": "action_required",
                "action": "follow_up",
                "command": commands[0],
                "alternatives": commands[1:],
                "reason": "structured remediation remains",
            }
        if int(manual_decisions.get("total") or 0) > 0:
            return {
                "status": "human_decision_required",
                "action": "human_decision",
                "command": None,
                "reason": "bounded transcript decisions remain",
            }
        summary = outcome.get("summary") if isinstance(outcome.get("summary"), dict) else {}
        if result == "ready_with_review" and summary.get("can_export") is True:
            return {
                "status": "action_required",
                "action": "finish",
                "command": f"murmurmark finish {shlex.quote(display_path(self.session))}",
                "reason": "guarded export remains",
            }
        if result == "ready_with_review":
            return {
                "status": "blocked_unactionable",
                "action": "blocked",
                "command": None,
                "reason": reason or "blocking follow-up has no executable remediation",
            }
        return {
            "status": "complete",
            "action": "complete",
            "command": None,
            "reason": reason or "no blocking follow-up remains",
        }

    def write_final_next_action(self, report: dict[str, Any]) -> None:
        next_step = report.get("next") if isinstance(report.get("next"), dict) else {}
        command_text = next_step.get("command")
        command = shlex.split(command_text) if isinstance(command_text, str) and command_text else None
        decision = {
            "complete": "terminal",
            "terminal_failure": "terminal",
            "human_decision_required": "human",
        }.get(str(next_step.get("status")), "run")
        payload = {
            "schema": NEXT_SCHEMA,
            "generator": GENERATOR,
            "generated_at": now_iso(),
            "session": display_path(self.session),
            "action": str(next_step.get("action") or "complete"),
            "decision": decision,
            "reason": str(next_step.get("reason") or "final lifecycle state"),
            "allowlisted": True,
            "command": command,
        }
        write_json(self.next_path, payload)
        self.state["next_action"] = payload["action"]
        self.save_state()

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

    def raw_intentionally_archived(
        self,
        payload: dict[str, Any] | None = None,
    ) -> bool:
        compaction = payload or read_json(
            self.session / "derived/retention/derived_compaction.json"
        )
        if not isinstance(compaction, dict):
            return False
        verification = (
            compaction.get("verification")
            if isinstance(compaction.get("verification"), dict)
            else {}
        )
        return bool(
            compaction.get("schema") == "murmurmark.derived_compaction/v1"
            and compaction.get("mode") == "transcript_only"
            and compaction.get("status") in {"applied", "verified"}
            and verification.get("passed") is True
            and verification.get("raw_deleted") is True
            and verification.get("retained_outputs_preserved") is True
        )

    def write_report(self, report: dict[str, Any]) -> None:
        write_json(self.report_path, report)
        unresolved = report["unresolved_review"]
        manual = report.get("manual_decisions") if isinstance(report.get("manual_decisions"), dict) else {}
        budgets = report.get("budgets") if isinstance(report.get("budgets"), dict) else {}
        deferred = report.get("deferred_work") if isinstance(report.get("deferred_work"), dict) else {}
        next_step = report.get("next") if isinstance(report.get("next"), dict) else {}
        elapsed = report["elapsed_sec"]
        compaction = report["derived_compaction"]
        lines = [
            "# Meeting Lifecycle",
            "",
            f"- Result: `{report['result']}`",
            f"- Session: `{report['session']}`",
            f"- Transcript: `{report.get('transcript') or 'not available'}`",
            f"- Notes: `{report.get('notes') or 'not available'}`",
            f"- Verdict: `{report.get('verdict') or 'unknown'}`",
            f"- Unresolved review: `{unresolved['count']}` items / `{unresolved['seconds']:.3f}s`",
            f"- Explicit manual decisions: `{int(manual.get('total') or 0)}`",
            f"- Post-stop budget: `{budgets.get('status') or 'unknown'}` / "
            f"`{float(budgets.get('post_stop_budget_sec') or 0.0):.3f}s`",
            f"- Deferred enrichment: `{deferred.get('status') or 'unknown'}`",
            f"- Next: `{next_step.get('status') or 'unknown'}`",
            f"- Export: `{report['export']['status']}`",
            f"- Derived compaction: `{compaction['status']}` / `{compaction['deleted_bytes']}` bytes",
            f"- Raw state: `{'preserved' if report['raw']['preserved'] else ('archived' if report['raw'].get('archived') else 'changed')}`",
            f"- Capture: `{elapsed['capture']:.3f}s`",
            f"- Capture finalization: `{elapsed['capture_finalize']:.3f}s`",
            f"- Authoritative process: `{elapsed['authoritative_process']:.3f}s`",
            f"- Enrichment: `{elapsed['enrichment']:.3f}s`",
            f"- Total after stop: `{elapsed['total_after_stop']:.3f}s`",
        ]
        if report.get("reason"):
            lines.append(f"- Reason: `{report['reason']}`")
        if budgets.get("reason"):
            lines.append(f"- Budget reason: `{budgets['reason']}`")
        if deferred.get("reason"):
            lines.append(f"- Deferred reason: `{deferred['reason']}`")
        if unresolved.get("blockers"):
            lines.append(f"- Review blockers: `{', '.join(unresolved['blockers'])}`")
        if report["export"].get("blockers"):
            lines.append(f"- Export blockers: `{', '.join(report['export']['blockers'])}`")
        if next_step.get("command"):
            lines += ["", "Next:", "", f"```bash\n{next_step['command']}\n```"]
        elif int(manual.get("total") or 0) > 0:
            lines += ["", "## Manual Decisions", ""]
            for item in manual.get("items", []):
                item_id = str(item.get("review_id") or item.get("utterance_id") or "unknown")
                item_scope = str(item.get("review_lane") or item.get("role") or "unknown")
                lines.append(
                    f"- `{item_id}` `{item_scope}` "
                    f"`{item['start']:.3f}..{item['end']:.3f}s`: `{item['reason']}`"
                )
            if manual.get("truncated"):
                lines.append(f"- ... `{int(manual['total']) - int(manual['listed'])}` more in source JSON")
        if report.get("resume_available"):
            lines += ["", "Resume:", "", f"```bash\n{report['resume_command']}\n```"]
        self.report_md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def max_number(payload: dict[str, Any], *keys: str) -> float | int | None:
    values = [payload.get(key) for key in keys]
    numbers = [value for value in values if isinstance(value, (int, float))]
    return max(numbers) if numbers else None


def string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(item.strip() for item in value if isinstance(item, str) and item.strip()))


def normalized_command_items(payload: dict[str, Any]) -> list[str]:
    rows: list[str] = []
    raw = payload.get("next_commands")
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str) and item.strip():
                rows.append(item.strip())
            elif isinstance(item, dict) and isinstance(item.get("command"), str):
                command = item["command"].strip()
                if command:
                    rows.append(command)
    recommended = payload.get("recommended_next")
    if isinstance(recommended, str) and recommended.strip():
        rows.append(recommended.strip())
    return rows


def remediation_commands(*payloads: dict[str, Any]) -> list[str]:
    commands: list[str] = []
    for payload in payloads:
        for command in normalized_command_items(payload):
            normalized = " ".join(command.split())
            if normalized.startswith(REMEDIATION_PREFIXES) and normalized not in commands:
                commands.append(normalized)
    return commands


def report_freshness_key(report: dict[str, Any]) -> dict[str, Any]:
    raw = report.get("raw") if isinstance(report.get("raw"), dict) else {}
    return {
        "result": report.get("result"),
        "reason": report.get("reason"),
        "transcript": report.get("transcript"),
        "notes": report.get("notes"),
        "verdict": report.get("verdict"),
        "verdict_path": report.get("verdict_path"),
        "selected_profile": report.get("selected_profile"),
        "selected_speaker_profile": report.get("selected_speaker_profile"),
        "speaker_resolution": report.get("speaker_resolution"),
        "unresolved_review": report.get("unresolved_review"),
        "manual_decisions": report.get("manual_decisions"),
        "budgets": report.get("budgets"),
        "deferred_work": report.get("deferred_work"),
        "next": report.get("next"),
        "export": report.get("export"),
        "derived_compaction": report.get("derived_compaction"),
        "raw_preserved": raw.get("preserved"),
        "raw_archived": raw.get("archived"),
    }


def print_summary(report: dict[str, Any]) -> None:
    unresolved = report.get("unresolved_review") if isinstance(report.get("unresolved_review"), dict) else {}
    export = report.get("export") if isinstance(report.get("export"), dict) else {}
    raw = report.get("raw") if isinstance(report.get("raw"), dict) else {}
    elapsed = report.get("elapsed_sec") if isinstance(report.get("elapsed_sec"), dict) else {}
    compaction = (
        report.get("derived_compaction")
        if isinstance(report.get("derived_compaction"), dict)
        else {}
    )
    budgets = report.get("budgets") if isinstance(report.get("budgets"), dict) else {}
    deferred = report.get("deferred_work") if isinstance(report.get("deferred_work"), dict) else {}
    print("")
    print(f"SESSION=\"{report.get('session')}\"")
    print("meeting:")
    print(f"  result: {report.get('result')}")
    print(f"  transcript: {report.get('transcript') or 'not_available'}")
    print(f"  notes: {report.get('notes') or 'not_available'}")
    print(f"  verdict: {report.get('verdict') or 'unknown'}")
    print(f"  speaker_profile: {report.get('selected_speaker_profile') or 'aggregate_colleagues'}")
    speaker = report.get("speaker_resolution") if isinstance(report.get("speaker_resolution"), dict) else {}
    if speaker.get("fallback_reason"):
        print(f"  speaker_fallback_reason: {speaker['fallback_reason']}")
    print(f"  unresolved: {int(unresolved.get('count') or 0)} items / {float(unresolved.get('seconds') or 0.0):.3f}s")
    manual = report.get("manual_decisions") if isinstance(report.get("manual_decisions"), dict) else {}
    next_step = report.get("next") if isinstance(report.get("next"), dict) else {}
    print(f"  manual_decisions: {int(manual.get('total') or 0)}")
    print(
        "  post_stop_budget: "
        f"{budgets.get('status') or 'unknown'} "
        f"({float(budgets.get('consumed_after_stop_sec') or 0.0):.1f}/"
        f"{float(budgets.get('post_stop_budget_sec') or 0.0):.1f}s)"
    )
    print(f"  deferred_enrichment: {deferred.get('status') or 'unknown'}")
    print(f"  next: {next_step.get('status') or 'unknown'}")
    print(f"  export: {export.get('status') or 'not_attempted'}")
    print(
        "  derived_compaction: "
        f"{compaction.get('status') or 'not_attempted'} "
        f"({int(compaction.get('deleted_bytes') or 0)} bytes)"
    )
    raw_state = (
        "preserved"
        if raw.get("preserved") is True
        else ("archived" if raw.get("archived") is True else "changed")
    )
    print(f"  raw_capture: {raw_state}")
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
    if next_step.get("command"):
        print(f"  next_command: {next_step['command']}")
    if report.get("resume_available"):
        print(f"  resume: {report.get('resume_command')}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path)
    parser.add_argument("--murmurmark-bin", type=Path, required=True)
    parser.add_argument("--record-elapsed-sec", "--capture-elapsed-sec", dest="record_elapsed_sec", type=float)
    parser.add_argument("--max-transitions", type=int, default=16)
    parser.add_argument(
        "--post-stop-budget-ratio",
        type=float,
        default=DEFAULT_POST_STOP_BUDGET_RATIO,
    )
    parser.add_argument(
        "--max-enrichment-budget-sec",
        type=float,
        default=DEFAULT_MAX_ENRICHMENT_BUDGET_SEC,
    )
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--keep-debug-artifacts", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.max_transitions < len(ACTION_ORDER):
            raise LifecycleError(f"--max-transitions must be at least {len(ACTION_ORDER)}")
        if not math.isfinite(args.post_stop_budget_ratio) or args.post_stop_budget_ratio < 0:
            raise LifecycleError("--post-stop-budget-ratio must be a finite non-negative number")
        if not math.isfinite(args.max_enrichment_budget_sec) or args.max_enrichment_budget_sec < 0:
            raise LifecycleError("--max-enrichment-budget-sec must be a finite non-negative number")
        lifecycle = MeetingLifecycle(
            session=args.session,
            murmurmark_bin=args.murmurmark_bin,
            max_transitions=args.max_transitions,
            record_elapsed_sec=args.record_elapsed_sec,
            resume=args.resume,
            keep_debug_artifacts=args.keep_debug_artifacts,
            post_stop_budget_ratio=args.post_stop_budget_ratio,
            max_enrichment_budget_sec=args.max_enrichment_budget_sec,
        )
        return lifecycle.run()
    except LockBusyError as error:
        print(f"error: {error}", file=sys.stderr)
        print(
            f"resume: {resume_command(args.session.resolve(), args.keep_debug_artifacts)}",
            file=sys.stderr,
        )
        return 3
    except LifecycleError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
