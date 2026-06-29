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
.build/debug/murmurmark self-test
scripts/smoke-fixture.sh
