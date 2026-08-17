#!/usr/bin/env python3
"""Build and verify deterministic MurmurMark release payloads."""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import inspect
import json
import os
import re
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


MANIFEST_SCHEMA = "murmurmark.release_bundle/v2"
COMPATIBILITY_SCHEMA = "murmurmark.release_compatibility/v1"
LICENSE_SCHEMA = "murmurmark.release_license_inventory/v1"
PROHIBITED_PARTS = {
    ".build",
    ".git",
    ".venv",
    "models",
    "recordings",
    "sessions",
    "weights",
}
PROHIBITED_NAMES = {"murmurmark.config.json"}
PROHIBITED_SUFFIXES = {".caf", ".flac", ".m4a", ".mp3", ".mp4", ".wav"}
TEXT_SUFFIXES = {
    "",
    ".c",
    ".json",
    ".md",
    ".py",
    ".rs",
    ".sh",
    ".swift",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}


class ReleaseError(RuntimeError):
    pass


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReleaseError(f"cannot read JSON {path}: {error}") from error
    if not isinstance(value, dict):
        raise ReleaseError(f"expected a JSON object: {path}")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def safe_relative(path: Path, root: Path) -> str:
    try:
        relative = path.relative_to(root)
    except ValueError as error:
        raise ReleaseError(f"path escapes release root: {path}") from error
    return relative.as_posix()


def payload_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        relative = safe_relative(path, root)
        if path.is_symlink():
            raise ReleaseError(f"release payload must not contain symlinks: {relative}")
        if path.is_file() and relative != "release-manifest.json":
            files.append(path)
    return sorted(files, key=lambda item: safe_relative(item, root))


def validate_payload_path(relative: str) -> None:
    parts = PurePosixPath(relative).parts
    if any(part in PROHIBITED_PARTS for part in parts):
        raise ReleaseError(f"private or generated path is prohibited: {relative}")
    if PurePosixPath(relative).name in PROHIBITED_NAMES:
        raise ReleaseError(f"local config is prohibited: {relative}")
    if PurePosixPath(relative).suffix.lower() in PROHIBITED_SUFFIXES:
        raise ReleaseError(f"audio or media payload is prohibited: {relative}")


def file_entry(path: Path, root: Path) -> dict[str, Any]:
    relative = safe_relative(path, root)
    validate_payload_path(relative)
    executable = bool(path.stat().st_mode & 0o111)
    return {
        "path": relative,
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
        "mode": "0755" if executable else "0644",
    }


def required_layout(root: Path) -> None:
    required = [
        "bin/murmurmark",
        "libexec/murmurmark/murmurmark",
        "scripts/release-bundle.py",
        "scripts/install-release.sh",
        "scripts/materialize-anonymous-rich-transcript.py",
        "scripts/review-remote-speaker-labels.py",
        "scripts/configure-remote-speaker-roster.py",
        "scripts/review_profile_lineage.py",
        "scripts/report-remote-speaker-cluster-purity-reference-v1.py",
        "scripts/apply-transcript-integrity.py",
        "scripts/report-transcript-integrity-corpus.py",
        "policies/remote-speaker-cluster-purity-reference-v1.json",
        "policies/transcript-integrity-v1.json",
        "release/compatibility-v1.json",
        "release/licenses-v1.json",
        "README.md",
        "CHANGELOG.md",
        "LICENSE",
    ]
    missing = [relative for relative in required if not (root / relative).is_file()]
    if missing:
        raise ReleaseError(f"release payload is incomplete: missing {', '.join(missing)}")


