#!/usr/bin/env python3
"""Run deterministic, fail-open, immutable, and promotion-decision gates."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


SCHEMA = "murmurmark.causal_recovery_generalization_acceptance/v1"
SCRIPT_VERSION = "1.0.0"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check causal recovery generalization v1.")
    parser.add_argument(
        "--corpus-dir",
        type=Path,
        default=Path("sessions/_reports/live-pipeline/causal-recovery-generalization-v1"),
    )
    parser.add_argument("--skip-full-input-verification", action="store_true")
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run(command: list[str], cwd: Path, timeout: float = 300.0) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        return {
            "status": "failed",
            "reason": "acceptance_command_timeout",
            "command": command,
            "returncode": None,
            "stdout_tail": (error.stdout or "")[-2000:] if isinstance(error.stdout, str) else "",
            "stderr_tail": (error.stderr or "")[-2000:] if isinstance(error.stderr, str) else "",
        }
    return {
        "status": "passed" if completed.returncode == 0 else "failed",
        "command": command,
        "returncode": completed.returncode,
        "stdout_tail": completed.stdout[-2000:],
        "stderr_tail": completed.stderr[-2000:],
    }


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    corpus_dir = args.corpus_dir.expanduser().resolve()
    python = sys.executable
    checks = {
        "double_talk_fail_open": run(
            [python, "scripts/check-live-causal-double-talk-me-recovery-v1.py"],
            repo_root,
        ),
        "runtime_backpressure_timeout": run(
            [python, "scripts/check-live-causal-me-recovery-runtime.py"],
            repo_root,
        ),
        "corrupt_incremental_cache": run(
            [python, "scripts/check-live-recovery-incremental-cache.py"],
            repo_root,
        ),
    }
    fail_open_checks = {
        "missing_model_fails_open": checks["double_talk_fail_open"]["status"] == "passed",
        "corrupt_whisper_cache_fails_open": checks["double_talk_fail_open"]["status"] == "passed",
        "stage_timeout_fails_open": checks["double_talk_fail_open"]["status"] == "passed",
        "bounded_queue_backpressure_fails_open": checks["runtime_backpressure_timeout"]["status"] == "passed",
        "runtime_timeout_fails_open": checks["runtime_backpressure_timeout"]["status"] == "passed",
        "corrupt_incremental_cache_invalidates_safely": checks["corrupt_incremental_cache"]["status"] == "passed",
    }
    fail_open_report = {
        "schema": "murmurmark.causal_recovery_fail_open_report/v1",
        "generator": {"name": "check-causal-recovery-generalization-v1", "version": SCRIPT_VERSION},
        "created_at": datetime.now(UTC).isoformat(),
        "status": "passed" if all(fail_open_checks.values()) else "failed",
        "checks": fail_open_checks,
        "commands": checks,
        "policy": {
            "base_draft_fallback": True,
            "batch_authoritative": True,
            "publication_on_error": False,
        },
    }
    write_json(corpus_dir / "fail_open_report_v1.json", fail_open_report)

    builder = run(
        [
            python,
            "scripts/build-causal-recovery-generalization-corpus.py",
            "--out-dir",
            str(corpus_dir),
            "--require-valid",
        ],
        repo_root,
        timeout=900.0,
    )
    reporter_command = [
        python,
        "scripts/report-causal-recovery-generalization-v1.py",
        "--corpus-dir",
        str(corpus_dir),
        "--require-decision",
    ]
    if args.skip_full_input_verification:
        reporter_command.append("--skip-input-verification")
    first = run(reporter_command, repo_root, timeout=1800.0)
    first_report = read_json(corpus_dir / "generalization_report_v1.json")
    first_fingerprint = str(first_report.get("outcome_fingerprint_sha256") or "")
    second_command = [
        python,
        "scripts/report-causal-recovery-generalization-v1.py",
        "--corpus-dir",
        str(corpus_dir),
        "--require-decision",
        "--skip-input-verification",
    ]
    second = run(second_command, repo_root, timeout=900.0)
    second_report = read_json(corpus_dir / "generalization_report_v1.json")
    second_fingerprint = str(second_report.get("outcome_fingerprint_sha256") or "")
    final = first
    if not args.skip_full_input_verification:
        final = run(reporter_command, repo_root, timeout=1800.0)
    decision = read_json(corpus_dir / "promotion_decision.json")
    report = read_json(corpus_dir / "generalization_report_v1.json")
    acceptance_checks = {
        "fail_open_passed": fail_open_report["status"] == "passed",
        "immutable_corpus_verified": builder["status"] == "passed",
        "first_report_completed": first["status"] == "passed",
        "second_report_completed": second["status"] == "passed",
        "final_verified_report_completed": final["status"] == "passed",
        "deterministic_outcome_fingerprint": bool(first_fingerprint)
        and first_fingerprint == second_fingerprint,
        "all_rows_have_outcomes": (
            (report.get("coverage") or {}).get("stable_machine_outcome_ratio") == 1.0
        ),
        "promotion_decision_passed": decision.get("status") == "passed",
        "promotion_decision_binary": decision.get("decision") in {"PROMOTE", "DO_NOT_PROMOTE"},
        "batch_authoritative": (decision.get("policy") or {}).get("batch_authoritative") is True,
        "remote_forbidden_guards_not_weakened": (
            (decision.get("policy") or {}).get("remote_forbidden_guards_weakened") is False
        ),
    }
    payload = {
        "schema": SCHEMA,
        "generator": {"name": "check-causal-recovery-generalization-v1", "version": SCRIPT_VERSION},
        "created_at": datetime.now(UTC).isoformat(),
        "status": "passed" if all(acceptance_checks.values()) else "failed",
        "checks": acceptance_checks,
        "outcome_fingerprint_first": first_fingerprint,
        "outcome_fingerprint_second": second_fingerprint,
        "decision": decision.get("decision"),
        "commands": {
            "fail_open": checks,
            "immutable_builder": builder,
            "report_first": first,
            "report_second": second,
            "report_final": final,
        },
    }
    write_json(corpus_dir / "acceptance_report_v1.json", payload)
    print(f"causal recovery generalization acceptance: {payload['status']}")
    print(f"decision: {payload['decision']}")
    print(f"outcome_fingerprint: {first_fingerprint}")
    return 0 if payload["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
