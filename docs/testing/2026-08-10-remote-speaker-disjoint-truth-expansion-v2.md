# Remote Speaker Disjoint Truth Expansion v2 Freeze

Date: 2026-08-10
State: blind review in progress
Current decision: `REFERENCE_INSUFFICIENT`

## Frozen Pack

- Original residual scope: 278 items / 851 words.
- Pool after excluding all 33 v1 primary intervals: 245 items.
- New primary set: 72 items / 148 words / 155.440894 seconds.
- Hidden repeat set: 12 items; total blind queue: 84 slots.
- Sessions: 6/6, including the five-speaker session.
- Primary strata: 5 model disagreement, 12 temporal boundary, 16 mixed/overlap, 37 utterance
  boundary and 2 short-turn rows.
- Tags additionally cover 29 session-edge, 66 short-turn, 25 mixed/overlap and 15 five-speaker
  cases.
- Pure bounded exemplars: 19 clips for 11 session-local profiles; confirmed mixed exemplars: 0.
- Candidate pack SHA-256: `5eb9016f2e15033f725ba439bd0ad2e2520b55a4779836e910240c5982699ecf`.
- Review pack SHA-256: `cc8195d0ad63cf7a66edd27aa96b28934fa16a818a7e528cb6a6560547a8501c`.

The candidate set was frozen before prior truth answers and model labels were read. The later
exemplar stage used old answers only to accept human-reviewed single-speaker clips and otherwise
required temporal purity or single-speaker topology.

## Current Result

All pack, privacy, disjointness, replay and 355 production-guard checks pass. The decision remains
`REFERENCE_INSUFFICIENT` solely because 84 blind answers have not yet been collected. Coverage v3,
selected transcripts, raw CAF, primary ASR, Echo Guard, v1 truth and production are unchanged.
