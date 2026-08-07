# Speaker-Resolved Transcript Default v1 Result

Date: 2026-08-08
Decision: `PROMOTE`

## Result

The ordinary `murmurmark transcript` path, Evidence Handoff v2 and guarded export now select
promoted Remote Speaker Coverage v3 when its complete lineage matches the current selected
transcript profile. Unsupported remote speech stays `Colleagues`; stale or missing evidence returns
the exact aggregate Markdown.

The six-session frozen qualification passed:

- 6/6 sessions selected, including two 1x1 and four group calls;
- 14 published session-local anonymous speakers, all inside expected per-session ranges;
- 5/5 internal speaker-boundary cases;
- exact selected dialogue, text, roles, `Me`, timestamps, order and word conservation;
- raw CAF preservation, deterministic replay and frozen artifact identities;
- exact selected bytes in Evidence Handoff; exact aggregate bytes through a real guarded export
  fallback.

The refreshed current-profile corpus leaves 851 words / `597.799508s` aggregate. This is deliberate:
default publication does not weaken v3 merely to assign every word.

## Product Behavior

`status`, `outcome`, meeting final report and export manifest now expose the selected speaker profile,
selection state and fallback reason. `--rich` remains compatible for diagnostics. Human names still
require complete fingerprint-bound session review.

## Reproduction

```bash
murmurmark corpus speaker-default all --verify-existing
murmurmark corpus perfection all --verify-existing
scripts/check.sh
```

Frozen lineage: `docs/testing/speaker-resolved-transcript-default-v1-manifest.json`.
