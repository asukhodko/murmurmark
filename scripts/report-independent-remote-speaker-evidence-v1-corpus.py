#!/usr/bin/env python3
"""Qualify Independent Remote Speaker Evidence v1 against the frozen v3 corpus."""

from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "murmurmark.independent_remote_speaker_corpus_report/v1"
MANIFEST_SCHEMA = "murmurmark.independent_remote_speaker_frozen_manifest/v1"
REFERENCE_SCHEMA = "murmurmark.independent_remote_speaker_reference_evaluation/v1"
CAUSE_MAP_SCHEMA = "murmurmark.independent_remote_speaker_cause_map/v1"
INDEPENDENT_DIR = "derived/audit/independent-remote-speaker-evidence-v1"
DEFAULT_BASELINE_MANIFEST = ROOT / "docs/testing/remote-speaker-coverage-v3-manifest.json"
DEFAULT_BASELINE_REPORT = (
    ROOT / "sessions/_reports/remote-speaker-coverage-v3/remote_speaker_coverage_corpus_report.json"
)
DEFAULT_BASELINE_REFERENCE = ROOT / "sessions/_reports/remote-speaker-coverage-v3/reference_evaluation.json"
DEFAULT_V2_MANIFEST = ROOT / "docs/testing/remote-speaker-diarization-v2-manifest.json"
DEFAULT_BOUNDARIES = ROOT / "docs/testing/remote-speaker-diarization-v2-boundaries.json"
DEFAULT_OUTPUT = ROOT / "sessions/_reports/independent-remote-speaker-evidence-v1"
V3_REPORTER = ROOT / "scripts/report-remote-speaker-coverage-v3-corpus.py"
AUDIT = ROOT / "scripts/audit-independent-remote-speaker-evidence-v1.py"
POLICY = ROOT / "policies/independent-remote-speaker-evidence-v1.json"


def load_v3_reporter() -> Any:
    spec = importlib.util.spec_from_file_location("murmurmark_remote_coverage_v3_report", V3_REPORTER)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot_load_v3_reporter")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module.V3_DIR = INDEPENDENT_DIR
    module.V2.REPORT_DIR = INDEPENDENT_DIR
    return module


V3 = load_v3_reporter()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate Independent Remote Speaker Evidence v1.")
    parser.add_argument("scope", nargs="?", default="all", choices=["all"])
    parser.add_argument("--baseline-manifest", type=Path, default=DEFAULT_BASELINE_MANIFEST)
    parser.add_argument("--baseline-report", type=Path, default=DEFAULT_BASELINE_REPORT)
    parser.add_argument("--baseline-reference", type=Path, default=DEFAULT_BASELINE_REFERENCE)
    parser.add_argument("--v2-manifest", type=Path, default=DEFAULT_V2_MANIFEST)
    parser.add_argument("--boundary-cases", type=Path, default=DEFAULT_BOUNDARIES)
    parser.add_argument("--sessions-root", type=Path, default=ROOT / "sessions")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--write-manifest", type=Path)
    parser.add_argument("--frozen-manifest", type=Path)
    parser.add_argument("--verify-existing", action="store_true")
    parser.add_argument("--policy", type=Path, default=POLICY)
    return parser.parse_args()


def canonical_json(payload: Any) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def artifact(path: Path) -> dict[str, Any]:
    row: dict[str, Any] = {"path": str(path), "exists": path.is_file()}
    if path.is_file():
        row.update({"bytes": path.stat().st_size, "sha256": sha256(path)})
    return row


def portable(row: dict[str, Any]) -> dict[str, Any]:
    result = {key: row[key] for key in ("exists", "bytes", "sha256") if key in row}
    path = str(row.get("path") or "")
    for marker in ("sessions/", "docs/", "scripts/", "policies/"):
        if marker in path:
            result["path"] = marker + path.split(marker, 1)[1]
            break
    return result


def corpus_inputs(
    baseline_manifest: dict[str, Any], v2_manifest: dict[str, Any], sessions_root: Path
) -> tuple[list[Path], dict[str, dict[str, int]]]:
    expected = {
        str(row["session_id"]): {
            "min": int(row["expected_speakers"]["min"]),
            "max": int(row["expected_speakers"]["max"]),
        }
        for row in v2_manifest.get("sessions") or []
    }
    sessions = [sessions_root / str(row["session_id"]) for row in baseline_manifest.get("sessions") or []]
    missing = [session.name for session in sessions if session.name not in expected]
    if missing:
        raise ValueError("expected_speaker_ranges_missing:" + ",".join(missing))
    return sessions, expected


