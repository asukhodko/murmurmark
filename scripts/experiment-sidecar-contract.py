#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MANIFEST_SCHEMA = "murmurmark.experimental_sidecar_manifest/v1"
STATE_SCHEMA = "murmurmark.experimental_sidecar_state/v1"
REPORT_SCHEMA = "murmurmark.experimental_sidecar_report/v1"
EVENT_SCHEMA = "murmurmark.experimental_sidecar_event/v1"
DEFAULT_EXPERIMENT_ID = "live-shadow-v1"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def read_jsonl(path: Path) -> list[dict[str, Any]]:
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
    except FileNotFoundError:
        pass
    return rows


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def rel(path: Path, session: Path) -> str | None:
    try:
        if not path.exists():
            return None
        return str(path.relative_to(session))
    except ValueError:
        return str(path)


def display_session(session: Path) -> str:
    try:
        cwd = Path.cwd().resolve()
        return str(session.resolve().relative_to(cwd))
    except ValueError:
        return str(session)


def latest_session(sessions_root: Path) -> Path:
    candidates = [
        path
        for path in sessions_root.iterdir()
        if path.is_dir() and not path.name.startswith("_") and (path / "session.json").exists()
    ]
    if not candidates:
        raise SystemExit(f"no sessions with session.json found under {sessions_root}")
    candidates.sort(key=lambda path: (path.stat().st_mtime, path.name), reverse=True)
    return candidates[0]


def resolve_session(value: str, sessions_root: Path) -> Path:
    if value == "latest":
        return latest_session(sessions_root)
    direct = Path(value).expanduser()
    if (direct / "session.json").exists():
        return direct
    rooted = sessions_root / value
    if (rooted / "session.json").exists():
        return rooted
    raise SystemExit(f"session.json not found for {value}")


def track_duration(session_manifest: dict[str, Any], source: str) -> float:
    health = session_manifest.get("health") if isinstance(session_manifest.get("health"), dict) else {}
    tracks = health.get("tracks") if isinstance(health.get("tracks"), dict) else {}
    row = tracks.get(source) if isinstance(tracks.get(source), dict) else {}
    value = row.get("duration_sec")
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def session_duration(session_manifest: dict[str, Any]) -> float:
    health = session_manifest.get("health") if isinstance(session_manifest.get("health"), dict) else {}
    for key in ("actual_duration_sec", "duration_sec"):
        try:
            return float(health.get(key))
        except (TypeError, ValueError):
            pass
    return max(track_duration(session_manifest, "mic"), track_duration(session_manifest, "remote"))


def sum_by_source(rows: list[dict[str, Any]]) -> dict[str, float]:
    result: dict[str, float] = {}
    for row in rows:
        source = str(row.get("source") or "unknown")
        try:
            duration = float(row.get("duration_sec") or 0.0)
        except (TypeError, ValueError):
            duration = 0.0
        result[source] = result.get(source, 0.0) + max(0.0, duration)
    return {key: round(value, 3) for key, value in sorted(result.items())}


def max_end(rows: list[dict[str, Any]]) -> float:
    ends: list[float] = []
    for row in rows:
        for key in ("end_sec", "hard_end_sec"):
            try:
                ends.append(float(row.get(key)))
                break
            except (TypeError, ValueError):
                continue
    return round(max(ends, default=0.0), 3)


def warning_text(session_manifest: dict[str, Any]) -> str:
    health = session_manifest.get("health") if isinstance(session_manifest.get("health"), dict) else {}
    warnings = health.get("warnings") if isinstance(health.get("warnings"), list) else []
    return "\n".join(str(item) for item in warnings)


