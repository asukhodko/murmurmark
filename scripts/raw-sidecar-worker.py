#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


EXPERIMENT_STATE_SCHEMA = "murmurmark.experimental_sidecar_state/v1"
EXPERIMENT_REPORT_SCHEMA = "murmurmark.experimental_sidecar_report/v1"
EXPERIMENT_EVENT_SCHEMA = "murmurmark.experimental_sidecar_event/v1"
RAW_COMMIT_SCHEMA = "murmurmark.raw_segment_commit/v1"
LIVE_SEGMENT_SCHEMA = "murmurmark.live_segment/v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Best-effort raw-commit sidecar worker.")
    parser.add_argument("session", type=Path)
    parser.add_argument("--experiment", default="live-shadow-v1")
    parser.add_argument("--poll-sec", type=float, default=1.0)
    parser.add_argument("--idle-after-session-json-sec", type=float, default=8.0)
    parser.add_argument("--max-ready-backlog", type=int, default=int(os.environ.get("MURMURMARK_RAW_SIDECAR_MAX_READY_BACKLOG", "5")))
    parser.add_argument("--ffmpeg-timeout-sec", type=float, default=float(os.environ.get("MURMURMARK_RAW_SIDECAR_FFMPEG_TIMEOUT_SEC", "4")))
    parser.add_argument(
        "--allow-open-raw-read",
        action="store_true",
        default=os.environ.get("MURMURMARK_RAW_SIDECAR_ALLOW_OPEN_RAW_READ") == "1",
        help="Allow reading raw CAF while recording is still open. Default is off because ffmpeg can hang on open CAF.",
    )
    parser.add_argument("--no-live-worker", action="store_true")
    parser.add_argument("--live-worker-timeout-sec", type=float, default=45.0)
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as file:
            for line in file:
                line = line.strip()
                if not line:
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    rows.append(value)
    except OSError:
        return []
    return rows


def session_is_closed(session: Path) -> bool:
    session_json = session / "session.json"
    try:
        payload = json.loads(session_json.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return False
    status = str(payload.get("status") or "")
    if status in {"completed", "completed_with_warnings", "failed"}:
        return True
    return bool(payload.get("ended_at"))


def rewrite_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    tmp.replace(path)


def rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def run(command: list[str], timeout_sec: float) -> tuple[int, str]:
    try:
        result = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=max(0.5, timeout_sec),
        )
        return result.returncode, result.stderr
    except subprocess.TimeoutExpired as error:
        stderr = error.stderr or ""
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        return 124, f"timed out after {timeout_sec:.1f}s {stderr}".strip()


def source_audio_path(session: Path, row: dict[str, Any]) -> Path:
    raw_path = str(row.get("raw_path") or "")
    return session / raw_path


def output_audio_path(session: Path, experiment: str, row: dict[str, Any]) -> Path:
    source = str(row.get("source") or "unknown")
    index = int(row.get("index") or 0)
    return session / "derived" / "experiments" / experiment / "audio" / source / f"{index:06d}.wav"


def ffmpeg_materialize(session: Path, experiment: str, row: dict[str, Any], timeout_sec: float) -> tuple[bool, str, Path]:
    source = source_audio_path(session, row)
    output = output_audio_path(session, experiment, row)
    output.parent.mkdir(parents=True, exist_ok=True)
    start = float(row.get("start_sec") or 0.0)
    duration = max(0.0, float(row.get("end_sec") or 0.0) - start)
    sample_rate = int(row.get("sample_rate") or 48000)
    if not source.exists():
        return False, "raw_missing", output
    if duration <= 0.0:
        return False, "empty_interval", output
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{start:.3f}",
        "-t",
        f"{duration:.3f}",
        "-i",
        str(source),
        "-ar",
        str(sample_rate),
        "-ac",
        "1",
        str(output),
    ]
    returncode, stderr_text = run(command, timeout_sec)
    if returncode == 0 and output.exists() and output.stat().st_size > 44:
        return True, "materialized", output
    if output.exists():
        try:
            output.unlink()
        except OSError:
            pass
    reason = "raw_not_readable_timeout" if returncode == 124 else "ffmpeg_failed"
    stderr = " ".join(stderr_text.split())
    if stderr:
        reason = f"{reason}: {stderr[:240]}"
    return False, reason, output


def grouped_commits(rows: list[dict[str, Any]]) -> dict[int, dict[str, dict[str, Any]]]:
    grouped: dict[int, dict[str, dict[str, Any]]] = {}
    for row in rows:
        if row.get("schema") != RAW_COMMIT_SCHEMA:
            continue
        if row.get("status") != "committed":
            continue
        source = str(row.get("source") or "")
        if source not in {"mic", "remote"}:
            continue
        try:
            index = int(row.get("index"))
        except (TypeError, ValueError):
            continue
        grouped.setdefault(index, {})[source] = row
    return grouped


