#!/usr/bin/env python3
"""Regression checks for Independent Remote Speaker Evidence v1."""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv/bin/python"
AUDIT = ROOT / "scripts/audit-independent-remote-speaker-evidence-v1.py"
CORPUS = ROOT / "scripts/report-independent-remote-speaker-evidence-v1-corpus.py"
V3_AUDIT = ROOT / "scripts/audit-remote-speaker-coverage-v3.py"
V3_CHECK = ROOT / "scripts/check-remote-speaker-coverage-v3.py"
V3_DIR = Path("derived/audit/remote-speaker-coverage-v3")
INDEPENDENT_DIR = Path("derived/audit/independent-remote-speaker-evidence-v1")


def load_v3_check() -> Any:
    spec = importlib.util.spec_from_file_location("murmurmark_check_remote_coverage_v3", V3_CHECK)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot_load_v3_check")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


V3 = load_v3_check()


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


def refresh_v2_source(session: Path, v2: Path, rows: list[dict[str, Any]]) -> None:
    v1_path = session / "v1_rows.jsonl"
    write_jsonl(v1_path, rows)
    report_path = v2 / "report.json"
    report = read_json(report_path)
    report["source"]["v1_attribution"] = V3.fp(v1_path, session)
    write_json(report_path, report)
    manifest_path = v2 / "artifact_manifest.json"
    manifest = read_json(manifest_path)
    manifest["artifacts"]["report.json"] = V3.sha256(report_path)
    write_json(manifest_path, manifest)


def enrollment_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for speaker, offset in (("remote_speaker_01", 0.0), ("remote_speaker_02", 30.0)):
        bucket_counts = {bucket: 0 for bucket in range(4)}
        candidate = 0
        while min(bucket_counts.values()) < 2:
            uid = f"{speaker}_enroll_{candidate + 1}"
            bucket = int(hashlib.sha256(uid.encode()).hexdigest()[:8], 16) % 4
            candidate += 1
            if bucket_counts[bucket] >= 2:
                continue
            index = len([row for row in rows if row["speaker_id"] == speaker])
            bucket_counts[bucket] += 1
            rows.append(
                {
                    "utterance_id": uid,
                    "speaker_id": speaker,
                    "status": "attributed",
                    "start": offset + index * 2.0,
                    "end": offset + index * 2.0 + 1.5,
                    "overlap_utterance_ids": [],
                }
            )
    return rows


def embedding_fixture(path: Path) -> None:
    embeddings: dict[str, list[float]] = {}
    for row in enrollment_rows():
        speaker = str(row["speaker_id"])
        embeddings[f"enroll:{speaker}:{row['utterance_id']}"] = (
            [1.0, 0.0, 0.0] if speaker.endswith("01") else [0.0, 1.0, 0.0]
        )
    for unit in range(1, 5):
        for window in ("exact", "compact", "context", "left_half", "right_half"):
            embeddings[f"residual:residual_{unit:06d}:{window}"] = [0.999, 0.01, 0.0]
    write_json(
        path,
        {
            "schema": "murmurmark.independent_remote_speaker_embedding_fixture/v1",
            "embeddings": embeddings,
        },
    )


