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
from collections import Counter
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any


SCRIPT_VERSION = "0.1.0"
PROFILE = "local_speech_completion_v2"
REPORT_DIR_NAME = "local-speech-completion-v2"
LOCAL_OUTCOMES = {"materialized", "rejected_remote", "rejected_conflict", "needs_review"}
TEXT_OUTCOMES = {"text_repaired", "needs_review"}
PROMOTE_DECISION = "PROMOTE_LOCAL_SPEECH_COMPLETION_V2"
MIN_CLOSED_RATIO = 0.50
MIN_LOCAL_SUPPORT = 0.45
MAX_REMOTE_TEXT_SIMILARITY = 0.42
MIN_MIC_AGREEMENT = 0.70
MIN_TARGET_CONFIDENCE = 0.88
KNOWN_HALLUCINATIONS = (
    "продолжение следует",
    "спасибо за просмотр",
    "редактор субтитров",
    "субтитры сделал",
)
VALID_SINGLE_LETTER_RUSSIAN = {"а", "в", "и", "к", "о", "с", "у", "я"}
TOKEN_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё_./+-]+")


def load_sibling(filename: str, module_name: str) -> ModuleType:
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load sibling module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


BASE = load_sibling("residual-me-evidence.py", "murmurmark_local_completion_base")
STRONGER = load_sibling("audit-stronger-audio-judge.py", "murmurmark_local_completion_stronger")
TARGET = load_sibling("audit-target-me.py", "murmurmark_local_completion_target")
ORDER = load_sibling("apply-transcript-order-repair.py", "murmurmark_local_completion_order")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build an evidence-backed local speech completion profile without changing its inputs."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def common(command: argparse.ArgumentParser) -> None:
        command.add_argument("--sessions-root", type=Path, default=Path("sessions"))
        command.add_argument("--out-dir", type=Path)

    freeze = subparsers.add_parser("freeze", help="Freeze selected profiles and completion queue.")
    common(freeze)
    freeze.add_argument("sessions", nargs="+", type=Path)
    freeze.add_argument("--force", action="store_true")

    evidence = subparsers.add_parser("evidence", help="Build local ASR and Target-Me evidence.")
    common(evidence)
    evidence.add_argument("sessions", nargs="*", type=Path)
    add_model_args(evidence)

    apply = subparsers.add_parser("apply", help="Apply safe completion decisions to one session.")
    common(apply)
    apply.add_argument("session", type=Path)
    apply.add_argument("--mode", choices=("conservative", "dry-run"), default="conservative")

    evaluate = subparsers.add_parser("evaluate", help="Evaluate the frozen candidate corpus.")
    common(evaluate)
    evaluate.add_argument("--apply", action="store_true")
    evaluate.add_argument("--synthesize", action="store_true")

    run = subparsers.add_parser("run", help="Freeze, resolve, apply and evaluate sessions.")
    common(run)
    run.add_argument("sessions", nargs="+", type=Path)
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
    parser.add_argument("--padding-sec", type=float, default=1.25)
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


def sha256_file(path: Path) -> str | None:
    return BASE.sha256_file(path)


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def suffix(profile: str) -> str:
    return "" if profile == "current" else f".{profile}"


def profile_paths(session: Path, profile: str) -> dict[str, Path]:
    return BASE.profile_paths(session, profile)


def role_name(row: dict[str, Any]) -> str:
    return BASE.role_name(row)


def interval(row: dict[str, Any]) -> tuple[float, float]:
    value = row.get("interval") if isinstance(row.get("interval"), dict) else {}
    start = safe_float(value.get("start"), safe_float(row.get("start_sec"), safe_float(row.get("start"))))
    end = safe_float(value.get("end"), safe_float(row.get("end_sec"), safe_float(row.get("end"), start)))
    return start, max(start, end)


def duration(row: dict[str, Any]) -> float:
    start, end = interval(row)
    return max(0.0, end - start)


def normalize_text(value: Any) -> str:
    return STRONGER.normalize_text(value)


def content_tokens(value: Any) -> list[str]:
    return STRONGER.content_tokens(value)


def text_similarity(left: Any, right: Any) -> dict[str, float]:
    return STRONGER.text_similarity(left, right)


def selected_profile(session: Path) -> str:
    readiness = read_json(session / "derived/readiness/session_readiness.json") or {}
    profile = str(readiness.get("selected_profile") or "")
    if profile and profile_paths(session, profile)["dialogue"].exists():
        return profile
    resolved = session / "derived/transcript-simple/whisper-cpp/resolved"
    for candidate in (
        "residual_local_recall_v1",
        "reviewed_v1",
        "agent_reviewed_v1",
        "audit_cleanup_v7",
        "audit_cleanup_v2",
        "shadow_v2",
        "current",
    ):
        if (resolved / f"clean_dialogue{suffix(candidate)}.json").exists():
            return candidate
    return "current"


def report_dir(sessions_root: Path, explicit: Path | None) -> Path:
    return explicit.expanduser().resolve() if explicit else sessions_root / "_reports" / REPORT_DIR_NAME


def session_profile_dir(session: Path) -> Path:
    return session / "derived/transcript-simple/whisper-cpp/local-speech-completion-v2"


def session_audit_dir(session: Path) -> Path:
    return session / "derived/audit/local-speech-completion-v2"


def artifact(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "exists": path.exists(),
        "size": path.stat().st_size if path.exists() else None,
        "sha256": sha256_file(path),
    }


def frozen_artifacts(session: Path, profile: str) -> dict[str, Any]:
    audio = session / "derived/preprocess/audio"
    return {
        "raw_capture": BASE.raw_fingerprints(session),
        "input_profile": BASE.artifact_fingerprints(session, profile),
        "working_audio": {
            "mic_raw": artifact(audio / "mic_raw_for_asr.wav"),
            "mic_clean": artifact(audio / "mic_clean_local_fir.wav"),
            "mic_role_masked": artifact(audio / "mic_role_masked_for_asr.wav"),
            "remote": artifact(audio / "remote_for_aec.wav"),
        },
        "speaker_state": artifact(session / "derived/preprocess/echo/speaker_state.jsonl"),
        "local_recall_items": artifact(session / "derived/audit/local-recall/local_recall_items.jsonl"),
        "remote_forbidden": artifact(session / "derived/audit/remote-forbidden/remote_forbidden_evidence.jsonl"),
    }


def materialized_local_recall_ids(dialogue: dict[str, Any]) -> set[str]:
    result: set[str] = set()
    for row in dialogue.get("utterances") or []:
        if not isinstance(row, dict):
            continue
        source = row.get("source") if isinstance(row.get("source"), dict) else {}
        quality = row.get("quality") if isinstance(row.get("quality"), dict) else {}
        if source.get("kind") != "local_recall_repair" or quality.get("needs_review") is True:
            continue
        item_id = str(source.get("item_id") or "")
        if item_id:
            result.add(item_id)
    return result


def suspicious_text_fragment(row: dict[str, Any]) -> tuple[bool, str]:
    quality = row.get("quality") if isinstance(row.get("quality"), dict) else {}
    if quality.get("needs_review") is not True:
        return False, "not_marked_needs_review"
    if isinstance(quality.get("text_fragment_review"), dict):
        return True, str(quality["text_fragment_review"].get("reason") or "text_fragment_review")
    repair = quality.get("repair") if isinstance(quality.get("repair"), dict) else {}
    micro = repair.get("micro_reasr") if isinstance(repair.get("micro_reasr"), dict) else {}
    text = str(row.get("text") or "").strip()
    token_values = [value.lower() for value in TOKEN_RE.findall(text)]
    if micro.get("status") == "failed" and len(token_values) <= 4:
        return True, f"failed_micro_asr:{micro.get('reason') or 'unknown'}"
    if len(token_values) == 1 and len(token_values[0]) == 1 and token_values[0] not in VALID_SINGLE_LETTER_RUSSIAN:
        return True, "single_letter_asr_fragment"
    if len(token_values) <= 3 and token_values and len(token_values[-1]) <= 2 and safe_float(row.get("end")) - safe_float(row.get("start")) <= 4.0:
        return True, "trailing_short_word_fragment"
    return False, "not_suspicious"


