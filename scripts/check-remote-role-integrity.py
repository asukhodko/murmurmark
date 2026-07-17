#!/usr/bin/env python3
"""Focused checks that remote text cannot absorb unique microphone speech."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def load_transcribe_module():
    script = Path(__file__).with_name("transcribe-simple-whispercpp.py")
    spec = importlib.util.spec_from_file_location("murmurmark_transcribe_simple_role_integrity", script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {script}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def candidate(
    module,
    *,
    identifier: str,
    track: str,
    text: str,
    start_ms: int,
    end_ms: int,
    display_start_ms: int | None = None,
    display_end_ms: int | None = None,
    token_avg_prob: float = 0.9,
    token_low_prob_ratio: float = 0.0,
):
    return module.Candidate(
        id=identifier,
        source_track=track,
        role="Colleagues" if track == "remote" else "Me",
        speaker_label="Colleagues" if track == "remote" else "Me",
        start_ms=start_ms,
        end_ms=end_ms,
        display_start_ms=display_start_ms if display_start_ms is not None else start_ms,
        display_end_ms=display_end_ms if display_end_ms is not None else end_ms,
        text_raw=text,
        text_norm=module.normalize_for_compare(text),
        source_segments=[],
        echo_features={},
        asr_features={
            "token_avg_prob": token_avg_prob,
            "token_low_prob_ratio": token_low_prob_ratio,
        },
    )


def main() -> int:
    module = load_transcribe_module()

    remote = candidate(
        module,
        identifier="remote",
        track="remote",
        text="Да, сегодня. Так точно. Давай, хорошего вечера.",
        start_ms=2_245_000,
        end_ms=2_270_480,
        display_start_ms=2_260_820,
        display_end_ms=2_270_480,
        token_avg_prob=0.60,
        token_low_prob_ratio=0.33,
    )
    earlier_mic = candidate(
        module,
        identifier="mic-earlier",
        track="mic",
        text="Хорошего тебе остатка пятницы.",
        start_ms=2_245_100,
        end_ms=2_257_880,
    )

    intervals = module.authoritative_remote_intervals([remote])
    assert intervals[0]["start_ms"] == remote.display_start_ms
    assert intervals[0]["source_start_ms"] == remote.start_ms
    assert module.remote_overlaps_for_candidate(earlier_mic, intervals) == []

    remote_decision = module.RoleDecision(
        candidate=remote,
        final_role="Colleagues",
        decision="keep",
        reason="authoritative_remote",
        confidence=0.9,
        matched_remote_candidate_id=None,
        evidence={},
    )
    mixed_mic = candidate(
        module,
        identifier="mixed-mic",
        track="mic",
        text="Да, сегодня с ним встречаюсь. Ну, передавай ему привет.",
        start_ms=2_258_580,
        end_ms=2_260_840,
    )
    mixed_decision = module.RoleDecision(
        candidate=mixed_mic,
        final_role=None,
        decision="drop",
        reason="remote_leak_echo_overlap",
        confidence=0.84,
        matched_remote_candidate_id=remote.id,
        evidence={"time_overlap_ratio": 0.95, "text_similarity": 0.55},
    )

    text, source, source_ids = module.output_text_for_decision(remote_decision)
    assert text == remote.text_raw
    assert source == "candidate"
    assert source_ids == []

    exact_mic = candidate(
        module,
        identifier="exact-mic",
        track="mic",
        text=remote.text_raw,
        start_ms=remote.display_start_ms,
        end_ms=remote.display_end_ms,
    )
    exact_decision = module.RoleDecision(
        candidate=exact_mic,
        final_role=None,
        decision="drop",
        reason="remote_duplicate",
        confidence=0.9,
        matched_remote_candidate_id=remote.id,
        evidence={"time_overlap_ratio": 1.0, "text_similarity": 1.0},
    )
    text, source, source_ids = module.output_text_for_decision(remote_decision)
    assert text == remote.text_raw
    assert source == "candidate"
    assert source_ids == []

    valid_remote_row = {
        "id": "utt_remote",
        "source_candidate_id": remote.id,
        "source_track": "remote",
        "role": "remote",
        "speaker_label": "Colleagues",
        "quality": {"text_source": "candidate", "text_source_candidate_ids": []},
    }
    module.assert_remote_text_provenance([valid_remote_row], [remote, mixed_mic])

    invalid_source_track = {**valid_remote_row, "source_track": "mic"}
    try:
        module.assert_remote_text_provenance([invalid_source_track], [remote, mixed_mic])
    except RuntimeError as error:
        assert "source_track is not remote" in str(error)
    else:
        raise AssertionError("remote role accepted a mic source track")

    invalid_text_candidate = {
        **valid_remote_row,
        "quality": {"text_source": "candidate", "text_source_candidate_ids": [mixed_mic.id]},
    }
    try:
        module.assert_remote_text_provenance([invalid_text_candidate], [remote, mixed_mic])
    except RuntimeError as error:
        assert "is not remote" in str(error)
    else:
        raise AssertionError("remote role accepted mic text evidence")

    echo_segment = module.Utterance(
        id="raw_mic_echo",
        source_track="mic",
        role="Me",
        speaker_label="Me",
        start_ms=2_258_580,
        end_ms=2_260_040,
        raw_text="Да, сегодня с ним встречаюсь.",
        token_avg_prob=0.83,
        token_low_prob_ratio=0.0,
    )
    local_segment = module.Utterance(
        id="raw_mic_local",
        source_track="mic",
        role="Me",
        speaker_label="Me",
        start_ms=2_260_060,
        end_ms=2_260_840,
        raw_text="Ну, передавай ему привет.",
        token_avg_prob=0.88,
        token_low_prob_ratio=0.2,
    )
    mixed_mic.source_segments = [echo_segment.id, local_segment.id]
    mixed_mic.echo_features = {"remote_active_ratio": 1.0}
    children, recovered_ids, matched_remote_id = module.split_short_remote_boundary_mic_candidate(
        candidate=mixed_mic,
        utterances_by_id={echo_segment.id: echo_segment, local_segment.id: local_segment},
        remote_intervals=intervals,
        speaker_states=[],
    )
    assert len(children) == 2
    assert recovered_ids == [local_segment.id]
    assert matched_remote_id == remote.id
    assert children[0].repair == {}
    assert children[1].repair["action"] == "keep_needs_review"
    assert children[1].repair["reason"] == "short_unique_mic_at_uncertain_remote_boundary"

    print("remote role integrity checks ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
