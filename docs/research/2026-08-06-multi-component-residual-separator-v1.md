# Multi-Component Residual Separator Qualification v1

Date: 2026-08-06

Decision: `READY_FOR_STRONGER_LOCAL_SEPARATOR`

Production impact: none. Speaker-Preserving Neural Echo v2 remains the selected pre-ASR profile and
exact fallback. Hard and sealed audio were not opened.

## Question

Can the remaining local-preservation class be closed with a small local separator that explicitly
decomposes production mic audio into four components?

```text
production v2 mic + frozen echo estimate + Target-Me query
    -> Target-Me + remote echo + other-local + unexplained residual
```

The candidate had to preserve mixture consistency, distinguish the correct speaker query from a
wrong query, keep nearby speech outside Target-Me and return production v2 for every unsupported
window. Transcript cleanup received no promotion credit.

## Frozen Protocol

Policy: `policies/multi-component-residual-separator-v1.json`.

The policy freezes:

- production v2 policy, corpus decision and report;
- Residual Echo Ceiling Map v1 and Alignment/Echo-Path v3 decisions;
- Target-Me Identifiability Corpus v1 publication and split-disjoint speakers;
- 320 train and 85 dev four-component mixtures;
- four output stems, exact-remainder mixture consistency and production fallback;
- candidate ladder, dev gates, 600-second runtime budget and one bounded revision;
- no hard/sealed access before an immutable full dev pass.

The train/dev mixtures contain independently sourced `target_me`, `remote_echo`,
`other_local_speech` and `other_local_noise`. Speaker state and ASR text are not labels.

## Candidate Ladder

1. A query-agnostic accounting control assigns the unexplained local mixture to Target-Me. It is
   not promotion eligible.
2. The frozen Reference-Conditioned v2 model is retained as the three-stem control.
3. `four_stem_film_gru_v1` predicts query-target and unexplained-residual complex masks. The frozen
   echo estimate remains its own stem; other-local is the exact mixture remainder.
4. No pretrained initialization was accepted: no available offline model had a pinned compatible
   four-stem query contract, local runtime and verified license.

The only allowed revision increased the residual-loss weight from `0.35` to `1.0`. No gate, split,
architecture or preservation rule changed.

## Result

| Metric | Initial | Final | Dev gate |
| --- | ---: | ---: | ---: |
| Target-Me SNR median | `5.497 dB` | `5.561 dB` | `>= 8.0 dB` |
| Target-Me improvement median | `5.688 dB` | `5.493 dB` | `>= 3.0 dB` |
| other-local SNR median | `4.538 dB` | `4.443 dB` | `>= 8.0 dB` |
| paired-query margin median | `3.971 dB` | `3.891 dB` | `>= 4.0 dB` |
| query collapse rate | `0.0` | `0.0` | `<= 0.05` |
| absent-query attenuation median | `6.915 dB` | `6.803 dB` | `>= 12.0 dB` |
| unexplained-residual SNR median | `-1.147 dB` | `-1.545 dB` | `>= 6.0 dB` |
| reconstruction max error | `0.0` | `0.0` | `<= 0.00001` |
| runtime | `561.309s` | `560.864s` | `<= 600s` |

The candidate improved Target-Me over the raw local mixture and did not ignore the query. It did
not separate quiet/absent target speech, nearby speech and residual noise well enough for direct
ASR. Increasing the residual objective did not improve the residual stem and slightly reduced
identity margin. Further tuning on the same dev speakers would be overfitting.

The final model state repeated exactly; an earlier final run completed in `489.980s`, while the
verification replay completed in `560.864s`:
`1f25ee2a2c69a7efb387f583527ce0a923746a983377b7685f6d0027e489df5e`.
Runtime is a gate, not part of the deterministic model fingerprint.

## Interpretation

Explicit four-stem accounting is the right contract but the small locally trained FiLM-GRU is below
the quality ceiling. The failure is not caused by reconstruction, query collapse or runtime. It is
limited by source quality and speaker absence discrimination. A stronger candidate needs:

- more split-disjoint Target-Me and nearby-speaker supervision;
- a larger pretrained speech-separation backbone with a pinned license, hash and offline runtime;
- stronger query-conditioned identity margins for quiet and absent Target-Me;
- an explicit residual/noise head while retaining exact production fallback.

Hard, sealed and real direct-ASR stages remain unopened because dev did not pass. This prevents a
weak waveform candidate from receiving apparent benefit from transcript cleanup.

## Reproduction

```bash
.venv/bin/python scripts/multi-component-residual-separator-v1.py preflight
.venv/bin/python scripts/multi-component-residual-separator-v1.py train-dev
.venv/bin/python scripts/multi-component-residual-separator-v1.py decide
.venv/bin/python scripts/multi-component-residual-separator-v1.py verify
.venv/bin/python scripts/check-multi-component-residual-separator-v1.py
```

Generated private reports remain under
`sessions/_reports/multi-component-residual-separator-v1/`.
