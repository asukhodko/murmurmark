#!/usr/bin/env python3
"""Regression checks for Remote Unknown Evidence Recovery v1."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv/bin/python"
AUDIT = ROOT / "scripts/audit-remote-unknown-evidence-recovery-v1.py"
CORPUS = ROOT / "scripts/report-remote-unknown-evidence-recovery-v1-corpus.py"
INDEPENDENT_CHECK = ROOT / "scripts/check-independent-remote-speaker-evidence-v1.py"
SNAPSHOT = ROOT / "docs/testing/remote-unknown-evidence-recovery-v1-snapshot.json"
REAL_REPORT = (
    ROOT
    / "sessions/_reports/remote-unknown-evidence-recovery-v1"
    / "remote_unknown_evidence_recovery_corpus_report.json"
)


def load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot_load:{path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


INDEPENDENT = load(INDEPENDENT_CHECK, "murmurmark_check_independent_for_unknown_recovery")
RECOVERY = load(AUDIT, "murmurmark_unknown_recovery_under_test")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def run(args: list[str], expected: int = 0) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        [str(PYTHON), *args], cwd=ROOT, text=True, capture_output=True, check=False
    )
    if completed.returncode != expected:
        raise AssertionError(
            f"unexpected exit {completed.returncode}, expected {expected}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return completed


def build_fixture(root: Path) -> tuple[Path, Path, Path]:
    session = root / "session"
    v2 = INDEPENDENT.V3.build_v2_fixture(session)
    INDEPENDENT.refresh_v2_source(session, v2, INDEPENDENT.enrollment_rows())
    run([str(INDEPENDENT.V3_AUDIT), str(session)])
    fixture = root / "embeddings.json"
    INDEPENDENT.embedding_fixture(fixture)
    run(
        [
            str(INDEPENDENT.AUDIT),
            str(session),
            "--embedding-fixture",
            str(fixture),
        ]
    )
    return (
        session,
        session / INDEPENDENT.V3_DIR,
        session / INDEPENDENT.INDEPENDENT_DIR,
    )


def check_structural_conflict() -> None:
    unit = {
        "speaker_id": "remote_speaker_01",
        "utterance_id": "utt_1",
        "word_ids": ["w2"],
    }
    words = [
        {"word_id": "w1", "utterance_id": "utt_1", "start": 0.0, "end": 1.0, "speaker_id": "remote_speaker_02"},
        {"word_id": "w2", "utterance_id": "utt_1", "start": 1.0, "end": 2.0, "speaker_id": None},
    ]
    policy = read_json(ROOT / "policies/remote-unknown-evidence-recovery-v1.json")
    evidence = RECOVERY.structural_evidence(
        unit,
        words,
        {"utt_1": words},
        {
            "utt_1": {
                "speaker_id": "remote_speaker_02",
                "status": "attributed",
                "overlap_utterance_ids": [],
            }
        },
        policy,
    )
    assert not evidence["supports"]
    assert "v1_speaker_disagreement" in evidence["conflicts"]
    assert "same_utterance_anchor_disagreement" in evidence["conflicts"]


def check_audit(root: Path) -> None:
    session, coverage, independent = build_fixture(root)
    baseline_words = read_jsonl(coverage / "word_attribution.jsonl")
    baseline_by_id = {str(row["word_id"]): row for row in baseline_words}
    run([str(AUDIT), str(session)])
    out = session / "derived/audit/remote-unknown-evidence-recovery-v1"
    report = read_json(out / "report.json")
    assert report["decision"] == "PUBLISH_SHADOW_EVIDENCE"
    assert report["summary"]["recovered_words"] == 1
    assert report["summary"]["remaining_unknown_words"] == 1
    assert all(report["gates"].values())
    assert report["gates"]["utterance_identity_exact"]
    decisions = read_jsonl(out / "recovery_decisions.jsonl")
    assert len(decisions) == report["summary"]["baseline_unknown_words"]
    accepted = [row for row in decisions if row["outcome"] == "attributed"]
    assert len(accepted) == 1
    assert accepted[0]["evidence"]["independent_wavlm"]["decision"]["speaker_id"]
    assert accepted[0]["evidence"]["structural"]["supports"]
    output = {str(row["word_id"]): row for row in read_jsonl(out / "word_attribution.jsonl")}
    for word_id, baseline in baseline_by_id.items():
        row = output[word_id]
        for field in ("text", "normalized", "start", "end", "start_char", "end_char"):
            assert row.get(field) == baseline.get(field)
        if baseline.get("speaker_id"):
            assert row.get("speaker_id") == baseline.get("speaker_id")
    protected = [
        row
        for row in baseline_words
        if row.get("v3_reason") in {"protected_remote_overlap", "conflicting_frame_speakers"}
    ]
    assert protected
    assert all(output[str(row["word_id"])].get("speaker_id") is None for row in protected)
    assert "Shadow evidence only" in (out / "transcript.rich.shadow.md").read_text(encoding="utf-8")
    run([str(AUDIT), str(session), "--verify-only"])

    replay = session / "derived/audit/remote-unknown-evidence-recovery-v1-replay"
    run([str(AUDIT), str(session), "--out-dir", str(replay)])
    for name in (
        "recovery_decisions.jsonl",
        "word_attribution.jsonl",
        "utterance_attribution.jsonl",
        "transcript.rich.shadow.json",
        "report.json",
    ):
        assert (out / name).read_bytes() == (replay / name).read_bytes()

    fallback = session / "derived/audit/remote-unknown-evidence-recovery-v1-fallback"
    run(
        [
            str(AUDIT),
            str(session),
            "--independent-dir",
            str(root / "missing-independent"),
            "--out-dir",
            str(fallback),
        ]
    )
    fallback_report = read_json(fallback / "report.json")
    assert fallback_report["decision"] == "FALLBACK_COVERAGE_V3"
    assert fallback_report["summary"]["recovered_words"] == 0
    assert (fallback / "word_attribution.jsonl").read_bytes() == (
        coverage / "word_attribution.jsonl"
    ).read_bytes()
    assert (fallback / "transcript.rich.shadow.json").read_bytes() == (
        coverage / "transcript.rich.shadow.json"
    ).read_bytes()
    run([str(AUDIT), str(session), "--out-dir", str(fallback), "--verify-only"])
    check_structural_conflict()


def check_snapshot() -> None:
    snapshot = read_json(SNAPSHOT)
    assert snapshot["schema"] == "murmurmark.remote_unknown_evidence_recovery_snapshot/v1"
    assert snapshot["decision"] in {"EVIDENCE_BOUND", "PROMOTE_REMOTE_UNKNOWN_RECOVERY"}
    assert snapshot["summary"]["frozen"]["baseline_unknown_words"] == 547
    assert snapshot["summary"]["held_out"]["baseline_unknown_words"] == 166
    assert snapshot["truth_evaluation"]["combined"]["wrong_speaker"] == 0
    if REAL_REPORT.is_file():
        run([str(CORPUS), "all", "--verify-existing"])


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="murmurmark-remote-unknown-recovery-v1-") as temp:
        check_audit(Path(temp))
    check_snapshot()
    print("remote unknown evidence recovery v1 checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
