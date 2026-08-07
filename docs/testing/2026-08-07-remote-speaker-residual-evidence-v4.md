# Remote Speaker Residual Evidence v4 Result

Date: 2026-08-07

Decision: `DO_NOT_PROMOTE`.

The bounded split-enrollment pass was replayed on the six-session promoted v3 corpus. It recovered
124 words / `83.640207s` while preserving every selected word, timestamp and existing v3 label.
Attributed-only B-cubed F1 stayed `0.962171`; pairwise precision stayed `0.961675`; all five internal
speaker-boundary controls passed.

```text
baseline unknown: 851 words / 598.239509s
recovered:        124 words /  83.640207s
remaining:        727 words / 514.599302s
word reduction:   0.145711
second reduction: 0.139811
```

The candidate missed only the declared `0.20` coverage gates. Relaxing v3 similarity or margin
would trade explicit uncertainty for unsupported identity, so the profile remains audit-only and
promoted Coverage v3 remains the supported source.

The cause ceiling shows where another copy of the same heuristic will not help: all 211
`embedding_unavailable` words remained unresolved; conflicts and protected overlap were preserved by
contract. Future progress there needs a separately pinned local speaker backend or better independent
enrollment evidence, not lower thresholds.

Tracked lineage: `docs/testing/remote-speaker-residual-evidence-v4-manifest.json`.
