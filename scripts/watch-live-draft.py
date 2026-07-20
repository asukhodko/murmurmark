#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
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
HEADING_RE = re.compile(r"(?m)^##\s+(.+?)\s*$")
PROVISIONAL_SUFFIX_RE = re.compile(r"\s+provisional\s*$", re.IGNORECASE)
STATE_HEARTBEAT_INTERVAL_SEC = 30.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Watch a MurmurMark shadow live draft and worker heartbeat.")
    parser.add_argument("session", type=Path)
    parser.add_argument("--poll-sec", type=float, default=1.0)
    parser.add_argument(
        "--diagnostic-draft",
        action="store_true",
        help="Watch transcript.draft.md with all candidate-only evidence instead of the conservative preview.",
    )
    parser.add_argument(
        "--embedded",
        action="store_true",
        help="Use concise output when the watcher is embedded in `murmurmark record`.",
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


def normalize_heading(value: str) -> str:
    return PROVISIONAL_SUFFIX_RE.sub("", value.strip())


def draft_blocks(text: str) -> tuple[str, list[dict[str, str]]]:
    matches = list(HEADING_RE.finditer(text))
    if not matches:
        return text.strip(), []
    blocks: list[dict[str, str]] = []
    occurrences: dict[str, int] = {}
    for index, match in enumerate(matches):
        title = match.group(1).strip()
        normalized = normalize_heading(title)
        occurrences[normalized] = occurrences.get(normalized, 0) + 1
        key = f"{normalized}#{occurrences[normalized]}"
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        markdown = text[match.start() : end].strip()
        canonical = HEADING_RE.sub(lambda heading: f"## {normalize_heading(heading.group(1))}", markdown, count=1)
        blocks.append(
            {
                "key": key,
                "title": normalized,
                "markdown": markdown,
                "canonical": canonical,
            }
        )
    return text[: matches[0].start()].strip(), blocks


def draft_delta(previous: str, current: str) -> str:
    if not previous:
        return current
    if current.startswith(previous):
        return current[len(previous) :]

    _, previous_blocks = draft_blocks(previous)
    _, current_blocks = draft_blocks(current)
    if not current_blocks:
        return changed_suffix(previous, current)

    previous_by_key = {block["key"]: block for block in previous_blocks}
    current_keys = {block["key"] for block in current_blocks}
    output: list[str] = []
    if not previous_blocks:
        output.append("[live] transcript started")
    for block in current_blocks:
        previous_block = previous_by_key.get(block["key"])
        if previous_block is None:
            output.append(block["markdown"])
        elif previous_block["canonical"] != block["canonical"]:
            output.append(f"[live] revised {block['title']}\n\n{block['markdown']}")

    removed = len(set(previous_by_key) - current_keys)
    if removed:
        output.append(f"[live] {removed} previously shown block(s) no longer present")
    return "\n\n".join(output)


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


def state_signature(state: dict[str, Any]) -> tuple[Any, ...]:
    progress = state.get("progress") if isinstance(state.get("progress"), dict) else {}
    lag = progress.get("live_lag_sec")
    return (
        str(state.get("status") or "waiting"),
        str(state.get("current_stage") or "unknown"),
        state.get("current_index"),
        round(float(lag), 1) if isinstance(lag, (int, float)) else None,
    )


def start_line(draft_path: Path, embedded: bool) -> str:
    if embedded:
        return "[live] inline preview started; batch remains authoritative"
    return f"watching: {draft_path}"


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
    previous_state_signature: tuple[Any, ...] | None = None
    last_state_printed_at = 0.0
    print(start_line(draft_path, args.embedded), flush=True)
    try:
        while True:
            desired_path = diagnostic_path if args.diagnostic_draft else preview_path
            if desired_path.exists() and desired_path != draft_path:
                draft_path = desired_path
                previous_draft = ""
                print(f"\n[live] switched to: {draft_path}\n", flush=True)
            current_draft = read_text(draft_path)
            if current_draft != previous_draft:
                delta = draft_delta(previous_draft, current_draft)
                if delta.strip():
                    print(delta.rstrip(), flush=True)
                previous_draft = current_draft

            state = read_json(state_path)
            signature = state_signature(state)
            now = time.monotonic()
            if signature != previous_state_signature or now - last_state_printed_at >= STATE_HEARTBEAT_INTERVAL_SEC:
                print(state_line(state), flush=True)
                previous_state_signature = signature
                last_state_printed_at = now

            status = str(state.get("status") or "")
            if (session / "session.json").exists() and status in TERMINAL_STATUSES:
                return 0
            time.sleep(max(0.2, args.poll_sec))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
