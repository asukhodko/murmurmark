#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def load(name: str, module_name: str) -> ModuleType:
    path = Path(__file__).with_name(name)
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def candidate_row(
    text: str,
    *,
    start: float = 10.0,
    end: float = 13.0,
    overlaps: list[dict[str, object]] | None = None,
    nearest: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "row_kind": "offline_candidate",
        "interval": {"start": start, "end": end, "duration_sec": end - start},
        "causal_selection": {"text": text},
        "evaluation_reference": {
            "use": "evaluation_only",
            "overlapping_utterances": overlaps or [],
            "nearest_utterances": nearest or [],
        },
    }


def main() -> int:
    reporter = load(
        "report-causal-recovery-generalization-v1.py",
        "murmurmark_generalization_unit_reporter",
    )
    compare = load("compare-live-batch.py", "murmurmark_generalization_unit_compare")
    builder = load(
        "build-causal-recovery-generalization-corpus.py",
        "murmurmark_generalization_unit_builder",
    )

    genuine = candidate_row(
        "Надо проверить алерты",
        overlaps=[
            {
                "id": "me-1",
                "role": "Me",
                "start": 10.0,
                "end": 13.0,
                "text": "Надо проверить алерты",
                "overlap_sec": 3.0,
            },
            {
                "id": "remote-1",
                "role": "Colleagues",
                "start": 10.0,
                "end": 13.0,
                "text": "Мы закончили задачу",
                "overlap_sec": 3.0,
            },
        ],
    )
    assert reporter.classify_row(genuine, compare)["classification"] == "genuine_double_talk"

    remote = candidate_row(
        "Мы закончили задачу",
        overlaps=[
            {
                "id": "remote-2",
                "role": "Colleagues",
                "start": 10.0,
                "end": 13.0,
                "text": "Мы закончили задачу",
                "overlap_sec": 3.0,
            }
        ],
    )
    assert reporter.classify_row(remote, compare)["classification"] == "probable_remote_leak"

    noise = candidate_row("Да", start=10.0, end=11.0)
    assert reporter.classify_row(noise, compare)["classification"] == "probable_asr_noise"

    timing = candidate_row(
        "Проверим завтра",
        start=10.0,
        end=13.0,
        nearest=[
            {
                "id": "me-2",
                "role": "Me",
                "start": 7.0,
                "end": 9.8,
                "text": "Проверим завтра",
            }
        ],
    )
    assert reporter.classify_row(timing, compare)["classification"] == "probable_timing_overlap"

    reference = builder.evaluation_reference(
        [
            {
                "id": "batch-1",
                "start": 1.0,
                "end": 2.0,
                "role": "Me",
                "text": "Привет",
                "needs_review": False,
            }
        ],
        1.2,
        1.8,
    )
    assert reference["use"] == "evaluation_only"
    assert reference["overlapping_utterances"][0]["overlap_sec"] == 0.6
    assert builder.canonical_sha256(reference) == builder.canonical_sha256(dict(reference))
    print("causal recovery generalization unit checks ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
