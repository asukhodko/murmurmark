#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import authoritative_asr_cache as authoritative_cache


SCHEMA = "murmurmark.whisper_cpp_chunk_rebuild_check/v1"
SCRIPT_VERSION = "0.2.0"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify that whisper.cpp raw JSON can be rebuilt from cached ASR chunks.")
    parser.add_argument("session", type=Path)
    parser.add_argument(
        "--out",
        type=Path,
        help="Default: SESSION/derived/transcript-simple/whisper-cpp/raw/chunk_rebuild_check.json",
    )
    parser.add_argument("--require-chunks", action="store_true", help="Fail when chunk reports are missing.")
    parser.add_argument(
        "--require-authoritative",
        action="store_true",
        help="Require v2 exact identity/integrity metadata and byte-identical raw JSON replay.",
    )
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def rel(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def timestamp_from_ms(ms: int) -> str:
    total_ms = max(0, ms)
    millis = total_ms % 1000
    total_sec = total_ms // 1000
    seconds = total_sec % 60
    minutes = (total_sec // 60) % 60
    hours = total_sec // 3600
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def shift_offset_node(node: dict[str, Any], shift_ms: int) -> None:
    offsets = node.get("offsets")
    if isinstance(offsets, dict):
        start = int(offsets.get("from") or 0) + shift_ms
        end = int(offsets.get("to") or 0) + shift_ms
        offsets["from"] = max(0, start)
        offsets["to"] = max(0, end)
        node["timestamps"] = {
            "from": timestamp_from_ms(offsets["from"]),
            "to": timestamp_from_ms(offsets["to"]),
        }


def shifted_row(row: dict[str, Any], shift_ms: int) -> dict[str, Any]:
    adjusted = copy.deepcopy(row)
    shift_offset_node(adjusted, shift_ms)
    for token in adjusted.get("tokens") or []:
        if isinstance(token, dict):
            shift_offset_node(token, shift_ms)
    return adjusted


def row_signature(row: dict[str, Any]) -> dict[str, Any]:
    offsets = row.get("offsets") if isinstance(row.get("offsets"), dict) else {}
    tokens: list[dict[str, Any]] = []
    for token in row.get("tokens") or []:
        if not isinstance(token, dict):
            continue
        token_offsets = token.get("offsets") if isinstance(token.get("offsets"), dict) else {}
        tokens.append(
            {
                "text": str(token.get("text") or ""),
                "from": int(token_offsets.get("from") or 0),
                "to": int(token_offsets.get("to") or 0),
            }
        )
    return {
        "text": str(row.get("text") or ""),
        "from": int(offsets.get("from") or 0),
        "to": int(offsets.get("to") or 0),
        "tokens": tokens,
    }


def signatures(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row_signature(row) for row in rows if isinstance(row, dict)]


def identity_contract_errors(
    *,
    track: str,
    chunk: dict[str, Any],
    identity: dict[str, Any],
    raw_config: dict[str, Any] | None,
) -> list[str]:
    errors: list[str] = []
    expected_role = "Me" if track == "mic" else "Colleagues"
    if identity.get("schema") != authoritative_cache.IDENTITY_SCHEMA:
        errors.append("identity_schema_mismatch")
    if identity.get("track") != track:
        errors.append("identity_track_mismatch")
    if identity.get("role") != expected_role:
        errors.append("identity_role_mismatch")
    window = identity.get("window") if isinstance(identity.get("window"), dict) else {}
    expected_window = {
        "index": int(chunk.get("index") or 0),
        "hard_start_sample": int(chunk.get("hard_start_sample") or 0),
        "hard_end_sample": int(chunk.get("hard_end_sample") or 0),
        "clip_start_sample": int(chunk.get("seek_sample") or 0),
        "clip_end_sample": int(chunk.get("clip_end_sample") or 0),
    }
    for key, expected in expected_window.items():
        if int(window.get(key) or 0) != expected:
            errors.append(f"identity_window_{key}_mismatch")
    if int(window.get("overlap_before_sample") or 0) != (
        expected_window["hard_start_sample"] - expected_window["clip_start_sample"]
    ):
        errors.append("identity_overlap_before_mismatch")
    if int(window.get("overlap_after_sample") or 0) != (
        expected_window["clip_end_sample"] - expected_window["hard_end_sample"]
    ):
        errors.append("identity_overlap_after_mismatch")
    if window.get("reconciliation") != authoritative_cache.RECONCILIATION_POLICY:
        errors.append("identity_reconciliation_mismatch")
    pcm = identity.get("pcm") if isinstance(identity.get("pcm"), dict) else {}
    if int(window.get("sample_rate") or 0) != int(pcm.get("sample_rate") or 0):
        errors.append("identity_sample_rate_mismatch")
    if int(pcm.get("frames") or 0) != (
        expected_window["clip_end_sample"] - expected_window["clip_start_sample"]
    ):
        errors.append("identity_pcm_frame_count_mismatch")
    if raw_config is not None:
        if identity.get("audio_prep") != raw_config.get("audio_prep"):
            errors.append("identity_audio_prep_mismatch")
        if identity.get("engine") != {
            "name": "whisper.cpp",
            "binary": (raw_config.get("engine_identity") or {}).get("binary"),
            "model": (raw_config.get("engine_identity") or {}).get("model"),
        }:
            errors.append("identity_engine_mismatch")
        expected_decode = copy.deepcopy(raw_config.get("decode_contract") or {})
        expected_decode["duration_ms"] = 0
        if identity.get("decode") != expected_decode:
            errors.append("identity_decode_mismatch")
    return errors


def rebuild_rows_from_chunks(
    session: Path,
    report: dict[str, Any],
    track: str,
    raw_config: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], list[str], list[str], dict[str, Any] | None]:
    rows: list[dict[str, Any]] = []
    used: list[str] = []
    errors: list[str] = []
    template: dict[str, Any] | None = None
    chunks = report.get("chunks") if isinstance(report.get("chunks"), list) else []
    for chunk in sorted(chunks, key=lambda item: int(item.get("index") or 0) if isinstance(item, dict) else 0):
        if not isinstance(chunk, dict):
            continue
        json_value = chunk.get("json")
        if not isinstance(json_value, str) or not json_value:
            continue
        json_path = Path(json_value)
        if not json_path.is_absolute():
            json_path = session / json_path
        data = read_json(json_path)
        if data is None:
            errors.append(f"chunk_json_missing_or_invalid:{chunk.get('index')}")
            continue
        template = data
        if report.get("schema") == "murmurmark.whisper_cpp_chunk_cache_report/v2":
            chunk_base = json_path.with_suffix("")
            meta = authoritative_cache.read_json(chunk_base.with_suffix(".meta.json"))
            identity = meta.get("identity") if isinstance(meta, dict) else None
            if not isinstance(identity, dict):
                errors.append(f"chunk_identity_missing:{chunk.get('index')}")
            else:
                errors.extend(
                    f"chunk_contract_failed:{chunk.get('index')}:{reason}"
                    for reason in identity_contract_errors(
                        track=track,
                        chunk=chunk,
                        identity=identity,
                        raw_config=raw_config,
                    )
                )
                valid, reason, _ = authoritative_cache.validate_chunk_cache(chunk_base, identity)
                if not valid:
                    errors.append(f"chunk_integrity_failed:{chunk.get('index')}:{reason}")
        hard_start = int(chunk.get("hard_start_ms") or 0)
        hard_end = int(chunk.get("hard_end_ms") or hard_start)
        seek_ms = int(chunk.get("seek_ms") or 0)
        for row in data.get("transcription") or []:
            if not isinstance(row, dict):
                continue
            offsets = row.get("offsets") if isinstance(row.get("offsets"), dict) else {}
            local_start = int(offsets.get("from") or 0)
            local_end = int(offsets.get("to") or local_start)
            global_start = seek_ms + local_start
            global_end = seek_ms + local_end
            center = (global_start + global_end) / 2.0
            if hard_start <= center < hard_end:
                rows.append(shifted_row(row, seek_ms))
        used.append(rel(json_path, session))
    return (
        sorted(
            rows,
            key=lambda row: (
                int((row.get("offsets") or {}).get("from") or 0),
                int((row.get("offsets") or {}).get("to") or 0),
            ),
        ),
        used,
        errors,
        template,
    )


def rebuilt_raw_bytes(
    *,
    template: dict[str, Any] | None,
    rows: list[dict[str, Any]],
    raw: dict[str, Any],
    report: dict[str, Any],
) -> bytes | None:
    if template is None:
        return None
    combined = copy.deepcopy(template)
    combined["transcription"] = rows
    combined.setdefault("params", {})
    raw_params = raw.get("params") if isinstance(raw.get("params"), dict) else {}
    if isinstance(combined["params"], dict):
        combined["params"]["murmurmark_asr_mode"] = "windowed"
        combined["params"]["murmurmark_window_sec"] = int(report.get("window_sec") or 0)
        combined["params"]["murmurmark_overlap_sec"] = int(report.get("overlap_sec") or 0)
        combined["params"]["murmurmark_source_audio"] = raw_params.get("murmurmark_source_audio")
    return (json.dumps(combined, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def compare_track(
    session: Path,
    raw_dir: Path,
    track: str,
    require_chunks: bool,
    require_authoritative: bool,
) -> dict[str, Any]:
    raw_path = raw_dir / f"{track}.json"
    report_path = raw_dir / "chunks" / track / "chunk_cache_report.json"
    raw = read_json(raw_path)
    report = read_json(report_path)
    if raw is None:
        return {
            "track": track,
            "status": "fail",
            "reason": "raw_json_missing_or_invalid",
            "raw_json": rel(raw_path, session),
            "chunk_report": rel(report_path, session),
        }
    if report is None:
        return {
            "track": track,
            "status": "fail" if require_chunks else "not_applicable",
            "reason": "chunk_report_missing",
            "raw_json": rel(raw_path, session),
            "chunk_report": rel(report_path, session),
            "raw_rows": len(raw.get("transcription") or []),
        }
    raw_meta = authoritative_cache.read_json(raw_meta_path := raw_path.with_suffix(".meta.json"))
    raw_config = raw_meta.get("config") if isinstance(raw_meta, dict) and isinstance(raw_meta.get("config"), dict) else None
    raw_meta_valid = False
    raw_meta_reason = "raw_cache_config_missing"
    if raw_config is not None:
        raw_meta_valid, raw_meta_reason = authoritative_cache.validate_raw_cache(raw_path.with_suffix(""), raw_config)
    rebuilt, used, integrity_errors, template = rebuild_rows_from_chunks(
        session,
        report,
        track,
        raw_config,
    )
    if report.get("schema") == "murmurmark.whisper_cpp_chunk_cache_report/v2" and not raw_meta_valid:
        integrity_errors.append(f"raw_cache_integrity_failed:{raw_meta_reason}")
    raw_rows = raw.get("transcription") if isinstance(raw.get("transcription"), list) else []
    raw_sig = signatures(raw_rows)
    rebuilt_sig = signatures(rebuilt)
    mismatches: list[dict[str, Any]] = []
    max_len = max(len(raw_sig), len(rebuilt_sig))
    for index in range(max_len):
        expected = raw_sig[index] if index < len(raw_sig) else None
        actual = rebuilt_sig[index] if index < len(rebuilt_sig) else None
        if expected != actual:
            mismatches.append({"index": index, "raw": expected, "rebuilt": actual})
        if len(mismatches) >= 5:
            break
    replay_bytes = rebuilt_raw_bytes(template=template, rows=rebuilt, raw=raw, report=report)
    raw_bytes = raw_path.read_bytes()
    byte_identical = replay_bytes == raw_bytes if replay_bytes is not None else False
    authoritative_schema = report.get("schema") == "murmurmark.whisper_cpp_chunk_cache_report/v2"
    authoritative_pass = authoritative_schema and not integrity_errors and byte_identical
    semantic_pass = not mismatches and len(raw_sig) == len(rebuilt_sig)
    status = "pass" if semantic_pass and (authoritative_pass or not require_authoritative) else "fail"
    if not semantic_pass:
        reason = "rebuilt_rows_differ"
    elif require_authoritative and not authoritative_schema:
        reason = "authoritative_chunk_schema_required"
    elif integrity_errors:
        reason = "chunk_integrity_failed"
    elif not byte_identical:
        reason = "raw_json_not_byte_identical"
    else:
        reason = "matches"
    return {
        "track": track,
        "status": status,
        "reason": reason,
        "raw_json": rel(raw_path, session),
        "chunk_report": rel(report_path, session),
        "raw_cache_meta": rel(raw_meta_path, session),
        "raw_rows": len(raw_sig),
        "rebuilt_rows": len(rebuilt_sig),
        "chunks_total": int(report.get("chunks_total") or 0),
        "chunks_completed": int(report.get("chunks_completed") or 0),
        "chunks_reused": int(report.get("chunks_reused") or 0),
        "chunks_transcribed": int(report.get("chunks_transcribed") or 0),
        "completed_hard_sec": float(report.get("completed_hard_sec") or 0.0),
        "total_sec": float(report.get("total_sec") or 0.0),
        "used_chunk_json": used,
        "mismatches": mismatches,
        "authoritative_schema": authoritative_schema,
        "integrity_errors": integrity_errors,
        "byte_identical": byte_identical,
        "raw_json_sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "rebuilt_json_sha256": hashlib.sha256(replay_bytes).hexdigest() if replay_bytes is not None else None,
    }


def main() -> int:
    args = parse_args()
    session = args.session.expanduser().resolve()
    raw_dir = session / "derived/transcript-simple/whisper-cpp/raw"
    out = args.out.expanduser() if args.out else raw_dir / "chunk_rebuild_check.json"
    tracks = [
        compare_track(session, raw_dir, track, args.require_chunks, args.require_authoritative)
        for track in ("mic", "remote")
    ]
    hard_fail = any(track.get("status") == "fail" for track in tracks)
    has_pass = any(track.get("status") == "pass" for track in tracks)
    if hard_fail:
        status = "failed"
    elif has_pass:
        status = "passed"
    else:
        status = "not_applicable"
    payload = {
        "schema": SCHEMA,
        "generator": {"name": "check-asr-chunk-cache", "version": SCRIPT_VERSION},
        "created_at": datetime.now(timezone.utc).isoformat(),
        "session": str(session),
        "status": status,
        "require_authoritative": args.require_authoritative,
        "tracks": tracks,
    }
    write_json(out, payload)
    print(f"chunk_rebuild_check: {out}")
    print(f"status: {status}")
    for track in tracks:
        print(
            f"{track['track']}: {track['status']} "
            f"raw_rows={track.get('raw_rows', 0)} rebuilt_rows={track.get('rebuilt_rows', 0)} "
            f"chunks={track.get('chunks_completed', 0)}/{track.get('chunks_total', 0)}"
        )
    return 2 if hard_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
