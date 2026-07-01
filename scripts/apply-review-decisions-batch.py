#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


SCRIPT_VERSION = "0.3.1"
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
        "--allow-partial-review",
        action="store_true",
        help="Apply closed rows even when some template rows are still missing or todo.",
    )
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
        "--refresh-reports",
        action="store_true",
        help="After applying decisions, refresh session quality, operational readiness, and review plan reports.",
    )
    parser.add_argument("--session-quality-out-dir", type=Path, default=Path("sessions/_reports/session-quality"))
    parser.add_argument("--operational-readiness-out-dir", type=Path, default=Path("sessions/_reports/operational-readiness"))
    parser.add_argument("--review-plan-out-dir", type=Path, default=Path("sessions/_reports/review-plan"))
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
        "--audio-judge-queue",
        type=Path,
        default=Path("sessions/_reports/audio-judge-v0/audio_judge_v0_queue_predictions.jsonl"),
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
    candidates = {
        str(session),
        session.as_posix(),
        session.name,
        f"sessions/{session.name}",
        f"./sessions/{session.name}",
        f"./{session.as_posix()}",
    }
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


def safe_str(value: Any) -> str:
    return str(value or "").strip()


def session_readiness_path(session: Path) -> Path:
    return session / "derived/readiness/session_readiness.json"


def post_apply_readiness(session: Path) -> dict[str, Any]:
    path = session_readiness_path(session)
    payload = read_json(path)
    if not isinstance(payload, dict):
        return {
            "exists": False,
            "path": path.as_posix(),
            "recommended_next": f"murmurmark report {session.as_posix()}",
            "next_commands": [
                {
                    "id": "report",
                    "label": "Refresh this session readiness.",
                    "command": f"murmurmark report {session.as_posix()}",
                }
            ],
        }

    commands = payload.get("next_commands")
    next_commands = [item for item in commands if isinstance(item, dict)] if isinstance(commands, list) else []
    if not next_commands:
        next_commands = [
            {
                "id": "report",
                "label": "Refresh this session readiness.",
                "command": f"murmurmark report {session.as_posix()}",
            }
        ]
    recommended = next((safe_str(item.get("command")) for item in next_commands if safe_str(item.get("command"))), "")
    return {
        "exists": True,
        "path": path.as_posix(),
        "use_gate": payload.get("use_gate"),
        "selected_profile": payload.get("selected_profile"),
        "verdict": payload.get("verdict"),
        "recommendation": payload.get("recommendation"),
        "recommended_next": recommended or f"murmurmark report {session.as_posix()}",
        "next_commands": next_commands,
    }


def report_next_commands(report_path: Path, results: list[dict[str, Any]], failed: list[dict[str, Any]], failed_refresh: list[dict[str, Any]]) -> list[dict[str, str]]:
    if failed or failed_refresh:
        return [
            {
                "id": "inspect_apply_report",
                "label": "Inspect failed review apply details.",
                "command": f"less {report_path.as_posix()}",
            }
        ]
    if len(results) == 1:
        readiness = results[0].get("post_apply_readiness") if isinstance(results[0].get("post_apply_readiness"), dict) else {}
        commands = readiness.get("next_commands")
        if isinstance(commands, list) and commands:
            out = [
                {
                    "id": safe_str(item.get("id")) or f"next_{index}",
                    "label": safe_str(item.get("label")) or "Next command after review apply.",
                    "command": safe_str(item.get("command")),
                }
                for index, item in enumerate(commands, start=1)
                if isinstance(item, dict) and safe_str(item.get("command"))
            ]
            session = safe_str(results[0].get("session"))
            if session:
                out.append(
                    {
                        "id": "report",
                        "label": "Refresh and print this session readiness.",
                        "command": f"murmurmark report {session}",
                    }
                )
            return out
        session = safe_str(results[0].get("session"))
        if session:
            return [
                {
                    "id": "report",
                    "label": "Refresh and print this session readiness.",
                    "command": f"murmurmark report {session}",
                }
            ]
    return [
        {
            "id": "report_corpus",
            "label": "Refresh and print the corpus readiness summary.",
            "command": "murmurmark report corpus",
        }
    ]


def refresh_sessions_from_existing_report(out_dir: Path, fallback: list[Path]) -> list[Path]:
    report = read_json(out_dir / "session_quality_report.json")
    rows = report.get("sessions") if isinstance(report, dict) else None
    if not isinstance(rows, list):
        return fallback
    sessions: list[Path] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        session = str(row.get("session") or "").strip()
        if not session or session in seen:
            continue
        seen.add(session)
        sessions.append(Path(session))
    return sessions or fallback


