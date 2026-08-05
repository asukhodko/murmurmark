#!/usr/bin/env python3
"""Report bounded completion and actionability across meeting lifecycle runs."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any


SCHEMA = "murmurmark.meeting_lifecycle_corpus/v1"
INPUT_SCHEMA = "murmurmark.meeting_lifecycle_corpus_inputs/v1"
SCRIPT_VERSION = "0.1.0"
REPORT_RELATIVE = Path("derived/meeting-lifecycle/report.json")
INPUT_FILES = (
    REPORT_RELATIVE,
    Path("derived/meeting-lifecycle/next_action.json"),
    Path("derived/outcome/outcome.json"),
    Path("derived/readiness/session_readiness.json"),
    Path("derived/readiness/review-plan/review_plan.json"),
    Path("derived/readiness/review-plan/review_decisions_progress.json"),
    Path("derived/pipeline-run/authoritative_handoff_runs.jsonl"),
    Path(
        "derived/preprocess/speaker-preserving-neural-echo-v2-15/"
        "direct-asr/chunk_report.json"
    ),
)
REMEDIATION_PREFIXES = (
    "murmurmark review ",
    "murmurmark meeting --resume ",
    "murmurmark process ",
    "murmurmark finish ",
    "murmurmark repair ",
    "murmurmark cleanup ",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Report Reliable Final Handoff v1 corpus evidence.")
    parser.add_argument("sessions", nargs="*", help="Session paths, or 'all'. Defaults to all lifecycle reports.")
    parser.add_argument("--sessions-root", type=Path, default=Path("sessions"))
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("sessions/_reports/reliable-final-handoff-v1"),
    )
    parser.add_argument("--max-p90-ratio", type=float, default=1.0)
    parser.add_argument("--max-session-ratio", type=float, default=2.0)
    parser.add_argument("--min-eligible-sessions", type=int, default=3)
    parser.add_argument("--freeze-inputs", action="store_true")
    parser.add_argument("--replace-frozen-inputs", action="store_true")
    parser.add_argument("--require-frozen-inputs", action="store_true")
    parser.add_argument("--require-passing-gates", action="store_true")
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def canonical_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def hash_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def display_path(path: Path) -> str:
    absolute = path.resolve()
    try:
        return str(absolute.relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(absolute)


def resolve_sessions(values: list[str], sessions_root: Path) -> list[Path]:
    root = sessions_root.expanduser().resolve()
    if not values or values == ["all"]:
        sessions = [path.parents[2] for path in root.glob("*/derived/meeting-lifecycle/report.json")]
    elif values == ["latest"]:
        candidates = sorted(path.parents[2] for path in root.glob("*/derived/meeting-lifecycle/report.json"))
        sessions = candidates[-1:] if candidates else []
    else:
        sessions = []
        for raw in values:
            candidate = Path(raw).expanduser()
            if not candidate.is_absolute():
                direct = candidate.resolve()
                candidate = direct if direct.exists() else root / candidate
            sessions.append(candidate.resolve())
    return sorted(set(sessions), key=lambda path: path.name)


def input_snapshot(sessions: list[Path]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for session in sessions:
        for relative in INPUT_FILES:
            path = session / relative
            row: dict[str, Any] = {
                "session_id": session.name,
                "artifact": relative.as_posix(),
                "exists": path.is_file(),
            }
            if path.is_file():
                row.update({"bytes": path.stat().st_size, "sha256": hash_file(path)})
            rows.append(row)
    fingerprint = hash_bytes(canonical_bytes(rows))
    return {
        "schema": INPUT_SCHEMA,
        "generator": {"name": "report-meeting-lifecycle-corpus", "version": SCRIPT_VERSION},
        "sessions": len(sessions),
        "artifacts": rows,
        "fingerprint": fingerprint,
    }


def normalized_command_items(payload: dict[str, Any] | None) -> list[dict[str, str]]:
    if not payload:
        return []
    raw = payload.get("next_commands")
    rows: list[dict[str, str]] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str) and item.strip():
                rows.append({"id": "command", "command": item.strip()})
            elif isinstance(item, dict) and isinstance(item.get("command"), str) and item["command"].strip():
                rows.append({"id": str(item.get("id") or "command"), "command": item["command"].strip()})
    recommended = payload.get("recommended_next")
    if isinstance(recommended, str) and recommended.strip():
        rows.append({"id": "recommended_next", "command": recommended.strip()})
    unique: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        if row["command"] not in seen:
            seen.add(row["command"])
            unique.append(row)
    return unique


def is_remediation_command(command: str) -> bool:
    normalized = " ".join(command.split())
    return normalized.startswith(REMEDIATION_PREFIXES)


def review_plan_manual_items(plan: dict[str, Any] | None) -> int:
    if not plan:
        return 0
    summary = plan.get("summary") if isinstance(plan.get("summary"), dict) else {}
    for key in ("review_action_count", "grouped_review_row_count", "raw_item_count"):
        value = safe_int(summary.get(key))
        if value > 0:
            return value
    for key in ("review_queue", "items", "lanes"):
        value = plan.get(key)
        if isinstance(value, list) and value:
            return len(value)
        if isinstance(value, dict) and value:
            return len(value)
    return 0


def output_transcript(outcome: dict[str, Any] | None) -> str | None:
    if not outcome:
        return None
    outputs = outcome.get("outputs") if isinstance(outcome.get("outputs"), dict) else {}
    transcript = outputs.get("transcript") if isinstance(outputs.get("transcript"), dict) else {}
    path = transcript.get("path")
    return str(path) if isinstance(path, str) and path else None


def same_session_path(left: Any, right: Any) -> bool:
    if not isinstance(left, str) or not isinstance(right, str) or not left or not right:
        return left == right
    return left == right or left.endswith("/" + right) or right.endswith("/" + left)


def explicit_budget_reason(report: dict[str, Any]) -> str | None:
    deferred = report.get("deferred_work")
    if isinstance(deferred, dict):
        reason = deferred.get("reason") or deferred.get("status")
        if isinstance(reason, str) and reason:
            return reason
    budgets = report.get("budgets")
    if isinstance(budgets, dict):
        reason = budgets.get("reason") or budgets.get("status")
        if isinstance(reason, str) and reason:
            return reason
    return None


def spne_asr_reuse_evidence(session: Path) -> dict[str, Any]:
    path = (
        session
        / "derived/preprocess/speaker-preserving-neural-echo-v2-15/direct-asr/chunk_report.json"
    )
    payload = read_json(path)
    if payload is None:
        return {
            "applicable": False,
            "passed": None,
            "reason": "chunk_report_missing",
            "report": None,
        }
    chunks = payload.get("chunks") if isinstance(payload.get("chunks"), list) else []
    changed_raw = payload.get("changed_chunks") if isinstance(payload.get("changed_chunks"), list) else []
    changed = {safe_int(value, -1) for value in changed_raw}
    changed.discard(-1)
    indices: set[int] = set()
    decoded: set[int] = set()
    reused: set[int] = set()
    unknown: list[dict[str, Any]] = []
    duplicate_indices: set[int] = set()
    for row in chunks:
        if not isinstance(row, dict):
            unknown.append({"index": None, "status": "invalid_row"})
            continue
        index = safe_int(row.get("index"), -1)
        status = str(row.get("status") or "unknown")
        if index < 0:
            unknown.append({"index": row.get("index"), "status": status})
            continue
        if index in indices:
            duplicate_indices.add(index)
        indices.add(index)
        if status == "candidate_audio_identity_bounded_splice":
            decoded.add(index)
        elif status == "bit_exact_baseline_reuse":
            reused.add(index)
        else:
            unknown.append({"index": index, "status": status})
    expected_reused = indices - changed
    passed = bool(chunks) and all(
        (
            payload.get("candidate_audio_is_primary_whisper_input") is True,
            changed <= indices,
            decoded == changed,
            reused == expected_reused,
            not unknown,
            not duplicate_indices,
            decoded.isdisjoint(reused),
            decoded | reused == indices,
        )
    )
    reasons: list[str] = []
    if payload.get("candidate_audio_is_primary_whisper_input") is not True:
        reasons.append("candidate_audio_not_primary_input")
    if changed - indices:
        reasons.append("changed_chunk_missing")
    if decoded != changed:
        reasons.append("decoded_set_does_not_match_changed_set")
    if reused != expected_reused:
        reasons.append("unchanged_set_not_bit_exact_reused")
    if unknown:
        reasons.append("unknown_chunk_status")
    if duplicate_indices:
        reasons.append("duplicate_chunk_index")
    if not chunks:
        reasons.append("empty_chunk_report")
    return {
        "applicable": True,
        "passed": passed,
        "reason": "exact_sparse_reuse" if passed else ",".join(reasons),
        "report": display_path(path),
        "total_chunks": len(indices),
        "changed_chunks": sorted(changed),
        "decoded_chunks": sorted(decoded),
        "bit_exact_reused_chunks": sorted(reused),
        "reuse_ratio": round(len(reused) / len(indices), 6) if indices else 0.0,
    }


def session_row(session: Path) -> dict[str, Any]:
    report_path = session / REPORT_RELATIVE
    report = read_json(report_path)
    outcome = read_json(session / "derived/outcome/outcome.json")
    readiness = read_json(session / "derived/readiness/session_readiness.json")
    review_plan = read_json(session / "derived/readiness/review-plan/review_plan.json")
    next_action = read_json(session / "derived/meeting-lifecycle/next_action.json")
    if not report or report.get("schema") != "murmurmark.meeting_lifecycle_report/v1":
        return {
            "session_id": session.name,
            "session": display_path(session),
            "valid": False,
            "errors": ["missing_or_incompatible_lifecycle_report"],
        }

    elapsed = report.get("elapsed_sec") if isinstance(report.get("elapsed_sec"), dict) else {}
    capture = safe_float(elapsed.get("capture"))
    after_stop = safe_float(elapsed.get("total_after_stop"), safe_float(elapsed.get("postprocessing")))
    ratio = after_stop / capture if capture > 0 else None
    result = str(report.get("result") or "unknown")
    unresolved = report.get("unresolved_review") if isinstance(report.get("unresolved_review"), dict) else {}
    unresolved_count = safe_int(unresolved.get("count"))
    unresolved_seconds = safe_float(unresolved.get("seconds"))
    export = report.get("export") if isinstance(report.get("export"), dict) else {}
    export_blockers = export.get("blockers") if isinstance(export.get("blockers"), list) else []

    lifecycle_next = report.get("next") if isinstance(report.get("next"), dict) else {}
    next_command = lifecycle_next.get("command")
    commands = normalized_command_items(readiness) + normalized_command_items(outcome)
    if isinstance(next_command, str) and next_command.strip():
        commands.append({"id": "lifecycle_next", "command": next_command.strip()})
    unique_commands = {row["command"]: row for row in commands}
    remediation = [row for row in unique_commands.values() if is_remediation_command(row["command"])]
    report_manual = (
        report.get("manual_decisions")
        if isinstance(report.get("manual_decisions"), dict)
        else {}
    )
    manual_items = max(
        review_plan_manual_items(review_plan),
        safe_int(report_manual.get("total")),
    )
    resume_command = report.get("resume_command")
    explicit_failure = result == "failed" and isinstance(report.get("reason"), str) and bool(report.get("reason"))
    actionability_reason = "none"
    actionable = False
    if result == "ready":
        actionable = True
        actionability_reason = "terminal_ready"
    elif result == "failed" and explicit_failure:
        actionable = True
        actionability_reason = "terminal_failure_explained"
    elif result == "interrupted" and report.get("resume_available") is True and isinstance(resume_command, str):
        actionable = True
        actionability_reason = "resume_command"
    elif lifecycle_next.get("status") == "human_decision_required" and manual_items > 0:
        actionable = True
        actionability_reason = "bounded_manual_items"
    elif remediation:
        actionable = True
        actionability_reason = "executable_remediation"
    elif manual_items > 0:
        actionable = True
        actionability_reason = "bounded_manual_items"
    elif result == "ready_with_review" and unresolved_count == 0 and not export_blockers:
        actionable = True
        actionability_reason = "nonblocking_follow_up"
    elif result == "ready_with_review":
        actionability_reason = str(
            lifecycle_next.get("reason") or "blocking_review_without_action"
        )

    current_profile = outcome.get("selected_profile") if outcome else None
    current_transcript = output_transcript(outcome)
    stale_profile = bool(current_profile and report.get("selected_profile") != current_profile)
    stale_transcript = bool(current_transcript and not same_session_path(report.get("transcript"), current_transcript))
    stale_handoff = stale_profile or stale_transcript
    budget_reason = explicit_budget_reason(report)
    over_budget = ratio is not None and ratio > 2.0
    action_times = elapsed.get("actions") if isinstance(elapsed.get("actions"), dict) else {}
    spne_reuse = spne_asr_reuse_evidence(session)

    return {
        "session_id": session.name,
        "session": display_path(session),
        "valid": True,
        "report": display_path(report_path),
        "result": result,
        "reason": report.get("reason"),
        "capture_sec": round(capture, 3),
        "total_after_stop_sec": round(after_stop, 3),
        "after_stop_capture_ratio": round(ratio, 6) if ratio is not None else None,
        "action_elapsed_sec": {
            str(key): round(safe_float(value), 3) for key, value in sorted(action_times.items())
        },
        "selected_profile": report.get("selected_profile"),
        "current_selected_profile": current_profile,
        "stale_handoff": stale_handoff,
        "stale_profile": stale_profile,
        "stale_transcript": stale_transcript,
        "unresolved_review": {
            "count": unresolved_count,
            "seconds": round(unresolved_seconds, 3),
            "export_blockers": [str(item) for item in export_blockers],
        },
        "actionability": {
            "passed": actionable,
            "reason": actionability_reason,
            "remediation_commands": remediation,
            "manual_items": manual_items,
            "next_action": next_action.get("action") if next_action else None,
            "lifecycle_next_status": lifecycle_next.get("status"),
        },
        "budget": {
            "over_default_session_ratio": over_budget,
            "explicit_reason": budget_reason,
        },
        "spne_asr_reuse": spne_reuse,
    }


def aggregate_actions(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    values: dict[str, list[float]] = {}
    for row in rows:
        for action, elapsed in row.get("action_elapsed_sec", {}).items():
            values.setdefault(action, []).append(safe_float(elapsed))
    return {
        action: {
            "count": len(items),
            "p50_sec": round(percentile(items, 0.50), 3),
            "p90_sec": round(percentile(items, 0.90), 3),
            "max_sec": round(max(items), 3),
        }
        for action, items in sorted(values.items())
    }


def markdown(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        "# Reliable Final Handoff v1 Corpus",
        "",
        f"Status: **{payload['status']}**",
        "",
        f"Input fingerprint: `{payload['inputs']['fingerprint']}`.",
        f"Lifecycle reports: `{summary['reports']}`; eligible usable: `{summary['eligible_usable']}`.",
        f"Results: `{json.dumps(summary['results'], ensure_ascii=False, sort_keys=True)}`.",
        f"Post-stop p50/p90/max: `{summary['post_stop_p50_sec']}s` / "
        f"`{summary['post_stop_p90_sec']}s` / `{summary['post_stop_max_sec']}s`.",
        f"Ratio p50/p90/max: `{summary['ratio_p50']}` / `{summary['ratio_p90']}` / "
        f"`{summary['ratio_max']}`.",
        f"Dead-end blockers: `{summary['dead_end_blockers']}`; stale handoffs: "
        f"`{summary['stale_handoffs']}`.",
        f"SPNE sparse reuse: `{summary['spne_reuse_passed']}/{summary['spne_reuse_applicable']}` "
        "applicable sessions passed.",
        "",
        "| Session | Result | Capture | After stop | Ratio | Actionability | Stale |",
        "|---|---|---:|---:|---:|---|---|",
    ]
    for row in payload["sessions"]:
        if not row.get("valid"):
            lines.append(f"| `{row['session_id']}` | invalid | - | - | - | {', '.join(row['errors'])} | - |")
            continue
        lines.append(
            f"| `{row['session_id']}` | {row['result']} | {row['capture_sec']:.3f}s | "
            f"{row['total_after_stop_sec']:.3f}s | "
            f"{row['after_stop_capture_ratio'] if row['after_stop_capture_ratio'] is not None else '-'} | "
            f"{row['actionability']['reason']} | {'yes' if row['stale_handoff'] else 'no'} |"
        )
    lines += ["", "## Gates", ""]
    for gate, passed in payload["gates"].items():
        lines.append(f"- `{gate}`: `{'pass' if passed else 'fail'}`")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    sessions = resolve_sessions(args.sessions, args.sessions_root)
    snapshot = input_snapshot(sessions)
    out_dir = args.out_dir.expanduser()
    frozen_path = out_dir / "meeting_lifecycle_inputs_v1.json"
    frozen_before = read_json(frozen_path)
    freeze_requested = args.freeze_inputs or args.replace_frozen_inputs
    if args.replace_frozen_inputs or (args.freeze_inputs and frozen_before is None):
        out_dir.mkdir(parents=True, exist_ok=True)
        frozen_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        frozen_before = snapshot
    frozen_match = bool(frozen_before and frozen_before.get("fingerprint") == snapshot["fingerprint"])

    rows = [session_row(session) for session in sessions]
    valid = [row for row in rows if row.get("valid")]
    eligible = [
        row
        for row in valid
        if row.get("result") in {"ready", "ready_with_review"}
        and safe_float(row.get("capture_sec")) > 0
    ]
    post = [safe_float(row["total_after_stop_sec"]) for row in eligible]
    ratios = [safe_float(row["after_stop_capture_ratio"]) for row in eligible]
    dead_ends = [row for row in valid if not row["actionability"]["passed"]]
    stale = [row for row in valid if row["stale_handoff"]]
    spne_applicable = [row for row in valid if row["spne_asr_reuse"]["applicable"]]
    spne_failed = [row for row in spne_applicable if row["spne_asr_reuse"]["passed"] is not True]
    unreasoned_overruns = [
        row for row in eligible
        if safe_float(row.get("after_stop_capture_ratio")) > args.max_session_ratio
        and not row["budget"]["explicit_reason"]
    ]
    summary = {
        "reports": len(rows),
        "valid_reports": len(valid),
        "eligible_usable": len(eligible),
        "results": dict(sorted(Counter(str(row.get("result")) for row in valid).items())),
        "post_stop_p50_sec": round(percentile(post, 0.50), 3),
        "post_stop_p75_sec": round(percentile(post, 0.75), 3),
        "post_stop_p90_sec": round(percentile(post, 0.90), 3),
        "post_stop_max_sec": round(max(post, default=0.0), 3),
        "ratio_p50": round(percentile(ratios, 0.50), 6),
        "ratio_p75": round(percentile(ratios, 0.75), 6),
        "ratio_p90": round(percentile(ratios, 0.90), 6),
        "ratio_max": round(max(ratios, default=0.0), 6),
        "dead_end_blockers": len(dead_ends),
        "stale_handoffs": len(stale),
        "unreasoned_overruns": len(unreasoned_overruns),
        "spne_reuse_applicable": len(spne_applicable),
        "spne_reuse_passed": len(spne_applicable) - len(spne_failed),
        "spne_reuse_failed": len(spne_failed),
        "actions": aggregate_actions(valid),
    }
    gates = {
        "all_reports_valid": bool(rows) and len(valid) == len(rows),
        "enough_eligible_sessions": len(eligible) >= args.min_eligible_sessions,
        "p90_ratio_within_goal": bool(ratios) and summary["ratio_p90"] <= args.max_p90_ratio,
        "no_unreasoned_session_overrun": not unreasoned_overruns,
        "no_dead_end_blockers": not dead_ends,
        "no_stale_handoffs": not stale,
        "spne_unchanged_windows_reused": bool(spne_applicable) and not spne_failed,
    }
    if args.require_frozen_inputs:
        gates["frozen_inputs_match"] = frozen_match
    status = "passed" if all(gates.values()) else "failed"
    payload = {
        "schema": SCHEMA,
        "generator": {"name": "report-meeting-lifecycle-corpus", "version": SCRIPT_VERSION},
        "status": status,
        "thresholds": {
            "max_p90_ratio": args.max_p90_ratio,
            "max_session_ratio": args.max_session_ratio,
            "min_eligible_sessions": args.min_eligible_sessions,
        },
        "inputs": {
            "fingerprint": snapshot["fingerprint"],
            "frozen_manifest": display_path(frozen_path),
            "frozen_manifest_present": frozen_before is not None,
            "frozen_match": frozen_match,
            "freeze_requested": freeze_requested,
        },
        "summary": summary,
        "gates": gates,
        "sessions": rows,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "meeting_lifecycle_corpus_v1.json"
    md_path = out_dir / "meeting_lifecycle_corpus_v1.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(markdown(payload), encoding="utf-8")
    print(f"meeting_lifecycle_corpus: {display_path(json_path)}")
    print(f"status: {status}")
    print(f"eligible: {summary['eligible_usable']}")
    print(f"ratio_p90: {summary['ratio_p90']}")
    print(f"dead_end_blockers: {summary['dead_end_blockers']}")
    print(f"stale_handoffs: {summary['stale_handoffs']}")
    return 2 if args.require_passing_gates and status != "passed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
