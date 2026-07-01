#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any


SCHEMA_MANIFEST = "murmurmark.audio_review_pack/v1"
SCHEMA_ITEM = "murmurmark.audio_review_pack_item/v1"
SCHEMA_SUMMARY = "murmurmark.audio_review_pack_summary/v1"
SCRIPT_VERSION = "0.1.2"
SAMPLE_RATE = 16000
PROFILE_CHOICES = [
    "auto",
    "current",
    "shadow_v2",
    "audit_cleanup_v1",
    "audit_cleanup_v2",
    "audit_cleanup_v3",
    "audit_cleanup_v4",
    "audit_cleanup_v5",
    "audit_cleanup_v6",
    "audit_cleanup_v7",
    "agent_reviewed_v1",
    "reviewed_v1",
    "suggested_review_v1",
    "order_repair_v1",
]
AUTO_PROFILE_ORDER = [
    "reviewed_v1",
    "order_repair_v1",
    "audit_cleanup_v7",
    "agent_reviewed_v1",
    "suggested_review_v1",
    "audit_cleanup_v6",
    "audit_cleanup_v5",
    "audit_cleanup_v4",
    "audit_cleanup_v3",
    "audit_cleanup_v2",
    "audit_cleanup_v1",
    "shadow_v2",
    "current",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build local audio clips for suspicious transcript regions.")
    parser.add_argument("session", type=Path)
    parser.add_argument(
        "--profile",
        default="audit_cleanup_v1",
        choices=PROFILE_CHOICES,
    )
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument("--min-overlap-sec", type=float, default=0.5)
    parser.add_argument("--padding-sec", type=float, default=3.0)
    parser.add_argument("--max-items", type=int, default=160)
    parser.add_argument("--write-clips", action=argparse.BooleanOptionalAction, default=True)
    return parser.parse_args()


def run(args: list[str]) -> None:
    subprocess.run(args, check=True)


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as file:
            value = json.load(file)
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


def suffix(profile: str) -> str:
    return "" if profile == "current" else f".{profile}"


def format_time(seconds: float) -> str:
    total = max(0, int(seconds))
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def resolve_profile(session: Path, requested: str) -> str:
    if requested != "auto":
        return requested
    resolved = session / "derived/transcript-simple/whisper-cpp/resolved"
    for candidate in AUTO_PROFILE_ORDER:
        if (resolved / f"clean_dialogue{suffix(candidate)}.json").exists():
            return candidate
    return "current"


def transcript_paths(session: Path, profile: str) -> dict[str, Path]:
    resolved = session / "derived/transcript-simple/whisper-cpp/resolved"
    return {
        "dialogue": resolved / f"clean_dialogue{suffix(profile)}.json",
        "quality": resolved / f"quality_report{suffix(profile)}.json",
        "overlaps": resolved / f"overlaps{suffix(profile)}.json",
        "repair_comparison": resolved / "repair_comparison.json",
    }


def cleanup_rejections_path(session: Path, profile: str) -> Path | None:
    if not profile.startswith("audit_cleanup_"):
        return None
    path = (
        session
        / "derived/transcript-simple/whisper-cpp/audit-cleanup"
        / f"audit_cleanup_rejected_patches.{profile}.jsonl"
    )
    return path if path.exists() else None


def synthesis_review_items_path(session: Path, profile: str) -> Path | None:
    out_dir = session / "derived/synthesis-simple/extractive"
    profile_path = out_dir / f"review_items{suffix(profile)}.jsonl"
    if profile != "current":
        return profile_path if profile_path.exists() else None
    path = out_dir / "review_items.jsonl"
    return path if path.exists() else None


def review_plan_items_path(session: Path) -> Path | None:
    path = session / "derived/readiness/review-plan/review_decisions.template.jsonl"
    return path if path.exists() else None


def source_paths(session: Path) -> dict[str, Path]:
    return {
        "mic_raw": session / "audio/mic/000001.caf",
        "remote": session / "audio/remote/000001.caf",
        "mic_clean": session / "derived/preprocess/audio/mic_clean_local_fir.wav",
        "mic_role_masked": session / "derived/preprocess/audio/mic_role_masked_for_asr.wav",
    }


def utterance_summary(row: dict[str, Any]) -> dict[str, Any]:
    quality = row.get("quality") if isinstance(row.get("quality"), dict) else {}
    return {
        "id": str(row.get("id") or ""),
        "role": row.get("role") or row.get("speaker_label") or row.get("source_track"),
        "source_track": row.get("source_track"),
        "start": float(row.get("start", 0.0) or 0.0),
        "end": float(row.get("end", 0.0) or 0.0),
        "text": str(row.get("text") or row.get("corrected_text") or row.get("raw_text") or ""),
        "needs_review": bool(quality.get("needs_review")),
        "quality_flags": sorted(key for key, value in quality.items() if value is True),
    }


def review_plan_is_open(row: dict[str, Any]) -> bool:
    decision = str(row.get("decision") or "todo")
    status = str(row.get("status") or "todo")
    if bool(row.get("reviewed")):
        return False
    if decision not in {"", "todo", "needs_review"}:
        return False
    return status not in {"applied", "reviewed", "done", "skipped"}


def review_plan_utterances(row: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    ids: list[str] = []
    for key in ("utterance_ids", "me_utterance_ids", "remote_utterance_ids"):
        values = row.get(key)
        if isinstance(values, list):
            ids.extend(str(value) for value in values if value)
    utterances = [by_id[item_id] for item_id in dict.fromkeys(ids) if item_id in by_id]
    if utterances:
        return utterances

    interval = row.get("interval") if isinstance(row.get("interval"), dict) else {}
    start = float(interval.get("start", 0.0) or 0.0)
    end = float(interval.get("end", start) or start)
    synthetic: list[dict[str, Any]] = []
    for index, item in enumerate(row.get("text") or [], start=1):
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or f"{row.get('source_audit_id') or 'review'}_{index}")
        role = item.get("role") or ("Me" if str(item.get("source_track") or "").lower() == "mic" else "")
        synthetic.append(
            {
                "id": item_id,
                "role": role,
                "source_track": item.get("source_track"),
                "start": start,
                "end": end,
                "text": item.get("text", ""),
                "quality": {"needs_review": True},
            }
        )
    return synthetic


def find_utterances_for_interval(by_id: dict[str, dict[str, Any]], start: float, end: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in by_id.values():
        row_start = float(row.get("start", 0.0) or 0.0)
        row_end = float(row.get("end", row_start) or row_start)
        if min(end, row_end) - max(start, row_start) > 0:
            rows.append(row)
    return sorted(rows, key=lambda item: (float(item.get("start", 0.0) or 0.0), str(item.get("id") or "")))[:6]


def item_key(start: float, end: float, utterances: list[dict[str, Any]]) -> tuple[Any, ...]:
    ids = tuple(sorted(str(row.get("id") or "") for row in utterances if row.get("id")))
    if ids:
        return ("ids", ids)
    return ("time", round(start, 1), round(end, 1))


def add_item(
    items: dict[tuple[Any, ...], dict[str, Any]],
    *,
    start: float,
    end: float,
    reasons: list[str],
    utterances: list[dict[str, Any]],
    priority: float,
    source_context: dict[str, Any],
) -> None:
    start = max(0.0, float(start))
    end = max(start + 0.05, float(end))
    summaries = [utterance_summary(row) for row in utterances]
    key = item_key(start, end, summaries)
    existing = items.get(key)
    if existing:
        existing["source_reasons"] = sorted(set(existing["source_reasons"]) | set(reasons))
        existing["priority_score"] = max(float(existing["priority_score"]), float(priority))
        existing["source_contexts"].append(source_context)
        existing["interval"]["start"] = min(float(existing["interval"]["start"]), start)
        existing["interval"]["end"] = max(float(existing["interval"]["end"]), end)
        existing["interval"]["duration_sec"] = round(existing["interval"]["end"] - existing["interval"]["start"], 3)
        return
    items[key] = {
        "schema": SCHEMA_ITEM,
        "id": "",
        "source_reasons": sorted(set(reasons)),
        "priority_score": round(float(priority), 3),
        "interval": {
            "start": round(start, 3),
            "end": round(end, 3),
            "duration_sec": round(end - start, 3),
            "start_time": format_time(start),
            "end_time": format_time(end),
        },
        "utterances": summaries,
        "utterance_ids": [row["id"] for row in summaries if row.get("id")],
        "source_contexts": [source_context],
        "clips": {},
        "commands": {},
    }


def add_needs_review(items: dict[tuple[Any, ...], dict[str, Any]], utterances: list[dict[str, Any]]) -> None:
    for row in utterances:
        quality = row.get("quality") if isinstance(row.get("quality"), dict) else {}
        if not quality.get("needs_review"):
            continue
        start = float(row.get("start", 0.0) or 0.0)
        end = float(row.get("end", start) or start)
        add_item(
            items,
            start=start,
            end=end,
            reasons=["transcript_needs_review"],
            utterances=[row],
            priority=75 + max(0.0, end - start),
            source_context={"type": "clean_dialogue_needs_review", "utterance_id": row.get("id")},
        )


def add_overlaps(
    items: dict[tuple[Any, ...], dict[str, Any]],
    overlaps: list[dict[str, Any]],
    by_id: dict[str, dict[str, Any]],
    min_overlap: float,
) -> None:
    for row in overlaps:
        duration = float(row.get("duration_sec", 0.0) or 0.0)
        if duration < min_overlap:
            continue
        left = by_id.get(str(row.get("left_utterance_id") or ""))
        right = by_id.get(str(row.get("right_utterance_id") or ""))
        if not left or not right:
            continue
        start = float(row.get("start", min(float(left.get("start", 0.0)), float(right.get("start", 0.0)))) or 0.0)
        end = float(row.get("end", max(float(left.get("end", start)), float(right.get("end", start)))) or start)
        add_item(
            items,
            start=start,
            end=end,
            reasons=["cross_role_overlap"],
            utterances=[left, right],
            priority=60 + duration * 4,
            source_context={"type": "overlap", "overlap": row},
        )


def add_group_audit(items: dict[tuple[Any, ...], dict[str, Any]], rows: list[dict[str, Any]], by_id: dict[str, dict[str, Any]]) -> None:
    harmful = {"probable_duplicate", "probable_remote_leak", "probable_asr_noise"}
    for row in rows:
        interval = row.get("interval") if isinstance(row.get("interval"), dict) else {}
        classification = row.get("classification") if isinstance(row.get("classification"), dict) else {}
        label = str(classification.get("label") or "unknown")
        if label not in harmful and label not in {"needs_human_review", "probable_double_talk", "probable_timing_overlap"}:
            continue
        start = float(interval.get("start", 0.0) or 0.0)
        end = float(interval.get("end", start) or start)
        refs = row.get("utterances") if isinstance(row.get("utterances"), dict) else {}
        ids = []
        me_id = ""
        for side in ("me", "remote"):
            value = refs.get(side) if isinstance(refs.get(side), dict) else {}
            if value.get("id"):
                item_id = str(value["id"])
                ids.append(item_id)
                if side == "me":
                    me_id = item_id
        if me_id and me_id not in by_id:
            continue
        utterances = [by_id[item_id] for item_id in ids if item_id in by_id]
        if len(utterances) < len(ids):
            for side in ("me", "remote"):
                value = refs.get(side) if isinstance(refs.get(side), dict) else {}
                item_id = str(value.get("id") or "")
                if not item_id or item_id in by_id:
                    continue
                utterances.append(
                    {
                        "id": item_id,
                        "role": "Me" if side == "me" else "Colleagues",
                        "source_track": "mic" if side == "me" else "remote",
                        "start": value.get("start", start),
                        "end": value.get("end", end),
                        "text": value.get("text", ""),
                        "quality": {"needs_review": value.get("needs_review", False)},
                    }
                )
        if not utterances:
            utterances = find_utterances_for_interval(by_id, start, end)
        base = 92 if label in harmful else 82 if label == "needs_human_review" else 55
        add_item(
            items,
            start=start,
            end=end,
            reasons=[f"group_overlap:{label}"],
            utterances=utterances,
            priority=base + float(interval.get("duration_sec", 0.0) or 0.0),
            source_context={
                "type": "group_overlap_audit",
                "id": row.get("id"),
                "classification": classification,
                "scores": row.get("scores"),
            },
        )


def add_cleanup_rejections(
    items: dict[tuple[Any, ...], dict[str, Any]],
    rows: list[dict[str, Any]],
    by_id: dict[str, dict[str, Any]],
) -> None:
    for row in rows:
        target = row.get("target") if isinstance(row.get("target"), dict) else {}
        utterance_id = str(target.get("utterance_id") or row.get("utterance_id") or "")
        utterance = by_id.get(utterance_id)
        if not utterance:
            continue
        start = float(utterance.get("start", 0.0) or 0.0)
        end = float(utterance.get("end", start) or start)
        reason = str(row.get("reason") or "cleanup_rejected")
        add_item(
            items,
            start=start,
            end=end,
            reasons=[f"cleanup_rejected:{reason}"],
            utterances=[utterance],
            priority=70 + max(0.0, end - start),
            source_context={"type": "audit_cleanup_rejected_patch", "patch": row},
        )


def add_synthesis_review_items(
    items: dict[tuple[Any, ...], dict[str, Any]],
    rows: list[dict[str, Any]],
    by_id: dict[str, dict[str, Any]],
) -> None:
    for row in rows:
        ids = [str(value) for value in row.get("utterance_ids", []) if value]
        utterances = [by_id[item_id] for item_id in ids if item_id in by_id]
        start = float(row.get("start", 0.0) or 0.0)
        end = float(row.get("end", start) or start)
        if not utterances and end > start:
            utterances = find_utterances_for_interval(by_id, start, end)
        if not utterances:
            continue
        severity = str(row.get("severity") or "medium")
        priority = {"high": 78, "medium": 64, "low": 45}.get(severity, 58)
        add_item(
            items,
            start=start,
            end=end,
            reasons=[f"synthesis_review:{row.get('type', 'review_item')}"],
            utterances=utterances,
            priority=priority + max(0.0, end - start),
            source_context={"type": "synthesis_review_item", "review_item": row},
        )


def add_review_plan_items(
    items: dict[tuple[Any, ...], dict[str, Any]],
    rows: list[dict[str, Any]],
    by_id: dict[str, dict[str, Any]],
) -> None:
    lane_priority = {
        "check_local_recall": 118,
        "classify_audio": 108,
        "check_unique_me_content": 102,
        "check_transcript_order": 92,
        "fast_confirm_drop": 88,
    }
    for row in rows:
        if not review_plan_is_open(row):
            continue
        lane = str(row.get("review_lane") or "")
        if lane not in lane_priority:
            continue
        interval = row.get("interval") if isinstance(row.get("interval"), dict) else {}
        start = float(interval.get("start", 0.0) or 0.0)
        end = float(interval.get("end", start) or start)
        if end <= start:
            continue
        utterances = review_plan_utterances(row, by_id)
        if not utterances:
            utterances = find_utterances_for_interval(by_id, start, end)
        if not utterances:
            continue
        label = str(row.get("label") or "review_item")
        source_audit_id = str(row.get("source_audit_id") or "")
        add_item(
            items,
            start=start,
            end=end,
            reasons=[f"review_plan:{lane}", f"review_plan_label:{label}"],
            utterances=utterances,
            priority=lane_priority[lane] + max(0.0, end - start),
            source_context={
                "type": "readiness_review_plan",
                "source_audit_id": source_audit_id,
                "review_lane": lane,
                "review_action": row.get("review_action"),
                "label": label,
                "verdict": row.get("verdict"),
                "confidence": row.get("confidence"),
                "row": row,
            },
        )


def extract_wav(source: Path, output: Path, start: float, duration: float) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{max(0.0, start):.3f}",
            "-t",
            f"{max(0.05, duration):.3f}",
            "-i",
            str(source),
            "-ar",
            str(SAMPLE_RATE),
            "-ac",
            "1",
            str(output),
        ]
    )


