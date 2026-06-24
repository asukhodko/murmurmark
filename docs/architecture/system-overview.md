# System Overview

MurmurMark is split into stages with file contracts between them.

The recorder must never depend on ASR, diarization or summarization. Its job is to create a trustworthy local session package. Later stages can be retried, replaced, moved to another machine, or skipped.

## Components

```text
MurmurMark
  |
  +-- Capture
  |     target app audio -> remote CAF
  |     selected mic      -> mic CAF
  |     session.json
  |     events.jsonl
  |
  +-- Preprocess
  |     decode CAF
  |     normalize working audio
  |     VAD
  |     Echo Guard diagnostics
  |     derived Echo Guard cleanup
  |     selected mic_for_asr
  |     ASR windows
  |
  +-- Transcribe
  |     ASR adapters
  |     diarization adapters
  |     speaker reconciliation
  |     domain correction
  |
  +-- Evidence
  |     utterance IDs
  |     quality report
  |     corrections log
  |     source links
  |
  +-- Synthesis
  |     chapter summaries
  |     decisions
  |     action items
  |     risks
  |     docs patch plan
  |
  +-- Policy
        retention
        redaction
        provider approvals
        privacy modes
```

## Data Flow

Current implemented CLI path:

```text
record command
  |
  v
session/
  session.json
  events.jsonl
  audio/mic/*.caf
  audio/remote/*.caf
  |
  v
preprocess --echo clean --echo-engine local_fir
  derived/preprocess/audio/mic_for_asr.wav
  derived/preprocess/echo/speaker_state.jsonl
  derived/preprocess/echo/echo_suppression_report.json
  |
  v
scripts/transcribe-simple-whispercpp.py
  derived/transcript-simple/whisper-cpp/resolved/
    clean_dialogue.json
    clean_dialogue.shadow_v2.json
    quality_report.json
    quality_report.shadow_v2.json
    transcript.md
    transcript.shadow_v2.md
    repair_comparison.json
```

Target full-product path:

```text
record command/app
  |
  v
session/
  session.json
  events.jsonl
  audio/mic/*.caf
  audio/remote/*.caf
  |
  v
derived/preprocess/
  asr_plan.json
  audio/*
  echo/*
  mic_asr_segments/*
  |
  v
derived/transcript/
  raw/*
  diarization/*
  resolved/transcript.rich.json
  resolved/quality_report.json
  export/transcript.md
  |
  v
derived/evidence_package/
  transcript.rich.json
  quality_report.json
  speaker_map.json
  corrections.jsonl
  context/*
  policy.yaml
  |
  v
notes/
  meeting-notes.md
  decisions.json
  actions.json
  docs_patch_plan.md
```

## Stage Boundaries

### Capture

Owns:

- permission onboarding;
- app/process resolution;
- Core Audio Process Tap;
- mic capture;
- writer safety;
- session package;
- health warnings;
- raw retention trigger.

Does not own:

- ASR;
- speaker identity beyond track hints;
- summarization;
- external model calls.

### Transcribe

Owns:

- local audio preprocessing;
- Echo Guard diagnostics and derived audio selection;
- window planning for long sessions;
- ASR and diarization adapters;
- speaker reconciliation;
- glossary-aware correction;
- transcript source of truth;
- quality report.

Current implementation note:

- the source of truth is `derived/transcript-simple/whisper-cpp/resolved/clean_dialogue*.json`;
- `transcript.rich.json`, `speaker_map.json` and per-remote-speaker diarization are future target artifacts;
- `transcript.shadow_v2.md` is a candidate export only when `repair_comparison.json` passes.

Does not own:

- meeting interpretation;
- action item ownership beyond transcript evidence;
- docs updates.

### Synthesis

Owns:

- context building;
- notes generation;
- evidence validation;
- optional external model calls under policy;
- export plans.

Does not own:

- raw audio processing;
- unreviewed writes to external systems.

## Repository Shape for Implementation

Suggested future layout:

```text
apps/
  macos/
    MurmurMarkApp/

Sources/
  MurmurMarkCaptureCore/
    Permissions/
    ProcessResolver/
    CoreAudioTap/
    AUHALInput/
    Writers/
    Health/
    SessionStore/

cli/
  murmurmark/

pipeline/
  murmurmark_pipeline/
    preprocess/
    asr/
    diarization/
    speaker/
    correction/
    evidence/
    synthesis/
    exporters/

examples/
  domain-packs/
  policies/
```

Swift should own capture. Python should own the initial heavy ASR pipeline because current ASR and diarization ecosystems are stronger there. The boundary is the session package, not an in-memory API.
