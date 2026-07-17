#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
REPORTER = REPO_ROOT / "scripts/report-authoritative-handoff-corpus.py"


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_row(session: Path, *, mode: str, workers: int, elapsed: float, fingerprint: str) -> dict[str, Any]:
    return {
        "schema": "murmurmark.authoritative_handoff_run/v1",
        "session": str(session),
        "mode": mode,
        "status": "review_required",
        "elapsed_sec": elapsed,
        "selected_transcript_profile": "audit_cleanup_v2",
        "verdict": "usable_with_review",
        "transcript_fingerprint": {"path": "transcript.md", "size": 10, "sha256": fingerprint},
        "asr_provenance": {
            "tracks": {
                "mic": {"chunks_transcribed": 30 if workers == 2 else 0},
                "remote": {"chunks_transcribed": 30 if workers == 2 else 0},
            }
        },
        "runtime": {
            "asr_track_workers": workers,
            "micro_asr_workers": workers,
            "force_asr": workers == 2,
            "reuse_asr_cache": workers == 1,
        },
        "quality_metrics": {
            "review_burden_sec": 12.0,
            "needs_review_count": 2,
            "local_only_island_recall": 1.0,
            "remote_duplicate_in_me_seconds": 0.0,
        },
    }


def build_session(root: Path, index: int, elapsed: float) -> Path:
    session = root / f"session-{index}"
    write_json(session / "session.json", {"health": {"actual_duration_sec": 2400.0 + index}})
    fingerprint = f"fingerprint-{index}"
    rows = [
        run_row(session, mode="computed", workers=1, elapsed=120.0, fingerprint=fingerprint),
        run_row(session, mode="computed", workers=2, elapsed=elapsed, fingerprint=fingerprint),
        run_row(session, mode="checkpoint_reuse", workers=2, elapsed=0.4, fingerprint=fingerprint),
    ]
    history = session / "derived/pipeline-run/authoritative_handoff_runs.jsonl"
    history.parent.mkdir(parents=True, exist_ok=True)
    history.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return session


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="murmurmark-handoff-corpus-") as raw_root:
        root = Path(raw_root)
        sessions = [build_session(root, index, elapsed) for index, elapsed in enumerate((700.0, 800.0, 850.0), start=1)]
        out = root / "report"
        raw_manifest = "abc  sessions/example/audio/mic/000001.caf\n"
        (out / "raw_before.sha256").parent.mkdir(parents=True, exist_ok=True)
        (out / "raw_before.sha256").write_text(raw_manifest, encoding="utf-8")
        (out / "raw_after.sha256").write_text(raw_manifest, encoding="utf-8")
        command = [
            sys.executable,
            str(REPORTER),
            *(str(session) for session in sessions),
            "--out-dir",
            str(out),
            "--require-raw-integrity",
            "--require-passing-gates",
        ]
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL)
        payload = json.loads((out / "authoritative_handoff_corpus_v1.json").read_text(encoding="utf-8"))
        assert payload["status"] == "passed", payload
        assert payload["summary"]["meaningful_sessions"] == 3
        assert payload["summary"]["cold_p95_sec"] <= 900.0
        assert payload["raw_capture_integrity"]["status"] == "passed"

        (out / "raw_after.sha256").write_text("changed\n", encoding="utf-8")
        failed = subprocess.run(command, stdout=subprocess.DEVNULL)
        assert failed.returncode == 2
        payload = json.loads((out / "authoritative_handoff_corpus_v1.json").read_text(encoding="utf-8"))
        assert payload["gates"]["raw_capture_hashes_unchanged"] is False
        (out / "raw_after.sha256").write_text(raw_manifest, encoding="utf-8")

        history = sessions[-1] / "derived/pipeline-run/authoritative_handoff_runs.jsonl"
        rows = [json.loads(line) for line in history.read_text(encoding="utf-8").splitlines()]
        rows[1]["transcript_fingerprint"]["sha256"] = "regression"
        history.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        failed = subprocess.run(command, stdout=subprocess.DEVNULL)
        assert failed.returncode == 2
        payload = json.loads((out / "authoritative_handoff_corpus_v1.json").read_text(encoding="utf-8"))
        assert payload["status"] == "failed"
        assert payload["sessions"][-1]["gates"]["transcript_fingerprint_equivalent"] is False

    print("authoritative handoff corpus checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
