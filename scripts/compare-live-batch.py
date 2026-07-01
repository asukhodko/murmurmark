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
SCRIPT_VERSION = "0.2.0"
TOKEN_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9_+-]+")


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


def gate(name: str, status: str, reason: str, evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    row: dict[str, Any] = {"name": name, "status": status, "reason": reason}
    if evidence:
        row["evidence"] = evidence
    return row


def parity_gates(
    *,
    blockers: list[str],
    duplicate_count: int,
    recall: float | None,
    batch_quality: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    gates: list[dict[str, Any]] = [
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
    batch_metrics = {
        "unrepaired_long_mic_crossings_count": metric_value(batch_quality, "unrepaired_long_mic_crossings_count"),
        "local_only_island_recall": metric_value(batch_quality, "local_only_island_recall"),
        "remote_duplicate_in_me_seconds": metric_value(batch_quality, "remote_duplicate_in_me_seconds"),
        "needs_review_count": metric_value(batch_quality, "needs_review_count"),
        "cross_role_overlap_gt2_seconds": metric_value(batch_quality, "cross_role_overlap_gt2_seconds"),
    }
    for name in ("order_risk", "local_recall", "remote_duplicate_leak", "review_burden", "missing_boundary_speech"):
        gates.append(
            gate(
                name,
                "not_evaluated",
                "near-realtime shadow v1 does not yet produce batch-grade profile metrics for this gate",
                {"batch_metrics": batch_metrics},
            )
        )
    return gates


def main() -> int:
    args = parse_args()
    session = args.session.expanduser().resolve()
    live_report_path = session / "derived/live/live_pipeline_report.json"
    chunks_path = session / "derived/live/chunks.jsonl"
    comparison_path = session / "derived/live/live_batch_comparison.json"
    live_report = read_json(live_report_path)
    chunks = read_jsonl(chunks_path)
    transcript_path = selected_transcript_path(session)
    profile = selected_profile(session)
    batch_quality_path = quality_report_path(session, profile)
    batch_quality = read_json(batch_quality_path) if batch_quality_path else None
    final_text = transcript_path.read_text(encoding="utf-8", errors="ignore") if transcript_path else ""
    live_text = "\n".join(chunk_text(row) for row in chunks)
    live_tokens = tokens(live_text)
    final_tokens = tokens(final_text)
    recall = bag_recall(live_tokens, final_tokens)
    duplicate_count = duplicate_adjacent_chunks(chunks)
    blockers: list[str] = []
    warnings: list[str] = []
    if live_report is None:
        blockers.append("live_report_missing")
    if not chunks:
        blockers.append("live_chunks_missing")
    if transcript_path is None:
        blockers.append("batch_transcript_missing")
    if duplicate_count > 0:
        warnings.append("adjacent_live_chunk_duplicates_detected")
    if recall is not None and recall < 0.60:
        warnings.append("low_live_token_recall_in_batch")
    gates = parity_gates(
        blockers=blockers,
        duplicate_count=duplicate_count,
        recall=recall,
        batch_quality=batch_quality,
    )
    gate_statuses = {str(row.get("status")) for row in gates}
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
            "batch_quality_report": rel(batch_quality_path, session) if batch_quality_path else None,
            "selected_batch_profile": profile,
        },
        "metrics": {
            "live_chunks": len(chunks),
            "live_token_count": len(live_tokens),
            "batch_token_count": len(final_tokens),
            "live_token_recall_in_batch": round(recall, 6) if recall is not None else None,
            "adjacent_duplicate_chunk_count": duplicate_count,
            "batch_authoritative": True,
        },
        "parity_gates": {
            "status": "not_promotable" if gate_statuses - {"passed"} else "passed_but_shadow_locked",
            "gates": gates,
        },
        "recommended_next": "murmurmark status " + str(session),
    }
    write_json(comparison_path, payload)
    print(f"live_batch_comparison: {comparison_path}")
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
