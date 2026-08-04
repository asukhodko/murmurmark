#!/usr/bin/env python3
"""Seed v2.15 diagnostic ASR caches for the frozen v2.16 corpus.

Only content-addressed whisper.cpp JSON results are copied. Candidate audio,
window selections and hard-test sessions are deliberately excluded.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import wave
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CORPUS_SET = (
    ROOT / "policies/speaker-preserving-neural-echo-v2-16-corpus-set.json"
)
DEFAULT_HARD_SET = (
    ROOT / "policies/speaker-preserving-neural-echo-v2-16-hard-set.json"
)
DEFAULT_MODEL = (
    Path.home()
    / ".local/share/murmurmark/models/whisper.cpp/ggml-large-v3-q5_0.bin"
)
TARGET_PROFILE = "speaker-preserving-neural-echo-v2-15"
REPORT_NAME = "cache_seed_v2_16.json"
COMPATIBLE_CACHE_SCHEMAS = {
    "murmurmark.spne_v24_chunk_asr_cache/v1",
    "murmurmark.spne_v26_chunk_asr_cache/v1",
    "murmurmark.spne_v27_chunk_asr_cache/v1",
}


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--corpus-set", type=Path, default=DEFAULT_CORPUS_SET)
    value.add_argument("--hard-set", type=Path, default=DEFAULT_HARD_SET)
    value.add_argument("--whisper-model", type=Path, default=DEFAULT_MODEL)
    return value


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def stable_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def pcm_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with wave.open(str(path), "rb") as source:
        if (
            source.getnchannels() != 1
            or source.getsampwidth() != 2
            or source.getframerate() != 16000
        ):
            raise ValueError("diagnostic clip is not mono PCM16 at 16 kHz")
        while frames := source.readframes(16000):
            digest.update(frames)
    return digest.hexdigest()


def validate_entry(
    entry: Path, *, model_sha256: str
) -> tuple[bool, str, dict[str, Any]]:
    metadata = entry / "cache.json"
    result = entry / "result.json"
    clip = entry / "clip.wav"
    payload = read_json(metadata)
    basis = payload.get("basis") if isinstance(payload.get("basis"), dict) else {}
    checks = {
        "directory_key": entry.name == stable_digest(basis) if basis else False,
        "schema": payload.get("schema") in COMPATIBLE_CACHE_SCHEMAS,
        "model": basis.get("model_sha256") == model_sha256,
        "language": basis.get("language") == "ru",
        "max_context": basis.get("max_context") == 0,
        "threads": basis.get("threads") == 6,
        "clip": clip.is_file(),
        "result": result.is_file(),
    }
    if checks["clip"]:
        try:
            checks["clip_sha256"] = pcm_sha256(clip) == basis.get("clip_sha256")
        except (OSError, EOFError, wave.Error, ValueError):
            checks["clip_sha256"] = False
    else:
        checks["clip_sha256"] = False
    result_payload = read_json(result) if checks["result"] else {}
    checks["result_json"] = isinstance(result_payload.get("transcription"), list)
    passed = all(checks.values())
    reason = "valid" if passed else ",".join(
        key for key, observed in checks.items() if not observed
    )
    return passed, reason, basis


def copy_entry(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    # A cache hit needs only metadata and the full JSON result. Avoid duplicating
    # hundreds of one-minute WAV clips across candidate revisions.
    shutil.copy2(source / "cache.json", destination / "cache.json")
    shutil.copy2(source / "result.json", destination / "result.json")


def validate_materialized_entry(
    destination: Path, *, basis: dict[str, Any]
) -> bool:
    metadata = read_json(destination / "cache.json")
    result = read_json(destination / "result.json")
    return (
        metadata.get("basis") == basis
        and isinstance(result.get("transcription"), list)
    )


def session_ids(path: Path) -> list[str]:
    payload = read_json(path)
    rows = payload.get("sessions") if isinstance(payload.get("sessions"), list) else []
    return [str(row.get("id")) for row in rows if isinstance(row, dict) and row.get("id")]


def seed_session(session: Path, *, model_sha256: str) -> dict[str, Any]:
    preprocess = session / "derived/preprocess"
    target_root = preprocess / TARGET_PROFILE
    target_cache = target_root / "diagnostic-asr-cache"
    target_cache.mkdir(parents=True, exist_ok=True)
    source_caches = sorted(
        path
        for path in preprocess.glob("speaker-preserving-neural-echo-v2-*/diagnostic-asr-cache")
        if path.resolve() != target_cache.resolve()
    )
    available_by_key: dict[str, dict[str, Any]] = {}
    rejected: list[dict[str, str]] = []
    seen_sources = 0
    for source_cache in source_caches:
        for source in sorted(path for path in source_cache.iterdir() if path.is_dir()):
            seen_sources += 1
            passed, reason, basis = validate_entry(source, model_sha256=model_sha256)
            if not passed:
                rejected.append({"source": str(source), "reason": reason})
                continue
            if source.name in available_by_key:
                continue
            destination = target_cache / source.name
            if destination.exists() and not validate_materialized_entry(
                destination, basis=basis
            ):
                shutil.rmtree(destination)
            if not destination.exists():
                copy_entry(source, destination)
            materialized = validate_materialized_entry(destination, basis=basis)
            available_by_key[source.name] = {
                "key": source.name,
                "source": str(source.relative_to(session)),
                "clip_sha256": basis["clip_sha256"],
                "materialized": materialized,
                "cache_json_sha256": sha256(destination / "cache.json")
                if materialized
                else None,
                "result_json_sha256": sha256(destination / "result.json")
                if materialized
                else None,
            }
    available = [available_by_key[key] for key in sorted(available_by_key)]
    materialized = [row for row in available if row["materialized"]]
    report = {
        "schema": "murmurmark.spne_v2_16_cache_seed/v1",
        "session_id": session.name,
        "target_profile": TARGET_PROFILE,
        "mode": "validated_content_addressed_json_copy",
        "model_sha256": model_sha256,
        "source_cache_count": len(source_caches),
        "source_entry_count": seen_sources,
        "validated_unique_entry_count": len(available),
        "materialized_entry_count": len(materialized),
        "rejected_entry_count": len(rejected),
        "entries": available,
        "rejected": rejected,
        "candidate_audio_copied": False,
        "selection_decisions_copied": False,
        "post_asr_cleanup_promotion_credit": 0,
    }
    report["fingerprint"] = stable_digest(report)
    write_json(target_root / REPORT_NAME, report)
    return report


def main() -> int:
    args = parser().parse_args()
    args.corpus_set = args.corpus_set.expanduser().resolve()
    args.hard_set = args.hard_set.expanduser().resolve()
    args.whisper_model = args.whisper_model.expanduser().resolve()
    if not args.whisper_model.is_file():
        raise SystemExit(f"whisper model not found: {args.whisper_model}")
    corpus_ids = session_ids(args.corpus_set)
    hard_ids = set(session_ids(args.hard_set))
    overlap = sorted(set(corpus_ids) & hard_ids)
    if overlap:
        raise SystemExit(f"corpus and hard sets overlap: {overlap}")
    model_sha = sha256(args.whisper_model)
    reports = []
    for session_id in corpus_ids:
        session = ROOT / "sessions" / session_id
        if not session.is_dir():
            raise SystemExit(f"corpus session missing: {session}")
        reports.append(seed_session(session, model_sha256=model_sha))
    summary = {
        "schema": "murmurmark.spne_v2_16_cache_seed_summary/v1",
        "corpus_set_sha256": sha256(args.corpus_set),
        "hard_set_sha256": sha256(args.hard_set),
        "model_sha256": model_sha,
        "sessions": len(reports),
        "validated_unique_entry_count": sum(
            row["validated_unique_entry_count"] for row in reports
        ),
        "materialized_entry_count": sum(
            row["materialized_entry_count"] for row in reports
        ),
        "rejected_entry_count": sum(row["rejected_entry_count"] for row in reports),
        "reports": [
            {
                "session_id": row["session_id"],
                "fingerprint": row["fingerprint"],
                "validated_unique_entry_count": row["validated_unique_entry_count"],
                "materialized_entry_count": row["materialized_entry_count"],
                "rejected_entry_count": row["rejected_entry_count"],
            }
            for row in reports
        ],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
