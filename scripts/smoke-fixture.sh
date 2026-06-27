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
require_tool ffprobe
require_tool jq

if [[ ! -x "$bin" ]]; then
  (cd "$repo_root" && swift build >/dev/null)
fi

workdir="$(mktemp -d "${TMPDIR:-/tmp}/murmurmark-smoke.XXXXXX")"
trap 'rm -rf "$workdir"' EXIT

session="$workdir/session"
mkdir -p "$session/audio/mic" "$session/audio/remote"

ffmpeg -y -hide_banner -loglevel error \
  -f lavfi -i "anoisesrc=color=white:sample_rate=48000:duration=30:amplitude=0.2" \
  -filter_complex "[0:a]asplit=2[remote][mic];[mic]volume=0.15[micout]" \
  -map "[micout]" -c:a pcm_s16le "$session/audio/mic/000001.caf" \
  -map "[remote]" -c:a pcm_s16le "$session/audio/remote/000001.caf"

mic_bytes="$(stat -f%z "$session/audio/mic/000001.caf")"
remote_bytes="$(stat -f%z "$session/audio/remote/000001.caf")"
audio_frames=1440000
mic_sha_before="$(shasum -a 256 "$session/audio/mic/000001.caf" | awk '{print $1}')"
remote_sha_before="$(shasum -a 256 "$session/audio/remote/000001.caf" | awk '{print $1}')"

jq -n \
  --argjson mic_bytes "$mic_bytes" \
  --argjson remote_bytes "$remote_bytes" \
  --argjson audio_frames "$audio_frames" \
  '{
    schema: "murmurmark.session/v1",
    session_id: "fixture",
    created_at: "2026-06-22T16:00:00.000Z",
    ended_at: "2026-06-22T16:00:01.000Z",
    app_version: "0.1.0",
    capture_mode: "fixture",
    status: "completed",
    target: {
      kind: "system_audio",
      bundle_id: null,
      display_name: "Fixture",
      pid_strategy: "fixture"
    },
    microphone: {
      device_uid: "default",
      display_name: "System Default Microphone",
      capture_backend: "fixture"
    },
    remote_audio: {
      backend: "fixture",
      sample_rate: 48000,
      channels: 1,
      format: "caf:lpcm"
    },
    mic_audio: {
      backend: "fixture",
      sample_rate: 48000,
      channels: 1,
      format: "caf:lpcm"
    },
    privacy: {
      network_allowed_during_capture: false,
      telemetry: false,
      raw_audio_retention: "fixture"
    },
    files: {
      mic: [{
        path: "audio/mic/000001.caf",
        start_host_time_ns: 0,
        start_session_sec: 0,
        sample_rate: 48000,
        frames: $audio_frames,
        channels: 1,
        bytes: $mic_bytes,
        sha256: null
      }],
      remote: [{
        path: "audio/remote/000001.caf",
        start_host_time_ns: 0,
        start_session_sec: 0,
        sample_rate: 48000,
        frames: $audio_frames,
        channels: 1,
        bytes: $remote_bytes,
        sha256: null
      }]
    },
    health: {
      summary: "ok",
      warnings: []
    }
  }' >"$session/session.json"

jq -n '{
  schema: "murmurmark.pipeline_job/v1",
  session_id: "fixture",
  inputs: {
    mic: "audio/mic",
    remote: "audio/remote",
    manifest: "session.json"
  },
  meeting_context: {
    language: ["ru", "en"]
  },
  steps: ["preprocess", "asr"]
}' >"$session/pipeline_job.json"

inspect_output="$("$bin" inspect "$session")"
echo "$inspect_output" | grep -q 'session_id: fixture'
echo "$inspect_output" | grep -q 'mic: files=1'
echo "$inspect_output" | grep -q 'remote: files=1'

"$bin" export-audio "$session" >/dev/null

for source in mic remote; do
  wav="$session/derived/asr/$source.wav"
  [[ -s "$wav" ]]
  probe="$(ffprobe -v error -show_entries stream=sample_rate,channels -of compact=p=0:nk=1 "$wav")"
  if [[ "$probe" != "16000|1" ]]; then
    echo "unexpected $source.wav format: $probe" >&2
    exit 1
  fi
done

"$bin" preprocess "$session" --echo diagnostic >/dev/null
echo_output="$("$bin" inspect "$session" --echo)"
echo "$echo_output" | grep -q 'echo:'
echo "$echo_output" | grep -q 'bleed_detected: true'
"$bin" preprocess "$session" --echo clean --echo-engine linear_baseline >/dev/null
echo_output="$("$bin" inspect "$session" --echo)"
echo "$echo_output" | grep -q 'suppression_engine: linear_baseline'

for path in \
  "$session/derived/preprocess/audio/mic_raw_for_asr.wav" \
  "$session/derived/preprocess/audio/remote_for_aec.wav" \
  "$session/derived/preprocess/audio/mic_for_asr.wav" \
  "$session/derived/preprocess/audio/mic_clean_linear.wav" \
  "$session/derived/preprocess/echo/echo_diagnostics.json" \
  "$session/derived/preprocess/echo/echo_segments.jsonl" \
  "$session/derived/preprocess/echo/echo_suppression_report.json"; do
  [[ -s "$path" ]]
done

jq -e '.schema == "murmurmark.echo_diagnostics/v1"' "$session/derived/preprocess/echo/echo_diagnostics.json" >/dev/null
jq -e '.summary.bleed_detected == true' "$session/derived/preprocess/echo/echo_diagnostics.json" >/dev/null
jq -e '.schema == "murmurmark.echo_suppression_report/v1"' "$session/derived/preprocess/echo/echo_suppression_report.json" >/dev/null
jq -e '.engine.name == "linear_baseline"' "$session/derived/preprocess/echo/echo_suppression_report.json" >/dev/null

python_cmd=""
if [[ -x "$repo_root/.venv/bin/python" ]] && "$repo_root/.venv/bin/python" - <<'PY' >/dev/null 2>&1
import numpy
import scipy
PY
then
  python_cmd="$repo_root/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1 && python3 - <<'PY' >/dev/null 2>&1
import numpy
import scipy
PY
then
  python_cmd="$(command -v python3)"
fi

if [[ -n "$python_cmd" ]]; then
  MURMURMARK_PYTHON="$python_cmd" "$bin" preprocess "$session" --echo clean --echo-engine local_fir >/dev/null
  echo_output="$("$bin" inspect "$session" --echo)"
  echo "$echo_output" | grep -q 'suppression_engine: local_fir'
  [[ -s "$session/derived/preprocess/audio/mic_clean_local_fir.wav" ]]
  [[ -s "$session/derived/preprocess/audio/mic_role_masked_for_asr.wav" ]]
  [[ -s "$session/derived/preprocess/audio/mic_role_preview.wav" ]]
  [[ -s "$session/derived/preprocess/audio/echo_hat_local_fir.wav" ]]
  [[ -s "$session/derived/preprocess/echo/local_fir_report.json" ]]
  [[ -s "$session/derived/preprocess/echo/local_fir_segments.jsonl" ]]
  [[ -s "$session/derived/preprocess/echo/speaker_state.jsonl" ]]
  [[ -s "$session/derived/preprocess/mic_asr_segments/segments_manifest.json" ]]
  jq -e '.engine.name == "local_fir"' "$session/derived/preprocess/echo/echo_suppression_report.json" >/dev/null
  jq -e '.outputs.role_masked_mic == "derived/preprocess/audio/mic_role_masked_for_asr.wav"' "$session/derived/preprocess/echo/echo_suppression_report.json" >/dev/null
  jq -e '.schema == "murmurmark.mic_asr_segments/v1"' "$session/derived/preprocess/mic_asr_segments/segments_manifest.json" >/dev/null
fi

if "$repo_root/scripts/build-speexdsp-helper.sh" >/dev/null 2>&1; then
  "$bin" preprocess "$session" --echo clean --echo-engine speexdsp >/dev/null
  echo_output="$("$bin" inspect "$session" --echo)"
  echo "$echo_output" | grep -q 'suppression_engine: speexdsp'
  [[ -s "$session/derived/preprocess/audio/mic_clean_speex.wav" ]]
  jq -e '.engine.name == "speexdsp"' "$session/derived/preprocess/echo/echo_suppression_report.json" >/dev/null
fi

if "$repo_root/scripts/build-webrtc-apm-helper.sh" >/dev/null 2>&1; then
  "$bin" preprocess "$session" --echo clean --echo-engine webrtc-apm >/dev/null
  echo_output="$("$bin" inspect "$session" --echo)"
  echo "$echo_output" | grep -q 'suppression_engine: webrtc-apm'
  [[ -s "$session/derived/preprocess/audio/mic_clean_webrtc.wav" ]]
  jq -e '.engine.name == "webrtc-apm"' "$session/derived/preprocess/echo/echo_suppression_report.json" >/dev/null
fi

selected_export_output="$("$bin" export-audio "$session" --out "$session/derived/asr-selected")"
echo "$selected_export_output" | grep -q 'exporting mic from derived/preprocess/audio/mic_for_asr.wav'
[[ -s "$session/derived/asr-selected/mic.wav" ]]
[[ -s "$session/derived/asr-selected/remote.wav" ]]

retention_plan_output="$("$bin" retention plan "$session")"
echo "$retention_plan_output" | grep -q '^retention:$'
echo "$retention_plan_output" | grep -q '  raw_audio_files: 2'
echo "$retention_plan_output" | grep -q '  next:'
[[ -s "$session/derived/retention/retention_plan.json" ]]
jq -e '.schema == "murmurmark.retention_plan/v1"' "$session/derived/retention/retention_plan.json" >/dev/null
jq -e '.policy.external_providers.allow == false' "$session/derived/retention/retention_plan.json" >/dev/null
jq -e 'all(.actions[]; .planned_action == "keep_raw_audio")' "$session/derived/retention/retention_plan.json" >/dev/null

retention_payload_output="$("$bin" retention payload "$session")"
echo "$retention_payload_output" | grep -q '^retention_payload:$'
echo "$retention_payload_output" | grep -q '  sends_data: false'
echo "$retention_payload_output" | grep -q '  raw_audio_included: false'
[[ -s "$session/derived/retention/provider_payload_manifest.json" ]]
jq -e '.schema == "murmurmark.provider_payload_manifest/v1"' "$session/derived/retention/provider_payload_manifest.json" >/dev/null
jq -e '.status == "blocked" and .sends_data == false and .raw_audio_included == false' "$session/derived/retention/provider_payload_manifest.json" >/dev/null

mkdir -p "$session/derived/transcript/resolved"
jq -n '{
  schema: "murmurmark.transcript/v1",
  session_id: "fixture",
  language_profile: ["ru", "en"],
  utterances: [
    {
      id: "utt_remote_001",
      start: 0.10,
      end: 0.35,
      source_track: "remote",
      speaker_cluster: "remote_speaker_01",
      role: "teammate",
      raw_text: "давайте посмотрим сло",
      corrected_text: "Давайте посмотрим SLO.",
      quality: {
        speaker_assignment: "verified",
        possible_mic_leakage: false,
        excluded_from_me_role: false,
        matched_remote_utterance_id: null,
        needs_review: false
      }
    },
    {
      id: "utt_mic_001",
      start: 0.10,
      end: 0.35,
      source_track: "mic",
      speaker_cluster: "me",
      role: "me",
      raw_text: "давайте посмотрим сло",
      corrected_text: "Давайте посмотрим SLO.",
      quality: {
        speaker_assignment: "probable",
        possible_mic_leakage: false,
        excluded_from_me_role: false,
        matched_remote_utterance_id: null,
        needs_review: false
      }
    },
    {
      id: "utt_mic_002",
      start: 0.38,
      end: 0.48,
      source_track: "mic",
      speaker_cluster: "me",
      role: "me",
      raw_text: "мой ответ",
      quality: {
        speaker_assignment: "verified",
        possible_mic_leakage: false,
        excluded_from_me_role: false,
        matched_remote_utterance_id: null,
        needs_review: false
      }
    }
  ]
}' >"$session/derived/transcript/resolved/transcript.rich.json"

"$bin" reconcile-transcript "$session" >/dev/null
jq -e '.utterances[] | select(.id == "utt_mic_001") | .quality.possible_mic_leakage == true' "$session/derived/transcript/resolved/transcript.rich.json" >/dev/null
jq -e '.utterances[] | select(.id == "utt_mic_001") | .quality.excluded_from_me_role == true' "$session/derived/transcript/resolved/transcript.rich.json" >/dev/null
jq -e '.utterances[] | select(.id == "utt_mic_001") | .quality.matched_remote_utterance_id == "utt_remote_001"' "$session/derived/transcript/resolved/transcript.rich.json" >/dev/null
jq -e '.utterances[] | select(.id == "utt_mic_002") | .quality.possible_mic_leakage == false' "$session/derived/transcript/resolved/transcript.rich.json" >/dev/null
jq -e '.summary.echo.segments_excluded_from_me_role == 1' "$session/derived/transcript/resolved/quality_report.json" >/dev/null
jq -e '.schema == "murmurmark.echo_reconciliation_report/v1"' "$session/derived/transcript/resolved/echo_reconciliation_report.json" >/dev/null

