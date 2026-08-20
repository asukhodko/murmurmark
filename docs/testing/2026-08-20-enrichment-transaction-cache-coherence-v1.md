# Enrichment Transaction and Cache Coherence v1

Updated: 2026-08-20

## Problem

Deferred Echo and review stages could change a transcript profile after downstream synthesis,
speaker selection, readiness and outcome had already been written. The stronger local audio judge
also repeated faster-whisper inference when a rebuilt review pack changed row identity but retained
the same clips. As a result, one session could expose different manual queues in progress, readiness
and outcome.

## Implemented Contract

- Deferred Echo invalidates and rebuilds the dependent cleanup, synthesis, order and review stages.
- `reconcile-session-state.py` refreshes review progress, speaker selection, provisional output,
  readiness and outcome, then verifies profile and queue agreement.
- Closed review decisions are carried only by exact evidence identity or one unambiguous
  interval/text match. Changed or ambiguous evidence remains open.
- Applied decisions remain in `review_decisions_history.jsonl`; repeated reconciliation cannot add
  duplicates.
- Faster-whisper clip decoding is cached by audio SHA-256, complete local model fingerprint and
  decode configuration. Profile and path changes alone do not invalidate a decode.
- Explicit lifecycle resume retries budget-deferred enrichment with a fresh invocation budget.
- Severe `speaker_playback` evidence prioritizes the advanced Echo candidate instead of reserving
  its entire budget for later review evidence.

## Real-Session Regression

Focus session: `sessions/2026-08-20_11-31-56`.

Before reconciliation, readiness reported `147.91s` while the current review progress contained
20 rows / `111.35s`. After reconciliation, progress, readiness and outcome all report 20 rows /
`111.35s`; selected profile remains `reviewed_v1`.

Two full reconciliation runs preserved:

- selected transcript SHA-256
  `cfca86269e489d30724f56564040915bb370c41f8e3060f40395e568a2b71530`;
- current review-decision SHA-256
  `128cf72f55c7642a243e456121881736c771fbb296c44b6d28f45ee5cb7ff0e9`;
- review-history SHA-256
  `492a9f27ab7506809ed21d03e93f9cb961d8811701cd42f0528c1c61bf1d7c04`;
- 47 archived closed decisions, with zero duplicate additions on the repeated run;
- raw mic and remote SHA-256 values recorded before implementation.

A cache-only probe changed the review-pack profile while preserving clip contents. All 160 required
mic/remote clip decodes were content-cache hits, no model inference ran, and the ordinary 80-item
judge output was restored afterwards.

## Replay

```bash
.venv/bin/python scripts/reconcile-session-state.py \
  sessions/2026-08-20_11-31-56 \
  --reason regression_replay

murmurmark report sessions/2026-08-20_11-31-56

.venv/bin/python scripts/check-enrichment-coherence.py
.venv/bin/python scripts/check-stronger-audio-judge.py
```

The result is `PROMOTE`: downstream state now converges deterministically, while any reconciliation
failure remains recoverable through the previous fingerprinted transcript and an explicit command.
