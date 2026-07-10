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
        assert turns[0].get("used_batch_fields_for_selection") is False, turns[0]
        assert compare.target_me_shadow_profile_is_live_implementable(
            compare.RUNTIME_CAUSAL_TARGET_ME_MICRO_ASR_PROFILE_POLICY
        ) is False
        assert compare.target_me_shadow_profile_is_live_implementable(
            compare.RUNTIME_CAUSAL_TARGET_ME_DIRECT_PROFILE_POLICY
        ) is True
        assert compare.selected_lab_policies(
            SimpleNamespace(with_labs=False, lab_policy=[])
        ) == (
            compare.RUNTIME_CAUSAL_TARGET_ME_BASELINE_PROFILE_POLICY,
            compare.RUNTIME_CAUSAL_TARGET_ME_DIRECT_PROFILE_POLICY,
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
                        "causal_pre_stop_accepted_candidate_count": 1,
                    },
                }
            ]
        )
        assert no_regression.get("status") == "safe_shadow_candidate", no_regression
        assert (no_regression.get("delta") or {}).get("missing_me_seconds") == -2.0, no_regression

    print("live progressive Target-Me checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
