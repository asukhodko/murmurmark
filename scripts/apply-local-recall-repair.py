#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import re
import shlex
import shutil
import sys
from pathlib import Path
from typing import Any


SCRIPT_VERSION = "0.2.1"
DEFAULT_MODEL = "~/.local/share/murmurmark/models/whisper.cpp/ggml-large-v3-q5_0.bin"
DEFAULT_OUTPUT_PROFILE = "local_recall_repair_v1"
SCHEMA_REPORT = "murmurmark.local_recall_repair_report/v1"
SCHEMA_PATCH = "murmurmark.local_recall_repair_patch/v1"
SCHEMA_REJECTION = "murmurmark.local_recall_repair_rejection/v1"
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
ACK_WORDS = {"да", "ага", "угу", "ок", "окей", "ладно", "понял", "понятно", "хорошо"}
BOUNDARY_FALLBACK_BEFORE_MS = 700
BOUNDARY_FALLBACK_AFTER_MS = 250


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply conservative local-recall repairs from audit items.")
    parser.add_argument("session", type=Path)
    parser.add_argument("--input-profile", default="auto")
    parser.add_argument("--output-profile", default=DEFAULT_OUTPUT_PROFILE)
    parser.add_argument("--mode", choices=("dry-run", "conservative"), default="conservative")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--language", default="ru")
    parser.add_argument("--whisper-cli", default="whisper-cli")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--min-confidence", type=float, default=0.78)
    parser.add_argument("--min-local-ratio", type=float, default=0.55)
    parser.add_argument("--max-remote-ratio", type=float, default=0.35)
    parser.add_argument("--min-duration-sec", type=float, default=0.55)
    parser.add_argument("--max-duration-sec", type=float, default=8.0)
    parser.add_argument("--min-micro-score", type=float, default=0.68)
    parser.add_argument("--force-micro-asr", action="store_true")
    parser.add_argument(
        "--skip-micro-asr",
        action="store_true",
        help="Use item.repair_candidate.text only. Intended for fixtures and debugging.",
    )
    return parser.parse_args()


def suffix(profile: str) -> str:
    return "" if profile == "current" else f".{profile}"


def display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(resolved)


def shell_path(path: Path) -> str:
    return shlex.quote(display_path(path))


