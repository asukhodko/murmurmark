#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf


ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "scripts/audit-remote-speaker-evidence.py"
CORPUS = ROOT / "scripts/report-remote-speaker-evidence-corpus.py"
ROSTER = ROOT / "scripts/configure-remote-speaker-roster.py"
FIXTURE_SCHEMA = "murmurmark.remote_speaker_embedding_fixture/v1"


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def invoke(command: list[str]) -> None:
    subprocess.run(
        command,
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def vector(speaker: int, index: int) -> list[float]:
    value = np.zeros(4, dtype=np.float32)
    value[speaker] = 1.0
    value[3] = ((index % 5) - 2) * 0.01
    value /= np.linalg.norm(value)
    return value.tolist()


def build_session(root: Path, session_id: str, speakers: int, units_per_speaker: int) -> tuple[Path, dict[str, str]]:
    session = root / session_id
    rate = 16_000
    utterances: list[dict[str, Any]] = []
    embeddings: dict[str, list[float]] = {}
    reference: dict[str, str] = {}
    samples: list[np.ndarray] = []
    current = 0.0
    counts = [0] * speakers
    total = speakers * units_per_speaker
    for index in range(total):
        speaker = index % speakers
        counts[speaker] += 1
        start = current
        end = start + 3.5
        utterance_id = f"utt_{index + 1:06d}"
        text = f"Содержательная фраза участника {speaker + 1} номер {counts[speaker]} для проверки"
        utterances.append(
            {
                "id": utterance_id,
                "role": "remote",
                "speaker_label": "Colleagues",
                "start": start,
                "end": end,
                "source_start": start,
                "source_end": end,
                "source_track": "remote",
                "text": text,
                "quality": {"needs_review": False},
            }
        )
        embeddings[utterance_id] = vector(speaker, index)
        reference[utterance_id] = f"Reference {speaker + 1}"
        tone = 0.02 * np.sin(2 * np.pi * (180 + speaker * 70) * np.arange(int(3.5 * rate)) / rate)
        samples.append(tone.astype(np.float32))
        samples.append(np.zeros(int(0.5 * rate), dtype=np.float32))
        current += 4.0

    raw = session / "audio/remote/000001.caf"
    raw.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(raw), np.concatenate(samples), rate, format="CAF", subtype="FLOAT")
    write_json(session / "session.json", {"schema": "murmurmark.session/v1", "session_id": session_id})
    write_json(
        session / "derived/transcript-simple/whisper-cpp/resolved/clean_dialogue.json",
        {"schema": "murmurmark.clean_dialogue/v1", "session": session_id, "utterances": utterances},
    )
    fixture = session / "embedding_fixture.json"
    write_json(fixture, {"schema": FIXTURE_SCHEMA, "embeddings": embeddings})
    return session, reference


def build_roster_merge_session(root: Path, session_id: str, *, ambiguous: bool) -> Path:
    session = root / session_id
    rate = 16_000
    units_per_cluster = 20
    primary_centers = np.eye(8, dtype=np.float32)[:5]
    primary_centers[4] = 0.90 * primary_centers[0] + np.sqrt(1.0 - 0.90**2) * primary_centers[4]
    consensus_centers = np.eye(8, dtype=np.float32)[:5]
    consensus_centers[4] = 0.85 * consensus_centers[0] + np.sqrt(1.0 - 0.85**2) * consensus_centers[4]
    if ambiguous:
        primary_centers[2] = 0.89 * primary_centers[1] + np.sqrt(1.0 - 0.89**2) * primary_centers[2]
        consensus_centers[2] = 0.84 * consensus_centers[1] + np.sqrt(1.0 - 0.84**2) * consensus_centers[2]

    utterances: list[dict[str, Any]] = []
    primary: dict[str, list[float]] = {}
    consensus: dict[str, list[float]] = {}
    samples: list[np.ndarray] = []
    current = 0.0
    # The two acoustic regimes of the same true speaker are adjacent and do not overlap.
    order = [0, 4, 1, 2, 3]
    for cluster_id in order:
        for unit_index in range(units_per_cluster):
            utterance_id = f"utt_{len(utterances) + 1:06d}"
            utterances.append(
                {
                    "id": utterance_id,
                    "role": "remote",
                    "speaker_label": "Colleagues",
                    "start": current,
                    "end": current + 3.5,
                    "source_start": current,
                    "source_end": current + 3.5,
                    "source_track": "remote",
                    "text": f"Содержательная проверочная фраза {cluster_id} {unit_index}",
                    "quality": {"needs_review": False},
                }
            )
            primary[utterance_id] = primary_centers[cluster_id].tolist()
            consensus[utterance_id] = consensus_centers[cluster_id].tolist()
            tone = 0.02 * np.sin(
                2 * np.pi * (170 + cluster_id * 60) * np.arange(int(3.5 * rate)) / rate
            )
            samples.extend([tone.astype(np.float32), np.zeros(int(0.5 * rate), dtype=np.float32)])
            current += 4.0

    raw = session / "audio/remote/000001.caf"
    raw.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(raw), np.concatenate(samples), rate, format="CAF", subtype="FLOAT")
    write_json(session / "session.json", {"schema": "murmurmark.session/v1", "session_id": session_id})
    write_json(
        session / "derived/transcript-simple/whisper-cpp/resolved/clean_dialogue.json",
        {"schema": "murmurmark.clean_dialogue/v1", "session": session_id, "utterances": utterances},
    )
    write_json(session / "embedding_fixture.json", {"schema": FIXTURE_SCHEMA, "embeddings": primary})
    write_json(
        session / "consensus_embedding_fixture.json",
        {"schema": FIXTURE_SCHEMA, "embeddings": consensus},
    )
    write_json(
        session / "derived/transcript-rich/speaker-roster-v1.json",
        {
            "schema": "murmurmark.remote_speaker_roster/v1",
            "session_id": session_id,
            "source": "synthetic_test",
            "expected_remote_speakers": 4,
            "remote_participants": [],
            "voice_identity_mapping": "not_asserted",
        },
    )
    return session


