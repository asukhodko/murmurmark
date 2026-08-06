#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf


ROOT = Path(__file__).resolve().parents[1]
PRODUCER = ROOT / "scripts/canonical-live-asr-producer.py"
MATERIALIZER = ROOT / "scripts/materialize-live-asr-cache.py"
TRANSCRIBER = ROOT / "scripts/transcribe-simple-whispercpp.py"


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fake_whisper(path: Path) -> None:
    path.write_text(
        """#!/usr/bin/env python3
import hashlib, json, sys
from pathlib import Path
args = sys.argv[1:]
output = Path(args[args.index('--output-file') + 1])
source = Path(args[args.index('--file') + 1])
text = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
payload = {
    'params': {'language': args[args.index('--language') + 1]},
    'transcription': [{
        'text': text,
        'offsets': {'from': 1000, 'to': 2000},
        'tokens': [{'text': text, 'offsets': {'from': 1000, 'to': 2000}}],
    }],
}
output.with_suffix('.json').write_text(json.dumps(payload, sort_keys=True) + '\\n')
output.with_suffix('.txt').write_text(text + '\\n')
output.with_suffix('.vtt').write_text('WEBVTT\\n')
""",
        encoding="utf-8",
    )
    path.chmod(0o755)


def run(command: list[str]) -> None:
    subprocess.run(command, check=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def make_audio(seconds: int = 66, rate: int = 16_000) -> np.ndarray:
    index = np.arange(seconds * rate, dtype=np.float64)
    left = 0.1 * np.sin(2 * math.pi * 330.0 * index / rate)
    right = 0.08 * np.sin(2 * math.pi * 550.0 * index / rate)
    return np.column_stack((left, right)).astype(np.float32)


def write_segment_manifest(session: Path, audio: np.ndarray, rate: int) -> None:
    boundaries = (0, 33 * rate, len(audio))
    overlap = 5 * rate
    rows: list[dict[str, Any]] = []
    for index, (hard_start, hard_end) in enumerate(zip(boundaries[:-1], boundaries[1:], strict=True), start=1):
        clip_start = max(0, hard_start - overlap)
        clip_end = min(len(audio), hard_end + overlap)
        path = session / f"derived/experiments/live-shadow-v1/audio/remote/{index:06d}.caf"
        path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(path), audio[clip_start:clip_end], rate, format="CAF", subtype="FLOAT")
        rows.append(
            {
                "schema": "murmurmark.live_segment/v1",
                "source": "remote",
                "index": index,
                "path": str(path.relative_to(session)),
                "hard_start_frame": hard_start,
                "hard_end_frame": hard_end,
                "clip_start_frame": clip_start,
                "clip_end_frame": clip_end,
                "start_sec": hard_start / rate,
                "end_sec": hard_end / rate,
                "clip_start_sec": clip_start / rate,
                "clip_end_sec": clip_end / rate,
                "frames": hard_end - hard_start,
                "clip_frames": clip_end - clip_start,
                "sample_rate": rate,
                "closed": True,
                "final": index == 2,
                "after_overlap_complete": True,
                "provenance": "recording_time_committed_pcm",
            }
        )
    manifest = session / "derived/live/segments.jsonl"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def export_asr(session: Path) -> None:
    out = session / "derived/asr"
    out.mkdir(parents=True, exist_ok=True)
    base = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y"]
    for track in ("mic", "remote"):
        run(
            base
            + [
                "-i",
                str(session / f"audio/{track}/000001.caf"),
                "-map",
                "0:a:0",
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                str(out / f"{track}.wav"),
            ]
        )


def invoke_producer(session: Path, model: Path, whisper: Path, *extra: str) -> None:
    run(
        [
            sys.executable,
            str(PRODUCER),
            str(session),
            "--model",
            str(model),
            "--whisper-cli",
            str(whisper),
            "--threads",
            "2",
            "--once",
            *extra,
        ]
    )


def invoke_materializer(session: Path, model: Path, whisper: Path, *extra: str) -> dict[str, Any]:
    run(
        [
            sys.executable,
            str(MATERIALIZER),
            str(session),
            "--model",
            str(model),
            "--whisper-cli",
            str(whisper),
            "--threads",
            "2",
            "--force",
            *extra,
        ]
    )
    return json.loads((session / "derived/live/live_asr_cache_report.json").read_text(encoding="utf-8"))


def raw_remote_hash(session: Path) -> str:
    return hashlib.sha256(
        (session / "derived/transcript-simple/whisper-cpp/raw/remote.json").read_bytes()
    ).hexdigest()


