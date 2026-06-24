# Transcription Architecture

Transcription turns the session package into a structured, speaker-aware transcript. It must preserve uncertainty instead of smoothing it away.

## Design Principle

Do not trust one model completely.

Use a primary rich ASR, independent diarization, targeted validators, and strict correction. Keep evidence for disagreements.

```text
session package
  |
  v
preprocess
  |
  +-- echo diagnostics
  +-- optional derived echo suppression
  |
  v
context compiler
  |
  +-- remote -> primary rich ASR
  +-- remote -> independent diarization
  +-- mic_for_asr -> mic ASR
  +-- difficult segments -> validators/alignment
  |
  v
speaker reconciliation
  |
  v
domain correction
  |
  v
transcript.rich.json + quality_report.json
```

## Current MVP Path

The current implemented path is deliberately narrower than the final heavy-local design. It exists so real meetings can already be recorded, processed and audited while the heavier stack remains future work.

Implemented now:

- `scripts/transcribe-simple-whispercpp.py`;
- local `whisper-cli` with `ggml-large-v3-q5_0.bin`;
- compact domain prompt through `--prompt-file`;
- speech-band mic preparation and loudness-normalized remote preparation;
- 60 second ASR windows with 5 second overlap;
- raw segment cache keyed by model, language, prompt and audio-prep settings;
- candidate utterances, role decisions, clean dialogue and Markdown export;
- `remote` as authoritative `Colleagues`;
- `mic` as candidate `Me`;
- timeline repair for long mic segments crossing remote intervals;
- micro-ASR on short local islands;
- `shadow_v2` no-regression profile with audit artifacts;
- start-of-call repair for short opening turns such as `Привет`, `Меня слышно?`, `Привет, да`.

Current output root:

```text
derived/transcript-simple/whisper-cpp/resolved/
  raw_segments*.json
  candidate_utterances*.json
  role_decisions*.json
  clean_dialogue*.json
  overlaps*.json
  quality_report*.json
  timeline_repair_report*.json
  timeline_repair_examples*.jsonl
  timeline_audit_examples*.jsonl
  opening_repair_report.shadow_v2.json
  opening_candidates.shadow_v2.jsonl
  opening_patch.shadow_v2.json
  transcript.md
  transcript.shadow_v2.md
  repair_comparison.json
```

Limitations:

- `Colleagues` is not split into individual people;
- domain terms are only nudged through the prompt, not guaranteed by a correction layer;
- some long overlaps remain audit risks;
- `transcript.rich.json` and `speaker_map.json` are not emitted yet;
- Markdown is useful for reading, but JSON remains the safer processing target.

## Input

```text
session/
  session.json
  events.jsonl
  audio/mic/*.caf
  audio/remote/*.caf
  domain_pack/
```

Track hints:

- `mic` means local microphone, default role `me`;
- `remote` means meeting app audio, default role `remote_participant`;
- mic track may contain room leakage if the user is not wearing headphones;
- remote track may contain multiple participants mixed by the meeting platform.

## Heavy-Local v1 Profile

Status: target architecture, not the current implementation.

Primary:

- remote ASR: VibeVoice-ASR;
- remote diarization: pyannote Community-1;
- mic ASR: GigaAM-v3;
- correction: local LLM with strict JSON/diff policy.

Planned validators:

- Qwen3-ASR for context-heavy re-ASR;
- Qwen3-ForcedAligner for timestamp refinement;
- NeMo Sortformer as optional diarization backend;
- Whisper as fallback/baseline.

## Context Compiler

Input sources:

- meeting title and agenda;
- participant list;
- selected domain pack;
- glossary;
- project/service names;
- previous meeting notes;
- relevant tickets/docs when available.

Outputs:

```text
asr_context_short:
  50-150 likely names, services, abbreviations and terms

asr_hotwords:
  terms suitable for ASR hotword/background input

correction_context_full:
  glossary and spelling rules for post-ASR correction

synthesis_context:
  broader domain summary for later notes
```

