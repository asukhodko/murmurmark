#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import hashlib
import json
import math
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
MATERIALIZER = REPO_ROOT / "scripts/materialize-live-asr-cache.py"
TRANSCRIBER = REPO_ROOT / "scripts/transcribe-simple-whispercpp.py"


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_module(MATERIALIZER, "murmurmark_live_asr_cache")
CACHE = load_module(REPO_ROOT / "scripts/authoritative_asr_cache.py", "murmurmark_authoritative_asr_cache")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_wav(path: Path, *, seconds: int, frequency: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rate = 16_000
    frames = bytearray()
    for index in range(rate * seconds):
        value = int(6000 * math.sin(2 * math.pi * frequency * index / rate))
        frames.extend(value.to_bytes(2, "little", signed=True))
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(frames)


def make_fake_whisper(path: Path) -> None:
    path.write_text(
        """#!/usr/bin/env python3
from pathlib import Path
import hashlib
import json
import sys

args = sys.argv[1:]
output = Path(args[args.index('--output-file') + 1])
source = Path(args[args.index('--file') + 1])
language = args[args.index('--language') + 1]
text = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
payload = {
    'params': {'model': args[args.index('--model') + 1], 'language': language},
    'transcription': [{
        'text': text,
        'offsets': {'from': 1000, 'to': 2000},
        'tokens': [{'text': text, 'offsets': {'from': 1000, 'to': 2000}}],
    }],
}
output.with_suffix('.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\\n')
""",
        encoding="utf-8",
    )
    path.chmod(0o755)


def build_session(
    root: Path,
    name: str,
    *,
    invalid_track: str | None = None,
    invalid_kind: str | None = None,
    prompt: str | None = None,
) -> tuple[Path, Path, Path, Path | None]:
    session = root / name
    model = root / f"{name}.model.bin"
    whisper_cli = root / f"{name}.whisper-cli"
    model.write_bytes(b"model-v1")
    make_fake_whisper(whisper_cli)
    prompt_path = root / f"{name}.prompt.txt" if prompt is not None else None
    if prompt_path is not None:
        prompt_path.write_text(prompt, encoding="utf-8")

    write_json(
        session / "derived/live/live_pipeline_report.json",
        {"schema": "murmurmark.live_pipeline_report/v1", "status": "completed"},
    )
    write_json(session / "session.json", {"schema": "murmurmark.session/v1", "session_id": name})
    chunks_by_index: dict[int, dict[str, Any]] = {}
    for track, prep, role, frequency in (
        ("mic", "speech", "Me", 440.0),
        ("remote", "loudnorm", "Colleagues", 660.0),
    ):
        source = session / f"derived/asr/{track}.wav"
        prepared = session / f"derived/transcript-simple/whisper-cpp/prepared-audio/{track}_{prep}.wav"
        write_wav(source, seconds=80, frequency=frequency)
        MODULE.prepare_audio(source, prepared, prep)
        specs = MODULE.build_specs(prepared, duration_ms=0, window_sec=60, overlap_sec=5)
        decode = CACHE.decode_contract(language="ru", threads=4, max_context=0, prompt=prompt, duration_ms=0)
        for spec in specs:
            index = spec["index"]
            chunk_dir = session / f"derived/live/chunks/{index:06d}"
            live_wav = chunk_dir / f"{track}.wav"
            CACHE.slice_pcm_wav(prepared, live_wav, spec["seek_sample"], spec["clip_end_sample"])
            identity = CACHE.build_chunk_identity(
                track=track,
                role=role,
                spec=spec,
                chunk_wav=live_wav,
                model=model,
                whisper_cli=whisper_cli,
                decode=decode,
                audio_prep=prep,
            )
            raw_json = chunk_dir / f"{track}.json"
            text = hashlib.sha256(live_wav.read_bytes()).hexdigest()[:16]
            payload = {
                "params": {"model": str(model.resolve()), "language": "ru"},
                "transcription": [
                    {
                        "text": text,
                        "offsets": {"from": 1000, "to": 2000},
                        "tokens": [{"text": text, "offsets": {"from": 1000, "to": 2000}}],
                    }
                ],
            }
            write_json(raw_json, payload)
            proof: dict[str, Any] = {
                "schema": MODULE.LIVE_PROOF_SCHEMA,
                "completed": True,
                "identity": identity,
                "identity_sha256": CACHE.content_sha256(identity),
                "output_json": CACHE.output_fingerprint(raw_json),
                "provenance": {"origin": "recording_time_committed_pcm"},
            }
            if invalid_track == track and index == 1:
                if invalid_kind == "proof_missing":
                    proof = {}
                elif invalid_kind == "legacy_proof":
                    proof = {"schema": "murmurmark.live_batch_asr_compatibility/v1"}
                elif invalid_kind == "model":
                    proof["identity"] = json.loads(json.dumps(identity))
                    proof["identity"]["engine"]["model"]["sha256"] = "bad"
                    proof["identity_sha256"] = CACHE.content_sha256(proof["identity"])
                elif invalid_kind == "prompt":
                    proof["identity"] = json.loads(json.dumps(identity))
                    proof["identity"]["decode"]["prompt_sha256"] = "bad"
                    proof["identity_sha256"] = CACHE.content_sha256(proof["identity"])
                elif invalid_kind == "pcm":
                    with live_wav.open("r+b") as file:
                        file.seek(44)
                        original = file.read(1)
                        file.seek(44)
                        file.write(bytes([(original[0] if original else 0) ^ 0xFF]))
                elif invalid_kind == "json":
                    raw_json.write_text("{broken", encoding="utf-8")
                elif invalid_kind == "partial":
                    proof.pop("output_json", None)
            row = chunks_by_index.setdefault(
                index,
                {
                    "schema": "murmurmark.live_chunk/v1",
                    "index": index,
                    "start_sec": spec["hard_start_ms"] / 1000,
                    "end_sec": spec["hard_end_ms"] / 1000,
                    "duration_sec": (spec["hard_end_ms"] - spec["hard_start_ms"]) / 1000,
                    "clip_start_sec": spec["seek_ms"] / 1000,
                    "clip_end_sec": spec["clip_end_ms"] / 1000,
                },
            )
            row[track] = {
                "wav": str(live_wav),
                "asr_wav": str(live_wav),
                "audio_prep": prep,
                "hard_start_sec": spec["hard_start_ms"] / 1000,
                "hard_end_sec": spec["hard_end_ms"] / 1000,
                "clip_start_sec": spec["seek_ms"] / 1000,
                "clip_end_sec": spec["clip_end_ms"] / 1000,
                "asr": {"status": "passed", "json": str(raw_json)},
                "batch_cache_compatibility": proof,
            }
    chunks_path = session / "derived/live/chunks.jsonl"
    chunks_path.parent.mkdir(parents=True, exist_ok=True)
    chunks_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for _, row in sorted(chunks_by_index.items())),
        encoding="utf-8",
    )
    return session, model, whisper_cli, prompt_path