def check_auditor(root: Path) -> None:
    session = root / "audit-session"
    v2 = V3.build_v2_fixture(session)
    refresh_v2_source(session, v2, enrollment_rows())
    run([str(V3_AUDIT), str(session)])
    v3 = session / V3_DIR
    baseline_words = {row["word_id"]: row for row in read_jsonl(v3 / "word_attribution.jsonl")}
    fixture = root / "embeddings.json"
    embedding_fixture(fixture)

    run([str(AUDIT), str(session), "--embedding-fixture", str(fixture)])
    out = session / INDEPENDENT_DIR
    report = read_json(out / "report.json")
    assert report["decision"] == "PUBLISH_EVIDENCE"
    assert report["summary"]["recovered_words"] >= 1, {
        "report": report,
        "units": read_jsonl(out / "residual_units.jsonl"),
    }
    assert report["gates"]["baseline_attributions_preserved"] is True
    assert report["gates"]["protected_causes_preserved"] is True
    words = {row["word_id"]: row for row in read_jsonl(out / "word_attribution.jsonl")}
    for word_id, baseline in baseline_words.items():
        assert words[word_id]["text"] == baseline["text"]
        assert words[word_id]["start"] == baseline["start"]
        assert words[word_id]["end"] == baseline["end"]
        if baseline.get("speaker_id"):
            assert words[word_id]["speaker_id"] == baseline["speaker_id"]
            assert words[word_id]["reason"] == baseline["reason"]
    protected = [
        row
        for row in words.values()
        if baseline_words[row["word_id"]].get("v3_reason") == "protected_remote_overlap"
    ]
    assert protected and all(row.get("speaker_id") is None for row in protected)

    run([str(AUDIT), str(session), "--verify-only"])
    run([str(AUDIT), str(session), "--verify-only", "--require-promoted"], expected=2)
    replay = session / "derived/audit/independent-remote-speaker-evidence-v1-replay"
    run(
        [
            str(AUDIT),
            str(session),
            "--embedding-fixture",
            str(fixture),
            "--out-dir",
            str(replay),
        ]
    )
    for name in (
        "report.json",
        "residual_units.jsonl",
        "residual_decisions.jsonl",
        "word_attribution.jsonl",
        "transcript.rich.shadow.json",
    ):
        assert (out / name).read_bytes() == (replay / name).read_bytes()

    missing = session / "derived/audit/independent-remote-speaker-evidence-v1-missing"
    run(
        [
            str(AUDIT),
            str(session),
            "--embedding-fixture",
            str(root / "missing.json"),
            "--out-dir",
            str(missing),
        ]
    )
    assert read_json(missing / "report.json")["decision"] == "FALLBACK_V3"
    missing_causes = read_json(missing / "cause_ceiling.json")
    assert missing_causes["baseline_unknown_words"] == len(
        [row for row in baseline_words.values() if row.get("speaker_id") is None]
    )
    assert missing_causes["causes"][0]["cause"] == "fallback_v3"
    assert (missing / "word_attribution.jsonl").read_bytes() == (
        v3 / "word_attribution.jsonl"
    ).read_bytes()

    stale = session / "derived/audit/independent-remote-speaker-evidence-v1-stale"
    (v3 / "recovery_decisions.jsonl").write_text(
        (v3 / "recovery_decisions.jsonl").read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )
    run(
        [
            str(AUDIT),
            str(session),
            "--embedding-fixture",
            str(fixture),
            "--out-dir",
            str(stale),
        ]
    )
    assert read_json(stale / "report.json")["decision"] == "FALLBACK_V3"


