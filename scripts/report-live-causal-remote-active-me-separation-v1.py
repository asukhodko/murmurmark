#!/usr/bin/env python3
"""Evaluate the explicit-only causal remote-active Me separation shadow."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


SCHEMA = "murmurmark.live_causal_remote_active_me_separation_v1_report/v1"
ROW_SCHEMA = "murmurmark.live_causal_remote_active_me_separation_v1_outcome/v1"
SCRIPT_VERSION = "1.0.0"
BASELINE_PROFILE = (
    "online_live_me_remote_overlap_filter_live_boundary_split_retime_causal_remote_energy_"
    "local_island_micro_asr_v2"
)
CANDIDATE_PROFILE = BASELINE_PROFILE + "_causal_remote_active_me_separation_v1"
EXPERIMENT_RELATIVE = Path("derived/live/causal-remote-active-me-separation-v1")
EXPECTED_SESSION_COUNT = 7
EXPECTED_PRIMARY_ROWS = 19
EXPECTED_PRIMARY_SECONDS = 88.39
EXPECTED_CROSS_ROWS = 16
EXPECTED_CROSS_SECONDS = 65.07
EXPECTED_BASELINE_MISSING_ME_SECONDS = 1910.79
EXPECTED_BASELINE_REMOTE_LIKE_ME_SECONDS = 108.42
EXPECTED_BASELINE_REVIEW_BURDEN_SECONDS = 490.38
MIN_OUTCOME_OVERLAP_SECONDS = 0.20
MATERIAL_SESSION_IMPROVEMENT_SECONDS = 10.0
EPSILON = 1.0e-6


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate causal remote-active Me separation over the fixed seven-session corpus."
    )
    parser.add_argument("--sessions-root", type=Path, default=Path("sessions"))
    parser.add_argument(
        "--baseline-report",
        type=Path,
        default=Path(
            "sessions/_reports/live-pipeline/live_causal_local_island_micro_asr_v2.json"
        ),
    )
    parser.add_argument(
        "--baseline-rows",
        type=Path,
        default=Path(
            "sessions/_reports/live-pipeline/live_causal_local_island_micro_asr_v2.jsonl"
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
        "--corpus-report",
        type=Path,
        default=Path(
            "sessions/_reports/live-pipeline/causal-remote-active-me-separation-v1-corpus/"
            "live_corpus_gates_report.json"
        ),
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("sessions/_reports/live-pipeline"),
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


def manifests(paths: list[Path], session: Path) -> list[dict[str, Any]]:
    unique = sorted({path for path in paths if path.is_file()})
    return [file_manifest(path, session) for path in unique]


def authoritative_manifest(session: Path) -> list[dict[str, Any]]:
    paths = sorted((session / "audio/mic").glob("*.caf"))
    paths += sorted((session / "audio/remote").glob("*.caf"))
    resolved = session / "derived/transcript-simple/whisper-cpp/resolved"
    paths += sorted(resolved.glob("clean_dialogue*.json"))
    return manifests(paths, session)


def experiment_input_manifest(session: Path, baseline_profile: str) -> list[dict[str, Any]]:
    paths = [
        session / "derived/live/causal-target-me/evaluations.jsonl",
        session / "derived/live/causal-target-me/enrollment.jsonl",
        session / "derived/live/causal-local-island-micro-asr-v2/selection.jsonl",
        session / "derived/live/causal-local-island-micro-asr-v2/candidates.jsonl",
        session / "derived/live/target-me-shadow" / baseline_profile / "draft.json",
    ]
    paths += sorted((session / "derived/experiments/live-shadow-v1/audio/mic").glob("*.caf"))
    paths += sorted((session / "derived/experiments/live-shadow-v1/audio/remote").glob("*.caf"))
    return manifests(paths, session)


def interval_overlap(start: float, end: float, other_start: float, other_end: float) -> float:
    return max(0.0, min(end, other_end) - max(start, other_start))


def profile_map(comparison: dict[str, Any]) -> dict[str, Any]:
    shadow = comparison.get("shadow_profiles") if isinstance(comparison.get("shadow_profiles"), dict) else {}
    value = shadow.get("target_me") if isinstance(shadow.get("target_me"), dict) else {}
    return value


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
    return read_json(path if path.is_absolute() else session / path)


def candidate_contract(row: dict[str, Any]) -> dict[str, Any]:
    recording = row.get("recording_time_evidence") if isinstance(row.get("recording_time_evidence"), dict) else {}
    residual = row.get("residual_audio_guard") if isinstance(row.get("residual_audio_guard"), dict) else {}
    training = row.get("past_training_evidence") if isinstance(row.get("past_training_evidence"), dict) else {}
    remote_active = row.get("remote_active_guard") if isinstance(row.get("remote_active_guard"), dict) else {}
    remote_text = row.get("remote_text_guard") if isinstance(row.get("remote_text_guard"), dict) else {}
    target_me = row.get("target_me_evidence") if isinstance(row.get("target_me_evidence"), dict) else {}
    checks = {
        "accepted": row.get("status") == "accepted",
        "text_present": bool(str(row.get("text") or "").strip()),
        "timeline_causal": row.get("timeline_causal") is True,
        "selection_does_not_use_batch": row.get("used_batch_fields_for_selection") is False,
        "selection_mode": row.get("selection_mode")
        == "recording_time_causal_remote_active_separation_v1",
        "recording_time_evidence": recording.get("status") == "passed",
        "residual_audio_guard": residual.get("status") == "passed",
        "past_training_evidence": training.get("status") == "passed"
        and training.get("past_only") is True,
        "remote_active_guard": remote_active.get("status") == "passed",
        "remote_text_guard": remote_text.get("status") == "passed",
        "target_me_evidence": target_me.get("status") == "passed",
        "remote_forbidden_tokens_clear": not (row.get("remote_forbidden_matches") or []),
        "publication_blocked": row.get("publication_allowed") is False,
        "promotion_blocked": row.get("promotion_allowed") is False,
        "batch_authoritative": row.get("batch_authoritative") is True,
    }
    return {"passed": all(checks.values()), "checks": checks}


def draft_contract(row: dict[str, Any]) -> dict[str, Any]:
    checks = {
        "role_is_me": row.get("role") == "Me",
        "candidate_flag": row.get("causal_remote_active_me_separation_v1_shadow") is True,
        "timeline_causal": row.get("timeline_causal") is True,
        "selection_does_not_use_batch": row.get("used_batch_fields_for_selection") is False,
        "text_present": bool(str(row.get("text") or "").strip()),
    }
    return {"passed": all(checks.values()), "checks": checks}


def rows_overlapping(
    start: float,
    end: float,
    rows: list[dict[str, Any]],
    *,
    minimum: float = 0.0,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        row_start = safe_float(row.get("start"))
        row_end = safe_float(row.get("end"), row_start)
        overlap = interval_overlap(start, end, row_start, row_end)
        if overlap > max(EPSILON, minimum - EPSILON):
            result.append({**row, "evaluation_overlap_sec": round(overlap, 3)})
    return sorted(result, key=lambda row: (-safe_float(row.get("evaluation_overlap_sec")), safe_float(row.get("start"))))


def compact_evidence(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "kind": row.get("kind"),
        "chunk_index": row.get("chunk_index"),
        "start": row.get("start"),
        "end": row.get("end"),
        "evaluation_overlap_sec": row.get("evaluation_overlap_sec"),
        "status": row.get("status"),
        "method": row.get("method") or row.get("residual_method"),
        "text": row.get("text"),
        "reasons": row.get("reasons") or [],
    }


def outcome_for_row(
    source: dict[str, Any],
    *,
    scope: str,
    draft_turns: list[dict[str, Any]],
    selection_rows: list[dict[str, Any]],
    residual_rows: list[dict[str, Any]],
    candidate_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    reference = source.get("evaluation_reference") if isinstance(source.get("evaluation_reference"), dict) else {}
    start = safe_float(reference.get("start"))
    end = safe_float(reference.get("end"), start)
    accepted = rows_overlapping(
        start,
        end,
        draft_turns,
        minimum=MIN_OUTCOME_OVERLAP_SECONDS,
    )
    selections = rows_overlapping(start, end, selection_rows)
    residuals = rows_overlapping(start, end, residual_rows)
    candidates = rows_overlapping(start, end, candidate_rows)
    if accepted:
        outcome = "accepted"
        reason = "a contract-valid causal remote-active Me turn overlaps the evaluation interval"
    elif selections or residuals or candidates:
        outcome = "rejected"
        reasons: Counter[str] = Counter()
        for row in selections + residuals + candidates:
            if row.get("reason"):
                reasons[str(row.get("reason"))] += 1
            for value in row.get("reasons") or []:
                reasons[str(value)] += 1
        reason = "causal evidence was evaluated but failed the publication contract"
        if reasons:
            reason += ": " + ", ".join(value for value, _ in reasons.most_common(4))
    else:
        outcome = "unresolved"
        reason = "no bounded causal remote-active candidate covers this evaluation interval"
    return {
        "schema": ROW_SCHEMA,
        "id": source.get("id"),
        "session": source.get("session"),
        "scope": scope,
        "status": "stable",
        "outcome": outcome,
        "reason": reason,
        "evaluation_reference": {**reference, "use": "evaluation_only"},
        "publication": {
            "status": "shadow_candidate" if accepted else "blocked",
            "authoritative": False,
            "uses_batch_for_selection": False,
            "batch_fields_use": "evaluation_only",
        },
        "machine_evidence": {
            "accepted_candidates": [
                {
                    **compact_evidence(row),
                    "contract": draft_contract(row),
                }
                for row in accepted
            ],
            "selection_evidence": [compact_evidence(row) for row in selections[:20]],
            "residual_evidence": [compact_evidence(row) for row in residuals[:20]],
            "micro_asr_evidence": [
                {
                    **compact_evidence(row),
                    "contract": candidate_contract(row) if row.get("status") == "accepted" else None,
                }
                for row in candidates[:20]
            ],
        },
    }


def manifest_matches(expected: list[dict[str, Any]], current: list[dict[str, Any]]) -> bool:
    expected_by_path = {str(row.get("path")): row for row in expected}
    current_by_path = {str(row.get("path")): row for row in current}
    return set(expected_by_path) == set(current_by_path) and all(
        expected_by_path[path].get("sha256") == current_by_path[path].get("sha256")
        and expected_by_path[path].get("bytes") == current_by_path[path].get("bytes")
        for path in expected_by_path
    )


def session_report(
    session: Path,
    *,
    baseline_policy: str,
    candidate_policy: str,
    baseline_session: dict[str, Any],
    primary_rows: list[dict[str, Any]],
    cross_rows: list[dict[str, Any]],
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
    candidate_order = candidate.get("order_role_reconciliation") if isinstance(candidate.get("order_role_reconciliation"), dict) else {}
    draft = resolve_draft(session, candidate)
    draft_turns = [
        row
        for row in draft.get("turns") or []
        if isinstance(row, dict) and row.get("causal_remote_active_me_separation_v1_shadow") is True
    ]
    output = session / EXPERIMENT_RELATIVE
    selection_rows = read_jsonl(output / "selection.jsonl")
    residual_rows = read_jsonl(output / "residual_candidates.jsonl")
    candidate_rows = read_jsonl(output / "candidates.jsonl")
    outcomes = [
        outcome_for_row(
            row,
            scope="primary_remote_active",
            draft_turns=draft_turns,
            selection_rows=selection_rows,
            residual_rows=residual_rows,
            candidate_rows=candidate_rows,
        )
        for row in primary_rows
    ]
    outcomes += [
        outcome_for_row(
            row,
            scope="mixed_double_talk_cross_check",
            draft_turns=draft_turns,
            selection_rows=selection_rows,
            residual_rows=residual_rows,
            candidate_rows=candidate_rows,
        )
        for row in cross_rows
    ]
    expected_manifest = baseline_session.get("expected_input_sha256_manifest") or []
    current_manifest = authoritative_manifest(session)
    expected_previous = baseline_session.get("protected_previous_shadow")
    previous_shadow_ok = True
    if isinstance(expected_previous, dict) and expected_previous.get("path"):
        previous_path = session / str(expected_previous.get("path"))
        previous_shadow_ok = previous_path.is_file() and file_manifest(previous_path, session) == expected_previous
    baseline_shadow = session / "derived/live/target-me-shadow" / baseline_policy / "draft.json"
    candidate_shadow = session / "derived/live/target-me-shadow" / candidate_policy / "draft.json"
    contracts = [draft_contract(row) for row in draft_turns]
    accepted_source_rows = [row for row in candidate_rows if row.get("status") == "accepted"]
    source_contracts = [candidate_contract(row) for row in accepted_source_rows]
    baseline_missing = safe_float(baseline_metrics.get("live_missing_me_seconds"))
    candidate_missing = safe_float(candidate_metrics.get("live_missing_me_seconds"))
    baseline_remote = safe_float(baseline_metrics.get("live_suspected_remote_leak_in_me_seconds"))
    candidate_remote = safe_float(candidate_metrics.get("live_suspected_remote_leak_in_me_seconds"))
    baseline_f1 = safe_float(baseline_metrics.get("live_batch_token_f1"))
    candidate_f1 = safe_float(candidate_metrics.get("live_batch_token_f1"))
    baseline_review_seconds = safe_float(baseline_review.get("review_burden_sec"))
    candidate_review_seconds = safe_float(candidate_review.get("review_burden_sec"))
    checks = {
        "profiles_present": bool(baseline and candidate),
        "missing_me_not_worse": candidate_missing <= baseline_missing + EPSILON,
        "remote_like_me_not_worse": candidate_remote <= baseline_remote + EPSILON,
        "effective_order_blockers_zero": safe_int(candidate_order.get("blocking_count")) == 0,
        "token_f1_not_worse": candidate_f1 + EPSILON >= baseline_f1,
        "review_burden_not_worse": candidate_review_seconds <= baseline_review_seconds + EPSILON,
        "candidate_contracts_pass": all(row.get("passed") is True for row in contracts + source_contracts),
        "batch_selection_not_used": all(
            row.get("used_batch_fields_for_selection") is False for row in selection_rows + candidate_rows
        ),
        "batch_authoritative": candidate.get("batch_authoritative") is True,
        "promotion_blocked": candidate.get("promotion_allowed") is False,
        "authoritative_inputs_unchanged": manifest_matches(expected_manifest, current_manifest),
        "previous_shadow_unchanged": previous_shadow_ok,
    }
    return (
        {
            "session": session.name,
            "status": "passed" if all(checks.values()) else "failed",
            "checks": checks,
            "comparison": str(comparison_path.relative_to(session)),
            "baseline_profile": baseline_policy,
            "candidate_profile": candidate_policy,
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
                    "before": baseline_review_seconds,
                    "after": candidate_review_seconds,
                    "delta": round(candidate_review_seconds - baseline_review_seconds, 3),
                },
                "candidate_added_turn_count": len(draft_turns),
                "candidate_added_turn_seconds": round(
                    sum(max(0.0, safe_float(row.get("end")) - safe_float(row.get("start"))) for row in draft_turns),
                    3,
                ),
            },
            "outcomes": {
                "row_count": len(outcomes),
                "by_scope": dict(sorted(Counter(str(row.get("scope")) for row in outcomes).items())),
                "by_outcome": dict(sorted(Counter(str(row.get("outcome")) for row in outcomes).items())),
            },
            "sha256_manifests": {
                "authoritative_inputs": current_manifest,
                "expected_authoritative_inputs": expected_manifest,
                "experiment_inputs_and_baseline_shadow": experiment_input_manifest(session, baseline_policy),
                "candidate_shadow": file_manifest(candidate_shadow, session) if candidate_shadow.is_file() else None,
                "baseline_shadow": file_manifest(baseline_shadow, session) if baseline_shadow.is_file() else None,
                "previous_shadow_from_v2_report": expected_previous,
            },
        },
        outcomes,
    )


def corpus_agreement(
    corpus: dict[str, Any],
    *,
    candidate_policy: str,
    session_reports: list[dict[str, Any]],
) -> dict[str, Any]:
    diagnostics = corpus.get("live_target_me_shadow_profile_diagnostics") if isinstance(corpus.get("live_target_me_shadow_profile_diagnostics"), dict) else {}
    real = diagnostics.get("real") if isinstance(diagnostics.get("real"), dict) else {}
    profiles = real.get("profiles") if isinstance(real.get("profiles"), list) else []
    observed = next((row for row in profiles if isinstance(row, dict) and row.get("policy") == candidate_policy), {})
    best = real.get("best_live_implementable_profile") if isinstance(real.get("best_live_implementable_profile"), dict) else {}
    expected = {
        "evaluated_session_count": len(session_reports),
        "policy": candidate_policy,
        "missing_me_seconds": round(sum(safe_float((row.get("metrics") or {}).get("missing_me_seconds", {}).get("after")) for row in session_reports), 3),
        "remote_like_me_seconds": round(sum(safe_float((row.get("metrics") or {}).get("remote_like_me_seconds", {}).get("after")) for row in session_reports), 3),
        "effective_order_blockers": sum(safe_int((row.get("metrics") or {}).get("effective_order_blockers")) for row in session_reports),
        "candidate_added_turn_count": sum(safe_int((row.get("metrics") or {}).get("candidate_added_turn_count")) for row in session_reports),
        "candidate_added_turn_seconds": round(sum(safe_float((row.get("metrics") or {}).get("candidate_added_turn_seconds")) for row in session_reports), 3),
    }
    actual = {
        "evaluated_session_count": safe_int(observed.get("evaluated_session_count")),
        "policy": best.get("policy"),
        "missing_me_seconds": safe_float(observed.get("live_missing_me_seconds")),
        "remote_like_me_seconds": safe_float(observed.get("live_suspected_remote_leak_in_me_seconds")),
        "effective_order_blockers": safe_int(observed.get("live_effective_blocking_contentful_role_constrained_order_mismatch_count")),
        "candidate_added_turn_count": safe_int(observed.get("causal_remote_active_me_separation_v1_added_turn_count")),
        "candidate_added_turn_seconds": safe_float(observed.get("causal_remote_active_me_separation_v1_added_turn_seconds")),
    }
    checks = {
        "report_present": bool(corpus),
        "session_count": actual["evaluated_session_count"] == expected["evaluated_session_count"],
        "selected_policy": actual["policy"] == expected["policy"],
        "missing_me_seconds": abs(actual["missing_me_seconds"] - expected["missing_me_seconds"]) <= 0.02,
        "remote_like_me_seconds": abs(actual["remote_like_me_seconds"] - expected["remote_like_me_seconds"]) <= 0.02,
        "effective_order_blockers": actual["effective_order_blockers"] == expected["effective_order_blockers"],
        "candidate_added_turn_count": actual["candidate_added_turn_count"] == expected["candidate_added_turn_count"],
        "candidate_added_turn_seconds": abs(actual["candidate_added_turn_seconds"] - expected["candidate_added_turn_seconds"]) <= 0.02,
    }
    return {
        "status": "passed" if all(checks.values()) else "failed",
        "checks": checks,
        "expected": expected,
        "observed": actual,
    }


def render_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# Causal Remote-Active Me Separation v1",
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
        f"- primary scope: `{summary.get('primary_row_count')}` rows / `{summary.get('primary_seconds')}s`",
        f"- primary outcomes: `{summary.get('primary_by_outcome')}`",
        f"- mixed/double-talk cross-check: `{summary.get('cross_check_row_count')}` rows / "
        f"`{summary.get('cross_check_seconds')}s`",
        f"- cross-check outcomes: `{summary.get('cross_check_by_outcome')}`",
        f"- missing Me: `{summary.get('baseline_missing_me_seconds')}s -> "
        f"{summary.get('candidate_missing_me_seconds')}s`",
        f"- recovered Me: `{summary.get('recovered_me_seconds')}s`",
        f"- remote-like Me: `{summary.get('baseline_remote_like_me_seconds')}s -> "
        f"{summary.get('candidate_remote_like_me_seconds')}s`",
        f"- effective order blockers: `{summary.get('effective_order_blocker_count')}`",
        f"- review burden: `{summary.get('review_burden_seconds')}s`",
        f"- added shadow turns: `{summary.get('candidate_added_turn_count')}` / "
        f"`{summary.get('candidate_added_turn_seconds')}s`",
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
            f"added `{metrics.get('candidate_added_turn_count')}` turns, "
            f"outcomes `{(row.get('outcomes') or {}).get('by_outcome')}`"
        )
    lines += [
        "",
        "## Contract",
        "",
        "Candidate selection uses committed PCM, past-only Target-Me enrollment and past "
        "remote-dominant training. FIR, spectral projection and their bounded hybrid are "
        "evaluated before micro-ASR. Remote audio/text guards can reject a candidate. Batch "
        "fields are used only here, after materialization, for corpus evaluation.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    baseline_report = read_json(args.baseline_report)
    baseline_sessions = {
        str(row.get("session")): row
        for row in baseline_report.get("sessions") or []
        if isinstance(row, dict) and row.get("session")
    }
    session_ids = list(baseline_sessions)
    primary_rows: list[dict[str, Any]] = []
    for row in read_jsonl(args.baseline_rows):
        selection = (row.get("machine_evidence") or {}).get("selection_evidence") or []
        if row.get("outcome") == "rejected" and any(
            (evidence.get("checks") or {}).get("speaker_supported") is True
            and (evidence.get("checks") or {}).get("remote_audio_strictly_quiet") is False
            for evidence in selection
            if isinstance(evidence, dict)
        ):
            primary_rows.append(row)
    cross_rows = [
        row
        for row in read_jsonl(args.hardening_rows)
        if row.get("disposition") == "mixed_double_talk"
    ]
    primary_by_session: dict[str, list[dict[str, Any]]] = defaultdict(list)
    cross_by_session: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in primary_rows:
        primary_by_session[str(row.get("session") or "")].append(row)
    for row in cross_rows:
        cross_by_session[str(row.get("session") or "")].append(row)
    session_reports: list[dict[str, Any]] = []
    outcomes: list[dict[str, Any]] = []
    for session_id in session_ids:
        report, rows = session_report(
            args.sessions_root / session_id,
            baseline_policy=args.baseline_profile,
            candidate_policy=args.candidate_profile,
            baseline_session=baseline_sessions[session_id],
            primary_rows=primary_by_session.get(session_id, []),
            cross_rows=cross_by_session.get(session_id, []),
        )
        session_reports.append(report)
        outcomes.extend(rows)
        write_jsonl(
            args.sessions_root / session_id / EXPERIMENT_RELATIVE / "outcomes.jsonl",
            rows,
        )
    primary_outcomes = [row for row in outcomes if row.get("scope") == "primary_remote_active"]
    cross_outcomes = [row for row in outcomes if row.get("scope") == "mixed_double_talk_cross_check"]
    baseline_missing = round(sum(safe_float((row.get("metrics") or {}).get("missing_me_seconds", {}).get("before")) for row in session_reports), 3)
    candidate_missing = round(sum(safe_float((row.get("metrics") or {}).get("missing_me_seconds", {}).get("after")) for row in session_reports), 3)
    baseline_remote = round(sum(safe_float((row.get("metrics") or {}).get("remote_like_me_seconds", {}).get("before")) for row in session_reports), 3)
    candidate_remote = round(sum(safe_float((row.get("metrics") or {}).get("remote_like_me_seconds", {}).get("after")) for row in session_reports), 3)
    review_burden = round(sum(safe_float((row.get("metrics") or {}).get("review_burden_sec", {}).get("after")) for row in session_reports), 3)
    improvements = [
        safe_float((row.get("metrics") or {}).get("missing_me_seconds", {}).get("before"))
        - safe_float((row.get("metrics") or {}).get("missing_me_seconds", {}).get("after"))
        for row in session_reports
    ]
    primary_seconds = round(sum(safe_float((row.get("evaluation_reference") or {}).get("duration_sec")) for row in primary_outcomes), 3)
    cross_seconds = round(sum(safe_float((row.get("evaluation_reference") or {}).get("duration_sec")) for row in cross_outcomes), 3)
    summary = {
        "session_count": len(session_reports),
        "passing_session_count": sum(1 for row in session_reports if row.get("status") == "passed"),
        "primary_row_count": len(primary_outcomes),
        "primary_seconds": primary_seconds,
        "primary_by_outcome": dict(sorted(Counter(str(row.get("outcome")) for row in primary_outcomes).items())),
        "cross_check_row_count": len(cross_outcomes),
        "cross_check_seconds": cross_seconds,
        "cross_check_by_outcome": dict(sorted(Counter(str(row.get("outcome")) for row in cross_outcomes).items())),
        "stable_outcome_count": sum(1 for row in outcomes if row.get("status") == "stable"),
        "baseline_missing_me_seconds": baseline_missing,
        "candidate_missing_me_seconds": candidate_missing,
        "recovered_me_seconds": round(baseline_missing - candidate_missing, 3),
        "max_session_improvement_seconds": round(max(improvements, default=0.0), 3),
        "baseline_remote_like_me_seconds": baseline_remote,
        "candidate_remote_like_me_seconds": candidate_remote,
        "effective_order_blocker_count": sum(safe_int((row.get("metrics") or {}).get("effective_order_blockers")) for row in session_reports),
        "review_burden_seconds": review_burden,
        "candidate_added_turn_count": sum(safe_int((row.get("metrics") or {}).get("candidate_added_turn_count")) for row in session_reports),
        "candidate_added_turn_seconds": round(sum(safe_float((row.get("metrics") or {}).get("candidate_added_turn_seconds")) for row in session_reports), 3),
    }
    agreement = corpus_agreement(
        read_json(args.corpus_report),
        candidate_policy=args.candidate_profile,
        session_reports=session_reports,
    )
    completion_checks = {
        "expected_session_scope": len(session_reports) == EXPECTED_SESSION_COUNT,
        "expected_primary_scope": len(primary_outcomes) == EXPECTED_PRIMARY_ROWS
        and abs(primary_seconds - EXPECTED_PRIMARY_SECONDS) <= 0.02,
        "expected_cross_check_scope": len(cross_outcomes) == EXPECTED_CROSS_ROWS
        and abs(cross_seconds - EXPECTED_CROSS_SECONDS) <= 0.02,
        "all_outcomes_stable": all(
            row.get("status") == "stable" and row.get("outcome") in {"accepted", "rejected", "unresolved"}
            for row in outcomes
        ),
        "baseline_matches_v2": abs(baseline_missing - EXPECTED_BASELINE_MISSING_ME_SECONDS) <= 0.02
        and abs(baseline_remote - EXPECTED_BASELINE_REMOTE_LIKE_ME_SECONDS) <= 0.02
        and abs(review_burden - EXPECTED_BASELINE_REVIEW_BURDEN_SECONDS) <= 0.02,
        "material_session_improvement": max(improvements, default=0.0)
        >= MATERIAL_SESSION_IMPROVEMENT_SECONDS,
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
            "name": "report-live-causal-remote-active-me-separation-v1",
            "version": SCRIPT_VERSION,
        },
        "created_at": datetime.now(UTC).isoformat(),
        "status": status,
        "baseline_profile": args.baseline_profile,
        "selected_shadow_profile": args.candidate_profile,
        "batch_authoritative": True,
        "promotion_allowed": False,
        "promotion_reason": "explicit-only causal experiment remains shadow-locked",
        "selection_contract": {
            "live_only": True,
            "batch_fields_used_for_selection": False,
            "batch_fields_use": "evaluation_only",
            "required_evidence": [
                "committed_pcm",
                "past_only_target_me_enrollment",
                "past_remote_dominant_training",
                "causal_fir_or_spectral_projection",
                "bounded_micro_asr",
                "remote_audio_and_text_guards",
            ],
        },
        "completion_checks": completion_checks,
        "corpus_agreement": agreement,
        "summary": summary,
        "sessions": session_reports,
        "outputs": {
            "report": "causal_remote_active_me_separation_v1.json",
            "rows": "causal_remote_active_me_separation_v1.jsonl",
            "markdown": "causal_remote_active_me_separation_v1.md",
            "standard_corpus_report": str(args.corpus_report),
        },
    }
    json_path = args.out_dir / "causal_remote_active_me_separation_v1.json"
    jsonl_path = args.out_dir / "causal_remote_active_me_separation_v1.jsonl"
    markdown_path = args.out_dir / "causal_remote_active_me_separation_v1.md"
    write_json(json_path, report)
    write_jsonl(jsonl_path, outcomes)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    print(f"status: {status}")
    print(f"sessions: {len(session_reports)}")
    print(f"primary_scope: {len(primary_outcomes)} / {primary_seconds}s")
    print(f"cross_check_scope: {len(cross_outcomes)} / {cross_seconds}s")
    print(f"recovered_me_seconds: {summary['recovered_me_seconds']}")
    print(f"remote_like_me_seconds: {candidate_remote}")
    print(f"effective_order_blockers: {summary['effective_order_blocker_count']}")
    print(f"report: {json_path}")
    return 0 if status == "passed_shadow_only" else 1


if __name__ == "__main__":
    raise SystemExit(main())