def write_stereo(left: Path, right: Path, output: Path) -> None:
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    output.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(left),
            "-i",
            str(right),
            "-filter_complex",
            "[0:a][1:a]join=inputs=2:channel_layout=stereo[a]",
            "-map",
            "[a]",
            str(output),
        ]
    )


def attach_clips(item: dict[str, Any], sources: dict[str, Path], clips_dir: Path, padding: float) -> None:
    start = max(0.0, float(item["interval"]["start"]) - padding)
    end = float(item["interval"]["end"]) + padding
    duration = max(0.05, end - start)
    clip_id = item["id"]
    clips: dict[str, str] = {}
    for name, source in sources.items():
        if not source.exists():
            continue
        output = clips_dir / f"{clip_id}_{name}.wav"
        extract_wav(source, output, start, duration)
        clips[name] = str(output)
    if "mic_raw" in clips and "remote" in clips:
        stereo = clips_dir / f"{clip_id}_stereo_mic_left_remote_right.wav"
        write_stereo(Path(clips["mic_raw"]), Path(clips["remote"]), stereo)
        clips["stereo_mic_left_remote_right"] = str(stereo)
    if "mic_clean" in clips and "remote" in clips:
        stereo = clips_dir / f"{clip_id}_stereo_clean_left_remote_right.wav"
        write_stereo(Path(clips["mic_clean"]), Path(clips["remote"]), stereo)
        clips["stereo_clean_left_remote_right"] = str(stereo)
    item["clips"] = clips
    item["commands"] = {key: f"afplay {path}" for key, path in clips.items()}