def final_commit_indexes(rows: list[dict[str, Any]]) -> dict[str, int]:
    finals: dict[str, int] = {}
    for row in rows:
        if row.get("schema") != RAW_COMMIT_SCHEMA:
            continue
        if row.get("status") != "committed" or not row.get("final"):
            continue
        source = str(row.get("source") or "")
        if source not in {"mic", "remote"}:
            continue
        try:
            index = int(row.get("index"))
        except (TypeError, ValueError):
            continue
        finals[source] = max(finals.get(source, 0), index)
    return finals


def all_final_pairs_processed(rows: list[dict[str, Any]], processed: set[int]) -> bool:
    finals = final_commit_indexes(rows)
    if "mic" not in finals or "remote" not in finals:
        return False
    last_index = max(finals["mic"], finals["remote"])
    return all(index in processed for index in range(1, last_index + 1))


def live_segment_row(session: Path, experiment: str, row: dict[str, Any], output: Path) -> dict[str, Any]:
    start = float(row.get("start_sec") or 0.0)
    end = float(row.get("end_sec") or start)
    frames = int(row.get("frames_committed") or 0)
    return {
        "schema": LIVE_SEGMENT_SCHEMA,
        "source": str(row.get("source") or ""),
        "index": int(row.get("index") or 0),
        "path": rel(output, session),
        "start_sec": round(start, 3),
        "end_sec": round(end, 3),
        "duration_sec": round(end - start, 3),
        "clip_start_sec": round(start, 3),
        "clip_end_sec": round(end, 3),
        "clip_duration_sec": round(end - start, 3),
        "overlap_before_sec": 0.0,
        "overlap_after_sec": 0.0,
        "frames": frames,
        "clip_frames": frames,
        "sample_rate": int(row.get("sample_rate") or 48000),
        "closed": True,
        "final": bool(row.get("final")),
        "after_overlap_complete": True,
        "materialized_from_raw_commit": True,
        "raw_path": str(row.get("raw_path") or ""),
    }


def start_live_worker(session: Path, log) -> subprocess.Popen[str] | None:
    script = Path("scripts/live-pipeline-shadow.py")
    if not script.exists():
        return None
    python = os.environ.get("MURMURMARK_PYTHON") or str(Path(".venv/bin/python"))
    if not Path(python).exists():
        python = shutil.which("python3") or "/usr/bin/python3"
    return subprocess.Popen(
        [python, str(script), str(session)],
        stdout=log,
        stderr=log,
        text=True,
    )


def write_state(
    session: Path,
    experiment: str,
    status: str,
    processed: set[int],
    materialized_rows: list[dict[str, Any]],
    reason: str | None = None,
) -> None:
    experiment_dir = session / "derived" / "experiments" / experiment
    raw_seconds = 0.0
    session_json = session / "session.json"
    if session_json.exists():
        try:
            payload = json.loads(session_json.read_text(encoding="utf-8"))
            raw_seconds = float(((payload.get("health") or {}).get("actual_duration_sec")) or 0.0)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            raw_seconds = 0.0
    sidecar_seconds = max((float(row.get("end_sec") or 0.0) for row in materialized_rows), default=0.0)
    state = {
        "schema": EXPERIMENT_STATE_SCHEMA,
        "experiment_id": experiment,
        "kind": "near_realtime_shadow",
        "status": status,
        "updated_at": utc_now(),
        "reason": reason,
        "answers": {
            "experiment_started": True,
            "raw_seconds_recorded": round(raw_seconds, 3),
            "sidecar_seconds_captured": round(sidecar_seconds, 3),
            "sidecar_seconds_preprocessed": 0.0,
            "sidecar_seconds_asr": 0.0,
            "dropped_chunks": 0,
            "backpressure_detected": status == "disabled_backpressure",
            "sidecar_disabled": status.startswith("disabled") or status == "failed",
            "raw_capture_affected": False if session_json.exists() else "unknown",
            "batch_reproducible_from_raw": session_json.exists(),
        },
        "counters": {
            "processed_indexes": sorted(processed),
            "segments_seen": len(materialized_rows),
        },
        "inputs": {
            "raw_segment_commits": f"derived/experiments/{experiment}/raw_segment_commits.jsonl",
            "raw_mic": "audio/mic/000001.caf",
            "raw_remote": "audio/remote/000001.caf",
        },
        "outputs": {
            "raw_segment_commits": f"derived/experiments/{experiment}/raw_segment_commits.jsonl",
            "segments": "derived/live/segments.jsonl",
            "compat_live_dir": "derived/live",
            "experiment_audio": f"derived/experiments/{experiment}/audio",
        },
    }
    write_json(experiment_dir / "state.json", state)
    report = {
        "schema": EXPERIMENT_REPORT_SCHEMA,
        "experiment_id": experiment,
        "generated_at": utc_now(),
        "session": str(session),
        "status": status,
        "raw_capture_affected": False if session_json.exists() else "unknown",
        "batch_authoritative": True,
        "promotion_allowed": False,
        "summary": {
            "materialized_indexes": sorted(processed),
            "materialized_segment_rows": len(materialized_rows),
            "reason": reason,
        },
    }
    write_json(experiment_dir / "report.json", report)