def combined_causes(sessions: list[Path]) -> dict[str, Any]:
    totals: dict[str, dict[str, float | int]] = defaultdict(
        lambda: {
            "baseline_words": 0,
            "baseline_seconds": 0.0,
            "recovered_words": 0,
            "recovered_seconds": 0.0,
            "remaining_words": 0,
            "remaining_seconds": 0.0,
        }
    )
    failure_reasons: dict[str, int] = defaultdict(int)
    session_rows: list[dict[str, Any]] = []
    for session in sessions:
        payload = read_json(session / INDEPENDENT_DIR / "cause_ceiling.json")
        session_rows.append(
            {
                "session_id": session.name,
                "baseline_unknown_words": int(payload["baseline_unknown_words"]),
                "baseline_unknown_seconds": float(payload["baseline_unknown_seconds"]),
                "causes": payload["causes"],
                "failure_reasons": payload.get("failure_reasons") or {},
            }
        )
        for row in payload.get("causes") or []:
            cause = str(row["cause"])
            for key in totals[cause]:
                totals[cause][key] = float(totals[cause][key]) + float(row[key])
        for reason, count in (payload.get("failure_reasons") or {}).items():
            failure_reasons[str(reason)] += int(count)
    return {
        "schema": CAUSE_MAP_SCHEMA,
        "sessions": session_rows,
        "causes": [
            {
                "cause": cause,
                "baseline_words": int(values["baseline_words"]),
                "baseline_seconds": round(float(values["baseline_seconds"]), 6),
                "recovered_words": int(values["recovered_words"]),
                "recovered_seconds": round(float(values["recovered_seconds"]), 6),
                "remaining_words": int(values["remaining_words"]),
                "remaining_seconds": round(float(values["remaining_seconds"]), 6),
            }
            for cause, values in sorted(totals.items())
        ],
        "failure_reasons": dict(sorted(failure_reasons.items())),
    }


def current_manifest(
    sessions: list[Path],
    baseline_manifest: Path,
    baseline_report: Path,
    baseline_reference: Path,
    v2_manifest: Path,
    boundaries: Path,
    policy: Path,
    decision: str,
) -> dict[str, Any]:
    return {
        "schema": MANIFEST_SCHEMA,
        "decision": decision,
        "implementation": {
            "audit": portable(artifact(AUDIT)),
            "corpus_report": portable(artifact(Path(__file__).resolve())),
            "v3_corpus_report_dependency": portable(artifact(V3_REPORTER)),
            "policy": portable(artifact(policy)),
        },
        "baseline": {
            "manifest": portable(artifact(baseline_manifest)),
            "corpus_report": portable(artifact(baseline_report)),
            "reference_evaluation": portable(artifact(baseline_reference)),
            "v2_manifest": portable(artifact(v2_manifest)),
            "boundary_cases": portable(artifact(boundaries)),
        },
        "sessions": [
            {
                "session_id": session.name,
                "report": portable(artifact(session / INDEPENDENT_DIR / "report.json")),
                "manifest": portable(artifact(session / INDEPENDENT_DIR / "artifact_manifest.json")),
                "rich": portable(artifact(session / INDEPENDENT_DIR / "transcript.rich.shadow.json")),
            }
            for session in sessions
        ],
    }


