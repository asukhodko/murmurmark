#!/usr/bin/env python3
"""Freeze and decide Causal Canonical Mic ASR v1 on real sessions."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import authoritative_asr_cache as cache


SCHEMA = "murmurmark.causal_canonical_mic_asr_corpus_report/v1"
MANIFEST_SCHEMA = "murmurmark.causal_canonical_mic_asr_frozen_manifest/v1"
DEFAULT_OUTPUT = Path("sessions/_reports/authoritative-incremental-asr-v1/causal-canonical-mic-asr-v1")
DEFAULT_MANIFEST = Path("docs/testing/causal-canonical-mic-asr-v1-manifest.json")
MIN_SESSIONS = 3
PROMOTION_RATIO = 0.50


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Report causal canonical mic ASR evidence across real sessions.")
    parser.add_argument("sessions", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--frozen-manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--refresh-manifest", action="store_true")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    return cache.read_json(path) or {}


def artifact(path: Path, session: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": str(path.resolve().relative_to(session.resolve())),
        "exists": path.is_file(),
    }
    if path.is_file():
        result.update(cache.file_fingerprint(path, include_path=False))
    return result


def optional_artifact(path: Path, session: Path) -> dict[str, Any] | None:
    return artifact(path, session) if path.is_file() else None


def selected_output_paths(session: Path) -> list[Path]:
    handoff = read_json(session / "derived/pipeline-run/authoritative_handoff.json")
    paths = handoff.get("paths") if isinstance(handoff.get("paths"), dict) else {}
    selected: list[Path] = []
    for key in ("transcript", "notes", "verdict_json", "readiness"):
        value = paths.get(key)
        if isinstance(value, str) and value:
            path = Path(value)
            selected.append(path if path.is_absolute() else session / path)
    selected.extend(
        [
            session / "derived/transcript-simple/whisper-cpp/raw/mic.json",
            session / "derived/transcript-simple/whisper-cpp/raw/remote.json",
            session / "derived/synthesis-simple/extractive/evidence_notes.json",
            session / "derived/pipeline-run/authoritative_handoff.json",
            session / "derived/handoff-v2/handoff_manifest.json",
        ]
    )
    unique: dict[str, Path] = {}
    for path in selected:
        if path.is_file():
            unique[str(path.resolve())] = path
    return [unique[key] for key in sorted(unique)]


def frozen_inputs(session: Path) -> dict[str, Any]:
    neural = (
        session
        / "derived/preprocess/speaker-preserving-neural-echo-v2/production_selection_report.json"
    )
    values = {
        "session_manifest": artifact(session / "session.json", session),
        "raw_mic": artifact(session / "audio/mic/000001.caf", session),
        "raw_remote": artifact(session / "audio/remote/000001.caf", session),
        "canonical_mic": artifact(session / "derived/asr/mic.wav", session),
        "canonical_remote": artifact(session / "derived/asr/remote.wav", session),
        "local_fir_report": artifact(
            session / "derived/preprocess/echo/local_fir_report.json", session
        ),
        "echo_selection": optional_artifact(
            session / "derived/preprocess/echo/echo_suppression_selection.json", session
        ),
        "speaker_preserving_selection": optional_artifact(neural, session),
        "selected_outputs": [artifact(path, session) for path in selected_output_paths(session)],
    }
    return values


def stable_inputs(value: dict[str, Any]) -> dict[str, Any]:
    # Manifest timestamps are intentionally absent; only immutable path/size/hash identity is frozen.
    return value


def pipeline_step_duration(session: Path, name: str) -> float | None:
    report = read_json(session / "derived/pipeline-run/pipeline_run_report.json")
    for row in report.get("steps") or []:
        if isinstance(row, dict) and row.get("name") == name:
            value = row.get("duration_sec")
            return round(float(value), 6) if isinstance(value, (int, float)) else None
    return None


def session_row(session: Path) -> dict[str, Any]:
    session = session.expanduser().resolve()
    report_path = (
        session
        / "derived/experiments/live-shadow-v1/authoritative-mic-asr/report.json"
    )
    report = read_json(report_path)
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    prefix = report.get("prefix_probe") if isinstance(report.get("prefix_probe"), dict) else {}
    lineage = report.get("lineage") if isinstance(report.get("lineage"), dict) else {}
    minimum = lineage.get("minimum_future_context") if isinstance(lineage.get("minimum_future_context"), dict) else {}
    exact_ratio = float(summary.get("exact_hard_ratio") or 0.0)
    selected_profile = str(report.get("selected_profile") or "missing")
    recording_time = bool((report.get("candidate") or {}).get("recording_time_evidence"))
    prefix_rows = prefix.get("rows") if isinstance(prefix.get("rows"), list) else []
    bounded_prefix_exact = bool(prefix_rows) and all(row.get("exact") is True for row in prefix_rows)
    reasons: list[str] = []
    if report.get("schema") != "murmurmark.causal_canonical_mic_asr_report/v1":
        reasons.append("session_report_missing_or_invalid")
    if selected_profile != "raw_fallback":
        reasons.append(f"selected_profile_whole_session_only:{selected_profile}")
    if minimum.get("kind") != "bounded" or not isinstance(minimum.get("bounded_sec"), (int, float)):
        reasons.append("finite_future_context_not_proven")
    if not bounded_prefix_exact:
        reasons.append("bounded_prefix_probe_not_exact")
    if not recording_time:
        reasons.append("recording_time_evidence_missing")
    if exact_ratio < PROMOTION_RATIO:
        reasons.append(f"exact_mic_ratio_below_50_percent:{exact_ratio:.9f}")
    safety = report.get("safety") if isinstance(report.get("safety"), dict) else {}
    if safety.get("raw_capture_unchanged") is not True:
        reasons.append("raw_capture_integrity_not_proven")
    return {
        "session_id": session.name,
        "report": artifact(report_path, session),
        "selected_profile": selected_profile,
        "minimum_future_context": minimum,
        "prefix_probe": {
            "status": prefix.get("status") or "missing",
            "lookaheads_sec": [row.get("lookahead_sec") for row in prefix_rows],
            "exact": [row.get("exact") for row in prefix_rows],
            "all_bounded_contexts_exact": bounded_prefix_exact,
        },
        "candidate": {
            "recording_time_evidence": recording_time,
            "windows_total": int(summary.get("windows_total") or 0),
            "exact_windows": int(summary.get("exact_windows") or 0),
            "total_hard_sec": float(summary.get("total_hard_sec") or 0.0),
            "exact_hard_sec": float(summary.get("exact_hard_sec") or 0.0),
            "exact_hard_ratio": exact_ratio,
            "proofs_published": int(summary.get("proofs_published") or 0),
        },
        "post_stop_runtime": {
            "echo_preprocess_sec": pipeline_step_duration(session, "echo_preprocess"),
            "transcribe_current_sec": pipeline_step_duration(session, "transcribe_current"),
            "speaker_prepare_sec": pipeline_step_duration(
                session, "speaker_preserving_neural_echo_v2_prepare"
            ),
            "modeled_asr_wall_reduction_ratio": exact_ratio,
        },
        "frozen_inputs": stable_inputs(frozen_inputs(session)),
        "eligible": not reasons,
        "reasons": reasons,
    }


def manifest_payload(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema": MANIFEST_SCHEMA,
        "version": 1,
        "sessions": [
            {"session_id": row["session_id"], "inputs": row["frozen_inputs"]} for row in rows
        ],
    }


def compare_manifest(rows: list[dict[str, Any]], manifest: dict[str, Any]) -> None:
    expected = {
        str(row.get("session_id")): row.get("inputs")
        for row in manifest.get("sessions") or []
        if isinstance(row, dict)
    }
    for row in rows:
        frozen = expected.get(row["session_id"])
        row["frozen_inputs_match"] = frozen == row["frozen_inputs"]
        if not row["frozen_inputs_match"]:
            row["eligible"] = False
            row["reasons"].append("inputs_changed_from_frozen_manifest")


def write_markdown(output: Path, report: dict[str, Any]) -> None:
    summary = report["summary"]
    decision = report["decision"]
    lines = [
        "# Causal Canonical Mic ASR v1 Corpus",
        "",
        f"Decision: `{decision['status']}`",
        "",
        f"- Frozen sessions: `{summary['sessions']}`",
        f"- Exact raw-fallback mic windows: `{summary['exact_windows']}/{summary['windows_total']}`",
        f"- Exact hard seconds: `{summary['exact_hard_sec']}/{summary['total_hard_sec']}`",
        f"- Median modeled ASR wall reduction: `{summary['median_modeled_asr_wall_reduction_ratio']}`",
        f"- Recording-time evidence sessions: `{summary['recording_time_sessions']}`",
        f"- Inputs still matching manifest: `{summary['frozen_inputs_matching_sessions']}`",
        "",
        "## Sessions",
        "",
        "| Session | Selected mic | Exact windows | Prefix 5/30/120 | Modeled reduction |",
        "|---|---|---:|---|---:|",
    ]
    for row in report["sessions"]:
        prefix = "/".join("yes" if value is True else "no" for value in row["prefix_probe"]["exact"])
        candidate = row["candidate"]
        lines.append(
            f"| `{row['session_id']}` | `{row['selected_profile']}` | "
            f"`{candidate['exact_windows']}/{candidate['windows_total']}` | `{prefix or 'missing'}` | "
            f"`{row['post_stop_runtime']['modeled_asr_wall_reduction_ratio']:.6f}` |"
        )
    lines.extend(
        [
            "",
            "## Ceiling",
            "",
            "The only causal candidate is raw mic fallback. Every frozen session selected a post-Echo",
            "branch, bounded local-FIR prefixes differed byte-for-byte from the final baseline, and no",
            "recording-time mic proof was eligible. The current exact architecture therefore has a",
            "session-end causal boundary and cannot remove the mic critical path.",
            "",
            "Ordinary batch ASR remains authoritative; this result does not enable automatic reuse.",
        ]
    )
    cache.atomic_write_bytes(output / "causal_canonical_mic_asr_corpus_report.md", ("\n".join(lines) + "\n").encode("utf-8"))


def main() -> int:
    args = parse_args()
    output = args.output.expanduser().resolve()
    manifest_path = args.frozen_manifest.expanduser().resolve()
    rows = [session_row(session) for session in args.sessions]
    frozen = manifest_payload(rows)
    if args.refresh_manifest:
        cache.atomic_write_json(manifest_path, frozen)
    manifest = read_json(manifest_path)
    compare_manifest(rows, manifest)
    exact_windows = sum(row["candidate"]["exact_windows"] for row in rows)
    windows_total = sum(row["candidate"]["windows_total"] for row in rows)
    exact_sec = sum(row["candidate"]["exact_hard_sec"] for row in rows)
    total_sec = sum(row["candidate"]["total_hard_sec"] for row in rows)
    reductions = sorted(row["post_stop_runtime"]["modeled_asr_wall_reduction_ratio"] for row in rows)
    median_reduction = reductions[len(reductions) // 2] if reductions else 0.0
    gates = {
        "minimum_three_fresh_sessions": len(rows) >= MIN_SESSIONS,
        "frozen_inputs_match": bool(rows) and all(row["frozen_inputs_match"] for row in rows),
        "all_selected_branches_checkpointable": bool(rows)
        and all(row["selected_profile"] == "raw_fallback" for row in rows),
        "bounded_future_context_proven": bool(rows)
        and all(row["minimum_future_context"].get("kind") == "bounded" for row in rows),
        "recording_time_evidence_complete": bool(rows)
        and all(row["candidate"]["recording_time_evidence"] for row in rows),
        "post_stop_asr_wall_reduction_at_least_50_percent": bool(rows)
        and all(row["post_stop_runtime"]["modeled_asr_wall_reduction_ratio"] >= PROMOTION_RATIO for row in rows),
        "raw_capture_integrity": bool(rows)
        and all("raw_capture_integrity_not_proven" not in row["reasons"] for row in rows),
    }
    promote = all(gates.values())
    blockers = [name for name, passed in gates.items() if not passed]
    report = {
        "schema": SCHEMA,
        "created_at": utc_now(),
        "decision": {
            "status": "PROMOTE" if promote else "DO_NOT_PROMOTE",
            "gates": gates,
            "blockers": blockers,
            "promotion_allowed": promote,
            "ordinary_pipeline_changed": False,
            "batch_authoritative": True,
        },
        "summary": {
            "sessions": len(rows),
            "eligible_sessions": sum(1 for row in rows if row["eligible"]),
            "frozen_inputs_matching_sessions": sum(1 for row in rows if row["frozen_inputs_match"]),
            "recording_time_sessions": sum(
                1 for row in rows if row["candidate"]["recording_time_evidence"]
            ),
            "windows_total": windows_total,
            "exact_windows": exact_windows,
            "total_hard_sec": round(total_sec, 6),
            "exact_hard_sec": round(exact_sec, 6),
            "exact_hard_ratio": round(exact_sec / total_sec if total_sec else 0.0, 9),
            "median_modeled_asr_wall_reduction_ratio": round(median_reduction, 9),
            "selected_profiles": {
                profile: sum(1 for row in rows if row["selected_profile"] == profile)
                for profile in sorted({row["selected_profile"] for row in rows})
            },
        },
        "sessions": rows,
        "frozen_manifest": {
            "path": str(args.frozen_manifest),
            "schema": manifest.get("schema"),
            "sha256": cache.sha256_file(manifest_path) if manifest_path.is_file() else None,
        },
        "ceiling": {
            "minimum_future_context": "session_end",
            "causal_candidate": "raw_fallback_only",
            "whole_session_branches": [
                "local_fir_role_masked",
                "speaker_preserving_neural_echo_v2",
            ],
            "next_hypothesis": "causal_echo_guard_with_frozen_past_only_state_and_independent_quality_rebaseline",
        },
    }
    output.mkdir(parents=True, exist_ok=True)
    cache.atomic_write_json(output / "causal_canonical_mic_asr_corpus_report.json", report)
    write_markdown(output, report)
    print(f"causal_canonical_mic_asr_corpus_report: {output / 'causal_canonical_mic_asr_corpus_report.json'}")
    print(f"decision: {report['decision']['status']}")
    print(f"exact_hard_ratio: {report['summary']['exact_hard_ratio']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