def build_roster_short_speaker_session(root: Path, session_id: str) -> Path:
    session = root / session_id
    rate = 16_000
    counts = [12, 12, 8]
    centers = np.eye(4, dtype=np.float32)[:3]
    utterances: list[dict[str, Any]] = []
    primary: dict[str, list[float]] = {}
    consensus: dict[str, list[float]] = {}
    samples: list[np.ndarray] = []
    current = 0.0
    emitted = [0, 0, 0]
    while emitted != counts:
        for speaker in range(3):
            if emitted[speaker] >= counts[speaker]:
                continue
            utterance_id = f"utt_{len(utterances) + 1:06d}"
            utterances.append(
                {
                    "id": utterance_id,
                    "role": "remote",
                    "speaker_label": "Colleagues",
                    "start": current,
                    "end": current + 6.0,
                    "source_start": current,
                    "source_end": current + 6.0,
                    "source_track": "remote",
                    "text": f"Содержательная проверочная фраза {speaker} {emitted[speaker]}",
                    "quality": {"needs_review": False},
                }
            )
            primary[utterance_id] = centers[speaker].tolist()
            consensus[utterance_id] = centers[speaker].tolist()
            tone = 0.02 * np.sin(
                2 * np.pi * (180 + speaker * 80) * np.arange(6 * rate) / rate
            )
            samples.extend([tone.astype(np.float32), np.zeros(int(0.5 * rate), dtype=np.float32)])
            current += 6.5
            emitted[speaker] += 1

    raw = session / "audio/remote/000001.caf"
    raw.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(raw), np.concatenate(samples), rate, format="CAF", subtype="FLOAT")
    write_json(session / "session.json", {"schema": "murmurmark.session/v1", "session_id": session_id})
    write_json(
        session / "derived/transcript-simple/whisper-cpp/resolved/clean_dialogue.json",
        {"schema": "murmurmark.clean_dialogue/v1", "session": session_id, "utterances": utterances},
    )
    write_json(session / "embedding_fixture.json", {"schema": FIXTURE_SCHEMA, "embeddings": primary})
    write_json(
        session / "consensus_embedding_fixture.json",
        {"schema": FIXTURE_SCHEMA, "embeddings": consensus},
    )
    write_json(
        session / "derived/transcript-rich/speaker-roster-v1.json",
        {
            "schema": "murmurmark.remote_speaker_roster/v1",
            "session_id": session_id,
            "source": "synthetic_test",
            "expected_remote_speakers": 3,
            "remote_participants": [],
            "voice_identity_mapping": "not_asserted",
        },
    )
    return session


