#!/usr/bin/env python3
"""Offline WavLM XVector embeddings for a frozen list of audio slices."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any

import numpy as np
from scipy.signal import resample_poly
import soundfile as sf


REQUEST_SCHEMA = "murmurmark.speaker_embedding_request/v1"
OUTPUT_SCHEMA = "murmurmark.speaker_embedding_result/v1"


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + "\n").encode()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("request must be a JSON object")
    return value


def audio_slice(path: Path, start: float, end: float, minimum_sec: float) -> np.ndarray:
    with sf.SoundFile(path) as source:
        sample_rate = int(source.samplerate)
        first = max(0, int(round(start * sample_rate)))
        last = min(len(source), int(round(end * sample_rate)))
        if last <= first:
            raise ValueError(f"empty audio slice: {path.name}:{start:.3f}-{end:.3f}")
        source.seek(first)
        values = source.read(last - first, dtype="float32", always_2d=True).mean(axis=1)
    if sample_rate != 16_000:
        divisor = math.gcd(sample_rate, 16_000)
        values = resample_poly(values, 16_000 // divisor, sample_rate // divisor)
    minimum = int(round(minimum_sec * 16_000))
    if values.size < minimum:
        missing = minimum - values.size
        values = np.pad(values, (missing // 2, missing - missing // 2))
    rms = float(np.sqrt(np.mean(np.square(values), dtype=np.float64)))
    if not np.isfinite(rms) or rms < 1e-7:
        raise ValueError(f"silent audio slice: {path.name}:{start:.3f}-{end:.3f}")
    return np.asarray(values, dtype=np.float32)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=8)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    request_path = args.request.expanduser().resolve()
    output_path = args.output.expanduser().resolve()
    model_path = args.model.expanduser().resolve()
    request = read_json(request_path)
    if request.get("schema") != REQUEST_SCHEMA:
        raise ValueError("unsupported embedding request schema")
    rows = request.get("requests") or []
    if not isinstance(rows, list) or not rows:
        raise ValueError("embedding request list is empty")
    keys = [str(row.get("key") or "") for row in rows]
    if not all(keys) or len(keys) != len(set(keys)):
        raise ValueError("embedding request keys must be non-empty and unique")

    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    import torch
    from transformers import AutoFeatureExtractor, AutoModelForAudioXVector

    torch.set_num_threads(max(1, int(args.threads)))
    torch.use_deterministic_algorithms(True, warn_only=True)
    processor = AutoFeatureExtractor.from_pretrained(str(model_path), local_files_only=True)
    model = AutoModelForAudioXVector.from_pretrained(str(model_path), local_files_only=True)
    model.eval()

    prepared: list[tuple[dict[str, Any], np.ndarray]] = []
    errors: list[dict[str, str]] = []
    allow_errors = bool(request.get("allow_errors", False))
    for row in sorted(rows, key=lambda item: str(item["key"])):
        try:
            values = audio_slice(
                Path(str(row["path"])).expanduser().resolve(),
                float(row["start"]),
                float(row["end"]),
                float(row["minimum_sec"]),
            )
            prepared.append((row, values))
        except (OSError, RuntimeError, ValueError) as error:
            if not allow_errors:
                raise
            errors.append({"key": str(row["key"]), "reason": f"{type(error).__name__}:{error}"})

    results: list[dict[str, Any]] = []
    prepared.sort(key=lambda item: (len(item[1]), str(item[0]["key"])))
    batch_size = max(1, int(args.batch_size))
    for offset in range(0, len(prepared), batch_size):
        batch = prepared[offset : offset + batch_size]
        inputs = processor(
            [values for _, values in batch],
            sampling_rate=16_000,
            return_tensors="pt",
            padding=True,
        )
        with torch.inference_mode():
            vectors = model(**inputs).embeddings.detach().cpu().numpy()
        for (row, _), vector in zip(batch, vectors):
            norm = float(np.linalg.norm(vector))
            if not np.isfinite(norm) or norm <= 0:
                if not allow_errors:
                    raise ValueError(f"invalid embedding: {row['key']}")
                errors.append({"key": str(row["key"]), "reason": "invalid_embedding"})
                continue
            normalized = np.asarray(vector / norm, dtype=np.float32)
            results.append(
                {
                    "key": str(row["key"]),
                    "embedding": [round(float(value), 9) for value in normalized],
                }
            )

    payload = {
        "schema": OUTPUT_SCHEMA,
        "request_sha256": sha256(request_path),
        "model_id": request["model_id"],
        "model_revision": request["model_revision"],
        "embedding_count": len(results),
        "embedding_dimensions": len(results[0]["embedding"]) if results else 0,
        "rows": sorted(results, key=lambda row: row["key"]),
        "errors": sorted(errors, key=lambda row: row["key"]),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(f".{output_path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(canonical_json(payload))
    os.replace(temporary, output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
