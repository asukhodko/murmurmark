#!/usr/bin/env python3
"""Extract deterministic WeSpeaker ResNet34-LM embeddings from frozen audio slices."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any

import numpy as np
import onnxruntime as ort
from scipy.signal import resample_poly
import soundfile as sf
import torch
import torchaudio.compliance.kaldi as kaldi


REQUEST_SCHEMA = "murmurmark.speaker_embedding_request/v1"
OUTPUT_SCHEMA = "murmurmark.speaker_embedding_result/v1"


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
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
    if sample_rate != 16000:
        divisor = math.gcd(sample_rate, 16000)
        values = resample_poly(values, 16000 // divisor, sample_rate // divisor).astype(np.float32)
    minimum = int(round(minimum_sec * 16000))
    if len(values) < minimum:
        missing = minimum - len(values)
        values = np.pad(values, (missing // 2, missing - missing // 2))
    rms = float(np.sqrt(np.mean(np.square(values), dtype=np.float64)))
    if rms < 1e-7:
        raise ValueError(f"silent audio slice: {path.name}:{start:.3f}-{end:.3f}")
    return np.asarray(values, dtype=np.float32)


def compute_fbank(values: np.ndarray) -> np.ndarray:
    # Matches WeSpeaker's pinned infer_onnx.py preprocessing: 16 kHz PCM scale,
    # 80-bin Kaldi fbank, Hamming window, no dither, and utterance CMN.
    waveform = torch.from_numpy(values).unsqueeze(0) * float(1 << 15)
    features = kaldi.fbank(
        waveform,
        num_mel_bins=80,
        frame_length=25,
        frame_shift=10,
        dither=0.0,
        sample_frequency=16000,
        window_type="hamming",
        use_energy=False,
    )
    features = features - torch.mean(features, dim=0)
    return np.asarray(features.numpy(), dtype=np.float32)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--threads", type=int, default=4)
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
    if not model_path.is_file():
        raise ValueError(f"WeSpeaker ONNX model is missing: {model_path}")

    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    torch.set_num_threads(max(1, int(args.threads)))
    options = ort.SessionOptions()
    options.inter_op_num_threads = 1
    options.intra_op_num_threads = max(1, int(args.threads))
    session = ort.InferenceSession(
        str(model_path),
        sess_options=options,
        providers=["CPUExecutionProvider"],
    )
    allow_errors = bool(request.get("allow_errors", False))
    results = []
    errors = []
    for row in sorted(rows, key=lambda item: str(item["key"])):
        try:
            values = audio_slice(
                Path(str(row["path"])).expanduser().resolve(),
                float(row["start"]),
                float(row["end"]),
                float(row["minimum_sec"]),
            )
            features = compute_fbank(values)[None, :, :]
            vector = np.asarray(
                session.run(output_names=["embs"], input_feed={"feats": features})[0],
                dtype=np.float32,
            ).reshape(-1)
            norm = float(np.linalg.norm(vector))
            if not np.isfinite(norm) or norm <= 0:
                raise ValueError(f"invalid embedding: {row['key']}")
            vector = vector / norm
            results.append(
                {
                    "key": str(row["key"]),
                    "embedding": [round(float(value), 9) for value in vector],
                }
            )
        except (OSError, RuntimeError, ValueError) as error:
            if not allow_errors:
                raise
            errors.append(
                {
                    "key": str(row["key"]),
                    "reason": f"{type(error).__name__}:{str(error)[:240]}",
                }
            )
    payload = {
        "schema": OUTPUT_SCHEMA,
        "request_sha256": sha256(request_path),
        "model_id": request["model_id"],
        "model_revision": request["model_revision"],
        "model_sha256": sha256(model_path),
        "preprocessing": "wespeaker_kaldi_fbank_80_hamming_cmn_v1",
        "embedding_count": len(results),
        "embedding_dimensions": len(results[0]["embedding"]) if results else 0,
        "rows": results,
        "errors": errors,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(f".{output_path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(canonical_json(payload))
    os.replace(temporary, output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
