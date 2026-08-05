#!/usr/bin/env python3
"""Materialize only byte-compatible live whisper.cpp chunks into authoritative batch cache."""

from __future__ import annotations

import argparse
import copy
import json
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import authoritative_asr_cache as authoritative_cache


SCHEMA = "murmurmark.live_asr_cache_report/v2"
SCRIPT_VERSION = "0.4.0"
LIVE_PROOF_SCHEMA = "murmurmark.authoritative_live_asr_chunk/v1"
DEFAULT_MODEL = Path.home() / ".local/share/murmurmark/models/whisper.cpp/ggml-large-v3-q5_0.bin"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Materialize exact live ASR chunks as authoritative whisper.cpp cache.")
    parser.add_argument("session", type=Path)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--language", default="ru")
    parser.add_argument("--prompt-file", type=Path)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--max-context", type=int, default=0)
    parser.add_argument("--duration-ms", type=int, default=0)
    parser.add_argument("--asr-mode", choices=("windowed", "whole"), default="windowed")
    parser.add_argument("--asr-window-sec", type=int, default=60)
    parser.add_argument("--asr-overlap-sec", type=int, default=5)
    parser.add_argument("--mic-audio-prep", default="speech")
    parser.add_argument("--remote-audio-prep", default="loudnorm")
    parser.add_argument("--whisper-cli", default="whisper-cli")
    parser.add_argument("--force", action="store_true", help="Replace an existing raw cache only after exact gates pass.")
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any] | None:
    return authoritative_cache.read_json(path)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
    return rows


def rel(path: Path, session: Path) -> str:
    try:
        return str(path.relative_to(session))
    except ValueError:
        return str(path)


def read_prompt(path: Path | None) -> str | None:
    if path is None:
        return None
    text = path.expanduser().read_text(encoding="utf-8").strip()
    return text or None


def raw_meta_path(output_base: Path) -> Path:
    return output_base.with_suffix(".meta.json")