def status_from(
    session_manifest: dict[str, Any] | None,
    live_report: dict[str, Any] | None,
    live_state: dict[str, Any] | None,
    experiment_state: dict[str, Any] | None,
    final_reconcile: dict[str, Any] | None,
    comparison: dict[str, Any] | None,
    warnings: str,
    segments: list[dict[str, Any]],
) -> str:
    if experiment_state:
        experiment_status = str(experiment_state.get("status") or "")
        if experiment_status in {
            "preview_running",
            "waiting_for_raw_readable",
            "disabled_backpressure",
            "disabled_pcm_copy",
            "failed",
            "running",
        }:
            return experiment_status
    if "backlog exceeded" in warnings:
        return "disabled_backpressure"
    if "live segment writer disabled" in warnings:
        return "disabled"
    if not segments and not live_report and not live_state and not final_reconcile:
        if session_manifest:
            return "not_started"
        return "missing_session"
    if str((final_reconcile or {}).get("status") or "") == "passed":
        return "completed"
    for payload in (live_report, live_state, experiment_state):
        if not isinstance(payload, dict):
            continue
        status = str(payload.get("status") or "")
        if status in {"failed", "disabled", "disabled_backpressure", "disabled_pcm_copy"}:
            return status
        if status == "completed_partial_draft":
            return status
        if status in {"completed", "stopped_by_limit", "capture_finished"}:
            return "completed"
        if status in {"running", "recording", "preview_running"}:
            return status
    if segments and session_manifest:
        return "completed_partial_draft"
    if session_manifest:
        return "not_started"
    return "missing_session"


def raw_capture_affected(session_manifest: dict[str, Any] | None) -> bool | str:
    if not session_manifest:
        return "unknown"
    health = session_manifest.get("health") if isinstance(session_manifest.get("health"), dict) else {}
    if bool(health.get("partial")):
        return "unknown"
    summary = str(health.get("summary") or "")
    if summary in {"ok", "warning"}:
        return False
    return "unknown"


