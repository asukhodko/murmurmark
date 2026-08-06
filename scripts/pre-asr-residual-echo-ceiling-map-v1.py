#!/usr/bin/env python3
"""Map the evidence ceiling of remote residual after production echo v2."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import warnings
from collections import Counter, defaultdict
from contextlib import ExitStack
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import soundfile as sf
from scipy import signal


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_POLICY = ROOT / "policies/pre-asr-residual-echo-ceiling-map-v1.json"
DEFAULT_OUTPUT = ROOT / "sessions/_reports/pre-asr-residual-echo-map-v1"
SCHEMA_POLICY = "murmurmark.pre_asr_residual_echo_ceiling_policy/v1"
SCHEMA_FROZEN = "murmurmark.pre_asr_residual_echo_frozen_inputs/v1"
SCHEMA_EVENT = "murmurmark.pre_asr_residual_event/v1"
SCHEMA_SESSION = "murmurmark.pre_asr_residual_echo_session/v1"
SCHEMA_CORPUS = "murmurmark.pre_asr_residual_echo_corpus/v1"
SCHEMA_REQUIREMENTS = "murmurmark.pre_asr_residual_capability_requirements/v1"
SCHEMA_DECISION = "murmurmark.pre_asr_residual_echo_decision/v1"
SCHEMA_REPLAY = "murmurmark.pre_asr_residual_echo_replay/v1"
EPS = 1.0e-9
TOKEN_RE = re.compile(r"[\wёЁ]+", re.UNICODE)
KNOWN_HALLUCINATIONS = (
    re.compile(r"^\s*продолжение следует\s*[.!?…-]*\s*$", re.IGNORECASE),
    re.compile(r"^\s*субтитры.*$", re.IGNORECASE),
    re.compile(r"^\s*редактор субтитров.*$", re.IGNORECASE),
)
REMOTE_STATES = {"remote_only", "remote_only_correlation", "remote_only_level", "double_talk", "double_talk_correlation"}
LOCAL_STATES = {"local_only", "double_talk", "double_talk_correlation", "other_local"}
CONTENT_STOP = {
    "а", "без", "бы", "в", "во", "вот", "да", "для", "до", "его", "ее", "если", "же", "за",
    "и", "из", "или", "к", "как", "мы", "на", "не", "но", "ну", "о", "он", "она", "они",
    "от", "по", "при", "с", "со", "так", "там", "то", "у", "уже", "это", "я",
}


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"cannot read JSON {path}: {error}") from error
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise RuntimeError(f"invalid JSONL {path}:{number}: {error}") from error
        if isinstance(value, dict):
            rows.append(value)
    return rows


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    temporary.replace(path)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def stable_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def relative(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path.resolve())


def artifact(path: Path, root: Path) -> dict[str, Any]:
    if not path.is_file():
        raise RuntimeError(f"required frozen artifact is missing: {path}")
    stat = path.stat()
    return {
        "path": relative(path, root),
        "bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": sha256(path),
    }


def resolve(root: Path, value: str | Path) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def load_policy(path: Path, root: Path) -> dict[str, Any]:
    policy = read_json(path)
    if policy.get("schema") != SCHEMA_POLICY:
        raise RuntimeError("unexpected residual ceiling policy schema")
    if policy.get("status") != "locked_before_residual_analysis":
        raise RuntimeError("residual ceiling policy is not locked")
    if policy.get("training_use") != "forbidden" or policy.get("promotion_use") != "forbidden":
        raise RuntimeError("discovery corpus safety boundary is not locked")
    for key in ("production_policy", "production_corpus"):
        if not resolve(root, str(policy.get(key) or "")).is_file():
            raise RuntimeError(f"policy artifact is missing: {key}")
    return policy


def corpus_rows(policy: dict[str, Any], root: Path) -> list[dict[str, Any]]:
    production = read_json(resolve(root, str(policy["production_corpus"])))
    rows = [dict(row, source="production_v2_16_corpus") for row in production.get("sessions", [])]
    rows.extend(dict(row, source="fresh_discovery") for row in policy.get("additional_discovery_sessions", []))
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        session_id = str(row.get("id") or "")
        if not session_id or session_id in seen:
            raise RuntimeError(f"invalid or duplicate discovery session: {session_id!r}")
        seen.add(session_id)
        result.append(row)
    return result


def session_paths(root: Path, session_id: str, policy: dict[str, Any]) -> dict[str, Path]:
    session = root / "sessions" / session_id
    selector = str(policy["selector_profile"])
    output = session / "derived/preprocess" / selector
    transcript = session / "derived/transcript-simple/whisper-cpp"
    return {
        "session": session,
        "session_json": session / "session.json",
        "raw_mic": session / "audio/mic/000001.caf",
        "raw_remote": session / "audio/remote/000001.caf",
        "baseline_audio": session / "derived/asr/mic.wav",
        "remote_audio": session / "derived/asr/remote.wav",
        "baseline_mic_asr": transcript / "raw/mic.json",
        "remote_asr": transcript / "raw/remote.json",
        "speaker_state": session / "derived/preprocess/echo/speaker_state.jsonl",
        "local_fir_report": session / "derived/preprocess/echo/local_fir_report.json",
        "selection_report": output / "selection_report.json",
        "candidate_audio": output / "candidate_clean_mic_pcm16.wav",
        "selected_audio": output / "selected_clean_mic_pcm16.wav",
        "candidate_mic_asr": output / "direct-asr/raw/mic.json",
        "proposed_windows": output / "proposed_windows.jsonl",
        "selected_windows": output / "selected_windows.jsonl",
        "rejected_windows": output / "rejected_windows.jsonl",
        "safety_rollbacks": output / "safety_rollbacks.jsonl",
        "direct_asr_rollbacks": output / "direct_asr_local_rollbacks.jsonl",
        "diagnostic_chunks": output / "diagnostic_chunk_decisions.jsonl",
    }


def required_input_keys() -> tuple[str, ...]:
    return (
        "session_json", "raw_mic", "raw_remote", "baseline_audio", "remote_audio",
        "baseline_mic_asr", "remote_asr", "speaker_state", "local_fir_report",
        "selection_report", "candidate_audio", "selected_audio",
        "proposed_windows", "selected_windows", "rejected_windows",
    )


def optional_input_keys() -> tuple[str, ...]:
    return ("candidate_mic_asr", "safety_rollbacks", "direct_asr_rollbacks", "diagnostic_chunks")


def freeze_inputs(policy_path: Path, output: Path, root: Path) -> dict[str, Any]:
    policy = load_policy(policy_path, root)
    sessions: list[dict[str, Any]] = []
    for corpus_row in corpus_rows(policy, root):
        paths = session_paths(root, str(corpus_row["id"]), policy)
        inputs = {key: artifact(paths[key], root) for key in required_input_keys()}
        for key in optional_input_keys():
            if paths[key].is_file():
                inputs[key] = artifact(paths[key], root)
        sessions.append(
            {
                "session_id": corpus_row["id"],
                "expected_mode": corpus_row.get("expected_mode"),
                "source": corpus_row.get("source"),
                "inputs": inputs,
            }
        )
    basis = {
        "policy": artifact(policy_path, root),
        "production_policy": artifact(resolve(root, policy["production_policy"]), root),
        "production_corpus": artifact(resolve(root, policy["production_corpus"]), root),
        "runtime": artifact(Path(__file__), root),
        "sessions": sessions,
    }
    payload = {
        "schema": SCHEMA_FROZEN,
        "status": "frozen",
        "scope": "discovery_only_no_training_or_promotion",
        "basis": basis,
        "frozen_fingerprint": stable_digest(basis),
    }
    write_json(output / "frozen_inputs.json", payload)
    write_json(output / "policy_snapshot.json", policy)
    return payload


def verify_frozen(frozen: dict[str, Any], root: Path, full_hash: bool = True) -> dict[str, Any]:
    changed: list[str] = []
    missing: list[str] = []
    rows: list[dict[str, Any]] = []
    basis = frozen.get("basis") or {}
    entries = [basis.get("policy"), basis.get("production_policy"), basis.get("production_corpus"), basis.get("runtime")]
    for session in basis.get("sessions", []):
        entries.extend((session.get("inputs") or {}).values())
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        path = resolve(root, str(entry.get("path") or ""))
        if not path.is_file():
            missing.append(str(entry.get("path")))
            continue
        stat = path.stat()
        expected_bytes = entry.get("bytes")
        expected_mtime = entry.get("mtime_ns")
        same_metadata = (
            expected_bytes is not None
            and expected_mtime is not None
            and stat.st_size == int(expected_bytes)
            and stat.st_mtime_ns == int(expected_mtime)
        )
        same_hash = sha256(path) == entry.get("sha256") if full_hash or not same_metadata else True
        if not same_metadata or not same_hash:
            changed.append(str(entry.get("path")))
        rows.append({"path": entry.get("path"), "metadata_match": same_metadata, "sha256_match": same_hash})
    return {"passed": not missing and not changed, "missing": missing, "changed": changed, "rows": rows}


def normalize_tokens(text: str) -> list[str]:
    return [match.group(0).lower().replace("ё", "е") for match in TOKEN_RE.finditer(text)]


def is_content(token: str, min_chars: int) -> bool:
    return len(token) >= min_chars and token not in CONTENT_STOP and not token.isdigit()


def known_hallucination(text: str) -> bool:
    return any(pattern.match(text) for pattern in KNOWN_HALLUCINATIONS)


def asr_segments(path: Path) -> list[dict[str, Any]]:
    payload = read_json(path)
    rows: list[dict[str, Any]] = []
    for item in payload.get("transcription", []):
        if not isinstance(item, dict):
            continue
        offsets = item.get("offsets") if isinstance(item.get("offsets"), dict) else {}
        text = str(item.get("text") or "").strip()
        tokens = normalize_tokens(text)
        start = float(offsets.get("from") or 0.0) / 1000.0
        end = float(offsets.get("to") or 0.0) / 1000.0
        if end > start and tokens and not known_hallucination(text):
            rows.append({"start": start, "end": end, "text": text, "tokens": tokens})
    return rows


def interval_overlap(start: float, end: float, row: dict[str, Any], padding: float = 0.0) -> float:
    return max(0.0, min(end + padding, float(row.get("end") or 0.0)) - max(start - padding, float(row.get("start") or 0.0)))


def matched_tokens(expected: list[str], observed: list[str]) -> list[str]:
    common = Counter(expected) & Counter(observed)
    result: list[str] = []
    for token in expected:
        if common[token] > 0:
            result.append(token)
            common[token] -= 1
    return result


def support(reference: dict[str, Any], mic: list[dict[str, Any]], padding: float) -> dict[str, Any]:
    nearby = [row for row in mic if interval_overlap(reference["start"], reference["end"], row, padding) > 0.0]
    observed = [token for row in nearby for token in row["tokens"]]
    matched = matched_tokens(reference["tokens"], observed)
    return {
        "matched_tokens": matched,
        "matched_token_count": len(matched),
        "reference_token_count": len(reference["tokens"]),
        "match_ratio": round(len(matched) / max(len(reference["tokens"]), 1), 6),
        "mic_segment_count": len(nearby),
        "mic_text": " ".join(row["text"] for row in nearby),
        "mic_text_sha256": hashlib.sha256("\n".join(row["text"] for row in nearby).encode()).hexdigest(),
    }


def state_features(states: list[dict[str, Any]], start: float, end: float) -> dict[str, Any]:
    duration = max(end - start, EPS)
    totals: Counter[str] = Counter()
    delays: list[float] = []
    confidences: list[tuple[float, float]] = []
    for row in states:
        shared = interval_overlap(start, end, row)
        if shared <= 0.0:
            continue
        state_name = str(row.get("state") or "unknown")
        totals[state_name] += shared
        if row.get("delay_ms") is not None:
            delays.append(float(row["delay_ms"]))
        confidences.append((float(row.get("confidence") or 0.0), shared))
    remote = sum(value for key, value in totals.items() if key in REMOTE_STATES)
    local = sum(value for key, value in totals.items() if key in LOCAL_STATES)
    double = sum(value for key, value in totals.items() if "double_talk" in key)
    other_local = sum(value for key, value in totals.items() if "other_local" in key)
    return {
        "remote_state_ratio": round(min(1.0, remote / duration), 6),
        "local_state_ratio": round(min(1.0, local / duration), 6),
        "double_talk_ratio": round(min(1.0, double / duration), 6),
        "other_local_ratio": round(min(1.0, other_local / duration), 6),
        "states": {key: round(value, 3) for key, value in sorted(totals.items())},
        "delay_ms_median": round(float(np.median(delays)), 3) if delays else None,
        "confidence_mean": round(sum(value * weight for value, weight in confidences) / max(sum(weight for _, weight in confidences), EPS), 6),
    }


class AudioReader:
    def __init__(self, path: Path, target_rate: int) -> None:
        self.path = path
        self.handle = sf.SoundFile(path)
        self.target_rate = target_rate

    def close(self) -> None:
        self.handle.close()

    def read(self, start: float, end: float) -> np.ndarray:
        source_rate = int(self.handle.samplerate)
        start_frame = max(0, int(math.floor(start * source_rate)))
        end_frame = min(len(self.handle), int(math.ceil(end * source_rate)))
        if end_frame <= start_frame:
            return np.zeros(0, dtype=np.float32)
        self.handle.seek(start_frame)
        values = self.handle.read(end_frame - start_frame, dtype="float32", always_2d=True)
        mono = np.mean(values, axis=1).astype(np.float32)
        if source_rate != self.target_rate and mono.size:
            divisor = math.gcd(source_rate, self.target_rate)
            mono = signal.resample_poly(mono, self.target_rate // divisor, source_rate // divisor).astype(np.float32)
        return mono


def align(left: np.ndarray, right: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    length = min(len(left), len(right))
    return left[:length], right[:length]


def rms_db(values: np.ndarray) -> float:
    if values.size == 0:
        return -120.0
    return round(max(-120.0, 20.0 * math.log10(float(np.sqrt(np.mean(values.astype(np.float64) ** 2))) + EPS)), 3)


def normalized_xcorr(remote: np.ndarray, mic: np.ndarray, sample_rate: int, max_lag_ms: float) -> dict[str, Any]:
    remote, mic = align(remote, mic)
    if len(remote) < 64:
        return {"max_corr": 0.0, "lag_ms": None, "peak_ratio": 0.0}
    remote = remote.astype(np.float64) - float(np.mean(remote))
    mic = mic.astype(np.float64) - float(np.mean(mic))
    denominator = len(remote) * float(np.std(remote)) * float(np.std(mic))
    if denominator < EPS:
        return {"max_corr": 0.0, "lag_ms": None, "peak_ratio": 0.0}
    correlation = signal.correlate(mic, remote, mode="full", method="fft")
    lags = signal.correlation_lags(len(mic), len(remote), mode="full")
    maximum = int(round(max_lag_ms * sample_rate / 1000.0))
    mask = np.abs(lags) <= maximum
    normalized = correlation[mask] / denominator
    selected_lags = lags[mask]
    best = int(np.argmax(np.abs(normalized)))
    value = float(abs(normalized[best]))
    median = float(np.median(np.abs(normalized))) + EPS
    return {
        "max_corr": round(value, 6),
        "lag_ms": round(float(selected_lags[best] * 1000.0 / sample_rate), 3),
        "peak_ratio": round(value / median, 6),
    }


def spectral_cosine(remote: np.ndarray, mic: np.ndarray, sample_rate: int) -> float:
    remote, mic = align(remote, mic)
    if len(remote) < 512:
        return 0.0
    nperseg = min(512, len(remote))
    _, _, remote_stft = signal.stft(remote, fs=sample_rate, nperseg=nperseg, noverlap=nperseg // 2)
    frequencies, _, mic_stft = signal.stft(mic, fs=sample_rate, nperseg=nperseg, noverlap=nperseg // 2)
    band = (frequencies >= 120.0) & (frequencies <= 7600.0)
    left = np.mean(np.log1p(np.abs(remote_stft[band])), axis=1)
    right = np.mean(np.log1p(np.abs(mic_stft[band])), axis=1)
    denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
    return round(float(np.dot(left, right) / denominator), 6) if denominator > EPS else 0.0


def speech_coherence(remote: np.ndarray, mic: np.ndarray, sample_rate: int) -> float:
    remote, mic = align(remote, mic)
    if len(remote) < 512:
        return 0.0
    nperseg = min(1024, len(remote))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        frequencies, coherence = signal.coherence(remote, mic, fs=sample_rate, nperseg=nperseg)
    values = np.nan_to_num(coherence, nan=0.0, posinf=0.0, neginf=0.0)
    band = (frequencies >= 300.0) & (frequencies <= 3400.0)
    return round(float(np.mean(values[band])), 6) if np.any(band) else 0.0


def pair_features(remote: np.ndarray, mic: np.ndarray, policy: dict[str, Any], expected_delay_ms: float | None) -> dict[str, Any]:
    audio = policy["audio_calibration"]
    sample_rate = int(audio["sample_rate"])
    xcorr = normalized_xcorr(remote, mic, sample_rate, float(audio["max_lag_ms"]))
    lag = xcorr.get("lag_ms")
    lag_consistent = lag is not None and expected_delay_ms is not None and abs(float(lag) - expected_delay_ms) <= float(audio["lag_consistency_tolerance_ms"])
    return {
        "remote_rms_db": rms_db(remote),
        "mic_rms_db": rms_db(mic),
        "xcorr": xcorr,
        "lag_consistent": bool(lag_consistent),
        "speech_band_coherence": speech_coherence(remote, mic, sample_rate),
        "spectral_cosine": spectral_cosine(remote, mic, sample_rate),
    }


def sample_windows(states: list[dict[str, Any]], kind: str, limit: int) -> list[tuple[float, float]]:
    matches: list[tuple[float, float]] = []
    for row in states:
        name = str(row.get("state") or "")
        if kind == "remote" and "remote_only" not in name:
            continue
        if kind == "local" and "local_only" not in name:
            continue
        if kind == "silence" and "silence" not in name:
            continue
        start = float(row.get("start") or 0.0)
        end = float(row.get("end") or start)
        if end - start >= 0.5:
            matches.append((start, end))
    if len(matches) <= limit:
        return matches
    step = len(matches) / limit
    return [matches[min(int(index * step), len(matches) - 1)] for index in range(limit)]


def percentile(values: list[float], q: float, default: float = 0.0) -> float:
    return round(float(np.percentile(values, q)), 6) if values else default


def calibrate(readers: dict[str, AudioReader], states: list[dict[str, Any]], policy: dict[str, Any], expected_delay_ms: float | None) -> dict[str, Any]:
    limit = int(policy["audio_calibration"]["windows_per_state_max"])
    baselines: dict[str, Any] = {}
    for kind in ("remote", "local", "silence"):
        values: defaultdict[str, list[float]] = defaultdict(list)
        for start, end in sample_windows(states, kind, limit):
            remote = readers["remote"].read(start, end)
            selected = readers["selected"].read(start, end)
            features = pair_features(remote, selected, policy, expected_delay_ms)
            values["xcorr"].append(float(features["xcorr"]["max_corr"]))
            values["coherence"].append(float(features["speech_band_coherence"]))
            values["spectral"].append(float(features["spectral_cosine"]))
            values["remote_rms"].append(float(features["remote_rms_db"]))
            values["mic_rms"].append(float(features["mic_rms_db"]))
        baselines[kind] = {
            "count": len(values["xcorr"]),
            **{f"{name}_p{q}": percentile(series, q, -120.0 if "rms" in name else 0.0) for name, series in values.items() for q in (50, 75, 90)},
        }
    configured = policy["audio_calibration"]
    local = baselines["local"]
    thresholds = {
        "xcorr_high": round(max(float(configured["xcorr_absolute_floor"]), float(local.get("xcorr_p90") or 0.0)), 6),
        "coherence_high": round(max(float(configured["coherence_absolute_floor"]), float(local.get("coherence_p90") or 0.0)), 6),
        "spectral_high": round(max(float(configured["spectral_cosine_absolute_floor"]), float(local.get("spectral_p90") or 0.0)), 6),
        "remote_rms_db_min": float(configured["remote_rms_db_min"]),
    }
    return {"baselines": baselines, "thresholds": thresholds}


def audio_event_features(readers: dict[str, AudioReader], start: float, end: float, policy: dict[str, Any], expected_delay_ms: float | None, calibration: dict[str, Any]) -> dict[str, Any]:
    material = policy["material_event"]
    padding = float(material["audio_context_padding_sec"])
    maximum = float(material["audio_analysis_max_sec"])
    begin = max(0.0, start - padding)
    finish = end + padding
    if finish - begin > maximum:
        center = (start + end) / 2.0
        begin = max(0.0, center - maximum / 2.0)
        finish = begin + maximum
    remote = readers["remote"].read(begin, finish)
    result = {
        key: pair_features(remote, reader.read(begin, finish), policy, expected_delay_ms)
        for key, reader in readers.items()
        if key != "remote"
    }
    result["analysis_interval"] = {"start": round(begin, 3), "end": round(finish, 3)}
    result["thresholds"] = calibration["thresholds"]
    return result


def overlapping_rows(rows: list[dict[str, Any]], start: float, end: float) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        row_start = row.get("start", row.get("hard_start_sec"))
        row_end = row.get("end", row.get("hard_end_sec"))
        if row_start is None or row_end is None:
            continue
        if max(0.0, min(end, float(row_end)) - max(start, float(row_start))) > 0.0:
            result.append(row)
    return result


def window_evidence(paths: dict[str, Path], start: float, end: float, cache: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    proposed = overlapping_rows(cache["proposed_windows"], start, end)
    selected = overlapping_rows(cache["selected_windows"], start, end)
    rejected = overlapping_rows(cache["rejected_windows"], start, end)
    diagnostics = overlapping_rows(cache["diagnostic_chunks"], start, end)
    proposal_ids = {str(row.get("proposal_id") or "") for row in proposed}
    safety: list[dict[str, Any]] = []
    for row in cache["safety_rollbacks"] + cache["direct_asr_rollbacks"]:
        removed = {str(value) for value in row.get("removed_window_ids", [])}
        regressions = overlapping_rows(list(row.get("regressions") or []), start, end)
        if proposal_ids & removed or regressions:
            safety.append(row)
    target_values: defaultdict[str, list[float]] = defaultdict(list)
    for row in proposed + selected:
        for key in (
            "wavlm_target_me_similarity", "resemblyzer_target_me_similarity",
            "wavlm_target_remote_margin", "resemblyzer_target_remote_margin",
            "wavlm_mic_remote_similarity", "resemblyzer_mic_remote_similarity",
            "train_calibrated_remote_score",
        ):
            if row.get(key) is not None:
                target_values[key].append(float(row[key]))
    return {
        "proposed_count": len(proposed),
        "selected_count": len(selected),
        "rejected_count": len(rejected),
        "rejected_reasons": dict(sorted(Counter(str(row.get("rejected_reason") or "unknown") for row in rejected).items())),
        "diagnostic_reasons": dict(sorted(Counter(str(row.get("reason") or "unknown") for row in diagnostics).items())),
        "safety_rollback_reasons": sorted({str(row.get("reason") or "unknown") for row in safety}),
        "proposal_ids": sorted(value for value in proposal_ids if value),
        "selected_proposal_ids": sorted(str(row.get("proposal_id")) for row in selected if row.get("proposal_id")),
        "target_me": {key: {"min": round(min(values), 6), "max": round(max(values), 6), "mean": round(sum(values) / len(values), 6)} for key, values in sorted(target_values.items())},
    }


def boundary_near(states: list[dict[str, Any]], start: float, end: float, distance: float) -> bool:
    ordered = sorted(states, key=lambda row: (float(row.get("start") or 0.0), float(row.get("end") or 0.0)))
    boundaries: set[float] = set()
    for previous, current in zip(ordered, ordered[1:]):
        previous_end = float(previous.get("end") or 0.0)
        current_start = float(current.get("start") or 0.0)
        if abs(previous_end - current_start) > 0.05:
            continue
        if str(previous.get("state") or "unknown") != str(current.get("state") or "unknown"):
            boundaries.add(round((previous_end + current_start) / 2.0, 6))
    return any(abs(start - value) <= distance or abs(end - value) <= distance for value in boundaries)


def score_evidence(event: dict[str, Any], policy: dict[str, Any]) -> dict[str, int]:
    production = event["text_support"]["production"]
    baseline = event["text_support"]["baseline"]
    candidate = event["text_support"]["candidate"]
    text = 0
    if production["matched_token_count"] >= 2:
        text += 40
    if production["match_ratio"] >= 0.5:
        text += 20
    if event["material"]["matched_content_token_count"] >= 1:
        text += 20
    if baseline["matched_token_count"] >= 2:
        text += 10
    if candidate["matched_token_count"] >= 2:
        text += 10

    selected_audio = (event.get("audio") or {}).get("selected") or {}
    thresholds = ((event.get("audio") or {}).get("thresholds") or {})
    remote_audio = 0
    if float((selected_audio.get("xcorr") or {}).get("max_corr") or 0.0) >= float(thresholds.get("xcorr_high") or 1.0):
        remote_audio += 30
    if float(selected_audio.get("speech_band_coherence") or 0.0) >= float(thresholds.get("coherence_high") or 1.0):
        remote_audio += 25
    if float(selected_audio.get("spectral_cosine") or 0.0) >= float(thresholds.get("spectral_high") or 1.0):
        remote_audio += 20
    if selected_audio.get("lag_consistent"):
        remote_audio += 15
    if float(selected_audio.get("remote_rms_db") or -120.0) >= float(thresholds.get("remote_rms_db_min") or -58.0):
        remote_audio += 10

    state = event["speaker_state"]
    target = event["window_evidence"]["target_me"]
    classification = policy["classification"]
    target_guard = (
        float((target.get("resemblyzer_target_me_similarity") or {}).get("max") or -1.0) >= float(classification["target_resemblyzer_similarity_guard"])
        or float((target.get("wavlm_target_me_similarity") or {}).get("max") or -1.0) >= float(classification["target_wavlm_similarity_guard"])
        or float((target.get("wavlm_target_remote_margin") or {}).get("max") or -1.0) >= float(classification["target_remote_margin_guard"])
        or float((target.get("resemblyzer_target_remote_margin") or {}).get("max") or -1.0) >= float(classification["target_remote_margin_guard"])
    )
    local = 0
    if state["local_state_ratio"] >= 0.5:
        local += 45
    elif state["local_state_ratio"] >= 0.25:
        local += 25
    if state["double_talk_ratio"] >= 0.25:
        local += 30
    if state["other_local_ratio"] >= 0.25:
        local += 35
    if target_guard:
        local += 25
    if float(selected_audio.get("mic_rms_db") or -120.0) > -45.0 and remote_audio < 40:
        local += 15
    if state["remote_state_ratio"] >= 0.8 and state["local_state_ratio"] < 0.1:
        local -= 15
    return {"text": min(100, text), "audio_remote": min(100, remote_audio), "local": max(0, min(100, local)), "target_guard": int(target_guard)}


def classify_event(event: dict[str, Any], policy: dict[str, Any]) -> tuple[str, str, str, list[str]]:
    scores = event["scores"]
    state = event["speaker_state"]
    config = policy["classification"]
    text_strong = scores["text"] >= int(config["text_evidence_strong_min"])
    audio_strong = scores["audio_remote"] >= int(config["audio_remote_strong_min"])
    remote_convergent = (
        scores["audio_remote"] >= int(config["audio_remote_medium_min"])
        and state["remote_state_ratio"] >= float(config["remote_state_strong_min"])
    )
    audio_weak = scores["audio_remote"] <= int(config["audio_remote_weak_max"])
    local_strong = scores["local"] >= int(config["local_evidence_strong_min"])
    local_weak = scores["local"] <= int(config["local_evidence_weak_max"])
    reasons: list[str] = []
    if local_strong and text_strong and (audio_strong or state["remote_state_ratio"] >= 0.5):
        truth = "mixed_double_talk"
        reasons.append("independent_local_and_remote_evidence")
    elif state["other_local_ratio"] >= 0.5 and not scores["target_guard"]:
        truth = "other_local"
        reasons.append("explicit_other_local_state_without_target_guard")
    elif local_strong and audio_weak:
        truth = "target_me"
        reasons.append("strong_local_evidence_without_remote_acoustic_support")
    elif text_strong and (audio_strong or remote_convergent) and local_weak:
        truth = "confirmed_remote_echo"
        reasons.append(
            "remote_text_and_audio_agree_with_weak_local_evidence"
            if audio_strong
            else "remote_text_state_and_medium_audio_converge_with_weak_local_evidence"
        )
    elif text_strong and audio_weak and local_weak:
        truth = "asr_instability"
        reasons.append("remote_text_support_without_remote_audio_support")
    else:
        truth = "unknown"
        reasons.append("independent_evidence_does_not_converge")

    applicability = str(event["production"].get("applicability") or "")
    window = event["window_evidence"]
    selected_audio = (event.get("audio") or {}).get("selected") or {}
    if applicability == "not_applicable_exact_fallback":
        blocker = "unsupported_mode"
    elif truth == "asr_instability":
        blocker = "metric_artifact"
    elif truth in {"mixed_double_talk", "other_local", "target_me"} or state["local_state_ratio"] >= 0.25 or "local_state_guard" in window["rejected_reasons"]:
        blocker = "local_preservation_guard"
    elif scores["target_guard"]:
        blocker = "target_identity_uncertainty"
    elif event["boundary_near"] and window["selected_count"] == 0:
        blocker = "boundary_guard"
    elif audio_strong and not selected_audio.get("lag_consistent"):
        blocker = "alignment_uncertainty"
    elif truth == "confirmed_remote_echo":
        blocker = "echo_path_mismatch"
    else:
        blocker = "insufficient_evidence"

    capability = {
        "alignment_uncertainty": "alignment_or_echo_model_v3",
        "echo_path_mismatch": "alignment_or_echo_model_v3",
        "boundary_guard": "alignment_or_echo_model_v3",
        "target_identity_uncertainty": "target_speaker_model",
        "local_preservation_guard": "multi_component_separator",
        "metric_artifact": "remote_metric_repair",
        "unsupported_mode": "no_action_unsupported",
        "insufficient_evidence": "more_supervision",
    }[blocker]
    reasons.append(f"production_blocker:{blocker}")
    return truth, blocker, capability, reasons


def find_source_metrics(selection: dict[str, Any]) -> dict[str, Any]:
    source = selection.get("source_runtime") or {}
    direct = source.get("metrics") if isinstance(source, dict) else None
    if isinstance(direct, dict) and "remote_like_before" in direct:
        return direct
    details = source.get("details") if isinstance(source, dict) else None
    nested = details.get("metrics") if isinstance(details, dict) else None
    return nested if isinstance(nested, dict) else {}


def cache_windows(paths: dict[str, Path]) -> dict[str, list[dict[str, Any]]]:
    return {key: read_jsonl(paths[key]) for key in (
        "proposed_windows", "selected_windows", "rejected_windows", "safety_rollbacks",
        "direct_asr_rollbacks", "diagnostic_chunks",
    )}


def expected_mode(local_fir: dict[str, Any]) -> str:
    value = ((local_fir.get("acoustic_mode") or {}).get("mode"))
    return str(value or "unknown")


def session_report(session_row: dict[str, Any], policy: dict[str, Any], root: Path, frozen_session: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    session_id = str(session_row["id"])
    paths = session_paths(root, session_id, policy)
    selection = read_json(paths["selection_report"])
    local_fir = read_json(paths["local_fir_report"])
    states = read_jsonl(paths["speaker_state"])
    baseline = asr_segments(paths["baseline_mic_asr"])
    candidate = asr_segments(paths["candidate_mic_asr"]) if paths["candidate_mic_asr"].is_file() else []
    remote = asr_segments(paths["remote_asr"])
    status = str(selection.get("status") or "fallback")
    production_mic = candidate if status == "candidate" else baseline
    applicability_value = selection.get("applicability")
    applicability = str((applicability_value or {}).get("classification") or applicability_value or "unknown") if isinstance(applicability_value, dict) else str(applicability_value or "unknown")
    production = {
        "status": status,
        "applicability": applicability,
        "reason": selection.get("reason"),
        "candidate_asr_available": paths["candidate_mic_asr"].is_file(),
    }
    delay = ((local_fir.get("parameters") or {}).get("delay_ms"))
    delay_ms = float(delay) if delay is not None else None
    windows = cache_windows(paths)
    audio_config = policy["audio_calibration"]
    target_rate = int(audio_config["sample_rate"])
    with ExitStack() as stack:
        readers = {
            key: AudioReader(paths[path_key], target_rate)
            for key, path_key in (("remote", "remote_audio"), ("baseline", "baseline_audio"), ("candidate", "candidate_audio"), ("selected", "selected_audio"))
        }
        for reader in readers.values():
            stack.callback(reader.close)
        calibration = calibrate(readers, states, policy, delay_ms)
        events: list[dict[str, Any]] = []
        material_config = policy["material_event"]
        context = float(material_config["mic_context_padding_sec"])
        min_chars = int(material_config["content_token_min_chars"])
        for reference in remote:
            state = state_features(states, reference["start"], reference["end"])
            if state["remote_state_ratio"] < float(material_config["remote_state_ratio_min"]):
                continue
            text_support = {
                "baseline": support(reference, baseline, context),
                "candidate": support(reference, candidate, context),
                "production": support(reference, production_mic, context),
            }
            production_support = text_support["production"]
            if production_support["matched_token_count"] <= 0:
                continue
            matched_content = [token for token in production_support["matched_tokens"] if is_content(token, min_chars)]
            material = production_support["matched_token_count"] >= int(material_config["matched_tokens_min"]) and len(matched_content) >= int(material_config["matched_content_tokens_min"])
            duration = max(reference["end"] - reference["start"], 0.0)
            supported_seconds = duration * production_support["match_ratio"]
            event_id = stable_digest({"session": session_id, "start": reference["start"], "end": reference["end"], "remote_tokens": reference["tokens"]})[:20]
            row: dict[str, Any] = {
                "schema": SCHEMA_EVENT,
                "event_id": event_id,
                "session_id": session_id,
                "start": round(reference["start"], 3),
                "end": round(reference["end"], 3),
                "duration": round(duration, 3),
                "remote_supported_seconds": round(supported_seconds, 6),
                "remote_reference": {
                    "text": reference["text"],
                    "text_sha256": hashlib.sha256(reference["text"].encode()).hexdigest(),
                    "tokens": reference["tokens"],
                },
                "text_support": text_support,
                "material": {"passed": material, "matched_content_tokens": matched_content, "matched_content_token_count": len(matched_content)},
                "speaker_state": state,
                "production": production,
                "window_evidence": window_evidence(paths, reference["start"], reference["end"], windows),
                "boundary_near": boundary_near(states, reference["start"], reference["end"], float(policy["classification"]["boundary_distance_sec"])),
                "provenance": {"frozen_input_fingerprint": stable_digest(frozen_session["inputs"])},
            }
            if material:
                row["audio"] = audio_event_features(readers, reference["start"], reference["end"], policy, delay_ms, calibration)
            else:
                row["audio"] = {"status": "not_evaluated_nonmaterial", "thresholds": calibration["thresholds"]}
            row["scores"] = score_evidence(row, policy)
            if material:
                truth, blocker, capability, reasons = classify_event(row, policy)
            else:
                truth, blocker, capability, reasons = "unknown", "insufficient_evidence", "more_supervision", ["below_locked_material_event_threshold"]
            row.update({"signal_truth": truth, "production_blocker": blocker, "required_capability": capability, "reasons": reasons})
            events.append(row)

    metrics = find_source_metrics(selection)
    expected_key = "remote_like_after" if status == "candidate" else "remote_like_before"
    expected_value = (metrics.get(expected_key) or {}).get("seconds")
    expected = float(expected_value) if expected_value is not None else None
    actual = round(sum(event["remote_supported_seconds"] for event in events), 3)
    material_events = [event for event in events if event["material"]["passed"]]

    def grouped(field: str) -> dict[str, Any]:
        values: defaultdict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "seconds": 0.0, "sessions": [session_id]})
        for event in material_events:
            key = str(event[field])
            values[key]["count"] += 1
            values[key]["seconds"] += float(event["remote_supported_seconds"])
        return {key: {**value, "seconds": round(value["seconds"], 3)} for key, value in sorted(values.items())}

    report = {
        "schema": SCHEMA_SESSION,
        "session_id": session_id,
        "expected_mode": session_row.get("expected_mode"),
        "observed_mode": expected_mode(local_fir),
        "source": session_row.get("source"),
        "production": production,
        "calibration": calibration,
        "summary": {
            "event_count": len(events),
            "material_event_count": len(material_events),
            "remote_supported_seconds": actual,
            "material_remote_supported_seconds": round(sum(event["remote_supported_seconds"] for event in material_events), 3),
            "matched_tokens": sum(event["text_support"]["production"]["matched_token_count"] for event in events),
            "material_matched_tokens": sum(event["text_support"]["production"]["matched_token_count"] for event in material_events),
        },
        "by_signal_truth": grouped("signal_truth"),
        "by_production_blocker": grouped("production_blocker"),
        "by_required_capability": grouped("required_capability"),
        "reconciliation": {
            "metric": "remote_reference_token_support_v1",
            "expected_source": expected_key,
            "expected_seconds": round(expected, 3) if expected is not None else None,
            "actual_seconds": actual,
            "delta_seconds": round(actual - expected, 6) if expected is not None else None,
            "status": "reconciled" if expected is not None else "source_metric_not_available_exact_fallback",
            "passed": expected is None or abs(actual - expected) <= 0.01,
        },
        "events_fingerprint": stable_digest(events),
        "frozen_session_fingerprint": stable_digest(frozen_session),
    }
    return report, events


def merge_groups(session_reports: list[dict[str, Any]], field: str) -> dict[str, Any]:
    combined: defaultdict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "seconds": 0.0, "sessions": set()})
    for report in session_reports:
        for key, value in report[field].items():
            combined[key]["count"] += int(value["count"])
            combined[key]["seconds"] += float(value["seconds"])
            combined[key]["sessions"].add(report["session_id"])
    return {
        key: {"count": value["count"], "seconds": round(value["seconds"], 3), "session_count": len(value["sessions"]), "sessions": sorted(value["sessions"])}
        for key, value in sorted(combined.items())
    }


def decide(corpus: dict[str, Any], policy: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    total_material = float(corpus["summary"]["material_remote_supported_seconds"])
    capabilities = corpus["by_required_capability"]
    unsupported_seconds = float((capabilities.get("no_action_unsupported") or {}).get("seconds") or 0.0)
    total = max(0.0, total_material - unsupported_seconds)
    config = policy["capability_decision"]
    rows: list[dict[str, Any]] = []
    decision_map = {
        "target_speaker_model": "READY_FOR_TARGET_SPEAKER_MODEL_QUALIFICATION",
        "alignment_or_echo_model_v3": "READY_FOR_ALIGNMENT_OR_ECHO_MODEL_V3",
        "multi_component_separator": "READY_FOR_MULTI_COMPONENT_SEPARATOR",
        "remote_metric_repair": "REMOTE_METRIC_REPAIR_REQUIRED",
    }
    for name, values in capabilities.items():
        share = float(values["seconds"]) / max(total, EPS)
        material = share >= float(config["material_seconds_share_min"]) and int(values["session_count"]) >= int(config["sessions_min"])
        rows.append({
            "capability": name,
            "count": values["count"],
            "seconds": values["seconds"],
            "seconds_share": round(share, 6),
            "session_count": values["session_count"],
            "material": material,
            "dominant": material and share >= float(config["dominant_seconds_share_min"]),
            "eligible_decision": decision_map.get(name),
        })
    eligible = [row for row in rows if row["material"] and row["eligible_decision"]]
    eligible.sort(key=lambda row: (-float(row["seconds"]), str(row["capability"])))
    unknown_seconds = float(corpus["summary"].get("unknown_actionable_remote_supported_seconds") or 0.0)
    unknown_share = unknown_seconds / max(total, EPS)
    if total <= EPS:
        decision = "CURRENT_BASELINE_AT_MEASURED_CEILING"
        reason = "no_material_remote_supported_residual"
    elif unknown_share > float(config["unknown_seconds_share_max"]):
        decision = "NEEDS_MORE_SUPERVISION"
        reason = "unknown_evidence_exceeds_locked_limit"
    elif eligible:
        decision = str(eligible[0]["eligible_decision"])
        reason = f"largest_material_capability:{eligible[0]['capability']}"
    else:
        decision = "NEEDS_MORE_SUPERVISION"
        reason = "no_capability_reaches_locked_cross_session_materiality"
    requirements = {
        "schema": SCHEMA_REQUIREMENTS,
        "scope": "discovery_only",
        "total_material_remote_supported_seconds": round(total_material, 3),
        "actionable_material_remote_supported_seconds": round(total, 3),
        "unsupported_material_remote_supported_seconds": round(unsupported_seconds, 3),
        "unknown_seconds_share": round(unknown_share, 6),
        "thresholds": config,
        "capabilities": rows,
        "recommended_capability": eligible[0]["capability"] if eligible else None,
    }
    outcome = {
        "schema": SCHEMA_DECISION,
        "decision": decision,
        "reason": reason,
        "promotion_authorized": False,
        "training_authorized": False,
        "next_capability": requirements["recommended_capability"],
        "evidence": {
            "material_remote_supported_seconds": round(total_material, 3),
            "actionable_material_remote_supported_seconds": round(total, 3),
            "material_event_count": corpus["summary"]["material_event_count"],
            "session_count": corpus["summary"]["session_count"],
            "unknown_seconds_share": round(unknown_share, 6),
        },
    }
    if decision not in policy["allowed_decisions"]:
        raise RuntimeError(f"decision is not allowed by policy: {decision}")
    return requirements, outcome


def render_summary(corpus: dict[str, Any], decision: dict[str, Any]) -> str:
    summary = corpus["summary"]
    lines = [
        "# Pre-ASR Residual Echo Ceiling Map v1", "",
        f"Decision: `{decision['decision']}`", "",
        f"Reason: `{decision['reason']}`", "",
        "## Scope", "",
        f"- Sessions: `{summary['session_count']}`",
        f"- Residual events: `{summary['event_count']}`; material: `{summary['material_event_count']}`",
        f"- Remote-supported seconds: `{summary['remote_supported_seconds']:.3f}`; material: `{summary['material_remote_supported_seconds']:.3f}`",
        f"- Matched tokens: `{summary['matched_tokens']}`; material: `{summary['material_matched_tokens']}`", "",
        "## Required Capabilities", "",
        "| Capability | Events | Seconds | Sessions |", "|---|---:|---:|---:|",
    ]
    for key, value in corpus["by_required_capability"].items():
        lines.append(f"| `{key}` | {value['count']} | {value['seconds']:.3f} | {value['session_count']} |")
    lines.extend(["", "## Signal Truth", "", "| Class | Events | Seconds |", "|---|---:|---:|"])
    for key, value in corpus["by_signal_truth"].items():
        lines.append(f"| `{key}` | {value['count']} | {value['seconds']:.3f} |")
    lines.extend(["", "This report is discovery-only. It does not authorize training or production promotion.", ""])
    return "\n".join(lines)


def run_corpus(policy_path: Path, output: Path, root: Path) -> dict[str, Any]:
    policy = load_policy(policy_path, root)
    frozen_path = output / "frozen_inputs.json"
    if not frozen_path.is_file():
        raise RuntimeError("frozen_inputs.json is missing; run freeze first")
    frozen = read_json(frozen_path)
    frozen_check = verify_frozen(frozen, root, full_hash=False)
    if not frozen_check["passed"]:
        raise RuntimeError(
            "frozen inputs changed: "
            f"missing={frozen_check['missing']} changed={frozen_check['changed']}"
        )
    frozen_by_id = {row["session_id"]: row for row in frozen["basis"]["sessions"]}
    session_reports: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    for session_row in corpus_rows(policy, root):
        session_id = str(session_row["id"])
        print(f"[residual-map] {session_id}", flush=True)
        report, session_events = session_report(session_row, policy, root, frozen_by_id[session_id])
        write_json(output / "session_reports" / f"{session_id}.json", report)
        session_reports.append(report)
        events.extend(session_events)
    events.sort(key=lambda row: (row["session_id"], row["start"], row["end"], row["event_id"]))
    previous = read_json(output / "corpus_summary.json") if (output / "corpus_summary.json").is_file() else {}
    corpus = {
        "schema": SCHEMA_CORPUS,
        "scope": "discovery_only_no_training_or_promotion",
        "frozen_fingerprint": frozen["frozen_fingerprint"],
        "summary": {
            "session_count": len(session_reports),
            "event_count": len(events),
            "material_event_count": sum(1 for row in events if row["material"]["passed"]),
            "remote_supported_seconds": round(sum(row["remote_supported_seconds"] for row in events), 3),
            "material_remote_supported_seconds": round(sum(row["remote_supported_seconds"] for row in events if row["material"]["passed"]), 3),
            "matched_tokens": sum(row["text_support"]["production"]["matched_token_count"] for row in events),
            "material_matched_tokens": sum(row["text_support"]["production"]["matched_token_count"] for row in events if row["material"]["passed"]),
            "unknown_actionable_remote_supported_seconds": round(
                sum(
                    row["remote_supported_seconds"]
                    for row in events
                    if row["material"]["passed"]
                    and row["signal_truth"] == "unknown"
                    and row["required_capability"] != "no_action_unsupported"
                ),
                3,
            ),
            "reconciliation_passed_sessions": sum(1 for row in session_reports if row["reconciliation"]["passed"]),
        },
        "by_signal_truth": merge_groups(session_reports, "by_signal_truth"),
        "by_production_blocker": merge_groups(session_reports, "by_production_blocker"),
        "by_required_capability": merge_groups(session_reports, "by_required_capability"),
        "sessions": [
            {
                "session_id": row["session_id"],
                "production": row["production"],
                "observed_mode": row["observed_mode"],
                "summary": row["summary"],
                "reconciliation": row["reconciliation"],
                "events_fingerprint": row["events_fingerprint"],
            }
            for row in session_reports
        ],
        "events_fingerprint": stable_digest(events),
    }
    requirements, decision = decide(corpus, policy)
    corpus["decision"] = decision["decision"]
    corpus["corpus_fingerprint"] = stable_digest({key: value for key, value in corpus.items() if key != "corpus_fingerprint"})
    previous_fingerprint = previous.get("corpus_fingerprint")
    replay = {
        "schema": SCHEMA_REPLAY,
        "frozen_fingerprint": frozen["frozen_fingerprint"],
        "previous_corpus_fingerprint": previous_fingerprint,
        "current_corpus_fingerprint": corpus["corpus_fingerprint"],
        "deterministic_match": previous_fingerprint == corpus["corpus_fingerprint"] if previous_fingerprint else None,
    }
    write_jsonl(output / "residual_events.jsonl", events)
    write_json(output / "corpus_summary.json", corpus)
    (output / "corpus_summary.md").write_text(render_summary(corpus, decision), encoding="utf-8")
    write_json(output / "capability_requirements.json", requirements)
    write_json(output / "decision.json", decision)
    write_json(output / "replay_report.json", replay)
    return corpus


def verify_outputs(policy_path: Path, output: Path, root: Path, full_hash: bool) -> dict[str, Any]:
    policy = load_policy(policy_path, root)
    frozen = read_json(output / "frozen_inputs.json")
    corpus = read_json(output / "corpus_summary.json")
    decision = read_json(output / "decision.json")
    replay = read_json(output / "replay_report.json")
    events = read_jsonl(output / "residual_events.jsonl")
    frozen_check = verify_frozen(frozen, root, full_hash=full_hash)
    checks = {
        "frozen_schema": frozen.get("schema") == SCHEMA_FROZEN,
        "frozen_inputs_unchanged": frozen_check["passed"],
        "corpus_schema": corpus.get("schema") == SCHEMA_CORPUS,
        "decision_schema": decision.get("schema") == SCHEMA_DECISION,
        "decision_allowed": decision.get("decision") in policy["allowed_decisions"],
        "event_schemas": all(row.get("schema") == SCHEMA_EVENT for row in events),
        "event_count": len(events) == int((corpus.get("summary") or {}).get("event_count") or -1),
        "event_fingerprint": stable_digest(events) == corpus.get("events_fingerprint"),
        "all_sessions_reconciled": int((corpus.get("summary") or {}).get("reconciliation_passed_sessions") or 0) == int((corpus.get("summary") or {}).get("session_count") or -1),
        "replay_deterministic": replay.get("deterministic_match") is True,
        "audit_only": decision.get("promotion_authorized") is False and decision.get("training_authorized") is False,
    }
    result = {"passed": all(checks.values()), "checks": checks, "frozen": {"missing": frozen_check["missing"], "changed": frozen_check["changed"]}}
    if not result["passed"]:
        raise RuntimeError(f"residual ceiling verification failed: {result}")
    return result


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--root", type=Path, default=ROOT)
    value.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    value.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    value.add_argument("--nice", type=int, default=20)
    sub = value.add_subparsers(dest="command", required=True)
    sub.add_parser("freeze")
    sub.add_parser("run")
    verify = sub.add_parser("verify")
    verify.add_argument("--metadata-only", action="store_true")
    return value


def main() -> int:
    args = parser().parse_args()
    root = args.root.resolve()
    policy = resolve(root, args.policy)
    output = resolve(root, args.output)
    if args.nice > 0:
        try:
            os.nice(min(args.nice, 20))
        except OSError:
            pass
    if args.command == "freeze":
        payload = freeze_inputs(policy, output, root)
        print(f"frozen_sessions: {len(payload['basis']['sessions'])}")
        print(f"frozen_fingerprint: {payload['frozen_fingerprint']}")
        return 0
    if args.command == "run":
        corpus = run_corpus(policy, output, root)
        print(f"decision: {corpus['decision']}")
        print(f"material_remote_supported_seconds: {corpus['summary']['material_remote_supported_seconds']:.3f}")
        return 0
    result = verify_outputs(policy, output, root, full_hash=not args.metadata_only)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
