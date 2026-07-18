#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import math
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any


SCRIPT_VERSION = "0.1.0"
PROFILE = "residual_audio_arbitration_v1"
INPUT_PROFILE = "residual_me_evidence_v1"
REPORT_DIR_NAME = "residual-audio-arbitration-v1"
PRIOR_REPORT_DIR_NAME = "residual-me-evidence-v1"
DEFAULT_MODEL = Path.home() / ".local/share/murmurmark/models/faster-whisper/large-v3"
OUTCOMES = {
    "genuine_me",
    "remote_duplicate_or_leak",
    "asr_noise",
    "real_double_talk",
    "insufficient_evidence",
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
    if not spec or not spec.loader:
        raise RuntimeError(f"cannot load sibling module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


BASE = load_sibling("residual-me-evidence.py", "murmurmark_residual_audio_base")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Arbitrate the residual audio-review queue with calibrated local evidence."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def common(command: argparse.ArgumentParser) -> None:
        command.add_argument("--sessions-root", type=Path, default=Path("sessions"))
        command.add_argument("--out-dir", type=Path)

    freeze = subparsers.add_parser("freeze", help="Freeze the promoted residual audio-review queue.")
    common(freeze)
    freeze.add_argument("--force", action="store_true")

    evidence = subparsers.add_parser("evidence", help="Build calibrated evidence for frozen rows.")
    common(evidence)
    evidence.add_argument("sessions", nargs="*", type=Path)
    add_model_args(evidence)

    apply = subparsers.add_parser("apply", help="Apply safe whole-Me decisions to one session.")
    common(apply)
    apply.add_argument("session", type=Path)
    apply.add_argument("--mode", choices=("conservative", "dry-run"), default="conservative")

    evaluate = subparsers.add_parser("evaluate", help="Evaluate the candidate profile on the corpus.")
    common(evaluate)
    evaluate.add_argument("--apply", action="store_true")
    evaluate.add_argument("--synthesize", action="store_true")

    run = subparsers.add_parser("run", help="Freeze, build evidence, apply, synthesize and evaluate.")
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
    parser.add_argument("--padding-sec", type=float, default=0.4)
    parser.add_argument("--allow-download", action="store_true")
    parser.add_argument("--no-cache", action="store_true")


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return default if math.isnan(number) or math.isinf(number) else number


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


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str | None:
    return BASE.sha256_file(path)


def interval(row: dict[str, Any]) -> tuple[float, float]:
    return BASE.interval(row)


def duration(row: dict[str, Any]) -> float:
    return BASE.duration(row)


def profile_paths(session: Path, profile: str) -> dict[str, Path]:
    return BASE.profile_paths(session, profile)


def report_dir(sessions_root: Path, explicit: Path | None) -> Path:
    return explicit.expanduser().resolve() if explicit else sessions_root / "_reports" / REPORT_DIR_NAME


def profile_dir(session: Path) -> Path:
    return session / "derived/transcript-simple/whisper-cpp/residual-audio-arbitration-v1"


def audit_dir(session: Path) -> Path:
    return session / "derived/audit/residual-audio-arbitration-v1"


def raw_fingerprints(session: Path) -> list[dict[str, Any]]:
    return BASE.raw_fingerprints(session)


def artifact_fingerprints(session: Path, profile: str) -> dict[str, Any]:
    return BASE.artifact_fingerprints(session, profile)


def output_fingerprint(paths: dict[str, Path]) -> str:
    return BASE.output_fingerprint(paths)


def role_name(row: dict[str, Any]) -> str:
    return BASE.role_name(row)


def model_path(args: argparse.Namespace) -> Path:
    if getattr(args, "model", None):
        return args.model.expanduser()
    configured = os.environ.get("MURMURMARK_FASTER_WHISPER_MODEL")
    return Path(configured).expanduser() if configured else DEFAULT_MODEL


class LazyWhisperModel:
    def __init__(self, path: Path, args: argparse.Namespace) -> None:
        self.path = path
        self.args = args
        self._model: Any | None = None

    def transcribe(self, *args: Any, **kwargs: Any) -> Any:
        if self._model is None:
            self._model = stronger_module().load_model(self.path, self.args)
        return self._model.transcribe(*args, **kwargs)


def transcribe_clip_with_words(
    session: Path,
    model: Any | None,
    clip: Path,
    args: argparse.Namespace,
) -> dict[str, Any]:
    clip_sha = sha256_file(clip)
    identity = {
        "model": str(model_path(args)),
        "device": args.device,
        "compute_type": args.compute_type,
        "language": args.language,
        "beam_size": args.beam_size,
        "word_timestamps": True,
        "clip_sha256": clip_sha,
    }
    cache_key = sha256_bytes(canonical_bytes(identity))
    cache_path = audit_dir(session) / "micro-asr-cache" / f"{cache_key}.json"
    if not args.no_cache:
        cached = read_json(cache_path)
        if isinstance(cached, dict) and cached.get("identity") == identity:
            return cached
    if model is None:
        return {
            "identity": identity,
            "path": str(clip),
            "exists": clip.exists(),
            "text": "",
            "segments": [],
            "error": "faster_whisper_model_unavailable",
        }
    if not clip.exists() or clip.stat().st_size <= 0:
        return {
            "identity": identity,
            "path": str(clip),
            "exists": False,
            "text": "",
            "segments": [],
            "error": "clip_missing_or_empty",
        }
    try:
        segments, info = model.transcribe(
            str(clip),
            language=args.language,
            beam_size=args.beam_size,
            temperature=0.0,
            vad_filter=False,
            condition_on_previous_text=False,
            word_timestamps=True,
        )
        rows: list[dict[str, Any]] = []
        for segment in segments:
            words = [
                {
                    "start": round(safe_float(getattr(word, "start", None)), 3),
                    "end": round(safe_float(getattr(word, "end", None)), 3),
                    "word": str(getattr(word, "word", "") or ""),
                    "probability": round(safe_float(getattr(word, "probability", None)), 6),
                }
                for word in (getattr(segment, "words", None) or [])
            ]
            rows.append(
                {
                    "start": round(safe_float(getattr(segment, "start", None)), 3),
                    "end": round(safe_float(getattr(segment, "end", None)), 3),
                    "text": str(getattr(segment, "text", "") or "").strip(),
                    "avg_logprob": round(safe_float(getattr(segment, "avg_logprob", None)), 6),
                    "no_speech_prob": round(safe_float(getattr(segment, "no_speech_prob", None)), 6),
                    "words": words,
                }
            )
        result = {
            "identity": identity,
            "path": str(clip),
            "exists": True,
            "text": " ".join(row["text"] for row in rows if row.get("text")).strip(),
            "segments": rows,
            "segment_count": len(rows),
            "language": getattr(info, "language", args.language),
            "language_probability": round(
                safe_float(getattr(info, "language_probability", None)), 6
            ),
        }
    except Exception as error:
        result = {
            "identity": identity,
            "path": str(clip),
            "exists": True,
            "text": "",
            "segments": [],
            "error": str(error),
        }
    write_json(cache_path, result)
    return result


def percentile(values: list[float], percent: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percent / 100.0
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - position) + ordered[upper] * (position - lower)


def freeze_corpus(args: argparse.Namespace) -> int:
    sessions_root = args.sessions_root.expanduser().resolve()
    out_dir = report_dir(sessions_root, args.out_dir)
    manifest_path = out_dir / "baseline_manifest.json"
    if manifest_path.exists() and not args.force:
        print(f"baseline_manifest: {manifest_path}")
        print("status: already_frozen")
        return 0

    prior_dir = sessions_root / "_reports" / PRIOR_REPORT_DIR_NAME
    prior_report = read_json(prior_dir / "residual_me_corpus_report.json")
    prior_manifest = read_json(prior_dir / "baseline_manifest.json")
    prior_queue = read_jsonl(prior_dir / "residual_queue.jsonl")
    if (
        not prior_report
        or prior_report.get("decision") != "PROMOTE_RESIDUAL_ME_EVIDENCE_V1"
        or not prior_manifest
        or not prior_queue
    ):
        print("status: residual_me_evidence_not_promoted", file=sys.stderr)
        return 2

    prior_by_id = {str(row.get("residual_queue_id") or ""): row for row in prior_queue}
    session_ids = sorted(str(value) for value in prior_report.get("promoted_sessions") or [])
    frozen: list[dict[str, Any]] = []
    sessions: list[dict[str, Any]] = []
    failures: list[str] = []
    local_count = 0
    order_count = 0
    for session_id in session_ids:
        session = sessions_root / session_id
        input_queue_path = profile_dir_for_input(session) / "residual_me_review_queue.jsonl"
        input_queue = read_jsonl(input_queue_path)
        audio_rows = [row for row in input_queue if str(row.get("source") or "") == "audio_review"]
        local_rows = [row for row in input_queue if str(row.get("source") or "") == "local_recall"]
        order_rows = [row for row in input_queue if str(row.get("source") or "") == "transcript_order"]
        local_count += len(local_rows)
        order_count += len(order_rows)
        for disposition in audio_rows:
            queue_id = str(disposition.get("residual_queue_id") or "")
            source = prior_by_id.get(queue_id)
            if not source:
                failures.append(f"{session_id}:{queue_id}:missing_prior_frozen_row")
                continue
            row = copy.deepcopy(source)
            row["schema"] = "murmurmark.residual_audio_arbitration_queue_item/v1"
            row["input_disposition"] = copy.deepcopy(disposition)
            row["input_disposition_sha256"] = sha256_bytes(canonical_bytes(disposition))
            frozen.append(row)
        paths = profile_paths(session, INPUT_PROFILE)
        if not paths["dialogue"].exists():
            failures.append(f"{session_id}:missing_input_dialogue")
        sessions.append(
            {
                "session_id": session_id,
                "session": str(session),
                "input_profile": INPUT_PROFILE,
                "artifacts": artifact_fingerprints(session, INPUT_PROFILE),
                "raw_capture": raw_fingerprints(session),
                "input_review_queue": {
                    "path": str(input_queue_path),
                    "sha256": sha256_file(input_queue_path),
                    "audio_review_count": len(audio_rows),
                    "local_recall_count": len(local_rows),
                    "local_recall_sha256": sha256_bytes(canonical_bytes(local_rows)),
                    "transcript_order_count": len(order_rows),
                    "transcript_order_sha256": sha256_bytes(canonical_bytes(order_rows)),
                },
            }
        )
        print(f"freeze: {session_id}: {len(audio_rows)} audio rows", flush=True)

    frozen.sort(key=lambda row: (str(row.get("session_id") or ""), str(row.get("residual_queue_id") or "")))
    queue_sha = sha256_bytes(canonical_bytes(frozen))
    manifest = {
        "schema": "murmurmark.residual_audio_arbitration_baseline/v1",
        "generator": {"name": "residual-audio-arbitration", "version": SCRIPT_VERSION},
        "input_profile": INPUT_PROFILE,
        "output_profile": PROFILE,
        "source_profile": {
            "report": str(prior_dir / "residual_me_corpus_report.json"),
            "report_sha256": sha256_file(prior_dir / "residual_me_corpus_report.json"),
            "decision": prior_report.get("decision"),
            "baseline_manifest_sha256": sha256_file(prior_dir / "baseline_manifest.json"),
            "queue_sha256": (prior_manifest.get("queue") or {}).get("sha256"),
        },
        "scope": {"session_count": len(sessions), "session_ids": session_ids},
        "queue": {
            "item_count": len(frozen),
            "seconds": round(sum(duration(row) for row in frozen), 3),
            "sha256": queue_sha,
            "source": "audio_review",
        },
        "preserved_residual_queues": {
            "local_recall_count": local_count,
            "transcript_order_count": order_count,
        },
        "sessions": sessions,
        "gates": {"passed": not failures, "hard_failures": failures},
    }
    write_json(out_dir / "baseline_manifest.json", manifest)
    write_jsonl(out_dir / "residual_audio_queue.jsonl", frozen)
    print(f"baseline_manifest: {manifest_path}")
    print(f"residual_audio_queue: {out_dir / 'residual_audio_queue.jsonl'}")
    print(f"items: {len(frozen)}")
    print(f"seconds: {manifest['queue']['seconds']}")
    return 0 if not failures else 2


def profile_dir_for_input(session: Path) -> Path:
    return session / "derived/transcript-simple/whisper-cpp/residual-me-evidence-v1"


def enrollment_args() -> SimpleNamespace:
    return SimpleNamespace(
        method="resemblyzer_dvector",
        wavlm_model=None,
        max_enrollment_segments=16,
        max_enrollment_total_sec=90.0,
        max_negative_enrollment_segments=0,
        min_enrollment_sec=1.2,
        max_enrollment_sec=14.0,
        min_enrollment_local_ratio=0.65,
        max_enrollment_remote_active_ratio=0.20,
        padding_sec=0.15,
        write_clips=True,
    )


def select_remote_negative_rows(
    utterances: list[dict[str, Any]], state_rows: list[dict[str, Any]], limit: int = 16
) -> list[dict[str, Any]]:
    candidates: list[tuple[float, dict[str, Any]]] = []
    for row in utterances:
        if role_name(row) != "Colleagues":
            continue
        quality = row.get("quality") if isinstance(row.get("quality"), dict) else {}
        start = safe_float(row.get("start"))
        end = safe_float(row.get("end"), start)
        item_duration = end - start
        if quality.get("needs_review") is True or item_duration < 1.2 or item_duration > 14.0:
            continue
        if len(BASE.content_tokens(str(row.get("text") or ""), stronger_module())) < 2:
            continue
        state = BASE.target_module().interval_state_features(state_rows, start, end)
        local = safe_float(state.get("local_only_ratio")) + safe_float(state.get("double_talk_ratio"))
        remote = safe_float(state.get("remote_active_ratio"))
        if local > 0.10 or remote < 0.55:
            continue
        score = remote * 100.0 - local * 120.0 + min(item_duration, 8.0)
        candidates.append((score, row))
    candidates.sort(key=lambda item: (-item[0], safe_float(item[1].get("start"))))
    return [row for _score, row in candidates[:limit]]


_STRONGER: ModuleType | None = None


def stronger_module() -> ModuleType:
    global _STRONGER
    if _STRONGER is None:
        _STRONGER = load_sibling("audit-stronger-audio-judge.py", "murmurmark_residual_audio_stronger")
    return _STRONGER


def build_calibration(
    session: Path,
    utterances: list[dict[str, Any]],
    state_rows: list[dict[str, Any]],
    backend: Any,
    backend_status: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    output = audit_dir(session) / "target-me"
    output.mkdir(parents=True, exist_ok=True)
    BASE.target_module().now_iso = lambda: "deterministic"
    enrollment, target_model = BASE.target_module().build_enrollment(
        session,
        INPUT_PROFILE,
        utterances,
        state_rows,
        output,
        backend,
        backend_status,
        enrollment_args(),
    )
    positive_embeddings: list[Any] = []
    for row in enrollment.get("segments") or []:
        clip = Path(str(row.get("clip") or ""))
        embedding, _info = backend.embed(clip)
        if embedding is not None:
            positive_embeddings.append(embedding)
    positive_scores: list[float] = []
    for index, embedding in enumerate(positive_embeddings):
        reference = BASE.target_module().make_centroid(
            [value for offset, value in enumerate(positive_embeddings) if offset != index]
        )
        if reference is None:
            continue
        leave_one_out_model = {"positive_centroid": reference, "negative_centroid": None, "scoring": "cosine"}
        positive_scores.append(
            safe_float(BASE.target_module().model_score(embedding, leave_one_out_model).get("positive_similarity"))
        )

    remote_source = BASE.audio_sources(session)["remote"]
    negative_rows = select_remote_negative_rows(utterances, state_rows)
    negative_records: list[dict[str, Any]] = []
    negative_scores: list[float] = []
    for index, row in enumerate(negative_rows, start=1):
        start = safe_float(row.get("start"))
        end = safe_float(row.get("end"), start)
        clip = output / "clips/remote-negative" / f"remote_{index:04d}_{row.get('id')}.wav"
        if not BASE.extract_wav(remote_source, clip, start, end):
            continue
        embedding, info = backend.embed(clip)
        if embedding is None:
            continue
        score = safe_float(BASE.target_module().model_score(embedding, target_model).get("positive_similarity"))
        negative_scores.append(score)
        negative_records.append(
            {
                "utterance_id": row.get("id"),
                "start": round(start, 3),
                "end": round(end, 3),
                "text": row.get("text"),
                "clip": str(clip),
                "clip_sha256": sha256_file(clip),
                "target_similarity": round(score, 6),
                "embedding_info": info,
            }
        )

    positive_p10 = percentile(positive_scores, 10)
    positive_p50 = percentile(positive_scores, 50)
    negative_p75 = percentile(negative_scores, 75)
    negative_p90 = percentile(negative_scores, 90)
    keep_threshold = max((positive_p10 + negative_p90) / 2.0, negative_p90 + 0.04)
    weak_threshold = max(negative_p75 + 0.01, keep_threshold - 0.08)
    margin = positive_p50 - negative_p90
    reliable = (
        enrollment.get("status") == "ready"
        and len(positive_scores) >= 3
        and len(negative_scores) >= 3
        and margin >= 0.08
        and positive_p10 >= negative_p75 + 0.04
    )
    calibration = {
        "schema": "murmurmark.target_me_session_calibration/v1",
        "session_id": session.name,
        "status": "reliable" if reliable else "weak",
        "backend": backend_status,
        "positive": {
            "count": len(positive_scores),
            "p10": round(positive_p10, 6),
            "p50": round(positive_p50, 6),
            "min": round(min(positive_scores), 6) if positive_scores else None,
            "max": round(max(positive_scores), 6) if positive_scores else None,
        },
        "remote_negative": {
            "count": len(negative_scores),
            "p50": round(percentile(negative_scores, 50), 6),
            "p75": round(negative_p75, 6),
            "p90": round(negative_p90, 6),
            "min": round(min(negative_scores), 6) if negative_scores else None,
            "max": round(max(negative_scores), 6) if negative_scores else None,
            "segments": negative_records,
        },
        "thresholds": {
            "keep": round(keep_threshold, 6),
            "weak": round(weak_threshold, 6),
            "positive_p50_minus_negative_p90": round(margin, 6),
        },
        "fail_open_reasons": [
            reason
            for condition, reason in (
                (enrollment.get("status") != "ready", "positive_enrollment_not_ready"),
                (len(positive_scores) < 3, "fewer_than_three_positive_examples"),
                (len(negative_scores) < 3, "fewer_than_three_remote_negative_examples"),
                (margin < 0.08, "positive_remote_negative_margin_below_0_08"),
                (positive_p10 < negative_p75 + 0.04, "positive_and_remote_negative_distributions_overlap"),
            )
            if condition
        ],
    }
    enrollment["created_at"] = "deterministic"
    write_json(output / "target_me_enrollment.json", enrollment)
    write_json(output / "target_me_session_calibration.json", calibration)
    return calibration, target_model


def me_utterance(queue_row: dict[str, Any], utterance_by_id: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    ids = [str(value) for value in queue_row.get("me_utterance_ids") or [] if value]
    return utterance_by_id.get(ids[0]) if len(ids) == 1 else None


def speaker_bounded_interval(
    state_rows: list[dict[str, Any]], whole_start: float, whole_end: float, audit_start: float, audit_end: float
) -> tuple[float, float] | None:
    islands: list[list[tuple[float, float, str]]] = []
    current: list[tuple[float, float, str]] = []
    for row in state_rows:
        start = max(whole_start, safe_float(row.get("start")))
        end = min(whole_end, safe_float(row.get("end"), start))
        state = str(row.get("state") or "")
        if end <= start or state not in {"local_only", "double_talk"}:
            continue
        if current and start - current[-1][1] > 0.12:
            islands.append(current)
            current = []
        current.append((start, end, state))
    if current:
        islands.append(current)
    if not islands:
        return None

    def score(island: list[tuple[float, float, str]]) -> float:
        start = island[0][0]
        end = island[-1][1]
        overlap = max(0.0, min(end, audit_end) - max(start, audit_start))
        local = sum(b - a for a, b, state in island if state == "local_only")
        return overlap * 10.0 + local + (end - start) * 0.1

    selected = max(islands, key=score)
    start = max(whole_start, selected[0][0] - 0.15)
    end = min(whole_end, selected[-1][1] + 0.20)
    return (start, end) if end - start >= 0.35 else None


def extract_interval_clips(
    session: Path,
    queue_id: str,
    label: str,
    start: float,
    end: float,
) -> tuple[dict[str, Path], list[str]]:
    exact: dict[str, Path] = {}
    failures: list[str] = []
    sources = BASE.audio_sources(session)
    clip_root = audit_dir(session) / "clips" / queue_id
    for source, source_path in sources.items():
        if not source_path.exists():
            failures.append(f"{label}:{source}:missing_source")
            continue
        exact_path = clip_root / f"{label}_{source}.wav"
        if BASE.extract_wav(source_path, exact_path, start, end):
            exact[source] = exact_path
        else:
            failures.append(f"{label}:{source}:exact_extract_failed")
    return exact, failures


def score_clip(backend: Any, clip: Path, target_model: dict[str, Any]) -> dict[str, Any]:
    embedding, info = backend.embed(clip)
    result = dict(info)
    result.update(BASE.target_module().model_score(embedding, target_model))
    return result


def text_similarity(left: str, right: str) -> dict[str, Any]:
    return stronger_module().text_similarity(left, right)


def normalized(text: str) -> str:
    return stronger_module().normalize_text(text)


def is_hallucination(text: str) -> bool:
    value = normalized(text)
    return any(marker in value for marker in KNOWN_HALLUCINATIONS)


def content_tokens(text: str) -> list[str]:
    return BASE.content_tokens(text, stronger_module())


def load_judges(session: Path) -> dict[str, dict[str, Any]]:
    path = session / "derived/audit/audio-review-pack/faster_whisper_judge.jsonl"
    return {str(row.get("source_pack_item_id") or ""): row for row in read_jsonl(path)}


def notes_referenced_ids(session: Path) -> set[str]:
    payload = read_json(profile_paths(session, INPUT_PROFILE)["evidence_notes"]) or {}
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

    selected = payload.get("selected") if isinstance(payload.get("selected"), dict) else {}
    visit(selected)
    return result


def best_transcript(
    transcripts: dict[str, dict[str, Any]], target_text: str
) -> tuple[str, str, dict[str, Any]]:
    choices: list[tuple[float, str, str, dict[str, Any]]] = []
    for key, value in transcripts.items():
        if not key.startswith(("mic_clean:", "mic_raw:", "mic_role_masked:")):
            continue
        text = str(value.get("text") or "").strip()
        if not text or is_hallucination(text):
            continue
        metric = text_similarity(text, target_text)
        score = safe_float(metric.get("similarity")) + safe_float(metric.get("containment")) * 0.35
        choices.append((score, key, text, metric))
    if not choices:
        return "", "", text_similarity("", target_text)
    _score, key, text, metric = max(choices)
    return key, text, metric


def classify(
    queue_row: dict[str, Any],
    *,
    calibration: dict[str, Any],
    voice_scores: dict[str, dict[str, Any]],
    state: dict[str, Any],
    transcripts: dict[str, dict[str, Any]],
    judge: dict[str, Any] | None,
    notes_ids: set[str],
) -> dict[str, Any]:
    target_text = str(queue_row.get("target_text") or "").strip()
    me_ids = [str(value) for value in queue_row.get("me_utterance_ids") or [] if value]
    detail = queue_row.get("source_detail") if isinstance(queue_row.get("source_detail"), dict) else {}
    detail_class = detail.get("classification") if isinstance(detail.get("classification"), dict) else {}
    judge_class = judge.get("classification") if isinstance(judge, dict) and isinstance(judge.get("classification"), dict) else {}
    judge_label = str(judge_class.get("label") or "")
    judge_confidence = safe_float(judge_class.get("confidence"))
    selected_key, selected_text, selected_to_target = best_transcript(transcripts, target_text)
    remote_texts = [
        str(value.get("text") or "").strip()
        for key, value in transcripts.items()
        if key.startswith("remote:") and value.get("text") and not is_hallucination(str(value.get("text") or ""))
    ]
    remote_text = max(remote_texts, key=lambda value: safe_float(text_similarity(value, target_text).get("similarity")), default="")
    remote_to_target = text_similarity(remote_text, target_text)
    mic_to_remote = text_similarity(selected_text, remote_text)
    clean_text = str(
        (
            transcripts.get("mic_clean:bounded")
            or transcripts.get("mic_clean:whole")
            or transcripts.get("mic_clean:judge_context")
            or transcripts.get("mic_clean:exact")
            or {}
        ).get("text")
        or ""
    )
    raw_text = str(
        (
            transcripts.get("mic_raw:bounded")
            or transcripts.get("mic_raw:whole")
            or transcripts.get("mic_raw:judge_context")
            or transcripts.get("mic_raw:exact")
            or {}
        ).get("text")
        or ""
    )
    cross_mic = text_similarity(clean_text, raw_text)

    mic_keys = [key for key in voice_scores if key.startswith(("mic_clean:", "mic_raw:", "mic_role_masked:"))]
    remote_keys = [key for key in voice_scores if key.startswith("remote:")]
    best_mic_key = max(mic_keys, key=lambda key: safe_float(voice_scores[key].get("positive_similarity")), default="")
    best_mic = safe_float((voice_scores.get(best_mic_key) or {}).get("positive_similarity"))
    remote_score = max(
        (safe_float(voice_scores[key].get("positive_similarity")) for key in remote_keys),
        default=0.0,
    )
    thresholds = calibration.get("thresholds") if isinstance(calibration.get("thresholds"), dict) else {}
    keep_threshold = safe_float(thresholds.get("keep"), 1.0)
    weak_threshold = safe_float(thresholds.get("weak"), 1.0)
    calibration_reliable = (
        calibration.get("status") == "reliable"
        and calibration.get("required_evidence_ready", True) is True
    )
    voice_confirmed = calibration_reliable and best_mic >= keep_threshold and best_mic - remote_score >= 0.08
    voice_absent = calibration_reliable and best_mic < weak_threshold and remote_score >= best_mic + 0.05

    local_support = min(
        1.0,
        safe_float(state.get("local_only_ratio")) + 0.75 * safe_float(state.get("double_talk_ratio")),
    )
    remote_active = safe_float(state.get("remote_active_ratio"))
    double_talk = safe_float(state.get("double_talk_ratio"))
    mixed = double_talk >= 0.20 and local_support >= 0.20 and remote_active >= 0.20
    mic_target = (
        safe_float(selected_to_target.get("similarity")) >= 0.50
        and safe_float(selected_to_target.get("containment")) >= 0.50
    )
    remote_forbidden = (
        safe_float(remote_to_target.get("similarity")) <= 0.68
        and safe_float(remote_to_target.get("containment")) <= 0.60
        and safe_float(mic_to_remote.get("similarity")) <= 0.82
    )
    dual_mic = safe_float(cross_mic.get("similarity")) >= 0.72
    independent_keep = (
        judge_label in {"confirm_me", "confirm_timing_or_doubletalk"} and judge_confidence >= 0.85
    ) or (dual_mic and mic_target)
    target_set = set(content_tokens(target_text))
    remote_set = set(content_tokens(remote_text))
    unique_target = sorted(target_set - remote_set)
    protected = BASE.has_protected_marker(target_text, stronger_module()) or bool(set(me_ids) & notes_ids)
    detail_label = str(detail_class.get("label") or "")
    detail_verdict = str(detail_class.get("verdict") or "")
    detail_confidence = safe_float(detail_class.get("confidence"))
    duplicate_source = (
        detail_label in {"remote_duplicate", "remote_leak"}
        and detail_verdict == "probable_transcript_error"
        and detail_confidence >= 0.90
    ) or (judge_label == "confirm_remote_duplicate" and judge_confidence >= 0.90)
    noise_source = judge_label == "confirm_asr_noise" and judge_confidence >= 0.90
    exact_duplicate_text = (
        safe_float(remote_to_target.get("similarity")) >= 0.72
        and safe_float(remote_to_target.get("containment")) >= 0.75
        and safe_float(mic_to_remote.get("similarity")) >= 0.75
    )

    outcome = "insufficient_evidence"
    action = "needs_review"
    reason = "independent_evidence_is_weak_or_conflicting"
    confidence = max(judge_confidence, best_mic, remote_score)
    if mixed and (voice_confirmed or judge_label == "confirm_timing_or_doubletalk"):
        outcome = "real_double_talk"
        reason = "local_and_remote_speech_are_both_supported"
    elif (
        len(me_ids) == 1
        and voice_confirmed
        and local_support >= 0.45
        and mic_target
        and remote_forbidden
        and independent_keep
        and not is_hallucination(selected_text)
    ):
        outcome = "genuine_me"
        action = "keep_me"
        reason = "calibrated_target_me_and_remote_forbidden_micro_asr_confirm_local_speech"
        confidence = min(0.99, 0.35 * best_mic + 0.30 * safe_float(selected_to_target.get("similarity")) + 0.20 * local_support + 0.15)
    elif (
        len(me_ids) == 1
        and duplicate_source
        and voice_absent
        and local_support <= 0.20
        and exact_duplicate_text
        and not unique_target
        and not protected
    ):
        outcome = "remote_duplicate_or_leak"
        action = "drop_duplicate_or_noise"
        reason = "whole_me_is_remote_duplicate_confirmed_by_calibrated_voice_text_and_prior_judge"
        confidence = min(0.99, max(detail_confidence, judge_confidence) * 0.5 + safe_float(remote_to_target.get("similarity")) * 0.3 + 0.2)
    elif (
        len(me_ids) == 1
        and noise_source
        and voice_absent
        and local_support <= 0.12
        and safe_float(selected_to_target.get("similarity")) < 0.35
        and len(target_set) <= 2
        and not protected
    ):
        outcome = "asr_noise"
        action = "drop_duplicate_or_noise"
        reason = "whole_me_is_asr_noise_confirmed_by_judge_voice_absence_and_missing_mic_text"
        confidence = min(0.97, judge_confidence * 0.7 + 0.25)
    elif duplicate_source and exact_duplicate_text:
        outcome = "remote_duplicate_or_leak"
        reason = "remote_duplicate_is_probable_but_whole_me_drop_gates_did_not_pass"
    elif noise_source:
        outcome = "asr_noise"
        reason = "asr_noise_is_probable_but_whole_me_drop_gates_did_not_pass"

    return {
        "outcome": outcome,
        "action": action,
        "reason": reason,
        "confidence": round(confidence, 6),
        "checks": {
            "calibration_reliable": calibration_reliable,
            "voice_confirmed": voice_confirmed,
            "voice_absent": voice_absent,
            "best_mic_source": best_mic_key,
            "best_mic_target_similarity": round(best_mic, 6),
            "remote_target_voice_similarity": round(remote_score, 6),
            "voice_delta_vs_remote": round(best_mic - remote_score, 6),
            "local_support": round(local_support, 6),
            "remote_active_ratio": round(remote_active, 6),
            "double_talk_ratio": round(double_talk, 6),
            "mixed_speech": mixed,
            "mic_target_pass": mic_target,
            "remote_forbidden_pass": remote_forbidden,
            "independent_keep": independent_keep,
            "duplicate_source": duplicate_source,
            "noise_source": noise_source,
            "exact_duplicate_text": exact_duplicate_text,
            "protected_content": protected,
            "unique_target_tokens": unique_target,
            "selected_mic_transcript": {"source": selected_key, "text": selected_text},
            "remote_transcript": remote_text,
            "selected_to_target": selected_to_target,
            "remote_to_target": remote_to_target,
            "mic_to_remote": mic_to_remote,
            "cross_mic": cross_mic,
            "prior_audio_review": {
                "label": detail_label,
                "verdict": detail_verdict,
                "confidence": detail_confidence,
            },
            "stronger_audio_judge": {
                "label": judge_label,
                "confidence": judge_confidence,
            },
        },
    }


def build_session_evidence(
    session: Path,
    queue_rows: list[dict[str, Any]],
    manifest_record: dict[str, Any],
    model: Any,
    backend: Any | None,
    backend_status: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, Any]:
    input_paths = profile_paths(session, INPUT_PROFILE)
    dialogue = read_json(input_paths["dialogue"])
    frozen_sha = (((manifest_record.get("artifacts") or {}).get("dialogue") or {}).get("sha256"))
    if not dialogue or sha256_file(input_paths["dialogue"]) != frozen_sha:
        return {"session_id": session.name, "status": "failed_open", "error": "frozen_input_dialogue_mismatch"}
    utterances = [row for row in dialogue.get("utterances") or [] if isinstance(row, dict)]
    by_id = {str(row.get("id") or ""): row for row in utterances}
    state_rows = BASE.target_module().load_speaker_state(session)
    if backend is None:
        calibration = {
            "schema": "murmurmark.target_me_session_calibration/v1",
            "session_id": session.name,
            "status": "weak",
            "backend": backend_status,
            "positive": {"count": 0},
            "remote_negative": {"count": 0, "segments": []},
            "thresholds": {"keep": 1.0, "weak": 1.0, "positive_p50_minus_negative_p90": 0.0},
            "required_evidence_ready": False,
            "fail_open_reasons": ["target_me_backend_unavailable"],
        }
        target_model = {}
        write_json(audit_dir(session) / "target-me/target_me_session_calibration.json", calibration)
    else:
        calibration, target_model = build_calibration(session, utterances, state_rows, backend, backend_status)
        calibration["required_evidence_ready"] = model is not None
        if model is None:
            calibration["fail_open_reasons"] = list(calibration.get("fail_open_reasons") or []) + [
                "faster_whisper_model_unavailable"
            ]
        write_json(audit_dir(session) / "target-me/target_me_session_calibration.json", calibration)
    judges = load_judges(session)
    notes_ids = notes_referenced_ids(session)
    evidence_rows: list[dict[str, Any]] = []
    for index, queue_row in enumerate(queue_rows, start=1):
        queue_id = str(queue_row.get("residual_queue_id") or "")
        audit_start, audit_end = interval(queue_row)
        me_row = me_utterance(queue_row, by_id)
        whole_start = safe_float(me_row.get("start")) if me_row else audit_start
        whole_end = safe_float(me_row.get("end"), whole_start) if me_row else audit_end
        bounded = speaker_bounded_interval(state_rows, whole_start, whole_end, audit_start, audit_end)
        interval_specs = {"exact": (audit_start, audit_end), "whole": (whole_start, whole_end)}
        if bounded:
            interval_specs["bounded"] = bounded
        clips: dict[str, dict[str, Any]] = {}
        failures: list[str] = []
        voice_scores: dict[str, dict[str, Any]] = {}
        for label, (start, end) in interval_specs.items():
            exact_clips, interval_failures = extract_interval_clips(session, queue_id, label, start, end)
            failures.extend(interval_failures)
            for source, path in exact_clips.items():
                key = f"{source}:{label}"
                clips[key] = {"path": str(path), "sha256": sha256_file(path)}
                if backend is not None:
                    voice_scores[key] = score_clip(backend, path, target_model)

        transcripts: dict[str, dict[str, Any]] = {}
        prior_path = session / "derived/audit/residual-me-evidence-v1/residual_me_evidence.jsonl"
        prior = next(
            (row for row in read_jsonl(prior_path) if str(row.get("residual_queue_id") or "") == queue_id),
            None,
        )
        if isinstance(prior, dict):
            for source, value in (prior.get("micro_asr") or {}).items():
                transcripts.setdefault(f"{source}:exact", value)
        judge = judges.get(str(queue_row.get("source_audit_id") or ""))
        if isinstance(judge, dict):
            for source, value in (judge.get("transcripts") or {}).items():
                if isinstance(value, dict):
                    transcripts.setdefault(f"{source}:judge_context", value)
        for key, clip_info in clips.items():
            if not (key.endswith(":exact") or key.endswith(":bounded")):
                continue
            if key in transcripts:
                continue
            clip_path = Path(str(clip_info.get("path") or ""))
            transcripts[key] = transcribe_clip_with_words(session, model, clip_path, args)
        state = BASE.target_module().interval_state_features(state_rows, whole_start, whole_end)
        decision = classify(
            queue_row,
            calibration=calibration,
            voice_scores=voice_scores,
            state=state,
            transcripts=transcripts,
            judge=judge,
            notes_ids=notes_ids,
        )
        evidence = {
            "schema": "murmurmark.residual_audio_arbitration_evidence/v1",
            "session_id": session.name,
            "residual_queue_id": queue_id,
            "source_audit_id": queue_row.get("source_audit_id"),
            "intervals": {
                label: {"start": round(start, 3), "end": round(end, 3), "duration_sec": round(end - start, 3)}
                for label, (start, end) in interval_specs.items()
            },
            "utterance_ids": queue_row.get("utterance_ids") or [],
            "me_utterance_ids": queue_row.get("me_utterance_ids") or [],
            "remote_utterance_ids": queue_row.get("remote_utterance_ids") or [],
            "target_text": queue_row.get("target_text") or "",
            "remote_text": queue_row.get("remote_text") or "",
            "calibration": calibration,
            "clips": clips,
            "clip_failures": failures,
            "voice_scores": voice_scores,
            "speaker_state": state,
            "micro_asr": transcripts,
            "stronger_audio_judge": judge,
            "faster_whisper_provenance": {
                "mode": "frozen_exact_and_stronger_judge_reuse",
                "exact_word_timestamps_present": any(
                    any(segment.get("words") for segment in value.get("segments") or [])
                    for key, value in transcripts.items()
                    if key.endswith(":exact") and isinstance(value, dict)
                ),
                "judge_fingerprint": judge.get("source_pack_item_fingerprint") if isinstance(judge, dict) else None,
                "model": next(
                    (
                        ((value.get("identity") or {}).get("model"))
                        for key, value in transcripts.items()
                        if key.endswith(":exact") and isinstance(value, dict) and (value.get("identity") or {}).get("model")
                    ),
                    None,
                ),
            },
            "prior_residual_evidence": {
                "path": str(prior_path),
                "sha256": sha256_file(prior_path),
                "row_sha256": sha256_bytes(canonical_bytes(prior)) if prior else None,
            },
            "decision": decision,
        }
        evidence["provenance_sha256"] = sha256_bytes(canonical_bytes(evidence))
        evidence_rows.append(evidence)
        print(
            f"evidence: {session.name}: {index}/{len(queue_rows)}: "
            f"{decision['outcome']}:{decision['action']}",
            flush=True,
        )
    evidence_path = audit_dir(session) / "residual_audio_evidence.jsonl"
    write_jsonl(evidence_path, evidence_rows)
    summary = summarize_evidence(session, evidence_rows, calibration, manifest_record)
    write_json(audit_dir(session) / "residual_audio_evidence_summary.json", summary)
    return summary


def summarize_evidence(
    session: Path,
    rows: list[dict[str, Any]],
    calibration: dict[str, Any],
    manifest_record: dict[str, Any],
) -> dict[str, Any]:
    by_outcome: dict[str, dict[str, Any]] = {}
    by_action: dict[str, dict[str, Any]] = {}
    for row in rows:
        item_seconds = safe_float(((row.get("intervals") or {}).get("exact") or {}).get("duration_sec"))
        decision = row.get("decision") if isinstance(row.get("decision"), dict) else {}
        for buckets, key in (
            (by_outcome, str(decision.get("outcome") or "unknown")),
            (by_action, str(decision.get("action") or "unknown")),
        ):
            bucket = buckets.setdefault(key, {"count": 0, "seconds": 0.0})
            bucket["count"] += 1
            bucket["seconds"] += item_seconds
    for bucket in list(by_outcome.values()) + list(by_action.values()):
        bucket["seconds"] = round(bucket["seconds"], 3)
    expected = safe_int(((manifest_record.get("input_review_queue") or {}).get("audio_review_count")))
    return {
        "schema": "murmurmark.residual_audio_arbitration_evidence_summary/v1",
        "generator": {"name": "residual-audio-arbitration", "version": SCRIPT_VERSION},
        "session_id": session.name,
        "input_profile": INPUT_PROFILE,
        "queue_items": len(rows),
        "queue_seconds": round(
            sum(safe_float(((row.get("intervals") or {}).get("exact") or {}).get("duration_sec")) for row in rows), 3
        ),
        "calibration": calibration,
        "by_outcome": dict(sorted(by_outcome.items())),
        "by_action": dict(sorted(by_action.items())),
        "gates": {
            "passed": len(rows) == expected and all((row.get("decision") or {}).get("outcome") in OUTCOMES for row in rows),
            "hard_failures": [] if len(rows) == expected else ["not_all_frozen_rows_have_evidence"],
        },
    }


def build_evidence(args: argparse.Namespace) -> int:
    sessions_root = args.sessions_root.expanduser().resolve()
    out_dir = report_dir(sessions_root, args.out_dir)
    manifest = read_json(out_dir / "baseline_manifest.json")
    queue = read_jsonl(out_dir / "residual_audio_queue.jsonl")
    if not manifest or not queue or (manifest.get("gates") or {}).get("passed") is not True:
        print("status: missing_or_failed_frozen_baseline", file=sys.stderr)
        return 2
    if not args.sessions:
        session_ids = sorted({str(row.get("session_id") or "") for row in queue})
        for session_id in session_ids:
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
                "--padding-sec",
                str(args.padding_sec),
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
    resolved_model = model_path(args)
    model_bin = resolved_model / "model.bin" if resolved_model.is_dir() else resolved_model
    model_available = model_bin.exists()
    if not model_available:
        print(f"warning: faster_whisper_model_missing: {resolved_model}; decisions fail open", file=sys.stderr)
    if not args.allow_download:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
    print(f"evidence: faster-whisper model: {resolved_model}", flush=True)
    model = LazyWhisperModel(resolved_model, args) if model_available else None
    backend, backend_status = BASE.target_module().resolve_embedding_backend(
        SimpleNamespace(method="resemblyzer_dvector", wavlm_model=None)
    )
    ready, reason = backend.ready()
    if not ready:
        print(f"warning: target_me_backend_unavailable: {reason}; decisions fail open", file=sys.stderr)
        backend = None
        backend_status = {**backend_status, "ready": False, "reason": reason}
    wanted = {path.expanduser().resolve().name for path in args.sessions} if args.sessions else set()
    status = 0
    print(
        f"evidence: frozen records={len(manifest.get('sessions') or [])} queue_rows={len(queue)} "
        f"selected={','.join(sorted(wanted)) if wanted else 'all'}",
        flush=True,
    )
    for record in manifest.get("sessions") or []:
        session_id = str(record.get("session_id") or "")
        if wanted and session_id not in wanted:
            continue
        session_rows = [row for row in queue if str(row.get("session_id") or "") == session_id]
        if not session_rows:
            continue
        summary = build_session_evidence(
            sessions_root / session_id,
            session_rows,
            record,
            model,
            backend,
            backend_status,
            args,
        )
        if (summary.get("gates") or {}).get("passed") is not True:
            status = 2
    return status


def raw_capture_matches(record: dict[str, Any]) -> tuple[bool, list[str]]:
    failures: list[str] = []
    for item in record.get("raw_capture") or []:
        path = Path(str(item.get("path") or ""))
        if (
            not path.exists()
            or path.stat().st_size != safe_int(item.get("size"))
            or sha256_file(path) != item.get("sha256")
        ):
            failures.append(str(path))
    return not failures, failures


def queue_subsets(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    return (
        [row for row in rows if str(row.get("source") or "") == "local_recall"],
        [row for row in rows if str(row.get("source") or "") == "transcript_order"],
    )


def annotate_utterance(row: dict[str, Any], evidence: dict[str, Any], closed: bool) -> dict[str, Any]:
    result = copy.deepcopy(row)
    quality = result.get("quality") if isinstance(result.get("quality"), dict) else {}
    quality = copy.deepcopy(quality)
    items = quality.get("residual_audio_arbitration") if isinstance(quality.get("residual_audio_arbitration"), list) else []
    decision = evidence.get("decision") if isinstance(evidence.get("decision"), dict) else {}
    items.append(
        {
            "profile": PROFILE,
            "residual_queue_id": evidence.get("residual_queue_id"),
            "outcome": decision.get("outcome"),
            "action": decision.get("action"),
            "reason": decision.get("reason"),
            "confidence": decision.get("confidence"),
            "closed": closed,
            "provenance_sha256": evidence.get("provenance_sha256"),
        }
    )
    quality["residual_audio_arbitration"] = items
    result["quality"] = quality
    return result


def apply_session(args: argparse.Namespace) -> int:
    sessions_root = args.sessions_root.expanduser().resolve()
    out_dir = report_dir(sessions_root, args.out_dir)
    manifest = read_json(out_dir / "baseline_manifest.json")
    frozen_queue = read_jsonl(out_dir / "residual_audio_queue.jsonl")
    if not manifest or not frozen_queue:
        print("status: missing_frozen_baseline", file=sys.stderr)
        return 2
    session = args.session.expanduser().resolve()
    record = next(
        (row for row in manifest.get("sessions") or [] if str(row.get("session_id") or "") == session.name),
        None,
    )
    if not record:
        print(f"status: session_not_in_frozen_scope: {session.name}", file=sys.stderr)
        return 2
    input_paths = profile_paths(session, INPUT_PROFILE)
    output_paths = profile_paths(session, PROFILE)
    dialogue = read_json(input_paths["dialogue"])
    quality = read_json(input_paths["quality"])
    evidence_path = audit_dir(session) / "residual_audio_evidence.jsonl"
    evidence_rows = read_jsonl(evidence_path)
    evidence_by_id = {str(row.get("residual_queue_id") or ""): row for row in evidence_rows}
    queue_rows = [row for row in frozen_queue if str(row.get("session_id") or "") == session.name]
    input_review_queue_path = profile_dir_for_input(session) / "residual_me_review_queue.jsonl"
    input_review_queue = read_jsonl(input_review_queue_path)
    local_rows, order_rows = queue_subsets(input_review_queue)
    failures: list[str] = []
    frozen_dialogue_sha = (((record.get("artifacts") or {}).get("dialogue") or {}).get("sha256"))
    if not dialogue or not quality:
        failures.append("missing_input_profile")
    if sha256_file(input_paths["dialogue"]) != frozen_dialogue_sha:
        failures.append("frozen_input_dialogue_hash_mismatch")
    raw_ok, raw_failures = raw_capture_matches(record)
    if not raw_ok:
        failures.append("raw_capture_hash_mismatch")
    if len(evidence_by_id) != len(queue_rows):
        failures.append("not_all_frozen_rows_have_evidence")
    queue_record = record.get("input_review_queue") if isinstance(record.get("input_review_queue"), dict) else {}
    if sha256_bytes(canonical_bytes(local_rows)) != queue_record.get("local_recall_sha256"):
        failures.append("local_recall_queue_changed")
    if sha256_bytes(canonical_bytes(order_rows)) != queue_record.get("transcript_order_sha256"):
        failures.append("transcript_order_queue_changed")
    if failures:
        report = {
            "schema": "murmurmark.residual_audio_arbitration_profile_report/v1",
            "session_id": session.name,
            "status": "failed_open",
            "gates": {"passed": False, "hard_failures": failures, "raw_capture_failures": raw_failures},
        }
        write_json(profile_dir(session) / "residual_audio_arbitration_profile_report.json", report)
        print("status: failed_open")
        return 2
    assert dialogue is not None and quality is not None
    input_utterances = [copy.deepcopy(row) for row in dialogue.get("utterances") or [] if isinstance(row, dict)]
    output_by_id = {str(row.get("id") or ""): copy.deepcopy(row) for row in input_utterances}
    dropped_ids: set[str] = set()
    closed_ids: set[str] = set()
    dispositions: list[dict[str, Any]] = []
    for queue_row in queue_rows:
        queue_id = str(queue_row.get("residual_queue_id") or "")
        evidence = evidence_by_id.get(queue_id, {})
        decision = evidence.get("decision") if isinstance(evidence.get("decision"), dict) else {}
        action = str(decision.get("action") or "needs_review")
        me_ids = [str(value) for value in queue_row.get("me_utterance_ids") or [] if value]
        closed = False
        if action == "keep_me" and len(me_ids) == 1 and me_ids[0] in output_by_id:
            output_by_id[me_ids[0]] = annotate_utterance(output_by_id[me_ids[0]], evidence, True)
            closed = True
        elif action == "drop_duplicate_or_noise" and len(me_ids) == 1 and me_ids[0] in output_by_id:
            dropped_ids.add(me_ids[0])
            closed = True
        else:
            for me_id in me_ids:
                if me_id in output_by_id:
                    output_by_id[me_id] = annotate_utterance(output_by_id[me_id], evidence, False)
        if closed:
            closed_ids.add(queue_id)
        dispositions.append(
            {
                "schema": "murmurmark.residual_audio_arbitration_disposition/v1",
                "session_id": session.name,
                "residual_queue_id": queue_id,
                "source": "audio_review",
                "source_audit_id": queue_row.get("source_audit_id"),
                "interval": queue_row.get("interval"),
                "outcome": decision.get("outcome"),
                "action": action if closed else "needs_review",
                "reason": decision.get("reason"),
                "confidence": decision.get("confidence"),
                "closed": closed,
                "provenance_sha256": evidence.get("provenance_sha256"),
            }
        )
    output_utterances = [
        output_by_id[str(row.get("id") or "")]
        for row in input_utterances
        if str(row.get("id") or "") not in dropped_ids
    ]
    order_module = load_sibling("apply-transcript-order-repair.py", "murmurmark_residual_audio_order")
    overlaps = order_module.build_overlaps(output_utterances)
    closed_rows = [row for row in dispositions if row.get("closed") is True]
    remaining_audio = [row for row in dispositions if row.get("closed") is not True]
    summary = {
        "queue_items": len(queue_rows),
        "queue_seconds": round(sum(duration(row) for row in queue_rows), 3),
        "closed_items": len(closed_rows),
        "closed_seconds": round(sum(duration(row) for row in queue_rows if str(row.get("residual_queue_id")) in closed_ids), 3),
        "remaining_items": len(remaining_audio),
        "remaining_seconds": round(sum(duration(row) for row in queue_rows if str(row.get("residual_queue_id")) not in closed_ids), 3),
        "kept_me_items": sum(1 for row in closed_rows if row.get("action") == "keep_me"),
        "dropped_me_items": len(dropped_ids),
        "by_outcome": dict(sorted(Counter(str(row.get("outcome") or "unknown") for row in dispositions).items())),
        "preserved_local_recall_items": len(local_rows),
        "preserved_transcript_order_items": len(order_rows),
    }
    output_quality = order_module.quality_report(quality, output_utterances, overlaps, summary)
    output_quality["residual_audio_arbitration"] = summary
    output_dialogue = {
        "schema": "murmurmark.clean_dialogue/v1",
        "session": dialogue.get("session", session.name),
        "utterances": output_utterances,
    }
    input_remote = [row for row in input_utterances if role_name(row) == "Colleagues"]
    output_remote = [row for row in output_utterances if role_name(row) == "Colleagues"]
    input_text = {str(row.get("id")): str(row.get("text") or "") for row in input_utterances if row.get("id")}
    changed_text = [
        str(row.get("id"))
        for row in output_utterances
        if str(row.get("id")) in input_text and str(row.get("text") or "") != input_text[str(row.get("id"))]
    ]
    hard_failures: list[str] = []
    if len(dispositions) != len(queue_rows):
        hard_failures.append("not_all_audio_rows_have_dispositions")
    if any(str(row.get("outcome") or "") not in OUTCOMES for row in dispositions):
        hard_failures.append("invalid_arbitration_outcome")
    if input_remote != output_remote:
        hard_failures.append("remote_utterances_changed")
    if changed_text:
        hard_failures.append("existing_utterance_text_changed")
    raw_ok_after, raw_failures_after = raw_capture_matches(record)
    if not raw_ok_after:
        hard_failures.append("raw_capture_changed")
    gates = {"passed": not hard_failures, "hard_failures": hard_failures, "raw_capture_failures": raw_failures_after}
    if args.mode == "conservative" and gates["passed"]:
        write_json(output_paths["dialogue"], output_dialogue)
        write_json(output_paths["quality"], output_quality)
        write_json(output_paths["overlaps"], {"schema": "murmurmark.transcript_overlaps/v1", "session": session.name, "overlaps": overlaps})
        write_json(
            output_paths["transcript_json"],
            {
                "schema": "murmurmark.transcript_simple/v1",
                "session": dialogue.get("session", session.name),
                "backend": "whisper.cpp",
                "utterances": [
                    {**copy.deepcopy(row), "raw_text": row.get("text"), "corrected_text": row.get("text"), "corrections": []}
                    for row in output_utterances
                ],
            },
        )
        transcribe_report = read_json(input_paths["dialogue"].parent / "transcribe_simple_report.json") or {}
        order_module.write_markdown(
            output_paths["transcript"], output_utterances, transcribe_report.get("model"), transcribe_report.get("language")
        )
    remaining_queue = copy.deepcopy(local_rows) + copy.deepcopy(order_rows) + remaining_audio
    report = {
        "schema": "murmurmark.residual_audio_arbitration_profile_report/v1",
        "generator": {"name": "residual-audio-arbitration", "version": SCRIPT_VERSION},
        "session_id": session.name,
        "input_profile": INPUT_PROFILE,
        "output_profile": PROFILE,
        "mode": args.mode,
        "status": "ok" if gates["passed"] else "failed_open",
        "inputs": {
            "frozen_dialogue_sha256": frozen_dialogue_sha,
            "actual_dialogue_sha256": sha256_file(input_paths["dialogue"]),
            "input_review_queue_sha256": sha256_file(input_review_queue_path),
            "evidence": str(evidence_path),
            "evidence_sha256": sha256_file(evidence_path),
            "queue_sha256": (manifest.get("queue") or {}).get("sha256"),
        },
        "preserved_queue_fingerprints": {
            "local_recall_sha256": sha256_bytes(canonical_bytes(local_rows)),
            "transcript_order_sha256": sha256_bytes(canonical_bytes(order_rows)),
        },
        "summary": summary,
        "gates": gates,
    }
    output_dir = profile_dir(session)
    write_jsonl(output_dir / "residual_audio_dispositions.jsonl", dispositions)
    write_jsonl(output_dir / "residual_audio_applied.jsonl", closed_rows)
    write_jsonl(output_dir / "residual_audio_review_queue.jsonl", remaining_audio)
    write_jsonl(output_dir / "residual_me_review_queue.jsonl", remaining_queue)
    write_json(
        output_dir / "residual_audio_diff.json",
        {
            "schema": "murmurmark.residual_audio_arbitration_diff/v1",
            "session_id": session.name,
            "input_profile": INPUT_PROFILE,
            "output_profile": PROFILE,
            "dropped_ids": sorted(dropped_ids),
            "existing_text_changed_ids": changed_text,
            "remote_unchanged": input_remote == output_remote,
        },
    )
    if args.mode == "conservative" and gates["passed"]:
        report["output_fingerprint"] = output_fingerprint(output_paths)
    write_json(output_dir / "residual_audio_arbitration_profile_report.json", report)
    print(f"profile_report: {output_dir / 'residual_audio_arbitration_profile_report.json'}")
    print(f"closed_items: {summary['closed_items']}")
    print(f"remaining_items: {summary['remaining_items']}")
    return 0 if gates["passed"] else 2


def selected_note_count(payload: dict[str, Any] | None) -> int:
    return BASE.selected_note_count(payload)


def apply_in_subprocess(session: Path, sessions_root: Path, out_dir: Path) -> int:
    return subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "apply",
            str(session),
            "--sessions-root",
            str(sessions_root),
            "--out-dir",
            str(out_dir),
        ],
        check=False,
    ).returncode


def quality_metric(payload: dict[str, Any] | None, key: str, default: float = 0.0) -> float:
    if not isinstance(payload, dict):
        return default
    if key in payload:
        return safe_float(payload.get(key), default)
    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
    return safe_float(metrics.get(key), default)


def evaluate_corpus(args: argparse.Namespace) -> int:
    sessions_root = args.sessions_root.expanduser().resolve()
    out_dir = report_dir(sessions_root, args.out_dir)
    manifest = read_json(out_dir / "baseline_manifest.json")
    queue = read_jsonl(out_dir / "residual_audio_queue.jsonl")
    if not manifest or not queue:
        print("status: missing_frozen_baseline", file=sys.stderr)
        return 2
    session_ids = sorted({str(row.get("session_id") or "") for row in queue})
    if args.apply:
        for session_id in session_ids:
            if apply_in_subprocess(sessions_root / session_id, sessions_root, out_dir) != 0:
                print(f"status: apply_failed: {session_id}", file=sys.stderr)
    if args.synthesize:
        synthesis_script = Path(__file__).with_name("synthesize-simple-extractive.py")
        for session_id in session_ids:
            report = read_json(profile_dir(sessions_root / session_id) / "residual_audio_arbitration_profile_report.json") or {}
            if (report.get("gates") or {}).get("passed") is True:
                subprocess.run(
                    [sys.executable, str(synthesis_script), str(sessions_root / session_id), "--transcript-profile", PROFILE],
                    check=True,
                )

    records = {str(row.get("session_id") or ""): row for row in manifest.get("sessions") or []}
    hard_failures: list[str] = []
    session_reports: list[dict[str, Any]] = []
    closed_items = 0
    closed_seconds = 0.0
    remaining_items = 0
    remaining_seconds = 0.0
    all_outcomes = Counter()
    for session_id in session_ids:
        session = sessions_root / session_id
        record = records.get(session_id, {})
        report_path = profile_dir(session) / "residual_audio_arbitration_profile_report.json"
        report = read_json(report_path)
        if not report or (report.get("gates") or {}).get("passed") is not True:
            hard_failures.append(f"{session_id}:profile_gates_failed")
            continue
        input_paths = profile_paths(session, INPUT_PROFILE)
        output_paths = profile_paths(session, PROFILE)
        frozen_artifacts = record.get("artifacts") if isinstance(record.get("artifacts"), dict) else {}
        for name, artifact in frozen_artifacts.items():
            if isinstance(artifact, dict) and artifact.get("sha256") and sha256_file(Path(str(artifact.get("path") or ""))) != artifact.get("sha256"):
                hard_failures.append(f"{session_id}:frozen_{name}_changed")
        raw_ok, raw_failures = raw_capture_matches(record)
        if not raw_ok:
            hard_failures.append(f"{session_id}:raw_capture_changed")
        if report.get("output_fingerprint") != output_fingerprint(output_paths):
            hard_failures.append(f"{session_id}:output_fingerprint_mismatch")
        input_queue = read_jsonl(profile_dir_for_input(session) / "residual_me_review_queue.jsonl")
        local_rows, order_rows = queue_subsets(input_queue)
        preserved = report.get("preserved_queue_fingerprints") if isinstance(report.get("preserved_queue_fingerprints"), dict) else {}
        if sha256_bytes(canonical_bytes(local_rows)) != preserved.get("local_recall_sha256"):
            hard_failures.append(f"{session_id}:local_recall_queue_regressed")
        if sha256_bytes(canonical_bytes(order_rows)) != preserved.get("transcript_order_sha256"):
            hard_failures.append(f"{session_id}:transcript_order_queue_regressed")
        input_quality = read_json(input_paths["quality"])
        output_quality = read_json(output_paths["quality"])
        for metric in (
            "local_only_island_recall",
            "unrepaired_long_mic_crossings_count",
            "golden_phrase_fail_count",
            "cross_role_overlap_gt2_seconds",
            "remote_duplicate_in_me_seconds",
            "needs_review_count",
        ):
            before = quality_metric(input_quality, metric)
            after = quality_metric(output_quality, metric)
            if metric == "local_only_island_recall" and after + 1e-6 < before:
                hard_failures.append(f"{session_id}:{metric}_regressed:{before}->{after}")
            if metric != "local_only_island_recall" and after > before + 1e-6:
                hard_failures.append(f"{session_id}:{metric}_regressed:{before}->{after}")
        before_notes = selected_note_count(read_json(input_paths["evidence_notes"]))
        after_notes = selected_note_count(read_json(output_paths["evidence_notes"]))
        if after_notes < before_notes:
            hard_failures.append(f"{session_id}:notes_evidence_regressed:{before_notes}->{after_notes}")
        auto_verdict = read_json(session / "derived/synthesis-simple/extractive/quality_verdict.json") or {}
        if auto_verdict.get("selected_transcript_profile") != INPUT_PROFILE:
            hard_failures.append(
                f"{session_id}:guarded_auto_selection_changed:"
                f"{auto_verdict.get('selected_transcript_profile')}"
            )
        summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
        closed_items += safe_int(summary.get("closed_items"))
        closed_seconds += safe_float(summary.get("closed_seconds"))
        remaining_items += safe_int(summary.get("remaining_items"))
        remaining_seconds += safe_float(summary.get("remaining_seconds"))
        for outcome, count in (summary.get("by_outcome") or {}).items():
            all_outcomes[str(outcome)] += safe_int(count)
        first = output_fingerprint(output_paths)
        apply_status = apply_in_subprocess(session, sessions_root, out_dir)
        deterministic = first == output_fingerprint(output_paths)
        if apply_status != 0:
            hard_failures.append(f"{session_id}:repeat_apply_failed")
        if not deterministic:
            hard_failures.append(f"{session_id}:profile_not_deterministic")
        session_reports.append(
            {
                "session_id": session_id,
                "summary": summary,
                "raw_capture_unchanged": raw_ok,
                "raw_capture_failures": raw_failures,
                "deterministic": deterministic,
                "selected_notes_before": before_notes,
                "selected_notes_after": after_notes,
                "output_fingerprint": report.get("output_fingerprint"),
            }
        )

    baseline_items = safe_int((manifest.get("queue") or {}).get("item_count"))
    baseline_seconds = safe_float((manifest.get("queue") or {}).get("seconds"))
    if closed_items + remaining_items != baseline_items:
        hard_failures.append("not_all_frozen_rows_accounted_for")
    if sum(safe_int(value) for value in all_outcomes.values()) != baseline_items:
        hard_failures.append("not_all_rows_have_stable_outcomes")
    item_ratio = closed_items / max(1, baseline_items)
    seconds_ratio = closed_seconds / max(0.001, baseline_seconds)
    closure_target = item_ratio >= 0.20 and seconds_ratio >= 0.20
    decision = "PROMOTE_RESIDUAL_AUDIO_ARBITRATION_V1" if not hard_failures and closure_target else "DO_NOT_PROMOTE"
    evidence_limit = []
    if not closure_target:
        evidence_limit.append(
            {
                "reason": "safe_closure_below_20_percent_target",
                "closed_items": closed_items,
                "closed_seconds": round(closed_seconds, 3),
                "required_items": math.ceil(baseline_items * 0.20),
                "required_seconds": round(baseline_seconds * 0.20, 3),
            }
        )
    report = {
        "schema": "murmurmark.residual_audio_arbitration_corpus_report/v1",
        "generator": {"name": "residual-audio-arbitration", "version": SCRIPT_VERSION},
        "profile": PROFILE,
        "input_profile": INPUT_PROFILE,
        "decision": decision,
        "baseline_manifest_sha256": sha256_file(out_dir / "baseline_manifest.json"),
        "queue_sha256": (manifest.get("queue") or {}).get("sha256"),
        "summary": {
            "session_count": len(session_reports),
            "frozen_queue_items": baseline_items,
            "frozen_queue_seconds": round(baseline_seconds, 3),
            "closed_items": closed_items,
            "closed_seconds": round(closed_seconds, 3),
            "remaining_items": remaining_items,
            "remaining_seconds": round(remaining_seconds, 3),
            "safe_closure_item_ratio": round(item_ratio, 6),
            "safe_closure_seconds_ratio": round(seconds_ratio, 6),
            "by_outcome": dict(sorted(all_outcomes.items())),
            "preserved_local_recall_items": safe_int((manifest.get("preserved_residual_queues") or {}).get("local_recall_count")),
            "preserved_transcript_order_items": safe_int((manifest.get("preserved_residual_queues") or {}).get("transcript_order_count")),
        },
        "evidence_limit": evidence_limit,
        "gates": {
            "passed": not hard_failures and closure_target,
            "scientifically_complete": not hard_failures,
            "hard_failures": sorted(set(hard_failures)),
            "warnings": [] if closure_target else ["safe_closure_below_20_percent_target"],
        },
        "promoted_sessions": session_ids if decision == "PROMOTE_RESIDUAL_AUDIO_ARBITRATION_V1" else [],
        "sessions": session_reports,
    }
    write_json(out_dir / "residual_audio_corpus_report.json", report)
    write_corpus_markdown(out_dir / "residual_audio_corpus_report.md", report)
    print(f"corpus_report: {out_dir / 'residual_audio_corpus_report.json'}")
    print(f"decision: {decision}")
    print(f"closed_items: {closed_items}/{baseline_items}")
    print(f"closed_seconds: {round(closed_seconds, 3)}/{round(baseline_seconds, 3)}")
    return 0 if not hard_failures else 2


def write_corpus_markdown(path: Path, report: dict[str, Any]) -> None:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    lines = [
        "# Residual Audio Evidence Arbitration Corpus",
        "",
        f"- Decision: `{report.get('decision')}`",
        f"- Frozen queue: `{summary.get('frozen_queue_items')}` rows / `{summary.get('frozen_queue_seconds')}` sec",
        f"- Safely closed: `{summary.get('closed_items')}` rows / `{summary.get('closed_seconds')}` sec",
        f"- Remaining: `{summary.get('remaining_items')}` rows / `{summary.get('remaining_seconds')}` sec",
        f"- Local-recall rows preserved: `{summary.get('preserved_local_recall_items')}`",
        f"- Order rows preserved: `{summary.get('preserved_transcript_order_items')}`",
        f"- Gates: `{(report.get('gates') or {}).get('passed')}`",
        "",
        "## Outcomes",
        "",
    ]
    for outcome, count in (summary.get("by_outcome") or {}).items():
        lines.append(f"- `{outcome}`: `{count}`")
    if report.get("evidence_limit"):
        lines.extend(["", "## Evidence Limit", ""])
        for row in report.get("evidence_limit") or []:
            lines.append(f"- `{row.get('reason')}`: closed `{row.get('closed_items')}` rows / `{row.get('closed_seconds')}` sec")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def run_all(args: argparse.Namespace) -> int:
    status = freeze_corpus(
        SimpleNamespace(sessions_root=args.sessions_root, out_dir=args.out_dir, force=args.force_freeze)
    )
    if status != 0:
        return status
    status = build_evidence(args)
    if status != 0:
        return status
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
