#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


SCHEMA_AUDIT = "murmurmark.transcript_order_audit/v1"
SCHEMA_ITEM = "murmurmark.transcript_order_item/v1"
SCRIPT_VERSION = "0.1.1"
TOKEN_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9_+-]+")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit transcript ordering risks caused by long Me turns crossing remote turns.")
    parser.add_argument("session", type=Path)
    parser.add_argument("--profile", default="auto")
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument("--min-overlap-sec", type=float, default=0.5)
    parser.add_argument("--long-me-sec", type=float, default=6.0)
    parser.add_argument("--tail-sec", type=float, default=0.8)
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


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")


def resolve_profile(session: Path, requested: str) -> str:
    if requested == "authoritative":
        handoff = read_json(session / "derived/pipeline-run/authoritative_handoff.json")
        if (
            not isinstance(handoff, dict)
            or handoff.get("schema") != "murmurmark.authoritative_handoff/v1"
            or handoff.get("status") not in {"ready", "review_required"}
            or not isinstance(handoff.get("selected_transcript_profile"), str)
            or not handoff.get("selected_transcript_profile")
        ):
            raise ValueError("authoritative handoff is missing, invalid, or not ready")
        return str(handoff["selected_transcript_profile"])
    if requested != "auto":
        return requested
    resolved = session / "derived/transcript-simple/whisper-cpp/resolved"
    for profile in (
        "audit_cleanup_v7",
        "agent_reviewed_v1",
        "reviewed_v1",
        "audit_cleanup_v6",
        "audit_cleanup_v5",
        "audit_cleanup_v4",
        "audit_cleanup_v3",
        "audit_cleanup_v2",
        "audit_cleanup_v1",
        "shadow_v2",
        "current",
    ):
        if (resolved / f"clean_dialogue{suffix(profile)}.json").exists():
            return profile
    return "current"


def role_name(row: dict[str, Any]) -> str:
    source = str(row.get("source_track") or "").lower()
    role = str(row.get("role") or row.get("speaker_label") or "").lower()
    if source == "mic" or role == "me":
        return "me"
    if source == "remote" or role in {"remote", "colleagues"}:
        return "remote"
    return role or "unknown"


def row_text(row: dict[str, Any]) -> str:
    return str(row.get("text") or row.get("corrected_text") or row.get("raw_text") or "")


def normalize(text: Any) -> str:
    value = str(text or "").lower().replace("ё", "е")
    value = re.sub(r"[^0-9a-zа-я_+-]+", " ", value)
    return " ".join(value.split())


def text_similarity(left: Any, right: Any) -> float:
    left_text = normalize(left)
    right_text = normalize(right)
    if not left_text or not right_text:
        return 0.0
    return SequenceMatcher(a=left_text, b=right_text).ratio()


def content_tokens(text: Any) -> set[str]:
    stop = {
        "а",
        "в",
        "во",
        "вот",
        "да",
        "для",
        "же",
        "и",
        "или",
        "как",
        "мы",
        "на",
        "не",
        "но",
        "ну",
        "по",
        "с",
        "то",
        "у",
        "что",
        "это",
        "я",
    }
    return {token for token in TOKEN_RE.findall(normalize(text)) if token not in stop and len(token) > 2}


def token_containment(left: Any, right: Any) -> float:
    left_tokens = content_tokens(left)
    if not left_tokens:
        return 0.0
    return len(left_tokens & content_tokens(right)) / max(1, len(left_tokens))


def format_time(seconds: float) -> str:
    total = max(0, int(seconds))
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def utterances(dialogue: dict[str, Any] | None) -> list[dict[str, Any]]:
    rows = (dialogue or {}).get("utterances")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def classify(
    *,
    me: dict[str, Any],
    remote: dict[str, Any],
    overlap: dict[str, Any],
    args: argparse.Namespace,
) -> tuple[str, str, float, dict[str, Any]]:
    me_start = float(me.get("start", 0.0) or 0.0)
    me_end = float(me.get("end", me_start) or me_start)
    remote_start = float(remote.get("start", 0.0) or 0.0)
    remote_end = float(remote.get("end", remote_start) or remote_start)
    me_duration = max(0.0, me_end - me_start)
    remote_duration = max(0.0, remote_end - remote_start)
    overlap_duration = float(overlap.get("duration_sec") or max(0.0, min(me_end, remote_end) - max(me_start, remote_start)))
    pre_remote_lead = max(0.0, remote_start - me_start)
    post_remote_tail = max(0.0, me_end - remote_end)
    remote_inside_me = remote_start >= me_start + 0.2 and remote_end <= me_end - 0.2
    me_wraps_remote = me_start < remote_start and me_end > remote_end
    similarity = float(overlap.get("text_similarity") or text_similarity(row_text(me), row_text(remote)))
    containment = token_containment(row_text(remote), row_text(me))
    overlap_type = str(overlap.get("type") or "")
    features = {
        "me_duration_sec": round(me_duration, 3),
        "remote_duration_sec": round(remote_duration, 3),
        "overlap_duration_sec": round(overlap_duration, 3),
        "pre_remote_lead_sec": round(pre_remote_lead, 3),
        "post_remote_tail_sec": round(post_remote_tail, 3),
        "remote_inside_me": remote_inside_me,
        "me_wraps_remote": me_wraps_remote,
        "text_similarity": round(similarity, 6),
        "remote_text_contained_in_me": round(containment, 6),
        "source_overlap_type": overlap_type or None,
    }

    if similarity >= 0.70 or overlap_type == "probable_duplicate_unresolved":
        return "probable_duplicate", "overlap text is highly similar; order risk is secondary to duplicate cleanup", 0.82, features
    if overlap_duration < args.min_overlap_sec:
        return "short_overlap", "overlap is below the audit threshold", 0.72, features
    if me_duration >= args.long_me_sec and remote_duration >= 0.8 and me_wraps_remote and post_remote_tail >= args.tail_sec:
        confidence = 0.86 if remote_inside_me else 0.78
        return "probable_order_risk", "long Me turn crosses a remote turn and continues after it", confidence, features
    if me_duration >= args.long_me_sec and overlap_duration >= 2.0:
        return "needs_review", "long Me turn has substantial cross-role overlap", 0.68, features
    if overlap_duration <= 2.0:
        return "likely_timing_overlap", "short overlap is likely timestamp boundary noise", 0.74, features
    return "possible_double_talk", "overlap may be normal double-talk rather than ordering damage", 0.64, features


