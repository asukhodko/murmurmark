#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
out_dir="$repo_root/dist/release-bundles"
build=1

usage() {
  cat <<'EOF'
usage: scripts/build-release-bundle.sh [--out-dir DIR] [--no-build]

Builds a local MurmurMark release bundle:
  bin/murmurmark
  libexec/murmurmark/murmurmark
  scripts/
  docs/
  examples/
  tools/
  murmurmark.config.example.json
  release-manifest.json

The bundle contains tracked project files only. It does not copy sessions,
exports, raw audio, models, .venv or murmurmark.config.json.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --out-dir)
      [[ $# -ge 2 ]] || { echo "error: --out-dir requires a path" >&2; exit 2; }
      out_dir="$2"
      shift 2
      ;;
    --no-build)
      build=0
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "error: unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

version="$(awk -F'"' '/let murmurmarkVersion/ {print $2; exit}' "$repo_root/Sources/MurmurMarkCLI/main.swift")"
git_commit="$(git -C "$repo_root" rev-parse --short HEAD)"
dirty="false"
if [[ -n "$(git -C "$repo_root" status --porcelain --untracked-files=normal)" ]]; then
  dirty="true"
fi

bundle_name="murmurmark-${version}-${git_commit}"
if [[ "$dirty" == "true" ]]; then
  bundle_name="${bundle_name}-dirty"
fi
bundle_root="$out_dir/$bundle_name"

release_bin="$repo_root/.build/release/murmurmark"
if [[ "$build" == "1" ]]; then
  swift build -c release --package-path "$repo_root"
elif [[ ! -x "$release_bin" ]]; then
  echo "error: release binary not found: $release_bin" >&2
  echo "hint: rerun without --no-build" >&2
  exit 1
fi

rm -rf "$bundle_root"
mkdir -p "$bundle_root/bin" "$bundle_root/libexec/murmurmark"
cp "$release_bin" "$bundle_root/libexec/murmurmark/murmurmark"
chmod +x "$bundle_root/libexec/murmurmark/murmurmark"

copy_file() {
  local rel="$1"
  [[ -f "$repo_root/$rel" ]] || return 0
  mkdir -p "$bundle_root/$(dirname "$rel")"
  cp -p "$repo_root/$rel" "$bundle_root/$rel"
}

git -C "$repo_root" ls-files -z -- \
  README.md \
  CONTRIBUTING.md \
  SECURITY.md \
  LICENSE \
  LICENSE.md \
  Package.swift \
  .swiftlint.yml \
  murmurmark.config.example.json \
  docs \
  examples \
  scripts \
  tools |
while IFS= read -r -d '' rel; do
  copy_file "$rel"
done

# Include this script before it is committed, so local verification can inspect
# the same bundle layout during development.
copy_file "scripts/build-release-bundle.sh"
copy_file "scripts/apply-retention-policy.py"
copy_file "scripts/build-provider-payload-manifest.py"
copy_file "examples/retention-policy.local-first.json"
copy_file "docs/contracts/retention-policy.md"

cat > "$bundle_root/bin/murmurmark" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
bundle_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export MURMURMARK_HOME="$bundle_root"
if [[ -z "${MURMURMARK_PYTHON:-}" && -x "$bundle_root/.venv/bin/python" ]]; then
  export MURMURMARK_PYTHON="$bundle_root/.venv/bin/python"
fi
exec "$bundle_root/libexec/murmurmark/murmurmark" "$@"
EOF
chmod +x "$bundle_root/bin/murmurmark"

binary_sha="$(shasum -a 256 "$bundle_root/libexec/murmurmark/murmurmark" | awk '{print $1}')"
created_at="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"

cat > "$bundle_root/RELEASE_BUNDLE.md" <<'EOF'
# MurmurMark Local Release Bundle

This bundle contains the MurmurMark CLI binary plus tracked scripts, docs,
examples and helper source files. It intentionally does not include private
meeting sessions, exports, raw audio, models, `.venv` or local config.

Run:

```bash
bin/murmurmark doctor
bin/murmurmark process ./sessions/<session>
```

If the host Python does not have MurmurMark's audio dependencies, set:

```bash
export MURMURMARK_PYTHON=/path/to/python
```
EOF

cat > "$bundle_root/release-manifest.json" <<EOF
{
  "schema": "murmurmark.release_bundle/v1",
  "name": "murmurmark",
  "version": "$version",
  "git_commit": "$git_commit",
  "dirty": $dirty,
  "created_at": "$created_at",
  "layout": {
    "wrapper": "bin/murmurmark",
    "executable": "libexec/murmurmark/murmurmark",
    "home": "."
  },
  "included": [
    "README.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "Package.swift",
    ".swiftlint.yml",
    "murmurmark.config.example.json",
    "docs/",
    "examples/",
    "scripts/",
    "tools/"
  ],
  "excluded": [
    "sessions/",
    "recordings/",
    "exports/private/",
    ".venv/",
    ".build/",
    "models/",
    "weights/",
    "murmurmark.config.json",
    "raw audio files"
  ],
  "external_requirements": [
    "macOS with ScreenCaptureKit permissions",
    "ffmpeg and ffprobe",
    "whisper.cpp whisper-cli",
    "multilingual whisper.cpp model",
    "Python with numpy, scipy, soundfile, librosa and sklearn"
  ],
  "checksums": {
    "libexec/murmurmark/murmurmark": "sha256:$binary_sha"
  }
}
EOF

echo "bundle: $bundle_root"
echo "manifest: $bundle_root/release-manifest.json"
echo "try: $bundle_root/bin/murmurmark doctor"