Do not pass the entire knowledge base to ASR. Short, relevant context is safer.

## Long Meetings

Do not treat a 60-minute model limit as a product limit.

Use:

- global session timeline;
- ASR windows of 35-50 minutes;
- overlap of 2-5 minutes;
- preferred cuts at silence or speaker turn boundaries;
- automatic smaller windows after model failure;
- overlap reconciliation;
- global speaker reconciliation.

Example:

```text
00:00-120:00 remote.caf
00:00-120:00 mic.caf

windows:
  w001 00:00-48:00
  w002 45:00-93:00
  w003 90:00-120:00
```

ASR window labels are local. Never assume `speaker_1` in two windows is the same person until mapped to global clusters.

## Speaker Reconciliation

Inputs:

- VibeVoice speaker/time/text segments;
- pyannote exclusive diarization intervals;
- optional Sortformer intervals;
- track hints;
- optional participant list;
- optional local voiceprints;
- manual mapping.

Algorithm:

```text
for each ASR utterance:
  find overlapping independent diarization intervals

  if one speaker covers >= 80%:
      assign canonical speaker cluster
  else if multiple speakers overlap:
      split utterance at diarization boundaries
      re-run ASR on split slices when needed
  else:
      mark speaker_uncertain

  keep ASR speaker label and diarization evidence
```

Identity mapping is conservative:

- prefer manual confirmation;
- use meeting participants as candidates;
- use local voiceprint only with confidence;
- leave `Speaker 2` when uncertain.

Wrong attribution is worse than unresolved attribution.

## Mic Leakage

MurmurMark records the physical mic, not necessarily what was transmitted into the meeting. If the user is muted in Teams/Zoom but speaks in the room, the mic track may contain private/local speech.

Design requirements:

- `Pause mic capture`;
- `Stop and delete last N seconds` later if feasible;
- `mic_segment.local_only_possible` quality flag;
- notes policy can exclude local-only/private mic segments;
- documentation must warn that mic capture is local microphone speech.

Leakage detection:

```text
1. Run VAD on mic and remote.
2. Compare nearby mic and remote text.
3. Compare audio envelope similarity.
4. If mic segment resembles remote audio and is much quieter, mark probable leakage.
```

## Echo Guard

Echo Guard is the dedicated preprocess layer for remote audio that leaks into the microphone track.

Its job is not to overwrite or "fix" the raw mic. Its job is to:

- keep `audio/mic/*.caf` unchanged;
- detect where mic resembles delayed remote audio;
- produce derived working audio under `derived/preprocess/`;
- choose `mic_for_asr.wav` conservatively;
- mark transcript segments that should not be treated as `me`.

Default posture:

```yaml
echo_mode: clean
echo_engine: local_fir
mic_asr_policy: use_role_mask_only_after_quality_gate
role_policy: preserve_local
```

See [Echo Guard Architecture](echo-suppression.md) for the derived file contract, quality gates and engine options.

## Domain Correction

Correction is not summarization.

Allowed:

- fix term spelling;
- normalize acronyms;
- restore punctuation;
- normalize service names;
- correct ASR errors supported by glossary/context.

Forbidden:

- add facts;
- remove inconvenient phrases;
- change meaning;
- assign speaker identity without confidence;
- make the speaker sound more certain or polite.

Correction output must include `from`, `to`, `reason`, `confidence`, and `meaning_changed`.

## Outputs

```text
derived/transcript/
  raw/
    vibevoice.remote.json
    gigaam.mic.json
  diarization/
    pyannote.remote.json
  resolved/
    transcript.rich.json
    transcript.corrected.json
    speaker_map.json
    corrections.jsonl
    quality_report.json
  export/
    transcript.md
    transcript.vtt
```

JSON is source of truth. Markdown and VTT are exports.
