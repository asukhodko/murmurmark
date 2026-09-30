#!/usr/bin/env python3
"""Exercise the global heavy-processing lease and its recovery guarantees."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from murmurmark_processing_lease import ProcessingLease, pid_is_alive  # noqa: E402


def append_event(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o644)
    try:
        row = (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        os.write(descriptor, row)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def read_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def worker(args: argparse.Namespace) -> int:
    sessions_root = args.sessions_root.resolve()
    session = sessions_root / args.name
    lease = ProcessingLease(
        sessions_root=sessions_root,
        session=session,
        phase="handoff",
        poll_interval_sec=0.02,
        heartbeat_interval_sec=0.05,
    )

    def on_wait(snapshot: dict[str, Any]) -> None:
        if not any(
            row.get("event") == "waiting" and row.get("name") == args.name
            for row in read_events(args.events)
        ):
            append_event(
                args.events,
                {
                    "event": "waiting",
                    "name": args.name,
                    "queue_position": snapshot.get("queue_position"),
                    "capture_blocked": snapshot.get("capture_blocked"),
                },
            )

    try:
        acquired = lease.acquire(on_wait)
    except KeyboardInterrupt:
        append_event(args.events, {"event": "interrupted", "name": args.name})
        return 130

    append_event(
        args.events,
        {
            "event": "acquired",
            "name": args.name,
            "capture_blocked": acquired.get("capture_blocked"),
        },
    )
    time.sleep(args.hold_sec)
    released = lease.release()
    append_event(
        args.events,
        {
            "event": "released",
            "name": args.name,
            "capture_blocked": released.get("capture_blocked"),
        },
    )
    return 0


def wait_until(predicate: Callable[[], bool], message: str, timeout_sec: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError(message)


def start_worker(
    sessions_root: Path,
    events: Path,
    name: str,
    hold_sec: float,
) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--worker",
            "--sessions-root",
            str(sessions_root),
            "--events",
            str(events),
            "--name",
            name,
            "--hold-sec",
            str(hold_sec),
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def require_exit(process: subprocess.Popen[str], expected: int, name: str) -> None:
    stdout, stderr = process.communicate(timeout=10)
    assert process.returncode == expected, (
        f"{name} exited with {process.returncode}, expected {expected}\n"
        f"stdout:\n{stdout}\nstderr:\n{stderr}"
    )


def ticket_for(sessions_root: Path, name: str) -> bool:
    queue = sessions_root / ".murmurmark-processing/queue"
    for path in queue.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if str(payload.get("session") or "").endswith(f"/{name}"):
            return True
    return False


def check_fifo_and_capture_independence(sessions_root: Path, events: Path) -> None:
    owner = start_worker(sessions_root, events, "session-a", 0.6)
    wait_until(
        lambda: any(
            row.get("event") == "acquired" and row.get("name") == "session-a"
            for row in read_events(events)
        ),
        "first worker did not acquire the processing lease",
    )

    recording_lock_path = sessions_root / ".murmurmark-recording.lock"
    with recording_lock_path.open("a+", encoding="utf-8") as recording_lock:
        fcntl.flock(recording_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(recording_lock.fileno(), fcntl.LOCK_UN)

    second = start_worker(sessions_root, events, "session-b", 0.1)
    wait_until(
        lambda: ticket_for(sessions_root, "session-b"),
        "second worker did not enter the processing queue",
    )
    third = start_worker(sessions_root, events, "session-c", 0.1)

    for process, name in ((owner, "session-a"), (second, "session-b"), (third, "session-c")):
        require_exit(process, 0, name)

    acquisition_order = [
        str(row["name"])
        for row in read_events(events)
        if row.get("event") == "acquired" and row.get("name") in {"session-a", "session-b", "session-c"}
    ]
    assert acquisition_order == ["session-a", "session-b", "session-c"], acquisition_order
    assert all(
        row.get("capture_blocked") is False
        for row in read_events(events)
        if row.get("event") in {"waiting", "acquired", "released"}
    )


def check_stale_ticket_cleanup(sessions_root: Path, events: Path) -> None:
    queue = sessions_root / ".murmurmark-processing/queue"
    queue.mkdir(parents=True, exist_ok=True)
    stale_pid = 99_999_999
    while pid_is_alive(stale_pid):
        stale_pid -= 1
    stale = queue / "00000000000000000000-stale.json"
    stale.write_text(
        json.dumps(
            {
                "schema": "murmurmark.processing_lease_ticket/v1",
                "pid": stale_pid,
                "host": socket.gethostname(),
                "session": str(sessions_root / "stale"),
                "phase": "handoff",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    process = start_worker(sessions_root, events, "session-after-stale", 0.0)
    require_exit(process, 0, "session-after-stale")
    assert not stale.exists(), "stale same-host queue ticket was not removed"


def check_incomplete_ticket_is_never_visible(sessions_root: Path) -> None:
    queue = sessions_root / ".murmurmark-processing/queue"
    queue.mkdir(parents=True, exist_ok=True)
    partial = queue / ".partial-ticket.json.test.tmp"
    partial.write_text('{"schema":', encoding="utf-8")

    lease = ProcessingLease(
        sessions_root=sessions_root,
        session=sessions_root / "session-atomic-ticket",
        phase="handoff",
        poll_interval_sec=0.02,
    )
    acquired = lease.acquire()
    assert acquired["status"] == "acquired"
    assert partial.exists(), "queue cleanup must ignore unpublished temporary tickets"
    lease.release()
    partial.unlink()


def check_failed_ticket_write_is_cleaned(sessions_root: Path) -> None:
    for name, side_effect in (
        ("ticket-fsync", [OSError("fixture file fsync failure")]),
        ("directory-fsync", [None, OSError("fixture directory fsync failure")]),
    ):
        isolated_root = sessions_root / name
        lease = ProcessingLease(
            sessions_root=isolated_root,
            session=isolated_root / "session",
            phase="handoff",
        )
        with mock.patch(
            "murmurmark_processing_lease.os.fsync",
            side_effect=side_effect,
        ):
            try:
                lease._write_ticket()
            except OSError:
                pass
            else:
                raise AssertionError(f"{name} fixture did not fail")
        queue = isolated_root / ".murmurmark-processing/queue"
        assert not list(queue.iterdir()), f"{name} left a queue artifact behind"


def check_interrupted_waiter_cleanup(sessions_root: Path, events: Path) -> None:
    owner = start_worker(sessions_root, events, "session-owner", 1.0)
    waiter: subprocess.Popen[str] | None = None
    try:
        wait_until(
            lambda: any(
                row.get("event") == "acquired" and row.get("name") == "session-owner"
                for row in read_events(events)
            ),
            "interrupt-test owner did not acquire the lease",
        )
        waiter = start_worker(sessions_root, events, "session-interrupted", 0.0)
        wait_until(
            lambda: ticket_for(sessions_root, "session-interrupted"),
            "interrupt-test waiter did not enter the queue",
        )
        waiter.send_signal(signal.SIGINT)
        require_exit(waiter, 130, "session-interrupted")
        assert not ticket_for(sessions_root, "session-interrupted"), (
            "interrupted waiter left a queue ticket behind"
        )
        require_exit(owner, 0, "session-owner")
    finally:
        for process in (waiter, owner):
            if process is not None and process.poll() is None:
                process.terminate()
                process.wait(timeout=5)


def check_budget_waiter_cleanup(sessions_root: Path) -> None:
    from murmurmark_deadline import ActionStopped, DEADLINE_ENV

    owner = ProcessingLease(sessions_root=sessions_root, session=sessions_root / "owner", phase="handoff")
    owner.acquire()
    original_owner = owner.lock_path.read_bytes()
    waiter = ProcessingLease(sessions_root=sessions_root, session=sessions_root / "waiter", phase="deferred_enrichment")
    try:
        with mock.patch.dict(os.environ, {DEADLINE_ENV: str(time.time() + 0.15)}):
            try:
                waiter.acquire()
            except ActionStopped as error:
                assert error.returncode == 75
            else:
                raise AssertionError("expired waiter acquired a held lease")
        assert waiter.handle is None
        assert not waiter.ticket_path.exists()
        assert owner.lock_path.read_bytes() == original_owner
    finally:
        owner.release()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--sessions-root", type=Path)
    parser.add_argument("--events", type=Path)
    parser.add_argument("--name")
    parser.add_argument("--hold-sec", type=float, default=0.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.worker:
        assert args.sessions_root is not None
        assert args.events is not None
        assert args.name
        return worker(args)

    with tempfile.TemporaryDirectory(prefix="murmurmark-processing-lease-") as raw_root:
        root = Path(raw_root)
        sessions_root = root / "sessions"
        sessions_root.mkdir()
        events = root / "events.jsonl"
        check_fifo_and_capture_independence(sessions_root, events)
        check_stale_ticket_cleanup(sessions_root, events)
        check_incomplete_ticket_is_never_visible(sessions_root)
        check_failed_ticket_write_is_cleaned(sessions_root)
        check_interrupted_waiter_cleanup(sessions_root, events)
        check_budget_waiter_cleanup(sessions_root)

    print("processing lease checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
