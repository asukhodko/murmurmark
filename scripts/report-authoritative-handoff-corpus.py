#!/usr/bin/env python3
"""Aggregate authoritative handoff runtime and no-regression evidence."""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = "murmurmark.authoritative_handoff_corpus/v1"
SCRIPT_VERSION = "0.1.0"
QUALITY_KEY_PARTS = (
    "review_burden",
    "needs_review",
    "local_only_island_recall",
    "remote_duplicate",
    "remote_like",
    "order",
    "notes_evidence_utterance_count",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Report Fast Authoritative Handoff corpus gates.")
    parser.add_argument("sessions", nargs="+", type=Path)
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("sessions/_reports/authoritative-handoff"),
    )
    parser.add_argument("--max-30-60m-sec", type=float, default=900.0)
    parser.add_argument("--max-long-ratio", type=float, default=0.4)
    parser.add_argument("--max-checkpoint-reuse-sec", type=float, default=30.0)
    parser.add_argument("--min-meaningful-sessions", type=int, default=3)
    parser.add_argument("--require-raw-integrity", action="store_true")
    parser.add_argument("--require-passing-gates", action="store_true")
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return rows
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("schema") == "murmurmark.authoritative_handoff_run/v1":
            rows.append(row)
    return rows


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def session_duration_sec(session: Path) -> float:
    payload = read_json(session / "session.json") or {}
    health = payload.get("health") if isinstance(payload.get("health"), dict) else {}
    for key in ("actual_duration_sec", "duration_sec"):
        value = safe_float(health.get(key), -1.0)
        if value > 0:
            return value
    durations: list[float] = []
    files = payload.get("files") if isinstance(payload.get("files"), dict) else {}
    for track in ("mic", "remote"):
        audio = payload.get(f"{track}_audio") if isinstance(payload.get(f"{track}_audio"), dict) else {}
        rate = safe_float(audio.get("sample_rate"), 0.0)
        for item in files.get(track) or []:
            if not isinstance(item, dict):
                continue
            frames = safe_float(item.get("frames"), 0.0)
            if frames > 0 and rate > 0:
                durations.append(frames / rate)
    return max(durations, default=0.0)


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


def is_cold_or_mixed(row: dict[str, Any]) -> bool:
    runtime = row.get("runtime") if isinstance(row.get("runtime"), dict) else {}
    if bool(runtime.get("force_asr")):
        return True
    provenance = row.get("asr_provenance") if isinstance(row.get("asr_provenance"), dict) else {}
    tracks = provenance.get("tracks") if isinstance(provenance.get("tracks"), dict) else {}
    return any(
        safe_float(track.get("chunks_transcribed"), 0.0) > 0
        for track in tracks.values()
        if isinstance(track, dict)
    )


def runtime_workers(row: dict[str, Any]) -> tuple[int, int]:
    runtime = row.get("runtime") if isinstance(row.get("runtime"), dict) else {}
    return int(runtime.get("asr_track_workers") or 0), int(runtime.get("micro_asr_workers") or 0)


def selected_quality(metrics: Any) -> dict[str, Any]:
    if not isinstance(metrics, dict):
        return {}
    return {
        key: value
        for key, value in sorted(metrics.items())
        if isinstance(value, (int, float, bool)) and any(part in key for part in QUALITY_KEY_PARTS)
    }


def comparable_fingerprint(row: dict[str, Any]) -> str | None:
    fingerprint = row.get("transcript_fingerprint")
    if not isinstance(fingerprint, dict):
        return None
    value = fingerprint.get("sha256")
    return str(value) if isinstance(value, str) and value else None


