# Transcript Perfection Corpus v1 Baseline

Status: `BASELINE_ESTABLISHED`

The unified scorecard was built from 12 frozen source artifacts. All byte counts, SHA-256 values,
schemas and source-level safety gates passed. Repeated offline generation was byte-identical, and a
deliberately changed source produced `INVALID_INPUTS`.

## Result

| Dimension | Result |
|---|---|
| Recognized words | conservation passes; exact generated 67-word subset WER/CER `0`; real meetings reference-insufficient |
| Chronology | 14 residual rows / `62.690s` |
| Me/remote roles and overlap | 65 residual rows / `196.280s` |
| Remote speaker turns | coverage `0.939312`; `598.240s` explicit unknown |
| Missing Me | 4 residual rows / `21.120s` |
| Remote leakage | v2.17 promoted subset passes; ambiguous rows remain visible |
| Acoustic modes | 17/17 labeled sessions matched; one valid uncertain no-speech case |

No aggregate score or total residual seconds are published because source scopes overlap and differ.

## Selection

The largest actionable measured residual remains `unknown_remote_speaker`: after promoted Coverage
v3, 851 preserved remote words and `598.240s` across six sessions still lack supported attribution.
Residual Evidence v4 measured that ceiling and closed `DO_NOT_PROMOTE`. The next bounded autonomous
goal is **Independent Remote Speaker Evidence v1**: qualify a genuinely different local backend
without weakening B-cubed F1 `0.962171`, pairwise precision `0.961675`, v2/v3 labels, word
conservation, timestamp order, one-to-one controls or exact aggregate fallback.

Lexical Accuracy Reference Corpus v1 now measures a bounded exact generated subset and keeps the
absent human-reviewed real-meeting reference as a separate blocker. Word conservation and machine
agreement are not presented as real-meeting lexical accuracy. Human-Reviewed Lexical Seed v1 stays
visible as an external-evidence prerequisite, not as the autonomous project goal.

## Reproduce

```bash
murmurmark corpus perfection all
murmurmark corpus perfection all --verify-existing
```

Tracked lineage: `docs/testing/transcript-perfection-corpus-v1-manifest.json`.
