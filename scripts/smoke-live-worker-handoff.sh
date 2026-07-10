#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

python_bin="${MURMURMARK_PYTHON:-$repo_root/.venv/bin/python}"
[[ -x "$python_bin" ]] || python_bin="$(command -v python3)"

fail() {
  echo "live worker handoff smoke failed: $*" >&2
  exit 1
}

workdir="$(mktemp -d "${TMPDIR:-/tmp}/murmurmark-live-worker-handoff.XXXXXX")"
cleanup() {
  if [[ "${MURMURMARK_KEEP_SMOKE_WORKDIR:-0}" == "1" ]]; then
    echo "live worker handoff smoke workdir: $workdir" >&2
    return
  fi
  rm -rf "$workdir"
}
trap cleanup EXIT

make_fake_whisper() {
  local path="$1"
  cat >"$path" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
output=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --output-file) output="$2"; shift 2 ;;
    *) shift ;;
  esac
done
if [[ "$0" == *slow* ]]; then sleep 5; fi
[[ -n "$output" ]]
if [[ "$output" == *mic ]]; then text="локальная тестовая реплика"; else text="удаленная тестовая реплика"; fi
printf '%s\n' "$text" >"${output}.txt"
printf '{"transcription":[{"text":" %s","offsets":{"from":0,"to":1800},"tokens":[{"p":0.95}]}]}\n' "$text" >"${output}.json"
EOF
  chmod +x "$path"
}

append_segment_pair() {
  local session="$1"
  local created_at="$2"
  cat >>"$session/derived/live/segments.jsonl" <<EOF
{"schema":"murmurmark.live_segment/v1","source":"mic","index":1,"path":"derived/experiments/live-shadow-v1/audio/mic/000001.wav","start_sec":0.0,"end_sec":2.0,"duration_sec":2.0,"clip_start_sec":0.0,"clip_end_sec":2.0,"clip_duration_sec":2.0,"overlap_before_sec":0.0,"overlap_after_sec":0.0,"frames":32000,"clip_frames":32000,"sample_rate":16000,"closed":true,"final":false,"after_overlap_complete":true,"created_at":"$created_at","provenance":"recording_time_committed_pcm"}
{"schema":"murmurmark.live_segment/v1","source":"remote","index":1,"path":"derived/experiments/live-shadow-v1/audio/remote/000001.wav","start_sec":0.0,"end_sec":2.0,"duration_sec":2.0,"clip_start_sec":0.0,"clip_end_sec":2.0,"clip_duration_sec":2.0,"overlap_before_sec":0.0,"overlap_after_sec":0.0,"frames":32000,"clip_frames":32000,"sample_rate":16000,"closed":true,"final":false,"after_overlap_complete":true,"created_at":"$created_at","provenance":"recording_time_committed_pcm"}
EOF
}

append_lag_segment_pairs() {
  local session="$1"
  local created_at="$2"
  cat >>"$session/derived/live/segments.jsonl" <<EOF
{"schema":"murmurmark.live_segment/v1","source":"mic","index":1,"path":"derived/experiments/live-shadow-v1/audio/mic/000001.wav","start_sec":0.0,"end_sec":2.0,"duration_sec":2.0,"clip_start_sec":0.0,"clip_end_sec":2.0,"clip_duration_sec":2.0,"overlap_before_sec":0.0,"overlap_after_sec":0.0,"frames":32000,"clip_frames":32000,"sample_rate":16000,"closed":true,"final":false,"after_overlap_complete":true,"created_at":"$created_at","provenance":"recording_time_committed_pcm"}
{"schema":"murmurmark.live_segment/v1","source":"remote","index":1,"path":"derived/experiments/live-shadow-v1/audio/remote/000001.wav","start_sec":0.0,"end_sec":2.0,"duration_sec":2.0,"clip_start_sec":0.0,"clip_end_sec":2.0,"clip_duration_sec":2.0,"overlap_before_sec":0.0,"overlap_after_sec":0.0,"frames":32000,"clip_frames":32000,"sample_rate":16000,"closed":true,"final":false,"after_overlap_complete":true,"created_at":"$created_at","provenance":"recording_time_committed_pcm"}
{"schema":"murmurmark.live_segment/v1","source":"mic","index":2,"path":"derived/experiments/live-shadow-v1/audio/mic/000001.wav","start_sec":120.0,"end_sec":122.0,"duration_sec":2.0,"clip_start_sec":120.0,"clip_end_sec":122.0,"clip_duration_sec":2.0,"overlap_before_sec":0.0,"overlap_after_sec":0.0,"frames":32000,"clip_frames":32000,"sample_rate":16000,"closed":true,"final":false,"after_overlap_complete":true,"created_at":"$created_at","provenance":"recording_time_committed_pcm"}
{"schema":"murmurmark.live_segment/v1","source":"remote","index":2,"path":"derived/experiments/live-shadow-v1/audio/remote/000001.wav","start_sec":120.0,"end_sec":122.0,"duration_sec":2.0,"clip_start_sec":120.0,"clip_end_sec":122.0,"clip_duration_sec":2.0,"overlap_before_sec":0.0,"overlap_after_sec":0.0,"frames":32000,"clip_frames":32000,"sample_rate":16000,"closed":true,"final":false,"after_overlap_complete":true,"created_at":"$created_at","provenance":"recording_time_committed_pcm"}
EOF
}

