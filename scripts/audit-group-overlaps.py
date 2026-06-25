#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
import tempfile
import warnings
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import librosa
import numpy as np
import soundfile as sf
from scipy import signal


SCHEMA_AUDIT = "murmurmark.group_overlap_audit/v1"
SCHEMA_SUMMARY = "murmurmark.group_overlap_summary/v1"
SCRIPT_VERSION = "0.1.0"
SAMPLE_RATE = 16000
EPS = 1e-9

FILLER_TOKENS = {
    "а",
    "ага",
    "да",
    "ну",
    "ок",
    "окей",
    "угу",
    "хм",
    "вот",
    "сейчас",
    "понял",
    "понятно",
    "ладно",
    "хорошо",
}
BACKCHANNEL_PHRASES = {
    "да",
    "да да",
    "ну да",
    "угу",
    "ага",
    "ок",
    "окей",
    "понял",
    "понятно",
    "ладно",
    "хорошо",
}
DOMAIN_SYNONYMS = {
    "gitlab": "gitlab",
    "гитлаб": "gitlab",
    "гитлаба": "gitlab",
    "github": "github",
    "kubernetes": "kubernetes",
    "кубер": "kubernetes",
    "кубы": "kubernetes",
    "kube": "kubernetes",
    "deploy": "deploy",
    "деплой": "deploy",
    "диплой": "deploy",
    "pipeline": "pipeline",
    "пайплайн": "pipeline",
    "ci": "cicd",
    "cicd": "cicd",
    "ci/cd": "cicd",
    "slo": "slo",
    "sla": "sla",
    "dns": "dns",
    "api": "api",
    "openapi": "openapi",
    "mcp": "mcp",
}


def run(args: list[str]) -> None:
    subprocess.run(args, check=True)


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"expected JSON object: {path}")
    return data


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(value)
    return rows


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")


def suffix_for_profile(profile: str) -> str:
    return "" if profile == "current" else f".{profile}"


def require_path(path: Path, label: str) -> Path:
    if not path.exists():
        raise FileNotFoundError(f"missing {label}: {path}")
    return path


def rms_db(audio: np.ndarray) -> float:
    if audio.size == 0:
        return -120.0
    value = float(np.sqrt(np.mean(np.square(audio.astype(np.float64))) + EPS))
    return round(max(-120.0, 20.0 * math.log10(value + EPS)), 3)


def ratio_db(numerator_db: float, denominator_db: float) -> float:
    return round(numerator_db - denominator_db, 3)


def percentile(values: list[float], q: float, default: float = 0.0) -> float:
    if not values:
        return default
    return round(float(np.percentile(np.asarray(values, dtype=np.float64), q)), 6)


def normalize_text(text: Any) -> str:
    lowered = str(text or "").lower().replace("ё", "е")
    lowered = re.sub(r"[^0-9a-zа-я_./+-]+", " ", lowered)
    tokens = []
    for token in lowered.split():
        token = token.strip(".,!?;:()[]{}«»\"'`")
        if not token:
            continue
        tokens.append(DOMAIN_SYNONYMS.get(token, token))
    return " ".join(tokens)


def text_tokens(text: Any) -> list[str]:
    return [token for token in normalize_text(text).split() if token]


def content_tokens(text: Any) -> list[str]:
    return [token for token in text_tokens(text) if token not in FILLER_TOKENS and len(token) > 1]


def ngrams(text: str, n: int = 3) -> set[str]:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) < n:
        return {compact} if compact else set()
    return {compact[index : index + n] for index in range(len(compact) - n + 1)}


def jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    return len(left & right) / max(1, len(left | right))


def text_features(me_text: str, remote_text: str) -> dict[str, Any]:
    me_norm = normalize_text(me_text)
    remote_norm = normalize_text(remote_text)
    me_tokens = set(content_tokens(me_text))
    remote_tokens = set(content_tokens(remote_text))
    token_jaccard = jaccard(me_tokens, remote_tokens)
    containment = 0.0
    if me_tokens and remote_tokens:
        containment = len(me_tokens & remote_tokens) / max(1, min(len(me_tokens), len(remote_tokens)))
    char3 = jaccard(ngrams(me_norm), ngrams(remote_norm))
    sequence = SequenceMatcher(None, me_norm, remote_norm).ratio() if me_norm or remote_norm else 1.0
    domain_me = {token for token in me_tokens if token in set(DOMAIN_SYNONYMS.values())}
    domain_remote = {token for token in remote_tokens if token in set(DOMAIN_SYNONYMS.values())}
    domain_overlap = jaccard(domain_me, domain_remote)
    similarity_max = max(token_jaccard, containment, char3, sequence)
    me_phrase = " ".join(text_tokens(me_text))
    is_backchannel = me_phrase in BACKCHANNEL_PHRASES or (
        0 < len(text_tokens(me_text)) <= 3 and all(token in FILLER_TOKENS for token in text_tokens(me_text))
    )
    return {
        "me_norm": me_norm,
        "remote_norm": remote_norm,
        "token_jaccard": round(token_jaccard, 6),
        "token_containment": round(containment, 6),
        "char3_jaccard": round(char3, 6),
        "sequence_ratio": round(sequence, 6),
        "domain_term_overlap": round(domain_overlap, 6),
        "similarity_max": round(similarity_max, 6),
        "is_short_backchannel": bool(is_backchannel),
        "me_token_count": len(text_tokens(me_text)),
        "remote_token_count": len(text_tokens(remote_text)),
    }


