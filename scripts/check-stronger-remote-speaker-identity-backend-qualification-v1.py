#!/usr/bin/env python3
"""Check Stronger Remote Speaker Identity Backend Qualification v1 contracts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv/bin/python"
SCRIPT = ROOT / "scripts/qualify-stronger-remote-speaker-identity-backend-v1.py"
POLICY = ROOT / "policies/stronger-remote-speaker-identity-backend-qualification-v1.json"
TRACKED = ROOT / "docs/testing/stronger-remote-speaker-identity-backend-qualification-v1-manifest.json"
REAL_OUT = ROOT / "sessions/_reports/stronger-remote-speaker-identity-backend-qualification-v1"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict), path
    return value


def run(*args: str, expect: int = 0) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [str(PYTHON), str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != expect:
        raise AssertionError(
            f"command exited {result.returncode}, expected {expect}: {' '.join(args)}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def assert_public_safe(value: Any) -> None:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True)
    for marker in (
        "/Users/", "/home/", "private_seed", "system_voice", "hard_vocabulary",
        "enrollment_scripts", '"text"', "audio_paths",
    ):
        assert marker not in encoded, marker


def check_fixture() -> None:
    with tempfile.TemporaryDirectory(prefix="_identity-backend-v1-", dir=ROOT / "sessions") as temporary:
        out = Path(temporary)
        manifest = out / "tracked.json"
        common = (
            "--fixture-mode", "--out-dir", str(out), "--write-manifest", str(manifest)
        )
        run("all", *common)
        report = read_json(out / "remote_speaker_identity_backend_qualification_report.json")
        assert report["decision"] == "PROMOTE_LAB_IDENTITY_CANDIDATE"
        assert report["selected_candidate_id"] == "speechbrain_ecapa_voxceleb_candidate"
        assert all(report["promotion_gates"].values())
        assert report["hard_v4"]["candidate"]["metrics"]["word_conservation"] is True
        assert report["hard_v4"]["candidate"]["metrics"]["mixed_words"] > 0
        assert report["hard_v4"]["candidate"]["metrics"]["open_set_false_attributions"] == 0
        ledger = read_json(out / "private/hard-v4/hard_v4_opening_ledger.json")
        assert ledger["open_count"] == 1
        run("evaluate-hard", *common)
        assert read_json(out / "private/hard-v4/hard_v4_opening_ledger.json")["open_count"] == 1
        run("replay", *common)
        replay = read_json(out / "replay_report.json")
        assert replay["byte_identical"] is True
        assert replay["hard_v4_open_count"] == 1
        assert_public_safe(read_json(out / "hard_v4_public_manifest.json"))
        assert_public_safe(read_json(manifest))


def check_policy() -> None:
    policy = read_json(POLICY)
    assert policy["schema"] == "murmurmark.stronger_remote_speaker_identity_backend_qualification_policy/v1"
    assert len(policy["shortlist"]) == 2
    assert {row["family"] for row in policy["shortlist"]} == {"wavlm_xvector", "ecapa_tdnn"}
    assert policy["calibration"]["maximum_selected_candidates"] == 1
    assert policy["promotion_gates"]["minimum_bcubed_f1"] == 0.85
    assert policy["promotion_gates"]["minimum_pairwise_precision"] == 0.99
    assert policy["promotion_gates"]["minimum_known_speaker_recall"] == 0.8
    assert policy["promotion_gates"]["maximum_open_set_false_attributions"] == 0
    for row in policy["upstream_guards"]:
        path = ROOT / row["path"]
        assert path.is_file(), path
        assert sha256(path) == row["sha256"], path
    for row in policy["development_corpora"]:
        path = ROOT / row["frozen_manifest"]
        assert sha256(path) == row["frozen_manifest_sha256"], path
        if row.get("opening_ledger"):
            ledger = ROOT / row["opening_ledger"]
            assert sha256(ledger) == row["opening_ledger_sha256"], ledger


def check_real_outputs() -> None:
    assert TRACKED.is_file(), TRACKED
    tracked = read_json(TRACKED)
    assert tracked["schema"] == "murmurmark.stronger_remote_speaker_identity_backend_qualification_manifest/v1"
    assert tracked["hard_v4_open_count"] == 1
    assert tracked["production_mutated"] is False
    assert tracked["private_values_excluded"] is True
    for key in ("policy", "hard_v4_public_manifest", "qualification_report", "replay_report"):
        row = tracked[key]
        assert row is not None, key
        path = ROOT / row["path"]
        assert path.is_file(), path
        assert path.stat().st_size == row["bytes"], path
        assert sha256(path) == row["sha256"], path
    report = read_json(REAL_OUT / "remote_speaker_identity_backend_qualification_report.json")
    assert report["decision"] in {
        "PROMOTE_LAB_IDENTITY_CANDIDATE", "DO_NOT_PROMOTE_IDENTITY_BACKEND"
    }
    assert report["hard_v4_open_count"] == 1
    assert report["safety"]["production_mutated"] is False
    assert report["safety"]["coverage_v3_mutated"] is False
    assert report["safety"]["synthetic_identity_transferred_to_real_sessions"] is False
    replay = read_json(REAL_OUT / "replay_report.json")
    assert replay["decision"] == "DETERMINISTIC_REPLAY_VERIFIED"
    assert replay["byte_identical"] is True
    assert_public_safe(read_json(REAL_OUT / "hard_v4_public_manifest.json"))
    assert_public_safe(tracked)


def main() -> int:
    check_policy()
    check_fixture()
    if TRACKED.is_file():
        check_real_outputs()
    print("stronger remote speaker identity backend qualification v1 checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
