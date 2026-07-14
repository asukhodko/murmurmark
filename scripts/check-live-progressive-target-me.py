#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from scipy.io import wavfile


def load_module():
    path = Path(__file__).with_name("live-progressive-target-me.py")
    spec = importlib.util.spec_from_file_location("murmurmark_live_progressive_target_me_test", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_compare_module():
    path = Path(__file__).with_name("compare-live-batch.py")
    spec = importlib.util.spec_from_file_location("murmurmark_live_progressive_target_me_compare_test", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_live_module():
    path = Path(__file__).with_name("live-pipeline-shadow.py")
    spec = importlib.util.spec_from_file_location("murmurmark_live_progressive_target_me_worker_test", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_corpus_module():
    path = Path(__file__).with_name("report-live-corpus-gates.py")
    spec = importlib.util.spec_from_file_location("murmurmark_live_progressive_target_me_corpus_test", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeBackend:
    method = "fake_dvector_v1"

    def embed(self, path: Path):
        if "seed_negative" in str(path):
            vector = np.asarray([0.0, 1.0], dtype=np.float64)
        else:
            vector = np.asarray([1.0, 0.0], dtype=np.float64)
        return vector, {"backend": self.method, "path": str(path)}


def fake_micro_runner(wav: Path, output_base: Path, model: str, language: str, whisper_cli: str):
    del wav, output_base, model, language, whisper_cli
    return {
        "status": "passed",
        "text": "recovered local action phrase",
        "score": 0.92,
        "rows": [
            {
                "start_sec": 2.1,
                "end_sec": 3.4,
                "text": "recovered local action phrase",
                "score": 0.92,
            }
        ],
    }


def write_asr(path: Path, text: str, duration_sec: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "transcription": [
                    {
                        "offsets": {"from": 0, "to": int(round(duration_sec * 1000))},
                        "text": text,
                        "tokens": [
                            {"text": token, "p": 0.95}
                            for token in text.split()
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


def make_chunk(session: Path, index: int, *, candidate: bool) -> dict:
    rate = 16_000
    duration = 2.0
    start = float((index - 1) * 10)
    timeline = np.arange(int(rate * duration), dtype=np.float32) / rate
    mic_audio = 0.2 * np.sin(2 * np.pi * 220 * timeline)
    remote_audio = 0.2 * np.sin(2 * np.pi * 440 * timeline)
    chunk_dir = session / "derived/live/chunks" / f"{index:06d}"
    chunk_dir.mkdir(parents=True, exist_ok=True)
    mic_wav = chunk_dir / "mic.wav"
    remote_wav = chunk_dir / "remote.wav"
    wavfile.write(mic_wav, rate, mic_audio)
    wavfile.write(remote_wav, rate, remote_audio)
    mic_json = chunk_dir / "mic.json"
    remote_json = chunk_dir / "remote.json"
    mic_text = "local action phrase" if candidate else f"local seed phrase {index}"
    remote_text = f"совещание коллег продолжается {index}"
    write_asr(mic_json, mic_text, duration)
    write_asr(remote_json, remote_text, 0.5 if candidate else duration)
    status = "suppressed" if candidate else "passed"
    return {
        "schema": "murmurmark.live_chunk/v1",
        "created_at": f"2026-01-01T00:00:{index:02d}+00:00",
        "index": index,
        "start_sec": start,
        "end_sec": start + duration,
        "mic": {
            "wav": str(mic_wav),
            "asr_wav": str(mic_wav),
            "clip_start_sec": start,
            "hard_start_sec": start,
            "hard_end_sec": start + duration,
            "asr": {"json": str(mic_json)},
            "live_role_gate": {"status": status},
            "live_segment_role_gate": {
                "kept_segments": []
                if candidate
                else [{"start": start, "end": start + duration, "text": mic_text}],
                "suppressed_segments": [
                    {"start": start, "end": start + duration, "text": mic_text}
                ]
                if candidate
                else [],
            },
        },
        "remote": {
            "wav": str(remote_wav),
            "clip_start_sec": start,
            "hard_start_sec": start,
            "hard_end_sec": start + duration,
            "asr": {"json": str(remote_json)},
        },
    }


def main() -> int:
    module = load_module()
    compare = load_compare_module()
    corpus = load_corpus_module()
    live = load_live_module()
    assert module.REMOTE_AUDIO_QUIET_MAX_DB == compare.REMOTE_AUDIO_QUIET_MAX_DB
    assert module.MIC_REMOTE_DOMINANCE_MIN_DB == compare.MIC_REMOTE_DOMINANCE_MIN_DB
    assert module.remote_audio_guard(
        {"mic_db": -35.0, "remote_db": -70.0, "mic_minus_remote_db": 35.0, "corr": 0.01}
    ).get("status") == "passed"
    assert module.remote_audio_guard(
        {"mic_db": -35.0, "remote_db": -30.0, "mic_minus_remote_db": -5.0, "corr": 0.01}
    ).get("status") == "rejected"
    assert compare.runtime_remote_audio_guard(
        {"mic_db": -30.0, "remote_db": -40.0, "mic_minus_remote_db": 10.0, "corr": 0.01}
    ).get("status") == "rejected"
    with tempfile.TemporaryDirectory(prefix="murmurmark-live-preview-") as temp:
        output_dir = Path(temp)
        preview_chunks = [
            {
                "index": 1,
                "start_sec": 0.0,
                "end_sec": 10.0,
                "mic": {
                    "text": "ordinary local phrase",
                    "causal_target_me_shadow": {
                        "status": "candidate",
                        "candidates": [
                            {
                                "text": "safe recovered phrase",
                                "start": 1.0,
                                "end": 2.0,
                                "remote_audio_guard": {
                                    "schema": "murmurmark.live_remote_audio_guard/v1",
                                    "status": "passed",
                                },
                            },
                            {
                                "text": "unsafe remote-like phrase",
                                "start": 3.0,
                                "end": 4.0,
                                "remote_audio_guard": {
                                    "schema": "murmurmark.live_remote_audio_guard/v1",
                                    "status": "rejected",
                                },
                            },
                        ],
                    },
                },
                "remote": {"text": "colleague phrase"},
            }
        ]
        live.write_live_views(output_dir, preview_chunks, 0.0)
        diagnostic = (output_dir / "transcript.draft.md").read_text(encoding="utf-8")
        preview = (output_dir / "transcript.preview.md").read_text(encoding="utf-8")
        assert "safe recovered phrase" in diagnostic and "unsafe remote-like phrase" in diagnostic
        assert "safe recovered phrase" in preview
        assert "unsafe remote-like phrase" not in preview
        summary = live.causal_target_me_summary(preview_chunks)
        assert summary.get("preview_candidate_count") == 1, summary
        assert summary.get("preview_rejected_count") == 1, summary
    with tempfile.TemporaryDirectory(prefix="murmurmark-live-preview-provenance-") as temp:
        session = Path(temp)
        (session / "derived/live").mkdir(parents=True)
        (session / "session.json").write_text(
            json.dumps(
                {
                    "created_at": "2026-07-10T10:00:00Z",
                    "ended_at": "2026-07-10T10:10:00Z",
                }
            ),
            encoding="utf-8",
        )
        snapshot_path = session / "derived/live/preview_snapshots.jsonl"
        snapshot = {
            "schema": "murmurmark.live_preview_snapshot/v1",
            "created_at": "2026-07-10T10:01:00Z",
            "chunk_count": 1,
            "provenance": "post_stop_raw_commit_recovery",
        }
        snapshot_path.write_text(json.dumps(snapshot) + "\n", encoding="utf-8")
        chunks = [{"created_at": "2026-07-10T10:00:30Z"}]
        temporal = compare.live_temporal_provenance(session, chunks)
        assert temporal.get("live_pre_stop_preview_snapshot_count") == 0, temporal
        assert temporal.get("live_invalid_provenance_preview_snapshot_count") == 1, temporal
        snapshot["provenance"] = "recording_time_committed_pcm"
        snapshot_path.write_text(json.dumps(snapshot) + "\n", encoding="utf-8")
        temporal = compare.live_temporal_provenance(session, chunks)
        assert temporal.get("live_pre_stop_preview_snapshot_count") == 1, temporal
        assert temporal.get("live_invalid_provenance_preview_snapshot_count") == 0, temporal
    within_budget = live.causal_target_me_lag_decision(
        captured_sec=90.0,
        chunk_end_sec=30.0,
        max_live_lag_sec=60.0,
    )
    assert within_budget.get("run") is True, within_budget
    over_budget = live.causal_target_me_lag_decision(
        captured_sec=90.1,
        chunk_end_sec=30.0,
        max_live_lag_sec=60.0,
    )
    assert over_budget.get("run") is False, over_budget
    assert over_budget.get("reason") == "live_lag_budget_exceeded", over_budget
    unbounded = live.causal_target_me_lag_decision(
        captured_sec=300.0,
        chunk_end_sec=30.0,
        max_live_lag_sec=0.0,
    )
    assert unbounded.get("run") is True, unbounded
    trimmed, trim = module.trim_remote_context_prefix(
        "Вроде включить значит как у нас июль до сентября",
        "Вроде включительно.",
    )
    assert trimmed == "значит как у нас июль до сентября", (trimmed, trim)
    assert trim.get("status") == "applied", trim
    untrimmed, no_trim = module.trim_remote_context_prefix(
        "Да, я не спорю, просто хотел уточнить",
        "Да, я поняла, спасибо",
    )
    assert untrimmed == "Да, я не спорю, просто хотел уточнить", (untrimmed, no_trim)
    assert module.bag_match_count(
        module.tokens("Вроде включительно"),
        module.tokens("значит как у нас июль до включительно сентябрь"),
    ) == 1
    with tempfile.TemporaryDirectory(prefix="murmurmark-live-progressive-target-me-") as temporary:
        runtime_session = Path(temporary) / "sessions/runtime-gate"
        runtime_live = runtime_session / "derived/live"
        runtime_live.mkdir(parents=True, exist_ok=True)
        (runtime_live / "segments.jsonl").write_text(
            json.dumps({"source": "mic", "index": 1, "end_sec": 120.0}) + "\n"
            + json.dumps({"source": "remote", "index": 1, "end_sec": 120.0}) + "\n",
            encoding="utf-8",
        )
        (runtime_live / "live_pipeline_state.json").write_text(
            json.dumps(
                {
                    "status": "completed",
                    "current_stage": "completed",
                    "termination_reason": "finalization_wait_timeout",
                }
            ),
            encoding="utf-8",
        )
        runtime_gates = compare.live_runtime_gates(
            session=runtime_session,
            live_report={"status": "completed", "progress": {"live_lag_sec": 0.0}},
            chunks=[{"index": 1, "end_sec": 30.0}],
        )
        runtime_by_name = {row.get("name"): row for row in runtime_gates}
        assert runtime_by_name["worker_terminal_state"].get("status") == "passed", runtime_gates
        assert (
            runtime_by_name["worker_terminal_state"].get("evidence") or {}
        ).get("effective_worker_status") == "completed_partial_draft", runtime_gates
        assert runtime_by_name["bounded_live_lag"].get("status") == "warning", runtime_gates
        assert (runtime_by_name["bounded_live_lag"].get("evidence") or {}).get("final_live_lag_sec") == 90.0

        session = Path(temporary) / "sessions/fixture"
        session.mkdir(parents=True, exist_ok=True)
        (session / "session.json").write_text(
            json.dumps(
                {
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "ended_at": "2999-01-01T00:00:00+00:00",
                }
            ),
            encoding="utf-8",
        )
        manager = module.ProgressiveTargetMeShadow(
            session,
            model="unused.bin",
            language="ru",
            whisper_cli="unused",
            backend=FakeBackend(),
            micro_runner=fake_micro_runner,
        )
        chunks = [make_chunk(session, index, candidate=index == 4) for index in range(1, 6)]
        for chunk in chunks:
            manager.process_chunk(chunk)
        manager.persist(status="completed")

        assert len(manager.positive_seeds) == 4, len(manager.positive_seeds)
        assert len(manager.negative_seeds) == 4, len(manager.negative_seeds)
        accepted = [row for row in manager.candidates if row.get("status") == "accepted"]
        assert len(accepted) == 1, manager.candidates
        candidate = accepted[0]
        assert candidate.get("chunk_index") == 4, candidate
        assert candidate.get("text") == "recovered local action phrase", candidate
        assert candidate.get("used_batch_fields_for_selection") is False, candidate
        assert candidate.get("timeline_causal") is True, candidate
        assert (candidate.get("remote_audio_guard") or {}).get("status") == "rejected", candidate
        localization = candidate.get("remote_free_localization") or {}
        assert localization.get("status") == "localized", localization
        assert localization.get("reason") == "past_target_voice_in_remote_free_gap", localization
        assert candidate.get("start") > candidate.get("source_start"), candidate
        enrollment = candidate.get("enrollment") or {}
        assert enrollment.get("positive_seed_count") == 3, enrollment
        assert enrollment.get("negative_seed_count") == 3, enrollment
        state = json.loads((session / "derived/live/causal-target-me/state.json").read_text(encoding="utf-8"))
        assert state.get("accepted_candidate_count") == 1, state
        assert state.get("promotion_allowed") is False, state
        assert state.get("batch_authoritative") is True, state
        provenance = compare.live_temporal_provenance(session, chunks)
        assert provenance.get("live_pre_stop_chunk_count") == len(chunks), provenance
        assert provenance.get("causal_pre_stop_accepted_candidate_count") == 1, provenance
        assert provenance.get("status") == "pre_stop_live_chunks_and_causal_candidates", provenance
        with (session / "derived/live/causal-target-me/candidates.jsonl").open(
            "a", encoding="utf-8"
        ) as stream:
            stream.write(
                json.dumps(
                    {
                        **candidate,
                        "id": "live_runtime_causal_target_me_sliding_rejected",
                        "remote_free_localization": {
                            "status": "localized",
                            "reason": "past_target_voice_sliding_window",
                            "remote_intervals": [[0.0, 1.0]],
                        },
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
            )
        turns, rejected = compare.runtime_causal_target_me_shadow_turns(session)
        assert len(rejected) == 1, rejected
        assert rejected[0].get("reason") == "runtime_candidate_not_remote_free", rejected
        assert len(turns) == 1, turns
        assert turns[0].get("runtime_causal_target_me_micro_asr_shadow") is True, turns[0]
        energy_turns, energy_rejected = compare.runtime_causal_target_me_shadow_turns(
            session,
            require_remote_audio_guard=True,
        )
        assert not energy_turns, energy_turns
        assert any(
            row.get("reason") == "runtime_remote_audio_guard_failed"
            for row in energy_rejected
        ), energy_rejected
        assert turns[0].get("used_batch_fields_for_selection") is False, turns[0]
        speaker_turns, speaker_rejected = compare.runtime_causal_target_me_shadow_turns(
            session,
            allow_speaker_confirmed_overlap=True,
        )
        assert len(speaker_rejected) == 0, speaker_rejected
        assert len(speaker_turns) == 2, speaker_turns
        assert speaker_turns[1].get("runtime_causal_target_me_speaker_overlap_shadow") is True
        assert speaker_turns[1].get("candidate_source") == "runtime-causal-target-me-speaker-overlap"
        _, _, speaker_only_turns, _, speaker_only_rejected = compare.target_me_shadow_profile_components(
            session=session,
            policy=compare.BASELINE_LIVE_BOUNDARY_SPLIT_RETIME_SPEAKER_ONLY_PROFILE_POLICY,
            live_turns_rows=[],
            suppressed_mic_asr_segments=[
                {
                    "chunk_index": 99,
                    "start": 100.0,
                    "end": 103.0,
                    "text": "unrelated suppressed mic candidate",
                    "rescue_policy_candidates": ["audio_safe_union_v1"],
                }
            ],
            target_me_rows=[],
            target_me_turns_by_policy={},
            persistent_target_me_rows=[],
            batch_utterances=[],
        )
        assert len(speaker_only_turns) == 1, speaker_only_turns
        assert speaker_only_turns[0].get("runtime_causal_target_me_speaker_overlap_shadow") is True
        assert any(
            row.get("reason") == "runtime_candidate_not_speaker_confirmed"
            for row in speaker_only_rejected
        ), speaker_only_rejected
        assert compare.target_me_shadow_profile_is_live_implementable(
            compare.RUNTIME_CAUSAL_TARGET_ME_MICRO_ASR_PROFILE_POLICY
        ) is False
        assert compare.target_me_shadow_profile_is_live_implementable(
            compare.RUNTIME_CAUSAL_TARGET_ME_DIRECT_PROFILE_POLICY
        ) is True
        assert compare.target_me_shadow_profile_is_live_implementable(
            compare.RUNTIME_CAUSAL_TARGET_ME_REMOTE_ENERGY_PROFILE_POLICY
        ) is True
        assert compare.target_me_shadow_profile_is_live_implementable(
            compare.RUNTIME_CAUSAL_TARGET_ME_SPEAKER_OVERLAP_PROFILE_POLICY
        ) is True
        assert compare.target_me_shadow_profile_is_live_implementable(
            compare.BASELINE_LIVE_BOUNDARY_SPLIT_RETIME_SPEAKER_ONLY_PROFILE_POLICY
        ) is True
        assert compare.selected_lab_policies(
            SimpleNamespace(with_labs=False, lab_policy=[])
        ) == (
            compare.RUNTIME_CAUSAL_TARGET_ME_BASELINE_PROFILE_POLICY,
            compare.BASELINE_LIVE_BOUNDARY_SPLIT_RETIME_PROFILE_POLICY,
            compare.BASELINE_LIVE_BOUNDARY_SPLIT_RETIME_SPEAKER_ONLY_PROFILE_POLICY,
            compare.RUNTIME_CAUSAL_TARGET_ME_DIRECT_PROFILE_POLICY,
            compare.RUNTIME_CAUSAL_TARGET_ME_REMOTE_ENERGY_PROFILE_POLICY,
            compare.RUNTIME_CAUSAL_TARGET_ME_SPEAKER_OVERLAP_PROFILE_POLICY,
        )

        overlap = compare.bag_overlap_metrics(
            ["один", "два", "три"],
            ["один", "два", "четыре", "пять"],
        )
        assert overlap.get("matched_dialogue_token_count") == 2, overlap
        assert overlap.get("live_token_precision_against_batch") == 0.666667, overlap
        assert overlap.get("batch_token_recall_in_live") == 0.5, overlap
        assert overlap.get("live_batch_token_f1") == 0.571429, overlap

        baseline = corpus.RUNTIME_CAUSAL_TARGET_ME_BASELINE_PROFILE_POLICY
        runtime = corpus.RUNTIME_CAUSAL_TARGET_ME_DIRECT_PROFILE_POLICY
        baseline_prefix = f"live_target_me_shadow_profile_{baseline}_"
        runtime_prefix = f"live_target_me_shadow_profile_{runtime}_"
        no_regression = corpus.runtime_profile_no_regression(
            [
                {
                    "session": "fixture",
                    "metrics": {
                        f"{baseline_prefix}non_passing_gate_count": 1,
                        f"{baseline_prefix}live_dialogue_token_count": 10,
                        f"{baseline_prefix}batch_dialogue_token_count": 10,
                        f"{baseline_prefix}matched_dialogue_token_count": 8,
                        f"{baseline_prefix}live_batch_token_f1": 0.8,
                        f"{baseline_prefix}live_missing_me_seconds": 10.0,
                        f"{baseline_prefix}live_suspected_remote_leak_in_me_seconds": 1.0,
                        f"{baseline_prefix}live_blocking_contentful_role_constrained_order_mismatch_count": 0,
                        f"{baseline_prefix}live_advisory_contentful_role_constrained_order_mismatch_count": 1,
                        f"{runtime_prefix}non_passing_gate_count": 1,
                        f"{runtime_prefix}live_dialogue_token_count": 11,
                        f"{runtime_prefix}batch_dialogue_token_count": 10,
                        f"{runtime_prefix}matched_dialogue_token_count": 9,
                        f"{runtime_prefix}live_batch_token_f1": 0.857143,
                        f"{runtime_prefix}live_missing_me_seconds": 8.0,
                        f"{runtime_prefix}live_suspected_remote_leak_in_me_seconds": 1.0,
                        f"{runtime_prefix}live_blocking_contentful_role_constrained_order_mismatch_count": 0,
                        f"{runtime_prefix}live_advisory_contentful_role_constrained_order_mismatch_count": 1,
                        f"{runtime_prefix}causal_pre_stop_direct_profile_candidate_count": 1,
                    },
                }
            ]
        )
        assert no_regression.get("status") == "safe_shadow_candidate", no_regression
        assert (no_regression.get("delta") or {}).get("missing_me_seconds") == -2.0, no_regression

        speaker_policy = corpus.RUNTIME_CAUSAL_TARGET_ME_SPEAKER_OVERLAP_PROFILE_POLICY
        speaker_prefix = f"live_target_me_shadow_profile_{speaker_policy}_"
        speaker_no_regression = corpus.runtime_profile_no_regression(
            [
                {
                    "session": "fixture",
                    "metrics": {
                        f"{runtime_prefix}non_passing_gate_count": 1,
                        f"{runtime_prefix}live_dialogue_token_count": 11,
                        f"{runtime_prefix}batch_dialogue_token_count": 10,
                        f"{runtime_prefix}matched_dialogue_token_count": 9,
                        f"{runtime_prefix}live_batch_token_f1": 0.857143,
                        f"{runtime_prefix}live_missing_me_seconds": 8.0,
                        f"{runtime_prefix}live_suspected_remote_leak_in_me_seconds": 1.0,
                        f"{runtime_prefix}live_blocking_contentful_role_constrained_order_mismatch_count": 0,
                        f"{runtime_prefix}live_advisory_contentful_role_constrained_order_mismatch_count": 1,
                        f"{speaker_prefix}non_passing_gate_count": 1,
                        f"{speaker_prefix}live_dialogue_token_count": 12,
                        f"{speaker_prefix}batch_dialogue_token_count": 10,
                        f"{speaker_prefix}matched_dialogue_token_count": 10,
                        f"{speaker_prefix}live_batch_token_f1": 0.909091,
                        f"{speaker_prefix}live_missing_me_seconds": 6.0,
                        f"{speaker_prefix}live_suspected_remote_leak_in_me_seconds": 1.0,
                        f"{speaker_prefix}live_blocking_contentful_role_constrained_order_mismatch_count": 0,
                        f"{speaker_prefix}live_advisory_contentful_role_constrained_order_mismatch_count": 1,
                        f"{speaker_prefix}causal_pre_stop_speaker_overlap_profile_candidate_count": 1,
                    },
                }
            ],
            policy=speaker_policy,
            baseline_policy=runtime,
            pre_stop_metric="causal_pre_stop_speaker_overlap_profile_candidate_count",
        )
        assert speaker_no_regression.get("status") == "safe_shadow_candidate", speaker_no_regression
        assert (speaker_no_regression.get("delta") or {}).get("missing_me_seconds") == -2.0

    print("live progressive Target-Me checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
