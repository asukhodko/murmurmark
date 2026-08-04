#!/usr/bin/env python3
"""Materialize v2.12 audio with window-identical candidate ASR preparation."""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Callable

import numpy as np


ROOT = Path(__file__).resolve().parent.parent
PARENT_POLICY = ROOT / "policies/speaker-preserving-neural-echo-v2-10-audio.json"
OUTPUT_NAME = "speaker-preserving-neural-echo-v2-12"


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


PARENT = load_module(
    ROOT / "scripts/speaker-preserving-neural-echo-v2-11-audio.py",
    "murmurmark_spne_v212_audio_parent",
)
V210 = PARENT.PARENT
V27 = V210.V27
me_guard_dialogue_path: Callable[[Path], Path] = PARENT.me_guard_dialogue_path


def seed_prior_cache(session: Path, output: Path) -> dict[str, int]:
    source = session / "derived/preprocess/speaker-preserving-neural-echo-v2-11"
    copied_files = 0
    for name in (
        "proposal_manifest.json",
        "proposed_windows.jsonl",
        "rejected_windows.jsonl",
    ):
        source_file = source / name
        destination = output / name
        if source_file.is_file() and not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_file, destination)
            copied_files += 1
    copied_entries = 0
    source_cache = source / "diagnostic-asr-cache"
    destination_cache = output / "diagnostic-asr-cache"
    if source_cache.is_dir():
        destination_cache.mkdir(parents=True, exist_ok=True)
        for entry in source_cache.iterdir():
            destination = destination_cache / entry.name
            if not entry.is_dir() or destination.exists():
                continue
            try:
                shutil.copytree(entry, destination, copy_function=os.link)
            except OSError:
                shutil.rmtree(destination, ignore_errors=True)
                shutil.copytree(entry, destination, copy_function=shutil.copy2)
            copied_entries += 1
    return {"proposal_files": copied_files, "diagnostic_entries": copied_entries}


def transcribe_window_identical(
    *,
    args: argparse.Namespace,
    session: Path,
    prepared_path: Path,
    selected: list[dict[str, Any]],
    output: Path,
) -> Path:
    """Transcribe changed windows with the same slicing/filter order as the guard."""

    paths = V27.session_paths(session)
    baseline_payload = V27.read_json(paths["baseline_mic_asr"])
    baseline_rows = [
        row
        for row in baseline_payload.get("transcription", [])
        if isinstance(row, dict)
    ]
    candidate_path = output / "candidate_clean_mic_pcm16.wav"
    candidate_pcm, sample_rate = V27.read_audio(candidate_path, dtype="int16")
    if sample_rate != V27.SAMPLE_RATE:
        raise RuntimeError("unexpected candidate sample rate")
    candidate_pcm = np.asarray(candidate_pcm, dtype=np.int16)
    duration_ms = int(round(candidate_pcm.size / V27.SAMPLE_RATE * 1000.0))
    guard = V27.read_json(args.policy)["diagnostic_asr_guard"]
    specs = V27.TRANSCRIBER.build_window_specs(
        source_duration_ms=duration_ms,
        duration_ms=0,
        window_sec=int(guard["window_sec"]),
        overlap_sec=int(guard["overlap_sec"]),
    )
    changed = {int(row["diagnostic_chunk"]) for row in selected}
    rows: list[dict[str, Any]] = []
    cache_root = output / "diagnostic-asr-cache"
    workspace = output / "final-window-work" / str(os.getpid())
    workspace.mkdir(parents=True, exist_ok=True)
    chunk_records: list[dict[str, Any]] = []
    try:
        for spec in specs:
            index = int(spec["index"])
            if index in changed:
                seek = int(round(spec["seek_ms"] * V27.SAMPLE_RATE / 1000.0))
                clip_end = int(
                    round(spec["clip_end_ms"] * V27.SAMPLE_RATE / 1000.0)
                )
                raw_clip = workspace / f"chunk_{index:04d}.wav"
                prepared_clip = workspace / f"chunk_{index:04d}.speech.wav"
                V27.write_pcm16(raw_clip, candidate_pcm[seek:clip_end])
                V27.prepare_speech(raw_clip, prepared_clip)
                prepared, prepared_rate = V27.read_audio(
                    prepared_clip, dtype="int16"
                )
                if prepared_rate != V27.SAMPLE_RATE:
                    raise RuntimeError("unexpected prepared candidate sample rate")
                current = V27.transcribe_clip_cached(
                    prepared=np.asarray(prepared, dtype=np.int16),
                    spec=spec,
                    cache_root=cache_root,
                    whisper_model=args.whisper_model,
                    force=args.refresh,
                    audio_origin_ms=int(spec["seek_ms"]),
                )
                status = "candidate_audio_window_transcribed_or_cache"
            else:
                current = []
                for row in baseline_rows:
                    offsets = (
                        row.get("offsets")
                        if isinstance(row.get("offsets"), dict)
                        else {}
                    )
                    center = (
                        float(offsets.get("from") or 0.0)
                        + float(offsets.get("to") or 0.0)
                    ) / 2.0
                    if spec["hard_start_ms"] <= center < spec["hard_end_ms"]:
                        current.append(copy.deepcopy(row))
                status = "bit_exact_baseline_reuse"
            rows.extend(current)
            chunk_records.append(
                {"index": index, "status": status, "rows": len(current)}
            )
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
        try:
            workspace.parent.rmdir()
        except OSError:
            pass

    payload = copy.deepcopy(baseline_payload)
    payload["transcription"] = sorted(
        rows,
        key=lambda row: (
            float((row.get("offsets") or {}).get("from") or 0.0),
            float((row.get("offsets") or {}).get("to") or 0.0),
        ),
    )
    payload.setdefault("params", {})
    payload["params"].update(
        {
            "murmurmark_source_audio": str(candidate_path),
            "murmurmark_echo_profile": "speaker_preserving_neural_echo_v2_12",
            "murmurmark_preparation_mode": "window_identical_candidate_audio_v1",
        }
    )
    destination = output / "direct-asr/raw/mic.json"
    V27.write_json(destination, payload)
    V27.TRANSCRIBER.write_whisper_text_sidecars(destination.with_suffix(""))
    V27.write_json(
        output / "direct-asr/chunk_report.json",
        {
            "schema": "murmurmark.speaker_preserving_neural_echo_chunk_asr/v2.12",
            "changed_chunks": sorted(changed),
            "chunks": chunk_records,
            "candidate_audio_is_primary_whisper_input": True,
            "preparation_mode": "window_identical_candidate_audio_v1",
            "source_audio": V27.fingerprint(candidate_path, session),
        },
    )
    return destination