def session_row(session: Path, args: argparse.Namespace) -> dict[str, Any]:
    session = session.expanduser().resolve()
    duration = session_duration_sec(session)
    runs_path = session / "derived/pipeline-run/authoritative_handoff_runs.jsonl"
    runs = read_jsonl(runs_path)
    computed = [row for row in runs if row.get("mode") == "computed" and row.get("status") in {"ready", "review_required"}]
    parallel_cold = [
        row
        for row in computed
        if is_cold_or_mixed(row)
        and runtime_workers(row)[0] == 2
        and runtime_workers(row)[1] >= 2
    ]
    sequential_baselines = [row for row in computed if runtime_workers(row) == (1, 1)]
    repeats = [row for row in runs if row.get("mode") == "checkpoint_reuse"]
    cold = parallel_cold[-1] if parallel_cold else None
    baseline = sequential_baselines[-1] if sequential_baselines else None
    repeat = repeats[-1] if repeats else None

    cold_elapsed = safe_float(cold.get("elapsed_sec"), 0.0) if cold else None
    repeat_elapsed = safe_float(repeat.get("elapsed_sec"), 0.0) if repeat else None
    duration_class = "30_60m" if 1800.0 <= duration <= 3600.0 else "long" if duration > 3600.0 else "short"
    runtime_gate = None
    if cold_elapsed is not None and duration_class == "30_60m":
        runtime_gate = cold_elapsed <= args.max_30_60m_sec
    elif cold_elapsed is not None and duration_class == "long":
        runtime_gate = cold_elapsed <= duration * args.max_long_ratio

    baseline_quality = selected_quality(baseline.get("quality_metrics")) if baseline else {}
    cold_quality = selected_quality(cold.get("quality_metrics")) if cold else {}
    quality_differences = {
        key: {"baseline": baseline_quality.get(key), "candidate": cold_quality.get(key)}
        for key in sorted(set(baseline_quality) | set(cold_quality))
        if baseline_quality.get(key) != cold_quality.get(key)
    }
    quality_equivalent = bool(baseline and cold) and not quality_differences
    transcript_equivalent = bool(baseline and cold) and comparable_fingerprint(baseline) == comparable_fingerprint(cold)
    profile_equivalent = bool(baseline and cold) and baseline.get("selected_transcript_profile") == cold.get(
        "selected_transcript_profile"
    )
    repeat_gate = repeat_elapsed is not None and repeat_elapsed <= args.max_checkpoint_reuse_sec
    meaningful = duration_class in {"30_60m", "long"}
    gates = {
        "meaningful_duration": meaningful,
        "parallel_cold_run_present": cold is not None,
        "sequential_baseline_present": baseline is not None,
        "runtime_within_goal": runtime_gate is True,
        "checkpoint_reuse_within_goal": repeat_gate,
        "transcript_fingerprint_equivalent": transcript_equivalent,
        "selected_profile_equivalent": profile_equivalent,
        "quality_metrics_equivalent": quality_equivalent,
    }
    return {
        "session": str(session),
        "session_id": session.name,
        "duration_sec": round(duration, 3),
        "duration_class": duration_class,
        "history": str(runs_path),
        "runs": len(runs),
        "baseline": baseline,
        "parallel_cold": cold,
        "checkpoint_reuse": repeat,
        "cold_elapsed_sec": cold_elapsed,
        "cold_ratio": round(cold_elapsed / duration, 6) if cold_elapsed is not None and duration > 0 else None,
        "checkpoint_reuse_elapsed_sec": repeat_elapsed,
        "quality_differences": quality_differences,
        "gates": gates,
        "passed": all(gates.values()),
    }


