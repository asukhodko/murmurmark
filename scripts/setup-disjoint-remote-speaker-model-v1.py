#!/usr/bin/env python3
"""Download or verify the pinned ERes2NetV2 qualification backend."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = ROOT / "policies/disjoint-remote-speaker-model-qualification-v1.json"
DEFAULT_MODEL_ROOT = (
    Path.home()
    / ".local/share/murmurmark/models/disjoint-remote-speaker-model-qualification-v1/eres2netv2-common"
)


class SetupError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def pretty_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def load_policy(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema") != "murmurmark.disjoint_remote_speaker_model_qualification_policy/v1":
        raise SetupError("unsupported disjoint model policy")
    return value


def run(arguments: list[str]) -> str:
    result = subprocess.run(arguments, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "command failed").strip()
        raise SetupError(f"{arguments[0]} failed: {message[:500]}")
    return result.stdout.strip()


def clone(policy: dict[str, Any], model_root: Path) -> None:
    candidate = policy["candidate"]
    model_repo = model_root / "model-repo"
    code_repo = model_root / "code-repo"
    model_root.mkdir(parents=True, exist_ok=True)
    if not model_repo.joinpath(".git").is_dir():
        if model_repo.exists():
            raise SetupError(f"refusing to replace non-git model directory: {model_repo}")
        run(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "--branch",
                candidate["model_tag"],
                candidate["model_repository"],
                str(model_repo),
            ]
        )
    if not code_repo.joinpath(".git").is_dir():
        if code_repo.exists():
            raise SetupError(f"refusing to replace non-git source directory: {code_repo}")
        run(["git", "clone", "--filter=blob:none", "--no-checkout", candidate["source_repository"], str(code_repo)])
    run(["git", "-C", str(code_repo), "checkout", "--detach", candidate["source_revision"]])


def package_versions() -> dict[str, str]:
    versions = {}
    for package in ("torch", "torchaudio", "numpy", "scipy", "soundfile"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "missing"
    return versions


def verify(policy: dict[str, Any], model_root: Path) -> dict[str, Any]:
    candidate = policy["candidate"]
    model_repo = model_root / "model-repo"
    code_repo = model_root / "code-repo"
    if not model_repo.joinpath(".git").is_dir() or not code_repo.joinpath(".git").is_dir():
        raise SetupError(f"candidate backend is not installed: {model_root}")
    model_commit = run(["git", "-C", str(model_repo), "rev-parse", "HEAD"])
    source_commit = run(["git", "-C", str(code_repo), "rev-parse", "HEAD"])
    if model_commit != candidate["model_revision"]:
        raise SetupError(f"model revision changed: {model_commit}")
    if source_commit != candidate["source_revision"]:
        raise SetupError(f"source revision changed: {source_commit}")
    artifacts = []
    for expected in candidate["artifacts"]:
        path = model_root / expected["path"]
        if not path.is_file():
            raise SetupError(f"candidate artifact is missing: {expected['path']}")
        actual = sha256(path)
        if actual != expected["sha256"] or path.stat().st_size != int(expected["bytes"]):
            raise SetupError(f"candidate artifact changed: {expected['path']}")
        artifacts.append({"path": expected["path"], "bytes": path.stat().st_size, "sha256": actual})
    versions = package_versions()
    for package, expected in policy["runtime"]["packages"].items():
        if versions.get(package) != str(expected):
            raise SetupError(f"runtime changed: {package}={versions.get(package)}, expected {expected}")
    return {
        "schema": "murmurmark.disjoint_remote_speaker_model_manifest/v1",
        "candidate_id": candidate["id"],
        "model_id": candidate["model_id"],
        "model_revision": model_commit,
        "source_revision": source_commit,
        "license": candidate["license"],
        "offline_ready": True,
        "artifacts": artifacts,
        "runtime": versions,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("verify", "download"), nargs="?", default="verify")
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--model-root", type=Path, default=DEFAULT_MODEL_ROOT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    policy = load_policy(args.policy.expanduser().resolve())
    model_root = args.model_root.expanduser().resolve()
    try:
        if args.action == "download":
            clone(policy, model_root)
        manifest = verify(policy, model_root)
    except SetupError as error:
        print(f"error: {error}")
        return 1
    manifest_path = model_root / "model_manifest.json"
    temporary = manifest_path.with_name(f".{manifest_path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(pretty_json(manifest))
    os.replace(temporary, manifest_path)
    print(f"candidate: {manifest['candidate_id']}")
    print(f"offline_ready: {str(manifest['offline_ready']).lower()}")
    print(f"manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
