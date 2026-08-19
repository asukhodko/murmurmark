#!/usr/bin/env python3
"""Regression checks for the disclaimer-bearing provisional speaker transcript."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/materialize-provisional-speaker-transcript.py"
V1_IMPLEMENTATION = ROOT / "scripts/audit-remote-speaker-evidence.py"
OUTCOME_IMPLEMENTATION = ROOT / "scripts/evaluate-outcome.py"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows))


def identity(path: Path, session: Path) -> dict[str, object]:
    return {
        "path": str(path.relative_to(session)),
        "exists": True,
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
    }


def run(session: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(session), *extra],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="murmurmark-provisional-speakers-"))
    try:
        session = root / "session"
        session.mkdir()
        write_json(session / "session.json", {"schema": "murmurmark.session/v1"})
        aggregate = session / "derived/transcript-simple/whisper-cpp/resolved/transcript.reviewed_v1.md"
        dialogue = session / "derived/transcript-simple/whisper-cpp/resolved/clean_dialogue.reviewed_v1.json"
        aggregate.parent.mkdir(parents=True)
        aggregate.write_text("# Aggregate\n\nunchanged\n")
        utterances = [
            {"id": "utt_1", "role": "remote", "start": 0.0, "end": 70.0, "text": "Первый голос."},
            {"id": "utt_2", "role": "remote", "start": 71.0, "end": 86.0, "text": "Второй голос."},
            {"id": "utt_3", "role": "remote", "start": 87.0, "end": 88.0, "text": "Да."},
            {"id": "utt_4", "role": "local", "start": 89.0, "end": 92.0, "text": "Моя реплика."},
        ]
        write_json(dialogue, {"schema": "murmurmark.clean_dialogue/v1", "utterances": utterances})
        readiness = session / "derived/readiness/session_readiness.json"
        write_json(
            readiness,
            {
                "schema": "murmurmark.session_readiness/v1",
                "selected_profile": "reviewed_v1",
                "outputs": {
                    "transcript": {"path": str(aggregate.relative_to(session))},
                    "clean_dialogue": {"path": str(dialogue.relative_to(session))},
                },
            },
        )
        strict = session / "derived/transcript-rich/speaker-resolved-default-v1/selection.json"
        strict_payload = {
            "schema": "murmurmark.speaker_resolved_transcript_selection/v1",
            "state": "fallback",
            "selected_profile": "reviewed_v1",
            "selected_speaker_profile": "aggregate_colleagues",
            "fallback_reason": "coverage_not_publishable:published_speech_ratio",
            "aggregate_transcript": identity(aggregate, session),
            "selected_dialogue": identity(dialogue, session),
            "selected_transcript": identity(aggregate, session),
        }
        write_json(strict, strict_payload)
        v1 = strict.parent / "evidence/key/remote-speaker-evidence-v1"
        report = v1 / "report.json"
        attributions = v1 / "utterance_attribution.jsonl"
        write_json(
            report,
            {
                "schema": "murmurmark.remote_speaker_evidence_report/v1",
                "decision": "DO_NOT_PUBLISH",
                "reasons": ["published_speech_ratio"],
                "implementation": {
                    "fingerprint": {
                        "bytes": V1_IMPLEMENTATION.stat().st_size,
                        "sha256": sha256(V1_IMPLEMENTATION),
                    }
                },
                "source": {
                    "profile": "reviewed_v1",
                    "dialogue": identity(dialogue, session),
                    "raw_remote_before": {"sha256": "same"},
                    "raw_remote_after": {"sha256": "same"},
                },
                "parameters": {
                    "min_cluster_units": 10,
                    "min_cluster_sec": 60.0,
                    "min_cluster_span_sec": 60.0,
                    "min_cluster_cohesion": 0.85,
                },
                "clusters": [
                    {
                        "cluster": 7,
                        "unit_count": 10,
                        "speech_sec": 70.0,
                        "span_sec": 70.0,
                        "cohesion_median": 0.92,
                        "first_start": 0.0,
                    },
                    {
                        "cluster": 8,
                        "unit_count": 10,
                        "speech_sec": 60.0,
                        "span_sec": 60.0,
                        "cohesion_median": 0.88,
                        "first_start": 71.0,
                    },
                ],
            },
        )
        write_jsonl(
            attributions,
            [
                {"utterance_id": "utt_1", "cluster": 7, "reason": "session_publish_gate_failed"},
                {"utterance_id": "utt_2", "cluster": 8, "reason": "session_publish_gate_failed"},
                {"utterance_id": "utt_3", "reason": "too_short_for_voice_evidence"},
            ],
        )
        write_json(
            v1 / "artifact_manifest.json",
            {
                "schema": "murmurmark.remote_speaker_evidence_artifact_manifest/v1",
                "session_id": session.name,
                "artifacts": {
                    "report.json": sha256(report),
                    "utterance_attribution.jsonl": sha256(attributions),
                },
            },
        )

        aggregate_before = aggregate.read_bytes()
        completed = run(session)
        assert completed.returncode == 0, completed.stderr
        out = strict.parent / "provisional"
        selection = json.loads((out / "selection.json").read_text())
        markdown = (out / "transcript.provisional.md").read_text()
        rich = json.loads((out / "transcript.provisional.json").read_text())
        assert selection["state"] == "provisional"
        assert selection["selected_speaker_profile"] == "remote_speaker_provisional_v1"
        assert selection["summary"]["speaker_clusters"] == 2
        assert "Speaker attribution is provisional" in markdown
        assert "## 00:00 remote_speaker_01" in markdown
        assert "## 01:11 remote_speaker_02" in markdown
        assert "remote_speaker_unknown [unattributed]" in markdown
        assert "## 01:29 Me" in markdown
        assert rich["utterances"][0]["text"] == utterances[0]["text"]
        assert rich["utterances"][1]["speaker_id"] == "remote_speaker_02"
        assert aggregate.read_bytes() == aggregate_before

        first_selection = (out / "selection.json").read_bytes()
        replay = run(session)
        assert replay.returncode == 0, replay.stderr
        assert (out / "selection.json").read_bytes() == first_selection
        verified = run(session, "--verify-only")
        assert verified.returncode == 0, verified.stdout + verified.stderr

        spec = importlib.util.spec_from_file_location("evaluate_outcome", OUTCOME_IMPLEMENTATION)
        assert spec is not None and spec.loader is not None
        outcome_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(outcome_module)
        noop = root / "noop.py"
        noop.write_text("raise SystemExit(0)\n")
        outcome_module.SPEAKER_SELECTOR = noop
        outcome_module.PROVISIONAL_SPEAKER_MATERIALIZER = noop
        outcome_speaker = outcome_module.speaker_resolution(session, "reviewed_v1")
        assert outcome_speaker["state"] == "provisional", outcome_speaker
        assert outcome_speaker["selected_speaker_profile"] == "remote_speaker_provisional_v1"
        assert outcome_speaker["attributed_remote_speech_ratio"] > 0

        strict_payload["state"] = "selected"
        strict_payload["selected_speaker_profile"] = "remote_speaker_coverage_v3"
        strict_payload["fallback_reason"] = None
        write_json(strict, strict_payload)
        promoted = run(session, "--print-path")
        assert promoted.returncode == 0, promoted.stderr
        assert "state=verified" in promoted.stdout
        assert str(aggregate) in promoted.stdout

        strict_payload["state"] = "fallback"
        strict_payload["selected_speaker_profile"] = "aggregate_colleagues"
        strict_payload["fallback_reason"] = "coverage_not_publishable:published_speech_ratio"
        write_json(strict, strict_payload)

        shutil.rmtree(strict.parent / "evidence")
        shutil.rmtree(out)
        unavailable = run(session)
        assert unavailable.returncode == 0, unavailable.stderr
        unavailable_selection = json.loads((out / "selection.json").read_text())
        unavailable_markdown = (out / "transcript.provisional.md").read_text()
        assert unavailable_selection["state"] == "unavailable"
        assert unavailable_selection["summary"]["speaker_clusters"] == 0
        assert "Speaker attribution is unavailable" in unavailable_markdown
        assert unavailable_markdown.count("remote_speaker_unknown [unattributed]") == 3
        assert aggregate.read_bytes() == aggregate_before
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print("provisional speaker transcript checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
