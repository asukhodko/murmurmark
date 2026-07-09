#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np


SCHEMA = "murmurmark.live_local_only_enrollment_probe/v1"
SCRIPT_VERSION = "0.3.0"
DEFAULT_CORPUS_REPORT = Path("sessions/_reports/live-pipeline/live_corpus_gates_report.json")
DEFAULT_OUT = Path("sessions/_reports/live-pipeline/live_local_only_enrollment_probe.json")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Probe whether same-session high-confidence local-only mic intervals are enough to seed "
            "a causal Target-Me enrollment model for blocked live mixed rows. Diagnostic only."
        )
    )
    parser.add_argument("sessions", nargs="*", type=Path)
    parser.add_argument("--corpus-report", type=Path, default=DEFAULT_CORPUS_REPORT)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--method",
        default="auto",
        choices=["auto", "mfcc_voiceprint", "mfcc_contrastive", "resemblyzer_dvector", "wavlm_xvector"],
    )
    parser.add_argument("--wavlm-model", type=Path, default=None)
    parser.add_argument("--min-segment-sec", type=float, default=1.2)
    parser.add_argument("--max-segment-sec", type=float, default=8.0)
    parser.add_argument("--min-local-confidence", type=float, default=0.80)
    parser.add_argument("--min-remote-confidence", type=float, default=0.60)
    parser.add_argument("--min-mic-db", type=float, default=-55.0)
    parser.add_argument("--min-remote-db", type=float, default=-55.0)
    parser.add_argument("--max-positive-segments", type=int, default=24)
    parser.add_argument("--max-negative-segments", type=int, default=24)
    parser.add_argument("--max-live-segments", type=int, default=120)
    parser.add_argument("--padding-sec", type=float, default=0.10)
    parser.add_argument("--write-clips", action=argparse.BooleanOptionalAction, default=True)
    return parser.parse_args()


def load_module(name: str, relative_path: str) -> Any:
    path = Path(__file__).resolve().parent / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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


def percentile(values: list[float], q: float, default: float = 0.0) -> float:
    if not values:
        return default
    return float(np.percentile(np.asarray(values, dtype=np.float64), q))


def round_float(value: float | None, digits: int = 6) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def sessions_from_report(report: dict[str, Any] | None) -> list[Path]:
    lab = (report or {}).get("live_same_session_voice_disambiguation_lab")
    examples = lab.get("examples") if isinstance(lab, dict) and isinstance(lab.get("examples"), list) else []
    names: list[str] = []
    for row in examples:
        if not isinstance(row, dict):
            continue
        name = str(row.get("session") or "")
        if name and name not in names:
            names.append(name)
    return [Path("sessions") / name for name in names]


def normalize_sessions(values: list[Path], report: dict[str, Any] | None) -> list[Path]:
    sessions = values or sessions_from_report(report)
    result: list[Path] = []
    for session in sessions:
        if session.is_absolute():
            result.append(session)
        elif session.parts and session.parts[0] == "sessions":
            result.append(session)
        else:
            result.append(Path("sessions") / session)
    seen: set[str] = set()
    unique: list[Path] = []
    for session in result:
        key = str(session)
        if key not in seen:
            unique.append(session)
            seen.add(key)
    return unique


