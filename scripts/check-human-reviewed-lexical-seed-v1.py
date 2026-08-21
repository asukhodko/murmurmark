#!/usr/bin/env python3
"""Regression checks for the blind human-reviewed lexical seed."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import wave
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv/bin/python"
SCRIPT = ROOT / "scripts/build-human-reviewed-lexical-seed-v1.py"


def load() -> Any:
    spec = importlib.util.spec_from_file_location("murmurmark_human_lexical_seed_check", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot_load_human_lexical_seed")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


SEED = load()


def run(args: list[str], expected: int = 0) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        [str(PYTHON), str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != expected:
        raise AssertionError(
            f"unexpected exit {completed.returncode}, expected {expected}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return completed


def write_wave(path: Path, duration: float = 40.0) -> None:
    sample_rate = 16000
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        frames = bytearray()
        for index in range(int(duration * sample_rate)):
            value = 1200 if (index // 80) % 2 else -1200
            frames.extend(struct.pack("<h", value))
        handle.writeframes(frames)


def artifact(path: Path, session: Path) -> dict[str, Any]:
    return {
        "path": str(path.relative_to(session)),
        "bytes": path.stat().st_size,
        "sha256": SEED.sha256(path),
    }


def utterances() -> list[dict[str, Any]]:
    rows = []
    index = 0
    for role, offset in (("me", 1.0), ("remote", 20.0)):
        for item in range(4):
            index += 1
            start = offset + item * 4.0
            text = (
                f"Проверяем Kubernetes сервис номер {item} сегодня"
                if item == 0
                else f"Это обычная контрольная фраза номер {item} для встречи"
            )
            rows.append(
                {
                    "id": f"utt_{index:06d}",
                    "role": role,
                    "speaker_label": "Me" if role == "me" else "Colleagues",
                    "start": start,
                    "end": start + 3.0,
                    "text": text,
                    "quality": {
                        "needs_review": False,
                        "overlap": False,
                        "role_confidence": 1.0,
                    },
                }
            )
    return sorted(rows, key=lambda row: row["start"])


def build_session(root: Path, session_id: str, speakers: int, similarity: float) -> None:
    session = root / session_id
    mic = session / "audio/mic/000001.wav"
    remote = session / "audio/remote/000001.wav"
    write_wave(mic)
    write_wave(remote)
    dialogue = session / "derived/dialogue.json"
    coverage = session / "derived/coverage.json"
    transcript = session / "derived/transcript.md"
    rich = session / "derived/rich.json"
    echo = session / "derived/preprocess/echo/echo_suppression_report.json"
    SEED.write_json(dialogue, {"schema": "fixture", "utterances": utterances()})
    SEED.write_json(
        coverage,
        {
            "schema": "fixture",
            "decision": "PUBLISH_EVIDENCE",
            "summary": {"published_speakers": speakers},
        },
    )
    transcript.parent.mkdir(parents=True, exist_ok=True)
    transcript.write_text("# private fixture transcript\n", encoding="utf-8")
    SEED.write_json(rich, {"schema": "fixture", "utterances": utterances()})
    SEED.write_json(echo, {"metrics": {"remote_similarity_before": similarity}})
    session_json = {
        "schema": "murmurmark.session/v1",
        "status": "completed",
        "files": {
            "mic": [{"path": str(mic.relative_to(session))}],
            "remote": [{"path": str(remote.relative_to(session))}],
        },
    }
    SEED.write_json(session / "session.json", session_json)
    selection = {
        "state": "selected",
        "batch_authoritative": True,
        "selected_profile": "reviewed_v1",
        "selected_speaker_profile": "remote_speaker_coverage_v3",
        "gates": {"speaker_evidence_promoted": True},
        "selected_dialogue": artifact(dialogue, session),
        "coverage_report": artifact(coverage, session),
        "selected_transcript": artifact(transcript, session),
        "rich_transcript": artifact(rich, session),
    }
    SEED.write_json(
        session / "derived/transcript-rich/speaker-resolved-default-v1/selection.json",
        selection,
    )


def policy(path: Path) -> None:
    SEED.write_json(
        path,
        {
            "schema": SEED.POLICY_SCHEMA,
            "version": "fixture",
            "state": "collecting_human_truth",
            "selection": {
                "salt": "fixture-human-lexical",
                "primary_slots_per_session_role": 2,
                "repeat_slots_per_session_role": 1,
                "minimum_duration_sec": 2.0,
                "maximum_duration_sec": 5.0,
                "minimum_hypothesis_words": 4,
                "maximum_hypothesis_words": 20,
                "minimum_role_confidence": 0.8,
                "clip_padding_sec": 0.1,
            },
            "sessions": [
                {
                    "alias": "group_low_leak",
                    "session_id": "group",
                    "meeting_mode": "group",
                    "acoustic_mode": "headphones_or_low_leak",
                },
                {
                    "alias": "one_to_one_speaker_playback",
                    "session_id": "one",
                    "meeting_mode": "1x1",
                    "acoustic_mode": "speaker_playback",
                },
            ],
            "acoustic_validation": {
                "maximum_low_leak_similarity": 0.06,
                "minimum_speaker_playback_similarity": 0.08,
            },
            "domain_terms": ["Kubernetes", "сервис"],
            "gates": {
                "minimum_sessions": 2,
                "minimum_primary_exact_slots_per_session_role": 2,
                "minimum_reference_words": 20,
                "required_meeting_modes": ["1x1", "group"],
                "required_acoustic_modes": ["headphones_or_low_leak", "speaker_playback"],
                "required_roles": ["me", "remote"],
                "minimum_repeat_consistency": 1.0,
            },
            "production_changes_allowed": False,
        },
    )


def command_args(policy_path: Path, sessions: Path, out: Path) -> list[str]:
    return [
        "--policy",
        str(policy_path),
        "--sessions-root",
        str(sessions),
        "--out-dir",
        str(out),
    ]


def check() -> None:
    with tempfile.TemporaryDirectory(prefix="murmurmark-human-lexical-seed-v1-") as temporary:
        root = Path(temporary)
        sessions = root / "sessions"
        out = sessions / "_reports/human-reviewed-lexical-seed-v1"
        policy_path = root / "policy.json"
        build_session(sessions, "group", speakers=3, similarity=0.03)
        build_session(sessions, "one", speakers=1, similarity=0.16)
        policy(policy_path)
        base = command_args(policy_path, sessions, out)
        raw = {
            path: SEED.sha256(path)
            for path in sessions.glob("*/audio/*/*.wav")
        }
        run(["preflight", *base])
        run(["freeze", *base])
        slots = SEED.read_jsonl(out / SEED.SLOTS)
        queue = SEED.read_jsonl(out / SEED.QUEUE)
        assert len(slots) == 8
        assert len(queue) == 12
        assert all("hypothesis_text" in row for row in slots)
        assert all("hypothesis_text" not in row for row in queue)
        by_item = {row["item_id"]: row for row in slots}
        run(["evaluate", *base], expected=2)
        incomplete = SEED.read_json(out / SEED.REPORT)
        assert incomplete["decision"] == "REVIEW_REQUIRED"
        for row in queue:
            text = by_item[row["item_id"]]["hypothesis_text"]
            run(
                [
                    "grade",
                    row["slot_id"],
                    "--outcome",
                    "exact_text",
                    "--text",
                    text,
                    *base,
                ]
            )
        snapshot = root / "snapshot.json"
        run(["evaluate", "--write-snapshot", str(snapshot), *base])
        report = SEED.read_json(out / SEED.REPORT)
        assert report["decision"] == "REFERENCE_READY"
        assert report["metrics"]["overall"]["wer"] == 0.0
        assert report["metrics"]["overall"]["cer"] == 0.0
        assert report["metrics"]["overall"]["domain_terms"]["accuracy"] == 1.0
        assert report["repeat_review"]["consistency"] == 1.0
        public = json.dumps(report, ensure_ascii=False)
        assert "private fixture transcript" not in public
        assert str(root) not in public
        run(["replay", "--write-snapshot", str(snapshot), *base])
        assert all(SEED.sha256(path) == digest for path, digest in raw.items())
        dialogue = sessions / "group/derived/dialogue.json"
        payload = SEED.read_json(dialogue)
        payload["utterances"][0]["text"] += " изменено"
        SEED.write_json(dialogue, payload)
        run(["replay", "--write-snapshot", str(snapshot), *base], expected=2)


def main() -> int:
    check()
    print("human reviewed lexical seed v1 checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
