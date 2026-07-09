#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

fail() {
  echo "raw sidecar worker smoke failed: $*" >&2
  exit 1
}

command -v ffmpeg >/dev/null 2>&1 || fail "missing ffmpeg"
command -v jq >/dev/null 2>&1 || fail "missing jq"

python_bin="${MURMURMARK_PYTHON:-$repo_root/.venv/bin/python}"
if [[ ! -x "$python_bin" ]]; then
  python_bin="python3"
fi

workdir="$(mktemp -d "${TMPDIR:-/tmp}/murmurmark-raw-sidecar.XXXXXX")"
trap 'rm -rf "$workdir"' EXIT

session="$workdir/session"
experiment="live-shadow-v1"
mkdir -p "$session/audio/mic" "$session/audio/remote" "$session/derived/experiments/$experiment"

ffmpeg -hide_banner -loglevel error -y \
  -f lavfi -i "sine=frequency=440:duration=2" \
  -ar 48000 -ac 1 -c:a pcm_s16le "$session/audio/mic/000001.caf"
ffmpeg -hide_banner -loglevel error -y \
  -f lavfi -i "sine=frequency=660:duration=2" \
  -ar 48000 -ac 1 -c:a pcm_s16le "$session/audio/remote/000001.caf"

mic_bytes="$(stat -f%z "$session/audio/mic/000001.caf")"
remote_bytes="$(stat -f%z "$session/audio/remote/000001.caf")"
jq -n \
  --argjson mic_bytes "$mic_bytes" \
  --argjson remote_bytes "$remote_bytes" \
  '{
    schema: "murmurmark.session/v1",
    session_id: "raw-sidecar-worker-smoke",
    created_at: "2026-07-08T09:00:00Z",
    ended_at: "2026-07-08T09:00:02Z",
    status: "completed",
    health: {
      summary: "ok",
      partial: false,
      actual_duration_sec: 2,
      warnings: [],
      tracks: {
        mic: {duration_sec: 2, frames: 96000, bytes: $mic_bytes, sample_rate: 48000, empty: false},
        remote: {duration_sec: 2, frames: 96000, bytes: $remote_bytes, sample_rate: 48000, empty: false}
      }
    }
  }' >"$session/session.json"

cat >"$session/derived/experiments/$experiment/raw_segment_commits.jsonl" <<'JSONL'
{"schema":"murmurmark.raw_segment_commit/v1","experiment_id":"live-shadow-v1","source":"mic","index":1,"start_sec":0.0,"end_sec":2.0,"duration_sec":2.0,"raw_path":"audio/mic/000001.caf","frames_committed":96000,"total_frames_committed":96000,"sample_rate":48000,"status":"committed","final":true}
{"schema":"murmurmark.raw_segment_commit/v1","experiment_id":"live-shadow-v1","source":"remote","index":1,"start_sec":0.0,"end_sec":2.0,"duration_sec":2.0,"raw_path":"audio/remote/000001.caf","frames_committed":96000,"total_frames_committed":96000,"sample_rate":48000,"status":"committed","final":true}
JSONL

"$python_bin" scripts/raw-sidecar-worker.py "$session" \
  --experiment "$experiment" \
  --no-live-worker \
  --poll-sec 0.1 \
  --idle-after-session-json-sec 0.1 >/dev/null

[[ -s "$session/derived/experiments/$experiment/audio/mic/000001.wav" ]] \
  || fail "mic segment was not materialized"
[[ -s "$session/derived/experiments/$experiment/audio/remote/000001.wav" ]] \
  || fail "remote segment was not materialized"

jq -e '
  .schema == "murmurmark.experimental_sidecar_state/v1"
  and .status == "completed"
  and .answers.raw_capture_affected == false
  and .answers.batch_reproducible_from_raw == true
  and .counters.processed_indexes == [1]
' "$session/derived/experiments/$experiment/state.json" >/dev/null \
  || fail "state did not prove completed fail-open sidecar"

