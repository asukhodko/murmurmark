# ADR-0004: Separate Capture from the Intelligence Pipeline

Status: accepted  
Date: 2026-06-22

## Context

ASR and summarization are heavy, failure-prone and likely to change. Capture must be reliable even when models are unavailable.

## Decision

Capture writes a local session package. Later stages read that package.

No ASR, diarization or summarization runs inside the realtime capture path.

## Consequences

Benefits:

- model crash cannot lose recording;
- pipeline can run later or on another machine;
- easier testing and debugging;
- model adapters can change without changing capture.

Costs:

- schema design is required early;
- user may see separate capture and processing stages.

## Alternatives

- one app does capture and transcription in one process;
- stream audio directly into ASR;
- use a hidden local service.

These can be explored later only if the file contract remains primary.

