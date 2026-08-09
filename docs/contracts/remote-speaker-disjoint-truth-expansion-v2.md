# Remote Speaker Disjoint Truth Expansion v2

Status: frozen, blind review in progress
Version: `2`

## Purpose

This contract creates a real-session speaker-truth set disjoint from the 33 primary items used to
develop and reject earlier identity backends. It must be complete before another speaker model is
selected, tuned or promoted.

The candidate pack was frozen before prior truth answers or Coverage labels were read:

- 245 residual items remained after excluding every overlapping v1 primary interval;
- 72 primary items / 148 words / 155.440894 seconds were selected across all six sessions;
- 12 primary items were duplicated as hidden repeat slots;
- all available ECAPA/WavLM, WeSpeaker and temporal disagreement cases are represented;
- short turns, utterance and session boundaries, mixed/overlap evidence and the five-speaker
  session are represented;
- all 355 inherited production guards and the 30-source Transcript Perfection snapshot are frozen.

## Frozen Boundary

Private ignored artifacts live under:

```text
sessions/_reports/remote-speaker-disjoint-truth-expansion-v2/private/
  candidate_pack.frozen.json
  review_pack.json
  selection.jsonl
  review_queue.jsonl
  slot_map.jsonl
  answers.jsonl
  exemplars.jsonl
  frozen_inputs/
  clips/
  exemplars/
```

`review_queue.jsonl` exposes only an opaque slot, session alias, target audio, anonymous available
profiles and bounded exemplars. It contains no item ID, stratum, model result, score, transcript
text, human name, previous answer or suggested outcome. Hidden repeats are indistinguishable from
primary slots.

The 19 exemplar clips use one of three explicit purity bases: a human-reviewed single-speaker v1
target, a temporal single-cluster interval agreeing with the frozen Coverage mapping, or a
single-remote-speaker session topology. Confirmed mixed v1 answers and ambiguous temporal clips are
excluded. Profiles without a bounded exemplar are not offered; the reviewer uses
`unknown_speaker` instead.

## Review And Decision

Allowed outcomes are an offered session-local `remote_speaker_XX`, `unknown_speaker`, `mixed` or
`unusable`. Human names and cross-session identity are forbidden.

`DIRECT_TRUTH_V2_READY` requires:

- 72 primary and 12 hidden repeat answers;
- at least 18 attributed primary items from at least four sessions;
- repeat consistency at least `0.85`;
- exact pack, source, privacy and production-guard conservation.

Until every gate passes, the public result is `REFERENCE_INSUFFICIENT`. This is also the terminal
result when clean anonymous exemplars or repeat consistency prove insufficient. Neither decision
changes production.

## Safety Boundary

Coverage v3, selected transcripts, raw CAF, primary ASR, Echo Guard, the v1 truth set and all prior
experiments are immutable. v2 performs no model selection, threshold tuning or production
promotion. Public artifacts contain aggregates and hashes only.
