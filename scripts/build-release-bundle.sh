#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
invocation_dir="$PWD"
out_dir="$repo_root/dist/release-bundles"
build=1
verify=0
archive=1
verify_python="${MURMURMARK_PYTHON:-}"

usage() {
  cat <<'EOF'
usage: scripts/build-release-bundle.sh [--out-dir DIR] [--no-build] [--no-archive] [--verify] [--python PATH]

Builds a deterministic local MurmurMark release distribution:
  murmurmark-<version>-<commit>/
  murmurmark-<version>-<commit>.tar.gz
  murmurmark-<version>-<commit>.tar.gz.sha256

The payload contains only public runtime files. Sessions, exports, raw audio,
models, virtual environments and murmurmark.config.json are never included.
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
    --no-archive)
      archive=0
      shift
      ;;
    --verify)
      verify=1
      shift
      ;;
    --python)
      [[ $# -ge 2 ]] || { echo "error: --python requires a path" >&2; exit 2; }
      verify_python="$2"
      shift 2
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

version="$(awk -F'"' '/static let version/ {print $2; exit}' "$repo_root/Sources/MurmurMarkCLI/MurmurMarkCLI.swift")"
git_commit="$(git -C "$repo_root" rev-parse --short=12 HEAD)"
source_date_epoch="${SOURCE_DATE_EPOCH:-$(git -C "$repo_root" show -s --format=%ct HEAD)}"
architecture="$(uname -m)"
dirty=false
if [[ -n "$(git -C "$repo_root" status --porcelain --untracked-files=normal)" ]]; then
  dirty=true
fi

bundle_name="murmurmark-${version}-${git_commit}"
if [[ "$dirty" == "true" ]]; then
  bundle_name="${bundle_name}-dirty"
fi
mkdir -p "$out_dir"
out_dir="$(cd "$out_dir" && pwd)"
bundle_root="$out_dir/$bundle_name"
stage="$out_dir/.stage-$bundle_name-$$"
archive_path="$out_dir/$bundle_name.tar.gz"

cleanup() {
  rm -rf "$stage"
}
trap cleanup EXIT

release_bin="$repo_root/.build/release/murmurmark"
if [[ "$build" == "1" ]]; then
  nice -n 20 swift build -c release --package-path "$repo_root"
elif [[ ! -x "$release_bin" ]]; then
  echo "error: release binary not found: $release_bin" >&2
  echo "hint: rerun without --no-build" >&2
  exit 1
fi

rm -rf "$stage"
mkdir -p "$stage/bin" "$stage/libexec/murmurmark"
cp "$release_bin" "$stage/libexec/murmurmark/murmurmark"
strip -Sx "$stage/libexec/murmurmark/murmurmark"
chmod +x "$stage/libexec/murmurmark/murmurmark"

copy_file() {
  local rel="$1"
  [[ -f "$repo_root/$rel" ]] || return 0
  mkdir -p "$stage/$(dirname "$rel")"
  cp -p "$repo_root/$rel" "$stage/$rel"
}

git -C "$repo_root" ls-files -z -- \
  README.md \
  CHANGELOG.md \
  CONTRIBUTING.md \
  SECURITY.md \
  LICENSE \
  LICENSE.md \
  Package.swift \
  .swiftlint.yml \
  murmurmark.config.example.json \
  docs \
  examples \
  policies \
  release \
  scripts \
  tools |
while IFS= read -r -d '' rel; do
  copy_file "$rel"
done

# Development builds may contain the release implementation before it is staged.
# The final clean release still contains only tracked files.
copy_file "CHANGELOG.md"
copy_file "release/compatibility-v1.json"
copy_file "release/licenses-v1.json"
copy_file "scripts/release-bundle.py"
copy_file "scripts/install-release.sh"
copy_file "scripts/acceptance-release-quality.sh"
copy_file "scripts/check-release-quality.py"

cat >"$stage/bin/murmurmark" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
bundle_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export MURMURMARK_RUNTIME_HOME="$bundle_root"
export PYTHONDONTWRITEBYTECODE=1
: "${MURMURMARK_BIN:=$bundle_root/bin/murmurmark}"
export MURMURMARK_BIN
exec "$bundle_root/libexec/murmurmark/murmurmark" "$@"
EOF
chmod +x "$stage/bin/murmurmark"

cat >"$stage/install.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
bundle_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$bundle_root/scripts/install-release.sh" --bundle "$bundle_root" "$@"
EOF
chmod +x "$stage/install.sh"

cat >"$stage/RELEASE_BUNDLE.md" <<'EOF'
# MurmurMark Release Bundle

This directory is a verified local CLI distribution. It contains no session,
raw audio, model, local config or private export data.

Install transactionally:

```bash
./install.sh --python /path/to/python3
export PATH="$HOME/.local/bin:$PATH"
```

Then enter a workspace where `murmurmark.config.json`, `sessions/` and exports
should live:

```bash
cd /path/to/workspace
export MURMURMARK_PYTHON=/path/to/python3
murmurmark config init
murmurmark doctor --strict
murmurmark self-test
```

Verify without installing:

```bash
python3 scripts/release-bundle.py verify .
MURMURMARK_PYTHON=/path/to/python3 bin/murmurmark doctor --strict
```
EOF

dirty_arg=""
if [[ "$dirty" == "true" ]]; then
  dirty_arg="--dirty"
fi
python3 "$repo_root/scripts/release-bundle.py" finalize "$stage" \
  --version "$version" \
  --git-commit "$git_commit" \
  --source-date-epoch "$source_date_epoch" \
  --architecture "$architecture" \
  ${dirty_arg:+"$dirty_arg"}

rm -rf "$bundle_root"
mv "$stage" "$bundle_root"
python3 "$bundle_root/scripts/release-bundle.py" verify "$bundle_root" >/dev/null

if [[ "$archive" == "1" ]]; then
  python3 "$bundle_root/scripts/release-bundle.py" archive "$bundle_root" "$archive_path"
  python3 "$bundle_root/scripts/release-bundle.py" verify "$archive_path" >/dev/null
fi

if [[ -z "$verify_python" && -x "$repo_root/.venv/bin/python" ]]; then
  verify_python="$repo_root/.venv/bin/python"
fi
if [[ -n "$verify_python" && "$verify_python" != /* ]]; then
  verify_python="$invocation_dir/$verify_python"
fi

echo "bundle: $bundle_root"
echo "manifest: $bundle_root/release-manifest.json"
if [[ "$archive" == "1" ]]; then
  echo "archive: $archive_path"
  echo "checksum: $archive_path.sha256"
fi

if [[ "$verify" == "1" ]]; then
  if [[ -z "$verify_python" || ! -x "$verify_python" ]]; then
    echo "error: --verify requires an executable Python runtime" >&2
    echo "hint: pass --python /path/to/python3" >&2
    exit 1
  fi
  MURMURMARK_PYTHON="$verify_python" "$bundle_root/bin/murmurmark" doctor --strict
  MURMURMARK_PYTHON="$verify_python" "$bundle_root/bin/murmurmark" self-test
  MURMURMARK_PYTHON="$verify_python" "$bundle_root/bin/murmurmark" acceptance --skip-release
fi
