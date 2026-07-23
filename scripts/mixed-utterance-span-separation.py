#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import math
import os
import re
import subprocess
import sys
import time
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any


SCRIPT_VERSION = "0.1.0"
PROFILE = "mixed_utterance_separation_v1"
REPORT_DIR_NAME = "mixed-utterance-separation-v1"
SPEAKER_REPORT_DIR_NAME = "speaker-mode-hardening-v1"
PROMOTE_DECISION = "PROMOTE_MIXED_UTTERANCE_SEPARATION_V1"
MIN_DUPLICATE_REDUCTION = 0.25
MIN_REVIEW_REDUCTION = 0.15
MAX_RUNTIME_RATIO = 0.25
TOKEN_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё_./+-]+")
VALID_OUTCOMES = {
    "confirmed_local",
    "confirmed_remote_duplicate",
    "confirmed_remote_leak",
    "confirmed_double_talk",
    "probable_asr_noise",
    "ambiguous",
}
VALID_ACTIONS = {
    "keep_unchanged",
    "split_remove_remote_span",
    "split_keep_local_prefix",
    "split_keep_local_tail",
    "split_keep_both_local_islands",
    "needs_review",
}
KNOWN_HALLUCINATIONS = (
    "продолжение следует",
    "спасибо за просмотр",
    "редактор субтитров",
    "субтитры сделал",
)


def load_sibling(filename: str, module_name: str) -> ModuleType:
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load sibling module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


BASE = load_sibling("residual-me-evidence.py", "murmurmark_mixed_span_base")
AUDIO = load_sibling("residual-audio-arbitration.py", "murmurmark_mixed_span_audio")
STRONGER = load_sibling("audit-stronger-audio-judge.py", "murmurmark_mixed_span_stronger")
ORDER = load_sibling("apply-transcript-order-repair.py", "murmurmark_mixed_span_order")
ORDER_AUDIT = load_sibling("audit-transcript-order.py", "murmurmark_mixed_span_order_audit")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Separate independently proven remote spans from mixed Me utterances."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def common(command: argparse.ArgumentParser) -> None:
        command.add_argument("--sessions-root", type=Path, default=Path("sessions"))
        command.add_argument("--out-dir", type=Path)

    freeze = subparsers.add_parser("freeze", help="Freeze mixed utterances and all input identities.")
    common(freeze)
    freeze.add_argument("--force", action="store_true")

    evidence = subparsers.add_parser("evidence", help="Build local and remote word-level evidence.")
    common(evidence)
    evidence.add_argument("sessions", nargs="*", type=Path)
    add_model_args(evidence)

    apply = subparsers.add_parser("apply", help="Apply safe span decisions to an isolated profile.")
    common(apply)
    apply.add_argument("session", type=Path)
    apply.add_argument("--mode", choices=("conservative", "dry-run"), default="conservative")

    evaluate = subparsers.add_parser("evaluate", help="Evaluate the frozen candidate corpus.")
    common(evaluate)
    evaluate.add_argument("--apply", action="store_true")
    evaluate.add_argument("--synthesize", action="store_true")

    run = subparsers.add_parser("run", help="Freeze, collect evidence, apply, synthesize and evaluate.")
    common(run)
    run.add_argument("--force-freeze", action="store_true")
    run.add_argument("--skip-synthesis", action="store_true")
    add_model_args(run)
    return parser.parse_args()


def add_model_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--model", type=Path)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--compute-type", default="int8")
    parser.add_argument("--language", default="ru")
    parser.add_argument("--beam-size", type=int, default=1)
    parser.add_argument("--allow-download", action="store_true")
    parser.add_argument("--no-cache", action="store_true")


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return default if math.isnan(result) or math.isinf(result) else result


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def read_json(path: Path) -> dict[str, Any] | None:
    return BASE.read_json(path)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return BASE.read_jsonl(path)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    BASE.write_json(path, payload)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    BASE.write_jsonl(path, rows)


def canonical_bytes(value: Any) -> bytes:
    return BASE.canonical_bytes(value)


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def sha256_file(path: Path) -> str | None:
    return BASE.sha256_file(path)


def suffix(profile: str) -> str:
    return "" if profile == "current" else f".{profile}"


def profile_paths(session: Path, profile: str) -> dict[str, Path]:
    return BASE.profile_paths(session, profile)


def report_dir(sessions_root: Path, explicit: Path | None) -> Path:
    return explicit.expanduser().resolve() if explicit else sessions_root / "_reports" / REPORT_DIR_NAME


def session_audit_dir(session: Path) -> Path:
    return session / "derived/audit" / REPORT_DIR_NAME


def session_profile_dir(session: Path) -> Path:
    return session / "derived/transcript-simple/whisper-cpp" / REPORT_DIR_NAME


def artifact(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "exists": path.is_file(),
        "size": path.stat().st_size if path.is_file() else None,
        "sha256": sha256_file(path),
    }


def interval(row: dict[str, Any]) -> tuple[float, float]:
    nested = row.get("interval") if isinstance(row.get("interval"), dict) else {}
    start = safe_float(nested.get("start"), safe_float(row.get("start")))
    end = safe_float(nested.get("end"), safe_float(row.get("end"), start))
    return start, max(start, end)


def duration(row: dict[str, Any]) -> float:
    start, end = interval(row)
    return max(0.0, end - start)


def role_name(row: dict[str, Any]) -> str:
    return BASE.role_name(row)


def normalize_token(value: str) -> str:
    return value.strip(".,!?;:()[]{}«»\"'`").replace("ё", "е").lower()


def token_records(text: str) -> list[dict[str, Any]]:
    content = set(STRONGER.content_tokens(text))
    rows: list[dict[str, Any]] = []
    for index, match in enumerate(TOKEN_RE.finditer(text)):
        normalized = normalize_token(match.group(0))
        if not normalized:
            continue
        rows.append(
            {
                "index": index,
                "text": match.group(0),
                "normalized": normalized,
                "start": match.start(),
                "end": match.end(),
                "content": normalized in content,
            }
        )
    return rows


def token_similarity(left: str, right: str) -> float:
    if left == right:
        return 1.0
    if len(left) < 4 or len(right) < 4:
        return 0.0
    return SequenceMatcher(None, left, right, autojunk=False).ratio()


def span_plan(target_text: str, remote_text: str) -> dict[str, Any] | None:
    target = token_records(target_text)
    remote = token_records(remote_text)
    target_content = [row for row in target if row["content"]]
    remote_content = [row for row in remote if row["content"]]
    if len(target_content) < 2 or len(remote_content) < 2:
        return None
    matcher = SequenceMatcher(
        None,
        [row["normalized"] for row in target],
        [row["normalized"] for row in remote],
        autojunk=False,
    )
    pairs = [
        (block.a + offset, block.b + offset, 1.0)
        for block in matcher.get_matching_blocks()
        for offset in range(block.size)
    ]
    target_content_indices = {row["index"] for row in target_content}
    if sum(1 for target_index, _remote_index, _score in pairs if target_index in target_content_indices) < 2:
        return None
    pairs.sort()
    clusters: list[list[tuple[int, int, float]]] = []
    current: list[tuple[int, int, float]] = []
    for pair in pairs:
        if current:
            target_gap = pair[0] - current[-1][0]
            remote_gap = pair[1] - current[-1][1]
            if target_gap > 3 or remote_gap < 0 or remote_gap > 4:
                clusters.append(current)
                current = []
        current.append(pair)
    if current:
        clusters.append(current)
    cluster = max(
        clusters,
        key=lambda values: (
            sum(1 for value in values if value[0] in target_content_indices),
            len(values),
        ),
    )
    if sum(1 for value in cluster if value[0] in target_content_indices) < 2:
        return None
    first_index = cluster[0][0]
    last_index = cluster[-1][0]
    selected = [row for row in target if first_index <= row["index"] <= last_index]
    if not selected:
        return None
    span_start = selected[0]["start"]
    span_end = selected[-1]["end"]
    span_text = target_text[span_start:span_end].strip()
    metric = STRONGER.text_similarity(span_text, remote_text)
    matched_indices = {value[0] for value in cluster}
    unmatched_content = [
        row["normalized"] for row in selected if row["content"] and row["index"] not in matched_indices
    ]
    if len(unmatched_content) > 1:
        return None
    if safe_float(metric.get("similarity")) < 0.58 and safe_float(metric.get("containment")) < 0.50:
        return None
    prefix = target_text[:span_start].strip(" \t\n,.;:-")
    tail = target_text[span_end:].strip(" \t\n,.;:-")
    local_tokens = STRONGER.content_tokens(f"{prefix} {tail}")
    if not local_tokens:
        return None
    return {
        "target_token_start": first_index,
        "target_token_end_exclusive": last_index + 1,
        "char_start": span_start,
        "char_end": span_end,
        "remote_span_text": span_text,
        "local_prefix_text": prefix,
        "local_tail_text": tail,
        "matched_content_tokens": len(cluster),
        "unmatched_content_tokens_inside_span": unmatched_content,
        "unique_local_content_tokens": sorted(set(local_tokens) - set(STRONGER.content_tokens(remote_text))),
        "remote_similarity": metric,
    }


def find_utterance(dialogue: dict[str, Any], utterance_id: str) -> dict[str, Any] | None:
    return next(
        (
            row
            for row in dialogue.get("utterances") or []
            if isinstance(row, dict) and str(row.get("id") or "") == utterance_id
        ),
        None,
    )


