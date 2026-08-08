#!/usr/bin/env python3
"""Check ECAPA Remote Speaker Shadow Qualification v1 contracts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv/bin/python"
SCRIPT = ROOT / "scripts/qualify-ecapa-remote-speaker-shadow-v1.py"
POLICY = ROOT / "policies/ecapa-remote-speaker-shadow-qualification-v1.json"
TRACKED = ROOT / "docs/testing/ecapa-remote-speaker-shadow-qualification-v1-manifest.json"
REAL_OUT = ROOT / "sessions/_reports/ecapa-remote-speaker-shadow-qualification-v1"


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
    for marker in ("/Users/", "/home/", '"text"', "transcript_fragment", "reviewer_id", "reference_speaker_", "private/"):
        assert marker not in encoded, marker


def check_policy() -> None:
    policy = read_json(POLICY)
    assert policy["schema"] == "murmurmark.ecapa_remote_speaker_shadow_qualification_policy/v1"
    assert policy["candidate"]["minimum_similarity"] == 0.5
    assert policy["candidate"]["minimum_margin"] == 0.3
    assert policy["technical_gates"]["minimum_recovered_word_ratio"] == 0.2
    assert policy["technical_gates"]["minimum_recovered_seconds_ratio"] == 0.2
    assert policy["promotion_evidence_gates"]["minimum_human_reviewed_proposal_words"] == 50
    assert policy["terminal_decisions"] == [
        "PROMOTE_REAL_IDENTITY_CANDIDATE",
        "DO_NOT_PROMOTE_REAL_IDENTITY",
        "REFERENCE_INSUFFICIENT",
    ]
    for row in policy["frozen_inputs"]:
        path = ROOT / row["path"]
        assert path.is_file(), path
        assert sha256(path) == row["sha256"], path


def check_fixture(case: str, expected: str) -> None:
    with tempfile.TemporaryDirectory(prefix="_ecapa-shadow-v1-", dir=ROOT / "sessions") as temporary:
        out = Path(temporary)
        tracked = out / "tracked.json"
        common = (
            "--fixture-mode", "--fixture-case", case,
            "--out-dir", str(out), "--write-manifest", str(tracked),
        )
        run("all", *common)
        report = read_json(out / "ecapa_remote_speaker_shadow_qualification_report.json")
        assert report["decision"] == expected
        assert report["summary"]["recovered_words"] >= 90
        assert report["technical_gates"]["exact_word_and_timestamp_conservation"] is True
        assert report["technical_gates"]["existing_labels_unchanged"] is True
        assert report["safety"]["production_mutated"] is False
        assert report["safety"]["human_names_inferred"] is False
        assert report["safety"]["cross_session_voice_linking"] is False
        replay = read_json(out / "replay_report.json")
        assert replay["byte_identical"] is True
        assert replay["decision"] == "DETERMINISTIC_REPLAY_VERIFIED"
        word_rows = (out / "private/word_shadow_decisions.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(word_rows) == 100
        assert_public_safe(read_json(out / "input_manifest.public.json"))
        assert_public_safe(read_json(tracked))


def check_fail_closed() -> None:
    with tempfile.TemporaryDirectory(prefix="_ecapa-shadow-policy-", dir=ROOT / "sessions") as temporary:
        root = Path(temporary)
        policy = read_json(POLICY)
        policy["frozen_inputs"][0]["path"] = "missing-shadow-input.json"
        bad = root / "policy.json"
        bad.write_text(json.dumps(policy, ensure_ascii=False), encoding="utf-8")
        result = run("preflight", "--policy", str(bad), expect=1)
        assert "frozen_input_stale" in result.stderr


def check_real_outputs() -> None:
    assert TRACKED.is_file(), TRACKED
    tracked = read_json(TRACKED)
    assert tracked["schema"] == "murmurmark.ecapa_remote_speaker_shadow_qualification_manifest/v1"
    assert tracked["decision"] in {
        "PROMOTE_REAL_IDENTITY_CANDIDATE",
        "DO_NOT_PROMOTE_REAL_IDENTITY",
        "REFERENCE_INSUFFICIENT",
    }
    assert tracked["production_mutated"] is False
    assert tracked["private_values_excluded"] is True
    for key in ("policy", "public_input_manifest", "qualification_report", "replay_report"):
        row = tracked[key]
        path = ROOT / row["path"]
        assert path.is_file(), path
        assert path.stat().st_size == row["bytes"], path
        assert sha256(path) == row["sha256"], path
    report = read_json(REAL_OUT / "ecapa_remote_speaker_shadow_qualification_report.json")
    assert report["input"]["sessions"] == 6
    assert report["input"]["review_items"] == 278
    assert report["input"]["residual_words"] == 851
    assert report["input"]["human_reviewed_items"] == 0
    assert report["promotion_evidence_gates"]["minimum_human_reviewed_proposal_words"] is False
    assert report["safety"]["coverage_v3_mutated"] is False
    assert report["safety"]["selected_transcript_mutated"] is False
    assert read_json(REAL_OUT / "replay_report.json")["byte_identical"] is True
    assert_public_safe(read_json(REAL_OUT / "input_manifest.public.json"))
    assert_public_safe(tracked)


def main() -> int:
    check_policy()
    check_fixture("promote", "PROMOTE_REAL_IDENTITY_CANDIDATE")
    check_fixture("reference-insufficient", "REFERENCE_INSUFFICIENT")
    check_fixture("technical-fail", "DO_NOT_PROMOTE_REAL_IDENTITY")
    check_fail_closed()
    if TRACKED.is_file():
        check_real_outputs()
    print("ecapa remote speaker shadow qualification v1 checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
