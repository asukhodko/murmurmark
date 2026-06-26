#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_VERSION = "0.2.0"
SCHEMA = "murmurmark.review_plan/v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a focused human review plan from operational readiness.")
    parser.add_argument(
        "--operational-readiness",
        type=Path,
        default=Path("sessions/_reports/operational-readiness/operational_readiness_report.json"),
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("sessions/_reports/review-plan"),
    )
    parser.add_argument("--merge-gap-sec", type=float, default=4.0)
    parser.add_argument("--listen-padding-sec", type=float, default=2.0)
    parser.add_argument("--max-clusters", type=int, default=80)
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        value = json.load(file)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")


def safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def fmt_time(seconds: float) -> str:
    total = max(0, int(seconds))
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def compact_text(text: Any, limit: int = 180) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "..."


def review_action(label: str, verdict: str) -> str:
    if label in {"remote_duplicate", "asr_noise"} and verdict == "probable_transcript_error":
        return "confirm_drop_or_keep_me"
    if label == "remote_leak":
        return "check_unique_me_content"
    if label == "lost_me":
        return "check_lost_local_speech"
    if label == "local_recall_needs_review":
        return "check_local_recall_island"
    if label in {"double_talk", "timing_overlap"}:
        return "confirm_benign_overlap"
    return "classify_audio"


def suggested_decision(label: str, verdict: str, confidence: float) -> dict[str, Any]:
    if label in {"remote_duplicate", "asr_noise"} and verdict == "probable_transcript_error":
        level = "high" if confidence >= 0.9 else "medium"
        reason = "probable leaked remote duplicate" if label == "remote_duplicate" else "probable short ASR noise"
        return {
            "suggested_decision": "drop_me",
            "suggested_decision_confidence": level,
            "suggested_decision_reason": f"{reason}; confirm by listening before changing decision",
        }
    if label == "remote_leak":
        return {
            "suggested_decision": "needs_review",
            "suggested_decision_confidence": "medium",
            "suggested_decision_reason": "remote leak may still contain unique local speech; check before dropping",
        }
    if label == "lost_me":
        return {
            "suggested_decision": "needs_review",
            "suggested_decision_confidence": "medium",
            "suggested_decision_reason": "possible missing local speech requires manual classification",
        }
    if label == "local_recall_needs_review":
        return {
            "suggested_decision": "needs_review",
            "suggested_decision_confidence": "low",
            "suggested_decision_reason": "unrecovered local-only island needs a quick local speech check",
        }
    if label in {"double_talk", "timing_overlap"}:
        return {
            "suggested_decision": "keep_me",
            "suggested_decision_confidence": "low",
            "suggested_decision_reason": "likely benign overlap; confirm local speech before clearing review",
        }
    return {
        "suggested_decision": "needs_review",
        "suggested_decision_confidence": "low",
        "suggested_decision_reason": "unclear audio-review class",
    }


def severity(label: str, verdict: str, confidence: float) -> str:
    if verdict == "probable_transcript_error" and label in {"remote_duplicate", "asr_noise"}:
        return "high"
    if verdict == "probable_transcript_error":
        return "medium"
    if confidence >= 0.8:
        return "medium"
    return "low"


