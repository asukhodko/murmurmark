#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
bin="${MURMURMARK_BIN:-$repo_root/.build/debug/murmurmark}"

require_tool() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "missing required tool: $1" >&2
    exit 1
  fi
}

require_tool ffmpeg
require_tool jq

if [[ ! -x "$bin" ]]; then
  (cd "$repo_root" && swift build >/dev/null)
fi

export MURMURMARK_HOME="$repo_root"

workdir="$(mktemp -d "${TMPDIR:-/tmp}/murmurmark-cli-handoff.XXXXXX")"
trap 'rm -rf "$workdir"' EXIT

write_full_capture_regression_proof() {
  mkdir -p "$workdir/sessions/_reports/capture-regression"
  cat >"$workdir/sessions/_reports/capture-regression/capture_regression_check.json" <<'JSON'
{
  "schema": "murmurmark.capture_regression_check/v1",
  "generated_at": "2026-07-06T00:00:00Z",
  "status": "passed",
  "mode": "static_system_audio_live_fail_open",
  "live_capture_test_enabled": true,
  "capture_safe_proof": {
    "status": "full_fail_open_proof_passed",
    "required_for_real_live_collection": true
  },
  "checks": [
    {"name": "static_capture_contract", "status": "passed", "mode": "static"},
    {"name": "silent_pipeline_gate", "status": "passed", "mode": "fixture"},
    {"name": "capture_health_matrix", "status": "passed", "mode": "fixture"},
    {"name": "live_pipeline_guard", "status": "passed", "mode": "static"},
    {"name": "system_audio_capture_probe", "status": "passed", "mode": "live_probe"},
    {"name": "live_segment_fail_open_probe", "status": "passed", "mode": "live_probe"}
  ]
}
JSON
}

config_path="$workdir/murmurmark.config.json"
"$bin" config init --config "$config_path" >/dev/null
"$bin" config print --config "$config_path" >/dev/null
before_config_hash="$(shasum -a 256 "$config_path" | awk '{print $1}')"
"$bin" config init --config "$config_path" >/dev/null
after_config_hash="$(shasum -a 256 "$config_path" | awk '{print $1}')"
if [[ "$before_config_hash" != "$after_config_hash" ]]; then
  echo "config init unexpectedly changed existing config without --force" >&2
  exit 1
fi
"$bin" config init --config "$config_path" --force >/dev/null
"$bin" config print --config "$config_path" >/dev/null

live_pilot_denied_session="$workdir/live-pilot-denied"
if MURMURMARK_CAPTURE_REGRESSION_REPORT="$workdir/missing-capture-proof.json" \
  "$repo_root/scripts/run-live-parity-pilot.sh" \
    --skip-safety-gate \
    --out "$live_pilot_denied_session" >"$workdir/live-pilot-denied.out" 2>&1; then
  echo "expected live parity pilot to reject new recording without full capture proof" >&2
  exit 1
fi
grep -q 'capture-safe proof is not full_fail_open_proof_passed' "$workdir/live-pilot-denied.out"
[[ ! -e "$live_pilot_denied_session/session.json" ]] || {
  echo "live parity pilot created a session despite missing capture proof" >&2
  exit 1
}

cd "$workdir"

session="$workdir/sessions/cli-handoff"
mkdir -p \
  "$session/audio/mic" \
  "$session/audio/remote" \
  "$session/derived/preprocess/echo" \
  "$session/derived/audit/group-overlaps" \
  "$session/derived/audit/order" \
  "$session/derived/audit/audio-review-pack" \
  "$session/derived/transcript-simple/whisper-cpp/resolved" \
  "$session/derived/transcript-simple/whisper-cpp/audit-cleanup" \
  "$session/derived/synthesis-simple/extractive" \
  "$session/derived/readiness/session-quality" \
  "$session/derived/readiness"

ffmpeg -y -hide_banner -loglevel error \
  -f lavfi -i "anullsrc=r=48000:cl=mono" \
  -t 1 -c:a pcm_s16le "$session/audio/mic/000001.caf"
ffmpeg -y -hide_banner -loglevel error \
  -f lavfi -i "anullsrc=r=48000:cl=mono" \
  -t 1 -c:a pcm_s16le "$session/audio/remote/000001.caf"

mic_bytes="$(stat -f%z "$session/audio/mic/000001.caf")"
remote_bytes="$(stat -f%z "$session/audio/remote/000001.caf")"

jq -n \
  --argjson mic_bytes "$mic_bytes" \
  --argjson remote_bytes "$remote_bytes" \
  '{
    schema: "murmurmark.session/v1",
    session_id: "cli-handoff",
    created_at: "2026-06-22T16:00:00.000Z",
    ended_at: "2026-06-22T16:00:01.000Z",
    app_version: "0.1.0",
    capture_mode: "fixture",
    status: "completed",
    target: {kind: "system_audio", bundle_id: null, display_name: "Fixture", pid_strategy: "fixture"},
    microphone: {device_uid: "default", display_name: "System Default Microphone", capture_backend: "fixture"},
    mic_audio: {backend: "fixture", sample_rate: 48000, channels: 1, format: "caf:lpcm"},
    remote_audio: {backend: "fixture", sample_rate: 48000, channels: 1, format: "caf:lpcm"},
    files: {
      mic: [{path: "audio/mic/000001.caf", bytes: $mic_bytes, frames: 48000}],
      remote: [{path: "audio/remote/000001.caf", bytes: $remote_bytes, frames: 48000}]
    }
  }' >"$session/session.json"

cat >"$session/derived/transcript-simple/whisper-cpp/resolved/transcript.md" <<'EOF'
# Simple Transcript

## 00:00 Me

Готово.
EOF

jq -n '{
  schema: "murmurmark.clean_dialogue/v1",
  session: "cli-handoff",
  utterances: [
    {
      id: "utt_cli_001",
      start: 0.0,
      end: 1.0,
      role: "Me",
      speaker_label: "Me",
      source_track: "mic",
      text: "Готово.",
      quality: {needs_review: false}
    }
  ]
}' >"$session/derived/transcript-simple/whisper-cpp/resolved/clean_dialogue.json"

jq -n '{
  schema: "murmurmark.simple_transcript/v1",
  utterances: [
    {id: "utt_cli_001", start: 0.0, end: 1.0, role: "Me", text: "Готово."}
  ]
}' >"$session/derived/transcript-simple/whisper-cpp/resolved/transcript.simple.json"

jq -n '{
  schema: "murmurmark.simple_transcript_quality/v1",
  utterances: 1,
  needs_review_count: 0,
  cross_role_overlap_gt2_count: 0,
  cross_role_overlap_gt2_seconds: 0,
  remote_duplicate_in_me_seconds: 0,
  unrepaired_long_mic_crossings_count: 0,
  golden_phrase_fail_count: 0,
  local_only_island_recall: 1.0
}' >"$session/derived/transcript-simple/whisper-cpp/resolved/quality_report.json"

