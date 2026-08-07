#!/usr/bin/env python3
"""Qualify Remote Speaker Coverage v3 against the frozen v2 corpus."""

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
SCHEMA = "murmurmark.remote_speaker_coverage_corpus_report/v3"
MANIFEST_SCHEMA = "murmurmark.remote_speaker_coverage_frozen_manifest/v3"
REFERENCE_SCHEMA = "murmurmark.remote_speaker_coverage_reference_evaluation/v3"
CAUSE_MAP_SCHEMA = "murmurmark.remote_speaker_unknown_cause_map/v3"
V3_DIR = "derived/audit/remote-speaker-coverage-v3"
DEFAULT_BASELINE_MANIFEST = ROOT / "docs/testing/remote-speaker-diarization-v2-manifest.json"
DEFAULT_BASELINE_REPORT = ROOT / "sessions/_reports/remote-speaker-diarization-v2/remote_speaker_diarization_corpus_report.json"
DEFAULT_BASELINE_REFERENCE = ROOT / "sessions/_reports/remote-speaker-diarization-v2/reference_evaluation.json"
DEFAULT_BOUNDARIES = ROOT / "docs/testing/remote-speaker-diarization-v2-boundaries.json"
DEFAULT_OUTPUT = ROOT / "sessions/_reports/remote-speaker-coverage-v3"
V2_REPORTER = ROOT / "scripts/report-remote-speaker-diarization-corpus.py"


def load_v2_module() -> Any:
    spec = importlib.util.spec_from_file_location("murmurmark_remote_diarization_v2_report", V2_REPORTER)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot_load_v2_reporter")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module.REPORT_DIR = V3_DIR
    return module


V2 = load_v2_module()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate Remote Speaker Coverage v3 corpus gates.")
    parser.add_argument("scope", nargs="?", default="all", choices=["all"])
    parser.add_argument("--baseline-manifest", type=Path, default=DEFAULT_BASELINE_MANIFEST)
    parser.add_argument("--baseline-report", type=Path, default=DEFAULT_BASELINE_REPORT)
    parser.add_argument("--baseline-reference", type=Path, default=DEFAULT_BASELINE_REFERENCE)
    parser.add_argument("--boundary-cases", type=Path, default=DEFAULT_BOUNDARIES)
    parser.add_argument("--sessions-root", type=Path, default=ROOT / "sessions")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--write-manifest", type=Path)
    parser.add_argument("--frozen-manifest", type=Path)
    parser.add_argument("--verify-existing", action="store_true")
    return parser.parse_args()


def canonical_json(payload: Any) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


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
    manifest: dict[str, Any], sessions_root: Path
) -> tuple[list[Path], dict[str, dict[str, int]]]:
    sessions: list[Path] = []
    expected: dict[str, dict[str, int]] = {}
    for row in manifest.get("sessions") or []:
        session_id = str(row["session_id"])
        sessions.append(sessions_root / session_id)
        expected[session_id] = {
            "min": int(row["expected_speakers"]["min"]),
            "max": int(row["expected_speakers"]["max"]),
        }
    return sessions, expected


def reference_evaluation(
    session: Path, baseline: dict[str, Any], baseline_reference_path: Path
) -> dict[str, Any]:
    attributions = {
        str(row["utterance_id"]): row
        for row in read_jsonl(session / V3_DIR / "utterance_attribution.jsonl")
    }
    rows: list[dict[str, Any]] = []
    for baseline_row in baseline.get("rows") or []:
        uid = str(baseline_row["utterance_id"])
        attribution = attributions.get(uid, {})
        rows.append(
            {
                "utterance_id": uid,
                "reference_speaker": baseline_row["reference_speaker"],
                "predicted_speaker": attribution.get("speaker_id"),
                "prediction_status": attribution.get("status"),
            }
        )
    attributed = [row for row in rows if row["predicted_speaker"] is not None]
    truth = [str(row["reference_speaker"]) for row in attributed]
    predicted = [str(row["predicted_speaker"]) for row in attributed]
    metrics = {
        "rows": len(attributed),
        "adjusted_rand_index": round(float(V2.adjusted_rand_score(truth, predicted)), 6)
        if len(attributed) >= 2
        else 0.0,
        "bcubed": V2.bcubed(truth, predicted),
        "pairwise": V2.pairwise(truth, predicted),
    }
    return {
        "schema": REFERENCE_SCHEMA,
        "status": "completed" if rows else "reference_missing",
        "session_id": session.name,
        "baseline_reference": portable(artifact(baseline_reference_path)),
        "aligned_rows": len(rows),
        "attributed_rows": len(attributed),
        "attributed_row_ratio": round(len(attributed) / len(rows), 6) if rows else 0.0,
        "attributed_only": metrics,
        "rows": rows,
    }


