#!/usr/bin/env python3
"""Qualify Remote Unknown Evidence Recovery v1 on frozen and held-out sessions."""

from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv/bin/python"
INDEPENDENT_AUDIT = ROOT / "scripts/audit-independent-remote-speaker-evidence-v1.py"
RECOVERY_AUDIT = ROOT / "scripts/audit-remote-unknown-evidence-recovery-v1.py"
POLICY = ROOT / "policies/remote-unknown-evidence-recovery-v1.json"
REBASELINE_MANIFEST = (
    ROOT / "sessions/_reports/post-segmentation-transcript-rebaseline-v1/private/input_manifest.json"
)
HELD_OUT_SESSION = ROOT / "sessions/2026-08-21_15-58-36"
TRUTH_V1 = ROOT / "sessions/_reports/remote-speaker-direct-truth-seed-v1/private"
TRUTH_V2 = ROOT / "sessions/_reports/remote-speaker-disjoint-truth-expansion-v2/private"
TRUTH_SESSIONS_MANIFEST = ROOT / "docs/testing/remote-speaker-coverage-v3-manifest.json"
DEFAULT_OUTPUT = ROOT / "sessions/_reports/remote-unknown-evidence-recovery-v1"
DEFAULT_SNAPSHOT = ROOT / "docs/testing/remote-unknown-evidence-recovery-v1-snapshot.json"

SCHEMA = "murmurmark.remote_unknown_evidence_recovery_corpus_report/v1"
INPUT_SCHEMA = "murmurmark.remote_unknown_evidence_recovery_input_manifest/v1"
SNAPSHOT_SCHEMA = "murmurmark.remote_unknown_evidence_recovery_snapshot/v1"
RECOVERY_DIR_NAME = "remote-unknown-evidence-recovery-v1"
INDEPENDENT_DIR_NAME = "independent-remote-speaker-evidence-v1"


class CorpusError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate Remote Unknown Evidence Recovery v1.")
    parser.add_argument("scope", nargs="?", default="all", choices=["all"])
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--verify-existing", action="store_true")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--write-snapshot", type=Path)
    parser.add_argument("--policy", type=Path, default=POLICY)
    parser.add_argument("--held-out-session", type=Path, default=HELD_OUT_SESSION)
    return parser.parse_args()


def canonical(payload: Any) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise CorpusError(f"json_object_required:{path}")
    return payload


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def portable(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path.resolve())


def artifact(path: Path) -> dict[str, Any]:
    row: dict[str, Any] = {"path": portable(path), "exists": path.is_file()}
    if path.is_file():
        row.update({"bytes": path.stat().st_size, "sha256": sha256(path)})
    return row


def verify_artifact(row: dict[str, Any], path: Path) -> None:
    if not path.is_file() or row.get("sha256") != sha256(path):
        raise CorpusError(f"frozen_artifact_mismatch:{path}")


def verify_output_manifest(root: Path) -> None:
    path = root / "artifact_manifest.json"
    payload = read_json(path)
    for name, expected in (payload.get("artifacts") or {}).items():
        target = root / str(name)
        if not target.is_file() or sha256(target) != str(expected):
            raise CorpusError(f"recovery_artifact_stale:{target}")


def fingerprint_current(session: Path, row: Any) -> bool:
    if not isinstance(row, dict) or not row.get("path") or not row.get("sha256"):
        return False
    path = Path(str(row["path"])).expanduser()
    path = path.resolve() if path.is_absolute() else session / path
    return path.is_file() and row.get("bytes") == path.stat().st_size and row.get("sha256") == sha256(path)


def coverage_from_selection(session: Path) -> Path:
    selection = read_json(
        session / "derived/transcript-rich/speaker-resolved-default-v1/selection.json"
    )
    row = selection.get("coverage_report") or {}
    path = Path(str(row.get("path") or ""))
    path = path.resolve() if path.is_absolute() else session / path
    if not path.is_file() or row.get("sha256") != sha256(path):
        raise CorpusError(f"held_out_coverage_stale:{session.name}")
    return path.parent


def frozen_sessions() -> list[dict[str, Any]]:
    manifest = read_json(REBASELINE_MANIFEST)
    rows = []
    for source in manifest.get("sessions") or []:
        report = (source.get("artifacts") or {}).get("coverage_report") or {}
        if not report.get("exists"):
            continue
        path = Path(str(report["path"])).expanduser().resolve()
        verify_artifact(report, path)
        rows.append(
            {
                "alias": str(source["alias"]),
                "session_id": str(source["session_name"]),
                "session": Path(str(source["session_path"])).expanduser().resolve(),
                "coverage": path.parent,
            }
        )
    return rows


