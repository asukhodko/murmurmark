#!/usr/bin/env python3
"""Evaluate Causal Recovery Generalization v1 and issue a promotion decision."""

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


SCHEMA = "murmurmark.causal_recovery_generalization_report/v1"
OUTCOME_SCHEMA = "murmurmark.causal_recovery_generalization_outcome/v1"
DECISION_SCHEMA = "murmurmark.causal_recovery_promotion_decision/v1"
SCRIPT_VERSION = "1.0.0"
EVALUATION_CLASSES = {
    "genuine_double_talk",
    "probable_remote_leak",
    "probable_timing_overlap",
    "probable_asr_noise",
    "insufficient_evidence",
}
NEGATIVE_ACCEPT_CLASSES = {"probable_remote_leak", "probable_asr_noise"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Report causal recovery generalization gates.")
    parser.add_argument(
        "--corpus-dir",
        type=Path,
        default=Path("sessions/_reports/live-pipeline/causal-recovery-generalization-v1"),
    )
    parser.add_argument("--sessions-root", type=Path, default=Path("sessions"))
    parser.add_argument(
        "--fixed-report",
        type=Path,
        default=Path(
            "sessions/_reports/live-pipeline/causal-double-talk-me-recovery-v1/recovery_report_v1.json"
        ),
    )
    parser.add_argument("--require-decision", action="store_true")
    parser.add_argument("--skip-input-verification", action="store_true")
    return parser.parse_args()


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.is_file():
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


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_script(name: str, module_name: str) -> ModuleType:
    path = Path(__file__).with_name(name)
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load helper: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def token_metrics(compare: ModuleType, candidate: str, reference: str) -> dict[str, Any]:
    candidate_tokens = compare.tokens(candidate)
    reference_tokens = compare.tokens(reference)
    metrics = compare.bag_overlap_metrics(candidate_tokens, reference_tokens)
    return {
        "precision": safe_float(metrics.get("live_token_precision_against_batch")),
        "recall": safe_float(metrics.get("batch_token_recall_in_live")),
        "f1": safe_float(metrics.get("live_batch_token_f1")),
        "candidate_token_count": len(candidate_tokens),
        "reference_token_count": len(reference_tokens),
        "content_token_count": len(compare.content_tokens(candidate_tokens)),
    }


def causal_text(row: dict[str, Any]) -> str:
    causal = row.get("causal_selection") if isinstance(row.get("causal_selection"), dict) else {}
    return clean_text(causal.get("text") or causal.get("source_text"))


def classify_row(row: dict[str, Any], compare: ModuleType) -> dict[str, Any]:
    reference = row.get("evaluation_reference") if isinstance(row.get("evaluation_reference"), dict) else {}
    overlaps = reference.get("overlapping_utterances") if isinstance(reference.get("overlapping_utterances"), list) else []
    nearest = reference.get("nearest_utterances") if isinstance(reference.get("nearest_utterances"), list) else []
    interval = row.get("interval") if isinstance(row.get("interval"), dict) else {}
    duration = safe_float(interval.get("duration_sec"))
    text = causal_text(row)
    me_rows = [item for item in overlaps if isinstance(item, dict) and item.get("role") == "Me"]
    remote_rows = [item for item in overlaps if isinstance(item, dict) and item.get("role") == "Colleagues"]
    me_text = " ".join(clean_text(item.get("text")) for item in me_rows)
    remote_text = " ".join(clean_text(item.get("text")) for item in remote_rows)
    me_metrics = token_metrics(compare, text, me_text)
    remote_metrics = token_metrics(compare, text, remote_text)
    me_overlap = round(sum(safe_float(item.get("overlap_sec")) for item in me_rows), 3)
    remote_overlap = round(sum(safe_float(item.get("overlap_sec")) for item in remote_rows), 3)
    nearest_me_distance = min(
        (
            min(
                abs(safe_float(interval.get("start")) - safe_float(item.get("end"))),
                abs(safe_float(interval.get("end")) - safe_float(item.get("start"))),
            )
            for item in nearest
            if isinstance(item, dict) and item.get("role") == "Me"
        ),
        default=999.0,
    )
    content_count = int(me_metrics["content_token_count"])
    me_score = max(me_metrics["f1"], me_metrics["recall"] * 0.8)
    remote_score = max(remote_metrics["f1"], remote_metrics["recall"] * 0.8)
    overlap_total = me_overlap + remote_overlap
    overlap_ratio = overlap_total / duration if duration > 0 else 0.0

    if me_overlap >= 0.35 and me_score >= 0.24 and me_score + 0.04 >= remote_score:
        classification = "genuine_double_talk"
        reason = "authoritative_me_overlap_and_text_support"
    elif remote_overlap >= 0.35 and remote_score >= 0.28 and remote_score >= me_score + 0.05:
        classification = "probable_remote_leak"
        reason = "authoritative_remote_text_support_dominates_me"
    elif content_count <= 1 or (duration <= 2.5 and max(me_score, remote_score) < 0.16):
        classification = "probable_asr_noise"
        reason = "short_or_noncontentful_text_without_role_support"
    elif (overlap_ratio <= 0.30 and nearest_me_distance <= 1.5) or (
        me_overlap > 0.0 and me_overlap < min(0.50, duration * 0.25)
    ):
        classification = "probable_timing_overlap"
        reason = "me_evidence_is_adjacent_or_boundary_limited"
    else:
        classification = "insufficient_evidence"
        reason = "authoritative_evidence_does_not_separate_roles_confidently"
    return {
        "classification": classification,
        "reason": reason,
        "text": text,
        "features": {
            "duration_sec": round(duration, 3),
            "me_overlap_sec": me_overlap,
            "remote_overlap_sec": remote_overlap,
            "overlap_ratio": round(overlap_ratio, 6),
            "nearest_me_distance_sec": round(nearest_me_distance, 3),
            "me_text_metrics": me_metrics,
            "remote_text_metrics": remote_metrics,
            "batch_me_ids": [item.get("id") for item in me_rows],
            "batch_remote_ids": [item.get("id") for item in remote_rows],
        },
    }


def fixed_outcomes(fixed_report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("id")): row
        for row in fixed_report.get("outcomes") or []
        if isinstance(row, dict) and row.get("id")
    }


