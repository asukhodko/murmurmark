#!/usr/bin/env python3
"""Static FIR sanity check for Echo Guard.

This script tests whether a leaked remote signal in the mic track can be
explained by a linear FIR model of the known remote reference. It is a lab tool:
it writes reports and short examples, but it does not update mic_for_asr.wav.
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
    parser = argparse.ArgumentParser(description="Run a static FIR echo-path sanity check.")
    parser.add_argument("session", type=Path, help="MurmurMark session directory")
    parser.add_argument(
        "--lab-dir",
        type=Path,
        default=None,
        help="Delay lab directory. Default: lab_margin3 if present, otherwise lab",
    )
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument("--sample-rate", type=int, default=16_000)
    parser.add_argument("--highpass-hz", type=float, default=120.0)
    parser.add_argument("--tail-ms", type=float, default=200.0)
    parser.add_argument("--regularization", type=float, default=1.0e-3)
    parser.add_argument("--fit-clips", type=int, default=8)
    parser.add_argument("--eval-clips", type=int, default=8)
    parser.add_argument("--min-gap-sec", type=float, default=8.0)
    parser.add_argument(
        "--split",
        choices=("alternating", "chronological"),
        default="alternating",
        help="How to split selected non-overlapping clips into fit/eval sets.",
    )
    parser.add_argument("--write-examples", type=int, default=3)
    parser.add_argument("--delay-ms", type=float, default=None)
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


def write_wav_float(path: Path, sample_rate: int, audio: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    clipped = np.clip(audio, -1.0, 1.0).astype(np.float32)
    wavfile.write(path, sample_rate, clipped)


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


def default_audio_paths(session: Path) -> tuple[Path, Path]:
    preprocess_audio = session / "derived" / "preprocess" / "audio"
    return preprocess_audio / "remote_for_aec.wav", preprocess_audio / "mic_raw_for_asr.wav"


def default_lab_dir(session: Path) -> Path:
    echo_dir = session / "derived" / "preprocess" / "echo"
    margin3 = echo_dir / "lab_margin3"
    return margin3 if margin3.exists() else echo_dir / "lab"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


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


def non_overlapping_candidates(
    candidates: list[dict[str, Any]],
    required_count: int,
    min_gap_sec: float,
) -> list[dict[str, Any]]:
    sorted_candidates = sorted(
        candidates,
        key=lambda item: (float(item.get("confidence", 0.0)), float(item.get("peak", 0.0))),
        reverse=True,
    )
    selected: list[dict[str, Any]] = []
    for candidate in sorted_candidates:
        start = float(candidate["start_sec"])
        end = float(candidate["end_sec"])
        overlaps = False
        for existing in selected:
            existing_start = float(existing["start_sec"])
            existing_end = float(existing["end_sec"])
            if start < existing_end + min_gap_sec and end + min_gap_sec > existing_start:
                overlaps = True
                break
        if not overlaps:
            selected.append(candidate)
        if len(selected) >= required_count:
            break
    return sorted(selected, key=lambda item: float(item["start_sec"]))


def split_candidates(
    selected: list[dict[str, Any]],
    fit_count: int,
    eval_count: int,
    split: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    required_count = fit_count + eval_count
    selected = selected[:required_count]
    if split == "chronological":
        return selected[:fit_count], selected[fit_count:required_count]

    fit: list[dict[str, Any]] = []
    evaluate: list[dict[str, Any]] = []
    for index, candidate in enumerate(selected):
        target = fit if index % 2 == 0 else evaluate
        target.append(candidate)

    overflow: list[dict[str, Any]] = []
    if len(fit) > fit_count:
        overflow.extend(fit[fit_count:])
        fit = fit[:fit_count]
    if len(evaluate) > eval_count:
        overflow.extend(evaluate[eval_count:])
        evaluate = evaluate[:eval_count]
    for candidate in overflow:
        if len(fit) < fit_count:
            fit.append(candidate)
        elif len(evaluate) < eval_count:
            evaluate.append(candidate)
    return sorted(fit, key=lambda item: float(item["start_sec"])), sorted(
        evaluate,
        key=lambda item: float(item["start_sec"]),
    )


def clip_arrays(
    remote_shifted: np.ndarray,
    mic: np.ndarray,
    clip: dict[str, Any],
    sample_rate: int,
) -> tuple[np.ndarray, np.ndarray]:
    start = int(round(float(clip["start_sec"]) * sample_rate))
    end = int(round(float(clip["end_sec"]) * sample_rate))
    end = min(end, remote_shifted.size, mic.size)
    start = max(0, min(start, end))
    return remote_shifted[start:end], mic[start:end]


def accumulate_correlations(
    clips: list[dict[str, Any]],
    remote_shifted: np.ndarray,
    mic: np.ndarray,
    sample_rate: int,
    taps: int,
) -> tuple[np.ndarray, np.ndarray]:
    r_xx = np.zeros(taps, dtype=np.float64)
    p_xy = np.zeros(taps, dtype=np.float64)
    total_energy = 0.0

    for clip in clips:
        x, y = clip_arrays(remote_shifted, mic, clip, sample_rate)
        if x.size <= taps or y.size <= taps:
            continue
        x = x.astype(np.float64) - float(np.mean(x))
        y = y.astype(np.float64) - float(np.mean(y))
        corr_xx = signal.correlate(x, x, mode="full", method="fft")
        corr_yx = signal.correlate(y, x, mode="full", method="fft")
        center = x.size - 1
        r_xx += corr_xx[center : center + taps]
        p_xy += corr_yx[center : center + taps]
        total_energy += float(np.sum(x * x))

    if total_energy <= EPSILON:
        raise ValueError("fit clips have no usable remote energy")
    return r_xx, p_xy


def fit_fir(
    clips: list[dict[str, Any]],
    remote_shifted: np.ndarray,
    mic: np.ndarray,
    sample_rate: int,
    taps: int,
    regularization: float,
) -> np.ndarray:
    r_xx, p_xy = accumulate_correlations(clips, remote_shifted, mic, sample_rate, taps)
    diag = max(float(r_xx[0]) * regularization, EPSILON)
    toeplitz_col = r_xx.copy()
    toeplitz_col[0] += diag
    fir = linalg.solve_toeplitz((toeplitz_col, toeplitz_col), p_xy, check_finite=False)
    return np.asarray(fir, dtype=np.float64)


def normalized_corr(a: np.ndarray, b: np.ndarray) -> float:
    a = a.astype(np.float64) - float(np.mean(a))
    b = b.astype(np.float64) - float(np.mean(b))
    denom = math.sqrt(float(np.sum(a * a) * np.sum(b * b))) + EPSILON
    return float(np.sum(a * b) / denom)


def evaluate_clip(
    clip: dict[str, Any],
    remote_shifted: np.ndarray,
    mic: np.ndarray,
    fir: np.ndarray,
    sample_rate: int,
) -> dict[str, Any]:
    x, y = clip_arrays(remote_shifted, mic, clip, sample_rate)
    x = x.astype(np.float64)
    y = y.astype(np.float64)
    echo_hat = signal.lfilter(fir, [1.0], x)
    clean = y - echo_hat

    # Ignore filter warm-up when scoring.
    skip = min(fir.size, max(0, y.size // 4))
    y_score = y[skip:]
    clean_score = clean[skip:]
    x_score = x[skip:]
    echo_score = echo_hat[skip:]

    before_power = float(np.mean(y_score * y_score) + EPSILON)
    after_power = float(np.mean(clean_score * clean_score) + EPSILON)
    echo_power = float(np.mean(echo_score * echo_score) + EPSILON)
    reduction_db = 10.0 * math.log10(before_power / after_power)

    return {
        "index": clip.get("index"),
        "start_sec": clip["start_sec"],
        "end_sec": clip["end_sec"],
        "input_delay_ms": clip.get("delay_ms"),
        "confidence": clip.get("confidence"),
        "reduction_db": round(reduction_db, 3),
        "remote_similarity_before": round(normalized_corr(x_score, y_score), 5),
        "remote_similarity_after": round(normalized_corr(x_score, clean_score), 5),
        "mic_power_db": round(10.0 * math.log10(before_power), 3),
        "clean_power_db": round(10.0 * math.log10(after_power), 3),
        "echo_hat_power_db": round(10.0 * math.log10(echo_power), 3),
    }


def median(values: list[float]) -> float | None:
    if not values:
        return None
    return round(float(np.median(np.asarray(values, dtype=np.float64))), 3)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_examples(
    out_dir: Path,
    eval_clips: list[dict[str, Any]],
    remote_shifted: np.ndarray,
    remote_raw: np.ndarray,
    mic: np.ndarray,
    fir: np.ndarray,
    sample_rate: int,
    count: int,
) -> None:
    examples_dir = out_dir / "examples"
    for ordinal, clip in enumerate(eval_clips[:count], start=1):
        start = int(round(float(clip["start_sec"]) * sample_rate))
        end = int(round(float(clip["end_sec"]) * sample_rate))
        x, y = clip_arrays(remote_shifted, mic, clip, sample_rate)
        remote_slice = remote_raw[start:end]
        echo_hat = signal.lfilter(fir, [1.0], x)
        clean = y - echo_hat
        prefix = examples_dir / f"eval_{ordinal:02d}_{float(clip['start_sec']):.1f}s"
        write_wav_float(prefix.with_name(prefix.name + "_remote.wav"), sample_rate, remote_slice)
        write_wav_float(prefix.with_name(prefix.name + "_mic_before.wav"), sample_rate, y)
        write_wav_float(prefix.with_name(prefix.name + "_echo_hat.wav"), sample_rate, echo_hat)
        write_wav_float(prefix.with_name(prefix.name + "_mic_after.wav"), sample_rate, clean)


def main() -> int:
    args = parse_args()
    session = args.session
    lab_dir = args.lab_dir or default_lab_dir(session)
    out_dir = args.out_dir or session / "derived" / "preprocess" / "echo" / "fir_lab"
    out_dir.mkdir(parents=True, exist_ok=True)

    delay_map_path = lab_dir / "delay_map.json"
    candidates_path = lab_dir / "candidate_clips.jsonl"
    if not delay_map_path.exists() or not candidates_path.exists():
        raise SystemExit(f"delay lab artifacts not found in {lab_dir}")

    delay_map = load_json(delay_map_path)
    candidates = load_jsonl(candidates_path)
    required_count = args.fit_clips + args.eval_clips
    selected = non_overlapping_candidates(candidates, required_count, args.min_gap_sec)
    if len(selected) < required_count:
        raise SystemExit(f"need {required_count} non-overlapping candidates, found {len(selected)}")

    fit_clips, eval_clips = split_candidates(
        selected,
        args.fit_clips,
        args.eval_clips,
        args.split,
    )

    remote_path, mic_path = default_audio_paths(session)
    remote_rate, remote = read_wav_float(remote_path)
    mic_rate, mic = read_wav_float(mic_path)
    remote = resample_if_needed(remote, remote_rate, args.sample_rate)
    mic = resample_if_needed(mic, mic_rate, args.sample_rate)
    remote = highpass(remote, args.sample_rate, args.highpass_hz)
    mic = highpass(mic, args.sample_rate, args.highpass_hz)
    total_samples = min(remote.size, mic.size)
    remote = remote[:total_samples]
    mic = mic[:total_samples]

    delay_ms = args.delay_ms
    if delay_ms is None:
        delay_ms = float(delay_map["summary"]["median_delay_ms"])
    delay_samples = int(round(delay_ms * args.sample_rate / 1_000.0))
    remote_shifted = shifted_reference(remote, delay_samples)

    taps = int(round(args.tail_ms * args.sample_rate / 1_000.0))
    if taps <= 0:
        raise SystemExit("tail-ms must produce at least one FIR tap")

    fir = fit_fir(fit_clips, remote_shifted, mic, args.sample_rate, taps, args.regularization)
    eval_metrics = [
        evaluate_clip(clip, remote_shifted, mic, fir, args.sample_rate)
        for clip in eval_clips
    ]
    fit_metrics = [
        evaluate_clip(clip, remote_shifted, mic, fir, args.sample_rate)
        for clip in fit_clips
    ]

    eval_reductions = [float(metric["reduction_db"]) for metric in eval_metrics]
    eval_similarity_drop = [
        abs(float(metric["remote_similarity_before"])) - abs(float(metric["remote_similarity_after"]))
        for metric in eval_metrics
    ]
    accepted = bool(eval_reductions and float(np.median(eval_reductions)) >= 6.0)

    report = {
        "schema": "murmurmark.echo_fir_lab/v1",
        "session": str(session),
        "inputs": {
            "delay_map": str(delay_map_path),
            "remote": str(remote_path),
            "mic": str(mic_path),
        },
        "parameters": {
            "sample_rate": args.sample_rate,
            "delay_ms": round(delay_ms, 3),
            "delay_samples": delay_samples,
            "tail_ms": args.tail_ms,
            "taps": taps,
            "regularization": args.regularization,
            "fit_clips": args.fit_clips,
            "eval_clips": args.eval_clips,
            "min_gap_sec": args.min_gap_sec,
            "split": args.split,
        },
        "summary": {
            "accepted_for_next_iteration": accepted,
            "acceptance_rule": "held-out median reduction_db >= 6.0",
            "fit_median_reduction_db": median([float(metric["reduction_db"]) for metric in fit_metrics]),
            "eval_median_reduction_db": median(eval_reductions),
            "eval_median_similarity_drop": median(eval_similarity_drop),
        },
        "fit_metrics": fit_metrics,
        "eval_metrics": eval_metrics,
    }

    np.save(out_dir / "fir_filter.npy", fir)
    write_json(out_dir / "fir_report.json", report)
    write_jsonl(out_dir / "selected_fit_clips.jsonl", fit_clips)
    write_jsonl(out_dir / "selected_eval_clips.jsonl", eval_clips)
    write_examples(out_dir, eval_clips, remote_shifted, remote, mic, fir, args.sample_rate, args.write_examples)

    print(f"fir lab: {out_dir / 'fir_report.json'}")
    print(f"accepted_for_next_iteration: {accepted}")
    print(f"fit_median_reduction_db: {report['summary']['fit_median_reduction_db']}")
    print(f"eval_median_reduction_db: {report['summary']['eval_median_reduction_db']}")
    print(f"eval_median_similarity_drop: {report['summary']['eval_median_similarity_drop']}")
    print(f"examples: {out_dir / 'examples'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