def queue_for_session(session: Path, profile: str) -> tuple[list[dict[str, Any]], list[str]]:
    paths = profile_paths(session, profile)
    dialogue = read_json(paths["dialogue"])
    failures: list[str] = []
    if not dialogue:
        return [], ["missing_input_dialogue"]
    covered = materialized_local_recall_ids(dialogue)
    rows: list[dict[str, Any]] = []
    for item in read_jsonl(session / "derived/audit/local-recall/local_recall_items.jsonl"):
        item_id = str(item.get("item_id") or "")
        if not item_id or item_id in covered:
            continue
        if str(item.get("label") or "") not in {"possible_lost_me", "needs_review"}:
            continue
        start, end = interval(item)
        queue = {
            "schema": "murmurmark.local_speech_completion_queue_item/v2",
            "queue_id": f"{session.name}:local_recall:{item_id}",
            "session_id": session.name,
            "input_profile": profile,
            "kind": "local_recall",
            "source_audit_id": item_id,
            "interval": {"start": round(start, 3), "end": round(end, 3), "duration_sec": round(end - start, 3)},
            "target_text": str(item.get("parent_text") or "").strip(),
            "remote_text": str(item.get("remote_overlap_text_sample") or "").strip(),
            "source_detail": copy.deepcopy(item),
        }
        queue["source_fingerprint"] = fingerprint(item)
        rows.append(queue)
    for utterance in dialogue.get("utterances") or []:
        if not isinstance(utterance, dict) or role_name(utterance) != "Me":
            continue
        suspicious, reason = suspicious_text_fragment(utterance)
        if not suspicious:
            continue
        start, end = interval(utterance)
        queue = {
            "schema": "murmurmark.local_speech_completion_queue_item/v2",
            "queue_id": f"{session.name}:text_fragment:{utterance.get('id')}",
            "session_id": session.name,
            "input_profile": profile,
            "kind": "text_fragment",
            "utterance_id": utterance.get("id"),
            "utterance_ids": [utterance.get("id")],
            "interval": {"start": round(start, 3), "end": round(end, 3), "duration_sec": round(end - start, 3)},
            "target_text": str(utterance.get("text") or "").strip(),
            "remote_text": "",
            "source_detail": copy.deepcopy(utterance),
            "detection_reason": reason,
        }
        queue["source_fingerprint"] = fingerprint(utterance)
        rows.append(queue)
    rows.sort(key=lambda row: (safe_float((row.get("interval") or {}).get("start")), str(row.get("queue_id"))))
    return rows, failures


def freeze_corpus(args: argparse.Namespace) -> int:
    sessions_root = args.sessions_root.expanduser().resolve()
    out_dir = report_dir(sessions_root, args.out_dir)
    baseline_path = out_dir / "baseline_manifest.json"
    queue_path = out_dir / "completion_queue.jsonl"
    if baseline_path.exists() and queue_path.exists() and not args.force:
        print(f"baseline_manifest: {baseline_path}")
        print(f"completion_queue: {queue_path}")
        print("status: already_frozen")
        return 0
    records: list[dict[str, Any]] = []
    queue: list[dict[str, Any]] = []
    failures: list[str] = []
    for source in args.sessions:
        session = source.expanduser().resolve()
        profile = selected_profile(session)
        session_queue, session_failures = queue_for_session(session, profile)
        queue.extend(session_queue)
        failures.extend(f"{session.name}:{value}" for value in session_failures)
        records.append(
            {
                "session_id": session.name,
                "session": str(session),
                "input_profile": profile,
                "artifacts": frozen_artifacts(session, profile),
                "queue_ids": [str(row.get("queue_id")) for row in session_queue],
                "queue_fingerprint": fingerprint(session_queue),
            }
        )
        print(f"freeze: {session.name}: profile={profile} queue={len(session_queue)}", flush=True)
    queue.sort(key=lambda row: (str(row.get("session_id")), safe_float((row.get("interval") or {}).get("start"))))
    baseline = {
        "schema": "murmurmark.local_speech_completion_baseline/v2",
        "generator": {"name": "local-speech-completion-v2", "version": SCRIPT_VERSION},
        "output_profile": PROFILE,
        "scope": {"session_count": len(records), "session_ids": [row["session_id"] for row in records]},
        "queue": {
            "item_count": len(queue),
            "seconds": round(sum(duration(row) for row in queue), 3),
            "by_kind": dict(sorted(Counter(str(row.get("kind")) for row in queue).items())),
            "fingerprint": fingerprint(queue),
        },
        "sessions": records,
        "gates": {"passed": bool(records) and not failures, "hard_failures": failures},
    }
    write_json(baseline_path, baseline)
    write_jsonl(queue_path, queue)
    print(f"baseline_manifest: {baseline_path}")
    print(f"completion_queue: {queue_path}")
    print(f"items: {len(queue)}")
    print(f"seconds: {baseline['queue']['seconds']}")
    return 0 if baseline["gates"]["passed"] else 2


def local_state_intervals(state_rows: list[dict[str, Any]], start: float, end: float) -> list[tuple[float, float]]:
    intervals: list[tuple[float, float]] = []
    for row in state_rows:
        if str(row.get("state") or "") != "local_only":
            continue
        left = max(start, safe_float(row.get("start")))
        right = min(end, safe_float(row.get("end"), left))
        if right - left >= 0.08:
            intervals.append((left, right))
    intervals.sort()
    merged: list[tuple[float, float]] = []
    for left, right in intervals:
        if merged and left - merged[-1][1] <= 0.18:
            merged[-1] = (merged[-1][0], max(merged[-1][1], right))
        else:
            merged.append((left, right))
    return merged


def model_path(args: argparse.Namespace) -> Path:
    return BASE.model_path(args)


def model_identity(path: Path) -> dict[str, Any]:
    return BASE.model_identity(path)


def load_models(args: argparse.Namespace) -> tuple[Any | None, Any | None, dict[str, Any], str | None]:
    resolved = model_path(args)
    binary = resolved / "model.bin" if resolved.is_dir() else resolved
    if not binary.exists():
        return None, None, {}, f"faster_whisper_model_missing:{resolved}"
    if not args.allow_download:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
    model_args = SimpleNamespace(device=args.device, compute_type=args.compute_type, allow_download=args.allow_download)
    model = STRONGER.load_model(resolved, model_args)
    backend_args = SimpleNamespace(method="resemblyzer_dvector", wavlm_model=None)
    backend, backend_status = TARGET.resolve_embedding_backend(backend_args)
    ready, reason = backend.ready()
    if not ready:
        return model, None, backend_status, f"target_me_backend_unavailable:{reason}"
    return model, backend, backend_status, None


def decode_sources(
    session: Path,
    queue_row: dict[str, Any],
    model: Any,
    args: argparse.Namespace,
    destination: Path,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Path], list[str]]:
    start, end = interval(queue_row)
    sources = BASE.audio_sources(session)
    windows = [("normal", max(0.0, start - args.padding_sec), end + args.padding_sec)]
    if str(queue_row.get("kind")) == "text_fragment":
        windows.append(("wide", max(0.0, start - 2.5), end + 1.8))
    runs: dict[str, list[dict[str, Any]]] = {}
    exact_clips: dict[str, Path] = {}
    failures: list[str] = []
    for source_name, source_path in sources.items():
        if not source_path.exists():
            failures.append(f"missing_audio:{source_name}")
            continue
        exact = destination / "clips" / str(queue_row.get("queue_id")).replace(":", "_") / f"{source_name}.wav"
        if BASE.extract_wav(source_path, exact, start, end):
            exact_clips[source_name] = exact
        else:
            failures.append(f"exact_extract_failed:{source_name}")
        for window_name, clip_start, clip_end in windows:
            clip = exact.parent / f"{source_name}_{window_name}_asr.wav"
            if not BASE.extract_wav(source_path, clip, clip_start, clip_end):
                failures.append(f"asr_extract_failed:{source_name}:{window_name}")
                continue
            run = BASE.decode_exact_interval(
                model,
                clip,
                core_start=max(0.0, start - clip_start - (0.35 if window_name == "wide" else 0.08)),
                core_end=end - clip_start + (0.35 if window_name == "wide" else 0.08),
                cache_dir=destination / "micro-asr-cache",
                args=args,
            )
            run = copy.deepcopy(run)
            run["source"] = source_name
            run["window"] = window_name
            run["clip"] = str(clip)
            runs.setdefault(source_name, []).append(run)
    return runs, exact_clips, failures


