#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
prefix="${MURMURMARK_PREFIX:-$HOME/.local}"
build=1

usage() {
  cat <<'EOF'
usage: scripts/install-local.sh [--prefix DIR] [--no-build]

Installs a local murmurmark wrapper into DIR/bin.
The wrapper keeps MURMURMARK_HOME pointed at this repository, so the CLI can
find scripts, local config and sessions when called from any directory.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix)
      [[ $# -ge 2 ]] || { echo "error: --prefix requires a path" >&2; exit 2; }
      prefix="$2"
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

release_bin="$repo_root/.build/release/murmurmark"
if [[ "$build" == "1" ]]; then
  swift build -c release --package-path "$repo_root"
elif [[ ! -x "$release_bin" ]]; then
  echo "error: release binary not found: $release_bin" >&2
  echo "hint: rerun without --no-build" >&2
  exit 1
fi

bin_dir="$prefix/bin"
wrapper="$bin_dir/murmurmark"
mkdir -p "$bin_dir"
printf -v quoted_repo_root '%q' "$repo_root"

cat > "$wrapper" <<EOF
#!/usr/bin/env bash
set -euo pipefail
export MURMURMARK_HOME=$quoted_repo_root
exec "\$MURMURMARK_HOME/.build/release/murmurmark" "\$@"
EOF

chmod +x "$wrapper"

echo "installed: $wrapper"
echo "home: $repo_root"
if [[ ":$PATH:" != *":$bin_dir:"* ]]; then
  echo "path hint: add this to your shell profile:"
  echo "  export PATH=\"$bin_dir:\$PATH\""
fi
echo "next:"
echo "  murmurmark doctor"
echo "  murmurmark self-test"
echo "  murmurmark config init"
echo "  murmurmark acceptance --skip-release"
echo "  murmurmark acceptance --live-checklist"
echo "  murmurmark record --target-bundle system"
echo "  murmurmark inspect latest"
echo "  murmurmark process latest"
echo "  murmurmark status latest"
echo "  murmurmark acceptance --live-session latest --report /tmp/murmurmark-live-session.json"
