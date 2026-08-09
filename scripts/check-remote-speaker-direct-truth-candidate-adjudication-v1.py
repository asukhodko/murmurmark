#!/usr/bin/env python3
"""Fixture checks for direct-truth remote speaker candidate adjudication."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/adjudicate-remote-speaker-direct-truth-candidate-v1.py"


def dump(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def dump_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source(path: Path, source_id: str) -> dict:
    return {
        "id": source_id,
        "path": str(path.relative_to(ROOT)),
        "bytes": path.stat().st_size,
        "sha256": digest(path),
    }


def run(policy: Path, out: Path, action: str, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), action, "--policy", str(policy), "--out-dir", str(out), *extra],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def fixture(root: Path, *, advance: bool) -> tuple[Path, Path, Path]:
    source_dir = root / "source"
    guard = source_dir / "guard.bin"
    guard.parent.mkdir(parents=True)
    guard.write_bytes(b"immutable-production")
    guard_artifact = source(guard, "production_guard")

    definitions = (
        [
            ("new_1", "newly_accepted", "remote_speaker_01", None, "remote_speaker_01"),
            ("new_2", "newly_accepted", "remote_speaker_02", None, "remote_speaker_02"),
            ("stable_1", "stable_accept", "remote_speaker_01", "remote_speaker_01", "remote_speaker_01"),
            ("stable_2", "stable_abstention", "remote_speaker_01", None, None),
            ("unknown", "stable_abstention", "unknown_speaker", None, None),
            ("mixed", "mixed_candidate", "mixed", None, None),
        ]
        if advance
        else [
            ("new_1", "newly_accepted", "remote_speaker_01", None, "remote_speaker_01"),
            ("new_2", "newly_accepted", "unknown_speaker", None, "remote_speaker_01"),
            ("removed_1", "removed_acceptance", "remote_speaker_01", "remote_speaker_01", None),
            ("removed_2", "removed_acceptance", "unusable", "remote_speaker_01", None),
            ("stable_1", "stable_accept", "remote_speaker_01", "remote_speaker_01", "remote_speaker_01"),
            ("mixed", "mixed_candidate", "mixed", None, None),
        ]
    )
    selection: list[dict] = []
    slots: list[dict] = []
    answers: list[dict] = []
    comparisons: list[dict] = []
    for index, (item_id, stratum, truth, control, candidate) in enumerate(definitions):
        selection.append({
            "item_id": item_id,
            "session_id": f"fixture_session_{index % 2}",
            "stratum": stratum,
            "word_count": 1,
            "coverage_weight_sec": 1.0,
            "source_item_sha256": hashlib.sha256(item_id.encode()).hexdigest(),
        })
        slot_id = f"slot_{item_id}"
        slots.append({"item_id": item_id, "slot_id": slot_id, "kind": "primary"})
        answers.append({"slot_id": slot_id, "outcome": truth, "truth_grade": "human_reviewed"})
        comparisons.append({
            "item_id": item_id,
            "change": stratum if stratum in {"newly_accepted", "removed_acceptance"} else "unchanged",
            "control": {"speaker_id": control},
            "candidate": {"speaker_id": candidate},
        })
    for repeat_index in (0, 1):
        item_id = definitions[repeat_index][0]
        slot_id = f"repeat_{item_id}"
        slots.append({"item_id": item_id, "slot_id": slot_id, "kind": "repeat"})
        answers.append({"slot_id": slot_id, "outcome": definitions[repeat_index][2], "truth_grade": "human_reviewed"})

    paths = {
        "direct_truth_selection": source_dir / "selection.jsonl",
        "direct_truth_slot_map": source_dir / "slots.jsonl",
        "direct_truth_answers": source_dir / "answers.jsonl",
        "enrollment_item_comparison": source_dir / "comparison.jsonl",
    }
    dump_jsonl(paths["direct_truth_selection"], selection)
    dump_jsonl(paths["direct_truth_slot_map"], slots)
    dump_jsonl(paths["direct_truth_answers"], answers)
    dump_jsonl(paths["enrollment_item_comparison"], comparisons)

    pack_path = source_dir / "pack.json"
    enrollment_input = source_dir / "enrollment-input.json"
    direct_report = source_dir / "direct-report.json"
    direct_replay = source_dir / "direct-replay.json"
    enrollment_report = source_dir / "enrollment-report.json"
    coverage_report = source_dir / "coverage-report.json"
    dump(pack_path, {"frozen_artifacts": {"guard": guard_artifact}})
    dump(enrollment_input, {"inherited_artifacts": [guard_artifact]})
    dump(direct_report, {
        "decision": "DIRECT_TRUTH_SEED_READY",
        "invariants": {"fixture": True},
        "gates": {"fixture": True},
    })
    dump(direct_replay, {"byte_exact": True})
    dump(enrollment_report, {
        "candidate": {"id": "contrastive_reliability_weighted_centroid_v1"},
        "safety": {"thresholds_tuned": False, "production_mutated": False},
    })
    dump(coverage_report, {"decision": "PROMOTE", "gates": {"fixture": True}})
    paths.update({
        "direct_truth_pack": pack_path,
        "enrollment_input_manifest": enrollment_input,
        "direct_truth_report": direct_report,
        "direct_truth_replay": direct_replay,
        "enrollment_report": enrollment_report,
        "coverage_v3_report": coverage_report,
    })

    strata: dict[str, int] = {}
    for _, stratum, *_ in definitions:
        strata[stratum] = strata.get(stratum, 0) + 1
    policy = root / "policy.json"
    dump(policy, {
        "schema": "murmurmark.remote_speaker_direct_truth_candidate_adjudication_policy/v1",
        "version": "fixture",
        "state": "frozen_before_candidate_evaluation",
        "purpose": "fixture",
        "sources": [source(path, source_id) for source_id, path in sorted(paths.items())],
        "scope": {
            "source_items": 6,
            "source_words": 6,
            "primary_items": 6,
            "repeat_items": 2,
            "changed_items": sum(stratum in {"newly_accepted", "removed_acceptance"} for _, stratum, *_ in definitions),
            "attributed_primary_items": sum(truth.startswith("remote_speaker_") for _, _, truth, *_ in definitions),
            "pack_artifacts": 1,
            "inherited_artifacts": 1,
            "strata": strata,
        },
        "truth": {
            "positive_prefix": "remote_speaker_",
            "fail_closed_outcomes": ["unknown_speaker", "mixed", "unusable"],
            "truth_grade": "human_reviewed",
            "repeat_consistency_floor": 1.0,
            "repeats_count_for_identity_metrics": False,
        },
        "candidate": {
            "id": "contrastive_reliability_weighted_centroid_v1",
            "threshold_tuning_allowed": False,
            "post_hoc_tuning_allowed": False,
            "item_embeddings_recomputed": False,
        },
        "decision": {
            "allowed_outcomes": ["ADVANCE_DIRECT_TRUTH_IDENTITY", "KEEP_COVERAGE_V3", "EVIDENCE_BOUND"],
            "minimum_additional_correct_identity_items": 2,
            "minimum_additional_correct_identity_ratio": 0.2,
            "require_no_new_false_identity": True,
            "require_no_lost_correct_control_identity": True,
            "require_no_fail_closed_acceptance_regression": True,
            "require_all_changed_items_adjudicated": True,
            "require_all_primary_items_adjudicated": True,
            "require_frozen_artifacts_verified": True,
            "require_deterministic_replay": True,
            "production_promotion_allowed": False,
        },
        "privacy": {},
        "safety": {},
    })
    return policy, root / "out", guard


def main() -> int:
    with tempfile.TemporaryDirectory(prefix=".direct-truth-adjudication-fixture-", dir=ROOT) as temporary:
        base = Path(temporary)
        advance_policy, advance_out, _ = fixture(base / "advance", advance=True)
        advance_manifest = base / "advance-manifest.json"
        advanced = run(advance_policy, advance_out, "all", "--write-manifest", str(advance_manifest))
        assert advanced.returncode == 0, advanced.stdout + advanced.stderr
        advance_report = json.loads((advance_out / "remote_speaker_direct_truth_candidate_adjudication_report.json").read_text())
        assert advance_report["decision"] == "ADVANCE_DIRECT_TRUTH_IDENTITY", advance_report
        assert advance_report["replay_verified"] is True

        keep_policy, keep_out, guard = fixture(base / "keep", advance=False)
        kept = run(keep_policy, keep_out, "all")
        assert kept.returncode == 0, kept.stdout + kept.stderr
        keep_report_path = keep_out / "remote_speaker_direct_truth_candidate_adjudication_report.json"
        keep_report = json.loads(keep_report_path.read_text())
        assert keep_report["decision"] == "KEEP_COVERAGE_V3", keep_report
        assert keep_report["comparison"]["lost_correct_control_identity_items"] == 1
        assert keep_report["comparison"]["candidate_fail_closed_unsafe_acceptance_items"] == 1
        assert "fixture_session" not in keep_report_path.read_text()

        guard.write_bytes(b"tampered")
        bounded = run(keep_policy, keep_out, "preflight")
        assert bounded.returncode == 2, bounded.stdout + bounded.stderr
        assert "EVIDENCE_BOUND" in bounded.stdout

        bounded_all = run(keep_policy, keep_out, "all")
        assert bounded_all.returncode == 2, bounded_all.stdout + bounded_all.stderr
        bounded_report = json.loads(keep_report_path.read_text())
        assert bounded_report["decision"] == "EVIDENCE_BOUND", bounded_report
        assert bounded_report["safety"]["production_mutated"] is False
        assert bounded_report["replay_verified"] is True

    print("remote speaker direct-truth candidate adjudication checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
