#!/usr/bin/env python3
"""Regression checks for Anonymous Rich Transcript Handoff v1."""

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

import evidence_handoff_v2 as evidence_handoff  # noqa: E402


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rich = load_module(
    "materialize_anonymous_rich_transcript",
    ROOT / "scripts/materialize-anonymous-rich-transcript.py",
)
handoff_check = load_module(
    "check_evidence_handoff_v2_fixture",
    ROOT / "scripts/check-evidence-handoff-v2.py",
)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_identity(path: Path, session: Path) -> dict[str, Any]:
    return {
        "path": str(path.relative_to(session)),
        "exists": True,
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
    }


def ordinary_hashes(session: Path) -> dict[str, str]:
    excluded = session / rich.DEFAULT_OUTPUT_DIR
    return {
        str(path.relative_to(session)): sha256(path)
        for path in sorted(session.rglob("*"))
        if path.is_file() and not path.is_relative_to(excluded)
    }


def tree_hashes(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): sha256(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def refresh_artifact_manifest(audit_dir: Path, session_id: str) -> None:
    names = ["report.json", "speaker_map.json", "utterance_attribution.jsonl"]
    write_json(
        audit_dir / "artifact_manifest.json",
        {
            "schema": rich.AUDIT_MANIFEST_SCHEMA,
            "session_id": session_id,
            "artifacts": {name: sha256(audit_dir / name) for name in names},
        },
    )


def create_policy(root: Path) -> tuple[Path, dict[str, Any]]:
    implementation = {
        "script": "fixture-audit.py",
        "version": "0.1.0",
        "fingerprint": {"exists": True, "bytes": 7, "sha256": "a" * 64},
    }
    model = {
        "method": "fixture_dvector",
        "package_version": "1.0",
        "runtime": {"python": "fixture"},
        "model": {"exists": True, "bytes": 11, "sha256": "b" * 64},
        "license": "test-only",
    }
    parameters = {"cluster_distance": 0.2, "min_assignment_similarity": 0.72}
    reporter = {
        "script": "fixture-corpus-report.py",
        "version": "0.1.0",
        "fingerprint": {"exists": True, "bytes": 13, "sha256": "c" * 64},
    }
    corpus = root / "frozen-corpus.json"
    write_json(
        corpus,
        {
            "schema": rich.CORPUS_MANIFEST_SCHEMA,
            "version": 1,
            "generator": reporter,
            "sessions": [
                {
                    "session_id": "frozen-approval-row",
                    "inputs": {
                        "implementation": implementation,
                        "model": model,
                        "parameters": parameters,
                    },
                }
            ],
        },
    )
    policy = root / "policy.json"
    write_json(
        policy,
        {
            "schema": rich.POLICY_SCHEMA,
            "version": 1,
            "decision": "PROMOTE_OPTIONAL_RICH",
            "scope": "optional_session_local_anonymous",
            "source_evidence": {
                "decision": "PROMOTE_AUDIT_ONLY",
                "promotion_scope": "optional_anonymous_remote_speaker_evidence",
                "frozen_manifest_path": str(corpus.relative_to(ROOT)),
                "frozen_manifest": {"bytes": corpus.stat().st_size, "sha256": sha256(corpus)},
                "audit_implementation": implementation,
                "corpus_reporter": reporter,
                "model": model,
                "parameters": parameters,
            },
            "constraints": {
                "speaker_id_pattern": rich.SPEAKER_ID_RE.pattern,
                "speaker_names_allowed": False,
                "cross_session_identity_allowed": False,
                "plain_transcript_authoritative": True,
            },
        },
    )
    return policy, json.loads(policy.read_text(encoding="utf-8"))["source_evidence"]


def create_audit(
    session: Path,
    source_policy: dict[str, Any],
    *,
    speaker_id: str = "remote_speaker_01",
) -> tuple[Path, Path]:
    selected_manifest, reasons = evidence_handoff.load_valid_handoff(session)
    assert selected_manifest is not None, reasons
    selected_path = session / selected_manifest["inputs"]["clean_dialogue"]["path"]
    selected = json.loads(selected_path.read_text(encoding="utf-8"))
    audit_source = session / "derived/audit-source/clean_dialogue.json"
    audit_dialogue = json.loads(json.dumps(selected))
    audit_dialogue["utterances"][0]["text"] = "Старый текст Me не должен попасть в rich transcript."
    write_json(audit_source, audit_dialogue)

    raw_remote = session / "audio/remote/000001.caf"
    remote_audio = session / "derived/preprocess/audio/remote_for_aec.wav"
    raw_remote.parent.mkdir(parents=True, exist_ok=True)
    remote_audio.parent.mkdir(parents=True, exist_ok=True)
    raw_remote.write_bytes(b"raw remote fixture\n")
    remote_audio.write_bytes(b"prepared remote fixture\n")

    remote_rows = [row for row in selected["utterances"] if row.get("role") == "remote"]
    assert len(remote_rows) == 2
    audit_dir = session / rich.DEFAULT_AUDIT_DIR
    audit_dir.mkdir(parents=True, exist_ok=True)
    write_json(
        audit_dir / "speaker_map.json",
        {
            "schema": rich.AUDIT_MAP_SCHEMA,
            "session_id": session.name,
            "scope": "session_local_anonymous",
            "status": "published_audit_only",
            "speakers": [{"speaker_id": speaker_id, "cluster": 0}],
        },
    )
    attributions = [
        {
            "schema": rich.AUDIT_ATTRIBUTION_SCHEMA,
            "utterance_id": remote_rows[0]["id"],
            "start": remote_rows[0]["start"],
            "end": remote_rows[0]["end"],
            "status": "attributed",
            "speaker_id": speaker_id,
            "speaker_label": speaker_id,
            "reason": "stable_fixture_cluster",
        },
        {
            "schema": rich.AUDIT_ATTRIBUTION_SCHEMA,
            "utterance_id": remote_rows[1]["id"],
            "start": remote_rows[1]["start"],
            "end": remote_rows[1]["end"],
            "status": "aggregate",
            "speaker_id": None,
            "speaker_label": "Colleagues",
            "reason": "abstain_fixture",
        },
    ]
    (audit_dir / "utterance_attribution.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in attributions),
        encoding="utf-8",
    )
    raw_identity = source_identity(raw_remote, session)
    write_json(
        audit_dir / "report.json",
        {
            "schema": rich.AUDIT_REPORT_SCHEMA,
            "session_id": session.name,
            "status": "completed",
            "decision": "PUBLISH_AUDIT_EVIDENCE",
            "implementation": source_policy["audit_implementation"],
            "model": source_policy["model"],
            "parameters": source_policy["parameters"],
            "source": {
                "profile": "fixture_audit_source",
                "dialogue": source_identity(audit_source, session),
                "remote_audio": source_identity(remote_audio, session),
                "raw_remote_before": raw_identity,
                "raw_remote_after": raw_identity,
            },
            "gates": {"publish_session_speaker_map": True},
            "safety": {
                "selected_dialogue_unchanged": True,
                "raw_remote_unchanged": True,
            },
            "summary": {"published_speakers": 1, "published_speech_ratio": 0.5},
        },
    )
    refresh_artifact_manifest(audit_dir, session.name)
    return audit_dir, audit_source


def expect_error(reason: str, callback: Any) -> None:
    try:
        callback()
    except rich.RichHandoffError as error:
        assert reason in str(error), (reason, str(error))
    else:
        raise AssertionError(f"expected RichHandoffError containing {reason}")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix=".anonymous-rich-check-", dir=ROOT) as temporary:
        root = Path(temporary)
        session = handoff_check.fixture(root, "ready")
        selected_path = session / "derived/transcript-simple/whisper-cpp/resolved/clean_dialogue.reviewed_v1.json"
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
        evidence_handoff.build_handoff(session)
        policy, source_policy = create_policy(root)
        audit_dir, audit_source = create_audit(session, source_policy)
        output_root = session / rich.DEFAULT_OUTPUT_DIR

        ordinary_before = ordinary_hashes(session)
        first = rich.build_handoff(session, policy, audit_dir, output_root)
        assert first["state"] == "ready"
        assert first["gates"]["publish_optional_rich"] is True
        assert first["referential_integrity"]["remote_utterances"] == 2
        assert ordinary_hashes(session) == ordinary_before
        rich_json_path = rich.rich_path(first, session, "transcript_json")
        rich_md_path = rich.rich_path(first, session, "transcript_markdown")
        assert rich_json_path is not None and rich_md_path is not None
        rich_payload = json.loads(rich_json_path.read_text(encoding="utf-8"))
        assert rich_payload["utterances"] == selected["utterances"]
        assert [row["utterance_id"] for row in rich_payload["remote_speaker_attributions"]] == [
            "utt_000002",
            "utt_000003",
        ]
        rich_markdown = rich_md_path.read_text(encoding="utf-8")
        assert "remote_speaker_01" in rich_markdown
        assert "Colleagues" in rich_markdown
        assert "Старый текст Me" not in rich_markdown

        pointer = output_root / "handoff_manifest.json"
        pointer_before = pointer.read_bytes()
        bundle_before = tree_hashes(session / first["bundle"]["path"])
        second = rich.build_handoff(session, policy, audit_dir, output_root)
        assert second["semantic_fingerprint"] == first["semantic_fingerprint"]
        assert pointer.read_bytes() == pointer_before
        assert tree_hashes(session / second["bundle"]["path"]) == bundle_before
        verified, reasons = rich.verify_handoff(session, policy, audit_dir, output_root)
        assert verified is not None, reasons

        selected = json.loads(selected_path.read_text(encoding="utf-8"))
        selected["utterances"][0]["text"] = "Новый Me из актуального selected profile."
        write_json(selected_path, selected)
        evidence_handoff.build_handoff(session)
        ordinary_before_interruption = ordinary_hashes(session)
        bundles_before = set((output_root / "bundles").iterdir())
        try:
            rich.build_handoff(
                session,
                policy,
                audit_dir,
                output_root,
                simulate_interruption_before_publish=True,
            )
        except rich.SimulatedInterruption:
            pass
        else:
            raise AssertionError("simulated interruption did not interrupt publication")
        assert pointer.read_bytes() == pointer_before
        assert len(set((output_root / "bundles").iterdir()) - bundles_before) == 1
        stale, reasons = rich.verify_handoff(session, policy, audit_dir, output_root)
        assert stale is None and "semantic_basis_mismatch" in reasons
        rebuilt = rich.build_handoff(session, policy, audit_dir, output_root)
        assert rebuilt["semantic_fingerprint"] != first["semantic_fingerprint"]
        assert ordinary_hashes(session) == ordinary_before_interruption
        rebuilt_json = rich.rich_path(rebuilt, session, "transcript_json")
        assert rebuilt_json is not None
        assert "Новый Me" in rebuilt_json.read_text(encoding="utf-8")

        audit_snapshot = {path.name: path.read_bytes() for path in audit_dir.iterdir() if path.is_file()}
        audit_source_before = audit_source.read_bytes()
        attribution_path = audit_dir / "utterance_attribution.jsonl"
        attribution_path.write_bytes(attribution_path.read_bytes() + b"\n")
        expect_error(
            "audit_artifact_hash_mismatch",
            lambda: rich.build_handoff(session, policy, audit_dir, output_root),
        )
        for name, payload in audit_snapshot.items():
            (audit_dir / name).write_bytes(payload)

        audit_dialogue = json.loads(audit_source.read_text(encoding="utf-8"))
        audit_dialogue["utterances"][1]["text"] = "Изменённый remote текст."
        write_json(audit_source, audit_dialogue)
        report_path = audit_dir / "report.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report["source"]["dialogue"] = source_identity(audit_source, session)
        write_json(report_path, report)
        refresh_artifact_manifest(audit_dir, session.name)
        expect_error(
            "selected_remote_projection_mismatch",
            lambda: rich.build_handoff(session, policy, audit_dir, output_root),
        )
        for name, payload in audit_snapshot.items():
            (audit_dir / name).write_bytes(payload)
        audit_source.write_bytes(audit_source_before)

        speaker_map_path = audit_dir / "speaker_map.json"
        speaker_map = json.loads(speaker_map_path.read_text(encoding="utf-8"))
        speaker_map["speakers"][0]["speaker_id"] = "explicit_person_label"
        write_json(speaker_map_path, speaker_map)
        refresh_artifact_manifest(audit_dir, session.name)
        expect_error(
            "invalid_anonymous_speaker_id",
            lambda: rich.build_handoff(session, policy, audit_dir, output_root),
        )
        for name, payload in audit_snapshot.items():
            (audit_dir / name).write_bytes(payload)

        report = json.loads(report_path.read_text(encoding="utf-8"))
        report["model"] = {"method": "unapproved_model"}
        write_json(report_path, report)
        refresh_artifact_manifest(audit_dir, session.name)
        expect_error(
            "audit_model_not_promoted",
            lambda: rich.build_handoff(session, policy, audit_dir, output_root),
        )
        for name, payload in audit_snapshot.items():
            (audit_dir / name).write_bytes(payload)

        unavailable_dir = session / "derived/transcript-rich/missing-policy"
        missing = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/materialize-anonymous-rich-transcript.py"),
                str(session),
                "--policy",
                str(root / "missing-policy.json"),
                "--audit-dir",
                str(audit_dir),
                "--out-dir",
                str(unavailable_dir),
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        assert missing.returncode == 0, missing.stdout
        unavailable = json.loads((unavailable_dir / "handoff_manifest.json").read_text(encoding="utf-8"))
        assert unavailable["state"] == "unavailable"
        assert "invalid_or_missing_json" in unavailable["reasons"][0]

        bundle_manifest = session / rebuilt["bundle"]["path"] / "handoff_manifest.json"
        bundle_manifest_before = bundle_manifest.read_bytes()
        bundle_manifest.write_text("{}\n", encoding="utf-8")
        invalid, reasons = rich.verify_handoff(session, policy, audit_dir, output_root)
        assert invalid is None and "bundle_manifest_mismatch" in reasons
        bundle_manifest.write_bytes(bundle_manifest_before)
        valid, reasons = rich.verify_handoff(session, policy, audit_dir, output_root)
        assert valid is not None, reasons

    print("anonymous rich transcript checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
