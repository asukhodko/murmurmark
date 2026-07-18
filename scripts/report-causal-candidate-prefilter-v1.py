#!/usr/bin/env python3
"""Publish completion evidence for Causal Candidate Coverage and Cheap Negative Prefilter v1."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any


SCHEMA = "murmurmark.causal_candidate_prefilter_report/v1"
DECISION_SCHEMA = "murmurmark.causal_candidate_prefilter_promotion_decision/v1"
SCRIPT_VERSION = "1.0.0"
PROFILE = "causal-candidate-coverage-cheap-negative-prefilter-v1"
EXPECTED_SOURCE_ROWS = 783
EXPECTED_FIXED_RECOVERIES = 4
EXPECTED_FIXED_SECONDS = 11.56
NEGATIVE_CLASSES = {"probable_remote_leak", "probable_asr_noise"}
ROUTES = ("cheap_reject", "expensive_candidate", "unresolved")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sessions-root", type=Path, default=Path("sessions"))
    parser.add_argument(
        "--baseline-dir",
        type=Path,
        default=Path("sessions/_reports/live-pipeline/causal-recovery-generalization-v1"),
    )
    parser.add_argument(
        "--fixed-report",
        type=Path,
        default=Path(
            "sessions/_reports/live-pipeline/causal-double-talk-me-recovery-v1/"
            "recovery_report_v1.json"
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(
            "sessions/_reports/live-pipeline/"
            "causal-candidate-coverage-cheap-negative-prefilter-v1"
        ),
    )
    parser.add_argument("--skip-input-verification", action="store_true")
    parser.add_argument("--require-decision", action="store_true")
    return parser.parse_args()


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_script(name: str, module_name: str) -> ModuleType:
    path = Path(__file__).with_name(name)
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load helper: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def interval_overlap(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def offline_dir(session: Path) -> Path:
    return session / "derived/live" / PROFILE / "offline"


def runtime_dir(session: Path) -> Path:
    return session / "derived/live" / PROFILE / "runtime"


def source_outcome_id(session: str, selection_id: str) -> str:
    return f"source:{session}:{selection_id}"


def candidate_outcome_id(session: str, candidate_id: str) -> str:
    return f"candidate:{session}:{candidate_id}"


def collect_decisions(
    run_state: dict[str, Any],
    sessions_root: Path,
    outcomes_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for session_row in run_state.get("sessions") or []:
        session_id = str(session_row.get("session") or "")
        if not session_id:
            continue
        for decision in read_jsonl(offline_dir(sessions_root / session_id) / "cheap_prefilter_decisions.jsonl"):
            selection_id = str(decision.get("id") or "")
            outcome = outcomes_by_id.get(source_outcome_id(session_id, selection_id), {})
            rows.append(
                {
                    **decision,
                    "corpus_row_id": source_outcome_id(session_id, selection_id),
                    "evaluation": {
                        "classification": outcome.get("evaluation_classification"),
                        "reason": outcome.get("reason"),
                        "use": "post_selection_only",
                    },
                }
            )
    return sorted(
        rows,
        key=lambda row: (
            str(row.get("session") or ""),
            safe_int(row.get("chunk_index")),
            safe_float(row.get("start")),
            str(row.get("id") or ""),
        ),
    )


def decision_evidence(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    routes = Counter(str(row.get("route") or "unknown") for row in decisions)
    class_routes: dict[str, Counter[str]] = defaultdict(Counter)
    invalid_contract: list[str] = []
    future_context_rows: list[dict[str, Any]] = []
    cheap_reject_genuine: list[str] = []
    by_source_id = {
        (str(row.get("session") or ""), str(row.get("id") or "")): row
        for row in decisions
        if row.get("id")
    }
    for row in decisions:
        evaluation = row.get("evaluation") if isinstance(row.get("evaluation"), dict) else {}
        classification = str(evaluation.get("classification") or "unknown")
        route = str(row.get("route") or "unknown")
        class_routes[classification][route] += 1
        if (
            row.get("timeline_causal") is not True
            or row.get("used_batch_fields_for_selection") is not False
            or route not in ROUTES
        ):
            invalid_contract.append(str(row.get("corpus_row_id") or row.get("id") or ""))
        if route == "cheap_reject" and classification == "genuine_double_talk":
            cheap_reject_genuine.append(str(row.get("corpus_row_id") or row.get("id") or ""))
        routing = row.get("routing") if isinstance(row.get("routing"), dict) else {}
        context_seed_id = str(routing.get("context_seed_id") or "")
        if context_seed_id:
            seed = by_source_id.get((str(row.get("session") or ""), context_seed_id))
            if (
                not seed
                or safe_int(seed.get("chunk_index")) > safe_int(row.get("chunk_index"))
                or safe_float(seed.get("end"), float("inf")) > safe_float(row.get("end"))
            ):
                future_context_rows.append(
                    {
                        "session": row.get("session"),
                        "id": row.get("id"),
                        "chunk_index": row.get("chunk_index"),
                        "end": row.get("end"),
                        "context_seed_id": context_seed_id,
                        "context_seed_chunk_index": seed.get("chunk_index") if seed else None,
                        "context_seed_end": seed.get("end") if seed else None,
                    }
                )
    fingerprint_rows = [
        {
            "session": row.get("session"),
            "id": row.get("id"),
            "chunk_index": row.get("chunk_index"),
            "route": row.get("route"),
            "reasons": row.get("reasons") or [],
            "decision_fingerprint_sha256": row.get("decision_fingerprint_sha256"),
        }
        for row in decisions
    ]
    return {
        "decision_count": len(decisions),
        "route_counts": {route: routes.get(route, 0) for route in ROUTES},
        "route_ratios": {
            route: round(routes.get(route, 0) / len(decisions), 6) if decisions else 0.0
            for route in ROUTES
        },
        "evaluation_class_route_matrix": {
            classification: {route: values.get(route, 0) for route in ROUTES}
            for classification, values in sorted(class_routes.items())
        },
        "invalid_contract_rows": invalid_contract,
        "future_context_rows": future_context_rows,
        "cheap_reject_genuine_double_talk_rows": cheap_reject_genuine,
        "decision_fingerprint_sha256": canonical_sha256(fingerprint_rows),
    }


def collect_candidates(
    run_state: dict[str, Any],
    sessions_root: Path,
    outcomes_by_id: dict[str, dict[str, Any]],
    compare: ModuleType,
    generalization: ModuleType,
) -> dict[str, Any]:
    all_rows: list[dict[str, Any]] = []
    accepted: list[dict[str, Any]] = []
    unmatched: list[str] = []
    negative: list[str] = []
    frozen_negative: list[str] = []
    posthoc_negative: list[str] = []
    negative_rows: list[dict[str, Any]] = []
    posthoc_evaluation_count = 0
    for session_row in run_state.get("sessions") or []:
        session_id = str(session_row.get("session") or "")
        session = sessions_root / session_id
        batch = compare.read_utterances(generalization.authoritative_dialogue_path(session))
        rows = read_jsonl(offline_dir(sessions_root / session_id) / "candidates.jsonl")
        all_rows.extend({**row, "session": session_id} for row in rows)
        for row in rows:
            if row.get("status") != "accepted":
                continue
            candidate_id = str(row.get("id") or "")
            outcome_id = candidate_outcome_id(session_id, candidate_id)
            outcome = outcomes_by_id.get(outcome_id)
            evaluation_source = "immutable_generalization_outcome"
            if outcome is None and batch:
                start = safe_float(row.get("start"))
                end = safe_float(row.get("end"), start)
                overlaps = [
                    {
                        **item,
                        "overlap_sec": round(
                            interval_overlap(
                                start,
                                end,
                                safe_float(item.get("start")),
                                safe_float(item.get("end"), safe_float(item.get("start"))),
                            ),
                            3,
                        ),
                    }
                    for item in batch
                    if interval_overlap(
                        start,
                        end,
                        safe_float(item.get("start")),
                        safe_float(item.get("end"), safe_float(item.get("start"))),
                    )
                    > 0.0
                ]
                nearest = sorted(
                    batch,
                    key=lambda item: min(
                        abs(start - safe_float(item.get("end"))),
                        abs(end - safe_float(item.get("start"))),
                    ),
                )[:4]
                classified = generalization.classify_row(
                    {
                        "interval": {
                            "start": start,
                            "end": end,
                            "duration_sec": max(0.0, end - start),
                        },
                        "causal_selection": {"text": clean_text(row.get("text"))},
                        "evaluation_reference": {
                            "overlapping_utterances": overlaps,
                            "nearest_utterances": nearest,
                            "source": "authoritative_batch",
                            "use": "evaluation_only",
                        },
                    },
                    compare,
                )
                outcome = {
                    "evaluation_classification": classified.get("classification"),
                    "reason": classified.get("reason"),
                    "features": classified.get("features") or {},
                }
                evaluation_source = "posthoc_current_candidate_authoritative_batch"
                posthoc_evaluation_count += 1
            enriched = {
                **row,
                "session": session_id,
                "corpus_row_id": outcome_id,
                "evaluation_classification": (
                    outcome.get("evaluation_classification") if outcome else None
                ),
                "evaluation_source": evaluation_source if outcome else None,
                "evaluation_reason": outcome.get("reason") if outcome else None,
            }
            accepted.append(enriched)
            if outcome is None:
                unmatched.append(outcome_id)
            elif outcome.get("evaluation_classification") in NEGATIVE_CLASSES:
                negative.append(outcome_id)
                if evaluation_source == "immutable_generalization_outcome":
                    frozen_negative.append(outcome_id)
                else:
                    posthoc_negative.append(outcome_id)
                negative_rows.append(
                    {
                        "id": outcome_id,
                        "session": session_id,
                        "candidate_id": candidate_id,
                        "start": row.get("start"),
                        "end": row.get("end"),
                        "duration_sec": row.get("duration_sec"),
                        "text": clean_text(row.get("text")),
                        "classification": outcome.get("evaluation_classification"),
                        "evaluation_source": evaluation_source,
                        "reason": outcome.get("reason"),
                    }
                )
    return {
        "candidate_count": len(all_rows),
        "accepted_candidate_count": len(accepted),
        "accepted_candidate_seconds": round(
            sum(safe_float(row.get("duration_sec")) for row in accepted), 3
        ),
        "accepted_negative_control_count": len(negative),
        "accepted_negative_control_ids": negative,
        "accepted_frozen_negative_control_count": len(frozen_negative),
        "accepted_frozen_negative_control_ids": frozen_negative,
        "accepted_posthoc_negative_count": len(posthoc_negative),
        "accepted_posthoc_negative_ids": posthoc_negative,
        "accepted_negative_rows": negative_rows,
        "accepted_without_evaluation_count": len(unmatched),
        "accepted_without_evaluation_ids": unmatched,
        "posthoc_current_candidate_evaluation_count": posthoc_evaluation_count,
        "accepted_rows": accepted,
    }


def fixed_recovery_evidence(
    fixed_report: dict[str, Any],
    accepted_rows: list[dict[str, Any]],
    compare: ModuleType,
) -> dict[str, Any]:
    matches: list[dict[str, Any]] = []
    accepted_fixed = [
        row
        for row in fixed_report.get("outcomes") or []
        if isinstance(row, dict) and row.get("status") == "accepted"
    ]
    for fixed in accepted_fixed:
        reference = (
            fixed.get("evaluation_reference")
            if isinstance(fixed.get("evaluation_reference"), dict)
            else {}
        )
        candidates = [
            row for row in accepted_rows if row.get("session") == fixed.get("session")
        ]
        best: dict[str, Any] | None = None
        for candidate in candidates:
            overlap = interval_overlap(
                safe_float(candidate.get("start")),
                safe_float(candidate.get("end")),
                safe_float(reference.get("start")),
                safe_float(reference.get("end")),
            )
            metrics = compare.bag_overlap_metrics(
                compare.tokens(clean_text(candidate.get("text"))),
                compare.tokens(clean_text(reference.get("text"))),
            )
            current = {
                "candidate_id": candidate.get("id"),
                "overlap_sec": round(overlap, 3),
                "token_f1": safe_float(metrics.get("live_batch_token_f1")),
                "reference_recall": safe_float(metrics.get("batch_token_recall_in_live")),
            }
            if best is None or (current["overlap_sec"], current["token_f1"]) > (
                best["overlap_sec"],
                best["token_f1"],
            ):
                best = current
        recovered = bool(best and best["overlap_sec"] >= 0.35 and best["token_f1"] >= 0.20)
        matches.append(
            {
                "fixed_id": fixed.get("id"),
                "session": fixed.get("session"),
                "expected_recovered_seconds": safe_float(fixed.get("recovered_seconds")),
                "recovered": recovered,
                "match": best,
            }
        )
    recovered_rows = [row for row in matches if row.get("recovered")]
    return {
        "expected_recovery_count": EXPECTED_FIXED_RECOVERIES,
        "recovered_count": len(recovered_rows),
        "expected_recovered_seconds": EXPECTED_FIXED_SECONDS,
        "recovered_seconds": round(
            sum(safe_float(row.get("expected_recovered_seconds")) for row in recovered_rows), 3
        ),
        "matches": matches,
    }


def no_regression_evidence(
    manifest: dict[str, Any],
    sessions_root: Path,
    compare: ModuleType,
    runtime_helper: ModuleType,
    generalization: ModuleType,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    session_ids = list(manifest.get("regression_sessions") or []) + list(
        manifest.get("holdout_sessions") or []
    )
    for session_id in session_ids:
        session = sessions_root / str(session_id)
        dialogue_path = generalization.authoritative_dialogue_path(session)
        batch = compare.read_utterances(dialogue_path)
        chunks = read_jsonl(session / "derived/live/chunks.jsonl")
        base_turns = compare.live_turns(session, chunks)
        candidate_path = offline_dir(session) / "candidates.jsonl"
        candidate_rows = read_jsonl(candidate_path)
        strict_turns, contract_rejected = compare.causal_double_talk_me_recovery_v1_shadow_turns(
            session,
            candidate_path if candidate_path.is_file() else None,
        )
        strict_ids = {str(row.get("id") or "") for row in strict_turns}
        marked, effective_rows = runtime_helper.mark_superseded_candidates(
            candidate_rows,
            source="causal-double-talk-me-recovery-v1-prefilter-offline",
            baseline_turns=base_turns,
            compare=compare,
        )
        effective_ids = {
            str(row.get("id") or "") for row in effective_rows if str(row.get("id") or "") in strict_ids
        }
        recovery_turns = [
            runtime_helper.candidate_turn(
                row,
                "causal-double-talk-me-recovery-v1-prefilter-publication",
            )
            for row in effective_rows
            if str(row.get("id") or "") in effective_ids
        ]
        before = compare.parity_metrics_for_turns(
            base_turns,
            batch,
            match_mode_prefix="prefilter_before",
        )
        after = compare.parity_metrics_for_turns(
            base_turns + recovery_turns,
            batch,
            match_mode_prefix="prefilter_after",
        )
        before_blocker_rows = [
            row
            for row in compare.order_mismatch_rows_for_turns(
                base_turns,
                batch,
                same_role_only=True,
                contentful_only=True,
                min_token_recall=0.45,
                min_score=0.65,
                match_mode="prefilter_before_role_constrained_contentful",
            )
            if compare.order_mismatch_is_blocking(row)
        ]
        after_blocker_rows = [
            row
            for row in compare.order_mismatch_rows_for_turns(
                base_turns + recovery_turns,
                batch,
                same_role_only=True,
                contentful_only=True,
                min_token_recall=0.45,
                min_score=0.65,
                match_mode="prefilter_after_role_constrained_contentful",
            )
            if compare.order_mismatch_is_blocking(row)
        ]
        before_text = compare.bag_overlap_metrics(
            compare.tokens(" ".join(clean_text(row.get("text")) for row in base_turns)),
            compare.tokens(" ".join(clean_text(row.get("text")) for row in batch)),
        )
        after_text = compare.bag_overlap_metrics(
            compare.tokens(
                " ".join(clean_text(row.get("text")) for row in base_turns + recovery_turns)
            ),
            compare.tokens(" ".join(clean_text(row.get("text")) for row in batch)),
        )
        before_missing = safe_float(before.get("live_missing_me_seconds"))
        after_missing = safe_float(after.get("live_missing_me_seconds"))
        before_remote = safe_float(before.get("live_suspected_remote_leak_in_me_seconds"))
        after_remote = safe_float(after.get("live_suspected_remote_leak_in_me_seconds"))
        before_order = safe_int(
            before.get("live_blocking_contentful_role_constrained_order_mismatch_count")
        )
        after_order = safe_int(
            after.get("live_blocking_contentful_role_constrained_order_mismatch_count")
        )
        before_f1 = safe_float(before_text.get("live_batch_token_f1"))
        after_f1 = safe_float(after_text.get("live_batch_token_f1"))
        before_burden = before_missing + before_remote
        after_burden = after_missing + after_remote
        accepted_count = sum(row.get("status") == "accepted" for row in candidate_rows)
        superseded = [
            row
            for row in marked
            if row.get("runtime_publication_status") == "superseded_by_later_base_turn"
        ]
        checks = {
            "authoritative_batch_present": bool(batch),
            "accepted_candidates_pass_strict_contract_or_are_rejected": (
                len(strict_turns) + len(contract_rejected) >= accepted_count
            ),
            "remote_like_me_not_increased": after_remote <= before_remote + 0.001,
            "effective_order_blockers_not_increased": after_order <= before_order,
            "token_f1_not_worse": after_f1 + 0.000001 >= before_f1,
            "mandatory_review_burden_not_increased": after_burden <= before_burden + 0.001,
        }
        rows.append(
            {
                "session": session_id,
                "status": "passed" if all(checks.values()) else "failed",
                "checks": checks,
                "algorithm_accepted_candidate_count": accepted_count,
                "strict_candidate_count": len(strict_turns),
                "effective_publication_candidate_count": len(recovery_turns),
                "superseded_by_base_me_count": len(superseded),
                "superseded_ids": [row.get("id") for row in superseded],
                "blocking_order_rows": {
                    "before": before_blocker_rows,
                    "after": after_blocker_rows,
                },
                "metrics": {
                    "missing_me_seconds": {"before": before_missing, "after": after_missing},
                    "remote_like_me_seconds": {"before": before_remote, "after": after_remote},
                    "effective_order_blockers": {"before": before_order, "after": after_order},
                    "token_f1": {"before": before_f1, "after": after_f1},
                    "mandatory_review_burden_seconds": {
                        "before": round(before_burden, 3),
                        "after": round(after_burden, 3),
                    },
                },
            }
        )
    return {
        "status": "passed" if rows and all(row.get("status") == "passed" for row in rows) else "failed",
        "session_count": len(rows),
        "failed_sessions": [row.get("session") for row in rows if row.get("status") != "passed"],
        "sessions": rows,
    }


def runtime_evidence(manifest: dict[str, Any], sessions_root: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for session_id in manifest.get("holdout_sessions") or []:
        replay_path = runtime_dir(sessions_root / str(session_id)) / "paced_replay.json"
        replay = read_json(replay_path)
        agreement = replay.get("candidate_agreement") if isinstance(replay.get("candidate_agreement"), dict) else {}
        prefilter = agreement.get("cheap_prefilter") if isinstance(agreement.get("cheap_prefilter"), dict) else {}
        double = agreement.get("double_talk") if isinstance(agreement.get("double_talk"), dict) else {}
        efficiency = replay.get("efficiency") if isinstance(replay.get("efficiency"), dict) else {}
        warm = replay.get("warm_cache_verification") if isinstance(replay.get("warm_cache_verification"), dict) else {}
        invocations = replay.get("invocations") if isinstance(replay.get("invocations"), list) else []
        timeout_rows = [
            row
            for row in invocations
            if ((row.get("incremental_runtime") or {}).get("double_talk_v1") or {}).get(
                "timed_out"
            )
            is True
            or ((row.get("incremental_runtime") or {}).get("double_talk_v1") or {}).get(
                "status"
            )
            == "timed_out_fail_open"
        ]
        p95 = safe_float(efficiency.get("latency_p95_sec"), 999.0)
        stage_p95 = safe_float(efficiency.get("double_talk_latency_p95_sec"), 999.0)
        final_lag = safe_float(replay.get("final_live_lag_sec"), 999.0)
        checks = {
            "report_present": bool(replay),
            "runtime_generator_current": (
                ((replay.get("runtime_state") or {}).get("generator") or {}).get("version") == "1.3.0"
            ),
            "cheap_prefilter_routes_exact": prefilter.get("passed") is True,
            "expensive_candidates_exact": double.get("passed") is True,
            "warm_final_deterministic": warm.get("status") == "passed",
            "overall_runtime_p95_at_most_30s": p95 <= 30.0,
            "double_talk_stage_p95_at_most_30s": stage_p95 <= 30.0,
            "final_lag_zero": final_lag <= 0.001,
            "batch_authoritative": replay.get("batch_authoritative") is True,
            "promotion_blocked": replay.get("promotion_allowed") is False,
        }
        rows.append(
            {
                "session": session_id,
                "status": "passed" if all(checks.values()) else "failed",
                "checks": checks,
                "runtime_p95_sec": p95,
                "double_talk_stage_p95_sec": stage_p95,
                "runtime_max_sec": safe_float(efficiency.get("latency_max_sec")),
                "final_lag_sec": final_lag,
                "timeout_count": len(timeout_rows),
                "offline_accepted_candidate_count": safe_int(double.get("replay_count")),
                "runtime_accepted_candidate_count": safe_int(double.get("runtime_count")),
                "matched_candidate_count": safe_int(double.get("matched_count")),
                "missing_candidate_ids": double.get("missing_in_runtime") or [],
                "prefilter_offline_count": safe_int(prefilter.get("offline_count")),
                "prefilter_runtime_count": safe_int(prefilter.get("runtime_count")),
                "replay_report": str(replay_path),
            }
        )
    return {
        "status": "passed" if rows and all(row.get("status") == "passed" for row in rows) else "failed",
        "session_count": len(rows),
        "passed_session_count": sum(row.get("status") == "passed" for row in rows),
        "failed_sessions": [row.get("session") for row in rows if row.get("status") != "passed"],
        "route_agreement_count": sum(safe_int(row.get("prefilter_runtime_count")) for row in rows),
        "timeout_count": sum(safe_int(row.get("timeout_count")) for row in rows),
        "runtime_p95_max_sec": max((safe_float(row.get("runtime_p95_sec")) for row in rows), default=0.0),
        "final_lag_max_sec": max((safe_float(row.get("final_lag_sec")) for row in rows), default=0.0),
        "sessions": rows,
    }


def render_markdown(report: dict[str, Any], decision: dict[str, Any]) -> str:
    coverage = report.get("coverage") or {}
    candidates = report.get("expensive_stage") or {}
    fixed = report.get("fixed_recoveries") or {}
    runtime = report.get("runtime") or {}
    regression = report.get("no_regression") or {}
    lines = [
        "# Causal Candidate Coverage and Cheap Negative Prefilter v1",
        "",
        f"Decision: `{decision.get('decision')}`.",
        f"Reason: {decision.get('reason')}",
        "",
        "## Coverage",
        "",
        f"- Decisions: `{coverage.get('decision_count')}/{EXPECTED_SOURCE_ROWS}`.",
        f"- Routes: `{coverage.get('route_counts')}`.",
        f"- Decision fingerprint: `{coverage.get('decision_fingerprint_sha256')}`.",
        f"- Cheap rejects of genuine double-talk: `{len(coverage.get('cheap_reject_genuine_double_talk_rows') or [])}`.",
        "",
        "## Expensive stage",
        "",
        f"- Candidates: `{candidates.get('candidate_count')}`; accepted: `{candidates.get('accepted_candidate_count')}` / `{candidates.get('accepted_candidate_seconds')}` sec.",
        f"- Accepted negative controls: `{candidates.get('accepted_negative_control_count')}`.",
        f"- Fixed recoveries: `{fixed.get('recovered_count')}/{fixed.get('expected_recovery_count')}` / `{fixed.get('recovered_seconds')}` sec.",
        "",
        "## Runtime",
        "",
        f"- Holdouts passed: `{runtime.get('passed_session_count')}/{runtime.get('session_count')}`.",
        f"- Exact route agreement rows: `{runtime.get('route_agreement_count')}`.",
        f"- Maximum overall p95: `{runtime.get('runtime_p95_max_sec')}` sec; final lag max: `{runtime.get('final_lag_max_sec')}` sec.",
        f"- One-shot fail-open timeouts: `{runtime.get('timeout_count')}`.",
        "",
        "## Regression",
        "",
        f"- Sessions: `{regression.get('session_count')}`; failed: `{regression.get('failed_sessions')}`.",
        "- Candidate publication projection applies the existing live-only supersession guard before metrics.",
        "",
        "## Next",
        "",
        str(decision.get("next_experiment") or "No next experiment required."),
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parent.parent
    sessions_root = args.sessions_root.expanduser().resolve()
    baseline_dir = args.baseline_dir.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    manifest = read_json(baseline_dir / "corpus_manifest_v1.json")
    outcomes = read_jsonl(baseline_dir / "outcomes_v1.jsonl")
    outcomes_by_id = {str(row.get("id")): row for row in outcomes if row.get("id")}
    run_state = read_json(output_dir / "corpus_run_state.json")
    fixed_report = read_json(args.fixed_report.expanduser().resolve())
    generalization = load_script(
        "report-causal-recovery-generalization-v1.py",
        "murmurmark_prefilter_generalization_helpers",
    )
    compare = load_script("compare-live-batch.py", "murmurmark_prefilter_compare_helpers")
    runtime_helper = load_script(
        "live-causal-me-recovery-runtime.py",
        "murmurmark_prefilter_runtime_helpers",
    )

    frozen_inputs = generalization.verify_inputs(
        manifest,
        repo_root,
        args.skip_input_verification,
    )
    decisions = collect_decisions(run_state, sessions_root, outcomes_by_id)
    coverage = decision_evidence(decisions)
    candidates = collect_candidates(
        run_state,
        sessions_root,
        outcomes_by_id,
        compare,
        generalization,
    )
    fixed = fixed_recovery_evidence(
        fixed_report,
        candidates.pop("accepted_rows"),
        compare,
    )
    regression = no_regression_evidence(
        manifest,
        sessions_root,
        compare,
        runtime_helper,
        generalization,
    )
    runtime = runtime_evidence(manifest, sessions_root)

    hard_checks = {
        "immutable_corpus_valid": manifest.get("status") == "valid",
        "immutable_input_hashes_unchanged": frozen_inputs.get("status") == "passed",
        "corpus_runner_passed": run_state.get("status") == "passed",
        "all_eligible_rows_decided": coverage.get("decision_count") == EXPECTED_SOURCE_ROWS,
        "all_routes_known": sum((coverage.get("route_counts") or {}).values()) == EXPECTED_SOURCE_ROWS,
        "causal_contract_valid": not coverage.get("invalid_contract_rows"),
        "context_is_strictly_past_only": not coverage.get("future_context_rows"),
        "cheap_reject_preserves_genuine_double_talk": not coverage.get(
            "cheap_reject_genuine_double_talk_rows"
        ),
        "fixed_recoveries_preserved": (
            fixed.get("recovered_count") == EXPECTED_FIXED_RECOVERIES
            and abs(safe_float(fixed.get("recovered_seconds")) - EXPECTED_FIXED_SECONDS) <= 0.001
        ),
        "accepted_negative_controls_zero": candidates.get("accepted_negative_control_count") == 0,
        "accepted_candidates_have_evaluation": (
            candidates.get("accepted_without_evaluation_count") == 0
        ),
        "no_regression_all_sessions": regression.get("status") == "passed",
        "all_holdout_runtime_gates_pass": runtime.get("status") == "passed",
    }
    passed = all(hard_checks.values())
    decision_text = (
        "READY_FOR_PROMOTION_RECONSIDERATION" if passed else "DO_NOT_PROMOTE"
    )
    blockers = [name for name, value in hard_checks.items() if not value]
    reason = (
        "all frozen-corpus safety, quality and bounded-runtime gates passed"
        if passed
        else "promotion remains blocked by: " + ", ".join(blockers)
    )
    next_experiment = (
        "Run a separate guarded promotion/rollback goal."
        if passed
        else (
            "Close the live-recovery promotion branch and return product work to authoritative "
            "batch order/boundary review closure. Keep a persistent local faster-whisper worker "
            "as a future isolated hypothesis; it must reuse this immutable corpus and may not "
            "change route decisions, the 28s timeout, normal preview or batch authority."
        )
    )
    report = {
        "schema": SCHEMA,
        "generator": {"name": "report-causal-candidate-prefilter-v1", "version": SCRIPT_VERSION},
        "created_at": now_iso(),
        "status": "passed" if passed else "completed_with_blockers",
        "profile": PROFILE,
        "immutable_baseline": {
            "corpus_row_count": safe_int((manifest.get("summary") or {}).get("row_count")),
            "eligible_source_count": safe_int(
                (manifest.get("summary") or {}).get("eligible_source_count")
            ),
            "input_file_count": safe_int((manifest.get("summary") or {}).get("input_file_count")),
            "corpus_fingerprint_sha256": manifest.get("corpus_fingerprint_sha256"),
            "outcome_fingerprint_sha256": read_json(
                baseline_dir / "generalization_report_v1.json"
            ).get("outcome_fingerprint_sha256"),
            "input_verification": frozen_inputs,
        },
        "coverage": coverage,
        "expensive_stage": candidates,
        "fixed_recoveries": fixed,
        "runtime": runtime,
        "no_regression": regression,
        "hard_checks": hard_checks,
        "batch_authoritative": True,
        "normal_preview_connected": False,
        "promotion_allowed": False,
    }
    decision = {
        "schema": DECISION_SCHEMA,
        "created_at": now_iso(),
        "decision": decision_text,
        "reason": reason,
        "blockers": blockers,
        "promotion_gates": hard_checks,
        "next_experiment": next_experiment,
        "report": str(output_dir / "coverage_report_v1.json"),
        "batch_authoritative": True,
        "normal_preview_connected": False,
    }
    write_jsonl(output_dir / "cheap_prefilter_decisions_v1.jsonl", decisions)
    write_json(output_dir / "coverage_report_v1.json", report)
    write_json(output_dir / "runtime_equivalence_v1.json", runtime)
    write_json(output_dir / "no_regression_v1.json", regression)
    write_json(output_dir / "promotion_decision.json", decision)
    (output_dir / "coverage_report_v1.md").write_text(
        render_markdown(report, decision),
        encoding="utf-8",
    )
    print(f"decision: {decision_text}")
    print(f"decisions: {coverage.get('decision_count')}/{EXPECTED_SOURCE_ROWS}")
    print(f"fixed_recoveries: {fixed.get('recovered_count')}/{EXPECTED_FIXED_RECOVERIES}")
    print(f"accepted_negative_controls: {candidates.get('accepted_negative_control_count')}")
    print(f"runtime_holdouts: {runtime.get('passed_session_count')}/{runtime.get('session_count')}")
    print(f"no_regression: {regression.get('status')}")
    print(f"report: {output_dir / 'coverage_report_v1.json'}")
    if args.require_decision and not passed:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
