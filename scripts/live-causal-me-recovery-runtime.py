#!/usr/bin/env python3
"""Materialize explicit-only causal Me recovery from closed live chunks.

This process is deliberately disposable. The live worker starts it only after the base chunk and
normal preview are durable. It may fail or be killed without changing those artifacts.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any


SCHEMA = "murmurmark.live_causal_me_recovery_runtime/v1"
SCRIPT_VERSION = "1.2.0"
BASE_PROFILE = (
    "online_live_me_remote_overlap_filter_live_boundary_split_retime_causal_remote_energy_"
    "local_island_micro_asr_v2_causal_remote_active_me_separation_v1_runtime_v1"
)
PROFILE = BASE_PROFILE.removesuffix("_runtime_v1") + "_causal_double_talk_me_recovery_v1_runtime_v1"
OUTPUT_RELATIVE = Path("derived/live/causal-me-recovery-runtime-v1")
REMOTE_ACTIVE_MAX_ASR_GROUPS = 24
DOUBLE_TALK_STAGE_TIMEOUT_SEC = 28.0


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


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


def clean_text(value: Any) -> str:
    return " ".join(str(value or "").replace("\n", " ").split()).strip()


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
    return rows


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    temporary.replace(path)


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def load_script(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def chunks_through(session: Path, cutoff: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted((session / "derived/live/chunks").glob("*/chunk.json")):
        row = read_json(path)
        if row and safe_int(row.get("index")) <= cutoff:
            rows.append(row)
    return rows


def build_runtime_baseline(session: Path, chunks: list[dict[str, Any]], compare: ModuleType) -> list[dict[str, Any]]:
    live_turn_rows = compare.live_turns(session, chunks)
    remote_turns = [row for row in live_turn_rows if row.get("role") == "Colleagues"]
    filtered = [
        row
        for row in live_turn_rows
        if row.get("role") != "Me"
        or not compare.live_me_remote_overlap_filter_decision(row, remote_turns)
    ]
    adjustments = compare.live_boundary_split_retime_adjustments(filtered)
    filtered = compare.apply_live_boundary_split_retime(filtered, adjustments)
    cutoff = max((safe_int(row.get("index")) for row in chunks), default=0)
    runtime_turns, _ = compare.runtime_causal_target_me_shadow_turns(
        session,
        require_remote_audio_guard=True,
    )
    runtime_turns = [row for row in runtime_turns if safe_int(row.get("chunk_index")) <= cutoff]
    runtime_turns, _ = compare.filter_micro_asr_turns_covered_by_base(runtime_turns, filtered)
    runtime_turns = compare.dedupe_supplemental_turns_by_interval(runtime_turns)
    return sorted(
        filtered + runtime_turns,
        key=lambda row: (
            safe_float(row.get("start")),
            safe_float(row.get("end")),
            str(row.get("id") or ""),
        ),
    )


def candidate_turn(row: dict[str, Any], source: str) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "chunk_index": row.get("chunk_index"),
        "source": source,
        "role": "Me",
        "start": safe_float(row.get("start")),
        "end": safe_float(row.get("end")),
        "text": clean_text(row.get("text")),
        "candidate_source": source,
        "timeline_causal": True,
        "used_batch_fields_for_selection": False,
        "runtime_evidence": row.get("runtime_evidence") or {},
    }


def mark_superseded_candidates(
    rows: list[dict[str, Any]],
    *,
    source: str,
    baseline_turns: list[dict[str, Any]],
    compare: ModuleType,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    accepted = [row for row in rows if row.get("status") == "accepted"]
    turns = [candidate_turn(row, source) for row in accepted]
    kept_turns, rejected = compare.filter_micro_asr_turns_covered_by_base(turns, baseline_turns)
    kept_ids = {str(row.get("id") or "") for row in kept_turns}
    rejected_by_id = {str(row.get("id") or ""): row for row in rejected}
    for turn in turns:
        start = safe_float(turn.get("start"))
        end = safe_float(turn.get("end"), start)
        duration = max(0.001, end - start)
        for base in baseline_turns:
            if base.get("role") != "Me":
                continue
            overlap = max(
                0.0,
                min(end, safe_float(base.get("end")))
                - max(start, safe_float(base.get("start"))),
            )
            if overlap / duration < 0.65:
                continue
            turn_id = str(turn.get("id") or "")
            kept_ids.discard(turn_id)
            rejected_by_id[turn_id] = {
                "reason": "covered_by_later_or_overlapping_base_me_turn",
                "overlap_sec": round(overlap, 3),
                "overlap_ratio": round(overlap / duration, 6),
                "base_turn": {
                    key: base.get(key)
                    for key in ("id", "chunk_index", "start", "end", "text")
                },
            }
            break
    effective: list[dict[str, Any]] = []
    for row in rows:
        if row.get("status") != "accepted":
            row["runtime_publication_status"] = "rejected_by_algorithm"
            continue
        row_id = str(row.get("id") or "")
        if row_id in kept_ids:
            row["runtime_publication_status"] = "effective_candidate"
            effective.append(row)
        else:
            row["runtime_publication_status"] = "superseded_by_later_base_turn"
            row["runtime_supersession_evidence"] = rejected_by_id.get(row_id) or {}
    return rows, effective


def run_stage(command: list[str], *, timeout_sec: float = 0.0) -> dict[str, Any]:
    started = time.monotonic()
    if timeout_sec <= 0.0:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        return {
            "status": "passed" if result.returncode == 0 else "failed",
            "returncode": result.returncode,
            "elapsed_sec": round(time.monotonic() - started, 3),
            "stdout_tail": result.stdout[-2000:],
            "stderr_tail": result.stderr[-2000:],
        }
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            stdout, stderr = process.communicate(timeout=2.0)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            stdout, stderr = process.communicate()
    return {
        "status": "timed_out_fail_open" if timed_out else "passed" if process.returncode == 0 else "failed",
        "returncode": process.returncode,
        "timeout_sec": timeout_sec,
        "timed_out": timed_out,
        "elapsed_sec": round(time.monotonic() - started, 3),
        "stdout_tail": stdout[-2000:],
        "stderr_tail": stderr[-2000:],
    }


def add_runtime_evidence(
    path: Path,
    *,
    invocation: dict[str, Any],
    stage: dict[str, Any],
) -> list[dict[str, Any]]:
    rows = read_jsonl(path)
    for row in rows:
        row["runtime_evidence"] = {
            **invocation,
            "stage_status": stage.get("status"),
            "stage_elapsed_sec": stage.get("elapsed_sec"),
        }
    write_jsonl(path, rows)
    return rows


def render_markdown(turns: list[dict[str, Any]], state: dict[str, Any]) -> str:
    lines = [
        "# Recording-Time Causal Me Recovery v1",
        "",
        "Explicit diagnostic shadow. The normal preview and batch transcript remain unchanged.",
        f"Profile: `{PROFILE}`.",
        f"Cutoff chunk: `{state.get('through_chunk_index')}`.",
        "Batch authoritative: `true`.",
        "Promotion allowed: `false`.",
        "",
    ]
    for row in turns:
        text = clean_text(row.get("text"))
        if not text:
            continue
        minutes, seconds = divmod(int(max(0.0, safe_float(row.get("start")))), 60)
        lines.extend([f"## {minutes:02d}:{seconds:02d} {row.get('role')}", "", text, ""])
    return "\n".join(lines).rstrip() + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run explicit-only causal Me recovery through one closed live chunk.")
    parser.add_argument("session", type=Path)
    parser.add_argument("--through-chunk-index", type=int, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--language", default="ru")
    parser.add_argument("--whisper-cli", default="whisper-cli")
    parser.add_argument("--invocation-id", required=True)
    parser.add_argument("--submitted-at", required=True)
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Isolated diagnostic output; defaults to derived/live/causal-me-recovery-runtime-v1.",
    )
    parser.add_argument("--recording-active", action="store_true")
    parser.add_argument("--paced-replay", action="store_true")
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    started_at = now_iso()
    started_monotonic = time.monotonic()
    session = args.session.expanduser().resolve()
    output = (
        args.output_dir.expanduser().resolve()
        if args.output_dir
        else session / OUTPUT_RELATIVE
    )
    try:
        output_relative = output.relative_to(session)
    except ValueError:
        print("error: output directory must stay inside the session", file=sys.stderr)
        return 2
    output.mkdir(parents=True, exist_ok=True)
    scripts = Path(__file__).resolve().parent
    compare = load_script(scripts / "compare-live-batch.py", "murmurmark_runtime_compare_helpers")
    chunks = chunks_through(session, args.through_chunk_index)
    invocation = {
        "schema": "murmurmark.live_causal_me_recovery_invocation/v1",
        "invocation_id": args.invocation_id,
        "submitted_at": args.submitted_at,
        "started_at": started_at,
        "through_chunk_index": args.through_chunk_index,
        "recording_active_at_submit": args.recording_active,
        "paced_replay": args.paced_replay,
        "closed_current_and_past_chunks_only": True,
        "past_only_enrollment": True,
        "incremental_runtime": True,
        "used_batch_fields_for_selection": False,
        "normal_preview_connected": False,
        "batch_authoritative": True,
        "promotion_allowed": False,
    }
    if not chunks:
        state = {**invocation, "schema": SCHEMA, "status": "failed_open", "reason": "closed_chunks_missing"}
        write_json(output / "state.json", state)
        append_jsonl(output / "runtime_runs.jsonl", state)
        return 2

    baseline_turns = build_runtime_baseline(session, chunks, compare)
    baseline_path = output / "runtime_baseline.json"
    write_json(baseline_path, {"schema": SCHEMA, "profile": "runtime_live_only_baseline", "turns": baseline_turns})

    local_output = output / "local-island-v2"
    incremental_root = output / "incremental-cache-v1"
    local_command = [
        sys.executable,
        str(scripts / "live-causal-local-island-micro-asr.py"),
        str(session),
        "--through-chunk-index",
        str(args.through_chunk_index),
        "--output-dir",
        str(local_output),
        "--existing-me-json",
        str(baseline_path),
        "--runtime-shadow",
        "--model",
        args.model,
        "--language",
        args.language,
        "--whisper-cli",
        args.whisper_cli,
        "--incremental-cache-dir",
        str(incremental_root / "local-island-v2"),
    ]
    if args.force:
        local_command.append("--force")
    local_stage = run_stage(local_command)
    local_rows = add_runtime_evidence(
        local_output / "candidates.jsonl",
        invocation=invocation,
        stage=local_stage,
    )
    if local_stage["status"] != "passed":
        completed_at = now_iso()
        state = {
            **invocation,
            "schema": SCHEMA,
            "status": "failed_open",
            "reason": "local_island_stage_failed",
            "completed_at": completed_at,
            "elapsed_sec": round(time.monotonic() - started_monotonic, 3),
            "stages": {"local_island_v2": local_stage},
        }
        write_json(output / "state.json", state)
        append_jsonl(output / "runtime_runs.jsonl", state)
        return 3

    local_rows, accepted_local = mark_superseded_candidates(
        local_rows,
        source="causal-local-island-micro-asr-v2-runtime",
        baseline_turns=baseline_turns,
        compare=compare,
    )
    write_jsonl(local_output / "candidates.jsonl", local_rows)
    remote_baseline = baseline_turns + [
        candidate_turn(row, "causal-local-island-micro-asr-v2-runtime")
        for row in accepted_local
    ]
    remote_baseline_path = output / "runtime_baseline_with_local.json"
    write_json(remote_baseline_path, {"schema": SCHEMA, "profile": "runtime_live_only_plus_local", "turns": remote_baseline})

    remote_output = output / "remote-active-v1"
    remote_command = [
        sys.executable,
        str(scripts / "live-causal-remote-active-me-separation.py"),
        str(session),
        "--through-chunk-index",
        str(args.through_chunk_index),
        "--output-dir",
        str(remote_output),
        "--source-selection",
        str(local_output / "selection.jsonl"),
        "--existing-me-json",
        str(remote_baseline_path),
        "--runtime-shadow",
        "--max-asr-groups",
        str(REMOTE_ACTIVE_MAX_ASR_GROUPS),
        "--model",
        args.model,
        "--language",
        args.language,
        "--whisper-cli",
        args.whisper_cli,
        "--incremental-cache-dir",
        str(incremental_root / "remote-active-v1"),
    ]
    if args.force:
        remote_command.append("--force")
    remote_stage = run_stage(remote_command)
    remote_rows = add_runtime_evidence(
        remote_output / "candidates.jsonl",
        invocation=invocation,
        stage=remote_stage,
    )

    double_output = output / "double-talk-v1"
    previous_double_state = read_json(double_output / "state.json")
    previous_double_cutoff = safe_int(previous_double_state.get("through_chunk_index"))
    double_command = [
        sys.executable,
        str(scripts / "live-causal-double-talk-me-recovery.py"),
        str(session),
        "--through-chunk-index",
        str(args.through_chunk_index),
        "--from-chunk-index",
        str(1 if args.force else max(1, previous_double_cutoff + 1)),
        "--source-selection",
        str(local_output / "selection.jsonl"),
        "--output-dir",
        str(double_output),
        "--runtime-shadow",
        "--view-profile",
        "runtime",
        "--micro-asr-backend",
        "whisper-cpp-cpu",
        "--model",
        args.model,
        "--language",
        args.language,
        "--whisper-cli",
        args.whisper_cli,
    ]
    if args.force:
        double_command.append("--force")
    double_stage = run_stage(double_command, timeout_sec=DOUBLE_TALK_STAGE_TIMEOUT_SEC)
    double_rows = add_runtime_evidence(
        double_output / "candidates.jsonl",
        invocation=invocation,
        stage=double_stage,
    )
    completed_at = now_iso()
    session_meta = read_json(session / "session.json")
    ended_at = parse_datetime(session_meta.get("ended_at"))
    completed_dt = parse_datetime(completed_at)
    completed_before_stop = bool(
        args.paced_replay
        or (args.recording_active and ended_at is None)
        or (ended_at and completed_dt and completed_dt <= ended_at)
    )
    final_invocation = {
        **invocation,
        "completed_at": completed_at,
        "completed_before_stop": completed_before_stop,
        "pre_stop_provenance": (
            "paced_closed_chunk_replay"
            if args.paced_replay
            else "recording_time_worker"
            if args.recording_active
            else "post_stop_diagnostic"
        ),
    }
    local_rows = add_runtime_evidence(
        local_output / "candidates.jsonl",
        invocation=final_invocation,
        stage=local_stage,
    )
    remote_rows = add_runtime_evidence(
        remote_output / "candidates.jsonl",
        invocation=final_invocation,
        stage=remote_stage,
    )
    double_rows = add_runtime_evidence(
        double_output / "candidates.jsonl",
        invocation=final_invocation,
        stage=double_stage,
    )
    local_rows, accepted_local = mark_superseded_candidates(
        local_rows,
        source="causal-local-island-micro-asr-v2-runtime",
        baseline_turns=baseline_turns,
        compare=compare,
    )
    remote_rows, accepted_remote = mark_superseded_candidates(
        remote_rows,
        source="causal-remote-active-me-separation-v1-runtime",
        baseline_turns=baseline_turns
        + [candidate_turn(row, "causal-local-island-micro-asr-v2-runtime") for row in accepted_local],
        compare=compare,
    )
    strict_double_turns, double_contract_rejections = (
        compare.causal_double_talk_me_recovery_v1_shadow_turns(
            session,
            double_output / "candidates.jsonl",
        )
    )
    strict_double_ids = {str(row.get("id") or "") for row in strict_double_turns}
    double_rows, accepted_double = mark_superseded_candidates(
        double_rows,
        source="causal-double-talk-me-recovery-v1-runtime",
        baseline_turns=baseline_turns
        + [candidate_turn(row, "causal-local-island-micro-asr-v2-runtime") for row in accepted_local]
        + [candidate_turn(row, "causal-remote-active-me-separation-v1-runtime") for row in accepted_remote],
        compare=compare,
    )
    accepted_double = [
        row for row in accepted_double if str(row.get("id") or "") in strict_double_ids
    ]
    for row in double_rows:
        if row.get("status") == "accepted" and str(row.get("id") or "") not in strict_double_ids:
            row["runtime_publication_status"] = "rejected_contract_fail_open"
    write_jsonl(local_output / "candidates.jsonl", local_rows)
    write_jsonl(remote_output / "candidates.jsonl", remote_rows)
    write_jsonl(double_output / "candidates.jsonl", double_rows)
    enriched_turns = sorted(
        baseline_turns
        + [candidate_turn(row, "causal-local-island-micro-asr-v2-runtime") for row in accepted_local]
        + [candidate_turn(row, "causal-remote-active-me-separation-v1-runtime") for row in accepted_remote]
        + [candidate_turn(row, "causal-double-talk-me-recovery-v1-runtime") for row in accepted_double],
        key=lambda row: (
            safe_float(row.get("start")),
            safe_float(row.get("end")),
            str(row.get("id") or ""),
        ),
    )
    status = (
        "completed"
        if remote_stage["status"] == "passed" and double_stage["status"] == "passed"
        else "completed_partial"
    )
    double_state = read_json(double_output / "state.json")
    double_incremental = {
        "stage": "causal_double_talk_me_recovery_v1",
        "status": double_stage.get("status"),
        "through_chunk_index": args.through_chunk_index,
        "previous_through_chunk_index": previous_double_cutoff,
        "new_chunk_count": max(0, args.through_chunk_index - previous_double_cutoff),
        "reused_chunk_count": min(args.through_chunk_index, previous_double_cutoff),
        "processed_group_count": safe_int(double_state.get("source_group_count")),
        "accepted_candidate_count": len(accepted_double),
        "elapsed_sec": double_stage.get("elapsed_sec"),
        "timed_out": double_stage.get("timed_out") is True,
    }
    state = {
        **final_invocation,
        "schema": SCHEMA,
        "generator": {"name": "live-causal-me-recovery-runtime", "version": SCRIPT_VERSION},
        "status": status,
        "profile": PROFILE,
        "through_chunk_index": args.through_chunk_index,
        "elapsed_sec": round(time.monotonic() - started_monotonic, 3),
        "baseline_turn_count": len(baseline_turns),
        "accepted_local_island_count": len(accepted_local),
        "accepted_remote_active_count": len(accepted_remote),
        "accepted_double_talk_count": len(accepted_double),
        "double_talk_contract_rejection_count": len(double_contract_rejections),
        "algorithm_accepted_candidate_count": sum(
            1 for row in local_rows + remote_rows + double_rows if row.get("status") == "accepted"
        ),
        "superseded_candidate_count": sum(
            1
            for row in local_rows + remote_rows + double_rows
            if row.get("runtime_publication_status") == "superseded_by_later_base_turn"
        ),
        "accepted_candidate_count": len(accepted_local) + len(accepted_remote) + len(accepted_double),
        "accepted_candidate_seconds": round(
            sum(
                safe_float(row.get("duration_sec"))
                for row in accepted_local + accepted_remote + accepted_double
            ),
            3,
        ),
        "stages": {
            "local_island_v2": local_stage,
            "remote_active_v1": remote_stage,
            "double_talk_v1": double_stage,
        },
        "incremental_runtime": {
            "schema": "murmurmark.live_recovery_incremental_runtime/v1",
            "mode": "persistent_watermark_content_addressed_cache",
            "local_island_v2": (
                read_json(local_output / "state.json").get("incremental_cache") or {}
            ),
            "remote_active_v1": (
                read_json(remote_output / "state.json").get("incremental_cache") or {}
            ),
            "double_talk_v1": double_incremental,
            "cache_root": str(incremental_root.relative_to(session)),
        },
        "outputs": {
            "draft_json": str(output_relative / "draft.json"),
            "draft_markdown": str(output_relative / "transcript.shadow.md"),
            "runtime_runs": str(output_relative / "runtime_runs.jsonl"),
            "local_candidates": str(output_relative / "local-island-v2/candidates.jsonl"),
            "remote_active_candidates": str(output_relative / "remote-active-v1/candidates.jsonl"),
            "double_talk_candidates": str(output_relative / "double-talk-v1/candidates.jsonl"),
        },
        "normal_preview_connected": False,
        "batch_authoritative": True,
        "promotion_allowed": False,
    }
    write_json(
        output / "draft.json",
        {
            "schema": "murmurmark.live_causal_me_recovery_runtime_draft/v1",
            "created_at": completed_at,
            "profile": PROFILE,
            "through_chunk_index": args.through_chunk_index,
            "turns": enriched_turns,
            "normal_preview_connected": False,
            "batch_authoritative": True,
            "promotion_allowed": False,
        },
    )
    (output / "transcript.shadow.md").write_text(render_markdown(enriched_turns, state), encoding="utf-8")
    write_json(output / "state.json", state)
    append_jsonl(output / "runtime_runs.jsonl", state)
    print(f"status: {status}")
    print(f"through_chunk_index: {args.through_chunk_index}")
    print(f"accepted_candidates: {state['accepted_candidate_count']}")
    print(f"completed_before_stop: {completed_before_stop}")
    print(f"draft: {output / 'transcript.shadow.md'}")
    return 0 if remote_stage["status"] == "passed" else 4


if __name__ == "__main__":
    raise SystemExit(main())
