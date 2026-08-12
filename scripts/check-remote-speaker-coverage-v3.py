#!/usr/bin/env python3
"""Regression checks for bounded Remote Speaker Coverage v3."""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv/bin/python"
AUDIT = ROOT / "scripts/audit-remote-speaker-coverage-v3.py"
CORPUS = ROOT / "scripts/report-remote-speaker-coverage-v3-corpus.py"
SELECTOR = ROOT / "scripts/select-speaker-resolved-transcript.py"
V2_AUDIT = ROOT / "scripts/audit-remote-speaker-diarization.py"
V3_DIR = Path("derived/audit/remote-speaker-coverage-v3")
CLI = ROOT / ".build/debug/murmurmark"


def canonical(payload: Any) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical(payload))


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fp(path: Path, session: Path | None = None) -> dict[str, Any]:
    return {
        "path": str(path.relative_to(session)) if session else str(path.resolve()),
        "exists": True,
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
    }


def run(args: list[str], expected: int = 0) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        [str(PYTHON), *args], cwd=ROOT, text=True, capture_output=True, check=False
    )
    if completed.returncode != expected:
        raise AssertionError(
            f"unexpected exit {completed.returncode}, expected {expected}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return completed


def turns(text: str, words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    runs: list[tuple[int, int, str | None]] = []
    left = 0
    current = words[0].get("speaker_id")
    for index, word in enumerate(words[1:], start=1):
        if word.get("speaker_id") != current:
            runs.append((left, index, current))
            left = index
            current = word.get("speaker_id")
    runs.append((left, len(words), current))
    result = []
    char_start = 0
    for left, right, speaker in runs:
        char_end = words[right]["start_char"] if right < len(words) else len(text)
        result.append(
            {
                "speaker_id": speaker,
                "speaker_label": speaker or "Colleagues",
                "status": "attributed" if speaker else "unknown",
                "start": words[left]["start"],
                "end": words[right - 1]["end"],
                "start_char": char_start,
                "end_char": char_end,
                "text": text[char_start:char_end],
                "word_ids": [word["word_id"] for word in words[left:right]],
            }
        )
        char_start = char_end
    return result


def build_v2_fixture(session: Path) -> Path:
    session.mkdir(parents=True)
    dialogue_path = session / "dialogue.json"
    remote_path = session / "remote.wav"
    raw_path = session / "raw_remote.json"
    v1_report_path = session / "v1_report.json"
    v1_rows_path = session / "v1_rows.jsonl"
    text = "Alpha beta gamma delta"
    dialogue = {
        "utterances": [
            {"id": "me_1", "role": "mic", "speaker_label": "Me", "start": 0.0, "end": 0.5, "text": "Hi"},
            {"id": "remote_1", "role": "remote", "speaker_label": "Colleagues", "start": 0.0, "end": 4.0, "text": text},
        ]
    }
    write_json(dialogue_path, dialogue)
    remote_path.write_bytes(b"RIFF-fixture")
    write_json(raw_path, {"transcription": []})
    write_json(v1_report_path, {"schema": "fixture"})
    write_jsonl(v1_rows_path, [{"schema": "fixture"}])
    source = {
        "session_id": session.name,
        "profile": "fixture",
        "dialogue": fp(dialogue_path, session),
        "remote_audio": fp(remote_path, session),
        "raw_remote_json": fp(raw_path, session),
        "v1_report": fp(v1_report_path, session),
        "v1_attribution": fp(v1_rows_path, session),
    }
    out = session / "derived/audit/remote-speaker-diarization-v2"
    word_specs = [
        ("Alpha", 0, 5, 0.0, 1.0, "remote_speaker_01", "frame_consensus"),
        ("beta", 6, 10, 1.0, 2.0, None, "weak_or_ambiguous_word_evidence"),
        ("gamma", 11, 16, 2.0, 3.0, None, "possible_remote_overlap"),
        ("delta", 17, 22, 3.0, 4.0, None, "weak_or_ambiguous_word_evidence"),
    ]
    words = []
    for index, (value, start_char, end_char, start, end, speaker, reason) in enumerate(word_specs, start=1):
        words.append(
            {
                "schema": "murmurmark.remote_speaker_word/v2",
                "word_id": f"remote_1:word:{index:04d}",
                "utterance_id": "remote_1",
                "text": value,
                "normalized": value.lower(),
                "start_char": start_char,
                "end_char": end_char,
                "start": start,
                "end": end,
                "coverage_weight_sec": 1.0,
                "timing_source": "fixture",
                "speaker_id": speaker,
                "speaker_label": speaker or "Colleagues",
                "status": "attributed" if speaker else "unknown",
                "reason": reason,
                "confidence": {"similarity": 0.8 if speaker else None, "margin": 0.2 if speaker else None},
                "frame_ids": ["frame_1"] if speaker else [],
            }
        )
    frames = [
        {
            "schema": "murmurmark.remote_speaker_frame/v2", "frame_id": "frame_1",
            "utterance_id": "remote_1", "start": 0.0, "end": 2.0,
            "speaker_id": "remote_speaker_01", "speaker_label": "remote_speaker_01",
            "status": "attributed", "reason": "seed_centroid_match",
            "confidence": {"similarity": 0.8, "margin": 0.2, "speaker_scores": {"remote_speaker_01": 0.8, "remote_speaker_02": 0.6}},
        },
        {
            "schema": "murmurmark.remote_speaker_frame/v2", "frame_id": "frame_2",
            "utterance_id": "remote_1", "start": 1.0, "end": 2.0,
            "speaker_id": None, "speaker_label": "Colleagues", "status": "unknown",
            "reason": "weak_or_ambiguous_frame",
            "confidence": {"similarity": 0.68, "margin": 0.01, "speaker_scores": {"remote_speaker_01": 0.68, "remote_speaker_02": 0.67}},
        },
        {
            "schema": "murmurmark.remote_speaker_frame/v2", "frame_id": "frame_3",
            "utterance_id": "remote_1", "start": 2.0, "end": 3.0,
            "speaker_id": None, "speaker_label": "Colleagues", "status": "unknown",
            "reason": "weak_or_ambiguous_frame",
            "confidence": {"similarity": 0.69, "margin": 0.01, "speaker_scores": {"remote_speaker_01": 0.69, "remote_speaker_02": 0.68}},
        },
        {
            "schema": "murmurmark.remote_speaker_frame/v2", "frame_id": "frame_4",
            "utterance_id": "remote_1", "start": 3.0, "end": 4.0,
            "speaker_id": None, "speaker_label": "Colleagues", "status": "unknown",
            "reason": "weak_or_ambiguous_frame",
            "confidence": {"similarity": 0.65, "margin": 0.02, "speaker_scores": {"remote_speaker_01": 0.65, "remote_speaker_02": 0.63}},
        },
    ]
    base_turns = turns(text, words)
    rich_rows = deepcopy(dialogue["utterances"])
    rich_rows[1]["speaker_turns"] = base_turns
    utterance_rows = [
        {
            "schema": "murmurmark.remote_speaker_utterance/v2", "utterance_id": "remote_1",
            "start": 0.0, "end": 4.0, "speaker_id": None, "speaker_label": "Colleagues",
            "status": "partial", "reason": "insufficient_word_level_evidence",
            "speaker_turns": base_turns, "attributed_weight_sec": 1.0, "total_weight_sec": 4.0,
        }
    ]
    speakers = [
        {"speaker_id": "remote_speaker_01", "speaker_label": "remote_speaker_01", "session_local": True, "display_name": None, "seed_units": 5, "attributed_speech_sec": 1.0},
        {"speaker_id": "remote_speaker_02", "speaker_label": "remote_speaker_02", "session_local": True, "display_name": None, "seed_units": 5, "attributed_speech_sec": 0.0},
    ]
    report = {
        "schema": "murmurmark.remote_speaker_diarization_report/v2", "version": "0.2.0",
        "status": "completed", "decision": "PUBLISH_EVIDENCE", "reasons": [], "source": source,
        "implementation": {"script": fp(V2_AUDIT), "version": "0.2.0"},
        "summary": {"remote_words": 4, "attributed_words": 1, "remote_speech_sec": 4.0, "attributed_speech_sec": 1.0, "published_speakers": 2, "internal_change_utterances": 0},
        "gates": {"word_conservation": True, "timestamp_order": True, "publish_session_evidence": True},
        "safety": {"raw_audio_unchanged": True},
    }
    write_jsonl(out / "frame_attribution.jsonl", frames)
    write_jsonl(out / "word_attribution.jsonl", words)
    write_jsonl(out / "utterance_attribution.jsonl", utterance_rows)
    write_json(out / "speaker_map.json", {"schema": "murmurmark.remote_speaker_map/v2", "speakers": speakers})
    write_json(out / "transcript.rich.shadow.json", {"schema": "murmurmark.remote_speaker_rich_transcript/v2", "utterances": rich_rows})
    (out / "transcript.rich.shadow.md").write_text("fixture\n", encoding="utf-8")
    write_json(out / "report.json", report)
    (out / "report.md").write_text("fixture\n", encoding="utf-8")
    names = ["frame_attribution.jsonl", "word_attribution.jsonl", "utterance_attribution.jsonl", "speaker_map.json", "transcript.rich.shadow.json", "transcript.rich.shadow.md", "report.json", "report.md"]
    write_json(
        out / "artifact_manifest.json",
        {"schema": "murmurmark.remote_speaker_diarization_artifact_manifest/v2", "session_id": session.name, "artifacts": {name: sha256(out / name) for name in names}},
    )
    return out


def check_auditor(root: Path) -> None:
    session = root / "audit-session"
    v2 = build_v2_fixture(session)
    write_json(
        session / "session.json",
        {
            "schema": "murmurmark.session/v1",
            "session_id": session.name,
            "status": "completed",
        },
    )
    dialogue_hash = sha256(session / "dialogue.json")
    run([str(AUDIT), str(session)])
    out = session / V3_DIR
    report = read_json(out / "report.json")
    assert report["decision"] == "PUBLISH_EVIDENCE"
    assert report["summary"]["recovered_words"] == 1
    assert report["summary"]["recovered_seconds"] == 1.0
    words = {row["word_id"]: row for row in read_jsonl(out / "word_attribution.jsonl")}
    assert words["remote_1:word:0001"]["speaker_id"] == "remote_speaker_01"
    assert words["remote_1:word:0002"]["speaker_id"] == "remote_speaker_01"
    assert words["remote_1:word:0003"]["speaker_id"] is None
    assert words["remote_1:word:0003"]["v3_reason"] == "protected_remote_overlap"
    assert words["remote_1:word:0004"]["speaker_id"] is None
    assert sha256(session / "dialogue.json") == dialogue_hash
    run([str(AUDIT), str(session), "--verify-only"])
    run([str(AUDIT), str(session), "--verify-only", "--require-promoted"])
    aggregate = session / "derived/transcript-simple/whisper-cpp/resolved/transcript.fixture.md"
    aggregate.parent.mkdir(parents=True, exist_ok=True)
    aggregate.write_text("# Aggregate fixture\n\n## 00:00 Colleagues\n\nAlpha beta gamma delta\n", encoding="utf-8")
    write_json(
        session / "derived/readiness/session_readiness.json",
        {
            "schema": "murmurmark.session_readiness/v1",
            "selected_profile": "fixture",
            "outputs": {
                "transcript": {"path": str(aggregate.relative_to(session)), "exists": True},
                "clean_dialogue": {"path": "dialogue.json", "exists": True},
            },
        },
    )
    selected = run([str(SELECTOR), str(session), "--require-speaker-resolved"])
    assert "state=selected" in selected.stdout
    selection_path = session / "derived/transcript-rich/speaker-resolved-default-v1/selection.json"
    selection_first = selection_path.read_bytes()
    run([str(SELECTOR), str(session), "--require-speaker-resolved"])
    assert selection_path.read_bytes() == selection_first
    selection = read_json(selection_path)
    assert selection["selected_speaker_profile"] == "remote_speaker_coverage_v3"
    assert selection["selected_transcript"]["sha256"] == sha256(out / "transcript.rich.shadow.md")
    assert selection["policy"]["path"] == "policies/speaker-resolved-transcript-default-v1.json"
    write_json(
        session / "derived/transcript-rich/speaker-roster-v1.json",
        {
            "schema": "murmurmark.remote_speaker_roster/v1",
            "session_id": session.name,
            "source": "synthetic_test",
            "expected_remote_speakers": 2,
            "remote_participants": [],
            "voice_identity_mapping": "not_asserted",
        },
    )
    run([str(SELECTOR), str(session)])
    roster_selection = read_json(selection_path)
    assert roster_selection["speaker_roster"]["exists"] is True, roster_selection
    assert roster_selection["semantic_fingerprint"] != selection["semantic_fingerprint"]
    assert roster_selection["state"] == "fallback", roster_selection
    assert str(roster_selection["fallback_reason"]).startswith(
        ("evidence_refresh_stage_", "refreshed_v3_invalid:")
    ), roster_selection
    (session / "derived/transcript-rich/speaker-roster-v1.json").unlink()
    run([str(SELECTOR), str(session), "--require-speaker-resolved"])

    policy = read_json(ROOT / "policies/speaker-resolved-transcript-default-v1.json")
    policy["state"] = "development"
    stale_policy = root / "stale-selector-policy.json"
    write_json(stale_policy, policy)
    policy_fallback_dir = session / "derived/transcript-rich/selector-policy-fallback"
    run(
        [
            str(SELECTOR),
            str(session),
            "--policy",
            str(stale_policy),
            "--out-dir",
            str(policy_fallback_dir),
        ]
    )
    policy_fallback = read_json(policy_fallback_dir / "selection.json")
    assert policy_fallback["state"] == "fallback"
    assert policy_fallback["fallback_reason"] == "selector_policy_not_promoted"
    assert policy_fallback["selected_transcript"]["sha256"] == sha256(aggregate)

    missing_fallback_dir = session / "derived/transcript-rich/selector-missing-fallback"
    run(
        [
            str(SELECTOR),
            str(session),
            "--coverage-dir",
            str(session / "missing-v3"),
            "--out-dir",
            str(missing_fallback_dir),
        ]
    )
    missing_fallback = read_json(missing_fallback_dir / "selection.json")
    assert missing_fallback["state"] == "fallback"
    assert missing_fallback["selected_transcript"]["sha256"] == sha256(aggregate)
    runtime_fallback_dir = session / "derived/transcript-rich/selector-runtime-fallback"
    run(
        [
            str(SELECTOR),
            str(session),
            "--coverage-dir",
            str(session / "missing-v3"),
            "--out-dir",
            str(runtime_fallback_dir),
            "--refresh-evidence",
        ]
    )
    runtime_fallback = read_json(runtime_fallback_dir / "selection.json")
    assert runtime_fallback["state"] == "fallback"
    runtime_reason = str(runtime_fallback["fallback_reason"])
    assert runtime_reason.startswith(("evidence_refresh_stage_", "refreshed_v3_invalid:")), runtime_reason
    assert runtime_fallback["selected_transcript"]["sha256"] == sha256(aggregate)
    write_json(
        session / "derived/synthesis-simple/extractive/quality_verdict.json",
        {"selected_transcript_profile": "fixture"},
    )
    cli = subprocess.run(
        [str(CLI), "transcript", str(session), "--rich", "--path-only"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert cli.returncode == 0, cli.stderr
    assert "remote-speaker-coverage-v3/transcript.rich.shadow.md" in cli.stdout, cli.stdout
    default_cli = subprocess.run(
        [str(CLI), "transcript", str(session), "--path-only"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert default_cli.returncode == 0, default_cli.stderr
    assert "remote-speaker-coverage-v3/transcript.rich.shadow.md" in default_cli.stdout
    replay = session / "derived/audit/remote-speaker-coverage-v3-replay"
    run([str(AUDIT), str(session), "--out-dir", str(replay)])
    for name in ("report.json", "word_attribution.jsonl", "recovery_decisions.jsonl", "transcript.rich.shadow.json"):
        assert (out / name).read_bytes() == (replay / name).read_bytes()
    (v2 / "frame_attribution.jsonl").write_text(
        (v2 / "frame_attribution.jsonl").read_text(encoding="utf-8") + "\n", encoding="utf-8"
    )
    fallback = session / "derived/audit/remote-speaker-coverage-v3-fallback"
    run([str(AUDIT), str(session), "--out-dir", str(fallback)])
    assert read_json(fallback / "report.json")["decision"] == "FALLBACK_V2"
    fallback_selection_dir = session / "derived/transcript-rich/selector-evidence-fallback"
    run(
        [
            str(SELECTOR),
            str(session),
            "--coverage-dir",
            str(fallback),
            "--out-dir",
            str(fallback_selection_dir),
        ]
    )
    evidence_fallback = read_json(fallback_selection_dir / "selection.json")
    assert evidence_fallback["state"] == "fallback"
    reason = str(evidence_fallback["fallback_reason"])
    assert reason.startswith("coverage_not_publishable:"), reason
    assert "coverage_artifact_missing" not in reason, reason
    run([str(SELECTOR), str(session)])
    stale_selection = read_json(selection_path)
    assert stale_selection["state"] == "fallback"
    assert stale_selection["selected_transcript"]["sha256"] == sha256(aggregate)
    fallback_cli = subprocess.run(
        [str(CLI), "transcript", str(session), "--path-only"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert fallback_cli.returncode == 0, fallback_cli.stderr
    assert fallback_cli.stdout.strip().endswith("transcript.fixture.md")


def corpus_words(uid: str, start: float, speakers: list[str | None]) -> tuple[str, list[dict[str, Any]]]:
    tokens = ["one", "two", "three", "four", "five"]
    text = " ".join(tokens)
    rows = []
    cursor = 0
    for index, (token, speaker) in enumerate(zip(tokens, speakers), start=1):
        rows.append(
            {
                "schema": "murmurmark.remote_speaker_word/v3", "word_id": f"{uid}:word:{index:04d}",
                "utterance_id": uid, "text": token, "normalized": token,
                "start_char": cursor, "end_char": cursor + len(token), "start": start + index - 1,
                "end": start + index, "coverage_weight_sec": 1.0, "speaker_id": speaker,
                "speaker_label": speaker or "Colleagues", "status": "attributed" if speaker else "unknown",
            }
        )
        cursor += len(token) + 1
    return text, rows


def build_corpus_session(session: Path, utterance_count: int, group: bool) -> list[dict[str, Any]]:
    out = session / V3_DIR
    dialogue_rows = []
    rich_rows = []
    words: list[dict[str, Any]] = []
    attributions = []
    references = []
    speaker_weights: dict[str, float] = defaultdict(float)
    internal = 0
    for index in range(utterance_count):
        uid = f"remote_{index:04d}"
        start = float(index * 6)
        dominant = "remote_speaker_01" if not group or index % 2 == 0 else "remote_speaker_02"
        labels: list[str | None] = [dominant, dominant, dominant, dominant, None]
        if group and index == 0:
            labels = ["remote_speaker_01", "remote_speaker_01", "remote_speaker_02", "remote_speaker_02", None]
            internal += 1
        text, utterance_words = corpus_words(uid, start, labels)
        row = {"id": uid, "role": "remote", "speaker_label": "Colleagues", "start": start, "end": start + 5, "text": text}
        rich_row = deepcopy(row)
        rich_row["speaker_turns"] = turns(text, utterance_words)
        dialogue_rows.append(row)
        rich_rows.append(rich_row)
        words.extend(utterance_words)
        weights: dict[str, float] = defaultdict(float)
        for word in utterance_words:
            if word["speaker_id"]:
                weights[str(word["speaker_id"])] += 1.0
                speaker_weights[str(word["speaker_id"])] += 1.0
        speaker = next(iter(weights)) if len(weights) == 1 and sum(weights.values()) / 5 >= 0.8 else None
        attributions.append(
            {"schema": "murmurmark.remote_speaker_utterance/v3", "utterance_id": uid,
             "start": start, "end": start + 5, "speaker_id": speaker,
             "speaker_label": speaker or "Colleagues", "status": "attributed" if speaker else "mixed",
             "speaker_turns": rich_row["speaker_turns"], "attributed_weight_sec": 4.0, "total_weight_sec": 5.0}
        )
        references.append({"utterance_id": uid, "reference_speaker": "reference_01" if dominant == "remote_speaker_01" else "reference_02"})
    write_json(session / "dialogue.json", {"utterances": dialogue_rows})
    speaker_rows = [
        {"speaker_id": speaker, "attributed_speech_sec": seconds, "seed_units": 5}
        for speaker, seconds in sorted(speaker_weights.items())
    ]
    summary = {
        "remote_words": len(words), "attributed_words": sum(row["speaker_id"] is not None for row in words),
        "remote_speech_sec": float(len(words)), "attributed_speech_sec": float(sum(row["speaker_id"] is not None for row in words)),
        "attributable_remote_speech_ratio": round(sum(row["speaker_id"] is not None for row in words) / len(words), 6),
        "published_speakers": len(speaker_rows), "internal_change_utterances": internal,
    }
    report = {
        "schema": "murmurmark.remote_speaker_coverage_report/v3", "decision": "PUBLISH_EVIDENCE",
        "source": {"dialogue": fp(session / "dialogue.json", session)}, "summary": summary,
        "gates": {"publish_session_evidence": True, "word_conservation": True, "timestamp_order": True,
                  "baseline_attributions_preserved": True, "remote_overlap_preserved": True},
        "safety": {"raw_audio_unchanged": True},
    }
    write_jsonl(out / "word_attribution.jsonl", words)
    write_jsonl(out / "utterance_attribution.jsonl", attributions)
    write_json(out / "speaker_map.json", {"speakers": speaker_rows})
    write_json(out / "transcript.rich.shadow.json", {"utterances": rich_rows})
    write_json(out / "report.json", report)
    write_json(out / "artifact_manifest.json", {"schema": "fixture", "artifacts": {}})
    write_json(
        out / "unknown_cause_map.json",
        {"baseline_unknown_words": len(words) * 2 // 5, "baseline_unknown_seconds": len(words) * 2 / 5,
         "causes": [{"cause": "recovered_bounded_seed_consensus", "words": len(words) // 5, "seconds": len(words) / 5},
                    {"cause": "similarity_below_threshold", "words": len(words) // 5, "seconds": len(words) / 5}]},
    )
    return references


def check_corpus(root: Path) -> None:
    sessions_root = root / "sessions"
    session_rows = []
    all_references = []
    for index in range(6):
        sid = f"fixture-{index + 1}"
        group = index == 0
        count = 60 if group else 1
        refs = build_corpus_session(sessions_root / sid, count, group)
        if group:
            all_references = refs
        session_rows.append(
            {"session_id": sid, "expected_speakers": {"min": 2 if group else 1, "max": 2 if group else 1}}
        )
    baseline_manifest = root / "baseline-manifest.json"
    baseline_report = root / "baseline-report.json"
    baseline_reference = root / "baseline-reference.json"
    boundaries = root / "boundaries.json"
    total_words = 65 * 5
    write_json(baseline_manifest, {"decision": "PROMOTE", "sessions": session_rows})
    write_json(
        baseline_report,
        {"decision": "PROMOTE", "summary": {"remote_words": total_words, "attributed_words": 200,
         "remote_speech_sec": float(total_words), "attributed_speech_sec": 200.0}},
    )
    write_json(
        baseline_reference,
        {"session_id": "fixture-1", "rows": all_references},
    )
    first_text = "one two three four five"
    write_json(
        boundaries,
        {"schema": "murmurmark.remote_speaker_boundary_cases/v2", "cases": [
            {"session_id": "fixture-1", "utterance_id": "remote_0000", "selected_text": first_text,
             "min_supported_runs": 2, "min_distinct_speakers": 2}
        ]},
    )
    output = root / "report"
    frozen = root / "frozen.json"
    common = [
        str(CORPUS), "all", "--sessions-root", str(sessions_root),
        "--baseline-manifest", str(baseline_manifest), "--baseline-report", str(baseline_report),
        "--baseline-reference", str(baseline_reference), "--boundary-cases", str(boundaries),
        "--output", str(output),
    ]
    run([*common, "--write-manifest", str(frozen)])
    run([*common, "--frozen-manifest", str(frozen)])
    run([*common, "--frozen-manifest", str(frozen), "--verify-existing"])
    report = read_json(output / "remote_speaker_coverage_corpus_report.json")
    assert report["decision"] == "PROMOTE"
    assert report["summary"]["unknown_words_reduction_ratio"] >= 0.25
    assert report["summary"]["unknown_seconds_reduction_ratio"] >= 0.25
    assert report["reference_evaluation"]["attributed_only"]["bcubed"]["f1"] == 1.0
    bad = read_json(sessions_root / "fixture-2" / V3_DIR / "report.json")
    bad["gates"]["baseline_attributions_preserved"] = False
    write_json(sessions_root / "fixture-2" / V3_DIR / "report.json", bad)
    run([*common, "--frozen-manifest", str(frozen)], expected=2)
    assert read_json(output / "remote_speaker_coverage_corpus_report.json")["decision"] == "DO_NOT_PROMOTE"


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="murmurmark-remote-coverage-v3-") as temporary:
        root = Path(temporary)
        check_auditor(root)
        check_corpus(root)
    print("remote speaker coverage v3 checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
