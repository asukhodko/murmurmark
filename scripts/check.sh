#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

swift build
swiftlint lint --quiet
python3 -m py_compile scripts/*.py
scripts/check-open-source-readiness.sh
scripts/check-capture-regressions.sh
scripts/smoke-process-chunk-resume.sh
if [[ -f sessions/_reports/session-quality/session_quality_report.json ]]; then
  scripts/check-current-pipeline-stabilization.py
fi
if command -v cargo >/dev/null 2>&1; then
  cargo fmt --manifest-path tools/murmurmark-aec-webrtc/Cargo.toml --check
fi
acceptance_report="$(mktemp "${TMPDIR:-/tmp}/murmurmark-acceptance.json.XXXXXX")"
release_acceptance_report="$(mktemp "${TMPDIR:-/tmp}/murmurmark-release-acceptance.json.XXXXXX")"
release_acceptance_root="$(mktemp -d "${TMPDIR:-/tmp}/murmurmark-release-acceptance.XXXXXX")"
trap 'rm -f "$acceptance_report" "$release_acceptance_report"; rm -rf "$release_acceptance_root"' EXIT
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

mkdir -p "$release_acceptance_root/scripts"
cp scripts/acceptance-cli-mvp.sh "$release_acceptance_root/scripts/acceptance-cli-mvp.sh"
release_acceptance_output="$(
  MURMURMARK_BIN="$repo_root/.build/debug/murmurmark" \
    "$release_acceptance_root/scripts/acceptance-cli-mvp.sh" \
    --skip-release \
    --report "$release_acceptance_report"
)"
printf '%s\n' "$release_acceptance_output"
tail -1 <<<"$release_acceptance_output" | grep -q '^next: murmurmark acceptance --live-checklist$'
jq -e '
  .schema == "murmurmark.cli_mvp_acceptance_report/v1"
  and .status == "ok"
  and .mode == "release"
  and any(.checks[]; .name == "release_bundle" and .status == "current")
  and any(.checks[]; .name == "open_source_readiness" and .status == "not_applicable")
' "$release_acceptance_report" >/dev/null
scripts/smoke-fixture.sh