def frozen_artifacts(session: Path, input_profile: str) -> dict[str, Any]:
    audio = session / "derived/preprocess/audio"
    speaker_root = session / "derived/audit" / SPEAKER_REPORT_DIR_NAME
    synthesis = session / "derived/synthesis-simple/extractive"
    return {
        "raw_capture": BASE.raw_fingerprints(session),
        "input_profile": BASE.artifact_fingerprints(session, input_profile),
        "working_audio": {
            "mic_raw": artifact(audio / "mic_raw_for_asr.wav"),
            "mic_clean": artifact(audio / "mic_clean_local_fir.wav"),
            "mic_role_masked": artifact(audio / "mic_role_masked_for_asr.wav"),
            "remote": artifact(audio / "remote_for_aec.wav"),
        },
        "speaker_state": artifact(session / "derived/preprocess/echo/speaker_state.jsonl"),
        "speaker_evidence": artifact(speaker_root / "speaker_mode_evidence.jsonl"),
        "local_recall_items": artifact(session / "derived/audit/local-recall/local_recall_items.jsonl"),
        "order_items": artifact(session / "derived/audit/order/transcript_order_items.jsonl"),
        "evidence_notes": artifact(synthesis / f"evidence_notes.{input_profile}.json"),
    }


def protected_utterance_ids(sessions_root: Path, session_id: str) -> tuple[set[str], set[str]]:
    speaker_root = sessions_root / "_reports" / SPEAKER_REPORT_DIR_NAME
    local_ids: set[str] = set()
    order_ids: set[str] = set()
    for row in read_jsonl(speaker_root / "local_recall_risk_queue.jsonl"):
        if str(row.get("session_id") or "") == session_id:
            local_ids.update(str(value) for value in row.get("utterance_ids") or [] if value)
            local_ids.update(str(value) for value in row.get("me_utterance_ids") or [] if value)
    for row in read_jsonl(speaker_root / "chronology_risk_queue.jsonl"):
        if str(row.get("session_id") or "") == session_id:
            order_ids.update(str(value) for value in row.get("utterance_ids") or [] if value)
            order_ids.update(str(value) for value in row.get("me_utterance_ids") or [] if value)
    return local_ids, order_ids


def source_rows(sessions_root: Path) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    speaker_root = sessions_root / "_reports" / SPEAKER_REPORT_DIR_NAME
    manifest = read_json(speaker_root / "profile_baseline_manifest.json") or {}
    records = {
        str(row.get("session_id") or ""): row
        for row in manifest.get("sessions") or []
        if isinstance(row, dict)
    }
    merged = {
        (str(row.get("session_id") or ""), str(row.get("residual_queue_id") or "")): copy.deepcopy(row)
        for row in read_jsonl(speaker_root / "duplicate_risk_queue.jsonl")
    }
    for session_id, record in records.items():
        session = Path(str(record.get("session") or sessions_root / session_id))
        for evidence in read_jsonl(
            session / "derived/audit" / SPEAKER_REPORT_DIR_NAME / "speaker_mode_evidence.jsonl"
        ):
            decision = evidence.get("speaker_mode_decision")
            if not isinstance(decision, dict) or decision.get("outcome") != "remote_duplicate_or_leak":
                continue
            queue_id = str(evidence.get("residual_queue_id") or "")
            key = (session_id, queue_id)
            if key in merged:
                continue
            exact = ((evidence.get("intervals") or {}).get("exact") or {})
            whole = ((evidence.get("intervals") or {}).get("whole") or {})
            merged[key] = {
                "schema": "murmurmark.speaker_mode_risk/v1",
                "session_id": session_id,
                "input_profile": record.get("input_profile"),
                "residual_queue_id": queue_id,
                "source": "remote_duplicate",
                "source_audit_id": evidence.get("source_audit_id"),
                "interval": {
                    "start": safe_float(exact.get("start")),
                    "end": safe_float(exact.get("end")),
                    "duration_sec": safe_float(exact.get("duration_sec")),
                },
                "whole_utterance": {
                    "start": safe_float(whole.get("start")),
                    "end": safe_float(whole.get("end")),
                    "coverage_ratio": (
                        safe_float(exact.get("duration_sec"))
                        / max(0.001, safe_float(whole.get("duration_sec")))
                    ),
                },
                "me_utterance_ids": evidence.get("me_utterance_ids") or [],
                "remote_utterance_ids": evidence.get("remote_utterance_ids") or [],
                "utterance_ids": evidence.get("utterance_ids") or [],
                "target_text": evidence.get("target_text") or "",
                "remote_text": evidence.get("remote_text") or "",
                "source_detail": {
                    "classification": {
                        "label": "remote_duplicate",
                        "verdict": "probable_transcript_error",
                        "confidence": safe_float(decision.get("confidence")),
                    }
                },
            }
    return sorted(merged.values(), key=lambda row: (str(row.get("session_id")), interval(row))), records


def freeze_corpus(args: argparse.Namespace) -> int:
    sessions_root = args.sessions_root.expanduser().resolve()
    output = report_dir(sessions_root, args.out_dir)
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / "baseline_manifest.json"
    if manifest_path.exists() and not args.force:
        print(f"baseline_manifest: {manifest_path}")
        print("status: already_frozen")
        return 0
    rows, speaker_records = source_rows(sessions_root)
    speaker_root = sessions_root / "_reports" / SPEAKER_REPORT_DIR_NAME
    failures: list[str] = []
    queue: list[dict[str, Any]] = []
    session_records: dict[str, dict[str, Any]] = {}
    dialogue_cache: dict[tuple[str, str], dict[str, Any]] = {}
    for source in rows:
        session_id = str(source.get("session_id") or "")
        speaker_record = speaker_records.get(session_id) or {}
        input_profile = str(source.get("input_profile") or speaker_record.get("input_profile") or "")
        if not session_id or not input_profile:
            continue
        session = sessions_root / session_id
        paths = profile_paths(session, input_profile)
        cache_key = (session_id, input_profile)
        dialogue = dialogue_cache.setdefault(cache_key, read_json(paths["dialogue"]) or {})
        me_ids = [str(value) for value in source.get("me_utterance_ids") or [] if value]
        if len(me_ids) != 1:
            continue
        me = find_utterance(dialogue, me_ids[0])
        if not isinstance(me, dict) or role_name(me) != "Me":
            continue
        target_text = str(me.get("text") or source.get("target_text") or "").strip()
        remote_text = str(source.get("remote_text") or "").strip()
        plan = span_plan(target_text, remote_text)
        if plan is None:
            continue
        remote_start, remote_end = interval(source)
        me_start = safe_float(me.get("start"))
        me_end = safe_float(me.get("end"), me_start)
        if remote_end <= remote_start or remote_start < me_start - 0.1 or remote_end > me_end + 0.1:
            continue
        local_ids, order_ids = protected_utterance_ids(sessions_root, session_id)
        queue_id = f"mix_{fingerprint([session_id, source.get('residual_queue_id'), me_ids[0]])[:16]}"
        source_evidence_path = (
            session / "derived/audit" / SPEAKER_REPORT_DIR_NAME / "speaker_mode_evidence.jsonl"
        )
        queue.append(
            {
                "schema": "murmurmark.mixed_utterance_queue/v1",
                "queue_id": queue_id,
                "session_id": session_id,
                "input_profile": input_profile,
                "source_queue_id": source.get("residual_queue_id"),
                "source_audit_id": source.get("source_audit_id"),
                "source_label": ((source.get("source_detail") or {}).get("classification") or {}).get("label"),
                "interval": {
                    "start": round(remote_start, 3),
                    "end": round(remote_end, 3),
                    "duration_sec": round(remote_end - remote_start, 3),
                },
                "whole_utterance": {
                    "start": round(me_start, 3),
                    "end": round(me_end, 3),
                    "duration_sec": round(me_end - me_start, 3),
                },
                "me_utterance_id": me_ids[0],
                "remote_utterance_ids": source.get("remote_utterance_ids") or [],
                "target_text": target_text,
                "remote_text": remote_text,
                "span_plan": plan,
                "protection": {
                    "local_recall_debt": me_ids[0] in local_ids,
                    "transcript_order_debt": me_ids[0] in order_ids,
                    "protected_work_marker_in_remote_span": BASE.has_protected_marker(
                        plan["remote_span_text"], STRONGER
                    ),
                    "protected_work_marker_in_local_text": BASE.has_protected_marker(
                        f"{plan['local_prefix_text']} {plan['local_tail_text']}", STRONGER
                    ),
                },
                "source_provenance": {
                    "speaker_risk_queue": str(speaker_root / "duplicate_risk_queue.jsonl"),
                    "speaker_risk_queue_sha256": sha256_file(speaker_root / "duplicate_risk_queue.jsonl"),
                    "speaker_evidence": str(source_evidence_path),
                    "speaker_evidence_sha256": sha256_file(source_evidence_path),
                },
            }
        )
        if session_id not in session_records:
            session_records[session_id] = {
                "session_id": session_id,
                "session": str(session),
                "input_profile": input_profile,
                "artifacts": frozen_artifacts(session, input_profile),
            }
    queue.sort(key=lambda row: (str(row.get("session_id")), safe_float((row.get("interval") or {}).get("start"))))
    for session_id, record in session_records.items():
        artifacts = record.get("artifacts") or {}
        if not ((artifacts.get("input_profile") or {}).get("dialogue") or {}).get("sha256"):
            failures.append(f"{session_id}:missing_input_dialogue")
        if not artifacts.get("raw_capture") or any(
            not row.get("sha256") for row in artifacts.get("raw_capture") or []
        ):
            failures.append(f"{session_id}:missing_raw_capture_fingerprint")
        for name in ("mic_raw", "mic_clean", "remote"):
            if not ((artifacts.get("working_audio") or {}).get(name) or {}).get("sha256"):
                failures.append(f"{session_id}:missing_working_audio:{name}")
    if not queue:
        failures.append("mixed_utterance_queue_is_empty")
    records = [session_records[key] for key in sorted(session_records)]
    manifest = {
        "schema": "murmurmark.mixed_utterance_separation_baseline/v1",
        "generator": {"name": "mixed-utterance-span-separation", "version": SCRIPT_VERSION},
        "input_profile": "per_session_frozen",
        "output_profile": PROFILE,
        "source_profile": {
            "name": "speaker_mode_hardening_v1",
            "manifest": str(speaker_root / "profile_baseline_manifest.json"),
            "manifest_sha256": sha256_file(speaker_root / "profile_baseline_manifest.json"),
            "corpus_report": str(speaker_root / "speaker_mode_hardening_corpus_report.json"),
            "corpus_report_sha256": sha256_file(speaker_root / "speaker_mode_hardening_corpus_report.json"),
        },
        "queue": {
            "item_count": len(queue),
            "seconds": round(sum(duration(row) for row in queue), 3),
            "sha256": fingerprint(queue),
        },
        "preserved_debt": {
            "local_recall_items": len(read_jsonl(speaker_root / "local_recall_risk_queue.jsonl")),
            "local_recall_sha256": sha256_file(speaker_root / "local_recall_risk_queue.jsonl"),
            "transcript_order_items": len(read_jsonl(speaker_root / "chronology_risk_queue.jsonl")),
            "transcript_order_sha256": sha256_file(speaker_root / "chronology_risk_queue.jsonl"),
        },
        "sessions": records,
        "frozen_identity": fingerprint({"sessions": records, "queue": queue}),
        "gates": {"passed": not failures, "hard_failures": sorted(set(failures))},
    }
    write_jsonl(output / "mixed_utterance_queue.jsonl", queue)
    write_json(manifest_path, manifest)
    print(f"baseline_manifest: {manifest_path}")
    print(f"mixed_utterance_queue: {output / 'mixed_utterance_queue.jsonl'}")
    print(f"items: {len(queue)}")
    print(f"seconds: {manifest['queue']['seconds']}")
    print(f"status: {'frozen' if not failures else 'failed_open'}")
    return 0 if not failures else 2


