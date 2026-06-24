# ADR-0008: Use ScreenCaptureKit for the First CLI Smoke Build

Status: accepted  
Date: 2026-06-22

## Context

ADR-0001 keeps Core Audio Process Tap as the intended capture backend for precise meeting-app audio capture. The first useful checkpoint, however, is smaller: prove that MurmurMark can create a local two-track session package and prepare audio for later transcription.

Process Tap implementation needs more low-level Core Audio work and a broader macOS test matrix. Waiting for that before any executable exists would delay validation of the session contract, command shape, permission flow and export step.

## Decision

Use ScreenCaptureKit audio output plus ScreenCaptureKit microphone output for the first Swift CLI smoke build.

The CLI must still expose the target product shape:

- `doctor`;
- `list-apps`;
- `list-audio-devices`;
- `record`;
- `inspect`;
- `export-audio`;
- separate `audio/mic/000001.caf` and `audio/remote/000001.caf`;
- `session.json`, `events.jsonl` and `pipeline_job.json`;
- ASR-ready WAV export under `derived/asr/`.

## Consequences

Benefits:

- executable recorder exists early;
- session package and export contracts can be tested now;
- no virtual audio device is introduced;
- macOS permission behavior is visible immediately.

Costs:

- current backend is broader than the final per-process tap design;
- first smoke build depends on ScreenCaptureKit permissions;
- app targeting is a practical bridge, not the final process resolver;
- remote audio precision must be revisited when Process Tap lands.

## Exit Criteria

Replace or demote this backend when the Core Audio Process Tap path can:

- capture selected meeting-app audio into a non-empty remote track;
- keep system and meeting-app audio routing unchanged;
- write the same session package contract;
- pass at least the same `inspect` and `export-audio` smoke checks.
