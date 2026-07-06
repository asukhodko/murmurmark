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

file_bytes() {
  wc -c <"$1" | tr -d '[:space:]'
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

  grep -q 'beginActivity' "$source_file" \
    || fail "recording must hold a ProcessInfo activity while ScreenCaptureKit is active"

  grep -q 'idleDisplaySleepDisabled' "$source_file" \
    || fail "recording must prevent display sleep during ScreenCaptureKit capture"

  grep -q 'MURMURMARK_ENABLE_UNSAFE_LIVE_PIPELINE' "$source_file" \
    || fail "unsafe live pipeline must be gated away from normal recording"
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

write_fixture_audio() {
  local kind="$1"
  local out="$2"
  case "$kind" in
    silence)
      ffmpeg -y -hide_banner -loglevel error -f lavfi -i anullsrc=r=48000:cl=mono -t 1 -c:a pcm_s16le "$out"
      ;;
    mic|remote|tone)
      ffmpeg -y -hide_banner -loglevel error -f lavfi -i sine=frequency=880:duration=1:sample_rate=48000 -c:a pcm_s16le "$out"
      ;;
    *)
      fail "unknown fixture audio kind: $kind"
      ;;
  esac
}

write_capture_fixture_session() {
  local session="$1"
  local mic_kind="$2"
  local remote_kind="$3"
  local status="$4"
  local partial="$5"
  local stop_reason="$6"
  local warning_mode="$7"

  mkdir -p "$session/audio/mic" "$session/audio/remote"
  write_fixture_audio "$mic_kind" "$session/audio/mic/000001.caf"
  write_fixture_audio "$remote_kind" "$session/audio/remote/000001.caf"

  local mic_bytes remote_bytes
  mic_bytes="$(file_bytes "$session/audio/mic/000001.caf")"
  remote_bytes="$(file_bytes "$session/audio/remote/000001.caf")"

  local warnings
  case "$warning_mode" in
    none)
      warnings='[]'
      ;;
    mic_silent)
      warnings='["mic track appears silent or almost silent (RMS -inf dB)"]'
      ;;
    remote_silent)
      warnings='["remote track appears silent or almost silent (RMS -inf dB)"]'
      ;;
    both_silent)
      warnings='["mic track appears silent or almost silent (RMS -inf dB)", "remote track appears silent or almost silent (RMS -inf dB)"]'
      ;;
    interrupted)
      warnings='["capture produced no audio samples for 10s; restarting ScreenCaptureKit stream"]'
      ;;
    *)
      fail "unknown warning mode: $warning_mode"
      ;;
  esac

  jq -n \
    --arg status "$status" \
    --arg stop_reason "$stop_reason" \
    --argjson partial "$partial" \
    --argjson warnings "$warnings" \
    --argjson mic_bytes "$mic_bytes" \
    --argjson remote_bytes "$remote_bytes" \
    '{
      schema: "murmurmark.session/v1",
      session_id: "capture-health-fixture",
      created_at: "2026-07-06T08:00:00.000Z",
      ended_at: "2026-07-06T08:01:00.000Z",
      app_version: "0.1.0",
      capture_mode: "screencapturekit_system",
      status: $status,
      target: {
        kind: "system_audio",
        bundle_id: null,
        display_name: "Capture Health Fixture",
        pid_strategy: "fixture"
      },
      microphone: {
        device_uid: "fixture",
        display_name: "Fixture Microphone",
        capture_backend: "fixture"
      },
      privacy: {
        network_allowed_during_capture: false,
        telemetry: false,
        raw_audio_retention: "local_only"
      },
      health: {
        summary: (if $partial then "partial" else "ok" end),
        actual_duration_sec: 60,
        requested_duration_sec: null,
        stop_reason: $stop_reason,
        partial: $partial,
        warnings: $warnings
      },
      mic_audio: {backend: "fixture", sample_rate: 48000, channels: 1, format: "caf:lpcm"},
      remote_audio: {backend: "fixture", sample_rate: 48000, channels: 1, format: "caf:lpcm"},
      files: {
        mic: [{
          path: "audio/mic/000001.caf",
          start_host_time_ns: 0,
          start_session_sec: 0,
          sample_rate: 48000,
          frames: 48000,
          channels: 1,
          bytes: $mic_bytes,
          sha256: null
        }],
        remote: [{
          path: "audio/remote/000001.caf",
          start_host_time_ns: 0,
          start_session_sec: 0,
          sample_rate: 48000,
          frames: 48000,
          channels: 1,
          bytes: $remote_bytes,
          sha256: null
        }]
      }
    }' >"$session/session.json"
}

