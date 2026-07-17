#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

require_tool() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "missing required tool: $1" >&2
    exit 1
  fi
}

require_tool ffmpeg
require_tool jq

python_bin="${MURMURMARK_PYTHON:-$repo_root/.venv/bin/python}"
if [[ ! -x "$python_bin" ]]; then
  python_bin="python3"
fi
murmurmark_bin="${MURMURMARK_BIN:-$repo_root/.build/debug/murmurmark}"
if [[ ! -x "$murmurmark_bin" ]]; then
  swift build >/dev/null
fi

workdir="$(mktemp -d "${TMPDIR:-/tmp}/murmurmark-process-resume.XXXXXX")"
trap 'rm -rf "$workdir"' EXIT

session="$workdir/session"
mkdir -p "$session/audio/mic" "$session/audio/remote" "$session/derived/asr" "$workdir/bin"

ffmpeg -hide_banner -loglevel error -y \
  -f lavfi -i "sine=frequency=440:duration=125" \
  -ar 48000 -ac 1 -c:a pcm_s16le "$session/audio/mic/000001.caf"
ffmpeg -hide_banner -loglevel error -y \
  -f lavfi -i "sine=frequency=660:duration=125" \
  -ar 48000 -ac 1 -c:a pcm_s16le "$session/audio/remote/000001.caf"
ffmpeg -hide_banner -loglevel error -y \
  -i "$session/audio/mic/000001.caf" -ar 16000 -ac 1 "$session/derived/asr/mic.wav"
ffmpeg -hide_banner -loglevel error -y \
  -i "$session/audio/remote/000001.caf" -ar 16000 -ac 1 "$session/derived/asr/remote.wav"

mic_bytes="$(stat -f%z "$session/audio/mic/000001.caf")"
remote_bytes="$(stat -f%z "$session/audio/remote/000001.caf")"
jq -n \
  --argjson mic_bytes "$mic_bytes" \
  --argjson remote_bytes "$remote_bytes" \
  '{
    schema: "murmurmark.session/v1",
    session_id: "process-resume-smoke",
    created_at: "2026-07-02T20:00:00.000Z",
    ended_at: "2026-07-02T20:02:05.000Z",
    app_version: "0.1.0",
    capture_mode: "screencapturekit_system",
    status: "completed",
    target: {kind: "system_audio", display_name: "System Audio", pid_strategy: "screen_capture_system_excluding_self"},
    microphone: {device_uid: "default", display_name: "System Default Microphone", capture_backend: "screencapturekit_microphone"},
    mic_audio: {backend: "screencapturekit_microphone", sample_rate: 48000, channels: 1, format: "caf:lpcm"},
    remote_audio: {backend: "screencapturekit_audio", sample_rate: 48000, channels: 1, format: "caf:lpcm"},
    privacy: {network_allowed_during_capture: false, raw_audio_retention: "keep_until_manual_delete", telemetry: false},
    health: {summary: "ok", warnings: []},
    files: {
      mic: [{path: "audio/mic/000001.caf", bytes: $mic_bytes, frames: 6000000, channels: 1, sample_rate: 48000, start_host_time_ns: 0, start_session_sec: 0}],
      remote: [{path: "audio/remote/000001.caf", bytes: $remote_bytes, frames: 6000000, channels: 1, sample_rate: 48000, start_host_time_ns: 0, start_session_sec: 0}]
    }
  }' >"$session/session.json"

: >"$workdir/fake-model.bin"
cat >"$workdir/bin/fake-whisper-cli" <<'PY'
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
    raise SystemExit(2)

count_path = Path(os.environ["FAKE_WHISPER_COUNT"])
count = int(count_path.read_text(encoding="utf-8") or "0") if count_path.exists() else 0
count += 1
count_path.write_text(str(count), encoding="utf-8")

text = f"fake process chunk {output_base.name}"
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
    print("intentional process fake failure", file=sys.stderr)
    raise SystemExit(42)
PY
chmod +x "$workdir/bin/fake-whisper-cli"

