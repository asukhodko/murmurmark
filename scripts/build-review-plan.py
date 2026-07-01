#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_VERSION = "0.5.0"
SCHEMA = "murmurmark.review_plan/v1"
GROUPABLE_REVIEW_LANES = {"check_transcript_order", "check_unique_me_content", "classify_audio"}
CROSS_LANE_RELATED_LANES = {"check_unique_me_content", "classify_audio"}
REVIEW_LANE_ORDER = [
    "fast_confirm_drop",
    "check_unique_me_content",
    "check_local_recall",
    "check_transcript_order",
    "confirm_benign",
    "classify_audio",
]

REVIEW_LANES = {
    "fast_confirm_drop": {
        "title": "Fast confirm drop",
        "description": "Likely leaked remote/ASR noise. Listen once; if it is only non-local speech, accept drop_me.",
    },
    "check_unique_me_content": {
        "title": "Check unique Me content",
        "description": "Remote leak/duplicate may cover only part of Me. Keep real local speech; drop remote only when remote is the duplicate.",
    },
    "check_local_recall": {
        "title": "Check local recall",
        "description": "Possible missing local speech from the mic candidate stream. Usually short, but it can affect meaning.",
    },
    "check_transcript_order": {
        "title": "Check transcript order",
        "description": "Long Me turn crosses a Colleagues turn. Check chronology before relying on reply order.",
    },
    "confirm_benign": {
        "title": "Confirm benign overlap",
        "description": "Likely double-talk or timing overlap. Confirm and keep Me when local speech is real.",
    },
    "classify_audio": {
        "title": "Classify audio",
        "description": "No safe shortcut; listen and choose keep_me, needs_review, or skip.",
    },
}


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


def duplicate_drop_hint_allowed(item: dict[str, Any] | None) -> bool:
    features = item.get("review_features") if isinstance(item, dict) and isinstance(item.get("review_features"), dict) else {}
    coverage = safe_float(features.get("me_overlap_coverage"))
    similarity = safe_float(features.get("text_similarity"))
    containment = safe_float(features.get("token_containment"))
    return coverage >= 0.80 and (similarity >= 0.92 or containment >= 0.75)


def review_action(label: str, verdict: str, item: dict[str, Any] | None = None) -> str:
    if label == "remote_duplicate" and verdict == "probable_transcript_error":
        if not duplicate_drop_hint_allowed(item):
            return "check_unique_me_content"
        return "confirm_drop_or_keep_me"
    if label == "asr_noise" and verdict == "probable_transcript_error":
        return "confirm_drop_or_keep_me"
    if label == "remote_leak":
        return "check_unique_me_content"
    if label == "lost_me":
        return "check_lost_local_speech"
    if label == "local_recall_needs_review":
        return "check_local_recall_island"
    if label == "local_recall_repair_inserted":
        return "check_inserted_local_recall_repair"
    if label in {"probable_order_risk", "transcript_order_needs_review"}:
        return "check_transcript_order"
    if label in {"double_talk", "timing_overlap"}:
        return "confirm_benign_overlap"
    return "classify_audio"


