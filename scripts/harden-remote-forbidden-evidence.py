#!/usr/bin/env python3
"""Materialize remote-forbidden token evidence from offline_aec_v2 ASR audit.

This is a shadow/review-only layer. It does not modify transcript profiles and
does not promote any Echo Guard audio candidate.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = "murmurmark.echo.remote_forbidden_evidence/v1"
SUMMARY_SCHEMA = "murmurmark.echo.remote_forbidden_summary/v1"
SCRIPT_VERSION = "0.1.0"
REMOTE_FORBIDDEN_KEY = "remote_forbidden_token_guard"
DEFAULT_OUT_DIR = Path("derived/audit/remote-forbidden")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build remote-forbidden evidence rows for one session.")
    parser.add_argument("session", type=Path)
    parser.add_argument(
        "--profile",
        default="auto",
        help="Transcript profile used for overlap links. Default: auto from session_readiness.json.",
    )
    parser.add_argument("--out-dir", type=Path, default=None)
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
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
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


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def suffix(profile: str) -> str:
    return "" if profile == "current" else f".{profile}"


def rel(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def profile_from_readiness(session: Path) -> str | None:
    payload = read_json(session / "derived/readiness/session_readiness.json")
    if not isinstance(payload, dict):
        return None
    profile = payload.get("selected_profile")
    return str(profile) if profile else None


def profile_exists(session: Path, profile: str) -> bool:
    resolved = session / "derived/transcript-simple/whisper-cpp/resolved"
    return (resolved / f"clean_dialogue{suffix(profile)}.json").exists()


def resolve_profile(session: Path, requested: str) -> str:
    if requested != "auto":
        return requested
    readiness_profile = profile_from_readiness(session)
    if readiness_profile and profile_exists(session, readiness_profile):
        return readiness_profile
    for profile in (
        "local_recall_repair_v1",
        "order_repair_v1",
        "audit_cleanup_v7",
        "audit_cleanup_v6",
        "audit_cleanup_v5",
        "audit_cleanup_v4",
        "audit_cleanup_v3",
        "audit_cleanup_v2",
        "audit_cleanup_v1",
        "shadow_v2",
        "current",
    ):
        if profile_exists(session, profile):
            return profile
    return "missing"


def load_dialogue(session: Path, profile: str) -> list[dict[str, Any]]:
    if profile == "missing":
        return []
    path = session / "derived/transcript-simple/whisper-cpp/resolved" / f"clean_dialogue{suffix(profile)}.json"
    payload = read_json(path)
    rows = payload.get("utterances") if isinstance(payload, dict) else None
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def interval_overlap(start: float, end: float, other_start: float, other_end: float) -> float:
    return max(0.0, min(end, other_end) - max(start, other_start))


def overlapping_utterance_ids(rows: list[dict[str, Any]], start: float, end: float) -> dict[str, list[str]]:
    result = {"me": [], "remote": []}
    for row in rows:
        row_start = safe_float(row.get("start"))
        row_end = safe_float(row.get("end"))
        if row_end <= row_start or interval_overlap(start, end, row_start, row_end) <= 0:
            continue
        source = str(row.get("source_track") or "").lower()
        role = str(row.get("role") or row.get("speaker_label") or "").lower()
        row_id = str(row.get("id") or "")
        if not row_id:
            continue
        if source == "mic" or role == "me":
            result["me"].append(row_id)
        elif source == "remote" or role in {"remote", "colleagues"}:
            result["remote"].append(row_id)
    return result


def tokens(text: str) -> list[str]:
    return [item.lower() for item in re.findall(r"[\wёЁ]+", text or "") if len(item) > 1]


def token_count(text: str) -> Counter[str]:
    return Counter(tokens(text))


def token_overlap_precision(reference_text: str, candidate_text: str) -> float:
    reference = token_count(reference_text)
    candidate = token_count(candidate_text)
    total = sum(candidate.values())
    if total <= 0:
        return 0.0
    overlap = sum(min(count, reference.get(token, 0)) for token, count in candidate.items())
    return round(overlap / total, 6)


def state_summary(speaker_rows: list[dict[str, Any]], start: float, end: float) -> dict[str, Any]:
    duration = max(0.0, end - start)
    by_state: dict[str, float] = {}
    local_scores: list[float] = []
    remote_active = 0.0
    local_active = 0.0
    for row in speaker_rows:
        row_start = safe_float(row.get("start", row.get("start_sec")))
        row_end = safe_float(row.get("end", row.get("end_sec")))
        overlap = interval_overlap(start, end, row_start, row_end)
        if overlap <= 0:
            continue
        state = str(row.get("state") or "unknown")
        by_state[state] = by_state.get(state, 0.0) + overlap
        if "remote" in state:
            remote_active += overlap
        if state == "local_only" or "double_talk" in state:
            local_active += overlap
        local_score = row.get("local_score")
        if local_score is not None:
            local_scores.append(safe_float(local_score))
    dominant = max(by_state.items(), key=lambda item: item[1])[0] if by_state else "unknown"
    return {
        "dominant_state": dominant,
        "state_seconds": {key: round(value, 3) for key, value in sorted(by_state.items())},
        "remote_active_ratio": round(remote_active / duration, 6) if duration > 0 else 0.0,
        "local_active_ratio": round(local_active / duration, 6) if duration > 0 else 0.0,
        "local_score_mean": round(sum(local_scores) / len(local_scores), 6) if local_scores else None,
        "local_score_max": round(max(local_scores), 6) if local_scores else None,
    }


def decide_remote_row(row: dict[str, Any]) -> dict[str, Any]:
    guard = row.get("guard") if isinstance(row.get("guard"), dict) else {}
    remote_text = str(row.get("remote_text") or "")
    local_text = str(row.get("local_fir_text") or "")
    base_text = str(row.get("base_candidate_text") or "")
    candidate_text = str(row.get("candidate_text") or "")
    local_overlap = safe_float(row.get("local_fir_remote_token_overlap"))
    base_overlap = token_overlap_precision(remote_text, base_text)
    guarded_overlap = safe_float(row.get("candidate_remote_token_overlap"))
    removed_tokens = [str(item) for item in guard.get("removed_tokens") or []]
    kept_tokens = [str(item) for item in guard.get("kept_tokens") or []]
    reason = str(guard.get("removed_reason") or "unknown")
    improvement = max(local_overlap, base_overlap) - guarded_overlap

    if reason == "known_asr_hallucination":
        return {
            "action": "quarantine",
            "confidence": 0.72,
            "reason": "base_candidate_is_known_hallucination",
            "safe_to_apply": False,
        }
    if reason == "remote_forbidden_overlap" and removed_tokens and not kept_tokens and improvement >= 0.5:
        return {
            "action": "suggest_drop",
            "confidence": 0.93 if local_overlap >= 0.75 else 0.88,
            "reason": "remote_only_window_all_candidate_tokens_are_remote_explainable",
            "safe_to_apply": False,
        }
    if reason == "remote_forbidden_overlap" and removed_tokens and kept_tokens:
        return {
            "action": "quarantine",
            "confidence": 0.78,
            "reason": "remote_tokens_removed_but_candidate_keeps_unique_tokens",
            "safe_to_apply": False,
        }
    if not removed_tokens and guarded_overlap <= 0.05 and local_overlap <= 0.05:
        return {
            "action": "keep",
            "confidence": 0.62,
            "reason": "no_remote_token_overlap_detected",
            "safe_to_apply": False,
        }
    if candidate_text.strip() and improvement < 0.2:
        return {
            "action": "needs_review",
            "confidence": 0.55,
            "reason": "guard_does_not_reduce_remote_token_overlap_enough",
            "safe_to_apply": False,
        }
    if local_text.strip() and local_overlap >= 0.5:
        return {
            "action": "needs_review",
            "confidence": 0.68,
            "reason": "local_fir_still_contains_remote_text",
            "safe_to_apply": False,
        }
    return {
        "action": "needs_review",
        "confidence": 0.5,
        "reason": "insufficient_evidence_for_automatic_action",
        "safe_to_apply": False,
    }


def decide_local_gate(row: dict[str, Any]) -> dict[str, Any]:
    candidate_recall = safe_float(row.get("candidate_local_token_recall"))
    local_fir_recall = safe_float(row.get("local_fir_local_token_recall"))
    delta = candidate_recall - local_fir_recall
    if delta < -0.02:
        return {
            "action": "needs_review",
            "confidence": 0.85,
            "reason": "candidate_loses_local_words_vs_local_fir",
            "safe_to_apply": False,
        }
    if candidate_recall < 0.90:
        return {
            "action": "quarantine",
            "confidence": 0.7,
            "reason": "local_recall_below_safe_threshold",
            "safe_to_apply": False,
        }
    return {
        "action": "keep",
        "confidence": 0.9,
        "reason": "local_only_words_preserved",
        "safe_to_apply": False,
    }


def remote_evidence_row(
    *,
    session: Path,
    index: int,
    source_row: dict[str, Any],
    speaker_rows: list[dict[str, Any]],
    dialogue: list[dict[str, Any]],
) -> dict[str, Any]:
    start = safe_float(source_row.get("start_sec"))
    end = safe_float(source_row.get("end_sec"))
    guard = source_row.get("guard") if isinstance(source_row.get("guard"), dict) else {}
    decision = decide_remote_row(source_row)
    remote_text = str(source_row.get("remote_text") or "")
    local_text = str(source_row.get("local_fir_text") or "")
    base_text = str(source_row.get("base_candidate_text") or "")
    guarded_text = str(source_row.get("candidate_text") or "")
    local_overlap = safe_float(source_row.get("local_fir_remote_token_overlap"))
    base_overlap = token_overlap_precision(remote_text, base_text)
    guarded_overlap = safe_float(source_row.get("candidate_remote_token_overlap"))
    linked = overlapping_utterance_ids(dialogue, start, end)
    return {
        "schema": SCHEMA,
        "id": f"rfg_remote_{index:04d}",
        "session": str(session),
        "kind": "remote_forbidden_token",
        "source": "offline_aec_v2_asr_clip_audit",
        "interval": {"start": round(start, 3), "end": round(end, 3), "duration_sec": round(max(0.0, end - start), 3)},
        "transcript_links": {
            "me_utterance_ids": linked["me"],
            "remote_utterance_ids": linked["remote"],
        },
        "speaker_state": state_summary(speaker_rows, start, end),
        "texts": {
            "remote_reference": remote_text,
            "local_fir": local_text,
            "base_candidate": base_text,
            "guarded_candidate": guarded_text,
        },
        "tokens": {
            "remote": tokens(remote_text),
            "mic_candidate": tokens(base_text),
            "mic_guarded_candidate": tokens(guarded_text),
            "local_fir": tokens(local_text),
            "base_candidate": tokens(base_text),
            "guarded_candidate": tokens(guarded_text),
            "removed": [str(item) for item in guard.get("removed_tokens") or []],
            "kept": [str(item) for item in guard.get("kept_tokens") or []],
        },
        "metrics": {
            "local_fir_remote_token_overlap": local_overlap,
            "base_candidate_remote_token_overlap": base_overlap,
            "guarded_remote_token_overlap": guarded_overlap,
            "leak_delta_vs_local_fir": round(guarded_overlap - local_overlap, 6),
            "leak_delta_vs_base_candidate": round(guarded_overlap - base_overlap, 6),
        },
        "decision": decision,
    }


def local_gate_row(
    *,
    session: Path,
    index: int,
    source_row: dict[str, Any],
    speaker_rows: list[dict[str, Any]],
    dialogue: list[dict[str, Any]],
) -> dict[str, Any]:
    start = safe_float(source_row.get("start_sec"))
    end = safe_float(source_row.get("end_sec"))
    decision = decide_local_gate(source_row)
    raw_text = str(source_row.get("raw_mic_text") or "")
    candidate_text = str(source_row.get("candidate_text") or "")
    local_fir_text = str(source_row.get("local_fir_text") or "")
    linked = overlapping_utterance_ids(dialogue, start, end)
    candidate_recall = safe_float(source_row.get("candidate_local_token_recall"))
    local_fir_recall = safe_float(source_row.get("local_fir_local_token_recall"))
    return {
        "schema": SCHEMA,
        "id": f"rfg_local_gate_{index:04d}",
        "session": str(session),
        "kind": "local_speech_gate",
        "source": "offline_aec_v2_near_end_preservation_audit",
        "interval": {"start": round(start, 3), "end": round(end, 3), "duration_sec": round(max(0.0, end - start), 3)},
        "transcript_links": {
            "me_utterance_ids": linked["me"],
            "remote_utterance_ids": linked["remote"],
        },
        "speaker_state": state_summary(speaker_rows, start, end),
        "texts": {
            "raw_mic": raw_text,
            "local_fir": local_fir_text,
            "guarded_candidate": candidate_text,
        },
        "tokens": {
            "raw_mic": tokens(raw_text),
            "mic_raw": tokens(raw_text),
            "mic_guarded_candidate": tokens(candidate_text),
            "local_fir": tokens(local_fir_text),
            "guarded_candidate": tokens(candidate_text),
        },
        "metrics": {
            "candidate_local_token_recall": candidate_recall,
            "local_fir_local_token_recall": local_fir_recall,
            "local_recall_delta_vs_local_fir": round(candidate_recall - local_fir_recall, 6),
        },
        "decision": decision,
    }


def bucket_seconds(rows: list[dict[str, Any]], action: str) -> float:
    return round(
        sum(safe_float(row.get("interval", {}).get("duration_sec")) for row in rows if row.get("decision", {}).get("action") == action),
        3,
    )


def total_seconds(rows: list[dict[str, Any]]) -> float:
    return round(sum(safe_float(row.get("interval", {}).get("duration_sec")) for row in rows), 3)


def build_summary(
    *,
    session: Path,
    profile: str,
    out_dir: Path,
    leak_report: dict[str, Any] | None,
    preservation_report: dict[str, Any] | None,
    rows: list[dict[str, Any]],
    status: str,
    reason: str | None = None,
) -> dict[str, Any]:
    actions = Counter(str(row.get("decision", {}).get("action") or "unknown") for row in rows)
    remote_rows = [row for row in rows if row.get("kind") == "remote_forbidden_token"]
    local_rows = [row for row in rows if row.get("kind") == "local_speech_gate"]
    review_burden_seconds = (
        bucket_seconds(rows, "suggest_drop")
        + bucket_seconds(rows, "quarantine")
        + bucket_seconds(rows, "needs_review")
    )
    candidate = {}
    if isinstance(leak_report, dict):
        candidates = leak_report.get("candidates") if isinstance(leak_report.get("candidates"), dict) else {}
        candidate = candidates.get(REMOTE_FORBIDDEN_KEY) if isinstance(candidates.get(REMOTE_FORBIDDEN_KEY), dict) else {}
    local_recall_delta = candidate.get("local_only_word_recall_delta")
    leak_delta = candidate.get("remote_token_leak_delta")
    local_gate_passed = local_recall_delta is not None and safe_float(local_recall_delta) >= -0.02
    leak_improved = leak_delta is not None and safe_float(leak_delta) < 0.0
    gate_passed = status == "ok" and leak_improved and local_gate_passed
    if status == "ok" and not leak_improved:
        gate_reason = "remote_token_leak_not_improved"
    elif status == "ok" and not local_gate_passed:
        gate_reason = "local_recall_gate_failed"
    elif status == "ok":
        gate_reason = "remote_tokens_reduced_without_local_recall_regression"
    else:
        gate_reason = reason or status
    return {
        "schema": SUMMARY_SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": {"name": "harden-remote-forbidden-evidence", "version": SCRIPT_VERSION},
        "session": str(session),
        "profile": profile,
        "status": status,
        "reason": reason,
        "mode": "shadow_review_only",
        "inputs": {
            "asr_leak_report": rel(session / "derived/preprocess/echo/offline_aec_v2_asr_leak_report.json", session),
            "near_end_preservation_report": rel(
                session / "derived/preprocess/echo/offline_aec_v2_near_end_preservation_report.json", session
            ),
            "speaker_state": rel(session / "derived/preprocess/echo/speaker_state.jsonl", session),
            "clean_dialogue": rel(
                session / "derived/transcript-simple/whisper-cpp/resolved" / f"clean_dialogue{suffix(profile)}.json",
                session,
            )
            if profile != "missing"
            else None,
        },
        "outputs": {
            "evidence_rows": rel(out_dir / "remote_forbidden_evidence.jsonl", session),
            "summary": rel(out_dir / "remote_forbidden_summary.json", session),
            "review": rel(out_dir / "remote_forbidden_review.md", session),
        },
        "metrics": {
            "remote_forbidden_rows": len(remote_rows),
            "local_speech_gate_rows": len(local_rows),
            "guarded_seconds": total_seconds(rows),
            "remote_forbidden_guarded_seconds": total_seconds(remote_rows),
            "local_speech_gate_guarded_seconds": total_seconds(local_rows),
            "review_burden_seconds": round(review_burden_seconds, 3),
            "actions": dict(sorted(actions.items())),
            "suggest_drop_seconds": bucket_seconds(rows, "suggest_drop"),
            "quarantine_seconds": bucket_seconds(rows, "quarantine"),
            "needs_review_seconds": bucket_seconds(rows, "needs_review"),
            "keep_seconds": bucket_seconds(rows, "keep"),
            "remote_token_leak_rate_before": candidate.get("local_fir_remote_token_leak_rate"),
            "remote_token_leak_rate_after": candidate.get("remote_token_leak_rate"),
            "remote_token_leak_delta": leak_delta,
            "local_word_recall_before": candidate.get("local_fir_local_only_word_recall"),
            "local_word_recall_after": candidate.get("local_only_word_recall"),
            "local_word_recall_delta": local_recall_delta,
        },
        "gates": {
            "passed": gate_passed,
            "reason": gate_reason,
            "remote_token_leak_improved": leak_improved,
            "local_recall_gate_passed": local_gate_passed,
            "no_default_promotion": True,
        },
        "recommendation": "review_remote_forbidden_evidence" if rows else "run_offline_aec_v2_asr_audit",
    }


def render_markdown(summary: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    metrics = summary.get("metrics") if isinstance(summary.get("metrics"), dict) else {}
    gates = summary.get("gates") if isinstance(summary.get("gates"), dict) else {}
    lines = [
        "# Remote-Forbidden Evidence Review",
        "",
        "This is a shadow-only audit. It does not edit the transcript and does not promote an Echo Guard candidate.",
        "",
        "## Summary",
        "",
        f"- Status: `{summary.get('status')}`",
        f"- Profile: `{summary.get('profile')}`",
        f"- Gate: `{gates.get('passed')}` / `{gates.get('reason')}`",
        f"- Remote-token leak delta: `{metrics.get('remote_token_leak_delta')}`",
        f"- Local-word recall delta: `{metrics.get('local_word_recall_delta')}`",
        f"- Guarded seconds: `{metrics.get('guarded_seconds')}`",
        f"- Review-burden seconds: `{metrics.get('review_burden_seconds')}`",
        f"- Actions: `{json.dumps(metrics.get('actions') or {}, ensure_ascii=False, sort_keys=True)}`",
        "",
        "## Evidence Rows",
        "",
        "| ID | Kind | Time | Action | Confidence | Remote / Raw | Candidate | Reason | Transcript IDs |",
        "|---|---|---:|---|---:|---|---|---|---|",
    ]
    for row in rows:
        interval = row.get("interval") if isinstance(row.get("interval"), dict) else {}
        decision = row.get("decision") if isinstance(row.get("decision"), dict) else {}
        texts = row.get("texts") if isinstance(row.get("texts"), dict) else {}
        links = row.get("transcript_links") if isinstance(row.get("transcript_links"), dict) else {}
        source_text = texts.get("remote_reference") or texts.get("raw_mic") or ""
        candidate_text = texts.get("guarded_candidate") or ""
        transcript_ids = ",".join((links.get("me_utterance_ids") or []) + (links.get("remote_utterance_ids") or []))
        lines.append(
            "| `{id}` | `{kind}` | {start:.1f}-{end:.1f} | `{action}` | {confidence:.2f} | {source} | {candidate} | `{reason}` | {ids} |".format(
                id=row.get("id"),
                kind=row.get("kind"),
                start=safe_float(interval.get("start")),
                end=safe_float(interval.get("end")),
                action=decision.get("action"),
                confidence=safe_float(decision.get("confidence")),
                source=escape_table(compact(source_text, 70)),
                candidate=escape_table(compact(candidate_text, 70)),
                reason=decision.get("reason"),
                ids=escape_table(transcript_ids or "-"),
            )
        )
    if not rows:
        lines.append("| none | | | | | | | | |")
    lines.extend(
        [
            "",
            "## How To Read",
            "",
            "- `suggest_drop`: remote-only evidence says the candidate text is explainable by remote tokens. This is still review-only.",
            "- `quarantine`: something was removed or hallucinated, but the evidence is not safe enough for a direct suggestion.",
            "- `needs_review`: the guard found risk or insufficient improvement.",
            "- `keep`: no remote-token action is currently needed.",
            "",
        ]
    )
    return "\n".join(lines)


def compact(text: Any, limit: int) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def escape_table(text: str) -> str:
    return text.replace("|", "\\|")


def skipped(session: Path, profile: str, out_dir: Path, reason: str) -> int:
    summary = build_summary(
        session=session,
        profile=profile,
        out_dir=out_dir,
        leak_report=None,
        preservation_report=None,
        rows=[],
        status="skipped",
        reason=reason,
    )
    write_jsonl(out_dir / "remote_forbidden_evidence.jsonl", [])
    write_json(out_dir / "remote_forbidden_summary.json", summary)
    (out_dir / "remote_forbidden_review.md").write_text(render_markdown(summary, []), encoding="utf-8")
    print(f"remote_forbidden_summary: {out_dir / 'remote_forbidden_summary.json'}")
    print(f"status: skipped")
    print(f"reason: {reason}")
    return 0


def main() -> int:
    args = parse_args()
    session = args.session
    out_dir = args.out_dir or session / DEFAULT_OUT_DIR
    profile = resolve_profile(session, str(args.profile))
    leak_report = read_json(session / "derived/preprocess/echo/offline_aec_v2_asr_leak_report.json")
    preservation_report = read_json(session / "derived/preprocess/echo/offline_aec_v2_near_end_preservation_report.json")
    if not isinstance(leak_report, dict):
        return skipped(session, profile, out_dir, "missing_offline_aec_v2_asr_leak_report")
    if leak_report.get("mode") != "faster_whisper_clip_audit":
        reason = str(leak_report.get("skipped_reason") or leak_report.get("mode") or "asr_audit_not_available")
        return skipped(session, profile, out_dir, reason)

    candidates = leak_report.get("candidates") if isinstance(leak_report.get("candidates"), dict) else {}
    guard_candidate = candidates.get(REMOTE_FORBIDDEN_KEY) if isinstance(candidates.get(REMOTE_FORBIDDEN_KEY), dict) else None
    if not guard_candidate:
        return skipped(session, profile, out_dir, "remote_forbidden_token_guard_missing")

    speaker_rows = read_jsonl(session / "derived/preprocess/echo/speaker_state.jsonl")
    dialogue = load_dialogue(session, profile)
    rows: list[dict[str, Any]] = []
    for index, source_row in enumerate(guard_candidate.get("remote_only_rows") or [], start=1):
        if isinstance(source_row, dict):
            rows.append(
                remote_evidence_row(
                    session=session,
                    index=index,
                    source_row=source_row,
                    speaker_rows=speaker_rows,
                    dialogue=dialogue,
                )
            )
    for index, source_row in enumerate(guard_candidate.get("local_only_rows") or [], start=1):
        if isinstance(source_row, dict):
            rows.append(
                local_gate_row(
                    session=session,
                    index=index,
                    source_row=source_row,
                    speaker_rows=speaker_rows,
                    dialogue=dialogue,
                )
            )
    summary = build_summary(
        session=session,
        profile=profile,
        out_dir=out_dir,
        leak_report=leak_report,
        preservation_report=preservation_report,
        rows=rows,
        status="ok",
    )
    write_jsonl(out_dir / "remote_forbidden_evidence.jsonl", rows)
    write_json(out_dir / "remote_forbidden_summary.json", summary)
    (out_dir / "remote_forbidden_review.md").write_text(render_markdown(summary, rows), encoding="utf-8")
    print(f"remote_forbidden_summary: {out_dir / 'remote_forbidden_summary.json'}")
    print(f"evidence_rows: {len(rows)}")
    print(f"gate_passed: {summary['gates']['passed']}")
    print(f"reason: {summary['gates']['reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
