#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_VERSION = "0.1.0"
SCHEMA_REPORT = "murmurmark.audio_error_taxonomy_report/v1"
SCHEMA_ITEM = "murmurmark.audio_error_taxonomy_item/v1"

EXPECTED_CLASSES = [
    "remote_duplicate",
    "asr_noise",
    "remote_leak",
    "lost_me",
    "uncertain",
    "double_talk",
    "timing_overlap",
    "likely_reliable",
]

CLASS_ACTIONS = {
    "remote_duplicate": "safe_cleanup_regression",
    "asr_noise": "safe_cleanup_regression",
    "remote_leak": "mark_only_repair_needed",
    "lost_me": "local_recall_repair",
    "uncertain": "needs_stronger_audio_judge",
    "double_talk": "false_positive_guard",
    "timing_overlap": "false_positive_guard",
    "likely_reliable": "false_positive_guard",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize MurmurMark audio error classes and next quality actions.")
    parser.add_argument("--corpus-dir", type=Path, default=Path("sessions/_reports/regression-corpus"))
    parser.add_argument("--audio-judge-dir", type=Path, default=Path("sessions/_reports/audio-judge-v0"))
    parser.add_argument(
        "--session-quality",
        type=Path,
        default=Path("sessions/_reports/session-quality/session_quality_report.json"),
    )
    parser.add_argument("--out-dir", type=Path, default=Path("sessions/_reports/audio-error-taxonomy"))
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


def safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def duration(row: dict[str, Any]) -> float:
    interval = row.get("interval") if isinstance(row.get("interval"), dict) else {}
    return safe_float(interval.get("duration_sec"))


def key_for(row: dict[str, Any]) -> tuple[str, str]:
    return (str(row.get("session_id") or ""), str(row.get("source_audit_id") or row.get("id") or ""))


def by_id(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row.get("id") or ""): row for row in rows if row.get("id")}


