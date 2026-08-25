#!/usr/bin/env python3
"""Run the interactive review UI for the frozen human lexical seed."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

try:
    import readline as _readline
except ImportError:  # pragma: no cover - MurmurMark review runs on macOS.
    _readline = None


ROOT = Path(__file__).resolve().parents[1]
CORE_SCRIPT = ROOT / "scripts/build-human-reviewed-lexical-seed-v1.py"
REVIEW_COMMANDS = {
    "/r": "replay",
    "/к": "replay",
    "/i": "inaudible",
    "/ш": "inaudible",
    "/m": "mixed",
    "/ь": "mixed",
    "/x": "unusable",
    "/ч": "unusable",
    "/q": "quit",
    "/й": "quit",
}


def load_core() -> Any:
    spec = importlib.util.spec_from_file_location("murmurmark_human_lexical_seed_core", CORE_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot_load_human_lexical_seed_core")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CORE = load_core()


def configure_input_history() -> None:
    if _readline is None:
        return
    _readline.clear_history()
    if hasattr(_readline, "set_auto_history"):
        _readline.set_auto_history(True)


def parse_review_input(value: str) -> tuple[str, str | None]:
    stripped = value.strip()
    command = REVIEW_COMMANDS.get(stripped.casefold())
    if command is not None:
        return command, None
    if stripped.startswith("/"):
        return "invalid_command", stripped
    if not CORE.LEXICAL.normalize_text(stripped):
        return "invalid_text", None
    return "exact_text", stripped


def read_review_input(prompt: str) -> str:
    return input(prompt)


def review(policy_path: Path, policy: dict[str, Any], sessions_root: Path, out: Path) -> int:
    configure_input_history()
    print("review controls: /r replay (/к in Russian layout); Up Arrow repeats the previous input")
    try:
        while True:
            bundle = CORE.load_bundle(policy_path, policy, sessions_root, out)
            slot = CORE.next_unanswered(bundle)
            if slot is None:
                print("review complete")
                return CORE.evaluate(policy_path, policy, sessions_root, out, None, write_outputs=True)
            print()
            CORE.print_slot(slot, bundle)
            CORE.play(CORE.queue_clip(slot, bundle["workspace"]))
            while True:
                value = read_review_input(
                    "exact text [/r replay, /i inaudible, /m mixed, /x unusable, /q quit]: "
                )
                outcome, text = parse_review_input(value)
                if outcome == "quit":
                    print("review stopped; progress saved")
                    return 0
                if outcome == "replay":
                    CORE.play(CORE.queue_clip(slot, bundle["workspace"]))
                    continue
                if outcome == "invalid_command":
                    print(f"unknown command: {text}; use /r, /i, /m, /x or /q")
                    continue
                if outcome == "invalid_text":
                    print("invalid answer: enter the exact words or a review command")
                    continue
                try:
                    CORE.save_answer(out, bundle, slot["slot_id"], outcome, text)
                except CORE.SeedError as error:
                    print(f"invalid answer: {error}")
                    continue
                break
    except (EOFError, KeyboardInterrupt):
        print("\nreview stopped; progress saved")
        return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("action", choices=["review"])
    result.add_argument("--policy", type=Path, default=CORE.DEFAULT_POLICY)
    result.add_argument("--out-dir", type=Path, default=CORE.DEFAULT_OUT)
    result.add_argument("--sessions-root", type=Path, default=ROOT / "sessions")
    return result


def main() -> int:
    args = parser().parse_args()
    policy_path = args.policy.expanduser().resolve()
    out = args.out_dir.expanduser().resolve()
    sessions_root = args.sessions_root.expanduser().resolve()
    policy = CORE.load_policy(policy_path)
    return review(policy_path, policy, sessions_root, out)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError, CORE.SeedError, subprocess.SubprocessError) as error:
        print(f"lexical_seed_v1: error={error}", file=sys.stderr)
        raise SystemExit(2)
