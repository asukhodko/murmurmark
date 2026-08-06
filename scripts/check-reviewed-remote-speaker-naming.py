#!/usr/bin/env python3
"""Regression checks for Reviewed Remote Speaker Naming v1."""

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


naming = load_module(
    "review_remote_speaker_labels_check",
    ROOT / "scripts/review-remote-speaker-labels.py",
)
anonymous_check = load_module(
    "anonymous_rich_check_for_reviewed_naming",
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


def existing_hashes(session: Path) -> dict[str, str]:
    return {
        str(path.relative_to(session)): sha256(path)
        for path in sorted(session.rglob("*"))
        if path.is_file()
    }


def assert_existing_unchanged(session: Path, snapshot: dict[str, str]) -> None:
    current = {
        relative: sha256(session / relative)
        for relative in snapshot
        if (session / relative).is_file()
    }
    assert current == snapshot


def tree_hashes(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): sha256(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def expect_error(reason: str, callback: Any) -> None:
    try:
        callback()
    except naming.NamingError as error:
        assert reason in str(error), (reason, str(error))
    else:
        raise AssertionError(f"expected NamingError containing {reason}")


def completed_decisions(template: dict[str, Any], label: str | None) -> dict[str, Any]:
    payload = json.loads(json.dumps(template))
    payload["review_completed"] = True
    for row in payload["labels"]:
        row["action"] = "label" if label is not None else "keep_anonymous"
        row["display_label"] = label
    return payload


def run_cli(session: Path, *args: str) -> subprocess.CompletedProcess[str]:
    binary = ROOT / ".build/debug/murmurmark"
    assert binary.is_file(), "swift build must run before the reviewed naming checker"
    return subprocess.run(
        [str(binary), *args],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def check_duplicate_label_gate(session: Path, template: dict[str, Any]) -> None:
    original = naming.template_payload
    expanded = json.loads(json.dumps(template))
    second = json.loads(json.dumps(expanded["labels"][0]))
    second["speaker_id"] = "remote_speaker_02"
    expanded["labels"].append(second)
    expanded["source"]["template_fingerprint"] = "fixture-expanded"
    naming.template_payload = lambda _session: expanded
    try:
        duplicate = completed_decisions(expanded, "Одинаковая метка")
        expect_error(
            "display_label_duplicate",
            lambda: naming.validate_decisions(session, duplicate),
        )
    finally:
        naming.template_payload = original


def main() -> int:
    with tempfile.TemporaryDirectory(prefix=".reviewed-speakers-check-", dir=ROOT) as temporary:
        root = Path(temporary)
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
        anonymous_check.evidence_handoff.build_handoff(session)
        policy = ROOT / naming.rich.DEFAULT_POLICY
        policy_payload = json.loads(policy.read_text(encoding="utf-8"))
        source_policy = policy_payload["source_evidence"]
        audit_dir, _ = anonymous_check.create_audit(session, source_policy)
        anonymous_root = session / anonymous_check.rich.DEFAULT_OUTPUT_DIR
        anonymous_manifest = anonymous_check.rich.build_handoff(
            session,
            policy,
            audit_dir,
            anonymous_root,
        )
        anonymous_path = anonymous_check.rich.rich_path(
            anonymous_manifest,
            session,
            "transcript_json",
        )
        assert anonymous_path is not None
        anonymous = json.loads(anonymous_path.read_text(encoding="utf-8"))
        assert any(row.get("speaker_id") is None for row in anonymous["remote_speaker_attributions"])

        decisions_path = session / naming.DEFAULT_DECISIONS
        output_root = session / naming.DEFAULT_OUTPUT
        protected = existing_hashes(session)

        template = naming.write_template(session, decisions_path, False)
        template_bytes = decisions_path.read_bytes()
        assert template["review_completed"] is False
        assert len(template["labels"]) == 1
        assert "Короткий".encode() not in template_bytes
        assert naming.write_template(session, decisions_path, False) == template
        assert decisions_path.read_bytes() == template_bytes
        expect_error(
            "review_not_completed",
            lambda: naming.build_handoff(session, decisions_path, output_root),
        )
        check_duplicate_label_gate(session, template)

        decisions = completed_decisions(template, "Участник A")
        write_json(decisions_path, decisions)
        first = naming.build_handoff(session, decisions_path, output_root)
        assert first["state"] == "ready"
        assert first["gates"]["publish_reviewed_labels"] is True
        assert first["gates"]["voice_identity_inference"] is False
        assert first["summary"] == {
            "speaker_count": 1,
            "labeled_count": 1,
            "kept_anonymous_count": 0,
        }
        reviewed_json = naming.reviewed_path(first, session, "transcript_json")
        reviewed_markdown = naming.reviewed_path(first, session, "transcript_markdown")
        assert reviewed_json is not None and reviewed_markdown is not None
        reviewed = json.loads(reviewed_json.read_text(encoding="utf-8"))
        assert reviewed["utterances"] == anonymous["utterances"]
        assert reviewed["remote_speaker_attributions"] == anonymous["remote_speaker_attributions"]
        markdown = reviewed_markdown.read_text(encoding="utf-8")
        assert "Участник A" in markdown
        assert "Colleagues" in markdown
        assert "Участник A" not in (output_root / "handoff_manifest.json").read_text(encoding="utf-8")
        assert "Участник A" not in (output_root / "report.json").read_text(encoding="utf-8")
        assert "Участник A" not in (output_root / "report.md").read_text(encoding="utf-8")
        assert_existing_unchanged(session, protected)

        pointer = output_root / "handoff_manifest.json"
        pointer_before = pointer.read_bytes()
        bundle_before = tree_hashes(session / first["bundle"]["path"])
        replay = naming.build_handoff(session, decisions_path, output_root)
        assert replay["semantic_fingerprint"] == first["semantic_fingerprint"]
        assert pointer.read_bytes() == pointer_before
        assert tree_hashes(session / replay["bundle"]["path"]) == bundle_before

        changed = completed_decisions(template, "Собеседник")
        write_json(decisions_path, changed)
        try:
            naming.build_handoff(
                session,
                decisions_path,
                output_root,
                simulate_interruption_before_publish=True,
            )
        except naming.SimulatedInterruption:
            pass
        else:
            raise AssertionError("simulated interruption did not interrupt publication")
        assert pointer.read_bytes() == pointer_before
        stale, reasons = naming.verify_handoff(session, decisions_path, output_root)
        assert stale is None and "semantic_fingerprint_mismatch" in reasons
        second = naming.build_handoff(session, decisions_path, output_root)
        assert second["semantic_fingerprint"] != first["semantic_fingerprint"]

        for bad_label, reason in (
            ("Me", "display_label_reserved"),
            ("/" + "Users/example/private", "display_label_unsafe"),
            ("", "display_label_not_trimmed_or_empty"),
            (" Собеседник", "display_label_not_trimmed_or_empty"),
        ):
            write_json(decisions_path, completed_decisions(template, bad_label))
            expect_error(reason, lambda: naming.build_material(session, decisions_path))

        stale_decisions = completed_decisions(template, "Собеседник")
        stale_decisions["source"]["anonymous_semantic_fingerprint"] = "0" * 64
        write_json(decisions_path, stale_decisions)
        expect_error(
            "decision_source_fingerprint_stale",
            lambda: naming.build_material(session, decisions_path),
        )

        anonymous_decisions = completed_decisions(template, None)
        write_json(decisions_path, anonymous_decisions)
        anonymous_review = naming.build_handoff(session, decisions_path, output_root)
        anonymous_markdown = naming.reviewed_path(
            anonymous_review,
            session,
            "transcript_markdown",
        )
        assert anonymous_markdown is not None
        anonymous_text = anonymous_markdown.read_text(encoding="utf-8")
        assert "remote_speaker_01" in anonymous_text
        assert "Colleagues" in anonymous_text

        verified, reasons = naming.verify_handoff(session, decisions_path, output_root)
        assert verified is not None, reasons
        cli_reviewed = run_cli(
            session,
            "transcript",
            str(session),
            "--rich",
            "--reviewed-speakers",
            "--path-only",
        )
        assert cli_reviewed.returncode == 0, cli_reviewed.stderr
        assert "reviewed-speakers-v1/bundles" in cli_reviewed.stdout

        decisions_path.unlink()
        unavailable, reasons = naming.verify_handoff(session, decisions_path, output_root)
        assert unavailable is None and "invalid_or_missing_json" in reasons[0]
        cli_fallback = run_cli(
            session,
            "transcript",
            str(session),
            "--rich",
            "--reviewed-speakers",
            "--path-only",
        )
        assert cli_fallback.returncode == 0, cli_fallback.stderr
        assert "anonymous-v1/bundles" in cli_fallback.stdout
        assert "using anonymous rich transcript" in cli_fallback.stderr
        plain = run_cli(session, "transcript", str(session), "--path-only")
        assert plain.returncode == 0, plain.stderr
        assert "transcript-rich" not in plain.stdout
        assert_existing_unchanged(session, protected)

    print("reviewed remote speaker naming checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
