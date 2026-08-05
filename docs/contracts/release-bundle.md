# Release Bundle Contract

MurmurMark publishes a verified local CLI archive that runs without a developer checkout. Python,
system tools and model weights remain external; `murmurmark doctor --strict` validates them against
the versioned compatibility contract.

## Artifact Set

```text
murmurmark-<version>-<commit>.tar.gz
murmurmark-<version>-<commit>.tar.gz.sha256

murmurmark-<version>-<commit>/
  bin/murmurmark
  libexec/murmurmark/murmurmark
  scripts/
  docs/
  examples/
  policies/
  release/
  CHANGELOG.md
  LICENSE
  RELEASE_BUNDLE.md
  install.sh
  release-manifest.json
```

The wrapper sets `MURMURMARK_RUNTIME_HOME` to the immutable package. The current directory remains
the external workspace containing `murmurmark.config.json`, `sessions/` and exports. Installing or
upgrading a runtime must never rewrite that workspace.

## Manifest

`release-manifest.json` uses `murmurmark.release_bundle/v2`. It records:

- version, source commit, architecture, source epoch and dirty state;
- immutable `release_id` and package fingerprint;
- every packaged path with mode, byte size and SHA-256;
- hashes and schemas of `release/compatibility-v1.json` and `release/licenses-v1.json`;
- explicit confirmation that sessions, raw audio, models and local config are absent.

The package fingerprint is derived from the normalized file inventory. Verification rejects a
missing, added, renamed, mode-changed or byte-changed file. Archive extraction rejects absolute
paths, traversal and unsafe links.

## Compatibility

`murmurmark.release_compatibility/v1` is the supported-environment contract. Release `0.1.0`
supports:

- macOS 15 or newer on arm64;
- a Swift 6 toolchain for source builds;
- Python `>=3.12,<3.14` with the listed required modules;
- FFmpeg/ffprobe 7 or newer and whisper.cpp 1.7 or newer;
- the tested `ggml-large-v3-q5_0.bin` model name, size and SHA-256.

Optional faster-whisper and WavLM/Resemblyzer models improve bounded diagnostics or the guarded
Echo profile. Their absence is a warning and must not block the baseline meeting path.

Config `v1`, session `v1` and Evidence Handoff `v2` are read in place. There is no destructive
installer migration. A future incompatible schema requires a new compatibility contract.

## Transactional Installation

`install.sh` verifies the package before copying it into:

```text
<prefix>/share/murmurmark/releases/<release_id>/
<prefix>/share/murmurmark/current -> releases/<release_id>
<prefix>/bin/murmurmark
```

Each release directory is immutable. Staging and self-test finish before the `current` link changes.
Activation uses atomic replacement. A failed verification, staging test or post-switch version
check preserves or restores the previous wrapper and release. Reinstalling the same release is
idempotent. `install-state.json` and `install-history.jsonl` contain package metadata, not meeting
content.

## Build And Verification

```bash
scripts/build-release-bundle.sh \
  --out-dir dist/release-bundles \
  --verify \
  --python .venv/bin/python

python3 scripts/release-bundle.py verify \
  dist/release-bundles/murmurmark-<version>-<commit>.tar.gz
```

The builder normalizes modes and timestamps, sorts all members, strips the binary and emits a
deterministic gzip stream. Two builds from the same source epoch must be byte-identical.

Full isolated acceptance:

```bash
scripts/acceptance-release-quality.sh \
  --python .venv/bin/python \
  --report dist/release-quality-acceptance.json
```

It exercises two identical archives, clean install, strict doctor, package self-test, offline
Evidence Handoff v2 and guarded export, idempotent reinstall, compatible upgrade, corrupted upgrade
rollback and actionable missing-dependency diagnostics. The temporary HOME, prefix and workspace
are isolated from the developer checkout.

## Privacy And Inclusion

The archive contains tracked public runtime files only. Verification rejects local config,
sessions, exports, virtual environments, models, raw audio, secrets and concrete user-home paths.
External tools and weights are not redistributed; their licensing remains the operator's
responsibility and is listed in `release/licenses-v1.json`.