export FAKE_WHISPER_COUNT="$workdir/fake-count.txt"
export WHISPER_CLI="$workdir/bin/fake-whisper-cli"

set +e
"$python_bin" scripts/run-session-pipeline.py "$session" \
  --murmurmark-bin "$murmurmark_bin" \
  --model "$workdir/fake-model.bin" \
  --skip-build \
  --skip-preprocess \
  --skip-audits \
  --skip-cleanup \
  --asr-track-workers 1 \
  --micro-asr-workers 1 \
  --progress-interval-sec 1 >"$workdir/first.log" 2>&1
first_status=$?
set -e

if [[ "$first_status" -eq 0 ]]; then
  echo "expected first process run to fail" >&2
  cat "$workdir/first.log" >&2
  exit 1
fi

jq -e '
  .status == "failed"
  and ([.steps[] | select(.name == "transcribe_current" and .status == "failed")] | length == 1)
' "$session/derived/pipeline-run/pipeline_run_report.json" >/dev/null
jq -e '
  .status == "running"
  and .chunks_completed == 1
  and .chunks_total == 3
  and .chunks_missing == 2
' "$session/derived/transcript-simple/whisper-cpp/raw/chunks/mic/chunk_cache_report.json" >/dev/null

"$python_bin" scripts/run-session-pipeline.py "$session" \
  --murmurmark-bin "$murmurmark_bin" \
  --model "$workdir/fake-model.bin" \
  --skip-build \
  --skip-preprocess \
  --skip-audits \
  --skip-cleanup \
  --asr-track-workers 1 \
  --micro-asr-workers 1 \
  --progress-interval-sec 1 >"$workdir/second.log" 2>&1

jq -e '
  .status == "passed"
  and ([.steps[] | select(.name == "check_asr_chunk_cache" and .status == "passed")] | length == 1)
' "$session/derived/pipeline-run/pipeline_run_report.json" >/dev/null
jq -e '
  .chunks_total == 3
  and .chunks_completed == 3
  and .chunks_reused == 1
  and .chunks_transcribed == 2
' "$session/derived/transcript-simple/whisper-cpp/raw/chunks/mic/chunk_cache_report.json" >/dev/null
jq -e '.status == "passed"' \
  "$session/derived/transcript-simple/whisper-cpp/raw/chunk_rebuild_check.json" >/dev/null
jq -e '
  .progress.asr_chunks.chunks_missing == 0
  and .progress.asr_chunks.remaining_sec == 0
  and .progress.asr_chunks.chunks_reused >= 1
' "$session/derived/pipeline-run/pipeline_run_report.json" >/dev/null

echo "process chunk resume smoke ok"

legacy_workdir="$(mktemp -d "${TMPDIR:-/tmp}/murmurmark-process-legacy-cache.XXXXXX")"
legacy_session="$legacy_workdir/session"
mkdir -p "$legacy_session/audio/mic" "$legacy_session/audio/remote" \
  "$legacy_session/derived/transcript-simple/whisper-cpp/raw"

ffmpeg -hide_banner -loglevel error -y \
  -f lavfi -i "sine=frequency=440:duration=65" \
  -ar 48000 -ac 1 -c:a pcm_s16le "$legacy_session/audio/mic/000001.caf"
ffmpeg -hide_banner -loglevel error -y \
  -f lavfi -i "sine=frequency=660:duration=65" \
  -ar 48000 -ac 1 -c:a pcm_s16le "$legacy_session/audio/remote/000001.caf"

