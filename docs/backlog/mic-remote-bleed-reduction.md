# Mic Remote Bleed Reduction

Status: active prototype
Created: 2026-06-22

Design home: [Echo Guard Architecture](../architecture/echo-suppression.md) and [ADR-0009](../adr/0009-derived-echo-suppression-only.md).

## Problem

When speakers are used without headphones, remote participant audio can leak from the speakers into the microphone track. The current minimal recorder captures this honestly: `mic` is what reaches the selected microphone, and `remote` is the system/application audio stream.

For later transcription and speaker-aware notes, duplicated remote speech in the mic track can confuse diarization, speaker attribution and local/remote role separation.

## Desired Behavior

Reduce or flag the part of the `mic` stream that repeats content already present in `remote`, even when it appears:

- with lower volume;
- with room coloration;
- with delay;
- with partial echo cancellation artifacts;
- with different clarity from the direct remote stream.

The target product behavior is Echo Guard, not destructive mic cleanup:

1. preserve raw mic;
2. detect remote bleed;
3. avoid attributing remote bleed to the user;
4. create derived clean mic only if quality gates pass;
5. fall back to raw mic when cleanup is risky.

## Candidate Approaches

- Encourage headphones in capture UX as the simple default.
- Stabilize delay and drift estimation first. A broad `0..1500 ms` range or a suspicious `0 ms` median is a measurement problem, not proof that cleanup is ready.
- Use `scripts/echo-guard-delay-lab.py` to find reliable remote-dominant windows and candidate clips before fitting any cleanup filter.
- Prototype an offline adaptive subtractor over exported PCM tracks: estimate delay, polarity and a time-varying transfer function from `remote` to the leaked component inside `mic`, then subtract only when quality gates preserve local speech.
- Use WebRTC Audio Processing Module and SpeexDSP as comparison baselines, not as assumed final answers.
- During transcription, prefer `remote` text over matching `mic` text when the same phrase appears in both streams.
- Add a quality report warning when cross-correlation suggests strong remote bleed in `mic`.

## Constraints

- Never mutate raw CAF recordings in place.
- Store cleaned audio as derived material under `derived/`.
- Keep the original `mic` and `remote` tracks available for audit.
- Do not block the minimal recorder on this work.
- Do not require BlackHole, Loopback or manual macOS routing for the normal v1 path.

## Current Implementation

Implemented:

- `preprocess --echo diagnostic` creates `echo_diagnostics.json` and `echo_segments.jsonl`.
- `linear_baseline`, `speexdsp` and `webrtc-apm` create derived clean candidates behind the same quality-gated contract.
- `local_fir` creates a session-wide cleaned mic candidate from the separated `remote` and `mic` tracks.
- `local_fir` writes `mic_role_masked_for_asr.wav`, `mic_role_preview.wav`, `local_fir_report.json`, `local_fir_segments.jsonl`, `speaker_state.jsonl` and mic ASR chunk manifests.
- Default `local_fir` policy is `preserve_local`, because real-session listening showed that losing quiet local speech is worse than keeping some remote residue.

Validated on a private validation session:

- stable delay around `-76.3 ms`;
- median remote-only reduction around `17 dB`;
- local-only VAD duration preserved;
- suspicious muted chunks checked by ear were correct after the preserve-local threshold update.

## Remaining Acceptance Gates

- Run ASR on `mic_role_masked_for_asr.wav` and compare against raw mic ASR.
- Confirm that remote residue no longer produces enough text to confuse role attribution in realistic sessions.
- Test at least one more real call with different speaker volume, room acoustics and overlap.
- Add automated dropout audit fixtures for quiet greetings and short local replies.
- Keep transcript-level reconciliation as the safety net when audio cleanup leaves residue.
