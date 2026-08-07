# Transcript Perfection Corpus v1 Baseline

Status: `BASELINE_ESTABLISHED`

The unified scorecard was built from 12 frozen source artifacts. All byte counts, SHA-256 values,
schemas and source-level safety gates passed. Repeated offline generation was byte-identical, and a
deliberately changed source produced `INVALID_INPUTS`.

## Result

| Dimension | Result |
|---|---|
| Recognized words | conservation passes; lexical correctness `not_measured` |
| Chronology | 14 residual rows / `62.690s` |
| Me/remote roles and overlap | 65 residual rows / `196.280s` |
| Remote speaker turns | coverage `0.919071`; `797.773s` explicit unknown |
| Missing Me | 4 residual rows / `21.120s` |
| Remote leakage | v2.17 promoted subset passes; ambiguous rows remain visible |
| Acoustic modes | 17/17 labeled sessions matched; one valid uncertain no-speech case |

No aggregate score or total residual seconds are published because source scopes overlap and differ.

## Selection

The largest actionable measured residual is `unknown_remote_speaker`: 1219 preserved remote words
and `797.773s` across six sessions lack supported speaker attribution. The next bounded goal is
**Remote Speaker Coverage v3**. It must reduce this frozen unknown region without weakening
attributed-only B-cubed F1 `0.960690`, pairwise precision `0.959564`, word conservation, timestamp
order, one-to-one controls or exact aggregate fallback.

The absent whole-session human word reference remains a separate evidence gap. Word conservation is
not presented as lexical accuracy.

## Reproduce

```bash
murmurmark corpus perfection all
murmurmark corpus perfection all --verify-existing
```

Tracked lineage: `docs/testing/transcript-perfection-corpus-v1-manifest.json`.
