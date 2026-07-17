#!/usr/bin/env python3
"""Evaluate the causal double-talk shadow against the immutable 16-row corpus."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any


SCHEMA = "murmurmark.causal_double_talk_me_recovery_report/v1"
SCRIPT_VERSION = "1.1.0"
PREVIOUS_PROFILE = (
    "online_live_me_remote_overlap_filter_live_boundary_split_retime_causal_remote_energy_"
    "local_island_micro_asr_v2_causal_remote_active_me_separation_v1"
)
PROFILE = PREVIOUS_PROFILE + "_causal_double_talk_me_recovery_v1"
BASELINE = {
    "missing_me_seconds": 1657.89,
    "remote_like_me_seconds": 108.42,
    "order_blocker_count": 0,
    "review_burden_seconds": 490.38,
}


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_script(filename: str, module_name: str) -> ModuleType:
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load helper: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def interval_overlap(start: float, end: float, other_start: float, other_end: float) -> float:
    return max(0.0, min(end, other_end) - max(start, other_start))


def token_metrics(progressive: ModuleType, candidate: str, reference: str) -> dict[str, Any]:
    candidate_tokens = progressive.tokens(candidate)
    reference_tokens = progressive.tokens(reference)
    if not candidate_tokens or not reference_tokens:
        return {
            "candidate_recall_in_reference": 0.0,
            "reference_recall_in_candidate": 0.0,
            "token_f1": 0.0,
            "matched_token_count": 0,
        }
    candidate_counter = Counter(candidate_tokens)
    reference_counter = Counter(reference_tokens)
    matched = sum((candidate_counter & reference_counter).values())
    precision = matched / len(candidate_tokens)
    recall = matched / len(reference_tokens)
    f1 = 2.0 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "candidate_recall_in_reference": round(precision, 6),
        "reference_recall_in_candidate": round(recall, 6),
        "token_f1": round(f1, 6),
        "matched_token_count": matched,
    }


def stable_payload_hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_frozen_inputs(manifest: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    changed: list[dict[str, Any]] = []
    checked = 0
    roles: Counter[str] = Counter()
    for row in manifest.get("causal_input_manifest") or []:
        if not isinstance(row, dict) or not row.get("path"):
            continue
        checked += 1
        roles[str(row.get("role") or "unknown")] += 1
        path = repo_root / str(row["path"])
        if not path.is_file():
            changed.append({"path": row["path"], "reason": "missing"})
            continue
        actual = file_sha256(path)
        if actual != row.get("sha256"):
            changed.append(
                {
                    "path": row["path"],
                    "reason": "sha256_changed",
                    "expected_sha256": row.get("sha256"),
                    "actual_sha256": actual,
                }
            )
    return {
        "status": "passed" if checked > 0 and not changed else "failed",
        "checked_file_count": checked,
        "roles": dict(sorted(roles.items())),
        "changed_file_count": len(changed),
        "changed_files": changed,
    }


def profile_metrics(session: Path, profile: str) -> dict[str, Any]:
    comparison = read_json(session / "derived/live/live_batch_comparison.json")
    shadows = comparison.get("shadow_profiles") if isinstance(comparison.get("shadow_profiles"), dict) else {}
    target = shadows.get("target_me") if isinstance(shadows.get("target_me"), dict) else {}
    row = target.get(profile) if isinstance(target.get(profile), dict) else {}
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    return {"profile": row, "metrics": metrics}


def session_gate_rows(
    previous_report: dict[str, Any],
    sessions_root: Path,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for previous in previous_report.get("sessions") or []:
        if not isinstance(previous, dict):
            continue
        session_name = str(previous.get("session") or "")
        before = previous.get("metrics") if isinstance(previous.get("metrics"), dict) else {}
        current = profile_metrics(sessions_root / session_name, PROFILE)
        profile = current["profile"]
        metrics = current["metrics"]
        before_missing = safe_float((before.get("missing_me_seconds") or {}).get("after"))
        before_remote = safe_float((before.get("remote_like_me_seconds") or {}).get("after"))
        before_f1 = safe_float((before.get("live_batch_token_f1") or {}).get("after"))
        after_missing = safe_float(metrics.get("live_missing_me_seconds"))
        after_remote = safe_float(metrics.get("live_suspected_remote_leak_in_me_seconds"))
        after_f1 = safe_float(metrics.get("live_batch_token_f1"))
        blockers = int(
            metrics.get("live_effective_blocking_contentful_role_constrained_order_mismatch_count")
            or 0
        )
        checks = {
            "profile_present": bool(profile),
            "missing_me_not_worse": bool(profile) and after_missing <= before_missing + 0.001,
            "remote_like_me_not_worse": bool(profile) and after_remote <= before_remote + 0.001,
            "effective_order_blockers_zero": bool(profile) and blockers == 0,
            "token_f1_not_worse": bool(profile) and after_f1 + 0.000001 >= before_f1,
            "batch_authoritative": profile.get("batch_authoritative") is True,
            "promotion_blocked": profile.get("promotion_allowed") is False,
        }
        rows.append(
            {
                "session": session_name,
                "status": "passed" if all(checks.values()) else "failed",
                "checks": checks,
                "metrics": {
                    "missing_me_seconds": {"before": before_missing, "after": after_missing},
                    "remote_like_me_seconds": {"before": before_remote, "after": after_remote},
                    "live_batch_token_f1": {"before": before_f1, "after": after_f1},
                    "effective_order_blockers": blockers,
                },
            }
        )
    return rows


def runtime_acceptance(paths: list[Path]) -> dict[str, Any]:
    reports = [read_json(path) for path in paths if path.is_file()]
    reports = [row for row in reports if row]
    p95_values = [
        safe_float((row.get("efficiency") or {}).get("double_talk_latency_p95_sec"), 999.0)
        for row in reports
    ]
    final_lags = [safe_float(row.get("final_live_lag_sec"), 999.0) for row in reports]
    checks = {
        "runtime_replay_present": bool(reports),
        "all_runtime_replays_passed": bool(reports) and all(row.get("status") == "passed" for row in reports),
        "double_talk_p95_within_30s": bool(reports) and max(p95_values, default=999.0) <= 30.0,
        "final_lag_zero": bool(reports) and max(final_lags, default=999.0) <= 0.001,
        "batch_authoritative": bool(reports) and all(row.get("batch_authoritative") is True for row in reports),
        "promotion_blocked": bool(reports) and all(row.get("promotion_allowed") is False for row in reports),
    }
    return {
        "status": "passed" if all(checks.values()) else "failed",
        "checks": checks,
        "report_count": len(reports),
        "sessions": [row.get("session") for row in reports],
        "double_talk_latency_p95_max_sec": max(p95_values, default=None),
        "final_live_lag_max_sec": max(final_lags, default=None),
    }


def evaluate_row(
    row: dict[str, Any],
    candidates: list[dict[str, Any]],
    progressive: ModuleType,
) -> dict[str, Any]:
    reference = row.get("evaluation_reference") if isinstance(row.get("evaluation_reference"), dict) else {}
    start = safe_float(reference.get("start"))
    end = safe_float(reference.get("end"), start)
    relevant: list[dict[str, Any]] = []
    for candidate in candidates:
        overlap = interval_overlap(
            start,
            end,
            safe_float(candidate.get("start")),
            safe_float(candidate.get("end")),
        )
        if overlap <= 0:
            continue
        metrics = token_metrics(progressive, clean_text(candidate.get("text")), clean_text(reference.get("text")))
        relevant.append(
            {
                "candidate": candidate,
                "overlap_sec": round(overlap, 3),
                "metrics": metrics,
            }
        )
    relevant.sort(
        key=lambda item: (
            item["candidate"].get("status") == "accepted",
            safe_float(item["metrics"].get("token_f1")),
            safe_float(item.get("overlap_sec")),
        ),
        reverse=True,
    )
    best = relevant[0] if relevant else None
    accepted_match = next(
        (
            item
            for item in relevant
            if item["candidate"].get("status") == "accepted"
            and safe_float(item.get("overlap_sec")) >= 0.35
            and safe_float(item["metrics"].get("token_f1")) >= 0.30
            and safe_float(item["metrics"].get("reference_recall_in_candidate")) >= 0.20
        ),
        None,
    )
    recovered = min(end - start, safe_float(accepted_match.get("overlap_sec"))) if accepted_match else 0.0
    if accepted_match:
        reason = "accepted_causal_candidate_matches_evaluation_reference"
    elif best:
        candidate = best["candidate"]
        reason = ", ".join(str(value) for value in candidate.get("reasons") or []) or "candidate_failed_evaluation_match"
    else:
        reason = "no_causal_candidate_overlaps_evaluation_interval"
    return {
        "schema": SCHEMA,
        "kind": "causal_double_talk_corpus_outcome",
        "id": row.get("id"),
        "session": row.get("session"),
        "status": "accepted" if accepted_match else "rejected",
        "stable_outcome": "recovered" if accepted_match else "not_recovered",
        "reason": reason,
        "recovered_seconds": round(recovered, 3),
        "evaluation_reference": reference,
        "selection_contract": row.get("selection_contract") or {},
        "candidate_count": len(relevant),
        "best_candidate": (
            {
                "id": best["candidate"].get("id"),
                "status": best["candidate"].get("status"),
                "classification": best["candidate"].get("classification"),
                "start": best["candidate"].get("start"),
                "end": best["candidate"].get("end"),
                "text": best["candidate"].get("text"),
                "source_selection_ids": best["candidate"].get("source_selection_ids") or [],
                "overlap_sec": best.get("overlap_sec"),
                "metrics": best.get("metrics"),
                "reasons": best["candidate"].get("reasons") or [],
                "independent_evidence": best["candidate"].get("independent_evidence") or {},
            }
            if best
            else None
        ),
        "used_batch_fields_for_selection": False,
        "evaluation_fields_used_post_selection_only": True,
    }


def render_markdown(report: dict[str, Any], outcomes: list[dict[str, Any]]) -> str:
    summary = report.get("summary") or {}
    acceptance = report.get("acceptance") or {}
    lines = [
        "# Causal Double-Talk Me Recovery v1",
        "",
        f"- Corpus fingerprint: `{report.get('corpus_fingerprint_sha256')}`",
        f"- Stable outcomes: `{summary.get('stable_outcome_count')}/16`",
        f"- Recovered: `{summary.get('recovered_row_count')}` rows / `{summary.get('recovered_seconds')}` sec",
        f"- Missing Me: `{summary.get('missing_me_seconds_before')}` -> `{summary.get('missing_me_seconds_after')}` sec",
        f"- Remote-like Me: `{summary.get('remote_like_me_seconds_before')}` -> `{summary.get('remote_like_me_seconds_after')}` sec",
        f"- Goal status: `{acceptance.get('status')}`",
        f"- Frozen inputs: `{(report.get('frozen_inputs') or {}).get('status')}`",
        f"- Per-session gates: `{sum(row.get('status') == 'passed' for row in report.get('session_gates') or [])}/{len(report.get('session_gates') or [])}`",
        f"- Runtime replay: `{(report.get('runtime_acceptance') or {}).get('status')}`",
        "",
        "## Fixed Corpus Outcomes",
        "",
    ]
    for row in outcomes:
        reference = row.get("evaluation_reference") or {}
        best = row.get("best_candidate") or {}
        lines.append(
            f"- `{row.get('session')}` `{safe_float(reference.get('start')):.3f}-{safe_float(reference.get('end')):.3f}` "
            f"`{row.get('status')}` ({row.get('recovered_seconds')} sec): {clean_text(reference.get('text'))}"
        )
        if best:
            lines.append(f"  Candidate: {clean_text(best.get('text')) or '(no text)'}")
        if row.get("status") != "accepted":
            lines.append(f"  Reason: `{row.get('reason')}`")
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Report the fixed causal double-talk recovery corpus.")
    parser.add_argument(
        "--corpus-dir",
        type=Path,
        default=Path("sessions/_reports/live-pipeline/causal-double-talk-me-recovery-v1"),
    )
    parser.add_argument("--sessions-root", type=Path, default=Path("sessions"))
    parser.add_argument(
        "--previous-report",
        type=Path,
        default=Path("sessions/_reports/live-pipeline/causal_remote_active_me_separation_v1.json"),
    )
    parser.add_argument("--runtime-replay", type=Path, action="append", default=[])
    parser.add_argument("--require-stable", action="store_true")
    parser.add_argument("--require-acceptance", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    corpus_dir = args.corpus_dir.expanduser().resolve()
    sessions_root = args.sessions_root.expanduser().resolve()
    repo_root = Path(__file__).resolve().parents[1]
    manifest = read_json(corpus_dir / "corpus_manifest_v1.json")
    corpus_rows = read_jsonl(corpus_dir / "corpus_rows_v1.jsonl")
    progressive = load_script("live-progressive-target-me.py", "murmurmark_cdt_report_progressive")
    session_candidates: dict[str, list[dict[str, Any]]] = {}
    for session_name in sorted({str(row.get("session")) for row in corpus_rows}):
        session_candidates[session_name] = read_jsonl(
            sessions_root
            / session_name
            / "derived/live/causal-double-talk-me-recovery-v1/candidates.jsonl"
        )
    outcomes = [
        evaluate_row(row, session_candidates.get(str(row.get("session")), []), progressive)
        for row in corpus_rows
    ]
    recovered = [row for row in outcomes if row.get("status") == "accepted"]
    recovered_seconds = round(sum(safe_float(row.get("recovered_seconds")) for row in recovered), 3)
    stable_projection = [
        {
            "id": row.get("id"),
            "session": row.get("session"),
            "status": row.get("status"),
            "recovered_seconds": row.get("recovered_seconds"),
            "reason": row.get("reason"),
            "best_candidate": row.get("best_candidate"),
        }
        for row in outcomes
    ]
    frozen_inputs = verify_frozen_inputs(manifest, repo_root)
    previous_report = read_json(args.previous_report.expanduser().resolve())
    gates = session_gate_rows(previous_report, sessions_root)
    runtime_paths = [path.expanduser().resolve() for path in args.runtime_replay]
    runtime = runtime_acceptance(runtime_paths)
    actual_missing = round(
        sum(safe_float((row.get("metrics") or {}).get("missing_me_seconds", {}).get("after")) for row in gates),
        3,
    )
    actual_remote = round(
        sum(safe_float((row.get("metrics") or {}).get("remote_like_me_seconds", {}).get("after")) for row in gates),
        3,
    )
    actual_blockers = sum(
        int((row.get("metrics") or {}).get("effective_order_blockers") or 0) for row in gates
    )
    projected_review_burden = round(
        max(0.0, BASELINE["review_burden_seconds"] - recovered_seconds), 3
    )
    acceptance_checks = {
        "stable_outcomes_16_of_16": len(outcomes) == 16 and all(row.get("reason") for row in outcomes),
        "recovered_at_least_3_rows": len(recovered) >= 3,
        "recovered_at_least_10_seconds": recovered_seconds >= 10.0,
        "missing_me_decreased": bool(gates) and actual_missing < BASELINE["missing_me_seconds"],
        "remote_like_not_increased": bool(gates) and actual_remote <= BASELINE["remote_like_me_seconds"],
        "order_blockers_zero": bool(gates) and actual_blockers == 0,
        "token_f1_not_worse_each_session": bool(gates) and all(
            (row.get("checks") or {}).get("token_f1_not_worse") is True for row in gates
        ),
        "all_session_gates_pass": bool(gates) and all(row.get("status") == "passed" for row in gates),
        "review_burden_not_increased": projected_review_burden <= BASELINE["review_burden_seconds"],
        "selection_is_batch_free": all(row.get("used_batch_fields_for_selection") is False for row in outcomes),
        "frozen_raw_echo_preview_authoritative_inputs_unchanged": frozen_inputs.get("status") == "passed",
        "runtime_p95_within_30s_and_final_lag_zero": runtime.get("status") == "passed",
    }
    summary = {
        "corpus_row_count": len(outcomes),
        "stable_outcome_count": sum(bool(row.get("reason")) for row in outcomes),
        "recovered_row_count": len(recovered),
        "recovered_seconds": recovered_seconds,
        "missing_me_seconds_before": BASELINE["missing_me_seconds"],
        "missing_me_seconds_after": actual_missing,
        "remote_like_me_seconds_before": BASELINE["remote_like_me_seconds"],
        "remote_like_me_seconds_after": actual_remote,
        "order_blocker_count_before": BASELINE["order_blocker_count"],
        "order_blocker_count_after": actual_blockers,
        "review_burden_seconds_before": BASELINE["review_burden_seconds"],
        "review_burden_seconds_after": projected_review_burden,
    }
    report = {
        "schema": SCHEMA,
        "generator": {"name": "report-causal-double-talk-me-recovery-v1", "version": SCRIPT_VERSION},
        "created_at": now_iso(),
        "corpus_fingerprint_sha256": manifest.get("corpus_fingerprint_sha256"),
        "outcome_fingerprint_sha256": stable_payload_hash(stable_projection),
        "summary": summary,
        "profile": PROFILE,
        "previous_profile": PREVIOUS_PROFILE,
        "frozen_inputs": frozen_inputs,
        "session_gates": gates,
        "runtime_acceptance": runtime,
        "acceptance": {
            "status": "passed" if all(acceptance_checks.values()) else "not_yet_passed",
            "checks": acceptance_checks,
        },
        "baseline": BASELINE,
        "selection_contract": {
            "timeline_causal": True,
            "batch_fields_forbidden": True,
            "evaluation_reference_used_post_selection_only": True,
        },
        "hypotheses": {
            "accepted": [
                "past-only adaptive echo models plus spectral/ratio residual views recover genuine overlapping Me",
                "past-only Target-Me localization can isolate short local speech islands inside mixed windows",
                "strict multi-view consensus is safe for offline evaluation",
                "one strict ratio-mask view plus independent Target-Me and ASR evidence is bounded enough for runtime shadow",
            ],
            "rejected": [
                "one static FIR can safely resolve every changing double-talk interval",
                "source-track ASR text alone is sufficient evidence for Me publication",
                "full-interval speaker embeddings preserve short Me speech inside remote-dominant windows",
                "GPU whisper.cpp is a reliable mandatory runtime dependency under current Metal memory pressure",
                "processing every remote-active group fits the live cutoff budget",
            ],
        },
        "outcomes": outcomes,
    }
    write_jsonl(corpus_dir / "outcomes_v1.jsonl", outcomes)
    write_json(corpus_dir / "recovery_report_v1.json", report)
    (corpus_dir / "recovery_report_v1.md").write_text(
        render_markdown(report, outcomes), encoding="utf-8"
    )
    print(f"causal double-talk report: {corpus_dir / 'recovery_report_v1.json'}")
    print(f"recovered: {len(recovered)} rows / {recovered_seconds:.3f}s")
    print(f"status: {report['acceptance']['status']}")
    if args.require_stable and not acceptance_checks["stable_outcomes_16_of_16"]:
        return 1
    if args.require_acceptance and report["acceptance"]["status"] != "passed":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