cp "$session/derived/transcript-simple/whisper-cpp/resolved/clean_dialogue.json" \
  "$session/derived/transcript-simple/whisper-cpp/resolved/clean_dialogue.shadow_v2.json"
cp "$session/derived/transcript-simple/whisper-cpp/resolved/quality_report.json" \
  "$session/derived/transcript-simple/whisper-cpp/resolved/quality_report.shadow_v2.json"
jq -n '{schema: "murmurmark.repair_comparison/v1", passed: true, gates: {passed: true}}' \
  >"$session/derived/transcript-simple/whisper-cpp/resolved/repair_comparison.json"

cp "$session/derived/transcript-simple/whisper-cpp/resolved/clean_dialogue.json" \
  "$session/derived/transcript-simple/whisper-cpp/resolved/clean_dialogue.audit_cleanup_v1.json"
cp "$session/derived/transcript-simple/whisper-cpp/resolved/quality_report.json" \
  "$session/derived/transcript-simple/whisper-cpp/resolved/quality_report.audit_cleanup_v1.json"
cp "$session/derived/transcript-simple/whisper-cpp/resolved/transcript.md" \
  "$session/derived/transcript-simple/whisper-cpp/resolved/transcript.audit_cleanup_v1.md"
cp "$session/derived/transcript-simple/whisper-cpp/resolved/transcript.simple.json" \
  "$session/derived/transcript-simple/whisper-cpp/resolved/transcript.simple.audit_cleanup_v1.json"

jq -n '{
  schema: "murmurmark.echo.local_fir_report/v1",
  accepted_for_asr: true,
  decision: {accepted_for_asr: true, reason: "fixture"}
}' >"$session/derived/preprocess/echo/local_fir_report.json"

jq -n '{
  schema: "murmurmark.group_overlap_summary/v1",
  profile: "shadow_v2",
  classified: {total_overlap_count: 0, total_overlap_seconds: 0},
  harmful: {seconds: 0},
  benign_or_expected: {seconds: 0},
  review: {count: 0, seconds: 0}
}' >"$session/derived/audit/group-overlaps/group_overlap_summary.json"

jq -n '{
  schema: "murmurmark.transcript_order_audit/v1",
  profile: "shadow_v2",
  summary: {
    audited_overlap_count: 0,
    probable_order_risk_count: 0,
    probable_order_risk_seconds: 0,
    needs_review_count: 0,
    needs_review_seconds: 0
  }
}' >"$session/derived/audit/order/transcript_order_audit.json"
: >"$session/derived/audit/order/transcript_order_items.jsonl"

jq -n '{
  schema: "murmurmark.audit_cleanup_report/v1",
  input_profile: "shadow_v2",
  output_profile: "audit_cleanup_v1",
  summary: {
    applied_patches: 0,
    rejected_patches: 0,
    dropped_me_duplicate_seconds: 0,
    dropped_me_noise_seconds: 0,
    audit_harmful_seconds_after: 0
  },
  gates: {passed: true, warnings: []}
}' >"$session/derived/transcript-simple/whisper-cpp/audit-cleanup/audit_cleanup_report.audit_cleanup_v1.json"

jq -n '{schema: "murmurmark.review_pack_summary/v1", items: 0}' \
  >"$session/derived/audit/audio-review-pack/review_pack_summary.json"
: >"$session/derived/audit/audio-review-pack/review_pack_items.jsonl"
jq -n '{
  schema: "murmurmark.audio_review_summary/v1",
  items: 0,
  probable_error: {count: 0, seconds: 0},
  likely_reliable: {count: 0, seconds: 0},
  needs_stronger_audio_judge: {count: 0, seconds: 0},
  recommended_next_step: "audio_review_clear"
}' >"$session/derived/audit/audio-review-pack/audio_review_summary.json"
: >"$session/derived/audit/audio-review-pack/audio_review_audit.jsonl"

cat >"$session/derived/synthesis-simple/extractive/notes.md" <<'EOF'
# Extractive Notes

- [utt_cli_001] Готово.
EOF

cat >"$session/derived/synthesis-simple/extractive/quality_verdict.md" <<'EOF'
# Quality Verdict

Verdict: good.
EOF

jq -n '{
  schema: "murmurmark.quality_verdict/v1",
  verdict: "good",
  selected_transcript_profile: "current",
  metrics: {},
  review_summary: {review_item_count: 0, review_item_seconds: 0, by_type: {}},
  risk_items: []
}' >"$session/derived/synthesis-simple/extractive/quality_verdict.json"

cat >"$session/derived/synthesis-simple/extractive/notes.audit_cleanup_v1.md" <<'EOF'
# Extractive Notes

- [utt_cli_001] Готово.
EOF

cat >"$session/derived/synthesis-simple/extractive/quality_verdict.audit_cleanup_v1.md" <<'EOF'
# Quality Verdict

Verdict: good.
EOF

jq -n '{
  schema: "murmurmark.quality_verdict/v1",
  verdict: "good",
  selected_transcript_profile: "audit_cleanup_v1",
  metrics: {},
  review_summary: {review_item_count: 0, review_item_seconds: 0, by_type: {}},
  risk_items: []
}' >"$session/derived/synthesis-simple/extractive/quality_verdict.audit_cleanup_v1.json"

jq -n '{
  schema: "murmurmark.evidence_notes/v2",
  source: {selected_transcript_profile: "current"},
  selected: {},
  review: {items: []},
  metrics: {review_item_count: 0}
}' >"$session/derived/synthesis-simple/extractive/evidence_notes.json"
cp "$session/derived/synthesis-simple/extractive/evidence_notes.json" \
  "$session/derived/synthesis-simple/extractive/evidence_notes.audit_cleanup_v1.json"
: >"$session/derived/synthesis-simple/extractive/review_items.jsonl"
: >"$session/derived/synthesis-simple/extractive/review_items.audit_cleanup_v1.jsonl"

cat >"$session/derived/readiness/session_readiness.md" <<'EOF'
# Session Readiness

ready_for_notes
EOF

