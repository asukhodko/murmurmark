#!/usr/bin/env python3
"""Focused regression checks for Evidence-Backed Me Completion v2."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import tempfile
from pathlib import Path


def load_module(filename: str, name: str):
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def asr_run(text: str, logprob: float = -0.1) -> dict:
    return {"status": "ok", "text": text, "avg_logprob": logprob, "window": "normal"}


def word(text: str, start: float, end: float) -> dict:
    return {"text": text, "start": start, "end": end, "probability": 0.95}


def check_consensus(completion) -> None:
    runs = {
        "mic_raw": [asr_run("мы обсудим новый план", -0.25)],
        "mic_clean": [asr_run("мы обсудим новый план работы", -0.15)],
        "mic_role_masked": [asr_run("совсем другой текст", -0.01)],
    }
    consensus = completion.mic_consensus(runs)
    assert consensus["passed"] is True, consensus
    assert consensus["independent"] is True, consensus
    assert "mic_raw" in consensus["sources"], consensus
    assert set(consensus["sources"]) != {"mic_clean", "mic_role_masked"}, consensus


def check_boundary_dedup(completion) -> None:
    fragment = {
        "start": 1.0,
        "end": 3.0,
        "text": "что будет дальше ладно",
        "words": [
            word("что", 1.0, 1.3),
            word("будет", 1.35, 1.8),
            word("дальше", 1.85, 2.35),
            word("ладно", 2.4, 3.0),
        ],
    }
    utterances = [
        {"id": "before", "role": "me", "start": 0.0, "end": 1.0, "text": "мы знаем что"},
        {"id": "after", "role": "me", "start": 3.0, "end": 4.0, "text": "ладно идем"},
    ]
    trimmed, metadata = completion.trim_fragment_against_existing_me(fragment, utterances)
    assert trimmed is not None, metadata
    assert completion.normalize_text(trimmed["text"]) == "будет дальше", trimmed
    assert metadata["prefix_words"] == 1, metadata
    assert metadata["suffix_words"] == 1, metadata


def check_existing_coverage(completion) -> None:
    dialogue = {
        "utterances": [
            {
                "id": "me_1",
                "role": "me",
                "start": 10.0,
                "end": 12.0,
                "text": "Если их будет 50, тоже странно.",
            }
        ]
    }
    coverage = completion.existing_me_coverage(dialogue, "Если их будет 50, тоже странно", 10.0, 12.0)
    assert coverage["covered"] is True, coverage
    assert coverage["utterance_ids"] == ["me_1"], coverage


def check_text_duplicate_repair(completion) -> None:
    queue = {
        "kind": "text_fragment",
        "utterance_id": "fragment",
        "target_text": "дает сп",
        "interval": {"start": 4.0, "end": 6.0},
    }
    dialogue = {
        "utterances": [
            {
                "id": "before",
                "role": "me",
                "start": 1.0,
                "end": 4.0,
                "text": "Я не сделаю, что они не ожидают.",
            },
            {"id": "fragment", "role": "me", "start": 4.0, "end": 6.0, "text": "дает сп"},
        ]
    }
    disposition = completion.classify_text_fragment(
        queue,
        dialogue,
        {"local_only_ratio": 0.8, "double_talk_ratio": 0.0},
        {
            "label": "target_me_confirmed",
            "confidence": 0.96,
            "delta_vs_remote": 0.35,
        },
        {
            "passed": True,
            "text": "не ожидают.",
            "similarity": {"similarity": 0.95, "containment": 1.0},
        },
        {"remote": [asr_run("другая удаленная фраза")]},
    )
    assert disposition["outcome"] == "text_repaired", disposition
    assert disposition["action"] == "drop_duplicate_fragment", disposition
    assert disposition["covered_by_utterance_ids"] == ["before"], disposition


def check_remote_forbidden(completion) -> None:
    candidate = "удаленная речь коллеги"
    run = asr_run(candidate)
    run.update(
        {
            "identity": {"word_timestamps": True},
            "segments": [
                {
                    "words": [
                        word("удаленная", 5.0, 5.4),
                        word("речь", 5.45, 5.8),
                        word("коллеги", 5.85, 6.2),
                    ]
                }
            ],
        }
    )
    disposition = completion.classify_local_recall(
        {
            "kind": "local_recall",
            "interval": {"start": 5.0, "end": 6.5},
            "remote_text": candidate,
        },
        {
            "utterances": [
                {"id": "remote", "role": "colleagues", "start": 5.0, "end": 6.5, "text": candidate}
            ]
        },
        {"local_only_ratio": 0.8, "double_talk_ratio": 0.0, "remote_active_ratio": 0.5},
        {"label": "target_me_confirmed", "confidence": 0.96, "delta_vs_remote": 0.4},
        {
            "passed": True,
            "text": candidate,
            "similarity": {"similarity": 1.0, "containment": 1.0},
            "run": run,
        },
        {"remote": [asr_run(candidate)]},
        [{"state": "local_only", "start": 5.0, "end": 6.5}],
    )
    assert disposition["outcome"] != "materialized", disposition
    assert disposition["checks"]["remote_forbidden_pass"] is False, disposition


def check_review_contract(completion, review_plan) -> None:
    local = completion.review_row(
        {"session_id": "s", "queue_id": "q1", "kind": "local_recall", "interval": {}, "utterance_ids": []},
        {"disposition": {"outcome": "needs_review", "reason": "weak", "confidence": 0.2}},
    )
    assert local["kind"] == "local_recall", local
    assert local["allowed_decisions"] == ["needs_review", "skip"], local

    text = completion.review_row(
        {
            "session_id": "s",
            "queue_id": "q2",
            "kind": "text_fragment",
            "interval": {"start": 1.0, "end": 2.0},
            "utterance_ids": ["utt_1"],
            "target_text": "обрывок",
        },
        {"disposition": {"outcome": "needs_review", "reason": "weak", "confidence": 0.3}},
    )
    assert text["allowed_decisions"] == ["keep_me", "needs_review", "skip"], text
    normalized = review_plan.normalize_item(
        {
            **text,
            "session": "sessions/s",
            "source": "transcript_text",
            "me_utterance_ids": ["utt_1"],
            "text": [{"id": "utt_1", "role": "Me", "source_track": "mic", "text": "обрывок"}],
        }
    )
    assert normalized["review_lane"] == "check_transcript_text", normalized
    assert normalized["review_action"] == "check_transcript_text", normalized
    assert normalized["allowed_decisions"] == ["keep_me", "needs_review", "skip"], normalized


def check_missing_model_fail_open(completion) -> None:
    with tempfile.TemporaryDirectory(prefix="murmurmark-local-completion-") as temp_dir:
        sessions_root = Path(temp_dir) / "sessions"
        session = sessions_root / "fixture"
        out_dir = sessions_root / "_reports" / "local-speech-completion-v2"
        session.mkdir(parents=True)
        out_dir.mkdir(parents=True)
        queue = [
            {
                "queue_id": "fixture:local_recall:item_1",
                "session_id": "fixture",
                "kind": "local_recall",
                "source_audit_id": "item_1",
                "interval": {"start": 1.0, "end": 2.0, "duration_sec": 1.0},
            }
        ]
        baseline = {
            "gates": {"passed": True},
            "sessions": [{"session_id": "fixture", "input_profile": "current"}],
        }
        (out_dir / "baseline_manifest.json").write_text(json.dumps(baseline), encoding="utf-8")
        (out_dir / "completion_queue.jsonl").write_text(json.dumps(queue[0]) + "\n", encoding="utf-8")
        args = argparse.Namespace(
            sessions_root=sessions_root,
            out_dir=out_dir,
            sessions=[],
            model=Path(temp_dir) / "missing-model",
            device="cpu",
            compute_type="int8",
            language="ru",
            beam_size=1,
            padding_sec=1.25,
            allow_download=False,
            no_cache=False,
        )
        assert completion.build_evidence(args) == 0
        rows = completion.read_jsonl(
            session / "derived/audit/local-speech-completion-v2/local_speech_completion_evidence.jsonl"
        )
        assert len(rows) == 1, rows
        assert rows[0]["disposition"]["outcome"] == "needs_review", rows[0]
        summary = completion.read_json(
            session / "derived/audit/local-speech-completion-v2/local_speech_completion_evidence_summary.json"
        )
        assert summary["gates"]["fail_open"] is True, summary
        corpus = completion.read_json(out_dir / "evidence_corpus_report.json")
        assert corpus["gates"]["passed"] is False, corpus
        assert corpus["gates"]["fail_open"] is True, corpus


def main() -> int:
    completion = load_module("local-speech-completion-v2.py", "murmurmark_local_speech_completion_v2_check")
    review_plan = load_module("build-review-plan.py", "murmurmark_local_speech_completion_review_plan_check")
    check_consensus(completion)
    check_boundary_dedup(completion)
    check_existing_coverage(completion)
    check_text_duplicate_repair(completion)
    check_remote_forbidden(completion)
    check_review_contract(completion, review_plan)
    check_missing_model_fail_open(completion)
    print("local speech completion v2 checks ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
