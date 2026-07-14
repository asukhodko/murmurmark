#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path

import numpy as np
from scipy.io import wavfile


def load_compare_module():
    path = Path(__file__).with_name("compare-live-batch.py")
    spec = importlib.util.spec_from_file_location("murmurmark_compare_live_batch_retime_test", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_micro_lab_module():
    path = Path(__file__).with_name("report-live-boundary-island-micro-asr-lab.py")
    spec = importlib.util.spec_from_file_location("murmurmark_live_micro_lab_test", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def tone(sample_rate: int, duration_sec: float, start_sec: float, end_sec: float, amplitude: float) -> np.ndarray:
    count = int(round(sample_rate * duration_sec))
    values = np.zeros(count, dtype=np.float32)
    start = int(round(sample_rate * start_sec))
    end = min(count, int(round(sample_rate * end_sec)))
    timeline = np.arange(max(0, end - start), dtype=np.float32) / sample_rate
    values[start:end] = amplitude * np.sin(2.0 * np.pi * 220.0 * timeline)
    return values


def write_track(path: Path, values: np.ndarray, sample_rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    wavfile.write(path, sample_rate, values)


def main() -> int:
    compare = load_compare_module()
    assert (
        compare.BASELINE_LIVE_BOUNDARY_SPLIT_RETIME_PROFILE_POLICY
        in compare.DEFAULT_TARGET_ME_SHADOW_POLICIES
    )
    assert (
        compare.BASELINE_LIVE_BOUNDARY_SPLIT_RETIME_SPEAKER_ONLY_PROFILE_POLICY
        in compare.DEFAULT_TARGET_ME_SHADOW_POLICIES
    )
    micro_lab = load_micro_lab_module()
    sample_rate = 16_000
    with tempfile.TemporaryDirectory(prefix="murmurmark-live-retime-") as temporary:
        session = Path(temporary) / "sessions/fixture-session"
        session.mkdir(parents=True)
        chunks_dir = session / "derived/live/chunks"

        remote_delayed = tone(sample_rate, 10.0, 4.0, 8.0, 0.20)
        write_track(chunks_dir / "000001/remote.wav", remote_delayed, sample_rate)
        write_track(chunks_dir / "000001/mic.live_echo_guard.wav", np.zeros_like(remote_delayed), sample_rate)

        remote_before_local = tone(sample_rate, 10.0, 0.4, 3.0, 0.20)
        local_after_remote = tone(sample_rate, 10.0, 5.0, 8.0, 0.20)
        mic_with_bleed = remote_before_local * 0.01 + local_after_remote
        write_track(chunks_dir / "000002/remote.wav", remote_before_local, sample_rate)
        write_track(chunks_dir / "000002/mic.live_echo_guard.wav", mic_with_bleed, sample_rate)

        token_json = chunks_dir / "000003/remote.json"
        token_json.parent.mkdir(parents=True, exist_ok=True)
        token_json.write_text(
            json.dumps(
                {
                    "transcription": [
                        {
                            "tokens": [
                                {"text": " low", "p": 0.1, "offsets": {"from": 0, "to": 4000}},
                                *[
                                    {
                                        "text": f" word{index}",
                                        "p": 0.9,
                                        "offsets": {"from": 8000 + index * 200, "to": 8150 + index * 200},
                                    }
                                    for index in range(5)
                                ],
                            ]
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        mic_gap_json = chunks_dir / "000004/mic.json"
        mic_gap_json.parent.mkdir(parents=True, exist_ok=True)
        mic_gap_json.write_text(
            json.dumps(
                {
                    "transcription": [
                        {
                            "offsets": {"from": 0, "to": 10000},
                            "tokens": [
                                {"text": " remote", "p": 0.95, "offsets": {"from": 0, "to": 1300}},
                                {"text": " phrase", "p": 0.95, "offsets": {"from": 1300, "to": 3000}},
                                {"text": " local", "p": 0.98, "offsets": {"from": 3400, "to": 4700}},
                                {"text": " answer", "p": 0.98, "offsets": {"from": 4700, "to": 6200}},
                                {"text": " remains", "p": 0.98, "offsets": {"from": 6200, "to": 8000}},
                            ],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        rows = [
            {
                "index": 1,
                "clip_start_sec": 0.0,
                "remote": {"wav": "derived/live/chunks/000001/remote.wav"},
                "mic": {"asr_wav": "derived/live/chunks/000001/mic.live_echo_guard.wav"},
            },
            {
                "index": 2,
                "clip_start_sec": 10.0,
                "remote": {"wav": "derived/live/chunks/000002/remote.wav"},
                "mic": {"asr_wav": "derived/live/chunks/000002/mic.live_echo_guard.wav"},
            },
            {
                "index": 3,
                "clip_start_sec": 20.0,
                "remote": {"asr": {"json": "derived/live/chunks/000003/remote.json"}},
            },
            {
                "index": 4,
                "clip_start_sec": 100.0,
                "mic": {"asr": {"json": "derived/live/chunks/000004/mic.json"}},
            },
        ]
        chunks_path = session / "derived/live/chunks.jsonl"
        chunks_path.parent.mkdir(parents=True, exist_ok=True)
        chunks_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

        turns = [
            {
                "id": "remote-delayed",
                "chunk_index": 1,
                "source": "remote_segment",
                "role": "Colleagues",
                "start": 0.0,
                "end": 8.0,
                "text": "remote speech",
            },
            {
                "id": "local-after-remote",
                "chunk_index": 2,
                "source": "mic_segment",
                "role": "Me",
                "start": 10.0,
                "end": 18.0,
                "text": "local speech",
            },
        ]
        result = compare.apply_voice_activity_boundary_retime(session, turns)
        by_id = {str(row.get("id")): row for row in result}
        remote_start = float(by_id["remote-delayed"]["start"])
        local_start = float(by_id["local-after-remote"]["start"])
        assert 3.7 <= remote_start <= 4.1, remote_start
        assert 4.7 <= local_start - 10.0 <= 5.1, local_start
        assert by_id["remote-delayed"].get("voice_activity_boundary_retime") is True
        assert by_id["local-after-remote"].get("voice_activity_boundary_retime") is True

        token_turns = [
            {
                "id": "remote-token-density",
                "chunk_index": 3,
                "segment_index": 1,
                "source": "remote_segment",
                "role": "Colleagues",
                "start": 20.0,
                "end": 30.0,
                "text": "dense remote speech",
            }
        ]
        token_result = compare.apply_token_density_boundary_retime(session, token_turns)
        assert 27.7 <= float(token_result[0]["start"]) <= 28.1, token_result[0]
        assert token_result[0].get("token_density_boundary_retime") is True

        short_turn = {
            "role": "Colleagues",
            "start": 100.0,
            "end": 101.0,
            "text": "Где есть?",
            "tokens": compare.tokens("Где есть?"),
        }
        nearby = {
            "id": "nearby",
            "role": "Colleagues",
            "start": 102.0,
            "end": 103.0,
            "text": "Где?",
            "tokens": compare.tokens("Где?"),
        }
        distant = {
            "id": "distant",
            "role": "Colleagues",
            "start": 1000.0,
            "end": 1001.0,
            "text": "Где есть?",
            "tokens": compare.tokens("Где есть?"),
        }
        match = compare.best_batch_match(short_turn, [distant, nearby], same_role_only=True)
        assert match is not None and match.get("batch_id") == "nearby", match

        deduped = compare.dedupe_supplemental_turns_by_interval(
            [
                {
                    "id": "long",
                    "chunk_index": 4,
                    "role": "Me",
                    "start": 40.0,
                    "end": 50.0,
                    "text": "Нужно проверить и сохранить все настройки проекта",
                },
                {
                    "id": "contained",
                    "chunk_index": 4,
                    "role": "Me",
                    "start": 42.0,
                    "end": 48.0,
                    "text": "проверить и сохранить все настройки",
                },
            ]
        )
        assert [row.get("id") for row in deduped] == ["long"], deduped

        baseline_boundary_turns = [
            {
                "id": "mic-duplicate",
                "chunk_index": 5,
                "source": "mic_segment",
                "role": "Me",
                "start": 50.0,
                "end": 54.0,
                "text": "remote phrase appears in mic",
            },
            {
                "id": "remote-authoritative",
                "chunk_index": 5,
                "source": "remote_segment",
                "role": "Colleagues",
                "start": 51.0,
                "end": 55.0,
                "text": "remote phrase appears in mic",
            },
        ]
        baseline_adjustments = compare.live_boundary_split_retime_adjustments(
            baseline_boundary_turns
        )
        assert "mic-duplicate" in baseline_adjustments, baseline_adjustments
        baseline_retimed = compare.apply_live_boundary_split_retime(
            baseline_boundary_turns,
            baseline_adjustments,
        )
        assert [row.get("id") for row in baseline_retimed] == [
            "mic-duplicate_boundary_prefix",
            "remote-authoritative",
        ], baseline_retimed
        assert baseline_retimed[0].get("boundary_order_split_part") == "preserved_prefix", baseline_retimed

        live_turns, target_turns, supplemental_turns, _, _ = compare.target_me_shadow_profile_components(
            session=session,
            policy=compare.BASELINE_LIVE_BOUNDARY_SPLIT_RETIME_PROFILE_POLICY,
            live_turns_rows=baseline_boundary_turns,
            suppressed_mic_asr_segments=[],
            target_me_rows=[],
            target_me_turns_by_policy={"target_me_possible_timeline_safe_v1": baseline_boundary_turns},
            persistent_target_me_rows=[],
            batch_utterances=[],
        )
        assert [row.get("id") for row in live_turns] == ["remote-authoritative"], live_turns
        assert not target_turns, target_turns
        assert not supplemental_turns, supplemental_turns

        mixed_local_turns = [
            {
                "id": "mic-mixed",
                "chunk_index": 6,
                "source": "mic_segment",
                "role": "Me",
                "start": 60.0,
                "end": 68.0,
                "text": "remote phrase and my unique local answer follows here",
            },
            {
                "id": "remote-mixed",
                "chunk_index": 6,
                "source": "remote_segment",
                "role": "Colleagues",
                "start": 61.0,
                "end": 65.0,
                "text": "remote phrase",
            },
        ]
        mixed_adjustments = compare.live_boundary_split_retime_adjustments(mixed_local_turns)
        assert "mic-mixed" not in mixed_adjustments, mixed_adjustments

        previous = {
            "role": "Me",
            "start": 100.0,
            "end": 104.0,
            "match": {"batch_start": 101.0, "batch_end": 104.0},
        }
        current = {
            "role": "Colleagues",
            "start": 100.2,
            "end": 101.0,
            "match": {"batch_start": 100.0, "batch_end": 100.2},
        }
        context = compare.order_mismatch_batch_interval_context(previous, current)
        assert context.get("near_simultaneous_cross_source_explains_reorder") is True, context

        advisory = {
            "role_mismatch_in_pair": False,
            "match_ambiguity": "ambiguous",
            "min_score_margin": 0.05,
            "previous": {"plausible_match_count": 4, "turn_content_token_count": 5},
            "current": {"plausible_match_count": 2, "turn_content_token_count": 5},
        }
        assert compare.order_mismatch_is_blocking(advisory) is False

        gates = compare.parity_gates(
            capture_safety_gate=compare.gate("capture_safety", "passed", "fixture"),
            blockers=[],
            duplicate_count=0,
            boundary_summary={},
            recall=1.0,
            batch_quality={},
            live_assessment={
                "metrics": {
                    "live_order_mismatch_count": 5,
                    "live_role_constrained_order_mismatch_count": 2,
                    "live_contentful_role_constrained_order_mismatch_count": 2,
                    "live_blocking_contentful_role_constrained_order_mismatch_count": 0,
                    "live_advisory_contentful_role_constrained_order_mismatch_count": 2,
                    "live_missing_me_seconds": 0,
                    "live_suspected_remote_leak_in_me_seconds": 0,
                }
            },
            readiness={"use_gate": "ready_for_notes"},
            outcome={"outcome": "ready_for_notes", "metrics": {"review_burden_ratio": 0}},
        )
        order_gate = next(row for row in gates if row.get("name") == "order_risk")
        assert order_gate.get("status") == "passed", order_gate

        gap_turns, gap_rejected = compare.target_me_remote_gap_trim_turns(
            session,
            target_me_rows=[
                {
                    "id": "target-confirmed",
                    "chunk_index": 4,
                    "interval": {"start": 100.0, "end": 110.0},
                    "text": "remote phrase local answer remains",
                    "classification": {"label": "target_me_confirmed", "confidence": 0.94},
                    "target_me_rescue_policy_candidates": ["target_me_confirmed_v1"],
                }
            ],
            remote_turns=[
                {
                    "id": "remote-prefix",
                    "chunk_index": 4,
                    "role": "Colleagues",
                    "start": 100.0,
                    "end": 103.0,
                    "text": "remote phrase",
                }
            ],
        )
        assert not gap_rejected, gap_rejected
        assert len(gap_turns) == 1, gap_turns
        assert gap_turns[0]["text"] == "local answer remains", gap_turns[0]
        assert float(gap_turns[0]["start"]) >= 103.2, gap_turns[0]

        micro_report_path = session.parent / "_reports/live-pipeline/live_target_me_remote_gap_micro_asr_lab.json"
        micro_report_path.parent.mkdir(parents=True, exist_ok=True)
        micro_report_path.write_text(
            json.dumps(
                {
                    "items": [
                        {
                            "session": session.name,
                            "start": 200.0,
                            "end": 204.0,
                            "existing_island_text": "local answer",
                            "decision": {
                                "label": "micro_asr_live_only_alignment_candidate",
                                "source_text_token_recall": 1.0,
                                "remote_text_recall_in_micro": 0.0,
                            },
                            "best_live_attempt": {
                                "status": "ok",
                                "chunk_index": 5,
                                "text": "full local answer remains",
                                "score": 0.95,
                                "remote_similarity": 0.05,
                            },
                        },
                        {
                            "session": session.name,
                            "start": 210.0,
                            "end": 214.0,
                            "decision": {"label": "blocked_remote_similarity"},
                            "best_live_attempt": {
                                "status": "ok",
                                "chunk_index": 5,
                                "text": "remote duplicate",
                                "score": 0.90,
                                "remote_similarity": 0.50,
                            },
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
        micro_turns, micro_rejected = compare.live_boundary_micro_asr_lab_shadow_turns(
            session,
            candidate_source="target-me-remote-gap",
        )
        assert len(micro_turns) == 1, micro_turns
        assert micro_turns[0].get("target_me_remote_gap_micro_asr_shadow") is True, micro_turns[0]
        assert micro_turns[0].get("used_batch_fields_for_selection") is False, micro_turns[0]
        assert len(micro_rejected) == 1, micro_rejected

        covered, covered_rejected = compare.filter_micro_asr_turns_covered_by_base(
            micro_turns,
            [
                {
                    "id": "base-covers-micro",
                    "chunk_index": 5,
                    "role": "Me",
                    "start": 199.0,
                    "end": 205.0,
                    "text": "full local answer remains in the base turn",
                }
            ],
        )
        assert not covered, covered
        assert covered_rejected[0].get("reason") == "micro_asr_already_covered_by_base_turn", covered_rejected

        enrollment_summary_path = (
            session
            / "derived/audit/live-local-only-enrollment-probe/live_local_only_enrollment_probe_summary.json"
        )
        enrollment_summary_path.parent.mkdir(parents=True, exist_ok=True)
        enrollment_summary_path.write_text(
            json.dumps(
                {
                    "live_segment_evaluations": [
                        {
                            "chunk_index": 9,
                            "start": 300.0,
                            "end": 304.0,
                            "text": "future full session candidate",
                            "classification": "local_only_seed_supports_live_segment",
                        }
                    ],
                    "causal_live_segment_evaluations": [
                        {
                            "chunk_index": 8,
                            "start": 220.0,
                            "end": 223.0,
                            "text": "causal local phrase",
                            "classification": "causal_local_only_seed_supports_live_segment",
                            "enrollment": {"mode": "past_only", "cutoff_sec": 220.0},
                            "live_features": {"segment_gate_status": "suppressed"},
                        },
                        {
                            "chunk_index": 8,
                            "start": 223.4,
                            "end": 227.0,
                            "text": "continues safely here",
                            "classification": "causal_local_only_seed_supports_live_segment",
                            "enrollment": {"mode": "past_only", "cutoff_sec": 223.4},
                            "live_features": {"segment_gate_status": "kept"},
                        },
                        {
                            "chunk_index": 8,
                            "start": 228.0,
                            "end": 230.0,
                            "text": "remote ambiguous",
                            "classification": "causal_live_segment_remote_ambiguous",
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        causal_candidates, causal_rejected = micro_lab.select_local_only_seed_live_segment_candidates(
            session.parent,
            10,
            causal=True,
        )
        assert not causal_rejected, causal_rejected
        assert len(causal_candidates) == 1, causal_candidates
        causal_candidate = causal_candidates[0]
        assert causal_candidate["local_island_examples"][0]["start"] == 220.0, causal_candidate
        assert causal_candidate["local_island_examples"][0]["end"] == 227.0, causal_candidate
        causal_features = causal_candidate["selection_features"]
        assert causal_features.get("enrollment_scope") == "past_only", causal_features
        assert causal_features.get("used_batch_fields_for_selection") is False, causal_features
        assert "future full session candidate" not in causal_candidate.get("text", ""), causal_candidate

        causal_report_path = (
            session.parent / "_reports/live-pipeline/live_causal_local_only_seed_live_segment_micro_asr_lab.json"
        )
        causal_report_path.write_text(
            json.dumps(
                {
                    "items": [
                        {
                            "session": session.name,
                            "start": 220.0,
                            "end": 227.0,
                            "existing_island_text": "causal local phrase continues safely here",
                            "decision": {
                                "label": "micro_asr_live_only_alignment_candidate",
                                "used_batch_fields_for_selection": False,
                                "source_text_token_recall": 1.0,
                                "remote_text_recall_in_micro": 0.0,
                            },
                            "best_live_attempt": {
                                "status": "ok",
                                "chunk_index": 8,
                                "text": "full causal local phrase continues safely here",
                                "score": 0.94,
                                "remote_similarity": 0.04,
                            },
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        causal_turns, causal_turns_rejected = compare.live_boundary_micro_asr_lab_shadow_turns(
            session,
            candidate_source="causal-local-only-seed-live-segment",
        )
        assert not causal_turns_rejected, causal_turns_rejected
        assert len(causal_turns) == 1, causal_turns
        assert causal_turns[0].get("causal_local_only_seed_live_segment_micro_asr_shadow") is True, causal_turns[0]
        assert causal_turns[0].get("used_batch_fields_for_selection") is False, causal_turns[0]
        assert compare.target_me_shadow_profile_is_live_implementable(
            compare.CAUSAL_LOCAL_ONLY_SEED_LIVE_SEGMENT_MICRO_ASR_LAB_PROFILE_POLICY
        ) is False

    print("live voice activity retime checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
