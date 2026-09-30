"""Shared lifecycle deadlines for bounded, resumable follow-up commands."""

from __future__ import annotations

import os
import math
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


DEADLINE_ENV = "MURMURMARK_ACTION_DEADLINE_EPOCH"
BUDGET_EXIT = 75


class ActionStopped(RuntimeError):
    def __init__(self, returncode: int):
        self.returncode = returncode
        super().__init__("deferred_budget_exhausted" if returncode == BUDGET_EXIT else "interrupted")


def check_deadline() -> None:
    remaining = remaining_seconds()
    if remaining is not None and remaining <= 0:
        raise ActionStopped(BUDGET_EXIT)


def remaining_seconds() -> float | None:
    try:
        deadline = float(os.environ.get(DEADLINE_ENV, ""))
    except ValueError:
        return None
    if not math.isfinite(deadline):
        return None
    return max(0.0, deadline - time.time())


def process_snapshot() -> dict[int, tuple[int, str]]:
    try:
        result = subprocess.run(
            ["/bin/ps", "-axo", "pid=,ppid=,stat=,lstart="],
            capture_output=True, text=True, timeout=2, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    snapshot = {}
    for line in result.stdout.splitlines():
        fields = line.split(maxsplit=3)
        if len(fields) == 4 and not fields[2].startswith("Z"):
            snapshot[int(fields[0])] = (int(fields[1]), fields[3])
    return snapshot


def extend_owned_processes(owned: dict[int, str], snapshot: dict[int, tuple[int, str]]) -> None:
    # Birth time prevents signaling unrelated work after PID reuse.
    active = {pid for pid, birth in owned.items() if snapshot.get(pid, (None, None))[1] == birth}
    while True:
        children = {pid for pid, (parent, _) in snapshot.items() if parent in active and pid not in active}
        if not children:
            return
        owned.update({pid: snapshot[pid][1] for pid in children})
        active.update(children)


def run_bounded(command: list[str], *, capture_output: bool = True,
                cwd: Path | None = None, max_seconds: float | None = None) -> dict[str, Any]:
    remaining = remaining_seconds()
    if max_seconds is not None:
        remaining = min(remaining, max_seconds) if remaining is not None else max_seconds
    if remaining is not None and remaining <= 0:
        return {"command": command, "returncode": BUDGET_EXIT, "status": "budget_exhausted",
                "stdout": "", "stderr": "", "started": False}
    environment = os.environ.copy()
    if remaining is not None:
        environment[DEADLINE_ENV] = str(time.time() + remaining)
    process = subprocess.Popen(command, cwd=cwd, env=environment, text=True, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE if capture_output else None,
                               stderr=subprocess.PIPE,
                               start_new_session=True)
    status = "completed"
    try:
        stdout, stderr = process.communicate(timeout=remaining)
    except (subprocess.TimeoutExpired, KeyboardInterrupt) as error:
        status = "interrupted" if isinstance(error, KeyboardInterrupt) else "budget_exhausted"
        # Freeze descendants before their wrappers can exit; nested supervisors
        # and Foundation helpers may own separate process groups.
        snapshot = process_snapshot()
        owned = {process.pid: snapshot[process.pid][1]} if process.pid in snapshot else {}
        extend_owned_processes(owned, snapshot)
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGKILL):
            snapshot = process_snapshot()
            extend_owned_processes(owned, snapshot)
            if sig == signal.SIGINT:
                if process.poll() is None:
                    try:
                        process.send_signal(sig)
                    except ProcessLookupError:
                        pass
            else:
                for pid, birth in reversed(list(owned.items())):
                    if snapshot.get(pid, (None, None))[1] == birth:
                        try:
                            os.kill(pid, sig)
                        except ProcessLookupError:
                            pass
                try:
                    os.killpg(process.pid, sig)
                except ProcessLookupError:
                    pass
            try:
                stdout, stderr = process.communicate(timeout=1.0)
            except subprocess.TimeoutExpired:
                continue
            snapshot = process_snapshot()
            extend_owned_processes(owned, snapshot)
            if any(snapshot.get(pid, (None, None))[1] == birth for pid, birth in owned.items()):
                continue
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                break
        stdout, stderr = process.communicate()
    if status == "completed":
        if process.returncode == BUDGET_EXIT:
            status = "budget_exhausted"
        elif process.returncode in {130, -signal.SIGINT}:
            status = "interrupted"
    if not capture_output and stderr:
        if status == "completed":
            print(stderr, file=sys.stderr, end="")
        else:
            # Retain raw diagnostics in the result, but do not present a supervisor
            # cancellation of a frozen child as a user-facing Python failure.
            print(f"followup: {status}; child diagnostics retained", file=sys.stderr)
    return {"command": command,
            "returncode": (130 if status == "interrupted" else BUDGET_EXIT)
            if status != "completed" else process.returncode,
            "status": status, "stdout": (stdout or "").strip(),
            "stderr": (stderr or "").strip(), "started": True}