jq -n --arg session "$session" '{
  schema: "murmurmark.session_readiness/v1",
  use_gate: "ready_for_notes",
  pipeline_status: "complete",
  selected_profile: "current",
  verdict: "good",
  export_blockers: [],
  review_blockers: [],
  warnings: [],
  metrics: {
    meeting_duration_sec: 1,
    review_burden_sec: 0,
    review_burden_ratio: 0,
    notes_review_burden_sec: 0,
    notes_review_burden_ratio: 0,
    transcript_review_burden_sec: 0,
    transcript_review_burden_ratio: 0,
    audit_harmful_seconds_after: 0,
    audio_review_probable_error_seconds: 0,
    local_only_island_recall: 1.0,
    local_recall_missing_island_count: 0,
    local_recall_possible_lost_me_seconds: 0,
    transcript_order_review_seconds: 0,
    transcript_order_probable_order_risk_count: 0,
    needs_review_count: 0,
    notes_needs_review_count: 0
  },
  next_commands: [
    {id: "finish", label: "Create final handoff.", command: ("murmurmark finish " + $session)}
  ],
  open_commands: [
    {id: "open_notes", label: "Read notes.", command: ("less " + $session + "/derived/synthesis-simple/extractive/notes.md")}
  ],
  outputs: {
    clean_dialogue: {exists: true, path: "derived/transcript-simple/whisper-cpp/resolved/clean_dialogue.json"},
    quality_report: {exists: true, path: "derived/transcript-simple/whisper-cpp/resolved/quality_report.json"},
    notes: {exists: true, path: "derived/synthesis-simple/extractive/notes.md"},
    transcript: {exists: true, path: "derived/transcript-simple/whisper-cpp/resolved/transcript.md"},
    quality_verdict: {exists: true, path: "derived/synthesis-simple/extractive/quality_verdict.md"},
    evidence_notes: {exists: true, path: "derived/synthesis-simple/extractive/evidence_notes.json"},
    review_items: {exists: true, path: "derived/synthesis-simple/extractive/review_items.jsonl"}
  }
}' >"$session/derived/readiness/session_readiness.json"

jq -n '{
  schema: "murmurmark.session_quality_report/v1",
  summary: {session_count: 1, complete_pipeline_count: 1, total_duration_min: 0.02},
  sessions: [
    {
      session_id: "cli-handoff",
      session: "sessions/cli-handoff",
      pipeline_status: "complete",
      use_gate: "ready_for_notes",
      risk_flags: [],
      selected_profile: "current",
      verdict: "good"
    }
  ]
}' >"$session/derived/readiness/session-quality/session_quality_report.json"

review_plan="$session/derived/readiness/review-plan"
review_template="$review_plan/review_decisions.template.jsonl"
review_decisions="$review_plan/review_decisions.jsonl"
lane_dir="$review_plan/lane-packs"
mkdir -p "$lane_dir"
cat >"$review_template" <<EOF
{"schema":"murmurmark.review_decision/v1","status":"todo","decision":"todo","session_id":"cli-handoff","session":"$session","input_profile":"current","source":"fixture","source_audit_id":"cli_review_001","label":"lost_me","verdict":"needs_review","review_lane":"check_unique_me_content","review_action":"check_unique_me_content","suggested_decision":"keep_me","suggested_decision_confidence":"medium","suggested_decision_reason":"fixture review row","allowed_decisions":["keep_me","needs_review","skip"],"me_utterance_ids":["utt_cli_001"],"utterance_ids":["utt_cli_001"],"interval":{"start":0.0,"end":1.0,"duration_sec":1.0},"text":[{"id":"utt_cli_001","role":"Me","source_track":"mic","text":"Готово."}],"commands":{"mic_raw":"afplay -ss 0 -t 1 \"$session/audio/mic/000001.caf\""}}
EOF

"$repo_root/scripts/build-review-lane-pack.py" \
  --template "$review_template" \
  --decisions "$review_decisions" \
  --lane check_unique_me_content \
  --out-dir "$lane_dir" >/dev/null

"$repo_root/scripts/build-review-workspace.py" \
  --template "$review_template" \
  --decisions "$review_decisions" \
  --out-dir "$review_plan" >/dev/null

lane_manifest="$lane_dir/review_lane_pack.check_unique_me_content.json"
lane_answers="$lane_dir/review_lane_answers.check_unique_me_content.txt"

workspace_apply_output="$("$bin" review workspace apply \
  --workspace "$review_plan/review_workspace.json" \
  --template "$review_template" \
  --out "$review_decisions" \
  --report "$review_plan/review_workspace_apply_report.json" \
  --dry-run)"
echo "$workspace_apply_output" | grep -q '^review_workspace_apply:$'
echo "$workspace_apply_output" | grep -q '^  recommended_next: \$EDITOR '
echo "$workspace_apply_output" | grep -q '^  open:$'
tail -1 <<<"$workspace_apply_output" | grep -q '^next: \$EDITOR '

lane_apply_todo_output="$("$bin" review lane apply check_unique_me_content \
  --manifest "$lane_manifest" \
  --template "$review_template" \
  --plan-out-dir "$review_plan" \
  --decisions-out "$review_decisions" \
  --answers-file "$lane_answers" \
  --dry-run)"
echo "$lane_apply_todo_output" | grep -q '^review_lane_apply:$'
echo "$lane_apply_todo_output" | grep -q '^  recommended_next: \$EDITOR '
tail -1 <<<"$lane_apply_todo_output" | grep -q '^next: \$EDITOR '

sed 's/^answers=.*/answers=k/' "$lane_answers" >"$lane_answers.tmp"
mv "$lane_answers.tmp" "$lane_answers"

lane_apply_ready_output="$("$bin" review lane apply check_unique_me_content \
  --manifest "$lane_manifest" \
  --template "$review_template" \
  --plan-out-dir "$review_plan" \
  --decisions-out "$review_decisions" \
  --answers-file "$lane_answers" \
  --dry-run)"
echo "$lane_apply_ready_output" | grep -q '^  recommended_next: murmurmark review lane apply '
tail -1 <<<"$lane_apply_ready_output" | grep -q '^next: murmurmark review lane apply '

lane_apply_output="$("$bin" review lane apply check_unique_me_content \
  --manifest "$lane_manifest" \
  --template "$review_template" \
  --plan-out-dir "$review_plan" \
  --decisions-out "$review_decisions" \
  --answers-file "$lane_answers")"
echo "$lane_apply_output" | grep -q '^  progress: '
tail -1 <<<"$lane_apply_output" | grep -q '^next: murmurmark review apply'

process_plan_output="$("$bin" process "$session" --plan-only --skip-build --skip-preprocess --skip-transcription --skip-audits --skip-cleanup)"
echo "$process_plan_output" | grep -q '^SESSION="'
echo "$process_plan_output" | grep -q '^pipeline_plan:$'
echo "$process_plan_output" | grep -q '^  mode: plan_only$'
echo "$process_plan_output" | grep -q '^  run_command: murmurmark process '
echo "$process_plan_output" | grep -q '^pipeline_run:$'
echo "$process_plan_output" | grep -q '^  status: planned$'
echo "$process_plan_output" | grep -q '^  recommended_next: murmurmark process '
tail -1 <<<"$process_plan_output" | grep -q '^next: murmurmark process '

jq -e '
  .schema == "murmurmark.session_pipeline_run/v1"
  and .status == "planned"
  and .plan.mode == "plan_only"
  and ([.next_commands[].id] | index("run_process"))
  and ([.open_commands[].id] | index("open_pipeline_run_report"))
' "$session/derived/pipeline-run/pipeline_plan_report.json" >/dev/null

if [[ -e "$session/derived/pipeline-run/pipeline_run_report.json" ]]; then
  echo "plan-only unexpectedly wrote pipeline_run_report.json" >&2
  exit 1
