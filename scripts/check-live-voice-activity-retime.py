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
    sample_rate = 16_000
    with tempfile.TemporaryDirectory(prefix="murmurmark-live-retime-") as temporary:
        session = Path(temporary)
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

    print("live voice activity retime checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
