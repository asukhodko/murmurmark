#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_VERSION = "0.5.0"
SCHEMA_REPORT = "murmurmark.corpus_gates_report/v1"
SCHEMA_BASELINE = "murmurmark.corpus_gates_baseline/v1"

DEFAULT_CORPUS_READINESS = {
    "partial_cleanup_regression_ready",
    "useful_for_audio_judge_v0",
    "broad_regression_ready",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check MurmurMark corpus no-regression gates.")
    parser.add_argument(
        "--session-quality",
        type=Path,
        default=Path("sessions/_reports/session-quality/session_quality_report.json"),
    )
    parser.add_argument(
        "--corpus-evaluation",
        type=Path,
        default=Path("sessions/_reports/regression-corpus/regression_corpus_evaluation.json"),
    )
    parser.add_argument(
        "--audio-judge",
        type=Path,
        default=Path("sessions/_reports/audio-judge-v0/audio_judge_v0_report.json"),
    )
    parser.add_argument(
        "--operational-readiness",
        type=Path,
        default=Path("sessions/_reports/operational-readiness/operational_readiness_report.json"),
    )
    parser.add_argument(
        "--transcript-order",
        type=Path,
        default=Path("sessions/_reports/transcript-order/transcript_order_corpus_report.json"),
    )
    parser.add_argument(
        "--local-recall",
        type=Path,
        default=Path("sessions/_reports/local-recall/local_recall_corpus_report.json"),
    )
    parser.add_argument(
        "--remote-leak-segment-corpus",
        type=Path,
        default=Path("sessions/_reports/remote-leak-segment/remote_leak_segment_corpus_report.json"),
    )
    parser.add_argument(
        "--asr-positive-echo-candidate",
        type=Path,
        default=Path(
            "sessions/_reports/asr-positive-echo-candidate/asr_positive_echo_candidate_corpus_report.json"
        ),
    )
    parser.add_argument("--out-dir", type=Path, default=Path("sessions/_reports/corpus-gates"))
    parser.add_argument("--min-complete-sessions", type=int, default=3)
    parser.add_argument("--min-ready-for-notes", type=int, default=1)
    parser.add_argument("--min-corpus-sessions", type=int, default=3)
    parser.add_argument("--min-corpus-items", type=int, default=40)
    parser.add_argument("--min-audio-judge-rows", type=int, default=40)
    parser.add_argument("--min-audio-judge-cv-accuracy", type=float, default=0.80)
    parser.add_argument("--min-local-recall", type=float, default=0.80)
    parser.add_argument("--max-total-review-burden-ratio", type=float, default=0.03)
    parser.add_argument("--max-session-review-burden-ratio", type=float, default=0.05)
    parser.add_argument("--max-operational-review-queue-items", type=int, default=80)
    parser.add_argument("--max-audio-judge-remaining-review-items", type=int, default=80)
    parser.add_argument("--baseline", type=Path, help="Compare current corpus metrics with a saved baseline.")
    parser.add_argument("--write-baseline", type=Path, help="Write a baseline snapshot from the current inputs.")
    parser.add_argument("--max-complete-sessions-drop", type=int, default=0)
    parser.add_argument("--max-ready-for-notes-drop", type=int, default=0)
    parser.add_argument("--max-review-first-increase", type=int, default=0)
    parser.add_argument("--max-corpus-items-drop", type=int, default=0)
    parser.add_argument("--max-audio-judge-rows-drop", type=int, default=0)
    parser.add_argument("--max-audio-judge-cv-accuracy-drop", type=float, default=0.03)
    parser.add_argument("--max-total-review-burden-ratio-increase", type=float, default=0.005)
    parser.add_argument("--max-session-review-burden-ratio-increase", type=float, default=0.01)
    parser.add_argument("--max-local-recall-drop", type=float, default=0.05)
    parser.add_argument("--max-audio-judge-review-queue-increase", type=int, default=10)
    parser.add_argument("--max-operational-review-queue-increase", type=int, default=10)
    parser.add_argument("--max-local-recall-complete-blocking-increase", type=int, default=0)
    parser.add_argument("--max-local-recall-possible-lost-sec-increase", type=float, default=0.0)
    parser.add_argument("--max-remote-leak-protected-items-increase", type=int, default=0)
    parser.add_argument("--max-remote-leak-protected-sec-increase", type=float, default=0.5)
    parser.add_argument("--allowed-corpus-readiness", default=",".join(sorted(DEFAULT_CORPUS_READINESS)))
    parser.add_argument("--strict-warnings", action="store_true", help="Treat warnings as failures.")
    parser.add_argument("--no-fail", action="store_true", help="Always exit 0 after writing the report.")
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def status_for(ok: bool, severity: str) -> str:
    if ok:
        return "pass"
    return "fail" if severity == "fail" else "warn"


def check(
    checks: list[dict[str, Any]],
    check_id: str,
    ok: bool,
    *,
    severity: str = "fail",
    observed: Any = None,
    threshold: Any = None,
    message: str,
    details: Any = None,
) -> None:
    checks.append(
        {
            "id": check_id,
            "severity": severity,
            "status": status_for(ok, severity),
            "observed": observed,
            "threshold": threshold,
            "message": message,
            "details": details,
        }
    )


def complete_sessions(session_quality: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not session_quality:
        return []
    sessions = session_quality.get("sessions")
    if not isinstance(sessions, list):
        return []
    return [item for item in sessions if isinstance(item, dict) and item.get("pipeline_status") == "complete"]


def session_ids(rows: list[dict[str, Any]]) -> list[str]:
    return [str(row.get("session_id") or row.get("label") or "?") for row in rows]


def operational_excluded_session_ids(operational: dict[str, Any] | None) -> set[str]:
    if not isinstance(operational, dict):
        return set()
    summary = operational.get("summary") if isinstance(operational.get("summary"), dict) else {}
    values = summary.get("excluded_diagnostic_sessions")
    if not isinstance(values, list):
        return set()
    return {str(value) for value in values if value is not None and str(value)}


def report_sessions(report: dict[str, Any] | None) -> list[dict[str, Any]]:
    rows = report.get("sessions") if isinstance(report, dict) else []
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def session_rank(use_gate: Any) -> int:
    return {
        "ready_for_notes": 3,
        "review_first": 2,
        "pipeline_incomplete_review_first": 1,
        "pipeline_incomplete": 0,
    }.get(str(use_gate or ""), 0)


def build_baseline_snapshot(
    *,
    args: argparse.Namespace,
    complete: list[dict[str, Any]],
    ready_for_notes: int,
    review_first: int,
    incomplete: int,
    total_review_burden_ratio: float,
    corpus_item_count: int,
    corpus_session_count: int,
    corpus_readiness: str,
    audio_rows: int,
    audio_sessions: int,
    cv_accuracy: float,
    audio_remaining: int,
    operational_verdict: str,
    operational_queue: int,
    local_recall_missing_audit_count: int,
    local_recall_complete_blocking: int,
    local_recall_possible_lost_me_seconds: float,
    local_recall_needs_review_seconds: float,
    remote_leak_item_count: int,
    remote_leak_missing_plan_count: int,
    remote_leak_protect_local_content_items: int,
    remote_leak_protect_local_content_seconds: float,
    suggested_closure_generated_rows: int,
    suggested_closure_generated_seconds: float,
    suggested_closure_actionable_rows: int,
    suggested_closure_actionable_seconds: float,
    suggested_closure_needs_review_rows: int,
    suggested_closure_needs_review_seconds: float,
    suggested_closure_todo_rows: int,
    suggested_closure_todo_seconds: float,
    suggested_closure_auto_rows: int,
    suggested_closure_auto_seconds: float,
    suggested_closure_auto_keep_rows: int,
    suggested_closure_auto_keep_seconds: float,
    suggested_closure_auto_drop_rows: int,
    suggested_closure_auto_drop_seconds: float,
    suggested_closure_auto_review_rows: int,
    suggested_closure_auto_review_seconds: float,
    suggested_closure_manual_remaining_rows: int,
    suggested_closure_manual_remaining_seconds: float,
) -> dict[str, Any]:
    sessions: dict[str, dict[str, Any]] = {}
    for row in complete:
        session_id = str(row.get("session_id") or row.get("label") or "")
        if not session_id:
            continue
        sessions[session_id] = {
            "session_id": session_id,
            "selected_profile": row.get("selected_profile"),
            "use_gate": row.get("use_gate"),
            "review_burden_ratio": round(safe_float(row.get("review_burden_ratio")), 6),
            "review_burden_sec": round(safe_float(row.get("review_burden_sec")), 3),
            "meeting_duration_sec": round(safe_float(row.get("meeting_duration_sec")), 3),
            "local_only_island_recall": (
                round(safe_float(row.get("local_only_island_recall")), 6)
                if row.get("local_only_island_recall") is not None
                else None
            ),
            "unrepaired_long_mic_crossings_count": safe_int(row.get("unrepaired_long_mic_crossings_count")),
            "golden_phrase_fail_count": safe_int(row.get("golden_phrase_fail_count")),
            "transcript_order_probable_order_risk_count": safe_int(row.get("transcript_order_probable_order_risk_count")),
            "transcript_order_needs_review_count": safe_int(row.get("transcript_order_needs_review_count")),
        }
    return {
        "schema": SCHEMA_BASELINE,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": {"name": "check-corpus-gates", "version": SCRIPT_VERSION},
        "source_inputs": {
            "session_quality": str(args.session_quality),
            "corpus_evaluation": str(args.corpus_evaluation),
            "audio_judge": str(args.audio_judge),
            "operational_readiness": str(args.operational_readiness),
            "transcript_order": str(args.transcript_order),
            "local_recall": str(args.local_recall),
            "remote_leak_segment_corpus": str(args.remote_leak_segment_corpus),
        },
        "metrics": {
            "complete_pipeline_count": len(complete),
            "ready_for_notes": ready_for_notes,
            "review_first": review_first,
            "incomplete_sessions": incomplete,
            "total_review_burden_ratio": round(total_review_burden_ratio, 6),
            "corpus_readiness": corpus_readiness or None,
            "corpus_item_count": corpus_item_count,
            "corpus_session_count": corpus_session_count,
            "audio_judge_rows": audio_rows,
            "audio_judge_sessions": audio_sessions,
            "audio_judge_cv_accuracy": round(cv_accuracy, 6),
            "audio_judge_remaining_review_items": audio_remaining,
            "operational_verdict": operational_verdict or None,
            "operational_review_queue_items": operational_queue,
            "local_recall_missing_audit_count": local_recall_missing_audit_count,
            "local_recall_complete_blocking_sessions": local_recall_complete_blocking,
            "local_recall_possible_lost_me_seconds": round(local_recall_possible_lost_me_seconds, 3),
            "local_recall_needs_review_seconds": round(local_recall_needs_review_seconds, 3),
            "remote_leak_segment_item_count": remote_leak_item_count,
            "remote_leak_segment_missing_plan_count": remote_leak_missing_plan_count,
            "remote_leak_segment_protect_local_content_items": remote_leak_protect_local_content_items,
            "remote_leak_segment_protect_local_content_seconds": round(remote_leak_protect_local_content_seconds, 3),
            "suggested_closure_generated_rows": suggested_closure_generated_rows,
            "suggested_closure_generated_seconds": round(suggested_closure_generated_seconds, 3),
            "suggested_closure_actionable_rows": suggested_closure_actionable_rows,
            "suggested_closure_actionable_seconds": round(suggested_closure_actionable_seconds, 3),
            "suggested_closure_needs_review_rows": suggested_closure_needs_review_rows,
            "suggested_closure_needs_review_seconds": round(suggested_closure_needs_review_seconds, 3),
            "suggested_closure_todo_rows": suggested_closure_todo_rows,
            "suggested_closure_todo_seconds": round(suggested_closure_todo_seconds, 3),
            "suggested_closure_auto_rows": suggested_closure_auto_rows,
            "suggested_closure_auto_seconds": round(suggested_closure_auto_seconds, 3),
            "suggested_closure_auto_keep_rows": suggested_closure_auto_keep_rows,
            "suggested_closure_auto_keep_seconds": round(suggested_closure_auto_keep_seconds, 3),
            "suggested_closure_auto_drop_rows": suggested_closure_auto_drop_rows,
            "suggested_closure_auto_drop_seconds": round(suggested_closure_auto_drop_seconds, 3),
            "suggested_closure_auto_review_rows": suggested_closure_auto_review_rows,
            "suggested_closure_auto_review_seconds": round(suggested_closure_auto_review_seconds, 3),
            "suggested_closure_manual_remaining_rows": suggested_closure_manual_remaining_rows,
            "suggested_closure_manual_remaining_seconds": round(suggested_closure_manual_remaining_seconds, 3),
        },
        "sessions": sessions,
    }


def compare_baseline(
    checks: list[dict[str, Any]],
    *,
    baseline: dict[str, Any] | None,
    current: dict[str, Any],
    args: argparse.Namespace,
) -> None:
    if args.baseline is None:
        return
    check(
        checks,
        "baseline.valid",
        isinstance(baseline, dict) and baseline.get("schema") == SCHEMA_BASELINE,
        observed=str(args.baseline),
        threshold=SCHEMA_BASELINE,
        message="baseline snapshot exists and uses the expected schema",
    )
    if not isinstance(baseline, dict) or baseline.get("schema") != SCHEMA_BASELINE:
        return

    baseline_metrics = baseline.get("metrics") if isinstance(baseline.get("metrics"), dict) else {}
    current_metrics = current.get("metrics") if isinstance(current.get("metrics"), dict) else {}

    def int_metric(metric: str) -> tuple[int, int]:
        return safe_int(current_metrics.get(metric)), safe_int(baseline_metrics.get(metric))

    def float_metric(metric: str) -> tuple[float, float]:
        return safe_float(current_metrics.get(metric)), safe_float(baseline_metrics.get(metric))

    current_complete, baseline_complete = int_metric("complete_pipeline_count")
    check(
        checks,
        "baseline.complete_sessions_not_lower",
        current_complete >= baseline_complete - args.max_complete_sessions_drop,
        observed=current_complete,
        threshold=f">= baseline {baseline_complete} - {args.max_complete_sessions_drop}",
        message="complete pipeline session count does not regress from baseline",
    )
    current_ready, baseline_ready = int_metric("ready_for_notes")
    check(
        checks,
        "baseline.ready_for_notes_not_lower",
        current_ready >= baseline_ready - args.max_ready_for_notes_drop,
        observed=current_ready,
        threshold=f">= baseline {baseline_ready} - {args.max_ready_for_notes_drop}",
        message="ready_for_notes session count does not regress from baseline",
    )
    current_review_first, baseline_review_first = int_metric("review_first")
    check(
        checks,
        "baseline.review_first_not_higher",
        current_review_first <= baseline_review_first + args.max_review_first_increase,
        observed=current_review_first,
        threshold=f"<= baseline {baseline_review_first} + {args.max_review_first_increase}",
        message="review_first session count does not grow from baseline",
    )
    current_corpus_items, baseline_corpus_items = int_metric("corpus_item_count")
    check(
        checks,
        "baseline.corpus_items_not_lower",
        current_corpus_items >= baseline_corpus_items - args.max_corpus_items_drop,
        observed=current_corpus_items,
        threshold=f">= baseline {baseline_corpus_items} - {args.max_corpus_items_drop}",
        message="regression corpus item count does not regress from baseline",
    )
    current_audio_rows, baseline_audio_rows = int_metric("audio_judge_rows")
    check(
        checks,
        "baseline.audio_judge_rows_not_lower",
        current_audio_rows >= baseline_audio_rows - args.max_audio_judge_rows_drop,
        observed=current_audio_rows,
        threshold=f">= baseline {baseline_audio_rows} - {args.max_audio_judge_rows_drop}",
        message="audio judge training row count does not regress from baseline",
    )
    current_cv, baseline_cv = float_metric("audio_judge_cv_accuracy")
    check(
        checks,
        "baseline.audio_judge_cv_not_lower",
        current_cv >= baseline_cv - args.max_audio_judge_cv_accuracy_drop,
        observed=round(current_cv, 6),
        threshold=f">= baseline {round(baseline_cv, 6)} - {args.max_audio_judge_cv_accuracy_drop}",
        message="audio judge validation accuracy does not drop beyond the allowed budget",
    )
    current_review_ratio, baseline_review_ratio = float_metric("total_review_burden_ratio")
    check(
        checks,
        "baseline.total_review_burden_not_higher",
        current_review_ratio <= baseline_review_ratio + args.max_total_review_burden_ratio_increase,
        observed=round(current_review_ratio, 6),
        threshold=f"<= baseline {round(baseline_review_ratio, 6)} + {args.max_total_review_burden_ratio_increase}",
        message="total review burden does not grow beyond the allowed budget",
    )
    current_audio_queue, baseline_audio_queue = int_metric("audio_judge_remaining_review_items")
    check(
        checks,
        "baseline.audio_judge_queue_not_higher",
        current_audio_queue <= baseline_audio_queue + args.max_audio_judge_review_queue_increase,
        observed=current_audio_queue,
        threshold=f"<= baseline {baseline_audio_queue} + {args.max_audio_judge_review_queue_increase}",
        message="audio judge review queue does not grow beyond the allowed budget",
    )
    current_operational_queue, baseline_operational_queue = int_metric("operational_review_queue_items")
    check(
        checks,
        "baseline.operational_queue_not_higher",
        current_operational_queue <= baseline_operational_queue + args.max_operational_review_queue_increase,
        observed=current_operational_queue,
        threshold=f"<= baseline {baseline_operational_queue} + {args.max_operational_review_queue_increase}",
        message="operational review queue does not grow beyond the allowed budget",
    )
    current_local_blocking, baseline_local_blocking = int_metric("local_recall_complete_blocking_sessions")
    check(
        checks,
        "baseline.local_recall_complete_blocking_not_higher",
        current_local_blocking <= baseline_local_blocking + args.max_local_recall_complete_blocking_increase,
        observed=current_local_blocking,
        threshold=f"<= baseline {baseline_local_blocking} + {args.max_local_recall_complete_blocking_increase}",
        message="complete-session local-recall blocker count does not grow from baseline",
    )
    current_local_lost, baseline_local_lost = float_metric("local_recall_possible_lost_me_seconds")
    check(
        checks,
        "baseline.local_recall_possible_lost_not_higher",
        current_local_lost <= baseline_local_lost + args.max_local_recall_possible_lost_sec_increase,
        observed=round(current_local_lost, 3),
        threshold=f"<= baseline {round(baseline_local_lost, 3)} + {args.max_local_recall_possible_lost_sec_increase}",
        message="possible lost-Me seconds do not grow from baseline",
    )
    current_remote_protected, baseline_remote_protected = int_metric("remote_leak_segment_protect_local_content_items")
    check(
        checks,
        "baseline.remote_leak_protected_items_not_higher",
        current_remote_protected <= baseline_remote_protected + args.max_remote_leak_protected_items_increase,
        severity="warn",
        observed=current_remote_protected,
        threshold=f"<= baseline {baseline_remote_protected} + {args.max_remote_leak_protected_items_increase}",
        message="remote-leak protected-local-content queue does not grow from baseline",
    )
    current_remote_protected_sec, baseline_remote_protected_sec = float_metric("remote_leak_segment_protect_local_content_seconds")
    check(
        checks,
        "baseline.remote_leak_protected_seconds_not_higher",
        current_remote_protected_sec <= baseline_remote_protected_sec + args.max_remote_leak_protected_sec_increase,
        severity="warn",
        observed=round(current_remote_protected_sec, 3),
        threshold=f"<= baseline {round(baseline_remote_protected_sec, 3)} + {args.max_remote_leak_protected_sec_increase}",
        message="remote-leak protected-local-content seconds do not grow from baseline",
    )

    baseline_sessions = baseline.get("sessions") if isinstance(baseline.get("sessions"), dict) else {}
    current_sessions = current.get("sessions") if isinstance(current.get("sessions"), dict) else {}
    missing_session_ids = sorted(str(key) for key in baseline_sessions if key not in current_sessions)
    check(
        checks,
        "baseline.sessions_still_complete",
        not missing_session_ids,
        observed=len(missing_session_ids),
        threshold="0 missing baseline sessions",
        message="sessions present in the baseline still have complete pipeline reports",
        details=missing_session_ids[:20],
    )

    use_gate_bad: list[str] = []
    local_recall_bad: list[str] = []
    review_burden_bad: list[str] = []
    hard_invariant_bad: list[str] = []
    for session_id, base_row in baseline_sessions.items():
        cur_row = current_sessions.get(session_id)
        if not isinstance(base_row, dict) or not isinstance(cur_row, dict):
            continue
        if session_rank(cur_row.get("use_gate")) < session_rank(base_row.get("use_gate")):
            use_gate_bad.append(str(session_id))
        base_recall = base_row.get("local_only_island_recall")
        cur_recall = cur_row.get("local_only_island_recall")
        if base_recall is not None and cur_recall is not None:
            if safe_float(cur_recall) < safe_float(base_recall) - args.max_local_recall_drop:
                local_recall_bad.append(str(session_id))
        if safe_float(cur_row.get("review_burden_ratio")) > safe_float(base_row.get("review_burden_ratio")) + args.max_session_review_burden_ratio_increase:
            review_burden_bad.append(str(session_id))
        if safe_int(cur_row.get("unrepaired_long_mic_crossings_count")) > safe_int(base_row.get("unrepaired_long_mic_crossings_count")):
            hard_invariant_bad.append(str(session_id))
        if safe_int(cur_row.get("golden_phrase_fail_count")) > safe_int(base_row.get("golden_phrase_fail_count")):
            hard_invariant_bad.append(str(session_id))
        if safe_int(cur_row.get("transcript_order_probable_order_risk_count")) > safe_int(
            base_row.get("transcript_order_probable_order_risk_count")
        ):
            hard_invariant_bad.append(str(session_id))
    check(
        checks,
        "baseline.session_use_gate_not_worse",
        not use_gate_bad,
        observed=len(use_gate_bad),
        threshold="0 sessions",
        message="no baseline session has a worse use gate",
        details=use_gate_bad[:20],
    )
    check(
        checks,
        "baseline.session_local_recall_not_lower",
        not local_recall_bad,
        observed=len(local_recall_bad),
        threshold=f"0 sessions below baseline - {args.max_local_recall_drop}",
        message="no baseline session loses too much local recall",
        details=local_recall_bad[:20],
    )
    check(
        checks,
        "baseline.session_review_burden_not_higher",
        not review_burden_bad,
        observed=len(review_burden_bad),
        threshold=f"0 sessions above baseline + {args.max_session_review_burden_ratio_increase}",
        message="no baseline session has a large review burden increase",
        details=review_burden_bad[:20],
    )
    check(
        checks,
        "baseline.session_hard_invariants_not_worse",
        not hard_invariant_bad,
        observed=len(set(hard_invariant_bad)),
        threshold="0 sessions",
        message="no baseline session regresses on golden phrases, unrepaired long crossings or transcript order risks",
        details=sorted(set(hard_invariant_bad))[:20],
    )


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    allowed_readiness = {item.strip() for item in str(args.allowed_corpus_readiness).split(",") if item.strip()}
    session_quality = read_json(args.session_quality)
    corpus = read_json(args.corpus_evaluation)
    audio_judge = read_json(args.audio_judge)
    operational = read_json(args.operational_readiness)
    transcript_order = read_json(args.transcript_order)
    local_recall = read_json(args.local_recall)
    remote_leak_segment = read_json(args.remote_leak_segment_corpus)
    asr_positive_echo_candidate = read_json(args.asr_positive_echo_candidate)

    checks: list[dict[str, Any]] = []
    inputs = {
        "session_quality": str(args.session_quality),
        "corpus_evaluation": str(args.corpus_evaluation),
        "audio_judge": str(args.audio_judge),
        "operational_readiness": str(args.operational_readiness),
        "transcript_order": str(args.transcript_order),
        "local_recall": str(args.local_recall),
        "remote_leak_segment_corpus": str(args.remote_leak_segment_corpus),
        "asr_positive_echo_candidate": str(args.asr_positive_echo_candidate),
    }
    thresholds = {
        "min_complete_sessions": args.min_complete_sessions,
        "min_ready_for_notes": args.min_ready_for_notes,
        "min_corpus_sessions": args.min_corpus_sessions,
        "min_corpus_items": args.min_corpus_items,
        "min_audio_judge_rows": args.min_audio_judge_rows,
        "min_audio_judge_cv_accuracy": args.min_audio_judge_cv_accuracy,
        "min_local_recall": args.min_local_recall,
        "max_total_review_burden_ratio": args.max_total_review_burden_ratio,
        "max_session_review_burden_ratio": args.max_session_review_burden_ratio,
        "max_operational_review_queue_items": args.max_operational_review_queue_items,
        "max_audio_judge_remaining_review_items": args.max_audio_judge_remaining_review_items,
        "max_complete_sessions_drop": args.max_complete_sessions_drop,
        "max_ready_for_notes_drop": args.max_ready_for_notes_drop,
        "max_review_first_increase": args.max_review_first_increase,
        "max_corpus_items_drop": args.max_corpus_items_drop,
        "max_audio_judge_rows_drop": args.max_audio_judge_rows_drop,
        "max_audio_judge_cv_accuracy_drop": args.max_audio_judge_cv_accuracy_drop,
        "max_total_review_burden_ratio_increase": args.max_total_review_burden_ratio_increase,
        "max_session_review_burden_ratio_increase": args.max_session_review_burden_ratio_increase,
        "max_local_recall_drop": args.max_local_recall_drop,
        "max_audio_judge_review_queue_increase": args.max_audio_judge_review_queue_increase,
        "max_operational_review_queue_increase": args.max_operational_review_queue_increase,
        "max_local_recall_complete_blocking_increase": args.max_local_recall_complete_blocking_increase,
        "max_local_recall_possible_lost_sec_increase": args.max_local_recall_possible_lost_sec_increase,
        "max_remote_leak_protected_items_increase": args.max_remote_leak_protected_items_increase,
        "max_remote_leak_protected_sec_increase": args.max_remote_leak_protected_sec_increase,
        "allowed_corpus_readiness": sorted(allowed_readiness),
    }

    required_inputs = {
        key: path
        for key, path in inputs.items()
        if key not in {"local_recall", "remote_leak_segment_corpus", "asr_positive_echo_candidate"}
    }
    for key, path in required_inputs.items():
        check(
            checks,
            f"input.{key}",
            read_json(Path(path)) is not None,
            observed=path,
            threshold="valid JSON object",
            message=f"{key} report exists and is valid JSON",
        )
    check(
        checks,
        "input.remote_leak_segment_corpus",
        remote_leak_segment is not None,
        severity="warn",
        observed=str(args.remote_leak_segment_corpus),
        threshold="valid JSON object",
        message="remote-leak segment corpus report exists; corpus process rebuilds it automatically",
    )
    check(
        checks,
        "input.local_recall",
        local_recall is not None,
        severity="warn",
        observed=str(args.local_recall),
        threshold="valid JSON object",
        message="local-recall corpus report exists; corpus process rebuilds it automatically",
    )
    check(
        checks,
        "input.asr_positive_echo_candidate",
        asr_positive_echo_candidate is not None,
        severity="warn",
        observed=str(args.asr_positive_echo_candidate),
        threshold="valid JSON object",
        message="ASR-positive Echo candidate corpus report exists for the experimental echo profile",
    )

    excluded_session_ids = operational_excluded_session_ids(operational)
    complete = complete_sessions(session_quality)
    scoped_complete = [
        row
        for row in complete
        if str(row.get("session_id") or row.get("label") or "") not in excluded_session_ids
    ]
    ready_scoped_complete = [row for row in scoped_complete if str(row.get("use_gate") or "") == "ready_for_notes"]
    summary = session_quality.get("summary") if isinstance(session_quality, dict) else {}
    summary = summary if isinstance(summary, dict) else {}
    use_gates = summary.get("use_gates") if isinstance(summary.get("use_gates"), dict) else {}
    ready_for_notes = safe_int(use_gates.get("ready_for_notes"))
    review_first = safe_int(use_gates.get("review_first"))
    if not use_gates:
        ready_for_notes = sum(1 for row in complete if row.get("use_gate") == "ready_for_notes")
        review_first = sum(1 for row in complete if row.get("use_gate") == "review_first")
    incomplete = safe_int(summary.get("partial_or_incomplete_count"))
    total_review_burden_ratio = safe_float(summary.get("total_review_burden_ratio"), default=-1.0)
    if total_review_burden_ratio < 0:
        total_review_sec = sum(safe_float(row.get("review_burden_sec")) for row in complete)
        total_duration_sec = sum(safe_float(row.get("meeting_duration_sec")) for row in complete)
        total_review_burden_ratio = total_review_sec / total_duration_sec if total_duration_sec > 0 else 0.0
    suggested_closure_generated_rows = safe_int(summary.get("suggested_closure_generated_rows"))
    suggested_closure_generated_seconds = safe_float(summary.get("suggested_closure_generated_seconds"))
    suggested_closure_actionable_rows = safe_int(summary.get("suggested_closure_actionable_rows"))
    suggested_closure_actionable_seconds = safe_float(summary.get("suggested_closure_actionable_seconds"))
    suggested_closure_needs_review_rows = safe_int(summary.get("suggested_closure_needs_review_rows"))
    suggested_closure_needs_review_seconds = safe_float(summary.get("suggested_closure_needs_review_seconds"))
    suggested_closure_todo_rows = safe_int(summary.get("suggested_closure_todo_rows"))
    suggested_closure_todo_seconds = safe_float(summary.get("suggested_closure_todo_seconds"))
    suggested_closure_auto_rows = safe_int(summary.get("suggested_closure_auto_rows"))
    suggested_closure_auto_seconds = safe_float(summary.get("suggested_closure_auto_seconds"))
    suggested_closure_auto_keep_rows = safe_int(summary.get("suggested_closure_auto_keep_rows"))
    suggested_closure_auto_keep_seconds = safe_float(summary.get("suggested_closure_auto_keep_seconds"))
    suggested_closure_auto_drop_rows = safe_int(summary.get("suggested_closure_auto_drop_rows"))
    suggested_closure_auto_drop_seconds = safe_float(summary.get("suggested_closure_auto_drop_seconds"))
    suggested_closure_auto_review_rows = safe_int(summary.get("suggested_closure_auto_review_rows"))
    suggested_closure_auto_review_seconds = safe_float(summary.get("suggested_closure_auto_review_seconds"))
    suggested_closure_manual_remaining_rows = safe_int(summary.get("suggested_closure_manual_remaining_rows"))
    suggested_closure_manual_remaining_seconds = safe_float(summary.get("suggested_closure_manual_remaining_seconds"))

    check(
        checks,
        "session_quality.complete_sessions",
        len(complete) >= args.min_complete_sessions,
        observed=len(complete),
        threshold=f">= {args.min_complete_sessions}",
        message="enough complete pipeline sessions for regression gates",
    )
    check(
        checks,
        "session_quality.ready_for_notes",
        ready_for_notes >= args.min_ready_for_notes,
        observed=ready_for_notes,
        threshold=f">= {args.min_ready_for_notes}",
        message="at least some complete sessions are ready for notes",
    )
    check(
        checks,
        "session_quality.incomplete_sessions",
        incomplete == 0,
        severity="warn",
        observed=incomplete,
        threshold="0",
        message="incomplete historical sessions are excluded from hard transcript gates",
    )
    check(
        checks,
        "session_quality.review_first_sessions",
        review_first == 0,
        severity="warn",
        observed=review_first,
        threshold="0",
        message="some complete sessions still require review before use",
    )
    check(
        checks,
        "session_quality.total_review_burden_ratio",
        total_review_burden_ratio <= args.max_total_review_burden_ratio,
        observed=round(total_review_burden_ratio, 6),
        threshold=f"<= {args.max_total_review_burden_ratio}",
        message="total review burden stays within the medium-risk budget",
    )

    long_crossing_bad = [
        row for row in scoped_complete if safe_int(row.get("unrepaired_long_mic_crossings_count")) > 0
    ]
    golden_bad = [row for row in scoped_complete if safe_int(row.get("golden_phrase_fail_count")) > 0]
    transcript_order_bad = [
        row
        for row in scoped_complete
        if row.get("transcript_order_blocking_order_risk") is True
        or safe_float(row.get("transcript_order_probable_order_risk_seconds")) > 0
    ]
    local_recall_bad = [
        row for row in ready_scoped_complete if safe_float(row.get("local_only_island_recall"), 1.0) < args.min_local_recall
    ]
    local_recall_selected_blocking = [
        row
        for row in scoped_complete
        if safe_float(row.get("local_recall_possible_lost_me_seconds")) > 0
    ]
    local_recall_selected_review = [
        row
        for row in scoped_complete
        if safe_float(row.get("local_recall_needs_review_seconds")) > 0
    ]
    session_review_bad = [
        row
        for row in ready_scoped_complete
        if safe_float(row.get("review_burden_ratio")) > args.max_session_review_burden_ratio
    ]
    check(
        checks,
        "transcript.no_unrepaired_long_crossings",
        not long_crossing_bad,
        observed=len(long_crossing_bad),
        threshold="0 sessions",
        message="no selected operational session has unrepaired long mic crossings",
        details=session_ids(long_crossing_bad),
    )
    check(
        checks,
        "transcript.no_golden_failures",
        not golden_bad,
        observed=len(golden_bad),
        threshold="0 sessions",
        message="no selected operational session has failed golden phrase checks",
        details=session_ids(golden_bad),
    )
    check(
        checks,
        "transcript.no_blocking_order_risk",
        not transcript_order_bad,
        observed=len(transcript_order_bad),
        threshold="0 sessions",
        message="no selected operational session has blocking transcript order risk",
        details=session_ids(transcript_order_bad),
    )
    check(
        checks,
        "transcript.local_recall_floor",
        not local_recall_bad,
        observed=len(local_recall_bad),
        threshold=f"all ready_for_notes operational sessions >= {args.min_local_recall}",
        message="local recall does not fall below the configured floor for ready-for-notes sessions",
        details=session_ids(local_recall_bad),
    )
    check(
        checks,
        "transcript.no_selected_local_recall_blockers",
        not local_recall_selected_blocking,
        observed=len(local_recall_selected_blocking),
        threshold="0 sessions",
        message="no selected operational session has possible lost-Me local-recall blockers",
        details=session_ids(local_recall_selected_blocking),
    )
    check(
        checks,
        "transcript.selected_local_recall_review_items",
        not local_recall_selected_review,
        severity="warn",
        observed=len(local_recall_selected_review),
        threshold="0 sessions with local-recall review items",
        message="selected operational sessions still have non-blocking local-recall review items",
        details=session_ids(local_recall_selected_review),
    )
    check(
        checks,
        "transcript.session_review_burden_ratio",
        not session_review_bad,
        observed=len(session_review_bad),
        threshold=f"all ready_for_notes operational sessions <= {args.max_session_review_burden_ratio}",
        message="no ready-for-notes operational session has excessive review burden",
        details=session_ids(session_review_bad),
    )

    corpus_item_count = safe_int(corpus.get("item_count") if corpus else None)
    corpus_session_count = safe_int(corpus.get("session_count") if corpus else None)
    corpus_readiness = str(corpus.get("readiness") if corpus else "")
    missing_labels = corpus.get("missing_labels") if isinstance(corpus, dict) and isinstance(corpus.get("missing_labels"), list) else []
    check(
        checks,
        "corpus.min_sessions",
        corpus_session_count >= args.min_corpus_sessions,
        observed=corpus_session_count,
        threshold=f">= {args.min_corpus_sessions}",
        message="regression corpus covers enough sessions",
    )
    check(
        checks,
        "corpus.min_items",
        corpus_item_count >= args.min_corpus_items,
        observed=corpus_item_count,
        threshold=f">= {args.min_corpus_items}",
        message="regression corpus has enough examples",
    )
    check(
        checks,
        "corpus.readiness",
        corpus_readiness in allowed_readiness,
        observed=corpus_readiness,
        threshold=sorted(allowed_readiness),
        message="regression corpus readiness is sufficient",
    )
    check(
        checks,
        "corpus.labels_complete",
        not missing_labels,
        observed=missing_labels,
        threshold="[]",
        message="regression corpus contains all expected labels",
    )

    training = audio_judge.get("training") if isinstance(audio_judge, dict) else {}
    evaluation = audio_judge.get("evaluation") if isinstance(audio_judge, dict) else {}
    queue = audio_judge.get("review_queue") if isinstance(audio_judge, dict) else {}
    training = training if isinstance(training, dict) else {}
    evaluation = evaluation if isinstance(evaluation, dict) else {}
    queue = queue if isinstance(queue, dict) else {}
    audio_rows = safe_int(training.get("rows"))
    audio_sessions = safe_int(training.get("sessions"))
    cv_accuracy = safe_float(evaluation.get("cv_accuracy"))
    audio_remaining = safe_int(queue.get("remaining_human_review_items"))
    check(
        checks,
        "audio_judge.min_rows",
        audio_rows >= args.min_audio_judge_rows,
        observed=audio_rows,
        threshold=f">= {args.min_audio_judge_rows}",
        message="audio judge has enough training rows",
    )
    check(
        checks,
        "audio_judge.cv_accuracy",
        cv_accuracy >= args.min_audio_judge_cv_accuracy,
        observed=round(cv_accuracy, 6),
        threshold=f">= {args.min_audio_judge_cv_accuracy}",
        message="audio judge cross-validation accuracy stays above the floor",
    )
    check(
        checks,
        "audio_judge.remaining_review_items",
        audio_remaining <= args.max_audio_judge_remaining_review_items,
        observed=audio_remaining,
        threshold=f"<= {args.max_audio_judge_remaining_review_items}",
        message="audio judge remaining review queue stays bounded",
    )

    operational_summary = operational.get("summary") if isinstance(operational, dict) else {}
    operational_summary = operational_summary if isinstance(operational_summary, dict) else {}
    operational_verdict = str(operational.get("operational_verdict") if isinstance(operational, dict) else "")
    operational_queue = safe_int(operational_summary.get("review_queue_items"))
    operational_blockers = operational.get("blockers") if isinstance(operational, dict) and isinstance(operational.get("blockers"), list) else []
    operational_warnings = operational.get("warnings") if isinstance(operational, dict) and isinstance(operational.get("warnings"), list) else []
    order_summary = transcript_order.get("summary") if isinstance(transcript_order, dict) else {}
    order_summary = order_summary if isinstance(order_summary, dict) else {}
    order_missing_audits = safe_int(order_summary.get("missing_order_audit_count"))
    order_complete_blocking = safe_int(order_summary.get("complete_blocking_session_count"))
    order_audited_sessions = safe_int(order_summary.get("audited_session_count"))
    order_session_count = safe_int(order_summary.get("session_count"))
    order_report_blocking_rows = [
        row
        for row in report_sessions(transcript_order)
        if row.get("pipeline_status") == "complete" and row.get("blocking_order_risk") is True
    ]
    transcript_order_bad_ids = set(session_ids(transcript_order_bad))
    order_report_scoped_blocking = [
        row
        for row in order_report_blocking_rows
        if str(row.get("session_id") or "") not in excluded_session_ids
        and (
            str(row.get("session_id") or "") in transcript_order_bad_ids
            or str(row.get("use_gate") or "") in {"ready_for_notes", "review_first"}
        )
    ]
    local_recall_summary = local_recall.get("summary") if isinstance(local_recall, dict) else {}
    local_recall_summary = local_recall_summary if isinstance(local_recall_summary, dict) else {}
    local_recall_schema = str(local_recall.get("schema") if isinstance(local_recall, dict) else "")
    local_recall_missing_audit_count = safe_int(local_recall_summary.get("missing_local_recall_audit_count"))
    local_recall_complete_blocking = safe_int(local_recall_summary.get("complete_blocking_session_count"))
    local_recall_audited_sessions = safe_int(local_recall_summary.get("audited_session_count"))
    local_recall_session_count = safe_int(local_recall_summary.get("session_count"))
    local_recall_possible_lost_me_seconds = safe_float(local_recall_summary.get("possible_lost_me_seconds"))
    local_recall_needs_review_seconds = safe_float(local_recall_summary.get("needs_review_seconds"))
    local_recall_report_blocking_rows = [
        row
        for row in report_sessions(local_recall)
        if row.get("pipeline_status") == "complete" and row.get("blocking_low_local_recall") is True
    ]
    local_recall_selected_blocking_ids = set(session_ids(local_recall_selected_blocking))
    local_recall_report_scoped_blocking = [
        row
        for row in local_recall_report_blocking_rows
        if str(row.get("session_id") or "") not in excluded_session_ids
        and str(row.get("session_id") or "") in local_recall_selected_blocking_ids
    ]
    remote_leak_summary = remote_leak_segment.get("summary") if isinstance(remote_leak_segment, dict) else {}
    remote_leak_summary = remote_leak_summary if isinstance(remote_leak_summary, dict) else {}
    remote_leak_schema = str(remote_leak_segment.get("schema") if isinstance(remote_leak_segment, dict) else "")
    remote_leak_item_count = safe_int(remote_leak_summary.get("item_count"))
    remote_leak_missing_plan_count = safe_int(remote_leak_summary.get("missing_plan_count"))
    remote_leak_protect_local_content_items = safe_int(remote_leak_summary.get("protect_local_content_items"))
    remote_leak_protect_local_content_seconds = safe_float(remote_leak_summary.get("protect_local_content_seconds"))
    remote_leak_sessions_with_protected = safe_int(remote_leak_summary.get("sessions_with_protect_local_content"))
    echo_candidate_summary = (
        asr_positive_echo_candidate.get("summary")
        if isinstance(asr_positive_echo_candidate, dict)
        and isinstance(asr_positive_echo_candidate.get("summary"), dict)
        else {}
    )
    echo_candidate_safe_improved = safe_int(echo_candidate_summary.get("safe_improved_sessions"))
    echo_candidate_local_regressions = safe_int(echo_candidate_summary.get("local_recall_regressions"))
    check(
        checks,
        "transcript_order.audits_complete",
        order_missing_audits == 0,
        severity="warn",
        observed=order_missing_audits,
        threshold="0 missing order audits",
        message="transcript order corpus report covers every session in its scope",
    )
    check(
        checks,
        "transcript_order.no_complete_blocking_sessions",
        not order_report_scoped_blocking,
        observed=len(order_report_scoped_blocking),
        threshold="0 selected operational sessions",
        message="no selected operational session has blocking transcript order risk in the corpus order report",
        details=session_ids(order_report_scoped_blocking),
    )
    check(
        checks,
        "transcript_order.raw_complete_blocking_sessions",
        order_complete_blocking == 0,
        severity="warn",
        observed=order_complete_blocking,
        threshold="0 complete sessions in full historical corpus",
        message="full historical order report still contains diagnostic or superseded blocking sessions",
    )
    if local_recall is not None:
        check(
            checks,
            "local_recall.schema",
            local_recall_schema == "murmurmark.local_recall_corpus_report/v1",
            severity="warn",
            observed=local_recall_schema,
            threshold="murmurmark.local_recall_corpus_report/v1",
            message="local-recall corpus report uses the expected schema",
        )
        check(
            checks,
            "local_recall.audits_complete",
            local_recall_missing_audit_count == 0,
            severity="warn",
            observed=local_recall_missing_audit_count,
            threshold="0 missing local-recall audits",
            message="local-recall corpus report covers every session in its scope",
        )
        check(
            checks,
            "local_recall.no_complete_blocking_sessions",
            not local_recall_report_scoped_blocking,
            observed=len(local_recall_report_scoped_blocking),
            threshold="0 selected operational sessions",
            message="no selected operational session has blocking local-recall risk in the corpus local-recall report",
            details=session_ids(local_recall_report_scoped_blocking),
        )
        check(
            checks,
            "local_recall.raw_complete_blocking_sessions",
            local_recall_complete_blocking == 0,
            severity="warn",
            observed=local_recall_complete_blocking,
            threshold="0 complete sessions in full historical corpus",
            message="full historical local-recall report still contains diagnostic or superseded blocking sessions",
        )
    if remote_leak_segment is not None:
        check(
            checks,
            "remote_leak_segment.schema",
            remote_leak_schema == "murmurmark.remote_leak_segment_corpus_report/v1",
            severity="warn",
            observed=remote_leak_schema,
            threshold="murmurmark.remote_leak_segment_corpus_report/v1",
            message="remote-leak segment corpus report uses the expected schema",
        )
        check(
            checks,
            "remote_leak_segment.no_missing_plans",
            remote_leak_missing_plan_count == 0,
            severity="warn",
            observed=remote_leak_missing_plan_count,
            threshold="0 missing plans",
            message="remote-leak segment plans exist for every session in the corpus report scope",
        )
        check(
            checks,
            "remote_leak_segment.no_pending_items",
            remote_leak_item_count == 0,
            severity="warn",
            observed=remote_leak_item_count,
            threshold="0 items",
            message="remote-leak segment corpus queue is empty",
        )
        check(
            checks,
            "remote_leak_segment.no_protected_local_content",
            remote_leak_protect_local_content_items == 0,
            severity="warn",
            observed={
                "items": remote_leak_protect_local_content_items,
                "seconds": round(remote_leak_protect_local_content_seconds, 3),
                "sessions": remote_leak_sessions_with_protected,
            },
            threshold="0 protected-local-content items",
            message="remote-leak queue has no intervals where Me may still contain unique local content",
        )
    if asr_positive_echo_candidate is not None:
        echo_schema = str(asr_positive_echo_candidate.get("schema") or "")
        echo_summary = (
            asr_positive_echo_candidate.get("summary")
            if isinstance(asr_positive_echo_candidate.get("summary"), dict)
            else {}
        )
        echo_gate = (
            asr_positive_echo_candidate.get("promotion_gate")
            if isinstance(asr_positive_echo_candidate.get("promotion_gate"), dict)
            else {}
        )
        echo_local_regressions = safe_int(echo_summary.get("local_recall_regressions"))
        echo_safe_improved = safe_int(echo_summary.get("safe_improved_sessions"))
        echo_promotion_decision = str(echo_summary.get("promotion_decision") or "")
        echo_promotion_ready = echo_gate.get("promotion_ready") is True
        check(
            checks,
            "asr_positive_echo_candidate.schema",
            echo_schema == "murmurmark.echo.asr_positive_echo_candidate_corpus_report/v1",
            severity="warn",
            observed=echo_schema,
            threshold="murmurmark.echo.asr_positive_echo_candidate_corpus_report/v1",
            message="ASR-positive Echo candidate corpus report uses the expected schema",
        )
        check(
            checks,
            "asr_positive_echo_candidate.no_local_recall_regressions",
            echo_local_regressions == 0,
            severity="warn",
            observed=echo_local_regressions,
            threshold="0",
            message="experimental Echo candidate has no ASR local-word recall regressions",
        )
        check(
            checks,
            "asr_positive_echo_candidate.has_safe_improvements",
            echo_safe_improved >= 1,
            severity="warn",
            observed=echo_safe_improved,
            threshold=">= 1",
            message="experimental Echo candidate demonstrates at least one safe ASR-visible improvement",
        )
        check(
            checks,
            "asr_positive_echo_candidate.shadow_only",
            echo_promotion_decision == "shadow_only_do_not_promote" and not echo_promotion_ready,
            observed={
                "promotion_decision": echo_promotion_decision,
                "promotion_ready": echo_promotion_ready,
            },
            threshold="shadow_only_do_not_promote and promotion_ready=false",
            message="experimental Echo candidate cannot be promoted by the general corpus gate",
        )
    check(
        checks,
        "operational.review_queue_items",
        operational_queue <= args.max_operational_review_queue_items,
        observed=operational_queue,
        threshold=f"<= {args.max_operational_review_queue_items}",
        message="operational review queue stays bounded",
    )
    check(
        checks,
        "operational.verdict",
        operational_verdict in {"medium_risk_ready", "review_limited_ready", "not_ready"},
        severity="warn",
        observed=operational_verdict,
        threshold="known verdict",
        message="operational readiness verdict is recognized",
    )
    check(
        checks,
        "operational.blockers",
        not operational_blockers,
        severity="warn",
        observed=operational_blockers,
        threshold="[]",
        message="operational readiness still has blockers outside hard corpus gates",
    )

    baseline_snapshot = build_baseline_snapshot(
        args=args,
        complete=complete,
        ready_for_notes=ready_for_notes,
        review_first=review_first,
        incomplete=incomplete,
        total_review_burden_ratio=total_review_burden_ratio,
        corpus_item_count=corpus_item_count,
        corpus_session_count=corpus_session_count,
        corpus_readiness=corpus_readiness,
        audio_rows=audio_rows,
        audio_sessions=audio_sessions,
        cv_accuracy=cv_accuracy,
        audio_remaining=audio_remaining,
        operational_verdict=operational_verdict,
        operational_queue=operational_queue,
        local_recall_missing_audit_count=local_recall_missing_audit_count,
        local_recall_complete_blocking=local_recall_complete_blocking,
        local_recall_possible_lost_me_seconds=local_recall_possible_lost_me_seconds,
        local_recall_needs_review_seconds=local_recall_needs_review_seconds,
        remote_leak_item_count=remote_leak_item_count,
        remote_leak_missing_plan_count=remote_leak_missing_plan_count,
        remote_leak_protect_local_content_items=remote_leak_protect_local_content_items,
        remote_leak_protect_local_content_seconds=remote_leak_protect_local_content_seconds,
        suggested_closure_generated_rows=suggested_closure_generated_rows,
        suggested_closure_generated_seconds=suggested_closure_generated_seconds,
        suggested_closure_actionable_rows=suggested_closure_actionable_rows,
        suggested_closure_actionable_seconds=suggested_closure_actionable_seconds,
        suggested_closure_needs_review_rows=suggested_closure_needs_review_rows,
        suggested_closure_needs_review_seconds=suggested_closure_needs_review_seconds,
        suggested_closure_todo_rows=suggested_closure_todo_rows,
        suggested_closure_todo_seconds=suggested_closure_todo_seconds,
        suggested_closure_auto_rows=suggested_closure_auto_rows,
        suggested_closure_auto_seconds=suggested_closure_auto_seconds,
        suggested_closure_auto_keep_rows=suggested_closure_auto_keep_rows,
        suggested_closure_auto_keep_seconds=suggested_closure_auto_keep_seconds,
        suggested_closure_auto_drop_rows=suggested_closure_auto_drop_rows,
        suggested_closure_auto_drop_seconds=suggested_closure_auto_drop_seconds,
        suggested_closure_auto_review_rows=suggested_closure_auto_review_rows,
        suggested_closure_auto_review_seconds=suggested_closure_auto_review_seconds,
        suggested_closure_manual_remaining_rows=suggested_closure_manual_remaining_rows,
        suggested_closure_manual_remaining_seconds=suggested_closure_manual_remaining_seconds,
    )
    baseline = read_json(args.baseline) if args.baseline else None
    compare_baseline(checks, baseline=baseline, current=baseline_snapshot, args=args)

    failed = [item for item in checks if item["status"] == "fail"]
    warnings = [item for item in checks if item["status"] == "warn"]
    status = "failed" if failed or (args.strict_warnings and warnings) else ("passed_with_warnings" if warnings else "passed")

    return {
        "schema": SCHEMA_REPORT,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": {"name": "check-corpus-gates", "version": SCRIPT_VERSION},
        "status": status,
        "failed_gate_count": len(failed),
        "warning_count": len(warnings),
        "inputs": inputs,
        "thresholds": thresholds,
        "baseline": {
            "input": str(args.baseline) if args.baseline else None,
            "write_path": str(args.write_baseline) if args.write_baseline else None,
        },
        "summary": {
            "complete_pipeline_count": len(complete),
            "selected_operational_complete_pipeline_count": len(scoped_complete),
            "excluded_diagnostic_session_count": len(excluded_session_ids),
            "ready_for_notes": ready_for_notes,
            "review_first": review_first,
            "incomplete_sessions": incomplete,
            "total_review_burden_ratio": round(total_review_burden_ratio, 6),
            "corpus_readiness": corpus_readiness or None,
            "corpus_item_count": corpus_item_count,
            "corpus_session_count": corpus_session_count,
            "audio_judge_rows": audio_rows,
            "audio_judge_sessions": audio_sessions,
            "audio_judge_cv_accuracy": round(cv_accuracy, 6),
            "audio_judge_remaining_review_items": audio_remaining,
            "operational_verdict": operational_verdict or None,
            "operational_review_queue_items": operational_queue,
            "operational_blockers": operational_blockers,
            "operational_warnings": operational_warnings,
            "transcript_order_audited_sessions": order_audited_sessions,
            "transcript_order_session_count": order_session_count,
            "transcript_order_missing_audits": order_missing_audits,
            "transcript_order_complete_blocking_sessions": order_complete_blocking,
            "transcript_order_selected_blocking_sessions": len(order_report_scoped_blocking),
            "local_recall_audited_sessions": local_recall_audited_sessions,
            "local_recall_session_count": local_recall_session_count,
            "local_recall_missing_audits": local_recall_missing_audit_count,
            "local_recall_complete_blocking_sessions": local_recall_complete_blocking,
            "local_recall_selected_blocking_sessions": len(local_recall_report_scoped_blocking),
            "local_recall_selected_profile_blocking_sessions": len(local_recall_selected_blocking),
            "local_recall_selected_profile_review_sessions": len(local_recall_selected_review),
            "local_recall_possible_lost_me_seconds": round(local_recall_possible_lost_me_seconds, 3),
            "local_recall_needs_review_seconds": round(local_recall_needs_review_seconds, 3),
            "remote_leak_segment_item_count": remote_leak_item_count,
            "remote_leak_segment_missing_plan_count": remote_leak_missing_plan_count,
            "remote_leak_segment_protect_local_content_items": remote_leak_protect_local_content_items,
            "remote_leak_segment_protect_local_content_seconds": round(remote_leak_protect_local_content_seconds, 3),
            "remote_leak_segment_sessions_with_protect_local_content": remote_leak_sessions_with_protected,
            "asr_positive_echo_candidate_safe_improved_sessions": echo_candidate_safe_improved,
            "asr_positive_echo_candidate_local_recall_regressions": echo_candidate_local_regressions,
            "suggested_closure_generated_rows": suggested_closure_generated_rows,
            "suggested_closure_generated_seconds": round(suggested_closure_generated_seconds, 3),
            "suggested_closure_actionable_rows": suggested_closure_actionable_rows,
            "suggested_closure_actionable_seconds": round(suggested_closure_actionable_seconds, 3),
            "suggested_closure_needs_review_rows": suggested_closure_needs_review_rows,
            "suggested_closure_needs_review_seconds": round(suggested_closure_needs_review_seconds, 3),
            "suggested_closure_todo_rows": suggested_closure_todo_rows,
            "suggested_closure_todo_seconds": round(suggested_closure_todo_seconds, 3),
            "suggested_closure_auto_rows": suggested_closure_auto_rows,
            "suggested_closure_auto_seconds": round(suggested_closure_auto_seconds, 3),
            "suggested_closure_auto_keep_rows": suggested_closure_auto_keep_rows,
            "suggested_closure_auto_keep_seconds": round(suggested_closure_auto_keep_seconds, 3),
            "suggested_closure_auto_drop_rows": suggested_closure_auto_drop_rows,
            "suggested_closure_auto_drop_seconds": round(suggested_closure_auto_drop_seconds, 3),
            "suggested_closure_auto_review_rows": suggested_closure_auto_review_rows,
            "suggested_closure_auto_review_seconds": round(suggested_closure_auto_review_seconds, 3),
            "suggested_closure_manual_remaining_rows": suggested_closure_manual_remaining_rows,
            "suggested_closure_manual_remaining_seconds": round(suggested_closure_manual_remaining_seconds, 3),
        },
        "checks": checks,
        "baseline_snapshot": baseline_snapshot,
    }


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Corpus Gates",
        "",
        f"- Status: `{report['status']}`",
        f"- Failed gates: `{report['failed_gate_count']}`",
        f"- Warnings: `{report['warning_count']}`",
        "",
        "## Summary",
        "",
    ]
    for key, value in report["summary"].items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(
        [
            "",
            "## Checks",
            "",
            "| Status | Severity | Gate | Observed | Threshold |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for item in report["checks"]:
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{item['status']}`",
                    f"`{item['severity']}`",
                    f"`{item['id']}`",
                    f"`{json.dumps(item.get('observed'), ensure_ascii=False)}`",
                    f"`{json.dumps(item.get('threshold'), ensure_ascii=False)}`",
                ]
            )
            + " |"
        )
    lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    args = parse_args()
    report = build_report(args)
    out_dir: Path = args.out_dir
    json_path = out_dir / "corpus_gates_report.json"
    markdown_path = out_dir / "corpus_gates_report.md"
    read_command = f"less {markdown_path}"
    report["recommended_next"] = read_command
    report["next_commands"] = [
        {
            "id": "open_corpus_gates_report",
            "command": read_command,
            "reason": "inspect corpus gate failures and warnings",
        }
    ]
    report["open_commands"] = [
        {
            "id": "open_corpus_gates_report",
            "command": read_command,
            "path": str(markdown_path),
        }
    ]
    write_json(json_path, report)
    write_markdown(markdown_path, report)
    if args.write_baseline:
        write_json(args.write_baseline, report["baseline_snapshot"])
    print(f"status: {report['status']}")
    print(f"failed_gates: {report['failed_gate_count']}")
    print(f"warnings: {report['warning_count']}")
    print(f"written: {json_path}")
    print(f"recommended_next: {read_command}")
    if args.write_baseline:
        print(f"baseline: {args.write_baseline}")
    if report["status"] == "failed" and not args.no_fail:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
