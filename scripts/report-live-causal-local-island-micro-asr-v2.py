#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = "murmurmark.live_causal_local_island_micro_asr_v2_report/v1"
ROW_SCHEMA = "murmurmark.live_causal_local_island_micro_asr_v2_outcome/v1"
SCRIPT_VERSION = "1.0.0"
BASELINE_PROFILE = (
    "online_live_me_remote_overlap_filter_live_boundary_split_retime_causal_remote_energy_v1"
)
CANDIDATE_PROFILE = (
    "online_live_me_remote_overlap_filter_live_boundary_split_retime_causal_remote_energy_"
    "local_island_micro_asr_v2"
)
EXPECTED_SESSION_COUNT = 7
EXPECTED_ROW_COUNT = 40
EXPECTED_ROW_SECONDS = 210.41
EXPECTED_BASELINE_MISSING_ME_SECONDS = 2166.56
EXPECTED_BASELINE_REMOTE_LIKE_ME_SECONDS = 108.42
EXPECTED_REVIEW_BURDEN_SECONDS = 490.38
MATERIAL_SESSION_IMPROVEMENT_SECONDS = 10.0
EPSILON = 1.0e-6


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate Causal Local-Island Micro-ASR v2 against the fixed 40-row unresolved scope."
    )
    parser.add_argument("--sessions-root", type=Path, default=Path("sessions"))
    parser.add_argument(
        "--hardening-report",
        type=Path,
        default=Path(
            "sessions/_reports/live-pipeline/live_local_recall_remote_leakage_hardening_v1.json"
        ),
    )
    parser.add_argument(
        "--hardening-rows",
        type=Path,
        default=Path(
            "sessions/_reports/live-pipeline/live_local_recall_remote_leakage_hardening_v1.jsonl"
        ),
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("sessions/_reports/live-pipeline"),
    )
    parser.add_argument(
        "--corpus-report",
        type=Path,
        default=Path(
            "sessions/_reports/live-pipeline/causal-local-island-micro-asr-v2-corpus/"
            "live_corpus_gates_report.json"
        ),
    )
    parser.add_argument("--baseline-profile", default=BASELINE_PROFILE)
    parser.add_argument("--candidate-profile", default=CANDIDATE_PROFILE)
    return parser.parse_args()


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
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
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_manifest(path: Path, session: Path) -> dict[str, Any]:
    return {
        "path": str(path.relative_to(session)),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
    }


def current_authoritative_manifest(session: Path) -> list[dict[str, Any]]:
    paths = sorted((session / "audio/mic").glob("*.caf"))
    paths += sorted((session / "audio/remote").glob("*.caf"))
    resolved = session / "derived/transcript-simple/whisper-cpp/resolved"
    paths += sorted(resolved.glob("clean_dialogue*.json"))
    return [file_manifest(path, session) for path in paths if path.is_file()]


def interval_overlap(start: float, end: float, other_start: float, other_end: float) -> float:
    return max(0.0, min(end, other_end) - max(start, other_start))


def profile_map(comparison: dict[str, Any]) -> dict[str, Any]:
    shadow = comparison.get("shadow_profiles") if isinstance(comparison.get("shadow_profiles"), dict) else {}
    profiles = shadow.get("target_me") if isinstance(shadow.get("target_me"), dict) else {}
    return profiles


def gate_evidence(profile: dict[str, Any], name: str) -> dict[str, Any]:
    parity = profile.get("parity_gates") if isinstance(profile.get("parity_gates"), dict) else {}
    for row in parity.get("gates") or []:
        if isinstance(row, dict) and row.get("name") == name:
            evidence = row.get("evidence")
            return evidence if isinstance(evidence, dict) else {}
    return {}


def resolve_draft(session: Path, profile: dict[str, Any]) -> dict[str, Any]:
    outputs = profile.get("outputs") if isinstance(profile.get("outputs"), dict) else {}
    value = outputs.get("draft_json")
    if not value:
        return {}
    path = Path(str(value))
    if not path.is_absolute():
        path = session / path
    return read_json(path)