def configure_audio_module(session: Path, input_profile: str) -> None:
    AUDIO.INPUT_PROFILE = input_profile
    AUDIO.audit_dir = lambda _session: session_audit_dir(_session)


def extract_clips(
    session: Path, queue_id: str, label: str, start: float, end: float
) -> tuple[dict[str, Path], list[str]]:
    result: dict[str, Path] = {}
    failures: list[str] = []
    for source, source_path in BASE.audio_sources(session).items():
        if source not in {"mic_raw", "mic_clean", "mic_role_masked", "remote"}:
            continue
        if not source_path.is_file():
            if source != "mic_role_masked":
                failures.append(f"{label}:{source}:missing")
            continue
        target = session_audit_dir(session) / "clips" / queue_id / f"{label}_{source}.wav"
        if BASE.extract_wav(source_path, target, start, end):
            result[source] = target
        else:
            failures.append(f"{label}:{source}:extract_failed")
    return result, failures


def prior_transcripts(session: Path, queue_id: str) -> dict[str, dict[str, Any]]:
    path = session / "derived/audit" / SPEAKER_REPORT_DIR_NAME / "speaker_mode_evidence.jsonl"
    row = next(
        (
            value
            for value in read_jsonl(path)
            if str(value.get("residual_queue_id") or "") == queue_id
        ),
        None,
    )
    return copy.deepcopy(row.get("micro_asr") or {}) if isinstance(row, dict) else {}


def transcript_words(run: dict[str, Any], absolute_start: float) -> list[dict[str, Any]]:
    words: list[dict[str, Any]] = []
    for segment in run.get("segments") or []:
        for value in segment.get("words") or []:
            raw = str(value.get("word") or value.get("text") or "").strip()
            normalized = normalize_token(raw)
            if not normalized:
                continue
            words.append(
                {
                    "text": raw,
                    "normalized": normalized,
                    "start": round(absolute_start + safe_float(value.get("start")), 3),
                    "end": round(absolute_start + safe_float(value.get("end")), 3),
                    "probability": round(safe_float(value.get("probability"), 1.0), 6),
                }
            )
    return words


def is_hallucination(text: str) -> bool:
    value = STRONGER.normalize_text(text)
    return not value or any(marker in value for marker in KNOWN_HALLUCINATIONS)


def transcript_support(run: dict[str, Any], expected: str) -> dict[str, Any]:
    text = str(run.get("text") or "").strip()
    metric = STRONGER.text_similarity(text, expected)
    return {
        "text": text,
        "valid": not is_hallucination(text),
        "similarity": round(safe_float(metric.get("similarity")), 6),
        "containment": round(safe_float(metric.get("containment")), 6),
        "supported": (
            not is_hallucination(text)
            and (
                safe_float(metric.get("similarity")) >= 0.55
                or safe_float(metric.get("containment")) >= 0.55
            )
        ),
    }


def state_features(state_rows: list[dict[str, Any]], start: float, end: float) -> dict[str, Any]:
    return BASE.target_module().interval_state_features(state_rows, start, end)


def voice_evidence(
    backend: Any | None,
    target_model: dict[str, Any],
    clips: dict[str, Path],
    keep_threshold: float,
    weak_threshold: float,
) -> dict[str, Any]:
    scores: dict[str, dict[str, Any]] = {}
    if backend is not None:
        for source, clip in clips.items():
            scores[source] = AUDIO.score_clip(backend, clip, target_model)
    mic_scores = [
        safe_float(value.get("positive_similarity"))
        for key, value in scores.items()
        if key.startswith("mic_")
    ]
    remote_score = safe_float((scores.get("remote") or {}).get("positive_similarity"))
    best_mic = max(mic_scores, default=0.0)
    return {
        "scores": scores,
        "best_mic": round(best_mic, 6),
        "remote": round(remote_score, 6),
        "delta": round(best_mic - remote_score, 6),
        "confirmed": bool(mic_scores) and best_mic >= keep_threshold and best_mic - remote_score >= 0.04,
        "weak": bool(mic_scores) and best_mic < weak_threshold,
    }


def island_evidence(
    label: str,
    expected: str,
    clips: dict[str, Path],
    transcripts: dict[str, dict[str, Any]],
    voice: dict[str, Any],
    state: dict[str, Any],
    item_duration: float,
) -> dict[str, Any]:
    mic_keys = [key for key in ("mic_clean", "mic_raw", "mic_role_masked") if key in clips]
    supports = {
        key: transcript_support(transcripts.get(f"{label}:{key}") or {}, expected)
        for key in mic_keys
    }
    supported_sources = [key for key, value in supports.items() if value["supported"]]
    mic_texts = [supports[key]["text"] for key in supported_sources if supports[key]["text"]]
    agreement = (
        STRONGER.text_similarity(mic_texts[0], mic_texts[1])
        if len(mic_texts) >= 2
        else {"similarity": 0.0, "containment": 0.0}
    )
    remote_support = transcript_support(transcripts.get(f"{label}:remote") or {}, expected)
    local_ratio = min(
        1.0,
        safe_float(state.get("local_only_ratio"))
        + 0.75 * safe_float(state.get("double_talk_ratio")),
    )
    short = item_duration <= 1.2
    min_expected_similarity = 0.65 if short else 0.55
    text_confirmed = (
        len(supported_sources) >= 2
        and max((supports[key]["similarity"] for key in supported_sources), default=0.0)
        >= min_expected_similarity
        and (
            safe_float(agreement.get("similarity")) >= 0.65
            or safe_float(agreement.get("containment")) >= 0.70
        )
    )
    remote_forbidden = (
        not remote_support["valid"]
        or (
            remote_support["similarity"] <= (0.35 if short else 0.55)
            and remote_support["containment"] <= (0.40 if short else 0.60)
        )
    )
    confirmed = (
        bool(expected.strip())
        and text_confirmed
        and voice.get("confirmed") is True
        and local_ratio >= (0.25 if short else 0.15)
        and remote_forbidden
    )
    return {
        "label": label,
        "expected_text": expected,
        "duration_sec": round(item_duration, 3),
        "mic_support": supports,
        "supported_mic_sources": supported_sources,
        "cross_mic_agreement": agreement,
        "remote_support": remote_support,
        "speaker_state": state,
        "voice": voice,
        "checks": {
            "text_confirmed": text_confirmed,
            "target_me_confirmed": voice.get("confirmed") is True,
            "local_state_confirmed": local_ratio >= (0.25 if short else 0.15),
            "remote_forbidden": remote_forbidden,
            "short_island": short,
        },
        "confirmed_local": confirmed,
    }


