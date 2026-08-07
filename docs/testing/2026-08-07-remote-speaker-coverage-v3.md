# Remote Speaker Coverage v3 Result

Decision: `PROMOTE`

The frozen six-session qualification recovered anonymous speaker attribution for 368 previously
unknown selected words covering `199.533s`. Existing v2 labels, words, timestamps, roles, overlap and
raw audio remained unchanged.

## Corpus Result

| Metric | v2 baseline | v3 result |
|---|---:|---:|
| Unknown words | 1219 | 851 |
| Unknown seconds | `797.773` | `598.240` |
| Attributable remote speech | `0.919071` | `0.939312` |
| Attributed B-cubed F1 | `0.960690` | `0.962171` |
| Attributed pairwise precision | `0.959564` | `0.961675` |
| Internal-boundary controls | 5/5 | 5/5 |

Unknown-word reduction is `30.1887%`; unknown-second reduction is `25.0113%`. All promotion,
conservation, 1x1, group, boundary, deterministic replay and exact fallback gates passed.

## Remaining Evidence Ceiling

| Cause | Words | Seconds |
|---|---:|---:|
| `similarity_below_threshold` | 113 | `191.081` |
| `conflicting_frame_speakers` | 233 | `131.497` |
| `embedding_unavailable` | 211 | `130.804` |
| `margin_below_threshold` | 278 | `110.882` |
| `protected_remote_overlap` | 16 | `33.536` |

V3 deliberately did not weaken similarity or margin thresholds further. The next experiment must
add cause-specific independent evidence, keep conflicts and overlap explicit, and preserve this v3
result as its fallback.

## Reproduce

```bash
murmurmark corpus remote-coverage all --verify-existing
murmurmark corpus perfection all --verify-existing
```

Tracked lineage: `docs/testing/remote-speaker-coverage-v3-manifest.json`.
