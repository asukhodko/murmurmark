#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_VERSION = "0.1.0"
SCHEMA_MANIFEST = "murmurmark.regression_corpus_manifest/v1"
SCHEMA_ITEM = "murmurmark.regression_corpus_item/v1"
SCHEMA_SUMMARY = "murmurmark.regression_corpus_summary/v1"

ERROR_LABELS = {"remote_duplicate", "asr_noise", "lost_me", "remote_leak"}
JUDGE_LABELS = {"uncertain"}
BENIGN_LABELS = {"double_talk", "timing_overlap", "likely_reliable"}
DEFAULT_LABEL_ORDER = [
    "remote_duplicate",
    "asr_noise",
    "lost_me",
    "remote_leak",
    "uncertain",
    "double_talk",
    "timing_overlap",
    "likely_reliable",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a cross-session regression corpus from audio-review audits.")
    parser.add_argument("sessions", nargs="+", type=Path)
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("sessions/_reports/regression-corpus"),
        help="Output directory. Default is under ignored sessions/_reports/.",
    )
    parser.add_argument("--per-label", type=int, default=16, help="Maximum examples per label.")
    parser.add_argument("--max-items", type=int, default=160, help="Maximum examples in the corpus.")
    parser.add_argument("--copy-clips", action=argparse.BooleanOptionalAction, default=True)
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


def format_time(seconds: float) -> str:
    total = max(0, int(seconds))
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def session_audio_review_paths(session: Path) -> dict[str, Path]:
    base = session / "derived/audit/audio-review-pack"
    return {
        "audit": base / "audio_review_audit.jsonl",
        "summary": base / "audio_review_summary.json",
        "pack_summary": base / "review_pack_summary.json",
    }


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def label_priority(label: str, verdict: str, confidence: float, duration: float, scores: dict[str, Any]) -> float:
    base = 0.0
    if verdict == "probable_transcript_error":
        base += 100.0
    elif verdict == "needs_stronger_audio_judge":
        base += 82.0
    elif verdict == "likely_reliable":
        base += 42.0
    if label in ERROR_LABELS:
        base += 18.0
    elif label in JUDGE_LABELS:
        base += 12.0
    elif label in BENIGN_LABELS:
        base += 4.0
    top_score = max(safe_float(value) for value in scores.values()) if scores else 0.0
    duration_bonus = min(15.0, max(0.0, duration))
    return round(base + confidence * 20.0 + top_score * 0.08 + duration_bonus, 3)


def target_use(label: str, verdict: str) -> list[str]:
    uses: list[str] = []
    if verdict == "probable_transcript_error" and label in {"remote_duplicate", "asr_noise"}:
        uses.extend(["cleanup_regression", "auto_drop_gate"])
    if verdict == "probable_transcript_error" and label in {"lost_me", "remote_leak"}:
        uses.extend(["audio_judge_training", "mark_only_regression"])
    if verdict == "needs_stronger_audio_judge" or label == "uncertain":
        uses.append("audio_judge_training")
    if label in {"double_talk", "timing_overlap", "likely_reliable"}:
        uses.append("false_positive_guard")
    return uses or ["manual_audit"]


def compact_utterance(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "role": row.get("role"),
        "source_track": row.get("source_track"),
        "start": row.get("start"),
        "end": row.get("end"),
        "text": row.get("text"),
        "needs_review": row.get("needs_review"),
        "quality_flags": row.get("quality_flags", []),
    }


def resolve_clip_path(path_value: Any) -> Path | None:
    if not isinstance(path_value, str) or not path_value:
        return None
    path = Path(path_value)
    return path if path.is_absolute() else Path.cwd() / path


def copy_clips(item: dict[str, Any], out_dir: Path) -> None:
    source_clips = item.pop("_source_clips", {})
    target_dir = out_dir / "clips" / item["session_id"]
    clips: dict[str, str] = {}
    commands: dict[str, str] = {}
    for name, source_value in sorted(source_clips.items()):
        source = resolve_clip_path(source_value)
        if not source or not source.exists():
            continue
        suffix = source.suffix or ".wav"
        target = target_dir / f"{item['id']}_{name}{suffix}"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        clips[name] = str(target)
        commands[name] = f"afplay {target}"
    item["clips"] = clips
    item["commands"] = commands


