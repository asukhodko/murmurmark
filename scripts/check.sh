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
"$python_bin" scripts/check-whisper-cpu-fallback.py
"$python_bin" scripts/check-review-materialization-guard.py
"$python_bin" scripts/check-session-quality-reconciliation.py
"$python_bin" scripts/check-remote-role-integrity.py
"$python_bin" scripts/check-live-voice-activity-retime.py
"$python_bin" scripts/check-live-progressive-target-me.py
"$python_bin" scripts/check-live-preview-hallucinations.py
"$python_bin" scripts/check-echo-sparse-overrange.py
"$python_bin" scripts/check-echo-promotion-timeline.py
"$python_bin" scripts/check-echo-suppression-promotion.py
"$python_bin" scripts/check-neural-residual-echo.py
"$python_bin" scripts/check-speaker-preserving-neural-echo-v2.py
"$python_bin" scripts/check-reference-conditioned-target-me-separation-v1.py
"$python_bin" scripts/check-target-me-identifiability-corpus-v1.py
"$python_bin" scripts/check-reference-conditioned-target-me-separation-v2.py
"$python_bin" scripts/check-pre-asr-residual-echo-ceiling-map-v1.py
"$python_bin" scripts/check-alignment-echo-path-model-v3.py
"$python_bin" scripts/check-multi-component-residual-separator-v1.py
"$python_bin" scripts/check-stronger-offline-target-speaker-separator-prerequisites-v1.py
"$python_bin" scripts/check-sepformer-four-stem-target-me-qualification-v1.py
"$python_bin" scripts/check-speaker-preserving-echo-adaptation-corpus.py
"$python_bin" scripts/check-controlled-echo-supervision-v1.py
"$python_bin" scripts/check-speaker-mode-hardening.py
"$python_bin" scripts/check-target-me-silence.py
"$python_bin" scripts/check-target-me-evidence-matching.py
"$python_bin" scripts/check-no-speech-outcome.py
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
"$python_bin" scripts/check-canonical-live-asr-producer.py
"$python_bin" scripts/check-canonical-live-asr-corpus.py
"$python_bin" scripts/check-causal-canonical-mic-asr.py
"$python_bin" scripts/check-causal-canonical-mic-asr-corpus.py
"$python_bin" scripts/check-remote-speaker-evidence.py
"$python_bin" scripts/check-remote-speaker-diarization.py
"$python_bin" scripts/check-remote-speaker-coverage-v3.py
"$python_bin" scripts/check-remote-speaker-residual-evidence-v4.py
"$python_bin" scripts/check-independent-remote-speaker-evidence-v1.py
"$python_bin" scripts/check-remote-speaker-residual-reference-corpus.py
"$python_bin" scripts/check-controlled-remote-speaker-truth-lab-v1.py
"$python_bin" scripts/check-duration-aware-remote-speaker-attribution-v2.py
"$python_bin" scripts/check-segment-context-remote-speaker-attribution-v1.py
"$python_bin" scripts/check-remote-speaker-attribution-error-decomposition-v1.py
"$python_bin" scripts/check-stronger-remote-speaker-identity-backend-qualification-v1.py
"$python_bin" scripts/check-ecapa-remote-speaker-shadow-qualification-v1.py
"$python_bin" scripts/check-remote-speaker-shadow-error-decomposition-v1.py
"$python_bin" scripts/check-bounded-remote-speaker-interval-purification-v1.py
"$python_bin" scripts/check-session-local-remote-speaker-enrollment-hardening-v1.py
"$python_bin" scripts/check-remote-speaker-direct-truth-seed-v1.py
"$python_bin" scripts/check-lexical-accuracy-reference-corpus.py
"$python_bin" scripts/check-transcript-perfection-corpus.py
"$python_bin" scripts/check-anonymous-rich-transcript.py
"$python_bin" scripts/check-reviewed-remote-speaker-naming.py
"$python_bin" scripts/check-reviewed-speaker-memory.py
"$python_bin" scripts/check-evidence-guarded-local-synthesis.py
"$python_bin" scripts/report-evidence-guarded-local-synthesis-corpus.py \
  --verify-frozen-only \
  --frozen-manifest docs/testing/evidence-guarded-local-synthesis-v1-manifest.json
"$python_bin" scripts/check-evidence-only-local-note-selection.py
"$python_bin" scripts/report-evidence-only-local-note-selection-corpus.py \
  --verify-frozen-only \
  --frozen-manifest docs/testing/evidence-only-local-note-selection-v1-manifest.json
"$python_bin" scripts/report-reviewed-remote-speaker-naming-corpus.py \
  --strict \
  --frozen-manifest docs/testing/reviewed-remote-speaker-naming-v1-manifest.json
"$python_bin" scripts/report-reviewed-speaker-memory-corpus.py \
  --strict \
  --frozen-manifest docs/testing/reviewed-speaker-memory-v1-manifest.json
"$python_bin" scripts/check-authoritative-incremental-asr.py
"$python_bin" scripts/check-authoritative-handoff.py
"$python_bin" scripts/check-authoritative-handoff-corpus.py
"$python_bin" scripts/check-evidence-handoff-v2.py
"$python_bin" scripts/check-fast-diagnostics.py
"$python_bin" scripts/check-bounded-asr-parallelism.py
"$python_bin" scripts/check-resource-policy.py
"$python_bin" scripts/check-audio-review-clip-parallelism.py
"$python_bin" scripts/check-stronger-audio-judge.py
"$python_bin" scripts/check-capture-continuity.py
"$python_bin" scripts/check-independent-me-evidence.py
"$python_bin" scripts/check-authoritative-boundary.py
"$python_bin" scripts/check-residual-me-evidence.py
"$python_bin" scripts/check-residual-audio-arbitration.py
"$python_bin" scripts/check-residual-local-recall.py
"$python_bin" scripts/check-residual-local-recall-corpus.py
"$python_bin" scripts/check-local-speech-completion-v2.py
"$python_bin" scripts/check-local-speech-completion-corpus.py
"$python_bin" scripts/check-mixed-utterance-separation.py
"$python_bin" scripts/check-experiment-compare-timeout.py
"$python_bin" scripts/check-planning-consistency.py
"$python_bin" scripts/check-release-quality.py
MURMURMARK_BIN="$repo_root/.build/debug/murmurmark" \
  "$python_bin" scripts/check-derived-compaction.py
MURMURMARK_BIN="$repo_root/.build/debug/murmurmark" \
  "$python_bin" scripts/check-meeting-lifecycle.py
"$python_bin" scripts/check-meeting-lifecycle-corpus.py
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
    and any(.checks[]; .name == "meeting_lifecycle" and .status == "passed")
    and any(.manual_gates[]; .name == "live_recording" and .status == "manual")
  ' "$acceptance_report" >/dev/null

  release_bundle_out="$release_acceptance_root/bundles"
  scripts/build-release-bundle.sh \
    --out-dir "$release_bundle_out" \
    --no-archive \
    --python "$python_bin" >/dev/null
  release_bundle_root="$(find "$release_bundle_out" -mindepth 1 -maxdepth 1 -type d -name 'murmurmark-*' -print -quit)"
  [[ -n "$release_bundle_root" ]]
  release_acceptance_output="$(
    MURMURMARK_BIN="$release_bundle_root/bin/murmurmark" \
    MURMURMARK_PYTHON="$python_bin" \
      "$release_bundle_root/scripts/acceptance-cli-mvp.sh" \
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
