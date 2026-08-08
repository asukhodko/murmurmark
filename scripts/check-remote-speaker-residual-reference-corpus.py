#!/usr/bin/env python3
"""Contract checks for Remote Speaker Residual Reference Corpus v1."""

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
SCRIPT = ROOT / "scripts/report-remote-speaker-residual-reference-corpus.py"


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical(value))


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(compact(row) for row in rows), encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_identity(path: Path, session: Path) -> dict[str, Any]:
    return {
        "path": str(path.relative_to(session)),
        "bytes": path.stat().st_size,
        "sha256": sha(path),
    }


def artifact_manifest(directory: Path, schema: str, names: list[str]) -> None:
    write_json(
        directory / "artifact_manifest.json",
        {
            "schema": schema,
            "artifacts": {name: sha(directory / name) for name in names},
        },
    )


def run(args: list[str], expected: int = 0) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != expected:
        raise AssertionError(
            f"unexpected exit {result.returncode}, expected {expected}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def word(
    uid: str,
    index: int,
    start: float,
    end: float,
    speaker: str | None,
) -> dict[str, Any]:
    return {
        "schema": "murmurmark.remote_speaker_word/v3",
        "word_id": f"{uid}:word:{index:04d}",
        "utterance_id": uid,
        "role": "remote",
        "text": f"word{index}",
        "normalized": f"word{index}",
        "start": start,
        "end": end,
        "start_char": 0,
        "end_char": 4,
        "coverage_weight_sec": 1.0 if speaker is None else end - start,
        "speaker_id": speaker,
        "speaker_label": speaker,
        "status": "attributed" if speaker else "unknown",
        "reason": "fixture_known" if speaker else "margin_below_threshold",
        "v3_reason": "fixture_known" if speaker else "margin_below_threshold",
    }


def build_session(session: Path, index: int) -> None:
    session.mkdir(parents=True)
    audio_path = session / "derived/asr/remote.wav"
    sample_rate = 16000
    time = np.arange(sample_rate * 32, dtype=np.float32) / sample_rate
    audio = 0.08 * np.sin(2 * np.pi * (180 + index * 5) * time)
    audio_path.parent.mkdir(parents=True)
    sf.write(audio_path, audio, sample_rate, subtype="PCM_16")
    for raw in (session / "audio/mic/000001.caf", session / "audio/remote/000001.caf"):
        raw.parent.mkdir(parents=True, exist_ok=True)
        raw.write_bytes(f"fixture-raw-{session.name}-{raw.parent.name}".encode())
    dialogue = session / "dialogue.json"
    write_json(dialogue, {"schema": "fixture", "utterances": []})

    speaker_1 = "remote_speaker_01"
    speaker_2 = "remote_speaker_02"
    words: list[dict[str, Any]] = []
    for speaker_number, speaker in enumerate((speaker_1, speaker_2), 1):
        for exemplar_index in range(2):
            start = 1.0 + speaker_number * 2.5 + exemplar_index * 5.0
            words.append(word(f"known_{speaker_number}_{exemplar_index}", 1, start, start + 2.0, speaker))
    decisions: list[dict[str, Any]] = []
    for block, speaker in enumerate((speaker_1, speaker_2)):
        uid = f"unknown_{block}"
        base = 16.0 + block * 6.0
        for offset in range(5):
            row = word(uid, offset + 1, base + offset * 0.3, base + offset * 0.3 + 0.2, None)
            words.append(row)
            decisions.append(
                {
                    "schema": "murmurmark.independent_remote_speaker_decision/v1",
                    "word_id": row["word_id"],
                    "utterance_id": uid,
                    "start": row["start"],
                    "end": row["end"],
                    "coverage_weight_sec": 1.0,
                    "baseline_cause": "margin_below_threshold",
                    "outcome": "attributed",
                    "speaker_id": speaker,
                    "reason": "fixture_wavlm",
                }
            )
    words.sort(key=lambda row: (row["start"], row["word_id"]))

    v3 = session / "derived/audit/remote-speaker-coverage-v3"
    write_jsonl(v3 / "word_attribution.jsonl", words)
    write_json(
        v3 / "speaker_map.json",
        {
            "schema": "murmurmark.remote_speaker_map/v3",
            "speakers": [
                {"speaker_id": speaker_1},
                {"speaker_id": speaker_2},
            ],
        },
    )
    write_json(
        v3 / "report.json",
        {
            "schema": "murmurmark.remote_speaker_coverage_report/v3",
            "decision": "PUBLISH_EVIDENCE",
            "source": {
                "remote_audio": source_identity(audio_path, session),
                "dialogue": source_identity(dialogue, session),
            },
            "summary": {},
        },
    )
    artifact_manifest(
        v3,
        "murmurmark.remote_speaker_coverage_artifact_manifest/v3",
        ["word_attribution.jsonl", "speaker_map.json", "report.json"],
    )

    independent = session / "derived/audit/independent-remote-speaker-evidence-v1"
    write_jsonl(independent / "residual_decisions.jsonl", decisions)
    write_json(
        independent / "report.json",
        {
            "schema": "murmurmark.independent_remote_speaker_evidence_report/v1",
            "decision": "PUBLISH_EVIDENCE",
            "status": "completed",
        },
    )
    artifact_manifest(
        independent,
        "murmurmark.independent_remote_speaker_artifact_manifest/v1",
        ["residual_decisions.jsonl", "report.json"],
    )


