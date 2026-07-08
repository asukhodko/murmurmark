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


SCHEMA = "murmurmark.live_target_me_enrollment_lab/v1"
SCRIPT_VERSION = "0.1.0"
LOCAL_LABELS = {"me_dominant", "mixed"}
REMOTE_RISK_LABELS = {"remote_dominant", "none"}
DEFAULT_REPORT = Path("sessions/_reports/live-pipeline/live_target_me_enrollment_lab.json")
POLICIES = ("confirmed_remote_guard", "confirmed", "possible")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate whether already published live Me turns are enough to build a "
            "live-accessible Target-Me model for suppressed mic rescue. Batch labels are used only "
            "for offline scoring."
        )
    )
    parser.add_argument("sessions", nargs="*", type=Path)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument(
        "--scope",
        choices=("capture-safe-candidate", "real", "all-live"),
        default="capture-safe-candidate",
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_REPORT)
    parser.add_argument(
        "--method",
        default="auto",
        choices=["auto", "mfcc_voiceprint", "mfcc_contrastive", "resemblyzer_dvector", "wavlm_xvector"],
    )
    parser.add_argument("--wavlm-model", type=Path, default=None)
    parser.add_argument("--padding-sec", type=float, default=0.20)
    parser.add_argument("--min-enrollment-sec", type=float, default=1.2)
    parser.add_argument("--max-enrollment-sec", type=float, default=14.0)
    parser.add_argument("--min-enrollment-local-ratio", type=float, default=0.65)
    parser.add_argument("--max-enrollment-remote-active-ratio", type=float, default=0.20)
    parser.add_argument("--min-enrollment-count", type=int, default=3)
    parser.add_argument("--prefix-guard-sec", type=float, default=1.0)
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


def discover_sessions(root: Path, scope: str, explicit: list[Path]) -> list[Path]:
    if explicit:
        return [
            session
            for session in (resolve_session(root, value) for value in explicit)
            if (session / "derived/live/chunks.jsonl").exists()
        ]
    known = report_session_ids(root, scope)
    sessions: list[Path] = []
    for chunks in sorted((root / "sessions").glob("*/derived/live/chunks.jsonl")):
        session = chunks.parents[2]
        if session.name.startswith("_"):
            continue
        if known and session.name not in known:
            continue
        sessions.append(session)
    return sessions


def session_path(session: Path, value: Any) -> Path | None:
    if not value:
        return None
    path = Path(str(value))
    return path if path.is_absolute() else session / path


def chunk_audio_sources(session: Path, chunk_index: int) -> tuple[dict[str, Path], float]:
    chunk_path = session / "derived/live/chunks" / f"{chunk_index:06d}" / "chunk.json"
    chunk = read_json(chunk_path)
    chunk = chunk if isinstance(chunk, dict) else {}
    mic = chunk.get("mic") if isinstance(chunk.get("mic"), dict) else {}
    remote = chunk.get("remote") if isinstance(chunk.get("remote"), dict) else {}
    clip_start = safe_float(mic.get("clip_start_sec"), safe_float(chunk.get("clip_start_sec")))
    sources: dict[str, Path] = {}
    mic_clean = session_path(session, mic.get("asr_wav") or mic.get("wav") or mic.get("input"))
    mic_raw = session_path(session, mic.get("wav") or mic.get("input") or mic.get("asr_wav"))
    remote_wav = session_path(session, remote.get("wav") or remote.get("input"))
    if mic_clean is not None:
        sources["mic_clean"] = mic_clean
        sources["mic_role_masked"] = mic_clean
    if mic_raw is not None:
        sources["mic_raw"] = mic_raw
    if remote_wav is not None:
        sources["remote"] = remote_wav
    return sources, clip_start


def turn_token_count(compare: Any, text: Any) -> int:
    return len(compare.tokens(str(text or "")))


def clip_interval_for_chunk(start: float, end: float, clip_start: float, padding: float) -> tuple[float, float]:
    local_start = max(0.0, start - clip_start - padding)
    duration = max(0.05, end - start + 2 * padding)
    return local_start, duration


