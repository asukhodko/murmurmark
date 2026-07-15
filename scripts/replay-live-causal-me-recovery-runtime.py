#!/usr/bin/env python3
"""Reveal closed chunks in order and exercise the recording-time recovery child."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


SCHEMA = "murmurmark.live_causal_me_recovery_paced_replay/v1"
SCRIPT_VERSION = "1.1.0"
OUTPUT_RELATIVE = Path("derived/live/causal-me-recovery-runtime-v1")


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


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


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def accepted_ids(path: Path, *, runtime: bool, through_chunk_index: int = 0) -> set[str]:
    return {
        str(row.get("id"))
        for row in read_jsonl(path)
        if row.get("status") == "accepted"
        and (through_chunk_index <= 0 or safe_int(row.get("chunk_index")) <= through_chunk_index)
        and (
            not runtime
            or row.get("runtime_publication_status") in {None, "effective_candidate"}
        )
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Paced causal Me recovery replay over closed chunk cutoffs.")
    parser.add_argument("session", type=Path)
    parser.add_argument(
        "--model",
        default=str(Path.home() / ".local/share/murmurmark/models/whisper.cpp/ggml-large-v3-q5_0.bin"),
    )
    parser.add_argument("--language", default="ru")
    parser.add_argument("--whisper-cli", default=os.environ.get("WHISPER_CLI", "whisper-cli"))
    parser.add_argument("--stride-chunks", type=int, default=12)
    parser.add_argument("--pace-scale", type=float, default=0.0)
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Isolated runtime output; defaults to the canonical diagnostic namespace.",
    )
    parser.add_argument(
        "--max-captured-sec",
        type=float,
        default=0.0,
        help="Stop at the last closed chunk at or before this source-time boundary.",
    )
    parser.add_argument("--refresh", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    session = args.session.expanduser().resolve()
    output = (
        args.output_dir.expanduser().resolve()
        if args.output_dir
        else session / OUTPUT_RELATIVE
    )
    try:
        output.relative_to(session)
    except ValueError:
        print("error: output directory must stay inside the session", file=sys.stderr)
        return 2
    if args.refresh:
        shutil.rmtree(output, ignore_errors=True)
    chunks = [
        row
        for row in read_jsonl(session / "derived/live/chunks.jsonl")
        if safe_int(row.get("index")) > 0
        and (
            args.max_captured_sec <= 0.0
            or float(row.get("end_sec") or 0.0) <= args.max_captured_sec + 0.001
        )
    ]
    indexes = sorted({safe_int(row.get("index")) for row in chunks})
    if not indexes:
        print("error: live chunks missing", file=sys.stderr)
        return 2
    stride = max(1, args.stride_chunks)
    cutoffs = indexes[stride - 1 :: stride]
    if indexes[-1] not in cutoffs:
        cutoffs.append(indexes[-1])
    runtime_script = Path(__file__).with_name("live-causal-me-recovery-runtime.py")
    invocations: list[dict[str, Any]] = []
    previous_end = 0.0
    for ordinal, cutoff in enumerate(cutoffs, start=1):
        chunk = next(row for row in chunks if safe_int(row.get("index")) == cutoff)
        chunk_end = float(chunk.get("end_sec") or previous_end)
        if args.pace_scale > 0.0 and previous_end > 0.0:
            time.sleep(max(0.0, chunk_end - previous_end) * args.pace_scale)
        previous_end = chunk_end
        submitted_at = now_iso()
        command = [
            sys.executable,
            str(runtime_script),
            str(session),
            "--through-chunk-index",
            str(cutoff),
            "--model",
            str(Path(args.model).expanduser()),
            "--language",
            args.language,
            "--whisper-cli",
            args.whisper_cli,
            "--invocation-id",
            f"paced_{ordinal:04d}_{cutoff:06d}",
            "--submitted-at",
            submitted_at,
            "--paced-replay",
        ]
        if args.output_dir:
            command.extend(["--output-dir", str(output)])
        started = time.monotonic()
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        invocations.append(
            {
                "ordinal": ordinal,
                "cutoff_chunk_index": cutoff,
                "simulated_captured_sec": round(chunk_end, 3),
                "submitted_at": submitted_at,
                "returncode": result.returncode,
                "elapsed_sec": round(time.monotonic() - started, 3),
                "stdout_tail": result.stdout[-1000:],
                "stderr_tail": result.stderr[-1000:],
            }
        )
        if result.returncode != 0:
            break

    final_cutoff = cutoffs[-1]
    replay_local = accepted_ids(
        session / "derived/live/causal-local-island-micro-asr-v2/candidates.jsonl",
        runtime=False,
        through_chunk_index=final_cutoff,
    )
    runtime_local = accepted_ids(
        output / "local-island-v2/candidates.jsonl",
        runtime=True,
        through_chunk_index=final_cutoff,
    )
    replay_remote = accepted_ids(
        session / "derived/live/causal-remote-active-me-separation-v1/candidates.jsonl",
        runtime=False,
        through_chunk_index=final_cutoff,
    )
    runtime_remote = accepted_ids(
        output / "remote-active-v1/candidates.jsonl",
        runtime=True,
        through_chunk_index=final_cutoff,
    )
    agreement = {
        "local_island": {
            "replay_count": len(replay_local),
            "runtime_count": len(runtime_local),
            "missing_in_runtime": sorted(replay_local - runtime_local),
            "extra_in_runtime": sorted(runtime_local - replay_local),
            "passed": replay_local == runtime_local,
        },
        "remote_active": {
            "replay_count": len(replay_remote),
            "runtime_count": len(runtime_remote),
            "missing_in_runtime": sorted(replay_remote - runtime_remote),
            "extra_in_runtime": sorted(runtime_remote - replay_remote),
            "passed": replay_remote == runtime_remote,
        },
    }
    state = read_json(output / "state.json")
    passed = bool(
        invocations
        and all(row.get("returncode") == 0 for row in invocations)
        and all(row.get("passed") is True for row in agreement.values())
        and state.get("completed_before_stop") is True
    )
    report = {
        "schema": SCHEMA,
        "generator": {"name": "replay-live-causal-me-recovery-runtime", "version": SCRIPT_VERSION},
        "created_at": now_iso(),
        "status": "passed" if passed else "failed",
        "session": session.name,
        "mode": "ordered_closed_chunk_cutoffs",
        "stride_chunks": stride,
        "pace_scale": args.pace_scale,
        "max_captured_sec": args.max_captured_sec,
        "source_coverage_sec": round(previous_end, 3),
        "cutoff_count": len(cutoffs),
        "invocations": invocations,
        "candidate_agreement": agreement,
        "runtime_state": state,
        "used_batch_fields_for_selection": False,
        "batch_authoritative": True,
        "promotion_allowed": False,
    }
    write_json(output / "paced_replay.json", report)
    print(f"status: {report['status']}")
    print(f"cutoffs: {len(cutoffs)}")
    print(f"local_agreement: {agreement['local_island']['passed']}")
    print(f"remote_active_agreement: {agreement['remote_active']['passed']}")
    print(f"report: {output / 'paced_replay.json'}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
