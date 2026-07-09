#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = "murmurmark.live_corpus_gates_report/v1"
SCRIPT_VERSION = "1.27.0"
REAL_SESSION_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}$")
DEFAULT_TARGET_LIVE_SESSIONS = 3
DEFAULT_TARGET_MEANINGFUL_COMPARED_SESSIONS = 3
DEFAULT_TARGET_PASSING_COMPARED_SESSIONS = 3
SUPPRESSED_MIC_RESCUE_POLICIES = (
    "current_text_segment_gate",
    "strict_text_unique_v1",
    "remote_silent_text_v1",
    "audio_remote_quiet_v1",
    "audio_mic_dominant_v1",
    "audio_low_coherence_v1",
    "audio_low_corr_text_guard_v1",
    "audio_safe_union_v1",
    "batch_oracle_local_ceiling",
)
LIVE_IMPLEMENTABLE_RESCUE_POLICIES = tuple(
    policy for policy in SUPPRESSED_MIC_RESCUE_POLICIES if policy != "batch_oracle_local_ceiling"
)
SUPPRESSED_MIC_RESCUE_POLICY_METRICS = (
    "selected_segment_count",
    "selected_seconds",
    "local_seconds",
    "me_dominant_seconds",
    "mixed_seconds",
    "remote_risk_seconds",
    "precision_proxy",
    "local_recall_proxy",
    "missing_me_seconds_after",
    "missing_me_recovered_seconds",
)
MATERIAL_RESCUE_LOCAL_SECONDS = 5.0
TARGET_ME_RESCUE_POLICIES = (
    "target_me_confirmed_v1",
    "target_me_confirmed_remote_guard_v1",
    "target_me_confirmed_remote_guard_timeline_safe_v1",
    "target_me_possible_v1",
    "target_me_possible_timeline_safe_v1",
)
LOCAL_ISLAND_SPLIT_ORACLE_PROFILE_POLICY = (
    "online_live_me_remote_overlap_filter_plus_target_me_possible_timeline_safe_audio_safe_union_"
    "batch_remote_forbidden_local_island_split_oracle_v1"
)
LOCAL_ISLAND_RETIME_ORACLE_PROFILE_POLICY = (
    "online_live_me_remote_overlap_filter_plus_target_me_possible_timeline_safe_audio_safe_union_"
    "batch_remote_forbidden_local_island_retime_oracle_v1"
)
STRICT_LIVE_ONLY_LOCAL_ISLAND_PROFILE_POLICY = (
    "online_live_me_remote_overlap_filter_plus_target_me_possible_timeline_safe_"
    "strict_live_only_local_island_v1"
)
STRICT_LIVE_ONLY_LOCAL_ISLAND_AUDIO_SAFE_UNION_PROFILE_POLICY = (
    "online_live_me_remote_overlap_filter_plus_target_me_possible_timeline_safe_audio_safe_union_"
    "strict_live_only_local_island_v1"
)
REMOTE_FORBIDDEN_BOUNDARY_CLASSIFIER_PROFILE_POLICY = (
    "online_live_me_remote_overlap_filter_plus_target_me_possible_timeline_safe_"
    "remote_forbidden_boundary_classifier_v1"
)
REMOTE_FORBIDDEN_RELAXED_BOUNDARY_CLASSIFIER_PROFILE_POLICY = (
    "online_live_me_remote_overlap_filter_plus_target_me_possible_timeline_safe_"
    "remote_forbidden_relaxed_boundary_classifier_v1"
)
LOCAL_SPEAKER_BOUNDARY_SHADOW_PROFILE_POLICY = (
    "online_live_me_remote_overlap_filter_plus_target_me_possible_timeline_safe_audio_safe_union_"
    "local_speaker_boundary_shadow_v1"
)
LIVE_BOUNDARY_SPLIT_RETIME_PROFILE_POLICY = (
    "online_live_me_remote_overlap_filter_plus_target_me_possible_timeline_safe_audio_safe_union_"
    "local_speaker_boundary_shadow_live_boundary_split_retime_v1"
)
REMOTE_GUARDED_VOICE_BOUNDARY_PROFILE_POLICY = (
    "online_live_me_remote_overlap_filter_plus_target_me_possible_timeline_safe_audio_safe_union_"
    "local_speaker_boundary_shadow_live_boundary_split_retime_remote_guarded_voice_boundary_v1"
)
SOFT_LOCAL_SPEAKER_BOUNDARY_SPLIT_RETIME_PROFILE_POLICY = (
    "online_live_me_remote_overlap_filter_plus_target_me_possible_timeline_safe_audio_safe_union_"
    "soft_local_speaker_boundary_shadow_live_boundary_split_retime_v1"
)
BOUNDARY_ORDER_RETIME_ORACLE_PROFILE_POLICY = (
    "online_live_me_remote_overlap_filter_plus_target_me_possible_timeline_safe_audio_safe_union_"
    "local_speaker_boundary_shadow_batch_order_boundary_retime_oracle_v1"
)
BOUNDARY_ORDER_SPLIT_RETIME_ORACLE_PROFILE_POLICY = (
    "online_live_me_remote_overlap_filter_plus_target_me_possible_timeline_safe_audio_safe_union_"
    "local_speaker_boundary_shadow_batch_order_boundary_split_retime_oracle_v1"
)
LIVE_BOUNDARY_MICRO_ASR_LAB_SHADOW_PROFILE_POLICY = (
    "online_live_me_remote_overlap_filter_plus_target_me_possible_timeline_safe_audio_safe_union_"
    "live_boundary_micro_asr_lab_shadow_v1"
)
LIVE_BOUNDARY_MICRO_ASR_LIVE_ONLY_SHADOW_PROFILE_POLICY = (
    "online_live_me_remote_overlap_filter_plus_target_me_possible_timeline_safe_audio_safe_union_"
    "live_boundary_micro_asr_live_only_shadow_v1"
)
TARGET_ME_SHADOW_PROFILE_POLICIES = (
    "target_me_confirmed_remote_guard_timeline_safe_v1",
    "target_me_possible_timeline_safe_v1",
    "target_me_confirmed_remote_guard_timeline_safe_batch_remote_forbidden_oracle_v1",
    "target_me_confirmed_remote_guard_timeline_safe_batch_remote_forbidden_online_suppressed_mic_audio_safe_union_v1",
    "target_me_confirmed_remote_guard_timeline_safe_batch_remote_forbidden_online_suppressed_mic_low_corr_text_guard_v1",
    "target_me_confirmed_remote_guard_timeline_safe_batch_remote_forbidden_visible_suppressed_mic_oracle_v1",
    "online_suppressed_mic_dual_target_remote_guard_v1",
    "online_live_me_remote_overlap_filter_v1",
    "online_live_me_remote_overlap_filter_plus_dual_target_remote_guard_v1",
    "online_live_me_remote_overlap_filter_plus_target_me_remote_guard_v1",
    "online_live_me_remote_overlap_filter_plus_target_me_remote_guard_audio_safe_union_v1",
    "online_live_me_remote_overlap_filter_plus_target_me_timeline_safe_v1",
    "online_live_me_remote_overlap_filter_plus_target_me_timeline_safe_audio_safe_union_v1",
    "online_live_me_remote_overlap_filter_plus_target_me_possible_timeline_safe_v1",
    "online_live_me_remote_overlap_filter_plus_target_me_possible_timeline_safe_audio_safe_union_v1",
    REMOTE_FORBIDDEN_BOUNDARY_CLASSIFIER_PROFILE_POLICY,
    REMOTE_FORBIDDEN_RELAXED_BOUNDARY_CLASSIFIER_PROFILE_POLICY,
    LOCAL_SPEAKER_BOUNDARY_SHADOW_PROFILE_POLICY,
    LIVE_BOUNDARY_SPLIT_RETIME_PROFILE_POLICY,
    REMOTE_GUARDED_VOICE_BOUNDARY_PROFILE_POLICY,
    LIVE_BOUNDARY_MICRO_ASR_LAB_SHADOW_PROFILE_POLICY,
    LIVE_BOUNDARY_MICRO_ASR_LIVE_ONLY_SHADOW_PROFILE_POLICY,
    SOFT_LOCAL_SPEAKER_BOUNDARY_SPLIT_RETIME_PROFILE_POLICY,
    BOUNDARY_ORDER_RETIME_ORACLE_PROFILE_POLICY,
    BOUNDARY_ORDER_SPLIT_RETIME_ORACLE_PROFILE_POLICY,
    STRICT_LIVE_ONLY_LOCAL_ISLAND_PROFILE_POLICY,
    STRICT_LIVE_ONLY_LOCAL_ISLAND_AUDIO_SAFE_UNION_PROFILE_POLICY,
    LOCAL_ISLAND_SPLIT_ORACLE_PROFILE_POLICY,
    LOCAL_ISLAND_RETIME_ORACLE_PROFILE_POLICY,
)
TARGET_ME_SHADOW_POLICY_METRICS = (
    "candidate_segment_count",
    "candidate_seconds",
    "missing_me_seconds_after",
    "missing_me_recovered_seconds",
    "suspected_remote_leak_in_me_count",
    "suspected_remote_leak_in_me_seconds",
    "order_mismatch_count",
    "role_constrained_order_mismatch_count",
    "contentful_role_constrained_order_mismatch_count",
    "batch_interval_overlap_order_ambiguity_count",
    "role_constrained_batch_interval_overlap_order_ambiguity_count",
    "contentful_role_constrained_batch_interval_overlap_order_ambiguity_count",
    "order_mismatch_delta_count",
    "role_constrained_order_mismatch_delta_count",
    "contentful_role_constrained_order_mismatch_delta_count",
    "rejected_candidate_count",
    "rejected_candidate_seconds",
    "rejected_would_add_contentful_order_mismatch_count",
    "rejected_would_add_contentful_order_mismatch_seconds",
    "rejected_would_add_suspected_remote_leak_count",
    "rejected_would_add_suspected_remote_leak_seconds",
)
TARGET_ME_SHADOW_PROFILE_METRICS = (
    "all_parity_gates_passed",
    "non_passing_gate_count",
    "live_token_recall_in_batch",
    "live_turn_count",
    "live_me_turn_count",
    "live_remote_turn_count",
    "live_order_mismatch_count",
    "live_role_constrained_order_mismatch_count",
    "live_contentful_role_constrained_order_mismatch_count",
    "live_batch_interval_overlap_order_ambiguity_count",
    "live_role_constrained_batch_interval_overlap_order_ambiguity_count",
    "live_contentful_role_constrained_batch_interval_overlap_order_ambiguity_count",
    "live_missing_me_utterance_count",
    "live_missing_me_seconds",
    "live_missing_me_visible_in_suppressed_mic_count",
    "live_missing_me_visible_in_suppressed_mic_seconds",
    "live_missing_me_not_visible_in_suppressed_mic_count",
    "live_missing_me_not_visible_in_suppressed_mic_seconds",
    "live_missing_me_with_target_me_candidate_count",
    "live_missing_me_with_target_me_candidate_seconds",
    "live_missing_me_without_target_me_candidate_count",
    "live_missing_me_without_target_me_candidate_seconds",
    "live_missing_me_visible_with_target_me_candidate_count",
    "live_missing_me_visible_with_target_me_candidate_seconds",
    "live_missing_me_visible_without_target_me_candidate_count",
    "live_missing_me_visible_without_target_me_candidate_seconds",
    "live_missing_me_not_visible_with_target_me_candidate_count",
    "live_missing_me_not_visible_with_target_me_candidate_seconds",
    "live_missing_me_not_visible_without_target_me_candidate_count",
    "live_missing_me_not_visible_without_target_me_candidate_seconds",
    "live_suspected_remote_leak_in_me_count",
    "live_suspected_remote_leak_in_me_seconds",
    "removed_live_turn_count",
    "removed_live_turn_seconds",
    "visible_suppressed_mic_added_turn_count",
    "visible_suppressed_mic_added_turn_seconds",
    "visible_suppressed_mic_rejected_turn_count",
    "live_boundary_micro_asr_lab_added_turn_count",
    "live_boundary_micro_asr_lab_added_turn_seconds",
    "live_boundary_micro_asr_lab_rejected_turn_count",
    "live_boundary_micro_asr_live_only_added_turn_count",
    "live_boundary_micro_asr_live_only_added_turn_seconds",
    "live_boundary_micro_asr_live_only_rejected_turn_count",
    "remote_guarded_voice_boundary_added_turn_count",
    "remote_guarded_voice_boundary_added_turn_seconds",
    "remote_guarded_voice_boundary_rejected_turn_count",
    "boundary_order_retime_oracle_turn_count",
    "boundary_order_retime_oracle_trimmed_seconds",
    "boundary_order_split_retime_oracle_turn_count",
    "boundary_order_split_retime_oracle_preserved_prefix_count",
    "boundary_order_split_retime_oracle_preserved_prefix_seconds",
)
LIVE_QUARANTINE_REASON = (
    "live pipeline is quarantined because the async live path has not yet passed capture-safety "
    "and parity gates; do not use normal production live collection, and use coverage_path to decide "
    "whether controlled Live Evidence collection is currently allowed"
)
PARITY_DIMENSIONS: dict[str, dict[str, Any]] = {
    "capture_safety": {
        "title": "Capture safety",
        "gates": ["capture_safety"],
        "promotion_required": True,
    },
    "order_risk": {
        "title": "Order risk",
        "gates": ["order_risk"],
        "promotion_required": True,
    },
    "local_recall": {
        "title": "Local recall",
        "gates": ["local_recall"],
        "promotion_required": True,
    },
    "remote_leakage": {
        "title": "Remote leakage",
        "gates": ["remote_duplicate_leak"],
        "promotion_required": True,
    },
    "review_burden": {
        "title": "Review burden",
        "gates": ["review_burden"],
        "promotion_required": True,
    },
    "selected_notes_readiness": {
        "title": "Selected notes readiness",
        "gates": ["selected_notes_readiness"],
        "promotion_required": True,
    },
    "chunk_boundary_risks": {
        "title": "Chunk-boundary risks",
        "gates": ["chunk_boundary_risks", "duplicate_chunks"],
        "promotion_required": True,
    },
    "draft_text_recall": {
        "title": "Draft text recall",
        "gates": ["live_token_recall"],
        "promotion_required": True,
    },
    "required_artifacts": {
        "title": "Required artifacts",
        "gates": ["required_artifacts", "raw_batch_authoritative"],
        "promotion_required": True,
    },
}
TRIAGE_CATEGORY_INFO: dict[str, dict[str, str]] = {
    "batch_review_required": {
        "title": "Batch review/readiness required",
        "recommended_next": (
            "finish the authoritative batch review/readiness path for this session; this is not a live-capture fix"
        ),
    },
    "capture_safety_risk": {
        "title": "Capture safety risk",
        "recommended_next": (
            "keep live quarantined for this session; follow coverage_path.recommended_next to decide "
            "whether controlled Live Evidence collection is currently allowed"
        ),
    },
    "live_local_recall_gap": {
        "title": "Live local-recall gap",
        "recommended_next": (
            "keep live quarantined for this session; collect controlled Live Evidence only when "
            "coverage_path allows it, and redesign live segmentation if new controlled evidence keeps "
            "failing local recall"
        ),
    },
    "live_remote_leakage": {
        "title": "Live remote leakage",
        "recommended_next": (
            "inspect echo/role evidence and keep live Me output blocked until remote-forbidden gates pass"
        ),
    },
    "live_draft_text_drift": {
        "title": "Live draft text drift",
        "recommended_next": "treat live draft as orientation only; keep the batch transcript authoritative",
    },
    "missing_batch_artifacts": {
        "title": "Missing batch artifacts",
        "recommended_next": "run or repair normal batch processing for this session before using it for live parity",
    },
    "missing_live_asr_artifacts": {
        "title": "Missing live ASR artifacts",
        "recommended_next": (
            "do not use this live run as promotion evidence; rerun only offline diagnostics on copied artifacts if useful"
        ),
    },
    "chunk_boundary_risk": {
        "title": "Chunk-boundary risk",
        "recommended_next": "fix chunk reconciliation and overlap dedupe before live promotion",
    },
    "order_risk": {
        "title": "Order risk",
        "recommended_next": "fix live timeline ordering/reconciliation before live promotion",
    },
    "other": {
        "title": "Other live parity blocker",
        "recommended_next": "inspect the session live_batch_comparison.json and keep live blocked",
    },
}
OBJECTIVE_DIMENSION_ACTIONS: dict[str, dict[str, str]] = {
    "capture_safety": {
        "id": "capture_safe_redesign_before_more_live_coverage",
        "title": "Prove capture-safe live segment production",
        "recommended_next": (
            "keep live quarantined for affected historical sessions; use coverage_path to decide whether "
            "controlled Live Evidence collection is currently allowed"
        ),
    },
    "required_artifacts": {
        "id": "complete_required_live_and_batch_artifacts",
        "title": "Complete required live/batch artifacts",
        "recommended_next": "repair or exclude sessions missing live chunks, batch transcript, or clean dialogue before parity use",
    },
    "local_recall": {
        "id": "fix_live_local_recall_gap",
        "title": "Fix live local recall gaps",
        "recommended_next": "inspect missing Me examples and improve live mic role/echo/boundary handling before promotion",
    },
    "remote_leakage": {
        "id": "fix_live_remote_leakage",
        "title": "Fix live remote-in-Me leakage",
        "recommended_next": "keep live Me output blocked until remote-forbidden evidence passes on live chunks",
    },
    "review_burden": {
        "id": "reduce_authoritative_batch_review_burden",
        "title": "Reduce selected batch review burden",
        "recommended_next": "finish batch review/readiness work; live cannot promote above a review-heavy authoritative baseline",
    },
    "selected_notes_readiness": {
        "id": "make_selected_batch_notes_ready",
        "title": "Make selected batch output notes-ready",
        "recommended_next": "complete authoritative batch readiness before using live parity as promotion evidence",
    },
    "chunk_boundary_risks": {
        "id": "fix_live_chunk_boundary_risks",
        "title": "Fix live chunk-boundary risks",
        "recommended_next": (
            "fix live chunk reconciliation and overlap dedupe; unresolved boundary suppressions still block promotion"
        ),
    },
    "draft_text_recall": {
        "id": "improve_live_draft_text_recall",
        "title": "Improve live draft text recall",
        "recommended_next": "treat live draft as orientation only until draft text reliably matches selected batch output",
    },
    "order_risk": {
        "id": "fix_live_order_risk",
        "title": "Fix live ordering risk",
        "recommended_next": "fix live timeline ordering/reconciliation before live promotion",
    },
}
OBJECTIVE_DIMENSION_PRIORITY = {
    "capture_safety": 0,
    "required_artifacts": 1,
    "order_risk": 2,
    "local_recall": 3,
    "remote_leakage": 4,
    "chunk_boundary_risks": 5,
    "review_burden": 6,
    "selected_notes_readiness": 7,
    "draft_text_recall": 8,
}
CAPTURE_SAFETY_BLOCKERS = {"interrupted_capture", "silent_capture", "sparse_capture"}
CAPTURE_SAFETY_WARNING_MARKERS = (
    "no screencapturekit audio samples",
    "capture produced no audio samples",
    "screencapturekit stream restarted",
    "capture finalized as partial",
    "captured audio covers only",
    "track appears silent or almost silent",
)


def dimensions_for_gate(gate_name: str) -> list[str]:
    return [
        key
        for key, spec in PARITY_DIMENSIONS.items()
        if gate_name in {str(name) for name in spec.get("gates", [])}
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aggregate near-realtime shadow parity gates over a local session corpus.")
    parser.add_argument("targets", nargs="*", help="all, latest or session paths. Default: all.")
    parser.add_argument("--sessions-root", type=Path, default=Path("sessions"))
    parser.add_argument("--out-dir", type=Path, default=Path("sessions/_reports/live-pipeline"))
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Run compare-live-batch.py for live sessions before aggregating the corpus report.",
    )
    parser.add_argument("--min-live-sessions", type=int, default=0, help="Required live sessions for strict coverage checks.")
    parser.add_argument("--min-compared-sessions", type=int, default=0, help="Required live-vs-batch compared sessions for strict coverage checks.")
    parser.add_argument(
        "--min-meaningful-compared-sessions",
        type=int,
        default=0,
        help="Required compared sessions with both Me and remote evidence in live and batch outputs.",
    )
    parser.add_argument(
        "--min-passing-compared-sessions",
        type=int,
        default=0,
        help="Required compared sessions where every live parity gate passed.",
    )
    parser.add_argument("--max-order-mismatches", type=int, default=None)
    parser.add_argument("--max-missing-me-sec", type=float, default=None)
    parser.add_argument("--max-remote-in-me-sec", type=float, default=None)
    parser.add_argument("--max-boundary-duplicates", type=int, default=None)
    parser.add_argument("--require-passing-gates", action="store_true", help="Fail strict coverage unless every live parity gate is passed.")
    parser.add_argument("--fail-on-insufficient-coverage", action="store_true")
    parser.add_argument("--fail-on-risk", action="store_true")
    parser.add_argument("--fail-on-promotion", action="store_true")
    parser.add_argument(
        "--target-live-sessions",
        type=int,
        default=DEFAULT_TARGET_LIVE_SESSIONS,
        help="Advisory coverage target for normal live-parity confidence. Does not fail unless also used through strict --min-* gates.",
    )
    parser.add_argument(
        "--target-meaningful-compared-sessions",
        type=int,
        default=DEFAULT_TARGET_MEANINGFUL_COMPARED_SESSIONS,
        help="Advisory target for meaningful live-vs-batch comparisons.",
    )
    parser.add_argument(
        "--target-passing-compared-sessions",
        type=int,
        default=DEFAULT_TARGET_PASSING_COMPARED_SESSIONS,
        help="Advisory target for passing live-vs-batch comparisons.",
    )
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def session_dirs(root: Path) -> list[Path]:
    if not root.exists():
        return []
    sessions = [path for path in root.iterdir() if path.is_dir() and (path / "session.json").exists()]
    return sorted(sessions, key=lambda path: path.name)


def resolve_targets(args: argparse.Namespace) -> list[Path]:
    root = args.sessions_root
    targets = args.targets or ["all"]
    if targets == ["all"]:
        return session_dirs(root)
    if targets == ["latest"]:
        sessions = session_dirs(root)
        return [sessions[-1]] if sessions else []
    resolved: list[Path] = []
    for target in targets:
        if target == "all":
            resolved.extend(session_dirs(root))
        elif target == "latest":
            sessions = session_dirs(root)
            if sessions:
                resolved.append(sessions[-1])
        else:
            path = Path(target)
            resolved.append(path if path.exists() else root / target)
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in resolved:
        key = path.resolve()
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def resolve_project_python() -> str:
    env_python = os.environ.get("MURMURMARK_PYTHON")
    if env_python:
        return env_python
    project_python = Path(__file__).resolve().parent.parent / ".venv/bin/python"
    if project_python.exists():
        return str(project_python)
    return sys.executable


def refresh_live_comparisons(sessions: list[Path]) -> list[dict[str, Any]]:
    script = Path(__file__).resolve().parent / "compare-live-batch.py"
    python = resolve_project_python()
    rows: list[dict[str, Any]] = []
    for session in sessions:
        live_report = session / "derived/live/live_pipeline_report.json"
        if not live_report.exists():
            continue
        command = [python, str(script), str(session)]
        result = subprocess.run(
            command,
            text=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        rows.append(
            {
                "session": str(session),
                "status": "passed" if result.returncode == 0 else "failed",
                "reason": None if result.returncode == 0 else "compare_live_batch_failed",
                "returncode": result.returncode,
                "stdout_tail": (result.stdout or "")[-2000:],
                "stderr_tail": (result.stderr or "")[-2000:],
            }
        )
    return rows


def rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def gate_rows(comparison: dict[str, Any] | None) -> list[dict[str, Any]]:
    gates = comparison.get("parity_gates") if isinstance(comparison, dict) else None
    rows = gates.get("gates") if isinstance(gates, dict) else None
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def non_passing_gate_rows(gates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [gate for gate in gates if gate.get("status") != "passed"]


def safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def safe_float(value: Any, default: Any = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        try:
            return float(default)
        except (TypeError, ValueError):
            return 0.0


def interval_overlap(start_a: float, end_a: float, start_b: float, end_b: float) -> float:
    return max(0.0, min(end_a, end_b) - max(start_a, start_b))


def optional_finite_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def text_tokens(text: str) -> list[str]:
    return [token for token in re.findall(r"[\w']+", text.lower()) if token]


def bag_recall(source_tokens: list[str], target_tokens: list[str]) -> float:
    if not source_tokens:
        return 0.0
    source_counts = Counter(source_tokens)
    target_counts = Counter(target_tokens)
    overlap = sum(min(count, target_counts.get(token, 0)) for token, count in source_counts.items())
    return overlap / sum(source_counts.values())


def suppressed_mic_rescue_policy_metric_values(metrics: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(metrics, dict):
        return {
            f"live_rescue_policy_{policy}_{metric}": None
            for policy in SUPPRESSED_MIC_RESCUE_POLICIES
            for metric in SUPPRESSED_MIC_RESCUE_POLICY_METRICS
        }
    return {
        f"live_rescue_policy_{policy}_{metric}": metrics.get(f"live_rescue_policy_{policy}_{metric}")
        for policy in SUPPRESSED_MIC_RESCUE_POLICIES
        for metric in SUPPRESSED_MIC_RESCUE_POLICY_METRICS
    }


def target_me_shadow_policy_metric_values(metrics: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(metrics, dict):
        return {
            f"live_target_me_shadow_policy_{policy}_{metric}": None
            for policy in TARGET_ME_RESCUE_POLICIES
            for metric in TARGET_ME_SHADOW_POLICY_METRICS
        }
    return {
        f"live_target_me_shadow_policy_{policy}_{metric}": metrics.get(
            f"live_target_me_shadow_policy_{policy}_{metric}"
        )
        for policy in TARGET_ME_RESCUE_POLICIES
        for metric in TARGET_ME_SHADOW_POLICY_METRICS
    }


def target_me_shadow_profile_metric_values(metrics: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(metrics, dict):
        return {
            f"live_target_me_shadow_profile_{policy}_{metric}": None
            for policy in TARGET_ME_SHADOW_PROFILE_POLICIES
            for metric in TARGET_ME_SHADOW_PROFILE_METRICS
        }
    return {
        f"live_target_me_shadow_profile_{policy}_{metric}": metrics.get(
            f"live_target_me_shadow_profile_{policy}_{metric}"
        )
        for policy in TARGET_ME_SHADOW_PROFILE_POLICIES
        for metric in TARGET_ME_SHADOW_PROFILE_METRICS
    }


def all_gates_passed(comparison: dict[str, Any] | None) -> bool:
    rows = gate_rows(comparison)
    return bool(rows) and all(row.get("status") == "passed" for row in rows)


def dimension_statuses(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    gate_by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        gate_by_name[str(row.get("name") or "unknown")].append(row)
    severity = {"failed": 4, "blocked": 4, "warning": 3, "not_evaluated": 2, "passed": 1}
    for key, spec in PARITY_DIMENSIONS.items():
        gates = [gate for name in spec["gates"] for gate in gate_by_name.get(name, [])]
        if not gates:
            status = "missing"
        else:
            status = max((str(gate.get("status") or "unknown") for gate in gates), key=lambda item: severity.get(item, 2))
        result[key] = {
            "title": spec["title"],
            "status": status,
            "promotion_required": bool(spec.get("promotion_required")),
            "gates": [
                {
                    "name": str(gate.get("name") or "unknown"),
                    "status": str(gate.get("status") or "unknown"),
                    "reason": gate.get("reason"),
                }
                for gate in gates
            ],
        }
    return result


def meaningful_comparison(metrics: dict[str, Any], comparison_status: Any) -> bool:
    if metrics.get("meaningful_live_comparison") is True:
        return True
    if comparison_status != "shadow_compared":
        return False
    return bool(
        safe_int(metrics.get("live_turn_count")) > 0
        and safe_int(metrics.get("batch_utterance_count")) > 0
        and safe_int(metrics.get("live_me_turn_count")) > 0
        and safe_int(metrics.get("live_remote_turn_count")) > 0
        and safe_int(metrics.get("batch_me_utterance_count")) > 0
        and safe_int(metrics.get("batch_remote_utterance_count")) > 0
    )


def build_capture_safety_gate(session: Path) -> dict[str, Any]:
    session_json = read_json(session / "session.json")
    pipeline = read_json(session / "derived/pipeline-run/pipeline_run_report.json")
    pipeline_status = pipeline.get("status") if isinstance(pipeline, dict) else None
    pipeline_blocker = str(pipeline.get("blocker") or "") if isinstance(pipeline, dict) else ""
    health = session_json.get("health") if isinstance(session_json, dict) else None
    health = health if isinstance(health, dict) else {}
    warnings = health.get("warnings") if isinstance(health.get("warnings"), list) else []
    warning_texts = [str(item) for item in warnings]
    restart_count = safe_int(health.get("screen_capture_restart_count"))
    partial = bool(health.get("partial")) or (isinstance(session_json, dict) and session_json.get("status") == "partial")
    explicit_stop = health.get("explicit_stop")
    health_summary = str(health.get("summary") or "")
    stop_reason = str(health.get("stop_reason") or "")
    safety_warnings = [
        text for text in warning_texts if any(marker in text.lower() for marker in CAPTURE_SAFETY_WARNING_MARKERS)
    ]
    evidence = {
        "pipeline_status": pipeline_status,
        "pipeline_blocker": pipeline_blocker or None,
        "session_status": session_json.get("status") if isinstance(session_json, dict) else None,
        "health_summary": health_summary or None,
        "partial": partial,
        "explicit_stop": explicit_stop,
        "stop_reason": stop_reason or None,
        "screen_capture_restart_count": restart_count,
        "warning_count": len(warning_texts),
        "safety_warning_count": len(safety_warnings),
        "sample_warnings": safety_warnings[:5],
    }
    if pipeline_status == "blocked" and pipeline_blocker in CAPTURE_SAFETY_BLOCKERS:
        return {
            "name": "capture_safety",
            "status": "blocked",
            "reason": f"batch pipeline blocked the session as {pipeline_blocker}",
            "evidence": evidence,
        }
    if not isinstance(session_json, dict) or not health:
        return {
            "name": "capture_safety",
            "status": "not_evaluated",
            "reason": "session capture health is missing",
            "evidence": evidence,
        }
    if partial or explicit_stop is False:
        return {
            "name": "capture_safety",
            "status": "blocked",
            "reason": "capture was partial or did not end through an explicit stop",
            "evidence": evidence,
        }
    if safety_warnings or restart_count > 0 or health_summary in {"warning", "partial"}:
        return {
            "name": "capture_safety",
            "status": "warning",
            "reason": "capture completed with ScreenCaptureKit/audio health warnings",
            "evidence": evidence,
        }
    return {
        "name": "capture_safety",
        "status": "passed",
        "reason": "capture health is complete and warning-free",
        "evidence": evidence,
    }


def evidence_scope(session: Path, root: Path) -> str:
    session_name = rel(session, root).split("/", maxsplit=1)[0]
    return "real_meeting" if REAL_SESSION_RE.match(session_name) else "diagnostic"


def summarize_dimensions(rows: list[dict[str, Any]]) -> tuple[dict[str, Counter[str]], dict[str, list[str]]]:
    dimension_counts: dict[str, Counter[str]] = defaultdict(Counter)
    dimension_issue_sessions: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        dimensions = row.get("parity_dimensions") if isinstance(row.get("parity_dimensions"), dict) else {}
        for key in PARITY_DIMENSIONS:
            value = dimensions.get(key) if isinstance(dimensions, dict) else None
            status = str(value.get("status") if isinstance(value, dict) else "missing")
            dimension_counts[key][status] += 1
            if status != "passed":
                dimension_issue_sessions[key].append(str(row.get("session") or ""))
    return dimension_counts, dimension_issue_sessions


def build_dimensions_payload(
    dimension_counts: dict[str, Counter[str]],
    dimension_issue_sessions: dict[str, list[str]],
) -> dict[str, dict[str, Any]]:
    return {
        key: {
            "title": spec["title"],
            "promotion_required": bool(spec.get("promotion_required")),
            "counts": dict(dimension_counts.get(key, Counter())),
            "issue_sessions": dimension_issue_sessions.get(key, []),
        }
        for key, spec in PARITY_DIMENSIONS.items()
    }


def row_dimension_status(row: dict[str, Any], key: str) -> str:
    dimensions = row.get("parity_dimensions") if isinstance(row.get("parity_dimensions"), dict) else {}
    value = dimensions.get(key) if isinstance(dimensions, dict) else None
    return str(value.get("status") if isinstance(value, dict) else "missing")


def capture_safe_candidate_row(row: dict[str, Any]) -> bool:
    return bool(
        row.get("evidence_scope") == "real_meeting"
        and row.get("comparison_status") == "shadow_compared"
        and row.get("meaningful_compared") is True
        and row_dimension_status(row, "capture_safety") == "passed"
        and row_dimension_status(row, "required_artifacts") == "passed"
    )


def capture_safe_evaluable_row(row: dict[str, Any]) -> bool:
    return bool(
        row.get("evidence_scope") == "real_meeting"
        and row.get("comparison_status") == "shadow_compared"
        and row_dimension_status(row, "capture_safety") == "passed"
        and row_dimension_status(row, "required_artifacts") == "passed"
    )


def blocking_dimensions_from_counts(dimension_counts: dict[str, Counter[str]]) -> list[str]:
    return [
        key
        for key, counts in dimension_counts.items()
        if any(status != "passed" and count > 0 for status, count in counts.items())
    ]


def capture_safe_proof_status(capture_regression_check: dict[str, Any] | None) -> str:
    proof = (
        capture_regression_check.get("capture_safe_proof")
        if isinstance(capture_regression_check, dict)
        and isinstance(capture_regression_check.get("capture_safe_proof"), dict)
        else {}
    )
    return str(proof.get("status") or "missing")


def controlled_real_live_pilot_allowed(capture_regression_check: dict[str, Any] | None) -> bool:
    return capture_safe_proof_status(capture_regression_check) == "full_fail_open_proof_passed"


def next_focus_for_dimensions(dimensions: list[str]) -> dict[str, Any] | None:
    if not dimensions:
        return None
    dimension = sorted(dimensions, key=lambda key: OBJECTIVE_DIMENSION_PRIORITY.get(key, 100))[0]
    action = OBJECTIVE_DIMENSION_ACTIONS.get(
        dimension,
        {
            "id": f"fix_{dimension}",
            "title": f"Fix {dimension}",
            "recommended_next": "inspect this live parity dimension before promotion",
        },
    )
    return {
        "dimension": dimension,
        "action_id": action["id"],
        "title": action["title"],
        "recommended_next": action["recommended_next"],
    }


def metric_aware_next_focus(
    dimensions: list[str],
    summary: dict[str, Any],
    base_focus: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    focus = dict(base_focus) if isinstance(base_focus, dict) else next_focus_for_dimensions(dimensions)
    if not focus:
        return None
    stable_order = safe_int(summary.get("real_live_unambiguous_contentful_role_constrained_order_mismatch_count"))
    contentful_order = safe_int(summary.get("real_live_contentful_role_constrained_order_mismatch_count"))
    missing_me_seconds = safe_float(summary.get("real_live_missing_me_seconds"))
    visible_missing_me_seconds = safe_float(summary.get("real_live_missing_me_visible_in_suppressed_mic_seconds"))
    remote_leak_seconds = safe_float(summary.get("real_live_suspected_remote_leak_in_me_seconds"))
    order_is_small_and_targeted = stable_order <= 2 and contentful_order <= 4
    target_dimension: str | None = None
    reason: str | None = None
    if focus.get("dimension") == "order_risk" and order_is_small_and_targeted:
        if "local_recall" in dimensions and missing_me_seconds >= 30.0:
            target_dimension = "local_recall"
            reason = (
                "order risk is narrowed to a small stable subset, while live local recall loses "
                "substantial Me speech"
            )
        elif "remote_leakage" in dimensions and remote_leak_seconds > 0.5:
            target_dimension = "remote_leakage"
            reason = (
                "order risk is narrowed to a small stable subset, while live Me still contains remote leakage"
            )
    if not target_dimension:
        return focus
    action = OBJECTIVE_DIMENSION_ACTIONS[target_dimension]
    return {
        **focus,
        "dimension": target_dimension,
        "action_id": action["id"],
        "title": action["title"],
        "recommended_next": action["recommended_next"],
        "source": "metric_aware_focus_override",
        "previous_focus": focus,
        "override_reason": reason,
        "metric_evidence": {
            "real_live_unambiguous_contentful_role_constrained_order_mismatch_count": stable_order,
            "real_live_contentful_role_constrained_order_mismatch_count": contentful_order,
            "real_live_missing_me_seconds": round(missing_me_seconds, 3),
            "real_live_missing_me_visible_in_suppressed_mic_seconds": round(visible_missing_me_seconds, 3),
            "real_live_suspected_remote_leak_in_me_seconds": round(remote_leak_seconds, 3),
        },
    }


def objective_audit_row(row_id: str, status: str, title: str, evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row_id,
        "status": status,
        "title": title,
        "evidence": evidence,
    }


def dimension_issue_score(non_passing: dict[str, Any], issue_session_count: int) -> int:
    status_weights = {
        "blocked": 100,
        "failed": 100,
        "missing": 90,
        "warning": 40,
        "not_evaluated": 25,
        "unknown": 10,
    }
    score = issue_session_count
    for status, count in non_passing.items():
        score += status_weights.get(str(status), 10) * safe_int(count)
    return score


def build_objective_audit(
    summary: dict[str, Any],
    coverage_target: dict[str, Any],
    real_dimensions: dict[str, dict[str, Any]],
    promotion_policy: dict[str, Any],
    candidate_scope: dict[str, Any],
    capture_regression_check: dict[str, Any] | None,
) -> dict[str, Any]:
    required_dimensions = set(PARITY_DIMENSIONS.keys())
    covered_dimensions = set(real_dimensions.keys())
    missing_dimensions = sorted(required_dimensions - covered_dimensions)
    blocking_dimensions: list[str] = []
    dimension_statuses: dict[str, dict[str, Any]] = {}
    next_actions: list[dict[str, Any]] = []
    for key in sorted(required_dimensions):
        value = real_dimensions.get(key) if isinstance(real_dimensions.get(key), dict) else {}
        counts = value.get("counts") if isinstance(value.get("counts"), dict) else {}
        non_passing = {status: count for status, count in counts.items() if status != "passed" and count}
        if not counts:
            non_passing = {"missing": 1}
        issue_sessions = value.get("issue_sessions") if isinstance(value.get("issue_sessions"), list) else []
        if non_passing:
            blocking_dimensions.append(key)
            action = OBJECTIVE_DIMENSION_ACTIONS.get(
                key,
                {
                    "id": f"fix_{key}",
                    "title": f"Fix {key}",
                    "recommended_next": "inspect this live parity dimension before promotion",
                },
            )
            next_actions.append(
                {
                    "dimension": key,
                    "action_id": action["id"],
                    "title": action["title"],
                    "recommended_next": action["recommended_next"],
                    "non_passing": non_passing,
                    "issue_sessions": issue_sessions,
                    "issue_session_count": len(issue_sessions),
                    "score": dimension_issue_score(non_passing, len(issue_sessions)),
                    "priority": OBJECTIVE_DIMENSION_PRIORITY.get(key, 100),
                }
            )
        dimension_statuses[key] = {
            "counts": counts,
            "non_passing": non_passing,
            "issue_sessions": issue_sessions,
        }
    next_actions.sort(
        key=lambda item: (
            safe_int(item.get("priority")) if item.get("priority") is not None else 100,
            -safe_int(item.get("score")),
            str(item.get("dimension") or ""),
        )
    )
    next_focus = next_actions[0] if next_actions else {
        "dimension": None,
        "action_id": "collect_controlled_live_parity_coverage",
        "title": "Collect controlled live parity coverage",
        "recommended_next": (
            "only after capture-safety proof and all parity dimensions pass, collect controlled Live Evidence"
        ),
        "non_passing": {},
        "issue_sessions": [],
        "issue_session_count": 0,
        "score": 0,
        "priority": 999,
    }
    next_focus = metric_aware_next_focus(blocking_dimensions, summary, next_focus) or next_focus
    capture_proof_status = capture_safe_proof_status(capture_regression_check)
    capture_proof_row_status = (
        "passed"
        if capture_proof_status == "full_fail_open_proof_passed"
        else ("incomplete" if capture_proof_status == "static_only" else "blocked")
    )
    if capture_proof_row_status != "passed" and (
        not next_actions or next_focus.get("action_id") == "collect_controlled_live_parity_coverage"
    ):
        next_focus = {
            "dimension": "capture_safety",
            "action_id": "capture_safe_redesign_before_more_live_coverage",
            "title": "Prove capture-safe live segment production",
            "recommended_next": (
                "keep live quarantined; run the full capture regression proof before collecting real live meetings"
            ),
            "non_passing": {"capture_safe_proof": 1},
            "issue_sessions": [],
            "issue_session_count": 0,
            "score": 100,
            "priority": 0,
        }
    if next_focus.get("dimension") == "capture_safety":
        if capture_proof_row_status == "passed":
            next_focus = {
                **next_focus,
                "title": "Resolve historical capture-safety evidence",
                "recommended_next": (
                    "capture-safe fail-open proof passed; keep live quarantined, treat historical unsafe "
                    "live sessions as negative evidence, and focus on capture-safe candidate parity blockers"
                ),
            }
        next_focus = {
            **next_focus,
            "capture_regression_check": {
                "status": capture_regression_check.get("status") if isinstance(capture_regression_check, dict) else None,
                "mode": capture_regression_check.get("mode") if isinstance(capture_regression_check, dict) else None,
                "capture_safe_proof_status": capture_proof_status,
            },
        }
    if (
        promotion_policy.get("controlled_real_live_pilot_allowed") is True
        and coverage_target.get("status") != "passed"
        and not (candidate_scope.get("blocking_dimensions") if isinstance(candidate_scope, dict) else [])
    ):
        next_focus = {
            "dimension": "controlled_live_pilot_coverage",
            "action_id": "collect_controlled_capture_safe_live_pilot",
            "title": "Collect controlled capture-safe live parity pilots",
            "recommended_next": (
                "run a controlled Live Evidence sidecar only after the full fail-open proof; keep batch "
                "authoritative and rerun `murmurmark corpus live all --refresh` after processing"
            ),
            "non_passing": {
                "passing_compared_sessions_remaining": coverage_target.get("passing_compared_sessions_remaining", 0)
            },
            "issue_sessions": [],
            "issue_session_count": 0,
            "score": 10,
            "priority": 2,
            "capture_regression_check": {
                "status": capture_regression_check.get("status") if isinstance(capture_regression_check, dict) else None,
                "mode": capture_regression_check.get("mode") if isinstance(capture_regression_check, dict) else None,
                "capture_safe_proof_status": capture_proof_status,
            },
        }
    candidate_next_focus = (
        candidate_scope.get("next_focus")
        if isinstance(candidate_scope, dict) and isinstance(candidate_scope.get("next_focus"), dict)
        else None
    )
    if (
        capture_proof_row_status == "passed"
        and safe_int(candidate_scope.get("sessions") if isinstance(candidate_scope, dict) else 0) > 0
        and (candidate_scope.get("blocking_dimensions") if isinstance(candidate_scope, dict) else [])
        and candidate_next_focus
    ):
        candidate_next_focus = metric_aware_next_focus(
            list(candidate_scope.get("blocking_dimensions") or []),
            summary,
            candidate_next_focus,
        )
        next_focus = {
            **candidate_next_focus,
            "source": "capture_safe_candidate_scope",
            "recommended_next": (
                f"{candidate_next_focus.get('recommended_next')}; "
                "historical unsafe live sessions remain negative evidence and must not be used as promotion candidates"
            ),
            "candidate_scope": {
                "sessions": candidate_scope.get("sessions"),
                "passing_sessions": candidate_scope.get("passing_sessions"),
                "blocking_dimensions": candidate_scope.get("blocking_dimensions"),
                "session_ids": candidate_scope.get("session_ids"),
            },
            "capture_regression_check": {
                "status": capture_regression_check.get("status") if isinstance(capture_regression_check, dict) else None,
                "mode": capture_regression_check.get("mode") if isinstance(capture_regression_check, dict) else None,
                "capture_safe_proof_status": capture_proof_status,
            },
        }

    rows = [
        objective_audit_row(
            "real_live_sessions_present",
            "passed" if safe_int(summary.get("real_live_sessions")) > 0 else "incomplete",
            "There is at least one real meeting live-pipeline session in the corpus",
            {"real_live_sessions": safe_int(summary.get("real_live_sessions"))},
        ),
        objective_audit_row(
            "live_compared_to_batch",
            "passed" if safe_int(summary.get("real_compared_sessions")) > 0 else "incomplete",
            "Real live chunks/drafts are compared with authoritative batch output",
            {
                "real_compared_sessions": safe_int(summary.get("real_compared_sessions")),
                "real_meaningful_compared_sessions": safe_int(summary.get("real_meaningful_compared_sessions")),
            },
        ),
        objective_audit_row(
            "coverage_target_met",
            "passed" if coverage_target.get("status") == "passed" else "incomplete",
            "Advisory real live coverage target is met",
            {
                "status": coverage_target.get("status"),
                "target_live_sessions": coverage_target.get("target_live_sessions"),
                "target_meaningful_compared_sessions": coverage_target.get("target_meaningful_compared_sessions"),
                "target_passing_compared_sessions": coverage_target.get("target_passing_compared_sessions"),
                "live_sessions_remaining": coverage_target.get("live_sessions_remaining"),
                "meaningful_compared_sessions_remaining": coverage_target.get("meaningful_compared_sessions_remaining"),
                "passing_compared_sessions_remaining": coverage_target.get("passing_compared_sessions_remaining"),
            },
        ),
        objective_audit_row(
            "required_dimensions_covered",
            "passed" if not missing_dimensions else "blocked",
            "Live parity report covers every required promotion dimension",
            {"required_dimensions": sorted(required_dimensions), "missing_dimensions": missing_dimensions},
        ),
        objective_audit_row(
            "required_dimensions_passed",
            "passed" if not blocking_dimensions else "blocked",
            "Every required real-session parity dimension has passed",
            {"blocking_dimensions": blocking_dimensions, "dimension_statuses": dimension_statuses},
        ),
        objective_audit_row(
            "capture_safe_fail_open_proof",
            capture_proof_row_status,
            "Capture-safe live fail-open proof has been run",
            {
                "report_present": isinstance(capture_regression_check, dict),
                "status": capture_regression_check.get("status") if isinstance(capture_regression_check, dict) else None,
                "mode": capture_regression_check.get("mode") if isinstance(capture_regression_check, dict) else None,
                "capture_safe_proof_status": capture_proof_status,
            },
        ),
        objective_audit_row(
            "batch_authoritative",
            "passed" if promotion_policy.get("batch_authoritative") is True else "blocked",
            "Batch transcript remains authoritative",
            {"batch_authoritative": promotion_policy.get("batch_authoritative")},
        ),
        objective_audit_row(
            "live_promotion_blocked",
            "passed"
            if promotion_policy.get("status") == "blocked" and safe_int(summary.get("promotion_allowed_sessions")) == 0
            else "blocked",
            "Live promotion is blocked until parity gates prove safety",
            {
                "promotion_policy_status": promotion_policy.get("status"),
                "promotion_allowed_sessions": safe_int(summary.get("promotion_allowed_sessions")),
            },
        ),
        objective_audit_row(
            "new_real_live_collection_quarantined",
            "passed" if promotion_policy.get("new_real_live_collection_allowed") is False else "blocked",
            "New real live-pipeline collection remains disabled while live is quarantined",
            {"new_real_live_collection_allowed": promotion_policy.get("new_real_live_collection_allowed")},
        ),
        objective_audit_row(
            "controlled_real_live_pilot_collection",
            "passed" if promotion_policy.get("controlled_real_live_pilot_allowed") is True else "incomplete",
            "Controlled real live-pipeline pilot collection is allowed only after full fail-open proof",
            {
                "controlled_real_live_pilot_allowed": promotion_policy.get("controlled_real_live_pilot_allowed"),
                "capture_safe_proof_status": capture_proof_status,
                "scope": "controlled Live Evidence only; promotion remains blocked and batch authoritative",
            },
        ),
    ]
    statuses = {str(row.get("status")) for row in rows}
    if "blocked" in statuses:
        overall = "blocked_by_parity_gates"
    elif "incomplete" in statuses:
        overall = "incomplete_coverage"
    else:
        overall = "coverage_ready_shadow_locked"
    return {
        "schema": "murmurmark.live_parity_objective_audit/v1",
        "objective": "Near-Realtime Live Parity Coverage v1",
        "overall_status": overall,
        "ready_for_live_promotion": False,
        "batch_authoritative": promotion_policy.get("batch_authoritative") is True,
        "new_real_live_collection_allowed": promotion_policy.get("new_real_live_collection_allowed") is True,
        "controlled_real_live_pilot_allowed": promotion_policy.get("controlled_real_live_pilot_allowed") is True,
        "blocking_dimensions": blocking_dimensions,
        "next_focus": next_focus,
        "next_actions": next_actions,
        "rows": rows,
    }


def sum_metric(rows: list[dict[str, Any]], metric: str) -> float:
    return round(sum(float(((row.get("metrics") or {}).get(metric) or 0.0)) for row in rows), 3)


def sum_int_metric(rows: list[dict[str, Any]], metric: str) -> int:
    return sum(int(((row.get("metrics") or {}).get(metric) or 0)) for row in rows)


def sum_counter_metric(rows: list[dict[str, Any]], metric: str) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
        value = metrics.get(metric)
        if not isinstance(value, dict):
            continue
        for key, count in value.items():
            counts[str(key)] += int(safe_float(count))
    return dict(sorted(counts.items()))


def add_suppressed_mic_rescue_policy_summary(summary: dict[str, Any], rows: list[dict[str, Any]], prefix: str) -> None:
    oracle_local_seconds = sum_metric(rows, "live_rescue_policy_batch_oracle_local_ceiling_local_seconds")
    for policy in SUPPRESSED_MIC_RESCUE_POLICIES:
        base = f"live_rescue_policy_{policy}"
        out = f"{prefix}_live_rescue_policy_{policy}" if prefix else base
        selected_count = sum_int_metric(rows, f"{base}_selected_segment_count")
        selected_seconds = sum_metric(rows, f"{base}_selected_seconds")
        local_seconds = sum_metric(rows, f"{base}_local_seconds")
        remote_risk_seconds = sum_metric(rows, f"{base}_remote_risk_seconds")
        missing_after_seconds = sum_metric(rows, f"{base}_missing_me_seconds_after")
        recovered_seconds = sum_metric(rows, f"{base}_missing_me_recovered_seconds")
        summary[f"{out}_selected_segment_count"] = selected_count
        summary[f"{out}_selected_seconds"] = selected_seconds
        summary[f"{out}_local_seconds"] = local_seconds
        summary[f"{out}_remote_risk_seconds"] = remote_risk_seconds
        summary[f"{out}_missing_me_seconds_after"] = missing_after_seconds
        summary[f"{out}_missing_me_recovered_seconds"] = recovered_seconds
        summary[f"{out}_precision_proxy"] = round(local_seconds / selected_seconds, 6) if selected_seconds > 0 else None
        summary[f"{out}_local_recall_proxy"] = (
            round(local_seconds / oracle_local_seconds, 6) if oracle_local_seconds > 0 else None
        )


def add_target_me_shadow_policy_summary(summary: dict[str, Any], rows: list[dict[str, Any]], prefix: str) -> None:
    for policy in TARGET_ME_RESCUE_POLICIES:
        base = f"live_target_me_shadow_policy_{policy}"
        out = f"{prefix}_live_target_me_shadow_policy_{policy}" if prefix else base
        summary[f"{out}_candidate_segment_count"] = sum_int_metric(rows, f"{base}_candidate_segment_count")
        summary[f"{out}_candidate_seconds"] = sum_metric(rows, f"{base}_candidate_seconds")
        summary[f"{out}_missing_me_seconds_after"] = sum_metric(rows, f"{base}_missing_me_seconds_after")
        summary[f"{out}_missing_me_recovered_seconds"] = sum_metric(rows, f"{base}_missing_me_recovered_seconds")
        summary[f"{out}_suspected_remote_leak_in_me_count"] = sum_int_metric(
            rows,
            f"{base}_suspected_remote_leak_in_me_count",
        )
        summary[f"{out}_suspected_remote_leak_in_me_seconds"] = sum_metric(
            rows,
            f"{base}_suspected_remote_leak_in_me_seconds",
        )
        summary[f"{out}_order_mismatch_count"] = sum_int_metric(rows, f"{base}_order_mismatch_count")
        summary[f"{out}_role_constrained_order_mismatch_count"] = sum_int_metric(
            rows,
            f"{base}_role_constrained_order_mismatch_count",
        )
        summary[f"{out}_contentful_role_constrained_order_mismatch_count"] = sum_int_metric(
            rows,
            f"{base}_contentful_role_constrained_order_mismatch_count",
        )
        summary[f"{out}_order_mismatch_delta_count"] = sum_int_metric(rows, f"{base}_order_mismatch_delta_count")
        summary[f"{out}_role_constrained_order_mismatch_delta_count"] = sum_int_metric(
            rows,
            f"{base}_role_constrained_order_mismatch_delta_count",
        )
        summary[f"{out}_contentful_role_constrained_order_mismatch_delta_count"] = sum_int_metric(
            rows,
            f"{base}_contentful_role_constrained_order_mismatch_delta_count",
        )
        summary[f"{out}_rejected_candidate_count"] = sum_int_metric(rows, f"{base}_rejected_candidate_count")
        summary[f"{out}_rejected_candidate_seconds"] = sum_metric(rows, f"{base}_rejected_candidate_seconds")
        summary[f"{out}_rejected_would_add_contentful_order_mismatch_count"] = sum_int_metric(
            rows,
            f"{base}_rejected_would_add_contentful_order_mismatch_count",
        )
        summary[f"{out}_rejected_would_add_contentful_order_mismatch_seconds"] = sum_metric(
            rows,
            f"{base}_rejected_would_add_contentful_order_mismatch_seconds",
        )
        summary[f"{out}_rejected_would_add_suspected_remote_leak_count"] = sum_int_metric(
            rows,
            f"{base}_rejected_would_add_suspected_remote_leak_count",
        )
        summary[f"{out}_rejected_would_add_suspected_remote_leak_seconds"] = sum_metric(
            rows,
            f"{base}_rejected_would_add_suspected_remote_leak_seconds",
        )


def add_target_me_shadow_profile_summary(summary: dict[str, Any], rows: list[dict[str, Any]], prefix: str) -> None:
    for policy in TARGET_ME_SHADOW_PROFILE_POLICIES:
        base = f"live_target_me_shadow_profile_{policy}"
        out = f"{prefix}_live_target_me_shadow_profile_{policy}" if prefix else base
        evaluated_rows = [
            row
            for row in rows
            if (row.get("metrics") or {}).get(f"{base}_non_passing_gate_count") is not None
        ]
        summary[f"{out}_evaluated_session_count"] = len(evaluated_rows)
        summary[f"{out}_all_parity_gates_passed_session_count"] = sum(
            1 for row in evaluated_rows if (row.get("metrics") or {}).get(f"{base}_all_parity_gates_passed") is True
        )
        summary[f"{out}_non_passing_gate_count"] = sum_int_metric(evaluated_rows, f"{base}_non_passing_gate_count")
        summary[f"{out}_live_missing_me_seconds"] = sum_metric(evaluated_rows, f"{base}_live_missing_me_seconds")
        summary[f"{out}_live_missing_me_visible_in_suppressed_mic_seconds"] = sum_metric(
            evaluated_rows,
            f"{base}_live_missing_me_visible_in_suppressed_mic_seconds",
        )
        summary[f"{out}_live_missing_me_not_visible_in_suppressed_mic_seconds"] = sum_metric(
            evaluated_rows,
            f"{base}_live_missing_me_not_visible_in_suppressed_mic_seconds",
        )
        summary[f"{out}_live_missing_me_with_target_me_candidate_seconds"] = sum_metric(
            evaluated_rows,
            f"{base}_live_missing_me_with_target_me_candidate_seconds",
        )
        summary[f"{out}_live_missing_me_without_target_me_candidate_seconds"] = sum_metric(
            evaluated_rows,
            f"{base}_live_missing_me_without_target_me_candidate_seconds",
        )
        summary[f"{out}_live_missing_me_visible_with_target_me_candidate_seconds"] = sum_metric(
            evaluated_rows,
            f"{base}_live_missing_me_visible_with_target_me_candidate_seconds",
        )
        summary[f"{out}_live_missing_me_visible_without_target_me_candidate_seconds"] = sum_metric(
            evaluated_rows,
            f"{base}_live_missing_me_visible_without_target_me_candidate_seconds",
        )
        summary[f"{out}_live_missing_me_not_visible_with_target_me_candidate_seconds"] = sum_metric(
            evaluated_rows,
            f"{base}_live_missing_me_not_visible_with_target_me_candidate_seconds",
        )
        summary[f"{out}_live_missing_me_not_visible_without_target_me_candidate_seconds"] = sum_metric(
            evaluated_rows,
            f"{base}_live_missing_me_not_visible_without_target_me_candidate_seconds",
        )
        summary[f"{out}_live_suspected_remote_leak_in_me_seconds"] = sum_metric(
            evaluated_rows,
            f"{base}_live_suspected_remote_leak_in_me_seconds",
        )
        summary[f"{out}_live_order_mismatch_count"] = sum_int_metric(
            evaluated_rows,
            f"{base}_live_order_mismatch_count",
        )
        summary[f"{out}_live_contentful_role_constrained_order_mismatch_count"] = sum_int_metric(
            evaluated_rows,
            f"{base}_live_contentful_role_constrained_order_mismatch_count",
        )
        summary[f"{out}_live_batch_interval_overlap_order_ambiguity_count"] = sum_int_metric(
            evaluated_rows,
            f"{base}_live_batch_interval_overlap_order_ambiguity_count",
        )
        summary[f"{out}_live_role_constrained_batch_interval_overlap_order_ambiguity_count"] = sum_int_metric(
            evaluated_rows,
            f"{base}_live_role_constrained_batch_interval_overlap_order_ambiguity_count",
        )
        summary[f"{out}_live_contentful_role_constrained_batch_interval_overlap_order_ambiguity_count"] = (
            sum_int_metric(
                evaluated_rows,
                f"{base}_live_contentful_role_constrained_batch_interval_overlap_order_ambiguity_count",
            )
        )
        summary[f"{out}_removed_live_turn_count"] = sum_int_metric(
            evaluated_rows,
            f"{base}_removed_live_turn_count",
        )
        summary[f"{out}_removed_live_turn_seconds"] = sum_metric(
            evaluated_rows,
            f"{base}_removed_live_turn_seconds",
        )
        summary[f"{out}_boundary_order_retime_oracle_turn_count"] = sum_int_metric(
            evaluated_rows,
            f"{base}_boundary_order_retime_oracle_turn_count",
        )
        summary[f"{out}_boundary_order_retime_oracle_trimmed_seconds"] = sum_metric(
            evaluated_rows,
            f"{base}_boundary_order_retime_oracle_trimmed_seconds",
        )
        summary[f"{out}_boundary_order_split_retime_oracle_turn_count"] = sum_int_metric(
            evaluated_rows,
            f"{base}_boundary_order_split_retime_oracle_turn_count",
        )
        summary[f"{out}_boundary_order_split_retime_oracle_preserved_prefix_count"] = sum_int_metric(
            evaluated_rows,
            f"{base}_boundary_order_split_retime_oracle_preserved_prefix_count",
        )
        summary[f"{out}_boundary_order_split_retime_oracle_preserved_prefix_seconds"] = sum_metric(
            evaluated_rows,
            f"{base}_boundary_order_split_retime_oracle_preserved_prefix_seconds",
        )
        summary[f"{out}_visible_suppressed_mic_added_turn_count"] = sum_int_metric(
            evaluated_rows,
            f"{base}_visible_suppressed_mic_added_turn_count",
        )
        summary[f"{out}_visible_suppressed_mic_added_turn_seconds"] = sum_metric(
            evaluated_rows,
            f"{base}_visible_suppressed_mic_added_turn_seconds",
        )
        summary[f"{out}_visible_suppressed_mic_rejected_turn_count"] = sum_int_metric(
            evaluated_rows,
            f"{base}_visible_suppressed_mic_rejected_turn_count",
        )
        summary[f"{out}_live_boundary_micro_asr_lab_added_turn_count"] = sum_int_metric(
            evaluated_rows,
            f"{base}_live_boundary_micro_asr_lab_added_turn_count",
        )
        summary[f"{out}_live_boundary_micro_asr_lab_added_turn_seconds"] = sum_metric(
            evaluated_rows,
            f"{base}_live_boundary_micro_asr_lab_added_turn_seconds",
        )
        summary[f"{out}_live_boundary_micro_asr_lab_rejected_turn_count"] = sum_int_metric(
            evaluated_rows,
            f"{base}_live_boundary_micro_asr_lab_rejected_turn_count",
        )
        summary[f"{out}_live_boundary_micro_asr_live_only_added_turn_count"] = sum_int_metric(
            evaluated_rows,
            f"{base}_live_boundary_micro_asr_live_only_added_turn_count",
        )
        summary[f"{out}_live_boundary_micro_asr_live_only_added_turn_seconds"] = sum_metric(
            evaluated_rows,
            f"{base}_live_boundary_micro_asr_live_only_added_turn_seconds",
        )
        summary[f"{out}_live_boundary_micro_asr_live_only_rejected_turn_count"] = sum_int_metric(
            evaluated_rows,
            f"{base}_live_boundary_micro_asr_live_only_rejected_turn_count",
        )
        summary[f"{out}_remote_guarded_voice_boundary_added_turn_count"] = sum_int_metric(
            evaluated_rows,
            f"{base}_remote_guarded_voice_boundary_added_turn_count",
        )
        summary[f"{out}_remote_guarded_voice_boundary_added_turn_seconds"] = sum_metric(
            evaluated_rows,
            f"{base}_remote_guarded_voice_boundary_added_turn_seconds",
        )
        summary[f"{out}_remote_guarded_voice_boundary_rejected_turn_count"] = sum_int_metric(
            evaluated_rows,
            f"{base}_remote_guarded_voice_boundary_rejected_turn_count",
        )


def target_me_shadow_policy_diagnostics(summary: dict[str, Any], prefix: str) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    best_safe: dict[str, Any] | None = None
    key_prefix = f"{prefix}_live_target_me_shadow_policy_" if prefix else "live_target_me_shadow_policy_"
    for policy in TARGET_ME_RESCUE_POLICIES:
        base = f"{key_prefix}{policy}"
        candidate_seconds = safe_float(summary.get(f"{base}_candidate_seconds"))
        recovered = safe_float(summary.get(f"{base}_missing_me_recovered_seconds"))
        remote_risk = safe_float(summary.get(f"{base}_suspected_remote_leak_in_me_seconds"))
        order_mismatches = safe_int(summary.get(f"{base}_contentful_role_constrained_order_mismatch_count"))
        order_mismatch_delta = safe_int(summary.get(f"{base}_contentful_role_constrained_order_mismatch_delta_count"))
        row = {
            "policy": policy,
            "candidate_seconds": candidate_seconds,
            "missing_me_recovered_seconds": recovered,
            "suspected_remote_leak_in_me_seconds": remote_risk,
            "contentful_role_constrained_order_mismatch_count": order_mismatches,
            "contentful_role_constrained_order_mismatch_delta_count": order_mismatch_delta,
            "rejected_candidate_count": safe_int(summary.get(f"{base}_rejected_candidate_count")),
            "rejected_candidate_seconds": safe_float(summary.get(f"{base}_rejected_candidate_seconds")),
            "rejected_would_add_contentful_order_mismatch_seconds": safe_float(
                summary.get(f"{base}_rejected_would_add_contentful_order_mismatch_seconds")
            ),
            "rejected_would_add_suspected_remote_leak_seconds": safe_float(
                summary.get(f"{base}_rejected_would_add_suspected_remote_leak_seconds")
            ),
            "safe_candidate": candidate_seconds > 0 and recovered > 0 and remote_risk <= 3.0 and order_mismatch_delta <= 0,
        }
        rows.append(row)
        if row["safe_candidate"] and (best_safe is None or recovered > safe_float(best_safe.get("missing_me_recovered_seconds"))):
            best_safe = row
    status = "safe_candidate_available" if best_safe else "no_safe_candidate"
    return {
        "schema": "murmurmark.live_target_me_shadow_policy_diagnostics/v1",
        "scope": prefix or "all",
        "status": status,
        "recommended_policy": best_safe,
        "policies": rows,
        "recommended_next": (
            "materialize_target_me_shadow_rescue_draft"
            if best_safe
            else "keep_collecting_target_me_evidence_or_add_enrollment_fallback"
        ),
    }


def target_me_shadow_profile_diagnostics(summary: dict[str, Any], prefix: str) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    key_prefix = f"{prefix}_live_target_me_shadow_profile_" if prefix else "live_target_me_shadow_profile_"
    best: dict[str, Any] | None = None
    best_live_implementable: dict[str, Any] | None = None
    for policy in TARGET_ME_SHADOW_PROFILE_POLICIES:
        base = f"{key_prefix}{policy}"
        live_implementable = (
            "batch_remote_forbidden" not in policy
            and "_oracle" not in policy
            and policy != LIVE_BOUNDARY_MICRO_ASR_LAB_SHADOW_PROFILE_POLICY
        )
        diagnostic_kind = (
            "live_implementable"
            if live_implementable
            else (
                "lab_shadow"
                if policy == LIVE_BOUNDARY_MICRO_ASR_LAB_SHADOW_PROFILE_POLICY
                else "live_only_shadow"
                if policy == LIVE_BOUNDARY_MICRO_ASR_LIVE_ONLY_SHADOW_PROFILE_POLICY
                else "oracle"
            )
        )
        row = {
            "policy": policy,
            "live_implementable": live_implementable,
            "diagnostic_kind": diagnostic_kind,
            "evaluated_session_count": safe_int(summary.get(f"{base}_evaluated_session_count")),
            "all_parity_gates_passed_session_count": safe_int(
                summary.get(f"{base}_all_parity_gates_passed_session_count")
            ),
            "non_passing_gate_count": safe_int(summary.get(f"{base}_non_passing_gate_count")),
            "live_missing_me_seconds": safe_float(summary.get(f"{base}_live_missing_me_seconds")),
            "live_missing_me_visible_in_suppressed_mic_seconds": safe_float(
                summary.get(f"{base}_live_missing_me_visible_in_suppressed_mic_seconds")
            ),
            "live_missing_me_not_visible_in_suppressed_mic_seconds": safe_float(
                summary.get(f"{base}_live_missing_me_not_visible_in_suppressed_mic_seconds")
            ),
            "live_missing_me_with_target_me_candidate_seconds": safe_float(
                summary.get(f"{base}_live_missing_me_with_target_me_candidate_seconds")
            ),
            "live_missing_me_without_target_me_candidate_seconds": safe_float(
                summary.get(f"{base}_live_missing_me_without_target_me_candidate_seconds")
            ),
            "live_missing_me_visible_with_target_me_candidate_seconds": safe_float(
                summary.get(f"{base}_live_missing_me_visible_with_target_me_candidate_seconds")
            ),
            "live_missing_me_visible_without_target_me_candidate_seconds": safe_float(
                summary.get(f"{base}_live_missing_me_visible_without_target_me_candidate_seconds")
            ),
            "live_missing_me_not_visible_with_target_me_candidate_seconds": safe_float(
                summary.get(f"{base}_live_missing_me_not_visible_with_target_me_candidate_seconds")
            ),
            "live_missing_me_not_visible_without_target_me_candidate_seconds": safe_float(
                summary.get(f"{base}_live_missing_me_not_visible_without_target_me_candidate_seconds")
            ),
            "live_suspected_remote_leak_in_me_seconds": safe_float(
                summary.get(f"{base}_live_suspected_remote_leak_in_me_seconds")
            ),
            "live_order_mismatch_count": safe_int(summary.get(f"{base}_live_order_mismatch_count")),
            "live_contentful_role_constrained_order_mismatch_count": safe_int(
                summary.get(f"{base}_live_contentful_role_constrained_order_mismatch_count")
            ),
            "removed_live_turn_count": safe_int(summary.get(f"{base}_removed_live_turn_count")),
            "removed_live_turn_seconds": safe_float(summary.get(f"{base}_removed_live_turn_seconds")),
            "visible_suppressed_mic_added_turn_count": safe_int(
                summary.get(f"{base}_visible_suppressed_mic_added_turn_count")
            ),
            "visible_suppressed_mic_added_turn_seconds": safe_float(
                summary.get(f"{base}_visible_suppressed_mic_added_turn_seconds")
            ),
            "visible_suppressed_mic_rejected_turn_count": safe_int(
                summary.get(f"{base}_visible_suppressed_mic_rejected_turn_count")
            ),
            "live_boundary_micro_asr_lab_added_turn_count": safe_int(
                summary.get(f"{base}_live_boundary_micro_asr_lab_added_turn_count")
            ),
            "live_boundary_micro_asr_lab_added_turn_seconds": safe_float(
                summary.get(f"{base}_live_boundary_micro_asr_lab_added_turn_seconds")
            ),
            "live_boundary_micro_asr_lab_rejected_turn_count": safe_int(
                summary.get(f"{base}_live_boundary_micro_asr_lab_rejected_turn_count")
            ),
            "live_boundary_micro_asr_live_only_added_turn_count": safe_int(
                summary.get(f"{base}_live_boundary_micro_asr_live_only_added_turn_count")
            ),
            "live_boundary_micro_asr_live_only_added_turn_seconds": safe_float(
                summary.get(f"{base}_live_boundary_micro_asr_live_only_added_turn_seconds")
            ),
            "live_boundary_micro_asr_live_only_rejected_turn_count": safe_int(
                summary.get(f"{base}_live_boundary_micro_asr_live_only_rejected_turn_count")
            ),
            "boundary_order_retime_oracle_turn_count": safe_int(
                summary.get(f"{base}_boundary_order_retime_oracle_turn_count")
            ),
            "boundary_order_retime_oracle_trimmed_seconds": safe_float(
                summary.get(f"{base}_boundary_order_retime_oracle_trimmed_seconds")
            ),
            "boundary_order_split_retime_oracle_turn_count": safe_int(
                summary.get(f"{base}_boundary_order_split_retime_oracle_turn_count")
            ),
            "boundary_order_split_retime_oracle_preserved_prefix_count": safe_int(
                summary.get(f"{base}_boundary_order_split_retime_oracle_preserved_prefix_count")
            ),
            "boundary_order_split_retime_oracle_preserved_prefix_seconds": safe_float(
                summary.get(f"{base}_boundary_order_split_retime_oracle_preserved_prefix_seconds")
            ),
        }
        rows.append(row)
        if row["evaluated_session_count"] > 0 and (
            best is None
            or (
                row["all_parity_gates_passed_session_count"],
                -row["non_passing_gate_count"],
                -row["live_missing_me_seconds"],
            )
            > (
                safe_int(best.get("all_parity_gates_passed_session_count")),
                -safe_int(best.get("non_passing_gate_count")),
                -safe_float(best.get("live_missing_me_seconds")),
            )
        ):
            best = row
        if live_implementable and row["evaluated_session_count"] > 0 and (
            best_live_implementable is None
            or (
                row["all_parity_gates_passed_session_count"],
                -row["non_passing_gate_count"],
                -row["live_suspected_remote_leak_in_me_seconds"],
                -row["live_contentful_role_constrained_order_mismatch_count"],
                -row["live_missing_me_seconds"],
            )
            > (
                safe_int(best_live_implementable.get("all_parity_gates_passed_session_count")),
                -safe_int(best_live_implementable.get("non_passing_gate_count")),
                -safe_float(best_live_implementable.get("live_suspected_remote_leak_in_me_seconds")),
                -safe_int(best_live_implementable.get("live_contentful_role_constrained_order_mismatch_count")),
                -safe_float(best_live_implementable.get("live_missing_me_seconds")),
            )
        ):
            best_live_implementable = row
    status = "profile_evaluated" if best else "not_evaluated"
    best_to_live_gap: dict[str, Any] | None = None
    if best and best_live_implementable:
        missing_gap = round(
            max(
                0.0,
                safe_float(best_live_implementable.get("live_missing_me_seconds"))
                - safe_float(best.get("live_missing_me_seconds")),
            ),
            3,
        )
        best_to_live_gap = {
            "schema": "murmurmark.live_shadow_profile_oracle_gap/v1",
            "best_profile": best.get("policy"),
            "best_profile_live_implementable": bool(best.get("live_implementable")),
            "best_live_implementable_profile": best_live_implementable.get("policy"),
            "missing_me_seconds_gap": missing_gap,
            "best_profile_missing_me_seconds": best.get("live_missing_me_seconds"),
            "best_live_implementable_missing_me_seconds": best_live_implementable.get("live_missing_me_seconds"),
            "best_profile_remote_leak_seconds": best.get("live_suspected_remote_leak_in_me_seconds"),
            "best_live_implementable_remote_leak_seconds": best_live_implementable.get(
                "live_suspected_remote_leak_in_me_seconds"
            ),
            "best_profile_contentful_order_mismatch_count": best.get(
                "live_contentful_role_constrained_order_mismatch_count"
            ),
            "best_live_implementable_contentful_order_mismatch_count": best_live_implementable.get(
                "live_contentful_role_constrained_order_mismatch_count"
            ),
            "interpretation": (
                "best_profile_is_live_implementable"
                if bool(best.get("live_implementable"))
                else f"best_profile_is_{best.get('diagnostic_kind')}_only"
            ),
            "promotion_allowed": False,
            "promotion_reason": "diagnostic_gap_is_not_promotion_evidence_and_parity_gates_still_block_promotion",
        }
    return {
        "schema": "murmurmark.live_target_me_shadow_profile_diagnostics/v1",
        "scope": prefix or "all",
        "status": status,
        "best_profile": best,
        "best_live_implementable_profile": best_live_implementable,
        "best_to_live_implementable_gap": best_to_live_gap,
        "profiles": rows,
        "recommended_next": (
            "fix_remaining_parity_gate_blockers_for_materialized_target_me_shadow"
            if best
            else "materialize_target_me_shadow_profiles"
        ),
    }


def rescue_policy_diagnostics(summary: dict[str, Any], prefix: str) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    best_safe: dict[str, Any] | None = None
    best_exploratory: dict[str, Any] | None = None
    key_prefix = f"{prefix}_live_rescue_policy_" if prefix else "live_rescue_policy_"
    for policy in SUPPRESSED_MIC_RESCUE_POLICIES:
        base = f"{key_prefix}{policy}"
        selected_seconds = safe_float(summary.get(f"{base}_selected_seconds"))
        local_seconds = safe_float(summary.get(f"{base}_local_seconds"))
        remote_risk_seconds = safe_float(summary.get(f"{base}_remote_risk_seconds"))
        precision = summary.get(f"{base}_precision_proxy")
        recall = summary.get(f"{base}_local_recall_proxy")
        recovered = safe_float(summary.get(f"{base}_missing_me_recovered_seconds"))
        row = {
            "policy": policy,
            "live_implementable": policy != "batch_oracle_local_ceiling",
            "selected_seconds": selected_seconds,
            "local_seconds": local_seconds,
            "remote_risk_seconds": remote_risk_seconds,
            "precision_proxy": precision,
            "local_recall_proxy": recall,
            "missing_me_recovered_seconds": recovered,
            "safe_candidate": selected_seconds > 0 and remote_risk_seconds <= 3.0 and (precision is None or precision >= 0.90),
            "material_candidate": local_seconds >= MATERIAL_RESCUE_LOCAL_SECONDS,
        }
        rows.append(row)
        if (
            row["live_implementable"]
            and row["safe_candidate"]
            and row["material_candidate"]
            and (best_safe is None or local_seconds > safe_float(best_safe.get("local_seconds")))
        ):
            best_safe = row
        if (
            row["live_implementable"]
            and selected_seconds > 0
            and (best_exploratory is None or local_seconds > safe_float(best_exploratory.get("local_seconds")))
        ):
            best_exploratory = row
    if best_safe:
        status = "safe_candidate_available"
        recommended = best_safe
        best = best_safe
    elif best_exploratory:
        status = "exploratory_only" if safe_float(best_exploratory.get("local_seconds")) >= MATERIAL_RESCUE_LOCAL_SECONDS else "no_material_live_candidate"
        recommended = None
        best = best_exploratory
    else:
        status = "no_candidate"
        recommended = None
        best = None
    return {
        "schema": "murmurmark.live_rescue_policy_diagnostics/v1",
        "scope": prefix or "all",
        "status": status,
        "safe_remote_risk_threshold_sec": 3.0,
        "safe_precision_threshold": 0.90,
        "material_local_seconds_threshold": MATERIAL_RESCUE_LOCAL_SECONDS,
        "recommended_policy": recommended,
        "best_policy": best,
        "policies": rows,
    }


def local_recall_gap_examples(rows: list[dict[str, Any]], *, limit: int = 50) -> dict[str, Any]:
    examples: list[dict[str, Any]] = []
    for row in rows:
        risk_examples = row.get("risk_examples") if isinstance(row.get("risk_examples"), dict) else {}
        inputs = row.get("inputs") if isinstance(row.get("inputs"), dict) else {}
        metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
        for category in ("local_missing", "local_missing_suspicious_batch_me"):
            for item in risk_examples.get(category) or []:
                if not isinstance(item, dict):
                    continue
                examples.append(
                    {
                        "session": row.get("session"),
                        "category": category,
                        "batch_id": item.get("batch_id"),
                        "start": item.get("start"),
                        "end": item.get("end"),
                        "duration_sec": item.get("duration_sec"),
                        "recall_in_live_me": item.get("recall_in_live_me"),
                        "recall_in_suppressed_mic": item.get("recall_in_suppressed_mic"),
                        "suppressed_mic_turn_ids": item.get("suppressed_mic_turn_ids"),
                        "reason": item.get("reason"),
                        "text": item.get("text"),
                        "comparison": inputs.get("live_batch_comparison"),
                        "live_me_turn_count": metrics.get("live_me_turn_count"),
                        "batch_me_utterance_count": metrics.get("batch_me_utterance_count"),
                    }
                )
    examples.sort(
        key=lambda item: (
            -safe_float(item.get("duration_sec")),
            str(item.get("session") or ""),
            safe_float(item.get("start")),
        )
    )
    total_seconds = round(sum(safe_float(item.get("duration_sec")) for item in examples), 3)
    return {
        "schema": "murmurmark.live_local_recall_gap_examples/v1",
        "item_count": len(examples),
        "seconds": total_seconds,
        "examples": examples[:limit],
        "truncated": len(examples) > limit,
        "limit": limit,
    }


def target_me_shadow_profile_row(diagnostics: dict[str, Any], policy: str) -> dict[str, Any]:
    profiles = diagnostics.get("profiles") if isinstance(diagnostics.get("profiles"), list) else []
    for row in profiles:
        if isinstance(row, dict) and row.get("policy") == policy:
            return row
    return {}


def remaining_gap_evidence_label_seconds(item: dict[str, Any]) -> dict[str, float]:
    grouped: dict[str, float] = {}
    evidence_rows = item.get("suppressed_mic_evidence") if isinstance(item.get("suppressed_mic_evidence"), list) else []
    for evidence in evidence_rows:
        if not isinstance(evidence, dict):
            continue
        label = str(evidence.get("batch_role_label") or "(none)")
        grouped[label] = round(grouped.get(label, 0.0) + safe_float(evidence.get("overlap_sec")), 3)
    return dict(sorted(grouped.items(), key=lambda pair: (-pair[1], pair[0])))


def remaining_gap_dominant_evidence_label(item: dict[str, Any]) -> str:
    grouped = remaining_gap_evidence_label_seconds(item)
    if not grouped:
        return "(none)"
    return next(iter(grouped.keys()))


def remaining_gap_mixed_segmentability(
    *,
    evidence_rows: list[dict[str, Any]],
    label_seconds: dict[str, float],
    duplicate_seconds: float,
    duration: float,
) -> dict[str, Any]:
    local_islands = [
        row
        for row in evidence_rows
        if isinstance(row, dict)
        and row.get("batch_role_label") in {"me_dominant", "mixed"}
        and not bool(row.get("known_hallucination"))
        and row.get("segment_gate_reason") == "segment_has_local_tokens_not_seen_in_overlapping_remote"
    ]
    local_island_seconds = round(sum(safe_float(row.get("overlap_sec")) for row in local_islands), 3)
    mixed_seconds = safe_float(label_seconds.get("mixed"))
    me_seconds = safe_float(label_seconds.get("me_dominant"))
    remote_seconds = safe_float(label_seconds.get("remote_dominant"))
    local_seconds = round(mixed_seconds + me_seconds, 3)
    duplicate_ratio = round(duplicate_seconds / duration, 6) if duration > 0 else 0.0
    remote_ratio = round(remote_seconds / duration, 6) if duration > 0 else 0.0
    local_island_ratio = round(local_island_seconds / duration, 6) if duration > 0 else 0.0
    if local_island_seconds >= 2.0 and remote_seconds <= local_seconds:
        label = "local_island_split_candidate"
        reason = "local-looking islands exist inside the mixed missing row"
    elif remote_seconds > local_seconds:
        label = "remote_dominant_mixed_not_rescuable"
        reason = "remote-dominant evidence outweighs local-looking evidence"
    elif duplicate_ratio >= 0.60:
        label = "duplicate_heavy_needs_speaker_evidence"
        reason = "most overlapping evidence is duplicate-overlapping remote"
    elif duration <= 1.5 or local_seconds <= 1.5:
        label = "short_low_value_tail"
        reason = "candidate is too short for a major live rescue focus"
    else:
        label = "needs_speaker_evidence"
        reason = "mixed evidence has no safe local island split signal"
    return {
        "label": label,
        "reason": reason,
        "local_island_seconds": local_island_seconds,
        "local_island_count": len(local_islands),
        "local_overlap_seconds": local_seconds,
        "remote_overlap_seconds": round(remote_seconds, 3),
        "duplicate_overlap_ratio": duplicate_ratio,
        "remote_overlap_ratio": remote_ratio,
        "local_island_ratio": local_island_ratio,
        "local_island_examples": [
            {
                "start": row.get("start"),
                "end": row.get("end"),
                "overlap_sec": row.get("overlap_sec"),
                "text": row.get("text"),
                "audio_mic_minus_remote_rms_db": row.get("audio_mic_minus_remote_rms_db"),
                "audio_mic_remote_zero_lag_abs_corr": row.get("audio_mic_remote_zero_lag_abs_corr"),
            }
            for row in local_islands[:5]
        ],
    }


def remaining_gap_actionability(item: dict[str, Any]) -> dict[str, Any]:
    bucket = str(item.get("bucket") or "")
    policies = item.get("target_me_candidate_policies") if isinstance(item.get("target_me_candidate_policies"), list) else []
    evidence_rows = item.get("suppressed_mic_evidence") if isinstance(item.get("suppressed_mic_evidence"), list) else []
    label_seconds = remaining_gap_evidence_label_seconds(item)
    dominant_label = remaining_gap_dominant_evidence_label(item)
    non_hallucination_evidence = [
        row
        for row in evidence_rows
        if isinstance(row, dict) and not bool(row.get("known_hallucination"))
    ]
    has_hallucination = any(
        isinstance(row, dict) and bool(row.get("known_hallucination"))
        for row in evidence_rows
    )
    has_local_label = any(label in label_seconds for label in ("me_dominant", "mixed"))
    has_remote_dominant = "remote_dominant" in label_seconds
    has_me_dominant = "me_dominant" in label_seconds
    has_mixed = "mixed" in label_seconds
    duplicate_seconds = sum(
        safe_float(row.get("overlap_sec"))
        for row in evidence_rows
        if isinstance(row, dict) and row.get("segment_gate_reason") == "segment_duplicates_overlapping_remote"
    )
    segmentability: dict[str, Any] | None = None
    if has_hallucination and not non_hallucination_evidence:
        label = "asr_hallucination_not_rescuable"
        reason = "only known hallucination evidence overlaps the missing batch Me row"
    elif bucket.startswith("not_visible"):
        label = "not_visible_needs_asr_or_boundary_repair"
        reason = "batch Me text is not visible in suppressed mic ASR"
    elif policies:
        label = "target_me_visible_needs_live_materialization_or_timeline_gate"
        reason = "broader Target-Me evidence exists but the best live-implementable profile did not recover it"
    elif has_mixed:
        label = "mixed_needs_segmentation_or_speaker_evidence"
        reason = "suppressed mic evidence is mixed; publishing it whole would risk remote content"
        segmentability = remaining_gap_mixed_segmentability(
            evidence_rows=non_hallucination_evidence,
            label_seconds=label_seconds,
            duplicate_seconds=duplicate_seconds,
            duration=safe_float(item.get("duration_sec")),
        )
    elif has_me_dominant and not has_remote_dominant:
        label = "speaker_confirmation_candidate"
        reason = "suppressed mic evidence is me-dominant but lacks Target-Me evidence"
    elif has_remote_dominant:
        label = "remote_dominant_not_rescuable_without_new_evidence"
        reason = "dominant suppressed mic evidence is remote-like"
    elif has_local_label:
        label = "local_label_needs_manual_or_new_evidence"
        reason = "local-looking evidence exists but is not covered by a trusted live rule"
    elif evidence_rows:
        label = "suppressed_evidence_not_actionable"
        reason = "suppressed mic evidence exists but has no trusted local signal"
    else:
        label = "missing_suppressed_evidence"
        reason = "no overlapping suppressed mic evidence was found"
    result = {
        "label": label,
        "reason": reason,
        "dominant_suppressed_label": dominant_label,
        "suppressed_label_overlap_seconds": label_seconds,
        "duplicate_overlap_seconds": round(duplicate_seconds, 3),
        "target_me_candidate_policy_count": len(policies),
    }
    if segmentability:
        result["segmentability"] = segmentability
    return result


def target_me_shadow_profile_remaining_gap_examples(
    rows: list[dict[str, Any]],
    *,
    root: Path,
    policy: str | None,
    limit: int = 80,
) -> dict[str, Any]:
    examples: list[dict[str, Any]] = []
    missing_inputs: list[str] = []
    if not policy:
        return {
            "schema": "murmurmark.live_target_me_shadow_profile_remaining_gap/v1",
            "status": "no_profile",
            "profile": None,
            "item_count": 0,
            "seconds": 0.0,
            "examples": [],
            "truncated": False,
            "limit": limit,
            "by_bucket": {},
            "by_policy_set": {},
            "by_session": {},
            "missing_inputs": [],
        }

    for row in rows:
        session_name = str(row.get("session") or "")
        inputs = row.get("inputs") if isinstance(row.get("inputs"), dict) else {}
        comparison_rel = inputs.get("live_batch_comparison")
        comparison_path = root / session_name / str(comparison_rel or "derived/live/live_batch_comparison.json")
        comparison = read_json(comparison_path)
        if not isinstance(comparison, dict):
            missing_inputs.append(session_name)
            continue
        shadow_profiles = comparison.get("shadow_profiles") if isinstance(comparison.get("shadow_profiles"), dict) else {}
        target_profiles = shadow_profiles.get("target_me") if isinstance(shadow_profiles.get("target_me"), dict) else {}
        profile = target_profiles.get(policy) if isinstance(target_profiles.get(policy), dict) else {}
        risk_examples = profile.get("risk_examples") if isinstance(profile.get("risk_examples"), dict) else {}
        for item in risk_examples.get("local_missing") or []:
            if not isinstance(item, dict):
                continue
            suppressed_recall = safe_float(item.get("recall_in_suppressed_mic"))
            policies = item.get("target_me_candidate_policies") if isinstance(item.get("target_me_candidate_policies"), list) else []
            if suppressed_recall >= 0.35 and policies:
                bucket = "visible_with_target_me"
            elif suppressed_recall >= 0.35:
                bucket = "visible_without_target_me"
            elif policies:
                bucket = "not_visible_with_target_me"
            else:
                bucket = "not_visible_without_target_me"
            policy_set = "+".join(str(policy_name) for policy_name in policies) if policies else "(none)"
            example = {
                "session": session_name,
                "profile": policy,
                "bucket": bucket,
                "policy_set": policy_set,
                "batch_id": item.get("batch_id"),
                "start": item.get("start"),
                "end": item.get("end"),
                "duration_sec": item.get("duration_sec"),
                "recall_in_live_me": item.get("recall_in_live_me"),
                "recall_in_suppressed_mic": item.get("recall_in_suppressed_mic"),
                "target_me_candidate_policies": policies,
                "suppressed_mic_turn_ids": item.get("suppressed_mic_turn_ids"),
                "suppressed_mic_evidence": item.get("suppressed_mic_evidence") or [],
                "text": item.get("text"),
                "comparison": str(comparison_rel or "derived/live/live_batch_comparison.json"),
            }
            example["actionability"] = remaining_gap_actionability(example)
            examples.append(example)

    def aggregate_by(key: str) -> dict[str, dict[str, Any]]:
        grouped: dict[str, dict[str, Any]] = {}
        for item in examples:
            group = str(item.get(key) or "")
            row = grouped.setdefault(group, {"count": 0, "seconds": 0.0})
            row["count"] += 1
            row["seconds"] = round(safe_float(row.get("seconds")) + safe_float(item.get("duration_sec")), 3)
        return dict(sorted(grouped.items(), key=lambda pair: (-safe_float(pair[1].get("seconds")), pair[0])))

    def aggregate_suppressed_policy_set() -> dict[str, dict[str, Any]]:
        grouped: dict[str, dict[str, Any]] = {}
        for item in examples:
            evidence_rows = item.get("suppressed_mic_evidence") if isinstance(item.get("suppressed_mic_evidence"), list) else []
            policies: set[str] = set()
            for evidence in evidence_rows:
                if not isinstance(evidence, dict):
                    continue
                for policy_name in evidence.get("rescue_policy_candidates") or []:
                    policies.add(str(policy_name))
            group = "+".join(sorted(policies)) if policies else "(none)"
            row = grouped.setdefault(group, {"count": 0, "seconds": 0.0})
            row["count"] += 1
            row["seconds"] = round(safe_float(row.get("seconds")) + safe_float(item.get("duration_sec")), 3)
        return dict(sorted(grouped.items(), key=lambda pair: (-safe_float(pair[1].get("seconds")), pair[0])))

    def aggregate_top_suppressed_evidence(field: str) -> dict[str, dict[str, Any]]:
        grouped: dict[str, dict[str, Any]] = {}
        for item in examples:
            evidence_rows = item.get("suppressed_mic_evidence") if isinstance(item.get("suppressed_mic_evidence"), list) else []
            top = evidence_rows[0] if evidence_rows and isinstance(evidence_rows[0], dict) else {}
            group = str(top.get(field) or "(none)")
            row = grouped.setdefault(group, {"count": 0, "seconds": 0.0})
            row["count"] += 1
            row["seconds"] = round(safe_float(row.get("seconds")) + safe_float(item.get("duration_sec")), 3)
        return dict(sorted(grouped.items(), key=lambda pair: (-safe_float(pair[1].get("seconds")), pair[0])))

    def aggregate_by_actionability() -> dict[str, dict[str, Any]]:
        grouped: dict[str, dict[str, Any]] = {}
        for item in examples:
            actionability = item.get("actionability") if isinstance(item.get("actionability"), dict) else {}
            group = str(actionability.get("label") or "(none)")
            row = grouped.setdefault(group, {"count": 0, "seconds": 0.0})
            row["count"] += 1
            row["seconds"] = round(safe_float(row.get("seconds")) + safe_float(item.get("duration_sec")), 3)
        return dict(sorted(grouped.items(), key=lambda pair: (-safe_float(pair[1].get("seconds")), pair[0])))

    def aggregate_by_segmentability() -> dict[str, dict[str, Any]]:
        grouped: dict[str, dict[str, Any]] = {}
        for item in examples:
            actionability = item.get("actionability") if isinstance(item.get("actionability"), dict) else {}
            segmentability = (
                actionability.get("segmentability")
                if isinstance(actionability.get("segmentability"), dict)
                else {}
            )
            group = str(segmentability.get("label") or "")
            if not group:
                continue
            row = grouped.setdefault(
                group,
                {
                    "count": 0,
                    "seconds": 0.0,
                    "local_island_seconds": 0.0,
                    "local_island_count": 0,
                },
            )
            row["count"] += 1
            row["seconds"] = round(safe_float(row.get("seconds")) + safe_float(item.get("duration_sec")), 3)
            row["local_island_seconds"] = round(
                safe_float(row.get("local_island_seconds"))
                + safe_float(segmentability.get("local_island_seconds")),
                3,
            )
            row["local_island_count"] = safe_int(row.get("local_island_count")) + safe_int(
                segmentability.get("local_island_count")
            )
        return dict(sorted(grouped.items(), key=lambda pair: (-safe_float(pair[1].get("seconds")), pair[0])))

    examples.sort(
        key=lambda item: (
            -safe_float(item.get("duration_sec")),
            str(item.get("session") or ""),
            safe_float(item.get("start")),
        )
    )
    total_seconds = round(sum(safe_float(item.get("duration_sec")) for item in examples), 3)
    return {
        "schema": "murmurmark.live_target_me_shadow_profile_remaining_gap/v1",
        "status": "ok",
        "profile": policy,
        "item_count": len(examples),
        "seconds": total_seconds,
        "examples": examples[:limit],
        "truncated": len(examples) > limit,
        "limit": limit,
        "by_bucket": aggregate_by("bucket"),
        "by_policy_set": aggregate_by("policy_set"),
        "by_session": aggregate_by("session"),
        "by_actionability": aggregate_by_actionability(),
        "by_segmentability": aggregate_by_segmentability(),
        "by_suppressed_policy_set": aggregate_suppressed_policy_set(),
        "by_suppressed_gate_reason": aggregate_top_suppressed_evidence("segment_gate_reason"),
        "by_suppressed_batch_role_label": aggregate_top_suppressed_evidence("batch_role_label"),
        "missing_inputs": missing_inputs,
    }


def _order_turn_feature(turn: dict[str, Any] | None, key: str) -> Any:
    if not isinstance(turn, dict):
        return None
    return turn.get(key)


def live_order_risk_triage_label(item: dict[str, Any]) -> dict[str, Any]:
    previous = item.get("previous") if isinstance(item.get("previous"), dict) else {}
    current = item.get("current") if isinstance(item.get("current"), dict) else {}
    previous_tokens = safe_int(_order_turn_feature(previous, "turn_content_token_count"))
    current_tokens = safe_int(_order_turn_feature(current, "turn_content_token_count"))
    previous_margin = safe_float(_order_turn_feature(previous, "score_margin"), 1.0)
    current_margin = safe_float(_order_turn_feature(current, "score_margin"), 1.0)
    min_margin = min(previous_margin, current_margin)
    previous_plausible = safe_int(_order_turn_feature(previous, "plausible_match_count"))
    current_plausible = safe_int(_order_turn_feature(current, "plausible_match_count"))
    max_plausible = max(previous_plausible, current_plausible)
    min_tokens = min(previous_tokens or 999, current_tokens or 999)
    same_source = bool(item.get("same_source"))
    same_chunk = bool(item.get("same_chunk"))
    ambiguous = (
        str(item.get("match_ambiguity") or "") == "ambiguous"
        or bool(_order_turn_feature(previous, "ambiguous_match"))
        or bool(_order_turn_feature(current, "ambiguous_match"))
    )
    batch_delta = abs(safe_float(item.get("batch_start_delta_sec")))
    live_delta = abs(safe_float(item.get("live_start_delta_sec")))
    previous_inside = bool(item.get("previous_live_inside_own_batch_interval"))
    current_inside = bool(item.get("current_live_inside_own_batch_interval"))
    source_pair = str(item.get("source_pair") or "")

    label = "needs_review_order_risk"
    severity = "blocking"
    confidence = "medium"
    reason = "order risk remains contentful and needs manual or algorithmic repair"

    if ambiguous and min_margin <= 0.15 and max_plausible >= 8:
        label = "weak_generic_match_false_positive_candidate"
        severity = "advisory"
        confidence = "high"
        reason = "match is ambiguous, has small score margin, and many plausible alternatives"
    elif same_source and min_tokens <= 2 and max_plausible >= 5 and min_margin <= 0.25 and batch_delta >= 20:
        label = "same_source_weak_short_match_candidate"
        severity = "advisory"
        confidence = "medium"
        reason = "same-source reorder is driven by a very short live phrase matched far away in batch"
    elif same_chunk and not same_source and live_delta <= 8 and (not previous_inside or not current_inside):
        label = "boundary_retime_candidate"
        severity = "blocking"
        confidence = "medium"
        reason = "cross-source order risk is near a chunk boundary and at least one live turn is outside its batch interval"
    elif same_chunk and same_source and batch_delta >= 20:
        label = "same_source_timeline_reorder_candidate"
        severity = "blocking"
        confidence = "medium"
        reason = "same-source order risk has a large batch-time reversal that is not explained by weak-match rules"
    elif "mic_segment" in source_pair and "remote_segment" in source_pair:
        label = "cross_source_order_risk"
        severity = "blocking"
        confidence = "medium"
        reason = "mic/remote order risk remains after current live profile"

    return {
        "label": label,
        "severity": severity,
        "confidence": confidence,
        "reason": reason,
        "features": {
            "same_chunk": same_chunk,
            "same_source": same_source,
            "source_pair": source_pair,
            "ambiguous_match": ambiguous,
            "min_score_margin": round(min_margin, 6),
            "max_plausible_match_count": max_plausible,
            "min_turn_content_token_count": 0 if min_tokens == 999 else min_tokens,
            "batch_start_delta_abs_sec": round(batch_delta, 3),
            "live_start_delta_abs_sec": round(live_delta, 3),
            "previous_live_inside_own_batch_interval": previous_inside,
            "current_live_inside_own_batch_interval": current_inside,
        },
    }


def live_order_risk_triage(
    rows: list[dict[str, Any]],
    *,
    root: Path,
    policy: str | None,
    limit: int = 80,
) -> dict[str, Any]:
    examples: list[dict[str, Any]] = []
    missing_inputs: list[str] = []
    if not policy:
        return {
            "schema": "murmurmark.live_order_risk_triage/v1",
            "status": "no_profile",
            "profile": None,
            "item_count": 0,
            "blocking_count": 0,
            "advisory_count": 0,
            "examples": [],
            "truncated": False,
            "limit": limit,
            "by_label": {},
            "by_severity": {},
            "by_session": {},
            "missing_inputs": [],
        }

    for row in rows:
        session_name = str(row.get("session") or "")
        inputs = row.get("inputs") if isinstance(row.get("inputs"), dict) else {}
        comparison_rel = inputs.get("live_batch_comparison")
        comparison_path = root / session_name / str(comparison_rel or "derived/live/live_batch_comparison.json")
        comparison = read_json(comparison_path)
        if not isinstance(comparison, dict):
            missing_inputs.append(session_name)
            continue
        shadow_profiles = comparison.get("shadow_profiles") if isinstance(comparison.get("shadow_profiles"), dict) else {}
        target_profiles = shadow_profiles.get("target_me") if isinstance(shadow_profiles.get("target_me"), dict) else {}
        profile = target_profiles.get(policy) if isinstance(target_profiles.get(policy), dict) else {}
        risk_examples = profile.get("risk_examples") if isinstance(profile.get("risk_examples"), dict) else {}
        for item in risk_examples.get("contentful_role_constrained_order_mismatches") or []:
            if not isinstance(item, dict):
                continue
            triage = live_order_risk_triage_label(item)
            previous = item.get("previous") if isinstance(item.get("previous"), dict) else {}
            current = item.get("current") if isinstance(item.get("current"), dict) else {}
            examples.append(
                {
                    "session": session_name,
                    "profile": policy,
                    "label": triage.get("label"),
                    "severity": triage.get("severity"),
                    "confidence": triage.get("confidence"),
                    "reason": triage.get("reason"),
                    "features": triage.get("features"),
                    "category": item.get("category"),
                    "primary_risk": item.get("primary_risk"),
                    "match_ambiguity": item.get("match_ambiguity"),
                    "source_pair": item.get("source_pair"),
                    "role_pair": item.get("role_pair"),
                    "live_start_delta_sec": item.get("live_start_delta_sec"),
                    "batch_start_delta_sec": item.get("batch_start_delta_sec"),
                    "previous_live_id": item.get("previous_live_id"),
                    "current_live_id": item.get("current_live_id"),
                    "previous_live_start": item.get("previous_live_start"),
                    "current_live_start": item.get("current_live_start"),
                    "previous_batch_start": item.get("previous_batch_start"),
                    "current_batch_start": item.get("current_batch_start"),
                    "previous_text": previous.get("text"),
                    "current_text": current.get("text"),
                    "previous_batch_id": previous.get("batch_id"),
                    "current_batch_id": current.get("batch_id"),
                    "previous_score_margin": previous.get("score_margin"),
                    "current_score_margin": current.get("score_margin"),
                    "previous_plausible_match_count": previous.get("plausible_match_count"),
                    "current_plausible_match_count": current.get("plausible_match_count"),
                    "comparison": str(comparison_rel or "derived/live/live_batch_comparison.json"),
                }
            )

    def aggregate_by(key: str) -> dict[str, dict[str, Any]]:
        grouped: dict[str, dict[str, Any]] = {}
        for item in examples:
            group = str(item.get(key) or "(none)")
            row = grouped.setdefault(group, {"count": 0})
            row["count"] += 1
        return dict(sorted(grouped.items(), key=lambda pair: (-safe_int(pair[1].get("count")), pair[0])))

    examples.sort(
        key=lambda item: (
            0 if item.get("severity") == "blocking" else 1,
            str(item.get("label") or ""),
            str(item.get("session") or ""),
            safe_float(item.get("current_live_start")),
        )
    )
    blocking_count = sum(1 for item in examples if item.get("severity") == "blocking")
    advisory_count = sum(1 for item in examples if item.get("severity") == "advisory")
    return {
        "schema": "murmurmark.live_order_risk_triage/v1",
        "status": "ok",
        "profile": policy,
        "item_count": len(examples),
        "blocking_count": blocking_count,
        "advisory_count": advisory_count,
        "likely_false_positive_count": sum(
            1
            for item in examples
            if item.get("label")
            in {
                "weak_generic_match_false_positive_candidate",
                "same_source_weak_short_match_candidate",
            }
        ),
        "boundary_retime_candidate_count": sum(
            1 for item in examples if item.get("label") == "boundary_retime_candidate"
        ),
        "examples": examples[:limit],
        "truncated": len(examples) > limit,
        "limit": limit,
        "by_label": aggregate_by("label"),
        "by_severity": aggregate_by("severity"),
        "by_session": aggregate_by("session"),
        "missing_inputs": missing_inputs,
        "interpretation": (
            "diagnostic only: strict order gates remain unchanged; advisory rows are candidates "
            "for future matcher refinement, while blocking rows still require repair or stronger evidence"
        ),
    }


def local_island_split_lab(remaining_gap: dict[str, Any], *, recall_threshold: float = 0.35) -> dict[str, Any]:
    examples = remaining_gap.get("examples") if isinstance(remaining_gap.get("examples"), list) else []
    rows: list[dict[str, Any]] = []
    for item in examples:
        if not isinstance(item, dict):
            continue
        actionability = item.get("actionability") if isinstance(item.get("actionability"), dict) else {}
        segmentability = (
            actionability.get("segmentability")
            if isinstance(actionability.get("segmentability"), dict)
            else {}
        )
        if segmentability.get("label") != "local_island_split_candidate":
            continue
        islands = (
            segmentability.get("local_island_examples")
            if isinstance(segmentability.get("local_island_examples"), list)
            else []
        )
        island_text = " ".join(str(row.get("text") or "") for row in islands if isinstance(row, dict))
        batch_tokens = text_tokens(str(item.get("text") or ""))
        island_tokens = text_tokens(island_text)
        token_recall = bag_recall(batch_tokens, island_tokens)
        accepted = token_recall >= recall_threshold
        island_seconds = safe_float(segmentability.get("local_island_seconds"))
        row = {
            "session": item.get("session"),
            "batch_id": item.get("batch_id"),
            "start": item.get("start"),
            "end": item.get("end"),
            "duration_sec": item.get("duration_sec"),
            "accepted": accepted,
            "reason": "token_recall_passed" if accepted else "token_recall_below_threshold",
            "token_recall_from_local_islands": round(token_recall, 6),
            "recall_threshold": recall_threshold,
            "local_island_seconds": island_seconds,
            "local_island_count": safe_int(segmentability.get("local_island_count")),
            "local_island_text": island_text,
            "batch_text": item.get("text"),
            "local_island_examples": islands,
        }
        rows.append(row)

    accepted_rows = [row for row in rows if row.get("accepted")]
    rejected_rows = [row for row in rows if not row.get("accepted")]

    def sum_field(items: list[dict[str, Any]], field: str) -> float:
        return round(sum(safe_float(row.get(field)) for row in items), 3)

    by_session: dict[str, dict[str, Any]] = {}
    for row in rows:
        session = str(row.get("session") or "")
        session_row = by_session.setdefault(
            session,
            {
                "count": 0,
                "batch_seconds": 0.0,
                "accepted_count": 0,
                "accepted_batch_seconds": 0.0,
                "accepted_local_island_seconds": 0.0,
            },
        )
        session_row["count"] += 1
        session_row["batch_seconds"] = round(
            safe_float(session_row.get("batch_seconds")) + safe_float(row.get("duration_sec")),
            3,
        )
        if row.get("accepted"):
            session_row["accepted_count"] += 1
            session_row["accepted_batch_seconds"] = round(
                safe_float(session_row.get("accepted_batch_seconds")) + safe_float(row.get("duration_sec")),
                3,
            )
            session_row["accepted_local_island_seconds"] = round(
                safe_float(session_row.get("accepted_local_island_seconds"))
                + safe_float(row.get("local_island_seconds")),
                3,
            )

    return {
        "schema": "murmurmark.live_local_island_split_lab/v1",
        "status": "ok" if rows else "no_candidates",
        "promotion_allowed": False,
        "interpretation": (
            "diagnostic estimate only: rows accepted here need a real split profile and parity checks "
            "before any live promotion"
        ),
        "recall_threshold": recall_threshold,
        "candidate_count": len(rows),
        "candidate_batch_seconds": sum_field(rows, "duration_sec"),
        "candidate_local_island_seconds": sum_field(rows, "local_island_seconds"),
        "accepted_count": len(accepted_rows),
        "accepted_batch_seconds": sum_field(accepted_rows, "duration_sec"),
        "accepted_local_island_seconds": sum_field(accepted_rows, "local_island_seconds"),
        "rejected_count": len(rejected_rows),
        "rejected_batch_seconds": sum_field(rejected_rows, "duration_sec"),
        "by_session": dict(
            sorted(by_session.items(), key=lambda pair: (-safe_float(pair[1].get("batch_seconds")), pair[0]))
        ),
        "examples": sorted(
            rows,
            key=lambda row: (
                not bool(row.get("accepted")),
                -safe_float(row.get("duration_sec")),
                str(row.get("session") or ""),
                safe_float(row.get("start")),
            ),
        ),
    }


def largest_group(grouped: dict[str, Any]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for label, payload in grouped.items():
        if not isinstance(payload, dict):
            continue
        rows.append(
            {
                "label": label,
                "count": safe_int(payload.get("count")),
                "seconds": round(safe_float(payload.get("seconds")), 3),
            }
        )
    rows.sort(key=lambda row: (-safe_float(row.get("seconds")), str(row.get("label") or "")))
    return rows[0] if rows else {"label": "(none)", "count": 0, "seconds": 0.0}


def live_next_unlock_report(
    *,
    summary: dict[str, Any],
    remaining_gap: dict[str, Any],
    order_risk_triage: dict[str, Any],
    speaker_boundary_lab: dict[str, Any],
    mixed_voice_coverage_lab: dict[str, Any],
    tight_voice_remote_guard_lab: dict[str, Any],
    local_island_split: dict[str, Any],
    live_only_local_island: dict[str, Any],
    timing_gap: dict[str, Any],
) -> dict[str, Any]:
    by_actionability = (
        remaining_gap.get("by_actionability")
        if isinstance(remaining_gap.get("by_actionability"), dict)
        else {}
    )
    by_segmentability = (
        remaining_gap.get("by_segmentability")
        if isinstance(remaining_gap.get("by_segmentability"), dict)
        else {}
    )
    top_actionability = largest_group(by_actionability)
    top_segmentability = largest_group(by_segmentability)

    missing_me_seconds = safe_float(remaining_gap.get("seconds"))
    remote_leak_seconds = safe_float(
        summary.get("real_live_target_me_shadow_profile_best_live_implementable_remote_leak_seconds")
    )
    contentful_order_mismatches = safe_int(
        summary.get("real_live_target_me_shadow_profile_best_live_implementable_contentful_order_mismatch_count")
    )
    live_sessions_remaining = safe_int(summary.get("coverage_target_live_sessions_remaining"))
    meaningful_remaining = safe_int(summary.get("coverage_target_meaningful_compared_sessions_remaining"))
    passing_remaining = safe_int(summary.get("coverage_target_passing_sessions_remaining"))
    new_recordings_needed = live_sessions_remaining > 0 or meaningful_remaining > 0

    blockers: list[dict[str, Any]] = []
    if missing_me_seconds > 0:
        blockers.append(
            {
                "dimension": "local_recall",
                "severity": "blocking",
                "seconds": round(missing_me_seconds, 3),
                "reason": "best live-implementable profile still misses batch Me speech",
            }
        )
    if contentful_order_mismatches > 0:
        blockers.append(
            {
                "dimension": "order_risk",
                "severity": "blocking",
                "count": contentful_order_mismatches,
                "reason": "contentful same-role order mismatches remain in live/batch comparison",
            }
        )
    if remote_leak_seconds > 0:
        blockers.append(
            {
                "dimension": "remote_leakage",
                "severity": "blocking",
                "seconds": round(remote_leak_seconds, 3),
                "reason": "remote-like text remains published as live Me",
            }
        )

    mixed_seconds = safe_float(
        (by_actionability.get("mixed_needs_segmentation_or_speaker_evidence") or {}).get("seconds")
    )
    speaker_seconds = safe_float((by_actionability.get("speaker_confirmation_candidate") or {}).get("seconds"))
    remote_dominant_seconds = safe_float(
        (by_actionability.get("remote_dominant_not_rescuable_without_new_evidence") or {}).get("seconds")
    )
    hallucination_seconds = safe_float((by_actionability.get("asr_hallucination_not_rescuable") or {}).get("seconds"))
    local_island_candidate_seconds = safe_float(local_island_split.get("candidate_batch_seconds"))
    local_island_accepted_seconds = safe_float(local_island_split.get("accepted_batch_seconds"))
    live_only_candidate_seconds = safe_float(live_only_local_island.get("candidate_seconds"))
    live_only_remote_risk_seconds = safe_float(live_only_local_island.get("remote_risk_seconds"))
    strict_profile = (
        live_only_local_island.get("stricter_profiles", {}).get("strict_zero_remote_risk_text_audio_v1")
        if isinstance(live_only_local_island.get("stricter_profiles"), dict)
        else {}
    )
    strict_zero_remote_seconds = (
        safe_float(strict_profile.get("candidate_seconds")) if isinstance(strict_profile, dict) else 0.0
    )
    oracle_gain = safe_float(timing_gap.get("retime_gain_vs_best_live_implementable_seconds"))
    order_triage_blocking_count = safe_int(order_risk_triage.get("blocking_count"))
    order_triage_advisory_count = safe_int(order_risk_triage.get("advisory_count"))
    order_triage_boundary_count = safe_int(order_risk_triage.get("boundary_retime_candidate_count"))
    order_triage_likely_false_positive_count = safe_int(order_risk_triage.get("likely_false_positive_count"))
    boundary_order_retime_oracle_missing = safe_float(
        summary.get("real_live_boundary_order_retime_oracle_profile_missing_me_seconds")
    )
    boundary_order_retime_oracle_missing_delta = safe_float(
        summary.get("real_live_boundary_order_retime_oracle_profile_missing_me_delta_vs_best_live_implementable_seconds")
    )
    boundary_order_retime_oracle_order_count = safe_int(
        summary.get("real_live_boundary_order_retime_oracle_profile_contentful_order_mismatch_count")
    )
    boundary_order_retime_oracle_turn_count = safe_int(
        summary.get("real_live_boundary_order_retime_oracle_profile_retimed_turn_count")
    )
    boundary_order_retime_oracle_trimmed = safe_float(
        summary.get("real_live_boundary_order_retime_oracle_profile_retimed_trimmed_seconds")
    )
    boundary_order_split_retime_oracle_missing = safe_float(
        summary.get("real_live_boundary_order_split_retime_oracle_profile_missing_me_seconds")
    )
    boundary_order_split_retime_oracle_missing_delta = safe_float(
        summary.get(
            "real_live_boundary_order_split_retime_oracle_profile_missing_me_delta_vs_best_live_implementable_seconds"
        )
    )
    boundary_order_split_retime_oracle_order_count = safe_int(
        summary.get("real_live_boundary_order_split_retime_oracle_profile_contentful_order_mismatch_count")
    )
    boundary_order_split_retime_oracle_turn_count = safe_int(
        summary.get("real_live_boundary_order_split_retime_oracle_profile_retimed_turn_count")
    )
    boundary_order_split_retime_oracle_trimmed = safe_float(
        summary.get("real_live_boundary_order_split_retime_oracle_profile_retimed_trimmed_seconds")
    )
    boundary_order_split_retime_preserved_prefix_count = safe_int(
        summary.get("real_live_boundary_order_split_retime_oracle_profile_preserved_prefix_count")
    )
    boundary_order_split_retime_preserved_prefix_seconds = safe_float(
        summary.get("real_live_boundary_order_split_retime_oracle_profile_preserved_prefix_seconds")
    )
    mixed_voice_next = (
        mixed_voice_coverage_lab.get("recommended_next")
        if isinstance(mixed_voice_coverage_lab.get("recommended_next"), dict)
        else {}
    )
    tight_voice_next = (
        tight_voice_remote_guard_lab.get("recommended_next")
        if isinstance(tight_voice_remote_guard_lab.get("recommended_next"), dict)
        else {}
    )

    next_actions: list[dict[str, Any]] = []
    if order_triage_boundary_count > 0:
        next_actions.append(
            {
                "id": "repair_live_boundary_retime_order_risk",
                "priority": 1,
                "scope_count": order_triage_boundary_count,
                "why": "remaining contentful order risks include mic/remote boundary-retime candidates",
                "oracle_evidence": {
                    "retimed_turn_count": boundary_order_retime_oracle_turn_count,
                    "retimed_trimmed_seconds": round(boundary_order_retime_oracle_trimmed, 3),
                    "contentful_order_mismatch_count_after": boundary_order_retime_oracle_order_count,
                    "missing_me_delta_seconds": round(boundary_order_retime_oracle_missing_delta, 3),
                },
                "split_oracle_evidence": {
                    "retimed_turn_count": boundary_order_split_retime_oracle_turn_count,
                    "retimed_trimmed_seconds": round(boundary_order_split_retime_oracle_trimmed, 3),
                    "preserved_prefix_count": boundary_order_split_retime_preserved_prefix_count,
                    "preserved_prefix_seconds": round(boundary_order_split_retime_preserved_prefix_seconds, 3),
                    "contentful_order_mismatch_count_after": boundary_order_split_retime_oracle_order_count,
                    "missing_me_delta_seconds": round(boundary_order_split_retime_oracle_missing_delta, 3),
                },
                "must_preserve": ["remote_leakage == 0", "strict order gates must remain authoritative"],
            }
        )
    if mixed_seconds > 0:
        mixed_voice_action: dict[str, Any] | None = None
        if mixed_voice_next:
            action_id = str(mixed_voice_next.get("id") or "inspect_mixed_voice_coverage")
            action_why = str(mixed_voice_next.get("why") or "mixed speech needs speaker/boundary evidence")
            if action_id == "tighten_voice_remote_guard_for_mixed_rows" and tight_voice_next:
                action_id = str(tight_voice_next.get("id") or action_id)
                action_why = str(tight_voice_next.get("why") or action_why)
            mixed_voice_action = {
                "id": action_id,
                "priority": 2,
                "scope_seconds": round(mixed_seconds, 3),
                "why": action_why,
                "voice_coverage": {
                    "status": mixed_voice_coverage_lab.get("status"),
                    "publication_candidate_seconds": round(
                        safe_float(mixed_voice_coverage_lab.get("publication_candidate_seconds")),
                        3,
                    ),
                    "no_target_me_audit_seconds": round(
                        safe_float(mixed_voice_coverage_lab.get("no_target_me_audit_seconds")),
                        3,
                    ),
                    "no_voice_overlap_seconds": round(
                        safe_float(mixed_voice_coverage_lab.get("no_voice_overlap_seconds")),
                        3,
                    ),
                    "target_me_enrollment_not_ready_seconds": round(
                        safe_float(mixed_voice_coverage_lab.get("target_me_enrollment_not_ready_seconds")),
                        3,
                    ),
                    "weak_or_ambiguous_seconds": round(
                        safe_float(mixed_voice_coverage_lab.get("weak_or_ambiguous_seconds")),
                        3,
                    ),
                },
                "tight_voice_remote_guard": {
                    "status": tight_voice_remote_guard_lab.get("status"),
                    "candidate_seconds": round(
                        safe_float(tight_voice_remote_guard_lab.get("candidate_seconds")),
                        3,
                    ),
                    "blocked_seconds": round(
                        safe_float(tight_voice_remote_guard_lab.get("blocked_seconds")),
                        3,
                    ),
                    "top_blocker": tight_voice_remote_guard_lab.get("top_blocker"),
                },
                "must_preserve": ["remote_leakage == 0", "contentful_order_mismatches must not increase"],
            }
        next_actions.append(
            mixed_voice_action
            or {
                "id": "build_online_local_speaker_boundary_evidence",
                "priority": 2,
                "scope_seconds": round(mixed_seconds, 3),
                "why": "largest actionable remaining bucket is mixed speech that needs segmentation or speaker evidence",
                "must_preserve": ["remote_leakage == 0", "contentful_order_mismatches must not increase"],
            }
        )
    if speaker_seconds > 0:
        next_actions.append(
            {
                "id": "confirm_speaker_confirmation_candidates",
                "priority": 3,
                "scope_seconds": round(speaker_seconds, 3),
                "why": "me-dominant suppressed-mic rows lack Target-Me evidence and need confirmation before publication",
            }
        )
    if local_island_candidate_seconds > 0:
        next_actions.append(
            {
                "id": "improve_local_island_candidate_selection",
                "priority": 4,
                "scope_seconds": round(local_island_candidate_seconds, 3),
                "accepted_seconds": round(local_island_accepted_seconds, 3),
                "why": "current local-island candidate exists, but token recall does not pass the publication threshold",
            }
        )
    if strict_zero_remote_seconds > 0:
        next_actions.append(
            {
                "id": "reuse_strict_zero_remote_evidence_without_broad_publication",
                "priority": 5,
                "scope_seconds": round(strict_zero_remote_seconds, 3),
                "why": "strict live-only candidates are zero-remote-risk under batch evaluation, but not enough after deduplication",
            }
        )
    if order_triage_likely_false_positive_count > 0:
        next_actions.append(
            {
                "id": "tighten_order_matcher_for_short_generic_phrases",
                "priority": 6,
                "scope_count": order_triage_likely_false_positive_count,
                "why": "some order-risk rows look like short/generic weak-match false positives",
                "must_preserve": ["do not lower strict order gate before regression evidence"],
            }
        )

    blocked_buckets = []
    if remote_dominant_seconds > 0:
        blocked_buckets.append(
            {
                "id": "remote_dominant_without_new_evidence",
                "seconds": round(remote_dominant_seconds, 3),
                "reason": "do not publish without stronger speaker evidence",
            }
        )
    if hallucination_seconds > 0:
        blocked_buckets.append(
            {
                "id": "known_hallucination",
                "seconds": round(hallucination_seconds, 3),
                "reason": "must stay excluded from rescue",
            }
        )

    return {
        "schema": "murmurmark.live_next_unlock/v1",
        "status": "needs_quality_work" if blockers else "ready_for_promotion_review",
        "batch_authoritative": True,
        "promotion_allowed": False,
        "promotion_decision": summary.get("promotion_decision"),
        "additional_recordings_required_for_current_blocker": new_recordings_needed,
        "new_real_live_collection_allowed": bool(summary.get("new_real_live_collection_allowed")),
        "controlled_real_live_pilot_allowed": bool(summary.get("controlled_real_live_pilot_allowed")),
        "coverage": {
            "live_sessions_remaining": live_sessions_remaining,
            "meaningful_compared_sessions_remaining": meaningful_remaining,
            "passing_compared_sessions_remaining": passing_remaining,
        },
        "best_live_implementable": {
            "policy": remaining_gap.get("profile"),
            "missing_me_seconds": round(missing_me_seconds, 3),
            "remote_leak_seconds": round(remote_leak_seconds, 3),
            "contentful_order_mismatch_count": contentful_order_mismatches,
            "remaining_gap_count": safe_int(remaining_gap.get("item_count")),
        },
        "top_actionability": top_actionability,
        "top_segmentability": top_segmentability,
        "speaker_boundary_evidence": {
            "status": speaker_boundary_lab.get("status"),
            "shadow_probe_seconds": round(safe_float(speaker_boundary_lab.get("shadow_probe_seconds")), 3),
            "publication_ready_seconds": round(safe_float(speaker_boundary_lab.get("publication_ready_seconds")), 3),
            "blocked_seconds": round(safe_float(speaker_boundary_lab.get("blocked_seconds")), 3),
        },
        "mixed_speaker_boundary_voice_coverage": {
            "status": mixed_voice_coverage_lab.get("status"),
            "publication_candidate_seconds": round(
                safe_float(mixed_voice_coverage_lab.get("publication_candidate_seconds")),
                3,
            ),
            "no_target_me_audit_seconds": round(safe_float(mixed_voice_coverage_lab.get("no_target_me_audit_seconds")), 3),
            "no_voice_overlap_seconds": round(safe_float(mixed_voice_coverage_lab.get("no_voice_overlap_seconds")), 3),
            "target_me_enrollment_not_ready_seconds": round(
                safe_float(mixed_voice_coverage_lab.get("target_me_enrollment_not_ready_seconds")),
                3,
            ),
            "weak_or_ambiguous_seconds": round(safe_float(mixed_voice_coverage_lab.get("weak_or_ambiguous_seconds")), 3),
            "recommended_next": mixed_voice_next,
        },
        "oracle_gap": {
            "diagnostic_gain_seconds": round(oracle_gain, 3),
            "requires_batch_timing": bool(timing_gap.get("requires_batch_timing")),
            "requires_batch_role_labels": bool(timing_gap.get("requires_batch_role_labels")),
        },
        "order_risk_triage": {
            "status": order_risk_triage.get("status"),
            "item_count": safe_int(order_risk_triage.get("item_count")),
            "blocking_count": order_triage_blocking_count,
            "advisory_count": order_triage_advisory_count,
            "boundary_retime_candidate_count": order_triage_boundary_count,
            "likely_false_positive_count": order_triage_likely_false_positive_count,
            "by_label": order_risk_triage.get("by_label") if isinstance(order_risk_triage.get("by_label"), dict) else {},
        },
        "boundary_order_retime_oracle": {
            "profile": summary.get("real_live_boundary_order_retime_oracle_profile_policy"),
            "diagnostic_only": True,
            "missing_me_seconds": round(boundary_order_retime_oracle_missing, 3),
            "missing_me_delta_vs_best_live_implementable_seconds": round(boundary_order_retime_oracle_missing_delta, 3),
            "remote_leak_seconds": round(
                safe_float(summary.get("real_live_boundary_order_retime_oracle_profile_remote_leak_seconds")),
                3,
            ),
            "contentful_order_mismatch_count": boundary_order_retime_oracle_order_count,
            "retimed_turn_count": boundary_order_retime_oracle_turn_count,
            "retimed_trimmed_seconds": round(boundary_order_retime_oracle_trimmed, 3),
        },
        "boundary_order_split_retime_oracle": {
            "profile": summary.get("real_live_boundary_order_split_retime_oracle_profile_policy"),
            "diagnostic_only": True,
            "missing_me_seconds": round(boundary_order_split_retime_oracle_missing, 3),
            "missing_me_delta_vs_best_live_implementable_seconds": round(
                boundary_order_split_retime_oracle_missing_delta,
                3,
            ),
            "remote_leak_seconds": round(
                safe_float(summary.get("real_live_boundary_order_split_retime_oracle_profile_remote_leak_seconds")),
                3,
            ),
            "contentful_order_mismatch_count": boundary_order_split_retime_oracle_order_count,
            "retimed_turn_count": boundary_order_split_retime_oracle_turn_count,
            "retimed_trimmed_seconds": round(boundary_order_split_retime_oracle_trimmed, 3),
            "preserved_prefix_count": boundary_order_split_retime_preserved_prefix_count,
            "preserved_prefix_seconds": round(boundary_order_split_retime_preserved_prefix_seconds, 3),
        },
        "live_only_evidence": {
            "candidate_seconds": round(live_only_candidate_seconds, 3),
            "remote_risk_seconds": round(live_only_remote_risk_seconds, 3),
            "strict_zero_remote_candidate_seconds": round(strict_zero_remote_seconds, 3),
        },
        "blockers": blockers,
        "next_actions": next_actions,
        "blocked_buckets": blocked_buckets,
    }


def live_speaker_boundary_evidence_lab(remaining_gap: dict[str, Any], *, limit: int = 80) -> dict[str, Any]:
    examples = remaining_gap.get("examples") if isinstance(remaining_gap.get("examples"), list) else []
    rows: list[dict[str, Any]] = []

    for item in examples:
        if not isinstance(item, dict):
            continue
        actionability = item.get("actionability") if isinstance(item.get("actionability"), dict) else {}
        segmentability = (
            actionability.get("segmentability")
            if isinstance(actionability.get("segmentability"), dict)
            else {}
        )
        label = str(actionability.get("label") or "(none)")
        segment_label = str(segmentability.get("label") or "")
        suppressed_labels = (
            actionability.get("suppressed_label_overlap_seconds")
            if isinstance(actionability.get("suppressed_label_overlap_seconds"), dict)
            else {}
        )
        local_like_seconds = round(
            safe_float(suppressed_labels.get("me_dominant"))
            + safe_float(suppressed_labels.get("mixed")),
            3,
        )
        remote_like_seconds = round(safe_float(suppressed_labels.get("remote_dominant")), 3)
        duration_sec = safe_float(item.get("duration_sec"))
        duplicate_seconds = safe_float(actionability.get("duplicate_overlap_seconds"))
        local_island_seconds = safe_float(segmentability.get("local_island_seconds"))
        local_island_count = safe_int(segmentability.get("local_island_count"))

        classification = "needs_human_design"
        candidate_kind = "blocked"
        reason = "unclassified remaining-gap row"
        if label == "speaker_confirmation_candidate":
            classification = "speaker_confirmation_shadow_candidate"
            candidate_kind = "shadow_probe_candidate"
            reason = "me-dominant suppressed mic evidence without Target-Me evidence"
        elif label == "mixed_needs_segmentation_or_speaker_evidence":
            if segment_label == "local_island_split_candidate":
                classification = "local_island_boundary_probe_candidate"
                candidate_kind = "shadow_probe_candidate"
                reason = "local-looking island exists inside mixed row, but text recall is insufficient"
            elif segment_label == "needs_speaker_evidence":
                classification = "mixed_speaker_boundary_probe_candidate"
                candidate_kind = "shadow_probe_candidate"
                reason = "mixed row has some local-looking evidence and needs speaker/boundary confirmation"
            elif segment_label == "short_low_value_tail":
                classification = "short_tail_low_value_probe_candidate"
                candidate_kind = "low_value_shadow_probe"
                reason = "short local-looking tail is low value but may help boundary diagnostics"
            elif segment_label == "duplicate_heavy_needs_speaker_evidence":
                classification = "blocked_duplicate_heavy_needs_speaker_evidence"
                reason = "duplicate-heavy mixed row must not publish without stronger speaker evidence"
            elif segment_label == "remote_dominant_mixed_not_rescuable":
                classification = "blocked_remote_dominant_mixed"
                reason = "remote-dominant mixed row is not rescuable with current evidence"
        elif label == "remote_dominant_not_rescuable_without_new_evidence":
            classification = "blocked_remote_dominant"
            reason = "dominant evidence is remote-like"
        elif label == "asr_hallucination_not_rescuable":
            classification = "blocked_known_hallucination"
            reason = "known hallucination must stay excluded"
        elif label == "not_visible_needs_asr_or_boundary_repair":
            classification = "not_visible_needs_asr_or_boundary_repair"
            reason = "missing Me is not visible in suppressed mic evidence"

        publication_ready = False
        shadow_probe = candidate_kind in {"shadow_probe_candidate", "low_value_shadow_probe"}
        rows.append(
            {
                "session": item.get("session"),
                "batch_id": item.get("batch_id"),
                "start": item.get("start"),
                "end": item.get("end"),
                "duration_sec": round(duration_sec, 3),
                "text": item.get("text"),
                "actionability": label,
                "segmentability": segment_label or None,
                "classification": classification,
                "candidate_kind": candidate_kind,
                "reason": reason,
                "shadow_probe": shadow_probe,
                "publication_ready": publication_ready,
                "local_like_seconds": local_like_seconds,
                "remote_like_seconds": remote_like_seconds,
                "duplicate_overlap_seconds": round(duplicate_seconds, 3),
                "local_island_seconds": round(local_island_seconds, 3),
                "local_island_count": local_island_count,
                "dominant_suppressed_label": actionability.get("dominant_suppressed_label"),
                "comparison": item.get("comparison"),
            }
        )

    def aggregate(items: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
        grouped: dict[str, dict[str, Any]] = {}
        for row in items:
            group = str(row.get(key) or "(none)")
            group_row = grouped.setdefault(group, {"count": 0, "seconds": 0.0})
            group_row["count"] += 1
            group_row["seconds"] = round(
                safe_float(group_row.get("seconds")) + safe_float(row.get("duration_sec")),
                3,
            )
        return dict(sorted(grouped.items(), key=lambda pair: (-safe_float(pair[1].get("seconds")), pair[0])))

    shadow_probe_rows = [row for row in rows if row.get("shadow_probe")]
    blocked_rows = [row for row in rows if not row.get("shadow_probe")]
    return {
        "schema": "murmurmark.live_speaker_boundary_evidence_lab/v1",
        "status": "ok" if rows else "no_remaining_gap_rows",
        "promotion_allowed": False,
        "interpretation": (
            "diagnostic only: classifies the current best live-implementable remaining gap into "
            "shadow-probe candidates and blocked rows; it does not publish live Me text"
        ),
        "row_count": len(rows),
        "seconds": round(sum(safe_float(row.get("duration_sec")) for row in rows), 3),
        "shadow_probe_count": len(shadow_probe_rows),
        "shadow_probe_seconds": round(sum(safe_float(row.get("duration_sec")) for row in shadow_probe_rows), 3),
        "publication_ready_seconds": 0.0,
        "blocked_count": len(blocked_rows),
        "blocked_seconds": round(sum(safe_float(row.get("duration_sec")) for row in blocked_rows), 3),
        "by_classification": aggregate(rows, "classification"),
        "by_candidate_kind": aggregate(rows, "candidate_kind"),
        "recommended_shadow_profile": {
            "name": "online_local_speaker_boundary_shadow_v1",
            "status": "design_candidate",
            "source": "shadow_probe rows only",
            "must_preserve": ["remote_leakage == 0", "contentful_order_mismatches must not increase"],
            "must_exclude_classifications": [
                "blocked_remote_dominant",
                "blocked_remote_dominant_mixed",
                "blocked_duplicate_heavy_needs_speaker_evidence",
                "blocked_known_hallucination",
            ],
        },
        "examples": sorted(
            rows,
            key=lambda row: (
                not bool(row.get("shadow_probe")),
                -safe_float(row.get("duration_sec")),
                str(row.get("session") or ""),
                safe_float(row.get("start")),
            ),
        )[:limit],
    }


def live_online_speaker_boundary_evidence_design_lab(
    remaining_gap: dict[str, Any],
    local_island_split_lab_report: dict[str, Any],
    *,
    limit: int = 80,
) -> dict[str, Any]:
    examples = remaining_gap.get("examples") if isinstance(remaining_gap.get("examples"), list) else []
    split_rows = (
        local_island_split_lab_report.get("examples")
        if isinstance(local_island_split_lab_report.get("examples"), list)
        else []
    )
    split_by_key = {
        (str(row.get("session") or ""), str(row.get("batch_id") or "")): row
        for row in split_rows
        if isinstance(row, dict)
    }
    rows: list[dict[str, Any]] = []

    for item in examples:
        if not isinstance(item, dict):
            continue
        actionability = item.get("actionability") if isinstance(item.get("actionability"), dict) else {}
        label = str(actionability.get("label") or "(none)")
        if label not in {"mixed_needs_segmentation_or_speaker_evidence", "speaker_confirmation_candidate"}:
            continue
        segmentability = (
            actionability.get("segmentability")
            if isinstance(actionability.get("segmentability"), dict)
            else {}
        )
        segment_label = str(segmentability.get("label") or "")
        suppressed_labels = (
            actionability.get("suppressed_label_overlap_seconds")
            if isinstance(actionability.get("suppressed_label_overlap_seconds"), dict)
            else {}
        )
        duration_sec = safe_float(item.get("duration_sec"))
        local_like_seconds = round(
            safe_float(suppressed_labels.get("me_dominant")) + safe_float(suppressed_labels.get("mixed")),
            3,
        )
        remote_like_seconds = round(safe_float(suppressed_labels.get("remote_dominant")), 3)
        duplicate_seconds = safe_float(actionability.get("duplicate_overlap_seconds"))
        local_island_seconds = safe_float(segmentability.get("local_island_seconds"))
        local_island_count = safe_int(segmentability.get("local_island_count"))
        duplicate_ratio = duplicate_seconds / duration_sec if duration_sec > 0 else 0.0
        remote_ratio = remote_like_seconds / duration_sec if duration_sec > 0 else 0.0
        local_island_ratio = local_island_seconds / duration_sec if duration_sec > 0 else 0.0
        split_row = split_by_key.get((str(item.get("session") or ""), str(item.get("batch_id") or ""))) or {}
        token_recall = (
            safe_float(split_row.get("token_recall_from_local_islands"))
            if isinstance(split_row, dict) and split_row.get("token_recall_from_local_islands") is not None
            else None
        )

        design_unit = "blocked_keep_suppressed"
        required_evidence = "none"
        priority = 99
        blocker = "current evidence must stay suppressed"
        potential_publish_seconds = 0.0
        next_experiment = "none"

        if label == "speaker_confirmation_candidate":
            design_unit = "speaker_confirmation_voice_gate"
            required_evidence = "online_target_me_or_persistent_voice_confirmation"
            priority = 4
            blocker = "me-dominant evidence has no speaker confirmation"
            potential_publish_seconds = duration_sec
            next_experiment = "confirm short me-dominant rows with online voice evidence"
        elif segment_label == "local_island_split_candidate":
            design_unit = "boundary_island_micro_asr"
            required_evidence = "online_local_island_token_alignment"
            priority = 1
            blocker = "local island exists but token recall is below publication threshold"
            potential_publish_seconds = local_island_seconds
            next_experiment = "decode only local island spans and compare text before publishing a split Me turn"
        elif segment_label == "needs_speaker_evidence":
            design_unit = "mixed_boundary_voice_gate"
            required_evidence = "online_speaker_boundary_confirmation"
            priority = 2
            blocker = "mixed row has local-looking audio but lacks speaker identity evidence"
            potential_publish_seconds = local_island_seconds or local_like_seconds
            next_experiment = "combine low remote correlation, local island boundary and Target-Me evidence"
        elif segment_label == "duplicate_heavy_needs_speaker_evidence":
            design_unit = "duplicate_heavy_voice_disambiguation"
            required_evidence = "target_me_voice_against_remote_duplicate"
            priority = 3
            blocker = "duplicate-heavy row risks publishing remote text as Me"
            potential_publish_seconds = max(0.0, local_like_seconds - remote_like_seconds)
            next_experiment = "only rescue duplicate-heavy rows when voice evidence proves unique local speech"
        elif segment_label == "short_low_value_tail":
            design_unit = "low_value_tail_policy"
            required_evidence = "optional_boundary_confirmation"
            priority = 5
            blocker = "short tail is low value and should not drive the next profile"
            potential_publish_seconds = min(duration_sec, local_island_seconds or local_like_seconds)
            next_experiment = "keep advisory unless it is needed to repair order or a phrase boundary"
        elif segment_label == "remote_dominant_mixed_not_rescuable":
            design_unit = "remote_dominant_keep_blocked"
            required_evidence = "do_not_publish_without_new_asr_or_voice_evidence"
            priority = 90
            blocker = "remote-dominant evidence outweighs local-looking evidence"
            next_experiment = "do not target in the next live profile"

        rows.append(
            {
                "session": item.get("session"),
                "batch_id": item.get("batch_id"),
                "start": item.get("start"),
                "end": item.get("end"),
                "duration_sec": round(duration_sec, 3),
                "text": item.get("text"),
                "actionability": label,
                "segmentability": segment_label or None,
                "design_unit": design_unit,
                "required_evidence": required_evidence,
                "priority": priority,
                "blocker": blocker,
                "next_experiment": next_experiment,
                "potential_publish_seconds": round(max(0.0, potential_publish_seconds), 3),
                "local_like_seconds": local_like_seconds,
                "remote_like_seconds": remote_like_seconds,
                "duplicate_overlap_seconds": round(duplicate_seconds, 3),
                "duplicate_overlap_ratio": round(duplicate_ratio, 6),
                "remote_like_ratio": round(remote_ratio, 6),
                "local_island_seconds": round(local_island_seconds, 3),
                "local_island_count": local_island_count,
                "local_island_ratio": round(local_island_ratio, 6),
                "token_recall_from_local_islands": (
                    round(token_recall, 6) if token_recall is not None else None
                ),
                "dominant_suppressed_label": actionability.get("dominant_suppressed_label"),
                "online_publication_safe_now": False,
                "publication_safety_reason": "diagnostic only; live promotion remains blocked",
                "comparison": item.get("comparison"),
            }
        )

    def aggregate(items: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
        grouped: dict[str, dict[str, Any]] = {}
        for row in items:
            group = str(row.get(key) or "(none)")
            group_row = grouped.setdefault(
                group,
                {
                    "count": 0,
                    "seconds": 0.0,
                    "potential_publish_seconds": 0.0,
                    "priority": safe_int(row.get("priority")),
                },
            )
            group_row["count"] += 1
            group_row["seconds"] = round(
                safe_float(group_row.get("seconds")) + safe_float(row.get("duration_sec")),
                3,
            )
            group_row["potential_publish_seconds"] = round(
                safe_float(group_row.get("potential_publish_seconds"))
                + safe_float(row.get("potential_publish_seconds")),
                3,
            )
            group_row["priority"] = min(safe_int(group_row.get("priority")), safe_int(row.get("priority")))
        return dict(
            sorted(
                grouped.items(),
                key=lambda pair: (
                    safe_int(pair[1].get("priority")),
                    -safe_float(pair[1].get("seconds")),
                    pair[0],
                ),
            )
        )

    by_design_unit = aggregate(rows, "design_unit")
    top_design_unit = next(iter(by_design_unit.items()), None)
    actionable_rows = [row for row in rows if safe_int(row.get("priority")) < 90]
    publishable_now_rows = [row for row in rows if row.get("online_publication_safe_now")]
    return {
        "schema": "murmurmark.live_online_speaker_boundary_evidence_design_lab/v1",
        "status": "ok" if rows else "no_mixed_or_speaker_rows",
        "promotion_allowed": False,
        "interpretation": (
            "diagnostic only: decomposes the remaining mixed/speaker gap into implementation units. "
            "It does not publish live Me text and cannot make live promotion pass."
        ),
        "row_count": len(rows),
        "seconds": round(sum(safe_float(row.get("duration_sec")) for row in rows), 3),
        "actionable_count": len(actionable_rows),
        "actionable_seconds": round(sum(safe_float(row.get("duration_sec")) for row in actionable_rows), 3),
        "potential_publish_seconds": round(
            sum(safe_float(row.get("potential_publish_seconds")) for row in actionable_rows),
            3,
        ),
        "publication_ready_count": len(publishable_now_rows),
        "publication_ready_seconds": round(
            sum(safe_float(row.get("duration_sec")) for row in publishable_now_rows),
            3,
        ),
        "by_design_unit": by_design_unit,
        "by_required_evidence": aggregate(rows, "required_evidence"),
        "top_design_unit": (
            {"id": top_design_unit[0], **top_design_unit[1]} if top_design_unit else None
        ),
        "recommended_next": (
            {
                "id": top_design_unit[0],
                "why": "highest-priority implementation unit covering remaining mixed/speaker gap",
                "seconds": top_design_unit[1].get("seconds"),
                "potential_publish_seconds": top_design_unit[1].get("potential_publish_seconds"),
            }
            if top_design_unit
            else None
        ),
        "candidate_shadow_profile": {
            "name": "online_speaker_boundary_evidence_shadow_v1",
            "status": "design_only",
            "inputs": [
                "live mic/remote segment timings",
                "suppressed mic evidence",
                "local island token alignment",
                "Target-Me or persistent voice evidence",
                "mic/remote correlation and RMS features",
            ],
            "hard_gates": [
                "remote_leakage == 0",
                "contentful_order_mismatches must not increase",
                "publish split Me turns only, never whole duplicate-heavy rows",
                "keep batch transcript authoritative",
            ],
        },
        "examples": sorted(
            rows,
            key=lambda row: (
                safe_int(row.get("priority")),
                -safe_float(row.get("duration_sec")),
                str(row.get("session") or ""),
                safe_float(row.get("start")),
            ),
        )[:limit],
    }


def _target_me_audit_rows(root: Path, session_id: str) -> tuple[list[dict[str, Any]], str | None, str | None]:
    audit_path = (
        root
        / session_id
        / "derived/audit/live-local-recall-target-me/live_local_recall_target_me_audit.jsonl"
    )
    summary_path = (
        root
        / session_id
        / "derived/audit/live-local-recall-target-me/live_local_recall_target_me_summary.json"
    )
    rows = read_jsonl(audit_path)
    summary = read_json(summary_path) or {}
    return rows, str(audit_path) if audit_path.exists() else None, str(summary.get("status") or "")


def _audit_interval(row: dict[str, Any]) -> tuple[float, float]:
    interval = row.get("interval") if isinstance(row.get("interval"), dict) else {}
    start = safe_float(interval.get("start"), row.get("start"))
    end = safe_float(interval.get("end"), row.get("end"))
    return start, end


def _voice_row_summary(row: dict[str, Any], overlap_sec: float) -> dict[str, Any]:
    classification = row.get("classification") if isinstance(row.get("classification"), dict) else {}
    scores = classification.get("scores") if isinstance(classification.get("scores"), dict) else {}
    start, end = _audit_interval(row)
    return {
        "id": row.get("id"),
        "start": round(start, 3),
        "end": round(end, 3),
        "duration_sec": round(max(0.0, end - start), 3),
        "overlap_sec": round(overlap_sec, 3),
        "batch_role_label": row.get("batch_role_label"),
        "text": row.get("text"),
        "target_me_label": classification.get("label"),
        "confidence": classification.get("confidence"),
        "suggested_decision": classification.get("suggested_decision"),
        "reason": classification.get("reason"),
        "target_me_rescue_policy_candidates": row.get("target_me_rescue_policy_candidates") or [],
        "scores": {
            "best_mic_target_similarity": scores.get("best_mic_target_similarity"),
            "remote_target_similarity": scores.get("remote_target_similarity"),
            "delta_vs_remote": scores.get("delta_vs_remote"),
            "state_local_score_proxy": scores.get("state_local_score_proxy"),
            "state_remote_active_ratio": scores.get("state_remote_active_ratio"),
        },
    }


def classify_mixed_voice_coverage(
    *,
    design_unit: str,
    matched_rows: list[dict[str, Any]],
    target_audit_present: bool,
    target_audit_status: str | None,
) -> tuple[str, str, bool]:
    if not target_audit_present:
        return (
            "no_target_me_audit",
            "session has no live-local-recall Target-Me audit for these mixed rows",
            False,
        )
    if not matched_rows:
        if target_audit_status and target_audit_status not in {"ok", "no_items"}:
            return (
                "target_me_enrollment_not_ready",
                f"Target-Me audit exists, but status is {target_audit_status}",
                False,
            )
        return (
            "no_voice_overlap",
            "Target-Me audit exists, but it does not cover this remaining mixed/speaker interval",
            False,
        )
    labels = {
        str(
            (
                row.get("classification")
                if isinstance(row.get("classification"), dict)
                else {}
            ).get("label")
            or ""
        )
        for row in matched_rows
    }
    roles = {str(row.get("batch_role_label") or "") for row in matched_rows}
    policy_candidates = {
        str(policy)
        for row in matched_rows
        for policy in (row.get("target_me_rescue_policy_candidates") or [])
    }
    has_remote_guard = "target_me_confirmed_remote_guard_v1" in policy_candidates
    has_confirmed = "target_me_confirmed" in labels
    has_possible = "target_me_possible" in labels
    has_remote_risk = "remote_dominant" in roles
    if has_remote_guard and not has_remote_risk:
        if design_unit == "low_value_tail_policy":
            return (
                "voice_confirmed_low_value_tail_advisory",
                "short low-value tail has voice evidence, but should not drive publication",
                False,
            )
        return (
            "voice_confirmed_remote_guard_candidate",
            "overlapping Target-Me row has confirmed remote-guard evidence and no remote-dominant audit label",
            True,
        )
    if has_confirmed and design_unit in {"mixed_boundary_voice_gate", "speaker_confirmation_voice_gate"}:
        return (
            "voice_confirmed_needs_remote_guard",
            "overlapping Target-Me row is confirmed, but needs a stricter remote guard before publication",
            False,
        )
    if has_possible:
        return (
            "voice_possible_needs_review",
            "overlapping Target-Me row is possible only; useful for the next gate, not enough to publish",
            False,
        )
    return (
        "voice_rejected_or_ambiguous",
        "overlapping Target-Me evidence is ambiguous or conflicts with remote/role evidence",
        False,
    )


def live_mixed_speaker_boundary_voice_coverage_lab(
    remaining_gap: dict[str, Any],
    online_design: dict[str, Any],
    *,
    root: Path,
    limit: int = 80,
) -> dict[str, Any]:
    design_examples = (
        online_design.get("examples")
        if isinstance(online_design.get("examples"), list)
        else []
    )
    remaining_examples = (
        remaining_gap.get("examples")
        if isinstance(remaining_gap.get("examples"), list)
        else []
    )
    remaining_by_key = {
        (str(row.get("session") or ""), str(row.get("batch_id") or "")): row
        for row in remaining_examples
        if isinstance(row, dict)
    }
    audit_cache: dict[str, tuple[list[dict[str, Any]], str | None]] = {}
    rows: list[dict[str, Any]] = []

    for design in design_examples:
        if not isinstance(design, dict):
            continue
        design_unit = str(design.get("design_unit") or "")
        if design_unit not in {
            "boundary_island_micro_asr",
            "mixed_boundary_voice_gate",
            "duplicate_heavy_voice_disambiguation",
            "speaker_confirmation_voice_gate",
            "low_value_tail_policy",
            "remote_dominant_keep_blocked",
        }:
            continue
        session_id = str(design.get("session") or "")
        batch_id = str(design.get("batch_id") or "")
        if not session_id:
            continue
        audit_rows, audit_path, audit_status = audit_cache.setdefault(
            session_id,
            _target_me_audit_rows(root, session_id),
        )
        gap = remaining_by_key.get((session_id, batch_id)) or {}
        gap_start = safe_float(gap.get("start"), design.get("start"))
        gap_end = safe_float(gap.get("end"), design.get("end"))
        suppressed_evidence = (
            gap.get("suppressed_mic_evidence")
            if isinstance(gap.get("suppressed_mic_evidence"), list)
            else []
        )
        spans: list[tuple[float, float]] = []
        for evidence in suppressed_evidence:
            if not isinstance(evidence, dict):
                continue
            start = safe_float(evidence.get("start"))
            end = safe_float(evidence.get("end"))
            if end > start:
                span_start = max(start, gap_start)
                span_end = min(end, gap_end)
                if span_end > span_start:
                    spans.append((span_start, span_end))
        if not spans:
            start = safe_float(design.get("start"))
            end = safe_float(design.get("end"))
            if end > start:
                spans.append((start, end))

        matched: list[tuple[dict[str, Any], float]] = []
        for audit_row in audit_rows:
            if not isinstance(audit_row, dict):
                continue
            audit_start, audit_end = _audit_interval(audit_row)
            if audit_end <= audit_start:
                continue
            overlap = sum(interval_overlap(audit_start, audit_end, span_start, span_end) for span_start, span_end in spans)
            if overlap >= 0.25:
                matched.append((audit_row, overlap))
        matched.sort(key=lambda pair: (-pair[1], _audit_interval(pair[0])[0]))
        matched_rows = [row for row, _overlap in matched]
        classification, reason, publish_candidate = classify_mixed_voice_coverage(
            design_unit=design_unit,
            matched_rows=matched_rows,
            target_audit_present=bool(audit_path),
            target_audit_status=audit_status,
        )
        overlap_seconds = round(sum(overlap for _row, overlap in matched), 3)
        rows.append(
            {
                "session": session_id,
                "batch_id": batch_id,
                "start": design.get("start"),
                "end": design.get("end"),
                "duration_sec": design.get("duration_sec"),
                "text": design.get("text"),
                "design_unit": design_unit,
                "segmentability": design.get("segmentability"),
                "actionability": design.get("actionability"),
                "target_me_audit_path": audit_path,
                "target_me_audit_status": audit_status,
                "target_me_audit_row_count": len(audit_rows),
                "voice_overlap_seconds": overlap_seconds,
                "voice_coverage_ratio": (
                    round(overlap_seconds / safe_float(design.get("duration_sec")), 6)
                    if safe_float(design.get("duration_sec")) > 0
                    else 0.0
                ),
                "voice_coverage_classification": classification,
                "voice_coverage_reason": reason,
                "would_be_publication_candidate": bool(publish_candidate),
                "publication_allowed": False,
                "publication_safety_reason": "diagnostic only; live promotion remains blocked",
                "matched_target_me_rows": [
                    _voice_row_summary(row, overlap)
                    for row, overlap in matched[:5]
                ],
            }
        )

    def aggregate(key: str) -> dict[str, dict[str, Any]]:
        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            group = str(row.get(key) or "(none)")
            payload = grouped.setdefault(
                group,
                {"count": 0, "seconds": 0.0, "voice_overlap_seconds": 0.0},
            )
            payload["count"] += 1
            payload["seconds"] = round(
                safe_float(payload.get("seconds")) + safe_float(row.get("duration_sec")),
                3,
            )
            payload["voice_overlap_seconds"] = round(
                safe_float(payload.get("voice_overlap_seconds")) + safe_float(row.get("voice_overlap_seconds")),
                3,
            )
        return dict(sorted(grouped.items(), key=lambda pair: (-safe_float(pair[1].get("seconds")), pair[0])))

    publication_candidates = [row for row in rows if row.get("would_be_publication_candidate")]
    no_audit_rows = [row for row in rows if row.get("voice_coverage_classification") == "no_target_me_audit"]
    enrollment_not_ready_rows = [
        row for row in rows if row.get("voice_coverage_classification") == "target_me_enrollment_not_ready"
    ]
    no_overlap_rows = [row for row in rows if row.get("voice_coverage_classification") == "no_voice_overlap"]
    weak_rows = [
        row
        for row in rows
        if row.get("voice_coverage_classification")
        in {
            "voice_possible_needs_review",
            "voice_confirmed_needs_remote_guard",
            "voice_rejected_or_ambiguous",
        }
    ]
    publication_candidate_seconds = round(
        sum(safe_float(row.get("duration_sec")) for row in publication_candidates),
        3,
    )
    missing_coverage_seconds = round(
        sum(safe_float(row.get("duration_sec")) for row in no_audit_rows + no_overlap_rows),
        3,
    )
    if enrollment_not_ready_rows:
        recommended_next = {
            "id": "add_target_me_enrollment_fallback_for_remaining_mixed_boundary_rows",
            "why": "remaining mixed/speaker rows are in sessions where Target-Me audit cannot build enough enrollment",
            "enrollment_not_ready_seconds": round(
                sum(safe_float(row.get("duration_sec")) for row in enrollment_not_ready_rows),
                3,
            ),
            "ready_candidate_seconds": publication_candidate_seconds,
        }
    elif no_audit_rows or (no_overlap_rows and publication_candidate_seconds < 2.0):
        recommended_next = {
            "id": "extend_target_me_audit_to_remaining_mixed_boundary_rows",
            "why": "current Target-Me audit does not cover the largest mixed/speaker blocker intervals",
            "missing_coverage_seconds": missing_coverage_seconds,
            "ready_candidate_seconds": publication_candidate_seconds,
        }
    elif rows and publication_candidates:
        recommended_next = {
            "id": "materialize_remote_guarded_voice_boundary_candidates",
            "why": "some mixed rows already have overlapping confirmed remote-guard Target-Me evidence",
            "candidate_seconds": publication_candidate_seconds,
        }
    elif weak_rows:
        recommended_next = {
            "id": "tighten_voice_remote_guard_for_mixed_rows",
            "why": "voice evidence exists but is not safe enough for publication",
            "weak_seconds": round(sum(safe_float(row.get("duration_sec")) for row in weak_rows), 3),
        }
    else:
        recommended_next = {
            "id": "no_mixed_voice_coverage_work",
            "why": "no mixed/speaker rows are present",
        }

    return {
        "schema": "murmurmark.live_mixed_speaker_boundary_voice_coverage_lab/v1",
        "status": "ok" if rows else "no_mixed_or_speaker_rows",
        "promotion_allowed": False,
        "interpretation": (
            "diagnostic only: measures whether the current mixed/speaker blocker is covered by existing "
            "Target-Me voice evidence. It does not publish live Me text."
        ),
        "row_count": len(rows),
        "seconds": round(sum(safe_float(row.get("duration_sec")) for row in rows), 3),
        "voice_overlap_seconds": round(sum(safe_float(row.get("voice_overlap_seconds")) for row in rows), 3),
        "publication_candidate_count": len(publication_candidates),
        "publication_candidate_seconds": round(
            sum(safe_float(row.get("duration_sec")) for row in publication_candidates),
            3,
        ),
        "no_target_me_audit_count": len(no_audit_rows),
        "no_target_me_audit_seconds": round(sum(safe_float(row.get("duration_sec")) for row in no_audit_rows), 3),
        "target_me_enrollment_not_ready_count": len(enrollment_not_ready_rows),
        "target_me_enrollment_not_ready_seconds": round(
            sum(safe_float(row.get("duration_sec")) for row in enrollment_not_ready_rows),
            3,
        ),
        "no_voice_overlap_count": len(no_overlap_rows),
        "no_voice_overlap_seconds": round(sum(safe_float(row.get("duration_sec")) for row in no_overlap_rows), 3),
        "weak_or_ambiguous_count": len(weak_rows),
        "weak_or_ambiguous_seconds": round(sum(safe_float(row.get("duration_sec")) for row in weak_rows), 3),
        "by_classification": aggregate("voice_coverage_classification"),
        "by_design_unit": aggregate("design_unit"),
        "recommended_next": recommended_next,
        "examples": sorted(
            rows,
            key=lambda row: (
                str(row.get("voice_coverage_classification") or ""),
                -safe_float(row.get("duration_sec")),
                str(row.get("session") or ""),
                safe_float(row.get("start")),
            ),
        )[:limit],
    }


def _tight_voice_remote_guard_metrics(row: dict[str, Any]) -> dict[str, Any]:
    matched_rows = (
        row.get("matched_target_me_rows")
        if isinstance(row.get("matched_target_me_rows"), list)
        else []
    )
    best_mic = 0.0
    best_delta = -999.0
    max_remote_similarity = 0.0
    max_remote_active = 0.0
    max_local_score = 0.0
    has_remote_role = False
    has_possible_or_confirmed = False
    has_remote_guard = False
    for matched in matched_rows:
        if not isinstance(matched, dict):
            continue
        role = str(matched.get("batch_role_label") or "")
        has_remote_role = has_remote_role or role == "remote_dominant"
        label = str(matched.get("target_me_label") or "")
        has_possible_or_confirmed = has_possible_or_confirmed or label in {
            "target_me_possible",
            "target_me_confirmed",
        }
        policies = matched.get("target_me_rescue_policy_candidates")
        if isinstance(policies, list):
            has_remote_guard = has_remote_guard or "target_me_confirmed_remote_guard_v1" in policies
        scores = matched.get("scores") if isinstance(matched.get("scores"), dict) else {}
        best_mic = max(best_mic, safe_float(scores.get("best_mic_target_similarity")))
        best_delta = max(best_delta, safe_float(scores.get("delta_vs_remote"), -999.0))
        max_remote_similarity = max(max_remote_similarity, safe_float(scores.get("remote_target_similarity")))
        max_remote_active = max(max_remote_active, safe_float(scores.get("state_remote_active_ratio")))
        max_local_score = max(max_local_score, safe_float(scores.get("state_local_score_proxy")))
    return {
        "best_mic_target_similarity": round(best_mic, 6),
        "best_delta_vs_remote": round(best_delta if best_delta > -900 else 0.0, 6),
        "max_remote_target_similarity": round(max_remote_similarity, 6),
        "max_state_remote_active_ratio": round(max_remote_active, 6),
        "max_state_local_score_proxy": round(max_local_score, 6),
        "has_remote_role_conflict": has_remote_role,
        "has_possible_or_confirmed_target_me": has_possible_or_confirmed,
        "has_confirmed_remote_guard": has_remote_guard,
    }


def classify_tight_voice_remote_guard(row: dict[str, Any]) -> tuple[str, list[str], bool, dict[str, Any]]:
    metrics = _tight_voice_remote_guard_metrics(row)
    blockers: list[str] = []
    design_unit = str(row.get("design_unit") or "")
    audit_status = str(row.get("target_me_audit_status") or "")
    voice_ratio = safe_float(row.get("voice_coverage_ratio"))

    if design_unit == "low_value_tail_policy":
        blockers.append("blocked_low_value_tail")
    if audit_status not in {"ok"}:
        blockers.append("blocked_target_me_audit_not_same_session_ok")
    if metrics.get("has_remote_role_conflict"):
        blockers.append("blocked_remote_role_conflict")
    if not metrics.get("has_possible_or_confirmed_target_me"):
        blockers.append("blocked_no_possible_target_me_voice")
    if safe_float(metrics.get("best_mic_target_similarity")) < 0.82:
        blockers.append("blocked_weak_target_similarity")
    if safe_float(metrics.get("best_delta_vs_remote")) < 0.10:
        blockers.append("blocked_low_delta_vs_remote")
    if safe_float(metrics.get("max_remote_target_similarity")) > 0.78:
        blockers.append("blocked_high_remote_similarity")
    if safe_float(metrics.get("max_state_remote_active_ratio")) > 0.15:
        blockers.append("blocked_remote_active_overlap")
    if safe_float(metrics.get("max_state_local_score_proxy")) < 0.75:
        blockers.append("blocked_weak_local_state")
    if voice_ratio < 0.75:
        blockers.append("blocked_insufficient_voice_coverage")

    can_publish = not blockers
    if can_publish:
        return "tight_voice_remote_guard_candidate", [], True, metrics

    priority = [
        "blocked_low_value_tail",
        "blocked_target_me_audit_not_same_session_ok",
        "blocked_remote_role_conflict",
        "blocked_no_possible_target_me_voice",
        "blocked_low_delta_vs_remote",
        "blocked_high_remote_similarity",
        "blocked_weak_target_similarity",
        "blocked_remote_active_overlap",
        "blocked_weak_local_state",
        "blocked_insufficient_voice_coverage",
    ]
    primary = next((label for label in priority if label in blockers), "blocked_by_tight_voice_remote_guard")
    return primary, blockers, False, metrics


def live_tight_voice_remote_guard_lab(
    mixed_voice_coverage: dict[str, Any],
    *,
    limit: int = 80,
) -> dict[str, Any]:
    examples = (
        mixed_voice_coverage.get("examples")
        if isinstance(mixed_voice_coverage.get("examples"), list)
        else []
    )
    rows: list[dict[str, Any]] = []
    for row in examples:
        if not isinstance(row, dict):
            continue
        if row.get("would_be_publication_candidate"):
            continue
        if row.get("voice_coverage_classification") not in {
            "voice_possible_needs_review",
            "voice_confirmed_needs_remote_guard",
            "voice_rejected_or_ambiguous",
        }:
            continue
        primary, blockers, candidate, metrics = classify_tight_voice_remote_guard(row)
        rows.append(
            {
                "session": row.get("session"),
                "batch_id": row.get("batch_id"),
                "start": row.get("start"),
                "end": row.get("end"),
                "duration_sec": row.get("duration_sec"),
                "text": row.get("text"),
                "design_unit": row.get("design_unit"),
                "voice_coverage_classification": row.get("voice_coverage_classification"),
                "target_me_audit_status": row.get("target_me_audit_status"),
                "primary_blocker": primary,
                "blockers": blockers,
                "would_be_tight_voice_remote_guard_candidate": candidate,
                "publication_allowed": False,
                "publication_safety_reason": "diagnostic only; live promotion remains blocked",
                "metrics": metrics,
                "matched_target_me_rows": row.get("matched_target_me_rows") or [],
            }
        )

    def aggregate(key: str) -> dict[str, dict[str, Any]]:
        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            label = str(row.get(key) or "(none)")
            payload = grouped.setdefault(label, {"count": 0, "seconds": 0.0})
            payload["count"] += 1
            payload["seconds"] = round(
                safe_float(payload.get("seconds")) + safe_float(row.get("duration_sec")),
                3,
            )
        return dict(sorted(grouped.items(), key=lambda pair: (-safe_float(pair[1].get("seconds")), pair[0])))

    candidates = [row for row in rows if row.get("would_be_tight_voice_remote_guard_candidate")]
    blocked = [row for row in rows if not row.get("would_be_tight_voice_remote_guard_candidate")]
    by_primary_blocker = aggregate("primary_blocker")
    top_blocker = largest_group(by_primary_blocker)
    candidate_seconds = round(sum(safe_float(row.get("duration_sec")) for row in candidates), 3)
    blocked_seconds = round(sum(safe_float(row.get("duration_sec")) for row in blocked), 3)
    if not rows:
        recommended_next = {
            "id": "no_tight_voice_remote_guard_work",
            "why": "mixed voice coverage has no weak rows in scope",
        }
    elif candidate_seconds > 0:
        recommended_next = {
            "id": "materialize_tight_voice_remote_guard_candidates",
            "why": "strict voice/remote guard found candidate rows that can be materialized as diagnostic shadow",
            "candidate_seconds": candidate_seconds,
        }
    else:
        recommended_next = {
            "id": "improve_same_session_voice_disambiguation_for_mixed_rows",
            "why": "strict voice/remote guard found no publishable mixed rows; remaining evidence is too close to remote or fallback-only",
            "blocked_seconds": blocked_seconds,
            "top_blocker": top_blocker,
        }

    return {
        "schema": "murmurmark.live_tight_voice_remote_guard_lab/v1",
        "status": "ok" if rows else "no_scope",
        "promotion_allowed": False,
        "interpretation": (
            "diagnostic only: applies stricter Target-Me-vs-remote thresholds to weak mixed rows. "
            "It does not publish live Me text and cannot promote live output."
        ),
        "row_count": len(rows),
        "seconds": round(sum(safe_float(row.get("duration_sec")) for row in rows), 3),
        "candidate_count": len(candidates),
        "candidate_seconds": candidate_seconds,
        "blocked_count": len(blocked),
        "blocked_seconds": blocked_seconds,
        "top_blocker": top_blocker,
        "by_primary_blocker": by_primary_blocker,
        "by_design_unit": aggregate("design_unit"),
        "recommended_next": recommended_next,
        "examples": sorted(
            rows,
            key=lambda row: (
                not bool(row.get("would_be_tight_voice_remote_guard_candidate")),
                -safe_float(row.get("duration_sec")),
                str(row.get("session") or ""),
                safe_float(row.get("start")),
            ),
        )[:limit],
    }


def live_local_island_audio_anchor_lab(local_island_split_lab_report: dict[str, Any]) -> dict[str, Any]:
    examples = (
        local_island_split_lab_report.get("examples")
        if isinstance(local_island_split_lab_report.get("examples"), list)
        else []
    )
    max_zero_lag_abs_corr = 0.03
    min_mic_minus_remote_rms_db = -6.0
    accepted_rows = [row for row in examples if isinstance(row, dict) and bool(row.get("accepted"))]
    candidate_rows: list[dict[str, Any]] = []
    anchor_seconds = 0.0
    anchor_count = 0
    for row in accepted_rows:
        islands = row.get("local_island_examples") if isinstance(row.get("local_island_examples"), list) else []
        anchor_islands: list[dict[str, Any]] = []
        for island in islands:
            if not isinstance(island, dict):
                continue
            corr = safe_float(island.get("audio_mic_remote_zero_lag_abs_corr"))
            mic_minus_remote = safe_float(island.get("audio_mic_minus_remote_rms_db"))
            if corr > max_zero_lag_abs_corr or mic_minus_remote < min_mic_minus_remote_rms_db:
                continue
            anchor_count += 1
            seconds = safe_float(island.get("overlap_sec"))
            anchor_seconds = round(anchor_seconds + seconds, 3)
            anchor_islands.append(
                {
                    "start": island.get("start"),
                    "end": island.get("end"),
                    "overlap_sec": island.get("overlap_sec"),
                    "text": island.get("text"),
                    "audio_mic_minus_remote_rms_db": island.get("audio_mic_minus_remote_rms_db"),
                    "audio_mic_remote_zero_lag_abs_corr": island.get("audio_mic_remote_zero_lag_abs_corr"),
                }
            )
        if anchor_islands:
            candidate_rows.append(
                {
                    "session": row.get("session"),
                    "batch_id": row.get("batch_id"),
                    "duration_sec": row.get("duration_sec"),
                    "local_island_seconds": row.get("local_island_seconds"),
                    "token_recall_from_local_islands": row.get("token_recall_from_local_islands"),
                    "anchor_island_count": len(anchor_islands),
                    "anchor_island_seconds": round(
                        sum(safe_float(island.get("overlap_sec")) for island in anchor_islands),
                        3,
                    ),
                    "anchor_islands": anchor_islands,
                }
            )
    return {
        "schema": "murmurmark.live_local_island_audio_anchor_lab/v1",
        "status": "ok" if accepted_rows else "no_accepted_local_island_rows",
        "promotion_allowed": False,
        "interpretation": (
            "diagnostic only: counts live-available audio anchors inside batch-backed local-island rows; "
            "it does not prove live publication safety by itself"
        ),
        "criteria": {
            "max_audio_mic_remote_zero_lag_abs_corr": max_zero_lag_abs_corr,
            "min_audio_mic_minus_remote_rms_db": min_mic_minus_remote_rms_db,
        },
        "accepted_batch_row_count": len(accepted_rows),
        "accepted_batch_seconds": safe_float(local_island_split_lab_report.get("accepted_batch_seconds")),
        "accepted_local_island_seconds": safe_float(local_island_split_lab_report.get("accepted_local_island_seconds")),
        "audio_anchor_row_count": len(candidate_rows),
        "audio_anchor_island_count": anchor_count,
        "audio_anchor_seconds": round(anchor_seconds, 3),
        "examples": candidate_rows[:20],
    }


def live_local_island_retime_anchor_lab(local_island_split_lab_report: dict[str, Any]) -> dict[str, Any]:
    rows = (
        local_island_split_lab_report.get("examples")
        if isinstance(local_island_split_lab_report.get("examples"), list)
        else []
    )
    accepted_rows = [row for row in rows if isinstance(row, dict) and bool(row.get("accepted"))]
    examples: list[dict[str, Any]] = []
    total_batch_seconds = 0.0
    total_local_island_seconds = 0.0
    total_anchor_span_seconds = 0.0
    total_context_expansion_seconds = 0.0
    max_leading_gap = 0.0
    max_trailing_gap = 0.0
    max_inter_island_gap = 0.0

    for row in accepted_rows:
        islands = row.get("local_island_examples") if isinstance(row.get("local_island_examples"), list) else []
        islands = [island for island in islands if isinstance(island, dict)]
        if not islands:
            continue
        islands.sort(key=lambda item: (safe_float(item.get("start")), safe_float(item.get("end"))))
        batch_start = safe_float(row.get("start"))
        batch_end = safe_float(row.get("end"), batch_start)
        batch_seconds = max(0.0, batch_end - batch_start)
        island_start = min(safe_float(item.get("start")) for item in islands)
        island_end = max(safe_float(item.get("end"), item.get("start")) for item in islands)
        anchor_span_seconds = max(0.0, island_end - island_start)
        local_island_seconds = safe_float(row.get("local_island_seconds"))
        leading_gap = max(0.0, island_start - batch_start)
        trailing_gap = max(0.0, batch_end - island_end)
        inter_gaps = [
            max(0.0, safe_float(current.get("start")) - safe_float(previous.get("end"), previous.get("start")))
            for previous, current in zip(islands, islands[1:])
        ]
        inter_gap = max(inter_gaps) if inter_gaps else 0.0
        context_expansion = max(0.0, batch_seconds - anchor_span_seconds)

        total_batch_seconds += batch_seconds
        total_local_island_seconds += local_island_seconds
        total_anchor_span_seconds += anchor_span_seconds
        total_context_expansion_seconds += context_expansion
        max_leading_gap = max(max_leading_gap, leading_gap)
        max_trailing_gap = max(max_trailing_gap, trailing_gap)
        max_inter_island_gap = max(max_inter_island_gap, inter_gap)

        examples.append(
            {
                "session": row.get("session"),
                "batch_id": row.get("batch_id"),
                "batch_start": row.get("start"),
                "batch_end": row.get("end"),
                "batch_seconds": round(batch_seconds, 3),
                "local_island_seconds": round(local_island_seconds, 3),
                "anchor_span_start": round(island_start, 3),
                "anchor_span_end": round(island_end, 3),
                "anchor_span_seconds": round(anchor_span_seconds, 3),
                "context_expansion_seconds": round(context_expansion, 3),
                "leading_gap_seconds": round(leading_gap, 3),
                "trailing_gap_seconds": round(trailing_gap, 3),
                "max_inter_island_gap_seconds": round(inter_gap, 3),
                "local_island_coverage_ratio": (
                    round(local_island_seconds / batch_seconds, 6) if batch_seconds > 0 else None
                ),
                "anchor_span_coverage_ratio": (
                    round(anchor_span_seconds / batch_seconds, 6) if batch_seconds > 0 else None
                ),
                "token_recall_from_local_islands": row.get("token_recall_from_local_islands"),
                "retime_need": "expand_anchor_span_to_batch_interval",
                "online_missing_evidence": [
                    "batch_start_without_batch_truth",
                    "batch_end_without_batch_truth",
                    "safe_context_expansion_without_remote_duplication",
                    "contentful_order_gate_without_authoritative_batch_interval",
                ],
                "local_island_examples": islands,
                "batch_text": row.get("batch_text"),
                "local_island_text": row.get("local_island_text"),
            }
        )

    return {
        "schema": "murmurmark.live_local_island_retime_anchor_lab/v1",
        "status": "ok" if examples else "no_accepted_retime_anchor_rows",
        "promotion_allowed": False,
        "interpretation": (
            "diagnostic only: measures how much online retiming must expand trusted local-island "
            "anchors to match the batch Me interval; it uses batch timing and cannot promote live output"
        ),
        "accepted_row_count": len(examples),
        "batch_seconds": round(total_batch_seconds, 3),
        "local_island_seconds": round(total_local_island_seconds, 3),
        "anchor_span_seconds": round(total_anchor_span_seconds, 3),
        "context_expansion_seconds": round(total_context_expansion_seconds, 3),
        "local_island_coverage_ratio": (
            round(total_local_island_seconds / total_batch_seconds, 6) if total_batch_seconds > 0 else None
        ),
        "anchor_span_coverage_ratio": (
            round(total_anchor_span_seconds / total_batch_seconds, 6) if total_batch_seconds > 0 else None
        ),
        "max_leading_gap_seconds": round(max_leading_gap, 3),
        "max_trailing_gap_seconds": round(max_trailing_gap, 3),
        "max_inter_island_gap_seconds": round(max_inter_island_gap, 3),
        "required_online_evidence": [
            "detect local-island anchors without batch labels",
            "estimate safe left/right context around anchors from live mic/remote evidence",
            "reject context expansion that duplicates remote text",
            "preserve contentful order without authoritative batch intervals",
        ],
        "examples": examples,
    }


def suppressed_mic_segments_from_report_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    for row in rows:
        risk_examples = row.get("risk_examples") if isinstance(row.get("risk_examples"), dict) else {}
        inputs = row.get("inputs") if isinstance(row.get("inputs"), dict) else {}
        for item in risk_examples.get("suppressed_mic_asr_segments") or []:
            if not isinstance(item, dict):
                continue
            segment = dict(item)
            segment["session"] = row.get("session")
            segment["comparison"] = inputs.get("live_batch_comparison")
            segments.append(segment)
    return segments


def live_only_local_island_candidate_lab(rows: list[dict[str, Any]], *, limit: int = 30) -> dict[str, Any]:
    max_zero_lag_abs_corr = 0.03
    min_mic_minus_remote_rms_db = -6.0
    min_token_count = 2
    segments = suppressed_mic_segments_from_report_rows(rows)
    base_candidates: list[dict[str, Any]] = []
    missing_audio_metrics = 0
    for segment in segments:
        if bool(segment.get("known_hallucination")):
            continue
        if safe_int(segment.get("token_count")) < min_token_count:
            continue
        if segment.get("segment_gate_reason") != "segment_has_local_tokens_not_seen_in_overlapping_remote":
            continue
        zero_lag_abs_corr = optional_finite_float(segment.get("audio_mic_remote_zero_lag_abs_corr"))
        mic_minus_remote_rms_db = optional_finite_float(segment.get("audio_mic_minus_remote_rms_db"))
        if zero_lag_abs_corr is None or mic_minus_remote_rms_db is None:
            missing_audio_metrics += 1
            continue
        if zero_lag_abs_corr > max_zero_lag_abs_corr:
            continue
        if mic_minus_remote_rms_db < min_mic_minus_remote_rms_db:
            continue
        base_candidates.append(segment)

    def is_local(segment: dict[str, Any]) -> bool:
        return segment.get("batch_role_label") in {"me_dominant", "mixed"}

    def is_remote_risk(segment: dict[str, Any]) -> bool:
        return segment.get("batch_role_label") in {"remote_dominant", "none"}

    def summarize_candidates(candidates: list[dict[str, Any]]) -> dict[str, Any]:
        selected_seconds = round(sum(safe_float(row.get("duration_sec")) for row in candidates), 3)
        local_seconds = round(sum(safe_float(row.get("duration_sec")) for row in candidates if is_local(row)), 3)
        remote_risk_seconds = round(
            sum(safe_float(row.get("duration_sec")) for row in candidates if is_remote_risk(row)),
            3,
        )
        by_label: dict[str, dict[str, Any]] = {}
        by_session: dict[str, dict[str, Any]] = {}
        for row in candidates:
            label = str(row.get("batch_role_label") or "(none)")
            label_row = by_label.setdefault(label, {"count": 0, "seconds": 0.0})
            label_row["count"] += 1
            label_row["seconds"] = round(safe_float(label_row.get("seconds")) + safe_float(row.get("duration_sec")), 3)
            session = str(row.get("session") or "")
            session_row = by_session.setdefault(
                session,
                {"count": 0, "seconds": 0.0, "local_seconds": 0.0, "remote_risk_seconds": 0.0},
            )
            session_row["count"] += 1
            session_row["seconds"] = round(
                safe_float(session_row.get("seconds")) + safe_float(row.get("duration_sec")),
                3,
            )
            if is_local(row):
                session_row["local_seconds"] = round(
                    safe_float(session_row.get("local_seconds")) + safe_float(row.get("duration_sec")),
                    3,
                )
            elif is_remote_risk(row):
                session_row["remote_risk_seconds"] = round(
                    safe_float(session_row.get("remote_risk_seconds")) + safe_float(row.get("duration_sec")),
                    3,
                )
        return {
            "candidate_count": len(candidates),
            "candidate_seconds": selected_seconds,
            "local_seconds": local_seconds,
            "remote_risk_seconds": remote_risk_seconds,
            "precision_proxy": round(local_seconds / selected_seconds, 6) if selected_seconds > 0 else None,
            "remote_risk_ratio": round(remote_risk_seconds / selected_seconds, 6) if selected_seconds > 0 else None,
            "by_label": dict(sorted(by_label.items(), key=lambda pair: (-safe_float(pair[1].get("seconds")), pair[0]))),
            "by_session": dict(
                sorted(by_session.items(), key=lambda pair: (-safe_float(pair[1].get("seconds")), pair[0]))
            ),
        }

    strict_zero_remote_risk_candidates = [
        row
        for row in base_candidates
        if safe_float(row.get("audio_mic_remote_zero_lag_abs_corr")) <= 0.01
        and safe_float(row.get("audio_mic_minus_remote_rms_db")) >= min_mic_minus_remote_rms_db
        and safe_int(row.get("segment_gate_overlapping_remote_token_count")) <= 10
        and safe_float(row.get("segment_gate_mic_token_recall_in_overlapping_remote")) == 0.0
    ]

    base_summary = summarize_candidates(base_candidates)
    strict_summary = summarize_candidates(strict_zero_remote_risk_candidates)

    examples = sorted(
        base_candidates,
        key=lambda row: (
            0 if is_remote_risk(row) else 1,
            -safe_float(row.get("duration_sec")),
            str(row.get("session") or ""),
            safe_float(row.get("start")),
        ),
    )
    return {
        "schema": "murmurmark.live_only_local_island_candidate_lab/v1",
        "status": "ok" if base_candidates else "no_candidates",
        "promotion_allowed": False,
        "interpretation": (
            "diagnostic only: selects suppressed mic segments using live-available text/audio gates, "
            "then uses batch labels only to estimate precision and remote risk"
        ),
        "criteria": {
            "segment_gate_reason": "segment_has_local_tokens_not_seen_in_overlapping_remote",
            "min_token_count": min_token_count,
            "require_audio_metrics": True,
            "max_audio_mic_remote_zero_lag_abs_corr": max_zero_lag_abs_corr,
            "min_audio_mic_minus_remote_rms_db": min_mic_minus_remote_rms_db,
        },
        "excluded_missing_audio_metrics_count": missing_audio_metrics,
        **base_summary,
        "stricter_profiles": {
            "strict_zero_remote_risk_text_audio_v1": {
                "status": "ok" if strict_zero_remote_risk_candidates else "no_candidates",
                "promotion_allowed": False,
                "interpretation": (
                    "diagnostic only: zero remote-risk under current batch labels; next step is "
                    "materialization plus ordinary live parity gates"
                ),
                "criteria": {
                    "segment_gate_reason": "segment_has_local_tokens_not_seen_in_overlapping_remote",
                    "min_token_count": min_token_count,
                    "require_audio_metrics": True,
                    "max_audio_mic_remote_zero_lag_abs_corr": 0.01,
                    "min_audio_mic_minus_remote_rms_db": min_mic_minus_remote_rms_db,
                    "max_segment_gate_overlapping_remote_token_count": 10,
                    "max_segment_gate_mic_token_recall_in_overlapping_remote": 0.0,
                },
                **strict_summary,
            }
        },
        "examples": [
            {
                "session": row.get("session"),
                "chunk_index": row.get("chunk_index"),
                "start": row.get("start"),
                "end": row.get("end"),
                "duration_sec": row.get("duration_sec"),
                "text": row.get("text"),
                "batch_role_label": row.get("batch_role_label"),
                "segment_gate_status": row.get("segment_gate_status"),
                "segment_gate_reason": row.get("segment_gate_reason"),
                "audio_mic_minus_remote_rms_db": row.get("audio_mic_minus_remote_rms_db"),
                "audio_mic_remote_zero_lag_abs_corr": row.get("audio_mic_remote_zero_lag_abs_corr"),
                "comparison": row.get("comparison"),
            }
            for row in examples[:limit]
        ],
    }


def live_only_retime_boundary_candidate_lab(
    rows: list[dict[str, Any]],
    *,
    remaining_gap: dict[str, Any] | None = None,
    limit: int = 30,
) -> dict[str, Any]:
    segments = suppressed_mic_segments_from_report_rows(rows)
    max_anchor_gap_sec = 3.0
    relaxed_anchor_rows = [
        row
        for row in segments
        if not bool(row.get("known_hallucination"))
        and safe_int(row.get("token_count")) >= 2
        and row.get("segment_gate_reason") == "segment_has_local_tokens_not_seen_in_overlapping_remote"
        and safe_float(row.get("audio_mic_remote_zero_lag_abs_corr"), default=1.0) <= 0.03
        and safe_float(row.get("audio_mic_minus_remote_rms_db"), default=-999.0) >= -6.0
    ]
    strict_anchor_rows = [
        row
        for row in relaxed_anchor_rows
        if not bool(row.get("known_hallucination"))
        and safe_float(row.get("audio_mic_remote_zero_lag_abs_corr"), default=1.0) <= 0.01
        and safe_int(row.get("segment_gate_overlapping_remote_token_count")) <= 10
        and safe_float(row.get("segment_gate_mic_token_recall_in_overlapping_remote"), default=1.0) == 0.0
    ]

    missing_rows: list[dict[str, Any]] = []
    suspicious_missing_rows: list[dict[str, Any]] = []
    remaining_examples = (
        remaining_gap.get("examples")
        if isinstance(remaining_gap, dict) and isinstance(remaining_gap.get("examples"), list)
        else []
    )
    if remaining_examples:
        for item in remaining_examples:
            if isinstance(item, dict):
                copied = dict(item)
                copied["session"] = str(copied.get("session") or "")
                missing_rows.append(copied)
    else:
        for row in rows:
            session = str(row.get("session") or "")
            risk_examples = row.get("risk_examples") if isinstance(row.get("risk_examples"), dict) else {}
            for item in risk_examples.get("local_missing") or []:
                if isinstance(item, dict):
                    copied = dict(item)
                    copied["session"] = session
                    missing_rows.append(copied)
            for item in risk_examples.get("local_missing_suspicious_batch_me") or []:
                if isinstance(item, dict):
                    copied = dict(item)
                    copied["session"] = session
                    suspicious_missing_rows.append(copied)

    remaining_gap_evidence_segments: list[dict[str, Any]] = []
    for item in remaining_examples:
        if not isinstance(item, dict):
            continue
        session = str(item.get("session") or "")
        evidence_rows = item.get("suppressed_mic_evidence") if isinstance(item.get("suppressed_mic_evidence"), list) else []
        for evidence in evidence_rows:
            if isinstance(evidence, dict):
                copied = dict(evidence)
                copied["session"] = session
                if copied.get("token_count") is None:
                    copied["token_count"] = len(text_tokens(str(copied.get("text") or "")))
                if copied.get("segment_gate_unique_token_count") is None:
                    copied["segment_gate_unique_token_count"] = copied.get("unique_token_count")
                if copied.get("segment_gate_mic_token_recall_in_overlapping_remote") is None:
                    copied["segment_gate_mic_token_recall_in_overlapping_remote"] = copied.get(
                        "mic_token_recall_in_overlapping_remote"
                    )
                if copied.get("segment_gate_overlapping_remote_token_recall_in_mic") is None:
                    copied["segment_gate_overlapping_remote_token_recall_in_mic"] = copied.get(
                        "overlapping_remote_token_recall_in_mic"
                    )
                if copied.get("segment_gate_overlapping_remote_token_count") is None:
                    copied["segment_gate_overlapping_remote_token_count"] = 0
                remaining_gap_evidence_segments.append(copied)
    if remaining_gap_evidence_segments:
        deduped_segments: dict[tuple[str, int, float, float, str], dict[str, Any]] = {}
        for row in segments + remaining_gap_evidence_segments:
            key = (
                str(row.get("session") or ""),
                safe_int(row.get("chunk_index")),
                round(safe_float(row.get("start")), 3),
                round(safe_float(row.get("end"), row.get("start")), 3),
                str(row.get("text") or ""),
            )
            deduped_segments[key] = row
        segments = list(deduped_segments.values())
        relaxed_anchor_rows = [
            row
            for row in segments
            if not bool(row.get("known_hallucination"))
            and safe_int(row.get("token_count")) >= 2
            and row.get("segment_gate_reason") == "segment_has_local_tokens_not_seen_in_overlapping_remote"
            and safe_float(row.get("audio_mic_remote_zero_lag_abs_corr"), default=1.0) <= 0.03
            and safe_float(row.get("audio_mic_minus_remote_rms_db"), default=-999.0) >= -6.0
        ]
        strict_anchor_rows = [
            row
            for row in relaxed_anchor_rows
            if not bool(row.get("known_hallucination"))
            and safe_float(row.get("audio_mic_remote_zero_lag_abs_corr"), default=1.0) <= 0.01
            and safe_int(row.get("segment_gate_overlapping_remote_token_count")) <= 10
            and safe_float(row.get("segment_gate_mic_token_recall_in_overlapping_remote"), default=1.0) == 0.0
        ]

    def row_interval(row: dict[str, Any]) -> tuple[float, float]:
        start = safe_float(row.get("start"))
        end = safe_float(row.get("end"), start)
        return start, max(start, end)

    def duration(interval: dict[str, Any]) -> float:
        return max(0.0, safe_float(interval.get("end")) - safe_float(interval.get("start")))

    def merge_intervals(intervals: list[dict[str, Any]]) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        for interval in sorted(intervals, key=lambda item: (str(item.get("session") or ""), safe_float(item.get("start")))):
            session = str(interval.get("session") or "")
            start = safe_float(interval.get("start"))
            end = safe_float(interval.get("end"), start)
            if end <= start:
                continue
            if merged and str(merged[-1].get("session") or "") == session and start <= safe_float(merged[-1].get("end")):
                merged[-1]["end"] = round(max(safe_float(merged[-1].get("end")), end), 3)
                continue
            merged.append({"session": session, "start": round(start, 3), "end": round(end, 3)})
        return merged

    def subtract_intervals(
        start: float,
        end: float,
        forbidden: list[tuple[float, float]],
        *,
        min_interval_sec: float,
    ) -> list[tuple[float, float]]:
        pieces = [(start, end)]
        for forbidden_start, forbidden_end in sorted(forbidden):
            if forbidden_end <= start or forbidden_start >= end:
                continue
            next_pieces: list[tuple[float, float]] = []
            for piece_start, piece_end in pieces:
                if forbidden_end <= piece_start or forbidden_start >= piece_end:
                    next_pieces.append((piece_start, piece_end))
                    continue
                if forbidden_start > piece_start:
                    next_pieces.append((piece_start, min(forbidden_start, piece_end)))
                if forbidden_end < piece_end:
                    next_pieces.append((max(forbidden_end, piece_start), piece_end))
            pieces = next_pieces
        return [
            (round(piece_start, 3), round(piece_end, 3))
            for piece_start, piece_end in pieces
            if piece_end - piece_start >= min_interval_sec
        ]

    def overlap_with_rows(intervals: list[dict[str, Any]], evidence_rows: list[dict[str, Any]]) -> float:
        total = 0.0
        for interval in intervals:
            session = str(interval.get("session") or "")
            start = safe_float(interval.get("start"))
            end = safe_float(interval.get("end"), start)
            for row in evidence_rows:
                if str(row.get("session") or "") != session:
                    continue
                row_start, row_end = row_interval(row)
                total += interval_overlap(start, end, row_start, row_end)
        return round(total, 3)

    def grouped_anchors(anchor_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        groups: list[dict[str, Any]] = []
        by_session_chunk: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
        for row in anchor_rows:
            by_session_chunk[(str(row.get("session") or ""), safe_int(row.get("chunk_index")))].append(row)
        for (session, chunk_index), chunk_rows in sorted(by_session_chunk.items()):
            current: list[dict[str, Any]] = []
            for row in sorted(chunk_rows, key=lambda item: (safe_float(item.get("start")), safe_float(item.get("end")))):
                if not current:
                    current = [row]
                    continue
                previous_end = safe_float(current[-1].get("end"), current[-1].get("start"))
                if safe_float(row.get("start")) - previous_end <= max_anchor_gap_sec:
                    current.append(row)
                    continue
                groups.append({"session": session, "chunk_index": chunk_index, "anchors": current})
                current = [row]
            if current:
                groups.append({"session": session, "chunk_index": chunk_index, "anchors": current})
        return groups

    anchor_sets = {
        "strict_zero_remote_anchor": strict_anchor_rows,
        "relaxed_audio_text_anchor": relaxed_anchor_rows,
    }
    profiles = {
        "anchor_span_v1": {"left_sec": 0.0, "right_sec": 0.0},
        "short_context_v1": {"left_sec": 3.0, "right_sec": 1.0},
        "oracle_gap_probe_v1": {"left_sec": 10.0, "right_sec": 1.0},
        "remote_forbidden_trimmed_oracle_gap_probe_v1": {
            "left_sec": 10.0,
            "right_sec": 1.0,
            "remote_forbidden_gate": True,
            "min_interval_sec": 0.35,
        },
        "remote_forbidden_boundary_classifier_v1": {
            "left_sec": 10.0,
            "right_sec": 1.0,
            "remote_forbidden_gate": True,
            "live_group_classifier": "remote_forbidden_multi_cut_v1",
            "min_interval_sec": 0.35,
            "min_remote_forbidden_cut_sec": 3.5,
            "min_remote_forbidden_row_count": 2,
            "min_anchor_span_sec": 3.0,
            "min_candidate_sec_after_trim": 6.0,
        },
    }

    local_segments = [row for row in segments if row.get("batch_role_label") in {"me_dominant", "mixed"}]
    remote_risk_segments = [
        row
        for row in segments
        if row.get("batch_role_label") in {"remote_dominant", "none", "known_hallucination"}
    ]
    remote_forbidden_rows = [
        row
        for row in segments
        if bool(row.get("known_hallucination"))
        or safe_float(row.get("segment_gate_mic_token_recall_in_overlapping_remote")) >= 0.4
        or safe_float(row.get("segment_gate_overlapping_remote_token_recall_in_mic")) >= 0.25
        or (
            row.get("segment_gate_reason") == "segment_duplicates_overlapping_remote"
            and safe_int(row.get("segment_gate_unique_token_count") or row.get("unique_token_count")) <= 1
        )
        or (
            row.get("segment_gate_reason") == "segment_duplicates_overlapping_remote"
            and safe_float(row.get("audio_mic_minus_remote_rms_db"), default=999.0) <= -8.0
        )
    ]
    profile_reports: dict[str, dict[str, Any]] = {}
    anchor_set_summaries: dict[str, dict[str, Any]] = {}
    for anchor_set_name, anchor_rows in anchor_sets.items():
        anchor_groups = grouped_anchors(anchor_rows)
        anchor_set_summaries[anchor_set_name] = {
            "anchor_segment_count": len(anchor_rows),
            "anchor_group_count": len(anchor_groups),
            "anchor_seconds": round(sum(safe_float(row.get("duration_sec")) for row in anchor_rows), 3),
        }
        for profile_name, config in profiles.items():
            full_profile_name = f"{anchor_set_name}_{profile_name}"
            raw_intervals: list[dict[str, Any]] = []
            examples: list[dict[str, Any]] = []
            for group in anchor_groups:
                anchors = group.get("anchors") if isinstance(group.get("anchors"), list) else []
                if not anchors:
                    continue
                session = str(group.get("session") or "")
                anchor_start = min(safe_float(row.get("start")) for row in anchors)
                anchor_end = max(safe_float(row.get("end"), row.get("start")) for row in anchors)
                start = max(0.0, anchor_start - safe_float(config.get("left_sec")))
                end = anchor_end + safe_float(config.get("right_sec"))
                forbidden = [
                    row_interval(row)
                    for row in remote_forbidden_rows
                    if str(row.get("session") or "") == session
                    and interval_overlap(start, end, *row_interval(row)) > 0
                ]
                kept_pieces = [(round(start, 3), round(end, 3))]
                if config.get("remote_forbidden_gate"):
                    kept_pieces = subtract_intervals(
                        start,
                        end,
                        forbidden,
                        min_interval_sec=safe_float(config.get("min_interval_sec"), 0.35),
                    )
                raw_candidate_seconds = round(max(0.0, end - start), 3)
                candidate_seconds_after_trim = round(
                    sum(max(0.0, piece_end - piece_start) for piece_start, piece_end in kept_pieces),
                    3,
                )
                remote_forbidden_cut_seconds = round(max(0.0, raw_candidate_seconds - candidate_seconds_after_trim), 3)
                anchor_span_seconds = round(max(0.0, anchor_end - anchor_start), 3)
                classifier_reasons: list[str] = []
                classifier_passed = True
                if config.get("live_group_classifier") == "remote_forbidden_multi_cut_v1":
                    if remote_forbidden_cut_seconds < safe_float(config.get("min_remote_forbidden_cut_sec")):
                        classifier_passed = False
                        classifier_reasons.append("insufficient_remote_forbidden_cut")
                    if len(forbidden) < safe_int(config.get("min_remote_forbidden_row_count")):
                        classifier_passed = False
                        classifier_reasons.append("insufficient_remote_forbidden_rows")
                    if anchor_span_seconds < safe_float(config.get("min_anchor_span_sec")):
                        classifier_passed = False
                        classifier_reasons.append("anchor_span_too_short")
                    if candidate_seconds_after_trim < safe_float(config.get("min_candidate_sec_after_trim")):
                        classifier_passed = False
                        classifier_reasons.append("candidate_after_trim_too_short")
                    if not classifier_passed:
                        kept_pieces = []
                        candidate_seconds_after_trim = 0.0
                for piece_start, piece_end in kept_pieces:
                    raw_intervals.append({"session": session, "start": round(piece_start, 3), "end": round(piece_end, 3)})
                group_intervals = [
                    {"session": session, "start": piece_start, "end": piece_end}
                    for piece_start, piece_end in kept_pieces
                ]
                examples.append(
                    {
                        "session": session,
                        "chunk_index": group.get("chunk_index"),
                        "start": round(start, 3),
                        "end": round(end, 3),
                        "candidate_seconds": candidate_seconds_after_trim,
                        "raw_candidate_seconds": raw_candidate_seconds,
                        "remote_forbidden_gate": bool(config.get("remote_forbidden_gate")),
                        "live_group_classifier": config.get("live_group_classifier"),
                        "live_group_classifier_passed": classifier_passed,
                        "live_group_classifier_reasons": classifier_reasons,
                        "remote_forbidden_cut_seconds": remote_forbidden_cut_seconds,
                        "remote_forbidden_row_count": len(forbidden),
                        "intervals": group_intervals,
                        "anchor_start": round(anchor_start, 3),
                        "anchor_end": round(anchor_end, 3),
                        "anchor_span_seconds": anchor_span_seconds,
                        "anchor_seconds": round(sum(safe_float(row.get("duration_sec")) for row in anchors), 3),
                        "anchor_count": len(anchors),
                        "anchor_set": anchor_set_name,
                        "anchor_text": " ".join(
                            " ".join(str(row.get("text") or "").split()) for row in anchors
                        ).strip(),
                        "local_missing_overlap_seconds": overlap_with_rows(
                            group_intervals,
                            missing_rows,
                        ),
                        "suppressed_remote_risk_seconds": overlap_with_rows(
                            group_intervals,
                            remote_risk_segments,
                        ),
                        "suppressed_local_seconds": overlap_with_rows(
                            group_intervals,
                            local_segments,
                        ),
                    }
                )
            merged = merge_intervals(raw_intervals)
            candidate_seconds = round(sum(duration(interval) for interval in merged), 3)
            local_missing_overlap_seconds = overlap_with_rows(merged, missing_rows)
            suspicious_missing_overlap_seconds = overlap_with_rows(merged, suspicious_missing_rows)
            suppressed_local_seconds = overlap_with_rows(merged, local_segments)
            suppressed_remote_risk_seconds = overlap_with_rows(merged, remote_risk_segments)
            evaluated_suppressed_seconds = round(suppressed_local_seconds + suppressed_remote_risk_seconds, 3)
            examples.sort(
                key=lambda item: (
                    -safe_float(item.get("local_missing_overlap_seconds")),
                    -safe_float(item.get("suppressed_remote_risk_seconds")),
                    str(item.get("session") or ""),
                    safe_float(item.get("start")),
                )
            )
            profile_reports[full_profile_name] = {
                "status": "ok" if merged else "no_candidates",
                "promotion_allowed": False,
                "evaluation_only": False,
                "anchor_set": anchor_set_name,
                "left_context_sec": config.get("left_sec"),
                "right_context_sec": config.get("right_sec"),
                "remote_forbidden_gate": bool(config.get("remote_forbidden_gate")),
                "live_group_classifier": config.get("live_group_classifier"),
                "live_group_classifier_criteria": (
                    {
                        "min_remote_forbidden_cut_sec": config.get("min_remote_forbidden_cut_sec"),
                        "min_remote_forbidden_row_count": config.get("min_remote_forbidden_row_count"),
                        "min_anchor_span_sec": config.get("min_anchor_span_sec"),
                        "min_candidate_sec_after_trim": config.get("min_candidate_sec_after_trim"),
                    }
                    if config.get("live_group_classifier")
                    else None
                ),
                "remote_forbidden_gate_criteria": (
                    {
                        "known_hallucination": True,
                        "min_mic_token_recall_in_overlapping_remote": 0.4,
                        "min_overlapping_remote_token_recall_in_mic": 0.25,
                        "duplicate_max_unique_token_count": 1,
                        "duplicate_max_audio_mic_minus_remote_rms_db": -8.0,
                        "min_kept_interval_sec": config.get("min_interval_sec"),
                    }
                    if config.get("remote_forbidden_gate")
                    else None
                ),
                "candidate_count": len(merged),
                "candidate_seconds": candidate_seconds,
                "anchor_group_count": len(anchor_groups),
                "anchor_seconds": round(sum(safe_float(row.get("duration_sec")) for row in anchor_rows), 3),
                "local_missing_overlap_seconds": local_missing_overlap_seconds,
                "suspicious_missing_overlap_seconds": suspicious_missing_overlap_seconds,
                "suppressed_local_seconds": suppressed_local_seconds,
                "suppressed_remote_risk_seconds": suppressed_remote_risk_seconds,
                "evaluated_suppressed_seconds": evaluated_suppressed_seconds,
                "precision_proxy": (
                    round(suppressed_local_seconds / evaluated_suppressed_seconds, 6)
                    if evaluated_suppressed_seconds > 0
                    else None
                ),
                "remote_risk_ratio": (
                    round(suppressed_remote_risk_seconds / evaluated_suppressed_seconds, 6)
                    if evaluated_suppressed_seconds > 0
                    else None
                ),
                "missing_overlap_per_candidate_second": (
                    round(local_missing_overlap_seconds / candidate_seconds, 6) if candidate_seconds > 0 else None
                ),
                "examples": examples[:limit],
                "truncated": len(examples) > limit,
                "limit": limit,
            }

    trimmed_relaxed_profile_name = "relaxed_audio_text_anchor_remote_forbidden_trimmed_oracle_gap_probe_v1"
    trimmed_relaxed_profile = profile_reports.get(trimmed_relaxed_profile_name)
    if isinstance(trimmed_relaxed_profile, dict):
        accepted_examples = [
            example
            for example in trimmed_relaxed_profile.get("examples") or []
            if isinstance(example, dict)
            and safe_float(example.get("suppressed_remote_risk_seconds")) == 0.0
            and safe_float(example.get("local_missing_overlap_seconds")) > 0.0
        ]
        evaluated_intervals: list[dict[str, Any]] = []
        for example in accepted_examples:
            for interval in example.get("intervals") or []:
                if isinstance(interval, dict):
                    evaluated_intervals.append(
                        {
                            "session": str(interval.get("session") or ""),
                            "start": safe_float(interval.get("start")),
                            "end": safe_float(interval.get("end"), interval.get("start")),
                        }
                    )
        merged = merge_intervals(evaluated_intervals)
        candidate_seconds = round(sum(duration(interval) for interval in merged), 3)
        local_missing_overlap_seconds = overlap_with_rows(merged, missing_rows)
        suspicious_missing_overlap_seconds = overlap_with_rows(merged, suspicious_missing_rows)
        suppressed_local_seconds = overlap_with_rows(merged, local_segments)
        suppressed_remote_risk_seconds = overlap_with_rows(merged, remote_risk_segments)
        evaluated_suppressed_seconds = round(suppressed_local_seconds + suppressed_remote_risk_seconds, 3)
        profile_reports[
            "relaxed_audio_text_anchor_remote_forbidden_trimmed_zero_remote_evaluated_gate_v1"
        ] = {
            "status": "ok" if merged else "no_candidates",
            "promotion_allowed": False,
            "evaluation_only": True,
            "interpretation": (
                "diagnostic ceiling: keeps only trimmed relaxed-anchor groups that have zero "
                "evaluated remote-risk; batch labels are used for this acceptance decision"
            ),
            "source_profile": trimmed_relaxed_profile_name,
            "anchor_set": "relaxed_audio_text_anchor",
            "remote_forbidden_gate": True,
            "candidate_count": len(merged),
            "accepted_group_count": len(accepted_examples),
            "candidate_seconds": candidate_seconds,
            "local_missing_overlap_seconds": local_missing_overlap_seconds,
            "suspicious_missing_overlap_seconds": suspicious_missing_overlap_seconds,
            "suppressed_local_seconds": suppressed_local_seconds,
            "suppressed_remote_risk_seconds": suppressed_remote_risk_seconds,
            "evaluated_suppressed_seconds": evaluated_suppressed_seconds,
            "precision_proxy": (
                round(suppressed_local_seconds / evaluated_suppressed_seconds, 6)
                if evaluated_suppressed_seconds > 0
                else None
            ),
            "remote_risk_ratio": (
                round(suppressed_remote_risk_seconds / evaluated_suppressed_seconds, 6)
                if evaluated_suppressed_seconds > 0
                else None
            ),
            "missing_overlap_per_candidate_second": (
                round(local_missing_overlap_seconds / candidate_seconds, 6) if candidate_seconds > 0 else None
            ),
            "examples": accepted_examples[:limit],
            "truncated": len(accepted_examples) > limit,
            "limit": limit,
        }

    recommended_profile = None
    zero_remote_profiles = [
        (name, report)
        for name, report in profile_reports.items()
        if not bool(report.get("evaluation_only"))
        and safe_float(report.get("suppressed_remote_risk_seconds")) == 0.0
        and safe_float(report.get("local_missing_overlap_seconds")) > 0.0
    ]
    if zero_remote_profiles:
        recommended_profile = max(
            zero_remote_profiles,
            key=lambda pair: (
                safe_float(pair[1].get("local_missing_overlap_seconds")),
                -safe_float(pair[1].get("candidate_seconds")),
            ),
        )[0]
    best_zero_remote_evaluated_profile = None
    zero_remote_evaluated_profiles = [
        (name, report)
        for name, report in profile_reports.items()
        if bool(report.get("evaluation_only"))
        and safe_float(report.get("suppressed_remote_risk_seconds")) == 0.0
        and safe_float(report.get("local_missing_overlap_seconds")) > 0.0
    ]
    if zero_remote_evaluated_profiles:
        best_zero_remote_evaluated_profile = max(
            zero_remote_evaluated_profiles,
            key=lambda pair: (
                safe_float(pair[1].get("local_missing_overlap_seconds")),
                -safe_float(pair[1].get("candidate_seconds")),
            ),
        )[0]
    best_missing_overlap_profile = None
    if profile_reports:
        best_missing_overlap_profile = max(
            profile_reports.items(),
            key=lambda pair: (
                safe_float(pair[1].get("local_missing_overlap_seconds")),
                -safe_float(pair[1].get("suppressed_remote_risk_seconds")),
                -safe_float(pair[1].get("candidate_seconds")),
            ),
        )[0]
    return {
        "schema": "murmurmark.live_only_retime_boundary_candidate_lab/v1",
        "status": "ok" if anchor_groups else "no_live_only_anchor_groups",
        "promotion_allowed": False,
        "interpretation": (
            "diagnostic only: groups strict live-only local anchors and probes fixed online context "
            "expansions; batch labels are used only to estimate local recall and remote-risk"
        ),
        "criteria": {
            "anchor_segment_gate_reason": "segment_has_local_tokens_not_seen_in_overlapping_remote",
            "anchor_min_token_count": 2,
            "relaxed_anchor_max_audio_mic_remote_zero_lag_abs_corr": 0.03,
            "strict_anchor_max_audio_mic_remote_zero_lag_abs_corr": 0.01,
            "anchor_min_audio_mic_minus_remote_rms_db": -6.0,
            "strict_anchor_max_overlapping_remote_token_count": 10,
            "strict_anchor_max_mic_token_recall_in_overlapping_remote": 0.0,
            "max_anchor_gap_sec": max_anchor_gap_sec,
        },
        "evaluation_gap_source": "best_live_implementable_remaining_gap" if remaining_examples else "raw_live_missing",
        "evaluation_gap_seconds": round(sum(safe_float(row.get("duration_sec")) for row in missing_rows), 3),
        "candidate_pool_source": (
            "top_level_suppressed_mic_segments_plus_remaining_gap_evidence"
            if remaining_gap_evidence_segments
            else "top_level_suppressed_mic_segments"
        ),
        "candidate_pool_segment_count": len(segments),
        "remaining_gap_evidence_segment_count": len(remaining_gap_evidence_segments),
        "anchor_sets": anchor_set_summaries,
        "anchor_group_count": safe_int(anchor_set_summaries.get("strict_zero_remote_anchor", {}).get("anchor_group_count")),
        "anchor_segment_count": safe_int(anchor_set_summaries.get("strict_zero_remote_anchor", {}).get("anchor_segment_count")),
        "anchor_seconds": safe_float(anchor_set_summaries.get("strict_zero_remote_anchor", {}).get("anchor_seconds")),
        "recommended_profile": recommended_profile,
        "best_missing_overlap_profile": best_missing_overlap_profile,
        "best_zero_remote_evaluated_profile": best_zero_remote_evaluated_profile,
        "recommended_next": (
            "design_online_context_boundary_gate"
            if recommended_profile
            else "implement_remote_forbidden_boundary_classifier"
            if best_zero_remote_evaluated_profile
            else "relaxed_anchor_boundary_gate_needed"
            if best_missing_overlap_profile
            else "no_live_only_boundary_candidate_found"
        ),
        "profiles": profile_reports,
    }


def live_strict_local_island_shadow_delta_lab(
    rows: list[dict[str, Any]],
    *,
    root: Path,
    base_policy: str,
    combined_policy: str,
    limit: int = 30,
) -> dict[str, Any]:
    examples: list[dict[str, Any]] = []
    missing_inputs: list[str] = []
    total_extra_turn_seconds = 0.0
    total_closed_missing_seconds = 0.0
    total_new_missing_seconds = 0.0

    def target_profile(comparison: dict[str, Any], policy: str) -> dict[str, Any]:
        shadow_profiles = comparison.get("shadow_profiles") if isinstance(comparison.get("shadow_profiles"), dict) else {}
        target_profiles = shadow_profiles.get("target_me") if isinstance(shadow_profiles.get("target_me"), dict) else {}
        profile = target_profiles.get(policy) if isinstance(target_profiles.get(policy), dict) else {}
        return profile

    def profile_missing(profile: dict[str, Any]) -> dict[str, dict[str, Any]]:
        risk_examples = profile.get("risk_examples") if isinstance(profile.get("risk_examples"), dict) else {}
        result: dict[str, dict[str, Any]] = {}
        for item in risk_examples.get("local_missing") or []:
            if not isinstance(item, dict):
                continue
            batch_id = str(item.get("batch_id") or "")
            if batch_id:
                result[batch_id] = item
        return result

    def profile_draft_turns(session_root: Path, profile: dict[str, Any]) -> list[dict[str, Any]]:
        outputs = profile.get("outputs") if isinstance(profile.get("outputs"), dict) else {}
        draft_rel = outputs.get("draft_json")
        if not draft_rel:
            return []
        draft = read_json(session_root / str(draft_rel))
        if not isinstance(draft, dict):
            return []
        turns = draft.get("turns")
        return [turn for turn in turns if isinstance(turn, dict)] if isinstance(turns, list) else []

    def turn_key(turn: dict[str, Any]) -> tuple[int, float, float, str]:
        return (
            safe_int(turn.get("chunk_index")),
            round(safe_float(turn.get("start")), 3),
            round(safe_float(turn.get("end")), 3),
            str(turn.get("role") or ""),
        )

    def segment_key(segment: dict[str, Any]) -> tuple[int, float, float]:
        return (
            safe_int(segment.get("chunk_index")),
            round(safe_float(segment.get("start")), 3),
            round(safe_float(segment.get("end")), 3),
        )

    def classify_turn(
        turn: dict[str, Any],
        *,
        segment: dict[str, Any],
        base_missing: dict[str, dict[str, Any]],
        combined_missing: dict[str, dict[str, Any]],
    ) -> str:
        start = safe_float(turn.get("start"))
        end = safe_float(turn.get("end"), start)
        if segment.get("batch_role_label") in {"remote_dominant", "none"}:
            return "remote_risk"
        closed_overlap = 0.0
        unclosed_overlap = 0.0
        for batch_id, item in base_missing.items():
            overlap = interval_overlap(start, end, safe_float(item.get("start")), safe_float(item.get("end")))
            if overlap <= 0:
                continue
            if batch_id in combined_missing:
                unclosed_overlap += overlap
            else:
                closed_overlap += overlap
        if closed_overlap > 0 and unclosed_overlap == 0:
            return "closed_missing_me"
        if closed_overlap > 0:
            return "partially_closed_missing_me"
        if unclosed_overlap > 0:
            return "overlaps_unclosed_missing_me"
        if segment.get("batch_role_label") in {"me_dominant", "mixed"}:
            return "already_covered_by_base_or_not_counted_missing"
        return "unclassified"

    for row in rows:
        session_name = str(row.get("session") or "")
        if not session_name:
            continue
        session_root = root / session_name
        inputs = row.get("inputs") if isinstance(row.get("inputs"), dict) else {}
        comparison_rel = inputs.get("live_batch_comparison")
        comparison_path = session_root / str(comparison_rel or "derived/live/live_batch_comparison.json")
        comparison = read_json(comparison_path)
        if not isinstance(comparison, dict):
            missing_inputs.append(session_name)
            continue
        base_profile = target_profile(comparison, base_policy)
        combined_profile = target_profile(comparison, combined_policy)
        if not base_profile or not combined_profile:
            missing_inputs.append(session_name)
            continue
        base_missing = profile_missing(base_profile)
        combined_missing = profile_missing(combined_profile)
        closed_missing = {
            batch_id: item
            for batch_id, item in base_missing.items()
            if batch_id not in combined_missing
        }
        new_missing = {
            batch_id: item
            for batch_id, item in combined_missing.items()
            if batch_id not in base_missing
        }
        total_closed_missing_seconds += sum(safe_float(item.get("duration_sec")) for item in closed_missing.values())
        total_new_missing_seconds += sum(safe_float(item.get("duration_sec")) for item in new_missing.values())

        base_turn_keys = {turn_key(turn) for turn in profile_draft_turns(session_root, base_profile)}
        combined_turns = profile_draft_turns(session_root, combined_profile)
        risk_examples = comparison.get("risk_examples") if isinstance(comparison.get("risk_examples"), dict) else {}
        suppressed_segments = [
            segment for segment in risk_examples.get("suppressed_mic_asr_segments") or []
            if isinstance(segment, dict)
        ]
        segment_by_key = {segment_key(segment): segment for segment in suppressed_segments}

        for turn in combined_turns:
            source = str(turn.get("source") or "")
            if source != "mic_suppressed_strict_live_only_local_island":
                continue
            if turn_key(turn) in base_turn_keys:
                continue
            start = safe_float(turn.get("start"))
            end = safe_float(turn.get("end"), start)
            duration = max(0.0, end - start)
            total_extra_turn_seconds += duration
            segment = segment_by_key.get(
                (
                    safe_int(turn.get("chunk_index")),
                    round(start, 3),
                    round(end, 3),
                ),
                {},
            )
            classification = classify_turn(
                turn,
                segment=segment,
                base_missing=base_missing,
                combined_missing=combined_missing,
            )
            examples.append(
                {
                    "session": session_name,
                    "chunk_index": turn.get("chunk_index"),
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "duration_sec": round(duration, 3),
                    "classification": classification,
                    "batch_role_label": segment.get("batch_role_label"),
                    "batch_role_evidence": segment.get("batch_role_evidence"),
                    "segment_gate_reason": segment.get("segment_gate_reason"),
                    "audio_mic_minus_remote_rms_db": segment.get("audio_mic_minus_remote_rms_db"),
                    "audio_mic_remote_zero_lag_abs_corr": segment.get("audio_mic_remote_zero_lag_abs_corr"),
                    "overlapping_base_missing_ids": [
                        batch_id for batch_id, item in base_missing.items()
                        if interval_overlap(start, end, safe_float(item.get("start")), safe_float(item.get("end"))) > 0
                    ],
                    "closed_missing_ids": list(closed_missing.keys()),
                    "new_missing_ids": list(new_missing.keys()),
                    "text": turn.get("text"),
                    "comparison": str(comparison_rel or "derived/live/live_batch_comparison.json"),
                }
            )

    def aggregate_by_classification() -> dict[str, dict[str, Any]]:
        grouped: dict[str, dict[str, Any]] = {}
        for item in examples:
            group = str(item.get("classification") or "unknown")
            row = grouped.setdefault(group, {"count": 0, "seconds": 0.0})
            row["count"] += 1
            row["seconds"] = round(safe_float(row.get("seconds")) + safe_float(item.get("duration_sec")), 3)
        return dict(sorted(grouped.items(), key=lambda pair: (-safe_float(pair[1].get("seconds")), pair[0])))

    examples.sort(key=lambda item: (-safe_float(item.get("duration_sec")), str(item.get("session") or ""), safe_float(item.get("start"))))
    extra_turn_seconds = round(total_extra_turn_seconds, 3)
    closed_seconds = round(total_closed_missing_seconds, 3)
    new_seconds = round(total_new_missing_seconds, 3)
    return {
        "schema": "murmurmark.live_strict_local_island_shadow_delta_lab/v1",
        "status": "ok" if examples else "no_incremental_strict_turns",
        "promotion_allowed": False,
        "base_policy": base_policy,
        "combined_policy": combined_policy,
        "incremental_strict_turn_count": len(examples),
        "incremental_strict_turn_seconds": extra_turn_seconds,
        "closed_missing_me_seconds": closed_seconds,
        "new_missing_me_seconds": new_seconds,
        "net_missing_me_delta_seconds": round(closed_seconds - new_seconds, 3),
        "missing_reduction_per_added_second": round(closed_seconds / extra_turn_seconds, 6) if extra_turn_seconds > 0 else None,
        "by_classification": aggregate_by_classification(),
        "interpretation": (
            "diagnostic only: explains whether strict live-only local-island turns close batch Me gaps "
            "or merely add safe but already-covered/non-material shadow text"
        ),
        "missing_inputs": sorted(set(missing_inputs)),
        "examples": examples[:limit],
        "truncated": len(examples) > limit,
        "limit": limit,
    }


def local_recall_rescue_lab(rows: list[dict[str, Any]], *, limit: int = 30) -> dict[str, Any]:
    segments = suppressed_mic_segments_from_report_rows(rows)

    def duration(item: dict[str, Any]) -> float:
        return safe_float(item.get("duration_sec"))

    def is_local(item: dict[str, Any]) -> bool:
        return item.get("batch_role_label") in {"me_dominant", "mixed"}

    def live_policies(item: dict[str, Any]) -> list[str]:
        policies = item.get("rescue_policy_candidates")
        if not isinstance(policies, list):
            return []
        return [str(policy) for policy in policies if str(policy) in LIVE_IMPLEMENTABLE_RESCUE_POLICIES]

    def has_live_policy(item: dict[str, Any]) -> bool:
        return bool(live_policies(item))

    local_segments = [item for item in segments if is_local(item)]
    live_selected = [item for item in segments if has_live_policy(item)]
    live_selected_local = [item for item in live_selected if is_local(item)]
    live_selected_remote_risk = [item for item in live_selected if not is_local(item)]
    speaker_needed = [item for item in local_segments if not has_live_policy(item)]
    text_duplicate_blocked_local = [
        item for item in speaker_needed
        if safe_float(item.get("segment_gate_mic_token_recall_in_overlapping_remote")) >= 0.70
        or safe_int(item.get("segment_gate_unique_token_count")) <= 2
    ]
    high_value_unrescued = sorted(
        speaker_needed,
        key=lambda item: (-duration(item), str(item.get("session") or ""), safe_float(item.get("start"))),
    )
    false_positive_remote_risk = sorted(
        live_selected_remote_risk,
        key=lambda item: (-duration(item), str(item.get("session") or ""), safe_float(item.get("start"))),
    )

    by_label_seconds: dict[str, float] = {}
    for item in segments:
        label = str(item.get("batch_role_label") or "unknown")
        by_label_seconds[label] = round(by_label_seconds.get(label, 0.0) + duration(item), 3)

    policy_seconds: dict[str, dict[str, Any]] = {}
    for policy in LIVE_IMPLEMENTABLE_RESCUE_POLICIES:
        selected = [
            item for item in segments
            if policy in (item.get("rescue_policy_candidates") or [])
        ]
        selected_seconds = sum(duration(item) for item in selected)
        local_seconds = sum(duration(item) for item in selected if is_local(item))
        remote_risk_seconds = selected_seconds - local_seconds
        policy_seconds[policy] = {
            "selected_count": len(selected),
            "selected_seconds": round(selected_seconds, 3),
            "local_seconds": round(local_seconds, 3),
            "remote_risk_seconds": round(remote_risk_seconds, 3),
            "precision_proxy": round(local_seconds / selected_seconds, 6) if selected_seconds > 0 else None,
        }

    recommended_next = "no_suppressed_mic_local_recall_gap"
    if sum(duration(item) for item in speaker_needed) >= 30.0:
        recommended_next = "add_target_speaker_or_stronger_local_evidence_for_suppressed_mic_segments"
    elif live_selected_remote_risk:
        recommended_next = "tighten_live_rescue_remote_forbidden_gate"
    elif live_selected_local:
        recommended_next = "evaluate_shadow_rescue_policy_on_capture_safe_candidates"

    def example(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "session": item.get("session"),
            "chunk_index": item.get("chunk_index"),
            "start": item.get("start"),
            "end": item.get("end"),
            "duration_sec": item.get("duration_sec"),
            "batch_role_label": item.get("batch_role_label"),
            "rescue_policy_candidates": item.get("rescue_policy_candidates") or [],
            "token_count": item.get("token_count"),
            "unique_token_count": item.get("segment_gate_unique_token_count"),
            "mic_token_recall_in_overlapping_remote": item.get(
                "segment_gate_mic_token_recall_in_overlapping_remote"
            ),
            "overlapping_remote_token_recall_in_mic": item.get(
                "segment_gate_overlapping_remote_token_recall_in_mic"
            ),
            "audio_mic_clean_rms_db": item.get("audio_mic_clean_rms_db"),
            "audio_remote_rms_db": item.get("audio_remote_rms_db"),
            "audio_mic_remote_zero_lag_abs_corr": item.get("audio_mic_remote_zero_lag_abs_corr"),
            "comparison": item.get("comparison"),
            "text": item.get("text"),
        }

    return {
        "schema": "murmurmark.live_local_recall_rescue_lab/v1",
        "segment_count": len(segments),
        "segment_seconds": round(sum(duration(item) for item in segments), 3),
        "local_segment_count": len(local_segments),
        "local_seconds": round(sum(duration(item) for item in local_segments), 3),
        "by_batch_role_seconds": by_label_seconds,
        "live_policy_selected_count": len(live_selected),
        "live_policy_selected_seconds": round(sum(duration(item) for item in live_selected), 3),
        "live_policy_local_seconds": round(sum(duration(item) for item in live_selected_local), 3),
        "live_policy_remote_risk_seconds": round(sum(duration(item) for item in live_selected_remote_risk), 3),
        "speaker_evidence_needed_count": len(speaker_needed),
        "speaker_evidence_needed_seconds": round(sum(duration(item) for item in speaker_needed), 3),
        "text_duplicate_blocked_local_count": len(text_duplicate_blocked_local),
        "text_duplicate_blocked_local_seconds": round(sum(duration(item) for item in text_duplicate_blocked_local), 3),
        "false_positive_remote_risk_count": len(false_positive_remote_risk),
        "false_positive_remote_risk_seconds": round(sum(duration(item) for item in false_positive_remote_risk), 3),
        "policy_seconds": policy_seconds,
        "recommended_next": recommended_next,
        "examples": {
            "high_value_unrescued": [example(item) for item in high_value_unrescued[:limit]],
            "false_positive_remote_risk": [example(item) for item in false_positive_remote_risk[:limit]],
            "live_policy_local": [
                example(item)
                for item in sorted(
                    live_selected_local,
                    key=lambda row: (-duration(row), str(row.get("session") or ""), safe_float(row.get("start"))),
                )[:limit]
            ],
        },
    }


def target_me_policy_metrics_from_summaries(summaries: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    total_local_seconds = round(sum(safe_float(row.get("local_seconds")) for row in summaries), 3)
    result: dict[str, dict[str, Any]] = {}
    for policy in TARGET_ME_RESCUE_POLICIES:
        selected_count = 0
        selected_seconds = 0.0
        local_seconds = 0.0
        remote_risk_seconds = 0.0
        missing_recovered_seconds = 0.0
        missing_after_seconds = 0.0
        for row in summaries:
            root = row.get("target_me_rescue_policy_metrics")
            metrics = root.get(policy) if isinstance(root, dict) else None
            if not isinstance(metrics, dict):
                continue
            selected_count += safe_int(metrics.get("selected_count"))
            selected_seconds += safe_float(metrics.get("selected_seconds"))
            local_seconds += safe_float(metrics.get("local_seconds"))
            remote_risk_seconds += safe_float(metrics.get("remote_risk_seconds"))
            missing_recovered_seconds += safe_float(metrics.get("missing_me_recovered_seconds"))
            missing_after_seconds += safe_float(metrics.get("missing_me_seconds_after"))
        result[policy] = {
            "selected_count": selected_count,
            "selected_seconds": round(selected_seconds, 3),
            "local_seconds": round(local_seconds, 3),
            "remote_risk_seconds": round(remote_risk_seconds, 3),
            "precision_proxy": round(local_seconds / selected_seconds, 6) if selected_seconds > 0 else None,
            "audited_local_recall_proxy": (
                round(local_seconds / total_local_seconds, 6) if total_local_seconds > 0 else None
            ),
            "missing_me_recovered_seconds": round(missing_recovered_seconds, 3),
            "missing_me_seconds_after": round(missing_after_seconds, 3),
        }
    return result


def live_local_recall_target_me_diagnostics(rows: list[dict[str, Any]], scope: str) -> dict[str, Any]:
    summaries = [
        row.get("live_local_recall_target_me")
        for row in rows
        if isinstance(row.get("live_local_recall_target_me"), dict)
    ]
    summaries = [row for row in summaries if isinstance(row, dict)]
    by_status = Counter(str(row.get("status") or "unknown") for row in summaries)
    policy_metrics = target_me_policy_metrics_from_summaries(summaries)
    best_policy: dict[str, Any] | None = None
    recommended_policy: dict[str, Any] | None = None
    for policy, metrics in policy_metrics.items():
        row = {
            "policy": policy,
            **metrics,
            "safe_candidate": (
                safe_float(metrics.get("selected_seconds")) > 0
                and safe_float(metrics.get("remote_risk_seconds")) <= 3.0
                and (
                    metrics.get("precision_proxy") is None
                    or safe_float(metrics.get("precision_proxy")) >= 0.90
                )
            ),
            "material_candidate": safe_float(metrics.get("local_seconds")) >= MATERIAL_RESCUE_LOCAL_SECONDS,
        }
        if (
            safe_float(row.get("selected_seconds")) > 0
            and (best_policy is None or safe_float(row.get("local_seconds")) > safe_float(best_policy.get("local_seconds")))
        ):
            best_policy = row
        if (
            row["safe_candidate"]
            and row["material_candidate"]
            and (
                recommended_policy is None
                or safe_float(row.get("local_seconds")) > safe_float(recommended_policy.get("local_seconds"))
            )
        ):
            recommended_policy = row
    target_me_possible_local = round(
        sum(safe_float(row.get("target_me_possible_or_confirmed_local_seconds")) for row in summaries),
        3,
    )
    status = "missing"
    recommended_next = "run_live_local_recall_target_me_audit"
    if summaries:
        status = "has_shadow_evidence"
        recommended_next = "design_target_me_gated_shadow_rescue_policy"
    if recommended_policy:
        status = "safe_candidate_available"
    elif summaries and target_me_possible_local < MATERIAL_RESCUE_LOCAL_SECONDS:
        status = "no_material_target_me_candidate"
        recommended_next = "keep_collecting_target_me_evidence"
    if by_status.get("insufficient_enrollment", 0) and target_me_possible_local >= MATERIAL_RESCUE_LOCAL_SECONDS:
        recommended_next = "design_target_me_gated_shadow_rescue_policy_then_add_enrollment_fallback"

    return {
        "schema": "murmurmark.live_local_recall_target_me_diagnostics/v1",
        "scope": scope,
        "status": status,
        "sessions_with_audit": len(summaries),
        "by_status": dict(sorted(by_status.items())),
        "items": sum(safe_int(row.get("items")) for row in summaries),
        "total_seconds": round(sum(safe_float(row.get("total_seconds")) for row in summaries), 3),
        "local_seconds": round(sum(safe_float(row.get("local_seconds")) for row in summaries), 3),
        "target_me_confirmed_local_seconds": round(
            sum(safe_float(row.get("target_me_confirmed_local_seconds")) for row in summaries),
            3,
        ),
        "target_me_possible_or_confirmed_local_seconds": target_me_possible_local,
        "remote_risk_seconds": round(sum(safe_float(row.get("remote_risk_seconds")) for row in summaries), 3),
        "target_me_rejected_remote_risk_seconds": round(
            sum(safe_float(row.get("target_me_rejected_remote_risk_seconds")) for row in summaries),
            3,
        ),
        "target_me_rescue_policy_metrics": policy_metrics,
        "recommended_policy": recommended_policy,
        "best_policy": best_policy,
        "recommended_next": recommended_next,
        "promotion_decision": "shadow_only_do_not_promote",
    }


def summarize_session(session: Path, root: Path) -> dict[str, Any]:
    live_report_path = session / "derived/live/live_pipeline_report.json"
    comparison_path = session / "derived/live/live_batch_comparison.json"
    session_report_path = session / "derived/live/live_parity_session_report.json"
    final_reconcile_path = session / "derived/live/final_reconcile_report.json"
    target_me_summary_path = (
        session / "derived/audit/live-local-recall-target-me/live_local_recall_target_me_summary.json"
    )
    live_report = read_json(live_report_path)
    comparison = read_json(comparison_path)
    final_reconcile = read_json(final_reconcile_path)
    target_me_summary = read_json(target_me_summary_path)
    live_present = live_report is not None
    blockers: list[str] = []
    if not live_present:
        blockers.append("live_report_missing")
    if live_present and comparison is None:
        blockers.append("live_batch_comparison_missing")
    metrics = comparison.get("metrics") if isinstance(comparison, dict) else {}
    risk_examples = comparison.get("risk_examples") if isinstance(comparison, dict) else {}
    risk_examples = risk_examples if isinstance(risk_examples, dict) else {}
    parity = comparison.get("parity_gates") if isinstance(comparison, dict) else {}
    comparison_status = comparison.get("status") if isinstance(comparison, dict) else None
    comparison_gates = gate_rows(comparison)
    capture_gate = next((gate for gate in comparison_gates if gate.get("name") == "capture_safety"), None)
    if live_present and capture_gate is None:
        capture_gate = build_capture_safety_gate(session)
        gates = [capture_gate] + comparison_gates
    else:
        gates = comparison_gates
    return {
        "session": rel(session, root),
        "evidence_scope": evidence_scope(session, root),
        "live_present": live_present,
        "live_status": live_report.get("status") if isinstance(live_report, dict) else None,
        "comparison_status": comparison_status,
        "parity_status": parity.get("status") if isinstance(parity, dict) else None,
        "meaningful_compared": meaningful_comparison(metrics if isinstance(metrics, dict) else {}, comparison_status),
        "all_parity_gates_passed": bool(gates) and all(gate.get("status") == "passed" for gate in gates),
        "promotion_allowed": bool(comparison.get("promotion_allowed")) if isinstance(comparison, dict) else False,
        "promotion_blockers": comparison.get("promotion_blockers") if isinstance(comparison, dict) else blockers,
        "blockers": blockers + list(comparison.get("blockers") or []) if isinstance(comparison, dict) else blockers,
        "warnings": comparison.get("warnings") if isinstance(comparison, dict) else [],
        "metrics": {
            "live_chunks": metrics.get("live_chunks") if isinstance(metrics, dict) else None,
            "live_token_recall_in_batch": metrics.get("live_token_recall_in_batch") if isinstance(metrics, dict) else None,
            "adjacent_duplicate_chunk_count": metrics.get("adjacent_duplicate_chunk_count") if isinstance(metrics, dict) else None,
            "live_boundary_gate_issue_count": (
                metrics.get("live_boundary_gate_issue_count") if isinstance(metrics, dict) else None
            ),
            "live_boundary_gate_suppressed_count": (
                metrics.get("live_boundary_gate_suppressed_count") if isinstance(metrics, dict) else None
            ),
            "live_boundary_gate_resolved_suppressed_count": (
                metrics.get("live_boundary_gate_resolved_suppressed_count") if isinstance(metrics, dict) else None
            ),
            "live_boundary_gate_unresolved_suppressed_count": (
                metrics.get("live_boundary_gate_unresolved_suppressed_count") if isinstance(metrics, dict) else None
            ),
            "live_order_mismatch_count": metrics.get("live_order_mismatch_count") if isinstance(metrics, dict) else None,
            "live_order_mismatch_by_category": (
                metrics.get("live_order_mismatch_by_category") if isinstance(metrics.get("live_order_mismatch_by_category"), dict) else {}
            )
            if isinstance(metrics, dict)
            else {},
            "live_order_mismatch_by_primary_risk": (
                metrics.get("live_order_mismatch_by_primary_risk")
                if isinstance(metrics.get("live_order_mismatch_by_primary_risk"), dict)
                else {}
            )
            if isinstance(metrics, dict)
            else {},
            "live_order_mismatch_by_confidence": (
                metrics.get("live_order_mismatch_by_confidence")
                if isinstance(metrics.get("live_order_mismatch_by_confidence"), dict)
                else {}
            )
            if isinstance(metrics, dict)
            else {},
            "live_batch_interval_overlap_order_ambiguity_count": (
                metrics.get("live_batch_interval_overlap_order_ambiguity_count")
                if isinstance(metrics, dict)
                else None
            ),
            "live_role_constrained_order_mismatch_count": (
                metrics.get("live_role_constrained_order_mismatch_count") if isinstance(metrics, dict) else None
            ),
            "live_role_constrained_order_mismatch_by_category": (
                metrics.get("live_role_constrained_order_mismatch_by_category")
                if isinstance(metrics.get("live_role_constrained_order_mismatch_by_category"), dict)
                else {}
            )
            if isinstance(metrics, dict)
            else {},
            "live_role_constrained_order_mismatch_by_confidence": (
                metrics.get("live_role_constrained_order_mismatch_by_confidence")
                if isinstance(metrics.get("live_role_constrained_order_mismatch_by_confidence"), dict)
                else {}
            )
            if isinstance(metrics, dict)
            else {},
            "live_role_constrained_batch_interval_overlap_order_ambiguity_count": (
                metrics.get("live_role_constrained_batch_interval_overlap_order_ambiguity_count")
                if isinstance(metrics, dict)
                else None
            ),
            "live_contentful_role_constrained_order_mismatch_count": (
                metrics.get("live_contentful_role_constrained_order_mismatch_count")
                if isinstance(metrics, dict)
                else None
            ),
            "live_contentful_role_constrained_order_mismatch_by_category": (
                metrics.get("live_contentful_role_constrained_order_mismatch_by_category")
                if isinstance(metrics.get("live_contentful_role_constrained_order_mismatch_by_category"), dict)
                else {}
            )
            if isinstance(metrics, dict)
            else {},
            "live_contentful_role_constrained_order_mismatch_by_confidence": (
                metrics.get("live_contentful_role_constrained_order_mismatch_by_confidence")
                if isinstance(metrics.get("live_contentful_role_constrained_order_mismatch_by_confidence"), dict)
                else {}
            )
            if isinstance(metrics, dict)
            else {},
            "live_contentful_role_constrained_order_mismatch_by_ambiguity": (
                metrics.get("live_contentful_role_constrained_order_mismatch_by_ambiguity")
                if isinstance(metrics.get("live_contentful_role_constrained_order_mismatch_by_ambiguity"), dict)
                else {}
            )
            if isinstance(metrics, dict)
            else {},
            "live_contentful_role_constrained_batch_interval_overlap_order_ambiguity_count": (
                metrics.get("live_contentful_role_constrained_batch_interval_overlap_order_ambiguity_count")
                if isinstance(metrics, dict)
                else None
            ),
            "live_unambiguous_contentful_role_constrained_order_mismatch_count": (
                metrics.get("live_unambiguous_contentful_role_constrained_order_mismatch_count")
                if isinstance(metrics, dict)
                else None
            ),
            "live_missing_me_seconds": metrics.get("live_missing_me_seconds") if isinstance(metrics, dict) else None,
            "live_suspicious_batch_me_missing_seconds": (
                metrics.get("live_suspicious_batch_me_missing_seconds") if isinstance(metrics, dict) else None
            ),
            "live_missing_me_visible_in_suppressed_mic_seconds": (
                metrics.get("live_missing_me_visible_in_suppressed_mic_seconds") if isinstance(metrics, dict) else None
            ),
            "live_missing_me_not_visible_in_suppressed_mic_seconds": (
                metrics.get("live_missing_me_not_visible_in_suppressed_mic_seconds") if isinstance(metrics, dict) else None
            ),
            "live_suppressed_mic_turn_count": (
                metrics.get("live_suppressed_mic_turn_count") if isinstance(metrics, dict) else None
            ),
            "live_segment_role_gate_candidate_chunk_count": (
                metrics.get("live_segment_role_gate_candidate_chunk_count") if isinstance(metrics, dict) else None
            ),
            "live_segment_role_gate_candidate_kept_segment_count": (
                metrics.get("live_segment_role_gate_candidate_kept_segment_count") if isinstance(metrics, dict) else None
            ),
            "live_segment_role_gate_candidate_suppressed_segment_count": (
                metrics.get("live_segment_role_gate_candidate_suppressed_segment_count") if isinstance(metrics, dict) else None
            ),
            "live_rescue_shadow_candidate_chunk_count": (
                metrics.get("live_rescue_shadow_candidate_chunk_count") if isinstance(metrics, dict) else None
            ),
            "live_rescue_shadow_candidate_segment_count": (
                metrics.get("live_rescue_shadow_candidate_segment_count") if isinstance(metrics, dict) else None
            ),
            "live_rescue_shadow_candidate_token_count": (
                metrics.get("live_rescue_shadow_candidate_token_count") if isinstance(metrics, dict) else None
            ),
            "live_rescue_shadow_order_mismatch_count": (
                metrics.get("live_rescue_shadow_order_mismatch_count") if isinstance(metrics, dict) else None
            ),
            "live_rescue_shadow_order_mismatch_by_category": (
                metrics.get("live_rescue_shadow_order_mismatch_by_category")
                if isinstance(metrics.get("live_rescue_shadow_order_mismatch_by_category"), dict)
                else {}
            )
            if isinstance(metrics, dict)
            else {},
            "live_rescue_shadow_order_mismatch_by_primary_risk": (
                metrics.get("live_rescue_shadow_order_mismatch_by_primary_risk")
                if isinstance(metrics.get("live_rescue_shadow_order_mismatch_by_primary_risk"), dict)
                else {}
            )
            if isinstance(metrics, dict)
            else {},
            "live_rescue_shadow_order_mismatch_by_confidence": (
                metrics.get("live_rescue_shadow_order_mismatch_by_confidence")
                if isinstance(metrics.get("live_rescue_shadow_order_mismatch_by_confidence"), dict)
                else {}
            )
            if isinstance(metrics, dict)
            else {},
            "live_rescue_shadow_role_constrained_order_mismatch_count": (
                metrics.get("live_rescue_shadow_role_constrained_order_mismatch_count")
                if isinstance(metrics, dict)
                else None
            ),
            "live_rescue_shadow_role_constrained_order_mismatch_by_category": (
                metrics.get("live_rescue_shadow_role_constrained_order_mismatch_by_category")
                if isinstance(metrics.get("live_rescue_shadow_role_constrained_order_mismatch_by_category"), dict)
                else {}
            )
            if isinstance(metrics, dict)
            else {},
            "live_rescue_shadow_role_constrained_order_mismatch_by_confidence": (
                metrics.get("live_rescue_shadow_role_constrained_order_mismatch_by_confidence")
                if isinstance(metrics.get("live_rescue_shadow_role_constrained_order_mismatch_by_confidence"), dict)
                else {}
            )
            if isinstance(metrics, dict)
            else {},
            "live_rescue_shadow_contentful_role_constrained_order_mismatch_count": (
                metrics.get("live_rescue_shadow_contentful_role_constrained_order_mismatch_count")
                if isinstance(metrics, dict)
                else None
            ),
            "live_rescue_shadow_contentful_role_constrained_order_mismatch_by_category": (
                metrics.get("live_rescue_shadow_contentful_role_constrained_order_mismatch_by_category")
                if isinstance(metrics.get("live_rescue_shadow_contentful_role_constrained_order_mismatch_by_category"), dict)
                else {}
            )
            if isinstance(metrics, dict)
            else {},
            "live_rescue_shadow_contentful_role_constrained_order_mismatch_by_confidence": (
                metrics.get("live_rescue_shadow_contentful_role_constrained_order_mismatch_by_confidence")
                if isinstance(metrics.get("live_rescue_shadow_contentful_role_constrained_order_mismatch_by_confidence"), dict)
                else {}
            )
            if isinstance(metrics, dict)
            else {},
            "live_rescue_shadow_contentful_role_constrained_order_mismatch_by_ambiguity": (
                metrics.get("live_rescue_shadow_contentful_role_constrained_order_mismatch_by_ambiguity")
                if isinstance(metrics.get("live_rescue_shadow_contentful_role_constrained_order_mismatch_by_ambiguity"), dict)
                else {}
            )
            if isinstance(metrics, dict)
            else {},
            "live_rescue_shadow_unambiguous_contentful_role_constrained_order_mismatch_count": (
                metrics.get("live_rescue_shadow_unambiguous_contentful_role_constrained_order_mismatch_count")
                if isinstance(metrics, dict)
                else None
            ),
            "live_rescue_shadow_suspected_remote_leak_in_me_seconds": (
                metrics.get("live_rescue_shadow_suspected_remote_leak_in_me_seconds") if isinstance(metrics, dict) else None
            ),
            "live_rescue_shadow_missing_me_seconds_after": (
                metrics.get("live_rescue_shadow_missing_me_seconds_after") if isinstance(metrics, dict) else None
            ),
            "live_rescue_shadow_missing_me_recovered_seconds": (
                metrics.get("live_rescue_shadow_missing_me_recovered_seconds") if isinstance(metrics, dict) else None
            ),
            "live_suppressed_mic_asr_segment_count": (
                metrics.get("live_suppressed_mic_asr_segment_count") if isinstance(metrics, dict) else None
            ),
            "live_suppressed_mic_asr_segment_seconds": (
                metrics.get("live_suppressed_mic_asr_segment_seconds") if isinstance(metrics, dict) else None
            ),
            "live_suppressed_mic_asr_me_dominant_segment_count": (
                metrics.get("live_suppressed_mic_asr_me_dominant_segment_count") if isinstance(metrics, dict) else None
            ),
            "live_suppressed_mic_asr_me_dominant_segment_seconds": (
                metrics.get("live_suppressed_mic_asr_me_dominant_segment_seconds") if isinstance(metrics, dict) else None
            ),
            "live_suppressed_mic_asr_mixed_segment_count": (
                metrics.get("live_suppressed_mic_asr_mixed_segment_count") if isinstance(metrics, dict) else None
            ),
            "live_suppressed_mic_asr_mixed_segment_seconds": (
                metrics.get("live_suppressed_mic_asr_mixed_segment_seconds") if isinstance(metrics, dict) else None
            ),
            **suppressed_mic_rescue_policy_metric_values(metrics if isinstance(metrics, dict) else None),
            **target_me_shadow_policy_metric_values(metrics if isinstance(metrics, dict) else None),
            **target_me_shadow_profile_metric_values(metrics if isinstance(metrics, dict) else None),
            "live_suspected_remote_leak_in_me_seconds": (
                metrics.get("live_suspected_remote_leak_in_me_seconds") if isinstance(metrics, dict) else None
            ),
            "live_turn_count": metrics.get("live_turn_count") if isinstance(metrics, dict) else None,
            "live_me_turn_count": metrics.get("live_me_turn_count") if isinstance(metrics, dict) else None,
            "live_remote_turn_count": metrics.get("live_remote_turn_count") if isinstance(metrics, dict) else None,
            "batch_utterance_count": metrics.get("batch_utterance_count") if isinstance(metrics, dict) else None,
            "batch_me_utterance_count": metrics.get("batch_me_utterance_count") if isinstance(metrics, dict) else None,
            "batch_remote_utterance_count": metrics.get("batch_remote_utterance_count") if isinstance(metrics, dict) else None,
            "batch_ready_for_notes": metrics.get("batch_ready_for_notes") if isinstance(metrics, dict) else None,
            "capture_safety_status": capture_gate.get("status") if isinstance(capture_gate, dict) else None,
            "screen_capture_restart_count": (
                (capture_gate.get("evidence") or {}).get("screen_capture_restart_count")
                if isinstance(capture_gate, dict)
                else None
            ),
            "capture_safety_warning_count": (
                (capture_gate.get("evidence") or {}).get("safety_warning_count")
                if isinstance(capture_gate, dict)
                else None
            ),
        },
        "final_reconcile": {
            "status": final_reconcile.get("status") if isinstance(final_reconcile, dict) else None,
            "speedup_status": final_reconcile.get("speedup_status") if isinstance(final_reconcile, dict) else None,
            "live_cache_reuse": final_reconcile.get("live_cache_reuse") if isinstance(final_reconcile, dict) else None,
        },
        "live_local_recall_target_me": target_me_summary if isinstance(target_me_summary, dict) else None,
        "inputs": {
            "live_report": rel(live_report_path, session) if live_report_path.exists() else None,
            "live_batch_comparison": rel(comparison_path, session) if comparison_path.exists() else None,
            "live_parity_session_report": rel(session_report_path, session) if session_report_path.exists() else None,
            "final_reconcile_report": rel(final_reconcile_path, session) if final_reconcile_path.exists() else None,
            "live_local_recall_target_me_summary": (
                rel(target_me_summary_path, session) if target_me_summary_path.exists() else None
            ),
        },
        "risk_examples": {
            "local_missing": risk_examples.get("local_missing") if isinstance(risk_examples.get("local_missing"), list) else [],
            "local_missing_suspicious_batch_me": (
                risk_examples.get("local_missing_suspicious_batch_me")
                if isinstance(risk_examples.get("local_missing_suspicious_batch_me"), list)
                else []
            ),
            "local_missing_visible_in_suppressed_mic": (
                risk_examples.get("local_missing_visible_in_suppressed_mic")
                if isinstance(risk_examples.get("local_missing_visible_in_suppressed_mic"), list)
                else []
            ),
            "local_missing_not_visible_in_suppressed_mic": (
                risk_examples.get("local_missing_not_visible_in_suppressed_mic")
                if isinstance(risk_examples.get("local_missing_not_visible_in_suppressed_mic"), list)
                else []
            ),
            "remote_leak": risk_examples.get("remote_leak") if isinstance(risk_examples.get("remote_leak"), list) else [],
            "live_rescue_shadow_remote_leak": (
                risk_examples.get("live_rescue_shadow_remote_leak")
                if isinstance(risk_examples.get("live_rescue_shadow_remote_leak"), list)
                else []
            ),
            "live_rescue_shadow_order_mismatches": (
                risk_examples.get("live_rescue_shadow_order_mismatches")
                if isinstance(risk_examples.get("live_rescue_shadow_order_mismatches"), list)
                else []
            ),
            "suppressed_mic_asr_segments": (
                risk_examples.get("suppressed_mic_asr_segments")
                if isinstance(risk_examples.get("suppressed_mic_asr_segments"), list)
                else []
            ),
            "segment_role_gate_candidates": (
                risk_examples.get("segment_role_gate_candidates")
                if isinstance(risk_examples.get("segment_role_gate_candidates"), list)
                else []
            ),
            "live_rescue_shadow": (
                risk_examples.get("live_rescue_shadow")
                if isinstance(risk_examples.get("live_rescue_shadow"), list)
                else []
            ),
            "suppressed_mic_rescue_policies": (
                risk_examples.get("suppressed_mic_rescue_policies")
                if isinstance(risk_examples.get("suppressed_mic_rescue_policies"), dict)
                else {}
            ),
            "live_target_me_shadow": (
                risk_examples.get("live_target_me_shadow")
                if isinstance(risk_examples.get("live_target_me_shadow"), dict)
                else {}
            ),
        },
        "gates": gates,
        "non_passing_gates": non_passing_gate_rows(gates),
        "parity_dimensions": dimension_statuses(gates),
    }


def build_report(sessions: list[Path], root: Path, args: argparse.Namespace) -> dict[str, Any]:
    refresh_results = refresh_live_comparisons(sessions) if args.refresh else []
    refresh_attempted = refresh_results
    refresh_failed = [row for row in refresh_results if row.get("status") == "failed"]
    refresh_skipped = max(0, len(sessions) - len(refresh_attempted)) if args.refresh else 0
    refresh_status = "not_requested"
    if args.refresh:
        refresh_status = "failed" if refresh_failed else "passed"
    rows = [summarize_session(session, root) for session in sessions]
    live_rows = [row for row in rows if row["live_present"]]
    real_live_rows = [row for row in live_rows if row.get("evidence_scope") == "real_meeting"]
    diagnostic_live_rows = [row for row in live_rows if row.get("evidence_scope") != "real_meeting"]
    gate_counts: dict[str, Counter[str]] = defaultdict(Counter)
    real_gate_counts: dict[str, Counter[str]] = defaultdict(Counter)
    blockers = Counter()
    warnings = Counter()
    for row in rows:
        for blocker in row.get("blockers") or []:
            blockers[str(blocker)] += 1
        for warning in row.get("warnings") or []:
            warnings[str(warning)] += 1
        for gate in row.get("gates") or []:
            gate_counts[str(gate.get("name") or "unknown")][str(gate.get("status") or "unknown")] += 1
    for row in real_live_rows:
        for gate in row.get("gates") or []:
            real_gate_counts[str(gate.get("name") or "unknown")][str(gate.get("status") or "unknown")] += 1
    dimension_counts, dimension_issue_sessions = summarize_dimensions(live_rows)
    real_dimension_counts, real_dimension_issue_sessions = summarize_dimensions(real_live_rows)
    capture_safe_candidate_rows = [row for row in real_live_rows if capture_safe_candidate_row(row)]
    capture_safe_evaluable_rows = [row for row in real_live_rows if capture_safe_evaluable_row(row)]
    candidate_dimension_counts, candidate_dimension_issue_sessions = summarize_dimensions(capture_safe_candidate_rows)
    candidate_blocking_dimensions = blocking_dimensions_from_counts(candidate_dimension_counts)
    promotable = [row for row in rows if row.get("promotion_allowed")]
    not_promotable = [row for row in live_rows if row.get("parity_status") != "passed_but_shadow_locked"]
    summary = {
        "sessions_total": len(rows),
        "live_sessions": len(live_rows),
        "real_live_sessions": len(real_live_rows),
        "diagnostic_live_sessions": len(diagnostic_live_rows),
        "live_comparison_refresh_status": refresh_status,
        "live_comparison_refresh_attempted_sessions": len(refresh_attempted),
        "live_comparison_refresh_failed_sessions": len(refresh_failed),
        "live_comparison_refresh_skipped_sessions": refresh_skipped,
        "compared_sessions": sum(1 for row in rows if row.get("comparison_status") == "shadow_compared"),
        "real_compared_sessions": sum(1 for row in real_live_rows if row.get("comparison_status") == "shadow_compared"),
        "meaningful_compared_sessions": sum(1 for row in rows if row.get("meaningful_compared")),
        "real_meaningful_compared_sessions": sum(1 for row in real_live_rows if row.get("meaningful_compared")),
        "passing_compared_sessions": sum(1 for row in rows if row.get("all_parity_gates_passed")),
        "real_passing_compared_sessions": sum(1 for row in real_live_rows if row.get("all_parity_gates_passed")),
        "real_capture_safe_candidate_sessions": len(capture_safe_candidate_rows),
        "real_capture_safe_candidate_passing_sessions": sum(
            1 for row in capture_safe_candidate_rows if row.get("all_parity_gates_passed")
        ),
        "real_capture_safe_evaluable_sessions": len(capture_safe_evaluable_rows),
        "real_capture_safe_candidate_blocking_dimensions": candidate_blocking_dimensions,
        "blocked_sessions": sum(1 for row in rows if row.get("comparison_status") == "blocked"),
        "promotion_allowed_sessions": len(promotable),
        "promotion_decision": "shadow_only_do_not_promote",
        "speedup_supported_sessions": sum(
            1 for row in rows if (row.get("final_reconcile") or {}).get("speedup_status") == "live_asr_cache_reused"
        ),
        "live_order_mismatch_count": sum_int_metric(rows, "live_order_mismatch_count"),
        "real_live_order_mismatch_count": sum_int_metric(real_live_rows, "live_order_mismatch_count"),
        "live_order_mismatch_by_category": sum_counter_metric(rows, "live_order_mismatch_by_category"),
        "real_live_order_mismatch_by_category": sum_counter_metric(real_live_rows, "live_order_mismatch_by_category"),
        "live_order_mismatch_by_primary_risk": sum_counter_metric(rows, "live_order_mismatch_by_primary_risk"),
        "real_live_order_mismatch_by_primary_risk": sum_counter_metric(
            real_live_rows,
            "live_order_mismatch_by_primary_risk",
        ),
        "live_order_mismatch_by_confidence": sum_counter_metric(rows, "live_order_mismatch_by_confidence"),
        "real_live_order_mismatch_by_confidence": sum_counter_metric(real_live_rows, "live_order_mismatch_by_confidence"),
        "live_batch_interval_overlap_order_ambiguity_count": sum_int_metric(
            rows,
            "live_batch_interval_overlap_order_ambiguity_count",
        ),
        "real_live_batch_interval_overlap_order_ambiguity_count": sum_int_metric(
            real_live_rows,
            "live_batch_interval_overlap_order_ambiguity_count",
        ),
        "live_role_constrained_order_mismatch_count": sum_int_metric(
            rows,
            "live_role_constrained_order_mismatch_count",
        ),
        "real_live_role_constrained_order_mismatch_count": sum_int_metric(
            real_live_rows,
            "live_role_constrained_order_mismatch_count",
        ),
        "live_role_constrained_order_mismatch_by_category": sum_counter_metric(
            rows,
            "live_role_constrained_order_mismatch_by_category",
        ),
        "real_live_role_constrained_order_mismatch_by_category": sum_counter_metric(
            real_live_rows,
            "live_role_constrained_order_mismatch_by_category",
        ),
        "live_role_constrained_order_mismatch_by_confidence": sum_counter_metric(
            rows,
            "live_role_constrained_order_mismatch_by_confidence",
        ),
        "real_live_role_constrained_order_mismatch_by_confidence": sum_counter_metric(
            real_live_rows,
            "live_role_constrained_order_mismatch_by_confidence",
        ),
        "live_role_constrained_batch_interval_overlap_order_ambiguity_count": sum_int_metric(
            rows,
            "live_role_constrained_batch_interval_overlap_order_ambiguity_count",
        ),
        "real_live_role_constrained_batch_interval_overlap_order_ambiguity_count": sum_int_metric(
            real_live_rows,
            "live_role_constrained_batch_interval_overlap_order_ambiguity_count",
        ),
        "live_contentful_role_constrained_order_mismatch_count": sum_int_metric(
            rows,
            "live_contentful_role_constrained_order_mismatch_count",
        ),
        "real_live_contentful_role_constrained_order_mismatch_count": sum_int_metric(
            real_live_rows,
            "live_contentful_role_constrained_order_mismatch_count",
        ),
        "live_contentful_role_constrained_order_mismatch_by_category": sum_counter_metric(
            rows,
            "live_contentful_role_constrained_order_mismatch_by_category",
        ),
        "real_live_contentful_role_constrained_order_mismatch_by_category": sum_counter_metric(
            real_live_rows,
            "live_contentful_role_constrained_order_mismatch_by_category",
        ),
        "live_contentful_role_constrained_order_mismatch_by_confidence": sum_counter_metric(
            rows,
            "live_contentful_role_constrained_order_mismatch_by_confidence",
        ),
        "real_live_contentful_role_constrained_order_mismatch_by_confidence": sum_counter_metric(
            real_live_rows,
            "live_contentful_role_constrained_order_mismatch_by_confidence",
        ),
        "live_contentful_role_constrained_order_mismatch_by_ambiguity": sum_counter_metric(
            rows,
            "live_contentful_role_constrained_order_mismatch_by_ambiguity",
        ),
        "real_live_contentful_role_constrained_order_mismatch_by_ambiguity": sum_counter_metric(
            real_live_rows,
            "live_contentful_role_constrained_order_mismatch_by_ambiguity",
        ),
        "live_contentful_role_constrained_batch_interval_overlap_order_ambiguity_count": sum_int_metric(
            rows,
            "live_contentful_role_constrained_batch_interval_overlap_order_ambiguity_count",
        ),
        "real_live_contentful_role_constrained_batch_interval_overlap_order_ambiguity_count": sum_int_metric(
            real_live_rows,
            "live_contentful_role_constrained_batch_interval_overlap_order_ambiguity_count",
        ),
        "live_unambiguous_contentful_role_constrained_order_mismatch_count": sum_int_metric(
            rows,
            "live_unambiguous_contentful_role_constrained_order_mismatch_count",
        ),
        "real_live_unambiguous_contentful_role_constrained_order_mismatch_count": sum_int_metric(
            real_live_rows,
            "live_unambiguous_contentful_role_constrained_order_mismatch_count",
        ),
        "live_missing_me_seconds": sum_metric(rows, "live_missing_me_seconds"),
        "real_live_missing_me_seconds": sum_metric(real_live_rows, "live_missing_me_seconds"),
        "live_suspicious_batch_me_missing_seconds": sum_metric(rows, "live_suspicious_batch_me_missing_seconds"),
        "real_live_suspicious_batch_me_missing_seconds": sum_metric(
            real_live_rows,
            "live_suspicious_batch_me_missing_seconds",
        ),
        "live_missing_me_visible_in_suppressed_mic_seconds": sum_metric(
            rows,
            "live_missing_me_visible_in_suppressed_mic_seconds",
        ),
        "real_live_missing_me_visible_in_suppressed_mic_seconds": sum_metric(
            real_live_rows,
            "live_missing_me_visible_in_suppressed_mic_seconds",
        ),
        "live_missing_me_not_visible_in_suppressed_mic_seconds": sum_metric(
            rows,
            "live_missing_me_not_visible_in_suppressed_mic_seconds",
        ),
        "real_live_missing_me_not_visible_in_suppressed_mic_seconds": sum_metric(
            real_live_rows,
            "live_missing_me_not_visible_in_suppressed_mic_seconds",
        ),
        "live_suppressed_mic_turn_count": sum_int_metric(rows, "live_suppressed_mic_turn_count"),
        "real_live_suppressed_mic_turn_count": sum_int_metric(real_live_rows, "live_suppressed_mic_turn_count"),
        "live_segment_role_gate_candidate_chunk_count": sum_int_metric(
            rows,
            "live_segment_role_gate_candidate_chunk_count",
        ),
        "real_live_segment_role_gate_candidate_chunk_count": sum_int_metric(
            real_live_rows,
            "live_segment_role_gate_candidate_chunk_count",
        ),
        "live_segment_role_gate_candidate_kept_segment_count": sum_int_metric(
            rows,
            "live_segment_role_gate_candidate_kept_segment_count",
        ),
        "real_live_segment_role_gate_candidate_kept_segment_count": sum_int_metric(
            real_live_rows,
            "live_segment_role_gate_candidate_kept_segment_count",
        ),
        "live_rescue_shadow_candidate_chunk_count": sum_int_metric(rows, "live_rescue_shadow_candidate_chunk_count"),
        "real_live_rescue_shadow_candidate_chunk_count": sum_int_metric(
            real_live_rows,
            "live_rescue_shadow_candidate_chunk_count",
        ),
        "live_rescue_shadow_candidate_segment_count": sum_int_metric(rows, "live_rescue_shadow_candidate_segment_count"),
        "real_live_rescue_shadow_candidate_segment_count": sum_int_metric(
            real_live_rows,
            "live_rescue_shadow_candidate_segment_count",
        ),
        "live_rescue_shadow_order_mismatch_count": sum_int_metric(rows, "live_rescue_shadow_order_mismatch_count"),
        "real_live_rescue_shadow_order_mismatch_count": sum_int_metric(
            real_live_rows,
            "live_rescue_shadow_order_mismatch_count",
        ),
        "live_rescue_shadow_order_mismatch_by_category": sum_counter_metric(
            rows,
            "live_rescue_shadow_order_mismatch_by_category",
        ),
        "real_live_rescue_shadow_order_mismatch_by_category": sum_counter_metric(
            real_live_rows,
            "live_rescue_shadow_order_mismatch_by_category",
        ),
        "live_rescue_shadow_order_mismatch_by_primary_risk": sum_counter_metric(
            rows,
            "live_rescue_shadow_order_mismatch_by_primary_risk",
        ),
        "real_live_rescue_shadow_order_mismatch_by_primary_risk": sum_counter_metric(
            real_live_rows,
            "live_rescue_shadow_order_mismatch_by_primary_risk",
        ),
        "live_rescue_shadow_order_mismatch_by_confidence": sum_counter_metric(
            rows,
            "live_rescue_shadow_order_mismatch_by_confidence",
        ),
        "real_live_rescue_shadow_order_mismatch_by_confidence": sum_counter_metric(
            real_live_rows,
            "live_rescue_shadow_order_mismatch_by_confidence",
        ),
        "live_rescue_shadow_role_constrained_order_mismatch_count": sum_int_metric(
            rows,
            "live_rescue_shadow_role_constrained_order_mismatch_count",
        ),
        "real_live_rescue_shadow_role_constrained_order_mismatch_count": sum_int_metric(
            real_live_rows,
            "live_rescue_shadow_role_constrained_order_mismatch_count",
        ),
        "live_rescue_shadow_role_constrained_order_mismatch_by_category": sum_counter_metric(
            rows,
            "live_rescue_shadow_role_constrained_order_mismatch_by_category",
        ),
        "real_live_rescue_shadow_role_constrained_order_mismatch_by_category": sum_counter_metric(
            real_live_rows,
            "live_rescue_shadow_role_constrained_order_mismatch_by_category",
        ),
        "live_rescue_shadow_role_constrained_order_mismatch_by_confidence": sum_counter_metric(
            rows,
            "live_rescue_shadow_role_constrained_order_mismatch_by_confidence",
        ),
        "real_live_rescue_shadow_role_constrained_order_mismatch_by_confidence": sum_counter_metric(
            real_live_rows,
            "live_rescue_shadow_role_constrained_order_mismatch_by_confidence",
        ),
        "live_rescue_shadow_contentful_role_constrained_order_mismatch_count": sum_int_metric(
            rows,
            "live_rescue_shadow_contentful_role_constrained_order_mismatch_count",
        ),
        "real_live_rescue_shadow_contentful_role_constrained_order_mismatch_count": sum_int_metric(
            real_live_rows,
            "live_rescue_shadow_contentful_role_constrained_order_mismatch_count",
        ),
        "live_rescue_shadow_contentful_role_constrained_order_mismatch_by_category": sum_counter_metric(
            rows,
            "live_rescue_shadow_contentful_role_constrained_order_mismatch_by_category",
        ),
        "real_live_rescue_shadow_contentful_role_constrained_order_mismatch_by_category": sum_counter_metric(
            real_live_rows,
            "live_rescue_shadow_contentful_role_constrained_order_mismatch_by_category",
        ),
        "live_rescue_shadow_contentful_role_constrained_order_mismatch_by_confidence": sum_counter_metric(
            rows,
            "live_rescue_shadow_contentful_role_constrained_order_mismatch_by_confidence",
        ),
        "real_live_rescue_shadow_contentful_role_constrained_order_mismatch_by_confidence": sum_counter_metric(
            real_live_rows,
            "live_rescue_shadow_contentful_role_constrained_order_mismatch_by_confidence",
        ),
        "live_rescue_shadow_contentful_role_constrained_order_mismatch_by_ambiguity": sum_counter_metric(
            rows,
            "live_rescue_shadow_contentful_role_constrained_order_mismatch_by_ambiguity",
        ),
        "real_live_rescue_shadow_contentful_role_constrained_order_mismatch_by_ambiguity": sum_counter_metric(
            real_live_rows,
            "live_rescue_shadow_contentful_role_constrained_order_mismatch_by_ambiguity",
        ),
        "live_rescue_shadow_unambiguous_contentful_role_constrained_order_mismatch_count": sum_int_metric(
            rows,
            "live_rescue_shadow_unambiguous_contentful_role_constrained_order_mismatch_count",
        ),
        "real_live_rescue_shadow_unambiguous_contentful_role_constrained_order_mismatch_count": sum_int_metric(
            real_live_rows,
            "live_rescue_shadow_unambiguous_contentful_role_constrained_order_mismatch_count",
        ),
        "live_rescue_shadow_suspected_remote_leak_in_me_seconds": sum_metric(
            rows,
            "live_rescue_shadow_suspected_remote_leak_in_me_seconds",
        ),
        "real_live_rescue_shadow_suspected_remote_leak_in_me_seconds": sum_metric(
            real_live_rows,
            "live_rescue_shadow_suspected_remote_leak_in_me_seconds",
        ),
        "live_rescue_shadow_missing_me_seconds_after": sum_metric(rows, "live_rescue_shadow_missing_me_seconds_after"),
        "real_live_rescue_shadow_missing_me_seconds_after": sum_metric(
            real_live_rows,
            "live_rescue_shadow_missing_me_seconds_after",
        ),
        "live_rescue_shadow_missing_me_recovered_seconds": sum_metric(
            rows,
            "live_rescue_shadow_missing_me_recovered_seconds",
        ),
        "real_live_rescue_shadow_missing_me_recovered_seconds": sum_metric(
            real_live_rows,
            "live_rescue_shadow_missing_me_recovered_seconds",
        ),
        "live_suppressed_mic_asr_segment_count": sum_int_metric(rows, "live_suppressed_mic_asr_segment_count"),
        "real_live_suppressed_mic_asr_segment_count": sum_int_metric(
            real_live_rows,
            "live_suppressed_mic_asr_segment_count",
        ),
        "live_suppressed_mic_asr_segment_seconds": sum_metric(rows, "live_suppressed_mic_asr_segment_seconds"),
        "real_live_suppressed_mic_asr_segment_seconds": sum_metric(
            real_live_rows,
            "live_suppressed_mic_asr_segment_seconds",
        ),
        "live_suppressed_mic_asr_me_dominant_segment_count": sum_int_metric(
            rows,
            "live_suppressed_mic_asr_me_dominant_segment_count",
        ),
        "real_live_suppressed_mic_asr_me_dominant_segment_count": sum_int_metric(
            real_live_rows,
            "live_suppressed_mic_asr_me_dominant_segment_count",
        ),
        "live_suppressed_mic_asr_me_dominant_segment_seconds": sum_metric(
            rows,
            "live_suppressed_mic_asr_me_dominant_segment_seconds",
        ),
        "real_live_suppressed_mic_asr_me_dominant_segment_seconds": sum_metric(
            real_live_rows,
            "live_suppressed_mic_asr_me_dominant_segment_seconds",
        ),
        "live_suppressed_mic_asr_mixed_segment_count": sum_int_metric(
            rows,
            "live_suppressed_mic_asr_mixed_segment_count",
        ),
        "real_live_suppressed_mic_asr_mixed_segment_count": sum_int_metric(
            real_live_rows,
            "live_suppressed_mic_asr_mixed_segment_count",
        ),
        "live_suppressed_mic_asr_mixed_segment_seconds": sum_metric(
            rows,
            "live_suppressed_mic_asr_mixed_segment_seconds",
        ),
        "real_live_suppressed_mic_asr_mixed_segment_seconds": sum_metric(
            real_live_rows,
            "live_suppressed_mic_asr_mixed_segment_seconds",
        ),
        "live_suspected_remote_leak_in_me_seconds": sum_metric(rows, "live_suspected_remote_leak_in_me_seconds"),
        "real_live_suspected_remote_leak_in_me_seconds": sum_metric(
            real_live_rows,
            "live_suspected_remote_leak_in_me_seconds",
        ),
        "adjacent_duplicate_chunk_count": sum_int_metric(rows, "adjacent_duplicate_chunk_count"),
        "real_adjacent_duplicate_chunk_count": sum_int_metric(real_live_rows, "adjacent_duplicate_chunk_count"),
        "live_boundary_gate_issue_count": sum_int_metric(rows, "live_boundary_gate_issue_count"),
        "real_live_boundary_gate_issue_count": sum_int_metric(real_live_rows, "live_boundary_gate_issue_count"),
        "live_boundary_gate_suppressed_count": sum_int_metric(rows, "live_boundary_gate_suppressed_count"),
        "real_live_boundary_gate_suppressed_count": sum_int_metric(
            real_live_rows,
            "live_boundary_gate_suppressed_count",
        ),
        "live_boundary_gate_resolved_suppressed_count": sum_int_metric(
            rows,
            "live_boundary_gate_resolved_suppressed_count",
        ),
        "real_live_boundary_gate_resolved_suppressed_count": sum_int_metric(
            real_live_rows,
            "live_boundary_gate_resolved_suppressed_count",
        ),
        "live_boundary_gate_unresolved_suppressed_count": sum_int_metric(
            rows,
            "live_boundary_gate_unresolved_suppressed_count",
        ),
        "real_live_boundary_gate_unresolved_suppressed_count": sum_int_metric(
            real_live_rows,
            "live_boundary_gate_unresolved_suppressed_count",
        ),
    }
    add_suppressed_mic_rescue_policy_summary(summary, rows, "")
    add_suppressed_mic_rescue_policy_summary(summary, real_live_rows, "real")
    add_suppressed_mic_rescue_policy_summary(summary, capture_safe_candidate_rows, "capture_safe_candidate")
    add_suppressed_mic_rescue_policy_summary(summary, capture_safe_evaluable_rows, "capture_safe_evaluable")
    add_target_me_shadow_policy_summary(summary, rows, "")
    add_target_me_shadow_policy_summary(summary, real_live_rows, "real")
    add_target_me_shadow_policy_summary(summary, capture_safe_candidate_rows, "capture_safe_candidate")
    add_target_me_shadow_policy_summary(summary, capture_safe_evaluable_rows, "capture_safe_evaluable")
    add_target_me_shadow_profile_summary(summary, rows, "")
    add_target_me_shadow_profile_summary(summary, real_live_rows, "real")
    add_target_me_shadow_profile_summary(summary, capture_safe_candidate_rows, "capture_safe_candidate")
    add_target_me_shadow_profile_summary(summary, capture_safe_evaluable_rows, "capture_safe_evaluable")
    local_recall_rescue_policy_diagnostics = {
        "real": rescue_policy_diagnostics(summary, "real"),
        "capture_safe_candidate": rescue_policy_diagnostics(summary, "capture_safe_candidate"),
        "capture_safe_evaluable": rescue_policy_diagnostics(summary, "capture_safe_evaluable"),
    }
    target_me_shadow_diagnostics_report = {
        "real": target_me_shadow_policy_diagnostics(summary, "real"),
        "capture_safe_candidate": target_me_shadow_policy_diagnostics(summary, "capture_safe_candidate"),
        "capture_safe_evaluable": target_me_shadow_policy_diagnostics(summary, "capture_safe_evaluable"),
    }
    target_me_shadow_profile_diagnostics_report = {
        "real": target_me_shadow_profile_diagnostics(summary, "real"),
        "capture_safe_candidate": target_me_shadow_profile_diagnostics(summary, "capture_safe_candidate"),
        "capture_safe_evaluable": target_me_shadow_profile_diagnostics(summary, "capture_safe_evaluable"),
    }
    real_best_live_profile = (
        (target_me_shadow_profile_diagnostics_report.get("real") or {}).get("best_live_implementable_profile")
        if isinstance(target_me_shadow_profile_diagnostics_report.get("real"), dict)
        else None
    )
    real_best_live_profile_policy = (
        real_best_live_profile.get("policy") if isinstance(real_best_live_profile, dict) else None
    )
    best_live_profile_remaining_gap = target_me_shadow_profile_remaining_gap_examples(
        real_live_rows,
        root=root,
        policy=real_best_live_profile_policy,
    )
    live_order_risk_triage_report = live_order_risk_triage(
        real_live_rows,
        root=root,
        policy=real_best_live_profile_policy,
    )
    speaker_boundary_lab_report = live_speaker_boundary_evidence_lab(best_live_profile_remaining_gap)
    local_island_split_lab_report = local_island_split_lab(best_live_profile_remaining_gap)
    online_speaker_boundary_design_lab_report = live_online_speaker_boundary_evidence_design_lab(
        best_live_profile_remaining_gap,
        local_island_split_lab_report,
    )
    mixed_speaker_boundary_voice_coverage_lab_report = live_mixed_speaker_boundary_voice_coverage_lab(
        best_live_profile_remaining_gap,
        online_speaker_boundary_design_lab_report,
        root=root,
    )
    tight_voice_remote_guard_lab_report = live_tight_voice_remote_guard_lab(
        mixed_speaker_boundary_voice_coverage_lab_report
    )
    local_island_audio_anchor_lab_report = live_local_island_audio_anchor_lab(local_island_split_lab_report)
    local_island_retime_anchor_lab_report = live_local_island_retime_anchor_lab(local_island_split_lab_report)
    live_only_local_island_candidate_lab_report = live_only_local_island_candidate_lab(real_live_rows)
    live_only_retime_boundary_lab_report = live_only_retime_boundary_candidate_lab(
        real_live_rows,
        remaining_gap=best_live_profile_remaining_gap,
    )
    strict_local_island_shadow_delta_lab_report = live_strict_local_island_shadow_delta_lab(
        real_live_rows,
        root=root,
        base_policy=real_best_live_profile_policy or "",
        combined_policy=STRICT_LIVE_ONLY_LOCAL_ISLAND_AUDIO_SAFE_UNION_PROFILE_POLICY,
    )
    local_recall_rescue_lab_report = local_recall_rescue_lab(real_live_rows)
    candidate_local_recall_rescue_lab_report = local_recall_rescue_lab(capture_safe_candidate_rows)
    evaluable_local_recall_rescue_lab_report = local_recall_rescue_lab(capture_safe_evaluable_rows)
    target_me_diagnostics_report = {
        "real": live_local_recall_target_me_diagnostics(real_live_rows, "real"),
        "capture_safe_candidate": live_local_recall_target_me_diagnostics(
            capture_safe_candidate_rows,
            "capture_safe_candidate",
        ),
        "capture_safe_evaluable": live_local_recall_target_me_diagnostics(
            capture_safe_evaluable_rows,
            "capture_safe_evaluable",
        ),
    }
    for scope, diagnostics in local_recall_rescue_policy_diagnostics.items():
        best_policy = diagnostics.get("best_policy")
        recommended_policy = diagnostics.get("recommended_policy")
        summary[f"{scope}_rescue_policy_status"] = diagnostics.get("status")
        summary[f"{scope}_best_live_rescue_policy"] = (
            best_policy.get("policy") if isinstance(best_policy, dict) else None
        )
        summary[f"{scope}_best_live_rescue_policy_local_seconds"] = (
            safe_float(best_policy.get("local_seconds")) if isinstance(best_policy, dict) else None
        )
        summary[f"{scope}_best_live_rescue_policy_remote_risk_seconds"] = (
            safe_float(best_policy.get("remote_risk_seconds")) if isinstance(best_policy, dict) else None
        )
        summary[f"{scope}_recommended_rescue_policy"] = (
            recommended_policy.get("policy") if isinstance(recommended_policy, dict) else None
        )
    for scope, lab in (
        ("real", local_recall_rescue_lab_report),
        ("capture_safe_candidate", candidate_local_recall_rescue_lab_report),
        ("capture_safe_evaluable", evaluable_local_recall_rescue_lab_report),
    ):
        summary[f"{scope}_live_rescue_lab_speaker_evidence_needed_seconds"] = safe_float(
            lab.get("speaker_evidence_needed_seconds")
        )
        summary[f"{scope}_live_rescue_lab_live_policy_remote_risk_seconds"] = safe_float(
            lab.get("live_policy_remote_risk_seconds")
        )
        summary[f"{scope}_live_rescue_lab_recommended_next"] = lab.get("recommended_next")
    for scope, diagnostics in target_me_diagnostics_report.items():
        recommended = diagnostics.get("recommended_policy")
        best = diagnostics.get("best_policy")
        summary[f"{scope}_live_target_me_status"] = diagnostics.get("status")
        summary[f"{scope}_live_target_me_sessions_with_audit"] = safe_int(diagnostics.get("sessions_with_audit"))
        summary[f"{scope}_live_target_me_possible_or_confirmed_local_seconds"] = safe_float(
            diagnostics.get("target_me_possible_or_confirmed_local_seconds")
        )
        summary[f"{scope}_live_target_me_remote_risk_seconds"] = safe_float(diagnostics.get("remote_risk_seconds"))
        summary[f"{scope}_live_target_me_recommended_policy"] = (
            recommended.get("policy") if isinstance(recommended, dict) else None
        )
        summary[f"{scope}_live_target_me_recommended_policy_local_seconds"] = (
            safe_float(recommended.get("local_seconds")) if isinstance(recommended, dict) else None
        )
        summary[f"{scope}_live_target_me_recommended_policy_remote_risk_seconds"] = (
            safe_float(recommended.get("remote_risk_seconds")) if isinstance(recommended, dict) else None
        )
        summary[f"{scope}_live_target_me_recommended_policy_missing_me_recovered_seconds"] = (
            safe_float(recommended.get("missing_me_recovered_seconds")) if isinstance(recommended, dict) else None
        )
        summary[f"{scope}_live_target_me_best_policy"] = best.get("policy") if isinstance(best, dict) else None
        summary[f"{scope}_live_target_me_recommended_next"] = diagnostics.get("recommended_next")
    for scope, diagnostics in target_me_shadow_diagnostics_report.items():
        recommended = diagnostics.get("recommended_policy")
        summary[f"{scope}_live_target_me_shadow_status"] = diagnostics.get("status")
        summary[f"{scope}_live_target_me_shadow_recommended_policy"] = (
            recommended.get("policy") if isinstance(recommended, dict) else None
        )
        summary[f"{scope}_live_target_me_shadow_recommended_policy_missing_me_recovered_seconds"] = (
            safe_float(recommended.get("missing_me_recovered_seconds")) if isinstance(recommended, dict) else None
        )
        summary[f"{scope}_live_target_me_shadow_recommended_policy_remote_risk_seconds"] = (
            safe_float(recommended.get("suspected_remote_leak_in_me_seconds")) if isinstance(recommended, dict) else None
        )
        summary[f"{scope}_live_target_me_shadow_recommended_policy_order_mismatch_count"] = (
            safe_int(recommended.get("contentful_role_constrained_order_mismatch_count"))
            if isinstance(recommended, dict)
            else None
        )
        summary[f"{scope}_live_target_me_shadow_recommended_policy_order_mismatch_delta_count"] = (
            safe_int(recommended.get("contentful_role_constrained_order_mismatch_delta_count"))
            if isinstance(recommended, dict)
            else None
        )
        summary[f"{scope}_live_target_me_shadow_recommended_next"] = diagnostics.get("recommended_next")
    for scope, diagnostics in target_me_shadow_profile_diagnostics_report.items():
        best = diagnostics.get("best_profile")
        best_live = diagnostics.get("best_live_implementable_profile")
        summary[f"{scope}_live_target_me_shadow_profile_status"] = diagnostics.get("status")
        summary[f"{scope}_live_target_me_shadow_profile_best_policy"] = (
            best.get("policy") if isinstance(best, dict) else None
        )
        summary[f"{scope}_live_target_me_shadow_profile_best_live_implementable_policy"] = (
            best_live.get("policy") if isinstance(best_live, dict) else None
        )
        summary[f"{scope}_live_target_me_shadow_profile_best_live_implementable_non_passing_gate_count"] = (
            safe_int(best_live.get("non_passing_gate_count")) if isinstance(best_live, dict) else None
        )
        summary[f"{scope}_live_target_me_shadow_profile_best_live_implementable_missing_me_seconds"] = (
            safe_float(best_live.get("live_missing_me_seconds")) if isinstance(best_live, dict) else None
        )
        summary[f"{scope}_live_target_me_shadow_profile_best_live_implementable_remote_leak_seconds"] = (
            safe_float(best_live.get("live_suspected_remote_leak_in_me_seconds")) if isinstance(best_live, dict) else None
        )
        summary[f"{scope}_live_target_me_shadow_profile_best_live_implementable_contentful_order_mismatch_count"] = (
            safe_int(best_live.get("live_contentful_role_constrained_order_mismatch_count"))
            if isinstance(best_live, dict)
            else None
        )
        summary[f"{scope}_live_target_me_shadow_profile_best_live_implementable_visible_with_target_me_seconds"] = (
            safe_float(best_live.get("live_missing_me_visible_with_target_me_candidate_seconds"))
            if isinstance(best_live, dict)
            else None
        )
        summary[f"{scope}_live_target_me_shadow_profile_best_live_implementable_visible_without_target_me_seconds"] = (
            safe_float(best_live.get("live_missing_me_visible_without_target_me_candidate_seconds"))
            if isinstance(best_live, dict)
            else None
        )
        summary[f"{scope}_live_target_me_shadow_profile_best_live_implementable_not_visible_with_target_me_seconds"] = (
            safe_float(best_live.get("live_missing_me_not_visible_with_target_me_candidate_seconds"))
            if isinstance(best_live, dict)
            else None
        )
        summary[f"{scope}_live_target_me_shadow_profile_best_live_implementable_not_visible_without_target_me_seconds"] = (
            safe_float(best_live.get("live_missing_me_not_visible_without_target_me_candidate_seconds"))
            if isinstance(best_live, dict)
            else None
        )
        summary[f"{scope}_live_target_me_shadow_profile_all_parity_gates_passed_sessions"] = (
            safe_int(best.get("all_parity_gates_passed_session_count")) if isinstance(best, dict) else None
        )
        summary[f"{scope}_live_target_me_shadow_profile_non_passing_gate_count"] = (
            safe_int(best.get("non_passing_gate_count")) if isinstance(best, dict) else None
        )
        summary[f"{scope}_live_target_me_shadow_profile_missing_me_seconds"] = (
            safe_float(best.get("live_missing_me_seconds")) if isinstance(best, dict) else None
        )
        summary[f"{scope}_live_target_me_shadow_profile_missing_me_visible_in_suppressed_mic_seconds"] = (
            safe_float(best.get("live_missing_me_visible_in_suppressed_mic_seconds")) if isinstance(best, dict) else None
        )
        summary[f"{scope}_live_target_me_shadow_profile_missing_me_not_visible_in_suppressed_mic_seconds"] = (
            safe_float(best.get("live_missing_me_not_visible_in_suppressed_mic_seconds")) if isinstance(best, dict) else None
        )
        summary[f"{scope}_live_target_me_shadow_profile_missing_me_with_target_me_candidate_seconds"] = (
            safe_float(best.get("live_missing_me_with_target_me_candidate_seconds")) if isinstance(best, dict) else None
        )
        summary[f"{scope}_live_target_me_shadow_profile_missing_me_without_target_me_candidate_seconds"] = (
            safe_float(best.get("live_missing_me_without_target_me_candidate_seconds")) if isinstance(best, dict) else None
        )
        summary[f"{scope}_live_target_me_shadow_profile_remote_leak_seconds"] = (
            safe_float(best.get("live_suspected_remote_leak_in_me_seconds")) if isinstance(best, dict) else None
        )
        summary[f"{scope}_live_target_me_shadow_profile_order_mismatch_count"] = (
            safe_int(best.get("live_order_mismatch_count")) if isinstance(best, dict) else None
        )
        summary[f"{scope}_live_target_me_shadow_profile_contentful_order_mismatch_count"] = (
            safe_int(best.get("live_contentful_role_constrained_order_mismatch_count")) if isinstance(best, dict) else None
        )
        summary[f"{scope}_live_target_me_shadow_profile_removed_live_turn_count"] = (
            safe_int(best.get("removed_live_turn_count")) if isinstance(best, dict) else None
        )
        summary[f"{scope}_live_target_me_shadow_profile_removed_live_turn_seconds"] = (
            safe_float(best.get("removed_live_turn_seconds")) if isinstance(best, dict) else None
        )
        summary[f"{scope}_live_target_me_shadow_profile_visible_suppressed_mic_added_turn_seconds"] = (
            safe_float(best.get("visible_suppressed_mic_added_turn_seconds")) if isinstance(best, dict) else None
        )
        summary[f"{scope}_live_target_me_shadow_profile_visible_suppressed_mic_rejected_turn_count"] = (
            safe_int(best.get("visible_suppressed_mic_rejected_turn_count")) if isinstance(best, dict) else None
        )
        summary[f"{scope}_live_target_me_shadow_profile_recommended_next"] = diagnostics.get("recommended_next")
    coverage_target = {
        "target_live_sessions": args.target_live_sessions,
        "target_meaningful_compared_sessions": args.target_meaningful_compared_sessions,
        "target_passing_compared_sessions": args.target_passing_compared_sessions,
        "live_sessions_remaining": max(0, args.target_live_sessions - summary["real_live_sessions"]),
        "meaningful_compared_sessions_remaining": max(
            0,
            args.target_meaningful_compared_sessions - summary["real_meaningful_compared_sessions"],
        ),
        "passing_compared_sessions_remaining": max(
            0,
            args.target_passing_compared_sessions - summary["real_passing_compared_sessions"],
        ),
    }
    coverage_target["status"] = (
        "passed"
        if coverage_target["live_sessions_remaining"] == 0
        and coverage_target["meaningful_compared_sessions_remaining"] == 0
        and coverage_target["passing_compared_sessions_remaining"] == 0
        else "needs_more_live_coverage"
    )
    summary["coverage_target_status"] = coverage_target["status"]
    summary["coverage_target_live_sessions_remaining"] = coverage_target["live_sessions_remaining"]
    summary["coverage_target_passing_sessions_remaining"] = coverage_target["passing_compared_sessions_remaining"]
    if not live_rows:
        target_status = "no_live_sessions"
    elif promotable:
        target_status = "unexpected_promotable_sessions"
    elif not_promotable:
        target_status = "shadow_only_not_promotable"
    elif coverage_target["status"] != "passed":
        target_status = "shadow_locked_needs_more_live_coverage"
    else:
        target_status = "shadow_locked_after_basic_gates"
    summary["target_status"] = target_status
    promotion_blocking_dimensions = blocking_dimensions_from_counts(real_dimension_counts)
    if live_rows and not real_live_rows:
        promotion_blocking_dimensions = list(PARITY_DIMENSIONS.keys())
    summary["promotion_blocking_dimensions"] = promotion_blocking_dimensions
    summary["promotion_blocking_dimension_count"] = len(promotion_blocking_dimensions)
    capture_regression_check_path = root / "_reports/capture-regression/capture_regression_check.json"
    capture_regression_check = read_json(capture_regression_check_path)
    pilot_allowed = controlled_real_live_pilot_allowed(capture_regression_check)
    pilot_reason = (
        "full fail-open proof passed; controlled Live Evidence runs may collect evidence"
        if pilot_allowed
        else "full fail-open proof is missing; run MURMURMARK_RUN_LIVE_CAPTURE_TEST=1 scripts/check-capture-regressions.sh"
    )
    summary["live_quarantined"] = True
    summary["live_quarantine_reason"] = LIVE_QUARANTINE_REASON
    summary["live_evidence_mode"] = "historical_debug_only"
    summary["new_real_live_collection_allowed"] = False
    summary["controlled_real_live_pilot_allowed"] = pilot_allowed
    summary["controlled_real_live_pilot_reason"] = pilot_reason
    strict_failures: list[dict[str, Any]] = []
    def add_failure(gate_id: str, message: str, value: Any, limit: Any) -> None:
        strict_failures.append({"id": gate_id, "message": message, "value": value, "limit": limit})

    if args.min_live_sessions and summary["real_live_sessions"] < args.min_live_sessions:
        add_failure("min_live_sessions", "not enough real live sessions", summary["real_live_sessions"], args.min_live_sessions)
    if args.min_compared_sessions and summary["real_compared_sessions"] < args.min_compared_sessions:
        add_failure(
            "min_compared_sessions",
            "not enough live-vs-batch compared sessions",
            summary["real_compared_sessions"],
            args.min_compared_sessions,
        )
    if (
        args.min_meaningful_compared_sessions
        and summary["real_meaningful_compared_sessions"] < args.min_meaningful_compared_sessions
    ):
        add_failure(
            "min_meaningful_compared_sessions",
            "not enough compared sessions with both Me and remote evidence",
            summary["real_meaningful_compared_sessions"],
            args.min_meaningful_compared_sessions,
        )
    if args.min_passing_compared_sessions and summary["real_passing_compared_sessions"] < args.min_passing_compared_sessions:
        add_failure(
            "min_passing_compared_sessions",
            "not enough compared sessions where every live parity gate passed",
            summary["real_passing_compared_sessions"],
            args.min_passing_compared_sessions,
        )
    if args.max_order_mismatches is not None and summary["real_live_order_mismatch_count"] > args.max_order_mismatches:
        add_failure(
            "max_order_mismatches",
            "real live order mismatches exceed limit",
            summary["real_live_order_mismatch_count"],
            args.max_order_mismatches,
        )
    if args.max_missing_me_sec is not None and summary["real_live_missing_me_seconds"] > args.max_missing_me_sec:
        add_failure(
            "max_missing_me_sec",
            "real live missing Me seconds exceed limit",
            summary["real_live_missing_me_seconds"],
            args.max_missing_me_sec,
        )
    if (
        args.max_remote_in_me_sec is not None
        and summary["real_live_suspected_remote_leak_in_me_seconds"] > args.max_remote_in_me_sec
    ):
        add_failure(
            "max_remote_in_me_sec",
            "real live suspected remote-in-Me seconds exceed limit",
            summary["real_live_suspected_remote_leak_in_me_seconds"],
            args.max_remote_in_me_sec,
        )
    if (
        args.max_boundary_duplicates is not None
        and summary["real_adjacent_duplicate_chunk_count"] > args.max_boundary_duplicates
    ):
        add_failure(
            "max_boundary_duplicates",
            "real adjacent live chunk duplicates exceed limit",
            summary["real_adjacent_duplicate_chunk_count"],
            args.max_boundary_duplicates,
        )
    if args.fail_on_promotion and summary["promotion_allowed_sessions"] > 0:
        add_failure("no_promotion", "live promotion must remain blocked in v1", summary["promotion_allowed_sessions"], 0)
    if args.require_passing_gates:
        non_passing: dict[str, dict[str, int]] = {}
        if not real_live_rows:
            add_failure("require_passing_gates", "no real live sessions available for parity gates", 0, "> 0")
        for name, counts in real_gate_counts.items():
            bad = {status: count for status, count in counts.items() if status != "passed" and count > 0}
            if bad:
                non_passing[name] = bad
        if real_live_rows and not real_gate_counts:
            non_passing["required_artifacts"] = {"missing": len(real_live_rows)}
        if non_passing:
            add_failure("require_passing_gates", "one or more live parity gates did not pass", non_passing, "all passed")
    strict_requested = any(
        [
            args.min_live_sessions,
            args.min_compared_sessions,
            args.min_meaningful_compared_sessions,
            args.min_passing_compared_sessions,
            args.max_order_mismatches is not None,
            args.max_missing_me_sec is not None,
            args.max_remote_in_me_sec is not None,
            args.max_boundary_duplicates is not None,
            args.require_passing_gates,
            args.fail_on_promotion,
        ]
    )
    summary["strict_coverage_status"] = "not_requested" if not strict_requested else ("failed" if strict_failures else "passed")
    gate_issues = build_gate_issues(rows)
    real_blocker_triage_summary, real_blocker_triage = build_real_blocker_triage(real_live_rows)
    real_blocker_triage_summary["controlled_real_live_pilot_allowed"] = pilot_allowed
    next_commands = recommended_next_commands(summary, real_gate_counts, gate_issues)
    parity_dimensions_payload = build_dimensions_payload(dimension_counts, dimension_issue_sessions)
    real_parity_dimensions_payload = build_dimensions_payload(real_dimension_counts, real_dimension_issue_sessions)
    candidate_parity_dimensions_payload = build_dimensions_payload(
        candidate_dimension_counts,
        candidate_dimension_issue_sessions,
    )
    promotion_policy = {
        "status": "blocked",
        "decision": summary["promotion_decision"],
        "batch_authoritative": True,
        "live_quarantined": True,
        "evidence_mode": summary["live_evidence_mode"],
        "evidence_scope": "real_meeting",
        "diagnostic_live_sessions": len(diagnostic_live_rows),
        "new_real_live_collection_allowed": False,
        "controlled_real_live_pilot_allowed": pilot_allowed,
        "controlled_real_live_pilot_reason": pilot_reason,
        "quarantine_reason": LIVE_QUARANTINE_REASON,
        "required_dimensions": list(PARITY_DIMENSIONS.keys()),
        "blocking_dimensions": promotion_blocking_dimensions,
        "promotion_allowed_sessions": summary["promotion_allowed_sessions"],
    }
    candidate_scope = {
        "definition": (
            "real_meeting sessions with shadow_compared, meaningful comparison, capture_safety passed "
            "and required_artifacts passed"
        ),
        "sessions": len(capture_safe_candidate_rows),
        "passing_sessions": summary["real_capture_safe_candidate_passing_sessions"],
        "blocking_dimensions": candidate_blocking_dimensions,
        "session_ids": [str(row.get("session") or "") for row in capture_safe_candidate_rows],
        "next_focus": metric_aware_next_focus(candidate_blocking_dimensions, summary),
        "promotion_decision": "shadow_only_do_not_promote",
        "new_real_live_collection_allowed": False,
        "controlled_real_live_pilot_allowed": pilot_allowed,
    }
    candidate_session_ids = {str(row.get("session") or "") for row in capture_safe_candidate_rows}
    historical_non_candidate_session_ids = [
        str(row.get("session") or "")
        for row in real_live_rows
        if str(row.get("session") or "") not in candidate_session_ids
    ]
    local_recall_examples = local_recall_gap_examples(real_live_rows)
    candidate_local_recall_examples = local_recall_gap_examples(capture_safe_candidate_rows, limit=30)
    evaluable_local_recall_examples = local_recall_gap_examples(capture_safe_evaluable_rows, limit=50)
    summary["live_local_recall_gap_example_count"] = safe_int(local_recall_examples.get("item_count"))
    summary["live_local_recall_gap_example_seconds"] = safe_float(local_recall_examples.get("seconds"))
    summary["capture_safe_candidate_local_recall_gap_example_count"] = safe_int(
        candidate_local_recall_examples.get("item_count")
    )
    summary["capture_safe_candidate_local_recall_gap_example_seconds"] = safe_float(
        candidate_local_recall_examples.get("seconds")
    )
    summary["capture_safe_evaluable_local_recall_gap_example_count"] = safe_int(
        evaluable_local_recall_examples.get("item_count")
    )
    summary["capture_safe_evaluable_local_recall_gap_example_seconds"] = safe_float(
        evaluable_local_recall_examples.get("seconds")
    )
    summary["real_live_target_me_shadow_profile_best_live_implementable_remaining_gap_count"] = safe_int(
        best_live_profile_remaining_gap.get("item_count")
    )
    summary["real_live_target_me_shadow_profile_best_live_implementable_remaining_gap_seconds"] = safe_float(
        best_live_profile_remaining_gap.get("seconds")
    )
    summary["real_live_order_risk_triage_item_count"] = safe_int(live_order_risk_triage_report.get("item_count"))
    summary["real_live_order_risk_triage_blocking_count"] = safe_int(
        live_order_risk_triage_report.get("blocking_count")
    )
    summary["real_live_order_risk_triage_advisory_count"] = safe_int(
        live_order_risk_triage_report.get("advisory_count")
    )
    summary["real_live_order_risk_triage_boundary_retime_candidate_count"] = safe_int(
        live_order_risk_triage_report.get("boundary_retime_candidate_count")
    )
    summary["real_live_order_risk_triage_likely_false_positive_count"] = safe_int(
        live_order_risk_triage_report.get("likely_false_positive_count")
    )
    summary["real_live_local_island_split_lab_candidate_count"] = safe_int(
        local_island_split_lab_report.get("candidate_count")
    )
    summary["real_live_local_island_split_lab_candidate_batch_seconds"] = safe_float(
        local_island_split_lab_report.get("candidate_batch_seconds")
    )
    summary["real_live_local_island_split_lab_candidate_local_island_seconds"] = safe_float(
        local_island_split_lab_report.get("candidate_local_island_seconds")
    )
    summary["real_live_local_island_split_lab_accepted_count"] = safe_int(
        local_island_split_lab_report.get("accepted_count")
    )
    summary["real_live_local_island_split_lab_accepted_batch_seconds"] = safe_float(
        local_island_split_lab_report.get("accepted_batch_seconds")
    )
    summary["real_live_local_island_split_lab_accepted_local_island_seconds"] = safe_float(
        local_island_split_lab_report.get("accepted_local_island_seconds")
    )
    summary["real_live_local_island_audio_anchor_lab_status"] = local_island_audio_anchor_lab_report.get("status")
    summary["real_live_local_island_audio_anchor_lab_anchor_seconds"] = safe_float(
        local_island_audio_anchor_lab_report.get("audio_anchor_seconds")
    )
    summary["real_live_local_island_audio_anchor_lab_anchor_island_count"] = safe_int(
        local_island_audio_anchor_lab_report.get("audio_anchor_island_count")
    )
    summary["real_live_local_island_audio_anchor_lab_anchor_row_count"] = safe_int(
        local_island_audio_anchor_lab_report.get("audio_anchor_row_count")
    )
    summary["real_live_live_only_local_island_candidate_lab_status"] = live_only_local_island_candidate_lab_report.get(
        "status"
    )
    summary["real_live_live_only_local_island_candidate_lab_candidate_seconds"] = safe_float(
        live_only_local_island_candidate_lab_report.get("candidate_seconds")
    )
    summary["real_live_live_only_local_island_candidate_lab_local_seconds"] = safe_float(
        live_only_local_island_candidate_lab_report.get("local_seconds")
    )
    summary["real_live_live_only_local_island_candidate_lab_remote_risk_seconds"] = safe_float(
        live_only_local_island_candidate_lab_report.get("remote_risk_seconds")
    )
    summary["real_live_live_only_local_island_candidate_lab_precision_proxy"] = live_only_local_island_candidate_lab_report.get(
        "precision_proxy"
    )
    live_only_strict_zero_remote_risk_profile = (
        live_only_local_island_candidate_lab_report.get("stricter_profiles", {}).get(
            "strict_zero_remote_risk_text_audio_v1"
        )
        if isinstance(live_only_local_island_candidate_lab_report.get("stricter_profiles"), dict)
        else {}
    )
    summary["real_live_live_only_local_island_strict_zero_remote_risk_candidate_seconds"] = safe_float(
        live_only_strict_zero_remote_risk_profile.get("candidate_seconds")
    )
    summary["real_live_live_only_local_island_strict_zero_remote_risk_local_seconds"] = safe_float(
        live_only_strict_zero_remote_risk_profile.get("local_seconds")
    )
    summary["real_live_live_only_local_island_strict_zero_remote_risk_remote_risk_seconds"] = safe_float(
        live_only_strict_zero_remote_risk_profile.get("remote_risk_seconds")
    )
    summary["real_live_live_only_local_island_strict_zero_remote_risk_precision_proxy"] = (
        live_only_strict_zero_remote_risk_profile.get("precision_proxy")
    )
    live_only_retime_boundary_profiles = (
        live_only_retime_boundary_lab_report.get("profiles")
        if isinstance(live_only_retime_boundary_lab_report.get("profiles"), dict)
        else {}
    )
    live_only_retime_boundary_recommended_profile = str(
        live_only_retime_boundary_lab_report.get("recommended_profile") or ""
    )
    live_only_retime_boundary_recommended = (
        live_only_retime_boundary_profiles.get(live_only_retime_boundary_recommended_profile)
        if live_only_retime_boundary_recommended_profile
        else {}
    )
    if not isinstance(live_only_retime_boundary_recommended, dict):
        live_only_retime_boundary_recommended = {}
    live_only_retime_boundary_best_missing_profile = str(
        live_only_retime_boundary_lab_report.get("best_missing_overlap_profile") or ""
    )
    live_only_retime_boundary_best_missing = (
        live_only_retime_boundary_profiles.get(live_only_retime_boundary_best_missing_profile)
        if live_only_retime_boundary_best_missing_profile
        else {}
    )
    if not isinstance(live_only_retime_boundary_best_missing, dict):
        live_only_retime_boundary_best_missing = {}
    live_only_retime_boundary_best_zero_remote_evaluated_profile = str(
        live_only_retime_boundary_lab_report.get("best_zero_remote_evaluated_profile") or ""
    )
    live_only_retime_boundary_best_zero_remote_evaluated = (
        live_only_retime_boundary_profiles.get(live_only_retime_boundary_best_zero_remote_evaluated_profile)
        if live_only_retime_boundary_best_zero_remote_evaluated_profile
        else {}
    )
    if not isinstance(live_only_retime_boundary_best_zero_remote_evaluated, dict):
        live_only_retime_boundary_best_zero_remote_evaluated = {}
    summary["real_live_live_only_retime_boundary_lab_status"] = live_only_retime_boundary_lab_report.get("status")
    summary["real_live_live_only_retime_boundary_anchor_group_count"] = safe_int(
        live_only_retime_boundary_lab_report.get("anchor_group_count")
    )
    summary["real_live_live_only_retime_boundary_anchor_seconds"] = safe_float(
        live_only_retime_boundary_lab_report.get("anchor_seconds")
    )
    summary["real_live_live_only_retime_boundary_recommended_profile"] = (
        live_only_retime_boundary_recommended_profile or None
    )
    summary["real_live_live_only_retime_boundary_recommended_missing_overlap_seconds"] = safe_float(
        live_only_retime_boundary_recommended.get("local_missing_overlap_seconds")
    )
    summary["real_live_live_only_retime_boundary_recommended_remote_risk_seconds"] = safe_float(
        live_only_retime_boundary_recommended.get("suppressed_remote_risk_seconds")
    )
    summary["real_live_live_only_retime_boundary_recommended_candidate_seconds"] = safe_float(
        live_only_retime_boundary_recommended.get("candidate_seconds")
    )
    summary["real_live_live_only_retime_boundary_best_missing_profile"] = (
        live_only_retime_boundary_best_missing_profile or None
    )
    summary["real_live_live_only_retime_boundary_best_missing_overlap_seconds"] = safe_float(
        live_only_retime_boundary_best_missing.get("local_missing_overlap_seconds")
    )
    summary["real_live_live_only_retime_boundary_best_missing_remote_risk_seconds"] = safe_float(
        live_only_retime_boundary_best_missing.get("suppressed_remote_risk_seconds")
    )
    summary["real_live_live_only_retime_boundary_best_missing_candidate_seconds"] = safe_float(
        live_only_retime_boundary_best_missing.get("candidate_seconds")
    )
    summary["real_live_live_only_retime_boundary_best_zero_remote_evaluated_profile"] = (
        live_only_retime_boundary_best_zero_remote_evaluated_profile or None
    )
    summary["real_live_live_only_retime_boundary_best_zero_remote_evaluated_missing_overlap_seconds"] = safe_float(
        live_only_retime_boundary_best_zero_remote_evaluated.get("local_missing_overlap_seconds")
    )
    summary["real_live_live_only_retime_boundary_best_zero_remote_evaluated_remote_risk_seconds"] = safe_float(
        live_only_retime_boundary_best_zero_remote_evaluated.get("suppressed_remote_risk_seconds")
    )
    summary["real_live_live_only_retime_boundary_best_zero_remote_evaluated_candidate_seconds"] = safe_float(
        live_only_retime_boundary_best_zero_remote_evaluated.get("candidate_seconds")
    )
    strict_live_only_profile_summary_prefix = (
        f"real_live_target_me_shadow_profile_{STRICT_LIVE_ONLY_LOCAL_ISLAND_PROFILE_POLICY}"
    )
    combined_strict_audio_safe_union_profile_summary_prefix = (
        f"real_live_target_me_shadow_profile_{STRICT_LIVE_ONLY_LOCAL_ISLAND_AUDIO_SAFE_UNION_PROFILE_POLICY}"
    )
    remote_forbidden_boundary_classifier_profile_summary_prefix = (
        f"real_live_target_me_shadow_profile_{REMOTE_FORBIDDEN_BOUNDARY_CLASSIFIER_PROFILE_POLICY}"
    )
    real_local_island_oracle_profile = target_me_shadow_profile_row(
        target_me_shadow_profile_diagnostics_report.get("real")
        if isinstance(target_me_shadow_profile_diagnostics_report.get("real"), dict)
        else {},
        LOCAL_ISLAND_SPLIT_ORACLE_PROFILE_POLICY,
    )
    summary["real_live_local_island_split_oracle_profile_policy"] = LOCAL_ISLAND_SPLIT_ORACLE_PROFILE_POLICY
    if real_local_island_oracle_profile:
        oracle_missing = safe_float(real_local_island_oracle_profile.get("live_missing_me_seconds"))
        summary["real_live_local_island_split_oracle_profile_missing_me_seconds"] = oracle_missing
        summary["real_live_local_island_split_oracle_profile_missing_me_delta_vs_best_live_implementable_seconds"] = round(
            safe_float(summary.get("real_live_target_me_shadow_profile_best_live_implementable_missing_me_seconds"))
            - oracle_missing,
            3,
        )
        summary["real_live_local_island_split_oracle_profile_remote_leak_seconds"] = safe_float(
            real_local_island_oracle_profile.get("live_suspected_remote_leak_in_me_seconds")
        )
        summary["real_live_local_island_split_oracle_profile_contentful_order_mismatch_count"] = safe_int(
            real_local_island_oracle_profile.get("live_contentful_role_constrained_order_mismatch_count")
        )
        summary["real_live_local_island_split_oracle_profile_added_turn_seconds"] = safe_float(
            real_local_island_oracle_profile.get("visible_suppressed_mic_added_turn_seconds")
        )
        summary["real_live_local_island_split_oracle_profile_rejected_turn_count"] = safe_int(
            real_local_island_oracle_profile.get("visible_suppressed_mic_rejected_turn_count")
        )
    else:
        summary["real_live_local_island_split_oracle_profile_missing_me_seconds"] = None
        summary["real_live_local_island_split_oracle_profile_missing_me_delta_vs_best_live_implementable_seconds"] = None
        summary["real_live_local_island_split_oracle_profile_remote_leak_seconds"] = None
        summary["real_live_local_island_split_oracle_profile_contentful_order_mismatch_count"] = None
        summary["real_live_local_island_split_oracle_profile_added_turn_seconds"] = None
        summary["real_live_local_island_split_oracle_profile_rejected_turn_count"] = None
    real_local_island_retime_profile = target_me_shadow_profile_row(
        target_me_shadow_profile_diagnostics_report.get("real")
        if isinstance(target_me_shadow_profile_diagnostics_report.get("real"), dict)
        else {},
        LOCAL_ISLAND_RETIME_ORACLE_PROFILE_POLICY,
    )
    summary["real_live_local_island_retime_oracle_profile_policy"] = LOCAL_ISLAND_RETIME_ORACLE_PROFILE_POLICY
    if real_local_island_retime_profile:
        retime_missing = safe_float(real_local_island_retime_profile.get("live_missing_me_seconds"))
        summary["real_live_local_island_retime_oracle_profile_missing_me_seconds"] = retime_missing
        summary["real_live_local_island_retime_oracle_profile_missing_me_delta_vs_best_live_implementable_seconds"] = round(
            safe_float(summary.get("real_live_target_me_shadow_profile_best_live_implementable_missing_me_seconds"))
            - retime_missing,
            3,
        )
        summary["real_live_local_island_retime_oracle_profile_delta_vs_split_oracle_seconds"] = round(
            safe_float(summary.get("real_live_local_island_split_oracle_profile_missing_me_seconds")) - retime_missing,
            3,
        )
        summary["real_live_local_island_retime_oracle_profile_remote_leak_seconds"] = safe_float(
            real_local_island_retime_profile.get("live_suspected_remote_leak_in_me_seconds")
        )
        summary["real_live_local_island_retime_oracle_profile_contentful_order_mismatch_count"] = safe_int(
            real_local_island_retime_profile.get("live_contentful_role_constrained_order_mismatch_count")
        )
        summary["real_live_local_island_retime_oracle_profile_added_turn_seconds"] = safe_float(
            real_local_island_retime_profile.get("visible_suppressed_mic_added_turn_seconds")
        )
        summary["real_live_local_island_retime_oracle_profile_rejected_turn_count"] = safe_int(
            real_local_island_retime_profile.get("visible_suppressed_mic_rejected_turn_count")
        )
    else:
        summary["real_live_local_island_retime_oracle_profile_missing_me_seconds"] = None
        summary["real_live_local_island_retime_oracle_profile_missing_me_delta_vs_best_live_implementable_seconds"] = None
        summary["real_live_local_island_retime_oracle_profile_delta_vs_split_oracle_seconds"] = None
        summary["real_live_local_island_retime_oracle_profile_remote_leak_seconds"] = None
        summary["real_live_local_island_retime_oracle_profile_contentful_order_mismatch_count"] = None
        summary["real_live_local_island_retime_oracle_profile_added_turn_seconds"] = None
        summary["real_live_local_island_retime_oracle_profile_rejected_turn_count"] = None
    real_boundary_order_retime_oracle_profile = target_me_shadow_profile_row(
        target_me_shadow_profile_diagnostics_report.get("real")
        if isinstance(target_me_shadow_profile_diagnostics_report.get("real"), dict)
        else {},
        BOUNDARY_ORDER_RETIME_ORACLE_PROFILE_POLICY,
    )
    summary["real_live_boundary_order_retime_oracle_profile_policy"] = BOUNDARY_ORDER_RETIME_ORACLE_PROFILE_POLICY
    if real_boundary_order_retime_oracle_profile:
        boundary_order_retime_missing = safe_float(real_boundary_order_retime_oracle_profile.get("live_missing_me_seconds"))
        summary["real_live_boundary_order_retime_oracle_profile_missing_me_seconds"] = boundary_order_retime_missing
        summary["real_live_boundary_order_retime_oracle_profile_missing_me_delta_vs_best_live_implementable_seconds"] = round(
            safe_float(summary.get("real_live_target_me_shadow_profile_best_live_implementable_missing_me_seconds"))
            - boundary_order_retime_missing,
            3,
        )
        summary["real_live_boundary_order_retime_oracle_profile_remote_leak_seconds"] = safe_float(
            real_boundary_order_retime_oracle_profile.get("live_suspected_remote_leak_in_me_seconds")
        )
        summary["real_live_boundary_order_retime_oracle_profile_contentful_order_mismatch_count"] = safe_int(
            real_boundary_order_retime_oracle_profile.get("live_contentful_role_constrained_order_mismatch_count")
        )
        summary["real_live_boundary_order_retime_oracle_profile_retimed_turn_count"] = safe_int(
            real_boundary_order_retime_oracle_profile.get("boundary_order_retime_oracle_turn_count")
        )
        summary["real_live_boundary_order_retime_oracle_profile_retimed_trimmed_seconds"] = safe_float(
            real_boundary_order_retime_oracle_profile.get("boundary_order_retime_oracle_trimmed_seconds")
        )
    else:
        summary["real_live_boundary_order_retime_oracle_profile_missing_me_seconds"] = None
        summary["real_live_boundary_order_retime_oracle_profile_missing_me_delta_vs_best_live_implementable_seconds"] = None
        summary["real_live_boundary_order_retime_oracle_profile_remote_leak_seconds"] = None
        summary["real_live_boundary_order_retime_oracle_profile_contentful_order_mismatch_count"] = None
        summary["real_live_boundary_order_retime_oracle_profile_retimed_turn_count"] = None
        summary["real_live_boundary_order_retime_oracle_profile_retimed_trimmed_seconds"] = None
    real_boundary_order_split_retime_oracle_profile = target_me_shadow_profile_row(
        target_me_shadow_profile_diagnostics_report.get("real")
        if isinstance(target_me_shadow_profile_diagnostics_report.get("real"), dict)
        else {},
        BOUNDARY_ORDER_SPLIT_RETIME_ORACLE_PROFILE_POLICY,
    )
    summary["real_live_boundary_order_split_retime_oracle_profile_policy"] = (
        BOUNDARY_ORDER_SPLIT_RETIME_ORACLE_PROFILE_POLICY
    )
    if real_boundary_order_split_retime_oracle_profile:
        split_retime_missing = safe_float(real_boundary_order_split_retime_oracle_profile.get("live_missing_me_seconds"))
        summary["real_live_boundary_order_split_retime_oracle_profile_missing_me_seconds"] = split_retime_missing
        summary["real_live_boundary_order_split_retime_oracle_profile_missing_me_delta_vs_best_live_implementable_seconds"] = round(
            safe_float(summary.get("real_live_target_me_shadow_profile_best_live_implementable_missing_me_seconds"))
            - split_retime_missing,
            3,
        )
        summary["real_live_boundary_order_split_retime_oracle_profile_remote_leak_seconds"] = safe_float(
            real_boundary_order_split_retime_oracle_profile.get("live_suspected_remote_leak_in_me_seconds")
        )
        summary["real_live_boundary_order_split_retime_oracle_profile_contentful_order_mismatch_count"] = safe_int(
            real_boundary_order_split_retime_oracle_profile.get("live_contentful_role_constrained_order_mismatch_count")
        )
        summary["real_live_boundary_order_split_retime_oracle_profile_retimed_turn_count"] = safe_int(
            real_boundary_order_split_retime_oracle_profile.get("boundary_order_retime_oracle_turn_count")
        )
        summary["real_live_boundary_order_split_retime_oracle_profile_retimed_trimmed_seconds"] = safe_float(
            real_boundary_order_split_retime_oracle_profile.get("boundary_order_retime_oracle_trimmed_seconds")
        )
        summary["real_live_boundary_order_split_retime_oracle_profile_preserved_prefix_count"] = safe_int(
            real_boundary_order_split_retime_oracle_profile.get(
                "boundary_order_split_retime_oracle_preserved_prefix_count"
            )
        )
        summary["real_live_boundary_order_split_retime_oracle_profile_preserved_prefix_seconds"] = safe_float(
            real_boundary_order_split_retime_oracle_profile.get(
                "boundary_order_split_retime_oracle_preserved_prefix_seconds"
            )
        )
    else:
        summary["real_live_boundary_order_split_retime_oracle_profile_missing_me_seconds"] = None
        summary["real_live_boundary_order_split_retime_oracle_profile_missing_me_delta_vs_best_live_implementable_seconds"] = None
        summary["real_live_boundary_order_split_retime_oracle_profile_remote_leak_seconds"] = None
        summary["real_live_boundary_order_split_retime_oracle_profile_contentful_order_mismatch_count"] = None
        summary["real_live_boundary_order_split_retime_oracle_profile_retimed_turn_count"] = None
        summary["real_live_boundary_order_split_retime_oracle_profile_retimed_trimmed_seconds"] = None
        summary["real_live_boundary_order_split_retime_oracle_profile_preserved_prefix_count"] = None
        summary["real_live_boundary_order_split_retime_oracle_profile_preserved_prefix_seconds"] = None
    real_soft_boundary_profile = target_me_shadow_profile_row(
        target_me_shadow_profile_diagnostics_report.get("real")
        if isinstance(target_me_shadow_profile_diagnostics_report.get("real"), dict)
        else {},
        SOFT_LOCAL_SPEAKER_BOUNDARY_SPLIT_RETIME_PROFILE_POLICY,
    )
    best_live_missing = safe_float(summary.get("real_live_target_me_shadow_profile_best_live_implementable_missing_me_seconds"))
    best_live_remote_leak = safe_float(
        summary.get("real_live_target_me_shadow_profile_best_live_implementable_remote_leak_seconds")
    )
    best_live_order = safe_int(
        summary.get("real_live_target_me_shadow_profile_best_live_implementable_contentful_order_mismatch_count")
    )
    if real_soft_boundary_profile:
        soft_missing = safe_float(real_soft_boundary_profile.get("live_missing_me_seconds"))
        soft_remote_leak = safe_float(real_soft_boundary_profile.get("live_suspected_remote_leak_in_me_seconds"))
        soft_order = safe_int(real_soft_boundary_profile.get("live_contentful_role_constrained_order_mismatch_count"))
        soft_missing_delta = round(best_live_missing - soft_missing, 3)
        soft_remote_leak_delta = round(soft_remote_leak - best_live_remote_leak, 3)
        soft_order_delta = soft_order - best_live_order
        soft_status = "no_incremental_gain"
        if soft_remote_leak_delta > 0 or soft_order_delta > 0:
            soft_status = "rejected_regression"
        elif soft_missing_delta > 0:
            soft_status = "candidate_improves_local_recall"
        soft_boundary_shadow_lab = {
            "schema": "murmurmark.live_soft_local_speaker_boundary_shadow_lab/v1",
            "status": soft_status,
            "promotion_allowed": False,
            "profile": SOFT_LOCAL_SPEAKER_BOUNDARY_SPLIT_RETIME_PROFILE_POLICY,
            "interpretation": (
                "diagnostic only: tests whether softer local-speaker boundary evidence adds safe "
                "Me turns beyond the current best live profile"
            ),
            "metrics": {
                "missing_me_seconds": soft_missing,
                "missing_me_delta_vs_best_live_implementable_seconds": soft_missing_delta,
                "remote_leak_seconds": soft_remote_leak,
                "remote_leak_delta_vs_best_live_implementable_seconds": soft_remote_leak_delta,
                "contentful_order_mismatch_count": soft_order,
                "contentful_order_mismatch_delta_vs_best_live_implementable": soft_order_delta,
                "visible_suppressed_mic_added_turn_seconds": safe_float(
                    real_soft_boundary_profile.get("visible_suppressed_mic_added_turn_seconds")
                ),
                "visible_suppressed_mic_added_turn_count": safe_int(
                    real_soft_boundary_profile.get("visible_suppressed_mic_added_turn_count")
                ),
            },
            "conclusion": (
                "keep current profile; soft boundary evidence does not reduce missing-Me"
                if soft_status == "no_incremental_gain"
                else "do not promote soft boundary evidence without stronger regression proof"
            ),
        }
    else:
        soft_boundary_shadow_lab = {
            "schema": "murmurmark.live_soft_local_speaker_boundary_shadow_lab/v1",
            "status": "missing_profile",
            "promotion_allowed": False,
            "profile": SOFT_LOCAL_SPEAKER_BOUNDARY_SPLIT_RETIME_PROFILE_POLICY,
        }
    summary["real_live_soft_local_speaker_boundary_shadow_lab_status"] = soft_boundary_shadow_lab.get("status")
    summary["real_live_soft_local_speaker_boundary_shadow_lab_missing_delta_seconds"] = (
        (soft_boundary_shadow_lab.get("metrics") or {}).get("missing_me_delta_vs_best_live_implementable_seconds")
        if isinstance(soft_boundary_shadow_lab.get("metrics"), dict)
        else None
    )
    summary["real_live_soft_local_speaker_boundary_shadow_lab_remote_leak_delta_seconds"] = (
        (soft_boundary_shadow_lab.get("metrics") or {}).get("remote_leak_delta_vs_best_live_implementable_seconds")
        if isinstance(soft_boundary_shadow_lab.get("metrics"), dict)
        else None
    )
    top_online_speaker_boundary_unit = (
        online_speaker_boundary_design_lab_report.get("top_design_unit")
        if isinstance(online_speaker_boundary_design_lab_report.get("top_design_unit"), dict)
        else {}
    )
    summary["real_live_online_speaker_boundary_design_status"] = online_speaker_boundary_design_lab_report.get("status")
    summary["real_live_online_speaker_boundary_design_actionable_seconds"] = safe_float(
        online_speaker_boundary_design_lab_report.get("actionable_seconds")
    )
    summary["real_live_online_speaker_boundary_design_potential_publish_seconds"] = safe_float(
        online_speaker_boundary_design_lab_report.get("potential_publish_seconds")
    )
    summary["real_live_online_speaker_boundary_design_top_unit"] = (
        top_online_speaker_boundary_unit.get("id") if top_online_speaker_boundary_unit else None
    )
    summary["real_live_online_speaker_boundary_design_top_unit_seconds"] = (
        safe_float(top_online_speaker_boundary_unit.get("seconds")) if top_online_speaker_boundary_unit else None
    )
    summary["real_live_mixed_speaker_boundary_voice_coverage_status"] = (
        mixed_speaker_boundary_voice_coverage_lab_report.get("status")
    )
    summary["real_live_mixed_speaker_boundary_voice_coverage_seconds"] = safe_float(
        mixed_speaker_boundary_voice_coverage_lab_report.get("seconds")
    )
    summary["real_live_mixed_speaker_boundary_voice_coverage_voice_overlap_seconds"] = safe_float(
        mixed_speaker_boundary_voice_coverage_lab_report.get("voice_overlap_seconds")
    )
    summary["real_live_mixed_speaker_boundary_voice_coverage_publication_candidate_seconds"] = safe_float(
        mixed_speaker_boundary_voice_coverage_lab_report.get("publication_candidate_seconds")
    )
    summary["real_live_mixed_speaker_boundary_voice_coverage_no_audit_seconds"] = safe_float(
        mixed_speaker_boundary_voice_coverage_lab_report.get("no_target_me_audit_seconds")
    )
    summary["real_live_mixed_speaker_boundary_voice_coverage_no_overlap_seconds"] = safe_float(
        mixed_speaker_boundary_voice_coverage_lab_report.get("no_voice_overlap_seconds")
    )
    summary["real_live_mixed_speaker_boundary_voice_coverage_enrollment_not_ready_seconds"] = safe_float(
        mixed_speaker_boundary_voice_coverage_lab_report.get("target_me_enrollment_not_ready_seconds")
    )
    remote_guarded_profile_prefix = f"real_live_target_me_shadow_profile_{REMOTE_GUARDED_VOICE_BOUNDARY_PROFILE_POLICY}"
    remote_guarded_added_seconds = safe_float(
        summary.get(f"{remote_guarded_profile_prefix}_remote_guarded_voice_boundary_added_turn_seconds")
    )
    publication_candidate_seconds = safe_float(
        mixed_speaker_boundary_voice_coverage_lab_report.get("publication_candidate_seconds")
    )
    if publication_candidate_seconds > 0 and remote_guarded_added_seconds + 0.001 >= publication_candidate_seconds:
        mixed_speaker_boundary_voice_coverage_lab_report["recommended_next"] = {
            "id": "tighten_voice_remote_guard_for_mixed_rows",
            "why": (
                "remote-guarded boundary candidates are already materialized in the diagnostic "
                "shadow profile; remaining rows still need stronger voice/remote guard evidence"
            ),
            "materialized_remote_guarded_voice_boundary_seconds": round(remote_guarded_added_seconds, 3),
            "weak_seconds": round(safe_float(mixed_speaker_boundary_voice_coverage_lab_report.get("weak_or_ambiguous_seconds")), 3),
        }
    mixed_voice_next = (
        mixed_speaker_boundary_voice_coverage_lab_report.get("recommended_next")
        if isinstance(mixed_speaker_boundary_voice_coverage_lab_report.get("recommended_next"), dict)
        else {}
    )
    summary["real_live_mixed_speaker_boundary_voice_coverage_recommended_next"] = (
        mixed_voice_next.get("id") if mixed_voice_next else None
    )
    summary["real_live_tight_voice_remote_guard_status"] = tight_voice_remote_guard_lab_report.get("status")
    summary["real_live_tight_voice_remote_guard_seconds"] = safe_float(
        tight_voice_remote_guard_lab_report.get("seconds")
    )
    summary["real_live_tight_voice_remote_guard_candidate_seconds"] = safe_float(
        tight_voice_remote_guard_lab_report.get("candidate_seconds")
    )
    summary["real_live_tight_voice_remote_guard_blocked_seconds"] = safe_float(
        tight_voice_remote_guard_lab_report.get("blocked_seconds")
    )
    tight_voice_top_blocker = (
        tight_voice_remote_guard_lab_report.get("top_blocker")
        if isinstance(tight_voice_remote_guard_lab_report.get("top_blocker"), dict)
        else {}
    )
    summary["real_live_tight_voice_remote_guard_top_blocker"] = tight_voice_top_blocker.get("label")
    tight_voice_next = (
        tight_voice_remote_guard_lab_report.get("recommended_next")
        if isinstance(tight_voice_remote_guard_lab_report.get("recommended_next"), dict)
        else {}
    )
    summary["real_live_tight_voice_remote_guard_recommended_next"] = (
        tight_voice_next.get("id") if tight_voice_next else None
    )
    local_island_timing_gap_report = {
        "schema": "murmurmark.live_local_island_timing_gap/v1",
        "status": "ok" if real_local_island_retime_profile else "missing_retime_oracle_profile",
        "promotion_allowed": False,
        "split_oracle_policy": LOCAL_ISLAND_SPLIT_ORACLE_PROFILE_POLICY,
        "retime_oracle_policy": LOCAL_ISLAND_RETIME_ORACLE_PROFILE_POLICY,
        "best_live_implementable_policy": summary.get("real_live_target_me_shadow_profile_best_live_implementable_policy"),
        "best_live_implementable_missing_me_seconds": summary.get(
            "real_live_target_me_shadow_profile_best_live_implementable_missing_me_seconds"
        ),
        "split_oracle_missing_me_seconds": summary.get("real_live_local_island_split_oracle_profile_missing_me_seconds"),
        "retime_oracle_missing_me_seconds": summary.get("real_live_local_island_retime_oracle_profile_missing_me_seconds"),
        "retime_gain_vs_best_live_implementable_seconds": summary.get(
            "real_live_local_island_retime_oracle_profile_missing_me_delta_vs_best_live_implementable_seconds"
        ),
        "retime_gain_vs_split_oracle_seconds": summary.get(
            "real_live_local_island_retime_oracle_profile_delta_vs_split_oracle_seconds"
        ),
        "retime_oracle_remote_leak_seconds": summary.get("real_live_local_island_retime_oracle_profile_remote_leak_seconds"),
        "retime_oracle_contentful_order_mismatch_count": summary.get(
            "real_live_local_island_retime_oracle_profile_contentful_order_mismatch_count"
        ),
        "live_audio_anchor_lab_status": local_island_audio_anchor_lab_report.get("status"),
        "live_audio_anchor_seconds": summary.get("real_live_local_island_audio_anchor_lab_anchor_seconds"),
        "live_audio_anchor_island_count": summary.get("real_live_local_island_audio_anchor_lab_anchor_island_count"),
        "retime_anchor_lab_status": local_island_retime_anchor_lab_report.get("status"),
        "retime_anchor_batch_seconds": local_island_retime_anchor_lab_report.get("batch_seconds"),
        "retime_anchor_local_island_seconds": local_island_retime_anchor_lab_report.get("local_island_seconds"),
        "retime_anchor_span_seconds": local_island_retime_anchor_lab_report.get("anchor_span_seconds"),
        "retime_anchor_context_expansion_seconds": local_island_retime_anchor_lab_report.get(
            "context_expansion_seconds"
        ),
        "retime_anchor_max_leading_gap_seconds": local_island_retime_anchor_lab_report.get(
            "max_leading_gap_seconds"
        ),
        "retime_anchor_max_inter_island_gap_seconds": local_island_retime_anchor_lab_report.get(
            "max_inter_island_gap_seconds"
        ),
        "live_only_candidate_lab_status": live_only_local_island_candidate_lab_report.get("status"),
        "live_only_candidate_seconds": summary.get("real_live_live_only_local_island_candidate_lab_candidate_seconds"),
        "live_only_candidate_local_seconds": summary.get("real_live_live_only_local_island_candidate_lab_local_seconds"),
        "live_only_candidate_remote_risk_seconds": summary.get(
            "real_live_live_only_local_island_candidate_lab_remote_risk_seconds"
        ),
        "live_only_candidate_precision_proxy": summary.get(
            "real_live_live_only_local_island_candidate_lab_precision_proxy"
        ),
        "live_only_strict_zero_remote_risk_candidate_seconds": summary.get(
            "real_live_live_only_local_island_strict_zero_remote_risk_candidate_seconds"
        ),
        "live_only_strict_zero_remote_risk_local_seconds": summary.get(
            "real_live_live_only_local_island_strict_zero_remote_risk_local_seconds"
        ),
        "live_only_strict_zero_remote_risk_remote_risk_seconds": summary.get(
            "real_live_live_only_local_island_strict_zero_remote_risk_remote_risk_seconds"
        ),
        "live_only_strict_zero_remote_risk_precision_proxy": summary.get(
            "real_live_live_only_local_island_strict_zero_remote_risk_precision_proxy"
        ),
        "live_only_retime_boundary_lab_status": live_only_retime_boundary_lab_report.get("status"),
        "live_only_retime_boundary_recommended_profile": summary.get(
            "real_live_live_only_retime_boundary_recommended_profile"
        ),
        "live_only_retime_boundary_anchor_group_count": summary.get(
            "real_live_live_only_retime_boundary_anchor_group_count"
        ),
        "live_only_retime_boundary_anchor_seconds": summary.get(
            "real_live_live_only_retime_boundary_anchor_seconds"
        ),
        "live_only_retime_boundary_recommended_candidate_seconds": summary.get(
            "real_live_live_only_retime_boundary_recommended_candidate_seconds"
        ),
        "live_only_retime_boundary_recommended_missing_overlap_seconds": summary.get(
            "real_live_live_only_retime_boundary_recommended_missing_overlap_seconds"
        ),
        "live_only_retime_boundary_recommended_remote_risk_seconds": summary.get(
            "real_live_live_only_retime_boundary_recommended_remote_risk_seconds"
        ),
        "live_only_retime_boundary_best_missing_profile": summary.get(
            "real_live_live_only_retime_boundary_best_missing_profile"
        ),
        "live_only_retime_boundary_best_missing_candidate_seconds": summary.get(
            "real_live_live_only_retime_boundary_best_missing_candidate_seconds"
        ),
        "live_only_retime_boundary_best_missing_overlap_seconds": summary.get(
            "real_live_live_only_retime_boundary_best_missing_overlap_seconds"
        ),
        "live_only_retime_boundary_best_missing_remote_risk_seconds": summary.get(
            "real_live_live_only_retime_boundary_best_missing_remote_risk_seconds"
        ),
        "live_only_retime_boundary_best_zero_remote_evaluated_profile": summary.get(
            "real_live_live_only_retime_boundary_best_zero_remote_evaluated_profile"
        ),
        "live_only_retime_boundary_best_zero_remote_evaluated_candidate_seconds": summary.get(
            "real_live_live_only_retime_boundary_best_zero_remote_evaluated_candidate_seconds"
        ),
        "live_only_retime_boundary_best_zero_remote_evaluated_missing_overlap_seconds": summary.get(
            "real_live_live_only_retime_boundary_best_zero_remote_evaluated_missing_overlap_seconds"
        ),
        "live_only_retime_boundary_best_zero_remote_evaluated_remote_risk_seconds": summary.get(
            "real_live_live_only_retime_boundary_best_zero_remote_evaluated_remote_risk_seconds"
        ),
        "strict_live_only_profile_policy": STRICT_LIVE_ONLY_LOCAL_ISLAND_PROFILE_POLICY,
        "strict_live_only_profile_missing_me_seconds": summary.get(
            f"{strict_live_only_profile_summary_prefix}_live_missing_me_seconds"
        ),
        "strict_live_only_profile_remote_leak_seconds": summary.get(
            f"{strict_live_only_profile_summary_prefix}_live_suspected_remote_leak_in_me_seconds"
        ),
        "strict_live_only_profile_contentful_order_mismatch_count": summary.get(
            f"{strict_live_only_profile_summary_prefix}_live_contentful_role_constrained_order_mismatch_count"
        ),
        "strict_live_only_profile_added_turn_seconds": summary.get(
            f"{strict_live_only_profile_summary_prefix}_visible_suppressed_mic_added_turn_seconds"
        ),
        "strict_live_only_profile_non_passing_gate_count": summary.get(
            f"{strict_live_only_profile_summary_prefix}_non_passing_gate_count"
        ),
        "combined_strict_audio_safe_union_profile_policy": STRICT_LIVE_ONLY_LOCAL_ISLAND_AUDIO_SAFE_UNION_PROFILE_POLICY,
        "combined_strict_audio_safe_union_profile_missing_me_seconds": summary.get(
            f"{combined_strict_audio_safe_union_profile_summary_prefix}_live_missing_me_seconds"
        ),
        "combined_strict_audio_safe_union_profile_remote_leak_seconds": summary.get(
            f"{combined_strict_audio_safe_union_profile_summary_prefix}_live_suspected_remote_leak_in_me_seconds"
        ),
        "combined_strict_audio_safe_union_profile_contentful_order_mismatch_count": summary.get(
            f"{combined_strict_audio_safe_union_profile_summary_prefix}_live_contentful_role_constrained_order_mismatch_count"
        ),
        "combined_strict_audio_safe_union_profile_added_turn_seconds": summary.get(
            f"{combined_strict_audio_safe_union_profile_summary_prefix}_visible_suppressed_mic_added_turn_seconds"
        ),
        "combined_strict_audio_safe_union_profile_non_passing_gate_count": summary.get(
            f"{combined_strict_audio_safe_union_profile_summary_prefix}_non_passing_gate_count"
        ),
        "remote_forbidden_boundary_classifier_profile_policy": REMOTE_FORBIDDEN_BOUNDARY_CLASSIFIER_PROFILE_POLICY,
        "remote_forbidden_boundary_classifier_profile_missing_me_seconds": summary.get(
            f"{remote_forbidden_boundary_classifier_profile_summary_prefix}_live_missing_me_seconds"
        ),
        "remote_forbidden_boundary_classifier_profile_remote_leak_seconds": summary.get(
            f"{remote_forbidden_boundary_classifier_profile_summary_prefix}_live_suspected_remote_leak_in_me_seconds"
        ),
        "remote_forbidden_boundary_classifier_profile_contentful_order_mismatch_count": summary.get(
            f"{remote_forbidden_boundary_classifier_profile_summary_prefix}_live_contentful_role_constrained_order_mismatch_count"
        ),
        "remote_forbidden_boundary_classifier_profile_added_turn_seconds": summary.get(
            f"{remote_forbidden_boundary_classifier_profile_summary_prefix}_visible_suppressed_mic_added_turn_seconds"
        ),
        "remote_forbidden_boundary_classifier_profile_rejected_turn_count": summary.get(
            f"{remote_forbidden_boundary_classifier_profile_summary_prefix}_visible_suppressed_mic_rejected_turn_count"
        ),
        "remote_forbidden_boundary_classifier_profile_non_passing_gate_count": summary.get(
            f"{remote_forbidden_boundary_classifier_profile_summary_prefix}_non_passing_gate_count"
        ),
        "strict_shadow_delta_lab_status": strict_local_island_shadow_delta_lab_report.get("status"),
        "strict_shadow_delta_incremental_turn_seconds": strict_local_island_shadow_delta_lab_report.get(
            "incremental_strict_turn_seconds"
        ),
        "strict_shadow_delta_closed_missing_me_seconds": strict_local_island_shadow_delta_lab_report.get(
            "closed_missing_me_seconds"
        ),
        "strict_shadow_delta_net_missing_me_delta_seconds": strict_local_island_shadow_delta_lab_report.get(
            "net_missing_me_delta_seconds"
        ),
        "strict_shadow_delta_by_classification": strict_local_island_shadow_delta_lab_report.get(
            "by_classification"
        ),
        "requires_batch_timing": True,
        "requires_batch_role_labels": True,
        "required_online_evidence": [
            "live-local-island detection without batch_role_label",
            "online timing anchor for local islands inside mixed remote-active chunks",
            "remote-forbidden guard before publication",
            "contentful-order gate that does not need authoritative batch intervals",
        ],
        "interpretation": (
            "diagnostic only: retime oracle proves local-island timing is the next blocker, "
            "but cannot be promoted because it uses authoritative batch timing and batch labels"
        ),
    }
    live_next_unlock = live_next_unlock_report(
        summary=summary,
        remaining_gap=best_live_profile_remaining_gap,
        order_risk_triage=live_order_risk_triage_report,
        speaker_boundary_lab=speaker_boundary_lab_report,
        mixed_voice_coverage_lab=mixed_speaker_boundary_voice_coverage_lab_report,
        tight_voice_remote_guard_lab=tight_voice_remote_guard_lab_report,
        local_island_split=local_island_split_lab_report,
        live_only_local_island=live_only_local_island_candidate_lab_report,
        timing_gap=local_island_timing_gap_report,
    )
    remaining_by_bucket = (
        best_live_profile_remaining_gap.get("by_bucket")
        if isinstance(best_live_profile_remaining_gap.get("by_bucket"), dict)
        else {}
    )
    for bucket in (
        "visible_with_target_me",
        "visible_without_target_me",
        "not_visible_with_target_me",
        "not_visible_without_target_me",
    ):
        bucket_row = remaining_by_bucket.get(bucket) if isinstance(remaining_by_bucket.get(bucket), dict) else {}
        summary[f"real_live_target_me_shadow_profile_best_live_implementable_remaining_gap_{bucket}_seconds"] = (
            safe_float(bucket_row.get("seconds"))
        )
        summary[f"real_live_target_me_shadow_profile_best_live_implementable_remaining_gap_{bucket}_count"] = (
            safe_int(bucket_row.get("count"))
        )
    if not pilot_allowed:
        coverage_path_status = "blocked_until_full_fail_open_proof"
        coverage_recommended_next = (
            "run MURMURMARK_RUN_LIVE_CAPTURE_TEST=1 scripts/check-capture-regressions.sh"
        )
    elif candidate_blocking_dimensions:
        coverage_path_status = "resolve_capture_safe_candidate_blockers"
        coverage_recommended_next = (
            metric_aware_next_focus(candidate_blocking_dimensions, summary) or {}
        ).get("recommended_next")
    elif coverage_target["passing_compared_sessions_remaining"] > 0:
        coverage_path_status = "needs_new_controlled_live_evidence"
        coverage_recommended_next = "murmurmark live pilot --controlled-real --skip-safety-gate --preflight-only"
    else:
        coverage_path_status = "coverage_target_met_shadow_still_locked"
        coverage_recommended_next = "murmurmark live gate"
    coverage_path = {
        "status": coverage_path_status,
        "recommended_next": coverage_recommended_next,
        "passing_compared_sessions_remaining": coverage_target["passing_compared_sessions_remaining"],
        "capture_safe_candidate_sessions": len(capture_safe_candidate_rows),
        "capture_safe_candidate_passing_sessions": summary["real_capture_safe_candidate_passing_sessions"],
        "capture_safe_candidate_blocking_dimensions": candidate_blocking_dimensions,
        "historical_non_candidate_sessions": len(historical_non_candidate_session_ids),
        "historical_non_candidate_session_ids": historical_non_candidate_session_ids,
        "new_real_live_collection_allowed": False,
        "controlled_real_live_pilot_allowed": pilot_allowed,
        "batch_authoritative": True,
    }
    summary["coverage_path_status"] = coverage_path_status
    summary["coverage_path_recommended_next"] = coverage_recommended_next
    summary["coverage_path_historical_non_candidate_sessions"] = len(historical_non_candidate_session_ids)
    summary["coverage_path_new_controlled_evidence_required"] = coverage_path_status == "needs_new_controlled_live_evidence"
    objective_audit = build_objective_audit(
        summary,
        coverage_target,
        real_parity_dimensions_payload,
        promotion_policy,
        candidate_scope,
        capture_regression_check,
    )
    objective_audit["capture_safe_candidate_scope"] = candidate_scope
    objective_next_focus = objective_audit.get("next_focus") if isinstance(objective_audit, dict) else None
    if not isinstance(objective_next_focus, dict):
        objective_next_focus = {}
    summary["objective_status"] = objective_audit.get("overall_status")
    summary["objective_ready_for_live_promotion"] = bool(objective_audit.get("ready_for_live_promotion"))
    summary["objective_next_focus"] = objective_next_focus.get("action_id")
    summary["objective_next_focus_dimension"] = objective_next_focus.get("dimension")
    summary["objective_next_recommended_next"] = objective_next_focus.get("recommended_next")
    summary["real_blocker_triage_items"] = safe_int(real_blocker_triage_summary.get("total_items"))
    summary["real_blocker_triage_sessions"] = safe_int(real_blocker_triage_summary.get("session_count"))
    summary["real_blocker_triage_uncategorized_items"] = safe_int(
        real_blocker_triage_summary.get("uncategorized_gate_issue_count")
    )
    return {
        "schema": SCHEMA,
        "generator": {"name": "report-live-corpus-gates", "version": SCRIPT_VERSION},
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": summary["target_status"],
        "sessions_root": str(root),
        "summary": summary,
        "strict_coverage": {
            "requested": strict_requested,
            "status": summary["strict_coverage_status"],
            "requirements": {
                "min_live_sessions": args.min_live_sessions,
                "min_compared_sessions": args.min_compared_sessions,
                "min_meaningful_compared_sessions": args.min_meaningful_compared_sessions,
                "min_passing_compared_sessions": args.min_passing_compared_sessions,
                "max_order_mismatches": args.max_order_mismatches,
                "max_missing_me_sec": args.max_missing_me_sec,
                "max_remote_in_me_sec": args.max_remote_in_me_sec,
                "max_boundary_duplicates": args.max_boundary_duplicates,
                "require_passing_gates": args.require_passing_gates,
                "fail_on_promotion": args.fail_on_promotion,
            },
            "failures": strict_failures,
        },
        "live_comparison_refresh": {
            "requested": bool(args.refresh),
            "status": refresh_status,
            "attempted_sessions": len(refresh_attempted),
            "failed_sessions": len(refresh_failed),
            "skipped_sessions": refresh_skipped,
            "results": refresh_results,
        },
        "coverage_target": coverage_target,
        "parity_dimensions": parity_dimensions_payload,
        "real_parity_dimensions": real_parity_dimensions_payload,
        "real_capture_safe_candidate_parity_dimensions": candidate_parity_dimensions_payload,
        "capture_safe_candidate_scope": candidate_scope,
        "live_local_recall_rescue_policy_diagnostics": local_recall_rescue_policy_diagnostics,
        "live_local_recall_rescue_lab": local_recall_rescue_lab_report,
        "capture_safe_candidate_live_local_recall_rescue_lab": candidate_local_recall_rescue_lab_report,
        "capture_safe_evaluable_live_local_recall_rescue_lab": evaluable_local_recall_rescue_lab_report,
        "live_local_recall_target_me_diagnostics": target_me_diagnostics_report,
        "live_target_me_shadow_policy_diagnostics": target_me_shadow_diagnostics_report,
        "live_target_me_shadow_profile_diagnostics": target_me_shadow_profile_diagnostics_report,
        "live_target_me_shadow_profile_best_live_implementable_remaining_gap": best_live_profile_remaining_gap,
        "live_order_risk_triage": live_order_risk_triage_report,
        "live_soft_local_speaker_boundary_shadow_lab": soft_boundary_shadow_lab,
        "live_speaker_boundary_evidence_lab": speaker_boundary_lab_report,
        "live_online_speaker_boundary_evidence_design_lab": online_speaker_boundary_design_lab_report,
        "live_mixed_speaker_boundary_voice_coverage_lab": mixed_speaker_boundary_voice_coverage_lab_report,
        "live_tight_voice_remote_guard_lab": tight_voice_remote_guard_lab_report,
        "live_local_island_split_lab": local_island_split_lab_report,
        "live_local_island_audio_anchor_lab": local_island_audio_anchor_lab_report,
        "live_local_island_retime_anchor_lab": local_island_retime_anchor_lab_report,
        "live_only_local_island_candidate_lab": live_only_local_island_candidate_lab_report,
        "live_only_retime_boundary_candidate_lab": live_only_retime_boundary_lab_report,
        "live_strict_local_island_shadow_delta_lab": strict_local_island_shadow_delta_lab_report,
        "live_local_island_timing_gap": local_island_timing_gap_report,
        "live_next_unlock": live_next_unlock,
        "live_local_recall_gap_examples": local_recall_examples,
        "capture_safe_candidate_local_recall_gap_examples": candidate_local_recall_examples,
        "capture_safe_evaluable_local_recall_gap_examples": evaluable_local_recall_examples,
        "coverage_path": coverage_path,
        "promotion_policy": promotion_policy,
        "capture_regression_check": {
            "path": str(capture_regression_check_path),
            "present": capture_regression_check is not None,
            "status": capture_regression_check.get("status") if isinstance(capture_regression_check, dict) else None,
            "mode": capture_regression_check.get("mode") if isinstance(capture_regression_check, dict) else None,
            "capture_safe_proof_status": (
                (capture_regression_check.get("capture_safe_proof") or {}).get("status")
                if isinstance(capture_regression_check, dict)
                and isinstance(capture_regression_check.get("capture_safe_proof"), dict)
                else None
            ),
        },
        "objective_audit": objective_audit,
        "blockers": dict(blockers),
        "warnings": dict(warnings),
        "gate_counts": {name: dict(counts) for name, counts in sorted(gate_counts.items())},
        "real_gate_counts": {name: dict(counts) for name, counts in sorted(real_gate_counts.items())},
        "gate_issues": gate_issues,
        "real_blocker_triage_summary": real_blocker_triage_summary,
        "real_blocker_triage": real_blocker_triage,
        "sessions": rows,
        "recommended_next": next_commands[0],
        "next_commands": next_commands,
    }


def build_gate_issues(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for row in rows:
        if not row.get("live_present"):
            continue
        session = str(row.get("session") or "")
        scope = str(row.get("evidence_scope") or "diagnostic")
        session_path = session if session.startswith("/") or session.startswith("sessions/") else f"sessions/{session}"
        comparison = f"{session_path}/derived/live/live_batch_comparison.json" if session else ""
        for gate in row.get("non_passing_gates") or []:
            if not isinstance(gate, dict):
                continue
            issues.append(
                {
                    "session": session,
                    "evidence_scope": scope,
                    "gate": gate.get("name"),
                    "status": gate.get("status"),
                    "reason": gate.get("reason"),
                    "evidence": gate.get("evidence"),
                    "session_path": session_path,
                    "comparison": comparison,
                }
            )
    return sorted(issues, key=lambda item: (item.get("evidence_scope") != "real_meeting", item.get("session") or ""))


def gate_blob(row: dict[str, Any], gate: dict[str, Any]) -> str:
    value = {
        "gate": gate,
        "blockers": row.get("blockers"),
        "warnings": row.get("warnings"),
        "promotion_blockers": row.get("promotion_blockers"),
    }
    return json.dumps(value, ensure_ascii=False, sort_keys=True).lower()


def triage_categories_for_gate(row: dict[str, Any], gate: dict[str, Any]) -> list[str]:
    name = str(gate.get("name") or "unknown")
    if name == "capture_safety":
        return ["capture_safety_risk"]
    if name in {"review_burden", "selected_notes_readiness"}:
        return ["batch_review_required"]
    if name == "local_recall":
        return ["live_local_recall_gap"]
    if name == "remote_duplicate_leak":
        return ["live_remote_leakage"]
    if name == "live_token_recall":
        return ["live_draft_text_drift"]
    if name in {"chunk_boundary_risks", "duplicate_chunks"}:
        return ["chunk_boundary_risk"]
    if name == "order_risk":
        return ["order_risk"]
    if name in {"required_artifacts", "raw_batch_authoritative"}:
        text = gate_blob(row, gate)
        categories: list[str] = []
        if "batch_clean_dialogue_missing" in text or "batch_transcript_missing" in text:
            categories.append("missing_batch_artifacts")
        if (
            "live_chunks_missing" in text
            or "live_report_missing" in text
            or "live_batch_comparison_missing" in text
            or "live_asr" in text
        ):
            categories.append("missing_live_asr_artifacts")
        if not categories:
            categories.append("missing_batch_artifacts")
        return categories
    return ["other"]


def triage_severity(statuses: list[str]) -> str:
    rank = {
        "blocked": 4,
        "failed": 4,
        "warning": 3,
        "not_evaluated": 2,
        "missing": 2,
        "unknown": 1,
    }
    status = max((status or "unknown" for status in statuses), key=lambda item: rank.get(item, 1))
    if status in {"blocked", "failed"}:
        return "blocker"
    if status == "warning":
        return "warning"
    if status in {"not_evaluated", "missing"}:
        return "needs_evidence"
    return "unknown"


def build_real_blocker_triage(real_live_rows: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    real_gate_issue_keys: set[tuple[str, str, str, str]] = set()
    categorized_gate_issue_keys: set[tuple[str, str, str, str]] = set()
    for row in real_live_rows:
        session = str(row.get("session") or "")
        if not session:
            continue
        session_path = session if session.startswith("/") or session.startswith("sessions/") else f"sessions/{session}"
        comparison = f"{session_path}/derived/live/live_batch_comparison.json"
        for gate in row.get("non_passing_gates") or []:
            if not isinstance(gate, dict):
                continue
            gate_name = str(gate.get("name") or "unknown")
            gate_status = str(gate.get("status") or "unknown")
            gate_reason = str(gate.get("reason") or "")
            gate_key = (session, gate_name, gate_status, gate_reason)
            real_gate_issue_keys.add(gate_key)
            categories = triage_categories_for_gate(row, gate)
            if categories:
                categorized_gate_issue_keys.add(gate_key)
            for category in categories:
                item = grouped.setdefault(
                    (session, category),
                    {
                        "session": session,
                        "session_path": session_path,
                        "comparison": comparison,
                        "category": category,
                        "title": TRIAGE_CATEGORY_INFO.get(category, TRIAGE_CATEGORY_INFO["other"])["title"],
                        "promotion_blocker": True,
                        "gates": [],
                        "dimensions": [],
                        "reasons": [],
                        "evidence": [],
                        "metrics": row.get("metrics") or {},
                        "recommended_next": TRIAGE_CATEGORY_INFO.get(
                            category,
                            TRIAGE_CATEGORY_INFO["other"],
                        )["recommended_next"],
                    },
                )
                item["gates"].append(
                    {
                        "name": gate_name,
                        "status": str(gate.get("status") or "unknown"),
                        "reason": gate.get("reason"),
                    }
                )
                for dimension in dimensions_for_gate(gate_name):
                    if dimension not in item["dimensions"]:
                        item["dimensions"].append(dimension)
                reason = gate.get("reason")
                if reason and reason not in item["reasons"]:
                    item["reasons"].append(reason)
                evidence = gate.get("evidence")
                if evidence is not None:
                    item["evidence"].append({"gate": gate_name, "value": evidence})
    items: list[dict[str, Any]] = []
    for item in grouped.values():
        statuses = [str(gate.get("status") or "unknown") for gate in item.get("gates") or []]
        item["severity"] = triage_severity(statuses)
        item["gates"] = sorted(item["gates"], key=lambda gate: str(gate.get("name") or ""))
        item["dimensions"] = sorted(item["dimensions"])
        items.append(item)
    items = sorted(
        items,
        key=lambda item: (
            {"blocker": 0, "warning": 1, "needs_evidence": 2, "unknown": 3}.get(str(item.get("severity")), 9),
            str(item.get("category") or ""),
            str(item.get("session") or ""),
        ),
    )
    by_category: dict[str, dict[str, Any]] = {}
    by_severity: Counter[str] = Counter()
    sessions_by_category: dict[str, set[str]] = defaultdict(set)
    for item in items:
        category = str(item.get("category") or "other")
        severity = str(item.get("severity") or "unknown")
        by_severity[severity] += 1
        sessions_by_category[category].add(str(item.get("session") or ""))
    for category, sessions in sorted(sessions_by_category.items()):
        category_items = [item for item in items if item.get("category") == category]
        by_category[category] = {
            "title": TRIAGE_CATEGORY_INFO.get(category, TRIAGE_CATEGORY_INFO["other"])["title"],
            "item_count": len(category_items),
            "session_count": len(sessions),
            "sessions": sorted(sessions),
            "severities": dict(Counter(str(item.get("severity") or "unknown") for item in category_items)),
            "recommended_next": TRIAGE_CATEGORY_INFO.get(category, TRIAGE_CATEGORY_INFO["other"])["recommended_next"],
        }
    summary = {
        "total_items": len(items),
        "session_count": len({str(item.get("session") or "") for item in items}),
        "by_category": by_category,
        "by_severity": dict(by_severity),
        "real_gate_issue_count": len(real_gate_issue_keys),
        "categorized_gate_issue_count": len(categorized_gate_issue_keys),
        "uncategorized_gate_issue_count": len(real_gate_issue_keys - categorized_gate_issue_keys),
        "promotion_scope": "real_meeting",
        "new_real_live_collection_allowed": False,
        "note": "triage is derived only from non-passing real live parity gates; diagnostic sessions are excluded",
    }
    return summary, items


def recommended_next_commands(
    summary: dict[str, Any],
    gate_counts: dict[str, Counter[str]],
    gate_issues: list[dict[str, Any]],
) -> list[str]:
    live_quarantine_note = (
        "murmurmark status latest  # production meetings still use normal record/process; "
        "controlled Live Evidence uses live pilot"
    )
    if summary.get("live_quarantined") is True:
        if (
            summary.get("controlled_real_live_pilot_allowed") is True
            and safe_int(summary.get("coverage_target_passing_sessions_remaining")) > 0
            and not (summary.get("real_capture_safe_candidate_blocking_dimensions") or [])
        ):
            return [
                "murmurmark live pilot --controlled-real --skip-safety-gate --preflight-only",
                "murmurmark live pilot --controlled-real --skip-safety-gate",
                "murmurmark experiment status latest",
                "murmurmark experiment report latest",
                "murmurmark experiment compare latest --experiment live-shadow-v1",
                "murmurmark corpus live all --refresh",
                "jq '.capture_safe_candidate_scope, .coverage_target, .promotion_policy' sessions/_reports/live-pipeline/live_corpus_gates_report.json",
                live_quarantine_note,
            ]
        commands = [
            "jq '.live_next_unlock' sessions/_reports/live-pipeline/live_corpus_gates_report.json",
            "jq '.real_blocker_triage_summary' sessions/_reports/live-pipeline/live_corpus_gates_report.json",
            "less sessions/_reports/live-pipeline/live_corpus_gates_report.md",
            live_quarantine_note,
        ]
        if (
            summary.get("objective_next_focus") == "fix_live_local_recall_gap"
            or "local_recall" in (summary.get("real_capture_safe_candidate_blocking_dimensions") or [])
        ):
            commands.insert(1, ".venv/bin/python scripts/report-suppressed-mic-policy-lab.py")
            commands.insert(2, ".venv/bin/python scripts/report-live-target-me-enrollment-lab.py --method resemblyzer_dvector")
            commands.insert(3, ".venv/bin/python scripts/report-persistent-target-me-profile-lab.py --method resemblyzer_dvector")
            commands.insert(4, ".venv/bin/python scripts/report-suppressed-mic-composite-gate-lab.py")
        real_issues = [
            issue
            for issue in gate_issues
            if isinstance(issue, dict) and issue.get("evidence_scope") == "real_meeting"
        ]
        first_issue = real_issues[0] if real_issues else {}
        session = first_issue.get("session")
        if isinstance(session, str) and session:
            commands.insert(
                1,
                f"jq '.real_blocker_triage[] | select(.session == \"{session}\")' "
                "sessions/_reports/live-pipeline/live_corpus_gates_report.json",
            )
        session_path = first_issue.get("session_path")
        if isinstance(session_path, str) and session_path:
            commands.insert(2, f"murmurmark status {session_path}")
        comparison = first_issue.get("comparison")
        if isinstance(comparison, str) and comparison:
            commands.insert(3, f"jq '.parity_gates.gates[] | select(.status != \"passed\")' {comparison}")
        return commands

    target_live = max(
        1,
        safe_int(summary.get("real_live_sessions")) + safe_int(summary.get("coverage_target_live_sessions_remaining")),
    )
    target_passing = max(
        1,
        safe_int(summary.get("real_passing_compared_sessions"))
        + safe_int(summary.get("coverage_target_passing_sessions_remaining")),
    )
    coverage_command = (
        f"murmurmark corpus live all --min-live-sessions {target_live} --min-compared-sessions {target_live} "
        f"--min-meaningful-compared-sessions {target_live} --min-passing-compared-sessions {target_passing} "
        "--max-order-mismatches 0 --max-missing-me-sec 0 --max-remote-in-me-sec 0 "
        "--max-boundary-duplicates 0 --require-passing-gates --fail-on-promotion"
    )
    if safe_int(summary.get("real_live_sessions")) == 0:
        return [
            live_quarantine_note,
            coverage_command,
        ]
    if safe_int(summary.get("real_compared_sessions")) == 0:
        return [
            "murmurmark process latest",
            coverage_command,
        ]
    if safe_int(summary.get("real_meaningful_compared_sessions")) == 0:
        return [
            live_quarantine_note,
            coverage_command,
        ]
    if safe_int(summary.get("real_passing_compared_sessions")) == 0:
        commands = [
            "less sessions/_reports/live-pipeline/live_corpus_gates_report.md",
            live_quarantine_note,
            coverage_command,
        ]
        first_issue = gate_issues[0] if gate_issues else {}
        comparison = first_issue.get("comparison")
        session = first_issue.get("session")
        if isinstance(comparison, str) and comparison:
            commands.insert(1, f"jq '.parity_gates.gates[] | select(.status != \"passed\")' {comparison}")
        session_path = first_issue.get("session_path")
        if isinstance(session_path, str) and session_path:
            commands.insert(1, f"murmurmark status {session_path}")
        non_passing = {
            name: {status: count for status, count in counts.items() if status != "passed" and count > 0}
            for name, counts in gate_counts.items()
        }
        if non_passing:
            commands.insert(1, "jq '.real_gate_counts' sessions/_reports/live-pipeline/live_corpus_gates_report.json")
        if safe_float(summary.get("real_live_suspicious_batch_me_missing_seconds")) > 0:
            commands.insert(
                1,
                "jq '.sessions[] | select(.evidence_scope == \"real_meeting\" and .metrics.live_suspicious_batch_me_missing_seconds > 0)' sessions/_reports/live-pipeline/live_corpus_gates_report.json",
            )
        return commands
    if safe_int(summary.get("promotion_allowed_sessions")) > 0:
        return [
            "jq '.sessions[] | select(.promotion_allowed == true)' sessions/_reports/live-pipeline/live_corpus_gates_report.json",
            coverage_command,
        ]
    if summary.get("coverage_target_status") != "passed":
        return [
            live_quarantine_note,
            coverage_command,
        ]
    return [
        coverage_command,
        live_quarantine_note,
    ]


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    summary = report["summary"]
    live_next_unlock = report.get("live_next_unlock") if isinstance(report.get("live_next_unlock"), dict) else {}
    best_live = (
        live_next_unlock.get("best_live_implementable")
        if isinstance(live_next_unlock.get("best_live_implementable"), dict)
        else {}
    )
    top_actionability = (
        live_next_unlock.get("top_actionability")
        if isinstance(live_next_unlock.get("top_actionability"), dict)
        else {}
    )
    top_segmentability = (
        live_next_unlock.get("top_segmentability")
        if isinstance(live_next_unlock.get("top_segmentability"), dict)
        else {}
    )
    speaker_boundary = (
        report.get("live_speaker_boundary_evidence_lab")
        if isinstance(report.get("live_speaker_boundary_evidence_lab"), dict)
        else {}
    )
    online_boundary_design = (
        report.get("live_online_speaker_boundary_evidence_design_lab")
        if isinstance(report.get("live_online_speaker_boundary_evidence_design_lab"), dict)
        else {}
    )
    mixed_voice_coverage = (
        report.get("live_mixed_speaker_boundary_voice_coverage_lab")
        if isinstance(report.get("live_mixed_speaker_boundary_voice_coverage_lab"), dict)
        else {}
    )
    tight_voice_remote_guard = (
        report.get("live_tight_voice_remote_guard_lab")
        if isinstance(report.get("live_tight_voice_remote_guard_lab"), dict)
        else {}
    )
    lines = [
        "# Live Pipeline Corpus Gates",
        "",
        f"- sessions: {summary['sessions_total']}",
        f"- live sessions: {summary['live_sessions']}",
        f"- real live sessions: {summary.get('real_live_sessions', 0)}",
        f"- diagnostic live sessions: {summary.get('diagnostic_live_sessions', 0)}",
        f"- live comparison refresh: `{summary.get('live_comparison_refresh_status')}`",
        f"- live comparison refresh attempted sessions: {summary.get('live_comparison_refresh_attempted_sessions', 0)}",
        f"- live comparison refresh failed sessions: {summary.get('live_comparison_refresh_failed_sessions', 0)}",
        f"- live comparison refresh skipped sessions: {summary.get('live_comparison_refresh_skipped_sessions', 0)}",
        f"- compared sessions: {summary['compared_sessions']}",
        f"- real compared sessions: {summary.get('real_compared_sessions', 0)}",
        f"- meaningful compared sessions: {summary['meaningful_compared_sessions']}",
        f"- real meaningful compared sessions: {summary.get('real_meaningful_compared_sessions', 0)}",
        f"- passing compared sessions: {summary['passing_compared_sessions']}",
        f"- real passing compared sessions: {summary.get('real_passing_compared_sessions', 0)}",
        f"- capture-safe candidate sessions: {summary.get('real_capture_safe_candidate_sessions', 0)}",
        f"- capture-safe candidate passing sessions: {summary.get('real_capture_safe_candidate_passing_sessions', 0)}",
        f"- blocked sessions: {summary['blocked_sessions']}",
        f"- promotion allowed sessions: {summary['promotion_allowed_sessions']}",
        f"- target status: `{summary['target_status']}`",
        f"- promotion decision: `{summary['promotion_decision']}`",
        f"- live order mismatches: {summary.get('live_order_mismatch_count', 0)}",
        f"- real live order mismatches: {summary.get('real_live_order_mismatch_count', 0)}",
        f"- real live order mismatches by category: `{json.dumps(summary.get('real_live_order_mismatch_by_category', {}), ensure_ascii=False)}`",
        "- real live order mismatches by primary risk: "
        f"`{json.dumps(summary.get('real_live_order_mismatch_by_primary_risk', {}), ensure_ascii=False)}`",
        "- real live order mismatches by confidence: "
        f"`{json.dumps(summary.get('real_live_order_mismatch_by_confidence', {}), ensure_ascii=False)}`",
        "- real live batch-interval overlap order ambiguities: "
        f"{summary.get('real_live_batch_interval_overlap_order_ambiguity_count', 0)}",
        "- real live role-constrained order mismatches: "
        f"{summary.get('real_live_role_constrained_order_mismatch_count', 0)}",
        "- real live role-constrained order mismatches by category: "
        f"`{json.dumps(summary.get('real_live_role_constrained_order_mismatch_by_category', {}), ensure_ascii=False)}`",
        "- real live role-constrained order mismatches by confidence: "
        f"`{json.dumps(summary.get('real_live_role_constrained_order_mismatch_by_confidence', {}), ensure_ascii=False)}`",
        "- real live role-constrained batch-interval overlap order ambiguities: "
        f"{summary.get('real_live_role_constrained_batch_interval_overlap_order_ambiguity_count', 0)}",
        "- real live contentful role-constrained order mismatches: "
        f"{summary.get('real_live_contentful_role_constrained_order_mismatch_count', 0)}",
        "- real live contentful role-constrained order mismatches by category: "
        f"`{json.dumps(summary.get('real_live_contentful_role_constrained_order_mismatch_by_category', {}), ensure_ascii=False)}`",
        "- real live contentful role-constrained order mismatches by confidence: "
        f"`{json.dumps(summary.get('real_live_contentful_role_constrained_order_mismatch_by_confidence', {}), ensure_ascii=False)}`",
        "- real live contentful role-constrained order mismatches by ambiguity: "
        f"`{json.dumps(summary.get('real_live_contentful_role_constrained_order_mismatch_by_ambiguity', {}), ensure_ascii=False)}`",
        "- real live contentful role-constrained batch-interval overlap order ambiguities: "
        f"{summary.get('real_live_contentful_role_constrained_batch_interval_overlap_order_ambiguity_count', 0)}",
        "- real live unambiguous contentful role-constrained order mismatches: "
        f"{summary.get('real_live_unambiguous_contentful_role_constrained_order_mismatch_count', 0)}",
        f"- live missing Me seconds: {summary.get('live_missing_me_seconds', 0.0)}",
        f"- real live missing Me seconds: {summary.get('real_live_missing_me_seconds', 0.0)}",
        "- real live missing Me visible in suppressed mic seconds: "
        f"{summary.get('real_live_missing_me_visible_in_suppressed_mic_seconds', 0.0)}",
        "- real live missing Me not visible in suppressed mic seconds: "
        f"{summary.get('real_live_missing_me_not_visible_in_suppressed_mic_seconds', 0.0)}",
        f"- real live suppressed mic turns: {summary.get('real_live_suppressed_mic_turn_count', 0)}",
        "- real live segment-gate candidate chunks: "
        f"{summary.get('real_live_segment_role_gate_candidate_chunk_count', 0)}",
        "- real live segment-gate candidate kept segments: "
        f"{summary.get('real_live_segment_role_gate_candidate_kept_segment_count', 0)}",
        "- real live rescue shadow candidates: "
        f"{summary.get('real_live_rescue_shadow_candidate_chunk_count', 0)} chunks / "
        f"{summary.get('real_live_rescue_shadow_candidate_segment_count', 0)} segments, "
        f"missing-Me recovered {summary.get('real_live_rescue_shadow_missing_me_recovered_seconds', 0.0)} sec, "
        f"missing-Me after {summary.get('real_live_rescue_shadow_missing_me_seconds_after', 0.0)} sec, "
        f"remote-risk {summary.get('real_live_rescue_shadow_suspected_remote_leak_in_me_seconds', 0.0)} sec, "
        f"order mismatches {summary.get('real_live_rescue_shadow_order_mismatch_count', 0)}",
        "- real live rescue shadow order mismatches by category: "
        f"`{json.dumps(summary.get('real_live_rescue_shadow_order_mismatch_by_category', {}), ensure_ascii=False)}`",
        "- real live rescue shadow order mismatches by primary risk: "
        f"`{json.dumps(summary.get('real_live_rescue_shadow_order_mismatch_by_primary_risk', {}), ensure_ascii=False)}`",
        "- real live rescue shadow order mismatches by confidence: "
        f"`{json.dumps(summary.get('real_live_rescue_shadow_order_mismatch_by_confidence', {}), ensure_ascii=False)}`",
        "- real live rescue shadow role-constrained order mismatches: "
        f"{summary.get('real_live_rescue_shadow_role_constrained_order_mismatch_count', 0)}",
        "- real live rescue shadow role-constrained order mismatches by category: "
        f"`{json.dumps(summary.get('real_live_rescue_shadow_role_constrained_order_mismatch_by_category', {}), ensure_ascii=False)}`",
        "- real live rescue shadow role-constrained order mismatches by confidence: "
        f"`{json.dumps(summary.get('real_live_rescue_shadow_role_constrained_order_mismatch_by_confidence', {}), ensure_ascii=False)}`",
        "- real live rescue shadow contentful role-constrained order mismatches: "
        f"{summary.get('real_live_rescue_shadow_contentful_role_constrained_order_mismatch_count', 0)}",
        "- real live rescue shadow contentful role-constrained order mismatches by category: "
        f"`{json.dumps(summary.get('real_live_rescue_shadow_contentful_role_constrained_order_mismatch_by_category', {}), ensure_ascii=False)}`",
        "- real live rescue shadow contentful role-constrained order mismatches by confidence: "
        f"`{json.dumps(summary.get('real_live_rescue_shadow_contentful_role_constrained_order_mismatch_by_confidence', {}), ensure_ascii=False)}`",
        "- real live rescue shadow contentful role-constrained order mismatches by ambiguity: "
        f"`{json.dumps(summary.get('real_live_rescue_shadow_contentful_role_constrained_order_mismatch_by_ambiguity', {}), ensure_ascii=False)}`",
        "- real live rescue shadow unambiguous contentful role-constrained order mismatches: "
        f"{summary.get('real_live_rescue_shadow_unambiguous_contentful_role_constrained_order_mismatch_count', 0)}",
        "- real live suppressed mic ASR segments: "
        f"{summary.get('real_live_suppressed_mic_asr_segment_count', 0)} / "
        f"{summary.get('real_live_suppressed_mic_asr_segment_seconds', 0.0)} sec",
        "- real live suppressed mic ASR Me-dominant segments: "
        f"{summary.get('real_live_suppressed_mic_asr_me_dominant_segment_count', 0)} / "
        f"{summary.get('real_live_suppressed_mic_asr_me_dominant_segment_seconds', 0.0)} sec",
        "- real live suppressed mic ASR mixed segments: "
        f"{summary.get('real_live_suppressed_mic_asr_mixed_segment_count', 0)} / "
        f"{summary.get('real_live_suppressed_mic_asr_mixed_segment_seconds', 0.0)} sec",
        "- real live rescue current text gate: "
        f"local {summary.get('real_live_rescue_policy_current_text_segment_gate_local_seconds', 0.0)} sec, "
        f"remote-risk {summary.get('real_live_rescue_policy_current_text_segment_gate_remote_risk_seconds', 0.0)} sec, "
        f"precision {summary.get('real_live_rescue_policy_current_text_segment_gate_precision_proxy')}",
        "- real live rescue strict text unique v1: "
        f"local {summary.get('real_live_rescue_policy_strict_text_unique_v1_local_seconds', 0.0)} sec, "
        f"remote-risk {summary.get('real_live_rescue_policy_strict_text_unique_v1_remote_risk_seconds', 0.0)} sec, "
        f"precision {summary.get('real_live_rescue_policy_strict_text_unique_v1_precision_proxy')}",
        "- real live rescue remote-silent text v1: "
        f"local {summary.get('real_live_rescue_policy_remote_silent_text_v1_local_seconds', 0.0)} sec, "
        f"remote-risk {summary.get('real_live_rescue_policy_remote_silent_text_v1_remote_risk_seconds', 0.0)} sec, "
        f"precision {summary.get('real_live_rescue_policy_remote_silent_text_v1_precision_proxy')}",
        "- real live rescue audio remote-quiet v1: "
        f"local {summary.get('real_live_rescue_policy_audio_remote_quiet_v1_local_seconds', 0.0)} sec, "
        f"remote-risk {summary.get('real_live_rescue_policy_audio_remote_quiet_v1_remote_risk_seconds', 0.0)} sec, "
        f"precision {summary.get('real_live_rescue_policy_audio_remote_quiet_v1_precision_proxy')}",
        "- real live rescue audio mic-dominant v1: "
        f"local {summary.get('real_live_rescue_policy_audio_mic_dominant_v1_local_seconds', 0.0)} sec, "
        f"remote-risk {summary.get('real_live_rescue_policy_audio_mic_dominant_v1_remote_risk_seconds', 0.0)} sec, "
        f"precision {summary.get('real_live_rescue_policy_audio_mic_dominant_v1_precision_proxy')}",
        "- real live rescue audio low-coherence v1: "
        f"local {summary.get('real_live_rescue_policy_audio_low_coherence_v1_local_seconds', 0.0)} sec, "
        f"remote-risk {summary.get('real_live_rescue_policy_audio_low_coherence_v1_remote_risk_seconds', 0.0)} sec, "
        f"precision {summary.get('real_live_rescue_policy_audio_low_coherence_v1_precision_proxy')}",
        "- real live rescue audio safe union v1: "
        f"local {summary.get('real_live_rescue_policy_audio_safe_union_v1_local_seconds', 0.0)} sec, "
        f"remote-risk {summary.get('real_live_rescue_policy_audio_safe_union_v1_remote_risk_seconds', 0.0)} sec, "
        f"precision {summary.get('real_live_rescue_policy_audio_safe_union_v1_precision_proxy')}, "
        f"missing-Me recovered {summary.get('real_live_rescue_policy_audio_safe_union_v1_missing_me_recovered_seconds', 0.0)} sec",
        "- real live Target-Me local evidence: "
        f"status `{summary.get('real_live_target_me_status')}`, "
        f"sessions {summary.get('real_live_target_me_sessions_with_audit', 0)}, "
        f"possible/confirmed local {summary.get('real_live_target_me_possible_or_confirmed_local_seconds', 0.0)} sec, "
        f"remote-risk {summary.get('real_live_target_me_remote_risk_seconds', 0.0)} sec, "
        f"recommended policy `{summary.get('real_live_target_me_recommended_policy')}`, "
        "missing-Me recovered "
        f"{summary.get('real_live_target_me_recommended_policy_missing_me_recovered_seconds', 0.0)} sec",
        "- real live rescue oracle local ceiling: "
        f"local {summary.get('real_live_rescue_policy_batch_oracle_local_ceiling_local_seconds', 0.0)} sec, "
        f"recall {summary.get('real_live_rescue_policy_batch_oracle_local_ceiling_local_recall_proxy')}",
        f"- real live missing Me examples: {summary.get('live_local_recall_gap_example_count', 0)}",
        "- capture-safe candidate missing Me examples: "
        f"{summary.get('capture_safe_candidate_local_recall_gap_example_count', 0)}",
        "- capture-safe evaluable missing Me examples: "
        f"{summary.get('capture_safe_evaluable_local_recall_gap_example_count', 0)}",
        f"- live suspicious batch-Me missing seconds: {summary.get('live_suspicious_batch_me_missing_seconds', 0.0)}",
        f"- real live suspicious batch-Me missing seconds: {summary.get('real_live_suspicious_batch_me_missing_seconds', 0.0)}",
        f"- live suspected remote-in-Me seconds: {summary.get('live_suspected_remote_leak_in_me_seconds', 0.0)}",
        f"- real live suspected remote-in-Me seconds: {summary.get('real_live_suspected_remote_leak_in_me_seconds', 0.0)}",
        f"- adjacent duplicate chunks: {summary.get('adjacent_duplicate_chunk_count', 0)}",
        f"- real adjacent duplicate chunks: {summary.get('real_adjacent_duplicate_chunk_count', 0)}",
        f"- live boundary-gate issues: {summary.get('live_boundary_gate_issue_count', 0)}",
        f"- real live boundary-gate issues: {summary.get('real_live_boundary_gate_issue_count', 0)}",
        f"- live boundary-gate suppressed chunks: {summary.get('live_boundary_gate_suppressed_count', 0)}",
        f"- real live boundary-gate suppressed chunks: {summary.get('real_live_boundary_gate_suppressed_count', 0)}",
        f"- live boundary-gate resolved suppressed chunks: {summary.get('live_boundary_gate_resolved_suppressed_count', 0)}",
        f"- real live boundary-gate resolved suppressed chunks: {summary.get('real_live_boundary_gate_resolved_suppressed_count', 0)}",
        f"- live boundary-gate unresolved suppressed chunks: {summary.get('live_boundary_gate_unresolved_suppressed_count', 0)}",
        f"- real live boundary-gate unresolved suppressed chunks: {summary.get('real_live_boundary_gate_unresolved_suppressed_count', 0)}",
        f"- strict coverage: `{summary.get('strict_coverage_status')}`",
        f"- coverage target: `{summary.get('coverage_target_status')}`",
        f"- coverage target live remaining: {summary.get('coverage_target_live_sessions_remaining', 0)}",
        f"- coverage target passing remaining: {summary.get('coverage_target_passing_sessions_remaining', 0)}",
        f"- coverage path: `{summary.get('coverage_path_status')}`",
        "- coverage path historical non-candidate sessions: "
        f"{summary.get('coverage_path_historical_non_candidate_sessions', 0)}",
        f"- live quarantined: `{summary.get('live_quarantined')}`",
        f"- live evidence mode: `{summary.get('live_evidence_mode')}`",
        f"- new real live collection allowed: `{summary.get('new_real_live_collection_allowed')}`",
        f"- controlled real live pilot allowed: `{summary.get('controlled_real_live_pilot_allowed')}`",
        f"- promotion blocking dimensions: {', '.join(summary.get('promotion_blocking_dimensions') or []) or 'none'}",
        "",
        "## Next Unlock",
        "",
        "This block is diagnostic. It names the next unblocker for live parity, but does not allow "
        "live promotion.",
        "",
        f"- status: `{live_next_unlock.get('status')}`",
        f"- batch authoritative: `{live_next_unlock.get('batch_authoritative')}`",
        f"- additional recordings required for current blocker: "
        f"`{live_next_unlock.get('additional_recordings_required_for_current_blocker')}`",
        f"- best live-implementable profile: `{best_live.get('policy')}`",
        f"- best live-implementable missing-Me: {best_live.get('missing_me_seconds')} sec",
        f"- best live-implementable remote leak: {best_live.get('remote_leak_seconds')} sec",
        f"- best live-implementable contentful order mismatches: "
        f"{best_live.get('contentful_order_mismatch_count')}",
        f"- top actionability: `{top_actionability.get('label')}` / "
        f"{top_actionability.get('seconds')} sec",
        f"- top segmentability: `{top_segmentability.get('label')}` / "
        f"{top_segmentability.get('seconds')} sec",
        "",
        "Next actions:",
    ]
    next_actions = (
        live_next_unlock.get("next_actions")
        if isinstance(live_next_unlock.get("next_actions"), list)
        else []
    )
    if next_actions:
        for action in next_actions:
            if not isinstance(action, dict):
                continue
            lines.append(
                f"- `{action.get('id')}`: priority {action.get('priority')}, "
                f"scope {action.get('scope_seconds')} sec; {action.get('why')}"
            )
    else:
        lines.append("- none")
    blocked_buckets = (
        live_next_unlock.get("blocked_buckets")
        if isinstance(live_next_unlock.get("blocked_buckets"), list)
        else []
    )
    if blocked_buckets:
        lines += ["", "Blocked buckets:"]
        for bucket in blocked_buckets:
            if not isinstance(bucket, dict):
                continue
            lines.append(
                f"- `{bucket.get('id')}`: {bucket.get('seconds')} sec; {bucket.get('reason')}"
            )
    if speaker_boundary:
        lines += [
            "",
            "## Speaker Boundary Evidence Lab",
            "",
            "Diagnostic only. It classifies the current remaining gap into future shadow-probe "
            "candidates and rows that must stay blocked.",
            "",
            f"- status: `{speaker_boundary.get('status')}`",
            f"- remaining rows: {speaker_boundary.get('row_count')} / {speaker_boundary.get('seconds')} sec",
            f"- shadow-probe candidates: {speaker_boundary.get('shadow_probe_count')} / "
            f"{speaker_boundary.get('shadow_probe_seconds')} sec",
            f"- publication-ready seconds: {speaker_boundary.get('publication_ready_seconds')}",
            f"- blocked: {speaker_boundary.get('blocked_count')} / {speaker_boundary.get('blocked_seconds')} sec",
            "",
            "By classification:",
        ]
        by_classification = (
            speaker_boundary.get("by_classification")
            if isinstance(speaker_boundary.get("by_classification"), dict)
            else {}
        )
        for label, payload in by_classification.items():
            if not isinstance(payload, dict):
                continue
            lines.append(f"- `{label}`: {payload.get('count')} / {payload.get('seconds')} sec")
    if online_boundary_design:
        top_unit = (
            online_boundary_design.get("top_design_unit")
            if isinstance(online_boundary_design.get("top_design_unit"), dict)
            else {}
        )
        lines += [
            "",
            "## Online Speaker Boundary Evidence Design Lab",
            "",
            "Diagnostic only. It decomposes the same remaining mixed/speaker gap into implementation "
            "units, so the next live-parity step is tied to a concrete evidence mechanism instead of "
            "another broad threshold change.",
            "",
            f"- status: `{online_boundary_design.get('status')}`",
            f"- actionable rows: {online_boundary_design.get('actionable_count')} / "
            f"{online_boundary_design.get('actionable_seconds')} sec",
            f"- potential publish seconds after new evidence: "
            f"{online_boundary_design.get('potential_publish_seconds')}",
            f"- publication-ready seconds now: {online_boundary_design.get('publication_ready_seconds')}",
        ]
        if top_unit:
            lines.append(
                f"- top design unit: `{top_unit.get('id')}` / {top_unit.get('seconds')} sec "
                f"(potential {top_unit.get('potential_publish_seconds')} sec)"
            )
        lines += [
            "",
            "| Design unit | Rows | Seconds | Potential publish sec |",
            "| --- | ---: | ---: | ---: |",
        ]
        design_units = (
            online_boundary_design.get("by_design_unit")
            if isinstance(online_boundary_design.get("by_design_unit"), dict)
            else {}
        )
        for label, payload in design_units.items():
            if not isinstance(payload, dict):
                continue
            lines.append(
                f"| `{label}` | {payload.get('count')} "
                f"| {safe_float(payload.get('seconds')):.2f} "
                f"| {safe_float(payload.get('potential_publish_seconds')):.2f} |"
            )
    if mixed_voice_coverage:
        recommended_next = (
            mixed_voice_coverage.get("recommended_next")
            if isinstance(mixed_voice_coverage.get("recommended_next"), dict)
            else {}
        )
        lines += [
            "",
            "## Mixed Speaker Boundary Voice Coverage Lab",
            "",
            "Diagnostic only. It checks whether the remaining mixed/speaker blocker is already covered "
            "by Target-Me voice rows. It does not publish live `Me` text.",
            "",
            f"- status: `{mixed_voice_coverage.get('status')}`",
            f"- rows: {mixed_voice_coverage.get('row_count')} / {mixed_voice_coverage.get('seconds')} sec",
            f"- voice-overlap seconds: {mixed_voice_coverage.get('voice_overlap_seconds')}",
            "- publication candidate seconds now: "
            f"{mixed_voice_coverage.get('publication_candidate_seconds')}",
            "- missing Target-Me audit seconds: "
            f"{mixed_voice_coverage.get('no_target_me_audit_seconds')}",
            "- no-overlap Target-Me seconds: "
            f"{mixed_voice_coverage.get('no_voice_overlap_seconds')}",
            "- Target-Me enrollment-not-ready seconds: "
            f"{mixed_voice_coverage.get('target_me_enrollment_not_ready_seconds')}",
            "- weak/ambiguous voice seconds: "
            f"{mixed_voice_coverage.get('weak_or_ambiguous_seconds')}",
            f"- recommended next: `{recommended_next.get('id')}`",
            "",
            "| Voice coverage class | Rows | Seconds | Voice overlap sec |",
            "| --- | ---: | ---: | ---: |",
        ]
        by_classification = (
            mixed_voice_coverage.get("by_classification")
            if isinstance(mixed_voice_coverage.get("by_classification"), dict)
            else {}
        )
        for label, payload in by_classification.items():
            if not isinstance(payload, dict):
                continue
            lines.append(
                f"| `{label}` | {payload.get('count')} "
                f"| {safe_float(payload.get('seconds')):.2f} "
                f"| {safe_float(payload.get('voice_overlap_seconds')):.2f} |"
            )
    if tight_voice_remote_guard:
        recommended_next = (
            tight_voice_remote_guard.get("recommended_next")
            if isinstance(tight_voice_remote_guard.get("recommended_next"), dict)
            else {}
        )
        top_blocker = (
            tight_voice_remote_guard.get("top_blocker")
            if isinstance(tight_voice_remote_guard.get("top_blocker"), dict)
            else {}
        )
        lines += [
            "",
            "## Tight Voice Remote Guard Lab",
            "",
            "Diagnostic only. It applies stricter Target-Me-vs-remote thresholds to weak mixed rows. "
            "It does not publish live `Me` text.",
            "",
            f"- status: `{tight_voice_remote_guard.get('status')}`",
            f"- rows: {tight_voice_remote_guard.get('row_count')} / {tight_voice_remote_guard.get('seconds')} sec",
            f"- candidate seconds: {tight_voice_remote_guard.get('candidate_seconds')}",
            f"- blocked seconds: {tight_voice_remote_guard.get('blocked_seconds')}",
            f"- top blocker: `{top_blocker.get('label')}` / {top_blocker.get('seconds')} sec",
            f"- recommended next: `{recommended_next.get('id')}`",
            "",
            "| Primary blocker | Rows | Seconds |",
            "| --- | ---: | ---: |",
        ]
        by_primary_blocker = (
            tight_voice_remote_guard.get("by_primary_blocker")
            if isinstance(tight_voice_remote_guard.get("by_primary_blocker"), dict)
            else {}
        )
        for label, payload in by_primary_blocker.items():
            if not isinstance(payload, dict):
                continue
            lines.append(
                f"| `{label}` | {payload.get('count')} "
                f"| {safe_float(payload.get('seconds')):.2f} |"
            )
    lines += [
        "",
        "## Promotion Policy",
        "",
        "Batch transcript remains authoritative. Live promotion is blocked while the live branch is "
        "quarantined and until every required parity dimension passes on enough meaningful real "
        "comparisons.",
        "",
        f"- quarantine reason: {summary.get('live_quarantine_reason')}",
        "",
        "## Capture Regression Proof",
        "",
    ]
    capture_regression_check = (
        report.get("capture_regression_check")
        if isinstance(report.get("capture_regression_check"), dict)
        else {}
    )
    if capture_regression_check:
        lines += [
            f"- report present: `{capture_regression_check.get('present')}`",
            f"- status: `{capture_regression_check.get('status')}`",
            f"- mode: `{capture_regression_check.get('mode')}`",
            f"- capture-safe proof: `{capture_regression_check.get('capture_safe_proof_status')}`",
            f"- path: `{capture_regression_check.get('path')}`",
            "",
        ]
    else:
        lines += [
            "- report present: `false`",
            "- capture-safe proof: `missing`",
            "",
        ]
    refresh = report.get("live_comparison_refresh") if isinstance(report.get("live_comparison_refresh"), dict) else {}
    if refresh:
        lines += [
            "## Live Comparison Refresh",
            "",
            "`--refresh` reruns `compare-live-batch.py` from existing live and batch artifacts before "
            "aggregating this report. It does not touch raw capture or the authoritative batch transcript.",
            "",
            f"- requested: `{refresh.get('requested')}`",
            f"- status: `{refresh.get('status')}`",
            f"- attempted sessions: {refresh.get('attempted_sessions', 0)}",
            f"- failed sessions: {refresh.get('failed_sessions', 0)}",
            f"- skipped sessions: {refresh.get('skipped_sessions', 0)}",
            "",
        ]
        failed_refresh = [
            row for row in refresh.get("results") or []
            if isinstance(row, dict) and row.get("status") == "failed"
        ]
        if failed_refresh:
            lines += [
                "| Session | Reason | Return Code |",
                "| --- | --- | ---: |",
            ]
            for row in failed_refresh[:20]:
                lines.append(
                    f"| `{row.get('session')}` | `{row.get('reason')}` | {row.get('returncode')} |"
                )
            lines.append("")
    candidate_scope = (
        report.get("capture_safe_candidate_scope")
        if isinstance(report.get("capture_safe_candidate_scope"), dict)
        else {}
    )
    candidate_dimensions = (
        report.get("real_capture_safe_candidate_parity_dimensions")
        if isinstance(report.get("real_capture_safe_candidate_parity_dimensions"), dict)
        else {}
    )
    if candidate_scope:
        lines += [
            "## Capture-Safe Candidate Evidence",
            "",
            "This is a narrower diagnostic slice. It excludes historical live runs that failed capture safety "
            "or required artifact checks, but it does not allow live promotion.",
            "",
            f"- definition: {candidate_scope.get('definition')}",
            f"- sessions: {candidate_scope.get('sessions', 0)}",
            f"- passing sessions: {candidate_scope.get('passing_sessions', 0)}",
            f"- blocking dimensions: {', '.join(candidate_scope.get('blocking_dimensions') or []) or 'none'}",
            f"- new real live collection allowed: `{candidate_scope.get('new_real_live_collection_allowed')}`",
            f"- controlled real live pilot allowed: `{candidate_scope.get('controlled_real_live_pilot_allowed')}`",
            "",
        ]
        candidate_next = (
            candidate_scope.get("next_focus") if isinstance(candidate_scope.get("next_focus"), dict) else {}
        )
        if candidate_next:
            lines += [
                f"- next candidate focus: `{candidate_next.get('action_id')}` ({candidate_next.get('title')})",
                "",
            ]
        lines += [
            "| Dimension | Required | Counts | Issue sessions |",
            "| --- | --- | --- | --- |",
        ]
        for key, value in candidate_dimensions.items():
            if not isinstance(value, dict):
                continue
            counts = value.get("counts") if isinstance(value.get("counts"), dict) else {}
            counts_text = ", ".join(f"{status}: {count}" for status, count in sorted(counts.items())) or "-"
            issue_sessions = value.get("issue_sessions") if isinstance(value.get("issue_sessions"), list) else []
            lines.append(
                f"| `{key}` | `{value.get('promotion_required')}` | {counts_text} | {len(issue_sessions)} |"
            )
        lines.append("")
    policy_diagnostics = (
        report.get("live_local_recall_rescue_policy_diagnostics")
        if isinstance(report.get("live_local_recall_rescue_policy_diagnostics"), dict)
        else {}
    )
    if policy_diagnostics:
        lines += [
            "## Live Local Recall Rescue Policy Diagnostics",
            "",
            "This is diagnostic only. It compares suppressed-mic rescue policies against batch labels and "
            "does not promote live output.",
            "",
            "| Scope | Status | Best live policy | Recommended policy | Local sec | Remote-risk sec | Precision | Missing-Me recovered sec |",
            "| --- | --- | --- | --- | ---: | ---: | ---: | ---: |",
        ]
        for scope in ("real", "capture_safe_candidate", "capture_safe_evaluable"):
            payload = policy_diagnostics.get(scope) if isinstance(policy_diagnostics.get(scope), dict) else {}
            recommended = (
                payload.get("recommended_policy")
                if isinstance(payload.get("recommended_policy"), dict)
                else {}
            )
            best = (
                payload.get("best_policy")
                if isinstance(payload.get("best_policy"), dict)
                else recommended
            )
            precision = best.get("precision_proxy")
            precision_text = "-" if precision is None else f"{safe_float(precision):.3f}"
            lines.append(
                f"| `{scope}` | `{payload.get('status')}` | `{best.get('policy') or '-'}` "
                f"| `{recommended.get('policy') or '-'}` "
                f"| {safe_float(best.get('local_seconds')):.2f} "
                f"| {safe_float(best.get('remote_risk_seconds')):.2f} "
                f"| {precision_text} "
                f"| {safe_float(best.get('missing_me_recovered_seconds')):.2f} |"
            )
        candidate_diag = (
            policy_diagnostics.get("capture_safe_candidate")
            if isinstance(policy_diagnostics.get("capture_safe_candidate"), dict)
            else {}
        )
        candidate_policies = [
            row for row in candidate_diag.get("policies") or []
            if isinstance(row, dict) and safe_float(row.get("selected_seconds")) > 0.0
        ]
        if candidate_policies:
            lines += [
                "",
                "Capture-safe candidate policy details:",
                "",
                "| Policy | Local sec | Remote-risk sec | Precision | Recall | Recovered sec | Safe | Material |",
                "| --- | ---: | ---: | ---: | ---: | ---: | --- | --- |",
            ]
            for row in sorted(candidate_policies, key=lambda item: safe_float(item.get("local_seconds")), reverse=True):
                precision = row.get("precision_proxy")
                recall = row.get("local_recall_proxy")
                precision_text = "-" if precision is None else f"{safe_float(precision):.3f}"
                recall_text = "-" if recall is None else f"{safe_float(recall):.3f}"
                lines.append(
                    f"| `{row.get('policy')}` | {safe_float(row.get('local_seconds')):.2f} "
                    f"| {safe_float(row.get('remote_risk_seconds')):.2f} "
                    f"| {precision_text} | {recall_text} "
                    f"| {safe_float(row.get('missing_me_recovered_seconds')):.2f} "
                    f"| `{row.get('safe_candidate')}` "
                    f"| `{row.get('material_candidate')}` |"
                )
        lines.append("")
    rescue_lab = (
        report.get("live_local_recall_rescue_lab")
        if isinstance(report.get("live_local_recall_rescue_lab"), dict)
        else {}
    )
    candidate_rescue_lab = (
        report.get("capture_safe_candidate_live_local_recall_rescue_lab")
        if isinstance(report.get("capture_safe_candidate_live_local_recall_rescue_lab"), dict)
        else {}
    )
    evaluable_rescue_lab = (
        report.get("capture_safe_evaluable_live_local_recall_rescue_lab")
        if isinstance(report.get("capture_safe_evaluable_live_local_recall_rescue_lab"), dict)
        else {}
    )
    if rescue_lab:
        lines += [
            "## Live Local Recall Rescue Lab",
            "",
            "This is diagnostic only. It ranks suppressed mic ASR segments using batch labels to show "
            "where current candidate rescue policies help, where they would leak remote, and where stronger "
            "local-speaker evidence is needed.",
            "",
            "| Scope | Segments | Local sec | Live-policy local sec | Live-policy remote-risk sec | Speaker-evidence needed sec | Recommended next |",
            "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
        for scope, payload in (
            ("real", rescue_lab),
            ("capture_safe_candidate", candidate_rescue_lab),
            ("capture_safe_evaluable", evaluable_rescue_lab),
        ):
            if not isinstance(payload, dict):
                continue
            lines.append(
                f"| `{scope}` | {safe_int(payload.get('segment_count'))} "
                f"| {safe_float(payload.get('local_seconds')):.2f} "
                f"| {safe_float(payload.get('live_policy_local_seconds')):.2f} "
                f"| {safe_float(payload.get('live_policy_remote_risk_seconds')):.2f} "
                f"| {safe_float(payload.get('speaker_evidence_needed_seconds')):.2f} "
                f"| `{payload.get('recommended_next')}` |"
            )
        high_value = (
            ((rescue_lab.get("examples") or {}).get("high_value_unrescued") or [])
            if isinstance(rescue_lab.get("examples"), dict)
            else []
        )
        false_positive = (
            ((rescue_lab.get("examples") or {}).get("false_positive_remote_risk") or [])
            if isinstance(rescue_lab.get("examples"), dict)
            else []
        )
        if high_value:
            lines += [
                "",
                "Top high-value unrescued local segments:",
                "",
                "| Session | Time | Duration | Label | Unique tokens | Text |",
                "| --- | ---: | ---: | --- | ---: | --- |",
            ]
            for item in high_value[:8]:
                if not isinstance(item, dict):
                    continue
                text = str(item.get("text") or "").replace("|", "\\|")
                lines.append(
                    f"| `{item.get('session')}` | {safe_float(item.get('start')):.2f} "
                    f"| {safe_float(item.get('duration_sec')):.2f} "
                    f"| `{item.get('batch_role_label')}` "
                    f"| {safe_int(item.get('unique_token_count'))} "
                    f"| {text[:140]} |"
                )
        if false_positive:
            lines += [
                "",
                "Top remote-risk false positives:",
                "",
                "| Session | Time | Duration | Policies | Text |",
                "| --- | ---: | ---: | --- | --- |",
            ]
            for item in false_positive[:8]:
                if not isinstance(item, dict):
                    continue
                policies = ", ".join(str(policy) for policy in item.get("rescue_policy_candidates") or [])
                text = str(item.get("text") or "").replace("|", "\\|")
                lines.append(
                    f"| `{item.get('session')}` | {safe_float(item.get('start')):.2f} "
                    f"| {safe_float(item.get('duration_sec')):.2f} "
                    f"| `{policies}` | {text[:140]} |"
                )
        lines.append("")
    target_me_diag = (
        report.get("live_local_recall_target_me_diagnostics")
        if isinstance(report.get("live_local_recall_target_me_diagnostics"), dict)
        else {}
    )
    if target_me_diag:
        lines += [
            "## Live Local Recall Target-Me Diagnostics",
            "",
            "This is diagnostic only. It audits suppressed live mic segments with local Target-Me speaker "
            "evidence and estimates shadow rescue policies against batch labels. It does not publish live `Me`.",
            "",
            "| Scope | Status | Sessions | Local sec | Possible/confirmed sec | Remote-risk sec | Recommended policy | Next |",
            "| --- | --- | ---: | ---: | ---: | ---: | --- | --- |",
        ]
        for scope in ("real", "capture_safe_candidate", "capture_safe_evaluable"):
            payload = target_me_diag.get(scope) if isinstance(target_me_diag.get(scope), dict) else {}
            recommended = (
                payload.get("recommended_policy")
                if isinstance(payload.get("recommended_policy"), dict)
                else {}
            )
            lines.append(
                f"| `{scope}` | `{payload.get('status')}` "
                f"| {safe_int(payload.get('sessions_with_audit'))} "
                f"| {safe_float(payload.get('local_seconds')):.2f} "
                f"| {safe_float(payload.get('target_me_possible_or_confirmed_local_seconds')):.2f} "
                f"| {safe_float(payload.get('remote_risk_seconds')):.2f} "
                f"| `{recommended.get('policy') or '-'}` "
                f"| `{payload.get('recommended_next')}` |"
            )
        real_target_me = target_me_diag.get("real") if isinstance(target_me_diag.get("real"), dict) else {}
        real_policy_metrics = (
            real_target_me.get("target_me_rescue_policy_metrics")
            if isinstance(real_target_me.get("target_me_rescue_policy_metrics"), dict)
            else {}
        )
        if real_policy_metrics:
            lines += [
                "",
                "Real-scope Target-Me policy details:",
                "",
                "| Policy | Local sec | Remote-risk sec | Precision | Audited local recall | Missing-Me recovered sec |",
                "| --- | ---: | ---: | ---: | ---: | ---: |",
            ]
            for policy in TARGET_ME_RESCUE_POLICIES:
                metrics = (
                    real_policy_metrics.get(policy)
                    if isinstance(real_policy_metrics.get(policy), dict)
                    else {}
                )
                precision = metrics.get("precision_proxy")
                recall = metrics.get("audited_local_recall_proxy")
                precision_text = "-" if precision is None else f"{safe_float(precision):.3f}"
                recall_text = "-" if recall is None else f"{safe_float(recall):.3f}"
                lines.append(
                    f"| `{policy}` | {safe_float(metrics.get('local_seconds')):.2f} "
                    f"| {safe_float(metrics.get('remote_risk_seconds')):.2f} "
                    f"| {precision_text} | {recall_text} "
                    f"| {safe_float(metrics.get('missing_me_recovered_seconds')):.2f} |"
                )
        lines.append("")
    local_island_lab = (
        report.get("live_local_island_split_lab")
        if isinstance(report.get("live_local_island_split_lab"), dict)
        else {}
    )
    remaining_gap = (
        report.get("live_target_me_shadow_profile_best_live_implementable_remaining_gap")
        if isinstance(report.get("live_target_me_shadow_profile_best_live_implementable_remaining_gap"), dict)
        else {}
    )
    if local_island_lab:
        audio_anchor_lab = (
            report.get("live_local_island_audio_anchor_lab")
            if isinstance(report.get("live_local_island_audio_anchor_lab"), dict)
            else {}
        )
        live_only_candidate_lab = (
            report.get("live_only_local_island_candidate_lab")
            if isinstance(report.get("live_only_local_island_candidate_lab"), dict)
            else {}
        )
        live_only_strict_profile = (
            live_only_candidate_lab.get("stricter_profiles", {}).get("strict_zero_remote_risk_text_audio_v1")
            if isinstance(live_only_candidate_lab.get("stricter_profiles"), dict)
            else {}
        )
        live_only_retime_lab = (
            report.get("live_only_retime_boundary_candidate_lab")
            if isinstance(report.get("live_only_retime_boundary_candidate_lab"), dict)
            else {}
        )
        timing_gap = (
            report.get("live_local_island_timing_gap")
            if isinstance(report.get("live_local_island_timing_gap"), dict)
            else {}
        )
        lines += [
            "## Local Island Split Lab",
            "",
            "This is diagnostic only. It looks at the best live-implementable profile's remaining "
            "`mixed` missing-Me rows and estimates whether short local-looking islands would cover enough "
            "batch `Me` text to be worth a real split profile. It never authorizes live promotion.",
            "",
            f"- remaining-gap rows: {remaining_gap.get('item_count', 0)} / {remaining_gap.get('seconds', 0.0)} sec",
            "- mixed local-island candidates: "
            f"{local_island_lab.get('candidate_count', 0)} / "
            f"{safe_float(local_island_lab.get('candidate_batch_seconds')):.2f} batch sec / "
            f"{safe_float(local_island_lab.get('candidate_local_island_seconds')):.2f} island sec",
            "- token-recall accepted: "
            f"{local_island_lab.get('accepted_count', 0)} / "
            f"{safe_float(local_island_lab.get('accepted_batch_seconds')):.2f} batch sec / "
            f"{safe_float(local_island_lab.get('accepted_local_island_seconds')):.2f} island sec",
            "- local-island oracle profile: "
            f"missing-Me {summary.get('real_live_local_island_split_oracle_profile_missing_me_seconds')} sec, "
            "delta vs best live-implementable "
            f"{summary.get('real_live_local_island_split_oracle_profile_missing_me_delta_vs_best_live_implementable_seconds')} sec, "
            "remote leak "
            f"{summary.get('real_live_local_island_split_oracle_profile_remote_leak_seconds')} sec, "
            "contentful order mismatches "
            f"{summary.get('real_live_local_island_split_oracle_profile_contentful_order_mismatch_count')}",
            "- local-island retime oracle profile: "
            f"missing-Me {summary.get('real_live_local_island_retime_oracle_profile_missing_me_seconds')} sec, "
            "delta vs best live-implementable "
            f"{summary.get('real_live_local_island_retime_oracle_profile_missing_me_delta_vs_best_live_implementable_seconds')} sec, "
            "delta vs split oracle "
            f"{summary.get('real_live_local_island_retime_oracle_profile_delta_vs_split_oracle_seconds')} sec, "
            "remote leak "
            f"{summary.get('real_live_local_island_retime_oracle_profile_remote_leak_seconds')} sec, "
            "contentful order mismatches "
            f"{summary.get('real_live_local_island_retime_oracle_profile_contentful_order_mismatch_count')}",
            "- live audio anchors inside accepted rows: "
            f"{safe_float(audio_anchor_lab.get('audio_anchor_seconds')):.2f} sec / "
            f"{safe_int(audio_anchor_lab.get('audio_anchor_island_count'))} islands "
            f"({audio_anchor_lab.get('status')})",
            "- retime anchor lab: "
            f"{timing_gap.get('retime_anchor_local_island_seconds')} sec local islands across "
            f"{timing_gap.get('retime_anchor_span_seconds')} sec anchor span; "
            f"{timing_gap.get('retime_anchor_context_expansion_seconds')} sec context expansion needed, "
            f"max leading gap {timing_gap.get('retime_anchor_max_leading_gap_seconds')} sec",
            "- live-only local-island candidates: "
            f"{safe_float(live_only_candidate_lab.get('candidate_seconds')):.2f} sec selected, "
            f"{safe_float(live_only_candidate_lab.get('local_seconds')):.2f} sec local, "
            f"{safe_float(live_only_candidate_lab.get('remote_risk_seconds')):.2f} sec remote-risk, "
            f"precision proxy {live_only_candidate_lab.get('precision_proxy')}",
            "- strict live-only zero-risk candidates: "
            f"{safe_float(live_only_strict_profile.get('candidate_seconds')):.2f} sec selected, "
            f"{safe_float(live_only_strict_profile.get('local_seconds')):.2f} sec local, "
            f"{safe_float(live_only_strict_profile.get('remote_risk_seconds')):.2f} sec remote-risk, "
            f"precision proxy {live_only_strict_profile.get('precision_proxy')}",
            "- live-only retime boundary probe: "
            f"{live_only_retime_lab.get('recommended_profile') or '(none)'}; "
            f"{timing_gap.get('live_only_retime_boundary_recommended_missing_overlap_seconds')} sec missing-Me overlap, "
            f"{timing_gap.get('live_only_retime_boundary_recommended_remote_risk_seconds')} sec remote-risk, "
            f"{timing_gap.get('live_only_retime_boundary_recommended_candidate_seconds')} sec candidate span",
            "- live-only retime best recall probe: "
            f"{timing_gap.get('live_only_retime_boundary_best_missing_profile') or '(none)'}; "
            f"{timing_gap.get('live_only_retime_boundary_best_missing_overlap_seconds')} sec missing-Me overlap, "
            f"{timing_gap.get('live_only_retime_boundary_best_missing_remote_risk_seconds')} sec remote-risk, "
            f"{timing_gap.get('live_only_retime_boundary_best_missing_candidate_seconds')} sec candidate span",
            "- live-only retime zero-remote evaluated ceiling: "
            f"{timing_gap.get('live_only_retime_boundary_best_zero_remote_evaluated_profile') or '(none)'}; "
            f"{timing_gap.get('live_only_retime_boundary_best_zero_remote_evaluated_missing_overlap_seconds')} sec missing-Me overlap, "
            f"{timing_gap.get('live_only_retime_boundary_best_zero_remote_evaluated_remote_risk_seconds')} sec remote-risk, "
            f"{timing_gap.get('live_only_retime_boundary_best_zero_remote_evaluated_candidate_seconds')} sec candidate span",
            "- strict live-only shadow profile: "
            f"added {timing_gap.get('strict_live_only_profile_added_turn_seconds')} sec, "
            f"missing-Me {timing_gap.get('strict_live_only_profile_missing_me_seconds')} sec, "
            f"remote leak {timing_gap.get('strict_live_only_profile_remote_leak_seconds')} sec, "
            "contentful order mismatches "
            f"{timing_gap.get('strict_live_only_profile_contentful_order_mismatch_count')}, "
            f"non-passing gates {timing_gap.get('strict_live_only_profile_non_passing_gate_count')}",
            "- combined strict+audio-safe shadow profile: "
            f"added {timing_gap.get('combined_strict_audio_safe_union_profile_added_turn_seconds')} sec, "
            f"missing-Me {timing_gap.get('combined_strict_audio_safe_union_profile_missing_me_seconds')} sec, "
            f"remote leak {timing_gap.get('combined_strict_audio_safe_union_profile_remote_leak_seconds')} sec, "
            "contentful order mismatches "
            f"{timing_gap.get('combined_strict_audio_safe_union_profile_contentful_order_mismatch_count')}, "
            f"non-passing gates {timing_gap.get('combined_strict_audio_safe_union_profile_non_passing_gate_count')}",
            "- remote-forbidden boundary classifier shadow profile: "
            f"added {timing_gap.get('remote_forbidden_boundary_classifier_profile_added_turn_seconds')} sec, "
            f"missing-Me {timing_gap.get('remote_forbidden_boundary_classifier_profile_missing_me_seconds')} sec, "
            f"remote leak {timing_gap.get('remote_forbidden_boundary_classifier_profile_remote_leak_seconds')} sec, "
            "contentful order mismatches "
            f"{timing_gap.get('remote_forbidden_boundary_classifier_profile_contentful_order_mismatch_count')}, "
            f"non-passing gates {timing_gap.get('remote_forbidden_boundary_classifier_profile_non_passing_gate_count')}, "
            f"rejected turns {timing_gap.get('remote_forbidden_boundary_classifier_profile_rejected_turn_count')}",
            "- strict shadow delta lab: "
            f"{timing_gap.get('strict_shadow_delta_incremental_turn_seconds')} sec incremental strict turns, "
            f"{timing_gap.get('strict_shadow_delta_closed_missing_me_seconds')} sec closed missing-Me, "
            f"net delta {timing_gap.get('strict_shadow_delta_net_missing_me_delta_seconds')} sec",
            "- timing gap: "
            f"{timing_gap.get('retime_gain_vs_split_oracle_seconds')} sec require batch timing; "
            "future live work needs online local-island timing anchors before publication",
            f"- recall threshold: {safe_float(local_island_lab.get('recall_threshold')):.2f}",
            f"- promotion allowed: `{local_island_lab.get('promotion_allowed')}`",
            "",
        ]
        examples = local_island_lab.get("examples") if isinstance(local_island_lab.get("examples"), list) else []
        if examples:
            lines += [
                "| Session | Batch ID | Accepted | Batch sec | Island sec | Recall | Island text |",
                "| --- | --- | --- | ---: | ---: | ---: | --- |",
            ]
            for item in examples[:8]:
                if not isinstance(item, dict):
                    continue
                text = str(item.get("local_island_text") or "").replace("|", "\\|")
                if len(text) > 120:
                    text = text[:117].rstrip() + "..."
                lines.append(
                    f"| `{item.get('session')}` | `{item.get('batch_id')}` "
                    f"| `{item.get('accepted')}` "
                    f"| {safe_float(item.get('duration_sec')):.2f} "
                    f"| {safe_float(item.get('local_island_seconds')):.2f} "
                    f"| {safe_float(item.get('token_recall_from_local_islands')):.3f} "
                    f"| {text} |"
                )
            lines.append("")
    candidate_gap_examples = (
        report.get("capture_safe_candidate_local_recall_gap_examples")
        if isinstance(report.get("capture_safe_candidate_local_recall_gap_examples"), dict)
        else {}
    )
    evaluable_gap_examples = (
        report.get("capture_safe_evaluable_local_recall_gap_examples")
        if isinstance(report.get("capture_safe_evaluable_local_recall_gap_examples"), dict)
        else {}
    )
    all_gap_examples = (
        report.get("live_local_recall_gap_examples")
        if isinstance(report.get("live_local_recall_gap_examples"), dict)
        else {}
    )
    if candidate_gap_examples or all_gap_examples:
        lines += [
            "## Live Local Recall Gap Examples",
            "",
            "These are batch `Me` utterances that were not visible as live `Me` turns. They are the "
            "main evidence for the current `fix_live_local_recall_gap` focus.",
            "",
            f"- real examples: {all_gap_examples.get('item_count', 0)} / {all_gap_examples.get('seconds', 0.0)} sec",
            "- capture-safe candidate examples: "
            f"{candidate_gap_examples.get('item_count', 0)} / {candidate_gap_examples.get('seconds', 0.0)} sec",
            "- capture-safe evaluable examples: "
            f"{evaluable_gap_examples.get('item_count', 0)} / {evaluable_gap_examples.get('seconds', 0.0)} sec",
            "",
        ]
        examples_source = evaluable_gap_examples if evaluable_gap_examples else candidate_gap_examples
        examples = examples_source.get("examples") if isinstance(examples_source.get("examples"), list) else []
        if examples:
            lines += [
                "| Session | Time | Duration | Suppressed Mic Recall | Batch ID | Text |",
                "| --- | ---: | ---: | ---: | --- | --- |",
            ]
            for item in examples[:12]:
                if not isinstance(item, dict):
                    continue
                text = str(item.get("text") or "").replace("|", "\\|")
                if len(text) > 110:
                    text = text[:107].rstrip() + "..."
                lines.append(
                    f"| `{item.get('session')}` | {safe_float(item.get('start')):.2f}-{safe_float(item.get('end')):.2f} "
                    f"| {safe_float(item.get('duration_sec')):.2f} "
                    f"| {safe_float(item.get('recall_in_suppressed_mic')):.2f} "
                    f"| `{item.get('batch_id')}` | {text} |"
                )
            lines.append("")
    lines += [
        "## Objective Audit",
        "",
    ]
    objective_audit = report.get("objective_audit") if isinstance(report.get("objective_audit"), dict) else {}
    if objective_audit:
        lines += [
            f"- objective: {objective_audit.get('objective')}",
            f"- overall status: `{objective_audit.get('overall_status')}`",
            f"- ready for live promotion: `{objective_audit.get('ready_for_live_promotion')}`",
            f"- batch authoritative: `{objective_audit.get('batch_authoritative')}`",
            f"- new real live collection allowed: `{objective_audit.get('new_real_live_collection_allowed')}`",
            f"- controlled real live pilot allowed: `{objective_audit.get('controlled_real_live_pilot_allowed')}`",
            f"- blocking dimensions: {', '.join(objective_audit.get('blocking_dimensions') or []) or 'none'}",
        "",
        ]
        next_focus = objective_audit.get("next_focus") if isinstance(objective_audit.get("next_focus"), dict) else {}
        if next_focus:
            lines += [
                "### Next Focus",
                "",
                f"- dimension: `{next_focus.get('dimension') or 'none'}`",
                f"- action: `{next_focus.get('action_id')}`",
                f"- title: {next_focus.get('title')}",
                f"- recommended next: {next_focus.get('recommended_next')}",
                "",
            ]
        candidate_scope = (
            objective_audit.get("capture_safe_candidate_scope")
            if isinstance(objective_audit.get("capture_safe_candidate_scope"), dict)
            else {}
        )
        if candidate_scope:
            lines += [
                "### Capture-Safe Candidate Scope",
                "",
                f"- sessions: {candidate_scope.get('sessions', 0)}",
                f"- passing sessions: {candidate_scope.get('passing_sessions', 0)}",
                f"- blocking dimensions: {', '.join(candidate_scope.get('blocking_dimensions') or []) or 'none'}",
                f"- controlled real live pilot allowed: `{candidate_scope.get('controlled_real_live_pilot_allowed')}`",
                "",
            ]
            candidate_next = (
                candidate_scope.get("next_focus") if isinstance(candidate_scope.get("next_focus"), dict) else {}
            )
            if candidate_next:
                lines += [
                    f"- next candidate focus: `{candidate_next.get('action_id')}` ({candidate_next.get('title')})",
                    "",
                ]
        lines += [
            "| Check | Status | Evidence |",
            "| --- | --- | --- |",
        ]
        for row in objective_audit.get("rows") or []:
            if not isinstance(row, dict):
                continue
            evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
            evidence_text = json.dumps(evidence, ensure_ascii=False, sort_keys=True)
            if len(evidence_text) > 240:
                evidence_text = evidence_text[:237] + "..."
            lines.append(f"| `{row.get('id')}` | `{row.get('status')}` | `{evidence_text}` |")
        lines.append("")
    lines += [
        "## Real Parity Dimensions",
        "",
        "Only `real_meeting` live sessions count toward promotion. Diagnostic and lab sessions remain evidence, "
        "but they do not satisfy real coverage.",
        "",
        "| Dimension | Required | Counts | Issue sessions |",
        "| --- | --- | --- | --- |",
    ]
    real_dimensions = report.get("real_parity_dimensions") if isinstance(report.get("real_parity_dimensions"), dict) else {}
    for key, value in real_dimensions.items():
        if not isinstance(value, dict):
            continue
        counts = value.get("counts") if isinstance(value.get("counts"), dict) else {}
        counts_text = ", ".join(f"{status}: {count}" for status, count in sorted(counts.items())) or "-"
        issue_sessions = value.get("issue_sessions") if isinstance(value.get("issue_sessions"), list) else []
        lines.append(
            f"| `{key}` | `{value.get('promotion_required')}` | {counts_text} | {len(issue_sessions)} |"
        )
    triage_summary = (
        report.get("real_blocker_triage_summary")
        if isinstance(report.get("real_blocker_triage_summary"), dict)
        else {}
    )
    triage_categories = (
        triage_summary.get("by_category") if isinstance(triage_summary.get("by_category"), dict) else {}
    )
    if triage_summary:
        lines += [
            "",
            "## Real Blocker Triage",
            "",
            "This section groups only real-meeting non-passing gates into actionable buckets. It does "
            "not suggest collecting new live meetings while live capture is quarantined.",
            "",
            f"- triage items: {triage_summary.get('total_items', 0)}",
            f"- affected sessions: {triage_summary.get('session_count', 0)}",
            f"- severities: "
            + (
                ", ".join(
                    f"{severity}: {count}"
                    for severity, count in sorted((triage_summary.get("by_severity") or {}).items())
                )
                or "-"
            ),
            "",
            "| Category | Items | Sessions | Severity | Next |",
            "| --- | ---: | --- | --- | --- |",
        ]
        for category, value in sorted(triage_categories.items()):
            if not isinstance(value, dict):
                continue
            sessions = value.get("sessions") if isinstance(value.get("sessions"), list) else []
            severities = value.get("severities") if isinstance(value.get("severities"), dict) else {}
            severity_text = ", ".join(f"{key}: {count}" for key, count in sorted(severities.items())) or "-"
            session_text = ", ".join(f"`{session}`" for session in sessions[:4])
            if len(sessions) > 4:
                session_text += f", +{len(sessions) - 4}"
            lines.append(
                f"| `{category}` | {value.get('item_count', 0)} | {session_text or '-'} | "
                f"{severity_text} | {value.get('recommended_next') or '-'} |"
            )
    lines += [
        "",
        "## All Parity Dimensions",
        "",
        "| Dimension | Required | Counts | Issue sessions |",
        "| --- | --- | --- | --- |",
    ]
    dimensions = report.get("parity_dimensions") if isinstance(report.get("parity_dimensions"), dict) else {}
    for key, value in dimensions.items():
        if not isinstance(value, dict):
            continue
        counts = value.get("counts") if isinstance(value.get("counts"), dict) else {}
        counts_text = ", ".join(f"{status}: {count}" for status, count in sorted(counts.items())) or "-"
        issue_sessions = value.get("issue_sessions") if isinstance(value.get("issue_sessions"), list) else []
        issue_text = str(len(issue_sessions))
        lines.append(
            f"| `{key}` | `{value.get('promotion_required')}` | {counts_text} | {issue_text} |"
        )
    lines += [
        "",
        "## Recommended Next",
        "",
    ]
    for command in report.get("next_commands") or []:
        lines.append(f"- `{command}`")
    issues = [issue for issue in report.get("gate_issues") or [] if isinstance(issue, dict)]
    if issues:
        lines += ["", "## Gate Issues", ""]
        for issue in issues:
            lines.append(
                f"- `{issue.get('session')}` gate `{issue.get('gate')}` is `{issue.get('status')}`: "
                f"{issue.get('reason') or '-'}"
            )
    lines += [
        "",
        "## Sessions",
        "",
    ]
    for row in report["sessions"]:
        if not row["live_present"]:
            continue
        final = row.get("final_reconcile") or {}
        metrics = row.get("metrics") or {}
        lines.append(
            f"- `{row['session']}`: comparison `{row.get('comparison_status')}`, "
            f"parity `{row.get('parity_status')}`, final `{final.get('status') or 'missing'}`, "
            f"speedup `{final.get('speedup_status') or 'unknown'}`, "
            f"meaningful `{row.get('meaningful_compared')}`, gates passed `{row.get('all_parity_gates_passed')}`, "
            f"order mismatches `{metrics.get('live_order_mismatch_count')}`, "
            f"missing Me sec `{metrics.get('live_missing_me_seconds')}`, "
            f"suspicious batch-Me sec `{metrics.get('live_suspicious_batch_me_missing_seconds')}`, "
            f"remote-in-Me sec `{metrics.get('live_suspected_remote_leak_in_me_seconds')}`, "
            f"boundary issues `{metrics.get('live_boundary_gate_issue_count')}`, "
            f"boundary suppressed `{metrics.get('live_boundary_gate_suppressed_count')}`, "
            f"boundary resolved `{metrics.get('live_boundary_gate_resolved_suppressed_count')}`, "
            f"boundary unresolved `{metrics.get('live_boundary_gate_unresolved_suppressed_count')}`"
        )
    if report.get("blockers"):
        lines += ["", "## Blockers", ""]
        for key, count in sorted(report["blockers"].items()):
            lines.append(f"- `{key}`: {count}")
    strict = report.get("strict_coverage") or {}
    if strict.get("requested"):
        lines += ["", "## Strict Coverage", ""]
        lines.append(f"- status: `{strict.get('status')}`")
        failures = strict.get("failures") if isinstance(strict, dict) else []
        for row in failures or []:
            lines.append(f"- `{row.get('id')}`: {row.get('message')} (value: `{row.get('value')}`, limit: `{row.get('limit')}`)")
    target = report.get("coverage_target") if isinstance(report.get("coverage_target"), dict) else {}
    if target:
        lines += ["", "## Coverage Target", ""]
        lines.append(f"- status: `{target.get('status')}`")
        lines.append(f"- target live sessions: `{target.get('target_live_sessions')}`")
        lines.append(f"- target meaningful comparisons: `{target.get('target_meaningful_compared_sessions')}`")
        lines.append(f"- target passing comparisons: `{target.get('target_passing_compared_sessions')}`")
        lines.append(f"- live sessions remaining: `{target.get('live_sessions_remaining')}`")
        lines.append(f"- passing comparisons remaining: `{target.get('passing_compared_sessions_remaining')}`")
    lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    args = parse_args()
    root = args.sessions_root
    sessions = resolve_targets(args)
    report = build_report(sessions, root, args)
    json_path = args.out_dir / "live_corpus_gates_report.json"
    md_path = args.out_dir / "live_corpus_gates_report.md"
    write_json(json_path, report)
    write_markdown(md_path, report)
    summary = report["summary"]
    print(f"live_corpus_gates: {json_path}")
    print(f"status: {summary['target_status']}")
    print(f"live_sessions: {summary['live_sessions']}/{summary['sessions_total']}")
    print(f"real_live_sessions: {summary.get('real_live_sessions', 0)}")
    print(f"diagnostic_live_sessions: {summary.get('diagnostic_live_sessions', 0)}")
    print(f"live_comparison_refresh: {summary.get('live_comparison_refresh_status')}")
    print(f"live_comparison_refresh_attempted_sessions: {summary.get('live_comparison_refresh_attempted_sessions', 0)}")
    print(f"live_comparison_refresh_failed_sessions: {summary.get('live_comparison_refresh_failed_sessions', 0)}")
    print(f"live_comparison_refresh_skipped_sessions: {summary.get('live_comparison_refresh_skipped_sessions', 0)}")
    print(f"real_meaningful_compared_sessions: {summary.get('real_meaningful_compared_sessions', 0)}")
    print(f"real_passing_compared_sessions: {summary.get('real_passing_compared_sessions', 0)}")
    print(f"real_capture_safe_candidate_sessions: {summary.get('real_capture_safe_candidate_sessions', 0)}")
    print(
        "real_capture_safe_candidate_passing_sessions: "
        f"{summary.get('real_capture_safe_candidate_passing_sessions', 0)}"
    )
    print(f"meaningful_compared_sessions: {summary['meaningful_compared_sessions']}")
    print(f"passing_compared_sessions: {summary['passing_compared_sessions']}")
    print(f"promotion_decision: {summary['promotion_decision']}")
    print(f"live_order_mismatch_count: {summary.get('live_order_mismatch_count', 0)}")
    print(f"real_live_order_mismatch_count: {summary.get('real_live_order_mismatch_count', 0)}")
    print(
        "real_live_order_mismatch_by_category: "
        f"{json.dumps(summary.get('real_live_order_mismatch_by_category', {}), ensure_ascii=False)}"
    )
    print(
        "real_live_order_mismatch_by_primary_risk: "
        f"{json.dumps(summary.get('real_live_order_mismatch_by_primary_risk', {}), ensure_ascii=False)}"
    )
    print(
        "real_live_order_mismatch_by_confidence: "
        f"{json.dumps(summary.get('real_live_order_mismatch_by_confidence', {}), ensure_ascii=False)}"
    )
    print(
        "real_live_batch_interval_overlap_order_ambiguity_count: "
        f"{summary.get('real_live_batch_interval_overlap_order_ambiguity_count', 0)}"
    )
    print(f"real_live_role_constrained_order_mismatch_count: {summary.get('real_live_role_constrained_order_mismatch_count', 0)}")
    print(
        "real_live_role_constrained_order_mismatch_by_category: "
        f"{json.dumps(summary.get('real_live_role_constrained_order_mismatch_by_category', {}), ensure_ascii=False)}"
    )
    print(
        "real_live_role_constrained_order_mismatch_by_confidence: "
        f"{json.dumps(summary.get('real_live_role_constrained_order_mismatch_by_confidence', {}), ensure_ascii=False)}"
    )
    print(
        "real_live_role_constrained_batch_interval_overlap_order_ambiguity_count: "
        f"{summary.get('real_live_role_constrained_batch_interval_overlap_order_ambiguity_count', 0)}"
    )
    print(
        "real_live_contentful_role_constrained_order_mismatch_count: "
        f"{summary.get('real_live_contentful_role_constrained_order_mismatch_count', 0)}"
    )
    print(
        "real_live_contentful_role_constrained_order_mismatch_by_category: "
        f"{json.dumps(summary.get('real_live_contentful_role_constrained_order_mismatch_by_category', {}), ensure_ascii=False)}"
    )
    print(
        "real_live_contentful_role_constrained_order_mismatch_by_confidence: "
        f"{json.dumps(summary.get('real_live_contentful_role_constrained_order_mismatch_by_confidence', {}), ensure_ascii=False)}"
    )
    print(
        "real_live_contentful_role_constrained_order_mismatch_by_ambiguity: "
        f"{json.dumps(summary.get('real_live_contentful_role_constrained_order_mismatch_by_ambiguity', {}), ensure_ascii=False)}"
    )
    print(
        "real_live_contentful_role_constrained_batch_interval_overlap_order_ambiguity_count: "
        f"{summary.get('real_live_contentful_role_constrained_batch_interval_overlap_order_ambiguity_count', 0)}"
    )
    print(f"real_live_order_risk_triage_item_count: {summary.get('real_live_order_risk_triage_item_count', 0)}")
    print(f"real_live_order_risk_triage_blocking_count: {summary.get('real_live_order_risk_triage_blocking_count', 0)}")
    print(f"real_live_order_risk_triage_advisory_count: {summary.get('real_live_order_risk_triage_advisory_count', 0)}")
    print(
        "real_live_order_risk_triage_boundary_retime_candidate_count: "
        f"{summary.get('real_live_order_risk_triage_boundary_retime_candidate_count', 0)}"
    )
    print(
        "real_live_order_risk_triage_likely_false_positive_count: "
        f"{summary.get('real_live_order_risk_triage_likely_false_positive_count', 0)}"
    )
    print(
        "real_live_boundary_order_retime_oracle_profile_contentful_order_mismatch_count: "
        f"{summary.get('real_live_boundary_order_retime_oracle_profile_contentful_order_mismatch_count', 0)}"
    )
    print(
        "real_live_boundary_order_retime_oracle_profile_retimed_turn_count: "
        f"{summary.get('real_live_boundary_order_retime_oracle_profile_retimed_turn_count', 0)}"
    )
    print(
        "real_live_boundary_order_retime_oracle_profile_retimed_trimmed_seconds: "
        f"{summary.get('real_live_boundary_order_retime_oracle_profile_retimed_trimmed_seconds', 0.0)}"
    )
    print(
        "real_live_boundary_order_retime_oracle_profile_missing_me_delta_vs_best_live_implementable_seconds: "
        f"{summary.get('real_live_boundary_order_retime_oracle_profile_missing_me_delta_vs_best_live_implementable_seconds')}"
    )
    print(
        "real_live_boundary_order_split_retime_oracle_profile_contentful_order_mismatch_count: "
        f"{summary.get('real_live_boundary_order_split_retime_oracle_profile_contentful_order_mismatch_count', 0)}"
    )
    print(
        "real_live_boundary_order_split_retime_oracle_profile_retimed_turn_count: "
        f"{summary.get('real_live_boundary_order_split_retime_oracle_profile_retimed_turn_count', 0)}"
    )
    print(
        "real_live_boundary_order_split_retime_oracle_profile_preserved_prefix_seconds: "
        f"{summary.get('real_live_boundary_order_split_retime_oracle_profile_preserved_prefix_seconds', 0.0)}"
    )
    print(
        "real_live_boundary_order_split_retime_oracle_profile_missing_me_delta_vs_best_live_implementable_seconds: "
        f"{summary.get('real_live_boundary_order_split_retime_oracle_profile_missing_me_delta_vs_best_live_implementable_seconds')}"
    )
    print(
        "real_live_unambiguous_contentful_role_constrained_order_mismatch_count: "
        f"{summary.get('real_live_unambiguous_contentful_role_constrained_order_mismatch_count', 0)}"
    )
    print(f"live_missing_me_seconds: {summary.get('live_missing_me_seconds', 0.0)}")
    print(f"real_live_missing_me_seconds: {summary.get('real_live_missing_me_seconds', 0.0)}")
    print(
        "real_live_segment_role_gate_candidate_chunk_count: "
        f"{summary.get('real_live_segment_role_gate_candidate_chunk_count', 0)}"
    )
    print(
        "real_live_segment_role_gate_candidate_kept_segment_count: "
        f"{summary.get('real_live_segment_role_gate_candidate_kept_segment_count', 0)}"
    )
    print(
        "real_live_rescue_shadow_candidate_chunk_count: "
        f"{summary.get('real_live_rescue_shadow_candidate_chunk_count', 0)}"
    )
    print(
        "real_live_rescue_shadow_candidate_segment_count: "
        f"{summary.get('real_live_rescue_shadow_candidate_segment_count', 0)}"
    )
    print(
        "real_live_rescue_shadow_order_mismatch_count: "
        f"{summary.get('real_live_rescue_shadow_order_mismatch_count', 0)}"
    )
    print(
        "real_live_rescue_shadow_order_mismatch_by_category: "
        f"{json.dumps(summary.get('real_live_rescue_shadow_order_mismatch_by_category', {}), ensure_ascii=False)}"
    )
    print(
        "real_live_rescue_shadow_order_mismatch_by_primary_risk: "
        f"{json.dumps(summary.get('real_live_rescue_shadow_order_mismatch_by_primary_risk', {}), ensure_ascii=False)}"
    )
    print(
        "real_live_rescue_shadow_order_mismatch_by_confidence: "
        f"{json.dumps(summary.get('real_live_rescue_shadow_order_mismatch_by_confidence', {}), ensure_ascii=False)}"
    )
    print(
        "real_live_rescue_shadow_role_constrained_order_mismatch_count: "
        f"{summary.get('real_live_rescue_shadow_role_constrained_order_mismatch_count', 0)}"
    )
    print(
        "real_live_rescue_shadow_role_constrained_order_mismatch_by_category: "
        f"{json.dumps(summary.get('real_live_rescue_shadow_role_constrained_order_mismatch_by_category', {}), ensure_ascii=False)}"
    )
    print(
        "real_live_rescue_shadow_role_constrained_order_mismatch_by_confidence: "
        f"{json.dumps(summary.get('real_live_rescue_shadow_role_constrained_order_mismatch_by_confidence', {}), ensure_ascii=False)}"
    )
    print(
        "real_live_rescue_shadow_contentful_role_constrained_order_mismatch_count: "
        f"{summary.get('real_live_rescue_shadow_contentful_role_constrained_order_mismatch_count', 0)}"
    )
    print(
        "real_live_rescue_shadow_contentful_role_constrained_order_mismatch_by_category: "
        f"{json.dumps(summary.get('real_live_rescue_shadow_contentful_role_constrained_order_mismatch_by_category', {}), ensure_ascii=False)}"
    )
    print(
        "real_live_rescue_shadow_contentful_role_constrained_order_mismatch_by_confidence: "
        f"{json.dumps(summary.get('real_live_rescue_shadow_contentful_role_constrained_order_mismatch_by_confidence', {}), ensure_ascii=False)}"
    )
    print(
        "real_live_rescue_shadow_contentful_role_constrained_order_mismatch_by_ambiguity: "
        f"{json.dumps(summary.get('real_live_rescue_shadow_contentful_role_constrained_order_mismatch_by_ambiguity', {}), ensure_ascii=False)}"
    )
    print(
        "real_live_rescue_shadow_unambiguous_contentful_role_constrained_order_mismatch_count: "
        f"{summary.get('real_live_rescue_shadow_unambiguous_contentful_role_constrained_order_mismatch_count', 0)}"
    )
    print(
        "real_live_rescue_shadow_suspected_remote_leak_in_me_seconds: "
        f"{summary.get('real_live_rescue_shadow_suspected_remote_leak_in_me_seconds', 0.0)}"
    )
    print(
        "real_live_rescue_shadow_missing_me_seconds_after: "
        f"{summary.get('real_live_rescue_shadow_missing_me_seconds_after', 0.0)}"
    )
    print(
        "real_live_rescue_shadow_missing_me_recovered_seconds: "
        f"{summary.get('real_live_rescue_shadow_missing_me_recovered_seconds', 0.0)}"
    )
    print(
        "real_live_suppressed_mic_asr_me_dominant_segment_count: "
        f"{summary.get('real_live_suppressed_mic_asr_me_dominant_segment_count', 0)}"
    )
    print(
        "real_live_suppressed_mic_asr_me_dominant_segment_seconds: "
        f"{summary.get('real_live_suppressed_mic_asr_me_dominant_segment_seconds', 0.0)}"
    )
    print(
        "real_live_suppressed_mic_asr_mixed_segment_count: "
        f"{summary.get('real_live_suppressed_mic_asr_mixed_segment_count', 0)}"
    )
    print(
        "real_live_suppressed_mic_asr_mixed_segment_seconds: "
        f"{summary.get('real_live_suppressed_mic_asr_mixed_segment_seconds', 0.0)}"
    )
    print(
        "real_live_rescue_policy_current_text_segment_gate_local_seconds: "
        f"{summary.get('real_live_rescue_policy_current_text_segment_gate_local_seconds', 0.0)}"
    )
    print(
        "real_live_rescue_policy_current_text_segment_gate_remote_risk_seconds: "
        f"{summary.get('real_live_rescue_policy_current_text_segment_gate_remote_risk_seconds', 0.0)}"
    )
    print(
        "real_live_rescue_policy_strict_text_unique_v1_local_seconds: "
        f"{summary.get('real_live_rescue_policy_strict_text_unique_v1_local_seconds', 0.0)}"
    )
    print(
        "real_live_rescue_policy_strict_text_unique_v1_remote_risk_seconds: "
        f"{summary.get('real_live_rescue_policy_strict_text_unique_v1_remote_risk_seconds', 0.0)}"
    )
    print(
        "real_live_rescue_policy_remote_silent_text_v1_local_seconds: "
        f"{summary.get('real_live_rescue_policy_remote_silent_text_v1_local_seconds', 0.0)}"
    )
    print(
        "real_live_rescue_policy_remote_silent_text_v1_remote_risk_seconds: "
        f"{summary.get('real_live_rescue_policy_remote_silent_text_v1_remote_risk_seconds', 0.0)}"
    )
    print(
        "real_live_rescue_policy_audio_remote_quiet_v1_local_seconds: "
        f"{summary.get('real_live_rescue_policy_audio_remote_quiet_v1_local_seconds', 0.0)}"
    )
    print(
        "real_live_rescue_policy_audio_remote_quiet_v1_remote_risk_seconds: "
        f"{summary.get('real_live_rescue_policy_audio_remote_quiet_v1_remote_risk_seconds', 0.0)}"
    )
    print(
        "real_live_rescue_policy_audio_mic_dominant_v1_local_seconds: "
        f"{summary.get('real_live_rescue_policy_audio_mic_dominant_v1_local_seconds', 0.0)}"
    )
    print(
        "real_live_rescue_policy_audio_mic_dominant_v1_remote_risk_seconds: "
        f"{summary.get('real_live_rescue_policy_audio_mic_dominant_v1_remote_risk_seconds', 0.0)}"
    )
    print(
        "real_live_rescue_policy_audio_low_coherence_v1_local_seconds: "
        f"{summary.get('real_live_rescue_policy_audio_low_coherence_v1_local_seconds', 0.0)}"
    )
    print(
        "real_live_rescue_policy_audio_low_coherence_v1_remote_risk_seconds: "
        f"{summary.get('real_live_rescue_policy_audio_low_coherence_v1_remote_risk_seconds', 0.0)}"
    )
    print(
        "real_live_rescue_policy_audio_safe_union_v1_local_seconds: "
        f"{summary.get('real_live_rescue_policy_audio_safe_union_v1_local_seconds', 0.0)}"
    )
    print(
        "real_live_rescue_policy_audio_safe_union_v1_remote_risk_seconds: "
        f"{summary.get('real_live_rescue_policy_audio_safe_union_v1_remote_risk_seconds', 0.0)}"
    )
    print(
        "real_live_rescue_policy_audio_safe_union_v1_missing_me_recovered_seconds: "
        f"{summary.get('real_live_rescue_policy_audio_safe_union_v1_missing_me_recovered_seconds', 0.0)}"
    )
    print(
        "real_live_rescue_policy_batch_oracle_local_ceiling_local_seconds: "
        f"{summary.get('real_live_rescue_policy_batch_oracle_local_ceiling_local_seconds', 0.0)}"
    )
    print(f"live_suspicious_batch_me_missing_seconds: {summary.get('live_suspicious_batch_me_missing_seconds', 0.0)}")
    print(
        "real_live_suspicious_batch_me_missing_seconds: "
        f"{summary.get('real_live_suspicious_batch_me_missing_seconds', 0.0)}"
    )
    print(f"live_suspected_remote_leak_in_me_seconds: {summary.get('live_suspected_remote_leak_in_me_seconds', 0.0)}")
    print(
        "real_live_suspected_remote_leak_in_me_seconds: "
        f"{summary.get('real_live_suspected_remote_leak_in_me_seconds', 0.0)}"
    )
    print(f"adjacent_duplicate_chunk_count: {summary.get('adjacent_duplicate_chunk_count', 0)}")
    print(f"real_adjacent_duplicate_chunk_count: {summary.get('real_adjacent_duplicate_chunk_count', 0)}")
    print(f"live_boundary_gate_issue_count: {summary.get('live_boundary_gate_issue_count', 0)}")
    print(f"real_live_boundary_gate_issue_count: {summary.get('real_live_boundary_gate_issue_count', 0)}")
    print(f"live_boundary_gate_suppressed_count: {summary.get('live_boundary_gate_suppressed_count', 0)}")
    print(f"real_live_boundary_gate_suppressed_count: {summary.get('real_live_boundary_gate_suppressed_count', 0)}")
    print(
        "real_live_boundary_gate_resolved_suppressed_count: "
        f"{summary.get('real_live_boundary_gate_resolved_suppressed_count', 0)}"
    )
    print(
        "real_live_boundary_gate_unresolved_suppressed_count: "
        f"{summary.get('real_live_boundary_gate_unresolved_suppressed_count', 0)}"
    )
    print(f"strict_coverage: {summary.get('strict_coverage_status')}")
    print(f"coverage_target: {summary.get('coverage_target_status')}")
    print(f"coverage_target_live_remaining: {summary.get('coverage_target_live_sessions_remaining', 0)}")
    print(f"coverage_target_passing_remaining: {summary.get('coverage_target_passing_sessions_remaining', 0)}")
    print(f"coverage_path: {summary.get('coverage_path_status')}")
    print(
        "coverage_path_historical_non_candidate_sessions: "
        f"{summary.get('coverage_path_historical_non_candidate_sessions', 0)}"
    )
    print(f"live_evidence_mode: {summary.get('live_evidence_mode')}")
    print(f"new_real_live_collection_allowed: {summary.get('new_real_live_collection_allowed')}")
    print(f"controlled_real_live_pilot_allowed: {summary.get('controlled_real_live_pilot_allowed')}")
    blocking_dimensions = summary.get("promotion_blocking_dimensions") or []
    print(f"promotion_blocking_dimensions: {', '.join(blocking_dimensions) if blocking_dimensions else 'none'}")
    policy_diagnostics = (
        report.get("live_local_recall_rescue_policy_diagnostics")
        if isinstance(report.get("live_local_recall_rescue_policy_diagnostics"), dict)
        else {}
    )
    candidate_policy_diag = (
        policy_diagnostics.get("capture_safe_candidate")
        if isinstance(policy_diagnostics.get("capture_safe_candidate"), dict)
        else {}
    )
    recommended_policy = (
        candidate_policy_diag.get("recommended_policy")
        if isinstance(candidate_policy_diag.get("recommended_policy"), dict)
        else {}
    )
    best_policy = (
        candidate_policy_diag.get("best_policy")
        if isinstance(candidate_policy_diag.get("best_policy"), dict)
        else recommended_policy
    )
    if candidate_policy_diag:
        print(f"capture_safe_candidate_rescue_policy_status: {candidate_policy_diag.get('status')}")
        if best_policy:
            print(
                "capture_safe_candidate_best_live_rescue_policy: "
                f"{best_policy.get('policy')} "
                f"(local={safe_float(best_policy.get('local_seconds')):.2f}s, "
                f"remote_risk={safe_float(best_policy.get('remote_risk_seconds')):.2f}s)"
            )
        if recommended_policy:
            print(f"capture_safe_candidate_recommended_rescue_policy: {recommended_policy.get('policy')}")
    target_me_diagnostics = (
        report.get("live_local_recall_target_me_diagnostics")
        if isinstance(report.get("live_local_recall_target_me_diagnostics"), dict)
        else {}
    )
    real_target_me = (
        target_me_diagnostics.get("real")
        if isinstance(target_me_diagnostics.get("real"), dict)
        else {}
    )
    candidate_target_me = (
        target_me_diagnostics.get("capture_safe_candidate")
        if isinstance(target_me_diagnostics.get("capture_safe_candidate"), dict)
        else {}
    )
    if real_target_me:
        print(f"real_live_target_me_status: {real_target_me.get('status')}")
        print(
            "real_live_target_me_possible_or_confirmed_local_seconds: "
            f"{safe_float(real_target_me.get('target_me_possible_or_confirmed_local_seconds'))}"
        )
        recommended_target_me = (
            real_target_me.get("recommended_policy")
            if isinstance(real_target_me.get("recommended_policy"), dict)
            else {}
        )
        if recommended_target_me:
            print(f"real_live_target_me_recommended_policy: {recommended_target_me.get('policy')}")
            print(
                "real_live_target_me_recommended_policy_missing_me_recovered_seconds: "
                f"{safe_float(recommended_target_me.get('missing_me_recovered_seconds'))}"
            )
    target_me_shadow_diagnostics = (
        report.get("live_target_me_shadow_policy_diagnostics")
        if isinstance(report.get("live_target_me_shadow_policy_diagnostics"), dict)
        else {}
    )
    real_target_me_shadow = (
        target_me_shadow_diagnostics.get("real")
        if isinstance(target_me_shadow_diagnostics.get("real"), dict)
        else {}
    )
    if real_target_me_shadow:
        print(f"real_live_target_me_shadow_status: {real_target_me_shadow.get('status')}")
        recommended_target_me_shadow = (
            real_target_me_shadow.get("recommended_policy")
            if isinstance(real_target_me_shadow.get("recommended_policy"), dict)
            else {}
        )
        if recommended_target_me_shadow:
            print(
                "real_live_target_me_shadow_recommended_policy: "
                f"{recommended_target_me_shadow.get('policy')}"
            )
            print(
                "real_live_target_me_shadow_recommended_policy_missing_me_recovered_seconds: "
                f"{safe_float(recommended_target_me_shadow.get('missing_me_recovered_seconds'))}"
            )
            print(
                "real_live_target_me_shadow_recommended_policy_remote_risk_seconds: "
                f"{safe_float(recommended_target_me_shadow.get('suspected_remote_leak_in_me_seconds'))}"
            )
            print(
                "real_live_target_me_shadow_recommended_policy_order_mismatch_count: "
                f"{safe_int(recommended_target_me_shadow.get('contentful_role_constrained_order_mismatch_count'))}"
            )
            print(
                "real_live_target_me_shadow_recommended_policy_order_mismatch_delta_count: "
                f"{safe_int(recommended_target_me_shadow.get('contentful_role_constrained_order_mismatch_delta_count'))}"
            )
            print(
                "real_live_target_me_shadow_recommended_policy_rejected_candidate_seconds: "
                f"{safe_float(recommended_target_me_shadow.get('rejected_candidate_seconds'))}"
            )
            print(
                "real_live_target_me_shadow_recommended_policy_rejected_order_seconds: "
                f"{safe_float(recommended_target_me_shadow.get('rejected_would_add_contentful_order_mismatch_seconds'))}"
            )
    target_me_shadow_profile_diagnostics = (
        report.get("live_target_me_shadow_profile_diagnostics")
        if isinstance(report.get("live_target_me_shadow_profile_diagnostics"), dict)
        else {}
    )
    real_target_me_shadow_profile = (
        target_me_shadow_profile_diagnostics.get("real")
        if isinstance(target_me_shadow_profile_diagnostics.get("real"), dict)
        else {}
    )
    if real_target_me_shadow_profile:
        print(f"real_live_target_me_shadow_profile_status: {real_target_me_shadow_profile.get('status')}")
        best_target_me_shadow_profile = (
            real_target_me_shadow_profile.get("best_profile")
            if isinstance(real_target_me_shadow_profile.get("best_profile"), dict)
            else {}
        )
        if best_target_me_shadow_profile:
            print(
                "real_live_target_me_shadow_profile_best_policy: "
                f"{best_target_me_shadow_profile.get('policy')}"
            )
            print(
                "real_live_target_me_shadow_profile_all_parity_gates_passed_sessions: "
                f"{safe_int(best_target_me_shadow_profile.get('all_parity_gates_passed_session_count'))}"
            )
            print(
                "real_live_target_me_shadow_profile_non_passing_gate_count: "
                f"{safe_int(best_target_me_shadow_profile.get('non_passing_gate_count'))}"
            )
            print(
                "real_live_target_me_shadow_profile_missing_me_seconds: "
                f"{safe_float(best_target_me_shadow_profile.get('live_missing_me_seconds'))}"
            )
            print(
                "real_live_target_me_shadow_profile_missing_me_visible_in_suppressed_mic_seconds: "
                f"{safe_float(best_target_me_shadow_profile.get('live_missing_me_visible_in_suppressed_mic_seconds'))}"
            )
            print(
                "real_live_target_me_shadow_profile_missing_me_with_target_me_candidate_seconds: "
                f"{safe_float(best_target_me_shadow_profile.get('live_missing_me_with_target_me_candidate_seconds'))}"
            )
            print(
                "real_live_target_me_shadow_profile_remote_leak_seconds: "
                f"{safe_float(best_target_me_shadow_profile.get('live_suspected_remote_leak_in_me_seconds'))}"
            )
            print(
                "real_live_target_me_shadow_profile_removed_live_turn_seconds: "
                f"{safe_float(best_target_me_shadow_profile.get('removed_live_turn_seconds'))}"
            )
            print(
                "real_live_target_me_shadow_profile_visible_suppressed_mic_added_turn_seconds: "
                f"{safe_float(best_target_me_shadow_profile.get('visible_suppressed_mic_added_turn_seconds'))}"
            )
        best_live_target_me_shadow_profile = (
            real_target_me_shadow_profile.get("best_live_implementable_profile")
            if isinstance(real_target_me_shadow_profile.get("best_live_implementable_profile"), dict)
            else {}
        )
        if best_live_target_me_shadow_profile:
            print(
                "real_live_target_me_shadow_profile_best_live_implementable_policy: "
                f"{best_live_target_me_shadow_profile.get('policy')}"
            )
            print(
                "real_live_target_me_shadow_profile_best_live_implementable_non_passing_gate_count: "
                f"{safe_int(best_live_target_me_shadow_profile.get('non_passing_gate_count'))}"
            )
            print(
                "real_live_target_me_shadow_profile_best_live_implementable_missing_me_seconds: "
                f"{safe_float(best_live_target_me_shadow_profile.get('live_missing_me_seconds'))}"
            )
            print(
                "real_live_target_me_shadow_profile_best_live_implementable_remote_leak_seconds: "
                f"{safe_float(best_live_target_me_shadow_profile.get('live_suspected_remote_leak_in_me_seconds'))}"
            )
            print(
                "real_live_target_me_shadow_profile_best_live_implementable_contentful_order_mismatch_count: "
                f"{safe_int(best_live_target_me_shadow_profile.get('live_contentful_role_constrained_order_mismatch_count'))}"
            )
            print(
                "real_live_target_me_shadow_profile_best_live_implementable_visible_with_target_me_seconds: "
                f"{safe_float(best_live_target_me_shadow_profile.get('live_missing_me_visible_with_target_me_candidate_seconds'))}"
            )
            print(
                "real_live_target_me_shadow_profile_best_live_implementable_visible_without_target_me_seconds: "
                f"{safe_float(best_live_target_me_shadow_profile.get('live_missing_me_visible_without_target_me_candidate_seconds'))}"
            )
            print(
                "real_live_target_me_shadow_profile_best_live_implementable_not_visible_with_target_me_seconds: "
                f"{safe_float(best_live_target_me_shadow_profile.get('live_missing_me_not_visible_with_target_me_candidate_seconds'))}"
            )
            print(
                "real_live_target_me_shadow_profile_best_live_implementable_not_visible_without_target_me_seconds: "
                f"{safe_float(best_live_target_me_shadow_profile.get('live_missing_me_not_visible_without_target_me_candidate_seconds'))}"
            )
            best_to_live_gap = (
                real_target_me_shadow_profile.get("best_to_live_implementable_gap")
                if isinstance(real_target_me_shadow_profile.get("best_to_live_implementable_gap"), dict)
                else {}
            )
            if best_to_live_gap:
                print(
                    "real_live_target_me_shadow_profile_best_to_live_implementable_missing_gap_seconds: "
                    f"{safe_float(best_to_live_gap.get('missing_me_seconds_gap'))}"
                )
                print(
                    "real_live_target_me_shadow_profile_best_to_live_implementable_interpretation: "
                    f"{best_to_live_gap.get('interpretation')}"
                )
            soft_boundary_lab = (
                report.get("live_soft_local_speaker_boundary_shadow_lab")
                if isinstance(report.get("live_soft_local_speaker_boundary_shadow_lab"), dict)
                else {}
            )
            if soft_boundary_lab:
                metrics = soft_boundary_lab.get("metrics") if isinstance(soft_boundary_lab.get("metrics"), dict) else {}
                print(f"real_live_soft_local_speaker_boundary_shadow_lab_status: {soft_boundary_lab.get('status')}")
                print(
                    "real_live_soft_local_speaker_boundary_shadow_lab_missing_delta_seconds: "
                    f"{metrics.get('missing_me_delta_vs_best_live_implementable_seconds')}"
                )
                print(
                    "real_live_soft_local_speaker_boundary_shadow_lab_remote_leak_delta_seconds: "
                    f"{metrics.get('remote_leak_delta_vs_best_live_implementable_seconds')}"
                )
            online_boundary_lab = (
                report.get("live_online_speaker_boundary_evidence_design_lab")
                if isinstance(report.get("live_online_speaker_boundary_evidence_design_lab"), dict)
                else {}
            )
            if online_boundary_lab:
                top_unit = (
                    online_boundary_lab.get("top_design_unit")
                    if isinstance(online_boundary_lab.get("top_design_unit"), dict)
                    else {}
                )
                print(
                    "real_live_online_speaker_boundary_design_status: "
                    f"{online_boundary_lab.get('status')}"
                )
                print(
                    "real_live_online_speaker_boundary_design_actionable_seconds: "
                    f"{safe_float(online_boundary_lab.get('actionable_seconds'))}"
                )
                print(
                    "real_live_online_speaker_boundary_design_potential_publish_seconds: "
                    f"{safe_float(online_boundary_lab.get('potential_publish_seconds'))}"
                )
                if top_unit:
                    print(
                        "real_live_online_speaker_boundary_design_top_unit: "
                        f"{top_unit.get('id')} ({safe_float(top_unit.get('seconds'))}s)"
                    )
            remaining_gap = (
                report.get("live_target_me_shadow_profile_best_live_implementable_remaining_gap")
                if isinstance(report.get("live_target_me_shadow_profile_best_live_implementable_remaining_gap"), dict)
                else {}
            )
            by_policy_set = (
                remaining_gap.get("by_policy_set")
                if isinstance(remaining_gap.get("by_policy_set"), dict)
                else {}
            )
            top_policy_set = next(iter(by_policy_set.items()), None)
            if top_policy_set:
                print(
                    "real_live_target_me_shadow_profile_best_live_implementable_remaining_gap_top_policy_set: "
                    f"{top_policy_set[0]} ({safe_float(top_policy_set[1].get('seconds'))}s)"
                )
            for field_name, print_name in (
                ("by_actionability", "top_actionability"),
                ("by_segmentability", "top_segmentability"),
                ("by_suppressed_policy_set", "top_suppressed_policy_set"),
                ("by_suppressed_gate_reason", "top_suppressed_gate_reason"),
                ("by_suppressed_batch_role_label", "top_suppressed_batch_role_label"),
            ):
                grouped = remaining_gap.get(field_name) if isinstance(remaining_gap.get(field_name), dict) else {}
                top_row = next(iter(grouped.items()), None)
                if top_row:
                    print(
                        f"real_live_target_me_shadow_profile_best_live_implementable_remaining_gap_{print_name}: "
                        f"{top_row[0]} ({safe_float(top_row[1].get('seconds'))}s)"
                    )
            local_island_lab = (
                report.get("live_local_island_split_lab")
                if isinstance(report.get("live_local_island_split_lab"), dict)
                else {}
            )
            if local_island_lab:
                print(f"real_live_local_island_split_lab_status: {local_island_lab.get('status')}")
                print(
                    "real_live_local_island_split_lab_candidate_batch_seconds: "
                    f"{safe_float(local_island_lab.get('candidate_batch_seconds'))}"
                )
                print(
                    "real_live_local_island_split_lab_accepted_batch_seconds: "
                    f"{safe_float(local_island_lab.get('accepted_batch_seconds'))}"
                )
                print(
                    "real_live_local_island_split_lab_accepted_local_island_seconds: "
                    f"{safe_float(local_island_lab.get('accepted_local_island_seconds'))}"
                )
                print(
                    "real_live_local_island_audio_anchor_lab_anchor_seconds: "
                    f"{summary.get('real_live_local_island_audio_anchor_lab_anchor_seconds')}"
                )
                retime_anchor_lab = (
                    report.get("live_local_island_retime_anchor_lab")
                    if isinstance(report.get("live_local_island_retime_anchor_lab"), dict)
                    else {}
                )
                print(
                    "real_live_local_island_retime_anchor_lab_context_expansion_seconds: "
                    f"{retime_anchor_lab.get('context_expansion_seconds')}"
                )
                print(
                    "real_live_local_island_retime_anchor_lab_max_leading_gap_seconds: "
                    f"{retime_anchor_lab.get('max_leading_gap_seconds')}"
                )
                print(
                    "real_live_live_only_local_island_candidate_lab_candidate_seconds: "
                    f"{summary.get('real_live_live_only_local_island_candidate_lab_candidate_seconds')}"
                )
                print(
                    "real_live_live_only_local_island_candidate_lab_remote_risk_seconds: "
                    f"{summary.get('real_live_live_only_local_island_candidate_lab_remote_risk_seconds')}"
                )
                print(
                    "real_live_live_only_local_island_strict_zero_remote_risk_candidate_seconds: "
                    f"{summary.get('real_live_live_only_local_island_strict_zero_remote_risk_candidate_seconds')}"
                )
                print(
                    "real_live_live_only_local_island_strict_zero_remote_risk_remote_risk_seconds: "
                    f"{summary.get('real_live_live_only_local_island_strict_zero_remote_risk_remote_risk_seconds')}"
                )
                print(
                    "real_live_live_only_retime_boundary_recommended_profile: "
                    f"{summary.get('real_live_live_only_retime_boundary_recommended_profile')}"
                )
                print(
                    "real_live_live_only_retime_boundary_recommended_missing_overlap_seconds: "
                    f"{summary.get('real_live_live_only_retime_boundary_recommended_missing_overlap_seconds')}"
                )
                print(
                    "real_live_live_only_retime_boundary_recommended_remote_risk_seconds: "
                    f"{summary.get('real_live_live_only_retime_boundary_recommended_remote_risk_seconds')}"
                )
                print(
                    "real_live_live_only_retime_boundary_best_missing_profile: "
                    f"{summary.get('real_live_live_only_retime_boundary_best_missing_profile')}"
                )
                print(
                    "real_live_live_only_retime_boundary_best_missing_overlap_seconds: "
                    f"{summary.get('real_live_live_only_retime_boundary_best_missing_overlap_seconds')}"
                )
                print(
                    "real_live_live_only_retime_boundary_best_missing_remote_risk_seconds: "
                    f"{summary.get('real_live_live_only_retime_boundary_best_missing_remote_risk_seconds')}"
                )
                print(
                    "real_live_live_only_retime_boundary_best_zero_remote_evaluated_profile: "
                    f"{summary.get('real_live_live_only_retime_boundary_best_zero_remote_evaluated_profile')}"
                )
                print(
                    "real_live_live_only_retime_boundary_best_zero_remote_evaluated_missing_overlap_seconds: "
                    f"{summary.get('real_live_live_only_retime_boundary_best_zero_remote_evaluated_missing_overlap_seconds')}"
                )
                print(
                    "real_live_live_only_retime_boundary_best_zero_remote_evaluated_remote_risk_seconds: "
                    f"{summary.get('real_live_live_only_retime_boundary_best_zero_remote_evaluated_remote_risk_seconds')}"
                )
                print(
                    "real_live_live_only_retime_boundary_best_zero_remote_evaluated_candidate_seconds: "
                    f"{summary.get('real_live_live_only_retime_boundary_best_zero_remote_evaluated_candidate_seconds')}"
                )
                strict_live_only_profile_prefix = (
                    f"real_live_target_me_shadow_profile_{STRICT_LIVE_ONLY_LOCAL_ISLAND_PROFILE_POLICY}"
                )
                print(
                    "real_live_strict_live_only_local_island_profile_missing_me_seconds: "
                    f"{summary.get(f'{strict_live_only_profile_prefix}_live_missing_me_seconds')}"
                )
                print(
                    "real_live_strict_live_only_local_island_profile_remote_leak_seconds: "
                    f"{summary.get(f'{strict_live_only_profile_prefix}_live_suspected_remote_leak_in_me_seconds')}"
                )
                combined_strict_audio_safe_union_profile_prefix = (
                    "real_live_target_me_shadow_profile_"
                    f"{STRICT_LIVE_ONLY_LOCAL_ISLAND_AUDIO_SAFE_UNION_PROFILE_POLICY}"
                )
                print(
                    "real_live_combined_strict_audio_safe_union_profile_missing_me_seconds: "
                    f"{summary.get(f'{combined_strict_audio_safe_union_profile_prefix}_live_missing_me_seconds')}"
                )
                print(
                    "real_live_combined_strict_audio_safe_union_profile_remote_leak_seconds: "
                    f"{summary.get(f'{combined_strict_audio_safe_union_profile_prefix}_live_suspected_remote_leak_in_me_seconds')}"
                )
                remote_forbidden_boundary_classifier_profile_prefix = (
                    "real_live_target_me_shadow_profile_"
                    f"{REMOTE_FORBIDDEN_BOUNDARY_CLASSIFIER_PROFILE_POLICY}"
                )
                print(
                    "real_live_remote_forbidden_boundary_classifier_profile_missing_me_seconds: "
                    f"{summary.get(f'{remote_forbidden_boundary_classifier_profile_prefix}_live_missing_me_seconds')}"
                )
                print(
                    "real_live_remote_forbidden_boundary_classifier_profile_remote_leak_seconds: "
                    f"{summary.get(f'{remote_forbidden_boundary_classifier_profile_prefix}_live_suspected_remote_leak_in_me_seconds')}"
                )
                print(
                    "real_live_remote_forbidden_boundary_classifier_profile_added_turn_seconds: "
                    f"{summary.get(f'{remote_forbidden_boundary_classifier_profile_prefix}_visible_suppressed_mic_added_turn_seconds')}"
                )
                print(
                    "real_live_remote_forbidden_boundary_classifier_profile_contentful_order_mismatch_count: "
                    f"{summary.get(f'{remote_forbidden_boundary_classifier_profile_prefix}_live_contentful_role_constrained_order_mismatch_count')}"
                )
                strict_delta_lab = (
                    report.get("live_strict_local_island_shadow_delta_lab")
                    if isinstance(report.get("live_strict_local_island_shadow_delta_lab"), dict)
                    else {}
                )
                print(
                    "real_live_strict_local_island_shadow_delta_incremental_turn_seconds: "
                    f"{strict_delta_lab.get('incremental_strict_turn_seconds')}"
                )
                print(
                    "real_live_strict_local_island_shadow_delta_closed_missing_me_seconds: "
                    f"{strict_delta_lab.get('closed_missing_me_seconds')}"
                )
                print(
                    "real_live_local_island_split_oracle_profile_missing_me_seconds: "
                    f"{summary.get('real_live_local_island_split_oracle_profile_missing_me_seconds')}"
                )
                print(
                    "real_live_local_island_split_oracle_profile_missing_me_delta_vs_best_live_implementable_seconds: "
                    f"{summary.get('real_live_local_island_split_oracle_profile_missing_me_delta_vs_best_live_implementable_seconds')}"
                )
                print(
                    "real_live_local_island_retime_oracle_profile_missing_me_seconds: "
                    f"{summary.get('real_live_local_island_retime_oracle_profile_missing_me_seconds')}"
                )
                print(
                    "real_live_local_island_retime_oracle_profile_missing_me_delta_vs_best_live_implementable_seconds: "
                    f"{summary.get('real_live_local_island_retime_oracle_profile_missing_me_delta_vs_best_live_implementable_seconds')}"
                )
    if candidate_target_me:
        print(f"capture_safe_candidate_target_me_status: {candidate_target_me.get('status')}")
    objective_audit = (
        report.get("objective_audit")
        if isinstance(report.get("objective_audit"), dict)
        else {}
    )
    if objective_audit:
        next_focus = objective_audit.get("next_focus") if isinstance(objective_audit.get("next_focus"), dict) else {}
        print(f"objective_audit: {objective_audit.get('overall_status')}")
        if next_focus:
            print(f"objective_next_focus: {next_focus.get('action_id')}")
        audit_blocking_dimensions = objective_audit.get("blocking_dimensions") or []
        print(
            "objective_blocking_dimensions: "
            f"{', '.join(audit_blocking_dimensions) if audit_blocking_dimensions else 'none'}"
        )
    print(f"gate_issues: {len(report.get('gate_issues') or [])}")
    triage_summary = (
        report.get("real_blocker_triage_summary")
        if isinstance(report.get("real_blocker_triage_summary"), dict)
        else {}
    )
    print(f"real_blocker_triage_items: {triage_summary.get('total_items', 0)}")
    print(f"real_blocker_triage_sessions: {triage_summary.get('session_count', 0)}")
    if report.get("recommended_next"):
        print(f"recommended_next: {report['recommended_next']}")
    print(f"report: {md_path}")
    for command in report.get("next_commands") or []:
        print(f"next: {command}")
    strict = report.get("strict_coverage") or {}
    strict_failed = strict.get("status") == "failed"
    risk_failed = bool(args.fail_on_risk and (
        summary.get("live_order_mismatch_count", 0) > 0
        or summary.get("live_missing_me_seconds", 0.0) > 0
        or summary.get("live_suspected_remote_leak_in_me_seconds", 0.0) > 0
        or summary.get("adjacent_duplicate_chunk_count", 0) > 0
        or summary.get("live_boundary_gate_issue_count", 0) > 0
    ))
    insufficient_coverage_failed = bool(
        args.fail_on_insufficient_coverage
        and (
            summary["live_sessions"] < args.min_live_sessions
            or summary["compared_sessions"] < args.min_compared_sessions
        )
    )
    if strict_failed or risk_failed or insufficient_coverage_failed:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
