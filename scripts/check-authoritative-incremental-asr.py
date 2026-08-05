#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
import wave
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
TRANSCRIBER = REPO_ROOT / "scripts/transcribe-simple-whispercpp.py"


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_wav(path: Path, *, seconds: int, frequency: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rate = 16_000
    frames = bytearray()
    for index in range(rate * seconds):
        value = int(5000 * math.sin(2 * math.pi * frequency * index / rate))
        frames.extend(value.to_bytes(2, "little", signed=True))
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(frames)


def make_fake_whisper(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """#!/usr/bin/env python3
from pathlib import Path
import hashlib
import json
import os
import sys

args = sys.argv[1:]
output = Path(args[args.index('--output-file') + 1])
source = Path(args[args.index('--file') + 1])
language = args[args.index('--language') + 1]
count_path = Path(os.environ['FAKE_WHISPER_COUNT'])
count = int(count_path.read_text() or '0') if count_path.exists() else 0
count_path.write_text(str(count + 1))
digest = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
text = f'{language}:{digest}'
payload = {
    'params': {'model': args[args.index('--model') + 1], 'language': language},
    'transcription': [{
        'text': text,
        'offsets': {'from': 1000, 'to': 2000},
        'timestamps': {'from': '00:00:01,000', 'to': '00:00:02,000'},
        'tokens': [{'text': text, 'offsets': {'from': 1000, 'to': 2000}}],
    }],
}
output.parent.mkdir(parents=True, exist_ok=True)
output.with_suffix('.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\\n')
output.with_suffix('.txt').write_text(text + '\\n')
output.with_suffix('.vtt').write_text('WEBVTT\\n\\n00:00:01.000 --> 00:00:02.000\\n' + text + '\\n')
""",
        encoding="utf-8",
    )
    path.chmod(0o755)


def run_transcriber(
    session: Path,
    model: Path,
    whisper: Path,
    count: Path,
    *,
    prompt: Path | None = None,
    language: str = "ru",
    threads: int = 4,
    window_sec: int = 60,
) -> int:
    before = int(count.read_text() or "0") if count.exists() else 0
    command = [
        sys.executable,
        str(TRANSCRIBER),
        str(session),
        "--skip-export",
        "--model",
        str(model),
        "--whisper-cli",
        str(whisper),
        "--language",
        language,
        "--threads",
        str(threads),
        "--track-workers",
        "1",
        "--asr-window-sec",
        str(window_sec),
        "--repair-profile",
        "current",
    ]
    if prompt is not None:
        command += ["--prompt-file", str(prompt)]
    env = dict(os.environ)
    env["FAKE_WHISPER_COUNT"] = str(count)
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
    return int(count.read_text()) - before


def report(session: Path, track: str) -> dict[str, Any]:
    path = session / f"derived/transcript-simple/whisper-cpp/raw/chunks/{track}/chunk_cache_report.json"
    return json.loads(path.read_text(encoding="utf-8"))


def output_snapshot(session: Path) -> dict[str, str]:
    paths = [
        "derived/transcript-simple/whisper-cpp/raw/mic.json",
        "derived/transcript-simple/whisper-cpp/raw/remote.json",
        "derived/transcript-simple/whisper-cpp/resolved/clean_dialogue.json",
        "derived/transcript-simple/whisper-cpp/resolved/transcript.md",
        "derived/transcript-simple/whisper-cpp/resolved/transcript.simple.json",
    ]
    return {path: hashlib.sha256((session / path).read_bytes()).hexdigest() for path in paths}


def invalidate_raw(session: Path, tracks: tuple[str, ...] = ("mic", "remote")) -> None:
    raw = session / "derived/transcript-simple/whisper-cpp/raw"
    for track in tracks:
        for suffix in (".json", ".meta.json", ".txt", ".vtt"):
            (raw / f"{track}{suffix}").unlink(missing_ok=True)


def authoritative_check(session: Path) -> int:
    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts/check-asr-chunk-cache.py"),
            str(session),
            "--require-chunks",
            "--require-authoritative",
        ],
        check=False,
        stdout=subprocess.DEVNULL,
    )
    return result.returncode


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="murmurmark-authoritative-asr-") as root_raw:
        root = Path(root_raw)
        session = root / "session"
        model = root / "model.bin"
        whisper = root / "fake-whisper-cli"
        count = root / "count.txt"
        prompt = root / "prompt.txt"
        model.write_bytes(b"model-v1")
        make_fake_whisper(whisper)
        write_wav(session / "derived/asr/mic.wav", seconds=65, frequency=440.0)
        write_wav(session / "derived/asr/remote.wav", seconds=65, frequency=660.0)
        write_json(session / "session.json", {"schema": "murmurmark.session/v1", "session_id": "authoritative-asr-check"})

        assert run_transcriber(session, model, whisper, count) == 4
        clean = output_snapshot(session)
        for track in ("mic", "remote"):
            assert report(session, track)["schema"] == "murmurmark.whisper_cpp_chunk_cache_report/v2"

        tampered_meta = session / "derived/transcript-simple/whisper-cpp/raw/chunks/mic/0001_000000s.meta.json"
        tampered = json.loads(tampered_meta.read_text(encoding="utf-8"))
        tampered["identity"]["window"]["hard_end_sample"] += 1
        tampered["identity_sha256"] = hashlib.sha256(
            json.dumps(tampered["identity"], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        write_json(tampered_meta, tampered)
        assert authoritative_check(session) == 2
        invalidate_raw(session, ("mic",))
        assert run_transcriber(session, model, whisper, count) == 1
        assert output_snapshot(session) == clean

        invalidate_raw(session)
        for track in ("mic", "remote"):
            base = session / f"derived/transcript-simple/whisper-cpp/raw/chunks/{track}/0002_000060s"
            base.with_suffix(".json").unlink()
            base.with_suffix(".meta.json").unlink()
        assert run_transcriber(session, model, whisper, count) == 2
        assert output_snapshot(session) == clean
        for track in ("mic", "remote"):
            track_report = report(session, track)
            assert track_report["chunks_reused"] == 1, track_report
            assert track_report["chunks_transcribed"] == 1, track_report
            assert track_report["chunks_reused_by_origin"] == {"batch_resume": 1}, track_report
            assert track_report["reused_sec_by_origin"], track_report

        assert authoritative_check(session) == 0

        invalidate_raw(session, ("mic",))
        corrupt = session / "derived/transcript-simple/whisper-cpp/raw/chunks/mic/0001_000000s.json"
        corrupt.write_text("{broken", encoding="utf-8")
        assert run_transcriber(session, model, whisper, count) == 1
        assert report(session, "mic")["chunks_transcribed"] == 1

        prompt.write_text("domain prompt", encoding="utf-8")
        assert run_transcriber(session, model, whisper, count, prompt=prompt) == 4
        assert all(report(session, track)["chunks_transcribed"] == 2 for track in ("mic", "remote"))

        model.write_bytes(b"model-v2")
        assert run_transcriber(session, model, whisper, count, prompt=prompt) == 4

        with whisper.open("a", encoding="utf-8") as file:
            file.write("\n# binary revision\n")
        assert run_transcriber(session, model, whisper, count, prompt=prompt) == 4

        assert run_transcriber(session, model, whisper, count, prompt=prompt, threads=3) == 4
        assert run_transcriber(session, model, whisper, count, prompt=prompt, threads=3, language="en") == 4

        invalidate_raw(session, ("mic",))
        partial = session / "derived/transcript-simple/whisper-cpp/raw/chunks/mic/0001_000000s.meta.json"
        partial.unlink()
        assert run_transcriber(session, model, whisper, count, prompt=prompt, threads=3, language="en") == 1

        invalidate_raw(session)
        assert run_transcriber(session, model, whisper, count, prompt=prompt, threads=3, language="en", window_sec=30) == 6
        assert all(report(session, track)["chunks_transcribed"] == 3 for track in ("mic", "remote"))

    print("authoritative incremental ASR checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