simple_resolved="$session/derived/transcript-simple/whisper-cpp/resolved"
mkdir -p "$simple_resolved"
jq -n '{
  schema: "murmurmark.clean_dialogue/v1",
  session: "fixture",
  utterances: [
    {id: "utt_simple_001", start: 0.0, end: 1.0, role: "Me", speaker_label: "Me", source_track: "mic", text: "Вот.", quality: {needs_review: false}},
    {id: "utt_simple_002", start: 2.0, end: 3.0, role: "Colleagues", speaker_label: "Colleagues", source_track: "remote", text: "Хм.", quality: {needs_review: false}},
    {id: "utt_simple_003", start: 10.0, end: 16.0, role: "Me", speaker_label: "Me", source_track: "mic", text: "Надо проверить логи деплоя в GitLab.", quality: {needs_review: false}},
    {id: "utt_simple_004", start: 22.0, end: 28.0, role: "Colleagues", speaker_label: "Colleagues", source_track: "remote", text: "Надо понимать, что это сложная тема.", quality: {needs_review: false}},
    {id: "utt_simple_005", start: 34.0, end: 38.0, role: "Me", speaker_label: "Me", source_track: "mic", text: "Надо подумать.", quality: {needs_review: false}},
    {id: "utt_simple_006", start: 52.0, end: 58.0, role: "Colleagues", speaker_label: "Colleagues", source_track: "remote", text: "Решили оставить Kubernetes как есть.", quality: {needs_review: false}},
    {id: "utt_simple_007", start: 68.0, end: 75.0, role: "Me", speaker_label: "Me", source_track: "mic", text: "Давай пока не трогать GitLab pipeline.", quality: {needs_review: false}},
    {id: "utt_simple_008", start: 78.0, end: 81.0, role: "Colleagues", speaker_label: "Colleagues", source_track: "remote", text: "Да, согласен.", quality: {needs_review: false}},
    {id: "utt_simple_009", start: 90.0, end: 91.0, role: "Me", speaker_label: "Me", source_track: "mic", text: "Окей.", quality: {needs_review: false}},
    {id: "utt_simple_010", start: 110.0, end: 118.0, role: "Colleagues", speaker_label: "Colleagues", source_track: "remote", text: "Есть риск, что мы не успеем выкатить deploy до пятницы.", quality: {needs_review: false}},
    {id: "utt_simple_011", start: 126.0, end: 132.0, role: "Me", speaker_label: "Me", source_track: "mic", text: "Проблема троттлингов скоро будет решена.", quality: {needs_review: false}},
    {id: "utt_simple_012", start: 144.0, end: 151.0, role: "Colleagues", speaker_label: "Colleagues", source_track: "remote", text: "Надо понять, кто будет делать миграцию.", quality: {needs_review: false}},
    {id: "utt_simple_013", start: 160.0, end: 164.0, role: "Me", speaker_label: "Me", source_track: "mic", text: "Вопрос по Kubernetes квотам.", quality: {needs_review: false}},
    {id: "utt_simple_014", start: 176.0, end: 179.0, role: "Me", speaker_label: "Me", source_track: "mic", text: "Давайте перейдем к следующему блоку.", quality: {needs_review: false}},
    {id: "utt_simple_015", start: 184.0, end: 187.0, role: "Me", speaker_label: "Me", source_track: "mic", text: "Давайте проголосуем.", quality: {needs_review: false}},
    {id: "utt_simple_016", start: 196.0, end: 202.0, role: "Me", speaker_label: "Me", source_track: "mic", text: "Нужно добавить задачу на алерты.", quality: {needs_review: false}},
    {id: "utt_simple_017", start: 212.0, end: 218.0, role: "Colleagues", speaker_label: "Colleagues", source_track: "remote", text: "Решили оставить ретро раз в две недели.", quality: {needs_review: false}}
  ]
}' >"$simple_resolved/clean_dialogue.json"
jq -n '{schema: "murmurmark.simple_transcript/v1", utterances: []}' >"$simple_resolved/transcript.simple.json"
cat >"$simple_resolved/transcript.md" <<'EOF'
# Simple Transcript

## 00:10 Me

Надо проверить логи деплоя в GitLab.

## 03:32 Colleagues

Решили оставить ретро раз в две недели.
EOF
jq -n '{
  schema: "murmurmark.simple_transcript_quality/v1",
  utterances: 17,
  needs_review_count: 0,
  cross_role_overlap_gt2_count: 1,
  cross_role_overlap_gt2_seconds: 3,
  remote_duplicate_in_me_seconds: 0,
  unrepaired_long_mic_crossings_count: 0,
  golden_phrase_fail_count: 0
}' >"$simple_resolved/quality_report.json"
jq -n '{
  schema: "murmurmark.transcript_overlaps/v1",
  session: "fixture",
  overlaps: [
    {
      left_utterance_id: "utt_simple_003",
      right_utterance_id: "utt_simple_004",
      start: 22.0,
      end: 25.0,
      duration_sec: 3.0,
      type: "fixture_overlap"
    }
  ]
}' >"$simple_resolved/overlaps.json"

"$repo_root/scripts/synthesize-simple-extractive.py" "$session" --transcript-profile auto >/dev/null
[[ -s "$session/derived/synthesis-simple/extractive/synthesis_manifest.json" ]]
[[ -s "$session/derived/synthesis-simple/extractive/quality_verdict.json" ]]
[[ -s "$session/derived/synthesis-simple/extractive/quality_verdict.md" ]]
[[ -s "$session/derived/synthesis-simple/extractive/notes.md" ]]
[[ -s "$session/derived/synthesis-simple/extractive/evidence_notes.json" ]]
[[ -s "$session/derived/synthesis-simple/extractive/review_items.jsonl" ]]
jq -e '.schema == "murmurmark.quality_verdict/v1"' "$session/derived/synthesis-simple/extractive/quality_verdict.json" >/dev/null
jq -e '.verdict == "usable_with_review"' "$session/derived/synthesis-simple/extractive/quality_verdict.json" >/dev/null
jq -e '.schema == "murmurmark.evidence_notes/v2"' "$session/derived/synthesis-simple/extractive/evidence_notes.json" >/dev/null
jq -e 'any(.selected.actions[]; (.display_text | contains("Надо проверить логи деплоя")) and .score >= 70)' "$session/derived/synthesis-simple/extractive/evidence_notes.json" >/dev/null
jq -e 'any(.candidates[]; .subtype == "process_discussion" and (.display_text | contains("Надо понимать")) and .status == "hidden")' "$session/derived/synthesis-simple/extractive/evidence_notes.json" >/dev/null
jq -e 'any(.candidates[]; .subtype == "weak_action" and (.display_text | contains("Надо подумать")) and .status == "hidden")' "$session/derived/synthesis-simple/extractive/evidence_notes.json" >/dev/null
jq -e 'any(.selected.decisions[]; (.display_text | contains("Решили оставить Kubernetes")) and .score >= 75)' "$session/derived/synthesis-simple/extractive/evidence_notes.json" >/dev/null
jq -e 'any(.selected.decisions[]; (.evidence_utterance_ids | index("utt_simple_007")) and (.evidence_utterance_ids | index("utt_simple_008")))' "$session/derived/synthesis-simple/extractive/evidence_notes.json" >/dev/null
jq -e 'all(.selected.decisions[]; .display_text != "Окей.")' "$session/derived/synthesis-simple/extractive/evidence_notes.json" >/dev/null
jq -e 'any(.selected.risks[]; .display_text | contains("не успеем"))' "$session/derived/synthesis-simple/extractive/evidence_notes.json" >/dev/null
jq -e 'all(.selected.risks[]; (.display_text | contains("Проблема троттлингов") | not))' "$session/derived/synthesis-simple/extractive/evidence_notes.json" >/dev/null
jq -e 'any(.selected.open_questions[]; .display_text | contains("кто будет делать миграцию"))' "$session/derived/synthesis-simple/extractive/evidence_notes.json" >/dev/null
jq -e 'all(.selected.open_questions[]; (.display_text | contains("Вопрос по Kubernetes") | not))' "$session/derived/synthesis-simple/extractive/evidence_notes.json" >/dev/null
jq -e 'all(.selected.outline_blocks[].representatives[]?; .text != "Вот." and .text != "Хм.")' "$session/derived/synthesis-simple/extractive/evidence_notes.json" >/dev/null
jq -e 'any(.candidates[]; .subtype == "meeting_facilitation" and .status == "hidden" and (.display_text | contains("Давайте перейдем")))' "$session/derived/synthesis-simple/extractive/evidence_notes.json" >/dev/null
jq -e 'any(.candidates[]; .subtype == "meeting_facilitation" and .status == "hidden" and (.display_text | contains("Давайте проголосуем")))' "$session/derived/synthesis-simple/extractive/evidence_notes.json" >/dev/null
jq -e 'all(.selected.decisions[]; (((.display_text | contains("Давайте перейдем")) or (.display_text | contains("Давайте проголосуем"))) | not))' "$session/derived/synthesis-simple/extractive/evidence_notes.json" >/dev/null
jq -e 'all(.selected.actions[]; (((.display_text | contains("Давайте перейдем")) or (.display_text | contains("Давайте проголосуем"))) | not))' "$session/derived/synthesis-simple/extractive/evidence_notes.json" >/dev/null
jq -e 'any(.selected.actions[]; .display_text | contains("Нужно добавить задачу на алерты"))' "$session/derived/synthesis-simple/extractive/evidence_notes.json" >/dev/null
jq -e 'any(.selected.decisions[]; .display_text | contains("Решили оставить ретро"))' "$session/derived/synthesis-simple/extractive/evidence_notes.json" >/dev/null
jq -e 'all(.selected.outline_blocks[].representatives[]?; (((.text | contains("Давайте перейдем")) or (.text | contains("Давайте проголосуем"))) | not))' "$session/derived/synthesis-simple/extractive/evidence_notes.json" >/dev/null
grep -q 'utt_simple_003' "$session/derived/synthesis-simple/extractive/notes.md"
grep -q 'utt_simple_006' "$session/derived/synthesis-simple/extractive/notes.md"
! grep -q 'Надо подумать' "$session/derived/synthesis-simple/extractive/notes.md"
! grep -q 'Вопрос по Kubernetes' "$session/derived/synthesis-simple/extractive/notes.md"
! grep -q 'Давайте перейдем к следующему блоку' "$session/derived/synthesis-simple/extractive/notes.md"
! grep -q 'Давайте проголосуем' "$session/derived/synthesis-simple/extractive/notes.md"
! grep -Eq '^### .*: (если|есть|меня|потому)(, (если|есть|меня|потому))*$' "$session/derived/synthesis-simple/extractive/notes.md"

"$repo_root/scripts/report-session-quality.py" \
  "$session" \
  --label "session=smoke fixture" \
  --out-dir "$session/derived/session-quality" \
  --write-session-readiness >/dev/null
[[ -s "$session/derived/session-quality/session_quality_report.json" ]]
[[ -s "$session/derived/session-quality/session_quality_report.csv" ]]
[[ -s "$session/derived/session-quality/session_quality_report.md" ]]
[[ -s "$session/derived/readiness/session_readiness.json" ]]
[[ -s "$session/derived/readiness/session_readiness.md" ]]
jq -e '.schema == "murmurmark.session_quality_report/v1"' "$session/derived/session-quality/session_quality_report.json" >/dev/null
jq -e '.summary.session_count == 1' "$session/derived/session-quality/session_quality_report.json" >/dev/null
jq -e '.sessions[0].label == "smoke fixture"' "$session/derived/session-quality/session_quality_report.json" >/dev/null
jq -e '.sessions[0].pipeline_status == "partial"' "$session/derived/session-quality/session_quality_report.json" >/dev/null
jq -e '.sessions[0].use_gate == "pipeline_incomplete"' "$session/derived/session-quality/session_quality_report.json" >/dev/null
jq -e '.schema == "murmurmark.session_readiness/v1" and .use_gate == "pipeline_incomplete"' "$session/derived/readiness/session_readiness.json" >/dev/null
jq -e '(.export_blockers | index("pipeline_incomplete")) and (.review_blockers | index("pipeline_incomplete"))' "$session/derived/readiness/session_readiness.json" >/dev/null
jq -e 'any(.use_gate_reasons[]; .id == "pipeline_incomplete" and .severity == "block")' "$session/derived/readiness/session_readiness.json" >/dev/null
jq -e 'any(.next_commands[]; .id == "process_session" and (.command | contains("murmurmark process")))' "$session/derived/readiness/session_readiness.json" >/dev/null
rg -n 'Next Commands' "$session/derived/readiness/session_readiness.md" >/dev/null
rg -n 'murmurmark process' "$session/derived/readiness/session_readiness.md" >/dev/null
python3 - "$repo_root" <<'PY'
import importlib.util
from pathlib import Path
import sys

repo_root = Path(sys.argv[1])
module_path = repo_root / "scripts/report-session-quality.py"
spec = importlib.util.spec_from_file_location("report_session_quality", module_path)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)
commands = module.readiness_next_commands(
    Path("sessions/review-session"),
    {"use_gate": "review_first", "review_blockers": ["risk:audio_review_probable_errors"]},
)
ids = [item["id"] for item in commands]
assert ids == ["review_plan", "review_workspace", "review_workspace_apply", "review_apply"], ids
assert any("murmurmark review workspace --session sessions/review-session" == item["command"] for item in commands)
assert any("murmurmark review apply" == item["command"] for item in commands)
PY
export_block_dir="$workdir/export-blocked"
if "$repo_root/scripts/export-session-bundle.py" "$session" --out-dir "$export_block_dir" >/dev/null 2>&1; then
  echo "expected export to block incomplete session" >&2
  exit 1