def suggested_decision(label: str, verdict: str, confidence: float, item: dict[str, Any] | None = None) -> dict[str, Any]:
    if label == "remote_duplicate" and verdict == "probable_transcript_error":
        if not duplicate_drop_hint_allowed(item):
            features = item.get("review_features") if isinstance(item, dict) and isinstance(item.get("review_features"), dict) else {}
            coverage = safe_float(features.get("me_overlap_coverage"))
            return {
                "suggested_decision": "needs_review",
                "suggested_decision_confidence": "medium",
                "suggested_decision_reason": (
                    "remote duplicate covers only part of the Me utterance"
                    f" (coverage {coverage:.2f}); check unique local content before dropping"
                ),
            }
        level = "high" if confidence >= 0.9 else "medium"
        return {
            "suggested_decision": "drop_me",
            "suggested_decision_confidence": level,
            "suggested_decision_reason": "probable leaked remote duplicate; confirm by listening before changing decision",
        }
    if label == "asr_noise" and verdict == "probable_transcript_error":
        level = "high" if confidence >= 0.9 else "medium"
        return {
            "suggested_decision": "drop_me",
            "suggested_decision_confidence": level,
            "suggested_decision_reason": "probable short ASR noise; confirm by listening before changing decision",
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
    if label == "local_recall_repair_inserted":
        return {
            "suggested_decision": "needs_review",
            "suggested_decision_confidence": "medium",
            "suggested_decision_reason": "inserted local-recall repair must be confirmed; keep if it is real local speech, drop if it is a false insertion",
        }
    if label in {"probable_order_risk", "transcript_order_needs_review"}:
        return {
            "suggested_decision": "needs_review",
            "suggested_decision_confidence": "medium",
            "suggested_decision_reason": "chronology risk must be checked before export; keep_me clears it only after review",
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


def review_lane(source: str, label: str, action: str, suggestion: dict[str, Any]) -> str:
    if source in {"local_recall", "local_recall_repair"} or label in {
        "lost_me",
        "local_recall_needs_review",
        "local_recall_repair_inserted",
    }:
        return "check_local_recall"
    if source == "transcript_order" or action == "check_transcript_order":
        return "check_transcript_order"
    if suggestion.get("suggested_decision") == "drop_me" and action == "confirm_drop_or_keep_me":
        return "fast_confirm_drop"
    if action == "check_unique_me_content":
        return "check_unique_me_content"
    if action == "confirm_benign_overlap":
        return "confirm_benign"
    return "classify_audio"


def item_list_values(item: dict[str, Any], key: str) -> list[str]:
    values = item.get(key)
    if isinstance(values, list):
        return [str(value) for value in values if value is not None and str(value)]
    return []


def item_text_utterance_ids(item: dict[str, Any], *, role: str) -> list[str]:
    rows = item.get("text")
    if not isinstance(rows, list):
        return []
    ids: list[str] = []
    role = role.lower()
    for row in rows:
        if not isinstance(row, dict):
            continue
        source_track = str(row.get("source_track") or "").lower()
        row_role = str(row.get("role") or "").lower()
        if role == "remote":
            matches = source_track == "remote" or row_role in {"remote", "colleagues"}
        else:
            matches = source_track == "mic" or row_role == "me"
        value = row.get("id")
        if matches and value is not None and str(value):
            ids.append(str(value))
    return ids


def has_remote_utterance(item: dict[str, Any]) -> bool:
    return bool(item_list_values(item, "remote_utterance_ids") or item_text_utterance_ids(item, role="remote"))


def output_allowed_decisions(item: dict[str, Any]) -> list[str]:
    values = item.get("allowed_decisions")
    if isinstance(values, list) and values:
        decisions = [str(value) for value in values]
        if item.get("source") not in {"local_recall", "transcript_order"} and has_remote_utterance(item) and "drop_remote" not in decisions:
            insert_at = decisions.index("drop_me") + 1 if "drop_me" in decisions else 0
            decisions.insert(insert_at, "drop_remote")
        return decisions
    if item.get("source") == "local_recall":
        return ["drop_me", "keep_me", "needs_review", "skip"]
    if item.get("source") == "transcript_order":
        return ["keep_me", "needs_review", "skip"]
    decisions = ["drop_me", "keep_me", "needs_review", "skip"]
    if has_remote_utterance(item):
        decisions.insert(1, "drop_remote")
    return decisions


def first_me_utterance_id(item: dict[str, Any]) -> str:
    me_ids = item_list_values(item, "me_utterance_ids")
    if me_ids:
        return me_ids[0]
    utterance_ids = item_list_values(item, "utterance_ids")
    return utterance_ids[0] if utterance_ids else ""


def me_utterance_group_key(item: dict[str, Any]) -> str:
    me_ids = item_list_values(item, "me_utterance_ids")
    if me_ids:
        return ",".join(me_ids)
    return first_me_utterance_id(item)


def cross_lane_related_key(item: dict[str, Any]) -> str:
    lane = str(item.get("review_lane") or "")
    if lane not in CROSS_LANE_RELATED_LANES:
        return ""
    me_key = me_utterance_group_key(item)
    if not me_key:
        return ""
    session_id = str(item.get("session_id") or item.get("session") or "")
    return f"cross_lane_me_audio:{session_id}:{me_key}"


def review_group_key(item: dict[str, Any]) -> str:
    cross_lane_key = cross_lane_related_key(item)
    if cross_lane_key:
        return cross_lane_key
    lane = str(item.get("review_lane") or "")
    if lane not in GROUPABLE_REVIEW_LANES:
        return ""
    me_key = me_utterance_group_key(item)
    if not me_key:
        return ""
    session_id = str(item.get("session_id") or item.get("session") or "")
    action = str(item.get("review_action") or "")
    if lane == "check_unique_me_content":
        return f"{lane}:{session_id}:{action}:{me_key}"
    label = str(item.get("label") or "")
    allowed = ",".join(sorted(output_allowed_decisions(item)))
    return f"{lane}:{session_id}:{label}:{action}:{allowed}:{me_key}"


def review_group_lane(group: list[dict[str, Any]]) -> str:
    lanes = {str(item.get("review_lane") or "classify_audio") for item in group}
    for lane in REVIEW_LANE_ORDER:
        if lane in lanes:
            return lane
    return str(group[0].get("review_lane") or "classify_audio") if group else "classify_audio"


def review_action_groups(items: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    by_key: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        key = review_group_key(item)
        if not key:
            groups.append([item])
            continue
        group = by_key.get(key)
        if group is None:
            group = []
            by_key[key] = group
            groups.append(group)
        group.append(item)
    return groups


def severity(label: str, verdict: str, confidence: float) -> str:
    if verdict == "probable_transcript_error" and label in {"remote_duplicate", "asr_noise"}:
        return "high"
    if verdict == "probable_transcript_error":
        return "medium"
    if label == "probable_order_risk":
        return "high"
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
    is_transcript_order = str(item.get("source") or "") == "transcript_order"
    source = str(item.get("source") or "audio_review")
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
        and (
            str(row.get("source_track") or "").lower() == "remote"
            or str(row.get("role") or "").lower() in {"remote", "colleagues"}
        )
        and row.get("id")
    ]
    action = review_action(label, verdict, item)
    suggestion = suggested_decision(label, verdict, confidence, item)
    lane = review_lane(source, label, action, suggestion)
    return {
        "session_id": item.get("session_id"),
        "session": item.get("session"),
        "source": source,
        "source_audit_id": item.get("source_audit_id"),
        "input_profile": item.get("input_profile"),
        "label": label,
        "verdict": verdict,
        "confidence": round(confidence, 6),
        "severity": severity(label, verdict, confidence),
        "review_action": action,
        "review_lane": lane,
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
        "review_features": item.get("review_features") if isinstance(item.get("review_features"), dict) else {},
        "text": item.get("text") if isinstance(item.get("text"), list) else [],
        "commands": item.get("commands") if isinstance(item.get("commands"), dict) else {},
        "allowed_decisions": ["keep_me", "needs_review", "skip"] if is_transcript_order else item.get("allowed_decisions"),
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
    by_lane: Counter[str] = Counter()
    by_lane_actions: Counter[str] = Counter()
    by_lane_grouped_rows: Counter[str] = Counter()
    lane_seconds: Counter[str] = Counter()
    for item in items:
        by_label[str(item.get("label") or "unknown")] += 1
        by_action[str(item.get("review_action") or "classify_audio")] += 1
        lane = str(item.get("review_lane") or "classify_audio")
        by_lane[lane] += 1
        lane_seconds[lane] += safe_float(item["interval"].get("duration_sec"))
    action_groups = review_action_groups(items)
    for group in action_groups:
        if not group:
            continue
        lane = review_group_lane(group)
        by_lane_actions[lane] += 1
        by_lane_grouped_rows[lane] += max(0, len(group) - 1)
    raw_seconds = sum(safe_float(item["interval"].get("duration_sec")) for item in items)
    listen_seconds = sum(safe_float(cluster.get("estimated_listen_sec")) for cluster in clusters)
    lanes = {
        lane: {
            **REVIEW_LANES.get(lane, REVIEW_LANES["classify_audio"]),
            "item_count": by_lane.get(lane, 0),
            "action_count": by_lane_actions.get(lane, by_lane.get(lane, 0)),
            "grouped_row_count": by_lane_grouped_rows.get(lane, 0),
            "raw_item_seconds": round(lane_seconds.get(lane, 0.0), 3),
        }
        for lane in sorted(by_lane)
    }
    promotion = report.get("promotion_plan") if isinstance(report.get("promotion_plan"), dict) else {}
    review_queue_strategy = promotion.get("review_queue_strategy") if isinstance(promotion.get("review_queue_strategy"), dict) else {}
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
            "review_action_count": len(action_groups),
            "grouped_review_row_count": sum(max(0, len(group) - 1) for group in action_groups),
            "cluster_count": len(clusters),
            "sessions_with_review": len(by_session),
            "raw_item_seconds": round(raw_seconds, 3),
            "estimated_listen_seconds": round(listen_seconds, 3),
            "estimated_listen_minutes": round(listen_seconds / 60.0, 2),
            "by_label": dict(sorted(by_label.items())),
            "by_review_action": dict(sorted(by_action.items())),
            "by_review_lane": dict(sorted(by_lane.items())),
            "by_review_lane_actions": dict(sorted(by_lane_actions.items())),
            "by_review_lane_grouped_rows": dict(sorted(by_lane_grouped_rows.items())),
            "by_session": dict(sorted(by_session.items())),
        },
        "review_queue_strategy": review_queue_strategy,
        "review_lanes": lanes,
        "session_review_burden": sessions,
        "clusters": clusters,
        "review_protocol": [
            "Listen to stereo_clean_left_remote_right first.",
            "Use suggested_decision as a hint only; it does not count until copied to decision.",
            "If Me contains only leaked remote speech, mark drop_me.",
            "If remote contains a duplicate of the local speaker, mark drop_remote.",
            "If the duplicate covers only part of a longer Me utterance, do not drop it blindly; check unique local content.",
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
            allowed_decisions = output_allowed_decisions(item)
            rows.append(
                {
                    "schema": "murmurmark.review_decision/v1",
                    "status": "todo",
                    "decision": "todo",
                    "allowed_decisions": allowed_decisions,
                    "session_id": session_id,
                    "session": cluster.get("session"),
                    "input_profile": item.get("input_profile") or session_row.get("selected_profile"),
                    "cluster_id": cluster.get("id"),
                    "source": item.get("source") or "audio_review",
                    "source_audit_id": item.get("source_audit_id"),
                    "label": item.get("label"),
                    "verdict": item.get("verdict"),
                    "confidence": item.get("confidence"),
                    "review_action": item.get("review_action"),
                    "review_lane": item.get("review_lane"),
                    "suggested_decision": item.get("suggested_decision"),
                    "suggested_decision_confidence": item.get("suggested_decision_confidence"),
                    "suggested_decision_reason": item.get("suggested_decision_reason"),
                    "interval": item.get("interval"),
                    "review_features": item.get("review_features") if isinstance(item.get("review_features"), dict) else {},
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
        f"- Packed review actions: `{summary.get('review_action_count')}`",
        f"- Grouped review rows saved: `{summary.get('grouped_review_row_count')}`",
        f"- Review clusters: `{summary.get('cluster_count')}`",
        f"- Sessions with review: `{summary.get('sessions_with_review')}`",
        f"- Estimated listening time: `{summary.get('estimated_listen_minutes')}` min",
        f"- Labels: `{summary.get('by_label')}`",
        f"- Lanes: `{summary.get('by_review_lane')}`",
        "",
        "## Protocol",
        "",
    ]
    for item in plan.get("review_protocol") or []:
        lines.append(f"- {item}")

    strategy = plan.get("review_queue_strategy") if isinstance(plan.get("review_queue_strategy"), dict) else {}
    if strategy:
        commands = strategy.get("commands") if isinstance(strategy.get("commands"), dict) else {}
        after_first = (
            strategy.get("after_first_lane_estimate")
            if isinstance(strategy.get("after_first_lane_estimate"), dict)
            else {}
        )
        lines.extend(
            [
                "",
                "## Recommended First Lane",
                "",
                f"- Lane: `{strategy.get('first_recommended_lane')}`",
                f"- Reason: `{strategy.get('first_recommended_reason')}`",
                f"- Quick lane: `{strategy.get('quick_recommended_lane')}`",
                f"- After first lane: `{after_first.get('remaining_items')}` items",
            ]
        )
        if commands.get("build_first_lane_pack"):
            lines.extend(["", "```bash", str(commands["build_first_lane_pack"]), "```"])

    lanes = plan.get("review_lanes") if isinstance(plan.get("review_lanes"), dict) else {}
    if lanes:
        lines.extend(
            [
                "",
                "## Decision Lanes",
                "",
                "Use this section to close the queue safely: the recommended first lane targets the largest blocking lane; quick confirm-drop candidates remain available as a separate lane.",
                "",
                "| Lane | Rows | Actions | Grouped | Raw sec | What to do |",
                "|---|---:|---:|---:|---:|---|",
            ]
        )
        for lane in REVIEW_LANE_ORDER:
            row = lanes.get(lane)
            if not isinstance(row, dict):
                continue
            lines.append(
                f"| `{lane}` | {row.get('item_count')} | {row.get('action_count')} | "
                f"{row.get('grouped_row_count')} | {safe_float(row.get('raw_item_seconds')):.2f} | "
                f"{row.get('description')} |"
            )
        lines.extend(
            [
                "",
                "Suggested commands:",
                "",
                "```bash",
                ".venv/bin/python scripts/review-decisions-cli.py \\",
                "  --template sessions/_reports/review-plan/review_decisions.template.jsonl \\",
                "  --out sessions/_reports/review-plan/review_decisions.jsonl",
                "",
                ".venv/bin/python scripts/apply-review-decisions-batch.py \\",
                "  --decisions sessions/_reports/review-plan/review_decisions.jsonl \\",
                "  --review-template sessions/_reports/review-plan/review_decisions.template.jsonl \\",
                "  --synthesize \\",
                "  --refresh-reports",
                "```",
            ]
        )

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
                f"lane `{item.get('review_lane')}`, action `{item.get('review_action')}`, "
                f"suggestion `{item.get('suggested_decision')}`"
            )
            if item.get("suggested_decision_reason"):
                lines.append(f"  - suggestion: {item.get('suggested_decision_reason')}")
            features = item.get("review_features") if isinstance(item.get("review_features"), dict) else {}
            if features.get("likely_partial_me_utterance"):
                lines.append(
                    "  - safety: partial Me utterance; do not drop the whole utterance unless listening confirms no unique local speech"
                )
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
    print(f"review_actions: {plan['summary']['review_action_count']}")
    print(f"grouped_review_rows: {plan['summary']['grouped_review_row_count']}")
    print(f"estimated_listen_minutes: {plan['summary']['estimated_listen_minutes']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