make_session() {
  local session="$1"
  mkdir -p \
    "$session/derived/live" \
    "$session/derived/experiments/live-shadow-v1/audio/mic" \
    "$session/derived/experiments/live-shadow-v1/audio/remote"
  : >"$session/derived/live/segments.jsonl"
  ffmpeg -hide_banner -loglevel error -y -f lavfi -i "sine=frequency=440:duration=2" -ar 16000 -ac 1 \
    "$session/derived/experiments/live-shadow-v1/audio/mic/000001.wav"
  ffmpeg -hide_banner -loglevel error -y -f lavfi -i "sine=frequency=660:duration=2" -ar 16000 -ac 1 \
    "$session/derived/experiments/live-shadow-v1/audio/remote/000001.wav"
  : >"$session/fake-model.bin"
}

wait_for() {
  local timeout_sec="$1"
  shift
  local deadline=$((SECONDS + timeout_sec))
  until "$@"; do
    (( SECONDS < deadline )) || return 1
    sleep 0.1
  done
}

wait_for_process_exit() {
  local pid="$1"
  local deadline=$((SECONDS + 5))
  while kill -0 "$pid" 2>/dev/null; do
    (( SECONDS < deadline )) || return 1
    sleep 0.1
  done
}

session="$workdir/session"
make_session "$session"
make_fake_whisper "$workdir/fake-whisper"

"$python_bin" -u scripts/live-pipeline-shadow.py "$session" \
  --model "$session/fake-model.bin" \
  --whisper-cli "$workdir/fake-whisper" \
  --poll-sec 0.1 \
  --idle-after-session-json-sec 0.2 \
  --commit-delay-sec 0 \
  --heartbeat-sec 0.1 \
  --ffmpeg-timeout-sec 5 \
  --whisper-timeout-sec 5 \
  --no-causal-target-me >"$workdir/worker.log" 2>&1 &
worker_pid=$!

wait_for 5 test -s "$session/derived/live/live_pipeline_state.json" \
  || fail "worker heartbeat did not appear before segments"
append_segment_pair "$session" "2026-07-10T10:00:02Z"
wait_for 10 grep -q "удаленная тестовая реплика" "$session/derived/live/transcript.draft.md" \
  || { cat "$workdir/worker.log" >&2; fail "draft did not appear before session stop"; }
wait_for 10 grep -q "удаленная тестовая реплика" "$session/derived/live/transcript.preview.md" \
  || { cat "$workdir/worker.log" >&2; fail "conservative preview did not appear before session stop"; }
jq -s -e 'any(.[]; .chunk_count > 0 and .provenance == "recording_time_committed_pcm")' \
  "$session/derived/live/preview_snapshots.jsonl" >/dev/null \
  || fail "pre-stop preview snapshot provenance is missing"