def candidate_reference_evaluation(
    session: Path,
    baseline_reference: dict[str, Any],
) -> dict[str, Any]:
    decisions_path = session / INDEPENDENT_DIR / "residual_decisions.jsonl"
    if not decisions_path.exists():
        return {
            "status": "REFERENCE_INSUFFICIENT",
            "reason": "residual_decisions_missing",
            "reference_session_id": session.name,
            "recovered_words": 0,
            "recovered_seconds": 0.0,
            "covered_words": 0,
            "covered_seconds": 0.0,
            "coverage_ratio_words": 0.0,
            "coverage_ratio_seconds": 0.0,
            "correct_words": 0,
            "correct_seconds": 0.0,
            "precision_words": None,
            "precision_seconds": None,
            "speaker_mapping": {},
        }
    reference_by_utterance = {
        str(row["utterance_id"]): str(row["reference_speaker"])
        for row in baseline_reference.get("rows") or []
        if row.get("utterance_id") and row.get("reference_speaker")
    }
    speaker_votes: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in baseline_reference.get("rows") or []:
        predicted = row.get("predicted_speaker")
        reference = row.get("reference_speaker")
        if predicted and reference:
            speaker_votes[str(predicted)][str(reference)] += 1
    speaker_mapping = {
        speaker: max(votes.items(), key=lambda item: (item[1], item[0]))[0]
        for speaker, votes in sorted(speaker_votes.items())
    }
    decisions = [
        row
        for row in V3.read_jsonl(decisions_path)
        if row.get("outcome") == "attributed" and row.get("speaker_id")
    ]
    covered = [row for row in decisions if str(row["utterance_id"]) in reference_by_utterance]
    correct = [
        row
        for row in covered
        if speaker_mapping.get(str(row["speaker_id"]))
        == reference_by_utterance[str(row["utterance_id"])]
    ]
    recovered_seconds = sum(float(row.get("coverage_weight_sec") or 0) for row in decisions)
    covered_seconds = sum(float(row.get("coverage_weight_sec") or 0) for row in covered)
    correct_seconds = sum(float(row.get("coverage_weight_sec") or 0) for row in correct)
    return {
        "status": "completed" if covered else "REFERENCE_INSUFFICIENT",
        "reference_session_id": session.name,
        "recovered_words": len(decisions),
        "recovered_seconds": round(recovered_seconds, 6),
        "covered_words": len(covered),
        "covered_seconds": round(covered_seconds, 6),
        "coverage_ratio_words": round(len(covered) / len(decisions), 6) if decisions else 0.0,
        "coverage_ratio_seconds": round(covered_seconds / recovered_seconds, 6)
        if recovered_seconds
        else 0.0,
        "correct_words": len(correct),
        "correct_seconds": round(correct_seconds, 6),
        "precision_words": round(len(correct) / len(covered), 6) if covered else None,
        "precision_seconds": round(correct_seconds / covered_seconds, 6)
        if covered_seconds
        else None,
        "speaker_mapping": speaker_mapping,
    }


def report_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    reference = report["reference_evaluation"]["attributed_only"]
    candidate_reference = report["candidate_reference_evaluation"]
    lines = [
        "# Independent Remote Speaker Evidence v1 Corpus",
        "",
        f"Decision: `{report['decision']}`",
        f"Sessions: `{summary['sessions']}`",
        f"Recovered: `{summary['recovered_words']}` words / `{summary['recovered_seconds']:.3f}s`",
        f"Remaining: `{summary['remaining_unknown_words']}` words / `{summary['remaining_unknown_seconds']:.3f}s`",
        f"Unknown reduction: `{summary['unknown_words_reduction_ratio']:.4%}` words / "
        f"`{summary['unknown_seconds_reduction_ratio']:.4%}` seconds",
        f"Attributable remote speech: `{summary['attributable_remote_speech_ratio']:.4%}`",
        f"Attributed-only B-cubed F1: `{reference['bcubed']['f1']:.6f}`",
        "Direct candidate reference: "
        f"`{candidate_reference['covered_words']}` / "
        f"`{candidate_reference['recovered_words']}` recovered words, "
        f"precision `{candidate_reference['precision_words']}`",
        f"Attributed-only pairwise precision: `{reference['pairwise']['precision']:.6f}`",
        "",
        "## Frozen Split",
        "",
    ]
    for name, row in report["split_summary"].items():
        lines.append(
            f"- `{name}`: recovered {row['recovered_words']} words / "
            f"{row['recovered_seconds']:.3f}s; reduction "
            f"{row['unknown_words_reduction_ratio']:.4%} words / "
            f"{row['unknown_seconds_reduction_ratio']:.4%} seconds"
        )
    lines.extend([
        "",
        "## Cause Ceiling",
        "",
    ])
    for row in report["cause_evidence"]["causes"]:
        lines.append(
            f"- `{row['cause']}`: {row['recovered_words']} / {row['baseline_words']} words, "
            f"{row['recovered_seconds']:.3f}s / {row['baseline_seconds']:.3f}s recovered"
        )
    lines.extend(("", "## Gates", ""))
    lines.extend(f"- `{name}`: `{str(value).lower()}`" for name, value in report["gates"].items())
    return "\n".join(lines) + "\n"


