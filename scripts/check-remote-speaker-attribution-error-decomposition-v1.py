#!/usr/bin/env python3
"""Check Remote Speaker Attribution Error Decomposition v1 contracts."""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv/bin/python"
ANALYZER = ROOT / "scripts/analyze-remote-speaker-attribution-errors-v1.py"
POLICY = ROOT / "policies/remote-speaker-attribution-error-decomposition-v1.json"
TRACKED = ROOT / "docs/testing/remote-speaker-attribution-error-decomposition-v1-manifest.json"
REAL_OUT = ROOT / "sessions/_reports/remote-speaker-attribution-error-decomposition-v1"


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


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def run(repo: Path, *args: str, expect: int = 0) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [str(PYTHON), str(ANALYZER), *args, "--repo-root", str(repo)],
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
    for marker in ("/Users/", "/home/", "private_seed", '"text"', '"voice"', "truth_path"):
        assert marker not in encoded, marker


def fixture_words(corpus_id: str, split: str) -> list[dict[str, Any]]:
    definitions = [
        ("speaker_a", "known_speaker", "event_a", 0.0, 0.5),
        ("speaker_a", "known_speaker", "event_a", 0.55, 1.0),
        ("speaker_b", "known_speaker", "event_b", 1.05, 1.5),
        ("speaker_b", "known_speaker", "event_b", 1.55, 2.0),
        (f"open_{corpus_id}", "open_set_speaker", "event_open", 2.05, 2.5),
        ("mixed", "mixed", "event_mixed", 2.45, 2.9),
    ]
    rows = []
    for index, (speaker, truth_class, event, start, end) in enumerate(definitions):
        word_id = f"{split}:{corpus_id}:word:{index:03d}"
        rows.append(
            {
                "schema": "murmurmark.controlled_remote_speaker_truth_word/v1",
                "split": split,
                "scenario_id": f"{corpus_id}_scenario",
                "event_id": f"{corpus_id}:{event}",
                "word_id": word_id,
                "speaker_id": speaker,
                "truth_class": truth_class,
                "truth_source": "exact_scripted",
                "text": f"token_{index}",
                "start": start,
                "end": end,
                "start_sample": int(start * 16000),
                "end_sample": int(end * 16000),
                "enrolled": truth_class == "known_speaker",
                "overlap_word_ids": [f"overlap:{index}"] if truth_class == "mixed" else [],
            }
        )
    return rows


