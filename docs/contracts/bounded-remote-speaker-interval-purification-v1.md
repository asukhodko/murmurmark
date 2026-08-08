# Bounded Remote Speaker Interval Purification v1

## Purpose

This diagnostic stage tests one fixed audio-boundary candidate against the frozen ECAPA
real-session shadow. It changes neither the authoritative Coverage v3 attribution nor any selected
transcript.

## Frozen Inputs

The policy freezes by SHA-256:

- all 278 shadow items and 851 residual words;
- the 93 interval-axis failures selected by error decomposition;
- 28 enrollment exemplars and their unchanged ECAPA embeddings;
- control item and word decisions;
- the ECAPA model revision and thresholds `0.50/0.30`;
- selected transcript, Coverage v3 and raw-audio guards inherited from the shadow manifest.

`freeze` fails before candidate materialization if any inherited artifact is absent or changed.

## Candidate

`word_span_guard_80ms_v1` is the only allowed candidate:

1. It applies only to the frozen 93-item interval failure scope.
2. `insufficient_audio_evidence` remains `unknown` without a new embedding.
3. Word spans shorter than `400ms`, zero-length spans and spans overlapped by already attributed
   context remain `unknown`.
4. Every other item is copied to a separate WAV containing the frozen word span plus at most `80ms`
   on each side.
5. Guards are clamped `20ms` away from the nearest already attributed context word.
6. Only the candidate item embeddings are recomputed. Enrollment, centroids and thresholds stay
   byte-identical to the control.

No parameter sweep or result-driven adjustment is allowed in v1.

## Private Outputs

```text
sessions/_reports/bounded-remote-speaker-interval-purification-v1/private/
  input_manifest.json
  candidate_intervals.jsonl
  candidate_audio/<session>/<item>.wav
  embedding_request.json
  candidate_embeddings.json
  item_comparison.jsonl
```

Private outputs may contain anonymous session/item identifiers, audio paths and embeddings. They
must stay ignored by Git.

## Public Outputs

```text
sessions/_reports/bounded-remote-speaker-interval-purification-v1/
  input_manifest.public.json
  bounded_remote_speaker_interval_purification_report.json
  bounded_remote_speaker_interval_purification_report.md
  replay_report.json
```

The tracked manifest is:

```text
docs/testing/bounded-remote-speaker-interval-purification-v1-manifest.json
```

Public artifacts contain aggregate counts, hashes, anonymous technical outcomes and safety gates.
They contain no speech text, human names, absolute paths or embeddings.

## Terminal Outcomes

- `ADVANCE_PURIFIED_SHADOW_CANDIDATE`: the fixed crop materially improves interval-scope recovery
  while all precision, conservation and fail-open gates pass. Promotion remains a separate goal.
- `DO_NOT_ADVANCE_INTERVAL_PURIFICATION`: inputs are valid, but this one candidate has no safe
  material gain. The same evidence must not be used to tune another crop in this goal.
- `EVIDENCE_BOUND`: frozen input, model, conservation or replay cannot be verified.

## Safety

The stage is offline, deterministic and shadow-only. It never writes a transcript profile, changes
speaker labels, edits ASR words/timestamps, infers human identity or links voices across sessions.
