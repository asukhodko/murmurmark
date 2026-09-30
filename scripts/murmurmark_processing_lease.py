#!/usr/bin/env python3
"""Fair, crash-tolerant lease for heavy MurmurMark post-processing."""

from __future__ import annotations

import atexit
import fcntl
import json
import os
import socket
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from murmurmark_deadline import check_deadline


SCHEMA = "murmurmark.processing_lease/v1"
TICKET_SCHEMA = "murmurmark.processing_lease_ticket/v1"
DEFAULT_POLL_INTERVAL_SEC = 0.25
DEFAULT_HEARTBEAT_INTERVAL_SEC = 30.0


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class ProcessingLease:
    """Serialize heavy pipelines while leaving the independent capture lock free."""

    def __init__(
        self,
        *,
        sessions_root: Path,
        session: Path,
        phase: str,
        poll_interval_sec: float = DEFAULT_POLL_INTERVAL_SEC,
        heartbeat_interval_sec: float = DEFAULT_HEARTBEAT_INTERVAL_SEC,
    ) -> None:
        self.sessions_root = sessions_root.expanduser().resolve()
        self.session = session.expanduser().resolve()
        self.phase = phase
        self.poll_interval_sec = max(0.05, float(poll_interval_sec))
        self.heartbeat_interval_sec = max(0.1, float(heartbeat_interval_sec))
        self.control_root = self.sessions_root / ".murmurmark-processing"
        self.queue_root = self.control_root / "queue"
        self.lock_path = self.control_root / "lease.lock"
        self.ticket_path: Path | None = None
        self.handle: Any = None
        self.queued_at: str | None = None
        self.acquired_at: str | None = None
        self.waited_sec = 0.0
        self.released = False
        self.host = socket.gethostname()
        self._atexit_callback: Callable[[], Any] | None = None

    def _write_ticket(self) -> None:
        self.queue_root.mkdir(parents=True, exist_ok=True)
        self.queued_at = now_iso()
        ticket_name = f"{time.time_ns():020d}-{os.getpid():010d}-{uuid.uuid4().hex}.json"
        self.ticket_path = self.queue_root / ticket_name
        temporary_path = self.queue_root / f".{ticket_name}.{uuid.uuid4().hex}.tmp"
        payload = {
            "schema": TICKET_SCHEMA,
            "pid": os.getpid(),
            "host": self.host,
            "session": str(self.session),
            "phase": self.phase,
            "queued_at": self.queued_at,
        }
        published = False
        try:
            descriptor = os.open(
                temporary_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o644,
            )
            try:
                data = (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
                offset = 0
                while offset < len(data):
                    written = os.write(descriptor, data[offset:])
                    if written <= 0:
                        raise OSError("could not write processing queue ticket")
                    offset += written
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.replace(temporary_path, self.ticket_path)
            published = True
            directory = os.open(self.queue_root, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except BaseException:
            temporary_path.unlink(missing_ok=True)
            if published:
                self.ticket_path.unlink(missing_ok=True)
            raise

    def _remove_ticket(self) -> None:
        if self.ticket_path is not None:
            self.ticket_path.unlink(missing_ok=True)

    def _queue(self) -> list[Path]:
        self.queue_root.mkdir(parents=True, exist_ok=True)
        for path in self.queue_root.glob("*.json"):
            payload = read_json(path)
            pid = int(payload.get("pid") or 0) if isinstance(payload, dict) else 0
            host = str(payload.get("host") or "") if isinstance(payload, dict) else ""
            if payload is None or (host == self.host and not pid_is_alive(pid)):
                path.unlink(missing_ok=True)
        return sorted(self.queue_root.glob("*.json"), key=lambda item: item.name)

    def _owner(self) -> dict[str, Any] | None:
        return read_json(self.lock_path)

    def _snapshot(self, started: float, *, status: str) -> dict[str, Any]:
        queue = self._queue()
        try:
            position = queue.index(self.ticket_path) + 1 if self.ticket_path is not None else 0
        except ValueError:
            position = 0
        return {
            "schema": SCHEMA,
            "status": status,
            "session": str(self.session),
            "phase": self.phase,
            "pid": os.getpid(),
            "queued_at": self.queued_at,
            "acquired_at": self.acquired_at,
            "waited_sec": round(max(0.0, time.monotonic() - started), 3),
            "queue_position": position,
            "queued_ahead": max(0, position - 1),
            "owner": self._owner() if status == "waiting" else None,
            "lock": str(self.lock_path),
            "capture_blocked": False,
        }

    def _write_owner(self) -> None:
        assert self.handle is not None
        payload = {
            "schema": SCHEMA,
            "status": "acquired",
            "pid": os.getpid(),
            "host": self.host,
            "session": str(self.session),
            "phase": self.phase,
            "queued_at": self.queued_at,
            "acquired_at": self.acquired_at,
        }
        self.handle.seek(0)
        self.handle.truncate()
        self.handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        self.handle.flush()
        os.fsync(self.handle.fileno())

    def acquire(
        self,
        on_wait: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        check_deadline()
        self.control_root.mkdir(parents=True, exist_ok=True)
        self._write_ticket()
        self.handle = self.lock_path.open("a+", encoding="utf-8")
        started = time.monotonic()
        next_heartbeat = started
        try:
            while True:
                check_deadline()
                queue = self._queue()
                if self.ticket_path is None or self.ticket_path not in queue:
                    raise RuntimeError("processing queue ticket disappeared while waiting")
                if queue[0] == self.ticket_path:
                    try:
                        fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        pass
                    else:
                        self.waited_sec = max(0.0, time.monotonic() - started)
                        self.acquired_at = now_iso()
                        self._remove_ticket()
                        self._write_owner()
                        self._atexit_callback = self.release
                        atexit.register(self._atexit_callback)
                        snapshot = self._snapshot(started, status="acquired")
                        snapshot["waited_sec"] = round(self.waited_sec, 3)
                        return snapshot
                now = time.monotonic()
                if on_wait is not None and now >= next_heartbeat:
                    on_wait(self._snapshot(started, status="waiting"))
                    next_heartbeat = now + self.heartbeat_interval_sec
                time.sleep(self.poll_interval_sec)
        except BaseException:
            self.waited_sec = max(0.0, time.monotonic() - started)
            self._remove_ticket()
            if self.handle is not None:
                self.handle.close()
                self.handle = None
            raise

    def release(self) -> dict[str, Any]:
        if self.released:
            return self.summary("released")
        self.released = True
        if self._atexit_callback is not None:
            atexit.unregister(self._atexit_callback)
            self._atexit_callback = None
        self._remove_ticket()
        if self.handle is not None:
            try:
                self.handle.seek(0)
                self.handle.truncate()
                self.handle.flush()
                os.fsync(self.handle.fileno())
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            finally:
                self.handle.close()
                self.handle = None
        return self.summary("released")

    def summary(self, status: str | None = None) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "status": status or ("acquired" if self.acquired_at else "waiting"),
            "session": str(self.session),
            "phase": self.phase,
            "pid": os.getpid(),
            "queued_at": self.queued_at,
            "acquired_at": self.acquired_at,
            "waited_sec": round(self.waited_sec, 3),
            "lock": str(self.lock_path),
            "capture_blocked": False,
        }

    def __del__(self) -> None:
        try:
            self.release()
        except Exception:
            pass
