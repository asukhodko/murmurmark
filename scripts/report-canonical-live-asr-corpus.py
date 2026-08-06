#!/usr/bin/env python3
"""Freeze real-session evidence for Canonical Live ASR Producer v1."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import authoritative_asr_cache as cache
from murmurmark_resource_policy import apply_resource_policy, resolve_resource_policy


SCHEMA = "murmurmark.canonical_live_asr_corpus_report/v1"
DEFAULT_OUTPUT = Path("sessions/_reports/authoritative-incremental-asr-v1/canonical-live-asr-producer-v1")
DEFAULT_MANIFEST = Path("docs/testing/canonical-live-asr-producer-v1-manifest.json")
DEFAULT_MODEL = Path.home() / ".local/share/murmurmark/models/whisper.cpp/ggml-large-v3-q5_0.bin"
MIC_FILTER = "highpass=f=100,lowpass=f=7600,alimiter=limit=0.98"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Report canonical live-origin ASR evidence across real sessions.")
    parser.add_argument("sessions", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--frozen-manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--refresh-manifest", action="store_true")
    parser.add_argument("--config", type=Path, default=Path("murmurmark.config.json"))
    parser.add_argument("--model", type=Path)
    parser.add_argument("--whisper-cli", default="whisper-cli")
    parser.add_argument("--language")
    parser.add_argument("--threads", type=int)
    parser.add_argument("--prompt-file", type=Path)
    parser.add_argument("--benchmark-mic", action="store_true")
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any] | None:
    return cache.read_json(path)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as file:
            for line in file:
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    rows.append(value)
    except OSError:
        pass
    return rows


def artifact(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"path": str(path), "exists": path.is_file()}
    if path.is_file():
        result.update(cache.file_fingerprint(path, include_path=False))
    return result


def load_settings(args: argparse.Namespace) -> dict[str, Any]:
    config = read_json(args.config.expanduser()) or {}
    transcription = config.get("transcription") if isinstance(config.get("transcription"), dict) else {}
    processing = config.get("processing") if isinstance(config.get("processing"), dict) else {}
    profile = str(processing.get("resource_profile") or "background")
    max_threads = processing.get("max_compute_threads")
    try:
        max_threads = int(max_threads) if max_threads is not None else None
    except (TypeError, ValueError):
        max_threads = None
    policy = resolve_resource_policy(profile, max_threads)
    model_value = args.model or Path(str(transcription.get("model") or DEFAULT_MODEL))
    prompt_value = args.prompt_file or (
        Path(str(transcription["prompt_file"])) if transcription.get("prompt_file") else None
    )
    prompt = prompt_value.expanduser().read_text(encoding="utf-8").strip() if prompt_value else None
    whisper_raw = shutil.which(args.whisper_cli) or args.whisper_cli
    return {
        "model": model_value.expanduser().resolve(),
        "whisper_cli": Path(whisper_raw).expanduser().resolve(),
        "language": args.language or str(transcription.get("language") or "ru"),
        "threads": int(args.threads or policy.asr_threads),
        "prompt": prompt or None,
        "resource_policy": apply_resource_policy(policy),
    }


def run(command: list[str]) -> float:
    started = time.monotonic()
    completed = subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"command_failed:{completed.returncode}:{command[0]}")
    return time.monotonic() - started


def mic_benchmark(session: Path, settings: dict[str, Any]) -> dict[str, Any]:
    source = session / "derived/asr/mic.wav"
    if not source.is_file():
        return {"status": "missing", "reason": "derived_asr_mic_missing", "elapsed_sec": None, "chunks": 0}
    with tempfile.TemporaryDirectory(prefix="murmurmark-canonical-live-mic-") as temporary:
        root = Path(temporary)
        prepared = root / "mic_speech.wav"
        ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
        run(
            [
                ffmpeg,
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(source),
                "-af",
                MIC_FILTER,
                "-ar",
                "16000",
                "-ac",
                "1",
                str(prepared),
            ]
        )
        total_frames = int(cache.wave_format(prepared)["frames"])
        specs = []
        hard = 0
        index = 1
        while hard < total_frames:
            end = min(total_frames, hard + 60 * 16_000)
            specs.append((index, max(0, hard - 5 * 16_000), min(total_frames, end + 5 * 16_000)))
            hard += 60 * 16_000
            index += 1
        elapsed = 0.0
        for index, start, end in specs:
            wav = root / f"mic-{index:04d}.wav"
            cache.slice_pcm_wav(prepared, wav, start, end)
            output = root / f"mic-{index:04d}"
            command = [
                str(settings["whisper_cli"]),
                "--model",
                str(settings["model"]),
                "--language",
                str(settings["language"]),
                "--threads",
                str(settings["threads"]),
                "--max-context",
                "0",
                "--output-txt",
                "--output-json",
                "--output-json-full",
                "--output-vtt",
                "--output-file",
                str(output),
                "--no-prints",
                "--log-score",
                "--suppress-nst",
                "--suppress-regex",
                r"^(Редактор субтитров|Продолжение следует|Спасибо за просмотр|Субтитры.*)$",
                "--file",
                str(wav),
            ]
            if settings["prompt"]:
                command += ["--prompt", str(settings["prompt"])]
            elapsed += run(command)
        return {
            "status": "completed",
            "elapsed_sec": round(elapsed, 6),
            "chunks": len(specs),
            "source": artifact(source),
        }


def session_row(session: Path, settings: dict[str, Any], benchmark: bool) -> dict[str, Any]:
    session = session.expanduser().resolve()
    producer_root = session / "derived/experiments/live-shadow-v1/authoritative-asr"
    producer_report_path = producer_root / "report.json"
    producer_report = read_json(producer_report_path) or {}
    chunks_path = producer_root / "chunks.jsonl"
    chunks = read_jsonl(chunks_path)
    materializer_path = session / "derived/live/live_asr_cache_report.json"
    materializer = read_json(materializer_path) or {}
    origins = sorted({str(row.get("provenance")) for row in chunks if row.get("provenance")})
    progress = producer_report.get("progress") if isinstance(producer_report.get("progress"), dict) else {}
    remote_elapsed = float(progress.get("decode_elapsed_sec") or 0.0)
    mic = mic_benchmark(session, settings) if benchmark else {"status": "not_run", "elapsed_sec": None, "chunks": 0}
    mic_elapsed = mic.get("elapsed_sec")
    modeled_parallel_reduction: float | None = None
    modeled_serial_reduction: float | None = None
    if isinstance(mic_elapsed, (int, float)) and mic_elapsed > 0 and remote_elapsed > 0:
        cold_parallel = max(float(mic_elapsed), remote_elapsed)
        modeled_parallel_reduction = max(0.0, (cold_parallel - float(mic_elapsed)) / cold_parallel)
        modeled_serial_reduction = remote_elapsed / (float(mic_elapsed) + remote_elapsed)
    strict_remote_verified = (
        materializer.get("verify_only") is True
        and "remote" in (materializer.get("verified_tracks") or [])
        and bool((materializer.get("track_compatibility") or {}).get("remote", {}).get("eligible"))
    )
    recording_time = origins == ["recording_time_committed_pcm"]
    reasons: list[str] = []
    if not strict_remote_verified:
        reasons.append("strict_remote_verification_missing")
    if not recording_time:
        reasons.append("recording_time_provenance_missing")
    if modeled_parallel_reduction is None:
        reasons.append("parallel_runtime_ceiling_unmeasured")
    elif modeled_parallel_reduction < 0.50:
        reasons.append(f"modeled_parallel_reduction_below_50_percent:{modeled_parallel_reduction:.6f}")
    manifest = read_json(session / "session.json") or {}
    health = manifest.get("health") if isinstance(manifest.get("health"), dict) else {}
    return {
        "session_id": session.name,
        "session": str(session),
        "duration_sec": health.get("actual_duration_sec"),
        "raw": {
            "mic": artifact(session / "audio/mic/000001.caf"),
            "remote": artifact(session / "audio/remote/000001.caf"),
        },
        "producer": {
            "report": artifact(producer_report_path),
            "chunks": artifact(chunks_path),
            "status": producer_report.get("status") or "missing",
            "origins": origins,
            "chunks_completed": progress.get("chunks_completed") or len(chunks),
            "chunks_expected": progress.get("chunks_expected") or len(chunks),
            "proven_sec": progress.get("proven_sec") or 0.0,
            "remote_decode_elapsed_sec": round(remote_elapsed, 6),
            "canonicalization_elapsed_sec": progress.get("canonicalization_elapsed_sec") or 0.0,
        },
        "strict_verification": {
            "report": artifact(materializer_path),
            "remote_verified": strict_remote_verified,
            "verified_tracks": materializer.get("verified_tracks") or [],
            "fallback_tracks": materializer.get("fallback_tracks") or [],
        },
        "mic_benchmark": mic,
        "runtime_ceiling": {
            "model": "two_track_workers_remote_precomputed_mic_post_stop",
            "modeled_parallel_reduction_ratio": (
                round(modeled_parallel_reduction, 6) if modeled_parallel_reduction is not None else None
            ),
            "modeled_serial_work_reduction_ratio": (
                round(modeled_serial_reduction, 6) if modeled_serial_reduction is not None else None
            ),
        },
        "eligible": not reasons,
        "reasons": reasons,
    }


def frozen_manifest(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "murmurmark.canonical_live_asr_frozen_manifest/v1",
        "producer_version": "0.2.0",
        "scope": "remote_only_v1",
        "sessions": [
            {
                "session_id": row["session_id"],
                "raw_sha256": {
                    "mic": row["raw"]["mic"].get("sha256"),
                    "remote": row["raw"]["remote"].get("sha256"),
                },
                "canonical_chunks_sha256": row["producer"]["chunks"].get("sha256"),
                "strict_remote_verified": row["strict_verification"]["remote_verified"],
                "evidence_origin": row["producer"]["origins"],
            }
            for row in payload["sessions"]
        ],
    }


def apply_frozen_manifest(rows: list[dict[str, Any]], manifest: dict[str, Any] | None) -> None:
    expected = {
        str(row.get("session_id")): row
        for row in (manifest or {}).get("sessions") or []
        if isinstance(row, dict)
    }
    for row in rows:
        frozen = expected.get(row["session_id"])
        if frozen is None:
            row["frozen_inputs_match"] = manifest is None
            if manifest is not None:
                row["reasons"].append("session_missing_from_frozen_manifest")
            continue
        expected_raw = frozen.get("raw_sha256") if isinstance(frozen.get("raw_sha256"), dict) else {}
        matches = all(
            row["raw"][track].get("sha256") == expected_raw.get(track)
            for track in ("mic", "remote")
        )
        row["frozen_inputs_match"] = matches
        if not matches:
            row["reasons"].append("raw_sha256_changed_from_frozen_manifest")
        row["eligible"] = not row["reasons"]


def write_report(
    output: Path,
    payload: dict[str, Any],
    *,
    manifest_path: Path,
    refresh_manifest: bool,
) -> None:
    output.mkdir(parents=True, exist_ok=True)
    cache.atomic_write_json(output / "canonical_live_asr_corpus_report.json", payload)
    manifest_payload = frozen_manifest(payload)
    cache.atomic_write_json(output / "canonical_live_asr_frozen_manifest.json", manifest_payload)
    if refresh_manifest or not manifest_path.exists():
        cache.atomic_write_json(manifest_path, manifest_payload)
    lines = [
        "# Canonical Live ASR Producer v1",
        "",
        f"- Decision: `{payload['decision']['status']}`",
        f"- Sessions: `{payload['summary']['sessions']}`",
        f"- Strict remote parity: `{payload['summary']['strict_remote_verified_sessions']}`",
        f"- Recording-time proofs: `{payload['summary']['recording_time_sessions']}`",
        f"- Sessions at >=50% modeled wall reduction: `{payload['summary']['sessions_at_least_50_percent']}`",
        "",
        "| Session | Origin | Remote parity | Mic s | Remote s | Parallel reduction |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for row in payload["sessions"]:
        mic_elapsed = row["mic_benchmark"].get("elapsed_sec")
        reduction = row["runtime_ceiling"].get("modeled_parallel_reduction_ratio")
        lines.append(
            f"| `{row['session_id']}` | `{','.join(row['producer']['origins']) or 'missing'}` | "
            f"`{str(row['strict_verification']['remote_verified']).lower()}` | "
            f"{mic_elapsed if mic_elapsed is not None else '-'} | "
            f"{row['producer']['remote_decode_elapsed_sec']} | "
            f"{reduction if reduction is not None else '-'} |"
        )
    lines += ["", "## Decision", "", payload["decision"]["reason"], ""]
    (output / "canonical_live_asr_corpus_report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    args = parse_args()
    settings = load_settings(args)
    rows = [session_row(session, settings, args.benchmark_mic) for session in args.sessions]
    manifest_path = args.frozen_manifest.expanduser()
    existing_manifest = None if args.refresh_manifest else read_json(manifest_path)
    apply_frozen_manifest(rows, existing_manifest)
    strict_count = sum(bool(row["strict_verification"]["remote_verified"]) for row in rows)
    recording_count = sum(row["producer"]["origins"] == ["recording_time_committed_pcm"] for row in rows)
    threshold_count = sum(
        (row["runtime_ceiling"].get("modeled_parallel_reduction_ratio") or 0.0) >= 0.50
        for row in rows
    )
    frozen_count = sum(bool(row.get("frozen_inputs_match")) for row in rows)
    promoted = (
        len(rows) >= 3
        and strict_count == len(rows)
        and recording_count == len(rows)
        and threshold_count == len(rows)
        and frozen_count == len(rows)
    )
    decision = {
        "status": "PROMOTE" if promoted else "DO_NOT_PROMOTE",
        "reason": (
            "Three real recording-time sessions prove strict parity and at least 50% modeled post-stop ASR reduction."
            if promoted
            else "Strict remote parity is proven by historical replay, but fresh recording-time evidence and/or the 50% wall-time gate are absent; ordinary batch remains authoritative."
        ),
    }
    payload = {
        "schema": SCHEMA,
        "generator": {"name": "report-canonical-live-asr-corpus", "version": "0.1.0"},
        "created_at": datetime.now(timezone.utc).isoformat(),
        "decision": decision,
        "summary": {
            "sessions": len(rows),
            "strict_remote_verified_sessions": strict_count,
            "recording_time_sessions": recording_count,
            "sessions_at_least_50_percent": threshold_count,
            "frozen_inputs_matching_sessions": frozen_count,
            "scope": "remote_only_v1",
            "mic_policy": "mandatory_post_stop_batch_after_echo_guard",
        },
        "settings": {
            "model": artifact(settings["model"]),
            "whisper_cli": artifact(settings["whisper_cli"]),
            "language": settings["language"],
            "threads": settings["threads"],
            "prompt_sha256": hashlib.sha256(settings["prompt"].encode("utf-8")).hexdigest()
            if settings["prompt"]
            else None,
            "resource_policy": settings["resource_policy"],
        },
        "sessions": rows,
    }
    payload["manifest"] = str(manifest_path)
    write_report(
        args.output.expanduser(),
        payload,
        manifest_path=manifest_path,
        refresh_manifest=args.refresh_manifest,
    )
    print(f"decision: {decision['status']}")
    print(f"report: {args.output / 'canonical_live_asr_corpus_report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
