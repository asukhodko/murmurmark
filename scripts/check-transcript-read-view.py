#!/usr/bin/env python3
"""Synthetic source/queue/publication invariants, without model or capture access."""

from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch
from murmurmark_deadline import ActionStopped, BUDGET_EXIT

import transcript_read_view as V


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(V.encoded(value))


def fixture(session, state="selected"):
    rows = [
        {"id": "r", "role": "remote", "start": 10., "end": 25., "text": "First. Last.",
         "speaker_turns": [{"start": 10., "end": 14., "text": "First. ", "speaker_id": "remote_speaker_01"},
                           {"start": 23., "end": 25., "text": "Last.", "speaker_id": "remote_speaker_02"}]},
        {"id": "m", "role": "me", "start": 17., "end": 18., "text": "Local words.",
         "quality": {"needs_review": True, "reason": "source_flag"}},
    ]
    dialogue = session / "dialogue.json"
    write(dialogue, {"utterances": rows})
    aggregate = session / "transcript.md"
    aggregate.write_text("Original immutable transcript.\n")
    descriptor = lambda p: {"path": str(p.relative_to(session)), "sha256": V.sha(p)}
    selection = {"state": state, "selected_profile": "fixture", "semantic_fingerprint": "fixture_selection",
                 "selected_transcript": descriptor(aggregate), "selected_dialogue": descriptor(dialogue),
                 "rich_transcript": descriptor(dialogue) if state != "fallback" else None}
    write(session / (V.PROVISIONAL if state in {"provisional", "unavailable"} else V.STRICT), selection)
    write(session / "derived/readiness/session_readiness.json", {"selected_profile": "fixture"})
    question = {"session_id": session.name, "input_profile": "fixture", "source": "transcript_text",
                "source_audit_id": "text:r", "utterance_ids": ["r"], "interval": {"start": 10., "end": 25.},
                "status": "todo", "decision": "todo", "label": "later_check", "review_features": {},
                "allowed_decisions": ["keep_me", "needs_review"], "text": [{"id": "r", "text": rows[0]["text"]}]}
    template = session / V.PLAN / "review_decisions.template.jsonl"
    template.parent.mkdir(parents=True)
    template.write_text(json.dumps(question) + "\n")
    decisions = session / V.PLAN / "review_decisions.jsonl"
    snapshot = V.review.queue_snapshot([question], template, decisions)
    write(session / V.PLAN / "review_decisions_progress.json", {"queue_snapshot": snapshot})
    outcome = {"selected_profile": "fixture", "outcome": "blocked", "use_gate": "review_required",
               "export_status": "blocked_until_review", "verdict": "usable_with_review", "gates": [],
               "speaker_resolution": {"state": state, "selection_fingerprint": "fixture_selection",
                                      "selected_speaker_profile": "fixture", "transcript_path": "transcript.md"}}
    return rows, question, outcome


