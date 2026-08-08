#!/usr/bin/env python3
"""Check Segment-Context Remote Speaker Attribution v1 contracts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv/bin/python"
POLICY = ROOT / "policies/segment-context-remote-speaker-attribution-v1.json"
FREEZER = ROOT / "scripts/freeze-remote-speaker-hard-v3.py"
EVALUATOR = ROOT / "scripts/evaluate-segment-context-remote-speaker-attribution-v1.py"
TRUTH_LAB = ROOT / "scripts/controlled-remote-speaker-truth-lab-v1.py"
HARD_V2_FREEZER = ROOT / "scripts/freeze-remote-speaker-hard-v2.py"
REAL_OUT = ROOT / "sessions/_reports/segment-context-remote-speaker-attribution-v1"
TRACKED = ROOT / "docs/testing/segment-context-remote-speaker-attribution-v1-manifest.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    assert isinstance(value, dict), path
    return value


def run(*args: str, expect: int = 0) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [str(PYTHON), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != expect:
        raise AssertionError(
            f"command exit {result.returncode}, expected {expect}: {' '.join(args)}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def assert_public_safe(value: Any) -> None:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True)
    forbidden = (
        "/Users/",
        "/home/",
        "system_voice",
        "private_seed",
        "vocabulary",
        "hard_vocabulary",
        "enrollment_scripts",
    )
    for marker in forbidden:
        assert marker not in encoded, marker


def fixture_args(truth: Path, duration: Path, out: Path) -> tuple[str, ...]:
    return (
        "--fixture-mode",
        "--truth-lab-out",
        str(truth),
        "--duration-out",
        str(duration),
        "--out-dir",
        str(out),
    )


def check_fixture() -> None:
    with tempfile.TemporaryDirectory(prefix="_segment-context-v1-", dir=ROOT / "sessions") as temp:
        root = Path(temp)
        truth = root / "truth"
        duration = root / "duration"
        out = root / "out"

        run(str(TRUTH_LAB), "build", "--fixture-mode", "--out-dir", str(truth))
        run(str(HARD_V2_FREEZER), "freeze", "--fixture-mode", "--out-dir", str(duration))
        run(
            str(HARD_V2_FREEZER),
            "public-manifest",
            "--fixture-mode",
            "--out-dir",
            str(duration),
        )
        hard_args = ("--fixture-mode", "--duration-out", str(duration), "--out-dir", str(out))
        run(str(FREEZER), "freeze", *hard_args)
        run(str(FREEZER), "replay", *hard_args)
        run(str(FREEZER), "public-manifest", *hard_args)

        hard_public = read_json(out / "hard_v3_public_manifest.json")
        assert hard_public["decision"] == "HARD_V3_FROZEN_UNOPENED"
        assert hard_public["scenario_count"] == 5
        assert hard_public["word_count"] == 197
        assert hard_public["boundary_count"] == 22
        assert hard_public["enrolled_speaker_count"] == 4
        assert hard_public["open_set_speaker_count"] == 2
        assert hard_public["maximum_reconstruction_sample_error"] == 0
        assert hard_public["scripts_disjoint_from_truth_lab_v1"] is True
        assert hard_public["scripts_disjoint_from_hard_v2"] is True
        assert hard_public["renderer_voices_disjoint_from_truth_lab_v1"] is True
        assert hard_public["renderer_voices_disjoint_from_hard_v2"] is True
        assert_public_safe(hard_public)

        common = fixture_args(truth, duration, out)
        run(str(EVALUATOR), "develop", *common)
        development = read_json(out / "development_report.json")
        assert development["decision"] == "CANDIDATE_SELECTED_ON_DEVELOPMENT"
        assert len(development["topologies"]) == 3
        assert development["hard_v3"]["opened"] is False
        assert development["hard_v3"]["used_for_selection"] is False
        assert not (out / "private/hard-v3/hard_v3_opening_ledger.json").exists()
        assert_public_safe(development)

        run(str(EVALUATOR), "evaluate-hard", *common)
        report = read_json(out / "segment_context_remote_speaker_attribution_report.json")
        assert report["decision"] in {
            "PROMOTE_LAB_CANDIDATE",
            "DO_NOT_PROMOTE_SEGMENT_CONTEXT",
        }
        assert report["hard_v3"]["decision_open_count"] == 1
        assert report["hard_v3"]["used_for_selection"] is False
        metrics = report["hard_v3_metrics"]
        assert metrics["word_count"] == metrics["prediction_count"] == 197
        assert metrics["word_conservation"] is True
        assert metrics["direct_truth_coverage"] is True
        assert metrics["mixed_safely_marked"] == metrics["mixed_words"]
        assert "known_attribution_coverage" in metrics
        assert "known_speaker_recall" in metrics
        assert report["production"]["changed"] is False
        assert_public_safe(report)

        second = run(str(EVALUATOR), "evaluate-hard", *common, expect=1)
        assert "hard_v3_decision_opening_already_consumed" in second.stderr
        run(str(EVALUATOR), "replay", *common)
        replay = read_json(out / "replay_report.json")
        assert replay["decision"] == "DETERMINISTIC_REPLAY_VERIFIED"
        assert replay["decision_open_count"] == 1
        assert all(replay["matches"].values())
        assert_public_safe(replay)

        candidate_path = out / "private/candidate_freeze.json"
        original = candidate_path.read_bytes()
        candidate = read_json(candidate_path)
        candidate["selected_config"]["tampered"] = True
        candidate_path.write_text(json.dumps(candidate, sort_keys=True))
        tampered = run(str(EVALUATOR), "replay", *common, expect=1)
        assert "segment_context_replay_mismatch" in tampered.stderr
        candidate_path.write_bytes(original)


def check_real() -> None:
    if not TRACKED.is_file():
        return
    tracked = read_json(TRACKED)
    assert tracked["schema"] == "murmurmark.segment_context_remote_speaker_attribution_tracked_manifest/v1"
    tracked_paths = {
        "policy": POLICY,
        "freezer": FREEZER,
        "evaluator": EVALUATOR,
        "checker": Path(__file__).resolve(),
    }
    local_paths = {
        "hard_frozen_manifest": REAL_OUT / "private/hard-v3/frozen_manifest.json",
        "hard_public_manifest": REAL_OUT / "hard_v3_public_manifest.json",
        "development_report": REAL_OUT / "development_report.json",
        "candidate_freeze": REAL_OUT / "private/candidate_freeze.json",
        "opening_ledger": REAL_OUT / "private/hard-v3/hard_v3_opening_ledger.json",
        "report": REAL_OUT / "segment_context_remote_speaker_attribution_report.json",
        "replay": REAL_OUT / "replay_report.json",
    }
    for key, path in tracked_paths.items():
        expected = tracked["artifacts"][key]
        assert expected["path"] == str(path.relative_to(ROOT))
        assert path.stat().st_size == expected["bytes"]
        assert sha256(path) == expected["sha256"]
    if not all(path.is_file() for path in local_paths.values()):
        print("local segment-context hard-v3 verification skipped")
        return
    for key, path in local_paths.items():
        expected = tracked["artifacts"][key]
        assert expected["path"] == str(path.relative_to(ROOT))
        assert path.stat().st_size == expected["bytes"]
        assert sha256(path) == expected["sha256"]

    report = read_json(local_paths["report"])
    replay = read_json(local_paths["replay"])
    ledger = read_json(local_paths["opening_ledger"])
    assert report["decision"] == tracked["decision"] == "DO_NOT_PROMOTE_SEGMENT_CONTEXT"
    assert report["selected_topology"] == "conservative_dual_backend_context_fusion"
    assert report["hard_v3"]["decision_open_count"] == ledger["decision_open_count"] == 1
    assert report["hard_v3"]["used_for_selection"] is False
    assert report["hard_v3_metrics"]["word_conservation"] is True
    assert report["hard_v3_metrics"]["direct_truth_coverage"] is True
    assert report["production"]["changed"] is False
    assert replay["decision"] == "DETERMINISTIC_REPLAY_VERIFIED"
    assert all(replay["matches"].values())
    assert_public_safe(report)
    assert_public_safe(replay)


def main() -> int:
    assert PYTHON.is_file(), PYTHON
    assert POLICY.is_file() and FREEZER.is_file() and EVALUATOR.is_file()
    policy = read_json(POLICY)
    assert policy["schema"] == "murmurmark.segment_context_remote_speaker_attribution_policy/v1"
    assert len(policy["topologies"]) == 3
    assert policy["selection"]["source"] == "truth_lab_v1_and_open_hard_v2_development_only"
    assert policy["selection"]["truth_allowed_for_boundary_detection"] is False
    assert policy["selection"]["unknown_policy"] == "fail_open"
    assert policy["production_boundaries"]["selected_transcript_changed"] is False
    check_fixture()
    check_real()
    print("segment-context remote speaker attribution v1 checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