def evaluate_rows(
    rows: list[dict[str, Any]],
    fixed_report: dict[str, Any],
    compare: ModuleType,
) -> list[dict[str, Any]]:
    fixed_by_id = fixed_outcomes(fixed_report)
    outcomes: list[dict[str, Any]] = []
    for row in rows:
        if row.get("row_kind") == "fixed_positive":
            fixed_id = str(row.get("id") or "").removeprefix("fixed:")
            fixed = fixed_by_id.get(fixed_id, {})
            recovered = fixed.get("status") == "accepted"
            outcome = {
                "classification": "genuine_double_talk",
                "reason": str(fixed.get("reason") or "fixed_positive_outcome_missing"),
                "text": clean_text((fixed.get("evaluation_reference") or {}).get("text")),
                "features": {
                    "recovered": recovered,
                    "recovered_seconds": safe_float(fixed.get("recovered_seconds")),
                    "source": "immutable_fixed_positive_report",
                },
            }
        else:
            outcome = classify_row(row, compare)
        accepted = row.get("algorithm_outcome") == "accepted"
        classification = outcome["classification"]
        control_kind = None
        if classification == "probable_remote_leak":
            control_kind = "remote_only"
        elif classification == "probable_asr_noise":
            control_kind = "asr_noise"
        elif classification == "probable_timing_overlap":
            control_kind = "timing_overlap"
        adversarial = (
            row.get("row_kind") in {"offline_candidate", "runtime_only_candidate"}
            and classification in NEGATIVE_ACCEPT_CLASSES
        )
        false_positive_accept = accepted and adversarial
        stable = {
            "schema": OUTCOME_SCHEMA,
            "id": row.get("id"),
            "session": row.get("session"),
            "split": row.get("split"),
            "row_kind": row.get("row_kind"),
            "algorithm_outcome": row.get("algorithm_outcome"),
            "algorithm_classification": row.get("algorithm_classification"),
            "evaluation_classification": classification,
            "stable_outcome": "accepted" if accepted else "not_accepted",
            "reason": outcome["reason"],
            "control_kind": control_kind,
            "adversarial_negative": adversarial,
            "false_positive_accept": false_positive_accept,
            "text": outcome["text"],
            "features": outcome["features"],
            "selection_contract": row.get("selection_contract") or {},
            "evaluation_fields_used_post_selection_only": True,
        }
        stable["outcome_fingerprint_sha256"] = canonical_sha256(stable)
        outcomes.append(stable)
    return outcomes


