#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np


SCHEMA = "murmurmark.persistent_target_me_profile_lab/v1"
SCRIPT_VERSION = "0.1.0"
DEFAULT_REPORT = Path("sessions/_reports/live-pipeline/persistent_target_me_profile_lab.json")
LOCAL_LABELS = {"me_dominant", "mixed"}
POLICIES = ("confirmed_remote_guard", "confirmed", "possible")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate a persistent Target-Me voice profile built from other processed sessions "
            "against live suppressed mic segments. Batch labels are used only for offline scoring."
        )
    )
    parser.add_argument("sessions", nargs="*", type=Path)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument(
        "--scope",
        choices=("capture-safe-candidate", "real", "all-live"),
        default="capture-safe-candidate",
    )
    parser.add_argument(
        "--enrollment-source",
        choices=("before-target", "all-other"),
        default="before-target",
        help="before-target is causal for historical replay; all-other is a non-causal ceiling.",
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_REPORT)
    parser.add_argument(
        "--method",
        default="resemblyzer_dvector",
        choices=["auto", "mfcc_voiceprint", "mfcc_contrastive", "resemblyzer_dvector", "wavlm_xvector"],
    )
    parser.add_argument("--wavlm-model", type=Path, default=None)
    parser.add_argument("--padding-sec", type=float, default=0.20)
    parser.add_argument("--min-enrollment-sec", type=float, default=1.2)
    parser.add_argument("--max-enrollment-sec", type=float, default=14.0)
    parser.add_argument("--min-enrollment-local-ratio", type=float, default=0.65)
    parser.add_argument("--max-enrollment-remote-active-ratio", type=float, default=0.20)
    parser.add_argument("--max-enrollment-segments", type=int, default=80)
    parser.add_argument("--max-enrollment-total-sec", type=float, default=240.0)
    parser.add_argument("--max-negative-enrollment-segments", type=int, default=80)
    parser.add_argument("--min-enrollment-count", type=int, default=3)
    parser.add_argument("--max-examples", type=int, default=8)
    parser.add_argument("--write-clips", action=argparse.BooleanOptionalAction, default=True)
    return parser.parse_args()


