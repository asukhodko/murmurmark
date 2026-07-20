#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shlex
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_VERSION = "0.1.2"
OUTCOME_SCHEMA = "murmurmark.outcome/v1"
REVIEW_PLAN_SCHEMA = "murmurmark.outcome_review_plan/v1"
RUN_SCHEMA = "murmurmark.pipeline_run/v1"
SUGGESTED_REVIEW_REPORT = Path("derived/readiness/review-plan/review_workspace_apply_report.json")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate the user-facing MurmurMark outcome for one session.")
    parser.add_argument("session", type=Path)
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Defaults to SESSION/derived/outcome.",
    )
    parser.add_argument(
        "--pipeline-report",
        type=Path,
        default=None,
        help="Defaults to SESSION/derived/pipeline-run/pipeline_run_report.json.",
    )
    parser.add_argument("--print-summary", action="store_true")
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def rel(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def shell_path(path: Path, base: Path) -> str:
    return shlex.quote(rel(path, base))


def safe_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def safe_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def compact_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "meeting_duration_sec",
        "review_burden_sec",
        "review_burden_ratio",
        "notes_review_burden_sec",
        "notes_review_burden_ratio",
        "transcript_review_burden_sec",
        "transcript_review_burden_ratio",
        "suggested_closure_status",
        "suggested_closure_generated_rows",
        "suggested_closure_generated_seconds",
        "suggested_closure_actionable_rows",
        "suggested_closure_actionable_seconds",
        "suggested_closure_needs_review_rows",
        "suggested_closure_needs_review_seconds",
        "suggested_closure_auto_rows",
        "suggested_closure_auto_seconds",
        "suggested_closure_auto_keep_rows",
        "suggested_closure_auto_keep_seconds",
        "suggested_closure_auto_drop_rows",
        "suggested_closure_auto_drop_seconds",
        "suggested_closure_manual_remaining_rows",
        "suggested_closure_manual_remaining_seconds",
        "audio_review_probable_error_seconds",
        "audio_review_stronger_judge_seconds",
        "audio_review_remote_leak_probable_error_seconds",
        "audit_harmful_seconds_after",
        "audit_review_seconds",
        "local_only_island_recall",
        "local_recall_missing_island_count",
        "local_recall_possible_lost_me_seconds",
        "transcript_order_review_seconds",
        "transcript_order_probable_order_risk_count",
        "remote_forbidden_review_burden_seconds",
        "needs_review_count",
        "notes_needs_review_count",
    ]
    return {key: metrics.get(key) for key in keys if key in metrics}


def suggested_review_report(session: Path) -> dict[str, Any] | None:
    payload = read_json(session / SUGGESTED_REVIEW_REPORT)
    if not isinstance(payload, dict):
        return None
    if str(payload.get("answers_source") or "") != "suggested":
        return None
    return payload


def suggested_review_metrics(session: Path) -> dict[str, Any]:
    report = suggested_review_report(session)
    if not isinstance(report, dict):
        return {}
    closure = report.get("suggested_closure")
    if not isinstance(closure, dict):
        return {}
    generated = closure.get("generated_suggestions") if isinstance(closure.get("generated_suggestions"), dict) else {}
    closed = closure.get("closed_by_suggestions") if isinstance(closure.get("closed_by_suggestions"), dict) else {}
    remaining = closure.get("remaining_manual_queue") if isinstance(closure.get("remaining_manual_queue"), dict) else {}
    generated_by_decision = generated.get("by_decision") if isinstance(generated.get("by_decision"), list) else []

    def decision_count(key: str) -> int:
        for item in generated_by_decision:
            if isinstance(item, dict) and item.get("key") == key:
                return safe_int(item.get("count")) or 0
        return 0

    def decision_seconds(key: str) -> float:
        for item in generated_by_decision:
            if isinstance(item, dict) and item.get("key") == key:
                return safe_float(item.get("seconds")) or 0.0
        return 0.0

    rows = safe_int(generated.get("rows")) or 0
    seconds = safe_float(generated.get("seconds")) or 0.0
    auto_rows = safe_int(closed.get("rows")) or 0
    auto_seconds = safe_float(closed.get("seconds")) or 0.0
    remaining_rows = safe_int(remaining.get("rows")) or 0
    remaining_seconds = safe_float(remaining.get("seconds")) or 0.0
    return {
        "suggested_closure_report_dry_run": report.get("dry_run"),
        "suggested_closure_status": closure.get("status"),
        "suggested_closure_generated_rows": rows,
        "suggested_closure_generated_seconds": round(seconds, 3),
        "suggested_closure_actionable_rows": auto_rows,
        "suggested_closure_actionable_seconds": round(auto_seconds, 3),
        "suggested_closure_needs_review_rows": decision_count("needs_review"),
        "suggested_closure_needs_review_seconds": round(decision_seconds("needs_review"), 3),
        "suggested_closure_auto_rows": auto_rows,
        "suggested_closure_auto_seconds": round(auto_seconds, 3),
        "suggested_closure_auto_keep_rows": decision_count("keep_me"),
        "suggested_closure_auto_keep_seconds": round(decision_seconds("keep_me"), 3),
        "suggested_closure_auto_drop_rows": decision_count("drop_me"),
        "suggested_closure_auto_drop_seconds": round(decision_seconds("drop_me"), 3),
        "suggested_closure_manual_remaining_rows": remaining_rows,
        "suggested_closure_manual_remaining_seconds": round(remaining_seconds, 3),
    }


