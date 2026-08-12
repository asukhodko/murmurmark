#!/usr/bin/env python3
"""Check the frozen disjoint remote-speaker truth v2 contract."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/build-remote-speaker-disjoint-truth-v2.py"
POLICY = ROOT / "policies/remote-speaker-disjoint-truth-expansion-v2.json"
OUT = ROOT / "sessions/_reports/remote-speaker-disjoint-truth-expansion-v2"


def load_module():
    spec = importlib.util.spec_from_file_location("murmurmark_disjoint_truth_v2_check", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load disjoint truth v2 builder")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    module = load_module()
    policy = module.load_policy(POLICY)
    require(policy["selection"]["expected_primary_items"] == 72, "policy primary count changed")
    require(policy["repeats"]["expected_items"] == 12, "policy repeat count changed")
    require(policy["decision"]["production_promotion_allowed"] is False, "promotion enabled")
    synthetic_slot = {
        "slot_id": "interactive_stdin_probe",
        "session_alias": "session_probe",
        "audio": {"path": "unused.wav"},
        "exemplars": [],
        "speaker_choices": ["remote_speaker_01", "unknown_speaker", "mixed", "unusable"],
    }
    captured_stdout = io.StringIO()
    captured_stderr = io.StringIO()
    with redirect_stdout(captured_stdout), redirect_stderr(captured_stderr):
        with (
            patch.object(module, "load_bundle", return_value={"queue": [synthetic_slot]}),
            patch.object(module, "accepted_answers", return_value={}),
            patch.object(module, "play_session_reference"),
            patch("builtins.input", side_effect=EOFError),
        ):
            require(module.interactive_review(policy, OUT, None) == 2, "interactive EOF must fail visibly")
    require("interactive review input is unavailable" in captured_stderr.getvalue(), "interactive EOF message missing")
    if not (OUT / "private/candidate_pack.frozen.json").is_file():
        print("remote speaker disjoint truth v2 static checks passed; private pack unavailable")
        return 0
    bundle = module.load_bundle(policy, OUT)
    report = module.build_report(policy, OUT)
    selection = bundle["selection"]
    old = module.read_jsonl(ROOT / "sessions/_reports/remote-speaker-direct-truth-seed-v1/private/seed_selection.jsonl")

    require(len(selection) == 72, "primary count changed")
    require(sum(row["repeat_selected"] for row in selection) == 12, "repeat count changed")
    require(len({row["session_alias"] for row in selection}) == 6, "session coverage changed")
    require(sum(row["word_count"] for row in selection) == 148, "word count changed")
    require(round(sum(row["coverage_weight_sec"] for row in selection), 6) == 155.440894, "seconds changed")
    require(all(
        module.overlap_seconds(row, previous) < 0.000001
        for row in selection for previous in old if row["session_id"] == previous["session_id"]
    ), "v1 interval overlap found")
    tags = {tag for row in selection for tag in row["tags"]}
    require({
        "ecapa_wavlm_disagreement", "wespeaker_disagreement", "temporal_shift_instability",
        "temporal_boundary_uncertain", "mixed_or_overlap", "short_turn", "utterance_boundary",
        "session_edge", "five_speaker",
    } <= tags, "stratification coverage changed")
    require(bundle["pack"]["prior_truth_read"] is False, "candidate pack read prior truth")
    require(bundle["pack"]["inherited_production_guards"] == 355, "production guards changed")
    require(bundle["review_pack"]["mixed_exemplars_allowed"] is False, "mixed exemplars enabled")
    require(bundle["review_pack"]["queue_order"] == "session_blocks_primary_then_blind_repeats_v1", "review queue order changed")
    kinds = {row["slot_id"]: row["kind"] for row in bundle["slot_map"]}
    sessions = {row["slot_id"]: row["session_alias"] for row in bundle["slot_map"]}
    order = [(kinds[row["slot_id"]], sessions[row["slot_id"]]) for row in bundle["queue"]]
    require(order == sorted(order, key=lambda value: (0 if value[0] == "primary" else 1, value[1])), "review queue is not session-blocked")
    require(sum(left[1] != right[1] for left, right in zip(order, order[1:])) <= 11, "review queue has excessive session switches")
    try:
        module.ensure_review_refresh_allowed([{"outcome": "unknown_speaker"}])
    except module.DisjointTruthError as error:
        require(str(error) == "review_refresh_forbidden_after_first_answer", "unexpected refresh refusal")
    else:
        raise AssertionError("review refresh accepted an existing answer")
    require(all(row["purity"]["basis"] in {
        "human_reviewed_single_speaker_v1",
        "temporal_single_cluster_and_coverage_mapping",
        "single_remote_speaker_topology",
    } for row in bundle["exemplars"]), "unbounded exemplar purity basis")
    require(not (module.BASE.nested_keys(bundle["queue"]) & {
        "stratum", "kind", "score", "suggested_outcome", "truth", "change", "control", "candidate",
    }), "blind queue leaked evidence")
    require(report["decision"] in {"DIRECT_TRUTH_V2_READY", "REFERENCE_INSUFFICIENT"}, "invalid terminal decision")
    require(report["invariants"]["v1_primary_interval_overlap_count"] == 0, "reported overlap changed")
    require(all(value is False for value in report["safety"].values()), "safety mutation reported")
    module.BASE.assert_public_safe(report)

    stored = module.BASE.read_json(OUT / "remote_speaker_disjoint_truth_report.json")
    require(module.BASE.pretty_json(report) == module.BASE.pretty_json(stored), "stored report is stale")
    replay = module.BASE.read_json(OUT / "replay_report.json")
    require(replay["byte_exact"] is True, "replay is not byte exact")
    require(replay["candidate_pack_sha256"] == module.BASE.sha256(OUT / "private/candidate_pack.frozen.json"), "candidate hash changed")
    print("remote speaker disjoint truth v2 checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
