#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import subprocess
import warnings
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_ROW = "murmurmark.live_local_recall_target_me_audit/v1"
SCHEMA_SUMMARY = "murmurmark.live_local_recall_target_me_summary/v1"
SCHEMA_CORPUS = "murmurmark.live_local_recall_target_me_corpus_report/v1"
SCRIPT_VERSION = "0.1.0"
LOCAL_LABELS = {"me_dominant", "mixed"}
LIVE_IMPLEMENTABLE_POLICIES = {
    "current_text_segment_gate",
    "strict_text_unique_v1",
    "remote_silent_text_v1",
    "audio_remote_quiet_v1",
    "audio_mic_dominant_v1",
    "audio_low_coherence_v1",
    "audio_safe_union_v1",
}
TARGET_ME_RESCUE_POLICIES = (
    "target_me_confirmed_v1",
    "target_me_confirmed_remote_guard_v1",
    "target_me_possible_v1",
)


def load_target_me_module() -> Any:
    path = Path(__file__).with_name("audit-target-me.py")
    spec = importlib.util.spec_from_file_location("murmurmark_audit_target_me", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tm = load_target_me_module()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit live suppressed mic local-recall gaps with the local Target-Me speaker evidence backend."
    )
    parser.add_argument("sessions", nargs="+", type=Path)
    parser.add_argument("--profile", default="auto")
    parser.add_argument(
        "--method",
        default="auto",
        choices=["auto", "mfcc_voiceprint", "mfcc_contrastive", "resemblyzer_dvector", "wavlm_xvector"],
    )
    parser.add_argument("--wavlm-model", type=Path, default=None)
    parser.add_argument("--out-dir-name", default="live-local-recall-target-me")
    parser.add_argument("--corpus-out-dir", type=Path, default=Path("sessions/_reports/live-local-recall-target-me"))
    parser.add_argument("--max-items", type=int, default=80)
    parser.add_argument("--padding-sec", type=float, default=0.20)
    parser.add_argument("--min-duration-sec", type=float, default=0.5)
    parser.add_argument("--include-policy-selected", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--write-clips", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--progress", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--max-enrollment-segments", type=int, default=40)
    parser.add_argument("--max-enrollment-total-sec", type=float, default=180.0)
    parser.add_argument("--max-negative-enrollment-segments", type=int, default=40)
    parser.add_argument("--min-enrollment-sec", type=float, default=1.2)
    parser.add_argument("--max-enrollment-sec", type=float, default=14.0)
    parser.add_argument("--min-enrollment-local-ratio", type=float, default=0.65)
    parser.add_argument("--max-enrollment-remote-active-ratio", type=float, default=0.20)
    return parser.parse_args()


def normalized_sessions(values: list[Path]) -> list[Path]:
    sessions: list[Path] = []
    for value in values:
        text = str(value)
        parts = [part.strip() for part in text.splitlines() if part.strip()] if "\n" in text else [text]
        sessions.extend(Path(part) for part in parts)
    return sessions


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def progress(args: argparse.Namespace, message: str) -> None:
    if args.progress:
        print(f"live_target_me: {message}", flush=True)


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


def session_path(path: Path, value: Any) -> Path | None:
    if not value:
        return None
    candidate = Path(str(value))
    return candidate if candidate.is_absolute() else path / candidate


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")


def format_time(seconds: float) -> str:
    total = max(0, int(seconds))
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def extract_wav(source: Path, output: Path, start: float, duration: float) -> bool:
    if not source.exists() or duration <= 0:
        return False
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg",
        "-nostdin",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{max(0.0, start):.3f}",
        "-t",
        f"{duration:.3f}",
        "-i",
        str(source),
        "-map",
        "0:a:0",
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        str(output),
    ]
    try:
        subprocess.run(command, check=True)
    except (OSError, subprocess.CalledProcessError):
        return False
    return output.exists() and output.stat().st_size > 44


