#!/usr/bin/env python3
"""Run the unchanged v2.15 audio selector in an isolated v2.17 output root."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parent.parent
OUTPUT_NAME = "speaker-preserving-neural-echo-v2-17"


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


BASE = load_module(
    ROOT / "scripts/speaker-preserving-neural-echo-v2-15-audio.py",
    "murmurmark_spne_v217_audio_base",
)
V210 = BASE.V210
me_guard_dialogue_path: Callable[[Path], Path] = BASE.me_guard_dialogue_path


def run_session(args: argparse.Namespace) -> dict[str, Any]:
    original_output_name = BASE.OUTPUT_NAME
    original_guard = BASE.me_guard_dialogue_path
    BASE.OUTPUT_NAME = OUTPUT_NAME
    BASE.me_guard_dialogue_path = me_guard_dialogue_path
    try:
        payload = BASE.run_session(args)
    finally:
        BASE.OUTPUT_NAME = original_output_name
        BASE.me_guard_dialogue_path = original_guard
    payload["requalification_audio_adapter"] = {
        "schema": "murmurmark.speaker_preserving_neural_echo_audio_adapter/v2.17",
        "algorithm_revision": "speaker_preserving_neural_echo_v2_15",
        "output_profile": OUTPUT_NAME,
        "threshold_changes": 0,
    }
    V210.write_json(
        args.session.expanduser().resolve()
        / "derived/preprocess"
        / OUTPUT_NAME
        / "runtime_report.json",
        payload,
    )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--whisper-model", type=Path, required=True)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    args.output = ROOT / "sessions/_reports/speaker-preserving-neural-echo-v2-17"
    args.excluded_window_ids = []
    payload = run_session(args)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if payload.get("status") in {"candidate", "fallback"} else 6


if __name__ == "__main__":
    raise SystemExit(main())