legacy_mic_bytes="$(stat -f%z "$legacy_session/audio/mic/000001.caf")"
legacy_remote_bytes="$(stat -f%z "$legacy_session/audio/remote/000001.caf")"
jq -n \
  --argjson mic_bytes "$legacy_mic_bytes" \
  --argjson remote_bytes "$legacy_remote_bytes" \
  '{
    schema: "murmurmark.session/v1",
    session_id: "process-legacy-cache-smoke",
    created_at: "2026-07-02T20:00:00.000Z",
    ended_at: "2026-07-02T20:01:05.000Z",
    app_version: "0.1.0",
    capture_mode: "screencapturekit_system",
    status: "completed",
    target: {kind: "system_audio", display_name: "System Audio", pid_strategy: "screen_capture_system_excluding_self"},
    microphone: {device_uid: "default", display_name: "System Default Microphone", capture_backend: "screencapturekit_microphone"},
    mic_audio: {backend: "screencapturekit_microphone", sample_rate: 48000, channels: 1, format: "caf:lpcm"},
    remote_audio: {backend: "screencapturekit_audio", sample_rate: 48000, channels: 1, format: "caf:lpcm"},
    privacy: {network_allowed_during_capture: false, raw_audio_retention: "keep_until_manual_delete", telemetry: false},
    health: {summary: "ok", warnings: []},
    files: {
      mic: [{path: "audio/mic/000001.caf", bytes: $mic_bytes, frames: 3120000, channels: 1, sample_rate: 48000, start_host_time_ns: 0, start_session_sec: 0}],
      remote: [{path: "audio/remote/000001.caf", bytes: $remote_bytes, frames: 3120000, channels: 1, sample_rate: 48000, start_host_time_ns: 0, start_session_sec: 0}]
    }
  }' >"$legacy_session/session.json"

for track in mic remote; do
  jq -n '{params: {}, transcription: [{text: "legacy raw cache", offsets: {from: 0, to: 500}}]}' \
    >"$legacy_session/derived/transcript-simple/whisper-cpp/raw/$track.json"
done
jq -n \
  --arg model "$workdir/fake-model.bin" \
  '{
    schema: "murmurmark.whisper_cpp_raw_cache/v1",
    model: $model,
    language: "ru",
    max_context: 0,
    prompt: null,
    duration_ms: 0,
    asr_mode: "windowed",
    asr_window_sec: 60,
    asr_overlap_sec: 5,
    audio_prep: "speech",
    output_json_full: true,
    log_score: true,
    suppress_nst: true,
    suppress_regex: "^(Редактор субтитров|Продолжение следует|Спасибо за просмотр|Субтитры.*)$"
  }' >"$legacy_session/derived/transcript-simple/whisper-cpp/raw/mic.meta.json"
jq -n \
  --arg model "$workdir/fake-model.bin" \
  '{
    schema: "murmurmark.whisper_cpp_raw_cache/v1",
    model: $model,
    language: "ru",
    max_context: 0,
    prompt: null,
    duration_ms: 0,
    asr_mode: "windowed",
    asr_window_sec: 60,
    asr_overlap_sec: 5,
    audio_prep: "loudnorm",
    output_json_full: true,
    log_score: true,
    suppress_nst: true,
    suppress_regex: "^(Редактор субтитров|Продолжение следует|Спасибо за просмотр|Субтитры.*)$"
  }' >"$legacy_session/derived/transcript-simple/whisper-cpp/raw/remote.meta.json"

"$python_bin" scripts/run-session-pipeline.py "$legacy_session" \
  --murmurmark-bin "$murmurmark_bin" \
  --model "$workdir/fake-model.bin" \
  --skip-build \
  --skip-preprocess \
  --skip-audits \
  --skip-cleanup \
  --asr-track-workers 1 \
  --micro-asr-workers 1 \
  --progress-interval-sec 1 >"$legacy_workdir/process.log" 2>&1

jq -e '.status == "passed"' \
  "$legacy_session/derived/transcript-simple/whisper-cpp/raw/chunk_rebuild_check.json" >/dev/null
jq -e '
  .chunks_total == 2
  and .chunks_completed == 2
  and .chunks_transcribed == 2
' "$legacy_session/derived/transcript-simple/whisper-cpp/raw/chunks/mic/chunk_cache_report.json" >/dev/null
jq -e '
  .chunks_total == 2
  and .chunks_completed == 2
  and .chunks_transcribed == 2
' "$legacy_session/derived/transcript-simple/whisper-cpp/raw/chunks/remote/chunk_cache_report.json" >/dev/null