def load_module(root: Path, name: str, relative_path: str) -> Any:
    module_path = root / relative_path
    spec = importlib.util.spec_from_file_location(name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_markdown(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def rel(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(number) or math.isinf(number):
        return default
    return number


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def round_float(value: float | None, digits: int = 6) -> float | None:
    if value is None:
        return None
    return round(value, digits)


def resolve_session(root: Path, value: Path) -> Path:
    if value.is_absolute():
        return value
    if value.parts and value.parts[0] == "sessions":
        return root / value
    return root / "sessions" / value


def report_session_ids(root: Path, scope: str) -> set[str]:
    report = read_json(root / "sessions/_reports/live-pipeline/live_corpus_gates_report.json")
    if not isinstance(report, dict):
        return set()
    if scope == "capture-safe-candidate":
        candidate_scope = report.get("capture_safe_candidate_scope")
        ids = candidate_scope.get("session_ids") if isinstance(candidate_scope, dict) else None
        return {str(item) for item in ids or []}
    sessions = report.get("sessions")
    if not isinstance(sessions, list):
        return set()
    result: set[str] = set()
    for row in sessions:
        if not isinstance(row, dict):
            continue
        item_id = str(row.get("session") or "")
        if not item_id:
            continue
        if scope == "real" and row.get("evidence_scope") != "real_meeting":
            continue
        result.add(item_id)
    return result


def discover_targets(root: Path, scope: str, explicit: list[Path]) -> list[Path]:
    if explicit:
        return [
            session
            for session in (resolve_session(root, value) for value in explicit)
            if (session / "derived/live/chunks.jsonl").exists()
        ]
    known = report_session_ids(root, scope)
    targets: list[Path] = []
    for chunks in sorted((root / "sessions").glob("*/derived/live/chunks.jsonl")):
        session = chunks.parents[2]
        if session.name.startswith("_"):
            continue
        if known and session.name not in known:
            continue
        targets.append(session)
    return targets


def discover_enrollment_sources(root: Path) -> list[Path]:
    sources: list[Path] = []
    for session in sorted((root / "sessions").iterdir()):
        if not session.is_dir() or session.name.startswith("_"):
            continue
        if not (session / "derived/preprocess/audio/mic_clean_local_fir.wav").exists():
            continue
        resolved = session / "derived/transcript-simple/whisper-cpp/resolved"
        if not any(resolved.glob("clean_dialogue*.json")):
            continue
        sources.append(session)
    return sources


def source_audio(session: Path) -> dict[str, Path]:
    return {
        "mic_clean": session / "derived/preprocess/audio/mic_clean_local_fir.wav",
        "mic_role_masked": session / "derived/preprocess/audio/mic_role_masked_for_asr.wav",
        "mic_raw": session / "audio/mic/000001.caf",
        "remote": session / "audio/remote/000001.caf",
    }


def best_source(session: Path, role: str) -> tuple[str, Path]:
    sources = source_audio(session)
    if role == "remote":
        return "remote", sources["remote"]
    for name in ("mic_clean", "mic_role_masked", "mic_raw"):
        if sources[name].exists():
            return name, sources[name]
    return "mic_raw", sources["mic_raw"]


def enrollment_candidate_rows(
    *,
    session: Path,
    tm: Any,
    args: argparse.Namespace,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    profile = tm.resolve_profile(session, "auto")
    dialogue = tm.read_json(tm.clean_dialogue_path(session, profile))
    utterances = dialogue.get("utterances") if isinstance(dialogue, dict) else []
    state_rows = tm.load_speaker_state(session)
    positives: list[dict[str, Any]] = []
    negatives: list[dict[str, Any]] = []
    if not isinstance(utterances, list):
        return positives, negatives
    for row in utterances:
        if not isinstance(row, dict):
            continue
        role = tm.role_of(row)
        if role not in {"me", "remote"}:
            continue
        quality = row.get("quality") if isinstance(row.get("quality"), dict) else {}
        if quality.get("needs_review") is True:
            continue
        start = safe_float(row.get("start"))
        end = safe_float(row.get("end"), start)
        duration = max(0.0, end - start)
        if duration < args.min_enrollment_sec or duration > args.max_enrollment_sec:
            continue
        if tm.text_token_count(row.get("text")) < 2:
            continue
        state = tm.interval_state_features(state_rows, start, end)
        if role == "me":
            if state["local_only_ratio"] < args.min_enrollment_local_ratio:
                continue
            if state["remote_active_ratio"] > args.max_enrollment_remote_active_ratio:
                continue
            score = (
                state["local_only_ratio"] * 100
                - state["remote_active_ratio"] * 80
                + min(12.0, duration) * 2
                + min(12, tm.text_token_count(row.get("text")))
            )
            positives.append(
                {
                    "session": session.name,
                    "utterance_id": str(row.get("id") or ""),
                    "role": "me",
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "duration_sec": round(duration, 3),
                    "text": str(row.get("text") or ""),
                    "state": state,
                    "score": round(float(score), 3),
                }
            )
        else:
            score = min(12.0, duration) * 2 + min(12, tm.text_token_count(row.get("text")))
            negatives.append(
                {
                    "session": session.name,
                    "utterance_id": str(row.get("id") or ""),
                    "role": "remote",
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "duration_sec": round(duration, 3),
                    "text": str(row.get("text") or ""),
                    "state": state,
                    "score": round(float(score), 3),
                }
            )
    return positives, negatives


def extract_embedding(
    *,
    source_session: Path,
    row: dict[str, Any],
    backend: Any,
    tm: Any,
    args: argparse.Namespace,
    out_dir: Path,
    index: int,
) -> tuple[np.ndarray | None, dict[str, Any]]:
    source_name, source = best_source(source_session, str(row.get("role") or "me"))
    clip = out_dir / "clips/enrollment" / source_session.name / f"{row.get('role')}_{index:04d}_{row.get('utterance_id')}.wav"
    start = max(0.0, safe_float(row.get("start")) - args.padding_sec)
    end = safe_float(row.get("end"), safe_float(row.get("start"))) + args.padding_sec
    if not tm.extract_wav(source, clip, start, max(0.05, end - start)):
        return None, {"error": "extract_failed", "source": str(source), "source_audio": source_name}
    embedding, info = tm.embedding_for_clip(clip, backend)
    info["source_audio"] = source_name
    return embedding, info


def build_embedding_pool(
    *,
    root: Path,
    sources: list[Path],
    tm: Any,
    args: argparse.Namespace,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    positive_rows: list[dict[str, Any]] = []
    negative_rows: list[dict[str, Any]] = []
    for source in sources:
        positives, negatives = enrollment_candidate_rows(session=source, tm=tm, args=args)
        for bucket_name, rows, target in (
            ("positive", positives, positive_rows),
            ("negative", negatives, negative_rows),
        ):
            rows.sort(key=lambda item: (-safe_float(item.get("score")), safe_float(item.get("start"))))
            limit = args.max_enrollment_segments if bucket_name == "positive" else args.max_negative_enrollment_segments
            for row in rows[:limit]:
                row = dict(row)
                row["session_path"] = rel(source, root)
                target.append(row)
    return positive_rows, negative_rows


def embed_selected_pool(
    *,
    root: Path,
    rows: list[dict[str, Any]],
    backend: Any,
    tm: Any,
    args: argparse.Namespace,
    out_dir: Path,
    cache: dict[tuple[str, str, str, float, float], tuple[np.ndarray | None, dict[str, Any]]],
) -> list[dict[str, Any]]:
    embedded: list[dict[str, Any]] = []
    for row in rows:
        source_path = str(row.get("session_path") or "")
        source_session = root / source_path
        key = (
            source_path,
            str(row.get("role") or ""),
            str(row.get("utterance_id") or ""),
            safe_float(row.get("start")),
            safe_float(row.get("end")),
        )
        if key not in cache:
            cache[key] = extract_embedding(
                source_session=source_session,
                row=row,
                backend=backend,
                tm=tm,
                args=args,
                out_dir=out_dir,
                index=len(cache) + 1,
            )
        embedding, info = cache[key]
        item = dict(row)
        item["embedding_info"] = info
        item["accepted"] = embedding is not None
        if embedding is not None:
            item["embedding"] = embedding
            embedded.append(item)
    return embedded


def eligible_pool(
    *,
    target: Path,
    rows: list[dict[str, Any]],
    source_mode: str,
    max_count: int,
    max_seconds: float,
) -> list[dict[str, Any]]:
    candidates = []
    for row in rows:
        source_id = str(row.get("session") or "")
        if source_id == target.name:
            continue
        if source_mode == "before-target" and source_id >= target.name:
            continue
        candidates.append(row)
    candidates.sort(key=lambda item: (-safe_float(item.get("score")), str(item.get("session")), safe_float(item.get("start"))))
    selected: list[dict[str, Any]] = []
    total = 0.0
    for row in candidates:
        if len(selected) >= max_count or total >= max_seconds:
            break
        selected.append(row)
        total += safe_float(row.get("duration_sec"))
    return selected


def build_model(tm: Any, method: str, positives: list[dict[str, Any]], negatives: list[dict[str, Any]], min_count: int) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    positive_embeddings = [row["embedding"] for row in positives if isinstance(row.get("embedding"), np.ndarray)]
    negative_embeddings = [row["embedding"] for row in negatives if isinstance(row.get("embedding"), np.ndarray)]
    if len(positive_embeddings) < min_count:
        return None, {
            "status": "insufficient_positive_enrollment",
            "positive_count": len(positive_embeddings),
            "negative_count": len(negative_embeddings),
        }
    scoring = "contrastive" if method == "mfcc_contrastive_v0" else "cosine"
    target_model = {
        "positive_centroid": tm.make_centroid(positive_embeddings),
        "negative_centroid": tm.make_centroid(negative_embeddings),
        "scoring": scoring,
    }
    if target_model["positive_centroid"] is None:
        return None, {
            "status": "empty_positive_centroid",
            "positive_count": len(positive_embeddings),
            "negative_count": len(negative_embeddings),
        }
    if scoring == "contrastive" and len(negative_embeddings) < min_count:
        return None, {
            "status": "insufficient_negative_enrollment",
            "positive_count": len(positive_embeddings),
            "negative_count": len(negative_embeddings),
        }
    positive_similarities = [tm.model_score(vector, target_model)["target_similarity"] for vector in positive_embeddings]
    negative_similarities = [tm.model_score(vector, target_model)["target_similarity"] for vector in negative_embeddings]
    calibration: dict[str, Any] = {
        "status": "ready",
        "positive_count": len(positive_embeddings),
        "negative_count": len(negative_embeddings),
        "positive_total_sec": round(sum(safe_float(row.get("duration_sec")) for row in positives), 3),
        "negative_total_sec": round(sum(safe_float(row.get("duration_sec")) for row in negatives), 3),
        "similarity_to_centroid": {
            "p10": round(tm.percentile(positive_similarities, 10), 6),
            "p50": round(tm.percentile(positive_similarities, 50), 6),
            "min": round(min(positive_similarities), 6),
            "max": round(max(positive_similarities), 6),
        },
    }
    if negative_similarities:
        positive_floor = tm.percentile(positive_similarities, 10)
        negative_ceiling = tm.percentile(negative_similarities, 90)
        negative_mid = tm.percentile(negative_similarities, 75)
        calibration["negative_similarity_to_target"] = {
            "p50": round(tm.percentile(negative_similarities, 50), 6),
            "p75": round(negative_mid, 6),
            "p90": round(negative_ceiling, 6),
        }
        calibration["target_threshold"] = round((positive_floor + negative_ceiling) / 2.0, 6)
        calibration["weak_target_threshold"] = round((positive_floor + negative_mid) / 2.0, 6)
    else:
        calibration["target_threshold"] = round(max(0.45, tm.percentile(positive_similarities, 10) - 0.12), 6)
        calibration["weak_target_threshold"] = round(max(0.38, tm.percentile(positive_similarities, 10) - 0.20), 6)
    return target_model, calibration


def classify_segment(
    *,
    session: Path,
    segment: dict[str, Any],
    target_model: dict[str, Any] | None,
    calibration: dict[str, Any],
    live_lab: Any,
    backend: Any,
    tm: Any,
    args: argparse.Namespace,
    out_dir: Path,
    state_rows: list[dict[str, Any]],
    index: int,
) -> dict[str, Any]:
    if target_model is None:
        return {
            "status": "not_ready",
            "classification": {
                "label": "target_me_ambiguous",
                "confidence": 0.0,
                "reason": calibration.get("status") or "enrollment_not_ready",
                "scores": {},
            },
            "policy_candidates": [],
        }
    result = live_lab.classify_segment(
        session=session,
        segment=segment,
        target_model=target_model,
        calibration=calibration,
        backend=backend,
        tm=tm,
        args=args,
        out_dir=out_dir,
        state_rows=state_rows,
        mode="persistent_profile",
        index=index,
    )
    result["policy_candidates"] = live_lab.policy_labels(result.get("classification") or {})
    return result


def local_label(row: dict[str, Any]) -> bool:
    return row.get("batch_role_label") in LOCAL_LABELS


def summarize_policy(rows: list[dict[str, Any]], policy: str) -> dict[str, Any]:
    selected = [row for row in rows if policy in (row.get("persistent_target_me") or {}).get("policy_candidates", [])]
    selected_seconds = sum(safe_float(row.get("duration_sec")) for row in selected)
    local_seconds = sum(safe_float(row.get("duration_sec")) for row in selected if local_label(row))
    remote_seconds = selected_seconds - local_seconds
    total_local = sum(safe_float(row.get("duration_sec")) for row in rows if local_label(row))
    return {
        "selected_count": len(selected),
        "selected_seconds": round(selected_seconds, 3),
        "local_seconds": round(local_seconds, 3),
        "remote_risk_seconds": round(remote_seconds, 3),
        "precision_proxy": round_float(local_seconds / selected_seconds if selected_seconds else None),
        "local_recall_proxy": round_float(local_seconds / total_local if total_local else None),
    }


def example_payload(row: dict[str, Any]) -> dict[str, Any]:
    persistent = row.get("persistent_target_me") if isinstance(row.get("persistent_target_me"), dict) else {}
    classification = persistent.get("classification") if isinstance(persistent.get("classification"), dict) else {}
    return {
        "session": row.get("session"),
        "chunk_index": row.get("chunk_index"),
        "start": row.get("start"),
        "end": row.get("end"),
        "duration_sec": row.get("duration_sec"),
        "batch_role_label": row.get("batch_role_label"),
        "text": row.get("text"),
        "label": classification.get("label"),
        "confidence": classification.get("confidence"),
        "policy_candidates": persistent.get("policy_candidates"),
        "scores": classification.get("scores"),
    }


def evaluate_target(
    *,
    target: Path,
    root: Path,
    compare: Any,
    tm: Any,
    live_lab: Any,
    backend: Any,
    backend_status: dict[str, Any],
    positives: list[dict[str, Any]],
    negatives: list[dict[str, Any]],
    args: argparse.Namespace,
    out_root: Path,
    embedding_cache: dict[tuple[str, str, str, float, float], tuple[np.ndarray | None, dict[str, Any]]],
) -> dict[str, Any]:
    out_dir = out_root / "targets" / target.name
    out_dir.mkdir(parents=True, exist_ok=True)
    positive_candidates = eligible_pool(
        target=target,
        rows=positives,
        source_mode=args.enrollment_source,
        max_count=args.max_enrollment_segments,
        max_seconds=args.max_enrollment_total_sec,
    )
    negative_candidates = eligible_pool(
        target=target,
        rows=negatives,
        source_mode=args.enrollment_source,
        max_count=args.max_negative_enrollment_segments,
        max_seconds=args.max_enrollment_total_sec,
    )
    positive_pool = embed_selected_pool(
        root=root,
        rows=positive_candidates,
        backend=backend,
        tm=tm,
        args=args,
        out_dir=out_root,
        cache=embedding_cache,
    )
    negative_pool = embed_selected_pool(
        root=root,
        rows=negative_candidates,
        backend=backend,
        tm=tm,
        args=args,
        out_dir=out_root,
        cache=embedding_cache,
    )
    target_model, calibration = build_model(
        tm,
        str(backend_status.get("selected") or backend.method),
        positive_pool,
        negative_pool,
        args.min_enrollment_count,
    )
    chunks = compare.read_jsonl(target / "derived/live/chunks.jsonl")
    profile = compare.selected_profile(target)
    clean_dialogue = compare.selected_clean_dialogue_path(target, profile)
    batch_utterances = compare.read_utterances(clean_dialogue)
    suppressed = compare.read_suppressed_mic_asr_segment_audit(target, chunks, batch_utterances).get("segments") or []
    suppressed = [row for row in suppressed if isinstance(row, dict)]
    state_rows = tm.load_speaker_state(target)
    rows: list[dict[str, Any]] = []
    for index, segment in enumerate(suppressed, start=1):
        start = safe_float(segment.get("start"))
        end = safe_float(segment.get("end"), start)
        row = {
            "session": target.name,
            "chunk_index": safe_int(segment.get("chunk_index")),
            "start": round(start, 3),
            "end": round(end, 3),
            "duration_sec": round(max(0.0, end - start), 3),
            "text": segment.get("text") or "",
            "batch_role_label": segment.get("batch_role_label"),
            "segment_features": {
                "token_count": segment.get("token_count"),
                "unique_token_count": segment.get("segment_gate_unique_token_count"),
                "audio_mic_clean_rms_db": segment.get("audio_mic_clean_rms_db"),
                "audio_remote_rms_db": segment.get("audio_remote_rms_db"),
                "audio_mic_remote_zero_lag_abs_corr": segment.get("audio_mic_remote_zero_lag_abs_corr"),
            },
            "persistent_target_me": classify_segment(
                session=target,
                segment=segment,
                target_model=target_model,
                calibration=calibration,
                live_lab=live_lab,
                backend=backend,
                tm=tm,
                args=args,
                out_dir=out_dir,
                state_rows=state_rows,
                index=index,
            ),
        }
        rows.append(row)
    write_json(out_dir / "persistent_target_me_profile_lab_rows.json", rows)
    policies = {policy: summarize_policy(rows, policy) for policy in POLICIES}
    local_seconds = sum(safe_float(row.get("duration_sec")) for row in rows if local_label(row))
    remote_seconds = sum(safe_float(row.get("duration_sec")) for row in rows if not local_label(row))
    summary = {
        "session": target.name,
        "path": rel(target, root),
        "selected_profile": profile,
        "clean_dialogue": rel(clean_dialogue, root) if clean_dialogue else None,
        "suppressed_mic_segment_count": len(rows),
        "suppressed_mic_segment_seconds": round(local_seconds + remote_seconds, 3),
        "local_seconds": round(local_seconds, 3),
        "remote_risk_seconds": round(remote_seconds, 3),
        "positive_enrollment_count": len(positive_pool),
        "positive_enrollment_seconds": round(sum(safe_float(row.get("duration_sec")) for row in positive_pool), 3),
        "negative_enrollment_count": len(negative_pool),
        "negative_enrollment_seconds": round(sum(safe_float(row.get("duration_sec")) for row in negative_pool), 3),
        "enrollment_status": calibration.get("status"),
        "backend": backend_status,
        "policies": policies,
        "examples": {
            "remote_risk_selected": [
                example_payload(row)
                for row in rows
                if not local_label(row) and (row.get("persistent_target_me") or {}).get("policy_candidates")
            ][: args.max_examples],
            "local_confirmed_remote_guard": [
                example_payload(row)
                for row in rows
                if local_label(row)
                and "confirmed_remote_guard" in ((row.get("persistent_target_me") or {}).get("policy_candidates") or [])
            ][: args.max_examples],
        },
    }
    write_json(out_dir / "persistent_target_me_profile_lab_summary.json", summary)
    return summary


def aggregate_policy(summaries: list[dict[str, Any]], policy: str) -> dict[str, Any]:
    selected_count = 0
    selected_seconds = 0.0
    local_seconds = 0.0
    remote_seconds = 0.0
    total_local = sum(safe_float(summary.get("local_seconds")) for summary in summaries)
    for summary in summaries:
        row = summary.get("policies", {}).get(policy, {})
        selected_count += safe_int(row.get("selected_count"))
        selected_seconds += safe_float(row.get("selected_seconds"))
        local_seconds += safe_float(row.get("local_seconds"))
        remote_seconds += safe_float(row.get("remote_risk_seconds"))
    return {
        "selected_count": selected_count,
        "selected_seconds": round(selected_seconds, 3),
        "local_seconds": round(local_seconds, 3),
        "remote_risk_seconds": round(remote_seconds, 3),
        "precision_proxy": round_float(local_seconds / selected_seconds if selected_seconds else None),
        "local_recall_proxy": round_float(local_seconds / total_local if total_local else None),
    }


def conclusion(report: dict[str, Any]) -> dict[str, Any]:
    guard = report["policies"]["confirmed_remote_guard"]
    possible = report["policies"]["possible"]
    total_local = safe_float(report.get("local_seconds"))
    guard_local = safe_float(guard.get("local_seconds"))
    guard_remote = safe_float(guard.get("remote_risk_seconds"))
    possible_remote = safe_float(possible.get("remote_risk_seconds"))
    if total_local <= 0:
        status = "no_local_labels"
        next_step = "collect comparable live sessions with suppressed mic local labels"
    elif guard_remote > 0:
        status = "persistent_profile_not_safe"
        next_step = "tighten remote-forbidden gates before considering a persistent profile"
    elif guard_local / total_local >= 0.30:
        status = "promising_persistent_profile"
        next_step = "materialize persistent Target-Me as a shadow profile and run parity gates"
    elif possible_remote > 0:
        status = "persistent_profile_safe_but_low_recall"
        next_step = "combine persistent profile with stricter remote-forbidden evidence or better calibration"
    else:
        status = "limited_persistent_profile_recovery"
        next_step = "treat persistent profile as supporting evidence, not the main rescue path"
    return {
        "status": status,
        "total_local_seconds": round(total_local, 3),
        "confirmed_remote_guard_local_seconds": round(guard_local, 3),
        "confirmed_remote_guard_remote_risk_seconds": round(guard_remote, 3),
        "possible_remote_risk_seconds": round(possible_remote, 3),
        "recommended_next": next_step,
    }


def aggregate_report(summaries: list[dict[str, Any]], args: argparse.Namespace, backend_status: dict[str, Any]) -> dict[str, Any]:
    local_seconds = sum(safe_float(summary.get("local_seconds")) for summary in summaries)
    remote_seconds = sum(safe_float(summary.get("remote_risk_seconds")) for summary in summaries)
    payload = {
        "schema": SCHEMA,
        "generator": {
            "script": "scripts/report-persistent-target-me-profile-lab.py",
            "version": SCRIPT_VERSION,
        },
        "created_at": now_iso(),
        "scope": args.scope if not args.sessions else "explicit",
        "config": {
            "method": args.method,
            "enrollment_source": args.enrollment_source,
            "max_enrollment_segments": args.max_enrollment_segments,
            "max_enrollment_total_sec": args.max_enrollment_total_sec,
        },
        "backend": backend_status,
        "session_count": len(summaries),
        "suppressed_mic_segment_count": sum(safe_int(summary.get("suppressed_mic_segment_count")) for summary in summaries),
        "suppressed_mic_segment_seconds": round(local_seconds + remote_seconds, 3),
        "local_seconds": round(local_seconds, 3),
        "remote_risk_seconds": round(remote_seconds, 3),
        "policies": {policy: aggregate_policy(summaries, policy) for policy in POLICIES},
        "session_summaries": summaries,
    }
    payload["conclusion"] = conclusion(payload)
    return payload


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# Persistent Target-Me Profile Lab",
        "",
        f"Generated: `{report['created_at']}`",
        "",
        "## Scope",
        "",
        f"- scope: `{report['scope']}`",
        f"- enrollment source: `{report['config']['enrollment_source']}`",
        f"- sessions: `{report['session_count']}`",
        f"- suppressed mic ASR segments: `{report['suppressed_mic_segment_count']}` / `{report['suppressed_mic_segment_seconds']}` sec",
        f"- local/mixed seconds by batch labels: `{report['local_seconds']}`",
        f"- remote-risk seconds by batch labels: `{report['remote_risk_seconds']}`",
        "",
        "## Conclusion",
        "",
        f"- status: `{report['conclusion']['status']}`",
        f"- confirmed remote-guard local seconds: `{report['conclusion']['confirmed_remote_guard_local_seconds']}`",
        f"- confirmed remote-guard remote-risk seconds: `{report['conclusion']['confirmed_remote_guard_remote_risk_seconds']}`",
        f"- recommended next: {report['conclusion']['recommended_next']}",
        "",
        "## Policies",
        "",
        "| Policy | Selected sec | Local sec | Remote-risk sec | Precision | Recall |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for policy, row in report["policies"].items():
        lines.append(
            f"| `{policy}` | {row['selected_seconds']} | {row['local_seconds']} "
            f"| {row['remote_risk_seconds']} | {row['precision_proxy']} | {row['local_recall_proxy']} |"
        )
    lines += [
        "",
        "## Sessions",
        "",
        "| Session | Local sec | Enrollment | Guard local sec | Guard remote-risk sec |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for summary in report["session_summaries"]:
        guard = summary["policies"]["confirmed_remote_guard"]
        lines.append(
            f"| `{summary['session']}` | {summary['local_seconds']} "
            f"| {summary['positive_enrollment_count']} / {summary['positive_enrollment_seconds']} "
            f"| {guard['local_seconds']} | {guard['remote_risk_seconds']} |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    compare = load_module(root, "murmurmark_compare_live_batch", "scripts/compare-live-batch.py")
    tm = load_module(root, "murmurmark_audit_target_me", "scripts/audit-target-me.py")
    live_lab = load_module(root, "murmurmark_live_target_me_enrollment_lab", "scripts/report-live-target-me-enrollment-lab.py")
    backend, backend_status = tm.resolve_embedding_backend(args)
    targets = discover_targets(root, args.scope, args.sessions)
    out = args.out if args.out.is_absolute() else root / args.out
    out_root = out.with_suffix("")
    sources = discover_enrollment_sources(root)
    positives, negatives = build_embedding_pool(
        root=root,
        sources=sources,
        tm=tm,
        args=args,
    )
    embedding_cache: dict[tuple[str, str, str, float, float], tuple[np.ndarray | None, dict[str, Any]]] = {}
    summaries = [
        evaluate_target(
            target=target,
            root=root,
            compare=compare,
            tm=tm,
            live_lab=live_lab,
            backend=backend,
            backend_status=backend_status,
            positives=positives,
            negatives=negatives,
            args=args,
            out_root=out_root,
            embedding_cache=embedding_cache,
        )
        for target in targets
    ]
    report = aggregate_report(summaries, args, backend_status)
    report["enrollment_pool"] = {
        "source_session_count": len(sources),
        "positive_count": len(positives),
        "positive_seconds": round(sum(safe_float(row.get("duration_sec")) for row in positives), 3),
        "negative_count": len(negatives),
        "negative_seconds": round(sum(safe_float(row.get("duration_sec")) for row in negatives), 3),
        "embedded_clip_count": len(embedding_cache),
    }
    write_json(out, report)
    write_markdown(out.with_suffix(".md"), markdown_report(report))
    print(f"written: {rel(out, root)}")
    print(f"written: {rel(out.with_suffix('.md'), root)}")
    print(
        "summary: status={status} sessions={sessions} guard_local={local}s guard_remote_risk={remote}s".format(
            status=report["conclusion"]["status"],
            sessions=report["session_count"],
            local=report["conclusion"]["confirmed_remote_guard_local_seconds"],
            remote=report["conclusion"]["confirmed_remote_guard_remote_risk_seconds"],
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
