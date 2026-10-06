#!/usr/bin/env python3
"""Shared playback must not share decisions or rebuild audio during finalization."""

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch


def load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    workspace = load("build-review-workspace")
    apply = load("apply-review-workspace-decisions")
    with tempfile.TemporaryDirectory(prefix="murmurmark-listening-contexts-") as directory:
        root = Path(directory)
        (root / "session.json").write_text("{}")
        audio = root / "audio/mic/000001.caf"
        audio.parent.mkdir(parents=True)
        audio.write_bytes(b"not read by this metadata-only index")
        rows = [{"session": str(root), "session_id": root.name, "input_profile": "fixture",
                 "source": source, "review_lane": source, "utterance_ids": ["u1"],
                 "interval": {"start": 10 + index, "end": 15 + index}, "decision": "todo",
                 "text": [{"id": "u1", "role": "Me", "text": "Synthetic question text."}],
                 "reason": "synthetic time ownership question",
                 "allowed_decisions": ["keep_me", "needs_review", "skip"]}
                for index, source in enumerate(("transcript_text", "transcript_order", "audio_review"))]
        original = deepcopy(rows)
        groups = workspace.listening_contexts(rows)
        assert len(groups) == 1 and len(groups[0]["questions"]) == 3
        assert groups[0]["start"] == 9 and groups[0]["end"] == 18
        assert groups[0]["answer_scope"] == "individual_question_only"
        assert len({q["question_id"] for q in groups[0]["questions"]}) == 3
        assert "-t 9.000" in groups[0]["commands"]["mic"]
        assert rows == original
        assert workspace.listening_contexts(list(reversed(rows))) == groups
        with_origin = {**rows[0], "review_features": {"interval_provenance": {
            "source_interval": {"start": 5, "end": 15}, "recognition_interval": {"start": 4, "end": 16}}}}
        assert workspace.listening_contexts([with_origin])[0]["start"] == 3
        assert workspace.listening_contexts([with_origin])[0]["questions"][0]["interval"] == rows[0]["interval"]
        answered = deepcopy(rows)
        answered[0].update(decision="keep_me", review_source="manual")
        assert len(workspace.listening_contexts(answered)[0]["questions"]) == 2
        separated = rows + [{**rows[0], "input_profile": "other"}, {**rows[0], "session": "other"},
                            {**rows[0], "interval": {}}, {**rows[0], "interval": {"start": 70, "end": 80}}]
        assert len(workspace.listening_contexts(separated)) == 5
        chain = [{**rows[0], "utterance_ids": [str(i)], "interval": {"start": i, "end": i + 20}}
                 for i in range(0, 100, 15)]
        assert all(g["end"] - g["start"] <= 45 for g in workspace.listening_contexts(chain))

        template, decisions = root / "template.jsonl", root / "decisions.jsonl"
        template.write_text("".join(json.dumps(row) + "\n" for row in rows))
        decisions.write_text("".join(json.dumps(row) + "\n" for row in answered))
        previous_workspace = root / "review_workspace.json"
        previous_workspace.write_text('{"existing": "keep lane audio and answers"}')
        before = {p: p.read_bytes() for p in (template, decisions, previous_workspace, audio)}
        with (patch.object(sys, "argv", ["workspace", "--template", str(template), "--decisions", str(decisions),
                                         "--out-dir", str(root), "--metadata-only"]),
              patch.object(workspace, "build_lane_pack", side_effect=AssertionError("must not build audio"))):
            assert workspace.main() == 0
        assert all(p.read_bytes() == value for p, value in before.items())
        with patch.object(sys, "argv", ["workspace", "--metadata-only", "--rebase-decisions"]):
            try:
                workspace.main()
                raise AssertionError("conflicting mode accepted")
            except SystemExit as error:
                assert error.code == 2
        with patch.object(Path, "replace", side_effect=OSError("simulated publication failure")):
            try:
                workspace.write_json(previous_workspace, {"partial": True})
                raise AssertionError("write unexpectedly succeeded")
            except OSError:
                pass
        assert previous_workspace.read_bytes() == before[previous_workspace]
        index = root / "review_listening_contexts.json"
        payload = json.loads(index.read_text())
        assert payload["parameters"]["metadata_only"] and not payload["lanes"]
        assert "manual_flow" not in payload
        assert "Shared Listening Contexts" in index.with_suffix(".md").read_text()
        assert "Synthetic question text." in index.with_suffix(".md").read_text()
        assert "synthetic time ownership question" in index.with_suffix(".md").read_text()
        markdown = index.with_suffix(".md")
        before_markdown = markdown.read_bytes()
        with patch.object(Path, "replace", side_effect=OSError("simulated Markdown publication failure")):
            try:
                workspace.write_markdown(markdown, payload)
                raise AssertionError("Markdown write unexpectedly succeeded")
            except OSError:
                pass
        assert markdown.read_bytes() == before_markdown
        assert not list(root.glob(".*.tmp"))
        with patch.object(sys, "argv", ["apply", "--workspace", str(index)]):
            assert apply.main() == 2
        assert all(p.read_bytes() == value for p, value in before.items())
    print("review listening context checks passed")


if __name__ == "__main__":
    main()
