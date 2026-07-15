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
session="$workdir/2026-07-15_10-00-00-live"
live="$session/derived/live"
experiment="$session/derived/experiments/live-shadow-v1"
recovery="$live/causal-me-recovery-runtime-v1"
mkdir -p "$live" "$experiment" "$recovery"

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
cat >"$experiment/state.json" <<'JSON'
{"schema":"murmurmark.experiment_state/v1","status":"completed","batch_authoritative":true,"promotion_allowed":false}
JSON
cat >"$recovery/worker_state.json" <<'JSON'
{
  "schema":"murmurmark.live_causal_me_recovery_worker/v1",
  "generator":{"name":"live-causal-me-recovery-manager","version":"1.1.0"},
  "status":"completed",
  "failed_invocations":0,
  "timed_out_invocations":0,
  "final_live_lag_sec":0.0,
  "last_completed_chunk":4,
  "last_completed_chunk_end_sec":120.0,
  "final_drain_invocations":1,
  "final_drain_completed_invocations":1,
  "last_incremental_runtime":{"schema":"murmurmark.live_recovery_incremental_runtime/v1"}
}
JSON
cat >"$recovery/state.json" <<'JSON'
{"schema":"murmurmark.live_causal_me_recovery_runtime/v1","status":"completed","accepted_candidate_count":1}
JSON
cat >"$recovery/runtime_runs.jsonl" <<'JSONL'
{"schema":"murmurmark.live_causal_me_recovery_runtime/v1","status":"completed","accepted_candidate_count":1,"completed_before_stop":true,"pre_stop_provenance":"recording_time_worker","recording_active_at_submit":true}
JSONL
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
cat >"$live/transcript.preview.md" <<'MARKDOWN'
# Live Preview Transcript

## 00:00 Colleagues

Проверка live evidence.
MARKDOWN
cat >"$live/preview_snapshots.jsonl" <<'JSONL'
{"schema":"murmurmark.live_preview_snapshot/v1","created_at":"2026-07-10T10:00:36Z","provenance":"recording_time_committed_pcm","preview_policy":"live_runtime_causal_target_me_remote_energy_v1","chunk_count":1,"processed_end_sec":30.0,"content_sha256":"fixture","batch_authoritative":true,"promotion_allowed":false}
JSONL
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
    "live_preview_snapshot_count":1,
    "live_preview_snapshot_created_at_count":1,
    "live_pre_stop_preview_snapshot_count":1,
    "live_post_stop_preview_snapshot_count":0,
    "live_preview_snapshot_provenance":["recording_time_committed_pcm"],
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

"$python_bin" scripts/report-live-session-evidence.py \
  "$session" \
  --strict \
  --require-causal-recovery \
  --max-recovery-final-lag-sec 0 >/dev/null
jq -e '
  .transport_evidence_passed == true
  and .metrics.causal_recovery.worker_status == "completed"
  and .metrics.causal_recovery.final_live_lag_sec == 0
  and .metrics.causal_recovery.pre_stop_candidate_run_count == 1
  and .metrics.causal_recovery.recording_time_run_count == 1
  and all(.checks[] | select(.id | startswith("causal_recovery")); .status == "passed")
' "$live/live_session_evidence.json" >/dev/null \
  || fail "passing fixture did not produce strict causal recovery evidence"

recovery_report="$workdir/recovery-report"
"$python_bin" scripts/report-live-recovery-real-evidence.py \
  "$session" \
  --out-dir "$recovery_report" \
  --min-sessions 1 \
  --max-recovery-final-lag-sec 0 \
  --strict >/dev/null
jq -e '
  .status == "passed"
  and .summary.passing_session_count == 1
  and .sessions[0].manager_version == "1.1.0"
' "$recovery_report/live_recovery_real_evidence_v1.json" >/dev/null \
  || fail "fresh recovery evidence aggregator did not accept the passing fixture"

jq '.final_live_lag_sec = 30.0' \
  "$recovery/worker_state.json" >"$recovery/worker_state.json.tmp"
mv "$recovery/worker_state.json.tmp" "$recovery/worker_state.json"
if "$python_bin" scripts/report-live-session-evidence.py \
  "$session" \
  --strict \
  --require-causal-recovery \
  --max-recovery-final-lag-sec 0 >/dev/null 2>&1; then
  fail "strict causal recovery evidence accepted non-zero final lag"
fi
jq -e '
  .transport_evidence_passed == false
  and any(.checks[]; .id == "causal_recovery_zero_final_lag" and .status == "failed")
' "$live/live_session_evidence.json" >/dev/null \
  || fail "non-zero recovery lag was not exposed"
if "$python_bin" scripts/report-live-recovery-real-evidence.py \
  "$session" \
  --out-dir "$recovery_report" \
  --min-sessions 1 \
  --max-recovery-final-lag-sec 0 \
  --strict >/dev/null 2>&1; then
  fail "fresh recovery evidence aggregator accepted non-zero final lag"
fi
jq -e '
  .status == "collecting_real_evidence"
  and .summary.passing_session_count == 0
  and (.sessions[0].failed_required_checks | index("causal_recovery_zero_final_lag")) != null
' "$recovery_report/live_recovery_real_evidence_v1.json" >/dev/null \
  || fail "fresh recovery evidence aggregator did not explain the failed fixture"
jq '.final_live_lag_sec = 0.0' \
  "$recovery/worker_state.json" >"$recovery/worker_state.json.tmp"
mv "$recovery/worker_state.json.tmp" "$recovery/worker_state.json"

jq '. + {termination_reason:"finalization_wait_timeout"}' \
  "$live/live_pipeline_state.json" >"$live/live_pipeline_state.json.tmp"
mv "$live/live_pipeline_state.json.tmp" "$live/live_pipeline_state.json"
jq -c '. + {start_sec:0.0,end_sec:120.0}' "$live/segments.jsonl" \
  >"$live/segments.jsonl.tmp"
mv "$live/segments.jsonl.tmp" "$live/segments.jsonl"
"$python_bin" scripts/report-live-session-evidence.py "$session" >/dev/null
jq -e '
  .status == "incomplete"
  and .metrics.final_live_lag_sec == 90.0
  and .metrics.report_final_live_lag_sec == 0.0
  and any(.checks[];
    .id == "worker_terminal"
    and .evidence.effective_worker_status == "completed_partial_draft"
  )
  and any(.checks[]; .id == "bounded_final_lag" and .status == "failed")
' "$live/live_session_evidence.json" >/dev/null \
  || fail "row-derived final lag did not override a stale completed report"

jq '
  .temporal_provenance.status = "post_stop_live_replay_only"
  | .temporal_provenance.live_pre_stop_chunk_count = 0
  | .temporal_provenance.live_post_stop_chunk_count = 1
  | .temporal_provenance.live_pre_stop_preview_snapshot_count = 0
  | .temporal_provenance.live_post_stop_preview_snapshot_count = 1
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