rm -rf "$legacy_workdir"
echo "process legacy raw-cache rebuild smoke ok"

interrupt_workdir="$(mktemp -d "${TMPDIR:-/tmp}/murmurmark-process-interrupt.XXXXXX")"
session="$interrupt_workdir/session"
mkdir -p "$session/audio/mic" "$session/audio/remote" "$session/derived/asr" "$interrupt_workdir/bin"

ffmpeg -hide_banner -loglevel error -y \
  -f lavfi -i "sine=frequency=440:duration=125" \
  -ar 48000 -ac 1 -c:a pcm_s16le "$session/audio/mic/000001.caf"
ffmpeg -hide_banner -loglevel error -y \
  -f lavfi -i "sine=frequency=660:duration=125" \
  -ar 48000 -ac 1 -c:a pcm_s16le "$session/audio/remote/000001.caf"
ffmpeg -hide_banner -loglevel error -y \
  -i "$session/audio/mic/000001.caf" -ar 16000 -ac 1 "$session/derived/asr/mic.wav"
ffmpeg -hide_banner -loglevel error -y \
  -i "$session/audio/remote/000001.caf" -ar 16000 -ac 1 "$session/derived/asr/remote.wav"

mic_bytes="$(stat -f%z "$session/audio/mic/000001.caf")"
remote_bytes="$(stat -f%z "$session/audio/remote/000001.caf")"
jq -n \
  --argjson mic_bytes "$mic_bytes" \
  --argjson remote_bytes "$remote_bytes" \
  '{
    schema: "murmurmark.session/v1",
    session_id: "process-interrupt-smoke",
    created_at: "2026-07-02T20:00:00.000Z",
    ended_at: "2026-07-02T20:02:05.000Z",
    app_version: "0.1.0",
    capture_mode: "screencapturekit_system",
    status: "completed",
    target: {kind: "system_audio", display_name: "System Audio", pid_strategy: "screen_capture_system_excluding_self"},
    microphone: {device_uid: "default", display_name: "System Default Microphone", capture_backend: "screencapturekit_microphone"},
    mic_audio: {backend: "screencapturekit_microphone", sample_rate: 48000, channels: 1, format: "caf:lpcm"},
    remote_audio: {backend: "screencapturekit_audio", sample_rate: 48000, channels: 1, format: "caf:lpcm"},
    privacy: {network_allowed_during_capture: false, raw_audio_retention: "keep_until_manual_delete", telemetry: false},
    health: {summary: "ok", warnings: []},
    files: {
      mic: [{path: "audio/mic/000001.caf", bytes: $mic_bytes, frames: 6000000, channels: 1, sample_rate: 48000, start_host_time_ns: 0, start_session_sec: 0}],
      remote: [{path: "audio/remote/000001.caf", bytes: $remote_bytes, frames: 6000000, channels: 1, sample_rate: 48000, start_host_time_ns: 0, start_session_sec: 0}]
    }
  }' >"$session/session.json"

: >"$interrupt_workdir/fake-model.bin"
cat >"$interrupt_workdir/bin/fake-whisper-cli" <<'PY'
#!/usr/bin/env python3
from pathlib import Path
import json
import os
import sys
import time

output_base = None
args = sys.argv[1:]
for index, arg in enumerate(args):
    if arg == "--output-file" and index + 1 < len(args):
        output_base = Path(args[index + 1])
        break
if output_base is None:
    raise SystemExit(2)

count_path = Path(os.environ["FAKE_WHISPER_COUNT"])
count = int(count_path.read_text(encoding="utf-8") or "0") if count_path.exists() else 0
count += 1
count_path.write_text(str(count), encoding="utf-8")

text = f"fake interrupt chunk {output_base.name}"
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
if os.environ.get("FAKE_WHISPER_SLEEP_ON_SECOND") == "1" and count == 2:
    time.sleep(60)
PY
chmod +x "$interrupt_workdir/bin/fake-whisper-cli"

