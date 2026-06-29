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
acceptance_report="$(mktemp "${TMPDIR:-/tmp}/murmurmark-acceptance.json.XXXXXX")"
trap 'rm -f "$acceptance_report"' EXIT
acceptance_output="$(.build/debug/murmurmark acceptance --skip-release --report "$acceptance_report")"
printf '%s\n' "$acceptance_output"
tail -1 <<<"$acceptance_output" | grep -q '^next: murmurmark acceptance --live-checklist$'
jq -e '
  .schema == "murmurmark.cli_mvp_acceptance_report/v1"
  and .status == "ok"
  and .mode == "automated"
  and .next == "murmurmark acceptance --live-checklist"
  and any(.checks[]; .name == "self_test" and .status == "passed")
  and any(.manual_gates[]; .name == "live_recording" and .status == "manual")
' "$acceptance_report" >/dev/null
scripts/smoke-fixture.sh