fi

status_output="$("$bin" status "$session")"
echo "$status_output" | grep -q '^readiness:$'
echo "$status_output" | grep -q '^  status: exportable$'
echo "$status_output" | grep -q '^  use:$'
echo "$status_output" | grep -q '^    summary: ready to read and export$'
echo "$status_output" | grep -q '^    can_read_notes: true$'
echo "$status_output" | grep -q '^    can_export: true$'
echo "$status_output" | grep -q '^    minimum_step: murmurmark finish '
tail -1 <<<"$status_output" | grep -q '^next: murmurmark finish '

outcome_output="$("$bin" outcome "$session")"
echo "$outcome_output" | grep -q '^outcome:$'
echo "$outcome_output" | grep -q '^  status: ready_for_notes$'
echo "$outcome_output" | grep -q '^  summary:$'
echo "$outcome_output" | grep -q '^    can_read_notes: true$'
echo "$outcome_output" | grep -q '^    can_export: true$'
echo "$outcome_output" | grep -q 'notes.md$'
echo "$outcome_output" | grep -q 'transcript.md$'
echo "$outcome_output" | grep -q 'quality_verdict.md$'

report_output="$("$bin" report "$session")"
echo "$report_output" | grep -q '^readiness:$'
echo "$report_output" | grep -q '^  status: exportable$'
echo "$report_output" | grep -q '^  use:$'
echo "$report_output" | grep -q '^    summary: ready to read and export$'
echo "$report_output" | grep -q '^    can_export: true$'
tail -1 <<<"$report_output" | grep -q '^next: murmurmark finish '

next_output="$("$bin" next "$session")"
echo "$next_output" | grep -q '^next:$'
echo "$next_output" | grep -q '^  command: murmurmark finish '

review_session="$workdir/sessions/review-handoff"
mkdir -p \
  "$review_session/audio" \
  "$review_session/derived/readiness/review-plan" \
  "$review_session/derived/synthesis-simple/extractive" \
  "$review_session/derived/transcript-simple/whisper-cpp/resolved"
cp -R "$session/audio/mic" "$review_session/audio/mic"
cp -R "$session/audio/remote" "$review_session/audio/remote"
cp "$session/session.json" "$review_session/session.json"
cat >"$review_session/derived/readiness/session_readiness.json" <<EOF
{
  "schema": "murmurmark.session_readiness/v1",
  "use_gate": "review_first",
  "pipeline_status": "complete",
  "selected_profile": "audit_cleanup_v2",
  "verdict": "usable_with_review",
  "risk_flags": ["transcript_order_risk"],
  "export_blockers": ["review_burden_requires_review"],
  "review_blockers": ["review_burden_requires_review"],
  "metrics": {
    "meeting_duration_sec": 1200,
    "review_burden_sec": 60,
    "review_burden_ratio": 0.05,
    "transcript_order_review_seconds": 60,
    "transcript_order_probable_order_risk_count": 0,
    "audit_harmful_seconds_after": 0,
    "local_only_island_recall": 1.0
  },
  "outputs": {
    "clean_dialogue": {"exists": true, "path": "derived/transcript-simple/whisper-cpp/resolved/clean_dialogue.audit_cleanup_v2.json"},
    "quality_report": {"exists": true, "path": "derived/transcript-simple/whisper-cpp/resolved/quality_report.audit_cleanup_v2.json"},
    "notes": {"exists": true, "path": "derived/synthesis-simple/extractive/notes.audit_cleanup_v2.md"},
    "quality_verdict": {"exists": true, "path": "derived/synthesis-simple/extractive/quality_verdict.audit_cleanup_v2.md"}
  },
  "recommended_next": "less $review_session/derived/audit/order/transcript_order_review.md"
}
EOF
cat >"$review_session/derived/readiness/review-plan/review_workspace_apply_report.json" <<'EOF'
{
  "schema": "murmurmark.review_workspace_apply_report/v1",
  "answers_source": "suggested",
  "dry_run": true,
  "summary": {
    "ready_for_partial_apply": true,
    "reviewed_count": 2,
    "remaining_rows": 1,
    "remaining_seconds": 2.5
  },
  "suggested_closure": {
    "status": "partial_apply_ready",
    "generated_suggestions": {
      "rows": 3,
      "seconds": 12.5,
      "by_decision": [
        {"key": "keep_me", "count": 1, "seconds": 7.0},
        {"key": "drop_me", "count": 1, "seconds": 3.0},
        {"key": "needs_review", "count": 1, "seconds": 2.5}
      ]
    },
    "closed_by_suggestions": {"rows": 2, "seconds": 10.0},
    "remaining_manual_queue": {"rows": 1, "seconds": 2.5}
  }
}
EOF
eval_python="$repo_root/.venv/bin/python"
if [[ ! -x "$eval_python" ]]; then
  eval_python="python3"
fi
"$eval_python" "$repo_root/scripts/evaluate-outcome.py" "$review_session" >/dev/null
jq -e '
  .outcome == "review_first"
  and (.next_command | startswith("murmurmark review suggested apply "))
  and (.next_command | contains("review-handoff"))
  and .metrics.suggested_closure_auto_seconds == 10
  and .metrics.suggested_closure_manual_remaining_seconds == 2.5
' "$review_session/derived/outcome/outcome.json" >/dev/null

open_output="$("$bin" open "$session" --kind all)"
echo "$open_output" | grep -q '^open:$'
echo "$open_output" | grep -q 'Quality verdict'
tail -1 <<<"$open_output" | grep -q '^next: less '

notes_command="$("$bin" open "$session" --kind notes --command-only)"
[[ "$notes_command" == less*notes.md ]]

export_output="$("$bin" export "$session" --format markdown --include-json --out-dir "$workdir/exports/private")"
echo "$export_output" | grep -q '^export:$'
echo "$export_output" | grep -q '^  status: exported'
tail -1 <<<"$export_output" | grep -q '^next: murmurmark retention plan '

manifest="$workdir/exports/private/cli-handoff/export_manifest.json"
[[ -s "$manifest" ]]
jq -e '.bundle_quality == "v1"' "$manifest" >/dev/null
jq -e '.outcome.outcome == "ready_for_notes" and .outcome.export_status == "allowed" and (.files.outcome_json.path | length > 0)' "$manifest" >/dev/null
grep -Fq '## Can I Use This?' "$workdir/exports/private/cli-handoff/index.md"
grep -q '^## Retention And Privacy$' "$workdir/exports/private/cli-handoff/index.md"
grep -q '`utt_cli_001`' "$workdir/exports/private/cli-handoff/transcript.md"
grep -q '^## Review Queue$' "$workdir/exports/private/cli-handoff/notes.md"