[[ ! -e "$session/session.json" ]] || fail "fixture stopped before proving pre-stop draft"
jq -e '
  .status == "running"
  and (
    .current_stage == "base_chunk_written"
    or .current_stage == "target_me_written"
    or .current_stage == "waiting_segments"
  )
  and .provenance == "recording_time_committed_pcm"
  and (.heartbeat_at | length > 0)
  and (.progress.live_lag_sec // 0) >= 0
' "$session/derived/live/live_pipeline_state.json" >/dev/null \
  || fail "worker state lacks realtime heartbeat provenance"

cat >"$session/session.json" <<'JSON'
{"schema":"murmurmark.session/v1","session_id":"live-worker-handoff","status":"completed","created_at":"2026-07-10T10:00:00Z","ended_at":"2026-07-10T10:00:10Z","health":{"summary":"ok","partial":false,"actual_duration_sec":10.0}}
JSON
wait_for_process_exit "$worker_pid" || fail "worker did not exit after session completion"
wait "$worker_pid" || fail "worker exited non-zero"
jq -e '.status == "completed" and .current_stage == "completed"' \
  "$session/derived/live/live_pipeline_state.json" >/dev/null \
  || fail "worker did not persist terminal state"
jq -e '
  .outputs.preview_transcript == "derived/live/transcript.preview.md"
  and .outputs.preview_snapshots == "derived/live/preview_snapshots.jsonl"
' \
  "$session/derived/live/live_pipeline_report.json" >/dev/null \
  || fail "live report does not expose the conservative preview"
"$python_bin" scripts/watch-live-draft.py "$session" --poll-sec 0.1 >"$workdir/watch.log"
grep -q 'удаленная тестовая реплика' "$workdir/watch.log" \
  || fail "live watch did not print the draft"
grep -q 'status=completed' "$workdir/watch.log" \
  || fail "live watch did not print terminal worker state"

timeout_session="$workdir/session-timeout"
make_session "$timeout_session"
make_fake_whisper "$workdir/fake-whisper-slow"
append_segment_pair "$timeout_session" "2026-07-10T10:01:02Z"
cat >"$timeout_session/session.json" <<'JSON'
{"schema":"murmurmark.session/v1","session_id":"live-worker-timeout","status":"completed","created_at":"2026-07-10T10:01:00Z","ended_at":"2026-07-10T10:01:10Z","health":{"summary":"ok","partial":false,"actual_duration_sec":10.0}}
JSON
"$python_bin" -u scripts/live-pipeline-shadow.py "$timeout_session" \
  --model "$timeout_session/fake-model.bin" \
  --whisper-cli "$workdir/fake-whisper-slow" \
  --poll-sec 0.1 \
  --idle-after-session-json-sec 0.2 \
  --heartbeat-sec 0.1 \
  --ffmpeg-timeout-sec 5 \
  --whisper-timeout-sec 0.5 \
  --no-causal-target-me >"$workdir/worker-timeout.log" 2>&1

jq -e '
  (.mic.asr.status == "timed_out")
  and (.remote.asr.status == "timed_out")
' "$timeout_session/derived/live/chunks/000001/chunk.json" >/dev/null \
  || fail "bounded Whisper timeout was not recorded"
jq -e '.status == "completed"' "$timeout_session/derived/live/live_pipeline_state.json" >/dev/null \
  || fail "worker did not fail open after child timeout"

lag_session="$workdir/session-lag-budget"
make_session "$lag_session"
append_lag_segment_pairs "$lag_session" "2026-07-10T10:01:32Z"
cat >"$lag_session/session.json" <<'JSON'
{"schema":"murmurmark.session/v1","session_id":"live-worker-lag-budget","status":"completed","created_at":"2026-07-10T10:01:30Z","ended_at":"2026-07-10T10:01:40Z","health":{"summary":"ok","partial":false,"actual_duration_sec":10.0}}
JSON
"$python_bin" -u scripts/live-pipeline-shadow.py "$lag_session" \
  --model "$lag_session/fake-model.bin" \
  --whisper-cli "$workdir/fake-whisper" \
  --poll-sec 0.1 \
  --idle-after-session-json-sec 0.2 \
  --heartbeat-sec 0.1 \
  --ffmpeg-timeout-sec 5 \
  --whisper-timeout-sec 5 \
  --causal-target-me-timeout-sec 5 \
  --causal-target-me-max-live-lag-sec 1 >"$workdir/worker-lag-budget.log" 2>&1

jq -e '
  .mic.causal_target_me_shadow.status == "skipped_lag_budget"
  and .mic.causal_target_me_shadow.reason == "live_lag_budget_exceeded"
  and .runtime.base_elapsed_sec >= 0
' "$lag_session/derived/live/chunks/000001/chunk.json" >/dev/null \
  || { cat "$workdir/worker-lag-budget.log" >&2; fail "lag budget did not preserve the base chunk and skip Target-Me"; }
jq -e '
  .causal_target_me_shadow.skipped_lag_budget_count >= 1
  and .runtime_cost.base_chunk.count == 2
' "$lag_session/derived/live/live_pipeline_report.json" >/dev/null \
  || fail "lag budget telemetry is missing from the live report"
grep -q 'удаленная тестовая реплика' "$lag_session/derived/live/transcript.draft.md" \
  || fail "lag budget removed the base live draft"
grep -q 'удаленная тестовая реплика' "$lag_session/derived/live/transcript.preview.md" \
  || fail "lag budget removed the conservative base preview"

shutdown_session="$workdir/session-shutdown"
make_session "$shutdown_session"
append_segment_pair "$shutdown_session" "2026-07-10T10:02:02Z"
"$python_bin" -u scripts/live-pipeline-shadow.py "$shutdown_session" \
  --model "$shutdown_session/fake-model.bin" \
  --whisper-cli "$workdir/fake-whisper-slow" \
  --poll-sec 0.1 \
  --heartbeat-sec 0.1 \
  --ffmpeg-timeout-sec 5 \
  --whisper-timeout-sec 30 \
  --no-causal-target-me >"$workdir/worker-shutdown.log" 2>&1 &
shutdown_worker_pid=$!
wait_for 10 jq -e '
  .current_stage == "asr_mic"
  and (.child_pid // 0) > 0
  and (.progress.live_lag_sec // -1) >= 0
' \
  "$shutdown_session/derived/live/live_pipeline_state.json" >/dev/null 2>&1 \
  || fail "shutdown fixture did not reach child ASR"
shutdown_child_pid="$(jq -r '.child_pid' "$shutdown_session/derived/live/live_pipeline_state.json")"
kill -TERM "$shutdown_worker_pid"
wait_for_process_exit "$shutdown_worker_pid" || fail "worker ignored SIGTERM"
wait "$shutdown_worker_pid" || fail "worker SIGTERM path exited non-zero"
kill -0 "$shutdown_child_pid" 2>/dev/null \
  && fail "worker SIGTERM left the child ASR process running"
jq -e '.status == "completed_partial_draft" and .current_stage == "terminated"' \
  "$shutdown_session/derived/live/live_pipeline_state.json" >/dev/null \
  || fail "worker SIGTERM did not persist terminal partial state"

echo "live worker handoff smoke ok"
