#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import numpy as np
    import soundfile as sf
except ModuleNotFoundError as error:
    np = None  # type: ignore[assignment]
    sf = None  # type: ignore[assignment]
    AUDIO_DEPENDENCY_ERROR: ModuleNotFoundError | None = error
else:
    AUDIO_DEPENDENCY_ERROR = None


SCHEMA = "murmurmark.capture_continuity/v1"
SCRIPT_VERSION = "0.2.0"
DEFAULT_OUT = Path("derived/audit/capture-continuity")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Measure PCM gaps around ScreenCaptureKit restarts.")
    parser.add_argument("session", type=Path)
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument("--search-before-sec", type=float, default=2.5)
    parser.add_argument("--search-after-sec", type=float, default=1.5)
    parser.add_argument("--zero-threshold", type=float, default=1e-12)
    parser.add_argument("--min-gap-sec", type=float, default=0.05)
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def track_paths(session: Path, manifest: dict[str, Any]) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    files = manifest.get("files") if isinstance(manifest.get("files"), dict) else {}
    for source in ("mic", "remote"):
        entries = files.get(source) if isinstance(files.get(source), list) else []
        first = entries[0] if entries and isinstance(entries[0], dict) else {}
        value = first.get("path")
        path = session / str(value) if value else session / f"audio/{source}/000001.caf"
        if path.exists():
            paths[source] = path
    return paths