def build_items(dialogue_rows: list[dict[str, Any]], overlaps: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    by_id = {str(row.get("id")): row for row in dialogue_rows if row.get("id")}
    items: list[dict[str, Any]] = []
    for index, overlap in enumerate(overlaps, start=1):
        left = by_id.get(str(overlap.get("left_utterance_id") or ""))
        right = by_id.get(str(overlap.get("right_utterance_id") or ""))
        if not left or not right:
            continue
        roles = {role_name(left), role_name(right)}
        if roles != {"me", "remote"}:
            continue
        me = left if role_name(left) == "me" else right
        remote = right if me is left else left
        label, reason, confidence, features = classify(me=me, remote=remote, overlap=overlap, args=args)
        if label == "short_overlap" and features["overlap_duration_sec"] < args.min_overlap_sec:
            continue
        items.append(
            {
                "schema": SCHEMA_ITEM,
                "item_id": f"order_{len(items) + 1:04d}",
                "label": label,
                "confidence": round(confidence, 3),
                "reason": reason,
                "interval": {
                    "start": round(float(overlap.get("start", max(float(me.get("start", 0.0) or 0.0), float(remote.get("start", 0.0) or 0.0))) or 0.0), 3),
                    "end": round(float(overlap.get("end", min(float(me.get("end", 0.0) or 0.0), float(remote.get("end", 0.0) or 0.0))) or 0.0), 3),
                    "duration_sec": features["overlap_duration_sec"],
                },
                "utterances": {
                    "me": {
                        "id": me.get("id"),
                        "start": me.get("start"),
                        "end": me.get("end"),
                        "text": row_text(me),
                        "needs_review": bool((me.get("quality") or {}).get("needs_review")) if isinstance(me.get("quality"), dict) else False,
                    },
                    "remote": {
                        "id": remote.get("id"),
                        "start": remote.get("start"),
                        "end": remote.get("end"),
                        "text": row_text(remote),
                        "needs_review": bool((remote.get("quality") or {}).get("needs_review")) if isinstance(remote.get("quality"), dict) else False,
                    },
                },
                "features": features,
                "commands": {
                    "inspect_transcript": f"rg -n '{me.get('id')}|{remote.get('id')}' <transcript-or-json>",
                },
            }
        )
    return items


def summarize(items: list[dict[str, Any]]) -> dict[str, Any]:
    by_label = Counter(str(item.get("label") or "unknown") for item in items)
    seconds_by_label: Counter[str] = Counter()
    for item in items:
        seconds_by_label[str(item.get("label") or "unknown")] += float((item.get("interval") or {}).get("duration_sec") or 0.0)
    order_risk_seconds = seconds_by_label["probable_order_risk"]
    needs_review_seconds = seconds_by_label["needs_review"]
    blocking = order_risk_seconds > 0.0 or needs_review_seconds >= 10.0
    return {
        "audited_overlap_count": len(items),
        "by_label": {
            label: {
                "count": by_label[label],
                "seconds": round(seconds_by_label[label], 3),
            }
            for label in sorted(by_label)
        },
        "probable_order_risk_count": by_label["probable_order_risk"],
        "probable_order_risk_seconds": round(order_risk_seconds, 3),
        "needs_review_count": by_label["needs_review"],
        "needs_review_seconds": round(needs_review_seconds, 3),
        "blocking_order_risk": blocking,
        "recommended_next_step": "review_transcript_order_items" if blocking else "order_risk_explained",
    }


def write_review(path: Path, session: Path, profile: str, summary: dict[str, Any], items: list[dict[str, Any]]) -> None:
    risky = [item for item in items if item.get("label") in {"probable_order_risk", "needs_review"}]
    risky.sort(key=lambda item: (str(item.get("label")) != "probable_order_risk", -float((item.get("interval") or {}).get("duration_sec") or 0.0)))
    lines = [
        "# Transcript Order Audit",
        "",
        f"- Session: `{session}`",
        f"- Profile: `{profile}`",
        f"- Audited overlaps: `{summary['audited_overlap_count']}`",
        f"- Probable order risk: `{summary['probable_order_risk_count']}` / `{summary['probable_order_risk_seconds']}` sec",
        f"- Needs review: `{summary['needs_review_count']}` / `{summary['needs_review_seconds']}` sec",
        f"- Blocking order risk: `{str(summary['blocking_order_risk']).lower()}`",
        f"- Recommended next step: `{summary['recommended_next_step']}`",
        "",
    ]
    if risky:
        lines += ["## Risky Items", ""]
        for item in risky[:25]:
            interval = item.get("interval") or {}
            me = (item.get("utterances") or {}).get("me") or {}
            remote = (item.get("utterances") or {}).get("remote") or {}
            features = item.get("features") or {}
            lines += [
                f"### {item['item_id']} `{item['label']}` {format_time(float(interval.get('start', 0.0) or 0.0))}-{format_time(float(interval.get('end', 0.0) or 0.0))}",
                "",
                f"- Confidence: `{item.get('confidence')}`",
                f"- Reason: {item.get('reason')}",
                f"- Me `{me.get('id')}`: {me.get('text')}",
                f"- Colleagues `{remote.get('id')}`: {remote.get('text')}",
                f"- Me duration: `{features.get('me_duration_sec')}` sec; post-remote tail: `{features.get('post_remote_tail_sec')}` sec",
                "",
            ]
    else:
        lines += ["No probable order risks found.", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    session = args.session.expanduser()
    profile = resolve_profile(session, args.profile)
    out_dir = args.out_dir.expanduser() if args.out_dir else session / "derived/audit/order"
    resolved = session / "derived/transcript-simple/whisper-cpp/resolved"
    dialogue = read_json(resolved / f"clean_dialogue{suffix(profile)}.json")
    overlaps_payload = read_json(resolved / f"overlaps{suffix(profile)}.json")
    if dialogue is None or overlaps_payload is None:
        summary = {
            "audited_overlap_count": 0,
            "by_label": {},
            "probable_order_risk_count": 0,
            "probable_order_risk_seconds": 0.0,
            "needs_review_count": 0,
            "needs_review_seconds": 0.0,
            "blocking_order_risk": True,
            "recommended_next_step": "missing_transcript_or_overlaps",
        }
        payload = {
            "schema": SCHEMA_AUDIT,
            "generator": {"name": "audit-transcript-order", "version": SCRIPT_VERSION},
            "session": str(session),
            "profile": profile,
            "status": "missing_inputs",
            "inputs": {
                "clean_dialogue": str(resolved / f"clean_dialogue{suffix(profile)}.json"),
                "overlaps": str(resolved / f"overlaps{suffix(profile)}.json"),
            },
            "parameters": {
                "min_overlap_sec": args.min_overlap_sec,
                "long_me_sec": args.long_me_sec,
                "tail_sec": args.tail_sec,
            },
            "summary": summary,
        }
        write_json(out_dir / "transcript_order_audit.json", payload)
        write_jsonl(out_dir / "transcript_order_items.jsonl", [])
        write_review(out_dir / "transcript_order_review.md", session, profile, summary, [])
        print(f"transcript_order_audit: {out_dir / 'transcript_order_audit.json'}")
        print("status: missing_inputs")
        return 2

    rows = utterances(dialogue)
    overlaps = overlaps_payload.get("overlaps") if isinstance(overlaps_payload.get("overlaps"), list) else []
    items = build_items(rows, [row for row in overlaps if isinstance(row, dict)], args)
    summary = summarize(items)
    payload = {
        "schema": SCHEMA_AUDIT,
        "generator": {"name": "audit-transcript-order", "version": SCRIPT_VERSION},
        "session": str(session),
        "profile": profile,
        "status": "ok",
        "inputs": {
            "clean_dialogue": str(resolved / f"clean_dialogue{suffix(profile)}.json"),
            "overlaps": str(resolved / f"overlaps{suffix(profile)}.json"),
        },
        "parameters": {
            "min_overlap_sec": args.min_overlap_sec,
            "long_me_sec": args.long_me_sec,
            "tail_sec": args.tail_sec,
        },
        "summary": summary,
    }
    write_json(out_dir / "transcript_order_audit.json", payload)
    write_jsonl(out_dir / "transcript_order_items.jsonl", items)
    write_review(out_dir / "transcript_order_review.md", session, profile, summary, items)
    print(f"transcript_order_audit: {out_dir / 'transcript_order_audit.json'}")
    print(f"probable_order_risk_seconds: {summary['probable_order_risk_seconds']}")
    print(f"recommended_next_step: {summary['recommended_next_step']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
