# Release Bundle Contract

The release bundle is a local CLI distribution layout. It is meant to make
MurmurMark runnable and inspectable without relying on a developer checkout.

It is not a full standalone macOS installer yet: Python, Homebrew tools and
whisper.cpp models stay external and are checked by `murmurmark doctor`.

## Directory Layout

```text
murmurmark-<version>-<commit>/
  bin/
    murmurmark
  libexec/
    murmurmark/
      murmurmark
  scripts/
  docs/
  examples/
  tools/
  README.md
  CONTRIBUTING.md
  SECURITY.md
  RELEASE_BUNDLE.md
  Package.swift
  murmurmark.config.example.json
  release-manifest.json
```

`bin/murmurmark` is a wrapper. It sets `MURMURMARK_HOME` to the bundle root and
executes `libexec/murmurmark/murmurmark`.

## Manifest

`release-manifest.json` uses `murmurmark.release_bundle/v1`.

Required fields:

```json
{
  "schema": "murmurmark.release_bundle/v1",
  "name": "murmurmark",
  "version": "0.1.0",
  "git_commit": "abcdef0",
  "dirty": false,
  "created_at": "2026-06-27T00:00:00Z",
  "layout": {
    "wrapper": "bin/murmurmark",
    "executable": "libexec/murmurmark/murmurmark",
    "home": "."
  },
  "included": [],
  "excluded": [],
  "external_requirements": [],
  "checksums": {
    "libexec/murmurmark/murmurmark": "sha256:..."
  }
}
```

## Inclusion Rules

The bundle may include only tracked, non-private project files needed to run or
understand the CLI:

- docs;
- scripts;
- examples that are safe to publish;
- helper source files under `tools`;
- `README.md`;
- `CONTRIBUTING.md`;
- `SECURITY.md`;
- `Package.swift`;
- `murmurmark.config.example.json`.

The bundle must not include:

- `sessions/`;
- `recordings/`;
- `exports/private/`;
- `.venv/`;
- `.build/`;
- models or weights;
- `murmurmark.config.json`;
- raw audio files;
- local ASR prompts or glossaries ignored by git.

## Verification

Build:

```bash
scripts/build-release-bundle.sh --verify
```

Verify:

```bash
BUNDLE="$(find dist/release-bundles -maxdepth 1 -type d -name 'murmurmark-*' | sort | tail -1)"
MURMURMARK_PYTHON="$PWD/.venv/bin/python" "$BUNDLE/bin/murmurmark" doctor --strict
MURMURMARK_PYTHON="$PWD/.venv/bin/python" "$BUNDLE/bin/murmurmark" self-test
MURMURMARK_PYTHON="$PWD/.venv/bin/python" "$BUNDLE/bin/murmurmark" acceptance --skip-release --report /tmp/murmurmark-release-acceptance.json
```

If the host Python does not contain MurmurMark's audio dependencies, set
`MURMURMARK_PYTHON` to a prepared environment before running the bundle.
`scripts/build-release-bundle.sh --verify --python PATH` runs both checks immediately after creating
the bundle.

In a bundle, `acceptance` does not require `Sources/`: it verifies the runnable bundle with
`doctor --strict`, `self-test` and local config initialization, then keeps the live recording gate as
manual in the optional report.
