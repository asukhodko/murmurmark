#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import importlib.util
import json
import os
import re
import signal as process_signal
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from scipy import linalg, signal
from scipy.io import wavfile


SCHEMA = "murmurmark.live_pipeline_report/v1"
SCRIPT_VERSION = "0.8.2"
EPSILON = 1.0e-12
LIVE_ROLE_DUPLICATE_THRESHOLD = 0.55
LIVE_RESCUE_SHADOW_POLICY = "audio_safe_union_v1"
LIVE_PREVIEW_POLICY = "live_runtime_causal_target_me_remote_energy_v1"
KNOWN_HALLUCINATIONS = {
    "редактор субтитров",
    "продолжение следует",
    "спасибо за просмотр",
}
HALLUCINATION_SUFFIX_MARKERS = (
    "редактор субтитров",
    "субтитры подготовлены",
    "субтитры сделал",
    "субтитры создал",
    "субтитры создавал",
    "спасибо за просмотр",
)
CORRECTOR_CREDIT_RE = re.compile(r"\bкорректор\s+[A-Za-zА-Яа-яЁё]\.\s*[A-Za-zА-Яа-яЁё-]+.*$", re.IGNORECASE)
TOKEN_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9_+-]+")
SHUTDOWN_REQUESTED = False
ACTIVE_CHILDREN: dict[int, subprocess.Popen[str]] = {}
ACTIVE_CHILDREN_LOCK = threading.Lock()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Shadow near-realtime worker for closed MurmurMark live segments.")
    parser.add_argument("session", type=Path)
    parser.add_argument("--model", type=Path, default=Path.home() / ".local/share/murmurmark/models/whisper.cpp/ggml-large-v3-q5_0.bin")
    parser.add_argument("--language", default="ru")
    parser.add_argument("--whisper-cli", default=os.environ.get("WHISPER_CLI", "whisper-cli"))
    parser.add_argument("--poll-sec", type=float, default=2.0)
    parser.add_argument("--idle-after-session-json-sec", type=float, default=6.0)
    parser.add_argument("--commit-delay-sec", type=float, default=10.0)
    parser.add_argument("--segments-path", type=Path, default=Path("derived/live/segments.jsonl"))
    parser.add_argument("--output-dir", type=Path, default=Path("derived/live"))
    parser.add_argument("--provenance", default="recording_time_committed_pcm")
    parser.add_argument("--heartbeat-sec", type=float, default=2.0)
    parser.add_argument("--ffmpeg-timeout-sec", type=float, default=45.0)
    parser.add_argument("--whisper-timeout-sec", type=float, default=180.0)
    parser.add_argument("--asr-threads", type=int, default=4)
    parser.add_argument(
        "--asr-parallelism",
        type=int,
        choices=(0, 1, 2),
        default=0,
        help="Base mic/remote ASR concurrency. 0 selects 2 only on hosts with at least 12 logical CPUs.",
    )
    parser.add_argument(
        "--causal-target-me-timeout-sec",
        type=float,
        default=60.0,
        help="Bound each optional Target-Me micro-ASR child. The base live draft is already durable before this work starts.",
    )
    parser.add_argument(
        "--causal-target-me-max-live-lag-sec",
        type=float,
        default=60.0,
        help=(
            "Skip optional Target-Me work when captured audio is this far ahead of the newly written base chunk. "
            "Use 0 only in diagnostics to disable the lag budget."
        ),
    )
    parser.add_argument(
        "--causal-me-recovery-timeout-sec",
        type=float,
        default=120.0,
        help="Kill only the explicit causal recovery child when one invocation exceeds this budget.",
    )
    parser.add_argument(
        "--causal-me-recovery-max-live-lag-sec",
        type=float,
        default=90.0,
        help="Skip explicit causal recovery when its newest closed chunk is too far behind capture.",
    )
    parser.add_argument(
        "--causal-me-recovery-stop-wait-sec",
        type=float,
        default=30.0,
        help="Bound the optional latest-cutoff recovery drain after capture stops.",
    )
    parser.add_argument("--max-segments", type=int, default=0, help="Debug limit. 0 means no limit.")
    parser.add_argument(
        "--no-causal-target-me",
        action="store_true",
        help="Disable the optional past-only Target-Me speaker shadow. Raw/live base processing is unchanged.",
    )
    parser.add_argument(
        "--no-causal-me-recovery-runtime",
        action="store_true",
        help="Disable the explicit-only local-island v2 + remote-active v1 runtime child.",
    )
    args = parser.parse_args()
    if args.asr_parallelism == 0:
        args.asr_parallelism = 2 if (os.cpu_count() or 1) >= 12 else 1
    args.asr_threads = max(1, args.asr_threads)
    return args


