# Speaker-Preserving Echo Adaptation Corpus v1

Date: 2026-07-23

Decision: **DO_NOT_TRAIN**

Decision fingerprint:
`32a07efc3614bbcc68aeab3b0b77610b000981d989a7a2df096429a959899507`.

## Question

Can existing local MurmurMark sessions provide privacy-safe, session-disjoint supervision for a
remote-conditioned echo suppressor without treating uncertain speaker state or measured
double-talk as clean ground truth?

The selection thresholds were frozen before the first corpus result. The final policy also pins an
explicit split seed and the SHA-256 of each hard-test ASR probe, so later policy metadata cannot
move sessions or replace evidence. It reserves both neural-AEC counterexamples for immutable
hard-test and forbids normalization, cross-split pairing, measured double-talk targets, network
access and source-control publication of audio or work text.

## Corpus Construction

The builder verified every raw, production-derived, transcript and evidence SHA-256 from the
previous nine-session frozen corpus. It also verified the signed aligned-remote timeline before
reading any interval.

The deterministic split contains:

| Split | Sessions |
| --- | ---: |
| train | 5 |
| dev | 2 |
| immutable hard-test | 2 |

The inventory contains `8,789` provenance-bearing interval candidates:

| Category | Candidates |
| --- | ---: |
| local-only | 4,879 |
| remote-only | 3,492 |
| measured double-talk | 409 |
| protected-local | 4 |
| chronology boundary | 4 |
| opening acknowledgement | 1 |

Work text is represented only by normalized SHA-256 and token count. Materialized audio remains
under ignored `sessions/_reports/` paths and is marked private, local-only and non-redistributable.

## Result

Reliable local-only target coverage is sufficient:

| Evidence | Observed | Required |
| --- | ---: | ---: |
| train local-only | `192s` | `120s` |
| dev local-only | `96s` | `30s` |

Remote-conditioned supervision is not:

| Evidence | Observed | Required |
| --- | ---: | ---: |
| train remote-only | `0s` | `120s` |
| dev remote-only | `0s` | `30s` |
| train synthetic pairs | `0s` | `60s` |
| dev synthetic pairs | `0s` | `16s` |
| hard measured double-talk | `6s` | `8s` |
| hard protected-local | `4` items | `4` |
| hard chronology | `4` items | `4` |
| hard opening acknowledgements | `0` items | `2` |

All `3,492` nominal `remote_only` state rows failed the frozen `0.85` confidence gate. Most were
emitted by the current state estimator at `0.8`; some also lacked authoritative remote-text
coverage or sufficient measured echo. Lowering the threshold after seeing the result would turn an
uncertainty label into training truth and violate the corpus contract.

The one opening candidate had no independently confirmed local words. It remains excluded rather
than becoming a synthetic label.

Without accepted remote-only echo examples, the builder cannot construct paired
`target + measured_echo -> target` supervision. Measured double-talk remains evaluation-only
because it has no independent clean near-end target.

## Integrity And Replay

- raw and production-derived SHA-256 values were unchanged before and after materialization;
- no production or transcript artifact was written;
- privacy/licensing checks passed;
- `202` measured rows and `404` private WAV artifacts were materialized;
- corpus fingerprint:
  `091da665f7e55fc62094453233838454ef3b148627ea850da7bdac2c1046ed63`;
- replay compared `414` files with no changed, missing or unexpected artifact;
- no training was performed.

## Decision

The current evidence scope cannot support a responsible neural adaptation experiment. Training now
would require one of three unsafe substitutions:

1. accepting low-confidence remote-only state as truth;
2. treating measured double-talk as if its clean near-end target were known;
3. inventing opening labels from absent or uncertain text evidence.

The decision is therefore `DO_NOT_TRAIN`. `local_fir_role_masked` remains production, and the
critical product path continues to Evidence Notes And Export v2.

Speaker-Preserving Neural Echo v2 remains blocked. It may be reconsidered only after materially new
supervision exists, such as controlled local playback captures with known clean near-end targets,
independently verified remote-only echo, and confirmed opening/double-talk labels.

## Commands

```bash
.venv/bin/python scripts/build-speaker-preserving-echo-adaptation-corpus-v1.py build
.venv/bin/python scripts/build-speaker-preserving-echo-adaptation-corpus-v1.py replay
.venv/bin/python scripts/build-speaker-preserving-echo-adaptation-corpus-v1.py inspect
```

The private artifacts live under:

```text
sessions/_reports/speaker-preserving-echo-adaptation-corpus-v1/
```