def refresh_reports(args: argparse.Namespace, repo_root: Path, sessions: list[Path]) -> list[dict[str, Any]]:
    if not sessions:
        return []
    session_quality_out = args.session_quality_out_dir.expanduser()
    operational_out = args.operational_readiness_out_dir.expanduser()
    review_plan_out = args.review_plan_out_dir.expanduser()
    refresh_sessions = refresh_sessions_from_existing_report(session_quality_out, sessions)
    steps = [
        [
            sys.executable,
            str(repo_root / "scripts/report-session-quality.py"),
            *[str(session) for session in refresh_sessions],
            "--out-dir",
            str(session_quality_out),
            "--write-session-readiness",
        ],
        [
            sys.executable,
            str(repo_root / "scripts/report-operational-readiness.py"),
            "--session-quality",
            str(session_quality_out / "session_quality_report.json"),
            "--corpus-evaluation",
            str(args.corpus_evaluation.expanduser()),
            "--audio-judge",
            str(args.audio_judge.expanduser()),
            "--audio-judge-queue",
            str(args.audio_judge_queue.expanduser()),
            "--out-dir",
            str(operational_out),
        ],
        [
            sys.executable,
            str(repo_root / "scripts/build-review-plan.py"),
            "--operational-readiness",
            str(operational_out / "operational_readiness_report.json"),
            "--out-dir",
            str(review_plan_out),
        ],
    ]
    return [run_command(command) for command in steps]


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
        if args.allow_partial_review:
            apply_command.append("--allow-partial-review")
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
        if (args.synthesize or args.refresh_reports) and apply_result["returncode"] == 0:
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
                    "coverage_allowed": coverage.get("allowed") if isinstance(coverage, dict) else None,
                    "coverage_status": coverage.get("status") if isinstance(coverage, dict) else None,
                    "coverage_remaining_review_seconds": coverage.get("remaining_review_seconds") if isinstance(coverage, dict) else None,
                    "coverage_ratio": coverage.get("coverage_ratio") if isinstance(coverage, dict) else None,
                    "review_report": review_report_path.as_posix(),
                    "stdout": apply_result["stdout"],
                    "stderr": apply_result["stderr"],
                },
                "synthesize": synthesize_result,
            }
        )

    refresh_results: list[dict[str, Any]] = []
    if args.refresh_reports:
        refresh_results = refresh_reports(args, repo_root, sessions)
        for row in results:
            session = Path(str(row.get("session") or ""))
            row["post_apply_readiness"] = post_apply_readiness(session)

    failed = [
        row
        for row in results
        if row["apply"]["returncode"] != 0
        or row["apply"]["gates_passed"] is not True
        or (row.get("synthesize") and row["synthesize"]["returncode"] != 0)
    ]
    failed_refresh = [row for row in refresh_results if row.get("returncode") != 0]
    next_commands = report_next_commands(args.out.expanduser(), results, failed, failed_refresh)
    report = {
        "schema": SCHEMA,
        "generator": {"name": "apply-review-decisions-batch", "version": SCRIPT_VERSION},
        "inputs": {
            "decisions": decisions.as_posix(),
            "review_template": template.as_posix(),
            "input_profile": args.input_profile,
            "output_profile": args.output_profile,
            "allow_partial_review": args.allow_partial_review,
            "synthesize": args.synthesize,
            "synthesize_effective": args.synthesize or args.refresh_reports,
            "refresh_reports": args.refresh_reports,
            "session_quality_out_dir": str(args.session_quality_out_dir),
            "operational_readiness_out_dir": str(args.operational_readiness_out_dir),
            "review_plan_out_dir": str(args.review_plan_out_dir),
        },
        "summary": {
            "session_count": len(results),
            "passed_sessions": len(results) - len(failed),
            "failed_sessions": len(failed),
            "refresh_steps": len(refresh_results),
            "failed_refresh_steps": len(failed_refresh),
            "recommended_next": next((item["command"] for item in next_commands if item.get("command")), None),
        },
        "sessions": results,
        "refresh_reports": refresh_results,
        "next_commands": next_commands,
    }
    write_json(args.out.expanduser(), report)

    print(f"review_decisions_apply_report: {args.out}")
    print(f"sessions: {len(results)}")
    print(f"failed_sessions: {len(failed)}")
    if args.refresh_reports:
        print(f"failed_refresh_steps: {len(failed_refresh)}")
    return 0 if not failed and not failed_refresh else 2


if __name__ == "__main__":
    raise SystemExit(main())
