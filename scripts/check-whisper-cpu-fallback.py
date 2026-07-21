#!/usr/bin/env python3
"""Focused checks for whisper-cli GPU crash fallback and text-fragment review."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path


def load_module():
    path = Path(__file__).with_name("transcribe-simple-whispercpp.py")
    spec = importlib.util.spec_from_file_location("murmurmark_transcribe_cpu_fallback", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    module = load_module()
    original_run = module.subprocess.run
    calls: list[list[str]] = []

    def crash_once(command, **kwargs):
        calls.append(list(command))
        if len(calls) == 1:
            raise subprocess.CalledProcessError(-11, command)
        return subprocess.CompletedProcess(command, 0)

    try:
        with tempfile.TemporaryDirectory(prefix="murmurmark-whisper-fallback-") as temp_dir:
            log_path = Path(temp_dir) / "whisper.log"
            module._WHISPER_FORCE_CPU_AFTER_GPU_CRASH = False
            module.subprocess.run = crash_once
            result = module.run_whisper_cli_with_cpu_fallback(
                ["whisper-cli", "--model", "model.bin", "--file", "clip.wav"],
                log_path,
            )
            assert result == {"mode": "cpu_fallback", "gpu_crash_returncode": -11}, result
            assert "--no-gpu" not in calls[0], calls
            assert "--no-gpu" in calls[1], calls

            def record_success(command, **kwargs):
                calls.append(list(command))
                return subprocess.CompletedProcess(command, 0)

            calls.clear()
            module.subprocess.run = record_success
            result = module.run_whisper_cli_with_cpu_fallback(
                ["whisper-cli", "--model", "model.bin", "--file", "next.wav"],
                log_path,
            )
            assert result == {"mode": "cpu_after_previous_gpu_crash"}, result
            assert len(calls) == 1 and "--no-gpu" in calls[0], calls

            def ordinary_failure(command, **kwargs):
                calls.append(list(command))
                raise subprocess.CalledProcessError(2, command)

            calls.clear()
            module._WHISPER_FORCE_CPU_AFTER_GPU_CRASH = False
            module.subprocess.run = ordinary_failure
            try:
                module.run_whisper_cli_with_cpu_fallback(
                    ["whisper-cli", "--model", "model.bin", "--file", "broken.wav"],
                    log_path,
                )
            except subprocess.CalledProcessError as error:
                assert error.returncode == 2
            else:
                raise AssertionError("ordinary whisper failure must not be retried as a GPU crash")
            assert len(calls) == 1 and "--no-gpu" not in calls[0], calls
    finally:
        module.subprocess.run = original_run
        module._WHISPER_FORCE_CPU_AFTER_GPU_CRASH = False

    rows = [
        {"id": "broken", "corrected_text": "Н", "quality": {}},
        {"id": "valid_me", "corrected_text": "Я", "quality": {}},
        {"id": "valid_conjunction", "corrected_text": "А", "quality": {}},
        {"id": "latin", "corrected_text": "R", "quality": {}},
        {"id": "normal", "corrected_text": "Наша команда", "quality": {}},
    ]
    assert module.mark_suspicious_text_fragments(rows) == 1, rows
    assert rows[0]["quality"]["needs_review"] is True
    assert rows[0]["quality"]["text_fragment_review"]["reason"] == "single_letter_asr_fragment"
    assert all(not row["quality"].get("needs_review") for row in rows[1:]), rows

    split_rows = [
        {
            "id": "utt_000001",
            "speaker_label": "Me",
            "source_track": "mic",
            "start": 51.72,
            "end": 53.0,
            "corrected_text": "Н",
            "quality": {},
        },
        {
            "id": "utt_000002",
            "speaker_label": "Me",
            "source_track": "mic",
            "start": 55.0,
            "end": 58.0,
            "corrected_text": "аша команда остается как есть",
            "quality": {},
        },
    ]
    repaired_rows, corrections = module.suppress_adjacent_same_speaker_duplicates(split_rows)
    assert len(repaired_rows) == 1, repaired_rows
    assert repaired_rows[0]["corrected_text"] == "Наша команда остается как есть", repaired_rows
    assert repaired_rows[0]["quality"]["split_initial_fragment_repaired"] is True
    assert corrections[0]["reason"] == "split_initial_letter_reattached", corrections

    standalone_rows = [
        {
            "id": "utt_000001",
            "speaker_label": "Me",
            "source_track": "mic",
            "start": 1.0,
            "end": 1.5,
            "corrected_text": "Я",
            "quality": {},
        },
        {
            "id": "utt_000002",
            "speaker_label": "Me",
            "source_track": "mic",
            "start": 2.0,
            "end": 3.0,
            "corrected_text": "согласен",
            "quality": {},
        },
    ]
    untouched_rows, corrections = module.suppress_adjacent_same_speaker_duplicates(standalone_rows)
    assert len(untouched_rows) == 2 and not corrections, (untouched_rows, corrections)

    print("whisper CPU fallback and text-fragment checks ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
