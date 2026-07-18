#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
python_bin="${MURMURMARK_PYTHON:-python3}"
if [[ -z "${MURMURMARK_PYTHON:-}" && -x "$repo_root/.venv/bin/python" ]]; then
  python_bin="$repo_root/.venv/bin/python"
fi

swift build
swiftlint lint --quiet
"$python_bin" -m py_compile scripts/*.py
"$python_bin" scripts/check-transcript-dedupe.py
"$python_bin" scripts/check-remote-role-integrity.py
"$python_bin" scripts/check-live-voice-activity-retime.py
"$python_bin" scripts/check-live-progressive-target-me.py
"$python_bin" scripts/check-live-profile-selection.py
"$python_bin" scripts/check-live-order-role-reconciliation.py
"$python_bin" scripts/check-live-local-recall-hardening.py
"$python_bin" scripts/check-live-causal-local-island-micro-asr-v2.py
"$python_bin" scripts/check-live-causal-remote-active-me-separation-v1.py
"$python_bin" scripts/check-live-causal-double-talk-me-recovery-v1.py
"$python_bin" scripts/check-causal-recovery-generalization-unit.py
"$python_bin" scripts/check-causal-candidate-prefilter-v1.py
"$python_bin" scripts/check-live-causal-me-recovery-runtime.py
"$python_bin" scripts/check-live-recovery-incremental-cache.py
"$python_bin" scripts/check-live-asr-cache-compatibility.py
"$python_bin" scripts/check-authoritative-handoff.py
"$python_bin" scripts/check-authoritative-handoff-corpus.py
"$python_bin" scripts/check-bounded-asr-parallelism.py
"$python_bin" scripts/check-audio-review-clip-parallelism.py
"$python_bin" scripts/check-independent-me-evidence.py
"$python_bin" scripts/check-authoritative-boundary.py
"$python_bin" scripts/check-residual-me-evidence.py
"$python_bin" scripts/check-experiment-compare-timeout.py
scripts/check-open-source-readiness.sh
scripts/check-capture-regressions.sh
scripts/smoke-experimental-sidecar-contract.sh
scripts/smoke-committed-pcm-sidecar.sh
scripts/smoke-live-worker-handoff.sh
scripts/smoke-live-session-evidence.sh
scripts/smoke-live-watch-in-progress.sh
scripts/smoke-live-replay-lab.sh
scripts/smoke-raw-sidecar-worker.sh
scripts/smoke-process-chunk-resume.sh
if [[ -f sessions/_reports/session-quality/session_quality_report.json ]]; then
  scripts/check-current-pipeline-stabilization.py
fi
prefilter_report="sessions/_reports/live-pipeline/causal-candidate-coverage-cheap-negative-prefilter-v1/coverage_report_v1.json"
if [[ -f "$prefilter_report" ]]; then
  "$python_bin" scripts/check-causal-candidate-prefilter-acceptance-v1.py
fi
if command -v cargo >/dev/null 2>&1; then
  cargo fmt --manifest-path tools/murmurmark-aec-webrtc/Cargo.toml --check
fi
doctor_output="$("$repo_root/.build/debug/murmurmark" doctor 2>/dev/null || true)"
if grep -q 'shareable displays: 0' <<<"$doctor_output"; then
  echo "acceptance smoke skipped: no shareable display found"
else
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
fi
scripts/smoke-fixture.sh
