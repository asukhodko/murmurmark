#!/usr/bin/env python3
"""Materialize isolated v2.11 audio with the frozen v2.10 exact-local guard."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parent.parent
PARENT_POLICY = ROOT / "policies/speaker-preserving-neural-echo-v2-10-audio.json"
OUTPUT_NAME = "speaker-preserving-neural-echo-v2-11"


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


PARENT = load_module(
    ROOT / "scripts/speaker-preserving-neural-echo-v2-10-audio.py",
    "murmurmark_spne_v211_audio_parent",
)
me_guard_dialogue_path: Callable[[Path], Path] = PARENT.me_guard_dialogue_path
V27 = PARENT.V27


def seed_parent_cache(session: Path, output: Path) -> dict[str, int]:
    source = session / "derived/preprocess/speaker-preserving-neural-echo-v2-10"
    copied_files = 0
    for name in (
        "proposal_manifest.json",
        "proposed_windows.jsonl",
        "rejected_windows.jsonl",
    ):
        source_file = source / name
        destination = output / name
        if source_file.is_file() and not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_file, destination)
            copied_files += 1
    copied_entries = 0
    source_cache = source / "diagnostic-asr-cache"
    destination_cache = output / "diagnostic-asr-cache"
    if source_cache.is_dir():
        destination_cache.mkdir(parents=True, exist_ok=True)
        for entry in source_cache.iterdir():
            destination = destination_cache / entry.name
            if not entry.is_dir() or destination.exists():
                continue
            try:
                shutil.copytree(entry, destination, copy_function=os.link)
            except OSError:
                shutil.rmtree(destination, ignore_errors=True)
                shutil.copytree(entry, destination, copy_function=shutil.copy2)
            copied_entries += 1
    return {"proposal_files": copied_files, "diagnostic_entries": copied_entries}


def run_session(args: argparse.Namespace) -> dict[str, Any]:
    session = args.session.expanduser().resolve()
    output = session / "derived/preprocess" / OUTPUT_NAME
    seeded = seed_parent_cache(session, output) if not args.refresh else {}
    original_output_name = PARENT.OUTPUT_NAME
    original_guard = PARENT.me_guard_dialogue_path
    PARENT.OUTPUT_NAME = OUTPUT_NAME
    PARENT.me_guard_dialogue_path = me_guard_dialogue_path
    try:
        payload = PARENT.run_session(args)
    finally:
        PARENT.OUTPUT_NAME = original_output_name
        PARENT.me_guard_dialogue_path = original_guard
    payload["schema"] = "murmurmark.speaker_preserving_neural_echo_runtime/v2.11"
    payload["selection_contract"] = (
        "v2_10_exact_local_audio_with_v2_11_outcome_rollback"
    )
    payload["parent_audio_runtime_sha256"] = PARENT.sha256(
        ROOT / "scripts/speaker-preserving-neural-echo-v2-10-audio.py"
    )
    payload["v2_10_cache_seed"] = seeded
    if payload.get("status") in {"candidate", "fallback"}:
        PARENT.write_json(
            output / "runtime_report.json",
            payload,
        )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path)
    parser.add_argument("--policy", type=Path, default=PARENT_POLICY)
    parser.add_argument("--whisper-model", type=Path, required=True)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--exclude-chunk", action="append", type=int, default=[])
    args = parser.parse_args()
    args.output = ROOT / "sessions/_reports/speaker-preserving-neural-echo-v2-11"
    args.excluded_chunks = args.exclude_chunk
    payload = run_session(args)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if payload.get("status") in {"candidate", "fallback"} else 6


if __name__ == "__main__":
    raise SystemExit(main())
