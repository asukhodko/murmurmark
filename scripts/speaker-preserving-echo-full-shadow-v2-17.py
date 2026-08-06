#!/usr/bin/env python3
"""Run the unchanged v2.15 full shadow with current ASR in an isolated root."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
CANDIDATE = "speaker_preserving_neural_echo_v2_17"
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
    ROOT / "scripts/speaker-preserving-echo-full-shadow-v2-15.py",
    "murmurmark_spne_v217_shadow_base",
)


def run(args: argparse.Namespace) -> dict[str, Any]:
    session = args.session.expanduser().resolve()
    # Prevent the inherited runtime from importing pre-v2.17 micro-ASR cache.
    # The directory is otherwise populated normally by full_shadow_stage().
    stage = (
        session
        / "derived/preprocess"
        / OUTPUT_NAME
        / "full-shadow-v2-15/candidates"
        / CANDIDATE
        / "full-shadow-precomputed-session"
    )
    stage.mkdir(parents=True, exist_ok=True)
    original_output = BASE.OUTPUT_NAME
    original_candidate = BASE.CANDIDATE
    original_file = BASE.__file__
    BASE.OUTPUT_NAME = OUTPUT_NAME
    BASE.CANDIDATE = CANDIDATE
    BASE.__file__ = str(Path(__file__).resolve())
    try:
        payload = BASE.run(args)
    finally:
        BASE.OUTPUT_NAME = original_output
        BASE.CANDIDATE = original_candidate
        BASE.__file__ = original_file
    payload["schema"] = "murmurmark.echo_suppression_full_shadow/v2.17"
    payload["candidate_profile"] = CANDIDATE
    payload["candidate_runtime_schema"] = (
        "murmurmark.speaker_preserving_neural_echo_runtime/v2.17"
    )
    report = (
        stage.parent
        / "full_shadow_precomputed_report.json"
    )
    BASE.COMMON.PROMOTION.write_json(report, payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path)
    parser.add_argument("--whisper-model", type=Path, required=True)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    payload = run(args)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if payload.get("passed") is True else 6


if __name__ == "__main__":
    raise SystemExit(main())