def classify_evidence(
    queue_row: dict[str, Any],
    remote_span: dict[str, Any],
    prefix: dict[str, Any] | None,
    tail: dict[str, Any] | None,
    span_voice: dict[str, Any],
    span_state: dict[str, Any],
) -> dict[str, Any]:
    protection = queue_row.get("protection") if isinstance(queue_row.get("protection"), dict) else {}
    source_label = str(queue_row.get("source_label") or "")
    remote_mic_support = remote_span.get("mic_support") or {}
    supported_mic = [key for key, value in remote_mic_support.items() if value.get("supported")]
    remote_track = remote_span.get("remote_support") or {}
    remote_confirmed = (
        len(supported_mic) >= 2
        and remote_track.get("supported") is True
        and safe_float(remote_span.get("cross_mic_similarity")) >= 0.68
        and safe_float(remote_span.get("authoritative_similarity")) >= 0.58
    )
    local_islands = [value for value in (prefix, tail) if isinstance(value, dict)]
    all_local_confirmed = bool(local_islands) and all(
        value.get("confirmed_local") is True for value in local_islands
    )
    span_local_support = min(
        1.0,
        safe_float(span_state.get("local_only_ratio"))
        + 0.75 * safe_float(span_state.get("double_talk_ratio")),
    )
    double_talk = (
        span_voice.get("confirmed") is True
        and safe_float(span_state.get("double_talk_ratio")) >= 0.20
        and safe_float(span_state.get("remote_active_ratio")) >= 0.20
    )
    protected_conflict = any(
        protection.get(key) is True
        for key in (
            "local_recall_debt",
            "transcript_order_debt",
            "protected_work_marker_in_remote_span",
        )
    )
    if double_talk:
        outcome = "confirmed_double_talk"
        action = "keep_unchanged"
        reason = "target_me_and_remote_activity_confirm_real_double_talk"
    elif remote_confirmed and all_local_confirmed and not protected_conflict:
        outcome = (
            "confirmed_remote_leak"
            if source_label == "remote_leak"
            else "confirmed_remote_duplicate"
        )
        if prefix is not None and tail is not None:
            action = "split_keep_both_local_islands"
        elif prefix is not None:
            action = "split_keep_local_prefix"
        elif tail is not None:
            action = "split_keep_local_tail"
        else:
            action = "split_remove_remote_span"
        reason = "remote_span_and_all_remaining_local_islands_have_independent_evidence"
    elif not remote_confirmed and all_local_confirmed and span_local_support >= 0.45:
        outcome = "confirmed_local"
        action = "keep_unchanged"
        reason = "remote_span_is_not_independently_confirmed_while_target_me_is_present"
    elif span_voice.get("weak") is True and not all_local_confirmed and remote_confirmed:
        outcome = "probable_asr_noise"
        action = "needs_review"
        reason = "remote_span_is_supported_but_local_islands_lack_safe_identity_evidence"
    else:
        outcome = "ambiguous"
        action = "needs_review"
        reason = (
            "protected_or_existing_debt_blocks_patch"
            if protected_conflict
            else "word_audio_state_or_target_me_evidence_conflicts"
        )
    return {
        "outcome": outcome,
        "action": action,
        "reason": reason,
        "confidence": round(
            max(
                safe_float(remote_span.get("authoritative_similarity")),
                safe_float(span_voice.get("best_mic")),
                max(
                    (
                        max(
                            safe_float(value.get("voice", {}).get("best_mic")),
                            max(
                                (
                                    safe_float(source.get("similarity"))
                                    for source in (value.get("mic_support") or {}).values()
                                ),
                                default=0.0,
                            ),
                        )
                        for value in local_islands
                    ),
                    default=0.0,
                ),
            ),
            6,
        ),
        "checks": {
            "remote_span_confirmed": remote_confirmed,
            "remote_span_supported_mic_sources": supported_mic,
            "all_local_islands_confirmed": all_local_confirmed,
            "local_island_count": len(local_islands),
            "double_talk_confirmed": double_talk,
            "protected_conflict": protected_conflict,
            "span_local_support": round(span_local_support, 6),
        },
    }


def build_session_evidence(
    session: Path,
    queue_rows: list[dict[str, Any]],
    record: dict[str, Any],
    model: Any | None,
    backend: Any | None,
    backend_status: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, Any]:
    input_profile = str(record.get("input_profile") or "current")
    configure_audio_module(session, input_profile)
    input_paths = profile_paths(session, input_profile)
    dialogue = read_json(input_paths["dialogue"]) or {}
    expected_artifacts = record.get("artifacts") or {}
    if fingerprint(frozen_artifacts(session, input_profile)) != fingerprint(expected_artifacts):
        return {
            "schema": "murmurmark.mixed_utterance_evidence_summary/v1",
            "session_id": session.name,
            "gates": {"passed": False, "hard_failures": ["frozen_input_artifacts_changed"]},
        }
    utterances = [row for row in dialogue.get("utterances") or [] if isinstance(row, dict)]
    state_rows = BASE.target_module().load_speaker_state(session)
    if backend is None:
        calibration = {
            "schema": "murmurmark.target_me_session_calibration/v1",
            "session_id": session.name,
            "status": "weak",
            "backend": backend_status,
            "thresholds": {"keep": 1.0, "weak": 1.0},
            "required_evidence_ready": False,
            "fail_open_reasons": ["target_me_backend_unavailable"],
        }
        target_model: dict[str, Any] = {}
    else:
        calibration, target_model = AUDIO.build_calibration(
            session, utterances, state_rows, backend, backend_status
        )
        calibration["required_evidence_ready"] = model is not None
        if model is None:
            calibration["status"] = "weak"
            calibration["fail_open_reasons"] = list(calibration.get("fail_open_reasons") or []) + [
                "faster_whisper_model_unavailable"
            ]
    write_json(session_audit_dir(session) / "target_me_session_calibration.json", calibration)
    keep_threshold = safe_float((calibration.get("thresholds") or {}).get("keep"), 1.0)
    weak_threshold = safe_float((calibration.get("thresholds") or {}).get("weak"), 1.0)
    results: list[dict[str, Any]] = []
    started = time.monotonic()
    for index, queue_row in enumerate(queue_rows, start=1):
        queue_id = str(queue_row.get("queue_id") or "")
        me = find_utterance(dialogue, str(queue_row.get("me_utterance_id") or ""))
        if not isinstance(me, dict):
            continue
        remote_start, remote_end = interval(queue_row)
        whole_start = safe_float(me.get("start"))
        whole_end = safe_float(me.get("end"), whole_start)
        specs: dict[str, tuple[float, float]] = {
            "whole": (whole_start, whole_end),
            "remote_span": (remote_start, remote_end),
        }
        plan = queue_row.get("span_plan") if isinstance(queue_row.get("span_plan"), dict) else {}
        if str(plan.get("local_prefix_text") or "").strip() and remote_start - whole_start >= 0.25:
            specs["local_prefix"] = (whole_start, remote_start)
        if str(plan.get("local_tail_text") or "").strip() and whole_end - remote_end >= 0.25:
            specs["local_tail"] = (remote_end, whole_end)
        clips: dict[str, dict[str, Any]] = {}
        clip_paths: dict[str, dict[str, Path]] = {}
        failures: list[str] = []
        transcripts: dict[str, dict[str, Any]] = {}
        prior = prior_transcripts(session, str(queue_row.get("source_queue_id") or ""))
        prior_by_sha: dict[str, dict[str, Any]] = {}
        for value in prior.values():
            if isinstance(value, dict):
                clip_sha = str((value.get("identity") or {}).get("clip_sha256") or "")
                if clip_sha:
                    prior_by_sha[clip_sha] = value
        voice_by_label: dict[str, dict[str, Any]] = {}
        state_by_label: dict[str, dict[str, Any]] = {}
        for label, (start, end) in specs.items():
            paths, item_failures = extract_clips(session, queue_id, label, start, end)
            failures.extend(item_failures)
            clip_paths[label] = paths
            clips[label] = {
                source: {"path": str(path), "sha256": sha256_file(path)}
                for source, path in paths.items()
            }
            voice_by_label[label] = voice_evidence(
                backend, target_model, paths, keep_threshold, weak_threshold
            )
            state_by_label[label] = state_features(state_rows, start, end)
            for source, path in paths.items():
                key = f"{label}:{source}"
                clip_sha = sha256_file(path) or ""
                if clip_sha in prior_by_sha:
                    run = copy.deepcopy(prior_by_sha[clip_sha])
                    run["cache_source"] = "speaker_mode_evidence"
                else:
                    run = AUDIO.transcribe_clip_with_words(session, model, path, args)
                run["absolute_words"] = transcript_words(run, start)
                transcripts[key] = run
        expected_remote = str(queue_row.get("remote_text") or plan.get("remote_span_text") or "")
        remote_supports = {
            source: transcript_support(transcripts.get(f"remote_span:{source}") or {}, expected_remote)
            for source in ("mic_clean", "mic_raw", "mic_role_masked")
            if f"remote_span:{source}" in transcripts
        }
        mic_texts = [
            value["text"]
            for value in remote_supports.values()
            if value["valid"] and value["text"]
        ]
        cross_mic = (
            STRONGER.text_similarity(mic_texts[0], mic_texts[1])
            if len(mic_texts) >= 2
            else {"similarity": 0.0, "containment": 0.0}
        )
        remote_track_support = transcript_support(
            transcripts.get("remote_span:remote") or {}, expected_remote
        )
        authoritative = STRONGER.text_similarity(
            str(plan.get("remote_span_text") or ""), expected_remote
        )
        remote_span_evidence = {
            "expected_text": expected_remote,
            "target_span_text": plan.get("remote_span_text"),
            "mic_support": remote_supports,
            "remote_support": remote_track_support,
            "cross_mic": cross_mic,
            "cross_mic_similarity": max(
                safe_float(cross_mic.get("similarity")),
                safe_float(cross_mic.get("containment")),
            ),
            "authoritative_similarity": max(
                safe_float(authoritative.get("similarity")),
                safe_float(authoritative.get("containment")),
            ),
            "speaker_state": state_by_label.get("remote_span") or {},
            "voice": voice_by_label.get("remote_span") or {},
        }
        local_evidence: dict[str, dict[str, Any] | None] = {
            "prefix": None,
            "tail": None,
        }
        if "local_prefix" in specs:
            start, end = specs["local_prefix"]
            local_evidence["prefix"] = island_evidence(
                "local_prefix",
                str(plan.get("local_prefix_text") or ""),
                clip_paths["local_prefix"],
                transcripts,
                voice_by_label["local_prefix"],
                state_by_label["local_prefix"],
                end - start,
            )
        if "local_tail" in specs:
            start, end = specs["local_tail"]
            local_evidence["tail"] = island_evidence(
                "local_tail",
                str(plan.get("local_tail_text") or ""),
                clip_paths["local_tail"],
                transcripts,
                voice_by_label["local_tail"],
                state_by_label["local_tail"],
                end - start,
            )
        decision = classify_evidence(
            queue_row,
            remote_span_evidence,
            local_evidence["prefix"],
            local_evidence["tail"],
            voice_by_label.get("remote_span") or {},
            state_by_label.get("remote_span") or {},
        )
        result = {
            "schema": "murmurmark.mixed_utterance_evidence/v1",
            "session_id": session.name,
            "queue_id": queue_id,
            "source_queue_id": queue_row.get("source_queue_id"),
            "input_profile": input_profile,
            "me_utterance_id": queue_row.get("me_utterance_id"),
            "remote_utterance_ids": queue_row.get("remote_utterance_ids") or [],
            "target_text": queue_row.get("target_text"),
            "remote_text": queue_row.get("remote_text"),
            "span_plan": plan,
            "intervals": {
                label: {
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "duration_sec": round(end - start, 3),
                }
                for label, (start, end) in specs.items()
            },
            "calibration": calibration,
            "clips": clips,
            "clip_failures": failures,
            "voice": voice_by_label,
            "speaker_state": state_by_label,
            "micro_asr": transcripts,
            "remote_span_evidence": remote_span_evidence,
            "local_island_evidence": local_evidence,
            "protection": queue_row.get("protection") or {},
            "decision": decision,
        }
        result["provenance_sha256"] = fingerprint(result)
        results.append(result)
        print(
            f"evidence: {session.name}: {index}/{len(queue_rows)}: "
            f"{decision['outcome']}:{decision['action']}",
            flush=True,
        )
    write_jsonl(session_audit_dir(session) / "mixed_utterance_evidence.jsonl", results)
    failures: list[str] = []
    if len(results) != len(queue_rows):
        failures.append("not_all_frozen_rows_have_evidence")
    if any(
        (row.get("decision") or {}).get("outcome") not in VALID_OUTCOMES
        or (row.get("decision") or {}).get("action") not in VALID_ACTIONS
        for row in results
    ):
        failures.append("invalid_evidence_disposition")
    summary = {
        "schema": "murmurmark.mixed_utterance_evidence_summary/v1",
        "generator": {"name": "mixed-utterance-span-separation", "version": SCRIPT_VERSION},
        "session_id": session.name,
        "input_profile": input_profile,
        "queue_items": len(queue_rows),
        "queue_seconds": round(sum(duration(row) for row in queue_rows), 3),
        "runtime_sec": round(time.monotonic() - started, 3),
        "by_outcome": dict(
            sorted(Counter(str((row.get("decision") or {}).get("outcome")) for row in results).items())
        ),
        "by_action": dict(
            sorted(Counter(str((row.get("decision") or {}).get("action")) for row in results).items())
        ),
        "calibration": calibration,
        "gates": {"passed": not failures, "hard_failures": failures},
    }
    write_json(session_audit_dir(session) / "mixed_utterance_evidence_summary.json", summary)
    return summary


