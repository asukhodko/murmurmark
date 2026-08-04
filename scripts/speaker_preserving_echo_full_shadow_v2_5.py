#!/usr/bin/env python3
"""Assemble a full shadow transcript from audited chunk ASR without re-decoding it."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
MICRO_ASR_CACHE_PATHS = (
    "timeline-repair/micro_reasr",
    "timeline-repair-shadow_v2/micro_reasr",
    "opening-repair-shadow_v2/micro_asr",
)


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


PROMOTION = load_module(
    ROOT / "scripts/echo-suppression-promotion-v1.py",
    "murmurmark_spne_v25_shadow_common",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def stable_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def require_file(path: Path, label: str) -> Path:
    if not path.is_file():
        raise RuntimeError(f"{label} missing: {path}")
    return path


def copy_asr_sidecars(source_json: Path, destination_base: Path) -> None:
    destination_base.parent.mkdir(parents=True, exist_ok=True)
    for suffix in (".json", ".txt", ".vtt"):
        source = source_json.with_suffix(suffix)
        if source.is_file():
            shutil.copy2(source, destination_base.with_suffix(suffix))


def seed_file_cache(source: Path, destination: Path) -> int:
    """Seed an isolated shadow cache without duplicating unchanged clip files."""

    if not source.is_dir():
        return 0
    copied = 0
    for item in source.rglob("*"):
        if not item.is_file():
            continue
        target = destination / item.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            continue
        try:
            os.link(item, target)
        except OSError:
            shutil.copy2(item, target)
        copied += 1
    return copied


def seed_micro_asr_caches(
    session: Path, stage: Path, *, prior_stage_cache: Path | None = None
) -> dict[str, Any]:
    transcript_root = session / "derived/transcript-simple/whisper-cpp"
    stage_root = stage / "derived/transcript-simple/whisper-cpp"
    rows = []
    for relative_path in MICRO_ASR_CACHE_PATHS:
        source = transcript_root / relative_path
        destination = stage_root / relative_path
        baseline_seeded = seed_file_cache(source, destination)
        prior_seeded = (
            seed_file_cache(prior_stage_cache / relative_path, destination)
            if prior_stage_cache is not None
            else 0
        )
        rows.append(
            {
                "path": relative_path,
                "baseline_seeded_files": baseline_seeded,
                "prior_shadow_seeded_files": prior_seeded,
                "seeded_files": baseline_seeded + prior_seeded,
            }
        )
    return {
        "mode": "session_local_hardlink_or_copy",
        "rows": rows,
        "seeded_files": sum(int(row["seeded_files"]) for row in rows),
    }


def build_input_basis(
    *,
    session: Path,
    candidate_audio: Path,
    candidate_mic_asr: Path,
    candidate_asr_report: Path,
    whisper_model: Path,
    source_paths: dict[str, Path],
) -> dict[str, Any]:
    paths = {
        "candidate_audio": candidate_audio,
        "candidate_mic_asr": candidate_mic_asr,
        "candidate_asr_report": candidate_asr_report,
        "speaker_state": session / "derived/preprocess/echo/speaker_state.jsonl",
        "baseline_quality": session
        / "derived/transcript-simple/whisper-cpp/resolved/quality_report.shadow_v2.json",
        "baseline_dialogue": session
        / "derived/transcript-simple/whisper-cpp/resolved/clean_dialogue.shadow_v2.json",
        "baseline_overlaps": session
        / "derived/transcript-simple/whisper-cpp/resolved/overlaps.shadow_v2.json",
        "baseline_transcript": session
        / "derived/transcript-simple/whisper-cpp/resolved/transcript.shadow_v2.md",
        "baseline_transcribe_report": session
        / "derived/transcript-simple/whisper-cpp/resolved/transcribe_simple_report.shadow_v2.json",
        "baseline_verdict": session
        / "derived/synthesis-simple/extractive/quality_verdict.json",
        "remote_asr": session / "derived/transcript-simple/whisper-cpp/raw/remote.json",
        "transcriber": ROOT / "scripts/transcribe-simple-whispercpp.py",
        "whisper_model": whisper_model,
        **source_paths,
    }
    for label, path in paths.items():
        require_file(path, label)
    return {
        label: {
            "path": PROMOTION.relative(path, session),
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        }
        for label, path in sorted(paths.items())
    }


def full_shadow_stage(
    *,
    session: Path,
    output_root: Path,
    candidate: str,
    candidate_mic_asr: Path,
    candidate_asr_report: Path,
    whisper_model: Path,
    refresh: bool,
    reuse_micro_asr_cache: bool = False,
) -> dict[str, Any]:
    """Run timeline repair from already audited primary candidate ASR.

    The candidate remains the primary mic source. Original clean/raw mic sources
    stay available to micro-ASR, so timeline repair can fail open instead of
    treating one filtered waveform as every source of evidence.
    """

    candidate_dir = output_root / "candidates" / candidate
    candidate_audio = require_file(candidate_dir / "mic_for_asr.wav", "candidate audio")
    candidate_mic_asr = require_file(candidate_mic_asr, "candidate mic ASR")
    candidate_asr_report = require_file(candidate_asr_report, "candidate ASR report")
    stage = candidate_dir / "full-shadow-precomputed-session"
    report_path = candidate_dir / "full_shadow_precomputed_report.json"
    original_audio = session / "derived/preprocess/audio"
    source_paths = {
        "original_clean_local_fir": original_audio / "mic_clean_local_fir.wav",
        "original_raw_for_asr": original_audio / "mic_raw_for_asr.wav",
        "original_remote_for_aec": original_audio / "remote_for_aec.wav",
        "original_remote_export": session / "derived/asr/remote.wav",
    }
    input_basis = build_input_basis(
        session=session,
        candidate_audio=candidate_audio,
        candidate_mic_asr=candidate_mic_asr,
        candidate_asr_report=candidate_asr_report,
        whisper_model=whisper_model,
        source_paths=source_paths,
    )
    input_fingerprint = stable_digest(input_basis)
    existing = PROMOTION.read_json(report_path)
    if (
        not refresh
        and existing.get("input_fingerprint") == input_fingerprint
        and existing.get("status") == "completed"
    ):
        return existing

    prior_stage_cache = candidate_dir / ".micro-asr-reuse"
    if prior_stage_cache.exists():
        shutil.rmtree(prior_stage_cache)
    if reuse_micro_asr_cache and stage.exists():
        old_stage_root = stage / "derived/transcript-simple/whisper-cpp"
        for relative_path in MICRO_ASR_CACHE_PATHS:
            seed_file_cache(
                old_stage_root / relative_path,
                prior_stage_cache / relative_path,
            )
    if stage.exists():
        shutil.rmtree(stage)
    (stage / "audio/mic").mkdir(parents=True, exist_ok=True)
    (stage / "audio/remote").mkdir(parents=True, exist_ok=True)
    PROMOTION.safe_link(candidate_audio, stage / "audio/mic/000001.wav")
    remote_source = require_file(session / "audio/remote/000001.caf", "raw remote")
    PROMOTION.safe_link(remote_source, stage / "audio/remote/000001.caf")

    manifest = copy.deepcopy(PROMOTION.read_json(session / "session.json"))
    mic_rows = ((manifest.get("files") or {}).get("mic") or [])
    if mic_rows:
        mic_rows[0]["path"] = "audio/mic/000001.wav"
        mic_rows[0]["channels"] = 1
        mic_rows[0]["sample_rate"] = 16_000
        mic_rows[0]["bytes"] = candidate_audio.stat().st_size
        mic_rows[0]["frames"] = PROMOTION.read_wav(candidate_audio).size
    PROMOTION.write_json(stage / "session.json", manifest)
    for optional in ("events.jsonl", "pipeline_job.json"):
        source = session / optional
        if source.exists():
            shutil.copy2(source, stage / optional)

    stage_asr = stage / "derived/asr"
    stage_audio = stage / "derived/preprocess/audio"
    stage_echo = stage / "derived/preprocess/echo"
    stage_asr.mkdir(parents=True, exist_ok=True)
    stage_audio.mkdir(parents=True, exist_ok=True)
    stage_echo.mkdir(parents=True, exist_ok=True)
    PROMOTION.safe_link(candidate_audio, stage_asr / "mic.wav")
    PROMOTION.safe_link(source_paths["original_remote_export"], stage_asr / "remote.wav")
    PROMOTION.safe_link(candidate_audio, stage_audio / "mic_for_asr.wav")
    PROMOTION.safe_link(candidate_audio, stage_audio / "mic_role_masked_for_asr.wav")
    PROMOTION.safe_link(
        source_paths["original_clean_local_fir"],
        stage_audio / "mic_clean_local_fir.wav",
    )
    PROMOTION.safe_link(
        source_paths["original_raw_for_asr"],
        stage_audio / "mic_raw_for_asr.wav",
    )
    PROMOTION.safe_link(
        source_paths["original_remote_for_aec"],
        stage_audio / "remote_for_aec.wav",
    )
    shutil.copy2(
        session / "derived/preprocess/echo/speaker_state.jsonl",
        stage_echo / "speaker_state.jsonl",
    )
    cache_seed = (
        seed_micro_asr_caches(
            session,
            stage,
            prior_stage_cache=(prior_stage_cache if prior_stage_cache.exists() else None),
        )
        if reuse_micro_asr_cache
        else {"mode": "disabled", "rows": [], "seeded_files": 0}
    )
    shutil.rmtree(prior_stage_cache, ignore_errors=True)

    raw_dir = stage / "derived/transcript-simple/whisper-cpp/raw"
    copy_asr_sidecars(candidate_mic_asr, raw_dir / "mic")
    prompt_path = PROMOTION.copy_remote_asr_cache(session, stage)
    murmurmark_bin = ROOT / ".build/debug/murmurmark"
    if not murmurmark_bin.exists():
        resolved = shutil.which("murmurmark")
        if not resolved:
            raise RuntimeError("murmurmark executable not found for full shadow")
        murmurmark_bin = Path(resolved)
    command = [
        sys.executable,
        str(ROOT / "scripts/transcribe-simple-whispercpp.py"),
        str(stage),
        "--model",
        str(whisper_model),
        "--language",
        "ru",
        "--repair-profile",
        "shadow_v2",
        "--track-workers",
        "2",
        "--threads",
        "6",
        "--micro-asr-workers",
        "4",
        "--murmurmark-bin",
        str(murmurmark_bin),
        "--skip-export",
        "--skip-transcribe",
        "--comparison-reference-resolved-dir",
        str(session / "derived/transcript-simple/whisper-cpp/resolved"),
        "--comparison-reference-suffix",
        ".shadow_v2",
    ]
    if prompt_path:
        command.extend(["--prompt-file", prompt_path])
    started = time.monotonic()
    subprocess.run(command, check=True)
    assembly_runtime = time.monotonic() - started
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/synthesize-simple-extractive.py"),
            str(stage),
            "--transcript-profile",
            "shadow_v2",
        ],
        check=True,
    )

    resolved = stage / "derived/transcript-simple/whisper-cpp/resolved"
    candidate_quality_path = resolved / "quality_report.shadow_v2.json"
    candidate_quality = PROMOTION.read_json(candidate_quality_path)
    baseline_quality = PROMOTION.read_json(
        session / "derived/transcript-simple/whisper-cpp/resolved/quality_report.shadow_v2.json"
    )
    baseline_remote_seconds = float(
        baseline_quality.get("remote_duplicate_in_me_seconds") or 0.0
    )
    candidate_remote_seconds = float(
        candidate_quality.get("remote_duplicate_in_me_seconds") or 0.0
    )
    baseline_dialogue = (
        session / "derived/transcript-simple/whisper-cpp/resolved/clean_dialogue.shadow_v2.json"
    )
    candidate_dialogue = resolved / "clean_dialogue.shadow_v2.json"
    transcript_path = resolved / "transcript.shadow_v2.md"
    overlaps_path = resolved / "overlaps.shadow_v2.json"
    notes_path = stage / "derived/synthesis-simple/extractive/notes.md"
    verdict_path = stage / "derived/synthesis-simple/extractive/quality_verdict.json"
    candidate_verdict = PROMOTION.read_json(verdict_path)
    baseline_verdict = PROMOTION.read_json(
        session / "derived/synthesis-simple/extractive/quality_verdict.json"
    )
    direct_report = PROMOTION.read_json(candidate_asr_report)
    staged_mic_asr = raw_dir / "mic.json"
    gates = {
        "unrepaired_crossings_zero": int(
            candidate_quality.get("unrepaired_long_mic_crossings_count") or 0
        )
        == 0,
        "golden_failures_zero": int(candidate_quality.get("golden_phrase_fail_count") or 0)
        == 0,
        "local_recall_preserved": float(
            candidate_quality.get("local_only_island_recall") or 0.0
        )
        >= float(baseline_quality.get("local_only_island_recall") or 0.0),
        "chronology_not_worse": float(
            candidate_quality.get("cross_role_overlap_gt2_seconds") or 0.0
        )
        <= float(baseline_quality.get("cross_role_overlap_gt2_seconds") or 0.0),
        "remote_content_unchanged": PROMOTION.dialogue_remote_fingerprint(baseline_dialogue)
        == PROMOTION.dialogue_remote_fingerprint(candidate_dialogue),
        "notes_evidence_valid": PROMOTION.evidence_ids_valid(stage),
        "verdict_not_worse": baseline_verdict.get("verdict")
        in {"good", "usable_with_review", "risky", "failed"}
        and PROMOTION.verdict_rank(candidate_verdict.get("verdict"))
        <= PROMOTION.verdict_rank(baseline_verdict.get("verdict")),
        "guarded_export_inputs_complete": all(
            path.exists()
            for path in (candidate_dialogue, transcript_path, notes_path, verdict_path)
        ),
        "candidate_asr_is_primary": sha256(staged_mic_asr) == sha256(candidate_mic_asr),
        "candidate_audio_is_primary": sha256(stage_asr / "mic.wav")
        == sha256(candidate_audio),
        "candidate_asr_provenance_valid": direct_report.get(
            "candidate_audio_is_primary_whisper_input"
        )
        is True,
        "original_clean_source_preserved": sha256(
            stage_audio / "mic_clean_local_fir.wav"
        )
        == sha256(source_paths["original_clean_local_fir"]),
        "original_raw_source_preserved": sha256(stage_audio / "mic_raw_for_asr.wav")
        == sha256(source_paths["original_raw_for_asr"]),
    }
    output_fingerprints = {
        name: PROMOTION.fingerprint(path, session)
        for name, path in {
            "dialogue": candidate_dialogue,
            "transcript": transcript_path,
            "quality": candidate_quality_path,
            "overlaps": overlaps_path,
            "notes": notes_path,
            "verdict": verdict_path,
        }.items()
        if path.exists()
    }
    replay_verified = (
        existing.get("output_fingerprints") == output_fingerprints
        if existing.get("input_fingerprint") == input_fingerprint
        and existing.get("status") == "completed"
        else None
    )
    payload = {
        "schema": "murmurmark.echo_suppression_full_shadow/v2.5",
        "status": "completed",
        "candidate": candidate,
        "primary_asr_mode": "precomputed_audited_chunks",
        "whole_file_primary_redecode": False,
        "input_fingerprint": input_fingerprint,
        "input_basis": input_basis,
        "stage": PROMOTION.relative(stage, session),
        "command": command,
        "micro_asr_cache_seed": cache_seed,
        "outputs": {
            "transcript": PROMOTION.relative(transcript_path, session),
            "quality": PROMOTION.relative(candidate_quality_path, session),
            "overlaps": PROMOTION.relative(overlaps_path, session),
            "notes": PROMOTION.relative(notes_path, session),
            "verdict": PROMOTION.relative(verdict_path, session),
        },
        "output_fingerprints": output_fingerprints,
        "determinism": {
            "previous_completed_same_input": replay_verified is not None,
            "replay_verified": replay_verified,
        },
        "metrics": {
            "remote_duplicate_in_me_seconds_baseline": round(baseline_remote_seconds, 3),
            "remote_duplicate_in_me_seconds_candidate": round(candidate_remote_seconds, 3),
            "remote_duplicate_reduction_ratio": round(
                (baseline_remote_seconds - candidate_remote_seconds)
                / max(baseline_remote_seconds, 1.0e-9),
                6,
            )
            if baseline_remote_seconds > 0
            else None,
            "local_only_island_recall_baseline": baseline_quality.get(
                "local_only_island_recall"
            ),
            "local_only_island_recall_candidate": candidate_quality.get(
                "local_only_island_recall"
            ),
            "cross_role_overlap_gt2_seconds_baseline": baseline_quality.get(
                "cross_role_overlap_gt2_seconds"
            ),
            "cross_role_overlap_gt2_seconds_candidate": candidate_quality.get(
                "cross_role_overlap_gt2_seconds"
            ),
            "needs_review_count_baseline": baseline_quality.get("needs_review_count"),
            "needs_review_count_candidate": candidate_quality.get("needs_review_count"),
            "timeline_assembly_runtime_sec": round(assembly_runtime, 3),
            "baseline_verdict": baseline_verdict.get("verdict"),
            "candidate_verdict": candidate_verdict.get("verdict"),
        },
        "gates": gates,
        "passed": all(gates.values()),
    }
    PROMOTION.write_json(report_path, payload)
    return payload