retention_output="$("$bin" retention plan "$session" --export-manifest "$manifest")"
echo "$retention_output" | grep -q '^retention:$'
echo "$retention_output" | grep -q '^  export_successful: true$'
echo "$retention_output" | grep -q '^  export_status: exported$'
echo "$retention_output" | grep -q '^  open:$'
echo "$retention_output" | grep -q '^    less .*retention_plan.json$'
echo "$retention_output" | grep -q '^    less .*export_manifest.json$'
tail -1 <<<"$retention_output" | grep -q '^next: murmurmark retention payload '

payload_output="$("$bin" retention payload "$session" --export-manifest "$manifest")"
echo "$payload_output" | grep -q '^retention_payload:$'
echo "$payload_output" | grep -q '^  open:$'
echo "$payload_output" | grep -q '^    less .*provider_payload_manifest.json$'
echo "$payload_output" | grep -q '^    less .*export_manifest.json$'
tail -1 <<<"$payload_output" | grep -q '^next: less '

finish_output="$("$bin" finish "$session" --out-dir "$workdir/finish/private")"
echo "$finish_output" | grep -q '^readiness:$'
echo "$finish_output" | grep -q '^export:$'
echo "$finish_output" | grep -q '^retention:$'
echo "$finish_output" | grep -q '^finish:$'
echo "$finish_output" | grep -q '^  status: ready$'
tail -1 <<<"$finish_output" | grep -q '^next: less '
[[ -s "$workdir/finish/private/cli-handoff/export_manifest.json" ]]
grep -Fq '## Can I Use This?' "$workdir/finish/private/cli-handoff/index.md"
[[ -s "$session/derived/retention/retention_plan.json" ]]
[[ -s "$session/derived/retention/provider_payload_manifest.json" ]]

post_export_next="$("$bin" next "$session" --export-manifest "$manifest")"
echo "$post_export_next" | grep -q '^  status: exportable$'
echo "$post_export_next" | grep -q '^  command: murmurmark retention plan '

live_cache_session="$workdir/sessions/live-cache-smoke"
mkdir -p \
  "$live_cache_session/derived/live/chunks/0001" \
  "$live_cache_session/derived/live/chunks/0002"
"$eval_python" - "$live_cache_session" <<'PY'
from pathlib import Path
import json
import sys