def extract_wav(source: Path, output: Path, start: float, duration: float) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{max(0.0, start):.3f}",
            "-t",
            f"{max(0.01, duration):.3f}",
            "-i",
            str(source),
            "-ar",
            str(SAMPLE_RATE),
            "-ac",
            "1",
            str(output),
        ]
    )


def write_stereo(left: Path, right: Path, output: Path) -> None:
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    output.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(left),
            "-i",
            str(right),
            "-filter_complex",
            "[0:a][1:a]join=inputs=2:channel_layout=stereo[a]",
            "-map",
            "[a]",
            str(output),
        ]
    )


def read_audio(path: Path) -> np.ndarray:
    data, sr = sf.read(path, dtype="float32", always_2d=False)
    if sr != SAMPLE_RATE:
        data = librosa.resample(np.asarray(data, dtype=np.float32), orig_sr=sr, target_sr=SAMPLE_RATE)
    if data.ndim > 1:
        data = np.mean(data, axis=1)
    return np.asarray(data, dtype=np.float32)


def align_pair(left: np.ndarray, right: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    size = min(len(left), len(right))
    if size <= 0:
        return np.zeros(0, dtype=np.float32), np.zeros(0, dtype=np.float32)
    return left[:size], right[:size]


def normalized_xcorr(reference: np.ndarray, target: np.ndarray, max_lag_ms: float = 500.0) -> dict[str, Any]:
    reference, target = align_pair(reference, target)
    if len(reference) < 32 or len(target) < 32:
        return {"max_corr": 0.0, "lag_ms": None, "peak_ratio": 0.0}
    ref = reference.astype(np.float64) - float(np.mean(reference))
    tar = target.astype(np.float64) - float(np.mean(target))
    ref_std = float(np.std(ref))
    tar_std = float(np.std(tar))
    if ref_std < EPS or tar_std < EPS:
        return {"max_corr": 0.0, "lag_ms": None, "peak_ratio": 0.0}
    corr = signal.correlate(tar, ref, mode="full", method="fft")
    lags = signal.correlation_lags(len(tar), len(ref), mode="full")
    max_lag = int(round(max_lag_ms * SAMPLE_RATE / 1000.0))
    mask = np.abs(lags) <= max_lag
    if not np.any(mask):
        return {"max_corr": 0.0, "lag_ms": None, "peak_ratio": 0.0}
    corr = corr[mask]
    lags = lags[mask]
    denom = len(ref) * ref_std * tar_std
    norm = corr / max(EPS, denom)
    best_index = int(np.argmax(np.abs(norm)))
    best = float(abs(norm[best_index]))
    lag_ms = float(lags[best_index] * 1000.0 / SAMPLE_RATE)
    median_peak = float(np.median(np.abs(norm))) + EPS
    return {
        "max_corr": round(best, 6),
        "lag_ms": round(lag_ms, 3),
        "peak_ratio": round(best / median_peak, 6),
    }


def mel_cosine(reference: np.ndarray, target: np.ndarray) -> float:
    reference, target = align_pair(reference, target)
    if len(reference) < 256 or len(target) < 256:
        return 0.0
    try:
        left = librosa.feature.melspectrogram(
            y=reference.astype(np.float32),
            sr=SAMPLE_RATE,
            n_fft=512,
            hop_length=160,
            n_mels=32,
            fmin=80,
            fmax=7600,
            power=2.0,
        )
        right = librosa.feature.melspectrogram(
            y=target.astype(np.float32),
            sr=SAMPLE_RATE,
            n_fft=512,
            hop_length=160,
            n_mels=32,
            fmin=80,
            fmax=7600,
            power=2.0,
        )
        size = min(left.shape[1], right.shape[1])
        if size <= 0:
            return 0.0
        left_vec = librosa.power_to_db(left[:, :size], ref=np.max).reshape(-1)
        right_vec = librosa.power_to_db(right[:, :size], ref=np.max).reshape(-1)
        denom = float(np.linalg.norm(left_vec) * np.linalg.norm(right_vec))
        if denom < EPS:
            return 0.0
        return round(float(np.dot(left_vec, right_vec) / denom), 6)
    except Exception:
        return 0.0


def speech_band_coherence(reference: np.ndarray, target: np.ndarray) -> dict[str, float]:
    reference, target = align_pair(reference, target)
    if len(reference) < 512 or len(target) < 512:
        return {"speech_band_300_3400": 0.0, "upper_speech_3400_7600": 0.0}
    nperseg = min(1024, len(reference), len(target))
    if nperseg < 256:
        return {"speech_band_300_3400": 0.0, "upper_speech_3400_7600": 0.0}
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            freqs, coh = signal.coherence(reference, target, fs=SAMPLE_RATE, nperseg=nperseg)
        coh = np.nan_to_num(coh, nan=0.0, posinf=0.0, neginf=0.0)
    except Exception:
        return {"speech_band_300_3400": 0.0, "upper_speech_3400_7600": 0.0}

    def mean_band(low: float, high: float) -> float:
        mask = (freqs >= low) & (freqs <= high)
        if not np.any(mask):
            return 0.0
        return round(float(np.mean(coh[mask])), 6)

    return {
        "speech_band_300_3400": mean_band(300.0, 3400.0),
        "upper_speech_3400_7600": mean_band(3400.0, 7600.0),
    }


def weighted_state_features(start: float, end: float, states: list[dict[str, Any]]) -> dict[str, Any]:
    duration = max(EPS, end - start)
    totals = Counter()
    confidence_weight = 0.0
    confidence_total = 0.0
    remote_db_values: list[float] = []
    mic_db_values: list[float] = []
    for row in states:
        row_start = float(row.get("start", 0.0) or 0.0)
        row_end = float(row.get("end", row_start) or row_start)
        overlap = min(end, row_end) - max(start, row_start)
        if overlap <= 0:
            continue
        state = str(row.get("state") or "unknown")
        totals[state] += overlap
        confidence = float(row.get("confidence", 0.0) or 0.0)
        confidence_weight += confidence * overlap
        confidence_total += overlap
        if row.get("remote_db") is not None:
            remote_db_values.append(float(row.get("remote_db")))
        if row.get("mic_db") is not None:
            mic_db_values.append(float(row.get("mic_db")))

    def ratio(names: tuple[str, ...]) -> float:
        return round(sum(total for name, total in totals.items() if any(key in name for key in names)) / duration, 6)

    local_only = ratio(("local_only",))
    remote_only = ratio(("remote_only", "remote_only_level"))
    double_talk = ratio(("double_talk",))
    silence = ratio(("silence",))
    remote_active = round(min(1.0, remote_only + double_talk), 6)
    local_score = round(min(1.0, local_only + double_talk * 0.7), 6)
    return {
        "local_only_ratio": local_only,
        "remote_only_ratio": remote_only,
        "double_talk_ratio": double_talk,
        "silence_ratio": silence,
        "remote_active_ratio": remote_active,
        "local_score_mean": local_score,
        "local_score_max": local_score,
        "state_confidence_mean": round(confidence_weight / max(EPS, confidence_total), 6) if confidence_total else 0.0,
        "remote_db_mean": round(float(np.mean(remote_db_values)), 3) if remote_db_values else None,
        "mic_db_mean": round(float(np.mean(mic_db_values)), 3) if mic_db_values else None,
        "state_seconds": {key: round(value, 3) for key, value in sorted(totals.items())},
    }


def interval_boundary_features(overlap: dict[str, Any], me: dict[str, Any], remote: dict[str, Any]) -> dict[str, Any]:
    start = float(overlap["start"])
    end = float(overlap["end"])
    overlap_duration = max(0.0, end - start)

    def coverage(row: dict[str, Any]) -> float:
        duration = max(EPS, float(row.get("end", end)) - float(row.get("start", start)))
        return min(1.0, overlap_duration / duration)

    def near_boundary(row: dict[str, Any]) -> bool:
        row_start = float(row.get("start", start))
        row_end = float(row.get("end", end))
        return abs(start - row_start) <= 1.5 or abs(end - row_end) <= 1.5

    me_cov = coverage(me)
    remote_cov = coverage(remote)
    boundary_like = me_cov < 0.25 or remote_cov < 0.25 or near_boundary(me) or near_boundary(remote)
    return {
        "me_coverage": round(me_cov, 6),
        "remote_coverage": round(remote_cov, 6),
        "near_boundary": bool(boundary_like),
    }


def clip_audio_for_features(
    sources: dict[str, Path],
    start: float,
    end: float,
    temp_dir: Path,
    stem: str,
) -> dict[str, np.ndarray]:
    start = max(0.0, start - 0.75)
    end = max(start + 0.05, end + 0.75)
    duration = end - start
    audios: dict[str, np.ndarray] = {}
    for name, source in sources.items():
        output = temp_dir / f"{stem}_{name}.wav"
        extract_wav(source, output, start, duration)
        audios[name] = read_audio(output)
    return audios


def audio_features(audios: dict[str, np.ndarray], session_delay_ms: float | None, calibration: dict[str, Any]) -> dict[str, Any]:
    remote = audios["remote"]
    raw = audios["mic_raw"]
    clean = audios["mic_clean"]
    masked = audios["mic_role_masked"]
    rms = {
        "remote": rms_db(remote),
        "mic_raw": rms_db(raw),
        "mic_clean": rms_db(clean),
        "mic_role_masked": rms_db(masked),
    }
    xcorr = {
        "raw": normalized_xcorr(remote, raw),
        "clean": normalized_xcorr(remote, clean),
        "role_masked": normalized_xcorr(remote, masked),
    }
    for row in xcorr.values():
        lag_ms = row.get("lag_ms")
        row["lag_consistent"] = bool(
            lag_ms is not None
            and session_delay_ms is not None
            and abs(float(lag_ms) - float(session_delay_ms)) <= 250.0
        )
    mel = {
        "raw": mel_cosine(remote, raw),
        "clean": mel_cosine(remote, clean),
        "role_masked": mel_cosine(remote, masked),
    }
    coherence = {
        "raw": speech_band_coherence(remote, raw),
        "clean": speech_band_coherence(remote, clean),
        "role_masked": speech_band_coherence(remote, masked),
    }
    energy_ratios = {
        "mic_clean_vs_raw": ratio_db(rms["mic_clean"], rms["mic_raw"]),
        "role_masked_vs_raw": ratio_db(rms["mic_role_masked"], rms["mic_raw"]),
        "remote_vs_mic_raw": ratio_db(rms["remote"], rms["mic_raw"]),
    }
    thresholds = calibration.get("thresholds", {})
    return {
        "rms_db": rms,
        "energy_ratios_db": energy_ratios,
        "xcorr": xcorr,
        "mel_cosine": mel,
        "coherence": coherence,
        "thresholds": thresholds,
    }


def sample_state_windows(states: list[dict[str, Any]], kind: str, limit: int = 20) -> list[tuple[float, float]]:
    candidates: list[tuple[float, float]] = []
    for row in states:
        state = str(row.get("state") or "")
        if kind == "local_baseline" and "local_only" not in state:
            continue
        if kind == "remote_leak_baseline" and "remote_only" not in state:
            continue
        if kind == "silence_baseline" and "silence" not in state:
            continue
        start = float(row.get("start", 0.0) or 0.0)
        end = float(row.get("end", start) or start)
        if end - start >= 0.5:
            candidates.append((start, end))
    if len(candidates) <= limit:
        return candidates
    step = len(candidates) / limit
    return [candidates[int(index * step)] for index in range(limit)]


def calibrate_session(sources: dict[str, Path], states: list[dict[str, Any]], session_delay_ms: float | None, temp_dir: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"baselines": {}, "thresholds": {}}
    for kind in ("local_baseline", "remote_leak_baseline", "silence_baseline"):
        values = {"xcorr_raw": [], "mel_raw": [], "rms_remote": [], "rms_mic_raw": [], "coherence_raw": []}
        for index, (start, end) in enumerate(sample_state_windows(states, kind), start=1):
            try:
                audios = clip_audio_for_features(sources, start, end, temp_dir, f"cal_{kind}_{index:04d}")
                features = audio_features(audios, session_delay_ms, {"thresholds": {}})
            except Exception:
                continue
            values["xcorr_raw"].append(float(features["xcorr"]["raw"]["max_corr"]))
            values["mel_raw"].append(float(features["mel_cosine"]["raw"]))
            values["rms_remote"].append(float(features["rms_db"]["remote"]))
            values["rms_mic_raw"].append(float(features["rms_db"]["mic_raw"]))
            values["coherence_raw"].append(float(features["coherence"]["raw"]["speech_band_300_3400"]))
        result["baselines"][kind] = {
            "n": len(values["xcorr_raw"]),
            "xcorr_p50": percentile(values["xcorr_raw"], 50),
            "xcorr_p75": percentile(values["xcorr_raw"], 75),
            "xcorr_p90": percentile(values["xcorr_raw"], 90),
            "mel_p50": percentile(values["mel_raw"], 50),
            "mel_p75": percentile(values["mel_raw"], 75),
            "mel_p90": percentile(values["mel_raw"], 90),
            "coherence_p90": percentile(values["coherence_raw"], 90),
            "rms_remote_p50": percentile(values["rms_remote"], 50, -120.0),
            "rms_mic_raw_p50": percentile(values["rms_mic_raw"], 50, -120.0),
        }
    local = result["baselines"].get("local_baseline", {})
    result["thresholds"] = {
        "xcorr_high": round(max(0.25, float(local.get("xcorr_p90", 0.0) or 0.0)), 6),
        "xcorr_medium": round(max(0.16, float(local.get("xcorr_p75", 0.0) or 0.0)), 6),
        "mel_high": round(max(0.72, float(local.get("mel_p90", 0.0) or 0.0)), 6),
        "mel_medium": round(max(0.62, float(local.get("mel_p75", 0.0) or 0.0)), 6),
        "coherence_high": round(max(0.35, float(local.get("coherence_p90", 0.0) or 0.0)), 6),
        "role_masked_silence_db": -55.0,
        "energy_drop_strong_db": -12.0,
    }
    return result


def clamp_score(value: float) -> int:
    return int(max(0, min(100, round(value))))


def compute_scores(features: dict[str, Any]) -> dict[str, int]:
    state = features["speaker_state"]
    audio = features["audio"]
    text = features["text"]
    interval = features["interval"]
    thresholds = audio.get("thresholds", {})

    local_evidence = 0.0
    if state["local_score_mean"] >= 0.65:
        local_evidence += 35
    if state["local_only_ratio"] >= 0.50:
        local_evidence += 25
    if state["double_talk_ratio"] >= 0.40:
        local_evidence += 20
    if audio["rms_db"]["mic_clean"] > -45 and audio["xcorr"]["clean"]["max_corr"] < thresholds.get("xcorr_medium", 0.16):
        local_evidence += 15
    if text["is_short_backchannel"] and text["similarity_max"] < 0.60:
        local_evidence += 10
    if state["remote_only_ratio"] >= 0.60:
        local_evidence -= 25
    if audio["rms_db"]["mic_role_masked"] <= thresholds.get("role_masked_silence_db", -55.0):
        local_evidence -= 20
    local_evidence = clamp_score(local_evidence)

    audio_leak = 0.0
    if audio["xcorr"]["raw"]["max_corr"] >= thresholds.get("xcorr_high", 0.25):
        audio_leak += 30
    if audio["xcorr"]["raw"].get("lag_consistent"):
        audio_leak += 20
    if audio["mel_cosine"]["raw"] >= thresholds.get("mel_high", 0.72):
        audio_leak += 20
    if audio["coherence"]["raw"]["speech_band_300_3400"] >= thresholds.get("coherence_high", 0.35):
        audio_leak += 15
    if audio["energy_ratios_db"]["role_masked_vs_raw"] <= thresholds.get("energy_drop_strong_db", -12.0):
        audio_leak += 15
    if audio["rms_db"]["mic_clean"] > -42 and audio["xcorr"]["clean"]["max_corr"] < thresholds.get("xcorr_medium", 0.16):
        audio_leak -= 25
    audio_leak = clamp_score(audio_leak)

    remote_evidence = 0.0
    if state["remote_active_ratio"] >= 0.70:
        remote_evidence += 30
    if state["remote_only_ratio"] >= 0.50:
        remote_evidence += 25
    if audio_leak >= 70:
        remote_evidence += 20
    if audio["xcorr"]["raw"].get("lag_consistent"):
        remote_evidence += 15
    if text["similarity_max"] >= 0.75:
        remote_evidence += 15
    if local_evidence >= 65:
        remote_evidence -= 25
    remote_evidence = clamp_score(remote_evidence)

    text_duplicate = 0.0
    if text["similarity_max"] >= 0.75:
        text_duplicate += 35
    if text["token_containment"] >= 0.80:
        text_duplicate += 25
    if text["domain_term_overlap"] >= 0.60:
        text_duplicate += 20
    if interval["time_overlap_ratio"] >= 0.50:
        text_duplicate += 15
    if text["is_short_backchannel"] and text["similarity_max"] < 0.60:
        text_duplicate -= 20
    text_duplicate = clamp_score(text_duplicate)

    probable_duplicate = 0.0
    if text_duplicate >= 70 and local_evidence < 55:
        probable_duplicate = max(probable_duplicate, text_duplicate + 5)
    if text["similarity_max"] >= 0.65 and state["remote_only_ratio"] >= 0.50 and state["local_score_mean"] < 0.40:
        probable_duplicate = max(probable_duplicate, 78)

    probable_remote_leak = 0.0
    if audio_leak >= 70 and local_evidence < 50 and remote_evidence >= 60:
        probable_remote_leak = max(probable_remote_leak, audio_leak)
    elif audio_leak >= 60 and state["remote_only_ratio"] >= 0.50 and local_evidence < 45:
        probable_remote_leak = max(probable_remote_leak, 68)

    probable_double_talk = 0.0
    if local_evidence >= 65 and state["remote_active_ratio"] >= 0.50 and text["similarity_max"] < 0.60:
        probable_double_talk = max(probable_double_talk, local_evidence + 5)
    if (
        state["double_talk_ratio"] >= 0.40
        and audio["rms_db"]["mic_clean"] > -50
        and audio_leak < 70
        and text["similarity_max"] < 0.60
    ):
        probable_double_talk = max(probable_double_talk, 75)

    probable_timing_overlap = 0.0
    if (
        text["similarity_max"] < 0.45
        and audio_leak < 55
        and local_evidence >= 45
        and interval["near_boundary"]
    ):
        probable_timing_overlap = 75
    elif interval["near_boundary"] and interval["time_overlap_ratio"] < 0.25 and text["similarity_max"] < 0.55:
        probable_timing_overlap = 68

    probable_asr_noise = 0.0
    if (
        text["is_short_backchannel"]
        and local_evidence < 45
        and audio["rms_db"]["mic_role_masked"] <= thresholds.get("role_masked_silence_db", -55.0)
    ):
        probable_asr_noise = 78
    elif text["me_token_count"] <= 2 and state["silence_ratio"] + state["remote_only_ratio"] >= 0.60 and local_evidence < 45:
        probable_asr_noise = 70

    return {
        "local_evidence": local_evidence,
        "remote_evidence": remote_evidence,
        "audio_leak": audio_leak,
        "text_duplicate": text_duplicate,
        "probable_duplicate": clamp_score(probable_duplicate),
        "probable_remote_leak": clamp_score(probable_remote_leak),
        "probable_double_talk": clamp_score(probable_double_talk),
        "probable_timing_overlap": clamp_score(probable_timing_overlap),
        "probable_asr_noise": clamp_score(probable_asr_noise),
    }


def classify(scores: dict[str, int], duration: float) -> dict[str, Any]:
    category_scores = {
        key: scores[key]
        for key in (
            "probable_duplicate",
            "probable_remote_leak",
            "probable_double_talk",
            "probable_timing_overlap",
            "probable_asr_noise",
        )
    }
    ordered = sorted(category_scores.items(), key=lambda item: item[1], reverse=True)
    top_label, top_score = ordered[0]
    second_score = ordered[1][1] if len(ordered) > 1 else 0
    if top_score < 70 or top_score - second_score < 12 or (duration > 8 and top_score < 82):
        label = "needs_human_review"
        confidence = max(0.0, min(0.69, top_score / 100.0))
    else:
        label = top_label
        confidence = min(0.99, max(0.7, top_score / 100.0))
    suggestion = {
        "probable_duplicate": "drop_me_duplicate",
        "probable_remote_leak": "drop_me_or_mark_remote_leak",
        "probable_double_talk": "mark_double_talk",
        "probable_timing_overlap": "mark_timing_overlap",
        "probable_asr_noise": "drop_me_noise",
        "needs_human_review": "needs_review",
    }[label]
    return {
        "label": label,
        "confidence": round(confidence, 3),
        "top_score": top_score,
        "second_score": second_score,
        "action_suggestion": suggestion,
    }


def role_name(row: dict[str, Any]) -> str:
    source = str(row.get("source_track") or "").lower()
    label = str(row.get("speaker_label") or row.get("role") or "").lower()
    if source == "mic" or label == "me":
        return "me"
    if source == "remote" or "colleague" in label or label == "remote":
        return "remote"
    return label or source


def build_intervals(overlaps: list[dict[str, Any]], by_id: dict[str, dict[str, Any]], min_overlap: float) -> list[dict[str, Any]]:
    intervals: list[dict[str, Any]] = []
    for index, overlap in enumerate(overlaps, start=1):
        duration = float(overlap.get("duration_sec", 0.0) or 0.0)
        if duration < min_overlap:
            continue
        left = by_id.get(str(overlap.get("left_utterance_id")))
        right = by_id.get(str(overlap.get("right_utterance_id")))
        if not left or not right:
            continue
        roles = {role_name(left), role_name(right)}
        if roles != {"me", "remote"}:
            continue
        me = left if role_name(left) == "me" else right
        remote = right if me is left else left
        severity = "low"
        if duration >= 6.0:
            severity = "critical"
        elif duration >= 2.0:
            severity = "high"
        elif duration >= 0.5:
            severity = "medium"
        intervals.append(
            {
                "id": f"ov_{len(intervals) + 1:06d}",
                "source_overlap_index": index,
                "start": float(overlap["start"]),
                "end": float(overlap["end"]),
                "duration_sec": duration,
                "severity": severity,
                "overlap": overlap,
                "me": me,
                "remote": remote,
            }
        )
    return intervals


def profile_paths(session: Path, profile: str) -> dict[str, Path]:
    suffix = suffix_for_profile(profile)
    resolved = session / "derived" / "transcript-simple" / "whisper-cpp" / "resolved"
    return {
        "dialogue": resolved / f"clean_dialogue{suffix}.json",
        "overlaps": resolved / f"overlaps{suffix}.json",
        "role_decisions": resolved / f"role_decisions{suffix}.json",
        "quality": resolved / f"quality_report{suffix}.json",
        "speaker_state": session / "derived" / "preprocess" / "echo" / "speaker_state.jsonl",
        "local_fir_report": session / "derived" / "preprocess" / "echo" / "local_fir_report.json",
        "mic_raw": session / "audio" / "mic" / "000001.caf",
        "remote": session / "audio" / "remote" / "000001.caf",
        "mic_clean": session / "derived" / "preprocess" / "audio" / "mic_clean_local_fir.wav",
        "mic_role_masked": session / "derived" / "preprocess" / "audio" / "mic_role_masked_for_asr.wav",
    }


def session_delay_ms(paths: dict[str, Path]) -> float | None:
    path = paths["local_fir_report"]
    if not path.exists():
        return None
    try:
        report = read_json(path)
        summary = report.get("summary")
        if isinstance(summary, dict) and summary.get("median_delay_ms") is not None:
            return float(summary["median_delay_ms"])
    except Exception:
        return None
    return None


def feature_record(interval: dict[str, Any], audios: dict[str, np.ndarray], states: list[dict[str, Any]], delay_ms: float | None, calibration: dict[str, Any]) -> dict[str, Any]:
    me = interval["me"]
    remote = interval["remote"]
    text = text_features(str(me.get("text") or ""), str(remote.get("text") or ""))
    boundary = interval_boundary_features(interval, me, remote)
    boundary["time_overlap_ratio"] = round(
        interval["duration_sec"] / max(EPS, min(float(me["end"]) - float(me["start"]), float(remote["end"]) - float(remote["start"]))),
        6,
    )
    return {
        "speaker_state": weighted_state_features(interval["start"], interval["end"], states),
        "audio": audio_features(audios, delay_ms, calibration),
        "text": text,
        "interval": boundary,
    }


def attach_clips(
    *,
    record: dict[str, Any],
    sources: dict[str, Path],
    clips_dir: Path,
    selected: bool,
) -> None:
    if not selected:
        record["clips"] = {}
        record["commands"] = {}
        return
    clip_id = record["id"]
    start = max(0.0, float(record["interval"]["start"]) - 3.0)
    end = float(record["interval"]["end"]) + 3.0
    duration = end - start
    outputs = {
        "mic_raw": clips_dir / f"{clip_id}_mic_raw.wav",
        "remote": clips_dir / f"{clip_id}_remote.wav",
        "mic_clean": clips_dir / f"{clip_id}_mic_clean.wav",
        "mic_role_masked": clips_dir / f"{clip_id}_mic_role_masked.wav",
    }
    extract_wav(sources["mic_raw"], outputs["mic_raw"], start, duration)
    extract_wav(sources["remote"], outputs["remote"], start, duration)
    extract_wav(sources["mic_clean"], outputs["mic_clean"], start, duration)
    extract_wav(sources["mic_role_masked"], outputs["mic_role_masked"], start, duration)
    stereo_raw = clips_dir / f"{clip_id}_stereo_mic_left_remote_right.wav"
    stereo_clean = clips_dir / f"{clip_id}_stereo_clean_left_remote_right.wav"
    write_stereo(outputs["mic_raw"], outputs["remote"], stereo_raw)
    write_stereo(outputs["mic_clean"], outputs["remote"], stereo_clean)
    record["clips"] = {
        **{key: str(path) for key, path in outputs.items()},
        "stereo_mic_left_remote_right": str(stereo_raw),
        "stereo_clean_left_remote_right": str(stereo_clean),
    }
    record["commands"] = {key: f"afplay {path}" for key, path in record["clips"].items()}


def choose_clip_ids(records: list[dict[str, Any]], max_clips: int) -> set[str]:
    priority = {
        "needs_human_review": 0,
        "probable_duplicate": 1,
        "probable_remote_leak": 2,
        "probable_asr_noise": 3,
        "probable_double_talk": 4,
        "probable_timing_overlap": 5,
    }
    ordered = sorted(
        records,
        key=lambda row: (
            priority.get(row["classification"]["label"], 9),
            -float(row["classification"]["confidence"]),
            -float(row["interval"]["duration_sec"]),
        ),
    )
    return {row["id"] for row in ordered[: max(0, max_clips)]}


def summarize(records: list[dict[str, Any]], quality: dict[str, Any], profile: str, calibration: dict[str, Any]) -> dict[str, Any]:
    by_label: dict[str, dict[str, Any]] = {}
    for row in records:
        label = row["classification"]["label"]
        bucket = by_label.setdefault(label, {"count": 0, "seconds": 0.0})
        bucket["count"] += 1
        bucket["seconds"] += float(row["interval"]["duration_sec"])
    for bucket in by_label.values():
        bucket["seconds"] = round(bucket["seconds"], 3)
    harmful_labels = {"probable_duplicate", "probable_remote_leak", "probable_asr_noise"}
    benign_labels = {"probable_double_talk", "probable_timing_overlap"}
    harmful_seconds = round(sum(by_label.get(label, {}).get("seconds", 0.0) for label in harmful_labels), 3)
    benign_seconds = round(sum(by_label.get(label, {}).get("seconds", 0.0) for label in benign_labels), 3)
    review_seconds = round(by_label.get("needs_human_review", {}).get("seconds", 0.0), 3)
    total_seconds = round(sum(float(row["interval"]["duration_sec"]) for row in records), 3)
    old_verdict = str(quality.get("_existing_synthesis_verdict") or "unknown")
    if harmful_seconds < 30 and review_seconds < 30 and int(quality.get("unrepaired_long_mic_crossings_count", 0) or 0) == 0:
        new_verdict = "good"
    elif harmful_seconds < 120 and review_seconds < 120 and float(quality.get("local_only_island_recall", 0.0) or 0.0) >= 0.90:
        new_verdict = "usable_with_review"
    else:
        new_verdict = "risky"
    return {
        "schema": SCHEMA_SUMMARY,
        "profile": profile,
        "input_metrics": {
            "cross_role_overlap_gt2_count": quality.get("cross_role_overlap_gt2_count"),
            "cross_role_overlap_gt2_seconds": quality.get("cross_role_overlap_gt2_seconds"),
            "remote_duplicate_in_me_count": quality.get("remote_duplicate_in_me_count"),
            "remote_duplicate_in_me_seconds": quality.get("remote_duplicate_in_me_seconds"),
            "unrepaired_long_mic_crossings_count": quality.get("unrepaired_long_mic_crossings_count"),
            "local_only_island_recall": quality.get("local_only_island_recall"),
        },
        "classified": {
            "total_overlap_count": len(records),
            "total_overlap_seconds": total_seconds,
            "by_label": dict(sorted(by_label.items())),
        },
        "harmful": {
            "seconds": harmful_seconds,
            "labels": sorted(harmful_labels),
        },
        "benign_or_expected": {
            "seconds": benign_seconds,
            "labels": sorted(benign_labels),
        },
        "review": {
            "seconds": review_seconds,
            "count": by_label.get("needs_human_review", {}).get("count", 0),
        },
        "recommended_verdict_adjustment": {
            "old": old_verdict,
            "new": new_verdict,
            "informational_only": True,
            "reason": "based on group overlap audit labels; quality_verdict is not modified",
        },
        "calibration": calibration,
    }


def write_review_markdown(path: Path, records: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    lines = [
        "# Group Overlap Audit",
        "",
        "This report is diagnostic. It does not change transcript or quality verdict artifacts.",
        "",
        "## Summary",
        "",
        f"- Harmful overlap: `{summary['harmful']['seconds']}` sec",
        f"- Benign or expected overlap: `{summary['benign_or_expected']['seconds']}` sec",
        f"- Needs human review: `{summary['review']['seconds']}` sec",
        "",
        "## By Label",
        "",
    ]
    for label, bucket in summary["classified"]["by_label"].items():
        lines.append(f"- `{label}`: `{bucket['count']}` intervals, `{bucket['seconds']}` sec")

    def add_section(title: str, rows: list[dict[str, Any]]) -> None:
        lines.extend(["", f"## {title}", ""])
        if not rows:
            lines.append("- none")
            return
        for index, row in enumerate(rows[:10], start=1):
            me = row["utterances"]["me"]
            remote = row["utterances"]["remote"]
            commands = row.get("commands") or {}
            lines.extend(
                [
                    f"### {index}. {row['classification']['label']} {row['interval']['start_time']}-{row['interval']['end_time']}",
                    "",
                    f"- Confidence: `{row['classification']['confidence']}`",
                    f"- Suggestion: `{row['classification']['action_suggestion']}`",
                    f"- Me `{me['id']}`: {me['text']}",
                    f"- Colleagues `{remote['id']}`: {remote['text']}",
                ]
            )
            if commands:
                lines.append(f"- Stereo clean/remote: `{commands.get('stereo_clean_left_remote_right', '')}`")
                lines.append(f"- Raw mic: `{commands.get('mic_raw', '')}`")
                lines.append(f"- Remote: `{commands.get('remote', '')}`")
            lines.append("")

    harmful = [row for row in records if row["classification"]["label"] in {"probable_duplicate", "probable_remote_leak", "probable_asr_noise"}]
    needs = [row for row in records if row["classification"]["label"] == "needs_human_review"]
    examples: list[dict[str, Any]] = []
    seen_labels: set[str] = set()
    for row in sorted(records, key=lambda item: (-item["classification"]["confidence"], -item["interval"]["duration_sec"])):
        label = row["classification"]["label"]
        if label in seen_labels:
            continue
        examples.append(row)
        seen_labels.add(label)

    add_section("Top Needs Review", sorted(needs, key=lambda row: (-row["interval"]["duration_sec"], row["interval"]["start"])))
    add_section("Top High-Confidence Harmful", sorted(harmful, key=lambda row: (-row["classification"]["confidence"], -row["interval"]["duration_sec"])))
    add_section("Examples By Class", examples)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def format_time(seconds: float) -> str:
    total = max(0, int(seconds))
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Classify group-call Me/Colleagues overlap intervals.")
    parser.add_argument("session", type=Path)
    parser.add_argument("--profile", default="shadow_v2", choices=["current", "shadow_v2"])
    parser.add_argument("--min-overlap-sec", type=float, default=0.5)
    parser.add_argument("--review-threshold-sec", type=float, default=2.0)
    parser.add_argument("--write-clips", action="store_true")
    parser.add_argument("--max-clips", type=int, default=80)
    args = parser.parse_args()

    session = args.session
    paths = profile_paths(session, args.profile)
    for key in ("dialogue", "overlaps", "role_decisions", "speaker_state", "mic_raw", "remote", "mic_clean", "mic_role_masked"):
        require_path(paths[key], key)

    dialogue = read_json(paths["dialogue"])
    overlaps = read_json(paths["overlaps"])
    quality = read_json(paths["quality"]) if paths["quality"].exists() else {}
    synthesis_verdict_path = session / "derived" / "synthesis-simple" / "extractive" / "quality_verdict.json"
    if synthesis_verdict_path.exists():
        try:
            synthesis_verdict = read_json(synthesis_verdict_path)
            quality["_existing_synthesis_verdict"] = synthesis_verdict.get("verdict")
        except Exception:
            pass
    states = read_jsonl(paths["speaker_state"])
    utterances = dialogue.get("utterances")
    overlap_rows = overlaps.get("overlaps")
    if not isinstance(utterances, list) or not isinstance(overlap_rows, list):
        raise ValueError("dialogue/overlaps artifacts have unexpected shape")

    by_id = {str(row.get("id")): row for row in utterances if isinstance(row, dict)}
    intervals = build_intervals(overlap_rows, by_id, args.min_overlap_sec)
    out_dir = session / "derived" / "audit" / "group-overlaps"
    clips_dir = out_dir / "clips"
    out_dir.mkdir(parents=True, exist_ok=True)
    sources = {
        "mic_raw": paths["mic_raw"],
        "remote": paths["remote"],
        "mic_clean": paths["mic_clean"],
        "mic_role_masked": paths["mic_role_masked"],
    }
    delay_ms = session_delay_ms(paths)

    with tempfile.TemporaryDirectory(prefix="murmurmark-group-overlap-") as temp_name:
        temp_dir = Path(temp_name)
        calibration = calibrate_session(sources, states, delay_ms, temp_dir)
        records: list[dict[str, Any]] = []
        for index, interval in enumerate(intervals, start=1):
            audios = clip_audio_for_features(sources, interval["start"], interval["end"], temp_dir, interval["id"])
            features = feature_record(interval, audios, states, delay_ms, calibration)
            scores = compute_scores(features)
            classification = classify(scores, interval["duration_sec"])
            me = interval["me"]
            remote = interval["remote"]
            record = {
                "schema": SCHEMA_AUDIT,
                "id": interval["id"],
                "session_id": session.name,
                "profile": args.profile,
                "interval": {
                    "start": round(interval["start"], 3),
                    "end": round(interval["end"], 3),
                    "duration_sec": round(interval["duration_sec"], 3),
                    "start_time": format_time(interval["start"]),
                    "end_time": format_time(interval["end"]),
                    "severity": interval["severity"],
                    "source_overlap_index": interval["source_overlap_index"],
                },
                "utterances": {
                    "me": {
                        "id": str(me.get("id")),
                        "start": me.get("start"),
                        "end": me.get("end"),
                        "text": me.get("text"),
                        "needs_review": bool((me.get("quality") or {}).get("needs_review")),
                    },
                    "remote": {
                        "id": str(remote.get("id")),
                        "start": remote.get("start"),
                        "end": remote.get("end"),
                        "text": remote.get("text"),
                        "needs_review": bool((remote.get("quality") or {}).get("needs_review")),
                    },
                },
                "features": features,
                "scores": scores,
                "classification": classification,
                "review": {
                    "needs_human_review": classification["label"] == "needs_human_review",
                    "priority": interval["severity"] if interval["duration_sec"] >= args.review_threshold_sec else "low",
                },
            }
            records.append(record)

        clip_ids = choose_clip_ids(records, args.max_clips) if args.write_clips else set()
        if args.write_clips and clips_dir.exists():
            shutil.rmtree(clips_dir)
        if args.write_clips:
            clips_dir.mkdir(parents=True, exist_ok=True)
        for record in records:
            attach_clips(record=record, sources=sources, clips_dir=clips_dir, selected=record["id"] in clip_ids)

    summary = summarize(records, quality, args.profile, calibration)
    summary["session_id"] = session.name
    summary["inputs"] = {
        key: str(path.relative_to(session)) if path.is_relative_to(session) else str(path)
        for key, path in paths.items()
        if key not in {"quality"}
    }
    summary["generator"] = {
        "name": "audit-group-overlaps",
        "version": SCRIPT_VERSION,
        "mode": "deterministic",
    }
    write_jsonl(out_dir / "group_overlap_audit.jsonl", records)
    write_json(out_dir / "group_overlap_summary.json", summary)
    write_review_markdown(out_dir / "group_overlap_review.md", records, summary)
    suggestions = [
        {
            "schema": "murmurmark.group_overlap_patch_suggestion/v1",
            "overlap_id": row["id"],
            "action": row["classification"]["action_suggestion"],
            "label": row["classification"]["label"],
            "confidence": row["classification"]["confidence"],
            "me_utterance_id": row["utterances"]["me"]["id"],
            "remote_utterance_id": row["utterances"]["remote"]["id"],
            "apply_automatically": False,
        }
        for row in records
    ]
    write_jsonl(out_dir / "group_overlap_patch_suggestions.jsonl", suggestions)

    print(f"audit: {out_dir / 'group_overlap_audit.jsonl'}")
    print(f"summary: {out_dir / 'group_overlap_summary.json'}")
    print(f"review: {out_dir / 'group_overlap_review.md'}")
    print(f"overlaps: {len(records)}")
    print(f"harmful_seconds: {summary['harmful']['seconds']}")
    print(f"review_seconds: {summary['review']['seconds']}")


if __name__ == "__main__":
    main()
