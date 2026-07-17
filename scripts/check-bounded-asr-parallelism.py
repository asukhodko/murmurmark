#!/usr/bin/env python3
"""Prove that bounded ASR scheduling preserves sequential results."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
TRANSCRIBE = REPO_ROOT / "scripts/transcribe-simple-whispercpp.py"


def load_module() -> Any:
    spec = importlib.util.spec_from_file_location("murmurmark_bounded_asr_parallelism", TRANSCRIBE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {TRANSCRIBE}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


MODULE = load_module()


def write_fake_whisper(path: Path) -> None:
    path.write_text(
        """#!/usr/bin/env python3
import json
import sys
from pathlib import Path

args = sys.argv[1:]
output = Path(args[args.index("--output-file") + 1])
track = "remote" if "remote" in str(output) else "mic"
text = "Здравствуйте" if track == "remote" else "Привет"
payload = {
    "params": {"language": "ru"},
    "transcription": [{
        "text": " " + text,
        "offsets": {"from": 100, "to": 1200},
        "tokens": [{"text": " " + text, "p": 0.95, "offsets": {"from": 100, "to": 1200}}],
    }],
}
output.parent.mkdir(parents=True, exist_ok=True)
output.with_suffix(".json").write_text(json.dumps(payload, ensure_ascii=False) + "\\n", encoding="utf-8")
output.with_suffix(".txt").write_text(text + "\\n", encoding="utf-8")
""",
        encoding="utf-8",
    )
    path.chmod(0o755)


def make_audio(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=16000:cl=mono",
            "-t",
            "2",
            "-c:a",
            "pcm_s16le",
            str(path),
        ],
        check=True,
    )


def run_track_case(root: Path, workers: int, fake_whisper: Path, model: Path) -> Path:
    session = root / f"session-{workers}"
    make_audio(session / "derived/asr/mic.wav")
    make_audio(session / "derived/asr/remote.wav")
    command = [
        sys.executable,
        str(TRANSCRIBE),
        str(session),
        "--model",
        str(model),
        "--whisper-cli",
        str(fake_whisper),
        "--skip-export",
        "--mic-audio-prep",
        "none",
        "--remote-audio-prep",
        "none",
        "--track-workers",
        str(workers),
        "--micro-asr-workers",
        "1",
    ]
    subprocess.run(command, cwd=REPO_ROOT, check=True, stdout=subprocess.DEVNULL)
    return session


def json_value(path: Path, key: str | None = None) -> Any:
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if key is None else value[key]


def check_track_equivalence(root: Path) -> None:
    fake_whisper = root / "fake-whisper"
    model = root / "model.bin"
    write_fake_whisper(fake_whisper)
    model.write_bytes(b"model")
    sequential = run_track_case(root, 1, fake_whisper, model)
    parallel = run_track_case(root, 2, fake_whisper, model)

    relative_root = Path("derived/transcript-simple/whisper-cpp")
    for track in ("mic", "remote"):
        assert json_value(
            sequential / relative_root / f"raw/{track}.json", "transcription"
        ) == json_value(
            parallel / relative_root / f"raw/{track}.json", "transcription"
        )
    assert json_value(
        sequential / relative_root / "resolved/quality_report.json"
    ) == json_value(
        parallel / relative_root / "resolved/quality_report.json"
    )
    assert (
        sequential / relative_root / "resolved/transcript.md"
    ).read_text(encoding="utf-8") == (
        parallel / relative_root / "resolved/transcript.md"
    ).read_text(encoding="utf-8")
    assert json_value(
        sequential / relative_root / "resolved/clean_dialogue.json", "utterances"
    ) == json_value(
        parallel / relative_root / "resolved/clean_dialogue.json", "utterances"
    )


def check_micro_equivalence(root: Path) -> None:
    session = root / "micro"
    source_dir = session / "derived/preprocess/audio"
    sources = []
    for index, label in enumerate(("clean", "raw", "masked"), start=1):
        path = source_dir / f"{label}.wav"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(bytes([index]))
        sources.append((label, path))

    original_sources = MODULE.micro_reasr_sources
    original_current = MODULE.run_micro_reasr_current
    original_materialize = MODULE.materialize_micro_reasr

    def fake_sources(_session: Path, _profile: str) -> list[tuple[str, Path]]:
        return sources

    def fake_current(**_kwargs: Any) -> tuple[str, dict[str, Any]]:
        return "исходная фраза", {"status": "ok", "source_label": "clean", "rows": [{"token_avg_prob": 0.7}]}

    def fake_materialize(**kwargs: Any) -> tuple[str, dict[str, Any]]:
        source_label = str(kwargs["source_label"])
        window_label = str(kwargs["window_label"])
        # Force completion order to differ from submission order.
        time.sleep({"clean": 0.03, "raw": 0.01, "masked": 0.0}[source_label])
        score = {"clean": 0.72, "raw": 0.91, "masked": 0.80}[source_label]
        text = f"{window_label} {source_label}"
        return text, {
            "status": "ok",
            "source_label": source_label,
            "window_label": window_label,
            "score": score,
            "rows": [{"token_avg_prob": score}],
        }

    MODULE.micro_reasr_sources = fake_sources
    MODULE.run_micro_reasr_current = fake_current
    MODULE.materialize_micro_reasr = fake_materialize
    try:
        kwargs = {
            "session": session,
            "repair_dir": session / "repair",
            "whisper_cli": "fake",
            "model": root / "model.bin",
            "language": "ru",
            "threads": 1,
            "start_ms": 1_000,
            "end_ms": 2_000,
            "recognition_windows": [
                {"label": "normal", "start_ms": 500, "end_ms": 2_500},
                {"label": "wide", "start_ms": 0, "end_ms": 3_000},
            ],
            "force": False,
            "repair_profile": "shadow_v2",
        }
        sequential = MODULE.run_micro_reasr(**kwargs, micro_asr_workers=1)
        parallel = MODULE.run_micro_reasr(**kwargs, micro_asr_workers=4)
        assert sequential == parallel, (sequential, parallel)
    finally:
        MODULE.micro_reasr_sources = original_sources
        MODULE.run_micro_reasr_current = original_current
        MODULE.materialize_micro_reasr = original_materialize


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="murmurmark-bounded-asr-") as raw_root:
        root = Path(raw_root)
        check_track_equivalence(root)
        check_micro_equivalence(root)
    print("bounded ASR parallelism checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
