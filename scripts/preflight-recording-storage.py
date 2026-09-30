#!/usr/bin/env python3
"""Reclaim old session storage before capture, without discarding review evidence."""

from __future__ import annotations

import argparse
import importlib.util
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable


GIB = 1024**3
SCRIPT = Path(__file__).with_name("compact-derived-artifacts.py")
SPEC = importlib.util.spec_from_file_location("murmurmark_compaction", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
compaction = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(compaction)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-out", type=Path, required=True)
    parser.add_argument("--sessions-root", type=Path, required=True)
    return parser.parse_args()


def free_space(path: Path, usage: Callable[[Path], Any]) -> tuple[int, int]:
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    result = usage(probe)
    return int(result.total), int(result.free)


def compaction_args(session: Path, mode: str) -> argparse.Namespace:
    return argparse.Namespace(
        action="apply",
        target=str(session),
        mode=mode,
        out=None,
        include_pinned=False,
        confirm_delete_derived_media=True,
        confirm_delete_raw=mode == compaction.TRANSCRIPT_ONLY_MODE,
        require_successful_export=False,
        export_manifest=None,
        allow_active_lifecycle=False,
    )


def raw_archive_ready(session: Path) -> bool:
    outcome = compaction.read_json(session / "derived/outcome/outcome.json") or {}
    readiness = outcome.get("readiness") or {}
    summary = outcome.get("summary") or {}
    return (
        isinstance(readiness, dict)
        and readiness.get("transcript") == "ready"
        and readiness.get("export") == "allowed"
        and isinstance(summary, dict)
        and summary.get("export_blockers") == []
        and outcome.get("verdict") == "good"
    )


def old_sessions(root: Path, capture_out: Path, pins: set[str]) -> list[Path]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=2)
    eligible: list[tuple[datetime, Path]] = []
    for session in root.iterdir():
        if (
            not session.is_dir()
            or session.is_symlink()
            or session.name.startswith("_")
            or session.name in pins
            or session.resolve() == capture_out
            or (session / "audio").is_symlink()
            or (session / "derived").is_symlink()
            or (session / "derived/retention").is_symlink()
        ):
            continue
        payload = compaction.read_json(session / "session.json")
        if payload is None or payload.get("status") not in {"completed", "completed_with_warnings"}:
            continue
        lifecycle = compaction.read_json(session / "derived/meeting-lifecycle/state.json") or {}
        if lifecycle.get("keep_debug_artifacts") is True:
            continue
        if compaction.pipeline_blockers(session, False) or not compaction.lifecycle_lock_available(session):
            continue
        ended_at = compaction.session_ended_at(session, payload)
        if ended_at > cutoff:
            continue
        selected, _ = compaction.selected_paths(session)
        if not compaction.has_selected_transcript(selected):
            continue
        eligible.append((ended_at, session))
    return [session for _, session in sorted(eligible, key=lambda row: (row[0], row[1].name))]