assert_capture_health_matrix() {
  require_tool ffmpeg
  require_tool jq

  local bin="${MURMURMARK_BIN:-$repo_root/.build/debug/murmurmark}"
  if [[ ! -x "$bin" ]]; then
    swift build >/dev/null
  fi

  local workdir
  workdir="$(mktemp -d "${TMPDIR:-/tmp}/murmurmark-capture-health.XXXXXX")"
  trap 'rm -rf "$workdir"' RETURN
  local fake_model="$workdir/fake-model.bin"
  : >"$fake_model"

  local name
  for name in mic-only remote-only mic-and-remote; do
    local session="$workdir/$name"
    case "$name" in
      mic-only)
        write_capture_fixture_session "$session" tone silence completed false sigint remote_silent
        ;;
      remote-only)
        write_capture_fixture_session "$session" silence tone completed false sigint mic_silent
        ;;
      mic-and-remote)
        write_capture_fixture_session "$session" tone tone completed false sigint none
        ;;
    esac
    python3 "$repo_root/scripts/run-session-pipeline.py" "$session" \
      --murmurmark-bin "$bin" \
      --model "$fake_model" \
      --skip-build \
      --skip-preprocess \
      --skip-transcription \
      --skip-audits \
      --skip-cleanup >"$workdir/$name.log" 2>&1 || {
        cat "$workdir/$name.log" >&2
        fail "$name fixture should pass the early capture gate"
      }
    jq -e '.status == "passed"' "$session/derived/pipeline-run/pipeline_run_report.json" >/dev/null \
      || fail "$name fixture did not write a passed pipeline report"
  done

  local silent="$workdir/silence"
  write_capture_fixture_session "$silent" silence silence completed_with_warnings false sigint both_silent
  set +e
  python3 "$repo_root/scripts/run-session-pipeline.py" "$silent" \
    --murmurmark-bin "$bin" \
    --model "$fake_model" \
    --skip-build \
    --skip-preprocess \
    --skip-transcription \
    --skip-audits \
    --skip-cleanup >"$workdir/silence.log" 2>&1
  local status=$?
  set -e
  [[ "$status" -eq 2 ]] || {
    cat "$workdir/silence.log" >&2
    fail "silence fixture should be blocked before ASR"
  }
  jq -e '.status == "blocked" and .blocker == "silent_capture"' \
    "$silent/derived/pipeline-run/pipeline_run_report.json" >/dev/null \
    || fail "silence fixture did not write silent_capture blocker"

  local interrupted="$workdir/interrupted"
  write_capture_fixture_session "$interrupted" tone tone partial true capture_stalled interrupted
  set +e
  python3 "$repo_root/scripts/run-session-pipeline.py" "$interrupted" \
    --murmurmark-bin "$bin" \
    --model "$fake_model" \
    --skip-build \
    --skip-preprocess \
    --skip-transcription \
    --skip-audits \
    --skip-cleanup >"$workdir/interrupted.log" 2>&1
  status=$?
  set -e
  [[ "$status" -eq 2 ]] || {
    cat "$workdir/interrupted.log" >&2
    fail "interrupted fixture should be blocked before ASR"
  }
  jq -e '.status == "blocked" and .blocker == "interrupted_capture"' \
    "$interrupted/derived/pipeline-run/pipeline_run_report.json" >/dev/null \
    || fail "interrupted fixture did not write interrupted_capture blocker"

  local sparse="$workdir/sparse"
  write_capture_fixture_session "$sparse" tone tone completed_with_warnings false sigint none
  jq '.health.actual_duration_sec = 600 | .health.summary = "warning" | .health.warnings = ["ScreenCaptureKit stream restarted after capture_stalled"]' \
    "$sparse/session.json" >"$sparse/session.json.tmp"
  mv "$sparse/session.json.tmp" "$sparse/session.json"
  set +e
  python3 "$repo_root/scripts/run-session-pipeline.py" "$sparse" \
    --murmurmark-bin "$bin" \
    --model "$fake_model" \
    --skip-build \
    --skip-preprocess \
    --skip-transcription \
    --skip-audits \
    --skip-cleanup >"$workdir/sparse.log" 2>&1
  status=$?
  set -e
  [[ "$status" -eq 2 ]] || {
    cat "$workdir/sparse.log" >&2
    fail "sparse fixture should be blocked before ASR"
  }
  jq -e '.status == "blocked" and .blocker == "sparse_capture"' \
    "$sparse/derived/pipeline-run/pipeline_run_report.json" >/dev/null \
    || fail "sparse fixture did not write sparse_capture blocker"
}

assert_live_pipeline_guard() {
  local bin="${MURMURMARK_BIN:-$repo_root/.build/debug/murmurmark}"
  if [[ ! -x "$bin" ]]; then
    swift build >/dev/null
  fi

  local workdir
  workdir="$(mktemp -d "${TMPDIR:-/tmp}/murmurmark-live-guard.XXXXXX")"
  trap 'rm -rf "$workdir"' RETURN

  set +e
  "$bin" record --target-bundle system --duration 1 --live-pipeline --out "$workdir/session" >"$workdir/live-guard.log" 2>&1
  local status=$?
  set -e
  [[ "$status" -ne 0 ]] || {
    cat "$workdir/live-guard.log" >&2
    fail "live pipeline should be disabled by default"
  }
  grep -q -- '--live-pipeline is disabled for real recordings' "$workdir/live-guard.log" \
    || {
      cat "$workdir/live-guard.log" >&2
      fail "live pipeline guard did not explain the safe command"
    }
  [[ ! -e "$workdir/session/session.json" ]] \
    || fail "disabled live pipeline should fail before creating a session manifest"
}

run_system_audio_capture_probe() {
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
    --out "$session" >"$record_log" 2>&1
  wait

  [[ -s "$session/session.json" ]] || {
    cat "$record_log" >&2
    fail "system-audio capture probe did not create session.json"
  }

  jq -e '.health.summary == "ok" and (.health.screen_capture_restart_count // 0) == 0' \
    "$session/session.json" >/dev/null || {
      cat "$record_log" >&2
      jq '.health' "$session/session.json" >&2
      fail "system-audio capture probe health is not ok"
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
    || fail "remote system audio is too quiet in capture probe: ${remote_mean} dB"
}

assert_static_capture_contract
assert_silent_pipeline_gate
assert_capture_health_matrix
assert_live_pipeline_guard

if [[ "${MURMURMARK_RUN_LIVE_CAPTURE_TEST:-0}" == "1" ]]; then
  run_system_audio_capture_probe
  echo "capture regression check ok (static + system-audio probe)"
else
  echo "capture regression check ok (static)"
fi