def audio_fingerprint(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {"path": str(path), "size": stat.st_size, "sha256": authoritative_cache.sha256_file(path)}


def timestamp_from_ms(ms: int, *, vtt: bool = False) -> str:
    value = max(0, ms)
    millis = value % 1000
    total_sec = value // 1000
    separator = "." if vtt else ","
    return f"{total_sec // 3600:02d}:{(total_sec // 60) % 60:02d}:{total_sec % 60:02d}{separator}{millis:03d}"


def shift_offsets(value: dict[str, Any], delta_ms: int) -> None:
    offsets = value.get("offsets")
    if not isinstance(offsets, dict):
        return
    start = int(offsets.get("from") or 0) + delta_ms
    end = int(offsets.get("to") or start) + delta_ms
    offsets["from"] = start
    offsets["to"] = end
    value["timestamps"] = {"from": timestamp_from_ms(start), "to": timestamp_from_ms(end)}


def shifted_row(row: dict[str, Any], delta_ms: int) -> dict[str, Any]:
    result = copy.deepcopy(row)
    shift_offsets(result, delta_ms)
    for token in result.get("tokens") or []:
        if isinstance(token, dict):
            shift_offsets(token, delta_ms)
    return result


def write_text_sidecars(output_base: Path, rows: list[dict[str, Any]]) -> None:
    txt = "".join(f"{str(row.get('text') or '').strip()}\n" for row in rows if str(row.get("text") or "").strip())
    lines = ["WEBVTT", ""]
    for row in rows:
        text = str(row.get("text") or "").strip()
        offsets = row.get("offsets") if isinstance(row.get("offsets"), dict) else {}
        start = int(offsets.get("from") or 0)
        end = int(offsets.get("to") or start)
        if text and end > start:
            lines.extend([f"{timestamp_from_ms(start, vtt=True)} --> {timestamp_from_ms(end, vtt=True)}", text, ""])
    authoritative_cache.atomic_write_bytes(output_base.with_suffix(".txt"), txt.encode("utf-8"))
    authoritative_cache.atomic_write_bytes(output_base.with_suffix(".vtt"), ("\n".join(lines) + "\n").encode("utf-8"))


def source_json_path(session: Path, source_record: dict[str, Any]) -> Path | None:
    asr = source_record.get("asr")
    if not isinstance(asr, dict) or asr.get("status") != "passed":
        return None
    raw = asr.get("json")
    if not isinstance(raw, str) or not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = session / path
    return path if path.exists() else None


def source_wav_path(session: Path, source_record: dict[str, Any]) -> Path | None:
    raw = source_record.get("asr_wav") or source_record.get("wav")
    if not isinstance(raw, str) or not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = session / path
    return path if path.exists() else None


def prepare_audio(input_wav: Path, output_wav: Path, mode: str) -> Path:
    if mode == "none":
        return input_wav
    filters = {
        "speech": "highpass=f=100,lowpass=f=7600,alimiter=limit=0.98",
        "loudnorm": "highpass=f=80,lowpass=f=7800,loudnorm=I=-20:LRA=9:TP=-2,alimiter=limit=0.98",
    }
    if mode not in filters:
        raise ValueError(f"unsupported audio prep: {mode}")
    output_wav.parent.mkdir(parents=True, exist_ok=True)
    if output_wav.exists() and output_wav.stat().st_mtime >= input_wav.stat().st_mtime:
        return output_wav
    subprocess.run(
        [
            shutil.which("ffmpeg") or "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(input_wav),
            "-af",
            filters[mode],
            "-ar",
            "16000",
            "-ac",
            "1",
            str(output_wav),
        ],
        check=True,
        stdin=subprocess.DEVNULL,
    )
    return output_wav


def build_specs(path: Path, *, duration_ms: int, window_sec: int, overlap_sec: int) -> list[dict[str, int]]:
    audio = authoritative_cache.wave_format(path)
    rate = int(audio["sample_rate"])
    available = int(audio["frames"])
    total = min(available, int(round(duration_ms * rate / 1000))) if duration_ms > 0 else available
    window = window_sec * rate
    overlap = overlap_sec * rate
    rows: list[dict[str, int]] = []
    start = 0
    index = 1
    while start < total:
        end = min(total, start + window)
        seek = max(0, start - overlap)
        clip_end = min(available, end + overlap)
        rows.append(
            {
                "index": index,
                "hard_start_sample": start,
                "hard_end_sample": end,
                "seek_sample": seek,
                "clip_end_sample": clip_end,
                "hard_start_ms": int(round(start * 1000 / rate)),
                "hard_end_ms": int(round(end * 1000 / rate)),
                "seek_ms": int(round(seek * 1000 / rate)),
                "clip_end_ms": int(round(clip_end * 1000 / rate)),
                "clip_duration_ms": int(round((clip_end - seek) * 1000 / rate)),
                "sample_rate": rate,
            }
        )
        start += window
        index += 1
    return rows


def all_v2_proofs_present(chunks: list[dict[str, Any]], track: str) -> bool:
    if not chunks:
        return False
    for chunk in chunks:
        source = chunk.get(track)
        proof = source.get("batch_cache_compatibility") if isinstance(source, dict) else None
        if not isinstance(proof, dict) or proof.get("schema") != LIVE_PROOF_SCHEMA:
            return False
    return True


def verify_track(
    *,
    session: Path,
    chunks: list[dict[str, Any]],
    track: str,
    role: str,
    prep: str,
    prepared_audio: Path,
    model: Path,
    whisper_cli: Path,
    language: str,
    threads: int,
    max_context: int,
    prompt: str | None,
    duration_ms: int,
    window_sec: int,
    overlap_sec: int,
    staging: Path,
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    specs = build_specs(prepared_audio, duration_ms=duration_ms, window_sec=window_sec, overlap_sec=overlap_sec)
    sorted_chunks = sorted(chunks, key=lambda row: int(row.get("index") or 0))
    reasons: list[str] = []
    verified: list[dict[str, Any]] = []
    if len(sorted_chunks) != len(specs):
        reasons.append(f"chunk_count_mismatch:{len(sorted_chunks)}!={len(specs)}")
        return verified, reasons, {"expected_chunks": len(specs), "live_chunks": len(sorted_chunks)}
    decode = authoritative_cache.decode_contract(
        language=language,
        threads=threads,
        max_context=max_context,
        prompt=prompt,
        duration_ms=0,
    )
    for chunk, spec in zip(sorted_chunks, specs, strict=True):
        index = spec["index"]
        source = chunk.get(track)
        if not isinstance(source, dict):
            reasons.append(f"source_record_missing:{index}")
            continue
        proof = source.get("batch_cache_compatibility")
        if not isinstance(proof, dict) or proof.get("schema") != LIVE_PROOF_SCHEMA:
            reasons.append(f"authoritative_proof_missing:{index}")
            continue
        live_wav = source_wav_path(session, source)
        live_json = source_json_path(session, source)
        if live_wav is None:
            reasons.append(f"live_pcm_missing:{index}")
            continue
        if live_json is None:
            reasons.append(f"live_json_missing:{index}")
            continue
        canonical_wav = staging / track / f"{index:04d}_{spec['hard_start_ms'] // 1000:06d}s.wav"
        authoritative_cache.slice_pcm_wav(
            prepared_audio,
            canonical_wav,
            spec["seek_sample"],
            spec["clip_end_sample"],
        )
        identity = authoritative_cache.build_chunk_identity(
            track=track,
            role=role,
            spec=spec,
            chunk_wav=canonical_wav,
            model=model,
            whisper_cli=whisper_cli,
            decode=decode,
            audio_prep=prep,
        )
        identity_sha = authoritative_cache.content_sha256(identity)
        if proof.get("identity_sha256") != identity_sha or proof.get("identity") != identity:
            reasons.append(f"canonical_identity_mismatch:{index}")
            continue
        if authoritative_cache.pcm_fingerprint(live_wav) != identity["pcm"]:
            reasons.append(f"live_pcm_mismatch:{index}")
            continue
        output_proof = proof.get("output_json")
        if not isinstance(output_proof, dict) or output_proof != authoritative_cache.output_fingerprint(live_json):
            reasons.append(f"live_json_hash_mismatch:{index}")
            continue
        payload = read_json(live_json)
        if payload is None or not isinstance(payload.get("transcription"), list):
            reasons.append(f"live_json_invalid:{index}")
            continue
        verified.append(
            {
                "spec": spec,
                "identity": identity,
                "canonical_wav": canonical_wav,
                "live_wav": live_wav,
                "live_json": live_json,
                "payload": payload,
                "proof": proof,
            }
        )
    return verified, sorted(set(reasons)), {
        "expected_chunks": len(specs),
        "verified_chunks": len(verified),
        "prepared_audio": audio_fingerprint(prepared_audio),
    }


def materialize_track(
    *,
    session: Path,
    raw_dir: Path,
    track: str,
    verified: list[dict[str, Any]],
    raw_config: dict[str, Any],
    prepared_audio: Path,
    window_sec: int,
    overlap_sec: int,
) -> dict[str, Any]:
    chunk_dir = raw_dir / "chunks" / track
    chunk_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    shifted_rows: list[dict[str, Any]] = []
    template: dict[str, Any] = {"params": {}, "transcription": []}
    for item in verified:
        spec = item["spec"]
        index = int(spec["index"])
        chunk_base = chunk_dir / f"{index:04d}_{spec['hard_start_ms'] // 1000:06d}s"
        authoritative_cache.atomic_write_bytes(
            chunk_base.with_suffix(".wav"),
            item["canonical_wav"].read_bytes(),
        )
        authoritative_cache.atomic_write_json(chunk_base.with_suffix(".json"), item["payload"])
        write_text_sidecars(chunk_base, item["payload"]["transcription"])
        entry = authoritative_cache.build_chunk_cache_entry(
            identity=item["identity"],
            json_path=chunk_base.with_suffix(".json"),
            origin="live_origin",
            created_at=datetime.now(timezone.utc).isoformat(),
            execution={"mode": "completed_during_capture"},
            source={
                "live_wav": rel(item["live_wav"], session),
                "live_json": rel(item["live_json"], session),
                "live_proof_sha256": authoritative_cache.content_sha256(item["proof"]),
            },
        )
        authoritative_cache.atomic_write_json(raw_meta_path(chunk_base), entry)
        for row in item["payload"]["transcription"]:
            if not isinstance(row, dict):
                continue
            offsets = row.get("offsets") if isinstance(row.get("offsets"), dict) else {}
            local_start = int(offsets.get("from") or 0)
            local_end = int(offsets.get("to") or local_start)
            global_start = spec["seek_ms"] + local_start
            global_end = spec["seek_ms"] + local_end
            center = (global_start + global_end) / 2.0
            if spec["hard_start_ms"] <= center < spec["hard_end_ms"]:
                shifted_rows.append(shifted_row(row, spec["seek_ms"]))
        template = copy.deepcopy(item["payload"])
        records.append(
            {
                "index": index,
                "status": "reused",
                "origin": "live_origin",
                "reuse_origin": "live_origin",
                "hard_start_ms": spec["hard_start_ms"],
                "hard_end_ms": spec["hard_end_ms"],
                "seek_ms": spec["seek_ms"],
                "clip_duration_ms": spec["clip_duration_ms"],
                "hard_start_sample": spec["hard_start_sample"],
                "hard_end_sample": spec["hard_end_sample"],
                "seek_sample": spec["seek_sample"],
                "clip_end_sample": spec["clip_end_sample"],
                "wav": str(chunk_base.with_suffix(".wav")),
                "json": str(chunk_base.with_suffix(".json")),
                "meta": str(raw_meta_path(chunk_base)),
                "identity_sha256": authoritative_cache.content_sha256(item["identity"]),
                "cache_validation": "post_stop_canonical_identity_match",
            }
        )
    shifted_rows.sort(
        key=lambda row: (
            int((row.get("offsets") or {}).get("from") or 0),
            int((row.get("offsets") or {}).get("to") or 0),
        )
    )
    template["transcription"] = shifted_rows
    template.setdefault("params", {})
    if isinstance(template["params"], dict):
        template["params"]["murmurmark_asr_mode"] = "windowed"
        template["params"]["murmurmark_window_sec"] = window_sec
        template["params"]["murmurmark_overlap_sec"] = overlap_sec
        template["params"]["murmurmark_source_audio"] = str(prepared_audio)
    output_base = raw_dir / track
    authoritative_cache.atomic_write_json(output_base.with_suffix(".json"), template)
    write_text_sidecars(output_base, shifted_rows)
    raw_entry = authoritative_cache.build_raw_cache_entry(
        config=raw_config,
        json_path=output_base.with_suffix(".json"),
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    authoritative_cache.atomic_write_json(raw_meta_path(output_base), raw_entry)
    total_ms = sum(max(0, row["hard_end_ms"] - row["hard_start_ms"]) for row in records)
    report = {
        "schema": "murmurmark.whisper_cpp_chunk_cache_report/v2",
        "generator": {"name": "materialize-live-asr-cache", "version": SCRIPT_VERSION},
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "status": "completed",
        "track": track,
        "output_json": str(output_base.with_suffix(".json")),
        "source_audio": raw_config["source_audio"],
        "source_duration_ms": total_ms,
        "limited_duration_ms": total_ms,
        "window_sec": window_sec,
        "overlap_sec": overlap_sec,
        "chunks_total": len(records),
        "chunks_completed": len(records),
        "chunks_missing": 0,
        "chunks_reused": len(records),
        "chunks_transcribed": 0,
        "chunks_reused_by_origin": {"live_origin": len(records)},
        "completed_hard_ms": total_ms,
        "completed_hard_sec": round(total_ms / 1000.0, 3),
        "total_sec": round(total_ms / 1000.0, 3),
        "remaining_sec": 0.0,
        "reused_sec": round(total_ms / 1000.0, 3),
        "reused_sec_by_origin": {"live_origin": round(total_ms / 1000.0, 3)},
        "transcribed_sec": 0.0,
        "completed_ratio": 1.0,
        "chunks": records,
    }
    report_path = chunk_dir / "chunk_cache_report.json"
    authoritative_cache.atomic_write_json(report_path, report)
    return {"rows": len(shifted_rows), "chunks": len(records), "report": rel(report_path, session)}


def main() -> int:
    args = parse_args()
    session = args.session.expanduser().resolve()
    model = args.model.expanduser().resolve()
    whisper_raw = shutil.which(args.whisper_cli) or args.whisper_cli
    whisper_cli = Path(whisper_raw).expanduser().resolve()
    prompt = read_prompt(args.prompt_file)
    report_path = session / "derived/live/live_asr_cache_report.json"
    live_report = read_json(session / "derived/live/live_pipeline_report.json")
    chunks = read_jsonl(session / "derived/live/chunks.jsonl")
    global_reasons: list[str] = []
    if live_report is None:
        global_reasons.append("live_report_missing")
    elif live_report.get("status") != "completed":
        global_reasons.append("live_pipeline_not_completed")
    if args.asr_mode != "windowed":
        global_reasons.append("only_windowed_mode_supported")
    if not model.exists():
        global_reasons.append("model_file_missing")
    if not whisper_cli.exists():
        global_reasons.append("whisper_cli_missing")
    if not chunks:
        global_reasons.append("live_chunks_missing")
    raw_dir = session / "derived/transcript-simple/whisper-cpp/raw"
    tracks = {
        "mic": {"role": "Me", "prep": args.mic_audio_prep},
        "remote": {"role": "Colleagues", "prep": args.remote_audio_prep},
    }
    compatibility: dict[str, dict[str, Any]] = {}
    materialized_tracks: list[str] = []
    outputs: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="murmurmark-authoritative-live-cache-") as temporary:
        staging = Path(temporary)
        for track, settings in tracks.items():
            reasons = list(global_reasons)
            if (raw_dir / f"{track}.json").exists() and not args.force:
                reasons.append("raw_cache_already_exists")
            if not all_v2_proofs_present(chunks, track):
                reasons.append("authoritative_live_chunk_proofs_missing")
            verified: list[dict[str, Any]] = []
            evidence: dict[str, Any] = {"verified_chunks": 0}
            prepared: Path | None = None
            raw_config: dict[str, Any] | None = None
            if not reasons:
                source = session / f"derived/asr/{track}.wav"
                if not source.exists():
                    reasons.append("canonical_asr_source_missing")
                else:
                    prepared = prepare_audio(
                        source,
                        session
                        / f"derived/transcript-simple/whisper-cpp/prepared-audio/{track}_{settings['prep']}.wav",
                        str(settings["prep"]),
                    )
                    verified, track_reasons, evidence = verify_track(
                        session=session,
                        chunks=chunks,
                        track=track,
                        role=str(settings["role"]),
                        prep=str(settings["prep"]),
                        prepared_audio=prepared,
                        model=model,
                        whisper_cli=whisper_cli,
                        language=args.language,
                        threads=args.threads,
                        max_context=args.max_context,
                        prompt=prompt,
                        duration_ms=args.duration_ms,
                        window_sec=args.asr_window_sec,
                        overlap_sec=args.asr_overlap_sec,
                        staging=staging,
                    )
                    reasons.extend(track_reasons)
                    raw_config = authoritative_cache.build_raw_cache_config(
                        whisper_cli=whisper_cli,
                        model=model,
                        language=args.language,
                        threads=args.threads,
                        max_context=args.max_context,
                        prompt=prompt,
                        duration_ms=args.duration_ms,
                        asr_mode=args.asr_mode,
                        asr_window_sec=args.asr_window_sec,
                        asr_overlap_sec=args.asr_overlap_sec,
                        audio_prep=str(settings["prep"]),
                        source_audio=audio_fingerprint(prepared),
                    )
            reasons = sorted(set(reasons))
            eligible = not reasons and prepared is not None and raw_config is not None
            compatibility[track] = {
                "eligible": eligible,
                "decision": "reuse" if eligible else "batch_fallback",
                "reasons": reasons,
                "evidence": evidence,
            }
            if eligible:
                assert prepared is not None and raw_config is not None
                outputs[track] = materialize_track(
                    session=session,
                    raw_dir=raw_dir,
                    track=track,
                    verified=verified,
                    raw_config=raw_config,
                    prepared_audio=prepared,
                    window_sec=args.asr_window_sec,
                    overlap_sec=args.asr_overlap_sec,
                )
                materialized_tracks.append(track)
    fallback_tracks = [track for track in tracks if track not in materialized_tracks]
    if len(materialized_tracks) == len(tracks):
        status = "materialized"
    elif materialized_tracks:
        status = "partially_materialized"
    else:
        status = "not_eligible"
    reasons = sorted(
        f"{track}:{reason}"
        for track, decision in compatibility.items()
        for reason in decision["reasons"]
    )
    payload = {
        "schema": SCHEMA,
        "generator": {"name": "materialize-live-asr-cache", "version": SCRIPT_VERSION},
        "created_at": datetime.now(timezone.utc).isoformat(),
        "session": str(session),
        "status": status,
        "materialized": bool(materialized_tracks),
        "materialized_tracks": materialized_tracks,
        "fallback_tracks": fallback_tracks,
        "reasons": reasons,
        "track_compatibility": compatibility,
        "parameters": {
            "model": str(model),
            "model_sha256": authoritative_cache.sha256_file(model) if model.exists() else None,
            "whisper_cli": str(whisper_cli),
            "whisper_cli_sha256": authoritative_cache.sha256_file(whisper_cli) if whisper_cli.exists() else None,
            "language": args.language,
            "threads": args.threads,
            "max_context": args.max_context,
            "duration_ms": args.duration_ms,
            "asr_mode": args.asr_mode,
            "asr_window_sec": args.asr_window_sec,
            "asr_overlap_sec": args.asr_overlap_sec,
            "mic_audio_prep": args.mic_audio_prep,
            "remote_audio_prep": args.remote_audio_prep,
        },
        "inputs": {
            "live_report": "derived/live/live_pipeline_report.json" if live_report else None,
            "chunks_jsonl": "derived/live/chunks.jsonl" if chunks else None,
        },
        "outputs": outputs,
        "notes": [
            "Live text is never accepted by similarity.",
            "Each reused chunk must match post-stop canonical PCM and the complete authoritative identity.",
            "A not_eligible result is fail-open batch fallback.",
        ],
    }
    authoritative_cache.atomic_write_json(report_path, payload)
    print(f"live_asr_cache_report: {report_path}")
    print(f"status: {status}")
    if reasons:
        print("reasons: " + ", ".join(reasons))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
