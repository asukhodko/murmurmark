#!/usr/bin/env python3
"""Materialize v2.14 audio with window-identical candidate ASR preparation."""

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
OUTPUT_NAME = "speaker-preserving-neural-echo-v2-14"
ASR_INFLUENCE_CONTEXT_SEC = 3.0


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
    "murmurmark_spne_v214_audio_parent",
)
V210 = PARENT.PARENT
V27 = V210.V27
me_guard_dialogue_path: Callable[[Path], Path] = PARENT.me_guard_dialogue_path


def seed_prior_cache(session: Path, output: Path) -> dict[str, Any]:
    source = session / "derived/preprocess/speaker-preserving-neural-echo-v2-13"
    output.mkdir(parents=True, exist_ok=True)
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
    selection_source = source / "selected_windows.jsonl"
    decisions_source = source / "diagnostic_chunk_decisions.jsonl"
    rollback_archive = source / "full-shadow-audio-rollback"
    archived_selection = rollback_archive / "initial_selected_windows.jsonl"
    archived_decisions = rollback_archive / "initial_diagnostic_chunk_decisions.jsonl"
    if archived_selection.is_file() and archived_decisions.is_file():
        selection_source = archived_selection
        decisions_source = archived_decisions
    else:
        for iteration in sorted(rollback_archive.glob("iteration-*")):
            iteration_selection = iteration / "selected_windows.jsonl"
            iteration_decisions = iteration / "diagnostic_chunk_decisions.jsonl"
            if iteration_selection.is_file() and iteration_decisions.is_file():
                selection_source = iteration_selection
                decisions_source = iteration_decisions
                break
    selection_seeded = False
    if selection_source.is_file() and decisions_source.is_file():
        shutil.copy2(selection_source, output / "selected_windows.jsonl")
        shutil.copy2(decisions_source, output / "diagnostic_chunk_decisions.jsonl")
        selection_seeded = True

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
    return {
        "proposal_files": copied_files,
        "diagnostic_entries": copied_entries,
        "selection_seeded": selection_seeded,
        "selection_source": str(selection_source) if selection_seeded else None,
        "decision_source": str(decisions_source) if selection_seeded else None,
    }


def row_center_sec(row: dict[str, Any]) -> float:
    offsets = row.get("offsets") if isinstance(row.get("offsets"), dict) else {}
    return (
        float(offsets.get("from") or 0.0) + float(offsets.get("to") or 0.0)
    ) / 2000.0


def build_influence_intervals(
    selected: list[dict[str, Any]],
) -> list[tuple[float, float]]:
    intervals: list[tuple[float, float]] = []
    for row in sorted(selected, key=lambda item: float(item["start"])):
        start = max(0.0, float(row["start"]) - ASR_INFLUENCE_CONTEXT_SEC)
        end = float(row["end"]) + ASR_INFLUENCE_CONTEXT_SEC
        if intervals and start <= intervals[-1][1]:
            intervals[-1] = (intervals[-1][0], max(intervals[-1][1], end))
        else:
            intervals.append((start, end))
    return intervals


def splice_identity_bounded_rows(
    *,
    baseline_rows: list[dict[str, Any]],
    candidate_rows: list[dict[str, Any]],
    influence_intervals: list[tuple[float, float]],
) -> list[dict[str, Any]]:
    def influenced(row: dict[str, Any]) -> bool:
        center = row_center_sec(row)
        return any(start <= center < end for start, end in influence_intervals)

    rows = [copy.deepcopy(row) for row in candidate_rows if influenced(row)]
    rows.extend(copy.deepcopy(row) for row in baseline_rows if not influenced(row))
    return rows