def build_item(session: Path, row: dict[str, Any], ordinal: int) -> dict[str, Any]:
    classification = row.get("classification") if isinstance(row.get("classification"), dict) else {}
    interval = row.get("interval") if isinstance(row.get("interval"), dict) else {}
    scores = row.get("scores") if isinstance(row.get("scores"), dict) else {}
    features = row.get("features") if isinstance(row.get("features"), dict) else {}
    label = str(classification.get("label") or "unknown")
    verdict = str(classification.get("verdict") or "unknown")
    confidence = safe_float(classification.get("confidence"))
    duration = safe_float(interval.get("duration_sec"))
    start = safe_float(interval.get("start"))
    end = safe_float(interval.get("end"), start + duration)
    item_id = f"rc_{ordinal:06d}"
    return {
        "schema": SCHEMA_ITEM,
        "id": item_id,
        "session_id": session.name,
        "source_audit_id": row.get("id"),
        "profile": row.get("profile"),
        "label": label,
        "verdict": verdict,
        "confidence": confidence,
        "priority_score": label_priority(label, verdict, confidence, duration, scores),
        "target_use": target_use(label, verdict),
        "interval": {
            "start": round(start, 3),
            "end": round(end, 3),
            "duration_sec": round(duration, 3),
            "start_time": format_time(start),
            "end_time": format_time(end),
        },
        "utterance_ids": row.get("utterance_ids", []),
        "utterances": [compact_utterance(value) for value in row.get("utterances", []) if isinstance(value, dict)],
        "source_reasons": row.get("source_reasons", []),
        "scores": scores,
        "classification": classification,
        "text_features": (features.get("text") if isinstance(features.get("text"), dict) else {}),
        "_source_clips": row.get("clips") if isinstance(row.get("clips"), dict) else {},
    }


def dedupe_key(row: dict[str, Any]) -> tuple[Any, ...]:
    interval = row.get("interval") if isinstance(row.get("interval"), dict) else {}
    classification = row.get("classification") if isinstance(row.get("classification"), dict) else {}
    ids = tuple(sorted(str(value) for value in row.get("utterance_ids", []) if value))
    return (
        row.get("session_id"),
        classification.get("label"),
        ids,
        round(safe_float(interval.get("start")), 1),
        round(safe_float(interval.get("end")), 1),
    )


def select_items(candidates: list[dict[str, Any]], per_label: int, max_items: int) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen: set[tuple[Any, ...]] = set()
    for row in candidates:
        key = dedupe_key(row)
        if key in seen:
            continue
        seen.add(key)
        classification = row.get("classification") if isinstance(row.get("classification"), dict) else {}
        label = str(classification.get("label") or "unknown")
        buckets[label].append(row)

    selected: list[dict[str, Any]] = []
    label_order = DEFAULT_LABEL_ORDER + sorted(label for label in buckets if label not in DEFAULT_LABEL_ORDER)
    for label in label_order:
        rows = sorted(
            buckets.get(label, []),
            key=lambda row: (
                -label_priority(
                    label,
                    str((row.get("classification") or {}).get("verdict") or "unknown"),
                    safe_float((row.get("classification") or {}).get("confidence")),
                    safe_float((row.get("interval") or {}).get("duration_sec")),
                    row.get("scores") if isinstance(row.get("scores"), dict) else {},
                ),
                safe_float((row.get("interval") or {}).get("start")),
            ),
        )
        selected.extend(rows[: max(0, per_label)])

    selected = sorted(
        selected,
        key=lambda row: (
            -label_priority(
                str((row.get("classification") or {}).get("label") or "unknown"),
                str((row.get("classification") or {}).get("verdict") or "unknown"),
                safe_float((row.get("classification") or {}).get("confidence")),
                safe_float((row.get("interval") or {}).get("duration_sec")),
                row.get("scores") if isinstance(row.get("scores"), dict) else {},
            ),
            str(row.get("session_id") or ""),
            safe_float((row.get("interval") or {}).get("start")),
        ),
    )
    return selected[: max_items]


def summarize(items: list[dict[str, Any]], skipped_sessions: list[dict[str, Any]]) -> dict[str, Any]:
    by_label: dict[str, dict[str, Any]] = {}
    by_verdict: dict[str, dict[str, Any]] = {}
    by_use = Counter()
    for item in items:
        duration = safe_float((item.get("interval") or {}).get("duration_sec"))
        label = str(item.get("label") or "unknown")
        verdict = str(item.get("verdict") or "unknown")
        for bucket, key in ((by_label, label), (by_verdict, verdict)):
            value = bucket.setdefault(key, {"count": 0, "seconds": 0.0})
            value["count"] += 1
            value["seconds"] += duration
        for use in item.get("target_use", []):
            by_use[str(use)] += 1
    for bucket in list(by_label.values()) + list(by_verdict.values()):
        bucket["seconds"] = round(bucket["seconds"], 3)
    return {
        "schema": SCHEMA_SUMMARY,
        "generator": {"name": "build-regression-corpus", "version": SCRIPT_VERSION},
        "item_count": len(items),
        "session_count": len({item["session_id"] for item in items}),
        "skipped_sessions": skipped_sessions,
        "by_label": dict(sorted(by_label.items())),
        "by_verdict": dict(sorted(by_verdict.items())),
        "by_target_use": dict(sorted(by_use.items())),
    }


