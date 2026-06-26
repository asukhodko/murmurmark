#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
from scipy import signal


SCHEMA_AUDIT = "murmurmark.audio_review_audit/v1"
SCHEMA_SUMMARY = "murmurmark.audio_review_summary/v1"
SCRIPT_VERSION = "0.1.0"
SAMPLE_RATE = 16000
EPS = 1e-9


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Classify audio review pack clips with local metrics.")
    parser.add_argument("session", type=Path)
    parser.add_argument("--pack-dir", type=Path, default=None)
    parser.add_argument("--out-dir", type=Path, default=None)
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as file:
            value = json.load(file)
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
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
    return rows


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


def read_audio(path: Path) -> np.ndarray:
    data, sr = sf.read(path, dtype="float32", always_2d=False)
    array = np.asarray(data, dtype=np.float32)
    if array.ndim > 1:
        array = np.mean(array, axis=1)
    if sr != SAMPLE_RATE and array.size:
        gcd = math.gcd(sr, SAMPLE_RATE)
        array = signal.resample_poly(array, SAMPLE_RATE // gcd, sr // gcd).astype(np.float32)
    return array


def align(left: np.ndarray, right: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    size = min(len(left), len(right))
    if size <= 0:
        return np.zeros(0, dtype=np.float32), np.zeros(0, dtype=np.float32)
    return left[:size], right[:size]


def rms_db(audio: np.ndarray) -> float:
    if audio.size == 0:
        return -120.0
    value = float(np.sqrt(np.mean(np.square(audio.astype(np.float64))) + EPS))
    return round(max(-120.0, 20.0 * math.log10(value + EPS)), 3)


def xcorr(reference: np.ndarray, target: np.ndarray, max_lag_ms: float = 500.0) -> dict[str, Any]:
    reference, target = align(reference, target)
    if len(reference) < 64 or len(target) < 64:
        return {"max_corr": 0.0, "lag_ms": None}
    ref = reference.astype(np.float64) - float(np.mean(reference))
    tar = target.astype(np.float64) - float(np.mean(target))
    ref_std = float(np.std(ref))
    tar_std = float(np.std(tar))
    if ref_std < EPS or tar_std < EPS:
        return {"max_corr": 0.0, "lag_ms": None}
    corr = signal.correlate(tar, ref, mode="full", method="fft")
    lags = signal.correlation_lags(len(tar), len(ref), mode="full")
    max_lag = int(round(max_lag_ms * SAMPLE_RATE / 1000.0))
    mask = np.abs(lags) <= max_lag
    if not np.any(mask):
        return {"max_corr": 0.0, "lag_ms": None}
    corr = corr[mask]
    lags = lags[mask]
    norm = corr / max(EPS, len(ref) * ref_std * tar_std)
    index = int(np.argmax(np.abs(norm)))
    return {"max_corr": round(float(abs(norm[index])), 6), "lag_ms": round(float(lags[index] * 1000.0 / SAMPLE_RATE), 3)}


def spectral_cosine(reference: np.ndarray, target: np.ndarray) -> float:
    reference, target = align(reference, target)
    if len(reference) < 512 or len(target) < 512:
        return 0.0
    try:
        _, _, ref_spec = signal.stft(reference, fs=SAMPLE_RATE, nperseg=512, noverlap=352)
        _, _, tar_spec = signal.stft(target, fs=SAMPLE_RATE, nperseg=512, noverlap=352)
    except Exception:
        return 0.0
    size = min(ref_spec.shape[1], tar_spec.shape[1])
    if size <= 0:
        return 0.0
    ref_vec = np.log1p(np.abs(ref_spec[:, :size])).reshape(-1)
    tar_vec = np.log1p(np.abs(tar_spec[:, :size])).reshape(-1)
    denom = float(np.linalg.norm(ref_vec) * np.linalg.norm(tar_vec))
    if denom < EPS:
        return 0.0
    return round(float(np.dot(ref_vec, tar_vec) / denom), 6)


def normalize_text(text: Any) -> str:
    value = str(text or "").lower().replace("ё", "е")
    value = re.sub(r"[^0-9a-zа-я_./+-]+", " ", value)
    return " ".join(token for token in value.split() if token)


def token_set(text: Any) -> set[str]:
    stop = {"а", "и", "ну", "да", "вот", "это", "как", "то", "же", "там", "тут", "у", "в", "на", "не"}
    return {token for token in normalize_text(text).split() if token not in stop and len(token) > 1}


def text_similarity(utterances: list[dict[str, Any]]) -> dict[str, Any]:
    me_texts = [str(row.get("text") or "") for row in utterances if str(row.get("role") or "").lower() == "me"]
    remote_texts = [str(row.get("text") or "") for row in utterances if "colleague" in str(row.get("role") or "").lower()]
    if not remote_texts:
        remote_texts = [str(row.get("text") or "") for row in utterances if str(row.get("source_track") or "").lower() == "remote"]
    if not me_texts or not remote_texts:
        return {"similarity": 0.0, "containment": 0.0, "me_text": " ".join(me_texts), "remote_text": " ".join(remote_texts)}
    me = " ".join(me_texts)
    remote = " ".join(remote_texts)
    me_norm = normalize_text(me)
    remote_norm = normalize_text(remote)
    sequence = SequenceMatcher(None, me_norm, remote_norm).ratio() if me_norm or remote_norm else 0.0
    me_tokens = token_set(me)
    remote_tokens = token_set(remote)
    if me_tokens and remote_tokens:
        containment = len(me_tokens & remote_tokens) / max(1, min(len(me_tokens), len(remote_tokens)))
        jaccard = len(me_tokens & remote_tokens) / max(1, len(me_tokens | remote_tokens))
    else:
        containment = 0.0
        jaccard = 0.0
    return {
        "similarity": round(max(sequence, containment, jaccard), 6),
        "sequence_ratio": round(sequence, 6),
        "containment": round(containment, 6),
        "jaccard": round(jaccard, 6),
        "me_text": me,
        "remote_text": remote,
    }


def load_clips(item: dict[str, Any]) -> dict[str, np.ndarray]:
    clips = item.get("clips") if isinstance(item.get("clips"), dict) else {}
    audios: dict[str, np.ndarray] = {}
    for name in ("mic_raw", "remote", "mic_clean", "mic_role_masked"):
        path_value = clips.get(name)
        if not path_value:
            continue
        path = Path(path_value)
        if path.exists():
            audios[name] = read_audio(path)
    return audios


def group_label(item: dict[str, Any]) -> tuple[str | None, float | None]:
    for context in item.get("source_contexts") or []:
        if not isinstance(context, dict) or context.get("type") != "group_overlap_audit":
            continue
        classification = context.get("classification") if isinstance(context.get("classification"), dict) else {}
        label = classification.get("label")
        confidence = classification.get("confidence")
        try:
            conf_value = float(confidence) if confidence is not None else None
        except (TypeError, ValueError):
            conf_value = None
        return str(label) if label else None, conf_value
    return None, None


def feature_record(item: dict[str, Any], audios: dict[str, np.ndarray]) -> dict[str, Any]:
    remote = audios.get("remote", np.zeros(0, dtype=np.float32))
    raw = audios.get("mic_raw", np.zeros(0, dtype=np.float32))
    clean = audios.get("mic_clean", np.zeros(0, dtype=np.float32))
    masked = audios.get("mic_role_masked", np.zeros(0, dtype=np.float32))
    rms = {
        "remote": rms_db(remote),
        "mic_raw": rms_db(raw),
        "mic_clean": rms_db(clean),
        "mic_role_masked": rms_db(masked),
    }
    correlations = {
        "raw": xcorr(remote, raw),
        "clean": xcorr(remote, clean),
        "role_masked": xcorr(remote, masked),
    }
    spectral = {
        "raw": spectral_cosine(remote, raw),
        "clean": spectral_cosine(remote, clean),
        "role_masked": spectral_cosine(remote, masked),
    }
    text = text_similarity(item.get("utterances") or [])
    return {
        "rms_db": rms,
        "energy_delta_db": {
            "mic_clean_vs_raw": round(rms["mic_clean"] - rms["mic_raw"], 3),
            "role_masked_vs_raw": round(rms["mic_role_masked"] - rms["mic_raw"], 3),
            "remote_vs_mic_raw": round(rms["remote"] - rms["mic_raw"], 3),
        },
        "xcorr": correlations,
        "spectral_cosine": spectral,
        "text": text,
        "source_reasons": item.get("source_reasons") or [],
    }


def score_item(item: dict[str, Any], features: dict[str, Any]) -> dict[str, int]:
    group, group_conf = group_label(item)
    rms = features["rms_db"]
    delta = features["energy_delta_db"]
    raw_corr = float(features["xcorr"]["raw"]["max_corr"])
    clean_corr = float(features["xcorr"]["clean"]["max_corr"])
    masked_corr = float(features["xcorr"]["role_masked"]["max_corr"])
    raw_spec = float(features["spectral_cosine"]["raw"])
    text_sim = float(features["text"]["similarity"])

    local_support = 0
    if rms["mic_clean"] > -45:
        local_support += 30
    if rms["mic_role_masked"] > -50:
        local_support += 25
    if raw_corr < 0.28 and clean_corr < 0.24:
        local_support += 25
    if text_sim < 0.55:
        local_support += 15
    if delta["role_masked_vs_raw"] <= -16:
        local_support -= 25
    local_support = max(0, min(100, local_support))

    remote_similarity = 0
    if raw_corr >= 0.35:
        remote_similarity += 30
    if clean_corr >= 0.28:
        remote_similarity += 25
    if masked_corr >= 0.25:
        remote_similarity += 15
    if raw_spec >= 0.72:
        remote_similarity += 15
    if text_sim >= 0.75:
        remote_similarity += 25
    remote_similarity = max(0, min(100, remote_similarity))

    remote_duplicate = 0
    if text_sim >= 0.75 and remote_similarity >= 50 and local_support < 60:
        remote_duplicate = max(remote_duplicate, 82)
    if group == "probable_duplicate" and (group_conf or 0.0) >= 0.85:
        remote_duplicate = max(remote_duplicate, int(round((group_conf or 0.85) * 100)))

    remote_leak = 0
    if raw_corr >= 0.40 and clean_corr >= 0.25 and local_support < 50:
        remote_leak = 78
    if group == "probable_remote_leak" and (group_conf or 0.0) >= 0.80:
        remote_leak = max(remote_leak, int(round((group_conf or 0.80) * 100)))

    asr_noise = 0
    short_text = max(len(features["text"]["me_text"].split()), len(features["text"]["remote_text"].split())) <= 3
    if short_text and rms["mic_role_masked"] <= -55 and local_support < 45:
        asr_noise = 78
    if group == "probable_asr_noise" and (group_conf or 0.0) >= 0.80:
        asr_noise = max(asr_noise, int(round((group_conf or 0.80) * 100)))

    double_talk = 0
    if group == "probable_double_talk" and local_support >= 45 and text_sim < 0.65:
        double_talk = max(double_talk, int(round((group_conf or 0.75) * 100)))
    if local_support >= 70 and remote_similarity >= 35 and text_sim < 0.55:
        double_talk = max(double_talk, 76)

    timing_overlap = 0
    if group == "probable_timing_overlap" and text_sim < 0.65:
        timing_overlap = max(timing_overlap, int(round((group_conf or 0.75) * 100)))

    lost_me = 0
    if "transcript_needs_review" in set(item.get("source_reasons") or []) and rms["mic_raw"] > -45 and rms["mic_clean"] <= -55:
        lost_me = 70

    likely_reliable = 0
    if local_support >= 65 and remote_duplicate < 70 and remote_leak < 70 and asr_noise < 70:
        likely_reliable = max(likely_reliable, local_support)
    if double_talk >= 70 or timing_overlap >= 70:
        likely_reliable = max(likely_reliable, max(double_talk, timing_overlap))

    return {
        "local_support": local_support,
        "remote_similarity": remote_similarity,
        "remote_duplicate": remote_duplicate,
        "remote_leak": remote_leak,
        "asr_noise": asr_noise,
        "double_talk": double_talk,
        "timing_overlap": timing_overlap,
        "lost_me": lost_me,
        "likely_reliable": likely_reliable,
    }


def classify(scores: dict[str, int], missing_audio: bool) -> dict[str, Any]:
    if missing_audio:
        return {
            "label": "missing_audio",
            "verdict": "needs_stronger_audio_judge",
            "confidence": 0.0,
            "reason": "review item has no readable audio clips",
        }
    labels = ["remote_duplicate", "remote_leak", "asr_noise", "lost_me", "double_talk", "timing_overlap", "likely_reliable"]
    ordered = sorted(((label, scores[label]) for label in labels), key=lambda item: item[1], reverse=True)
    top, top_score = ordered[0]
    second_score = ordered[1][1] if len(ordered) > 1 else 0
    if top_score < 68 or top_score - second_score < 10:
        return {
            "label": "uncertain",
            "verdict": "needs_stronger_audio_judge",
            "confidence": round(min(0.69, top_score / 100.0), 3),
            "reason": "local metrics are weak or conflicting",
            "top_score": top_score,
            "second_score": second_score,
        }
    if top in {"remote_duplicate", "remote_leak", "asr_noise", "lost_me"}:
        verdict = "probable_transcript_error"
    else:
        verdict = "likely_reliable"
    return {
        "label": top,
        "verdict": verdict,
        "confidence": round(max(0.70, min(0.99, top_score / 100.0)), 3),
        "reason": f"top local metric class is {top}",
        "top_score": top_score,
        "second_score": second_score,
    }


def audit_item(item: dict[str, Any]) -> dict[str, Any]:
    audios = load_clips(item)
    missing_audio = "remote" not in audios or "mic_raw" not in audios
    features = feature_record(item, audios)
    scores = score_item(item, features)
    classification = classify(scores, missing_audio)
    return {
        "schema": SCHEMA_AUDIT,
        "id": item.get("id"),
        "session_id": item.get("session_id"),
        "profile": item.get("profile"),
        "interval": item.get("interval"),
        "source_reasons": item.get("source_reasons"),
        "utterance_ids": item.get("utterance_ids"),
        "utterances": item.get("utterances"),
        "features": features,
        "scores": scores,
        "classification": classification,
        "clips": item.get("clips") or {},
        "commands": item.get("commands") or {},
    }


def summarize(records: list[dict[str, Any]], pack_summary: dict[str, Any] | None) -> dict[str, Any]:
    by_label: dict[str, dict[str, Any]] = {}
    by_verdict: dict[str, dict[str, Any]] = {}
    for row in records:
        duration = float((row.get("interval") or {}).get("duration_sec", 0.0) or 0.0)
        label = str(row.get("classification", {}).get("label") or "unknown")
        verdict = str(row.get("classification", {}).get("verdict") or "unknown")
        for bucket, key in ((by_label, label), (by_verdict, verdict)):
            value = bucket.setdefault(key, {"count": 0, "seconds": 0.0})
            value["count"] += 1
            value["seconds"] += duration
    for bucket in list(by_label.values()) + list(by_verdict.values()):
        bucket["seconds"] = round(bucket["seconds"], 3)
    probable_error = by_verdict.get("probable_transcript_error", {"count": 0, "seconds": 0.0})
    stronger = by_verdict.get("needs_stronger_audio_judge", {"count": 0, "seconds": 0.0})
    reliable = by_verdict.get("likely_reliable", {"count": 0, "seconds": 0.0})
    return {
        "schema": SCHEMA_SUMMARY,
        "generator": {"name": "audit-audio-review-pack", "version": SCRIPT_VERSION},
        "input_pack": pack_summary or {},
        "items": len(records),
        "by_label": dict(sorted(by_label.items())),
        "by_verdict": dict(sorted(by_verdict.items())),
        "probable_error": probable_error,
        "needs_stronger_audio_judge": stronger,
        "likely_reliable": reliable,
        "recommended_next_step": (
            "send_uncertain_clips_to_stronger_local_audio_judge"
            if stronger.get("count", 0) > 0
            else "review_probable_errors_before_using_transcript"
            if probable_error.get("count", 0) > 0
            else "no_extra_audio_judge_needed_for_current_pack"
        ),
    }


def write_markdown(path: Path, summary: dict[str, Any], records: list[dict[str, Any]]) -> None:
    lines = [
        "# Audio Review Audit",
        "",
        "This report uses only local metrics over the audio review pack. It does not change transcripts.",
        "",
        "## Summary",
        "",
        f"- Items: `{summary['items']}`",
        f"- Likely reliable: `{summary['likely_reliable'].get('count', 0)}` items, `{summary['likely_reliable'].get('seconds', 0.0)}` sec",
        f"- Probable transcript error: `{summary['probable_error'].get('count', 0)}` items, `{summary['probable_error'].get('seconds', 0.0)}` sec",
        f"- Needs stronger audio judge: `{summary['needs_stronger_audio_judge'].get('count', 0)}` items, `{summary['needs_stronger_audio_judge'].get('seconds', 0.0)}` sec",
        f"- Recommended next step: `{summary['recommended_next_step']}`",
        "",
        "## By Label",
        "",
    ]
    for label, bucket in summary["by_label"].items():
        lines.append(f"- `{label}`: `{bucket['count']}` items, `{bucket['seconds']}` sec")

    def section(title: str, verdict: str) -> None:
        rows = [row for row in records if row["classification"]["verdict"] == verdict]
        rows = sorted(rows, key=lambda item: (-float(item["classification"].get("confidence", 0.0)), -float(item["interval"]["duration_sec"])))
        lines.extend(["", f"## {title}", ""])
        if not rows:
            lines.append("- none")
            return
        for row in rows[:12]:
            lines.extend(
                [
                    f"### {row['id']} {row['interval']['start_time']}-{row['interval']['end_time']}",
                    "",
                    f"- Label: `{row['classification']['label']}`",
                    f"- Confidence: `{row['classification']['confidence']}`",
                    f"- Reasons: `{', '.join(row.get('source_reasons') or [])}`",
                ]
            )
            for utterance in row.get("utterances") or []:
                lines.append(f"- {utterance.get('role')} `{utterance.get('id')}`: {utterance.get('text')}")
            commands = row.get("commands") or {}
            if commands:
                lines.append(f"- Stereo clean/remote: `{commands.get('stereo_clean_left_remote_right', '')}`")
                lines.append(f"- Raw mic: `{commands.get('mic_raw', '')}`")
            lines.append("")

    section("Probable Transcript Errors", "probable_transcript_error")
    section("Needs Stronger Audio Judge", "needs_stronger_audio_judge")
    section("Likely Reliable Examples", "likely_reliable")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    session = args.session
    pack_dir = args.pack_dir or session / "derived/audit/audio-review-pack"
    out_dir = args.out_dir or pack_dir
    items = read_jsonl(pack_dir / "review_pack_items.jsonl")
    if not items:
        raise SystemExit(f"missing or empty review pack: {pack_dir / 'review_pack_items.jsonl'}")
    pack_summary = read_json(pack_dir / "review_pack_summary.json")
    records = [audit_item(item) for item in items]
    summary = summarize(records, pack_summary)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(out_dir / "audio_review_audit.jsonl", records)
    write_json(out_dir / "audio_review_summary.json", summary)
    write_markdown(out_dir / "audio_review_report.md", summary, records)
    print(f"items: {len(records)}")
    print(f"summary: {out_dir / 'audio_review_summary.json'}")
    print(f"report: {out_dir / 'audio_review_report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
