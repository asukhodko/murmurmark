#!/usr/bin/env python3
"""Build the independent ordinary-session hard set for candidate v2.3."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent


def load_builder() -> Any:
    path = ROOT / "scripts/build-speaker-preserving-neural-echo-v2-1-hard.py"
    spec = importlib.util.spec_from_file_location("murmurmark_spne_v23_hard_builder", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load builder: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    builder = load_builder()
    builder.OUTPUT = ROOT / "sessions/_reports/speaker-preserving-neural-echo-v2-3-hard"
    builder.LOCAL_SESSION = ROOT / "sessions/2026-07-28_15-01-01"
    builder.REMOTE_SESSION = ROOT / "sessions/2026-07-30_14-01-49"
    builder.LOCAL_AUDIO = (
        builder.LOCAL_SESSION / "derived/preprocess/audio/mic_clean_local_fir.wav"
    )
    builder.LOCAL_STATE = (
        builder.LOCAL_SESSION / "derived/preprocess/echo/speaker_state.jsonl"
    )
    builder.LOCAL_DIALOGUE = (
        builder.LOCAL_SESSION
        / "derived/transcript-simple/whisper-cpp/resolved/clean_dialogue.reviewed_v1.json"
    )
    builder.REMOTE_MIC = (
        builder.REMOTE_SESSION / "derived/preprocess/audio/mic_raw_for_asr.wav"
    )
    builder.REMOTE_AUDIO = (
        builder.REMOTE_SESSION / "derived/preprocess/audio/remote_for_aec.wav"
    )
    builder.REMOTE_STATE = (
        builder.REMOTE_SESSION / "derived/preprocess/echo/speaker_state.jsonl"
    )
    builder.REMOTE_FIR_REPORT = (
        builder.REMOTE_SESSION / "derived/preprocess/echo/local_fir_report.json"
    )
    manifest = builder.build()
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
