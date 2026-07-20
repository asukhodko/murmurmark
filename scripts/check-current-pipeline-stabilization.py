#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = "murmurmark.current_pipeline_stabilization_check/v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check the current MurmurMark production pipeline stabilization invariants."
    )
    parser.add_argument(
        "--session-quality",
        type=Path,
        default=Path("sessions/_reports/session-quality/session_quality_report.json"),
        help="Path to session_quality_report.json produced by `murmurmark report corpus`.",
    )
    parser.add_argument(
        "--sessions-root",
        type=Path,
        default=Path("sessions"),
        help="Sessions root used for latest/status checks.",
    )
    parser.add_argument(
        "--murmurmark-bin",
        type=Path,
        default=None,
        help="MurmurMark binary. Defaults to .build/debug/murmurmark, then PATH.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("sessions/_reports/current-pipeline-stabilization"),
        help="Directory for JSON/Markdown check reports.",
    )
    parser.add_argument(
        "--skip-cli",
        action="store_true",
        help="Only inspect JSON artifacts; do not run `murmurmark status/next`.",
    )
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def suffix(profile: str | None) -> str:
    if not profile or profile == "current":
        return ""
    return f".{profile}"


def session_path(row: dict[str, Any]) -> Path | None:
    value = row.get("session")
    if not isinstance(value, str) or not value:
        return None
    return Path(value)


def classify(row: dict[str, Any]) -> str:
    pipeline = str(row.get("pipeline_status") or "")
    gate = str(row.get("use_gate") or "")
    verdict = str(row.get("verdict") or "")
    if pipeline in {"partial", "incomplete"} or gate == "pipeline_incomplete":
        return "broken_capture_or_incomplete"
    if pipeline == "complete" and gate == "ready_for_notes":
        return "usable"
    if pipeline == "complete" and gate == "review_first":
        return "review_first"
    if pipeline == "complete" and gate == "do_not_use_without_manual_review":
        return "blocked"
    if verdict in {"failed", "missing"}:
        return "broken_or_missing"
    return "unknown"


def utterance_count(session: Path, profile: str | None) -> int | None:
    clean = session / "derived/transcript-simple/whisper-cpp/resolved" / f"clean_dialogue{suffix(profile)}.json"
    payload = read_json(clean)
    if not payload:
        return None
    utterances = payload.get("utterances")
    if not isinstance(utterances, list):
        return None
    return len(utterances)


def transcript_has_content(session: Path, profile: str | None) -> bool:
    transcript = session / "derived/transcript-simple/whisper-cpp/resolved" / f"transcript{suffix(profile)}.md"
    try:
        text = transcript.read_text(encoding="utf-8")
    except OSError:
        return False
    metadata_prefixes = ("Backend:", "Model:", "Language:", "Profile:")
    meaningful = [
        line.strip()
        for line in text.splitlines()
        if line.strip()
        and not line.startswith("#")
        and not line.startswith(metadata_prefixes)
    ]
    return bool(meaningful)


def has_verified_no_speech(session: Path, row: dict[str, Any]) -> bool:
    if row.get("session_classification") != "verified_no_speech":
        return False
    evidence = read_json(session / "derived/synthesis-simple/extractive/no_speech_evidence.json")
    checks = evidence.get("checks") if isinstance(evidence, dict) else None
    return (
        isinstance(evidence, dict)
        and evidence.get("schema") == "murmurmark.no_speech_evidence/v1"
        and evidence.get("status") == "verified_no_speech"
        and evidence.get("failures") == []
        and isinstance(checks, list)
        and bool(checks)
        and all(isinstance(check, dict) and check.get("passed") is True for check in checks)
    )


def readiness(session: Path) -> dict[str, Any]:
    return read_json(session / "derived/readiness/session_readiness.json") or {}