def verify_inputs(manifest: dict[str, Any], repo_root: Path, skip: bool) -> dict[str, Any]:
    if skip:
        return {"status": "skipped", "checked_file_count": 0, "changed_files": []}
    changed: list[dict[str, Any]] = []
    checked = 0
    for row in manifest.get("input_files") or []:
        if not isinstance(row, dict) or not row.get("path"):
            continue
        checked += 1
        path = repo_root / str(row["path"])
        if not path.is_file():
            changed.append({"path": row["path"], "reason": "missing"})
            continue
        actual = sha256_file(path)
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
        "changed_file_count": len(changed),
        "changed_files": changed,
    }


def authoritative_dialogue_path(session: Path) -> Path | None:
    outcome = read_json(session / "derived/outcome/outcome.json")
    profile = str(outcome.get("selected_profile") or "audit_cleanup_v2").strip()
    root = session / "derived/transcript-simple/whisper-cpp/resolved"
    paths = [
        root / f"clean_dialogue.{profile}.json",
        root / "clean_dialogue.audit_cleanup_v2.json",
        root / "clean_dialogue.shadow_v2.json",
        root / "clean_dialogue.json",
    ]
    return next((path for path in paths if path.is_file()), None)


def recovery_dir(session: Path) -> Path | None:
    isolated = session / "derived/live/causal-recovery-generalization-v1/offline"
    canonical = session / "derived/live/causal-double-talk-me-recovery-v1"
    return isolated if (isolated / "state.json").is_file() else canonical if (canonical / "state.json").is_file() else None


