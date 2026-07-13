#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

fail() {
  echo "live replay lab smoke failed: $*" >&2
  exit 1
}

workdir="$(mktemp -d "${TMPDIR:-/tmp}/murmurmark-live-replay.XXXXXX")"
trap 'rm -rf "$workdir"' EXIT
session="$workdir/session"
live="$session/derived/live"
mkdir -p "$live" "$session/derived/pipeline-run"

cat >"$session/session.json" <<'JSON'
{"schema":"murmurmark.session/v1","status":"completed","health":{"summary":"ok"}}
JSON
cat >"$live/chunks.jsonl" <<'JSONL'
{"schema":"murmurmark.live_chunk/v1","index":1,"start_sec":0.0,"end_sec":30.0,"duration_sec":30.0,"clip_start_sec":0.0,"clip_end_sec":35.0}
{"schema":"murmurmark.live_chunk/v1","index":2,"start_sec":30.0,"end_sec":60.0,"duration_sec":30.0,"clip_start_sec":25.0,"clip_end_sec":65.0}
JSONL
cat >"$live/live_asr_cache_report.json" <<'JSON'
{
  "schema":"murmurmark.live_asr_cache_report/v1",
  "status":"not_eligible",
  "materialized":false,
  "reasons":["window_duration_mismatch:1"],
  "parameters":{"asr_window_sec":60,"asr_overlap_sec":5}
}
JSON
cat >"$session/derived/pipeline-run/pipeline_run_report.json" <<'JSON'
{
  "schema":"murmurmark.session_pipeline_run/v1",
  "steps":[
    {"name":"transcribe_current","duration_sec":600},
    {"name":"transcribe_shadow_v2","duration_sec":300},
    {"name":"audit_stronger_audio_judge","duration_sec":120}
  ]
}
JSON
cat >"$live/live_batch_comparison.json" <<'JSON'
{
  "schema":"murmurmark.live_batch_comparison/v1",
  "metrics":{
    "live_missing_me_utterance_count":10,
    "live_missing_me_seconds":100.0,
    "live_missing_me_visible_in_suppressed_mic_seconds":90.0,
    "live_missing_me_not_visible_in_suppressed_mic_seconds":10.0,
    "live_suspected_remote_leak_in_me_count":2,
    "live_suspected_remote_leak_in_me_seconds":20.0,
    "live_blocking_contentful_role_constrained_order_mismatch_count":0,
    "live_batch_token_f1":0.80,
    "all_parity_gates_passed":false
  },
  "parity_gates":{"status":"not_promotable","gates":[{"name":"local_recall","status":"warning"}]},
  "shadow_profiles":{"target_me":{
    "online_live_me_remote_overlap_filter_v1":{
      "status":"not_promotable","live_implementable":true,
      "metrics":{
        "live_missing_me_utterance_count":10,"live_missing_me_seconds":100.0,
        "live_missing_me_visible_in_suppressed_mic_seconds":90.0,
        "live_missing_me_not_visible_in_suppressed_mic_seconds":10.0,
        "live_suspected_remote_leak_in_me_count":2,"live_suspected_remote_leak_in_me_seconds":20.0,
        "live_blocking_contentful_role_constrained_order_mismatch_count":0,
        "live_batch_token_f1":0.80,"all_parity_gates_passed":false
      },
      "parity_gates":{"gates":[{"name":"local_recall","status":"warning"}]}
    },
    "safe_candidate":{
      "status":"not_promotable","live_implementable":true,
      "metrics":{
        "live_missing_me_utterance_count":6,"live_missing_me_seconds":60.0,
        "live_missing_me_visible_in_suppressed_mic_seconds":50.0,
        "live_missing_me_not_visible_in_suppressed_mic_seconds":10.0,
        "live_suspected_remote_leak_in_me_count":2,"live_suspected_remote_leak_in_me_seconds":20.0,
        "live_blocking_contentful_role_constrained_order_mismatch_count":0,
        "live_batch_token_f1":0.81,"all_parity_gates_passed":false
      },
      "parity_gates":{"gates":[{"name":"local_recall","status":"warning"}]}
    },
    "risky_candidate":{
      "status":"not_promotable","live_implementable":true,
      "metrics":{
        "live_missing_me_utterance_count":4,"live_missing_me_seconds":40.0,
        "live_missing_me_visible_in_suppressed_mic_seconds":30.0,
        "live_missing_me_not_visible_in_suppressed_mic_seconds":10.0,
        "live_suspected_remote_leak_in_me_count":2,"live_suspected_remote_leak_in_me_seconds":20.0,
        "live_blocking_contentful_role_constrained_order_mismatch_count":1,
        "live_batch_token_f1":0.82,"all_parity_gates_passed":false
      },
      "parity_gates":{"gates":[{"name":"order_risk","status":"warning"}]}
    }
  }}
}
JSON

.build/debug/murmurmark live replay "$session" >/dev/null
report="$live/replay-lab/live_replay_matrix.json"
[[ -f "$report" ]] || fail "report was not created"
jq -e '
  .schema == "murmurmark.live_replay_lab/v1"
  and .recommendation.policy == "safe_candidate"
  and .recommendation.promotion_allowed == false
  and .raw_audio_modified == false
  and .batch_authoritative == true
  and .asr_cache.live_geometry_candidate.compatible == true
  and .asr_cache.batch_default_geometry.compatible == false
  and .pipeline_timings.batch_transcription_sec == 900
  and any(.profiles[]; .policy == "risky_candidate" and .safe_improvement_vs_baseline == false)
' "$report" >/dev/null || fail "unexpected replay decision"

echo "live replay lab smoke ok"
