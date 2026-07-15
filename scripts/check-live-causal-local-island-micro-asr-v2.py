#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import re
import sys
import tempfile
from pathlib import Path
from types import ModuleType
from typing import Any


def load_module(filename: str, name: str) -> ModuleType:
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class ProgressiveStub:
    REMOTE_AUDIO_QUIET_MAX_DB = -65.0

    @staticmethod
    def tokens(value: Any) -> list[str]:
        return re.findall(r"[a-zа-я0-9]+", str(value or "").lower())

    @classmethod
    def asr_text_similarity(cls, left: Any, right: Any) -> float:
        left_tokens = set(cls.tokens(left))
        right_tokens = set(cls.tokens(right))
        if not left_tokens or not right_tokens:
            return 0.0
        return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)

    @classmethod
    def bag_recall(cls, source: list[str], target: list[str]) -> float:
        if not source:
            return 0.0
        remaining = list(target)
        matched = 0
        for token in source:
            if token in remaining:
                remaining.remove(token)
                matched += 1
        return matched / len(source)

    @staticmethod
    def bag_match_count(source: list[str], target: list[str]) -> int:
        remaining = list(target)
        matched = 0
        for token in source:
            if token in remaining:
                remaining.remove(token)
                matched += 1
        return matched

    @staticmethod
    def read_asr_segments(source: dict[str, Any], _session: Path) -> list[dict[str, Any]]:
        return list(source.get("asr_segments") or [])

    @classmethod
    def remote_audio_guard(cls, features: dict[str, Any]) -> dict[str, Any]:
        remote_db = float(features.get("remote_db", 0.0))
        mic_minus_remote = float(features.get("mic_minus_remote_db", 0.0))
        return {
            "status": "passed" if remote_db <= cls.REMOTE_AUDIO_QUIET_MAX_DB or mic_minus_remote >= 20 else "rejected",
            "remote_db": remote_db,
            "mic_minus_remote_db": mic_minus_remote,
        }


def evaluation(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "chunk_index": 1,
        "segment_index": 1,
        "start": 10.0,
        "end": 12.0,
        "text": "local phrase has content",
        "classification": "causal_target_me_supported",
        "timeline_causal": True,
        "used_batch_fields_for_selection": False,
        "enrollment": {
            "mode": "past_chunks_only",
            "positive_seed_count": 3,
            "negative_seed_count": 3,
            "cutoff_sec": 9.0,
        },
        "scores": {"target": 0.92},
        "audio": {"mic_db": -28.0, "remote_db": -72.0, "mic_minus_remote_db": 44.0},
    }
    row.update(overrides)
    return row


def timeline_fixture(session: Path, *, remote_segments: list[dict[str, Any]] | None = None):
    (session / "session.json").write_text(
        json.dumps({"ended_at": "2026-07-15T07:00:30Z"}),
        encoding="utf-8",
    )
    chunk = {
        "index": 1,
        "created_at": "2026-07-15T07:00:20Z",
        "mic": {"clip_start_sec": 0.0, "clip_end_sec": 30.0},
        "remote": {
            "clip_start_sec": 0.0,
            "clip_end_sec": 30.0,
            "asr_segments": list(remote_segments or []),
        },
    }
    segments = {
        (source, 1): {
            "source": source,
            "index": 1,
            "closed": True,
            "provenance": "recording_time_committed_pcm",
        }
        for source in ("mic", "remote")
    }
    return {1: chunk}, segments


def check_selection(module: ModuleType) -> None:
    progressive = ProgressiveStub()
    with tempfile.TemporaryDirectory(prefix="murmurmark-local-island-selection-") as root:
        session = Path(root) / "session"
        session.mkdir()
        chunks, segments = timeline_fixture(session)
        selected, decisions = module.select_strict_evaluations(
            session=session,
            evaluations=[evaluation(batch_text="must not influence selection", batch_start=999.0)],
            chunks=chunks,
            segments_by_key=segments,
            existing_me=[],
            progressive=progressive,
        )
        assert len(selected) == 1, decisions
        assert decisions[0]["status"] == "selected", decisions
        assert decisions[0]["used_batch_fields_for_selection"] is False, decisions

        selected_mutated, decisions_mutated = module.select_strict_evaluations(
            session=session,
            evaluations=[evaluation(batch_text="completely different", batch_start=-500.0)],
            chunks=chunks,
            segments_by_key=segments,
            existing_me=[],
            progressive=progressive,
        )
        assert len(selected_mutated) == 1, decisions_mutated
        assert decisions_mutated[0]["checks"] == decisions[0]["checks"], decisions_mutated

        remote_chunks, remote_timeline = timeline_fixture(
            session,
            remote_segments=[{"start": 10.5, "end": 11.5, "text": "remote phrase"}],
        )
        remote_selected, remote_decisions = module.select_strict_evaluations(
            session=session,
            evaluations=[evaluation()],
            chunks=remote_chunks,
            segments_by_key=remote_timeline,
            existing_me=[],
            progressive=progressive,
        )
        assert not remote_selected, remote_decisions
        assert "remote_asr_guard_clear" in remote_decisions[0]["reasons"], remote_decisions

        loud_selected, loud_decisions = module.select_strict_evaluations(
            session=session,
            evaluations=[
                evaluation(audio={"mic_db": -5.0, "remote_db": -35.0, "mic_minus_remote_db": 30.0})
            ],
            chunks=chunks,
            segments_by_key=segments,
            existing_me=[],
            progressive=progressive,
        )
        assert not loud_selected, loud_decisions
        assert "remote_audio_strictly_quiet" in loud_decisions[0]["reasons"], loud_decisions

        short_selected, short_decisions = module.select_strict_evaluations(
            session=session,
            evaluations=[evaluation(text="да нет")],
            chunks=chunks,
            segments_by_key=segments,
            existing_me=[],
            progressive=progressive,
        )
        assert not short_selected, short_decisions
        assert "source_text_contentful" in short_decisions[0]["reasons"], short_decisions


