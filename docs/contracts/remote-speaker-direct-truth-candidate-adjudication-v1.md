# Remote Speaker Direct-Truth Candidate Adjudication v1

## Purpose

This stage compares the unchanged Coverage v3 control and the frozen
`contrastive_reliability_weighted_centroid_v1` candidate once against completed blind,
session-local direct truth. It is an evidence adjudication, not a new classifier or promotion step.

## Frozen Inputs

The policy freezes by SHA-256:

- the 278-item / 851-word residual source and all 355 inherited production guards;
- 33 primary clips, 8 hidden repeats, opaque slot mapping and completed blind answers;
- Coverage v3 decisions and gates;
- enrollment centroids, all 278 control/candidate comparisons, report and replay;
- direct-truth policy, pack, report and byte-exact replay.

The evaluator verifies 65 review-pack files and 355 production guards independently. Any missing,
changed or malformed input yields `EVIDENCE_BOUND`.

## Truth Semantics

`remote_speaker_*` on a primary slot is positive anonymous identity truth. `unknown_speaker`,
`mixed` and `unusable` are fail-closed abstention evidence: an accepted identity on such a row is
unsafe, but the row is never converted into a positive speaker label. Repeats measure reviewer
consistency only and do not duplicate identity metrics.

The decision policy was frozen before evaluation. `ADVANCE_DIRECT_TRUTH_IDENTITY` requires at least
two net additional correct identities and a `0.20` gain ratio, with no new false identity, no lost
correct control identity and no increase in fail-closed unsafe accepts. Threshold search and
post-result adjustment are forbidden.

## Outputs

Private, Git-ignored evidence:

```text
sessions/_reports/remote-speaker-direct-truth-candidate-adjudication-v1/private/
  evaluation_core.json
  item_adjudication.jsonl
```

Portable outputs:

```text
sessions/_reports/remote-speaker-direct-truth-candidate-adjudication-v1/
  input_manifest.public.json
  remote_speaker_direct_truth_candidate_adjudication_report.json
  remote_speaker_direct_truth_candidate_adjudication_report.md
  replay_report.json
```

The tracked aggregate is
`docs/testing/remote-speaker-direct-truth-candidate-adjudication-v1-manifest.json`.

## Outcomes

- `ADVANCE_DIRECT_TRUTH_IDENTITY`: open a separate corpus qualification; production is unchanged.
- `KEEP_COVERAGE_V3`: close this candidate and preserve Coverage v3.
- `EVIDENCE_BOUND`: repair provenance or bounded truth acquisition only.

The frozen result is `KEEP_COVERAGE_V3`: the candidate gained three correct identities, lost two
correct controls, and increased fail-closed unsafe accepts from 8 to 13. Net correct gain was one
item (`0.125`), below both material gates.

## Safety

No transcript, timestamp, raw audio, ASR, Echo Guard, Coverage v3 label or threshold is changed.
Public outputs contain no speech text, session IDs, human names, reviewer identity, embeddings or
absolute paths. Cross-session voice identity remains forbidden.
