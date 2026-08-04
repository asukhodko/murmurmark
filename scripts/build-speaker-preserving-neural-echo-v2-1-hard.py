#!/usr/bin/env python3
"""Build an immutable ordinary-session hard set for neural echo candidate v2.1."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
from scipy import signal

import speaker_preserving_neural_echo_v2 as CORE
from echo_promotion_timeline import align_remote_constant


ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "sessions/_reports/speaker-preserving-neural-echo-v2-1-hard"
LOCAL_SESSION = ROOT / "sessions/2026-07-16_11-15-15-live"
REMOTE_SESSION = ROOT / "sessions/2026-07-20_15-15-26-live"
LOCAL_AUDIO = LOCAL_SESSION / "derived/preprocess/audio/mic_clean_local_fir.wav"
LOCAL_STATE = LOCAL_SESSION / "derived/preprocess/echo/speaker_state.jsonl"
LOCAL_DIALOGUE = (
    LOCAL_SESSION
    / "derived/transcript-simple/whisper-cpp/resolved/clean_dialogue.agent_reviewed_v1.json"
)
REMOTE_MIC = REMOTE_SESSION / "derived/preprocess/audio/mic_raw_for_asr.wav"
REMOTE_AUDIO = REMOTE_SESSION / "derived/preprocess/audio/remote_for_aec.wav"
REMOTE_STATE = REMOTE_SESSION / "derived/preprocess/echo/speaker_state.jsonl"
REMOTE_FIR_REPORT = REMOTE_SESSION / "derived/preprocess/echo/local_fir_report.json"
GAINS_DB = (-6.0, -3.0, 0.0)
CLIP_SEC = 4.0
CLIP_SAMPLES = int(CORE.SAMPLE_RATE * CLIP_SEC)


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return payload


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def read_slice(path: Path, start_sec: float, duration_sec: float) -> np.ndarray:
    with sf.SoundFile(path) as handle:
        source_rate = int(handle.samplerate)
        handle.seek(max(0, int(round(start_sec * source_rate))))
        values = handle.read(
            int(round(duration_sec * source_rate)),
            dtype="float32",
            always_2d=False,
        )
    values = np.asarray(values, dtype=np.float32)
    if values.ndim != 1:
        raise RuntimeError(f"expected mono audio: {path}")
    if source_rate != CORE.SAMPLE_RATE:
        divisor = math.gcd(source_rate, CORE.SAMPLE_RATE)
        values = signal.resample_poly(
            values,
            CORE.SAMPLE_RATE // divisor,
            source_rate // divisor,
        ).astype(np.float32)
    if values.size < CLIP_SAMPLES:
        values = np.pad(values, (0, CLIP_SAMPLES - values.size))
    return values[:CLIP_SAMPLES].astype(np.float32)


def source_fingerprint(path: Path) -> dict[str, Any]:
    return {
        "path": str(path.relative_to(ROOT)),
        "bytes": path.stat().st_size,
        "sha256": CORE.sha256(path),
    }


def rows_covering(
    states: list[dict[str, Any]], start_sec: float, end_sec: float
) -> list[dict[str, Any]]:
    return [
        row
        for row in states
        if float(row["start"]) < end_sec and float(row["end"]) > start_sec
    ]


def local_windows() -> list[dict[str, Any]]:
    states = read_jsonl(LOCAL_STATE)
    utterances = read_json(LOCAL_DIALOGUE).get("utterances", [])
    windows: list[dict[str, Any]] = []
    for utterance in utterances:
        if utterance.get("role") != "me" or utterance.get("quality", {}).get("needs_review"):
            continue
        start = float(utterance["start"])
        end = float(utterance["end"])
        candidate = start
        while candidate + CLIP_SEC <= end + 1.0e-6:
            rows = rows_covering(states, candidate, candidate + CLIP_SEC)
            if (
                len(rows) >= 2
                and all(
                    row.get("state") == "local_only"
                    and float(row.get("remote_db") or -120.0) <= -40.0
                    and float(row.get("mic_db") or -120.0) >= -50.0
                    for row in rows
                )
                and all(
                    candidate >= row["end"] + CLIP_SEC
                    or candidate + CLIP_SEC <= row["start"] - CLIP_SEC
                    for row in windows
                )
            ):
                windows.append(
                    {
                        "start": round(candidate, 6),
                        "end": round(candidate + CLIP_SEC, 6),
                        "utterance_id": utterance["id"],
                        "text_sha256": __import__("hashlib").sha256(
                            str(utterance.get("text") or "").encode("utf-8")
                        ).hexdigest(),
                    }
                )
            candidate += CLIP_SEC
    if len(windows) < 9:
        raise RuntimeError(f"only {len(windows)} frozen local windows passed")
    return windows[:9]


def remote_windows() -> list[dict[str, Any]]:
    states = read_jsonl(REMOTE_STATE)
    delay_ms = float(read_json(REMOTE_FIR_REPORT).get("parameters", {}).get("delay_ms") or 0.0)
    candidates: list[dict[str, Any]] = []
    for row in states:
        start = float(row["start"])
        end = start + CLIP_SEC
        covered = rows_covering(states, start, end)
        if len(covered) < 2 or not all(
            item.get("state") == "remote_only"
            and float(item.get("confidence") or 0.0) >= 0.7
            and -45.0 <= float(item.get("mic_db") or -120.0) <= -20.0
            and float(item.get("remote_db") or -120.0) >= -35.0
            for item in covered
        ):
            continue
        mic = read_slice(REMOTE_MIC, start, CLIP_SEC)
        remote = align_remote_constant(
            read_slice(REMOTE_AUDIO, start, CLIP_SEC),
            CORE.SAMPLE_RATE,
            delay_ms,
        ).astype(np.float32)
        correlation = abs(CORE.correlation(mic, remote))
        if correlation < 0.15:
            continue
        candidates.append(
            {
                "start": round(start, 6),
                "end": round(end, 6),
                "delay_ms": delay_ms,
                "correlation": round(correlation, 6),
            }
        )
    if len(candidates) < 9:
        raise RuntimeError(f"only {len(candidates)} frozen remote windows passed")
    indices = np.linspace(0, len(candidates) - 1, 9, dtype=int)
    return [candidates[int(index)] for index in indices]


def build() -> dict[str, Any]:
    manifest_path = OUTPUT / "hard_manifest.json"
    cache_path = OUTPUT / "hard_waveforms.npz"
    if manifest_path.exists() or cache_path.exists():
        if not manifest_path.is_file() or not cache_path.is_file():
            raise RuntimeError("partial v2.1 hard set exists")
        manifest = read_json(manifest_path)
        if CORE.sha256(cache_path) != manifest.get("cache", {}).get("sha256"):
            raise RuntimeError("frozen v2.1 hard cache changed")
        return manifest
    local = local_windows()
    remote = remote_windows()
    rows: list[dict[str, Any]] = []
    waveforms: list[np.ndarray] = []
    for index, (local_row, remote_row) in enumerate(zip(local, remote), 1):
        target = read_slice(LOCAL_AUDIO, float(local_row["start"]), CLIP_SEC)
        measured_echo = read_slice(REMOTE_MIC, float(remote_row["start"]), CLIP_SEC)
        aligned_remote = align_remote_constant(
            read_slice(REMOTE_AUDIO, float(remote_row["start"]), CLIP_SEC),
            CORE.SAMPLE_RATE,
            float(remote_row["delay_ms"]),
        ).astype(np.float32)
        for gain_db in GAINS_DB:
            gain = 10.0 ** (gain_db / 20.0)
            mixture = target + gain * measured_echo
            _, full_echo_estimate = CORE.fir_residual(
                mixture,
                aligned_remote,
                measured_echo,
                gain,
            )
            before_correlation = abs(CORE.correlation(aligned_remote, mixture))
            strength = (
                CORE.DOUBLE_TALK_HIGH_STRENGTH
                if before_correlation >= CORE.DOUBLE_TALK_HIGH_CORRELATION_THRESHOLD
                else CORE.DOUBLE_TALK_STRENGTH
            )
            echo_estimate = strength * full_echo_estimate
            baseline = mixture - echo_estimate
            item_id = f"ordinary_hard_{index:02d}_{int(gain_db):+03d}db"
            waveforms.append(
                np.stack((baseline, aligned_remote, target, echo_estimate)).astype(np.float32)
            )
            rows.append(
                {
                    "item_id": item_id,
                    "local_window": local_row,
                    "remote_window": remote_row,
                    "gain_db": gain_db,
                    "gain_linear": round(gain, 9),
                    "fir_strength": strength,
                    "baseline_target_snr_db": round(CORE.snr_db(target, baseline), 6),
                    "source_fingerprint": CORE.digest_json(
                        {
                            "local": local_row,
                            "remote": remote_row,
                            "gain_db": gain_db,
                        }
                    ),
                }
            )
    OUTPUT.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(cache_path, waveforms=np.stack(waveforms))
    manifest = {
        "schema": "murmurmark.speaker_preserving_neural_echo_hard_set/v2.1",
        "status": "sealed_unopened",
        "training_use": "forbidden",
        "selection_use": "forbidden",
        "evaluation_attempts_allowed": 1,
        "evaluation_attempts_consumed": 0,
        "sources": {
            "local_session": LOCAL_SESSION.name,
            "remote_session": REMOTE_SESSION.name,
            "artifacts": [
                source_fingerprint(path)
                for path in (
                    LOCAL_AUDIO,
                    LOCAL_STATE,
                    LOCAL_DIALOGUE,
                    REMOTE_MIC,
                    REMOTE_AUDIO,
                    REMOTE_STATE,
                    REMOTE_FIR_REPORT,
                )
            ],
        },
        "construction": {
            "sample_rate": CORE.SAMPLE_RATE,
            "clip_sec": CLIP_SEC,
            "local_windows": local,
            "remote_windows": remote,
            "gains_db": list(GAINS_DB),
            "preparation": "production_preserve_local_fir_80ms_reg_1e-2_v2",
        },
        "items": rows,
        "cache": {
            "path": str(cache_path.relative_to(ROOT)),
            "bytes": cache_path.stat().st_size,
            "sha256": CORE.sha256(cache_path),
            "shape": list(np.stack(waveforms).shape),
        },
    }
    manifest["fingerprint"] = CORE.digest_json(
        {
            "sources": manifest["sources"],
            "construction": manifest["construction"],
            "items": [row["source_fingerprint"] for row in rows],
            "cache_sha256": manifest["cache"]["sha256"],
        }
    )
    CORE.write_json(manifest_path, manifest)
    return manifest


def main() -> int:
    manifest = build()
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