def append_event(session: Path, experiment: str, event_type: str, status: str, **fields: Any) -> None:
    append_jsonl(
        session / "derived" / "experiments" / experiment / "events.jsonl",
        {
            "schema": EXPERIMENT_EVENT_SCHEMA,
            "t": utc_now(),
            "type": event_type,
            "status": status,
            "raw_capture_affected": False,
            "batch_authoritative": True,
            "promotion_allowed": False,
            **fields,
        },
    )


def main() -> int:
    args = parse_args()
    session = args.session.expanduser().resolve()
    experiment = args.experiment
    experiment_dir = session / "derived" / "experiments" / experiment
    commit_log = experiment_dir / "raw_segment_commits.jsonl"
    live_segments_path = session / "derived" / "live" / "segments.jsonl"
    live_segments_path.parent.mkdir(parents=True, exist_ok=True)
    if not live_segments_path.exists():
        live_segments_path.write_text("", encoding="utf-8")

    log_path = experiment_dir / "worker.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log:
        child = None
        status = "running"
        reason = None
        processed: set[int] = set()
        materialized_rows: list[dict[str, Any]] = []
        last_progress = time.monotonic()
        waiting_notified = False
        append_event(session, experiment, "raw_sidecar_worker.started", status)
        write_state(session, experiment, status, processed, materialized_rows)

        try:
            while True:
                commits = read_jsonl(commit_log)
                grouped = grouped_commits(commits)
                ready = [index for index, pair in grouped.items() if {"mic", "remote"} <= set(pair)]
                ready_unprocessed = sorted(index for index in ready if index not in processed)
                session_closed = session_is_closed(session)
                if ready_unprocessed and not session_closed and not args.allow_open_raw_read:
                    status = "waiting_for_raw_readable"
                    reason = "open_raw_read_disabled_until_session_close"
                    if not waiting_notified or time.monotonic() - last_progress >= args.idle_after_session_json_sec:
                        append_event(session, experiment, "raw_sidecar_worker.waiting_for_session_close", status, reason=reason)
                        waiting_notified = True
                        last_progress = time.monotonic()
                    write_state(session, experiment, status, processed, materialized_rows, reason=reason)
                    time.sleep(max(0.1, args.poll_sec))
                    continue
                if not session_closed and len(ready_unprocessed) > max(1, args.max_ready_backlog):
                    status = "disabled_backpressure"
                    reason = "ready commit backlog exceeded"
                    append_event(session, experiment, "raw_sidecar_worker.disabled", status, reason=reason)
                    break
                for index in ready_unprocessed:
                    pair = grouped[index]
                    rows_for_index: list[dict[str, Any]] = []
                    failed_reason = None
                    for source in ("mic", "remote"):
                        ok, mat_reason, output = ffmpeg_materialize(session, experiment, pair[source], args.ffmpeg_timeout_sec)
                        if not ok:
                            failed_reason = mat_reason
                            break
                        rows_for_index.append(live_segment_row(session, experiment, pair[source], output))
                    if failed_reason:
                        status = "waiting_for_raw_readable"
                        reason = failed_reason
                        break
                    materialized_rows.extend(rows_for_index)
                    rewrite_jsonl(live_segments_path, sorted(materialized_rows, key=lambda row: (int(row.get("index") or 0), str(row.get("source") or ""))))
                    processed.add(index)
                    if child is None and not args.no_live_worker:
                        child = start_live_worker(session, log)
                        if child is not None:
                            append_event(session, experiment, "live_worker.started", status, index=index)
                    status = "running"
                    reason = None
                    waiting_notified = False
                    last_progress = time.monotonic()
                    append_event(session, experiment, "raw_sidecar_worker.materialized", status, index=index)
                write_state(session, experiment, status, processed, materialized_rows, reason=reason)

                if (session / "session.json").exists():
                    if all_final_pairs_processed(commits, processed):
                        status = "completed" if status != "disabled_backpressure" else status
                        reason = None if status == "completed" else reason
                        break
                    if status == "waiting_for_raw_readable" and time.monotonic() - last_progress >= args.idle_after_session_json_sec:
                        append_event(session, experiment, "raw_sidecar_worker.waiting_retry", status, reason=reason)
                time.sleep(max(0.1, args.poll_sec))
        finally:
            if child is not None:
                deadline = time.monotonic() + max(0.0, args.live_worker_timeout_sec)
                while child.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.25)
                if child.poll() is None:
                    child.terminate()
                    append_event(session, experiment, "live_worker.terminated", "terminated", reason="sidecar_worker_timeout")
            write_state(session, experiment, status, processed, materialized_rows, reason=reason)
            append_event(session, experiment, "raw_sidecar_worker.finished", status, reason=reason)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
