#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

fail() {
  echo "committed PCM sidecar smoke failed: $*" >&2
  exit 1
}

command -v jq >/dev/null 2>&1 || fail "missing jq"

bin="${MURMURMARK_BIN:-$repo_root/.build/debug/murmurmark}"
if [[ ! -x "$bin" ]]; then
  swift build >/dev/null
fi

doctor_output="$("$bin" doctor 2>/dev/null || true)"
if grep -q 'shareable displays: 0' <<<"$doctor_output"; then
  if [[ "${MURMURMARK_RUN_LIVE_CAPTURE_TEST:-0}" == "1" ]]; then
    echo "$doctor_output" >&2
    fail "no shareable display found"
  fi
  echo "committed PCM sidecar smoke skipped: no shareable display found"
  exit 0
fi

workdir="$(mktemp -d "${TMPDIR:-/tmp}/murmurmark-committed-pcm-sidecar.XXXXXX")"
trap 'rm -rf "$workdir"' EXIT

session="$workdir/session"
record_log="$workdir/record.log"

"$bin" record \
  --target-bundle system \
  --duration 12 \
  --experiment live-shadow-v1 \
  --live-no-worker \
  --live-segment-sec 5 \
  --live-overlap-sec 2.4 \
  --out "$session" >"$record_log" 2>&1 || {
    cat "$record_log" >&2
    fail "record command failed"
  }

[[ -s "$session/session.json" ]] || fail "session.json was not created"
[[ -s "$session/derived/live/segments.jsonl" ]] || fail "segments.jsonl was not created"
[[ -s "$session/derived/experiments/live-shadow-v1/state.json" ]] || fail "experiment state was not created"
[[ -s "$session/derived/experiments/live-shadow-v1/raw_segment_commits.jsonl" ]] || fail "raw commit log was not created"