def write_markdown(path: Path, summary: dict[str, Any], items: list[dict[str, Any]]) -> None:
    lines = [
        "# MurmurMark Regression Corpus",
        "",
        "This corpus is built from existing audio-review audits. It does not edit sessions or raw audio.",
        "",
        "## Summary",
        "",
        f"- Items: `{summary['item_count']}`",
        f"- Sessions represented: `{summary['session_count']}`",
        f"- Skipped sessions: `{len(summary['skipped_sessions'])}`",
        "",
        "## By Label",
        "",
    ]
    for label, bucket in summary["by_label"].items():
        lines.append(f"- `{label}`: `{bucket['count']}` items, `{bucket['seconds']}` sec")
    lines.extend(["", "## By Verdict", ""])
    for verdict, bucket in summary["by_verdict"].items():
        lines.append(f"- `{verdict}`: `{bucket['count']}` items, `{bucket['seconds']}` sec")
    lines.extend(["", "## Top Items", ""])
    for item in items[:50]:
        text = " / ".join(
            f"{utterance.get('role')} `{utterance.get('id')}`: {utterance.get('text')}"
            for utterance in item.get("utterances", [])[:3]
            if utterance.get("text")
        )
        lines.extend(
            [
                f"### {item['id']} `{item['label']}` {item['session_id']} {item['interval']['start_time']}-{item['interval']['end_time']}",
                "",
                f"- Verdict: `{item['verdict']}`, confidence `{item['confidence']}`",
                f"- Target use: `{', '.join(item['target_use'])}`",
                f"- Priority: `{item['priority_score']}`",
                f"- Text: {text}" if text else "- Text: ",
            ]
        )
        commands = item.get("commands") or {}
        stereo = commands.get("stereo_clean_left_remote_right") or commands.get("stereo_mic_left_remote_right")
        if stereo:
            lines.append(f"- Stereo: `{stereo}`")
        if commands.get("mic_raw"):
            lines.append(f"- Mic raw: `{commands['mic_raw']}`")
        lines.append("")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    candidates: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    inputs: dict[str, dict[str, Any]] = {}
    for session in args.sessions:
        paths = session_audio_review_paths(session)
        rows = read_jsonl(paths["audit"])
        summary = read_json(paths["summary"])
        inputs[str(session)] = {
            "audit": str(paths["audit"]),
            "audit_exists": paths["audit"].exists(),
            "summary": str(paths["summary"]),
            "summary_exists": paths["summary"].exists(),
        }
        if not rows:
            skipped.append({"session": str(session), "reason": "missing_or_empty_audio_review_audit"})
            continue
        for row in rows:
            row = dict(row)
            row.setdefault("session_id", session.name)
            candidates.append(row)

    selected_rows = select_items(candidates, args.per_label, args.max_items)
    items = [build_item(Path(row.get("session_id", "")), row, index) for index, row in enumerate(selected_rows, start=1)]
    if args.copy_clips:
        for item in items:
            copy_clips(item, out_dir)
    else:
        for item in items:
            item.pop("_source_clips", None)
            item["clips"] = {}
            item["commands"] = {}

    summary = summarize(items, skipped)
    manifest = {
        "schema": SCHEMA_MANIFEST,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": {"name": "build-regression-corpus", "version": SCRIPT_VERSION},
        "inputs": inputs,
        "selection": {
            "per_label": args.per_label,
            "max_items": args.max_items,
            "copy_clips": args.copy_clips,
        },
        "outputs": {
            "items": "regression_corpus_items.jsonl",
            "summary": "regression_corpus_summary.json",
            "markdown": "regression_corpus.md",
        },
    }
    write_json(out_dir / "regression_corpus_manifest.json", manifest)
    write_json(out_dir / "regression_corpus_summary.json", summary)
    write_jsonl(out_dir / "regression_corpus_items.jsonl", items)
    write_markdown(out_dir / "regression_corpus.md", summary, items)
    print(f"items: {len(items)}")
    print(f"written: {out_dir / 'regression_corpus_items.jsonl'}")
    print(f"markdown: {out_dir / 'regression_corpus.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