def merged_metrics(readiness: dict[str, Any] | None, session: Path) -> dict[str, Any]:
    raw = readiness.get("metrics") if isinstance(readiness, dict) and isinstance(readiness.get("metrics"), dict) else {}
    metrics = dict(raw)
    for key, value in suggested_review_metrics(session).items():
        if value is not None:
            metrics[key] = value
    return metrics


def first_command(commands: Any) -> str | None:
    if not isinstance(commands, list):
        return None
    for item in commands:
        if not isinstance(item, dict):
            continue
        command = str(item.get("command") or "").strip()
        if command:
            return command
    return None


def first_recommended_command(items: Any) -> str | None:
    if not isinstance(items, list):
        return None
    for item in items:
        if not isinstance(item, dict):
            continue
        command = str(item.get("recommended_command") or item.get("command") or "").strip()
        if command:
            return command
    return None


def outcome_from_readiness(readiness: dict[str, Any] | None, pipeline_report: dict[str, Any] | None) -> str:
    if not isinstance(readiness, dict):
        status = str((pipeline_report or {}).get("status") or "")
        if status == "failed":
            return "pipeline_failed"
        if status == "blocked":
            return "blocked"
        return "partial"
    gate = str(readiness.get("use_gate") or "pipeline_incomplete")
    verdict = str(readiness.get("verdict") or "")
    if gate == "ready_for_notes":
        return "ready_for_notes"
    if gate == "review_first":
        return "review_first"
    if gate == "pipeline_incomplete":
        return "partial"
    if gate == "pipeline_incomplete_review_first":
        return "partial"
    if verdict == "failed":
        return "blocked"
    return "blocked"


def outcome_from_gates(base_outcome: str, gates: list[dict[str, Any]]) -> str:
    hard_fail_ids = {
        str(gate.get("id") or "")
        for gate in gates
        if gate.get("status") == "fail" and gate.get("severity") == "hard"
    }
    if "last_pipeline_run" in hard_fail_ids:
        return "pipeline_failed"
    if hard_fail_ids:
        if hard_fail_ids & {"session_readiness", "pipeline_complete"}:
            return "partial"
        return "blocked"
    if any(gate.get("status") == "fail" and gate.get("severity") in {"risk", "review"} for gate in gates):
        return "blocked"
    review_or_unknown = [
        gate
        for gate in gates
        if gate.get("status") in {"review", "unknown"} and gate.get("severity") != "export"
    ]
    if base_outcome == "ready_for_notes" and review_or_unknown:
        return "review_first"
    return base_outcome


