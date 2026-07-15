#!/usr/bin/env python3
"""Aggregate fresh recording-time causal-recovery evidence without requiring live promotion."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


SCHEMA = "murmurmark.live_recovery_real_evidence_report/v1"
SCRIPT_VERSION = "1.0.0"
MIN_MANAGER_VERSION = (1, 1, 0)
DATE_SESSION_RE = re.compile(r"^20\d{2}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}(?:-.+)?$")
REQUIRED_RECOVERY_CHECKS = {
    "experiment_no_backpressure",
    "causal_recovery_healthy",
    "causal_recovery_incremental_runtime",
    "causal_recovery_recording_time_run",
    "causal_recovery_pre_stop_candidates",
    "causal_recovery_zero_final_lag",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aggregate fresh real-session recovery proofs.")
    parser.add_argument("targets", nargs="*", default=["all"])
    parser.add_argument("--sessions-root", type=Path, default=Path("sessions"))
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--min-sessions", type=int, default=3)
    parser.add_argument("--max-recovery-final-lag-sec", type=float, default=0.0)
    return parser.parse_args()


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def version_tuple(value: Any) -> tuple[int, int, int]:
    parts = str(value or "").split(".")
    parsed: list[int] = []
    for part in parts[:3]:
        match = re.match(r"^(\d+)", part)
        parsed.append(int(match.group(1)) if match else 0)
    return tuple((parsed + [0, 0, 0])[:3])  # type: ignore[return-value]


def session_candidates(root: Path, targets: list[str]) -> list[Path]:
    if not targets or targets == ["all"]:
        return sorted(
            path.resolve()
            for path in root.iterdir()
            if path.is_dir() and DATE_SESSION_RE.match(path.name)
        ) if root.exists() else []
    resolved: list[Path] = []
    for target in targets:
        if target == "all":
            resolved.extend(session_candidates(root, ["all"]))
            continue
        if target == "latest":
            candidates = session_candidates(root, ["all"])
            if candidates:
                resolved.append(candidates[-1])
            continue
        path = Path(target).expanduser()
        if not path.is_absolute() and not path.exists():
            path = root / path
        resolved.append(path.resolve())
    unique: dict[str, Path] = {str(path): path for path in resolved}
    return list(unique.values())


def refresh_session(session: Path, max_final_lag: float) -> dict[str, Any]:
    script = Path(__file__).with_name("report-live-session-evidence.py")
    command = [
        sys.executable,
        str(script),
        str(session),
        "--refresh",
        "--require-causal-recovery",
        "--max-recovery-final-lag-sec",
        str(max(0.0, max_final_lag)),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    return {
        "returncode": result.returncode,
        "stdout_tail": result.stdout[-1000:],
        "stderr_tail": result.stderr[-1000:],
    }


def session_row(session: Path, *, refresh: bool, max_final_lag: float) -> dict[str, Any]:
    recovery_dir = session / "derived/live/causal-me-recovery-runtime-v1"
    worker_path = recovery_dir / "worker_state.json"
    worker = read_json(worker_path)
    generator = worker.get("generator") if isinstance(worker.get("generator"), dict) else {}
    manager_version = str(generator.get("version") or "")
    implementation_current = version_tuple(manager_version) >= MIN_MANAGER_VERSION
    refresh_result: dict[str, Any] | None = None
    if refresh and implementation_current:
        refresh_result = refresh_session(session, max_final_lag)

    evidence_path = session / "derived/live/live_session_evidence.json"
    evidence = read_json(evidence_path)
    check_rows = evidence.get("checks") if isinstance(evidence.get("checks"), list) else []
    checks = {
        str(row.get("id")): row
        for row in check_rows
        if isinstance(row, dict) and row.get("id")
    }
    missing_checks = sorted(REQUIRED_RECOVERY_CHECKS - set(checks))
    failed_checks = sorted(
        check_id
        for check_id in REQUIRED_RECOVERY_CHECKS
        if checks.get(check_id, {}).get("status") != "passed"
    )
    meaningful = bool(evidence.get("meaningful_comparison"))
    transport = bool(evidence.get("transport_evidence_passed"))
    batch_authoritative = bool(evidence.get("batch_authoritative"))
    session_manifest = read_json(session / "session.json")
    completed_session = session_manifest.get("status") == "completed"
    eligible = bool(
        DATE_SESSION_RE.match(session.name)
        and implementation_current
        and worker
    )
    passed = bool(
        eligible
        and completed_session
        and meaningful
        and transport
        and batch_authoritative
        and not missing_checks
        and not failed_checks
        and (refresh_result is None or refresh_result["returncode"] == 0)
    )
    reasons: list[str] = []
    if not DATE_SESSION_RE.match(session.name):
        reasons.append("not_date_named_real_session")
    if not worker:
        reasons.append("recovery_worker_state_missing")
    elif not implementation_current:
        reasons.append("manager_version_before_1.1.0")
    if not completed_session:
        reasons.append("session_not_completed")
    if not evidence:
        reasons.append("session_evidence_missing")
    if not meaningful:
        reasons.append("comparison_not_meaningful")
    if not transport:
        reasons.append("transport_or_recovery_checks_failed")
    if not batch_authoritative:
        reasons.append("batch_not_authoritative")
    if missing_checks:
        reasons.append("required_recovery_checks_missing")
    if failed_checks:
        reasons.append("required_recovery_checks_failed")
    if refresh_result is not None and refresh_result["returncode"] != 0:
        reasons.append("evidence_refresh_failed")
    recovery_metrics = ((evidence.get("metrics") or {}).get("causal_recovery") or {})
    return {
        "schema": "murmurmark.live_recovery_real_session_evidence/v1",
        "session": session.name,
        "path": str(session),
        "status": "passed" if passed else "failed" if eligible else "ineligible",
        "eligible": eligible,
        "passed": passed,
        "manager_version": manager_version or None,
        "meaningful_comparison": meaningful,
        "transport_evidence_passed": transport,
        "batch_authoritative": batch_authoritative,
        "recovery": recovery_metrics,
        "missing_required_checks": missing_checks,
        "failed_required_checks": failed_checks,
        "reasons": reasons,
        "refresh": refresh_result,
        "inputs": {
            "worker_state": str(worker_path) if worker else None,
            "session_evidence": str(evidence_path) if evidence else None,
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Live Recovery Real Evidence v1",
        "",
        f"Status: `{report['status']}`",
        f"Passing fresh sessions: `{summary['passing_session_count']}/{summary['required_session_count']}`",
        f"Eligible sessions: `{summary['eligible_session_count']}`",
        "Batch authoritative: `true`",
        "Promotion allowed: `false`",
        "",
        "## Sessions",
        "",
    ]
    if report["sessions"]:
        for row in report["sessions"]:
            suffix = ", ".join(row["reasons"]) or "all required evidence passed"
            lines.append(
                f"- `{row['status']}` `{row['session']}`: manager `{row['manager_version']}`; {suffix}."
            )
    else:
        lines.append("- No sessions on the required recording-time implementation yet.")
    lines.extend(["", f"Next: `{report['recommended_next']}`", ""])
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    root = args.sessions_root.expanduser().resolve()
    sessions = session_candidates(root, args.targets)
    rows = [
        session_row(
            session,
            refresh=args.refresh,
            max_final_lag=args.max_recovery_final_lag_sec,
        )
        for session in sessions
    ]
    eligible = [row for row in rows if row["eligible"]]
    passing = [row for row in eligible if row["passed"]]
    required = max(1, args.min_sessions)
    status = "passed" if len(passing) >= required else "collecting_real_evidence"
    remaining = max(0, required - len(passing))
    recommended_next = (
        "complete Live Recovery Runtime Efficiency and Real Evidence v1"
        if remaining == 0
        else f"record {remaining} fresh meaningful Live Evidence session(s)"
    )
    report = {
        "schema": SCHEMA,
        "generator": {"name": "report-live-recovery-real-evidence", "version": SCRIPT_VERSION},
        "generated_at": now_iso(),
        "status": status,
        "batch_authoritative": True,
        "promotion_allowed": False,
        "policy": {
            "minimum_manager_version": ".".join(str(value) for value in MIN_MANAGER_VERSION),
            "required_session_count": required,
            "max_recovery_final_lag_sec": max(0.0, args.max_recovery_final_lag_sec),
            "required_recovery_checks": sorted(REQUIRED_RECOVERY_CHECKS),
        },
        "summary": {
            "scanned_session_count": len(rows),
            "eligible_session_count": len(eligible),
            "passing_session_count": len(passing),
            "required_session_count": required,
            "remaining_session_count": remaining,
        },
        "sessions": rows,
        "recommended_next": recommended_next,
    }
    out_dir = (args.out_dir or (root / "_reports/live-pipeline")).expanduser().resolve()
    json_path = out_dir / "live_recovery_real_evidence_v1.json"
    markdown_path = out_dir / "live_recovery_real_evidence_v1.md"
    write_json(json_path, report)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    print(f"live_recovery_real_evidence: {json_path}")
    print(f"status: {status}")
    print(f"passing_sessions: {len(passing)}/{required}")
    print(f"remaining_sessions: {remaining}")
    print(f"next: {recommended_next}")
    if args.strict and status != "passed":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