def run_session(args: argparse.Namespace) -> dict[str, Any]:
    session = args.session.expanduser().resolve()
    output = session / "derived/preprocess" / OUTPUT_NAME
    seeded = seed_prior_cache(session, output) if not args.refresh else {}
    original_output_name = PARENT.OUTPUT_NAME
    original_guard = PARENT.me_guard_dialogue_path
    original_transcribe_final = V27.transcribe_final
    PARENT.OUTPUT_NAME = OUTPUT_NAME
    PARENT.me_guard_dialogue_path = me_guard_dialogue_path
    V27.transcribe_final = transcribe_window_identical
    try:
        payload = PARENT.run_session(args)
    finally:
        PARENT.OUTPUT_NAME = original_output_name
        PARENT.me_guard_dialogue_path = original_guard
        V27.transcribe_final = original_transcribe_final
    payload["schema"] = "murmurmark.speaker_preserving_neural_echo_runtime/v2.12"
    payload["selection_contract"] = (
        "exact_local_window_identical_candidate_asr_with_outcome_rollback"
    )
    payload["parent_audio_runtime_sha256"] = V210.sha256(
        ROOT / "scripts/speaker-preserving-neural-echo-v2-11-audio.py"
    )
    payload["v2_11_cache_seed"] = seeded
    if payload.get("status") in {"candidate", "fallback"}:
        V210.write_json(output / "runtime_report.json", payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path)
    parser.add_argument("--policy", type=Path, default=PARENT_POLICY)
    parser.add_argument("--whisper-model", type=Path, required=True)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--exclude-chunk", action="append", type=int, default=[])
    args = parser.parse_args()
    args.output = ROOT / "sessions/_reports/speaker-preserving-neural-echo-v2-12"
    args.excluded_chunks = args.exclude_chunk
    payload = run_session(args)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if payload.get("status") in {"candidate", "fallback"} else 6


if __name__ == "__main__":
    raise SystemExit(main())
