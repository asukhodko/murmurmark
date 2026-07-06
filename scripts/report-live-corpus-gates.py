#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = "murmurmark.live_corpus_gates_report/v1"
SCRIPT_VERSION = "0.6.0"
REAL_SESSION_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}$")
DEFAULT_TARGET_LIVE_SESSIONS = 3
DEFAULT_TARGET_MEANINGFUL_COMPARED_SESSIONS = 3
DEFAULT_TARGET_PASSING_COMPARED_SESSIONS = 3
LIVE_QUARANTINE_REASON = (
    "live pipeline is quarantined because the async live path has not yet passed capture-safety "
    "and parity gates; collect no new real live meetings until those gates pass"
)
PARITY_DIMENSIONS: dict[str, dict[str, Any]] = {
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
    "required_artifacts": {
        "title": "Required artifacts",
        "gates": ["required_artifacts", "live_token_recall", "raw_batch_authoritative"],
        "promotion_required": True,
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aggregate near-realtime shadow parity gates over a local session corpus.")
    parser.add_argument("targets", nargs="*", help="all, latest or session paths. Default: all.")
    parser.add_argument("--sessions-root", type=Path, default=Path("sessions"))
    parser.add_argument("--out-dir", type=Path, default=Path("sessions/_reports/live-pipeline"))
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


def sum_metric(rows: list[dict[str, Any]], metric: str) -> float:
    return round(sum(float(((row.get("metrics") or {}).get(metric) or 0.0)) for row in rows), 3)


def sum_int_metric(rows: list[dict[str, Any]], metric: str) -> int:
    return sum(int(((row.get("metrics") or {}).get(metric) or 0)) for row in rows)


def summarize_session(session: Path, root: Path) -> dict[str, Any]:
    live_report_path = session / "derived/live/live_pipeline_report.json"
    comparison_path = session / "derived/live/live_batch_comparison.json"
    session_report_path = session / "derived/live/live_parity_session_report.json"
    final_reconcile_path = session / "derived/live/final_reconcile_report.json"
    live_report = read_json(live_report_path)
    comparison = read_json(comparison_path)
    final_reconcile = read_json(final_reconcile_path)
    live_present = live_report is not None
    blockers: list[str] = []
    if not live_present:
        blockers.append("live_report_missing")
    if live_present and comparison is None:
        blockers.append("live_batch_comparison_missing")
    metrics = comparison.get("metrics") if isinstance(comparison, dict) else {}
    parity = comparison.get("parity_gates") if isinstance(comparison, dict) else {}
    comparison_status = comparison.get("status") if isinstance(comparison, dict) else None
    gates = gate_rows(comparison)
    return {
        "session": rel(session, root),
        "evidence_scope": evidence_scope(session, root),
        "live_present": live_present,
        "live_status": live_report.get("status") if isinstance(live_report, dict) else None,
        "comparison_status": comparison_status,
        "parity_status": parity.get("status") if isinstance(parity, dict) else None,
        "meaningful_compared": meaningful_comparison(metrics if isinstance(metrics, dict) else {}, comparison_status),
        "all_parity_gates_passed": all_gates_passed(comparison),
        "promotion_allowed": bool(comparison.get("promotion_allowed")) if isinstance(comparison, dict) else False,
        "promotion_blockers": comparison.get("promotion_blockers") if isinstance(comparison, dict) else blockers,
        "blockers": blockers + list(comparison.get("blockers") or []) if isinstance(comparison, dict) else blockers,
        "warnings": comparison.get("warnings") if isinstance(comparison, dict) else [],
        "metrics": {
            "live_chunks": metrics.get("live_chunks") if isinstance(metrics, dict) else None,
            "live_token_recall_in_batch": metrics.get("live_token_recall_in_batch") if isinstance(metrics, dict) else None,
            "adjacent_duplicate_chunk_count": metrics.get("adjacent_duplicate_chunk_count") if isinstance(metrics, dict) else None,
            "live_order_mismatch_count": metrics.get("live_order_mismatch_count") if isinstance(metrics, dict) else None,
            "live_missing_me_seconds": metrics.get("live_missing_me_seconds") if isinstance(metrics, dict) else None,
            "live_suspicious_batch_me_missing_seconds": (
                metrics.get("live_suspicious_batch_me_missing_seconds") if isinstance(metrics, dict) else None
            ),
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
        },
        "final_reconcile": {
            "status": final_reconcile.get("status") if isinstance(final_reconcile, dict) else None,
            "speedup_status": final_reconcile.get("speedup_status") if isinstance(final_reconcile, dict) else None,
            "live_cache_reuse": final_reconcile.get("live_cache_reuse") if isinstance(final_reconcile, dict) else None,
        },
        "inputs": {
            "live_report": rel(live_report_path, session) if live_report_path.exists() else None,
            "live_batch_comparison": rel(comparison_path, session) if comparison_path.exists() else None,
            "live_parity_session_report": rel(session_report_path, session) if session_report_path.exists() else None,
            "final_reconcile_report": rel(final_reconcile_path, session) if final_reconcile_path.exists() else None,
        },
        "gates": gates,
        "non_passing_gates": non_passing_gate_rows(gates),
        "parity_dimensions": dimension_statuses(gates),
    }


def build_report(sessions: list[Path], root: Path, args: argparse.Namespace) -> dict[str, Any]:
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
    promotable = [row for row in rows if row.get("promotion_allowed")]
    not_promotable = [row for row in live_rows if row.get("parity_status") != "passed_but_shadow_locked"]
    summary = {
        "sessions_total": len(rows),
        "live_sessions": len(live_rows),
        "real_live_sessions": len(real_live_rows),
        "diagnostic_live_sessions": len(diagnostic_live_rows),
        "compared_sessions": sum(1 for row in rows if row.get("comparison_status") == "shadow_compared"),
        "real_compared_sessions": sum(1 for row in real_live_rows if row.get("comparison_status") == "shadow_compared"),
        "meaningful_compared_sessions": sum(1 for row in rows if row.get("meaningful_compared")),
        "real_meaningful_compared_sessions": sum(1 for row in real_live_rows if row.get("meaningful_compared")),
        "passing_compared_sessions": sum(1 for row in rows if row.get("all_parity_gates_passed")),
        "real_passing_compared_sessions": sum(1 for row in real_live_rows if row.get("all_parity_gates_passed")),
        "blocked_sessions": sum(1 for row in rows if row.get("comparison_status") == "blocked"),
        "promotion_allowed_sessions": len(promotable),
        "promotion_decision": "shadow_only_do_not_promote",
        "speedup_supported_sessions": sum(
            1 for row in rows if (row.get("final_reconcile") or {}).get("speedup_status") == "live_asr_cache_reused"
        ),
        "live_order_mismatch_count": sum_int_metric(rows, "live_order_mismatch_count"),
        "real_live_order_mismatch_count": sum_int_metric(real_live_rows, "live_order_mismatch_count"),
        "live_missing_me_seconds": sum_metric(rows, "live_missing_me_seconds"),
        "real_live_missing_me_seconds": sum_metric(real_live_rows, "live_missing_me_seconds"),
        "live_suspicious_batch_me_missing_seconds": sum_metric(rows, "live_suspicious_batch_me_missing_seconds"),
        "real_live_suspicious_batch_me_missing_seconds": sum_metric(
            real_live_rows,
            "live_suspicious_batch_me_missing_seconds",
        ),
        "live_suspected_remote_leak_in_me_seconds": sum_metric(rows, "live_suspected_remote_leak_in_me_seconds"),
        "real_live_suspected_remote_leak_in_me_seconds": sum_metric(
            real_live_rows,
            "live_suspected_remote_leak_in_me_seconds",
        ),
        "adjacent_duplicate_chunk_count": sum_int_metric(rows, "adjacent_duplicate_chunk_count"),
        "real_adjacent_duplicate_chunk_count": sum_int_metric(real_live_rows, "adjacent_duplicate_chunk_count"),
    }
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
    promotion_blocking_dimensions = [
        key
        for key, counts in real_dimension_counts.items()
        if any(status != "passed" and count > 0 for status, count in counts.items())
    ]
    if live_rows and not real_live_rows:
        promotion_blocking_dimensions = list(PARITY_DIMENSIONS.keys())
    summary["promotion_blocking_dimensions"] = promotion_blocking_dimensions
    summary["promotion_blocking_dimension_count"] = len(promotion_blocking_dimensions)
    summary["live_quarantined"] = True
    summary["live_quarantine_reason"] = LIVE_QUARANTINE_REASON
    summary["live_evidence_mode"] = "historical_debug_only"
    summary["new_real_live_collection_allowed"] = False
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
    next_commands = recommended_next_commands(summary, real_gate_counts, gate_issues)
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
        "coverage_target": coverage_target,
        "parity_dimensions": {
            key: {
                "title": spec["title"],
                "promotion_required": bool(spec.get("promotion_required")),
                "counts": dict(dimension_counts.get(key, Counter())),
                "issue_sessions": dimension_issue_sessions.get(key, []),
            }
            for key, spec in PARITY_DIMENSIONS.items()
        },
        "real_parity_dimensions": {
            key: {
                "title": spec["title"],
                "promotion_required": bool(spec.get("promotion_required")),
                "counts": dict(real_dimension_counts.get(key, Counter())),
                "issue_sessions": real_dimension_issue_sessions.get(key, []),
            }
            for key, spec in PARITY_DIMENSIONS.items()
        },
        "promotion_policy": {
            "status": "blocked",
            "decision": summary["promotion_decision"],
            "batch_authoritative": True,
            "live_quarantined": True,
            "evidence_mode": summary["live_evidence_mode"],
            "evidence_scope": "real_meeting",
            "diagnostic_live_sessions": len(diagnostic_live_rows),
            "new_real_live_collection_allowed": False,
            "quarantine_reason": LIVE_QUARANTINE_REASON,
            "required_dimensions": list(PARITY_DIMENSIONS.keys()),
            "blocking_dimensions": promotion_blocking_dimensions,
            "promotion_allowed_sessions": summary["promotion_allowed_sessions"],
        },
        "blockers": dict(blockers),
        "warnings": dict(warnings),
        "gate_counts": {name: dict(counts) for name, counts in sorted(gate_counts.items())},
        "real_gate_counts": {name: dict(counts) for name, counts in sorted(real_gate_counts.items())},
        "gate_issues": gate_issues,
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


def recommended_next_commands(
    summary: dict[str, Any],
    gate_counts: dict[str, Counter[str]],
    gate_issues: list[dict[str, Any]],
) -> list[str]:
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
    live_quarantine_note = "murmurmark status latest  # live pipeline is quarantined; use normal record/process for real meetings"
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
        f"- compared sessions: {summary['compared_sessions']}",
        f"- real compared sessions: {summary.get('real_compared_sessions', 0)}",
        f"- meaningful compared sessions: {summary['meaningful_compared_sessions']}",
        f"- real meaningful compared sessions: {summary.get('real_meaningful_compared_sessions', 0)}",
        f"- passing compared sessions: {summary['passing_compared_sessions']}",
        f"- real passing compared sessions: {summary.get('real_passing_compared_sessions', 0)}",
        f"- blocked sessions: {summary['blocked_sessions']}",
        f"- promotion allowed sessions: {summary['promotion_allowed_sessions']}",
        f"- target status: `{summary['target_status']}`",
        f"- promotion decision: `{summary['promotion_decision']}`",
        f"- live order mismatches: {summary.get('live_order_mismatch_count', 0)}",
        f"- real live order mismatches: {summary.get('real_live_order_mismatch_count', 0)}",
        f"- live missing Me seconds: {summary.get('live_missing_me_seconds', 0.0)}",
        f"- real live missing Me seconds: {summary.get('real_live_missing_me_seconds', 0.0)}",
        f"- live suspicious batch-Me missing seconds: {summary.get('live_suspicious_batch_me_missing_seconds', 0.0)}",
        f"- real live suspicious batch-Me missing seconds: {summary.get('real_live_suspicious_batch_me_missing_seconds', 0.0)}",
        f"- live suspected remote-in-Me seconds: {summary.get('live_suspected_remote_leak_in_me_seconds', 0.0)}",
        f"- real live suspected remote-in-Me seconds: {summary.get('real_live_suspected_remote_leak_in_me_seconds', 0.0)}",
        f"- adjacent duplicate chunks: {summary.get('adjacent_duplicate_chunk_count', 0)}",
        f"- real adjacent duplicate chunks: {summary.get('real_adjacent_duplicate_chunk_count', 0)}",
        f"- strict coverage: `{summary.get('strict_coverage_status')}`",
        f"- coverage target: `{summary.get('coverage_target_status')}`",
        f"- coverage target live remaining: {summary.get('coverage_target_live_sessions_remaining', 0)}",
        f"- coverage target passing remaining: {summary.get('coverage_target_passing_sessions_remaining', 0)}",
        f"- live quarantined: `{summary.get('live_quarantined')}`",
        f"- live evidence mode: `{summary.get('live_evidence_mode')}`",
        f"- new real live collection allowed: `{summary.get('new_real_live_collection_allowed')}`",
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
            f"remote-in-Me sec `{metrics.get('live_suspected_remote_leak_in_me_seconds')}`"
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
    print(f"real_meaningful_compared_sessions: {summary.get('real_meaningful_compared_sessions', 0)}")
    print(f"real_passing_compared_sessions: {summary.get('real_passing_compared_sessions', 0)}")
    print(f"meaningful_compared_sessions: {summary['meaningful_compared_sessions']}")
    print(f"passing_compared_sessions: {summary['passing_compared_sessions']}")
    print(f"promotion_decision: {summary['promotion_decision']}")
    print(f"live_order_mismatch_count: {summary.get('live_order_mismatch_count', 0)}")
    print(f"real_live_order_mismatch_count: {summary.get('real_live_order_mismatch_count', 0)}")
    print(f"live_missing_me_seconds: {summary.get('live_missing_me_seconds', 0.0)}")
    print(f"real_live_missing_me_seconds: {summary.get('real_live_missing_me_seconds', 0.0)}")
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
    print(f"strict_coverage: {summary.get('strict_coverage_status')}")
    print(f"coverage_target: {summary.get('coverage_target_status')}")
    print(f"coverage_target_live_remaining: {summary.get('coverage_target_live_sessions_remaining', 0)}")
    print(f"coverage_target_passing_remaining: {summary.get('coverage_target_passing_sessions_remaining', 0)}")
    print(f"live_evidence_mode: {summary.get('live_evidence_mode')}")
    print(f"new_real_live_collection_allowed: {summary.get('new_real_live_collection_allowed')}")
    blocking_dimensions = summary.get("promotion_blocking_dimensions") or []
    print(f"promotion_blocking_dimensions: {', '.join(blocking_dimensions) if blocking_dimensions else 'none'}")
    print(f"gate_issues: {len(report.get('gate_issues') or [])}")
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
