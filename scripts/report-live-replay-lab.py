#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = "murmurmark.live_replay_lab/v1"
SCRIPT_VERSION = "0.1.0"
BASELINE_POLICY = "online_live_me_remote_overlap_filter_v1"
DEFAULT_OUTPUT_DIR = Path("derived/live/replay-lab")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare live role policies offline without changing raw audio or batch output."
    )
    parser.add_argument("session", type=Path)
    parser.add_argument("--refresh", action="store_true", help="refresh live-vs-batch comparison first")
    parser.add_argument("--with-labs", action="store_true", help="refresh every exploratory policy")
    parser.add_argument("--lab-policy", action="append", default=[], help="refresh one extra policy")
    parser.add_argument("--out-dir", type=Path, help="output directory; defaults inside the session")
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise SystemExit(f"missing input: {path}") from error
    except json.JSONDecodeError as error:
        raise SystemExit(f"invalid JSON: {path}: {error}") from error
    if not isinstance(value, dict):
        raise SystemExit(f"expected JSON object: {path}")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise SystemExit(f"invalid JSONL: {path}:{line_number}: {error}") from error
            if isinstance(value, dict):
                rows.append(value)
    return rows


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def rel(path: Path, session: Path) -> str:
    try:
        return str(path.relative_to(session))
    except ValueError:
        return str(path)


def refresh_comparison(session: Path, args: argparse.Namespace) -> None:
    command = [sys.executable, str(Path(__file__).with_name("compare-live-batch.py")), str(session)]
    if args.with_labs:
        command.append("--with-labs")
    for policy in args.lab_policy:
        command.extend(["--lab-policy", policy])
    subprocess.run(command, check=True)


def baseline_profile(comparison: dict[str, Any]) -> dict[str, Any]:
    metrics = comparison.get("metrics") if isinstance(comparison.get("metrics"), dict) else {}
    parity = comparison.get("parity_gates") if isinstance(comparison.get("parity_gates"), dict) else {}
    return {
        "policy": BASELINE_POLICY,
        "status": "baseline",
        "metrics": metrics,
        "parity_gates": parity,
        "live_implementable": True,
    }


def policy_rows(comparison: dict[str, Any]) -> list[dict[str, Any]]:
    target_me = ((comparison.get("shadow_profiles") or {}).get("target_me") or {})
    profiles = dict(target_me) if isinstance(target_me, dict) else {}
    profiles.setdefault(BASELINE_POLICY, baseline_profile(comparison))
    rows: list[dict[str, Any]] = []
    for policy, profile_value in profiles.items():
        profile = profile_value if isinstance(profile_value, dict) else {}
        metrics = profile.get("metrics") if isinstance(profile.get("metrics"), dict) else {}
        parity = profile.get("parity_gates") if isinstance(profile.get("parity_gates"), dict) else {}
        gates = parity.get("gates") if isinstance(parity.get("gates"), list) else []
        non_passing = [
            str(gate.get("name"))
            for gate in gates
            if isinstance(gate, dict) and gate.get("status") != "passed"
        ]
        rows.append(
            {
                "policy": str(policy),
                "status": profile.get("status"),
                "live_implementable": bool(profile.get("live_implementable", True)),
                "diagnostic_lab": bool(profile.get("diagnostic_lab", False)),
                "missing_me_count": safe_int(metrics.get("live_missing_me_utterance_count")),
                "missing_me_seconds": round(safe_float(metrics.get("live_missing_me_seconds")), 3),
                "missing_me_visible_in_suppressed_mic_seconds": round(
                    safe_float(metrics.get("live_missing_me_visible_in_suppressed_mic_seconds")), 3
                ),
                "missing_me_not_visible_in_suppressed_mic_seconds": round(
                    safe_float(metrics.get("live_missing_me_not_visible_in_suppressed_mic_seconds")), 3
                ),
                "remote_in_me_count": safe_int(metrics.get("live_suspected_remote_leak_in_me_count")),
                "remote_in_me_seconds": round(
                    safe_float(metrics.get("live_suspected_remote_leak_in_me_seconds")), 3
                ),
                "blocking_order_mismatch_count": safe_int(
                    metrics.get("live_blocking_contentful_role_constrained_order_mismatch_count")
                ),
                "token_f1": (
                    round(safe_float(metrics.get("live_batch_token_f1")), 6)
                    if metrics.get("live_batch_token_f1") is not None
                    else None
                ),
                "all_parity_gates_passed": bool(metrics.get("all_parity_gates_passed")),
                "non_passing_gates": non_passing,
            }
        )
    rows.sort(key=lambda row: (row["policy"] != BASELINE_POLICY, row["missing_me_seconds"], row["policy"]))
    return rows


