#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = "murmurmark.live_corpus_gates_report/v1"
SCRIPT_VERSION = "0.4.0"


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


def summarize_session(session: Path, root: Path) -> dict[str, Any]:
    live_report_path = session / "derived/live/live_pipeline_report.json"
    comparison_path = session / "derived/live/live_batch_comparison.json"
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
    return {
        "session": rel(session, root),
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
            "final_reconcile_report": rel(final_reconcile_path, session) if final_reconcile_path.exists() else None,
        },
        "gates": gate_rows(comparison),
    }


def build_report(sessions: list[Path], root: Path, args: argparse.Namespace) -> dict[str, Any]:
    rows = [summarize_session(session, root) for session in sessions]
    live_rows = [row for row in rows if row["live_present"]]
    gate_counts: dict[str, Counter[str]] = defaultdict(Counter)
    blockers = Counter()
    warnings = Counter()
    for row in rows:
        for blocker in row.get("blockers") or []:
            blockers[str(blocker)] += 1
        for warning in row.get("warnings") or []:
            warnings[str(warning)] += 1
        for gate in row.get("gates") or []:
            gate_counts[str(gate.get("name") or "unknown")][str(gate.get("status") or "unknown")] += 1
    promotable = [row for row in rows if row.get("promotion_allowed")]
    not_promotable = [row for row in live_rows if row.get("parity_status") != "passed_but_shadow_locked"]
    if not live_rows:
        target_status = "no_live_sessions"
    elif promotable:
        target_status = "unexpected_promotable_sessions"
    elif not_promotable:
        target_status = "shadow_only_not_promotable"
    else:
        target_status = "shadow_locked_after_basic_gates"
    summary = {
        "sessions_total": len(rows),
        "live_sessions": len(live_rows),
        "compared_sessions": sum(1 for row in rows if row.get("comparison_status") == "shadow_compared"),
        "meaningful_compared_sessions": sum(1 for row in rows if row.get("meaningful_compared")),
        "passing_compared_sessions": sum(1 for row in rows if row.get("all_parity_gates_passed")),
        "blocked_sessions": sum(1 for row in rows if row.get("comparison_status") == "blocked"),
        "promotion_allowed_sessions": len(promotable),
        "target_status": target_status,
        "promotion_decision": "shadow_only_do_not_promote",
        "speedup_supported_sessions": sum(
            1 for row in rows if (row.get("final_reconcile") or {}).get("speedup_status") == "live_asr_cache_reused"
        ),
        "live_order_mismatch_count": sum(int(((row.get("metrics") or {}).get("live_order_mismatch_count") or 0)) for row in rows),
        "live_missing_me_seconds": round(
            sum(float(((row.get("metrics") or {}).get("live_missing_me_seconds") or 0.0)) for row in rows),
            3,
        ),
        "live_suspicious_batch_me_missing_seconds": round(
            sum(float(((row.get("metrics") or {}).get("live_suspicious_batch_me_missing_seconds") or 0.0)) for row in rows),
            3,
        ),
        "live_suspected_remote_leak_in_me_seconds": round(
            sum(float(((row.get("metrics") or {}).get("live_suspected_remote_leak_in_me_seconds") or 0.0)) for row in rows),
            3,
        ),
        "adjacent_duplicate_chunk_count": sum(
            int(((row.get("metrics") or {}).get("adjacent_duplicate_chunk_count") or 0)) for row in rows
        ),
    }
    strict_failures: list[dict[str, Any]] = []
    def add_failure(gate_id: str, message: str, value: Any, limit: Any) -> None:
        strict_failures.append({"id": gate_id, "message": message, "value": value, "limit": limit})

    if args.min_live_sessions and summary["live_sessions"] < args.min_live_sessions:
        add_failure("min_live_sessions", "not enough real live sessions", summary["live_sessions"], args.min_live_sessions)
    if args.min_compared_sessions and summary["compared_sessions"] < args.min_compared_sessions:
        add_failure(
            "min_compared_sessions",
            "not enough live-vs-batch compared sessions",
            summary["compared_sessions"],
            args.min_compared_sessions,
        )
    if (
        args.min_meaningful_compared_sessions
        and summary["meaningful_compared_sessions"] < args.min_meaningful_compared_sessions
    ):
        add_failure(
            "min_meaningful_compared_sessions",
            "not enough compared sessions with both Me and remote evidence",
            summary["meaningful_compared_sessions"],
            args.min_meaningful_compared_sessions,
        )
    if args.min_passing_compared_sessions and summary["passing_compared_sessions"] < args.min_passing_compared_sessions:
        add_failure(
            "min_passing_compared_sessions",
            "not enough compared sessions where every live parity gate passed",
            summary["passing_compared_sessions"],
            args.min_passing_compared_sessions,
        )
    if args.max_order_mismatches is not None and summary["live_order_mismatch_count"] > args.max_order_mismatches:
        add_failure("max_order_mismatches", "live order mismatches exceed limit", summary["live_order_mismatch_count"], args.max_order_mismatches)
    if args.max_missing_me_sec is not None and summary["live_missing_me_seconds"] > args.max_missing_me_sec:
        add_failure("max_missing_me_sec", "live missing Me seconds exceed limit", summary["live_missing_me_seconds"], args.max_missing_me_sec)
    if args.max_remote_in_me_sec is not None and summary["live_suspected_remote_leak_in_me_seconds"] > args.max_remote_in_me_sec:
        add_failure(
            "max_remote_in_me_sec",
            "live suspected remote-in-Me seconds exceed limit",
            summary["live_suspected_remote_leak_in_me_seconds"],
            args.max_remote_in_me_sec,
        )
    if args.max_boundary_duplicates is not None and summary["adjacent_duplicate_chunk_count"] > args.max_boundary_duplicates:
        add_failure(
            "max_boundary_duplicates",
            "adjacent live chunk duplicates exceed limit",
            summary["adjacent_duplicate_chunk_count"],
            args.max_boundary_duplicates,
        )
    if args.fail_on_promotion and summary["promotion_allowed_sessions"] > 0:
        add_failure("no_promotion", "live promotion must remain blocked in v1", summary["promotion_allowed_sessions"], 0)
    if args.require_passing_gates:
        non_passing: dict[str, dict[str, int]] = {}
        for name, counts in gate_counts.items():
            bad = {status: count for status, count in counts.items() if status != "passed" and count > 0}
            if bad:
                non_passing[name] = bad
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
        "blockers": dict(blockers),
        "warnings": dict(warnings),
        "gate_counts": {name: dict(counts) for name, counts in sorted(gate_counts.items())},
        "sessions": rows,
    }


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    summary = report["summary"]
    lines = [
        "# Live Pipeline Corpus Gates",
        "",
        f"- sessions: {summary['sessions_total']}",
        f"- live sessions: {summary['live_sessions']}",
        f"- compared sessions: {summary['compared_sessions']}",
        f"- meaningful compared sessions: {summary['meaningful_compared_sessions']}",
        f"- passing compared sessions: {summary['passing_compared_sessions']}",
        f"- blocked sessions: {summary['blocked_sessions']}",
        f"- promotion allowed sessions: {summary['promotion_allowed_sessions']}",
        f"- target status: `{summary['target_status']}`",
        f"- promotion decision: `{summary['promotion_decision']}`",
        f"- live order mismatches: {summary.get('live_order_mismatch_count', 0)}",
        f"- live missing Me seconds: {summary.get('live_missing_me_seconds', 0.0)}",
        f"- live suspicious batch-Me missing seconds: {summary.get('live_suspicious_batch_me_missing_seconds', 0.0)}",
        f"- live suspected remote-in-Me seconds: {summary.get('live_suspected_remote_leak_in_me_seconds', 0.0)}",
        f"- adjacent duplicate chunks: {summary.get('adjacent_duplicate_chunk_count', 0)}",
        f"- strict coverage: `{summary.get('strict_coverage_status')}`",
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
    print(f"meaningful_compared_sessions: {summary['meaningful_compared_sessions']}")
    print(f"passing_compared_sessions: {summary['passing_compared_sessions']}")
    print(f"promotion_decision: {summary['promotion_decision']}")
    print(f"live_order_mismatch_count: {summary.get('live_order_mismatch_count', 0)}")
    print(f"live_missing_me_seconds: {summary.get('live_missing_me_seconds', 0.0)}")
    print(f"live_suspicious_batch_me_missing_seconds: {summary.get('live_suspicious_batch_me_missing_seconds', 0.0)}")
    print(f"live_suspected_remote_leak_in_me_seconds: {summary.get('live_suspected_remote_leak_in_me_seconds', 0.0)}")
    print(f"adjacent_duplicate_chunk_count: {summary.get('adjacent_duplicate_chunk_count', 0)}")
    print(f"strict_coverage: {summary.get('strict_coverage_status')}")
    print(f"report: {md_path}")
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
