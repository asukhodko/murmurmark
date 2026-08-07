#!/usr/bin/env python3
"""Regression checks for Reviewed Speaker-Aware Meeting Memory v1."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


memory = load_module(
    "reviewed_speaker_memory_check",
    ROOT / "scripts/materialize-reviewed-speaker-memory.py",
)
anonymous_check = load_module(
    "anonymous_rich_check_for_speaker_memory",
    ROOT / "scripts/check-anonymous-rich-transcript.py",
)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_hashes(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): sha256(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def ordinary_hashes(session: Path) -> dict[str, str]:
    excluded = (
        session / memory.DEFAULT_OUTPUT,
        session / memory.naming.DEFAULT_OUTPUT,
        session / memory.naming.rich.DEFAULT_OUTPUT_DIR,
        session / "review",
    )
    return {
        str(path.relative_to(session)): sha256(path)
        for path in sorted(session.rglob("*"))
        if path.is_file() and not any(path.is_relative_to(root) for root in excluded)
    }


def completed_decisions(template: dict[str, Any], label: str | None) -> dict[str, Any]:
    payload = json.loads(json.dumps(template))
    payload["review_completed"] = True
    for row in payload["labels"]:
        row["action"] = "label" if label is not None else "keep_anonymous"
        row["display_label"] = label
    return payload


def expect_memory_error(reason: str, callback: Any) -> None:
    try:
        callback()
    except memory.MemoryError as error:
        assert reason in str(error), (reason, str(error))
    else:
        raise AssertionError(f"expected MemoryError containing {reason}")


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    binary = ROOT / ".build/debug/murmurmark"
    assert binary.is_file(), "swift build must run before the speaker memory checker"
    return subprocess.run(
        [str(binary), *args],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def run_export(session: Path, out_dir: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/export-session-bundle.py"),
            str(session),
            "--out-dir",
            str(out_dir),
            "--include-json",
            *extra,
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def create_fixture(root: Path) -> tuple[Path, Path]:
    session = anonymous_check.handoff_check.fixture(root, "ready")
    selected_path = session / (
        "derived/transcript-simple/whisper-cpp/resolved/clean_dialogue.reviewed_v1.json"
    )
    selected = json.loads(selected_path.read_text(encoding="utf-8"))
    selected["utterances"].append(
        {
            "id": "utt_000003",
            "start": 4.2,
            "end": 5.0,
            "role": "remote",
            "speaker_label": "Colleagues",
            "text": "Короткий неподтверждённый ответ.",
            "quality": {"needs_review": False},
        }
    )
    write_json(selected_path, selected)
    evidence_path = session / (
        "derived/synthesis-simple/extractive/evidence_notes.reviewed_v1.json"
    )
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["selected"]["outline_blocks"][0]["representative_utterance_ids"].append("utt_000002")
    evidence["selected"]["outline_blocks"][0]["representatives"].append(
        {
            "utterance_id": "utt_000002",
            "role": "Colleagues",
            "start": 2.2,
            "end": 4.0,
            "text": "Договорились.",
        }
    )
    write_json(evidence_path, evidence)
    anonymous_check.evidence_handoff.build_handoff(session)
    policy = ROOT / memory.naming.rich.DEFAULT_POLICY
    source_policy = json.loads(policy.read_text(encoding="utf-8"))["source_evidence"]
    audit_dir, _ = anonymous_check.create_audit(session, source_policy)
    anonymous_root = session / memory.naming.rich.DEFAULT_OUTPUT_DIR
    anonymous_check.rich.build_handoff(session, policy, audit_dir, anonymous_root)

    decisions_path = session / memory.naming.DEFAULT_DECISIONS
    template = memory.naming.write_template(session, decisions_path, False)
    write_json(decisions_path, completed_decisions(template, "Участник A"))
    memory.naming.build_handoff(session, decisions_path, session / memory.naming.DEFAULT_OUTPUT)
    return session, decisions_path


def main() -> int:
    with tempfile.TemporaryDirectory(prefix=".reviewed-memory-check-", dir=ROOT) as temporary:
        root = Path(temporary)
        session, decisions_path = create_fixture(root)
        output_root = session / memory.DEFAULT_OUTPUT
        default_before = ordinary_hashes(session)

        ordinary_export_before = root / "ordinary-before"
        exported = run_export(session, ordinary_export_before)
        assert exported.returncode == 0, exported.stdout + exported.stderr
        ordinary_bundle_before = tree_hashes(ordinary_export_before / session.name)

        first = memory.build_handoff(session, decisions_path, output_root)
        assert first["state"] == "ready"
        assert first["gates"]["referential_integrity"] is True
        assert first["summary"]["reviewed_labels"] == 1
        assert first["summary"]["aggregate_labels"] == 1
        assert first["safety"]["default_notes_export_unchanged"] is True
        memory_path = memory.artifact_path(first, session, "memory_json")
        notes_path = memory.artifact_path(first, session, "notes")
        transcript_path = memory.artifact_path(first, session, "transcript")
        evidence_path = memory.artifact_path(first, session, "handoff_evidence")
        verdict_path = memory.artifact_path(first, session, "quality_verdict")
        assert all(path is not None for path in (memory_path, notes_path, transcript_path, evidence_path, verdict_path))
        payload = json.loads(memory_path.read_text(encoding="utf-8"))
        assert payload["schema"] == memory.MEMORY_SCHEMA
        assert payload["referential_integrity"]["passed"] is True
        known_ids = {row["utterance_id"] for row in payload["utterance_bindings"]}
        assert all(
            set(row["evidence_utterance_ids"]).issubset(known_ids)
            for row in payload["statement_bindings"]
        )
        reviewed = [row for row in payload["speaker_bindings"] if row["display_mode"] == "reviewed_label"]
        assert reviewed and reviewed[0]["decision_row_index"] == 0
        assert reviewed[0]["anonymous_speaker_id"] == "remote_speaker_01"
        assert any(row["display_mode"] == "aggregate_remote" for row in payload["speaker_bindings"])
        assert "Участник A" in notes_path.read_text(encoding="utf-8")
        assert "Участник A" in transcript_path.read_text(encoding="utf-8")
        ordinary_manifest, reasons = anonymous_check.evidence_handoff.load_valid_handoff(session)
        assert ordinary_manifest is not None, reasons
        assert evidence_path.read_bytes() == anonymous_check.evidence_handoff.artifact_path(
            ordinary_manifest, session, "handoff_evidence"
        ).read_bytes()
        assert verdict_path.read_bytes() == anonymous_check.evidence_handoff.artifact_path(
            ordinary_manifest, session, "quality_verdict"
        ).read_bytes()
        assert ordinary_hashes(session) == default_before

        pointer = output_root / "handoff_manifest.json"
        pointer_before = pointer.read_bytes()
        bundle_before = tree_hashes(session / first["bundle"]["path"])
        replay = memory.build_handoff(session, decisions_path, output_root)
        assert replay["semantic_fingerprint"] == first["semantic_fingerprint"]
        assert pointer.read_bytes() == pointer_before
        assert tree_hashes(session / replay["bundle"]["path"]) == bundle_before

        cli_apply = run_cli("speakers", "apply", str(session))
        assert cli_apply.returncode == 0, cli_apply.stdout + cli_apply.stderr
        assert "reviewed_speaker_memory:" in cli_apply.stdout
        assert "state: ready" in cli_apply.stdout

        cli_notes = run_cli("notes", str(session), "--reviewed-speakers", "--path-only")
        assert cli_notes.returncode == 0, cli_notes.stderr
        assert "meeting-memory/reviewed-speakers-v1/bundles" in cli_notes.stdout
        plain_notes = run_cli("notes", str(session), "--path-only")
        assert plain_notes.returncode == 0, plain_notes.stderr
        assert "meeting-memory" not in plain_notes.stdout

        reviewed_export = root / "reviewed-export"
        exported = run_export(session, reviewed_export, "--reviewed-speakers")
        assert exported.returncode == 0, exported.stdout + exported.stderr
        reviewed_manifest = json.loads(
            (reviewed_export / session.name / "export_manifest.json").read_text(encoding="utf-8")
        )
        assert reviewed_manifest["speaker_mode"] == "reviewed_session_labels"
        assert reviewed_manifest["bundle_quality"] == "reviewed_speaker_memory_v1"
        assert "Участник A" in (reviewed_export / session.name / "notes.md").read_text(encoding="utf-8")
        assert (reviewed_export / session.name / "speaker_aware_memory.json").is_file()

        ordinary_export_after = root / "ordinary-after"
        exported = run_export(session, ordinary_export_after)
        assert exported.returncode == 0, exported.stdout + exported.stderr
        assert tree_hashes(ordinary_export_after / session.name) == ordinary_bundle_before

        original_collect = memory.collect_statement_rows
        memory.collect_statement_rows = lambda evidence: original_collect(evidence) + [
            {
                "statement_id": "fixture:unknown",
                "category": "actions",
                "text": "fixture",
                "evidence_utterance_ids": ["utt_unknown"],
                "context_utterance_ids": [],
            }
        ]
        try:
            expect_memory_error(
                "unknown_statement_utterance_id",
                lambda: memory.build_material(session, decisions_path),
            )
        finally:
            memory.collect_statement_rows = original_collect

        decisions = json.loads(decisions_path.read_text(encoding="utf-8"))
        decisions["review_completed"] = False
        write_json(decisions_path, decisions)
        verified, reasons = memory.verify_handoff(session, decisions_path, output_root)
        assert verified is None and "review_not_completed" in reasons[0]
        fallback_notes = run_cli("notes", str(session), "--reviewed-speakers", "--path-only")
        assert fallback_notes.returncode == 0, fallback_notes.stderr
        assert "meeting-memory" not in fallback_notes.stdout
        assert "using ordinary Evidence Handoff v2" in fallback_notes.stderr
        fallback_export = root / "fallback-export"
        exported = run_export(session, fallback_export, "--reviewed-speakers")
        assert exported.returncode == 0, exported.stdout + exported.stderr
        fallback_manifest = json.loads(
            (fallback_export / session.name / "export_manifest.json").read_text(encoding="utf-8")
        )
        assert fallback_manifest["speaker_mode"] == "aggregate_colleagues"
        assert fallback_manifest["selected_speaker_profile"] == "aggregate_colleagues"
        assert fallback_manifest["speaker_resolution_state"] == "fallback"
        assert fallback_manifest["status"] == "exported_with_warnings"
        assert (fallback_export / session.name / "notes.md").read_bytes() == (
            ordinary_export_after / session.name / "notes.md"
        ).read_bytes()

        template = memory.naming.template_payload(session)
        write_json(decisions_path, completed_decisions(template, None))
        memory.naming.build_handoff(session, decisions_path, session / memory.naming.DEFAULT_OUTPUT)
        try:
            memory.build_handoff(
                session,
                decisions_path,
                output_root,
                simulate_interruption_before_publish=True,
            )
        except memory.SimulatedInterruption:
            pass
        else:
            raise AssertionError("simulated interruption did not interrupt publication")
        assert pointer.read_bytes() == pointer_before
        anonymous_memory = memory.build_handoff(session, decisions_path, output_root)
        anonymous_path = memory.artifact_path(anonymous_memory, session, "memory_json")
        assert anonymous_path is not None
        anonymous_payload = json.loads(anonymous_path.read_text(encoding="utf-8"))
        assert any(row["display_mode"] == "anonymous_id" for row in anonymous_payload["speaker_bindings"])
        assert ordinary_hashes(session) == default_before

    print("reviewed speaker memory checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