def materialize_independent_fixture(session: Path) -> None:
    v3 = session / V3_DIR
    out = session / INDEPENDENT_DIR
    out.mkdir(parents=True)
    words = read_jsonl(v3 / "word_attribution.jsonl")
    baseline_unknown_words = sum(row.get("speaker_id") is None for row in words)
    baseline_unknown_seconds = sum(
        float(row.get("coverage_weight_sec") or 0)
        for row in words
        if row.get("speaker_id") is None
    )
    by_utterance: dict[str, list[dict[str, Any]]] = defaultdict(list)
    decisions: list[dict[str, Any]] = []
    for source in words:
        word = deepcopy(source)
        word["schema"] = "murmurmark.independent_remote_speaker_word/v1"
        if word.get("speaker_id") is None:
            siblings = by_utterance[str(word["utterance_id"])]
            speaker = next(
                str(row["speaker_id"])
                for row in reversed(siblings)
                if row.get("speaker_id") is not None
            )
            word["speaker_id"] = speaker
            word["speaker_label"] = speaker
            word["status"] = "attributed"
            word["independent_v1_reason"] = "fixture_recovery"
            decisions.append(
                {
                    "schema": "murmurmark.independent_remote_speaker_decision/v1",
                    "word_id": word["word_id"],
                    "utterance_id": word["utterance_id"],
                    "outcome": "attributed",
                    "speaker_id": speaker,
                    "coverage_weight_sec": word.get("coverage_weight_sec", 0.0),
                    "reason": "fixture_recovery",
                }
            )
        by_utterance[str(word["utterance_id"])].append(word)
    all_words = [word for uid in sorted(by_utterance) for word in by_utterance[uid]]

    selected = read_json(session / "dialogue.json")["utterances"]
    rich_rows = deepcopy(selected)
    attributions = []
    weights: dict[str, float] = defaultdict(float)
    internal = 0
    for row in rich_rows:
        uid = str(row["id"])
        utterance_words = by_utterance[uid]
        row["speaker_turns"] = V3.turns(str(row["text"]), utterance_words)
        speakers = {str(word["speaker_id"]) for word in utterance_words}
        internal += len(speakers) > 1
        for word in utterance_words:
            weights[str(word["speaker_id"])] += float(word["coverage_weight_sec"])
        dominant = next(iter(speakers)) if len(speakers) == 1 else None
        attributions.append(
            {
                "schema": "murmurmark.independent_remote_speaker_utterance/v1",
                "utterance_id": uid,
                "start": row["start"],
                "end": row["end"],
                "speaker_id": dominant,
                "speaker_label": dominant or "Colleagues",
                "status": "attributed" if dominant else "mixed",
                "speaker_turns": row["speaker_turns"],
                "attributed_weight_sec": 5.0,
                "total_weight_sec": 5.0,
            }
        )
    speakers = [
        {"speaker_id": speaker, "attributed_speech_sec": seconds, "seed_units": 5}
        for speaker, seconds in sorted(weights.items())
    ]
    source = {
        "session_id": session.name,
        "dialogue": V3.fp(session / "dialogue.json", session),
        "embedding_backend": {
            "method": "wavlm_xvector_independent_v1",
            "model": {"tree_sha256": "fixture-model-tree-sha256"},
        },
    }
    summary = {
        "remote_words": len(all_words),
        "attributed_words": len(all_words),
        "baseline_unknown_words": baseline_unknown_words,
        "baseline_unknown_seconds": baseline_unknown_seconds,
        "recovered_words": baseline_unknown_words,
        "recovered_seconds": baseline_unknown_seconds,
        "remaining_unknown_words": 0,
        "remaining_unknown_seconds": 0.0,
        "remote_speech_sec": float(len(all_words)),
        "attributed_speech_sec": float(len(all_words)),
        "attributable_remote_speech_ratio": 1.0,
        "published_speakers": len(speakers),
        "internal_change_utterances": internal,
    }
    report = {
        "schema": "murmurmark.independent_remote_speaker_evidence_report/v1",
        "status": "completed",
        "decision": "PUBLISH_EVIDENCE",
        "source": source,
        "summary": summary,
        "gates": {
            "publish_session_evidence": True,
            "word_conservation": True,
            "timestamp_order": True,
            "baseline_attributions_preserved": True,
            "protected_causes_preserved": True,
            "complete_split_enrollment": True,
            "enrollment_test_accepted_precision": True,
            "aggregate_fallback_exact": True,
        },
        "safety": {"raw_audio_unchanged": True},
    }
    write_jsonl(out / "word_attribution.jsonl", all_words)
    write_jsonl(out / "residual_decisions.jsonl", decisions)
    write_jsonl(out / "utterance_attribution.jsonl", attributions)
    write_json(out / "speaker_map.json", {"speakers": speakers})
    write_json(out / "transcript.rich.shadow.json", {"utterances": rich_rows})
    write_json(out / "report.json", report)
    write_json(
        out / "cause_ceiling.json",
        {
            "baseline_unknown_words": len(attributions),
            "baseline_unknown_seconds": float(len(attributions)),
            "causes": [
                {
                    "cause": "margin_below_threshold",
                    "baseline_words": len(attributions),
                    "baseline_seconds": float(len(attributions)),
                    "recovered_words": len(attributions),
                    "recovered_seconds": float(len(attributions)),
                    "remaining_words": 0,
                    "remaining_seconds": 0.0,
                }
            ],
            "failure_reasons": {},
        },
    )
    write_json(out / "artifact_manifest.json", {"schema": "fixture", "artifacts": {}})


