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
SCRIPT_VERSION = "0.1.0"
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


def score_embeddings(tm: Any, positives: list[np.ndarray], negatives: list[np.ndarray]) -> dict[str, Any]:
    positive_centroid = tm.make_centroid(positives)
    negative_centroid = tm.make_centroid(negatives)
    model = {
        "positive_centroid": positive_centroid,
        "negative_centroid": negative_centroid,
        "scoring": "contrastive" if negative_centroid is not None else "cosine",
    }
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


def session_report(session: Path, tm: Any, backend: Any, backend_status: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
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
    scores = score_embeddings(tm, positive_embeddings, negative_embeddings)
    accepted_positive = sum(1 for item in positives if item.get("accepted"))
    accepted_negative = sum(1 for item in negatives if item.get("accepted"))
    classification, reasons = classify_probe(accepted_positive, accepted_negative, scores)
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
        },
        "positive_candidate_count": len(positive_candidates),
        "positive_accepted_count": accepted_positive,
        "positive_accepted_seconds": round(sum(safe_float(item.get("duration_sec")) for item in positives if item.get("accepted")), 3),
        "negative_candidate_count": len(negative_candidates),
        "negative_accepted_count": accepted_negative,
        "negative_accepted_seconds": round(sum(safe_float(item.get("duration_sec")) for item in negatives if item.get("accepted")), 3),
        "scores": scores,
        "positive_segments": positives,
        "negative_segments": negatives,
    }
    write_json(out_dir / "live_local_only_enrollment_probe_summary.json", report)
    return report


def corpus_summary(session_reports: list[dict[str, Any]], backend_status: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    by_status: dict[str, dict[str, Any]] = {}
    for report in session_reports:
        status = str(report.get("status") or "unknown")
        payload = by_status.setdefault(status, {"count": 0, "positive_seconds": 0.0})
        payload["count"] += 1
        payload["positive_seconds"] = round(
            safe_float(payload.get("positive_seconds")) + safe_float(report.get("positive_accepted_seconds")),
            3,
        )
    ready = [report for report in session_reports if report.get("status") == "local_only_enrollment_probe_ready"]
    ambiguous = [report for report in session_reports if "ambiguous" in str(report.get("status") or "")]
    if ready:
        recommended_next = {
            "id": "evaluate_local_only_enrollment_probe_against_blocked_mixed_rows",
            "why": "some sessions have enough local-only voice evidence to test blocked mixed intervals",
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
    if not sessions:
        raise SystemExit("no sessions provided and no same-session lab examples found")
    reports = [session_report(session, tm, backend, backend_status, args) for session in sessions]
    summary = corpus_summary(reports, backend_status, args)
    write_json(args.out, summary)
    print(f"live_local_only_enrollment_probe: {args.out}")
    print(f"session_count: {summary['session_count']}")
    print(f"by_status: {json.dumps(summary['by_status'], ensure_ascii=False)}")
    print(f"recommended_next: {summary['recommended_next']['id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