def by_source_key(rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    output: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = key_for(row)
        if key[0] and key[1]:
            output[key] = row
    return output


def session_quality_summary(path: Path) -> dict[str, Any]:
    payload = read_json(path) or {}
    sessions = payload.get("sessions") if isinstance(payload.get("sessions"), list) else []
    by_gate = Counter(str(row.get("use_gate") or "unknown") for row in sessions if isinstance(row, dict))
    review_burden = sum(safe_float(row.get("review_burden_sec")) for row in sessions if isinstance(row, dict))
    return {
        "path": str(path),
        "exists": path.exists(),
        "sessions": len(sessions),
        "by_use_gate": dict(sorted(by_gate.items())),
        "review_burden_sec": round(review_burden, 3),
    }


def recommended_action(label: str, readiness_bucket: str, cv_row: dict[str, Any] | None) -> str:
    if label == "lost_me":
        return "local_recall_repair"
    if label == "remote_leak":
        return "mark_only_repair_needed"
    if label in {"remote_duplicate", "asr_noise"}:
        if readiness_bucket == "silver_cleanup_positive":
            return "safe_cleanup_regression"
        if readiness_bucket == "weak_cleanup_positive":
            return "candidate_cleanup_review"
        return "cleanup_guard_or_review"
    if readiness_bucket == "needs_audio_judge" or label == "uncertain":
        return "needs_stronger_audio_judge"
    if label in {"double_talk", "timing_overlap", "likely_reliable"}:
        return "false_positive_guard"
    if cv_row and cv_row.get("cv_correct") is not True:
        return "inspect_judge_confusion"
    return CLASS_ACTIONS.get(label, "collect_more_evidence")


def needs_attention(item: dict[str, Any]) -> bool:
    if item["readiness_bucket"] in {"needs_audio_judge", "mark_only_regression", "weak_cleanup_positive"}:
        return True
    cv = item.get("cv") if isinstance(item.get("cv"), dict) else {}
    if cv and cv.get("cv_correct") is not True:
        return True
    return item["recommended_action"] in {
        "local_recall_repair",
        "mark_only_repair_needed",
        "needs_stronger_audio_judge",
        "inspect_judge_confusion",
    }


def build_items(
    corpus_items: list[dict[str, Any]],
    eval_by_id: dict[str, dict[str, Any]],
    cv_by_id_map: dict[str, dict[str, Any]],
    judge_by_source: dict[tuple[str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in corpus_items:
        item_id = str(row.get("id") or "")
        eval_row = eval_by_id.get(item_id, {})
        cv_row = cv_by_id_map.get(item_id, {})
        judge_row = judge_by_source.get(key_for(row), {})
        label = str(row.get("label") or "unknown")
        bucket = str(eval_row.get("readiness_bucket") or "unknown")
        action = recommended_action(label, bucket, cv_row)
        item = {
            "schema": SCHEMA_ITEM,
            "id": item_id,
            "session_id": row.get("session_id"),
            "source_audit_id": row.get("source_audit_id"),
            "class": label,
            "audio_review_label": row.get("audio_review_label"),
            "verdict": row.get("verdict"),
            "confidence": row.get("confidence"),
            "seconds": round(duration(row), 3),
            "readiness_bucket": bucket,
            "recommended_action": action,
            "target_use": row.get("target_use", []),
            "priority_score": row.get("priority_score"),
            "utterance_ids": row.get("utterance_ids", []),
            "interval": row.get("interval"),
            "cv": {
                "label": cv_row.get("cv_label"),
                "policy_label": cv_row.get("policy_label"),
                "confidence": cv_row.get("cv_confidence"),
                "correct": cv_row.get("cv_correct"),
            }
            if cv_row
            else {},
            "queue_judge": {
                "label": judge_row.get("judge_label"),
                "confidence": judge_row.get("judge_confidence"),
                "shadow_action": judge_row.get("shadow_action"),
            }
            if judge_row
            else {},
            "commands": row.get("commands", {}),
        }
        item["needs_attention"] = needs_attention(item)
        output.append(item)
    output.sort(
        key=lambda item: (
            not item["needs_attention"],
            -safe_float(item.get("seconds")),
            str(item.get("class") or ""),
            str(item.get("session_id") or ""),
        )
    )
    return output


def summarize_class(items: list[dict[str, Any]]) -> dict[str, Any]:
    sessions = sorted({str(item.get("session_id") or "") for item in items if item.get("session_id")})
    buckets = Counter(str(item.get("readiness_bucket") or "unknown") for item in items)
    actions = Counter(str(item.get("recommended_action") or "unknown") for item in items)
    cv_errors = [item for item in items if (item.get("cv") or {}).get("correct") is False]
    attention = [item for item in items if item.get("needs_attention")]
    return {
        "items": len(items),
        "seconds": round(sum(safe_float(item.get("seconds")) for item in items), 3),
        "sessions": len(sessions),
        "session_ids": sessions,
        "readiness_buckets": dict(sorted(buckets.items())),
        "recommended_actions": dict(sorted(actions.items())),
        "attention_items": len(attention),
        "cv_error_items": len(cv_errors),
        "recommended_action": actions.most_common(1)[0][0] if actions else "collect_more_evidence",
        "top_attention_examples": [
            {
                "id": item.get("id"),
                "session_id": item.get("session_id"),
                "seconds": item.get("seconds"),
                "readiness_bucket": item.get("readiness_bucket"),
                "recommended_action": item.get("recommended_action"),
                "utterance_ids": item.get("utterance_ids", []),
                "stereo_command": (item.get("commands") or {}).get("stereo_clean_left_remote_right")
                or (item.get("commands") or {}).get("stereo_mic_left_remote_right"),
            }
            for item in attention[:5]
        ],
    }


def build_focus_areas(by_class: dict[str, dict[str, Any]], missing_classes: list[str]) -> list[dict[str, Any]]:
    focus: list[dict[str, Any]] = []
    if missing_classes:
        focus.append(
            {
                "area": "label_coverage",
                "classes": missing_classes,
                "reason": "taxonomy cannot protect a class that has no corpus examples",
                "next_action": "collect_or_surface_missing_audio_review_examples",
            }
        )
    for label, stats in sorted(by_class.items(), key=lambda item: (-safe_float(item[1].get("seconds")), item[0])):
        if safe_int(stats.get("attention_items")) <= 0:
            continue
        action = str(stats.get("recommended_action") or "collect_more_evidence")
        if action in {"false_positive_guard", "safe_cleanup_regression"} and safe_int(stats.get("cv_error_items")) == 0:
            continue
        focus.append(
            {
                "area": label,
                "items": stats.get("items"),
                "seconds": stats.get("seconds"),
                "attention_items": stats.get("attention_items"),
                "cv_error_items": stats.get("cv_error_items"),
                "next_action": action,
                "reason": focus_reason(label, action),
            }
        )
    return focus[:10]


def focus_reason(label: str, action: str) -> str:
    if action == "local_recall_repair":
        return "probable local speech loss cannot be fixed by dropping Me duplicates"
    if action == "mark_only_repair_needed":
        return "remote leak is harmful but whole-utterance deletion is unsafe"
    if action == "needs_stronger_audio_judge":
        return "current local metrics disagree or are too weak"
    if action == "candidate_cleanup_review":
        return "cleanup-shaped class is below silver confidence"
    if action == "inspect_judge_confusion":
        return "audio judge disagrees with silver label"
    if label in {"remote_duplicate", "asr_noise"}:
        return "class can feed conservative cleanup if safety gates still pass"
    return "class should be used as a regression guard"


def summarize_queue(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_label = Counter(str(row.get("audio_review_label") or "unknown") for row in rows)
    by_judge = Counter(str(row.get("judge_label") or "unknown") for row in rows)
    by_action = Counter(str(row.get("shadow_action") or "unknown") for row in rows)
    return {
        "items": len(rows),
        "by_audio_review_label": dict(sorted(by_label.items())),
        "by_judge_label": dict(sorted(by_judge.items())),
        "by_shadow_action": dict(sorted(by_action.items())),
    }


def build_report(args: argparse.Namespace) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    corpus_items = read_jsonl(args.corpus_dir / "regression_corpus_items.jsonl")
    eval_items = read_jsonl(args.corpus_dir / "regression_corpus_evaluation_items.jsonl")
    cv_rows = read_jsonl(args.audio_judge_dir / "audio_judge_v0_cv_predictions.jsonl")
    queue_rows = read_jsonl(args.audio_judge_dir / "audio_judge_v0_queue_predictions.jsonl")
    judge_report = read_json(args.audio_judge_dir / "audio_judge_v0_report.json") or {}

    items = build_items(corpus_items, by_id(eval_items), by_id(cv_rows), by_source_key(queue_rows))
    by_class_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        by_class_rows[str(item.get("class") or "unknown")].append(item)
    by_class = {label: summarize_class(rows) for label, rows in sorted(by_class_rows.items())}
    missing_classes = [label for label in EXPECTED_CLASSES if label not in by_class]
    attention = [item for item in items if item.get("needs_attention")]
    total_seconds = sum(safe_float(item.get("seconds")) for item in items)
    report = {
        "schema": SCHEMA_REPORT,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": {"name": "report-audio-error-taxonomy", "version": SCRIPT_VERSION},
        "inputs": {
            "corpus_dir": str(args.corpus_dir),
            "audio_judge_dir": str(args.audio_judge_dir),
            "session_quality": str(args.session_quality),
        },
        "summary": {
            "items": len(items),
            "sessions": len({str(item.get("session_id") or "") for item in items if item.get("session_id")}),
            "total_seconds": round(total_seconds, 3),
            "attention_items": len(attention),
            "attention_seconds": round(sum(safe_float(item.get("seconds")) for item in attention), 3),
            "missing_classes": missing_classes,
        },
        "by_class": by_class,
        "focus_areas": build_focus_areas(by_class, missing_classes),
        "audio_judge": {
            "readiness": judge_report.get("readiness"),
            "training": judge_report.get("training", {}),
            "evaluation": judge_report.get("evaluation", {}),
            "queue": summarize_queue(queue_rows),
        },
        "session_quality": session_quality_summary(args.session_quality),
        "next_commands": [
            "murmurmark review plan",
            "murmurmark review agent",
            "murmurmark corpus gate",
        ],
        "policy": {
            "mode": "read_only",
            "may_modify_transcript": False,
            "may_modify_raw_audio": False,
        },
    }
    return report, items


def write_markdown(path: Path, report: dict[str, Any], items: list[dict[str, Any]]) -> None:
    summary = report["summary"]
    lines = [
        "# Audio Error Taxonomy",
        "",
        "Read-only map over the private regression corpus and audio-judge reports.",
        "",
        "## Summary",
        "",
        f"- Items: `{summary['items']}`",
        f"- Sessions: `{summary['sessions']}`",
        f"- Total seconds: `{summary['total_seconds']}`",
        f"- Attention items: `{summary['attention_items']}` / `{summary['attention_seconds']}` sec",
        f"- Missing classes: `{', '.join(summary['missing_classes']) or 'none'}`",
        f"- Audio judge readiness: `{report['audio_judge'].get('readiness')}`",
        "",
        "## Classes",
        "",
        "| Class | Items | Seconds | Attention | CV errors | Recommended action |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for label, stats in report["by_class"].items():
        lines.append(
            f"| `{label}` | `{stats['items']}` | `{stats['seconds']}` | `{stats['attention_items']}` | "
            f"`{stats['cv_error_items']}` | `{stats['recommended_action']}` |"
        )
    lines.extend(["", "## Focus Areas", ""])
    if report["focus_areas"]:
        for row in report["focus_areas"]:
            label = row.get("area")
            lines.extend(
                [
                    f"### `{label}`",
                    "",
                    f"- Next action: `{row.get('next_action')}`",
                    f"- Reason: {row.get('reason')}",
                    f"- Items: `{row.get('items', '-')}`, seconds: `{row.get('seconds', '-')}`",
                    "",
                ]
            )
    else:
        lines.append("- none")
    lines.extend(["", "## Top Attention Examples", ""])
    for item in [row for row in items if row.get("needs_attention")][:25]:
        interval = item.get("interval") if isinstance(item.get("interval"), dict) else {}
        lines.extend(
            [
                f"### {item.get('id')} `{item.get('class')}` {item.get('session_id')} {interval.get('start_time', '')}-{interval.get('end_time', '')}",
                "",
                f"- Action: `{item.get('recommended_action')}`",
                f"- Readiness bucket: `{item.get('readiness_bucket')}`",
                f"- CV: `{(item.get('cv') or {}).get('label')}` correct `{(item.get('cv') or {}).get('correct')}`",
                f"- Utterances: `{', '.join(item.get('utterance_ids') or [])}`",
            ]
        )
        commands = item.get("commands") if isinstance(item.get("commands"), dict) else {}
        stereo = commands.get("stereo_clean_left_remote_right") or commands.get("stereo_mic_left_remote_right")
        if stereo:
            lines.append(f"- Stereo: `{stereo}`")
        lines.append("")
    lines.extend(["", "## Next Commands", ""])
    for command in report["next_commands"]:
        lines.append(f"- `{command}`")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    report, items = build_report(args)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    write_json(args.out_dir / "audio_error_taxonomy_report.json", report)
    write_jsonl(args.out_dir / "audio_error_taxonomy_items.jsonl", items)
    write_markdown(args.out_dir / "audio_error_taxonomy_report.md", report, items)
    print(f"items: {report['summary']['items']}")
    print(f"attention_items: {report['summary']['attention_items']}")
    print(f"written: {args.out_dir / 'audio_error_taxonomy_report.json'}")
    print(f"report: {args.out_dir / 'audio_error_taxonomy_report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