def candidate_contract(row: dict[str, Any]) -> dict[str, Any]:
    recording_time = (
        row.get("recording_time_evidence")
        if isinstance(row.get("recording_time_evidence"), dict)
        else {}
    )
    remote_audio = (
        row.get("remote_audio_guard")
        if isinstance(row.get("remote_audio_guard"), dict)
        else {}
    )
    strict_remote = (
        row.get("strict_remote_free_guard")
        if isinstance(row.get("strict_remote_free_guard"), dict)
        else {}
    )
    remote_asr = (
        row.get("remote_asr_guard")
        if isinstance(row.get("remote_asr_guard"), dict)
        else {}
    )
    checks = {
        "role_is_me": row.get("role") == "Me",
        "candidate_flag": row.get("causal_local_island_micro_asr_v2_shadow") is True,
        "timeline_causal": row.get("timeline_causal") is True,
        "selection_does_not_use_batch": row.get("used_batch_fields_for_selection") is False,
        "recording_time_evidence": recording_time.get("status") == "passed",
        "remote_audio_guard": remote_audio.get("status") == "passed",
        "strict_remote_free_guard": strict_remote.get("status") == "passed",
        "remote_asr_guard": remote_asr.get("status") == "passed",
        "text_present": bool(str(row.get("text") or "").strip()),
    }
    return {"passed": all(checks.values()), "checks": checks}


