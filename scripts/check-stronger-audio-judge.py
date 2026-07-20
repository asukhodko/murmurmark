#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts/audit-stronger-audio-judge.py"


def load_module() -> Any:
    spec = importlib.util.spec_from_file_location("murmurmark_stronger_audio_judge", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load stronger audio judge")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def group_context(local: float, remote: float, leak: float) -> dict[str, Any]:
    return {
        "type": "group_overlap_audit",
        "classification": {"label": "probable_double_talk", "confidence": 0.75},
        "scores": {
            "local_evidence": local,
            "remote_evidence": remote,
            "audio_leak": leak,
            "text_duplicate": 35,
        },
    }


def classify(
    module: Any,
    mic_text: str,
    remote_text: str,
    me_text: str,
    *,
    local: float,
    corroborated_duplicate: bool = False,
) -> dict[str, Any]:
    item = {
        "utterances": [
            {"id": "me", "role": "me", "source_track": "mic", "text": me_text},
            {"id": "remote", "role": "remote", "source_track": "remote", "text": "Скинул мерч-квест."},
        ],
        "source_contexts": [group_context(local, 45, 40)],
    }
    transcripts = {
        "mic_clean": {"text": mic_text, "avg_logprob": -0.4, "no_speech_prob": 0.1},
        "mic_raw": {"text": mic_text, "avg_logprob": -0.4, "no_speech_prob": 0.1},
        "remote": {"text": remote_text, "avg_logprob": -0.3, "no_speech_prob": 0.05},
    }
    metrics = module.source_metrics(transcripts, me_text, "Скинул мерч-квест.")
    audit_row = None
    if corroborated_duplicate:
        audit_row = {
            "classification": {
                "label": "remote_duplicate",
                "verdict": "probable_transcript_error",
            },
            "scores": {
                "local_support": 25,
                "remote_similarity": 90,
            },
        }
    return module.classify_item(item, audit_row, transcripts, metrics)


def main() -> int:
    module = load_module()
    duplicate = classify(
        module,
        "дашборд или софи на overview скинул мерч квест вы его посмотрите",
        "Дашборд Эль Софи на овервью. Скинул мерч квест. Вы его посмотрите.",
        "дашборд лесов и на overview скинул мерч квест вы его посмотрите вольем",
        local=35,
        corroborated_duplicate=True,
    )
    assert duplicate["label"] == "confirm_remote_duplicate", duplicate
    assert duplicate["suggested_decision"] == "drop_me", duplicate

    uncorroborated = classify(
        module,
        "дашборд или софи на overview скинул мерч квест вы его посмотрите",
        "Дашборд Эль Софи на овервью. Скинул мерч квест. Вы его посмотрите.",
        "дашборд лесов и на overview скинул мерч квест вы его посмотрите вольем",
        local=35,
    )
    assert uncorroborated["suggested_decision"] != "drop_me", uncorroborated

    double_talk = classify(
        module,
        "нам нужно проверить квоты и отдельно собрать прогноз",
        "я добавил панельки на дашборд и скинул мерч квест",
        "нам нужно проверить квоты и отдельно собрать прогноз",
        local=70,
    )
    assert double_talk["label"] in {"confirm_me", "confirm_timing_or_doubletalk"}, double_talk
    assert double_talk["suggested_decision"] == "keep_me", double_talk
    print("stronger audio judge checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
