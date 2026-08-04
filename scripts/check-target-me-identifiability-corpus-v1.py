#!/usr/bin/env python3
"""Deterministic fixture checks for Target-Me Identifiability Corpus v1."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

import numpy as np


ROOT = Path(__file__).resolve().parent.parent


def load_module() -> Any:
    path = ROOT / "scripts/target-me-identifiability-corpus-v1.py"
    spec = importlib.util.spec_from_file_location("murmurmark_identifiability_fixture", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CORE = load_module()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def write_wave(path: Path, values: np.ndarray) -> None:
    CORE.write_wave(path, values)


def audio_artifact(path: Path, root: Path) -> dict[str, Any]:
    return CORE.artifact(path, root)


def fake_enrollment(
    root: Path, split: str, speaker: str, values: np.ndarray
) -> dict[str, Any]:
    path = root / "enrollments" / split / f"{speaker}.npy"
    path.parent.mkdir(parents=True, exist_ok=True)
    np.save(path, values.astype(np.float32), allow_pickle=False)
    return {
        "schema": CORE.ENROLLMENT_SCHEMA,
        "enrollment_id": f"{split}:{speaker}",
        "split": split,
        "speaker_id": speaker,
        "vector": CORE.artifact(path, root),
    }


def fixture_policy() -> dict[str, Any]:
    return {
        "audio": {"clip_samples": 64_000, "clip_duration_sec": 4.0, "peak_limit": 0.92},
        "rendering": {
            "seed": 7,
            "target_gain_db": [0.0],
            "remote_relative_db": [0.0],
            "other_relative_db": [0.0],
            "noise_relative_db": [-20.0],
            "other_local_paths": ["nearfield_direct_v1"],
        },
    }


def render_fixture(root: Path) -> dict[str, Any]:
    time = np.arange(64_000, dtype=np.float32) / 16_000.0
    values = {
        "target_me": 0.8 * np.sin(2.0 * np.pi * 180.0 * time),
        "remote_echo": 0.8 * np.sin(2.0 * np.pi * 320.0 * time),
        "other_local_speech": 0.8 * np.sin(2.0 * np.pi * 510.0 * time),
        "other_local_noise": 0.3 * np.sin(2.0 * np.pi * 900.0 * time),
    }
    sources: dict[str, Any] = {}
    for kind, audio in values.items():
        path = root / "streams" / f"{kind}.wav"
        write_wave(path, audio)
        sources[kind] = {
            "stream": audio_artifact(path, root),
            "stream_kind": kind,
            "offset_samples": 0,
            "samples": 64_000,
        }
    components = {key: True for key in values}
    rendering = CORE.rendering_descriptor(
        policy=fixture_policy(),
        stage=root,
        sources=sources,
        family="ordinary_double_talk",
        seed=7,
        index=0,
        components=components,
    )
    row = {
        "sources": sources,
        "rendering": rendering,
    }
    first = CORE.render_from_descriptor(root, row)
    second = CORE.render_from_descriptor(root, row)
    require(
        all(np.array_equal(first[key], second[key]) for key in first),
        "rendering is not deterministic",
    )
    require(float(np.max(np.abs(first["mixture"]))) <= 0.920001, "peak limiter failed")
    require(
        np.array_equal(
            first["mixture"],
            first["target_me"]
            + first["remote_echo"]
            + first["other_local_speech"]
            + first["other_local_noise"],
        ),
        "additive reconstruction failed",
    )
    return {"sources": sources, "rendering": rendering, "audio": first}


def run() -> None:
    with tempfile.TemporaryDirectory(prefix="murmurmark-identifiability-") as temporary:
        root = Path(temporary)
        rendered = render_fixture(root)

        deterministic_a = root / "deterministic-a.wav"
        deterministic_b = root / "deterministic-b.wav"
        write_wave(deterministic_a, rendered["audio"]["mixture"])
        write_wave(deterministic_b, rendered["audio"]["mixture"])
        require(
            deterministic_a.read_bytes() == deterministic_b.read_bytes(),
            "identical audio produced different WAV bytes",
        )
        require(
            b"PEAK" not in deterministic_a.read_bytes(),
            "WAV contains a volatile libsndfile PEAK timestamp",
        )

        changed = root / "changed.bin"
        changed.write_bytes(b"before")
        descriptor = CORE.artifact(changed, root)
        changed.write_bytes(b"after")
        try:
            CORE.resolve_artifact(root, descriptor)
        except RuntimeError:
            pass
        else:
            raise SystemExit("stale source hash was accepted")

        target = fake_enrollment(root, "train", "private_target_me_v1", np.array([1.0, 0.0]))
        other = fake_enrollment(root, "train", "speaker_other", np.array([0.0, 1.0]))
        audio_descriptors: dict[str, Any] = {}
        for kind, values in rendered["audio"].items():
            path = root / "item" / f"{kind}.wav"
            write_wave(path, values)
            audio_descriptors[kind] = CORE.artifact(path, root)
        item = {
            "item_id": "fixture-item",
            "split": "train",
            "other_local_speaker_id": "speaker_other",
            "rendering": rendered["rendering"],
            "audio": audio_descriptors,
        }
        queries = CORE.build_queries(items=[item], enrollments=[target, other])
        require(len(queries) == 2, "speaker-bearing item lacks paired queries")
        require(
            len({row["mixture"]["sha256"] for row in queries}) == 1,
            "enrollment swap changed mixture bytes",
        )
        require(
            len({row["expected_target"]["sha256"] for row in queries}) == 2,
            "enrollment swap did not change expected speaker",
        )
        try:
            CORE.build_queries(items=[item], enrollments=[target])
        except KeyError:
            pass
        else:
            raise SystemExit("missing enrollment was accepted")

        speakers = [
            {"role": "non_target_other_local", "speaker_id": "same", "split": "train"},
            {"role": "non_target_other_local", "speaker_id": "same", "split": "dev"},
        ]
        require(CORE.non_target_identity_overlap(speakers) == 1, "speaker contamination missed")
        require(
            CORE.source_overlap_count({"train": {"same.flac"}, "dev": {"same.flac"}}) == 1,
            "source contamination missed",
        )
        source_sets = {
            "train:speaker": {"enrollment": {"same.flac"}, "mixture": {"same.flac"}}
        }
        require(CORE.enrollment_mixture_overlap(source_sets) == 1, "enrollment reuse missed")
        require(not CORE.audio_chunks(np.zeros(64_000, dtype=np.float32)), "silence was accepted")

        publication = root / "published" / "stable"
        publication.mkdir(parents=True)
        CORE.write_json(
            root / "current.json",
            {
                "schema": "murmurmark.target_me_identifiability_current/v1",
                "publication": "published/stable",
            },
        )
        (root / ".staging-interrupted").mkdir()
        require(
            CORE.current_publication(root) == publication,
            "orphan staging changed the current publication",
        )

        publication_stage = root / "publication-staging"
        publication_stage.mkdir()
        anchor = publication_stage / "anchor.json"
        CORE.write_json(anchor, {"stable": True})
        basis_rows = [
            {"name": anchor.name, "bytes": anchor.stat().st_size, "sha256": CORE.sha256(anchor)}
        ]
        basis = {
            "schema": CORE.DECISION_SCHEMA,
            "generator": {"name": "fixture", "version": "1"},
            "policy_sha256": "fixture",
            "manifests": {
                "files": basis_rows,
                "fingerprint": CORE.digest_json(basis_rows),
            },
            "oracle_passed": True,
            "replay_status": "passed",
            "training_performed": False,
            "production_changed": False,
        }
        fingerprint = CORE.digest_json(basis)
        CORE.write_json(
            publication_stage / "corpus_decision.json",
            {
                "schema": CORE.DECISION_SCHEMA,
                "decision": CORE.READY,
                "fingerprint": fingerprint,
                "basis": basis,
            },
        )
        CORE.write_json(
            publication_stage / "oracle_report.json",
            {"schema": CORE.ORACLE_SCHEMA, "passed": True},
        )
        CORE.write_json(
            publication_stage / "replay_report.json",
            {"schema": CORE.REPLAY_SCHEMA, "status": "passed"},
        )
        publication_rows = [
            CORE.artifact(path, publication_stage)
            for path in sorted(publication_stage.iterdir())
            if path.is_file()
        ]
        CORE.write_json(
            publication_stage / "publication_manifest.json",
            {
                "schema": "murmurmark.target_me_identifiability_publication/v1",
                "fingerprint": fingerprint,
                "files": publication_rows,
                "tree_fingerprint": CORE.digest_json(publication_rows),
            },
        )
        publication_path = root / "published" / fingerprint
        publication_stage.rename(publication_path)
        require(
            CORE.verify_publication(publication_path)["passed"],
            "valid publication was rejected",
        )
        tampered_rows = [row for row in publication_rows if row["path"] != "anchor.json"]
        CORE.write_json(
            publication_path / "publication_manifest.json",
            {
                "schema": "murmurmark.target_me_identifiability_publication/v1",
                "fingerprint": fingerprint,
                "files": tampered_rows,
                "tree_fingerprint": CORE.digest_json(tampered_rows),
            },
        )
        require(
            not CORE.verify_publication(publication_path)["passed"],
            "publication with an omitted file was accepted",
        )

    production = ROOT / "policies/speaker-preserving-neural-echo-production-v2.json"
    require(
        CORE.sha256(production)
        == "68f9abab1197035c76a936b97cca6fba05d3992e7a0ccce82de4d8ec0959a425",
        "production Speaker-Preserving Neural Echo v2 policy changed",
    )
    print("target-me identifiability corpus fixture ok")


if __name__ == "__main__":
    run()
