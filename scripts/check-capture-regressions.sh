#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

source_file="Sources/MurmurMarkCLI/MurmurMarkCLI.swift"

fail() {
  echo "capture regression check failed: $*" >&2
  exit 1
}

require_tool() {
  command -v "$1" >/dev/null 2>&1 || fail "missing required tool: $1"
}

assert_static_capture_contract() {
  [[ -f "$source_file" ]] || fail "missing $source_file"

  if grep -Eq 'config\.excludesCurrentProcessAudio[[:space:]]*=[[:space:]]*true' "$source_file"; then
    fail "ScreenCaptureKit system-audio capture must not exclude current-process audio"
  fi

  grep -Eq 'config\.capturesAudio[[:space:]]*=[[:space:]]*remoteBackend[[:space:]]*==[[:space:]]*\.screenCaptureKit' "$source_file" \
    || fail "ScreenCaptureKit remote capture must enable config.capturesAudio for the screenCaptureKit backend"

  grep -Eq 'config\.excludesCurrentProcessAudio[[:space:]]*=[[:space:]]*false' "$source_file" \
    || fail "ScreenCaptureKit system-audio capture must explicitly set excludesCurrentProcessAudio to false"

  grep -q 'terminal-launched or otherwise related system audio' "$source_file" \
    || fail "capture exclusion rationale comment is missing"

  grep -q 'capture finalized as partial because both mic and remote tracks are silent' "$source_file" \
    || fail "silent mic+remote capture must be finalized as partial"

  grep -q 'capture produced no ScreenCaptureKit audio samples for' "$source_file" \
    || fail "ScreenCaptureKit no-sample capture must be detected"
}

assert_silent_pipeline_gate() {
  require_tool jq

  local workdir
  workdir="$(mktemp -d "${TMPDIR:-/tmp}/murmurmark-silent-capture-gate.XXXXXX")"
  trap 'rm -rf "$workdir"' RETURN

  local session="$workdir/session"
  mkdir -p "$session"
  jq -n '{
    schema: "murmurmark.session/v1",
    session_id: "silent-capture-fixture",
    created_at: "2026-07-06T08:00:00.000Z",
    ended_at: "2026-07-06T08:10:00.000Z",
    app_version: "0.1.0",
    capture_mode: "screencapturekit_system",
    status: "completed_with_warnings",
    health: {
      actual_duration_sec: 600,
      stop_reason: "sigint",
      partial: false,
      warnings: [
        "mic track appears silent or almost silent (RMS -85.9 dB)",
        "remote track appears silent or almost silent (RMS -inf dB)"
      ]
    }
  }' >"$session/session.json"

  local fake_model="$workdir/fake-model.bin"
  : >"$fake_model"

  set +e
  python3 "$repo_root/scripts/run-session-pipeline.py" "$session" \
    --murmurmark-bin "${MURMURMARK_BIN:-$repo_root/.build/debug/murmurmark}" \
    --model "$fake_model" \
    --skip-build \
    --skip-preprocess \
    --skip-transcription \
    --skip-audits \
    --skip-cleanup >"$workdir/process.log" 2>&1
  local status=$?
  set -e

  [[ "$status" -eq 2 ]] || {
    cat "$workdir/process.log" >&2
    fail "silent capture fixture should be blocked before processing"
  }
  grep -q 'blocker: silent_capture' "$workdir/process.log" \
    || fail "silent capture fixture did not print silent_capture blocker"
  jq -e '.blocker == "silent_capture" and .status == "blocked"' \
    "$session/derived/pipeline-run/pipeline_run_report.json" >/dev/null \
    || fail "silent capture fixture did not write blocked pipeline report"
}

run_live_capture_probe() {
  require_tool ffmpeg
  require_tool ffplay
  require_tool ffprobe
  require_tool jq

  local bin="${MURMURMARK_BIN:-$repo_root/.build/debug/murmurmark}"
  if [[ ! -x "$bin" ]]; then
    swift build >/dev/null
  fi

  local workdir
  workdir="$(mktemp -d "${TMPDIR:-/tmp}/murmurmark-capture-regression.XXXXXX")"
  trap 'rm -rf "$workdir"' RETURN

  local session="$workdir/session"
  local record_log="$workdir/record.log"

  (
    sleep 1
    ffplay -autoexit -loglevel quiet -f lavfi -i 'sine=frequency=770:duration=8:sample_rate=48000'
  ) &
  "$bin" record \
    --target-bundle system \
    --duration 10 \
    --live-pipeline \
    --live-segment-sec 5 \
    --live-overlap-sec 1 \
    --live-no-finalize \
    --out "$session" >"$record_log" 2>&1
  wait

  [[ -s "$session/session.json" ]] || {
    cat "$record_log" >&2
    fail "live capture probe did not create session.json"
  }

  jq -e '.health.summary == "ok" and (.health.screen_capture_restart_count // 0) == 0' \
    "$session/session.json" >/dev/null || {
      cat "$record_log" >&2
      jq '.health' "$session/session.json" >&2
      fail "live capture probe health is not ok"
    }

  local remote_mean
  remote_mean="$(
    ffmpeg -hide_banner -nostats \
      -i "$session/audio/remote/000001.caf" \
      -af volumedetect \
      -f null - 2>&1 |
      awk '/mean_volume:/ {print $(NF-1); exit}'
  )"
  [[ -n "$remote_mean" ]] || fail "cannot read remote mean volume"

  awk -v value="$remote_mean" 'BEGIN { exit(value > -60.0 ? 0 : 1) }' \
    || fail "remote system audio is too quiet in live probe: ${remote_mean} dB"
}

assert_static_capture_contract
assert_silent_pipeline_gate

if [[ "${MURMURMARK_RUN_LIVE_CAPTURE_TEST:-0}" == "1" ]]; then
  run_live_capture_probe
  echo "capture regression check ok (static + live)"
else
  echo "capture regression check ok (static)"
fi
