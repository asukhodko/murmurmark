#!/usr/bin/env python3
"""Local FIR subtraction lab for one Echo Guard clip.

This script fits an FIR echo model on the first N seconds of a selected clip and
applies it to the whole clip. It is intended for quick listening tests on
manually confirmed examples, not for assembling mic_for_asr.wav.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
from scipy import linalg, signal
from scipy.io import wavfile


EPSILON = 1.0e-12


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run local FIR subtraction on a selected clip.")
    parser.add_argument("session", type=Path, help="MurmurMark session directory")
    parser.add_argument("--start-sec", type=float, required=True)
    parser.add_argument("--duration-sec", type=float, default=8.0)
    parser.add_argument("--fit-sec", type=float, default=2.0)
    parser.add_argument("--delay-ms", type=float, default=None)
    parser.add_argument("--lab-dir", type=Path, default=None)
    parser.add_argument("--sample-rate", type=int, default=16_000)
    parser.add_argument("--highpass-hz", type=float, default=120.0)
    parser.add_argument("--tail-ms", type=float, default=80.0)
    parser.add_argument("--regularization", type=float, default=1.0e-2)
    parser.add_argument("--strength", type=float, default=1.0)
    parser.add_argument("--out-dir", type=Path, default=None)
    return parser.parse_args()


def default_audio_paths(session: Path) -> tuple[Path, Path]:
    preprocess_audio = session / "derived" / "preprocess" / "audio"
    return preprocess_audio / "remote_for_aec.wav", preprocess_audio / "mic_raw_for_asr.wav"


def default_lab_dir(session: Path) -> Path:
    echo_dir = session / "derived" / "preprocess" / "echo"
    margin3 = echo_dir / "lab_margin3"
    return margin3 if margin3.exists() else echo_dir / "lab"


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


def resample_if_needed(audio: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    if source_rate == target_rate:
        return audio.astype(np.float32)
    divisor = math.gcd(source_rate, target_rate)
    up = target_rate // divisor
    down = source_rate // divisor
    return signal.resample_poly(audio, up, down).astype(np.float32)


def highpass(audio: np.ndarray, sample_rate: int, cutoff_hz: float) -> np.ndarray:
    if cutoff_hz <= 0:
        return audio.astype(np.float32)
    sos = signal.butter(4, cutoff_hz, btype="highpass", fs=sample_rate, output="sos")
    return signal.sosfilt(sos, audio).astype(np.float32)


def shifted_reference(remote: np.ndarray, delay_samples: int) -> np.ndarray:
    shifted = np.zeros_like(remote)
    if delay_samples > 0:
        shifted[delay_samples:] = remote[:-delay_samples]
    elif delay_samples < 0:
        lead = -delay_samples
        shifted[:-lead] = remote[lead:]
    else:
        shifted[:] = remote
    return shifted


def load_delay_ms(session: Path, lab_dir: Path | None) -> float:
    resolved_lab_dir = lab_dir or default_lab_dir(session)
    delay_map_path = resolved_lab_dir / "delay_map.json"
    payload = json.loads(delay_map_path.read_text(encoding="utf-8"))
    delay_ms = payload.get("summary", {}).get("median_delay_ms")
    if delay_ms is None:
        raise ValueError(f"delay map has no median_delay_ms: {delay_map_path}")
    return float(delay_ms)


def fit_local_fir(
    remote_fit: np.ndarray,
    mic_fit: np.ndarray,
    taps: int,
    regularization: float,
) -> np.ndarray:
    x = remote_fit.astype(np.float64) - float(np.mean(remote_fit))
    y = mic_fit.astype(np.float64) - float(np.mean(mic_fit))
    corr_xx = signal.correlate(x, x, mode="full", method="fft")
    corr_yx = signal.correlate(y, x, mode="full", method="fft")
    center = x.size - 1
    r_xx = corr_xx[center : center + taps]
    p_yx = corr_yx[center : center + taps]
    if r_xx.size < taps or p_yx.size < taps:
        raise ValueError("fit window is too short for requested FIR tail")
    toeplitz_col = r_xx.copy()
    toeplitz_col[0] += max(float(r_xx[0]) * regularization, EPSILON)
    fir = linalg.solve_toeplitz((toeplitz_col, toeplitz_col), p_yx, check_finite=False)
    return np.asarray(fir, dtype=np.float64)


def power_db(audio: np.ndarray) -> float:
    return 10.0 * math.log10(float(np.mean(np.asarray(audio, dtype=np.float64) ** 2)) + EPSILON)


def normalized_corr(left: np.ndarray, right: np.ndarray) -> float:
    a = np.asarray(left, dtype=np.float64) - float(np.mean(left))
    b = np.asarray(right, dtype=np.float64) - float(np.mean(right))
    return float(np.dot(a, b) / (math.sqrt(np.dot(a, a) * np.dot(b, b)) + EPSILON))


def segment_metrics(name: str, remote: np.ndarray, audio: np.ndarray, fit_samples: int) -> dict[str, Any]:
    return {
        "name": name,
        "fit_power_db": round(power_db(audio[:fit_samples]), 3),
        "rest_power_db": round(power_db(audio[fit_samples:]), 3),
        "fit_remote_corr": round(normalized_corr(remote[:fit_samples], audio[:fit_samples]), 5),
        "rest_remote_corr": round(normalized_corr(remote[fit_samples:], audio[fit_samples:]), 5),
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    session = args.session
    delay_ms = args.delay_ms if args.delay_ms is not None else load_delay_ms(session, args.lab_dir)
    delay_samples = int(round(delay_ms * args.sample_rate / 1_000.0))
    out_dir = args.out_dir or (
        session
        / "derived"
        / "preprocess"
        / "echo"
        / f"local_subtract_{args.start_sec:.1f}s"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    remote_path, mic_path = default_audio_paths(session)
    remote_rate, remote = read_wav_float(remote_path)
    mic_rate, mic = read_wav_float(mic_path)
    remote = highpass(resample_if_needed(remote, remote_rate, args.sample_rate), args.sample_rate, args.highpass_hz)
    mic = highpass(resample_if_needed(mic, mic_rate, args.sample_rate), args.sample_rate, args.highpass_hz)

    total_samples = min(remote.size, mic.size)
    remote = remote[:total_samples]
    mic = mic[:total_samples]
    remote_aligned_full = shifted_reference(remote, delay_samples)

    start = int(round(args.start_sec * args.sample_rate))
    end = int(round((args.start_sec + args.duration_sec) * args.sample_rate))
    fit_samples = int(round(args.fit_sec * args.sample_rate))
    if start < 0 or end > total_samples or end <= start:
        raise ValueError("selected clip is outside available audio")
    if fit_samples <= 0 or fit_samples >= end - start:
        raise ValueError("fit-sec must be positive and shorter than duration-sec")

    remote_raw = remote[start:end].astype(np.float64)
    remote_aligned = remote_aligned_full[start:end].astype(np.float64)
    mic_clip = mic[start:end].astype(np.float64)
    remote_fit = remote_aligned[:fit_samples]
    mic_fit = mic_clip[:fit_samples]

    taps = int(round(args.tail_ms * args.sample_rate / 1_000.0))
    if taps <= 0:
        raise ValueError("tail-ms must produce at least one FIR tap")
    fir = fit_local_fir(remote_fit, mic_fit, taps, args.regularization)
    echo_hat = signal.lfilter(fir, [1.0], remote_aligned)
    mic_after = mic_clip - args.strength * echo_hat

    write_wav_float(out_dir / "remote.wav", args.sample_rate, remote_raw)
    write_wav_float(out_dir / "remote_aligned.wav", args.sample_rate, remote_aligned)
    write_wav_float(out_dir / "mic_before.wav", args.sample_rate, mic_clip)
    write_wav_float(out_dir / "echo_hat.wav", args.sample_rate, echo_hat)
    write_wav_float(out_dir / "mic_after.wav", args.sample_rate, mic_after)
    np.save(out_dir / "local_fir.npy", fir)

    report = {
        "schema": "murmurmark.echo_local_subtract_lab/v1",
        "session": str(session),
        "inputs": {
            "remote": str(remote_path),
            "mic": str(mic_path),
        },
        "parameters": {
            "start_sec": args.start_sec,
            "duration_sec": args.duration_sec,
            "fit_sec": args.fit_sec,
            "sample_rate": args.sample_rate,
            "delay_ms": round(delay_ms, 3),
            "delay_samples": delay_samples,
            "tail_ms": args.tail_ms,
            "taps": taps,
            "regularization": args.regularization,
            "strength": args.strength,
        },
        "metrics": [
            segment_metrics("mic_before", remote_aligned, mic_clip, fit_samples),
            segment_metrics("mic_after", remote_aligned, mic_after, fit_samples),
            segment_metrics("echo_hat", remote_aligned, echo_hat, fit_samples),
        ],
        "outputs": {
            "remote": "remote.wav",
            "remote_aligned": "remote_aligned.wav",
            "mic_before": "mic_before.wav",
            "echo_hat": "echo_hat.wav",
            "mic_after": "mic_after.wav",
            "fir": "local_fir.npy",
        },
    }
    write_json(out_dir / "subtract_report.json", report)

    print(f"local subtract lab: {out_dir / 'subtract_report.json'}")
    print(f"mic_before: {out_dir / 'mic_before.wav'}")
    print(f"mic_after: {out_dir / 'mic_after.wav'}")
    print(f"echo_hat: {out_dir / 'echo_hat.wav'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
