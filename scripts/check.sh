#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

swift build
swiftlint lint --quiet
python3 -m py_compile scripts/*.py
scripts/check-open-source-readiness.sh
if command -v cargo >/dev/null 2>&1; then
  cargo fmt --manifest-path tools/murmurmark-aec-webrtc/Cargo.toml --check
fi
acceptance_output="$(.build/debug/murmurmark acceptance --skip-release)"
printf '%s\n' "$acceptance_output"
tail -1 <<<"$acceptance_output" | grep -q '^next: murmurmark acceptance --live-checklist$'
scripts/smoke-fixture.sh
