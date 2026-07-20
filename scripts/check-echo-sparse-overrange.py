#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts/echo-guard-session-local-fir.py"


def load_module() -> Any:
    spec = importlib.util.spec_from_file_location("echo_guard_session_local_fir", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load local FIR helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    module = load_module()
    sample_rate = 16_000
    base = np.full(sample_rate * 20, 0.1, dtype=np.float32)

    sparse = base.copy()
    sparse[80_000:80_080] = 2.5
    limited, sparse_report = module.limit_sparse_input_overrange(sparse, sample_rate)
    assert sparse_report["applied"] is True, sparse_report
    assert sparse_report["sample_count"] == 80, sparse_report
    assert sparse_report["region_count"] == 1, sparse_report
    assert sparse_report["duration_ms"] == 5.0, sparse_report
    assert float(np.max(np.abs(limited))) <= module.INPUT_PEAK_LIMIT + 1.0e-6, sparse_report
    assert np.array_equal(limited[:80_000], sparse[:80_000])

    sustained = base.copy()
    sustained[80_000:88_000] = 2.5
    unchanged, sustained_report = module.limit_sparse_input_overrange(sustained, sample_rate)
    assert sustained_report["applied"] is False, sustained_report
    assert sustained_report["region_count"] == 1, sustained_report
    assert sustained_report["duration_ms"] == 500.0, sustained_report
    assert float(np.max(np.abs(unchanged))) == 2.5, sustained_report

    clean, clean_report = module.limit_sparse_input_overrange(base, sample_rate)
    assert clean_report["applied"] is False, clean_report
    assert clean_report["sample_count"] == 0, clean_report
    assert clean_report["region_count"] == 0, clean_report
    assert np.array_equal(clean, base)

    mode = module.acoustic_mode_from_segments(
        [
            {"state": "remote_only", "remote_similarity_before": value}
            for value in [0.02, 0.05, 0.07, 0.08, 0.10, 0.12, 0.15, 0.20, 0.24, 0.30]
        ]
    )
    assert mode["mode"] == "speaker_playback", mode

    with tempfile.TemporaryDirectory(prefix="murmurmark-echo-metrics-only-") as value:
        root = Path(value)
        session = root / "session"
        audio_dir = session / "derived/preprocess/audio"
        audio_dir.mkdir(parents=True)
        seconds = 12
        timeline = np.arange(sample_rate * seconds, dtype=np.float32) / sample_rate
        remote = (0.12 * np.sin(2 * np.pi * 440.0 * timeline)).astype(np.float32)
        mic = np.roll(remote, 320) * 0.35
        mic[:320] = 0.0
        module.write_wav_float(audio_dir / "remote_for_aec.wav", sample_rate, remote)
        module.write_wav_float(audio_dir / "mic_raw_for_asr.wav", sample_rate, mic)
        report = root / "report.json"
        outputs = {
            "clean": root / "clean.wav",
            "echo": root / "echo.wav",
            "role": root / "role.wav",
            "preview": root / "preview.wav",
            "segments": root / "segments.jsonl",
            "state": root / "state.jsonl",
            "chunks": root / "chunks",
        }
        completed = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                str(session),
                "--metrics-only",
                "--output-clean",
                str(outputs["clean"]),
                "--output-echo",
                str(outputs["echo"]),
                "--output-role-mask",
                str(outputs["role"]),
                "--output-role-preview",
                str(outputs["preview"]),
                "--asr-segments-dir",
                str(outputs["chunks"]),
                "--report",
                str(report),
                "--segments",
                str(outputs["segments"]),
                "--speaker-state",
                str(outputs["state"]),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr
        payload = json.loads(report.read_text(encoding="utf-8"))
        assert payload["outputs"]["persisted"] is False, payload["outputs"]
        assert payload["acoustic_mode"]["mode"] in {
            "speaker_playback",
            "headphones_or_low_leak",
            "uncertain",
        }
        assert not any(path.exists() for path in outputs.values()), outputs

    print("echo sparse overrange checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