def run_case(session: Path, model: Path, whisper_cli: Path, prompt: Path | None = None) -> dict[str, Any]:
    command = [
        sys.executable,
        str(MATERIALIZER),
        str(session),
        "--model",
        str(model),
        "--whisper-cli",
        str(whisper_cli),
        "--threads",
        "4",
        "--force",
    ]
    if prompt is not None:
        command += ["--prompt-file", str(prompt)]
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL)
    return json.loads((session / "derived/live/live_asr_cache_report.json").read_text(encoding="utf-8"))


def assert_tracks(report: dict[str, Any], reused: set[str], fallback: set[str]) -> None:
    assert set(report["materialized_tracks"]) == reused, report
    assert set(report["fallback_tracks"]) == fallback, report
    for track in reused:
        assert report["track_compatibility"][track]["eligible"] is True, report
    for track in fallback:
        assert report["track_compatibility"][track]["eligible"] is False, report


def raw_hashes(session: Path) -> dict[str, str]:
    raw = session / "derived/transcript-simple/whisper-cpp/raw"
    return {track: hashlib.sha256((raw / f"{track}.json").read_bytes()).hexdigest() for track in ("mic", "remote")}


def run_clean_batch(session: Path, model: Path, whisper_cli: Path) -> None:
    raw = session / "derived/transcript-simple/whisper-cpp/raw"
    shutil.rmtree(raw)
    subprocess.run(
        [
            sys.executable,
            str(TRANSCRIBER),
            str(session),
            "--skip-export",
            "--model",
            str(model),
            "--whisper-cli",
            str(whisper_cli),
            "--language",
            "ru",
            "--threads",
            "4",
            "--track-workers",
            "1",
            "--repair-profile",
            "current",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="murmurmark-live-asr-compat-") as raw_root:
        root = Path(raw_root)
        report = run_case(*build_session(root, "both"))
        assert report["schema"] == MODULE.SCHEMA
        assert report["status"] == "materialized"
        assert_tracks(report, {"mic", "remote"}, set())
        check = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts/check-asr-chunk-cache.py"),
                str(root / "both"),
                "--require-chunks",
                "--require-authoritative",
            ],
            check=False,
            stdout=subprocess.DEVNULL,
        )
        assert check.returncode == 0
        materialized_hashes = raw_hashes(root / "both")
        run_clean_batch(root / "both", root / "both.model.bin", root / "both.whisper-cli")
        assert raw_hashes(root / "both") == materialized_hashes

        for kind in ("proof_missing", "legacy_proof", "model", "prompt", "pcm", "json", "partial"):
            args = build_session(root, f"bad-{kind}", invalid_track="mic", invalid_kind=kind, prompt="actual")
            report = run_case(*args)
            assert_tracks(report, {"remote"}, {"mic"})
            assert any(reason.startswith("mic:") for reason in report["reasons"]), report

    print("live ASR cache compatibility checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
