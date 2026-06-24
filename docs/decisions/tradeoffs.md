# Tradeoffs and Rejected Options

This document records choices that are easy to reopen later.

## Accepted: Build a Native Capture Layer

Decision:

Use Core Audio Process Tap for meeting-app audio and AUHAL/Core Audio for selected microphone input.

Why:

- keeps meeting app routing unchanged;
- avoids virtual device setup;
- aligns with local-first privacy story;
- gives a narrow audio-only foundation.

Cost:

- macOS-specific;
- more implementation complexity;
- requires careful permission and process handling.

## Rejected as Default: OBS

OBS is a useful baseline and fallback, but not the MurmurMark foundation.

Reasons:

- UI is video/streaming-oriented;
- more manual routing;
- easier to record too much or too little;
- not a clean product experience for repeated meeting capture;
- GPL code cannot be copied into a permissive project.

## Rejected as Default: BlackHole / Aggregate Device Routing

Reasons:

- changes or complicates audio routing;
- higher risk of broken calls;
- manual setup is fragile;
- virtual device approach conflicts with "works beside the meeting app";
- GPL constraints matter if integrating code.

Still useful:

- legacy/manual fallback;
- debugging;
- reference for loopback patterns.

## Rejected as Dependency: Audio Hijack

Audio Hijack is a strong UX reference and commercial benchmark. It should not be a required dependency.

Reasons:

- proprietary external dependency;
- limits open-source implementation;
- product goal is a native local-first system, not a wrapper.

Useful ideas:

- Voice Chat preset;
- clear local/remote split;
- recording inspector;
- menu bar control;
- pause/split/mark controls;
- file limits.

## Accepted: CAF Raw Streams

Decision:

Store raw mic and remote audio as separate CAF streams.

Why:

- CAF handles long recordings better than classic WAV/AIFF limits;
- separate streams simplify ASR and diarization;
- mic track carries role hint `me`;
- remote track can be diarized independently;
- retention/deletion can be track-aware.

Cost:

- some ASR tools prefer WAV/FLAC, so derived working files are needed.

## Rejected: One Stereo L/R File as Source of Truth

Reasons:

- every pipeline step must split channels;
- harder per-track retention;
- less explicit semantics;
- mistakes become easier.

Stereo export may still be useful for compatibility.

## Accepted: File Contract Between Capture and Pipeline

Decision:

Capture writes a session package. Pipeline reads it later.

Why:

- ASR crashes cannot lose recording;
- processing can happen on another machine;
- adapters can change without recorder changes;
- easier debugging and reproducibility.

Cost:

- more manifest/schema design upfront.

## Accepted: Preserve-Local Echo Guard Default

Decision:

Use `local_fir` with `preserve_local` as the current default for real speaker-bleed cleanup experiments.

Why:

- the session already has separated `mic` and `remote` references;
- local FIR worked better than the first WebRTC/Speex attempts on the real test session;
- preserving quiet local speech is more important than removing every trace of remote residue;
- stricter policies remain available for comparison.

Cost:

- remote residue may still be audible;
- transcript-level reconciliation remains necessary;
- the current helper depends on Python, NumPy and SciPy.

## Accepted: Heavy-Local Transcription Profile

Decision:

Start the serious design around VibeVoice-ASR, pyannote Community-1, GigaAM-v3 and later Qwen3-ASR/ForcedAligner.

Why:

- prioritizes accuracy and domain handling over speed;
- keeps sensitive raw audio local;
- gives independent checks for speaker and text uncertainty.

Cost:

- heavier hardware requirements;
- more pipeline complexity;
- model licensing/access must be tracked.

## Rejected: "One Model Produces Final Notes"

Reasons:

- hides uncertainty;
- mixes transcript and interpretation;
- increases hallucination risk;
- makes evidence tracing weak.

Instead:

```text
ASR -> transcript evidence -> synthesis -> evidence guard -> human review
```

## Accepted: Delayed Synthesis with Privacy Policy

Decision:

Synthesis can use local or frontier models, but only after transcript/evidence exists and under a policy file.

Why:

- raw audio remains local;
- powerful models can be used for reasoning and docs integration;
- payload can be reviewed;
- notes can cite utterance IDs.

Cost:

- more moving pieces;
- local-only users may get weaker synthesis until local models improve.

## Accepted: Lowercase Repository Name

Decision:

Use `murmurmark` for repository and CLI, `MurmurMark` for product name.

Why:

- clean GitHub URL;
- predictable package and CLI names;
- leaves room for ecosystem names like `murmurmark-capture`.
