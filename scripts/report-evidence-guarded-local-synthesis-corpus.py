#!/usr/bin/env python3
"""Qualify evidence-guarded local synthesis on the frozen six-session corpus."""

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
SCHEMA = "murmurmark.evidence_guarded_local_synthesis_frozen_manifest/v1"
REPORT_SCHEMA = "murmurmark.evidence_guarded_local_synthesis_corpus_report/v1"
DEFAULT_SOURCE_MANIFEST = ROOT / "docs/testing/reviewed-speaker-memory-v1-manifest.json"
DEFAULT_OUT_DIR = ROOT / "sessions/_reports/evidence-guarded-local-synthesis-v1"
INTEGRITY_GATES = (
    "minimum_frozen_sessions",
    "all_sessions_passed",
    "published_unsupported_claims_zero",
    "all_replays_deterministic",
    "all_references_exact",
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
    "evidence_guarded_local_synthesis_corpus",
    ROOT / "scripts/materialize-evidence-guarded-local-synthesis.py",
)
memory_corpus = load_module(
    "reviewed_speaker_memory_corpus_for_local_synthesis",
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
        "fingerprint": local.repository_identity(path),
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
    decision_path = session / "review/.local-synthesis-corpus-v1.json"
    naming_root = session / "derived/transcript-rich/.local-synthesis-corpus-v1"
    memory_root = session / "derived/meeting-memory/.local-synthesis-speakers-v1"
    output_root = session / "derived/meeting-memory/.local-synthesis-corpus-v1"
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
        synthesis_path = local.artifact_path(first, session, "synthesis_json")
        model_run_path = local.artifact_path(first, session, "model_run_json")
        transcript_path = local.artifact_path(first, session, "transcript")
        verdict_path = local.artifact_path(first, session, "quality_verdict")
        if None in (synthesis_path, model_run_path, transcript_path, verdict_path):
            raise RuntimeError("local_synthesis_artifact_missing")
        synthesis = json.loads(synthesis_path.read_text(encoding="utf-8"))
        model_run = json.loads(model_run_path.read_text(encoding="utf-8"))
        memory_manifest, reasons = local.speaker_memory.verify_handoff(
            session, decision_path, memory_root, naming_root
        )
        if memory_manifest is None:
            raise RuntimeError("speaker_memory_invalid:" + ",".join(reasons))
        expected_transcript = local.speaker_memory.artifact_path(memory_manifest, session, "transcript")
        expected_verdict = local.speaker_memory.artifact_path(memory_manifest, session, "quality_verdict")
        after = protected_hashes(session)
        metrics = synthesis.get("metrics") or {}
        gates = {
            "handoff_ready": first.get("state") == "ready",
            "deterministic_replay": (
                first.get("semantic_fingerprint") == second.get("semantic_fingerprint")
                and pointer.read_bytes() == pointer_bytes
            ),
            "referential_integrity": metrics.get("referential_integrity") is True,
            "published_unsupported_claims_zero": metrics.get("published_unsupported_claims") == 0,
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
                    "prompt_rendered_sha256": (model_run.get("prompt") or {}).get("rendered_sha256"),
                    "prompt_eval_count": model_run.get("prompt_eval_count"),
                    "eval_count": model_run.get("eval_count"),
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
        endpoint = local.validate_loopback_endpoint(str(policy["runtime"]["endpoint"]))
        local.request_json(
            f"{endpoint}/api/generate",
            {"model": policy["model"]["name"], "keep_alive": 0},
            30,
        )
    except Exception:
        pass


def synthetic_checker() -> tuple[bool, str | None]:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/check-evidence-guarded-local-synthesis.py")],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    return result.returncode == 0, result.stdout.strip() or None


def qualification_contract(
    policy: dict[str, Any], source_manifest: Path
) -> dict[str, Any]:
    prompt_path = local.repository_path(policy["prompt"]["path"])
    return {
        "source_manifest": local.repository_identity(source_manifest),
        "materializer": local.implementation(),
        "corpus_reporter": implementation(),
        "prompt": local.repository_identity(prompt_path),
        "generation": policy["generation"],
        "prompt_chunking": policy["prompt_chunking"],
        "support_gates": policy["support_gates"],
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
            rows.append(analyze_session(session, policy_file))
    finally:
        unload_model(policy)
    checker_passed, checker_output = synthetic_checker()
    passed_rows = [row for row in rows if row["status"] == "passed"]
    metrics_rows = [row.get("metrics") or {} for row in passed_rows]
    proposed = sum(int(row.get("proposed_claims") or 0) for row in metrics_rows)
    accepted = sum(int(row.get("accepted_claims") or 0) for row in metrics_rows)
    rejected = sum(int(row.get("rejected_claims") or 0) for row in metrics_rows)
    safety_rejected = sum(int(row.get("safety_rejected_claims") or 0) for row in metrics_rows)
    selection_hidden = sum(int(row.get("selection_hidden_claims") or 0) for row in metrics_rows)
    available_evidence = sum(
        int(row.get("available_evidence_utterances") or 0) for row in metrics_rows
    )
    accepted_evidence = sum(
        int(row.get("accepted_evidence_utterances") or 0) for row in metrics_rows
    )
    source_needs_review = sum(
        int(row.get("source_needs_review_statements") or 0) for row in metrics_rows
    )
    accepted_needs_review = sum(
        int(row.get("accepted_needs_review_claims") or 0) for row in metrics_rows
    )
    available_categories = sum(int(row.get("available_categories") or 0) for row in metrics_rows)
    accepted_categories = sum(int(row.get("accepted_categories") or 0) for row in metrics_rows)
    performances = [
        run
        for row in passed_rows
        for run in (row.get("performance") or {}).values()
        if isinstance(run, dict)
    ]
    aggregate = {
        "sessions_total": len(rows),
        "sessions_passed": len(passed_rows),
        "sessions_with_accepted_claims": sum(
            int((row.get("metrics") or {}).get("accepted_claims") or 0) > 0 for row in passed_rows
        ),
        "proposed_claims": proposed,
        "accepted_claims": accepted,
        "rejected_claims": rejected,
        "rejected_ratio": round(rejected / proposed, 6) if proposed else 0.0,
        "safety_rejected_claims": safety_rejected,
        "safety_rejected_ratio": round(safety_rejected / proposed, 6) if proposed else 0.0,
        "selection_hidden_claims": selection_hidden,
        "available_evidence_utterances": available_evidence,
        "accepted_evidence_utterances": accepted_evidence,
        "source_needs_review_statements": source_needs_review,
        "accepted_needs_review_claims": accepted_needs_review,
        "published_unsupported_claims": sum(
            int(row.get("published_unsupported_claims") or 0) for row in metrics_rows
        ),
        "available_categories": available_categories,
        "accepted_categories": accepted_categories,
        "category_coverage_ratio": (
            round(accepted_categories / available_categories, 6) if available_categories else 1.0
        ),
        "max_session_wall_sec": max((float(row.get("wall_sec") or 0) for row in performances), default=0.0),
        "max_peak_rss_mb": max(
            (float(row.get("peak_model_rss_mb") or 0) for row in performances), default=0.0
        ),
    }
    gates_policy = policy["corpus_gates"]
    gates = {
        "minimum_frozen_sessions": len(rows) >= int(gates_policy["min_sessions"]),
        "all_sessions_passed": len(rows) == len(passed_rows),
        "minimum_sessions_with_accepted_claims": (
            aggregate["sessions_with_accepted_claims"]
            >= int(gates_policy["min_sessions_with_accepted_claims"])
        ),
        "minimum_accepted_claims": accepted >= int(gates_policy["min_accepted_claims"]),
        "safety_rejected_ratio_within_limit": (
            aggregate["safety_rejected_ratio"]
            <= float(gates_policy["max_safety_rejected_ratio"])
        ),
        "category_coverage": (
            aggregate["category_coverage_ratio"]
            >= float(gates_policy["min_category_coverage_ratio"])
        ),
        "published_unsupported_claims_zero": aggregate["published_unsupported_claims"] == 0,
        "all_replays_deterministic": all(
            (row.get("gates") or {}).get("deterministic_replay") is True for row in passed_rows
        ),
        "all_references_exact": all(
            (row.get("gates") or {}).get("referential_integrity") is True for row in passed_rows
        ),
        "all_ordinary_outputs_unchanged": all(
            (row.get("gates") or {}).get("ordinary_outputs_unchanged") is True for row in passed_rows
        ),
        "runtime_within_limit": aggregate["max_session_wall_sec"] <= float(gates_policy["max_session_wall_sec"]),
        "memory_within_limit": aggregate["max_peak_rss_mb"] <= float(gates_policy["max_peak_rss_mb"]),
        "synthetic_contract_checker": checker_passed,
    }
    decision = (
        "PROMOTE_OPTIONAL_LOCAL_SYNTHESIS"
        if gates and all(gates.values())
        else "DO_NOT_PROMOTE"
    )
    qualification_complete = all(gates.get(name) is True for name in INTEGRITY_GATES)
    return {
        "schema": REPORT_SCHEMA,
        "version": 1,
        "generator": implementation(),
        "decision": decision,
        "qualification_complete": qualification_complete,
        "scope": "frozen_six_session_local_synthesis_qualification",
        "policy": memory_corpus.identity(policy_file, ROOT),
        "qualification_contract": qualification_contract(policy, source_manifest),
        "model": local.inspect_runtime(policy),
        "metrics": aggregate,
        "gates": gates,
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
        "sessions": sessions,
        "constraints": report["constraints"],
    }


def render_markdown(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    lines = [
        "# Evidence-Guarded Local Synthesis v1 Corpus",
        "",
        f"- Decision: `{report['decision']}`",
        f"- Sessions: `{metrics['sessions_passed']}/{metrics['sessions_total']}`",
        f"- Accepted/rejected claims: `{metrics['accepted_claims']}/{metrics['rejected_claims']}`",
        f"- Safety rejected/selection hidden: `{metrics['safety_rejected_claims']}/{metrics['selection_hidden_claims']}`",
        f"- Category coverage: `{metrics['category_coverage_ratio']}`",
        f"- Evidence utterances accepted/available: `{metrics['accepted_evidence_utterances']}/{metrics['available_evidence_utterances']}`",
        f"- Review-marked claims accepted/source: `{metrics['accepted_needs_review_claims']}/{metrics['source_needs_review_statements']}`",
        f"- Maximum wall time: `{metrics['max_session_wall_sec']}s`",
        f"- Maximum model RSS: `{metrics['max_peak_rss_mb']} MB`",
        "",
        "## Sessions",
        "",
    ]
    for row in report["sessions"]:
        metrics_row = row.get("metrics") or {}
        lines.append(
            f"- `{row['session_id']}`: `{row['status']}`, accepted "
            f"`{metrics_row.get('accepted_claims', 0)}`, rejected `{metrics_row.get('rejected_claims', 0)}`"
        )
    lines.extend(["", "## Gates", ""])
    lines.extend(f"- `{key}`: `{value}`" for key, value in report["gates"].items())
    return "\n".join(lines) + "\n"


def verify_frozen_contract(
    policy_file: Path, source_manifest: Path, frozen_manifest: Path
) -> list[str]:
    reasons: list[str] = []
    try:
        policy = local.validate_policy(policy_file, allow_unpromoted=True)
        frozen = json.loads(frozen_manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError, local.LocalSynthesisError) as error:
        return [f"frozen_contract_unreadable:{error}"]
    if policy.get("decision") != "DO_NOT_PROMOTE":
        reasons.append("policy_decision_not_final_do_not_promote")
    if frozen.get("schema") != SCHEMA or frozen.get("decision") != policy.get("decision"):
        reasons.append("frozen_decision_mismatch")
    if frozen.get("qualification_complete") is not True:
        reasons.append("qualification_not_complete")
    contract = frozen.get("qualification_contract") or {}
    expected_contract = qualification_contract(policy, source_manifest)
    if contract != expected_contract:
        reasons.append("qualification_contract_mismatch")
    policy_source = policy.get("source") or {}
    if policy_source.get("materializer") != local.implementation():
        reasons.append("policy_materializer_mismatch")
    if policy_source.get("corpus_reporter") != implementation():
        reasons.append("policy_corpus_reporter_mismatch")
    expected_frozen = local.repository_identity(frozen_manifest)
    if policy_source.get("frozen_manifest") != expected_frozen:
        reasons.append("policy_frozen_manifest_mismatch")
    if policy_source.get("frozen_manifest_path") != expected_frozen["path"]:
        reasons.append("policy_frozen_manifest_path_mismatch")
    gates = frozen.get("gates") or {}
    if gates.get("safety_rejected_ratio_within_limit") is not False:
        reasons.append("expected_safety_rejection_blocker_missing")
    if gates.get("published_unsupported_claims_zero") is not True:
        reasons.append("unsupported_claim_gate_failed")
    if any(gates.get(name) is not True for name in INTEGRITY_GATES):
        reasons.append("frozen_integrity_gate_failed")
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
        print("evidence-guarded local synthesis frozen DO_NOT_PROMOTE verified")
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
    print("evidence_guarded_local_synthesis_corpus:")
    print(f"  decision: {report['decision']}")
    print(f"  sessions: {report['metrics']['sessions_passed']}/{report['metrics']['sessions_total']}")
    print(f"  accepted_claims: {report['metrics']['accepted_claims']}")
    print(f"  rejected_claims: {report['metrics']['rejected_claims']}")
    print(f"  category_coverage_ratio: {report['metrics']['category_coverage_ratio']}")
    if frozen_match is not None:
        print(f"  frozen_manifest_match: {str(frozen_match).lower()}")
    print(f"  report: {out_dir / 'corpus_report.json'}")
    if args.strict and (not report["qualification_complete"] or frozen_match is False):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
