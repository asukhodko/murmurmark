#!/usr/bin/env python3
"""Focused regression checks for conservative same-speaker transcript dedupe."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def load_transcribe_module():
    script = Path(__file__).with_name("transcribe-simple-whispercpp.py")
    spec = importlib.util.spec_from_file_location("murmurmark_transcribe_simple", script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {script}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def row(identifier: str, text: str, start: float, end: float, confidence: float = 0.8) -> dict:
    return {
        "id": identifier,
        "speaker_label": "Me",
        "corrected_text": text,
        "start": start,
        "end": end,
        "quality": {"role_confidence": confidence},
    }


def main() -> int:
    module = load_transcribe_module()

    output, corrections = module.suppress_adjacent_same_speaker_duplicates(
        [
            row("first", "ручки в этом клиенте", 58.31, 62.0, 1.0),
            row("duplicate", "Ручки в этом клиенте.", 58.31, 62.0, 0.78),
        ]
    )
    assert len(output) == 1
    assert corrections[0]["reason"] == "exact_overlapping_same_speaker_duplicate_drop"
    assert corrections[0]["dropped_utterance_id"] == "duplicate"

    output, _ = module.suppress_adjacent_same_speaker_duplicates(
        [
            row("first", "повторим важную мысль", 10.0, 12.0),
            row("later", "повторим важную мысль", 40.0, 42.0),
        ]
    )
    assert len(output) == 2

    output, _ = module.suppress_adjacent_same_speaker_duplicates(
        [row("first", "Да", 10.0, 10.8), row("second", "Да", 10.1, 10.9)]
    )
    assert len(output) == 2

    print("transcript dedupe checks ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