def build(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, bytes], dict[str, Any]]:
    baseline_manifest_path = args.baseline_manifest.expanduser().resolve()
    baseline_report_path = args.baseline_report.expanduser().resolve()
    baseline_reference_path = args.baseline_reference.expanduser().resolve()
    v2_manifest_path = args.v2_manifest.expanduser().resolve()
    boundaries_path = args.boundary_cases.expanduser().resolve()
    policy_path = args.policy.expanduser().resolve()
    baseline_manifest = read_json(baseline_manifest_path)
    baseline_report = read_json(baseline_report_path)
    baseline_reference = read_json(baseline_reference_path)
    v2_manifest = read_json(v2_manifest_path)
    policy = read_json(policy_path)
    if policy.get("schema") != "murmurmark.independent_remote_speaker_policy/v1":
        raise ValueError("independent_policy_schema_invalid")
    if baseline_manifest.get("decision") != "PROMOTE" or baseline_report.get("decision") != "PROMOTE":
        raise ValueError("v3_baseline_not_promoted")
    sessions, expected = corpus_inputs(
        baseline_manifest, v2_manifest, args.sessions_root.expanduser().resolve()
    )
    session_ids = {session.name for session in sessions}
    split = policy.get("corpus_split") or {}
    development_ids = [str(value) for value in split.get("development") or []]
    held_out_ids = [str(value) for value in split.get("held_out") or []]
    split_exact = (
        len(development_ids) == 3
        and len(held_out_ids) == 3
        and set(development_ids).isdisjoint(held_out_ids)
        and set(development_ids + held_out_ids) == session_ids
    )
    rows = [V3.V2.session_row(session, expected[session.name]) for session in sessions]
    reference_session_id = str(baseline_reference["session_id"])
    reference_session = next((session for session in sessions if session.name == reference_session_id), None)
    if reference_session is None:
        raise ValueError("reference_session_not_in_corpus")
    reference = V3.reference_evaluation(
        reference_session, baseline_reference, baseline_reference_path
    )
    reference["schema"] = REFERENCE_SCHEMA
    candidate_reference = candidate_reference_evaluation(reference_session, baseline_reference)
    boundaries = V3.V2.boundary_evaluation(
        {session.name: session for session in sessions}, boundaries_path
    )
    causes = combined_causes(sessions)
    baseline_summary = baseline_report["summary"]
    baseline_unknown_words = int(baseline_summary["remaining_unknown_words"])
    baseline_unknown_seconds = float(baseline_summary["remaining_unknown_seconds"])
    remote_words = sum(int(row["remote_words"]) for row in rows)
    attributed_words = sum(int(row["attributed_words"]) for row in rows)
    remote_seconds = sum(float(row["remote_speech_sec"]) for row in rows)
    attributed_seconds = sum(float(row["attributed_speech_sec"]) for row in rows)
    remaining_words = remote_words - attributed_words
    remaining_seconds = remote_seconds - attributed_seconds
    recovered_words = baseline_unknown_words - remaining_words
    recovered_seconds = baseline_unknown_seconds - remaining_seconds
    if baseline_unknown_words <= 0 or baseline_unknown_seconds <= 0 or remote_seconds <= 0:
        raise ValueError("v3_baseline_residual_empty")
    attributed_only = reference["attributed_only"]
    session_reports = [read_json(session / INDEPENDENT_DIR / "report.json") for session in sessions]
    reports_by_session = {
        str((row.get("source") or {}).get("session_id")): row for row in session_reports
    }

    def split_metrics(ids: list[str]) -> dict[str, Any]:
        selected = [reports_by_session[session_id]["summary"] for session_id in ids]
        unknown_words = sum(int(row["baseline_unknown_words"]) for row in selected)
        unknown_seconds = sum(float(row["baseline_unknown_seconds"]) for row in selected)
        split_recovered_words = sum(int(row["recovered_words"]) for row in selected)
        split_recovered_seconds = sum(float(row["recovered_seconds"]) for row in selected)
        return {
            "sessions": len(selected),
            "baseline_unknown_words": unknown_words,
            "baseline_unknown_seconds": round(unknown_seconds, 6),
            "recovered_words": split_recovered_words,
            "recovered_seconds": round(split_recovered_seconds, 6),
            "unknown_words_reduction_ratio": round(
                split_recovered_words / unknown_words, 6
            ) if unknown_words else 0.0,
            "unknown_seconds_reduction_ratio": round(
                split_recovered_seconds / unknown_seconds, 6
            ) if unknown_seconds else 0.0,
        }

    split_summary = {
        "development": split_metrics(development_ids),
        "held_out": split_metrics(held_out_ids),
    }
    baseline_attributed_only = baseline_reference["attributed_only"]
    promotion = policy["promotion"]
    model_hashes = {
        str(((row.get("source") or {}).get("embedding_backend") or {}).get("model", {}).get("tree_sha256") or "")
        for row in session_reports
    }
    backend_methods = {
        str(((row.get("source") or {}).get("embedding_backend") or {}).get("method") or "")
        for row in session_reports
    }
    gates = {
        "minimum_corpus_size": len(rows) == int(promotion["required_sessions"]),
        "v3_baseline_promoted": True,
        "unknown_words_reduction": recovered_words / baseline_unknown_words
        >= float(promotion["minimum_unknown_word_reduction_ratio"]),
        "unknown_seconds_reduction": recovered_seconds / baseline_unknown_seconds
        >= float(promotion["minimum_unknown_second_reduction_ratio"]),
        "corpus_split_exact": split_exact,
        "independent_backend": backend_methods == {"wavlm_xvector_independent_v1"},
        "single_pinned_model": len(model_hashes) == 1 and "" not in model_hashes,
        "reference_minimum_rows": int(reference["aligned_rows"]) >= 50,
        "reference_attributed_bcubed_f1": float(attributed_only["bcubed"]["f1"])
        >= float(baseline_attributed_only["bcubed"]["f1"]),
        "reference_attributed_pairwise_precision": float(
            attributed_only["pairwise"]["precision"]
        )
        >= float(baseline_attributed_only["pairwise"]["precision"]),
        "candidate_direct_reference_words": int(candidate_reference["covered_words"])
        >= int(promotion["minimum_direct_reference_words"]),
        "candidate_direct_reference_precision": (
            candidate_reference["precision_words"] is not None
            and float(candidate_reference["precision_words"])
            >= float(promotion["minimum_direct_reference_precision"])
        ),
        "all_session_publish_gates": all(row["publish_gate"] for row in rows),
        "all_expected_speaker_ranges": all(row["expected_count_gate"] for row in rows),
        "all_one_to_one_controls": all(row["one_to_one_gate"] for row in rows),
        "all_selected_dialogue_exact": all(row["selected_dialogue_exact"] for row in rows),
        "all_turn_text_exact": all(row["turn_text_exact"] for row in rows),
        "all_word_ids_unique": all(row["word_ids_unique"] for row in rows),
        "all_word_conservation": all(row["word_conservation_gate"] for row in rows),
        "all_timestamp_order": all(row["timestamp_order_gate"] for row in rows),
        "all_raw_audio_unchanged": all(row["raw_audio_unchanged"] for row in rows),
        "all_v3_labels_preserved": all(
            bool((row.get("gates") or {}).get("baseline_attributions_preserved"))
            for row in session_reports
        ),
        "all_protected_causes_preserved": all(
            bool((row.get("gates") or {}).get("protected_causes_preserved"))
            for row in session_reports
        ),
        "all_split_enrollments_complete": all(
            bool((row.get("gates") or {}).get("complete_split_enrollment"))
            for row in session_reports
        ),
        "all_enrollment_tests_precise": all(
            bool((row.get("gates") or {}).get("enrollment_test_accepted_precision"))
            for row in session_reports
        ),
        "all_aggregate_fallback_exact": all(
            bool((row.get("gates") or {}).get("aggregate_fallback_exact"))
            for row in session_reports
        ),
        "boundary_cases": (
            bool(boundaries["passed"])
            and len(boundaries.get("cases") or [])
            >= int(promotion["required_internal_boundaries"])
        ),
    }
    decision = (
        "PROMOTE_INDEPENDENT_REMOTE_SPEAKER_EVIDENCE_V1"
        if all(gates.values())
        else "DO_NOT_PROMOTE"
    )
    summary = {
        "sessions": len(rows),
        "remote_words": remote_words,
        "attributed_words": attributed_words,
        "baseline_unknown_words": baseline_unknown_words,
        "recovered_words": recovered_words,
        "remaining_unknown_words": remaining_words,
        "unknown_words_reduction_ratio": round(recovered_words / baseline_unknown_words, 6),
        "remote_speech_sec": round(remote_seconds, 6),
        "attributed_speech_sec": round(attributed_seconds, 6),
        "baseline_unknown_seconds": round(baseline_unknown_seconds, 6),
        "recovered_seconds": round(recovered_seconds, 6),
        "remaining_unknown_seconds": round(remaining_seconds, 6),
        "unknown_seconds_reduction_ratio": round(recovered_seconds / baseline_unknown_seconds, 6),
        "attributable_remote_speech_ratio": round(attributed_seconds / remote_seconds, 6),
        "published_speakers": sum(int(row["published_speakers"]) for row in rows),
        "internal_change_utterances": sum(int(row["internal_change_utterances"]) for row in rows),
    }
    report = {
        "schema": SCHEMA,
        "decision": decision,
        "profile": "wavlm_xvector_independent_residual_v1",
        "summary": summary,
        "gates": gates,
        "sessions": rows,
        "reference_evaluation": reference,
        "candidate_reference_evaluation": candidate_reference,
        "boundary_evaluation": boundaries,
        "cause_evidence": causes,
        "corpus_split": {
            "development": development_ids,
            "held_out": held_out_ids,
            "exact": split_exact,
        },
        "split_summary": split_summary,
        "backend": {
            "methods": sorted(backend_methods),
            "model_tree_sha256": sorted(model_hashes),
            "policy": portable(artifact(policy_path)),
        },
        "baseline": {
            "manifest": portable(artifact(baseline_manifest_path)),
            "corpus_report": portable(artifact(baseline_report_path)),
            "reference_evaluation": portable(artifact(baseline_reference_path)),
        },
        "decision_reason": (
            "independent_coverage_target_met_without_v3_precision_or_conservation_regression"
            if decision == "PROMOTE_INDEPENDENT_REMOTE_SPEAKER_EVIDENCE_V1"
            else "independent_evidence_did_not_clear_all_promotion_gates"
        ),
        "failed_gates": [name for name, passed in gates.items() if not passed],
    }
    manifest = current_manifest(
        sessions,
        baseline_manifest_path,
        baseline_report_path,
        baseline_reference_path,
        v2_manifest_path,
        boundaries_path,
        policy_path,
        decision,
    )
    if args.frozen_manifest:
        frozen = read_json(args.frozen_manifest.expanduser().resolve())
        gates["frozen_manifest_match"] = frozen == manifest
        if not gates["frozen_manifest_match"]:
            report["decision"] = "DO_NOT_PROMOTE"
            report["decision_reason"] = "frozen_manifest_mismatch"
    outputs = {
        "independent_remote_speaker_corpus_report.json": canonical_json(report),
        "independent_remote_speaker_corpus_report.md": report_markdown(report).encode(),
        "reference_evaluation.json": canonical_json(reference),
        "cause_evidence.json": canonical_json(causes),
    }
    return report, outputs, manifest


