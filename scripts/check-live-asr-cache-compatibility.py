#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
MATERIALIZER = REPO_ROOT / "scripts/materialize-live-asr-cache.py"


def load_materializer() -> Any:
    spec = importlib.util.spec_from_file_location("murmurmark_live_asr_cache", MATERIALIZER)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load live ASR cache materializer")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_materializer()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_session(
    root: Path,
    name: str,
    *,
    valid_tracks: set[str] = frozenset(("mic", "remote")),
    geometry_sec: int = 60,
    prompt: str | None = None,
    proof_prompt: str | None = None,
    model_hash_override: str | None = None,
    remote_prep: str = "loudnorm",
    corrupt_track: str | None = None,
) -> tuple[Path, Path, Path, Path | None]:
    session = root / name
    model = root / "model.bin"
    whisper_cli = root / "whisper-cli"
    model.write_bytes(b"model-v1")
    whisper_cli.write_bytes(b"#!/bin/sh\nexit 0\n")
    whisper_cli.chmod(0o755)
    prompt_path = root / f"{name}.prompt.txt" if prompt is not None else None
    if prompt_path is not None:
        prompt_path.write_text(prompt, encoding="utf-8")

    write_json(
        session / "derived/live/live_pipeline_report.json",
        {"schema": "murmurmark.live_pipeline_report/v1", "status": "completed"},
    )
    chunks: list[dict[str, Any]] = []
    windows = [(1, 0, geometry_sec, 0, geometry_sec + 5), (2, geometry_sec, geometry_sec + 20, geometry_sec - 5, geometry_sec + 20)]
    for index, start, end, clip_start, clip_end in windows:
        row: dict[str, Any] = {
            "schema": "murmurmark.live_chunk/v1",
            "index": index,
            "start_sec": start,
            "end_sec": end,
            "duration_sec": end - start,
            "clip_start_sec": clip_start,
            "clip_end_sec": clip_end,
        }
        for track, prep in (("mic", "speech"), ("remote", remote_prep)):
            chunk_dir = session / f"derived/live/chunks/{index:04d}"
            wav = chunk_dir / f"{track}.wav"
            raw_json = chunk_dir / f"{track}.json"
            wav.parent.mkdir(parents=True, exist_ok=True)
            wav.write_bytes(f"{name}:{track}:{index}".encode())
            payload = {
                "params": {"model": str(model.resolve()), "language": "ru"},
                "transcription": [
                    {
                        "text": f"{track}-{index}",
                        "offsets": {"from": 1000, "to": 2000},
                        "tokens": [],
                    }
                ],
            }
            if corrupt_track == track and index == 1:
                raw_json.write_text("{broken", encoding="utf-8")
            else:
                write_json(raw_json, payload)
            source: dict[str, Any] = {
                "wav": str(wav),
                "audio_prep": prep,
                "hard_start_sec": start,
                "hard_end_sec": end,
                "clip_start_sec": clip_start,
                "clip_end_sec": clip_end,
                "asr": {"status": "passed", "json": str(raw_json)},
            }
            if track in valid_tracks:
                settings = MODULE.compatibility_settings(
                    source=track,
                    model=model.resolve(),
                    language="ru",
                    max_context=0,
                    prompt=proof_prompt if proof_prompt is not None else prompt,
                    asr_mode="windowed",
                    asr_window_sec=60,
                    asr_overlap_sec=5,
                    audio_prep="speech" if track == "mic" else "loudnorm",
                )
                source["batch_cache_compatibility"] = {
                    "schema": "murmurmark.live_batch_asr_compatibility/v1",
                    "source_kind": "exact_batch_prepared_window",
                    "prepared_audio_sha256": sha256_file(wav),
                    "model_sha256": model_hash_override or sha256_file(model),
                    "whisper_cli_sha256": sha256_file(whisper_cli),
                    "settings_sha256": MODULE.content_sha256(settings),
                }
            row[track] = source
        chunks.append(row)
    chunks_path = session / "derived/live/chunks.jsonl"
    chunks_path.parent.mkdir(parents=True, exist_ok=True)
    chunks_path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in chunks), encoding="utf-8")
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
        "--asr-window-sec",
        "60",
        "--asr-overlap-sec",
        "5",
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


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="murmurmark-live-asr-compat-") as raw_root:
        root = Path(raw_root)

        report = run_case(*build_session(root, "both"))
        assert report["status"] == "materialized"
        assert_tracks(report, {"mic", "remote"}, set())

        report = run_case(*build_session(root, "mic-only", valid_tracks={"mic"}))
        assert report["status"] == "partially_materialized"
        assert_tracks(report, {"mic"}, {"remote"})

        report = run_case(*build_session(root, "remote-only", valid_tracks={"remote"}))
        assert report["status"] == "partially_materialized"
        assert_tracks(report, {"remote"}, {"mic"})

        report = run_case(*build_session(root, "geometry", geometry_sec=30))
        assert_tracks(report, set(), {"mic", "remote"})
        assert any("window_duration_mismatch" in reason for reason in report["reasons"])

        report = run_case(*build_session(root, "model", model_hash_override="bad"))
        assert_tracks(report, set(), {"mic", "remote"})
        assert any("model_hash_mismatch" in reason for reason in report["reasons"])

        report = run_case(*build_session(root, "prompt", prompt="actual", proof_prompt="different"))
        assert_tracks(report, set(), {"mic", "remote"})
        assert any("asr_settings_identity_mismatch" in reason for reason in report["reasons"])

        report = run_case(*build_session(root, "prep", remote_prep="raw"))
        assert_tracks(report, {"mic"}, {"remote"})
        assert any("remote:audio_prep_mismatch" in reason for reason in report["reasons"])

        report = run_case(*build_session(root, "corrupt", corrupt_track="mic"))
        assert_tracks(report, {"remote"}, {"mic"})
        assert any("mic:asr_json_invalid" in reason for reason in report["reasons"])

    print("live ASR cache compatibility checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