fi
[[ -s "$export_block_dir/$(basename "$session").export_blocked.json" ]]
jq -e '.status == "blocked" and (.blockers | index("pipeline_incomplete")) and (.readiness.export_blockers | index("pipeline_incomplete"))' \
  "$export_block_dir/$(basename "$session").export_blocked.json" >/dev/null
jq -e '.next | contains("murmurmark process")' "$export_block_dir/$(basename "$session").export_blocked.json" >/dev/null
export_force_dir="$workdir/export-forced"
export_stdout="$workdir/export_forced_stdout.txt"
"$bin" export "$session" --force --out-dir "$export_force_dir" --include-json >"$export_stdout"
[[ -s "$export_force_dir/$(basename "$session")/export_manifest.json" ]]
rg -n '^export:|manifest:|retention plan|retention payload' "$export_stdout" >/dev/null
jq -e '.schema == "murmurmark.export_manifest/v1" and (.status | startswith("exported")) and (.files.transcript_md.path | type == "string")' \
  "$export_force_dir/$(basename "$session")/export_manifest.json" >/dev/null

audit_python=""
if [[ -x "$repo_root/.venv/bin/python" ]] && "$repo_root/.venv/bin/python" - <<'PY' >/dev/null 2>&1
import librosa
import numpy
import scipy
import soundfile
PY
then
  audit_python="$repo_root/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1 && python3 - <<'PY' >/dev/null 2>&1
import librosa
import numpy
import scipy
import soundfile
PY
then
  audit_python="$(command -v python3)"
fi

if [[ -n "$audit_python" ]]; then
  "$audit_python" - "$repo_root/scripts/audit-audio-review-pack.py" <<'PY'
import importlib.util
import sys