def embed_interval(
    *,
    session: Path,
    chunk_index: int,
    source_name: str,
    start: float,
    end: float,
    output: Path,
    backend: Any,
    tm: Any,
    args: argparse.Namespace,
) -> tuple[np.ndarray | None, dict[str, Any]]:
    sources, clip_start = chunk_audio_sources(session, chunk_index)
    source = sources.get(source_name)
    if source is None:
        return None, {"error": f"missing_source:{source_name}"}
    local_start, duration = clip_interval_for_chunk(start, end, clip_start, args.padding_sec)
    if args.write_clips:
        ok = tm.extract_wav(source, output, local_start, duration)
        clip = output
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        ok = tm.extract_wav(source, output, local_start, duration)
        clip = output
    if not ok:
        return None, {"error": "extract_failed", "source": str(source)}
    embedding, info = tm.embedding_for_clip(clip, backend)
    info["source_audio"] = source_name
    info["absolute_start"] = round(start, 3)
    info["absolute_end"] = round(end, 3)
    return embedding, info


def enrollment_candidates(
    *,
    session: Path,
    live_turns: list[dict[str, Any]],
    state_rows: list[dict[str, Any]],
    backend: Any,
    tm: Any,
    compare: Any,
    args: argparse.Namespace,
    out_dir: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    positives: list[dict[str, Any]] = []
    negatives: list[dict[str, Any]] = []
    for turn in live_turns:
        role = str(turn.get("role") or "")
        start = safe_float(turn.get("start"))
        end = safe_float(turn.get("end"), start)
        duration = max(0.0, end - start)
        if duration < args.min_enrollment_sec or duration > args.max_enrollment_sec:
            continue
        if turn_token_count(compare, turn.get("text")) < 2:
            continue
        chunk_index = safe_int(turn.get("chunk_index"))
        if role == "Me":
            state = tm.interval_state_features(state_rows, start, end)
            if state["local_only_ratio"] < args.min_enrollment_local_ratio:
                continue
            if state["remote_active_ratio"] > args.max_enrollment_remote_active_ratio:
                continue
            source_name = "mic_clean"
            bucket = positives
            prefix = "positive"
        elif role == "Colleagues":
            state = tm.interval_state_features(state_rows, start, end)
            source_name = "remote"
            bucket = negatives
            prefix = "negative"
        else:
            continue
        clip = out_dir / "clips/enrollment" / f"{prefix}_{len(bucket) + 1:04d}_{chunk_index:06d}.wav"
        embedding, info = embed_interval(
            session=session,
            chunk_index=chunk_index,
            source_name=source_name,
            start=start,
            end=end,
            output=clip,
            backend=backend,
            tm=tm,
            args=args,
        )
        item = {
            "id": str(turn.get("id") or f"{prefix}_{len(bucket) + 1:04d}"),
            "role": role,
            "chunk_index": chunk_index,
            "start": round(start, 3),
            "end": round(end, 3),
            "duration_sec": round(duration, 3),
            "text": str(turn.get("text") or ""),
            "state": state,
            "source_audio": source_name,
            "embedding_info": info,
            "accepted": embedding is not None,
            "embedding": embedding,
        }
        if embedding is None:
            item["reject_reason"] = info.get("error") or "embedding_failed"
        bucket.append(item)
    positives = [row for row in positives if row.get("accepted")]
    negatives = [row for row in negatives if row.get("accepted")]
    positives.sort(key=lambda row: (safe_float(row.get("end")), safe_float(row.get("start"))))
    negatives.sort(key=lambda row: (safe_float(row.get("end")), safe_float(row.get("start"))))
    return positives, negatives


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


def embed_candidate_sources(
    *,
    session: Path,
    segment: dict[str, Any],
    backend: Any,
    target_model: dict[str, Any],
    tm: Any,
    args: argparse.Namespace,
    out_dir: Path,
    mode: str,
    index: int,
) -> dict[str, dict[str, Any]]:
    start = safe_float(segment.get("start"))
    end = safe_float(segment.get("end"), start)
    chunk_index = safe_int(segment.get("chunk_index"))
    scores: dict[str, dict[str, Any]] = {}
    for source_name in ("mic_clean", "mic_raw", "remote"):
        clip = out_dir / "clips/candidates" / f"{mode}_{index:04d}_{source_name}.wav"
        embedding, info = embed_interval(
            session=session,
            chunk_index=chunk_index,
            source_name=source_name,
            start=start,
            end=end,
            output=clip,
            backend=backend,
            tm=tm,
            args=args,
        )
        model_scores = tm.model_score(embedding, target_model)
        payload = dict(info)
        payload["exists"] = embedding is not None
        payload["target_similarity"] = round(model_scores["target_similarity"], 6)
        payload["positive_similarity"] = round(model_scores["positive_similarity"], 6)
        payload["negative_similarity"] = round(model_scores["negative_similarity"], 6)
        scores[source_name] = payload
    scores.setdefault("mic_role_masked", scores.get("mic_clean", {"exists": False}))
    return scores


def classify_segment(
    *,
    session: Path,
    segment: dict[str, Any],
    target_model: dict[str, Any] | None,
    calibration: dict[str, Any],
    backend: Any,
    tm: Any,
    args: argparse.Namespace,
    out_dir: Path,
    state_rows: list[dict[str, Any]],
    mode: str,
    index: int,
) -> dict[str, Any]:
    start = safe_float(segment.get("start"))
    end = safe_float(segment.get("end"), start)
    if target_model is None:
        return {
            "mode": mode,
            "status": "not_ready",
            "classification": {
                "label": "target_me_ambiguous",
                "confidence": 0.0,
                "reason": calibration.get("status") or "enrollment_not_ready",
                "scores": {},
            },
        }
    state = tm.interval_state_features(state_rows, start, end)
    source_scores = embed_candidate_sources(
        session=session,
        segment=segment,
        backend=backend,
        target_model=target_model,
        tm=tm,
        args=args,
        out_dir=out_dir,
        mode=mode,
        index=index,
    )
    item = {
        "id": f"{mode}_{index:04d}",
        "utterances": [
            {
                "id": f"{mode}_{index:04d}_suppressed_mic",
                "role": "me",
                "source_track": "mic",
                "start": start,
                "end": end,
                "text": segment.get("text") or "",
            }
        ],
    }
    classification = tm.classify_target_me(
        item=item,
        target_model=target_model,
        calibration=calibration,
        state=state,
        source_scores=source_scores,
        audio_review=None,
        stronger_judge=None,
    )
    return {
        "mode": mode,
        "status": "ready",
        "classification": classification,
        "state": state,
        "source_scores": source_scores,
    }


def policy_labels(classification: dict[str, Any]) -> list[str]:
    label = str(classification.get("label") or "")
    confidence = safe_float(classification.get("confidence"))
    scores = classification.get("scores") if isinstance(classification.get("scores"), dict) else {}
    delta_vs_remote = safe_float(scores.get("delta_vs_remote"))
    remote_active = safe_float(scores.get("state_remote_active_ratio"))
    policies: list[str] = []
    if label == "target_me_confirmed" and confidence >= 0.78 and delta_vs_remote >= 0.12 and remote_active <= 0.25:
        policies.append("confirmed_remote_guard")
    if label == "target_me_confirmed" and confidence >= 0.72:
        policies.append("confirmed")
    if label in {"target_me_confirmed", "target_me_possible"} and confidence >= 0.55:
        policies.append("possible")
    return policies


def local_label(row: dict[str, Any]) -> bool:
    return row.get("batch_role_label") in LOCAL_LABELS


def summarize_policy(rows: list[dict[str, Any]], mode: str, policy: str) -> dict[str, Any]:
    selected = [
        row
        for row in rows
        if policy in (row.get("modes", {}).get(mode, {}).get("policy_candidates") or [])
    ]
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


def summarize_mode(rows: list[dict[str, Any]], mode: str) -> dict[str, Any]:
    mode_rows = [row for row in rows if row.get("modes", {}).get(mode, {}).get("status") == "ready"]
    local_ready = sum(safe_float(row.get("duration_sec")) for row in mode_rows if local_label(row))
    remote_ready = sum(safe_float(row.get("duration_sec")) for row in mode_rows if not local_label(row))
    total_local = sum(safe_float(row.get("duration_sec")) for row in rows if local_label(row))
    total_remote = sum(safe_float(row.get("duration_sec")) for row in rows if not local_label(row))
    return {
        "ready_count": len(mode_rows),
        "ready_seconds": round(local_ready + remote_ready, 3),
        "ready_local_seconds": round(local_ready, 3),
        "ready_remote_risk_seconds": round(remote_ready, 3),
        "not_ready_count": len(rows) - len(mode_rows),
        "not_ready_local_seconds": round(total_local - local_ready, 3),
        "not_ready_remote_risk_seconds": round(total_remote - remote_ready, 3),
        "policies": {policy: summarize_policy(rows, mode, policy) for policy in POLICIES},
    }


def example_payload(row: dict[str, Any], mode: str) -> dict[str, Any]:
    mode_row = row.get("modes", {}).get(mode, {})
    classification = mode_row.get("classification") if isinstance(mode_row.get("classification"), dict) else {}
    return {
        "session": row.get("session"),
        "chunk_index": row.get("chunk_index"),
        "start": row.get("start"),
        "end": row.get("end"),
        "duration_sec": row.get("duration_sec"),
        "batch_role_label": row.get("batch_role_label"),
        "text": row.get("text"),
        "mode": mode,
        "status": mode_row.get("status"),
        "label": classification.get("label"),
        "confidence": classification.get("confidence"),
        "policy_candidates": mode_row.get("policy_candidates"),
        "scores": classification.get("scores"),
    }


def session_report(
    *,
    session: Path,
    root: Path,
    compare: Any,
    tm: Any,
    backend: Any,
    backend_status: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, Any]:
    out_dir = session / "derived/audit/live-target-me-enrollment-lab"
    out_dir.mkdir(parents=True, exist_ok=True)
    chunks = compare.read_jsonl(session / "derived/live/chunks.jsonl")
    profile = compare.selected_profile(session)
    clean_dialogue = compare.selected_clean_dialogue_path(session, profile)
    batch_utterances = compare.read_utterances(clean_dialogue)
    live_turn_rows = compare.live_turns(session, chunks)
    state_rows = tm.load_speaker_state(session)
    suppressed = compare.read_suppressed_mic_asr_segment_audit(session, chunks, batch_utterances).get("segments") or []
    suppressed = [row for row in suppressed if isinstance(row, dict)]
    positives, negatives = enrollment_candidates(
        session=session,
        live_turns=live_turn_rows,
        state_rows=state_rows,
        backend=backend,
        tm=tm,
        compare=compare,
        args=args,
        out_dir=out_dir,
    )
    selected_rows: list[dict[str, Any]] = []
    full_model, full_calibration = build_model(
        tm,
        str(backend_status.get("selected") or backend.method),
        positives,
        negatives,
        args.min_enrollment_count,
    )
    for index, segment in enumerate(suppressed, start=1):
        start = safe_float(segment.get("start"))
        prefix_positives = [
            row for row in positives if safe_float(row.get("end")) <= start - args.prefix_guard_sec
        ]
        prefix_negatives = [
            row for row in negatives if safe_float(row.get("end")) <= start - args.prefix_guard_sec
        ]
        prefix_model, prefix_calibration = build_model(
            tm,
            str(backend_status.get("selected") or backend.method),
            prefix_positives,
            prefix_negatives,
            args.min_enrollment_count,
        )
        row = {
            "session": session.name,
            "chunk_index": safe_int(segment.get("chunk_index")),
            "start": round(start, 3),
            "end": segment.get("end"),
            "duration_sec": segment.get("duration_sec"),
            "text": segment.get("text") or "",
            "batch_role_label": segment.get("batch_role_label"),
            "segment_features": {
                "token_count": segment.get("token_count"),
                "unique_token_count": segment.get("segment_gate_unique_token_count"),
                "audio_mic_clean_rms_db": segment.get("audio_mic_clean_rms_db"),
                "audio_remote_rms_db": segment.get("audio_remote_rms_db"),
                "audio_mic_remote_zero_lag_abs_corr": segment.get("audio_mic_remote_zero_lag_abs_corr"),
            },
            "modes": {},
        }
        for mode, model, calibration in (
            ("full_live_enrollment", full_model, full_calibration),
            ("prefix_live_enrollment", prefix_model, prefix_calibration),
        ):
            result = classify_segment(
                session=session,
                segment=segment,
                target_model=model,
                calibration=calibration,
                backend=backend,
                tm=tm,
                args=args,
                out_dir=out_dir,
                state_rows=state_rows,
                mode=mode,
                index=index,
            )
            result["policy_candidates"] = policy_labels(result.get("classification") or {})
            result["enrollment"] = {
                "positive_count": safe_int(calibration.get("positive_count")),
                "negative_count": safe_int(calibration.get("negative_count")),
                "positive_total_sec": calibration.get("positive_total_sec"),
                "negative_total_sec": calibration.get("negative_total_sec"),
                "target_threshold": calibration.get("target_threshold"),
                "weak_target_threshold": calibration.get("weak_target_threshold"),
            }
            row["modes"][mode] = result
        selected_rows.append(row)
    write_json(out_dir / "live_target_me_enrollment_lab_rows.json", selected_rows)
    summary = {
        "session": session.name,
        "path": rel(session, root),
        "selected_profile": profile,
        "clean_dialogue": rel(clean_dialogue, root) if clean_dialogue else None,
        "live_turn_count": len(live_turn_rows),
        "suppressed_mic_segment_count": len(selected_rows),
        "suppressed_mic_segment_seconds": round(sum(safe_float(row.get("duration_sec")) for row in selected_rows), 3),
        "local_seconds": round(sum(safe_float(row.get("duration_sec")) for row in selected_rows if local_label(row)), 3),
        "remote_risk_seconds": round(sum(safe_float(row.get("duration_sec")) for row in selected_rows if not local_label(row)), 3),
        "positive_enrollment_count": len(positives),
        "positive_enrollment_seconds": round(sum(safe_float(row.get("duration_sec")) for row in positives), 3),
        "negative_enrollment_count": len(negatives),
        "negative_enrollment_seconds": round(sum(safe_float(row.get("duration_sec")) for row in negatives), 3),
        "backend": backend_status,
        "modes": {
            "full_live_enrollment": summarize_mode(selected_rows, "full_live_enrollment"),
            "prefix_live_enrollment": summarize_mode(selected_rows, "prefix_live_enrollment"),
        },
        "examples": {
            "prefix_remote_risk": [
                example_payload(row, "prefix_live_enrollment")
                for row in selected_rows
                if not local_label(row)
                and row.get("modes", {}).get("prefix_live_enrollment", {}).get("policy_candidates")
            ][: args.max_examples],
            "prefix_local_confirmed": [
                example_payload(row, "prefix_live_enrollment")
                for row in selected_rows
                if local_label(row)
                and "confirmed_remote_guard" in (row.get("modes", {}).get("prefix_live_enrollment", {}).get("policy_candidates") or [])
            ][: args.max_examples],
        },
    }
    write_json(out_dir / "live_target_me_enrollment_lab_summary.json", summary)
    return summary


def aggregate_mode(summaries: list[dict[str, Any]], mode: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ready_count": 0,
        "ready_seconds": 0.0,
        "ready_local_seconds": 0.0,
        "ready_remote_risk_seconds": 0.0,
        "not_ready_count": 0,
        "not_ready_local_seconds": 0.0,
        "not_ready_remote_risk_seconds": 0.0,
        "policies": {policy: Counter() for policy in POLICIES},
    }
    for summary in summaries:
        mode_row = summary.get("modes", {}).get(mode, {})
        for key in (
            "ready_count",
            "ready_seconds",
            "ready_local_seconds",
            "ready_remote_risk_seconds",
            "not_ready_count",
            "not_ready_local_seconds",
            "not_ready_remote_risk_seconds",
        ):
            result[key] += safe_float(mode_row.get(key))
        policies = mode_row.get("policies") if isinstance(mode_row.get("policies"), dict) else {}
        for policy in POLICIES:
            policy_row = policies.get(policy) if isinstance(policies.get(policy), dict) else {}
            for key in ("selected_count", "selected_seconds", "local_seconds", "remote_risk_seconds"):
                result["policies"][policy][key] += safe_float(policy_row.get(key))
    total_local = sum(safe_float(summary.get("local_seconds")) for summary in summaries)
    for key in (
        "ready_seconds",
        "ready_local_seconds",
        "ready_remote_risk_seconds",
        "not_ready_local_seconds",
        "not_ready_remote_risk_seconds",
    ):
        result[key] = round(result[key], 3)
    result["ready_count"] = int(result["ready_count"])
    result["not_ready_count"] = int(result["not_ready_count"])
    policies_payload: dict[str, Any] = {}
    for policy, values in result["policies"].items():
        selected_seconds = values["selected_seconds"]
        local_seconds = values["local_seconds"]
        remote_seconds = values["remote_risk_seconds"]
        policies_payload[policy] = {
            "selected_count": int(values["selected_count"]),
            "selected_seconds": round(selected_seconds, 3),
            "local_seconds": round(local_seconds, 3),
            "remote_risk_seconds": round(remote_seconds, 3),
            "precision_proxy": round_float(local_seconds / selected_seconds if selected_seconds else None),
            "local_recall_proxy": round_float(local_seconds / total_local if total_local else None),
        }
    result["policies"] = policies_payload
    return result


def corpus_conclusion(summary: dict[str, Any]) -> dict[str, Any]:
    prefix = summary["modes"]["prefix_live_enrollment"]["policies"]["confirmed_remote_guard"]
    full = summary["modes"]["full_live_enrollment"]["policies"]["confirmed_remote_guard"]
    prefix_ready_local = safe_float(summary["modes"]["prefix_live_enrollment"].get("ready_local_seconds"))
    total_local = safe_float(summary.get("local_seconds"))
    prefix_local = safe_float(prefix.get("local_seconds"))
    prefix_remote = safe_float(prefix.get("remote_risk_seconds"))
    full_local = safe_float(full.get("local_seconds"))
    full_remote = safe_float(full.get("remote_risk_seconds"))
    if total_local <= 0:
        status = "no_local_labels"
        next_step = "collect comparable live sessions with suppressed mic local labels"
    elif prefix_ready_local / total_local < 0.25:
        status = "not_enough_online_enrollment"
        next_step = "add enrollment fallback/calibration before relying on online Target-Me rescue"
    elif prefix_remote > 0:
        status = "online_enrollment_not_safe"
        next_step = "tighten remote-forbidden gates and inspect remote-risk examples before materializing"
    elif prefix_local / total_local >= 0.30:
        status = "promising_online_target_me_gate"
        next_step = "materialize prefix-live Target-Me as a shadow profile and run parity gates"
    elif full_local > prefix_local * 1.5 and full_remote <= prefix_remote:
        status = "needs_warmer_enrollment"
        next_step = "design warmup/enrollment fallback; same-session live evidence works only after enough Me turns"
    else:
        status = "limited_online_target_me_recovery"
        next_step = "keep Target-Me as supporting evidence and continue with stronger remote-forbidden/local judge"
    return {
        "status": status,
        "total_local_seconds": round(total_local, 3),
        "prefix_ready_local_seconds": round(prefix_ready_local, 3),
        "prefix_confirmed_remote_guard_local_seconds": round(prefix_local, 3),
        "prefix_confirmed_remote_guard_remote_risk_seconds": round(prefix_remote, 3),
        "full_confirmed_remote_guard_local_seconds": round(full_local, 3),
        "full_confirmed_remote_guard_remote_risk_seconds": round(full_remote, 3),
        "recommended_next": next_step,
    }


def aggregate_report(summaries: list[dict[str, Any]], args: argparse.Namespace) -> dict[str, Any]:
    by_backend = Counter(str((summary.get("backend") or {}).get("selected") or "unknown") for summary in summaries)
    total_local = sum(safe_float(summary.get("local_seconds")) for summary in summaries)
    total_remote = sum(safe_float(summary.get("remote_risk_seconds")) for summary in summaries)
    payload = {
        "schema": SCHEMA,
        "generator": {
            "script": "scripts/report-live-target-me-enrollment-lab.py",
            "version": SCRIPT_VERSION,
        },
        "created_at": now_iso(),
        "scope": args.scope if not args.sessions else "explicit",
        "config": {
            "method": args.method,
            "prefix_guard_sec": args.prefix_guard_sec,
            "min_enrollment_count": args.min_enrollment_count,
            "min_enrollment_local_ratio": args.min_enrollment_local_ratio,
            "max_enrollment_remote_active_ratio": args.max_enrollment_remote_active_ratio,
        },
        "session_count": len(summaries),
        "backend_counts": dict(by_backend),
        "suppressed_mic_segment_count": sum(safe_int(summary.get("suppressed_mic_segment_count")) for summary in summaries),
        "suppressed_mic_segment_seconds": round(
            sum(safe_float(summary.get("suppressed_mic_segment_seconds")) for summary in summaries),
            3,
        ),
        "local_seconds": round(total_local, 3),
        "remote_risk_seconds": round(total_remote, 3),
        "positive_enrollment_count": sum(safe_int(summary.get("positive_enrollment_count")) for summary in summaries),
        "positive_enrollment_seconds": round(sum(safe_float(summary.get("positive_enrollment_seconds")) for summary in summaries), 3),
        "modes": {
            "full_live_enrollment": aggregate_mode(summaries, "full_live_enrollment"),
            "prefix_live_enrollment": aggregate_mode(summaries, "prefix_live_enrollment"),
        },
        "session_summaries": summaries,
    }
    payload["conclusion"] = corpus_conclusion(payload)
    return payload


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# Live Target-Me Enrollment Lab",
        "",
        f"Generated: `{report['created_at']}`",
        "",
        "## Scope",
        "",
        f"- scope: `{report['scope']}`",
        f"- sessions: `{report['session_count']}`",
        f"- suppressed mic ASR segments: `{report['suppressed_mic_segment_count']}` / `{report['suppressed_mic_segment_seconds']}` sec",
        f"- local/mixed seconds by batch labels: `{report['local_seconds']}`",
        f"- remote-risk seconds by batch labels: `{report['remote_risk_seconds']}`",
        f"- positive live enrollment: `{report['positive_enrollment_count']}` segments / `{report['positive_enrollment_seconds']}` sec",
        "",
        "## Conclusion",
        "",
        f"- status: `{report['conclusion']['status']}`",
        f"- prefix ready local seconds: `{report['conclusion']['prefix_ready_local_seconds']}`",
        f"- prefix confirmed remote-guard local seconds: `{report['conclusion']['prefix_confirmed_remote_guard_local_seconds']}`",
        f"- prefix confirmed remote-guard remote-risk seconds: `{report['conclusion']['prefix_confirmed_remote_guard_remote_risk_seconds']}`",
        f"- full-live confirmed remote-guard local seconds: `{report['conclusion']['full_confirmed_remote_guard_local_seconds']}`",
        f"- full-live confirmed remote-guard remote-risk seconds: `{report['conclusion']['full_confirmed_remote_guard_remote_risk_seconds']}`",
        f"- recommended next: {report['conclusion']['recommended_next']}",
        "",
        "## Policy Summary",
        "",
    ]
    for mode_name, mode in report["modes"].items():
        lines.append(f"### `{mode_name}`")
        lines.append("")
        lines.append("| Policy | Selected sec | Local sec | Remote-risk sec | Precision | Recall |")
        lines.append("| --- | ---: | ---: | ---: | ---: | ---: |")
        for policy, row in mode["policies"].items():
            lines.append(
                f"| `{policy}` | {row['selected_seconds']} | {row['local_seconds']} "
                f"| {row['remote_risk_seconds']} | {row['precision_proxy']} | {row['local_recall_proxy']} |"
            )
        lines.append("")
    lines.append("## Sessions")
    lines.append("")
    lines.append("| Session | Local sec | Positive enrollment | Prefix ready local sec | Prefix guard local sec | Prefix guard remote-risk sec |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: |")
    for session in report["session_summaries"]:
        prefix = session["modes"]["prefix_live_enrollment"]
        policy = prefix["policies"]["confirmed_remote_guard"]
        lines.append(
            f"| `{session['session']}` | {session['local_seconds']} "
            f"| {session['positive_enrollment_count']} / {session['positive_enrollment_seconds']} "
            f"| {prefix['ready_local_seconds']} | {policy['local_seconds']} "
            f"| {policy['remote_risk_seconds']} |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    compare = load_module(root, "murmurmark_compare_live_batch", "scripts/compare-live-batch.py")
    tm = load_module(root, "murmurmark_audit_target_me", "scripts/audit-target-me.py")
    backend, backend_status = tm.resolve_embedding_backend(args)
    sessions = discover_sessions(root, args.scope, args.sessions)
    summaries = [
        session_report(
            session=session,
            root=root,
            compare=compare,
            tm=tm,
            backend=backend,
            backend_status=backend_status,
            args=args,
        )
        for session in sessions
    ]
    report = aggregate_report(summaries, args)
    out = args.out if args.out.is_absolute() else root / args.out
    write_json(out, report)
    write_markdown(out.with_suffix(".md"), markdown_report(report))
    print(f"written: {rel(out, root)}")
    print(f"written: {rel(out.with_suffix('.md'), root)}")
    print(
        "summary: status={status} sessions={sessions} prefix_local={local}s prefix_remote_risk={remote}s".format(
            status=report["conclusion"]["status"],
            sessions=report["session_count"],
            local=report["conclusion"]["prefix_confirmed_remote_guard_local_seconds"],
            remote=report["conclusion"]["prefix_confirmed_remote_guard_remote_risk_seconds"],
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
