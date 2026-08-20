#!/usr/bin/env python3
"""Synthetic checks for the frozen boundary and minority-voice candidate."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile

import numpy as np
import soundfile as sf


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/evaluate-remote-speaker-boundary-minority-v1.py"
POLICY = ROOT / "policies/remote-speaker-boundary-minority-v1.json"


def load_module():
    spec = importlib.util.spec_from_file_location("boundary_minority_v1", SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError("cannot load evaluator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeBackend:
    provenance = {"backend": "synthetic"}

    def embed(self, _path: Path, start: float, end: float) -> np.ndarray | None:
        midpoint = (start + end) / 2
        if midpoint < 2.0:
            return np.asarray([1.0, 0.0, 0.0])
        if midpoint < 4.5:
            return np.asarray([0.0, 1.0, 0.0])
        return np.asarray([0.0, 0.0, 1.0])


def fixture_audio(path: Path) -> None:
    rate = 16_000
    time = np.arange(rate * 7, dtype=np.float64) / rate
    signal = np.zeros_like(time)
    intervals = [
        (0.30, 0.90, 180.0),
        (1.00, 1.60, 180.0),
        (2.40, 3.00, 330.0),
        (3.10, 3.70, 330.0),
        (5.10, 5.70, 520.0),
    ]
    for start, end, frequency in intervals:
        keep = (time >= start) & (time < end)
        signal[keep] = 0.15 * np.sin(2 * np.pi * frequency * time[keep])
    sf.write(path, signal.astype(np.float32), rate)


def main() -> int:
    module = load_module()
    policy = module.load_policy(POLICY)
    assert policy["candidate"]["truth_identity_used_by_inference"] is False
    assert policy["candidate"]["text_used_by_inference"] is False
    module._SEGMENT_BACKEND = FakeBackend()
    with tempfile.TemporaryDirectory(prefix="murmurmark-boundary-minority-") as raw:
        audio = Path(raw) / "fixture.wav"
        fixture_audio(audio)
        source_words = [
            {
                "word_id": f"w{index}",
                "utterance_id": f"u{(index - 1) // 2 + 1}",
                "start": start,
                "end": end,
                "text": "private text must not enter features",
                "speaker_id": speaker,
            }
            for index, (start, end, speaker) in enumerate(
                [
                    (0.30, 0.90, "a"),
                    (1.00, 1.60, "a"),
                    (2.40, 3.00, "b"),
                    (3.10, 3.70, "b"),
                    (5.10, 5.70, "c"),
                ],
                1,
            )
        ]
        words = module.sanitized_words(source_words)
        assert all("text" not in row and "speaker_id" not in row for row in words)
        signatures = module.word_signatures(audio, words, policy["candidate"])
        features = module.boundary_features("fixture", audio, words, signatures, policy["candidate"])
        assert features and all("text" not in json.dumps(row) for row in features)
        parameters = {
            "minimum_change_distance": 0.03,
            "minimum_change_contrast": -0.04,
            "strong_pause_sec": 0.5,
        }
        classified = [
            {**row, "predicted_boundary": module.classify_boundary(row, parameters)}
            for row in features
        ]
        config = {
            **policy["candidate"],
            "cluster_distance_threshold": 0.2,
            "adaptive_cluster_quantile": 0.5,
            "adaptive_cluster_margin": 0.02,
            "adaptive_cluster_maximum": 0.35,
        }
        segments, partition, diagnostics = module.build_segments(
            "fixture", audio, words, signatures, classified, parameters, config
        )
        conservation = module.conservation_metrics(words, segments, partition)
        assert conservation["score"] == 1.0
        assert conservation["word_ids_and_order_exact"] is True
        assert module.unknown_metrics(segments)["segments"] >= 1
        damaged = [dict(row) for row in segments]
        damaged[-1] = {**damaged[-1], "word_ids": []}
        assert module.conservation_metrics(words, damaged, partition)["score"] == 0.0
        assert diagnostics["cluster_count"] >= 2
        stability = module.timing_shift_stability(
            "fixture", audio, words, classified, partition, parameters, config
        )
        assert 0.0 <= stability["minimum_boundary_agreement"] <= 1.0
        assert -1.0 <= stability["minimum_partition_adjusted_rand"] <= 1.0

    controlled = {
        "boundary": {"precision": 1.0, "recall": 1.0, "f1": 1.0},
        "bcubed": {"f1": 1.0},
        "pairwise": {"precision": 1.0},
        "minority_speaker_recall": 1.0,
        "unknown_word_ratio": 0.0,
        "timing_shift_stability": {
            "minimum_boundary_agreement": 1.0,
            "minimum_partition_adjusted_rand": 1.0,
        },
        "word_conservation": 1.0,
    }
    real = {
        "trust_grade": "independent_machine",
        "boundary": {"precision": 1.0, "recall": 1.0, "f1": 1.0},
        "minority_boundary_recall": 1.0,
        "speaker_count_ratio": 1.0,
        "unknown": {"word_ratio": 0.0},
        "timing_shift_stability": {
            "minimum_boundary_agreement": 1.0,
            "minimum_partition_adjusted_rand": 1.0,
        },
        "word_conservation": 1.0,
    }
    candidate = {
        "candidate_id": "synthetic",
        "parameters": {},
        "cluster_distance_threshold": 0.2,
        "adaptive_cluster_quantile": 0.5,
        "adaptive_cluster_margin": 0.02,
        "adaptive_cluster_maximum": 0.35,
        "tuning": {},
    }
    report = module.build_report(policy, candidate, controlled, real, True)
    assert report["decision"] == "EVIDENCE_BOUND"
    assert report["safety"]["production_promoted"] is False
    assert report["safety"]["candidate_qualified_for_integration"] is False
    assert report["decision"] in set(policy["decision"]["allowed_outcomes"])
    print("remote speaker boundary and minority-voice v1 checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
