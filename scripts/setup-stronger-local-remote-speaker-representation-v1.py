#!/usr/bin/env python3
"""Download or verify the pinned offline WeSpeaker representation candidate."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = ROOT / "policies/stronger-local-remote-speaker-representation-qualification-v1.json"
DEFAULT_MODEL_DIR = (
    Path.home()
    / ".local/share/murmurmark/models/remote-speaker-representation-v1/wespeaker-resnet34-lm"
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def load_policy(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema") != "murmurmark.stronger_local_remote_speaker_representation_policy/v1":
        raise ValueError("unsupported representation policy")
    return value


def runtime_versions() -> dict[str, str]:
    result = {}
    for package in ("onnxruntime", "torch", "torchaudio", "huggingface-hub"):
        try:
            result[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            result[package] = "missing"
    return result


def download(policy: dict[str, Any], model_dir: Path) -> None:
    from huggingface_hub import hf_hub_download

    candidate = policy["candidate"]
    model_dir.mkdir(parents=True, exist_ok=True)
    for filename in (candidate["model_file"], candidate["license_file"], "README.md"):
        cached = Path(
            hf_hub_download(
                repo_id=candidate["model_id"],
                filename=filename,
                revision=candidate["model_revision"],
            )
        )
        temporary = model_dir / f".{filename}.{os.getpid()}.tmp"
        shutil.copyfile(cached, temporary)
        os.replace(temporary, model_dir / filename)


def verify(policy: dict[str, Any], model_dir: Path) -> dict[str, Any]:
    candidate = policy["candidate"]
    expected = {
        candidate["model_file"]: candidate["model_sha256"],
        candidate["license_file"]: candidate["license_sha256"],
        "README.md": candidate["readme_sha256"],
    }
    artifacts = []
    for filename, digest in expected.items():
        path = model_dir / filename
        if not path.is_file():
            raise ValueError(f"model artifact is missing: {path}")
        actual = sha256(path)
        if actual != digest:
            raise ValueError(f"model artifact hash mismatch: {filename}")
        artifacts.append({"name": filename, "bytes": path.stat().st_size, "sha256": actual})
    versions = runtime_versions()
    for package, expected_version in policy["runtime"].items():
        if package not in versions:
            continue
        if versions[package] != str(expected_version):
            raise ValueError(
                f"runtime version mismatch: {package}={versions[package]}, expected {expected_version}"
            )
    return {
        "schema": "murmurmark.stronger_local_remote_speaker_representation_model_manifest/v1",
        "model_id": candidate["model_id"],
        "model_revision": candidate["model_revision"],
        "license": candidate["license"],
        "offline_ready": True,
        "artifacts": artifacts,
        "runtime": versions,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("verify", "download"), nargs="?", default="verify")
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    policy = load_policy(args.policy.expanduser().resolve())
    model_dir = args.model_dir.expanduser().resolve()
    if args.action == "download":
        download(policy, model_dir)
    manifest = verify(policy, model_dir)
    manifest_path = model_dir / "model_manifest.json"
    manifest_path.write_bytes(canonical_json(manifest))
    print(f"model: {manifest['model_id']}@{manifest['model_revision']}")
    print(f"offline_ready: {str(manifest['offline_ready']).lower()}")
    print(f"manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
