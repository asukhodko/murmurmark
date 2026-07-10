#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

python_bin="${MURMURMARK_PYTHON:-$repo_root/.venv/bin/python}"
[[ -x "$python_bin" ]] || python_bin="$(command -v python3)"

fail() {
  echo "live session evidence smoke failed: $*" >&2
  exit 1
}

workdir="$(mktemp -d "${TMPDIR:-/tmp}/murmurmark-live-session-evidence.XXXXXX")"
trap 'rm -rf "$workdir"' EXIT
session="$workdir/session"
live="$session/derived/live"
mkdir -p "$live"

cat >"$session/session.json" <<'JSON'
{
  "schema": "murmurmark.session/v1",
  "status": "completed",
  "created_at": "2026-07-10T10:00:00Z",
  "ended_at": "2026-07-10T10:02:00Z",
  "health": {"summary": "ok", "partial": false, "actual_duration_sec": 120.0}
}
JSON
cat >"$live/live_pipeline_state.json" <<'JSON'
{"schema":"murmurmark.live_pipeline_state/v1","status":"completed","current_stage":"completed","provenance":"recording_time_committed_pcm"}
JSON
cat >"$live/live_pipeline_report.json" <<'JSON'
{
  "schema":"murmurmark.live_pipeline_report/v1",
  "status":"completed",
  "batch_authoritative":true,
  "progress":{"captured_sec":120.0,"processed_sec":120.0,"live_lag_sec":0.0,"chunks_processed":1}
}
JSON
cat >"$live/chunks.jsonl" <<'JSONL'
{"schema":"murmurmark.live_chunk/v1","index":1,"start_sec":0.0,"end_sec":30.0,"created_at":"2026-07-10T10:00:35Z","provenance":"recording_time_committed_pcm"}
JSONL
cat >"$live/segments.jsonl" <<'JSONL'
{"schema":"murmurmark.live_segment/v1","source":"mic","index":1,"path":"derived/experiments/live-shadow-v1/audio/mic/000001.caf","created_at":"2026-07-10T10:00:30Z","provenance":"recording_time_committed_pcm"}
{"schema":"murmurmark.live_segment/v1","source":"remote","index":1,"path":"derived/experiments/live-shadow-v1/audio/remote/000001.caf","created_at":"2026-07-10T10:00:30Z","provenance":"recording_time_committed_pcm"}
JSONL
cat >"$live/transcript.draft.md" <<'MARKDOWN'
# Live Draft Transcript

## 00:00 Colleagues draft

Проверка live evidence.
MARKDOWN
cat >"$live/live_batch_comparison.json" <<'JSON'
{
  "schema":"murmurmark.live_batch_comparison/v1",
  "batch_authoritative":true,
  "temporal_provenance":{
    "schema":"murmurmark.live_temporal_provenance/v1",
    "status":"pre_stop_live_chunks_no_causal_candidate",
    "live_pre_stop_chunk_count":1,
    "live_post_stop_chunk_count":0,
    "live_chunk_created_at_count":1,
    "first_live_chunk_latency_sec":35.0,
    "last_pre_stop_chunk_lead_sec":85.0
  },
  "parity_gates":{
    "status":"passed",
    "gates":[
      {"name":"capture_safety","status":"passed","reason":"ok"},
      {"name":"raw_batch_authoritative","status":"passed","reason":"ok"},
      {"name":"required_artifacts","status":"passed","reason":"ok"},
      {"name":"pre_stop_live_artifacts","status":"passed","reason":"ok"}
    ]
  },
  "metrics":{
    "batch_authoritative":true,
    "meaningful_live_comparison":true,
    "all_parity_gates_passed":true,
    "live_missing_me_seconds":0.0,
    "live_suspected_remote_leak_in_me_seconds":0.0,
    "live_blocking_contentful_role_constrained_order_mismatch_count":0,
    "adjacent_duplicate_chunk_count":0
  }
}
JSON

"$python_bin" scripts/report-live-session-evidence.py "$session" --strict >/dev/null
jq -e '
  .status == "parity_passed"
  and .transport_evidence_passed == true
  and .meaningful_comparison == true
  and .all_parity_gates_passed == true
  and .promotion_allowed == false
' "$live/live_session_evidence.json" >/dev/null \
  || fail "passing fixture did not produce parity evidence"

jq '
  .temporal_provenance.status = "post_stop_live_replay_only"
  | .temporal_provenance.live_pre_stop_chunk_count = 0
  | .temporal_provenance.live_post_stop_chunk_count = 1
  | .temporal_provenance.first_live_chunk_latency_sec = 130
  | .parity_gates.status = "not_promotable"
  | (.parity_gates.gates[] | select(.name == "pre_stop_live_artifacts") | .status) = "warning"
  | .metrics.all_parity_gates_passed = false
' "$live/live_batch_comparison.json" >"$live/live_batch_comparison.json.tmp"
mv "$live/live_batch_comparison.json.tmp" "$live/live_batch_comparison.json"

"$python_bin" scripts/report-live-session-evidence.py "$session" >/dev/null
jq -e '
  .status == "incomplete"
  and .transport_evidence_passed == false
  and .all_parity_gates_passed == false
  and any(.checks[]; .id == "pre_stop_live_artifacts" and .status == "failed")
' "$live/live_session_evidence.json" >/dev/null \
  || fail "post-stop replay fixture was accepted"

echo "live session evidence smoke ok"