def check_corpus(root: Path) -> None:
    sessions_root = root / "sessions"
    manifest_rows = []
    reference_rows = []
    for index in range(6):
        session_id = f"fixture-{index + 1}"
        group = index == 0
        count = 60 if group else 1
        references = V3.build_corpus_session(sessions_root / session_id, count, group)
        materialize_independent_fixture(sessions_root / session_id)
        if group:
            predicted_by_utterance = {
                str(row["utterance_id"]): row.get("speaker_id")
                for row in read_jsonl(
                    sessions_root / session_id / INDEPENDENT_DIR / "utterance_attribution.jsonl"
                )
            }
            reference_rows = [
                {
                    **row,
                    "predicted_speaker": predicted_by_utterance.get(str(row["utterance_id"])),
                }
                for row in references
            ]
        manifest_rows.append(
            {
                "session_id": session_id,
                "expected_speakers": {"min": 2 if group else 1, "max": 2 if group else 1},
            }
        )
    total_words = 65 * 5
    baseline_manifest = root / "baseline-manifest.json"
    baseline_report = root / "baseline-report.json"
    baseline_reference = root / "baseline-reference.json"
    v2_manifest = root / "v2-manifest.json"
    boundaries = root / "boundaries.json"
    policy = root / "policy.json"
    write_json(baseline_manifest, {"decision": "PROMOTE", "sessions": manifest_rows})
    write_json(
        baseline_report,
        {
            "decision": "PROMOTE",
            "summary": {
                "remaining_unknown_words": 65,
                "remaining_unknown_seconds": 65.0,
            },
        },
    )
    write_json(
        baseline_reference,
        {
            "session_id": "fixture-1",
            "rows": reference_rows,
            "attributed_only": {
                "bcubed": {"f1": 1.0},
                "pairwise": {"precision": 1.0},
            },
        },
    )
    write_json(v2_manifest, {"sessions": manifest_rows})
    write_json(
        policy,
        {
            "schema": "murmurmark.independent_remote_speaker_policy/v1",
            "corpus_split": {
                "development": ["fixture-1", "fixture-2", "fixture-3"],
                "held_out": ["fixture-4", "fixture-5", "fixture-6"],
            },
            "promotion": {
                "minimum_unknown_word_reduction_ratio": 0.2,
                "minimum_unknown_second_reduction_ratio": 0.2,
                "minimum_direct_reference_words": 20,
                "minimum_direct_reference_precision": 0.98,
                "required_sessions": 6,
                "required_internal_boundaries": 1,
            },
        },
    )
    write_json(
        boundaries,
        {
            "schema": "murmurmark.remote_speaker_boundary_cases/v2",
            "cases": [
                {
                    "session_id": "fixture-1",
                    "utterance_id": "remote_0000",
                    "selected_text": "one two three four five",
                    "min_supported_runs": 2,
                    "min_distinct_speakers": 2,
                }
            ],
        },
    )
    output = root / "corpus-report"
    frozen = root / "frozen.json"
    common = [
        str(CORPUS),
        "all",
        "--sessions-root",
        str(sessions_root),
        "--baseline-manifest",
        str(baseline_manifest),
        "--baseline-report",
        str(baseline_report),
        "--baseline-reference",
        str(baseline_reference),
        "--v2-manifest",
        str(v2_manifest),
        "--boundary-cases",
        str(boundaries),
        "--output",
        str(output),
        "--policy",
        str(policy),
    ]
    run([*common, "--write-manifest", str(frozen)])
    run([*common, "--frozen-manifest", str(frozen)])
    run([*common, "--frozen-manifest", str(frozen), "--verify-existing"])
    report = read_json(output / "independent_remote_speaker_corpus_report.json")
    assert report["decision"] == "PROMOTE_INDEPENDENT_REMOTE_SPEAKER_EVIDENCE_V1"
    assert report["summary"]["remote_words"] == total_words
    assert report["summary"]["unknown_words_reduction_ratio"] == 1.0
    assert report["reference_evaluation"]["attributed_only"]["bcubed"]["f1"] == 1.0

    bad_path = sessions_root / "fixture-2" / INDEPENDENT_DIR / "report.json"
    bad = read_json(bad_path)
    bad["gates"]["baseline_attributions_preserved"] = False
    write_json(bad_path, bad)
    run([*common, "--frozen-manifest", str(frozen)], expected=2)
    assert read_json(output / "independent_remote_speaker_corpus_report.json")["decision"] == (
        "DO_NOT_PROMOTE"
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="murmurmark-independent-remote-speaker-v1-") as temporary:
        root = Path(temporary)
        check_auditor(root)
        check_corpus(root)
    print("independent remote speaker evidence v1 checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
