#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
out="${1:-$repo_root/.build/tools/murmurmark-aec-speexdsp}"

pkg_config="${PKG_CONFIG:-}"
if [[ -z "$pkg_config" ]]; then
  if command -v pkg-config >/dev/null 2>&1; then
    pkg_config="pkg-config"
  elif command -v pkgconf >/dev/null 2>&1; then
    pkg_config="pkgconf"
  else
    echo "pkg-config/pkgconf is required to build the SpeexDSP helper" >&2
    echo "Install with: brew install pkgconf speexdsp" >&2
    exit 1
  fi
fi

if ! "$pkg_config" --exists speexdsp; then
  echo "speexdsp development files were not found by $pkg_config" >&2
  echo "Install with: brew install speexdsp" >&2
  exit 1
fi

mkdir -p "$(dirname "$out")"
cc -O2 -Wall -Wextra \
  "$repo_root/tools/murmurmark-aec-speexdsp.c" \
  -o "$out" \
  $("$pkg_config" --cflags --libs speexdsp)
