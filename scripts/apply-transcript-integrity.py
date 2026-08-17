#!/usr/bin/env python3
"""Build an evidence-backed transcript profile with conservative repetition repairs."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import re
import sys
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
import review_profile_lineage as review_lineage


SCRIPT_VERSION = "0.1.1"
REPORT_SCHEMA = "murmurmark.transcript_integrity_report/v1"
CANDIDATE_SCHEMA = "murmurmark.transcript_integrity_candidate/v1"
PATCH_SCHEMA = "murmurmark.transcript_integrity_patch/v1"
REVIEW_SCHEMA = "murmurmark.transcript_integrity_review/v1"
OUTPUT_PROFILE = "transcript_integrity_v1"
DEFAULT_MODEL = Path.home() / ".local/share/murmurmark/models/faster-whisper/large-v3"
TOKEN_RE = re.compile(r"[0-9A-Za-z\u0410-\u044f\u0401\u0451]+(?:[-'][0-9A-Za-z\u0410-\u044f\u0401\u0451]+)*")
AUTO_INPUT_PROFILES = (
    "reviewed_v1",
    "agent_reviewed_v1",
    "mixed_utterance_separation_v1",
    "local_speech_completion_v2",
    "residual_local_recall_v1",
    "residual_audio_arbitration_v1",
    "residual_me_evidence_v1",
    "authoritative_boundary_v1",
    "order_repair_v1",
    "audit_cleanup_v7",
    "audit_cleanup_v6",
    "audit_cleanup_v5",
    "audit_cleanup_v4",
    "audit_cleanup_v3",
    "audit_cleanup_v2",
    "audit_cleanup_v1",
    "shadow_v2",
    "current",
)


class IntegrityError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a conservative transcript profile with evidence-backed duplicate repairs."
    )
    parser.add_argument("session", type=Path)
    parser.add_argument("--input-profile", default="auto")
    parser.add_argument("--output-profile", default=OUTPUT_PROFILE)
    parser.add_argument("--model", type=Path, default=None)
    parser.add_argument("--language", default="ru")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--compute-type", default="int8")
    parser.add_argument("--beam-size", type=int, default=1)
    parser.add_argument("--max-judge-items", type=int, default=24)
    parser.add_argument(
        "--judge-mode",
        choices=("auto", "cached", "off"),
        default="auto",
        help="auto runs the local judge, cached uses matching cache only, off leaves judged cases for review.",
    )
    parser.add_argument("--judge-fixture", type=Path, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--force-judge", action="store_true")
    return parser.parse_args()


def suffix(profile: str) -> str:
    return "" if profile == "current" else f".{profile}"


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    text = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def hash_payload(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def rel(path: Path, session: Path) -> str:
    try:
        return str(path.resolve().relative_to(session.resolve()))
    except ValueError:
        return str(path.resolve())


def normalized_tokens(value: Any) -> list[str]:
    return [match.group(0).lower().replace("ё", "е") for match in TOKEN_RE.finditer(str(value or ""))]


def token_spans(value: Any) -> list[tuple[str, int, int]]:
    text = str(value or "")
    return [
        (match.group(0).lower().replace("ё", "е"), match.start(), match.end())
        for match in TOKEN_RE.finditer(text)
    ]


def row_text(row: dict[str, Any]) -> str:
    return str(row.get("corrected_text") or row.get("text") or "").strip()


def role_name(row: dict[str, Any]) -> str:
    role = str(row.get("role") or row.get("speaker_label") or "").lower()
    if role in {"me", "mic"}:
        return "me"
    if role in {"remote", "colleagues"}:
        return "remote"
    return role


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def profile_paths(resolved: Path, profile: str) -> dict[str, Path]:
    profile_suffix = suffix(profile)
    return {
        "dialogue": resolved / f"clean_dialogue{profile_suffix}.json",
        "quality": resolved / f"quality_report{profile_suffix}.json",
        "overlaps": resolved / f"overlaps{profile_suffix}.json",
        "simple": resolved / f"transcript.simple{profile_suffix}.json",
        "markdown": resolved / f"transcript{profile_suffix}.md",
    }


def resolve_input_profile(session: Path, requested: str, output_profile: str) -> str:
    resolved = session / "derived/transcript-simple/whisper-cpp/resolved"

    def usable(profile: str) -> bool:
        paths = profile_paths(resolved, profile)
        if not paths["dialogue"].is_file() or not paths["quality"].is_file():
            return False
        if profile not in {"reviewed_v1", "agent_reviewed_v1"}:
            return True
        report = read_json(
            session
            / "derived/transcript-simple/whisper-cpp/review-decisions"
            / f"review_decisions_report.{profile}.json"
        )
        return review_lineage.review_profile_is_current(session, report)

    if requested != "auto":
        if requested == output_profile:
            raise IntegrityError("input and output profiles must differ")
        return requested

    prior_report = read_json(
        session
        / "derived/transcript-simple/whisper-cpp/text-integrity"
        / f"transcript_integrity_report.{output_profile}.json"
    )
    if isinstance(prior_report, dict):
        prior_input = str(prior_report.get("input_profile") or "")
        prior_paths = profile_paths(resolved, prior_input)
        if prior_input and prior_input != output_profile and usable(prior_input):
            return prior_input

    for profile in AUTO_INPUT_PROFILES:
        if usable(profile):
            return profile
    raise IntegrityError("no compatible transcript profile found")


def overlap_evidence(left: dict[str, Any], right: dict[str, Any]) -> dict[str, float]:
    left_start = safe_float(left.get("start"))
    left_end = safe_float(left.get("end"), left_start)
    right_start = safe_float(right.get("start"))
    right_end = safe_float(right.get("end"), right_start)
    overlap = max(0.0, min(left_end, right_end) - max(left_start, right_start))
    left_duration = max(0.0, left_end - left_start)
    right_duration = max(0.0, right_end - right_start)
    shorter = min(left_duration, right_duration)
    return {
        "overlap_sec": round(overlap, 6),
        "shorter_overlap_ratio": round(overlap / shorter, 6) if shorter > 0 else 0.0,
        "gap_sec": round(max(0.0, right_start - left_end), 6),
    }


def contains_tokens(container: list[str], fragment: list[str]) -> int | None:
    if not fragment or len(fragment) > len(container):
        return None
    for index in range(len(container) - len(fragment) + 1):
        if container[index : index + len(fragment)] == fragment:
            return index
    return None


def longest_suffix_prefix(left: list[str], right: list[str]) -> int:
    for length in range(min(len(left), len(right)), 0, -1):
        if left[-length:] == right[:length]:
            return length
    return 0


def fuzzy_suffix_contains(long_tokens: list[str], short_tokens: list[str]) -> bool:
    if len(short_tokens) < 3 or len(long_tokens) < len(short_tokens):
        return False
    if long_tokens[-(len(short_tokens) - 1) :] != short_tokens[1:]:
        return False
    left = long_tokens[-len(short_tokens)]
    right = short_tokens[0]
    return len(left) >= 5 and len(right) >= 4 and (left.endswith(right) or right.endswith(left[-4:]))


def stable_candidate_id(kind: str, utterance_ids: list[str], original_text: str) -> str:
    value = {"kind": kind, "utterance_ids": utterance_ids, "original_text": original_text}
    return f"ti_{hash_payload(value)[:16]}"


def candidate(
    *,
    kind: str,
    rows: list[dict[str, Any]],
    action: str,
    proposed_text: str | None,
    repeated_tokens: list[str],
    evidence: dict[str, Any],
    deterministic_safe: bool,
) -> dict[str, Any]:
    utterance_ids = [str(row.get("id") or "") for row in rows]
    original_text = " || ".join(row_text(row) for row in rows)
    start = min(safe_float(row.get("start")) for row in rows)
    end = max(safe_float(row.get("end")) for row in rows)
    return {
        "schema": CANDIDATE_SCHEMA,
        "candidate_id": stable_candidate_id(kind, utterance_ids, original_text),
        "kind": kind,
        "role": role_name(rows[0]),
        "utterance_ids": utterance_ids,
        "start": round(start, 6),
        "end": round(end, 6),
        "original_text": original_text,
        "proposed_action": action,
        "proposed_text": proposed_text,
        "repeated_tokens": repeated_tokens,
        "evidence": evidence,
        "deterministic_safe": deterministic_safe,
        "requires_local_judge": not deterministic_safe,
    }


def adjacent_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for left, right in zip(rows, rows[1:]):
        if role_name(left) != role_name(right) or role_name(left) not in {"me", "remote"}:
            continue
        left_tokens = normalized_tokens(row_text(left))
        right_tokens = normalized_tokens(row_text(right))
        if not left_tokens or not right_tokens:
            continue
        timing = overlap_evidence(left, right)
        close = timing["overlap_sec"] > 0 or timing["gap_sec"] <= 1.0
        if not close:
            continue

        right_in_left = contains_tokens(left_tokens, right_tokens)
        left_in_right = contains_tokens(right_tokens, left_tokens)
        overlap_ratio = timing["shorter_overlap_ratio"]
        if right_in_left is not None and overlap_ratio >= 0.65:
            found.append(
                candidate(
                    kind="adjacent_contained_duplicate",
                    rows=[left, right],
                    action="drop_utterance",
                    proposed_text=None,
                    repeated_tokens=right_tokens,
                    evidence={**timing, "drop_utterance_id": right.get("id"), "kept_utterance_id": left.get("id")},
                    deterministic_safe=True,
                )
            )
            continue
        if left_in_right is not None and overlap_ratio >= 0.65:
            found.append(
                candidate(
                    kind="adjacent_contained_duplicate",
                    rows=[left, right],
                    action="drop_utterance",
                    proposed_text=None,
                    repeated_tokens=left_tokens,
                    evidence={**timing, "drop_utterance_id": left.get("id"), "kept_utterance_id": right.get("id")},
                    deterministic_safe=True,
                )
            )
            continue
        if overlap_ratio >= 0.65 and fuzzy_suffix_contains(left_tokens, right_tokens):
            found.append(
                candidate(
                    kind="adjacent_fuzzy_suffix_duplicate",
                    rows=[left, right],
                    action="drop_utterance",
                    proposed_text=None,
                    repeated_tokens=right_tokens,
                    evidence={**timing, "drop_utterance_id": right.get("id"), "kept_utterance_id": left.get("id")},
                    deterministic_safe=True,
                )
            )
            continue

        suffix_length = longest_suffix_prefix(left_tokens, right_tokens)
        if suffix_length >= 2 and suffix_length < len(right_tokens) and timing["gap_sec"] <= 0.05:
            spans = token_spans(row_text(right))
            cut = spans[suffix_length - 1][2]
            proposed = row_text(right)[cut:].lstrip(" ,.;:!?-\u2014").strip()
            if proposed:
                found.append(
                    candidate(
                        kind="adjacent_boundary_overlap",
                        rows=[left, right],
                        action="replace_text",
                        proposed_text=proposed,
                        repeated_tokens=right_tokens[:suffix_length],
                        evidence={**timing, "target_utterance_id": right.get("id"), "overlap_token_count": suffix_length},
                        deterministic_safe=True,
                    )
                )
                continue

        if left_tokens == right_tokens and timing["gap_sec"] <= 2.0:
            found.append(
                candidate(
                    kind="adjacent_exact_repeat",
                    rows=[left, right],
                    action="drop_utterance",
                    proposed_text=None,
                    repeated_tokens=right_tokens,
                    evidence={**timing, "drop_utterance_id": right.get("id"), "kept_utterance_id": left.get("id")},
                    deterministic_safe=False,
                )
            )
    return found


def repeated_unit(tokens: list[str]) -> tuple[list[str], int] | None:
    if not tokens:
        return None
    for unit_length in range(1, min(4, len(tokens)) + 1):
        if len(tokens) % unit_length:
            continue
        unit = tokens[:unit_length]
        count = len(tokens) // unit_length
        if count >= 4 and unit * count == tokens:
            return unit, count
    return None


def best_adjacent_repeat(tokens: list[str]) -> tuple[int, int] | None:
    best: tuple[int, int] | None = None
    for block_length in range(min(16, len(tokens) // 2), 2, -1):
        for start in range(len(tokens) - block_length * 2 + 1):
            if tokens[start : start + block_length] == tokens[start + block_length : start + block_length * 2]:
                best = (start, block_length)
                break
        if best is not None:
            break
    return best


def remove_first_repeat_text(text: str, start: int, block_length: int) -> str:
    spans = token_spans(text)
    remove_start = spans[start][1]
    remove_end = spans[start + block_length][1]
    repaired = (text[:remove_start] + text[remove_end:]).strip()
    return re.sub(r"\s+", " ", repaired).lstrip(" ,.;:!?-\u2014").strip()


def internal_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for row in rows:
        text = row_text(row)
        tokens = normalized_tokens(text)
        unit = repeated_unit(tokens)
        if unit is not None:
            repeated_tokens, count = unit
            found.append(
                candidate(
                    kind="decoder_repetition_loop",
                    rows=[row],
                    action="drop_utterance",
                    proposed_text=None,
                    repeated_tokens=repeated_tokens,
                    evidence={"repeat_count": count, "unit_token_count": len(repeated_tokens)},
                    deterministic_safe=False,
                )
            )
            continue
        repeat = best_adjacent_repeat(tokens)
        if repeat is None:
            continue
        start, block_length = repeat
        proposed = remove_first_repeat_text(text, start, block_length)
        if not proposed or proposed == text:
            continue
        found.append(
            candidate(
                kind="internal_exact_repeat",
                rows=[row],
                action="replace_text",
                proposed_text=proposed,
                repeated_tokens=tokens[start : start + block_length],
                evidence={"repeat_start_token": start, "repeat_token_count": block_length, "repeat_count": 2},
                deterministic_safe=False,
            )
        )
    return found


def deduplicate_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    values = adjacent_candidates(rows) + internal_candidates(rows)
    by_id = {row["candidate_id"]: row for row in values}
    return sorted(by_id.values(), key=lambda row: (row["start"], row["end"], row["candidate_id"]))


def resolve_audio(session: Path, role: str) -> Path | None:
    candidates = (
        (
            session / "derived/asr/remote.wav",
            session / "derived/preprocess/audio/remote_for_aec.wav",
        )
        if role == "remote"
        else (
            session / "derived/preprocess/audio/mic_clean_local_fir.wav",
            session / "derived/asr/mic.wav",
            session / "derived/preprocess/audio/mic_for_asr.wav",
        )
    )
    return next((path for path in candidates if path.is_file()), None)


def resolve_model(args: argparse.Namespace) -> Path:
    if args.model is not None:
        return args.model.expanduser().resolve()
    env = os.environ.get("MURMURMARK_FASTER_WHISPER_MODEL")
    return Path(env).expanduser().resolve() if env else DEFAULT_MODEL.resolve()


def model_ready(path: Path) -> bool:
    return path.is_dir() and (path / "model.bin").is_file()


def load_audio_slice(path: Path, start: float, end: float) -> tuple[np.ndarray, int, float]:
    with sf.SoundFile(str(path)) as handle:
        rate = int(handle.samplerate)
        begin_frame = max(0, int(round(start * rate)))
        end_frame = min(len(handle), int(round(end * rate)))
        handle.seek(begin_frame)
        audio = handle.read(max(0, end_frame - begin_frame), dtype="float32", always_2d=True)
    mono = audio.mean(axis=1) if audio.size else np.zeros(0, dtype=np.float32)
    rms = float(np.sqrt(np.mean(np.square(mono, dtype=np.float64)) + 1e-12)) if mono.size else 0.0
    rms_db = 20.0 * math.log10(max(rms, 1e-12))
    return mono.astype(np.float32), rate, round(rms_db, 6)


def load_judge_fixture(path: Path | None) -> dict[str, dict[str, Any]]:
    payload = read_json(path) if path is not None else None
    values = payload.get("candidates") if isinstance(payload, dict) else None
    if not isinstance(values, dict):
        return {}
    return {str(key): value for key, value in values.items() if isinstance(value, dict)}


def load_model(path: Path, args: argparse.Namespace) -> Any:
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    from faster_whisper import WhisperModel

    return WhisperModel(str(path), device=args.device, compute_type=args.compute_type)


def transcribe_audio(model: Any, audio: np.ndarray, args: argparse.Namespace) -> dict[str, Any]:
    segments, info = model.transcribe(
        audio,
        language=args.language,
        beam_size=args.beam_size,
        temperature=0.0,
        condition_on_previous_text=False,
        vad_filter=False,
        word_timestamps=True,
    )
    segment_rows: list[dict[str, Any]] = []
    words: list[dict[str, Any]] = []
    for segment in segments:
        text = str(segment.text or "").strip()
        segment_rows.append(
            {
                "start": round(safe_float(segment.start), 6),
                "end": round(safe_float(segment.end), 6),
                "text": text,
                "avg_logprob": round(safe_float(getattr(segment, "avg_logprob", 0.0)), 6),
                "no_speech_prob": round(safe_float(getattr(segment, "no_speech_prob", 0.0)), 6),
            }
        )
        for word in getattr(segment, "words", None) or []:
            words.append(
                {
                    "start": round(safe_float(word.start), 6),
                    "end": round(safe_float(word.end), 6),
                    "word": str(word.word or "").strip(),
                    "probability": round(safe_float(getattr(word, "probability", 0.0)), 6),
                }
            )
    return {
        "status": "ok",
        "text": " ".join(row["text"] for row in segment_rows if row["text"]).strip(),
        "segments": segment_rows,
        "words": words,
        "language": str(getattr(info, "language", args.language) or args.language),
    }


def phrase_occurrences(haystack: list[str], needle: list[str]) -> int:
    if not needle or len(needle) > len(haystack):
        return 0
    return sum(1 for index in range(len(haystack) - len(needle) + 1) if haystack[index : index + len(needle)] == needle)


def fuzzy_phrase_support(haystack: list[str], needle: list[str]) -> float:
    if not haystack or not needle:
        return 0.0
    width_min = max(1, len(needle) - 2)
    width_max = min(len(haystack), len(needle) + 2)
    best = 0.0
    for width in range(width_min, width_max + 1):
        for start in range(len(haystack) - width + 1):
            best = max(best, SequenceMatcher(None, needle, haystack[start : start + width]).ratio())
    return round(best, 6)


def text_similarity(left: Any, right: Any) -> float:
    return round(SequenceMatcher(None, normalized_tokens(left), normalized_tokens(right)).ratio(), 6)


def judge_fingerprint(candidate_row: dict[str, Any], audio_path: Path, audio_sha256: str, model_path: Path) -> str:
    return hash_payload(
        {
            "candidate": candidate_row,
            "audio": {"path": audio_path.name, "sha256": audio_sha256},
            "model": str(model_path),
            "judge_version": SCRIPT_VERSION,
        }
    )


def judge_candidates(
    args: argparse.Namespace,
    session: Path,
    out_dir: Path,
    candidates: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    fixture = load_judge_fixture(args.judge_fixture)
    model_path = resolve_model(args)
    model: Any = None
    model_error: str | None = None
    source_hashes: dict[Path, str] = {}
    results: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    computed = 0

    for candidate_row in candidates:
        if not candidate_row["requires_local_judge"]:
            continue
        candidate_id = candidate_row["candidate_id"]
        fixture_row = fixture.get(candidate_id)
        if fixture_row is None:
            fixture_row = fixture.get(candidate_row["kind"])
        audio_path = resolve_audio(session, candidate_row["role"])
        if audio_path is None:
            results[candidate_id] = {"status": "unavailable", "reason": "audio_missing"}
            continue
        audio_sha256 = source_hashes.setdefault(audio_path, hash_file(audio_path))
        fingerprint = judge_fingerprint(candidate_row, audio_path, audio_sha256, model_path)
        cache_path = out_dir / "judge-cache" / f"{fingerprint}.json"
        if fixture_row is not None:
            result = {
                "status": str(fixture_row.get("status") or "ok"),
                "text": str(fixture_row.get("text") or ""),
                "source": "fixture",
            }
        elif cache_path.is_file() and not args.force_judge:
            result = read_json(cache_path) or {"status": "unavailable", "reason": "invalid_cache"}
        elif args.judge_mode != "auto":
            result = {"status": "unavailable", "reason": f"judge_mode_{args.judge_mode}"}
        elif computed >= args.max_judge_items:
            result = {"status": "unavailable", "reason": "max_judge_items_reached"}
        elif not model_ready(model_path):
            result = {"status": "unavailable", "reason": "model_missing", "model": str(model_path)}
        else:
            try:
                if model is None and model_error is None:
                    model = load_model(model_path, args)
                start = max(0.0, safe_float(candidate_row.get("start")) - 2.0)
                end = max(start, safe_float(candidate_row.get("end")) + 2.0)
                audio, rate, rms_db = load_audio_slice(audio_path, start, end)
                _, _, target_rms_db = load_audio_slice(
                    audio_path,
                    safe_float(candidate_row.get("start")),
                    safe_float(candidate_row.get("end")),
                )
                if rate != 16000:
                    raise IntegrityError(f"expected 16 kHz judge audio, got {rate} Hz: {audio_path}")
                decoded = transcribe_audio(model, audio, args)
                result = {
                    **decoded,
                    "source": "faster_whisper",
                    "model": str(model_path),
                    "audio": {
                        "path": rel(audio_path, session),
                        "sha256": audio_sha256,
                        "start": round(start, 6),
                        "end": round(end, 6),
                        "rms_db": rms_db,
                        "target_rms_db": target_rms_db,
                    },
                }
                computed += 1
                write_json(cache_path, result)
            except Exception as error:  # fail open: optional local evidence must not break transcript
                model_error = f"{type(error).__name__}: {error}"
                result = {"status": "unavailable", "reason": "judge_failed", "detail": model_error}
                warnings.append(model_error)
        if fixture_row is not None:
            start = max(0.0, safe_float(candidate_row.get("start")) - 2.0)
            end = max(start, safe_float(candidate_row.get("end")) + 2.0)
            _, _, rms_db = load_audio_slice(audio_path, start, end)
            _, _, target_rms_db = load_audio_slice(
                audio_path,
                safe_float(candidate_row.get("start")),
                safe_float(candidate_row.get("end")),
            )
            result["audio"] = {
                "path": rel(audio_path, session),
                "sha256": audio_sha256,
                "start": round(start, 6),
                "end": round(end, 6),
                "rms_db": rms_db,
                "target_rms_db": target_rms_db,
            }
        result["fingerprint"] = fingerprint
        results[candidate_id] = result
    return results, sorted(set(warnings))


def classify(candidate_row: dict[str, Any], judge: dict[str, Any] | None) -> dict[str, Any]:
    if candidate_row["deterministic_safe"]:
        return {"outcome": "apply", "confidence": 1.0, "reason": "deterministic_duplicate_proof"}
    if not isinstance(judge, dict) or judge.get("status") != "ok":
        return {
            "outcome": "needs_review",
            "confidence": 0.0,
            "reason": str((judge or {}).get("reason") or "local_judge_unavailable"),
        }

    judge_text = str(judge.get("text") or "")
    judge_tokens = normalized_tokens(judge_text)
    repeated = [str(value) for value in candidate_row.get("repeated_tokens") or []]
    exact_count = phrase_occurrences(judge_tokens, repeated)
    fuzzy_support = fuzzy_phrase_support(judge_tokens, repeated)
    evidence = candidate_row.get("evidence") if isinstance(candidate_row.get("evidence"), dict) else {}
    original_text = candidate_row["original_text"]
    proposed_text = candidate_row.get("proposed_text") or ""
    original_similarity = text_similarity(original_text, judge_text)
    repaired_similarity = text_similarity(proposed_text, judge_text)
    metrics = {
        "judge_text": judge_text,
        "judge_exact_repeat_count": exact_count,
        "judge_fuzzy_repeat_support": fuzzy_support,
        "original_similarity": original_similarity,
        "repaired_similarity": repaired_similarity,
        "repair_gain": round(repaired_similarity - original_similarity, 6),
    }

    if candidate_row["kind"] == "decoder_repetition_loop":
        repeat_count = int(evidence.get("repeat_count") or 0)
        audio = judge.get("audio") if isinstance(judge.get("audio"), dict) else {}
        rms_db = safe_float(audio.get("target_rms_db"), 0.0)
        if repeat_count >= 4 and exact_count == 0 and fuzzy_support < 0.55 and rms_db <= -55.0:
            return {
                "outcome": "apply",
                "confidence": 0.99,
                "reason": "silent_decoder_loop_rejected_by_independent_asr",
                "metrics": metrics,
            }
        return {
            "outcome": "needs_review",
            "confidence": 0.5,
            "reason": "repetition_loop_not_independently_disproved",
            "metrics": metrics,
        }

    if candidate_row["kind"] == "internal_exact_repeat":
        if exact_count <= 1 and fuzzy_support >= 0.72 and (
            repaired_similarity >= 0.48 and repaired_similarity - original_similarity >= 0.04
        ):
            return {
                "outcome": "apply",
                "confidence": round(min(0.99, 0.82 + max(0.0, repaired_similarity - original_similarity)), 6),
                "reason": "single_repeat_supported_by_independent_asr",
                "metrics": metrics,
            }
        return {
            "outcome": "needs_review",
            "confidence": 0.5,
            "reason": "independent_asr_does_not_prove_internal_duplicate",
            "metrics": metrics,
        }

    if candidate_row["kind"] == "adjacent_exact_repeat":
        if exact_count == 1 and fuzzy_support >= 0.80:
            return {
                "outcome": "apply",
                "confidence": 0.9,
                "reason": "single_utterance_supported_by_independent_asr",
                "metrics": metrics,
            }
        return {
            "outcome": "needs_review",
            "confidence": 0.5,
            "reason": "independent_asr_does_not_prove_adjacent_duplicate",
            "metrics": metrics,
        }

    return {"outcome": "needs_review", "confidence": 0.0, "reason": "unsupported_candidate_kind", "metrics": metrics}


def mark_integrity_quality(row: dict[str, Any], decision: dict[str, Any], applied: bool) -> None:
    quality = row.setdefault("quality", {})
    if not isinstance(quality, dict):
        quality = {}
        row["quality"] = quality
    values = quality.setdefault("transcript_integrity", [])
    if not isinstance(values, list):
        values = []
        quality["transcript_integrity"] = values
    values.append(
        {
            "candidate_id": decision["candidate_id"],
            "kind": decision["kind"],
            "outcome": decision["outcome"],
            "status": "needs_review" if decision["outcome"] == "needs_review" else "applied",
            "reason": decision["reason"],
            "profile": OUTPUT_PROFILE,
            "utterance_ids": decision["utterance_ids"],
            "source_audit_ids": [decision["candidate_id"]],
        }
    )
    if not applied and decision["outcome"] == "needs_review":
        quality["needs_review"] = True


def apply_decisions(
    rows: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    judges: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    output = [copy.deepcopy(row) for row in rows]
    by_id = {str(row.get("id")): row for row in output}
    dropped_ids: set[str] = set()
    patches: list[dict[str, Any]] = []
    review: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []

    for candidate_row in candidates:
        classification = classify(candidate_row, judges.get(candidate_row["candidate_id"]))
        decision = {
            **candidate_row,
            **classification,
            "judge": judges.get(candidate_row["candidate_id"]),
        }
        decisions.append(decision)
        target_id = str((candidate_row.get("evidence") or {}).get("target_utterance_id") or "")
        drop_id = str((candidate_row.get("evidence") or {}).get("drop_utterance_id") or "")
        if classification["outcome"] != "apply":
            for utterance_id in candidate_row["utterance_ids"]:
                if utterance_id in by_id:
                    mark_integrity_quality(by_id[utterance_id], decision, False)
                    break
            review.append(
                {
                    "schema": REVIEW_SCHEMA,
                    "candidate_id": candidate_row["candidate_id"],
                    "kind": candidate_row["kind"],
                    "role": candidate_row["role"],
                    "start": candidate_row["start"],
                    "end": candidate_row["end"],
                    "utterance_ids": candidate_row["utterance_ids"],
                    "reason": classification["reason"],
                    "text": candidate_row["original_text"],
                }
            )
            continue

        if candidate_row["proposed_action"] == "drop_utterance":
            if not drop_id and len(candidate_row["utterance_ids"]) == 1:
                drop_id = candidate_row["utterance_ids"][0]
            if drop_id not in by_id:
                decision["outcome"] = "needs_review"
                decision["reason"] = "drop_target_missing"
                review.append(
                    {
                        "schema": REVIEW_SCHEMA,
                        "candidate_id": candidate_row["candidate_id"],
                        "kind": candidate_row["kind"],
                        "role": candidate_row["role"],
                        "start": candidate_row["start"],
                        "end": candidate_row["end"],
                        "utterance_ids": candidate_row["utterance_ids"],
                        "reason": "drop_target_missing",
                        "text": candidate_row["original_text"],
                    }
                )
                continue
            dropped_ids.add(drop_id)
        elif candidate_row["proposed_action"] == "replace_text":
            if not target_id and len(candidate_row["utterance_ids"]) == 1:
                target_id = candidate_row["utterance_ids"][0]
            target = by_id.get(target_id)
            proposed = str(candidate_row.get("proposed_text") or "").strip()
            if target is None or not proposed:
                decision["outcome"] = "needs_review"
                decision["reason"] = "replace_target_missing"
                continue
            original = row_text(target)
            target["text"] = proposed
            if "corrected_text" in target:
                target["corrected_text"] = proposed
            target.setdefault("corrections", []).append(
                {
                    "reason": "transcript_integrity_repair",
                    "candidate_id": candidate_row["candidate_id"],
                    "source_text": original,
                    "repaired_text": proposed,
                }
            )
            mark_integrity_quality(target, decision, True)
        patch = {
            "schema": PATCH_SCHEMA,
            "candidate_id": candidate_row["candidate_id"],
            "kind": candidate_row["kind"],
            "role": candidate_row["role"],
            "utterance_ids": candidate_row["utterance_ids"],
            "action": candidate_row["proposed_action"],
            "drop_utterance_id": drop_id or None,
            "target_utterance_id": target_id or None,
            "original_text": candidate_row["original_text"],
            "repaired_text": candidate_row.get("proposed_text"),
            "reason": decision["reason"],
            "confidence": decision["confidence"],
            "evidence": candidate_row["evidence"],
            "judge": decision.get("judge"),
        }
        patches.append(patch)

    return [row for row in output if str(row.get("id")) not in dropped_ids], patches, review, decisions


def build_overlaps(
    rows: list[dict[str, Any]],
    source: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    by_id = {str(row.get("id") or ""): row for row in rows}
    source_rows = source.get("overlaps") if isinstance(source, dict) else None
    if isinstance(source_rows, list):
        values: list[dict[str, Any]] = []
        for source_row in source_rows:
            if not isinstance(source_row, dict):
                continue
            me = by_id.get(str(source_row.get("me_utterance_id") or ""))
            remote = by_id.get(str(source_row.get("remote_utterance_id") or ""))
            if me is None or remote is None:
                continue
            start = max(safe_float(me.get("start")), safe_float(remote.get("start")))
            end = min(safe_float(me.get("end")), safe_float(remote.get("end")))
            if end <= start:
                continue
            value = copy.deepcopy(source_row)
            value.update(
                {
                    "start": round(start, 6),
                    "end": round(end, 6),
                    "duration_sec": round(end - start, 6),
                    "me_text": row_text(me),
                    "remote_text": row_text(remote),
                    "text_similarity": text_similarity(row_text(me), row_text(remote)),
                }
            )
            if "duration" in value:
                value["duration"] = value["duration_sec"]
            values.append(value)
        return values

    values: list[dict[str, Any]] = []
    me_rows = [row for row in rows if role_name(row) == "me"]
    remote_rows = [row for row in rows if role_name(row) == "remote"]
    for me in me_rows:
        for remote in remote_rows:
            start = max(safe_float(me.get("start")), safe_float(remote.get("start")))
            end = min(safe_float(me.get("end")), safe_float(remote.get("end")))
            if end <= start:
                continue
            values.append(
                {
                    "id": f"ov_{len(values) + 1:06d}",
                    "start": round(start, 6),
                    "end": round(end, 6),
                    "duration_sec": round(end - start, 6),
                    "me_utterance_id": me.get("id"),
                    "remote_utterance_id": remote.get("id"),
                    "text_similarity": text_similarity(row_text(me), row_text(remote)),
                    "me_text": row_text(me),
                    "remote_text": row_text(remote),
                }
            )
    return values


def write_markdown(path: Path, rows: list[dict[str, Any]], profile: str) -> None:
    lines = ["# Transcript", "", f"Profile: `{profile}`", ""]
    for row in rows:
        minutes, seconds = divmod(int(max(0.0, safe_float(row.get("start")))), 60)
        hours, minutes = divmod(minutes, 60)
        stamp = f"{hours:02d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"
        label = "Me" if role_name(row) == "me" else "Colleagues"
        lines.extend([f"## {stamp} {label}", "", row_text(row), ""])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def quality_report(
    source: dict[str, Any],
    rows: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    patches: list[dict[str, Any]],
    review: list[dict[str, Any]],
    input_profile: str,
    output_profile: str,
) -> dict[str, Any]:
    result = copy.deepcopy(source)
    result["schema"] = str(source.get("schema") or "murmurmark.quality_report/v1")
    result["profile"] = output_profile
    result["utterances"] = len(rows)
    needs_review = sum(
        1
        for row in rows
        if isinstance(row.get("quality"), dict) and row["quality"].get("needs_review") is True
    )
    result["needs_review_count"] = needs_review
    result["needs_review_ratio"] = round(needs_review / max(1, len(rows)), 6)
    result["transcript_integrity"] = {
        "schema": "murmurmark.transcript_integrity_quality/v1",
        "input_profile": input_profile,
        "output_profile": output_profile,
        "candidate_count": len(candidates),
        "applied_patch_count": len(patches),
        "remaining_review_count": len(review),
        "dropped_utterance_count": sum(1 for row in patches if row.get("action") == "drop_utterance"),
        "repaired_utterance_count": sum(1 for row in patches if row.get("action") == "replace_text"),
    }
    return result


def simple_payload(source: dict[str, Any] | None, dialogue: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    payload = copy.deepcopy(source) if isinstance(source, dict) else {}
    payload["schema"] = str(payload.get("schema") or "murmurmark.transcript_simple/v1")
    payload["session"] = dialogue.get("session")
    payload["backend"] = payload.get("backend") or "whisper.cpp"
    source_rows = source.get("utterances") if isinstance(source, dict) else []
    by_id = {
        str(row.get("id") or ""): row
        for row in source_rows or []
        if isinstance(row, dict)
    }
    merged_rows: list[dict[str, Any]] = []
    for row in rows:
        identifier = str(row.get("id") or "")
        source_row = copy.deepcopy(by_id.get(identifier) or {})
        source_corrections = source_row.get("corrections") if isinstance(source_row.get("corrections"), list) else []
        row_corrections = row.get("corrections") if isinstance(row.get("corrections"), list) else []
        source_row.update(copy.deepcopy(row))
        source_row["raw_text"] = str(
            by_id.get(identifier, {}).get("raw_text")
            or by_id.get(identifier, {}).get("text")
            or row_text(row)
        )
        source_row["corrected_text"] = row_text(row)
        source_row["text"] = row_text(row)
        source_row["corrections"] = copy.deepcopy(source_corrections)
        for correction in row_corrections:
            if correction not in source_row["corrections"]:
                source_row["corrections"].append(copy.deepcopy(correction))
        merged_rows.append(source_row)
    payload["utterances"] = merged_rows
    return payload


def main() -> int:
    args = parse_args()
    session = args.session.expanduser().resolve()
    if not (session / "session.json").is_file():
        raise IntegrityError(f"session.json not found: {session}")
    if args.output_profile != OUTPUT_PROFILE:
        raise IntegrityError(f"unsupported output profile: {args.output_profile}")

    resolved = session / "derived/transcript-simple/whisper-cpp/resolved"
    out_dir = session / "derived/transcript-simple/whisper-cpp/text-integrity"
    input_profile = resolve_input_profile(session, args.input_profile, args.output_profile)
    inputs = profile_paths(resolved, input_profile)
    outputs = profile_paths(resolved, args.output_profile)
    dialogue = read_json(inputs["dialogue"])
    quality = read_json(inputs["quality"])
    if dialogue is None or quality is None:
        raise IntegrityError(f"invalid input profile: {input_profile}")
    rows = [row for row in dialogue.get("utterances") or [] if isinstance(row, dict)]
    if not rows:
        raise IntegrityError("input transcript is empty")

    input_hashes = {
        key: {"path": rel(path, session), "sha256": hash_file(path)}
        for key, path in inputs.items()
        if path.is_file()
    }
    raw_hashes = {
        str(path.relative_to(session)): hash_file(path)
        for path in sorted((session / "audio").glob("*/*.caf"))
    }
    candidates = deduplicate_candidates(rows)
    judges, warnings = judge_candidates(args, session, out_dir, candidates)
    output_rows, patches, review, decisions = apply_decisions(rows, candidates, judges)
    source_overlaps = read_json(inputs["overlaps"])
    overlaps = build_overlaps(output_rows, source_overlaps)

    kept_input_ids = {str(row.get("id")) for row in rows} - {
        str(patch.get("drop_utterance_id")) for patch in patches if patch.get("drop_utterance_id")
    }
    output_ids = {str(row.get("id")) for row in output_rows}
    roles_changed = sum(
        1
        for original in rows
        if str(original.get("id")) in output_ids
        and role_name(original) != role_name(next(row for row in output_rows if row.get("id") == original.get("id")))
    )
    timestamps_changed = sum(
        1
        for original in rows
        if str(original.get("id")) in output_ids
        and any(
            safe_float(original.get(key))
            != safe_float(next(row for row in output_rows if row.get("id") == original.get("id")).get(key))
            for key in ("start", "end")
        )
    )
    lineage_ok = kept_input_ids == output_ids
    gates = {
        "passed": roles_changed == 0 and timestamps_changed == 0 and lineage_ok,
        "roles_unchanged": roles_changed == 0,
        "timestamps_unchanged": timestamps_changed == 0,
        "utterance_lineage_exact": lineage_ok,
        "raw_capture_unchanged": True,
        "fail_open_judge": all(
            decision["outcome"] != "apply" or decision["deterministic_safe"] or decision.get("judge", {}).get("status") == "ok"
            for decision in decisions
        ),
    }
    gates["passed"] = gates["passed"] and gates["fail_open_judge"]

    output_dialogue = copy.deepcopy(dialogue)
    output_dialogue["schema"] = "murmurmark.clean_dialogue/v1"
    output_dialogue["session"] = dialogue.get("session", session.name)
    output_dialogue["utterances"] = output_rows
    output_quality = quality_report(
        quality,
        output_rows,
        candidates,
        patches,
        review,
        input_profile,
        args.output_profile,
    )
    source_simple = read_json(inputs["simple"])
    output_simple = simple_payload(source_simple, dialogue, output_rows)
    overlap_payload = copy.deepcopy(source_overlaps) if isinstance(source_overlaps, dict) else {}
    overlap_payload["schema"] = str(
        overlap_payload.get("schema") or "murmurmark.transcript_overlaps/v1"
    )
    overlap_payload["session"] = dialogue.get("session", session.name)
    overlap_payload["overlaps"] = overlaps

    write_json(outputs["dialogue"], output_dialogue)
    write_json(outputs["quality"], output_quality)
    write_json(outputs["simple"], output_simple)
    write_json(outputs["overlaps"], overlap_payload)
    write_markdown(outputs["markdown"], output_rows, args.output_profile)
    write_jsonl(out_dir / f"transcript_integrity_candidates.{args.output_profile}.jsonl", candidates)
    write_jsonl(out_dir / f"transcript_integrity_patches.{args.output_profile}.jsonl", patches)
    write_jsonl(out_dir / f"transcript_integrity_review.{args.output_profile}.jsonl", review)

    output_hashes = {
        key: {"path": rel(path, session), "sha256": hash_file(path)}
        for key, path in outputs.items()
        if path.is_file()
    }
    report = {
        "schema": REPORT_SCHEMA,
        "generator": {"name": "apply-transcript-integrity", "version": SCRIPT_VERSION},
        "session": session.name,
        "input_profile": input_profile,
        "output_profile": args.output_profile,
        "inputs": input_hashes,
        "raw_capture": raw_hashes,
        "judge": {
            "mode": args.judge_mode,
            "model": str(resolve_model(args)),
            "candidate_count": sum(1 for row in candidates if row["requires_local_judge"]),
            "available_count": sum(1 for row in judges.values() if row.get("status") == "ok"),
            "warnings": warnings,
        },
        "summary": {
            "input_utterances": len(rows),
            "output_utterances": len(output_rows),
            "candidate_count": len(candidates),
            "applied_patch_count": len(patches),
            "remaining_review_count": len(review),
            "dropped_utterance_count": sum(1 for row in patches if row.get("action") == "drop_utterance"),
            "repaired_utterance_count": sum(1 for row in patches if row.get("action") == "replace_text"),
            "by_kind": dict(sorted(Counter(row["kind"] for row in candidates).items())),
            "applied_by_kind": dict(sorted(Counter(row["kind"] for row in patches).items())),
            "review_by_kind": dict(sorted(Counter(row["kind"] for row in review).items())),
        },
        "gates": gates,
        "outputs": output_hashes,
        "output_fingerprint": hash_payload(output_hashes),
        "decision_fingerprint": hash_payload(
            [
                {
                    "candidate_id": row["candidate_id"],
                    "outcome": row["outcome"],
                    "reason": row["reason"],
                }
                for row in decisions
            ]
        ),
    }
    report_path = out_dir / f"transcript_integrity_report.{args.output_profile}.json"
    write_json(report_path, report)

    current_raw_hashes = {
        str(path.relative_to(session)): hash_file(path)
        for path in sorted((session / "audio").glob("*/*.caf"))
    }
    if raw_hashes != current_raw_hashes:
        raise IntegrityError("raw capture changed while applying transcript integrity profile")
    if not gates["passed"]:
        raise IntegrityError("transcript integrity gates failed")

    print(f"input_profile: {input_profile}")
    print(f"output_profile: {args.output_profile}")
    print(f"candidates: {len(candidates)}")
    print(f"applied: {len(patches)}")
    print(f"needs_review: {len(review)}")
    print(f"report: {report_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except IntegrityError as error:
        print(f"error: {error}", file=os.sys.stderr)
        raise SystemExit(2)