def normalize_item(item: dict[str, Any]) -> dict[str, Any]:
    interval = item.get("interval") if isinstance(item.get("interval"), dict) else {}
    start = safe_float(interval.get("start"))
    end = safe_float(interval.get("end"))
    if end < start:
        end = start
    label = str(item.get("label") or "unknown")
    verdict = str(item.get("verdict") or "unknown")
    confidence = safe_float(item.get("confidence"))
    text_rows = item.get("text") if isinstance(item.get("text"), list) else []
    is_local_recall = str(item.get("source") or "") == "local_recall"
    me_ids = [] if is_local_recall else [
        str(row.get("id"))
        for row in text_rows
        if isinstance(row, dict)
        and (str(row.get("source_track") or "").lower() == "mic" or str(row.get("role") or "").lower() == "me")
        and row.get("id")
    ]
    remote_ids = [
        str(row.get("id"))
        for row in text_rows
        if isinstance(row, dict)
        and (str(row.get("source_track") or "").lower() == "remote" or str(row.get("role") or "").lower() == "remote")
        and row.get("id")
    ]
    suggestion = suggested_decision(label, verdict, confidence)
    return {
        "session_id": item.get("session_id"),
        "session": item.get("session"),
        "source": item.get("source") or "audio_review",
        "source_audit_id": item.get("source_audit_id"),
        "label": label,
        "verdict": verdict,
        "confidence": round(confidence, 6),
        "severity": severity(label, verdict, confidence),
        "review_action": review_action(label, verdict),
        **suggestion,
        "priority_score": safe_float(item.get("priority_score")),
        "interval": {
            "start": round(start, 3),
            "end": round(end, 3),
            "duration_sec": round(max(0.0, end - start), 3),
            "start_time": interval.get("start_time") or fmt_time(start),
            "end_time": interval.get("end_time") or fmt_time(end),
        },
        "utterance_ids": item.get("utterance_ids") if isinstance(item.get("utterance_ids"), list) else [],
        "me_utterance_ids": me_ids,
        "remote_utterance_ids": remote_ids,
        "text": item.get("text") if isinstance(item.get("text"), list) else [],
        "commands": item.get("commands") if isinstance(item.get("commands"), dict) else {},
    }


def cluster_items(items: list[dict[str, Any]], merge_gap_sec: float, padding_sec: float) -> list[dict[str, Any]]:
    by_session: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        by_session[str(item.get("session_id") or "unknown")].append(item)

    clusters: list[dict[str, Any]] = []
    cluster_index = 1
    severity_rank = {"high": 3, "medium": 2, "low": 1}

    for session_id, session_items in sorted(by_session.items()):
        session_items.sort(key=lambda row: (safe_float(row["interval"].get("start")), -safe_float(row.get("priority_score"))))
        current: list[dict[str, Any]] = []
        current_start = 0.0
        current_end = 0.0

        def flush() -> None:
            nonlocal cluster_index, current, current_start, current_end
            if not current:
                return
            labels = Counter(str(row.get("label") or "unknown") for row in current)
            actions = Counter(str(row.get("review_action") or "classify_audio") for row in current)
            max_severity = max((str(row.get("severity") or "low") for row in current), key=lambda value: severity_rank.get(value, 0))
            raw_seconds = sum(safe_float(row["interval"].get("duration_sec")) for row in current)
            listen_start = max(0.0, current_start - padding_sec)
            listen_end = current_end + padding_sec
            primary = max(current, key=lambda row: safe_float(row.get("priority_score")))
            clusters.append(
                {
                    "id": f"review_cluster_{cluster_index:04d}",
                    "session_id": session_id,
                    "session": primary.get("session"),
                    "severity": max_severity,
                    "start": round(current_start, 3),
                    "end": round(current_end, 3),
                    "start_time": fmt_time(current_start),
                    "end_time": fmt_time(current_end),
                    "listen_start": round(listen_start, 3),
                    "listen_end": round(listen_end, 3),
                    "estimated_listen_sec": round(max(0.0, listen_end - listen_start), 3),
                    "raw_item_sec": round(raw_seconds, 3),
                    "item_count": len(current),
                    "labels": dict(sorted(labels.items())),
                    "review_actions": dict(sorted(actions.items())),
                    "max_priority_score": round(max(safe_float(row.get("priority_score")) for row in current), 3),
                    "primary_command": primary.get("commands", {}).get("stereo_clean_left_remote_right")
                    or primary.get("commands", {}).get("stereo_mic_left_remote_right")
                    or primary.get("commands", {}).get("mic_raw"),
                    "items": current,
                }
            )
            cluster_index += 1
            current = []

        for item in session_items:
            start = safe_float(item["interval"].get("start"))
            end = safe_float(item["interval"].get("end"))
            if not current:
                current = [item]
                current_start = start
                current_end = end
                continue
            if start <= current_end + merge_gap_sec:
                current.append(item)
                current_end = max(current_end, end)
            else:
                flush()
                current = [item]
                current_start = start
                current_end = end
        flush()

    clusters.sort(key=lambda row: (-severity_rank.get(str(row.get("severity")), 0), -safe_float(row.get("max_priority_score")), str(row.get("session_id"))))
    return clusters