jq -e '
  .schema == "murmurmark.experimental_sidecar_report/v1"
  and .status == "completed"
  and .batch_authoritative == true
  and .promotion_allowed == false
' "$session/derived/experiments/$experiment/report.json" >/dev/null \
  || fail "report contract is invalid"

jq -s -e '
  length == 2
  and all(.[]; .schema == "murmurmark.live_segment/v1")
  and all(.[]; .materialized_from_raw_commit == true)
  and all(.[]; (.path | startswith("derived/experiments/live-shadow-v1/audio/")))
' "$session/derived/live/segments.jsonl" >/dev/null \
  || fail "compat live segments do not point to canonical experiment audio"

python3 scripts/experiment-sidecar-contract.py refresh "$session" >/dev/null
jq -e '
  .outputs.raw_segment_commits == "derived/experiments/live-shadow-v1/raw_segment_commits.jsonl"
  and .counters.raw_commits_seen == 2
  and .answers.sidecar_seconds_captured == 2
' "$session/derived/experiments/$experiment/state.json" >/dev/null \
  || fail "contract did not include raw commit evidence"

backlog_session="$workdir/session-backlog"
mkdir -p "$backlog_session/audio/mic" "$backlog_session/audio/remote" "$backlog_session/derived/experiments/$experiment"
cp "$session/audio/mic/000001.caf" "$backlog_session/audio/mic/000001.caf"
cp "$session/audio/remote/000001.caf" "$backlog_session/audio/remote/000001.caf"
cp "$session/session.json" "$backlog_session/session.json"
jq '.status = "recording" | del(.ended_at)' "$backlog_session/session.json" >"$backlog_session/session.json.tmp"
mv "$backlog_session/session.json.tmp" "$backlog_session/session.json"
cat >"$backlog_session/derived/experiments/$experiment/raw_segment_commits.jsonl" <<'JSONL'
{"schema":"murmurmark.raw_segment_commit/v1","experiment_id":"live-shadow-v1","source":"mic","index":1,"start_sec":0.0,"end_sec":1.0,"duration_sec":1.0,"raw_path":"audio/mic/000001.caf","frames_committed":48000,"total_frames_committed":48000,"sample_rate":48000,"status":"committed","final":false}
{"schema":"murmurmark.raw_segment_commit/v1","experiment_id":"live-shadow-v1","source":"remote","index":1,"start_sec":0.0,"end_sec":1.0,"duration_sec":1.0,"raw_path":"audio/remote/000001.caf","frames_committed":48000,"total_frames_committed":48000,"sample_rate":48000,"status":"committed","final":false}
{"schema":"murmurmark.raw_segment_commit/v1","experiment_id":"live-shadow-v1","source":"mic","index":2,"start_sec":1.0,"end_sec":2.0,"duration_sec":1.0,"raw_path":"audio/mic/000001.caf","frames_committed":48000,"total_frames_committed":96000,"sample_rate":48000,"status":"committed","final":true}
{"schema":"murmurmark.raw_segment_commit/v1","experiment_id":"live-shadow-v1","source":"remote","index":2,"start_sec":1.0,"end_sec":2.0,"duration_sec":1.0,"raw_path":"audio/remote/000001.caf","frames_committed":48000,"total_frames_committed":96000,"sample_rate":48000,"status":"committed","final":true}
JSONL

"$python_bin" scripts/raw-sidecar-worker.py "$backlog_session" \
  --experiment "$experiment" \
  --allow-open-raw-read \
  --no-live-worker \
  --poll-sec 0.1 \
  --max-ready-backlog 1 >/dev/null

jq -e '
  .status == "disabled_backpressure"
  and .answers.backpressure_detected == true
  and .answers.sidecar_disabled == true
  and .answers.raw_capture_affected == false
  and .answers.batch_reproducible_from_raw == true
' "$backlog_session/derived/experiments/$experiment/state.json" >/dev/null \
  || fail "ready backlog did not disable only the sidecar"

echo "raw sidecar worker smoke ok"