export FAKE_WHISPER_COUNT="$interrupt_workdir/fake-count.txt"
export WHISPER_CLI="$interrupt_workdir/bin/fake-whisper-cli"
export FAKE_WHISPER_SLEEP_ON_SECOND=1
export PYTHON_BIN="$python_bin"
export REPO_ROOT="$repo_root"
export INTERRUPT_SESSION="$session"
export INTERRUPT_WORKDIR="$interrupt_workdir"
export MURMURMARK_BIN_FOR_SMOKE="$murmurmark_bin"

set +e
"$python_bin" - <<'PY'
from pathlib import Path
import os
import signal
import subprocess
import sys
import time

python_bin = os.environ["PYTHON_BIN"]
repo_root = os.environ["REPO_ROOT"]
session = os.environ["INTERRUPT_SESSION"]
workdir = os.environ["INTERRUPT_WORKDIR"]
murmurmark_bin = os.environ["MURMURMARK_BIN_FOR_SMOKE"]
count_path = Path(os.environ["FAKE_WHISPER_COUNT"])
log_path = Path(workdir) / "first.log"
command = [
    python_bin,
    "scripts/run-session-pipeline.py",
    session,
    "--murmurmark-bin",
    murmurmark_bin,
    "--model",
    str(Path(workdir) / "fake-model.bin"),
    "--skip-build",
    "--skip-preprocess",
    "--skip-audits",
    "--skip-cleanup",
    "--asr-track-workers",
    "1",
    "--micro-asr-workers",
    "1",
    "--progress-interval-sec",
    "1",
]

with log_path.open("w", encoding="utf-8") as log_file:
    process = subprocess.Popen(
        command,
        cwd=repo_root,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    for _ in range(100):
        if count_path.exists():
            try:
                if int(count_path.read_text(encoding="utf-8") or "0") >= 2:
                    break
            except ValueError:
                pass
        time.sleep(0.1)
    else:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=10)
        print("fake whisper did not reach the second chunk", file=sys.stderr)
        sys.exit(97)

    os.killpg(process.pid, signal.SIGINT)
    sys.exit(process.wait())
PY
interrupt_status=$?
set -e
if [[ "$interrupt_status" -eq 0 ]]; then
  echo "expected interrupted process run to exit non-zero" >&2
  cat "$interrupt_workdir/first.log" >&2
  exit 1
fi

jq -e '
  .status == "interrupted"
  and ([.steps[] | select(.name == "transcribe_current" and .status == "interrupted")] | length == 1)
' "$session/derived/pipeline-run/pipeline_run_report.json" >/dev/null
jq -e '
  .status == "running"
  and .chunks_completed == 1
  and .chunks_total == 3
  and .chunks_missing == 2
' "$session/derived/transcript-simple/whisper-cpp/raw/chunks/mic/chunk_cache_report.json" >/dev/null

unset FAKE_WHISPER_SLEEP_ON_SECOND
"$python_bin" scripts/run-session-pipeline.py "$session" \
  --murmurmark-bin "$murmurmark_bin" \
  --model "$interrupt_workdir/fake-model.bin" \
  --skip-build \
  --skip-preprocess \
  --skip-audits \
  --skip-cleanup \
  --asr-track-workers 1 \
  --micro-asr-workers 1 \
  --progress-interval-sec 1 >"$interrupt_workdir/second.log" 2>&1

jq -e '
  .status == "passed"
  and ([.steps[] | select(.name == "check_asr_chunk_cache" and .status == "passed")] | length == 1)
' "$session/derived/pipeline-run/pipeline_run_report.json" >/dev/null
jq -e '
  .chunks_total == 3
  and .chunks_completed == 3
  and .chunks_reused == 1
  and .chunks_transcribed == 2
' "$session/derived/transcript-simple/whisper-cpp/raw/chunks/mic/chunk_cache_report.json" >/dev/null
jq -e '.status == "passed"' \
  "$session/derived/transcript-simple/whisper-cpp/raw/chunk_rebuild_check.json" >/dev/null

rm -rf "$interrupt_workdir"
echo "process chunk interrupt smoke ok"
