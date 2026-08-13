#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import tempfile
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
    speaker_state: dict[str, Any] | None = None,
    item_remote_text: str = "Скинул мерч-квест.",
    mic_raw_text: str | None = None,
    mic_no_speech_prob: float = 0.1,
    audit_local_support: float | None = None,
    audit_remote_similarity: float | None = None,
    group_remote: float = 45,
    group_leak: float = 40,
) -> dict[str, Any]:
    item = {
        "utterances": [
            {"id": "me", "role": "me", "source_track": "mic", "text": me_text},
            {"id": "remote", "role": "remote", "source_track": "remote", "text": item_remote_text},
        ],
        "source_contexts": [group_context(local, group_remote, group_leak)],
    }
    transcripts = {
        "mic_clean": {"text": mic_text, "avg_logprob": -0.4, "no_speech_prob": mic_no_speech_prob},
        "mic_raw": {"text": mic_raw_text or mic_text, "avg_logprob": -0.4, "no_speech_prob": mic_no_speech_prob},
        "remote": {"text": remote_text, "avg_logprob": -0.3, "no_speech_prob": 0.05},
    }
    metrics = module.source_metrics(transcripts, me_text, item_remote_text)
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
    elif audit_local_support is not None or audit_remote_similarity is not None:
        audit_row = {
            "classification": {
                "label": "likely_reliable",
                "verdict": "likely_reliable",
            },
            "scores": {
                "local_support": audit_local_support or 0,
                "remote_similarity": audit_remote_similarity or 0,
            },
        }
    return module.classify_item(item, audit_row, transcripts, metrics, speaker_state)


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

    short_adjacent_duplicate = classify(
        module,
        "Да, посмотрим, что там может быть.",
        "Почему разблокировали? Твоя задача там написана, в ней прямо что-то.",
        "воя задача там",
        local=20,
        speaker_state={
            "available": True,
            "coverage_ratio": 1.0,
            "remote_only_ratio": 0.4,
            "local_active_ratio": 0.0,
            "double_talk_ratio": 0.0,
            "silence_ratio": 0.6,
        },
        item_remote_text="",
    )
    assert short_adjacent_duplicate["label"] == "confirm_remote_duplicate", short_adjacent_duplicate
    assert short_adjacent_duplicate["suggested_decision"] == "drop_me", short_adjacent_duplicate
    assert short_adjacent_duplicate["scores"]["decoded_remote_phrase_match"] >= 0.88

    double_talk = classify(
        module,
        "нам нужно проверить квоты и отдельно собрать прогноз",
        "я добавил панельки на дашборд и скинул мерч квест",
        "нам нужно проверить квоты и отдельно собрать прогноз",
        local=70,
    )
    assert double_talk["label"] in {"confirm_me", "confirm_timing_or_doubletalk"}, double_talk
    assert double_talk["suggested_decision"] == "keep_me", double_talk

    remote_only_false_keep = classify(
        module,
        "Вот он, предыдущий голос. Росбанк, да. Давайте, если вас...",
        "Ну, вон там ребята еще делаются. Росбанк слетел. Росбанк, да? Давайте, если вы хотите, мы можем сделать это.",
        "Давайте если вас это",
        local=40,
        speaker_state={
            "available": True,
            "coverage_ratio": 1.0,
            "remote_only_ratio": 1.0,
            "local_active_ratio": 0.0,
            "double_talk_ratio": 0.0,
        },
        item_remote_text="",
        mic_raw_text="Ну, вон там ребята еще, Росбанк слетел. Росбанк, да? Давайте, если вас...",
    )
    assert remote_only_false_keep["label"] == "uncertain", remote_only_false_keep
    assert remote_only_false_keep["suggested_decision"] == "needs_review", remote_only_false_keep
    assert remote_only_false_keep["scores"]["speaker_state_remote_only_keep_veto"] is True

    remote_only_short_duplicate = classify(
        module,
        "короче у меня отсюда",
        "Вау. Ну, короче, у меня особо здесь нечего добавить.",
        "короче у меня",
        local=0,
        speaker_state={
            "available": True,
            "coverage_ratio": 1.0,
            "remote_only_ratio": 1.0,
            "local_active_ratio": 0.0,
            "double_talk_ratio": 0.0,
        },
        item_remote_text="",
        mic_no_speech_prob=0.69,
        audit_local_support=70,
        audit_remote_similarity=85,
    )
    assert remote_only_short_duplicate["label"] == "confirm_remote_duplicate", remote_only_short_duplicate
    assert remote_only_short_duplicate["suggested_decision"] == "drop_me", remote_only_short_duplicate
    assert remote_only_short_duplicate["scores"]["remote_only_decoded_duplicate"] is True

    remote_only_single_token_duplicate = classify(
        module,
        "Все нормально. Все, и дальше он двигается, делает. Вот эти, эти.",
        "Все, и дальше он двигается, делает. Ну, кстати, идея отличная.",
        "кстати",
        local=0,
        speaker_state={
            "available": True,
            "coverage_ratio": 1.0,
            "remote_only_ratio": 1.0,
            "local_active_ratio": 0.0,
            "double_talk_ratio": 0.0,
        },
        item_remote_text="",
        mic_no_speech_prob=0.55,
        audit_local_support=0,
        audit_remote_similarity=45,
    )
    assert remote_only_single_token_duplicate["label"] == "confirm_remote_duplicate", remote_only_single_token_duplicate
    assert remote_only_single_token_duplicate["suggested_decision"] == "drop_me", remote_only_single_token_duplicate
    assert remote_only_single_token_duplicate["scores"]["single_token_remote_only_duplicate"] is True

    remote_dominant_false_keep = classify(
        module,
        "Это уже будет деталь имплементации, которая особо корректор знать не будет.",
        "это уже будет как бы детали имплементации которые особо коллектор знать не будет",
        "которая как бы особо коллекторзной будет",
        local=55,
        group_remote=65,
        group_leak=85,
        speaker_state={
            "available": True,
            "coverage_ratio": 1.0,
            "remote_only_ratio": 0.0,
            "local_active_ratio": 0.0,
            "double_talk_ratio": 0.0,
        },
        item_remote_text=(
            "То, что это сырье берется из DLH, это уже будет деталь имплементации, "
            "про которую особо коллектор знать не будет."
        ),
        audit_local_support=0,
        audit_remote_similarity=45,
    )
    assert remote_dominant_false_keep["label"] == "uncertain", remote_dominant_false_keep
    assert remote_dominant_false_keep["suggested_decision"] == "needs_review", remote_dominant_false_keep
    assert remote_dominant_false_keep["scores"]["remote_dominant_mic_decode_keep_veto"] is True

    remote_dominant_without_group_row = classify(
        module,
        "Это уже будет деталь имплементации, которая особо корректор знать не будет.",
        "это уже будет как бы детали имплементации которые особо коллектор знать не будет",
        "которая как бы особо коллекторзной будет",
        local=0,
        group_remote=0,
        group_leak=0,
        speaker_state={
            "available": True,
            "coverage_ratio": 1.0,
            "remote_only_ratio": 0.0,
            "local_active_ratio": 0.0,
            "double_talk_ratio": 0.0,
        },
        item_remote_text="",
        mic_raw_text="это уже будет как бы детали имплементации которые особо коллектор знать не будет",
        audit_local_support=0,
        audit_remote_similarity=45,
    )
    assert remote_dominant_without_group_row["label"] == "uncertain", remote_dominant_without_group_row
    assert remote_dominant_without_group_row["suggested_decision"] == "needs_review", remote_dominant_without_group_row

    state = module.interval_speaker_state_evidence(
        [
            {"start": 10.0, "end": 12.0, "state": "remote_only_level"},
            {"start": 12.0, "end": 14.0, "state": "remote_only"},
        ],
        {"start": 10.5, "end": 13.5},
    )
    assert state["coverage_ratio"] == 1.0, state
    assert state["remote_only_ratio"] == 1.0, state
    assert state["local_active_ratio"] == 0.0, state

    with tempfile.TemporaryDirectory(prefix="murmurmark-stronger-checkpoint-") as raw_root:
        out_dir = Path(raw_root)
        checkpoint_items = [{"id": "arp_000002"}, {"id": "arp_000001"}]
        checkpoint_rows = {
            "arp_000001": {"source_pack_item_id": "arp_000001", "classification": {"label": "confirm_me"}},
        }
        module.write_incremental_checkpoint(
            out_dir,
            checkpoint_items,
            checkpoint_rows,
            model_path=Path("model"),
            pack_summary={},
            selected_items=2,
            cached_items=0,
            computed_items=1,
            pending_items=1,
            sources=("mic_clean", "remote"),
        )
        persisted = [
            json.loads(line)
            for line in (out_dir / "faster_whisper_judge.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert [row["source_pack_item_id"] for row in persisted] == ["arp_000001"], persisted
        checkpoint_summary = json.loads(
            (out_dir / "faster_whisper_judge_summary.json").read_text(encoding="utf-8")
        )
        assert checkpoint_summary["status"] == "in_progress", checkpoint_summary
        assert checkpoint_summary["computed_items"] == 1, checkpoint_summary
        assert checkpoint_summary["pending_selected_items_after_cap"] == 1, checkpoint_summary
        assert not list(out_dir.glob(".*.tmp")), list(out_dir.iterdir())
        assert module.final_run_status(0, [], []) == "completed"
        assert module.final_run_status(1, [], []) == "completed_partial"
        assert module.final_run_status(0, ["missing"], []) == "completed_partial"
        assert module.final_run_status(0, [], ["missing.json"]) == "completed_partial"

        cached_item = {
            "id": "arp_cached",
            "session_id": "fixture",
            "profile": "reviewed_v1",
            "interval": {"start": 1.0, "end": 2.0, "duration_sec": 1.0},
            "utterance_ids": ["utt_cached"],
            "utterances": [
                {
                    "id": "utt_cached",
                    "role": "Me",
                    "source_track": "mic",
                    "start": 1.0,
                    "end": 2.0,
                    "text": "Проверка",
                }
            ],
            "review_features": {},
        }
        cached_row = {
            "source_pack_item_id": "arp_cached",
            "source_pack_item_fingerprint": module.item_fingerprint(cached_item),
            "session_id": "fixture",
            "profile": "reviewed_v1",
            "sources": ["mic_clean", "remote"],
            "interval": cached_item["interval"],
            "utterance_ids": ["utt_cached"],
            "utterances": cached_item["utterances"],
            "transcripts": {
                "mic_clean": {"text": "Проверка", "segments": [], "no_speech_prob": 0.1},
                "remote": {"text": "", "segments": [], "no_speech_prob": 0.9},
            },
        }
        module.write_jsonl(out_dir / "faster_whisper_judge.jsonl", [cached_row])
        preserved = module.refreshed_valid_existing_rows_by_pack_id(
            out_dir,
            [cached_item],
            ("mic_clean", "remote"),
            {},
            [],
        )
        assert set(preserved) == {"arp_cached"}, preserved
        assert preserved["arp_cached"]["classification_policy_version"] == module.SCRIPT_VERSION

    remote_contained_me = classify(
        module,
        "вот они прям понимают если колонка красная горит",
        "как с этим работать то есть они прям понимали если колонка красная горит прикол",
        "то есть вот они прям понимали если колонка красная",
        local=70,
    )
    assert remote_contained_me["label"] == "uncertain", remote_contained_me
    assert remote_contained_me["suggested_decision"] == "needs_review", remote_contained_me
    print("stronger audio judge checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
