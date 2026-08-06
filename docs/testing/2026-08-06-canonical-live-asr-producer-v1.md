# Canonical Live ASR Producer v1

Date: 2026-08-06

Decision: `DO_NOT_PROMOTE`

## Result

The capture-safe producer can reconstruct canonical remote ASR audio from closed committed-PCM
segments, decode exact whisper.cpp `60s/5s` windows and publish
`murmurmark.authoritative_live_asr_chunk/v1`. The strict post-stop consumer accepted the remote
track on all three frozen sessions, and a clean batch recompute produced byte-identical raw ASR.

The hypothesis does not pass the product runtime gate. Batch already decodes mic and remote in
parallel. Removing only remote decode reduced modeled post-stop wall time by `2.8651%`, `4.1040%`
and `4.0818%`, although it removed about `51%` of total ASR CPU work. The corpus also contains
historical replay rather than fresh recording-time proofs.

| Session | Remote parity | Origin | Mic decode | Remote decode | Modeled wall reduction |
|---|---:|---|---:|---:|---:|
| `2026-07-08_16-22-42` | yes | `historical_replay` | `56.702s` | `58.374s` | `2.8651%` |
| `2026-07-08_17-10-35` | yes | `historical_replay` | `24.633s` | `25.687s` | `4.1040%` |
| `2026-07-21_15-11-15-live` | yes | `historical_replay` | `19.901s` | `20.748s` | `4.0818%` |

## Product Decision

- Ordinary `murmurmark meeting` and `--experiment live-shadow-v1` do not start this extra ASR.
- Evidence collection requires explicit `--canonical-live-asr-evidence`.
- Recording-time proofs are not materialized unless the frozen corpus decision becomes `PROMOTE`;
  lab verification requires an explicit override.
- Raw CAF and normal batch remain authoritative. Missing, stale, corrupt or late evidence falls
  back to batch without changing the transcript.

The next latency hypothesis must remove the mic critical path. The authoritative mic source is
selected after Echo Guard and Speaker-Preserving Neural Echo policy, so raw committed mic PCM cannot
be declared canonical during capture. Causal Canonical Mic ASR v1 subsequently tested delayed,
checkpointable mic preparation and closed with `DO_NOT_PROMOTE`: the current exact boundary is
session end.

## Reproduction

```bash
scripts/report-canonical-live-asr-corpus.py \
  sessions/2026-07-08_16-22-42 \
  sessions/2026-07-08_17-10-35 \
  sessions/2026-07-21_15-11-15-live \
  --benchmark-mic

jq '{decision,summary}' \
  sessions/_reports/authoritative-incremental-asr-v1/canonical-live-asr-producer-v1/canonical_live_asr_corpus_report.json
```

Frozen raw and producer fingerprints are in
`docs/testing/canonical-live-asr-producer-v1-manifest.json`.