def build_evidence(args: argparse.Namespace) -> int:
    sessions_root = args.sessions_root.expanduser().resolve()
    output = report_dir(sessions_root, args.out_dir)
    manifest = read_json(output / "baseline_manifest.json")
    queue = read_jsonl(output / "mixed_utterance_queue.jsonl")
    if not manifest or (manifest.get("gates") or {}).get("passed") is not True or not queue:
        print("status: missing_or_failed_frozen_baseline", file=sys.stderr)
        return 2
    if not args.sessions:
        for session_id in sorted({str(row.get("session_id") or "") for row in queue}):
            command = [
                sys.executable,
                str(Path(__file__).resolve()),
                "evidence",
                str(sessions_root / session_id),
                "--sessions-root",
                str(sessions_root),
                "--device",
                args.device,
                "--compute-type",
                args.compute_type,
                "--language",
                args.language,
                "--beam-size",
                str(args.beam_size),
            ]
            if args.out_dir:
                command.extend(["--out-dir", str(args.out_dir)])
            if args.model:
                command.extend(["--model", str(args.model)])
            if args.allow_download:
                command.append("--allow-download")
            if args.no_cache:
                command.append("--no-cache")
            print(f"evidence: isolated session: {session_id}", flush=True)
            result = subprocess.run(command, check=False)
            if result.returncode != 0:
                return result.returncode
        return 0
    resolved_model = AUDIO.model_path(args)
    model_bin = resolved_model / "model.bin" if resolved_model.is_dir() else resolved_model
    model_available = model_bin.exists()
    if not args.allow_download:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
    model = AUDIO.LazyWhisperModel(resolved_model, args) if model_available else None
    if model is None:
        print(f"warning: faster_whisper_model_missing:{resolved_model}; fail-open", file=sys.stderr)
    backend, backend_status = BASE.target_module().resolve_embedding_backend(
        SimpleNamespace(method="resemblyzer_dvector", wavlm_model=None)
    )
    ready, reason = backend.ready()
    if not ready:
        backend = None
        backend_status = {**backend_status, "ready": False, "reason": reason}
        print(f"warning: target_me_backend_unavailable:{reason}; fail-open", file=sys.stderr)
    wanted = {path.expanduser().resolve().name for path in args.sessions}
    status = 0
    for record in manifest.get("sessions") or []:
        if not isinstance(record, dict):
            continue
        session_id = str(record.get("session_id") or "")
        if wanted and session_id not in wanted:
            continue
        rows = [row for row in queue if str(row.get("session_id") or "") == session_id]
        if not rows:
            continue
        summary = build_session_evidence(
            sessions_root / session_id,
            rows,
            record,
            model,
            backend,
            backend_status,
            args,
        )
        if (summary.get("gates") or {}).get("passed") is not True:
            status = 2
    return status


def trim_text_piece(value: str) -> str:
    return value.strip(" \t\n,.;:-")


def local_row(
    original: dict[str, Any],
    *,
    text: str,
    start: float,
    end: float,
    row_id: str,
    queue_id: str,
    action: str,
    part: str,
    evidence_sha: str,
) -> dict[str, Any]:
    row = copy.deepcopy(original)
    row["id"] = row_id
    row["text"] = trim_text_piece(text)
    row["corrected_text"] = row["text"]
    row["start"] = round(start, 3)
    row["end"] = round(max(start, end), 3)
    row["source_start"] = row["start"]
    row["source_end"] = row["end"]
    quality = copy.deepcopy(row.get("quality")) if isinstance(row.get("quality"), dict) else {}
    quality["needs_review"] = False
    quality["mixed_utterance_separation"] = {
        "profile": PROFILE,
        "queue_id": queue_id,
        "action": action,
        "part": part,
        "original_utterance_id": original.get("id"),
        "evidence_sha256": evidence_sha,
    }
    row["quality"] = quality
    return row


def make_patch_rows(
    original: dict[str, Any], queue_row: dict[str, Any], evidence: dict[str, Any]
) -> list[dict[str, Any]]:
    plan = queue_row.get("span_plan") or {}
    action = str((evidence.get("decision") or {}).get("action") or "")
    queue_id = str(queue_row.get("queue_id") or "")
    evidence_sha = str(evidence.get("provenance_sha256") or "")
    start = safe_float(original.get("start"))
    end = safe_float(original.get("end"), start)
    remote_start, remote_end = interval(queue_row)
    prefix = trim_text_piece(str(plan.get("local_prefix_text") or ""))
    tail = trim_text_piece(str(plan.get("local_tail_text") or ""))
    rows: list[dict[str, Any]] = []
    original_id = str(original.get("id") or "")
    if prefix:
        rows.append(
            local_row(
                original,
                text=prefix,
                start=start,
                end=min(end, remote_start),
                row_id=original_id,
                queue_id=queue_id,
                action=action,
                part="prefix",
                evidence_sha=evidence_sha,
            )
        )
    if tail:
        tail_id = (
            f"{original_id}__mixed_post_{fingerprint([queue_id, tail])[:8]}"
            if prefix
            else original_id
        )
        rows.append(
            local_row(
                original,
                text=tail,
                start=max(start, remote_end),
                end=end,
                row_id=tail_id,
                queue_id=queue_id,
                action=action,
                part="tail",
                evidence_sha=evidence_sha,
            )
        )
    return rows


