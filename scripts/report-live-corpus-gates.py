#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = "murmurmark.live_corpus_gates_report/v1"
SCRIPT_VERSION = "1.9.4"
REAL_SESSION_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}$")
DEFAULT_TARGET_LIVE_SESSIONS = 3
DEFAULT_TARGET_MEANINGFUL_COMPARED_SESSIONS = 3
DEFAULT_TARGET_PASSING_COMPARED_SESSIONS = 3
SUPPRESSED_MIC_RESCUE_POLICIES = (
    "current_text_segment_gate",
    "strict_text_unique_v1",
    "remote_silent_text_v1",
    "audio_remote_quiet_v1",
    "audio_mic_dominant_v1",
    "audio_low_coherence_v1",
    "audio_low_corr_text_guard_v1",
    "audio_safe_union_v1",
    "batch_oracle_local_ceiling",
)
LIVE_IMPLEMENTABLE_RESCUE_POLICIES = tuple(
    policy for policy in SUPPRESSED_MIC_RESCUE_POLICIES if policy != "batch_oracle_local_ceiling"
)
SUPPRESSED_MIC_RESCUE_POLICY_METRICS = (
    "selected_segment_count",
    "selected_seconds",
    "local_seconds",
    "me_dominant_seconds",
    "mixed_seconds",
    "remote_risk_seconds",
    "precision_proxy",
    "local_recall_proxy",
    "missing_me_seconds_after",
    "missing_me_recovered_seconds",
)
MATERIAL_RESCUE_LOCAL_SECONDS = 5.0
TARGET_ME_RESCUE_POLICIES = (
    "target_me_confirmed_v1",
    "target_me_confirmed_remote_guard_v1",
    "target_me_confirmed_remote_guard_timeline_safe_v1",
    "target_me_possible_v1",
)
TARGET_ME_SHADOW_PROFILE_POLICIES = (
    "target_me_confirmed_remote_guard_timeline_safe_v1",
    "target_me_confirmed_remote_guard_timeline_safe_batch_remote_forbidden_oracle_v1",
    "target_me_confirmed_remote_guard_timeline_safe_batch_remote_forbidden_online_suppressed_mic_audio_safe_union_v1",
    "target_me_confirmed_remote_guard_timeline_safe_batch_remote_forbidden_online_suppressed_mic_low_corr_text_guard_v1",
    "target_me_confirmed_remote_guard_timeline_safe_batch_remote_forbidden_visible_suppressed_mic_oracle_v1",
    "online_suppressed_mic_dual_target_remote_guard_v1",
    "online_live_me_remote_overlap_filter_v1",
    "online_live_me_remote_overlap_filter_plus_dual_target_remote_guard_v1",
    "online_live_me_remote_overlap_filter_plus_target_me_remote_guard_v1",
    "online_live_me_remote_overlap_filter_plus_target_me_remote_guard_audio_safe_union_v1",
    "online_live_me_remote_overlap_filter_plus_target_me_timeline_safe_v1",
    "online_live_me_remote_overlap_filter_plus_target_me_timeline_safe_audio_safe_union_v1",
)
TARGET_ME_SHADOW_POLICY_METRICS = (
    "candidate_segment_count",
    "candidate_seconds",
    "missing_me_seconds_after",
    "missing_me_recovered_seconds",
    "suspected_remote_leak_in_me_count",
    "suspected_remote_leak_in_me_seconds",
    "order_mismatch_count",
    "role_constrained_order_mismatch_count",
    "contentful_role_constrained_order_mismatch_count",
    "order_mismatch_delta_count",
    "role_constrained_order_mismatch_delta_count",
    "contentful_role_constrained_order_mismatch_delta_count",
    "rejected_candidate_count",
    "rejected_candidate_seconds",
    "rejected_would_add_contentful_order_mismatch_count",
    "rejected_would_add_contentful_order_mismatch_seconds",
    "rejected_would_add_suspected_remote_leak_count",
    "rejected_would_add_suspected_remote_leak_seconds",
)
TARGET_ME_SHADOW_PROFILE_METRICS = (
    "all_parity_gates_passed",
    "non_passing_gate_count",
    "live_token_recall_in_batch",
    "live_turn_count",
    "live_me_turn_count",
    "live_remote_turn_count",
    "live_order_mismatch_count",
    "live_role_constrained_order_mismatch_count",
    "live_contentful_role_constrained_order_mismatch_count",
    "live_missing_me_utterance_count",
    "live_missing_me_seconds",
    "live_missing_me_visible_in_suppressed_mic_count",
    "live_missing_me_visible_in_suppressed_mic_seconds",
    "live_missing_me_not_visible_in_suppressed_mic_count",
    "live_missing_me_not_visible_in_suppressed_mic_seconds",
    "live_missing_me_with_target_me_candidate_count",
    "live_missing_me_with_target_me_candidate_seconds",
    "live_missing_me_without_target_me_candidate_count",
    "live_missing_me_without_target_me_candidate_seconds",
    "live_missing_me_visible_with_target_me_candidate_count",
    "live_missing_me_visible_with_target_me_candidate_seconds",
    "live_missing_me_visible_without_target_me_candidate_count",
    "live_missing_me_visible_without_target_me_candidate_seconds",
    "live_missing_me_not_visible_with_target_me_candidate_count",
    "live_missing_me_not_visible_with_target_me_candidate_seconds",
    "live_missing_me_not_visible_without_target_me_candidate_count",
    "live_missing_me_not_visible_without_target_me_candidate_seconds",
    "live_suspected_remote_leak_in_me_count",
    "live_suspected_remote_leak_in_me_seconds",
    "removed_live_turn_count",
    "removed_live_turn_seconds",
    "visible_suppressed_mic_added_turn_count",
    "visible_suppressed_mic_added_turn_seconds",
    "visible_suppressed_mic_rejected_turn_count",
)
LIVE_QUARANTINE_REASON = (
    "live pipeline is quarantined because the async live path has not yet passed capture-safety "
    "and parity gates; do not use normal production live collection, and use coverage_path to decide "
    "whether controlled Live Evidence collection is currently allowed"
)
PARITY_DIMENSIONS: dict[str, dict[str, Any]] = {
    "capture_safety": {
        "title": "Capture safety",
        "gates": ["capture_safety"],
        "promotion_required": True,
    },
    "order_risk": {
        "title": "Order risk",
        "gates": ["order_risk"],
        "promotion_required": True,
    },
    "local_recall": {
        "title": "Local recall",
        "gates": ["local_recall"],
        "promotion_required": True,
    },
    "remote_leakage": {
        "title": "Remote leakage",
        "gates": ["remote_duplicate_leak"],
        "promotion_required": True,
    },
    "review_burden": {
        "title": "Review burden",
        "gates": ["review_burden"],
        "promotion_required": True,
    },
    "selected_notes_readiness": {
        "title": "Selected notes readiness",
        "gates": ["selected_notes_readiness"],
        "promotion_required": True,
    },
    "chunk_boundary_risks": {
        "title": "Chunk-boundary risks",
        "gates": ["chunk_boundary_risks", "duplicate_chunks"],
        "promotion_required": True,
    },
    "draft_text_recall": {
        "title": "Draft text recall",
        "gates": ["live_token_recall"],
        "promotion_required": True,
    },
    "required_artifacts": {
        "title": "Required artifacts",
        "gates": ["required_artifacts", "raw_batch_authoritative"],
        "promotion_required": True,
    },
}
TRIAGE_CATEGORY_INFO: dict[str, dict[str, str]] = {
    "batch_review_required": {
        "title": "Batch review/readiness required",
        "recommended_next": (
            "finish the authoritative batch review/readiness path for this session; this is not a live-capture fix"
        ),
    },
    "capture_safety_risk": {
        "title": "Capture safety risk",
        "recommended_next": (
            "keep live quarantined for this session; follow coverage_path.recommended_next to decide "
            "whether controlled Live Evidence collection is currently allowed"
        ),
    },
    "live_local_recall_gap": {
        "title": "Live local-recall gap",
        "recommended_next": (
            "keep live quarantined for this session; collect controlled Live Evidence only when "
            "coverage_path allows it, and redesign live segmentation if new controlled evidence keeps "
            "failing local recall"
        ),
    },
    "live_remote_leakage": {
        "title": "Live remote leakage",
        "recommended_next": (
            "inspect echo/role evidence and keep live Me output blocked until remote-forbidden gates pass"
        ),
    },
    "live_draft_text_drift": {
        "title": "Live draft text drift",
        "recommended_next": "treat live draft as orientation only; keep the batch transcript authoritative",
    },
    "missing_batch_artifacts": {
        "title": "Missing batch artifacts",
        "recommended_next": "run or repair normal batch processing for this session before using it for live parity",
    },
    "missing_live_asr_artifacts": {
        "title": "Missing live ASR artifacts",
        "recommended_next": (
            "do not use this live run as promotion evidence; rerun only offline diagnostics on copied artifacts if useful"
        ),
    },
    "chunk_boundary_risk": {
        "title": "Chunk-boundary risk",
        "recommended_next": "fix chunk reconciliation and overlap dedupe before live promotion",
    },
    "order_risk": {
        "title": "Order risk",
        "recommended_next": "fix live timeline ordering/reconciliation before live promotion",
    },
    "other": {
        "title": "Other live parity blocker",
        "recommended_next": "inspect the session live_batch_comparison.json and keep live blocked",
    },
}
OBJECTIVE_DIMENSION_ACTIONS: dict[str, dict[str, str]] = {
    "capture_safety": {
        "id": "capture_safe_redesign_before_more_live_coverage",
        "title": "Prove capture-safe live segment production",
        "recommended_next": (
            "keep live quarantined for affected historical sessions; use coverage_path to decide whether "
            "controlled Live Evidence collection is currently allowed"
        ),
    },
    "required_artifacts": {
        "id": "complete_required_live_and_batch_artifacts",
        "title": "Complete required live/batch artifacts",
        "recommended_next": "repair or exclude sessions missing live chunks, batch transcript, or clean dialogue before parity use",
    },
    "local_recall": {
        "id": "fix_live_local_recall_gap",
        "title": "Fix live local recall gaps",
        "recommended_next": "inspect missing Me examples and improve live mic role/echo/boundary handling before promotion",
    },
    "remote_leakage": {
        "id": "fix_live_remote_leakage",
        "title": "Fix live remote-in-Me leakage",
        "recommended_next": "keep live Me output blocked until remote-forbidden evidence passes on live chunks",
    },
    "review_burden": {
        "id": "reduce_authoritative_batch_review_burden",
        "title": "Reduce selected batch review burden",
        "recommended_next": "finish batch review/readiness work; live cannot promote above a review-heavy authoritative baseline",
    },
    "selected_notes_readiness": {
        "id": "make_selected_batch_notes_ready",
        "title": "Make selected batch output notes-ready",
        "recommended_next": "complete authoritative batch readiness before using live parity as promotion evidence",
    },
    "chunk_boundary_risks": {
        "id": "fix_live_chunk_boundary_risks",
        "title": "Fix live chunk-boundary risks",
        "recommended_next": (
            "fix live chunk reconciliation and overlap dedupe; unresolved boundary suppressions still block promotion"
        ),
    },
    "draft_text_recall": {
        "id": "improve_live_draft_text_recall",
        "title": "Improve live draft text recall",
        "recommended_next": "treat live draft as orientation only until draft text reliably matches selected batch output",
    },
    "order_risk": {
        "id": "fix_live_order_risk",
        "title": "Fix live ordering risk",
        "recommended_next": "fix live timeline ordering/reconciliation before live promotion",
    },
}
OBJECTIVE_DIMENSION_PRIORITY = {
    "capture_safety": 0,
    "required_artifacts": 1,
    "order_risk": 2,
    "local_recall": 3,
    "remote_leakage": 4,
    "chunk_boundary_risks": 5,
    "review_burden": 6,
    "selected_notes_readiness": 7,
    "draft_text_recall": 8,
}
CAPTURE_SAFETY_BLOCKERS = {"interrupted_capture", "silent_capture", "sparse_capture"}
CAPTURE_SAFETY_WARNING_MARKERS = (
    "no screencapturekit audio samples",
    "capture produced no audio samples",
    "screencapturekit stream restarted",
    "capture finalized as partial",
    "captured audio covers only",
    "track appears silent or almost silent",
)


def dimensions_for_gate(gate_name: str) -> list[str]:
    return [
        key
        for key, spec in PARITY_DIMENSIONS.items()
        if gate_name in {str(name) for name in spec.get("gates", [])}
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aggregate near-realtime shadow parity gates over a local session corpus.")
    parser.add_argument("targets", nargs="*", help="all, latest or session paths. Default: all.")
    parser.add_argument("--sessions-root", type=Path, default=Path("sessions"))
    parser.add_argument("--out-dir", type=Path, default=Path("sessions/_reports/live-pipeline"))
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Run compare-live-batch.py for live sessions before aggregating the corpus report.",
    )
    parser.add_argument("--min-live-sessions", type=int, default=0, help="Required live sessions for strict coverage checks.")
    parser.add_argument("--min-compared-sessions", type=int, default=0, help="Required live-vs-batch compared sessions for strict coverage checks.")
    parser.add_argument(
        "--min-meaningful-compared-sessions",
        type=int,
        default=0,
        help="Required compared sessions with both Me and remote evidence in live and batch outputs.",
    )
    parser.add_argument(
        "--min-passing-compared-sessions",
        type=int,
        default=0,
        help="Required compared sessions where every live parity gate passed.",
    )
    parser.add_argument("--max-order-mismatches", type=int, default=None)
    parser.add_argument("--max-missing-me-sec", type=float, default=None)
    parser.add_argument("--max-remote-in-me-sec", type=float, default=None)
    parser.add_argument("--max-boundary-duplicates", type=int, default=None)
    parser.add_argument("--require-passing-gates", action="store_true", help="Fail strict coverage unless every live parity gate is passed.")
    parser.add_argument("--fail-on-insufficient-coverage", action="store_true")
    parser.add_argument("--fail-on-risk", action="store_true")
    parser.add_argument("--fail-on-promotion", action="store_true")
    parser.add_argument(
        "--target-live-sessions",
        type=int,
        default=DEFAULT_TARGET_LIVE_SESSIONS,
        help="Advisory coverage target for normal live-parity confidence. Does not fail unless also used through strict --min-* gates.",
    )
    parser.add_argument(
        "--target-meaningful-compared-sessions",
        type=int,
        default=DEFAULT_TARGET_MEANINGFUL_COMPARED_SESSIONS,
        help="Advisory target for meaningful live-vs-batch comparisons.",
    )
    parser.add_argument(
        "--target-passing-compared-sessions",
        type=int,
        default=DEFAULT_TARGET_PASSING_COMPARED_SESSIONS,
        help="Advisory target for passing live-vs-batch comparisons.",
    )
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
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def session_dirs(root: Path) -> list[Path]:
    if not root.exists():
        return []
    sessions = [path for path in root.iterdir() if path.is_dir() and (path / "session.json").exists()]
    return sorted(sessions, key=lambda path: path.name)


