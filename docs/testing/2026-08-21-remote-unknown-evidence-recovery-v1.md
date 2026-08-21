# Remote Unknown Evidence Recovery v1 Result

Date: 2026-08-21
Decision: `EVIDENCE_BOUND`

## Result

| Slice | Baseline unknown | Safe shadow recovery | Reduction |
|---|---:|---:|---:|
| Frozen five strict sessions | 547 words / 397.543570s | 10 words / 4.682812s | 1.8282% / 1.1779% |
| Untuned `2026-08-21_15-58-36` | 166 words / 286.137303s | 1 word / 4.580652s | 0.6024% / 1.6009% |

All 547 frozen words have one stable top-level cause and complete decision provenance:

| Cause | Words | Seconds |
|---|---:|---:|
| `conflicting_frame_speakers` | 147 | 74.067191 |
| `embedding_unavailable` | 146 | 108.000000 |
| `margin_below_threshold` | 85 | 35.799814 |
| `protected_remote_overlap` | 50 | 43.218590 |
| `similarity_below_threshold` | 119 | 136.457975 |

WavLM abstained on 291 words. It proposed 59, but 49 lacked an independent structural
confirmation. The conservative intersection retained only 10.

## Truth And Safety

Direct Truth v1 plus disjoint Direct Truth v2 contain 105 primary items. None intersects the 10
newly accepted frozen words, so measured wrong-speaker and fail-closed regressions are both zero,
but candidate precision is not directly established. Promotion gates requiring 5% word/second
recovery and five direct-truth recoveries all failed.

Every conservation gate passed: existing Coverage v3 labels, protected overlap/conflict words,
text, IDs, roles, timestamps, character offsets, chronology, Me utterances and aggregate fallback
are unchanged. Missing independent evidence falls back byte-exactly to Coverage v3.

The useful conclusion is a bound: repeating WavLM with looser thresholds or trusting nearby words
cannot safely close this residual. Coverage v3 remains authoritative.