def markdown(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        "# Fast Authoritative Handoff Corpus",
        "",
        f"Status: **{payload['status']}**",
        "",
        f"Meaningful sessions: `{summary['meaningful_sessions']}`; passing: `{summary['passing_sessions']}`.",
        f"Cold handoff p50/p95: `{summary['cold_p50_sec']}s` / `{summary['cold_p95_sec']}s`.",
        f"Checkpoint reuse max: `{summary['checkpoint_reuse_max_sec']}s`.",
        f"Raw capture integrity: `{payload['raw_capture_integrity']['status']}`.",
        "",
        "| Session | Duration | Cold | Ratio | Repeat | Gates |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for row in payload["sessions"]:
        failed = [key for key, passed in row["gates"].items() if not passed]
        lines.append(
            f"| `{row['session_id']}` | {row['duration_sec']:.1f}s | "
            f"{row['cold_elapsed_sec'] if row['cold_elapsed_sec'] is not None else '-'} | "
            f"{row['cold_ratio'] if row['cold_ratio'] is not None else '-'} | "
            f"{row['checkpoint_reuse_elapsed_sec'] if row['checkpoint_reuse_elapsed_sec'] is not None else '-'} | "
            f"{'pass' if not failed else ', '.join(failed)} |"
        )
    lines += ["", "Batch remains authoritative; deferred enrichment is outside these timings.", ""]
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    out_dir = args.out_dir.expanduser()
    raw_before_path = out_dir / "raw_before.sha256"
    raw_after_path = out_dir / "raw_after.sha256"
    try:
        raw_before = raw_before_path.read_text(encoding="utf-8")
        raw_after = raw_after_path.read_text(encoding="utf-8")
    except OSError:
        raw_before = None
        raw_after = None
    raw_match = raw_before is not None and raw_after is not None and raw_before == raw_after
    raw_integrity_status = "passed" if raw_match else "failed" if raw_before is not None and raw_after is not None else "not_checked"
    rows = [session_row(session, args) for session in args.sessions]
    meaningful = [row for row in rows if row["gates"]["meaningful_duration"]]
    cold_values = [float(row["cold_elapsed_sec"]) for row in meaningful if row["cold_elapsed_sec"] is not None]
    repeat_values = [
        float(row["checkpoint_reuse_elapsed_sec"])
        for row in meaningful
        if row["checkpoint_reuse_elapsed_sec"] is not None
    ]
    summary = {
        "sessions": len(rows),
        "meaningful_sessions": len(meaningful),
        "passing_sessions": sum(1 for row in meaningful if row["passed"]),
        "cold_p50_sec": round(percentile(cold_values, 0.50), 3),
        "cold_p95_sec": round(percentile(cold_values, 0.95), 3),
        "checkpoint_reuse_max_sec": round(max(repeat_values, default=0.0), 3),
    }
    gates = {
        "enough_meaningful_sessions": len(meaningful) >= args.min_meaningful_sessions,
        "cold_p95_within_goal": bool(cold_values) and summary["cold_p95_sec"] <= args.max_30_60m_sec,
        "all_meaningful_sessions_pass": bool(meaningful) and all(row["passed"] for row in meaningful),
    }
    if args.require_raw_integrity:
        gates["raw_capture_hashes_unchanged"] = raw_match
    status = "passed" if all(gates.values()) else "failed"
    payload = {
        "schema": SCHEMA,
        "generator": {"name": "report-authoritative-handoff-corpus", "version": SCRIPT_VERSION},
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "thresholds": {
            "max_30_60m_sec": args.max_30_60m_sec,
            "max_long_ratio": args.max_long_ratio,
            "max_checkpoint_reuse_sec": args.max_checkpoint_reuse_sec,
            "min_meaningful_sessions": args.min_meaningful_sessions,
        },
        "summary": summary,
        "raw_capture_integrity": {
            "status": raw_integrity_status,
            "required": bool(args.require_raw_integrity),
            "before": str(raw_before_path),
            "after": str(raw_after_path),
            "match": raw_match,
        },
        "gates": gates,
        "sessions": rows,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "authoritative_handoff_corpus_v1.json"
    md_path = out_dir / "authoritative_handoff_corpus_v1.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(markdown(payload), encoding="utf-8")
    print(f"authoritative_handoff_corpus: {json_path}")
    print(f"status: {status}")
    print(f"meaningful: {summary['passing_sessions']}/{summary['meaningful_sessions']}")
    print(f"cold_p95_sec: {summary['cold_p95_sec']}")
    return 2 if args.require_passing_gates and status != "passed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
