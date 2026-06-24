# ADR-0005: Use a Heavy-Local Transcription Stack

Status: accepted  
Date: 2026-06-22

## Context

The project prioritizes reliable, private, domain-aware transcription over speed.

## Decision

Design v1 transcription around a heavy-local profile:

- VibeVoice-ASR for primary remote rich ASR;
- pyannote Community-1 for independent remote diarization;
- GigaAM-v3 for Russian mic ASR and validation;
- local LLM for strict glossary-aware correction.

Qwen3-ASR and Qwen3-ForcedAligner are planned validators once the base pipeline works.

## Consequences

Benefits:

- stronger handling of long technical meetings;
- independent evidence for speaker boundaries;
- better Russian-domain validation;
- raw audio can remain local.

Costs:

- heavier hardware needs;
- more orchestration;
- model licenses/access must be tracked;
- slower processing is expected.

## Alternatives

- Whisper-only local pipeline.
- Cloud transcription API as default.
- One rich model for both transcript and notes.

These remain fallback or optional modes, not the main design.