jq -e '
  (.health.partial // false) == false
  and (.health.tracks.mic.duration_sec // 0) >= 11.0
  and (.health.tracks.remote.duration_sec // 0) >= 11.0
  and (.health.tracks.mic.frames // 0) > 0
  and (.health.tracks.remote.frames // 0) > 0
' "$session/session.json" >/dev/null || {
  cat "$record_log" >&2
  jq '.health' "$session/session.json" >&2
  fail "raw capture did not survive committed PCM sidecar"
}

jq -e '
  .live_preview_mode == "committed_pcm_queue_v1"
  and .status == "completed"
  and .answers.raw_capture_affected == false
  and .answers.sidecar_disabled == false
  and .counters.max_pending_pcm_packets == 100000
  and .counters.max_pending_pcm_seconds == 30
  and (.counters.max_observed_pending_pcm_seconds // 999) < 30
' "$session/derived/experiments/live-shadow-v1/state.json" >/dev/null \
  || {
    jq '.' "$session/derived/experiments/live-shadow-v1/state.json" >&2
    fail "experiment state does not describe committed PCM preview"
  }

jq -s -e '
  length >= 4
  and all(.[]; .schema == "murmurmark.live_segment/v1")
  and all(.[]; (.path | startswith("derived/experiments/live-shadow-v1/audio/")))
  and any(.[]; .source == "mic")
  and any(.[]; .source == "remote")
' "$session/derived/live/segments.jsonl" >/dev/null \
  || fail "live segment rows do not point to committed PCM experiment audio"

grep -q '"writer_mode":"committed_pcm_queue_v1"' "$session/events.jsonl" \
  || fail "experiment prepare event does not record committed PCM mode"

backpressure_session="$workdir/session-backpressure"
backpressure_log="$workdir/record-backpressure.log"

MURMURMARK_LIVE_PCM_MAX_PENDING_PACKETS=1 \
MURMURMARK_LIVE_PCM_WRITE_DELAY_MS=250 \
  "$bin" record \
    --target-bundle system \
    --duration 4 \
    --experiment live-shadow-v1 \
    --live-no-worker \
    --live-segment-sec 2 \
    --live-overlap-sec 0.5 \
    --out "$backpressure_session" >"$backpressure_log" 2>&1 || {
      cat "$backpressure_log" >&2
      fail "backpressure record command failed"
    }

jq -e '
  (.health.partial // false) == false
  and (.health.tracks.mic.duration_sec // 0) >= 3.0
  and (.health.tracks.remote.duration_sec // 0) >= 3.0
' "$backpressure_session/session.json" >/dev/null || {
  cat "$backpressure_log" >&2
  jq '.health' "$backpressure_session/session.json" >&2
  fail "raw capture did not survive committed PCM backpressure"
}

jq -e '
  .status == "disabled_backpressure"
  and .live_preview_mode == "committed_pcm_queue_v1"
  and .answers.raw_capture_affected == false
  and .answers.sidecar_disabled == true
  and .answers.backpressure_detected == true
' "$backpressure_session/derived/experiments/live-shadow-v1/state.json" >/dev/null \
  || fail "backpressure state does not prove committed PCM fail-open behavior"

duration_backpressure_session="$workdir/session-duration-backpressure"
duration_backpressure_log="$workdir/record-duration-backpressure.log"

MURMURMARK_LIVE_PCM_MAX_PENDING_PACKETS=100000 \
MURMURMARK_LIVE_PCM_MAX_PENDING_SECONDS=0.1 \
MURMURMARK_LIVE_PCM_WRITE_DELAY_MS=250 \
  "$bin" record \
    --target-bundle system \
    --duration 4 \
    --experiment live-shadow-v1 \
    --live-no-worker \
    --live-segment-sec 2 \
    --live-overlap-sec 0.5 \
    --out "$duration_backpressure_session" >"$duration_backpressure_log" 2>&1 || {
      cat "$duration_backpressure_log" >&2
      fail "duration-backpressure record command failed"
    }

jq -e '
  (.health.partial // false) == false
  and (.health.tracks.mic.duration_sec // 0) >= 3.0
  and (.health.tracks.remote.duration_sec // 0) >= 3.0
' "$duration_backpressure_session/session.json" >/dev/null || {
  cat "$duration_backpressure_log" >&2
  jq '.health' "$duration_backpressure_session/session.json" >&2
  fail "raw capture did not survive duration-based committed PCM backpressure"
}

jq -e '
  .status == "disabled_backpressure"
  and .live_preview_mode == "committed_pcm_queue_v1"
  and .answers.raw_capture_affected == false
  and .answers.sidecar_disabled == true
  and .answers.backpressure_detected == true
  and .counters.max_pending_pcm_seconds == 0.1
  and (.reason | contains("exceeded 0.1s"))
' "$duration_backpressure_session/derived/experiments/live-shadow-v1/state.json" >/dev/null \
  || fail "duration-backpressure state does not prove duration-based fail-open behavior"

if [[ "${MURMURMARK_RUN_LIVE_WORKER_CAPTURE_TEST:-0}" == "1" ]]; then
  worker_session="$workdir/session-worker"
  worker_log="$workdir/record-worker.log"
  "$bin" record \
    --target-bundle system \
    --duration 35 \
    --experiment live-shadow-v1 \
    --live-segment-sec 10 \
    --live-overlap-sec 2 \
    --out "$worker_session" >"$worker_log" 2>&1 &
  worker_record_pid=$!
  draft_seen_before_stop=0
  for _ in {1..240}; do
    if [[ -s "$worker_session/derived/live/chunks.jsonl" && ! -e "$worker_session/session.json" ]]; then
      draft_seen_before_stop=1
      break
    fi
    kill -0 "$worker_record_pid" 2>/dev/null || break
    sleep 0.25
  done
  wait "$worker_record_pid" || {
    cat "$worker_log" >&2
    fail "worker-enabled record command failed"
  }
  [[ "$draft_seen_before_stop" == "1" ]] \
    || fail "worker-enabled capture produced no chunk before recording stop"
  grep -q '^\[live\] inline preview started; batch remains authoritative$' "$worker_log" \
    || fail "worker-enabled capture did not start the inline delta preview"
  grep -q '"type":"live_preview.console_started"' "$worker_session/events.jsonl" \
    || fail "worker-enabled capture did not record inline preview provenance"
  jq -e '
    .status == "completed"
    and .provenance == "recording_time_committed_pcm"
    and (.progress.chunks_processed // 0) > 0
    and (.progress.processed_sec // 0) >= 30
  ' "$worker_session/derived/live/live_pipeline_report.json" >/dev/null \
    || fail "worker-enabled capture did not complete realtime draft processing"
fi

echo "committed PCM sidecar smoke ok"
