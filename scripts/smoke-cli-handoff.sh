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
' "$session/derived/pipeline-run/pipeline_run_report.json" >/dev/null

status_output="$("$bin" status "$session")"
echo "$status_output" | grep -q '^readiness:$'
echo "$status_output" | grep -q '^  status: exportable$'
echo "$status_output" | grep -q '^  use:$'
echo "$status_output" | grep -q '^    summary: ready to read and export$'
echo "$status_output" | grep -q '^    can_read_notes: true$'
echo "$status_output" | grep -q '^    can_export: true$'
echo "$status_output" | grep -q '^    minimum_step: murmurmark finish '
tail -1 <<<"$status_output" | grep -q '^next: murmurmark finish '

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

echo "cli handoff smoke ok"
