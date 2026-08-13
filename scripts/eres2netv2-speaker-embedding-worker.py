#!/usr/bin/env python3
"""Extract deterministic local ERes2NetV2 speaker embeddings."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sys
from typing import Any

import numpy as np
from scipy.signal import resample_poly
import soundfile as sf
import torch
import torchaudio.compliance.kaldi as kaldi


REQUEST_SCHEMA = "murmurmark.eres2netv2_embedding_request/v1"
OUTPUT_SCHEMA = "murmurmark.eres2netv2_embedding_result/v1"


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def pretty_json(value: Any) -> bytes:
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
        raise ValueError("embedding request must be a JSON object")
    return value


def audio_slice(
    path: Path,
    start: float,
    end: float | None,
    minimum_sec: float,
) -> tuple[np.ndarray, dict[str, Any]]:
    with sf.SoundFile(path) as source:
        sample_rate = int(source.samplerate)
        first = max(0, int(round(start * sample_rate)))
        last = len(source) if end is None else min(len(source), int(round(end * sample_rate)))
        if last <= first:
            raise ValueError(f"empty audio slice: {path.name}:{start:.3f}-{end}")
        source.seek(first)
        values = source.read(last - first, dtype="float32", always_2d=True).mean(axis=1)
    source_samples = len(values)
    if sample_rate != 16000:
        divisor = math.gcd(sample_rate, 16000)
        values = resample_poly(values, 16000 // divisor, sample_rate // divisor).astype(np.float32)
    minimum_samples = int(round(minimum_sec * 16000))
    if len(values) < minimum_samples:
        missing = minimum_samples - len(values)
        values = np.pad(values, (missing // 2, missing - missing // 2))
    rms = float(np.sqrt(np.mean(np.square(values), dtype=np.float64)))
    if not np.isfinite(rms) or rms < 1e-7:
        raise ValueError(f"silent audio slice: {path.name}:{start:.3f}-{end}")
    return np.asarray(values, dtype=np.float32), {
        "source_sample_rate": sample_rate,
        "source_samples": source_samples,
        "effective_samples_16k": len(values),
        "source_duration_sec": round(source_samples / sample_rate, 6),
        "rms_dbfs": round(20.0 * math.log10(max(rms, 1e-12)), 6),
    }


def compute_fbank(values: np.ndarray) -> torch.Tensor:
    # This is the pinned 3D-Speaker infer_sv.py preprocessing: 16 kHz mono,
    # 80-bin Kaldi fbank, zero dither and utterance mean normalization.
    waveform = torch.from_numpy(values).unsqueeze(0)
    features = kaldi.fbank(
        waveform,
        num_mel_bins=80,
        sample_frequency=16000,
        dither=0.0,
    )
    return features - features.mean(dim=0, keepdim=True)


def load_model(code_root: Path, model_path: Path) -> torch.nn.Module:
    if not code_root.joinpath("speakerlab/models/eres2net/ERes2NetV2.py").is_file():
        raise ValueError(f"3D-Speaker source is missing: {code_root}")
    sys.path.insert(0, str(code_root))
    try:
        from speakerlab.models.eres2net.ERes2NetV2 import ERes2NetV2
    finally:
        sys.path.pop(0)
    model = ERes2NetV2(
        feat_dim=80,
        embedding_size=192,
        baseWidth=26,
        scale=2,
        expansion=2,
    )
    checkpoint = torch.load(model_path, map_location="cpu", weights_only=True)
    if isinstance(checkpoint, dict) and "state_dict" in checkpoint:
        checkpoint = checkpoint["state_dict"]
    model.load_state_dict(checkpoint)
    model.eval()
    return model


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--code-root", type=Path, required=True)
    parser.add_argument("--threads", type=int, default=4)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    request_path = args.request.expanduser().resolve()
    output_path = args.output.expanduser().resolve()
    model_path = args.model.expanduser().resolve()
    code_root = args.code_root.expanduser().resolve()
    request = read_json(request_path)
    if request.get("schema") != REQUEST_SCHEMA:
        raise ValueError("unsupported ERes2NetV2 request schema")
    rows = request.get("requests") or []
    if not isinstance(rows, list) or not rows:
        raise ValueError("embedding request list is empty")
    keys = [str(row.get("key") or "") for row in rows]
    if not all(keys) or len(keys) != len(set(keys)):
        raise ValueError("embedding request keys must be non-empty and unique")
    if not model_path.is_file():
        raise ValueError(f"ERes2NetV2 checkpoint is missing: {model_path}")

    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["PYTHONHASHSEED"] = "0"
    np.random.seed(0)
    torch.manual_seed(0)
    torch.set_num_threads(max(1, int(args.threads)))
    try:
        torch.set_num_interop_threads(1)
    except RuntimeError:
        pass
    model = load_model(code_root, model_path)
    allow_errors = bool(request.get("allow_errors", False))
    results: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for row in sorted(rows, key=lambda item: str(item["key"])):
        try:
            path = Path(str(row["path"])).expanduser().resolve()
            values, audio = audio_slice(
                path,
                float(row.get("start") or 0.0),
                float(row["end"]) if row.get("end") is not None else None,
                float(row.get("minimum_sec") or 0.65),
            )
            features = compute_fbank(values).unsqueeze(0)
            with torch.inference_mode():
                vector = np.asarray(model(features).squeeze(0).cpu().numpy(), dtype=np.float32)
            norm = float(np.linalg.norm(vector))
            if not np.isfinite(norm) or norm <= 0:
                raise ValueError(f"invalid embedding: {row['key']}")
            vector = vector / norm
            results.append(
                {
                    "key": str(row["key"]),
                    "embedding": [round(float(value), 9) for value in vector],
                    "audio": audio,
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
        "source_revision": request["source_revision"],
        "preprocessing": "3dspeaker_kaldi_fbank80_mean_norm_v1",
        "embedding_count": len(results),
        "embedding_dimensions": len(results[0]["embedding"]) if results else 0,
        "rows": results,
        "errors": errors,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(f".{output_path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(pretty_json(payload))
    os.replace(temporary, output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