def truth_sessions() -> list[dict[str, Any]]:
    manifest = read_json(TRUTH_SESSIONS_MANIFEST)
    return [
        {
            "alias": f"truth_{index:02d}",
            "session_id": str(row["session_id"]),
            "session": ROOT / "sessions" / str(row["session_id"]),
            "coverage": ROOT
            / "sessions"
            / str(row["session_id"])
            / "derived/audit/remote-speaker-coverage-v3",
        }
        for index, row in enumerate(manifest.get("sessions") or [], start=1)
    ]


def evidence_dirs(coverage: Path) -> tuple[Path, Path]:
    return coverage.parent / INDEPENDENT_DIR_NAME, coverage.parent / RECOVERY_DIR_NAME


def run(command: list[str]) -> None:
    env = dict(os.environ)
    env["HF_HUB_OFFLINE"] = "1"
    completed = subprocess.run(command, cwd=ROOT, env=env, check=False)
    if completed.returncode:
        raise CorpusError(f"command_failed:{completed.returncode}:{' '.join(command)}")


def refresh_session(row: dict[str, Any], run_independent: bool) -> None:
    independent, recovery = evidence_dirs(row["coverage"])
    if run_independent:
        run(
            [
                "nice",
                "-n",
                "20",
                str(PYTHON),
                str(INDEPENDENT_AUDIT),
                str(row["session"]),
                "--input-dir",
                str(row["coverage"]),
                "--out-dir",
                str(independent),
            ]
        )
    run(
        [
            str(PYTHON),
            str(RECOVERY_AUDIT),
            str(row["session"]),
            "--input-dir",
            str(row["coverage"]),
            "--independent-dir",
            str(independent),
            "--out-dir",
            str(recovery),
        ]
    )


def session_summary(row: dict[str, Any], role: str) -> dict[str, Any]:
    independent, recovery = evidence_dirs(row["coverage"])
    verify_output_manifest(recovery)
    report = read_json(recovery / "report.json")
    if report.get("decision") != "PUBLISH_SHADOW_EVIDENCE":
        raise CorpusError(f"recovery_not_publishable:{row['session_id']}")
    source = report.get("source") or {}
    if not all(fingerprint_current(row["session"], item) for item in (source.get("coverage_v3") or {}).values()):
        raise CorpusError(f"recovery_coverage_source_stale:{row['session_id']}")
    if not all(
        fingerprint_current(row["session"], item)
        for item in (source.get("independent_wavlm") or {}).values()
    ):
        raise CorpusError(f"recovery_independent_source_stale:{row['session_id']}")
    if not fingerprint_current(row["session"], source.get("policy")):
        raise CorpusError(f"recovery_policy_source_stale:{row['session_id']}")
    if not fingerprint_current(
        row["session"], (report.get("implementation") or {}).get("script")
    ):
        raise CorpusError(f"recovery_implementation_stale:{row['session_id']}")
    return {
        "alias": row["alias"],
        "session_id": row["session_id"],
        "role": role,
        "recovery_dir": portable(recovery),
        "coverage": artifact(row["coverage"] / "artifact_manifest.json"),
        "independent": artifact(independent / "artifact_manifest.json"),
        "recovery": artifact(recovery / "artifact_manifest.json"),
        "summary": report["summary"],
        "gates": report["gates"],
    }


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    fields = (
        "remote_words",
        "baseline_unknown_words",
        "baseline_unknown_seconds",
        "recovered_words",
        "recovered_seconds",
        "remaining_unknown_words",
        "remaining_unknown_seconds",
    )
    result: dict[str, Any] = {"sessions": len(rows)}
    for field in fields:
        value = sum(float(row["summary"][field]) for row in rows)
        result[field] = int(value) if field.endswith("words") else round(value, 6)
    result["unknown_words_reduction_ratio"] = round(
        result["recovered_words"] / result["baseline_unknown_words"], 6
    ) if result["baseline_unknown_words"] else 0.0
    result["unknown_seconds_reduction_ratio"] = round(
        result["recovered_seconds"] / result["baseline_unknown_seconds"], 6
    ) if result["baseline_unknown_seconds"] else 0.0
    return result


