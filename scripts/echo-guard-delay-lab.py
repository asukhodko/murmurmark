#!/usr/bin/env python3
"""Offline delay/confidence lab for Echo Guard.

This is an investigation tool, not an audio cleanup engine. It estimates how a
known remote reference aligns with the leaked remote component inside the mic
track, then writes JSON artifacts that can guide later adaptive filtering work.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
from scipy import signal
from scipy.fft import irfft, next_fast_len, rfft
from scipy.io import wavfile


EPSILON = 1.0e-12


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Estimate remote-to-mic delay confidence for an Echo Guard session.",
    )
    parser.add_argument("session", type=Path, help="MurmurMark session directory")
    parser.add_argument("--sample-rate", type=int, default=16_000)
    parser.add_argument("--window-sec", type=float, default=10.0)
    parser.add_argument("--hop-sec", type=float, default=5.0)
    parser.add_argument("--min-delay-ms", type=float, default=-500.0)
    parser.add_argument("--max-delay-ms", type=float, default=2_000.0)
    parser.add_argument("--highpass-hz", type=float, default=120.0)
    parser.add_argument("--remote-margin-db", type=float, default=10.0)
    parser.add_argument("--mic-margin-db", type=float, default=8.0)
    parser.add_argument("--min-remote-db", type=float, default=-55.0)
    parser.add_argument("--min-mic-db", type=float, default=-60.0)
    parser.add_argument("--min-confidence", type=float, default=1.25)
    parser.add_argument("--second-peak-radius-ms", type=float, default=50.0)
    parser.add_argument("--max-windows", type=int, default=0, help="0 means all windows")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Default: <session>/derived/preprocess/echo/lab",
    )
    return parser.parse_args()


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


def resample_if_needed(audio: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    if source_rate == target_rate:
        return audio
    divisor = math.gcd(source_rate, target_rate)
    up = target_rate // divisor
    down = source_rate // divisor
    return signal.resample_poly(audio, up, down).astype(np.float32)


def highpass(audio: np.ndarray, sample_rate: int, cutoff_hz: float) -> np.ndarray:
    if cutoff_hz <= 0:
        return audio
    sos = signal.butter(4, cutoff_hz, btype="highpass", fs=sample_rate, output="sos")
    return signal.sosfilt(sos, audio).astype(np.float32)


def rms_db(audio: np.ndarray) -> float:
    rms = float(np.sqrt(np.mean(np.square(audio), dtype=np.float64) + EPSILON))
    return 20.0 * math.log10(rms + EPSILON)


def safe_percentile(values: list[float], percentile: float, fallback: float) -> float:
    if not values:
        return fallback
    finite = np.array([value for value in values if math.isfinite(value)], dtype=np.float64)
    if finite.size == 0:
        return fallback
    return float(np.percentile(finite, percentile))


def gcc_phat(
    mic: np.ndarray,
    remote: np.ndarray,
    sample_rate: int,
    min_delay_ms: float,
    max_delay_ms: float,
    second_peak_radius_ms: float,
) -> dict[str, Any]:
    mic = mic.astype(np.float32, copy=False)
    remote = remote.astype(np.float32, copy=False)
    mic = mic - float(np.mean(mic))
    remote = remote - float(np.mean(remote))

    taper = np.hanning(min(len(mic), len(remote))).astype(np.float32)
    mic = mic[: taper.size] * taper
    remote = remote[: taper.size] * taper

    fft_size = next_fast_len(len(mic) + len(remote) - 1)
    mic_fft = rfft(mic, fft_size)
    remote_fft = rfft(remote, fft_size)
    cross_power = mic_fft * np.conj(remote_fft)
    cross_power /= np.maximum(np.abs(cross_power), EPSILON)

    corr = irfft(cross_power, fft_size)
    corr = np.concatenate((corr[-(len(remote) - 1) :], corr[: len(mic)]))
    lags = np.arange(-len(remote) + 1, len(mic))

    min_lag = int(round(min_delay_ms * sample_rate / 1_000.0))
    max_lag = int(round(max_delay_ms * sample_rate / 1_000.0))
    lag_mask = (lags >= min_lag) & (lags <= max_lag)
    limited_corr = corr[lag_mask]
    limited_lags = lags[lag_mask]

    if limited_corr.size == 0:
        raise ValueError("delay search range produced no correlation samples")

    abs_corr = np.abs(limited_corr)
    peak_index = int(np.argmax(abs_corr))
    peak_lag = int(limited_lags[peak_index])
    peak_value = float(abs_corr[peak_index])
    signed_peak = float(limited_corr[peak_index])

    exclusion_radius = int(round(second_peak_radius_ms * sample_rate / 1_000.0))
    second_mask = np.abs(limited_lags - peak_lag) > exclusion_radius
    second_peak = float(np.max(abs_corr[second_mask])) if np.any(second_mask) else 0.0
    confidence = peak_value / (second_peak + EPSILON)

    return {
        "delay_ms": round(peak_lag * 1_000.0 / sample_rate, 3),
        "peak": round(peak_value, 6),
        "signed_peak": round(signed_peak, 6),
        "second_peak": round(second_peak, 6),
        "confidence": round(confidence, 4),
        "polarity": "inverted" if signed_peak < 0 else "normal",
    }


def default_audio_paths(session: Path) -> tuple[Path, Path]:
    preprocess_audio = session / "derived" / "preprocess" / "audio"
    return preprocess_audio / "remote_for_aec.wav", preprocess_audio / "mic_raw_for_asr.wav"


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    session = args.session
    remote_path, mic_path = default_audio_paths(session)
    if not remote_path.exists() or not mic_path.exists():
        raise SystemExit(
            "working audio not found; run: murmurmark preprocess "
            f"{session} --echo diagnostic"
        )

    out_dir = args.out_dir or session / "derived" / "preprocess" / "echo" / "lab"
    out_dir.mkdir(parents=True, exist_ok=True)

    remote_rate, remote = read_wav_float(remote_path)
    mic_rate, mic = read_wav_float(mic_path)
    remote = resample_if_needed(remote, remote_rate, args.sample_rate)
    mic = resample_if_needed(mic, mic_rate, args.sample_rate)
    remote = highpass(remote, args.sample_rate, args.highpass_hz)
    mic = highpass(mic, args.sample_rate, args.highpass_hz)

    total_samples = min(remote.size, mic.size)
    remote = remote[:total_samples]
    mic = mic[:total_samples]

    window_samples = int(round(args.window_sec * args.sample_rate))
    hop_samples = int(round(args.hop_sec * args.sample_rate))
    if window_samples <= 0 or hop_samples <= 0:
        raise SystemExit("window and hop must be positive")
    if total_samples < window_samples:
        raise SystemExit("audio is shorter than one analysis window")

    starts = list(range(0, total_samples - window_samples + 1, hop_samples))
    if args.max_windows > 0:
        starts = starts[: args.max_windows]

    remote_levels: list[float] = []
    mic_levels: list[float] = []
    for start in starts:
        stop = start + window_samples
        remote_levels.append(rms_db(remote[start:stop]))
        mic_levels.append(rms_db(mic[start:stop]))

    remote_floor_db = safe_percentile(remote_levels, 20.0, -80.0)
    mic_floor_db = safe_percentile(mic_levels, 20.0, -80.0)
    remote_threshold_db = max(remote_floor_db + args.remote_margin_db, args.min_remote_db)
    mic_threshold_db = max(mic_floor_db + args.mic_margin_db, args.min_mic_db)

    windows: list[dict[str, Any]] = []
    for index, start in enumerate(starts):
        stop = start + window_samples
        start_sec = start / args.sample_rate
        remote_db = remote_levels[index]
        mic_db = mic_levels[index]
        remote_active = remote_db >= remote_threshold_db
        mic_active = mic_db >= mic_threshold_db
        state = "far_end_candidate" if remote_active and mic_active else "inactive_or_local"

        result: dict[str, Any] = {
            "index": index,
            "start_sec": round(start_sec, 3),
            "end_sec": round(stop / args.sample_rate, 3),
            "remote_db": round(remote_db, 3),
            "mic_db": round(mic_db, 3),
            "remote_active": remote_active,
            "mic_active": mic_active,
            "state": state,
            "reliable": False,
        }

        if remote_active and mic_active:
            estimate = gcc_phat(
                mic[start:stop],
                remote[start:stop],
                args.sample_rate,
                args.min_delay_ms,
                args.max_delay_ms,
                args.second_peak_radius_ms,
            )
            result.update(estimate)
            result["reliable"] = estimate["confidence"] >= args.min_confidence

        windows.append(result)

    reliable = [window for window in windows if window.get("reliable")]
    reliable_delays = [float(window["delay_ms"]) for window in reliable]
    candidate_clips = sorted(
        reliable,
        key=lambda item: (float(item.get("confidence", 0.0)), float(item.get("peak", 0.0))),
        reverse=True,
    )[:20]

    summary = {
        "windows_total": len(windows),
        "far_end_candidate_windows": sum(1 for window in windows if window["state"] == "far_end_candidate"),
        "reliable_windows": len(reliable),
        "median_delay_ms": round(float(np.median(reliable_delays)), 3) if reliable_delays else None,
        "delay_p10_ms": round(float(np.percentile(reliable_delays, 10)), 3) if reliable_delays else None,
        "delay_p90_ms": round(float(np.percentile(reliable_delays, 90)), 3) if reliable_delays else None,
        "remote_floor_db": round(remote_floor_db, 3),
        "mic_floor_db": round(mic_floor_db, 3),
        "remote_threshold_db": round(remote_threshold_db, 3),
        "mic_threshold_db": round(mic_threshold_db, 3),
        "sample_rate": args.sample_rate,
    }

    delay_map = {
        "schema": "murmurmark.echo_delay_lab/v1",
        "session": str(session),
        "delay_ms_semantics": "positive means mic lags remote; negative means mic leads remote in the decoded files",
        "inputs": {
            "remote": str(remote_path),
            "mic": str(mic_path),
        },
        "parameters": {
            "sample_rate": args.sample_rate,
            "window_sec": args.window_sec,
            "hop_sec": args.hop_sec,
            "min_delay_ms": args.min_delay_ms,
            "max_delay_ms": args.max_delay_ms,
            "highpass_hz": args.highpass_hz,
            "min_confidence": args.min_confidence,
        },
        "summary": summary,
        "candidate_clips": candidate_clips,
    }

    delay_map_path = out_dir / "delay_map.json"
    windows_path = out_dir / "delay_windows.jsonl"
    candidates_path = out_dir / "candidate_clips.jsonl"
    write_json(delay_map_path, delay_map)
    with windows_path.open("w", encoding="utf-8") as handle:
        for window in windows:
            handle.write(json.dumps(window, ensure_ascii=False) + "\n")
    with candidates_path.open("w", encoding="utf-8") as handle:
        for clip in candidate_clips:
            handle.write(json.dumps(clip, ensure_ascii=False) + "\n")

    print(f"delay lab: {delay_map_path}")
    print(f"windows: {windows_path}")
    print(f"candidate clips: {candidates_path}")
    print(f"reliable_windows: {summary['reliable_windows']}/{summary['windows_total']}")
    print(f"median_delay_ms: {summary['median_delay_ms']}")
    print(f"delay_p10_p90_ms: {summary['delay_p10_ms']}..{summary['delay_p90_ms']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