def live_policy_candidates(segment: dict[str, Any]) -> list[str]:
    values = segment.get("rescue_policy_candidates")
    if not isinstance(values, list):
        return []
    return [str(value) for value in values if str(value) in LIVE_IMPLEMENTABLE_POLICIES]


def target_me_rescue_policy_candidates(row: dict[str, Any]) -> list[str]:
    classification = row.get("classification") if isinstance(row.get("classification"), dict) else {}
    scores = classification.get("scores") if isinstance(classification.get("scores"), dict) else {}
    label = str(classification.get("label") or "")
    confidence = safe_float(classification.get("confidence"))
    delta_vs_remote = safe_float(scores.get("delta_vs_remote"))
    remote_active = safe_float(scores.get("state_remote_active_ratio"))
    policies: list[str] = []
    if label == "target_me_confirmed" and confidence >= 0.72:
        policies.append("target_me_confirmed_v1")
    if (
        label == "target_me_confirmed"
        and confidence >= 0.78
        and delta_vs_remote >= 0.12
        and remote_active <= 0.25
    ):
        policies.append("target_me_confirmed_remote_guard_v1")
    if label in {"target_me_confirmed", "target_me_possible"} and confidence >= 0.55:
        policies.append("target_me_possible_v1")
    return policies


def segment_priority(segment: dict[str, Any]) -> tuple[int, float, str, float]:
    label = str(segment.get("batch_role_label") or "")
    has_live_policy = bool(live_policy_candidates(segment))
    if label in LOCAL_LABELS and not has_live_policy:
        bucket = 0
    elif label in LOCAL_LABELS:
        bucket = 1
    elif has_live_policy:
        bucket = 2
    else:
        bucket = 3
    return (
        bucket,
        -safe_float(segment.get("duration_sec")),
        str(segment.get("session_id") or ""),
        safe_float(segment.get("start")),
    )


def selected_segments(comparison: dict[str, Any], args: argparse.Namespace) -> list[dict[str, Any]]:
    risk_examples = comparison.get("risk_examples") if isinstance(comparison.get("risk_examples"), dict) else {}
    rows: list[dict[str, Any]] = []
    for item in risk_examples.get("suppressed_mic_asr_segments") or []:
        if not isinstance(item, dict):
            continue
        duration = safe_float(item.get("duration_sec"))
        if duration < args.min_duration_sec:
            continue
        label = str(item.get("batch_role_label") or "")
        has_policy = bool(live_policy_candidates(item))
        if label in LOCAL_LABELS and (args.include_policy_selected or not has_policy):
            rows.append(dict(item))
        elif has_policy and label not in LOCAL_LABELS:
            rows.append(dict(item))
    rows.sort(key=segment_priority)
    return rows[: args.max_items] if args.max_items > 0 else rows


def chunk_audio_sources(session: Path, chunk_index: int) -> tuple[dict[str, Path], float]:
    chunk_path = session / "derived/live/chunks" / f"{chunk_index:06d}" / "chunk.json"
    chunk = read_json(chunk_path) or {}
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


def embed_clip(path: Path | None, backend: Any, target_model: dict[str, Any]) -> dict[str, Any]:
    if path is None:
        return {"exists": False, "target_similarity": 0.0, "positive_similarity": 0.0, "negative_similarity": 0.0}
    try:
        if not path.exists() or path.stat().st_size <= 44:
            return {
                "exists": False,
                "path": str(path),
                "error": "empty_or_missing_clip",
                "target_similarity": 0.0,
                "positive_similarity": 0.0,
                "negative_similarity": 0.0,
            }
    except OSError as error:
        return {
            "exists": False,
            "path": str(path),
            "error": str(error),
            "target_similarity": 0.0,
            "positive_similarity": 0.0,
            "negative_similarity": 0.0,
        }
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        embedding, info = tm.embedding_for_clip(path, backend)
    scores = tm.model_score(embedding, target_model)
    payload = dict(info)
    payload["path"] = str(path)
    payload["target_similarity"] = round(scores["target_similarity"], 6)
    payload["positive_similarity"] = round(scores["positive_similarity"], 6)
    payload["negative_similarity"] = round(scores["negative_similarity"], 6)
    return payload


