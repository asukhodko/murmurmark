#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


SCHEMA_ROW = "murmurmark.faster_whisper_judge/v1"
SCHEMA_SUMMARY = "murmurmark.faster_whisper_judge_summary/v1"
SCRIPT_VERSION = "0.1.0"
DEFAULT_MODEL = Path.home() / ".local/share/murmurmark/models/faster-whisper/large-v3"
DEFAULT_MAX_ITEMS = 80
DEFAULT_SOURCES = ("mic_role_masked", "mic_clean", "mic_raw", "remote")
STOP_WORDS = {
    "а",
    "и",
    "но",
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
    "по",
    "за",
    "из",
    "с",
    "со",
    "что",
    "чтобы",
    "мы",
    "ты",
    "он",
    "она",
    "они",
    "оно",
    "я",
}
MEANINGFUL_SHORT_UTTERANCES = {
    "да",
    "нет",
    "ок",
    "окей",
    "ага",
    "угу",
    "неа",
    "ну",
    "вот",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Use local faster-whisper as a stronger audio judge for review clips.")
    parser.add_argument("session", type=Path)
    parser.add_argument("--profile", default="auto", help="Profile used for the audio-review pack. Written to reports.")
    parser.add_argument("--pack-dir", type=Path, default=None)
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument("--model", type=Path, default=None)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--compute-type", default="int8")
    parser.add_argument("--language", default="ru")
    parser.add_argument("--max-items", type=int, default=DEFAULT_MAX_ITEMS)
    parser.add_argument("--beam-size", type=int, default=1)
    parser.add_argument("--allow-download", action="store_true", help="Allow Hugging Face network access.")
    parser.add_argument("--no-cache", action="store_true", help="Recompute selected clips even when cached judge rows exist.")
    parser.add_argument(
        "--pack-item-id",
        action="append",
        default=[],
        help="Audit a specific audio-review pack item id, e.g. arp_000042. Can repeat.",
    )
    parser.add_argument(
        "--review-lane-pack",
        action="append",
        type=Path,
        default=[],
        help="Read source_audit_id/source_audit_ids from a review lane pack and audit those pack items first.",
    )
    parser.add_argument("--source", action="append", choices=DEFAULT_SOURCES, help="Clip source to decode. Can repeat.")
    parser.add_argument("--write-clips", action=argparse.BooleanOptionalAction, default=None, help=argparse.SUPPRESS)
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def collect_source_audit_ids(value: Any) -> list[str]:
    ids: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "source_audit_id" and child:
                ids.append(str(child))
            elif key == "source_audit_ids" and isinstance(child, list):
                ids.extend(str(item) for item in child if item)
            else:
                ids.extend(collect_source_audit_ids(child))
    elif isinstance(value, list):
        for item in value:
            ids.extend(collect_source_audit_ids(item))
    return ids


def list_strings(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if item is not None and str(item)]
    return []


def lane_pack_selectors(args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[str]]:
    selectors: list[dict[str, Any]] = []
    missing_pack_files: list[str] = []
    for path in args.review_lane_pack:
        lane_pack = read_json(path.expanduser())
        if lane_pack is None:
            missing_pack_files.append(str(path))
            continue
        for item in lane_pack.get("items") or []:
            if not isinstance(item, dict):
                continue
            selectors.append(
                {
                    "source_ids": collect_source_audit_ids(item),
                    "utterance_ids": list_strings(item.get("utterance_ids")),
                    "me_utterance_ids": list_strings(item.get("me_utterance_ids")),
                    "remote_utterance_ids": list_strings(item.get("remote_utterance_ids")),
                }
            )
    return selectors, missing_pack_files


def item_id_set(item: dict[str, Any]) -> set[str]:
    ids = set(str(value) for value in item.get("utterance_ids") or [] if value)
    ids.update(utterance_ids(item))
    return ids


def item_matches_lane_selector(item: dict[str, Any], selector: dict[str, Any]) -> bool:
    item_ids = item_id_set(item)
    selector_ids = set(selector.get("utterance_ids") or [])
    me_ids = set(selector.get("me_utterance_ids") or [])
    remote_ids = set(selector.get("remote_utterance_ids") or [])
    if item_ids and selector_ids and (item_ids <= selector_ids or selector_ids <= item_ids):
        return True
    if me_ids and item_ids & me_ids:
        return True
    if not me_ids and remote_ids and item_ids & remote_ids:
        return True
    return False


def target_item_ids(args: argparse.Namespace, items: list[dict[str, Any]]) -> tuple[list[str], list[str], list[str]]:
    ids = [str(value).strip() for value in args.pack_item_id if str(value).strip()]
    selectors, missing_pack_files = lane_pack_selectors(args)
    matched_selector_ids: list[str] = []
    for selector in selectors:
        matched = [str(item.get("id") or "") for item in items if item.get("id") and item_matches_lane_selector(item, selector)]
        if matched:
            matched_selector_ids.extend(matched)
        else:
            ids.extend(str(value) for value in selector.get("source_ids") or [] if value)
    ids = list(matched_selector_ids) + ids
    return list(dict.fromkeys(ids)), missing_pack_files, [",".join(selector.get("utterance_ids") or []) for selector in selectors]


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


def normalize_text(value: Any) -> str:
    text = str(value or "").lower().replace("ё", "е")
    text = re.sub(r"[^0-9a-zа-я_./+-]+", " ", text)
    return " ".join(text.split())


def content_tokens(value: Any) -> list[str]:
    return [token for token in normalize_text(value).split() if token not in STOP_WORDS and len(token) > 1]


def looks_like_noise_fragment(value: Any) -> bool:
    tokens = normalize_text(value).split()
    if not tokens or len(tokens) > 2:
        return False
    text = " ".join(tokens)
    if text in MEANINGFUL_SHORT_UTTERANCES:
        return False
    return all(len(token) <= 2 for token in tokens)


def text_similarity(left: Any, right: Any) -> dict[str, float]:
    left_norm = normalize_text(left)
    right_norm = normalize_text(right)
    sequence = SequenceMatcher(None, left_norm, right_norm).ratio() if left_norm or right_norm else 0.0
    left_tokens = set(content_tokens(left_norm))
    right_tokens = set(content_tokens(right_norm))
    if left_tokens and right_tokens:
        containment = len(left_tokens & right_tokens) / max(1, min(len(left_tokens), len(right_tokens)))
        jaccard = len(left_tokens & right_tokens) / max(1, len(left_tokens | right_tokens))
        length_ratio = min(len(left_tokens), len(right_tokens)) / max(1, max(len(left_tokens), len(right_tokens)))
    else:
        containment = 0.0
        jaccard = 0.0
        length_ratio = 0.0
    containment_score = containment * min(1.0, length_ratio * 2.0)
    return {
        "sequence_ratio": round(sequence, 6),
        "containment": round(containment, 6),
        "jaccard": round(jaccard, 6),
        "length_ratio": round(length_ratio, 6),
        "similarity": round(max(sequence, containment_score, jaccard), 6),
    }


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(number) or math.isinf(number):
        return default
    return number


def format_time(seconds: float) -> str:
    total = max(0, int(seconds))
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def resolve_model(args: argparse.Namespace) -> Path:
    if args.model is not None:
        return args.model.expanduser()
    env_value = os.environ.get("MURMURMARK_FASTER_WHISPER_MODEL")
    if env_value:
        return Path(env_value).expanduser()
    return DEFAULT_MODEL


def model_ready(model_path: Path) -> tuple[bool, str]:
    if not model_path.exists():
        return False, f"model path not found: {model_path}"
    if model_path.is_dir() and not (model_path / "model.bin").exists():
        return False, f"model.bin not found under: {model_path}"
    if model_path.is_file() and model_path.name != "model.bin":
        return False, f"expected CTranslate2 model directory or model.bin: {model_path}"
    return True, "ok"


def utterance_texts(item: dict[str, Any]) -> tuple[str, str]:
    me: list[str] = []
    remote: list[str] = []
    for row in item.get("utterances") or []:
        if not isinstance(row, dict):
            continue
        role = str(row.get("role") or "").lower()
        track = str(row.get("source_track") or "").lower()
        text = str(row.get("text") or "")
        if role == "me" or track == "mic":
            me.append(text)
        elif role == "remote" or "colleague" in role or track == "remote":
            remote.append(text)
    return " ".join(me).strip(), " ".join(remote).strip()


def utterance_ids(item: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for row in item.get("utterances") or []:
        if isinstance(row, dict) and row.get("id"):
            ids.append(str(row["id"]))
    return list(dict.fromkeys(ids))


def item_fingerprint_payload(item: dict[str, Any]) -> dict[str, Any]:
    interval = item.get("interval") if isinstance(item.get("interval"), dict) else {}
    utterances: list[dict[str, Any]] = []
    for row in item.get("utterances") or []:
        if not isinstance(row, dict):
            continue
        utterances.append(
            {
                "id": str(row.get("id") or ""),
                "role": str(row.get("role") or ""),
                "source_track": str(row.get("source_track") or ""),
                "start": round(safe_float(row.get("start")), 3),
                "end": round(safe_float(row.get("end")), 3),
                "text": normalize_text(row.get("text")),
            }
        )
    return {
        "session_id": str(item.get("session_id") or ""),
        "profile": str(item.get("profile") or ""),
        "interval": {
            "start": round(safe_float(interval.get("start")), 3),
            "end": round(safe_float(interval.get("end")), 3),
        },
        "utterance_ids": [str(value) for value in item.get("utterance_ids") or []],
        "utterances": utterances,
    }


def item_fingerprint(item: dict[str, Any]) -> str:
    payload = item_fingerprint_payload(item)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def cached_row_matches_item(row: dict[str, Any], item: dict[str, Any]) -> bool:
    expected = item_fingerprint(item)
    actual = str(row.get("source_pack_item_fingerprint") or "")
    if actual:
        return actual == expected
    # Compatibility for rows written before fingerprints existed.
    return item_fingerprint_payload(row) == item_fingerprint_payload(item)


def audit_by_id(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row.get("id")): row for row in rows if row.get("id")}


def audit_row_matches_item(row: dict[str, Any] | None, item: dict[str, Any]) -> bool:
    if not row:
        return False
    return item_fingerprint_payload(row) == item_fingerprint_payload(item)


def item_priority(item: dict[str, Any], audit_row: dict[str, Any] | None) -> tuple[int, float, str]:
    reasons = set(str(value) for value in item.get("source_reasons") or [])
    classification = audit_row.get("classification") if isinstance(audit_row, dict) else {}
    if not isinstance(classification, dict):
        classification = {}
    label = str(classification.get("label") or "")
    verdict = str(classification.get("verdict") or "")
    duration = safe_float((item.get("interval") or {}).get("duration_sec"), 0.0)
    if verdict == "needs_stronger_audio_judge" or label == "uncertain":
        return (0, -duration, str(item.get("id") or ""))
    if any("local_recall" in reason for reason in reasons):
        return (1, -duration, str(item.get("id") or ""))
    if any("cross_role_overlap" in reason or "group_overlap:needs_human_review" in reason for reason in reasons):
        return (2, -duration, str(item.get("id") or ""))
    if verdict == "probable_transcript_error":
        return (3, -duration, str(item.get("id") or ""))
    return (9, -duration, str(item.get("id") or ""))


def selected_items(
    items: list[dict[str, Any]],
    audit_rows: dict[str, dict[str, Any]],
    limit: int,
    *,
    target_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    if target_ids:
        by_id = {str(item.get("id") or ""): item for item in items}
        targeted = [by_id[item_id] for item_id in target_ids if item_id in by_id]
        if limit > 0:
            return targeted[:limit]
        return targeted
    ranked = sorted(items, key=lambda item: item_priority(item, audit_rows.get(str(item.get("id") or ""))))
    selected = [item for item in ranked if item_priority(item, audit_rows.get(str(item.get("id") or "")))[0] < 9]
    if not selected:
        selected = ranked
    return selected[: max(0, limit)]


def load_model(model_path: Path, args: argparse.Namespace) -> Any:
    if not args.allow_download:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
    try:
        from faster_whisper import WhisperModel
    except ImportError as error:
        raise SystemExit("missing faster_whisper module; install faster-whisper ctranslate2") from error
    return WhisperModel(str(model_path), device=args.device, compute_type=args.compute_type)


def transcribe_clip(model: Any, path: Path, args: argparse.Namespace) -> dict[str, Any]:
    if not path.exists() or path.stat().st_size <= 0:
        return {"path": str(path), "exists": False, "text": "", "segments": [], "avg_logprob": None, "no_speech_prob": None}
    try:
        segments, info = model.transcribe(
            str(path),
            language=args.language,
            beam_size=args.beam_size,
            vad_filter=False,
            condition_on_previous_text=False,
            word_timestamps=False,
        )
        rows: list[dict[str, Any]] = []
        for segment in segments:
            rows.append(
                {
                    "start": round(float(segment.start), 3),
                    "end": round(float(segment.end), 3),
                    "text": str(segment.text or "").strip(),
                    "avg_logprob": round(safe_float(getattr(segment, "avg_logprob", None), 0.0), 6),
                    "no_speech_prob": round(safe_float(getattr(segment, "no_speech_prob", None), 0.0), 6),
                }
            )
    except Exception as error:
        return {"path": str(path), "exists": True, "error": str(error), "text": "", "segments": []}
    text = " ".join(row["text"] for row in rows if row.get("text")).strip()
    durations = [max(0.0, row["end"] - row["start"]) for row in rows]
    total_duration = sum(durations)
    if rows and total_duration > 0:
        avg_logprob = sum(row["avg_logprob"] * duration for row, duration in zip(rows, durations)) / total_duration
        no_speech_prob = max(row["no_speech_prob"] for row in rows)
    else:
        avg_logprob = None
        no_speech_prob = None
    return {
        "path": str(path),
        "exists": True,
        "text": text,
        "segments": rows,
        "segment_count": len(rows),
        "language": getattr(info, "language", args.language),
        "language_probability": round(safe_float(getattr(info, "language_probability", 0.0), 0.0), 6),
        "avg_logprob": round(avg_logprob, 6) if avg_logprob is not None else None,
        "no_speech_prob": round(no_speech_prob, 6) if no_speech_prob is not None else None,
    }


def source_metrics(transcripts: dict[str, dict[str, Any]], me_text: str, remote_text: str) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    remote_reference_text = remote_text or str(transcripts.get("remote", {}).get("text") or "")
    for source, result in transcripts.items():
        text = str(result.get("text") or "")
        metrics[source] = {
            "text_len": len(text),
            "content_token_count": len(content_tokens(text)),
            "to_me": text_similarity(text, me_text),
            "to_remote": text_similarity(text, remote_reference_text),
            "avg_logprob": result.get("avg_logprob"),
            "no_speech_prob": result.get("no_speech_prob"),
        }
    return metrics


def best_score(metrics: dict[str, Any], sources: tuple[str, ...], target: str) -> tuple[float, str]:
    best = (0.0, "")
    for source in sources:
        score = safe_float(metrics.get(source, {}).get(target, {}).get("similarity"), 0.0)
        if score > best[0]:
            best = (score, source)
    return best


def classify_item(
    item: dict[str, Any],
    audit_row: dict[str, Any] | None,
    transcripts: dict[str, dict[str, Any]],
    metrics: dict[str, Any],
) -> dict[str, Any]:
    me_text, remote_text = utterance_texts(item)
    remote_reference_text = remote_text or str(transcripts.get("remote", {}).get("text") or "")
    me_tokens = content_tokens(me_text)
    remote_tokens = content_tokens(remote_reference_text)
    mic_sources = tuple(source for source in ("mic_role_masked", "mic_clean", "mic_raw") if source in metrics)
    clean_sources = tuple(source for source in ("mic_role_masked", "mic_clean") if source in metrics)
    best_me, best_me_source = best_score(metrics, clean_sources or mic_sources, "to_me")
    best_me_any, best_me_any_source = best_score(metrics, mic_sources, "to_me")
    best_remote_in_mic, remote_in_mic_source = best_score(metrics, mic_sources, "to_remote")
    remote_source_to_remote = safe_float(metrics.get("remote", {}).get("to_remote", {}).get("similarity"), 0.0)
    remote_source_tokens = int(metrics.get("remote", {}).get("content_token_count") or 0)
    mic_content_tokens = max(int(metrics.get(source, {}).get("content_token_count") or 0) for source in mic_sources) if mic_sources else 0

    audit_class = audit_row.get("classification") if isinstance(audit_row, dict) else {}
    if not isinstance(audit_class, dict):
        audit_class = {}
    audit_scores = audit_row.get("scores") if isinstance(audit_row, dict) else {}
    if not isinstance(audit_scores, dict):
        audit_scores = {}
    audit_label = str(audit_class.get("label") or "")
    audit_verdict = str(audit_class.get("verdict") or "")
    local_support = safe_float(audit_scores.get("local_support"), 0.0)
    remote_similarity = safe_float(audit_scores.get("remote_similarity"), 0.0)

    reasons: list[str] = []
    label = "uncertain"
    suggested = "needs_review"
    confidence = 0.5

    me_confirmed = best_me >= 0.46 or best_me_any >= 0.58
    remote_confirmed = remote_source_to_remote >= 0.46 or (remote_source_tokens >= 2 and remote_text)
    remote_duplicate = best_remote_in_mic >= 0.70 and best_me_any < 0.42 and remote_similarity >= 35
    very_short_me = len(me_tokens) <= 3 and len(normalize_text(me_text).split()) <= 4
    noise_fragment_me = looks_like_noise_fragment(me_text)
    no_mic_me = best_me_any < 0.24 and mic_content_tokens <= 2
    mic_rejects_noise_fragment = (
        noise_fragment_me
        and best_me_any < 0.18
        and audit_verdict == "probable_transcript_error"
        and audit_label in {"asr_noise", "remote_leak", "uncertain"}
        and local_support <= 20
    )
    short_remote_leak_rejected = (
        very_short_me
        and len(me_tokens) >= 2
        and best_me_any < 0.32
        and audit_verdict == "probable_transcript_error"
        and audit_label in {"remote_leak", "asr_noise", "uncertain"}
        and remote_similarity >= 60
        and mic_content_tokens >= 4
        and remote_source_tokens >= 4
    )

    if me_confirmed and remote_confirmed and best_remote_in_mic < 0.68:
        label = "confirm_timing_or_doubletalk" if remote_text else "confirm_me"
        suggested = "keep_me"
        confidence = min(0.92, max(0.78, best_me_any + 0.25))
        reasons.append(f"mic confirms Me via {best_me_any_source or best_me_source}")
        if remote_text:
            reasons.append("remote track confirms Colleagues; overlap is likely timing/double-talk")
    elif me_confirmed:
        label = "confirm_me"
        suggested = "keep_me"
        confidence = min(0.90, max(0.75, best_me_any + 0.20))
        reasons.append(f"mic confirms Me via {best_me_any_source or best_me_source}")
    elif remote_duplicate:
        label = "confirm_remote_duplicate"
        suggested = "drop_me"
        confidence = min(0.95, max(0.82, best_remote_in_mic + 0.12))
        reasons.append(f"mic resembles remote via {remote_in_mic_source}")
    elif mic_rejects_noise_fragment:
        label = "confirm_asr_noise"
        suggested = "drop_me"
        confidence = 0.88
        reasons.append("non-word short Me fragment is rejected by mic decodes")
    elif short_remote_leak_rejected:
        label = "confirm_asr_noise"
        suggested = "drop_me"
        confidence = 0.90
        reasons.append("short Me text is rejected by mic decodes while audio review points to remote leak")
    elif very_short_me and no_mic_me and local_support < 45 and audit_label in {"uncertain", "asr_noise", "remote_leak"}:
        label = "confirm_asr_noise"
        suggested = "drop_me"
        confidence = 0.82 if audit_verdict != "probable_transcript_error" else 0.88
        reasons.append("short Me text is not confirmed by mic decodes")
    else:
        label = "uncertain"
        suggested = "needs_review"
        confidence = max(0.0, min(0.69, max(best_me_any, best_remote_in_mic, remote_source_to_remote)))
        reasons.append("faster-whisper evidence is weak or conflicting")

    if label in {"confirm_remote_duplicate", "confirm_asr_noise"} and any(
        marker in normalize_text(me_text)
        for marker in ("надо", "нужно", "решили", "договорились", "сделаю", "давай", "проверь")
    ):
        label = "uncertain"
        suggested = "needs_review"
        confidence = min(confidence, 0.69)
        reasons.append("protected work marker prevents automatic drop suggestion")

    return {
        "label": label,
        "suggested_decision": suggested,
        "confidence": round(confidence, 3),
        "reason": "; ".join(reasons),
        "scores": {
            "best_me_similarity": round(best_me, 6),
            "best_me_any_similarity": round(best_me_any, 6),
            "best_remote_in_mic_similarity": round(best_remote_in_mic, 6),
            "remote_source_to_remote_similarity": round(remote_source_to_remote, 6),
            "mic_content_tokens": mic_content_tokens,
            "me_content_tokens": len(me_tokens),
            "remote_content_tokens": len(remote_tokens),
            "audio_review_local_support": local_support,
            "audio_review_remote_similarity": remote_similarity,
        },
        "best_sources": {
            "me": best_me_source,
            "me_any": best_me_any_source,
            "remote_in_mic": remote_in_mic_source,
        },
    }


def audit_item(model: Any, item: dict[str, Any], audit_row: dict[str, Any] | None, args: argparse.Namespace) -> dict[str, Any]:
    sources = tuple(args.source or DEFAULT_SOURCES)
    clips = item.get("clips") if isinstance(item.get("clips"), dict) else {}
    transcripts: dict[str, dict[str, Any]] = {}
    for source in sources:
        path_value = clips.get(source)
        if not path_value:
            continue
        transcripts[source] = transcribe_clip(model, Path(path_value), args)
    me_text, remote_text = utterance_texts(item)
    metrics = source_metrics(transcripts, me_text, remote_text)
    classification = classify_item(item, audit_row, transcripts, metrics)
    return {
        "schema": SCHEMA_ROW,
        "id": f"fwj_{str(item.get('id') or '').replace('arp_', '')}",
        "source_pack_item_id": item.get("id"),
        "source_pack_item_fingerprint": item_fingerprint(item),
        "session_id": item.get("session_id"),
        "profile": item.get("profile") or args.profile,
        "interval": item.get("interval"),
        "source_reasons": item.get("source_reasons") or [],
        "utterance_ids": utterance_ids(item),
        "utterances": item.get("utterances") or [],
        "audio_review_classification": (audit_row or {}).get("classification"),
        "audio_review_scores": (audit_row or {}).get("scores"),
        "clips": clips,
        "transcripts": transcripts,
        "text_metrics": metrics,
        "classification": classification,
    }


def summarize(rows: list[dict[str, Any]], *, model_path: Path, pack_summary: dict[str, Any] | None, skipped_reason: str | None = None) -> dict[str, Any]:
    by_label: dict[str, dict[str, Any]] = {}
    by_suggested: dict[str, dict[str, Any]] = {}
    for row in rows:
        duration = safe_float((row.get("interval") or {}).get("duration_sec"), 0.0)
        classification = row.get("classification") if isinstance(row.get("classification"), dict) else {}
        label = str(classification.get("label") or "unknown")
        suggested = str(classification.get("suggested_decision") or "unknown")
        for bucket, key in ((by_label, label), (by_suggested, suggested)):
            value = bucket.setdefault(key, {"count": 0, "seconds": 0.0})
            value["count"] += 1
            value["seconds"] += duration
    for bucket in list(by_label.values()) + list(by_suggested.values()):
        bucket["seconds"] = round(float(bucket["seconds"]), 3)
    closed = sum(
        safe_float((row.get("interval") or {}).get("duration_sec"), 0.0)
        for row in rows
        if (row.get("classification") or {}).get("label") in {"confirm_me", "confirm_timing_or_doubletalk"}
    )
    drops = sum(
        safe_float((row.get("interval") or {}).get("duration_sec"), 0.0)
        for row in rows
        if (row.get("classification") or {}).get("label") in {"confirm_remote_duplicate", "confirm_asr_noise"}
    )
    return {
        "schema": SCHEMA_SUMMARY,
        "generator": {"name": "audit-stronger-audio-judge", "version": SCRIPT_VERSION},
        "model": str(model_path),
        "input_pack": pack_summary or {},
        "items": len(rows),
        "by_label": dict(sorted(by_label.items())),
        "by_suggested_decision": dict(sorted(by_suggested.items())),
        "suggested_keep_me_seconds": round(closed, 3),
        "suggested_drop_me_seconds": round(drops, 3),
        "skipped_reason": skipped_reason,
        "recommended_next_step": (
            "build_review_lane_pack_with_suggested_answers"
            if rows
            else "no_stronger_audio_judge_rows"
            if not skipped_reason
            else "install_or_download_faster_whisper_model"
        ),
    }


def cached_rows_for_items(
    out_dir: Path,
    items: list[dict[str, Any]],
    *,
    disabled: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    if disabled:
        return [], items, 0
    cached_by_pack_id = {
        str(row.get("source_pack_item_id") or ""): row
        for row in read_jsonl(out_dir / "faster_whisper_judge.jsonl")
        if row.get("source_pack_item_id")
    }
    cached: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for item in items:
        pack_id = str(item.get("id") or "")
        row = cached_by_pack_id.get(pack_id)
        if row and cached_row_matches_item(row, item):
            cached.append(row)
        else:
            missing.append(item)
    return cached, missing, len(cached)


def valid_existing_rows_by_pack_id(out_dir: Path, items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_item_id = {str(item.get("id") or ""): item for item in items if item.get("id")}
    rows: dict[str, dict[str, Any]] = {}
    for row in read_jsonl(out_dir / "faster_whisper_judge.jsonl"):
        pack_id = str(row.get("source_pack_item_id") or "")
        item = by_item_id.get(pack_id)
        if item and cached_row_matches_item(row, item):
            rows[pack_id] = row
    return rows


def write_report(path: Path, summary: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    lines = [
        "# Faster Whisper Judge",
        "",
        "This report uses a local faster-whisper model over existing audio-review clips. It does not edit transcripts.",
        "",
        "## Summary",
        "",
        f"- Items: `{summary['items']}`",
        f"- Suggested keep seconds: `{summary['suggested_keep_me_seconds']}`",
        f"- Suggested drop seconds: `{summary['suggested_drop_me_seconds']}`",
        f"- Recommended next step: `{summary['recommended_next_step']}`",
        "",
        "## By Label",
        "",
    ]
    if summary.get("skipped_reason"):
        lines.extend(["", f"Skipped: `{summary['skipped_reason']}`", ""])
    for label, bucket in summary.get("by_label", {}).items():
        lines.append(f"- `{label}`: `{bucket['count']}` items, `{bucket['seconds']}` sec")
    lines.extend(["", "## Examples", ""])
    ordered = sorted(rows, key=lambda row: -safe_float((row.get("classification") or {}).get("confidence"), 0.0))
    for row in ordered[:20]:
        classification = row.get("classification") or {}
        interval = row.get("interval") or {}
        lines.extend(
            [
                f"### {row.get('source_pack_item_id')} {interval.get('start_time') or format_time(safe_float(interval.get('start')))}-{interval.get('end_time') or format_time(safe_float(interval.get('end')))}",
                "",
                f"- Label: `{classification.get('label')}`",
                f"- Suggested: `{classification.get('suggested_decision')}`",
                f"- Confidence: `{classification.get('confidence')}`",
                f"- Reason: {classification.get('reason')}",
            ]
        )
        for utterance in row.get("utterances") or []:
            role = utterance.get("role") or utterance.get("source_track") or "?"
            lines.append(f"- {role} `{utterance.get('id')}`: {utterance.get('text')}")
        transcripts = row.get("transcripts") if isinstance(row.get("transcripts"), dict) else {}
        for source in DEFAULT_SOURCES:
            result = transcripts.get(source)
            if isinstance(result, dict):
                lines.append(f"- {source}: `{str(result.get('text') or '').strip()}`")
        lines.append("")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    session = args.session.expanduser()
    pack_dir = args.pack_dir or session / "derived/audit/audio-review-pack"
    out_dir = args.out_dir or pack_dir
    items = read_jsonl(pack_dir / "review_pack_items.jsonl")
    audio_review_rows = audit_by_id(read_jsonl(pack_dir / "audio_review_audit.jsonl"))
    pack_summary = read_json(pack_dir / "review_pack_summary.json")
    model_path = resolve_model(args)
    ready, ready_reason = model_ready(model_path)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not ready:
        summary = summarize([], model_path=model_path, pack_summary=pack_summary, skipped_reason=ready_reason)
        write_jsonl(out_dir / "faster_whisper_judge.jsonl", [])
        write_json(out_dir / "faster_whisper_judge_summary.json", summary)
        write_report(out_dir / "faster_whisper_judge_report.md", summary, [])
        print(f"stronger_audio_judge: skipped ({ready_reason})")
        print(f"summary: {out_dir / 'faster_whisper_judge_summary.json'}")
        return 0

    if not items:
        summary = summarize([], model_path=model_path, pack_summary=pack_summary)
        write_jsonl(out_dir / "faster_whisper_judge.jsonl", [])
        write_json(out_dir / "faster_whisper_judge_summary.json", summary)
        write_report(out_dir / "faster_whisper_judge_report.md", summary, [])
        print("stronger_audio_judge: no review pack items")
        print(f"summary: {out_dir / 'faster_whisper_judge_summary.json'}")
        return 0

    matched_audio_review_rows = {
        str(item.get("id") or ""): row
        for item in items
        if (row := audio_review_rows.get(str(item.get("id") or ""))) and audit_row_matches_item(row, item)
    }
    requested_target_ids, missing_lane_pack_files, lane_pack_selector_keys = target_item_ids(args, items)
    selected = selected_items(items, matched_audio_review_rows, args.max_items, target_ids=requested_target_ids)
    missing_target_ids = [item_id for item_id in requested_target_ids if item_id not in {str(item.get("id") or "") for item in items}]
    cached_rows, missing_items, cached_count = cached_rows_for_items(out_dir, selected, disabled=args.no_cache)
    cached_by_pack_id = {str(row.get("source_pack_item_id") or ""): row for row in cached_rows}
    if missing_items:
        model = load_model(model_path, args)
        new_by_pack_id = {
            str(item.get("id") or ""): audit_item(model, item, matched_audio_review_rows.get(str(item.get("id") or "")), args)
            for item in missing_items
        }
    else:
        new_by_pack_id = {}
    selected_rows = [
        new_by_pack_id.get(str(item.get("id") or "")) or cached_by_pack_id[str(item.get("id") or "")]
        for item in selected
    ]
    merged_by_pack_id = valid_existing_rows_by_pack_id(out_dir, items)
    for row in selected_rows:
        if row.get("source_pack_item_id"):
            merged_by_pack_id[str(row["source_pack_item_id"])] = row
    rows = [merged_by_pack_id[str(item.get("id") or "")] for item in items if str(item.get("id") or "") in merged_by_pack_id]
    summary = summarize(rows, model_path=model_path, pack_summary=pack_summary)
    summary["cached_items"] = cached_count
    summary["computed_items"] = len(missing_items)
    summary["selected_items"] = len(selected)
    summary["target_item_ids"] = requested_target_ids
    summary["missing_target_item_ids"] = missing_target_ids
    summary["missing_review_lane_pack_files"] = missing_lane_pack_files
    summary["review_lane_pack_selector_keys"] = lane_pack_selector_keys
    write_jsonl(out_dir / "faster_whisper_judge.jsonl", rows)
    write_json(out_dir / "faster_whisper_judge_summary.json", summary)
    write_report(out_dir / "faster_whisper_judge_report.md", summary, rows)
    print(f"items: {len(rows)}")
    print(f"cached_items: {cached_count}")
    print(f"computed_items: {len(missing_items)}")
    if requested_target_ids:
        print(f"target_items: {len(selected)}/{len(requested_target_ids)}")
    if missing_target_ids:
        print(f"missing_target_items: {', '.join(missing_target_ids)}")
    print(f"summary: {out_dir / 'faster_whisper_judge_summary.json'}")
    print(f"report: {out_dir / 'faster_whisper_judge_report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
