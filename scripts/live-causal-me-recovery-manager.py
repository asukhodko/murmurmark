#!/usr/bin/env python3
"""Nonblocking latest-only supervisor for recording-time causal Me recovery."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable


SCHEMA = "murmurmark.live_causal_me_recovery_worker/v1"
SCRIPT_VERSION = "1.0.0"


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


class CausalMeRecoveryManager:
    def __init__(
        self,
        *,
        session: Path,
        model: Path,
        language: str,
        whisper_cli: str,
        timeout_sec: float,
        max_live_lag_sec: float,
        runtime_script: Path | None = None,
        register_child: Callable[[subprocess.Popen[str]], None] | None = None,
        unregister_child: Callable[[subprocess.Popen[str]], None] | None = None,
    ) -> None:
        self.session = session
        self.model = model
        self.language = language
        self.whisper_cli = whisper_cli
        self.timeout_sec = max(0.05, timeout_sec)
        self.max_live_lag_sec = max_live_lag_sec
        self.runtime_script = runtime_script or Path(__file__).with_name("live-causal-me-recovery-runtime.py")
        self.register_child = register_child
        self.unregister_child = unregister_child
        self.output = session / "derived/live/causal-me-recovery-runtime-v1"
        self.state_path = self.output / "worker_state.json"
        self.events_path = self.output / "worker_events.jsonl"
        self.process: subprocess.Popen[str] | None = None
        self.process_started = 0.0
        self.active: dict[str, Any] | None = None
        self.pending: dict[str, Any] | None = None
        self.stopping = False
        self.completed_invocations = 0
        self.failed_invocations = 0
        self.timed_out_invocations = 0
        self.coalesced_submissions = 0
        self.pending_skipped_at_stop_count = 0
        self.last_completed_chunk = 0
        self.last_completed_chunk_end_sec = 0.0
        self.last_observed_live_lag_sec = 0.0
        self.max_observed_live_lag_sec = 0.0
        self.final_live_lag_sec: float | None = None
        self.last_error: str | None = None
        self._persist("idle")

    def _event(self, kind: str, **payload: Any) -> None:
        append_jsonl(
            self.events_path,
            {
                "schema": SCHEMA,
                "created_at": now_iso(),
                "kind": kind,
                **payload,
                "batch_authoritative": True,
                "promotion_allowed": False,
            },
        )

    def _persist(self, status: str) -> None:
        write_json(
            self.state_path,
            {
                "schema": SCHEMA,
                "generator": {"name": "live-causal-me-recovery-manager", "version": SCRIPT_VERSION},
                "updated_at": now_iso(),
                "status": status,
                "mode": "bounded_latest_only_child",
                "active": self.active,
                "pending": self.pending,
                "completed_invocations": self.completed_invocations,
                "failed_invocations": self.failed_invocations,
                "timed_out_invocations": self.timed_out_invocations,
                "coalesced_submissions": self.coalesced_submissions,
                "pending_skipped_at_stop_count": self.pending_skipped_at_stop_count,
                "last_completed_chunk": self.last_completed_chunk,
                "last_completed_chunk_end_sec": round(self.last_completed_chunk_end_sec, 3),
                "last_observed_live_lag_sec": round(self.last_observed_live_lag_sec, 3),
                "max_observed_live_lag_sec": round(self.max_observed_live_lag_sec, 3),
                "final_live_lag_sec": (
                    round(self.final_live_lag_sec, 3)
                    if self.final_live_lag_sec is not None
                    else None
                ),
                "within_live_lag_budget": (
                    self.final_live_lag_sec is None
                    or self.max_live_lag_sec <= 0.0
                    or self.final_live_lag_sec <= self.max_live_lag_sec
                ),
                "last_error": self.last_error,
                "timeout_sec": self.timeout_sec,
                "max_live_lag_sec": self.max_live_lag_sec,
                "normal_preview_connected": False,
                "base_draft_fallback": True,
                "batch_authoritative": True,
                "promotion_allowed": False,
            },
        )

    def submit(
        self,
        *,
        chunk_index: int,
        chunk_end_sec: float,
        captured_sec: float,
        recording_active: bool,
    ) -> None:
        self.poll(captured_sec=captured_sec)
        item = {
            "chunk_index": chunk_index,
            "chunk_end_sec": round(chunk_end_sec, 3),
            "captured_sec": round(captured_sec, 3),
            "submitted_at": now_iso(),
            "recording_active": recording_active,
        }
        if self.process is not None:
            if self.pending is not None:
                self.coalesced_submissions += 1
                self._event(
                    "submission_coalesced",
                    replaced_chunk_index=self.pending.get("chunk_index"),
                    replacement_chunk_index=chunk_index,
                )
            self.pending = item
            self._persist("running_with_pending")
            return
        self.pending = item
        self._launch_pending(captured_sec=captured_sec)

    def _launch_pending(self, *, captured_sec: float) -> None:
        if self.pending is None or self.stopping:
            return
        item = self.pending
        self.pending = None
        lag = max(0.0, captured_sec - float(item["chunk_end_sec"]))
        self.last_observed_live_lag_sec = lag
        self.max_observed_live_lag_sec = max(self.max_observed_live_lag_sec, lag)
        if self.max_live_lag_sec > 0.0 and lag > self.max_live_lag_sec:
            self.failed_invocations += 1
            self.last_error = "live_lag_budget_exceeded"
            self._event(
                "skipped_lag_budget",
                chunk_index=item["chunk_index"],
                observed_live_lag_sec=round(lag, 3),
                max_live_lag_sec=self.max_live_lag_sec,
            )
            self._persist("base_draft_fallback_lag")
            return
        invocation_id = f"runtime_{int(time.time() * 1000)}_{int(item['chunk_index']):06d}"
        command = [
            sys.executable,
            str(self.runtime_script),
            str(self.session),
            "--through-chunk-index",
            str(item["chunk_index"]),
            "--model",
            str(self.model),
            "--language",
            self.language,
            "--whisper-cli",
            self.whisper_cli,
            "--invocation-id",
            invocation_id,
            "--submitted-at",
            str(item["submitted_at"]),
        ]
        if item.get("recording_active"):
            command.append("--recording-active")
        try:
            self.process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
        except OSError as error:
            self.failed_invocations += 1
            self.last_error = f"child_launch_failed: {error}"
            self.active = None
            self._event(
                "child_launch_failed",
                chunk_index=item["chunk_index"],
                error=str(error),
            )
            self._persist("base_draft_fallback_error")
            return
        if self.register_child:
            self.register_child(self.process)
        self.process_started = time.monotonic()
        self.active = {
            **item,
            "invocation_id": invocation_id,
            "pid": self.process.pid,
            "observed_live_lag_sec": round(lag, 3),
            "started_at": now_iso(),
        }
        self._event("started", **self.active)
        self._persist("running")

    def _terminate(self) -> None:
        if self.process is None or self.process.poll() is not None:
            return
        try:
            os.killpg(self.process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            self.process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def poll(self, *, captured_sec: float) -> None:
        if self.process is None:
            if self.pending is not None and not self.stopping:
                self._launch_pending(captured_sec=captured_sec)
            return
        elapsed = time.monotonic() - self.process_started
        if self.process.poll() is None and elapsed <= self.timeout_sec:
            return
        timed_out = self.process.poll() is None
        if timed_out:
            self._terminate()
        stdout, stderr = self.process.communicate()
        returncode = self.process.returncode
        if self.unregister_child:
            self.unregister_child(self.process)
        active = self.active or {}
        completed_lag = max(0.0, captured_sec - float(active.get("chunk_end_sec") or 0.0))
        self.last_observed_live_lag_sec = completed_lag
        self.max_observed_live_lag_sec = max(self.max_observed_live_lag_sec, completed_lag)
        if timed_out:
            self.timed_out_invocations += 1
            self.failed_invocations += 1
            self.last_error = "causal_me_recovery_timeout"
            status = "base_draft_fallback_timeout"
        elif returncode == 0:
            self.completed_invocations += 1
            self.last_completed_chunk = max(
                self.last_completed_chunk,
                int(active.get("chunk_index") or 0),
            )
            self.last_completed_chunk_end_sec = max(
                self.last_completed_chunk_end_sec,
                float(active.get("chunk_end_sec") or 0.0),
            )
            self.last_error = None
            status = "completed_one"
        else:
            self.failed_invocations += 1
            self.last_error = f"child_exit_{returncode}"
            status = "base_draft_fallback_error"
        self._event(
            "finished",
            invocation_id=active.get("invocation_id"),
            chunk_index=active.get("chunk_index"),
            status=status,
            returncode=returncode,
            timed_out=timed_out,
            elapsed_sec=round(elapsed, 3),
            stdout_tail=stdout[-2000:],
            stderr_tail=stderr[-2000:],
        )
        self.process = None
        self.active = None
        self._persist(status)
        if self.pending is not None and not self.stopping:
            self._launch_pending(captured_sec=captured_sec)

    def finish(self, *, captured_sec: float, wait_sec: float) -> None:
        self.stopping = True
        if self.pending is not None:
            self.pending_skipped_at_stop_count += 1
            self._event(
                "pending_skipped_at_stop",
                chunk_index=self.pending.get("chunk_index"),
                reason="base_draft_already_durable",
            )
            self.pending = None
        deadline = time.monotonic() + max(0.0, wait_sec)
        while self.process is not None and time.monotonic() < deadline:
            self.poll(captured_sec=captured_sec)
            if self.process is not None:
                time.sleep(0.1)
        if self.process is not None:
            self._terminate()
            stdout, stderr = self.process.communicate()
            if self.unregister_child:
                self.unregister_child(self.process)
            self.failed_invocations += 1
            self.last_error = "stop_wait_budget_exceeded"
            self._event(
                "terminated_at_stop",
                invocation_id=(self.active or {}).get("invocation_id"),
                chunk_index=(self.active or {}).get("chunk_index"),
                stdout_tail=stdout[-1000:],
                stderr_tail=stderr[-1000:],
            )
            self.process = None
            self.active = None
        self.final_live_lag_sec = max(0.0, captured_sec - self.last_completed_chunk_end_sec)
        within_lag_budget = bool(
            self.max_live_lag_sec <= 0.0
            or self.final_live_lag_sec <= self.max_live_lag_sec
        )
        status = (
            "completed"
            if self.failed_invocations == 0
            and self.pending_skipped_at_stop_count == 0
            and within_lag_budget
            else "completed_with_fallback"
        )
        self._persist(status)

    def disable(self, reason: str) -> None:
        """Terminate only the optional child and persist an explicit fail-open state."""
        self.stopping = True
        self.pending = None
        if self.process is not None:
            self._terminate()
            try:
                self.process.communicate(timeout=0.5)
            except subprocess.TimeoutExpired:
                self._terminate()
            if self.unregister_child:
                self.unregister_child(self.process)
            self.process = None
            self.active = None
        self.failed_invocations += 1
        self.last_error = reason
        self._event("disabled_fail_open", reason=reason)
        self._persist("disabled_fail_open")


__all__ = ["CausalMeRecoveryManager"]