base = Path(sys.argv[1])
(base / "derived/live").mkdir(parents=True, exist_ok=True)
(base / "derived/live/live_pipeline_report.json").write_text(
    json.dumps({"schema": "murmurmark.live_pipeline_report/v1", "status": "completed"}, indent=2) + "\n",
    encoding="utf-8",
)
chunks = []
for index, start, end, clip_start, clip_end in [(1, 0, 60, 0, 65), (2, 60, 80, 55, 80)]:
    chunk = {
        "schema": "murmurmark.live_chunk/v1",
        "index": index,
        "start_sec": start,
        "end_sec": end,
        "duration_sec": end - start,
        "clip_start_sec": clip_start,
        "clip_end_sec": clip_end,
    }
    for source, prep in (("mic", "speech"), ("remote", "loudnorm")):
        live_dir = base / f"derived/live/chunks/{index:04d}"
        live_json = live_dir / f"{source}.json"
        live_wav = live_dir / f"{source}.wav"
        live_wav.write_bytes(b"fake-wav")
        if index == 1:
            text = f"{source} first"
            offset_from, offset_to = 1000, 2000
        else:
            text = f"{source} second"
            offset_from, offset_to = 6000, 7000
        live_json.write_text(
            json.dumps(
                {
                    "params": {"source": source},
                    "transcription": [
                        {
                            "text": text,
                            "offsets": {"from": offset_from, "to": offset_to},
                            "timestamps": {"from": "00:00:01,000", "to": "00:00:02,000"},
                            "tokens": [{"text": text, "offsets": {"from": offset_from, "to": offset_to}}],
                        }
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        chunk[source] = {
            "input": str(live_wav),
            "wav": str(live_wav),
            "audio_prep": prep,
            "hard_start_sec": start,
            "hard_end_sec": end,
            "clip_start_sec": clip_start,
            "clip_end_sec": clip_end,
            "preprocess_status": "passed",
            "asr": {"status": "passed", "json": str(live_json)},
            "text": text,
        }
    chunks.append(chunk)
with (base / "derived/live/chunks.jsonl").open("w", encoding="utf-8") as file:
    for chunk in chunks:
        file.write(json.dumps(chunk, ensure_ascii=False) + "\n")
PY

"$eval_python" "$repo_root/scripts/materialize-live-asr-cache.py" \
  "$live_cache_session" \
  --asr-window-sec 60 \
  --asr-overlap-sec 5 \
  --force >/dev/null
"$eval_python" "$repo_root/scripts/check-asr-chunk-cache.py" \
  "$live_cache_session" \
  --require-chunks >/dev/null
jq -e '
  .status == "materialized"
  and .materialized == true
  and .chunk_records_by_source.mic.chunks_completed == 2
  and .chunk_records_by_source.remote.chunks_completed == 2
' "$live_cache_session/derived/live/live_asr_cache_report.json" >/dev/null
jq -e '
  .status == "passed"
  and ([.tracks[] | select(.status == "pass")] | length == 2)
  and ([.tracks[] | select(.chunks_completed == 2 and .chunks_total == 2)] | length == 2)
' "$live_cache_session/derived/transcript-simple/whisper-cpp/raw/chunk_rebuild_check.json" >/dev/null

chunk_resume_session="$workdir/sessions/chunk-resume-smoke"
mkdir -p "$chunk_resume_session/derived/asr" "$chunk_resume_session/bin"
ffmpeg -hide_banner -loglevel error -y \
  -f lavfi -i "sine=frequency=440:duration=5" \
  -ar 16000 -ac 1 "$chunk_resume_session/derived/asr/mic.wav"
ffmpeg -hide_banner -loglevel error -y \
  -f lavfi -i "sine=frequency=660:duration=5" \
  -ar 16000 -ac 1 "$chunk_resume_session/derived/asr/remote.wav"
: >"$chunk_resume_session/fake-model.bin"
cat >"$chunk_resume_session/bin/fake-whisper-cli" <<'PY'
#!/usr/bin/env python3
from pathlib import Path
import json
import os
import sys

output_base = None
args = sys.argv[1:]
for index, arg in enumerate(args):
    if arg == "--output-file" and index + 1 < len(args):
        output_base = Path(args[index + 1])
        break
if output_base is None:
    print("missing --output-file", file=sys.stderr)
    raise SystemExit(2)

count_path = Path(os.environ["FAKE_WHISPER_COUNT"])
count = int(count_path.read_text(encoding="utf-8") or "0") if count_path.exists() else 0
count += 1
count_path.write_text(str(count), encoding="utf-8")

text = f"fake chunk {output_base.name}"
payload = {
    "params": {"fake_whisper_call": count},
    "transcription": [
        {
            "text": text,
            "offsets": {"from": 100, "to": 500},
            "timestamps": {"from": "00:00:00,100", "to": "00:00:00,500"},
            "tokens": [{"text": text, "offsets": {"from": 100, "to": 500}}],
        }
    ],
}
output_base.parent.mkdir(parents=True, exist_ok=True)
output_base.with_suffix(".json").write_text(
    json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
output_base.with_suffix(".txt").write_text(text + "\n", encoding="utf-8")
output_base.with_suffix(".vtt").write_text(
    "WEBVTT\n\n00:00:00.100 --> 00:00:00.500\n" + text + "\n",
    encoding="utf-8",
)
if count == 2:
    print("intentional fake whisper failure on call 2", file=sys.stderr)
    raise SystemExit(42)
PY
chmod +x "$chunk_resume_session/bin/fake-whisper-cli"
export FAKE_WHISPER_COUNT="$chunk_resume_session/fake-count.txt"
set +e
"$eval_python" "$repo_root/scripts/transcribe-simple-whispercpp.py" "$chunk_resume_session" \
  --model "$chunk_resume_session/fake-model.bin" \
  --whisper-cli "$chunk_resume_session/bin/fake-whisper-cli" \
  --skip-export \
  --asr-window-sec 2 \
  --asr-overlap-sec 0 \
  --mic-audio-prep none \
  --remote-audio-prep none \
  --threads 1 >"$chunk_resume_session/first.log" 2>&1
first_status=$?
set -e
if [[ "$first_status" -eq 0 ]]; then
  echo "expected first fake ASR run to fail" >&2
  exit 1
fi
jq -e '.status == "running" and .chunks_completed == 1 and .chunks_total == 3 and .chunks_missing == 2' \
  "$chunk_resume_session/derived/transcript-simple/whisper-cpp/raw/chunks/mic/chunk_cache_report.json" >/dev/null
"$eval_python" "$repo_root/scripts/transcribe-simple-whispercpp.py" "$chunk_resume_session" \
  --model "$chunk_resume_session/fake-model.bin" \
  --whisper-cli "$chunk_resume_session/bin/fake-whisper-cli" \
  --skip-export \
  --asr-window-sec 2 \
  --asr-overlap-sec 0 \
  --mic-audio-prep none \
  --remote-audio-prep none \
  --threads 1 >"$chunk_resume_session/second.log" 2>&1
"$eval_python" "$repo_root/scripts/check-asr-chunk-cache.py" "$chunk_resume_session" --require-chunks >/dev/null
jq -e '
  .status == "completed"
  and .chunks_total == 3
  and .chunks_completed == 3
  and .chunks_reused == 1
  and .chunks_transcribed == 2
' "$chunk_resume_session/derived/transcript-simple/whisper-cpp/raw/chunks/mic/chunk_cache_report.json" >/dev/null
jq -e '
  .status == "passed"
  and ([.tracks[] | select(.status == "pass")] | length == 2)
' "$chunk_resume_session/derived/transcript-simple/whisper-cpp/raw/chunk_rebuild_check.json" >/dev/null

live_parity_session="$workdir/sessions/2026-07-06_00-00-00"
mkdir -p \
  "$live_parity_session/derived/live" \
  "$live_parity_session/derived/transcript-simple/whisper-cpp/resolved" \
  "$live_parity_session/derived/readiness" \
  "$live_parity_session/derived/outcome"
cat >"$live_parity_session/session.json" <<'JSON'
{
  "schema": "murmurmark.session/v1",
  "session_id": "live-parity-smoke",
  "status": "completed",
  "health": {
    "summary": "ok",
    "partial": false,
    "explicit_stop": true,
    "stop_reason": "duration_elapsed",
    "screen_capture_restart_count": 0,
    "warnings": []
  }
}
JSON
cat >"$live_parity_session/derived/live/live_pipeline_report.json" <<'JSON'
{
  "schema": "murmurmark.live_pipeline_report/v1",
  "status": "completed",
  "batch_authoritative": true,
  "promotion_allowed": false,
  "progress": {"chunks_processed": 1, "draft_sec": 10.0}
}
JSON
cat >"$live_parity_session/derived/live/chunks.jsonl" <<'JSONL'
{"schema":"murmurmark.live_chunk/v1","index":1,"start_sec":0.0,"end_sec":10.0,"mic":{"text":"привет проверю задачу","hard_start_sec":0.0,"hard_end_sec":10.0},"remote":{"text":"привет обсудим план","hard_start_sec":0.0,"hard_end_sec":10.0}}
{"schema":"murmurmark.live_chunk/v1","index":2,"start_sec":10.0,"end_sec":12.0,"mic":{"text":"","hard_start_sec":10.0,"hard_end_sec":12.0,"live_boundary_gate":{"status":"passed","reason":"too_short_for_boundary_gate"}},"remote":{"text":"","raw_text_before_boundary_gate":"привет обсудим план","hard_start_sec":10.0,"hard_end_sec":12.0,"live_boundary_gate":{"status":"suppressed","reason":"adjacent_chunk_duplicate","duplicate_score":1.0,"current_token_recall_in_previous":1.0,"previous_token_recall_in_current":1.0}}}
JSONL
cat >"$live_parity_session/derived/transcript-simple/whisper-cpp/resolved/clean_dialogue.audit_cleanup_v4.json" <<'JSON'
{
  "schema": "murmurmark.clean_dialogue/v1",
  "utterances": [
    {"id":"utt_0001","start":0.0,"end":4.0,"role":"Me","speaker_label":"Me","text":"привет проверю задачу","quality":{"needs_review":false}},
    {"id":"utt_0002","start":4.0,"end":10.0,"role":"Colleagues","speaker_label":"Colleagues","text":"привет обсудим план","quality":{"needs_review":false}}
  ]
}
JSON
cat >"$live_parity_session/derived/transcript-simple/whisper-cpp/resolved/transcript.audit_cleanup_v4.md" <<'MD'
# Simple Transcript

## 00:00 Me

привет проверю задачу

## 00:04 Colleagues

привет обсудим план
MD
cat >"$live_parity_session/derived/transcript-simple/whisper-cpp/resolved/quality_report.audit_cleanup_v4.json" <<'JSON'
{
  "schema": "murmurmark.simple_transcript_quality/v1",
  "unrepaired_long_mic_crossings_count": 0,
  "local_only_island_recall": 1.0,
  "remote_duplicate_in_me_seconds": 0.0,
  "needs_review_count": 0,
  "cross_role_overlap_gt2_seconds": 0.0
}
JSON
cat >"$live_parity_session/derived/readiness/session_readiness.json" <<'JSON'
{
  "schema": "murmurmark.session_readiness/v1",
  "selected_profile": "audit_cleanup_v4",
  "use_gate": "ready_for_notes",
  "outputs": {
    "transcript": {"path": "derived/transcript-simple/whisper-cpp/resolved/transcript.audit_cleanup_v4.md"}
  }
}
JSON
cat >"$live_parity_session/derived/outcome/outcome.json" <<'JSON'
{
  "schema": "murmurmark.outcome/v1",
  "outcome": "ready_for_notes",
  "metrics": {
    "review_burden_sec": 0.0,
    "review_burden_ratio": 0.0
  }
}
JSON
"$eval_python" "$repo_root/scripts/compare-live-batch.py" "$live_parity_session" >/dev/null
jq -e '
  .promotion_allowed == false
  and .parity_gates.status == "passed_but_shadow_locked"
  and .metrics.meaningful_live_comparison == true
  and .metrics.all_parity_gates_passed == true
  and .metrics.capture_safety_status == "passed"
  and .metrics.live_boundary_gate_issue_count == 0
  and .metrics.live_boundary_gate_suppressed_count == 1
  and .metrics.live_boundary_gate_resolved_suppressed_count == 1
  and .metrics.live_boundary_gate_unresolved_suppressed_count == 0
  and ([.parity_gates.gates[] | select(.name == "capture_safety" and .status == "passed")] | length == 1)
  and ([.parity_gates.gates[] | select(.name == "order_risk" and .status == "passed")] | length == 1)
  and ([.parity_gates.gates[] | select(.name == "local_recall" and .status == "passed")] | length == 1)
  and ([.parity_gates.gates[] | select(.name == "remote_duplicate_leak" and .status == "passed")] | length == 1)
  and ([.parity_gates.gates[] | select(.name == "review_burden" and .status == "passed")] | length == 1)
  and ([.parity_gates.gates[] | select(.name == "selected_notes_readiness" and .status == "passed")] | length == 1)
  and ([.parity_gates.gates[] | select(.name == "chunk_boundary_risks" and .status == "passed")] | length == 1)
' "$live_parity_session/derived/live/live_batch_comparison.json" >/dev/null

live_boundary_risk_session="$workdir/sessions/2026-07-06_00-01-00"
mkdir -p \
  "$live_boundary_risk_session/derived/live" \
  "$live_boundary_risk_session/derived/transcript-simple/whisper-cpp/resolved" \
  "$live_boundary_risk_session/derived/readiness" \
  "$live_boundary_risk_session/derived/outcome"
cp "$live_parity_session/session.json" "$live_boundary_risk_session/session.json"
cp "$live_parity_session/derived/live/live_pipeline_report.json" \
  "$live_boundary_risk_session/derived/live/live_pipeline_report.json"
cp "$live_parity_session/derived/transcript-simple/whisper-cpp/resolved/clean_dialogue.audit_cleanup_v4.json" \
  "$live_boundary_risk_session/derived/transcript-simple/whisper-cpp/resolved/clean_dialogue.audit_cleanup_v4.json"
cp "$live_parity_session/derived/transcript-simple/whisper-cpp/resolved/transcript.audit_cleanup_v4.md" \
  "$live_boundary_risk_session/derived/transcript-simple/whisper-cpp/resolved/transcript.audit_cleanup_v4.md"
cp "$live_parity_session/derived/transcript-simple/whisper-cpp/resolved/quality_report.audit_cleanup_v4.json" \
  "$live_boundary_risk_session/derived/transcript-simple/whisper-cpp/resolved/quality_report.audit_cleanup_v4.json"
cp "$live_parity_session/derived/readiness/session_readiness.json" \
  "$live_boundary_risk_session/derived/readiness/session_readiness.json"
cp "$live_parity_session/derived/outcome/outcome.json" \
  "$live_boundary_risk_session/derived/outcome/outcome.json"
cat >"$live_boundary_risk_session/derived/live/chunks.jsonl" <<'JSONL'
{"schema":"murmurmark.live_chunk/v1","index":1,"start_sec":0.0,"end_sec":5.0,"mic":{"text":"привет проверю задачу","hard_start_sec":0.0,"hard_end_sec":5.0},"remote":{"text":"привет обсудим план","hard_start_sec":0.0,"hard_end_sec":5.0}}
{"schema":"murmurmark.live_chunk/v1","index":2,"start_sec":5.0,"end_sec":10.0,"mic":{"text":"","hard_start_sec":5.0,"hard_end_sec":10.0,"live_boundary_gate":{"status":"passed","reason":"too_short_for_boundary_gate"}},"remote":{"text":"","raw_text_before_boundary_gate":"привет обсудим план новый хвост","hard_start_sec":5.0,"hard_end_sec":10.0,"live_boundary_gate":{"status":"suppressed","reason":"adjacent_chunk_duplicate","duplicate_score":0.6,"current_token_recall_in_previous":0.6,"previous_token_recall_in_current":1.0}}}
JSONL
"$eval_python" "$repo_root/scripts/compare-live-batch.py" "$live_boundary_risk_session" >/dev/null
jq -e '
  .promotion_allowed == false
  and .parity_gates.status == "not_promotable"
  and (.warnings | index("live_boundary_gate_issues_detected") != null)
  and .metrics.live_boundary_gate_issue_count == 1
  and .metrics.live_boundary_gate_suppressed_count == 1
  and .metrics.live_boundary_gate_resolved_suppressed_count == 0
  and .metrics.live_boundary_gate_unresolved_suppressed_count == 1
  and ([.parity_gates.gates[] | select(.name == "chunk_boundary_risks" and .status == "warning")] | length == 1)
' "$live_boundary_risk_session/derived/live/live_batch_comparison.json" >/dev/null
jq -e '
  [.risk_examples.boundary_gate_issues[] | select(.source == "remote" and .status == "suppressed" and .resolution.unique_current_token_count == 2)]
  | length == 1
' "$live_boundary_risk_session/derived/live/live_batch_comparison.json" >/dev/null
write_full_capture_regression_proof
"$eval_python" "$repo_root/scripts/report-live-corpus-gates.py" "$live_boundary_risk_session" \
  --sessions-root "$workdir/sessions" \
  --out-dir "$workdir/live-boundary-risk-report" \
  --refresh >/dev/null
jq -e '
  .summary.real_live_boundary_gate_issue_count == 1
  and .summary.real_live_boundary_gate_suppressed_count == 1
  and .summary.real_live_boundary_gate_resolved_suppressed_count == 0
  and .summary.real_live_boundary_gate_unresolved_suppressed_count == 1
  and .summary.live_comparison_refresh_status == "passed"
  and .summary.live_comparison_refresh_attempted_sessions == 1
  and .summary.live_comparison_refresh_failed_sessions == 0
  and .live_comparison_refresh.requested == true
  and .live_comparison_refresh.attempted_sessions == 1
  and .live_comparison_refresh.failed_sessions == 0
  and .real_parity_dimensions.chunk_boundary_risks.counts.warning == 1
  and .real_blocker_triage_summary.by_category.chunk_boundary_risk.item_count == 1
  and .objective_audit.overall_status == "blocked_by_parity_gates"
  and .summary.real_capture_safe_candidate_sessions == 1
  and .summary.real_capture_safe_candidate_passing_sessions == 0
  and .real_capture_safe_candidate_parity_dimensions.chunk_boundary_risks.counts.warning == 1
  and .objective_audit.capture_safe_candidate_scope.blocking_dimensions == ["chunk_boundary_risks"]
  and .objective_audit.capture_safe_candidate_scope.next_focus.action_id == "fix_live_chunk_boundary_risks"
  and .objective_audit.next_focus.action_id == "fix_live_chunk_boundary_risks"
' "$workdir/live-boundary-risk-report/live_corpus_gates_report.json" >/dev/null
jq -e '
  .status == "passing_shadow_locked"
  and .promotion_allowed == false
  and ([.checks[] | select(.id == "meaningful_two_role_comparison" and .status == "pass")] | length == 1)
  and ([.checks[] | select(.id == "all_parity_gates_passed" and .status == "pass")] | length == 1)
  and ([.checks[] | select(.id == "promotion_blocked" and .status == "pass")] | length == 1)
' "$live_parity_session/derived/live/live_parity_session_report.json" >/dev/null
"$eval_python" "$repo_root/scripts/report-live-corpus-gates.py" "$live_parity_session" \
  --sessions-root "$workdir/sessions" \
  --out-dir "$workdir/live-report" \
  --refresh >/dev/null
jq -e '
  .summary.live_sessions == 1
  and .summary.real_live_sessions == 1
  and .summary.diagnostic_live_sessions == 0
  and .summary.live_comparison_refresh_status == "passed"
  and .summary.live_comparison_refresh_attempted_sessions == 1
  and .summary.live_comparison_refresh_failed_sessions == 0
  and .summary.compared_sessions == 1
  and .summary.real_compared_sessions == 1
  and .summary.meaningful_compared_sessions == 1
  and .summary.real_meaningful_compared_sessions == 1
  and .summary.passing_compared_sessions == 1
  and .summary.real_passing_compared_sessions == 1
  and .summary.real_capture_safe_candidate_sessions == 1
  and .summary.real_capture_safe_candidate_passing_sessions == 1
  and .summary.real_capture_safe_candidate_blocking_dimensions == []
  and .summary.real_live_boundary_gate_issue_count == 0
  and .summary.real_live_boundary_gate_suppressed_count == 1
  and .summary.real_live_boundary_gate_resolved_suppressed_count == 1
  and .summary.real_live_boundary_gate_unresolved_suppressed_count == 0
  and .summary.promotion_allowed_sessions == 0
  and .summary.live_quarantined == true
  and .summary.live_evidence_mode == "historical_debug_only"
  and .summary.new_real_live_collection_allowed == false
  and .summary.controlled_real_live_pilot_allowed == true
  and .summary.target_status == "shadow_locked_needs_more_live_coverage"
  and .coverage_target.status == "needs_more_live_coverage"
  and .coverage_target.live_sessions_remaining == 2
  and .coverage_target.passing_compared_sessions_remaining == 2
  and (.recommended_next | startswith("MURMURMARK_ENABLE_UNSAFE_LIVE_PIPELINE=1 murmurmark record"))
  and ([.next_commands[] | select(contains("--min-live-sessions"))] | length) == 0
  and ([.next_commands[] | select(contains("murmurmark corpus live all --refresh"))] | length) == 1
  and .real_blocker_triage_summary.total_items == 0
  and .real_blocker_triage_summary.real_gate_issue_count == 0
  and .real_blocker_triage_summary.uncategorized_gate_issue_count == 0
  and .real_parity_dimensions.capture_safety.counts.passed == 1
  and .real_capture_safe_candidate_parity_dimensions.capture_safety.counts.passed == 1
  and .real_capture_safe_candidate_parity_dimensions.required_artifacts.counts.passed == 1
  and .promotion_policy.status == "blocked"
  and .promotion_policy.batch_authoritative == true
  and .promotion_policy.live_quarantined == true
  and .promotion_policy.new_real_live_collection_allowed == false
  and .promotion_policy.controlled_real_live_pilot_allowed == true
  and .objective_audit.overall_status == "incomplete_coverage"
  and .objective_audit.batch_authoritative == true
  and .objective_audit.ready_for_live_promotion == false
  and .objective_audit.new_real_live_collection_allowed == false
  and .objective_audit.controlled_real_live_pilot_allowed == true
  and .objective_audit.capture_safe_candidate_scope.sessions == 1
  and .objective_audit.capture_safe_candidate_scope.passing_sessions == 1
  and .objective_audit.capture_safe_candidate_scope.blocking_dimensions == []
  and .objective_audit.capture_safe_candidate_scope.next_focus == null
  and .objective_audit.capture_safe_candidate_scope.controlled_real_live_pilot_allowed == true
  and .objective_audit.next_focus.action_id == "collect_controlled_capture_safe_live_pilot"
  and ((.promotion_policy.required_dimensions | sort) == [
    "capture_safety",
    "chunk_boundary_risks",
    "draft_text_recall",
    "local_recall",
    "order_risk",
    "remote_leakage",
    "required_artifacts",
    "review_burden",
    "selected_notes_readiness"
  ])
  and ([.parity_dimensions | keys[]] | sort) == [
    "capture_safety",
    "chunk_boundary_risks",
    "draft_text_recall",
    "local_recall",
    "order_risk",
    "remote_leakage",
    "required_artifacts",
    "review_burden",
    "selected_notes_readiness"
  ]
' "$workdir/live-report/live_corpus_gates_report.json" >/dev/null
"$eval_python" "$repo_root/scripts/report-live-corpus-gates.py" "$live_parity_session" \
  --sessions-root "$workdir/sessions" \
  --out-dir "$workdir/live-report-strict" \
  --min-live-sessions 1 \
  --min-compared-sessions 1 \
  --min-meaningful-compared-sessions 1 \
  --min-passing-compared-sessions 1 \
  --max-order-mismatches 0 \
  --max-missing-me-sec 0 \
  --max-remote-in-me-sec 0 \
  --max-boundary-duplicates 0 \
  --require-passing-gates \
  --fail-on-promotion >/dev/null
jq -e '
  .summary.strict_coverage_status == "passed"
  and .summary.live_quarantined == true
  and .summary.new_real_live_collection_allowed == false
  and .summary.controlled_real_live_pilot_allowed == true
  and .promotion_policy.status == "blocked"
  and .promotion_policy.batch_authoritative == true
  and .promotion_policy.controlled_real_live_pilot_allowed == true
  and .strict_coverage.requested == true
  and (.strict_coverage.failures | length) == 0
  and (.recommended_next | startswith("MURMURMARK_ENABLE_UNSAFE_LIVE_PIPELINE=1 murmurmark record"))
  and ([.next_commands[] | select(contains("--min-live-sessions"))] | length) == 0
  and ([.next_commands[] | select(contains("murmurmark corpus live all --refresh"))] | length) == 1
' "$workdir/live-report-strict/live_corpus_gates_report.json" >/dev/null

"$repo_root/scripts/smoke-process-chunk-resume.sh" >/dev/null

echo "cli handoff smoke ok"
