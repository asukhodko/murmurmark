#!/usr/bin/env python3
"""Install, build, or verify the pinned local temporal diarization backend."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import urllib.request
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = ROOT / "policies/temporal-end-to-end-remote-diarization-qualification-v1.json"
DEFAULT_MODEL_DIR = (
    Path.home() / ".local/share/murmurmark/models/temporal-remote-diarization-v1/dia-community-1"
)
WORKER_ROOT = ROOT / "tools/temporal-diarization-worker"
WORKER = WORKER_ROOT / "target/release/murmurmark-temporal-diarization-worker"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def load_policy(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema") != "murmurmark.temporal_remote_diarization_policy/v1":
        raise ValueError("unsupported temporal diarization policy")
    return value


def download(policy: dict[str, Any], model_dir: Path) -> None:
    candidate = policy["candidate"]
    revision = candidate["model_revision"]
    repository = candidate["model_repository"]
    files = ["wespeaker_resnet34_lm.onnx", *sorted(candidate["provenance_files"])]
    model_dir.mkdir(parents=True, exist_ok=True)
    for filename in files:
        url = f"https://huggingface.co/{repository}/resolve/{revision}/{filename}"
        temporary = model_dir / f".{filename}.{os.getpid()}.tmp"
        with urllib.request.urlopen(url, timeout=120) as response, temporary.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
        os.replace(temporary, model_dir / filename)


def build_worker() -> None:
    completed = subprocess.run(
        ["cargo", "build", "--release", "--locked"], cwd=WORKER_ROOT, check=False
    )
    if completed.returncode != 0:
        raise ValueError("temporal diarization worker build failed")


def verify(policy: dict[str, Any], model_dir: Path) -> dict[str, Any]:
    candidate = policy["candidate"]
    runtime = policy["runtime"]
    expected = {
        "wespeaker_resnet34_lm.onnx": candidate["model_sha256"],
        **candidate["provenance_files"],
    }
    artifacts = []
    for filename, digest in expected.items():
        path = model_dir / filename
        if not path.is_file() or sha256(path) != digest:
            raise ValueError(f"model artifact missing or changed: {filename}")
        artifacts.append({"name": filename, "bytes": path.stat().st_size, "sha256": digest})
    if not WORKER.is_file() or sha256(WORKER) != runtime["worker_binary_sha256"]:
        raise ValueError("worker binary missing or changed; run the build action")
    return {
        "schema": "murmurmark.temporal_remote_diarization_model_manifest/v1",
        "candidate": candidate["id"],
        "crate": f"{candidate['crate']}@{candidate['crate_version']}",
        "crate_revision": candidate["crate_revision"],
        "offline_ready": True,
        "artifacts": sorted(artifacts, key=lambda row: row["name"]),
        "worker": {
            "path": str(WORKER.relative_to(ROOT)),
            "bytes": WORKER.stat().st_size,
            "sha256": sha256(WORKER),
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("verify", "download", "build", "all"), nargs="?", default="verify")
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    policy = load_policy(args.policy.expanduser().resolve())
    model_dir = args.model_dir.expanduser().resolve()
    if args.action in {"download", "all"}:
        download(policy, model_dir)
    if args.action in {"build", "all"}:
        build_worker()
    manifest = verify(policy, model_dir)
    manifest_path = model_dir / "model_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    print(f"candidate: {manifest['candidate']}")
    print("offline_ready: true")
    print(f"manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
