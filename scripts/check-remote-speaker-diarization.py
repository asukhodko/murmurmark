#!/usr/bin/env python3
"""Smoke and safety checks for Remote Speaker Diarization v2."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

import numpy as np
import soundfile as sf


ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "scripts/audit-remote-speaker-diarization.py"
FIXTURE_SCHEMA = "murmurmark.remote_speaker_embedding_fixture/v2"


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fp(path: Path, session: Path) -> dict[str, object]:
    return {
        "path": str(path.relative_to(session)),
        "exists": True,
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
    }


def raw_segment(start: float, end: float, text: str) -> dict[str, object]:
    words = text.split()
    tokens: list[dict[str, object]] = []
    duration = end - start
    for index, word in enumerate(words):
        left = start + duration * index / max(1, len(words))
        right = start + duration * (index + 1) / max(1, len(words))
        tokens.append(
            {
                "text": (" " if index == 0 else " ") + word,
                "offsets": {"from": round(left * 1000), "to": round(right * 1000)},
            }
        )
    return {
        "text": " " + text,
        "offsets": {"from": round(start * 1000), "to": round(end * 1000)},
        "tokens": tokens,
    }


def build_fixture(root: Path) -> tuple[Path, Path]:
    session = root / "fixture-session"
    audio = session / "derived/asr/remote.wav"
    audio.parent.mkdir(parents=True, exist_ok=True)
    sf.write(audio, np.zeros(32_000, dtype=np.float32), 1_000)
    utterances = [
        {"id": "utt_me", "role": "local", "speaker_label": "Me", "start": 0.0, "end": 1.0, "text": "Привет."},
        {"id": "utt_a", "role": "remote", "speaker_label": "Colleagues", "start": 1.0, "end": 5.0, "text": "Альфа говорит спокойно"},
        {"id": "utt_b", "role": "remote", "speaker_label": "Colleagues", "start": 6.0, "end": 10.0, "text": "Бета отвечает уверенно"},
        {
            "id": "utt_mixed",
            "role": "remote",
            "speaker_label": "Colleagues",
            "start": 11.0,
            "end": 23.0,
            "text": "первый голос продолжает затем второй голос отвечает подробно",
        },
        {"id": "utt_overlap_a", "role": "remote", "speaker_label": "Colleagues", "start": 24.0, "end": 28.0, "text": "альфа одновременно"},
        {"id": "utt_overlap_b", "role": "remote", "speaker_label": "Colleagues", "start": 25.0, "end": 29.0, "text": "бета одновременно"},
    ]
    dialogue = session / "derived/transcript-simple/whisper-cpp/resolved/clean_dialogue.fixture.json"
    write_json(dialogue, {"schema": "fixture", "utterances": utterances})
    raw = session / "derived/transcript-simple/whisper-cpp/raw/remote.json"
    write_json(
        raw,
        {
            "transcription": [
                raw_segment(float(row["start"]), float(row["end"]), str(row["text"]))
                for row in utterances
                if row["role"] == "remote"
            ]
        },
    )
    v1 = session / "derived/audit/remote-speaker-evidence-v1"
    write_json(
        v1 / "report.json",
        {
            "schema": "murmurmark.remote_speaker_evidence_report/v1",
            "status": "completed",
            "decision": "PUBLISH_AUDIT_EVIDENCE",
            "source": {
                "session_id": session.name,
                "profile": "fixture",
                "dialogue": fp(dialogue, session),
                "remote_audio": fp(audio, session),
            },
        },
    )
    write_json(
        v1 / "speaker_map.json",
        {
            "speakers": [
                {"speaker_id": "remote_speaker_01"},
                {"speaker_id": "remote_speaker_02"},
            ]
        },
    )
    base = {
        "schema": "murmurmark.remote_utterance_attribution/v1",
        "start": 0,
        "end": 0,
        "speaker_label": "Colleagues",
        "status": "aggregate",
        "reason": "too_long_for_single_speaker_evidence",
    }
    write_jsonl(
        v1 / "utterance_attribution.jsonl",
        [
            {**base, "utterance_id": "utt_a", "speaker_id": "remote_speaker_01", "status": "attributed", "reason": "stable_anonymous_cluster"},
            {**base, "utterance_id": "utt_b", "speaker_id": "remote_speaker_02", "status": "attributed", "reason": "stable_anonymous_cluster"},
            {**base, "utterance_id": "utt_mixed", "speaker_id": None},
            {**base, "utterance_id": "utt_overlap_a", "speaker_id": "remote_speaker_01", "status": "attributed", "reason": "stable_anonymous_cluster"},
            {**base, "utterance_id": "utt_overlap_b", "speaker_id": "remote_speaker_02", "status": "attributed", "reason": "stable_anonymous_cluster"},
        ],
    )
    embeddings = {
        "seed:utt_a": [1.0, 0.0],
        "seed:utt_b": [0.0, 1.0],
        "seed:utt_overlap_a": [1.0, 0.0],
        "seed:utt_overlap_b": [0.0, 1.0],
        "frame:utt_a:0001": [1.0, 0.0],
        "frame:utt_b:0001": [0.0, 1.0],
        "frame:utt_mixed:0001": [1.0, 0.0],
        "frame:utt_mixed:0002": [0.8, 0.2],
        "frame:utt_mixed:0003": [0.0, 1.0],
        "frame:utt_overlap_a:0001": [1.0, 0.0],
        "frame:utt_overlap_b:0001": [0.0, 1.0],
        "whole:utt_a": [1.0, 0.0],
        "whole:utt_b": [0.0, 1.0],
        "whole:utt_mixed": [0.7, 0.7],
        "whole:utt_overlap_a": [1.0, 0.0],
        "whole:utt_overlap_b": [0.0, 1.0],
    }
    fixture = root / "embeddings.json"
    write_json(fixture, {"schema": FIXTURE_SCHEMA, "embeddings": embeddings})
    return session, fixture


def run(session: Path, fixture: Path, out_dir: str = "derived/audit/remote-speaker-diarization-v2") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(AUDIT),
            str(session),
            "--profile",
            "fixture",
            "--embedding-fixture",
            str(fixture),
            "--out-dir",
            out_dir,
            "--no-progress",
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="murmurmark-remote-diarization-") as temporary:
        session, fixture = build_fixture(Path(temporary))
        raw_hash = sha256(session / "derived/asr/remote.wav")
        dialogue_hash = sha256(
            session / "derived/transcript-simple/whisper-cpp/resolved/clean_dialogue.fixture.json"
        )
        run(session, fixture)
        out = session / "derived/audit/remote-speaker-diarization-v2"
        report = json.loads((out / "report.json").read_text())
        assert report["decision"] == "PUBLISH_EVIDENCE", report
        assert report["gates"]["word_conservation"] is True
        assert report["gates"]["timestamp_order"] is True
        assert report["summary"]["internal_change_utterances"] >= 1
        rows = {
            row["utterance_id"]: row
            for row in map(json.loads, (out / "utterance_attribution.jsonl").read_text().splitlines())
        }
        mixed = rows["utt_mixed"]
        assert mixed["status"] == "mixed"
        assert mixed["speaker_id"] is None
        assert len({turn["speaker_id"] for turn in mixed["speaker_turns"] if turn["speaker_id"]}) == 2
        assert "".join(turn["text"] for turn in mixed["speaker_turns"]) == next(
            row["text"]
            for row in json.loads(
                (session / "derived/transcript-simple/whisper-cpp/resolved/clean_dialogue.fixture.json").read_text()
            )["utterances"]
            if row["id"] == "utt_mixed"
        )
        words = [json.loads(line) for line in (out / "word_attribution.jsonl").read_text().splitlines()]
        overlap_words = [row for row in words if row["utterance_id"].startswith("utt_overlap")]
        assert overlap_words and all(row["speaker_id"] is None for row in overlap_words)
        assert all(row["reason"] == "possible_remote_overlap" for row in overlap_words)
        assert sha256(session / "derived/asr/remote.wav") == raw_hash
        assert sha256(
            session / "derived/transcript-simple/whisper-cpp/resolved/clean_dialogue.fixture.json"
        ) == dialogue_hash
        promoted_verify = subprocess.run(
            [
                sys.executable,
                str(AUDIT),
                str(session),
                "--verify-only",
                "--require-promoted",
                "--no-progress",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        assert promoted_verify.returncode == 0, promoted_verify.stdout + promoted_verify.stderr

        first_manifest = (out / "artifact_manifest.json").read_bytes()
        run(session, fixture)
        assert (out / "artifact_manifest.json").read_bytes() == first_manifest

        raw = session / "derived/transcript-simple/whisper-cpp/raw/remote.json"
        raw.unlink()
        run(session, fixture, "derived/audit/remote-speaker-diarization-v2-fallback")
        fallback = session / "derived/audit/remote-speaker-diarization-v2-fallback"
        fallback_report = json.loads((fallback / "report.json").read_text())
        assert fallback_report["decision"] == "FALLBACK_AGGREGATE"
        fallback_rich = json.loads((fallback / "transcript.rich.shadow.json").read_text())
        assert all(
            all(turn["speaker_id"] is None for turn in row.get("speaker_turns") or [])
            for row in fallback_rich["utterances"]
            if row.get("role") == "remote"
        )

    print("remote speaker diarization checks ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
