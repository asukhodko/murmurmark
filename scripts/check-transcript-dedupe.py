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


def load_local_recall_repair_module():
    script = Path(__file__).with_name("apply-local-recall-repair.py")
    spec = importlib.util.spec_from_file_location("murmurmark_local_recall_repair", script)
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
    local_recall_repair = load_local_recall_repair_module()

    assert module.KNOWN_HALLUCINATION_RE.search("Субтитры подогнал «Симон»")
    assert not module.KNOWN_HALLUCINATION_RE.search("Обсудили подготовку субтитров к докладу")

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

    output, corrections = module.suppress_adjacent_same_speaker_duplicates(
        [
            row(
                "short_repair",
                "я потому что сам как-то очень по-разному раньше слушал музыку",
                118.0,
                125.0,
            ),
            row(
                "full_repair",
                "что сам както очень поразному раньше слушал музыку во время работы и заметил результат",
                118.0,
                130.0,
            ),
        ]
    )
    assert len(output) == 1, output
    assert corrections[-1]["reason"] == "near_same_speaker_fuzzy_overlap_drop", corrections

    output, corrections = module.suppress_adjacent_same_speaker_duplicates(
        [
            row(
                "overlap_variant_short",
                "То есть понятно, что это неправильно и так далее, но это не является каким-то нарушением правил правительства.",
                1074.71,
                1084.72,
            ),
            row(
                "overlap_variant_long",
                "Понятно, что это неправильно и так далее, но это не является каким-то нарушением правил поведения и так далее.",
                1075.22,
                1085.82,
            ),
        ]
    )
    assert len(output) == 1, output
    assert corrections[-1]["fuzzy_match"]["near_synchronous_asr_variant"] is True, corrections

    output, _ = module.suppress_adjacent_same_speaker_duplicates(
        [
            row("enable", "Нужно включить сервис после проверки", 20.0, 25.0),
            row("disable", "Нужно выключить сервис после аварии", 21.0, 26.0),
        ]
    )
    assert len(output) == 2, output

    existing = {
        "id": "existing_tail",
        "speaker_label": "Me",
        "source_track": "mic",
        "text": "Мы этого особо не заметили.",
        "start": 2692.0,
        "end": 2694.75,
    }
    duplicate = local_recall_repair.existing_overlap(
        [existing],
        {"start_sec": 2690.822, "end_sec": 2694.942, "duration_sec": 4.12},
        "Падет, чтобы упал АИКС, и мы этого особо не заметили, нам нужна",
    )
    assert duplicate is existing
    distinct = local_recall_repair.existing_overlap(
        [existing],
        {"start_sec": 2690.822, "end_sec": 2694.942, "duration_sec": 4.12},
        "Нужно проверить алерты после выкладки",
    )
    assert distinct is None

    print("transcript dedupe checks ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
