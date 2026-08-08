#!/usr/bin/env python3
"""Fail-closed fixture checks for Remote Speaker Direct Truth Seed v1."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/build-remote-speaker-direct-truth-seed-v1.py"


def dump(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def dump_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fp(path: Path, artifact_id: str | None = None) -> dict:
    result = {"path": str(path), "bytes": path.stat().st_size, "sha256": digest(path)}
    if artifact_id:
        result["id"] = artifact_id
    return result


def run(policy: Path, out: Path, action: str, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), action, *extra, "--policy", str(policy), "--out-dir", str(out)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def build_fixture(root: Path) -> tuple[Path, Path, Path]:
    source = root / "source"
    audio = source / "audio"
    audio.mkdir(parents=True)
    guard = source / "guard.bin"
    guard.write_bytes(b"immutable-production-guard")

    sessions = ("fixture_session_01", "fixture_session_02", "fixture_session_03")
    rows: list[dict] = []
    comparisons: list[dict] = []
    definitions = [
        (sessions[0], "new", "newly_accepted", None, "remote_speaker_01", "accepted_centroid", []),
        (sessions[1], "removed", "removed_acceptance", "remote_speaker_01", None, "accepted_centroid", []),
        (sessions[0], "accept_a", "unchanged", "remote_speaker_01", "remote_speaker_01", "accepted_centroid", []),
        (sessions[1], "accept_b", "unchanged", "remote_speaker_01", "remote_speaker_01", "accepted_centroid", []),
        (sessions[0], "abstain_a", "unchanged", None, None, "open_set_abstention", []),
        (sessions[1], "abstain_b", "unchanged", None, None, "open_set_abstention", []),
        (sessions[2], "mixed", "unchanged", None, None, "open_set_abstention", ["protected_remote_overlap"]),
        (sessions[2], "unusable", "unchanged", None, None, "embedding_unavailable", ["embedding_unavailable"]),
    ]
    for index, (session_id, suffix, change, control_speaker, candidate_speaker, reason, causes) in enumerate(definitions):
        item_id = f"fixture_{suffix}"
        clip = audio / f"{item_id}.wav"
        clip.write_bytes(b"RIFF" + bytes([index]) * 64)
        audio_fp = fp(clip)
        item = {
            "schema": "fixture.review_item/v1",
            "item_id": item_id,
            "item_sha256": hashlib.sha256(item_id.encode()).hexdigest(),
            "session_id": session_id,
            "utterance_id": f"u{index}",
            "start": float(index),
            "end": float(index + 1),
            "coverage_weight_sec": 1.0,
            "word_count": 1,
            "word_ids": [f"w{index}"],
            "speaker_choices": ["remote_speaker_01"],
            "baseline_causes": causes,
            "audio": audio_fp,
        }
        rows.append(item)
        comparisons.append({
            "schema": "fixture.comparison/v1",
            "item_id": item_id,
            "session_id": session_id,
            "utterance_id": item["utterance_id"],
            "start": item["start"],
            "end": item["end"],
            "coverage_weight_sec": 1.0,
            "word_count": 1,
            "word_ids": item["word_ids"],
            "change": change,
            "in_enrollment_scope": change == "newly_accepted",
            "control": {"speaker_id": control_speaker, "reason": reason},
            "candidate": {"speaker_id": candidate_speaker, "reason": "accepted_centroid" if candidate_speaker else "open_set_abstention"},
        })

    exemplars: list[dict] = []
    for index, session_id in enumerate(sessions):
        clip = audio / f"exemplar_{index}.wav"
        clip.write_bytes(b"RIFFexemplar" + bytes([index]) * 64)
        exemplars.append({
            "schema": "fixture.exemplar/v1",
            "session_id": session_id,
            "speaker_id": "remote_speaker_01",
            "utterance_id": f"e{index}",
            "audio": fp(clip),
        })

    enrollment_policy = source / "enrollment-policy.json"
    tracked = source / "enrollment-manifest.json"
    input_manifest = source / "enrollment-input.json"
    comparison_path = source / "comparison.jsonl"
    centroids = source / "centroids.json"
    enrollment_report = source / "enrollment-report.json"
    residual_pack = source / "residual-pack.json"
    review_path = source / "review-items.jsonl"
    exemplar_path = source / "exemplars.jsonl"
    residual_report = source / "residual-report.json"
    dump(enrollment_policy, {"schema": "fixture.enrollment-policy/v1"})
    dump(tracked, {"schema": "fixture.enrollment-manifest/v1"})
    dump(input_manifest, {"schema": "fixture.input/v1", "inherited_artifacts": [fp(guard, "production_guard")]})
    dump_jsonl(comparison_path, comparisons)
    dump(centroids, {"schema": "fixture.centroids/v1"})
    dump(enrollment_report, {"decision": "DO_NOT_ADVANCE_ENROLLMENT_HARDENING"})
    dump_jsonl(review_path, rows)
    dump_jsonl(exemplar_path, exemplars)
    dump(residual_pack, {
        "schema": "murmurmark.remote_speaker_residual_reference_pack/v1",
        "artifacts": {"review_items": fp(review_path), "speaker_exemplars": fp(exemplar_path)},
    })
    dump(residual_report, {"decision": "REFERENCE_INSUFFICIENT"})

    sources = []
    for artifact_id, path in (
        ("enrollment_policy", enrollment_policy),
        ("enrollment_tracked_manifest", tracked),
        ("enrollment_input_manifest", input_manifest),
        ("enrollment_item_comparison", comparison_path),
        ("enrollment_candidate_centroids", centroids),
        ("enrollment_report", enrollment_report),
        ("residual_pack", residual_pack),
        ("residual_review_items", review_path),
        ("residual_speaker_exemplars", exemplar_path),
        ("residual_reference_report", residual_report),
    ):
        sources.append(fp(path, artifact_id))

    policy = root / "policy.json"
    dump(policy, {
        "schema": "murmurmark.remote_speaker_direct_truth_seed_policy/v1",
        "state": "frozen_before_direct_labels",
        "sources": sources,
        "frozen_scope": {
            "items": 8, "words": 8, "seconds": 8.0, "enrollment_exemplars": 3,
            "newly_accepted_items": 1, "removed_control_acceptances": 1, "changed_sessions": 2,
        },
        "selection": {
            "id": "fixture_selection_v1",
            "selection_salt": "fixture-selection",
            "include_all_changes": ["newly_accepted", "removed_acceptance"],
            "stable_accepted_per_changed_session": 1,
            "stable_abstentions_per_changed_session": 1,
            "mixed_candidate_sessions": 1,
            "mixed_candidate_cause": "protected_remote_overlap",
            "include_all_embedding_unavailable": True,
            "expected_seed_items": 8,
            "expected_seed_words": 8,
            "expected_seed_seconds": 8.0,
            "minimum_sessions": 3,
            "expected_sessions": 3,
            "forbidden_selection_inputs": ["speech_text", "human_name", "direct_truth", "future_answers", "candidate_correctness"],
        },
        "repeat_review": {
            "selection_salt": "fixture-repeat",
            "stratum_counts": {
                "newly_accepted": 1, "removed_acceptance": 1, "stable_accept": 1,
                "stable_abstention": 1, "mixed_candidate": 1, "unusable_candidate": 1,
            },
            "expected_repeat_items": 6,
            "minimum_consistency": 1.0,
        },
        "review": {
            "truth_grades": ["human_reviewed"],
            "special_outcomes": ["unknown_speaker", "mixed", "unusable"],
            "blind_forbidden_keys": [
                "stratum", "change", "control", "candidate", "reference", "truth", "score",
                "similarity", "margin", "suggested_outcome", "transcript_fragment",
            ],
            "show_model_suggestion": False,
            "allow_human_names": False,
            "allow_cross_session_identity": False,
        },
        "readiness": {
            "required_primary_answers": 8,
            "required_repeat_answers": 6,
            "required_changed_answers": 2,
            "minimum_attributed_primary_answers": 2,
            "minimum_consistency": 1.0,
            "require_all_seed_words_once": True,
            "require_all_changed_items_once": True,
            "require_exact_source_conservation": True,
            "require_blind_prediction_separation": True,
            "require_selected_transcript_unchanged": True,
            "require_raw_audio_unchanged": True,
        },
        "decision": {
            "allowed_outcomes": ["DIRECT_TRUTH_SEED_READY", "REFERENCE_INSUFFICIENT", "EVIDENCE_BOUND"],
            "production_promotion_allowed": False,
        },
        "safety": {},
    })
    return policy, root / "out", guard


def main() -> int:
    with tempfile.TemporaryDirectory(prefix=".direct-truth-seed-fixture-", dir=ROOT) as temporary:
        policy, out, guard = build_fixture(Path(temporary))
        manifest = Path(temporary) / "tracked.json"
        built = run(policy, out, "all", "--write-manifest", str(manifest))
        assert built.returncode == 0, built.stdout + built.stderr
        report_path = out / "remote_speaker_direct_truth_seed_report.json"
        report = json.loads(report_path.read_text())
        assert report["decision"] == "REFERENCE_INSUFFICIENT", report
        assert report["scope"]["seed_items"] == 8
        assert report["scope"]["repeat_items"] == 6

        queue = [json.loads(line) for line in (out / "private/review_queue.jsonl").read_text().splitlines()]
        forbidden = {"stratum", "change", "control", "candidate", "reference", "truth", "score", "similarity", "margin", "suggested_outcome", "transcript_fragment"}
        assert not (set().union(*(set(row) for row in queue)) & forbidden)
        next_result = run(policy, out, "next")
        assert next_result.returncode == 0
        assert "suggest" not in next_result.stdout.lower()
        assert "stratum" not in next_result.stdout.lower()

        slot_map = [json.loads(line) for line in (out / "private/slot_map.jsonl").read_text().splitlines()]
        answers = [
            {
                "schema": "murmurmark.remote_speaker_direct_truth_answer/v1",
                "slot_id": row["slot_id"],
                "outcome": "remote_speaker_01",
                "truth_grade": "human_reviewed",
                "reviewed_at": "2026-08-09T00:00:00Z",
            }
            for row in queue
        ]
        dump_jsonl(out / "private/answers.jsonl", answers)
        final = run(policy, out, "finalize", "--write-manifest", str(manifest))
        assert final.returncode == 0, final.stdout + final.stderr
        assert json.loads(report_path.read_text())["decision"] == "DIRECT_TRUTH_SEED_READY"

        repeat_slot = next(row["slot_id"] for row in slot_map if row["kind"] == "repeat")
        changed_answers = [dict(row, outcome="unknown_speaker") if row["slot_id"] == repeat_slot else row for row in answers]
        dump_jsonl(out / "private/answers.jsonl", changed_answers)
        inconsistent = run(policy, out, "finalize")
        assert inconsistent.returncode == 0
        assert json.loads(report_path.read_text())["decision"] == "REFERENCE_INSUFFICIENT"
        dump_jsonl(out / "private/answers.jsonl", answers)
        assert run(policy, out, "finalize").returncode == 0

        preserved = run(policy, out, "build")
        assert preserved.returncode == 0, preserved.stdout + preserved.stderr
        assert json.loads(report_path.read_text())["decision"] == "DIRECT_TRUTH_SEED_READY"

        frozen_clip = next((out / "private/clips").rglob("*.wav"))
        original_clip = frozen_clip.read_bytes()
        frozen_clip.write_bytes(original_clip + b"tamper")
        tampered_pack = run(policy, out, "replay")
        assert tampered_pack.returncode == 2
        frozen_clip.write_bytes(original_clip)
        assert run(policy, out, "replay").returncode == 0

        original_guard = guard.read_bytes()
        guard.write_bytes(original_guard + b"tamper")
        tampered_source = run(policy, out, "preflight")
        assert tampered_source.returncode == 2
        guard.write_bytes(original_guard)

        public = report_path.read_text() + manifest.read_text()
        for marker in ("fixture_session_", "PRIVATE_REVIEWER_NAME_SENTINEL", "transcript_fragment"):
            assert marker not in public

    print("remote speaker direct truth seed v1 checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