def load_progressive_target_me() -> Any:
    path = Path(__file__).with_name("live-progressive-target-me.py")
    spec = importlib.util.spec_from_file_location("murmurmark_live_progressive_target_me", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import progressive Target-Me worker: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_causal_me_recovery_manager() -> Any:
    path = Path(__file__).with_name("live-causal-me-recovery-manager.py")
    spec = importlib.util.spec_from_file_location("murmurmark_live_causal_me_recovery_manager", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import causal Me recovery manager: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_output_path(session: Path, value: Path) -> Path:
    return value if value.is_absolute() else session / value


class WorkerHeartbeat:
    def __init__(self, output_dir: Path, *, provenance: str, interval_sec: float) -> None:
        self.output_dir = output_dir
        self.provenance = provenance
        self.interval_sec = max(0.2, interval_sec)
        self.state_path = output_dir / "live_pipeline_state.json"
        self.events_path = output_dir / "worker_events.jsonl"
        self.last_event: tuple[str, str, int | None] | None = None
        self.last_progress: dict[str, Any] = {}
        self.lock = threading.Lock()

    def update(self, **kwargs: Any) -> None:
        with self.lock:
            self._update(**kwargs)

    def _update(
        self,
        *,
        status: str,
        stage: str,
        index: int | None = None,
        child_pid: int | None = None,
        detail: str | None = None,
        progress: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "schema": "murmurmark.live_pipeline_state/v1",
            "status": status,
            "current_stage": stage,
            "current_index": index,
            "child_pid": child_pid,
            "child_pids": active_child_pids(),
            "detail": detail,
            "heartbeat_at": utc_now(),
            "worker_pid": os.getpid(),
            "provenance": self.provenance,
            "batch_authoritative": True,
            "promotion_allowed": False,
            "draft_transcript": str(self.output_dir / "transcript.draft.md"),
            "preview_transcript": str(self.output_dir / "transcript.preview.md"),
            "report": str(self.output_dir / "live_pipeline_report.json"),
        }
        if progress is not None:
            self.last_progress = dict(progress)
        if self.last_progress:
            payload["progress"] = self.last_progress
        write_json(self.state_path, payload)
        event_key = (status, stage, index)
        if event_key != self.last_event:
            append_jsonl(
                self.events_path,
                {
                    "schema": "murmurmark.live_worker_event/v1",
                    "created_at": payload["heartbeat_at"],
                    "status": status,
                    "stage": stage,
                    "index": index,
                    "child_pid": child_pid,
                    "child_pids": active_child_pids(),
                    "detail": detail,
                    "provenance": self.provenance,
                },
            )
            self.last_event = event_key


def active_child_pids() -> list[int]:
    with ACTIVE_CHILDREN_LOCK:
        return sorted(ACTIVE_CHILDREN)


def register_active_child(process: subprocess.Popen[str]) -> None:
    with ACTIVE_CHILDREN_LOCK:
        ACTIVE_CHILDREN[process.pid] = process


def unregister_active_child(process: subprocess.Popen[str]) -> None:
    with ACTIVE_CHILDREN_LOCK:
        ACTIVE_CHILDREN.pop(process.pid, None)


def terminate_process_group(process: subprocess.Popen[str], signal: int) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal)
    except (ProcessLookupError, PermissionError):
        if signal == process_signal.SIGKILL:
            process.kill()
        else:
            process.terminate()


def request_worker_shutdown(_signum: int, _frame: Any) -> None:
    global SHUTDOWN_REQUESTED
    SHUTDOWN_REQUESTED = True
    with ACTIVE_CHILDREN_LOCK:
        children = list(ACTIVE_CHILDREN.values())
    for child in children:
        terminate_process_group(child, process_signal.SIGTERM)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def disable_causal_me_recovery(
    session: Path,
    manager: Any,
    error: Exception,
) -> None:
    reason = f"manager_error: {error}"
    try:
        manager.disable(reason)
        return
    except Exception:
        pass
    try:
        write_json(
            session / "derived/live/causal-me-recovery-runtime-v1/worker_state.json",
            {
                "schema": "murmurmark.live_causal_me_recovery_worker/v1",
                "status": "disabled_fail_open",
                "reason": reason,
                "normal_preview_connected": False,
                "base_draft_fallback": True,
                "batch_authoritative": True,
                "promotion_allowed": False,
            },
        )
    except OSError:
        pass


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def rewrite_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as file:
            for line in file:
                line = line.strip()
                if not line:
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    rows.append(value)
    except OSError:
        return []
    return rows


def rel(path: Path, session: Path) -> str:
    try:
        return str(path.relative_to(session))
    except ValueError:
        return str(path)


def resolve_existing_session_path(session: Path, value: Any) -> Path | None:
    if not value:
        return None
    path = Path(str(value))
    if not path.is_absolute():
        path = session / path
    return path if path.exists() else None


def fmt_time(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def clean_text(text: str) -> str:
    return " ".join(text.replace("\n", " ").split()).strip()


def tokens(text: str) -> list[str]:
    return [match.group(0).lower() for match in TOKEN_RE.finditer(text)]


def bag_recall(source_tokens: list[str], target_tokens: list[str]) -> float | None:
    if not source_tokens:
        return None
    target = {}
    for token in target_tokens:
        target[token] = target.get(token, 0) + 1
    matched = 0
    for token in source_tokens:
        if target.get(token, 0) > 0:
            matched += 1
            target[token] -= 1
    return matched / max(1, len(source_tokens))


def counter_unique_tokens(source_tokens: list[str], target_tokens: list[str]) -> list[str]:
    target = {}
    for token in target_tokens:
        target[token] = target.get(token, 0) + 1
    unique: list[str] = []
    for token in source_tokens:
        if target.get(token, 0) > 0:
            target[token] -= 1
        else:
            unique.append(token)
    return unique


def audio_slice(audio: np.ndarray | None, sample_rate: int | None, start_sec: float, end_sec: float) -> np.ndarray:
    if audio is None or sample_rate is None:
        return np.asarray([], dtype=np.float32)
    start = max(0, int(round(start_sec * sample_rate)))
    end = min(len(audio), int(round(end_sec * sample_rate)))
    if end <= start:
        return np.asarray([], dtype=np.float32)
    return audio[start:end]


def peak_db(audio: np.ndarray) -> float | None:
    if audio.size == 0:
        return None
    peak = float(np.max(np.abs(audio.astype(np.float64))))
    return round(20.0 * np.log10(peak + EPSILON), 3)


def zero_lag_abs_corr(left: np.ndarray, right: np.ndarray) -> float | None:
    count = min(left.size, right.size)
    if count < 160:
        return None
    a = left[:count].astype(np.float64)
    b = right[:count].astype(np.float64)
    a -= float(np.mean(a))
    b -= float(np.mean(b))
    denom = float(np.sqrt(np.sum(a * a) * np.sum(b * b)))
    if denom <= EPSILON:
        return None
    return round(abs(float(np.sum(a * b) / denom)), 6)


def segment_audio_features(
    *,
    mic_audio: np.ndarray | None,
    remote_audio: np.ndarray | None,
    sample_rate: int | None,
    clip_start_sec: float,
    start_sec: float,
    end_sec: float,
) -> dict[str, Any]:
    local_start = max(0.0, start_sec - clip_start_sec)
    local_end = max(local_start, end_sec - clip_start_sec)
    mic = audio_slice(mic_audio, sample_rate, local_start, local_end)
    remote = audio_slice(remote_audio, sample_rate, local_start, local_end)
    mic_db = round(rms_db(mic), 3) if mic.size else None
    remote_db = round(rms_db(remote), 3) if remote.size else None
    return {
        "mic_clean_rms_db": mic_db,
        "remote_rms_db": remote_db,
        "mic_clean_peak_db": peak_db(mic),
        "remote_peak_db": peak_db(remote),
        "mic_minus_remote_rms_db": round(mic_db - remote_db, 3)
        if mic_db is not None and remote_db is not None
        else None,
        "mic_remote_zero_lag_abs_corr": zero_lag_abs_corr(mic, remote),
    }


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def strip_hallucination_fragments(text: str) -> str:
    cleaned = clean_text(text)
    lowered = cleaned.lower()
    marker_offsets = [lowered.find(marker) for marker in HALLUCINATION_SUFFIX_MARKERS]
    marker_offsets = [offset for offset in marker_offsets if offset >= 0]
    if marker_offsets:
        cleaned = cleaned[: min(marker_offsets)]
    cleaned = CORRECTOR_CREDIT_RE.sub("", cleaned)
    normalized = clean_text(cleaned).strip(".!?, ")
    for phrase in sorted(KNOWN_HALLUCINATIONS, key=len, reverse=True):
        normalized = re.sub(re.escape(phrase), " ", normalized, flags=re.IGNORECASE)
    normalized = clean_text(normalized).strip(".!?, ")
    lowered_normalized = normalized.lower()
    if lowered_normalized.startswith("субтитры") or not re.sub(
        r"[^A-Za-zА-Яа-яЁё0-9]+", "", lowered_normalized
    ):
        return ""
    return normalized


def is_hallucination(text: str) -> bool:
    return bool(clean_text(text)) and not strip_hallucination_fragments(text)


def read_asr_segments(
    json_path: Path | None,
    clip_start_sec: float,
    hard_start_sec: float,
    hard_end_sec: float,
) -> list[dict[str, Any]]:
    if json_path is None or not json_path.exists():
        return []
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    segments: list[dict[str, Any]] = []
    for row in data.get("transcription") or []:
        if not isinstance(row, dict):
            continue
        offsets = row.get("offsets") or {}
        local_start = int(offsets.get("from") or 0)
        local_end = int(offsets.get("to") or local_start)
        global_start = clip_start_sec + local_start / 1000.0
        global_end = clip_start_sec + local_end / 1000.0
        center = (global_start + global_end) / 2.0
        if not (hard_start_sec <= center < hard_end_sec):
            continue
        text = strip_hallucination_fragments(str(row.get("text") or ""))
        if text and not is_hallucination(text):
            segments.append(
                {
                    "start": round(global_start, 3),
                    "end": round(max(global_end, global_start), 3),
                    "text": text,
                    "tokens": tokens(text),
                }
            )
    return segments


def segments_text(segments: list[dict[str, Any]]) -> str:
    return clean_text(" ".join(str(row.get("text") or "") for row in segments))


def overlapping_segment_tokens(
    segments: list[dict[str, Any]],
    start_sec: float,
    end_sec: float,
    guard_sec: float = 1.0,
) -> list[str]:
    start = start_sec - guard_sec
    end = end_sec + guard_sec
    result: list[str] = []
    for row in segments:
        row_start = float(row.get("start") or 0.0)
        row_end = float(row.get("end") or row_start)
        if min(end, row_end) - max(start, row_start) > 0.0:
            result.extend(row.get("tokens") or [])
    return result


def segment_policy_candidates(
    *,
    token_count: int,
    unique_count: int,
    remote_token_count: int,
    mic_in_remote: float,
    remote_in_mic: float,
    audio_features: dict[str, Any],
) -> list[str]:
    labels: list[str] = []
    mic_db = audio_features.get("mic_clean_rms_db")
    remote_db = audio_features.get("remote_rms_db")
    mic_minus_remote_db = audio_features.get("mic_minus_remote_rms_db")
    corr = audio_features.get("mic_remote_zero_lag_abs_corr")
    mic_db_value = safe_float(mic_db, -120.0) if mic_db is not None else -120.0
    remote_db_value = safe_float(remote_db, -120.0) if remote_db is not None else -120.0
    mic_minus_remote_value = safe_float(mic_minus_remote_db, 0.0) if mic_minus_remote_db is not None else 0.0
    corr_value = safe_float(corr, 1.0) if corr is not None else 1.0
    if token_count >= 5 and unique_count >= 4 and mic_in_remote <= 0.35 and remote_in_mic <= 0.45:
        labels.append("strict_text_unique_v1")
    if token_count >= 3 and remote_token_count == 0:
        labels.append("remote_silent_text_v1")
    if token_count >= 3 and mic_db_value >= -58.0 and remote_db_value <= -48.0:
        labels.append("audio_remote_quiet_v1")
    if (
        token_count >= 4
        and mic_db_value >= -56.0
        and mic_minus_remote_value >= 8.0
        and mic_in_remote <= 0.55
    ):
        labels.append("audio_mic_dominant_v1")
    if (
        token_count >= 4
        and mic_db_value >= -56.0
        and corr_value <= 0.20
        and mic_in_remote <= 0.55
        and unique_count >= 2
    ):
        labels.append("audio_low_coherence_v1")
    if "remote_silent_text_v1" in labels or "audio_mic_dominant_v1" in labels:
        labels.append("audio_safe_union_v1")
    return labels


def segment_rescue_decision(
    mic_segment: dict[str, Any],
    remote_segments: list[dict[str, Any]],
    *,
    mic_audio: np.ndarray | None = None,
    remote_audio: np.ndarray | None = None,
    sample_rate: int | None = None,
    clip_start_sec: float = 0.0,
) -> dict[str, Any]:
    mic_tokens = mic_segment.get("tokens") or []
    remote_tokens = overlapping_segment_tokens(
        remote_segments,
        float(mic_segment.get("start") or 0.0),
        float(mic_segment.get("end") or 0.0),
    )
    mic_in_remote = bag_recall(mic_tokens, remote_tokens) or 0.0
    remote_in_mic = bag_recall(remote_tokens, mic_tokens) or 0.0
    unique_tokens = counter_unique_tokens(mic_tokens, remote_tokens)
    token_count = len(mic_tokens)
    unique_count = len(unique_tokens)
    remote_token_count = len(remote_tokens)
    audio_features = segment_audio_features(
        mic_audio=mic_audio,
        remote_audio=remote_audio,
        sample_rate=sample_rate,
        clip_start_sec=clip_start_sec,
        start_sec=float(mic_segment.get("start") or 0.0),
        end_sec=float(mic_segment.get("end") or 0.0),
    )
    policies = segment_policy_candidates(
        token_count=token_count,
        unique_count=unique_count,
        remote_token_count=remote_token_count,
        mic_in_remote=mic_in_remote,
        remote_in_mic=remote_in_mic,
        audio_features=audio_features,
    )
    keep = bool(
        token_count >= 3
        and (
            not remote_tokens
            or (mic_in_remote <= 0.25 and unique_count >= 2)
            or (token_count >= 8 and unique_count >= 5 and mic_in_remote <= 0.35)
        )
    )
    if keep:
        reason = "segment_has_local_tokens_not_seen_in_overlapping_remote"
        policies = ["current_text_segment_gate", *policies]
    elif not mic_tokens:
        reason = "empty_segment"
    elif not remote_tokens:
        reason = "no_overlapping_remote_but_too_short_or_weak"
    else:
        reason = "segment_duplicates_overlapping_remote"
    return {
        "status": "kept" if keep else "suppressed",
        "reason": reason,
        "start": mic_segment.get("start"),
        "end": mic_segment.get("end"),
        "text": mic_segment.get("text"),
        "token_count": token_count,
        "unique_token_count": unique_count,
        "unique_tokens": unique_tokens[:12],
        "overlapping_remote_token_count": remote_token_count,
        "mic_token_recall_in_overlapping_remote": round(mic_in_remote, 6),
        "overlapping_remote_token_recall_in_mic": round(remote_in_mic, 6),
        "audio": audio_features,
        "rescue_policy_candidates": policies,
    }


def segment_level_role_rescue(session: Path, record: dict[str, Any]) -> dict[str, Any]:
    mic = record.get("mic") if isinstance(record.get("mic"), dict) else {}
    remote = record.get("remote") if isinstance(record.get("remote"), dict) else {}
    mic_asr = mic.get("asr") if isinstance(mic.get("asr"), dict) else {}
    remote_asr = remote.get("asr") if isinstance(remote.get("asr"), dict) else {}
    mic_json = Path(str(mic_asr.get("json"))) if mic_asr.get("json") else None
    remote_json = Path(str(remote_asr.get("json"))) if remote_asr.get("json") else None
    mic_audio: np.ndarray | None = None
    remote_audio: np.ndarray | None = None
    sample_rate: int | None = None
    mic_audio_path = resolve_existing_session_path(session, mic.get("asr_wav") or mic.get("wav") or mic.get("input"))
    remote_audio_path = resolve_existing_session_path(session, remote.get("wav") or remote.get("input"))
    if mic_audio_path and remote_audio_path:
        try:
            mic_rate, mic_audio = read_wav_float(mic_audio_path)
            remote_rate, remote_audio = read_wav_float(remote_audio_path)
            if mic_rate == remote_rate:
                sample_rate = mic_rate
            else:
                mic_audio = None
                remote_audio = None
        except (OSError, ValueError):
            mic_audio = None
            remote_audio = None
    mic_segments = read_asr_segments(
        mic_json,
        clip_start_sec=float(mic.get("clip_start_sec") or 0.0),
        hard_start_sec=float(mic.get("hard_start_sec") or 0.0),
        hard_end_sec=float(mic.get("hard_end_sec") or 0.0),
    )
    remote_segments = read_asr_segments(
        remote_json,
        clip_start_sec=float(remote.get("clip_start_sec") or 0.0),
        hard_start_sec=float(remote.get("hard_start_sec") or 0.0),
        hard_end_sec=float(remote.get("hard_end_sec") or 0.0),
    )
    decisions = [
        segment_rescue_decision(
            segment,
            remote_segments,
            mic_audio=mic_audio,
            remote_audio=remote_audio,
            sample_rate=sample_rate,
            clip_start_sec=float(mic.get("clip_start_sec") or 0.0),
        )
        for segment in mic_segments
    ]
    kept = [row for row in decisions if row.get("status") == "kept"]
    suppressed = [row for row in decisions if row.get("status") == "suppressed"]
    shadow_segments = [
        row for row in decisions if LIVE_RESCUE_SHADOW_POLICY in (row.get("rescue_policy_candidates") or [])
    ]
    kept_text = segments_text(kept)
    shadow_text = segments_text(shadow_segments)
    rescued = len(tokens(kept_text)) >= 2
    shadow_rescued = len(tokens(shadow_text)) >= 2
    return {
        "schema": "murmurmark.live_segment_role_gate/v1",
        "status": "rescued" if rescued else "no_rescue",
        "reason": "kept_low_remote_similarity_mic_segments" if rescued else "no_safe_mic_segments",
        "shadow_status": "candidate" if shadow_rescued else "no_candidate",
        "shadow_policy": LIVE_RESCUE_SHADOW_POLICY,
        "shadow_publish_policy": "shadow_only_not_live_me",
        "shadow_reason": "audio_safe_union_candidate" if shadow_rescued else "no_audio_safe_union_candidate",
        "mic_segment_count": len(mic_segments),
        "remote_segment_count": len(remote_segments),
        "kept_segment_count": len(kept),
        "suppressed_segment_count": len(suppressed),
        "shadow_segment_count": len(shadow_segments),
        "kept_text": kept_text,
        "shadow_text": shadow_text,
        "kept_segments": kept[:20],
        "suppressed_segments": suppressed[:20],
        "shadow_segments": shadow_segments[:20],
    }


def apply_live_role_gate(session: Path, record: dict[str, Any]) -> None:
    mic = record.get("mic")
    remote = record.get("remote")
    if not isinstance(mic, dict) or not isinstance(remote, dict):
        return
    mic_text = clean_text(str(mic.get("text") or ""))
    remote_text = clean_text(str(remote.get("text") or ""))
    mic_tokens = tokens(mic_text)
    remote_tokens = tokens(remote_text)
    segment_gate = segment_level_role_rescue(session, record)
    mic["live_segment_role_gate"] = segment_gate
    if len(mic_tokens) < 3 or len(remote_tokens) < 3:
        mic["live_role_gate"] = {"status": "passed", "reason": "too_short_for_duplicate_gate"}
        return
    mic_in_remote = bag_recall(mic_tokens, remote_tokens) or 0.0
    remote_in_mic = bag_recall(remote_tokens, mic_tokens) or 0.0
    duplicate_score = max(mic_in_remote, remote_in_mic)
    if duplicate_score >= LIVE_ROLE_DUPLICATE_THRESHOLD:
        mic["raw_text_before_role_gate"] = mic_text
        shadow_text = clean_text(str(segment_gate.get("shadow_text") or ""))
        if shadow_text:
            mic["live_rescue_shadow"] = {
                "schema": "murmurmark.live_rescue_shadow/v1",
                "status": "candidate",
                "policy": segment_gate.get("shadow_policy") or LIVE_RESCUE_SHADOW_POLICY,
                "publish_policy": segment_gate.get("shadow_publish_policy") or "shadow_only_not_live_me",
                "reason": segment_gate.get("shadow_reason") or "audio_safe_union_candidate",
                "text": shadow_text,
                "segment_count": segment_gate.get("shadow_segment_count") or 0,
                "segments": segment_gate.get("shadow_segments") or [],
            }
        mic["text"] = ""
        mic["live_role_gate"] = {
            "status": "suppressed",
            "reason": "mic_text_duplicates_remote_text",
            "duplicate_score": round(duplicate_score, 6),
            "mic_token_recall_in_remote": round(mic_in_remote, 6),
            "remote_token_recall_in_mic": round(remote_in_mic, 6),
            "segment_gate_status": segment_gate.get("status"),
            "segment_gate_publish_policy": "diagnostic_only",
            "rescue_shadow_status": segment_gate.get("shadow_status"),
            "rescue_shadow_policy": segment_gate.get("shadow_policy"),
            "rescue_shadow_publish_policy": segment_gate.get("shadow_publish_policy"),
            "rescue_shadow_segment_count": segment_gate.get("shadow_segment_count"),
            "kept_segment_count": segment_gate.get("kept_segment_count"),
            "suppressed_segment_count": segment_gate.get("suppressed_segment_count"),
        }
    else:
        mic["live_role_gate"] = {
            "status": "passed",
            "reason": "mic_text_not_duplicate_remote_text",
            "duplicate_score": round(duplicate_score, 6),
            "mic_token_recall_in_remote": round(mic_in_remote, 6),
            "remote_token_recall_in_mic": round(remote_in_mic, 6),
        }


def apply_adjacent_boundary_gate(previous: dict[str, Any] | None, current: dict[str, Any]) -> None:
    if previous is None:
        return
    for source in ("mic", "remote"):
        previous_source = previous.get(source)
        current_source = current.get(source)
        if not isinstance(previous_source, dict) or not isinstance(current_source, dict):
            continue
        previous_text = clean_text(str(previous_source.get("text") or ""))
        current_text = clean_text(str(current_source.get("text") or ""))
        previous_tokens = tokens(previous_text)
        current_tokens = tokens(current_text)
        if len(previous_tokens) < 3 or len(current_tokens) < 2:
            current_source["live_boundary_gate"] = {"status": "passed", "reason": "too_short_for_boundary_gate"}
            continue
        current_in_previous = bag_recall(current_tokens, previous_tokens) or 0.0
        previous_in_current = bag_recall(previous_tokens, current_tokens) or 0.0
        duplicate_score = max(current_in_previous, previous_in_current)
        if current_in_previous >= 0.80 or (duplicate_score >= 0.88 and len(current_tokens) <= len(previous_tokens) + 2):
            current_source["raw_text_before_boundary_gate"] = current_text
            current_source["text"] = ""
            current_source["live_boundary_gate"] = {
                "status": "suppressed",
                "reason": "adjacent_chunk_duplicate",
                "duplicate_score": round(duplicate_score, 6),
                "current_token_recall_in_previous": round(current_in_previous, 6),
                "previous_token_recall_in_current": round(previous_in_current, 6),
            }
        else:
            current_source["live_boundary_gate"] = {
                "status": "passed",
                "reason": "not_adjacent_duplicate",
                "duplicate_score": round(duplicate_score, 6),
                "current_token_recall_in_previous": round(current_in_previous, 6),
                "previous_token_recall_in_current": round(previous_in_current, 6),
            }


def run_bounded(
    command: list[str],
    *,
    timeout_sec: float,
    heartbeat: WorkerHeartbeat,
    stage: str,
    index: int,
) -> dict[str, Any]:
    if SHUTDOWN_REQUESTED:
        heartbeat.update(
            status="running",
            stage=f"{stage}_cancelled",
            index=index,
            detail="worker shutdown requested",
        )
        return {
            "returncode": 143,
            "stdout": "",
            "stderr": "worker shutdown requested",
            "timed_out": False,
            "elapsed_sec": 0.0,
        }
    started = time.monotonic()
    process = subprocess.Popen(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    register_active_child(process)
    try:
        while True:
            elapsed = time.monotonic() - started
            remaining = timeout_sec - elapsed
            if remaining <= 0:
                terminate_process_group(process, process_signal.SIGTERM)
                try:
                    stdout, stderr = process.communicate(timeout=5.0)
                except subprocess.TimeoutExpired:
                    terminate_process_group(process, process_signal.SIGKILL)
                    stdout, stderr = process.communicate()
                heartbeat.update(
                    status="running",
                    stage=f"{stage}_timeout",
                    index=index,
                    detail=f"timed out after {timeout_sec:.1f}s",
                )
                return {
                    "returncode": 124,
                    "stdout": stdout,
                    "stderr": stderr,
                    "timed_out": True,
                    "elapsed_sec": round(time.monotonic() - started, 3),
                }
            heartbeat.update(
                status="running",
                stage=stage,
                index=index,
                child_pid=process.pid,
                detail=f"elapsed={elapsed:.1f}s",
            )
            try:
                stdout, stderr = process.communicate(timeout=min(heartbeat.interval_sec, remaining))
                return {
                    "returncode": process.returncode,
                    "stdout": stdout,
                    "stderr": stderr,
                    "timed_out": False,
                    "elapsed_sec": round(time.monotonic() - started, 3),
                }
            except subprocess.TimeoutExpired:
                continue
    finally:
        unregister_active_child(process)


def causal_target_me_lag_decision(
    *,
    captured_sec: float,
    chunk_end_sec: float,
    max_live_lag_sec: float,
) -> dict[str, Any]:
    observed_lag = max(0.0, captured_sec - chunk_end_sec)
    enabled = max_live_lag_sec <= 0.0 or observed_lag <= max_live_lag_sec
    return {
        "run": enabled,
        "observed_live_lag_sec": round(observed_lag, 3),
        "max_live_lag_sec": round(max_live_lag_sec, 3),
        "reason": "within_lag_budget" if enabled else "live_lag_budget_exceeded",
    }


def bounded_progressive_micro_runner(
    *,
    args: argparse.Namespace,
    heartbeat: WorkerHeartbeat,
    progressive_module: Any,
):
    def run(
        wav: Path,
        output_base: Path,
        model: str,
        language: str,
        whisper_cli: str,
    ) -> dict[str, Any]:
        model_path = Path(model).expanduser()
        if not model_path.exists():
            return {"status": "skipped", "reason": "model_missing", "text": "", "score": 0.0}
        executable = shutil.which(whisper_cli) or (whisper_cli if Path(whisper_cli).exists() else None)
        if not executable:
            return {"status": "skipped", "reason": "whisper_cli_missing", "text": "", "score": 0.0}
        output_base.parent.mkdir(parents=True, exist_ok=True)
        match = re.search(r"chunk_(\d+)", output_base.name)
        index = int(match.group(1)) if match else 0
        result = run_bounded(
            [
                executable,
                "--model",
                str(model_path),
                "--language",
                language,
                "--threads",
                "4",
                "--max-context",
                "0",
                "--output-txt",
                "--output-json",
                "--output-json-full",
                "--output-file",
                str(output_base),
                "--no-prints",
                "--log-score",
                "--suppress-nst",
                "--suppress-regex",
                "^(Редактор субтитров|Продолжение следует|Спасибо за просмотр|Субтитры.*)$",
                "--file",
                str(wav),
            ],
            timeout_sec=max(0.1, args.causal_target_me_timeout_sec),
            heartbeat=heartbeat,
            stage="target_me_micro_asr",
            index=index,
        )
        txt_path = output_base.with_suffix(".txt")
        json_path = output_base.with_suffix(".json")
        text = clean_text(txt_path.read_text(encoding="utf-8", errors="ignore")) if txt_path.exists() else ""
        if progressive_module.is_hallucination(text):
            text = ""
        timed_out = bool(result.get("timed_out"))
        return {
            "status": "passed" if result.get("returncode") == 0 else "failed",
            "reason": "target_me_micro_asr_timeout" if timed_out else None,
            "text": text,
            "score": round(
                progressive_module.token_average_probability(json_path if json_path.exists() else None),
                6,
            ),
            "json": str(json_path) if json_path.exists() else None,
            "rows": progressive_module.asr_rows(json_path if json_path.exists() else None),
            "stderr_tail": str(result.get("stderr") or "")[-1000:] if result.get("returncode") else "",
            "timed_out": timed_out,
            "elapsed_sec": result.get("elapsed_sec"),
        }

    return run


def audio_filter(source_name: str) -> tuple[str, str]:
    if source_name == "mic":
        return "speech", "highpass=f=100,lowpass=f=7600,alimiter=limit=0.98"
    return "loudnorm", "highpass=f=80,lowpass=f=7800,loudnorm=I=-20:LRA=9:TP=-2,alimiter=limit=0.98"


def convert_to_wav(
    source: Path,
    destination: Path,
    source_name: str,
    *,
    args: argparse.Namespace,
    heartbeat: WorkerHeartbeat,
    index: int,
) -> tuple[bool, str, str | None]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    prep_name, filters = audio_filter(source_name)
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-af",
        filters,
        "-ar",
        "16000",
        "-ac",
        "1",
        str(destination),
    ]
    result = run_bounded(
        command,
        timeout_sec=max(0.1, args.ffmpeg_timeout_sec),
        heartbeat=heartbeat,
        stage=f"preprocess_{source_name}",
        index=index,
    )
    ok = result["returncode"] == 0 and destination.exists() and destination.stat().st_size > 44
    reason = None if ok else ("ffmpeg_timeout" if result["timed_out"] else "ffmpeg_failed")
    return ok, prep_name, reason


def transcribe(
    wav: Path,
    output_base: Path,
    args: argparse.Namespace,
    *,
    heartbeat: WorkerHeartbeat,
    index: int,
    source: str,
) -> dict[str, Any]:
    if not args.model.exists():
        return {
            "status": "skipped",
            "reason": "model_missing",
            "model": str(args.model),
            "text": "",
        }
    if shutil.which(args.whisper_cli) is None:
        return {
            "status": "skipped",
            "reason": "whisper_cli_missing",
            "whisper_cli": args.whisper_cli,
            "text": "",
        }
    output_base.parent.mkdir(parents=True, exist_ok=True)
    command = [
        args.whisper_cli,
        "--model",
        str(args.model),
        "--language",
        args.language,
        "--threads",
        str(args.asr_threads),
        "--max-context",
        "0",
        "--output-txt",
        "--output-json",
        "--output-json-full",
        "--output-file",
        str(output_base),
        "--no-prints",
        "--log-score",
        "--suppress-nst",
        "--suppress-regex",
        "^(Редактор субтитров|Продолжение следует|Спасибо за просмотр|Субтитры.*)$",
        "--file",
        str(wav),
    ]
    result = run_bounded(
        command,
        timeout_sec=max(0.1, args.whisper_timeout_sec),
        heartbeat=heartbeat,
        stage=f"asr_{source}",
        index=index,
    )
    elapsed = result["elapsed_sec"]
    txt_path = output_base.with_suffix(".txt")
    text = clean_text(txt_path.read_text(encoding="utf-8", errors="ignore")) if txt_path.exists() else ""
    status = "passed" if result["returncode"] == 0 else ("timed_out" if result["timed_out"] else "failed")
    if is_hallucination(text):
        text = ""
    return {
        "status": status,
        "elapsed_sec": elapsed,
        "text": text,
        "json": str(output_base.with_suffix(".json")) if output_base.with_suffix(".json").exists() else None,
        "stderr_tail": result["stderr"][-1000:] if result["returncode"] != 0 else "",
    }


def read_wav_float(path: Path) -> tuple[int, np.ndarray]:
    sample_rate, data = wavfile.read(path)
    if data.ndim > 1:
        data = data.mean(axis=1)
    if np.issubdtype(data.dtype, np.integer):
        info = np.iinfo(data.dtype)
        scale = float(max(abs(info.min), info.max))
        audio = data.astype(np.float32) / scale
    else:
        audio = data.astype(np.float32)
    return sample_rate, np.nan_to_num(audio)


def write_wav_float(path: Path, sample_rate: int, audio: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    wavfile.write(path, sample_rate, np.clip(audio, -1.0, 1.0).astype(np.float32))


def rms_db(audio: np.ndarray) -> float:
    rms = float(np.sqrt(np.mean(np.square(audio), dtype=np.float64) + EPSILON))
    return 20.0 * np.log10(rms + EPSILON)


def normalized_corr(left: np.ndarray, right: np.ndarray) -> float:
    a = np.asarray(left, dtype=np.float64) - float(np.mean(left))
    b = np.asarray(right, dtype=np.float64) - float(np.mean(right))
    return float(np.dot(a, b) / (np.sqrt(np.dot(a, a) * np.dot(b, b)) + EPSILON))


def shift_reference(remote: np.ndarray, delay_samples: int) -> np.ndarray:
    shifted = np.zeros_like(remote)
    if delay_samples > 0:
        shifted[delay_samples:] = remote[:-delay_samples]
    elif delay_samples < 0:
        lead = -delay_samples
        shifted[:-lead] = remote[lead:]
    else:
        shifted[:] = remote
    return shifted


def estimate_delay_samples(mic: np.ndarray, remote: np.ndarray, sample_rate: int) -> tuple[int, float]:
    min_lag = int(round(-0.25 * sample_rate))
    max_lag = int(round(0.80 * sample_rate))
    count = min(mic.size, remote.size)
    if count < sample_rate // 4:
        return 0, 0.0
    mic_data = mic[:count].astype(np.float64) - float(np.mean(mic[:count]))
    remote_data = remote[:count].astype(np.float64) - float(np.mean(remote[:count]))
    corr = signal.correlate(mic_data, remote_data, mode="full", method="fft")
    lags = signal.correlation_lags(mic_data.size, remote_data.size, mode="full")
    mask = (lags >= min_lag) & (lags <= max_lag)
    if not np.any(mask):
        return 0, 0.0
    limited = np.abs(corr[mask])
    limited_lags = lags[mask]
    best_index = int(np.argmax(limited))
    peak = float(limited[best_index])
    denom = float(np.sqrt(np.dot(mic_data, mic_data) * np.dot(remote_data, remote_data)) + EPSILON)
    return int(limited_lags[best_index]), peak / denom


def fit_local_fir(remote_fit: np.ndarray, mic_fit: np.ndarray, taps: int, regularization: float) -> np.ndarray:
    x = remote_fit.astype(np.float64) - float(np.mean(remote_fit))
    y = mic_fit.astype(np.float64) - float(np.mean(mic_fit))
    corr_xx = signal.correlate(x, x, mode="full", method="fft")
    corr_yx = signal.correlate(y, x, mode="full", method="fft")
    center = x.size - 1
    r_xx = corr_xx[center : center + taps]
    p_yx = corr_yx[center : center + taps]
    if r_xx.size < taps or p_yx.size < taps or float(r_xx[0]) <= EPSILON:
        return np.zeros(taps, dtype=np.float64)
    toeplitz_col = r_xx.copy()
    toeplitz_col[0] += max(float(r_xx[0]) * regularization, EPSILON)
    try:
        fir = linalg.solve_toeplitz((toeplitz_col, toeplitz_col), p_yx, check_finite=False)
    except Exception:
        return np.zeros(taps, dtype=np.float64)
    return np.asarray(fir, dtype=np.float64)


def live_echo_guard(mic_wav: Path, remote_wav: Path, output_wav: Path) -> dict[str, Any]:
    if not mic_wav.exists() or not remote_wav.exists():
        return {"status": "skipped", "reason": "missing_wav"}
    mic_rate, mic = read_wav_float(mic_wav)
    remote_rate, remote = read_wav_float(remote_wav)
    if mic_rate != remote_rate:
        return {"status": "skipped", "reason": "sample_rate_mismatch", "mic_rate": mic_rate, "remote_rate": remote_rate}
    count = min(mic.size, remote.size)
    if count < int(round(mic_rate * 1.0)):
        return {"status": "skipped", "reason": "too_short"}
    mic = mic[:count]
    remote = remote[:count]
    mic_db = rms_db(mic)
    remote_db = rms_db(remote)
    if remote_db < -55.0 or mic_db < -65.0:
        return {
            "status": "skipped",
            "reason": "inactive_audio",
            "mic_db": round(mic_db, 3),
            "remote_db": round(remote_db, 3),
        }
    delay_samples, delay_corr = estimate_delay_samples(mic, remote, mic_rate)
    remote_aligned = shift_reference(remote, delay_samples)
    before_corr = abs(normalized_corr(remote_aligned, mic))
    if before_corr < 0.08 and delay_corr < 0.08:
        return {
            "status": "skipped",
            "reason": "weak_remote_similarity",
            "mic_db": round(mic_db, 3),
            "remote_db": round(remote_db, 3),
            "delay_samples": delay_samples,
            "delay_corr": round(delay_corr, 6),
            "remote_similarity_before": round(before_corr, 6),
        }
    taps = max(1, int(round(mic_rate * 0.080)))
    fir = fit_local_fir(remote_aligned, mic, taps=taps, regularization=1.0e-2)
    echo_hat = signal.lfilter(fir, [1.0], remote_aligned.astype(np.float64))
    strength = 0.85
    clean = mic.astype(np.float64) - strength * echo_hat
    after_corr = abs(normalized_corr(remote_aligned, clean))
    before_power = float(np.mean(mic.astype(np.float64) ** 2) + EPSILON)
    after_power = float(np.mean(clean.astype(np.float64) ** 2) + EPSILON)
    peak = float(np.max(np.abs(clean))) if clean.size else 0.0
    reduction_db = 10.0 * np.log10(before_power / after_power)
    accepted = (
        np.all(np.isfinite(clean))
        and peak <= 1.25
        and after_corr <= max(before_corr * 0.95, 0.10)
        and after_power <= before_power * 1.25
    )
    report = {
        "schema": "murmurmark.live_echo_guard/v1",
        "status": "accepted" if accepted else "rejected",
        "reason": "accepted" if accepted else "quality_gate_rejected",
        "mic_db": round(mic_db, 3),
        "remote_db": round(remote_db, 3),
        "delay_samples": delay_samples,
        "delay_ms": round(delay_samples * 1000.0 / max(mic_rate, 1), 3),
        "delay_corr": round(delay_corr, 6),
        "remote_similarity_before": round(before_corr, 6),
        "remote_similarity_after": round(after_corr, 6),
        "estimated_reduction_db": round(float(reduction_db), 3),
        "strength": strength,
        "taps": taps,
    }
    if accepted:
        write_wav_float(output_wav, mic_rate, clean)
        report["output"] = str(output_wav)
    return report


def text_inside_hard_window(json_path: Path | None, clip_start_sec: float, hard_start_sec: float, hard_end_sec: float) -> str | None:
    if json_path is None or not json_path.exists():
        return None
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    parts: list[str] = []
    for row in data.get("transcription") or []:
        if not isinstance(row, dict):
            continue
        offsets = row.get("offsets") or {}
        local_start = int(offsets.get("from") or 0)
        local_end = int(offsets.get("to") or local_start)
        global_start = clip_start_sec + local_start / 1000.0
        global_end = clip_start_sec + local_end / 1000.0
        center = (global_start + global_end) / 2.0
        if hard_start_sec <= center < hard_end_sec:
            text = strip_hallucination_fragments(str(row.get("text") or ""))
            if text and not is_hallucination(text):
                parts.append(text)
    return clean_text(" ".join(parts))


def grouped_segments(rows: list[dict[str, Any]]) -> dict[int, dict[str, dict[str, Any]]]:
    grouped: dict[int, dict[str, dict[str, Any]]] = {}
    for row in rows:
        if row.get("closed") is not True:
            continue
        source = str(row.get("source") or "")
        if source not in {"mic", "remote"}:
            continue
        try:
            index = int(row.get("index"))
        except (TypeError, ValueError):
            continue
        grouped.setdefault(index, {})[source] = row
    return grouped


def write_draft(output_dir: Path, chunks: list[dict[str, Any]], commit_delay_sec: float) -> None:
    draft = output_dir / "transcript.draft.md"
    lines = [
        "# Live Draft Transcript",
        "",
        "Shadow near-realtime transcript. The batch pipeline remains authoritative.",
        "",
    ]
    if not chunks:
        lines += ["_Waiting for closed audio segments._", ""]
    max_end = max(float(chunk.get("end_sec") or 0.0) for chunk in chunks) if chunks else 0.0
    for chunk in sorted(chunks, key=lambda item: int(item.get("index") or 0)):
        provisional = max_end - float(chunk.get("end_sec") or 0.0) < commit_delay_sec
        marker = " provisional" if provisional else ""
        lines.append(f"## {fmt_time(float(chunk.get('start_sec') or 0.0))}{marker}")
        lines.append("")
        mic_row = chunk.get("mic") if isinstance(chunk.get("mic"), dict) else {}
        mic = strip_hallucination_fragments(str(mic_row.get("text") or ""))
        shadow = mic_row.get("live_rescue_shadow") if isinstance(mic_row.get("live_rescue_shadow"), dict) else {}
        shadow_text = clean_text(str(shadow.get("text") or ""))
        causal = mic_row.get("causal_target_me_shadow") if isinstance(mic_row.get("causal_target_me_shadow"), dict) else {}
        causal_candidates = causal.get("candidates") if isinstance(causal.get("candidates"), list) else []
        remote = strip_hallucination_fragments(str((chunk.get("remote") or {}).get("text") or ""))
        if mic:
            lines += ["**Me draft**", "", mic, ""]
        if shadow_text:
            policy = clean_text(str(shadow.get("policy") or LIVE_RESCUE_SHADOW_POLICY))
            lines += [
                "**Me rescue shadow**",
                "",
                f"_Candidate only: `{policy}`; batch remains authoritative._",
                "",
                shadow_text,
                "",
            ]
        for candidate in causal_candidates:
            if not isinstance(candidate, dict):
                continue
            candidate_text = strip_hallucination_fragments(str(candidate.get("text") or ""))
            if not candidate_text:
                continue
            lines += [
                "**Me causal speaker shadow**",
                "",
                "_Candidate only: past chunks + local speaker evidence; batch remains authoritative._",
                "",
                candidate_text,
                "",
            ]
        if remote:
            lines += ["**Colleagues draft**", "", remote, ""]
        if not mic and not shadow_text and not causal_candidates and not remote:
            lines += ["_No speech decoded in this segment._", ""]
    draft.parent.mkdir(parents=True, exist_ok=True)
    draft.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def candidate_passes_preview_gate(candidate: dict[str, Any]) -> bool:
    guard = candidate.get("remote_audio_guard")
    return bool(
        isinstance(guard, dict)
        and guard.get("schema") == "murmurmark.live_remote_audio_guard/v1"
        and guard.get("status") == "passed"
    )


def write_preview(
    output_dir: Path,
    chunks: list[dict[str, Any]],
    commit_delay_sec: float,
    provenance: str = "recording_time_committed_pcm",
) -> None:
    preview = output_dir / "transcript.preview.md"
    lines = [
        "# Live Preview Transcript",
        "",
        "Conservative shadow preview. The batch pipeline remains authoritative.",
        f"Policy: `{LIVE_PREVIEW_POLICY}`.",
        "",
    ]
    if not chunks:
        lines += ["_Waiting for closed audio segments._", ""]
    max_end = max(float(chunk.get("end_sec") or 0.0) for chunk in chunks) if chunks else 0.0
    preview_candidate_count = 0
    preview_rejected_count = 0
    preview_not_evaluated_count = 0
    for chunk in sorted(chunks, key=lambda item: int(item.get("index") or 0)):
        provisional = max_end - float(chunk.get("end_sec") or 0.0) < commit_delay_sec
        marker = " provisional" if provisional else ""
        lines += [f"## {fmt_time(float(chunk.get('start_sec') or 0.0))}{marker}", ""]
        mic_row = chunk.get("mic") if isinstance(chunk.get("mic"), dict) else {}
        remote_row = chunk.get("remote") if isinstance(chunk.get("remote"), dict) else {}
        mic = strip_hallucination_fragments(str(mic_row.get("text") or ""))
        remote = strip_hallucination_fragments(str(remote_row.get("text") or ""))
        causal = (
            mic_row.get("causal_target_me_shadow")
            if isinstance(mic_row.get("causal_target_me_shadow"), dict)
            else {}
        )
        candidates = causal.get("candidates") if isinstance(causal.get("candidates"), list) else []
        accepted = [
            candidate
            for candidate in candidates
            if isinstance(candidate, dict) and candidate_passes_preview_gate(candidate)
        ]
        preview_candidate_count += len(accepted)
        for candidate in candidates:
            if not isinstance(candidate, dict) or candidate in accepted:
                continue
            guard = candidate.get("remote_audio_guard")
            if isinstance(guard, dict) and guard.get("status") == "rejected":
                preview_rejected_count += 1
            else:
                preview_not_evaluated_count += 1
        if mic:
            lines += ["**Me**", "", mic, ""]
        for candidate in accepted:
            text = strip_hallucination_fragments(str(candidate.get("text") or ""))
            if text:
                lines += ["**Me**", "", text, ""]
        if remote:
            lines += ["**Colleagues**", "", remote, ""]
        if not mic and not accepted and not remote:
            lines += ["_No speech decoded in this segment._", ""]
    preview.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(lines).rstrip() + "\n"
    preview.write_text(content, encoding="utf-8")
    append_jsonl(
        output_dir / "preview_snapshots.jsonl",
        {
            "schema": "murmurmark.live_preview_snapshot/v1",
            "created_at": utc_now(),
            "provenance": provenance,
            "preview_policy": LIVE_PREVIEW_POLICY,
            "chunk_count": len(chunks),
            "processed_end_sec": round(max_end, 3),
            "preview_candidate_count": preview_candidate_count,
            "preview_rejected_count": preview_rejected_count,
            "preview_not_evaluated_count": preview_not_evaluated_count,
            "content_bytes": len(content.encode("utf-8")),
            "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "batch_authoritative": True,
            "promotion_allowed": False,
        },
    )


def write_live_views(
    output_dir: Path,
    chunks: list[dict[str, Any]],
    commit_delay_sec: float,
    provenance: str = "recording_time_committed_pcm",
) -> None:
    write_draft(output_dir, chunks, commit_delay_sec)
    write_preview(output_dir, chunks, commit_delay_sec, provenance)


def process_segment(
    session: Path,
    index: int,
    pair: dict[str, dict[str, Any]],
    args: argparse.Namespace,
    output_dir: Path,
    heartbeat: WorkerHeartbeat,
) -> dict[str, Any]:
    chunk_dir = output_dir / "chunks" / f"{index:06d}"
    start_sec = min(float(pair[source].get("start_sec") or 0.0) for source in pair)
    end_sec = max(float(pair[source].get("end_sec") or 0.0) for source in pair)
    record: dict[str, Any] = {
        "schema": "murmurmark.live_chunk/v1",
        "index": index,
        "start_sec": round(start_sec, 3),
        "end_sec": round(end_sec, 3),
        "duration_sec": round(end_sec - start_sec, 3),
        "clip_start_sec": round(min(float(pair[source].get("clip_start_sec") or pair[source].get("start_sec") or 0.0) for source in pair), 3),
        "clip_end_sec": round(max(float(pair[source].get("clip_end_sec") or pair[source].get("end_sec") or 0.0) for source in pair), 3),
        "created_at": utc_now(),
        "provenance": args.provenance,
        "mic": {},
        "remote": {},
    }
    converted: dict[str, Path] = {}
    for source in ("mic", "remote"):
        source_path = session / str(pair[source].get("path"))
        wav_path = chunk_dir / f"{source}.wav"
        hard_start_sec = float(pair[source].get("start_sec") or 0.0)
        hard_end_sec = float(pair[source].get("end_sec") or hard_start_sec)
        clip_start_sec = float(pair[source].get("clip_start_sec") or hard_start_sec)
        clip_end_sec = float(pair[source].get("clip_end_sec") or hard_end_sec)
        ok, prep_name, preprocess_reason = convert_to_wav(
            source_path,
            wav_path,
            source,
            args=args,
            heartbeat=heartbeat,
            index=index,
        )
        source_record: dict[str, Any] = {
            "input": rel(source_path, session),
            "wav": rel(wav_path, session) if ok else None,
            "audio_prep": prep_name,
            "hard_start_sec": round(hard_start_sec, 3),
            "hard_end_sec": round(hard_end_sec, 3),
            "clip_start_sec": round(clip_start_sec, 3),
            "clip_end_sec": round(clip_end_sec, 3),
            "preprocess_status": "passed" if ok else "failed",
            "preprocess_reason": preprocess_reason,
        }
        if ok:
            converted[source] = wav_path
        record[source] = source_record
    if {"mic", "remote"} <= set(converted):
        clean_wav = chunk_dir / "mic.live_echo_guard.wav"
        guard_report = live_echo_guard(converted["mic"], converted["remote"], clean_wav)
        record["mic"]["live_echo_guard"] = {
            **{key: value for key, value in guard_report.items() if key != "output"},
            "output": rel(clean_wav, session) if clean_wav.exists() else None,
        }
        if guard_report.get("status") == "accepted" and clean_wav.exists():
            record["mic"]["asr_wav"] = rel(clean_wav, session)
            converted["mic"] = clean_wav
        else:
            record["mic"]["asr_wav"] = record["mic"].get("wav")
    def decode_source(source: str) -> tuple[str, dict[str, Any], str]:
        source_record = record[source]
        wav_for_asr = converted[source]
        asr = transcribe(
            wav_for_asr,
            chunk_dir / source,
            args,
            heartbeat=heartbeat,
            index=index,
            source=source,
        )
        asr_json = Path(str(asr.get("json"))) if asr.get("json") else None
        text = text_inside_hard_window(
            asr_json,
            clip_start_sec=float(source_record.get("clip_start_sec") or 0.0),
            hard_start_sec=float(source_record.get("hard_start_sec") or 0.0),
            hard_end_sec=float(source_record.get("hard_end_sec") or 0.0),
        ) or strip_hallucination_fragments(str(asr.get("text") or ""))
        return source, asr, text

    decode_sources = [source for source in ("mic", "remote") if source in converted]
    if args.asr_parallelism >= 2 and len(decode_sources) == 2:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            decoded = list(executor.map(decode_source, decode_sources))
    else:
        decoded = [decode_source(source) for source in decode_sources]
    for source, asr, text in decoded:
        record[source]["asr"] = asr
        record[source]["text"] = text
    apply_live_role_gate(session, record)
    write_json(chunk_dir / "chunk.json", record)
    return record


def write_chunks(output_dir: Path, chunks: list[dict[str, Any]]) -> None:
    rewrite_jsonl(output_dir / "chunks.jsonl", chunks)
    for chunk in chunks:
        try:
            index = int(chunk.get("index") or 0)
        except (TypeError, ValueError):
            continue
        if index > 0:
            write_json(output_dir / "chunks" / f"{index:06d}" / "chunk.json", chunk)


def live_rescue_shadow_summary(chunks: list[dict[str, Any]]) -> dict[str, Any]:
    chunk_count = 0
    segment_count = 0
    token_count = 0
    for chunk in chunks:
        mic = chunk.get("mic") if isinstance(chunk.get("mic"), dict) else {}
        shadow = mic.get("live_rescue_shadow") if isinstance(mic.get("live_rescue_shadow"), dict) else {}
        text = clean_text(str(shadow.get("text") or ""))
        if not text:
            continue
        chunk_count += 1
        segment_count += int(shadow.get("segment_count") or 0)
        token_count += len(tokens(text))
    return {
        "policy": LIVE_RESCUE_SHADOW_POLICY,
        "publish_policy": "shadow_only_not_live_me",
        "candidate_chunk_count": chunk_count,
        "candidate_segment_count": segment_count,
        "candidate_token_count": token_count,
    }


def causal_target_me_summary(chunks: list[dict[str, Any]]) -> dict[str, Any]:
    candidate_count = 0
    candidate_seconds = 0.0
    preview_candidate_count = 0
    preview_rejected_count = 0
    preview_not_evaluated_count = 0
    evaluated_segments = 0
    skipped_lag_budget_count = 0
    failed_open_count = 0
    for chunk in chunks:
        mic = chunk.get("mic") if isinstance(chunk.get("mic"), dict) else {}
        shadow = mic.get("causal_target_me_shadow") if isinstance(mic.get("causal_target_me_shadow"), dict) else {}
        if shadow.get("status") == "skipped_lag_budget":
            skipped_lag_budget_count += 1
        if shadow.get("status") == "failed_open":
            failed_open_count += 1
        evaluated_segments += safe_int(shadow.get("evaluated_segment_count"))
        candidates = shadow.get("candidates") if isinstance(shadow.get("candidates"), list) else []
        candidate_count += len(candidates)
        candidate_seconds += sum(
            max(0.0, safe_float(row.get("end")) - safe_float(row.get("start")))
            for row in candidates
            if isinstance(row, dict)
        )
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            guard = candidate.get("remote_audio_guard")
            if candidate_passes_preview_gate(candidate):
                preview_candidate_count += 1
            elif isinstance(guard, dict) and guard.get("status") == "rejected":
                preview_rejected_count += 1
            else:
                preview_not_evaluated_count += 1
    return {
        "policy": "live_runtime_causal_target_me_direct_v1",
        "publish_policy": "shadow_only_not_live_me",
        "preview_policy": LIVE_PREVIEW_POLICY,
        "candidate_count": candidate_count,
        "candidate_seconds": round(candidate_seconds, 3),
        "preview_candidate_count": preview_candidate_count,
        "preview_rejected_count": preview_rejected_count,
        "preview_not_evaluated_count": preview_not_evaluated_count,
        "evaluated_segment_count": evaluated_segments,
        "skipped_lag_budget_count": skipped_lag_budget_count,
        "failed_open_count": failed_open_count,
        "batch_authoritative": True,
        "promotion_allowed": False,
    }


def runtime_cost_summary(chunks: list[dict[str, Any]]) -> dict[str, Any]:
    base_values: list[float] = []
    target_values: list[float] = []
    for chunk in chunks:
        runtime = chunk.get("runtime") if isinstance(chunk.get("runtime"), dict) else {}
        if runtime.get("base_elapsed_sec") is not None:
            base_values.append(max(0.0, safe_float(runtime.get("base_elapsed_sec"))))
        if runtime.get("target_me_elapsed_sec") is not None:
            target_values.append(max(0.0, safe_float(runtime.get("target_me_elapsed_sec"))))

    def summarize(values: list[float]) -> dict[str, Any]:
        if not values:
            return {"count": 0, "total_sec": 0.0, "median_sec": None, "max_sec": None}
        return {
            "count": len(values),
            "total_sec": round(sum(values), 3),
            "median_sec": round(float(np.median(values)), 3),
            "max_sec": round(max(values), 3),
        }

    return {
        "base_chunk": summarize(base_values),
        "causal_target_me": summarize(target_values),
    }


def write_report(
    session: Path,
    output_dir: Path,
    status: str,
    chunks: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    args: argparse.Namespace,
) -> None:
    captured = max((float(row.get("end_sec") or 0.0) for row in rows), default=0.0)
    processed = max((float(row.get("end_sec") or 0.0) for row in chunks), default=0.0)
    rescue_shadow = live_rescue_shadow_summary(chunks)
    causal_shadow = causal_target_me_summary(chunks)
    runtime_cost = runtime_cost_summary(chunks)
    recovery_worker_path = session / "derived/live/causal-me-recovery-runtime-v1/worker_state.json"
    recovery_worker: dict[str, Any] = {}
    if recovery_worker_path.exists():
        try:
            value = json.loads(recovery_worker_path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                recovery_worker = value
        except (OSError, json.JSONDecodeError):
            recovery_worker = {"status": "state_unreadable"}
    report = {
        "schema": SCHEMA,
        "generator": {"name": "live-pipeline-shadow", "version": SCRIPT_VERSION},
        "status": status,
        "updated_at": utc_now(),
        "session": str(session),
        "mode": "near_realtime_shadow",
        "batch_authoritative": True,
        "promotion_allowed": False,
        "provenance": args.provenance,
        "current_worker": "live-pipeline-shadow",
        "current_stage": status,
        "parameters": {
            "commit_delay_sec": args.commit_delay_sec,
            "language": args.language,
            "model": str(args.model),
            "asr_threads": args.asr_threads,
            "asr_parallelism": args.asr_parallelism,
            "logical_cpu_count": os.cpu_count(),
            "causal_target_me_timeout_sec": args.causal_target_me_timeout_sec,
            "causal_target_me_max_live_lag_sec": args.causal_target_me_max_live_lag_sec,
            "causal_me_recovery_timeout_sec": args.causal_me_recovery_timeout_sec,
            "causal_me_recovery_max_live_lag_sec": args.causal_me_recovery_max_live_lag_sec,
            "causal_me_recovery_stop_wait_sec": args.causal_me_recovery_stop_wait_sec,
        },
        "live_rescue_shadow": rescue_shadow,
        "causal_target_me_shadow": causal_shadow,
        "causal_me_recovery_runtime": recovery_worker,
        "runtime_cost": runtime_cost,
        "progress": {
            "captured_sec": round(captured, 3),
            "preprocessed_sec": round(processed, 3),
            "asr_sec": round(processed, 3),
            "processed_sec": round(processed, 3),
            "draft_sec": round(processed, 3),
            "live_lag_sec": round(max(0.0, captured - processed), 3),
            "chunks_processed": len(chunks),
            "segments_seen": len(rows),
        },
        "outputs": {
            "draft_transcript": rel(output_dir / "transcript.draft.md", session),
            "preview_transcript": rel(output_dir / "transcript.preview.md", session),
            "preview_snapshots": rel(output_dir / "preview_snapshots.jsonl", session),
            "chunks_jsonl": rel(output_dir / "chunks.jsonl", session),
            "segments_jsonl": rel(resolve_output_path(session, args.segments_path), session),
            "causal_me_recovery_runtime": rel(
                session / "derived/live/causal-me-recovery-runtime-v1",
                session,
            ),
        },
        "recommended_next": "murmurmark process " + str(session),
    }
    write_json(output_dir / "live_pipeline_report.json", report)


def main() -> int:
    global SHUTDOWN_REQUESTED
    args = parse_args()
    session = args.session.expanduser().resolve()
    segments_path = resolve_output_path(session, args.segments_path)
    output_dir = resolve_output_path(session, args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    heartbeat = WorkerHeartbeat(
        output_dir,
        provenance=args.provenance,
        interval_sec=args.heartbeat_sec,
    )
    SHUTDOWN_REQUESTED = False
    process_signal.signal(process_signal.SIGTERM, request_worker_shutdown)
    process_signal.signal(process_signal.SIGINT, request_worker_shutdown)
    processed: set[int] = set()
    chunks: list[dict[str, Any]] = []
    last_new_work = time.monotonic()
    heartbeat.update(status="running", stage="initializing")
    write_live_views(output_dir, chunks, args.commit_delay_sec, args.provenance)
    write_report(session, output_dir, "running", chunks, [], args)
    progressive_target_me: Any | None = None
    causal_me_recovery: Any | None = None
    if not args.no_causal_target_me:
        try:
            progressive_module = load_progressive_target_me()
            progressive_target_me = progressive_module.ProgressiveTargetMeShadow(
                session,
                model=str(args.model),
                language=args.language,
                whisper_cli=args.whisper_cli,
                micro_runner=bounded_progressive_micro_runner(
                    args=args,
                    heartbeat=heartbeat,
                    progressive_module=progressive_module,
                ),
            )
        except Exception as error:
            write_json(
                session / "derived/live/causal-target-me/state.json",
                {
                    "schema": "murmurmark.live_progressive_target_me/v1",
                    "status": "failed_open",
                    "reason": str(error),
                    "batch_authoritative": True,
                    "promotion_allowed": False,
                },
            )
    if not args.no_causal_me_recovery_runtime:
        try:
            recovery_module = load_causal_me_recovery_manager()
            runtime_script_value = os.environ.get("MURMURMARK_CAUSAL_ME_RECOVERY_RUNTIME_SCRIPT")
            causal_me_recovery = recovery_module.CausalMeRecoveryManager(
                session=session,
                model=args.model,
                language=args.language,
                whisper_cli=args.whisper_cli,
                timeout_sec=args.causal_me_recovery_timeout_sec,
                max_live_lag_sec=args.causal_me_recovery_max_live_lag_sec,
                runtime_script=(Path(runtime_script_value) if runtime_script_value else None),
                register_child=register_active_child,
                unregister_child=unregister_active_child,
            )
        except Exception as error:
            try:
                write_json(
                    session / "derived/live/causal-me-recovery-runtime-v1/worker_state.json",
                    {
                        "schema": "murmurmark.live_causal_me_recovery_worker/v1",
                        "status": "failed_open",
                        "reason": str(error),
                        "normal_preview_connected": False,
                        "base_draft_fallback": True,
                        "batch_authoritative": True,
                        "promotion_allowed": False,
                    },
                )
            except OSError:
                pass

    while True:
        rows = read_jsonl(segments_path)
        grouped = grouped_segments(rows)
        ready_indexes = sorted(index for index, pair in grouped.items() if {"mic", "remote"} <= set(pair))
        captured = max((float(row.get("end_sec") or 0.0) for row in rows), default=0.0)
        if causal_me_recovery is not None:
            try:
                causal_me_recovery.poll(captured_sec=captured)
            except Exception as error:
                disable_causal_me_recovery(session, causal_me_recovery, error)
                causal_me_recovery = None
        completed = max((float(row.get("end_sec") or 0.0) for row in chunks), default=0.0)
        heartbeat.update(
            status="running",
            stage="waiting_segments" if not [index for index in ready_indexes if index not in processed] else "segments_ready",
            progress={
                "segments_seen": len(rows),
                "chunks_processed": len(chunks),
                "captured_sec": round(captured, 3),
                "processed_sec": round(completed, 3),
                "live_lag_sec": round(max(0.0, captured - completed), 3),
            },
        )
        for index in ready_indexes:
            if index in processed:
                continue
            if args.max_segments and len(processed) >= args.max_segments:
                break
            heartbeat.update(status="running", stage="chunk_start", index=index)
            base_started = time.monotonic()
            chunk = process_segment(
                session,
                index,
                grouped[index],
                args,
                output_dir,
                heartbeat,
            )
            chunk["runtime"] = {
                "base_elapsed_sec": round(time.monotonic() - base_started, 3),
                "target_me_elapsed_sec": None,
            }
            processed.add(index)
            apply_adjacent_boundary_gate(chunks[-1] if chunks else None, chunk)
            chunks.append(chunk)
            latest_rows = read_jsonl(segments_path)
            rows = latest_rows
            captured = max(
                (float(row.get("end_sec") or 0.0) for row in latest_rows),
                default=0.0,
            )
            write_chunks(output_dir, chunks)
            write_live_views(output_dir, chunks, args.commit_delay_sec, args.provenance)
            write_report(session, output_dir, "running", chunks, latest_rows, args)
            heartbeat.update(status="running", stage="base_chunk_written", index=index)
            last_new_work = time.monotonic()

            if progressive_target_me is not None:
                lag_decision = causal_target_me_lag_decision(
                    captured_sec=captured,
                    chunk_end_sec=safe_float(chunk.get("end_sec")),
                    max_live_lag_sec=args.causal_target_me_max_live_lag_sec,
                )
                if SHUTDOWN_REQUESTED:
                    chunk["mic"]["causal_target_me_shadow"] = {
                        "schema": "murmurmark.live_progressive_target_me/v1",
                        "status": "skipped_shutdown",
                        "reason": "worker shutdown requested after base draft was written",
                        "batch_authoritative": True,
                        "promotion_allowed": False,
                    }
                    heartbeat.update(
                        status="running",
                        stage="target_me_skipped_shutdown",
                        index=index,
                    )
                elif not lag_decision["run"]:
                    chunk["mic"]["causal_target_me_shadow"] = {
                        "schema": "murmurmark.live_progressive_target_me/v1",
                        "status": "skipped_lag_budget",
                        "reason": lag_decision["reason"],
                        "observed_live_lag_sec": lag_decision["observed_live_lag_sec"],
                        "max_live_lag_sec": lag_decision["max_live_lag_sec"],
                        "batch_authoritative": True,
                        "promotion_allowed": False,
                    }
                    heartbeat.update(
                        status="running",
                        stage="target_me_skipped_lag_budget",
                        index=index,
                        detail=(
                            f"lag={lag_decision['observed_live_lag_sec']:.1f}s "
                            f"budget={lag_decision['max_live_lag_sec']:.1f}s"
                        ),
                    )
                else:
                    heartbeat.update(
                        status="running",
                        stage="target_me_start",
                        index=index,
                        detail=f"lag={lag_decision['observed_live_lag_sec']:.1f}s",
                    )
                    target_me_started = time.monotonic()
                    try:
                        progressive_target_me.process_chunk(chunk)
                    except Exception as error:
                        chunk["mic"]["causal_target_me_shadow"] = {
                            "schema": "murmurmark.live_progressive_target_me/v1",
                            "status": "failed_open",
                            "reason": str(error),
                            "batch_authoritative": True,
                            "promotion_allowed": False,
                        }
                    chunk["runtime"]["target_me_elapsed_sec"] = round(
                        time.monotonic() - target_me_started,
                        3,
                    )
                    heartbeat.update(status="running", stage="target_me_written", index=index)
                write_chunks(output_dir, chunks)
                write_live_views(output_dir, chunks, args.commit_delay_sec, args.provenance)
                write_report(session, output_dir, "running", chunks, latest_rows, args)

            if causal_me_recovery is not None:
                try:
                    causal_me_recovery.submit(
                        chunk_index=index,
                        chunk_end_sec=safe_float(chunk.get("end_sec")),
                        captured_sec=captured,
                        recording_active=not (session / "session.json").exists(),
                    )
                except Exception as error:
                    disable_causal_me_recovery(session, causal_me_recovery, error)
                    causal_me_recovery = None
                write_report(session, output_dir, "running", chunks, latest_rows, args)

        if SHUTDOWN_REQUESTED:
            status = "completed_partial_draft"
            completed = max((float(row.get("end_sec") or 0.0) for row in chunks), default=0.0)
            if causal_me_recovery is not None:
                try:
                    causal_me_recovery.finish(
                        captured_sec=captured,
                        wait_sec=args.causal_me_recovery_stop_wait_sec,
                    )
                except Exception as error:
                    disable_causal_me_recovery(session, causal_me_recovery, error)
                    causal_me_recovery = None
            write_live_views(output_dir, chunks, args.commit_delay_sec, args.provenance)
            write_report(session, output_dir, status, chunks, rows, args)
            heartbeat.update(
                status=status,
                stage="terminated",
                detail="worker shutdown requested",
                progress={
                    "segments_seen": len(rows),
                    "chunks_processed": len(chunks),
                    "captured_sec": round(captured, 3),
                    "processed_sec": round(completed, 3),
                    "live_lag_sec": round(max(0.0, captured - completed), 3),
                },
            )
            return 0

        session_finished = (session / "session.json").exists()
        all_ready_done = all(index in processed for index in ready_indexes)
        idle_after_finish = session_finished and all_ready_done and (time.monotonic() - last_new_work >= args.idle_after_session_json_sec)
        if idle_after_finish or (args.max_segments and len(processed) >= args.max_segments):
            status = "completed" if session_finished else "stopped_by_limit"
            if causal_me_recovery is not None:
                try:
                    causal_me_recovery.finish(
                        captured_sec=captured,
                        wait_sec=args.causal_me_recovery_stop_wait_sec,
                    )
                except Exception as error:
                    disable_causal_me_recovery(session, causal_me_recovery, error)
                    causal_me_recovery = None
            write_live_views(output_dir, chunks, args.commit_delay_sec, args.provenance)
            write_report(session, output_dir, status, chunks, rows, args)
            if progressive_target_me is not None:
                progressive_target_me.persist(status="completed")
            completed = max((float(row.get("end_sec") or 0.0) for row in chunks), default=0.0)
            heartbeat.update(
                status=status,
                stage="completed",
                progress={
                    "segments_seen": len(rows),
                    "chunks_processed": len(chunks),
                    "captured_sec": round(captured, 3),
                    "processed_sec": round(completed, 3),
                    "live_lag_sec": round(max(0.0, captured - completed), 3),
                },
            )
            return 0

        write_report(session, output_dir, "running", chunks, rows, args)
        time.sleep(max(0.2, args.poll_sec))


if __name__ == "__main__":
    raise SystemExit(main())
