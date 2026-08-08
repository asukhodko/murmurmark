# Remote Speaker Shadow Error Decomposition v1 Result

Date: 2026-08-09
Decision: `ADVANCE_INTERVAL_PURIFICATION`

## Result

All 278 ECAPA shadow items and 851 residual words were reproduced from frozen inputs. The diagnostic
failure scope is 214 items, 699 words and 392.415726 seconds: 210 abstentions plus four accepted
words contradicted by the available coarse independent machine reference.

| Technical axis | Items | Seconds | Item ratio | Seconds ratio | Material score |
|---|---:|---:|---:|---:|---:|
| Interval purification | 93 | 201.273504 | `0.434579` | `0.512909` | `0.434579` |
| Enrollment hardening | 83 | 119.920926 | `0.387850` | `0.305597` | `0.305597` |
| Identity backend | 38 | 71.221296 | `0.177570` | `0.181495` | `0.177570` |

Interval purification passes both materiality gates and leads enrollment hardening by `0.128982`,
above the frozen `0.10` dominance margin. The terminal outcome is therefore fixed without threshold
tuning.

## What Was Explained

- 75 items have insufficient audio evidence; 35 are in the failure scope;
- 66 items show boundary or mixed-speech risk; 58 are in the failure scope;
- 93 items depend on unstable leave-one-out enrollment; 83 are failures;
- 37 failures are below the fixed similarity threshold and one is below the fixed margin;
- both embedding failures are digitally silent clips;
- three wrong reference words use a coarse reference speaker not mapped to session-local enrollment;
- one wrong reference word is an actual ECAPA versus machine-reference identity conflict;
- no human-reviewed truth exists for any of those four mismatch words.

The causes overlap as secondary evidence, but each item has exactly one primary cause under a fixed
priority. This prevents double counting in the routing decision.

## Safety And Reproducibility

- every item and word is accounted for exactly once;
- all 68 accepted proposals and 210 abstentions are unchanged;
- frozen scores and decisions reproduce from the original embedding values;
- replay is byte-identical;
- Coverage v3, selected transcripts, raw CAF, primary ASR and Echo Guard are unchanged;
- public artifacts contain no speech text, names, absolute paths or embeddings.

## Consequence

Another identity model is not the next justified move. The next bounded experiment should improve
only the speaker-relevant interval passed to the unchanged ECAPA backend, compare against the exact
frozen control and remain shadow-only unless all conservation, precision and reference gates pass.