def fixture_boundaries(corpus_id: str, split: str, words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pairs = ((1, 2), (3, 4))
    rows = []
    for index, (left, right) in enumerate(pairs):
        rows.append(
            {
                "schema": "murmurmark.controlled_remote_speaker_truth_boundary/v1",
                "split": split,
                "scenario_id": f"{corpus_id}_scenario",
                "boundary_id": f"{corpus_id}:boundary:{index:03d}",
                "kind": "internal_change",
                "evaluation": True,
                "time": words[right]["start"],
                "left_event_id": words[left]["event_id"],
                "right_event_id": words[right]["event_id"],
                "left_word_id": words[left]["word_id"],
                "right_word_id": words[right]["word_id"],
                "left_speaker_id": words[left]["speaker_id"],
                "right_speaker_id": words[right]["speaker_id"],
            }
        )
    return rows


def fixture_predictions(
    corpus_id: str,
    split: str,
    words: list[dict[str, Any]],
    track_id: str,
    explicit: bool,
) -> list[dict[str, Any]]:
    labels = ("speaker_b", "speaker_b", "speaker_a", "speaker_a", "speaker_a", "mixed")
    segments = (0, 0, 1, 1, 2, 3)
    rows = []
    for index, (word, label) in enumerate(zip(words, labels)):
        row: dict[str, Any] = {
            "schema": "murmurmark.fixture_remote_speaker_prediction/v1",
            "split": split,
            "scenario_id": word["scenario_id"],
            "word_id": word["word_id"],
            "track": track_id,
            "speaker_id": label,
            "reason": "fixture_identity_swap",
        }
        if explicit:
            row["segment_index"] = segments[index]
        rows.append(row)
    return rows


def build_fixture_corpus(
    repo: Path,
    corpus_id: str,
    split: str,
    expected_decision: str,
    *,
    ledger: bool,
    explicit_primary: bool,
    control: bool,
) -> dict[str, Any]:
    root = repo / corpus_id
    scenario = root / "private/truth/scenario"
    words_path = scenario / "truth_words.jsonl"
    boundaries_path = scenario / "truth_boundaries.jsonl"
    words = fixture_words(corpus_id, split)
    boundaries = fixture_boundaries(corpus_id, split, words)
    write_jsonl(words_path, words)
    write_jsonl(boundaries_path, boundaries)
    frozen = {
        "schema": "murmurmark.fixture_frozen_manifest/v1",
        "corpus_sha256": hashlib.sha256(corpus_id.encode()).hexdigest(),
        "artifacts": {
            "truth/scenario/truth_words.jsonl": sha256(words_path),
            "truth/scenario/truth_boundaries.jsonl": sha256(boundaries_path),
        },
    }
    write_json(root / "private/frozen_manifest.json", frozen)
    write_json(root / "report.json", {"decision": expected_decision})
    write_json(root / "replay.json", {"decision": "DETERMINISTIC_REPLAY_VERIFIED"})
    write_json(repo / f"tracked-{corpus_id}.json", {"schema": "fixture"})
    primary_id = f"{corpus_id}_primary"
    write_jsonl(
        root / "private/current.jsonl",
        fixture_predictions(corpus_id, split, words, primary_id, explicit_primary),
    )
    controls = []
    if control:
        write_jsonl(
            root / "private/control.jsonl",
            fixture_predictions(corpus_id, split, words, f"{corpus_id}_control", False),
        )
        controls.append(
            {
                "track_id": f"{corpus_id}_control",
                "predictions": "private/control.jsonl",
                "segment_key": "contiguous_prediction_label",
            }
        )
    spec: dict[str, Any] = {
        "corpus_id": corpus_id,
        "root": corpus_id,
        "split": split,
        "frozen_manifest": "private/frozen_manifest.json",
        "truth_words_glob": "private/truth/*/truth_words.jsonl",
        "truth_boundaries_glob": "private/truth/*/truth_boundaries.jsonl",
        "decision_report": "report.json",
        "expected_decision": expected_decision,
        "tracked_manifest": f"tracked-{corpus_id}.json",
        "replay_report": "replay.json",
        "primary_track": {
            "track_id": primary_id,
            "predictions": "private/current.jsonl",
            "segment_key": "explicit_segment_index" if explicit_primary else "contiguous_prediction_label",
        },
        "control_tracks": controls,
    }
    if ledger:
        write_json(
            root / "private/ledger.json",
            {"status": "completed", "decision_open_count": 1, "decision": expected_decision},
        )
        write_json(root / "private/candidate.json", {"decision": "FROZEN"})
        write_json(root / "private/spec.json", {"schema": "fixture"})
        spec.update(
            {
                "opening_ledger": "private/ledger.json",
                "candidate_freeze": "private/candidate.json",
                "private_spec": "private/spec.json",
            }
        )
    return spec


def check_fixture() -> None:
    with tempfile.TemporaryDirectory(prefix="_remote-error-decomposition-v1-", dir=ROOT / "sessions") as temp:
        repo = Path(temp)
        policy = read_json(POLICY)
        policy["corpora"] = [
            build_fixture_corpus(repo, "truth_v1", "hard", "DO_NOT_ADVANCE", ledger=False, explicit_primary=False, control=False),
            build_fixture_corpus(repo, "hard_v2", "hard_v2", "DO_NOT_PROMOTE_TOPOLOGY", ledger=True, explicit_primary=False, control=True),
            build_fixture_corpus(repo, "hard_v3", "hard_v3", "DO_NOT_PROMOTE_SEGMENT_CONTEXT", ledger=True, explicit_primary=True, control=True),
        ]
        policy["production_guards"] = ["guard-coverage.json", "guard-perfection.json"]
        write_json(repo / "guard-coverage.json", {"schema": "fixture_guard"})
        write_json(repo / "guard-perfection.json", {"schema": "fixture_guard"})
        write_json(repo / "policy.json", policy)

        common = ("--policy", "policy.json", "--out-dir", "out")
        run(repo, "freeze", *common)
        run(repo, "freeze", *common)
        run(repo, "analyze", *common)
        run(repo, "replay", *common)

        report = read_json(repo / "out/remote_speaker_attribution_error_decomposition_report.json")
        replay = read_json(repo / "out/replay_report.json")
        public_input = read_json(repo / "out/input_manifest.public.json")
        assert report["decision"] == "ADVANCE_STRONGER_SPEAKER_IDENTITY"
        assert report["routing_evidence"]["axis_gains"]["speaker_identity"] > report["routing_evidence"]["axis_gains"]["segmentation"]
        assert report["aggregate_primary"]["full_oracle_control"]["known_speaker_recall"] == 1.0
        assert report["aggregate_primary"]["full_oracle_control"]["boundary_recall"] == 1.0
        assert report["aggregate_primary"]["current"]["word_count"] == 18
        assert report["aggregate_primary"]["current"]["boundary_count"] == 6
        assert all(report["invariants"].values())
        assert replay["decision"] == "DETERMINISTIC_REPLAY_VERIFIED"
        assert all(replay["matches"].values())
        assert_public_safe(report)
        assert_public_safe(public_input)

        predictions = repo / "hard_v3/private/current.jsonl"
        original = predictions.read_bytes()
        predictions.write_bytes(original + b"\n")
        tampered = run(repo, "analyze", *common, expect=2)
        assert "frozen_input_changed" in tampered.stdout
        predictions.write_bytes(original)
        run(repo, "replay", *common)


def check_real() -> None:
    if not TRACKED.is_file():
        print("tracked remote attribution error decomposition manifest not written yet")
        return
    tracked = read_json(TRACKED)
    assert tracked["schema"] == "murmurmark.remote_speaker_attribution_error_decomposition_tracked_manifest/v1"
    tracked_paths = {
        "policy": POLICY,
        "analyzer": ANALYZER,
        "checker": Path(__file__).resolve(),
        "contract": ROOT / "docs/contracts/remote-speaker-attribution-error-decomposition-v1.md",
        "runbook": ROOT / "docs/runbooks/remote-speaker-attribution-error-decomposition-v1.md",
        "result": ROOT / "docs/testing/2026-08-08-remote-speaker-attribution-error-decomposition-v1.md",
    }
    for key, path in tracked_paths.items():
        expected = tracked["artifacts"][key]
        assert expected["path"] == str(path.relative_to(ROOT))
        assert expected["bytes"] == path.stat().st_size
        assert expected["sha256"] == sha256(path)

    local_paths = {
        "input_manifest": REAL_OUT / "private/input_manifest.json",
        "public_input_manifest": REAL_OUT / "input_manifest.public.json",
        "word_decomposition": REAL_OUT / "private/word_error_decomposition.jsonl",
        "boundary_decomposition": REAL_OUT / "private/boundary_error_decomposition.jsonl",
        "report": REAL_OUT / "remote_speaker_attribution_error_decomposition_report.json",
        "replay": REAL_OUT / "replay_report.json",
    }
    if not all(path.is_file() for path in local_paths.values()):
        print("local remote attribution error decomposition verification skipped")
        return
    for key, path in local_paths.items():
        expected = tracked["artifacts"][key]
        assert expected["path"] == str(path.relative_to(ROOT))
        assert expected["bytes"] == path.stat().st_size
        assert expected["sha256"] == sha256(path)

    report = read_json(local_paths["report"])
    replay = read_json(local_paths["replay"])
    assert report["decision"] == tracked["decision"] == "ADVANCE_STRONGER_SPEAKER_IDENTITY"
    assert report["aggregate_primary"]["current"]["word_count"] == 393
    assert report["aggregate_primary"]["current"]["boundary_count"] == 64
    assert report["routing_evidence"]["axis_gains"]["speaker_identity"] == 0.351382
    assert all(report["invariants"].values())
    assert report["production_changed"] is False
    assert replay["decision"] == "DETERMINISTIC_REPLAY_VERIFIED"
    assert all(replay["matches"].values())
    assert_public_safe(report)
    assert_public_safe(read_json(local_paths["public_input_manifest"]))


def main() -> int:
    assert PYTHON.is_file(), PYTHON
    assert ANALYZER.is_file() and POLICY.is_file()
    policy = read_json(POLICY)
    assert policy["schema"] == "murmurmark.remote_speaker_attribution_error_decomposition_policy/v1"
    assert [row["track_id"] for row in policy["oracle_matrix"]] == [
        "current",
        "oracle_boundaries_current_identity",
        "current_boundaries_oracle_identity",
        "overlap_open_set_oracle",
        "full_oracle_control",
    ]
    assert policy["scope"]["production_candidate_selection"] is False
    assert policy["scope"]["hard_sets_may_be_reopened"] is False
    check_fixture()
    check_real()
    print("remote speaker attribution error decomposition v1 checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
