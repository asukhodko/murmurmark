#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = "murmurmark.live_corpus_gates_report/v1"
SCRIPT_VERSION = "0.1.0"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aggregate near-realtime shadow parity gates over a local session corpus.")
    parser.add_argument("targets", nargs="*", help="all, latest or session paths. Default: all.")
    parser.add_argument("--sessions-root", type=Path, default=Path("sessions"))
    parser.add_argument("--out-dir", type=Path, default=Path("sessions/_reports/live-pipeline"))
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
    return {
        "session": rel(session, root),
        "live_present": live_present,
        "live_status": live_report.get("status") if isinstance(live_report, dict) else None,
        "comparison_status": comparison.get("status") if isinstance(comparison, dict) else None,
        "parity_status": parity.get("status") if isinstance(parity, dict) else None,
        "promotion_allowed": bool(comparison.get("promotion_allowed")) if isinstance(comparison, dict) else False,
        "promotion_blockers": comparison.get("promotion_blockers") if isinstance(comparison, dict) else blockers,
        "blockers": blockers + list(comparison.get("blockers") or []) if isinstance(comparison, dict) else blockers,
        "warnings": comparison.get("warnings") if isinstance(comparison, dict) else [],
        "metrics": {
            "live_chunks": metrics.get("live_chunks") if isinstance(metrics, dict) else None,
            "live_token_recall_in_batch": metrics.get("live_token_recall_in_batch") if isinstance(metrics, dict) else None,
            "adjacent_duplicate_chunk_count": metrics.get("adjacent_duplicate_chunk_count") if isinstance(metrics, dict) else None,
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


def build_report(sessions: list[Path], root: Path) -> dict[str, Any]:
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
    return {
        "schema": SCHEMA,
        "generator": {"name": "report-live-corpus-gates", "version": SCRIPT_VERSION},
        "created_at": datetime.now(timezone.utc).isoformat(),
        "sessions_root": str(root),
        "summary": {
            "sessions_total": len(rows),
            "live_sessions": len(live_rows),
            "compared_sessions": sum(1 for row in rows if row.get("comparison_status") == "shadow_compared"),
            "blocked_sessions": sum(1 for row in rows if row.get("comparison_status") == "blocked"),
            "promotion_allowed_sessions": len(promotable),
            "target_status": target_status,
            "promotion_decision": "shadow_only_do_not_promote",
            "speedup_supported_sessions": sum(
                1 for row in rows if (row.get("final_reconcile") or {}).get("speedup_status") == "live_asr_cache_reused"
            ),
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
        f"- blocked sessions: {summary['blocked_sessions']}",
        f"- promotion allowed sessions: {summary['promotion_allowed_sessions']}",
        f"- target status: `{summary['target_status']}`",
        f"- promotion decision: `{summary['promotion_decision']}`",
        "",
        "## Sessions",
        "",
    ]
    for row in report["sessions"]:
        if not row["live_present"]:
            continue
        final = row.get("final_reconcile") or {}
        lines.append(
            f"- `{row['session']}`: comparison `{row.get('comparison_status')}`, "
            f"parity `{row.get('parity_status')}`, final `{final.get('status') or 'missing'}`, "
            f"speedup `{final.get('speedup_status') or 'unknown'}`"
        )
    if report.get("blockers"):
        lines += ["", "## Blockers", ""]
        for key, count in sorted(report["blockers"].items()):
            lines.append(f"- `{key}`: {count}")
    lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    args = parse_args()
    root = args.sessions_root
    sessions = resolve_targets(args)
    report = build_report(sessions, root)
    json_path = args.out_dir / "live_corpus_gates_report.json"
    md_path = args.out_dir / "live_corpus_gates_report.md"
    write_json(json_path, report)
    write_markdown(md_path, report)
    summary = report["summary"]
    print(f"live_corpus_gates: {json_path}")
    print(f"status: {summary['target_status']}")
    print(f"live_sessions: {summary['live_sessions']}/{summary['sessions_total']}")
    print(f"promotion_decision: {summary['promotion_decision']}")
    print(f"report: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