def session_table(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = report.get("session_review_burden")
    if not isinstance(rows, list):
        return {}
    return {str(row.get("session_id")): row for row in rows if isinstance(row, dict)}


def build_plan(report: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    queue = report.get("review_queue") if isinstance(report.get("review_queue"), list) else []
    items = [normalize_item(row) for row in queue if isinstance(row, dict)]
    clusters = cluster_items(items, args.merge_gap_sec, args.listen_padding_sec)
    clusters = clusters[: max(0, args.max_clusters)]
    sessions = session_table(report)
    by_session = Counter(str(cluster.get("session_id")) for cluster in clusters)
    by_label: Counter[str] = Counter()
    by_action: Counter[str] = Counter()
    for item in items:
        by_label[str(item.get("label") or "unknown")] += 1
        by_action[str(item.get("review_action") or "classify_audio")] += 1
    raw_seconds = sum(safe_float(item["interval"].get("duration_sec")) for item in items)
    listen_seconds = sum(safe_float(cluster.get("estimated_listen_sec")) for cluster in clusters)
    return {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": {"name": "build-review-plan", "version": SCRIPT_VERSION},
        "inputs": {
            "operational_readiness": str(args.operational_readiness),
            "operational_verdict": report.get("operational_verdict"),
        },
        "parameters": {
            "merge_gap_sec": args.merge_gap_sec,
            "listen_padding_sec": args.listen_padding_sec,
            "max_clusters": args.max_clusters,
        },
        "summary": {
            "raw_item_count": len(items),
            "cluster_count": len(clusters),
            "sessions_with_review": len(by_session),
            "raw_item_seconds": round(raw_seconds, 3),
            "estimated_listen_seconds": round(listen_seconds, 3),
            "estimated_listen_minutes": round(listen_seconds / 60.0, 2),
            "by_label": dict(sorted(by_label.items())),
            "by_review_action": dict(sorted(by_action.items())),
            "by_session": dict(sorted(by_session.items())),
        },
        "session_review_burden": sessions,
        "clusters": clusters,
        "review_protocol": [
            "Listen to stereo_clean_left_remote_right first.",
            "Use suggested_decision as a hint only; it does not count until copied to decision.",
            "If Me contains only leaked remote speech, mark drop_me.",
            "If Me contains real local speech or intentional repeat, mark keep_me.",
            "If the case is unclear, keep the transcript item and mark needs_review.",
        ],
    }


def decision_template_rows(plan: dict[str, Any]) -> list[dict[str, Any]]:
    sessions = plan.get("session_review_burden") if isinstance(plan.get("session_review_burden"), dict) else {}
    rows: list[dict[str, Any]] = []
    for cluster in plan.get("clusters") or []:
        if not isinstance(cluster, dict):
            continue
        session_id = str(cluster.get("session_id") or "")
        session_row = sessions.get(session_id) if isinstance(sessions.get(session_id), dict) else {}
        for item in cluster.get("items") or []:
            if not isinstance(item, dict):
                continue
            allowed_decisions = (
                ["keep_me", "needs_review", "skip"]
                if item.get("source") == "local_recall"
                else ["drop_me", "keep_me", "needs_review", "skip"]
            )
            rows.append(
                {
                    "schema": "murmurmark.review_decision/v1",
                    "status": "todo",
                    "decision": "todo",
                    "allowed_decisions": allowed_decisions,
                    "session_id": session_id,
                    "session": cluster.get("session"),
                    "input_profile": session_row.get("selected_profile"),
                    "cluster_id": cluster.get("id"),
                    "source": item.get("source") or "audio_review",
                    "source_audit_id": item.get("source_audit_id"),
                    "label": item.get("label"),
                    "verdict": item.get("verdict"),
                    "confidence": item.get("confidence"),
                    "review_action": item.get("review_action"),
                    "suggested_decision": item.get("suggested_decision"),
                    "suggested_decision_confidence": item.get("suggested_decision_confidence"),
                    "suggested_decision_reason": item.get("suggested_decision_reason"),
                    "interval": item.get("interval"),
                    "me_utterance_ids": item.get("me_utterance_ids") or [],
                    "remote_utterance_ids": item.get("remote_utterance_ids") or [],
                    "utterance_ids": item.get("utterance_ids") or [],
                    "text": item.get("text") or [],
                    "commands": item.get("commands") or {},
                    "reviewer": "",
                    "notes": "",
                }
            )
    return rows


def write_markdown(path: Path, plan: dict[str, Any]) -> None:
    summary = plan.get("summary") or {}
    lines = [
        "# MurmurMark Review Plan",
        "",
        f"Operational verdict: `{plan.get('inputs', {}).get('operational_verdict')}`",
        "",
        "## Summary",
        "",
        f"- Raw review items: `{summary.get('raw_item_count')}`",
        f"- Review clusters: `{summary.get('cluster_count')}`",
        f"- Sessions with review: `{summary.get('sessions_with_review')}`",
        f"- Estimated listening time: `{summary.get('estimated_listen_minutes')}` min",
        f"- Labels: `{summary.get('by_label')}`",
        "",
        "## Protocol",
        "",
    ]
    for item in plan.get("review_protocol") or []:
        lines.append(f"- {item}")

    sessions = plan.get("session_review_burden") if isinstance(plan.get("session_review_burden"), dict) else {}
    if sessions:
        lines.extend(["", "## Sessions", "", "| Session | Gate | Profile | Review % | Flags |", "|---|---|---|---:|---|"])
        for session_id, row in sorted(sessions.items(), key=lambda pair: -safe_float(pair[1].get("review_burden_ratio"))):
            flags = ", ".join(row.get("risk_flags") or [])
            lines.append(
                f"| `{session_id}` | `{row.get('use_gate')}` | `{row.get('selected_profile')}` | "
                f"{safe_float(row.get('review_burden_ratio')) * 100:.2f} | {flags} |"
            )

    lines.extend(["", "## Clusters", ""])
    for cluster in plan.get("clusters") or []:
        lines.extend(
            [
                f"### {cluster.get('id')} `{cluster.get('session_id')}` {cluster.get('start_time')}-{cluster.get('end_time')}",
                "",
                f"- Severity: `{cluster.get('severity')}`",
                f"- Items: `{cluster.get('item_count')}`, labels: `{cluster.get('labels')}`",
                f"- Review actions: `{cluster.get('review_actions')}`",
                f"- Estimated listen: `{cluster.get('estimated_listen_sec')}` sec",
            ]
        )
        if cluster.get("primary_command"):
            lines.append(f"- Primary: `{cluster.get('primary_command')}`")
        for item in cluster.get("items") or []:
            lines.append(
                f"- `{item.get('source_audit_id')}` `{item.get('label')}` "
                f"{item.get('interval', {}).get('start_time')}-{item.get('interval', {}).get('end_time')} "
                f"action `{item.get('review_action')}`, suggestion `{item.get('suggested_decision')}`"
            )
            if item.get("suggested_decision_reason"):
                lines.append(f"  - suggestion: {item.get('suggested_decision_reason')}")
            for text in (item.get("text") or [])[:2]:
                if not isinstance(text, dict):
                    continue
                lines.append(
                    f"  - {text.get('role')} `{text.get('id')}`: {compact_text(text.get('text'))}"
                )
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    report = read_json(args.operational_readiness)
    plan = build_plan(report, args)
    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(out_dir / "review_plan.json", plan)
    write_jsonl(out_dir / "review_plan_clusters.jsonl", plan["clusters"])
    write_jsonl(out_dir / "review_decisions.template.jsonl", decision_template_rows(plan))
    write_markdown(out_dir / "review_plan.md", plan)
    print(f"review_plan: {out_dir / 'review_plan.json'}")
    print(f"clusters: {len(plan['clusters'])}")
    print(f"estimated_listen_minutes: {plan['summary']['estimated_listen_minutes']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
