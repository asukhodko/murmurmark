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


SCRIPT_VERSION = "0.2.0"
PROFILE = "residual_me_evidence_v1"
INPUT_PROFILE = "authoritative_boundary_v1"
REPORT_DIR_NAME = "residual-me-evidence-v1"
DEFAULT_MODEL = Path.home() / ".local/share/murmurmark/models/faster-whisper/large-v3"
PROTECTED_MARKERS = {
    "блокер",
    "вопрос",
    "давай",
    "давайте",
    "договорились",
    "надо",
    "нужно",
    "проблема",
    "решили",
    "риск",
    "согласовали",
}
KNOWN_HALLUCINATIONS = (
    "продолжение следует",
    "спасибо за просмотр",
    "редактор субтитров",
    "субтитры сделал",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Close residual Me review rows with exact Target-Me, remote-forbidden and micro-ASR evidence."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def common(command: argparse.ArgumentParser) -> None:
        command.add_argument("--sessions-root", type=Path, default=Path("sessions"))
        command.add_argument("--out-dir", type=Path)

    freeze = subparsers.add_parser("freeze", help="Freeze the promoted authoritative-boundary residual queue.")
    common(freeze)
    freeze.add_argument("--force", action="store_true")

    evidence = subparsers.add_parser("evidence", help="Build exact local evidence for frozen residual rows.")
    common(evidence)
    evidence.add_argument("sessions", nargs="*", type=Path)
    add_model_args(evidence)

    apply = subparsers.add_parser("apply", help="Apply safe residual evidence decisions to one session.")
    common(apply)
    apply.add_argument("session", type=Path)
    apply.add_argument("--mode", choices=("conservative", "dry-run"), default="conservative")

    evaluate = subparsers.add_parser("evaluate", help="Evaluate the candidate profile on the frozen corpus.")
    common(evaluate)
    evaluate.add_argument("--apply", action="store_true")
    evaluate.add_argument("--synthesize", action="store_true")

    run = subparsers.add_parser("run", help="Freeze, build evidence, apply, synthesize and evaluate the corpus.")
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


def load_sibling(filename: str, module_name: str) -> ModuleType:
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(module_name, path)
    if not spec or not spec.loader:
        raise RuntimeError(f"cannot load sibling module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


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
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
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
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as output:
        for row in rows:
            output.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


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


def suffix(profile: str) -> str:
    return "" if profile == "current" else f".{profile}"


def report_dir(sessions_root: Path, explicit: Path | None) -> Path:
    return explicit.expanduser().resolve() if explicit else sessions_root / "_reports" / REPORT_DIR_NAME


def profile_paths(session: Path, profile: str) -> dict[str, Path]:
    resolved = session / "derived/transcript-simple/whisper-cpp/resolved"
    synthesis = session / "derived/synthesis-simple/extractive"
    profile_suffix = suffix(profile)
    return {
        "dialogue": resolved / f"clean_dialogue{profile_suffix}.json",
        "quality": resolved / f"quality_report{profile_suffix}.json",
        "overlaps": resolved / f"overlaps{profile_suffix}.json",
        "transcript": resolved / f"transcript{profile_suffix}.md",
        "transcript_json": resolved / f"transcript.simple{profile_suffix}.json",
        "notes": synthesis / f"notes{profile_suffix}.md",
        "evidence_notes": synthesis / f"evidence_notes{profile_suffix}.json",
        "quality_verdict": synthesis / f"quality_verdict{profile_suffix}.json",
    }


def artifact_fingerprints(session: Path, profile: str) -> dict[str, Any]:
    return {
        name: {"path": str(path), "exists": path.exists(), "sha256": sha256_file(path)}
        for name, path in profile_paths(session, profile).items()
    }


def interval(row: dict[str, Any]) -> tuple[float, float]:
    nested = row.get("interval") if isinstance(row.get("interval"), dict) else {}
    start = safe_float(nested.get("start", row.get("start", row.get("start_sec"))))
    end = safe_float(nested.get("end", row.get("end", row.get("end_sec"))), start)
    return start, end


def duration(row: dict[str, Any]) -> float:
    start, end = interval(row)
    nested = row.get("interval") if isinstance(row.get("interval"), dict) else {}
    return max(0.0, safe_float(nested.get("duration_sec"), end - start))


def role_name(row: dict[str, Any]) -> str:
    role = str(row.get("role") or row.get("speaker_label") or "").lower()
    source = str(row.get("source_track") or "").lower()
    if role == "me" or source == "mic":
        return "Me"
    if role in {"remote", "colleagues"} or source == "remote" or "colleague" in role:
        return "Colleagues"
    return str(row.get("speaker_label") or row.get("role") or "Unknown")


def content_tokens(value: Any, stronger: ModuleType) -> list[str]:
    return [
        token.strip(".,!?;:()[]{}\"'`«»")
        for token in stronger.content_tokens(value)
        if token.strip(".,!?;:()[]{}\"'`«»")
    ]


def has_protected_marker(value: Any, stronger: ModuleType) -> bool:
    return bool(set(stronger.normalize_text(value).split()) & PROTECTED_MARKERS)


def transcript_core_word_spans(
    transcript: dict[str, Any],
    *,
    interval_start: float,
    interval_end: float,
) -> list[dict[str, Any]]:
    identity = transcript.get("identity") if isinstance(transcript.get("identity"), dict) else {}
    clip_start = interval_start - safe_float(identity.get("core_start"))
    rows: list[dict[str, Any]] = []
    for segment in transcript.get("segments") or []:
        if not isinstance(segment, dict):
            continue
        for word in segment.get("words") or []:
            if not isinstance(word, dict):
                continue
            start = clip_start + safe_float(word.get("start"))
            end = clip_start + safe_float(word.get("end"), start)
            midpoint = (start + end) / 2.0
            text = str(word.get("text") or "").strip()
            if text and interval_start - 0.08 <= midpoint <= interval_end + 0.08:
                rows.append(
                    {
                        "start": round(max(interval_start, start), 3),
                        "end": round(min(interval_end, max(start, end)), 3),
                        "text": text,
                    }
                )
    return rows


def source_audit_path(session: Path, source: str) -> Path:
    if source == "audio_review":
        return session / "derived/audit/audio-review-pack/audio_review_audit.jsonl"
    if source == "local_recall":
        return session / "derived/audit/local-recall/local_recall_items.jsonl"
    if source == "transcript_order":
        return session / "derived/audit/order/transcript_order_items.jsonl"
    return session / "derived/audit/missing.jsonl"


def source_detail(session: Path, queue_row: dict[str, Any]) -> tuple[dict[str, Any], Path]:
    source = str(queue_row.get("source") or "")
    path = source_audit_path(session, source)
    wanted = str(queue_row.get("source_audit_id") or "")
    for row in read_jsonl(path):
        if str(row.get("id") or row.get("item_id") or "") == wanted:
            return row, path
    return {}, path


def dialogue_by_id(dialogue: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("id")): row
        for row in dialogue.get("utterances") or []
        if isinstance(row, dict) and row.get("id")
    }


def overlapping_utterances(dialogue: dict[str, Any], start: float, end: float) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in dialogue.get("utterances") or []:
        if not isinstance(row, dict):
            continue
        row_start = safe_float(row.get("start"))
        row_end = safe_float(row.get("end"), row_start)
        if min(end, row_end) - max(start, row_start) > 0:
            result.append(row)
    return result


def queue_context(queue_row: dict[str, Any], detail: dict[str, Any], dialogue: dict[str, Any]) -> dict[str, Any]:
    start, end = interval(queue_row)
    by_id = dialogue_by_id(dialogue)
    utterance_ids = [str(value) for value in queue_row.get("utterance_ids") or [] if value]
    referenced = [by_id[value] for value in utterance_ids if value in by_id]
    if not referenced:
        referenced = overlapping_utterances(dialogue, start, end)
    me_rows = [row for row in referenced if role_name(row) == "Me"]
    remote_rows = [row for row in referenced if role_name(row) == "Colleagues"]
    if str(queue_row.get("source")) == "transcript_order":
        nested = detail.get("utterances") if isinstance(detail.get("utterances"), dict) else {}
        for key, expected_role in (("me", "Me"), ("remote", "Colleagues")):
            row = nested.get(key) if isinstance(nested.get(key), dict) else None
            if row and not any(str(item.get("id")) == str(row.get("id")) for item in referenced):
                referenced.append(row)
                (me_rows if expected_role == "Me" else remote_rows).append(row)
    target_text = " ".join(str(row.get("text") or "").strip() for row in me_rows).strip()
    if str(queue_row.get("source")) == "local_recall":
        target_text = str(detail.get("parent_text") or "").strip()
    remote_text = " ".join(str(row.get("text") or "").strip() for row in remote_rows).strip()
    if not remote_text:
        remote_text = str(detail.get("remote_overlap_text_sample") or "").strip()
    return {
        "utterance_ids": [str(row.get("id")) for row in referenced if row.get("id")],
        "me_utterance_ids": [str(row.get("id")) for row in me_rows if row.get("id")],
        "remote_utterance_ids": [str(row.get("id")) for row in remote_rows if row.get("id")],
        "target_text": target_text,
        "remote_text": remote_text,
    }


def raw_fingerprints(session: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for track in ("mic", "remote"):
        for path in sorted((session / "audio" / track).glob("*.caf")):
            rows.append(
                {
                    "track": track,
                    "path": str(path),
                    "size": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )
    return rows


def freeze_corpus(args: argparse.Namespace) -> int:
    sessions_root = args.sessions_root.expanduser().resolve()
    out_dir = report_dir(sessions_root, args.out_dir)
    manifest_path = out_dir / "baseline_manifest.json"
    if manifest_path.exists() and not args.force:
        print(f"baseline_manifest: {manifest_path}")
        print("status: already_frozen")
        return 0
    boundary_report_path = sessions_root / "_reports/authoritative-boundary-v1/boundary_corpus_report.json"
    boundary_report = read_json(boundary_report_path)
    if not boundary_report or boundary_report.get("decision") != "PROMOTE_AUTHORITATIVE_BOUNDARY_V1":
        print("status: authoritative_boundary_not_promoted", file=sys.stderr)
        return 2
    session_ids = [str(value) for value in boundary_report.get("promoted_sessions") or []]
    frozen_queue: list[dict[str, Any]] = []
    sessions: list[dict[str, Any]] = []
    failures: list[str] = []
    for session_id in sorted(session_ids):
        session = sessions_root / session_id
        paths = profile_paths(session, INPUT_PROFILE)
        dialogue = read_json(paths["dialogue"])
        if not dialogue:
            failures.append(f"{session_id}:missing_authoritative_dialogue")
            continue
        queue_path = session / "derived/transcript-simple/whisper-cpp/authoritative-boundary-v1/boundary_review_queue.jsonl"
        session_queue = read_jsonl(queue_path)
        for row in session_queue:
            detail, detail_path = source_detail(session, row)
            context = queue_context(row, detail, dialogue)
            frozen = copy.deepcopy(row)
            frozen.update(context)
            frozen["schema"] = "murmurmark.residual_me_queue_item/v1"
            frozen["residual_queue_id"] = str(row.get("queue_id") or row.get("boundary_queue_id") or "")
            frozen["source_detail"] = detail
            frozen["source_detail_path"] = str(detail_path)
            frozen["source_detail_sha256"] = sha256_file(detail_path)
            if not frozen["residual_queue_id"]:
                frozen["residual_queue_id"] = f"residual_{sha256_bytes(canonical_bytes(frozen))[:16]}"
            if not detail:
                failures.append(f"{session_id}:{frozen['residual_queue_id']}:missing_source_detail")
            frozen_queue.append(frozen)
        sessions.append(
            {
                "session_id": session_id,
                "session": str(session),
                "input_profile": INPUT_PROFILE,
                "artifacts": artifact_fingerprints(session, INPUT_PROFILE),
                "raw_capture": raw_fingerprints(session),
                "residual_queue_path": str(queue_path),
                "residual_queue_sha256": sha256_file(queue_path),
                "residual_items": len(session_queue),
                "residual_seconds": round(sum(duration(row) for row in session_queue), 3),
            }
        )
        print(f"freeze: {session_id}: {len(session_queue)} rows", flush=True)
    frozen_queue.sort(key=lambda row: (str(row.get("session_id") or ""), str(row.get("residual_queue_id") or "")))
    queue_sha = sha256_bytes(canonical_bytes(frozen_queue))
    manifest = {
        "schema": "murmurmark.residual_me_evidence_baseline/v1",
        "generator": {"name": "residual-me-evidence", "version": SCRIPT_VERSION},
        "input_profile": INPUT_PROFILE,
        "output_profile": PROFILE,
        "source_boundary": {
            "report": str(boundary_report_path),
            "report_sha256": sha256_file(boundary_report_path),
            "decision": boundary_report.get("decision"),
            "baseline_manifest_sha256": boundary_report.get("baseline_manifest_sha256"),
            "queue_sha256": boundary_report.get("queue_sha256"),
        },
        "scope": {"session_count": len(sessions), "session_ids": [row["session_id"] for row in sessions]},
        "queue": {
            "item_count": len(frozen_queue),
            "seconds": round(sum(duration(row) for row in frozen_queue), 3),
            "sha256": queue_sha,
            "by_source": dict(sorted(Counter(str(row.get("source") or "unknown") for row in frozen_queue).items())),
        },
        "sessions": sessions,
        "gates": {"passed": not failures, "hard_failures": failures},
    }
    write_json(out_dir / "baseline_manifest.json", manifest)
    write_jsonl(out_dir / "residual_queue.jsonl", frozen_queue)
    print(f"baseline_manifest: {out_dir / 'baseline_manifest.json'}")
    print(f"residual_queue: {out_dir / 'residual_queue.jsonl'}")
    print(f"items: {len(frozen_queue)}")
    print(f"seconds: {manifest['queue']['seconds']}")
    return 0 if not failures else 2


def audio_sources(session: Path) -> dict[str, Path]:
    mic_raw = session / "derived/preprocess/audio/mic_raw_for_asr.wav"
    remote = session / "derived/preprocess/audio/remote_for_aec.wav"
    return {
        "mic_clean": session / "derived/preprocess/audio/mic_clean_local_fir.wav",
        "mic_raw": mic_raw if mic_raw.exists() else session / "audio/mic/000001.caf",
        "mic_role_masked": session / "derived/preprocess/audio/mic_role_masked_for_asr.wav",
        "remote": remote if remote.exists() else session / "audio/remote/000001.caf",
    }


def extract_wav(source: Path, destination: Path, start: float, end: float) -> bool:
    if not source.exists() or end <= start:
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{max(0.0, start):.3f}",
        "-t",
        f"{max(0.05, end - max(0.0, start)):.3f}",
        "-i",
        str(source),
        "-ar",
        "16000",
        "-ac",
        "1",
        str(destination),
    ]
    result = subprocess.run(command, check=False)
    return result.returncode == 0 and destination.exists() and destination.stat().st_size > 44


def model_path(args: argparse.Namespace) -> Path:
    if getattr(args, "model", None):
        return args.model.expanduser()
    if os.environ.get("MURMURMARK_FASTER_WHISPER_MODEL"):
        return Path(os.environ["MURMURMARK_FASTER_WHISPER_MODEL"]).expanduser()
    return DEFAULT_MODEL


def model_identity(path: Path) -> dict[str, Any]:
    binary = path / "model.bin" if path.is_dir() else path
    return {
        "path": str(path),
        "model_bin_size": binary.stat().st_size if binary.exists() else None,
        "model_bin_mtime_ns": binary.stat().st_mtime_ns if binary.exists() else None,
    }


def decode_exact_interval(
    model: Any,
    clip: Path,
    *,
    core_start: float,
    core_end: float,
    cache_dir: Path,
    args: argparse.Namespace,
) -> dict[str, Any]:
    identity = {
        "clip_sha256": sha256_file(clip),
        "model": model_identity(model_path(args)),
        "language": args.language,
        "beam_size": args.beam_size,
        "core_start": round(core_start, 3),
        "core_end": round(core_end, 3),
        "word_timestamps": True,
    }
    cache_key = sha256_bytes(canonical_bytes(identity))
    cache_path = cache_dir / f"{cache_key}.json"
    cached = read_json(cache_path)
    if cached and not args.no_cache:
        return cached
    try:
        segments, info = model.transcribe(
            str(clip),
            language=args.language,
            beam_size=args.beam_size,
            vad_filter=False,
            condition_on_previous_text=False,
            word_timestamps=True,
            temperature=0.0,
        )
        segment_rows: list[dict[str, Any]] = []
        core_words: list[str] = []
        for segment in segments:
            words: list[dict[str, Any]] = []
            for word in getattr(segment, "words", None) or []:
                word_start = safe_float(getattr(word, "start", None))
                word_end = safe_float(getattr(word, "end", None), word_start)
                text = str(getattr(word, "word", "") or "").strip()
                words.append({"start": round(word_start, 3), "end": round(word_end, 3), "text": text})
                midpoint = (word_start + word_end) / 2.0
                if text and core_start - 0.08 <= midpoint <= core_end + 0.08:
                    core_words.append(text)
            segment_rows.append(
                {
                    "start": round(safe_float(getattr(segment, "start", None)), 3),
                    "end": round(safe_float(getattr(segment, "end", None)), 3),
                    "text": str(getattr(segment, "text", "") or "").strip(),
                    "avg_logprob": round(safe_float(getattr(segment, "avg_logprob", None)), 6),
                    "no_speech_prob": round(safe_float(getattr(segment, "no_speech_prob", None)), 6),
                    "words": words,
                }
            )
        full_text = " ".join(row["text"] for row in segment_rows if row["text"]).strip()
        core_text = " ".join(core_words).strip()
        if not core_text:
            core_text = " ".join(
                row["text"]
                for row in segment_rows
                if row["text"] and min(core_end, row["end"]) - max(core_start, row["start"]) > 0
            ).strip()
        total = sum(max(0.0, row["end"] - row["start"]) for row in segment_rows)
        avg_logprob = (
            sum(row["avg_logprob"] * max(0.0, row["end"] - row["start"]) for row in segment_rows) / total
            if total > 0
            else None
        )
        result = {
            "schema": "murmurmark.residual_micro_asr/v1",
            "identity": identity,
            "status": "ok",
            "text": core_text,
            "full_text": full_text,
            "segments": segment_rows,
            "avg_logprob": round(avg_logprob, 6) if avg_logprob is not None else None,
            "language": getattr(info, "language", args.language),
            "language_probability": round(safe_float(getattr(info, "language_probability", None)), 6),
        }
    except Exception as error:
        result = {
            "schema": "murmurmark.residual_micro_asr/v1",
            "identity": identity,
            "status": "failed",
            "error": str(error),
            "text": "",
            "full_text": "",
            "segments": [],
        }
    write_json(cache_path, result)
    return result


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


def target_me_evidence(
    clips: dict[str, Path],
    backend: Any,
    target_model: dict[str, Any],
    calibration: dict[str, Any],
) -> dict[str, Any]:
    scores: dict[str, Any] = {}
    for source, clip in clips.items():
        embedding, info = backend.embed(clip)
        value = dict(info)
        value.update(target_module().model_score(embedding, target_model))
        scores[source] = value
    mic_scores = [
        (safe_float(scores.get(source, {}).get("target_similarity")), source)
        for source in (
            "mic_clean_context",
            "mic_role_masked_context",
            "mic_raw_context",
            "mic_clean",
            "mic_role_masked",
            "mic_raw",
        )
        if source in scores
    ]
    best_mic, best_source = max(mic_scores, default=(0.0, ""))
    remote = max(
        safe_float(scores.get("remote", {}).get("target_similarity")),
        safe_float(scores.get("remote_context", {}).get("target_similarity")),
    )
    target_threshold = safe_float(calibration.get("target_threshold"), 0.72)
    weak_threshold = safe_float(calibration.get("weak_target_threshold"), max(0.62, target_threshold - 0.10))
    delta = best_mic - remote
    if best_mic >= target_threshold and delta >= 0.10:
        label = "target_me_confirmed"
        confidence = min(0.97, max(0.80, 0.64 + best_mic * 0.24 + delta * 0.45))
    elif best_mic < weak_threshold and remote >= best_mic + 0.08:
        label = "target_me_absent_remote_like"
        confidence = min(0.94, max(0.74, 0.66 + (weak_threshold - best_mic) * 0.30 + (remote - best_mic) * 0.30))
    else:
        label = "target_me_ambiguous"
        confidence = min(0.74, max(0.40, best_mic, remote))
    return {
        "label": label,
        "confidence": round(confidence, 6),
        "scores": scores,
        "best_mic_source": best_source,
        "best_mic_similarity": round(best_mic, 6),
        "remote_similarity": round(remote, 6),
        "delta_vs_remote": round(delta, 6),
        "target_threshold": round(target_threshold, 6),
        "weak_target_threshold": round(weak_threshold, 6),
    }


def speaker_context_interval(
    state_rows: list[dict[str, Any]],
    start: float,
    end: float,
) -> tuple[float, float] | None:
    local_rows: list[dict[str, Any]] = []
    for row in state_rows:
        row_start = safe_float(row.get("start"))
        row_end = safe_float(row.get("end"), row_start)
        if min(end, row_end) - max(start, row_start) <= 0:
            continue
        if str(row.get("state") or "") in {"local_only", "double_talk"}:
            local_rows.append(row)
    if not local_rows:
        return None
    context_start = max(0.0, max(start - 1.0, min(safe_float(row.get("start")) for row in local_rows)))
    context_end = min(end + 1.0, max(safe_float(row.get("end")) for row in local_rows))
    if context_end - context_start < 0.55:
        return None
    context_state = target_module().interval_state_features(state_rows, context_start, context_end)
    local_support = safe_float(context_state.get("local_only_ratio")) + 0.75 * safe_float(
        context_state.get("double_talk_ratio")
    )
    if local_support < 0.40 or safe_float(context_state.get("remote_only_ratio")) > 0.30:
        return None
    return context_start, context_end


_TARGET_MODULE: ModuleType | None = None


def target_module() -> ModuleType:
    global _TARGET_MODULE
    if _TARGET_MODULE is None:
        _TARGET_MODULE = load_sibling("audit-target-me.py", "murmurmark_residual_target_me")
    return _TARGET_MODULE


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

    visit(payload)
    return result


def choose_mic_transcript(transcripts: dict[str, dict[str, Any]], target_text: str, stronger: ModuleType) -> tuple[str, str]:
    choices: list[tuple[float, str, str]] = []
    for source in ("mic_clean", "mic_raw"):
        text = str((transcripts.get(source) or {}).get("text") or "").strip()
        if not text:
            continue
        similarity = safe_float(stronger.text_similarity(text, target_text).get("similarity")) if target_text else 0.0
        logprob = safe_float((transcripts.get(source) or {}).get("avg_logprob"), -2.0)
        score = similarity * 2.0 + min(0.0, logprob) * 0.12 + min(8, len(content_tokens(text, stronger))) * 0.01
        choices.append((score, source, text))
    if not choices:
        return "", ""
    _score, source, text = max(choices)
    return source, text


def classify_residual(
    queue_row: dict[str, Any],
    *,
    target_me: dict[str, Any],
    state: dict[str, Any],
    transcripts: dict[str, dict[str, Any]],
    referenced_note_ids: set[str],
    stronger: ModuleType,
) -> dict[str, Any]:
    source = str(queue_row.get("source") or "")
    detail = queue_row.get("source_detail") if isinstance(queue_row.get("source_detail"), dict) else {}
    target_text = str(queue_row.get("target_text") or "").strip()
    remote_text = str(queue_row.get("remote_text") or "").strip()
    selected_source, selected_text = choose_mic_transcript(transcripts, target_text, stronger)
    remote_asr = str((transcripts.get("remote") or {}).get("text") or "").strip()
    remote_reference = remote_asr or remote_text
    clean_text = str((transcripts.get("mic_clean") or {}).get("text") or "").strip()
    raw_text = str((transcripts.get("mic_raw") or {}).get("text") or "").strip()
    selected_to_target = stronger.text_similarity(selected_text, target_text)
    remote_to_target = stronger.text_similarity(remote_reference, target_text)
    mic_to_remote = stronger.text_similarity(selected_text, remote_reference)
    cross_mic = stronger.text_similarity(clean_text, raw_text)
    target_tokens = set(content_tokens(target_text, stronger))
    selected_tokens = set(content_tokens(selected_text, stronger))
    remote_tokens = set(content_tokens(remote_reference, stronger))
    unique_target = sorted(target_tokens - remote_tokens)
    unique_selected = sorted(selected_tokens - remote_tokens)
    local_support = min(1.0, safe_float(state.get("local_only_ratio")) + safe_float(state.get("double_talk_ratio")) * 0.75)
    remote_active = safe_float(state.get("remote_active_ratio"))
    voice_label = str(target_me.get("label") or "")
    voice_confidence = safe_float(target_me.get("confidence"))
    remote_forbidden_pass = (
        safe_float(remote_to_target.get("similarity")) <= 0.42
        and safe_float(remote_to_target.get("containment")) <= 0.50
        and safe_float(mic_to_remote.get("similarity")) <= 0.68
    )
    mic_target_pass = (
        safe_float(selected_to_target.get("similarity")) >= 0.42
        and safe_float(selected_to_target.get("containment")) >= 0.40
    )
    hallucination = any(value in stronger.normalize_text(selected_text) for value in KNOWN_HALLUCINATIONS)
    action = "needs_review"
    reason = "combined_exact_evidence_is_not_strong_enough"
    confidence = min(0.79, max(voice_confidence, safe_float(selected_to_target.get("similarity"))))
    candidate_text = ""
    me_ids = [str(value) for value in queue_row.get("me_utterance_ids") or [] if value]

    if source == "audio_review":
        detail_class = detail.get("classification") if isinstance(detail.get("classification"), dict) else {}
        detail_label = str(detail_class.get("label") or "")
        detail_verdict = str(detail_class.get("verdict") or "")
        detail_confidence = safe_float(detail_class.get("confidence"))
        conflicting_drop = detail_label in {"remote_duplicate", "remote_leak", "asr_noise"} and detail_confidence >= 0.85
        independent_judge_support = any(
            str(row.get("label") or "") in {"confirm_me", "confirm_timing_or_doubletalk"}
            and safe_float(row.get("confidence")) >= 0.90
            for row in queue_row.get("evidence") or []
            if isinstance(row, dict)
        )
        conflict_overruled = (
            voice_label == "target_me_confirmed"
            and voice_confidence >= 0.95
            and safe_float(target_me.get("best_mic_similarity")) >= 0.88
            and safe_float(target_me.get("delta_vs_remote")) >= 0.25
            and local_support >= 0.45
            and safe_float(cross_mic.get("similarity")) >= 0.90
            and mic_target_pass
            and remote_forbidden_pass
            and independent_judge_support
        )
        if (
            len(me_ids) >= 1
            and voice_label == "target_me_confirmed"
            and voice_confidence >= 0.84
            and local_support >= 0.20
            and mic_target_pass
            and remote_forbidden_pass
            and (not conflicting_drop or conflict_overruled)
        ):
            action = "keep_me"
            reason = (
                "independent_exact_evidence_overrules_legacy_duplicate_label"
                if conflict_overruled
                else "target_me_and_exact_micro_asr_confirm_local_speech"
            )
            confidence = min(0.97, 0.45 * voice_confidence + 0.35 * safe_float(selected_to_target.get("similarity")) + 0.20)
        unique_me = sorted(target_tokens - remote_tokens)
        notes_protected = bool(set(me_ids) & referenced_note_ids)
        drop_confirmed = (
            len(me_ids) == 1
            and detail_label in {"remote_duplicate", "remote_leak", "asr_noise"}
            and detail_verdict == "probable_transcript_error"
            and detail_confidence >= 0.90
            and voice_label == "target_me_absent_remote_like"
            and voice_confidence >= 0.80
            and safe_float(remote_to_target.get("similarity")) >= 0.70
            and safe_float(remote_to_target.get("containment")) >= 0.80
            and safe_float(mic_to_remote.get("similarity")) >= 0.62
            and not unique_me
            and not has_protected_marker(target_text, stronger)
            and (not notes_protected or detail_confidence >= 0.97)
        )
        if drop_confirmed:
            action = "drop_me"
            reason = "whole_me_row_is_remote_duplicate_confirmed_by_voice_and_exact_asr"
            confidence = min(0.99, (detail_confidence + voice_confidence + safe_float(remote_to_target.get("similarity"))) / 3.0)
    elif source == "local_recall":
        words = stronger.normalize_text(selected_text).split()
        plausible_length = 1 <= len(words) <= max(3, int(duration(queue_row) * 5.5 + 2))
        latin_word_ratio = sum(1 for word in words if re.search(r"[a-z]", word)) / max(1, len(words))
        candidate_language_ok = latin_word_ratio <= 0.25
        selected_content = content_tokens(selected_text, stronger)
        short_supported = stronger.normalize_text(selected_text) in {
            "да",
            "нет",
            "ага",
            "угу",
            "ок",
            "окей",
            "привет",
            "спасибо",
            "пожалуйста",
        }
        dual_mic_support = safe_float(cross_mic.get("similarity")) >= 0.55
        strong_dual_mic_support = safe_float(cross_mic.get("similarity")) >= 0.75
        best_voice_similarity = safe_float(target_me.get("best_mic_similarity"))
        voice_delta = safe_float(target_me.get("delta_vs_remote"))
        strong_independent_identity = (
            voice_label == "target_me_confirmed"
            and voice_confidence >= 0.95
            and best_voice_similarity >= 0.80
            and voice_delta >= 0.35
            and safe_float(cross_mic.get("similarity")) >= 0.70
        )
        identity_supported = (
            (voice_label == "target_me_confirmed" and voice_confidence >= 0.86)
            or (best_voice_similarity >= 0.75 and voice_delta >= 0.20 and strong_dual_mic_support)
            or strong_independent_identity
            or (
                local_support >= 0.90
                and safe_float(cross_mic.get("similarity")) >= 0.90
                and not remote_asr
                and short_supported
            )
        )
        target_support = mic_target_pass or (
            len(target_tokens & selected_tokens) >= 2 and safe_float(selected_to_target.get("containment")) >= 0.40
        ) or (
            dual_mic_support
            and voice_label == "target_me_confirmed"
            and voice_confidence >= 0.92
        ) or (
            local_support >= 0.90
            and safe_float(cross_mic.get("similarity")) >= 0.90
            and not remote_asr
            and short_supported
        )
        local_gate = (
            local_support >= 0.45
            or (voice_label == "target_me_confirmed" and voice_confidence >= 0.94 and strong_dual_mic_support)
            or (best_voice_similarity >= 0.75 and voice_delta >= 0.25 and strong_dual_mic_support)
            or strong_independent_identity
            or (
                local_support >= 0.85
                and safe_float(cross_mic.get("similarity")) >= 0.90
                and not remote_asr
                and short_supported
            )
        )
        if (
            identity_supported
            and local_gate
            and remote_forbidden_pass
            and target_support
            and (dual_mic_support or voice_confidence >= 0.92)
            and (len(selected_content) >= 2 or short_supported)
            and (len(unique_selected) >= 2 or short_supported)
            and plausible_length
            and candidate_language_ok
            and not hallucination
        ):
            action = "insert_me"
            reason = "exact_micro_asr_recovers_remote_forbidden_target_me_island"
            confidence = min(0.97, 0.45 * voice_confidence + 0.30 * local_support + 0.25 * max(safe_float(selected_to_target.get("similarity")), safe_float(cross_mic.get("similarity"))))
            candidate_text = selected_text
    elif source == "transcript_order":
        selected_content = content_tokens(selected_text, stronger)
        proven_local_overlap = (
            voice_label == "target_me_confirmed"
            and voice_confidence >= 0.90
            and local_support >= 0.35
            and remote_forbidden_pass
            and safe_float(cross_mic.get("similarity")) >= 0.75
            and (mic_target_pass or safe_float(selected_to_target.get("similarity")) >= 0.40)
            and len(selected_content) >= 2
            and not hallucination
        )
        if proven_local_overlap:
            action = "keep_me_overlap"
            reason = "exact_interval_proves_local_speech_not_remote_duplicate"
            confidence = min(
                0.97,
                0.45 * voice_confidence
                + 0.25 * local_support
                + 0.30 * safe_float(cross_mic.get("similarity")),
            )
        else:
            reason = "v1_does_not_reorder_or_split_without_lossless_source_reconstruction"

    start, end = interval(queue_row)
    candidate_words = (
        transcript_core_word_spans(
            transcripts.get(selected_source) or {},
            interval_start=start,
            interval_end=end,
        )
        if action == "insert_me"
        else []
    )
    return {
        "action": action,
        "reason": reason,
        "confidence": round(confidence, 6),
        "candidate_text": candidate_text,
        "candidate_words": candidate_words,
        "selected_mic_source": selected_source,
        "selected_mic_text": selected_text,
        "remote_asr_text": remote_asr,
        "checks": {
            "local_support": round(local_support, 6),
            "remote_active_ratio": round(remote_active, 6),
            "remote_forbidden_pass": remote_forbidden_pass,
            "mic_target_pass": mic_target_pass,
            "hallucination": hallucination,
            "candidate_language_ok": candidate_language_ok if source == "local_recall" else True,
            "selected_to_target": selected_to_target,
            "remote_to_target": remote_to_target,
            "mic_to_remote": mic_to_remote,
            "cross_mic": cross_mic,
            "unique_target_tokens": unique_target,
            "unique_selected_tokens": unique_selected,
        },
    }


def build_session_evidence(
    session: Path,
    queue_rows: list[dict[str, Any]],
    manifest_record: dict[str, Any],
    model: Any,
    backend: Any,
    backend_status: dict[str, Any],
    args: argparse.Namespace,
    stronger: ModuleType,
) -> dict[str, Any]:
    input_paths = profile_paths(session, INPUT_PROFILE)
    dialogue = read_json(input_paths["dialogue"])
    frozen_sha = (((manifest_record.get("artifacts") or {}).get("dialogue") or {}).get("sha256"))
    if not dialogue or sha256_file(input_paths["dialogue"]) != frozen_sha:
        return {"session_id": session.name, "status": "failed_open", "error": "frozen_input_dialogue_mismatch"}
    audit_dir = session / "derived/audit/residual-me-evidence-v1"
    state_rows = target_module().load_speaker_state(session)
    enrollment_output = audit_dir / "target-me"
    target_module().now_iso = lambda: "deterministic"
    enrollment, target_model = target_module().build_enrollment(
        session,
        INPUT_PROFILE,
        [row for row in dialogue.get("utterances") or [] if isinstance(row, dict)],
        state_rows,
        enrollment_output,
        backend,
        backend_status,
        enrollment_args(),
    )
    enrollment["created_at"] = "deterministic"
    write_json(enrollment_output / "target_me_enrollment.json", enrollment)
    calibration = enrollment.get("calibration") if isinstance(enrollment.get("calibration"), dict) else {}
    sources = audio_sources(session)
    note_ids = notes_referenced_ids(session)
    rows: list[dict[str, Any]] = []
    for index, queue_row in enumerate(queue_rows, start=1):
        queue_id = str(queue_row.get("residual_queue_id") or "")
        start, end = interval(queue_row)
        clip_dir = audit_dir / "clips" / queue_id
        exact_clips: dict[str, Path] = {}
        speaker_clips: dict[str, Path] = {}
        padded_clips: dict[str, Path] = {}
        clip_failures: list[str] = []
        clip_start = max(0.0, start - args.padding_sec)
        clip_end = end + args.padding_sec
        for source, path in sources.items():
            if not path.exists():
                continue
            exact = clip_dir / f"{source}.wav"
            padded = clip_dir / f"{source}_asr.wav"
            if extract_wav(path, exact, start, end):
                exact_clips[source] = exact
            else:
                clip_failures.append(f"{source}:exact_extract_failed")
            if extract_wav(path, padded, clip_start, clip_end):
                padded_clips[source] = padded
            else:
                clip_failures.append(f"{source}:padded_extract_failed")
        context_interval = speaker_context_interval(state_rows, start, end)
        if context_interval is not None:
            context_start, context_end = context_interval
            for source, path in sources.items():
                if not path.exists():
                    continue
                context_clip = clip_dir / f"{source}_speaker_context.wav"
                if extract_wav(path, context_clip, context_start, context_end):
                    speaker_clips[f"{source}_context"] = context_clip
                else:
                    clip_failures.append(f"{source}:speaker_context_extract_failed")
        target_clips = {**exact_clips, **speaker_clips}
        target_evidence = (
            target_me_evidence(target_clips, backend, target_model, calibration)
            if enrollment.get("status") == "ready" and target_clips
            else {"label": "target_me_unavailable", "confidence": 0.0, "scores": {}}
        )
        transcripts: dict[str, dict[str, Any]] = {}
        for source in ("mic_clean", "mic_raw", "remote"):
            clip = padded_clips.get(source)
            if not clip:
                continue
            transcripts[source] = decode_exact_interval(
                model,
                clip,
                core_start=start - clip_start,
                core_end=end - clip_start,
                cache_dir=audit_dir / "micro-asr-cache",
                args=args,
            )
        state = target_module().interval_state_features(state_rows, start, end)
        disposition = classify_residual(
            queue_row,
            target_me=target_evidence,
            state=state,
            transcripts=transcripts,
            referenced_note_ids=note_ids,
            stronger=stronger,
        )
        rows.append(
            {
                "schema": "murmurmark.residual_me_evidence/v1",
                "session_id": session.name,
                "residual_queue_id": queue_id,
                "source": queue_row.get("source"),
                "source_audit_id": queue_row.get("source_audit_id"),
                "interval": queue_row.get("interval"),
                "utterance_ids": queue_row.get("utterance_ids") or [],
                "me_utterance_ids": queue_row.get("me_utterance_ids") or [],
                "remote_utterance_ids": queue_row.get("remote_utterance_ids") or [],
                "target_text": queue_row.get("target_text") or "",
                "remote_text": queue_row.get("remote_text") or "",
                "clips": {
                    source: {"path": str(path), "sha256": sha256_file(path)}
                    for source, path in target_clips.items()
                },
                "speaker_context_interval": (
                    {"start": round(context_interval[0], 3), "end": round(context_interval[1], 3)}
                    if context_interval is not None
                    else None
                ),
                "clip_failures": clip_failures,
                "speaker_state": state,
                "target_me": target_evidence,
                "micro_asr": transcripts,
                "disposition": disposition,
            }
        )
        print(f"evidence: {session.name}: {index}/{len(queue_rows)}: {disposition['action']}", flush=True)
    write_jsonl(audit_dir / "residual_me_evidence.jsonl", rows)
    summary = summarize_evidence(session, rows, enrollment, manifest_record)
    write_json(audit_dir / "residual_me_evidence_summary.json", summary)
    write_evidence_markdown(audit_dir / "residual_me_evidence_report.md", summary)
    return summary


def summarize_evidence(
    session: Path,
    rows: list[dict[str, Any]],
    enrollment: dict[str, Any],
    manifest_record: dict[str, Any],
) -> dict[str, Any]:
    by_action: dict[str, dict[str, Any]] = {}
    by_source: dict[str, dict[str, Any]] = {}
    for row in rows:
        item_seconds = duration(row)
        action = str((row.get("disposition") or {}).get("action") or "unknown")
        source = str(row.get("source") or "unknown")
        for bucket, key in ((by_action, action), (by_source, source)):
            value = bucket.setdefault(key, {"count": 0, "seconds": 0.0})
            value["count"] += 1
            value["seconds"] += item_seconds
    for value in list(by_action.values()) + list(by_source.values()):
        value["seconds"] = round(value["seconds"], 3)
    return {
        "schema": "murmurmark.residual_me_evidence_summary/v1",
        "generator": {"name": "residual-me-evidence", "version": SCRIPT_VERSION},
        "session_id": session.name,
        "input_profile": INPUT_PROFILE,
        "queue_items": len(rows),
        "queue_seconds": round(sum(duration(row) for row in rows), 3),
        "queue_sha256": sha256_bytes(canonical_bytes(rows)),
        "frozen_input_dialogue_sha256": (((manifest_record.get("artifacts") or {}).get("dialogue") or {}).get("sha256")),
        "enrollment": {
            "status": enrollment.get("status"),
            "method": enrollment.get("method"),
            "accepted_count": enrollment.get("accepted_count"),
            "accepted_total_sec": enrollment.get("accepted_total_sec"),
            "calibration": enrollment.get("calibration"),
        },
        "by_action": dict(sorted(by_action.items())),
        "by_source": dict(sorted(by_source.items())),
        "gates": {
            "passed": len(rows) == safe_int(manifest_record.get("residual_items")),
            "hard_failures": []
            if len(rows) == safe_int(manifest_record.get("residual_items"))
            else ["not_all_frozen_rows_have_evidence"],
        },
    }


def write_evidence_markdown(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# Residual Me Evidence",
        "",
        f"- Session: `{summary.get('session_id')}`",
        f"- Queue: `{summary.get('queue_items')}` rows / `{summary.get('queue_seconds')}` sec",
        f"- Enrollment: `{(summary.get('enrollment') or {}).get('status')}`",
        f"- Gates: `{(summary.get('gates') or {}).get('passed')}`",
        "",
        "## Actions",
        "",
    ]
    for action, bucket in (summary.get("by_action") or {}).items():
        lines.append(f"- `{action}`: `{bucket.get('count')}` / `{bucket.get('seconds')}` sec")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def build_evidence(args: argparse.Namespace) -> int:
    sessions_root = args.sessions_root.expanduser().resolve()
    out_dir = report_dir(sessions_root, args.out_dir)
    manifest = read_json(out_dir / "baseline_manifest.json")
    queue = read_jsonl(out_dir / "residual_queue.jsonl")
    if not manifest or not queue or (manifest.get("gates") or {}).get("passed") is not True:
        print("status: missing_or_failed_frozen_baseline", file=sys.stderr)
        return 2
    wanted = {path.expanduser().resolve().name for path in args.sessions} if args.sessions else set()
    records = [
        row
        for row in manifest.get("sessions") or []
        if isinstance(row, dict) and (not wanted or str(row.get("session_id")) in wanted)
    ]
    selected_queue = [row for row in queue if not wanted or str(row.get("session_id")) in wanted]
    resolved_model = model_path(args)
    model_bin = resolved_model / "model.bin" if resolved_model.is_dir() else resolved_model
    if not model_bin.exists():
        print(f"status: faster_whisper_model_missing: {resolved_model}", file=sys.stderr)
        return 2
    if not args.allow_download:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
    stronger = load_sibling("audit-stronger-audio-judge.py", "murmurmark_residual_stronger_judge")
    print(f"evidence: loading faster-whisper model: {resolved_model}", flush=True)
    model_args = SimpleNamespace(device=args.device, compute_type=args.compute_type, allow_download=args.allow_download)
    model = stronger.load_model(resolved_model, model_args)
    backend_args = SimpleNamespace(method="resemblyzer_dvector", wavlm_model=None)
    backend, backend_status = target_module().resolve_embedding_backend(backend_args)
    ready, reason = backend.ready()
    if not ready:
        print(f"status: target_me_backend_unavailable: {reason}", file=sys.stderr)
        return 2
    summaries: list[dict[str, Any]] = []
    by_session = {str(row.get("session_id")): row for row in records}
    for session_id in sorted(by_session):
        session = sessions_root / session_id
        session_queue = [row for row in selected_queue if str(row.get("session_id")) == session_id]
        if not session_queue:
            continue
        summaries.append(
            build_session_evidence(
                session,
                session_queue,
                by_session[session_id],
                model,
                backend,
                backend_status,
                args,
                stronger,
            )
        )
    corpus = {
        "schema": "murmurmark.residual_me_evidence_corpus/v1",
        "generator": {"name": "residual-me-evidence", "version": SCRIPT_VERSION},
        "model": model_identity(resolved_model),
        "sessions": summaries,
        "session_count": len(summaries),
        "gates": {
            "passed": bool(summaries) and all((row.get("gates") or {}).get("passed") is True for row in summaries),
            "hard_failures": [
                str(row.get("session_id"))
                for row in summaries
                if (row.get("gates") or {}).get("passed") is not True
            ],
        },
    }
    write_json(out_dir / "evidence_corpus_report.json", corpus)
    print(f"evidence_corpus_report: {out_dir / 'evidence_corpus_report.json'}")
    return 0 if corpus["gates"]["passed"] else 2


def annotate_utterance(row: dict[str, Any], evidence: dict[str, Any], *, closed: bool) -> dict[str, Any]:
    result = copy.deepcopy(row)
    quality = copy.deepcopy(result.get("quality")) if isinstance(result.get("quality"), dict) else {}
    current = quality.get("residual_me_evidence") if isinstance(quality.get("residual_me_evidence"), list) else []
    current.append(
        {
            "profile": PROFILE,
            "residual_queue_id": evidence.get("residual_queue_id"),
            "action": (evidence.get("disposition") or {}).get("action"),
            "reason": (evidence.get("disposition") or {}).get("reason"),
            "confidence": (evidence.get("disposition") or {}).get("confidence"),
            "closed": closed,
        }
    )
    quality["residual_me_evidence"] = current
    if not closed:
        quality["needs_review"] = True
    result["quality"] = quality
    return result


def normalized_word_tokens(value: Any, stronger: ModuleType) -> list[str]:
    return [token for token in stronger.normalize_text(value).split() if token]


def format_word_group(words: list[dict[str, Any]]) -> str:
    text = " ".join(str(row.get("text") or "").strip() for row in words).strip()
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r"(?<=\w)\s+-\s+(?=\w)", "-", text)
    return text


def matched_candidate_word_indexes(
    candidate_words: list[dict[str, Any]],
    existing_text: str,
    stronger: ModuleType,
) -> set[int]:
    from difflib import SequenceMatcher

    candidate_tokens = [
        normalized_word_tokens(str(row.get("text") or ""), stronger)[0]
        if normalized_word_tokens(str(row.get("text") or ""), stronger)
        else ""
        for row in candidate_words
    ]
    existing_tokens = normalized_word_tokens(existing_text, stronger)
    matched: set[int] = set()
    for block in SequenceMatcher(None, candidate_tokens, existing_tokens, autojunk=False).get_matching_blocks():
        matched.update(range(block.a, block.a + block.size))
    return matched


def partial_insert_plan(
    candidate_text: str,
    candidate_words: list[dict[str, Any]],
    existing_rows: list[dict[str, Any]],
    stronger: ModuleType,
) -> dict[str, Any]:
    ordered_existing = sorted(existing_rows, key=lambda row: (safe_float(row.get("start")), safe_float(row.get("end"))))
    existing_text = " ".join(str(row.get("text") or "").strip() for row in ordered_existing).strip()
    existing_similarity = stronger.text_similarity(candidate_text, existing_text)
    if not candidate_words:
        return {"status": "unsafe", "reason": "missing_word_timestamps", "existing_similarity": existing_similarity}

    matched_indexes = matched_candidate_word_indexes(candidate_words, existing_text, stronger)
    uncovered_groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for index, word in enumerate(candidate_words):
        midpoint = (safe_float(word.get("start")) + safe_float(word.get("end"))) / 2.0
        covered_by_time = any(
            safe_float(row.get("start")) - 0.15 <= midpoint <= safe_float(row.get("end")) + 0.15
            for row in ordered_existing
        )
        represented = covered_by_time or index in matched_indexes
        if represented:
            if current:
                uncovered_groups.append(current)
                current = []
            continue
        if current and safe_float(word.get("start")) - safe_float(current[-1].get("end")) > 0.80:
            uncovered_groups.append(current)
            current = []
        current.append(word)
    if current:
        uncovered_groups.append(current)

    fragments = [
        {
            "start": round(safe_float(group[0].get("start")), 3),
            "end": round(safe_float(group[-1].get("end")), 3),
            "text": format_word_group(group),
        }
        for group in uncovered_groups
        if len(normalized_word_tokens(format_word_group(group), stronger)) >= 2
        and safe_float(group[-1].get("end")) - safe_float(group[0].get("start")) >= 0.08
    ]
    if not fragments:
        covered = (
            safe_float(existing_similarity.get("similarity")) >= 0.65
            or safe_float(existing_similarity.get("containment")) >= 0.70
        )
        return {
            "status": "covered_existing" if covered else "unsafe",
            "reason": "existing_me_already_represents_island" if covered else "uncovered_fragment_too_weak",
            "existing_similarity": existing_similarity,
            "fragments": [],
        }

    pieces = [
        (safe_float(row.get("start")), str(row.get("text") or ""))
        for row in ordered_existing
    ] + [(safe_float(row.get("start")), str(row.get("text") or "")) for row in fragments]
    reconstructed_text = " ".join(text for _, text in sorted(pieces)).strip()
    reconstructed_similarity = stronger.text_similarity(candidate_text, reconstructed_text)
    candidate_tokens = set(content_tokens(candidate_text, stronger))
    reconstructed_tokens = set(content_tokens(reconstructed_text, stronger))
    token_coverage = len(candidate_tokens & reconstructed_tokens) / max(1, len(candidate_tokens))
    safe = (
        safe_float(reconstructed_similarity.get("similarity")) >= 0.60
        or safe_float(reconstructed_similarity.get("containment")) >= 0.70
        or token_coverage >= 0.75
    )
    return {
        "status": "insert_fragments" if safe else "unsafe",
        "reason": "word_timestamps_isolate_missing_me_fragments" if safe else "partial_reconstruction_not_supported",
        "existing_similarity": existing_similarity,
        "reconstructed_similarity": reconstructed_similarity,
        "token_coverage": round(token_coverage, 6),
        "fragments": fragments if safe else [],
    }


def merge_insertions_preserving_existing_order(
    existing: list[dict[str, Any]],
    inserted: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    result = list(existing)
    for candidate in sorted(inserted, key=lambda row: (safe_float(row.get("start")), safe_float(row.get("end")))):
        candidate_start = safe_float(candidate.get("start"))
        position = next(
            (
                index
                for index, row in enumerate(result)
                if safe_float(row.get("start")) > candidate_start
            ),
            len(result),
        )
        result.insert(position, candidate)
    return result


def output_fingerprint(paths: dict[str, Path]) -> str:
    values = {
        name: sha256_file(path)
        for name, path in paths.items()
        if name in {"dialogue", "quality", "overlaps", "transcript", "transcript_json"}
    }
    return sha256_bytes(canonical_bytes(values))


def session_profile_dir(session: Path) -> Path:
    return session / "derived/transcript-simple/whisper-cpp/residual-me-evidence-v1"


def raw_capture_matches(record: dict[str, Any]) -> tuple[bool, list[str]]:
    failures: list[str] = []
    for item in record.get("raw_capture") or []:
        if not isinstance(item, dict):
            continue
        path = Path(str(item.get("path") or ""))
        if not path.exists() or path.stat().st_size != safe_int(item.get("size")) or sha256_file(path) != item.get("sha256"):
            failures.append(str(path))
    return not failures, failures


def apply_session(args: argparse.Namespace) -> int:
    sessions_root = args.sessions_root.expanduser().resolve()
    out_dir = report_dir(sessions_root, args.out_dir)
    manifest = read_json(out_dir / "baseline_manifest.json")
    frozen_queue = read_jsonl(out_dir / "residual_queue.jsonl")
    if not manifest or not frozen_queue:
        print("status: missing_frozen_baseline", file=sys.stderr)
        return 2
    session = args.session.expanduser().resolve()
    record = next(
        (
            row
            for row in manifest.get("sessions") or []
            if isinstance(row, dict) and str(row.get("session_id")) == session.name
        ),
        None,
    )
    queue_rows = [row for row in frozen_queue if str(row.get("session_id")) == session.name]
    if not record:
        print(f"status: session_not_in_frozen_scope: {session.name}", file=sys.stderr)
        return 2
    input_paths = profile_paths(session, INPUT_PROFILE)
    output_paths = profile_paths(session, PROFILE)
    dialogue = read_json(input_paths["dialogue"])
    quality = read_json(input_paths["quality"])
    evidence_path = session / "derived/audit/residual-me-evidence-v1/residual_me_evidence.jsonl"
    evidence_rows = read_jsonl(evidence_path)
    evidence_by_id = {str(row.get("residual_queue_id")): row for row in evidence_rows}
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
    if failures:
        report = {
            "schema": "murmurmark.residual_me_evidence_profile_report/v1",
            "session_id": session.name,
            "status": "failed_open",
            "gates": {"passed": False, "hard_failures": failures, "raw_capture_failures": raw_failures},
        }
        write_json(session_profile_dir(session) / "residual_me_evidence_profile_report.json", report)
        print(f"profile_report: {session_profile_dir(session) / 'residual_me_evidence_profile_report.json'}")
        print("status: failed_open")
        return 2
    assert dialogue is not None and quality is not None
    input_utterances = [copy.deepcopy(row) for row in dialogue.get("utterances") or [] if isinstance(row, dict)]
    by_id = {str(row.get("id")): row for row in input_utterances if row.get("id")}
    output_by_id = {key: copy.deepcopy(value) for key, value in by_id.items()}
    inserted: list[dict[str, Any]] = []
    dispositions: list[dict[str, Any]] = []
    closed_ids: set[str] = set()
    dropped_ids: set[str] = set()
    stronger = load_sibling("audit-stronger-audio-judge.py", "murmurmark_residual_apply_stronger")
    for queue_row in queue_rows:
        queue_id = str(queue_row.get("residual_queue_id") or "")
        evidence = evidence_by_id.get(queue_id, {})
        decision = evidence.get("disposition") if isinstance(evidence.get("disposition"), dict) else {}
        action = str(decision.get("action") or "needs_review")
        applied_action = action
        closed = False
        reason = str(decision.get("reason") or "missing_evidence_disposition")
        me_ids = [str(value) for value in queue_row.get("me_utterance_ids") or [] if value]
        if action in {"keep_me", "keep_me_overlap"} and me_ids and all(value in output_by_id for value in me_ids):
            for value in me_ids:
                output_by_id[value] = annotate_utterance(output_by_id[value], evidence, closed=True)
            closed = True
        elif action == "drop_me" and len(me_ids) == 1 and me_ids[0] in output_by_id:
            dropped_ids.add(me_ids[0])
            closed = True
        elif action == "insert_me":
            candidate_text = str(decision.get("candidate_text") or "").strip()
            candidate_words = [
                row for row in decision.get("candidate_words") or [] if isinstance(row, dict)
            ]
            start, end = interval(queue_row)
            overlapping_me = [
                row
                for row in input_utterances
                if (
                role_name(row) == "Me"
                and min(end, safe_float(row.get("end"))) - max(start, safe_float(row.get("start"))) > 0.20
                )
            ]
            if candidate_text and not overlapping_me:
                inserted_id = f"utt_rme_{sha256_bytes(queue_id.encode('utf-8'))[:12]}"
                inserted.append(
                    {
                        "id": inserted_id,
                        "role": "me",
                        "speaker_label": "Me",
                        "source_track": "mic",
                        "start": round(start, 3),
                        "end": round(end, 3),
                        "text": candidate_text,
                        "source_segments": [],
                        "quality": {
                            "residual_me_evidence": [
                                {
                                    "profile": PROFILE,
                                    "residual_queue_id": queue_id,
                                    "action": "insert_me",
                                    "reason": reason,
                                    "confidence": decision.get("confidence"),
                                    "closed": True,
                                    "provenance": {
                                        "evidence_path": str(evidence_path),
                                        "evidence_sha256": sha256_file(evidence_path),
                                        "selected_mic_source": decision.get("selected_mic_source"),
                                        "exact_interval": queue_row.get("interval"),
                                    },
                                }
                            ]
                        },
                    }
                )
                closed = True
            elif candidate_text and overlapping_me:
                plan = partial_insert_plan(candidate_text, candidate_words, overlapping_me, stronger)
                if plan.get("status") == "covered_existing":
                    for row in overlapping_me:
                        value = str(row.get("id") or "")
                        if value in output_by_id:
                            output_by_id[value] = annotate_utterance(output_by_id[value], evidence, closed=True)
                    applied_action = "keep_me_covered"
                    reason = str(plan.get("reason") or reason)
                    closed = True
                elif plan.get("status") == "insert_fragments":
                    fragments = [row for row in plan.get("fragments") or [] if isinstance(row, dict)]
                    fragment_overlap = any(
                        min(safe_float(fragment.get("end")), safe_float(row.get("end")))
                        - max(safe_float(fragment.get("start")), safe_float(row.get("start")))
                        > 0.15
                        for fragment in fragments
                        for row in inserted
                    )
                    if not fragment_overlap:
                        for index, fragment in enumerate(fragments, start=1):
                            fragment_id = f"utt_rme_{sha256_bytes(f'{queue_id}:{index}'.encode('utf-8'))[:12]}"
                            inserted.append(
                                {
                                    "id": fragment_id,
                                    "role": "me",
                                    "speaker_label": "Me",
                                    "source_track": "mic",
                                    "start": fragment.get("start"),
                                    "end": fragment.get("end"),
                                    "text": fragment.get("text"),
                                    "source_segments": [],
                                    "quality": {
                                        "residual_me_evidence": [
                                            {
                                                "profile": PROFILE,
                                                "residual_queue_id": queue_id,
                                                "action": "insert_me_partial",
                                                "reason": plan.get("reason"),
                                                "confidence": decision.get("confidence"),
                                                "closed": True,
                                                "provenance": {
                                                    "evidence_path": str(evidence_path),
                                                    "evidence_sha256": sha256_file(evidence_path),
                                                    "selected_mic_source": decision.get("selected_mic_source"),
                                                    "exact_interval": {
                                                        "start": fragment.get("start"),
                                                        "end": fragment.get("end"),
                                                    },
                                                    "word_timestamp_reconstruction": plan,
                                                },
                                            }
                                        ]
                                    },
                                }
                            )
                        for row in overlapping_me:
                            value = str(row.get("id") or "")
                            if value in output_by_id:
                                output_by_id[value] = annotate_utterance(output_by_id[value], evidence, closed=True)
                        applied_action = "insert_me_partial"
                        reason = str(plan.get("reason") or reason)
                        closed = True
                    else:
                        reason = "partial_insert_rejected_due_to_prior_insert_overlap"
                else:
                    reason = f"partial_insert_rejected:{plan.get('reason') or 'unsafe_reconstruction'}"
            else:
                reason = "insert_rejected_due_to_missing_text_or_existing_me_overlap"
        if not closed:
            for value in me_ids:
                if value in output_by_id:
                    output_by_id[value] = annotate_utterance(output_by_id[value], evidence, closed=False)
        if closed:
            closed_ids.add(queue_id)
        dispositions.append(
            {
                "schema": "murmurmark.residual_me_disposition/v1",
                "session_id": session.name,
                "residual_queue_id": queue_id,
                "source": queue_row.get("source"),
                "source_audit_id": queue_row.get("source_audit_id"),
                "interval": queue_row.get("interval"),
                "action": applied_action if closed else "needs_review",
                "reason": reason,
                "confidence": decision.get("confidence"),
                "closed": closed,
                "evidence_sha256": sha256_bytes(canonical_bytes(evidence)) if evidence else None,
            }
        )
    existing_output = [
        output_by_id[str(row.get("id"))]
        for row in input_utterances
        if str(row.get("id")) not in dropped_ids
    ]
    output_utterances = merge_insertions_preserving_existing_order(existing_output, inserted)
    order = load_sibling("apply-transcript-order-repair.py", "murmurmark_residual_order")
    output_overlaps = order.build_overlaps(output_utterances)
    closed_rows = [row for row in dispositions if row["closed"]]
    remaining_rows = [row for row in dispositions if not row["closed"]]
    summary = {
        "queue_items": len(queue_rows),
        "queue_seconds": round(sum(duration(row) for row in queue_rows), 3),
        "closed_items": len(closed_rows),
        "closed_seconds": round(sum(duration(row) for row in queue_rows if str(row.get("residual_queue_id")) in closed_ids), 3),
        "remaining_items": len(remaining_rows),
        "remaining_seconds": round(sum(duration(row) for row in queue_rows if str(row.get("residual_queue_id")) not in closed_ids), 3),
        "inserted_me_items": len(inserted),
        "dropped_me_items": len(dropped_ids),
        "kept_me_items": sum(1 for row in closed_rows if row["action"] in {"keep_me", "keep_me_overlap"}),
        "by_action": dict(sorted(Counter(str(row["action"]) for row in dispositions).items())),
        "remaining_by_source": dict(sorted(Counter(str(row.get("source") or "unknown") for row in remaining_rows).items())),
    }
    output_quality = order.quality_report(quality, output_utterances, output_overlaps, summary)
    output_quality["residual_me_evidence"] = summary
    output_dialogue = {
        "schema": "murmurmark.clean_dialogue/v1",
        "session": dialogue.get("session", session.name),
        "utterances": output_utterances,
    }
    input_remote = [row for row in input_utterances if role_name(row) == "Colleagues"]
    output_remote = [row for row in output_utterances if role_name(row) == "Colleagues"]
    input_text_by_id = {str(row.get("id")): str(row.get("text") or "") for row in input_utterances if row.get("id")}
    existing_text_changed = [
        str(row.get("id"))
        for row in output_utterances
        if row.get("id") in input_text_by_id and str(row.get("text") or "") != input_text_by_id[str(row.get("id"))]
    ]
    hard_failures: list[str] = []
    if len(dispositions) != len(queue_rows):
        hard_failures.append("not_all_queue_rows_have_dispositions")
    if input_remote != output_remote:
        hard_failures.append("remote_utterances_changed")
    if existing_text_changed:
        hard_failures.append("existing_utterance_text_changed")
    raw_ok_after, raw_failures_after = raw_capture_matches(record)
    if not raw_ok_after:
        hard_failures.append("raw_capture_changed")
    gates = {"passed": not hard_failures, "hard_failures": hard_failures, "raw_capture_failures": raw_failures_after}
    if args.mode == "conservative" and gates["passed"]:
        write_json(output_paths["dialogue"], output_dialogue)
        write_json(output_paths["quality"], output_quality)
        write_json(
            output_paths["overlaps"],
            {"schema": "murmurmark.transcript_overlaps/v1", "session": session.name, "overlaps": output_overlaps},
        )
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
        order.write_markdown(output_paths["transcript"], output_utterances, transcribe_report.get("model"), transcribe_report.get("language"))
    report = {
        "schema": "murmurmark.residual_me_evidence_profile_report/v1",
        "generator": {"name": "residual-me-evidence", "version": SCRIPT_VERSION},
        "session_id": session.name,
        "input_profile": INPUT_PROFILE,
        "output_profile": PROFILE,
        "mode": args.mode,
        "status": "ok" if gates["passed"] else "failed_open",
        "inputs": {
            "frozen_dialogue_sha256": frozen_dialogue_sha,
            "actual_dialogue_sha256": sha256_file(input_paths["dialogue"]),
            "evidence": str(evidence_path),
            "evidence_sha256": sha256_file(evidence_path),
            "queue_sha256": (manifest.get("queue") or {}).get("sha256"),
        },
        "summary": summary,
        "gates": gates,
    }
    profile_dir = session_profile_dir(session)
    write_jsonl(profile_dir / "residual_me_applied.jsonl", closed_rows)
    write_jsonl(profile_dir / "residual_me_review_queue.jsonl", remaining_rows)
    write_jsonl(profile_dir / "residual_me_dispositions.jsonl", dispositions)
    write_json(
        profile_dir / "residual_me_diff.json",
        {
            "schema": "murmurmark.residual_me_evidence_diff/v1",
            "session_id": session.name,
            "input_profile": INPUT_PROFILE,
            "output_profile": PROFILE,
            "inserted_ids": [str(row.get("id")) for row in inserted],
            "dropped_ids": sorted(dropped_ids),
            "existing_text_changed_ids": existing_text_changed,
            "remote_unchanged": input_remote == output_remote,
        },
    )
    if args.mode == "conservative" and gates["passed"]:
        report["output_fingerprint"] = output_fingerprint(output_paths)
    write_json(profile_dir / "residual_me_evidence_profile_report.json", report)
    print(f"profile_report: {profile_dir / 'residual_me_evidence_profile_report.json'}")
    print(f"closed_items: {summary['closed_items']}")
    print(f"remaining_items: {summary['remaining_items']}")
    return 0 if gates["passed"] else 2


def selected_note_count(payload: dict[str, Any] | None) -> int:
    selected = payload.get("selected") if isinstance(payload, dict) and isinstance(payload.get("selected"), dict) else {}
    return sum(len(value) for value in selected.values() if isinstance(value, list))


def evaluate_corpus(args: argparse.Namespace) -> int:
    sessions_root = args.sessions_root.expanduser().resolve()
    out_dir = report_dir(sessions_root, args.out_dir)
    manifest = read_json(out_dir / "baseline_manifest.json")
    queue = read_jsonl(out_dir / "residual_queue.jsonl")
    if not manifest or not queue:
        print("status: missing_frozen_baseline", file=sys.stderr)
        return 2
    session_ids = sorted({str(row.get("session_id")) for row in queue})
    if args.apply:
        for session_id in session_ids:
            child = SimpleNamespace(session=sessions_root / session_id, sessions_root=sessions_root, out_dir=out_dir, mode="conservative")
            apply_session(child)
    if args.synthesize:
        script = Path(__file__).with_name("synthesize-simple-extractive.py")
        for session_id in session_ids:
            report = read_json(session_profile_dir(sessions_root / session_id) / "residual_me_evidence_profile_report.json") or {}
            if (report.get("gates") or {}).get("passed") is True:
                subprocess.run(
                    [sys.executable, str(script), str(sessions_root / session_id), "--transcript-profile", PROFILE],
                    check=True,
                )
    records = {
        str(row.get("session_id")): row
        for row in manifest.get("sessions") or []
        if isinstance(row, dict) and row.get("session_id")
    }
    session_reports: list[dict[str, Any]] = []
    hard_failures: list[str] = []
    closed_items = 0
    closed_seconds = 0.0
    remaining_items = 0
    remaining_seconds = 0.0
    for session_id in session_ids:
        session = sessions_root / session_id
        record = records.get(session_id, {})
        report_path = session_profile_dir(session) / "residual_me_evidence_profile_report.json"
        profile_report = read_json(report_path)
        if not profile_report:
            hard_failures.append(f"{session_id}:missing_profile_report")
            continue
        gates = profile_report.get("gates") if isinstance(profile_report.get("gates"), dict) else {}
        if gates.get("passed") is not True:
            hard_failures.append(f"{session_id}:profile_gates_failed")
        frozen_regressions: list[str] = []
        for name, artifact in (record.get("artifacts") or {}).items():
            if isinstance(artifact, dict) and artifact.get("sha256"):
                if sha256_file(Path(str(artifact.get("path") or ""))) != artifact.get("sha256"):
                    frozen_regressions.append(str(name))
        if frozen_regressions:
            hard_failures.append(f"{session_id}:frozen_input_artifacts_changed")
        raw_ok, raw_failures = raw_capture_matches(record)
        if not raw_ok:
            hard_failures.append(f"{session_id}:raw_capture_changed")
        output_paths = profile_paths(session, PROFILE)
        output_fingerprint_matches = profile_report.get("output_fingerprint") == output_fingerprint(output_paths)
        if not output_fingerprint_matches:
            hard_failures.append(f"{session_id}:output_fingerprint_mismatch")
        before_notes = selected_note_count(read_json(profile_paths(session, INPUT_PROFILE)["evidence_notes"]))
        after_notes = selected_note_count(read_json(output_paths["evidence_notes"]))
        if after_notes < before_notes:
            hard_failures.append(f"{session_id}:selected_notes_regressed:{before_notes}->{after_notes}")
        summary = profile_report.get("summary") if isinstance(profile_report.get("summary"), dict) else {}
        closed_items += safe_int(summary.get("closed_items"))
        closed_seconds += safe_float(summary.get("closed_seconds"))
        remaining_items += safe_int(summary.get("remaining_items"))
        remaining_seconds += safe_float(summary.get("remaining_seconds"))
        first_fingerprint = output_fingerprint(output_paths)
        child = SimpleNamespace(session=session, sessions_root=sessions_root, out_dir=out_dir, mode="conservative")
        apply_session(child)
        deterministic = first_fingerprint == output_fingerprint(output_paths)
        if not deterministic:
            hard_failures.append(f"{session_id}:profile_not_deterministic")
        session_reports.append(
            {
                "session_id": session_id,
                "gates_passed": gates.get("passed") is True,
                "summary": summary,
                "frozen_input_regressions": frozen_regressions,
                "raw_capture_unchanged": raw_ok,
                "raw_capture_failures": raw_failures,
                "output_fingerprint": profile_report.get("output_fingerprint"),
                "output_fingerprint_matches": output_fingerprint_matches,
                "deterministic": deterministic,
                "selected_notes_before": before_notes,
                "selected_notes_after": after_notes,
            }
        )
    baseline_items = safe_int((manifest.get("queue") or {}).get("item_count"))
    baseline_seconds = safe_float((manifest.get("queue") or {}).get("seconds"))
    item_ratio = closed_items / max(1, baseline_items)
    seconds_ratio = closed_seconds / max(0.001, baseline_seconds)
    if closed_items + remaining_items != baseline_items:
        hard_failures.append("not_all_frozen_rows_accounted_for")
    threshold_passed = item_ratio >= 0.25 and seconds_ratio >= 0.25
    decision = "PROMOTE_RESIDUAL_ME_EVIDENCE_V1" if not hard_failures and threshold_passed else "DO_NOT_PROMOTE"
    warnings = [] if threshold_passed else ["safe_closure_below_25_percent_target"]
    corpus_report = {
        "schema": "murmurmark.residual_me_evidence_corpus_report/v1",
        "generator": {"name": "residual-me-evidence", "version": SCRIPT_VERSION},
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
        },
        "gates": {"passed": not hard_failures and threshold_passed, "hard_failures": sorted(set(hard_failures)), "warnings": warnings},
        "promoted_sessions": session_ids if decision == "PROMOTE_RESIDUAL_ME_EVIDENCE_V1" else [],
        "sessions": session_reports,
    }
    write_json(out_dir / "residual_me_corpus_report.json", corpus_report)
    write_corpus_markdown(out_dir / "residual_me_corpus_report.md", corpus_report)
    if decision == "PROMOTE_RESIDUAL_ME_EVIDENCE_V1" and args.synthesize:
        script = Path(__file__).with_name("synthesize-simple-extractive.py")
        for session_id in session_ids:
            subprocess.run(
                [sys.executable, str(script), str(sessions_root / session_id), "--transcript-profile", PROFILE],
                check=True,
            )
    print(f"corpus_report: {out_dir / 'residual_me_corpus_report.json'}")
    print(f"decision: {decision}")
    print(f"closed_items: {closed_items}/{baseline_items}")
    print(f"closed_seconds: {round(closed_seconds, 3)}/{round(baseline_seconds, 3)}")
    return 0 if decision == "PROMOTE_RESIDUAL_ME_EVIDENCE_V1" else 2


def write_corpus_markdown(path: Path, report: dict[str, Any]) -> None:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    lines = [
        "# Residual Me Evidence Corpus",
        "",
        f"- Decision: `{report.get('decision')}`",
        f"- Frozen queue: `{summary.get('frozen_queue_items')}` rows / `{summary.get('frozen_queue_seconds')}` sec",
        f"- Safely closed: `{summary.get('closed_items')}` rows / `{summary.get('closed_seconds')}` sec",
        f"- Remaining: `{summary.get('remaining_items')}` rows / `{summary.get('remaining_seconds')}` sec",
        f"- Item closure: `{summary.get('safe_closure_item_ratio')}`",
        f"- Seconds closure: `{summary.get('safe_closure_seconds_ratio')}`",
        f"- Gates: `{(report.get('gates') or {}).get('passed')}`",
        "",
        "## Sessions",
        "",
    ]
    for row in report.get("sessions") or []:
        value = row.get("summary") if isinstance(row.get("summary"), dict) else {}
        lines.append(
            f"- `{row.get('session_id')}`: closed `{value.get('closed_items')}` / "
            f"`{value.get('closed_seconds')}` sec; deterministic `{row.get('deterministic')}`"
        )
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def run_all(args: argparse.Namespace) -> int:
    freeze_args = SimpleNamespace(sessions_root=args.sessions_root, out_dir=args.out_dir, force=args.force_freeze)
    freeze_status = freeze_corpus(freeze_args)
    if freeze_status != 0:
        return freeze_status
    evidence_status = build_evidence(args)
    if evidence_status != 0:
        return evidence_status
    evaluate_args = SimpleNamespace(
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