def has_explicit_blocker(row: dict[str, Any], session: Path) -> bool:
    risk_flags = row.get("risk_flags") if isinstance(row.get("risk_flags"), list) else []
    if risk_flags:
        return True
    payload = readiness(session)
    for key in ("review_blockers", "export_blockers", "use_gate_reasons"):
        value = payload.get(key)
        if isinstance(value, list) and value:
            return True
    return str(row.get("verdict") or "") == "failed"


def resolve_bin(explicit: Path | None) -> str:
    if explicit:
        return str(explicit)
    debug = Path(".build/debug/murmurmark")
    if debug.exists():
        return str(debug)
    return "murmurmark"


def run_command(command: list[str]) -> tuple[int, str]:
    proc = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    return proc.returncode, proc.stdout


def first_status(output: str) -> str | None:
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("status: "):
            return stripped.split("status: ", 1)[1].strip()
    return None


def contains_line(output: str, needle: str) -> bool:
    return any(line.strip() == needle for line in output.splitlines())


def find_silent_blocked_sessions(sessions_root: Path) -> list[Path]:
    result: list[Path] = []
    for report in sessions_root.glob("*/derived/pipeline-run/pipeline_run_report.json"):
        payload = read_json(report)
        if not payload:
            continue
        if payload.get("status") == "blocked" and payload.get("blocker") == "silent_capture":
            result.append(report.parents[2])
    return sorted(result)


def find_stale_live_sessions(sessions_root: Path) -> list[Path]:
    result: list[Path] = []
    for report in sessions_root.glob("*/derived/live/live_pipeline_report.json"):
        payload = read_json(report)
        if not payload:
            continue
        final = report.parent / "final_reconcile_report.json"
        if payload.get("status") == "running" and final.exists():
            result.append(report.parents[2])
    return sorted(result)


def run_cli_checks(bin_path: str, sessions_root: Path, errors: list[str], warnings: list[str]) -> dict[str, Any]:
    details: dict[str, Any] = {}

    code, status_out = run_command([bin_path, "status", "latest", "--sessions-root", str(sessions_root)])
    next_code, next_out = run_command([bin_path, "next", "latest", "--sessions-root", str(sessions_root)])
    status_value = first_status(status_out)
    next_value = first_status(next_out)
    details["latest"] = {
        "status_exit": code,
        "next_exit": next_code,
        "status": status_value,
        "next_status": next_value,
    }
    if code != 0:
        errors.append("`murmurmark status latest` failed")
    if next_code != 0:
        errors.append("`murmurmark next latest` failed")
    if status_value != next_value:
        errors.append(f"`status latest` and `next latest` disagree: {status_value!r} vs {next_value!r}")

    silent_sessions = find_silent_blocked_sessions(sessions_root)
    details["silent_capture_sessions"] = [str(path) for path in silent_sessions]
    if not silent_sessions:
        warnings.append("no silent_capture blocked session found under sessions root")
    for session in silent_sessions[:3]:
        code, status_out = run_command([bin_path, "status", str(session)])
        next_code, next_out = run_command([bin_path, "next", str(session)])
        if code != 0 or next_code != 0:
            errors.append(f"silent capture CLI check failed for {session}")
            continue
        if first_status(status_out) != "blocked" or first_status(next_out) != "blocked":
            errors.append(f"silent capture is not reported as blocked: {session}")
        if "gate: silent_capture" not in status_out or "gate: silent_capture" not in next_out:
            errors.append(f"silent capture does not expose silent_capture gate: {session}")
        if "murmurmark review" in next_out:
            errors.append(f"silent capture next suggests review instead of inspect/re-record: {session}")
        if not contains_line(status_out, "can_read_notes: false"):
            errors.append(f"silent capture status allows reading notes: {session}")

    stale_live_sessions = find_stale_live_sessions(sessions_root)
    details["stale_live_sessions"] = [str(path) for path in stale_live_sessions]
    for session in stale_live_sessions[:5]:
        code, status_out = run_command([bin_path, "status", str(session)])
        if code != 0:
            errors.append(f"stale live CLI check failed for {session}")
            continue
        if (
            "status: stale_running_after_finalize" not in status_out
            and "status: terminated_after_finalization_wait_timeout" not in status_out
        ):
            errors.append(f"stale live session still looks running in status output: {session}")

    return details


