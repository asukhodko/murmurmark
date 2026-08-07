#!/usr/bin/env python3
"""Qualify ID-only local note selection on the frozen six-session corpus."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_VERSION = "0.1.0"
SCHEMA = "murmurmark.evidence_only_local_note_selection_frozen_manifest/v1"
REPORT_SCHEMA = "murmurmark.evidence_only_local_note_selection_corpus_report/v1"
DEFAULT_SOURCE_MANIFEST = ROOT / "docs/testing/reviewed-speaker-memory-v1-manifest.json"
DEFAULT_OUT_DIR = ROOT / "sessions/_reports/evidence-only-local-note-selection-v1"
INTEGRITY_GATES = (
    "minimum_frozen_sessions",
    "all_sessions_passed",
    "selection_contract_exact",
    "baseline_high_confidence_retained",
    "all_replays_deterministic",
    "all_references_exact",
    "all_source_text_exact",
    "all_ordinary_outputs_unchanged",
    "synthetic_contract_checker",
)


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


local = load_module(
    "evidence_only_local_note_selection_corpus",
    ROOT / "scripts/materialize-evidence-only-local-note-selection.py",
)
memory_corpus = load_module(
    "reviewed_speaker_memory_corpus_for_id_selection",
    ROOT / "scripts/report-reviewed-speaker-memory-corpus.py",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sessions", nargs="*", type=Path)
    parser.add_argument("--source-manifest", type=Path, default=DEFAULT_SOURCE_MANIFEST)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--write-frozen-manifest", type=Path)
    parser.add_argument("--frozen-manifest", type=Path)
    parser.add_argument("--verify-frozen-only", action="store_true")
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args()


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_bytes(payload))


def implementation() -> dict[str, Any]:
    path = Path(__file__).resolve()
    return {
        "script": path.name,
        "version": SCRIPT_VERSION,
        "fingerprint": local.legacy.repository_identity(path),
    }


def resolve_session(path: Path) -> Path:
    candidate = path.expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    if len(candidate.parts) == 1:
        return (ROOT / "sessions" / candidate).resolve()
    return (Path.cwd() / candidate).resolve()


def source_sessions(path: Path) -> list[Path]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    sessions = payload.get("sessions") or []
    return [ROOT / "sessions" / str(row["session_id"]) for row in sessions]


def cleanup(paths: list[Path]) -> None:
    for path in reversed(paths):
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        elif path.exists():
            path.unlink()


def protected_hashes(session: Path) -> dict[str, str]:
    return {
        key: sha256_file(path)
        for key, path in sorted(memory_corpus.protected_paths(session).items())
    }


def analyze_session(session: Path, policy_file: Path) -> dict[str, Any]:
    row: dict[str, Any] = {"session_id": session.name, "status": "failed", "reasons": []}
    decision_path = session / "review/.evidence-selection-corpus-v1.json"
    naming_root = session / "derived/transcript-rich/.evidence-selection-corpus-v1"
    memory_root = session / "derived/meeting-memory/.evidence-selection-speakers-v1"
    output_root = session / "derived/meeting-memory/.evidence-selection-corpus-v1"
    temporary_paths = [decision_path, naming_root, memory_root, output_root]
    cleanup(temporary_paths)
    try:
        before = protected_hashes(session)
        template = local.speaker_memory.naming.template_payload(session)
        decisions = memory_corpus.complete_keep_anonymous(template)
        write_json(decision_path, decisions)
        local.speaker_memory.naming.build_handoff(session, decision_path, naming_root)
        local.speaker_memory.build_handoff(
            session,
            decision_path,
            memory_root,
            reviewed_root=naming_root,
        )
        first, first_performance = local.build_handoff(
            session,
            decision_path,
            memory_root,
            output_root,
            policy_file,
            reviewed_root=naming_root,
            allow_unpromoted=True,
            keep_alive="30m",
        )
        pointer = output_root / "handoff_manifest.json"
        pointer_bytes = pointer.read_bytes()
        second, second_performance = local.build_handoff(
            session,
            decision_path,
            memory_root,
            output_root,
            policy_file,
            reviewed_root=naming_root,
            allow_unpromoted=True,
            keep_alive="30m",
        )
        selection_path = local.artifact_path(first, session, "selection_json")
        catalog_path = local.artifact_path(first, session, "catalog_json")
        model_run_path = local.artifact_path(first, session, "model_run_json")
        transcript_path = local.artifact_path(first, session, "transcript")
        verdict_path = local.artifact_path(first, session, "quality_verdict")
        if None in (selection_path, catalog_path, model_run_path, transcript_path, verdict_path):
            raise RuntimeError("evidence_selection_artifact_missing")
        selection = json.loads(selection_path.read_text(encoding="utf-8"))
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        model_run = json.loads(model_run_path.read_text(encoding="utf-8"))
        memory_manifest, reasons = local.speaker_memory.verify_handoff(
            session, decision_path, memory_root, naming_root
        )
        if memory_manifest is None:
            raise RuntimeError("speaker_memory_invalid:" + ",".join(reasons))
        expected_transcript = local.speaker_memory.artifact_path(
            memory_manifest, session, "transcript"
        )
        expected_verdict = local.speaker_memory.artifact_path(
            memory_manifest, session, "quality_verdict"
        )
        after = protected_hashes(session)
        metrics = selection.get("metrics") or {}
        selected = [
            item
            for category in local.CATEGORIES
            for item in (selection.get("selected") or {}).get(category) or []
        ]
        by_id = {
            item["statement_id"]: item for item in catalog.get("candidates") or []
        }
        source_projection_exact = all(
            item.get("statement_id") in by_id
            and item.get("text") == by_id[item["statement_id"]].get("text")
            and item.get("text_sha256") == by_id[item["statement_id"]].get("text_sha256")
            and item.get("evidence_utterance_ids")
            == by_id[item["statement_id"]].get("evidence_utterance_ids")
            and item.get("speaker_evidence") == by_id[item["statement_id"]].get("speaker_evidence")
            for item in selected
        )
        gates = {
            "handoff_ready": first.get("state") == "ready",
            "deterministic_replay": (
                first.get("semantic_fingerprint") == second.get("semantic_fingerprint")
                and pointer.read_bytes() == pointer_bytes
                and model_run.get("raw_response_sha256")
                == json.loads(
                    local.artifact_path(second, session, "model_run_json").read_text(encoding="utf-8")
                ).get("raw_response_sha256")
            ),
            "model_contract_valid": metrics.get("model_contract_valid") is True,
            "referential_integrity": metrics.get("referential_integrity") is True,
            "exact_source_publication": (
                metrics.get("exact_source_publication") is True and source_projection_exact
            ),
            "baseline_high_confidence_retained": (
                metrics.get("baseline_high_confidence_retention_ratio") == 1.0
            ),
            "published_generated_claims_zero": metrics.get("published_generated_claims") == 0,
            "ordinary_outputs_unchanged": before == after,
            "source_transcript_exact": (
                expected_transcript is not None
                and transcript_path.read_bytes() == expected_transcript.read_bytes()
            ),
            "source_verdict_exact": (
                expected_verdict is not None
                and verdict_path.read_bytes() == expected_verdict.read_bytes()
            ),
        }
        failed = [name for name, passed in gates.items() if not passed]
        row.update(
            {
                "status": "passed" if not failed else "failed",
                "reasons": failed,
                "gates": gates,
                "source": {
                    "evidence_handoff": memory_corpus.identity(
                        session / "derived/handoff-v2/handoff_manifest.json", session
                    ),
                    "anonymous_handoff": memory_corpus.identity(
                        session
                        / local.speaker_memory.naming.rich.DEFAULT_OUTPUT_DIR
                        / "handoff_manifest.json",
                        session,
                    ),
                },
                "model_run": {
                    "raw_response_sha256": model_run.get("raw_response_sha256"),
                    "prompt_rendered_sha256": (model_run.get("prompt") or {}).get(
                        "rendered_sha256"
                    ),
                    "prompt_input_sha256": (model_run.get("prompt") or {}).get("input_sha256"),
                    "prompt_rendered_bytes": (model_run.get("prompt") or {}).get(
                        "rendered_bytes"
                    ),
                    "prompt_eval_count": model_run.get("prompt_eval_count"),
                    "eval_count": model_run.get("eval_count"),
                },
                "selection": {
                    "catalog_sha256": selection.get("catalog_sha256"),
                    "contract_errors": selection.get("contract_errors") or [],
                    "model_container_ids": selection.get("model_container_ids") or {},
                    "model_ranked_ids": selection.get("model_ranked_ids") or [],
                    "model_selected_ids": selection.get("model_selected_ids") or {},
                    "policy_dropped_ids": selection.get("policy_dropped_ids") or [],
                    "category_normalizations": selection.get("category_normalizations") or [],
                    "final_selected_ids": {
                        category: [
                            item["statement_id"]
                            for item in (selection.get("selected") or {}).get(category) or []
                        ]
                        for category in local.CATEGORIES
                    },
                },
                "metrics": metrics,
                "performance": {
                    "first": first_performance,
                    "second": second_performance,
                },
            }
        )
    except Exception as error:  # fail-open corpus accounting
        row["reasons"] = [f"{type(error).__name__}:{error}"]
    finally:
        cleanup(temporary_paths)
    return row


def unload_model(policy: dict[str, Any]) -> None:
    try:
        endpoint = local.legacy.validate_loopback_endpoint(str(policy["runtime"]["endpoint"]))
        local.legacy.request_json(
            f"{endpoint}/api/generate",
            {"model": policy["model"]["name"], "keep_alive": 0},
            30,
        )
    except Exception:
        pass


def synthetic_checker() -> tuple[bool, str | None]:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/check-evidence-only-local-note-selection.py")],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    return result.returncode == 0, result.stdout.strip() or None


def qualification_contract(policy: dict[str, Any], source_manifest: Path) -> dict[str, Any]:
    prompt_path = local.legacy.repository_path(policy["prompt"]["path"])
    return {
        "source_manifest": local.legacy.repository_identity(source_manifest),
        "materializer": local.implementation(),
        "model_runtime_helper": local.legacy.implementation(),
        "corpus_reporter": implementation(),
        "synthetic_contract_checker": local.legacy.repository_identity(
            ROOT / "scripts/check-evidence-only-local-note-selection.py"
        ),
        "prompt": local.legacy.repository_identity(prompt_path),
        "generation": policy["generation"],
        "prompt_budget": policy["prompt_budget"],
        "selection": policy["selection"],
        "limits": policy["limits"],
        "corpus_gates": policy["corpus_gates"],
    }


def build_report(
    sessions: list[Path], policy_file: Path, source_manifest: Path
) -> dict[str, Any]:
    policy = local.validate_policy(policy_file, allow_unpromoted=True)
    rows: list[dict[str, Any]] = []
    try:
        for session in sessions:
            print(f"[evidence-selection] {session.name}", flush=True)
            rows.append(analyze_session(session, policy_file))
    finally:
        unload_model(policy)
    checker_passed, checker_output = synthetic_checker()
    passed_rows = [row for row in rows if row["status"] == "passed"]
    metrics_rows = [row.get("metrics") or {} for row in passed_rows]
    source_candidates = sum(int(row.get("source_candidates") or 0) for row in metrics_rows)
    selected = sum(int(row.get("selected_statements") or 0) for row in metrics_rows)
    source_review = sum(int(row.get("source_review_marked") or 0) for row in metrics_rows)
    selected_review = sum(int(row.get("selected_review_marked") or 0) for row in metrics_rows)
    source_high = sum(int(row.get("baseline_high_confidence") or 0) for row in metrics_rows)
    retained_high = sum(
        int(row.get("retained_baseline_high_confidence") or 0) for row in metrics_rows
    )
    available_categories = sum(int(row.get("available_categories") or 0) for row in metrics_rows)
    selected_categories = sum(int(row.get("selected_categories") or 0) for row in metrics_rows)
    available_speakers = sum(int(row.get("available_speakers") or 0) for row in metrics_rows)
    selected_speakers = sum(int(row.get("selected_speakers") or 0) for row in metrics_rows)
    category_metrics = {
        metric: {
            category: sum(
                int(((row.get(metric) or {}).get(category)) or 0)
                for row in metrics_rows
            )
            for category in local.CATEGORIES
        }
        for metric in (
            "source_candidates_by_category",
            "selected_statements_by_category",
            "source_review_marked_by_category",
            "selected_review_marked_by_category",
        )
    }
    performances = [
        run
        for row in passed_rows
        for run in (row.get("performance") or {}).values()
        if isinstance(run, dict)
    ]
    aggregate = {
        "sessions_total": len(rows),
        "sessions_passed": len(passed_rows),
        "sessions_with_review_selection": sum(
            int((row.get("metrics") or {}).get("selected_review_marked") or 0) > 0
            for row in passed_rows
        ),
        "source_candidates": source_candidates,
        "selected_statements": selected,
        "source_review_marked": source_review,
        "selected_review_marked": selected_review,
        "selected_review_ratio": round(selected_review / source_review, 6) if source_review else 0.0,
        "review_compression_ratio": round(1.0 - selected_review / source_review, 6)
        if source_review
        else 1.0,
        "baseline_high_confidence": source_high,
        "baseline_high_confidence_population_present": source_high > 0,
        "retained_baseline_high_confidence": retained_high,
        "baseline_high_confidence_retention_ratio": round(retained_high / source_high, 6)
        if source_high
        else 1.0,
        "available_categories": available_categories,
        "selected_categories": selected_categories,
        "category_coverage_ratio": round(selected_categories / available_categories, 6)
        if available_categories
        else 1.0,
        "available_speakers": available_speakers,
        "selected_speakers": selected_speakers,
        "speaker_coverage_ratio": round(selected_speakers / available_speakers, 6)
        if available_speakers
        else 1.0,
        **category_metrics,
        "selection_contract_errors": sum(
            int(row.get("selection_contract_errors") or 0) for row in metrics_rows
        ),
        "published_generated_claims": sum(
            int(row.get("published_generated_claims") or 0) for row in metrics_rows
        ),
        "max_session_wall_sec": max(
            (float(row.get("wall_sec") or 0) for row in performances), default=0.0
        ),
        "max_peak_rss_mb": max(
            (float(row.get("peak_model_rss_mb") or 0) for row in performances), default=0.0
        ),
    }
    gate_policy = policy["corpus_gates"]
    gates = {
        "minimum_frozen_sessions": len(rows) >= int(gate_policy["min_sessions"]),
        "all_sessions_passed": len(rows) == len(passed_rows),
        "selection_contract_exact": (
            aggregate["selection_contract_errors"]
            <= int(gate_policy["max_selection_contract_errors"])
        ),
        "baseline_high_confidence_retained": (
            aggregate["baseline_high_confidence_retention_ratio"]
            >= float(gate_policy["min_baseline_high_confidence_retention_ratio"])
        ),
        "useful_review_compression": (
            aggregate["selected_review_ratio"]
            <= float(gate_policy["max_selected_review_ratio"])
            and aggregate["selected_review_marked"]
            >= int(gate_policy["min_selected_review_marked"])
            and aggregate["sessions_with_review_selection"]
            >= int(gate_policy["min_sessions_with_review_selection"])
        ),
        "category_coverage": (
            aggregate["category_coverage_ratio"]
            >= float(gate_policy["min_category_coverage_ratio"])
        ),
        "speaker_coverage": (
            aggregate["speaker_coverage_ratio"]
            >= float(gate_policy["min_speaker_coverage_ratio"])
        ),
        "published_generated_claims_zero": aggregate["published_generated_claims"] == 0,
        "all_replays_deterministic": all(
            (row.get("gates") or {}).get("deterministic_replay") is True for row in passed_rows
        ),
        "all_references_exact": all(
            (row.get("gates") or {}).get("referential_integrity") is True for row in passed_rows
        ),
        "all_source_text_exact": all(
            (row.get("gates") or {}).get("exact_source_publication") is True
            for row in passed_rows
        ),
        "all_ordinary_outputs_unchanged": all(
            (row.get("gates") or {}).get("ordinary_outputs_unchanged") is True
            for row in passed_rows
        ),
        "runtime_within_limit": (
            aggregate["max_session_wall_sec"] <= float(gate_policy["max_session_wall_sec"])
        ),
        "memory_within_limit": (
            aggregate["max_peak_rss_mb"] <= float(gate_policy["max_peak_rss_mb"])
        ),
        "synthetic_contract_checker": checker_passed,
    }
    decision = (
        "PROMOTE_OPTIONAL_EVIDENCE_SELECTION"
        if gates and all(gates.values())
        else "DO_NOT_PROMOTE"
    )
    qualification_complete = all(gates.get(name) is True for name in INTEGRITY_GATES)
    limitations = []
    if not aggregate["baseline_high_confidence_population_present"]:
        limitations.append(
            "baseline_high_confidence_retention_is_vacuous:no_high_confidence_items_in_frozen_corpus"
        )
    return {
        "schema": REPORT_SCHEMA,
        "version": 1,
        "generator": implementation(),
        "decision": decision,
        "qualification_complete": qualification_complete,
        "scope": "frozen_six_session_exact_evidence_selection_qualification",
        "policy": memory_corpus.identity(policy_file, ROOT),
        "qualification_contract": qualification_contract(policy, source_manifest),
        "model": local.legacy.inspect_runtime(policy),
        "metrics": aggregate,
        "gates": gates,
        "limitations": limitations,
        "sessions": rows,
        "synthetic_checker": {"passed": checker_passed, "output": checker_output},
        "constraints": policy["constraints"],
    }


def frozen_payload(report: dict[str, Any]) -> dict[str, Any]:
    sessions = []
    for row in report["sessions"]:
        sessions.append(
            {
                "session_id": row["session_id"],
                "status": row["status"],
                "reasons": row["reasons"],
                "gates": row.get("gates") or {},
                "source": row.get("source") or {},
                "model_run": row.get("model_run") or {},
                "selection": row.get("selection") or {},
                "metrics": row.get("metrics") or {},
            }
        )
    return {
        "schema": SCHEMA,
        "version": 1,
        "generator": report["generator"],
        "decision": report["decision"],
        "qualification_complete": report["qualification_complete"],
        "scope": report["scope"],
        "qualification_contract": report["qualification_contract"],
        "model": report["model"],
        "metrics": {
            key: value
            for key, value in report["metrics"].items()
            if key not in {"max_session_wall_sec", "max_peak_rss_mb"}
        },
        "gates": {
            key: value
            for key, value in report["gates"].items()
            if key not in {"runtime_within_limit", "memory_within_limit"}
        },
        "limitations": report["limitations"],
        "sessions": sessions,
        "constraints": report["constraints"],
    }


def render_markdown(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    lines = [
        "# Evidence-Only Local Note Selection v1 Corpus",
        "",
        f"- Decision: `{report['decision']}`",
        f"- Sessions: `{metrics['sessions_passed']}/{metrics['sessions_total']}`",
        f"- Selected/source statements: `{metrics['selected_statements']}/{metrics['source_candidates']}`",
        f"- Review-marked selected/source: `{metrics['selected_review_marked']}/{metrics['source_review_marked']}`",
        f"- Review compression: `{metrics['review_compression_ratio']}`",
        f"- High-confidence retention: `{metrics['baseline_high_confidence_retention_ratio']}`",
        f"- High-confidence population present: `{metrics['baseline_high_confidence_population_present']}`",
        f"- Category/speaker coverage: `{metrics['category_coverage_ratio']}/{metrics['speaker_coverage_ratio']}`",
        f"- Maximum wall time: `{metrics['max_session_wall_sec']}s`",
        f"- Maximum model RSS: `{metrics['max_peak_rss_mb']} MB`",
        "",
        "## Sessions",
        "",
    ]
    for row in report["sessions"]:
        item = row.get("metrics") or {}
        lines.append(
            f"- `{row['session_id']}`: `{row['status']}`, selected "
            f"`{item.get('selected_statements', 0)}`, review compression "
            f"`{item.get('review_compression_ratio', 0)}`"
        )
    lines.extend(["", "## Gates", ""])
    lines.extend(f"- `{key}`: `{value}`" for key, value in report["gates"].items())
    if report["limitations"]:
        lines.extend(["", "## Limitations", ""])
        lines.extend(f"- `{item}`" for item in report["limitations"])
    return "\n".join(lines) + "\n"


def verify_frozen_contract(
    policy_file: Path, source_manifest: Path, frozen_manifest: Path
) -> list[str]:
    reasons: list[str] = []
    try:
        policy = local.validate_policy(policy_file, allow_unpromoted=True)
        frozen = json.loads(frozen_manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError, local.SelectionError) as error:
        return [f"frozen_contract_unreadable:{error}"]
    if policy.get("decision") not in {
        "PROMOTE_OPTIONAL_EVIDENCE_SELECTION",
        "DO_NOT_PROMOTE",
    }:
        reasons.append("policy_decision_not_final")
    if frozen.get("schema") != SCHEMA or frozen.get("decision") != policy.get("decision"):
        reasons.append("frozen_decision_mismatch")
    if frozen.get("qualification_complete") is not True:
        reasons.append("qualification_not_complete")
    expected_contract = qualification_contract(policy, source_manifest)
    if frozen.get("qualification_contract") != expected_contract:
        reasons.append("qualification_contract_mismatch")
    source = policy.get("source") or {}
    if source.get("materializer") != local.implementation():
        reasons.append("policy_materializer_mismatch")
    if source.get("corpus_reporter") != implementation():
        reasons.append("policy_corpus_reporter_mismatch")
    expected_frozen = local.legacy.repository_identity(frozen_manifest)
    if source.get("frozen_manifest") != expected_frozen:
        reasons.append("policy_frozen_manifest_mismatch")
    gates = frozen.get("gates") or {}
    if any(gates.get(name) is not True for name in INTEGRITY_GATES):
        reasons.append("frozen_integrity_gate_failed")
    if policy.get("decision") == "PROMOTE_OPTIONAL_EVIDENCE_SELECTION" and any(
        value is not True for value in gates.values()
    ):
        reasons.append("promoted_frozen_gate_failed")
    source_ids = [path.name for path in source_sessions(source_manifest)]
    frozen_ids = [str(row.get("session_id")) for row in frozen.get("sessions") or []]
    if frozen_ids != source_ids:
        reasons.append("frozen_session_set_mismatch")
    return reasons


def main() -> int:
    args = parse_args()
    source = args.source_manifest.expanduser().resolve()
    policy_file = ROOT / local.DEFAULT_POLICY
    if args.verify_frozen_only:
        if args.frozen_manifest is None:
            print("error: --verify-frozen-only requires --frozen-manifest", file=sys.stderr)
            return 2
        reasons = verify_frozen_contract(
            policy_file, source, args.frozen_manifest.expanduser().resolve()
        )
        if reasons:
            for reason in reasons:
                print(reason)
            return 1
        print("evidence-only local note selection frozen decision verified")
        return 0
    sessions = [resolve_session(path) for path in args.sessions] if args.sessions else source_sessions(source)
    if not sessions:
        print("error: no sessions", file=sys.stderr)
        return 2
    missing = [str(path) for path in sessions if not (path / "session.json").is_file()]
    if missing:
        print("error: missing sessions: " + ", ".join(missing), file=sys.stderr)
        return 2
    report = build_report(sessions, policy_file, source)
    frozen = frozen_payload(report)
    out_dir = args.out_dir.expanduser().resolve()
    write_json(out_dir / "corpus_report.json", report)
    (out_dir / "corpus_report.md").write_text(render_markdown(report), encoding="utf-8")
    if args.write_frozen_manifest:
        write_json(args.write_frozen_manifest.expanduser().resolve(), frozen)
    frozen_match: bool | None = None
    if args.frozen_manifest:
        expected = json.loads(args.frozen_manifest.expanduser().resolve().read_text(encoding="utf-8"))
        frozen_match = expected == frozen
    print("evidence_only_local_note_selection_corpus:")
    print(f"  decision: {report['decision']}")
    print(f"  sessions: {report['metrics']['sessions_passed']}/{report['metrics']['sessions_total']}")
    print(f"  selected_review_marked: {report['metrics']['selected_review_marked']}")
    print(f"  review_compression_ratio: {report['metrics']['review_compression_ratio']}")
    print(f"  category_coverage_ratio: {report['metrics']['category_coverage_ratio']}")
    print(f"  speaker_coverage_ratio: {report['metrics']['speaker_coverage_ratio']}")
    if frozen_match is not None:
        print(f"  frozen_manifest_match: {str(frozen_match).lower()}")
    print(f"  report: {out_dir / 'corpus_report.json'}")
    if args.strict and (not report["qualification_complete"] or frozen_match is False):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
