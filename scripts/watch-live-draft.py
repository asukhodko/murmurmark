#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any


TERMINAL_STATUSES = {
    "completed",
    "completed_partial_draft",
    "failed",
    "disabled_backpressure",
    "disabled_pcm_copy",
    "stopped_by_limit",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Watch a MurmurMark shadow live draft and worker heartbeat.")
    parser.add_argument("session", type=Path)
    parser.add_argument("--poll-sec", type=float, default=1.0)
    parser.add_argument(
        "--diagnostic-draft",
        action="store_true",
        help="Watch transcript.draft.md with all candidate-only evidence instead of the conservative preview.",
    )
    return parser.parse_args()


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def changed_suffix(previous: str, current: str) -> str:
    if not previous or not current.startswith(previous):
        return current
    return current[len(previous) :]


def state_line(state: dict[str, Any]) -> str:
    progress = state.get("progress") if isinstance(state.get("progress"), dict) else {}
    status = str(state.get("status") or "waiting")
    stage = str(state.get("current_stage") or "unknown")
    index = state.get("current_index")
    lag = progress.get("live_lag_sec")
    heartbeat = str(state.get("heartbeat_at") or state.get("updated_at") or "-")
    parts = [f"status={status}", f"stage={stage}"]
    if index is not None:
        parts.append(f"chunk={index}")
    if isinstance(lag, (int, float)):
        parts.append(f"lag={float(lag):.1f}s")
    parts.append(f"heartbeat={heartbeat}")
    return "[live] " + " ".join(parts)


def main() -> int:
    args = parse_args()
    session = args.session.expanduser().resolve()
    diagnostic_path = session / "derived/live/transcript.draft.md"
    preview_path = session / "derived/live/transcript.preview.md"
    draft_path = diagnostic_path if args.diagnostic_draft else preview_path
    if not draft_path.exists() and diagnostic_path.exists():
        draft_path = diagnostic_path
    state_path = session / "derived/live/live_pipeline_state.json"
    previous_draft = ""
    previous_state_line = ""
    print(f"watching: {draft_path}", flush=True)
    try:
        while True:
            desired_path = diagnostic_path if args.diagnostic_draft else preview_path
            if desired_path.exists() and desired_path != draft_path:
                draft_path = desired_path
                previous_draft = ""
                print(f"\n[live] switched to: {draft_path}\n", flush=True)
            current_draft = read_text(draft_path)
            if current_draft != previous_draft:
                suffix = changed_suffix(previous_draft, current_draft)
                if suffix.strip():
                    if previous_draft and not current_draft.startswith(previous_draft):
                        print("\n[live] draft refreshed\n", flush=True)
                    print(suffix.rstrip(), flush=True)
                previous_draft = current_draft

            state = read_json(state_path)
            rendered = state_line(state)
            if rendered != previous_state_line:
                print(rendered, flush=True)
                previous_state_line = rendered

            status = str(state.get("status") or "")
            if (session / "session.json").exists() and status in TERMINAL_STATUSES:
                return 0
            time.sleep(max(0.2, args.poll_sec))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