path = sys.argv[1]
spec = importlib.util.spec_from_file_location("audio_review_audit", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

base = {
    "remote_duplicate": 0,
    "remote_leak": 0,
    "asr_noise": 0,
    "lost_me": 0,
    "double_talk": 0,
    "timing_overlap": 0,
    "likely_reliable": 65,
}
assert module.classify(base, False)["verdict"] == "likely_reliable"
competing = dict(base)
competing["remote_leak"] = 60
assert module.classify(competing, False)["verdict"] == "needs_stronger_audio_judge"
benign_tie = dict(base)
benign_tie["likely_reliable"] = 75
benign_tie["timing_overlap"] = 75
assert module.classify(benign_tie, False)["verdict"] == "likely_reliable"
PY

  group_session="$workdir/group-session"
  group_resolved="$group_session/derived/transcript-simple/whisper-cpp/resolved"
  mkdir -p \
    "$group_session/audio/mic" \
    "$group_session/audio/remote" \
    "$group_session/derived/preprocess/audio" \
    "$group_session/derived/preprocess/echo" \
    "$group_resolved"

  ffmpeg -y -hide_banner -loglevel error \
    -f lavfi -i 'aevalsrc=0.18*sin(2*PI*600*t)*between(t\,0\,3)+0.18*sin(2*PI*500*t)*between(t\,5\,8)+0.18*sin(2*PI*700*t)*between(t\,10\,11)+0.18*sin(2*PI*650*t)*between(t\,12\,13.6):s=16000:d=16' \
    -c:a pcm_s16le "$group_session/audio/remote/000001.caf"
  ffmpeg -y -hide_banner -loglevel error \
    -f lavfi -i 'aevalsrc=0.12*sin(2*PI*600*t)*between(t\,0\,3)+0.18*sin(2*PI*1000*t)*between(t\,5\,8)+0.18*sin(2*PI*900*t)*between(t\,13\,14.2):s=16000:d=16' \
    -c:a pcm_s16le "$group_session/audio/mic/000001.caf"
  jq -n '{
    schema: "murmurmark.session/v1",
    session_id: "group-fixture",
    status: "completed",
    capture_mode: "fixture",
    created_at: "2026-01-01T00:00:00Z",
    files: {
      mic: [{path: "audio/mic/000001.caf"}],
      remote: [{path: "audio/remote/000001.caf"}]
    },
    health: {summary: "ok", warnings: []}
  }' >"$group_session/session.json"
  ffmpeg -y -hide_banner -loglevel error \
    -f lavfi -i 'aevalsrc=0.18*sin(2*PI*1000*t)*between(t\,5\,8)+0.18*sin(2*PI*900*t)*between(t\,13\,14.2):s=16000:d=16' \
    -c:a pcm_s16le "$group_session/derived/preprocess/audio/mic_clean_local_fir.wav"
  ffmpeg -y -hide_banner -loglevel error \
    -f lavfi -i 'aevalsrc=0.18*sin(2*PI*1000*t)*between(t\,5\,8)+0.18*sin(2*PI*900*t)*between(t\,13\,14.2):s=16000:d=16' \
    -c:a pcm_s16le "$group_session/derived/preprocess/audio/mic_role_masked_for_asr.wav"

  cat >"$group_session/derived/preprocess/echo/speaker_state.jsonl" <<'EOF'
{"start":0.0,"end":3.0,"state":"remote_only","confidence":0.95,"remote_db":-18,"mic_db":-28}
{"start":5.0,"end":8.0,"state":"double_talk","confidence":0.95,"remote_db":-18,"mic_db":-18}
{"start":10.0,"end":11.0,"state":"remote_only","confidence":0.95,"remote_db":-18,"mic_db":-80}
{"start":12.0,"end":13.6,"state":"remote_only","confidence":0.90,"remote_db":-18,"mic_db":-24}
{"start":13.0,"end":14.2,"state":"local_only","confidence":0.90,"remote_db":-80,"mic_db":-18}
EOF

  jq -n '{
    schema: "murmurmark.local_fir_report/v1",
    summary: {median_delay_ms: 0}
  }' >"$group_session/derived/preprocess/echo/local_fir_report.json"
  jq -n '{
    schema: "murmurmark.clean_dialogue/v1",
    session: "group-fixture",
    utterances: [
      {id: "utt_dup_me", start: 0.5, end: 2.5, source_track: "mic", speaker_label: "Me", role: "Me", text: "Надо проверить deploy.", quality: {needs_review: false}},
      {id: "utt_dup_remote", start: 0.3, end: 2.7, source_track: "remote", speaker_label: "Colleagues", role: "Colleagues", text: "Надо проверить deploy.", quality: {needs_review: false}},
      {id: "utt_dt_me", start: 5.0, end: 7.0, source_track: "mic", speaker_label: "Me", role: "Me", text: "Я возьму логи.", quality: {needs_review: false}},
      {id: "utt_dt_remote", start: 5.2, end: 7.2, source_track: "remote", speaker_label: "Colleagues", role: "Colleagues", text: "Давайте обсудим релиз.", quality: {needs_review: false}},
      {id: "utt_noise_me", start: 10.0, end: 10.8, source_track: "mic", speaker_label: "Me", role: "Me", text: "Окей.", quality: {needs_review: false}},
      {id: "utt_noise_remote", start: 10.0, end: 11.0, source_track: "remote", speaker_label: "Colleagues", role: "Colleagues", text: "Проверим после созвона.", quality: {needs_review: false}},
      {id: "utt_timing_remote", start: 12.0, end: 13.6, source_track: "remote", speaker_label: "Colleagues", role: "Colleagues", text: "Закроем вопрос по квотам.", quality: {needs_review: false}},
      {id: "utt_timing_me", start: 13.0, end: 14.2, source_track: "mic", speaker_label: "Me", role: "Me", text: "Я понял.", quality: {needs_review: false}}
    ]
  }' >"$group_resolved/clean_dialogue.shadow_v2.json"
  jq -n '{
    schema: "murmurmark.transcript_overlaps/v1",
    session: "group-fixture",
    overlaps: [
      {left_utterance_id: "utt_dup_me", right_utterance_id: "utt_dup_remote", start: 0.5, end: 2.5, duration_sec: 2.0},
      {left_utterance_id: "utt_dt_me", right_utterance_id: "utt_dt_remote", start: 5.2, end: 7.0, duration_sec: 1.8},
      {left_utterance_id: "utt_noise_me", right_utterance_id: "utt_noise_remote", start: 10.0, end: 10.8, duration_sec: 0.8},
      {left_utterance_id: "utt_timing_remote", right_utterance_id: "utt_timing_me", start: 13.0, end: 13.6, duration_sec: 0.6}
    ]
  }' >"$group_resolved/overlaps.shadow_v2.json"
  jq -n '{
    schema: "murmurmark.simple_transcript_quality/v1",
    utterances: 8,
    needs_review_count: 0,
    cross_role_overlap_gt2_count: 1,
    cross_role_overlap_gt2_seconds: 2,
    remote_duplicate_in_me_seconds: 2,
    unrepaired_long_mic_crossings_count: 0,
    local_only_island_recall: 1.0
  }' >"$group_resolved/quality_report.shadow_v2.json"
  jq -n '{schema: "murmurmark.role_decisions/v1", decisions: []}' >"$group_resolved/role_decisions.shadow_v2.json"
  jq -n '{
    schema: "murmurmark.timeline_repair/v1",
    enabled: true,
    parameters: {repair_profile: "shadow_v2"},
    metrics: {
      local_only_island_count: 3,
      local_only_island_recovered_count: 1,
      local_only_island_recall: 0.333333,
      long_mic_segments_count: 3,
      long_mic_segments_crossing_remote_count: 3,
      long_mic_segments_repaired_count: 3,
      unrepaired_long_mic_crossings_count: 0
    }
  }' >"$group_resolved/timeline_repair_report.shadow_v2.json"
  cat >"$group_resolved/timeline_repair_examples.shadow_v2.jsonl" <<'EOF'
{"action":"split","parent_candidate_id":"cand_mic_fixture_001","parent_start_ms":5000,"parent_end_ms":8000,"parent_text":"Я возьму логи.","remote_overlaps":[],"local_intervals":[[5000,5800]],"local_islands":[[5000,5800]],"children":[{"candidate_id":"cand_mic_repair_fixture_001","start_ms":5000,"end_ms":5800,"action":"micro_reasr","text":"Я возьму логи."}]}
{"action":"drop","parent_candidate_id":"cand_mic_fixture_002","parent_start_ms":13000,"parent_end_ms":14200,"parent_text":"Я понял.","remote_overlaps":[],"local_intervals":[[13000,14200]],"local_islands":[[13000,14200]],"children":[]}
{"action":"drop","parent_candidate_id":"cand_mic_fixture_boundary","parent_start_ms":15000,"parent_end_ms":19000,"parent_text":"короткий граничный хвост","remote_overlaps":[{"candidate_id":"cand_remote_boundary","start_ms":14000,"end_ms":14750,"guarded_start_ms":13750,"guarded_end_ms":15000,"overlap_ms":0,"guarded_overlap_ms":0,"text":"удаленная фраза"}],"local_intervals":[[15000,15650]],"local_islands":[[15000,15650]],"children":[]}
EOF

  local_recall_cli_output="$("$bin" audit local-recall "$group_session" --profile shadow_v2)"
  echo "$local_recall_cli_output" | grep -q '^audit:$'
  echo "$local_recall_cli_output" | grep -q '  kind: local_recall'
  echo "$local_recall_cli_output" | grep -q '  missing_islands: 2'
  local_recall_summary="$group_session/derived/audit/local-recall/local_recall_audit.json"
  [[ -s "$local_recall_summary" ]]
  [[ -s "$group_session/derived/audit/local-recall/local_recall_items.jsonl" ]]
  [[ -s "$group_session/derived/audit/local-recall/local_recall_review.md" ]]
  jq -e '.schema == "murmurmark.local_recall_audit/v1"' "$local_recall_summary" >/dev/null
  jq -e '.summary.audited_missing_island_count == 2' "$local_recall_summary" >/dev/null
  jq -e '.summary.blocking_low_local_recall == true' "$local_recall_summary" >/dev/null
  jq -s 'any(.[]; .label == "possible_lost_me")' "$group_session/derived/audit/local-recall/local_recall_items.jsonl" >/dev/null
  jq -s 'any(.[]; .label == "likely_harmless_boundary_fragment" and .boundary.boundary_fragment == true)' "$group_session/derived/audit/local-recall/local_recall_items.jsonl" >/dev/null

  group_overlap_cli_output="$("$bin" audit group-overlaps "$group_session" \
    --profile shadow_v2 \
    --min-overlap-sec 0.5 \
    --review-threshold-sec 2.0 \
    --write-clips \
    --max-clips 10)"
  echo "$group_overlap_cli_output" | grep -q '^audit:$'
  echo "$group_overlap_cli_output" | grep -q '  kind: group_overlaps'
  echo "$group_overlap_cli_output" | grep -q '  overlaps: 4 /'

  group_audit="$group_session/derived/audit/group-overlaps/group_overlap_audit.jsonl"
  group_summary="$group_session/derived/audit/group-overlaps/group_overlap_summary.json"
  [[ -s "$group_audit" ]]
  [[ -s "$group_summary" ]]
  [[ -s "$group_session/derived/audit/group-overlaps/group_overlap_review.md" ]]
  [[ -s "$group_session/derived/audit/group-overlaps/group_overlap_patch_suggestions.jsonl" ]]
  jq -e '.schema == "murmurmark.group_overlap_summary/v1"' "$group_summary" >/dev/null
  jq -e '.classified.total_overlap_count == 4' "$group_summary" >/dev/null
  jq -s 'any(.[]; .classification.label == "probable_duplicate")' "$group_audit" >/dev/null
  jq -s 'any(.[]; .classification.label == "probable_double_talk")' "$group_audit" >/dev/null
  jq -s 'any(.[]; .classification.label == "probable_asr_noise")' "$group_audit" >/dev/null
  jq -s 'any(.[]; .classification.label == "probable_timing_overlap")' "$group_audit" >/dev/null
  compgen -G "$group_session/derived/audit/group-overlaps/clips/*.wav" >/dev/null

  tmp_json="$workdir/group-dialogue-extra.json"
  jq '.utterances += [
    {id: "utt_repeat_remote", start: 14.5, end: 15.4, source_track: "remote", speaker_label: "Colleagues", role: "Colleagues", text: "Надо идти к IAM.", quality: {needs_review: false}},
    {id: "utt_repeat_me", start: 15.2, end: 17.6, source_track: "mic", speaker_label: "Me", role: "Me", text: "Да, надо идти к IAM, но сначала проверим права доступа.", quality: {needs_review: false}},
    {id: "utt_action_remote", start: 18.0, end: 19.0, source_track: "remote", speaker_label: "Colleagues", role: "Colleagues", text: "Надо проверить deploy.", quality: {needs_review: false}},
    {id: "utt_action_me", start: 18.1, end: 19.2, source_track: "mic", speaker_label: "Me", role: "Me", text: "Надо проверить deploy и права.", quality: {needs_review: false}},
    {id: "utt_audio_dup_remote", start: 20.0, end: 22.0, source_track: "remote", speaker_label: "Colleagues", role: "Colleagues", text: "Обновим pipeline.", quality: {needs_review: false}},
    {id: "utt_audio_dup_me", start: 20.1, end: 22.1, source_track: "mic", speaker_label: "Me", role: "Me", text: "Обновим pipeline.", quality: {needs_review: false}},
    {id: "utt_audio_uncertain_remote", start: 23.0, end: 25.0, source_track: "remote", speaker_label: "Colleagues", role: "Colleagues", text: "Там есть спорный кусок.", quality: {needs_review: false}},
    {id: "utt_audio_uncertain_me", start: 23.1, end: 25.1, source_track: "mic", speaker_label: "Me", role: "Me", text: "Я уточню отдельно.", quality: {needs_review: false}},
    {id: "utt_audio_judge_remote", start: 26.0, end: 28.0, source_track: "remote", speaker_label: "Colleagues", role: "Colleagues", text: "Релиз готов.", quality: {needs_review: false}},
    {id: "utt_audio_judge_me", start: 26.1, end: 28.1, source_track: "mic", speaker_label: "Me", role: "Me", text: "Релиз готов.", quality: {needs_review: false}},
    {id: "utt_audio_judge_v4_remote", start: 29.0, end: 31.0, source_track: "remote", speaker_label: "Colleagues", role: "Colleagues", text: "Проверим логи.", quality: {needs_review: false}},
    {id: "utt_audio_judge_v4_me", start: 29.1, end: 31.1, source_track: "mic", speaker_label: "Me", role: "Me", text: "Проверим логи.", quality: {needs_review: false}}
  ]' "$group_resolved/clean_dialogue.shadow_v2.json" >"$tmp_json"
  mv "$tmp_json" "$group_resolved/clean_dialogue.shadow_v2.json"

  mkdir -p "$group_session/derived/synthesis-simple/extractive"
  jq -n '{
    schema: "murmurmark.evidence_notes/v2",
    selected: {
      actions: [{evidence_utterance_ids: ["utt_action_me"]}],
      decisions: [],
      risks: [],
      open_questions: [],
      outline_blocks: []
    }
  }' >"$group_session/derived/synthesis-simple/extractive/evidence_notes.json"

  tmp_json="$workdir/group-audit-confidence.jsonl"
  jq -c 'if .utterances.me.id == "utt_noise_me" then
    .classification.confidence = 0.9
    | .classification.top_score = 90
    | .scores.probable_asr_noise = 90
    else . end' "$group_audit" >"$tmp_json"
  mv "$tmp_json" "$group_audit"

  cat >>"$group_audit" <<'EOF'
{"schema":"murmurmark.group_overlap_audit/v1","id":"ov_manual_duplicate","session_id":"group-fixture","profile":"shadow_v2","interval":{"start":0.5,"end":2.5,"duration_sec":2.0,"severity":"high","source_overlap_index":8},"utterances":{"me":{"id":"utt_dup_me","start":0.5,"end":2.5,"text":"Надо проверить deploy.","needs_review":false},"remote":{"id":"utt_dup_remote","start":0.3,"end":2.7,"text":"Надо проверить deploy.","needs_review":false}},"features":{"speaker_state":{"local_only_ratio":0.0,"remote_only_ratio":1.0,"double_talk_ratio":0.0,"remote_active_ratio":1.0,"local_score_mean":0.0,"local_score_max":0.0},"audio":{"rms_db":{"mic_role_masked":-80},"energy_ratios_db":{},"xcorr":{"raw":{"max_corr":0.9}}},"text":{"similarity_max":1.0,"token_containment":1.0,"domain_term_overlap":1.0,"is_short_backchannel":false,"me_token_count":3,"remote_token_count":3},"interval":{"me_coverage":1.0,"time_overlap_ratio":1.0,"near_boundary":true}},"scores":{"local_evidence":0,"remote_evidence":100,"audio_leak":100,"text_duplicate":100,"probable_duplicate":100,"probable_remote_leak":0,"probable_double_talk":0,"probable_timing_overlap":0,"probable_asr_noise":0},"classification":{"label":"probable_duplicate","confidence":0.96,"top_score":100,"second_score":0,"action_suggestion":"drop_me_duplicate"}}
{"schema":"murmurmark.group_overlap_audit/v1","id":"ov_manual_double_talk","session_id":"group-fixture","profile":"shadow_v2","interval":{"start":5.2,"end":7.0,"duration_sec":1.8,"severity":"medium","source_overlap_index":9},"utterances":{"me":{"id":"utt_dt_me","start":5.0,"end":7.0,"text":"Я возьму логи.","needs_review":false},"remote":{"id":"utt_dt_remote","start":5.2,"end":7.2,"text":"Давайте обсудим релиз.","needs_review":false}},"features":{"speaker_state":{"local_only_ratio":0.0,"remote_only_ratio":0.0,"double_talk_ratio":1.0,"remote_active_ratio":1.0,"local_score_mean":0.7,"local_score_max":0.7},"audio":{"rms_db":{"mic_role_masked":-18},"energy_ratios_db":{},"xcorr":{"raw":{"max_corr":0.2}}},"text":{"similarity_max":0.1,"token_containment":0.0,"domain_term_overlap":0.0,"is_short_backchannel":false,"me_token_count":3,"remote_token_count":3},"interval":{"me_coverage":0.9,"time_overlap_ratio":0.9,"near_boundary":false}},"scores":{"local_evidence":70,"remote_evidence":30,"audio_leak":10,"text_duplicate":0,"probable_duplicate":0,"probable_remote_leak":0,"probable_double_talk":90,"probable_timing_overlap":0,"probable_asr_noise":0},"classification":{"label":"probable_double_talk","confidence":0.9,"top_score":90,"second_score":0,"action_suggestion":"mark_double_talk"}}
{"schema":"murmurmark.group_overlap_audit/v1","id":"ov_manual_noise","session_id":"group-fixture","profile":"shadow_v2","interval":{"start":10.0,"end":10.8,"duration_sec":0.8,"severity":"medium","source_overlap_index":7},"utterances":{"me":{"id":"utt_noise_me","start":10.0,"end":10.8,"text":"Окей.","needs_review":false},"remote":{"id":"utt_noise_remote","start":10.0,"end":11.0,"text":"Проверим после созвона.","needs_review":false}},"features":{"speaker_state":{"local_only_ratio":0.0,"remote_only_ratio":1.0,"double_talk_ratio":0.0,"remote_active_ratio":1.0,"local_score_mean":0.0,"local_score_max":0.0},"audio":{"rms_db":{"mic_role_masked":-80},"energy_ratios_db":{},"xcorr":{"raw":{"max_corr":0.1}}},"text":{"similarity_max":0.1,"token_containment":0.0,"domain_term_overlap":0.0,"is_short_backchannel":true,"me_token_count":1,"remote_token_count":3},"interval":{"me_coverage":1.0,"time_overlap_ratio":1.0,"near_boundary":true}},"scores":{"local_evidence":0,"remote_evidence":70,"audio_leak":10,"text_duplicate":0,"probable_duplicate":0,"probable_remote_leak":0,"probable_double_talk":0,"probable_timing_overlap":0,"probable_asr_noise":90},"classification":{"label":"probable_asr_noise","confidence":0.9,"top_score":90,"second_score":0,"action_suggestion":"drop_me_noise"}}
{"schema":"murmurmark.group_overlap_audit/v1","id":"ov_manual_repeat","session_id":"group-fixture","profile":"shadow_v2","interval":{"start":15.2,"end":15.4,"duration_sec":0.2,"severity":"low","source_overlap_index":5},"utterances":{"me":{"id":"utt_repeat_me","start":15.2,"end":17.6,"text":"Да, надо идти к IAM, но сначала проверим права доступа.","needs_review":false},"remote":{"id":"utt_repeat_remote","start":14.5,"end":15.4,"text":"Надо идти к IAM.","needs_review":false}},"features":{"speaker_state":{"local_only_ratio":0.3,"remote_only_ratio":0.2,"double_talk_ratio":0.5,"remote_active_ratio":0.7,"local_score_mean":0.65,"local_score_max":0.8},"audio":{"rms_db":{"mic_role_masked":-20},"energy_ratios_db":{},"xcorr":{"raw":{"max_corr":0.2}}},"text":{"similarity_max":0.9,"token_containment":1.0,"domain_term_overlap":1.0,"is_short_backchannel":false,"me_token_count":10,"remote_token_count":4},"interval":{"me_coverage":0.95,"time_overlap_ratio":0.95,"near_boundary":true}},"scores":{"local_evidence":70,"remote_evidence":65,"audio_leak":20,"text_duplicate":95,"probable_duplicate":100,"probable_remote_leak":0,"probable_double_talk":0,"probable_timing_overlap":0,"probable_asr_noise":0},"classification":{"label":"probable_duplicate","confidence":0.99,"top_score":100,"second_score":0,"action_suggestion":"drop_me_duplicate"}}
{"schema":"murmurmark.group_overlap_audit/v1","id":"ov_manual_action","session_id":"group-fixture","profile":"shadow_v2","interval":{"start":18.1,"end":19.0,"duration_sec":0.9,"severity":"medium","source_overlap_index":6},"utterances":{"me":{"id":"utt_action_me","start":18.1,"end":19.2,"text":"Надо проверить deploy и права.","needs_review":false},"remote":{"id":"utt_action_remote","start":18.0,"end":19.0,"text":"Надо проверить deploy.","needs_review":false}},"features":{"speaker_state":{"local_only_ratio":0.0,"remote_only_ratio":0.9,"double_talk_ratio":0.0,"remote_active_ratio":0.9,"local_score_mean":0.0,"local_score_max":0.0},"audio":{"rms_db":{"mic_role_masked":-70},"energy_ratios_db":{},"xcorr":{"raw":{"max_corr":0.5}}},"text":{"similarity_max":0.95,"token_containment":1.0,"domain_term_overlap":1.0,"is_short_backchannel":false,"me_token_count":5,"remote_token_count":3},"interval":{"me_coverage":0.95,"time_overlap_ratio":0.95,"near_boundary":true}},"scores":{"local_evidence":0,"remote_evidence":80,"audio_leak":80,"text_duplicate":95,"probable_duplicate":100,"probable_remote_leak":0,"probable_double_talk":0,"probable_timing_overlap":0,"probable_asr_noise":0},"classification":{"label":"probable_duplicate","confidence":0.99,"top_score":100,"second_score":0,"action_suggestion":"drop_me_duplicate"}}
EOF

  shadow_dialogue_sha_before="$(shasum -a 256 "$group_resolved/clean_dialogue.shadow_v2.json" | awk '{print $1}')"
  "$repo_root/scripts/apply-audit-cleanup.py" "$group_session" \
    --input-profile shadow_v2 \
    --output-profile audit_cleanup_v1 \
    --mode conservative >/dev/null
  shadow_dialogue_sha_after="$(shasum -a 256 "$group_resolved/clean_dialogue.shadow_v2.json" | awk '{print $1}')"
  [[ "$shadow_dialogue_sha_before" == "$shadow_dialogue_sha_after" ]]

  cleanup_dialogue="$group_resolved/clean_dialogue.audit_cleanup_v1.json"
  cleanup_quality="$group_resolved/quality_report.audit_cleanup_v1.json"
  cleanup_report="$group_session/derived/transcript-simple/whisper-cpp/audit-cleanup/audit_cleanup_report.audit_cleanup_v1.json"
  cleanup_patches="$group_session/derived/transcript-simple/whisper-cpp/audit-cleanup/audit_cleanup_patches.audit_cleanup_v1.jsonl"
  cleanup_rejected="$group_session/derived/transcript-simple/whisper-cpp/audit-cleanup/audit_cleanup_rejected_patches.audit_cleanup_v1.jsonl"
  [[ -s "$cleanup_dialogue" ]]
  [[ -s "$cleanup_quality" ]]
  [[ -s "$cleanup_report" ]]
  [[ -s "$cleanup_patches" ]]
  [[ -s "$cleanup_rejected" ]]
  jq -e '.gates.passed == true' "$cleanup_report" >/dev/null
  jq -e 'all(.utterances[]; .id != "utt_dup_me" and .id != "utt_noise_me")' "$cleanup_dialogue" >/dev/null
  jq -e 'any(.utterances[]; .id == "utt_dt_me" and (.quality.audit_cleanup.labels | index("probable_double_talk")))' "$cleanup_dialogue" >/dev/null
  jq -e 'any(.utterances[]; .id == "utt_timing_me" and (.quality.audit_cleanup.labels | index("probable_timing_overlap")))' "$cleanup_dialogue" >/dev/null
  jq -e 'any(.utterances[]; .id == "utt_repeat_me")' "$cleanup_dialogue" >/dev/null
  jq -e 'any(.utterances[]; .id == "utt_action_me")' "$cleanup_dialogue" >/dev/null
  jq -e 'any(.utterances[]; .id == "utt_audio_dup_me")' "$cleanup_dialogue" >/dev/null
  jq -s 'all(.[]; (.reason | length) > 0 and (.evidence | type) == "object")' "$cleanup_patches" >/dev/null
  jq -s 'any(.[]; .target.utterance_id == "utt_repeat_me" and .safety_checks.intentional_repeat_candidate == true)' "$cleanup_rejected" >/dev/null
  jq -s 'any(.[]; .target.utterance_id == "utt_action_me" and .safety_checks.has_protected_action_decision_risk_marker == true)' "$cleanup_rejected" >/dev/null

  "$repo_root/scripts/synthesize-simple-extractive.py" "$group_session" --transcript-profile audit_cleanup_v1 >/dev/null
  jq -e '.selected_transcript_profile == "audit_cleanup_v1"' "$group_session/derived/synthesis-simple/extractive/quality_verdict.audit_cleanup_v1.json" >/dev/null

  audio_review_cli_output="$("$bin" audit audio-review "$group_session" \
    --profile audit_cleanup_v1 \
    --write-clips \
    --max-items 20)"
  echo "$audio_review_cli_output" | grep -q '^audit:$'
  echo "$audio_review_cli_output" | grep -q '  kind: audio_review'
  echo "$audio_review_cli_output" | grep -q '  items:'
  review_pack="$group_session/derived/audit/audio-review-pack/review_pack_items.jsonl"
  review_summary="$group_session/derived/audit/audio-review-pack/audio_review_summary.json"
  [[ -s "$review_pack" ]]
  [[ -s "$group_session/derived/audit/audio-review-pack/review_pack_summary.json" ]]
  [[ -s "$review_summary" ]]
  [[ -s "$group_session/derived/audit/audio-review-pack/audio_review_report.md" ]]
  jq -s 'length >= 4' "$review_pack" >/dev/null
  jq -e '.schema == "murmurmark.audio_review_summary/v1"' "$review_summary" >/dev/null
  jq -e '.items >= 4' "$review_summary" >/dev/null
  jq -e '.by_verdict.probable_transcript_error.count >= 1' "$review_summary" >/dev/null
  jq -e '.by_verdict.likely_reliable.count >= 1 or .by_verdict.needs_stronger_audio_judge.count >= 1' "$review_summary" >/dev/null
  compgen -G "$group_session/derived/audit/audio-review-pack/clips/*.wav" >/dev/null

  cat >>"$group_session/derived/audit/audio-review-pack/audio_review_audit.jsonl" <<'EOF'
{"schema":"murmurmark.audio_review_audit/v1","id":"arp_manual_v2_duplicate","session_id":"group-fixture","profile":"audit_cleanup_v1","interval":{"start":20.1,"end":22.1,"duration_sec":2.0,"start_time":"00:20","end_time":"00:22"},"source_reasons":["manual_v2_fixture"],"utterance_ids":["utt_audio_dup_remote","utt_audio_dup_me"],"utterances":[{"id":"utt_audio_dup_remote","role":"Colleagues","source_track":"remote","start":20.0,"end":22.0,"text":"Обновим pipeline.","needs_review":false},{"id":"utt_audio_dup_me","role":"Me","source_track":"mic","start":20.1,"end":22.1,"text":"Обновим pipeline.","needs_review":false}],"features":{"rms_db":{"remote":-20,"mic_raw":-32,"mic_clean":-60,"mic_role_masked":-65},"energy_delta_db":{"mic_clean_vs_raw":-28,"role_masked_vs_raw":-33},"xcorr":{"raw":{"max_corr":0.8},"clean":{"max_corr":0.1},"role_masked":{"max_corr":0.05}},"spectral_cosine":{"raw":0.9,"clean":0.2,"role_masked":0.1},"text":{"similarity":1.0,"sequence_ratio":1.0,"containment":1.0,"jaccard":1.0,"me_text":"Обновим pipeline.","remote_text":"Обновим pipeline."},"source_reasons":["manual_v2_fixture"]},"scores":{"local_support":10,"remote_similarity":95,"remote_duplicate":95,"remote_leak":0,"asr_noise":0,"double_talk":0,"timing_overlap":0,"lost_me":0,"likely_reliable":0},"classification":{"label":"remote_duplicate","verdict":"probable_transcript_error","confidence":0.94,"reason":"fixture","top_score":94,"second_score":10}}
{"schema":"murmurmark.audio_review_audit/v1","id":"arp_manual_v2_uncertain","session_id":"group-fixture","profile":"audit_cleanup_v1","interval":{"start":23.1,"end":25.1,"duration_sec":2.0,"start_time":"00:23","end_time":"00:25"},"source_reasons":["manual_v2_fixture"],"utterance_ids":["utt_audio_uncertain_remote","utt_audio_uncertain_me"],"utterances":[{"id":"utt_audio_uncertain_remote","role":"Colleagues","source_track":"remote","start":23.0,"end":25.0,"text":"Там есть спорный кусок.","needs_review":false},{"id":"utt_audio_uncertain_me","role":"Me","source_track":"mic","start":23.1,"end":25.1,"text":"Я уточню отдельно.","needs_review":false}],"features":{"rms_db":{"remote":-30,"mic_raw":-36,"mic_clean":-35,"mic_role_masked":-35},"energy_delta_db":{"mic_clean_vs_raw":1,"role_masked_vs_raw":1},"xcorr":{"raw":{"max_corr":0.2},"clean":{"max_corr":0.2},"role_masked":{"max_corr":0.2}},"spectral_cosine":{"raw":0.3,"clean":0.3,"role_masked":0.3},"text":{"similarity":0.1,"sequence_ratio":0.1,"containment":0.0,"jaccard":0.0,"me_text":"Я уточню отдельно.","remote_text":"Там есть спорный кусок."},"source_reasons":["manual_v2_fixture"]},"scores":{"local_support":50,"remote_similarity":20,"remote_duplicate":0,"remote_leak":0,"asr_noise":0,"double_talk":0,"timing_overlap":0,"lost_me":0,"likely_reliable":0},"classification":{"label":"uncertain","verdict":"needs_stronger_audio_judge","confidence":0.5,"reason":"fixture","top_score":50,"second_score":45}}
{"schema":"murmurmark.audio_review_audit/v1","id":"arp_manual_v3_judge_duplicate","session_id":"group-fixture","profile":"audit_cleanup_v1","interval":{"start":26.1,"end":28.1,"duration_sec":2.0,"start_time":"00:26","end_time":"00:28"},"source_reasons":["manual_v3_fixture"],"utterance_ids":["utt_audio_judge_remote","utt_audio_judge_me"],"utterances":[{"id":"utt_audio_judge_remote","role":"Colleagues","source_track":"remote","start":26.0,"end":28.0,"text":"Релиз готов.","needs_review":false},{"id":"utt_audio_judge_me","role":"Me","source_track":"mic","start":26.1,"end":28.1,"text":"Релиз готов.","needs_review":false}],"features":{"rms_db":{"remote":-20,"mic_raw":-32,"mic_clean":-60,"mic_role_masked":-65},"energy_delta_db":{"mic_clean_vs_raw":-28,"role_masked_vs_raw":-33},"xcorr":{"raw":{"max_corr":0.8},"clean":{"max_corr":0.1},"role_masked":{"max_corr":0.05}},"spectral_cosine":{"raw":0.9,"clean":0.2,"role_masked":0.1},"text":{"similarity":1.0,"sequence_ratio":1.0,"containment":1.0,"jaccard":1.0,"me_text":"Релиз готов.","remote_text":"Релиз готов."},"source_reasons":["manual_v3_fixture"]},"scores":{"local_support":10,"remote_similarity":95,"remote_duplicate":95,"remote_leak":0,"asr_noise":0,"double_talk":0,"timing_overlap":0,"lost_me":0,"likely_reliable":0},"classification":{"label":"remote_duplicate","verdict":"probable_transcript_error","confidence":0.78,"reason":"fixture low confidence until audio judge","top_score":78,"second_score":10}}
{"schema":"murmurmark.audio_review_audit/v1","id":"arp_manual_v4_judge_duplicate","session_id":"group-fixture","profile":"audit_cleanup_v1","interval":{"start":29.1,"end":31.1,"duration_sec":2.0,"start_time":"00:29","end_time":"00:31"},"source_reasons":["manual_v4_fixture"],"utterance_ids":["utt_audio_judge_v4_remote","utt_audio_judge_v4_me"],"utterances":[{"id":"utt_audio_judge_v4_remote","role":"Colleagues","source_track":"remote","start":29.0,"end":31.0,"text":"Проверим логи.","needs_review":false},{"id":"utt_audio_judge_v4_me","role":"Me","source_track":"mic","start":29.1,"end":31.1,"text":"Проверим логи.","needs_review":false}],"features":{"rms_db":{"remote":-20,"mic_raw":-32,"mic_clean":-60,"mic_role_masked":-65},"energy_delta_db":{"mic_clean_vs_raw":-28,"role_masked_vs_raw":-33},"xcorr":{"raw":{"max_corr":0.8},"clean":{"max_corr":0.1},"role_masked":{"max_corr":0.05}},"spectral_cosine":{"raw":0.9,"clean":0.2,"role_masked":0.1},"text":{"similarity":1.0,"sequence_ratio":1.0,"containment":1.0,"jaccard":1.0,"me_text":"Проверим логи.","remote_text":"Проверим логи."},"source_reasons":["manual_v4_fixture"]},"scores":{"local_support":10,"remote_similarity":95,"remote_duplicate":95,"remote_leak":0,"asr_noise":0,"double_talk":0,"timing_overlap":0,"lost_me":0,"likely_reliable":0},"classification":{"label":"remote_duplicate","verdict":"probable_transcript_error","confidence":0.82,"reason":"fixture v4 low confidence until audio judge","top_score":82,"second_score":10}}
EOF

  cleanup_v1_sha_before="$(shasum -a 256 "$cleanup_dialogue" | awk '{print $1}')"
  "$repo_root/scripts/apply-audit-cleanup.py" "$group_session" \
    --input-profile audit_cleanup_v1 \
    --output-profile audit_cleanup_v2 \
    --mode conservative >/dev/null
  cleanup_v1_sha_after="$(shasum -a 256 "$cleanup_dialogue" | awk '{print $1}')"
  [[ "$cleanup_v1_sha_before" == "$cleanup_v1_sha_after" ]]

  cleanup_v2_dialogue="$group_resolved/clean_dialogue.audit_cleanup_v2.json"
  cleanup_v2_report="$group_session/derived/transcript-simple/whisper-cpp/audit-cleanup/audit_cleanup_report.audit_cleanup_v2.json"
  cleanup_v2_patches="$group_session/derived/transcript-simple/whisper-cpp/audit-cleanup/audit_cleanup_patches.audit_cleanup_v2.jsonl"
  cleanup_v2_rejected="$group_session/derived/transcript-simple/whisper-cpp/audit-cleanup/audit_cleanup_rejected_patches.audit_cleanup_v2.jsonl"
  [[ -s "$cleanup_v2_dialogue" ]]
  [[ -s "$cleanup_v2_report" ]]
  [[ -s "$cleanup_v2_patches" ]]
  [[ -s "$cleanup_v2_rejected" ]]
  jq -e '.gates.passed == true' "$cleanup_v2_report" >/dev/null
  jq -e '.summary.audio_review_records >= 2 and .summary.audio_review_applied_patches >= 1' "$cleanup_v2_report" >/dev/null
  jq -e 'all(.utterances[]; .id != "utt_audio_dup_me")' "$cleanup_v2_dialogue" >/dev/null
  jq -e 'any(.utterances[]; .id == "utt_audio_uncertain_me" and (.quality.audit_cleanup.labels | index("uncertain")))' "$cleanup_v2_dialogue" >/dev/null
  jq -e 'any(.utterances[]; .id == "utt_audio_judge_me")' "$cleanup_v2_dialogue" >/dev/null
  jq -e 'any(.utterances[]; .id == "utt_repeat_me")' "$cleanup_v2_dialogue" >/dev/null
  jq -e 'any(.utterances[]; .id == "utt_action_me")' "$cleanup_v2_dialogue" >/dev/null
  jq -s 'any(.[]; .target.utterance_id == "utt_audio_dup_me" and .evidence.source == "audio_review")' "$cleanup_v2_patches" >/dev/null

  "$repo_root/scripts/synthesize-simple-extractive.py" "$group_session" --transcript-profile audit_cleanup_v2 >/dev/null
  jq -e '.selected_transcript_profile == "audit_cleanup_v2"' "$group_session/derived/synthesis-simple/extractive/quality_verdict.audit_cleanup_v2.json" >/dev/null

  judge_queue="$workdir/audio_judge_queue.jsonl"
  cat >"$judge_queue" <<'EOF'
{"schema":"murmurmark.audio_judge_v0_queue_prediction/v1","id":"queue_group-session_arp_manual_v3_judge_duplicate","session_id":"group-session","source_audit_id":"arp_manual_v3_judge_duplicate","audio_review_label":"remote_duplicate","audio_review_verdict":"probable_transcript_error","judge_label":"drop_error","judge_confidence":0.99,"probabilities":{"drop_error":0.99,"keep":0.0,"mark_only_error":0.0,"uncertain":0.01},"interval":{"start":26.1,"end":28.1,"duration_sec":2.0,"start_time":"00:26","end_time":"00:28"},"utterance_ids":["utt_audio_judge_remote","utt_audio_judge_me"],"commands":{},"shadow_action":"candidate_future_cleanup_review"}
{"schema":"murmurmark.audio_judge_v0_queue_prediction/v1","id":"queue_group-session_arp_manual_v4_judge_duplicate","session_id":"group-session","source_audit_id":"arp_manual_v4_judge_duplicate","audio_review_label":"remote_duplicate","audio_review_verdict":"probable_transcript_error","judge_label":"drop_error","judge_confidence":0.95,"probabilities":{"drop_error":0.95,"keep":0.0,"mark_only_error":0.0,"uncertain":0.05},"interval":{"start":29.1,"end":31.1,"duration_sec":2.0,"start_time":"00:29","end_time":"00:31"},"utterance_ids":["utt_audio_judge_v4_remote","utt_audio_judge_v4_me"],"commands":{},"shadow_action":"candidate_future_cleanup_review"}
EOF

  cleanup_v2_sha_before="$(shasum -a 256 "$cleanup_v2_dialogue" | awk '{print $1}')"
  "$repo_root/scripts/apply-audit-cleanup.py" "$group_session" \
    --input-profile audit_cleanup_v2 \
    --output-profile audit_cleanup_v3 \
    --mode conservative \
    --audio-judge-queue "$judge_queue" >/dev/null
  cleanup_v2_sha_after="$(shasum -a 256 "$cleanup_v2_dialogue" | awk '{print $1}')"
  [[ "$cleanup_v2_sha_before" == "$cleanup_v2_sha_after" ]]

  cleanup_v3_dialogue="$group_resolved/clean_dialogue.audit_cleanup_v3.json"
  cleanup_v3_report="$group_session/derived/transcript-simple/whisper-cpp/audit-cleanup/audit_cleanup_report.audit_cleanup_v3.json"
  cleanup_v3_patches="$group_session/derived/transcript-simple/whisper-cpp/audit-cleanup/audit_cleanup_patches.audit_cleanup_v3.jsonl"
  [[ -s "$cleanup_v3_dialogue" ]]
  [[ -s "$cleanup_v3_report" ]]
  [[ -s "$cleanup_v3_patches" ]]
  jq -e '.gates.passed == true' "$cleanup_v3_report" >/dev/null
  jq -e '.summary.audio_judge_applied_patches >= 1' "$cleanup_v3_report" >/dev/null
  jq -e 'all(.utterances[]; .id != "utt_audio_judge_me")' "$cleanup_v3_dialogue" >/dev/null
  jq -e 'any(.utterances[]; .id == "utt_audio_judge_v4_me")' "$cleanup_v3_dialogue" >/dev/null
  jq -s 'any(.[]; .target.utterance_id == "utt_audio_judge_me" and .evidence.source == "audio_judge")' "$cleanup_v3_patches" >/dev/null
  "$repo_root/scripts/synthesize-simple-extractive.py" "$group_session" --transcript-profile auto >/dev/null
  jq -e '.selected_transcript_profile == "audit_cleanup_v3"' "$group_session/derived/synthesis-simple/extractive/quality_verdict.json" >/dev/null

  cleanup_v3_sha_before="$(shasum -a 256 "$cleanup_v3_dialogue" | awk '{print $1}')"
  "$repo_root/scripts/apply-audit-cleanup.py" "$group_session" \
    --input-profile audit_cleanup_v3 \
    --output-profile audit_cleanup_v4 \
    --mode conservative \
    --audio-judge-queue "$judge_queue" >/dev/null
  cleanup_v3_sha_after="$(shasum -a 256 "$cleanup_v3_dialogue" | awk '{print $1}')"
  [[ "$cleanup_v3_sha_before" == "$cleanup_v3_sha_after" ]]
  cleanup_v4_dialogue="$group_resolved/clean_dialogue.audit_cleanup_v4.json"
  cleanup_v4_report="$group_session/derived/transcript-simple/whisper-cpp/audit-cleanup/audit_cleanup_report.audit_cleanup_v4.json"
  cleanup_v4_patches="$group_session/derived/transcript-simple/whisper-cpp/audit-cleanup/audit_cleanup_patches.audit_cleanup_v4.jsonl"
  [[ -s "$cleanup_v4_dialogue" ]]
  [[ -s "$cleanup_v4_report" ]]
  [[ -s "$cleanup_v4_patches" ]]
  jq -e '.gates.passed == true and .summary.audio_judge_applied_patches >= 1' "$cleanup_v4_report" >/dev/null
  jq -e 'all(.utterances[]; .id != "utt_audio_judge_v4_me")' "$cleanup_v4_dialogue" >/dev/null
  jq -s 'any(.[]; .target.utterance_id == "utt_audio_judge_v4_me" and .safety_checks.audio_judge_expanded_duplicate_gate_passed == true)' "$cleanup_v4_patches" >/dev/null
  "$repo_root/scripts/synthesize-simple-extractive.py" "$group_session" --transcript-profile auto >/dev/null
  jq -e '.selected_transcript_profile == "audit_cleanup_v4"' "$group_session/derived/synthesis-simple/extractive/quality_verdict.json" >/dev/null

  review_decisions="$workdir/review_decisions.jsonl"
  review_template="$workdir/review_decisions.template.jsonl"
  cat >"$review_template" <<EOF
{"schema":"murmurmark.review_decision/v1","status":"todo","decision":"todo","session_id":"group-session","session":"$group_session","input_profile":"audit_cleanup_v4","source_audit_id":"arp_manual_review_keep","label":"uncertain","verdict":"needs_stronger_audio_judge","review_lane":"classify_audio","review_action":"classify_audio","suggested_decision":"keep_me","suggested_decision_confidence":"low","suggested_decision_reason":"fixture review cli default","me_utterance_ids":["utt_audio_uncertain_me"],"remote_utterance_ids":["utt_audio_uncertain_remote"],"utterance_ids":["utt_audio_uncertain_remote","utt_audio_uncertain_me"],"text":[{"id":"utt_audio_uncertain_remote","role":"remote","source_track":"remote","text":"Там есть спорный кусок."},{"id":"utt_audio_uncertain_me","role":"me","source_track":"mic","text":"Я уточню отдельно."}],"reviewer":"","notes":""}
{"schema":"murmurmark.review_decision/v1","status":"todo","decision":"todo","allowed_decisions":["keep_me","needs_review","skip"],"session_id":"group-session","session":"$group_session","input_profile":"audit_cleanup_v4","cluster_id":"review_cluster_local_001","source":"local_recall","source_audit_id":"local_recall_0001","label":"lost_me","verdict":"needs_stronger_audio_judge","review_lane":"check_local_recall","review_action":"check_lost_local_speech","suggested_decision":"needs_review","suggested_decision_confidence":"medium","suggested_decision_reason":"fixture local recall row","me_utterance_ids":[],"remote_utterance_ids":[],"utterance_ids":[],"interval":{"start":13.0,"end":14.2,"duration_sec":1.2},"text":[{"id":"cand_mic_fixture_002","role":"Me","source_track":"local_recall","text":"Я понял."}],"commands":{"mic_raw":"ffplay -hide_banner -loglevel error -ss 12.000 -t 3.200 \"$group_session/audio/mic/000001.caf\""},"reviewer":"","notes":""}
EOF
  review_cli_out="$workdir/review_decisions_cli.jsonl"
  review_cli_stdout="$workdir/review_decisions_cli_stdout.txt"
  printf '\n' | "$repo_root/scripts/review-decisions-cli.py" \
    --template "$review_template" \
    --out "$review_cli_out" \
    --no-play \
    --limit 1 >"$review_cli_stdout"
  jq -s '.[0].decision == "keep_me" and .[0].status == "reviewed"' "$review_cli_out" >/dev/null
  rg -q 'Context:' "$review_cli_stdout"
  rg -q 'utt_audio_uncertain_me' "$review_cli_stdout"
  rg -q 'Progress: reviewed=1/2, remaining=1' "$review_cli_stdout"
  rg -q 'Resume:' "$review_cli_stdout"
  lane_cli_out="$workdir/review_decisions_lane.jsonl"
  lane_cli_stdout="$workdir/review_decisions_lane_stdout.txt"
  printf '\n' | "$repo_root/scripts/review-decisions-cli.py" \
    --template "$review_template" \
    --out "$lane_cli_out" \
    --lane check_local_recall \
    --no-play \
    --limit 1 >"$lane_cli_stdout"
  jq -s '.[0].decision == "todo" and .[1].decision == "needs_review"' "$lane_cli_out" >/dev/null
  rg -q 'Progress: reviewed=1/1, remaining=0' "$lane_cli_stdout"
  lane_pack_dir="$workdir/review_lane_pack"
  "$repo_root/scripts/build-review-lane-pack.py" \
    --template "$review_template" \
    --lane check_local_recall \
    --out-dir "$lane_pack_dir" >/dev/null
  [[ -s "$lane_pack_dir/review_lane_pack.check_local_recall.wav" ]]
  [[ -s "$lane_pack_dir/review_lane_pack.check_local_recall.json" ]]
  [[ -s "$lane_pack_dir/review_lane_pack.check_local_recall.md" ]]
  [[ -s "$lane_pack_dir/review_lane_answers.check_local_recall.txt" ]]
  [[ -s "$lane_pack_dir/review_lane_answers.check_local_recall.suggested.txt" ]]
  jq -e '.schema == "murmurmark.review_lane_pack/v1" and .summary.item_count == 1 and .items[0].source_audit_id == "local_recall_0001"' "$lane_pack_dir/review_lane_pack.check_local_recall.json" >/dev/null
  jq -e '.outputs.answer_sheet | endswith("review_lane_answers.check_local_recall.txt")' "$lane_pack_dir/review_lane_pack.check_local_recall.json" >/dev/null
  jq -e '.outputs.suggested_answer_sheet | endswith("review_lane_answers.check_local_recall.suggested.txt")' "$lane_pack_dir/review_lane_pack.check_local_recall.json" >/dev/null
  grep -q '^answers=\.$' "$lane_pack_dir/review_lane_answers.check_local_recall.txt"
  grep -q '^answers=r$' "$lane_pack_dir/review_lane_answers.check_local_recall.suggested.txt"
  grep -q -- '--answers-file' "$lane_pack_dir/review_lane_pack.check_local_recall.md"
  review_workspace_dir="$workdir/review_workspace"
  "$repo_root/scripts/build-review-workspace.py" \
    --template "$review_template" \
    --out-dir "$review_workspace_dir" >/dev/null
  [[ -s "$review_workspace_dir/review_workspace.json" ]]
  [[ -s "$review_workspace_dir/review_workspace.md" ]]
  jq -e '.schema == "murmurmark.review_workspace/v1" and (.lanes | length) >= 1 and any(.lanes[]; .lane == "check_local_recall" and .status == "ok")' "$review_workspace_dir/review_workspace.json" >/dev/null
  answer_sheet="$review_workspace_dir/lane-packs/review_lane_answers.check_local_recall.txt"
  [[ -s "$answer_sheet" ]]
  grep -q '^answers=\.$' "$answer_sheet"
  suggested_answer_sheet="$review_workspace_dir/lane-packs/review_lane_answers.check_local_recall.suggested.txt"
  [[ -s "$suggested_answer_sheet" ]]
  grep -q '^answers=r$' "$suggested_answer_sheet"
  grep -q -- '--answers-file' "$review_workspace_dir/review_workspace.md"
  suggested_apply_out="$workdir/review_decisions_workspace_suggested_apply.jsonl"
  "$repo_root/scripts/apply-review-workspace-decisions.py" \
    --workspace "$review_workspace_dir/review_workspace.json" \
    --template "$review_template" \
    --out "$suggested_apply_out" \
    --report "$workdir/review_workspace_suggested_apply_report.json" \
    --answers-source suggested >/dev/null
  jq -s '.[0].decision == "todo" and .[1].decision == "needs_review" and .[1].review_source == "workspace_suggested_answer_sheet"' "$suggested_apply_out" >/dev/null
  jq -e '.schema == "murmurmark.review_workspace_apply_report/v1" and .answers_source == "suggested" and .summary.reviewed_count == 1 and .summary.remaining_rows == 1 and .summary.rejected_count == 0' "$workdir/review_workspace_suggested_apply_report.json" >/dev/null
  sed 's/^answers=.*/answers=k/' "$answer_sheet" >"$answer_sheet.tmp"
  mv "$answer_sheet.tmp" "$answer_sheet"
  workspace_apply_out="$workdir/review_decisions_workspace_apply.jsonl"
  "$repo_root/scripts/apply-review-workspace-decisions.py" \
    --workspace "$review_workspace_dir/review_workspace.json" \
    --template "$review_template" \
    --out "$workspace_apply_out" \
    --report "$workdir/review_workspace_apply_report.json" >/dev/null
  jq -s '.[0].decision == "todo" and .[1].decision == "keep_me" and .[1].review_source == "workspace_answer_sheet"' "$workspace_apply_out" >/dev/null
  jq -e '.schema == "murmurmark.review_workspace_apply_report/v1" and .summary.reviewed_count == 1 and .summary.remaining_rows == 1 and .summary.workspace_todo_count == 0 and .summary.rejected_count == 0' "$workdir/review_workspace_apply_report.json" >/dev/null
  cli_review_workspace_dir="$workdir/review_workspace_cli"
  "$repo_root/.build/debug/murmurmark" review workspace \
    --template "$review_template" \
    --out-dir "$cli_review_workspace_dir" >/dev/null
  [[ -s "$cli_review_workspace_dir/review_workspace.json" ]]
  jq -e '.schema == "murmurmark.review_workspace/v1" and any(.lanes[]; .lane == "check_local_recall" and .status == "ok")' "$cli_review_workspace_dir/review_workspace.json" >/dev/null
  cli_answer_sheet="$cli_review_workspace_dir/lane-packs/review_lane_answers.check_local_recall.txt"
  sed 's/^answers=.*/answers=k/' "$cli_answer_sheet" >"$cli_answer_sheet.tmp"
  mv "$cli_answer_sheet.tmp" "$cli_answer_sheet"
  cli_workspace_apply_out="$workdir/review_decisions_workspace_cli_apply.jsonl"
  "$repo_root/.build/debug/murmurmark" review workspace apply \
    --workspace "$cli_review_workspace_dir/review_workspace.json" \
    --template "$review_template" \
    --out "$cli_workspace_apply_out" \
    --report "$workdir/review_workspace_cli_apply_report.json" >/dev/null
  jq -s '.[0].decision == "todo" and .[1].decision == "keep_me" and .[1].review_source == "workspace_answer_sheet"' "$cli_workspace_apply_out" >/dev/null
  jq -e '.schema == "murmurmark.review_workspace_apply_report/v1" and .summary.reviewed_count == 1 and .summary.remaining_rows == 1' "$workdir/review_workspace_cli_apply_report.json" >/dev/null
  lane_pack_apply_out="$workdir/review_decisions_lane_pack_apply.jsonl"
  "$repo_root/scripts/apply-review-lane-pack-decisions.py" \
    "$review_workspace_dir/lane-packs/review_lane_pack.check_local_recall.json" \
    --template "$review_template" \
    --out "$lane_pack_apply_out" \
    --answers-file "$answer_sheet" >/dev/null
  jq -s '.[0].decision == "todo" and .[1].decision == "keep_me" and .[1].review_source == "lane_pack"' "$lane_pack_apply_out" >/dev/null
  jq -e '.schema == "murmurmark.review_lane_pack_apply_report/v1" and .summary.reviewed_count == 1 and .summary.rejected_count == 0' "$workdir/review_lane_pack_apply_report.json" >/dev/null
  duplicate_source_template="$workdir/review_decisions_duplicate_source.template.jsonl"
  duplicate_source_out="$workdir/review_decisions_duplicate_source.jsonl"
  duplicate_lane_dir="$workdir/review_lane_pack_duplicate_source"
  cat >"$duplicate_source_template" <<EOF
{"schema":"murmurmark.review_decision/v1","status":"todo","decision":"todo","session_id":"group-session","session":"$group_session","input_profile":"audit_cleanup_v4","source_audit_id":"duplicate_source","label":"remote_duplicate","verdict":"probable_transcript_error","review_lane":"fast_confirm_drop","review_action":"check_duplicate","suggested_decision":"drop_me","suggested_decision_confidence":"high","me_utterance_ids":["utt_dup_a"],"utterance_ids":["utt_dup_a"],"interval":{"start":0.0,"end":0.8,"duration_sec":0.8},"text":[{"id":"utt_dup_a","role":"me","source_track":"mic","text":"Первый дубль."}],"commands":{"mic_raw":"ffplay -hide_banner -loglevel error -ss 0.000 -t 1.000 \"$group_session/audio/mic/000001.caf\""}}
{"schema":"murmurmark.review_decision/v1","status":"todo","decision":"todo","session_id":"group-session","session":"$group_session","input_profile":"audit_cleanup_v4","source_audit_id":"duplicate_source","label":"remote_duplicate","verdict":"probable_transcript_error","review_lane":"fast_confirm_drop","review_action":"check_duplicate","suggested_decision":"drop_me","suggested_decision_confidence":"high","me_utterance_ids":["utt_dup_b"],"utterance_ids":["utt_dup_b"],"interval":{"start":1.0,"end":1.8,"duration_sec":0.8},"text":[{"id":"utt_dup_b","role":"me","source_track":"mic","text":"Второй дубль."}],"commands":{"mic_raw":"ffplay -hide_banner -loglevel error -ss 1.000 -t 1.000 \"$group_session/audio/mic/000001.caf\""}}
EOF
  "$repo_root/scripts/build-review-lane-pack.py" \
    --template "$duplicate_source_template" \
    --lane fast_confirm_drop \
    --out-dir "$duplicate_lane_dir" >/dev/null
  jq -e '.items | length == 2 and all(.[]; .review_row_key | startswith("source:duplicate_source") | not)' "$duplicate_lane_dir/review_lane_pack.fast_confirm_drop.json" >/dev/null
  "$repo_root/scripts/apply-review-lane-pack-decisions.py" \
    "$duplicate_lane_dir/review_lane_pack.fast_confirm_drop.json" \
    --template "$duplicate_source_template" \
    --out "$duplicate_source_out" \
    --answers ks >/dev/null
  jq -s '.[0].decision == "keep_me" and .[1].decision == "skip"' "$duplicate_source_out" >/dev/null
  "$repo_root/scripts/report-review-decisions-progress.py" \
    --template "$review_template" \
    --decisions "$lane_pack_apply_out" \
    --out "$workdir/review_decisions_progress.json" \
    --markdown "$workdir/review_decisions_progress.md" >/dev/null
  jq -e '.schema == "murmurmark.review_decisions_progress/v1" and .summary.reviewed == 1 and .summary.remaining == 1 and .summary.invalid_rows == 0 and (.by_lane | length) == 2' "$workdir/review_decisions_progress.json" >/dev/null
  rg -q 'Ready for batch apply: `False`' "$workdir/review_decisions_progress.md"
  local_cli_template="$workdir/review_decisions_local_only.template.jsonl"
  local_cli_out="$workdir/review_decisions_local_only.jsonl"
  local_cli_stdout="$workdir/review_decisions_local_only_stdout.txt"
  tail -n 1 "$review_template" >"$local_cli_template"
  printf 'd\n\n' | "$repo_root/scripts/review-decisions-cli.py" \
    --template "$local_cli_template" \
    --out "$local_cli_out" \
    --no-play \
    --limit 1 >"$local_cli_stdout"
  jq -s '.[0].decision == "needs_review" and (.[0].allowed_decisions | index("drop_me") | not)' "$local_cli_out" >/dev/null
  rg -q 'audio=1:mic_raw' "$local_cli_stdout"
  rg -q 'Decision drop_me is not allowed' "$local_cli_stdout"
  rg -q 'Progress: reviewed=1/1, remaining=0' "$local_cli_stdout"
  rg -q 'apply-review-decisions-batch.py' "$local_cli_stdout"
  invalid_allowed_decisions="$workdir/review_decisions_invalid_allowed.jsonl"
  jq -c '.decision = "drop_me" | .status = "reviewed"' "$local_cli_template" >"$invalid_allowed_decisions"
  if "$repo_root/scripts/apply-review-decisions.py" "$group_session" \
    --decisions "$invalid_allowed_decisions" \
    --review-template "$local_cli_template" \
    --input-profile auto \
    --output-profile reviewed_invalid_allowed_v1 >/dev/null; then
    echo "invalid allowed_decisions unexpectedly passed" >&2
    exit 1
  fi
  jq -e '.gates.passed == false and (.gates.hard_failures | index("invalid_decisions"))' \
    "$group_session/derived/transcript-simple/whisper-cpp/review-decisions/review_decisions_report.reviewed_invalid_allowed_v1.json" >/dev/null
  partial_review_decisions="$workdir/review_decisions_partial.jsonl"
  cp "$review_template" "$partial_review_decisions"
  if "$repo_root/scripts/apply-review-decisions.py" "$group_session" \
    --decisions "$partial_review_decisions" \
    --review-template "$review_template" \
    --input-profile auto \
    --output-profile reviewed_partial_v1 >/dev/null; then
    echo "partial review unexpectedly passed" >&2
    exit 1
  fi
  jq -e '.gates.passed == false and (.gates.hard_failures | index("incomplete_review_scope"))' \
    "$group_session/derived/transcript-simple/whisper-cpp/review-decisions/review_decisions_report.reviewed_partial_v1.json" >/dev/null

  cat >"$review_decisions" <<EOF
{"schema":"murmurmark.review_decision/v1","status":"reviewed","decision":"keep_me","session_id":"group-session","session":"$group_session","input_profile":"audit_cleanup_v4","source_audit_id":"arp_manual_review_keep","label":"uncertain","verdict":"needs_stronger_audio_judge","review_action":"classify_audio","me_utterance_ids":["utt_audio_uncertain_me"],"remote_utterance_ids":["utt_audio_uncertain_remote"],"utterance_ids":["utt_audio_uncertain_remote","utt_audio_uncertain_me"],"text":[{"id":"utt_audio_uncertain_remote","role":"remote","source_track":"remote","text":"Там есть спорный кусок."},{"id":"utt_audio_uncertain_me","role":"me","source_track":"mic","text":"Я уточню отдельно."}],"reviewer":"smoke","notes":"confirmed local speech"}
{"schema":"murmurmark.review_decision/v1","status":"reviewed","decision":"keep_me","allowed_decisions":["keep_me","needs_review","skip"],"session_id":"group-session","session":"$group_session","input_profile":"audit_cleanup_v4","cluster_id":"review_cluster_local_001","source":"local_recall","source_audit_id":"local_recall_0001","label":"lost_me","verdict":"needs_stronger_audio_judge","review_action":"check_lost_local_speech","me_utterance_ids":[],"remote_utterance_ids":[],"utterance_ids":[],"interval":{"start":13.0,"end":14.2,"duration_sec":1.2},"text":[{"id":"cand_mic_fixture_002","role":"Me","source_track":"local_recall","text":"Я понял."}],"commands":{"mic_raw":"ffplay -hide_banner -loglevel error -ss 12.000 -t 3.200 \"$group_session/audio/mic/000001.caf\""},"reviewer":"smoke","notes":"local recall checked as harmless"}
EOF
  "$repo_root/scripts/apply-review-decisions.py" "$group_session" \
    --decisions "$review_decisions" \
    --review-template "$review_template" \
    --input-profile auto \
    --output-profile reviewed_v1 >/dev/null
  reviewed_dialogue="$group_resolved/clean_dialogue.reviewed_v1.json"
  reviewed_report="$group_session/derived/transcript-simple/whisper-cpp/review-decisions/review_decisions_report.reviewed_v1.json"
  [[ -s "$reviewed_dialogue" ]]
  [[ -s "$reviewed_report" ]]
  jq -e '.gates.passed == true and .coverage.complete == true and .summary.applied_decision_rows == 2 and .summary.audit_only_applied_decision_rows == 1 and .summary.local_recall_cleared_decisions == 1' "$reviewed_report" >/dev/null
  jq -e 'any(.utterances[]; .id == "utt_audio_uncertain_me" and .quality.human_review.decisions[0] == "keep_me")' "$reviewed_dialogue" >/dev/null
  "$repo_root/scripts/synthesize-simple-extractive.py" "$group_session" --transcript-profile auto >/dev/null
  jq -e '.selected_transcript_profile == "reviewed_v1"' "$group_session/derived/synthesis-simple/extractive/quality_verdict.json" >/dev/null
  jq -e '.selected_transcript_profile == "reviewed_v1"' "$group_session/derived/synthesis-simple/extractive/quality_verdict.reviewed_v1.json" >/dev/null
  batch_report="$workdir/review_decisions_apply_report.json"
  batch_session_quality_dir="$workdir/batch-session-quality"
  batch_operational_dir="$workdir/batch-operational-readiness"
  batch_review_plan_dir="$workdir/batch-review-plan"
  "$repo_root/scripts/apply-review-decisions-batch.py" \
    --decisions "$review_decisions" \
    --review-template "$review_template" \
    --out "$batch_report" \
    --synthesize \
    --refresh-reports \
    --session-quality-out-dir "$batch_session_quality_dir" \
    --operational-readiness-out-dir "$batch_operational_dir" \
    --review-plan-out-dir "$batch_review_plan_dir" >/dev/null
  [[ -s "$batch_report" ]]
  jq -e '.schema == "murmurmark.review_decisions_batch_report/v1" and .summary.session_count == 1 and .summary.failed_sessions == 0 and .summary.failed_refresh_steps == 0 and (.refresh_reports | length) == 3' "$batch_report" >/dev/null
  [[ -s "$batch_session_quality_dir/session_quality_report.json" ]]
  [[ -s "$batch_operational_dir/operational_readiness_report.json" ]]
  [[ -s "$batch_review_plan_dir/review_plan.json" ]]
  pipeline_plan="$workdir/pipeline_run_report.json"
  "$repo_root/scripts/run-session-pipeline.py" "$group_session" \
    --plan-only \
    --skip-build \
    --reuse-asr-cache \
    --report "$pipeline_plan" >/dev/null
  [[ -s "$pipeline_plan" ]]
  jq -e '.schema == "murmurmark.session_pipeline_run/v1" and .status == "planned" and (.steps | length) >= 10' "$pipeline_plan" >/dev/null
  jq -e 'all(.steps[]; (.started_at | type) == "string" and (.duration_sec | type) == "number")' "$pipeline_plan" >/dev/null
  jq -e 'any(.steps[]; .name == "session_readiness")' "$pipeline_plan" >/dev/null

  corpus_dir="$workdir/regression-corpus"
  "$repo_root/scripts/build-regression-corpus.py" "$group_session" \
    --out-dir "$corpus_dir" \
    --per-label 4 \
    --max-items 12 >/dev/null
  [[ -s "$corpus_dir/regression_corpus_manifest.json" ]]
  [[ -s "$corpus_dir/regression_corpus_summary.json" ]]
  [[ -s "$corpus_dir/regression_corpus_items.jsonl" ]]
  [[ -s "$corpus_dir/regression_corpus.md" ]]
  jq -e '.schema == "murmurmark.regression_corpus_summary/v1"' "$corpus_dir/regression_corpus_summary.json" >/dev/null
  jq -e '.item_count >= 2 and .skipped_sessions == []' "$corpus_dir/regression_corpus_summary.json" >/dev/null
  jq -s 'any(.[]; .label == "remote_duplicate") and any(.[]; .label == "uncertain")' "$corpus_dir/regression_corpus_items.jsonl" >/dev/null
  "$repo_root/scripts/evaluate-regression-corpus.py" --corpus-dir "$corpus_dir" >/dev/null
  [[ -s "$corpus_dir/regression_corpus_evaluation.json" ]]
  [[ -s "$corpus_dir/regression_corpus_evaluation_items.jsonl" ]]
  [[ -s "$corpus_dir/regression_corpus_evaluation.md" ]]
  jq -e '.schema == "murmurmark.regression_corpus_evaluation/v1"' "$corpus_dir/regression_corpus_evaluation.json" >/dev/null
  jq -e '.by_readiness_bucket.silver_cleanup_positive.count >= 1 and .by_readiness_bucket.needs_audio_judge.count >= 1' "$corpus_dir/regression_corpus_evaluation.json" >/dev/null

  judge_dir="$workdir/audio-judge-v0"
  "$audit_python" "$repo_root/scripts/train-audio-judge-v0.py" \
    --corpus-dir "$corpus_dir" \
    --out-dir "$judge_dir" >/dev/null
  [[ -s "$judge_dir/audio_judge_v0_report.json" ]]
  [[ -s "$judge_dir/audio_judge_v0_predictions.jsonl" ]]
  [[ -s "$judge_dir/audio_judge_v0_cv_predictions.jsonl" ]]
  [[ -s "$judge_dir/audio_judge_v0_report.md" ]]
  jq -e '.schema == "murmurmark.audio_judge_v0_report/v1"' "$judge_dir/audio_judge_v0_report.json" >/dev/null
  jq -e '.policy.may_modify_transcript == false and .training.rows >= 2 and (.evaluation.policy_accuracy | type) == "number"' "$judge_dir/audio_judge_v0_report.json" >/dev/null
  jq -e '.evaluation_detail.per_session | type == "array"' "$judge_dir/audio_judge_v0_report.json" >/dev/null
  jq -e '.evaluation_detail.confidence_buckets | length == 4' "$judge_dir/audio_judge_v0_report.json" >/dev/null
  jq -e '.evaluation_detail.cleanup_precision_by_threshold | length == 3' "$judge_dir/audio_judge_v0_report.json" >/dev/null
  jq -s 'all(.[]; .schema == "murmurmark.audio_judge_v0_cv_prediction/v1" and (.cv_correct | type) == "boolean" and (.policy_label | type) == "string")' "$judge_dir/audio_judge_v0_cv_predictions.jsonl" >/dev/null
  jq -e '(.review_queue.remaining_human_review_items // 0) >= ((.review_queue.candidate_future_cleanup_items // 0) + (.review_queue.candidate_mark_only_items // 0))' "$judge_dir/audio_judge_v0_report.json" >/dev/null

  quality_dir="$workdir/session-quality"
  "$repo_root/scripts/report-session-quality.py" "$group_session" --out-dir "$quality_dir" >/dev/null
  jq -e '.sessions[0].audio_review_resolved_by_cleanup_count >= 1' "$quality_dir/session_quality_report.json" >/dev/null
  readiness_dir="$workdir/operational-readiness"
  "$repo_root/scripts/report-operational-readiness.py" \
    --session-quality "$quality_dir/session_quality_report.json" \
    --corpus-evaluation "$corpus_dir/regression_corpus_evaluation.json" \
    --audio-judge "$judge_dir/audio_judge_v0_report.json" \
    --out-dir "$readiness_dir" >/dev/null
  [[ -s "$readiness_dir/operational_readiness_report.json" ]]
  [[ -s "$readiness_dir/operational_readiness_report.md" ]]
  jq -e '.schema == "murmurmark.operational_readiness_report/v1"' "$readiness_dir/operational_readiness_report.json" >/dev/null
  jq -e '.operational_verdict | IN("not_ready", "pilot_ready_with_review", "medium_risk_ready")' "$readiness_dir/operational_readiness_report.json" >/dev/null
  jq -e '.summary.use_gates | type == "object"' "$readiness_dir/operational_readiness_report.json" >/dev/null
  jq -e '.summary.audio_judge_readiness | type == "string"' "$readiness_dir/operational_readiness_report.json" >/dev/null
  jq -e 'all(.session_review_burden[]; (.use_gate | type) == "string")' "$readiness_dir/operational_readiness_report.json" >/dev/null
  jq -e '.review_queue | type == "array"' "$readiness_dir/operational_readiness_report.json" >/dev/null
  jq -e '.promotion_plan.review_queue_strategy.by_lane | type == "array"' "$readiness_dir/operational_readiness_report.json" >/dev/null
  jq -e '.promotion_plan.review_queue_strategy.after_first_lane_estimate.remaining_items | type == "number"' "$readiness_dir/operational_readiness_report.json" >/dev/null
  jq -e 'any(.review_queue[]; .source == "local_recall" and .label == "lost_me")' "$readiness_dir/operational_readiness_report.json" >/dev/null
  jq -e 'all(.review_queue[]; .source_audit_id != "arp_manual_v2_duplicate")' "$readiness_dir/operational_readiness_report.json" >/dev/null
  gates_dir="$workdir/corpus-gates"
  gate_args=(
    --session-quality "$quality_dir/session_quality_report.json"
    --corpus-evaluation "$corpus_dir/regression_corpus_evaluation.json"
    --audio-judge "$judge_dir/audio_judge_v0_report.json"
    --operational-readiness "$readiness_dir/operational_readiness_report.json"
    --min-complete-sessions 1
    --min-ready-for-notes 0
    --min-corpus-sessions 1
    --min-corpus-items 2
    --min-audio-judge-rows 2
    --min-audio-judge-cv-accuracy 0
    --max-total-review-burden-ratio 1
    --max-session-review-burden-ratio 1
    --max-operational-review-queue-items 99
    --max-audio-judge-remaining-review-items 99
  )
  "$repo_root/scripts/check-corpus-gates.py" \
    "${gate_args[@]}" \
    --out-dir "$gates_dir" \
    --write-baseline "$gates_dir/baseline.json" \
    --no-fail >/dev/null
  [[ -s "$gates_dir/corpus_gates_report.json" ]]
  [[ -s "$gates_dir/baseline.json" ]]
  jq -e '.schema == "murmurmark.corpus_gates_baseline/v1"' "$gates_dir/baseline.json" >/dev/null
  "$repo_root/scripts/check-corpus-gates.py" \
    "${gate_args[@]}" \
    --out-dir "$gates_dir/with-baseline" \
    --baseline "$gates_dir/baseline.json" \
    --no-fail >/dev/null
  jq -e 'all(.checks[] | select(.id | startswith("baseline.")); .status == "pass")' \
    "$gates_dir/with-baseline/corpus_gates_report.json" >/dev/null
  jq '.metrics.ready_for_notes = 99' "$gates_dir/baseline.json" >"$gates_dir/baseline_bad.json"
  if "$repo_root/scripts/check-corpus-gates.py" \
    "${gate_args[@]}" \
    --out-dir "$gates_dir/with-bad-baseline" \
    --baseline "$gates_dir/baseline_bad.json" >/dev/null 2>&1; then
    echo "expected corpus gate to fail against bad baseline" >&2
    exit 1
  fi
  jq -e 'any(.checks[]; .id == "baseline.ready_for_notes_not_lower" and .status == "fail")' \
    "$gates_dir/with-bad-baseline/corpus_gates_report.json" >/dev/null
  review_plan_dir="$workdir/review-plan"
  "$repo_root/scripts/build-review-plan.py" \
    --operational-readiness "$readiness_dir/operational_readiness_report.json" \
    --out-dir "$review_plan_dir" >/dev/null
  [[ -s "$review_plan_dir/review_plan.json" ]]
  [[ -s "$review_plan_dir/review_plan.md" ]]
  [[ -s "$review_plan_dir/review_plan_clusters.jsonl" ]]
  [[ -s "$review_plan_dir/review_decisions.template.jsonl" ]]
  jq -e '.schema == "murmurmark.review_plan/v1" and .summary.cluster_count >= 1' "$review_plan_dir/review_plan.json" >/dev/null
  jq -s 'all(.[]; (.primary_command | type) == "string")' "$review_plan_dir/review_plan_clusters.jsonl" >/dev/null
  jq -s 'all(.[]; .schema == "murmurmark.review_decision/v1" and .decision == "todo" and (.me_utterance_ids | type) == "array" and (.suggested_decision | IN("drop_me", "keep_me", "needs_review")))' "$review_plan_dir/review_decisions.template.jsonl" >/dev/null
  jq -s 'any(.[]; .suggested_decision == "drop_me" and .decision == "todo")' "$review_plan_dir/review_decisions.template.jsonl" >/dev/null
  jq -s 'all(.[]; .label != "remote_duplicate" or .suggested_decision != "drop_me" or ((.review_features.me_overlap_coverage // 0) >= 0.6))' "$review_plan_dir/review_decisions.template.jsonl" >/dev/null
  jq -s 'all(.[]; .label != "remote_duplicate" or ((.review_features.me_overlap_coverage // 0) >= 0.55) or .suggested_decision == "needs_review")' "$review_plan_dir/review_decisions.template.jsonl" >/dev/null
  jq -s 'any(.[]; .label == "lost_me" and .review_action == "check_lost_local_speech")' "$review_plan_dir/review_decisions.template.jsonl" >/dev/null
