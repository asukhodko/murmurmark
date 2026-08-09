# Remote Speaker Direct Truth Seed v1

Status: completed with `DIRECT_TRUTH_SEED_READY`
Version: `1`

## Purpose

The contract freezes a small blind real-session speaker-truth seed before another remote identity
backend is proposed. It converts the measured enrollment disagreement into a bounded review queue
without exposing model suggestions to the reviewer or changing the selected transcript.

Frozen source scope:

- 278 residual items / 851 words;
- all 11 newly accepted enrollment cases;
- all 5 removed control acceptances;
- 6 stable accepted and 6 stable abstention controls;
- 3 protected-overlap candidates and 2 embedding-unavailable candidates;
- 33 unique seed items / 116 words / 90.100820 seconds across 6 sessions;
- 8 hidden repeat slots, for 41 blind review slots total.

## Private Boundary

Ignored private artifacts live under:

```text
sessions/_reports/remote-speaker-direct-truth-seed-v1/private/
  pack.json
  seed_selection.jsonl
  review_queue.jsonl
  slot_map.jsonl
  answers.jsonl
  exemplars.jsonl
  clips/
  exemplars/
```

`review_queue.jsonl` contains only an opaque slot, session alias, target clip, anonymous speaker
choices and local exemplar clips. It does not contain the source item ID, stratum, control or
candidate result, score, reference, transcript text, human name or suggested answer.

`slot_map.jsonl` and `seed_selection.jsonl` are sealed from the reviewer. They preserve the private
mapping needed for aggregate evaluation and deterministic replay.

Tracked artifacts contain only counts, hashes, terminal decisions and safety state:

```text
policies/remote-speaker-direct-truth-seed-v1.json
docs/testing/remote-speaker-direct-truth-seed-v1-manifest.json
```

## Review Contract

Accepted direct outcomes are one listed session-local `remote_speaker_XX`, `unknown_speaker`,
`mixed` or `unusable`. The only accepted truth grade in v1 is `human_reviewed`.

`DIRECT_TRUTH_SEED_READY` requires:

- all 33 primary slots and all 8 repeat slots reviewed;
- all 16 changed cases directly answered;
- at least 8 primary answers attributed to a session-local anonymous speaker;
- repeat consistency at least `0.875`;
- exact source, selection, word, timestamp, clip and production-guard conservation.

Missing answers produce `REFERENCE_INSUFFICIENT`. Missing or changed source/provenance, malformed
answers, a non-blind queue or conservation failure produces `EVIDENCE_BOUND` or a fail-closed CLI
error.

The completed seed contains 33 primary and 8 repeat answers. Primary outcomes are 8 anonymous
speaker labels, 11 `unknown_speaker`, 4 `mixed` and 10 `unusable`; repeat consistency is 7/8
(`0.875`). This is enough for bounded candidate adjudication, not for production promotion. Mixed or
silent exemplars and one repeat disagreement remain explicit evidence limitations.

## Safety Boundary

- Raw CAF, selected transcripts, Coverage v3, primary ASR, Echo Guard and ECAPA shadow are immutable.
- Interval and enrollment candidates remain closed and cannot be retuned from these answers.
- Human names and cross-session voice identity are forbidden.
- The seed evaluates future candidates but never promotes one itself.