def build_session(
    root: Path,
    name: str,
    *,
    exact_manifest: bool = True,
    finalized: bool = True,
    seconds: int = 66,
) -> tuple[Path, Path, Path]:
    session = root / name
    model = root / f"{name}.model.bin"
    whisper = root / f"{name}.whisper-cli"
    model.write_bytes(b"model-v1")
    fake_whisper(whisper)
    audio = make_audio(seconds)
    for track, scale in (("remote", 1.0), ("mic", 0.25)):
        path = session / f"audio/{track}/000001.caf"
        path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(path), audio * scale, 16_000, format="CAF", subtype="FLOAT")
    if finalized:
        write_json(session / "session.json", {"schema": "murmurmark.session/v1", "session_id": name})
    if exact_manifest:
        write_segment_manifest(session, audio, 16_000)
    else:
        manifest = session / "derived/live/segments.jsonl"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(
            json.dumps(
                {
                    "schema": "murmurmark.live_segment/v1",
                    "source": "remote",
                    "index": 1,
                    "path": "missing.caf",
                    "closed": True,
                }
            )
            + "\n",
            encoding="utf-8",
        )
    export_asr(session)
    return session, model, whisper


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="murmurmark-canonical-live-asr-") as temporary:
        root = Path(temporary)
        session, model, whisper = build_session(root, "exact")
        invoke_producer(session, model, whisper)
        producer_root = session / "derived/experiments/live-shadow-v1/authoritative-asr"
        report = json.loads((producer_root / "report.json").read_text(encoding="utf-8"))
        assert report["status"] == "completed", report
        assert report["progress"]["chunks_completed"] == 2, report
        assert report["progress"]["proven_sec"] == 66.0, report
        manifest_hash = hashlib.sha256((producer_root / "chunks.jsonl").read_bytes()).hexdigest()
        invoke_producer(session, model, whisper)
        assert hashlib.sha256((producer_root / "chunks.jsonl").read_bytes()).hexdigest() == manifest_hash

        quarantined = invoke_materializer(session, model, whisper, "--verify-only")
        assert quarantined["status"] == "not_eligible", quarantined
        assert any(
            "canonical_live_origin_not_promoted" in reason for reason in quarantined["reasons"]
        ), quarantined

        verified = invoke_materializer(
            session,
            model,
            whisper,
            "--verify-only",
            "--allow-unpromoted-live-origin",
        )
        assert verified["status"] == "partially_verified", verified
        assert verified["verified_tracks"] == ["remote"], verified
        assert verified["materialized_tracks"] == [], verified

        materialized = invoke_materializer(
            session,
            model,
            whisper,
            "--allow-unpromoted-live-origin",
        )
        assert materialized["materialized_tracks"] == ["remote"], materialized
        assert materialized["fallback_tracks"] == ["mic"], materialized
        live_hash = raw_remote_hash(session)

        shutil.rmtree(session / "derived/transcript-simple/whisper-cpp/raw")
        run(
            [
                sys.executable,
                str(TRANSCRIBER),
                str(session),
                "--skip-export",
                "--model",
                str(model),
                "--whisper-cli",
                str(whisper),
                "--threads",
                "2",
                "--track-workers",
                "1",
                "--repair-profile",
                "current",
            ]
        )
        assert raw_remote_hash(session) == live_hash

        live_partial, partial_model, partial_whisper = build_session(
            root,
            "live-partial",
            finalized=False,
            seconds=96,
        )
        invoke_producer(live_partial, partial_model, partial_whisper)
        partial_report = json.loads(
            (
                live_partial
                / "derived/experiments/live-shadow-v1/authoritative-asr/report.json"
            ).read_text(encoding="utf-8")
        )
        assert partial_report["status"] == "completed_partial", partial_report
        assert partial_report["progress"]["chunks_completed"] == 1, partial_report
        partial_rows = (
            live_partial
            / "derived/experiments/live-shadow-v1/authoritative-asr/chunks.jsonl"
        ).read_text(encoding="utf-8").splitlines()
        assert len(partial_rows) == 1, partial_rows

        first_row = json.loads((producer_root / "chunks.jsonl").read_text(encoding="utf-8").splitlines()[0])
        first_json = session / first_row["remote"]["asr"]["json"]
        first_json.write_text("{corrupt", encoding="utf-8")
        rejected = invoke_materializer(
            session,
            model,
            whisper,
            "--allow-unpromoted-live-origin",
        )
        assert "remote" in rejected["fallback_tracks"], rejected
        assert any("remote:live_json_hash_mismatch" in reason for reason in rejected["reasons"]), rejected

        incomplete, incomplete_model, incomplete_whisper = build_session(root, "incomplete", exact_manifest=False)
        invoke_producer(incomplete, incomplete_model, incomplete_whisper)
        incomplete_report = json.loads(
            (incomplete / "derived/experiments/live-shadow-v1/authoritative-asr/report.json").read_text(encoding="utf-8")
        )
        assert incomplete_report["status"] == "fallback", incomplete_report
        assert not (incomplete / "derived/experiments/live-shadow-v1/authoritative-asr/chunks.jsonl").exists()

        replay, replay_model, replay_whisper = build_session(root, "replay")
        invoke_producer(replay, replay_model, replay_whisper, "--replay-from-raw")
        replay_rejected = invoke_materializer(replay, replay_model, replay_whisper)
        assert "remote" in replay_rejected["fallback_tracks"], replay_rejected
        replay_allowed = invoke_materializer(
            replay,
            replay_model,
            replay_whisper,
            "--allow-historical-replay",
        )
        assert replay_allowed["materialized_tracks"] == ["remote"], replay_allowed

    print("canonical live ASR producer checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