fi

empty_session="$workdir/empty-session"
empty_resolved="$empty_session/derived/transcript-simple/whisper-cpp/resolved"
mkdir -p "$empty_resolved"
jq -n '{schema: "murmurmark.clean_dialogue/v1", session: "empty", utterances: []}' >"$empty_resolved/clean_dialogue.json"
jq -n '{schema: "murmurmark.simple_transcript_quality/v1", utterances: 0, needs_review_count: 0}' >"$empty_resolved/quality_report.json"
jq -n '{schema: "murmurmark.transcript_overlaps/v1", session: "empty", overlaps: []}' >"$empty_resolved/overlaps.json"
"$repo_root/scripts/synthesize-simple-extractive.py" "$empty_session" --transcript-profile auto >/dev/null
jq -e '.verdict == "failed"' "$empty_session/derived/synthesis-simple/extractive/quality_verdict.json" >/dev/null

mic_sha_after="$(shasum -a 256 "$session/audio/mic/000001.caf" | awk '{print $1}')"
remote_sha_after="$(shasum -a 256 "$session/audio/remote/000001.caf" | awk '{print $1}')"
[[ "$mic_sha_before" == "$mic_sha_after" ]]
[[ "$remote_sha_before" == "$remote_sha_after" ]]

echo "smoke fixture ok"