def best_run(runs: list[dict[str, Any]]) -> dict[str, Any]:
    usable = [row for row in runs if row.get("status") == "ok" and str(row.get("text") or "").strip()]
    return max(
        usable,
        key=lambda row: (
            safe_float(row.get("avg_logprob"), -99.0),
            len(content_tokens(row.get("text"))),
            row.get("window") == "normal",
        ),
        default={},
    )


def mic_consensus(runs: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    selected = {source: best_run(rows) for source, rows in runs.items() if source != "remote"}
    selected = {source: row for source, row in selected.items() if row}
    best: dict[str, Any] = {
        "passed": False,
        "sources": [],
        "selected_source": "",
        "text": "",
        "similarity": {},
    }
    pairs = (("mic_raw", "mic_clean"), ("mic_raw", "mic_role_masked"), ("mic_clean", "mic_role_masked"))
    candidates: list[dict[str, Any]] = []
    for left, right in pairs:
        if left not in selected or right not in selected:
            continue
        similarity = text_similarity(selected[left].get("text"), selected[right].get("text"))
        score = max(safe_float(similarity.get("similarity")), safe_float(similarity.get("containment")))
        candidate_source = max(
            (left, right),
            key=lambda value: (
                safe_float(selected[value].get("avg_logprob"), -99.0),
                len(content_tokens(selected[value].get("text"))),
            ),
        )
        independent = "mic_raw" in {left, right} and any(value in {left, right} for value in {"mic_clean", "mic_role_masked"})
        candidates.append({
            "passed": independent and score >= MIN_MIC_AGREEMENT,
            "independent": independent,
            "sources": [left, right],
            "selected_source": candidate_source,
            "text": str(selected[candidate_source].get("text") or "").strip(),
            "similarity": similarity,
            "run": selected[candidate_source],
            "runs": selected,
            "score": score,
        })
    if not candidates:
        return best
    return max(
        candidates,
        key=lambda row: (
            row.get("independent") is True,
            row.get("passed") is True,
            safe_float(row.get("score")),
            safe_float((row.get("run") or {}).get("avg_logprob"), -99.0),
        ),
    )


def words_from_run(run: dict[str, Any], start: float, end: float) -> list[dict[str, Any]]:
    return BASE.transcript_core_word_spans(run, interval_start=start, interval_end=end)


def valid_word_timestamps(run: dict[str, Any]) -> bool:
    identity = run.get("identity") if isinstance(run.get("identity"), dict) else {}
    words = [
        word
        for segment in run.get("segments") or []
        if isinstance(segment, dict)
        for word in segment.get("words") or []
        if isinstance(word, dict)
    ]
    return bool(
        identity.get("word_timestamps") is True
        and words
        and all("start" in word and "end" in word for word in words)
    )


def words_in_local_intervals(words: list[dict[str, Any]], intervals: list[tuple[float, float]]) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for word in words:
        midpoint = (safe_float(word.get("start")) + safe_float(word.get("end"))) / 2.0
        local = any(left - 0.08 <= midpoint <= right + 0.08 for left, right in intervals)
        if not local:
            if current:
                groups.append(current)
                current = []
            continue
        if current and safe_float(word.get("start")) - safe_float(current[-1].get("end")) > 0.75:
            groups.append(current)
            current = []
        current.append(word)
    if current:
        groups.append(current)
    return groups


def format_words(words: list[dict[str, Any]]) -> str:
    return BASE.format_word_group(words).strip()


def overlapping_remote_text(dialogue: dict[str, Any], start: float, end: float) -> str:
    values = []
    for row in dialogue.get("utterances") or []:
        if not isinstance(row, dict) or role_name(row) != "Colleagues":
            continue
        if min(end, safe_float(row.get("end"))) - max(start, safe_float(row.get("start"))) > 0:
            values.append(str(row.get("text") or ""))
    return " ".join(values).strip()


def existing_me_coverage(
    dialogue: dict[str, Any],
    text: str,
    start: float,
    end: float,
    *,
    excluded_ids: set[str] | None = None,
) -> dict[str, Any]:
    excluded_ids = excluded_ids or set()
    nearby = [
        row
        for row in dialogue.get("utterances") or []
        if isinstance(row, dict)
        and role_name(row) == "Me"
        and str(row.get("id") or "") not in excluded_ids
        and safe_float(row.get("end")) >= start - 12.0
        and safe_float(row.get("start")) <= end + 12.0
    ]
    candidates: list[dict[str, Any]] = []
    ordered = sorted(nearby, key=lambda row: (safe_float(row.get("start")), safe_float(row.get("end"))))
    for index, row in enumerate(ordered):
        groups = [[row]]
        if index + 1 < len(ordered) and safe_float(ordered[index + 1].get("start")) - safe_float(row.get("end")) <= 3.0:
            groups.append([row, ordered[index + 1]])
        for group in groups:
            candidate = " ".join(str(value.get("text") or "").strip() for value in group).strip()
            candidates.append(
                {
                    "utterance_ids": [str(value.get("id") or "") for value in group],
                    "text": candidate,
                    "similarity": text_similarity(text, candidate),
                }
            )
    best = max(
        candidates,
        key=lambda row: (
            safe_float((row.get("similarity") or {}).get("containment")),
            safe_float((row.get("similarity") or {}).get("similarity")),
        ),
        default={"utterance_ids": [], "text": "", "similarity": {}},
    )
    metrics = best.get("similarity") if isinstance(best.get("similarity"), dict) else {}
    covered = (
        safe_float(metrics.get("containment")) >= 0.82
        and safe_float(metrics.get("similarity")) >= 0.68
    ) or (
        safe_float(metrics.get("containment")) >= 0.60
        and safe_float(metrics.get("sequence_ratio")) >= 0.86
    )
    return {**best, "covered": covered}


def classify_local_recall(
    queue_row: dict[str, Any],
    dialogue: dict[str, Any],
    state: dict[str, Any],
    target_me: dict[str, Any],
    consensus: dict[str, Any],
    runs: dict[str, list[dict[str, Any]]],
    state_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    start, end = interval(queue_row)
    selected_run = consensus.get("run") if isinstance(consensus.get("run"), dict) else {}
    candidate_text = str(consensus.get("text") or "").strip()
    remote_run = best_run(runs.get("remote") or [])
    remote_text = " ".join(
        value for value in (str(remote_run.get("text") or "").strip(), overlapping_remote_text(dialogue, start, end), str(queue_row.get("remote_text") or "").strip()) if value
    )
    candidate_remote = text_similarity(candidate_text, remote_text)
    remote_score = max(safe_float(candidate_remote.get("similarity")), safe_float(candidate_remote.get("containment")))
    local_support = safe_float(state.get("local_only_ratio")) + 0.5 * safe_float(state.get("double_talk_ratio"))
    target_confirmed = (
        target_me.get("label") == "target_me_confirmed"
        and safe_float(target_me.get("confidence")) >= MIN_TARGET_CONFIDENCE
        and safe_float(target_me.get("delta_vs_remote")) >= 0.15
    )
    local_intervals = local_state_intervals(state_rows, start, end)
    existing_coverage = existing_me_coverage(dialogue, candidate_text, start, end)
    word_groups = words_in_local_intervals(words_from_run(selected_run, start, end), local_intervals)
    fragments: list[dict[str, Any]] = []
    for group in word_groups:
        text = format_words(group)
        if len(content_tokens(text)) < 2:
            continue
        remote_similarity = text_similarity(text, remote_text)
        if max(safe_float(remote_similarity.get("similarity")), safe_float(remote_similarity.get("containment"))) > MAX_REMOTE_TEXT_SIMILARITY:
            continue
        coverage = existing_me_coverage(dialogue, text, safe_float(group[0].get("start")), safe_float(group[-1].get("end")))
        if coverage.get("covered"):
            continue
        fragments.append(
            {
                "start": round(safe_float(group[0].get("start")), 3),
                "end": round(safe_float(group[-1].get("end")), 3),
                "text": text,
                "words": group,
                "remote_similarity": remote_similarity,
                "coverage": coverage,
            }
        )
    hallucination = any(value in normalize_text(candidate_text) for value in KNOWN_HALLUCINATIONS)
    checks = {
        "consensus": {key: value for key, value in consensus.items() if key not in {"run", "runs"}},
        "target_me_confirmed": target_confirmed,
        "target_me": target_me,
        "speaker_state": state,
        "local_support": round(local_support, 6),
        "remote_text": remote_text,
        "candidate_to_remote": candidate_remote,
        "remote_forbidden_pass": remote_score <= MAX_REMOTE_TEXT_SIMILARITY,
        "word_timestamps_valid": valid_word_timestamps(selected_run) if selected_run else False,
        "local_intervals": [{"start": left, "end": right} for left, right in local_intervals],
        "hallucination": hallucination,
        "existing_me_coverage": existing_coverage,
    }
    if existing_coverage.get("covered"):
        return {
            "outcome": "materialized",
            "action": "close_without_change",
            "reason": "confirmed_local_text_already_materialized_in_input_profile",
            "confidence": round(max(0.90, safe_float(target_me.get("confidence"))), 6),
            "fragments": [],
            "checks": checks,
        }
    if (
        consensus.get("passed") is True
        and target_confirmed
        and local_support >= MIN_LOCAL_SUPPORT
        and checks["remote_forbidden_pass"]
        and checks["word_timestamps_valid"]
        and not hallucination
        and fragments
    ):
        return {
            "outcome": "materialized",
            "action": "insert_me",
            "reason": "independent_mic_asr_target_me_and_local_word_bounds_confirm_missing_speech",
            "confidence": round(min(0.97, 0.45 * safe_float(target_me.get("confidence")) + 0.30 * local_support + 0.25 * max(safe_float((consensus.get("similarity") or {}).get("similarity")), safe_float((consensus.get("similarity") or {}).get("containment")))), 6),
            "fragments": fragments,
            "checks": checks,
        }
    if target_me.get("label") == "target_me_absent_remote_like" and remote_score >= 0.60:
        return {
            "outcome": "rejected_remote",
            "action": "close_without_change",
            "reason": "remote_text_and_voice_evidence_reject_missing_me_candidate",
            "confidence": round(max(safe_float(target_me.get("confidence")), remote_score), 6),
            "fragments": [],
            "checks": checks,
        }
    if target_confirmed and (safe_float(state.get("double_talk_ratio")) > 0.10 or safe_float(state.get("remote_active_ratio")) > 0.35):
        outcome = "rejected_conflict"
        reason = "target_me_present_but_local_words_cannot_be_separated_from_remote_activity"
    else:
        outcome = "needs_review"
        reason = "independent_local_speech_evidence_did_not_pass_all_materialization_gates"
    return {
        "outcome": outcome,
        "action": "needs_review",
        "reason": reason,
        "confidence": round(max(safe_float(target_me.get("confidence")), max(safe_float((consensus.get("similarity") or {}).get("similarity")), safe_float((consensus.get("similarity") or {}).get("containment")))), 6),
        "fragments": [],
        "checks": checks,
    }


def classify_text_fragment(
    queue_row: dict[str, Any],
    dialogue: dict[str, Any],
    state: dict[str, Any],
    target_me: dict[str, Any],
    consensus: dict[str, Any],
    runs: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    candidate = str(consensus.get("text") or "").strip()
    old_text = str(queue_row.get("target_text") or "").strip()
    remote_text = str(best_run(runs.get("remote") or []).get("text") or "").strip()
    remote_similarity = text_similarity(candidate, remote_text)
    remote_score = max(safe_float(remote_similarity.get("similarity")), safe_float(remote_similarity.get("containment")))
    local_support = safe_float(state.get("local_only_ratio")) + 0.5 * safe_float(state.get("double_talk_ratio"))
    target_confirmed = (
        target_me.get("label") == "target_me_confirmed"
        and safe_float(target_me.get("confidence")) >= MIN_TARGET_CONFIDENCE
        and safe_float(target_me.get("delta_vs_remote")) >= 0.15
    )
    old_similarity = text_similarity(candidate, old_text)
    candidate_tokens = TOKEN_RE.findall(candidate)
    plausible_length = 1 <= len(candidate_tokens) <= max(6, int(duration(queue_row) * 5.5 + 3))
    materially_better = len(content_tokens(candidate)) >= max(2, len(content_tokens(old_text)) + 1) or len(candidate) >= len(old_text) + 4
    hallucination = any(value in normalize_text(candidate) for value in KNOWN_HALLUCINATIONS)
    coverage = existing_me_coverage(
        dialogue,
        candidate,
        *interval(queue_row),
        excluded_ids={str(queue_row.get("utterance_id") or "")},
    )
    passed = (
        consensus.get("passed") is True
        and target_confirmed
        and local_support >= 0.40
        and remote_score <= MAX_REMOTE_TEXT_SIMILARITY
        and plausible_length
        and materially_better
        and not hallucination
    )
    checks = {
        "consensus": {key: value for key, value in consensus.items() if key not in {"run", "runs"}},
        "target_me": target_me,
        "speaker_state": state,
        "local_support": round(local_support, 6),
        "candidate_to_remote": remote_similarity,
        "candidate_to_old_text": old_similarity,
        "plausible_length": plausible_length,
        "materially_better": materially_better,
        "hallucination": hallucination,
        "existing_me_coverage": coverage,
    }
    if passed and coverage.get("covered"):
        return {
            "outcome": "text_repaired",
            "action": "drop_duplicate_fragment",
            "reason": "independent_mic_asr_confirms_fragment_is_already_present_in_adjacent_me",
            "confidence": round(min(0.98, max(0.92, safe_float(target_me.get("confidence")))), 6),
            "replacement_text": "",
            "covered_by_utterance_ids": coverage.get("utterance_ids") or [],
            "checks": checks,
        }
    if passed:
        return {
            "outcome": "text_repaired",
            "action": "replace_text",
            "reason": "independent_mic_asr_and_target_me_confirm_better_boundary_text",
            "confidence": round(min(0.97, 0.45 * safe_float(target_me.get("confidence")) + 0.35 * max(safe_float((consensus.get("similarity") or {}).get("similarity")), safe_float((consensus.get("similarity") or {}).get("containment"))) + 0.20 * local_support), 6),
            "replacement_text": candidate,
            "checks": checks,
        }
    return {
        "outcome": "needs_review",
        "action": "needs_review",
        "reason": "text_fragment_has_no_independently_confirmed_replacement",
        "confidence": round(max(safe_float(target_me.get("confidence")), max(safe_float((consensus.get("similarity") or {}).get("similarity")), safe_float((consensus.get("similarity") or {}).get("containment")))), 6),
        "replacement_text": "",
        "checks": checks,
    }


def build_session_evidence(
    session: Path,
    record: dict[str, Any],
    queue_rows: list[dict[str, Any]],
    model: Any,
    backend: Any,
    backend_status: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, Any]:
    profile = str(record.get("input_profile") or "current")
    paths = profile_paths(session, profile)
    dialogue = read_json(paths["dialogue"])
    destination = session_audit_dir(session)
    failures: list[str] = []
    if not dialogue or fingerprint(frozen_artifacts(session, profile)) != fingerprint(record.get("artifacts") or {}):
        failures.append("frozen_input_artifacts_changed")
        dialogue = dialogue or {"utterances": []}
    state_rows = TARGET.load_speaker_state(session)
    TARGET.now_iso = lambda: "deterministic"
    enrollment, target_model = TARGET.build_enrollment(
        session,
        profile,
        [row for row in dialogue.get("utterances") or [] if isinstance(row, dict)],
        state_rows,
        destination / "target-me",
        backend,
        backend_status,
        BASE.enrollment_args(),
    )
    enrollment["created_at"] = "deterministic"
    write_json(destination / "target-me/target_me_enrollment.json", enrollment)
    calibration = enrollment.get("calibration") if isinstance(enrollment.get("calibration"), dict) else {}
    evidence_rows: list[dict[str, Any]] = []
    for index, queue_row in enumerate(queue_rows, start=1):
        start, end = interval(queue_row)
        runs, exact_clips, clip_failures = decode_sources(session, queue_row, model, args, destination)
        speaker_context = BASE.speaker_context_interval(state_rows, start, end)
        target_clips = dict(exact_clips)
        if speaker_context is not None:
            context_start, context_end = speaker_context
            for source_name, source_path in BASE.audio_sources(session).items():
                if not source_path.exists():
                    continue
                context_clip = exact_clips.get(source_name, destination / "missing").parent / f"{source_name}_speaker_context.wav"
                if BASE.extract_wav(source_path, context_clip, context_start, context_end):
                    target_clips[f"{source_name}_context"] = context_clip
        target_evidence = (
            BASE.target_me_evidence(target_clips, backend, target_model, calibration)
            if enrollment.get("status") == "ready" and target_clips
            else {"label": "target_me_unavailable", "confidence": 0.0, "scores": {}}
        )
        state = TARGET.interval_state_features(state_rows, start, end)
        consensus = mic_consensus(runs)
        disposition = (
            classify_local_recall(queue_row, dialogue, state, target_evidence, consensus, runs, state_rows)
            if queue_row.get("kind") == "local_recall"
            else classify_text_fragment(queue_row, dialogue, state, target_evidence, consensus, runs)
        )
        evidence = {
            "schema": "murmurmark.local_speech_completion_evidence/v2",
            "session_id": session.name,
            "queue_id": queue_row.get("queue_id"),
            "kind": queue_row.get("kind"),
            "source_audit_id": queue_row.get("source_audit_id"),
            "utterance_id": queue_row.get("utterance_id"),
            "interval": queue_row.get("interval"),
            "input_row_fingerprint": fingerprint(queue_row),
            "clips": {key: {"path": str(value), "sha256": sha256_file(value)} for key, value in target_clips.items()},
            "clip_failures": clip_failures,
            "speaker_context_interval": (
                {"start": round(speaker_context[0], 3), "end": round(speaker_context[1], 3)}
                if speaker_context is not None
                else None
            ),
            "speaker_state": state,
            "target_me": target_evidence,
            "micro_asr": runs,
            "consensus": {key: value for key, value in consensus.items() if key not in {"run", "runs"}},
            "disposition": disposition,
        }
        evidence["evidence_fingerprint"] = fingerprint(evidence)
        evidence_rows.append(evidence)
        print(f"evidence: {session.name}: {index}/{len(queue_rows)}: {disposition['outcome']}", flush=True)
    write_jsonl(destination / "local_speech_completion_evidence.jsonl", evidence_rows)
    summary = {
        "schema": "murmurmark.local_speech_completion_evidence_summary/v2",
        "generator": {"name": "local-speech-completion-v2", "version": SCRIPT_VERSION},
        "session_id": session.name,
        "input_profile": profile,
        "queue_items": len(queue_rows),
        "queue_seconds": round(sum(duration(row) for row in queue_rows), 3),
        "by_outcome": dict(sorted(Counter(str((row.get("disposition") or {}).get("outcome")) for row in evidence_rows).items())),
        "enrollment": {
            "status": enrollment.get("status"),
            "accepted_count": enrollment.get("accepted_count"),
            "accepted_total_sec": enrollment.get("accepted_total_sec"),
            "method": enrollment.get("method"),
        },
        "gates": {"passed": not failures, "hard_failures": failures},
    }
    write_json(destination / "local_speech_completion_evidence_summary.json", summary)
    return summary


def build_evidence(args: argparse.Namespace) -> int:
    sessions_root = args.sessions_root.expanduser().resolve()
    out_dir = report_dir(sessions_root, args.out_dir)
    baseline = read_json(out_dir / "baseline_manifest.json") or {}
    queue = read_jsonl(out_dir / "completion_queue.jsonl")
    if not baseline or (baseline.get("gates") or {}).get("passed") is not True:
        print("status: missing_or_invalid_baseline", file=sys.stderr)
        return 2
    wanted = {path.expanduser().resolve().name for path in args.sessions}
    records = [
        row
        for row in baseline.get("sessions") or []
        if isinstance(row, dict) and (not wanted or str(row.get("session_id")) in wanted)
    ]
    model, backend, backend_status, error = load_models(args)
    if error or model is None or backend is None:
        summaries: list[dict[str, Any]] = []
        for record in records:
            session = sessions_root / str(record.get("session_id"))
            rows = [row for row in queue if row.get("session_id") == session.name]
            evidence_rows = []
            for row in rows:
                evidence = {
                    "schema": "murmurmark.local_speech_completion_evidence/v2",
                    "session_id": session.name,
                    "queue_id": row.get("queue_id"),
                    "kind": row.get("kind"),
                    "source_audit_id": row.get("source_audit_id"),
                    "utterance_id": row.get("utterance_id"),
                    "interval": row.get("interval"),
                    "input_row_fingerprint": fingerprint(row),
                    "clips": {},
                    "clip_failures": [error or "local_model_unavailable"],
                    "speaker_state": {},
                    "target_me": {"label": "target_me_unavailable", "confidence": 0.0, "scores": {}},
                    "micro_asr": [],
                    "consensus": {},
                    "disposition": {
                        "outcome": "needs_review",
                        "action": "needs_review",
                        "reason": error or "local_model_unavailable",
                        "confidence": 0.0,
                    },
                }
                evidence["evidence_fingerprint"] = fingerprint(evidence)
                evidence_rows.append(evidence)
            destination = session_audit_dir(session)
            write_jsonl(destination / "local_speech_completion_evidence.jsonl", evidence_rows)
            summary = {
                "schema": "murmurmark.local_speech_completion_evidence_summary/v2",
                "generator": {"name": "local-speech-completion-v2", "version": SCRIPT_VERSION},
                "session_id": session.name,
                "input_profile": record.get("input_profile"),
                "queue_items": len(rows),
                "queue_seconds": round(sum(duration(row) for row in rows), 3),
                "by_outcome": {"needs_review": len(rows)} if rows else {},
                "enrollment": {"status": "unavailable", "reason": error or "local_model_unavailable"},
                "gates": {
                    "passed": False,
                    "hard_failures": [error or "local_model_unavailable"],
                    "fail_open": True,
                },
            }
            write_json(destination / "local_speech_completion_evidence_summary.json", summary)
            summaries.append(summary)
        write_json(
            out_dir / "evidence_corpus_report.json",
            {
                "schema": "murmurmark.local_speech_completion_evidence_corpus/v2",
                "generator": {"name": "local-speech-completion-v2", "version": SCRIPT_VERSION},
                "model": model_identity(model_path(args)),
                "sessions": summaries,
                "gates": {
                    "passed": False,
                    "hard_failures": [error or "local_model_unavailable"],
                    "fail_open": True,
                },
            },
        )
        print(f"status: {error}", file=sys.stderr)
        return 0
    summaries = []
    for record in records:
        session = sessions_root / str(record.get("session_id"))
        session_queue = [row for row in queue if row.get("session_id") == session.name]
        summaries.append(build_session_evidence(session, record, session_queue, model, backend, backend_status, args))
    corpus = {
        "schema": "murmurmark.local_speech_completion_evidence_corpus/v2",
        "generator": {"name": "local-speech-completion-v2", "version": SCRIPT_VERSION},
        "model": model_identity(model_path(args)),
        "sessions": summaries,
        "gates": {
            "passed": bool(summaries) and all((row.get("gates") or {}).get("passed") is True for row in summaries),
            "hard_failures": [str(row.get("session_id")) for row in summaries if (row.get("gates") or {}).get("passed") is not True],
        },
    }
    write_json(out_dir / "evidence_corpus_report.json", corpus)
    return 0 if corpus["gates"]["passed"] else 2


def insertion_id(queue_id: str, index: int) -> str:
    return f"utt_lsc_{hashlib.sha256(queue_id.encode('utf-8')).hexdigest()[:10]}_{index:02d}"


def lexical_tokens(value: Any) -> list[str]:
    return [token.lower() for token in TOKEN_RE.findall(str(value or ""))]


def boundary_overlap_size(left: list[str], right: list[str], *, maximum: int = 12) -> int:
    for size in range(min(len(left), len(right), maximum), 0, -1):
        if left[-size:] == right[:size]:
            return size
    return 0


def trim_fragment_against_existing_me(
    fragment: dict[str, Any],
    utterances: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    words = [copy.deepcopy(row) for row in fragment.get("words") or [] if isinstance(row, dict)]
    if not words:
        return copy.deepcopy(fragment), {"prefix_words": 0, "suffix_words": 0}
    start = safe_float(fragment.get("start"))
    end = safe_float(fragment.get("end"), start)
    before = max(
        (
            row
            for row in utterances
            if role_name(row) == "Me"
            and safe_float(row.get("start")) <= start
            and safe_float(row.get("end")) >= start - 1.0
        ),
        key=lambda row: safe_float(row.get("end")),
        default=None,
    )
    prefix_words = 0
    if before is not None:
        prefix_words = boundary_overlap_size(
            lexical_tokens(before.get("text")),
            [lexical_tokens(word.get("text"))[0] for word in words if lexical_tokens(word.get("text"))],
        )
        if prefix_words == 1 and safe_float(words[0].get("start")) < safe_float(before.get("end")) - 0.35:
            prefix_words = 0
        if prefix_words:
            words = words[prefix_words:]
    after = min(
        (
            row
            for row in utterances
            if role_name(row) == "Me"
            and safe_float(row.get("start")) >= start
            and safe_float(row.get("start")) <= end + 1.0
            and (before is None or row.get("id") != before.get("id"))
        ),
        key=lambda row: safe_float(row.get("start")),
        default=None,
    )
    suffix_words = 0
    if after is not None and words:
        fragment_tokens = [lexical_tokens(word.get("text"))[0] for word in words if lexical_tokens(word.get("text"))]
        suffix_words = boundary_overlap_size(fragment_tokens, lexical_tokens(after.get("text")))
        if suffix_words == 1 and safe_float(words[-1].get("end")) < safe_float(after.get("start")) - 0.35:
            suffix_words = 0
        if suffix_words:
            words = words[:-suffix_words]
    if len(words) < 2:
        return None, {
            "prefix_words": prefix_words,
            "suffix_words": suffix_words,
            "reason": "boundary_dedup_left_no_substantive_fragment",
        }
    result = copy.deepcopy(fragment)
    result["words"] = words
    result["start"] = round(safe_float(words[0].get("start")), 3)
    result["end"] = round(safe_float(words[-1].get("end")), 3)
    result["text"] = format_words(words)
    metadata = {
        "prefix_words": prefix_words,
        "suffix_words": suffix_words,
        "before_utterance_id": before.get("id") if before is not None else None,
        "after_utterance_id": after.get("id") if after is not None else None,
    }
    result["boundary_dedup"] = metadata
    return result, metadata


def make_inserted_utterance(queue_row: dict[str, Any], evidence: dict[str, Any], fragment: dict[str, Any], index: int) -> dict[str, Any]:
    return {
        "id": insertion_id(str(queue_row.get("queue_id")), index),
        "role": "me",
        "speaker_label": "Me",
        "source_track": "mic",
        "start": fragment.get("start"),
        "end": fragment.get("end"),
        "text": fragment.get("text"),
        "source_segments": [],
        "quality": {
            "needs_review": False,
            "local_speech_completion": {
                "profile": PROFILE,
                "queue_id": queue_row.get("queue_id"),
                "source_audit_id": queue_row.get("source_audit_id"),
                "evidence_fingerprint": evidence.get("evidence_fingerprint"),
                "outcome": "materialized",
                "boundary_dedup": fragment.get("boundary_dedup"),
            },
        },
        "source": {"kind": "local_speech_completion", "queue_id": queue_row.get("queue_id")},
    }


def review_row(queue_row: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    disposition = evidence.get("disposition") if isinstance(evidence.get("disposition"), dict) else {}
    kind = str(queue_row.get("kind"))
    return {
        "schema": "murmurmark.local_speech_completion_review_item/v2",
        "session_id": queue_row.get("session_id"),
        "queue_id": queue_row.get("queue_id"),
        "kind": kind,
        "source": "local_speech_completion",
        "source_audit_id": queue_row.get("source_audit_id") or queue_row.get("queue_id"),
        "label": "local_recall_needs_review" if kind == "local_recall" else "transcript_text_needs_review",
        "review_lane": "check_local_recall" if kind == "local_recall" else "check_transcript_text",
        "review_action": "check_lost_local_speech" if kind == "local_recall" else "check_transcript_text",
        "allowed_decisions": ["needs_review", "skip"] if kind == "local_recall" else ["keep_me", "needs_review", "skip"],
        "interval": queue_row.get("interval"),
        "utterance_ids": queue_row.get("utterance_ids") or [],
        "me_utterance_ids": queue_row.get("utterance_ids") or [],
        "text": queue_row.get("target_text"),
        "outcome": disposition.get("outcome"),
        "reason": disposition.get("reason"),
        "confidence": disposition.get("confidence"),
        "evidence_fingerprint": evidence.get("evidence_fingerprint"),
    }


def write_profile_outputs(
    session: Path,
    input_profile: str,
    input_dialogue: dict[str, Any],
    input_quality: dict[str, Any],
    utterances: list[dict[str, Any]],
    summary: dict[str, Any],
) -> None:
    paths = profile_paths(session, PROFILE)
    overlaps = ORDER.build_overlaps(utterances)
    inherited_order_repair = (
        copy.deepcopy(input_quality.get("transcript_order_repair"))
        if isinstance(input_quality.get("transcript_order_repair"), dict)
        else {}
    )
    quality = ORDER.quality_report(input_quality, utterances, overlaps, inherited_order_repair)
    quality["local_speech_completion"] = summary
    dialogue = {
        "schema": "murmurmark.clean_dialogue/v1",
        "session": input_dialogue.get("session", session.name),
        "utterances": utterances,
    }
    write_json(paths["dialogue"], dialogue)
    write_json(paths["quality"], quality)
    write_json(paths["overlaps"], {"schema": "murmurmark.transcript_overlaps/v1", "session": session.name, "overlaps": overlaps})
    write_json(
        paths["transcript_json"],
        {
            "schema": "murmurmark.transcript_simple/v1",
            "session": input_dialogue.get("session", session.name),
            "backend": "whisper.cpp",
            "utterances": [
                {**copy.deepcopy(row), "raw_text": row.get("text"), "corrected_text": row.get("text"), "corrections": []}
                for row in utterances
            ],
        },
    )
    base_report = read_json(profile_paths(session, input_profile)["dialogue"].parent / "transcribe_simple_report.json") or {}
    ORDER.write_markdown(paths["transcript"], utterances, base_report.get("model"), base_report.get("language"))


def output_fingerprint(session: Path) -> str:
    paths = profile_paths(session, PROFILE)
    return fingerprint({name: sha256_file(path) for name, path in paths.items() if name in {"dialogue", "quality", "overlaps", "transcript", "transcript_json"}})


def apply_session(args: argparse.Namespace) -> int:
    sessions_root = args.sessions_root.expanduser().resolve()
    out_dir = report_dir(sessions_root, args.out_dir)
    baseline = read_json(out_dir / "baseline_manifest.json") or {}
    queue = read_jsonl(out_dir / "completion_queue.jsonl")
    session = args.session.expanduser().resolve()
    record = next((row for row in baseline.get("sessions") or [] if row.get("session_id") == session.name), None)
    if not isinstance(record, dict):
        print(f"status: session_not_in_frozen_scope:{session.name}", file=sys.stderr)
        return 2
    input_profile = str(record.get("input_profile") or "current")
    input_paths = profile_paths(session, input_profile)
    dialogue = read_json(input_paths["dialogue"])
    quality = read_json(input_paths["quality"])
    evidence_rows = read_jsonl(session_audit_dir(session) / "local_speech_completion_evidence.jsonl")
    evidence_by_id = {str(row.get("queue_id")): row for row in evidence_rows}
    session_queue = [row for row in queue if row.get("session_id") == session.name]
    failures: list[str] = []
    if not dialogue or not quality:
        failures.append("missing_input_profile")
    if fingerprint(frozen_artifacts(session, input_profile)) != fingerprint(record.get("artifacts") or {}):
        failures.append("frozen_input_artifacts_changed")
    if len(evidence_by_id) != len(session_queue):
        failures.append("not_all_queue_rows_have_evidence")
    if failures:
        write_json(
            session_profile_dir(session) / "local_speech_completion_profile_report.json",
            {
                "schema": "murmurmark.local_speech_completion_profile_report/v2",
                "session_id": session.name,
                "status": "failed_open",
                "gates": {"passed": False, "hard_failures": failures},
            },
        )
        return 2
    assert dialogue is not None and quality is not None
    utterances = [copy.deepcopy(row) for row in dialogue.get("utterances") or [] if isinstance(row, dict)]
    by_id = {str(row.get("id")): row for row in utterances if row.get("id")}
    inserted: list[dict[str, Any]] = []
    dispositions: list[dict[str, Any]] = []
    review: list[dict[str, Any]] = []
    for queue_row in session_queue:
        queue_id = str(queue_row.get("queue_id"))
        evidence = evidence_by_id.get(queue_id, {})
        disposition = evidence.get("disposition") if isinstance(evidence.get("disposition"), dict) else {}
        outcome = str(disposition.get("outcome") or "needs_review")
        applied = False
        if (
            queue_row.get("kind") == "local_recall"
            and outcome == "materialized"
            and disposition.get("action") == "close_without_change"
        ):
            pass
        elif queue_row.get("kind") == "local_recall" and outcome == "materialized":
            for index, fragment in enumerate(disposition.get("fragments") or [], start=1):
                trimmed, boundary_dedup = trim_fragment_against_existing_me(fragment, utterances + inserted)
                if trimmed is None:
                    failures.append(f"{queue_id}:materialized_fragment_has_no_unique_boundary_content")
                    continue
                row = make_inserted_utterance(queue_row, evidence, trimmed, index)
                overlap_me = any(
                    role_name(existing) == "Me"
                    and min(safe_float(row.get("end")), safe_float(existing.get("end")))
                    - max(safe_float(row.get("start")), safe_float(existing.get("start"))) > 0.15
                    for existing in utterances + inserted
                )
                if overlap_me:
                    failures.append(f"{queue_id}:materialized_fragment_overlaps_existing_me")
                    continue
                row["quality"]["local_speech_completion"]["boundary_dedup"] = boundary_dedup
                inserted.append(row)
                applied = True
            if not applied:
                outcome = "rejected_conflict"
                disposition = {**disposition, "outcome": outcome, "action": "needs_review", "reason": "materialized_fragments_conflict_with_existing_me"}
        elif queue_row.get("kind") == "text_fragment" and outcome == "text_repaired":
            utterance_id = str(queue_row.get("utterance_id") or "")
            target = by_id.get(utterance_id)
            replacement = str(disposition.get("replacement_text") or "").strip()
            if target is not None and disposition.get("action") == "drop_duplicate_fragment":
                utterances = [row for row in utterances if str(row.get("id") or "") != utterance_id]
                by_id.pop(utterance_id, None)
                applied = True
            elif target is not None and replacement:
                old_text = str(target.get("text") or "")
                target["text"] = replacement
                target["corrected_text"] = replacement
                quality_value = target.get("quality") if isinstance(target.get("quality"), dict) else {}
                quality_value = copy.deepcopy(quality_value)
                quality_value["needs_review"] = False
                quality_value["local_speech_completion"] = {
                    "profile": PROFILE,
                    "queue_id": queue_id,
                    "outcome": "text_repaired",
                    "old_text": old_text,
                    "evidence_fingerprint": evidence.get("evidence_fingerprint"),
                }
                target["quality"] = quality_value
                applied = True
        close_without_change = disposition.get("action") == "close_without_change"
        closed = outcome in {"materialized", "rejected_remote", "text_repaired"} and (
            applied or outcome == "rejected_remote" or close_without_change
        )
        row = {
            "schema": "murmurmark.local_speech_completion_disposition/v2",
            "session_id": session.name,
            "queue_id": queue_id,
            "kind": queue_row.get("kind"),
            "source_audit_id": queue_row.get("source_audit_id"),
            "utterance_id": queue_row.get("utterance_id"),
            "interval": queue_row.get("interval"),
            "outcome": outcome,
            "action": disposition.get("action") if closed else "needs_review",
            "reason": disposition.get("reason"),
            "confidence": disposition.get("confidence"),
            "closed": closed,
            "evidence_fingerprint": evidence.get("evidence_fingerprint"),
        }
        dispositions.append(row)
        if not closed:
            review.append(review_row(queue_row, evidence))
    utterances = BASE.merge_insertions_preserving_existing_order(utterances, inserted)
    remote_before = [row for row in dialogue.get("utterances") or [] if isinstance(row, dict) and role_name(row) == "Colleagues"]
    remote_after = [row for row in utterances if role_name(row) == "Colleagues"]
    input_by_id = {str(row.get("id")): row for row in dialogue.get("utterances") or [] if isinstance(row, dict) and row.get("id")}
    output_ids = {str(row.get("id")) for row in utterances if row.get("id")}
    changed_existing = [
        str(row.get("id"))
        for row in utterances
        if str(row.get("id")) in input_by_id and row != input_by_id[str(row.get("id"))]
    ]
    allowed_changed = {str(row.get("utterance_id")) for row in session_queue if row.get("kind") == "text_fragment"}
    unexpected_changed = sorted(set(changed_existing) - allowed_changed)
    removed_existing = set(input_by_id) - output_ids
    allowed_removed = {
        str(queue_row.get("utterance_id"))
        for queue_row in session_queue
        if queue_row.get("kind") == "text_fragment"
        and (evidence_by_id.get(str(queue_row.get("queue_id")), {}).get("disposition") or {}).get("action")
        == "drop_duplicate_fragment"
    }
    unexpected_removed = sorted(removed_existing - allowed_removed)
    summary = {
        "queue_items": len(session_queue),
        "queue_seconds": round(sum(duration(row) for row in session_queue), 3),
        "closed_items": sum(1 for row in dispositions if row.get("closed") is True),
        "closed_seconds": round(sum(duration(queue_row) for queue_row in session_queue if next((row for row in dispositions if row.get("queue_id") == queue_row.get("queue_id")), {}).get("closed") is True), 3),
        "remaining_items": len(review),
        "remaining_seconds": round(sum(duration(queue_row) for queue_row in session_queue if next((row for row in dispositions if row.get("queue_id") == queue_row.get("queue_id")), {}).get("closed") is not True), 3),
        "inserted_me_items": len(inserted),
        "repaired_text_items": sum(1 for row in dispositions if row.get("outcome") == "text_repaired" and row.get("closed") is True),
        "by_outcome": dict(sorted(Counter(str(row.get("outcome")) for row in dispositions).items())),
    }
    if remote_before != remote_after:
        failures.append("remote_utterances_changed")
    if unexpected_changed:
        failures.append(f"unexpected_existing_utterances_changed:{','.join(unexpected_changed)}")
    if unexpected_removed:
        failures.append(f"unexpected_existing_utterances_removed:{','.join(unexpected_removed)}")
    if any(str(row.get("outcome")) not in (LOCAL_OUTCOMES if row.get("kind") == "local_recall" else TEXT_OUTCOMES) for row in dispositions):
        failures.append("invalid_outcome")
    gates = {"passed": not failures, "hard_failures": failures}
    if args.mode == "conservative" and gates["passed"]:
        write_profile_outputs(session, input_profile, dialogue, quality, utterances, summary)
    output_dir = session_profile_dir(session)
    write_jsonl(output_dir / "local_speech_completion_dispositions.jsonl", dispositions)
    write_jsonl(output_dir / "local_speech_completion_applied.jsonl", [row for row in dispositions if row.get("closed") is True])
    write_jsonl(output_dir / "local_speech_completion_review_queue.jsonl", review)
    report = {
        "schema": "murmurmark.local_speech_completion_profile_report/v2",
        "generator": {"name": "local-speech-completion-v2", "version": SCRIPT_VERSION},
        "session_id": session.name,
        "input_profile": input_profile,
        "output_profile": PROFILE,
        "status": "ok" if gates["passed"] else "failed_open",
        "summary": summary,
        "gates": gates,
        "evidence_sha256": sha256_file(session_audit_dir(session) / "local_speech_completion_evidence.jsonl"),
        "review_queue_actionable": all(
            row.get("review_lane") in {"check_local_recall", "check_transcript_text"}
            and bool(row.get("allowed_decisions"))
            and set(row.get("allowed_decisions") or []) <= {"drop_me", "drop_remote", "keep_me", "needs_review", "skip"}
            for row in review
        ),
    }
    if args.mode == "conservative" and gates["passed"]:
        report["output_fingerprint"] = output_fingerprint(session)
    write_json(output_dir / "local_speech_completion_profile_report.json", report)
    print(f"profile: {session.name}: closed={summary['closed_items']} remaining={summary['remaining_items']}")
    return 0 if gates["passed"] else 2


def synthesize(session: Path) -> int:
    environment = os.environ.copy()
    environment["MURMURMARK_LOCAL_SPEECH_COMPLETION_CANDIDATE"] = "1"
    return subprocess.run(
        [sys.executable, str(Path(__file__).with_name("synthesize-simple-extractive.py")), str(session), "--transcript-profile", PROFILE],
        env=environment,
        check=False,
    ).returncode


def evaluate_corpus(args: argparse.Namespace) -> int:
    sessions_root = args.sessions_root.expanduser().resolve()
    out_dir = report_dir(sessions_root, args.out_dir)
    baseline_path = out_dir / "baseline_manifest.json"
    queue_path = out_dir / "completion_queue.jsonl"
    baseline = read_json(baseline_path) or {}
    queue = read_jsonl(queue_path)
    failures: list[str] = []
    if fingerprint(queue) != ((baseline.get("queue") or {}).get("fingerprint")):
        failures.append("frozen_queue_changed")
    sessions = []
    dispositions: list[dict[str, Any]] = []
    for record in baseline.get("sessions") or []:
        session = sessions_root / str(record.get("session_id"))
        if args.apply:
            apply_args = argparse.Namespace(session=session, sessions_root=sessions_root, out_dir=out_dir, mode="conservative")
            if apply_session(apply_args) != 0:
                failures.append(f"{session.name}:apply_failed")
        report = read_json(session_profile_dir(session) / "local_speech_completion_profile_report.json") or {}
        session_dispositions = read_jsonl(session_profile_dir(session) / "local_speech_completion_dispositions.jsonl")
        dispositions.extend(session_dispositions)
        if (report.get("gates") or {}).get("passed") is not True:
            failures.append(f"{session.name}:profile_gates_failed")
        sessions.append(
            {
                "session_id": session.name,
                "input_profile": record.get("input_profile"),
                "summary": report.get("summary"),
                "gates_passed": (report.get("gates") or {}).get("passed") is True,
                "output_fingerprint": report.get("output_fingerprint"),
            }
        )
    local_queue = [row for row in queue if row.get("kind") == "local_recall"]
    local_by_id = {str(row.get("queue_id")): row for row in local_queue}
    local_closed = [
        row
        for row in dispositions
        if row.get("kind") == "local_recall" and row.get("closed") is True
    ]
    closed_ids = {str(row.get("queue_id")) for row in local_closed}
    local_seconds = round(sum(duration(row) for row in local_queue), 3)
    closed_seconds = round(sum(duration(local_by_id[value]) for value in closed_ids if value in local_by_id), 3)
    closed_row_ratio = len(local_closed) / max(1, len(local_queue))
    closed_second_ratio = closed_seconds / max(0.001, local_seconds)
    useful = closed_row_ratio >= MIN_CLOSED_RATIO and closed_second_ratio >= MIN_CLOSED_RATIO
    if len(dispositions) != len(queue):
        failures.append(f"not_all_rows_have_dispositions:{len(dispositions)}/{len(queue)}")
    if not useful:
        failures.append("minimum_safe_completion_ratio_not_met")
    if args.synthesize and not failures:
        for record in baseline.get("sessions") or []:
            session = sessions_root / str(record.get("session_id"))
            if synthesize(session) != 0:
                failures.append(f"{session.name}:synthesis_failed")
    decision = PROMOTE_DECISION if not failures and args.synthesize else "DO_NOT_PROMOTE"
    promoted_sessions = [str(row.get("session_id")) for row in baseline.get("sessions") or []] if decision == PROMOTE_DECISION else []
    summary = {
        "queue_items": len(queue),
        "queue_seconds": round(sum(duration(row) for row in queue), 3),
        "local_recall_items": len(local_queue),
        "local_recall_seconds": local_seconds,
        "local_recall_closed_items": len(local_closed),
        "local_recall_closed_seconds": closed_seconds,
        "local_recall_closed_row_ratio": round(closed_row_ratio, 6),
        "local_recall_closed_second_ratio": round(closed_second_ratio, 6),
        "text_fragment_items": sum(1 for row in queue if row.get("kind") == "text_fragment"),
        "text_repaired_items": sum(1 for row in dispositions if row.get("outcome") == "text_repaired" and row.get("closed") is True),
        "remaining_items": sum(1 for row in dispositions if row.get("closed") is not True),
        "by_outcome": dict(sorted(Counter(str(row.get("outcome")) for row in dispositions).items())),
    }
    evidence_ceiling = [
        {
            "session_id": row.get("session_id"),
            "queue_id": row.get("queue_id"),
            "outcome": row.get("outcome"),
            "reason": row.get("reason"),
        }
        for row in dispositions
        if row.get("closed") is not True
    ]
    report = {
        "schema": "murmurmark.local_speech_completion_corpus_report/v2",
        "generator": {"name": "local-speech-completion-v2", "version": SCRIPT_VERSION},
        "output_profile": PROFILE,
        "decision": decision,
        "baseline_sha256": sha256_file(baseline_path),
        "queue_sha256": sha256_file(queue_path),
        "queue_fingerprint": fingerprint(queue),
        "summary": summary,
        "sessions": sessions,
        "promoted_sessions": promoted_sessions,
        "gates": {
            "passed": not failures,
            "hard_failures": failures,
            "minimum_closed_ratio": MIN_CLOSED_RATIO,
            "usefulness_threshold_passed": useful,
        },
        "evidence_ceiling": evidence_ceiling,
    }
    report["decision_fingerprint"] = fingerprint({key: value for key, value in report.items() if key != "decision_fingerprint"})
    write_json(out_dir / "local_speech_completion_corpus_report.json", report)
    write_json(
        out_dir / "local_speech_completion_decision.json",
        {
            "schema": "murmurmark.local_speech_completion_decision/v2",
            "decision": decision,
            "profile": PROFILE,
            "selected_profile": PROFILE if decision == PROMOTE_DECISION else None,
            "decision_fingerprint": report["decision_fingerprint"],
            "reason": "all_gates_passed" if decision == PROMOTE_DECISION else failures,
        },
    )
    lines = [
        "# Local Speech Completion v2",
        "",
        f"Decision: `{decision}`",
        f"Local recall closed: `{len(local_closed)}/{len(local_queue)}` rows, `{closed_seconds}/{local_seconds}` sec",
        f"Text repaired: `{summary['text_repaired_items']}/{summary['text_fragment_items']}`",
        f"Remaining: `{summary['remaining_items']}`",
        "",
        "## Evidence ceiling",
        "",
    ]
    lines.extend(f"- `{row['session_id']}` `{row['queue_id']}`: `{row['outcome']}` - {row['reason']}" for row in evidence_ceiling)
    (out_dir / "local_speech_completion_corpus_report.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    print(f"decision: {decision}")
    print(f"local_recall_closed: {len(local_closed)}/{len(local_queue)} rows; {closed_seconds}/{local_seconds}s")
    return 0


def run_all(args: argparse.Namespace) -> int:
    freeze_args = argparse.Namespace(
        sessions=args.sessions,
        sessions_root=args.sessions_root,
        out_dir=args.out_dir,
        force=args.force_freeze,
    )
    if freeze_corpus(freeze_args) != 0:
        return 2
    evidence_args = argparse.Namespace(
        sessions=[],
        sessions_root=args.sessions_root,
        out_dir=args.out_dir,
        model=args.model,
        device=args.device,
        compute_type=args.compute_type,
        language=args.language,
        beam_size=args.beam_size,
        padding_sec=args.padding_sec,
        allow_download=args.allow_download,
        no_cache=args.no_cache,
    )
    if build_evidence(evidence_args) != 0:
        return 2
    evaluate_args = argparse.Namespace(
        sessions_root=args.sessions_root,
        out_dir=args.out_dir,
        apply=True,
        synthesize=not args.skip_synthesis,
    )
    return evaluate_corpus(evaluate_args)


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