def filter_selected_windows(
    rows: list[dict[str, Any]], excluded_window_ids: set[str]
) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if str(row.get("proposal_id") or "") not in excluded_window_ids
    ]


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
    influence_intervals = build_influence_intervals(selected)

    rows: list[dict[str, Any]] = []
    cache_root = output / "diagnostic-asr-cache"
    workspace = output / "final-window-work" / str(os.getpid())
    workspace.mkdir(parents=True, exist_ok=True)
    chunk_records: list[dict[str, Any]] = []
    try:
        for spec in specs:
            index = int(spec["index"])
            baseline_current = [
                copy.deepcopy(row)
                for row in baseline_rows
                if spec["hard_start_ms"]
                <= row_center_sec(row) * 1000.0
                < spec["hard_end_ms"]
            ]
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
                candidate_current = V27.transcribe_clip_cached(
                    prepared=np.asarray(prepared, dtype=np.int16),
                    spec=spec,
                    cache_root=cache_root,
                    whisper_model=args.whisper_model,
                    force=args.refresh,
                    audio_origin_ms=int(spec["seek_ms"]),
                )
                current = splice_identity_bounded_rows(
                    baseline_rows=baseline_current,
                    candidate_rows=candidate_current,
                    influence_intervals=influence_intervals,
                )
                status = "candidate_audio_identity_bounded_splice"
            else:
                candidate_current = []
                current = baseline_current
                status = "bit_exact_baseline_reuse"
            rows.extend(current)
            chunk_records.append(
                {
                    "index": index,
                    "status": status,
                    "rows": len(current),
                    "candidate_rows": len(
                        [
                            row
                            for row in candidate_current
                            if row in current and row not in baseline_current
                        ]
                    ),
                    "baseline_rows": len(
                        [row for row in current if row in baseline_current]
                    ),
                }
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
            "murmurmark_echo_profile": "speaker_preserving_neural_echo_v2_14",
            "murmurmark_preparation_mode": "identity_bounded_candidate_audio_v1",
        }
    )
    destination = output / "direct-asr/raw/mic.json"
    V27.write_json(destination, payload)
    V27.TRANSCRIBER.write_whisper_text_sidecars(destination.with_suffix(""))
    V27.write_json(
        output / "direct-asr/chunk_report.json",
        {
            "schema": "murmurmark.speaker_preserving_neural_echo_chunk_asr/v2.14",
            "changed_chunks": sorted(changed),
            "chunks": chunk_records,
            "candidate_audio_is_primary_whisper_input": True,
            "preparation_mode": "identity_bounded_candidate_audio_v1",
            "asr_influence_context_sec": ASR_INFLUENCE_CONTEXT_SEC,
            "influence_intervals": [
                {"start": round(start, 3), "end": round(end, 3)}
                for start, end in influence_intervals
            ],
            "source_audio": V27.fingerprint(candidate_path, session),
        },
    )
    return destination