def state_candidates(
    rows: list[dict[str, Any]],
    *,
    state_name: str,
    min_duration: float,
    max_duration: float,
    min_confidence: float,
    min_db_key: str,
    min_db: float,
    limit: int,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for row in rows:
        if str(row.get("state") or "") != state_name:
            continue
        start = safe_float(row.get("start"))
        end = safe_float(row.get("end"), start)
        duration = end - start
        if duration < min_duration or duration > max_duration:
            continue
        confidence = safe_float(row.get("confidence"))
        if confidence < min_confidence:
            continue
        level_db = safe_float(row.get(min_db_key), -120.0)
        if level_db < min_db:
            continue
        score = confidence * 100 + min(duration, 8.0) * 3 + max(-60.0, level_db)
        candidates.append(
            {
                "start": round(start, 3),
                "end": round(end, 3),
                "duration_sec": round(duration, 3),
                "state": state_name,
                "confidence": round(confidence, 3),
                "mic_db": row.get("mic_db"),
                "remote_db": row.get("remote_db"),
                "score": round(score, 3),
            }
        )
    candidates.sort(key=lambda item: (-safe_float(item.get("score")), safe_float(item.get("start"))))
    return candidates[:limit]


def embed_candidates(
    *,
    session: Path,
    source: Path,
    source_name: str,
    candidates: list[dict[str, Any]],
    out_dir: Path,
    prefix: str,
    backend: Any,
    tm: Any,
    args: argparse.Namespace,
) -> tuple[list[dict[str, Any]], list[np.ndarray]]:
    selected: list[dict[str, Any]] = []
    embeddings: list[np.ndarray] = []
    for index, candidate in enumerate(candidates, start=1):
        start = max(0.0, safe_float(candidate.get("start")) - args.padding_sec)
        end = safe_float(candidate.get("end")) + args.padding_sec
        clip = out_dir / "clips" / prefix / f"{prefix}_{index:04d}_{start:.3f}_{end:.3f}.wav"
        ok = tm.extract_wav(source, clip, start, end - start)
        item = dict(candidate)
        item["source_audio"] = source_name
        item["clip"] = str(clip) if args.write_clips else ""
        if not ok:
            item["accepted"] = False
            item["reject_reason"] = "extract_failed"
            selected.append(item)
            continue
        embedding, info = backend.embed(clip)
        item["embedding_info"] = info
        if embedding is None:
            item["accepted"] = False
            item["reject_reason"] = info.get("error") or "embedding_failed"
            selected.append(item)
            continue
        item["accepted"] = True
        selected.append(item)
        embeddings.append(embedding)
    return selected, embeddings


def build_target_model(tm: Any, positives: list[np.ndarray], negatives: list[np.ndarray]) -> dict[str, Any]:
    positive_centroid = tm.make_centroid(positives)
    negative_centroid = tm.make_centroid(negatives)
    return {
        "positive_centroid": positive_centroid,
        "negative_centroid": negative_centroid,
        "scoring": "contrastive" if negative_centroid is not None else "cosine",
    }


def accepted_embedding_pairs(
    rows: list[dict[str, Any]],
    embeddings: list[np.ndarray],
) -> list[tuple[dict[str, Any], np.ndarray]]:
    pairs: list[tuple[dict[str, Any], np.ndarray]] = []
    embedding_index = 0
    for row in rows:
        if not row.get("accepted"):
            continue
        if embedding_index >= len(embeddings):
            break
        pairs.append((row, embeddings[embedding_index]))
        embedding_index += 1
    return pairs


def score_embeddings(tm: Any, positives: list[np.ndarray], negatives: list[np.ndarray], model: dict[str, Any]) -> dict[str, Any]:
    positive_scores = [tm.model_score(vector, model) for vector in positives]
    negative_scores = [tm.model_score(vector, model) for vector in negatives]
    positive_target = [safe_float(row.get("target_similarity")) for row in positive_scores]
    negative_target = [safe_float(row.get("target_similarity")) for row in negative_scores]
    positive_similarity = [safe_float(row.get("positive_similarity")) for row in positive_scores]
    negative_similarity = [safe_float(row.get("positive_similarity")) for row in negative_scores]
    margin_p50 = percentile(positive_target, 50) - percentile(negative_target, 50)
    margin_p10_p90 = percentile(positive_target, 10) - percentile(negative_target, 90)
    return {
        "scoring": model["scoring"],
        "positive_target_similarity": {
            "p10": round_float(percentile(positive_target, 10)),
            "p50": round_float(percentile(positive_target, 50)),
            "p90": round_float(percentile(positive_target, 90)),
        },
        "negative_target_similarity": {
            "p10": round_float(percentile(negative_target, 10)),
            "p50": round_float(percentile(negative_target, 50)),
            "p90": round_float(percentile(negative_target, 90)),
        },
        "positive_similarity_to_positive_centroid": {
            "p10": round_float(percentile(positive_similarity, 10)),
            "p50": round_float(percentile(positive_similarity, 50)),
            "p90": round_float(percentile(positive_similarity, 90)),
        },
        "negative_similarity_to_positive_centroid": {
            "p10": round_float(percentile(negative_similarity, 10)),
            "p50": round_float(percentile(negative_similarity, 50)),
            "p90": round_float(percentile(negative_similarity, 90)),
        },
        "margin_p50": round_float(margin_p50),
        "margin_p10_p90": round_float(margin_p10_p90),
    }


def blocked_rows_by_session(report: dict[str, Any] | None) -> dict[str, list[dict[str, Any]]]:
    lab = (report or {}).get("live_same_session_voice_disambiguation_lab")
    examples = lab.get("examples") if isinstance(lab, dict) and isinstance(lab.get("examples"), list) else []
    result: dict[str, list[dict[str, Any]]] = {}
    for row in examples:
        if not isinstance(row, dict):
            continue
        session_id = str(row.get("session") or "")
        if not session_id:
            continue
        result.setdefault(session_id, []).append(row)
    return result


def target_thresholds(scores: dict[str, Any]) -> dict[str, float]:
    positive_target = scores.get("positive_target_similarity") if isinstance(scores.get("positive_target_similarity"), dict) else {}
    negative_target = scores.get("negative_target_similarity") if isinstance(scores.get("negative_target_similarity"), dict) else {}
    positive_to_centroid = (
        scores.get("positive_similarity_to_positive_centroid")
        if isinstance(scores.get("positive_similarity_to_positive_centroid"), dict)
        else {}
    )
    negative_to_centroid = (
        scores.get("negative_similarity_to_positive_centroid")
        if isinstance(scores.get("negative_similarity_to_positive_centroid"), dict)
        else {}
    )
    positive_target_p10 = safe_float(positive_target.get("p10"))
    negative_target_p90 = safe_float(negative_target.get("p90"))
    positive_centroid_p10 = safe_float(positive_to_centroid.get("p10"))
    negative_centroid_p90 = safe_float(negative_to_centroid.get("p90"))
    return {
        "target_similarity": round((positive_target_p10 + negative_target_p90) / 2.0, 6),
        "positive_similarity": round(max(0.0, positive_centroid_p10 - 0.05), 6),
        "negative_similarity_max": round(negative_centroid_p90 + 0.05, 6),
    }


def evaluate_blocked_mixed_rows(
    *,
    session: Path,
    rows: list[dict[str, Any]],
    source: Path,
    source_name: str,
    state_rows: list[dict[str, Any]],
    model: dict[str, Any],
    thresholds: dict[str, float],
    out_dir: Path,
    backend: Any,
    tm: Any,
    args: argparse.Namespace,
) -> list[dict[str, Any]]:
    evaluations: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        start = safe_float(row.get("start"))
        end = safe_float(row.get("end"), start)
        if end <= start:
            continue
        clip_start = max(0.0, start - args.padding_sec)
        clip_end = end + args.padding_sec
        clip = out_dir / "clips/blocked_mixed" / f"blocked_{index:04d}_{start:.3f}_{end:.3f}.wav"
        ok = tm.extract_wav(source, clip, clip_start, clip_end - clip_start)
        embedding = None
        info: dict[str, Any]
        if ok:
            embedding, info = backend.embed(clip)
        else:
            info = {"error": "extract_failed"}
        scores = tm.model_score(embedding, model)
        state = tm.interval_state_features(state_rows, start, end)
        target_score = safe_float(scores.get("target_similarity"))
        positive_score = safe_float(scores.get("positive_similarity"))
        negative_score = safe_float(scores.get("negative_similarity"))
        reasons: list[str] = []
        if embedding is None:
            reasons.append(info.get("error") or "embedding_failed")
        if target_score < safe_float(thresholds.get("target_similarity")):
            reasons.append("below_target_similarity_threshold")
        if positive_score < safe_float(thresholds.get("positive_similarity")):
            reasons.append("below_positive_similarity_threshold")
        if negative_score > safe_float(thresholds.get("negative_similarity_max")):
            reasons.append("too_close_to_remote_negative")
        if safe_float(state.get("remote_active_ratio")) > 0.20:
            reasons.append("remote_active_state_overlap")
        if safe_float(state.get("local_score_proxy")) < 0.65:
            reasons.append("weak_local_state")
        if not reasons:
            classification = "local_only_seed_supports_blocked_mixed_row"
        elif any(reason in reasons for reason in ("too_close_to_remote_negative", "remote_active_state_overlap")):
            classification = "blocked_mixed_row_remote_ambiguous"
        elif embedding is None:
            classification = "blocked_mixed_row_embedding_failed"
        else:
            classification = "blocked_mixed_row_not_supported_by_seed"
        evaluations.append(
            {
                "session": session.name,
                "batch_id": row.get("batch_id"),
                "start": row.get("start"),
                "end": row.get("end"),
                "duration_sec": row.get("duration_sec"),
                "text": row.get("text"),
                "source_audio": source_name,
                "clip": str(clip) if args.write_clips else "",
                "classification": classification,
                "reasons": reasons,
                "thresholds": thresholds,
                "scores": {
                    "target_similarity": round(target_score, 6),
                    "positive_similarity": round(positive_score, 6),
                    "negative_similarity": round(negative_score, 6),
                },
                "state": state,
                "embedding_info": info,
                "publication_allowed": False,
            }
        )
    return evaluations


def live_suppressed_mic_segments(session: Path, limit: int) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for chunk_path in sorted((session / "derived/live/chunks").glob("*/chunk.json")):
        chunk = read_json(chunk_path) or {}
        mic = chunk.get("mic") if isinstance(chunk.get("mic"), dict) else {}
        gate = mic.get("live_segment_role_gate") if isinstance(mic.get("live_segment_role_gate"), dict) else {}
        chunk_index = safe_int(chunk_path.parent.name)
        for status, key in (("kept", "kept_segments"), ("suppressed", "suppressed_segments")):
            rows = gate.get(key) if isinstance(gate.get(key), list) else []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                start = safe_float(row.get("start"))
                end = safe_float(row.get("end"), start)
                text = " ".join(str(row.get("text") or "").split())
                normalized = text.casefold()
                if any(
                    marker in normalized
                    for marker in ("продолжение следует", "редактор субтитров", "субтитры создавал")
                ):
                    continue
                if end - start < 0.5 or end - start > 12.0 or len(text.split()) < 2:
                    continue
                candidates.append(
                    {
                        **row,
                        "chunk_index": chunk_index,
                        "segment_gate_status": status,
                        "segment_gate_reason": row.get("reason"),
                        "segment_gate_mic_token_recall_in_overlapping_remote": row.get(
                            "mic_token_recall_in_overlapping_remote"
                        ),
                        "segment_gate_overlapping_remote_token_recall_in_mic": row.get(
                            "overlapping_remote_token_recall_in_mic"
                        ),
                        "candidate_source": "live_chunk_segment_role_gate",
                    }
                )
    candidates.sort(key=lambda row: (safe_int(row.get("chunk_index")), safe_float(row.get("start"))))
    return candidates[:limit] if limit > 0 else candidates


def live_chunk_mic_source(session: Path, chunk_index: int) -> tuple[Path | None, float]:
    chunk = read_json(session / "derived/live/chunks" / f"{chunk_index:06d}" / "chunk.json") or {}
    mic = chunk.get("mic") if isinstance(chunk.get("mic"), dict) else {}
    value = mic.get("asr_wav") or mic.get("wav") or mic.get("input")
    if not value:
        return None, 0.0
    path = Path(str(value))
    if not path.is_absolute():
        path = session / path
    clip_start = safe_float(mic.get("clip_start_sec"), safe_float(chunk.get("clip_start_sec")))
    return path, clip_start


def evaluate_live_segments(
    *,
    session: Path,
    rows: list[dict[str, Any]],
    state_rows: list[dict[str, Any]],
    model: dict[str, Any],
    thresholds: dict[str, float],
    out_dir: Path,
    backend: Any,
    tm: Any,
    args: argparse.Namespace,
    causal_positive_seeds: list[tuple[dict[str, Any], np.ndarray]] | None = None,
    causal_negative_seeds: list[tuple[dict[str, Any], np.ndarray]] | None = None,
) -> list[dict[str, Any]]:
    evaluations: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        start = safe_float(row.get("start"))
        end = safe_float(row.get("end"), start)
        chunk_index = safe_int(row.get("chunk_index"))
        source, clip_start = live_chunk_mic_source(session, chunk_index)
        local_start = max(0.0, start - clip_start - args.padding_sec)
        duration = max(0.0, end - start) + 2 * args.padding_sec
        clip = out_dir / "clips/live_segments" / f"segment_{index:04d}_{start:.3f}_{end:.3f}.wav"
        ok = bool(source) and tm.extract_wav(source, clip, local_start, duration)
        embedding = None
        info: dict[str, Any]
        if ok:
            embedding, info = backend.embed(clip)
        else:
            info = {"error": "extract_failed"}
        causal = causal_positive_seeds is not None and causal_negative_seeds is not None
        active_model = model
        active_thresholds = thresholds
        enrollment: dict[str, Any] = {"mode": "full_session_noncausal"}
        enrollment_reasons: list[str] = []
        if causal:
            prior_positives = [
                vector
                for seed, vector in causal_positive_seeds
                if safe_float(seed.get("end")) <= start
            ]
            prior_negatives = [
                vector
                for seed, vector in causal_negative_seeds
                if safe_float(seed.get("end")) <= start
            ]
            enrollment = {
                "mode": "past_only",
                "positive_seed_count": len(prior_positives),
                "negative_seed_count": len(prior_negatives),
                "cutoff_sec": round(start, 3),
            }
            if len(prior_positives) < 3:
                enrollment_reasons.append("insufficient_past_local_only_seeds")
            if len(prior_negatives) < 3:
                enrollment_reasons.append("insufficient_past_remote_only_seeds")
            if not enrollment_reasons:
                active_model = build_target_model(tm, prior_positives, prior_negatives)
                active_score_distribution = score_embeddings(
                    tm,
                    prior_positives,
                    prior_negatives,
                    active_model,
                )
                active_thresholds = target_thresholds(active_score_distribution)
                enrollment["thresholds"] = active_thresholds
                enrollment["score_distribution"] = active_score_distribution
        scores = tm.model_score(embedding, active_model) if not enrollment_reasons else {}
        state = tm.interval_state_features(state_rows, start, end)
        target_score = safe_float(scores.get("target_similarity"))
        positive_score = safe_float(scores.get("positive_similarity"))
        negative_score = safe_float(scores.get("negative_similarity"))
        mic_remote_recall = safe_float(row.get("segment_gate_mic_token_recall_in_overlapping_remote"))
        remote_mic_recall = safe_float(row.get("segment_gate_overlapping_remote_token_recall_in_mic"))
        reasons: list[str] = list(enrollment_reasons)
        if embedding is None:
            reasons.append(info.get("error") or "embedding_failed")
        if not enrollment_reasons and target_score < safe_float(active_thresholds.get("target_similarity")):
            reasons.append("below_target_similarity_threshold")
        if not enrollment_reasons and positive_score < safe_float(active_thresholds.get("positive_similarity")):
            reasons.append("below_positive_similarity_threshold")
        if not enrollment_reasons and negative_score > safe_float(active_thresholds.get("negative_similarity_max")):
            reasons.append("too_close_to_remote_negative")
        if safe_float(state.get("remote_active_ratio")) > 0.20:
            reasons.append("remote_active_state_overlap")
        if safe_float(state.get("local_score_proxy")) < 0.40:
            reasons.append("weak_local_state")
        if mic_remote_recall > 0.75 or remote_mic_recall > 0.75:
            reasons.append("live_text_too_close_to_remote")
        if not reasons:
            classification = (
                "causal_local_only_seed_supports_live_segment"
                if causal
                else "local_only_seed_supports_live_segment"
            )
        elif any(
            reason in reasons
            for reason in (
                "too_close_to_remote_negative",
                "remote_active_state_overlap",
                "live_text_too_close_to_remote",
            )
        ):
            classification = "causal_live_segment_remote_ambiguous" if causal else "live_segment_remote_ambiguous"
        elif embedding is None:
            classification = "causal_live_segment_embedding_failed" if causal else "live_segment_embedding_failed"
        else:
            classification = (
                "causal_live_segment_not_supported_by_seed"
                if causal
                else "live_segment_not_supported_by_seed"
            )
        evaluations.append(
            {
                "session": session.name,
                "chunk_index": chunk_index,
                "start": round(start, 3),
                "end": round(end, 3),
                "duration_sec": round(max(0.0, end - start), 3),
                "text": row.get("text") or "",
                "source_audio": "live_chunk_mic",
                "source_path": str(source) if source else None,
                "source_clip_start_sec": round(clip_start, 3),
                "clip": str(clip) if args.write_clips else "",
                "classification": classification,
                "reasons": reasons,
                "thresholds": active_thresholds,
                "enrollment": enrollment,
                "scores": {
                    "target_similarity": round(target_score, 6),
                    "positive_similarity": round(positive_score, 6),
                    "negative_similarity": round(negative_score, 6),
                },
                "state": state,
                "live_features": {
                    "segment_gate_status": row.get("segment_gate_status"),
                    "segment_gate_reason": row.get("segment_gate_reason"),
                    "mic_token_recall_in_overlapping_remote": row.get(
                        "segment_gate_mic_token_recall_in_overlapping_remote"
                    ),
                    "overlapping_remote_token_recall_in_mic": row.get(
                        "segment_gate_overlapping_remote_token_recall_in_mic"
                    ),
                    "audio_mic_minus_remote_rms_db": row.get("audio_mic_minus_remote_rms_db"),
                    "audio_mic_remote_zero_lag_abs_corr": row.get("audio_mic_remote_zero_lag_abs_corr"),
                },
                "embedding_info": info,
                "used_batch_fields_for_selection": False,
                "publication_allowed": False,
            }
        )
    return evaluations


def classify_probe(accepted_positive: int, accepted_negative: int, scores: dict[str, Any]) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if accepted_positive < 3:
        reasons.append("insufficient_positive_local_only_segments")
    if accepted_negative < 3:
        reasons.append("insufficient_remote_negative_segments")
    margin_p50 = safe_float(scores.get("margin_p50"))
    margin_p10_p90 = safe_float(scores.get("margin_p10_p90"))
    pos_p10 = safe_float((scores.get("positive_similarity_to_positive_centroid") or {}).get("p10"))
    neg_p90 = safe_float((scores.get("negative_similarity_to_positive_centroid") or {}).get("p90"))
    if pos_p10 < 0.62:
        reasons.append("weak_positive_cluster")
    if accepted_negative >= 3 and neg_p90 > 0.82:
        reasons.append("remote_too_close_to_local_centroid")
    if accepted_negative >= 3 and margin_p50 < 0.08:
        reasons.append("low_positive_vs_remote_margin")
    if accepted_negative >= 3 and margin_p10_p90 < -0.02:
        reasons.append("unsafe_low_tail_margin")

    if accepted_positive < 3:
        return "not_enough_local_only_audio", reasons
    if "weak_positive_cluster" in reasons:
        return "local_only_voice_cluster_weak", reasons
    if accepted_negative >= 3 and any(reason in reasons for reason in (
        "remote_too_close_to_local_centroid",
        "low_positive_vs_remote_margin",
        "unsafe_low_tail_margin",
    )):
        return "local_only_enrollment_remote_ambiguous", reasons
    if accepted_negative < 3:
        return "local_only_enrollment_needs_remote_negative_probe", reasons
    return "local_only_enrollment_probe_ready", reasons


def session_report(
    session: Path,
    tm: Any,
    backend: Any,
    backend_status: dict[str, Any],
    args: argparse.Namespace,
    blocked_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    out_dir = session / "derived/audit/live-local-only-enrollment-probe"
    states = read_jsonl(session / "derived/preprocess/echo/speaker_state.jsonl")
    sources = tm.source_audio(session)
    source_name, mic_source = tm.best_enrollment_source(session)
    remote_source = sources["remote"]
    positive_candidates = state_candidates(
        states,
        state_name="local_only",
        min_duration=args.min_segment_sec,
        max_duration=args.max_segment_sec,
        min_confidence=args.min_local_confidence,
        min_db_key="mic_db",
        min_db=args.min_mic_db,
        limit=args.max_positive_segments,
    )
    negative_candidates = state_candidates(
        states,
        state_name="remote_only",
        min_duration=args.min_segment_sec,
        max_duration=args.max_segment_sec,
        min_confidence=args.min_remote_confidence,
        min_db_key="remote_db",
        min_db=args.min_remote_db,
        limit=args.max_negative_segments,
    )
    positives, positive_embeddings = embed_candidates(
        session=session,
        source=mic_source,
        source_name=source_name,
        candidates=positive_candidates,
        out_dir=out_dir,
        prefix="positive_local_only",
        backend=backend,
        tm=tm,
        args=args,
    )
    negatives, negative_embeddings = embed_candidates(
        session=session,
        source=remote_source,
        source_name="remote",
        candidates=negative_candidates,
        out_dir=out_dir,
        prefix="negative_remote_only",
        backend=backend,
        tm=tm,
        args=args,
    )
    model = build_target_model(tm, positive_embeddings, negative_embeddings)
    scores = score_embeddings(tm, positive_embeddings, negative_embeddings, model)
    accepted_positive = sum(1 for item in positives if item.get("accepted"))
    accepted_negative = sum(1 for item in negatives if item.get("accepted"))
    classification, reasons = classify_probe(accepted_positive, accepted_negative, scores)
    thresholds = target_thresholds(scores)
    blocked_mixed_evaluations = evaluate_blocked_mixed_rows(
        session=session,
        rows=blocked_rows,
        source=mic_source,
        source_name=source_name,
        state_rows=states,
        model=model,
        thresholds=thresholds,
        out_dir=out_dir,
        backend=backend,
        tm=tm,
        args=args,
    )
    supported = [
        row
        for row in blocked_mixed_evaluations
        if row.get("classification") == "local_only_seed_supports_blocked_mixed_row"
    ]
    live_segment_evaluations = evaluate_live_segments(
        session=session,
        rows=live_suppressed_mic_segments(session, args.max_live_segments),
        state_rows=states,
        model=model,
        thresholds=thresholds,
        out_dir=out_dir,
        backend=backend,
        tm=tm,
        args=args,
    )
    supported_live_segments = [
        row
        for row in live_segment_evaluations
        if row.get("classification") == "local_only_seed_supports_live_segment"
    ]
    causal_live_segment_evaluations = evaluate_live_segments(
        session=session,
        rows=live_suppressed_mic_segments(session, args.max_live_segments),
        state_rows=states,
        model=model,
        thresholds=thresholds,
        out_dir=out_dir / "causal",
        backend=backend,
        tm=tm,
        args=args,
        causal_positive_seeds=accepted_embedding_pairs(positives, positive_embeddings),
        causal_negative_seeds=accepted_embedding_pairs(negatives, negative_embeddings),
    )
    supported_causal_live_segments = [
        row
        for row in causal_live_segment_evaluations
        if row.get("classification") == "causal_local_only_seed_supports_live_segment"
    ]
    report = {
        "schema": SCHEMA,
        "generator": {"name": "report-live-local-only-enrollment-probe", "version": SCRIPT_VERSION},
        "created_at": now_iso(),
        "session": session.name,
        "path": str(session),
        "promotion_allowed": False,
        "interpretation": (
            "diagnostic only: tests whether high-confidence same-session local-only mic intervals "
            "can seed a causal Target-Me enrollment probe. It does not publish live text."
        ),
        "status": classification,
        "reasons": reasons,
        "embedding_backend": backend_status,
        "source_audio": {
            "positive": {"name": source_name, "path": str(mic_source), "exists": mic_source.exists()},
            "negative": {"name": "remote", "path": str(remote_source), "exists": remote_source.exists()},
        },
        "config": {
            "min_segment_sec": args.min_segment_sec,
            "max_segment_sec": args.max_segment_sec,
            "min_local_confidence": args.min_local_confidence,
            "min_remote_confidence": args.min_remote_confidence,
            "min_mic_db": args.min_mic_db,
            "min_remote_db": args.min_remote_db,
            "max_positive_segments": args.max_positive_segments,
            "max_negative_segments": args.max_negative_segments,
            "max_live_segments": args.max_live_segments,
        },
        "positive_candidate_count": len(positive_candidates),
        "positive_accepted_count": accepted_positive,
        "positive_accepted_seconds": round(sum(safe_float(item.get("duration_sec")) for item in positives if item.get("accepted")), 3),
        "negative_candidate_count": len(negative_candidates),
        "negative_accepted_count": accepted_negative,
        "negative_accepted_seconds": round(sum(safe_float(item.get("duration_sec")) for item in negatives if item.get("accepted")), 3),
        "scores": scores,
        "thresholds": thresholds,
        "blocked_mixed_row_count": len(blocked_mixed_evaluations),
        "blocked_mixed_supported_count": len(supported),
        "blocked_mixed_supported_seconds": round(
            sum(safe_float(row.get("duration_sec")) for row in supported),
            3,
        ),
        "blocked_mixed_evaluations": blocked_mixed_evaluations,
        "live_segment_evaluated_count": len(live_segment_evaluations),
        "live_segment_supported_count": len(supported_live_segments),
        "live_segment_supported_seconds": round(
            sum(safe_float(row.get("duration_sec")) for row in supported_live_segments),
            3,
        ),
        "live_segment_evaluations": live_segment_evaluations,
        "causal_live_segment_evaluated_count": len(causal_live_segment_evaluations),
        "causal_live_segment_supported_count": len(supported_causal_live_segments),
        "causal_live_segment_supported_seconds": round(
            sum(safe_float(row.get("duration_sec")) for row in supported_causal_live_segments),
            3,
        ),
        "causal_live_segment_evaluations": causal_live_segment_evaluations,
        "positive_segments": positives,
        "negative_segments": negatives,
    }
    write_json(out_dir / "live_local_only_enrollment_probe_summary.json", report)
    return report


def corpus_summary(session_reports: list[dict[str, Any]], backend_status: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    by_status: dict[str, dict[str, Any]] = {}
    supported_seconds = 0.0
    supported_count = 0
    evaluated_count = 0
    live_segment_supported_count = 0
    live_segment_supported_seconds = 0.0
    causal_live_segment_supported_count = 0
    causal_live_segment_supported_seconds = 0.0
    for report in session_reports:
        status = str(report.get("status") or "unknown")
        payload = by_status.setdefault(status, {"count": 0, "positive_seconds": 0.0})
        payload["count"] += 1
        payload["positive_seconds"] = round(
            safe_float(payload.get("positive_seconds")) + safe_float(report.get("positive_accepted_seconds")),
            3,
        )
        supported_count += safe_int(report.get("blocked_mixed_supported_count"))
        supported_seconds += safe_float(report.get("blocked_mixed_supported_seconds"))
        evaluated_count += safe_int(report.get("blocked_mixed_row_count"))
        live_segment_supported_count += safe_int(report.get("live_segment_supported_count"))
        live_segment_supported_seconds += safe_float(report.get("live_segment_supported_seconds"))
        causal_live_segment_supported_count += safe_int(report.get("causal_live_segment_supported_count"))
        causal_live_segment_supported_seconds += safe_float(report.get("causal_live_segment_supported_seconds"))
    ready = [report for report in session_reports if report.get("status") == "local_only_enrollment_probe_ready"]
    ambiguous = [report for report in session_reports if "ambiguous" in str(report.get("status") or "")]
    if causal_live_segment_supported_seconds > 0:
        recommended_next = {
            "id": "materialize_causal_local_only_seed_live_segment_shadow",
            "why": (
                "past-only same-session enrollment supports closed live mic segments; materialize "
                "causal micro-ASR candidates and run parity gates"
            ),
            "supported_seconds": round(causal_live_segment_supported_seconds, 3),
        }
    elif supported_seconds > 0:
        recommended_next = {
            "id": "materialize_local_only_seed_mixed_row_shadow",
            "why": "local-only seed models support some blocked mixed rows; materialize a diagnostic shadow and run parity gates",
            "supported_seconds": round(supported_seconds, 3),
        }
    elif ready:
        recommended_next = {
            "id": "tighten_or_recalibrate_local_only_seed_mixed_row_scoring",
            "why": "same-session local-only seeds are ready, but they do not support the blocked mixed rows under current thresholds",
            "ready_sessions": len(ready),
        }
    elif ambiguous:
        recommended_next = {
            "id": "improve_local_only_enrollment_remote_guard",
            "why": "local-only candidates exist, but remote similarity is too close for safe use",
            "ambiguous_sessions": len(ambiguous),
        }
    else:
        recommended_next = {
            "id": "collect_or_identify_more_local_only_target_speaker_audio",
            "why": "the local-only probe did not find a safe same-session enrollment seed",
        }
    return {
        "schema": "murmurmark.live_local_only_enrollment_probe_corpus/v1",
        "generator": {"name": "report-live-local-only-enrollment-probe", "version": SCRIPT_VERSION},
        "created_at": now_iso(),
        "promotion_allowed": False,
        "embedding_backend": backend_status,
        "session_count": len(session_reports),
        "by_status": by_status,
        "blocked_mixed_evaluated_count": evaluated_count,
        "blocked_mixed_supported_count": supported_count,
        "blocked_mixed_supported_seconds": round(supported_seconds, 3),
        "live_segment_supported_count": live_segment_supported_count,
        "live_segment_supported_seconds": round(live_segment_supported_seconds, 3),
        "causal_live_segment_supported_count": causal_live_segment_supported_count,
        "causal_live_segment_supported_seconds": round(causal_live_segment_supported_seconds, 3),
        "recommended_next": recommended_next,
        "sessions": [
            {
                "session": report.get("session"),
                "status": report.get("status"),
                "reasons": report.get("reasons"),
                "positive_accepted_count": report.get("positive_accepted_count"),
                "positive_accepted_seconds": report.get("positive_accepted_seconds"),
                "negative_accepted_count": report.get("negative_accepted_count"),
                "negative_accepted_seconds": report.get("negative_accepted_seconds"),
                "blocked_mixed_row_count": report.get("blocked_mixed_row_count"),
                "blocked_mixed_supported_count": report.get("blocked_mixed_supported_count"),
                "blocked_mixed_supported_seconds": report.get("blocked_mixed_supported_seconds"),
                "live_segment_supported_count": report.get("live_segment_supported_count"),
                "live_segment_supported_seconds": report.get("live_segment_supported_seconds"),
                "causal_live_segment_supported_count": report.get("causal_live_segment_supported_count"),
                "causal_live_segment_supported_seconds": report.get("causal_live_segment_supported_seconds"),
                "scores": report.get("scores"),
                "summary_path": (
                    f"sessions/{report.get('session')}/derived/audit/live-local-only-enrollment-probe/"
                    "live_local_only_enrollment_probe_summary.json"
                ),
            }
            for report in session_reports
        ],
    }


def main() -> int:
    args = parse_args()
    tm = load_module("murmurmark_audit_target_me", "audit-target-me.py")
    backend, backend_status = tm.resolve_embedding_backend(args)
    corpus_report = read_json(args.corpus_report)
    sessions = normalize_sessions(args.sessions, corpus_report)
    blocked_by_session = blocked_rows_by_session(corpus_report)
    if not sessions:
        raise SystemExit("no sessions provided and no same-session lab examples found")
    reports = [
        session_report(
            session,
            tm,
            backend,
            backend_status,
            args,
            blocked_by_session.get(session.name, []),
        )
        for session in sessions
    ]
    summary = corpus_summary(reports, backend_status, args)
    write_json(args.out, summary)
    print(f"live_local_only_enrollment_probe: {args.out}")
    print(f"session_count: {summary['session_count']}")
    print(f"by_status: {json.dumps(summary['by_status'], ensure_ascii=False)}")
    print(f"recommended_next: {summary['recommended_next']['id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
