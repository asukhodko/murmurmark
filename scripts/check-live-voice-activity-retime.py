#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path

import numpy as np
from scipy.io import wavfile


def load_compare_module():
    path = Path(__file__).with_name("compare-live-batch.py")
    spec = importlib.util.spec_from_file_location("murmurmark_compare_live_batch_retime_test", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def tone(sample_rate: int, duration_sec: float, start_sec: float, end_sec: float, amplitude: float) -> np.ndarray:
    count = int(round(sample_rate * duration_sec))
    values = np.zeros(count, dtype=np.float32)
    start = int(round(sample_rate * start_sec))
    end = min(count, int(round(sample_rate * end_sec)))
    timeline = np.arange(max(0, end - start), dtype=np.float32) / sample_rate
    values[start:end] = amplitude * np.sin(2.0 * np.pi * 220.0 * timeline)
    return values


def write_track(path: Path, values: np.ndarray, sample_rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    wavfile.write(path, sample_rate, values)


def main() -> int:
    compare = load_compare_module()
    sample_rate = 16_000
    with tempfile.TemporaryDirectory(prefix="murmurmark-live-retime-") as temporary:
        session = Path(temporary)
        chunks_dir = session / "derived/live/chunks"

        remote_delayed = tone(sample_rate, 10.0, 4.0, 8.0, 0.20)
        write_track(chunks_dir / "000001/remote.wav", remote_delayed, sample_rate)
        write_track(chunks_dir / "000001/mic.live_echo_guard.wav", np.zeros_like(remote_delayed), sample_rate)

        remote_before_local = tone(sample_rate, 10.0, 0.4, 3.0, 0.20)
        local_after_remote = tone(sample_rate, 10.0, 5.0, 8.0, 0.20)
        mic_with_bleed = remote_before_local * 0.01 + local_after_remote
        write_track(chunks_dir / "000002/remote.wav", remote_before_local, sample_rate)
        write_track(chunks_dir / "000002/mic.live_echo_guard.wav", mic_with_bleed, sample_rate)

        rows = [
            {
                "index": 1,
                "clip_start_sec": 0.0,
                "remote": {"wav": "derived/live/chunks/000001/remote.wav"},
                "mic": {"asr_wav": "derived/live/chunks/000001/mic.live_echo_guard.wav"},
            },
            {
                "index": 2,
                "clip_start_sec": 10.0,
                "remote": {"wav": "derived/live/chunks/000002/remote.wav"},
                "mic": {"asr_wav": "derived/live/chunks/000002/mic.live_echo_guard.wav"},
            },
        ]
        chunks_path = session / "derived/live/chunks.jsonl"
        chunks_path.parent.mkdir(parents=True, exist_ok=True)
        chunks_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

        turns = [
            {
                "id": "remote-delayed",
                "chunk_index": 1,
                "source": "remote_segment",
                "role": "Colleagues",
                "start": 0.0,
                "end": 8.0,
                "text": "remote speech",
            },
            {
                "id": "local-after-remote",
                "chunk_index": 2,
                "source": "mic_segment",
                "role": "Me",
                "start": 10.0,
                "end": 18.0,
                "text": "local speech",
            },
        ]
        result = compare.apply_voice_activity_boundary_retime(session, turns)
        by_id = {str(row.get("id")): row for row in result}
        remote_start = float(by_id["remote-delayed"]["start"])
        local_start = float(by_id["local-after-remote"]["start"])
        assert 3.7 <= remote_start <= 4.1, remote_start
        assert 4.7 <= local_start - 10.0 <= 5.1, local_start
        assert by_id["remote-delayed"].get("voice_activity_boundary_retime") is True
        assert by_id["local-after-remote"].get("voice_activity_boundary_retime") is True

    print("live voice activity retime checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
