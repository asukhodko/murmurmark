# Disjoint Remote Speaker Model Qualification v1 Result

Date: 2026-08-13

## Decision

`KEEP_COVERAGE_V3`

The pinned ERes2NetV2 candidate is locally reproducible and materially different from the previous
ECAPA, WavLM, WeSpeaker and temporal AHC/VBx routes. It did not pass real-session safety gates.

## Freeze

- candidate pack SHA-256: `f7b3e47803d0dacb20c51aec39925570cffea0c6ffae6cc8715069540a5ff194`;
- embedding requests: 1095;
- two embedding runs were byte-identical;
- controlled-dev threshold: similarity `0.55`, margin `0.10`;
- Truth v2 item labels were not read before freeze;
- no threshold or candidate output changed after unseal.

## Independent Truth v2

| Metric | Result |
|---|---:|
| Primary / hidden repeat slots | 72 / 12 |
| Correct identity | 12 / 21 |
| Attributed precision | 0.631579 |
| Attributed recall | 0.571429 |
| Wrong known-speaker substitutions | 0 |
| Unsafe identity on unknown/mixed/unusable | 7 |
| B-cubed F1 | 0.746269 |
| Repeat determinism | 1.0 |
| Exact speaker-count sessions | 3 / 6 |
| Speaker-count MAE | 0.5 |

The boundary cohort contained seven correct identities, five missed identities and five unsafe
special accepts. Boundary attributed precision was `0.583333`; recall was `0.583333`.

## Controls

- truth v1: two previously correct identity controls were lost;
- truth v1: no new wrong-known-speaker substitution, but unsafe special accepts reached 12;
- controlled hard: B-cubed F1 `1.0`, pairwise precision `1.0`, known recall `1.0`, boundaries
  `16/16`, zero open-set false attribution and exact 71-word conservation;
- replay reconstructed evaluation core SHA-256
  `16c40e8fc4e91fbe1426d9a71c005c4663779785d5d8b64b7c2592ab36a91d32`.

## Conclusion

Clean scripted quality did not predict safe behavior on heterogeneous meeting clips. Identity
geometry is no longer the only bottleneck: the system needs reliable evidence that an interval is
usable, speech-bearing and single-speaker before assigning a known session-local identity.

Coverage v3, raw CAF, selected transcripts, primary ASR, Echo Guard and all 355 production guards
remain unchanged. ERes2NetV2 is closed for this route; no shadow profile was created.