def policy(path: Path, sessions: list[str]) -> None:
    write_json(
        path,
        {
            "schema": "murmurmark.remote_speaker_residual_reference_policy/v1",
            "state": "fixture",
            "corpus": {
                "sessions": sessions,
                "expected_residual_words": 60,
                "expected_residual_seconds": 60.0,
                "expected_referenceable_word_seconds": 60.0,
                "expected_unaligned_residual_seconds": 0.0,
                "expected_wavlm_proposal_words": 60,
                "expected_wavlm_proposal_seconds": 60.0,
                "expected_review_items": 12,
            },
            "review_pack": {
                "join_gap_sec": 0.35,
                "target_item_sec": 10.0,
                "clip_padding_sec": 0.1,
                "exemplars_per_speaker": 2,
                "minimum_exemplar_sec": 1.25,
                "maximum_exemplar_sec": 3.0,
                "audio_subtype": "PCM_16",
            },
            "truth": {
                "eligible_grades": ["human_reviewed", "exact_scripted"],
                "special_outcomes": ["unknown_speaker", "mixed", "unusable"],
                "exact_scripted_sessions": sessions[:-1],
            },
            "readiness": {
                "required_reviewed_proposal_words": 60,
                "required_direct_reference_proposal_words": 60,
                "minimum_attributable_proposal_words": 20,
                "minimum_candidate_precision": 0.98,
                "require_all_residual_words_once": True,
                "require_blind_prediction_separation": True,
                "require_selected_transcript_unchanged": True,
                "require_raw_audio_unchanged": True,
            },
        },
    )


def check() -> None:
    with tempfile.TemporaryDirectory(
        prefix="_remote-reference-v1-", dir=ROOT / "sessions"
    ) as temporary:
        root = Path(temporary)
        sessions_root = root / "sessions"
        session_ids = [f"fixture-{index + 1}" for index in range(6)]
        for index, session_id in enumerate(session_ids):
            build_session(sessions_root / session_id, index)
        policy_path = root / "policy.json"
        out_dir = root / "report"
        frozen = root / "frozen.json"
        policy(policy_path, session_ids)
        common = [
            "--policy", str(policy_path),
            "--sessions-root", str(sessions_root),
            "--out-dir", str(out_dir),
        ]

        run(["build", *common, "--write-manifest", str(frozen)])
        report = read_json(out_dir / "remote_speaker_residual_reference_report.json")
        assert report["decision"] == "REFERENCE_INSUFFICIENT"
        assert report["summary"]["residual_words"] == 60
        assert report["summary"]["wavlm_proposal_words"] == 60
        assert report["summary"]["review_items"] == 12
        items = read_jsonl(out_dir / "private/review_items.jsonl")
        rendered_items = canonical(items).decode()
        assert "candidate_speaker_id" not in rendered_items
        assert "prediction" not in rendered_items
        assert sum(len(row["word_ids"]) for row in items) == 60
        next_result = run(["next", *common])
        assert "candidate" not in next_result.stdout.casefold()
        assert "sealed" not in next_result.stdout.casefold()

        for index, item in enumerate(items):
            expected = "remote_speaker_01" if item["start"] < 20.0 else "remote_speaker_02"
            trust = "human_reviewed" if index == 0 or item["session_id"] == session_ids[-1] else "exact_scripted"
            run(
                [
                    "grade", item["item_id"], *common,
                    "--outcome", expected,
                    "--truth-grade", trust,
                    "--reviewer-id", "fixture-reviewer",
                    "--reviewed-at", "2026-01-01T00:00:00Z",
                ]
            )
        report = read_json(out_dir / "remote_speaker_residual_reference_report.json")
        assert report["decision"] == "REFERENCE_READY", report
        assert report["summary"]["candidate_precision"] == 1.0
        run(["build", *common, "--write-manifest", str(frozen)])
        run(["replay", *common, "--frozen-manifest", str(frozen)])

        first = items[0]
        wrong = "remote_speaker_02" if first["start"] < 20.0 else "remote_speaker_01"
        run(
            [
                "grade", first["item_id"], *common,
                "--outcome", wrong,
                "--truth-grade", "human_reviewed",
                "--reviewed-at", "2026-01-01T00:00:00Z",
            ]
        )
        assert read_json(out_dir / "remote_speaker_residual_reference_report.json")["decision"] == (
            "REFERENCE_INSUFFICIENT"
        )
        run(["replay", *common, "--frozen-manifest", str(frozen)], expected=2)

        public = canonical(read_json(out_dir / "remote_speaker_residual_reference_report.json")).decode()
        assert "word1" not in public
        assert "fixture-reviewer" not in public
        assert "/Users/" not in public

        source = sessions_root / session_ids[0] / "derived/asr/remote.wav"
        original = source.read_bytes()
        source.write_bytes(original + b"stale")
        run(["replay", *common], expected=2)
        source.write_bytes(original)


if __name__ == "__main__":
    check()
    print("remote speaker residual reference corpus checks passed")
