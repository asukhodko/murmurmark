#!/usr/bin/env python3
"""Install or verify the isolated ECAPA runtime used by identity qualification v1."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = ROOT / "policies/stronger-remote-speaker-identity-backend-qualification-v1.json"
MODEL_ENV = "MURMURMARK_REMOTE_SPEAKER_ECAPA_MODEL"
RUNTIME_ENV = "MURMURMARK_REMOTE_SPEAKER_IDENTITY_RUNTIME"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("policy must be a JSON object")
    return value


def candidate(policy: dict[str, Any]) -> dict[str, Any]:
    rows = [row for row in policy.get("shortlist", []) if row.get("role") == "candidate"]
    if len(rows) != 1:
        raise ValueError("policy must contain exactly one candidate backend")
    return rows[0]


def locations(row: dict[str, Any]) -> tuple[Path, Path]:
    model = Path(os.environ.get(MODEL_ENV, row["default_path"])).expanduser().resolve()
    runtime = Path(os.environ.get(RUNTIME_ENV, row["default_runtime"])).expanduser().resolve()
    return model, runtime


def package_versions(python: Path) -> dict[str, str]:
    packages = ["torch", "torchaudio", "speechbrain", "numpy", "scipy", "soundfile"]
    code = (
        "import importlib.metadata as m,json;"
        f"print(json.dumps({{p:m.version(p) for p in {packages!r}}},sort_keys=True))"
    )
    result = subprocess.run(
        [str(python), "-c", code], capture_output=True, text=True, check=False
    )
    if result.returncode:
        return {}
    value = json.loads(result.stdout)
    return {str(key): str(item) for key, item in value.items()}


def inspect(policy: dict[str, Any]) -> dict[str, Any]:
    row = candidate(policy)
    model, runtime = locations(row)
    python = runtime / "bin/python"
    file_rows = []
    for name, expected in sorted(row["files"].items()):
        path = model / name
        actual = sha256(path) if path.is_file() else None
        file_rows.append(
            {
                "name": name,
                "present": path.is_file(),
                "expected_sha256": expected,
                "actual_sha256": actual,
                "valid": actual == expected,
            }
        )
    versions = package_versions(python) if python.is_file() else {}
    runtime_checks = {
        name: versions.get(name) == expected
        for name, expected in row["runtime"].items()
    }
    ready = bool(file_rows) and all(item["valid"] for item in file_rows) and bool(
        runtime_checks
    ) and all(runtime_checks.values())
    return {
        "schema": "murmurmark.remote_speaker_identity_backend_setup/v1",
        "backend_id": row["id"],
        "model_revision": row["revision"],
        "model_path": str(model),
        "runtime_path": str(runtime),
        "model_files": file_rows,
        "runtime_versions": versions,
        "runtime_checks": runtime_checks,
        "offline_ready": ready,
    }


def install(policy: dict[str, Any]) -> None:
    row = candidate(policy)
    model, runtime = locations(row)
    runtime.parent.mkdir(parents=True, exist_ok=True)
    if not (runtime / "bin/python").is_file():
        subprocess.run([sys.executable, "-m", "venv", str(runtime)], check=True)
    python = runtime / "bin/python"
    requirements = [
        f"torch=={row['runtime']['torch']}",
        f"torchaudio=={row['runtime']['torchaudio']}",
        f"speechbrain=={row['runtime']['speechbrain']}",
        f"numpy=={row['runtime']['numpy']}",
        f"scipy=={row['runtime']['scipy']}",
        f"soundfile=={row['runtime']['soundfile']}",
    ]
    subprocess.run(
        ["nice", "-n", "20", str(python), "-m", "pip", "install", *requirements],
        check=True,
    )
    try:
        from huggingface_hub import snapshot_download
    except ImportError as error:
        raise RuntimeError("huggingface_hub is required in the MurmurMark environment") from error
    model.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=row["model_id"],
        revision=row["revision"],
        local_dir=model,
        allow_patterns=sorted(row["files"]),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("status", "install"), nargs="?", default="status")
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    policy = read_json(args.policy.expanduser().resolve())
    if args.action == "install":
        install(policy)
    report = inspect(policy)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if report["offline_ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
