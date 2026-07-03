#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = "murmurmark.whisper_cpp_chunk_rebuild_check/v1"
SCRIPT_VERSION = "0.1.0"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify that whisper.cpp raw JSON can be rebuilt from cached ASR chunks.")
    parser.add_argument("session", type=Path)
    parser.add_argument(
        "--out",
        type=Path,
        help="Default: SESSION/derived/transcript-simple/whisper-cpp/raw/chunk_rebuild_check.json",
    )
    parser.add_argument("--require-chunks", action="store_true", help="Fail when chunk reports are missing.")
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


def rebuild_rows_from_chunks(session: Path, report: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    used: list[str] = []
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
            continue
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
    )


def compare_track(session: Path, raw_dir: Path, track: str, require_chunks: bool) -> dict[str, Any]:
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
    rebuilt, used = rebuild_rows_from_chunks(session, report)
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
    status = "pass" if not mismatches and len(raw_sig) == len(rebuilt_sig) else "fail"
    return {
        "track": track,
        "status": status,
        "reason": "matches" if status == "pass" else "rebuilt_rows_differ",
        "raw_json": rel(raw_path, session),
        "chunk_report": rel(report_path, session),
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
    }


def main() -> int:
    args = parse_args()
    session = args.session.expanduser().resolve()
    raw_dir = session / "derived/transcript-simple/whisper-cpp/raw"
    out = args.out.expanduser() if args.out else raw_dir / "chunk_rebuild_check.json"
    tracks = [compare_track(session, raw_dir, track, args.require_chunks) for track in ("mic", "remote")]
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
