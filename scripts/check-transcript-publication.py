#!/usr/bin/env python3
"""Public synthetic regressions for the rendered transcript, not only its parents."""

from copy import deepcopy
import importlib.util
from pathlib import Path
import tempfile
from unittest.mock import patch

import numpy as np
import soundfile as sf

import transcript_publication as P
import acoustic_timing_evidence as A
import transcript_interval_evidence as I


def main() -> int:
    rows = [
        {"id": "remote", "role": "remote", "start": 10.0, "end": 20.0,
         "text": "First. Second.", "speaker_turns": [
             {"text": "First. ", "start": 10.0, "end": 12.0, "speaker_id": "remote_speaker_01"},
             {"text": "Second.", "start": 18.0, "end": 20.0, "speaker_id": "remote_speaker_02"}]},
        {"id": "me", "role": "me", "start": 15.0, "end": 19.0, "text": "Local.",
         "quality": {"needs_review": True, "text_integrity": {"status": "needs_review", "reason": "repetition"}}},
        {"id": "punctuation", "role": "remote", "start": 32.5, "end": 33.0,
         "text": "...", "speaker_turns": [{"text": "...", "speaker_id": None}]},
    ]
    before = deepcopy(rows)
    turns = P.display_turns(rows)
    assert [r["start"] for r in turns] == [10.0, 15.0, 18.0, 32.5]
    assert [r["utterance_id"] for r in turns] == ["remote", "me", "remote", "punctuation"]
    assert turns[-1]["time_basis"] == "parent_interval"
    assert turns[1]["review_reasons"][0]["facet"] == "text"
    assert rows == before
    for parent in rows:
        assert "".join(r["text"] for r in turns if r["utterance_id"] == parent["id"]) == parent["text"]
    rendered = "\n".join(P.render_body(turns))
    assert "## ~00:32-00:33 remote_speaker_unknown [unattributed] [needs_review: time]" in rendered
    assert "## 00:15 Me [needs_review: text]" in rendered
    assert "repetition" in rendered and "Approximate parent interval" in rendered

    # One invalid child invalidates precision, not labels or the order of words.
    damaged = deepcopy(rows[:1])
    damaged[0]["speaker_turns"][0]["start"] = 0.0
    projected = P.display_turns(damaged)
    assert [r["start"] for r in projected] == [10.0, 10.0]
    assert [r["speaker_label"] for r in projected] == ["remote_speaker_01", "remote_speaker_02"]
    assert all(r["time_basis"] == "parent_interval" for r in projected)
    assert projected[0]["source_interval"]["start"] == 0.0
    assert "".join(r["text"] for r in projected) == "First. Second."
    with_local = P.display_turns(damaged + rows[1:2])
    assert "me" in with_local[0]["overlapping_utterance_ids"]
    assert "remote" in with_local[-1]["overlapping_utterance_ids"]
    assert "Intersecting source/display intervals" in "\n".join(P.render_body(with_local))
    damaged[0]["speaker_turns"][0]["text"] = "Unrelated words. "
    mismatch = P.display_turns(damaged)
    assert len(mismatch) == 1 and mismatch[0]["text"] == rows[0]["text"]
    assert any(r["reason"] == "speaker_turn_text_mismatch" for r in mismatch[0]["review_reasons"])
    for bad in (None, -1, float("nan"), float("inf"), True):
        assert P.format_time(bad) == "??:??"
    assert P.format_time(0) == "00:00"
    missing = [{"id": "missing", "role": "me", "text": "Unknown time."}]
    assert P.display_turns(missing)[0]["start"] is None
    assert "## ??:?? Me [needs_review: time]" in "\n".join(P.render_body(P.display_turns(missing)))
    assert P.review_reasons({"needs_review": False}) == []
    assert P.review_reasons({"needs_review": True})[0]["facet"] == "review"
    assert P.review_reasons({"local_voice": {"status": "needs_review"}})[0]["facet"] == "role"
    assert P.review_reasons({"review": {"status": "cleared", "history": {"needs_review": True}}}) == []
    assert {r["reason"] for r in P.review_reasons({
        "needs_review": True, "reason": "parent_issue",
        "text_integrity": {"status": "needs_review", "reason": "child_issue"},
    })} == {"parent_issue", "child_issue"}
    nested = deepcopy(rows[:1])
    nested[0]["speaker_turns"][1]["quality"] = {"needs_review": True, "reason": "turn_issue"}
    assert P.display_turns(nested)[0]["review_reasons"] == []
    assert P.display_turns(nested)[1]["review_reasons"][0]["reason"] == "turn_issue"

    contextual = [{"id": "short", "role": "me", "text": "Several context words.", "start": 5.0, "end": 5.5,
                   "quality": {"needs_review": False, "repair": {"action": "micro_reasr", "micro_reasr": {
                       "status": "ok", "rows": [{"text": "Several context words.", "start_ms": 4000, "end_ms": 6500}]
                   }}}}]
    assert "micro_asr_context_not_owned_by_target" in "\n".join(P.render_body(P.display_turns(contextual)))
    assert contextual[0]["quality"]["needs_review"] is False
    padded = deepcopy(contextual[0])
    padded["quality"]["repair"].update(recognition_start_ms=5000, recognition_end_ms=5500)
    padded["quality"]["repair"]["micro_reasr"].update(slice_start_ms=4000, slice_end_ms=6500)
    assert I.provenance(padded)["recognition_interval"] == {"start": 4.0, "end": 6.5}

    # Ordinary candidates of either role must not evade checks through an empty repair.
    narrowed = [{"id": "trim", "role": role, "source_start": 10.0, "source_end": 13.78,
                 "start": 12.5, "end": 13.78, "text": "A complete sentence incorrectly survives the interval trim.",
                 "quality": {"needs_review": False, "repair": {}}} for role in ("me", "remote")]
    before_narrowed = deepcopy(narrowed)
    for row in narrowed:
        assert I.assess_utterance(row)["automatic_edit_allowed"] is False
        view = P.display_turns([row])[0]
        assert view["interval_provenance"]["source_interval"]["start"] == 10.0
        assert view["source_interval"]["start"] == 12.5  # Legacy display field is unchanged.
        assert "text_interval_narrowed_without_word_support" in "\n".join(P.render_body([view]))
        assert view["text"] == row["text"] and view["start"] == row["start"]
    assert narrowed == before_narrowed
    short = {"id": "short", "role": "me", "start": 0.0, "end": 0.4, "text": "Yes.",
             "source_start": 0.0, "source_end": 0.4}
    assert I.assess_utterance(short) is None
    assert I.assess_utterance({**short, "source_end": 0.6}) is None
    assert I.assess_utterance({**short, "source_end": 10.0}) is None
    assert I.assess_utterance({**narrowed[0], "start": 10.5, "text": "Several calm words."}) is None
    for malformed in (None, [], "legacy"):
        assert I.assess_utterance({**narrowed[0], "quality": malformed}) is not None
        assert I.assess_utterance({**narrowed[0], "source_end": malformed}) is None
    # A substantial remote trim at normal character density can still crowd words.
    remote = {**narrowed[1], "source_start": 0., "source_end": 12., "start": 0., "end": 6.,
              "text": " ".join(["word"] * 30)}
    assert I.assess_utterance(remote)["words_per_sec"] == 5.0
    supported = deepcopy(narrowed[0])
    supported["quality"]["repair"] = {"micro_reasr": {"selected_words": [
        {"word": supported["text"], "start_ms": 12500, "end_ms": 13780}]}}
    assert I.assess_utterance(supported) is None
    supported["quality"]["repair"]["micro_reasr"]["selected_words"][0]["word"] = "Incomplete."
    assert I.assess_utterance(supported) is not None

    with tempfile.TemporaryDirectory(prefix="murmurmark-acoustic-timing-") as temp:
        session = Path(temp)
        audio = session / "audio/remote/000001.caf"
        audio.parent.mkdir(parents=True)
        samples = np.zeros((24_000, 2))
        samples[21_000:22_000] = [0.1, -0.1]
        sf.write(audio, samples, 1000, subtype="FLOAT")
        utterances = [
            {"id": "remote", "role": "remote", "start": 0.7, "end": 22.5,
             "source_start": 0.0, "source_end": 22.5, "text": "Remote words.", "speaker_label": "remote_speaker_01"},
            {"id": "local", "role": "me", "start": 2.0, "end": 10.0, "text": "Earlier local words."},
        ]
        evidence = A.inspect(session, utterances)
        assert evidence["status"] == "checked", evidence
        assert evidence["utterances"]["remote"]["sound_not_before"] == 21.0
        projection = P.display_turns(utterances, timing_evidence=evidence)
        assert [r["utterance_id"] for r in projection] == ["local", "remote"]
        assert projection[1]["time_basis"] == "acoustic_lower_bound"
        assert projection[1]["source_interval"]["start"] == 0.7
        assert "word timing is not established" in "\n".join(P.render_body(projection))
        assert utterances[0]["start"] == 0.7
        with patch.object(Path, "open", side_effect=PermissionError("fixture denied")):
            denied = A.inspect(session, utterances)
        assert denied["status"] == "unavailable" and denied["utterances"] == {}
        assert denied["reason"] == "audio_fingerprint_unavailable"
        second = audio.with_name("000002.caf")
        sf.write(second, samples, 1000, subtype="FLOAT")
        multiple = A.inspect(session, utterances)
        assert multiple["status"] == "unavailable" and multiple["utterances"] == {}
        second.unlink()
        before_identity = A.fingerprint(audio, session)
        with patch.object(A, "fingerprint", side_effect=[before_identity, {**before_identity, "sha256": "changed"}]):
            changed = A.inspect(session, utterances)
        assert changed["status"] == "unavailable" and changed["utterances"] == {}
        assert changed["reason"] == "audio_changed_during_analysis"
        # Quiet, out-of-phase audio is not digital silence; never discard it.
        samples[10] = [1e-10, -1e-10]
        sf.write(audio, samples, 1000, subtype="FLOAT")
        assert A.inspect(session, utterances)["utterances"] == {}
        sf.write(audio, np.zeros_like(samples), 1000, subtype="FLOAT")
        silent = A.inspect(session, utterances)
        assert silent["utterances"]["remote"]["sound_not_before"] is None
        assert P.display_turns(utterances, timing_evidence=silent)[0]["start"] == 0.7
        audio.write_bytes(b"not an audio file")
        corrupt = A.inspect(session, utterances)
        assert corrupt["status"] == "unavailable" and corrupt["utterances"] == {}
        audio.unlink()
        missing = A.inspect(session, utterances)
        assert missing["status"] == "unavailable" and missing["utterances"] == {}
        assert P.display_turns(utterances, timing_evidence=missing)[0]["start"] == 0.7

    script = Path(__file__).with_name("materialize-provisional-speaker-transcript.py")
    spec = importlib.util.spec_from_file_location("publication_test", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    markdown = module.render_markdown(rows, {}, "fixture", "provisional", "fixture", {})
    assert rendered in markdown
    assert "not a human-verified truth" in markdown
    assert markdown.index("First.") < markdown.index("Local.") < markdown.index("Second.")
    print("transcript publication checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
