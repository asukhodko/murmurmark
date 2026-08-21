# Human-Reviewed Lexical Seed v1 Contract

Status: frozen, `REVIEW_REQUIRED`

Human-Reviewed Lexical Seed v1 creates direct word truth for real meetings without changing capture,
Echo Guard, ASR, speaker attribution or any selected transcript. The reviewer hears bounded audio and
never sees the production hypothesis before answering.

## Frozen Scope

The policy `policies/human-reviewed-lexical-seed-v1.json` selects two real sessions:

- one group call in headphones or another low-leak acoustic mode;
- one 1x1 call with speaker playback;
- both `Me` and remote speech.

Selection is deterministic. Every source transcript, selected speaker profile, raw CAF, policy,
implementation and materialized clip is fingerprinted with SHA-256. Once an answer exists, `freeze`
only validates the existing bundle; it cannot regenerate it.

## Private Artifacts

All speech-bearing data stays under the ignored directory:

```text
sessions/_reports/human-reviewed-lexical-seed-v1/private/
  frozen_input_manifest.json
  artifact_manifest.json
  slots.jsonl
  review_queue.jsonl
  answers.jsonl
  evaluation.jsonl
  clips/<session>/<role>/*.wav
```

`slots.jsonl` contains the production hypothesis for offline scoring. `review_queue.jsonl` contains
only a stable slot ID, role, session alias, exact interval and clip fingerprint. It must not contain
the hypothesis, transcript text, speaker names or an absolute path.

Schemas:

- `murmurmark.human_reviewed_lexical_seed_freeze/v1`;
- `murmurmark.human_reviewed_lexical_seed_slot/v1`;
- `murmurmark.human_reviewed_lexical_seed_queue_slot/v1`;
- `murmurmark.human_reviewed_lexical_seed_answer/v1`;
- `murmurmark.human_reviewed_lexical_seed_private_evaluation/v1`.

## Review Outcomes

Each slot accepts exactly one outcome:

- `exact_text`: the reviewer types the words heard in the clip;
- `inaudible`: speech exists but cannot be transcribed reliably;
- `mixed`: more than one overlapping or sequential speaker makes one exact answer unsafe;
- `unusable`: the interval cannot provide lexical truth.

Four blind repeats measure reviewer consistency. Repeats do not disclose their relationship to the
primary slot.

## Public Result

The public report and optional tracked snapshot use:

- `murmurmark.human_reviewed_lexical_seed_report/v1`;
- `murmurmark.human_reviewed_lexical_seed_snapshot/v1`.

They contain only counts, WER, CER, substitutions, deletions, insertions, domain-term accuracy,
grouped metrics, gate outcomes and provenance hashes. A privacy guard rejects speech text, speaker
names and absolute paths.

## Decisions

- `REVIEW_REQUIRED`: at least one review slot is unanswered.
- `REFERENCE_READY`: all slots are answered, repeat consistency is `1.0`, both roles and modes are
  covered, every session-role cell has at least four exact primary slots and the corpus has at least
  180 reference words.
- `EVIDENCE_BOUND`: review is complete and reproducible, but one or more evidence gates are not met.

Only `REFERENCE_READY` may unblock Session-Scoped Lexical Context v1. Neither decision changes the
production transcript by itself.

## Safety

- Raw CAF, selected transcripts, Coverage v3 and ASR cache are read-only.
- Missing or changed inputs fail closed before scoring.
- Machine agreement and cloud transcripts are never accepted as truth.
- `replay` must reproduce the private evaluation, public report and tracked snapshot byte for byte.
- A special outcome excludes the slot from WER/CER instead of inventing words.
