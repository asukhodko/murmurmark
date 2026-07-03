#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = "murmurmark.asr_chunk_cache_corpus_report/v1"
SCRIPT_VERSION = "0.3.0"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aggregate ASR chunk cache rebuild checks across sessions.")
    parser.add_argument("sessions", nargs="*", type=Path, help="Default: all sessions under --sessions-root with session.json.")
    parser.add_argument("--sessions-root", type=Path, default=Path("sessions"))
    parser.add_argument("--out-dir", type=Path, default=Path("sessions/_reports/asr-chunk-cache"))
    parser.add_argument("--refresh", action="store_true", help="Run check-asr-chunk-cache.py for each session before reporting.")
    parser.add_argument("--require-chunks", action="store_true", help="Treat missing chunk reports as failures during refresh/check.")
    parser.add_argument("--no-fail", action="store_true", help="Always exit 0 after writing the report.")
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


def discover_sessions(root: Path) -> list[Path]:
    if not root.exists():
        return []
    result: list[Path] = []
    for path in sorted(root.iterdir()):
        if path.is_dir() and (path / "session.json").exists():
            result.append(path)
    return result


def check_path(session: Path) -> Path:
    return session / "derived/transcript-simple/whisper-cpp/raw/chunk_rebuild_check.json"


def has_raw_asr(session: Path) -> bool:
    raw_dir = session / "derived/transcript-simple/whisper-cpp/raw"
    return (raw_dir / "mic.json").exists() and (raw_dir / "remote.json").exists()


