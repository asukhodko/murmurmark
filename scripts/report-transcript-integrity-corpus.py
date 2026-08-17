#!/usr/bin/env python3
"""Qualify transcript_integrity_v1 from private per-session reports."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any


SCHEMA = "murmurmark.transcript_integrity_corpus_report/v1"
POLICY_SCHEMA = "murmurmark.transcript_integrity_policy/v1"
SCRIPT_VERSION = "0.1.1"
PROFILE = "transcript_integrity_v1"


class CorpusError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build corpus promotion evidence for transcript_integrity_v1.")
    parser.add_argument("sessions", nargs="+", type=Path)
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("sessions/_reports/transcript-integrity-v1"),
    )
    parser.add_argument("--min-sessions", type=int, default=3)
    parser.add_argument("--min-applied-patches", type=int, default=3)
    parser.add_argument("--write-policy", type=Path, default=None)
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def report_path(session: Path) -> Path:
    return (
        session
        / "derived/transcript-simple/whisper-cpp/text-integrity"
        / f"transcript_integrity_report.{PROFILE}.json"
    )


def frozen_files_current(session: Path, records: dict[str, Any]) -> bool:
    if not records:
        return False
    return all(
        isinstance(record, dict)
        and bool(record.get("path"))
        and (session / str(record["path"])).is_file()
        and sha256(session / str(record["path"])) == record.get("sha256")
        for record in records.values()
    )


def raw_capture_current(session: Path, records: dict[str, Any]) -> bool:
    return all(
        (session / relative_path).is_file()
        and sha256(session / relative_path) == expected_hash
        for relative_path, expected_hash in records.items()
    )


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parent.parent
    algorithm = repo_root / "scripts/apply-transcript-integrity.py"
    rows: list[dict[str, Any]] = []
    failures: list[str] = []
    by_kind: Counter[str] = Counter()
    applied_by_kind: Counter[str] = Counter()

    for index, value in enumerate(args.sessions, start=1):
        session = value.expanduser().resolve()
        report = read_json(report_path(session))
        if report is None or report.get("schema") != "murmurmark.transcript_integrity_report/v1":
            failures.append(f"session_{index}:report_missing_or_invalid")
            continue
        summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
        gates = report.get("gates") if isinstance(report.get("gates"), dict) else {}
        inputs = report.get("inputs") if isinstance(report.get("inputs"), dict) else {}
        outputs = report.get("outputs") if isinstance(report.get("outputs"), dict) else {}
        raw_capture = report.get("raw_capture") if isinstance(report.get("raw_capture"), dict) else {}
        inputs_current = frozen_files_current(session, inputs)
        outputs_current = frozen_files_current(session, outputs)
        raw_current = raw_capture_current(session, raw_capture)
        row = {
            "slot": f"session_{index:02d}",
            "gates_passed": gates.get("passed") is True,
            "raw_capture_unchanged": gates.get("raw_capture_unchanged") is True,
            "roles_unchanged": gates.get("roles_unchanged") is True,
            "timestamps_unchanged": gates.get("timestamps_unchanged") is True,
            "utterance_lineage_exact": gates.get("utterance_lineage_exact") is True,
            "fail_open_judge": gates.get("fail_open_judge") is True,
            "inputs_current": inputs_current,
            "outputs_current": outputs_current,
            "raw_capture_current": raw_current,
            "candidate_count": int(summary.get("candidate_count") or 0),
            "applied_patch_count": int(summary.get("applied_patch_count") or 0),
            "remaining_review_count": int(summary.get("remaining_review_count") or 0),
            "output_fingerprint": report.get("output_fingerprint"),
            "decision_fingerprint": report.get("decision_fingerprint"),
        }
        rows.append(row)
        by_kind.update({str(key): int(count) for key, count in (summary.get("by_kind") or {}).items()})
        applied_by_kind.update(
            {str(key): int(count) for key, count in (summary.get("applied_by_kind") or {}).items()}
        )
        if not all(
            row[key]
            for key in (
                "gates_passed",
                "raw_capture_unchanged",
                "roles_unchanged",
                "timestamps_unchanged",
                "utterance_lineage_exact",
                "fail_open_judge",
                "inputs_current",
                "outputs_current",
                "raw_capture_current",
            )
        ):
            failures.append(f"session_{index}:safety_gate_failed")

    total_applied = sum(row["applied_patch_count"] for row in rows)
    enough_sessions = len(rows) >= args.min_sessions
    material_gain = total_applied >= args.min_applied_patches
    all_safe = not failures and all(row["gates_passed"] for row in rows)
    decision = "PROMOTE" if enough_sessions and material_gain and all_safe else "KEEP_CURRENT"
    gates = {
        "passed": decision == "PROMOTE",
        "minimum_sessions": enough_sessions,
        "material_gain": material_gain,
        "all_session_safety_gates": all_safe,
        "algorithm_present": algorithm.is_file(),
    }
    report = {
        "schema": SCHEMA,
        "generator": {"name": "report-transcript-integrity-corpus", "version": SCRIPT_VERSION},
        "profile": PROFILE,
        "decision": decision,
        "algorithm": {
            "path": "scripts/apply-transcript-integrity.py",
            "sha256": sha256(algorithm),
        },
        "summary": {
            "session_count": len(rows),
            "candidate_count": sum(row["candidate_count"] for row in rows),
            "applied_patch_count": total_applied,
            "remaining_review_count": sum(row["remaining_review_count"] for row in rows),
            "by_kind": dict(sorted(by_kind.items())),
            "applied_by_kind": dict(sorted(applied_by_kind.items())),
        },
        "thresholds": {
            "min_sessions": args.min_sessions,
            "min_applied_patches": args.min_applied_patches,
        },
        "gates": gates,
        "failures": failures,
        "sessions": rows,
    }
    out_dir = args.out_dir.expanduser().resolve()
    report_file = out_dir / "corpus_report.json"
    write_json(report_file, report)
    markdown = [
        "# Transcript Integrity v1 Corpus Report",
        "",
        f"- Decision: `{decision}`",
        f"- Sessions: `{len(rows)}`",
        f"- Candidates: `{report['summary']['candidate_count']}`",
        f"- Applied patches: `{total_applied}`",
        f"- Remaining review: `{report['summary']['remaining_review_count']}`",
        "",
        "No meeting text, session identifier or absolute source path is written to this report.",
    ]
    (out_dir / "corpus_report.md").write_text("\n".join(markdown) + "\n", encoding="utf-8")

    if args.write_policy is not None:
        policy = {
            "schema": POLICY_SCHEMA,
            "profile": PROFILE,
            "decision": decision,
            "qualified_algorithm": report["algorithm"],
            "qualification": {
                "session_count": len(rows),
                "candidate_count": report["summary"]["candidate_count"],
                "applied_patch_count": total_applied,
                "remaining_review_count": report["summary"]["remaining_review_count"],
                "all_session_safety_gates": all_safe,
            },
            "fallback_profile": "current_selected_profile",
        }
        write_json(args.write_policy.expanduser().resolve(), policy)

    print(f"decision: {decision}")
    print(f"sessions: {len(rows)}")
    print(f"applied: {total_applied}")
    print(f"report: {report_file}")
    return 0 if decision == "PROMOTE" else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except CorpusError as error:
        print(f"error: {error}", file=os.sys.stderr)
        raise SystemExit(2)