def write_profile_outputs(
    session: Path,
    input_profile: str,
    input_dialogue: dict[str, Any],
    input_quality: dict[str, Any],
    utterances: list[dict[str, Any]],
    summary: dict[str, Any],
) -> None:
    paths = profile_paths(session, PROFILE)
    utterances.sort(key=lambda row: (safe_float(row.get("start")), safe_float(row.get("end")), str(row.get("id"))))
    overlaps = ORDER.build_overlaps(utterances)
    quality = ORDER.quality_report(input_quality, utterances, overlaps, summary)
    quality["profile"] = PROFILE
    quality["mixed_utterance_separation"] = summary
    quality["remote_duplicate_in_me_seconds"] = round(
        max(
            0.0,
            safe_float(input_quality.get("remote_duplicate_in_me_seconds"))
            - safe_float(summary.get("removed_remote_span_seconds")),
        ),
        3,
    )
    quality["remote_duplicate_in_me_count"] = max(
        0,
        safe_int(input_quality.get("remote_duplicate_in_me_count"))
        - safe_int(summary.get("applied_items")),
    )
    write_json(
        paths["dialogue"],
        {
            "schema": "murmurmark.clean_dialogue/v1",
            "session": input_dialogue.get("session", session.name),
            "utterances": utterances,
        },
    )
    write_json(paths["quality"], quality)
    write_json(
        paths["overlaps"],
        {
            "schema": "murmurmark.transcript_overlaps/v1",
            "session": session.name,
            "overlaps": overlaps,
        },
    )
    write_json(
        paths["transcript_json"],
        {
            "schema": "murmurmark.transcript_simple/v1",
            "session": input_dialogue.get("session", session.name),
            "backend": "whisper.cpp",
            "utterances": [
                {
                    **copy.deepcopy(row),
                    "raw_text": row.get("text"),
                    "corrected_text": row.get("text"),
                    "corrections": [],
                }
                for row in utterances
            ],
        },
    )
    report = read_json(profile_paths(session, input_profile)["dialogue"].parent / "transcribe_simple_report.json") or {}
    ORDER.write_markdown(paths["transcript"], utterances, report.get("model"), report.get("language"))


def output_fingerprint(session: Path) -> str:
    paths = profile_paths(session, PROFILE)
    return fingerprint(
        {
            name: sha256_file(path)
            for name, path in paths.items()
            if name in {"dialogue", "quality", "overlaps", "transcript", "transcript_json"}
        }
    )


def apply_session(args: argparse.Namespace) -> int:
    sessions_root = args.sessions_root.expanduser().resolve()
    output = report_dir(sessions_root, args.out_dir)
    manifest = read_json(output / "baseline_manifest.json") or {}
    queue = read_jsonl(output / "mixed_utterance_queue.jsonl")
    session = args.session.expanduser().resolve()
    record = next(
        (
            row
            for row in manifest.get("sessions") or []
            if isinstance(row, dict) and row.get("session_id") == session.name
        ),
        None,
    )
    failures: list[str] = []
    if not isinstance(record, dict):
        print(f"status: session_not_in_frozen_scope:{session.name}", file=sys.stderr)
        return 2
    input_profile = str(record.get("input_profile") or "current")
    input_paths = profile_paths(session, input_profile)
    dialogue = read_json(input_paths["dialogue"])
    quality = read_json(input_paths["quality"])
    session_queue = [row for row in queue if row.get("session_id") == session.name]
    evidence_rows = read_jsonl(session_audit_dir(session) / "mixed_utterance_evidence.jsonl")
    evidence_by_id = {str(row.get("queue_id") or ""): row for row in evidence_rows}
    if not dialogue or not quality:
        failures.append("missing_input_profile")
    if fingerprint(frozen_artifacts(session, input_profile)) != fingerprint(record.get("artifacts") or {}):
        failures.append("frozen_input_artifacts_changed")
    if len(evidence_by_id) != len(session_queue):
        failures.append("not_all_frozen_rows_have_evidence")
    if failures:
        write_json(
            session_profile_dir(session) / "mixed_utterance_profile_report.json",
            {
                "schema": "murmurmark.mixed_utterance_profile_report/v1",
                "session_id": session.name,
                "status": "failed_open",
                "gates": {"passed": False, "hard_failures": failures},
            },
        )
        return 2
    assert dialogue is not None and quality is not None
    utterances = [copy.deepcopy(row) for row in dialogue.get("utterances") or [] if isinstance(row, dict)]
    input_by_id = {str(row.get("id") or ""): row for row in utterances if row.get("id")}
    replacements: dict[str, list[dict[str, Any]]] = {}
    applied: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    dispositions: list[dict[str, Any]] = []
    for queue_row in session_queue:
        queue_id = str(queue_row.get("queue_id") or "")
        evidence = evidence_by_id.get(queue_id) or {}
        decision = evidence.get("decision") if isinstance(evidence.get("decision"), dict) else {}
        action = str(decision.get("action") or "needs_review")
        utterance_id = str(queue_row.get("me_utterance_id") or "")
        original = input_by_id.get(utterance_id)
        closed = False
        reason = str(decision.get("reason") or "missing_decision")
        patch_rows: list[dict[str, Any]] = []
        if action.startswith("split_") and isinstance(original, dict):
            patch_rows = make_patch_rows(original, queue_row, evidence)
            if patch_rows and all(str(row.get("text") or "").strip() for row in patch_rows):
                removed_text = str((queue_row.get("span_plan") or {}).get("remote_span_text") or "")
                output_local_text = " ".join(str(row.get("text") or "") for row in patch_rows)
                expected_local_text = " ".join(
                    value
                    for value in (
                        str((queue_row.get("span_plan") or {}).get("local_prefix_text") or "").strip(),
                        str((queue_row.get("span_plan") or {}).get("local_tail_text") or "").strip(),
                    )
                    if value
                )
                local_metric = STRONGER.text_similarity(output_local_text, expected_local_text)
                if (
                    safe_float(local_metric.get("containment")) >= 0.99
                    and not (
                        BASE.has_protected_marker(removed_text, STRONGER)
                        and (queue_row.get("protection") or {}).get("protected_work_marker_in_remote_span")
                    )
                ):
                    replacements[utterance_id] = patch_rows
                    closed = True
                    applied.append(
                        {
                            "schema": "murmurmark.mixed_utterance_patch/v1",
                            "session_id": session.name,
                            "queue_id": queue_id,
                            "utterance_id": utterance_id,
                            "action": action,
                            "removed_text": removed_text,
                            "kept_texts": [row.get("text") for row in patch_rows],
                            "interval": queue_row.get("interval"),
                            "evidence_sha256": evidence.get("provenance_sha256"),
                        }
                    )
                else:
                    reason = "patch_did_not_preserve_exact_local_text_or_protected_span"
            else:
                reason = "patch_produced_no_local_rows"
        elif action == "keep_unchanged":
            closed = decision.get("outcome") in {"confirmed_local", "confirmed_double_talk"}
        disposition = {
            "schema": "murmurmark.mixed_utterance_disposition/v1",
            "session_id": session.name,
            "queue_id": queue_id,
            "source_queue_id": queue_row.get("source_queue_id"),
            "utterance_id": utterance_id,
            "interval": queue_row.get("interval"),
            "outcome": decision.get("outcome") if closed or action == "needs_review" else "ambiguous",
            "action": action if closed else "needs_review",
            "reason": reason,
            "confidence": decision.get("confidence"),
            "closed": closed,
            "evidence_sha256": evidence.get("provenance_sha256"),
        }
        dispositions.append(disposition)
        if not closed:
            rejected.append(
                {
                    **disposition,
                    "target_text": queue_row.get("target_text"),
                    "remote_text": queue_row.get("remote_text"),
                    "span_plan": queue_row.get("span_plan"),
                    "checks": decision.get("checks") or {},
                }
            )
    output_utterances: list[dict[str, Any]] = []
    for row in utterances:
        row_id = str(row.get("id") or "")
        output_utterances.extend(replacements.get(row_id, [row]))
    input_remote = [row for row in utterances if role_name(row) == "Colleagues"]
    output_remote = [row for row in output_utterances if role_name(row) == "Colleagues"]
    allowed_changed = set(replacements)
    output_ids = {str(row.get("id") or "") for row in output_utterances}
    for row_id, original in input_by_id.items():
        if row_id in allowed_changed or role_name(original) == "Colleagues":
            continue
        if row_id not in output_ids or next(
            (row for row in output_utterances if str(row.get("id") or "") == row_id),
            None,
        ) != original:
            failures.append(f"unrelated_utterance_changed:{row_id}")
    if input_remote != output_remote:
        failures.append("remote_utterances_changed")
    if any(
        row.get("outcome") not in VALID_OUTCOMES or row.get("action") not in VALID_ACTIONS
        for row in dispositions
    ):
        failures.append("invalid_disposition")
    removed_seconds = sum(
        duration(queue_row)
        for queue_row in session_queue
        if str(queue_row.get("queue_id") or "") in {str(row.get("queue_id") or "") for row in applied}
    )
    summary = {
        "queue_items": len(session_queue),
        "queue_seconds": round(sum(duration(row) for row in session_queue), 3),
        "applied_items": len(applied),
        "removed_remote_span_seconds": round(removed_seconds, 3),
        "closed_items": sum(1 for row in dispositions if row.get("closed") is True),
        "closed_seconds": round(
            sum(
                duration(queue_row)
                for queue_row in session_queue
                if next(
                    (
                        row
                        for row in dispositions
                        if row.get("queue_id") == queue_row.get("queue_id")
                    ),
                    {},
                ).get("closed")
                is True
            ),
            3,
        ),
        "remaining_items": sum(1 for row in dispositions if row.get("closed") is not True),
        "remaining_seconds": round(
            sum(
                duration(queue_row)
                for queue_row in session_queue
                if next(
                    (
                        row
                        for row in dispositions
                        if row.get("queue_id") == queue_row.get("queue_id")
                    ),
                    {},
                ).get("closed")
                is not True
            ),
            3,
        ),
        "by_outcome": dict(sorted(Counter(str(row.get("outcome")) for row in dispositions).items())),
        "by_action": dict(sorted(Counter(str(row.get("action")) for row in dispositions).items())),
    }
    gates = {"passed": not failures, "hard_failures": sorted(set(failures))}
    if args.mode == "conservative" and gates["passed"]:
        write_profile_outputs(
            session,
            input_profile,
            dialogue,
            quality,
            output_utterances,
            summary,
        )
    output_dir = session_profile_dir(session)
    write_jsonl(output_dir / "mixed_utterance_dispositions.jsonl", dispositions)
    write_jsonl(output_dir / "mixed_utterance_applied.jsonl", applied)
    write_jsonl(output_dir / "mixed_utterance_rejected.jsonl", rejected)
    report = {
        "schema": "murmurmark.mixed_utterance_profile_report/v1",
        "generator": {"name": "mixed-utterance-span-separation", "version": SCRIPT_VERSION},
        "session_id": session.name,
        "input_profile": input_profile,
        "output_profile": PROFILE,
        "status": "ok" if gates["passed"] else "failed_open",
        "summary": summary,
        "gates": gates,
        "evidence_sha256": sha256_file(
            session_audit_dir(session) / "mixed_utterance_evidence.jsonl"
        ),
    }
    if args.mode == "conservative" and gates["passed"]:
        report["output_fingerprint"] = output_fingerprint(session)
    write_json(output_dir / "mixed_utterance_profile_report.json", report)
    print(
        f"profile: {session.name}: applied={summary['applied_items']} "
        f"closed={summary['closed_items']} remaining={summary['remaining_items']}"
    )
    return 0 if gates["passed"] else 2