def restart_events(session: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    created_at = parse_time(manifest.get("created_at"))
    rows: list[dict[str, Any]] = []
    for event in read_jsonl(session / "events.jsonl"):
        if event.get("type") != "capture.restarted":
            continue
        timestamp = parse_time(event.get("t"))
        offset = (timestamp - created_at).total_seconds() if timestamp and created_at else None
        rows.append(
            {
                "restart_count": int(event.get("restart_count") or len(rows) + 1),
                "attempt_id": safe_int(event.get("attempt_id"), 0) or None,
                "reason": str(event.get("reason") or "unknown"),
                "timestamp": event.get("t"),
                "offset_sec": round(max(0.0, offset), 6) if offset is not None else None,
            }
        )
    return rows


def restart_provenance(session: Path) -> tuple[str, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for event in read_jsonl(session / "events.jsonl"):
        if event.get("type") != "capture.restart_provenance":
            continue
        attempt_id = safe_int(event.get("attempt_id"), 0)
        if attempt_id <= 0:
            continue
        grouped.setdefault(attempt_id, []).append(event)

    if not grouped:
        return "legacy_unavailable", []

    attempts: list[dict[str, Any]] = []
    for attempt_id, rows in sorted(grouped.items()):
        monotonic_values = [safe_int(row.get("monotonic_ns"), -1) for row in rows]
        monotonic = all(
            left >= 0 and right > left
            for left, right in zip(monotonic_values, monotonic_values[1:])
        )
        indexed: dict[tuple[str, str | None], list[dict[str, Any]]] = {}
        for row in rows:
            phase = str(row.get("phase") or "")
            source = str(row.get("source")) if row.get("source") is not None else None
            indexed.setdefault((phase, source), []).append(row)

        def first(phase: str, source: str | None = None) -> dict[str, Any] | None:
            values = indexed.get((phase, source)) or []
            return values[0] if values else None

        def elapsed_ms(start: dict[str, Any] | None, end: dict[str, Any] | None) -> float | None:
            if start is None or end is None:
                return None
            start_ns = safe_int(start.get("monotonic_ns"), -1)
            end_ns = safe_int(end.get("monotonic_ns"), -1)
            if start_ns < 0 or end_ns < start_ns:
                return None
            return round((end_ns - start_ns) / 1_000_000.0, 3)

        requested = first("requested")
        expected_sources = [
            str(value)
            for value in ((requested or {}).get("expected_sources") or ["mic", "remote"])
            if str(value) in {"mic", "remote"}
        ]
        if not expected_sources:
            expected_sources = ["mic", "remote"]
        old_stop_anchor = (
            first("old_stream_already_stopped")
            or first("old_stream_stop_completed")
            or first("old_stream_stop_failed")
            or requested
        )
        start_requested = first("start_requested")
        start_completed = first("start_completed")
        terminal_rows = indexed.get(("terminal", None)) or []
        terminal = terminal_rows[0] if terminal_rows else None
        source_evidence: dict[str, Any] = {}
        for source in ("mic", "remote"):
            callback = first("first_callback", source)
            committed = first("first_committed_pcm", source)
            source_evidence[source] = {
                "first_callback_ms_from_request": elapsed_ms(requested, callback),
                "first_committed_pcm_ms_from_request": elapsed_ms(requested, committed),
                "delivery_after_start_ms": elapsed_ms(start_completed, callback),
                "callback_to_commit_ms": elapsed_ms(callback, committed),
                "gap_frames": safe_int((committed or {}).get("gap_frames"), 0),
                "captured_audio": (committed or {}).get("captured_audio") is True,
            }

        terminal_status = str((terminal or {}).get("terminal_status") or "") or None
        requires_pcm = terminal_status == "started"
        complete = all(
            (
                requested is not None,
                start_requested is not None,
                start_completed is not None,
                len(terminal_rows) == 1,
                monotonic,
                not requires_pcm
                or all(first("first_callback", source) is not None for source in expected_sources),
                not requires_pcm
                or all(first("first_committed_pcm", source) is not None for source in expected_sources),
            )
        )
        first_committed = [
            row
            for row in (
                first("first_committed_pcm", "mic"),
                first("first_committed_pcm", "remote"),
            )
            if row is not None
        ]
        last_committed = max(
            first_committed,
            key=lambda row: safe_int(row.get("monotonic_ns"), -1),
            default=None,
        )
        attempts.append(
            {
                "attempt_id": attempt_id,
                "reason": str((requested or rows[0]).get("reason") or "unknown"),
                "session_offset_sec": (
                    round(safe_float(requested.get("session_offset_sec")), 6)
                    if requested and requested.get("session_offset_sec") is not None
                    else None
                ),
                "terminal_status": terminal_status,
                "provenance_complete": complete,
                "strictly_monotonic": monotonic,
                "terminal_event_count": len(terminal_rows),
                "event_count": len(rows),
                "expected_sources": expected_sources,
                "software_idle_ms": elapsed_ms(old_stop_anchor, start_requested),
                "start_api_ms": elapsed_ms(start_requested, start_completed),
                "request_to_first_committed_pcm_ms": elapsed_ms(
                    requested,
                    min(
                        first_committed,
                        key=lambda row: safe_int(row.get("monotonic_ns"), -1),
                        default=None,
                    ),
                ),
                "request_to_all_sources_committed_ms": elapsed_ms(requested, last_committed),
                "sources": source_evidence,
                "events": [
                    {
                        "sequence": safe_int(row.get("sequence"), 0) or None,
                        "phase": row.get("phase"),
                        "source": row.get("source"),
                        "terminal_status": row.get("terminal_status"),
                        "monotonic_ns": safe_int(row.get("monotonic_ns"), 0),
                        "elapsed_from_request_ms": row.get("elapsed_from_request_ms"),
                    }
                    for row in rows
                ],
            }
        )

    status = "complete" if all(row["provenance_complete"] for row in attempts) else "incomplete"
    return status, attempts


def optional_max(values: list[Any]) -> float | None:
    numbers = [safe_float(value) for value in values if value is not None]
    return round(max(numbers), 3) if numbers else None


def zero_runs(mask: np.ndarray, base_frame: int, sample_rate: float, min_frames: int) -> list[dict[str, Any]]:
    if mask.size == 0:
        return []
    padded = np.concatenate((np.array([False]), mask, np.array([False])))
    transitions = np.flatnonzero(padded[1:] != padded[:-1])
    runs: list[dict[str, Any]] = []
    for start_index, end_index in zip(transitions[0::2], transitions[1::2]):
        frames = int(end_index - start_index)
        if frames < min_frames:
            continue
        start_frame = base_frame + int(start_index)
        end_frame = base_frame + int(end_index)
        runs.append(
            {
                "start_sec": start_frame / sample_rate,
                "end_sec": end_frame / sample_rate,
                "duration_sec": frames / sample_rate,
                "frames": frames,
            }
        )
    return runs


def nearest_zero_run(
    path: Path,
    offset_sec: float,
    *,
    search_before_sec: float,
    search_after_sec: float,
    zero_threshold: float,
    min_gap_sec: float,
) -> dict[str, Any] | None:
    try:
        with sf.SoundFile(path) as audio:
            sample_rate = float(audio.samplerate)
            start_frame = max(0, int((offset_sec - search_before_sec) * sample_rate))
            end_frame = min(len(audio), int(math.ceil((offset_sec + search_after_sec) * sample_rate)))
            audio.seek(start_frame)
            data = audio.read(end_frame - start_frame, dtype="float32", always_2d=True)
    except (OSError, RuntimeError, sf.LibsndfileError):
        return None
    silent = np.all(np.abs(data) <= zero_threshold, axis=1)
    runs = zero_runs(silent, start_frame, sample_rate, max(1, int(min_gap_sec * sample_rate)))
    if not runs:
        return None

    def distance(run: dict[str, Any]) -> float:
        start = safe_float(run.get("start_sec"))
        end = safe_float(run.get("end_sec"))
        if start <= offset_sec <= end:
            return 0.0
        return min(abs(offset_sec - start), abs(offset_sec - end))

    candidate = min(runs, key=lambda run: (distance(run), -safe_float(run.get("duration_sec"))))
    if distance(candidate) > max(search_before_sec, search_after_sec):
        return None
    return {
        **candidate,
        "sample_rate": int(round(sample_rate)),
        "distance_to_restart_sec": round(distance(candidate), 6),
    }


def manifest_gap_rows(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    health = manifest.get("health") if isinstance(manifest.get("health"), dict) else {}
    rows = health.get("capture_gaps") if isinstance(health.get("capture_gaps"), list) else []
    normalized: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        normalized.append(
            {
                **row,
                "start_sec": round(safe_float(row.get("start_sec")), 6),
                "end_sec": round(safe_float(row.get("end_sec")), 6),
                "duration_sec": round(safe_float(row.get("duration_sec")), 6),
                "sources": sorted(str(value) for value in (row.get("sources") or [])),
                "evidence": str(row.get("evidence") or "session_manifest"),
                "captured_audio": row.get("captured_audio") is True,
                "confidence": "high",
            }
        )
    return normalized


def correlate_gaps_with_restarts(
    gaps: list[dict[str, Any]],
    attempts: list[dict[str, Any]],
    legacy_events: list[dict[str, Any]],
) -> None:
    candidates = [
        {
            "attempt_id": row.get("attempt_id"),
            "restart_count": None,
            "reason": row.get("reason"),
            "offset_sec": row.get("session_offset_sec"),
        }
        for row in attempts
        if row.get("session_offset_sec") is not None
    ]
    if not candidates:
        candidates.extend(
            {
                "attempt_id": row.get("attempt_id"),
                "restart_count": row.get("restart_count"),
                "reason": row.get("reason"),
                "offset_sec": row.get("offset_sec"),
            }
            for row in legacy_events
            if row.get("offset_sec") is not None
        )
    for gap in gaps:
        start = safe_float(gap.get("start_sec"))
        end = safe_float(gap.get("end_sec"))
        if not candidates:
            continue
        nearest = min(
            candidates,
            key=lambda row: min(
                abs(safe_float(row.get("offset_sec")) - start),
                abs(safe_float(row.get("offset_sec")) - end),
            ),
        )
        offset = safe_float(nearest.get("offset_sec"))
        distance = 0.0 if start <= offset <= end else min(abs(offset - start), abs(offset - end))
        if distance > 5.0:
            continue
        gap.setdefault("attempt_id", nearest.get("attempt_id"))
        gap.setdefault("restart_count", nearest.get("restart_count"))
        gap.setdefault("reason", nearest.get("reason"))
        gap["restart_request_offset_sec"] = round(offset, 6)
        gap["restart_distance_sec"] = round(distance, 6)
        gap["loss_before_restart_request_sec"] = round(max(0.0, min(end, offset) - start), 6)
        gap["loss_after_restart_request_sec"] = round(max(0.0, end - max(start, offset)), 6)


def analyze(args: argparse.Namespace) -> dict[str, Any]:
    session = args.session.expanduser().resolve()
    manifest = read_json(session / "session.json") or {}
    duration = safe_float((manifest.get("health") or {}).get("actual_duration_sec"), 0.0)
    events = restart_events(session, manifest)
    provenance_status, provenance_attempts = restart_provenance(session)
    tracks = track_paths(session, manifest)
    native_gaps = manifest_gap_rows(manifest)
    gaps: list[dict[str, Any]] = []
    source = "session_manifest"
    if native_gaps:
        gaps = native_gaps
    else:
        source = "restart_bounded_pcm_scan"
        for event in events:
            offset = event.get("offset_sec")
            if offset is None:
                continue
            evidence: dict[str, Any] = {}
            for track, path in tracks.items():
                candidate = nearest_zero_run(
                    path,
                    float(offset),
                    search_before_sec=max(0.1, args.search_before_sec),
                    search_after_sec=max(0.1, args.search_after_sec),
                    zero_threshold=max(0.0, args.zero_threshold),
                    min_gap_sec=max(0.01, args.min_gap_sec),
                )
                if candidate is not None:
                    evidence[track] = candidate
            canonical = evidence.get("mic") or evidence.get("remote")
            if canonical is None:
                continue
            gaps.append(
                {
                    "restart_count": event.get("restart_count"),
                    "reason": event.get("reason"),
                    "restart_offset_sec": offset,
                    "start_sec": round(safe_float(canonical.get("start_sec")), 6),
                    "end_sec": round(safe_float(canonical.get("end_sec")), 6),
                    "duration_sec": round(safe_float(canonical.get("duration_sec")), 6),
                    "sources": sorted(evidence),
                    "confidence": "high" if "mic" in evidence else "medium",
                    "evidence": "restart_bounded_exact_zero_pcm_scan",
                    "captured_audio": False,
                    "track_evidence": evidence,
                }
            )

    correlate_gaps_with_restarts(gaps, provenance_attempts, events)

    total = sum(safe_float(row.get("duration_sec")) for row in gaps)
    maximum = max((safe_float(row.get("duration_sec")) for row in gaps), default=0.0)
    ratio = total / duration if duration > 0 else 0.0
    partial_recommended = maximum >= 2.0 or total >= 5.0 or ratio >= 0.005
    successful_restart_count = max(
        len(events),
        safe_int((manifest.get("health") or {}).get("screen_capture_restart_count"), 0),
    )
    health = manifest.get("health") if isinstance(manifest.get("health"), dict) else {}
    native_capture_complete = health.get("capture_complete")
    capture_complete = bool(
        not gaps
        and native_capture_complete is not False
        and provenance_status != "incomplete"
    )
    if partial_recommended:
        status = "partial_recommended"
    elif gaps:
        status = "capture_incomplete"
    elif native_capture_complete is False:
        status = "capture_incomplete_unbounded"
    elif provenance_status == "incomplete":
        status = "provenance_incomplete"
    elif events or provenance_attempts:
        status = "restart_without_detectable_pcm_gap"
    else:
        status = "ok"
    return {
        "schema": SCHEMA,
        "generator": {"name": "audit-capture-continuity", "version": SCRIPT_VERSION},
        "session": str(session),
        "status": status,
        "source": source,
        "capture_duration_sec": round(duration, 3),
        "capture_complete": capture_complete,
        "terminal_completeness_gate": "pass" if capture_complete else "review",
        "screen_capture_restart_count": successful_restart_count,
        "restart_attempt_count": len(provenance_attempts) if provenance_attempts else len(events),
        "observed_gap_count": len(gaps),
        "observed_gap_seconds": round(total, 6),
        "max_observed_gap_seconds": round(maximum, 6),
        "observed_gap_ratio": round(ratio, 9),
        "partial_recommended": partial_recommended,
        "restart_provenance_status": (
            "not_applicable"
            if successful_restart_count == 0 and not provenance_attempts
            else provenance_status
        ),
        "restart_provenance": provenance_attempts,
        "restart_latency": {
            "max_software_idle_ms": optional_max(
                [row.get("software_idle_ms") for row in provenance_attempts]
            ),
            "max_start_api_ms": optional_max(
                [row.get("start_api_ms") for row in provenance_attempts]
            ),
            "max_request_to_all_sources_committed_ms": optional_max(
                [row.get("request_to_all_sources_committed_ms") for row in provenance_attempts]
            ),
            "software_delay_removed": all(
                row.get("software_idle_ms") is not None
                and safe_float(row.get("software_idle_ms")) <= 50.0
                for row in provenance_attempts
            ) if provenance_attempts else None,
        },
        "thresholds": {
            "partial_max_gap_sec": 2.0,
            "partial_total_gap_sec": 5.0,
            "partial_gap_ratio": 0.005,
            "zero_threshold": args.zero_threshold,
            "minimum_detected_gap_sec": args.min_gap_sec,
        },
        "restart_events": events,
        "gaps": gaps,
        "tracks": {key: str(value) for key, value in tracks.items()},
        "batch_authoritative": True,
    }


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Capture Continuity",
        "",
        f"- Status: `{report['status']}`",
        f"- Capture complete: `{str(report['capture_complete']).lower()}`",
        f"- Terminal completeness gate: `{report['terminal_completeness_gate']}`",
        f"- ScreenCaptureKit restarts: `{report['screen_capture_restart_count']}`",
        f"- Observed PCM gaps: `{report['observed_gap_count']}` / `{report['observed_gap_seconds']:.3f}s`",
        f"- Largest gap: `{report['max_observed_gap_seconds']:.3f}s`",
        f"- Partial recommended: `{str(report['partial_recommended']).lower()}`",
        f"- Restart provenance: `{report['restart_provenance_status']}`",
        "",
        "## Gaps",
        "",
    ]
    if not report.get("gaps"):
        lines.append("No restart-correlated PCM gaps were detected.")
    for row in report.get("gaps") or []:
        lines.append(
            f"- restart `{row.get('restart_count')}`: "
            f"`{safe_float(row.get('start_sec')):.3f}..{safe_float(row.get('end_sec')):.3f}` "
            f"(`{safe_float(row.get('duration_sec')):.3f}s`, {', '.join(row.get('sources') or [])})"
        )
    attempts = report.get("restart_provenance") or []
    if attempts:
        lines.extend(["", "## Restart Latency", ""])
        for row in attempts:
            lines.append(
                f"- attempt `{row.get('attempt_id')}` / `{row.get('reason')}`: "
                f"software idle `{safe_float(row.get('software_idle_ms')):.3f}ms`, "
                f"start API `{safe_float(row.get('start_api_ms')):.3f}ms`, "
                f"all sources committed `{safe_float(row.get('request_to_all_sources_committed_ms')):.3f}ms`, "
                f"provenance complete `{str(row.get('provenance_complete') is True).lower()}`"
            )
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    if AUDIO_DEPENDENCY_ERROR is not None:
        print(
            f"capture continuity audit skipped: missing Python dependency: {AUDIO_DEPENDENCY_ERROR.name}",
            file=sys.stderr,
        )
        return 78
    session = args.session.expanduser().resolve()
    out_dir = args.out_dir.expanduser() if args.out_dir else session / DEFAULT_OUT
    report = analyze(args)
    write_json(out_dir / "capture_continuity_report.json", report)
    write_markdown(out_dir / "capture_continuity_report.md", report)
    print(f"capture_continuity: {report['status']}")
    print(f"capture_complete: {str(report['capture_complete']).lower()}")
    print(f"restarts: {report['screen_capture_restart_count']}")
    print(f"gaps: {report['observed_gap_count']} / {report['observed_gap_seconds']:.3f}s")
    print(f"report: {out_dir / 'capture_continuity_report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