def annotate_candidates(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str, str]:
    baseline = next((row for row in rows if row["policy"] == BASELINE_POLICY), None)
    if baseline is None:
        raise SystemExit("baseline live policy is missing from comparison")
    baseline_f1 = baseline.get("token_f1")
    for row in rows:
        row["delta_vs_baseline"] = {
            "missing_me_seconds": round(row["missing_me_seconds"] - baseline["missing_me_seconds"], 3),
            "remote_in_me_seconds": round(row["remote_in_me_seconds"] - baseline["remote_in_me_seconds"], 3),
            "blocking_order_mismatch_count": (
                row["blocking_order_mismatch_count"] - baseline["blocking_order_mismatch_count"]
            ),
            "token_f1": (
                round(row["token_f1"] - baseline_f1, 6)
                if row.get("token_f1") is not None and baseline_f1 is not None
                else None
            ),
        }
        row["safe_improvement_vs_baseline"] = bool(
            row["policy"] != BASELINE_POLICY
            and row["missing_me_seconds"] < baseline["missing_me_seconds"]
            and row["remote_in_me_seconds"] <= baseline["remote_in_me_seconds"]
            and row["blocking_order_mismatch_count"] <= baseline["blocking_order_mismatch_count"]
            and (
                baseline_f1 is None
                or row.get("token_f1") is None
                or row["token_f1"] >= baseline_f1 - 0.01
            )
        )
    safe = [row for row in rows if row["safe_improvement_vs_baseline"]]
    if not safe:
        return rows, BASELINE_POLICY, "no_candidate_improves_local_recall_without_remote_or_order_regression"
    best = min(
        safe,
        key=lambda row: (
            row["missing_me_seconds"],
            row["remote_in_me_seconds"],
            row["blocking_order_mismatch_count"],
            -(row.get("token_f1") or 0.0),
        ),
    )
    return rows, str(best["policy"]), "safe_offline_candidate_improves_local_recall"