def evaluate_gates(
    session: Path,
    readiness: dict[str, Any] | None,
    pipeline_report: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    gates: list[dict[str, Any]] = []
    session_json = session / "session.json"
    gates.append(
        {
            "id": "session_package",
            "status": "pass" if session_json.exists() else "fail",
            "severity": "hard",
            "message": "session.json exists" if session_json.exists() else "session.json is missing",
            "path": rel(session_json, session),
        }
    )
    for audio_id, audio_dir in (("raw_mic", session / "audio/mic"), ("raw_remote", session / "audio/remote")):
        exists = audio_dir.exists() and any(audio_dir.iterdir())
        gates.append(
            {
                "id": audio_id,
                "status": "pass" if exists else "fail",
                "severity": "hard",
                "message": f"{audio_id} files exist" if exists else f"{audio_id} files are missing",
                "path": rel(audio_dir, session),
            }
        )

    if not isinstance(readiness, dict):
        gates.append(
            {
                "id": "session_readiness",
                "status": "fail",
                "severity": "hard",
                "message": "session_readiness.json is missing; run the post-recording pipeline.",
                "path": "derived/readiness/session_readiness.json",
            }
        )
        return gates

    metrics = readiness.get("metrics") if isinstance(readiness.get("metrics"), dict) else {}
    outputs = readiness.get("outputs") if isinstance(readiness.get("outputs"), dict) else {}
    profile = str(readiness.get("selected_profile") or "missing")
    gate = str(readiness.get("use_gate") or "pipeline_incomplete")
    verdict = str(readiness.get("verdict") or "unknown")
    pipeline_status = str(readiness.get("pipeline_status") or "unknown")

    gates.append(
        {
            "id": "pipeline_complete",
            "status": "pass" if pipeline_status == "complete" else "fail",
            "severity": "hard",
            "message": f"pipeline_status={pipeline_status}",
            "value": pipeline_status,
        }
    )
    gates.append(
        {
            "id": "quality_verdict",
            "status": "pass" if verdict not in {"failed", "risky"} else "fail" if verdict == "failed" else "review",
            "severity": "hard" if verdict == "failed" else "risk",
            "message": f"quality verdict is {verdict}",
            "value": verdict,
        }
    )

    for output_id in ("clean_dialogue", "quality_report", "notes", "quality_verdict"):
        item = outputs.get(output_id)
        exists = isinstance(item, dict) and item.get("exists") is True
        gates.append(
            {
                "id": f"output:{output_id}",
                "status": "pass" if exists else "fail",
                "severity": "hard" if output_id in {"clean_dialogue", "quality_report"} else "risk",
                "message": f"{output_id} exists for {profile}" if exists else f"{output_id} is missing for {profile}",
                "path": item.get("path") if isinstance(item, dict) else None,
            }
        )

    duration = safe_float(metrics.get("meeting_duration_sec")) or 0.0
    review_sec = safe_float(metrics.get("review_burden_sec")) or 0.0
    review_ratio = safe_float(metrics.get("review_burden_ratio"))
    if review_ratio is None and duration > 0:
        review_ratio = review_sec / duration
    review_ratio = review_ratio or 0.0
    gates.append(
        {
            "id": "review_burden",
            "status": "pass" if review_ratio <= 0.025 else "review" if review_ratio <= 0.08 else "fail",
            "severity": "review" if review_ratio > 0.025 else "info",
            "message": "manual review burden threshold",
            "value": round(review_ratio, 6),
            "threshold_ready": 0.025,
            "threshold_block": 0.08,
            "seconds": round(review_sec, 3),
        }
    )

    local_recall = safe_float(metrics.get("local_only_island_recall"))
    gates.append(
        {
            "id": "local_recall",
            "status": "unknown" if local_recall is None else "pass" if local_recall >= 0.9 else "review",
            "severity": "review" if local_recall is not None and local_recall < 0.9 else "info",
            "message": "local-only speech recall should stay high",
            "value": local_recall,
            "threshold": 0.9,
        }
    )

    order_seconds = safe_float(metrics.get("transcript_order_review_seconds"))
    order_count = safe_int(metrics.get("transcript_order_probable_order_risk_count"))
    has_order_flag = "transcript_order_risk" in [str(item) for item in readiness.get("risk_flags") or []]
    gates.append(
        {
            "id": "transcript_order",
            "status": "review" if has_order_flag or (order_seconds or 0.0) > 0 or (order_count or 0) > 0 else "pass",
            "severity": "review" if has_order_flag else "info",
            "message": "chronology risk in transcript",
            "seconds": order_seconds,
            "count": order_count,
        }
    )

    harmful_seconds = safe_float(metrics.get("audit_harmful_seconds_after"))
    if harmful_seconds is None:
        harmful_seconds = safe_float(metrics.get("audio_review_probable_error_seconds"))
    gates.append(
        {
            "id": "harmful_remote_in_me",
            "status": "unknown" if harmful_seconds is None else "pass" if harmful_seconds <= 5 else "review",
            "severity": "review" if harmful_seconds and harmful_seconds > 5 else "info",
            "message": "probable harmful remote/ASR noise left in Me",
            "seconds": harmful_seconds,
            "threshold": 5,
        }
    )

    export_blockers = readiness.get("export_blockers") if isinstance(readiness.get("export_blockers"), list) else []
    gates.append(
        {
            "id": "export_allowed",
            "status": "pass" if gate == "ready_for_notes" and not export_blockers else "review",
            "severity": "export",
            "message": "full export gate",
            "blockers": export_blockers,
        }
    )

    if isinstance(pipeline_report, dict):
        status = str(pipeline_report.get("status") or "unknown")
        pipeline_ok = status in {"passed", "planned"}
        gates.append(
            {
                "id": "last_pipeline_run",
                "status": "pass" if pipeline_ok else "fail",
                "severity": "info" if pipeline_ok else "hard",
                "message": f"last pipeline run status={status}",
                "value": status,
            }
        )
    return gates


def gate_summary(gates: list[dict[str, Any]]) -> dict[str, Any]:
    by_status: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    blocking_ids: list[str] = []
    review_ids: list[str] = []
    unknown_ids: list[str] = []
    for gate in gates:
        status = str(gate.get("status") or "unknown")
        severity = str(gate.get("severity") or "unknown")
        gate_id = str(gate.get("id") or "unknown")
        by_status[status] = by_status.get(status, 0) + 1
        by_severity[severity] = by_severity.get(severity, 0) + 1
        if status == "fail":
            blocking_ids.append(gate_id)
        elif status == "review":
            review_ids.append(gate_id)
        elif status == "unknown":
            unknown_ids.append(gate_id)
    return {
        "by_status": dict(sorted(by_status.items())),
        "by_severity": dict(sorted(by_severity.items())),
        "blocking_gate_ids": blocking_ids,
        "review_gate_ids": review_ids,
        "unknown_gate_ids": unknown_ids,
    }


def build_review_plan(session: Path, readiness: dict[str, Any] | None, outcome: str) -> dict[str, Any]:
    session_arg = shell_path(session, Path.cwd())
    if not isinstance(readiness, dict):
        lanes = [
            {
                "id": "rerun_pipeline",
                "status": "open",
                "severity": "block",
                "estimated_seconds": None,
                "recommended_command": f"murmurmark process {session_arg}",
                "reason": "readiness is missing",
            }
        ]
    else:
        metrics = merged_metrics(readiness, session)
        lanes = []
        suggested_auto_seconds = safe_float(metrics.get("suggested_closure_auto_seconds")) or 0.0
        suggested_auto_rows = safe_int(metrics.get("suggested_closure_auto_rows")) or 0
        suggested_remaining_seconds = safe_float(metrics.get("suggested_closure_manual_remaining_seconds"))
        suggested_report = suggested_review_report(session)
        suggested_report_exists = suggested_report is not None
        suggested_pending_apply = bool(suggested_report.get("dry_run")) if isinstance(suggested_report, dict) else False
        if suggested_pending_apply and (suggested_auto_rows > 0 or suggested_auto_seconds > 0):
            lanes.append(
                {
                    "id": "suggested_review_apply",
                    "status": "open",
                    "severity": "review",
                    "estimated_seconds": suggested_remaining_seconds,
                    "auto_close_seconds": suggested_auto_seconds,
                    "recommended_command": f"murmurmark review suggested apply {session_arg}",
                    "reason": "safe suggested review decisions can reduce the manual queue",
                }
            )
        elif suggested_report_exists and (suggested_remaining_seconds or 0.0) > 0:
            lanes.append(
                {
                    "id": "suggested_review_progress",
                    "status": "open",
                    "severity": "review",
                    "estimated_seconds": suggested_remaining_seconds,
                    "auto_close_seconds": 0.0,
                    "recommended_command": f"murmurmark review progress --session {session_arg}",
                    "reason": "safe suggested rows were already applied; inspect the remaining manual tail",
                }
            )
        elif outcome == "review_first" and not suggested_report_exists:
            lanes.append(
                {
                    "id": "suggested_review_preview",
                    "status": "open",
                    "severity": "review",
                    "estimated_seconds": safe_float(metrics.get("review_burden_sec")),
                    "auto_close_seconds": None,
                    "recommended_command": f"murmurmark review suggested {session_arg}",
                    "reason": "preview safe generated review decisions before manual listening",
                }
            )
        if "transcript_order_risk" in [str(item) for item in readiness.get("risk_flags") or []]:
            lanes.append(
                {
                    "id": "transcript_order",
                    "status": "open",
                    "severity": "review",
                    "estimated_seconds": safe_float(metrics.get("transcript_order_review_seconds")),
                    "recommended_command": f"less {shell_path(session / 'derived/audit/order/transcript_order_review.md', Path.cwd())}",
                    "reason": "reply order may be wrong in some intervals",
                }
            )
        local_seconds = safe_float(metrics.get("local_recall_needs_review_seconds")) or safe_float(
            metrics.get("local_recall_possible_lost_me_seconds")
        )
        if local_seconds:
            lanes.append(
                {
                    "id": "local_recall",
                    "status": "open",
                    "severity": "review",
                    "estimated_seconds": local_seconds,
                    "recommended_command": f"less {shell_path(session / 'derived/audit/local-recall/local_recall_review.md', Path.cwd())}",
                    "reason": "possible lost local speech regions",
                }
            )
        audio_review_seconds = safe_float(metrics.get("audio_review_probable_error_seconds")) or 0.0
        stronger_seconds = safe_float(metrics.get("audio_review_stronger_judge_seconds")) or 0.0
        if audio_review_seconds or stronger_seconds:
            lanes.append(
                {
                    "id": "audio_review",
                    "status": "open",
                    "severity": "review",
                    "estimated_seconds": round(audio_review_seconds + stronger_seconds, 3),
                    "recommended_command": f"less {shell_path(session / 'derived/audit/audio-review-pack/audio_review_report.md', Path.cwd())}",
                    "reason": "audio evidence still marks transcript-risk regions",
                }
            )
        if not lanes and outcome == "review_first":
            lanes.append(
                {
                    "id": "readiness_review",
                    "status": "open",
                    "severity": "review",
                    "estimated_seconds": safe_float(metrics.get("review_burden_sec")),
                    "recommended_command": str(readiness.get("recommended_next") or f"murmurmark status {session_arg}"),
                    "reason": "session readiness requires review but no specialized lane was detected",
                }
            )
    return {
        "schema": REVIEW_PLAN_SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": {"name": "evaluate-outcome", "version": SCRIPT_VERSION},
        "session": str(session),
        "outcome": outcome,
        "lanes": lanes,
        "summary": {
            "open_lanes": len([item for item in lanes if item.get("status") == "open"]),
            "estimated_seconds": round(
                sum(safe_float(item.get("estimated_seconds")) or 0.0 for item in lanes),
                3,
            ),
            "auto_close_seconds": round(
                sum(safe_float(item.get("auto_close_seconds")) or 0.0 for item in lanes),
                3,
            ),
        },
    }


def expected_outputs_from_report(session: Path, pipeline_report: dict[str, Any] | None) -> list[dict[str, Any]]:
    expected = []
    plan = pipeline_report.get("plan") if isinstance(pipeline_report, dict) and isinstance(pipeline_report.get("plan"), dict) else {}
    raw_outputs = plan.get("expected_outputs") if isinstance(plan.get("expected_outputs"), list) else []
    for item in raw_outputs:
        if not isinstance(item, dict):
            continue
        raw_path = item.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            continue
        path = Path(raw_path)
        absolute = path if path.is_absolute() else session / path
        expected.append(
            {
                "id": item.get("id"),
                "path": raw_path,
                "produced_by": item.get("produced_by"),
                "purpose": item.get("purpose"),
                "exists": absolute.exists(),
            }
        )
    existing_ids = {str(item.get("id") or "") for item in expected}
    for output_id, raw_path in (
        ("outcome", "derived/outcome/outcome.json"),
        ("outcome_markdown", "derived/outcome/outcome.md"),
        ("review_plan", "derived/outcome/review_plan.json"),
        ("next_command", "derived/outcome/next_command.txt"),
        ("run_manifest", "derived/run/pipeline_run.json"),
        ("readiness", "derived/readiness/session_readiness.json"),
    ):
        if output_id not in existing_ids:
            exists = True if output_id == "run_manifest" else (session / raw_path).exists()
            expected.append(
                {
                    "id": output_id,
                    "path": raw_path,
                    "produced_by": "evaluate-outcome",
                    "exists": exists,
                }
            )
    return expected


def build_run_manifest(session: Path, pipeline_report: dict[str, Any] | None, outcome: str, next_command: str) -> dict[str, Any]:
    steps = []
    failed_step = None
    current_step = None
    if isinstance(pipeline_report, dict):
        raw_steps = pipeline_report.get("steps")
        if isinstance(raw_steps, list):
            for item in raw_steps:
                if not isinstance(item, dict):
                    continue
                status = item.get("status")
                step_row = {
                    "name": item.get("name"),
                    "status": status,
                    "started_at": item.get("started_at"),
                    "finished_at": item.get("finished_at"),
                    "duration_sec": item.get("duration_sec"),
                    "enabled": item.get("enabled"),
                    "cost_hint": item.get("cost_hint"),
                }
                steps.append(step_row)
                if status == "failed" and failed_step is None:
                    failed_step = item.get("name")
                if status in {"pending", "running"}:
                    current_step = item.get("name")
    completed_steps = [item for item in steps if item.get("status") == "passed"]
    skipped_steps = [item for item in steps if item.get("status") == "skipped"]
    planned_steps = [item for item in steps if item.get("status") == "planned"]
    expected_outputs = expected_outputs_from_report(session, pipeline_report)
    missing_outputs = [item for item in expected_outputs if not item.get("exists")]
    status = str((pipeline_report or {}).get("status") or "unknown")
    pipeline_progress = pipeline_report.get("progress") if isinstance(pipeline_report, dict) else None
    asr_chunks = pipeline_progress.get("asr_chunks") if isinstance(pipeline_progress, dict) else None
    return {
        "schema": RUN_SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": {"name": "evaluate-outcome", "version": SCRIPT_VERSION},
        "session": str(session),
        "status": status,
        "outcome": outcome,
        "resume_command": f"murmurmark process {shell_path(session, Path.cwd())}",
        "next_command": next_command,
        "resume_granularity": "session_step_cache",
        "progress": {
            "steps_total": len(steps),
            "steps_passed": len(completed_steps),
            "steps_skipped": len(skipped_steps),
            "steps_planned": len(planned_steps),
            "steps_failed": 1 if failed_step else 0,
            "expected_outputs": len(expected_outputs),
            "missing_outputs": len(missing_outputs),
            "asr_chunks": asr_chunks,
        },
        "stuck_state": {
            "status": "failed" if failed_step else "incomplete" if status not in {"passed", "planned"} else "clear",
            "failed_step": failed_step,
            "current_step": current_step,
            "recommended_action": "inspect_failed_step_and_rerun" if failed_step else "rerun_process_if_outputs_missing"
            if missing_outputs and status != "planned"
            else "none",
        },
        "checkpoints": {
            "steps": steps,
            "expected_outputs": expected_outputs,
            "missing_outputs": missing_outputs,
        },
        "note": "Current resume reruns the pipeline shell, reuses existing derived caches where steps support it, and windowed ASR can resume from verified chunk cache with rebuild checks.",
        "steps": steps,
    }


def build_outcome_summary(
    *,
    outcome: str,
    export_status: str,
    next_command: str,
    readiness: dict[str, Any] | None,
    metrics: dict[str, Any],
    gates: list[dict[str, Any]],
    review_plan: dict[str, Any],
    outputs: dict[str, Any],
) -> dict[str, Any]:
    review_seconds = safe_float(metrics.get("review_burden_sec")) or 0.0
    transcript_review_seconds = safe_float(metrics.get("transcript_review_burden_sec"))
    if transcript_review_seconds is None:
        transcript_review_seconds = review_seconds
    lanes = review_plan.get("lanes") if isinstance(review_plan.get("lanes"), list) else []
    gate_counts = gate_summary(gates)
    export_blockers = []
    if isinstance(readiness, dict) and isinstance(readiness.get("export_blockers"), list):
        export_blockers = [str(item) for item in readiness.get("export_blockers") or []]
    if outcome == "ready_for_notes":
        headline = "ready_for_notes: notes can be read; export is allowed only when export_status is allowed"
    elif outcome == "review_first":
        headline = "review_first: transcript is useful, but a short review queue remains"
    elif outcome == "pipeline_failed":
        headline = "pipeline_failed: inspect the failed step, then rerun process"
    elif outcome == "partial":
        headline = "partial: pipeline outputs are incomplete; resume processing"
    else:
        headline = "blocked: do not use this session until the blocker is resolved"
    def output_path(key: str) -> str | None:
        item = outputs.get(key)
        if not isinstance(item, dict) or item.get("exists") is not True:
            return None
        path = item.get("path")
        return str(path) if path else None

    return {
        "headline": headline,
        "can_read_notes": outcome in {"ready_for_notes", "review_first"},
        "can_export": outcome == "ready_for_notes" and export_status == "allowed",
        "notes_path": output_path("notes"),
        "transcript_path": output_path("transcript"),
        "quality_verdict_path": output_path("quality_verdict"),
        "export_blockers": export_blockers,
        "review_burden_seconds": round(review_seconds, 3),
        "review_burden_minutes": round(review_seconds / 60.0, 3),
        "transcript_review_burden_seconds": round(transcript_review_seconds, 3),
        "transcript_review_burden_minutes": round(transcript_review_seconds / 60.0, 3),
        "open_review_lanes": len([lane for lane in lanes if isinstance(lane, dict) and lane.get("status") == "open"]),
        "first_review_lane": lanes[0].get("id") if lanes and isinstance(lanes[0], dict) else None,
        "next_command": next_command,
        "selected_profile": (readiness or {}).get("selected_profile"),
        "verdict": (readiness or {}).get("verdict"),
        "session_classification": (readiness or {}).get("session_classification"),
        "use_gate": (readiness or {}).get("use_gate"),
        "export_status": export_status,
        "gate_counts": gate_counts,
    }


def markdown(outcome_payload: dict[str, Any], review_plan: dict[str, Any]) -> str:
    lines = [
        "# MurmurMark Outcome",
        "",
        f"Outcome: `{outcome_payload['outcome']}`",
        f"Selected profile: `{outcome_payload.get('selected_profile')}`",
        f"Verdict: `{outcome_payload.get('verdict')}`",
        f"Next command: `{outcome_payload.get('next_command')}`",
        "",
        "## Summary",
        "",
    ]
    summary = outcome_payload.get("summary") if isinstance(outcome_payload.get("summary"), dict) else {}
    for key in (
        "headline",
        "can_read_notes",
        "can_export",
        "review_burden_minutes",
        "transcript_review_burden_minutes",
        "open_review_lanes",
        "first_review_lane",
        "notes_path",
        "transcript_path",
        "quality_verdict_path",
        "export_blockers",
    ):
        if key in summary:
            lines.append(f"- `{key}`: `{summary.get(key)}`")
    lines.extend(
        [
            "",
            "## Gates",
            "",
        ]
    )
    for gate in outcome_payload.get("gates", []):
        lines.append(
            f"- `{gate.get('id')}`: `{gate.get('status')}` / `{gate.get('severity')}` - {gate.get('message')}"
        )
    lines.extend(["", "## Review Plan", ""])
    lanes = review_plan.get("lanes") if isinstance(review_plan.get("lanes"), list) else []
    if lanes:
        for lane in lanes:
            lines.append(
                f"- `{lane.get('id')}`: {lane.get('reason')} "
                f"({lane.get('estimated_seconds')} sec), `{lane.get('recommended_command')}`"
            )
    else:
        lines.append("- no open lanes")
    lines.extend(["", "## Metrics", ""])
    for key, value in outcome_payload.get("metrics", {}).items():
        lines.append(f"- `{key}`: `{value}`")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    args = parse_args()
    session = args.session.expanduser()
    out_dir = args.out_dir.expanduser() if args.out_dir else session / "derived/outcome"
    pipeline_report_path = (
        args.pipeline_report.expanduser() if args.pipeline_report else session / "derived/pipeline-run/pipeline_run_report.json"
    )
    readiness_path = session / "derived/readiness/session_readiness.json"
    readiness = read_json(readiness_path)
    pipeline_report = read_json(pipeline_report_path)
    gates = evaluate_gates(session, readiness, pipeline_report)
    base_outcome = outcome_from_readiness(readiness, pipeline_report)
    outcome = outcome_from_gates(base_outcome, gates)
    review_plan = build_review_plan(session, readiness, outcome)
    next_command = (
        first_recommended_command(review_plan.get("lanes"))
        if outcome == "review_first"
        else None
    ) or (
        str((readiness or {}).get("recommended_next") or "").strip()
        or first_command((readiness or {}).get("next_commands"))
        or str((pipeline_report or {}).get("recommended_next") or "").strip()
        or first_command((pipeline_report or {}).get("next_commands"))
        or f"murmurmark process {shell_path(session, Path.cwd())}"
    )
    metrics = compact_metrics(merged_metrics(readiness, session))
    outputs = readiness.get("outputs") if isinstance(readiness, dict) and isinstance(readiness.get("outputs"), dict) else {}
    export_blockers = (readiness or {}).get("export_blockers") or []
    export_status = "allowed" if outcome == "ready_for_notes" and not export_blockers else "blocked_until_review"
    summary = build_outcome_summary(
        outcome=outcome,
        export_status=export_status,
        next_command=next_command,
        readiness=readiness,
        metrics=metrics,
        gates=gates,
        review_plan=review_plan,
        outputs=outputs,
    )
    payload = {
        "schema": OUTCOME_SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": {"name": "evaluate-outcome", "version": SCRIPT_VERSION},
        "session": str(session),
        "outcome": outcome,
        "base_outcome": base_outcome,
        "selected_profile": (readiness or {}).get("selected_profile"),
        "verdict": (readiness or {}).get("verdict"),
        "session_classification": (readiness or {}).get("session_classification"),
        "use_gate": (readiness or {}).get("use_gate"),
        "next_command": next_command,
        "export_status": export_status,
        "retention_status": "not_planned",
        "summary": summary,
        "risk_flags": (readiness or {}).get("risk_flags") or [],
        "review_blockers": (readiness or {}).get("review_blockers") or [],
        "export_blockers": (readiness or {}).get("export_blockers") or [],
        "metrics": metrics,
        "gates": gates,
        "gate_summary": gate_summary(gates),
        "readiness": {
            "transcript": "ready" if outcome == "ready_for_notes" and not export_blockers else "review_required"
            if outcome == "review_first"
            else outcome,
            "notes": "ready" if outcome == "ready_for_notes" else "review_required"
            if outcome == "review_first"
            else outcome,
            "export": "allowed" if outcome == "ready_for_notes" and not export_blockers else "blocked",
            "retention": "not_planned",
        },
        "inputs": {
            "session_readiness": rel(readiness_path, session) if readiness_path.exists() else None,
            "pipeline_report": rel(pipeline_report_path, session) if pipeline_report_path.exists() else None,
        },
        "outputs": outputs,
    }
    write_json(out_dir / "outcome.json", payload)
    write_json(out_dir / "review_plan.json", review_plan)
    (out_dir / "next_command.txt").write_text(next_command + "\n", encoding="utf-8")
    (out_dir / "outcome.md").write_text(markdown(payload, review_plan), encoding="utf-8")
    run_manifest = build_run_manifest(session, pipeline_report, outcome, next_command)
    write_json(session / "derived/run/pipeline_run.json", run_manifest)

    if args.print_summary:
        print(f"outcome: {outcome}")
        print(f"next: {next_command}")
        print(f"outcome_report: {out_dir / 'outcome.json'}")
        print(f"review_plan: {out_dir / 'review_plan.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
