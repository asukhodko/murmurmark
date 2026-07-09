#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

fail() {
  echo "experimental sidecar contract smoke failed: $*" >&2
  exit 1
}

command -v jq >/dev/null 2>&1 || fail "missing jq"

bin="${MURMURMARK_BIN:-$repo_root/.build/debug/murmurmark}"
if [[ ! -x "$bin" ]]; then
  swift build >/dev/null
fi

workdir="$(mktemp -d "${TMPDIR:-/tmp}/murmurmark-sidecar-contract.XXXXXX")"
trap 'rm -rf "$workdir"' EXIT

write_session() {
  local session="$1"
  local warnings_json="$2"
  mkdir -p "$session/audio/mic" "$session/audio/remote" "$session/derived/live"
  printf 'mic\n' >"$session/audio/mic/000001.caf"
  printf 'remote\n' >"$session/audio/remote/000001.caf"
  cat >"$session/session.json" <<JSON
{
  "schema": "murmurmark.session/v1",
  "session_id": "$(basename "$session")",
  "created_at": "2026-07-07T09:00:00Z",
  "ended_at": "2026-07-07T09:01:00Z",
  "status": "completed_with_warnings",
  "health": {
    "summary": "warning",
    "partial": false,
    "actual_duration_sec": 60,
    "screen_capture_restart_count": 0,
    "warnings": $warnings_json,
    "tracks": {
      "mic": {"duration_sec": 60, "frames": 2880000, "bytes": 4, "sample_rate": 48000, "empty": false},
      "remote": {"duration_sec": 60, "frames": 2880000, "bytes": 7, "sample_rate": 48000, "empty": false}
    }
  }
}
JSON
  cat >"$session/events.jsonl" <<'JSONL'
{"t":"2026-07-07T09:00:00Z","type":"capture.prepare","live_pipeline":true}
{"t":"2026-07-07T09:00:01Z","type":"live_pipeline.prepare","callback_policy":"raw_write_then_nonblocking_live_enqueue"}
{"t":"2026-07-07T09:01:00Z","type":"capture.stopped","explicit_stop":true}
JSONL
  cat >"$session/derived/live/segments.jsonl" <<'JSONL'
{"schema":"murmurmark.live_segment/v1","source":"mic","index":1,"path":"derived/live/audio/mic/000001.caf","start_sec":0,"end_sec":30,"duration_sec":30}
{"schema":"murmurmark.live_segment/v1","source":"remote","index":1,"path":"derived/live/audio/remote/000001.caf","start_sec":0,"end_sec":30,"duration_sec":30}
JSONL
  cat >"$session/derived/live/chunks.jsonl" <<'JSONL'
{"schema":"murmurmark.live_chunk/v1","index":1,"start_sec":0,"end_sec":30,"duration_sec":30}
JSONL
  cat >"$session/derived/live/live_pipeline_report.json" <<'JSON'
{
  "schema": "murmurmark.live_pipeline_report/v1",
  "status": "completed",
  "batch_authoritative": true,
  "promotion_allowed": false,
  "progress": {
    "captured_sec": 30,
    "preprocessed_sec": 30,
    "asr_sec": 30,
    "processed_sec": 30,
    "chunks_processed": 1,
    "segments_seen": 2
  }
}
JSON
}

session="$workdir/session-ok"
write_session "$session" "[]"

python3 scripts/experiment-sidecar-contract.py refresh "$session" >/dev/null
"$bin" experiment status "$session" >/dev/null
"$bin" experiment report "$session" >/dev/null

manifest="$session/derived/experiments/live-shadow-v1/experiment_manifest.json"
state="$session/derived/experiments/live-shadow-v1/state.json"
report="$session/derived/experiments/live-shadow-v1/report.json"
events="$session/derived/experiments/live-shadow-v1/events.jsonl"

jq -e '
  .schema == "murmurmark.experimental_sidecar_manifest/v1"
  and .experiment_id == "live-shadow-v1"
  and .batch_authoritative == true
  and .promotion_allowed == false
  and .raw_capture_affected == false
  and (.recovery_command | startswith("murmurmark process "))
  and (.comparison_command | startswith("murmurmark experiment compare "))
  and (.comparison_command | endswith(" --experiment live-shadow-v1"))
' "$manifest" >/dev/null || fail "manifest contract is invalid"

jq -e '
  .schema == "murmurmark.experimental_sidecar_state/v1"
  and .live_preview_mode == "committed_pcm_queue_v1"
  and .answers.experiment_started == true
  and .answers.raw_seconds_recorded == 60
  and .answers.sidecar_seconds_captured == 30
  and .answers.sidecar_seconds_asr == 30
  and .answers.raw_capture_affected == false
  and .answers.batch_reproducible_from_raw == true
' "$state" >/dev/null || fail "state machine answers are invalid"

jq -e '
  .schema == "murmurmark.experimental_sidecar_report/v1"
  and .machine_answers.batch_reproducible_from_raw == true
' "$report" >/dev/null || fail "report contract is invalid"

grep -q '"type": "experiment_contract.refreshed"' "$events" \
  || fail "experiment events were not written"

backpressure_session="$workdir/session-backpressure"
write_session "$backpressure_session" '["live segment writer disabled for remote: backlog exceeded 1 samples"]'
python3 scripts/experiment-sidecar-contract.py refresh "$backpressure_session" >/dev/null
jq -e '
  .status == "disabled_backpressure"
  and .disabled_reason == "sidecar_backpressure"
  and .raw_capture_affected == false
' "$backpressure_session/derived/experiments/live-shadow-v1/experiment_manifest.json" >/dev/null \
  || fail "backpressure fixture did not fail open"
jq -e '
  .answers.backpressure_detected == true
  and .answers.sidecar_disabled == true
  and .answers.batch_reproducible_from_raw == true
' "$backpressure_session/derived/experiments/live-shadow-v1/state.json" >/dev/null \
  || fail "backpressure state did not preserve raw/batch answers"

echo "experimental sidecar contract smoke ok"
