#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = "murmurmark.live_local_recall_remote_leakage_hardening/v1"
ROW_SCHEMA = "murmurmark.live_local_recall_blocker_disposition/v1"
SCRIPT_VERSION = "1.0.0"
BASELINE_PROFILE = (
    "online_live_me_remote_overlap_filter_live_boundary_split_retime_causal_speaker_only_v1"
)
CANDIDATE_PROFILE = (
    "online_live_me_remote_overlap_filter_live_boundary_split_retime_causal_remote_energy_v1"
)
DISPOSITIONS = {
    "partial_safe_tail",
    "remote_free_local_island",
    "mixed_double_talk",
    "duplicate_context",
    "unsupported",
    "unresolved",
}
MATERIAL_BLOCKER_RECOVERY_SECONDS = 10.0
EPSILON = 1.0e-6


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Report causal live local-recall recovery and per-session no-regression gates."
    )
    parser.add_argument("--sessions-root", type=Path, default=Path("sessions"))
    parser.add_argument(
        "--scope-report",
        type=Path,
        default=Path("sessions/_reports/live-pipeline/live_order_role_reconciliation_v1.json"),
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("sessions/_reports/live-pipeline"),
    )
    parser.add_argument("--baseline-profile", default=BASELINE_PROFILE)
    parser.add_argument("--candidate-profile", default=CANDIDATE_PROFILE)
    parser.add_argument(
        "--allow-missing-scope",
        action="store_true",
        help="return success without outputs when the seven-session candidate scope is unavailable",
    )
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


def interval_overlap(start: float, end: float, other_start: float, other_end: float) -> float:
    return max(0.0, min(end, other_end) - max(start, other_start))


def stable_row_id(session: str, batch_id: str, start: float, end: float) -> str:
    raw = f"{session}\0{batch_id}\0{start:.3f}\0{end:.3f}".encode("utf-8")
    return f"llr_{hashlib.sha256(raw).hexdigest()[:16]}"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def input_manifest(session: Path) -> list[dict[str, Any]]:
    paths = sorted((session / "audio/mic").glob("*.caf"))
    paths += sorted((session / "audio/remote").glob("*.caf"))
    resolved = session / "derived/transcript-simple/whisper-cpp/resolved"
    paths += sorted(resolved.glob("clean_dialogue*.json"))
    return [
        {
            "path": str(path.relative_to(session)),
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        }
        for path in paths
        if path.is_file()
    ]


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


def causal_candidate_contract(turn: dict[str, Any]) -> dict[str, Any]:
    selection = turn.get("selection_features") if isinstance(turn.get("selection_features"), dict) else {}
    localization = (
        selection.get("remote_free_localization")
        if isinstance(selection.get("remote_free_localization"), dict)
        else {}
    )
    speaker_scores = selection.get("speaker_scores") if isinstance(selection.get("speaker_scores"), dict) else {}
    remote_guard = turn.get("remote_audio_guard") if isinstance(turn.get("remote_audio_guard"), dict) else {}
    checks = {
        "role_is_me": turn.get("role") == "Me",
        "timeline_causal": turn.get("timeline_causal") is True,
        "selection_does_not_use_batch": turn.get("used_batch_fields_for_selection") is False,
        "remote_audio_guard_passed": remote_guard.get("status") == "passed",
        "remote_free_localization": localization.get("reason")
        in {"no_remote_overlap", "past_target_voice_in_remote_free_gap"},
        "causal_local_speaker_evidence_present": bool(speaker_scores),
        "text_present": bool(str(turn.get("text") or "").strip()),
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "remote_audio_guard": remote_guard,
        "remote_free_localization": localization,
        "speaker_scores": speaker_scores,
    }


