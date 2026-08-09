#!/usr/bin/env python3
"""Fixture checks for remote-speaker enrollment purity and abstention v2."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/evaluate-remote-speaker-enrollment-purity-abstention-v2.py"
REAL_REPORT = (
    ROOT
    / "sessions/_reports/remote-speaker-enrollment-purity-abstention-hardening-v2"
    / "remote_speaker_enrollment_purity_abstention_report.json"
)
REAL_REPLAY = (
    ROOT
    / "sessions/_reports/remote-speaker-enrollment-purity-abstention-hardening-v2"
    / "replay_report.json"
)


def load_module():
    spec = importlib.util.spec_from_file_location("remote_speaker_enrollment_purity_v2", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def vector(values: list[float]) -> list[float]:
    total = sum(value * value for value in values) ** 0.5
    return [value / total for value in values]


def policy() -> dict:
    return {
        "scope": {"expected_subwindow_requests": 12},
        "candidate": {
            "id": "fixture_subwindow_core",
            "core_similarity_threshold": 0.5,
            "minimum_available_windows": 4,
            "minimum_core_windows": 4,
            "minimum_source_exemplars_in_core": 2,
            "minimum_core_pairwise_similarity": 0.45,
            "minimum_window_impostor_margin": 0.3,
            "minimum_target_similarity": 0.5,
            "minimum_target_margin": 0.3,
            "minimum_target_coverage_sec": 1.0,
            "minimum_active_frame_ratio": 0.2,
            "maximum_context_neighbor_distance_sec": 6.0,
            "require_original_candidate_same_speaker": True,
        },
    }


def purity_inputs() -> tuple[dict, dict]:
    requests = []
    rows = []
    profiles = {
        "fixture:remote_speaker_01": [
            [1, 0, 0], [0.99, 0.01, 0], [0, 0, 1],
            [1, 0, 0], [0.98, 0.02, 0], [0, 0, -1],
        ],
        "fixture:remote_speaker_02": [
            [0, 1, 0], [0.01, 0.99, 0], [0, 0, 1],
            [0, 1, 0], [0.02, 0.98, 0], [0, 0, -1],
        ],
    }
    for profile_key, values in profiles.items():
        for index, values_row in enumerate(values):
            exemplar = index // 3
            key = f"purity:{profile_key}:{exemplar}:{index % 3}"
            requests.append({
                "key": key,
                "profile_key": profile_key,
                "source_exemplar_key": f"{profile_key}:exemplar:{exemplar}",
                "window_index": index % 3,
            })
            rows.append({"key": key, "embedding": vector(values_row)})
    return {"requests": requests}, {"rows": rows, "errors": []}


def item_payloads() -> dict:
    definitions = [
        ("keep", "remote_speaker_01", None, False, [], True, 0.0),
        ("add", None, "remote_speaker_01", False, [], True, 1.0),
        ("risky", None, "remote_speaker_01", True, [], True, 1.0),
        ("context", None, "remote_speaker_01", False, ["remote_speaker_02"], True, 1.0),
        ("noise", None, "remote_speaker_01", False, [], False, 1.0),
    ]
    comparisons = []
    decomposition = []
    embeddings = []
    for item_id, control, original, risky, context, speech_supported, active_ratio in definitions:
        key = f"item:{item_id}"
        comparisons.append({
            "item_id": item_id,
            "session_id": "fixture",
            "utterance_id": f"utterance:{item_id}",
            "start": 0.0,
            "end": 2.0,
            "word_ids": [f"word:{item_id}"],
            "word_count": 1,
            "coverage_weight_sec": 2.0,
            "item_embedding_key": key,
            "item_embedding_sha256": hashlib.sha256(key.encode()).hexdigest(),
            "control": {"speaker_id": control},
            "candidate": {"speaker_id": original},
        })
        decomposition.append({
            "item_id": item_id,
            "interval": {
                "risky": risky,
                "context_speakers": context,
                "same_utterance_speakers": [],
                "left_neighbor": {"speaker_id": None, "distance_sec": None},
                "right_neighbor": {"speaker_id": None, "distance_sec": None},
            },
            "audio": {"speech_supported": speech_supported, "active_frame_ratio": active_ratio},
        })
        embeddings.append({"key": key, "embedding": vector([1, 0, 0])})
    return {
        "enrollment_item_comparison": comparisons,
        "item_error_decomposition": decomposition,
        "shadow_embeddings": {"rows": embeddings, "errors": []},
    }


def main() -> int:
    module = load_module()
    fixture_policy = policy()
    request, embeddings = purity_inputs()
    profiles, centers, _centroids = module.build_purity_profiles(fixture_policy, request, embeddings)
    assert len(profiles) == 2
    assert all(row["status"] == "qualified" for row in profiles), profiles
    assert set(centers["fixture"]) == {"remote_speaker_01", "remote_speaker_02"}

    decisions = module.build_item_decisions(fixture_policy, item_payloads(), centers)
    by_id = {row["item_id"]: row for row in decisions}
    assert by_id["keep"]["candidate_speaker_id"] == "remote_speaker_01"
    assert by_id["keep"]["reason"] == "coverage_v3_preserved"
    assert by_id["add"]["candidate_speaker_id"] == "remote_speaker_01"
    assert by_id["risky"]["candidate_speaker_id"] is None
    assert by_id["risky"]["gates"]["interval_not_risky"] is False
    assert by_id["context"]["candidate_speaker_id"] is None
    assert by_id["context"]["gates"]["context_not_conflicting"] is False
    assert by_id["noise"]["candidate_speaker_id"] is None
    assert by_id["noise"]["gates"]["speech_supported"] is False

    with tempfile.TemporaryDirectory(prefix=".purity-source-fixture-", dir=ROOT) as temporary:
        source = Path(temporary) / "source.json"
        source.write_text("{}\n", encoding="utf-8")
        source_policy = {
            "sources": [{
                "id": "source",
                "path": str(source.relative_to(ROOT)),
                "bytes": source.stat().st_size,
                "sha256": module.sha256(source),
            }]
        }
        _verified, failures = module.verify_sources(source_policy)
        assert not failures
        source.write_text('{"tampered":true}\n', encoding="utf-8")
        _verified, failures = module.verify_sources(source_policy)
        assert failures and failures[0].startswith("source_")

    if REAL_REPORT.is_file():
        report = json.loads(REAL_REPORT.read_text(encoding="utf-8"))
        replay = json.loads(REAL_REPLAY.read_text(encoding="utf-8"))
        assert report["decision"] == "KEEP_COVERAGE_V3"
        assert report["scope"]["coverage_v3_accepted_items"] == 68
        assert report["development"]["lost_correct_control_identity_items"] == 0
        assert report["development"]["new_false_identity_items"] == 0
        assert report["development"]["candidate_fail_closed_unsafe_accepts"] == 8
        assert report["safety"]["production_mutated"] is False
        assert report["replay_verified"] is True and replay["matched"] is True

    print("remote speaker enrollment purity and abstention v2 checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
