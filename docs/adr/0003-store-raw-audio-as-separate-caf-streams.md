# ADR-0003: Store Raw Audio as Separate CAF Streams

Status: accepted  
Date: 2026-06-22

## Context

The pipeline needs to process the user's microphone and remote meeting audio differently.

## Decision

Store raw capture as two independent CAF stream families:

```text
audio/mic/*.caf
audio/remote/*.caf
```

Do not use one stereo L/R file as source of truth.

## Consequences

Benefits:

- mic track has role hint `me`;
- remote track can be diarized independently;
- ASR retries are simpler;
- retention can delete raw tracks explicitly;
- CAF is suitable for long recordings.

Costs:

- derived WAV/FLAC chunks may be needed for ASR tools;
- session package has more files.

## Alternatives

- Stereo WAV/AIFF with mic left and remote right.
- MKV/MP4 multitrack recording.
- One mixed audio file.

These can be export formats, not canonical raw session format.

