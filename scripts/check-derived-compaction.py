#!/usr/bin/env python3
"""Exercise derived compaction safety gates without touching real sessions."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "compact-derived-artifacts.py"


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_session(root: Path, name: str) -> Path:
    session = root / "sessions" / name
    mic = session / "audio/mic/000001.caf"
    remote = session / "audio/remote/000001.caf"
    mic.parent.mkdir(parents=True)
    remote.parent.mkdir(parents=True)
    mic.write_bytes(b"raw-mic" * 64)
    remote.write_bytes(b"raw-remote" * 64)

    transcript = session / "derived/transcript-simple/whisper-cpp/resolved/transcript.reviewed_v1.md"
    notes = session / "derived/synthesis-simple/extractive/notes.reviewed_v1.md"
    verdict = session / "derived/synthesis-simple/extractive/quality_verdict.reviewed_v1.md"
    transcript.parent.mkdir(parents=True)
    notes.parent.mkdir(parents=True)
    transcript.write_text("# Transcript\n\nHello\n", encoding="utf-8")
    notes.write_text("# Notes\n", encoding="utf-8")
    verdict.write_text("# Verdict\n\ngood\n", encoding="utf-8")
    write_json(
        session / "derived/transcript-simple/whisper-cpp/resolved/clean_dialogue.reviewed_v1.json",
        {"schema": "murmurmark.clean_dialogue/v1", "utterances": []},
    )
    write_json(
        session / "derived/transcript-simple/whisper-cpp/resolved/transcript.simple.reviewed_v1.json",
        {"schema": "murmurmark.transcript_simple/v1", "utterances": []},
    )
    write_json(
        session / "derived/synthesis-simple/extractive/evidence_notes.reviewed_v1.json",
        {"schema": "murmurmark.evidence_notes/v2"},
    )
    write_json(
        session / "derived/synthesis-simple/extractive/quality_verdict.reviewed_v1.json",
        {"schema": "murmurmark.quality_verdict/v1", "verdict": "good"},
    )
    write_json(
        session / "derived/outcome/outcome.json",
        {
            "schema": "murmurmark.outcome/v1",
            "selected_profile": "reviewed_v1",
            "summary": {
                "transcript_path": str(transcript.relative_to(session)),
                "notes_path": str(notes.relative_to(session)),
                "quality_verdict_path": str(verdict.relative_to(session)),
            },
        },
    )
    write_json(
        session / "derived/readiness/session_readiness.json",
        {
            "schema": "murmurmark.session_readiness/v1",
            "selected_profile": "reviewed_v1",
            "use_gate": "ready_for_notes",
        },
    )
    write_json(
        session / "derived/pipeline-run/authoritative_handoff.json",
        {"schema": "murmurmark.authoritative_handoff/v1"},
    )
    (session / "derived/preprocess/audio/mic_for_asr.wav").parent.mkdir(parents=True)
    (session / "derived/preprocess/audio/mic_for_asr.wav").write_bytes(b"RIFF" + b"\0" * 4096)
    (session / "derived/live/chunks/000001.caf").parent.mkdir(parents=True)
    (session / "derived/live/chunks/000001.caf").write_bytes(b"caff" + b"\0" * 2048)
    (session / "derived/audit/debug.txt").parent.mkdir(parents=True)
    (session / "derived/audit/debug.txt").write_text("provenance\n", encoding="utf-8")
    (session / "events.jsonl").write_text("{}\n", encoding="utf-8")
    write_json(session / "pipeline_job.json", {"schema": "fixture"})
    write_json(
        session / "session.json",
        {
            "schema": "murmurmark.session/v1",
            "session_id": name,
            "status": "completed",
            "ended_at": "2026-01-01T00:00:00Z",
            "files": {
                "mic": [{"path": "audio/mic/000001.caf", "bytes": mic.stat().st_size}],
                "remote": [{"path": "audio/remote/000001.caf", "bytes": remote.stat().st_size}],
            },
        },
    )
    return session


def run(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            *args,
            "--sessions-root",
            str(root / "sessions"),
        ],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )


def manifest(session: Path) -> dict[str, Any]:
    return json.loads(
        (session / "derived/retention/derived_compaction.json").read_text(encoding="utf-8")
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="murmurmark-derived-compaction-") as temporary:
        root = Path(temporary)
        session = write_session(root, "2026-01-01_10-00-00")
        mic = session / "audio/mic/000001.caf"
        remote = session / "audio/remote/000001.caf"
        transcript = (
            session
            / "derived/transcript-simple/whisper-cpp/resolved/transcript.reviewed_v1.md"
        )
        identities = {path: sha256(path) for path in (mic, remote, transcript)}

        external = root / "outside.wav"
        external.write_bytes(b"outside")
        symlink = session / "derived/audit/outside.wav"
        symlink.symlink_to(external)

        planned = run(root, "plan", str(session))
        assert planned.returncode == 0, (planned.stdout, planned.stderr)
        planned_manifest = manifest(session)
        assert planned_manifest["status"] == "eligible"
        assert planned_manifest["inventory"]["candidate_files"] == 2
        assert planned_manifest["inventory"]["candidate_bytes"] == 6152

        unconfirmed = run(root, "apply", str(session))
        assert unconfirmed.returncode == 2
        assert (session / "derived/preprocess/audio/mic_for_asr.wav").is_file()

        applied = run(root, "apply", str(session), "--confirm-delete-derived-media")
        assert applied.returncode == 0, (applied.stdout, applied.stderr)
        applied_manifest = manifest(session)
        assert applied_manifest["status"] == "applied"
        assert applied_manifest["application"]["deleted_files"] == 2
        assert applied_manifest["verification"]["passed"] is True
        assert not (session / "derived/preprocess/audio/mic_for_asr.wav").exists()
        assert not (session / "derived/live/chunks/000001.caf").exists()
        assert (session / "derived/audit/debug.txt").read_text(encoding="utf-8") == "provenance\n"
        assert symlink.is_symlink()
        assert external.read_bytes() == b"outside"
        for path, expected in identities.items():
            assert sha256(path) == expected

        verified = run(root, "verify", str(session))
        assert verified.returncode == 0, (verified.stdout, verified.stderr)
        assert manifest(session)["status"] == "verified"

        regenerated = session / "derived/preprocess/audio/regenerated.wav"
        regenerated.write_bytes(b"new")
        invalid = run(root, "verify", str(session))
        assert invalid.returncode == 2
        assert manifest(session)["status"] == "reexpanded_or_invalid"
        regenerated.unlink()

        archived = write_session(root, "2026-01-01_11-00-00")
        archived_mic = archived / "audio/mic/000001.caf"
        archived_remote = archived / "audio/remote/000001.caf"
        archived_transcript = (
            archived
            / "derived/transcript-simple/whisper-cpp/resolved/transcript.reviewed_v1.md"
        )
        archived_transcript_hash = sha256(archived_transcript)
        archive_plan = run(root, "plan", str(archived), "--mode", "transcript_only")
        assert archive_plan.returncode == 0, (archive_plan.stdout, archive_plan.stderr)
        archive_plan_manifest = manifest(archived)
        assert archive_plan_manifest["raw_inventory"]["candidate_files"] == 2
        assert archive_plan_manifest["raw_inventory"]["candidate_bytes"] > 0
        assert archived_mic.is_file() and archived_remote.is_file()

        archive_unconfirmed = run(
            root,
            "apply",
            str(archived),
            "--mode",
            "transcript_only",
            "--confirm-delete-derived-media",
        )
        assert archive_unconfirmed.returncode == 2
        assert archived_mic.is_file() and archived_remote.is_file()

        archive_applied = run(
            root,
            "apply",
            str(archived),
            "--mode",
            "transcript_only",
            "--confirm-delete-derived-media",
            "--confirm-delete-raw",
        )
        assert archive_applied.returncode == 0, (
            archive_applied.stdout,
            archive_applied.stderr,
        )
        archive_manifest = manifest(archived)
        assert archive_manifest["status"] == "applied"
        assert archive_manifest["application"]["deleted_raw_files"] == 2
        assert archive_manifest["verification"]["raw_deleted"] is True
        assert not archived_mic.exists() and not archived_remote.exists()
        assert sha256(archived_transcript) == archived_transcript_hash

        archive_verified = run(root, "verify", str(archived))
        assert archive_verified.returncode == 0, (
            archive_verified.stdout,
            archive_verified.stderr,
        )
        assert manifest(archived)["status"] == "verified"
        archive_reapplied = run(
            root,
            "apply",
            str(archived),
            "--mode",
            "transcript_only",
            "--confirm-delete-derived-media",
            "--confirm-delete-raw",
        )
        assert archive_reapplied.returncode == 0
        assert manifest(archived)["verification"]["passed"] is True

        active = write_session(root, "2026-01-02_10-00-00")
        (active / "session.lock").write_text("active\n", encoding="utf-8")
        blocked = run(root, "plan", str(active))
        assert blocked.returncode == 2
        assert "capture_session_lock_present" in manifest(active)["eligibility"]["blockers"]

        pinned = write_session(root, "2026-01-03_10-00-00")
        baseline_manifest = (
            root / "sessions/_reports/frozen-fixture/baseline_manifest.json"
        )
        write_json(
            baseline_manifest,
            {"sessions": [{"session_id": pinned.name}]},
        )
        auto_pinned_plan = run(root, "plan", str(pinned))
        assert auto_pinned_plan.returncode == 2
        auto_pinned_manifest = manifest(pinned)
        assert "pinned_corpus_session" in auto_pinned_manifest["eligibility"]["blockers"]
        assert str(baseline_manifest.resolve()) in auto_pinned_manifest["pin_sources"]
        retired_manifest = root / "sessions/_reports/private-archive/retired_sessions.json"
        write_json(
            retired_manifest,
            {"schema": "murmurmark.retired_sessions/v1", "sessions": [pinned.name]},
        )
        retired_plan = run(root, "plan", str(pinned))
        assert retired_plan.returncode == 0
        assert manifest(pinned)["pinned"] is False
        pin_file = root / "pins.json"
        write_json(pin_file, {"sessions": [pinned.name]})
        pinned_plan = run(root, "plan", str(pinned), "--pin-file", str(pin_file))
        assert pinned_plan.returncode == 2
        assert manifest(pinned)["pinned"] is True
        assert "pinned_corpus_session" in manifest(pinned)["eligibility"]["blockers"]
        pinned_verify = run(root, "verify", str(pinned), "--pin-file", str(pin_file))
        assert pinned_verify.returncode == 2
        assert manifest(pinned)["status"] == "not_compacted"
        assert "compaction_not_applied" in manifest(pinned)["eligibility"]["blockers"]
        included = run(
            root,
            "plan",
            str(pinned),
            "--pin-file",
            str(pin_file),
            "--include-pinned",
        )
        assert included.returncode == 0

        archive_pinned = write_session(root, "2026-01-03_11-00-00")
        archive_pin_file = root / "sessions/_reports/private-archive/pinned_sessions.json"
        write_json(
            archive_pin_file,
            {
                "schema": "murmurmark.pinned_sessions/v1",
                "sessions": [archive_pinned.name, archived.name],
            },
        )
        archive_pinned_plan = run(root, "plan", str(archive_pinned))
        assert archive_pinned_plan.returncode == 2
        archive_manifest = manifest(archive_pinned)
        assert "pinned_corpus_session" in archive_manifest["eligibility"]["blockers"]
        assert str(archive_pin_file.resolve()) in archive_manifest["pin_sources"]

        gated = write_session(root, "2026-01-04_10-00-00")
        missing_export = run(root, "plan", str(gated), "--require-successful-export")
        assert missing_export.returncode == 2
        export_manifest = root / "export_manifest.json"
        write_json(
            export_manifest,
            {
                "schema": "murmurmark.export_manifest/v1",
                "status": "exported",
                "session_id": gated.name,
                "blockers": [],
            },
        )
        export_ready = run(
            root,
            "plan",
            str(gated),
            "--require-successful-export",
            "--export-manifest",
            str(export_manifest),
        )
        assert export_ready.returncode == 0, (export_ready.stdout, export_ready.stderr)

        bulk = run(
            root,
            "plan",
            "all",
            "--older-than",
            "7d",
            "--pin-file",
            str(pin_file),
        )
        assert bulk.returncode == 0, (bulk.stdout, bulk.stderr)
        report = json.loads(
            (
                root
                / "sessions/_reports/retention-compaction/derived_compaction_report.json"
            ).read_text(encoding="utf-8")
        )
        assert report["schema"] == "murmurmark.derived_compaction_report/v1"
        assert report["summary"]["sessions_considered"] == 6
        assert report["summary"]["eligible_candidate_bytes"] < report["summary"]["candidate_bytes"]
        assert any(
            row["session_id"] == pinned.name and row["status"] == "blocked"
            for row in report["sessions"]
        )
        assert any(
            row["session_id"] == archived.name
            and row["status"] == "verified"
            and row["pinned"] is True
            for row in report["sessions"]
        )

        cli_value = os.environ.get("MURMURMARK_BIN")
        if cli_value:
            cli_session = write_session(root, "2026-01-05_10-00-00")
            cli_env = os.environ.copy()
            cli_env["MURMURMARK_HOME"] = str(ROOT)
            cli_env["MURMURMARK_PYTHON"] = sys.executable
            cli_run = subprocess.run(
                [
                    str(Path(cli_value).resolve()),
                    "retention",
                    "compact",
                    "plan",
                    str(cli_session),
                    "--sessions-root",
                    str(root / "sessions"),
                ],
                cwd=root,
                env=cli_env,
                text=True,
                capture_output=True,
                check=False,
            )
            assert cli_run.returncode == 0, (cli_run.stdout, cli_run.stderr)
            assert "derived_compaction:" in cli_run.stdout
            assert "action: plan" in cli_run.stdout

    print("derived compaction checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