def check_micro_asr_trimming(module: ModuleType) -> None:
    progressive = ProgressiveStub()
    with tempfile.TemporaryDirectory(prefix="murmurmark-local-island-micro-") as root:
        session = Path(root) / "session"
        session.mkdir()
        chunks, _segments = timeline_fixture(session)

        def runner(_wav: Path, _output: Path, _model: str, _language: str, _cli: str) -> dict[str, Any]:
            return {
                "status": "passed",
                "json": "fixture.json",
                "rows": [
                    {"start_sec": 0.1, "end_sec": 0.9, "text": "before", "score": 0.99},
                    {"start_sec": 1.4, "end_sec": 1.8, "text": "local", "score": 0.91},
                    {"start_sec": 2.2, "end_sec": 2.8, "text": "phrase", "score": 0.89},
                    {"start_sec": 3.7, "end_sec": 4.3, "text": "after", "score": 0.99},
                ],
            }

        materializer = module.MicroASRMaterializer(
            session=session,
            chunks=chunks,
            existing_me=[],
            progressive=progressive,
            model="fixture",
            language="ru",
            whisper_cli="fixture",
            force=True,
            runner=runner,
        )
        materializer.write_clip = lambda *args, **kwargs: True
        materializer.audio_features = lambda *args, **kwargs: {
            "mic_db": -25.0,
            "remote_db": -80.0,
            "mic_minus_remote_db": 55.0,
            "corr": 0.0,
        }
        row = evaluation()
        row["strict_selection"] = {
            "id": "selection_1",
            "recording_time_evidence": {"status": "passed"},
        }
        candidate = materializer.materialize([row], 1)
        assert candidate["status"] == "accepted", candidate
        assert candidate["text"] == "local phrase", candidate
        assert candidate["start"] == 10.0 and candidate["end"] == 12.0, candidate
        assert candidate["selected_asr_row_count"] == 2, candidate
        assert candidate["strict_remote_free_guard"]["status"] == "passed", candidate
        assert candidate["used_batch_fields_for_selection"] is False, candidate


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def accepted_candidate() -> dict[str, Any]:
    return {
        "id": "local_island_1",
        "status": "accepted",
        "chunk_index": 1,
        "start": 10.0,
        "end": 12.0,
        "text": "local phrase",
        "timeline_causal": True,
        "used_batch_fields_for_selection": False,
        "selection_mode": "recording_time_causal_local_island_v2",
        "recording_time_evidence": {"status": "passed"},
        "remote_audio_guard": {"status": "passed"},
        "strict_remote_free_guard": {"status": "passed"},
        "remote_asr_guard": {"status": "passed"},
        "promotion_allowed": False,
        "batch_authoritative": True,
    }