def main() -> int:
    args = parse_args()
    report, outputs, manifest = build(args)
    out_dir = args.output.expanduser().resolve()
    if args.verify_existing:
        stale = [
            name
            for name, content in outputs.items()
            if not (out_dir / name).is_file() or (out_dir / name).read_bytes() != content
        ]
        if stale:
            print("independent remote-speaker outputs are stale: " + ", ".join(stale), file=sys.stderr)
            return 2
    else:
        out_dir.mkdir(parents=True, exist_ok=True)
        for name, content in outputs.items():
            (out_dir / name).write_bytes(content)
    if args.write_manifest:
        args.write_manifest.parent.mkdir(parents=True, exist_ok=True)
        args.write_manifest.write_bytes(canonical_json(manifest))
    summary = report["summary"]
    print(
        f"independent remote-speaker v1 corpus: decision={report['decision']} "
        f"recovered={summary['recovered_words']}w/{summary['recovered_seconds']:.3f}s "
        f"remaining={summary['remaining_unknown_words']}w/{summary['remaining_unknown_seconds']:.3f}s"
    )
    return 0 if report["decision"] == "PROMOTE_INDEPENDENT_REMOTE_SPEAKER_EVIDENCE_V1" else 2


if __name__ == "__main__":
    raise SystemExit(main())
