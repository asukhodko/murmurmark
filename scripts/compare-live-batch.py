#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = "murmurmark.live_batch_comparison/v1"
SESSION_REPORT_SCHEMA = "murmurmark.live_parity_session_report/v1"
SCRIPT_VERSION = "0.8.0"
TOKEN_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9_+-]+")
CAPTURE_SAFETY_BLOCKERS = {"interrupted_capture", "silent_capture", "sparse_capture"}
CAPTURE_SAFETY_WARNING_MARKERS = (
    "no screencapturekit audio samples",
    "capture produced no audio samples",
    "screencapturekit stream restarted",
    "capture finalized as partial",
    "captured audio covers only",
    "track appears silent or almost silent",
)
BOUNDARY_ISSUE_STATUSES = {"suppressed", "failed", "warning", "blocked", "not_evaluated"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare near-realtime shadow draft with authoritative batch transcript.")
    parser.add_argument("session", type=Path)
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
    with path.open("r", encoding="utf-8") as file:
        for line in file:
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


def rel(path: Path, session: Path) -> str:
    try:
        return str(path.relative_to(session))
    except ValueError:
        return str(path)


def tokens(text: str) -> list[str]:
    return [match.group(0).lower() for match in TOKEN_RE.finditer(text)]


def bag_recall(source_tokens: list[str], target_tokens: list[str]) -> float | None:
    if not source_tokens:
        return None
    source = Counter(source_tokens)
    target = Counter(target_tokens)
    matched = sum(min(count, target[token]) for token, count in source.items())
    return matched / max(1, sum(source.values()))


def chunk_text(row: dict[str, Any]) -> str:
    parts: list[str] = []
    for source in ("mic", "remote"):
        value = row.get(source)
        if isinstance(value, dict):
            text = str(value.get("text") or "")
            if text:
                parts.append(text)
    return " ".join(parts)


def duplicate_adjacent_chunks(chunks: list[dict[str, Any]]) -> int:
    count = 0
    previous: list[str] = []
    for row in chunks:
        current = tokens(chunk_text(row))
        if current and previous and bag_recall(current, previous) is not None and (bag_recall(current, previous) or 0.0) >= 0.8:
            count += 1
        previous = current
    return count


def live_boundary_gate_summary(chunks: list[dict[str, Any]]) -> dict[str, Any]:
    status_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    issue_count = 0
    suppressed_count = 0
    examples: list[dict[str, Any]] = []
    for row in chunks:
        chunk_index = row.get("index")
        for source in ("mic", "remote"):
            source_row = row.get(source)
            if not isinstance(source_row, dict):
                continue
            boundary_gate = source_row.get("live_boundary_gate")
            if not isinstance(boundary_gate, dict):
                continue
            status = str(boundary_gate.get("status") or "unknown")
            reason = str(boundary_gate.get("reason") or "unknown")
            status_counts[status] += 1
            reason_counts[reason] += 1
            if status == "suppressed":
                suppressed_count += 1
            if status in BOUNDARY_ISSUE_STATUSES:
                issue_count += 1
                if len(examples) < 20:
                    examples.append(
                        {
                            "chunk_index": chunk_index,
                            "source": source,
                            "status": status,
                            "reason": reason,
                            "duplicate_score": boundary_gate.get("duplicate_score"),
                            "current_token_recall_in_previous": boundary_gate.get("current_token_recall_in_previous"),
                            "previous_token_recall_in_current": boundary_gate.get("previous_token_recall_in_current"),
                            "hard_start_sec": source_row.get("hard_start_sec"),
                            "hard_end_sec": source_row.get("hard_end_sec"),
                        }
                    )
    return {
        "evaluated_count": sum(status_counts.values()),
        "issue_count": issue_count,
        "suppressed_count": suppressed_count,
        "status_counts": dict(sorted(status_counts.items())),
        "reason_counts": dict(sorted(reason_counts.items())),
        "examples": examples,
    }


def selected_transcript_path(session: Path) -> Path | None:
    readiness = read_json(session / "derived/readiness/session_readiness.json")
    outputs = readiness.get("outputs") if isinstance(readiness, dict) else None
    transcript = outputs.get("transcript") if isinstance(outputs, dict) else None
    path = transcript.get("path") if isinstance(transcript, dict) else None
    if isinstance(path, str):
        candidate = session / path
        if candidate.exists():
            return candidate
    resolved = session / "derived/transcript-simple/whisper-cpp/resolved"
    candidates = sorted(resolved.glob("transcript.*.md"), key=lambda item: item.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def selected_profile(session: Path) -> str | None:
    readiness = read_json(session / "derived/readiness/session_readiness.json")
    if not isinstance(readiness, dict):
        return None
    value = readiness.get("selected_profile") or readiness.get("selected_transcript_profile")
    return str(value) if value else None


def quality_report_path(session: Path, profile: str | None) -> Path | None:
    resolved = session / "derived/transcript-simple/whisper-cpp/resolved"
    candidates: list[Path] = []
    if profile:
        candidates.append(resolved / f"quality_report.{profile}.json")
    candidates.append(resolved / "quality_report.json")
    candidates.extend(sorted(resolved.glob("quality_report.*.json"), key=lambda item: item.stat().st_mtime, reverse=True))
    return next((path for path in candidates if path.exists()), None)


def metric_value(report: dict[str, Any] | None, key: str) -> Any:
    if not isinstance(report, dict):
        return None
    for container in (report, report.get("metrics"), report.get("summary")):
        if isinstance(container, dict) and key in container:
            return container.get(key)
    return None


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
        return gate(
            "capture_safety",
            "blocked",
            f"batch pipeline blocked the session as {pipeline_blocker}",
            evidence,
        )
    if not isinstance(session_json, dict) or not health:
        return gate("capture_safety", "not_evaluated", "session capture health is missing", evidence)
    if partial or explicit_stop is False:
        return gate("capture_safety", "blocked", "capture was partial or did not end through an explicit stop", evidence)
    if safety_warnings or restart_count > 0 or health_summary in {"warning", "partial"}:
        return gate(
            "capture_safety",
            "warning",
            "capture completed with ScreenCaptureKit/audio health warnings",
            evidence,
        )
    return gate("capture_safety", "passed", "capture health is complete and warning-free", evidence)


def interval_overlap(start_a: float, end_a: float, start_b: float, end_b: float) -> float:
    return max(0.0, min(end_a, end_b) - max(start_a, start_b))


def read_utterances(path: Path | None) -> list[dict[str, Any]]:
    data = read_json(path) if path else None
    rows = data.get("utterances") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return []
    utterances: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        start = safe_float(row.get("start"), safe_float(row.get("source_start")))
        end = safe_float(row.get("end"), safe_float(row.get("source_end"), start))
        role = str(row.get("speaker_label") or row.get("role") or "")
        if role.lower() in {"me", "mic"}:
            role = "Me"
        elif role.lower() in {"remote", "colleagues", "colleague"}:
            role = "Colleagues"
        utterances.append(
            {
                "id": str(row.get("id") or f"batch_{index:06d}"),
                "start": start,
                "end": max(end, start),
                "role": role,
                "text": text,
                "tokens": tokens(text),
                "needs_review": bool((row.get("quality") or {}).get("needs_review")) if isinstance(row.get("quality"), dict) else False,
                "quality": row.get("quality") if isinstance(row.get("quality"), dict) else {},
            }
        )
    return utterances


def selected_clean_dialogue_path(session: Path, profile: str | None) -> Path | None:
    resolved = session / "derived/transcript-simple/whisper-cpp/resolved"
    candidates: list[Path] = []
    if profile and profile != "missing":
        candidates.append(resolved / f"clean_dialogue.{profile}.json")
    candidates.append(resolved / "clean_dialogue.json")
    candidates.extend(sorted(resolved.glob("clean_dialogue.*.json"), key=lambda item: item.stat().st_mtime, reverse=True))
    return next((path for path in candidates if path.exists()), None)


def live_turns(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    turns: list[dict[str, Any]] = []
    for row in chunks:
        try:
            index = int(row.get("index") or 0)
        except (TypeError, ValueError):
            index = 0
        for source, role in (("mic", "Me"), ("remote", "Colleagues")):
            source_row = row.get(source)
            if not isinstance(source_row, dict):
                continue
            text = str(source_row.get("text") or "").strip()
            if not text:
                continue
            start = safe_float(source_row.get("hard_start_sec"), safe_float(row.get("start_sec")))
            end = safe_float(source_row.get("hard_end_sec"), safe_float(row.get("end_sec"), start))
            turns.append(
                {
                    "id": f"live_{index:06d}_{source}",
                    "chunk_index": index,
                    "source": source,
                    "role": role,
                    "start": start,
                    "end": max(end, start),
                    "text": text,
                    "tokens": tokens(text),
                }
            )
    return sorted(turns, key=lambda item: (item["start"], item["end"], item["source"]))


def utterance_tokens_in_interval(utterances: list[dict[str, Any]], start: float, end: float, role: str | None = None) -> list[str]:
    result: list[str] = []
    for row in utterances:
        if role and row.get("role") != role:
            continue
        if interval_overlap(start, end, safe_float(row.get("start")), safe_float(row.get("end"))) <= 0:
            continue
        result.extend(row.get("tokens") or [])
    return result


def nested_token_probability(row: dict[str, Any]) -> float | None:
    quality = row.get("quality") if isinstance(row.get("quality"), dict) else {}
    repair = quality.get("repair") if isinstance(quality.get("repair"), dict) else {}
    micro = repair.get("micro_reasr") if isinstance(repair.get("micro_reasr"), dict) else {}
    rows = micro.get("rows") if isinstance(micro.get("rows"), list) else []
    values: list[float] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        try:
            values.append(float(item.get("token_avg_prob")))
        except (TypeError, ValueError):
            continue
    if not values:
        return None
    return sum(values) / len(values)


def suspicious_batch_me_utterance(row: dict[str, Any]) -> bool:
    quality = row.get("quality") if isinstance(row.get("quality"), dict) else {}
    repair = quality.get("repair") if isinstance(quality.get("repair"), dict) else {}
    duration = max(0.0, safe_float(row.get("end")) - safe_float(row.get("start")))
    reason = str(quality.get("decision_reason") or repair.get("reason") or "")
    matched_remote = repair.get("matched_remote_candidate_ids") if isinstance(repair, dict) else None
    token_prob = nested_token_probability(row)
    return bool(
        row.get("role") == "Me"
        and duration <= 1.2
        and len(row.get("tokens") or []) <= 4
        and (
            "crosses_authoritative_remote" in reason
            or (isinstance(matched_remote, list) and len(matched_remote) > 0)
        )
        and (token_prob is None or token_prob < 0.70)
    )


def best_batch_match(turn: dict[str, Any], batch: list[dict[str, Any]]) -> dict[str, Any] | None:
    turn_tokens = turn.get("tokens") or []
    if not turn_tokens:
        return None
    best: dict[str, Any] | None = None
    for row in batch:
        row_tokens = row.get("tokens") or []
        if not row_tokens:
            continue
        score = bag_recall(turn_tokens, row_tokens) or 0.0
        overlap = interval_overlap(
            safe_float(turn.get("start")),
            safe_float(turn.get("end")),
            safe_float(row.get("start")),
            safe_float(row.get("end")),
        )
        if overlap > 0:
            score += 0.15
        if row.get("role") == turn.get("role"):
            score += 0.1
        candidate = {
            "batch_id": row.get("id"),
            "batch_start": row.get("start"),
            "batch_end": row.get("end"),
            "batch_role": row.get("role"),
            "score": round(score, 6),
            "token_recall": round(bag_recall(turn_tokens, row_tokens) or 0.0, 6),
        }
        if best is None or safe_float(candidate["score"]) > safe_float(best["score"]):
            best = candidate
    return best


def assess_live_vs_batch(chunks: list[dict[str, Any]], batch_utterances: list[dict[str, Any]]) -> dict[str, Any]:
    turns = live_turns(chunks)
    local_missing: list[dict[str, Any]] = []
    local_missing_suspicious_batch_me: list[dict[str, Any]] = []
    remote_leak: list[dict[str, Any]] = []
    order_mismatches: list[dict[str, Any]] = []
    matched_turns: list[dict[str, Any]] = []
    for turn in turns:
        match = best_batch_match(turn, batch_utterances)
        if match and safe_float(match.get("token_recall")) >= 0.25:
            matched_turn = {**turn, "match": match}
            matched_turns.append(matched_turn)
    previous: dict[str, Any] | None = None
    for turn in matched_turns:
        if previous is not None:
            previous_batch_start = safe_float((previous.get("match") or {}).get("batch_start"))
            current_batch_start = safe_float((turn.get("match") or {}).get("batch_start"))
            if current_batch_start + 1.0 < previous_batch_start:
                order_mismatches.append(
                    {
                        "previous_live_id": previous.get("id"),
                        "current_live_id": turn.get("id"),
                        "previous_batch_start": round(previous_batch_start, 3),
                        "current_batch_start": round(current_batch_start, 3),
                    }
                )
        previous = turn
    for row in batch_utterances:
        if row.get("role") != "Me" or len(row.get("tokens") or []) < 2:
            continue
        live_tokens_for_me = utterance_tokens_in_interval(turns, safe_float(row.get("start")), safe_float(row.get("end")), "Me")
        recall = bag_recall(row.get("tokens") or [], live_tokens_for_me) or 0.0
        if recall < 0.35:
            missing_row = {
                "batch_id": row.get("id"),
                "start": row.get("start"),
                "end": row.get("end"),
                "duration_sec": round(safe_float(row.get("end")) - safe_float(row.get("start")), 3),
                "recall_in_live_me": round(recall, 6),
                "text": row.get("text"),
            }
            if suspicious_batch_me_utterance(row):
                missing_row["reason"] = "suspicious_short_batch_me_crosses_authoritative_remote"
                local_missing_suspicious_batch_me.append(missing_row)
            else:
                local_missing.append(missing_row)
    for turn in turns:
        if turn.get("role") != "Me" or len(turn.get("tokens") or []) < 3:
            continue
        same_role_tokens = utterance_tokens_in_interval(batch_utterances, safe_float(turn.get("start")), safe_float(turn.get("end")), "Me")
        remote_tokens = utterance_tokens_in_interval(batch_utterances, safe_float(turn.get("start")), safe_float(turn.get("end")), "Colleagues")
        same_recall = bag_recall(turn.get("tokens") or [], same_role_tokens) or 0.0
        remote_recall = bag_recall(turn.get("tokens") or [], remote_tokens) or 0.0
        if remote_recall >= 0.55 and same_recall < 0.35:
            remote_leak.append(
                {
                    "live_id": turn.get("id"),
                    "start": turn.get("start"),
                    "end": turn.get("end"),
                    "duration_sec": round(safe_float(turn.get("end")) - safe_float(turn.get("start")), 3),
                    "same_role_recall": round(same_recall, 6),
                    "remote_role_recall": round(remote_recall, 6),
                    "text": turn.get("text"),
                }
            )
    return {
        "live_turns": turns,
        "matched_turns": matched_turns,
        "local_missing": local_missing,
        "local_missing_suspicious_batch_me": local_missing_suspicious_batch_me,
        "remote_leak": remote_leak,
        "order_mismatches": order_mismatches,
        "metrics": {
            "live_turn_count": len(turns),
            "live_me_turn_count": sum(1 for turn in turns if turn.get("role") == "Me"),
            "live_remote_turn_count": sum(1 for turn in turns if turn.get("role") == "Colleagues"),
            "batch_utterance_count": len(batch_utterances),
            "batch_me_utterance_count": sum(1 for row in batch_utterances if row.get("role") == "Me"),
            "batch_remote_utterance_count": sum(1 for row in batch_utterances if row.get("role") == "Colleagues"),
            "live_order_mismatch_count": len(order_mismatches),
            "live_missing_me_utterance_count": len(local_missing),
            "live_missing_me_seconds": round(sum(safe_float(row.get("duration_sec")) for row in local_missing), 3),
            "live_suspicious_batch_me_missing_count": len(local_missing_suspicious_batch_me),
            "live_suspicious_batch_me_missing_seconds": round(
                sum(safe_float(row.get("duration_sec")) for row in local_missing_suspicious_batch_me),
                3,
            ),
            "live_suspected_remote_leak_in_me_count": len(remote_leak),
            "live_suspected_remote_leak_in_me_seconds": round(sum(safe_float(row.get("duration_sec")) for row in remote_leak), 3),
        },
    }


def gate(name: str, status: str, reason: str, evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    row: dict[str, Any] = {"name": name, "status": status, "reason": reason}
    if evidence:
        row["evidence"] = evidence
    return row


def parity_gates(
    *,
    capture_safety_gate: dict[str, Any],
    blockers: list[str],
    duplicate_count: int,
    boundary_summary: dict[str, Any],
    recall: float | None,
    batch_quality: dict[str, Any] | None,
    live_assessment: dict[str, Any],
    readiness: dict[str, Any] | None,
    outcome: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    gates: list[dict[str, Any]] = [
        capture_safety_gate,
        gate(
            "raw_batch_authoritative",
            "passed",
            "near-realtime remains shadow-only and batch transcript is source of truth",
            {"batch_authoritative": True},
        )
    ]
    if blockers:
        gates.append(gate("required_artifacts", "blocked", "comparison inputs are missing", {"blockers": blockers}))
    else:
        gates.append(gate("required_artifacts", "passed", "live and batch artifacts are present"))
    gates.append(
        gate(
            "duplicate_chunks",
            "passed" if duplicate_count == 0 else "failed",
            "adjacent live chunks should not repeat the same decoded text",
            {"adjacent_duplicate_chunk_count": duplicate_count},
        )
    )
    if recall is None:
        gates.append(gate("live_token_recall", "not_evaluated", "live draft has no decoded tokens"))
    else:
        gates.append(
            gate(
                "live_token_recall",
                "passed" if recall >= 0.60 else "warning",
                "bag-of-words live draft tokens should mostly appear in selected batch transcript",
                {"live_token_recall_in_batch": round(recall, 6)},
            )
        )
    assessment_metrics = live_assessment.get("metrics") if isinstance(live_assessment, dict) else {}
    batch_metrics = {
        "unrepaired_long_mic_crossings_count": metric_value(batch_quality, "unrepaired_long_mic_crossings_count"),
        "local_only_island_recall": metric_value(batch_quality, "local_only_island_recall"),
        "remote_duplicate_in_me_seconds": metric_value(batch_quality, "remote_duplicate_in_me_seconds"),
        "needs_review_count": metric_value(batch_quality, "needs_review_count"),
        "cross_role_overlap_gt2_seconds": metric_value(batch_quality, "cross_role_overlap_gt2_seconds"),
    }
    order_mismatches = int(assessment_metrics.get("live_order_mismatch_count") or 0)
    missing_me_seconds = safe_float(assessment_metrics.get("live_missing_me_seconds"))
    remote_leak_seconds = safe_float(assessment_metrics.get("live_suspected_remote_leak_in_me_seconds"))
    gates.append(
        gate(
            "order_risk",
            "passed" if order_mismatches == 0 else "warning",
            "live turn order should not contradict the selected batch transcript order",
            {"live_order_mismatch_count": order_mismatches, "batch_metrics": batch_metrics},
        )
    )
    gates.append(
        gate(
            "local_recall",
            "passed" if missing_me_seconds <= 0.5 else "warning",
            "batch Me speech should be visible in live mic turns when live draft is used as evidence",
            {
                "live_missing_me_seconds": round(missing_me_seconds, 3),
                "live_missing_me_utterance_count": assessment_metrics.get("live_missing_me_utterance_count"),
                "batch_metrics": batch_metrics,
            },
        )
    )
    gates.append(
        gate(
            "remote_duplicate_leak",
            "passed" if remote_leak_seconds <= 0.5 else "warning",
            "live mic turns should not look like selected batch remote speech",
            {
                "live_suspected_remote_leak_in_me_seconds": round(remote_leak_seconds, 3),
                "live_suspected_remote_leak_in_me_count": assessment_metrics.get("live_suspected_remote_leak_in_me_count"),
                "batch_metrics": batch_metrics,
            },
        )
    )
    outcome_metrics = outcome.get("metrics") if isinstance(outcome, dict) else {}
    review_burden_sec = safe_float(outcome_metrics.get("review_burden_sec"))
    review_burden_ratio = safe_float(outcome_metrics.get("review_burden_ratio"))
    gates.append(
        gate(
            "review_burden",
            "passed" if review_burden_ratio <= 0.03 else ("warning" if review_burden_ratio <= 0.12 else "failed"),
            "selected batch outcome review burden is the maximum allowed burden for live cache promotion",
            {
                "review_burden_sec": round(review_burden_sec, 3),
                "review_burden_ratio": round(review_burden_ratio, 6),
            },
        )
    )
    use_gate = readiness.get("use_gate") if isinstance(readiness, dict) else None
    outcome_value = outcome.get("outcome") if isinstance(outcome, dict) else None
    gates.append(
        gate(
            "selected_notes_readiness",
            "passed" if use_gate == "ready_for_notes" or outcome_value == "ready_for_notes" else "warning",
            "live parity is only promotion-ready when the authoritative batch result is notes-ready",
            {"readiness_use_gate": use_gate, "outcome": outcome_value},
        )
    )
    gates.append(
        gate(
            "chunk_boundary_risks",
            "passed"
            if duplicate_count == 0 and safe_int(boundary_summary.get("issue_count")) == 0
            else ("failed" if duplicate_count > 0 else "warning"),
            "live chunk boundaries should not introduce duplicate or suppressed boundary text",
            {
                "adjacent_duplicate_chunk_count": duplicate_count,
                "live_boundary_gate_issue_count": boundary_summary.get("issue_count"),
                "live_boundary_gate_suppressed_count": boundary_summary.get("suppressed_count"),
                "live_boundary_gate_status_counts": boundary_summary.get("status_counts"),
                "live_boundary_gate_reason_counts": boundary_summary.get("reason_counts"),
            },
        )
    )
    return gates


def check_row(check_id: str, status: str, message: str, evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": check_id,
        "status": status,
        "message": message,
        "evidence": evidence or {},
    }


def live_session_next_commands(session: Path, payload: dict[str, Any]) -> list[str]:
    session_path = str(session)
    comparison_path = session / "derived/live/live_batch_comparison.json"
    coverage_command = (
        "murmurmark corpus live all --min-live-sessions 3 --min-compared-sessions 3 "
        "--min-meaningful-compared-sessions 3 --min-passing-compared-sessions 3 "
        "--max-order-mismatches 0 --max-missing-me-sec 0 --max-remote-in-me-sec 0 "
        "--max-boundary-duplicates 0 --require-passing-gates --fail-on-promotion"
    )
    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
    gates = (payload.get("parity_gates") or {}).get("gates") if isinstance(payload.get("parity_gates"), dict) else []
    non_passing = [gate for gate in gates or [] if isinstance(gate, dict) and gate.get("status") != "passed"]
    commands = [f"murmurmark status {session_path}"]
    if non_passing:
        commands.append(f"jq '.parity_gates.gates[] | select(.status != \"passed\")' {comparison_path}")
    risk_examples = payload.get("risk_examples") if isinstance(payload.get("risk_examples"), dict) else {}
    if any(risk_examples.values()):
        commands.append(f"jq '.risk_examples' {comparison_path}")
    if not metrics.get("meaningful_live_comparison") or not metrics.get("all_parity_gates_passed"):
        commands.append("murmurmark status latest  # live pipeline is quarantined; use normal record/process for real meetings")
    commands.append(coverage_command)
    deduped: list[str] = []
    for command in commands:
        if command not in deduped:
            deduped.append(command)
    return deduped


def build_session_report(session: Path, payload: dict[str, Any]) -> dict[str, Any]:
    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
    blockers = payload.get("blockers") if isinstance(payload.get("blockers"), list) else []
    gates = (payload.get("parity_gates") or {}).get("gates") if isinstance(payload.get("parity_gates"), dict) else []
    non_passing = [gate for gate in gates or [] if isinstance(gate, dict) and gate.get("status") != "passed"]
    checks = [
        check_row(
            "live_artifacts_present",
            "pass" if "live_report_missing" not in blockers and "live_chunks_missing" not in blockers else "block",
            "live report and chunks are required for a live comparison",
            {"blockers": [item for item in blockers if item in {"live_report_missing", "live_chunks_missing"}]},
        ),
        check_row(
            "batch_artifacts_present",
            "pass"
            if "batch_transcript_missing" not in blockers and "batch_clean_dialogue_missing" not in blockers
            else "block",
            "authoritative batch transcript and clean dialogue are required",
            {"blockers": [item for item in blockers if item in {"batch_transcript_missing", "batch_clean_dialogue_missing"}]},
        ),
        check_row(
            "meaningful_two_role_comparison",
            "pass" if metrics.get("meaningful_live_comparison") else "block",
            "live and batch outputs must both contain Me and Colleagues evidence",
            {
                "live_me_turn_count": metrics.get("live_me_turn_count"),
                "live_remote_turn_count": metrics.get("live_remote_turn_count"),
                "batch_me_utterance_count": metrics.get("batch_me_utterance_count"),
                "batch_remote_utterance_count": metrics.get("batch_remote_utterance_count"),
            },
        ),
        check_row(
            "batch_ready_for_notes",
            "pass" if metrics.get("batch_ready_for_notes") else "block",
            "live parity can pass only when the authoritative batch result is notes-ready",
            {
                "batch_use_gate": metrics.get("batch_use_gate"),
                "batch_outcome": metrics.get("batch_outcome"),
            },
        ),
        check_row(
            "all_parity_gates_passed",
            "pass" if metrics.get("all_parity_gates_passed") else "block",
            "every live parity gate must pass before this session can count as a passing comparison",
            {"non_passing_gates": [gate.get("name") for gate in non_passing]},
        ),
        check_row(
            "promotion_blocked",
            "pass" if payload.get("promotion_allowed") is False else "fail",
            "live promotion must stay blocked in v1",
            {"promotion_allowed": payload.get("promotion_allowed")},
        ),
        check_row(
            "suspicious_batch_me_missing",
            "pass" if safe_float(metrics.get("live_suspicious_batch_me_missing_seconds")) == 0 else "warn",
            "suspicious short batch Me missing from live does not count as missing local speech, but should be inspected",
            {
                "seconds": metrics.get("live_suspicious_batch_me_missing_seconds"),
                "count": metrics.get("live_suspicious_batch_me_missing_count"),
            },
        ),
    ]
    hard_statuses = {row["status"] for row in checks if row["id"] != "suspicious_batch_me_missing"}
    if "fail" in hard_statuses:
        status = "failed"
    elif "block" in hard_statuses:
        status = "not_passing"
    elif any(row["status"] == "warn" for row in checks):
        status = "passing_with_warnings"
    else:
        status = "passing_shadow_locked"
    next_commands = live_session_next_commands(session, payload)
    return {
        "schema": SESSION_REPORT_SCHEMA,
        "generator": {"name": "compare-live-batch", "version": SCRIPT_VERSION},
        "created_at": datetime.now(timezone.utc).isoformat(),
        "session": str(session),
        "status": status,
        "promotion_allowed": False,
        "checks": checks,
        "non_passing_gates": non_passing,
        "metrics": metrics,
        "risk_examples": payload.get("risk_examples") if isinstance(payload.get("risk_examples"), dict) else {},
        "recommended_next": next_commands[0],
        "next_commands": next_commands,
    }


def write_session_report_markdown(path: Path, report: dict[str, Any]) -> None:
    metrics = report.get("metrics") if isinstance(report.get("metrics"), dict) else {}
    lines = [
        "# Live Parity Session Report",
        "",
        f"- status: `{report.get('status')}`",
        f"- promotion allowed: `{report.get('promotion_allowed')}`",
        f"- meaningful comparison: `{metrics.get('meaningful_live_comparison')}`",
        f"- all parity gates passed: `{metrics.get('all_parity_gates_passed')}`",
        f"- batch use gate: `{metrics.get('batch_use_gate')}`",
        f"- batch outcome: `{metrics.get('batch_outcome')}`",
        f"- suspicious batch-Me missing seconds: `{metrics.get('live_suspicious_batch_me_missing_seconds')}`",
        "",
        "## Checks",
        "",
    ]
    for row in report.get("checks") or []:
        if not isinstance(row, dict):
            continue
        lines.append(f"- `{row.get('id')}`: `{row.get('status')}` - {row.get('message')}")
    issues = [row for row in report.get("non_passing_gates") or [] if isinstance(row, dict)]
    if issues:
        lines += ["", "## Non-Passing Gates", ""]
        for row in issues:
            lines.append(f"- `{row.get('name')}`: `{row.get('status')}` - {row.get('reason')}")
    risk_examples = report.get("risk_examples") if isinstance(report.get("risk_examples"), dict) else {}
    if any(risk_examples.values()):
        lines += ["", "## Risk Examples", ""]
        for key, values in risk_examples.items():
            if values:
                lines.append(f"- `{key}`: `{len(values)}`")
    lines += ["", "## Next", ""]
    for command in report.get("next_commands") or []:
        lines.append(f"- `{command}`")
    lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    args = parse_args()
    session = args.session.expanduser().resolve()
    live_report_path = session / "derived/live/live_pipeline_report.json"
    chunks_path = session / "derived/live/chunks.jsonl"
    comparison_path = session / "derived/live/live_batch_comparison.json"
    session_report_path = session / "derived/live/live_parity_session_report.json"
    session_report_md_path = session / "derived/live/live_parity_session_report.md"
    live_report = read_json(live_report_path)
    chunks = read_jsonl(chunks_path)
    transcript_path = selected_transcript_path(session)
    profile = selected_profile(session)
    clean_dialogue_path = selected_clean_dialogue_path(session, profile)
    batch_utterances = read_utterances(clean_dialogue_path)
    batch_quality_path = quality_report_path(session, profile)
    batch_quality = read_json(batch_quality_path) if batch_quality_path else None
    readiness = read_json(session / "derived/readiness/session_readiness.json")
    outcome = read_json(session / "derived/outcome/outcome.json")
    final_text = transcript_path.read_text(encoding="utf-8", errors="ignore") if transcript_path else ""
    live_text = "\n".join(chunk_text(row) for row in chunks)
    live_tokens = tokens(live_text)
    final_tokens = tokens(final_text)
    recall = bag_recall(live_tokens, final_tokens)
    duplicate_count = duplicate_adjacent_chunks(chunks)
    boundary_summary = live_boundary_gate_summary(chunks)
    live_assessment = assess_live_vs_batch(chunks, batch_utterances)
    blockers: list[str] = []
    warnings: list[str] = []
    if live_report is None:
        blockers.append("live_report_missing")
    if not chunks:
        blockers.append("live_chunks_missing")
    if transcript_path is None:
        blockers.append("batch_transcript_missing")
    if clean_dialogue_path is None or not batch_utterances:
        blockers.append("batch_clean_dialogue_missing")
    if duplicate_count > 0:
        warnings.append("adjacent_live_chunk_duplicates_detected")
    if safe_int(boundary_summary.get("issue_count")) > 0:
        warnings.append("live_boundary_gate_issues_detected")
    if recall is not None and recall < 0.60:
        warnings.append("low_live_token_recall_in_batch")
    if safe_float((live_assessment.get("metrics") or {}).get("live_missing_me_seconds")) > 0.5:
        warnings.append("live_missing_me_speech_detected")
    if safe_float((live_assessment.get("metrics") or {}).get("live_suspected_remote_leak_in_me_seconds")) > 0.5:
        warnings.append("live_remote_leak_in_me_detected")
    if int((live_assessment.get("metrics") or {}).get("live_order_mismatch_count") or 0) > 0:
        warnings.append("live_order_mismatch_detected")
    capture_safety_gate = build_capture_safety_gate(session)
    if capture_safety_gate.get("status") != "passed":
        warnings.append("capture_safety_not_passed")
    gates = parity_gates(
        capture_safety_gate=capture_safety_gate,
        blockers=blockers,
        duplicate_count=duplicate_count,
        boundary_summary=boundary_summary,
        recall=recall,
        batch_quality=batch_quality,
        live_assessment=live_assessment,
        readiness=readiness,
        outcome=outcome,
    )
    gate_statuses = {str(row.get("status")) for row in gates}
    live_metrics = live_assessment.get("metrics") if isinstance(live_assessment, dict) else {}
    batch_use_gate = readiness.get("use_gate") if isinstance(readiness, dict) else None
    batch_outcome = outcome.get("outcome") if isinstance(outcome, dict) else None
    all_parity_gates_passed = bool(gates) and all(row.get("status") == "passed" for row in gates)
    meaningful_live_comparison = bool(
        not blockers
        and int(live_metrics.get("live_turn_count") or 0) > 0
        and int(live_metrics.get("batch_utterance_count") or 0) > 0
        and int(live_metrics.get("live_me_turn_count") or 0) > 0
        and int(live_metrics.get("live_remote_turn_count") or 0) > 0
        and int(live_metrics.get("batch_me_utterance_count") or 0) > 0
        and int(live_metrics.get("batch_remote_utterance_count") or 0) > 0
    )
    promotion_blockers = [
        "shadow_v1_never_promotes_by_default",
        *[str(row.get("name")) for row in gates if row.get("status") in {"blocked", "failed", "warning", "not_evaluated"}],
    ]
    payload = {
        "schema": SCHEMA,
        "generator": {"name": "compare-live-batch", "version": SCRIPT_VERSION},
        "created_at": datetime.now(timezone.utc).isoformat(),
        "session": str(session),
        "status": "blocked" if blockers else "shadow_compared",
        "promotion_allowed": False,
        "promotion_reason": "near_realtime_shadow_v1_never_promotes_by_default",
        "promotion_blockers": promotion_blockers,
        "blockers": blockers,
        "warnings": warnings,
        "inputs": {
            "live_report": rel(live_report_path, session) if live_report_path.exists() else None,
            "live_chunks": rel(chunks_path, session) if chunks_path.exists() else None,
            "batch_transcript": rel(transcript_path, session) if transcript_path else None,
            "batch_clean_dialogue": rel(clean_dialogue_path, session) if clean_dialogue_path else None,
            "batch_quality_report": rel(batch_quality_path, session) if batch_quality_path else None,
            "readiness": "derived/readiness/session_readiness.json" if (session / "derived/readiness/session_readiness.json").exists() else None,
            "outcome": "derived/outcome/outcome.json" if (session / "derived/outcome/outcome.json").exists() else None,
            "selected_batch_profile": profile,
        },
        "metrics": {
            "live_chunks": len(chunks),
            "live_token_count": len(live_tokens),
            "batch_token_count": len(final_tokens),
            "live_token_recall_in_batch": round(recall, 6) if recall is not None else None,
            "adjacent_duplicate_chunk_count": duplicate_count,
            "live_boundary_gate_evaluated_count": boundary_summary.get("evaluated_count"),
            "live_boundary_gate_issue_count": boundary_summary.get("issue_count"),
            "live_boundary_gate_suppressed_count": boundary_summary.get("suppressed_count"),
            "batch_authoritative": True,
            "batch_use_gate": batch_use_gate,
            "batch_outcome": batch_outcome,
            "batch_ready_for_notes": bool(batch_use_gate == "ready_for_notes" or batch_outcome == "ready_for_notes"),
            "all_parity_gates_passed": all_parity_gates_passed,
            "meaningful_live_comparison": meaningful_live_comparison,
            "capture_safety_status": capture_safety_gate.get("status"),
            "screen_capture_restart_count": (capture_safety_gate.get("evidence") or {}).get("screen_capture_restart_count"),
            "capture_safety_warning_count": (capture_safety_gate.get("evidence") or {}).get("safety_warning_count"),
            **live_metrics,
        },
        "risk_examples": {
            "order_mismatches": (live_assessment.get("order_mismatches") or [])[:20],
            "local_missing": (live_assessment.get("local_missing") or [])[:20],
            "local_missing_suspicious_batch_me": (live_assessment.get("local_missing_suspicious_batch_me") or [])[:20],
            "remote_leak": (live_assessment.get("remote_leak") or [])[:20],
            "boundary_gate_issues": boundary_summary.get("examples") or [],
        },
        "parity_gates": {
            "status": "not_promotable" if gate_statuses - {"passed"} else "passed_but_shadow_locked",
            "gates": gates,
        },
        "outputs": {
            "live_parity_session_report": rel(session_report_path, session),
            "live_parity_session_report_markdown": rel(session_report_md_path, session),
        },
        "recommended_next": "murmurmark status " + str(session),
    }
    session_report = build_session_report(session, payload)
    write_json(comparison_path, payload)
    write_json(session_report_path, session_report)
    write_session_report_markdown(session_report_md_path, session_report)
    print(f"live_batch_comparison: {comparison_path}")
    print(f"live_parity_session_report: {session_report_path}")
    print(f"status: {payload['status']}")
    print("promotion_allowed: false")
    if warnings:
        print("warnings: " + ", ".join(warnings))
    if blockers:
        print("blockers: " + ", ".join(blockers))
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