def suppressed_evidence_for_gap(
    gap: dict[str, Any],
    suppressed_segments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    start = safe_float(gap.get("start"))
    end = safe_float(gap.get("end"), start)
    rows: list[dict[str, Any]] = []
    for segment in suppressed_segments:
        segment_start = safe_float(segment.get("start"))
        segment_end = safe_float(segment.get("end"), segment_start)
        overlap = interval_overlap(start, end, segment_start, segment_end)
        if overlap <= 0.0:
            continue
        rows.append(
            {
                "chunk_index": segment.get("chunk_index"),
                "start": segment.get("start"),
                "end": segment.get("end"),
                "overlap_sec": round(overlap, 3),
                "text": segment.get("text"),
                "batch_role_label": segment.get("batch_role_label"),
                "known_hallucination": bool(segment.get("known_hallucination")),
                "segment_gate_status": segment.get("segment_gate_status"),
                "segment_gate_reason": segment.get("segment_gate_reason"),
                "segment_gate_unique_token_count": segment.get("segment_gate_unique_token_count"),
                "segment_gate_mic_token_recall_in_overlapping_remote": segment.get(
                    "segment_gate_mic_token_recall_in_overlapping_remote"
                ),
                "segment_gate_overlapping_remote_token_recall_in_mic": segment.get(
                    "segment_gate_overlapping_remote_token_recall_in_mic"
                ),
                "audio_mic_minus_remote_rms_db": segment.get("audio_mic_minus_remote_rms_db"),
                "audio_mic_remote_zero_lag_abs_corr": segment.get(
                    "audio_mic_remote_zero_lag_abs_corr"
                ),
                "rescue_policy_candidates": segment.get("rescue_policy_candidates") or [],
            }
        )
    return sorted(rows, key=lambda row: (-safe_float(row.get("overlap_sec")), safe_float(row.get("start"))))


def candidate_evidence_for_gap(
    gap: dict[str, Any],
    candidate_turns: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    start = safe_float(gap.get("start"))
    end = safe_float(gap.get("end"), start)
    rows: list[dict[str, Any]] = []
    for turn in candidate_turns:
        turn_start = safe_float(turn.get("start"))
        turn_end = safe_float(turn.get("end"), turn_start)
        overlap = interval_overlap(start, end, turn_start, turn_end)
        if overlap <= 0.0:
            continue
        contract = causal_candidate_contract(turn)
        rows.append(
            {
                "id": turn.get("id"),
                "start": turn.get("start"),
                "end": turn.get("end"),
                "overlap_sec": round(overlap, 3),
                "text": turn.get("text"),
                "source": turn.get("source"),
                "chunk_index": turn.get("chunk_index"),
                "contract": contract,
            }
        )
    return sorted(rows, key=lambda row: (-safe_float(row.get("overlap_sec")), safe_float(row.get("start"))))


def evidence_seconds(rows: list[dict[str, Any]], labels: set[str]) -> float:
    return round(
        sum(
            safe_float(row.get("overlap_sec"))
            for row in rows
            if str(row.get("batch_role_label") or "") in labels
        ),
        3,
    )


def classify_disposition(
    category: str,
    duration: float,
    suppressed: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
) -> tuple[str, str]:
    candidate_seconds = round(sum(safe_float(row.get("overlap_sec")) for row in candidates), 3)
    candidate_ratio = candidate_seconds / duration if duration > 0.0 else 0.0
    local_seconds = evidence_seconds(suppressed, {"me_dominant", "mixed"})
    remote_seconds = evidence_seconds(
        suppressed,
        {"remote_dominant", "none", "known_hallucination"},
    )
    duplicate_seconds = round(
        sum(
            safe_float(row.get("overlap_sec"))
            for row in suppressed
            if row.get("segment_gate_reason") == "segment_duplicates_overlapping_remote"
        ),
        3,
    )
    if candidates and all((row.get("contract") or {}).get("passed") for row in candidates):
        if candidate_ratio >= 0.75:
            return "remote_free_local_island", "causal remote-free candidate covers most of the gap"
        return "partial_safe_tail", "causal remote-free candidate safely recovers part of the gap"
    if category == "local_missing_suspicious_batch_me":
        return "duplicate_context", "the batch Me reference is suspicious and crosses remote context"
    if local_seconds > 0.0 and duplicate_seconds >= max(0.5, local_seconds * 0.5):
        return "mixed_double_talk", "local evidence is entangled with duplicated remote text"
    if local_seconds > 0.0 and remote_seconds > 0.0:
        return "mixed_double_talk", "both local and remote-risk evidence overlap the gap"
    if remote_seconds > 0.0 or duplicate_seconds > 0.0:
        return "duplicate_context", "available evidence is remote-dominant or duplicate context"
    if local_seconds > 0.0:
        return "unresolved", "local-looking evidence lacks a complete causal publication contract"
    return "unsupported", "no causal local evidence can support live publication"


def session_profile_report(
    session: Path,
    baseline_policy: str,
    candidate_policy: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    comparison_path = session / "derived/live/live_batch_comparison.json"
    comparison = read_json(comparison_path)
    profiles = (
        ((comparison.get("shadow_profiles") or {}).get("target_me") or {})
        if isinstance(comparison.get("shadow_profiles"), dict)
        else {}
    )
    baseline = profiles.get(baseline_policy) if isinstance(profiles.get(baseline_policy), dict) else {}
    candidate = profiles.get(candidate_policy) if isinstance(profiles.get(candidate_policy), dict) else {}
    baseline_metrics = baseline.get("metrics") if isinstance(baseline.get("metrics"), dict) else {}
    candidate_metrics = candidate.get("metrics") if isinstance(candidate.get("metrics"), dict) else {}
    draft = resolve_draft(session, candidate)
    draft_turns = draft.get("turns") if isinstance(draft.get("turns"), list) else []
    candidate_turns = [
        turn
        for turn in draft_turns
        if isinstance(turn, dict)
        and turn.get("shadow_added") is True
        and turn.get("shadow_policy") == candidate_policy
    ]
    causal_rows = {
        str(row.get("id") or ""): row
        for row in read_jsonl(session / "derived/live/causal-target-me/candidates.jsonl")
    }
    for turn in candidate_turns:
        causal = causal_rows.get(str(turn.get("id") or "")) or {}
        turn.setdefault("timeline_causal", causal.get("timeline_causal"))
        turn.setdefault("used_batch_fields_for_selection", causal.get("used_batch_fields_for_selection"))
        turn.setdefault(
            "selection_features",
            {
                "enrollment": causal.get("enrollment"),
                "speaker_scores": causal.get("speaker_scores"),
                "source_text": causal.get("source_text"),
                "remote_free_localization": causal.get("remote_free_localization"),
            },
        )
    candidate_contracts = [causal_candidate_contract(turn) for turn in candidate_turns]
    baseline_review = gate_evidence(baseline, "review_burden")
    candidate_review = gate_evidence(candidate, "review_burden")
    baseline_missing = safe_float(baseline_metrics.get("live_missing_me_seconds"))
    candidate_missing = safe_float(candidate_metrics.get("live_missing_me_seconds"))
    baseline_remote = safe_float(baseline_metrics.get("live_suspected_remote_leak_in_me_seconds"))
    candidate_remote = safe_float(candidate_metrics.get("live_suspected_remote_leak_in_me_seconds"))
    baseline_f1 = safe_float(baseline_metrics.get("live_batch_token_f1"))
    candidate_f1 = safe_float(candidate_metrics.get("live_batch_token_f1"))
    candidate_order = candidate.get("order_role_reconciliation") or {}
    checks = {
        "profiles_present": bool(baseline and candidate),
        "missing_me_not_worse": candidate_missing <= baseline_missing + EPSILON,
        "remote_like_me_not_worse": candidate_remote <= baseline_remote + EPSILON,
        "effective_order_blockers_zero": safe_int(candidate_order.get("blocking_count")) == 0,
        "token_f1_not_worse": candidate_f1 + EPSILON >= baseline_f1,
        "review_burden_not_worse": safe_float(candidate_review.get("review_burden_sec"))
        <= safe_float(baseline_review.get("review_burden_sec")) + EPSILON,
        "candidate_turns_causal": all(row.get("passed") is True for row in candidate_contracts),
        "batch_authoritative": candidate.get("batch_authoritative") is True,
        "promotion_blocked": candidate.get("promotion_allowed") is False,
    }
    risk_examples = comparison.get("risk_examples") if isinstance(comparison.get("risk_examples"), dict) else {}
    suppressed_segments = [
        row for row in risk_examples.get("suppressed_mic_asr_segments") or [] if isinstance(row, dict)
    ]
    disposition_rows: list[dict[str, Any]] = []
    for category in ("local_missing", "local_missing_suspicious_batch_me"):
        for gap in risk_examples.get(category) or []:
            if not isinstance(gap, dict):
                continue
            start = safe_float(gap.get("start"))
            end = safe_float(gap.get("end"), start)
            duration = max(0.0, end - start)
            suppressed = suppressed_evidence_for_gap(gap, suppressed_segments)
            causal = candidate_evidence_for_gap(gap, candidate_turns)
            disposition, reason = classify_disposition(category, duration, suppressed, causal)
            disposition_rows.append(
                {
                    "schema": ROW_SCHEMA,
                    "id": stable_row_id(session.name, str(gap.get("batch_id") or ""), start, end),
                    "session": session.name,
                    "category": category,
                    "disposition": disposition,
                    "status": "stable" if disposition in DISPOSITIONS else "unresolved",
                    "reason": reason,
                    "publication": {
                        "status": "shadow_candidate" if causal else "blocked",
                        "authoritative": False,
                        "policy": candidate_policy,
                        "uses_batch_for_selection": False,
                    },
                    "evaluation_reference": {
                        "batch_id": gap.get("batch_id"),
                        "start": gap.get("start"),
                        "end": gap.get("end"),
                        "duration_sec": gap.get("duration_sec"),
                        "text": gap.get("text"),
                        "recall_in_live_me": gap.get("recall_in_live_me"),
                        "recall_in_suppressed_mic": gap.get("recall_in_suppressed_mic"),
                        "use": "evaluation_only",
                    },
                    "machine_evidence": {
                        "suppressed_mic": suppressed,
                        "causal_candidates": causal,
                        "causal_candidate_overlap_sec": round(
                            sum(safe_float(row.get("overlap_sec")) for row in causal),
                            3,
                        ),
                        "local_evidence_sec": evidence_seconds(suppressed, {"me_dominant", "mixed"}),
                        "remote_risk_evidence_sec": evidence_seconds(
                            suppressed,
                            {"remote_dominant", "none", "known_hallucination"},
                        ),
                    },
                }
            )
    report = {
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
                sum(max(0.0, safe_float(turn.get("end")) - safe_float(turn.get("start"))) for turn in candidate_turns),
                3,
            ),
        },
        "immutable_inputs": input_manifest(session),
    }
    errors = [name for name, passed in checks.items() if not passed]
    return report, disposition_rows, errors


def render_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# Live Local Recall and Remote Leakage Hardening v1",
        "",
        f"Status: `{report.get('status')}`",
        f"Baseline: `{report.get('baseline_profile')}`",
        f"Selected shadow: `{report.get('selected_shadow_profile')}`",
        "Batch authoritative: `true`",
        "Promotion allowed: `false`",
        "",
        "## Corpus result",
        "",
        f"- sessions: `{summary.get('session_count')}`",
        f"- blocker rows classified: `{summary.get('blocker_row_count')}`",
        f"- baseline missing Me: `{summary.get('baseline_missing_me_seconds')}s`",
        f"- candidate missing Me: `{summary.get('candidate_missing_me_seconds')}s`",
        f"- recovered Me: `{summary.get('recovered_me_seconds')}s`",
        f"- causal recovery inside the 118-row queue: `{summary.get('blocker_candidate_overlap_seconds')}s`",
        f"- largest affected session recovery inside the queue: "
        f"`{summary.get('max_session_blocker_candidate_overlap_seconds')}s`",
        f"- remote-like Me before/after: `{summary.get('baseline_remote_like_me_seconds')}s` / "
        f"`{summary.get('candidate_remote_like_me_seconds')}s`",
        f"- effective order blockers: `{summary.get('effective_order_blocker_count')}`",
        f"- per-session no-regression: `{summary.get('passing_session_count')}/{summary.get('session_count')}`",
        "",
        "## Dispositions",
        "",
    ]
    for label, values in sorted((summary.get("by_disposition") or {}).items()):
        lines.append(f"- `{label}`: `{values.get('count')}` rows / `{values.get('seconds')}s`")
    lines.extend(["", "## Sessions", ""])
    for row in report.get("sessions") or []:
        metrics = row.get("metrics") or {}
        missing = metrics.get("missing_me_seconds") or {}
        lines.append(
            f"- `{row.get('session')}`: `{row.get('status')}`, missing Me "
            f"`{missing.get('before')}s -> {missing.get('after')}s`, "
            f"effective order `{metrics.get('effective_order_blockers')}`"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "The selected profile uses recording-time causal Target-Me candidates, remote-free localization, "
            "a committed-PCM remote audio guard and local-speaker evidence. Batch text and timing are used "
            "only to score the shadow result. Ambiguous rows remain explicit and batch stays authoritative.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    scope = read_json(args.scope_report)
    session_ids = ((scope.get("scope") or {}).get("candidate_session_ids") or [])
    if len(session_ids) != 7:
        if args.allow_missing_scope:
            print(
                f"status: skipped_incomplete_scope "
                f"({len(session_ids)}/7 sessions in {args.scope_report})"
            )
            return 0
        raise SystemExit(f"expected 7 candidate sessions in {args.scope_report}, found {len(session_ids)}")
    session_reports: list[dict[str, Any]] = []
    disposition_rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for session_id in session_ids:
        session = args.sessions_root / str(session_id)
        row, dispositions, session_errors = session_profile_report(
            session,
            args.baseline_profile,
            args.candidate_profile,
        )
        session_reports.append(row)
        disposition_rows.extend(dispositions)
        if session_errors:
            errors.append({"session": session_id, "checks": session_errors})

    grouped: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "seconds": 0.0})
    for row in disposition_rows:
        label = str(row.get("disposition") or "unresolved")
        grouped[label]["count"] += 1
        grouped[label]["seconds"] = round(
            safe_float(grouped[label].get("seconds"))
            + safe_float((row.get("evaluation_reference") or {}).get("duration_sec")),
            3,
        )
    for label in DISPOSITIONS:
        grouped[label]
    baseline_missing = sum(
        safe_float(((row.get("metrics") or {}).get("missing_me_seconds") or {}).get("before"))
        for row in session_reports
    )
    candidate_missing = sum(
        safe_float(((row.get("metrics") or {}).get("missing_me_seconds") or {}).get("after"))
        for row in session_reports
    )
    baseline_remote = sum(
        safe_float(((row.get("metrics") or {}).get("remote_like_me_seconds") or {}).get("before"))
        for row in session_reports
    )
    candidate_remote = sum(
        safe_float(((row.get("metrics") or {}).get("remote_like_me_seconds") or {}).get("after"))
        for row in session_reports
    )
    recovered = round(baseline_missing - candidate_missing, 3)
    blocker_overlap_by_session: Counter[str] = Counter()
    for row in disposition_rows:
        blocker_overlap_by_session[str(row.get("session") or "")] += safe_float(
            (row.get("machine_evidence") or {}).get("causal_candidate_overlap_sec")
        )
    for row in session_reports:
        (row.get("metrics") or {})["blocker_candidate_overlap_sec"] = round(
            blocker_overlap_by_session.get(str(row.get("session") or ""), 0.0),
            3,
        )
    blocker_candidate_overlap = round(sum(blocker_overlap_by_session.values()), 3)
    max_session_blocker_overlap = round(max(blocker_overlap_by_session.values(), default=0.0), 3)
    material_recovery = (
        recovered > EPSILON
        and max_session_blocker_overlap >= MATERIAL_BLOCKER_RECOVERY_SECONDS
    )
    all_rows_stable = len(disposition_rows) == 118 and all(
        row.get("status") == "stable" and row.get("disposition") in DISPOSITIONS
        for row in disposition_rows
    )
    all_sessions_pass = len(session_reports) == 7 and all(row.get("status") == "passed" for row in session_reports)
    status = "passed_shadow_only" if all_rows_stable and all_sessions_pass and material_recovery else "failed"
    summary = {
        "session_count": len(session_reports),
        "passing_session_count": sum(1 for row in session_reports if row.get("status") == "passed"),
        "blocker_row_count": len(disposition_rows),
        "stable_disposition_count": sum(1 for row in disposition_rows if row.get("status") == "stable"),
        "baseline_missing_me_seconds": round(baseline_missing, 3),
        "candidate_missing_me_seconds": round(candidate_missing, 3),
        "recovered_me_seconds": recovered,
        "blocker_candidate_overlap_seconds": blocker_candidate_overlap,
        "max_session_blocker_candidate_overlap_seconds": max_session_blocker_overlap,
        "material_blocker_recovery_threshold_seconds": MATERIAL_BLOCKER_RECOVERY_SECONDS,
        "material_recovery_passed": material_recovery,
        "baseline_remote_like_me_seconds": round(baseline_remote, 3),
        "candidate_remote_like_me_seconds": round(candidate_remote, 3),
        "effective_order_blocker_count": sum(
            safe_int((row.get("metrics") or {}).get("effective_order_blockers"))
            for row in session_reports
        ),
        "review_burden_seconds": round(
            sum(
                safe_float(((row.get("metrics") or {}).get("review_burden_sec") or {}).get("after"))
                for row in session_reports
            ),
            3,
        ),
        "by_disposition": dict(sorted(grouped.items())),
    }
    report = {
        "schema": SCHEMA,
        "generator": {"name": "report-live-local-recall-hardening", "version": SCRIPT_VERSION},
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "baseline_profile": args.baseline_profile,
        "selected_shadow_profile": args.candidate_profile,
        "batch_authoritative": True,
        "promotion_allowed": False,
        "promotion_reason": "remaining parity gates still block promotion",
        "selection_contract": {
            "live_only": True,
            "batch_fields_used_for_selection": False,
            "batch_fields_use": "evaluation_only",
            "required_evidence": [
                "causal_chunk_timing",
                "recording_time_committed_pcm",
                "remote_free_localization",
                "remote_audio_guard",
                "causal_local_speaker_evidence",
            ],
        },
        "summary": summary,
        "sessions": session_reports,
        "errors": errors,
        "outputs": {
            "report": "live_local_recall_remote_leakage_hardening_v1.json",
            "rows": "live_local_recall_remote_leakage_hardening_v1.jsonl",
            "markdown": "live_local_recall_remote_leakage_hardening_v1.md",
        },
    }
    json_path = args.out_dir / "live_local_recall_remote_leakage_hardening_v1.json"
    jsonl_path = args.out_dir / "live_local_recall_remote_leakage_hardening_v1.jsonl"
    markdown_path = args.out_dir / "live_local_recall_remote_leakage_hardening_v1.md"
    write_json(json_path, report)
    write_jsonl(jsonl_path, disposition_rows)
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    print(f"status: {status}")
    print(f"sessions: {len(session_reports)}")
    print(f"blocker_rows: {len(disposition_rows)}")
    print(f"recovered_me_seconds: {recovered}")
    print(f"report: {json_path}")
    return 0 if status == "passed_shadow_only" else 1


if __name__ == "__main__":
    raise SystemExit(main())
