# Install And Upgrade

This runbook installs the packaged CLI while keeping the runtime separate from the workspace that
holds local config and sessions.

## Prerequisites

- macOS 15 or newer on Apple Silicon;
- Python 3.12 or 3.13 with MurmurMark's required modules;
- `ffmpeg`, `ffprobe` and `whisper-cli` in `PATH`;
- the tested multilingual whisper.cpp model at the path from
  `release/compatibility-v1.json`.

The release archive does not download tools, models or contact a network at runtime.

## Verify And Install

```bash
shasum -a 256 -c murmurmark-<version>-<commit>.tar.gz.sha256
tar -xzf murmurmark-<version>-<commit>.tar.gz
cd murmurmark-<version>-<commit>

python3 scripts/release-bundle.py verify .
./install.sh --python /absolute/path/to/python3
export PATH="$HOME/.local/bin:$PATH"
```

Choose or create a workspace outside the installed release:

```bash
mkdir -p "$HOME/murmurmark-workspace"
cd "$HOME/murmurmark-workspace"
export MURMURMARK_PYTHON=/absolute/path/to/python3

murmurmark version --json
murmurmark config init
murmurmark doctor --strict
murmurmark self-test
```

`doctor --strict` must report the release compatibility and manifest as valid. The baseline path
may continue with warnings for optional local models, but missing required tools, Python modules or
the tested Whisper model fail with a repair hint.

## Upgrade

Extract and verify the new archive, then run its `install.sh` with the same prefix and Python:

```bash
cd murmurmark-<new-version>-<commit>
python3 scripts/release-bundle.py verify .
./install.sh --python /absolute/path/to/python3

cd "$HOME/murmurmark-workspace"
murmurmark doctor --strict
murmurmark self-test
```

The installer stages and tests the new immutable release before atomically switching `current`.
Existing config, sessions, raw CAF and Evidence Handoff fingerprints are external and remain
unchanged. Repeating the same install is safe.

If verification or upgrade fails, stop and keep using the existing `murmurmark` command. The old
release remains current. Do not delete the prior release directory until the new install and a real
meeting have both been verified.

## Developer Checkout

Contributors may keep using the repository-local installer:

```bash
source .venv/bin/activate
scripts/install-local.sh
export PATH="$HOME/.local/bin:$PATH"
murmurmark doctor --strict
murmurmark self-test
```

This path builds current source and is not the clean-install release acceptance path.