def audit_segment(
    *,
    session: Path,
    segment: dict[str, Any],
    index: int,
    target_model: dict[str, Any],
    calibration: dict[str, Any],
    backend: Any,
    state_rows: list[dict[str, Any]],
    out_dir: Path,
    args: argparse.Namespace,
) -> dict[str, Any]:
    start = safe_float(segment.get("start"))
    end = safe_float(segment.get("end"), start)
    duration = max(0.0, end - start)
    chunk_index = safe_int(segment.get("chunk_index"))
    source_audio, clip_start = chunk_audio_sources(session, chunk_index)
    clip_start_local = max(0.0, start - clip_start - args.padding_sec)
    clip_duration = duration + 2 * args.padding_sec
    clips_dir = out_dir / "clips"
    item_id = f"lltme_{index:06d}"
    clips: dict[str, str] = {}
    for source_name, source in source_audio.items():
        clip = clips_dir / f"{item_id}_{source_name}.wav"
        if args.write_clips and extract_wav(source, clip, clip_start_local, clip_duration):
            clips[source_name] = str(clip)

    source_scores = {
        source: embed_clip(Path(path), backend, target_model)
        for source, path in clips.items()
    }
    for source in ("mic_role_masked", "mic_clean", "mic_raw", "remote"):
        source_scores.setdefault(
            source,
            {"exists": False, "target_similarity": 0.0, "positive_similarity": 0.0, "negative_similarity": 0.0},
        )
    state = tm.interval_state_features(state_rows, start, end)
    item = {
        "id": item_id,
        "utterances": [
            {
                "id": f"{item_id}_live_suppressed_mic",
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
    row = {
        "schema": SCHEMA_ROW,
        "id": item_id,
        "session_id": session.name,
        "chunk_index": chunk_index,
        "interval": {
            "start": round(start, 3),
            "end": round(end, 3),
            "duration_sec": round(duration, 3),
            "start_time": format_time(start),
            "end_time": format_time(end),
        },
        "batch_role_label": segment.get("batch_role_label"),
        "live_rescue_policy_candidates": live_policy_candidates(segment),
        "text": segment.get("text") or "",
        "segment_features": {
            "token_count": segment.get("token_count"),
            "unique_token_count": segment.get("segment_gate_unique_token_count"),
            "mic_token_recall_in_overlapping_remote": segment.get(
                "segment_gate_mic_token_recall_in_overlapping_remote"
            ),
            "overlapping_remote_token_recall_in_mic": segment.get(
                "segment_gate_overlapping_remote_token_recall_in_mic"
            ),
            "audio_mic_clean_rms_db": segment.get("audio_mic_clean_rms_db"),
            "audio_remote_rms_db": segment.get("audio_remote_rms_db"),
            "audio_mic_remote_zero_lag_abs_corr": segment.get("audio_mic_remote_zero_lag_abs_corr"),
        },
        "state": state,
        "source_scores": source_scores,
        "classification": classification,
        "clips": clips,
    }
    row["target_me_rescue_policy_candidates"] = target_me_rescue_policy_candidates(row)
    return row


def interval_overlap(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def overlap_covered_seconds(intervals: list[tuple[float, float]], start: float, end: float) -> float:
    clipped = sorted(
        (max(start, left), min(end, right))
        for left, right in intervals
        if interval_overlap(left, right, start, end) > 0
    )
    if not clipped:
        return 0.0
    total = 0.0
    current_left, current_right = clipped[0]
    for left, right in clipped[1:]:
        if left <= current_right:
            current_right = max(current_right, right)
        else:
            total += max(0.0, current_right - current_left)
            current_left, current_right = left, right
    total += max(0.0, current_right - current_left)
    return min(max(0.0, end - start), total)


def missing_me_rows(comparison: dict[str, Any] | None) -> list[dict[str, Any]]:
    risk_examples = comparison.get("risk_examples") if isinstance(comparison, dict) else {}
    rows = risk_examples.get("local_missing") if isinstance(risk_examples, dict) else []
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def target_me_policy_metrics(
    rows: list[dict[str, Any]],
    comparison: dict[str, Any] | None = None,
) -> dict[str, Any]:
    def duration(row: dict[str, Any]) -> float:
        return safe_float((row.get("interval") or {}).get("duration_sec"))

    def is_local(row: dict[str, Any]) -> bool:
        return row.get("batch_role_label") in LOCAL_LABELS

    local_seconds = sum(duration(row) for row in rows if is_local(row))
    missing_rows = missing_me_rows(comparison)
    baseline_missing_seconds = sum(safe_float(row.get("duration_sec")) for row in missing_rows)
    metrics: dict[str, Any] = {}
    for policy in TARGET_ME_RESCUE_POLICIES:
        selected = [row for row in rows if policy in (row.get("target_me_rescue_policy_candidates") or [])]
        selected_seconds = sum(duration(row) for row in selected)
        selected_local_seconds = sum(duration(row) for row in selected if is_local(row))
        remote_risk_seconds = selected_seconds - selected_local_seconds
        selected_intervals = [
            (safe_float((row.get("interval") or {}).get("start")), safe_float((row.get("interval") or {}).get("end")))
            for row in selected
        ]
        missing_recovered = sum(
            overlap_covered_seconds(
                selected_intervals,
                safe_float(row.get("start")),
                safe_float(row.get("end"), safe_float(row.get("start"))),
            )
            for row in missing_rows
        )
        metrics[policy] = {
            "selected_count": len(selected),
            "selected_seconds": round(selected_seconds, 3),
            "local_seconds": round(selected_local_seconds, 3),
            "remote_risk_seconds": round(remote_risk_seconds, 3),
            "precision_proxy": round(selected_local_seconds / selected_seconds, 6) if selected_seconds > 0 else None,
            "audited_local_recall_proxy": (
                round(selected_local_seconds / local_seconds, 6) if local_seconds > 0 else None
            ),
            "missing_me_recovered_seconds": round(min(baseline_missing_seconds, missing_recovered), 3),
            "missing_me_seconds_after": round(max(0.0, baseline_missing_seconds - missing_recovered), 3),
        }
    return metrics


def summarize_session(
    *,
    session: Path,
    profile: str,
    out_dir: Path,
    enrollment: dict[str, Any],
    rows: list[dict[str, Any]],
    comparison: dict[str, Any] | None = None,
) -> dict[str, Any]:
    by_label = Counter(str(row.get("batch_role_label") or "unknown") for row in rows)
    by_class = Counter(str((row.get("classification") or {}).get("label") or "unknown") for row in rows)
    local_rows = [row for row in rows if row.get("batch_role_label") in LOCAL_LABELS]
    remote_rows = [row for row in rows if row.get("batch_role_label") not in LOCAL_LABELS]
    confirmed_local = [
        row for row in local_rows
        if (row.get("classification") or {}).get("label") in {"target_me_confirmed", "target_me_possible"}
    ]
    confirmed_strong_local = [
        row for row in local_rows
        if (row.get("classification") or {}).get("label") == "target_me_confirmed"
    ]
    rejected_remote = [
        row for row in remote_rows
        if (row.get("classification") or {}).get("label") in {"target_me_absent", "target_me_absent_remote_like"}
    ]

    def seconds(items: list[dict[str, Any]]) -> float:
        return round(sum(safe_float((row.get("interval") or {}).get("duration_sec")) for row in items), 3)

    return {
        "schema": SCHEMA_SUMMARY,
        "generator": {"name": "audit-live-local-recall-target-me", "version": SCRIPT_VERSION},
        "created_at": now_iso(),
        "session": str(session),
        "session_id": session.name,
        "profile": profile,
        "method": enrollment.get("method"),
        "status": "ok" if rows else "no_items",
        "items": len(rows),
        "total_seconds": seconds(rows),
        "by_batch_role_label": dict(by_label),
        "by_target_me_label": dict(by_class),
        "local_items": len(local_rows),
        "local_seconds": seconds(local_rows),
        "target_me_confirmed_local_items": len(confirmed_strong_local),
        "target_me_confirmed_local_seconds": seconds(confirmed_strong_local),
        "target_me_possible_or_confirmed_local_items": len(confirmed_local),
        "target_me_possible_or_confirmed_local_seconds": seconds(confirmed_local),
        "remote_risk_items": len(remote_rows),
        "remote_risk_seconds": seconds(remote_rows),
        "target_me_rejected_remote_risk_items": len(rejected_remote),
        "target_me_rejected_remote_risk_seconds": seconds(rejected_remote),
        "target_me_rescue_policy_metrics": target_me_policy_metrics(rows, comparison),
        "promotion_decision": "shadow_only_do_not_promote",
        "outputs": {
            "audit": str(out_dir / "live_local_recall_target_me_audit.jsonl"),
            "summary": str(out_dir / "live_local_recall_target_me_summary.json"),
            "report": str(out_dir / "live_local_recall_target_me_report.md"),
        },
    }


def write_session_report(path: Path, summary: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    lines = [
        "# Live Local Recall Target-Me Audit",
        "",
        f"- Session: `{summary.get('session_id')}`",
        f"- Status: `{summary.get('status')}`",
        f"- Method: `{summary.get('method')}`",
        f"- Items: `{summary.get('items')}` / `{summary.get('total_seconds')}` sec",
        f"- Local confirmed: `{summary.get('target_me_confirmed_local_items')}` / "
        f"`{summary.get('target_me_confirmed_local_seconds')}` sec",
        f"- Local possible or confirmed: `{summary.get('target_me_possible_or_confirmed_local_items')}` / "
        f"`{summary.get('target_me_possible_or_confirmed_local_seconds')}` sec",
        f"- Remote-risk rejected: `{summary.get('target_me_rejected_remote_risk_items')}` / "
        f"`{summary.get('target_me_rejected_remote_risk_seconds')}` sec",
        "",
        "This report is diagnostic only. Batch remains authoritative and live output is not promoted.",
        "",
    ]
    policy_metrics = (
        summary.get("target_me_rescue_policy_metrics")
        if isinstance(summary.get("target_me_rescue_policy_metrics"), dict)
        else {}
    )
    if policy_metrics:
        lines += [
            "## Shadow Rescue Policy Metrics",
            "",
            "| Policy | Selected sec | Local sec | Remote-risk sec | Precision proxy | Audited local recall proxy | Missing-Me recovered sec |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
        for policy in TARGET_ME_RESCUE_POLICIES:
            metrics = policy_metrics.get(policy) if isinstance(policy_metrics.get(policy), dict) else {}
            lines.append(
                f"| `{policy}` | {safe_float(metrics.get('selected_seconds')):.2f} "
                f"| {safe_float(metrics.get('local_seconds')):.2f} "
                f"| {safe_float(metrics.get('remote_risk_seconds')):.2f} "
                f"| {metrics.get('precision_proxy')} "
                f"| {metrics.get('audited_local_recall_proxy')} "
                f"| {safe_float(metrics.get('missing_me_recovered_seconds')):.2f} |"
            )
        lines.append("")
    if rows:
        lines += [
            "| Time | Duration | Batch label | Target-Me label | Confidence | Text |",
            "| ---: | ---: | --- | --- | ---: | --- |",
        ]
        ranked = sorted(
            rows,
            key=lambda row: (
                row.get("batch_role_label") not in LOCAL_LABELS,
                -safe_float((row.get("interval") or {}).get("duration_sec")),
                safe_float((row.get("interval") or {}).get("start")),
            ),
        )
        for row in ranked[:40]:
            interval = row.get("interval") if isinstance(row.get("interval"), dict) else {}
            classification = row.get("classification") if isinstance(row.get("classification"), dict) else {}
            text = str(row.get("text") or "").replace("|", "\\|")
            lines.append(
                f"| {interval.get('start_time')} | {safe_float(interval.get('duration_sec')):.2f} "
                f"| `{row.get('batch_role_label')}` | `{classification.get('label')}` "
                f"| {safe_float(classification.get('confidence')):.2f} | {text[:160]} |"
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def audit_session(session: Path, args: argparse.Namespace) -> dict[str, Any]:
    profile = tm.resolve_profile(session, args.profile)
    out_dir = session / "derived/audit" / args.out_dir_name
    out_dir.mkdir(parents=True, exist_ok=True)
    dialogue = tm.read_json(tm.clean_dialogue_path(session, profile))
    comparison = read_json(session / "derived/live/live_batch_comparison.json")
    if not dialogue or not isinstance(dialogue.get("utterances"), list) or not comparison:
        summary = {
            "schema": SCHEMA_SUMMARY,
            "generator": {"name": "audit-live-local-recall-target-me", "version": SCRIPT_VERSION},
            "created_at": now_iso(),
            "session": str(session),
            "session_id": session.name,
            "profile": profile,
            "status": "missing_inputs",
            "items": 0,
            "total_seconds": 0.0,
            "promotion_decision": "shadow_only_do_not_promote",
        }
        write_jsonl(out_dir / "live_local_recall_target_me_audit.jsonl", [])
        write_json(out_dir / "live_local_recall_target_me_summary.json", summary)
        write_session_report(out_dir / "live_local_recall_target_me_report.md", summary, [])
        return summary

    backend, backend_status = tm.resolve_embedding_backend(args)
    missing_embedding_backend = (
        args.method == "wavlm_xvector"
        and backend_status.get("wavlm_ready") is not True
    ) or (
        args.method == "resemblyzer_dvector"
        and backend_status.get("resemblyzer_ready") is False
    )
    if missing_embedding_backend:
        summary = {
            "schema": SCHEMA_SUMMARY,
            "generator": {"name": "audit-live-local-recall-target-me", "version": SCRIPT_VERSION},
            "created_at": now_iso(),
            "session": str(session),
            "session_id": session.name,
            "profile": profile,
            "method": backend.method,
            "embedding_backend": backend_status,
            "status": "missing_embedding_model",
            "items": 0,
            "total_seconds": 0.0,
            "promotion_decision": "shadow_only_do_not_promote",
        }
        write_jsonl(out_dir / "live_local_recall_target_me_audit.jsonl", [])
        write_json(out_dir / "live_local_recall_target_me_summary.json", summary)
        write_session_report(out_dir / "live_local_recall_target_me_report.md", summary, [])
        return summary

    state_rows = tm.load_speaker_state(session)
    progress(args, f"{session.name}: build Target-Me enrollment")
    enrollment, target_model = tm.build_enrollment(
        session,
        profile,
        dialogue["utterances"],
        state_rows,
        out_dir,
        backend,
        backend_status,
        args,
    )
    enrollment["method"] = str(backend_status.get("selected") or backend.method)
    if enrollment.get("status") != "ready":
        summary = summarize_session(session=session, profile=profile, out_dir=out_dir, enrollment=enrollment, rows=[])
        summary["status"] = str(enrollment.get("status") or "enrollment_not_ready")
        write_jsonl(out_dir / "live_local_recall_target_me_audit.jsonl", [])
        write_json(out_dir / "live_local_recall_target_me_summary.json", summary)
        write_session_report(out_dir / "live_local_recall_target_me_report.md", summary, [])
        return summary

    rows: list[dict[str, Any]] = []
    segments = selected_segments(comparison, args)
    for index, segment in enumerate(segments, start=1):
        progress(args, f"{session.name}: audit {index}/{len(segments)} chunk={segment.get('chunk_index')}")
        rows.append(
            audit_segment(
                session=session,
                segment=segment,
                index=index,
                target_model=target_model,
                calibration=enrollment.get("calibration") if isinstance(enrollment.get("calibration"), dict) else {},
                backend=backend,
                state_rows=state_rows,
                out_dir=out_dir,
                args=args,
            )
        )
    summary = summarize_session(
        session=session,
        profile=profile,
        out_dir=out_dir,
        enrollment=enrollment,
        rows=rows,
        comparison=comparison,
    )
    summary["embedding_backend"] = backend_status
    write_jsonl(out_dir / "live_local_recall_target_me_audit.jsonl", rows)
    write_json(out_dir / "live_local_recall_target_me_summary.json", summary)
    write_session_report(out_dir / "live_local_recall_target_me_report.md", summary, rows)
    return summary


def write_corpus_report(path: Path, summaries: list[dict[str, Any]]) -> dict[str, Any]:
    def seconds(key: str) -> float:
        return round(sum(safe_float(row.get(key)) for row in summaries), 3)

    policy_metrics: dict[str, dict[str, Any]] = {}
    total_local_seconds = seconds("local_seconds")
    for policy in TARGET_ME_RESCUE_POLICIES:
        selected_seconds = 0.0
        local_seconds = 0.0
        remote_risk_seconds = 0.0
        missing_after_seconds = 0.0
        missing_recovered_seconds = 0.0
        selected_count = 0
        for row in summaries:
            metrics_root = row.get("target_me_rescue_policy_metrics")
            metrics = metrics_root.get(policy) if isinstance(metrics_root, dict) else None
            if not isinstance(metrics, dict):
                continue
            selected_count += safe_int(metrics.get("selected_count"))
            selected_seconds += safe_float(metrics.get("selected_seconds"))
            local_seconds += safe_float(metrics.get("local_seconds"))
            remote_risk_seconds += safe_float(metrics.get("remote_risk_seconds"))
            missing_after_seconds += safe_float(metrics.get("missing_me_seconds_after"))
            missing_recovered_seconds += safe_float(metrics.get("missing_me_recovered_seconds"))
        policy_metrics[policy] = {
            "selected_count": selected_count,
            "selected_seconds": round(selected_seconds, 3),
            "local_seconds": round(local_seconds, 3),
            "remote_risk_seconds": round(remote_risk_seconds, 3),
            "precision_proxy": round(local_seconds / selected_seconds, 6) if selected_seconds > 0 else None,
            "audited_local_recall_proxy": (
                round(local_seconds / total_local_seconds, 6) if total_local_seconds > 0 else None
            ),
            "missing_me_recovered_seconds": round(missing_recovered_seconds, 3),
            "missing_me_seconds_after": round(missing_after_seconds, 3),
        }

    report = {
        "schema": SCHEMA_CORPUS,
        "generator": {"name": "audit-live-local-recall-target-me", "version": SCRIPT_VERSION},
        "created_at": now_iso(),
        "sessions": len(summaries),
        "by_status": dict(Counter(str(row.get("status") or "unknown") for row in summaries)),
        "total_items": sum(safe_int(row.get("items")) for row in summaries),
        "total_seconds": seconds("total_seconds"),
        "local_seconds": seconds("local_seconds"),
        "target_me_confirmed_local_seconds": seconds("target_me_confirmed_local_seconds"),
        "target_me_possible_or_confirmed_local_seconds": seconds("target_me_possible_or_confirmed_local_seconds"),
        "remote_risk_seconds": seconds("remote_risk_seconds"),
        "target_me_rejected_remote_risk_seconds": seconds("target_me_rejected_remote_risk_seconds"),
        "target_me_rescue_policy_metrics": policy_metrics,
        "promotion_decision": "shadow_only_do_not_promote",
        "session_summaries": summaries,
    }
    write_json(path / "live_local_recall_target_me_corpus_report.json", report)
    lines = [
        "# Live Local Recall Target-Me Corpus",
        "",
        f"- Sessions: `{report['sessions']}`",
        f"- Total items: `{report['total_items']}` / `{report['total_seconds']}` sec",
        f"- Local seconds: `{report['local_seconds']}`",
        f"- Target-Me confirmed local seconds: `{report['target_me_confirmed_local_seconds']}`",
        f"- Target-Me possible or confirmed local seconds: `{report['target_me_possible_or_confirmed_local_seconds']}`",
        f"- Remote-risk seconds: `{report['remote_risk_seconds']}`",
        f"- Target-Me rejected remote-risk seconds: `{report['target_me_rejected_remote_risk_seconds']}`",
        "",
        "Batch remains authoritative; this is shadow evidence only.",
        "",
        "## Shadow Rescue Policy Metrics",
        "",
        "| Policy | Selected sec | Local sec | Remote-risk sec | Precision proxy | Audited local recall proxy | Missing-Me recovered sec |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for policy in TARGET_ME_RESCUE_POLICIES:
        metrics = policy_metrics.get(policy) or {}
        lines.append(
            f"| `{policy}` | {safe_float(metrics.get('selected_seconds')):.2f} "
            f"| {safe_float(metrics.get('local_seconds')):.2f} "
            f"| {safe_float(metrics.get('remote_risk_seconds')):.2f} "
            f"| {metrics.get('precision_proxy')} "
            f"| {metrics.get('audited_local_recall_proxy')} "
            f"| {safe_float(metrics.get('missing_me_recovered_seconds')):.2f} |"
        )
    lines += [
        "",
        "| Session | Status | Items | Local sec | Confirmed local sec | Possible/confirmed local sec | Remote-risk rejected sec |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in summaries:
        lines.append(
            f"| `{row.get('session_id')}` | `{row.get('status')}` | {safe_int(row.get('items'))} "
            f"| {safe_float(row.get('local_seconds')):.2f} "
            f"| {safe_float(row.get('target_me_confirmed_local_seconds')):.2f} "
            f"| {safe_float(row.get('target_me_possible_or_confirmed_local_seconds')):.2f} "
            f"| {safe_float(row.get('target_me_rejected_remote_risk_seconds')):.2f} |"
        )
    (path / "live_local_recall_target_me_corpus_report.md").write_text(
        "\n".join(lines).rstrip() + "\n",
        encoding="utf-8",
    )
    return report


def main() -> int:
    args = parse_args()
    summaries = [audit_session(session, args) for session in normalized_sessions(args.sessions)]
    args.corpus_out_dir.mkdir(parents=True, exist_ok=True)
    corpus = write_corpus_report(args.corpus_out_dir, summaries)
    print(f"live_local_recall_target_me_corpus: {args.corpus_out_dir / 'live_local_recall_target_me_corpus_report.json'}")
    print(f"sessions: {corpus['sessions']}")
    print(f"local_seconds: {corpus['local_seconds']}")
    print(f"target_me_confirmed_local_seconds: {corpus['target_me_confirmed_local_seconds']}")
    print(f"target_me_possible_or_confirmed_local_seconds: {corpus['target_me_possible_or_confirmed_local_seconds']}")
    print(f"remote_risk_seconds: {corpus['remote_risk_seconds']}")
    print(f"target_me_rejected_remote_risk_seconds: {corpus['target_me_rejected_remote_risk_seconds']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
