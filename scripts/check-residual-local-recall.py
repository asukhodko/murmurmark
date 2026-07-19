#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def load_module():
    path = Path(__file__).with_name("residual-local-recall.py")
    spec = importlib.util.spec_from_file_location("murmurmark_check_residual_local", path)
    if not spec or not spec.loader:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


MODULE = load_module()


def utterance(row_id: str, role: str, start: float, end: float, text: str) -> dict:
    return {"id": row_id, "role": role, "start": start, "end": end, "text": text}


def queue(text: str = "Проверю локальные логи") -> dict:
    return {
        "schema": "murmurmark.residual_local_recall_queue_item/v1",
        "session_id": "fixture",
        "residual_queue_id": "queue_1",
        "source": "local_recall",
        "source_audit_id": "local_1",
        "interval": {"start": 10.0, "end": 14.0, "duration_sec": 4.0},
        "target_text": text,
    }


def run(text: str, words: list[tuple[float, float, str]]) -> dict:
    return {
        "status": "ok",
        "text": text,
        "avg_logprob": -0.2,
        "identity": {"word_timestamps": True, "core_start": 0.4, "model": {"path": "fixture"}},
        "segments": [
            {
                "words": [{"start": start, "end": end, "text": word} for start, end, word in words],
            }
        ],
    }


def prior(
    *,
    mic_text: str = "Проверю локальные логи",
    remote_text: str = "Другой ответ",
    label: str = "target_me_confirmed",
    local: float = 0.9,
    remote: float = 0.0,
    remote_only: float = 0.0,
    with_words: bool = True,
) -> dict:
    words = [(0.5, 1.0, "Проверю"), (1.0, 1.5, "локальные"), (1.5, 2.0, "логи")] if with_words else []
    return {
        "target_text": mic_text,
        "target_me": {
            "label": label,
            "confidence": 0.96 if label == "target_me_confirmed" else 0.65,
            "delta_vs_remote": 0.35 if label == "target_me_confirmed" else 0.02,
        },
        "speaker_state": {
            "local_only_ratio": local,
            "remote_active_ratio": remote,
            "remote_only_ratio": remote_only,
        },
        "micro_asr": {
            "mic_clean": run(mic_text, words),
            "mic_raw": run(mic_text, words),
            "remote": run(remote_text, [(0.5, 1.0, value) for value in remote_text.split()]),
        },
        "disposition": {
            "checks": {
                "remote_to_target": MODULE.similarity(remote_text, mic_text),
                "mic_to_remote": MODULE.similarity(mic_text, remote_text),
            }
        },
    }


def main() -> int:
    empty_dialogue = {"utterances": [utterance("remote", "remote", 20.0, 21.0, "Другой ответ")]}
    missing = MODULE.resolve_row(queue(), prior(), empty_dialogue)
    assert missing["outcome"] == "confirmed_missing_me"
    assert missing["action"] == "insert_me"
    assert missing["insertion"]["start"] >= 10.0 and missing["insertion"]["end"] <= 14.0

    covered_dialogue = {"utterances": [utterance("me_1", "me", 15.0, 17.0, "Проверю локальные логи")]}
    covered = MODULE.resolve_row(queue(), prior(), covered_dialogue)
    assert covered["outcome"] == "already_covered"
    assert covered["action"] == "close_without_change"

    paraphrase_dialogue = {
        "utterances": [utterance("me_2", "me", 10.0, 12.0, "Я проверю локальные логи отдельно")]
    }
    paraphrase = MODULE.resolve_row(
        queue("Проверю локальные логи и отдельно посмотрю"),
        prior(mic_text="проверю локальные логи"),
        paraphrase_dialogue,
    )
    assert paraphrase["outcome"] in {"already_covered", "duplicate_or_paraphrase"}

    remote_text = "Проверю локальные логи"
    remote_supported = MODULE.resolve_row(
        queue(remote_text),
        prior(
            mic_text=remote_text,
            remote_text=remote_text,
            label="target_me_ambiguous",
            local=0.0,
            remote=1.0,
            remote_only=1.0,
        ),
        empty_dialogue,
    )
    assert remote_supported["outcome"] == "remote_supported"
    assert remote_supported["action"] == "close_without_change"

    mixed = MODULE.resolve_row(
        queue(),
        prior(local=0.5, remote=0.7, remote_text="Проверю часть ответа"),
        empty_dialogue,
    )
    assert mixed["outcome"] == "mixed_or_double_talk"
    assert mixed["action"] == "needs_review"

    weak = MODULE.resolve_row(queue(), prior(label="target_me_ambiguous"), empty_dialogue)
    assert weak["outcome"] == "insufficient_local_evidence"

    missing_artifact = MODULE.resolve_row(queue(), {}, empty_dialogue)
    assert missing_artifact["outcome"] == "needs_review"
    assert missing_artifact["reason"] == "required_evidence_missing"

    invalid_timestamps = MODULE.resolve_row(queue(), prior(with_words=False), empty_dialogue)
    assert invalid_timestamps["action"] == "needs_review"

    first = json.dumps(missing, ensure_ascii=False, sort_keys=True)
    second = json.dumps(MODULE.resolve_row(queue(), prior(), empty_dialogue), ensure_ascii=False, sort_keys=True)
    assert first == second
    print("residual local recall checks ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
