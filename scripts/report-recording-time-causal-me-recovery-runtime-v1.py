#!/usr/bin/env python3
"""Verify replay/runtime agreement and inherit the fixed-corpus no-regression gates."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


SCHEMA = "murmurmark.recording_time_causal_me_recovery_runtime_report/v1"
SCRIPT_VERSION = "1.0.0"
REPLAY_PROFILE = (
    "online_live_me_remote_overlap_filter_live_boundary_split_retime_causal_remote_energy_"
    "local_island_micro_asr_v2_causal_remote_active_me_separation_v1"
)
RUNTIME_PROFILE = REPLAY_PROFILE + "_runtime_v1"
EXPECTED_MISSING_ME_SECONDS = 1657.89
EXPECTED_REMOTE_LIKE_ME_SECONDS = 108.42
EXPECTED_ORDER_BLOCKERS = 0
EXPECTED_REVIEW_BURDEN_SECONDS = 490.38


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
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def same_number(left: Any, right: Any, tolerance: float = 0.001) -> bool:
    try:
        return abs(float(left) - float(right)) <= tolerance
    except (TypeError, ValueError):
        return left == right


def profile_metrics(comparison: dict[str, Any], profile: str) -> dict[str, Any]:
    row = (((comparison.get("shadow_profiles") or {}).get("target_me") or {}).get(profile) or {})
    metrics = row.get("metrics")
    return metrics if isinstance(metrics, dict) else {}


def authoritative_inputs_unchanged(session: Path, focused_row: dict[str, Any]) -> bool:
    expected = ((focused_row.get("sha256_manifests") or {}).get("authoritative_inputs") or [])
    if not expected:
        return False
    for row in expected:
        path = session / str(row.get("path") or "")
        if not path.exists() or path.stat().st_size != int(row.get("bytes") or -1):
            return False
        if sha256(path) != row.get("sha256"):
            return False
    return True


def run(command: list[str]) -> None:
    result = subprocess.run(command, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(command)}")


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Recording-Time Causal Me Recovery Integration v1",
        "",
        f"Status: `{report['status']}`",
        f"Sessions: `{summary['session_count']}` / passing `{summary['passing_session_count']}`",
        f"Replay/runtime agreement: `{summary['agreement_session_count']}/{summary['session_count']}`",
        f"Missing Me: `{summary['missing_me_seconds']}s`",
        f"Remote-like Me: `{summary['remote_like_me_seconds']}s`",
        f"Effective order blockers: `{summary['effective_order_blocker_count']}`",
        f"Review burden: `{summary['review_burden_seconds']}s`",
        f"Runtime invocations: `{summary['runtime_invocation_count']}`; max elapsed: `{summary['max_runtime_invocation_sec']}s`",
        f"Sessions with pre-stop candidates: `{summary['pre_stop_candidate_session_count']}`",
        "Batch authoritative: `true`",
        "Promotion allowed: `false`",
        "",
        "## Sessions",
        "",
    ]
    for row in report["sessions"]:
        lines.append(
            f"- `{row['status']}` `{row['session']}`: candidate agreement "
            f"`{row['checks']['candidate_agreement']}`, metrics `{"passed" if row['checks']['profile_metrics_agree'] else "failed"}`"
        )
    lines.extend(
        [
            "",
            "Runtime candidates are evaluated in an explicit-only profile. Normal preview, batch transcript, notes and export are unchanged.",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Report fixed-corpus recording-time recovery agreement.")
    parser.add_argument("--sessions-root", type=Path, default=Path("sessions"))
    parser.add_argument("--run-paced-replay", action="store_true")
    parser.add_argument("--run-compare", action="store_true")
    parser.add_argument("--refresh-runtime", action="store_true")
    parser.add_argument("--stride-chunks", type=int, default=12)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.sessions_root.expanduser().resolve()
    repo = Path(__file__).resolve().parent.parent
    focused_path = root / "_reports/live-pipeline/causal_remote_active_me_separation_v1.json"
    focused = read_json(focused_path)
    focused_rows = [row for row in focused.get("sessions") or [] if isinstance(row, dict)]
    rows: list[dict[str, Any]] = []
    for focused_row in focused_rows:
        session_name = str(focused_row.get("session") or "")
        session = root / session_name
        if args.run_paced_replay:
            command = [
                sys.executable,
                str(repo / "scripts/replay-live-causal-me-recovery-runtime.py"),
                str(session),
                "--stride-chunks",
                str(max(1, args.stride_chunks)),
            ]
            if args.refresh_runtime:
                command.append("--refresh")
            run(command)
        if args.run_compare:
            run(
                [
                    sys.executable,
                    str(repo / "scripts/compare-live-batch.py"),
                    str(session),
                    "--only-lab-policy",
                    REPLAY_PROFILE,
                    "--only-lab-policy",
                    RUNTIME_PROFILE,
                ]
            )
        paced = read_json(
            session / "derived/live/causal-me-recovery-runtime-v1/paced_replay.json"
        )
        paced_invocations = [
            row for row in paced.get("invocations") or [] if isinstance(row, dict)
        ]
        invocation_latencies = [
            safe_number(row.get("elapsed_sec"), 0.0) for row in paced_invocations
        ]
        runtime_state = paced.get("runtime_state") if isinstance(paced.get("runtime_state"), dict) else {}
        comparison = read_json(session / "derived/live/live_batch_comparison.json")
        replay_metrics = profile_metrics(comparison, REPLAY_PROFILE)
        runtime_metrics = profile_metrics(comparison, RUNTIME_PROFILE)
        metric_names = (
            "live_missing_me_seconds",
            "live_suspected_remote_leak_in_me_seconds",
            "live_effective_blocking_contentful_role_constrained_order_mismatch_count",
            "live_batch_token_f1",
        )
        metrics_agree = bool(replay_metrics and runtime_metrics) and all(
            same_number(replay_metrics.get(name), runtime_metrics.get(name))
            for name in metric_names
        )
        focused_checks = focused_row.get("checks") if isinstance(focused_row.get("checks"), dict) else {}
        checks = {
            "paced_replay_passed": paced.get("status") == "passed",
            "candidate_agreement": all(
                row.get("passed") is True
                for row in (paced.get("candidate_agreement") or {}).values()
            ),
            "runtime_pre_stop_provenance": runtime_state.get("completed_before_stop") is True,
            "runtime_invocations_within_timeout": bool(paced_invocations)
            and max(invocation_latencies, default=0.0) <= 120.0,
            "runtime_selection_batch_free": paced.get("used_batch_fields_for_selection") is False,
            "profile_metrics_agree": metrics_agree,
            "focused_no_regression_gates_pass": all(
                focused_checks.get(name) is True
                for name in (
                    "missing_me_not_worse",
                    "remote_like_me_not_worse",
                    "effective_order_blockers_zero",
                    "token_f1_not_worse",
                    "review_burden_not_worse",
                )
            ),
            "authoritative_inputs_unchanged": authoritative_inputs_unchanged(session, focused_row),
            "batch_authoritative": True,
            "promotion_blocked": True,
        }
        rows.append(
            {
                "session": session_name,
                "status": "passed" if all(checks.values()) else "failed",
                "checks": checks,
                "paced_replay": str(
                    Path("derived/live/causal-me-recovery-runtime-v1/paced_replay.json")
                ),
                "runtime_profile": RUNTIME_PROFILE,
                "replay_profile": REPLAY_PROFILE,
                "runtime_metrics": {name: runtime_metrics.get(name) for name in metric_names},
                "replay_metrics": {name: replay_metrics.get(name) for name in metric_names},
                "runtime_evidence": {
                    "invocation_count": len(paced_invocations),
                    "max_invocation_elapsed_sec": round(max(invocation_latencies, default=0.0), 3),
                    "accepted_candidate_count": runtime_state.get("accepted_candidate_count"),
                    "accepted_candidate_seconds": runtime_state.get("accepted_candidate_seconds"),
                    "completed_before_stop": runtime_state.get("completed_before_stop"),
                },
                "focused_metrics": focused_row.get("metrics") or {},
            }
        )

    focused_summary = focused.get("summary") if isinstance(focused.get("summary"), dict) else {}
    all_invocation_latencies = [
        safe_number(value, 0.0)
        for row in rows
        for value in [((row.get("runtime_evidence") or {}).get("max_invocation_elapsed_sec"))]
    ]
    summary = {
        "session_count": len(rows),
        "passing_session_count": sum(row["status"] == "passed" for row in rows),
        "agreement_session_count": sum(row["checks"]["candidate_agreement"] for row in rows),
        "missing_me_seconds": focused_summary.get("candidate_missing_me_seconds"),
        "remote_like_me_seconds": focused_summary.get("candidate_remote_like_me_seconds"),
        "effective_order_blocker_count": focused_summary.get("effective_order_blocker_count"),
        "review_burden_seconds": focused_summary.get("review_burden_seconds"),
        "runtime_invocation_count": sum(
            safe_int((row.get("runtime_evidence") or {}).get("invocation_count"), 0)
            for row in rows
        ),
        "max_runtime_invocation_sec": round(max(all_invocation_latencies, default=0.0), 3),
        "pre_stop_candidate_session_count": sum(
            safe_int((row.get("runtime_evidence") or {}).get("accepted_candidate_count"), 0) > 0
            for row in rows
        ),
    }
    aggregate_checks = {
        "all_sessions_pass": len(rows) == 7 and all(row["status"] == "passed" for row in rows),
        "missing_me_within_baseline": safe_number(summary["missing_me_seconds"]) <= EXPECTED_MISSING_ME_SECONDS + 0.001,
        "remote_like_me_within_baseline": safe_number(summary["remote_like_me_seconds"]) <= EXPECTED_REMOTE_LIKE_ME_SECONDS + 0.001,
        "effective_order_blockers_zero": safe_int(summary["effective_order_blocker_count"]) == EXPECTED_ORDER_BLOCKERS,
        "review_burden_not_worse": safe_number(summary["review_burden_seconds"]) <= EXPECTED_REVIEW_BURDEN_SECONDS + 0.001,
        "batch_authoritative": True,
        "promotion_blocked": True,
    }
    report = {
        "schema": SCHEMA,
        "generator": {"name": "report-recording-time-causal-me-recovery-runtime-v1", "version": SCRIPT_VERSION},
        "created_at": now_iso(),
        "status": "passed" if all(aggregate_checks.values()) else "failed",
        "replay_profile": REPLAY_PROFILE,
        "runtime_profile": RUNTIME_PROFILE,
        "summary": summary,
        "checks": aggregate_checks,
        "sessions": rows,
        "batch_authoritative": True,
        "promotion_allowed": False,
    }
    output = root / "_reports/live-pipeline/recording_time_causal_me_recovery_runtime_v1"
    write_json(output.with_suffix(".json"), report)
    write_jsonl(output.with_suffix(".jsonl"), rows)
    output.with_suffix(".md").write_text(render_markdown(report), encoding="utf-8")
    print(f"status: {report['status']}")
    print(f"passing_sessions: {summary['passing_session_count']}/{summary['session_count']}")
    print(f"agreement_sessions: {summary['agreement_session_count']}/{summary['session_count']}")
    print(f"report: {output.with_suffix('.json')}")
    return 0 if report["status"] == "passed" else 1


def safe_number(value: Any, default: float = 1.0e12) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = -1) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


if __name__ == "__main__":
    raise SystemExit(main())
