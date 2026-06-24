# ADR-0010: Use Preserve-Local FIR as the Current Echo Guard Cleanup Path

Status: accepted
Date: 2026-06-23

## Context

Real speaker playback can leak remote participant audio into the local microphone track. MurmurMark already records `mic` and `remote` as separate raw tracks, so the problem is offline processing: use the clean `remote` reference to reduce or flag the leaked copy in `mic`.

Tests on a private validation session showed:

- WebRTC APM and SpeexDSP wrappers are useful baselines but did not remove enough leak on this real session.
- A static global FIR did not generalize across the full call.
- A local FIR fit on nearby remote-dominant audio worked well on a manually reviewed short clip.
- Aggressive role masking can remove quiet local greetings and short replies.

## Decision

Use `local_fir` as the current recommended experimental Echo Guard cleanup engine for real speaker-bleed sessions.

Default command:

```bash
murmurmark preprocess ./session --echo clean --echo-engine local_fir
```

Default role policy:

```text
preserve_local
```

This default keeps ambiguous remote-active mic chunks as mildly cleaned and flagged audio. It mutes detected silence, passes local-only speech raw, and promotes `mic_role_masked_for_asr.wav` to `mic_for_asr.wav` only when the quality gate accepts.

## Consequences

Benefits:

- raw `audio/mic/*.caf` and `audio/remote/*.caf` remain untouched;
- quiet local speech is less likely to be deleted;
- the selected ASR mic reflects role policy, not only acoustic subtraction metrics;
- reports expose per-chunk state and action through `speaker_state.jsonl`;
- stricter policies remain available for experiments.

Costs:

- some remote residue can remain audible in `mic_for_asr.wav`;
- ASR may still need transcript-level reconciliation to avoid role confusion;
- the current helper depends on Python, NumPy and SciPy;
- the policy is biased toward recall of local speech, not toward maximum remote removal.

## Rejected Defaults

- `strict_silence`: too likely to delete local overlap speech.
- `role_safe`: safer than strict silence, but still too aggressive for quiet greetings in the tested real session.
- "Remove all remote sound from mic": not reliable enough with room coloration, double-talk and nonlinear speaker/mic paths.
- BlackHole/Loopback routing: solves a different problem and is not required for the normal MurmurMark path.

## Follow-Up

- Run ASR on `mic_role_masked_for_asr.wav` and compare with raw mic ASR.
- Add fixtures for quiet greetings and short local replies.
- Keep WebRTC APM and SpeexDSP as comparison engines.
- Keep transcript-level Echo Guard reconciliation as the final role-safety layer.