def build_contract(session: Path, experiment_id: str, event_reason: str) -> dict[str, Any]:
    experiment_dir = session / "derived" / "experiments" / experiment_id
    live_dir = session / "derived" / "live"
    session_manifest = read_json(session / "session.json")
    live_report = read_json(live_dir / "live_pipeline_report.json")
    live_state = read_json(live_dir / "live_pipeline_state.json")
    existing_manifest = read_json(experiment_dir / "experiment_manifest.json")
    existing_experiment_state = read_json(experiment_dir / "state.json")
    raw_commit_state = read_json(experiment_dir / "raw_commit_state.json")
    fallback_dir = experiment_dir / "fallback"
    fallback_state = read_json(fallback_dir / "state.json")
    fallback_report = read_json(fallback_dir / "live_pipeline_report.json")
    final_reconcile = read_json(live_dir / "final_reconcile_report.json")
    comparison = read_json(live_dir / "live_batch_comparison.json")
    pipeline_report = read_json(session / "derived/pipeline-run/pipeline_run_report.json")
    readiness = read_json(session / "derived/readiness/session_readiness.json")
    segments = read_jsonl(live_dir / "segments.jsonl")
    raw_commits = read_jsonl(experiment_dir / "raw_segment_commits.jsonl")
    fallback_segments = read_jsonl(fallback_dir / "segments.jsonl")
    fallback_chunks = read_jsonl(fallback_dir / "chunks.jsonl")
    chunks = read_jsonl(live_dir / "chunks.jsonl")
    events = read_jsonl(session / "events.jsonl")
    warnings = warning_text(session_manifest or {})
    status = status_from(session_manifest, live_report, live_state, existing_experiment_state, final_reconcile, comparison, warnings, segments)
    raw_seconds = round(session_duration(session_manifest or {}), 3)
    raw_committed_seconds = max_end(raw_commits)
    sidecar_captured = max(
        max_end(segments),
        float(((live_report or {}).get("progress") or {}).get("captured_sec") or 0.0)
        if isinstance((live_report or {}).get("progress"), dict)
        else 0.0,
    )
    sidecar_processed = max(
        max_end(chunks),
        float(((live_report or {}).get("progress") or {}).get("processed_sec") or 0.0)
        if isinstance((live_report or {}).get("progress"), dict)
        else 0.0,
    )
    existing_answers = (existing_experiment_state or {}).get("answers") if isinstance((existing_experiment_state or {}).get("answers"), dict) else {}
    existing_counters = (existing_experiment_state or {}).get("counters") if isinstance((existing_experiment_state or {}).get("counters"), dict) else {}
    transport_counter_keys = (
        "pending_pcm_packets",
        "pending_pcm_seconds_by_source",
        "dropped_pcm_packets",
        "max_pending_pcm_packets",
        "max_pending_pcm_seconds",
        "max_observed_pending_pcm_seconds",
        "artificial_write_delay_ms",
    )
    transport_counters = {
        key: existing_counters[key]
        for key in transport_counter_keys
        if key in existing_counters
    }
    existing_config = (existing_manifest or {}).get("config") if isinstance((existing_manifest or {}).get("config"), dict) else {}
    live_preview_mode = str(
        (existing_experiment_state or {}).get("live_preview_mode")
        or existing_config.get("handoff")
        or "committed_pcm_queue_v1"
    )
    backpressure = (
        "backlog exceeded" in warnings
        or "live segment writer disabled" in warnings
        or bool(existing_answers.get("backpressure_detected"))
    )
    affected = raw_capture_affected(session_manifest)
    batch_report_status = str((pipeline_report or {}).get("status") or "missing")
    batch_reproducible = bool(
        session_manifest
        and (session / "audio/mic/000001.caf").exists()
        and (session / "audio/remote/000001.caf").exists()
        and affected is not True
    )
    session_display = display_session(session)
    recovery_command = f"murmurmark process {session_display}"
    comparison_command = f"murmurmark experiment compare {session_display} --experiment {experiment_id}"
    draft_recovery_command = f"murmurmark experiment recover-draft {session_display} --experiment {experiment_id}"

    outputs = {
        "compat_live_dir": rel(live_dir, session),
        "segments": rel(live_dir / "segments.jsonl", session),
        "chunks": rel(live_dir / "chunks.jsonl", session),
        "draft_transcript": rel(live_dir / "transcript.draft.md", session),
        "worker_log": rel(live_dir / "live_worker.log", session),
        "raw_segment_commits": rel(experiment_dir / "raw_segment_commits.jsonl", session),
        "experiment_audio": rel(experiment_dir / "audio", session) or f"derived/experiments/{experiment_id}/audio",
        "live_pipeline_report": rel(live_dir / "live_pipeline_report.json", session),
        "live_batch_comparison": rel(live_dir / "live_batch_comparison.json", session),
        "final_reconcile_report": rel(live_dir / "final_reconcile_report.json", session),
        "pilot_report": rel(live_dir / "live_parity_pilot_report.json", session),
        "experiment_report": f"derived/experiments/{experiment_id}/report.json",
        "experiment_report_markdown": f"derived/experiments/{experiment_id}/report.md",
        "fallback_dir": rel(fallback_dir, session),
        "fallback_segments": rel(fallback_dir / "segments.jsonl", session),
        "fallback_chunks": rel(fallback_dir / "chunks.jsonl", session),
        "fallback_draft_transcript": rel(fallback_dir / "transcript.draft.md", session),
        "fallback_state": rel(fallback_dir / "state.json", session),
        "fallback_report": rel(fallback_dir / "live_pipeline_report.json", session),
    }
    outputs = {key: value for key, value in outputs.items() if value is not None}

    state = {
        "schema": STATE_SCHEMA,
        "experiment_id": experiment_id,
        "kind": "near_realtime_shadow",
        "status": status,
        "updated_at": utc_now(),
        "live_preview_mode": live_preview_mode,
        "reason": (existing_experiment_state or {}).get("reason") or disabled_reason(status, warnings, live_report),
        "answers": {
            "experiment_started": bool(segments or raw_commits or live_report or live_state or existing_experiment_state),
            "raw_seconds_recorded": raw_seconds,
            "raw_commit_seconds": raw_committed_seconds,
            "sidecar_seconds_captured": round(sidecar_captured, 3),
            "sidecar_seconds_preprocessed": round(sidecar_processed, 3),
            "sidecar_seconds_asr": round(sidecar_processed, 3),
            "dropped_chunks": 0,
            "backpressure_detected": backpressure,
            "sidecar_disabled": status.startswith("disabled") or backpressure,
            "raw_capture_affected": affected,
            "batch_reproducible_from_raw": batch_reproducible,
            "batch_pipeline_status": batch_report_status,
        },
        "counters": {
            "segments_seen": len(segments),
            "raw_commits_seen": len(raw_commits),
            "chunks_seen": len(chunks),
            "segments_by_source_seconds": sum_by_source(segments),
            "screen_capture_restart_count": (
                ((session_manifest or {}).get("health") or {}).get("screen_capture_restart_count")
                if isinstance((session_manifest or {}).get("health"), dict)
                else None
            ),
            **transport_counters,
        },
        "inputs": {
            "session_manifest": "session.json",
            "events": "events.jsonl" if (session / "events.jsonl").exists() else None,
            "raw_mic": "audio/mic/000001.caf" if (session / "audio/mic/000001.caf").exists() else None,
            "raw_remote": "audio/remote/000001.caf" if (session / "audio/remote/000001.caf").exists() else None,
        },
        "outputs": outputs,
        "recovery_command": recovery_command,
        "comparison_command": comparison_command,
        "draft_recovery_command": draft_recovery_command,
        "fallback": {
            "status": (fallback_state or {}).get("status") or (fallback_report or {}).get("status") or "not_run",
            "segments_seen": len(fallback_segments),
            "chunks_seen": len(fallback_chunks),
            "provenance": "post_stop_raw_commit_recovery",
        },
    }

    manifest = {
        "schema": MANIFEST_SCHEMA,
        "experiment_id": experiment_id,
        "kind": "near_realtime_shadow",
        "status": status,
        "started_at": first_event_time(events, "experiment_sidecar.prepare") or first_event_time(events, "live_pipeline.prepare") or first_event_time(events, "capture.prepare"),
        "ended_at": ((session_manifest or {}).get("ended_at") if session_manifest else None),
        "config": {
            "segment_sec": (
                value_from(live_state, live_report, "segment_sec")
                or value_from(live_report, live_report, "parameters.segment_sec")
                or (existing_experiment_state or {}).get("segment_sec")
                or existing_config.get("segment_sec")
                or (raw_commit_state or {}).get("segment_sec")
            ),
            "overlap_sec": (
                value_from(live_state, live_report, "overlap_sec")
                or (existing_experiment_state or {}).get("overlap_sec")
                or existing_config.get("overlap_sec")
            ),
            "compatibility_alias": "derived/live",
            "handoff": live_preview_mode,
            "fallback_handoff": "raw_segment_commits",
        },
        "inputs": state["inputs"],
        "outputs": outputs,
        "disabled_reason": disabled_reason(status, warnings, live_report),
        "raw_capture_affected": affected,
        "batch_authoritative": True,
        "promotion_allowed": False,
        "recovery_command": recovery_command,
        "comparison_command": comparison_command,
        "draft_recovery_command": draft_recovery_command,
        "state": f"derived/experiments/{experiment_id}/state.json",
        "events": f"derived/experiments/{experiment_id}/events.jsonl",
    }

    report = {
        "schema": REPORT_SCHEMA,
        "experiment_id": experiment_id,
        "generated_at": utc_now(),
        "session": session_display,
        "status": status,
        "raw_capture_affected": affected,
        "batch_authoritative": True,
        "promotion_allowed": False,
        "summary": {
            "raw_seconds_recorded": raw_seconds,
            "sidecar_seconds_captured": round(sidecar_captured, 3),
            "sidecar_seconds_processed": round(sidecar_processed, 3),
            "batch_pipeline_status": batch_report_status,
            "readiness_status": (readiness or {}).get("use_gate") or (readiness or {}).get("status") or "missing",
            "sidecar_disabled": state["answers"]["sidecar_disabled"],
            "backpressure_detected": backpressure,
            "fallback_status": state["fallback"]["status"],
            "fallback_chunks": state["fallback"]["chunks_seen"],
        },
        "machine_answers": state["answers"],
        "manifest": f"derived/experiments/{experiment_id}/experiment_manifest.json",
        "state": f"derived/experiments/{experiment_id}/state.json",
        "recovery_command": recovery_command,
        "comparison_command": comparison_command,
        "draft_recovery_command": draft_recovery_command,
    }

    write_json(experiment_dir / "experiment_manifest.json", manifest)
    write_json(experiment_dir / "state.json", state)
    write_json(experiment_dir / "report.json", report)
    write_report_md(experiment_dir / "report.md", report)
    append_jsonl(
        experiment_dir / "events.jsonl",
        {
            "schema": EVENT_SCHEMA,
            "t": utc_now(),
            "type": "experiment_contract.refreshed",
            "reason": event_reason,
            "status": status,
            "raw_capture_affected": affected,
            "batch_authoritative": True,
            "promotion_allowed": False,
        },
    )
    return {"manifest": manifest, "state": state, "report": report}