def check_shadow_contract_and_isolation(compare: ModuleType) -> None:
    with tempfile.TemporaryDirectory(prefix="murmurmark-local-island-shadow-") as root:
        session = Path(root) / "session"
        candidate_path = session / "derived/live/causal-local-island-micro-asr-v2/candidates.jsonl"
        write_jsonl(candidate_path, [accepted_candidate()])
        turns, rejected = compare.causal_local_island_micro_asr_v2_shadow_turns(session)
        assert len(turns) == 1 and not rejected, (turns, rejected)
        assert turns[0]["role"] == "Me", turns

        unsafe = accepted_candidate()
        unsafe.pop("strict_remote_free_guard")
        write_jsonl(candidate_path, [unsafe])
        unsafe_turns, unsafe_rejected = compare.causal_local_island_micro_asr_v2_shadow_turns(session)
        assert not unsafe_turns, unsafe_turns
        assert "strict_remote_free_guard" in unsafe_rejected[0]["failed_checks"], unsafe_rejected

        old_policy = "old_profile_fixture"
        old_dir = session / "derived/live/target-me-shadow" / old_policy
        old_dir.mkdir(parents=True)
        old_json = old_dir / "draft.json"
        old_md = old_dir / "draft.md"
        old_json.write_bytes(b"old-json-byte-stable\n")
        old_md.write_bytes(b"old-md-byte-stable\n")
        before = (old_json.read_bytes(), old_md.read_bytes())

        outputs = compare.write_target_me_shadow_drafts(
            session=session,
            live_turns_rows=[],
            suppressed_mic_asr_segments=[],
            target_me_rows=[],
            target_me_turns_by_policy={},
            persistent_target_me_rows=[],
            batch_utterances=[],
            metrics={},
            policies=(old_policy, compare.CAUSAL_LOCAL_ISLAND_MICRO_ASR_V2_PROFILE_POLICY),
            write_policies={compare.CAUSAL_LOCAL_ISLAND_MICRO_ASR_V2_PROFILE_POLICY},
        )
        assert (old_json.read_bytes(), old_md.read_bytes()) == before
        assert old_policy in outputs, outputs
        new_paths = outputs[compare.CAUSAL_LOCAL_ISLAND_MICRO_ASR_V2_PROFILE_POLICY]
        assert (session / new_paths["draft_json"]).exists(), new_paths
        assert compare.CAUSAL_LOCAL_ISLAND_MICRO_ASR_V2_PROFILE_POLICY not in (
            compare.DEFAULT_TARGET_ME_SHADOW_POLICIES
        )


def check_corpus_registration(corpus: ModuleType, compare: ModuleType) -> None:
    policy = compare.CAUSAL_LOCAL_ISLAND_MICRO_ASR_V2_PROFILE_POLICY
    assert policy in corpus.TARGET_ME_SHADOW_PROFILE_POLICIES
    assert policy == corpus.CAUSAL_LOCAL_ISLAND_MICRO_ASR_V2_PROFILE_POLICY
    for metric in (
        "causal_local_island_micro_asr_v2_added_turn_count",
        "causal_local_island_micro_asr_v2_added_turn_seconds",
        "causal_local_island_micro_asr_v2_rejected_turn_count",
    ):
        assert metric in corpus.TARGET_ME_SHADOW_PROFILE_METRICS, metric


def check_focused_corpus_agreement(reporter: ModuleType, compare: ModuleType) -> None:
    policy = compare.CAUSAL_LOCAL_ISLAND_MICRO_ASR_V2_PROFILE_POLICY
    prefix = f"real_live_target_me_shadow_profile_{policy}"
    session_reports = [
        {"metrics": {"candidate_added_turn_count": 2, "candidate_added_turn_seconds": 3.25}},
        {"metrics": {"candidate_added_turn_count": 1, "candidate_added_turn_seconds": 1.75}},
    ]
    corpus = {
        "summary": {
            f"{prefix}_evaluated_session_count": 2,
            f"{prefix}_live_missing_me_seconds": 12.5,
            f"{prefix}_live_suspected_remote_leak_in_me_seconds": 1.25,
            f"{prefix}_live_effective_blocking_contentful_role_constrained_order_mismatch_count": 0,
            f"{prefix}_causal_local_island_micro_asr_v2_added_turn_count": 3,
            f"{prefix}_causal_local_island_micro_asr_v2_added_turn_seconds": 5.0,
        },
        "live_target_me_shadow_profile_diagnostics": {
            "real": {"best_live_implementable_profile": {"policy": policy}}
        },
    }
    agreement = reporter.corpus_agreement(
        corpus,
        candidate_policy=policy,
        session_reports=session_reports,
        candidate_missing=12.5,
        candidate_remote=1.25,
        effective_order_blockers=0,
    )
    assert agreement["status"] == "passed", agreement

    corpus["summary"][f"{prefix}_live_missing_me_seconds"] = 12.75
    mismatch = reporter.corpus_agreement(
        corpus,
        candidate_policy=policy,
        session_reports=session_reports,
        candidate_missing=12.5,
        candidate_remote=1.25,
        effective_order_blockers=0,
    )
    assert mismatch["status"] == "failed", mismatch
    assert mismatch["checks"]["missing_me_seconds"] is False, mismatch


def main() -> int:
    local_island = load_module(
        "live-causal-local-island-micro-asr.py",
        "murmurmark_check_live_causal_local_island_micro_asr_v2",
    )
    compare = load_module("compare-live-batch.py", "murmurmark_check_compare_live_batch_v2")
    corpus = load_module("report-live-corpus-gates.py", "murmurmark_check_live_corpus_v2")
    reporter = load_module(
        "report-live-causal-local-island-micro-asr-v2.py",
        "murmurmark_check_live_causal_local_island_micro_asr_v2_report",
    )
    check_selection(local_island)
    check_micro_asr_trimming(local_island)
    check_shadow_contract_and_isolation(compare)
    check_corpus_registration(corpus, compare)
    check_focused_corpus_agreement(reporter, compare)
    print("live causal local-island micro-ASR v2 checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