def summarize(items: list[dict[str, Any]], profile: str, out_dir: Path) -> dict[str, Any]:
    reasons = Counter(reason for item in items for reason in item["source_reasons"])
    total_seconds = round(sum(float(item["interval"]["duration_sec"]) for item in items), 3)
    return {
        "schema": SCHEMA_SUMMARY,
        "profile": profile,
        "output_dir": str(out_dir),
        "item_count": len(items),
        "total_suspicious_seconds": total_seconds,
        "by_reason": dict(sorted(reasons.items())),
        "with_clips": sum(1 for item in items if item.get("clips")),
    }


def write_markdown(path: Path, summary: dict[str, Any], items: list[dict[str, Any]]) -> None:
    lines = [
        "# Audio Review Pack",
        "",
        "This pack contains local clips for suspicious transcript regions. It does not change transcripts.",
        "",
        "## Summary",
        "",
        f"- Items: `{summary['item_count']}`",
        f"- Suspicious seconds: `{summary['total_suspicious_seconds']}`",
        f"- With clips: `{summary['with_clips']}`",
        "",
        "## Reasons",
        "",
    ]
    for reason, count in summary["by_reason"].items():
        lines.append(f"- `{reason}`: `{count}`")
    lines.extend(["", "## Top Items", ""])
    for item in items[:30]:
        lines.extend(
            [
                f"### {item['id']} {item['interval']['start_time']}-{item['interval']['end_time']}",
                "",
                f"- Reasons: `{', '.join(item['source_reasons'])}`",
                f"- Priority: `{item['priority_score']}`",
            ]
        )
        for utterance in item["utterances"][:4]:
            lines.append(f"- {utterance.get('role')} `{utterance.get('id')}`: {utterance.get('text')}")
        commands = item.get("commands") or {}
        if commands:
            lines.append(f"- Stereo clean/remote: `{commands.get('stereo_clean_left_remote_right', '')}`")
            lines.append(f"- Raw mic: `{commands.get('mic_raw', '')}`")
        lines.append("")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    session = args.session
    profile = resolve_profile(session, args.profile)
    paths = transcript_paths(session, profile)
    dialogue = read_json(paths["dialogue"])
    if not dialogue:
        raise SystemExit(f"missing clean dialogue for profile {profile}: {paths['dialogue']}")
    utterances = dialogue.get("utterances")
    if not isinstance(utterances, list):
        raise SystemExit(f"invalid clean dialogue: {paths['dialogue']}")
    by_id = {str(row.get("id")): row for row in utterances if isinstance(row, dict) and row.get("id")}

    out_dir = args.out_dir or session / "derived/audit/audio-review-pack"
    out_dir.mkdir(parents=True, exist_ok=True)
    items_by_key: dict[tuple[Any, ...], dict[str, Any]] = {}

    add_needs_review(items_by_key, [row for row in utterances if isinstance(row, dict)])
    overlaps = (read_json(paths["overlaps"]) or {}).get("overlaps")
    add_overlaps(items_by_key, overlaps if isinstance(overlaps, list) else [], by_id, args.min_overlap_sec)
    add_group_audit(items_by_key, read_jsonl(session / "derived/audit/group-overlaps/group_overlap_audit.jsonl"), by_id)
    cleanup_rejections = cleanup_rejections_path(session, profile)
    synthesis_review_items = synthesis_review_items_path(session, profile)
    review_plan_items = review_plan_items_path(session)
    add_cleanup_rejections(
        items_by_key,
        read_jsonl(cleanup_rejections) if cleanup_rejections else [],
        by_id,
    )
    add_synthesis_review_items(
        items_by_key,
        read_jsonl(synthesis_review_items) if synthesis_review_items else [],
        by_id,
    )
    add_review_plan_items(
        items_by_key,
        read_jsonl(review_plan_items) if review_plan_items else [],
        by_id,
    )

    items = sorted(items_by_key.values(), key=lambda item: (-float(item["priority_score"]), float(item["interval"]["start"])))
    if args.max_items > 0:
        items = items[: args.max_items]
    for index, item in enumerate(items, start=1):
        item["id"] = f"arp_{index:06d}"
        item["session_id"] = session.name
        item["profile"] = profile
    if args.write_clips:
        clips_dir = out_dir / "clips"
        sources = source_paths(session)
        for item in items:
            attach_clips(item, sources, clips_dir, args.padding_sec)

    summary = summarize(items, profile, out_dir)
    manifest = {
        "schema": SCHEMA_MANIFEST,
        "generator": {"name": "build-audio-review-pack", "version": SCRIPT_VERSION},
        "session": str(session),
        "session_id": session.name,
        "profile": profile,
        "inputs": {key: str(path) for key, path in paths.items()},
        "audio_sources": {key: {"path": str(path), "exists": path.exists()} for key, path in source_paths(session).items()},
        "outputs": {
            "items": "review_pack_items.jsonl",
            "summary": "review_pack_summary.json",
            "markdown": "review_pack.md",
            "clips": "clips/",
        },
        "summary": summary,
    }
    write_json(out_dir / "review_pack_manifest.json", manifest)
    write_json(out_dir / "review_pack_summary.json", summary)
    write_jsonl(out_dir / "review_pack_items.jsonl", items)
    write_markdown(out_dir / "review_pack.md", summary, items)
    print(f"items: {len(items)}")
    print(f"written: {out_dir / 'review_pack_items.jsonl'}")
    print(f"summary: {out_dir / 'review_pack_summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
