#!/usr/bin/env python3
"""Run the source-preserving full transcript shadow for the v2.7 echo candidate."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
CANDIDATE = "speaker_preserving_neural_echo_v2_7"
WHISPER_MODEL = (
    Path.home() / ".local/share/murmurmark/models/whisper.cpp/ggml-large-v3-q5_0.bin"
)


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


SHADOW = load_module(
    ROOT / "scripts/speaker_preserving_echo_full_shadow_v2_5.py",
    "murmurmark_spne_v27_shadow_base",
)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("session", type=Path)
    value.add_argument("--whisper-model", type=Path, default=WHISPER_MODEL)
    value.add_argument("--refresh", action="store_true")
    return value


def run(args: argparse.Namespace) -> dict[str, Any]:
    session = args.session.expanduser().resolve()
    candidate_root = (
        session / "derived/preprocess/speaker-preserving-neural-echo-v2-7"
    )
    candidate_audio = candidate_root / "candidate_clean_mic_pcm16.wav"
    output_root = candidate_root / "full-shadow-v2-7"
    candidate_dir = output_root / "candidates" / CANDIDATE
    baseline_audio = session / "derived/asr/mic.wav"
    remote_audio = session / "derived/preprocess/audio/remote_for_aec.wav"
    for path in (candidate_audio, baseline_audio, remote_audio):
        if not path.is_file():
            raise RuntimeError(f"required audio missing: {path}")
    SHADOW.PROMOTION.safe_link(candidate_audio, candidate_dir / "mic_for_asr.wav")
    SHADOW.PROMOTION.safe_link(baseline_audio, output_root / "canonical/mic.wav")
    SHADOW.PROMOTION.safe_link(
        remote_audio, output_root / "canonical/remote_aligned.wav"
    )
    payload = SHADOW.full_shadow_stage(
        session=session,
        output_root=output_root,
        candidate=CANDIDATE,
        candidate_mic_asr=candidate_root / "direct-asr/raw/mic.json",
        candidate_asr_report=candidate_root / "direct-asr/chunk_report.json",
        whisper_model=args.whisper_model.expanduser().resolve(),
        refresh=args.refresh,
        reuse_micro_asr_cache=True,
    )
    payload["candidate_profile"] = CANDIDATE
    payload["candidate_runtime_schema"] = (
        "murmurmark.speaker_preserving_neural_echo_runtime/v2.7"
    )
    SHADOW.PROMOTION.write_json(
        candidate_dir / "full_shadow_precomputed_report.json", payload
    )
    return payload


def main() -> int:
    args = parser().parse_args()
    payload = run(args)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if payload.get("passed") is True else 6


if __name__ == "__main__":
    raise SystemExit(main())
