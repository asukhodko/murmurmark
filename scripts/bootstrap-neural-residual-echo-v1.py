#!/usr/bin/env python3
"""Download and verify the pinned local models for Neural Residual Echo Suppression v1."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Any


SOURCE_COMMIT = "6c633d0a9d2a143a0e364899b91b06f127315b18"
DEFAULT_MODEL_DIR = (
    Path.home() / ".local/share/murmurmark/models/neural-residual-echo-v1"
)
MODELS: tuple[dict[str, Any], ...] = (
    {
        "id": "microsoft_icassp2022_dec",
        "filename": "dec-baseline-model-icassp2022.onnx",
        "sha256": "4436ee4f80e5f1d0299196bd7057137a3cad7cac324409dce7540f2a113bb931",
        "bytes": 5_201_196,
        "url": (
            "https://raw.githubusercontent.com/microsoft/AEC-Challenge/"
            f"{SOURCE_COMMIT}/baseline/icassp2022/"
            "dec-baseline-model-icassp2022.onnx"
        ),
        "expected_inputs": ["input", "h01", "h02"],
        "expected_outputs": ["output", "hn1", "hn2"],
        "purpose": "remote_conditioned_spectral_mask",
    },
    {
        "id": "microsoft_aecmos_16k_no_scenarios",
        "filename": "aecmos-16k-no-scenarios.onnx",
        "sha256": "b517d8d9ca2f91ea55d15f605a15917c19be5d832868fe115c7c5bc48986dae1",
        "bytes": 1_170_947,
        "url": (
            "https://raw.githubusercontent.com/microsoft/AEC-Challenge/"
            f"{SOURCE_COMMIT}/AECMOS/AECMOS_local/"
            "Run_1663829550_Stage_0.onnx"
        ),
        "expected_inputs": ["input", "h0"],
        "expected_outputs": ["output"],
        "purpose": "secondary_non_scenario_echo_and_degradation_metric",
    },
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare pinned ONNX models for Neural Residual Echo Suppression v1."
    )
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument(
        "--download",
        action="store_true",
        help="Download missing or invalid files before checking them.",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        help="Optional path for the machine-readable verification manifest.",
    )
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def download_atomic(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".download",
        dir=destination.parent,
    )
    os.close(file_descriptor)
    temporary = Path(temporary_name)
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            with temporary.open("wb") as output:
                shutil.copyfileobj(response, output)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def inspect_onnx(path: Path) -> dict[str, Any]:
    try:
        import onnxruntime as ort
    except ImportError as error:
        return {
            "available": False,
            "reason": f"onnxruntime_import_failed:{error}",
        }
    try:
        options = ort.SessionOptions()
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        session = ort.InferenceSession(
            str(path),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
    except Exception as error:  # onnxruntime raises several runtime-specific types
        return {
            "available": False,
            "reason": f"onnx_load_failed:{type(error).__name__}:{error}",
        }
    return {
        "available": True,
        "providers": session.get_providers(),
        "inputs": [value.name for value in session.get_inputs()],
        "outputs": [value.name for value in session.get_outputs()],
    }


def check_model(model: dict[str, Any], model_dir: Path, download: bool) -> dict[str, Any]:
    path = model_dir / str(model["filename"])
    observed_sha = sha256(path) if path.exists() else None
    if download and observed_sha != model["sha256"]:
        download_atomic(str(model["url"]), path)
        observed_sha = sha256(path)
    runtime = inspect_onnx(path) if path.exists() and observed_sha == model["sha256"] else {}
    inputs = runtime.get("inputs") if isinstance(runtime.get("inputs"), list) else []
    outputs = runtime.get("outputs") if isinstance(runtime.get("outputs"), list) else []
    checks = {
        "exists": path.exists(),
        "bytes_match": path.exists() and path.stat().st_size == int(model["bytes"]),
        "sha256_match": observed_sha == model["sha256"],
        "onnx_loadable": runtime.get("available") is True,
        "cpu_provider": "CPUExecutionProvider" in (runtime.get("providers") or []),
        "inputs_match": inputs == model["expected_inputs"],
        "outputs_match": outputs == model["expected_outputs"],
    }
    return {
        "id": model["id"],
        "purpose": model["purpose"],
        "path": str(path),
        "source": {
            "repository": "https://github.com/microsoft/AEC-Challenge",
            "commit": SOURCE_COMMIT,
            "url": model["url"],
            "license": "MIT",
        },
        "expected": {
            "bytes": model["bytes"],
            "sha256": model["sha256"],
            "inputs": model["expected_inputs"],
            "outputs": model["expected_outputs"],
        },
        "observed": {
            "bytes": path.stat().st_size if path.exists() else None,
            "sha256": observed_sha,
            "runtime": runtime,
        },
        "checks": checks,
        "passed": all(checks.values()),
    }


def main() -> int:
    args = parse_args()
    model_dir = args.model_dir.expanduser().resolve()
    rows = [check_model(model, model_dir, bool(args.download)) for model in MODELS]
    payload = {
        "schema": "murmurmark.neural_residual_echo_model_manifest/v1",
        "profile": "neural_residual_echo_v1",
        "model_dir": str(model_dir),
        "runtime": {
            "python": sys.version.split()[0],
            "provider": "CPUExecutionProvider",
            "network_required_for_inference": False,
        },
        "models": rows,
        "passed": all(row["passed"] for row in rows),
    }
    if args.manifest:
        manifest = args.manifest.expanduser().resolve()
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if payload["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
