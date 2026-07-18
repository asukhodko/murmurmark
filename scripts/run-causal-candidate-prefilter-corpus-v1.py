#!/usr/bin/env python3
"""Run the shared cheap prefilter over the immutable generalization corpus."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any


SCHEMA = "murmurmark.causal_candidate_prefilter_corpus_run/v1"
SCRIPT_VERSION = "1.0.0"
PROFILE = "causal-candidate-coverage-cheap-negative-prefilter-v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run causal candidate prefilter corpus v1.")
    parser.add_argument(
        "--corpus-dir",
        type=Path,
        default=Path("sessions/_reports/live-pipeline/causal-recovery-generalization-v1"),
    )
    parser.add_argument("--sessions-root", type=Path, default=Path("sessions"))
    parser.add_argument("--session", action="append", default=[])
    parser.add_argument(
        "--model",
        default=str(
            Path.home()
            / ".local/share/murmurmark/models/whisper.cpp/ggml-large-v3-q5_0.bin"
        ),
    )
    parser.add_argument("--language", default="ru")
    parser.add_argument("--whisper-cli", default=os.environ.get("WHISPER_CLI", "whisper-cli"))
    parser.add_argument(
        "--faster-whisper-model",
        type=Path,
        default=Path(
            os.environ.get(
                "MURMURMARK_FASTER_WHISPER_MODEL",
                str(Path.home() / ".local/share/murmurmark/models/faster-whisper/large-v3"),
            )
        ),
    )
    parser.add_argument("--decision-only", action="store_true")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--require-complete", action="store_true")
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    corpus_dir = args.corpus_dir.expanduser().resolve()
    sessions_root = args.sessions_root.expanduser().resolve()
    manifest = read_json(corpus_dir / "corpus_manifest_v1.json")
    rows = [
        row
        for row in read_jsonl(corpus_dir / "corpus_rows_v1.jsonl")
        if row.get("row_kind") == "eligible_remote_active_source"
    ]
    expected_count = int((manifest.get("summary") or {}).get("eligible_source_count") or 783)
    selected_sessions = set(args.session)
    if selected_sessions:
        rows = [row for row in rows if str(row.get("session")) in selected_sessions]
    by_session: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_session[str(row.get("session"))].append(row)
    script = Path(__file__).with_name("live-causal-double-talk-me-recovery.py")
    session_results: list[dict[str, Any]] = []
    for session_id, session_rows in sorted(by_session.items()):
        session = sessions_root / session_id
        output = session / "derived/live" / PROFILE / "offline"
        if args.refresh:
            shutil.rmtree(output, ignore_errors=True)
        output.mkdir(parents=True, exist_ok=True)
        source_path = output / "frozen_source_selection.jsonl"
        source_rows = [
            row.get("causal_selection")
            for row in sorted(
                session_rows,
                key=lambda item: (
                    int((item.get("causal_selection") or {}).get("chunk_index") or 0),
                    float((item.get("causal_selection") or {}).get("start") or 0.0),
                    str(item.get("id") or ""),
                ),
            )
            if isinstance(row.get("causal_selection"), dict)
        ]
        write_jsonl(source_path, source_rows)
        command = [
            sys.executable,
            str(script),
            str(session),
            "--source-selection",
            str(source_path),
            "--output-dir",
            str(output),
            "--runtime-shadow",
            "--cheap-prefilter-v1",
            "--view-profile",
            "runtime",
            "--micro-asr-backend",
            "faster-whisper",
            "--faster-whisper-model",
            str(args.faster_whisper_model.expanduser()),
            "--language",
            args.language,
            "--whisper-cli",
            args.whisper_cli,
        ]
        if args.decision_only:
            command.append("--cheap-prefilter-decision-only")
        completed = subprocess.run(
            command,
            cwd=repo_root,
            text=True,
            capture_output=True,
            check=False,
        )
        state = read_json(output / "state.json")
        decisions = read_jsonl(output / "cheap_prefilter_decisions.jsonl")
        result = {
            "session": session_id,
            "status": "passed" if completed.returncode == 0 else "failed",
            "returncode": completed.returncode,
            "eligible_source_count": len(source_rows),
            "decision_count": len(decisions),
            "routes": dict(Counter(str(row.get("route")) for row in decisions)),
            "candidate_count": int(state.get("candidate_count") or 0),
            "accepted_candidate_count": int(state.get("accepted_candidate_count") or 0),
            "decision_fingerprint_sha256": (
                (state.get("cheap_prefilter") or {}).get("decision_fingerprint_sha256")
            ),
            "output": str(output.relative_to(repo_root)),
            "stdout_tail": completed.stdout[-1000:],
            "stderr_tail": completed.stderr[-1000:],
        }
        result["checks"] = {
            "child_passed": completed.returncode == 0,
            "all_source_rows_decided": len(decisions) == len(source_rows),
            "all_routes_known": all(
                row.get("route") in {"cheap_reject", "expensive_candidate", "unresolved"}
                for row in decisions
            ),
            "batch_fields_unused": all(
                row.get("used_batch_fields_for_selection") is False for row in decisions
            ),
        }
        result["status"] = "passed" if all(result["checks"].values()) else "failed"
        session_results.append(result)
        print(
            f"{session_id}: {result['status']} "
            f"({len(decisions)}/{len(source_rows)} decisions, {result['routes']})"
        )
    total_rows = sum(int(row.get("eligible_source_count") or 0) for row in session_results)
    total_decisions = sum(int(row.get("decision_count") or 0) for row in session_results)
    complete_scope = not selected_sessions and total_rows == expected_count
    checks = {
        "sessions_present": bool(session_results),
        "all_sessions_passed": all(row.get("status") == "passed" for row in session_results),
        "selected_scope_complete": total_decisions == total_rows,
        "full_corpus_783_decisions": total_decisions == expected_count if complete_scope else None,
    }
    passed = all(value is not False for value in checks.values())
    payload = {
        "schema": SCHEMA,
        "generator": {
            "name": "run-causal-candidate-prefilter-corpus-v1",
            "version": SCRIPT_VERSION,
        },
        "created_at": datetime.now(UTC).isoformat(),
        "status": "passed" if passed else "failed",
        "profile": PROFILE,
        "mode": "decision_only" if args.decision_only else "expensive_stage",
        "immutable_corpus_fingerprint_sha256": manifest.get("corpus_fingerprint_sha256"),
        "expected_full_corpus_source_count": expected_count,
        "selected_source_count": total_rows,
        "decision_count": total_decisions,
        "checks": checks,
        "sessions": session_results,
        "batch_authoritative": True,
        "normal_preview_connected": False,
        "promotion_allowed": False,
    }
    report_dir = (
        repo_root
        / "sessions/_reports/live-pipeline/causal-candidate-coverage-cheap-negative-prefilter-v1"
    )
    write_json(report_dir / "corpus_run_state.json", payload)
    print(f"corpus run: {payload['status']}")
    print(f"decisions: {total_decisions}/{total_rows}")
    return 0 if passed or not args.require_complete else 1


if __name__ == "__main__":
    raise SystemExit(main())
