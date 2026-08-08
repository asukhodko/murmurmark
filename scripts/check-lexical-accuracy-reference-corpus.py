#!/usr/bin/env python3
"""Contract, privacy and determinism checks for Lexical Accuracy Reference Corpus v1."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts/report-lexical-accuracy-reference-corpus.py"
POLICY_PATH = ROOT / "policies/lexical-accuracy-reference-corpus-v1.json"


def load_module():
    spec = importlib.util.spec_from_file_location("lexical_accuracy_reference", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def private_row(
    module,
    source_id: str,
    trust_grade: str,
    meeting_mode: str,
    acoustic_mode: str,
    roles: list[str],
    session_id: str | None,
) -> dict:
    reference = "раз два три"
    hypothesis = "раз четыре три пять"
    return {
        "schema": module.PRIVATE_ROW_SCHEMA,
        "source_id": source_id,
        "kind": "fixture",
        "trust_grade": trust_grade,
        "correctness_eligible": trust_grade in {"exact_generated", "human_reviewed"},
        "session_id": session_id,
        "meeting_mode": meeting_mode,
        "acoustic_mode": acoustic_mode,
        "role_scope": roles,
        "split": "fixture",
        "reference_text": reference,
        "hypothesis_text": hypothesis,
        "metrics": module.edit_metrics(reference, hypothesis),
        "timing": {"timestamped_tokens": 4},
        "inputs": [],
    }


def main() -> int:
    module = load_module()
    policy = module.load_policy(POLICY_PATH)

    metric = module.edit_metrics("раз два три", "раз четыре три пять")
    assert metric["substitutions"] == 1
    assert metric["insertions"] == 1
    assert metric["deletions"] == 0
    assert metric["wer"] == 0.666667
    assert module.edit_metrics("Надёжный ёж", "надежный еж")["wer"] == 0.0

    tab = module.parse_tab_transcript("header\n00:00:01\tLocal Person\tПривет\n00:00:03\tRemote Person\tДа\n")
    blocks = module.parse_range_transcript("0:00 - 0:02\nRemote Person\nПривет\n\n0:03 - 0:04\nLocal Person\nДа\n")
    assert len(tab) == 2 and tab[0]["end"] == 3.0
    assert len(blocks) == 2 and blocks[1]["start"] == 3.0

    with tempfile.TemporaryDirectory(dir=ROOT / "sessions") as temp:
        root = Path(temp)
        args = SimpleNamespace(policy=POLICY_PATH, out_dir=root, sessions_root=ROOT / "sessions")
        exact = private_row(module, "exact", "exact_generated", "controlled", "digital_source", ["remote"], None)
        weak_1x1 = private_row(module, "weak-1x1", "independent_machine", "1x1", "speaker_playback", ["me", "remote"], "fixture-1")
        weak_group = private_row(module, "weak-group", "independent_machine", "group", "headphones_or_low_leak", ["me", "remote"], "fixture-2")
        report, manifest = module.build_public(policy, [exact, weak_1x1, weak_group], args)
        assert report["decision"] == "REFERENCE_INSUFFICIENT"
        assert report["summary"]["correctness_eligible_sources"] == 1
        assert report["summary"]["human_reviewed_real_sessions"] == 0
        assert report["gates"]["weak_references_excluded_from_correctness"] is True
        assert module.canonical_json(report) == module.canonical_json(module.build_public(policy, [exact, weak_1x1, weak_group], args)[0])
        public = module.canonical_json(manifest).decode("utf-8")
        assert "reference_text" not in public
        assert "hypothesis_text" not in public
        assert "Local Person" not in public
        assert "/Users/" not in public

        human_1x1 = private_row(module, "human-1x1", "human_reviewed", "1x1", "speaker_playback", ["me", "remote"], "fixture-1")
        human_group = private_row(module, "human-group", "human_reviewed", "group", "headphones_or_low_leak", ["me", "remote"], "fixture-2")
        promoted, _ = module.build_public(policy, [exact, human_1x1, human_group], args)
        assert promoted["decision"] == "LEXICAL_BASELINE_ESTABLISHED"

        try:
            absolute_fixture = "/" + "Users/private/reference.txt"
            module.assert_public_safe({"path": absolute_fixture})
        except module.CorpusError:
            pass
        else:
            raise AssertionError("absolute path leakage was not rejected")
        try:
            module.assert_public_safe({"reference_text": "private"})
        except module.CorpusError:
            pass
        else:
            raise AssertionError("private reference text was not rejected")

        source = root / "source.txt"
        source.write_text("reference", encoding="utf-8")
        frozen = module.fingerprint(source)
        source.write_text("changed", encoding="utf-8")
        assert module.fingerprint(source) != frozen

    print("lexical accuracy reference corpus checks: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