def resolve_targets(args: argparse.Namespace) -> list[Path]:
    root = args.sessions_root
    targets = args.targets or ["all"]
    if targets == ["all"]:
        return session_dirs(root)
    if targets == ["latest"]:
        sessions = session_dirs(root)
        return [sessions[-1]] if sessions else []
    resolved: list[Path] = []
    for target in targets:
        if target == "all":
            resolved.extend(session_dirs(root))
        elif target == "latest":
            sessions = session_dirs(root)
            if sessions:
                resolved.append(sessions[-1])
        else:
            path = Path(target)
            resolved.append(path if path.exists() else root / target)
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in resolved:
        key = path.resolve()
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def refresh_live_comparisons(sessions: list[Path]) -> list[dict[str, Any]]:
    script = Path(__file__).resolve().parent / "compare-live-batch.py"
    rows: list[dict[str, Any]] = []
    for session in sessions:
        live_report = session / "derived/live/live_pipeline_report.json"
        if not live_report.exists():
            continue
        command = [sys.executable, str(script), str(session)]
        result = subprocess.run(
            command,
            text=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        rows.append(
            {
                "session": str(session),
                "status": "passed" if result.returncode == 0 else "failed",
                "reason": None if result.returncode == 0 else "compare_live_batch_failed",
                "returncode": result.returncode,
                "stdout_tail": (result.stdout or "")[-2000:],
                "stderr_tail": (result.stderr or "")[-2000:],
            }
        )
    return rows


def rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def gate_rows(comparison: dict[str, Any] | None) -> list[dict[str, Any]]:
    gates = comparison.get("parity_gates") if isinstance(comparison, dict) else None
    rows = gates.get("gates") if isinstance(gates, dict) else None
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def non_passing_gate_rows(gates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [gate for gate in gates if gate.get("status") != "passed"]


def safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def suppressed_mic_rescue_policy_metric_values(metrics: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(metrics, dict):
        return {
            f"live_rescue_policy_{policy}_{metric}": None
            for policy in SUPPRESSED_MIC_RESCUE_POLICIES
            for metric in SUPPRESSED_MIC_RESCUE_POLICY_METRICS
        }
    return {
        f"live_rescue_policy_{policy}_{metric}": metrics.get(f"live_rescue_policy_{policy}_{metric}")
        for policy in SUPPRESSED_MIC_RESCUE_POLICIES
        for metric in SUPPRESSED_MIC_RESCUE_POLICY_METRICS
    }


def target_me_shadow_policy_metric_values(metrics: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(metrics, dict):
        return {
            f"live_target_me_shadow_policy_{policy}_{metric}": None
            for policy in TARGET_ME_RESCUE_POLICIES
            for metric in TARGET_ME_SHADOW_POLICY_METRICS
        }
    return {
        f"live_target_me_shadow_policy_{policy}_{metric}": metrics.get(
            f"live_target_me_shadow_policy_{policy}_{metric}"
        )
        for policy in TARGET_ME_RESCUE_POLICIES
        for metric in TARGET_ME_SHADOW_POLICY_METRICS
    }


def target_me_shadow_profile_metric_values(metrics: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(metrics, dict):
        return {
            f"live_target_me_shadow_profile_{policy}_{metric}": None
            for policy in TARGET_ME_SHADOW_PROFILE_POLICIES
            for metric in TARGET_ME_SHADOW_PROFILE_METRICS
        }
    return {
        f"live_target_me_shadow_profile_{policy}_{metric}": metrics.get(
            f"live_target_me_shadow_profile_{policy}_{metric}"
        )
        for policy in TARGET_ME_SHADOW_PROFILE_POLICIES
        for metric in TARGET_ME_SHADOW_PROFILE_METRICS
    }


def all_gates_passed(comparison: dict[str, Any] | None) -> bool:
    rows = gate_rows(comparison)
    return bool(rows) and all(row.get("status") == "passed" for row in rows)


def dimension_statuses(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    gate_by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        gate_by_name[str(row.get("name") or "unknown")].append(row)
    severity = {"failed": 4, "blocked": 4, "warning": 3, "not_evaluated": 2, "passed": 1}
    for key, spec in PARITY_DIMENSIONS.items():
        gates = [gate for name in spec["gates"] for gate in gate_by_name.get(name, [])]
        if not gates:
            status = "missing"
        else:
            status = max((str(gate.get("status") or "unknown") for gate in gates), key=lambda item: severity.get(item, 2))
        result[key] = {
            "title": spec["title"],
            "status": status,
            "promotion_required": bool(spec.get("promotion_required")),
            "gates": [
                {
                    "name": str(gate.get("name") or "unknown"),
                    "status": str(gate.get("status") or "unknown"),
                    "reason": gate.get("reason"),
                }
                for gate in gates
            ],
        }
    return result


def meaningful_comparison(metrics: dict[str, Any], comparison_status: Any) -> bool:
    if metrics.get("meaningful_live_comparison") is True:
        return True
    if comparison_status != "shadow_compared":
        return False
    return bool(
        safe_int(metrics.get("live_turn_count")) > 0
        and safe_int(metrics.get("batch_utterance_count")) > 0
        and safe_int(metrics.get("live_me_turn_count")) > 0
        and safe_int(metrics.get("live_remote_turn_count")) > 0
        and safe_int(metrics.get("batch_me_utterance_count")) > 0
        and safe_int(metrics.get("batch_remote_utterance_count")) > 0
    )


def build_capture_safety_gate(session: Path) -> dict[str, Any]:
    session_json = read_json(session / "session.json")
    pipeline = read_json(session / "derived/pipeline-run/pipeline_run_report.json")
    pipeline_status = pipeline.get("status") if isinstance(pipeline, dict) else None
    pipeline_blocker = str(pipeline.get("blocker") or "") if isinstance(pipeline, dict) else ""
    health = session_json.get("health") if isinstance(session_json, dict) else None
    health = health if isinstance(health, dict) else {}
    warnings = health.get("warnings") if isinstance(health.get("warnings"), list) else []
    warning_texts = [str(item) for item in warnings]
    restart_count = safe_int(health.get("screen_capture_restart_count"))
    partial = bool(health.get("partial")) or (isinstance(session_json, dict) and session_json.get("status") == "partial")
    explicit_stop = health.get("explicit_stop")
    health_summary = str(health.get("summary") or "")
    stop_reason = str(health.get("stop_reason") or "")
    safety_warnings = [
        text for text in warning_texts if any(marker in text.lower() for marker in CAPTURE_SAFETY_WARNING_MARKERS)
    ]
    evidence = {
        "pipeline_status": pipeline_status,
        "pipeline_blocker": pipeline_blocker or None,
        "session_status": session_json.get("status") if isinstance(session_json, dict) else None,
        "health_summary": health_summary or None,
        "partial": partial,
        "explicit_stop": explicit_stop,
        "stop_reason": stop_reason or None,
        "screen_capture_restart_count": restart_count,
        "warning_count": len(warning_texts),
        "safety_warning_count": len(safety_warnings),
        "sample_warnings": safety_warnings[:5],
    }
    if pipeline_status == "blocked" and pipeline_blocker in CAPTURE_SAFETY_BLOCKERS:
        return {
            "name": "capture_safety",
            "status": "blocked",
            "reason": f"batch pipeline blocked the session as {pipeline_blocker}",
            "evidence": evidence,
        }
    if not isinstance(session_json, dict) or not health:
        return {
            "name": "capture_safety",
            "status": "not_evaluated",
            "reason": "session capture health is missing",
            "evidence": evidence,
        }
    if partial or explicit_stop is False:
        return {
            "name": "capture_safety",
            "status": "blocked",
            "reason": "capture was partial or did not end through an explicit stop",
            "evidence": evidence,
        }
    if safety_warnings or restart_count > 0 or health_summary in {"warning", "partial"}:
        return {
            "name": "capture_safety",
            "status": "warning",
            "reason": "capture completed with ScreenCaptureKit/audio health warnings",
            "evidence": evidence,
        }
    return {
        "name": "capture_safety",
        "status": "passed",
        "reason": "capture health is complete and warning-free",
        "evidence": evidence,
    }


def evidence_scope(session: Path, root: Path) -> str:
    session_name = rel(session, root).split("/", maxsplit=1)[0]
    return "real_meeting" if REAL_SESSION_RE.match(session_name) else "diagnostic"


def summarize_dimensions(rows: list[dict[str, Any]]) -> tuple[dict[str, Counter[str]], dict[str, list[str]]]:
    dimension_counts: dict[str, Counter[str]] = defaultdict(Counter)
    dimension_issue_sessions: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        dimensions = row.get("parity_dimensions") if isinstance(row.get("parity_dimensions"), dict) else {}
        for key in PARITY_DIMENSIONS:
            value = dimensions.get(key) if isinstance(dimensions, dict) else None
            status = str(value.get("status") if isinstance(value, dict) else "missing")
            dimension_counts[key][status] += 1
            if status != "passed":
                dimension_issue_sessions[key].append(str(row.get("session") or ""))
    return dimension_counts, dimension_issue_sessions


def build_dimensions_payload(
    dimension_counts: dict[str, Counter[str]],
    dimension_issue_sessions: dict[str, list[str]],
) -> dict[str, dict[str, Any]]:
    return {
        key: {
            "title": spec["title"],
            "promotion_required": bool(spec.get("promotion_required")),
            "counts": dict(dimension_counts.get(key, Counter())),
            "issue_sessions": dimension_issue_sessions.get(key, []),
        }
        for key, spec in PARITY_DIMENSIONS.items()
    }


def row_dimension_status(row: dict[str, Any], key: str) -> str:
    dimensions = row.get("parity_dimensions") if isinstance(row.get("parity_dimensions"), dict) else {}
    value = dimensions.get(key) if isinstance(dimensions, dict) else None
    return str(value.get("status") if isinstance(value, dict) else "missing")


def capture_safe_candidate_row(row: dict[str, Any]) -> bool:
    return bool(
        row.get("evidence_scope") == "real_meeting"
        and row.get("comparison_status") == "shadow_compared"
        and row.get("meaningful_compared") is True
        and row_dimension_status(row, "capture_safety") == "passed"
        and row_dimension_status(row, "required_artifacts") == "passed"
    )


def capture_safe_evaluable_row(row: dict[str, Any]) -> bool:
    return bool(
        row.get("evidence_scope") == "real_meeting"
        and row.get("comparison_status") == "shadow_compared"
        and row_dimension_status(row, "capture_safety") == "passed"
        and row_dimension_status(row, "required_artifacts") == "passed"
    )


def blocking_dimensions_from_counts(dimension_counts: dict[str, Counter[str]]) -> list[str]:
    return [
        key
        for key, counts in dimension_counts.items()
        if any(status != "passed" and count > 0 for status, count in counts.items())
    ]


def capture_safe_proof_status(capture_regression_check: dict[str, Any] | None) -> str:
    proof = (
        capture_regression_check.get("capture_safe_proof")
        if isinstance(capture_regression_check, dict)
        and isinstance(capture_regression_check.get("capture_safe_proof"), dict)
        else {}
    )
    return str(proof.get("status") or "missing")


def controlled_real_live_pilot_allowed(capture_regression_check: dict[str, Any] | None) -> bool:
    return capture_safe_proof_status(capture_regression_check) == "full_fail_open_proof_passed"


def next_focus_for_dimensions(dimensions: list[str]) -> dict[str, Any] | None:
    if not dimensions:
        return None
    dimension = sorted(dimensions, key=lambda key: OBJECTIVE_DIMENSION_PRIORITY.get(key, 100))[0]
    action = OBJECTIVE_DIMENSION_ACTIONS.get(
        dimension,
        {
            "id": f"fix_{dimension}",
            "title": f"Fix {dimension}",
            "recommended_next": "inspect this live parity dimension before promotion",
        },
    )
    return {
        "dimension": dimension,
        "action_id": action["id"],
        "title": action["title"],
        "recommended_next": action["recommended_next"],
    }


def metric_aware_next_focus(
    dimensions: list[str],
    summary: dict[str, Any],
    base_focus: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    focus = dict(base_focus) if isinstance(base_focus, dict) else next_focus_for_dimensions(dimensions)
    if not focus:
        return None
    stable_order = safe_int(summary.get("real_live_unambiguous_contentful_role_constrained_order_mismatch_count"))
    contentful_order = safe_int(summary.get("real_live_contentful_role_constrained_order_mismatch_count"))
    missing_me_seconds = safe_float(summary.get("real_live_missing_me_seconds"))
    visible_missing_me_seconds = safe_float(summary.get("real_live_missing_me_visible_in_suppressed_mic_seconds"))
    remote_leak_seconds = safe_float(summary.get("real_live_suspected_remote_leak_in_me_seconds"))
    order_is_small_and_targeted = stable_order <= 2 and contentful_order <= 4
    target_dimension: str | None = None
    reason: str | None = None
    if focus.get("dimension") == "order_risk" and order_is_small_and_targeted:
        if "local_recall" in dimensions and missing_me_seconds >= 30.0:
            target_dimension = "local_recall"
            reason = (
                "order risk is narrowed to a small stable subset, while live local recall loses "
                "substantial Me speech"
            )
        elif "remote_leakage" in dimensions and remote_leak_seconds > 0.5:
            target_dimension = "remote_leakage"
            reason = (
                "order risk is narrowed to a small stable subset, while live Me still contains remote leakage"
            )
    if not target_dimension:
        return focus
    action = OBJECTIVE_DIMENSION_ACTIONS[target_dimension]
    return {
        **focus,
        "dimension": target_dimension,
        "action_id": action["id"],
        "title": action["title"],
        "recommended_next": action["recommended_next"],
        "source": "metric_aware_focus_override",
        "previous_focus": focus,
        "override_reason": reason,
        "metric_evidence": {
            "real_live_unambiguous_contentful_role_constrained_order_mismatch_count": stable_order,
            "real_live_contentful_role_constrained_order_mismatch_count": contentful_order,
            "real_live_missing_me_seconds": round(missing_me_seconds, 3),
            "real_live_missing_me_visible_in_suppressed_mic_seconds": round(visible_missing_me_seconds, 3),
            "real_live_suspected_remote_leak_in_me_seconds": round(remote_leak_seconds, 3),
        },
    }


def objective_audit_row(row_id: str, status: str, title: str, evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row_id,
        "status": status,
        "title": title,
        "evidence": evidence,
    }


def dimension_issue_score(non_passing: dict[str, Any], issue_session_count: int) -> int:
    status_weights = {
        "blocked": 100,
        "failed": 100,
        "missing": 90,
        "warning": 40,
        "not_evaluated": 25,
        "unknown": 10,
    }
    score = issue_session_count
    for status, count in non_passing.items():
        score += status_weights.get(str(status), 10) * safe_int(count)
    return score


def build_objective_audit(
    summary: dict[str, Any],
    coverage_target: dict[str, Any],
    real_dimensions: dict[str, dict[str, Any]],
    promotion_policy: dict[str, Any],
    candidate_scope: dict[str, Any],
    capture_regression_check: dict[str, Any] | None,
) -> dict[str, Any]:
    required_dimensions = set(PARITY_DIMENSIONS.keys())
    covered_dimensions = set(real_dimensions.keys())
    missing_dimensions = sorted(required_dimensions - covered_dimensions)
    blocking_dimensions: list[str] = []
    dimension_statuses: dict[str, dict[str, Any]] = {}
    next_actions: list[dict[str, Any]] = []
    for key in sorted(required_dimensions):
        value = real_dimensions.get(key) if isinstance(real_dimensions.get(key), dict) else {}
        counts = value.get("counts") if isinstance(value.get("counts"), dict) else {}
        non_passing = {status: count for status, count in counts.items() if status != "passed" and count}
        if not counts:
            non_passing = {"missing": 1}
        issue_sessions = value.get("issue_sessions") if isinstance(value.get("issue_sessions"), list) else []
        if non_passing:
            blocking_dimensions.append(key)
            action = OBJECTIVE_DIMENSION_ACTIONS.get(
                key,
                {
                    "id": f"fix_{key}",
                    "title": f"Fix {key}",
                    "recommended_next": "inspect this live parity dimension before promotion",
                },
            )
            next_actions.append(
                {
                    "dimension": key,
                    "action_id": action["id"],
                    "title": action["title"],
                    "recommended_next": action["recommended_next"],
                    "non_passing": non_passing,
                    "issue_sessions": issue_sessions,
                    "issue_session_count": len(issue_sessions),
                    "score": dimension_issue_score(non_passing, len(issue_sessions)),
                    "priority": OBJECTIVE_DIMENSION_PRIORITY.get(key, 100),
                }
            )
        dimension_statuses[key] = {
            "counts": counts,
            "non_passing": non_passing,
            "issue_sessions": issue_sessions,
        }
    next_actions.sort(
        key=lambda item: (
            safe_int(item.get("priority")) if item.get("priority") is not None else 100,
            -safe_int(item.get("score")),
            str(item.get("dimension") or ""),
        )
    )
    next_focus = next_actions[0] if next_actions else {
        "dimension": None,
        "action_id": "collect_controlled_live_parity_coverage",
        "title": "Collect controlled live parity coverage",
        "recommended_next": (
            "only after capture-safety proof and all parity dimensions pass, collect controlled Live Evidence"
        ),
        "non_passing": {},
        "issue_sessions": [],
        "issue_session_count": 0,
        "score": 0,
        "priority": 999,
    }
    next_focus = metric_aware_next_focus(blocking_dimensions, summary, next_focus) or next_focus
    capture_proof_status = capture_safe_proof_status(capture_regression_check)
    capture_proof_row_status = (
        "passed"
        if capture_proof_status == "full_fail_open_proof_passed"
        else ("incomplete" if capture_proof_status == "static_only" else "blocked")
    )
    if capture_proof_row_status != "passed" and (
        not next_actions or next_focus.get("action_id") == "collect_controlled_live_parity_coverage"
    ):
        next_focus = {
            "dimension": "capture_safety",
            "action_id": "capture_safe_redesign_before_more_live_coverage",
            "title": "Prove capture-safe live segment production",
            "recommended_next": (
                "keep live quarantined; run the full capture regression proof before collecting real live meetings"
            ),
            "non_passing": {"capture_safe_proof": 1},
            "issue_sessions": [],
            "issue_session_count": 0,
            "score": 100,
            "priority": 0,
        }
    if next_focus.get("dimension") == "capture_safety":
        if capture_proof_row_status == "passed":
            next_focus = {
                **next_focus,
                "title": "Resolve historical capture-safety evidence",
                "recommended_next": (
                    "capture-safe fail-open proof passed; keep live quarantined, treat historical unsafe "
                    "live sessions as negative evidence, and focus on capture-safe candidate parity blockers"
                ),
            }
        next_focus = {
            **next_focus,
            "capture_regression_check": {
                "status": capture_regression_check.get("status") if isinstance(capture_regression_check, dict) else None,
                "mode": capture_regression_check.get("mode") if isinstance(capture_regression_check, dict) else None,
                "capture_safe_proof_status": capture_proof_status,
            },
        }
    if (
        promotion_policy.get("controlled_real_live_pilot_allowed") is True
        and coverage_target.get("status") != "passed"
        and not (candidate_scope.get("blocking_dimensions") if isinstance(candidate_scope, dict) else [])
    ):
        next_focus = {
            "dimension": "controlled_live_pilot_coverage",
            "action_id": "collect_controlled_capture_safe_live_pilot",
            "title": "Collect controlled capture-safe live parity pilots",
            "recommended_next": (
                "run a controlled Live Evidence sidecar only after the full fail-open proof; keep batch "
                "authoritative and rerun `murmurmark corpus live all --refresh` after processing"
            ),
            "non_passing": {
                "passing_compared_sessions_remaining": coverage_target.get("passing_compared_sessions_remaining", 0)
            },
            "issue_sessions": [],
            "issue_session_count": 0,
            "score": 10,
            "priority": 2,
            "capture_regression_check": {
                "status": capture_regression_check.get("status") if isinstance(capture_regression_check, dict) else None,
                "mode": capture_regression_check.get("mode") if isinstance(capture_regression_check, dict) else None,
                "capture_safe_proof_status": capture_proof_status,
            },
        }
    candidate_next_focus = (
        candidate_scope.get("next_focus")
        if isinstance(candidate_scope, dict) and isinstance(candidate_scope.get("next_focus"), dict)
        else None
    )
    if (
        capture_proof_row_status == "passed"
        and safe_int(candidate_scope.get("sessions") if isinstance(candidate_scope, dict) else 0) > 0
        and (candidate_scope.get("blocking_dimensions") if isinstance(candidate_scope, dict) else [])
        and candidate_next_focus
    ):
        candidate_next_focus = metric_aware_next_focus(
            list(candidate_scope.get("blocking_dimensions") or []),
            summary,
            candidate_next_focus,
        )
        next_focus = {
            **candidate_next_focus,
            "source": "capture_safe_candidate_scope",
            "recommended_next": (
                f"{candidate_next_focus.get('recommended_next')}; "
                "historical unsafe live sessions remain negative evidence and must not be used as promotion candidates"
            ),
            "candidate_scope": {
                "sessions": candidate_scope.get("sessions"),
                "passing_sessions": candidate_scope.get("passing_sessions"),
                "blocking_dimensions": candidate_scope.get("blocking_dimensions"),
                "session_ids": candidate_scope.get("session_ids"),
            },
            "capture_regression_check": {
                "status": capture_regression_check.get("status") if isinstance(capture_regression_check, dict) else None,
                "mode": capture_regression_check.get("mode") if isinstance(capture_regression_check, dict) else None,
                "capture_safe_proof_status": capture_proof_status,
            },
        }

    rows = [
        objective_audit_row(
            "real_live_sessions_present",
            "passed" if safe_int(summary.get("real_live_sessions")) > 0 else "incomplete",
            "There is at least one real meeting live-pipeline session in the corpus",
            {"real_live_sessions": safe_int(summary.get("real_live_sessions"))},
        ),
        objective_audit_row(
            "live_compared_to_batch",
            "passed" if safe_int(summary.get("real_compared_sessions")) > 0 else "incomplete",
            "Real live chunks/drafts are compared with authoritative batch output",
            {
                "real_compared_sessions": safe_int(summary.get("real_compared_sessions")),
                "real_meaningful_compared_sessions": safe_int(summary.get("real_meaningful_compared_sessions")),
            },
        ),
        objective_audit_row(
            "coverage_target_met",
            "passed" if coverage_target.get("status") == "passed" else "incomplete",
            "Advisory real live coverage target is met",
            {
                "status": coverage_target.get("status"),
                "target_live_sessions": coverage_target.get("target_live_sessions"),
                "target_meaningful_compared_sessions": coverage_target.get("target_meaningful_compared_sessions"),
                "target_passing_compared_sessions": coverage_target.get("target_passing_compared_sessions"),
                "live_sessions_remaining": coverage_target.get("live_sessions_remaining"),
                "meaningful_compared_sessions_remaining": coverage_target.get("meaningful_compared_sessions_remaining"),
                "passing_compared_sessions_remaining": coverage_target.get("passing_compared_sessions_remaining"),
            },
        ),
        objective_audit_row(
            "required_dimensions_covered",
            "passed" if not missing_dimensions else "blocked",
            "Live parity report covers every required promotion dimension",
            {"required_dimensions": sorted(required_dimensions), "missing_dimensions": missing_dimensions},
        ),
        objective_audit_row(
            "required_dimensions_passed",
            "passed" if not blocking_dimensions else "blocked",
            "Every required real-session parity dimension has passed",
            {"blocking_dimensions": blocking_dimensions, "dimension_statuses": dimension_statuses},
        ),
        objective_audit_row(
            "capture_safe_fail_open_proof",
            capture_proof_row_status,
            "Capture-safe live fail-open proof has been run",
            {
                "report_present": isinstance(capture_regression_check, dict),
                "status": capture_regression_check.get("status") if isinstance(capture_regression_check, dict) else None,
                "mode": capture_regression_check.get("mode") if isinstance(capture_regression_check, dict) else None,
                "capture_safe_proof_status": capture_proof_status,
            },
        ),
        objective_audit_row(
            "batch_authoritative",
            "passed" if promotion_policy.get("batch_authoritative") is True else "blocked",
            "Batch transcript remains authoritative",
            {"batch_authoritative": promotion_policy.get("batch_authoritative")},
        ),
        objective_audit_row(
            "live_promotion_blocked",
            "passed"
            if promotion_policy.get("status") == "blocked" and safe_int(summary.get("promotion_allowed_sessions")) == 0
            else "blocked",
            "Live promotion is blocked until parity gates prove safety",
            {
                "promotion_policy_status": promotion_policy.get("status"),
                "promotion_allowed_sessions": safe_int(summary.get("promotion_allowed_sessions")),
            },
        ),
        objective_audit_row(
            "new_real_live_collection_quarantined",
            "passed" if promotion_policy.get("new_real_live_collection_allowed") is False else "blocked",
            "New real live-pipeline collection remains disabled while live is quarantined",
            {"new_real_live_collection_allowed": promotion_policy.get("new_real_live_collection_allowed")},
        ),
        objective_audit_row(
            "controlled_real_live_pilot_collection",
            "passed" if promotion_policy.get("controlled_real_live_pilot_allowed") is True else "incomplete",
            "Controlled real live-pipeline pilot collection is allowed only after full fail-open proof",
            {
                "controlled_real_live_pilot_allowed": promotion_policy.get("controlled_real_live_pilot_allowed"),
                "capture_safe_proof_status": capture_proof_status,
                "scope": "controlled Live Evidence only; promotion remains blocked and batch authoritative",
            },
        ),
    ]
    statuses = {str(row.get("status")) for row in rows}
    if "blocked" in statuses:
        overall = "blocked_by_parity_gates"
    elif "incomplete" in statuses:
        overall = "incomplete_coverage"
    else:
        overall = "coverage_ready_shadow_locked"
    return {
        "schema": "murmurmark.live_parity_objective_audit/v1",
        "objective": "Near-Realtime Live Parity Coverage v1",
        "overall_status": overall,
        "ready_for_live_promotion": False,
        "batch_authoritative": promotion_policy.get("batch_authoritative") is True,
        "new_real_live_collection_allowed": promotion_policy.get("new_real_live_collection_allowed") is True,
        "controlled_real_live_pilot_allowed": promotion_policy.get("controlled_real_live_pilot_allowed") is True,
        "blocking_dimensions": blocking_dimensions,
        "next_focus": next_focus,
        "next_actions": next_actions,
        "rows": rows,
    }


def sum_metric(rows: list[dict[str, Any]], metric: str) -> float:
    return round(sum(float(((row.get("metrics") or {}).get(metric) or 0.0)) for row in rows), 3)


def sum_int_metric(rows: list[dict[str, Any]], metric: str) -> int:
    return sum(int(((row.get("metrics") or {}).get(metric) or 0)) for row in rows)


def sum_counter_metric(rows: list[dict[str, Any]], metric: str) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
        value = metrics.get(metric)
        if not isinstance(value, dict):
            continue
        for key, count in value.items():
            counts[str(key)] += int(safe_float(count))
    return dict(sorted(counts.items()))


def add_suppressed_mic_rescue_policy_summary(summary: dict[str, Any], rows: list[dict[str, Any]], prefix: str) -> None:
    oracle_local_seconds = sum_metric(rows, "live_rescue_policy_batch_oracle_local_ceiling_local_seconds")
    for policy in SUPPRESSED_MIC_RESCUE_POLICIES:
        base = f"live_rescue_policy_{policy}"
        out = f"{prefix}_live_rescue_policy_{policy}" if prefix else base
        selected_count = sum_int_metric(rows, f"{base}_selected_segment_count")
        selected_seconds = sum_metric(rows, f"{base}_selected_seconds")
        local_seconds = sum_metric(rows, f"{base}_local_seconds")
        remote_risk_seconds = sum_metric(rows, f"{base}_remote_risk_seconds")
        missing_after_seconds = sum_metric(rows, f"{base}_missing_me_seconds_after")
        recovered_seconds = sum_metric(rows, f"{base}_missing_me_recovered_seconds")
        summary[f"{out}_selected_segment_count"] = selected_count
        summary[f"{out}_selected_seconds"] = selected_seconds
        summary[f"{out}_local_seconds"] = local_seconds
        summary[f"{out}_remote_risk_seconds"] = remote_risk_seconds
        summary[f"{out}_missing_me_seconds_after"] = missing_after_seconds
        summary[f"{out}_missing_me_recovered_seconds"] = recovered_seconds
        summary[f"{out}_precision_proxy"] = round(local_seconds / selected_seconds, 6) if selected_seconds > 0 else None
        summary[f"{out}_local_recall_proxy"] = (
            round(local_seconds / oracle_local_seconds, 6) if oracle_local_seconds > 0 else None
        )


def add_target_me_shadow_policy_summary(summary: dict[str, Any], rows: list[dict[str, Any]], prefix: str) -> None:
    for policy in TARGET_ME_RESCUE_POLICIES:
        base = f"live_target_me_shadow_policy_{policy}"
        out = f"{prefix}_live_target_me_shadow_policy_{policy}" if prefix else base
        summary[f"{out}_candidate_segment_count"] = sum_int_metric(rows, f"{base}_candidate_segment_count")
        summary[f"{out}_candidate_seconds"] = sum_metric(rows, f"{base}_candidate_seconds")
        summary[f"{out}_missing_me_seconds_after"] = sum_metric(rows, f"{base}_missing_me_seconds_after")
        summary[f"{out}_missing_me_recovered_seconds"] = sum_metric(rows, f"{base}_missing_me_recovered_seconds")
        summary[f"{out}_suspected_remote_leak_in_me_count"] = sum_int_metric(
            rows,
            f"{base}_suspected_remote_leak_in_me_count",
        )
        summary[f"{out}_suspected_remote_leak_in_me_seconds"] = sum_metric(
            rows,
            f"{base}_suspected_remote_leak_in_me_seconds",
        )
        summary[f"{out}_order_mismatch_count"] = sum_int_metric(rows, f"{base}_order_mismatch_count")
        summary[f"{out}_role_constrained_order_mismatch_count"] = sum_int_metric(
            rows,
            f"{base}_role_constrained_order_mismatch_count",
        )
        summary[f"{out}_contentful_role_constrained_order_mismatch_count"] = sum_int_metric(
            rows,
            f"{base}_contentful_role_constrained_order_mismatch_count",
        )
        summary[f"{out}_order_mismatch_delta_count"] = sum_int_metric(rows, f"{base}_order_mismatch_delta_count")
        summary[f"{out}_role_constrained_order_mismatch_delta_count"] = sum_int_metric(
            rows,
            f"{base}_role_constrained_order_mismatch_delta_count",
        )
        summary[f"{out}_contentful_role_constrained_order_mismatch_delta_count"] = sum_int_metric(
            rows,
            f"{base}_contentful_role_constrained_order_mismatch_delta_count",
        )
        summary[f"{out}_rejected_candidate_count"] = sum_int_metric(rows, f"{base}_rejected_candidate_count")
        summary[f"{out}_rejected_candidate_seconds"] = sum_metric(rows, f"{base}_rejected_candidate_seconds")
        summary[f"{out}_rejected_would_add_contentful_order_mismatch_count"] = sum_int_metric(
            rows,
            f"{base}_rejected_would_add_contentful_order_mismatch_count",
        )
        summary[f"{out}_rejected_would_add_contentful_order_mismatch_seconds"] = sum_metric(
            rows,
            f"{base}_rejected_would_add_contentful_order_mismatch_seconds",
        )
        summary[f"{out}_rejected_would_add_suspected_remote_leak_count"] = sum_int_metric(
            rows,
            f"{base}_rejected_would_add_suspected_remote_leak_count",
        )
        summary[f"{out}_rejected_would_add_suspected_remote_leak_seconds"] = sum_metric(
            rows,
            f"{base}_rejected_would_add_suspected_remote_leak_seconds",
        )


def add_target_me_shadow_profile_summary(summary: dict[str, Any], rows: list[dict[str, Any]], prefix: str) -> None:
    for policy in TARGET_ME_SHADOW_PROFILE_POLICIES:
        base = f"live_target_me_shadow_profile_{policy}"
        out = f"{prefix}_live_target_me_shadow_profile_{policy}" if prefix else base
        evaluated_rows = [
            row
            for row in rows
            if (row.get("metrics") or {}).get(f"{base}_non_passing_gate_count") is not None
        ]
        summary[f"{out}_evaluated_session_count"] = len(evaluated_rows)
        summary[f"{out}_all_parity_gates_passed_session_count"] = sum(
            1 for row in evaluated_rows if (row.get("metrics") or {}).get(f"{base}_all_parity_gates_passed") is True
        )
        summary[f"{out}_non_passing_gate_count"] = sum_int_metric(evaluated_rows, f"{base}_non_passing_gate_count")
        summary[f"{out}_live_missing_me_seconds"] = sum_metric(evaluated_rows, f"{base}_live_missing_me_seconds")
        summary[f"{out}_live_missing_me_visible_in_suppressed_mic_seconds"] = sum_metric(
            evaluated_rows,
            f"{base}_live_missing_me_visible_in_suppressed_mic_seconds",
        )
        summary[f"{out}_live_missing_me_not_visible_in_suppressed_mic_seconds"] = sum_metric(
            evaluated_rows,
            f"{base}_live_missing_me_not_visible_in_suppressed_mic_seconds",
        )
        summary[f"{out}_live_missing_me_with_target_me_candidate_seconds"] = sum_metric(
            evaluated_rows,
            f"{base}_live_missing_me_with_target_me_candidate_seconds",
        )
        summary[f"{out}_live_missing_me_without_target_me_candidate_seconds"] = sum_metric(
            evaluated_rows,
            f"{base}_live_missing_me_without_target_me_candidate_seconds",
        )
        summary[f"{out}_live_missing_me_visible_with_target_me_candidate_seconds"] = sum_metric(
            evaluated_rows,
            f"{base}_live_missing_me_visible_with_target_me_candidate_seconds",
        )
        summary[f"{out}_live_missing_me_visible_without_target_me_candidate_seconds"] = sum_metric(
            evaluated_rows,
            f"{base}_live_missing_me_visible_without_target_me_candidate_seconds",
        )
        summary[f"{out}_live_missing_me_not_visible_with_target_me_candidate_seconds"] = sum_metric(
            evaluated_rows,
            f"{base}_live_missing_me_not_visible_with_target_me_candidate_seconds",
        )
        summary[f"{out}_live_missing_me_not_visible_without_target_me_candidate_seconds"] = sum_metric(
            evaluated_rows,
            f"{base}_live_missing_me_not_visible_without_target_me_candidate_seconds",
        )
        summary[f"{out}_live_suspected_remote_leak_in_me_seconds"] = sum_metric(
            evaluated_rows,
            f"{base}_live_suspected_remote_leak_in_me_seconds",
        )
        summary[f"{out}_live_order_mismatch_count"] = sum_int_metric(
            evaluated_rows,
            f"{base}_live_order_mismatch_count",
        )
        summary[f"{out}_live_contentful_role_constrained_order_mismatch_count"] = sum_int_metric(
            evaluated_rows,
            f"{base}_live_contentful_role_constrained_order_mismatch_count",
        )
        summary[f"{out}_removed_live_turn_count"] = sum_int_metric(
            evaluated_rows,
            f"{base}_removed_live_turn_count",
        )
        summary[f"{out}_removed_live_turn_seconds"] = sum_metric(
            evaluated_rows,
            f"{base}_removed_live_turn_seconds",
        )
        summary[f"{out}_visible_suppressed_mic_added_turn_count"] = sum_int_metric(
            evaluated_rows,
            f"{base}_visible_suppressed_mic_added_turn_count",
        )
        summary[f"{out}_visible_suppressed_mic_added_turn_seconds"] = sum_metric(
            evaluated_rows,
            f"{base}_visible_suppressed_mic_added_turn_seconds",
        )
        summary[f"{out}_visible_suppressed_mic_rejected_turn_count"] = sum_int_metric(
            evaluated_rows,
            f"{base}_visible_suppressed_mic_rejected_turn_count",
        )


def target_me_shadow_policy_diagnostics(summary: dict[str, Any], prefix: str) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    best_safe: dict[str, Any] | None = None
    key_prefix = f"{prefix}_live_target_me_shadow_policy_" if prefix else "live_target_me_shadow_policy_"
    for policy in TARGET_ME_RESCUE_POLICIES:
        base = f"{key_prefix}{policy}"
        candidate_seconds = safe_float(summary.get(f"{base}_candidate_seconds"))
        recovered = safe_float(summary.get(f"{base}_missing_me_recovered_seconds"))
        remote_risk = safe_float(summary.get(f"{base}_suspected_remote_leak_in_me_seconds"))
        order_mismatches = safe_int(summary.get(f"{base}_contentful_role_constrained_order_mismatch_count"))
        order_mismatch_delta = safe_int(summary.get(f"{base}_contentful_role_constrained_order_mismatch_delta_count"))
        row = {
            "policy": policy,
            "candidate_seconds": candidate_seconds,
            "missing_me_recovered_seconds": recovered,
            "suspected_remote_leak_in_me_seconds": remote_risk,
            "contentful_role_constrained_order_mismatch_count": order_mismatches,
            "contentful_role_constrained_order_mismatch_delta_count": order_mismatch_delta,
            "rejected_candidate_count": safe_int(summary.get(f"{base}_rejected_candidate_count")),
            "rejected_candidate_seconds": safe_float(summary.get(f"{base}_rejected_candidate_seconds")),
            "rejected_would_add_contentful_order_mismatch_seconds": safe_float(
                summary.get(f"{base}_rejected_would_add_contentful_order_mismatch_seconds")
            ),
            "rejected_would_add_suspected_remote_leak_seconds": safe_float(
                summary.get(f"{base}_rejected_would_add_suspected_remote_leak_seconds")
            ),
            "safe_candidate": candidate_seconds > 0 and recovered > 0 and remote_risk <= 3.0 and order_mismatch_delta <= 0,
        }
        rows.append(row)
        if row["safe_candidate"] and (best_safe is None or recovered > safe_float(best_safe.get("missing_me_recovered_seconds"))):
            best_safe = row
    status = "safe_candidate_available" if best_safe else "no_safe_candidate"
    return {
        "schema": "murmurmark.live_target_me_shadow_policy_diagnostics/v1",
        "scope": prefix or "all",
        "status": status,
        "recommended_policy": best_safe,
        "policies": rows,
        "recommended_next": (
            "materialize_target_me_shadow_rescue_draft"
            if best_safe
            else "keep_collecting_target_me_evidence_or_add_enrollment_fallback"
        ),
    }


def target_me_shadow_profile_diagnostics(summary: dict[str, Any], prefix: str) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    key_prefix = f"{prefix}_live_target_me_shadow_profile_" if prefix else "live_target_me_shadow_profile_"
    best: dict[str, Any] | None = None
    best_live_implementable: dict[str, Any] | None = None
    for policy in TARGET_ME_SHADOW_PROFILE_POLICIES:
        base = f"{key_prefix}{policy}"
        live_implementable = "batch_remote_forbidden" not in policy
        row = {
            "policy": policy,
            "live_implementable": live_implementable,
            "evaluated_session_count": safe_int(summary.get(f"{base}_evaluated_session_count")),
            "all_parity_gates_passed_session_count": safe_int(
                summary.get(f"{base}_all_parity_gates_passed_session_count")
            ),
            "non_passing_gate_count": safe_int(summary.get(f"{base}_non_passing_gate_count")),
            "live_missing_me_seconds": safe_float(summary.get(f"{base}_live_missing_me_seconds")),
            "live_missing_me_visible_in_suppressed_mic_seconds": safe_float(
                summary.get(f"{base}_live_missing_me_visible_in_suppressed_mic_seconds")
            ),
            "live_missing_me_not_visible_in_suppressed_mic_seconds": safe_float(
                summary.get(f"{base}_live_missing_me_not_visible_in_suppressed_mic_seconds")
            ),
            "live_missing_me_with_target_me_candidate_seconds": safe_float(
                summary.get(f"{base}_live_missing_me_with_target_me_candidate_seconds")
            ),
            "live_missing_me_without_target_me_candidate_seconds": safe_float(
                summary.get(f"{base}_live_missing_me_without_target_me_candidate_seconds")
            ),
            "live_missing_me_visible_with_target_me_candidate_seconds": safe_float(
                summary.get(f"{base}_live_missing_me_visible_with_target_me_candidate_seconds")
            ),
            "live_missing_me_visible_without_target_me_candidate_seconds": safe_float(
                summary.get(f"{base}_live_missing_me_visible_without_target_me_candidate_seconds")
            ),
            "live_missing_me_not_visible_with_target_me_candidate_seconds": safe_float(
                summary.get(f"{base}_live_missing_me_not_visible_with_target_me_candidate_seconds")
            ),
            "live_missing_me_not_visible_without_target_me_candidate_seconds": safe_float(
                summary.get(f"{base}_live_missing_me_not_visible_without_target_me_candidate_seconds")
            ),
            "live_suspected_remote_leak_in_me_seconds": safe_float(
                summary.get(f"{base}_live_suspected_remote_leak_in_me_seconds")
            ),
            "live_order_mismatch_count": safe_int(summary.get(f"{base}_live_order_mismatch_count")),
            "live_contentful_role_constrained_order_mismatch_count": safe_int(
                summary.get(f"{base}_live_contentful_role_constrained_order_mismatch_count")
            ),
            "removed_live_turn_count": safe_int(summary.get(f"{base}_removed_live_turn_count")),
            "removed_live_turn_seconds": safe_float(summary.get(f"{base}_removed_live_turn_seconds")),
            "visible_suppressed_mic_added_turn_count": safe_int(
                summary.get(f"{base}_visible_suppressed_mic_added_turn_count")
            ),
            "visible_suppressed_mic_added_turn_seconds": safe_float(
                summary.get(f"{base}_visible_suppressed_mic_added_turn_seconds")
            ),
            "visible_suppressed_mic_rejected_turn_count": safe_int(
                summary.get(f"{base}_visible_suppressed_mic_rejected_turn_count")
            ),
        }
        rows.append(row)
        if row["evaluated_session_count"] > 0 and (
            best is None
            or (
                row["all_parity_gates_passed_session_count"],
                -row["non_passing_gate_count"],
                -row["live_missing_me_seconds"],
            )
            > (
                safe_int(best.get("all_parity_gates_passed_session_count")),
                -safe_int(best.get("non_passing_gate_count")),
                -safe_float(best.get("live_missing_me_seconds")),
            )
        ):
            best = row
        if live_implementable and row["evaluated_session_count"] > 0 and (
            best_live_implementable is None
            or (
                row["all_parity_gates_passed_session_count"],
                -row["non_passing_gate_count"],
                -row["live_suspected_remote_leak_in_me_seconds"],
                -row["live_contentful_role_constrained_order_mismatch_count"],
                -row["live_missing_me_seconds"],
            )
            > (
                safe_int(best_live_implementable.get("all_parity_gates_passed_session_count")),
                -safe_int(best_live_implementable.get("non_passing_gate_count")),
                -safe_float(best_live_implementable.get("live_suspected_remote_leak_in_me_seconds")),
                -safe_int(best_live_implementable.get("live_contentful_role_constrained_order_mismatch_count")),
                -safe_float(best_live_implementable.get("live_missing_me_seconds")),
            )
        ):
            best_live_implementable = row
    status = "profile_evaluated" if best else "not_evaluated"
    return {
        "schema": "murmurmark.live_target_me_shadow_profile_diagnostics/v1",
        "scope": prefix or "all",
        "status": status,
        "best_profile": best,
        "best_live_implementable_profile": best_live_implementable,
        "profiles": rows,
        "recommended_next": (
            "fix_remaining_parity_gate_blockers_for_materialized_target_me_shadow"
            if best
            else "materialize_target_me_shadow_profiles"
        ),
    }


def rescue_policy_diagnostics(summary: dict[str, Any], prefix: str) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    best_safe: dict[str, Any] | None = None
    best_exploratory: dict[str, Any] | None = None
    key_prefix = f"{prefix}_live_rescue_policy_" if prefix else "live_rescue_policy_"
    for policy in SUPPRESSED_MIC_RESCUE_POLICIES:
        base = f"{key_prefix}{policy}"
        selected_seconds = safe_float(summary.get(f"{base}_selected_seconds"))
        local_seconds = safe_float(summary.get(f"{base}_local_seconds"))
        remote_risk_seconds = safe_float(summary.get(f"{base}_remote_risk_seconds"))
        precision = summary.get(f"{base}_precision_proxy")
        recall = summary.get(f"{base}_local_recall_proxy")
        recovered = safe_float(summary.get(f"{base}_missing_me_recovered_seconds"))
        row = {
            "policy": policy,
            "live_implementable": policy != "batch_oracle_local_ceiling",
            "selected_seconds": selected_seconds,
            "local_seconds": local_seconds,
            "remote_risk_seconds": remote_risk_seconds,
            "precision_proxy": precision,
            "local_recall_proxy": recall,
            "missing_me_recovered_seconds": recovered,
            "safe_candidate": selected_seconds > 0 and remote_risk_seconds <= 3.0 and (precision is None or precision >= 0.90),
            "material_candidate": local_seconds >= MATERIAL_RESCUE_LOCAL_SECONDS,
        }
        rows.append(row)
        if (
            row["live_implementable"]
            and row["safe_candidate"]
            and row["material_candidate"]
            and (best_safe is None or local_seconds > safe_float(best_safe.get("local_seconds")))
        ):
            best_safe = row
        if (
            row["live_implementable"]
            and selected_seconds > 0
            and (best_exploratory is None or local_seconds > safe_float(best_exploratory.get("local_seconds")))
        ):
            best_exploratory = row
    if best_safe:
        status = "safe_candidate_available"
        recommended = best_safe
        best = best_safe
    elif best_exploratory:
        status = "exploratory_only" if safe_float(best_exploratory.get("local_seconds")) >= MATERIAL_RESCUE_LOCAL_SECONDS else "no_material_live_candidate"
        recommended = None
        best = best_exploratory
    else:
        status = "no_candidate"
        recommended = None
        best = None
    return {
        "schema": "murmurmark.live_rescue_policy_diagnostics/v1",
        "scope": prefix or "all",
        "status": status,
        "safe_remote_risk_threshold_sec": 3.0,
        "safe_precision_threshold": 0.90,
        "material_local_seconds_threshold": MATERIAL_RESCUE_LOCAL_SECONDS,
        "recommended_policy": recommended,
        "best_policy": best,
        "policies": rows,
    }


def local_recall_gap_examples(rows: list[dict[str, Any]], *, limit: int = 50) -> dict[str, Any]:
    examples: list[dict[str, Any]] = []
    for row in rows:
        risk_examples = row.get("risk_examples") if isinstance(row.get("risk_examples"), dict) else {}
        inputs = row.get("inputs") if isinstance(row.get("inputs"), dict) else {}
        metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
        for category in ("local_missing", "local_missing_suspicious_batch_me"):
            for item in risk_examples.get(category) or []:
                if not isinstance(item, dict):
                    continue
                examples.append(
                    {
                        "session": row.get("session"),
                        "category": category,
                        "batch_id": item.get("batch_id"),
                        "start": item.get("start"),
                        "end": item.get("end"),
                        "duration_sec": item.get("duration_sec"),
                        "recall_in_live_me": item.get("recall_in_live_me"),
                        "recall_in_suppressed_mic": item.get("recall_in_suppressed_mic"),
                        "suppressed_mic_turn_ids": item.get("suppressed_mic_turn_ids"),
                        "reason": item.get("reason"),
                        "text": item.get("text"),
                        "comparison": inputs.get("live_batch_comparison"),
                        "live_me_turn_count": metrics.get("live_me_turn_count"),
                        "batch_me_utterance_count": metrics.get("batch_me_utterance_count"),
                    }
                )
    examples.sort(
        key=lambda item: (
            -safe_float(item.get("duration_sec")),
            str(item.get("session") or ""),
            safe_float(item.get("start")),
        )
    )
    total_seconds = round(sum(safe_float(item.get("duration_sec")) for item in examples), 3)
    return {
        "schema": "murmurmark.live_local_recall_gap_examples/v1",
        "item_count": len(examples),
        "seconds": total_seconds,
        "examples": examples[:limit],
        "truncated": len(examples) > limit,
        "limit": limit,
    }


def local_recall_rescue_lab(rows: list[dict[str, Any]], *, limit: int = 30) -> dict[str, Any]:
    segments: list[dict[str, Any]] = []
    for row in rows:
        risk_examples = row.get("risk_examples") if isinstance(row.get("risk_examples"), dict) else {}
        inputs = row.get("inputs") if isinstance(row.get("inputs"), dict) else {}
        for item in risk_examples.get("suppressed_mic_asr_segments") or []:
            if not isinstance(item, dict):
                continue
            segment = dict(item)
            segment["session"] = row.get("session")
            segment["comparison"] = inputs.get("live_batch_comparison")
            segments.append(segment)

    def duration(item: dict[str, Any]) -> float:
        return safe_float(item.get("duration_sec"))

    def is_local(item: dict[str, Any]) -> bool:
        return item.get("batch_role_label") in {"me_dominant", "mixed"}

    def live_policies(item: dict[str, Any]) -> list[str]:
        policies = item.get("rescue_policy_candidates")
        if not isinstance(policies, list):
            return []
        return [str(policy) for policy in policies if str(policy) in LIVE_IMPLEMENTABLE_RESCUE_POLICIES]

    def has_live_policy(item: dict[str, Any]) -> bool:
        return bool(live_policies(item))

    local_segments = [item for item in segments if is_local(item)]
    live_selected = [item for item in segments if has_live_policy(item)]
    live_selected_local = [item for item in live_selected if is_local(item)]
    live_selected_remote_risk = [item for item in live_selected if not is_local(item)]
    speaker_needed = [item for item in local_segments if not has_live_policy(item)]
    text_duplicate_blocked_local = [
        item for item in speaker_needed
        if safe_float(item.get("segment_gate_mic_token_recall_in_overlapping_remote")) >= 0.70
        or safe_int(item.get("segment_gate_unique_token_count")) <= 2
    ]
    high_value_unrescued = sorted(
        speaker_needed,
        key=lambda item: (-duration(item), str(item.get("session") or ""), safe_float(item.get("start"))),
    )
    false_positive_remote_risk = sorted(
        live_selected_remote_risk,
        key=lambda item: (-duration(item), str(item.get("session") or ""), safe_float(item.get("start"))),
    )

    by_label_seconds: dict[str, float] = {}
    for item in segments:
        label = str(item.get("batch_role_label") or "unknown")
        by_label_seconds[label] = round(by_label_seconds.get(label, 0.0) + duration(item), 3)

    policy_seconds: dict[str, dict[str, Any]] = {}
    for policy in LIVE_IMPLEMENTABLE_RESCUE_POLICIES:
        selected = [
            item for item in segments
            if policy in (item.get("rescue_policy_candidates") or [])
        ]
        selected_seconds = sum(duration(item) for item in selected)
        local_seconds = sum(duration(item) for item in selected if is_local(item))
        remote_risk_seconds = selected_seconds - local_seconds
        policy_seconds[policy] = {
            "selected_count": len(selected),
            "selected_seconds": round(selected_seconds, 3),
            "local_seconds": round(local_seconds, 3),
            "remote_risk_seconds": round(remote_risk_seconds, 3),
            "precision_proxy": round(local_seconds / selected_seconds, 6) if selected_seconds > 0 else None,
        }

    recommended_next = "no_suppressed_mic_local_recall_gap"
    if sum(duration(item) for item in speaker_needed) >= 30.0:
        recommended_next = "add_target_speaker_or_stronger_local_evidence_for_suppressed_mic_segments"
    elif live_selected_remote_risk:
        recommended_next = "tighten_live_rescue_remote_forbidden_gate"
    elif live_selected_local:
        recommended_next = "evaluate_shadow_rescue_policy_on_capture_safe_candidates"

    def example(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "session": item.get("session"),
            "chunk_index": item.get("chunk_index"),
            "start": item.get("start"),
            "end": item.get("end"),
            "duration_sec": item.get("duration_sec"),
            "batch_role_label": item.get("batch_role_label"),
            "rescue_policy_candidates": item.get("rescue_policy_candidates") or [],
            "token_count": item.get("token_count"),
            "unique_token_count": item.get("segment_gate_unique_token_count"),
            "mic_token_recall_in_overlapping_remote": item.get(
                "segment_gate_mic_token_recall_in_overlapping_remote"
            ),
            "overlapping_remote_token_recall_in_mic": item.get(
                "segment_gate_overlapping_remote_token_recall_in_mic"
            ),
            "audio_mic_clean_rms_db": item.get("audio_mic_clean_rms_db"),
            "audio_remote_rms_db": item.get("audio_remote_rms_db"),
            "audio_mic_remote_zero_lag_abs_corr": item.get("audio_mic_remote_zero_lag_abs_corr"),
            "comparison": item.get("comparison"),
            "text": item.get("text"),
        }

    return {
        "schema": "murmurmark.live_local_recall_rescue_lab/v1",
        "segment_count": len(segments),
        "segment_seconds": round(sum(duration(item) for item in segments), 3),
        "local_segment_count": len(local_segments),
        "local_seconds": round(sum(duration(item) for item in local_segments), 3),
        "by_batch_role_seconds": by_label_seconds,
        "live_policy_selected_count": len(live_selected),
        "live_policy_selected_seconds": round(sum(duration(item) for item in live_selected), 3),
        "live_policy_local_seconds": round(sum(duration(item) for item in live_selected_local), 3),
        "live_policy_remote_risk_seconds": round(sum(duration(item) for item in live_selected_remote_risk), 3),
        "speaker_evidence_needed_count": len(speaker_needed),
        "speaker_evidence_needed_seconds": round(sum(duration(item) for item in speaker_needed), 3),
        "text_duplicate_blocked_local_count": len(text_duplicate_blocked_local),
        "text_duplicate_blocked_local_seconds": round(sum(duration(item) for item in text_duplicate_blocked_local), 3),
        "false_positive_remote_risk_count": len(false_positive_remote_risk),
        "false_positive_remote_risk_seconds": round(sum(duration(item) for item in false_positive_remote_risk), 3),
        "policy_seconds": policy_seconds,
        "recommended_next": recommended_next,
        "examples": {
            "high_value_unrescued": [example(item) for item in high_value_unrescued[:limit]],
            "false_positive_remote_risk": [example(item) for item in false_positive_remote_risk[:limit]],
            "live_policy_local": [
                example(item)
                for item in sorted(
                    live_selected_local,
                    key=lambda row: (-duration(row), str(row.get("session") or ""), safe_float(row.get("start"))),
                )[:limit]
            ],
        },
    }


def target_me_policy_metrics_from_summaries(summaries: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    total_local_seconds = round(sum(safe_float(row.get("local_seconds")) for row in summaries), 3)
    result: dict[str, dict[str, Any]] = {}
    for policy in TARGET_ME_RESCUE_POLICIES:
        selected_count = 0
        selected_seconds = 0.0
        local_seconds = 0.0
        remote_risk_seconds = 0.0
        missing_recovered_seconds = 0.0
        missing_after_seconds = 0.0
        for row in summaries:
            root = row.get("target_me_rescue_policy_metrics")
            metrics = root.get(policy) if isinstance(root, dict) else None
            if not isinstance(metrics, dict):
                continue
            selected_count += safe_int(metrics.get("selected_count"))
            selected_seconds += safe_float(metrics.get("selected_seconds"))
            local_seconds += safe_float(metrics.get("local_seconds"))
            remote_risk_seconds += safe_float(metrics.get("remote_risk_seconds"))
            missing_recovered_seconds += safe_float(metrics.get("missing_me_recovered_seconds"))
            missing_after_seconds += safe_float(metrics.get("missing_me_seconds_after"))
        result[policy] = {
            "selected_count": selected_count,
            "selected_seconds": round(selected_seconds, 3),
            "local_seconds": round(local_seconds, 3),
            "remote_risk_seconds": round(remote_risk_seconds, 3),
            "precision_proxy": round(local_seconds / selected_seconds, 6) if selected_seconds > 0 else None,
            "audited_local_recall_proxy": (
                round(local_seconds / total_local_seconds, 6) if total_local_seconds > 0 else None
            ),
            "missing_me_recovered_seconds": round(missing_recovered_seconds, 3),
            "missing_me_seconds_after": round(missing_after_seconds, 3),
        }
    return result


def live_local_recall_target_me_diagnostics(rows: list[dict[str, Any]], scope: str) -> dict[str, Any]:
    summaries = [
        row.get("live_local_recall_target_me")
        for row in rows
        if isinstance(row.get("live_local_recall_target_me"), dict)
    ]
    summaries = [row for row in summaries if isinstance(row, dict)]
    by_status = Counter(str(row.get("status") or "unknown") for row in summaries)
    policy_metrics = target_me_policy_metrics_from_summaries(summaries)
    best_policy: dict[str, Any] | None = None
    recommended_policy: dict[str, Any] | None = None
    for policy, metrics in policy_metrics.items():
        row = {
            "policy": policy,
            **metrics,
            "safe_candidate": (
                safe_float(metrics.get("selected_seconds")) > 0
                and safe_float(metrics.get("remote_risk_seconds")) <= 3.0
                and (
                    metrics.get("precision_proxy") is None
                    or safe_float(metrics.get("precision_proxy")) >= 0.90
                )
            ),
            "material_candidate": safe_float(metrics.get("local_seconds")) >= MATERIAL_RESCUE_LOCAL_SECONDS,
        }
        if (
            safe_float(row.get("selected_seconds")) > 0
            and (best_policy is None or safe_float(row.get("local_seconds")) > safe_float(best_policy.get("local_seconds")))
        ):
            best_policy = row
        if (
            row["safe_candidate"]
            and row["material_candidate"]
            and (
                recommended_policy is None
                or safe_float(row.get("local_seconds")) > safe_float(recommended_policy.get("local_seconds"))
            )
        ):
            recommended_policy = row
    target_me_possible_local = round(
        sum(safe_float(row.get("target_me_possible_or_confirmed_local_seconds")) for row in summaries),
        3,
    )
    status = "missing"
    recommended_next = "run_live_local_recall_target_me_audit"
    if summaries:
        status = "has_shadow_evidence"
        recommended_next = "design_target_me_gated_shadow_rescue_policy"
    if recommended_policy:
        status = "safe_candidate_available"
    elif summaries and target_me_possible_local < MATERIAL_RESCUE_LOCAL_SECONDS:
        status = "no_material_target_me_candidate"
        recommended_next = "keep_collecting_target_me_evidence"
    if by_status.get("insufficient_enrollment", 0) and target_me_possible_local >= MATERIAL_RESCUE_LOCAL_SECONDS:
        recommended_next = "design_target_me_gated_shadow_rescue_policy_then_add_enrollment_fallback"

    return {
        "schema": "murmurmark.live_local_recall_target_me_diagnostics/v1",
        "scope": scope,
        "status": status,
        "sessions_with_audit": len(summaries),
        "by_status": dict(sorted(by_status.items())),
        "items": sum(safe_int(row.get("items")) for row in summaries),
        "total_seconds": round(sum(safe_float(row.get("total_seconds")) for row in summaries), 3),
        "local_seconds": round(sum(safe_float(row.get("local_seconds")) for row in summaries), 3),
        "target_me_confirmed_local_seconds": round(
            sum(safe_float(row.get("target_me_confirmed_local_seconds")) for row in summaries),
            3,
        ),
        "target_me_possible_or_confirmed_local_seconds": target_me_possible_local,
        "remote_risk_seconds": round(sum(safe_float(row.get("remote_risk_seconds")) for row in summaries), 3),
        "target_me_rejected_remote_risk_seconds": round(
            sum(safe_float(row.get("target_me_rejected_remote_risk_seconds")) for row in summaries),
            3,
        ),
        "target_me_rescue_policy_metrics": policy_metrics,
        "recommended_policy": recommended_policy,
        "best_policy": best_policy,
        "recommended_next": recommended_next,
        "promotion_decision": "shadow_only_do_not_promote",
    }


def summarize_session(session: Path, root: Path) -> dict[str, Any]:
    live_report_path = session / "derived/live/live_pipeline_report.json"
    comparison_path = session / "derived/live/live_batch_comparison.json"
    session_report_path = session / "derived/live/live_parity_session_report.json"
    final_reconcile_path = session / "derived/live/final_reconcile_report.json"
    target_me_summary_path = (
        session / "derived/audit/live-local-recall-target-me/live_local_recall_target_me_summary.json"
    )
    live_report = read_json(live_report_path)
    comparison = read_json(comparison_path)
    final_reconcile = read_json(final_reconcile_path)
    target_me_summary = read_json(target_me_summary_path)
    live_present = live_report is not None
    blockers: list[str] = []
    if not live_present:
        blockers.append("live_report_missing")
    if live_present and comparison is None:
        blockers.append("live_batch_comparison_missing")
    metrics = comparison.get("metrics") if isinstance(comparison, dict) else {}
    risk_examples = comparison.get("risk_examples") if isinstance(comparison, dict) else {}
    risk_examples = risk_examples if isinstance(risk_examples, dict) else {}
    parity = comparison.get("parity_gates") if isinstance(comparison, dict) else {}
    comparison_status = comparison.get("status") if isinstance(comparison, dict) else None
    comparison_gates = gate_rows(comparison)
    capture_gate = next((gate for gate in comparison_gates if gate.get("name") == "capture_safety"), None)
    if live_present and capture_gate is None:
        capture_gate = build_capture_safety_gate(session)
        gates = [capture_gate] + comparison_gates
    else:
        gates = comparison_gates
    return {
        "session": rel(session, root),
        "evidence_scope": evidence_scope(session, root),
        "live_present": live_present,
        "live_status": live_report.get("status") if isinstance(live_report, dict) else None,
        "comparison_status": comparison_status,
        "parity_status": parity.get("status") if isinstance(parity, dict) else None,
        "meaningful_compared": meaningful_comparison(metrics if isinstance(metrics, dict) else {}, comparison_status),
        "all_parity_gates_passed": bool(gates) and all(gate.get("status") == "passed" for gate in gates),
        "promotion_allowed": bool(comparison.get("promotion_allowed")) if isinstance(comparison, dict) else False,
        "promotion_blockers": comparison.get("promotion_blockers") if isinstance(comparison, dict) else blockers,
        "blockers": blockers + list(comparison.get("blockers") or []) if isinstance(comparison, dict) else blockers,
        "warnings": comparison.get("warnings") if isinstance(comparison, dict) else [],
        "metrics": {
            "live_chunks": metrics.get("live_chunks") if isinstance(metrics, dict) else None,
            "live_token_recall_in_batch": metrics.get("live_token_recall_in_batch") if isinstance(metrics, dict) else None,
            "adjacent_duplicate_chunk_count": metrics.get("adjacent_duplicate_chunk_count") if isinstance(metrics, dict) else None,
            "live_boundary_gate_issue_count": (
                metrics.get("live_boundary_gate_issue_count") if isinstance(metrics, dict) else None
            ),
            "live_boundary_gate_suppressed_count": (
                metrics.get("live_boundary_gate_suppressed_count") if isinstance(metrics, dict) else None
            ),
            "live_boundary_gate_resolved_suppressed_count": (
                metrics.get("live_boundary_gate_resolved_suppressed_count") if isinstance(metrics, dict) else None
            ),
            "live_boundary_gate_unresolved_suppressed_count": (
                metrics.get("live_boundary_gate_unresolved_suppressed_count") if isinstance(metrics, dict) else None
            ),
            "live_order_mismatch_count": metrics.get("live_order_mismatch_count") if isinstance(metrics, dict) else None,
            "live_order_mismatch_by_category": (
                metrics.get("live_order_mismatch_by_category") if isinstance(metrics.get("live_order_mismatch_by_category"), dict) else {}
            )
            if isinstance(metrics, dict)
            else {},
            "live_order_mismatch_by_primary_risk": (
                metrics.get("live_order_mismatch_by_primary_risk")
                if isinstance(metrics.get("live_order_mismatch_by_primary_risk"), dict)
                else {}
            )
            if isinstance(metrics, dict)
            else {},
            "live_order_mismatch_by_confidence": (
                metrics.get("live_order_mismatch_by_confidence")
                if isinstance(metrics.get("live_order_mismatch_by_confidence"), dict)
                else {}
            )
            if isinstance(metrics, dict)
            else {},
            "live_role_constrained_order_mismatch_count": (
                metrics.get("live_role_constrained_order_mismatch_count") if isinstance(metrics, dict) else None
            ),
            "live_role_constrained_order_mismatch_by_category": (
                metrics.get("live_role_constrained_order_mismatch_by_category")
                if isinstance(metrics.get("live_role_constrained_order_mismatch_by_category"), dict)
                else {}
            )
            if isinstance(metrics, dict)
            else {},
            "live_role_constrained_order_mismatch_by_confidence": (
                metrics.get("live_role_constrained_order_mismatch_by_confidence")
                if isinstance(metrics.get("live_role_constrained_order_mismatch_by_confidence"), dict)
                else {}
            )
            if isinstance(metrics, dict)
            else {},
            "live_contentful_role_constrained_order_mismatch_count": (
                metrics.get("live_contentful_role_constrained_order_mismatch_count")
                if isinstance(metrics, dict)
                else None
            ),
            "live_contentful_role_constrained_order_mismatch_by_category": (
                metrics.get("live_contentful_role_constrained_order_mismatch_by_category")
                if isinstance(metrics.get("live_contentful_role_constrained_order_mismatch_by_category"), dict)
                else {}
            )
            if isinstance(metrics, dict)
            else {},
            "live_contentful_role_constrained_order_mismatch_by_confidence": (
                metrics.get("live_contentful_role_constrained_order_mismatch_by_confidence")
                if isinstance(metrics.get("live_contentful_role_constrained_order_mismatch_by_confidence"), dict)
                else {}
            )
            if isinstance(metrics, dict)
            else {},
            "live_contentful_role_constrained_order_mismatch_by_ambiguity": (
                metrics.get("live_contentful_role_constrained_order_mismatch_by_ambiguity")
                if isinstance(metrics.get("live_contentful_role_constrained_order_mismatch_by_ambiguity"), dict)
                else {}
            )
            if isinstance(metrics, dict)
            else {},
            "live_unambiguous_contentful_role_constrained_order_mismatch_count": (
                metrics.get("live_unambiguous_contentful_role_constrained_order_mismatch_count")
                if isinstance(metrics, dict)
                else None
            ),
            "live_missing_me_seconds": metrics.get("live_missing_me_seconds") if isinstance(metrics, dict) else None,
            "live_suspicious_batch_me_missing_seconds": (
                metrics.get("live_suspicious_batch_me_missing_seconds") if isinstance(metrics, dict) else None
            ),
            "live_missing_me_visible_in_suppressed_mic_seconds": (
                metrics.get("live_missing_me_visible_in_suppressed_mic_seconds") if isinstance(metrics, dict) else None
            ),
            "live_missing_me_not_visible_in_suppressed_mic_seconds": (
                metrics.get("live_missing_me_not_visible_in_suppressed_mic_seconds") if isinstance(metrics, dict) else None
            ),
            "live_suppressed_mic_turn_count": (
                metrics.get("live_suppressed_mic_turn_count") if isinstance(metrics, dict) else None
            ),
            "live_segment_role_gate_candidate_chunk_count": (
                metrics.get("live_segment_role_gate_candidate_chunk_count") if isinstance(metrics, dict) else None
            ),
            "live_segment_role_gate_candidate_kept_segment_count": (
                metrics.get("live_segment_role_gate_candidate_kept_segment_count") if isinstance(metrics, dict) else None
            ),
            "live_segment_role_gate_candidate_suppressed_segment_count": (
                metrics.get("live_segment_role_gate_candidate_suppressed_segment_count") if isinstance(metrics, dict) else None
            ),
            "live_rescue_shadow_candidate_chunk_count": (
                metrics.get("live_rescue_shadow_candidate_chunk_count") if isinstance(metrics, dict) else None
            ),
            "live_rescue_shadow_candidate_segment_count": (
                metrics.get("live_rescue_shadow_candidate_segment_count") if isinstance(metrics, dict) else None
            ),
            "live_rescue_shadow_candidate_token_count": (
                metrics.get("live_rescue_shadow_candidate_token_count") if isinstance(metrics, dict) else None
            ),
            "live_rescue_shadow_order_mismatch_count": (
                metrics.get("live_rescue_shadow_order_mismatch_count") if isinstance(metrics, dict) else None
            ),
            "live_rescue_shadow_order_mismatch_by_category": (
                metrics.get("live_rescue_shadow_order_mismatch_by_category")
                if isinstance(metrics.get("live_rescue_shadow_order_mismatch_by_category"), dict)
                else {}
            )
            if isinstance(metrics, dict)
            else {},
            "live_rescue_shadow_order_mismatch_by_primary_risk": (
                metrics.get("live_rescue_shadow_order_mismatch_by_primary_risk")
                if isinstance(metrics.get("live_rescue_shadow_order_mismatch_by_primary_risk"), dict)
                else {}
            )
            if isinstance(metrics, dict)
            else {},
            "live_rescue_shadow_order_mismatch_by_confidence": (
                metrics.get("live_rescue_shadow_order_mismatch_by_confidence")
                if isinstance(metrics.get("live_rescue_shadow_order_mismatch_by_confidence"), dict)
                else {}
            )
            if isinstance(metrics, dict)
            else {},
            "live_rescue_shadow_role_constrained_order_mismatch_count": (
                metrics.get("live_rescue_shadow_role_constrained_order_mismatch_count")
                if isinstance(metrics, dict)
                else None
            ),
            "live_rescue_shadow_role_constrained_order_mismatch_by_category": (
                metrics.get("live_rescue_shadow_role_constrained_order_mismatch_by_category")
                if isinstance(metrics.get("live_rescue_shadow_role_constrained_order_mismatch_by_category"), dict)
                else {}
            )
            if isinstance(metrics, dict)
            else {},
            "live_rescue_shadow_role_constrained_order_mismatch_by_confidence": (
                metrics.get("live_rescue_shadow_role_constrained_order_mismatch_by_confidence")
                if isinstance(metrics.get("live_rescue_shadow_role_constrained_order_mismatch_by_confidence"), dict)
                else {}
            )
            if isinstance(metrics, dict)
            else {},
            "live_rescue_shadow_contentful_role_constrained_order_mismatch_count": (
                metrics.get("live_rescue_shadow_contentful_role_constrained_order_mismatch_count")
                if isinstance(metrics, dict)
                else None
            ),
            "live_rescue_shadow_contentful_role_constrained_order_mismatch_by_category": (
                metrics.get("live_rescue_shadow_contentful_role_constrained_order_mismatch_by_category")
                if isinstance(metrics.get("live_rescue_shadow_contentful_role_constrained_order_mismatch_by_category"), dict)
                else {}
            )
            if isinstance(metrics, dict)
            else {},
            "live_rescue_shadow_contentful_role_constrained_order_mismatch_by_confidence": (
                metrics.get("live_rescue_shadow_contentful_role_constrained_order_mismatch_by_confidence")
                if isinstance(metrics.get("live_rescue_shadow_contentful_role_constrained_order_mismatch_by_confidence"), dict)
                else {}
            )
            if isinstance(metrics, dict)
            else {},
            "live_rescue_shadow_contentful_role_constrained_order_mismatch_by_ambiguity": (
                metrics.get("live_rescue_shadow_contentful_role_constrained_order_mismatch_by_ambiguity")
                if isinstance(metrics.get("live_rescue_shadow_contentful_role_constrained_order_mismatch_by_ambiguity"), dict)
                else {}
            )
            if isinstance(metrics, dict)
            else {},
            "live_rescue_shadow_unambiguous_contentful_role_constrained_order_mismatch_count": (
                metrics.get("live_rescue_shadow_unambiguous_contentful_role_constrained_order_mismatch_count")
                if isinstance(metrics, dict)
                else None
            ),
            "live_rescue_shadow_suspected_remote_leak_in_me_seconds": (
                metrics.get("live_rescue_shadow_suspected_remote_leak_in_me_seconds") if isinstance(metrics, dict) else None
            ),
            "live_rescue_shadow_missing_me_seconds_after": (
                metrics.get("live_rescue_shadow_missing_me_seconds_after") if isinstance(metrics, dict) else None
            ),
            "live_rescue_shadow_missing_me_recovered_seconds": (
                metrics.get("live_rescue_shadow_missing_me_recovered_seconds") if isinstance(metrics, dict) else None
            ),
            "live_suppressed_mic_asr_segment_count": (
                metrics.get("live_suppressed_mic_asr_segment_count") if isinstance(metrics, dict) else None
            ),
            "live_suppressed_mic_asr_segment_seconds": (
                metrics.get("live_suppressed_mic_asr_segment_seconds") if isinstance(metrics, dict) else None
            ),
            "live_suppressed_mic_asr_me_dominant_segment_count": (
                metrics.get("live_suppressed_mic_asr_me_dominant_segment_count") if isinstance(metrics, dict) else None
            ),
            "live_suppressed_mic_asr_me_dominant_segment_seconds": (
                metrics.get("live_suppressed_mic_asr_me_dominant_segment_seconds") if isinstance(metrics, dict) else None
            ),
            "live_suppressed_mic_asr_mixed_segment_count": (
                metrics.get("live_suppressed_mic_asr_mixed_segment_count") if isinstance(metrics, dict) else None
            ),
            "live_suppressed_mic_asr_mixed_segment_seconds": (
                metrics.get("live_suppressed_mic_asr_mixed_segment_seconds") if isinstance(metrics, dict) else None
            ),
            **suppressed_mic_rescue_policy_metric_values(metrics if isinstance(metrics, dict) else None),
            **target_me_shadow_policy_metric_values(metrics if isinstance(metrics, dict) else None),
            **target_me_shadow_profile_metric_values(metrics if isinstance(metrics, dict) else None),
            "live_suspected_remote_leak_in_me_seconds": (
                metrics.get("live_suspected_remote_leak_in_me_seconds") if isinstance(metrics, dict) else None
            ),
            "live_turn_count": metrics.get("live_turn_count") if isinstance(metrics, dict) else None,
            "live_me_turn_count": metrics.get("live_me_turn_count") if isinstance(metrics, dict) else None,
            "live_remote_turn_count": metrics.get("live_remote_turn_count") if isinstance(metrics, dict) else None,
            "batch_utterance_count": metrics.get("batch_utterance_count") if isinstance(metrics, dict) else None,
            "batch_me_utterance_count": metrics.get("batch_me_utterance_count") if isinstance(metrics, dict) else None,
            "batch_remote_utterance_count": metrics.get("batch_remote_utterance_count") if isinstance(metrics, dict) else None,
            "batch_ready_for_notes": metrics.get("batch_ready_for_notes") if isinstance(metrics, dict) else None,
            "capture_safety_status": capture_gate.get("status") if isinstance(capture_gate, dict) else None,
            "screen_capture_restart_count": (
                (capture_gate.get("evidence") or {}).get("screen_capture_restart_count")
                if isinstance(capture_gate, dict)
                else None
            ),
            "capture_safety_warning_count": (
                (capture_gate.get("evidence") or {}).get("safety_warning_count")
                if isinstance(capture_gate, dict)
                else None
            ),
        },
        "final_reconcile": {
            "status": final_reconcile.get("status") if isinstance(final_reconcile, dict) else None,
            "speedup_status": final_reconcile.get("speedup_status") if isinstance(final_reconcile, dict) else None,
            "live_cache_reuse": final_reconcile.get("live_cache_reuse") if isinstance(final_reconcile, dict) else None,
        },
        "live_local_recall_target_me": target_me_summary if isinstance(target_me_summary, dict) else None,
        "inputs": {
            "live_report": rel(live_report_path, session) if live_report_path.exists() else None,
            "live_batch_comparison": rel(comparison_path, session) if comparison_path.exists() else None,
            "live_parity_session_report": rel(session_report_path, session) if session_report_path.exists() else None,
            "final_reconcile_report": rel(final_reconcile_path, session) if final_reconcile_path.exists() else None,
            "live_local_recall_target_me_summary": (
                rel(target_me_summary_path, session) if target_me_summary_path.exists() else None
            ),
        },
        "risk_examples": {
            "local_missing": risk_examples.get("local_missing") if isinstance(risk_examples.get("local_missing"), list) else [],
            "local_missing_suspicious_batch_me": (
                risk_examples.get("local_missing_suspicious_batch_me")
                if isinstance(risk_examples.get("local_missing_suspicious_batch_me"), list)
                else []
            ),
            "local_missing_visible_in_suppressed_mic": (
                risk_examples.get("local_missing_visible_in_suppressed_mic")
                if isinstance(risk_examples.get("local_missing_visible_in_suppressed_mic"), list)
                else []
            ),
            "local_missing_not_visible_in_suppressed_mic": (
                risk_examples.get("local_missing_not_visible_in_suppressed_mic")
                if isinstance(risk_examples.get("local_missing_not_visible_in_suppressed_mic"), list)
                else []
            ),
            "remote_leak": risk_examples.get("remote_leak") if isinstance(risk_examples.get("remote_leak"), list) else [],
            "live_rescue_shadow_remote_leak": (
                risk_examples.get("live_rescue_shadow_remote_leak")
                if isinstance(risk_examples.get("live_rescue_shadow_remote_leak"), list)
                else []
            ),
            "live_rescue_shadow_order_mismatches": (
                risk_examples.get("live_rescue_shadow_order_mismatches")
                if isinstance(risk_examples.get("live_rescue_shadow_order_mismatches"), list)
                else []
            ),
            "suppressed_mic_asr_segments": (
                risk_examples.get("suppressed_mic_asr_segments")
                if isinstance(risk_examples.get("suppressed_mic_asr_segments"), list)
                else []
            ),
            "segment_role_gate_candidates": (
                risk_examples.get("segment_role_gate_candidates")
                if isinstance(risk_examples.get("segment_role_gate_candidates"), list)
                else []
            ),
            "live_rescue_shadow": (
                risk_examples.get("live_rescue_shadow")
                if isinstance(risk_examples.get("live_rescue_shadow"), list)
                else []
            ),
            "suppressed_mic_rescue_policies": (
                risk_examples.get("suppressed_mic_rescue_policies")
                if isinstance(risk_examples.get("suppressed_mic_rescue_policies"), dict)
                else {}
            ),
            "live_target_me_shadow": (
                risk_examples.get("live_target_me_shadow")
                if isinstance(risk_examples.get("live_target_me_shadow"), dict)
                else {}
            ),
        },
        "gates": gates,
        "non_passing_gates": non_passing_gate_rows(gates),
        "parity_dimensions": dimension_statuses(gates),
    }


def build_report(sessions: list[Path], root: Path, args: argparse.Namespace) -> dict[str, Any]:
    refresh_results = refresh_live_comparisons(sessions) if args.refresh else []
    refresh_attempted = refresh_results
    refresh_failed = [row for row in refresh_results if row.get("status") == "failed"]
    refresh_skipped = max(0, len(sessions) - len(refresh_attempted)) if args.refresh else 0
    refresh_status = "not_requested"
    if args.refresh:
        refresh_status = "failed" if refresh_failed else "passed"
    rows = [summarize_session(session, root) for session in sessions]
    live_rows = [row for row in rows if row["live_present"]]
    real_live_rows = [row for row in live_rows if row.get("evidence_scope") == "real_meeting"]
    diagnostic_live_rows = [row for row in live_rows if row.get("evidence_scope") != "real_meeting"]
    gate_counts: dict[str, Counter[str]] = defaultdict(Counter)
    real_gate_counts: dict[str, Counter[str]] = defaultdict(Counter)
    blockers = Counter()
    warnings = Counter()
    for row in rows:
        for blocker in row.get("blockers") or []:
            blockers[str(blocker)] += 1
        for warning in row.get("warnings") or []:
            warnings[str(warning)] += 1
        for gate in row.get("gates") or []:
            gate_counts[str(gate.get("name") or "unknown")][str(gate.get("status") or "unknown")] += 1
    for row in real_live_rows:
        for gate in row.get("gates") or []:
            real_gate_counts[str(gate.get("name") or "unknown")][str(gate.get("status") or "unknown")] += 1
    dimension_counts, dimension_issue_sessions = summarize_dimensions(live_rows)
    real_dimension_counts, real_dimension_issue_sessions = summarize_dimensions(real_live_rows)
    capture_safe_candidate_rows = [row for row in real_live_rows if capture_safe_candidate_row(row)]
    capture_safe_evaluable_rows = [row for row in real_live_rows if capture_safe_evaluable_row(row)]
    candidate_dimension_counts, candidate_dimension_issue_sessions = summarize_dimensions(capture_safe_candidate_rows)
    candidate_blocking_dimensions = blocking_dimensions_from_counts(candidate_dimension_counts)
    promotable = [row for row in rows if row.get("promotion_allowed")]
    not_promotable = [row for row in live_rows if row.get("parity_status") != "passed_but_shadow_locked"]
    summary = {
        "sessions_total": len(rows),
        "live_sessions": len(live_rows),
        "real_live_sessions": len(real_live_rows),
        "diagnostic_live_sessions": len(diagnostic_live_rows),
        "live_comparison_refresh_status": refresh_status,
        "live_comparison_refresh_attempted_sessions": len(refresh_attempted),
        "live_comparison_refresh_failed_sessions": len(refresh_failed),
        "live_comparison_refresh_skipped_sessions": refresh_skipped,
        "compared_sessions": sum(1 for row in rows if row.get("comparison_status") == "shadow_compared"),
        "real_compared_sessions": sum(1 for row in real_live_rows if row.get("comparison_status") == "shadow_compared"),
        "meaningful_compared_sessions": sum(1 for row in rows if row.get("meaningful_compared")),
        "real_meaningful_compared_sessions": sum(1 for row in real_live_rows if row.get("meaningful_compared")),
        "passing_compared_sessions": sum(1 for row in rows if row.get("all_parity_gates_passed")),
        "real_passing_compared_sessions": sum(1 for row in real_live_rows if row.get("all_parity_gates_passed")),
        "real_capture_safe_candidate_sessions": len(capture_safe_candidate_rows),
        "real_capture_safe_candidate_passing_sessions": sum(
            1 for row in capture_safe_candidate_rows if row.get("all_parity_gates_passed")
        ),
        "real_capture_safe_evaluable_sessions": len(capture_safe_evaluable_rows),
        "real_capture_safe_candidate_blocking_dimensions": candidate_blocking_dimensions,
        "blocked_sessions": sum(1 for row in rows if row.get("comparison_status") == "blocked"),
        "promotion_allowed_sessions": len(promotable),
        "promotion_decision": "shadow_only_do_not_promote",
        "speedup_supported_sessions": sum(
            1 for row in rows if (row.get("final_reconcile") or {}).get("speedup_status") == "live_asr_cache_reused"
        ),
        "live_order_mismatch_count": sum_int_metric(rows, "live_order_mismatch_count"),
        "real_live_order_mismatch_count": sum_int_metric(real_live_rows, "live_order_mismatch_count"),
        "live_order_mismatch_by_category": sum_counter_metric(rows, "live_order_mismatch_by_category"),
        "real_live_order_mismatch_by_category": sum_counter_metric(real_live_rows, "live_order_mismatch_by_category"),
        "live_order_mismatch_by_primary_risk": sum_counter_metric(rows, "live_order_mismatch_by_primary_risk"),
        "real_live_order_mismatch_by_primary_risk": sum_counter_metric(
            real_live_rows,
            "live_order_mismatch_by_primary_risk",
        ),
        "live_order_mismatch_by_confidence": sum_counter_metric(rows, "live_order_mismatch_by_confidence"),
        "real_live_order_mismatch_by_confidence": sum_counter_metric(real_live_rows, "live_order_mismatch_by_confidence"),
        "live_role_constrained_order_mismatch_count": sum_int_metric(
            rows,
            "live_role_constrained_order_mismatch_count",
        ),
        "real_live_role_constrained_order_mismatch_count": sum_int_metric(
            real_live_rows,
            "live_role_constrained_order_mismatch_count",
        ),
        "live_role_constrained_order_mismatch_by_category": sum_counter_metric(
            rows,
            "live_role_constrained_order_mismatch_by_category",
        ),
        "real_live_role_constrained_order_mismatch_by_category": sum_counter_metric(
            real_live_rows,
            "live_role_constrained_order_mismatch_by_category",
        ),
        "live_role_constrained_order_mismatch_by_confidence": sum_counter_metric(
            rows,
            "live_role_constrained_order_mismatch_by_confidence",
        ),
        "real_live_role_constrained_order_mismatch_by_confidence": sum_counter_metric(
            real_live_rows,
            "live_role_constrained_order_mismatch_by_confidence",
        ),
        "live_contentful_role_constrained_order_mismatch_count": sum_int_metric(
            rows,
            "live_contentful_role_constrained_order_mismatch_count",
        ),
        "real_live_contentful_role_constrained_order_mismatch_count": sum_int_metric(
            real_live_rows,
            "live_contentful_role_constrained_order_mismatch_count",
        ),
        "live_contentful_role_constrained_order_mismatch_by_category": sum_counter_metric(
            rows,
            "live_contentful_role_constrained_order_mismatch_by_category",
        ),
        "real_live_contentful_role_constrained_order_mismatch_by_category": sum_counter_metric(
            real_live_rows,
            "live_contentful_role_constrained_order_mismatch_by_category",
        ),
        "live_contentful_role_constrained_order_mismatch_by_confidence": sum_counter_metric(
            rows,
            "live_contentful_role_constrained_order_mismatch_by_confidence",
        ),
        "real_live_contentful_role_constrained_order_mismatch_by_confidence": sum_counter_metric(
            real_live_rows,
            "live_contentful_role_constrained_order_mismatch_by_confidence",
        ),
        "live_contentful_role_constrained_order_mismatch_by_ambiguity": sum_counter_metric(
            rows,
            "live_contentful_role_constrained_order_mismatch_by_ambiguity",
        ),
        "real_live_contentful_role_constrained_order_mismatch_by_ambiguity": sum_counter_metric(
            real_live_rows,
            "live_contentful_role_constrained_order_mismatch_by_ambiguity",
        ),
        "live_unambiguous_contentful_role_constrained_order_mismatch_count": sum_int_metric(
            rows,
            "live_unambiguous_contentful_role_constrained_order_mismatch_count",
        ),
        "real_live_unambiguous_contentful_role_constrained_order_mismatch_count": sum_int_metric(
            real_live_rows,
            "live_unambiguous_contentful_role_constrained_order_mismatch_count",
        ),
        "live_missing_me_seconds": sum_metric(rows, "live_missing_me_seconds"),
        "real_live_missing_me_seconds": sum_metric(real_live_rows, "live_missing_me_seconds"),
        "live_suspicious_batch_me_missing_seconds": sum_metric(rows, "live_suspicious_batch_me_missing_seconds"),
        "real_live_suspicious_batch_me_missing_seconds": sum_metric(
            real_live_rows,
            "live_suspicious_batch_me_missing_seconds",
        ),
        "live_missing_me_visible_in_suppressed_mic_seconds": sum_metric(
            rows,
            "live_missing_me_visible_in_suppressed_mic_seconds",
        ),
        "real_live_missing_me_visible_in_suppressed_mic_seconds": sum_metric(
            real_live_rows,
            "live_missing_me_visible_in_suppressed_mic_seconds",
        ),
        "live_missing_me_not_visible_in_suppressed_mic_seconds": sum_metric(
            rows,
            "live_missing_me_not_visible_in_suppressed_mic_seconds",
        ),
        "real_live_missing_me_not_visible_in_suppressed_mic_seconds": sum_metric(
            real_live_rows,
            "live_missing_me_not_visible_in_suppressed_mic_seconds",
        ),
        "live_suppressed_mic_turn_count": sum_int_metric(rows, "live_suppressed_mic_turn_count"),
        "real_live_suppressed_mic_turn_count": sum_int_metric(real_live_rows, "live_suppressed_mic_turn_count"),
        "live_segment_role_gate_candidate_chunk_count": sum_int_metric(
            rows,
            "live_segment_role_gate_candidate_chunk_count",
        ),
        "real_live_segment_role_gate_candidate_chunk_count": sum_int_metric(
            real_live_rows,
            "live_segment_role_gate_candidate_chunk_count",
        ),
        "live_segment_role_gate_candidate_kept_segment_count": sum_int_metric(
            rows,
            "live_segment_role_gate_candidate_kept_segment_count",
        ),
        "real_live_segment_role_gate_candidate_kept_segment_count": sum_int_metric(
            real_live_rows,
            "live_segment_role_gate_candidate_kept_segment_count",
        ),
        "live_rescue_shadow_candidate_chunk_count": sum_int_metric(rows, "live_rescue_shadow_candidate_chunk_count"),
        "real_live_rescue_shadow_candidate_chunk_count": sum_int_metric(
            real_live_rows,
            "live_rescue_shadow_candidate_chunk_count",
        ),
        "live_rescue_shadow_candidate_segment_count": sum_int_metric(rows, "live_rescue_shadow_candidate_segment_count"),
        "real_live_rescue_shadow_candidate_segment_count": sum_int_metric(
            real_live_rows,
            "live_rescue_shadow_candidate_segment_count",
        ),
        "live_rescue_shadow_order_mismatch_count": sum_int_metric(rows, "live_rescue_shadow_order_mismatch_count"),
        "real_live_rescue_shadow_order_mismatch_count": sum_int_metric(
            real_live_rows,
            "live_rescue_shadow_order_mismatch_count",
        ),
        "live_rescue_shadow_order_mismatch_by_category": sum_counter_metric(
            rows,
            "live_rescue_shadow_order_mismatch_by_category",
        ),
        "real_live_rescue_shadow_order_mismatch_by_category": sum_counter_metric(
            real_live_rows,
            "live_rescue_shadow_order_mismatch_by_category",
        ),
        "live_rescue_shadow_order_mismatch_by_primary_risk": sum_counter_metric(
            rows,
            "live_rescue_shadow_order_mismatch_by_primary_risk",
        ),
        "real_live_rescue_shadow_order_mismatch_by_primary_risk": sum_counter_metric(
            real_live_rows,
            "live_rescue_shadow_order_mismatch_by_primary_risk",
        ),
        "live_rescue_shadow_order_mismatch_by_confidence": sum_counter_metric(
            rows,
            "live_rescue_shadow_order_mismatch_by_confidence",
        ),
        "real_live_rescue_shadow_order_mismatch_by_confidence": sum_counter_metric(
            real_live_rows,
            "live_rescue_shadow_order_mismatch_by_confidence",
        ),
        "live_rescue_shadow_role_constrained_order_mismatch_count": sum_int_metric(
            rows,
            "live_rescue_shadow_role_constrained_order_mismatch_count",
        ),
        "real_live_rescue_shadow_role_constrained_order_mismatch_count": sum_int_metric(
            real_live_rows,
            "live_rescue_shadow_role_constrained_order_mismatch_count",
        ),
        "live_rescue_shadow_role_constrained_order_mismatch_by_category": sum_counter_metric(
            rows,
            "live_rescue_shadow_role_constrained_order_mismatch_by_category",
        ),
        "real_live_rescue_shadow_role_constrained_order_mismatch_by_category": sum_counter_metric(
            real_live_rows,
            "live_rescue_shadow_role_constrained_order_mismatch_by_category",
        ),
        "live_rescue_shadow_role_constrained_order_mismatch_by_confidence": sum_counter_metric(
            rows,
            "live_rescue_shadow_role_constrained_order_mismatch_by_confidence",
        ),
        "real_live_rescue_shadow_role_constrained_order_mismatch_by_confidence": sum_counter_metric(
            real_live_rows,
            "live_rescue_shadow_role_constrained_order_mismatch_by_confidence",
        ),
        "live_rescue_shadow_contentful_role_constrained_order_mismatch_count": sum_int_metric(
            rows,
            "live_rescue_shadow_contentful_role_constrained_order_mismatch_count",
        ),
        "real_live_rescue_shadow_contentful_role_constrained_order_mismatch_count": sum_int_metric(
            real_live_rows,
            "live_rescue_shadow_contentful_role_constrained_order_mismatch_count",
        ),
        "live_rescue_shadow_contentful_role_constrained_order_mismatch_by_category": sum_counter_metric(
            rows,
            "live_rescue_shadow_contentful_role_constrained_order_mismatch_by_category",
        ),
        "real_live_rescue_shadow_contentful_role_constrained_order_mismatch_by_category": sum_counter_metric(
            real_live_rows,
            "live_rescue_shadow_contentful_role_constrained_order_mismatch_by_category",
        ),
        "live_rescue_shadow_contentful_role_constrained_order_mismatch_by_confidence": sum_counter_metric(
            rows,
            "live_rescue_shadow_contentful_role_constrained_order_mismatch_by_confidence",
        ),
        "real_live_rescue_shadow_contentful_role_constrained_order_mismatch_by_confidence": sum_counter_metric(
            real_live_rows,
            "live_rescue_shadow_contentful_role_constrained_order_mismatch_by_confidence",
        ),
        "live_rescue_shadow_contentful_role_constrained_order_mismatch_by_ambiguity": sum_counter_metric(
            rows,
            "live_rescue_shadow_contentful_role_constrained_order_mismatch_by_ambiguity",
        ),
        "real_live_rescue_shadow_contentful_role_constrained_order_mismatch_by_ambiguity": sum_counter_metric(
            real_live_rows,
            "live_rescue_shadow_contentful_role_constrained_order_mismatch_by_ambiguity",
        ),
        "live_rescue_shadow_unambiguous_contentful_role_constrained_order_mismatch_count": sum_int_metric(
            rows,
            "live_rescue_shadow_unambiguous_contentful_role_constrained_order_mismatch_count",
        ),
        "real_live_rescue_shadow_unambiguous_contentful_role_constrained_order_mismatch_count": sum_int_metric(
            real_live_rows,
            "live_rescue_shadow_unambiguous_contentful_role_constrained_order_mismatch_count",
        ),
        "live_rescue_shadow_suspected_remote_leak_in_me_seconds": sum_metric(
            rows,
            "live_rescue_shadow_suspected_remote_leak_in_me_seconds",
        ),
        "real_live_rescue_shadow_suspected_remote_leak_in_me_seconds": sum_metric(
            real_live_rows,
            "live_rescue_shadow_suspected_remote_leak_in_me_seconds",
        ),
        "live_rescue_shadow_missing_me_seconds_after": sum_metric(rows, "live_rescue_shadow_missing_me_seconds_after"),
        "real_live_rescue_shadow_missing_me_seconds_after": sum_metric(
            real_live_rows,
            "live_rescue_shadow_missing_me_seconds_after",
        ),
        "live_rescue_shadow_missing_me_recovered_seconds": sum_metric(
            rows,
            "live_rescue_shadow_missing_me_recovered_seconds",
        ),
        "real_live_rescue_shadow_missing_me_recovered_seconds": sum_metric(
            real_live_rows,
            "live_rescue_shadow_missing_me_recovered_seconds",
        ),
        "live_suppressed_mic_asr_segment_count": sum_int_metric(rows, "live_suppressed_mic_asr_segment_count"),
        "real_live_suppressed_mic_asr_segment_count": sum_int_metric(
            real_live_rows,
            "live_suppressed_mic_asr_segment_count",
        ),
        "live_suppressed_mic_asr_segment_seconds": sum_metric(rows, "live_suppressed_mic_asr_segment_seconds"),
        "real_live_suppressed_mic_asr_segment_seconds": sum_metric(
            real_live_rows,
            "live_suppressed_mic_asr_segment_seconds",
        ),
        "live_suppressed_mic_asr_me_dominant_segment_count": sum_int_metric(
            rows,
            "live_suppressed_mic_asr_me_dominant_segment_count",
        ),
        "real_live_suppressed_mic_asr_me_dominant_segment_count": sum_int_metric(
            real_live_rows,
            "live_suppressed_mic_asr_me_dominant_segment_count",
        ),
        "live_suppressed_mic_asr_me_dominant_segment_seconds": sum_metric(
            rows,
            "live_suppressed_mic_asr_me_dominant_segment_seconds",
        ),
        "real_live_suppressed_mic_asr_me_dominant_segment_seconds": sum_metric(
            real_live_rows,
            "live_suppressed_mic_asr_me_dominant_segment_seconds",
        ),
        "live_suppressed_mic_asr_mixed_segment_count": sum_int_metric(
            rows,
            "live_suppressed_mic_asr_mixed_segment_count",
        ),
        "real_live_suppressed_mic_asr_mixed_segment_count": sum_int_metric(
            real_live_rows,
            "live_suppressed_mic_asr_mixed_segment_count",
        ),
        "live_suppressed_mic_asr_mixed_segment_seconds": sum_metric(
            rows,
            "live_suppressed_mic_asr_mixed_segment_seconds",
        ),
        "real_live_suppressed_mic_asr_mixed_segment_seconds": sum_metric(
            real_live_rows,
            "live_suppressed_mic_asr_mixed_segment_seconds",
        ),
        "live_suspected_remote_leak_in_me_seconds": sum_metric(rows, "live_suspected_remote_leak_in_me_seconds"),
        "real_live_suspected_remote_leak_in_me_seconds": sum_metric(
            real_live_rows,
            "live_suspected_remote_leak_in_me_seconds",
        ),
        "adjacent_duplicate_chunk_count": sum_int_metric(rows, "adjacent_duplicate_chunk_count"),
        "real_adjacent_duplicate_chunk_count": sum_int_metric(real_live_rows, "adjacent_duplicate_chunk_count"),
        "live_boundary_gate_issue_count": sum_int_metric(rows, "live_boundary_gate_issue_count"),
        "real_live_boundary_gate_issue_count": sum_int_metric(real_live_rows, "live_boundary_gate_issue_count"),
        "live_boundary_gate_suppressed_count": sum_int_metric(rows, "live_boundary_gate_suppressed_count"),
        "real_live_boundary_gate_suppressed_count": sum_int_metric(
            real_live_rows,
            "live_boundary_gate_suppressed_count",
        ),
        "live_boundary_gate_resolved_suppressed_count": sum_int_metric(
            rows,
            "live_boundary_gate_resolved_suppressed_count",
        ),
        "real_live_boundary_gate_resolved_suppressed_count": sum_int_metric(
            real_live_rows,
            "live_boundary_gate_resolved_suppressed_count",
        ),
        "live_boundary_gate_unresolved_suppressed_count": sum_int_metric(
            rows,
            "live_boundary_gate_unresolved_suppressed_count",
        ),
        "real_live_boundary_gate_unresolved_suppressed_count": sum_int_metric(
            real_live_rows,
            "live_boundary_gate_unresolved_suppressed_count",
        ),
    }
    add_suppressed_mic_rescue_policy_summary(summary, rows, "")
    add_suppressed_mic_rescue_policy_summary(summary, real_live_rows, "real")
    add_suppressed_mic_rescue_policy_summary(summary, capture_safe_candidate_rows, "capture_safe_candidate")
    add_suppressed_mic_rescue_policy_summary(summary, capture_safe_evaluable_rows, "capture_safe_evaluable")
    add_target_me_shadow_policy_summary(summary, rows, "")
    add_target_me_shadow_policy_summary(summary, real_live_rows, "real")
    add_target_me_shadow_policy_summary(summary, capture_safe_candidate_rows, "capture_safe_candidate")
    add_target_me_shadow_policy_summary(summary, capture_safe_evaluable_rows, "capture_safe_evaluable")
    add_target_me_shadow_profile_summary(summary, rows, "")
    add_target_me_shadow_profile_summary(summary, real_live_rows, "real")
    add_target_me_shadow_profile_summary(summary, capture_safe_candidate_rows, "capture_safe_candidate")
    add_target_me_shadow_profile_summary(summary, capture_safe_evaluable_rows, "capture_safe_evaluable")
    local_recall_rescue_policy_diagnostics = {
        "real": rescue_policy_diagnostics(summary, "real"),
        "capture_safe_candidate": rescue_policy_diagnostics(summary, "capture_safe_candidate"),
        "capture_safe_evaluable": rescue_policy_diagnostics(summary, "capture_safe_evaluable"),
    }
    target_me_shadow_diagnostics_report = {
        "real": target_me_shadow_policy_diagnostics(summary, "real"),
        "capture_safe_candidate": target_me_shadow_policy_diagnostics(summary, "capture_safe_candidate"),
        "capture_safe_evaluable": target_me_shadow_policy_diagnostics(summary, "capture_safe_evaluable"),
    }
    target_me_shadow_profile_diagnostics_report = {
        "real": target_me_shadow_profile_diagnostics(summary, "real"),
        "capture_safe_candidate": target_me_shadow_profile_diagnostics(summary, "capture_safe_candidate"),
        "capture_safe_evaluable": target_me_shadow_profile_diagnostics(summary, "capture_safe_evaluable"),
    }
    local_recall_rescue_lab_report = local_recall_rescue_lab(real_live_rows)
    candidate_local_recall_rescue_lab_report = local_recall_rescue_lab(capture_safe_candidate_rows)
    evaluable_local_recall_rescue_lab_report = local_recall_rescue_lab(capture_safe_evaluable_rows)
    target_me_diagnostics_report = {
        "real": live_local_recall_target_me_diagnostics(real_live_rows, "real"),
        "capture_safe_candidate": live_local_recall_target_me_diagnostics(
            capture_safe_candidate_rows,
            "capture_safe_candidate",
        ),
        "capture_safe_evaluable": live_local_recall_target_me_diagnostics(
            capture_safe_evaluable_rows,
            "capture_safe_evaluable",
        ),
    }
    for scope, diagnostics in local_recall_rescue_policy_diagnostics.items():
        best_policy = diagnostics.get("best_policy")
        recommended_policy = diagnostics.get("recommended_policy")
        summary[f"{scope}_rescue_policy_status"] = diagnostics.get("status")
        summary[f"{scope}_best_live_rescue_policy"] = (
            best_policy.get("policy") if isinstance(best_policy, dict) else None
        )
        summary[f"{scope}_best_live_rescue_policy_local_seconds"] = (
            safe_float(best_policy.get("local_seconds")) if isinstance(best_policy, dict) else None
        )
        summary[f"{scope}_best_live_rescue_policy_remote_risk_seconds"] = (
            safe_float(best_policy.get("remote_risk_seconds")) if isinstance(best_policy, dict) else None
        )
        summary[f"{scope}_recommended_rescue_policy"] = (
            recommended_policy.get("policy") if isinstance(recommended_policy, dict) else None
        )
    for scope, lab in (
        ("real", local_recall_rescue_lab_report),
        ("capture_safe_candidate", candidate_local_recall_rescue_lab_report),
        ("capture_safe_evaluable", evaluable_local_recall_rescue_lab_report),
    ):
        summary[f"{scope}_live_rescue_lab_speaker_evidence_needed_seconds"] = safe_float(
            lab.get("speaker_evidence_needed_seconds")
        )
        summary[f"{scope}_live_rescue_lab_live_policy_remote_risk_seconds"] = safe_float(
            lab.get("live_policy_remote_risk_seconds")
        )
        summary[f"{scope}_live_rescue_lab_recommended_next"] = lab.get("recommended_next")
    for scope, diagnostics in target_me_diagnostics_report.items():
        recommended = diagnostics.get("recommended_policy")
        best = diagnostics.get("best_policy")
        summary[f"{scope}_live_target_me_status"] = diagnostics.get("status")
        summary[f"{scope}_live_target_me_sessions_with_audit"] = safe_int(diagnostics.get("sessions_with_audit"))
        summary[f"{scope}_live_target_me_possible_or_confirmed_local_seconds"] = safe_float(
            diagnostics.get("target_me_possible_or_confirmed_local_seconds")
        )
        summary[f"{scope}_live_target_me_remote_risk_seconds"] = safe_float(diagnostics.get("remote_risk_seconds"))
        summary[f"{scope}_live_target_me_recommended_policy"] = (
            recommended.get("policy") if isinstance(recommended, dict) else None
        )
        summary[f"{scope}_live_target_me_recommended_policy_local_seconds"] = (
            safe_float(recommended.get("local_seconds")) if isinstance(recommended, dict) else None
        )
        summary[f"{scope}_live_target_me_recommended_policy_remote_risk_seconds"] = (
            safe_float(recommended.get("remote_risk_seconds")) if isinstance(recommended, dict) else None
        )
        summary[f"{scope}_live_target_me_recommended_policy_missing_me_recovered_seconds"] = (
            safe_float(recommended.get("missing_me_recovered_seconds")) if isinstance(recommended, dict) else None
        )
        summary[f"{scope}_live_target_me_best_policy"] = best.get("policy") if isinstance(best, dict) else None
        summary[f"{scope}_live_target_me_recommended_next"] = diagnostics.get("recommended_next")
    for scope, diagnostics in target_me_shadow_diagnostics_report.items():
        recommended = diagnostics.get("recommended_policy")
        summary[f"{scope}_live_target_me_shadow_status"] = diagnostics.get("status")
        summary[f"{scope}_live_target_me_shadow_recommended_policy"] = (
            recommended.get("policy") if isinstance(recommended, dict) else None
        )
        summary[f"{scope}_live_target_me_shadow_recommended_policy_missing_me_recovered_seconds"] = (
            safe_float(recommended.get("missing_me_recovered_seconds")) if isinstance(recommended, dict) else None
        )
        summary[f"{scope}_live_target_me_shadow_recommended_policy_remote_risk_seconds"] = (
            safe_float(recommended.get("suspected_remote_leak_in_me_seconds")) if isinstance(recommended, dict) else None
        )
        summary[f"{scope}_live_target_me_shadow_recommended_policy_order_mismatch_count"] = (
            safe_int(recommended.get("contentful_role_constrained_order_mismatch_count"))
            if isinstance(recommended, dict)
            else None
        )
        summary[f"{scope}_live_target_me_shadow_recommended_policy_order_mismatch_delta_count"] = (
            safe_int(recommended.get("contentful_role_constrained_order_mismatch_delta_count"))
            if isinstance(recommended, dict)
            else None
        )
        summary[f"{scope}_live_target_me_shadow_recommended_next"] = diagnostics.get("recommended_next")
    for scope, diagnostics in target_me_shadow_profile_diagnostics_report.items():
        best = diagnostics.get("best_profile")
        best_live = diagnostics.get("best_live_implementable_profile")
        summary[f"{scope}_live_target_me_shadow_profile_status"] = diagnostics.get("status")
        summary[f"{scope}_live_target_me_shadow_profile_best_policy"] = (
            best.get("policy") if isinstance(best, dict) else None
        )
        summary[f"{scope}_live_target_me_shadow_profile_best_live_implementable_policy"] = (
            best_live.get("policy") if isinstance(best_live, dict) else None
        )
        summary[f"{scope}_live_target_me_shadow_profile_best_live_implementable_non_passing_gate_count"] = (
            safe_int(best_live.get("non_passing_gate_count")) if isinstance(best_live, dict) else None
        )
        summary[f"{scope}_live_target_me_shadow_profile_best_live_implementable_missing_me_seconds"] = (
            safe_float(best_live.get("live_missing_me_seconds")) if isinstance(best_live, dict) else None
        )
        summary[f"{scope}_live_target_me_shadow_profile_best_live_implementable_remote_leak_seconds"] = (
            safe_float(best_live.get("live_suspected_remote_leak_in_me_seconds")) if isinstance(best_live, dict) else None
        )
        summary[f"{scope}_live_target_me_shadow_profile_best_live_implementable_contentful_order_mismatch_count"] = (
            safe_int(best_live.get("live_contentful_role_constrained_order_mismatch_count"))
            if isinstance(best_live, dict)
            else None
        )
        summary[f"{scope}_live_target_me_shadow_profile_best_live_implementable_visible_with_target_me_seconds"] = (
            safe_float(best_live.get("live_missing_me_visible_with_target_me_candidate_seconds"))
            if isinstance(best_live, dict)
            else None
        )
        summary[f"{scope}_live_target_me_shadow_profile_best_live_implementable_visible_without_target_me_seconds"] = (
            safe_float(best_live.get("live_missing_me_visible_without_target_me_candidate_seconds"))
            if isinstance(best_live, dict)
            else None
        )
        summary[f"{scope}_live_target_me_shadow_profile_best_live_implementable_not_visible_with_target_me_seconds"] = (
            safe_float(best_live.get("live_missing_me_not_visible_with_target_me_candidate_seconds"))
            if isinstance(best_live, dict)
            else None
        )
        summary[f"{scope}_live_target_me_shadow_profile_best_live_implementable_not_visible_without_target_me_seconds"] = (
            safe_float(best_live.get("live_missing_me_not_visible_without_target_me_candidate_seconds"))
            if isinstance(best_live, dict)
            else None
        )
        summary[f"{scope}_live_target_me_shadow_profile_all_parity_gates_passed_sessions"] = (
            safe_int(best.get("all_parity_gates_passed_session_count")) if isinstance(best, dict) else None
        )
        summary[f"{scope}_live_target_me_shadow_profile_non_passing_gate_count"] = (
            safe_int(best.get("non_passing_gate_count")) if isinstance(best, dict) else None
        )
        summary[f"{scope}_live_target_me_shadow_profile_missing_me_seconds"] = (
            safe_float(best.get("live_missing_me_seconds")) if isinstance(best, dict) else None
        )
        summary[f"{scope}_live_target_me_shadow_profile_missing_me_visible_in_suppressed_mic_seconds"] = (
            safe_float(best.get("live_missing_me_visible_in_suppressed_mic_seconds")) if isinstance(best, dict) else None
        )
        summary[f"{scope}_live_target_me_shadow_profile_missing_me_not_visible_in_suppressed_mic_seconds"] = (
            safe_float(best.get("live_missing_me_not_visible_in_suppressed_mic_seconds")) if isinstance(best, dict) else None
        )
        summary[f"{scope}_live_target_me_shadow_profile_missing_me_with_target_me_candidate_seconds"] = (
            safe_float(best.get("live_missing_me_with_target_me_candidate_seconds")) if isinstance(best, dict) else None
        )
        summary[f"{scope}_live_target_me_shadow_profile_missing_me_without_target_me_candidate_seconds"] = (
            safe_float(best.get("live_missing_me_without_target_me_candidate_seconds")) if isinstance(best, dict) else None
        )
        summary[f"{scope}_live_target_me_shadow_profile_remote_leak_seconds"] = (
            safe_float(best.get("live_suspected_remote_leak_in_me_seconds")) if isinstance(best, dict) else None
        )
        summary[f"{scope}_live_target_me_shadow_profile_order_mismatch_count"] = (
            safe_int(best.get("live_order_mismatch_count")) if isinstance(best, dict) else None
        )
        summary[f"{scope}_live_target_me_shadow_profile_contentful_order_mismatch_count"] = (
            safe_int(best.get("live_contentful_role_constrained_order_mismatch_count")) if isinstance(best, dict) else None
        )
        summary[f"{scope}_live_target_me_shadow_profile_removed_live_turn_count"] = (
            safe_int(best.get("removed_live_turn_count")) if isinstance(best, dict) else None
        )
        summary[f"{scope}_live_target_me_shadow_profile_removed_live_turn_seconds"] = (
            safe_float(best.get("removed_live_turn_seconds")) if isinstance(best, dict) else None
        )
        summary[f"{scope}_live_target_me_shadow_profile_visible_suppressed_mic_added_turn_seconds"] = (
            safe_float(best.get("visible_suppressed_mic_added_turn_seconds")) if isinstance(best, dict) else None
        )
        summary[f"{scope}_live_target_me_shadow_profile_visible_suppressed_mic_rejected_turn_count"] = (
            safe_int(best.get("visible_suppressed_mic_rejected_turn_count")) if isinstance(best, dict) else None
        )
        summary[f"{scope}_live_target_me_shadow_profile_recommended_next"] = diagnostics.get("recommended_next")
    coverage_target = {
        "target_live_sessions": args.target_live_sessions,
        "target_meaningful_compared_sessions": args.target_meaningful_compared_sessions,
        "target_passing_compared_sessions": args.target_passing_compared_sessions,
        "live_sessions_remaining": max(0, args.target_live_sessions - summary["real_live_sessions"]),
        "meaningful_compared_sessions_remaining": max(
            0,
            args.target_meaningful_compared_sessions - summary["real_meaningful_compared_sessions"],
        ),
        "passing_compared_sessions_remaining": max(
            0,
            args.target_passing_compared_sessions - summary["real_passing_compared_sessions"],
        ),
    }
    coverage_target["status"] = (
        "passed"
        if coverage_target["live_sessions_remaining"] == 0
        and coverage_target["meaningful_compared_sessions_remaining"] == 0
        and coverage_target["passing_compared_sessions_remaining"] == 0
        else "needs_more_live_coverage"
    )
    summary["coverage_target_status"] = coverage_target["status"]
    summary["coverage_target_live_sessions_remaining"] = coverage_target["live_sessions_remaining"]
    summary["coverage_target_passing_sessions_remaining"] = coverage_target["passing_compared_sessions_remaining"]
    if not live_rows:
        target_status = "no_live_sessions"
    elif promotable:
        target_status = "unexpected_promotable_sessions"
    elif not_promotable:
        target_status = "shadow_only_not_promotable"
    elif coverage_target["status"] != "passed":
        target_status = "shadow_locked_needs_more_live_coverage"
    else:
        target_status = "shadow_locked_after_basic_gates"
    summary["target_status"] = target_status
    promotion_blocking_dimensions = blocking_dimensions_from_counts(real_dimension_counts)
    if live_rows and not real_live_rows:
        promotion_blocking_dimensions = list(PARITY_DIMENSIONS.keys())
    summary["promotion_blocking_dimensions"] = promotion_blocking_dimensions
    summary["promotion_blocking_dimension_count"] = len(promotion_blocking_dimensions)
    capture_regression_check_path = root / "_reports/capture-regression/capture_regression_check.json"
    capture_regression_check = read_json(capture_regression_check_path)
    pilot_allowed = controlled_real_live_pilot_allowed(capture_regression_check)
    pilot_reason = (
        "full fail-open proof passed; controlled Live Evidence runs may collect evidence"
        if pilot_allowed
        else "full fail-open proof is missing; run MURMURMARK_RUN_LIVE_CAPTURE_TEST=1 scripts/check-capture-regressions.sh"
    )
    summary["live_quarantined"] = True
    summary["live_quarantine_reason"] = LIVE_QUARANTINE_REASON
    summary["live_evidence_mode"] = "historical_debug_only"
    summary["new_real_live_collection_allowed"] = False
    summary["controlled_real_live_pilot_allowed"] = pilot_allowed
    summary["controlled_real_live_pilot_reason"] = pilot_reason
    strict_failures: list[dict[str, Any]] = []
    def add_failure(gate_id: str, message: str, value: Any, limit: Any) -> None:
        strict_failures.append({"id": gate_id, "message": message, "value": value, "limit": limit})

    if args.min_live_sessions and summary["real_live_sessions"] < args.min_live_sessions:
        add_failure("min_live_sessions", "not enough real live sessions", summary["real_live_sessions"], args.min_live_sessions)
    if args.min_compared_sessions and summary["real_compared_sessions"] < args.min_compared_sessions:
        add_failure(
            "min_compared_sessions",
            "not enough live-vs-batch compared sessions",
            summary["real_compared_sessions"],
            args.min_compared_sessions,
        )
    if (
        args.min_meaningful_compared_sessions
        and summary["real_meaningful_compared_sessions"] < args.min_meaningful_compared_sessions
    ):
        add_failure(
            "min_meaningful_compared_sessions",
            "not enough compared sessions with both Me and remote evidence",
            summary["real_meaningful_compared_sessions"],
            args.min_meaningful_compared_sessions,
        )
    if args.min_passing_compared_sessions and summary["real_passing_compared_sessions"] < args.min_passing_compared_sessions:
        add_failure(
            "min_passing_compared_sessions",
            "not enough compared sessions where every live parity gate passed",
            summary["real_passing_compared_sessions"],
            args.min_passing_compared_sessions,
        )
    if args.max_order_mismatches is not None and summary["real_live_order_mismatch_count"] > args.max_order_mismatches:
        add_failure(
            "max_order_mismatches",
            "real live order mismatches exceed limit",
            summary["real_live_order_mismatch_count"],
            args.max_order_mismatches,
        )
    if args.max_missing_me_sec is not None and summary["real_live_missing_me_seconds"] > args.max_missing_me_sec:
        add_failure(
            "max_missing_me_sec",
            "real live missing Me seconds exceed limit",
            summary["real_live_missing_me_seconds"],
            args.max_missing_me_sec,
        )
    if (
        args.max_remote_in_me_sec is not None
        and summary["real_live_suspected_remote_leak_in_me_seconds"] > args.max_remote_in_me_sec
    ):
        add_failure(
            "max_remote_in_me_sec",
            "real live suspected remote-in-Me seconds exceed limit",
            summary["real_live_suspected_remote_leak_in_me_seconds"],
            args.max_remote_in_me_sec,
        )
    if (
        args.max_boundary_duplicates is not None
        and summary["real_adjacent_duplicate_chunk_count"] > args.max_boundary_duplicates
    ):
        add_failure(
            "max_boundary_duplicates",
            "real adjacent live chunk duplicates exceed limit",
            summary["real_adjacent_duplicate_chunk_count"],
            args.max_boundary_duplicates,
        )
    if args.fail_on_promotion and summary["promotion_allowed_sessions"] > 0:
        add_failure("no_promotion", "live promotion must remain blocked in v1", summary["promotion_allowed_sessions"], 0)
    if args.require_passing_gates:
        non_passing: dict[str, dict[str, int]] = {}
        if not real_live_rows:
            add_failure("require_passing_gates", "no real live sessions available for parity gates", 0, "> 0")
        for name, counts in real_gate_counts.items():
            bad = {status: count for status, count in counts.items() if status != "passed" and count > 0}
            if bad:
                non_passing[name] = bad
        if real_live_rows and not real_gate_counts:
            non_passing["required_artifacts"] = {"missing": len(real_live_rows)}
        if non_passing:
            add_failure("require_passing_gates", "one or more live parity gates did not pass", non_passing, "all passed")
    strict_requested = any(
        [
            args.min_live_sessions,
            args.min_compared_sessions,
            args.min_meaningful_compared_sessions,
            args.min_passing_compared_sessions,
            args.max_order_mismatches is not None,
            args.max_missing_me_sec is not None,
            args.max_remote_in_me_sec is not None,
            args.max_boundary_duplicates is not None,
            args.require_passing_gates,
            args.fail_on_promotion,
        ]
    )
    summary["strict_coverage_status"] = "not_requested" if not strict_requested else ("failed" if strict_failures else "passed")
    gate_issues = build_gate_issues(rows)
    real_blocker_triage_summary, real_blocker_triage = build_real_blocker_triage(real_live_rows)
    real_blocker_triage_summary["controlled_real_live_pilot_allowed"] = pilot_allowed
    next_commands = recommended_next_commands(summary, real_gate_counts, gate_issues)
    parity_dimensions_payload = build_dimensions_payload(dimension_counts, dimension_issue_sessions)
    real_parity_dimensions_payload = build_dimensions_payload(real_dimension_counts, real_dimension_issue_sessions)
    candidate_parity_dimensions_payload = build_dimensions_payload(
        candidate_dimension_counts,
        candidate_dimension_issue_sessions,
    )
    promotion_policy = {
        "status": "blocked",
        "decision": summary["promotion_decision"],
        "batch_authoritative": True,
        "live_quarantined": True,
        "evidence_mode": summary["live_evidence_mode"],
        "evidence_scope": "real_meeting",
        "diagnostic_live_sessions": len(diagnostic_live_rows),
        "new_real_live_collection_allowed": False,
        "controlled_real_live_pilot_allowed": pilot_allowed,
        "controlled_real_live_pilot_reason": pilot_reason,
        "quarantine_reason": LIVE_QUARANTINE_REASON,
        "required_dimensions": list(PARITY_DIMENSIONS.keys()),
        "blocking_dimensions": promotion_blocking_dimensions,
        "promotion_allowed_sessions": summary["promotion_allowed_sessions"],
    }
    candidate_scope = {
        "definition": (
            "real_meeting sessions with shadow_compared, meaningful comparison, capture_safety passed "
            "and required_artifacts passed"
        ),
        "sessions": len(capture_safe_candidate_rows),
        "passing_sessions": summary["real_capture_safe_candidate_passing_sessions"],
        "blocking_dimensions": candidate_blocking_dimensions,
        "session_ids": [str(row.get("session") or "") for row in capture_safe_candidate_rows],
        "next_focus": metric_aware_next_focus(candidate_blocking_dimensions, summary),
        "promotion_decision": "shadow_only_do_not_promote",
        "new_real_live_collection_allowed": False,
        "controlled_real_live_pilot_allowed": pilot_allowed,
    }
    candidate_session_ids = {str(row.get("session") or "") for row in capture_safe_candidate_rows}
    historical_non_candidate_session_ids = [
        str(row.get("session") or "")
        for row in real_live_rows
        if str(row.get("session") or "") not in candidate_session_ids
    ]
    local_recall_examples = local_recall_gap_examples(real_live_rows)
    candidate_local_recall_examples = local_recall_gap_examples(capture_safe_candidate_rows, limit=30)
    evaluable_local_recall_examples = local_recall_gap_examples(capture_safe_evaluable_rows, limit=50)
    summary["live_local_recall_gap_example_count"] = safe_int(local_recall_examples.get("item_count"))
    summary["live_local_recall_gap_example_seconds"] = safe_float(local_recall_examples.get("seconds"))
    summary["capture_safe_candidate_local_recall_gap_example_count"] = safe_int(
        candidate_local_recall_examples.get("item_count")
    )
    summary["capture_safe_candidate_local_recall_gap_example_seconds"] = safe_float(
        candidate_local_recall_examples.get("seconds")
    )
    summary["capture_safe_evaluable_local_recall_gap_example_count"] = safe_int(
        evaluable_local_recall_examples.get("item_count")
    )
    summary["capture_safe_evaluable_local_recall_gap_example_seconds"] = safe_float(
        evaluable_local_recall_examples.get("seconds")
    )
    if not pilot_allowed:
        coverage_path_status = "blocked_until_full_fail_open_proof"
        coverage_recommended_next = (
            "run MURMURMARK_RUN_LIVE_CAPTURE_TEST=1 scripts/check-capture-regressions.sh"
        )
    elif candidate_blocking_dimensions:
        coverage_path_status = "resolve_capture_safe_candidate_blockers"
        coverage_recommended_next = (
            metric_aware_next_focus(candidate_blocking_dimensions, summary) or {}
        ).get("recommended_next")
    elif coverage_target["passing_compared_sessions_remaining"] > 0:
        coverage_path_status = "needs_new_controlled_live_evidence"
        coverage_recommended_next = "murmurmark live pilot --controlled-real --skip-safety-gate --preflight-only"
    else:
        coverage_path_status = "coverage_target_met_shadow_still_locked"
        coverage_recommended_next = "murmurmark live gate"
    coverage_path = {
        "status": coverage_path_status,
        "recommended_next": coverage_recommended_next,
        "passing_compared_sessions_remaining": coverage_target["passing_compared_sessions_remaining"],
        "capture_safe_candidate_sessions": len(capture_safe_candidate_rows),
        "capture_safe_candidate_passing_sessions": summary["real_capture_safe_candidate_passing_sessions"],
        "capture_safe_candidate_blocking_dimensions": candidate_blocking_dimensions,
        "historical_non_candidate_sessions": len(historical_non_candidate_session_ids),
        "historical_non_candidate_session_ids": historical_non_candidate_session_ids,
        "new_real_live_collection_allowed": False,
        "controlled_real_live_pilot_allowed": pilot_allowed,
        "batch_authoritative": True,
    }
    summary["coverage_path_status"] = coverage_path_status
    summary["coverage_path_recommended_next"] = coverage_recommended_next
    summary["coverage_path_historical_non_candidate_sessions"] = len(historical_non_candidate_session_ids)
    summary["coverage_path_new_controlled_evidence_required"] = coverage_path_status == "needs_new_controlled_live_evidence"
    objective_audit = build_objective_audit(
        summary,
        coverage_target,
        real_parity_dimensions_payload,
        promotion_policy,
        candidate_scope,
        capture_regression_check,
    )
    objective_audit["capture_safe_candidate_scope"] = candidate_scope
    objective_next_focus = objective_audit.get("next_focus") if isinstance(objective_audit, dict) else None
    if not isinstance(objective_next_focus, dict):
        objective_next_focus = {}
    summary["objective_status"] = objective_audit.get("overall_status")
    summary["objective_ready_for_live_promotion"] = bool(objective_audit.get("ready_for_live_promotion"))
    summary["objective_next_focus"] = objective_next_focus.get("action_id")
    summary["objective_next_focus_dimension"] = objective_next_focus.get("dimension")
    summary["objective_next_recommended_next"] = objective_next_focus.get("recommended_next")
    summary["real_blocker_triage_items"] = safe_int(real_blocker_triage_summary.get("total_items"))
    summary["real_blocker_triage_sessions"] = safe_int(real_blocker_triage_summary.get("session_count"))
    summary["real_blocker_triage_uncategorized_items"] = safe_int(
        real_blocker_triage_summary.get("uncategorized_gate_issue_count")
    )
    return {
        "schema": SCHEMA,
        "generator": {"name": "report-live-corpus-gates", "version": SCRIPT_VERSION},
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": summary["target_status"],
        "sessions_root": str(root),
        "summary": summary,
        "strict_coverage": {
            "requested": strict_requested,
            "status": summary["strict_coverage_status"],
            "requirements": {
                "min_live_sessions": args.min_live_sessions,
                "min_compared_sessions": args.min_compared_sessions,
                "min_meaningful_compared_sessions": args.min_meaningful_compared_sessions,
                "min_passing_compared_sessions": args.min_passing_compared_sessions,
                "max_order_mismatches": args.max_order_mismatches,
                "max_missing_me_sec": args.max_missing_me_sec,
                "max_remote_in_me_sec": args.max_remote_in_me_sec,
                "max_boundary_duplicates": args.max_boundary_duplicates,
                "require_passing_gates": args.require_passing_gates,
                "fail_on_promotion": args.fail_on_promotion,
            },
            "failures": strict_failures,
        },
        "live_comparison_refresh": {
            "requested": bool(args.refresh),
            "status": refresh_status,
            "attempted_sessions": len(refresh_attempted),
            "failed_sessions": len(refresh_failed),
            "skipped_sessions": refresh_skipped,
            "results": refresh_results,
        },
        "coverage_target": coverage_target,
        "parity_dimensions": parity_dimensions_payload,
        "real_parity_dimensions": real_parity_dimensions_payload,
        "real_capture_safe_candidate_parity_dimensions": candidate_parity_dimensions_payload,
        "capture_safe_candidate_scope": candidate_scope,
        "live_local_recall_rescue_policy_diagnostics": local_recall_rescue_policy_diagnostics,
        "live_local_recall_rescue_lab": local_recall_rescue_lab_report,
        "capture_safe_candidate_live_local_recall_rescue_lab": candidate_local_recall_rescue_lab_report,
        "capture_safe_evaluable_live_local_recall_rescue_lab": evaluable_local_recall_rescue_lab_report,
        "live_local_recall_target_me_diagnostics": target_me_diagnostics_report,
        "live_target_me_shadow_policy_diagnostics": target_me_shadow_diagnostics_report,
        "live_target_me_shadow_profile_diagnostics": target_me_shadow_profile_diagnostics_report,
        "live_local_recall_gap_examples": local_recall_examples,
        "capture_safe_candidate_local_recall_gap_examples": candidate_local_recall_examples,
        "capture_safe_evaluable_local_recall_gap_examples": evaluable_local_recall_examples,
        "coverage_path": coverage_path,
        "promotion_policy": promotion_policy,
        "capture_regression_check": {
            "path": str(capture_regression_check_path),
            "present": capture_regression_check is not None,
            "status": capture_regression_check.get("status") if isinstance(capture_regression_check, dict) else None,
            "mode": capture_regression_check.get("mode") if isinstance(capture_regression_check, dict) else None,
            "capture_safe_proof_status": (
                (capture_regression_check.get("capture_safe_proof") or {}).get("status")
                if isinstance(capture_regression_check, dict)
                and isinstance(capture_regression_check.get("capture_safe_proof"), dict)
                else None
            ),
        },
        "objective_audit": objective_audit,
        "blockers": dict(blockers),
        "warnings": dict(warnings),
        "gate_counts": {name: dict(counts) for name, counts in sorted(gate_counts.items())},
        "real_gate_counts": {name: dict(counts) for name, counts in sorted(real_gate_counts.items())},
        "gate_issues": gate_issues,
        "real_blocker_triage_summary": real_blocker_triage_summary,
        "real_blocker_triage": real_blocker_triage,
        "sessions": rows,
        "recommended_next": next_commands[0],
        "next_commands": next_commands,
    }


def build_gate_issues(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for row in rows:
        if not row.get("live_present"):
            continue
        session = str(row.get("session") or "")
        scope = str(row.get("evidence_scope") or "diagnostic")
        session_path = session if session.startswith("/") or session.startswith("sessions/") else f"sessions/{session}"
        comparison = f"{session_path}/derived/live/live_batch_comparison.json" if session else ""
        for gate in row.get("non_passing_gates") or []:
            if not isinstance(gate, dict):
                continue
            issues.append(
                {
                    "session": session,
                    "evidence_scope": scope,
                    "gate": gate.get("name"),
                    "status": gate.get("status"),
                    "reason": gate.get("reason"),
                    "evidence": gate.get("evidence"),
                    "session_path": session_path,
                    "comparison": comparison,
                }
            )
    return sorted(issues, key=lambda item: (item.get("evidence_scope") != "real_meeting", item.get("session") or ""))


def gate_blob(row: dict[str, Any], gate: dict[str, Any]) -> str:
    value = {
        "gate": gate,
        "blockers": row.get("blockers"),
        "warnings": row.get("warnings"),
        "promotion_blockers": row.get("promotion_blockers"),
    }
    return json.dumps(value, ensure_ascii=False, sort_keys=True).lower()


def triage_categories_for_gate(row: dict[str, Any], gate: dict[str, Any]) -> list[str]:
    name = str(gate.get("name") or "unknown")
    if name == "capture_safety":
        return ["capture_safety_risk"]
    if name in {"review_burden", "selected_notes_readiness"}:
        return ["batch_review_required"]
    if name == "local_recall":
        return ["live_local_recall_gap"]
    if name == "remote_duplicate_leak":
        return ["live_remote_leakage"]
    if name == "live_token_recall":
        return ["live_draft_text_drift"]
    if name in {"chunk_boundary_risks", "duplicate_chunks"}:
        return ["chunk_boundary_risk"]
    if name == "order_risk":
        return ["order_risk"]
    if name in {"required_artifacts", "raw_batch_authoritative"}:
        text = gate_blob(row, gate)
        categories: list[str] = []
        if "batch_clean_dialogue_missing" in text or "batch_transcript_missing" in text:
            categories.append("missing_batch_artifacts")
        if (
            "live_chunks_missing" in text
            or "live_report_missing" in text
            or "live_batch_comparison_missing" in text
            or "live_asr" in text
        ):
            categories.append("missing_live_asr_artifacts")
        if not categories:
            categories.append("missing_batch_artifacts")
        return categories
    return ["other"]


def triage_severity(statuses: list[str]) -> str:
    rank = {
        "blocked": 4,
        "failed": 4,
        "warning": 3,
        "not_evaluated": 2,
        "missing": 2,
        "unknown": 1,
    }
    status = max((status or "unknown" for status in statuses), key=lambda item: rank.get(item, 1))
    if status in {"blocked", "failed"}:
        return "blocker"
    if status == "warning":
        return "warning"
    if status in {"not_evaluated", "missing"}:
        return "needs_evidence"
    return "unknown"


def build_real_blocker_triage(real_live_rows: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    real_gate_issue_keys: set[tuple[str, str, str, str]] = set()
    categorized_gate_issue_keys: set[tuple[str, str, str, str]] = set()
    for row in real_live_rows:
        session = str(row.get("session") or "")
        if not session:
            continue
        session_path = session if session.startswith("/") or session.startswith("sessions/") else f"sessions/{session}"
        comparison = f"{session_path}/derived/live/live_batch_comparison.json"
        for gate in row.get("non_passing_gates") or []:
            if not isinstance(gate, dict):
                continue
            gate_name = str(gate.get("name") or "unknown")
            gate_status = str(gate.get("status") or "unknown")
            gate_reason = str(gate.get("reason") or "")
            gate_key = (session, gate_name, gate_status, gate_reason)
            real_gate_issue_keys.add(gate_key)
            categories = triage_categories_for_gate(row, gate)
            if categories:
                categorized_gate_issue_keys.add(gate_key)
            for category in categories:
                item = grouped.setdefault(
                    (session, category),
                    {
                        "session": session,
                        "session_path": session_path,
                        "comparison": comparison,
                        "category": category,
                        "title": TRIAGE_CATEGORY_INFO.get(category, TRIAGE_CATEGORY_INFO["other"])["title"],
                        "promotion_blocker": True,
                        "gates": [],
                        "dimensions": [],
                        "reasons": [],
                        "evidence": [],
                        "metrics": row.get("metrics") or {},
                        "recommended_next": TRIAGE_CATEGORY_INFO.get(
                            category,
                            TRIAGE_CATEGORY_INFO["other"],
                        )["recommended_next"],
                    },
                )
                item["gates"].append(
                    {
                        "name": gate_name,
                        "status": str(gate.get("status") or "unknown"),
                        "reason": gate.get("reason"),
                    }
                )
                for dimension in dimensions_for_gate(gate_name):
                    if dimension not in item["dimensions"]:
                        item["dimensions"].append(dimension)
                reason = gate.get("reason")
                if reason and reason not in item["reasons"]:
                    item["reasons"].append(reason)
                evidence = gate.get("evidence")
                if evidence is not None:
                    item["evidence"].append({"gate": gate_name, "value": evidence})
    items: list[dict[str, Any]] = []
    for item in grouped.values():
        statuses = [str(gate.get("status") or "unknown") for gate in item.get("gates") or []]
        item["severity"] = triage_severity(statuses)
        item["gates"] = sorted(item["gates"], key=lambda gate: str(gate.get("name") or ""))
        item["dimensions"] = sorted(item["dimensions"])
        items.append(item)
    items = sorted(
        items,
        key=lambda item: (
            {"blocker": 0, "warning": 1, "needs_evidence": 2, "unknown": 3}.get(str(item.get("severity")), 9),
            str(item.get("category") or ""),
            str(item.get("session") or ""),
        ),
    )
    by_category: dict[str, dict[str, Any]] = {}
    by_severity: Counter[str] = Counter()
    sessions_by_category: dict[str, set[str]] = defaultdict(set)
    for item in items:
        category = str(item.get("category") or "other")
        severity = str(item.get("severity") or "unknown")
        by_severity[severity] += 1
        sessions_by_category[category].add(str(item.get("session") or ""))
    for category, sessions in sorted(sessions_by_category.items()):
        category_items = [item for item in items if item.get("category") == category]
        by_category[category] = {
            "title": TRIAGE_CATEGORY_INFO.get(category, TRIAGE_CATEGORY_INFO["other"])["title"],
            "item_count": len(category_items),
            "session_count": len(sessions),
            "sessions": sorted(sessions),
            "severities": dict(Counter(str(item.get("severity") or "unknown") for item in category_items)),
            "recommended_next": TRIAGE_CATEGORY_INFO.get(category, TRIAGE_CATEGORY_INFO["other"])["recommended_next"],
        }
    summary = {
        "total_items": len(items),
        "session_count": len({str(item.get("session") or "") for item in items}),
        "by_category": by_category,
        "by_severity": dict(by_severity),
        "real_gate_issue_count": len(real_gate_issue_keys),
        "categorized_gate_issue_count": len(categorized_gate_issue_keys),
        "uncategorized_gate_issue_count": len(real_gate_issue_keys - categorized_gate_issue_keys),
        "promotion_scope": "real_meeting",
        "new_real_live_collection_allowed": False,
        "note": "triage is derived only from non-passing real live parity gates; diagnostic sessions are excluded",
    }
    return summary, items


def recommended_next_commands(
    summary: dict[str, Any],
    gate_counts: dict[str, Counter[str]],
    gate_issues: list[dict[str, Any]],
) -> list[str]:
    live_quarantine_note = (
        "murmurmark status latest  # production meetings still use normal record/process; "
        "controlled Live Evidence uses live pilot"
    )
    if summary.get("live_quarantined") is True:
        if (
            summary.get("controlled_real_live_pilot_allowed") is True
            and safe_int(summary.get("coverage_target_passing_sessions_remaining")) > 0
            and not (summary.get("real_capture_safe_candidate_blocking_dimensions") or [])
        ):
            return [
                "murmurmark live pilot --controlled-real --skip-safety-gate --preflight-only",
                "murmurmark live pilot --controlled-real --skip-safety-gate",
                "murmurmark experiment status latest",
                "murmurmark experiment report latest",
                "murmurmark experiment compare latest --experiment live-shadow-v1",
                "murmurmark corpus live all --refresh",
                "jq '.capture_safe_candidate_scope, .coverage_target, .promotion_policy' sessions/_reports/live-pipeline/live_corpus_gates_report.json",
                live_quarantine_note,
            ]
        commands = [
            "jq '.real_blocker_triage_summary' sessions/_reports/live-pipeline/live_corpus_gates_report.json",
            "less sessions/_reports/live-pipeline/live_corpus_gates_report.md",
            live_quarantine_note,
        ]
        if (
            summary.get("objective_next_focus") == "fix_live_local_recall_gap"
            or "local_recall" in (summary.get("real_capture_safe_candidate_blocking_dimensions") or [])
        ):
            commands.insert(1, ".venv/bin/python scripts/report-suppressed-mic-policy-lab.py")
            commands.insert(2, ".venv/bin/python scripts/report-live-target-me-enrollment-lab.py --method resemblyzer_dvector")
            commands.insert(3, ".venv/bin/python scripts/report-persistent-target-me-profile-lab.py --method resemblyzer_dvector")
            commands.insert(4, ".venv/bin/python scripts/report-suppressed-mic-composite-gate-lab.py")
        real_issues = [
            issue
            for issue in gate_issues
            if isinstance(issue, dict) and issue.get("evidence_scope") == "real_meeting"
        ]
        first_issue = real_issues[0] if real_issues else {}
        session = first_issue.get("session")
        if isinstance(session, str) and session:
            commands.insert(
                1,
                f"jq '.real_blocker_triage[] | select(.session == \"{session}\")' "
                "sessions/_reports/live-pipeline/live_corpus_gates_report.json",
            )
        session_path = first_issue.get("session_path")
        if isinstance(session_path, str) and session_path:
            commands.insert(2, f"murmurmark status {session_path}")
        comparison = first_issue.get("comparison")
        if isinstance(comparison, str) and comparison:
            commands.insert(3, f"jq '.parity_gates.gates[] | select(.status != \"passed\")' {comparison}")
        return commands

    target_live = max(
        1,
        safe_int(summary.get("real_live_sessions")) + safe_int(summary.get("coverage_target_live_sessions_remaining")),
    )
    target_passing = max(
        1,
        safe_int(summary.get("real_passing_compared_sessions"))
        + safe_int(summary.get("coverage_target_passing_sessions_remaining")),
    )
    coverage_command = (
        f"murmurmark corpus live all --min-live-sessions {target_live} --min-compared-sessions {target_live} "
        f"--min-meaningful-compared-sessions {target_live} --min-passing-compared-sessions {target_passing} "
        "--max-order-mismatches 0 --max-missing-me-sec 0 --max-remote-in-me-sec 0 "
        "--max-boundary-duplicates 0 --require-passing-gates --fail-on-promotion"
    )
    if safe_int(summary.get("real_live_sessions")) == 0:
        return [
            live_quarantine_note,
            coverage_command,
        ]
    if safe_int(summary.get("real_compared_sessions")) == 0:
        return [
            "murmurmark process latest",
            coverage_command,
        ]
    if safe_int(summary.get("real_meaningful_compared_sessions")) == 0:
        return [
            live_quarantine_note,
            coverage_command,
        ]
    if safe_int(summary.get("real_passing_compared_sessions")) == 0:
        commands = [
            "less sessions/_reports/live-pipeline/live_corpus_gates_report.md",
            live_quarantine_note,
            coverage_command,
        ]
        first_issue = gate_issues[0] if gate_issues else {}
        comparison = first_issue.get("comparison")
        session = first_issue.get("session")
        if isinstance(comparison, str) and comparison:
            commands.insert(1, f"jq '.parity_gates.gates[] | select(.status != \"passed\")' {comparison}")
        session_path = first_issue.get("session_path")
        if isinstance(session_path, str) and session_path:
            commands.insert(1, f"murmurmark status {session_path}")
        non_passing = {
            name: {status: count for status, count in counts.items() if status != "passed" and count > 0}
            for name, counts in gate_counts.items()
        }
        if non_passing:
            commands.insert(1, "jq '.real_gate_counts' sessions/_reports/live-pipeline/live_corpus_gates_report.json")
        if safe_float(summary.get("real_live_suspicious_batch_me_missing_seconds")) > 0:
            commands.insert(
                1,
                "jq '.sessions[] | select(.evidence_scope == \"real_meeting\" and .metrics.live_suspicious_batch_me_missing_seconds > 0)' sessions/_reports/live-pipeline/live_corpus_gates_report.json",
            )
        return commands
    if safe_int(summary.get("promotion_allowed_sessions")) > 0:
        return [
            "jq '.sessions[] | select(.promotion_allowed == true)' sessions/_reports/live-pipeline/live_corpus_gates_report.json",
            coverage_command,
        ]
    if summary.get("coverage_target_status") != "passed":
        return [
            live_quarantine_note,
            coverage_command,
        ]
    return [
        coverage_command,
        live_quarantine_note,
    ]


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    summary = report["summary"]
    lines = [
        "# Live Pipeline Corpus Gates",
        "",
        f"- sessions: {summary['sessions_total']}",
        f"- live sessions: {summary['live_sessions']}",
        f"- real live sessions: {summary.get('real_live_sessions', 0)}",
        f"- diagnostic live sessions: {summary.get('diagnostic_live_sessions', 0)}",
        f"- live comparison refresh: `{summary.get('live_comparison_refresh_status')}`",
        f"- live comparison refresh attempted sessions: {summary.get('live_comparison_refresh_attempted_sessions', 0)}",
        f"- live comparison refresh failed sessions: {summary.get('live_comparison_refresh_failed_sessions', 0)}",
        f"- live comparison refresh skipped sessions: {summary.get('live_comparison_refresh_skipped_sessions', 0)}",
        f"- compared sessions: {summary['compared_sessions']}",
        f"- real compared sessions: {summary.get('real_compared_sessions', 0)}",
        f"- meaningful compared sessions: {summary['meaningful_compared_sessions']}",
        f"- real meaningful compared sessions: {summary.get('real_meaningful_compared_sessions', 0)}",
        f"- passing compared sessions: {summary['passing_compared_sessions']}",
        f"- real passing compared sessions: {summary.get('real_passing_compared_sessions', 0)}",
        f"- capture-safe candidate sessions: {summary.get('real_capture_safe_candidate_sessions', 0)}",
        f"- capture-safe candidate passing sessions: {summary.get('real_capture_safe_candidate_passing_sessions', 0)}",
        f"- blocked sessions: {summary['blocked_sessions']}",
        f"- promotion allowed sessions: {summary['promotion_allowed_sessions']}",
        f"- target status: `{summary['target_status']}`",
        f"- promotion decision: `{summary['promotion_decision']}`",
        f"- live order mismatches: {summary.get('live_order_mismatch_count', 0)}",
        f"- real live order mismatches: {summary.get('real_live_order_mismatch_count', 0)}",
        f"- real live order mismatches by category: `{json.dumps(summary.get('real_live_order_mismatch_by_category', {}), ensure_ascii=False)}`",
        "- real live order mismatches by primary risk: "
        f"`{json.dumps(summary.get('real_live_order_mismatch_by_primary_risk', {}), ensure_ascii=False)}`",
        "- real live order mismatches by confidence: "
        f"`{json.dumps(summary.get('real_live_order_mismatch_by_confidence', {}), ensure_ascii=False)}`",
        "- real live role-constrained order mismatches: "
        f"{summary.get('real_live_role_constrained_order_mismatch_count', 0)}",
        "- real live role-constrained order mismatches by category: "
        f"`{json.dumps(summary.get('real_live_role_constrained_order_mismatch_by_category', {}), ensure_ascii=False)}`",
        "- real live role-constrained order mismatches by confidence: "
        f"`{json.dumps(summary.get('real_live_role_constrained_order_mismatch_by_confidence', {}), ensure_ascii=False)}`",
        "- real live contentful role-constrained order mismatches: "
        f"{summary.get('real_live_contentful_role_constrained_order_mismatch_count', 0)}",
        "- real live contentful role-constrained order mismatches by category: "
        f"`{json.dumps(summary.get('real_live_contentful_role_constrained_order_mismatch_by_category', {}), ensure_ascii=False)}`",
        "- real live contentful role-constrained order mismatches by confidence: "
        f"`{json.dumps(summary.get('real_live_contentful_role_constrained_order_mismatch_by_confidence', {}), ensure_ascii=False)}`",
        "- real live contentful role-constrained order mismatches by ambiguity: "
        f"`{json.dumps(summary.get('real_live_contentful_role_constrained_order_mismatch_by_ambiguity', {}), ensure_ascii=False)}`",
        "- real live unambiguous contentful role-constrained order mismatches: "
        f"{summary.get('real_live_unambiguous_contentful_role_constrained_order_mismatch_count', 0)}",
        f"- live missing Me seconds: {summary.get('live_missing_me_seconds', 0.0)}",
        f"- real live missing Me seconds: {summary.get('real_live_missing_me_seconds', 0.0)}",
        "- real live missing Me visible in suppressed mic seconds: "
        f"{summary.get('real_live_missing_me_visible_in_suppressed_mic_seconds', 0.0)}",
        "- real live missing Me not visible in suppressed mic seconds: "
        f"{summary.get('real_live_missing_me_not_visible_in_suppressed_mic_seconds', 0.0)}",
        f"- real live suppressed mic turns: {summary.get('real_live_suppressed_mic_turn_count', 0)}",
        "- real live segment-gate candidate chunks: "
        f"{summary.get('real_live_segment_role_gate_candidate_chunk_count', 0)}",
        "- real live segment-gate candidate kept segments: "
        f"{summary.get('real_live_segment_role_gate_candidate_kept_segment_count', 0)}",
        "- real live rescue shadow candidates: "
        f"{summary.get('real_live_rescue_shadow_candidate_chunk_count', 0)} chunks / "
        f"{summary.get('real_live_rescue_shadow_candidate_segment_count', 0)} segments, "
        f"missing-Me recovered {summary.get('real_live_rescue_shadow_missing_me_recovered_seconds', 0.0)} sec, "
        f"missing-Me after {summary.get('real_live_rescue_shadow_missing_me_seconds_after', 0.0)} sec, "
        f"remote-risk {summary.get('real_live_rescue_shadow_suspected_remote_leak_in_me_seconds', 0.0)} sec, "
        f"order mismatches {summary.get('real_live_rescue_shadow_order_mismatch_count', 0)}",
        "- real live rescue shadow order mismatches by category: "
        f"`{json.dumps(summary.get('real_live_rescue_shadow_order_mismatch_by_category', {}), ensure_ascii=False)}`",
        "- real live rescue shadow order mismatches by primary risk: "
        f"`{json.dumps(summary.get('real_live_rescue_shadow_order_mismatch_by_primary_risk', {}), ensure_ascii=False)}`",
        "- real live rescue shadow order mismatches by confidence: "
        f"`{json.dumps(summary.get('real_live_rescue_shadow_order_mismatch_by_confidence', {}), ensure_ascii=False)}`",
        "- real live rescue shadow role-constrained order mismatches: "
        f"{summary.get('real_live_rescue_shadow_role_constrained_order_mismatch_count', 0)}",
        "- real live rescue shadow role-constrained order mismatches by category: "
        f"`{json.dumps(summary.get('real_live_rescue_shadow_role_constrained_order_mismatch_by_category', {}), ensure_ascii=False)}`",
        "- real live rescue shadow role-constrained order mismatches by confidence: "
        f"`{json.dumps(summary.get('real_live_rescue_shadow_role_constrained_order_mismatch_by_confidence', {}), ensure_ascii=False)}`",
        "- real live rescue shadow contentful role-constrained order mismatches: "
        f"{summary.get('real_live_rescue_shadow_contentful_role_constrained_order_mismatch_count', 0)}",
        "- real live rescue shadow contentful role-constrained order mismatches by category: "
        f"`{json.dumps(summary.get('real_live_rescue_shadow_contentful_role_constrained_order_mismatch_by_category', {}), ensure_ascii=False)}`",
        "- real live rescue shadow contentful role-constrained order mismatches by confidence: "
        f"`{json.dumps(summary.get('real_live_rescue_shadow_contentful_role_constrained_order_mismatch_by_confidence', {}), ensure_ascii=False)}`",
        "- real live rescue shadow contentful role-constrained order mismatches by ambiguity: "
        f"`{json.dumps(summary.get('real_live_rescue_shadow_contentful_role_constrained_order_mismatch_by_ambiguity', {}), ensure_ascii=False)}`",
        "- real live rescue shadow unambiguous contentful role-constrained order mismatches: "
        f"{summary.get('real_live_rescue_shadow_unambiguous_contentful_role_constrained_order_mismatch_count', 0)}",
        "- real live suppressed mic ASR segments: "
        f"{summary.get('real_live_suppressed_mic_asr_segment_count', 0)} / "
        f"{summary.get('real_live_suppressed_mic_asr_segment_seconds', 0.0)} sec",
        "- real live suppressed mic ASR Me-dominant segments: "
        f"{summary.get('real_live_suppressed_mic_asr_me_dominant_segment_count', 0)} / "
        f"{summary.get('real_live_suppressed_mic_asr_me_dominant_segment_seconds', 0.0)} sec",
        "- real live suppressed mic ASR mixed segments: "
        f"{summary.get('real_live_suppressed_mic_asr_mixed_segment_count', 0)} / "
        f"{summary.get('real_live_suppressed_mic_asr_mixed_segment_seconds', 0.0)} sec",
        "- real live rescue current text gate: "
        f"local {summary.get('real_live_rescue_policy_current_text_segment_gate_local_seconds', 0.0)} sec, "
        f"remote-risk {summary.get('real_live_rescue_policy_current_text_segment_gate_remote_risk_seconds', 0.0)} sec, "
        f"precision {summary.get('real_live_rescue_policy_current_text_segment_gate_precision_proxy')}",
        "- real live rescue strict text unique v1: "
        f"local {summary.get('real_live_rescue_policy_strict_text_unique_v1_local_seconds', 0.0)} sec, "
        f"remote-risk {summary.get('real_live_rescue_policy_strict_text_unique_v1_remote_risk_seconds', 0.0)} sec, "
        f"precision {summary.get('real_live_rescue_policy_strict_text_unique_v1_precision_proxy')}",
        "- real live rescue remote-silent text v1: "
        f"local {summary.get('real_live_rescue_policy_remote_silent_text_v1_local_seconds', 0.0)} sec, "
        f"remote-risk {summary.get('real_live_rescue_policy_remote_silent_text_v1_remote_risk_seconds', 0.0)} sec, "
        f"precision {summary.get('real_live_rescue_policy_remote_silent_text_v1_precision_proxy')}",
        "- real live rescue audio remote-quiet v1: "
        f"local {summary.get('real_live_rescue_policy_audio_remote_quiet_v1_local_seconds', 0.0)} sec, "
        f"remote-risk {summary.get('real_live_rescue_policy_audio_remote_quiet_v1_remote_risk_seconds', 0.0)} sec, "
        f"precision {summary.get('real_live_rescue_policy_audio_remote_quiet_v1_precision_proxy')}",
        "- real live rescue audio mic-dominant v1: "
        f"local {summary.get('real_live_rescue_policy_audio_mic_dominant_v1_local_seconds', 0.0)} sec, "
        f"remote-risk {summary.get('real_live_rescue_policy_audio_mic_dominant_v1_remote_risk_seconds', 0.0)} sec, "
        f"precision {summary.get('real_live_rescue_policy_audio_mic_dominant_v1_precision_proxy')}",
        "- real live rescue audio low-coherence v1: "
        f"local {summary.get('real_live_rescue_policy_audio_low_coherence_v1_local_seconds', 0.0)} sec, "
        f"remote-risk {summary.get('real_live_rescue_policy_audio_low_coherence_v1_remote_risk_seconds', 0.0)} sec, "
        f"precision {summary.get('real_live_rescue_policy_audio_low_coherence_v1_precision_proxy')}",
        "- real live rescue audio safe union v1: "
        f"local {summary.get('real_live_rescue_policy_audio_safe_union_v1_local_seconds', 0.0)} sec, "
        f"remote-risk {summary.get('real_live_rescue_policy_audio_safe_union_v1_remote_risk_seconds', 0.0)} sec, "
        f"precision {summary.get('real_live_rescue_policy_audio_safe_union_v1_precision_proxy')}, "
        f"missing-Me recovered {summary.get('real_live_rescue_policy_audio_safe_union_v1_missing_me_recovered_seconds', 0.0)} sec",
        "- real live Target-Me local evidence: "
        f"status `{summary.get('real_live_target_me_status')}`, "
        f"sessions {summary.get('real_live_target_me_sessions_with_audit', 0)}, "
        f"possible/confirmed local {summary.get('real_live_target_me_possible_or_confirmed_local_seconds', 0.0)} sec, "
        f"remote-risk {summary.get('real_live_target_me_remote_risk_seconds', 0.0)} sec, "
        f"recommended policy `{summary.get('real_live_target_me_recommended_policy')}`, "
        "missing-Me recovered "
        f"{summary.get('real_live_target_me_recommended_policy_missing_me_recovered_seconds', 0.0)} sec",
        "- real live rescue oracle local ceiling: "
        f"local {summary.get('real_live_rescue_policy_batch_oracle_local_ceiling_local_seconds', 0.0)} sec, "
        f"recall {summary.get('real_live_rescue_policy_batch_oracle_local_ceiling_local_recall_proxy')}",
        f"- real live missing Me examples: {summary.get('live_local_recall_gap_example_count', 0)}",
        "- capture-safe candidate missing Me examples: "
        f"{summary.get('capture_safe_candidate_local_recall_gap_example_count', 0)}",
        "- capture-safe evaluable missing Me examples: "
        f"{summary.get('capture_safe_evaluable_local_recall_gap_example_count', 0)}",
        f"- live suspicious batch-Me missing seconds: {summary.get('live_suspicious_batch_me_missing_seconds', 0.0)}",
        f"- real live suspicious batch-Me missing seconds: {summary.get('real_live_suspicious_batch_me_missing_seconds', 0.0)}",
        f"- live suspected remote-in-Me seconds: {summary.get('live_suspected_remote_leak_in_me_seconds', 0.0)}",
        f"- real live suspected remote-in-Me seconds: {summary.get('real_live_suspected_remote_leak_in_me_seconds', 0.0)}",
        f"- adjacent duplicate chunks: {summary.get('adjacent_duplicate_chunk_count', 0)}",
        f"- real adjacent duplicate chunks: {summary.get('real_adjacent_duplicate_chunk_count', 0)}",
        f"- live boundary-gate issues: {summary.get('live_boundary_gate_issue_count', 0)}",
        f"- real live boundary-gate issues: {summary.get('real_live_boundary_gate_issue_count', 0)}",
        f"- live boundary-gate suppressed chunks: {summary.get('live_boundary_gate_suppressed_count', 0)}",
        f"- real live boundary-gate suppressed chunks: {summary.get('real_live_boundary_gate_suppressed_count', 0)}",
        f"- live boundary-gate resolved suppressed chunks: {summary.get('live_boundary_gate_resolved_suppressed_count', 0)}",
        f"- real live boundary-gate resolved suppressed chunks: {summary.get('real_live_boundary_gate_resolved_suppressed_count', 0)}",
        f"- live boundary-gate unresolved suppressed chunks: {summary.get('live_boundary_gate_unresolved_suppressed_count', 0)}",
        f"- real live boundary-gate unresolved suppressed chunks: {summary.get('real_live_boundary_gate_unresolved_suppressed_count', 0)}",
        f"- strict coverage: `{summary.get('strict_coverage_status')}`",
        f"- coverage target: `{summary.get('coverage_target_status')}`",
        f"- coverage target live remaining: {summary.get('coverage_target_live_sessions_remaining', 0)}",
        f"- coverage target passing remaining: {summary.get('coverage_target_passing_sessions_remaining', 0)}",
        f"- coverage path: `{summary.get('coverage_path_status')}`",
        "- coverage path historical non-candidate sessions: "
        f"{summary.get('coverage_path_historical_non_candidate_sessions', 0)}",
        f"- live quarantined: `{summary.get('live_quarantined')}`",
        f"- live evidence mode: `{summary.get('live_evidence_mode')}`",
        f"- new real live collection allowed: `{summary.get('new_real_live_collection_allowed')}`",
        f"- controlled real live pilot allowed: `{summary.get('controlled_real_live_pilot_allowed')}`",
        f"- promotion blocking dimensions: {', '.join(summary.get('promotion_blocking_dimensions') or []) or 'none'}",
        "",
        "## Promotion Policy",
        "",
        "Batch transcript remains authoritative. Live promotion is blocked while the live branch is "
        "quarantined and until every required parity dimension passes on enough meaningful real "
        "comparisons.",
        "",
        f"- quarantine reason: {summary.get('live_quarantine_reason')}",
        "",
        "## Capture Regression Proof",
        "",
    ]
    capture_regression_check = (
        report.get("capture_regression_check")
        if isinstance(report.get("capture_regression_check"), dict)
        else {}
    )
    if capture_regression_check:
        lines += [
            f"- report present: `{capture_regression_check.get('present')}`",
            f"- status: `{capture_regression_check.get('status')}`",
            f"- mode: `{capture_regression_check.get('mode')}`",
            f"- capture-safe proof: `{capture_regression_check.get('capture_safe_proof_status')}`",
            f"- path: `{capture_regression_check.get('path')}`",
            "",
        ]
    else:
        lines += [
            "- report present: `false`",
            "- capture-safe proof: `missing`",
            "",
        ]
    refresh = report.get("live_comparison_refresh") if isinstance(report.get("live_comparison_refresh"), dict) else {}
    if refresh:
        lines += [
            "## Live Comparison Refresh",
            "",
            "`--refresh` reruns `compare-live-batch.py` from existing live and batch artifacts before "
            "aggregating this report. It does not touch raw capture or the authoritative batch transcript.",
            "",
            f"- requested: `{refresh.get('requested')}`",
            f"- status: `{refresh.get('status')}`",
            f"- attempted sessions: {refresh.get('attempted_sessions', 0)}",
            f"- failed sessions: {refresh.get('failed_sessions', 0)}",
            f"- skipped sessions: {refresh.get('skipped_sessions', 0)}",
            "",
        ]
        failed_refresh = [
            row for row in refresh.get("results") or []
            if isinstance(row, dict) and row.get("status") == "failed"
        ]
        if failed_refresh:
            lines += [
                "| Session | Reason | Return Code |",
                "| --- | --- | ---: |",
            ]
            for row in failed_refresh[:20]:
                lines.append(
                    f"| `{row.get('session')}` | `{row.get('reason')}` | {row.get('returncode')} |"
                )
            lines.append("")
    candidate_scope = (
        report.get("capture_safe_candidate_scope")
        if isinstance(report.get("capture_safe_candidate_scope"), dict)
        else {}
    )
    candidate_dimensions = (
        report.get("real_capture_safe_candidate_parity_dimensions")
        if isinstance(report.get("real_capture_safe_candidate_parity_dimensions"), dict)
        else {}
    )
    if candidate_scope:
        lines += [
            "## Capture-Safe Candidate Evidence",
            "",
            "This is a narrower diagnostic slice. It excludes historical live runs that failed capture safety "
            "or required artifact checks, but it does not allow live promotion.",
            "",
            f"- definition: {candidate_scope.get('definition')}",
            f"- sessions: {candidate_scope.get('sessions', 0)}",
            f"- passing sessions: {candidate_scope.get('passing_sessions', 0)}",
            f"- blocking dimensions: {', '.join(candidate_scope.get('blocking_dimensions') or []) or 'none'}",
            f"- new real live collection allowed: `{candidate_scope.get('new_real_live_collection_allowed')}`",
            f"- controlled real live pilot allowed: `{candidate_scope.get('controlled_real_live_pilot_allowed')}`",
            "",
        ]
        candidate_next = (
            candidate_scope.get("next_focus") if isinstance(candidate_scope.get("next_focus"), dict) else {}
        )
        if candidate_next:
            lines += [
                f"- next candidate focus: `{candidate_next.get('action_id')}` ({candidate_next.get('title')})",
                "",
            ]
        lines += [
            "| Dimension | Required | Counts | Issue sessions |",
            "| --- | --- | --- | --- |",
        ]
        for key, value in candidate_dimensions.items():
            if not isinstance(value, dict):
                continue
            counts = value.get("counts") if isinstance(value.get("counts"), dict) else {}
            counts_text = ", ".join(f"{status}: {count}" for status, count in sorted(counts.items())) or "-"
            issue_sessions = value.get("issue_sessions") if isinstance(value.get("issue_sessions"), list) else []
            lines.append(
                f"| `{key}` | `{value.get('promotion_required')}` | {counts_text} | {len(issue_sessions)} |"
            )
        lines.append("")
    policy_diagnostics = (
        report.get("live_local_recall_rescue_policy_diagnostics")
        if isinstance(report.get("live_local_recall_rescue_policy_diagnostics"), dict)
        else {}
    )
    if policy_diagnostics:
        lines += [
            "## Live Local Recall Rescue Policy Diagnostics",
            "",
            "This is diagnostic only. It compares suppressed-mic rescue policies against batch labels and "
            "does not promote live output.",
            "",
            "| Scope | Status | Best live policy | Recommended policy | Local sec | Remote-risk sec | Precision | Missing-Me recovered sec |",
            "| --- | --- | --- | --- | ---: | ---: | ---: | ---: |",
        ]
        for scope in ("real", "capture_safe_candidate", "capture_safe_evaluable"):
            payload = policy_diagnostics.get(scope) if isinstance(policy_diagnostics.get(scope), dict) else {}
            recommended = (
                payload.get("recommended_policy")
                if isinstance(payload.get("recommended_policy"), dict)
                else {}
            )
            best = (
                payload.get("best_policy")
                if isinstance(payload.get("best_policy"), dict)
                else recommended
            )
            precision = best.get("precision_proxy")
            precision_text = "-" if precision is None else f"{safe_float(precision):.3f}"
            lines.append(
                f"| `{scope}` | `{payload.get('status')}` | `{best.get('policy') or '-'}` "
                f"| `{recommended.get('policy') or '-'}` "
                f"| {safe_float(best.get('local_seconds')):.2f} "
                f"| {safe_float(best.get('remote_risk_seconds')):.2f} "
                f"| {precision_text} "
                f"| {safe_float(best.get('missing_me_recovered_seconds')):.2f} |"
            )
        candidate_diag = (
            policy_diagnostics.get("capture_safe_candidate")
            if isinstance(policy_diagnostics.get("capture_safe_candidate"), dict)
            else {}
        )
        candidate_policies = [
            row for row in candidate_diag.get("policies") or []
            if isinstance(row, dict) and safe_float(row.get("selected_seconds")) > 0.0
        ]
        if candidate_policies:
            lines += [
                "",
                "Capture-safe candidate policy details:",
                "",
                "| Policy | Local sec | Remote-risk sec | Precision | Recall | Recovered sec | Safe | Material |",
                "| --- | ---: | ---: | ---: | ---: | ---: | --- | --- |",
            ]
            for row in sorted(candidate_policies, key=lambda item: safe_float(item.get("local_seconds")), reverse=True):
                precision = row.get("precision_proxy")
                recall = row.get("local_recall_proxy")
                precision_text = "-" if precision is None else f"{safe_float(precision):.3f}"
                recall_text = "-" if recall is None else f"{safe_float(recall):.3f}"
                lines.append(
                    f"| `{row.get('policy')}` | {safe_float(row.get('local_seconds')):.2f} "
                    f"| {safe_float(row.get('remote_risk_seconds')):.2f} "
                    f"| {precision_text} | {recall_text} "
                    f"| {safe_float(row.get('missing_me_recovered_seconds')):.2f} "
                    f"| `{row.get('safe_candidate')}` "
                    f"| `{row.get('material_candidate')}` |"
                )
        lines.append("")
    rescue_lab = (
        report.get("live_local_recall_rescue_lab")
        if isinstance(report.get("live_local_recall_rescue_lab"), dict)
        else {}
    )
    candidate_rescue_lab = (
        report.get("capture_safe_candidate_live_local_recall_rescue_lab")
        if isinstance(report.get("capture_safe_candidate_live_local_recall_rescue_lab"), dict)
        else {}
    )
    evaluable_rescue_lab = (
        report.get("capture_safe_evaluable_live_local_recall_rescue_lab")
        if isinstance(report.get("capture_safe_evaluable_live_local_recall_rescue_lab"), dict)
        else {}
    )
    if rescue_lab:
        lines += [
            "## Live Local Recall Rescue Lab",
            "",
            "This is diagnostic only. It ranks suppressed mic ASR segments using batch labels to show "
            "where current candidate rescue policies help, where they would leak remote, and where stronger "
            "local-speaker evidence is needed.",
            "",
            "| Scope | Segments | Local sec | Live-policy local sec | Live-policy remote-risk sec | Speaker-evidence needed sec | Recommended next |",
            "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
        for scope, payload in (
            ("real", rescue_lab),
            ("capture_safe_candidate", candidate_rescue_lab),
            ("capture_safe_evaluable", evaluable_rescue_lab),
        ):
            if not isinstance(payload, dict):
                continue
            lines.append(
                f"| `{scope}` | {safe_int(payload.get('segment_count'))} "
                f"| {safe_float(payload.get('local_seconds')):.2f} "
                f"| {safe_float(payload.get('live_policy_local_seconds')):.2f} "
                f"| {safe_float(payload.get('live_policy_remote_risk_seconds')):.2f} "
                f"| {safe_float(payload.get('speaker_evidence_needed_seconds')):.2f} "
                f"| `{payload.get('recommended_next')}` |"
            )
        high_value = (
            ((rescue_lab.get("examples") or {}).get("high_value_unrescued") or [])
            if isinstance(rescue_lab.get("examples"), dict)
            else []
        )
        false_positive = (
            ((rescue_lab.get("examples") or {}).get("false_positive_remote_risk") or [])
            if isinstance(rescue_lab.get("examples"), dict)
            else []
        )
        if high_value:
            lines += [
                "",
                "Top high-value unrescued local segments:",
                "",
                "| Session | Time | Duration | Label | Unique tokens | Text |",
                "| --- | ---: | ---: | --- | ---: | --- |",
            ]
            for item in high_value[:8]:
                if not isinstance(item, dict):
                    continue
                text = str(item.get("text") or "").replace("|", "\\|")
                lines.append(
                    f"| `{item.get('session')}` | {safe_float(item.get('start')):.2f} "
                    f"| {safe_float(item.get('duration_sec')):.2f} "
                    f"| `{item.get('batch_role_label')}` "
                    f"| {safe_int(item.get('unique_token_count'))} "
                    f"| {text[:140]} |"
                )
        if false_positive:
            lines += [
                "",
                "Top remote-risk false positives:",
                "",
                "| Session | Time | Duration | Policies | Text |",
                "| --- | ---: | ---: | --- | --- |",
            ]
            for item in false_positive[:8]:
                if not isinstance(item, dict):
                    continue
                policies = ", ".join(str(policy) for policy in item.get("rescue_policy_candidates") or [])
                text = str(item.get("text") or "").replace("|", "\\|")
                lines.append(
                    f"| `{item.get('session')}` | {safe_float(item.get('start')):.2f} "
                    f"| {safe_float(item.get('duration_sec')):.2f} "
                    f"| `{policies}` | {text[:140]} |"
                )
        lines.append("")
    target_me_diag = (
        report.get("live_local_recall_target_me_diagnostics")
        if isinstance(report.get("live_local_recall_target_me_diagnostics"), dict)
        else {}
    )
    if target_me_diag:
        lines += [
            "## Live Local Recall Target-Me Diagnostics",
            "",
            "This is diagnostic only. It audits suppressed live mic segments with local Target-Me speaker "
            "evidence and estimates shadow rescue policies against batch labels. It does not publish live `Me`.",
            "",
            "| Scope | Status | Sessions | Local sec | Possible/confirmed sec | Remote-risk sec | Recommended policy | Next |",
            "| --- | --- | ---: | ---: | ---: | ---: | --- | --- |",
        ]
        for scope in ("real", "capture_safe_candidate", "capture_safe_evaluable"):
            payload = target_me_diag.get(scope) if isinstance(target_me_diag.get(scope), dict) else {}
            recommended = (
                payload.get("recommended_policy")
                if isinstance(payload.get("recommended_policy"), dict)
                else {}
            )
            lines.append(
                f"| `{scope}` | `{payload.get('status')}` "
                f"| {safe_int(payload.get('sessions_with_audit'))} "
                f"| {safe_float(payload.get('local_seconds')):.2f} "
                f"| {safe_float(payload.get('target_me_possible_or_confirmed_local_seconds')):.2f} "
                f"| {safe_float(payload.get('remote_risk_seconds')):.2f} "
                f"| `{recommended.get('policy') or '-'}` "
                f"| `{payload.get('recommended_next')}` |"
            )
        real_target_me = target_me_diag.get("real") if isinstance(target_me_diag.get("real"), dict) else {}
        real_policy_metrics = (
            real_target_me.get("target_me_rescue_policy_metrics")
            if isinstance(real_target_me.get("target_me_rescue_policy_metrics"), dict)
            else {}
        )
        if real_policy_metrics:
            lines += [
                "",
                "Real-scope Target-Me policy details:",
                "",
                "| Policy | Local sec | Remote-risk sec | Precision | Audited local recall | Missing-Me recovered sec |",
                "| --- | ---: | ---: | ---: | ---: | ---: |",
            ]
            for policy in TARGET_ME_RESCUE_POLICIES:
                metrics = (
                    real_policy_metrics.get(policy)
                    if isinstance(real_policy_metrics.get(policy), dict)
                    else {}
                )
                precision = metrics.get("precision_proxy")
                recall = metrics.get("audited_local_recall_proxy")
                precision_text = "-" if precision is None else f"{safe_float(precision):.3f}"
                recall_text = "-" if recall is None else f"{safe_float(recall):.3f}"
                lines.append(
                    f"| `{policy}` | {safe_float(metrics.get('local_seconds')):.2f} "
                    f"| {safe_float(metrics.get('remote_risk_seconds')):.2f} "
                    f"| {precision_text} | {recall_text} "
                    f"| {safe_float(metrics.get('missing_me_recovered_seconds')):.2f} |"
                )
        lines.append("")
    candidate_gap_examples = (
        report.get("capture_safe_candidate_local_recall_gap_examples")
        if isinstance(report.get("capture_safe_candidate_local_recall_gap_examples"), dict)
        else {}
    )
    evaluable_gap_examples = (
        report.get("capture_safe_evaluable_local_recall_gap_examples")
        if isinstance(report.get("capture_safe_evaluable_local_recall_gap_examples"), dict)
        else {}
    )
    all_gap_examples = (
        report.get("live_local_recall_gap_examples")
        if isinstance(report.get("live_local_recall_gap_examples"), dict)
        else {}
    )
    if candidate_gap_examples or all_gap_examples:
        lines += [
            "## Live Local Recall Gap Examples",
            "",
            "These are batch `Me` utterances that were not visible as live `Me` turns. They are the "
            "main evidence for the current `fix_live_local_recall_gap` focus.",
            "",
            f"- real examples: {all_gap_examples.get('item_count', 0)} / {all_gap_examples.get('seconds', 0.0)} sec",
            "- capture-safe candidate examples: "
            f"{candidate_gap_examples.get('item_count', 0)} / {candidate_gap_examples.get('seconds', 0.0)} sec",
            "- capture-safe evaluable examples: "
            f"{evaluable_gap_examples.get('item_count', 0)} / {evaluable_gap_examples.get('seconds', 0.0)} sec",
            "",
        ]
        examples_source = evaluable_gap_examples if evaluable_gap_examples else candidate_gap_examples
        examples = examples_source.get("examples") if isinstance(examples_source.get("examples"), list) else []
        if examples:
            lines += [
                "| Session | Time | Duration | Suppressed Mic Recall | Batch ID | Text |",
                "| --- | ---: | ---: | ---: | --- | --- |",
            ]
            for item in examples[:12]:
                if not isinstance(item, dict):
                    continue
                text = str(item.get("text") or "").replace("|", "\\|")
                if len(text) > 110:
                    text = text[:107].rstrip() + "..."
                lines.append(
                    f"| `{item.get('session')}` | {safe_float(item.get('start')):.2f}-{safe_float(item.get('end')):.2f} "
                    f"| {safe_float(item.get('duration_sec')):.2f} "
                    f"| {safe_float(item.get('recall_in_suppressed_mic')):.2f} "
                    f"| `{item.get('batch_id')}` | {text} |"
                )
            lines.append("")
    lines += [
        "## Objective Audit",
        "",
    ]
    objective_audit = report.get("objective_audit") if isinstance(report.get("objective_audit"), dict) else {}
    if objective_audit:
        lines += [
            f"- objective: {objective_audit.get('objective')}",
            f"- overall status: `{objective_audit.get('overall_status')}`",
            f"- ready for live promotion: `{objective_audit.get('ready_for_live_promotion')}`",
            f"- batch authoritative: `{objective_audit.get('batch_authoritative')}`",
            f"- new real live collection allowed: `{objective_audit.get('new_real_live_collection_allowed')}`",
            f"- controlled real live pilot allowed: `{objective_audit.get('controlled_real_live_pilot_allowed')}`",
            f"- blocking dimensions: {', '.join(objective_audit.get('blocking_dimensions') or []) or 'none'}",
        "",
        ]
        next_focus = objective_audit.get("next_focus") if isinstance(objective_audit.get("next_focus"), dict) else {}
        if next_focus:
            lines += [
                "### Next Focus",
                "",
                f"- dimension: `{next_focus.get('dimension') or 'none'}`",
                f"- action: `{next_focus.get('action_id')}`",
                f"- title: {next_focus.get('title')}",
                f"- recommended next: {next_focus.get('recommended_next')}",
                "",
            ]
        candidate_scope = (
            objective_audit.get("capture_safe_candidate_scope")
            if isinstance(objective_audit.get("capture_safe_candidate_scope"), dict)
            else {}
        )
        if candidate_scope:
            lines += [
                "### Capture-Safe Candidate Scope",
                "",
                f"- sessions: {candidate_scope.get('sessions', 0)}",
                f"- passing sessions: {candidate_scope.get('passing_sessions', 0)}",
                f"- blocking dimensions: {', '.join(candidate_scope.get('blocking_dimensions') or []) or 'none'}",
                f"- controlled real live pilot allowed: `{candidate_scope.get('controlled_real_live_pilot_allowed')}`",
                "",
            ]
            candidate_next = (
                candidate_scope.get("next_focus") if isinstance(candidate_scope.get("next_focus"), dict) else {}
            )
            if candidate_next:
                lines += [
                    f"- next candidate focus: `{candidate_next.get('action_id')}` ({candidate_next.get('title')})",
                    "",
                ]
        lines += [
            "| Check | Status | Evidence |",
            "| --- | --- | --- |",
        ]
        for row in objective_audit.get("rows") or []:
            if not isinstance(row, dict):
                continue
            evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
            evidence_text = json.dumps(evidence, ensure_ascii=False, sort_keys=True)
            if len(evidence_text) > 240:
                evidence_text = evidence_text[:237] + "..."
            lines.append(f"| `{row.get('id')}` | `{row.get('status')}` | `{evidence_text}` |")
        lines.append("")
    lines += [
        "## Real Parity Dimensions",
        "",
        "Only `real_meeting` live sessions count toward promotion. Diagnostic and lab sessions remain evidence, "
        "but they do not satisfy real coverage.",
        "",
        "| Dimension | Required | Counts | Issue sessions |",
        "| --- | --- | --- | --- |",
    ]
    real_dimensions = report.get("real_parity_dimensions") if isinstance(report.get("real_parity_dimensions"), dict) else {}
    for key, value in real_dimensions.items():
        if not isinstance(value, dict):
            continue
        counts = value.get("counts") if isinstance(value.get("counts"), dict) else {}
        counts_text = ", ".join(f"{status}: {count}" for status, count in sorted(counts.items())) or "-"
        issue_sessions = value.get("issue_sessions") if isinstance(value.get("issue_sessions"), list) else []
        lines.append(
            f"| `{key}` | `{value.get('promotion_required')}` | {counts_text} | {len(issue_sessions)} |"
        )
    triage_summary = (
        report.get("real_blocker_triage_summary")
        if isinstance(report.get("real_blocker_triage_summary"), dict)
        else {}
    )
    triage_categories = (
        triage_summary.get("by_category") if isinstance(triage_summary.get("by_category"), dict) else {}
    )
    if triage_summary:
        lines += [
            "",
            "## Real Blocker Triage",
            "",
            "This section groups only real-meeting non-passing gates into actionable buckets. It does "
            "not suggest collecting new live meetings while live capture is quarantined.",
            "",
            f"- triage items: {triage_summary.get('total_items', 0)}",
            f"- affected sessions: {triage_summary.get('session_count', 0)}",
            f"- severities: "
            + (
                ", ".join(
                    f"{severity}: {count}"
                    for severity, count in sorted((triage_summary.get("by_severity") or {}).items())
                )
                or "-"
            ),
            "",
            "| Category | Items | Sessions | Severity | Next |",
            "| --- | ---: | --- | --- | --- |",
        ]
        for category, value in sorted(triage_categories.items()):
            if not isinstance(value, dict):
                continue
            sessions = value.get("sessions") if isinstance(value.get("sessions"), list) else []
            severities = value.get("severities") if isinstance(value.get("severities"), dict) else {}
            severity_text = ", ".join(f"{key}: {count}" for key, count in sorted(severities.items())) or "-"
            session_text = ", ".join(f"`{session}`" for session in sessions[:4])
            if len(sessions) > 4:
                session_text += f", +{len(sessions) - 4}"
            lines.append(
                f"| `{category}` | {value.get('item_count', 0)} | {session_text or '-'} | "
                f"{severity_text} | {value.get('recommended_next') or '-'} |"
            )
    lines += [
        "",
        "## All Parity Dimensions",
        "",
        "| Dimension | Required | Counts | Issue sessions |",
        "| --- | --- | --- | --- |",
    ]
    dimensions = report.get("parity_dimensions") if isinstance(report.get("parity_dimensions"), dict) else {}
    for key, value in dimensions.items():
        if not isinstance(value, dict):
            continue
        counts = value.get("counts") if isinstance(value.get("counts"), dict) else {}
        counts_text = ", ".join(f"{status}: {count}" for status, count in sorted(counts.items())) or "-"
        issue_sessions = value.get("issue_sessions") if isinstance(value.get("issue_sessions"), list) else []
        issue_text = str(len(issue_sessions))
        lines.append(
            f"| `{key}` | `{value.get('promotion_required')}` | {counts_text} | {issue_text} |"
        )
    lines += [
        "",
        "## Recommended Next",
        "",
    ]
    for command in report.get("next_commands") or []:
        lines.append(f"- `{command}`")
    issues = [issue for issue in report.get("gate_issues") or [] if isinstance(issue, dict)]
    if issues:
        lines += ["", "## Gate Issues", ""]
        for issue in issues:
            lines.append(
                f"- `{issue.get('session')}` gate `{issue.get('gate')}` is `{issue.get('status')}`: "
                f"{issue.get('reason') or '-'}"
            )
    lines += [
        "",
        "## Sessions",
        "",
    ]
    for row in report["sessions"]:
        if not row["live_present"]:
            continue
        final = row.get("final_reconcile") or {}
        metrics = row.get("metrics") or {}
        lines.append(
            f"- `{row['session']}`: comparison `{row.get('comparison_status')}`, "
            f"parity `{row.get('parity_status')}`, final `{final.get('status') or 'missing'}`, "
            f"speedup `{final.get('speedup_status') or 'unknown'}`, "
            f"meaningful `{row.get('meaningful_compared')}`, gates passed `{row.get('all_parity_gates_passed')}`, "
            f"order mismatches `{metrics.get('live_order_mismatch_count')}`, "
            f"missing Me sec `{metrics.get('live_missing_me_seconds')}`, "
            f"suspicious batch-Me sec `{metrics.get('live_suspicious_batch_me_missing_seconds')}`, "
            f"remote-in-Me sec `{metrics.get('live_suspected_remote_leak_in_me_seconds')}`, "
            f"boundary issues `{metrics.get('live_boundary_gate_issue_count')}`, "
            f"boundary suppressed `{metrics.get('live_boundary_gate_suppressed_count')}`, "
            f"boundary resolved `{metrics.get('live_boundary_gate_resolved_suppressed_count')}`, "
            f"boundary unresolved `{metrics.get('live_boundary_gate_unresolved_suppressed_count')}`"
        )
    if report.get("blockers"):
        lines += ["", "## Blockers", ""]
        for key, count in sorted(report["blockers"].items()):
            lines.append(f"- `{key}`: {count}")
    strict = report.get("strict_coverage") or {}
    if strict.get("requested"):
        lines += ["", "## Strict Coverage", ""]
        lines.append(f"- status: `{strict.get('status')}`")
        failures = strict.get("failures") if isinstance(strict, dict) else []
        for row in failures or []:
            lines.append(f"- `{row.get('id')}`: {row.get('message')} (value: `{row.get('value')}`, limit: `{row.get('limit')}`)")
    target = report.get("coverage_target") if isinstance(report.get("coverage_target"), dict) else {}
    if target:
        lines += ["", "## Coverage Target", ""]
        lines.append(f"- status: `{target.get('status')}`")
        lines.append(f"- target live sessions: `{target.get('target_live_sessions')}`")
        lines.append(f"- target meaningful comparisons: `{target.get('target_meaningful_compared_sessions')}`")
        lines.append(f"- target passing comparisons: `{target.get('target_passing_compared_sessions')}`")
        lines.append(f"- live sessions remaining: `{target.get('live_sessions_remaining')}`")
        lines.append(f"- passing comparisons remaining: `{target.get('passing_compared_sessions_remaining')}`")
    lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    args = parse_args()
    root = args.sessions_root
    sessions = resolve_targets(args)
    report = build_report(sessions, root, args)
    json_path = args.out_dir / "live_corpus_gates_report.json"
    md_path = args.out_dir / "live_corpus_gates_report.md"
    write_json(json_path, report)
    write_markdown(md_path, report)
    summary = report["summary"]
    print(f"live_corpus_gates: {json_path}")
    print(f"status: {summary['target_status']}")
    print(f"live_sessions: {summary['live_sessions']}/{summary['sessions_total']}")
    print(f"real_live_sessions: {summary.get('real_live_sessions', 0)}")
    print(f"diagnostic_live_sessions: {summary.get('diagnostic_live_sessions', 0)}")
    print(f"live_comparison_refresh: {summary.get('live_comparison_refresh_status')}")
    print(f"live_comparison_refresh_attempted_sessions: {summary.get('live_comparison_refresh_attempted_sessions', 0)}")
    print(f"live_comparison_refresh_failed_sessions: {summary.get('live_comparison_refresh_failed_sessions', 0)}")
    print(f"live_comparison_refresh_skipped_sessions: {summary.get('live_comparison_refresh_skipped_sessions', 0)}")
    print(f"real_meaningful_compared_sessions: {summary.get('real_meaningful_compared_sessions', 0)}")
    print(f"real_passing_compared_sessions: {summary.get('real_passing_compared_sessions', 0)}")
    print(f"real_capture_safe_candidate_sessions: {summary.get('real_capture_safe_candidate_sessions', 0)}")
    print(
        "real_capture_safe_candidate_passing_sessions: "
        f"{summary.get('real_capture_safe_candidate_passing_sessions', 0)}"
    )
    print(f"meaningful_compared_sessions: {summary['meaningful_compared_sessions']}")
    print(f"passing_compared_sessions: {summary['passing_compared_sessions']}")
    print(f"promotion_decision: {summary['promotion_decision']}")
    print(f"live_order_mismatch_count: {summary.get('live_order_mismatch_count', 0)}")
    print(f"real_live_order_mismatch_count: {summary.get('real_live_order_mismatch_count', 0)}")
    print(
        "real_live_order_mismatch_by_category: "
        f"{json.dumps(summary.get('real_live_order_mismatch_by_category', {}), ensure_ascii=False)}"
    )
    print(
        "real_live_order_mismatch_by_primary_risk: "
        f"{json.dumps(summary.get('real_live_order_mismatch_by_primary_risk', {}), ensure_ascii=False)}"
    )
    print(
        "real_live_order_mismatch_by_confidence: "
        f"{json.dumps(summary.get('real_live_order_mismatch_by_confidence', {}), ensure_ascii=False)}"
    )
    print(f"real_live_role_constrained_order_mismatch_count: {summary.get('real_live_role_constrained_order_mismatch_count', 0)}")
    print(
        "real_live_role_constrained_order_mismatch_by_category: "
        f"{json.dumps(summary.get('real_live_role_constrained_order_mismatch_by_category', {}), ensure_ascii=False)}"
    )
    print(
        "real_live_role_constrained_order_mismatch_by_confidence: "
        f"{json.dumps(summary.get('real_live_role_constrained_order_mismatch_by_confidence', {}), ensure_ascii=False)}"
    )
    print(
        "real_live_contentful_role_constrained_order_mismatch_count: "
        f"{summary.get('real_live_contentful_role_constrained_order_mismatch_count', 0)}"
    )
    print(
        "real_live_contentful_role_constrained_order_mismatch_by_category: "
        f"{json.dumps(summary.get('real_live_contentful_role_constrained_order_mismatch_by_category', {}), ensure_ascii=False)}"
    )
    print(
        "real_live_contentful_role_constrained_order_mismatch_by_confidence: "
        f"{json.dumps(summary.get('real_live_contentful_role_constrained_order_mismatch_by_confidence', {}), ensure_ascii=False)}"
    )
    print(
        "real_live_contentful_role_constrained_order_mismatch_by_ambiguity: "
        f"{json.dumps(summary.get('real_live_contentful_role_constrained_order_mismatch_by_ambiguity', {}), ensure_ascii=False)}"
    )
    print(
        "real_live_unambiguous_contentful_role_constrained_order_mismatch_count: "
        f"{summary.get('real_live_unambiguous_contentful_role_constrained_order_mismatch_count', 0)}"
    )
    print(f"live_missing_me_seconds: {summary.get('live_missing_me_seconds', 0.0)}")
    print(f"real_live_missing_me_seconds: {summary.get('real_live_missing_me_seconds', 0.0)}")
    print(
        "real_live_segment_role_gate_candidate_chunk_count: "
        f"{summary.get('real_live_segment_role_gate_candidate_chunk_count', 0)}"
    )
    print(
        "real_live_segment_role_gate_candidate_kept_segment_count: "
        f"{summary.get('real_live_segment_role_gate_candidate_kept_segment_count', 0)}"
    )
    print(
        "real_live_rescue_shadow_candidate_chunk_count: "
        f"{summary.get('real_live_rescue_shadow_candidate_chunk_count', 0)}"
    )
    print(
        "real_live_rescue_shadow_candidate_segment_count: "
        f"{summary.get('real_live_rescue_shadow_candidate_segment_count', 0)}"
    )
    print(
        "real_live_rescue_shadow_order_mismatch_count: "
        f"{summary.get('real_live_rescue_shadow_order_mismatch_count', 0)}"
    )
    print(
        "real_live_rescue_shadow_order_mismatch_by_category: "
        f"{json.dumps(summary.get('real_live_rescue_shadow_order_mismatch_by_category', {}), ensure_ascii=False)}"
    )
    print(
        "real_live_rescue_shadow_order_mismatch_by_primary_risk: "
        f"{json.dumps(summary.get('real_live_rescue_shadow_order_mismatch_by_primary_risk', {}), ensure_ascii=False)}"
    )
    print(
        "real_live_rescue_shadow_order_mismatch_by_confidence: "
        f"{json.dumps(summary.get('real_live_rescue_shadow_order_mismatch_by_confidence', {}), ensure_ascii=False)}"
    )
    print(
        "real_live_rescue_shadow_role_constrained_order_mismatch_count: "
        f"{summary.get('real_live_rescue_shadow_role_constrained_order_mismatch_count', 0)}"
    )
    print(
        "real_live_rescue_shadow_role_constrained_order_mismatch_by_category: "
        f"{json.dumps(summary.get('real_live_rescue_shadow_role_constrained_order_mismatch_by_category', {}), ensure_ascii=False)}"
    )
    print(
        "real_live_rescue_shadow_role_constrained_order_mismatch_by_confidence: "
        f"{json.dumps(summary.get('real_live_rescue_shadow_role_constrained_order_mismatch_by_confidence', {}), ensure_ascii=False)}"
    )
    print(
        "real_live_rescue_shadow_contentful_role_constrained_order_mismatch_count: "
        f"{summary.get('real_live_rescue_shadow_contentful_role_constrained_order_mismatch_count', 0)}"
    )
    print(
        "real_live_rescue_shadow_contentful_role_constrained_order_mismatch_by_category: "
        f"{json.dumps(summary.get('real_live_rescue_shadow_contentful_role_constrained_order_mismatch_by_category', {}), ensure_ascii=False)}"
    )
    print(
        "real_live_rescue_shadow_contentful_role_constrained_order_mismatch_by_confidence: "
        f"{json.dumps(summary.get('real_live_rescue_shadow_contentful_role_constrained_order_mismatch_by_confidence', {}), ensure_ascii=False)}"
    )
    print(
        "real_live_rescue_shadow_contentful_role_constrained_order_mismatch_by_ambiguity: "
        f"{json.dumps(summary.get('real_live_rescue_shadow_contentful_role_constrained_order_mismatch_by_ambiguity', {}), ensure_ascii=False)}"
    )
    print(
        "real_live_rescue_shadow_unambiguous_contentful_role_constrained_order_mismatch_count: "
        f"{summary.get('real_live_rescue_shadow_unambiguous_contentful_role_constrained_order_mismatch_count', 0)}"
    )
    print(
        "real_live_rescue_shadow_suspected_remote_leak_in_me_seconds: "
        f"{summary.get('real_live_rescue_shadow_suspected_remote_leak_in_me_seconds', 0.0)}"
    )
    print(
        "real_live_rescue_shadow_missing_me_seconds_after: "
        f"{summary.get('real_live_rescue_shadow_missing_me_seconds_after', 0.0)}"
    )
    print(
        "real_live_rescue_shadow_missing_me_recovered_seconds: "
        f"{summary.get('real_live_rescue_shadow_missing_me_recovered_seconds', 0.0)}"
    )
    print(
        "real_live_suppressed_mic_asr_me_dominant_segment_count: "
        f"{summary.get('real_live_suppressed_mic_asr_me_dominant_segment_count', 0)}"
    )
    print(
        "real_live_suppressed_mic_asr_me_dominant_segment_seconds: "
        f"{summary.get('real_live_suppressed_mic_asr_me_dominant_segment_seconds', 0.0)}"
    )
    print(
        "real_live_suppressed_mic_asr_mixed_segment_count: "
        f"{summary.get('real_live_suppressed_mic_asr_mixed_segment_count', 0)}"
    )
    print(
        "real_live_suppressed_mic_asr_mixed_segment_seconds: "
        f"{summary.get('real_live_suppressed_mic_asr_mixed_segment_seconds', 0.0)}"
    )
    print(
        "real_live_rescue_policy_current_text_segment_gate_local_seconds: "
        f"{summary.get('real_live_rescue_policy_current_text_segment_gate_local_seconds', 0.0)}"
    )
    print(
        "real_live_rescue_policy_current_text_segment_gate_remote_risk_seconds: "
        f"{summary.get('real_live_rescue_policy_current_text_segment_gate_remote_risk_seconds', 0.0)}"
    )
    print(
        "real_live_rescue_policy_strict_text_unique_v1_local_seconds: "
        f"{summary.get('real_live_rescue_policy_strict_text_unique_v1_local_seconds', 0.0)}"
    )
    print(
        "real_live_rescue_policy_strict_text_unique_v1_remote_risk_seconds: "
        f"{summary.get('real_live_rescue_policy_strict_text_unique_v1_remote_risk_seconds', 0.0)}"
    )
    print(
        "real_live_rescue_policy_remote_silent_text_v1_local_seconds: "
        f"{summary.get('real_live_rescue_policy_remote_silent_text_v1_local_seconds', 0.0)}"
    )
    print(
        "real_live_rescue_policy_remote_silent_text_v1_remote_risk_seconds: "
        f"{summary.get('real_live_rescue_policy_remote_silent_text_v1_remote_risk_seconds', 0.0)}"
    )
    print(
        "real_live_rescue_policy_audio_remote_quiet_v1_local_seconds: "
        f"{summary.get('real_live_rescue_policy_audio_remote_quiet_v1_local_seconds', 0.0)}"
    )
    print(
        "real_live_rescue_policy_audio_remote_quiet_v1_remote_risk_seconds: "
        f"{summary.get('real_live_rescue_policy_audio_remote_quiet_v1_remote_risk_seconds', 0.0)}"
    )
    print(
        "real_live_rescue_policy_audio_mic_dominant_v1_local_seconds: "
        f"{summary.get('real_live_rescue_policy_audio_mic_dominant_v1_local_seconds', 0.0)}"
    )
    print(
        "real_live_rescue_policy_audio_mic_dominant_v1_remote_risk_seconds: "
        f"{summary.get('real_live_rescue_policy_audio_mic_dominant_v1_remote_risk_seconds', 0.0)}"
    )
    print(
        "real_live_rescue_policy_audio_low_coherence_v1_local_seconds: "
        f"{summary.get('real_live_rescue_policy_audio_low_coherence_v1_local_seconds', 0.0)}"
    )
    print(
        "real_live_rescue_policy_audio_low_coherence_v1_remote_risk_seconds: "
        f"{summary.get('real_live_rescue_policy_audio_low_coherence_v1_remote_risk_seconds', 0.0)}"
    )
    print(
        "real_live_rescue_policy_audio_safe_union_v1_local_seconds: "
        f"{summary.get('real_live_rescue_policy_audio_safe_union_v1_local_seconds', 0.0)}"
    )
    print(
        "real_live_rescue_policy_audio_safe_union_v1_remote_risk_seconds: "
        f"{summary.get('real_live_rescue_policy_audio_safe_union_v1_remote_risk_seconds', 0.0)}"
    )
    print(
        "real_live_rescue_policy_audio_safe_union_v1_missing_me_recovered_seconds: "
        f"{summary.get('real_live_rescue_policy_audio_safe_union_v1_missing_me_recovered_seconds', 0.0)}"
    )
    print(
        "real_live_rescue_policy_batch_oracle_local_ceiling_local_seconds: "
        f"{summary.get('real_live_rescue_policy_batch_oracle_local_ceiling_local_seconds', 0.0)}"
    )
    print(f"live_suspicious_batch_me_missing_seconds: {summary.get('live_suspicious_batch_me_missing_seconds', 0.0)}")
    print(
        "real_live_suspicious_batch_me_missing_seconds: "
        f"{summary.get('real_live_suspicious_batch_me_missing_seconds', 0.0)}"
    )
    print(f"live_suspected_remote_leak_in_me_seconds: {summary.get('live_suspected_remote_leak_in_me_seconds', 0.0)}")
    print(
        "real_live_suspected_remote_leak_in_me_seconds: "
        f"{summary.get('real_live_suspected_remote_leak_in_me_seconds', 0.0)}"
    )
    print(f"adjacent_duplicate_chunk_count: {summary.get('adjacent_duplicate_chunk_count', 0)}")
    print(f"real_adjacent_duplicate_chunk_count: {summary.get('real_adjacent_duplicate_chunk_count', 0)}")
    print(f"live_boundary_gate_issue_count: {summary.get('live_boundary_gate_issue_count', 0)}")
    print(f"real_live_boundary_gate_issue_count: {summary.get('real_live_boundary_gate_issue_count', 0)}")
    print(f"live_boundary_gate_suppressed_count: {summary.get('live_boundary_gate_suppressed_count', 0)}")
    print(f"real_live_boundary_gate_suppressed_count: {summary.get('real_live_boundary_gate_suppressed_count', 0)}")
    print(
        "real_live_boundary_gate_resolved_suppressed_count: "
        f"{summary.get('real_live_boundary_gate_resolved_suppressed_count', 0)}"
    )
    print(
        "real_live_boundary_gate_unresolved_suppressed_count: "
        f"{summary.get('real_live_boundary_gate_unresolved_suppressed_count', 0)}"
    )
    print(f"strict_coverage: {summary.get('strict_coverage_status')}")
    print(f"coverage_target: {summary.get('coverage_target_status')}")
    print(f"coverage_target_live_remaining: {summary.get('coverage_target_live_sessions_remaining', 0)}")
    print(f"coverage_target_passing_remaining: {summary.get('coverage_target_passing_sessions_remaining', 0)}")
    print(f"coverage_path: {summary.get('coverage_path_status')}")
    print(
        "coverage_path_historical_non_candidate_sessions: "
        f"{summary.get('coverage_path_historical_non_candidate_sessions', 0)}"
    )
    print(f"live_evidence_mode: {summary.get('live_evidence_mode')}")
    print(f"new_real_live_collection_allowed: {summary.get('new_real_live_collection_allowed')}")
    print(f"controlled_real_live_pilot_allowed: {summary.get('controlled_real_live_pilot_allowed')}")
    blocking_dimensions = summary.get("promotion_blocking_dimensions") or []
    print(f"promotion_blocking_dimensions: {', '.join(blocking_dimensions) if blocking_dimensions else 'none'}")
    policy_diagnostics = (
        report.get("live_local_recall_rescue_policy_diagnostics")
        if isinstance(report.get("live_local_recall_rescue_policy_diagnostics"), dict)
        else {}
    )
    candidate_policy_diag = (
        policy_diagnostics.get("capture_safe_candidate")
        if isinstance(policy_diagnostics.get("capture_safe_candidate"), dict)
        else {}
    )
    recommended_policy = (
        candidate_policy_diag.get("recommended_policy")
        if isinstance(candidate_policy_diag.get("recommended_policy"), dict)
        else {}
    )
    best_policy = (
        candidate_policy_diag.get("best_policy")
        if isinstance(candidate_policy_diag.get("best_policy"), dict)
        else recommended_policy
    )
    if candidate_policy_diag:
        print(f"capture_safe_candidate_rescue_policy_status: {candidate_policy_diag.get('status')}")
        if best_policy:
            print(
                "capture_safe_candidate_best_live_rescue_policy: "
                f"{best_policy.get('policy')} "
                f"(local={safe_float(best_policy.get('local_seconds')):.2f}s, "
                f"remote_risk={safe_float(best_policy.get('remote_risk_seconds')):.2f}s)"
            )
        if recommended_policy:
            print(f"capture_safe_candidate_recommended_rescue_policy: {recommended_policy.get('policy')}")
    target_me_diagnostics = (
        report.get("live_local_recall_target_me_diagnostics")
        if isinstance(report.get("live_local_recall_target_me_diagnostics"), dict)
        else {}
    )
    real_target_me = (
        target_me_diagnostics.get("real")
        if isinstance(target_me_diagnostics.get("real"), dict)
        else {}
    )
    candidate_target_me = (
        target_me_diagnostics.get("capture_safe_candidate")
        if isinstance(target_me_diagnostics.get("capture_safe_candidate"), dict)
        else {}
    )
    if real_target_me:
        print(f"real_live_target_me_status: {real_target_me.get('status')}")
        print(
            "real_live_target_me_possible_or_confirmed_local_seconds: "
            f"{safe_float(real_target_me.get('target_me_possible_or_confirmed_local_seconds'))}"
        )
        recommended_target_me = (
            real_target_me.get("recommended_policy")
            if isinstance(real_target_me.get("recommended_policy"), dict)
            else {}
        )
        if recommended_target_me:
            print(f"real_live_target_me_recommended_policy: {recommended_target_me.get('policy')}")
            print(
                "real_live_target_me_recommended_policy_missing_me_recovered_seconds: "
                f"{safe_float(recommended_target_me.get('missing_me_recovered_seconds'))}"
            )
    target_me_shadow_diagnostics = (
        report.get("live_target_me_shadow_policy_diagnostics")
        if isinstance(report.get("live_target_me_shadow_policy_diagnostics"), dict)
        else {}
    )
    real_target_me_shadow = (
        target_me_shadow_diagnostics.get("real")
        if isinstance(target_me_shadow_diagnostics.get("real"), dict)
        else {}
    )
    if real_target_me_shadow:
        print(f"real_live_target_me_shadow_status: {real_target_me_shadow.get('status')}")
        recommended_target_me_shadow = (
            real_target_me_shadow.get("recommended_policy")
            if isinstance(real_target_me_shadow.get("recommended_policy"), dict)
            else {}
        )
        if recommended_target_me_shadow:
            print(
                "real_live_target_me_shadow_recommended_policy: "
                f"{recommended_target_me_shadow.get('policy')}"
            )
            print(
                "real_live_target_me_shadow_recommended_policy_missing_me_recovered_seconds: "
                f"{safe_float(recommended_target_me_shadow.get('missing_me_recovered_seconds'))}"
            )
            print(
                "real_live_target_me_shadow_recommended_policy_remote_risk_seconds: "
                f"{safe_float(recommended_target_me_shadow.get('suspected_remote_leak_in_me_seconds'))}"
            )
            print(
                "real_live_target_me_shadow_recommended_policy_order_mismatch_count: "
                f"{safe_int(recommended_target_me_shadow.get('contentful_role_constrained_order_mismatch_count'))}"
            )
            print(
                "real_live_target_me_shadow_recommended_policy_order_mismatch_delta_count: "
                f"{safe_int(recommended_target_me_shadow.get('contentful_role_constrained_order_mismatch_delta_count'))}"
            )
            print(
                "real_live_target_me_shadow_recommended_policy_rejected_candidate_seconds: "
                f"{safe_float(recommended_target_me_shadow.get('rejected_candidate_seconds'))}"
            )
            print(
                "real_live_target_me_shadow_recommended_policy_rejected_order_seconds: "
                f"{safe_float(recommended_target_me_shadow.get('rejected_would_add_contentful_order_mismatch_seconds'))}"
            )
    target_me_shadow_profile_diagnostics = (
        report.get("live_target_me_shadow_profile_diagnostics")
        if isinstance(report.get("live_target_me_shadow_profile_diagnostics"), dict)
        else {}
    )
    real_target_me_shadow_profile = (
        target_me_shadow_profile_diagnostics.get("real")
        if isinstance(target_me_shadow_profile_diagnostics.get("real"), dict)
        else {}
    )
    if real_target_me_shadow_profile:
        print(f"real_live_target_me_shadow_profile_status: {real_target_me_shadow_profile.get('status')}")
        best_target_me_shadow_profile = (
            real_target_me_shadow_profile.get("best_profile")
            if isinstance(real_target_me_shadow_profile.get("best_profile"), dict)
            else {}
        )
        if best_target_me_shadow_profile:
            print(
                "real_live_target_me_shadow_profile_best_policy: "
                f"{best_target_me_shadow_profile.get('policy')}"
            )
            print(
                "real_live_target_me_shadow_profile_all_parity_gates_passed_sessions: "
                f"{safe_int(best_target_me_shadow_profile.get('all_parity_gates_passed_session_count'))}"
            )
            print(
                "real_live_target_me_shadow_profile_non_passing_gate_count: "
                f"{safe_int(best_target_me_shadow_profile.get('non_passing_gate_count'))}"
            )
            print(
                "real_live_target_me_shadow_profile_missing_me_seconds: "
                f"{safe_float(best_target_me_shadow_profile.get('live_missing_me_seconds'))}"
            )
            print(
                "real_live_target_me_shadow_profile_missing_me_visible_in_suppressed_mic_seconds: "
                f"{safe_float(best_target_me_shadow_profile.get('live_missing_me_visible_in_suppressed_mic_seconds'))}"
            )
            print(
                "real_live_target_me_shadow_profile_missing_me_with_target_me_candidate_seconds: "
                f"{safe_float(best_target_me_shadow_profile.get('live_missing_me_with_target_me_candidate_seconds'))}"
            )
            print(
                "real_live_target_me_shadow_profile_remote_leak_seconds: "
                f"{safe_float(best_target_me_shadow_profile.get('live_suspected_remote_leak_in_me_seconds'))}"
            )
            print(
                "real_live_target_me_shadow_profile_removed_live_turn_seconds: "
                f"{safe_float(best_target_me_shadow_profile.get('removed_live_turn_seconds'))}"
            )
            print(
                "real_live_target_me_shadow_profile_visible_suppressed_mic_added_turn_seconds: "
                f"{safe_float(best_target_me_shadow_profile.get('visible_suppressed_mic_added_turn_seconds'))}"
            )
        best_live_target_me_shadow_profile = (
            real_target_me_shadow_profile.get("best_live_implementable_profile")
            if isinstance(real_target_me_shadow_profile.get("best_live_implementable_profile"), dict)
            else {}
        )
        if best_live_target_me_shadow_profile:
            print(
                "real_live_target_me_shadow_profile_best_live_implementable_policy: "
                f"{best_live_target_me_shadow_profile.get('policy')}"
            )
            print(
                "real_live_target_me_shadow_profile_best_live_implementable_non_passing_gate_count: "
                f"{safe_int(best_live_target_me_shadow_profile.get('non_passing_gate_count'))}"
            )
            print(
                "real_live_target_me_shadow_profile_best_live_implementable_missing_me_seconds: "
                f"{safe_float(best_live_target_me_shadow_profile.get('live_missing_me_seconds'))}"
            )
            print(
                "real_live_target_me_shadow_profile_best_live_implementable_remote_leak_seconds: "
                f"{safe_float(best_live_target_me_shadow_profile.get('live_suspected_remote_leak_in_me_seconds'))}"
            )
            print(
                "real_live_target_me_shadow_profile_best_live_implementable_contentful_order_mismatch_count: "
                f"{safe_int(best_live_target_me_shadow_profile.get('live_contentful_role_constrained_order_mismatch_count'))}"
            )
            print(
                "real_live_target_me_shadow_profile_best_live_implementable_visible_with_target_me_seconds: "
                f"{safe_float(best_live_target_me_shadow_profile.get('live_missing_me_visible_with_target_me_candidate_seconds'))}"
            )
            print(
                "real_live_target_me_shadow_profile_best_live_implementable_visible_without_target_me_seconds: "
                f"{safe_float(best_live_target_me_shadow_profile.get('live_missing_me_visible_without_target_me_candidate_seconds'))}"
            )
            print(
                "real_live_target_me_shadow_profile_best_live_implementable_not_visible_with_target_me_seconds: "
                f"{safe_float(best_live_target_me_shadow_profile.get('live_missing_me_not_visible_with_target_me_candidate_seconds'))}"
            )
            print(
                "real_live_target_me_shadow_profile_best_live_implementable_not_visible_without_target_me_seconds: "
                f"{safe_float(best_live_target_me_shadow_profile.get('live_missing_me_not_visible_without_target_me_candidate_seconds'))}"
            )
    if candidate_target_me:
        print(f"capture_safe_candidate_target_me_status: {candidate_target_me.get('status')}")
    objective_audit = (
        report.get("objective_audit")
        if isinstance(report.get("objective_audit"), dict)
        else {}
    )
    if objective_audit:
        next_focus = objective_audit.get("next_focus") if isinstance(objective_audit.get("next_focus"), dict) else {}
        print(f"objective_audit: {objective_audit.get('overall_status')}")
        if next_focus:
            print(f"objective_next_focus: {next_focus.get('action_id')}")
        audit_blocking_dimensions = objective_audit.get("blocking_dimensions") or []
        print(
            "objective_blocking_dimensions: "
            f"{', '.join(audit_blocking_dimensions) if audit_blocking_dimensions else 'none'}"
        )
    print(f"gate_issues: {len(report.get('gate_issues') or [])}")
    triage_summary = (
        report.get("real_blocker_triage_summary")
        if isinstance(report.get("real_blocker_triage_summary"), dict)
        else {}
    )
    print(f"real_blocker_triage_items: {triage_summary.get('total_items', 0)}")
    print(f"real_blocker_triage_sessions: {triage_summary.get('session_count', 0)}")
    if report.get("recommended_next"):
        print(f"recommended_next: {report['recommended_next']}")
    print(f"report: {md_path}")
    for command in report.get("next_commands") or []:
        print(f"next: {command}")
    strict = report.get("strict_coverage") or {}
    strict_failed = strict.get("status") == "failed"
    risk_failed = bool(args.fail_on_risk and (
        summary.get("live_order_mismatch_count", 0) > 0
        or summary.get("live_missing_me_seconds", 0.0) > 0
        or summary.get("live_suspected_remote_leak_in_me_seconds", 0.0) > 0
        or summary.get("adjacent_duplicate_chunk_count", 0) > 0
        or summary.get("live_boundary_gate_issue_count", 0) > 0
    ))
    insufficient_coverage_failed = bool(
        args.fail_on_insufficient_coverage
        and (
            summary["live_sessions"] < args.min_live_sessions
            or summary["compared_sessions"] < args.min_compared_sessions
        )
    )
    if strict_failed or risk_failed or insufficient_coverage_failed:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