def run_preflight(
    capture_out: Path,
    sessions_root: Path,
    *,
    usage: Callable[[Path], Any] = shutil.disk_usage,
    trigger_bytes: int | None = None,
    target_bytes: int | None = None,
) -> int:
    capture_out = capture_out.expanduser().resolve()
    sessions_root = sessions_root.expanduser().absolute()
    root_is_symlink = sessions_root.is_symlink()
    sessions_root = sessions_root.resolve()
    total, before = free_space(capture_out.parent, usage)
    trigger = trigger_bytes if trigger_bytes is not None else min(50 * GIB, total // 10)
    target = target_bytes if target_bytes is not None else min(100 * GIB, total // 5)
    if before >= trigger:
        return 0

    print(
        f"storage_preflight: {before / GIB:.1f} GiB free (< {trigger / GIB:.1f} GiB); "
        "checking old unpinned sessions",
        flush=True,
    )
    rows: list[dict[str, Any]] = []
    if (
        root_is_symlink
        or not sessions_root.is_dir()
        or capture_out.parent != sessions_root.resolve()
    ):
        print(
            "storage_preflight: automatic cleanup is limited to the canonical sessions directory; "
            "recording refused on this low-space volume",
            file=sys.stderr,
        )
        return 2

    policy_files = sorted((Path(__file__).resolve().parents[1] / "policies").glob("*.json"))
    pins, pin_sources = compaction.discover_pins(sessions_root, policy_files)
    sessions = old_sessions(sessions_root, capture_out, pins)
    free = before
    for mode, emergency in (
        (compaction.KEEP_RAW_MODE, False),
        (compaction.TRANSCRIPT_ONLY_MODE, False),
        (compaction.TRANSCRIPT_ONLY_MODE, True),
    ):
        if emergency:
            if free >= trigger:
                break
            print(
                "storage_preflight: emergency archive of old review-blocked sessions; "
                "raw audio may be deleted",
                file=sys.stderr,
                flush=True,
            )
        stop_at = trigger if emergency else target
        for session in sessions:
            if free >= stop_at:
                break
            if mode == compaction.TRANSCRIPT_ONLY_MODE:
                quality_passed = raw_archive_ready(session)
                if emergency and quality_passed:
                    continue
                if not emergency and not quality_passed:
                    continue
            try:
                manifest, _ = compaction.process_session(
                    session,
                    compaction_args(session, mode),
                    pins=pins,
                    pin_sources=pin_sources,
                )
                application = manifest.get("application") or {}
                deleted = int(application.get("deleted_bytes") or 0)
                if manifest.get("status") in {"applied", "partial"} and (
                    deleted or manifest.get("status") == "partial"
                ):
                    rows.append(
                        {
                            "session_id": session.name,
                            "mode": mode,
                            "emergency": emergency,
                            "status": manifest.get("status"),
                            "deleted_bytes": deleted,
                        }
                    )
                elif manifest.get("status") == "blocked":
                    rows.append(
                        {
                            "session_id": session.name,
                            "mode": mode,
                            "emergency": emergency,
                            "status": "blocked",
                            "blockers": list((manifest.get("eligibility") or {}).get("blockers") or []),
                            "deleted_bytes": 0,
                        }
                    )
            except (OSError, RuntimeError, ValueError, KeyError) as error:
                rows.append(
                    {
                        "session_id": session.name,
                        "mode": mode,
                        "emergency": emergency,
                        "status": "failed",
                        "error": str(error),
                    }
                )
            _, free = free_space(capture_out.parent, usage)
        if free >= stop_at:
            break

    report = {
        "schema": "murmurmark.recording_storage_preflight/v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "capture_out": str(capture_out),
        "free_before_bytes": before,
        "free_after_bytes": free,
        "trigger_bytes": trigger,
        "target_bytes": target,
        "pinned_session_count": len(pins),
        "candidate_session_count": len(sessions),
        "sessions": rows,
    }
    report_path = sessions_root / "_reports/retention-compaction/recording_storage_preflight.json"
    try:
        compaction.atomic_write_json(report_path, report)
    except OSError as error:
        print(f"storage_preflight: could not write report: {error}", file=sys.stderr)
    deleted_bytes = sum(int(row.get("deleted_bytes") or 0) for row in rows)
    print(
        f"storage_preflight: reclaimed {deleted_bytes / GIB:.1f} GiB; "
        f"free now {free / GIB:.1f} GiB; report: {report_path}",
        flush=True,
    )
    if free < trigger:
        print(
            "storage_preflight: not enough space for a reliable recording. "
            "Inspect the report and free space manually; pinned/debug/active sessions were preserved.",
            file=sys.stderr,
        )
        return 2
    if free < target:
        print("storage_preflight: target reserve not reached; recording may need more space", file=sys.stderr)
    return 0


def main() -> int:
    args = parse_args()
    try:
        return run_preflight(args.capture_out, args.sessions_root)
    except OSError as error:
        print(f"storage_preflight: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
