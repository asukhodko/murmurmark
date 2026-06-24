# Session Package Contract

The session package is the boundary between capture and every later stage.

It must be durable, local, inspectable and safe to move to another machine.

## Directory Layout

```text
session/
  session.json
  events.jsonl
  session.lock              # exists only while active

  audio/
    mic/
      000001.caf
      000002.caf
    remote/
      000001.caf
      000002.caf

  derived/
    preprocess/
      audio/
        mic_raw_for_asr.wav
        remote_for_aec.wav
        mic_clean_linear.wav
        mic_clean_local_fir.wav
        mic_role_masked_for_asr.wav
        mic_role_preview.wav
        echo_hat_local_fir.wav
        mic_clean_speex.wav
        mic_clean_webrtc.wav
        mic_for_asr.wav
      mic_asr_segments/
        segments_manifest.json
        mic_000001.wav
        mic_000002.wav
      echo/
        echo_diagnostics.json
        echo_segments.jsonl
        local_fir_report.json
        local_fir_segments.jsonl
        speaker_state.jsonl
        echo_suppression_report.json
    transcript/
      resolved/
        transcript.rich.json
        quality_report.json
        echo_reconciliation_report.json
    evidence_package/
    notes/
```

During active recording chunks use `.part` suffix and are atomically finalized.

## `session.json`

Required fields:

```json
{
  "schema": "murmurmark.session/v1",
  "session_id": "2026-06-20T14-30-00Z_7f3a",
  "created_at": "2026-06-20T14:30:00Z",
  "ended_at": "2026-06-20T15:10:00Z",
  "app_version": "0.1.0",
  "capture_mode": "coreaudio_process_tap",
  "status": "completed",
  "target": {
    "kind": "bundle_id",
    "bundle_id": "com.microsoft.teams2",
    "display_name": "Microsoft Teams",
    "pid_strategy": "bundle_process_group"
  },
  "microphone": {
    "device_uid": "AppleHDAEngineInput:1B,0,1,0:1",
    "display_name": "MacBook Pro Microphone",
    "capture_backend": "auhal"
  },
  "remote_audio": {
    "backend": "coreaudio_process_tap",
    "sample_rate": 48000,
    "channels": 2,
    "format": "caf:lpcm:int16"
  },
  "mic_audio": {
    "backend": "auhal",
    "sample_rate": 48000,
    "channels": 1,
    "format": "caf:lpcm:int16"
  },
  "privacy": {
    "network_allowed_during_capture": false,
    "telemetry": false,
    "raw_audio_retention": "delete_after_pipeline_success"
  },
  "files": {
    "remote": [
      {
        "path": "audio/remote/000001.caf",
        "start_host_time_ns": 123456789000,
        "start_session_sec": 0.0,
        "sample_rate": 48000,
        "frames": 86400000,
        "sha256": null
      }
    ],
    "mic": [
      {
        "path": "audio/mic/000001.caf",
        "start_host_time_ns": 123456789120,
        "start_session_sec": 0.0,
        "sample_rate": 48000,
        "frames": 86400000,
        "sha256": null
      }
    ]
  },
  "health": {
    "summary": "ok",
    "warnings": []
  }
}
```

Status values:

- `active`
- `completed`
- `stopped_deleted`
- `failed`
- `quarantined`

Health summary:

- `ok`
- `warning`
- `degraded`
- `failed`

## `events.jsonl`

Append-only technical events. No raw transcript, no speech text, no generated notes.

Example:

```jsonl
{"t":"2026-06-20T14:30:00.120Z","type":"permission.system_audio.granted"}
{"t":"2026-06-20T14:30:00.420Z","type":"permission.microphone.granted"}
{"t":"2026-06-20T14:30:01.001Z","type":"capture.started"}
{"t":"2026-06-20T14:32:15.300Z","type":"health.remote.level_ok","rms_db":-24.1}
{"t":"2026-06-20T14:32:15.301Z","type":"health.mic.level_ok","rms_db":-18.7}
{"t":"2026-06-20T14:45:00.000Z","type":"user.marker","label":"decision"}
{"t":"2026-06-20T15:05:42.900Z","type":"capture.stopped"}
```

Allowed event families:

- `permission.*`
- `capture.*`
- `health.*`
- `device.*`
- `target.*`
- `writer.*`
- `sync.*`
- `user.marker`
- `retention.*`

Forbidden event payload:

- spoken text;
- ASR text;
- names inferred from transcript;
- raw audio paths outside the session;
- external provider secrets.

## `pipeline_job.json`

Created after capture when pipeline handoff is requested.

```json
{
  "schema": "murmurmark.pipeline_job/v1",
  "session_id": "2026-06-20T14-30-00Z_7f3a",
  "inputs": {
    "mic": "audio/mic",
    "remote": "audio/remote",
    "manifest": "session.json"
  },
  "meeting_context": {
    "language": ["ru", "en"],
    "domain_profile": "backend-platform",
    "glossary": "domain_pack/glossary.yaml"
  },
  "steps": [
    "preprocess",
    "asr",
    "diarization",
    "speaker_resolution",
    "glossary_correction",
    "notes",
    "export",
    "retention"
  ]
}
```

## Inspection Rules

`murmurmark inspect ./session` must show:

- duration by track;
- file count and byte size;
- non-empty status for mic and remote;
- sample rates and channels;
- warnings;
- retention state;
- whether derived transcript/notes exist.

When echo diagnostics exist, `murmurmark inspect ./session --echo` should also show:

- echo mode;
- whether probable remote bleed was detected;
- median delay and delay range;
- count of probable bleed segments;
- whether suppression was attempted;
- whether clean mic was accepted for ASR;
- selected `mic_for_asr` path.

For `local_fir` suppression, inspection should also expose:

- median local FIR delay;
- reliable delay window count;
- remote-only window count.

`local_fir` output roles:

- `mic_clean_local_fir.wav`: diagnostic cleaned mic for listening and measurement.
- `mic_role_masked_for_asr.wav`: full-timeline mic selected according to the role policy.
- `mic_role_preview.wav`: concatenated preview of retained mic regions for fast listening.
- `mic_asr_segments/`: chunk files and manifest for future mic ASR on retained regions.
- `speaker_state.jsonl`: 2 second role/action decisions for audit and debugging.