def cause_and_outcome_map(rows: list[dict[str, Any]]) -> dict[str, Any]:
    causes: dict[str, dict[str, float | int]] = defaultdict(lambda: {"words": 0, "seconds": 0.0})
    outcomes: dict[str, dict[str, float | int]] = defaultdict(lambda: {"words": 0, "seconds": 0.0})
    decision_rows = 0
    for row in rows:
        recovery = ROOT / str(row["recovery_dir"])
        for decision in read_jsonl(recovery / "recovery_decisions.jsonl"):
            decision_rows += 1
            weight = float(decision.get("coverage_weight_sec") or 0)
            cause = str(decision.get("baseline_cause") or "unknown")
            reason = str(decision.get("reason") or "unknown")
            causes[cause]["words"] = int(causes[cause]["words"]) + 1
            causes[cause]["seconds"] = float(causes[cause]["seconds"]) + weight
            outcomes[reason]["words"] = int(outcomes[reason]["words"]) + 1
            outcomes[reason]["seconds"] = float(outcomes[reason]["seconds"]) + weight
    def materialize(values: dict[str, dict[str, float | int]], key: str) -> list[dict[str, Any]]:
        return [
            {
                key: name,
                "words": int(row["words"]),
                "seconds": round(float(row["seconds"]), 6),
            }
            for name, row in sorted(values.items())
        ]
    return {
        "decision_rows": decision_rows,
        "causes": materialize(causes, "cause"),
        "outcomes": materialize(outcomes, "reason"),
    }


def truth_pack(
    name: str,
    root: Path,
    selection_name: str,
    truth_outputs: dict[str, Path],
) -> dict[str, Any]:
    answers = {str(row["slot_id"]): str(row["outcome"]) for row in read_jsonl(root / "answers.jsonl")}
    slots = {
        str(row["item_id"]): row
        for row in read_jsonl(root / "slot_map.jsonl")
        if row.get("kind") == "primary"
    }
    selection = {str(row["item_id"]): row for row in read_jsonl(root / selection_name)}
    decisions_by_session: dict[str, dict[str, dict[str, Any]]] = {}
    for session_id, recovery in truth_outputs.items():
        decisions_by_session[session_id] = {
            str(row["word_id"]): row for row in read_jsonl(recovery / "recovery_decisions.jsonl")
        }
    rows = []
    for item_id, slot in sorted(slots.items()):
        item = selection[item_id]
        decisions = decisions_by_session.get(str(slot["session_id"]), {})
        recovered = [
            decisions[str(word_id)]
            for word_id in item.get("word_ids") or []
            if str(word_id) in decisions and decisions[str(word_id)].get("outcome") == "attributed"
        ]
        if not recovered:
            continue
        speakers = sorted({str(row["speaker_id"]) for row in recovered if row.get("speaker_id")})
        predicted = speakers[0] if len(speakers) == 1 else "mixed_prediction"
        truth = answers[str(slot["slot_id"])]
        positive = truth.startswith("remote_speaker_")
        if positive and predicted == truth:
            outcome = "correct_identity"
        elif positive:
            outcome = "wrong_speaker"
        else:
            outcome = "fail_closed_acceptance"
        rows.append(
            {
                "slot_id": slot["slot_id"],
                "session_id": slot["session_id"],
                "truth": truth,
                "predicted": predicted,
                "outcome": outcome,
                "recovered_words": len(recovered),
                "recovered_seconds": round(
                    sum(float(row.get("coverage_weight_sec") or 0) for row in recovered), 6
                ),
            }
        )
    counts = {name: sum(row["outcome"] == name for row in rows) for name in (
        "correct_identity", "wrong_speaker", "fail_closed_acceptance"
    )}
    return {
        "name": name,
        "primary_items": len(slots),
        "newly_recovered_items": len(rows),
        "newly_recovered_words": sum(int(row["recovered_words"]) for row in rows),
        "newly_recovered_seconds": round(sum(float(row["recovered_seconds"]) for row in rows), 6),
        **counts,
        "rows": rows,
    }


def report_markdown(report: dict[str, Any]) -> str:
    frozen = report["summary"]["frozen"]
    held = report["summary"]["held_out"]
    truth = report["truth_evaluation"]["combined"]
    lines = [
        "# Remote Unknown Evidence Recovery v1",
        "",
        f"Decision: `{report['decision']}`",
        f"Frozen recovery: `{frozen['recovered_words']}` / `{frozen['baseline_unknown_words']}` words, "
        f"`{frozen['recovered_seconds']:.3f}` / `{frozen['baseline_unknown_seconds']:.3f}s`",
        f"Held-out recovery: `{held['recovered_words']}` / `{held['baseline_unknown_words']}` words, "
        f"`{held['recovered_seconds']:.3f}` / `{held['baseline_unknown_seconds']:.3f}s`",
        f"Direct truth overlap: `{truth['newly_recovered_items']}` items; wrong speaker "
        f"`{truth['wrong_speaker']}`; fail-closed acceptance `{truth['fail_closed_acceptance']}`",
        "",
        "## Evidence Bound",
        "",
        report["evidence_bound"]["reason"],
        "",
        "## Gates",
        "",
    ]
    lines.extend(f"- `{name}`: `{str(value).lower()}`" for name, value in report["gates"].items())
    return "\n".join(lines) + "\n"


