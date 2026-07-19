#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import math
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from types import ModuleType
from typing import Any


SCRIPT_VERSION = "0.1.0"
PROFILE = "residual_local_recall_v1"
INPUT_PROFILE = "residual_me_evidence_v1"
REPORT_DIR_NAME = "residual-local-recall-v1"
PRIOR_REPORT_DIR_NAME = "residual-me-evidence-v1"
OUTCOMES = {
    "confirmed_missing_me",
    "already_covered",
    "duplicate_or_paraphrase",
    "remote_supported",
    "mixed_or_double_talk",
    "insufficient_local_evidence",
    "needs_review",
}
SAFE_CLOSED_OUTCOMES = {
    "confirmed_missing_me",
    "already_covered",
    "duplicate_or_paraphrase",
    "remote_supported",
}
KNOWN_HALLUCINATIONS = (
    "продолжение следует",
    "спасибо за просмотр",
    "редактор субтитров",
    "субтитры сделал",
)
MIN_CLOSED_ITEMS = 3
MIN_CLOSED_SECONDS = 9.615
VERDICT_RANK = {"failed": 0, "risky": 1, "usable_with_review": 2, "good": 3}


def load_sibling(filename: str, module_name: str) -> ModuleType:
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(module_name, path)
    if not spec or not spec.loader:
        raise RuntimeError(f"cannot load sibling module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


BASE = load_sibling("residual-me-evidence.py", "murmurmark_residual_local_base")
STRONGER = load_sibling("audit-stronger-audio-judge.py", "murmurmark_residual_local_stronger")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Close the frozen residual local-recall queue.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def common(command: argparse.ArgumentParser) -> None:
        command.add_argument("--sessions-root", type=Path, default=Path("sessions"))
        command.add_argument("--out-dir", type=Path)

    freeze = subparsers.add_parser("freeze", help="Freeze the 13-row residual local-recall queue.")
    common(freeze)
    freeze.add_argument("--force", action="store_true")

    evidence = subparsers.add_parser("evidence", help="Resolve evidence for frozen rows.")
    common(evidence)
    evidence.add_argument("sessions", nargs="*", type=Path)

    apply = subparsers.add_parser("apply", help="Apply insertion-only decisions to one session.")
    common(apply)
    apply.add_argument("session", type=Path)
    apply.add_argument("--mode", choices=("conservative", "dry-run"), default="conservative")

    evaluate = subparsers.add_parser("evaluate", help="Evaluate the candidate corpus profile.")
    common(evaluate)
    evaluate.add_argument("--apply", action="store_true")
    evaluate.add_argument("--synthesize", action="store_true")

    run = subparsers.add_parser("run", help="Freeze, resolve, apply and evaluate the corpus.")
    common(run)
    run.add_argument("--force-freeze", action="store_true")
    run.add_argument("--skip-synthesis", action="store_true")
    return parser.parse_args()


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return default if math.isnan(number) or math.isinf(number) else number


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
    return session / "derived/transcript-simple/whisper-cpp/residual-local-recall-v1"


def input_profile_dir(session: Path) -> Path:
    return session / "derived/transcript-simple/whisper-cpp/residual-me-evidence-v1"


def audit_dir(session: Path) -> Path:
    return session / "derived/audit/residual-local-recall-v1"


def prior_audit_dir(session: Path) -> Path:
    return session / "derived/audit/residual-me-evidence-v1"


def role_name(row: dict[str, Any]) -> str:
    return BASE.role_name(row)


def artifact(path: Path) -> dict[str, Any]:
    return {"path": str(path), "exists": path.exists(), "sha256": sha256_file(path)}


def raw_fingerprints(session: Path) -> list[dict[str, Any]]:
    return BASE.raw_fingerprints(session)


def artifact_fingerprints(session: Path, profile: str) -> dict[str, Any]:
    return BASE.artifact_fingerprints(session, profile)


def output_fingerprint(paths: dict[str, Path]) -> str:
    return BASE.output_fingerprint(paths)


def row_fingerprint(row: dict[str, Any]) -> str:
    return sha256_bytes(canonical_bytes(row))


def source_queue() -> Path:
    return Path("sessions/_reports/residual-me-evidence-v1/residual_queue.jsonl")


def target_queue_rows(sessions_root: Path) -> tuple[list[dict[str, Any]], list[str]]:
    prior_dir = sessions_root / "_reports" / PRIOR_REPORT_DIR_NAME
    prior_queue = read_jsonl(prior_dir / "residual_queue.jsonl")
    prior_by_id = {str(row.get("residual_queue_id") or ""): row for row in prior_queue}
    prior_manifest = read_json(sessions_root / "_reports/residual-audio-arbitration-v1/baseline_manifest.json") or {}
    session_ids = [str(value) for value in (prior_manifest.get("scope") or {}).get("session_ids") or []]
    failures: list[str] = []
    frozen: list[dict[str, Any]] = []
    for session_id in session_ids:
        session = sessions_root / session_id
        review_path = input_profile_dir(session) / "residual_me_review_queue.jsonl"
        for disposition in read_jsonl(review_path):
            if str(disposition.get("source") or "") != "local_recall":
                continue
            queue_id = str(disposition.get("residual_queue_id") or "")
            source = prior_by_id.get(queue_id)
            if not source:
                failures.append(f"{session_id}:{queue_id}:missing_frozen_source_row")
                continue
            row = copy.deepcopy(source)
            row["schema"] = "murmurmark.residual_local_recall_queue_item/v1"
            row["input_disposition"] = copy.deepcopy(disposition)
            row["input_disposition_sha256"] = row_fingerprint(disposition)
            frozen.append(row)
    frozen.sort(key=lambda row: (str(row.get("session_id") or ""), str(row.get("residual_queue_id") or "")))
    return frozen, failures


def session_frozen_artifacts(session: Path) -> dict[str, Any]:
    audio = session / "derived/preprocess/audio"
    prior = prior_audit_dir(session)
    return {
        "raw_capture": raw_fingerprints(session),
        "working_audio": {
            "mic_raw": artifact(audio / "mic_raw_for_asr.wav"),
            "mic_clean": artifact(audio / "mic_clean_local_fir.wav"),
            "mic_role_masked": artifact(audio / "mic_role_masked_for_asr.wav"),
            "remote": artifact(audio / "remote_for_aec.wav"),
        },
        "speaker_state": artifact(session / "derived/preprocess/echo/speaker_state.jsonl"),
        "input_profile": artifact_fingerprints(session, INPUT_PROFILE),
        "input_review_queue": artifact(input_profile_dir(session) / "residual_me_review_queue.jsonl"),
        "target_me_enrollment": artifact(prior / "target-me/target_me_enrollment.json"),
        "prior_evidence": artifact(prior / "residual_me_evidence.jsonl"),
    }


def model_identity(rows: list[dict[str, Any]]) -> dict[str, Any]:
    identities: list[dict[str, Any]] = []
    for row in rows:
        for run in ((row.get("micro_asr") or {}).values() if isinstance(row.get("micro_asr"), dict) else []):
            if isinstance(run, dict) and isinstance(run.get("identity"), dict):
                identities.append(copy.deepcopy(run["identity"]))
    models = []
    for identity in identities:
        model = identity.get("model") if isinstance(identity.get("model"), dict) else {}
        if model and model not in models:
            models.append(model)
    return {
        "backend": "faster-whisper",
        "word_timestamps_required": True,
        "models": models,
        "decode_identity_sha256": sha256_bytes(canonical_bytes(identities)),
    }


def freeze_corpus(args: argparse.Namespace) -> int:
    sessions_root = args.sessions_root.expanduser().resolve()
    out_dir = report_dir(sessions_root, args.out_dir)
    baseline_path = out_dir / "residual_local_recall_baseline.json"
    queue_path = out_dir / "residual_local_recall_queue.jsonl"
    if baseline_path.exists() and queue_path.exists() and not args.force:
        print(f"baseline: {baseline_path}")
        print(f"queue: {queue_path}")
        print("status: already_frozen")
        return 0
    frozen, failures = target_queue_rows(sessions_root)
    if len(frozen) != 13:
        failures.append(f"expected_13_rows_got_{len(frozen)}")
    seconds = round(sum(duration(row) for row in frozen), 3)
    if abs(seconds - 48.073) > 0.001:
        failures.append(f"expected_48.073_seconds_got_{seconds}")
    session_ids = sorted({str(row.get("session_id") or "") for row in frozen})
    prior_audio_manifest = read_json(
        sessions_root / "_reports/residual-audio-arbitration-v1/baseline_manifest.json"
    ) or {}
    all_session_ids = [
        str(value) for value in (prior_audio_manifest.get("scope") or {}).get("session_ids") or []
    ]
    sessions = []
    prior_rows: list[dict[str, Any]] = []
    chronology_rows = [
        row
        for session_id in all_session_ids
        for row in read_jsonl(
            input_profile_dir(sessions_root / session_id) / "residual_me_review_queue.jsonl"
        )
        if str(row.get("source") or "") == "transcript_order"
    ]
    for session_id in session_ids:
        session = sessions_root / session_id
        prior_path = prior_audit_dir(session) / "residual_me_evidence.jsonl"
        prior_by_id = {str(row.get("residual_queue_id") or ""): row for row in read_jsonl(prior_path)}
        target_ids = {str(row.get("residual_queue_id") or "") for row in frozen if row.get("session_id") == session_id}
        selected_prior = [prior_by_id[value] for value in sorted(target_ids) if value in prior_by_id]
        if len(selected_prior) != len(target_ids):
            failures.append(f"{session_id}:missing_prior_evidence")
        prior_rows.extend(selected_prior)
        sessions.append(
            {
                "session_id": session_id,
                "artifacts": session_frozen_artifacts(session),
                "queue_ids": sorted(target_ids),
                "queue_sha256": sha256_bytes(
                    canonical_bytes([row for row in frozen if row.get("session_id") == session_id])
                ),
                "prior_evidence_sha256": sha256_bytes(canonical_bytes(selected_prior)),
            }
        )
    prior_audio = read_json(sessions_root / "_reports/residual-audio-arbitration-v1/residual_audio_corpus_report.json") or {}
    baseline = {
        "schema": "murmurmark.residual_local_recall_baseline/v1",
        "generator": {"name": "residual-local-recall", "version": SCRIPT_VERSION},
        "input_profile": INPUT_PROFILE,
        "output_profile": PROFILE,
        "queue": {
            "item_count": len(frozen),
            "seconds": seconds,
            "sha256": sha256_bytes(canonical_bytes(frozen)),
        },
        "excluded_queues": {
            "audio_review": {
                "item_count": 66,
                "seconds": 196.92,
                "report_sha256": sha256_file(
                    sessions_root / "_reports/residual-audio-arbitration-v1/residual_audio_corpus_report.json"
                ),
                "decision": prior_audio.get("decision"),
            },
            "transcript_order": {
                "item_count": len(chronology_rows),
                "seconds": round(sum(duration(row) for row in chronology_rows), 3),
                "sha256": sha256_bytes(canonical_bytes(chronology_rows)),
                "session_ids": all_session_ids,
            },
        },
        "resolver": {
            "version": SCRIPT_VERSION,
            "coverage_window_sec": 20.0,
            "coverage_similarity": 0.82,
            "remote_support_similarity": 0.60,
            "insertion_requires_two_mic_sources": True,
            "insertion_requires_word_timestamps": True,
        },
        "models": model_identity(prior_rows),
        "sessions": sessions,
        "gates": {"passed": not failures, "hard_failures": failures},
    }
    write_json(baseline_path, baseline)
    write_jsonl(queue_path, frozen)
    print(f"baseline: {baseline_path}")
    print(f"queue: {queue_path}")
    print(f"items: {len(frozen)}")
    print(f"seconds: {seconds}")
    return 0 if not failures else 2


def similarity(left: Any, right: Any) -> dict[str, float]:
    return STRONGER.text_similarity(left, right)


def content_tokens(value: Any) -> list[str]:
    return STRONGER.content_tokens(value)


def nearby_me(dialogue: dict[str, Any], start: float, end: float, window: float = 20.0) -> list[dict[str, Any]]:
    return [
        row
        for row in dialogue.get("utterances") or []
        if isinstance(row, dict)
        and role_name(row) == "Me"
        and safe_float(row.get("end")) >= start - window
        and safe_float(row.get("start")) <= end + window
    ]


def coverage_evidence(target_text: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    ordered = sorted(rows, key=lambda row: (safe_float(row.get("start")), safe_float(row.get("end"))))
    for index, row in enumerate(ordered):
        groups = [[row]]
        if index + 1 < len(ordered) and safe_float(ordered[index + 1].get("start")) - safe_float(row.get("end")) <= 3.0:
            groups.append([row, ordered[index + 1]])
        for group in groups:
            text = " ".join(str(value.get("text") or "").strip() for value in group).strip()
            score = similarity(target_text, text)
            candidates.append(
                {
                    "utterance_ids": [str(value.get("id") or "") for value in group],
                    "text": text,
                    "similarity": score,
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
        (
            safe_float(metrics.get("containment")) >= 0.82
            and safe_float(metrics.get("similarity")) >= 0.68
        )
        or (
            safe_float(metrics.get("containment")) >= 0.60
            and safe_float(metrics.get("sequence_ratio")) >= 0.86
        )
    )
    return {**best, "covered": covered}


def valid_word_timestamps(run: dict[str, Any]) -> bool:
    identity = run.get("identity") if isinstance(run.get("identity"), dict) else {}
    words = [word for segment in run.get("segments") or [] for word in segment.get("words") or []]
    return bool(identity.get("word_timestamps") is True and words and all("start" in word and "end" in word for word in words))


def local_asr_summary(prior: dict[str, Any]) -> dict[str, Any]:
    runs = prior.get("micro_asr") if isinstance(prior.get("micro_asr"), dict) else {}
    return {
        source: {
            "status": run.get("status"),
            "text": run.get("text"),
            "avg_logprob": run.get("avg_logprob"),
            "identity": run.get("identity"),
            "word_timestamps_valid": valid_word_timestamps(run),
            "run_sha256": row_fingerprint(run),
        }
        for source, run in sorted(runs.items())
        if isinstance(run, dict)
    }


def confirmed_insert_candidate(prior: dict[str, Any], start: float, end: float) -> dict[str, Any] | None:
    target = prior.get("target_me") if isinstance(prior.get("target_me"), dict) else {}
    state = prior.get("speaker_state") if isinstance(prior.get("speaker_state"), dict) else {}
    runs = prior.get("micro_asr") if isinstance(prior.get("micro_asr"), dict) else {}
    clean = runs.get("mic_clean") if isinstance(runs.get("mic_clean"), dict) else {}
    raw = runs.get("mic_raw") if isinstance(runs.get("mic_raw"), dict) else {}
    remote = runs.get("remote") if isinstance(runs.get("remote"), dict) else {}
    clean_text = str(clean.get("text") or "").strip()
    raw_text = str(raw.get("text") or "").strip()
    remote_text = str(remote.get("text") or "").strip()
    selected = clean if safe_float(clean.get("avg_logprob"), -99.0) >= safe_float(raw.get("avg_logprob"), -99.0) else raw
    selected_source = "mic_clean" if selected is clean else "mic_raw"
    selected_text = str(selected.get("text") or "").strip()
    mic_agreement = similarity(clean_text, raw_text)
    remote_similarity = similarity(selected_text, remote_text)
    hallucination = any(value in STRONGER.normalize_text(selected_text) for value in KNOWN_HALLUCINATIONS)
    words = [word for segment in selected.get("segments") or [] for word in segment.get("words") or [] if isinstance(word, dict)]
    core_start = safe_float((selected.get("identity") or {}).get("core_start"), 0.0)
    core_words = [
        word
        for word in words
        if safe_float(word.get("end")) > core_start
        and safe_float(word.get("start")) < core_start + max(0.0, end - start)
    ]
    candidate_text = " ".join(str(word.get("text") or "").strip() for word in core_words).strip()
    candidate_start = start + max(0.0, min((safe_float(word.get("start")) - core_start for word in core_words), default=0.0))
    candidate_end = start + min(end - start, max((safe_float(word.get("end")) - core_start for word in core_words), default=0.0))
    checks = {
        "target_me_confirmed": str(target.get("label") or "") == "target_me_confirmed",
        "target_me_confidence": safe_float(target.get("confidence")),
        "target_me_delta_vs_remote": safe_float(target.get("delta_vs_remote")),
        "local_only_ratio": safe_float(state.get("local_only_ratio")),
        "remote_active_ratio": safe_float(state.get("remote_active_ratio")),
        "mic_agreement": mic_agreement,
        "mic_to_remote": remote_similarity,
        "word_timestamps": valid_word_timestamps(selected),
        "content_token_count": len(content_tokens(candidate_text)),
        "hallucination": hallucination,
        "timestamps_inside_interval": start <= candidate_start < candidate_end <= end,
    }
    passed = (
        checks["target_me_confirmed"]
        and checks["target_me_confidence"] >= 0.90
        and checks["target_me_delta_vs_remote"] >= 0.20
        and checks["local_only_ratio"] >= 0.55
        and checks["remote_active_ratio"] <= 0.15
        and safe_float(mic_agreement.get("similarity")) >= 0.72
        and safe_float(remote_similarity.get("similarity")) <= 0.42
        and checks["word_timestamps"]
        and checks["content_token_count"] >= 3
        and not hallucination
        and checks["timestamps_inside_interval"]
    )
    if not passed:
        return None
    return {
        "text": candidate_text,
        "start": round(candidate_start, 3),
        "end": round(candidate_end, 3),
        "source": selected_source,
        "words": core_words,
        "checks": checks,
    }


def cyrillic_content_tokens(value: Any) -> set[str]:
    return {
        normalized
        for token in content_tokens(value)
        if (normalized := re.sub(r"[^а-яё]", "", token.lower()))
    }


def insertion_already_covered(insertion_text: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    normal = coverage_evidence(insertion_text, rows)
    if normal.get("covered"):
        return normal
    candidate_tokens = cyrillic_content_tokens(insertion_text)
    best: dict[str, Any] = {"covered": False, "utterance_ids": [], "text": "", "cyrillic_containment": 0.0}
    if len(candidate_tokens) < 3:
        return best
    for row in rows:
        row_tokens = cyrillic_content_tokens(row.get("text"))
        containment = len(candidate_tokens & row_tokens) / len(candidate_tokens)
        if containment > safe_float(best.get("cyrillic_containment")):
            best = {
                "covered": containment >= 0.80,
                "utterance_ids": [str(row.get("id") or "")],
                "text": str(row.get("text") or ""),
                "cyrillic_containment": round(containment, 6),
            }
    return best


def resolve_row(queue_row: dict[str, Any], prior: dict[str, Any], dialogue: dict[str, Any]) -> dict[str, Any]:
    start, end = interval(queue_row)
    target_text = str(queue_row.get("target_text") or prior.get("target_text") or "").strip()
    target = prior.get("target_me") if isinstance(prior.get("target_me"), dict) else {}
    state = prior.get("speaker_state") if isinstance(prior.get("speaker_state"), dict) else {}
    disposition = prior.get("disposition") if isinstance(prior.get("disposition"), dict) else {}
    checks = disposition.get("checks") if isinstance(disposition.get("checks"), dict) else {}
    required_ready = bool(target and prior.get("micro_asr") and target_text)
    coverage = coverage_evidence(target_text, nearby_me(dialogue, start, end))
    outcome = "needs_review"
    reason = "required_evidence_missing"
    action = "needs_review"
    confidence = 0.0
    insertion: dict[str, Any] | None = None

    if required_ready and coverage.get("covered"):
        outcome = "already_covered"
        reason = "target_content_is_already_present_in_nearby_me_utterance"
        action = "close_without_change"
        confidence = min(0.99, max(
            safe_float((coverage.get("similarity") or {}).get("containment")),
            safe_float((coverage.get("similarity") or {}).get("similarity")),
        ))
    elif required_ready:
        remote_to_target = checks.get("remote_to_target") if isinstance(checks.get("remote_to_target"), dict) else {}
        local_only = safe_float(state.get("local_only_ratio"))
        remote_only = safe_float(state.get("remote_only_ratio"))
        remote_active = safe_float(state.get("remote_active_ratio"))
        voice_label = str(target.get("label") or "")
        remote_supported = (
            local_only <= 0.05
            and voice_label != "target_me_confirmed"
            and (
                (remote_only >= 0.80 and safe_float(remote_to_target.get("similarity")) >= 0.55)
                or safe_float(remote_to_target.get("similarity")) >= 0.78
            )
        )
        if remote_supported:
            outcome = "remote_supported"
            reason = "remote_state_and_text_support_the_candidate_without_target_me_confirmation"
            action = "close_without_change"
            confidence = min(0.97, max(remote_only, safe_float(remote_to_target.get("similarity"))))
        else:
            insertion = confirmed_insert_candidate(prior, start, end)
            if insertion:
                insertion_coverage = insertion_already_covered(
                    insertion["text"], nearby_me(dialogue, start, end)
                )
                remote_text = str((prior.get("micro_asr") or {}).get("remote", {}).get("text") or "")
                remote_match = similarity(insertion["text"], remote_text)
                overlapping_remote = [
                    row
                    for row in dialogue.get("utterances") or []
                    if isinstance(row, dict)
                    and role_name(row) == "Colleagues"
                    and min(insertion["end"], safe_float(row.get("end")))
                    - max(insertion["start"], safe_float(row.get("start"))) > 0.20
                ]
                if insertion_coverage.get("covered"):
                    outcome = "duplicate_or_paraphrase"
                    reason = "speaker_confirmed_candidate_is_already_covered_by_nearby_me_text"
                    action = "close_without_change"
                    confidence = 0.90
                    insertion = None
                elif safe_float(remote_match.get("similarity")) <= 0.42 and not overlapping_remote:
                    outcome = "confirmed_missing_me"
                    reason = "two_mic_asr_and_target_me_confirm_remote_forbidden_missing_speech"
                    action = "insert_me"
                    confidence = min(0.97, safe_float(target.get("confidence")))
                else:
                    outcome = "mixed_or_double_talk"
                    reason = "candidate_words_overlap_remote_or_guarded_remote_interval"
                    action = "needs_review"
                    confidence = 0.75
                    insertion = None
            elif voice_label == "target_me_confirmed" and (remote_active > 0.15 or safe_float((checks.get("mic_to_remote") or {}).get("similarity")) > 0.42):
                outcome = "mixed_or_double_talk"
                reason = "target_me_is_present_but_remote_forbidden_words_cannot_be_separated"
                action = "needs_review"
                confidence = min(0.85, safe_float(target.get("confidence")))
            else:
                outcome = "insufficient_local_evidence"
                reason = "local_audio_or_word_evidence_is_not_strong_enough_for_insertion"
                action = "needs_review"
                confidence = max(0.40, min(0.79, safe_float(target.get("confidence"))))

    evidence = {
        "schema": "murmurmark.residual_local_recall_evidence/v1",
        "session_id": queue_row.get("session_id"),
        "residual_queue_id": queue_row.get("residual_queue_id"),
        "source_audit_id": queue_row.get("source_audit_id"),
        "interval": queue_row.get("interval"),
        "target_text": target_text,
        "outcome": outcome,
        "action": action,
        "reason": reason,
        "confidence": round(confidence, 6),
        "coverage": coverage,
        "insertion": insertion,
        "speaker_state": copy.deepcopy(state),
        "target_me": copy.deepcopy(target),
        "local_asr": local_asr_summary(prior),
        "prior_evidence_sha256": row_fingerprint(prior) if prior else None,
        "input_row_sha256": row_fingerprint(queue_row),
    }
    evidence["evidence_fingerprint"] = row_fingerprint(evidence)
    return evidence


def build_session_evidence(session: Path, queue_rows: list[dict[str, Any]], record: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    failures: list[str] = []
    input_paths = profile_paths(session, INPUT_PROFILE)
    dialogue = read_json(input_paths["dialogue"])
    prior_path = prior_audit_dir(session) / "residual_me_evidence.jsonl"
    prior_by_id = {str(row.get("residual_queue_id") or ""): row for row in read_jsonl(prior_path)}
    if not dialogue:
        failures.append("missing_input_dialogue")
        dialogue = {"utterances": []}
    frozen = record.get("artifacts") if isinstance(record.get("artifacts"), dict) else {}
    actual = session_frozen_artifacts(session)
    if row_fingerprint(frozen) != row_fingerprint(actual):
        failures.append("frozen_session_artifacts_changed")
    rows = []
    for queue_row in queue_rows:
        queue_id = str(queue_row.get("residual_queue_id") or "")
        prior = prior_by_id.get(queue_id)
        if not prior:
            failures.append(f"{queue_id}:missing_prior_evidence")
            prior = {}
        rows.append(resolve_row(queue_row, prior, dialogue))
    return rows, failures


def evidence_summary(session_id: str, rows: list[dict[str, Any]], failures: list[str]) -> dict[str, Any]:
    by_outcome = Counter(str(row.get("outcome") or "unknown") for row in rows)
    closed = [row for row in rows if str(row.get("outcome") or "") in SAFE_CLOSED_OUTCOMES]
    return {
        "schema": "murmurmark.residual_local_recall_summary/v1",
        "generator": {"name": "residual-local-recall", "version": SCRIPT_VERSION},
        "session_id": session_id,
        "queue_items": len(rows),
        "closed_items": len(closed),
        "closed_seconds": round(sum(duration(row) for row in closed), 3),
        "insert_items": sum(1 for row in rows if row.get("action") == "insert_me"),
        "by_outcome": dict(sorted(by_outcome.items())),
        "gates": {"passed": not failures, "hard_failures": failures},
    }


def write_session_markdown(path: Path, summary: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    lines = [
        "# Residual Local Recall v1",
        "",
        f"Session: `{summary.get('session_id')}`",
        f"Closed: `{summary.get('closed_items')}/{summary.get('queue_items')}`",
        "",
        "## Outcomes",
        "",
    ]
    for row in rows:
        interval_value = row.get("interval") or {}
        lines.append(
            f"- `{row.get('residual_queue_id')}` {interval_value.get('start')}-{interval_value.get('end')}s: "
            f"`{row.get('outcome')}` - {row.get('reason')}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_evidence(args: argparse.Namespace) -> int:
    sessions_root = args.sessions_root.expanduser().resolve()
    out_dir = report_dir(sessions_root, args.out_dir)
    baseline = read_json(out_dir / "residual_local_recall_baseline.json") or {}
    queue = read_jsonl(out_dir / "residual_local_recall_queue.jsonl")
    if not baseline or not queue or not (baseline.get("gates") or {}).get("passed"):
        print("status: missing_or_invalid_frozen_baseline", file=sys.stderr)
        return 2
    requested = {path.expanduser().resolve().name for path in args.sessions}
    failures: list[str] = []
    corpus_sessions = []
    for record in baseline.get("sessions") or []:
        session_id = str(record.get("session_id") or "")
        if requested and session_id not in requested:
            continue
        session = sessions_root / session_id
        session_rows = [row for row in queue if row.get("session_id") == session_id]
        rows, session_failures = build_session_evidence(session, session_rows, record)
        destination = audit_dir(session)
        write_jsonl(destination / "residual_local_recall_evidence.jsonl", rows)
        write_jsonl(
            destination / "local_asr_runs.jsonl",
            [
                {
                    "schema": "murmurmark.residual_local_asr_run/v1",
                    "session_id": session_id,
                    "residual_queue_id": row.get("residual_queue_id"),
                    "runs": row.get("local_asr"),
                }
                for row in rows
            ],
        )
        summary = evidence_summary(session_id, rows, session_failures)
        write_json(destination / "residual_local_recall_summary.json", summary)
        write_session_markdown(destination / "residual_local_recall_report.md", summary, rows)
        corpus_sessions.append(summary)
        failures.extend(f"{session_id}:{value}" for value in session_failures)
        print(f"evidence: {session_id}: {len(rows)} rows", flush=True)
    report = {
        "schema": "murmurmark.residual_local_recall_evidence_corpus/v1",
        "generator": {"name": "residual-local-recall", "version": SCRIPT_VERSION},
        "sessions": corpus_sessions,
        "gates": {"passed": not failures, "hard_failures": failures},
    }
    write_json(out_dir / "residual_local_recall_evidence_corpus.json", report)
    return 0 if not failures else 2


def verify_frozen_session(session: Path, record: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    frozen = record.get("artifacts") if isinstance(record.get("artifacts"), dict) else {}
    if row_fingerprint(frozen) != row_fingerprint(session_frozen_artifacts(session)):
        failures.append("frozen_session_artifacts_changed")
    return failures


def insertion_id(queue_id: str) -> str:
    return f"utt_rlr_{sha256_bytes(queue_id.encode('utf-8'))[:12]}"


def apply_session(args: argparse.Namespace) -> int:
    sessions_root = args.sessions_root.expanduser().resolve()
    out_dir = report_dir(sessions_root, args.out_dir)
    baseline = read_json(out_dir / "residual_local_recall_baseline.json") or {}
    queue = read_jsonl(out_dir / "residual_local_recall_queue.jsonl")
    session = args.session.expanduser().resolve()
    record = next((row for row in baseline.get("sessions") or [] if row.get("session_id") == session.name), None)
    if not record:
        print(f"status: session_not_in_frozen_scope: {session.name}", file=sys.stderr)
        return 2
    session_queue = [row for row in queue if row.get("session_id") == session.name]
    evidence_path = audit_dir(session) / "residual_local_recall_evidence.jsonl"
    evidence_rows = read_jsonl(evidence_path)
    evidence_by_id = {str(row.get("residual_queue_id") or ""): row for row in evidence_rows}
    input_paths = profile_paths(session, INPUT_PROFILE)
    output_paths = profile_paths(session, PROFILE)
    dialogue = read_json(input_paths["dialogue"])
    quality = read_json(input_paths["quality"])
    failures = verify_frozen_session(session, record)
    if not dialogue or not quality:
        failures.append("missing_input_profile")
    if len(evidence_by_id) != len(session_queue):
        failures.append("not_all_frozen_rows_have_evidence")
    if failures:
        write_json(
            profile_dir(session) / "residual_local_recall_profile_report.json",
            {
                "schema": "murmurmark.residual_local_recall_profile_report/v1",
                "session_id": session.name,
                "status": "failed_open",
                "gates": {"passed": False, "hard_failures": failures},
            },
        )
        return 2
    assert dialogue is not None and quality is not None
    input_utterances = [copy.deepcopy(row) for row in dialogue.get("utterances") or [] if isinstance(row, dict)]
    inserted: list[dict[str, Any]] = []
    dispositions: list[dict[str, Any]] = []
    closed_ids: set[str] = set()
    for queue_row in session_queue:
        queue_id = str(queue_row.get("residual_queue_id") or "")
        evidence = evidence_by_id.get(queue_id, {})
        outcome = str(evidence.get("outcome") or "needs_review")
        action = str(evidence.get("action") or "needs_review")
        closed = outcome in SAFE_CLOSED_OUTCOMES
        applied_action = action
        reason = str(evidence.get("reason") or "missing_evidence")
        if action == "insert_me":
            insertion = evidence.get("insertion") if isinstance(evidence.get("insertion"), dict) else {}
            candidate_start = safe_float(insertion.get("start"))
            candidate_end = safe_float(insertion.get("end"))
            overlap = any(
                min(candidate_end, safe_float(row.get("end"))) - max(candidate_start, safe_float(row.get("start"))) > 0.15
                for row in input_utterances + inserted
            )
            if not insertion.get("text") or overlap:
                closed = False
                applied_action = "needs_review"
                reason = "insertion_rejected_due_to_missing_text_or_chronology_ambiguity"
            else:
                inserted.append(
                    {
                        "id": insertion_id(queue_id),
                        "role": "me",
                        "speaker_label": "Me",
                        "source_track": "mic",
                        "start": candidate_start,
                        "end": candidate_end,
                        "text": insertion.get("text"),
                        "source_segments": [],
                        "quality": {
                            "residual_local_recall": {
                                "profile": PROFILE,
                                "residual_queue_id": queue_id,
                                "source_audit_id": queue_row.get("source_audit_id"),
                                "evidence_fingerprint": evidence.get("evidence_fingerprint"),
                                "closed": True,
                            }
                        },
                    }
                )
        if closed:
            closed_ids.add(queue_id)
        dispositions.append(
            {
                "schema": "murmurmark.residual_local_recall_disposition/v1",
                "session_id": session.name,
                "residual_queue_id": queue_id,
                "source": "local_recall",
                "source_audit_id": queue_row.get("source_audit_id"),
                "interval": queue_row.get("interval"),
                "outcome": outcome,
                "action": applied_action if closed else "needs_review",
                "reason": reason,
                "confidence": evidence.get("confidence"),
                "closed": closed,
                "evidence_fingerprint": evidence.get("evidence_fingerprint"),
            }
        )
    output_utterances = BASE.merge_insertions_preserving_existing_order(input_utterances, inserted)
    order = load_sibling("apply-transcript-order-repair.py", "murmurmark_residual_local_order")
    overlaps = order.build_overlaps(output_utterances)
    closed_rows = [row for row in dispositions if row.get("closed") is True]
    rejected_rows = [row for row in dispositions if row.get("closed") is not True]
    summary = {
        "queue_items": len(session_queue),
        "queue_seconds": round(sum(duration(row) for row in session_queue), 3),
        "closed_items": len(closed_rows),
        "closed_seconds": round(
            sum(duration(row) for row in session_queue if str(row.get("residual_queue_id") or "") in closed_ids), 3
        ),
        "remaining_items": len(rejected_rows),
        "remaining_seconds": round(
            sum(duration(row) for row in session_queue if str(row.get("residual_queue_id") or "") not in closed_ids), 3
        ),
        "inserted_me_items": len(inserted),
        "by_outcome": dict(sorted(Counter(str(row.get("outcome") or "unknown") for row in dispositions).items())),
    }
    output_quality = order.quality_report(quality, output_utterances, overlaps, summary)
    output_quality["residual_local_recall"] = summary
    output_dialogue = {
        "schema": "murmurmark.clean_dialogue/v1",
        "session": dialogue.get("session", session.name),
        "utterances": output_utterances,
    }
    input_remote = [row for row in input_utterances if role_name(row) == "Colleagues"]
    output_remote = [row for row in output_utterances if role_name(row) == "Colleagues"]
    input_by_id = {str(row.get("id")): row for row in input_utterances if row.get("id")}
    existing_changed = [
        str(row.get("id"))
        for row in output_utterances
        if str(row.get("id")) in input_by_id and row != input_by_id[str(row.get("id"))]
    ]
    hard_failures = []
    if input_remote != output_remote:
        hard_failures.append("remote_utterances_changed")
    if existing_changed:
        hard_failures.append("existing_utterances_changed")
    if any(str(row.get("outcome")) not in OUTCOMES for row in dispositions):
        hard_failures.append("invalid_outcome")
    if len(dispositions) != len(session_queue):
        hard_failures.append("not_all_rows_have_dispositions")
    for metric in (
        "remote_duplicate_in_me_seconds",
        "cross_role_overlap_gt2_seconds",
        "unrepaired_long_mic_crossings_count",
        "golden_phrase_fail_count",
    ):
        if safe_float(output_quality.get(metric)) > safe_float(quality.get(metric)) + 0.001:
            hard_failures.append(f"metric_regressed:{metric}")
    if safe_float(output_quality.get("local_only_island_recall"), 1.0) + 0.001 < safe_float(
        quality.get("local_only_island_recall"), 1.0
    ):
        hard_failures.append("metric_regressed:local_only_island_recall")
    hard_failures.extend(verify_frozen_session(session, record))
    gates = {"passed": not hard_failures, "hard_failures": hard_failures}
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
        order.write_markdown(output_paths["transcript"], output_utterances, transcribe_report.get("model"), transcribe_report.get("language"))
    output_dir = profile_dir(session)
    write_jsonl(output_dir / "residual_local_recall_dispositions.jsonl", dispositions)
    write_jsonl(output_dir / "residual_local_recall_applied.jsonl", closed_rows)
    write_jsonl(output_dir / "residual_local_recall_rejected.jsonl", rejected_rows)
    write_jsonl(output_dir / "residual_local_recall_review_queue.jsonl", rejected_rows)
    write_json(
        output_dir / "residual_local_recall_diff.json",
        {
            "schema": "murmurmark.residual_local_recall_diff/v1",
            "session_id": session.name,
            "input_profile": INPUT_PROFILE,
            "output_profile": PROFILE,
            "inserted_ids": [str(row.get("id")) for row in inserted],
            "existing_changed_ids": existing_changed,
            "remote_unchanged": input_remote == output_remote,
        },
    )
    report = {
        "schema": "murmurmark.residual_local_recall_profile_report/v1",
        "generator": {"name": "residual-local-recall", "version": SCRIPT_VERSION},
        "session_id": session.name,
        "input_profile": INPUT_PROFILE,
        "output_profile": PROFILE,
        "status": "ok" if gates["passed"] else "failed_open",
        "summary": summary,
        "gates": gates,
        "evidence_sha256": sha256_file(evidence_path),
    }
    if args.mode == "conservative" and gates["passed"]:
        report["output_fingerprint"] = output_fingerprint(output_paths)
    write_json(output_dir / "residual_local_recall_profile_report.json", report)
    print(f"profile: {session.name}: closed={summary['closed_items']} inserted={summary['inserted_me_items']}")
    return 0 if gates["passed"] else 2


def synthesize(session: Path) -> int:
    script = Path(__file__).with_name("synthesize-simple-extractive.py")
    environment = os.environ.copy()
    environment["MURMURMARK_RESIDUAL_LOCAL_RECALL_CANDIDATE"] = "1"
    return subprocess.run(
        [sys.executable, str(script), str(session), "--transcript-profile", PROFILE],
        env=environment,
        check=False,
    ).returncode


def collect_evidence_utterance_ids(value: Any) -> set[str]:
    result: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"evidence_utterance_ids", "context_utterance_ids", "utterance_ids"} and isinstance(child, list):
                result.update(str(item) for item in child if item)
            else:
                result.update(collect_evidence_utterance_ids(child))
    elif isinstance(value, list):
        for child in value:
            result.update(collect_evidence_utterance_ids(child))
    return result


def verify_synthesis(session: Path) -> tuple[dict[str, Any], list[str]]:
    failures: list[str] = []
    synthesis_dir = session / "derived/synthesis-simple/extractive"
    input_verdict = read_json(synthesis_dir / f"quality_verdict.{INPUT_PROFILE}.json") or {}
    output_verdict = read_json(synthesis_dir / f"quality_verdict.{PROFILE}.json") or {}
    evidence_notes = read_json(synthesis_dir / f"evidence_notes.{PROFILE}.json") or {}
    dialogue = read_json(profile_paths(session, PROFILE)["dialogue"]) or {}
    utterance_ids = {str(row.get("id")) for row in dialogue.get("utterances") or [] if row.get("id")}
    evidence_ids = collect_evidence_utterance_ids(evidence_notes)
    missing_ids = sorted(evidence_ids - utterance_ids)
    input_value = str(input_verdict.get("verdict") or "failed")
    output_value = str(output_verdict.get("verdict") or "failed")
    if output_verdict.get("selected_transcript_profile") != PROFILE:
        failures.append("synthesis_selected_wrong_profile")
    if VERDICT_RANK.get(output_value, -1) < VERDICT_RANK.get(input_value, -1):
        failures.append(f"verdict_regressed:{input_value}->{output_value}")
    if missing_ids:
        failures.append(f"notes_reference_missing_utterances:{','.join(missing_ids[:10])}")
    return (
        {
            "input_verdict": input_value,
            "output_verdict": output_value,
            "evidence_utterance_id_count": len(evidence_ids),
            "missing_evidence_utterance_ids": missing_ids,
            "passed": not failures,
        },
        failures,
    )


def corpus_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    gates = report.get("gates") or {}
    return "\n".join(
        [
            "# Residual Local Recall Closure v1",
            "",
            f"Decision: `{report.get('decision')}`",
            f"Closed: `{summary.get('closed_items')}/13` rows, `{summary.get('closed_seconds')}/48.073s`",
            f"Inserted: `{summary.get('inserted_me_items')}` Me utterances",
            f"Remaining: `{summary.get('remaining_items')}` rows",
            f"Gates: `{'passed' if gates.get('passed') else 'failed'}`",
            "",
            "## Evidence ceiling",
            "",
            str(report.get("evidence_ceiling") or "All safely resolvable rows were closed."),
            "",
        ]
    )


def evaluate_corpus(args: argparse.Namespace) -> int:
    sessions_root = args.sessions_root.expanduser().resolve()
    out_dir = report_dir(sessions_root, args.out_dir)
    baseline = read_json(out_dir / "residual_local_recall_baseline.json") or {}
    queue = read_jsonl(out_dir / "residual_local_recall_queue.jsonl")
    failures: list[str] = []
    if sha256_bytes(canonical_bytes(queue)) != ((baseline.get("queue") or {}).get("sha256")):
        failures.append("frozen_queue_changed")
    sessions = []
    all_dispositions: list[dict[str, Any]] = []
    for record in baseline.get("sessions") or []:
        session = sessions_root / str(record.get("session_id") or "")
        if args.apply:
            apply_args = argparse.Namespace(session=session, sessions_root=sessions_root, out_dir=out_dir, mode="conservative")
            if apply_session(apply_args) != 0:
                failures.append(f"{session.name}:apply_failed")
        report = read_json(profile_dir(session) / "residual_local_recall_profile_report.json") or {}
        dispositions = read_jsonl(profile_dir(session) / "residual_local_recall_dispositions.jsonl")
        all_dispositions.extend(dispositions)
        if not (report.get("gates") or {}).get("passed"):
            failures.append(f"{session.name}:profile_gates_failed")
        sessions.append(
            {
                "session_id": session.name,
                "summary": report.get("summary"),
                "gates_passed": (report.get("gates") or {}).get("passed") is True,
                "output_fingerprint": report.get("output_fingerprint"),
            }
        )
    closed = [row for row in all_dispositions if row.get("closed") is True]
    closed_ids = {str(row.get("residual_queue_id") or "") for row in closed}
    closed_seconds = round(sum(duration(row) for row in queue if str(row.get("residual_queue_id") or "") in closed_ids), 3)
    inserted = sum(1 for row in closed if row.get("action") == "insert_me")
    useful = len(closed) >= MIN_CLOSED_ITEMS and closed_seconds >= MIN_CLOSED_SECONDS
    if len(all_dispositions) != 13:
        failures.append(f"expected_13_dispositions_got_{len(all_dispositions)}")
    audio_report_sha = sha256_file(sessions_root / "_reports/residual-audio-arbitration-v1/residual_audio_corpus_report.json")
    if audio_report_sha != (((baseline.get("excluded_queues") or {}).get("audio_review") or {}).get("report_sha256")):
        failures.append("audio_review_dispositions_changed")
    frozen_chronology = ((baseline.get("excluded_queues") or {}).get("transcript_order") or {})
    chronology_rows = []
    for session_id in frozen_chronology.get("session_ids") or []:
        session = sessions_root / str(session_id)
        chronology_rows.extend(
            row
            for row in read_jsonl(input_profile_dir(session) / "residual_me_review_queue.jsonl")
            if str(row.get("source") or "") == "transcript_order"
        )
    if len(chronology_rows) != 14 or sha256_bytes(canonical_bytes(chronology_rows)) != frozen_chronology.get("sha256"):
        failures.append("transcript_order_queue_changed")
    if not useful:
        failures.append("minimum_usefulness_threshold_not_met")
    candidate_eligible = not failures
    if candidate_eligible and not args.synthesize:
        failures.append("synthesis_not_run")
    decision = "CANDIDATE_RESIDUAL_LOCAL_RECALL_V1" if candidate_eligible and args.synthesize else "DO_NOT_PROMOTE"
    summary = {
        "frozen_queue_items": len(queue),
        "frozen_queue_seconds": round(sum(duration(row) for row in queue), 3),
        "closed_items": len(closed),
        "closed_seconds": closed_seconds,
        "remaining_items": len(queue) - len(closed),
        "remaining_seconds": round(sum(duration(row) for row in queue) - closed_seconds, 3),
        "inserted_me_items": inserted,
        "by_outcome": dict(sorted(Counter(str(row.get("outcome") or "unknown") for row in all_dispositions).items())),
        "preserved_audio_review_items": 66,
        "preserved_transcript_order_items": 14,
    }
    gates = {
        "passed": not failures,
        "hard_failures": failures,
        "minimum_closed_items": MIN_CLOSED_ITEMS,
        "minimum_closed_seconds": MIN_CLOSED_SECONDS,
        "usefulness_threshold_passed": useful,
    }
    evidence_ceiling = (
        "Rows left open lack independent word-level local evidence or contain unresolved mixed speech."
        if summary["remaining_items"]
        else "No residual local-recall rows remain."
    )
    report = {
        "schema": "murmurmark.residual_local_recall_corpus_report/v1",
        "generator": {"name": "residual-local-recall", "version": SCRIPT_VERSION},
        "input_profile": INPUT_PROFILE,
        "output_profile": PROFILE,
        "decision": decision,
        "baseline_sha256": sha256_file(out_dir / "residual_local_recall_baseline.json"),
        "queue_sha256": sha256_file(out_dir / "residual_local_recall_queue.jsonl"),
        "summary": summary,
        "sessions": sessions,
        "promoted_sessions": [row["session_id"] for row in sessions] if decision.startswith("PROMOTE") else [],
        "gates": gates,
        "evidence_ceiling": evidence_ceiling,
        "decision_fingerprint": "",
    }
    report["decision_fingerprint"] = row_fingerprint({key: value for key, value in report.items() if key != "decision_fingerprint"})
    decision_payload = {
        "schema": "murmurmark.residual_local_recall_decision/v1",
        "decision": decision,
        "profile": PROFILE,
        "selected_profile": PROFILE if decision.startswith("PROMOTE") else INPUT_PROFILE,
        "decision_fingerprint": report["decision_fingerprint"],
        "reason": "all_promotion_gates_passed" if decision.startswith("PROMOTE") else failures,
    }
    write_json(out_dir / "residual_local_recall_corpus_report.json", report)
    (out_dir / "residual_local_recall_corpus_report.md").write_text(corpus_markdown(report), encoding="utf-8")
    write_json(out_dir / "residual_local_recall_decision.json", decision_payload)
    if args.synthesize and candidate_eligible:
        synthesis_failures = []
        for row in sessions:
            session = sessions_root / str(row.get("session_id") or "")
            if synthesize(session) != 0:
                synthesis_failures.append(f"{session.name}:synthesis_failed")
                row["synthesis"] = {"passed": False, "reason": "synthesis_failed"}
                continue
            synthesis_result, session_synthesis_failures = verify_synthesis(session)
            row["synthesis"] = synthesis_result
            synthesis_failures.extend(f"{session.name}:{value}" for value in session_synthesis_failures)
        if synthesis_failures:
            report["decision"] = "DO_NOT_PROMOTE"
            report["promoted_sessions"] = []
            report["gates"]["passed"] = False
            report["gates"]["hard_failures"].extend(synthesis_failures)
            report["decision_fingerprint"] = ""
            report["decision_fingerprint"] = row_fingerprint(
                {key: value for key, value in report.items() if key != "decision_fingerprint"}
            )
            decision_payload.update(
                {
                    "decision": "DO_NOT_PROMOTE",
                    "selected_profile": INPUT_PROFILE,
                    "decision_fingerprint": report["decision_fingerprint"],
                    "reason": synthesis_failures,
                }
            )
            write_json(out_dir / "residual_local_recall_corpus_report.json", report)
            (out_dir / "residual_local_recall_corpus_report.md").write_text(corpus_markdown(report), encoding="utf-8")
            write_json(out_dir / "residual_local_recall_decision.json", decision_payload)
            decision = "DO_NOT_PROMOTE"
        else:
            decision = "PROMOTE_RESIDUAL_LOCAL_RECALL_V1"
            report["decision"] = decision
            report["promoted_sessions"] = [row["session_id"] for row in sessions]
            report["synthesis_gates"] = {
                "passed": True,
                "sessions_checked": len(sessions),
                "verdict_regressions": 0,
                "missing_evidence_utterance_ids": 0,
            }
            report["decision_fingerprint"] = ""
            report["decision_fingerprint"] = row_fingerprint(
                {key: value for key, value in report.items() if key != "decision_fingerprint"}
            )
            decision_payload.update(
                {
                    "decision": decision,
                    "selected_profile": PROFILE,
                    "decision_fingerprint": report["decision_fingerprint"],
                    "reason": "all_promotion_gates_passed",
                }
            )
            write_json(out_dir / "residual_local_recall_corpus_report.json", report)
            (out_dir / "residual_local_recall_corpus_report.md").write_text(corpus_markdown(report), encoding="utf-8")
            write_json(out_dir / "residual_local_recall_decision.json", decision_payload)
    print(f"decision: {decision}")
    print(f"closed: {len(closed)} / {closed_seconds}s")
    return 0


def run_all(args: argparse.Namespace) -> int:
    freeze_args = argparse.Namespace(sessions_root=args.sessions_root, out_dir=args.out_dir, force=args.force_freeze)
    if freeze_corpus(freeze_args) != 0:
        return 2
    evidence_args = argparse.Namespace(sessions_root=args.sessions_root, out_dir=args.out_dir, sessions=[])
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
