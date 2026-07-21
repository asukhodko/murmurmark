#!/usr/bin/env python3
"""Validate the frozen Evidence-Backed Me Completion v2 control corpus."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SESSIONS = ROOT / "sessions"
REPORT_DIR = SESSIONS / "_reports/local-speech-completion-v2"
PROFILE = "local_speech_completion_v2"
EXPECTED_SESSIONS = {
    "2026-07-21_14-27-53-live",
    "2026-07-21_15-11-15-live",
}


def load_completion():
    path = Path(__file__).with_name("local-speech-completion-v2.py")
    spec = importlib.util.spec_from_file_location("murmurmark_local_speech_completion_corpus_check", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_synthesis():
    path = Path(__file__).with_name("synthesize-simple-extractive.py")
    spec = importlib.util.spec_from_file_location("murmurmark_local_speech_completion_synthesis_check", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict), path
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def suffix(profile: str) -> str:
    return "" if profile == "current" else f".{profile}"


def role(row: dict[str, Any]) -> str:
    value = str(row.get("role") or row.get("speaker_label") or "").lower()
    source = str(row.get("source_track") or "").lower()
    if value == "me" or source == "mic":
        return "Me"
    if value in {"colleagues", "remote"} or source == "remote":
        return "Colleagues"
    return value


def collect_evidence_ids(value: Any) -> set[str]:
    result: set[str] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            if key in {
                "evidence_utterance_ids",
                "context_utterance_ids",
                "representative_utterance_ids",
                "utterance_ids",
            } and isinstance(nested, list):
                result.update(str(item) for item in nested if item)
            result.update(collect_evidence_ids(nested))
    elif isinstance(value, list):
        for nested in value:
            result.update(collect_evidence_ids(nested))
    return result


def verdict_rank(value: str) -> int:
    return {"failed": 0, "risky": 1, "usable_with_review": 2, "good": 3}.get(value, -1)


def main() -> int:
    report_path = REPORT_DIR / "local_speech_completion_corpus_report.json"
    if not report_path.exists():
        print("local speech completion corpus check skipped: no local corpus artifacts")
        return 0

    completion = load_completion()
    synthesis_module = load_synthesis()
    baseline_path = REPORT_DIR / "baseline_manifest.json"
    queue_path = REPORT_DIR / "completion_queue.jsonl"
    baseline = read_json(baseline_path)
    queue = read_jsonl(queue_path)
    report = read_json(report_path)
    decision = read_json(REPORT_DIR / "local_speech_completion_decision.json")
    summary = report["summary"]

    assert set(baseline["scope"]["session_ids"]) == EXPECTED_SESSIONS, baseline["scope"]
    assert baseline["queue"]["item_count"] == 7, baseline["queue"]
    assert abs(float(baseline["queue"]["seconds"]) - 38.54) < 0.001, baseline["queue"]
    assert baseline["queue"]["by_kind"] == {"local_recall": 6, "text_fragment": 1}, baseline["queue"]
    assert completion.fingerprint(queue) == baseline["queue"]["fingerprint"]
    assert report["baseline_sha256"] == sha256(baseline_path)
    assert report["queue_sha256"] == sha256(queue_path)
    assert report["queue_fingerprint"] == completion.fingerprint(queue)
    assert report["decision"] == "PROMOTE_LOCAL_SPEECH_COMPLETION_V2", report["gates"]
    assert decision["decision"] == report["decision"]
    assert decision["decision_fingerprint"] == report["decision_fingerprint"]
    assert report["gates"]["passed"] is True, report["gates"]
    assert report["gates"]["usefulness_threshold_passed"] is True, report["gates"]
    assert summary["local_recall_closed_items"] >= 3, summary
    assert float(summary["local_recall_closed_row_ratio"]) >= 0.5, summary
    assert float(summary["local_recall_closed_second_ratio"]) >= 0.5, summary
    assert summary["text_repaired_items"] == 1, summary

    baseline_by_session = {str(row["session_id"]): row for row in baseline["sessions"]}
    corpus_by_session = {str(row["session_id"]): row for row in report["sessions"]}
    assert set(corpus_by_session) == EXPECTED_SESSIONS
    valid_outcomes = {"materialized", "rejected_remote", "rejected_conflict", "needs_review", "text_repaired"}

    for session_id in sorted(EXPECTED_SESSIONS):
        session = SESSIONS / session_id
        frozen = baseline_by_session[session_id]
        input_profile = str(frozen["input_profile"])
        assert completion.fingerprint(completion.frozen_artifacts(session, input_profile)) == completion.fingerprint(
            frozen["artifacts"]
        ), f"frozen artifacts changed: {session_id}"

        resolved = session / "derived/transcript-simple/whisper-cpp/resolved"
        auto_profile, _paths, _comparison, _risks = synthesis_module.choose_profile(resolved, "auto")
        assert auto_profile == PROFILE, (session_id, auto_profile)
        profile_dir = session / "derived/transcript-simple/whisper-cpp/local-speech-completion-v2"
        input_dialogue = read_json(resolved / f"clean_dialogue{suffix(input_profile)}.json")
        output_dialogue = read_json(resolved / f"clean_dialogue.{PROFILE}.json")
        input_remote = [row for row in input_dialogue["utterances"] if role(row) == "Colleagues"]
        output_remote = [row for row in output_dialogue["utterances"] if role(row) == "Colleagues"]
        assert output_remote == input_remote, f"remote changed: {session_id}"

        utterances = output_dialogue["utterances"]
        assert all(float(left.get("start", 0.0)) <= float(right.get("start", 0.0)) for left, right in zip(utterances, utterances[1:])), session_id
        ids = {str(row.get("id")) for row in utterances if row.get("id")}
        assert len(ids) == len([row for row in utterances if row.get("id")]), f"duplicate ids: {session_id}"

        profile_report = read_json(profile_dir / "local_speech_completion_profile_report.json")
        assert profile_report["gates"]["passed"] is True, profile_report
        assert profile_report["review_queue_actionable"] is True, profile_report
        assert profile_report["output_fingerprint"] == corpus_by_session[session_id]["output_fingerprint"]
        assert profile_report["output_fingerprint"] == completion.output_fingerprint(session)

        dispositions = read_jsonl(profile_dir / "local_speech_completion_dispositions.jsonl")
        review_rows = read_jsonl(profile_dir / "local_speech_completion_review_queue.jsonl")
        expected_queue = [row for row in queue if row["session_id"] == session_id]
        assert {row["queue_id"] for row in dispositions} == {row["queue_id"] for row in expected_queue}
        assert all(str(row["outcome"]) in valid_outcomes for row in dispositions), dispositions
        open_ids = {str(row["queue_id"]) for row in dispositions if row.get("closed") is not True}
        assert open_ids == {str(row["queue_id"]) for row in review_rows}, (open_ids, review_rows)
        assert all(row.get("kind") in {"local_recall", "text_fragment"} for row in review_rows), review_rows

        quality_in = read_json(resolved / f"quality_report{suffix(input_profile)}.json")
        quality_out = read_json(resolved / f"quality_report.{PROFILE}.json")
        assert quality_out.get("unrepaired_long_mic_crossings_count", 0) == 0, quality_out
        assert quality_out.get("golden_phrase_fail_count", 0) == 0, quality_out
        assert float(quality_out.get("remote_duplicate_in_me_seconds", 0.0)) <= float(
            quality_in.get("remote_duplicate_in_me_seconds", 0.0)
        ) + 0.001
        assert quality_out.get("transcript_order_repair") == quality_in.get("transcript_order_repair", {})

        synthesis = session / "derived/synthesis-simple/extractive"
        verdict_in = read_json(synthesis / f"quality_verdict{suffix(input_profile)}.json")
        verdict_out = read_json(synthesis / f"quality_verdict.{PROFILE}.json")
        assert verdict_rank(str(verdict_out.get("verdict"))) >= verdict_rank(str(verdict_in.get("verdict"))), (
            verdict_in,
            verdict_out,
        )
        notes = read_json(synthesis / f"evidence_notes.{PROFILE}.json")
        missing_evidence = collect_evidence_ids(notes) - ids
        assert not missing_evidence, f"missing evidence ids in {session_id}: {sorted(missing_evidence)}"

    long_dialogue = read_json(
        SESSIONS
        / "2026-07-21_14-27-53-live/derived/transcript-simple/whisper-cpp/resolved/clean_dialogue.local_speech_completion_v2.json"
    )
    inserted = {
        str(row.get("text"))
        for row in long_dialogue["utterances"]
        if isinstance(row.get("source"), dict) and row["source"].get("kind") == "local_speech_completion"
    }
    assert "летайма а также уменьшение разброса летайма то есть вот" in inserted, inserted
    assert "мы от него знаем, что вообще там по" in inserted, inserted

    short_dialogue = read_json(
        SESSIONS
        / "2026-07-21_15-11-15-live/derived/transcript-simple/whisper-cpp/resolved/clean_dialogue.local_speech_completion_v2.json"
    )
    short_me = [str(row.get("text") or "") for row in short_dialogue["utterances"] if role(row) == "Me"]
    assert any("Наша команда SpiritCode остается как есть" in text for text in short_me), short_me
    assert not any("дает сп" in text.lower() for text in short_me), short_me
    assert sum("не ожидают" in text.lower() for text in short_me) == 1, short_me

    print("local speech completion corpus checks ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