def dominates(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_f1 = left.get("token_f1") if left.get("token_f1") is not None else 0.0
    right_f1 = right.get("token_f1") if right.get("token_f1") is not None else 0.0
    no_worse = (
        left["missing_me_seconds"] <= right["missing_me_seconds"]
        and left["remote_in_me_seconds"] <= right["remote_in_me_seconds"]
        and left["blocking_order_mismatch_count"] <= right["blocking_order_mismatch_count"]
        and left_f1 >= right_f1
    )
    strictly_better = (
        left["missing_me_seconds"] < right["missing_me_seconds"]
        or left["remote_in_me_seconds"] < right["remote_in_me_seconds"]
        or left["blocking_order_mismatch_count"] < right["blocking_order_mismatch_count"]
        or left_f1 > right_f1
    )
    return no_worse and strictly_better


def pareto_frontier(rows: list[dict[str, Any]]) -> list[str]:
    return [
        str(row["policy"])
        for row in rows
        if not any(dominates(other, row) for other in rows if other is not row)
    ]


def infer_live_geometry(chunks: list[dict[str, Any]]) -> dict[str, Any]:
    durations = [safe_float(row.get("duration_sec")) for row in chunks if safe_float(row.get("duration_sec")) > 0]
    overlaps: list[float] = []
    for row in chunks:
        start = safe_float(row.get("start_sec"))
        end = safe_float(row.get("end_sec"))
        clip_start = safe_float(row.get("clip_start_sec"), start)
        clip_end = safe_float(row.get("clip_end_sec"), end)
        overlap = max(start - clip_start, clip_end - end, 0.0)
        if overlap > 0:
            overlaps.append(overlap)
    return {
        "window_sec": round(statistics.median(durations), 3) if durations else None,
        "overlap_sec": round(statistics.median(overlaps), 3) if overlaps else 0.0,
        "chunk_count": len(chunks),
    }


def geometry_check(chunks: list[dict[str, Any]], expected_window: float, expected_overlap: float) -> dict[str, Any]:
    reasons: list[str] = []
    if not chunks:
        reasons.append("live_chunks_missing")
    for offset, row in enumerate(sorted(chunks, key=lambda item: safe_int(item.get("index")))):
        index = safe_int(row.get("index"), offset + 1)
        start = safe_float(row.get("start_sec"))
        duration = safe_float(row.get("duration_sec"))
        expected_start = offset * expected_window
        if abs(start - expected_start) > 1.0:
            reasons.append(f"window_start_mismatch:{index}")
            break
        is_final = offset == len(chunks) - 1
        if not is_final and abs(duration - expected_window) > 1.0:
            reasons.append(f"window_duration_mismatch:{index}")
            break
        clip_start = safe_float(row.get("clip_start_sec"), start)
        clip_end = safe_float(row.get("clip_end_sec"), start + duration)
        if expected_overlap > 0 and offset > 0 and clip_start > start - expected_overlap + 1.0:
            reasons.append(f"overlap_before_mismatch:{index}")
            break
        if expected_overlap > 0 and not is_final and clip_end < start + duration + expected_overlap - 1.0:
            reasons.append(f"overlap_after_mismatch:{index}")
            break
    return {
        "expected_window_sec": expected_window,
        "expected_overlap_sec": expected_overlap,
        "compatible": not reasons,
        "reasons": reasons,
    }


def pipeline_timings(session: Path) -> dict[str, Any]:
    path = session / "derived/pipeline-run/pipeline_run_report.json"
    if not path.exists():
        path = session / "derived/run/pipeline_run.json"
    if not path.exists():
        return {"source": None, "steps": {}, "batch_transcription_sec": None, "stronger_audio_judge_sec": None}
    report = read_json(path)
    steps = report.get("steps") if isinstance(report.get("steps"), list) else []
    durations = {
        str(step.get("name")): round(safe_float(step.get("duration_sec")), 3)
        for step in steps
        if isinstance(step, dict) and step.get("name")
    }
    transcription = sum(
        duration for name, duration in durations.items() if name in {"transcribe_current", "transcribe_shadow_v2"}
    )
    return {
        "source": rel(path, session),
        "steps": durations,
        "batch_transcription_sec": round(transcription, 3),
        "stronger_audio_judge_sec": durations.get("audit_stronger_audio_judge"),
    }


def asr_cache_analysis(session: Path, chunks: list[dict[str, Any]]) -> dict[str, Any]:
    report_path = session / "derived/live/live_asr_cache_report.json"
    report = read_json(report_path) if report_path.exists() else {}
    parameters = report.get("parameters") if isinstance(report.get("parameters"), dict) else {}
    actual = infer_live_geometry(chunks)
    batch_window = safe_float(parameters.get("asr_window_sec"), 60.0)
    batch_overlap = safe_float(parameters.get("asr_overlap_sec"), 5.0)
    return {
        "current_report": rel(report_path, session) if report_path.exists() else None,
        "current_status": report.get("status"),
        "current_reasons": report.get("reasons") or [],
        "actual_live_geometry": actual,
        "batch_default_geometry": geometry_check(chunks, batch_window, batch_overlap),
        "live_geometry_candidate": geometry_check(chunks, 30.0, 5.0),
        "decision": (
            "cache_already_materialized"
            if report.get("materialized") is True
            else "shadow_compare_live_geometry_before_changing_batch_default"
        ),
        "production_default_changed": False,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Live Replay Lab",
        "",
        f"Session: `{payload['session']}`  ",
        f"Recommended policy: `{payload['recommendation']['policy']}`  ",
        f"Reason: `{payload['recommendation']['reason']}`  ",
        "Batch authoritative: `true`",
        "",
        "## Policy Matrix",
        "",
        "| Policy | Missing Me, s | Remote in Me, s | Blocking order | F1 | Safe improvement |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for row in payload["profiles"]:
        f1 = "-" if row.get("token_f1") is None else f"{row['token_f1']:.3f}"
        lines.append(
            f"| `{row['policy']}` | {row['missing_me_seconds']:.2f} | "
            f"{row['remote_in_me_seconds']:.2f} | {row['blocking_order_mismatch_count']} | "
            f"{f1} | {'yes' if row['safe_improvement_vs_baseline'] else 'no'} |"
        )
    cache = payload["asr_cache"]
    actual = cache["actual_live_geometry"]
    timings = payload["pipeline_timings"]
    lines.extend(
        [
            "",
            "## ASR Cache",
            "",
            f"- current status: `{cache.get('current_status')}`",
            f"- current reasons: `{', '.join(cache.get('current_reasons') or []) or 'none'}`",
            f"- observed live geometry: `{actual.get('window_sec')}/{actual.get('overlap_sec')}` seconds",
            f"- batch default compatible: `{str(cache['batch_default_geometry']['compatible']).lower()}`",
            f"- live geometry compatible: `{str(cache['live_geometry_candidate']['compatible']).lower()}`",
            f"- decision: `{cache['decision']}`",
            "",
            "## Cost Evidence",
            "",
            f"- batch transcription: `{timings.get('batch_transcription_sec')}` seconds",
            f"- stronger audio judge: `{timings.get('stronger_audio_judge_sec')}` seconds",
            "",
            "This report is offline evidence. It does not modify raw audio, batch transcript or production defaults.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    session = args.session.expanduser().resolve()
    if args.refresh:
        refresh_comparison(session, args)
    comparison_path = session / "derived/live/live_batch_comparison.json"
    comparison = read_json(comparison_path)
    rows, recommendation, reason = annotate_candidates(policy_rows(comparison))
    chunks_path = session / "derived/live/chunks.jsonl"
    chunks = read_jsonl(chunks_path)
    out_dir = (args.out_dir.expanduser().resolve() if args.out_dir else session / DEFAULT_OUTPUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": SCHEMA,
        "generator": {"name": "report-live-replay-lab", "version": SCRIPT_VERSION},
        "created_at": datetime.now(timezone.utc).isoformat(),
        "session": str(session),
        "batch_authoritative": True,
        "raw_audio_modified": False,
        "production_defaults_changed": False,
        "inputs": {
            "comparison": rel(comparison_path, session),
            "chunks": rel(chunks_path, session) if chunks_path.exists() else None,
        },
        "baseline_policy": BASELINE_POLICY,
        "profiles": rows,
        "pareto_frontier": pareto_frontier(rows),
        "recommendation": {
            "policy": recommendation,
            "reason": reason,
            "promotion_allowed": False,
        },
        "asr_cache": asr_cache_analysis(session, chunks),
        "pipeline_timings": pipeline_timings(session),
        "outputs": {
            "json": rel(out_dir / "live_replay_matrix.json", session),
            "markdown": rel(out_dir / "live_replay_matrix.md", session),
        },
    }
    json_path = out_dir / "live_replay_matrix.json"
    md_path = out_dir / "live_replay_matrix.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(payload), encoding="utf-8")
    print(f"live_replay_matrix: {json_path}")
    print(f"live_replay_report: {md_path}")
    print(f"recommended_policy: {recommendation}")
    print(f"reason: {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
