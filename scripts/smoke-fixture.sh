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
    {id: "utt_simple_013", start: 160.0, end: 164.0, role: "Me", speaker_label: "Me", source_track: "mic", text: "Вопрос по Kubernetes квотам.", quality: {needs_review: false}}
  ]
}' >"$simple_resolved/clean_dialogue.json"
jq -n '{
  schema: "murmurmark.simple_transcript_quality/v1",
  utterances: 13,
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
grep -q 'utt_simple_003' "$session/derived/synthesis-simple/extractive/notes.md"
grep -q 'utt_simple_006' "$session/derived/synthesis-simple/extractive/notes.md"
! grep -q 'Надо подумать' "$session/derived/synthesis-simple/extractive/notes.md"
! grep -q 'Вопрос по Kubernetes' "$session/derived/synthesis-simple/extractive/notes.md"

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
