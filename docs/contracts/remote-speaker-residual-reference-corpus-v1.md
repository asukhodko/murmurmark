# Remote Speaker Residual Reference Corpus v1

Status: completed with `REFERENCE_INSUFFICIENT`  
Version: `1`

## Purpose

The corpus freezes the unresolved Remote Speaker Coverage v3 surface and provides a blind private
review path for candidate-targeted truth. It must not turn agreement between diarization models into
reference evidence.

Frozen scope:

- 6 real sessions;
- 851 unresolved remote words;
- 598.239509 seconds in the Coverage v3 aggregate;
- 597.799509 seconds attached to word-level intervals;
- one explicit 0.440-second legacy accounting gap without a word ID;
- 53 independent WavLM proposals covering 23.356997 seconds.

## Public And Private Boundary

Private ignored artifacts live under:

```text
sessions/_reports/remote-speaker-residual-reference-corpus-v1/private/
  pack.json
  review_items.jsonl
  sealed_predictions.jsonl
  speaker_exemplars.jsonl
  answers.jsonl
  clips/
  exemplars/
```

They may contain speech text, bounded audio, anonymous session-local speaker choices and reviewer
metadata. `review_items.jsonl` never contains the sealed WavLM candidate.

Tracked artifacts contain only schemas, counts, portable paths, hashes and aggregate decisions:

```text
policies/remote-speaker-residual-reference-corpus-v1.json
docs/testing/remote-speaker-residual-reference-corpus-v1-manifest.json
```

## Truth Contract

Accepted grades are:

- `human_reviewed`;
- `exact_scripted` for sessions explicitly admitted by policy.

Accepted outcomes are a listed session-local `remote_speaker_XX`, `unknown_speaker`, `mixed` or
`unusable`. Model agreement, embedding similarity and ASR agreement are evidence, never truth.

`REFERENCE_READY` requires all structural gates plus:

- direct review of all 53 proposal words;
- direct non-mixed/non-unusable outcomes for all 53 proposal words;
- at least 20 attributable proposal words;
- candidate precision at least 0.98.

Anything weaker is `REFERENCE_INSUFFICIENT` and cannot change the selected transcript.

## Safety Invariants

- Coverage v3, selected dialogue, raw CAF, primary ASR and Echo Guard stay byte-identical.
- Every residual word ID occurs exactly once in the blind pack.
- Sealed predictions are not read by `next` and are revealed only during aggregate evaluation.
- Missing, stale or conflicting evidence fails open to an unresolved reference.
- Replay verifies source and artifact hashes before reproducing the public report.

