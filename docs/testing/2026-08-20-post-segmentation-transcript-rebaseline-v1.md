# Post-Segmentation Transcript Rebaseline v1

Date: 2026-08-20

Decision: `REBASELINE_ESTABLISHED`.

## Scope

Six fresh real sessions were frozen behind private aliases and artifact SHA-256 identities. The
public report contains no session identifiers, paths, speech text or names. The corpus includes
1x1 and group meetings, five strict Coverage v3 read surfaces and one disclaimer-bearing provisional
surface.

No session artifact was rebuilt. Coverage v3, selected dialogue, ASR, Echo Guard and raw CAF stayed
unchanged. Exact in-memory replay matches every public output byte-for-byte.

## Result

| Dimension | Result |
|---|---|
| Frozen sessions | 6/6 current |
| Capture duration | `13936.399s` |
| Strict rich / provisional | 5 / 1 |
| Word, role and timestamp conservation | passed |
| Read-surface coherence | passed |
| Strict Coverage v3 remote speech | `7862.440s`, 15332 words |
| Strict explicit unknown | `397.543570s`, 547 words |
| Unknown seconds ratio | `5.0562%` versus frozen `6.0688%` |
| Fresh lexical truth | unavailable; machine review queue only |
| Capture continuity | failed: 3 gaps / `2.268542s` |

The rebaseline evidence is complete, while the product no-regression gate is false. One session has
three high-confidence restart-bounded intervals without captured mic and remote PCM. Each interval
is shorter than one second, but downstream ASR cannot recover speech that was never recorded.

## Ranked Direction

1. `capture_continuity`: hard source regression, 3 gaps / `2.268542s`.
2. `remote_unknown_evidence`: the largest downstream residual; strict Coverage v3 leaves
   `397.543570s` and the selected provisional surface adds further explicit unknown.
3. remaining review, chronology and lexical-evidence debt.

The next executable goal is **Capture Continuity Loss Closure v1**. Remote Unknown Evidence Recovery
v1 follows it. This ordering comes from source irreversibility, not from an aggregate quality score.

## Replay

```bash
murmurmark corpus post-segmentation-rebaseline all --verify-existing
```