def finalize(args: argparse.Namespace) -> None:
    root = args.root.resolve()
    if not root.is_dir():
        raise ReleaseError(f"release root does not exist: {root}")
    required_layout(root)

    compatibility_path = root / "release/compatibility-v1.json"
    licenses_path = root / "release/licenses-v1.json"
    compatibility = read_json(compatibility_path)
    licenses = read_json(licenses_path)
    if compatibility.get("schema") != COMPATIBILITY_SCHEMA:
        raise ReleaseError("unsupported release compatibility schema")
    if licenses.get("schema") != LICENSE_SCHEMA:
        raise ReleaseError("unsupported release license inventory schema")
    if compatibility.get("release_version") != args.version:
        raise ReleaseError(
            "release version differs from release/compatibility-v1.json: "
            f"{args.version} != {compatibility.get('release_version')}"
        )

    entries = [file_entry(path, root) for path in payload_files(root)]
    source_epoch = int(args.source_date_epoch)
    created_at = dt.datetime.fromtimestamp(source_epoch, tz=dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    fingerprint_input = {
        "version": args.version,
        "git_commit": args.git_commit,
        "dirty": args.dirty,
        "source_date_epoch": source_epoch,
        "target_architecture": args.architecture,
        "files": entries,
    }
    package_fingerprint = canonical_sha256(fingerprint_input)
    release_id = f"{args.version}-{args.git_commit}-{package_fingerprint[:12]}"
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "name": "murmurmark",
        "version": args.version,
        "release_id": release_id,
        "git_commit": args.git_commit,
        "dirty": args.dirty,
        "created_at": created_at,
        "source_date_epoch": source_epoch,
        "target_architecture": args.architecture,
        "package_fingerprint": package_fingerprint,
        "layout": {
            "wrapper": "bin/murmurmark",
            "executable": "libexec/murmurmark/murmurmark",
            "runtime_home": ".",
            "workspace": "external current directory",
        },
        "contracts": {
            "compatibility": {
                "path": "release/compatibility-v1.json",
                "schema": COMPATIBILITY_SCHEMA,
                "sha256": sha256_file(compatibility_path),
            },
            "licenses": {
                "path": "release/licenses-v1.json",
                "schema": LICENSE_SCHEMA,
                "sha256": sha256_file(licenses_path),
            },
        },
        "privacy": {
            "contains_sessions": False,
            "contains_raw_audio": False,
            "contains_models": False,
            "contains_local_config": False,
        },
        "files": entries,
    }
    write_json(root / "release-manifest.json", manifest)
    verify_root(root)
    print(f"manifest: {root / 'release-manifest.json'}")
    print(f"release_id: {release_id}")
    print(f"package_fingerprint: {package_fingerprint}")


def scan_private_paths(path: Path, relative: str) -> None:
    if relative in {
        "scripts/check-open-source-readiness.sh",
        "scripts/release-bundle.py",
    }:
        return
    if path.suffix.lower() not in TEXT_SUFFIXES:
        return
    try:
        data = path.read_bytes()
    except OSError as error:
        raise ReleaseError(f"cannot read payload file {relative}: {error}") from error
    if relative == "libexec/murmurmark/murmurmark":
        if b"/Users/" in data:
            raise ReleaseError(f"private build path found in {relative}")
        return
    if b"\x00" in data[:4096]:
        return
    if re.search(rb"/Users/[A-Za-z0-9._-]+/", data):
        raise ReleaseError(f"private absolute path found in {relative}")


