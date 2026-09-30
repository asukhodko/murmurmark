#!/usr/bin/env python3
from __future__ import annotations

import copy
import argparse
import importlib.util
import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from transcript_overlaps import build_overlaps


def load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main():
    rows = [
        {"id": "me", "speaker_label": "Me", "source_track": "mic", "start": 1, "end": 5, "corrected_text": "unique local continuation"},
        {"id": "remote", "speaker_label": "Colleagues", "source_track": "remote", "start": 3, "end": 8, "corrected_text": "remote speech"},
    ]
    original = copy.deepcopy(rows)
    for old in ([], [{"left_utterance_id": "me", "right_utterance_id": "remote", "note": "keep"}],
                [{"me_utterance_id": "me", "remote_utterance_id": "remote", "note": "keep"}]):
        overlaps = build_overlaps(rows, old)
        assert len(overlaps) == 1 and overlaps[0]["duration_sec"] == 2
        assert overlaps[0]["left_utterance_id"] == overlaps[0]["me_utterance_id"] == "me"
        if old:
            assert overlaps[0]["note"] == "keep"
        integrity = load("apply-transcript-integrity")
        assert integrity.build_overlaps(rows, {"overlaps": old}) == integrity.build_overlaps(rows, {"overlaps": overlaps})
    assert rows == original
    assert build_overlaps(rows[1:], overlaps) == []
    assert build_overlaps([{**rows[0], "end": 3}, rows[1]], overlaps) == []
    for invalid in ([rows[0], rows[0]], [{**rows[0], "end": float("nan")}], [{**rows[0], "id": ""}]):
        try:
            build_overlaps(invalid)
            raise AssertionError("invalid dialogue accepted")
        except ValueError:
            pass
    audit = load("audit-transcript-order")
    args = argparse.Namespace(min_overlap_sec=0.5, long_me_sec=6.0, tail_sec=0.8)
    from_empty = audit.build_items(rows, [], args)
    from_valid = audit.build_items(rows, overlaps, args)
    from_legacy = audit.build_items(rows, [{"me_utterance_id": "me", "remote_utterance_id": "remote"}], args)
    assert from_empty and from_empty == from_valid == from_legacy
    with tempfile.TemporaryDirectory(prefix="murmurmark-order-inputs-") as temporary:
        session = Path(temporary)
        resolved = session / "derived/transcript-simple/whisper-cpp/resolved"
        resolved.mkdir(parents=True)
        (resolved / "overlaps.json").write_text(json.dumps({"overlaps": []}))
        cli_args = argparse.Namespace(**vars(args), session=session, profile="current", out_dir=None)
        cases = [
            ({}, 2),
            ({"utterances": None}, 2),
            ({"utterances": {}}, 2),
            ({"utterances": [rows[0], None]}, 2),
            ({"utterances": [{}]}, 2),
            ({"utterances": []}, 0),
            ({"utterances": rows}, 0),
        ]
        for dialogue, expected_code in cases:
            (resolved / "clean_dialogue.json").write_text(json.dumps(dialogue))
            with patch.object(audit, "parse_args", return_value=cli_args), contextlib.redirect_stdout(io.StringIO()):
                assert audit.main() == expected_code, dialogue
            report = json.loads((session / "derived/audit/order/transcript_order_audit.json").read_text())
            assert report["status"] == ("missing_inputs" if expected_code else "ok"), report
            if expected_code:
                assert report["input_error"] and report["summary"]["blocking_order_risk"], report
                markdown = (session / "derived/audit/order/transcript_order_review.md").read_text()
                assert "Audit unavailable" in markdown and "No probable order risks found" not in markdown
    print("current-dialogue overlap checks ok")


if __name__ == "__main__":
    main()
