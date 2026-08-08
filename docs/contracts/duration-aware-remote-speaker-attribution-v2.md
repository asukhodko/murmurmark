# Duration-Aware Remote Speaker Attribution v2 Contract

## Purpose

This laboratory contract tests whether duration-aware speaker prototypes, cohort-normalized WavLM,
or conservative WavLM/Resemblyzer agreement can safely reduce anonymous `unknown_speaker` words.
It cannot change a selected transcript or the promoted Coverage v3 selector.

## Evidence Separation

- Controlled Remote Speaker Truth Lab v1 `train`, `dev`, and `hard` are development evidence.
- Hard-v2 uses different renderer voices and private scripts.
- Four known hard-v2 voices have separate enrollment audio whose script does not occur in hard-v2.
- Two hard-v2 open-set voices have no enrollment.
- Sealed hard-v2 scenarios contain source stems, canonical mixtures, and exact word/speaker/time truth.
- `develop` reads only v1 evidence and the public hard-v2 hash.
- A candidate freeze pins policy, implementation, hard-v2 hash, topology, thresholds, and development metrics.
- `evaluate-hard` creates `hard_v2_opening_ledger.json` before reading sealed truth. The decision count is exactly one.

## Fixed Topologies

The policy permits exactly three candidates:

1. `duration_binned_prototype_bank`;
2. `cohort_normalized_wavlm`;
3. `conservative_resemblyzer_wavlm_fusion`.

Mixed words are detected only from timestamp overlap. Every weak or conflicting score becomes
`unknown_speaker`; no topology may force an identity to improve coverage.

## Outputs

Public, private-safe artifacts live under
`sessions/_reports/duration-aware-remote-speaker-attribution-v2/`:

- `hard_v2_public_manifest.json`;
- `development_report.json`;
- `duration_aware_remote_speaker_attribution_report.json` and `.md`;
- `replay_report.json`.

Generated speech, source stems, truth, embeddings, predictions, candidate freeze, and opening ledger
remain under ignored `private/`. The tracked manifest contains only portable paths, hashes, aggregate
metrics, and the scientific decision.

## Promotion Gates

`PROMOTE_LAB_CANDIDATE` requires all of:

- exact word conservation and direct truth coverage;
- B-cubed F1 and pairwise precision at least `0.98`;
- known-speaker recall at least `0.98`;
- boundary recall `1.0`;
- zero open-set false attribution;
- every mixed word fails closed;
- no regression against the Coverage v3 control;
- deterministic replay and unchanged production boundaries.

Any failed gate yields `DO_NOT_PROMOTE_TOPOLOGY`. Synthetic truth never labels real sessions.

## Frozen Result

The selected conservative fusion retained pairwise precision `1.0` and zero open-set false
attributions, but hard-v2 B-cubed F1 was `0.499381`, known-speaker recall `0.551402`, and boundary
recall `0.321429`. Decision: `DO_NOT_PROMOTE_TOPOLOGY`. Coverage v3 remains authoritative.
