#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
out="${1:-$repo_root/.build/tools/murmurmark-aec-webrtc}"
manifest="$repo_root/tools/murmurmark-aec-webrtc/Cargo.toml"

if ! command -v cargo >/dev/null 2>&1; then
  echo "cargo is required to build the WebRTC APM helper" >&2
  echo "Install with: brew install rust meson ninja" >&2
  exit 1
fi

if ! command -v meson >/dev/null 2>&1 || ! command -v ninja >/dev/null 2>&1; then
  echo "meson and ninja are required for bundled webrtc-audio-processing" >&2
  echo "Install with: brew install meson ninja" >&2
  exit 1
fi

if [[ ! -f "$repo_root/tools/murmurmark-aec-webrtc/Cargo.lock" ]]; then
  cargo generate-lockfile --manifest-path "$manifest"
fi

cargo build --manifest-path "$manifest" --release --locked
mkdir -p "$(dirname "$out")"
cp "$repo_root/tools/murmurmark-aec-webrtc/target/release/murmurmark-aec-webrtc" "$out"
