#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def load_module():
    path = Path(__file__).with_name("audit-local-recall.py")
    spec = importlib.util.spec_from_file_location("murmurmark_audit_local_recall", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def candidate(
    candidate_id: str,
    start: float,
    end: float,
    text: str,
    *,
    target: float = 0.22,
    remote_text: str = "",
    localization_reason: str = "no_remote_overlap",
) -> dict[str, object]:
    localization: dict[str, object] = {
        "status": "not_needed" if localization_reason == "no_remote_overlap" else "localized",
        "reason": localization_reason,
    }
    if localization_reason == "past_target_voice_sliding_window":
        localization["selected"] = {"mean_target_score": target}
    return {
        "id": candidate_id,
        "status": "accepted",
        "timeline_causal": True,
        "used_batch_fields_for_selection": False,
        "start": start,
        "end": end,
        "text": text,
        "score": 0.91,
        "remote_similarity": 0.08,
        "remote_text_recall_in_micro": 0.0,
        "source_text_token_recall": 0.72,
        "micro_text_token_recall_in_source": 0.81,
        "remote_text": remote_text,
        "speaker_scores": {"positive": 0.88, "negative": 0.66, "target": target},
        "remote_free_localization": localization,
    }


def main() -> int:
    module = load_module()
    assert module.token_containment("Global своя", "Global. своя...") == 1.0
    utterances = [
        {
            "id": "remote_1",
            "role": "remote",
            "start": 20.0,
            "end": 24.0,
            "text": "Нужно проверить настройки кластера.",
        },
        {
            "id": "me_1",
            "role": "me",
            "start": 30.0,
            "end": 34.0,
            "text": "Я добавлю задачу про алерты.",
        },
        {
            "id": "me_split_1",
            "role": "me",
            "start": 70.0,
            "end": 72.0,
            "text": "Нужно проверить лимиты.",
        },
        {
            "id": "me_split_2",
            "role": "me",
            "start": 72.0,
            "end": 74.0,
            "text": "Очереди добавить алерт.",
        },
        {
            "id": "me_split_3",
            "role": "me",
            "start": 74.0,
            "end": 76.0,
            "text": "Настроить дашборд оповещения.",
        },
    ]
    candidates = [
        candidate(
            "missing_me",
            10.0,
            13.0,
            "Я предлагаю отдельно проверить лимиты очереди.",
        ),
        candidate(
            "remote_duplicate",
            20.2,
            23.8,
            "Нужно проверить настройки кластера.",
        ),
        candidate(
            "already_present",
            30.1,
            33.9,
            "Я добавлю задачу про алерты.",
        ),
        candidate(
            "weak_speaker",
            40.0,
            43.0,
            "Нужно обсудить политику хранения.",
            target=0.04,
        ),
        candidate(
            "speaker_overlap",
            50.0,
            52.0,
            "Давайте затюним наши классификаторы.",
            target=0.20,
            localization_reason="past_target_voice_sliding_window",
        ),
        candidate(
            "hallucination",
            60.0,
            63.0,
            "Спасибо за просмотр",
        ),
        candidate(
            "already_present_split",
            70.0,
            76.0,
            "Нужно проверить лимиты очереди добавить алерт настроить дашборд оповещения.",
        ),
    ]

    items, rejected = module.independent_live_me_items(candidates, utterances, [])
    assert [row["parent_candidate_id"] for row in items] == ["missing_me", "speaker_overlap"], items
    assert all(row["label"] == "possible_lost_me" for row in items), items
    assert all(row["evidence_source"] == "live_causal_target_me" for row in items), items
    assert rejected["candidate_matches_authoritative_remote"] == 1, rejected
    assert rejected["candidate_already_present_in_me"] == 2, rejected
    assert rejected["target_speaker_evidence_too_weak"] == 1, rejected
    assert rejected["known_hallucination"] == 1, rejected

    timeline_item = {
        "label": "likely_harmless_short",
        "duration_sec": 0.2,
        "evidence_source": "timeline_repair",
    }
    summary = module.summarize(
        {"metrics": {"local_only_island_count": 1, "local_only_island_recovered_count": 0}},
        [{}],
        [timeline_item, *items],
        rejected,
    )
    assert summary["audited_missing_island_count"] == 1, summary
    assert summary["audit_count_matches_timeline_metrics"] is True, summary
    assert summary["independent_live_me_evidence_count"] == 2, summary
    assert summary["blocking_low_local_recall"] is True, summary
    print("independent Me evidence checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