def run_session(args: argparse.Namespace) -> dict[str, Any]:
    session = args.session.expanduser().resolve()
    output = session / "derived/preprocess" / OUTPUT_NAME
    seeded = seed_prior_cache(session, output) if not args.refresh else {}
    excluded_window_ids = sorted(
        {
            str(value)
            for value in getattr(args, "excluded_window_ids", [])
            if str(value)
        }
    )
    selected_path = output / "selected_windows.jsonl"
    decisions_path = output / "diagnostic_chunk_decisions.jsonl"
    reuse_selection = selected_path.is_file() and decisions_path.is_file() and (
        bool(excluded_window_ids) or bool(seeded.get("selection_seeded"))
    )
    cached_selected_all = V27.read_jsonl(selected_path) if reuse_selection else []
    excluded_set = set(excluded_window_ids)
    cached_selected = filter_selected_windows(cached_selected_all, excluded_set)
    cached_decisions = V27.read_jsonl(decisions_path) if reuse_selection else []
    use_cached_selection = reuse_selection
    local_rollbacks: list[dict[str, Any]] = []
    original_output_name = PARENT.OUTPUT_NAME
    original_guard = PARENT.me_guard_dialogue_path
    original_transcribe_final = V27.transcribe_final
    original_diagnostic_select = V27.diagnostic_select

    def cached_diagnostic_select(
        **kwargs: Any,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        if use_cached_selection:
            return copy.deepcopy(cached_selected), copy.deepcopy(cached_decisions)
        return original_diagnostic_select(**kwargs)

    PARENT.OUTPUT_NAME = OUTPUT_NAME
    PARENT.me_guard_dialogue_path = me_guard_dialogue_path
    V27.transcribe_final = transcribe_window_identical
    V27.diagnostic_select = cached_diagnostic_select
    try:
        payload: dict[str, Any] = {}
        for pass_index in range(1, 6):
            if use_cached_selection:
                # The inherited runtime basis does not know about proposal IDs.
                # Never let an earlier report bypass a filtered selection.
                (output / "runtime_report.json").unlink(missing_ok=True)
            payload = PARENT.run_session(args)
            if payload.get("status") == "candidate":
                break
            details = (
                payload.get("details")
                if isinstance(payload.get("details"), dict)
                else {}
            )
            checks = details.get("checks") if isinstance(details.get("checks"), dict) else {}
            metrics = details.get("metrics") if isinstance(details.get("metrics"), dict) else {}
            retention = (
                metrics.get("local_retention")
                if isinstance(metrics.get("local_retention"), dict)
                else {}
            )
            examples = [
                row
                for row in retention.get("low_retention_examples", [])
                if isinstance(row, dict)
            ]
            if (
                payload.get("reason") != "final_development_gates_failed"
                or checks.get("local_retention") is not False
                or not examples
            ):
                break
            if not use_cached_selection:
                cached_selected_all = V27.read_jsonl(selected_path)
                cached_decisions = V27.read_jsonl(decisions_path)
                use_cached_selection = bool(cached_selected_all and cached_decisions)
            new_ids: set[str] = set()
            evidence: list[dict[str, Any]] = []
            for example in examples:
                start = float(example.get("start") or 0.0)
                end = float(example.get("end") or start)
                matching = [
                    row
                    for row in cached_selected_all
                    if str(row.get("proposal_id") or "") not in excluded_set
                    and float(row.get("end") or 0.0)
                    > start - ASR_INFLUENCE_CONTEXT_SEC
                    and float(row.get("start") or 0.0)
                    < end + ASR_INFLUENCE_CONTEXT_SEC
                ]
                ids = [str(row["proposal_id"]) for row in matching]
                new_ids.update(ids)
                evidence.append(
                    {
                        "start": start,
                        "end": end,
                        "token_recall": example.get("token_recall"),
                        "rollback_window_ids": ids,
                    }
                )
            if not use_cached_selection or not new_ids:
                break
            excluded_set.update(new_ids)
            cached_selected = filter_selected_windows(cached_selected_all, excluded_set)
            local_rollbacks.append(
                {
                    "schema": "murmurmark.speaker_preserving_neural_echo_local_rollback/v2.14",
                    "pass": pass_index,
                    "reason": "direct_asr_local_token_regression",
                    "new_excluded_window_ids": sorted(new_ids),
                    "cumulative_excluded_window_ids": sorted(excluded_set),
                    "evidence": evidence,
                }
            )
    finally:
        PARENT.OUTPUT_NAME = original_output_name
        PARENT.me_guard_dialogue_path = original_guard
        V27.transcribe_final = original_transcribe_final
        V27.diagnostic_select = original_diagnostic_select
    payload["schema"] = "murmurmark.speaker_preserving_neural_echo_runtime/v2.14"
    payload["selection_contract"] = (
        "exact_local_identity_bounded_candidate_asr_with_window_rollback"
    )
    payload["parent_audio_runtime_sha256"] = V210.sha256(
        ROOT / "scripts/speaker-preserving-neural-echo-v2-11-audio.py"
    )
    payload["v2_13_cache_seed"] = seeded
    V27.write_jsonl(output / "direct_asr_local_rollbacks.jsonl", local_rollbacks)
    excluded_window_ids = sorted(excluded_set)
    payload["diagnostic_selection_reuse"] = {
        "used": use_cached_selection,
        "reason": (
            "excluded_window_ids_only_remove_previously_audited_windows"
            if excluded_window_ids
            else "seeded_v2_13_audited_selection"
        )
        if reuse_selection
        else "initial_or_refresh_run",
        "cached_selected_rows": len(cached_selected_all),
        "selected_rows": len(cached_selected),
        "decision_rows": len(cached_decisions),
        "excluded_window_ids": excluded_window_ids,
    }
    payload["direct_asr_local_rollback"] = {
        "passes": len(local_rollbacks),
        "excluded_window_ids": excluded_window_ids,
        "exact_local_required": True,
    }
    if payload.get("status") in {"candidate", "fallback"}:
        V210.write_json(output / "runtime_report.json", payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path)
    parser.add_argument("--policy", type=Path, default=PARENT_POLICY)
    parser.add_argument("--whisper-model", type=Path, required=True)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--exclude-window", action="append", default=[])
    args = parser.parse_args()
    args.output = ROOT / "sessions/_reports/speaker-preserving-neural-echo-v2-14"
    args.excluded_window_ids = args.exclude_window
    payload = run_session(args)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if payload.get("status") in {"candidate", "fallback"} else 6


if __name__ == "__main__":
    raise SystemExit(main())
