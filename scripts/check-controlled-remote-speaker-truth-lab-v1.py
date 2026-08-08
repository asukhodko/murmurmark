#!/usr/bin/env python3
"""Contract checks for Controlled Remote Speaker Truth Lab v1."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any

import numpy as np
import soundfile as sf


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/controlled-remote-speaker-truth-lab-v1.py"
POLICY = ROOT / "policies/controlled-remote-speaker-truth-lab-v1.json"
TRACKED_MANIFEST = ROOT / "docs/testing/controlled-remote-speaker-truth-lab-v1-manifest.json"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(
    action: str,
    out_dir: Path,
    *,
    policy: Path = POLICY,
    expected: int = 0,
    force: bool = False,
) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        str(SCRIPT),
        action,
        "--fixture-mode",
        "--policy",
        str(policy),
        "--out-dir",
        str(out_dir),
    ]
    if force:
        command.append("--force")
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    if result.returncode != expected:
        raise AssertionError(
            f"unexpected exit {result.returncode}, expected {expected}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def assert_reconstruction(private: Path, policy: dict[str, Any]) -> None:
    for split, scenario_ids in policy["corpus"]["splits"].items():
        for scenario_id in scenario_ids:
            directory = private / "sessions" / split / scenario_id
            scenario = read_json(directory / "scenario.json")
            mixture = sf.read(directory / "mixture.wav", dtype="int16")[0].astype(np.int32)
            reconstructed = np.zeros_like(mixture, dtype=np.int32)
            for speaker in scenario["active_speakers"]:
                reconstructed += sf.read(
                    directory / "sources" / f"{speaker}.wav", dtype="int16"
                )[0].astype(np.int32)
            assert int(np.max(np.abs(mixture - reconstructed), initial=0)) == 0


def check() -> None:
    with tempfile.TemporaryDirectory(prefix="_remote-truth-lab-v1-", dir=ROOT / "sessions") as temp:
        root = Path(temp)
        ready_dir = root / "ready"
        run("build", ready_dir, force=True)
        frozen = read_json(ready_dir / "private/frozen_manifest.json")
        assert frozen["schema"] == "murmurmark.controlled_remote_speaker_truth_lab_frozen_manifest/v1"
        assert frozen["mode"] == "fixture"
        assert frozen["enrolled_speaker_count"] >= 4
        assert len(frozen["scenario_summaries"]) == 8
        frozen_hashes = {
            name: digest(ready_dir / "private" / name) for name in frozen["artifacts"]
        }

        run("evaluate", ready_dir)
        report_path = ready_dir / "controlled_remote_speaker_truth_lab_report.json"
        report = read_json(report_path)
        assert report["schema"] == "murmurmark.controlled_remote_speaker_truth_lab_report/v1"
        assert report["decision"] == "LAB_READY"
        assert all(report["gates"].values())
        assert report["evaluation"]["wavlm_open_set_candidate"]["hard"]["bcubed"]["f1"] == 1.0
        assert report["evaluation"]["wavlm_open_set_candidate"]["hard"]["boundary_recall"] == 1.0
        assert report["evaluation"]["wavlm_open_set_candidate"]["hard"]["open_set_false_attributions"] == 0
        assert report["evaluation"]["track_decisions"] == {
            "coverage_v3_topology": {
                "decision": "CONTROL_QUALIFIED",
                "gates": {
                    "boundary_recall": True,
                    "held_out_bcubed_f1": True,
                    "held_out_pairwise_precision": True,
                    "zero_open_set_false_attribution": True,
                },
            },
            "wavlm_open_set_candidate": {
                "decision": "CANDIDATE_QUALIFIED",
                "gates": {
                    "boundary_recall": True,
                    "held_out_bcubed_f1": True,
                    "held_out_pairwise_precision": True,
                    "zero_open_set_false_attribution": True,
                },
            },
        }
        rendered = report_path.read_text(encoding="utf-8")
        assert "/Users/" not in rendered and "/home/" not in rendered
        assert "system_voice" not in rendered and '"text"' not in rendered
        assert report["safety"] == {
            "audit_only": True,
            "coverage_v3_changed": False,
            "echo_guard_changed": False,
            "primary_asr_changed": False,
            "real_transcript_changed": False,
            "synthetic_labels_promoted": False,
        }
        hard_truth = [
            row
            for path in sorted((ready_dir / "private/sessions/hard").glob("*/truth_words.jsonl"))
            for row in read_jsonl(path)
        ]
        assert any(row["truth_class"] == "mixed" for row in hard_truth)
        assert any(row["truth_class"] == "open_set_speaker" for row in hard_truth)
        assert all(row["truth_source"] == "exact_scripted" for row in hard_truth)
        assert_reconstruction(ready_dir / "private", read_json(POLICY))
        run("replay", ready_dir)
        assert read_json(ready_dir / "replay_report.json")["deterministic"] is True
        assert frozen_hashes == {
            name: digest(ready_dir / "private" / name) for name in frozen["artifacts"]
        }

        blocked_dir = root / "missing"
        run("status", blocked_dir, expected=2)

        strict_policy = read_json(POLICY)
        strict_policy["state"] = "fixture_do_not_advance"
        strict_policy["gates"]["minimum_held_out_bcubed_f1"] = 1.01
        strict_path = root / "strict-policy.json"
        write_json(strict_path, strict_policy)
        strict_dir = root / "strict"
        run("build", strict_dir, policy=strict_path, force=True)
        run("evaluate", strict_dir, policy=strict_path, expected=2)
        strict_report = read_json(strict_dir / "controlled_remote_speaker_truth_lab_report.json")
        assert strict_report["decision"] == "DO_NOT_ADVANCE"
        assert strict_report["blockers"] == ["wavlm_candidate_held_out_bcubed_f1"]

        mixture = ready_dir / "private/sessions/hard/hard_short_turns/mixture.wav"
        with mixture.open("ab") as handle:
            handle.write(b"tamper")
        tampered = run("replay", ready_dir, expected=2)
        assert "frozen_artifacts_stale" in tampered.stderr

    tracked = read_json(TRACKED_MANIFEST)
    assert tracked["schema"] == "murmurmark.controlled_remote_speaker_truth_lab_tracked_manifest/v1"
    assert tracked["implementation"]["checker"]["sha256"] == digest(Path(__file__).resolve())
    assert tracked["implementation"]["policy"]["sha256"] == digest(POLICY)
    assert tracked["implementation"]["runner"]["sha256"] == digest(SCRIPT)
    available = True
    for section in ("private_corpus", "public_artifacts"):
        rows = tracked[section]
        candidates = [rows["frozen_manifest"]] if section == "private_corpus" else list(rows.values())
        for row in candidates:
            path = ROOT / str(row["path"])
            if not path.is_file():
                available = False
                continue
            assert digest(path) == row["sha256"]
    if available:
        report = read_json(
            ROOT / tracked["public_artifacts"]["report"]["path"]
        )
        replay_report = read_json(
            ROOT / tracked["public_artifacts"]["replay"]["path"]
        )
        assert report["decision"] == tracked["decision"] == "DO_NOT_ADVANCE"
        assert replay_report["deterministic"] is True
        assert report["source"]["corpus_sha256"] == tracked["private_corpus"]["corpus_sha256"]
    else:
        print("local controlled remote speaker truth corpus verification skipped")

    print("controlled remote speaker truth lab checks passed")


if __name__ == "__main__":
    check()