def process_runtime(session: Path) -> float:
    payload = read_json(session / "derived/pipeline-run/pipeline_run_report.json") or {}
    return sum(
        safe_float(row.get("duration_sec"))
        for row in payload.get("steps") or []
        if isinstance(row, dict) and row.get("status") == "passed"
    )


def order_summary(dialogue: dict[str, Any]) -> dict[str, Any]:
    utterances = [row for row in dialogue.get("utterances") or [] if isinstance(row, dict)]
    overlaps = ORDER.build_overlaps(utterances)
    args = SimpleNamespace(min_overlap_sec=0.5, long_me_sec=6.0, tail_sec=0.8)
    return ORDER_AUDIT.summarize(ORDER_AUDIT.build_items(utterances, overlaps, args))


def evidence_ids(payload: dict[str, Any]) -> set[str]:
    result: set[str] = set()

    def visit(value: Any, key: str = "") -> None:
        if isinstance(value, dict):
            for child_key, child in value.items():
                visit(child, str(child_key))
        elif isinstance(value, list):
            if key in {"utterance_ids", "evidence_utterance_ids"}:
                result.update(str(item) for item in value if isinstance(item, str))
            else:
                for child in value:
                    visit(child, key)
        elif key in {"utterance_id", "evidence_utterance_id"} and isinstance(value, str):
            result.add(value)

    visit(payload.get("selected") or {})
    return result


def synthesize(session: Path) -> int:
    synthesis_dir = session / "derived/synthesis-simple/extractive"
    generic_outputs = (
        "synthesis_manifest.json",
        "quality_verdict.json",
        "quality_verdict.md",
        "notes.md",
        "evidence_notes.json",
        "review_items.jsonl",
    )
    snapshots = {
        name: (synthesis_dir / name).read_bytes()
        if (synthesis_dir / name).exists()
        else None
        for name in generic_outputs
    }
    environment = os.environ.copy()
    environment["MURMURMARK_MIXED_UTTERANCE_CANDIDATE"] = "1"
    try:
        return subprocess.run(
            [
                sys.executable,
                str(Path(__file__).with_name("synthesize-simple-extractive.py")),
                str(session),
                "--transcript-profile",
                PROFILE,
            ],
            env=environment,
            check=False,
        ).returncode
    finally:
        for name, content in snapshots.items():
            path = synthesis_dir / name
            if content is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(content)


