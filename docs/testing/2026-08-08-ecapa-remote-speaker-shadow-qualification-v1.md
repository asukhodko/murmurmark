# ECAPA Remote Speaker Shadow Qualification v1 Result

Date: 2026-08-08
Decision: `DO_NOT_PROMOTE_REAL_IDENTITY`

## Result

The lab-qualified ECAPA backend was frozen before inference and applied only to 278 bounded residual
items from six real sessions. It proposed 68 assignments covering 156 words and 211.099681 seconds.

| Metric | Result | Gate |
|---|---:|---:|
| Recovered words | 156/851, `0.183314` | `>= 0.20` |
| Recovered seconds | 211.099681/598.239509, `0.352868` | `>= 0.20` |
| Structural 1x1 precision | `1.0` over 27 words | `1.0` |
| Independent machine-reference precision | `0.878788` over 33 words | `>= 0.99` |
| Human-reviewed proposal words | `0` | `>= 50` for promotion |
| Embedding failures | `2`, both fail-open | no corpus failure |
| Runtime | `9.278614s` | `<= 900s` |
| Replay | byte-identical | required |

The result fails both the word-recovery and independent-reference precision gates. Direct reviewed
truth is also absent, but it is not the only blocker. Thresholds were not changed after the result.

## Interpretation

ECAPA transfers useful identity evidence from the synthetic lab to real audio: the shadow would
raise attributable speech from `93.9312%` to `96.0727%` if it were safe. The accepted set is not
precise enough under the available independent reference, and the word gain misses the predeclared
minimum. The reference itself is utterance-level and can disagree with true speaker changes inside
an utterance, so this number must be decomposed before choosing the next model or fusion topology.

## Safety Result

- all 851 original words and timestamps are conserved;
- existing Coverage v3 labels and chronology are unchanged;
- selected transcripts, raw CAF, primary ASR and Echo Guard are unchanged;
- no names or cross-session voice links were created;
- two silent clips remained `unknown`;
- public artifacts contain no speech text or absolute paths.

Coverage v3 remains authoritative. The 68 proposals are retained only as private evidence for the
next error-decomposition goal.
