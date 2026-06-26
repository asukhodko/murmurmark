#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


SCRIPT_VERSION = "0.1.0"
SCHEMA = "murmurmark.review_decisions_batch_report/v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply review decisions to every session mentioned in a decisions JSONL.")
    parser.add_argument(
        "--decisions",
        type=Path,
        default=Path("sessions/_reports/review-plan/review_decisions.jsonl"),
        help="Edited review decisions JSONL.",
    )
    parser.add_argument(
        "--review-template",
        type=Path,
        default=Path("sessions/_reports/review-plan/review_decisions.template.jsonl"),
        help="Template JSONL that defines the required review scope.",
    )
    parser.add_argument("--input-profile", default="auto")
    parser.add_argument("--output-profile", default="reviewed_v1")
    parser.add_argument(
        "--session",
        action="append",
        default=[],
        help="Optional session path or session id filter. Can be repeated.",
    )
    parser.add_argument(
        "--synthesize",
        action="store_true",
        help="Run synthesize-simple-extractive.py for every successfully reviewed session.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("sessions/_reports/review-plan/review_decisions_apply_report.json"),
        help="Batch report path.",
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


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(value)
    return rows


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def session_key(row: dict[str, Any]) -> str:
    session = str(row.get("session") or "").strip()
    if session:
        return session
    session_id = str(row.get("session_id") or "").strip()
    if session_id:
        return f"sessions/{session_id}"
    return ""


def session_matches(session: Path, filters: set[str]) -> bool:
    if not filters:
        return True
    candidates = {str(session), session.as_posix(), session.name, f"./{session.as_posix()}"}
    try:
        candidates.add(str(session.resolve()))
    except OSError:
        pass
    return bool(candidates & filters)


def collect_sessions(decisions_path: Path, template_path: Path, filters: set[str]) -> list[Path]:
    rows: list[dict[str, Any]] = []
    if template_path.exists():
        rows.extend(read_jsonl(template_path))
    if decisions_path.exists():
        rows.extend(read_jsonl(decisions_path))
    by_key: dict[str, Path] = {}
    for row in rows:
        key = session_key(row)
        if not key:
            continue
        path = Path(key)
        if session_matches(path, filters):
            by_key[path.as_posix()] = path
    return [by_key[key] for key in sorted(by_key)]


def run_command(command: list[str]) -> dict[str, Any]:
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    return {
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    decisions = args.decisions.expanduser()
    template = args.review_template.expanduser()
    if not decisions.exists():
        raise SystemExit(f"missing decisions file: {decisions}")
    if not template.exists():
        raise SystemExit(f"missing review template: {template}")

    filters = {item.strip() for item in args.session if item.strip()}
    sessions = collect_sessions(decisions, template, filters)
    results: list[dict[str, Any]] = []
    for session in sessions:
        apply_command = [
            sys.executable,
            str(repo_root / "scripts/apply-review-decisions.py"),
            str(session),
            "--decisions",
            str(decisions),
            "--review-template",
            str(template),
            "--input-profile",
            args.input_profile,
            "--output-profile",
            args.output_profile,
        ]
        apply_result = run_command(apply_command)
        review_report_path = (
            session
            / "derived/transcript-simple/whisper-cpp/review-decisions"
            / f"review_decisions_report.{args.output_profile}.json"
        )
        review_report = read_json(review_report_path)
        gates = review_report.get("gates") if isinstance(review_report, dict) else {}
        coverage = review_report.get("coverage") if isinstance(review_report, dict) else {}

        synthesize_result: dict[str, Any] | None = None
        if args.synthesize and apply_result["returncode"] == 0:
            synthesize_result = run_command(
                [
                    sys.executable,
                    str(repo_root / "scripts/synthesize-simple-extractive.py"),
                    str(session),
                    "--transcript-profile",
                    args.output_profile,
                ]
            )

        results.append(
            {
                "session": session.as_posix(),
                "session_id": session.name,
                "apply": {
                    "returncode": apply_result["returncode"],
                    "gates_passed": gates.get("passed") if isinstance(gates, dict) else None,
                    "coverage_complete": coverage.get("complete") if isinstance(coverage, dict) else None,
                    "coverage_ratio": coverage.get("coverage_ratio") if isinstance(coverage, dict) else None,
                    "review_report": review_report_path.as_posix(),
                    "stdout": apply_result["stdout"],
                    "stderr": apply_result["stderr"],
                },
                "synthesize": synthesize_result,
            }
        )

    failed = [
        row
        for row in results
        if row["apply"]["returncode"] != 0
        or row["apply"]["gates_passed"] is not True
        or (row.get("synthesize") and row["synthesize"]["returncode"] != 0)
    ]
    report = {
        "schema": SCHEMA,
        "generator": {"name": "apply-review-decisions-batch", "version": SCRIPT_VERSION},
        "inputs": {
            "decisions": decisions.as_posix(),
            "review_template": template.as_posix(),
            "input_profile": args.input_profile,
            "output_profile": args.output_profile,
            "synthesize": args.synthesize,
        },
        "summary": {
            "session_count": len(results),
            "passed_sessions": len(results) - len(failed),
            "failed_sessions": len(failed),
        },
        "sessions": results,
    }
    write_json(args.out.expanduser(), report)

    print(f"review_decisions_apply_report: {args.out}")
    print(f"sessions: {len(results)}")
    print(f"failed_sessions: {len(failed)}")
    return 0 if not failed else 2


if __name__ == "__main__":
    raise SystemExit(main())
