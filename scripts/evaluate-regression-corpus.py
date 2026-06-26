#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_VERSION = "0.2.0"
SCHEMA_REPORT = "murmurmark.regression_corpus_evaluation/v1"
SCHEMA_ITEM = "murmurmark.regression_corpus_evaluation_item/v1"

AUTO_DROP_LABELS = {"remote_duplicate", "asr_noise"}
MARK_ONLY_LABELS = {"remote_leak", "lost_me"}
BENIGN_LABELS = {"likely_reliable", "double_talk", "timing_overlap"}
EXPECTED_LABELS = ["remote_duplicate", "asr_noise", "remote_leak", "lost_me", "uncertain", "double_talk", "timing_overlap", "likely_reliable"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate a MurmurMark regression corpus for cleanup and audio-judge readiness.")
    parser.add_argument(
        "--corpus-dir",
        type=Path,
        default=Path("sessions/_reports/regression-corpus"),
        help="Directory produced by build-regression-corpus.py.",
    )
    parser.add_argument("--out-dir", type=Path, default=None)
    return parser.parse_args()


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


def safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def classify_item(item: dict[str, Any]) -> tuple[str, list[str]]:
    label = str(item.get("label") or "unknown")
    verdict = str(item.get("verdict") or "unknown")
    confidence = safe_float(item.get("confidence"))
    reasons: list[str] = []

    if label in AUTO_DROP_LABELS and verdict == "probable_transcript_error" and confidence >= 0.90:
        reasons.append("high-confidence auto-drop error")
        return "silver_cleanup_positive", reasons
    if label in AUTO_DROP_LABELS and verdict == "likely_reliable":
        reasons.append("auto-drop-shaped example that local metrics say to keep")
        return "silver_keep_negative", reasons
    if label in BENIGN_LABELS and verdict == "likely_reliable" and confidence >= 0.85:
        reasons.append("high-confidence keep example")
        return "silver_keep_negative", reasons
    if label in BENIGN_LABELS and verdict == "likely_reliable" and confidence >= 0.65:
        reasons.append("low-risk keep example")
        return "silver_keep_negative", reasons
    if label in MARK_ONLY_LABELS and verdict == "probable_transcript_error":
        reasons.append("harmful but not safe for whole-utterance deletion")
        return "mark_only_regression", reasons
    if verdict == "needs_stronger_audio_judge" or label == "uncertain":
        reasons.append("weak or conflicting local metrics")
        return "needs_audio_judge", reasons
    if label in AUTO_DROP_LABELS and verdict == "probable_transcript_error":
        reasons.append("probable auto-drop class below silver confidence")
        return "weak_cleanup_positive", reasons
    reasons.append("does not match a readiness bucket")
    return "unclassified", reasons


def evaluate(items: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    by_label: dict[str, dict[str, Any]] = {}
    by_bucket: dict[str, dict[str, Any]] = {}
    by_session = Counter()
    evaluated: list[dict[str, Any]] = []

    for item in items:
        label = str(item.get("label") or "unknown")
        verdict = str(item.get("verdict") or "unknown")
        duration = safe_float((item.get("interval") or {}).get("duration_sec"))
        bucket, reasons = classify_item(item)
        by_session[str(item.get("session_id") or "unknown")] += 1
        for collection, key in ((by_label, label), (by_bucket, bucket)):
            entry = collection.setdefault(key, {"count": 0, "seconds": 0.0})
            entry["count"] += 1
            entry["seconds"] += duration
        evaluated.append(
            {
                "schema": SCHEMA_ITEM,
                "id": item.get("id"),
                "session_id": item.get("session_id"),
                "source_audit_id": item.get("source_audit_id"),
                "label": label,
                "verdict": verdict,
                "confidence": item.get("confidence"),
                "readiness_bucket": bucket,
                "reasons": reasons,
                "target_use": item.get("target_use", []),
                "interval": item.get("interval"),
                "utterance_ids": item.get("utterance_ids", []),
                "commands": item.get("commands", {}),
            }
        )

    for collection in (by_label, by_bucket):
        for entry in collection.values():
            entry["seconds"] = round(entry["seconds"], 3)

    missing_labels = [label for label in EXPECTED_LABELS if label not in by_label]
    silver_cleanup = by_bucket.get("silver_cleanup_positive", {"count": 0, "seconds": 0.0})
    silver_keep = by_bucket.get("silver_keep_negative", {"count": 0, "seconds": 0.0})
    needs_judge = by_bucket.get("needs_audio_judge", {"count": 0, "seconds": 0.0})
    mark_only = by_bucket.get("mark_only_regression", {"count": 0, "seconds": 0.0})

    readiness = "weak"
    if silver_cleanup.get("count", 0) >= 8 and silver_keep.get("count", 0) >= 8:
        readiness = "partial_cleanup_regression_ready"
    if (
        not missing_labels
        and silver_cleanup.get("count", 0) >= 10
        and silver_keep.get("count", 0) >= 16
        and needs_judge.get("count", 0) >= 20
    ):
        readiness = "useful_for_audio_judge_v0"
    if not missing_labels and silver_cleanup.get("count", 0) >= 20 and silver_keep.get("count", 0) >= 20:
        readiness = "broad_regression_ready"

    recommended_next_steps: list[str] = []
    if missing_labels:
        recommended_next_steps.append("collect_or_surface_missing_labels:" + ",".join(missing_labels))
    if needs_judge.get("count", 0) > 0:
        recommended_next_steps.append("build_stronger_local_audio_judge_for_uncertain_items")
    if silver_cleanup.get("count", 0) > 0 and silver_keep.get("count", 0) > 0:
        recommended_next_steps.append("use_silver_cleanup_and_keep_examples_as_no_regression_set")
    if mark_only.get("count", 0) > 0:
        recommended_next_steps.append("keep_remote_leak_lost_me_mark_only_until_audio_judge_improves")

    report = {
        "schema": SCHEMA_REPORT,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": {"name": "evaluate-regression-corpus", "version": SCRIPT_VERSION},
        "item_count": len(items),
        "session_count": len(by_session),
        "by_session": dict(sorted(by_session.items())),
        "by_label": dict(sorted(by_label.items())),
        "by_readiness_bucket": dict(sorted(by_bucket.items())),
        "missing_labels": missing_labels,
        "readiness": readiness,
        "recommended_next_steps": recommended_next_steps,
    }
    return report, evaluated


def write_markdown(path: Path, report: dict[str, Any], items: list[dict[str, Any]]) -> None:
    lines = [
        "# Regression Corpus Evaluation",
        "",
        f"Readiness: `{report['readiness']}`",
        f"Items: `{report['item_count']}`",
        f"Sessions: `{report['session_count']}`",
        "",
        "## Readiness Buckets",
        "",
    ]
    for bucket, stats in report["by_readiness_bucket"].items():
        lines.append(f"- `{bucket}`: `{stats['count']}` items, `{stats['seconds']}` sec")
    lines.extend(["", "## Label Coverage", ""])
    for label, stats in report["by_label"].items():
        lines.append(f"- `{label}`: `{stats['count']}` items, `{stats['seconds']}` sec")
    if report["missing_labels"]:
        lines.extend(["", "Missing labels: `" + ", ".join(report["missing_labels"]) + "`"])
    lines.extend(["", "## Recommended Next Steps", ""])
    for step in report["recommended_next_steps"]:
        lines.append(f"- `{step}`")
    lines.extend(["", "## Top Needs Audio Judge", ""])
    for item in [row for row in items if row["readiness_bucket"] == "needs_audio_judge"][:20]:
        commands = item.get("commands") or {}
        stereo = commands.get("stereo_clean_left_remote_right") or commands.get("stereo_mic_left_remote_right")
        lines.extend(
            [
                f"### {item['id']} {item['session_id']} {item['interval']['start_time']}-{item['interval']['end_time']}",
                "",
                f"- Label: `{item['label']}`, confidence `{item['confidence']}`",
                f"- Utterances: `{', '.join(item.get('utterance_ids') or [])}`",
            ]
        )
        if stereo:
            lines.append(f"- Stereo: `{stereo}`")
        lines.append("")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    corpus_dir: Path = args.corpus_dir
    out_dir: Path = args.out_dir or corpus_dir
    items = read_jsonl(corpus_dir / "regression_corpus_items.jsonl")
    if not items:
        raise SystemExit(f"missing or empty corpus: {corpus_dir / 'regression_corpus_items.jsonl'}")
    report, evaluated = evaluate(items)
    write_json(out_dir / "regression_corpus_evaluation.json", report)
    write_jsonl(out_dir / "regression_corpus_evaluation_items.jsonl", evaluated)
    write_markdown(out_dir / "regression_corpus_evaluation.md", report, evaluated)
    print(f"readiness: {report['readiness']}")
    print(f"items: {report['item_count']}")
    print(f"written: {out_dir / 'regression_corpus_evaluation.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
