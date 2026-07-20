#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any


SCRIPT = Path(__file__).with_name("speaker-mode-hardening.py")
ECHO_HELPER = Path(__file__).with_name("echo-guard-session-local-fir.py")


def load_module() -> Any:
    spec = importlib.util.spec_from_file_location("murmurmark_speaker_mode_hardening", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load speaker-mode hardening module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_echo_helper() -> Any:
    spec = importlib.util.spec_from_file_location("murmurmark_echo_guard_local_fir", ECHO_HELPER)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load Echo Guard helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rows(values: list[float]) -> list[dict[str, float]]:
    return [{"remote_similarity_before": value} for value in values]


def main() -> int:
    module = load_module()
    echo_helper = load_echo_helper()

    speaker = rows([0.02, 0.05, 0.07, 0.08, 0.10, 0.12, 0.15, 0.20, 0.24, 0.30])
    mode, confidence, reasons, metrics = module.classify_mode(speaker)
    assert mode == "speaker_playback", (mode, metrics)
    assert confidence >= 0.75, confidence
    assert reasons == ["remote_only_windows_show_repeatable_remote_to_mic_coupling"], reasons

    low_leak = rows([0.002, 0.004, 0.006, 0.008, 0.01, 0.012, 0.015, 0.02, 0.025, 0.04])
    mode, confidence, reasons, metrics = module.classify_mode(low_leak)
    assert mode == "headphones_or_low_leak", (mode, metrics)
    assert confidence >= 0.75, confidence
    assert reasons == ["remote_only_windows_show_low_remote_to_mic_coupling"], reasons

    mode, confidence, reasons, metrics = module.classify_mode(rows([0.01, 0.02, 0.03, 0.04]))
    assert mode == "uncertain", (mode, metrics)
    assert confidence == 0.0, confidence
    assert reasons == ["not_enough_remote_only_windows"], reasons

    first = module.classify_mode(speaker)
    second = module.classify_mode(speaker)
    assert first == second
    for sample in (speaker, low_leak, rows([0.01, 0.02, 0.03, 0.04])):
        laboratory_mode = module.classify_mode(sample)[0]
        production_mode = echo_helper.acoustic_mode_from_segments(
            [{**row, "state": "remote_only"} for row in sample]
        )["mode"]
        assert production_mode == laboratory_mode, (production_mode, laboratory_mode)
    assert module.expected_modes(["headphones", "office_noise"]) == ["headphones_or_low_leak"]
    assert module.expected_modes(["verified_no_speech"]) == ["uncertain"]
    assert module.expected_modes(["speakers", "multiple_remote_speakers"]) == [
        "speaker_playback",
        "headphones_or_low_leak",
    ]

    class FakeAudio:
        @staticmethod
        def content_tokens(text: str) -> list[str]:
            return [token for token in text.lower().split() if token]

        @staticmethod
        def text_similarity(left: str, right: str) -> dict[str, float]:
            equal = left.strip().lower() == right.strip().lower()
            return {
                "similarity": 1.0 if equal else 0.0,
                "containment": 1.0 if equal else 0.0,
            }

    evidence = {
        "decision": {"checks": {"protected_content": False, "unique_target_tokens": []}},
        "calibration": {
            "status": "reliable",
            "required_evidence_ready": True,
            "thresholds": {"weak": 0.5, "keep": 0.8},
        },
        "speaker_state": {"local_only_ratio": 0.0, "double_talk_ratio": 0.0},
        "voice_scores": {"mic_clean:exact": {"positive_similarity": 0.2}},
    }
    queue = {
        "source": "remote_duplicate",
        "me_utterance_ids": ["me_1"],
        "target_text": "same words",
        "remote_text": "same words",
        "whole_utterance": {"coverage_ratio": 1.0},
        "source_detail": {"classification": {"label": "remote_duplicate", "confidence": 0.96}},
    }
    decision = module.profile_decision(evidence, queue, FakeAudio)
    assert decision["action"] == "drop_duplicate_or_noise", decision

    weak_evidence = dict(evidence)
    weak_evidence["calibration"] = {"status": "weak", "thresholds": {"weak": 0.5, "keep": 0.8}}
    decision = module.profile_decision(weak_evidence, queue, FakeAudio)
    assert decision["action"] == "needs_review", decision

    assert module.has_confirmed_me_evidence(
        {"quality": {"review": {"action": "keep_me", "confidence": 0.9}}}
    )
    assert not module.has_confirmed_me_evidence(
        {"quality": {"review": {"action": "drop_me", "confidence": 0.99}}}
    )
    assert module.split_text_lossless("первая часть вторая часть", 2) == ("первая часть", "вторая часть")

    print("speaker-mode hardening checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