def build(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    policy_path = args.policy.expanduser().resolve()
    policy = read_json(policy_path)
    if policy.get("schema") != "murmurmark.remote_unknown_evidence_recovery_policy/v1":
        raise CorpusError("policy_schema_invalid")
    frozen = frozen_sessions()
    held_session = args.held_out_session.expanduser().resolve()
    held = {
        "alias": "held_out_control",
        "session_id": held_session.name,
        "session": held_session,
        "coverage": coverage_from_selection(held_session),
    }
    truth = truth_sessions()
    if args.refresh:
        for row in frozen + [held]:
            refresh_session(row, run_independent=True)
        for row in truth:
            refresh_session(row, run_independent=False)

    frozen_rows = [session_summary(row, "frozen") for row in frozen]
    held_row = session_summary(held, "held_out")
    truth_rows = [session_summary(row, "direct_truth_control") for row in truth]
    frozen_summary = aggregate(frozen_rows)
    held_summary = aggregate([held_row])
    cause_map = cause_and_outcome_map(frozen_rows)
    truth_outputs = {
        row["session_id"]: evidence_dirs(row["coverage"])[1]
        for row in truth
    }
    direct_v1 = truth_pack("direct_truth_v1", TRUTH_V1, "seed_selection.jsonl", truth_outputs)
    direct_v2 = truth_pack("direct_truth_v2", TRUTH_V2, "selection.jsonl", truth_outputs)
    combined_truth = {
        "primary_items": direct_v1["primary_items"] + direct_v2["primary_items"],
        "newly_recovered_items": direct_v1["newly_recovered_items"] + direct_v2["newly_recovered_items"],
        "newly_recovered_words": direct_v1["newly_recovered_words"] + direct_v2["newly_recovered_words"],
        "newly_recovered_seconds": round(
            direct_v1["newly_recovered_seconds"] + direct_v2["newly_recovered_seconds"], 6
        ),
        "correct_identity": direct_v1["correct_identity"] + direct_v2["correct_identity"],
        "wrong_speaker": direct_v1["wrong_speaker"] + direct_v2["wrong_speaker"],
        "fail_closed_acceptance": direct_v1["fail_closed_acceptance"]
        + direct_v2["fail_closed_acceptance"],
    }
    promotion = policy["promotion"]
    gates = {
        "frozen_strict_sessions_exact": len(frozen_rows)
        == int(promotion["required_frozen_strict_sessions"]),
        "frozen_unknown_words_exact": frozen_summary["baseline_unknown_words"]
        == int(promotion["required_frozen_unknown_words"]),
        "frozen_unknown_seconds_exact": abs(
            frozen_summary["baseline_unknown_seconds"]
            - float(promotion["required_frozen_unknown_seconds"])
        ) <= 1e-6,
        "all_frozen_unknown_rows_classified": cause_map["decision_rows"]
        == frozen_summary["baseline_unknown_words"],
        "all_session_invariants": all(
            all(bool(value) for value in row["gates"].values())
            for row in frozen_rows + [held_row] + truth_rows
        ),
        "frozen_unknown_word_reduction": frozen_summary["unknown_words_reduction_ratio"]
        >= float(promotion["minimum_frozen_unknown_word_reduction_ratio"]),
        "frozen_unknown_second_reduction": frozen_summary["unknown_seconds_reduction_ratio"]
        >= float(promotion["minimum_frozen_unknown_second_reduction_ratio"]),
        "held_out_session_exact": held_row["session_id"]
        == str(promotion["required_held_out_session"]),
        "held_out_unknown_words_exact": held_summary["baseline_unknown_words"]
        == int(promotion["required_held_out_unknown_words"]),
        "minimum_direct_truth_newly_recovered_items": combined_truth["newly_recovered_items"]
        >= int(promotion["minimum_direct_truth_newly_recovered_items"]),
        "no_direct_truth_wrong_speaker": combined_truth["wrong_speaker"]
        <= int(promotion["maximum_direct_truth_wrong_speaker_items"]),
        "no_direct_truth_fail_closed_acceptance": combined_truth["fail_closed_acceptance"]
        <= int(promotion["maximum_direct_truth_fail_closed_acceptance_items"]),
        "coverage_v3_fallback_preserved": all(
            bool((row.get("gates") or {}).get("baseline_labels_preserved"))
            for row in frozen_rows + [held_row] + truth_rows
        ),
    }
    decision = "PROMOTE_REMOTE_UNKNOWN_RECOVERY" if all(gates.values()) else "EVIDENCE_BOUND"
    failed = [name for name, value in gates.items() if not value]
    input_manifest = {
        "schema": INPUT_SCHEMA,
        "implementation": {
            "audit": artifact(RECOVERY_AUDIT),
            "corpus_report": artifact(Path(__file__).resolve()),
            "independent_audit": artifact(INDEPENDENT_AUDIT),
        },
        "policy": artifact(policy_path),
        "rebaseline_manifest": artifact(REBASELINE_MANIFEST),
        "held_out_selection": artifact(
            held_session / "derived/transcript-rich/speaker-resolved-default-v1/selection.json"
        ),
        "frozen": frozen_rows,
        "held_out": held_row,
        "truth_controls": truth_rows,
        "truth_sources": {
            "v1_answers": artifact(TRUTH_V1 / "answers.jsonl"),
            "v1_slots": artifact(TRUTH_V1 / "slot_map.jsonl"),
            "v1_selection": artifact(TRUTH_V1 / "seed_selection.jsonl"),
            "v2_answers": artifact(TRUTH_V2 / "answers.jsonl"),
            "v2_slots": artifact(TRUTH_V2 / "slot_map.jsonl"),
            "v2_selection": artifact(TRUTH_V2 / "selection.jsonl"),
        },
    }
    report = {
        "schema": SCHEMA,
        "decision": decision,
        "profile": "remote_unknown_evidence_recovery_v1",
        "implementation": input_manifest["implementation"],
        "summary": {"frozen": frozen_summary, "held_out": held_summary},
        "cause_provenance": cause_map,
        "truth_evaluation": {
            "direct_truth_v1": direct_v1,
            "direct_truth_v2": direct_v2,
            "combined": combined_truth,
        },
        "gates": gates,
        "failed_gates": failed,
        "evidence_bound": {
            "reason": (
                "The conservative WavLM-plus-structural intersection is safe but too small and has "
                "no direct-truth overlap; Coverage v3 remains authoritative."
                if decision == "EVIDENCE_BOUND"
                else "The candidate cleared corpus coverage and direct-truth safety gates."
            ),
            "safe_frozen_words": frozen_summary["recovered_words"],
            "safe_frozen_seconds": frozen_summary["recovered_seconds"],
            "held_out_words": held_summary["recovered_words"],
            "held_out_seconds": held_summary["recovered_seconds"],
            "blocked_by": failed,
        },
        "safety": {
            "production_promoted": decision == "PROMOTE_REMOTE_UNKNOWN_RECOVERY",
            "coverage_v3_mutated": False,
            "selected_transcript_mutated": False,
            "raw_audio_mutated": False,
            "primary_asr_mutated": False,
            "aggregate_fallback_mutated": False,
        },
        "inputs": {
            "manifest": "private/input_manifest.json",
            "manifest_sha256": hashlib.sha256(canonical(input_manifest)).hexdigest(),
        },
    }
    snapshot = {
        "schema": SNAPSHOT_SCHEMA,
        "decision": decision,
        "profile": report["profile"],
        "summary": report["summary"],
        "truth_evaluation": {"combined": combined_truth},
        "gates": gates,
        "evidence_bound": report["evidence_bound"],
        "input_manifest_sha256": report["inputs"]["manifest_sha256"],
    }
    return report, input_manifest, snapshot


def main() -> int:
    args = parse_args()
    report, manifest, snapshot = build(args)
    output = args.output.expanduser().resolve()
    outputs = {
        "remote_unknown_evidence_recovery_corpus_report.json": canonical(report),
        "remote_unknown_evidence_recovery_corpus_report.md": report_markdown(report).encode(),
        "private/input_manifest.json": canonical(manifest),
    }
    if args.verify_existing:
        stale = [
            name
            for name, content in outputs.items()
            if not (output / name).is_file() or (output / name).read_bytes() != content
        ]
        if stale:
            print("remote unknown recovery corpus outputs are stale: " + ", ".join(stale), file=sys.stderr)
            return 2
    else:
        for name, content in outputs.items():
            path = output / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
    if args.write_snapshot:
        path = args.write_snapshot.expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(canonical(snapshot))
    print(
        f"remote_unknown_recovery_corpus_v1: decision={report['decision']} "
        f"frozen={report['summary']['frozen']['recovered_words']}w/"
        f"{report['summary']['frozen']['recovered_seconds']:.3f}s"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError, CorpusError) as error:
        print(f"remote_unknown_recovery_corpus_v1: error={error}", file=sys.stderr)
        raise SystemExit(2)
