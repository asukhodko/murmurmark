#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


SCHEMA_AUDIT = "murmurmark.local_recall_audit/v1"
SCHEMA_ITEM = "murmurmark.local_recall_item/v1"
SCRIPT_VERSION = "0.4.1"
ACK_TOKENS = {
    "ага",
    "угу",
    "ок",
    "окей",
    "хорошо",
    "понял",
    "поняла",
    "понятно",
    "ясно",
    "ладно",
    "супер",
    "класс",
    "да",
    "нет",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit unrecovered local-only islands from timeline repair.")
    parser.add_argument("session", type=Path)
    parser.add_argument(
        "--profile",
        default="auto",
        help="Timeline repair profile to inspect. Default: auto, preferring shadow_v2.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Output directory. Default: SESSION/derived/audit/local-recall.",
    )
    return parser.parse_args()


def suffix(profile: str) -> str:
    return "" if profile == "current" else f".{profile}"


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


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")


def resolve_profile(session: Path, requested: str) -> str:
    resolved = session / "derived/transcript-simple/whisper-cpp/resolved"
    if requested != "auto":
        return requested
    if (resolved / "timeline_repair_report.shadow_v2.json").exists():
        return "shadow_v2"
    if (resolved / "timeline_repair_report.json").exists():
        return "current"
    return "shadow_v2"


def interval_overlap(left: tuple[int, int], right: tuple[int, int]) -> int:
    return max(0, min(left[1], right[1]) - max(left[0], right[0]))


def format_time(seconds: float) -> str:
    total = max(0, int(seconds))
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def normalize_text(text: Any) -> str:
    value = str(text or "").lower().replace("ё", "е")
    value = re.sub(r"[^0-9a-zа-я_./+-]+", " ", value)
    return " ".join(token for token in value.split() if token)


def content_tokens(text: Any) -> list[str]:
    stop = {
        "а",
        "и",
        "ну",
        "да",
        "вот",
        "это",
        "как",
        "то",
        "же",
        "там",
        "тут",
        "у",
        "в",
        "на",
        "не",
        "что",
        "мы",
        "вы",
        "я",
        "он",
        "она",
        "они",
        "по",
        "за",
        "из",
        "с",
        "к",
        "про",
        "для",
        "или",
        "если",
        "бы",
        "быть",
    }
    return [token for token in normalize_text(text).split() if token not in stop and len(token) > 2]


def token_containment(left: Any, right: Any) -> float:
    left_tokens = set(content_tokens(left))
    if not left_tokens:
        return 0.0
    right_tokens = set(content_tokens(right))
    return len(left_tokens & right_tokens) / max(1, len(left_tokens))


def has_work_marker(text: Any) -> bool:
    value = normalize_text(text)
    markers = (
        "надо",
        "нужно",
        "сделаю",
        "сделаем",
        "давай",
        "давайте",
        "добав",
        "решили",
        "договорились",
        "согласовали",
        "проблем",
        "риск",
        "блокер",
        "вопрос",
        "задач",
        "заявк",
        "тикет",
        "таск",
        "alert",
        "алерт",
        "проверь",
        "посмотрю",
    )
    return any(marker in value for marker in markers)


def is_acknowledgement_text(text: Any) -> bool:
    normalized = normalize_text(text)
    if not normalized:
        return False
    tokens = normalized.split()
    if len(tokens) > 3:
        return False
    meaningful = [token for token in tokens if token not in {"я", "ну", "вот", "это"}]
    return bool(meaningful) and all(token in ACK_TOKENS for token in meaningful)


def state_rows_for_interval(rows: list[dict[str, Any]], start_sec: float, end_sec: float) -> list[tuple[dict[str, Any], float]]:
    matches: list[tuple[dict[str, Any], float]] = []
    for row in rows:
        row_start = float(row.get("start", 0.0) or 0.0)
        row_end = float(row.get("end", row_start) or row_start)
        overlap = max(0.0, min(end_sec, row_end) - max(start_sec, row_start))
        if overlap > 0:
            matches.append((row, overlap))
    return matches


def weighted_average(rows: list[tuple[dict[str, Any], float]], key: str, default: float) -> float:
    total = sum(weight for _, weight in rows)
    if total <= 0:
        return default
    values: list[float] = []
    weights: list[float] = []
    for row, weight in rows:
        value = row.get(key)
        if value is None:
            continue
        try:
            values.append(float(value))
            weights.append(weight)
        except (TypeError, ValueError):
            continue
    if not values:
        return default
    return sum(value * weight for value, weight in zip(values, weights)) / max(1e-9, sum(weights))


def state_ratio(rows: list[tuple[dict[str, Any], float]], predicate: Any) -> float:
    total = sum(weight for _, weight in rows)
    if total <= 0:
        return 0.0
    matched = sum(weight for row, weight in rows if predicate(row))
    return matched / total


def state_summary(speaker_states: list[dict[str, Any]], start_ms: int, end_ms: int) -> dict[str, Any]:
    rows = state_rows_for_interval(speaker_states, start_ms / 1000.0, end_ms / 1000.0)
    local_ratio = state_ratio(rows, lambda row: row.get("state") == "local_only" or row.get("action") == "pass_raw_local_only")
    double_talk_ratio = state_ratio(rows, lambda row: row.get("state") == "double_talk" or "overlap" in str(row.get("action") or ""))
    remote_ratio = state_ratio(rows, lambda row: str(row.get("state") or "").startswith("remote") or "remote_active" in str(row.get("action") or ""))
    silence_ratio = state_ratio(rows, lambda row: "silence" in str(row.get("state") or "") or "silence" in str(row.get("action") or ""))
    mic_db = weighted_average(rows, "mic_db", -120.0)
    remote_db = weighted_average(rows, "remote_db", -120.0)
    confidence = weighted_average(rows, "confidence", 0.0)
    return {
        "local_only_ratio": round(local_ratio, 6),
        "double_talk_ratio": round(double_talk_ratio, 6),
        "remote_active_ratio": round(remote_ratio, 6),
        "silence_ratio": round(silence_ratio, 6),
        "mic_db_mean": round(mic_db, 3),
        "remote_db_mean": round(remote_db, 3),
        "state_confidence_mean": round(confidence, 6),
    }


def covered_by_child(island: tuple[int, int], children: list[dict[str, Any]]) -> bool:
    duration = max(1, island[1] - island[0])
    covered = 0
    for child in children:
        try:
            child_interval = (int(child.get("start_ms")), int(child.get("end_ms")))
        except (TypeError, ValueError):
            continue
        covered += interval_overlap(island, child_interval)
    return covered / duration >= 0.5


def parse_interval(row: dict[str, Any], start_key: str, end_key: str) -> tuple[int, int] | None:
    try:
        start = int(row.get(start_key))
        end = int(row.get(end_key))
    except (TypeError, ValueError):
        return None
    if end <= start:
        return None
    return (start, end)


def nearest_boundary_gap_ms(point_ms: int, intervals: list[tuple[int, int]]) -> int | None:
    gaps: list[int] = []
    for start_ms, end_ms in intervals:
        gaps.append(abs(point_ms - start_ms))
        gaps.append(abs(point_ms - end_ms))
    return min(gaps) if gaps else None


def boundary_features(example: dict[str, Any], island: tuple[int, int], children: list[dict[str, Any]]) -> dict[str, Any]:
    start_ms, end_ms = island
    parent_start = int(example.get("parent_start_ms", start_ms) or start_ms)
    parent_end = int(example.get("parent_end_ms", end_ms) or end_ms)
    child_intervals = [
        interval
        for child in children
        if isinstance(child, dict)
        for interval in [parse_interval(child, "start_ms", "end_ms")]
        if interval is not None
    ]
    remote_rows = example.get("remote_overlaps") if isinstance(example.get("remote_overlaps"), list) else []
    remote_guarded = [
        interval
        for row in remote_rows
        if isinstance(row, dict)
        for interval in [parse_interval(row, "guarded_start_ms", "guarded_end_ms")]
        if interval is not None
    ]
    start_offset = max(0, start_ms - parent_start)
    end_offset = max(0, parent_end - end_ms)
    nearest_child = min(
        (
            gap
            for interval in child_intervals
            for gap in (abs(start_ms - interval[1]), abs(end_ms - interval[0]))
        ),
        default=None,
    )
    remote_start_gap = nearest_boundary_gap_ms(start_ms, remote_guarded)
    remote_end_gap = nearest_boundary_gap_ms(end_ms, remote_guarded)
    remote_gap_values = [gap for gap in (remote_start_gap, remote_end_gap) if gap is not None]
    nearest_remote_guard = min(remote_gap_values) if remote_gap_values else None
    duration_ms = max(0, end_ms - start_ms)
    near_parent_boundary = start_offset <= 1_000 or end_offset <= 1_000
    adjacent_child = nearest_child is not None and nearest_child <= 350
    adjacent_remote_guard = nearest_remote_guard is not None and nearest_remote_guard <= 350
    boundary_fragment = duration_ms <= 900 and (near_parent_boundary or adjacent_child or adjacent_remote_guard)
    return {
        "start_offset_from_parent_ms": start_offset,
        "end_offset_from_parent_ms": end_offset,
        "near_parent_boundary": near_parent_boundary,
        "nearest_child_boundary_ms": nearest_child,
        "adjacent_to_child": adjacent_child,
        "nearest_remote_guard_boundary_ms": nearest_remote_guard,
        "adjacent_to_remote_guard": adjacent_remote_guard,
        "boundary_fragment": boundary_fragment,
    }


def classify_item(
    duration_sec: float,
    parent_text: str,
    state: dict[str, Any],
    remote_overlap_text: str,
    boundary: dict[str, Any],
) -> tuple[str, str, float]:
    local_ratio = float(state.get("local_only_ratio", 0.0) or 0.0)
    double_ratio = float(state.get("double_talk_ratio", 0.0) or 0.0)
    remote_ratio = float(state.get("remote_active_ratio", 0.0) or 0.0)
    mic_db = float(state.get("mic_db_mean", -120.0) or -120.0)
    token_count = len(content_tokens(parent_text))
    marker = has_work_marker(parent_text)
    ack = is_acknowledgement_text(parent_text)
    remote_coverage = token_containment(parent_text, remote_overlap_text)

    if duration_sec < 0.55:
        return "likely_harmless_short", "local island is shorter than 550 ms", 0.74
    if token_count >= 3 and remote_coverage >= 0.70:
        return "likely_harmless_remote_covered", "unrecovered local island text is already covered by remote transcript", 0.82
    if boundary.get("boundary_fragment") and token_count >= 3 and remote_coverage >= 0.50:
        return (
            "likely_harmless_remote_boundary_covered",
            "short boundary island has substantial remote transcript coverage",
            0.8,
        )
    if ack and not marker and duration_sec <= 1.6:
        return "likely_harmless_ack_fragment", "short acknowledgement island has no work marker or unique meeting content", 0.79
    if boundary.get("boundary_fragment") and not marker:
        return "likely_harmless_boundary_fragment", "short unrecovered island sits on a parent, child, or remote guard boundary", 0.77
    if local_ratio >= 0.55 and mic_db > -52.0 and duration_sec >= 1.2:
        confidence = 0.84 if marker or token_count >= 4 else 0.78
        return "possible_lost_me", "strong local-only evidence was not recovered as a Me utterance", confidence
    if (local_ratio + double_ratio) >= 0.55 and duration_sec >= 0.9 and mic_db > -55.0:
        return "needs_review", "local or double-talk evidence is present but not strong enough for an automatic call", 0.68
    if marker and duration_sec >= 0.8 and mic_db > -58.0:
        return "needs_review", "parent text contains work markers near an unrecovered island", 0.64
    if remote_ratio >= 0.65 and local_ratio < 0.25:
        return "likely_harmless_remote_guard", "island is mostly inside remote-active state", 0.76
    if mic_db <= -55.0:
        return "likely_harmless_weak_audio", "mic energy is weak in the unrecovered island", 0.72
    return "needs_review", "unrecovered island has inconclusive local evidence", 0.6


def audit_items(examples: list[dict[str, Any]], speaker_states: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    index = 1
    for example in examples:
        islands = example.get("local_islands")
        if not isinstance(islands, list):
            continue
        children = example.get("children") if isinstance(example.get("children"), list) else []
        for island in islands:
            if not isinstance(island, list) or len(island) != 2:
                continue
            try:
                interval = (int(island[0]), int(island[1]))
            except (TypeError, ValueError):
                continue
            if covered_by_child(interval, children):
                continue
            start_ms, end_ms = interval
            duration_sec = max(0.0, (end_ms - start_ms) / 1000.0)
            parent_text = str(example.get("parent_text") or "")
            state = state_summary(speaker_states, start_ms, end_ms)
            remote_overlaps = example.get("remote_overlaps") if isinstance(example.get("remote_overlaps"), list) else []
            remote_overlap_text = " ".join(str(row.get("text") or "") for row in remote_overlaps if isinstance(row, dict))
            boundary = boundary_features(example, interval, children)
            label, reason, confidence = classify_item(duration_sec, parent_text, state, remote_overlap_text, boundary)
            items.append(
                {
                    "schema": SCHEMA_ITEM,
                    "item_id": f"local_recall_{index:04d}",
                    "label": label,
                    "confidence": round(confidence, 3),
                    "reason": reason,
                    "parent_candidate_id": example.get("parent_candidate_id"),
                    "parent_action": example.get("action"),
                    "parent_start_sec": round(float(example.get("parent_start_ms", 0) or 0) / 1000.0, 3),
                    "parent_end_sec": round(float(example.get("parent_end_ms", 0) or 0) / 1000.0, 3),
                    "start_sec": round(start_ms / 1000.0, 3),
                    "end_sec": round(end_ms / 1000.0, 3),
                    "duration_sec": round(duration_sec, 3),
                    "parent_text": parent_text,
                    "parent_content_token_count": len(content_tokens(parent_text)),
                    "parent_has_work_marker": has_work_marker(parent_text),
                    "parent_is_acknowledgement": is_acknowledgement_text(parent_text),
                    "state": state,
                    "boundary": boundary,
                    "matched_remote_candidate_ids": [str(row.get("candidate_id")) for row in remote_overlaps if isinstance(row, dict)],
                    "remote_overlap_text_sample": remote_overlap_text[:280],
                    "remote_overlap_text_containment": round(token_containment(parent_text, remote_overlap_text), 6),
                }
            )
            index += 1
    return items


def summarize(report: dict[str, Any] | None, examples: list[dict[str, Any]], items: list[dict[str, Any]]) -> dict[str, Any]:
    metrics = report.get("metrics") if isinstance(report, dict) else {}
    if not isinstance(metrics, dict):
        metrics = {}
    by_label = Counter(str(item.get("label") or "unknown") for item in items)
    seconds_by_label: Counter[str] = Counter()
    for item in items:
        seconds_by_label[str(item.get("label") or "unknown")] += float(item.get("duration_sec", 0.0) or 0.0)

    possible_lost = seconds_by_label["possible_lost_me"]
    review = seconds_by_label["needs_review"]
    harmful_or_review = possible_lost + review
    missing_count = len(items)
    expected_missing = None
    island_count = metrics.get("local_only_island_count")
    recovered_count = metrics.get("local_only_island_recovered_count")
    try:
        expected_missing = int(island_count) - int(recovered_count)
    except (TypeError, ValueError):
        expected_missing = None
    if harmful_or_review >= 5.0 or by_label["possible_lost_me"] > 0:
        next_step = "review_local_recall_items"
        blocking = True
    elif missing_count > 0:
        next_step = "local_recall_risk_explained"
        blocking = False
    else:
        next_step = "ok"
        blocking = False

    return {
        "timeline_metrics": metrics,
        "timeline_example_count": len(examples),
        "expected_missing_island_count": expected_missing,
        "audited_missing_island_count": missing_count,
        "audit_count_matches_timeline_metrics": expected_missing is None or expected_missing == missing_count,
        "by_label": {
            label: {
                "count": by_label[label],
                "seconds": round(seconds_by_label[label], 3),
            }
            for label in sorted(by_label)
        },
        "possible_lost_me_count": by_label["possible_lost_me"],
        "possible_lost_me_seconds": round(possible_lost, 3),
        "needs_review_count": by_label["needs_review"],
        "needs_review_seconds": round(review, 3),
        "likely_harmless_seconds": round(sum(seconds for label, seconds in seconds_by_label.items() if label.startswith("likely_harmless")), 3),
        "meaningful_review_seconds": round(harmful_or_review, 3),
        "blocking_low_local_recall": blocking,
        "recommended_next_step": next_step,
    }


def write_review(path: Path, session: Path, profile: str, summary: dict[str, Any], items: list[dict[str, Any]]) -> None:
    lines = [
        "# Local Recall Audit",
        "",
        f"Session: `{session}`",
        f"Profile: `{profile}`",
        f"Recommendation: `{summary.get('recommended_next_step')}`",
        f"Blocking low local recall: `{str(summary.get('blocking_low_local_recall')).lower()}`",
        "",
        "## Summary",
        "",
        f"- Missing islands: `{summary.get('audited_missing_island_count')}`",
        f"- Possible lost Me: `{summary.get('possible_lost_me_count')}` / `{summary.get('possible_lost_me_seconds')}` sec",
        f"- Needs review: `{summary.get('needs_review_count')}` / `{summary.get('needs_review_seconds')}` sec",
        f"- Likely harmless: `{summary.get('likely_harmless_seconds')}` sec",
        "",
    ]
    by_label = summary.get("by_label") if isinstance(summary.get("by_label"), dict) else {}
    if by_label:
        lines += ["## Labels", ""]
        for label, payload in sorted(by_label.items()):
            if isinstance(payload, dict):
                lines.append(f"- `{label}`: `{payload.get('count')}` / `{payload.get('seconds')}` sec")
        lines.append("")
    risky = [item for item in items if item.get("label") in {"possible_lost_me", "needs_review"}]
    risky.sort(key=lambda item: (str(item.get("label")) != "possible_lost_me", -float(item.get("duration_sec", 0.0) or 0.0)))
    if risky:
        lines += ["## Review Items", ""]
        for item in risky[:20]:
            start = float(item.get("start_sec", 0.0) or 0.0)
            end = float(item.get("end_sec", start) or start)
            duration = max(0.5, end - start)
            lines += [
                f"### {item.get('item_id')} `{item.get('label')}` {format_time(start)}-{format_time(end)}",
                "",
                f"- Confidence: `{item.get('confidence')}`",
                f"- Reason: {item.get('reason')}",
                f"- Parent candidate: `{item.get('parent_candidate_id')}`",
                f"- State: `{item.get('state')}`",
                f"- Parent text: {item.get('parent_text')}",
                f"- Quick check: `ffplay -hide_banner -loglevel error -ss {max(0.0, start - 1.0):.3f} -t {duration + 2.0:.3f} \"{session}/audio/mic/000001.caf\"`",
                "",
            ]
    else:
        lines += ["No possible lost-Me or review-needed local islands were found.", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    session = args.session
    profile = resolve_profile(session, args.profile)
    out_dir = args.out_dir or session / "derived/audit/local-recall"
    resolved = session / "derived/transcript-simple/whisper-cpp/resolved"
    report_path = resolved / f"timeline_repair_report{suffix(profile)}.json"
    examples_path = resolved / f"timeline_repair_examples{suffix(profile)}.jsonl"
    speaker_state_path = session / "derived/preprocess/echo/speaker_state.jsonl"

    report = read_json(report_path)
    examples = read_jsonl(examples_path)
    speaker_states = read_jsonl(speaker_state_path)
    if not report or not examples_path.exists():
        payload = {
            "schema": SCHEMA_AUDIT,
            "version": SCRIPT_VERSION,
            "session": str(session),
            "profile": profile,
            "status": "missing_inputs",
            "inputs": {
                "timeline_repair_report": str(report_path),
                "timeline_repair_examples": str(examples_path),
                "speaker_state": str(speaker_state_path),
            },
            "summary": {
                "blocking_low_local_recall": True,
                "recommended_next_step": "generate_timeline_repair_first",
            },
        }
        write_json(out_dir / "local_recall_audit.json", payload)
        write_jsonl(out_dir / "local_recall_items.jsonl", [])
        write_review(out_dir / "local_recall_review.md", session, profile, payload["summary"], [])
        print(f"local_recall_audit: {out_dir / 'local_recall_audit.json'}")
        print("status: missing_inputs")
        return 1

    items = audit_items(examples, speaker_states)
    summary = summarize(report, examples, items)
    payload = {
        "schema": SCHEMA_AUDIT,
        "version": SCRIPT_VERSION,
        "session": str(session),
        "profile": profile,
        "status": "ok",
        "inputs": {
            "timeline_repair_report": str(report_path),
            "timeline_repair_examples": str(examples_path),
            "speaker_state": str(speaker_state_path),
        },
        "summary": summary,
    }
    write_json(out_dir / "local_recall_audit.json", payload)
    write_jsonl(out_dir / "local_recall_items.jsonl", items)
    write_review(out_dir / "local_recall_review.md", session, profile, summary, items)
    print(f"local_recall_audit: {out_dir / 'local_recall_audit.json'}")
    print(f"missing_islands: {summary['audited_missing_island_count']}")
    print(f"possible_lost_me_seconds: {summary['possible_lost_me_seconds']}")
    print(f"needs_review_seconds: {summary['needs_review_seconds']}")
    print(f"recommendation: {summary['recommended_next_step']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
