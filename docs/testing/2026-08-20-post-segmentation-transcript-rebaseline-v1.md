# Post-Segmentation Transcript Rebaseline v1

Date: 2026-08-20; current controls requalified 2026-08-21

Decision: `REBASELINE_ESTABLISHED`.

## Scope

Six fresh real sessions were frozen behind private aliases and artifact SHA-256 identities. The
public report contains no session identifiers, paths, speech text or names. The corpus includes
1x1 and group meetings, four strict Coverage v3 read surfaces and two disclaimer-bearing provisional
surface.

No session artifact was rebuilt. Coverage v3, selected dialogue, ASR, Echo Guard and raw CAF stayed
unchanged. Exact in-memory replay matches every public output byte-for-byte.

## Result

| Dimension | Result |
|---|---|
| Frozen sessions | 6/6 current |
| Capture duration | `14423.865s` |
| Strict rich / provisional | 4 / 2 |
| Word, role and timestamp conservation | passed |
| Read-surface coherence | passed |
| Current remote speech | `5329.840s`, 7789 words |
| Current explicit unknown | `451.690506s`, 295 words |
| Unknown seconds / word ratio | `8.4747%` / `3.7874%` |
| Fresh lexical truth | unavailable; machine review queue only |
| Capture continuity | failed: 4 gaps / `1.223687s` |

The rebaseline evidence is complete, while the product no-regression gate is false. Four sessions
contain bounded intervals without captured PCM. Downstream ASR cannot recover speech that was never
recorded. The provisional materializer control now includes qualified cohesive secondary clusters;
it changes only the warned read view and preserves words, roles, timestamps and aggregate fallback.

## Ranked Direction

1. `capture_continuity`: hard source regression, 4 gaps / `1.223687s`.
2. `remote_unknown_evidence`: current read surfaces leave `451.690506s` explicit unknown.
3. chronology, review and missing direct lexical evidence remain separate residuals.

Capture Continuity Loss Closure and Remote Unknown Evidence Recovery have since completed
`EVIDENCE_BOUND`. Terminal Gate Instrumentation owns the current cross-dimension view; this report
remains its fresh transcript-surface input rather than selecting a new goal by itself.

## Replay

```bash
murmurmark corpus post-segmentation-rebaseline all --verify-existing
```