def value_from(first: dict[str, Any] | None, second: dict[str, Any] | None, key: str) -> Any:
    def lookup(payload: dict[str, Any] | None, dotted: str) -> Any:
        current: Any = payload
        for part in dotted.split("."):
            if not isinstance(current, dict):
                return None
            current = current.get(part)
        return current

    return lookup(first, key) or lookup(second, key)


def first_event_time(events: list[dict[str, Any]], event_type: str) -> str | None:
    for row in events:
        if row.get("type") == event_type and row.get("t"):
            return str(row.get("t"))
    return None


def disabled_reason(status: str, warnings: str, live_report: dict[str, Any] | None) -> str | None:
    if "backlog exceeded" in warnings:
        return "sidecar_backpressure"
    if "live segment writer disabled" in warnings:
        return "sidecar_writer_disabled"
    if status == "failed":
        return str((live_report or {}).get("error") or "sidecar_failed")
    if status.startswith("disabled"):
        return status
    return None


def write_report_md(path: Path, report: dict[str, Any]) -> None:
    summary = report["summary"]
    answers = report["machine_answers"]
    lines = [
        "# Experimental Sidecar Report",
        "",
        f"- experiment: `{report['experiment_id']}`",
        f"- status: `{report['status']}`",
        f"- raw_capture_affected: `{report['raw_capture_affected']}`",
        f"- batch_authoritative: `{report['batch_authoritative']}`",
        f"- promotion_allowed: `{report['promotion_allowed']}`",
        f"- raw seconds: `{summary['raw_seconds_recorded']}`",
        f"- sidecar captured seconds: `{summary['sidecar_seconds_captured']}`",
        f"- sidecar processed seconds: `{summary['sidecar_seconds_processed']}`",
        f"- backpressure_detected: `{summary['backpressure_detected']}`",
        f"- sidecar_disabled: `{summary['sidecar_disabled']}`",
        f"- batch_pipeline_status: `{summary['batch_pipeline_status']}`",
        "",
        "## Machine Answers",
        "",
    ]
    for key, value in answers.items():
        lines.append(f"- `{key}`: `{value}`")
    lines += [
        "",
        "## Commands",
        "",
        f"- recovery: `{report['recovery_command']}`",
        f"- comparison: `{report['comparison_command']}`",
        f"- post-stop draft recovery: `{report['draft_recovery_command']}`",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def print_status(contract: dict[str, Any]) -> None:
    report = contract["report"]
    answers = report["machine_answers"]
    def render(value: Any) -> str:
        if isinstance(value, bool):
            return str(value).lower()
        return str(value)

    print("experiment:")
    print(f"  id: {report['experiment_id']}")
    print(f"  status: {report['status']}")
    print(f"  raw_capture_affected: {render(report['raw_capture_affected'])}")
    print(f"  batch_authoritative: {str(report['batch_authoritative']).lower()}")
    print(f"  promotion_allowed: {str(report['promotion_allowed']).lower()}")
    print(f"  live_preview_mode: {contract['state'].get('live_preview_mode') or 'unknown'}")
    print(f"  raw_seconds_recorded: {answers['raw_seconds_recorded']}")
    print(f"  sidecar_seconds_captured: {answers['sidecar_seconds_captured']}")
    print(f"  sidecar_seconds_asr: {answers['sidecar_seconds_asr']}")
    print(f"  sidecar_disabled: {str(answers['sidecar_disabled']).lower()}")
    print(f"  backpressure_detected: {str(answers['backpressure_detected']).lower()}")
    print(f"  fallback_status: {report['summary'].get('fallback_status') or 'not_run'}")
    print(f"  batch_reproducible_from_raw: {str(answers['batch_reproducible_from_raw']).lower()}")
    print(f"  manifest: {report['manifest']}")
    print(f"  report: derived/experiments/{report['experiment_id']}/report.md")
    print(f"next: {report['recovery_command']}")


def print_report(contract: dict[str, Any]) -> None:
    report = contract["report"]
    raw_capture_affected = report["raw_capture_affected"]
    if isinstance(raw_capture_affected, bool):
        raw_capture_affected = str(raw_capture_affected).lower()
    print(f"experiment_report: derived/experiments/{report['experiment_id']}/report.json")
    print(f"experiment_report_markdown: derived/experiments/{report['experiment_id']}/report.md")
    print(f"manifest: {report['manifest']}")
    print(f"state: {report['state']}")
    print(f"raw_capture_affected: {raw_capture_affected}")
    print(f"batch_authoritative: {str(report['batch_authoritative']).lower()}")
    print(f"promotion_allowed: {str(report['promotion_allowed']).lower()}")
    print(f"live_preview_mode: {contract['state'].get('live_preview_mode') or 'unknown'}")
    print(f"recovery_command: {report['recovery_command']}")
    print(f"comparison_command: {report['comparison_command']}")
    print(f"draft_recovery_command: {report['draft_recovery_command']}")


def compare_timeout(session: Path) -> tuple[float, str]:
    configured_timeout = os.environ.get("MURMURMARK_LIVE_BATCH_COMPARE_TIMEOUT_SEC")
    if configured_timeout is not None:
        return float(configured_timeout), "environment_override"
    manifest = read_json(session / "session.json") or {}
    duration = session_duration(manifest)
    return max(300.0, min(1800.0, 120.0 + duration * 0.12)), "session_duration_adaptive"


def run_compare(session: Path) -> int:
    script = Path("scripts/compare-live-batch.py")
    if not script.exists():
        print(f"missing comparison script: {script}", file=sys.stderr)
        return 1
    timeout, timeout_policy = compare_timeout(session)
    try:
        return subprocess.run([sys.executable, str(script), str(session)], timeout=timeout, check=False).returncode
    except subprocess.TimeoutExpired:
        experiment_id = DEFAULT_EXPERIMENT_ID
        append_jsonl(
            session / "derived" / "experiments" / experiment_id / "events.jsonl",
            {
                "schema": EVENT_SCHEMA,
                "created_at": utc_now(),
                "event": "live_batch_compare.timeout",
                "experiment_id": experiment_id,
                "status": "warning",
                "details": {
                    "timeout_sec": timeout,
                    "timeout_policy": timeout_policy,
                    "script": str(script),
                    "reason": "comparison_timeout",
                },
            },
        )
        print(
            f"warning: live/batch comparison timed out after {timeout:.1f}s; "
            "using existing comparison artifacts",
            file=sys.stderr,
        )
        return 0


def run_recover_draft(session: Path, experiment_id: str) -> int:
    script = Path("scripts/raw-sidecar-worker.py")
    if not script.exists():
        print(f"missing raw sidecar worker: {script}", file=sys.stderr)
        return 1
    return subprocess.run(
        [
            sys.executable,
            str(script),
            str(session),
            "--experiment",
            experiment_id,
            "--poll-sec",
            "0.2",
            "--idle-after-session-json-sec",
            "0.2",
            "--live-worker-timeout-sec",
            os.environ.get("MURMURMARK_RAW_SIDECAR_RECOVERY_TIMEOUT_SEC", "1800"),
        ],
        check=False,
    ).returncode


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Write and inspect MurmurMark experimental sidecar contract artifacts.")
    parser.add_argument("command", choices=["refresh", "status", "report", "compare", "recover-draft"])
    parser.add_argument("session", help="Session path or latest.")
    parser.add_argument("--experiment", default=DEFAULT_EXPERIMENT_ID)
    parser.add_argument("--sessions-root", default="sessions")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    session = resolve_session(args.session, Path(args.sessions_root))
    if args.command == "compare":
        status = run_compare(session)
        if status != 0:
            return status
    elif args.command == "recover-draft":
        status = run_recover_draft(session, args.experiment)
        if status != 0:
            return status
    contract = build_contract(session, args.experiment, args.command)
    if args.command == "status":
        print_status(contract)
    elif args.command == "report":
        print_report(contract)
    elif args.command == "compare":
        print_report(contract)
    elif args.command == "recover-draft":
        print_report(contract)
    else:
        manifest_path = f"{display_session(session)}/derived/experiments/{args.experiment}/experiment_manifest.json"
        print(f"experiment_manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
