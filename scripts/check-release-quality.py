#!/usr/bin/env python3
"""Fast contract and deterministic-packaging checks for Release-quality CLI."""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON = Path(os.environ.get("MURMURMARK_PYTHON", os.sys.executable)).resolve()


def run(*args: str | Path, check: bool = True, **kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(arg) for arg in args],
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **kwargs,
    )


def executable(path: Path) -> None:
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


source = (ROOT / "Sources/MurmurMarkCLI/MurmurMarkCLI.swift").read_text(encoding="utf-8")
match = re.search(r'static let version = "([^"]+)"', source)
assert match, "CLI version is missing"
version = match.group(1)

compatibility = json.loads((ROOT / "release/compatibility-v1.json").read_text(encoding="utf-8"))
licenses = json.loads((ROOT / "release/licenses-v1.json").read_text(encoding="utf-8"))
config = json.loads((ROOT / "murmurmark.config.example.json").read_text(encoding="utf-8"))
assert compatibility["schema"] == "murmurmark.release_compatibility/v1"
assert compatibility["release_version"] == version
assert compatibility["platform"]["minimum_version"] == "15.0"
assert compatibility["runtime"]["python"]["version"] == ">=3.12,<3.14"
assert compatibility["schemas"]["config"]["current"] == config["schema"]
assert compatibility["schemas"]["session"]["current"] == "murmurmark.session/v1"
assert compatibility["schemas"]["evidence_handoff"]["current"] == "murmurmark.evidence_handoff/v2"
assert compatibility["schemas"]["speaker_resolved_transcript_selection"]["current"] == (
    "murmurmark.speaker_resolved_transcript_selection/v1"
)
assert compatibility["schemas"]["reviewed_speaker_handoff"]["current"] == (
    "murmurmark.reviewed_speaker_handoff/v1"
)
assert licenses["schema"] == "murmurmark.release_license_inventory/v1"
assert any(row["name"] == "MurmurMark" and row["license"] == "MIT" for row in licenses["redistributed"])

for relative in [
    "scripts/build-release-bundle.sh",
    "scripts/install-release.sh",
    "scripts/acceptance-release-quality.sh",
    "scripts/release-bundle.py",
    "scripts/materialize-anonymous-rich-transcript.py",
    "scripts/review-remote-speaker-labels.py",
]:
    path = ROOT / relative
    assert path.is_file(), f"missing release file: {relative}"
    if path.suffix == ".sh":
        run("/bin/bash", "-n", path)

acceptance_source = (ROOT / "scripts/acceptance-release-quality.sh").read_text(encoding="utf-8")
assert "build_args[@]" not in acceptance_source, "empty Bash arrays are unsafe under macOS Bash with set -u"
assert acceptance_source.startswith("#!/usr/bin/env bash\nset -E"), "ERR diagnostics must survive subshell failures"
bundle_source = (ROOT / "scripts/build-release-bundle.sh").read_text(encoding="utf-8")
assert "dirty_args[@]" not in bundle_source, "empty Bash arrays are unsafe under macOS Bash with set -u"

with tempfile.TemporaryDirectory(prefix="murmurmark-release-unit-") as temporary:
    base = Path(temporary)
    bundle = base / "murmurmark-test"
    for directory in [
        "bin",
        "libexec/murmurmark",
        "scripts",
        "release",
    ]:
        (bundle / directory).mkdir(parents=True, exist_ok=True)
    for relative in [
        "README.md",
        "CHANGELOG.md",
        "LICENSE",
        "release/compatibility-v1.json",
        "release/licenses-v1.json",
        "scripts/release-bundle.py",
        "scripts/install-release.sh",
        "scripts/materialize-anonymous-rich-transcript.py",
        "scripts/review-remote-speaker-labels.py",
    ]:
        destination = bundle / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, destination)
    (bundle / "bin/murmurmark").write_text("#!/bin/bash\necho murmurmark 0.1.0\n", encoding="utf-8")
    (bundle / "libexec/murmurmark/murmurmark").write_text("fixture binary\n", encoding="utf-8")
    executable(bundle / "bin/murmurmark")
    executable(bundle / "scripts/release-bundle.py")
    executable(bundle / "scripts/install-release.sh")
    executable(bundle / "libexec/murmurmark/murmurmark")

    utility = bundle / "scripts/release-bundle.py"
    run(
        PYTHON,
        utility,
        "finalize",
        bundle,
        "--version",
        version,
        "--git-commit",
        "acceptance",
        "--source-date-epoch",
        "1700000000",
        "--architecture",
        "arm64",
    )
    run(PYTHON, utility, "verify", bundle)
    archive_a = base / "a.tar.gz"
    archive_b = base / "b.tar.gz"
    run(PYTHON, utility, "archive", bundle, archive_a)
    run(PYTHON, utility, "archive", bundle, archive_b)
    assert archive_a.read_bytes() == archive_b.read_bytes(), "release archives are not deterministic"
    run(PYTHON, utility, "verify", archive_a)

    with (bundle / "README.md").open("a", encoding="utf-8") as handle:
        handle.write("tamper\n")
    tampered = run(PYTHON, utility, "verify", bundle, check=False)
    assert tampered.returncode != 0
    assert "mismatch" in tampered.stderr

    unsafe_archive = base / "unsafe.tar.gz"
    with tarfile.open(unsafe_archive, mode="w:gz") as archive:
        info = tarfile.TarInfo(name="../escape")
        payload = b"unsafe\n"
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    unsafe = run(PYTHON, utility, "verify", unsafe_archive, check=False)
    assert unsafe.returncode != 0
    assert "unsafe archive member" in unsafe.stderr

binary = ROOT / ".build/debug/murmurmark"
if binary.is_file():
    with tempfile.TemporaryDirectory(prefix="murmurmark-runtime-root-") as temporary:
        result = run(
            binary,
            "version",
            "--json",
            cwd=temporary,
            env={**os.environ, "MURMURMARK_RUNTIME_HOME": str(ROOT)},
        )
        metadata = json.loads(result.stdout)
        assert metadata["version"] == version
        assert metadata["runtime_mode"] == "release_bundle"

print("release quality contract checks passed")