def run_audit(
    session: Path,
    *,
    missing_model: bool = False,
    extra_args: list[str] | None = None,
) -> dict[str, Any]:
    command = [sys.executable, str(AUDIT), str(session), "--no-progress"]
    if missing_model:
        command += ["--model-path", str(session / "missing-model.pt")]
    else:
        command += ["--embedding-fixture", str(session / "embedding_fixture.json")]
    command += extra_args or []
    invoke(command)
    return json.loads((session / "derived/audit/remote-speaker-evidence-v1/report.json").read_text())


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="murmurmark-remote-speakers-") as temporary:
        root = Path(temporary)
        configured = root / "configured-roster"
        invoke(
            [
                sys.executable,
                str(ROSTER),
                str(configured),
                "--expected-remote-speakers",
                "2",
                "--participant",
                "Participant A",
                "--participant",
                "Participant B",
            ]
        )
        invoke([sys.executable, str(ROSTER), str(configured), "--status"])
        configured_payload = json.loads(
            (configured / "derived/transcript-rich/speaker-roster-v1.json").read_text()
        )
        assert configured_payload["expected_remote_speakers"] == 2, configured_payload
        assert configured_payload["voice_identity_mapping"] == "not_asserted", configured_payload
        specifications = [
            ("one-a", 1, 20),
            ("one-b", 1, 20),
            ("group-a", 2, 20),
            ("group-b", 2, 20),
            ("group-reference", 3, 20),
            ("group-c", 2, 20),
        ]
        sessions: list[Path] = []
        references: dict[str, dict[str, str]] = {}
        for session_id, speakers, units in specifications:
            session, reference = build_session(root, session_id, speakers, units)
            if session_id == "group-a":
                dialogue_path = session / "derived/transcript-simple/whisper-cpp/resolved/clean_dialogue.json"
                dialogue = json.loads(dialogue_path.read_text())
                dialogue["utterances"][1]["start"] = 2.0
                dialogue["utterances"][1]["source_start"] = 2.0
                write_json(dialogue_path, dialogue)
            sessions.append(session)
            references[session_id] = reference
            before = hash_file(session / "audio/remote/000001.caf")
            report = run_audit(session)
            assert report["status"] == "completed", report
            assert report["decision"] == "PUBLISH_AUDIT_EVIDENCE", report
            assert report["summary"]["published_speakers"] == speakers, report
            assert report["stability"]["reverse_order_ari"] == 1.0, report
            assert report["stability"]["chunk_replay_ari"] == 1.0, report
            assert report["safety"]["selected_dialogue_unchanged"] is True, report
            assert report["safety"]["raw_remote_unchanged"] is True, report
            assert hash_file(session / "audio/remote/000001.caf") == before
            if session_id == "group-a":
                attributions = [
                    json.loads(line)
                    for line in (
                        session / "derived/audit/remote-speaker-evidence-v1/utterance_attribution.jsonl"
                    ).read_text().splitlines()
                ]
                assert attributions[0]["reason"] == "possible_remote_double_talk", attributions[:2]
                assert attributions[1]["reason"] == "possible_remote_double_talk", attributions[:2]

            artifact_manifest = session / "derived/audit/remote-speaker-evidence-v1/artifact_manifest.json"
            first_hash = hash_file(artifact_manifest)
            repeated = run_audit(session)
            assert repeated["summary"] == report["summary"], repeated
            assert hash_file(artifact_manifest) == first_hash
            source = json.loads(
                (session / "derived/transcript-simple/whisper-cpp/resolved/clean_dialogue.json").read_text()
            )
            rich = json.loads(
                (session / "derived/audit/remote-speaker-evidence-v1/transcript.rich.shadow.json").read_text()
            )
            assert rich["utterances"] == source["utterances"]

        fallback, _reference = build_session(root, "missing-model", 1, 20)
        fallback_report = run_audit(fallback, missing_model=True)
        assert fallback_report["status"] == "fallback_aggregate", fallback_report
        assert fallback_report["decision"] == "DO_NOT_PUBLISH", fallback_report
        assert fallback_report["summary"]["published_speakers"] == 0, fallback_report
        fallback_rich = json.loads(
            (fallback / "derived/audit/remote-speaker-evidence-v1/transcript.rich.shadow.json").read_text()
        )
        assert all(row["speaker_id"] is None for row in fallback_rich["remote_speaker_attributions"])

        failed_gate, _reference = build_session(root, "failed-publish-gate", 1, 20)
        failed_report = run_audit(
            failed_gate,
            extra_args=["--min-cluster-units", "100"],
        )
        assert failed_report["status"] == "completed_fail_open", failed_report
        assert failed_report["decision"] == "DO_NOT_PUBLISH", failed_report
        assert failed_report["summary"]["published_speakers"] == 0, failed_report
        assert all(row["speaker_id"] is None for row in failed_report["clusters"]), failed_report
        failed_map = json.loads(
            (failed_gate / "derived/audit/remote-speaker-evidence-v1/speaker_map.json").read_text()
        )
        assert failed_map["speakers"] == [], failed_map

        roster_merge = build_roster_merge_session(root, "roster-merge", ambiguous=False)
        roster_report = run_audit(
            roster_merge,
            extra_args=[
                "--consensus-embedding-fixture",
                str(roster_merge / "consensus_embedding_fixture.json"),
                "--cluster-distance",
                "0.05",
                "--roster-max-cluster-distance",
                "0.05",
            ],
        )
        assert roster_report["decision"] == "PUBLISH_AUDIT_EVIDENCE", roster_report
        assert roster_report["summary"]["published_speakers"] == 4, roster_report
        assert roster_report["speaker_roster"]["consensus"]["status"] == "applied", roster_report
        assert roster_report["speaker_roster"]["identity_mapping_applied"] is False, roster_report

        roster_short = build_roster_short_speaker_session(root, "roster-short-speaker")
        short_report = run_audit(
            roster_short,
            extra_args=[
                "--consensus-embedding-fixture",
                str(roster_short / "consensus_embedding_fixture.json"),
            ],
        )
        assert short_report["decision"] == "PUBLISH_AUDIT_EVIDENCE", short_report
        assert short_report["summary"]["published_speakers"] == 3, short_report
        assert (
            short_report["speaker_roster"]["consensus"]["status"]
            == "applied_minor_promotion"
        ), short_report
        assert short_report["speaker_roster"]["identity_mapping_applied"] is False, short_report

        roster_ambiguous = build_roster_merge_session(root, "roster-ambiguous", ambiguous=True)
        ambiguous_report = run_audit(
            roster_ambiguous,
            extra_args=[
                "--consensus-embedding-fixture",
                str(roster_ambiguous / "consensus_embedding_fixture.json"),
                "--cluster-distance",
                "0.05",
                "--roster-max-cluster-distance",
                "0.05",
            ],
        )
        assert ambiguous_report["decision"] == "DO_NOT_PUBLISH", ambiguous_report
        assert ambiguous_report["summary"]["published_speakers"] == 0, ambiguous_report
        assert (
            ambiguous_report["speaker_roster"]["consensus"]["reason"]
            == "roster_consensus_merge_gates_failed"
        ), ambiguous_report

        reference_session = next(path for path in sessions if path.name == "group-reference")
        dialogue = json.loads(
            (reference_session / "derived/transcript-simple/whisper-cpp/resolved/clean_dialogue.json").read_text()
        )
        reference_path = root / "reference.txt"
        lines = ["Тестовая расшифровка"]
        for utterance in dialogue["utterances"]:
            seconds = int(utterance["start"])
            hour, remainder = divmod(seconds, 3600)
            minute, second = divmod(remainder, 60)
            speaker = references["group-reference"][utterance["id"]]
            lines.append(f"{hour:02d}:{minute:02d}:{second:02d}\t{speaker}\t{utterance['text']}")
        reference_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        manifest = root / "manifest.json"
        output = root / "corpus"
        command = [
            sys.executable,
            str(CORPUS),
            *map(str, sessions),
            "--output",
            str(output),
            "--frozen-manifest",
            str(manifest),
            "--refresh-manifest",
            "--reference-transcript",
            str(reference_path),
            "--reference-session",
            "group-reference",
            "--reference-local-speaker",
            "Local Person",
        ]
        for session_id, speakers, _units in specifications:
            command += ["--expect", f"{session_id}={speakers}:{speakers}"]
        invoke(command)
        corpus = json.loads((output / "remote_speaker_evidence_corpus_report.json").read_text())
        assert corpus["decision"] == "PROMOTE_AUDIT_ONLY", corpus
        assert all(corpus["gates"].values()), corpus
        assert corpus["reference_evaluation"]["aligned_rows"] == 60, corpus
        assert corpus["reference_evaluation"]["adjusted_rand_index"] == 1.0, corpus
        assert corpus["reference_evaluation"]["bcubed"]["f1"] == 1.0, corpus

        command.remove("--refresh-manifest")
        invoke(command)
        repeated_corpus = json.loads((output / "remote_speaker_evidence_corpus_report.json").read_text())
        assert repeated_corpus["decision"] == "PROMOTE_AUDIT_ONLY", repeated_corpus
        assert repeated_corpus["gates"]["frozen_inputs_match"] is True, repeated_corpus

        stale_dialogue = sessions[0] / "derived/transcript-simple/whisper-cpp/resolved/clean_dialogue.json"
        original_dialogue = stale_dialogue.read_bytes()
        stale_dialogue.write_bytes(original_dialogue + b" ")
        invoke(command)
        stale_corpus = json.loads((output / "remote_speaker_evidence_corpus_report.json").read_text())
        assert stale_corpus["decision"] == "DO_NOT_PROMOTE", stale_corpus
        assert stale_corpus["gates"]["frozen_inputs_match"] is False, stale_corpus
        stale_dialogue.write_bytes(original_dialogue)

    print("remote speaker evidence checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
