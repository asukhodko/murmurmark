# Capture Architecture

Capture is the most safety-critical part of MurmurMark. If it records silence or the wrong audio, later intelligence does not matter.

## Target Behavior

The meeting app keeps working normally:

```text
Teams/Zoom/Meet:
  uses its configured microphone
  uses its configured output device

MurmurMark:
  passively captures outgoing audio from the meeting app
  captures the selected physical microphone separately
  writes two local audio streams
  does not become a virtual microphone
  does not become a virtual speaker
  does not change global system input/output
```

## Backends

### Primary: Core Audio Process Tap

Use Core Audio Process Tap for remote/app audio.

Responsibilities:

- resolve bundle ID or PID set;
- create process tap;
- attach tap to a private aggregate device when required by Core Audio;
- start IOProc;
- copy audio frames into a preallocated buffer;
- forward data to writer thread.

Realtime callback rules:

- no file IO;
- no network;
- no heavy allocation;
- no logging of audio content;
- copy frames and timing metadata only.

### Microphone: AUHAL/Core Audio

Use AUHAL/Core Audio to capture a selected input device without changing system default input.

Responsibilities:

- enumerate input devices;
- let the user select `default` or concrete device UID;
- initialize AUHAL for selected `AudioDeviceID`;
- write mic stream into its own CAF chunk sequence.

### Microphone: Voice Processing Path

For comparison runs on real calls played through speakers, v1 can expose a microphone backend that uses Apple's voice-processing capture path.

Responsibilities:

- enable voice processing on the capture input;
- write a mono local microphone CAF;
- label the session with `av_audio_engine_voice_processing`;
- keep raw remote capture separate.

This is an experimental comparison backend. It may help in some real speaker scenarios, but it is not the main Echo Guard path because it can change the local microphone signal before MurmurMark sees it.

Current implementation constraint: the CLI voice-processing backend supports `--mic default` only. Concrete input-device selection should be added later through the Core Audio path.

### Remote: Audio Input Device

Manual routed capture can use an explicit input device as the remote track:

```bash
murmurmark record \
  --remote-backend audio-input \
  --remote-device SomeInputDevice_UID \
  --mic-backend screencapturekit
```

Responsibilities:

- select an explicit audio input device UID from `list-audio-devices`;
- capture it through `AVCaptureAudioFileOutput`;
- write it as `audio/remote/000001.caf`;
- label the session with `remote_audio.backend = avcapture_audio_input`;
- avoid treating a virtual device as a required product dependency.

This mode is useful only for explicit routing experiments and for validating a product route where MurmurMark owns or receives a clean far-end reference. It remains an experimental fallback, not the default app-capture path and not the basis for Echo Guard algorithm tests.
AVFoundation may write this raw CAF as AAC; later stages must consume the exported PCM WAV, not assume lossless raw remote in this fallback.

### Fallbacks

Fallback A: ScreenCaptureKit app/window audio + AUHAL mic.

Fallback B: ScreenCaptureKit system audio + AUHAL mic.

Fallback C: ScreenCaptureKit system audio + AVAudioEngine voice-processing mic.

Fallback D: explicit audio input remote for manual lab experiments.

Fallback modes must be explicit and labelled. The user should understand when MurmurMark needs broader capture permissions or manual routing. BlackHole/Loopback-style virtual routing is not a MurmurMark v1 dependency.

## Process Resolver

Inputs:

- bundle ID;
- optional process ID;
- optional window title hint;
- optional include/exclude process list.

Responsibilities:

- initial process discovery;
- child process discovery;
- process death detection;
- tap recreation when target process changes;
- event log entries for restarts.

MVP may start with `bundle ID -> current running PIDs`, but the design must leave space for Electron/browser process churn.

## Writers

Raw recording layout:

```text
session/
  session.lock
  session.json
  events.jsonl
  audio/
    mic/
      000001.caf.part
      000001.caf
    remote/
      000001.caf.part
      000001.caf
```

Rules:

- write `.caf.part` while active;
- atomically finalize to `.caf`;
- append technical events to `events.jsonl`;
- keep sample counters and host timestamps;
- split chunks by size or time;
- never write transcript or speech content to logs.

## Health Monitor

Before start:

- mic permission OK;
- system audio permission OK or dry-run instruction shown;
- target app resolved;
- mic level seen in last 5 seconds;
- disk space above threshold.

During recording:

- mic RMS and peak;
- remote RMS and peak;
- silence duration by source;
- clipping;
- writer backpressure;
- target process restart;
- device changes;
- drift warnings;
- disk low warnings.

Danger case:

```text
remote.caf is empty while the meeting was active
```

This must produce an obvious warning during recording and a degraded session status after stop.

## Permission UX

First run must be explicit:

```text
MurmurMark needs:
  1. Microphone access
     To record your side of the meeting.

  2. System Audio Recording access
     To record audio produced by the selected meeting app.

MurmurMark does not:
  - create a virtual microphone;
  - create a virtual speaker;
  - change your meeting app settings;
  - upload recordings;
  - record screen by default.
```

Because public proactive system-audio permission APIs are limited, v1 should use a controlled dry run:

1. `murmurmark doctor` tries a minimal capture.
2. If permission is missing, it shows specific instructions.
3. The user grants permission in macOS settings.
4. The dry run repeats.
5. Real recording is enabled only after a successful check.

No private TCC probing in default builds.

## CLI Contract

```bash
MURMURMARK_SESSIONS="${MURMURMARK_SESSIONS:-./sessions}"

murmurmark doctor
murmurmark list-apps

murmurmark record \
  --target-bundle com.microsoft.teams2 \
  --mic default \
  --out "$MURMURMARK_SESSIONS/2026-06-20-retro" \
  --format caf \
  --split-every 15m

murmurmark inspect "$MURMURMARK_SESSIONS/2026-06-20-retro"

murmurmark export-audio \
  "$MURMURMARK_SESSIONS/2026-06-20-retro" \
  --asr-chunks derived/asr \
  --sample-rate 16000
```

GUI and CLI must call the same capture core.

## Acceptance Criteria

- Remote track and mic track are both non-empty.
- Meeting app audio settings are unchanged.
- System input/output are unchanged.
- Capture survives at least 30 minutes.
- Stop-and-delete removes raw audio and manifest except optional safe audit record.
- Crash recovery either finalizes or quarantines partial chunks.
- No raw speech content appears in logs or crash reports.