def combined_causes(sessions: list[Path]) -> dict[str, Any]:
    totals: dict[str, dict[str, float | int]] = defaultdict(lambda: {"words": 0, "seconds": 0.0})
    session_rows: list[dict[str, Any]] = []
    for session in sessions:
        payload = read_json(session / V3_DIR / "unknown_cause_map.json")
        session_rows.append(
            {
                "session_id": session.name,
                "baseline_unknown_words": int(payload["baseline_unknown_words"]),
                "baseline_unknown_seconds": float(payload["baseline_unknown_seconds"]),
                "causes": payload["causes"],
            }
        )
        for row in payload.get("causes") or []:
            cause = str(row["cause"])
            totals[cause]["words"] = int(totals[cause]["words"]) + int(row["words"])
            totals[cause]["seconds"] = float(totals[cause]["seconds"]) + float(row["seconds"])
    return {
        "schema": CAUSE_MAP_SCHEMA,
        "sessions": session_rows,
        "causes": [
            {
                "cause": cause,
                "words": int(values["words"]),
                "seconds": round(float(values["seconds"]), 6),
            }
            for cause, values in sorted(totals.items())
        ],
    }


def current_manifest(
    sessions: list[Path], baseline_manifest: Path, baseline_report: Path,
    baseline_reference: Path, boundaries: Path, decision: str,
) -> dict[str, Any]:
    return {
        "schema": MANIFEST_SCHEMA,
        "decision": decision,
        "implementation": {
            "audit": portable(artifact(ROOT / "scripts/audit-remote-speaker-coverage-v3.py")),
            "corpus_report": portable(artifact(Path(__file__).resolve())),
            "v2_corpus_report_dependency": portable(artifact(V2_REPORTER)),
        },
        "baseline": {
            "manifest": portable(artifact(baseline_manifest)),
            "corpus_report": portable(artifact(baseline_report)),
            "reference_evaluation": portable(artifact(baseline_reference)),
            "boundary_cases": portable(artifact(boundaries)),
        },
        "sessions": [
            {
                "session_id": session.name,
                "report": portable(artifact(session / V3_DIR / "report.json")),
                "manifest": portable(artifact(session / V3_DIR / "artifact_manifest.json")),
                "rich": portable(artifact(session / V3_DIR / "transcript.rich.shadow.json")),
            }
            for session in sessions
        ],
    }


def report_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    reference = report["reference_evaluation"]
    lines = [
        "# Remote Speaker Coverage v3 Corpus",
        "",
        f"- Decision: `{report['decision']}`",
        f"- Sessions: `{summary['sessions']}`",
        f"- Recovered: `{summary['recovered_words']}` words / `{summary['recovered_seconds']:.3f}s`",
        f"- Unknown reduction: words `{summary['unknown_words_reduction_ratio']:.6f}`, seconds `{summary['unknown_seconds_reduction_ratio']:.6f}`",
        f"- Remaining unknown: `{summary['remaining_unknown_words']}` words / `{summary['remaining_unknown_seconds']:.3f}s`",
        f"- Attributable remote speech: `{summary['attributable_remote_speech_ratio']:.6f}`",
        f"- Reference B-cubed F1: `{reference['attributed_only']['bcubed']['f1']:.6f}`",
        f"- Reference pairwise precision: `{reference['attributed_only']['pairwise']['precision']:.6f}`",
        "",
        "## Gates",
        "",
    ]
    lines.extend(f"- `{key}`: `{str(value).lower()}`" for key, value in report["gates"].items())
    lines.extend(["", "## Residual Cause Map", ""])
    for row in report["unknown_cause_map"]["causes"]:
        lines.append(f"- `{row['cause']}`: `{row['words']}` words / `{row['seconds']:.3f}s`")
    return "\n".join(lines) + "\n"