def verify_root(root: Path) -> dict[str, Any]:
    root = root.resolve()
    manifest_path = root / "release-manifest.json"
    manifest = read_json(manifest_path)
    if manifest.get("schema") != MANIFEST_SCHEMA:
        raise ReleaseError(f"unsupported release manifest schema: {manifest.get('schema')}")
    expected_layout = {
        "wrapper": "bin/murmurmark",
        "executable": "libexec/murmurmark/murmurmark",
        "runtime_home": ".",
        "workspace": "external current directory",
    }
    if manifest.get("layout") != expected_layout:
        raise ReleaseError("release layout contract mismatch")
    expected_privacy = {
        "contains_sessions": False,
        "contains_raw_audio": False,
        "contains_models": False,
        "contains_local_config": False,
    }
    if manifest.get("privacy") != expected_privacy:
        raise ReleaseError("release privacy contract mismatch")
    required_layout(root)

    rows = manifest.get("files")
    if not isinstance(rows, list) or not rows:
        raise ReleaseError("release manifest has no file inventory")
    expected: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("path"), str):
            raise ReleaseError("invalid release file inventory row")
        relative = row["path"]
        validate_payload_path(relative)
        if relative in expected:
            raise ReleaseError(f"duplicate release file inventory row: {relative}")
        expected[relative] = row

    actual_paths = {
        safe_relative(path, root)
        for path in payload_files(root)
    }
    expected_paths = set(expected)
    if actual_paths != expected_paths:
        missing = sorted(expected_paths - actual_paths)
        extra = sorted(actual_paths - expected_paths)
        raise ReleaseError(f"release file set mismatch: missing={missing} extra={extra}")

    for relative in sorted(expected):
        row = expected[relative]
        path = root / relative
        actual_size = path.stat().st_size
        actual_sha = sha256_file(path)
        actual_mode = "0755" if path.stat().st_mode & 0o111 else "0644"
        if row.get("size_bytes") != actual_size:
            raise ReleaseError(f"release size mismatch: {relative}")
        if row.get("sha256") != actual_sha:
            raise ReleaseError(f"release checksum mismatch: {relative}")
        if row.get("mode") != actual_mode:
            raise ReleaseError(f"release mode mismatch: {relative}")
        scan_private_paths(path, relative)

    contracts = manifest.get("contracts")
    if not isinstance(contracts, dict):
        raise ReleaseError("release contracts are missing")
    for name in ("compatibility", "licenses"):
        contract = contracts.get(name)
        if not isinstance(contract, dict):
            raise ReleaseError(f"release contract is missing: {name}")
        relative = contract.get("path")
        if not isinstance(relative, str):
            raise ReleaseError(f"release contract path is missing: {name}")
        if sha256_file(root / relative) != contract.get("sha256"):
            raise ReleaseError(f"release contract checksum mismatch: {name}")

    compatibility = read_json(root / "release/compatibility-v1.json")
    licenses = read_json(root / "release/licenses-v1.json")
    if contracts["compatibility"].get("schema") != compatibility.get("schema"):
        raise ReleaseError("release compatibility manifest schema mismatch")
    if contracts["licenses"].get("schema") != licenses.get("schema"):
        raise ReleaseError("release license manifest schema mismatch")
    if compatibility.get("schema") != COMPATIBILITY_SCHEMA:
        raise ReleaseError("unsupported release compatibility schema")
    if licenses.get("schema") != LICENSE_SCHEMA:
        raise ReleaseError("unsupported release license inventory schema")
    if compatibility.get("release_version") != manifest.get("version"):
        raise ReleaseError("manifest and compatibility release versions differ")
    fingerprint_input = {
        "version": manifest.get("version"),
        "git_commit": manifest.get("git_commit"),
        "dirty": manifest.get("dirty"),
        "source_date_epoch": manifest.get("source_date_epoch"),
        "target_architecture": manifest.get("target_architecture"),
        "files": rows,
    }
    expected_fingerprint = canonical_sha256(fingerprint_input)
    if manifest.get("package_fingerprint") != expected_fingerprint:
        raise ReleaseError("release package fingerprint mismatch")
    expected_release_id = (
        f"{manifest.get('version')}-{manifest.get('git_commit')}-"
        f"{expected_fingerprint[:12]}"
    )
    if manifest.get("release_id") != expected_release_id:
        raise ReleaseError("release_id does not match package fingerprint")
    return manifest


def normalized_tar_info(path: Path, name: str, epoch: int) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name=name)
    stat = path.stat()
    info.mtime = epoch
    info.uid = 0
    info.gid = 0
    info.uname = "root"
    info.gname = "root"
    if path.is_dir():
        info.type = tarfile.DIRTYPE
        info.mode = 0o755
        info.size = 0
    else:
        info.type = tarfile.REGTYPE
        info.mode = 0o755 if stat.st_mode & 0o111 else 0o644
        info.size = stat.st_size
    return info


