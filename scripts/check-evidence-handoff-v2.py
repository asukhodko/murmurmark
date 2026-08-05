#!/usr/bin/env python3
"""Regression checks for Evidence Notes and Export v2."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import evidence_handoff_v2 as handoff  # noqa: E402


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def fixture(
    root: Path,
    name: str,
    *,
    no_speech: bool = False,
    review_required: bool = False,
    profile_mismatch: bool = False,
    unknown_evidence: bool = False,
    unknown_representative: bool = False,
) -> Path:
    session = root / "sessions" / name
    profile = "reviewed_v1"
    suffix = f".{profile}"
    resolved = session / "derived/transcript-simple/whisper-cpp/resolved"
    synthesis = session / "derived/synthesis-simple/extractive"
    readiness_dir = session / "derived/readiness"
    write_json(
        session / "session.json",
        {"schema": "murmurmark.session/v1", "session_id": name, "status": "completed"},
    )
    utterances = [] if no_speech else [
        {
            "id": "utt_000001",
            "start": 0.0,
            "end": 2.0,
            "role": "mic",
            "speaker_label": "Me",
            "text": "Нужно добавить задачу на алерты.",
            "quality": {"needs_review": False},
        },
        {
            "id": "utt_000002",
            "start": 2.2,
            "end": 4.0,
            "role": "remote",
            "speaker_label": "Colleagues",
            "text": "Договорились.",
            "quality": {"needs_review": False},
        },
    ]
    write_json(
        resolved / f"clean_dialogue{suffix}.json",
        {"schema": handoff.DIALOGUE_SCHEMA, "session": name, "utterances": utterances},
    )
    write_text(resolved / f"transcript{suffix}.md", "# Source transcript\n")
    write_text(synthesis / f"notes{suffix}.md", "# Source notes\n")
    write_text(synthesis / f"quality_verdict{suffix}.md", "# Source verdict\n")
    selected = {
        "outline_blocks": [],
        "decisions": [],
        "actions": [],
        "risks": [],
        "open_questions": [],
    }
    if not no_speech:
        selected["outline_blocks"] = [
            {
                "id": "topic_0001",
                "start": 0.0,
                "end": 4.0,
                "keywords": ["алерты"],
                "representative_utterance_ids": ["utt_missing" if unknown_evidence else "utt_000001"],
                "representatives": [
                    {
                        "utterance_id": (
                            "utt_missing_representative"
                            if unknown_representative
                            else "utt_missing" if unknown_evidence else "utt_000001"
                        ),
                        "role": "Me",
                        "start": 0.0,
                        "end": 2.0,
                        "text": "Нужно добавить задачу на алерты.",
                    }
                ],
            }
        ]
        selected["actions"] = [
            {
                "id": "cand_action_0001",
                "display_text": "Нужно добавить задачу на алерты.",
                "evidence_utterance_ids": ["utt_000001"],
                "context_utterance_ids": ["utt_000002"],
                "time": {"start": 0.0, "end": 2.0},
                "needs_review": True,
            }
        ]
    evidence_path = synthesis / f"evidence_notes{suffix}.json"
    write_json(
        evidence_path,
        {
            "schema": handoff.NOTES_SCHEMA,
            "generator": {"name": "fixture", "version": "1"},
            "session_id": name,
            "source": {"transcript_profile": profile},
            "selected": selected,
            "review": {"items": [], "summary": {}},
        },
    )
    review_path = synthesis / f"review_items{suffix}.jsonl"
    write_text(review_path, "")
    quality_profile = "wrong_profile" if profile_mismatch else profile
    write_json(
        synthesis / f"quality_verdict{suffix}.json",
        {
            "schema": handoff.QUALITY_SCHEMA,
            "verdict": "good" if not review_required else "usable_with_review",
            "selected_transcript_profile": quality_profile,
            "review_summary": {"review_item_count": 0, "review_item_seconds": 0.0},
        },
    )
    outputs = {
        "transcript": {"path": str((resolved / f"transcript{suffix}.md").relative_to(session)), "exists": True},
        "clean_dialogue": {"path": str((resolved / f"clean_dialogue{suffix}.json").relative_to(session)), "exists": True},
        "notes": {"path": str((synthesis / f"notes{suffix}.md").relative_to(session)), "exists": True},
        "quality_verdict": {"path": str((synthesis / f"quality_verdict{suffix}.md").relative_to(session)), "exists": True},
        "evidence_notes": {"path": str(evidence_path.relative_to(session)), "exists": True},
        "review_items": {"path": str(review_path.relative_to(session)), "exists": True},
    }
    write_json(
        readiness_dir / "session_readiness.json",
        {
            "schema": handoff.READINESS_SCHEMA,
            "session_id": name,
            "selected_profile": profile,
            "verdict": "good" if not review_required else "usable_with_review",
            "use_gate": "ready_for_notes" if not review_required else "review_first",
            "session_classification": "verified_no_speech" if no_speech else "conversation",
            "export_blockers": ["mandatory_review_open"] if review_required else [],
            "review_blockers": [],
            "metrics": {
                "review_burden_sec": 5.0 if review_required else 0.0,
                "transcript_review_burden_sec": 5.0 if review_required else 0.0,
                "review_scope_required_rows": 1 if review_required else 0,
                "review_scope_closed_rows": 0,
            },
            "outputs": outputs,
        },
    )
    return session


def tree_hashes(path: Path) -> dict[str, str]:
    return {
        str(item.relative_to(path)): hashlib.sha256(item.read_bytes()).hexdigest()
        for item in sorted(path.rglob("*"))
        if item.is_file()
    }


def run_export(session: Path, out: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/export-session-bundle.py"),
            str(session),
            "--out-dir",
            str(out),
            "--include-json",
            *extra,
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="murmurmark-handoff-v2-") as temporary:
        root = Path(temporary)
        ready = fixture(root, "ready")
        first = handoff.build_handoff(ready)
        assert first["state"] == "ready"
        assert first["gates"]["export_allowed"] is True
        first_bytes = (ready / "derived/handoff-v2/handoff_manifest.json").read_bytes()
        first_bundle = tree_hashes(ready / first["bundle"]["path"])
        second = handoff.build_handoff(ready)
        assert second["semantic_fingerprint"] == first["semantic_fingerprint"]
        assert (ready / "derived/handoff-v2/handoff_manifest.json").read_bytes() == first_bytes
        assert tree_hashes(ready / second["bundle"]["path"]) == first_bundle
        incomplete = json.loads(json.dumps(second))
        incomplete["inputs"].pop("transcript")
        valid, invalid_reasons = handoff.verify_manifest(incomplete, ready)
        assert valid is False
        assert "manifest_inputs_incomplete" in invalid_reasons

        notes = handoff.artifact_path(first, ready, "notes")
        assert notes is not None
        notes_text = notes.read_text(encoding="utf-8")
        assert "utt_000001" in notes_text
        assert "Potential Actions" in notes_text

        transcript_source = ready / first["inputs"]["transcript"]["path"]
        transcript_source.write_text("# Changed source transcript\n", encoding="utf-8")
        stale, stale_reasons = handoff.load_valid_handoff(ready)
        assert stale is None
        assert "stale_input_hash:transcript" in stale_reasons
        rebuilt = handoff.build_handoff(ready)
        assert rebuilt["semantic_fingerprint"] != first["semantic_fingerprint"]

        pointer_before = (ready / "derived/handoff-v2/handoff_manifest.json").read_bytes()
        transcript_source.write_text("# Interrupted source transcript\n", encoding="utf-8")
        try:
            handoff.build_handoff(ready, simulate_interruption_before_publish=True)
        except handoff.SimulatedInterruption:
            pass
        else:
            raise AssertionError("simulated interruption did not interrupt publication")
        assert (ready / "derived/handoff-v2/handoff_manifest.json").read_bytes() == pointer_before
        handoff.build_handoff(ready)

        export_root = root / "exports"
        exported = run_export(ready, export_root)
        assert exported.returncode == 0, exported.stdout
        export_dir = export_root / ready.name
        export_hashes = tree_hashes(export_dir)
        exported_again = run_export(ready, export_root)
        assert exported_again.returncode == 0, exported_again.stdout
        assert tree_hashes(export_dir) == export_hashes
        export_text = "\n".join(
            path.read_text(encoding="utf-8", errors="replace")
            for path in export_dir.rglob("*")
            if path.is_file()
        )
        assert str(root) not in export_text
        assert "/Users/" not in export_text

        review = fixture(root, "review", review_required=True)
        review_manifest = handoff.build_handoff(review)
        assert review_manifest["state"] == "review_required"
        assert review_manifest["export"]["allowed"] is False
        forced = run_export(review, root / "review-export", "--force")
        assert forced.returncode == 2
        blocked = json.loads(
            (root / "review-export/review.export_blocked.json").read_text(encoding="utf-8")
        )
        assert blocked["status"] == "blocked"
        assert "force_cannot_bypass_handoff_v2" in blocked["warnings"]

        no_speech = fixture(root, "no-speech", no_speech=True)
        no_speech_manifest = handoff.build_handoff(no_speech)
        assert no_speech_manifest["state"] == "no_speech"
        assert no_speech_manifest["export"]["allowed"] is True
        assert run_export(no_speech, root / "no-speech-export").returncode == 0

        unknown = fixture(root, "unknown", unknown_evidence=True)
        unknown_manifest = handoff.build_handoff(unknown)
        assert unknown_manifest["state"] == "blocked"
        assert "unknown_evidence_utterance_id" in unknown_manifest["blockers"]

        unknown_representative = fixture(
            root, "unknown-representative", unknown_representative=True
        )
        unknown_representative_manifest = handoff.build_handoff(unknown_representative)
        assert unknown_representative_manifest["state"] == "blocked"
        assert "unknown_evidence_utterance_id" in unknown_representative_manifest["blockers"]

        mismatch = fixture(root, "mismatch", profile_mismatch=True)
        mismatch_manifest = handoff.build_handoff(mismatch)
        assert mismatch_manifest["state"] == "blocked"
        assert "quality_profile_mismatch" in mismatch_manifest["blockers"]

        corpus_out = root / "corpus-report"
        corpus = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/report-evidence-handoff-corpus.py"),
                str(ready),
                str(review),
                str(no_speech),
                "--refresh",
                "--strict",
                "--out-dir",
                str(corpus_out),
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        assert corpus.returncode == 0, corpus.stdout
        corpus_report = json.loads(
            (corpus_out / "evidence_handoff_corpus.json").read_text(encoding="utf-8")
        )
        assert corpus_report["schema"] == "murmurmark.evidence_handoff_corpus/v2"
        assert corpus_report["gates"]["passed"] is True
        assert corpus_report["by_state"] == {
            "no_speech": 1,
            "ready": 1,
            "review_required": 1,
        }

    print("evidence handoff v2 checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