def build(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, bytes], dict[str, Any]]:
    baseline_manifest_path = args.baseline_manifest.expanduser().resolve()
    baseline_report_path = args.baseline_report.expanduser().resolve()
    baseline_reference_path = args.baseline_reference.expanduser().resolve()
    boundaries_path = args.boundary_cases.expanduser().resolve()
    baseline_manifest = read_json(baseline_manifest_path)
    baseline_report = read_json(baseline_report_path)
    baseline_reference = read_json(baseline_reference_path)
    if baseline_manifest.get("decision") != "PROMOTE" or baseline_report.get("decision") != "PROMOTE":
        raise ValueError("v2_baseline_not_promoted")
    sessions, expected = corpus_inputs(
        baseline_manifest, args.sessions_root.expanduser().resolve()
    )
    rows = [V2.session_row(session, expected[session.name]) for session in sessions]
    reference_session_id = str(baseline_reference["session_id"])
    reference_session = next((session for session in sessions if session.name == reference_session_id), None)
    if reference_session is None:
        raise ValueError("reference_session_not_in_corpus")
    reference = reference_evaluation(reference_session, baseline_reference, baseline_reference_path)
    boundaries = V2.boundary_evaluation({session.name: session for session in sessions}, boundaries_path)
    causes = combined_causes(sessions)
    baseline_summary = baseline_report["summary"]
    baseline_unknown_words = int(baseline_summary["remote_words"]) - int(baseline_summary["attributed_words"])
    baseline_unknown_seconds = float(baseline_summary["remote_speech_sec"]) - float(baseline_summary["attributed_speech_sec"])
    remote_words = sum(int(row["remote_words"]) for row in rows)
    attributed_words = sum(int(row["attributed_words"]) for row in rows)
    remote_seconds = sum(float(row["remote_speech_sec"]) for row in rows)
    attributed_seconds = sum(float(row["attributed_speech_sec"]) for row in rows)
    remaining_words = remote_words - attributed_words
    remaining_seconds = remote_seconds - attributed_seconds
    recovered_words = baseline_unknown_words - remaining_words
    recovered_seconds = baseline_unknown_seconds - remaining_seconds
    attributed_only = reference["attributed_only"]
    session_reports = [read_json(session / V3_DIR / "report.json") for session in sessions]
    gates = {
        "minimum_corpus_size": len(rows) >= 6,
        "v2_baseline_promoted": True,
        "unknown_words_reduction": recovered_words / baseline_unknown_words >= 0.25,
        "unknown_seconds_reduction": recovered_seconds / baseline_unknown_seconds >= 0.25,
        "reference_minimum_rows": int(reference["aligned_rows"]) >= 50,
        "reference_attributed_bcubed_f1": float(attributed_only["bcubed"]["f1"]) >= 0.95,
        "reference_attributed_pairwise_precision": float(attributed_only["pairwise"]["precision"]) >= 0.95,
        "all_session_publish_gates": all(row["publish_gate"] for row in rows),
        "all_expected_speaker_ranges": all(row["expected_count_gate"] for row in rows),
        "all_one_to_one_controls": all(row["one_to_one_gate"] for row in rows),
        "all_selected_dialogue_exact": all(row["selected_dialogue_exact"] for row in rows),
        "all_turn_text_exact": all(row["turn_text_exact"] for row in rows),
        "all_word_ids_unique": all(row["word_ids_unique"] for row in rows),
        "all_word_conservation": all(row["word_conservation_gate"] for row in rows),
        "all_timestamp_order": all(row["timestamp_order_gate"] for row in rows),
        "all_raw_audio_unchanged": all(row["raw_audio_unchanged"] for row in rows),
        "all_v2_labels_preserved": all(
            bool((row.get("gates") or {}).get("baseline_attributions_preserved"))
            for row in session_reports
        ),
        "all_remote_overlap_preserved": all(
            bool((row.get("gates") or {}).get("remote_overlap_preserved"))
            for row in session_reports
        ),
        "boundary_cases": bool(boundaries["passed"]),
    }
    decision = "PROMOTE" if all(gates.values()) else "DO_NOT_PROMOTE"
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
        "profile": "resemblyzer_seeded_frame_recovery_v3",
        "summary": summary,
        "gates": gates,
        "sessions": rows,
        "reference_evaluation": reference,
        "boundary_evaluation": boundaries,
        "unknown_cause_map": causes,
        "baseline": {
            "manifest": portable(artifact(baseline_manifest_path)),
            "corpus_report": portable(artifact(baseline_report_path)),
            "reference_evaluation": portable(artifact(baseline_reference_path)),
        },
    }
    manifest = current_manifest(
        sessions, baseline_manifest_path, baseline_report_path, baseline_reference_path,
        boundaries_path, decision,
    )
    if args.frozen_manifest:
        frozen = read_json(args.frozen_manifest.expanduser().resolve())
        gates["frozen_manifest_match"] = frozen == manifest
        if not gates["frozen_manifest_match"]:
            report["decision"] = "DO_NOT_PROMOTE"
    outputs = {
        "remote_speaker_coverage_corpus_report.json": canonical_json(report),
        "remote_speaker_coverage_corpus_report.md": report_markdown(report).encode(),
        "reference_evaluation.json": canonical_json(reference),
        "unknown_cause_map.json": canonical_json(causes),
    }
    return report, outputs, manifest


def main() -> int:
    args = parse_args()
    report, outputs, manifest = build(args)
    out_dir = args.output.expanduser().resolve()
    if args.verify_existing:
        stale = [
            name for name, content in outputs.items()
            if not (out_dir / name).is_file() or (out_dir / name).read_bytes() != content
        ]
        if stale:
            print("remote coverage v3 outputs are stale: " + ", ".join(stale), file=sys.stderr)
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
        f"remote coverage v3 corpus: decision={report['decision']} "
        f"recovered={summary['recovered_words']}w/{summary['recovered_seconds']:.3f}s "
        f"remaining={summary['remaining_unknown_words']}w/{summary['remaining_unknown_seconds']:.3f}s"
    )
    return 0 if report["decision"] == "PROMOTE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
