#!/usr/bin/env python3
"""Core helpers for the private Speaker-Preserving Echo Adaptation Corpus v1."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
import soundfile as sf
from scipy.io import wavfile


SAMPLE_RATE = 16_000


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def digest_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint(path: Path, root: Path) -> dict[str, Any]:
    return {
        "path": relative(path, root),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
    }


def relative(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path.resolve())


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return payload


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise RuntimeError(f"expected JSON object at {path}:{line_number}")
        rows.append(payload)
    return rows


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def stable_id(*parts: Any, length: int = 16) -> str:
    digest = hashlib.sha256(
        "\x1f".join(str(part) for part in parts).encode("utf-8")
    ).hexdigest()
    return digest[:length]


def text_evidence(text: Any) -> dict[str, Any]:
    normalized = " ".join(str(text or "").lower().split())
    tokens = [token for token in normalized.split() if token]
    return {
        "present": bool(tokens),
        "token_count": len(tokens),
        "sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
    }


def rms(audio: np.ndarray) -> float:
    if audio.size == 0:
        return 0.0
    return math.sqrt(float(np.mean(np.asarray(audio, dtype=np.float64) ** 2)) + 1.0e-15)


def rms_db(audio: np.ndarray) -> float:
    return 20.0 * math.log10(rms(audio) + 1.0e-12)


def normalized_corr(left: np.ndarray, right: np.ndarray) -> float:
    count = min(left.size, right.size)
    if count < 2:
        return 0.0
    a = np.asarray(left[:count], dtype=np.float64)
    b = np.asarray(right[:count], dtype=np.float64)
    a -= float(np.mean(a))
    b -= float(np.mean(b))
    denominator = math.sqrt(float(np.dot(a, a) * np.dot(b, b)))
    if denominator <= 1.0e-15:
        return 0.0
    return float(np.dot(a, b) / denominator)


def audio_metrics(mic: np.ndarray, remote: np.ndarray) -> dict[str, Any]:
    count = min(mic.size, remote.size)
    mic = np.asarray(mic[:count], dtype=np.float32)
    remote = np.asarray(remote[:count], dtype=np.float32)
    return {
        "samples": int(count),
        "duration_sec": round(count / SAMPLE_RATE, 6),
        "finite": bool(np.all(np.isfinite(mic)) and np.all(np.isfinite(remote))),
        "mic_peak": round(float(np.max(np.abs(mic))) if count else 0.0, 9),
        "remote_peak": round(float(np.max(np.abs(remote))) if count else 0.0, 9),
        "mic_clipped_ratio": round(
            float(np.mean(np.abs(mic) >= 0.995)) if count else 0.0,
            12,
        ),
        "remote_clipped_ratio": round(
            float(np.mean(np.abs(remote) >= 0.995)) if count else 0.0,
            12,
        ),
        "mic_rms_db": round(rms_db(mic), 6),
        "remote_rms_db": round(rms_db(remote), 6),
        "abs_remote_correlation": round(abs(normalized_corr(mic, remote)), 9),
    }


def read_audio_slice(
    path: Path,
    start_sec: float,
    end_sec: float,
    *,
    expected_rate: int = SAMPLE_RATE,
) -> np.ndarray:
    start = max(0, int(round(start_sec * expected_rate)))
    end = max(start, int(round(end_sec * expected_rate)))
    with sf.SoundFile(path) as handle:
        if handle.samplerate != expected_rate:
            raise RuntimeError(
                f"unexpected sample rate for {path}: {handle.samplerate} != {expected_rate}"
            )
        if handle.channels != 1:
            raise RuntimeError(f"expected mono audio: {path}")
        handle.seek(start)
        audio = handle.read(end - start, dtype="float32", always_2d=False)
    return np.asarray(audio, dtype=np.float32)


def write_audio(path: Path, audio: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    wavfile.write(path, SAMPLE_RATE, np.asarray(audio, dtype=np.float32))


def overlap_seconds(
    start: float,
    end: float,
    utterance: dict[str, Any],
) -> float:
    left = max(start, float(utterance.get("start", 0.0)))
    right = min(end, float(utterance.get("end", 0.0)))
    return max(0.0, right - left)


def role_evidence(
    start: float,
    end: float,
    utterances: Sequence[dict[str, Any]],
    role: str,
    minimum_confidence: float,
) -> dict[str, Any]:
    accepted: list[tuple[float, float, dict[str, Any]]] = []
    text_rows: list[dict[str, Any]] = []
    for utterance in utterances:
        if str(utterance.get("role") or "").lower() != role:
            continue
        quality = utterance.get("quality") if isinstance(utterance.get("quality"), dict) else {}
        if bool(quality.get("needs_review")):
            continue
        if float(quality.get("role_confidence") or 0.0) < minimum_confidence:
            continue
        text = text_evidence(utterance.get("text"))
        if not text["present"]:
            continue
        left = max(start, float(utterance.get("start", 0.0)))
        right = min(end, float(utterance.get("end", 0.0)))
        if right <= left:
            continue
        accepted.append((left, right, utterance))
        text_rows.append(
            {
                "utterance_id": utterance.get("id"),
                "role_confidence": quality.get("role_confidence"),
                "text": text,
            }
        )
    intervals = sorted((left, right) for left, right, _ in accepted)
    covered = 0.0
    cursor: float | None = None
    current_end = 0.0
    for left, right in intervals:
        if cursor is None:
            cursor = left
            current_end = right
            continue
        if left <= current_end:
            current_end = max(current_end, right)
        else:
            covered += current_end - cursor
            cursor = left
            current_end = right
    if cursor is not None:
        covered += current_end - cursor
    duration = max(end - start, 1.0e-12)
    return {
        "coverage_ratio": round(covered / duration, 9),
        "utterances": text_rows,
    }


def assign_session_splits(
    sessions: Sequence[dict[str, Any]],
    policy: dict[str, Any],
    policy_sha256: str,
) -> list[dict[str, Any]]:
    hard_sessions = set(policy["hard_test"]["sessions"])
    assignments: dict[str, str] = {
        str(row["session"]): "hard_test"
        for row in sessions
        if str(row["session"]) in hard_sessions
    }
    remaining = [
        row for row in sessions if str(row["session"]) not in hard_sessions
    ]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in remaining:
        mode = str(row.get("acoustic_mode", {}).get("mode") or "uncertain")
        grouped.setdefault(mode, []).append(row)
    for mode in sorted(grouped):
        group = sorted(
            grouped[mode],
            key=lambda row: hashlib.sha256(
                f"{policy_sha256}:{mode}:{row['session']}".encode("utf-8")
            ).hexdigest(),
        )
        if mode != "no_speech" and len(group) >= 2:
            assignments[str(group[0]["session"])] = "dev"
            group = group[1:]
        for row in group:
            assignments[str(row["session"])] = "train"
    return [
        {
            "session": str(row["session"]),
            "acoustic_mode": str(row.get("acoustic_mode", {}).get("mode") or "uncertain"),
            "split": assignments[str(row["session"])],
        }
        for row in sorted(sessions, key=lambda item: str(item["session"]))
    ]


def validate_splits(
    assignments: Sequence[dict[str, Any]],
    policy: dict[str, Any],
) -> dict[str, Any]:
    by_session: dict[str, set[str]] = {}
    for row in assignments:
        by_session.setdefault(str(row["session"]), set()).add(str(row["split"]))
    overlaps = sorted(session for session, splits in by_session.items() if len(splits) != 1)
    hard_sessions = set(policy["hard_test"]["sessions"])
    observed_hard = {
        str(row["session"])
        for row in assignments
        if str(row["split"]) == "hard_test"
    }
    counts = {
        split: sum(1 for row in assignments if row["split"] == split)
        for split in ("train", "dev", "hard_test")
    }
    speaker_counts = {
        split: sum(
            1
            for row in assignments
            if row["split"] == split and row["acoustic_mode"] == "speaker_playback"
        )
        for split in ("train", "dev", "hard_test")
    }
    split_policy = policy["split"]
    gates = {
        "session_disjoint": not overlaps,
        "hard_test_exact": observed_hard == hard_sessions,
        "minimum_train_sessions": counts["train"]
        >= int(split_policy["minimum_train_sessions"]),
        "minimum_dev_sessions": counts["dev"]
        >= int(split_policy["minimum_dev_sessions"]),
        "minimum_hard_test_sessions": counts["hard_test"]
        >= int(split_policy["minimum_hard_test_sessions"]),
        "minimum_train_speaker_playback_sessions": speaker_counts["train"]
        >= int(split_policy["minimum_train_speaker_playback_sessions"]),
        "minimum_dev_speaker_playback_sessions": speaker_counts["dev"]
        >= int(split_policy["minimum_dev_speaker_playback_sessions"]),
    }
    return {
        "assignments": list(assignments),
        "counts": counts,
        "speaker_playback_counts": speaker_counts,
        "overlapping_sessions": overlaps,
        "gates": gates,
        "passed": all(gates.values()),
    }


def union_seconds(rows: Iterable[dict[str, Any]]) -> float:
    by_session: dict[str, list[tuple[float, float]]] = {}
    for row in rows:
        by_session.setdefault(str(row["session"]), []).append(
            (float(row["start"]), float(row["end"]))
        )
    total = 0.0
    for intervals in by_session.values():
        cursor: float | None = None
        current_end = 0.0
        for start, end in sorted(intervals):
            if cursor is None:
                cursor = start
                current_end = end
            elif start <= current_end:
                current_end = max(current_end, end)
            else:
                total += current_end - cursor
                cursor = start
                current_end = end
        if cursor is not None:
            total += current_end - cursor
    return round(total, 6)


def make_synthetic_pair(
    target: np.ndarray,
    measured_echo: np.ndarray,
    *,
    gain_db: float,
    maximum_peak: float,
) -> tuple[dict[str, np.ndarray] | None, dict[str, Any]]:
    count = min(target.size, measured_echo.size)
    target = np.asarray(target[:count], dtype=np.float32)
    measured_echo = np.asarray(measured_echo[:count], dtype=np.float32)
    gain = float(10.0 ** (gain_db / 20.0))
    echo_component = measured_echo * gain
    mixture = target + echo_component
    finite = bool(
        np.all(np.isfinite(target))
        and np.all(np.isfinite(echo_component))
        and np.all(np.isfinite(mixture))
    )
    peak = float(np.max(np.abs(mixture))) if count else 0.0
    reconstruction_error = (
        float(np.max(np.abs(mixture - target - echo_component))) if count else 0.0
    )
    report = {
        "samples": int(count),
        "duration_sec": round(count / SAMPLE_RATE, 6),
        "gain_db": gain_db,
        "finite": finite,
        "peak": round(peak, 9),
        "reconstruction_max_abs_error": round(reconstruction_error, 12),
        "mixture_target_correlation": round(
            abs(normalized_corr(mixture, target)),
            9,
        ),
    }
    if not finite:
        report["reason"] = "non_finite"
        return None, report
    if peak > maximum_peak:
        report["reason"] = "would_clip_without_normalization"
        return None, report
    return {
        "target": target,
        "echo_component": echo_component,
        "mixture": mixture,
    }, report
