#!/usr/bin/env python3
"""Focused regression checks for the transcript_integrity_v1 profile."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf


SCRIPT = Path(__file__).with_name("apply-transcript-integrity.py")
CORPUS_SCRIPT = Path(__file__).with_name("report-transcript-integrity-corpus.py")


def load_module():
    spec = importlib.util.spec_from_file_location("murmurmark_transcript_integrity", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def utterance(identifier: str, start: float, end: float, text: str, role: str = "remote") -> dict:
    return {
        "id": identifier,
        "start": start,
        "end": end,
        "role": role,
        "speaker_label": "Me" if role == "me" else "Colleagues",
        "source_track": "mic" if role == "me" else "remote",
        "text": text,
        "quality": {"needs_review": False, "role_confidence": 0.9},
    }


def run(session: Path, fixture: Path | None = None) -> None:
    command = [
        sys.executable,
        str(SCRIPT),
        str(session),
        "--input-profile",
        "reviewed_v1",
        "--judge-mode",
        "off" if fixture is None else "auto",
    ]
    if fixture is not None:
        command += ["--judge-fixture", str(fixture)]
    subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def main() -> int:
    module = load_module()
    with tempfile.TemporaryDirectory(prefix="murmurmark-transcript-integrity-") as temporary:
        session = Path(temporary) / "session"
        resolved = session / "derived/transcript-simple/whisper-cpp/resolved"
        write_json(session / "session.json", {"schema": "murmurmark.session/v1", "session_id": "fixture"})
        rows = [
            utterance("utt_001", 0.0, 4.0, "Проверяем текущую задачу.", "me"),
            utterance("utt_002", 3.0, 4.0, "задачу.", "me"),
            utterance("utt_003", 5.0, 9.0, "Это трудно преодолевать или еще не пробовали", "me"),
            utterance("utt_004", 8.0, 9.5, "одолевать или еще не пробовали", "me"),
            utterance("utt_005", 10.0, 15.0, "Надо проверить логи Надо проверить логи перед релизом"),
            utterance("utt_006", 16.0, 18.0, "Спасибо. Спасибо. Спасибо. Спасибо. Спасибо."),
            utterance("utt_007", 20.0, 22.0, "Фраза один"),
            utterance("utt_008", 22.2, 24.0, "Фраза один"),
            utterance("utt_009", 25.0, 28.0, "Решаем по факту каких-то"),
            utterance("utt_010", 28.0, 31.0, "факту каких-то обращений"),
            utterance("utt_011", 32.0, 36.0, "Очень важный вопрос Очень важный вопрос"),
            utterance("utt_012", 2.5, 3.5, "Параллельная удалённая реплика"),
        ]
        dialogue = {"schema": "murmurmark.clean_dialogue/v1", "session": "fixture", "utterances": rows}
        write_json(resolved / "clean_dialogue.reviewed_v1.json", dialogue)
        write_json(
            resolved / "quality_report.reviewed_v1.json",
            {"schema": "murmurmark.quality_report/v1", "profile": "reviewed_v1", "needs_review_count": 0},
        )
        write_json(
            resolved / "transcript.simple.reviewed_v1.json",
            {
                "schema": "murmurmark.transcript_simple/v1",
                "session": "fixture",
                "utterances": [
                    {**row, "raw_text": row["text"], "corrected_text": row["text"], "corrections": []}
                    for row in rows
                ],
            },
        )
        write_json(
            resolved / "overlaps.reviewed_v1.json",
            {
                "schema": "murmurmark.transcript_overlaps/v1",
                "session": "fixture",
                "overlaps": [
                    {
                        "id": "ov_000001",
                        "start": 3.0,
                        "end": 4.0,
                        "duration_sec": 1.0,
                        "me_utterance_id": "utt_001",
                        "remote_utterance_id": "utt_012",
                        "text_similarity": 0.0,
                        "me_text": rows[0]["text"],
                        "remote_text": rows[-1]["text"],
                    }
                ],
            },
        )

        rate = 16000
        duration = 40
        timeline = np.arange(rate * duration, dtype=np.float32) / rate
        remote = 0.05 * np.sin(2.0 * np.pi * 220.0 * timeline)
        remote[16 * rate : 18 * rate] = 0.0
        mic = 0.04 * np.sin(2.0 * np.pi * 180.0 * timeline)
        asr_dir = session / "derived/asr"
        asr_dir.mkdir(parents=True, exist_ok=True)
        sf.write(asr_dir / "remote.wav", remote, rate)
        sf.write(asr_dir / "mic.wav", mic, rate)
        for source, audio in (("mic", mic), ("remote", remote)):
            path = session / "audio" / source / "000001.caf"
            path.parent.mkdir(parents=True, exist_ok=True)
            sf.write(path, audio, rate, format="CAF", subtype="FLOAT")
        raw_hashes = {
            path: sha256(path)
            for path in (session / "audio/mic/000001.caf", session / "audio/remote/000001.caf")
        }

        run(session)
        fail_open = json.loads(
            (resolved / "clean_dialogue.transcript_integrity_v1.json").read_text()
        )
        fail_open_by_id = {row["id"]: row for row in fail_open["utterances"]}
        assert "utt_006" in fail_open_by_id
        assert "utt_008" in fail_open_by_id
        assert fail_open_by_id["utt_005"]["text"] == rows[4]["text"]
        fail_open_report = json.loads(
            (
                session
                / "derived/transcript-simple/whisper-cpp/text-integrity"
                / "transcript_integrity_report.transcript_integrity_v1.json"
            ).read_text()
        )
        assert fail_open_report["gates"]["fail_open_judge"] is True
        assert fail_open_report["summary"]["remaining_review_count"] == 4

        candidates = module.deduplicate_candidates(rows)
        fixture_rows: dict[str, dict[str, str]] = {}
        for candidate in candidates:
            original = candidate["original_text"]
            if candidate["kind"] == "internal_exact_repeat" and "Надо проверить" in original:
                text = "Надо проверить логи перед релизом"
            elif candidate["kind"] == "internal_exact_repeat":
                text = "Очень важный вопрос Очень важный вопрос"
            elif candidate["kind"] == "decoder_repetition_loop":
                text = ""
            elif candidate["kind"] == "adjacent_exact_repeat":
                text = "Фраза один"
            else:
                continue
            fixture_rows[candidate["candidate_id"]] = {"status": "ok", "text": text}
        fixture = session / "judge_fixture.json"
        write_json(fixture, {"candidates": fixture_rows})
        run(session, fixture)

        out = json.loads((resolved / "clean_dialogue.transcript_integrity_v1.json").read_text())
        by_id = {row["id"]: row for row in out["utterances"]}
        assert "utt_002" not in by_id
        assert "utt_004" not in by_id
        assert "utt_006" not in by_id
        assert "utt_008" not in by_id
        assert by_id["utt_005"]["text"] == "Надо проверить логи перед релизом"
        assert by_id["utt_010"]["text"] == "обращений"
        assert by_id["utt_011"]["text"] == "Очень важный вопрос Очень важный вопрос"
        assert by_id["utt_011"]["quality"]["needs_review"] is True
        simple = json.loads(
            (resolved / "transcript.simple.transcript_integrity_v1.json").read_text()
        )
        simple_by_id = {row["id"]: row for row in simple["utterances"]}
        assert simple_by_id["utt_005"]["raw_text"] == rows[4]["text"]
        assert simple_by_id["utt_005"]["corrected_text"] == "Надо проверить логи перед релизом"
        assert simple_by_id["utt_005"]["corrections"]
        overlap = json.loads(
            (resolved / "overlaps.transcript_integrity_v1.json").read_text()
        )["overlaps"][0]
        assert set(("id", "duration_sec", "text_similarity")) <= set(overlap)
        assert all(sha256(path) == digest for path, digest in raw_hashes.items())

        report_path = (
            session
            / "derived/transcript-simple/whisper-cpp/text-integrity"
            / "transcript_integrity_report.transcript_integrity_v1.json"
        )
        report = json.loads(report_path.read_text())
        assert report["gates"]["passed"] is True
        assert report["summary"]["applied_patch_count"] == 6, report["summary"]
        assert report["summary"]["remaining_review_count"] == 1, report["summary"]

        output_paths = [
            resolved / "clean_dialogue.transcript_integrity_v1.json",
            resolved / "quality_report.transcript_integrity_v1.json",
            resolved / "overlaps.transcript_integrity_v1.json",
            resolved / "transcript.simple.transcript_integrity_v1.json",
            resolved / "transcript.transcript_integrity_v1.md",
            report_path,
        ]
        first_hashes = {path: sha256(path) for path in output_paths}
        run(session, fixture)
        assert all(sha256(path) == digest for path, digest in first_hashes.items())

        corpus_dir = Path(temporary) / "corpus-report"
        policy_path = Path(temporary) / "policy.json"
        subprocess.run(
            [
                sys.executable,
                str(CORPUS_SCRIPT),
                str(session),
                "--out-dir",
                str(corpus_dir),
                "--min-sessions",
                "1",
                "--min-applied-patches",
                "1",
                "--write-policy",
                str(policy_path),
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        corpus = json.loads((corpus_dir / "corpus_report.json").read_text())
        assert corpus["decision"] == "PROMOTE"
        assert corpus["sessions"][0]["slot"] == "session_01"
        serialized = json.dumps(corpus, ensure_ascii=False)
        assert str(session) not in serialized
        assert "Надо проверить логи" not in serialized
        assert json.loads(policy_path.read_text())["decision"] == "PROMOTE"

    pipeline_source = SCRIPT.with_name("run-session-pipeline.py").read_text(encoding="utf-8")
    command_marker = 'str(repo_root / "scripts/apply-transcript-integrity.py")'
    assert pipeline_source.count(command_marker) == 1
    command_offset = pipeline_source.index(command_marker)
    step_start = pipeline_source.rfind("step(", 0, command_offset)
    step_end = pipeline_source.find("step(", command_offset)
    integrity_step = pipeline_source[step_start:step_end]
    assert '"transcript_integrity"' in integrity_step
    assert "phase=DEFERRED_PHASE" not in integrity_step
    assert command_offset < pipeline_source.index('step("synthesize_auto"', command_offset)

    print("transcript integrity v1 checks ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