def write_corpus_markdown(path: Path, report: dict[str, Any]) -> None:
    summary = report.get("summary") or {}
    evidence_limit = report.get("evidence_limit") or {}
    lines = [
        "# Mixed-Utterance Remote Span Separation v1",
        "",
        f"Decision: `{report.get('decision')}`",
        "",
        "## Result",
        "",
        f"- Frozen rows: `{summary.get('queue_items')}` / `{summary.get('queue_seconds')}s`",
        f"- Closed rows: `{summary.get('closed_items')}` / `{summary.get('closed_seconds')}s`",
        f"- Applied splits: `{summary.get('applied_items')}` / `{summary.get('removed_remote_span_seconds')}s`",
        f"- Duplicate/leak reduction: `{summary.get('duplicate_reduction_ratio')}`",
        f"- Mandatory review reduction: `{summary.get('review_reduction_ratio')}`",
        f"- Additional runtime ratio: `{summary.get('additional_runtime_ratio')}`",
        "",
        "## Evidence Ceiling",
        "",
        str(evidence_limit.get("interpretation") or ""),
        "",
        f"- Outcomes: `{json.dumps(evidence_limit.get('outcomes') or {}, ensure_ascii=False, sort_keys=True)}`",
        f"- Actions: `{json.dumps(evidence_limit.get('actions') or {}, ensure_ascii=False, sort_keys=True)}`",
        "",
        "## Gates",
        "",
    ]
    failures = (report.get("gates") or {}).get("hard_failures") or []
    lines.extend([f"- `{value}`" for value in failures] or ["- all gates passed"])
    lines.extend(["", "## Largest Unresolved Examples", ""])
    for row in (report.get("audit") or {}).get("unresolved_examples") or []:
        lines.append(
            f"- `{row.get('session_id')}` `{row.get('start'):.3f}..{row.get('end'):.3f}` "
            f"`{row.get('outcome')}`: {row.get('reason')}; "
            f"Me: {row.get('target_text')!r}; remote span: {row.get('remote_span_text')!r}"
        )
    if not (report.get("audit") or {}).get("unresolved_examples"):
        lines.append("- none")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def evaluate_corpus(args: argparse.Namespace) -> int:
    sessions_root = args.sessions_root.expanduser().resolve()
    output = report_dir(sessions_root, args.out_dir)
    manifest = read_json(output / "baseline_manifest.json")
    queue = read_jsonl(output / "mixed_utterance_queue.jsonl")
    if not manifest or (manifest.get("gates") or {}).get("passed") is not True:
        print("status: missing_or_failed_frozen_baseline", file=sys.stderr)
        return 2
    records = [row for row in manifest.get("sessions") or [] if isinstance(row, dict)]
    if args.apply:
        for record in records:
            session = sessions_root / str(record.get("session_id") or "")
            code = apply_session(
                SimpleNamespace(
                    sessions_root=sessions_root,
                    out_dir=args.out_dir,
                    session=session,
                    mode="conservative",
                )
            )
            if code != 0:
                print(f"warning: apply failed open:{session.name}", file=sys.stderr)
    hard_failures: list[str] = []
    sessions: list[dict[str, Any]] = []
    closed_items = 0
    closed_seconds = 0.0
    applied_items = 0
    removed_seconds = 0.0
    evidence_runtime = 0.0
    baseline_runtime = 0.0
    outcomes: Counter[str] = Counter()
    actions: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    unresolved_examples: list[dict[str, Any]] = []
    queue_by_id = {
        str(row.get("queue_id") or ""): row
        for row in queue
        if isinstance(row, dict) and row.get("queue_id")
    }
    for record in records:
        session_id = str(record.get("session_id") or "")
        session = sessions_root / session_id
        input_profile = str(record.get("input_profile") or "current")
        input_paths = profile_paths(session, input_profile)
        candidate_paths = profile_paths(session, PROFILE)
        profile_report = read_json(
            session_profile_dir(session) / "mixed_utterance_profile_report.json"
        )
        evidence_summary = read_json(
            session_audit_dir(session) / "mixed_utterance_evidence_summary.json"
        ) or {}
        if not profile_report or (profile_report.get("gates") or {}).get("passed") is not True:
            hard_failures.append(f"{session_id}:profile_gates_failed")
            continue
        if profile_report.get("output_fingerprint") != output_fingerprint(session):
            hard_failures.append(f"{session_id}:output_fingerprint_mismatch")
        if fingerprint(frozen_artifacts(session, input_profile)) != fingerprint(
            record.get("artifacts") or {}
        ):
            hard_failures.append(f"{session_id}:frozen_inputs_changed")
        input_dialogue = read_json(input_paths["dialogue"]) or {}
        output_dialogue = read_json(candidate_paths["dialogue"]) or {}
        input_quality = read_json(input_paths["quality"]) or {}
        output_quality = read_json(candidate_paths["quality"]) or {}
        input_remote = [
            row
            for row in input_dialogue.get("utterances") or []
            if isinstance(row, dict) and role_name(row) == "Colleagues"
        ]
        output_remote = [
            row
            for row in output_dialogue.get("utterances") or []
            if isinstance(row, dict) and role_name(row) == "Colleagues"
        ]
        if input_remote != output_remote:
            hard_failures.append(f"{session_id}:remote_content_changed")
        before_recall = safe_float(input_quality.get("local_only_island_recall"))
        after_recall = safe_float(output_quality.get("local_only_island_recall"))
        if after_recall + 1e-6 < before_recall:
            hard_failures.append(f"{session_id}:local_recall_regressed")
        before_order = order_summary(input_dialogue)
        after_order = order_summary(output_dialogue)
        if safe_int(after_order.get("probable_order_risk_count")) > safe_int(
            before_order.get("probable_order_risk_count")
        ):
            hard_failures.append(f"{session_id}:chronology_count_regressed")
        if safe_float(after_order.get("probable_order_risk_seconds")) > safe_float(
            before_order.get("probable_order_risk_seconds")
        ) + 1e-6:
            hard_failures.append(f"{session_id}:chronology_seconds_regressed")
        summary = profile_report.get("summary") or {}
        closed_items += safe_int(summary.get("closed_items"))
        closed_seconds += safe_float(summary.get("closed_seconds"))
        applied_items += safe_int(summary.get("applied_items"))
        removed_seconds += safe_float(summary.get("removed_remote_span_seconds"))
        evidence_runtime += safe_float(evidence_summary.get("runtime_sec"))
        baseline_runtime += process_runtime(session)
        for row in read_jsonl(session_profile_dir(session) / "mixed_utterance_dispositions.jsonl"):
            outcomes[str(row.get("outcome") or "unknown")] += 1
            actions[str(row.get("action") or "unknown")] += 1
            reasons[str(row.get("reason") or "unknown")] += 1
            if row.get("closed") is not True:
                queue_row = queue_by_id.get(str(row.get("queue_id") or "")) or {}
                row_start, row_end = interval(queue_row)
                unresolved_examples.append(
                    {
                        "session_id": session_id,
                        "queue_id": row.get("queue_id"),
                        "start": round(row_start, 3),
                        "end": round(row_end, 3),
                        "duration_sec": round(max(0.0, row_end - row_start), 3),
                        "outcome": row.get("outcome"),
                        "reason": row.get("reason"),
                        "target_text": queue_row.get("target_text"),
                        "remote_span_text": (
                            queue_row.get("span_plan") or {}
                        ).get("remote_span_text"),
                    }
                )
        if args.synthesize:
            synthesis_dir = session / "derived/synthesis-simple/extractive"
            baseline_verdict = read_json(
                synthesis_dir / f"quality_verdict.{input_profile}.json"
            ) or {}
            if synthesize(session) != 0:
                hard_failures.append(f"{session_id}:candidate_synthesis_failed")
            notes = read_json(
                synthesis_dir / f"evidence_notes.{PROFILE}.json"
            ) or {}
            output_ids = {
                str(row.get("id") or "")
                for row in output_dialogue.get("utterances") or []
                if isinstance(row, dict)
            }
            missing_note_ids = sorted(evidence_ids(notes) - output_ids)
            if missing_note_ids:
                hard_failures.append(
                    f"{session_id}:notes_reference_missing_utterances:{','.join(missing_note_ids)}"
                )
            verdict = read_json(
                synthesis_dir / f"quality_verdict.{PROFILE}.json"
            ) or {}
            verdict_rank = {"good": 0, "usable_with_review": 1, "risky": 2, "failed": 3}
            if verdict_rank.get(str(verdict.get("verdict")), 3) > verdict_rank.get(
                str(baseline_verdict.get("verdict")), 3
            ):
                hard_failures.append(f"{session_id}:candidate_verdict_regressed")
        first = output_fingerprint(session)
        repeat = apply_session(
            SimpleNamespace(
                sessions_root=sessions_root,
                out_dir=args.out_dir,
                session=session,
                mode="conservative",
            )
        )
        deterministic = repeat == 0 and first == output_fingerprint(session)
        if not deterministic:
            hard_failures.append(f"{session_id}:profile_not_deterministic")
        sessions.append(
            {
                "session_id": session_id,
                "input_profile": input_profile,
                "queue_items": safe_int(summary.get("queue_items")),
                "closed_items": safe_int(summary.get("closed_items")),
                "closed_seconds": safe_float(summary.get("closed_seconds")),
                "applied_items": safe_int(summary.get("applied_items")),
                "removed_remote_span_seconds": safe_float(
                    summary.get("removed_remote_span_seconds")
                ),
                "local_recall_before": before_recall,
                "local_recall_after": after_recall,
                "order_before": before_order,
                "order_after": after_order,
                "deterministic": deterministic,
                "output_fingerprint": first,
            }
        )
    queue_seconds = sum(duration(row) for row in queue)
    queue_items = len(queue)
    duplicate_reduction = removed_seconds / queue_seconds if queue_seconds > 0 else 0.0
    review_reduction_items = closed_items / queue_items if queue_items > 0 else 0.0
    review_reduction_seconds = closed_seconds / queue_seconds if queue_seconds > 0 else 0.0
    review_reduction = min(review_reduction_items, review_reduction_seconds)
    runtime_ratio = evidence_runtime / baseline_runtime if baseline_runtime > 0 else 0.0
    if duplicate_reduction + 1e-9 < MIN_DUPLICATE_REDUCTION:
        hard_failures.append(
            f"duplicate_leak_reduction_below_25_percent:{duplicate_reduction:.6f}"
        )
    if review_reduction + 1e-9 < MIN_REVIEW_REDUCTION:
        hard_failures.append(
            f"mandatory_review_reduction_below_15_percent:{review_reduction:.6f}"
        )
    if runtime_ratio > MAX_RUNTIME_RATIO + 1e-9:
        hard_failures.append(f"additional_runtime_above_25_percent:{runtime_ratio:.6f}")
    speaker_root = sessions_root / "_reports" / SPEAKER_REPORT_DIR_NAME
    if sha256_file(speaker_root / "local_recall_risk_queue.jsonl") != (
        manifest.get("preserved_debt") or {}
    ).get("local_recall_sha256"):
        hard_failures.append("frozen_local_recall_queue_changed")
    if sha256_file(speaker_root / "chronology_risk_queue.jsonl") != (
        manifest.get("preserved_debt") or {}
    ).get("transcript_order_sha256"):
        hard_failures.append("frozen_transcript_order_queue_changed")
    hard_failures = sorted(set(hard_failures))
    decision = PROMOTE_DECISION if not hard_failures else "DO_NOT_PROMOTE"
    report = {
        "schema": "murmurmark.mixed_utterance_separation_corpus_report/v1",
        "generator": {"name": "mixed-utterance-span-separation", "version": SCRIPT_VERSION},
        "profile": PROFILE,
        "decision": decision,
        "baseline_frozen_identity": manifest.get("frozen_identity"),
        "baseline_sha256": sha256_file(output / "baseline_manifest.json"),
        "summary": {
            "session_count": len(sessions),
            "queue_items": queue_items,
            "queue_seconds": round(queue_seconds, 3),
            "closed_items": closed_items,
            "closed_seconds": round(closed_seconds, 3),
            "applied_items": applied_items,
            "removed_remote_span_seconds": round(removed_seconds, 3),
            "duplicate_reduction_ratio": round(duplicate_reduction, 6),
            "review_reduction_items_ratio": round(review_reduction_items, 6),
            "review_reduction_seconds_ratio": round(review_reduction_seconds, 6),
            "review_reduction_ratio": round(review_reduction, 6),
            "baseline_process_runtime_sec": round(baseline_runtime, 3),
            "additional_evidence_runtime_sec": round(evidence_runtime, 3),
            "additional_runtime_ratio": round(runtime_ratio, 6),
        },
        "evidence_limit": {
            "outcomes": dict(sorted(outcomes.items())),
            "actions": dict(sorted(actions.items())),
            "reasons": dict(sorted(reasons.items())),
            "interpretation": (
                "The profile changes only contiguous spans with independent remote and local-island "
                "evidence. Every conflicting, protected, cross-debt or weak-calibration row remains "
                "unchanged and explicit."
            ),
        },
        "audit": {
            "unresolved_examples": sorted(
                unresolved_examples,
                key=lambda row: (
                    -safe_float(row.get("duration_sec")),
                    str(row.get("session_id") or ""),
                    safe_float(row.get("start")),
                ),
            )[:10],
        },
        "sessions": sessions,
        "promoted_sessions": (
            sorted(row["session_id"] for row in sessions) if decision == PROMOTE_DECISION else []
        ),
        "gates": {
            "passed": not hard_failures,
            "hard_failures": hard_failures,
            "thresholds": {
                "duplicate_leak_reduction_ratio_min": MIN_DUPLICATE_REDUCTION,
                "mandatory_review_reduction_ratio_min": MIN_REVIEW_REDUCTION,
                "additional_runtime_ratio_max": MAX_RUNTIME_RATIO,
            },
        },
    }
    write_json(output / "mixed_utterance_separation_corpus_report.json", report)
    write_corpus_markdown(output / "mixed_utterance_separation_corpus_report.md", report)
    print(f"corpus_report: {output / 'mixed_utterance_separation_corpus_report.json'}")
    print(f"decision: {decision}")
    print(f"closed: {closed_items}/{queue_items}")
    print(f"removed_remote_span_seconds: {round(removed_seconds, 3)}")
    return 0


def run_all(args: argparse.Namespace) -> int:
    freeze_code = freeze_corpus(
        SimpleNamespace(
            sessions_root=args.sessions_root,
            out_dir=args.out_dir,
            force=args.force_freeze,
        )
    )
    if freeze_code != 0:
        return freeze_code
    evidence_code = build_evidence(args)
    if evidence_code != 0:
        return evidence_code
    return evaluate_corpus(
        SimpleNamespace(
            sessions_root=args.sessions_root,
            out_dir=args.out_dir,
            apply=True,
            synthesize=not args.skip_synthesis,
        )
    )


def main() -> int:
    args = parse_args()
    if args.command == "freeze":
        return freeze_corpus(args)
    if args.command == "evidence":
        return build_evidence(args)
    if args.command == "apply":
        return apply_session(args)
    if args.command == "evaluate":
        return evaluate_corpus(args)
    if args.command == "run":
        return run_all(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