def run_refresh(session: Path, require_chunks: bool) -> dict[str, Any]:
    script = Path(__file__).resolve().parent / "check-asr-chunk-cache.py"
    command = [sys.executable, str(script), str(session)]
    if require_chunks:
        command.append("--require-chunks")
    result = subprocess.run(
        command,
        text=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return {
        "returncode": result.returncode,
        "stdout_tail": (result.stdout or "")[-2000:],
        "stderr_tail": (result.stderr or "")[-2000:],
    }


def session_row(session: Path, *, refresh: bool, require_chunks: bool) -> dict[str, Any]:
    if not has_raw_asr(session):
        return {
            "session": str(session),
            "status": "not_applicable",
            "reason": "raw_asr_missing",
            "report": rel(check_path(session), session),
            "chunks_total": 0,
            "chunks_completed": 0,
            "chunks_reused": 0,
            "chunks_transcribed": 0,
            "raw_rows": 0,
            "rebuilt_rows": 0,
            "tracks": [],
            "refresh": None,
        }
    refresh_result = run_refresh(session, require_chunks) if refresh else None
    report = read_json(check_path(session))
    if report is None:
        status = "missing"
        tracks: list[dict[str, Any]] = []
    else:
        status = str(report.get("status") or "unknown")
        tracks = [item for item in report.get("tracks") or [] if isinstance(item, dict)]
    reasons = sorted(
        {
            str(track.get("reason"))
            for track in tracks
            if isinstance(track.get("reason"), str) and str(track.get("reason"))
        }
    )
    reason = ",".join(reasons) if reasons else None
    chunks_total = sum(int(track.get("chunks_total") or 0) for track in tracks)
    chunks_completed = sum(int(track.get("chunks_completed") or 0) for track in tracks)
    chunks_reused = sum(int(track.get("chunks_reused") or 0) for track in tracks)
    chunks_transcribed = sum(int(track.get("chunks_transcribed") or 0) for track in tracks)
    raw_rows = sum(int(track.get("raw_rows") or 0) for track in tracks)
    rebuilt_rows = sum(int(track.get("rebuilt_rows") or 0) for track in tracks)
    return {
        "session": str(session),
        "status": status,
        "reason": reason,
        "report": rel(check_path(session), session),
        "chunks_total": chunks_total,
        "chunks_completed": chunks_completed,
        "chunks_reused": chunks_reused,
        "chunks_transcribed": chunks_transcribed,
        "raw_rows": raw_rows,
        "rebuilt_rows": rebuilt_rows,
        "tracks": tracks,
        "refresh": refresh_result,
    }


def markdown(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        "# ASR Chunk Cache Corpus Report",
        "",
        f"Status: `{payload['status']}`",
        f"Sessions: `{summary['sessions']}`",
        f"Passed: `{summary['passed']}`",
        f"Failed: `{summary['failed']}`",
        f"Not applicable: `{summary['not_applicable']}`",
        f"Missing: `{summary['missing']}`",
        f"Coverage: `{summary['coverage_ratio']}`",
        f"Raw ASR without chunks: `{summary['raw_asr_without_chunks']}`",
        f"Raw ASR missing: `{summary['raw_asr_missing']}`",
        f"Chunks completed: `{summary['chunks_completed']}/{summary['chunks_total']}`",
        f"Chunks reused: `{summary['chunks_reused']}`",
        f"Chunks transcribed: `{summary['chunks_transcribed']}`",
        "",
        "## Interpretation",
        "",
        "- `passed` means current raw ASR JSON can be rebuilt from chunk reports.",
        "- `failed` means chunk rebuild parity is broken and must block promotion.",
        "- `not_applicable` means the session has no raw ASR or no chunked ASR artifacts yet.",
        "- This report proves chunk-cache rebuild parity, not transcript quality by itself.",
        "",
        "## Sessions",
        "",
    ]
    for row in payload["sessions"]:
        lines.append(
            f"- `{Path(row['session']).name}`: `{row['status']}`, "
            f"reason `{row.get('reason') or '-'}`, "
            f"chunks `{row['chunks_completed']}/{row['chunks_total']}`, "
            f"rows `{row['rebuilt_rows']}/{row['raw_rows']}`"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    sessions = [path.expanduser() for path in args.sessions] if args.sessions else discover_sessions(args.sessions_root)
    rows = [
        session_row(session, refresh=args.refresh, require_chunks=args.require_chunks)
        for session in sessions
    ]
    failed = [row for row in rows if row["status"] == "failed" or (row.get("refresh") or {}).get("returncode") not in {None, 0}]
    missing = [row for row in rows if row["status"] == "missing"]
    not_applicable = [row for row in rows if row["status"] == "not_applicable"]
    passed = [row for row in rows if row["status"] == "passed"]
    raw_asr_missing = [row for row in rows if row.get("reason") == "raw_asr_missing"]
    raw_asr_without_chunks = [
        row
        for row in rows
        if row.get("status") == "not_applicable" and "chunk_report_missing" in str(row.get("reason") or "")
    ]
    if failed:
        status = "failed"
    elif passed and not missing:
        status = "passed"
    elif passed:
        status = "passed_with_warnings"
    else:
        status = "not_evaluated"
    summary = {
        "sessions": len(rows),
        "passed": len(passed),
        "failed": len(failed),
        "missing": len(missing),
        "not_applicable": len(not_applicable),
        "coverage_ratio": round(len(passed) / len(rows), 6) if rows else 0.0,
        "raw_asr_missing": len(raw_asr_missing),
        "raw_asr_without_chunks": len(raw_asr_without_chunks),
        "chunks_total": sum(int(row.get("chunks_total") or 0) for row in rows),
        "chunks_completed": sum(int(row.get("chunks_completed") or 0) for row in rows),
        "chunks_reused": sum(int(row.get("chunks_reused") or 0) for row in rows),
        "chunks_transcribed": sum(int(row.get("chunks_transcribed") or 0) for row in rows),
    }
    payload = {
        "schema": SCHEMA,
        "generator": {"name": "report-asr-chunk-cache-corpus", "version": SCRIPT_VERSION},
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "summary": summary,
        "sessions": rows,
    }
    out_json = args.out_dir / "asr_chunk_cache_corpus_report.json"
    out_md = args.out_dir / "asr_chunk_cache_corpus_report.md"
    write_json(out_json, payload)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(markdown(payload), encoding="utf-8")
    print(f"status: {status}")
    print(f"failed: {len(failed)}")
    print(f"passed: {len(passed)}")
    print(f"written: {out_json}")
    print(f"recommended_next: less {out_md}")
    return 0 if args.no_fail or status != "failed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
