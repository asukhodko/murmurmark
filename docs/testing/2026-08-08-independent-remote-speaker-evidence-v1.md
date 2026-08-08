# Independent Remote Speaker Evidence v1 Result

Date: 2026-08-08

Decision: `DO_NOT_PROMOTE`

## Backend And Split

The experiment pinned local `microsoft/wavlm-base-plus-sv` with exact file hashes, offline CPU
runtime and a deterministic development/held-out split of three sessions each. Enrollment and test
utterances are disjoint by hashed utterance ID. The held-out half was not used while choosing the
final `0.90` similarity, `0.04` margin and `0.94/0.08` strict-window thresholds.

## Frozen Corpus Result

| Metric | Result | Gate |
|---|---:|---:|
| Recovered unknown words | 53 / 851 (`6.2280%`) | `>=20%` |
| Recovered unknown seconds | `23.357 / 598.240` (`3.9043%`) | `>=20%` |
| Remaining unknown | 798 words / `574.883s` | explicit fallback |
| Attributed-only B-cubed F1 | `0.962171` | `>=0.962171` |
| Pairwise precision | `0.961675` | `>=0.961675` |
| Internal boundaries | 5/5 | 5/5 |
| Directly referenced recovered words | 0 | `>=20` |

Development recovered 12 words / `6.028s`; held-out recovered 41 words / `17.329s`. The asymmetry is
reported rather than used for threshold tuning.

## Safety Result

- selected words, timestamps, roles, `Me` and all existing v2/v3 labels are unchanged;
- unsupported words retain exact aggregate fallback;
- raw CAF hashes and frozen inputs are unchanged;
- missing model, stale lineage and incomplete enrollment fail open to Coverage v3;
- repeated corpus replay is byte-stable;
- the tracked manifest contains hashes and portable paths, never speech text, names or local
  absolute paths.

## Evidence Limit

The existing reference session has 123 labelled utterances, but none includes the five words newly
recovered there by WavLM. Unchanged aggregate B-cubed metrics therefore do not establish correctness
of the new decisions. Backend agreement is not ground truth.

The result closes threshold tuning for this topology. The justified next step is a blind private
reference corpus over the frozen residual and all 53 recovered proposals; only after direct labels
exist should a constrained or open-set diarization topology be evaluated.