def archive_members(root: Path) -> Iterable[tuple[Path, str]]:
    prefix = root.name
    yield root, prefix
    for path in sorted(root.rglob("*"), key=lambda item: safe_relative(item, root)):
        if path.is_symlink():
            raise ReleaseError(f"release archive cannot contain symlink: {path}")
        relative = safe_relative(path, root)
        yield path, f"{prefix}/{relative}"


def create_archive(args: argparse.Namespace) -> None:
    root = args.root.resolve()
    manifest = verify_root(root)
    epoch = int(manifest["source_date_epoch"])
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("wb") as raw:
            with gzip.GzipFile(
                filename="",
                mode="wb",
                compresslevel=9,
                fileobj=raw,
                mtime=epoch,
            ) as compressed:
                with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
                    for path, name in archive_members(root):
                        info = normalized_tar_info(path, name, epoch)
                        if path.is_dir():
                            archive.addfile(info)
                        else:
                            with path.open("rb") as handle:
                                archive.addfile(info, fileobj=handle)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    digest = sha256_file(output)
    checksum_path = output.with_name(output.name + ".sha256")
    checksum_path.write_text(f"{digest}  {output.name}\n", encoding="ascii")
    print(f"archive: {output}")
    print(f"sha256: {digest}")
    print(f"checksum: {checksum_path}")


def safe_extract(archive: tarfile.TarFile, destination: Path) -> None:
    destination = destination.resolve()
    for member in archive.getmembers():
        member_path = PurePosixPath(member.name)
        if member_path.is_absolute() or ".." in member_path.parts:
            raise ReleaseError(f"unsafe archive member: {member.name}")
        if not (member.isfile() or member.isdir()):
            raise ReleaseError(f"unsupported archive member: {member.name}")
    if "filter" in inspect.signature(archive.extractall).parameters:
        archive.extractall(destination, filter="data")
    else:
        archive.extractall(destination)


def verify_archive(path: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="murmurmark-release-verify-") as temporary:
        destination = Path(temporary)
        try:
            with tarfile.open(path, mode="r:gz") as archive:
                safe_extract(archive, destination)
        except (OSError, tarfile.TarError) as error:
            raise ReleaseError(f"cannot extract release archive {path}: {error}") from error
        roots = [item for item in destination.iterdir() if item.is_dir()]
        if len(roots) != 1:
            raise ReleaseError("release archive must contain exactly one top-level directory")
        return verify_root(roots[0])


def verify(args: argparse.Namespace) -> None:
    target = args.target.resolve()
    if target.is_dir():
        manifest = verify_root(target)
    elif target.is_file():
        manifest = verify_archive(target)
    else:
        raise ReleaseError(f"release target does not exist: {target}")
    print(f"release: {manifest['release_id']}")
    print(f"version: {manifest['version']}")
    print(f"package_fingerprint: {manifest['package_fingerprint']}")
    print("status: verified")


def metadata(args: argparse.Namespace) -> None:
    manifest = verify_root(args.root.resolve())
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)

    finalize_parser = commands.add_parser("finalize", help="write a complete release manifest")
    finalize_parser.add_argument("root", type=Path)
    finalize_parser.add_argument("--version", required=True)
    finalize_parser.add_argument("--git-commit", required=True)
    finalize_parser.add_argument("--source-date-epoch", required=True, type=int)
    finalize_parser.add_argument("--architecture", required=True)
    finalize_parser.add_argument("--dirty", action="store_true")
    finalize_parser.set_defaults(handler=finalize)

    archive_parser = commands.add_parser("archive", help="create a deterministic tar.gz")
    archive_parser.add_argument("root", type=Path)
    archive_parser.add_argument("output", type=Path)
    archive_parser.set_defaults(handler=create_archive)

    verify_parser = commands.add_parser("verify", help="verify a release directory or tar.gz")
    verify_parser.add_argument("target", type=Path)
    verify_parser.set_defaults(handler=verify)

    metadata_parser = commands.add_parser("metadata", help="print verified manifest JSON")
    metadata_parser.add_argument("root", type=Path)
    metadata_parser.set_defaults(handler=metadata)
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        args.handler(args)
    except ReleaseError as error:
        print(f"error: {error}", file=os.sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
