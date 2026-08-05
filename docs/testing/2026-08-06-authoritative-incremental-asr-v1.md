# Authoritative Incremental ASR v1

Date: 2026-08-06

Decision: `PROMOTE_BATCH_RESUME / DO_NOT_PROMOTE_LIVE_ORIGIN`.

## Result

The authoritative cache now uses exact sample boundaries and versioned identities covering track,
role, overlap policy, PCM SHA-256 and format, whisper.cpp binary/model, language, prompt and decode
options. Metadata and JSON outputs are committed atomically. Missing, stale, partial, corrupt or
mismatched chunks are decoded normally.

`scripts/check-authoritative-incremental-asr.py` proves that clean full recompute and mixed
cache/recompute produce byte-identical raw ASR, clean dialogue, Markdown and simple JSON. It also
tests PCM, model, binary, prompt, language, thread/options, geometry, corruption and interruption
fail-open behavior.

`scripts/check-live-asr-cache-compatibility.py` independently proves that a fully evidenced
live-origin fixture materializes to the same raw ASR bytes as a clean batch decode and that missing,
legacy, model, prompt, PCM, JSON and partial-write proofs fall back per track.

## Frozen Evidence

The input manifest is
`docs/testing/authoritative-incremental-asr-v1-manifest.json`. Rebuild the decision with:

```bash
scripts/report-authoritative-incremental-asr.py
less sessions/_reports/authoritative-incremental-asr-v1/authoritative_incremental_asr_v1.md
```

Historical checkpoint/cache timing is kept separate from live-origin evidence:

| Session | Cold process | Cache process | Reduction |
|---|---:|---:|---:|
| `2026-08-04_15-01-39` | 1274.465s | 108.449s | 91.49% |
| `2026-08-05_14-16-08` | 18802.810s | 165.246s | 99.12% |
| `2026-08-05_17-00-29` | 13274.232s | 140.740s | 98.94% |

Median reduction is `0.989398`; p90 is `0.990849`. This proves checkpoint value, not fresh
live-origin compatibility.

Three frozen real live sessions contain `0/30` required
`murmurmark.authoritative_live_asr_chunk/v1` proofs. Older sidecars also use incompatible window
geometry or preprocessing. Their text is never accepted by similarity, and ordinary batch ASR is
the safe fallback.

## Consequence

Strict interrupted-batch reuse is enabled. Live-origin reuse remains disabled until Canonical Live
ASR Producer v1 emits exact canonical 60s/5s windows and proof for both tracks. Raw CAF, selected
transcript, quality gates, Evidence Handoff v2 and guarded export are unchanged.