def markdown_report(payload: dict[str, Any]) -> str:
    lines = [
        "# Current Pipeline Stabilization Check",
        "",
        f"Status: `{payload['status']}`",
        "",
        "## Categories",
        "",
        "| Category | Count |",
        "| --- | ---: |",
    ]
    for key, value in sorted(payload["categories"].items()):
        lines.append(f"| `{key}` | {value} |")
    lines.extend(["", "## Errors", ""])
    errors = payload.get("errors") or []
    if errors:
        lines.extend(f"- {item}" for item in errors)
    else:
        lines.append("- none")
    lines.extend(["", "## Warnings", ""])
    warnings = payload.get("warnings") or []
    if warnings:
        lines.extend(f"- {item}" for item in warnings)
    else:
        lines.append("- none")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    report = read_json(args.session_quality)
    if not report:
        raise SystemExit(f"cannot read session quality report: {args.session_quality}")
    rows = report.get("sessions")
    if not isinstance(rows, list):
        raise SystemExit(f"session quality report has no sessions list: {args.session_quality}")

    errors: list[str] = []
    warnings: list[str] = []
    categories: Counter[str] = Counter()

    for row in rows:
        if not isinstance(row, dict):
            errors.append("session_quality_report contains a non-object session row")
            continue
        category = classify(row)
        categories[category] += 1
        session = session_path(row)
        session_id = str(row.get("session_id") or session or "unknown")
        if category == "unknown":
            errors.append(
                f"uncategorized session {session_id}: "
                f"pipeline_status={row.get('pipeline_status')!r}, use_gate={row.get('use_gate')!r}"
            )
            continue
        if not session or not session.exists():
            errors.append(f"session path is missing for {session_id}: {session}")
            continue

        gate = str(row.get("use_gate") or "")
        profile = str(row.get("selected_profile") or "")
        if category in {"usable", "review_first"}:
            count = utterance_count(session, profile)
            verified_empty = has_verified_no_speech(session, row)
            if verified_empty:
                if count != 0:
                    errors.append(f"verified no-speech session has transcript utterances: {session_id}")
                if transcript_has_content(session, profile):
                    errors.append(f"verified no-speech session has transcript content: {session_id}")
            else:
                if count is None or count <= 0:
                    errors.append(f"{category} session has no clean dialogue utterances: {session_id}")
                if not transcript_has_content(session, profile):
                    errors.append(f"{category} session has no non-empty transcript: {session_id}")
        elif category == "blocked" and not has_explicit_blocker(row, session):
            errors.append(f"blocked session has no explicit blocker evidence: {session_id}")
        elif category == "broken_capture_or_incomplete" and gate != "pipeline_incomplete":
            errors.append(f"incomplete session is not gated as pipeline_incomplete: {session_id}")

    cli_details: dict[str, Any] = {}
    if not args.skip_cli:
        cli_details = run_cli_checks(resolve_bin(args.murmurmark_bin), args.sessions_root, errors, warnings)

    payload = {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": "ok" if not errors else "failed",
        "inputs": {
            "session_quality": str(args.session_quality),
            "sessions_root": str(args.sessions_root),
        },
        "categories": dict(sorted(categories.items())),
        "errors": errors,
        "warnings": warnings,
        "cli": cli_details,
    }

    write_json(args.out_dir / "current_pipeline_stabilization_check.json", payload)
    (args.out_dir / "current_pipeline_stabilization_check.md").write_text(markdown_report(payload), encoding="utf-8")

    if errors:
        print("current pipeline stabilization check failed")
        for item in errors:
            print(f"- {item}")
        print(f"report: {args.out_dir / 'current_pipeline_stabilization_check.json'}")
        return 1

    print("current pipeline stabilization check ok")
    print(f"report: {args.out_dir / 'current_pipeline_stabilization_check.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