def overlap_rows(
    start: float,
    end: float,
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        row_start = safe_float(row.get("start"))
        row_end = safe_float(row.get("end"), row_start)
        overlap = interval_overlap(start, end, row_start, row_end)
        if overlap > EPSILON:
            result.append({**row, "evaluation_overlap_sec": round(overlap, 3)})
    return sorted(result, key=lambda row: (-safe_float(row.get("evaluation_overlap_sec")), safe_float(row.get("start"))))


def compact_selection(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "chunk_index": row.get("chunk_index"),
        "start": row.get("start"),
        "end": row.get("end"),
        "evaluation_overlap_sec": row.get("evaluation_overlap_sec"),
        "text": row.get("text"),
        "status": row.get("status"),
        "reasons": row.get("reasons") or [],
        "checks": row.get("checks") or {},
    }


def compact_candidate(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "chunk_index": row.get("chunk_index"),
        "start": row.get("start"),
        "end": row.get("end"),
        "evaluation_overlap_sec": row.get("evaluation_overlap_sec"),
        "text": row.get("text"),
        "status": row.get("status"),
        "reasons": row.get("reasons") or [],
        "score": row.get("score"),
        "remote_similarity": row.get("remote_similarity"),
        "strict_remote_free_guard": row.get("strict_remote_free_guard") or {},
    }


def outcome_for_row(
    source: dict[str, Any],
    *,
    candidate_turns: list[dict[str, Any]],
    selection_rows: list[dict[str, Any]],
    micro_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    reference = source.get("evaluation_reference") if isinstance(source.get("evaluation_reference"), dict) else {}
    start = safe_float(reference.get("start"))
    end = safe_float(reference.get("end"), start)
    accepted = overlap_rows(start, end, candidate_turns)
    selections = overlap_rows(start, end, selection_rows)
    micro = overlap_rows(start, end, micro_rows)
    if accepted:
        outcome = "accepted"
        reason = "a causal remote-free micro-ASR turn safely overlaps the evaluation reference"
    elif selections or micro:
        outcome = "rejected"
        reason_counts: Counter[str] = Counter()
        for row in selections + micro:
            for value in row.get("reasons") or []:
                reason_counts[str(value)] += 1
        reason = (
            "safe publication contract rejected available live evidence: "
            + ", ".join(value for value, _ in reason_counts.most_common(4))
            if reason_counts
            else "available live evidence was not materialized into the final v2 shadow"
        )
    else:
        outcome = "unresolved"
        reason = "no causal speaker-supported remote-free island overlaps this evaluation reference"
    accepted_overlap = round(sum(safe_float(row.get("evaluation_overlap_sec")) for row in accepted), 3)
    return {
        "schema": ROW_SCHEMA,
        "id": source.get("id"),
        "session": source.get("session"),
        "status": "stable",
        "outcome": outcome,
        "reason": reason,
        "publication": {
            "status": "shadow_candidate" if accepted else "blocked",
            "authoritative": False,
            "uses_batch_for_selection": False,
            "batch_fields_use": "evaluation_only",
        },
        "evaluation_reference": {**reference, "use": "evaluation_only"},
        "machine_evidence": {
            "accepted_candidate_overlap_sec": accepted_overlap,
            "accepted_candidates": [
                {
                    "id": row.get("id"),
                    "start": row.get("start"),
                    "end": row.get("end"),
                    "evaluation_overlap_sec": row.get("evaluation_overlap_sec"),
                    "text": row.get("text"),
                    "contract": candidate_contract(row),
                }
                for row in accepted
            ],
            "selection_evidence": [compact_selection(row) for row in selections[:20]],
            "micro_asr_evidence": [compact_candidate(row) for row in micro[:20]],
        },
    }


def session_report(
    session: Path,
    *,
    baseline_policy: str,
    candidate_policy: str,
    expected_manifest: list[dict[str, Any]],
    source_rows: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    comparison_path = session / "derived/live/live_batch_comparison.json"
    comparison = read_json(comparison_path)
    profiles = profile_map(comparison)
    baseline = profiles.get(baseline_policy) if isinstance(profiles.get(baseline_policy), dict) else {}
    candidate = profiles.get(candidate_policy) if isinstance(profiles.get(candidate_policy), dict) else {}
    baseline_metrics = baseline.get("metrics") if isinstance(baseline.get("metrics"), dict) else {}
    candidate_metrics = candidate.get("metrics") if isinstance(candidate.get("metrics"), dict) else {}
    baseline_review = gate_evidence(baseline, "review_burden")
    candidate_review = gate_evidence(candidate, "review_burden")
    candidate_order = (
        candidate.get("order_role_reconciliation")
        if isinstance(candidate.get("order_role_reconciliation"), dict)
        else {}
    )
    draft = resolve_draft(session, candidate)
    candidate_turns = [
        row
        for row in draft.get("turns") or []
        if isinstance(row, dict) and row.get("causal_local_island_micro_asr_v2_shadow") is True
    ]
    selection_rows = read_jsonl(
        session / "derived/live/causal-local-island-micro-asr-v2/selection.jsonl"
    )
    micro_rows = read_jsonl(
        session / "derived/live/causal-local-island-micro-asr-v2/candidates.jsonl"
    )
    outcomes = [
        outcome_for_row(
            row,
            candidate_turns=candidate_turns,
            selection_rows=selection_rows,
            micro_rows=micro_rows,
        )
        for row in source_rows
    ]
    current_manifest = current_authoritative_manifest(session)
    expected_by_path = {str(row.get("path")): row for row in expected_manifest}
    current_by_path = {str(row.get("path")): row for row in current_manifest}
    manifest_paths_match = set(current_by_path) == set(expected_by_path)
    manifest_hashes_match = manifest_paths_match and all(
        current_by_path[path].get("sha256") == expected_by_path[path].get("sha256")
        and current_by_path[path].get("bytes") == expected_by_path[path].get("bytes")
        for path in expected_by_path
    )
    baseline_missing = safe_float(baseline_metrics.get("live_missing_me_seconds"))
    candidate_missing = safe_float(candidate_metrics.get("live_missing_me_seconds"))
    baseline_remote = safe_float(baseline_metrics.get("live_suspected_remote_leak_in_me_seconds"))
    candidate_remote = safe_float(candidate_metrics.get("live_suspected_remote_leak_in_me_seconds"))
    baseline_f1 = safe_float(baseline_metrics.get("live_batch_token_f1"))
    candidate_f1 = safe_float(candidate_metrics.get("live_batch_token_f1"))
    contracts = [candidate_contract(row) for row in candidate_turns]
    checks = {
        "profiles_present": bool(baseline and candidate),
        "missing_me_not_worse": candidate_missing <= baseline_missing + EPSILON,
        "remote_like_me_not_worse": candidate_remote <= baseline_remote + EPSILON,
        "effective_order_blockers_zero": safe_int(candidate_order.get("blocking_count")) == 0,
        "token_f1_not_worse": candidate_f1 + EPSILON >= baseline_f1,
        "review_burden_not_worse": safe_float(candidate_review.get("review_burden_sec"))
        <= safe_float(baseline_review.get("review_burden_sec")) + EPSILON,
        "candidate_contracts_pass": all(row.get("passed") is True for row in contracts),
        "batch_selection_not_used": all(
            row.get("used_batch_fields_for_selection") is False for row in micro_rows
        ),
        "batch_authoritative": candidate.get("batch_authoritative") is True,
        "promotion_blocked": candidate.get("promotion_allowed") is False,
        "authoritative_inputs_unchanged": manifest_hashes_match,
    }
    baseline_shadow = (
        session
        / "derived/live/target-me-shadow"
        / baseline_policy
        / "draft.json"
    )
    protected_shadow = file_manifest(baseline_shadow, session) if baseline_shadow.exists() else None
    return (
        {
            "session": session.name,
            "status": "passed" if all(checks.values()) else "failed",
            "comparison": str(comparison_path.relative_to(session)),
            "baseline_profile": baseline_policy,
            "candidate_profile": candidate_policy,
            "checks": checks,
            "metrics": {
                "missing_me_seconds": {
                    "before": baseline_missing,
                    "after": candidate_missing,
                    "delta": round(candidate_missing - baseline_missing, 3),
                },
                "remote_like_me_seconds": {
                    "before": baseline_remote,
                    "after": candidate_remote,
                    "delta": round(candidate_remote - baseline_remote, 3),
                },
                "live_batch_token_f1": {
                    "before": baseline_f1,
                    "after": candidate_f1,
                    "delta": round(candidate_f1 - baseline_f1, 6),
                },
                "effective_order_blockers": safe_int(candidate_order.get("blocking_count")),
                "review_burden_sec": {
                    "before": safe_float(baseline_review.get("review_burden_sec")),
                    "after": safe_float(candidate_review.get("review_burden_sec")),
                    "delta": round(
                        safe_float(candidate_review.get("review_burden_sec"))
                        - safe_float(baseline_review.get("review_burden_sec")),
                        3,
                    ),
                },
                "candidate_added_turn_count": len(candidate_turns),
                "candidate_added_turn_seconds": round(
                    sum(
                        max(0.0, safe_float(row.get("end")) - safe_float(row.get("start")))
                        for row in candidate_turns
                    ),
                    3,
                ),
            },
            "outcomes": {
                "row_count": len(outcomes),
                "by_outcome": dict(sorted(Counter(str(row.get("outcome")) for row in outcomes).items())),
            },
            "input_sha256_manifest": current_manifest,
            "expected_input_sha256_manifest": expected_manifest,
            "protected_previous_shadow": protected_shadow,
        },
        outcomes,
    )


def corpus_agreement(
    corpus: dict[str, Any],
    *,
    candidate_policy: str,
    session_reports: list[dict[str, Any]],
    candidate_missing: float,
    candidate_remote: float,
    effective_order_blockers: int,
) -> dict[str, Any]:
    summary = corpus.get("summary") if isinstance(corpus.get("summary"), dict) else {}
    diagnostics = (
        corpus.get("live_target_me_shadow_profile_diagnostics")
        if isinstance(corpus.get("live_target_me_shadow_profile_diagnostics"), dict)
        else {}
    )
    real = diagnostics.get("real") if isinstance(diagnostics.get("real"), dict) else {}
    best = (
        real.get("best_live_implementable_profile")
        if isinstance(real.get("best_live_implementable_profile"), dict)
        else {}
    )
    prefix = f"real_live_target_me_shadow_profile_{candidate_policy}"
    session_added_count = sum(
        safe_int((row.get("metrics") or {}).get("candidate_added_turn_count"))
        for row in session_reports
    )
    session_added_seconds = round(
        sum(
            safe_float((row.get("metrics") or {}).get("candidate_added_turn_seconds"))
            for row in session_reports
        ),
        3,
    )
    observed = {
        "evaluated_session_count": safe_int(summary.get(f"{prefix}_evaluated_session_count")),
        "selected_policy": best.get("policy"),
        "missing_me_seconds": safe_float(summary.get(f"{prefix}_live_missing_me_seconds")),
        "remote_like_me_seconds": safe_float(
            summary.get(f"{prefix}_live_suspected_remote_leak_in_me_seconds")
        ),
        "effective_order_blockers": safe_int(
            summary.get(
                f"{prefix}_live_effective_blocking_contentful_role_constrained_order_mismatch_count"
            )
        ),
        "candidate_added_turn_count": safe_int(
            summary.get(f"{prefix}_causal_local_island_micro_asr_v2_added_turn_count")
        ),
        "candidate_added_turn_seconds": safe_float(
            summary.get(f"{prefix}_causal_local_island_micro_asr_v2_added_turn_seconds")
        ),
    }
    expected = {
        "evaluated_session_count": len(session_reports),
        "selected_policy": candidate_policy,
        "missing_me_seconds": candidate_missing,
        "remote_like_me_seconds": candidate_remote,
        "effective_order_blockers": effective_order_blockers,
        "candidate_added_turn_count": session_added_count,
        "candidate_added_turn_seconds": session_added_seconds,
    }
    checks = {
        "report_present": bool(corpus),
        "session_count": observed["evaluated_session_count"] == expected["evaluated_session_count"],
        "selected_policy": observed["selected_policy"] == expected["selected_policy"],
        "missing_me_seconds": abs(observed["missing_me_seconds"] - expected["missing_me_seconds"])
        <= 0.02,
        "remote_like_me_seconds": abs(
            observed["remote_like_me_seconds"] - expected["remote_like_me_seconds"]
        )
        <= 0.02,
        "effective_order_blockers": observed["effective_order_blockers"]
        == expected["effective_order_blockers"],
        "candidate_added_turn_count": observed["candidate_added_turn_count"]
        == expected["candidate_added_turn_count"],
        "candidate_added_turn_seconds": abs(
            observed["candidate_added_turn_seconds"] - expected["candidate_added_turn_seconds"]
        )
        <= 0.02,
    }
    return {
        "status": "passed" if all(checks.values()) else "failed",
        "checks": checks,
        "expected": expected,
        "observed": observed,
    }


def render_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# Causal Local-Island Micro-ASR v2",
        "",
        f"Status: `{report.get('status')}`",
        f"Baseline: `{report.get('baseline_profile')}`",
        f"Candidate: `{report.get('selected_shadow_profile')}`",
        "Batch authoritative: `true`",
        "Promotion allowed: `false`",
        "",
        "## Corpus result",
        "",
        f"- sessions: `{summary.get('session_count')}`",
        f"- outcomes: `{summary.get('outcome_row_count')}` / `{summary.get('outcome_seconds')}s`",
        f"- by outcome: `{summary.get('by_outcome')}`",
        f"- missing Me: `{summary.get('baseline_missing_me_seconds')}s -> "
        f"{summary.get('candidate_missing_me_seconds')}s`",
        f"- recovered Me: `{summary.get('recovered_me_seconds')}s`",
        f"- remote-like Me: `{summary.get('baseline_remote_like_me_seconds')}s -> "
        f"{summary.get('candidate_remote_like_me_seconds')}s`",
        f"- effective order blockers: `{summary.get('effective_order_blocker_count')}`",
        f"- review burden: `{summary.get('review_burden_seconds')}s`",
        f"- per-session gates: `{summary.get('passing_session_count')}/{summary.get('session_count')}`",
        f"- standard corpus agreement: `{(report.get('corpus_agreement') or {}).get('status')}`",
        "",
        "## Sessions",
        "",
    ]
    for row in report.get("sessions") or []:
        metrics = row.get("metrics") or {}
        missing = metrics.get("missing_me_seconds") or {}
        lines.append(
            f"- `{row.get('session')}`: `{row.get('status')}`, missing Me "
            f"`{missing.get('before')}s -> {missing.get('after')}s`, "
            f"candidates `{metrics.get('candidate_added_turn_count')}`, "
            f"outcomes `{(row.get('outcomes') or {}).get('by_outcome')}`"
        )
    lines.extend(
        [
            "",
            "## Contract",
            "",
            "Candidate selection uses committed-PCM timing, past-only local-speaker evidence, "
            "strictly quiet remote audio and a guarded remote ASR timeline. Batch fields are used "
            "only after materialization to evaluate the fixed 40-row scope.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    hardening = read_json(args.hardening_report)
    source_rows = [
        row
        for row in read_jsonl(args.hardening_rows)
        if row.get("disposition") == "unresolved"
    ]
    sessions_from_report = hardening.get("sessions") if isinstance(hardening.get("sessions"), list) else []
    session_ids = [str(row.get("session")) for row in sessions_from_report if row.get("session")]
    expected_manifest_by_session = {
        str(row.get("session")): row.get("immutable_inputs") or []
        for row in sessions_from_report
        if row.get("session")
    }
    source_by_session: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in source_rows:
        source_by_session[str(row.get("session") or "")].append(row)
    session_reports: list[dict[str, Any]] = []
    outcomes: list[dict[str, Any]] = []
    for session_id in session_ids:
        session = args.sessions_root / session_id
        report, rows = session_report(
            session,
            baseline_policy=args.baseline_profile,
            candidate_policy=args.candidate_profile,
            expected_manifest=expected_manifest_by_session.get(session_id, []),
            source_rows=source_by_session.get(session_id, []),
        )
        session_reports.append(report)
        outcomes.extend(rows)
        write_jsonl(
            session / "derived/live/causal-local-island-micro-asr-v2/outcomes.jsonl",
            rows,
        )
    baseline_missing = round(
        sum(safe_float((row.get("metrics") or {}).get("missing_me_seconds", {}).get("before")) for row in session_reports),
        3,
    )
    candidate_missing = round(
        sum(safe_float((row.get("metrics") or {}).get("missing_me_seconds", {}).get("after")) for row in session_reports),
        3,
    )
    baseline_remote = round(
        sum(safe_float((row.get("metrics") or {}).get("remote_like_me_seconds", {}).get("before")) for row in session_reports),
        3,
    )
    candidate_remote = round(
        sum(safe_float((row.get("metrics") or {}).get("remote_like_me_seconds", {}).get("after")) for row in session_reports),
        3,
    )
    review_burden = round(
        sum(safe_float((row.get("metrics") or {}).get("review_burden_sec", {}).get("after")) for row in session_reports),
        3,
    )
    improvements = [
        safe_float((row.get("metrics") or {}).get("missing_me_seconds", {}).get("before"))
        - safe_float((row.get("metrics") or {}).get("missing_me_seconds", {}).get("after"))
        for row in session_reports
    ]
    outcome_seconds = round(
        sum(safe_float((row.get("evaluation_reference") or {}).get("duration_sec")) for row in outcomes),
        3,
    )
    summary = {
        "session_count": len(session_reports),
        "passing_session_count": sum(1 for row in session_reports if row.get("status") == "passed"),
        "outcome_row_count": len(outcomes),
        "stable_outcome_count": sum(1 for row in outcomes if row.get("status") == "stable"),
        "outcome_seconds": outcome_seconds,
        "by_outcome": dict(sorted(Counter(str(row.get("outcome")) for row in outcomes).items())),
        "baseline_missing_me_seconds": baseline_missing,
        "candidate_missing_me_seconds": candidate_missing,
        "recovered_me_seconds": round(baseline_missing - candidate_missing, 3),
        "max_session_improvement_seconds": round(max(improvements, default=0.0), 3),
        "material_session_improvement_threshold_seconds": MATERIAL_SESSION_IMPROVEMENT_SECONDS,
        "material_session_improvement_passed": max(improvements, default=0.0)
        >= MATERIAL_SESSION_IMPROVEMENT_SECONDS,
        "baseline_remote_like_me_seconds": baseline_remote,
        "candidate_remote_like_me_seconds": candidate_remote,
        "effective_order_blocker_count": sum(
            safe_int((row.get("metrics") or {}).get("effective_order_blockers"))
            for row in session_reports
        ),
        "review_burden_seconds": review_burden,
    }
    effective_order_blockers = summary["effective_order_blocker_count"]
    agreement = corpus_agreement(
        read_json(args.corpus_report),
        candidate_policy=args.candidate_profile,
        session_reports=session_reports,
        candidate_missing=candidate_missing,
        candidate_remote=candidate_remote,
        effective_order_blockers=effective_order_blockers,
    )
    completion_checks = {
        "expected_session_scope": len(session_reports) == EXPECTED_SESSION_COUNT,
        "expected_unresolved_scope": len(outcomes) == EXPECTED_ROW_COUNT
        and abs(outcome_seconds - EXPECTED_ROW_SECONDS) <= 0.02,
        "all_outcomes_stable": all(
            row.get("status") == "stable" and row.get("outcome") in {"accepted", "rejected", "unresolved"}
            for row in outcomes
        ),
        "baseline_matches_hardening_v1": abs(baseline_missing - EXPECTED_BASELINE_MISSING_ME_SECONDS) <= 0.02
        and abs(baseline_remote - EXPECTED_BASELINE_REMOTE_LIKE_ME_SECONDS) <= 0.02
        and abs(review_burden - EXPECTED_REVIEW_BURDEN_SECONDS) <= 0.02,
        "material_improvement": summary["material_session_improvement_passed"],
        "all_per_session_gates_pass": all(row.get("status") == "passed" for row in session_reports),
        "remote_like_me_not_worse": candidate_remote <= EXPECTED_BASELINE_REMOTE_LIKE_ME_SECONDS + EPSILON,
        "effective_order_blockers_zero": summary["effective_order_blocker_count"] == 0,
        "standard_corpus_report_agrees": agreement.get("status") == "passed",
        "promotion_blocked": True,
    }
    status = "passed_shadow_only" if all(completion_checks.values()) else "failed"
    report = {
        "schema": SCHEMA,
        "generator": {
            "name": "report-live-causal-local-island-micro-asr-v2",
            "version": SCRIPT_VERSION,
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "baseline_profile": args.baseline_profile,
        "selected_shadow_profile": args.candidate_profile,
        "batch_authoritative": True,
        "promotion_allowed": False,
        "promotion_reason": "live parity remains blocked outside this bounded local-recall slice",
        "selection_contract": {
            "live_only": True,
            "batch_fields_used_for_selection": False,
            "batch_fields_use": "evaluation_only",
            "required_evidence": [
                "committed_pcm_timing",
                "past_only_local_speaker_evidence",
                "strict_remote_quiet_audio_guard",
                "guarded_remote_asr_timeline",
                "bounded_micro_asr",
            ],
        },
        "completion_checks": completion_checks,
        "corpus_agreement": agreement,
        "summary": summary,
        "sessions": session_reports,
        "outputs": {
            "report": "live_causal_local_island_micro_asr_v2.json",
            "rows": "live_causal_local_island_micro_asr_v2.jsonl",
            "markdown": "live_causal_local_island_micro_asr_v2.md",
            "standard_corpus_report": str(args.corpus_report),
        },
    }
    json_path = args.out_dir / "live_causal_local_island_micro_asr_v2.json"
    jsonl_path = args.out_dir / "live_causal_local_island_micro_asr_v2.jsonl"
    markdown_path = args.out_dir / "live_causal_local_island_micro_asr_v2.md"
    write_json(json_path, report)
    write_jsonl(jsonl_path, outcomes)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    print(f"status: {status}")
    print(f"sessions: {len(session_reports)}")
    print(f"outcomes: {len(outcomes)}")
    print(f"recovered_me_seconds: {summary['recovered_me_seconds']}")
    print(f"remote_like_me_seconds: {candidate_remote}")
    print(f"effective_order_blockers: {summary['effective_order_blocker_count']}")
    print(f"report: {json_path}")
    return 0 if status == "passed_shadow_only" else 1


if __name__ == "__main__":
    raise SystemExit(main())