def repair_handoff(session: Path, output_profile: str, report_path: Path, transcript_path: Path) -> dict[str, Any]:
    session_arg = shell_path(session)
    profile_arg = shlex.quote(output_profile)
    synthesize_command = f"murmurmark synthesize {session_arg} --transcript-profile {profile_arg}"
    transcript_command = f"murmurmark transcript {session_arg} --profile {profile_arg}"
    report_command = f"murmurmark report {session_arg}"
    return {
        "recommended_next": synthesize_command,
        "next_commands": [
            {
                "id": "synthesize_repair_profile",
                "command": synthesize_command,
                "reason": "build quality verdict and notes from the repair profile",
            },
            {
                "id": "open_repair_transcript",
                "command": transcript_command,
                "reason": "inspect the repair transcript through the CLI",
            },
            {
                "id": "refresh_session_report",
                "command": report_command,
                "reason": "refresh readiness after repair-derived synthesis",
            },
        ],
        "open_commands": [
            {
                "id": "open_repair_report",
                "command": f"less {shell_path(report_path)}",
                "path": display_path(report_path),
            },
            {
                "id": "open_repair_transcript",
                "command": f"less {shell_path(transcript_path)}",
                "path": display_path(transcript_path),
            },
        ],
    }


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        value = json.load(file)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def read_json_if_exists(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return read_json(path)
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            value = json.loads(line)
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


def normalize_text(value: Any) -> str:
    text = str(value or "").lower().replace("ё", "е")
    text = re.sub(r"[^0-9a-zа-я_./+-]+", " ", text)
    return " ".join(text.split())


def tokens(text: Any) -> list[str]:
    values = [
        token.lower().replace("ё", "е").strip("./+-")
        for token in TOKEN_RE.findall(str(text or ""))
    ]
    return [token for token in values if token]


def content_tokens(text: Any) -> list[str]:
    return [token for token in tokens(text) if token not in STOP_WORDS and len(token) > 2]


def is_ack(text: Any) -> bool:
    normalized = normalize_text(text)
    if not normalized:
        return False
    value_tokens = normalized.split()
    return len(value_tokens) <= 3 and all(token in ACK_WORDS for token in value_tokens)


def clean_repair_text(text: Any) -> str:
    value = " ".join(str(text or "").split())
    value = re.sub(r"^[\\s.。…,:;!?-]+", "", value)
    value = re.sub(r"[\\s.。…,:;!?-]+$", "", value)
    return " ".join(value.split())


def role_name(row: dict[str, Any]) -> str:
    source = str(row.get("source_track") or "").lower()
    role = str(row.get("role") or row.get("speaker_label") or "").lower()
    if source == "mic" or role == "me":
        return "Me"
    if source == "remote" or role in {"remote", "colleagues"}:
        return "Colleagues"
    return str(row.get("role") or row.get("speaker_label") or "Unknown")


def is_me(row: dict[str, Any]) -> bool:
    return role_name(row) == "Me"


def interval_overlap(left: dict[str, Any], right: dict[str, Any]) -> float:
    start = max(safe_float(left.get("start")), safe_float(right.get("start")))
    end = min(safe_float(left.get("end")), safe_float(right.get("end")))
    return max(0.0, end - start)


def text_similarity(left: Any, right: Any) -> float:
    left_norm = normalize_text(left)
    right_norm = normalize_text(right)
    if not left_norm or not right_norm:
        return 0.0
    left_set = set(content_tokens(left_norm))
    right_set = set(content_tokens(right_norm))
    if not left_set or not right_set:
        return 1.0 if left_norm == right_norm else 0.0
    return len(left_set & right_set) / max(1, len(left_set))


def format_time(seconds: Any) -> str:
    total = max(0, int(safe_float(seconds)))
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
    order_repair_report = read_json_if_exists(
        session
        / "derived/transcript-simple/whisper-cpp/order-repair/transcript_order_repair_report.order_repair_v1.json"
    )
    order_summary = order_repair_report.get("summary") if isinstance(order_repair_report, dict) else {}
    order_gates = order_repair_report.get("gates") if isinstance(order_repair_report, dict) else {}
    order_applied = safe_int(order_summary.get("applied_repairs")) if isinstance(order_summary, dict) else 0

    def profile_exists(profile: str) -> bool:
        return (resolved / f"clean_dialogue{suffix(profile)}.json").exists()

    def order_repair_usable_for(profile: str) -> bool:
        return (
            profile_exists("order_repair_v1")
            and isinstance(order_repair_report, dict)
            and isinstance(order_gates, dict)
            and order_gates.get("passed") is True
            and order_applied > 0
            and order_repair_report.get("input_profile") == profile
        )

    for profile in (
        "reviewed_v1",
        "agent_reviewed_v1",
        "audit_cleanup_v6",
        "audit_cleanup_v5",
        "audit_cleanup_v4",
        "audit_cleanup_v3",
        "audit_cleanup_v2",
        "audit_cleanup_v1",
        "shadow_v2",
        "current",
    ):
        if profile_exists(profile):
            if order_repair_usable_for(profile):
                return "order_repair_v1"
            return profile
    if profile_exists("order_repair_v1"):
        return "order_repair_v1"
    return "current"


def load_transcribe_bridge() -> Any:
    path = Path(__file__).resolve().parent / "transcribe-simple-whispercpp.py"
    spec = importlib.util.spec_from_file_location("murmurmark_transcribe_bridge", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import transcribe bridge: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def existing_overlap(utterances: list[dict[str, Any]], item: dict[str, Any], text: str) -> dict[str, Any] | None:
    item_row = {"start": item.get("start_sec"), "end": item.get("end_sec")}
    duration = max(0.001, safe_float(item.get("duration_sec")))
    candidate_tokens = set(content_tokens(text))
    for row in utterances:
        if not is_me(row):
            continue
        overlap = interval_overlap(item_row, row)
        row_duration = max(0.001, safe_float(row.get("end")) - safe_float(row.get("start")))
        candidate_coverage = overlap / duration
        existing_coverage = overlap / row_duration
        if candidate_coverage < 0.5 and existing_coverage < 0.8:
            continue
        existing_tokens = set(content_tokens(row.get("text")))
        existing_text_containment = (
            len(candidate_tokens & existing_tokens) / len(existing_tokens)
            if len(existing_tokens) >= 3
            else 0.0
        )
        if (
            text_similarity(text, row.get("text")) >= 0.55
            or candidate_coverage >= 0.85
            or existing_text_containment >= 0.8
        ):
            return row
    return None


def candidate_allowed(item: dict[str, Any], args: argparse.Namespace) -> tuple[bool, str]:
    label = str(item.get("label") or "")
    state = item.get("state") if isinstance(item.get("state"), dict) else {}
    confidence = safe_float(item.get("confidence"))
    duration = safe_float(item.get("duration_sec"))
    local_only_ratio = safe_float(state.get("local_only_ratio"))
    remote_active_ratio = safe_float(state.get("remote_active_ratio"))
    strong_review_local_only = (
        label == "needs_review"
        and confidence >= 0.65
        and local_only_ratio >= 0.90
        and remote_active_ratio <= 0.05
    )
    if label != "possible_lost_me" and not strong_review_local_only:
        return False, "label_not_possible_lost_me"
    if label == "possible_lost_me" and confidence < args.min_confidence:
        return False, "confidence_below_threshold"
    if duration < args.min_duration_sec:
        return False, "duration_below_threshold"
    if duration > args.max_duration_sec:
        return False, "duration_above_threshold"
    min_local_ratio = 0.90 if strong_review_local_only else args.min_local_ratio
    max_remote_ratio = 0.05 if strong_review_local_only else args.max_remote_ratio
    if local_only_ratio < min_local_ratio:
        return False, "local_ratio_below_threshold"
    if remote_active_ratio > max_remote_ratio:
        return False, "remote_ratio_above_threshold"
    if safe_float(item.get("remote_overlap_text_containment")) >= 0.55:
        return False, "parent_text_mostly_covered_by_remote"
    return True, "strong_needs_review_local_only_allowed" if strong_review_local_only else "candidate_allowed"


def read_stub_repair_text(item: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    candidate = item.get("repair_candidate") if isinstance(item.get("repair_candidate"), dict) else {}
    text = str(candidate.get("text") or "").strip()
    if not text:
        return "", {"status": "failed", "reason": "missing_repair_candidate_text"}
    return text, {
        "status": "ok",
        "source_label": "repair_candidate",
        "window_label": "fixture",
        "score": safe_float(candidate.get("score")) or 0.75,
        "rows": [],
    }


def compact_micro_rows(meta: dict[str, Any], bridge: Any) -> list[dict[str, Any]]:
    json_value = meta.get("json")
    if not json_value:
        return []
    json_path = Path(str(json_value))
    if not json_path.exists():
        return []
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    global_offset_ms = safe_int(meta.get("slice_start_ms")) - safe_int(meta.get("leading_silence_ms"))
    rows: list[dict[str, Any]] = []
    for row in data.get("transcription", []):
        if not isinstance(row, dict):
            continue
        shifted = bridge.shift_transcription_row(row, global_offset_ms)
        offsets = shifted.get("offsets") if isinstance(shifted.get("offsets"), dict) else {}
        start_ms = safe_int(offsets.get("from"))
        end_ms = safe_int(offsets.get("to")) or start_ms
        text = bridge.clean_text(str(shifted.get("text") or ""))
        if not text or bridge.KNOWN_HALLUCINATION_RE.search(text):
            continue
        stats = bridge.token_confidence_stats(shifted)
        rows.append(
            {
                "start_ms": start_ms,
                "end_ms": end_ms,
                "midpoint_ms": (start_ms + end_ms) // 2,
                "text": text,
                "avg_logprob": bridge.first_optional_float(shifted, ("avg_logprob", "average_logprob", "logprob")),
                "no_speech_prob": bridge.first_optional_float(shifted, ("no_speech_prob", "no_speech_probability")),
                "token_avg_prob": stats.get("token_avg_prob"),
            }
        )
    return rows


def enrich_micro_meta(meta: dict[str, Any], bridge: Any) -> dict[str, Any]:
    rows = compact_micro_rows(meta, bridge)
    if not rows:
        meta["raw_transcription_count"] = 0
        meta["raw_transcription_text"] = ""
        return meta
    meta["raw_transcription_count"] = len(rows)
    meta["raw_transcription_text"] = bridge.clean_text(" ".join(str(row.get("text") or "") for row in rows))
    meta["raw_transcription_rows"] = rows
    return meta


def row_distance_to_interval_ms(row: dict[str, Any], start_ms: int, end_ms: int) -> int:
    row_start = safe_int(row.get("start_ms"))
    row_end = safe_int(row.get("end_ms")) or row_start
    if row_end < start_ms:
        return start_ms - row_end
    if row_start > end_ms:
        return row_start - end_ms
    return 0


def recover_boundary_micro_text(
    *,
    text: str,
    meta: dict[str, Any],
    item: dict[str, Any],
    remote_text: str,
    bridge: Any,
) -> tuple[str, dict[str, Any]]:
    if text or meta.get("reason") != "empty_micro_text":
        return text, meta
    boundary = item.get("boundary") if isinstance(item.get("boundary"), dict) else {}
    if boundary.get("near_parent_boundary") is not True:
        return text, meta

    start_ms = int(round(safe_float(item.get("start_sec")) * 1000))
    end_ms = int(round(safe_float(item.get("end_sec")) * 1000))
    rows = meta.get("raw_transcription_rows") if isinstance(meta.get("raw_transcription_rows"), list) else []
    selected = [
        row
        for row in rows
        if row_distance_to_interval_ms(row, start_ms, end_ms) <= BOUNDARY_FALLBACK_BEFORE_MS
        and safe_int(row.get("start_ms")) <= end_ms + BOUNDARY_FALLBACK_AFTER_MS
    ]
    if not selected:
        meta["boundary_overlap_fallback"] = {
            "attempted": True,
            "recovered": False,
            "reason": "no_raw_row_near_selection",
            "before_ms": BOUNDARY_FALLBACK_BEFORE_MS,
            "after_ms": BOUNDARY_FALLBACK_AFTER_MS,
        }
        return text, meta

    recovered_text = bridge.clean_text(" ".join(str(row.get("text") or "") for row in selected))
    if not recovered_text:
        return text, meta
    score = bridge.score_micro_reasr_text(
        recovered_text,
        selected,
        start_ms,
        end_ms,
        remote_context_text=remote_text,
    )
    recovered_meta = dict(meta)
    recovered_meta.update(
        {
            "status": "ok",
            "reason": "boundary_overlap_recovered",
            "selection_policy": "boundary_overlap_fallback",
            "rows": selected,
            "raw_text": recovered_text,
            "score": round(score, 6),
            "boundary_overlap_fallback": {
                "attempted": True,
                "recovered": True,
                "before_ms": BOUNDARY_FALLBACK_BEFORE_MS,
                "after_ms": BOUNDARY_FALLBACK_AFTER_MS,
                "selected_row_count": len(selected),
            },
        }
    )
    return recovered_text, recovered_meta


def run_micro_asr(
    session: Path,
    item: dict[str, Any],
    args: argparse.Namespace,
    bridge: Any,
    repair_dir: Path,
) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    if args.skip_micro_asr:
        text, meta = read_stub_repair_text(item)
        return text, meta, [meta]

    whisper_cli = shutil.which(args.whisper_cli) or args.whisper_cli
    model = Path(args.model).expanduser()
    if not model.exists():
        return "", {"status": "failed", "reason": "model_not_found", "model": str(model)}, []

    start_ms = int(round(safe_float(item.get("start_sec")) * 1000))
    end_ms = int(round(safe_float(item.get("end_sec")) * 1000))
    state = item.get("state") if isinstance(item.get("state"), dict) else {}
    remote_text = str(item.get("remote_overlap_text_sample") or "")
    sources = bridge.micro_reasr_sources(session, "shadow_v2")
    micro_dir = repair_dir / "micro_reasr"
    micro_dir.mkdir(parents=True, exist_ok=True)
    windows = [
        ("normal", max(0, start_ms - 900), end_ms + 700),
        ("wide", max(0, start_ms - 1600), end_ms + 1000),
    ]
    attempts: list[dict[str, Any]] = []
    best_text = ""
    best_meta: dict[str, Any] = {"status": "failed", "reason": "no_micro_candidate"}
    best_score = -999.0
    for source_label, source_path in sources:
        for window_label, rec_start, rec_end in windows:
            text, meta = bridge.materialize_micro_reasr(
                source=source_path,
                source_label=source_label,
                window_label=window_label,
                micro_dir=micro_dir,
                whisper_cli=whisper_cli,
                model=model,
                language=args.language,
                threads=args.threads,
                recognition_start_ms=rec_start,
                recognition_end_ms=rec_end,
                selection_start_ms=start_ms,
                selection_end_ms=end_ms,
                target_start_ms=start_ms,
                target_end_ms=end_ms,
                force=args.force_micro_asr,
                repair_profile=args.output_profile,
                leading_silence_ms=400,
                remote_context_text=remote_text,
                local_score=safe_float(state.get("local_only_ratio")),
            )
            meta = dict(meta)
            meta = enrich_micro_meta(meta, bridge)
            text, meta = recover_boundary_micro_text(
                text=text,
                meta=meta,
                item=item,
                remote_text=remote_text,
                bridge=bridge,
            )
            meta["text"] = text
            attempts.append(meta)
            score = safe_float(meta.get("score"))
            if text and score > best_score:
                best_text = text
                best_meta = meta
                best_score = score
    return best_text, best_meta, attempts


def micro_text_allowed(text: str, meta: dict[str, Any], item: dict[str, Any], args: argparse.Namespace, bridge: Any) -> tuple[bool, str]:
    clean = clean_repair_text(text)
    if clean != text:
        meta["normalized_text"] = clean
    if not clean:
        return False, "empty_micro_text"
    if bridge.KNOWN_HALLUCINATION_RE.search(clean):
        return False, "known_hallucination"
    if safe_float(meta.get("score")) < args.min_micro_score:
        return False, "micro_score_below_threshold"
    duration = max(0.25, safe_float(item.get("duration_sec")))
    chars_per_sec = len(clean) / duration
    if chars_per_sec > 24.0 and not is_ack(clean):
        return False, "text_too_long_for_interval"
    if not content_tokens(clean) and not is_ack(clean):
        return False, "no_content_tokens"
    if text_similarity(clean, item.get("remote_overlap_text_sample")) >= 0.55:
        return False, "micro_text_too_similar_to_remote"
    return True, "micro_text_allowed"


def make_repair_row(item: dict[str, Any], text: str, args: argparse.Namespace) -> dict[str, Any]:
    item_id = str(item.get("item_id") or "local_recall")
    clean_text = clean_repair_text(text)
    row = {
        "id": f"{args.output_profile}_{item_id}",
        "source_track": "mic",
        "role": "Me",
        "speaker_label": "Me",
        "start": round(safe_float(item.get("start_sec")), 3),
        "end": round(safe_float(item.get("end_sec")), 3),
        "text": clean_text,
        "quality": {
            "needs_review": True,
            "local_recall_repair": {
                "status": "inserted_needs_review",
                "source_item_id": item_id,
                "output_profile": args.output_profile,
            },
        },
        "source": {
            "kind": "local_recall_repair",
            "item_id": item_id,
            "parent_candidate_id": item.get("parent_candidate_id"),
        },
    }
    return row


def build_overlaps(utterances: list[dict[str, Any]]) -> list[dict[str, Any]]:
    me_rows = [row for row in utterances if is_me(row)]
    remote_rows = [row for row in utterances if role_name(row) == "Colleagues"]
    overlaps: list[dict[str, Any]] = []
    index = 1
    for me in me_rows:
        for remote in remote_rows:
            duration = interval_overlap(me, remote)
            if duration <= 0:
                continue
            start = max(safe_float(me.get("start")), safe_float(remote.get("start")))
            end = min(safe_float(me.get("end")), safe_float(remote.get("end")))
            overlaps.append(
                {
                    "id": f"ov_{index:06d}",
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "duration_sec": round(duration, 3),
                    "me_utterance_id": me.get("id"),
                    "remote_utterance_id": remote.get("id"),
                    "text_similarity": round(text_similarity(me.get("text"), remote.get("text")), 6),
                }
            )
            index += 1
    return overlaps


def quality_report(input_quality: dict[str, Any], utterances: list[dict[str, Any]], overlaps: list[dict[str, Any]], summary: dict[str, Any]) -> dict[str, Any]:
    report = copy.deepcopy(input_quality)
    report["schema"] = "murmurmark.simple_transcript_quality/v1"
    report["utterances"] = len(utterances)
    report["needs_review_count"] = sum(1 for row in utterances if isinstance(row.get("quality"), dict) and row["quality"].get("needs_review"))
    report["needs_review_ratio"] = round(report["needs_review_count"] / max(1, len(utterances)), 6)
    report["cross_role_overlap_count"] = len(overlaps)
    report["cross_role_overlap_seconds"] = round(sum(safe_float(row.get("duration_sec")) for row in overlaps), 3)
    report["cross_role_overlap_gt2_count"] = sum(1 for row in overlaps if safe_float(row.get("duration_sec")) > 2.0)
    report["cross_role_overlap_gt2_seconds"] = round(
        sum(safe_float(row.get("duration_sec")) for row in overlaps if safe_float(row.get("duration_sec")) > 2.0),
        3,
    )
    duplicate_overlaps = [row for row in overlaps if safe_float(row.get("text_similarity")) >= 0.65]
    report["remote_duplicate_in_me_count"] = len(duplicate_overlaps)
    report["remote_duplicate_in_me_seconds"] = round(sum(safe_float(row.get("duration_sec")) for row in duplicate_overlaps), 3)
    report["meeting_duration_sec"] = round(max((safe_float(row.get("end")) for row in utterances), default=0.0), 3)
    report["local_recall_repair"] = summary
    return report


def write_markdown(path: Path, utterances: list[dict[str, Any]], model: str | None, language: str | None) -> None:
    lines = [
        "# Simple Transcript",
        "",
        "Backend: whisper.cpp  ",
        f"Model: `{Path(model).name if model else 'unknown'}`  ",
        f"Language: `{language or 'unknown'}`",
        "",
    ]
    for row in utterances:
        lines.extend([f"## {format_time(row.get('start'))} {role_name(row)}", "", str(row.get("text") or "").strip(), ""])
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def write_review(path: Path, session: Path, report: dict[str, Any], patches: list[dict[str, Any]], rejected: list[dict[str, Any]]) -> None:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    lines = [
        "# Local Recall Repair",
        "",
        f"Session: `{session}`",
        f"Input profile: `{report.get('input_profile')}`",
        f"Output profile: `{report.get('output_profile')}`",
        f"Gates passed: `{str((report.get('gates') or {}).get('passed')).lower()}`",
        "",
        "## Summary",
        "",
        f"- Source items: `{summary.get('source_items')}`",
        f"- Eligible items: `{summary.get('eligible_items')}`",
        f"- Applied repairs: `{summary.get('applied_repairs')}` / `{summary.get('inserted_me_seconds')}` sec",
        f"- Rejected items: `{summary.get('rejected_items')}`",
        "",
    ]
    if patches:
        lines += ["## Applied", ""]
        for patch in patches[:20]:
            row = patch.get("utterance") if isinstance(patch.get("utterance"), dict) else {}
            lines += [
                f"- `{patch.get('source_item_id')}` -> `{row.get('id')}` {format_time(row.get('start'))}-{format_time(row.get('end'))}: {row.get('text')}",
            ]
        lines.append("")
    if rejected:
        lines += ["## Rejected", ""]
        for row in rejected[:30]:
            lines.append(f"- `{row.get('source_item_id')}`: `{row.get('reason')}`")
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    session = args.session.expanduser().resolve()
    resolved = session / "derived/transcript-simple/whisper-cpp/resolved"
    repair_dir = session / "derived/transcript-simple/whisper-cpp/local-recall-repair"
    input_profile = resolve_profile(session, args.input_profile)
    input_suffix = suffix(input_profile)
    output_suffix = suffix(args.output_profile)
    report_path = repair_dir / f"local_recall_repair_report{output_suffix}.json"
    transcript_path = resolved / f"transcript{output_suffix}.md"
    dialogue_path = resolved / f"clean_dialogue{input_suffix}.json"
    quality_path = resolved / f"quality_report{input_suffix}.json"
    local_recall_audit_path = session / "derived/audit/local-recall/local_recall_audit.json"
    local_recall_items_path = session / "derived/audit/local-recall/local_recall_items.jsonl"
    bridge = load_transcribe_bridge()

    if not dialogue_path.exists() or not quality_path.exists() or not local_recall_items_path.exists():
        report = {
            "schema": SCHEMA_REPORT,
            "generator": {"name": "apply-local-recall-repair", "version": SCRIPT_VERSION},
            "input_profile": input_profile,
            "output_profile": args.output_profile,
            "status": "missing_inputs",
            "inputs": {
                "clean_dialogue": str(dialogue_path),
                "quality_report": str(quality_path),
                "local_recall_items": str(local_recall_items_path),
            },
            "summary": {},
            "gates": {"passed": False, "hard_failures": ["missing_inputs"], "warnings": []},
            **repair_handoff(session, args.output_profile, report_path, transcript_path),
        }
        write_json(report_path, report)
        print(f"local_recall_repair_report: {report_path}")
        print("status: missing_inputs")
        return 2

    dialogue = read_json(dialogue_path)
    input_quality = read_json(quality_path)
    transcript_report = read_json_if_exists(resolved / f"transcribe_simple_report{input_suffix}.json") or read_json_if_exists(resolved / "transcribe_simple_report.json") or {}
    local_recall_audit = read_json_if_exists(local_recall_audit_path) or {}
    utterances = [row for row in dialogue.get("utterances", []) if isinstance(row, dict)]
    items = [row for row in read_jsonl(local_recall_items_path) if isinstance(row, dict)]
    output_utterances = [copy.deepcopy(row) for row in utterances]
    patches: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    micro_runs: list[dict[str, Any]] = []
    eligible = 0

    for item in items:
        item_id = str(item.get("item_id") or "")
        allowed, reason = candidate_allowed(item, args)
        if not allowed:
            rejected.append({"schema": SCHEMA_REJECTION, "source_item_id": item_id, "reason": reason})
            continue
        eligible += 1
        text, meta, attempts = run_micro_asr(session, item, args, bridge, repair_dir)
        micro_runs.extend({"source_item_id": item_id, **attempt} for attempt in attempts)
        text_ok, text_reason = micro_text_allowed(text, meta, item, args, bridge)
        if not text_ok:
            rejected.append({"schema": SCHEMA_REJECTION, "source_item_id": item_id, "reason": text_reason, "micro_asr": meta})
            continue
        duplicate = existing_overlap(output_utterances, item, text)
        if duplicate:
            duplicate_source = duplicate.get("source") if isinstance(duplicate.get("source"), dict) else {}
            if duplicate.get("id") == f"{args.output_profile}_{item_id}" or duplicate_source.get("kind") == "local_recall_repair":
                patches.append(
                    {
                        "schema": SCHEMA_PATCH,
                        "source_item_id": item_id,
                        "action": "keep_existing_me_utterance",
                        "status": "already_present",
                        "utterance": copy.deepcopy(duplicate),
                        "micro_asr": meta,
                    }
                )
                continue
            rejected.append(
                {
                    "schema": SCHEMA_REJECTION,
                    "source_item_id": item_id,
                    "reason": "already_covered_by_existing_me",
                    "existing_utterance_id": duplicate.get("id"),
                    "micro_asr": meta,
                }
            )
            continue
        new_row = make_repair_row(item, text, args)
        if args.mode == "conservative":
            output_utterances.append(new_row)
        patches.append(
            {
                "schema": SCHEMA_PATCH,
                "source_item_id": item_id,
                "action": "insert_me_utterance",
                "status": "applied" if args.mode == "conservative" else "planned",
                "utterance": new_row,
                "micro_asr": meta,
            }
        )

    output_utterances.sort(key=lambda row: (safe_float(row.get("start")), safe_float(row.get("end")), str(row.get("id") or "")))
    overlaps = build_overlaps(output_utterances)
    inserted_seconds = round(sum(max(0.0, safe_float((patch.get("utterance") or {}).get("end")) - safe_float((patch.get("utterance") or {}).get("start"))) for patch in patches), 3)
    summary = {
        "schema": "murmurmark.local_recall_repair_summary/v1",
        "input_profile": input_profile,
        "output_profile": args.output_profile,
        "mode": args.mode,
        "source_items": len(items),
        "eligible_items": eligible,
        "applied_repairs": len(patches) if args.mode == "conservative" else 0,
        "planned_repairs": len(patches),
        "inserted_me_seconds": inserted_seconds if args.mode == "conservative" else 0.0,
        "planned_inserted_me_seconds": inserted_seconds,
        "rejected_items": len(rejected),
        "micro_boundary_overlap_recovered_attempts": sum(
            1
            for row in micro_runs
            if row.get("selection_policy") == "boundary_overlap_fallback" and row.get("status") == "ok"
        ),
        "micro_boundary_overlap_recovered_items": len(
            {
                str(row.get("source_item_id") or "")
                for row in micro_runs
                if row.get("selection_policy") == "boundary_overlap_fallback" and row.get("status") == "ok"
            }
            - {""}
        ),
        "micro_raw_transcription_rows": sum(safe_int(row.get("raw_transcription_count")) for row in micro_runs),
    }
    warnings = []
    if not patches:
        warnings.append("no_local_recall_repairs_applied")
    if args.skip_micro_asr:
        warnings.append("micro_asr_skipped")
    gates = {"passed": True, "hard_failures": [], "warnings": warnings}
    output_quality = quality_report(input_quality, output_utterances, overlaps, summary)
    output_dialogue = {
        "schema": "murmurmark.clean_dialogue/v1",
        "session": dialogue.get("session", session.name),
        "utterances": output_utterances,
    }
    simple_payload = {
        "schema": "murmurmark.transcript_simple/v1",
        "session": dialogue.get("session", session.name),
        "backend": "whisper.cpp",
        "utterances": [
            {
                **copy.deepcopy(row),
                "raw_text": row.get("text"),
                "corrected_text": row.get("text"),
                "corrections": [],
            }
            for row in output_utterances
        ],
    }
    report = {
        "schema": SCHEMA_REPORT,
        "generator": {"name": "apply-local-recall-repair", "version": SCRIPT_VERSION},
        "input_profile": input_profile,
        "output_profile": args.output_profile,
        "status": "ok",
        "inputs": {
            "clean_dialogue": str(dialogue_path),
            "quality_report": str(quality_path),
            "local_recall_audit": str(local_recall_audit_path),
            "local_recall_items": str(local_recall_items_path),
        },
        "parameters": {
            "min_confidence": args.min_confidence,
            "min_local_ratio": args.min_local_ratio,
            "max_remote_ratio": args.max_remote_ratio,
            "min_micro_score": args.min_micro_score,
        },
        "local_recall_audit_summary": local_recall_audit.get("summary") if isinstance(local_recall_audit.get("summary"), dict) else {},
        "summary": summary,
        "gates": gates,
        **repair_handoff(session, args.output_profile, report_path, transcript_path),
    }

    write_json(resolved / f"clean_dialogue{output_suffix}.json", output_dialogue)
    write_json(resolved / f"quality_report{output_suffix}.json", output_quality)
    write_json(resolved / f"overlaps{output_suffix}.json", {"schema": "murmurmark.transcript_overlaps/v1", "session": session.name, "overlaps": overlaps})
    write_json(resolved / f"transcript.simple{output_suffix}.json", simple_payload)
    write_markdown(transcript_path, output_utterances, transcript_report.get("model"), transcript_report.get("language"))
    write_json(report_path, report)
    write_jsonl(repair_dir / f"local_recall_repair_patches{output_suffix}.jsonl", patches)
    write_jsonl(repair_dir / f"local_recall_repair_rejected{output_suffix}.jsonl", rejected)
    write_jsonl(repair_dir / f"local_recall_repair_micro_runs{output_suffix}.jsonl", micro_runs)
    write_review(repair_dir / f"local_recall_repair{output_suffix}.md", session, report, patches, rejected)

    print(f"local_recall_repair_report: {report_path}")
    print(f"output_profile: {args.output_profile}")
    print(f"applied_repairs: {summary['applied_repairs']}")
    print(f"inserted_me_seconds: {summary['inserted_me_seconds']}")
    print(f"rejected_items: {summary['rejected_items']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
