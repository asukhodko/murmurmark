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
  +-- Recognize
  |     ASR adapters
  |
  +-- Reconcile And Attribute
  |     diarization adapters
  |     speaker reconciliation
  |     domain correction
  |
  +-- Evidence And Selection
  |     utterance IDs
  |     quality report
  |     corrections log
  |     source links
  |     selected transcript
  |
  +-- Policy
  |     retention
  |     redaction
  |     provider approvals
  |     privacy modes
  |
  +-- Optional Derivatives
        notes and summaries
        reviewed exports
        retrieval and work proposals
```

## Data Flow

Current implemented CLI path:

```text
record --out SESSION
  -> durable raw writer
       -> audio/mic/*.caf
       -> audio/remote/*.caf
       -> session.json + events.jsonl
  -> optional committed-PCM sidecar
       -> derived/live/transcript.preview.md
       -> derived/live/transcript.draft.md
       -> shadow evidence only

process SESSION
  -> capture health gate
  -> local_fir Echo Guard + speaker_state + exact fallback snapshot
  -> baseline whisper.cpp evidence
  -> guarded personalized pre-ASR Echo selector
       -> direct candidate whisper.cpp or exact local_fir fallback
  -> timeline/start/boundary repair candidates
  -> audit cleanup + reviewed transcript profiles
  -> quality verdict + optional evidence-backed extractive notes
  -> atomic authoritative_handoff.json
       -> selected transcript + SHA-256
       -> verdict + exact next command

enrich SESSION
  -> stronger local audio judge
  -> extended repair evidence and clips
  -> live-vs-batch diagnostics
  -> deferred report without changing the published transcript

review / finish / export / retention
  -> selected batch profile
  -> guarded user-facing bundle
```

Raw CAF files and the selected batch transcript are authoritative. The optional live sidecar reads
only copied PCM after durable raw writes, can fail independently and cannot mutate batch outputs.
Live promotion remains blocked until all corpus parity gates pass.

The causal-recovery branch has completed two promotion-readiness passes with `DO_NOT_PROMOTE`.
Candidate Prefilter v1 now routes all `783` eligible rows (`48` cheap reject, `159` expensive, `576`
unresolved) and removes the earlier order regression. All frozen negative controls remain rejected,
but one post-hoc ASR-noise candidate and a `0/3` holdout runtime result still fail hard gates. The
sidecar remains diagnostic and cannot affect
the raw writer, normal preview or authoritative batch path. Authoritative batch work now follows the
finite residual route: local-recall insertion, lossless chronology repair and an operational
rebaseline. A persistent ASR worker is only a future isolated live hypothesis.

The post-stop pipeline has two explicit phases. `murmurmark process SESSION` stops after the first
safe handoff. `murmurmark enrich SESSION` runs bounded optional diagnostics and may be interrupted
or resumed independently. `murmurmark process SESSION --full` runs both for compatibility. CLI
read commands accept the handoff only while its transcript SHA-256, selected profile and readiness
path still match; a stale or edited result falls back to normal readiness/resume handling.

Reliable Final Handoff v1 makes the phase boundary explicit: unchanged candidate windows reuse
baseline ASR, expensive work has a machine-readable budget/deferred reason, and every terminal
blocker has an executable next action or a bounded manual decision item. Authoritative Incremental
ASR v1 extends exact-identity replay to completed baseline chunks from durable-capture or
interrupted-run provenance; approximate text reuse remains forbidden. Canonical Live ASR Producer
v1 proved exact remote parity but closed with `DO_NOT_PROMOTE` because the parallel mic branch limits
wall-time benefit to `2.8651%..4.1040%`. Causal Canonical Mic ASR v1 then measured the selected
post-Echo path and closed with `DO_NOT_PROMOTE`: `0/147` candidate windows matched and bounded
prefixes through `120s` remained different from final PCM. The current exact mic boundary is
session end. Remote Speaker Evidence Map v1 remains the conservative seed map over the already
authoritative remote track. Remote Speaker Diarization v2 established the word/frame view; promoted
Coverage v3 then raised attributable remote speech to `93.9312%` while preserving every v2 label,
selected word, timestamp and aggregate fallback. `--rich` verifies the promoted policy and current
input lineage; explicit session-local decisions remain the only way to replace anonymous display
IDs. Transcript Perfection Corpus v1 remains the measurement baseline. Its current ranked closure is
complete: Residual Evidence v4 recovered 124 words / `83.640s` but missed both `20%` gates and closed
with `DO_NOT_PROMOTE`. Speaker-Resolved Transcript Default v1 is promoted as the normal transcript
surface with exact aggregate fail-open. Lexical Accuracy Reference Corpus v1 closed
`REFERENCE_INSUFFICIENT`. Independent WavLM recovered 53 words / `23.357s`, missed both coverage
gates and closed `DO_NOT_PROMOTE`. Remote Speaker Residual Reference Corpus v1 froze 278 blind items
and closed `REFERENCE_INSUFFICIENT` because direct truth covers 0/53 proposals. Controlled Remote
Speaker Truth Lab v1 then produced exact held-out truth: the Coverage v3 control qualified, while the
WavLM candidate failed short-word and unseen open-set gates. Duration-Aware v2 then preserved
precision but reached only `55.1402%` known recall and `32.1429%` boundary recall on blind hard-v2.
Segment-Context v1 also failed hard-v3 with `44.5087%` known recall, boundaries `0/20` and two
open-set errors. Error Decomposition v1 then measured speaker identity as the dominant bottleneck:
gain `0.351382` versus `0.063882` for segmentation and `0.036364` for overlap/open-set. ECAPA passed
synthetic hard-v4 but failed real-session promotion; interval and enrollment variants also failed
their material gates. Blind review then completed 33 primary and 8 repeat answers at consistency
`0.875`. Direct adjudication kept Coverage v3: the candidate gained three correct identities, lost
two correct controls and increased fail-closed unsafe accepts from 8 to 13. Purity v2 restored
control safety but added no identity because only 7/14 profiles qualified. Homogeneous enrollment,
label-independent re-clustering, WeSpeaker and temporal AHC/VBx then closed without promotion.
Disjoint Truth v2 completed 72 primary + 12 repeat decisions. Frozen ERes2NetV2 correctly attributed
12/21 positives with no wrong-known-speaker substitutions, but made seven unsafe special accepts
and lost two truth-v1 controls, so it closed `KEEP_COVERAGE_V3`. Cluster Purity Reference v1 then
aligned `92.8157%` of a private independent-machine group reference and found 10 remote voices
compressed into four acoustic clusters, weighted purity `0.898106` and minority recall `0`. It
closed `ADVANCE_SEGMENTATION`. Current work improves label-independent boundaries and rare turns
before identity assignment. Open Truth v2 is development evidence; future promotion requires a new
frozen terminal set. Notes remain optional.

Target transcription path:

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

optional derivatives/
  notes/*
  exports/*
  work_proposals/*
```

The transcription path is complete at the versioned evidence package. Optional derivatives may
consume it, but they are not a success condition for MurmurMark and cannot mutate the selected
transcript.

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
- low-impact derived execution: normal-priority durable capture followed by either the bounded
  `background` profile or work-conserving `opportunistic` processing (`nice=20`, no Darwin
  background clamp); foreground `performance` remains explicit;
- Echo Guard diagnostics and derived audio selection;
- window planning for long sessions;
- ASR and diarization adapters;
- speaker reconciliation;
- glossary-aware correction;
- transcript source of truth;
- quality report.

Current implementation note:

- the source of truth is `derived/transcript-simple/whisper-cpp/resolved/clean_dialogue*.json`;
- word/frame-level Remote Speaker Diarization v2 and Coverage v3 are promoted optional evidence;
  ordinary read/handoff/export uses their fingerprint-verified speaker-resolved view and exact
  aggregate fallback;
- `transcript.shadow_v2.md` is a candidate export only when `repair_comparison.json` passes.

Does not own:

- meeting interpretation;
- action item ownership beyond transcript evidence;
- docs updates.

### Synthesis

Synthesis is an optional derivative. It does not define MurmurMark's mission or block the
speaker-resolved transcript roadmap.

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
