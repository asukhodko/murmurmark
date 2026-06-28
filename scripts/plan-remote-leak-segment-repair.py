#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_VERSION = "0.1.0"
SCHEMA_PLAN = "murmurmark.remote_leak_segment_repair_plan/v1"
SCHEMA_ITEM = "murmurmark.remote_leak_segment_repair_item/v1"
TOKEN_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё_./+-]+")

STOP_WORDS = {
    "а",
    "в",
    "во",
    "вот",
    "да",
    "для",
    "же",
    "и",
    "как",
    "на",
    "не",
    "ну",
    "о",
    "он",
    "она",
    "они",
    "по",
    "просто",
    "с",
    "там",
    "то",
    "тут",
    "у",
    "это",
    "что",
    "я",
}

DOMAIN_TERMS = {
    "api",
    "backend",
    "deploy",
    "gitlab",
    "kubernetes",
    "pipeline",
    "бэкенд",
    "деплой",
    "квота",
    "логи",
    "пайплайн",
    "сервис",
    "троттлинг",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build an audit-only plan for segment-level remote leak repair.")
    parser.add_argument("session", type=Path)
    parser.add_argument(
        "--audit",
        type=Path,
        default=None,
        help="audio_review_audit.jsonl. Default: SESSION/derived/audit/audio-review-pack/audio_review_audit.jsonl",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Output dir. Default: SESSION/derived/transcript-simple/whisper-cpp/remote-leak-repair",
    )
    parser.add_argument("--min-local-support", type=float, default=35.0)
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


def safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def suffix_path(session: Path, explicit: Path | None) -> Path:
    return explicit or session / "derived/transcript-simple/whisper-cpp/remote-leak-repair"


def format_time(seconds: float | int | None) -> str:
    total = max(0, int(float(seconds or 0)))
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def tokens(text: Any) -> list[str]:
    return [token.lower().replace("ё", "е") for token in TOKEN_RE.findall(str(text or ""))]


def content_tokens(text: Any) -> list[str]:
    return [token for token in tokens(text) if token not in STOP_WORDS and len(token) > 2]


def domain_terms(text: Any) -> list[str]:
    return sorted({token for token in tokens(text) if token in DOMAIN_TERMS})


def role_of(row: dict[str, Any]) -> str:
    role = str(row.get("role") or "").lower()
    source = str(row.get("source_track") or "").lower()
    if role == "me" or source == "mic":
        return "me"
    if "colleague" in role or role == "remote" or source == "remote":
        return "remote"
    return role or source


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


def remote_leak_diagnostic(row: dict[str, Any], min_local_support: float) -> dict[str, Any]:
    scores = row.get("scores") if isinstance(row.get("scores"), dict) else {}
    features = row.get("features") if isinstance(row.get("features"), dict) else {}
    text = features.get("text") if isinstance(features.get("text"), dict) else {}
    local = safe_float(scores.get("local_support"))
    duplicate = safe_float(scores.get("remote_duplicate"))
    similarity = safe_float(text.get("similarity"))
    me_text = text.get("me_text") or " ".join(
        str(utterance.get("text") or "")
        for utterance in row.get("utterances") or []
        if isinstance(utterance, dict) and role_of(utterance) == "me"
    )
    unique_tokens = content_tokens(me_text)
    terms = domain_terms(me_text)

    if similarity >= 0.65 or duplicate >= 70:
        return {
            "label": "remote_leak_duplicate_like",
            "reason": "leak row also looks textually similar to remote",
            "protect_local_content": len(unique_tokens) >= 3 or bool(terms),
        }
    if local >= min_local_support and (len(unique_tokens) >= 2 or bool(terms)):
        return {
            "label": "remote_leak_with_local_content_risk",
            "reason": "local support or unique text makes whole-utterance deletion unsafe",
            "protect_local_content": True,
        }
    return {
        "label": "remote_leak_plain",
        "reason": "remote leak is likely but local-content evidence is weak",
        "protect_local_content": False,
    }


def proposed_strategy(diagnostic: dict[str, Any]) -> dict[str, Any]:
    label = diagnostic.get("label")
    if label == "remote_leak_with_local_content_risk":
        return {
            "action": "segment_level_repair_candidate",
            "future_patch_type": "split_or_reasr_local_islands",
            "whole_me_drop_allowed": False,
            "notes": "Preserve Me utterance text; only a future segment-level repair may touch the leak interval.",
        }
    if label == "remote_leak_duplicate_like":
        return {
            "action": "review_duplicate_like_leak",
            "future_patch_type": "duplicate_gate_or_mark_only",
            "whole_me_drop_allowed": False,
            "notes": "Text similarity is high, but parent class is remote_leak; require stronger evidence before deletion.",
        }
    return {
        "action": "mark_remote_leak_interval",
        "future_patch_type": "mark_only",
        "whole_me_drop_allowed": False,
        "notes": "Keep transcript unchanged and carry explicit remote-leak evidence.",
    }


def build_item(row: dict[str, Any], index: int, min_local_support: float) -> dict[str, Any]:
    interval = row.get("interval") if isinstance(row.get("interval"), dict) else {}
    features = row.get("features") if isinstance(row.get("features"), dict) else {}
    text = features.get("text") if isinstance(features.get("text"), dict) else {}
    utterances = [utterance for utterance in row.get("utterances") or [] if isinstance(utterance, dict)]
    me_text = text.get("me_text") or " ".join(str(item.get("text") or "") for item in utterances if role_of(item) == "me")
    remote_text = text.get("remote_text") or " ".join(str(item.get("text") or "") for item in utterances if role_of(item) == "remote")
    diagnostic = remote_leak_diagnostic(row, min_local_support)
    strategy = proposed_strategy(diagnostic)
    start = safe_float(interval.get("start"))
    end = safe_float(interval.get("end"))
    if end <= start:
        end = start + safe_float(interval.get("duration_sec"))
    return {
        "schema": SCHEMA_ITEM,
        "id": f"rlr_{index:06d}",
        "session_id": row.get("session_id"),
        "source_audit_id": row.get("id"),
        "profile": row.get("profile"),
        "interval": {
            "start": round(start, 3),
            "end": round(end, 3),
            "duration_sec": round(max(0.0, end - start), 3),
            "start_time": format_time(start),
            "end_time": format_time(end),
        },
        "utterance_ids": row.get("utterance_ids", []),
        "utterances": [compact_utterance(item) for item in utterances],
        "diagnostic": diagnostic,
        "proposal": strategy,
        "evidence": {
            "scores": row.get("scores") if isinstance(row.get("scores"), dict) else {},
            "text": {
                "me_text": me_text,
                "remote_text": remote_text,
                "similarity": text.get("similarity"),
                "content_tokens": content_tokens(me_text),
                "domain_terms": domain_terms(me_text),
            },
            "audio_features": {
                "rms_db": features.get("rms_db", {}),
                "energy_delta_db": features.get("energy_delta_db", {}),
                "xcorr": features.get("xcorr", {}),
                "spectral_cosine": features.get("spectral_cosine", {}),
            },
        },
        "commands": row.get("commands", {}),
    }


def selected_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for row in rows:
        classification = row.get("classification") if isinstance(row.get("classification"), dict) else {}
        if classification.get("label") != "remote_leak":
            continue
        if classification.get("verdict") != "probable_transcript_error":
            continue
        output.append(row)
    output.sort(
        key=lambda row: (
            -safe_float((row.get("interval") or {}).get("duration_sec")),
            safe_float((row.get("interval") or {}).get("start")),
        )
    )
    return output


def summarize(items: list[dict[str, Any]], session: Path, audit_path: Path) -> dict[str, Any]:
    by_diag = Counter(str(item.get("diagnostic", {}).get("label") or "unknown") for item in items)
    protect = [item for item in items if item.get("diagnostic", {}).get("protect_local_content")]
    seconds = sum(safe_float(item.get("interval", {}).get("duration_sec")) for item in items)
    return {
        "schema": SCHEMA_PLAN,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": {"name": "plan-remote-leak-segment-repair", "version": SCRIPT_VERSION},
        "inputs": {
            "session": str(session),
            "audio_review_audit": str(audit_path),
        },
        "summary": {
            "items": len(items),
            "seconds": round(seconds, 3),
            "protect_local_content_items": len(protect),
            "protect_local_content_seconds": round(
                sum(safe_float(item.get("interval", {}).get("duration_sec")) for item in protect),
                3,
            ),
            "by_diagnostic": dict(sorted(by_diag.items())),
        },
        "action_plan": [
            {
                "next_work": "implement_segment_level_remote_leak_repair",
                "diagnostic": "remote_leak_with_local_content_risk",
                "items": len(protect),
                "deliverable": "separate transcript profile that protects Me text and only edits verified leak segments",
            }
        ]
        if protect
        else [
            {
                "next_work": "keep_remote_leak_mark_only",
                "diagnostic": "remote_leak_plain",
                "items": len(items),
                "deliverable": "no transcript edit; keep explicit review markers",
            }
        ],
        "policy": {
            "mode": "audit_only",
            "may_modify_transcript": False,
            "may_modify_raw_audio": False,
            "whole_me_drop_allowed": False,
        },
    }


def write_markdown(path: Path, plan: dict[str, Any], items: list[dict[str, Any]]) -> None:
    summary = plan["summary"]
    lines = [
        "# Remote Leak Segment Repair Plan",
        "",
        "Audit-only plan. It does not edit transcript profiles or raw audio.",
        "",
        "## Summary",
        "",
        f"- Items: `{summary['items']}`",
        f"- Seconds: `{summary['seconds']}`",
        f"- Protect local content: `{summary['protect_local_content_items']}` items / `{summary['protect_local_content_seconds']}` sec",
        f"- By diagnostic: `{json.dumps(summary['by_diagnostic'], ensure_ascii=False, sort_keys=True)}`",
        "",
        "## Action Plan",
        "",
    ]
    for row in plan["action_plan"]:
        lines.extend(
            [
                f"- `{row['next_work']}` for `{row['diagnostic']}`",
                f"  - items: `{row['items']}`",
                f"  - deliverable: {row['deliverable']}",
            ]
        )
    lines.extend(["", "## Items", ""])
    for item in items[:30]:
        diagnostic = item["diagnostic"]
        proposal = item["proposal"]
        interval = item["interval"]
        evidence = item["evidence"]
        lines.extend(
            [
                f"### {item['id']} `{diagnostic['label']}` {interval['start_time']}-{interval['end_time']}",
                "",
                f"- Source audit: `{item.get('source_audit_id')}`",
                f"- Utterances: `{', '.join(item.get('utterance_ids') or [])}`",
                f"- Proposal: `{proposal['action']}` / `{proposal['future_patch_type']}`",
                f"- Whole Me drop allowed: `{proposal['whole_me_drop_allowed']}`",
                f"- Protect local content: `{diagnostic['protect_local_content']}`",
                f"- Reason: {diagnostic['reason']}",
                f"- Me text: {evidence['text']['me_text']}",
                f"- Remote text: {evidence['text']['remote_text']}",
            ]
        )
        commands = item.get("commands") if isinstance(item.get("commands"), dict) else {}
        stereo = commands.get("stereo_clean_left_remote_right") or commands.get("stereo_mic_left_remote_right")
        if stereo:
            lines.append(f"- Stereo: `{stereo}`")
        lines.append("")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    session = args.session
    audit_path = args.audit or session / "derived/audit/audio-review-pack/audio_review_audit.jsonl"
    out_dir = suffix_path(session, args.out_dir)
    rows = read_jsonl(audit_path)
    items = [build_item(row, index, args.min_local_support) for index, row in enumerate(selected_rows(rows), start=1)]
    plan = summarize(items, session, audit_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(out_dir / "remote_leak_segment_repair_plan.json", plan)
    write_jsonl(out_dir / "remote_leak_segment_repair_items.jsonl", items)
    write_markdown(out_dir / "remote_leak_segment_repair.md", plan, items)
    print(f"items: {plan['summary']['items']}")
    print(f"protect_local_content_items: {plan['summary']['protect_local_content_items']}")
    print(f"plan: {out_dir / 'remote_leak_segment_repair_plan.json'}")
    print(f"report: {out_dir / 'remote_leak_segment_repair.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