def main():
    with tempfile.TemporaryDirectory(prefix="murmurmark-read-view-") as temp:
        # The installed self-test also runs on the system Python 3.9.
        with patch.object(V.hashlib, "file_digest", create=True,
                          side_effect=AssertionError("Python 3.11-only API")):
            sample = Path(temp) / "hash-input"
            for data in (b"", b"abc", b"chunked hash\n" * 100000):
                sample.write_bytes(data)
                assert V.sha(sample) == V.hashlib.sha256(data).hexdigest()
        for state in ("selected", "provisional", "fallback", "unavailable"):
            session = Path(temp).resolve() / state
            session.mkdir()
            rows, question, outcome = fixture(session, state)
            before = (session / "dialogue.json").read_bytes()
            result = V.materialize(session, outcome)
            path = V.verified_path(session, outcome)
            assert path == session / result["path"]
            assert V.verified_path(session, outcome, expected_generation=result["generation"]) == path
            assert V.verified_path(session, outcome, expected_generation="previous-generation") is None
            text = path.read_text()
            assert "Quality: `blocked`" in text and "Attribution: `" + state in text
            assert "later_check" in text and "source_flag" not in text
            assert text.index("First.") < text.index("Local words.") < text.index("Last.")
            assert V.materialize(session, outcome) == result
            relative = Path(os.path.relpath(session))
            assert V.materialize(relative, outcome) == result
            assert V.verified_path(relative, outcome) == path
            assert (session / "dialogue.json").read_bytes() == before
            pointer = session / V.DIRECTORY / "selection.json"
            saved_pointer = pointer.read_bytes()
            original_write = V.atomic_write

            def interrupt_markdown(path, data):
                if path.suffix == ".md":
                    raise KeyboardInterrupt
                original_write(path, data)

            with patch.object(V, "atomic_write", side_effect=interrupt_markdown):
                try:
                    V.materialize(session, {**outcome, "verdict": "changed_context"})
                    raise AssertionError("interruption ignored")
                except KeyboardInterrupt:
                    pass
            assert pointer.read_bytes() == saved_pointer and V.verified_path(session, outcome) == path
            with patch.object(V, "check_deadline", side_effect=ActionStopped(BUDGET_EXIT)):
                try:
                    V.materialize(session, outcome)
                    raise AssertionError("deadline ignored")
                except ActionStopped as error:
                    assert error.returncode == BUDGET_EXIT
            assert pointer.read_bytes() == saved_pointer and V.verified_path(session, outcome) == path
            # A changed gate invalidates the read view, not the underlying evidence.
            assert V.verified_path(session, {**outcome, "outcome": "ready_for_notes"}) is None
            mismatched = deepcopy(outcome)
            mismatched["speaker_resolution"]["transcript_path"] = "another.md"
            assert V.verified_path(session, mismatched) is None
            with patch.object(V.publication, "__file__", str(session / "missing_implementation.py")):
                assert V.verified_path(session, outcome) is None
            progress = session / V.PLAN / "review_decisions_progress.json"
            historical = {**question, "source_audit_id": "historical:text:m", "utterance_ids": ["m"],
                          "decision": "needs_review", "status": "reviewed", "label": "historical_open"}
            decisions = session / V.PLAN / "review_decisions.jsonl"
            decisions.write_text(json.dumps(historical) + "\n")
            snapshot = V.review.queue_snapshot([question, historical], session / V.PLAN / "review_decisions.template.jsonl", decisions)
            write(progress, {"queue_snapshot": snapshot})
            V.materialize(session, outcome)
            assert "historical_open" in V.verified_path(session, outcome).read_text()
            assert len(V.questions(session, "fixture")[0]) == 2
            historical["input_profile"] = "previous_profile"
            decisions.write_text(json.dumps(historical) + "\n")
            write(progress, {"queue_snapshot": V.review.queue_snapshot([question, historical],
                            session / V.PLAN / "review_decisions.template.jsonl", decisions)})
            V.materialize(session, outcome)
            text = V.verified_path(session, outcome).read_text()
            assert "historical_open" in text.split("## Review Questions")[1]
            assert "historical_open" not in text.split("## Review Questions")[0]
            assert "historical_profile_unplaced" in text
            decisions.unlink()
            closed = {**question, "decision": "skip", "status": "reviewed"}
            snapshot = V.review.queue_snapshot([closed], session / V.PLAN / "review_decisions.template.jsonl",
                                               session / V.PLAN / "review_decisions.jsonl")
            write(progress, {"queue_snapshot": snapshot})
            assert V.verified_path(session, outcome) is None
            V.materialize(session, outcome)
            assert "later_check" not in V.verified_path(session, outcome).read_text()
            progress.write_text("{}")
            V.materialize(session, outcome)
            missing = V.verified_path(session, outcome).read_text()
            assert "unavailable_or_stale" in missing and "open questions: unknown" in missing
            assert "source_flag" in missing
            view_path = V.verified_path(session, outcome)
            view_path.write_text("corrupted")
            assert V.verified_path(session, outcome) is None
            assert (session / "dialogue.json").read_bytes() == before

        damaged = deepcopy(rows)
        damaged[0]["speaker_turns"][-1]["end"] = 26.
        turns = V.project(damaged, [], "current")
        assert all(t["time_basis"] == "parent_interval" for t in turns if t["utterance_id"] == "r")
        assert all(t["speaker_label"].startswith("remote_speaker_") for t in turns if t["utterance_id"] == "r")
        assert "m" in turns[0]["overlapping_utterance_ids"]
        assert any(t["utterance_id"] == "m" for t in turns)
        assert damaged[0]["speaker_turns"][-1]["end"] == 26.
        named = deepcopy(rows)
        named[0]["speaker_turns"][0]["speaker_label"] = "Confirmed Alias"
        assert V.project(named, [], "current")[0]["speaker_label"] == "Confirmed Alias"
        unknown_turn = deepcopy(rows)
        unknown_turn[0]["speaker_turns"][0].update(speaker_label="Colleagues", speaker_id=None)
        assert V.project(unknown_turn, [], "current")[0]["speaker_label"] == "remote_speaker_unknown"
        for value in ("../outside.json", "/tmp/not-in-session.json"):
            try:
                V.local(session, value)
                raise AssertionError("external path accepted")
            except ValueError:
                pass
    print("transcript read-view checks passed")


if __name__ == "__main__":
    main()