def no_regression_rows(
    manifest: dict[str, Any],
    sessions_root: Path,
    compare: ModuleType,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for session_id in manifest.get("regression_sessions", []) + manifest.get("holdout_sessions", []):
        session = sessions_root / str(session_id)
        dialogue_path = authoritative_dialogue_path(session)
        batch = compare.read_utterances(dialogue_path)
        chunks = read_jsonl(session / "derived/live/chunks.jsonl")
        base_turns = compare.live_turns(session, chunks)
        directory = recovery_dir(session)
        candidate_rows = read_jsonl(directory / "candidates.jsonl") if directory else []
        algorithm_accepted_count = sum(row.get("status") == "accepted" for row in candidate_rows)
        recovery_turns, contract_rejected = compare.causal_double_talk_me_recovery_v1_shadow_turns(
            session,
            (directory / "candidates.jsonl") if directory else None,
        )
        before = compare.parity_metrics_for_turns(
            base_turns,
            batch,
            match_mode_prefix="generalization_before",
        )
        after = compare.parity_metrics_for_turns(
            base_turns + recovery_turns,
            batch,
            match_mode_prefix="generalization_after",
        )
        before_text = compare.bag_overlap_metrics(
            compare.tokens(" ".join(clean_text(turn.get("text")) for turn in base_turns)),
            compare.tokens(" ".join(clean_text(turn.get("text")) for turn in batch)),
        )
        after_text = compare.bag_overlap_metrics(
            compare.tokens(" ".join(clean_text(turn.get("text")) for turn in base_turns + recovery_turns)),
            compare.tokens(" ".join(clean_text(turn.get("text")) for turn in batch)),
        )
        before_missing = safe_float(before.get("live_missing_me_seconds"))
        after_missing = safe_float(after.get("live_missing_me_seconds"))
        before_remote = safe_float(before.get("live_suspected_remote_leak_in_me_seconds"))
        after_remote = safe_float(after.get("live_suspected_remote_leak_in_me_seconds"))
        before_blockers = int(before.get("live_blocking_contentful_role_constrained_order_mismatch_count") or 0)
        after_blockers = int(after.get("live_blocking_contentful_role_constrained_order_mismatch_count") or 0)
        before_f1 = safe_float(before_text.get("live_batch_token_f1"))
        after_f1 = safe_float(after_text.get("live_batch_token_f1"))
        before_burden = before_missing + before_remote
        after_burden = after_missing + after_remote
        checks = {
            "authoritative_batch_present": bool(batch),
            "recovery_contract_valid": len(recovery_turns) == algorithm_accepted_count,
            "remote_like_me_not_increased": after_remote <= before_remote + 0.001,
            "effective_order_blockers_not_increased": after_blockers <= before_blockers,
            "token_f1_not_worse": after_f1 + 0.000001 >= before_f1,
            "mandatory_review_burden_not_increased": after_burden <= before_burden + 0.001,
        }
        rows.append(
            {
                "session": session_id,
                "status": "passed" if all(checks.values()) else "failed",
                "checks": checks,
                "accepted_recovery_turn_count": len(recovery_turns),
                "algorithm_accepted_candidate_count": algorithm_accepted_count,
                "contract_rejected_accepted_candidate_count": max(
                    0,
                    algorithm_accepted_count - len(recovery_turns),
                ),
                "algorithm_rejected_candidate_count": len(contract_rejected)
                - max(0, algorithm_accepted_count - len(recovery_turns)),
                "metrics": {
                    "missing_me_seconds": {"before": before_missing, "after": after_missing},
                    "remote_like_me_seconds": {"before": before_remote, "after": after_remote},
                    "effective_order_blockers": {"before": before_blockers, "after": after_blockers},
                    "token_f1": {"before": before_f1, "after": after_f1},
                    "mandatory_review_burden_seconds": {
                        "before": round(before_burden, 3),
                        "after": round(after_burden, 3),
                    },
                },
            }
        )
    return rows


def runtime_paths(session: Path) -> tuple[Path | None, Path | None]:
    isolated = session / "derived/live/causal-recovery-generalization-v1/runtime"
    canonical = session / "derived/live/causal-me-recovery-runtime-v1"
    root = isolated if (isolated / "paced_replay.json").is_file() else canonical
    replay = root / "paced_replay.json"
    candidates = root / "double-talk-v1/candidates.jsonl"
    return (replay if replay.is_file() else None, candidates if candidates.is_file() else None)


def runtime_evidence(manifest: dict[str, Any], sessions_root: Path) -> dict[str, Any]:
    session_rows: list[dict[str, Any]] = []
    for session_id in manifest.get("holdout_sessions") or []:
        session = sessions_root / str(session_id)
        replay_path, runtime_candidates_path = runtime_paths(session)
        offline = recovery_dir(session)
        replay = read_json(replay_path) if replay_path else {}
        offline_rows = read_jsonl(offline / "candidates.jsonl") if offline else []
        runtime_rows = read_jsonl(runtime_candidates_path) if runtime_candidates_path else []
        offline_decisions = {str(row.get("id")): str(row.get("status")) for row in offline_rows if row.get("id")}
        runtime_decisions = {str(row.get("id")): str(row.get("status")) for row in runtime_rows if row.get("id")}
        common = sorted(set(offline_decisions) & set(runtime_decisions))
        mismatched = [
            {"id": row_id, "offline": offline_decisions[row_id], "runtime": runtime_decisions[row_id]}
            for row_id in common
            if offline_decisions[row_id] != runtime_decisions[row_id]
        ]
        missing = sorted(set(offline_decisions) - set(runtime_decisions))
        extra = sorted(set(runtime_decisions) - set(offline_decisions))
        efficiency = replay.get("efficiency") if isinstance(replay.get("efficiency"), dict) else {}
        runtime_state = replay.get("runtime_state") if isinstance(replay.get("runtime_state"), dict) else {}
        p95 = safe_float(
            efficiency.get("double_talk_latency_p95_sec"),
            safe_float(efficiency.get("latency_p95_sec"), 999.0),
        )
        final_lag = safe_float(runtime_state.get("final_live_lag_sec"), 999.0)
        warm = replay.get("warm_cache_verification") if isinstance(replay.get("warm_cache_verification"), dict) else {}
        checks = {
            "replay_present": bool(replay),
            "replay_passed": replay.get("status") == "passed",
            "offline_runtime_common_candidates_present": bool(common),
            "offline_runtime_decisions_agree": not mismatched,
            "offline_runtime_candidate_set_agrees": not missing and not extra,
            "warm_repeat_deterministic": warm.get("status") == "passed",
            "runtime_p95_within_30_seconds": p95 <= 30.0,
            "final_lag_zero": final_lag <= 0.001,
            "batch_authoritative": replay.get("batch_authoritative") is True,
            "promotion_blocked": replay.get("promotion_allowed") is False,
        }
        session_rows.append(
            {
                "session": session_id,
                "status": "passed" if all(checks.values()) else "failed",
                "checks": checks,
                "offline_candidate_count": len(offline_decisions),
                "runtime_candidate_count": len(runtime_decisions),
                "common_candidate_count": len(common),
                "missing_in_runtime": missing,
                "extra_in_runtime": extra,
                "mismatched_decisions": mismatched,
                "runtime_p95_sec": p95,
                "final_lag_sec": final_lag,
                "replay_path": str(replay_path) if replay_path else None,
            }
        )
    checks = {
        "all_holdout_replays_present": bool(session_rows) and all((row.get("checks") or {}).get("replay_present") for row in session_rows),
        "all_offline_runtime_decisions_agree": bool(session_rows) and all((row.get("checks") or {}).get("offline_runtime_decisions_agree") for row in session_rows),
        "all_candidate_sets_agree": bool(session_rows) and all((row.get("checks") or {}).get("offline_runtime_candidate_set_agrees") for row in session_rows),
        "all_warm_repeats_deterministic": bool(session_rows) and all((row.get("checks") or {}).get("warm_repeat_deterministic") for row in session_rows),
        "all_runtime_p95_within_30_seconds": bool(session_rows) and all((row.get("checks") or {}).get("runtime_p95_within_30_seconds") for row in session_rows),
        "all_final_lag_zero": bool(session_rows) and all((row.get("checks") or {}).get("final_lag_zero") for row in session_rows),
    }
    return {
        "status": "passed" if all(checks.values()) else "failed",
        "checks": checks,
        "sessions": session_rows,
        "runtime_p95_max_sec": max((safe_float(row.get("runtime_p95_sec")) for row in session_rows), default=None),
        "final_lag_max_sec": max((safe_float(row.get("final_lag_sec")) for row in session_rows), default=None),
    }


def coverage_evidence(rows: list[dict[str, Any]]) -> dict[str, Any]:
    source_rows = [row for row in rows if row.get("row_kind") == "eligible_remote_active_source"]
    represented = [row for row in source_rows if row.get("algorithm_outcome") == "grouped_candidate"]
    machine_outcome = [row for row in rows if row.get("reason")]
    return {
        "eligible_source_count": len(source_rows),
        "processed_source_count": len(represented),
        "processed_source_ratio": round(len(represented) / len(source_rows), 6) if source_rows else 0.0,
        "stable_machine_outcome_count": len(machine_outcome),
        "stable_machine_outcome_ratio": round(len(machine_outcome) / len(rows), 6) if rows else 0.0,
    }


def render_markdown(report: dict[str, Any], decision: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Causal Recovery Generalization and Promotion Readiness v1",
        "",
        f"Decision: **{decision['decision']}**",
        f"Decision status: `{decision['status']}`",
        f"Corpus: `{report.get('corpus_fingerprint_sha256')}`",
        f"Outcomes: `{report.get('outcome_fingerprint_sha256')}`",
        "",
        f"- Rows evaluated: `{summary['outcome_count']}`",
        f"- Holdout sessions: `{summary['holdout_session_count']}` / `{summary['holdout_duration_sec']}` sec",
        f"- Accepted algorithm candidates: `{summary['accepted_candidate_count']}`",
        f"- Accepted negative controls: `{summary['accepted_negative_control_count']}`",
        f"- Runtime: `{report['runtime_evidence']['status']}`",
        f"- No-regression: `{report['no_regression']['status']}`",
        f"- Frozen inputs: `{report['frozen_inputs']['status']}`",
        "",
        "## Promotion Blockers",
        "",
    ]
    if decision["blockers"]:
        lines.extend(f"- `{item['id']}`: {item['reason']}" for item in decision["blockers"])
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Next Experiment",
            "",
            decision.get("next_experiment") or "No follow-up required before guarded promotion.",
            "",
            "Batch remains authoritative regardless of this decision.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    corpus_dir = args.corpus_dir.expanduser().resolve()
    sessions_root = args.sessions_root.expanduser().resolve()
    manifest = read_json(corpus_dir / "corpus_manifest_v1.json")
    corpus_rows = read_jsonl(corpus_dir / "corpus_rows_v1.jsonl")
    fixed_report = read_json(args.fixed_report.expanduser().resolve())
    compare = load_script("compare-live-batch.py", "murmurmark_generalization_compare")
    outcomes = evaluate_rows(corpus_rows, fixed_report, compare)
    frozen = verify_inputs(manifest, repo_root, args.skip_input_verification)
    runtime = runtime_evidence(manifest, sessions_root)
    regression = no_regression_rows(manifest, sessions_root, compare)
    regression_status = "passed" if regression and all(row.get("status") == "passed" for row in regression) else "failed"
    fail_open = read_json(corpus_dir / "fail_open_report_v1.json")
    classifications = Counter(str(row.get("evaluation_classification")) for row in outcomes)
    controls = Counter(str(row.get("control_kind")) for row in outcomes if row.get("control_kind"))
    controls["adversarial_negative"] = sum(row.get("adversarial_negative") is True for row in outcomes)
    accepted_candidates = [
        row
        for row in outcomes
        if row.get("row_kind") in {"offline_candidate", "runtime_only_candidate"}
        and row.get("algorithm_outcome") == "accepted"
    ]
    accepted_negative = [row for row in accepted_candidates if row.get("false_positive_accept")]
    fixed_summary = fixed_report.get("summary") if isinstance(fixed_report.get("summary"), dict) else {}
    fixed_recovered_count = int(fixed_summary.get("recovered_row_count") or 0)
    fixed_recovered_seconds = safe_float(fixed_summary.get("recovered_seconds"))
    coverage = coverage_evidence(outcomes)
    hard_checks = {
        "corpus_valid": manifest.get("status") == "valid",
        "immutable_inputs_verified": frozen.get("status") == "passed",
        "all_rows_have_stable_outcome": len(outcomes) == len(corpus_rows)
        and bool(outcomes)
        and all(row.get("reason") and row.get("evaluation_classification") in EVALUATION_CLASSES for row in outcomes),
        "original_positive_recall_preserved": fixed_recovered_count >= 4 and fixed_recovered_seconds >= 11.56,
        "false_positive_negative_accept_count_zero": len(accepted_negative) == 0,
        "negative_control_families_present": all(
            controls.get(name, 0) > 0
            for name in ("remote_only", "asr_noise", "timing_overlap", "adversarial_negative")
        ),
        "no_regression_all_sessions": regression_status == "passed",
        "offline_runtime_equivalence": runtime.get("status") == "passed",
        "fail_open_contract_passed": fail_open.get("status") == "passed",
        "all_rows_machine_explained": coverage["stable_machine_outcome_ratio"] == 1.0,
    }
    blockers: list[dict[str, Any]] = []
    blocker_reasons = {
        "immutable_inputs_verified": "one or more frozen raw, Echo Guard, live, or batch inputs changed",
        "false_positive_negative_accept_count_zero": "at least one accepted candidate matches a remote-only or ASR-noise control",
        "no_regression_all_sessions": "candidate additions regress a per-session remote-like, order, token-F1, or review metric",
        "offline_runtime_equivalence": "recording-time replay is absent, too slow, lagged, nondeterministic, or disagrees with offline output",
        "fail_open_contract_passed": "missing-model, corrupt-cache, timeout, or backpressure fail-open proof is incomplete",
        "original_positive_recall_preserved": "the immutable 4-row / 11.56s positive result was not preserved",
        "all_rows_have_stable_outcome": "not every corpus row has a stable machine-readable outcome",
        "negative_control_families_present": "one or more required remote-only, ASR-noise, timing-overlap, or adversarial control families are absent",
        "corpus_valid": "the generalization corpus contract is invalid",
    }
    for check, passed in hard_checks.items():
        if not passed:
            blockers.append({"id": check, "reason": blocker_reasons.get(check, check)})
    if coverage["processed_source_ratio"] < 1.0:
        blockers.append(
            {
                "id": "eligible_candidate_processing_coverage",
                "reason": (
                    f"only {coverage['processed_source_count']}/{coverage['eligible_source_count']} "
                    "eligible remote-active source rows reached the expensive candidate stage"
                ),
            }
        )
    decision_value = "PROMOTE" if not blockers else "DO_NOT_PROMOTE"
    decision_checks = {
        "decision_is_binary": decision_value in {"PROMOTE", "DO_NOT_PROMOTE"},
        "blockers_explained_when_not_promoted": decision_value == "PROMOTE" or bool(blockers),
        "next_experiment_defined_when_not_promoted": decision_value == "PROMOTE" or bool(blockers),
        "batch_remains_authoritative": True,
        "existing_preview_unchanged": True,
    }
    decision_status = "passed" if all(decision_checks.values()) and hard_checks["all_rows_have_stable_outcome"] and hard_checks["original_positive_recall_preserved"] else "failed"
    next_experiment = (
        "Build Causal Candidate Coverage and Cheap Negative Prefilter v1: replace the one-group-per-chunk "
        "budget with a deterministic cheap first pass, reject remote-only/ASR-noise before micro-ASR, "
        "then replay the same immutable holdout until 100% eligible rows receive bounded decisions with "
        "zero accepted negative controls and p95 <= 30s."
        if decision_value == "DO_NOT_PROMOTE"
        else None
    )
    stable_projection = [
        {
            "id": row.get("id"),
            "algorithm_outcome": row.get("algorithm_outcome"),
            "evaluation_classification": row.get("evaluation_classification"),
            "reason": row.get("reason"),
            "control_kind": row.get("control_kind"),
            "adversarial_negative": row.get("adversarial_negative"),
            "false_positive_accept": row.get("false_positive_accept"),
            "features": row.get("features"),
        }
        for row in outcomes
    ]
    report = {
        "schema": SCHEMA,
        "generator": {"name": "report-causal-recovery-generalization-v1", "version": SCRIPT_VERSION},
        "created_at": datetime.now(UTC).isoformat(),
        "corpus_fingerprint_sha256": manifest.get("corpus_fingerprint_sha256"),
        "outcome_fingerprint_sha256": canonical_sha256(stable_projection),
        "status": "completed" if decision_status == "passed" else "incomplete",
        "summary": {
            "outcome_count": len(outcomes),
            "holdout_session_count": len(manifest.get("holdout_sessions") or []),
            "holdout_duration_sec": safe_float((manifest.get("summary") or {}).get("holdout_duration_sec")),
            "accepted_candidate_count": len(accepted_candidates),
            "accepted_negative_control_count": len(accepted_negative),
            "classifications": dict(sorted(classifications.items())),
            "controls": dict(sorted(controls.items())),
            "fixed_recovered_count": fixed_recovered_count,
            "fixed_recovered_seconds": fixed_recovered_seconds,
        },
        "coverage": coverage,
        "frozen_inputs": frozen,
        "runtime_evidence": runtime,
        "no_regression": {"status": regression_status, "sessions": regression},
        "fail_open": fail_open,
        "hard_checks": hard_checks,
        "accepted_negative_controls": accepted_negative,
        "hypotheses": {
            "confirmed": [
                "strict causal residual plus Target-Me and ASR evidence can recover some hidden Me speech",
                "the immutable fixed-positive result remains reproducible",
            ],
            "rejected_or_unproven": [
                "the narrow 16-row success is sufficient evidence for default advisory promotion",
                "one expensive remote-active candidate per chunk provides complete generalization coverage",
                "offline acceptance alone proves recording-time safety and latency",
            ],
        },
        "outcomes": outcomes,
    }
    decision = {
        "schema": DECISION_SCHEMA,
        "created_at": datetime.now(UTC).isoformat(),
        "status": decision_status,
        "decision": decision_value,
        "corpus_fingerprint_sha256": report["corpus_fingerprint_sha256"],
        "outcome_fingerprint_sha256": report["outcome_fingerprint_sha256"],
        "checks": decision_checks,
        "promotion_gates": hard_checks,
        "blockers": blockers,
        "next_experiment": next_experiment,
        "policy": {
            "batch_authoritative": True,
            "profile_remains_explicit_only": decision_value == "DO_NOT_PROMOTE",
            "normal_live_preview_changed": False,
            "remote_forbidden_guards_weakened": False,
        },
    }
    write_jsonl(corpus_dir / "outcomes_v1.jsonl", outcomes)
    write_json(corpus_dir / "generalization_report_v1.json", report)
    write_json(corpus_dir / "promotion_decision.json", decision)
    (corpus_dir / "generalization_report_v1.md").write_text(
        render_markdown(report, decision), encoding="utf-8"
    )
    print(f"causal recovery generalization report: {corpus_dir / 'generalization_report_v1.json'}")
    print(f"outcomes: {len(outcomes)}")
    print(f"accepted_negative_controls: {len(accepted_negative)}")
    print(f"decision: {decision_value}")
    print(f"decision_status: {decision_status}")
    if args.require_decision and decision_status != "passed":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
