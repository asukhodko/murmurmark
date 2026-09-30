# Speaker Handoff And Overlap Recovery

## Problem

An authoritative transcript could finish while optional audio judging consumed the remaining
lifecycle window. Speaker attribution then timed out, leaving only unknown remote speakers.
An interrupted deferred report also replaced the successful primary outcome with `pipeline_failed`,
while lifecycle could still say `ready_with_review`. Separately, integrity/review overlap writers
and the chronology reader disagreed about utterance-reference keys.

## Changes

- Initial speaker publication precedes optional judges, with a separate 300-second limit.
- Optional actions retain one shared deadline; final state publication is cache-only and capped at
  30 seconds. Strict selection uses verification only; since the September 25 follow-up,
  provisional publication may rebind compatible cached evidence without inference.
- Deferred failure stays visible without replacing a successful authoritative run. A missing or
  failed primary result still fails closed, and lifecycle cannot hide a genuine `pipeline_failed`.
- Current-dialogue overlap construction supports both reference formats and reconstructs missing
  links. Chronology `auto` selection follows the same lineage/promotion checks as readiness.
- The integrity policy now pins the shared overlap helper as well as its entry point.

## Real Replay

Three existing sessions were replayed from the same inputs with cached judge evidence only.
All six existing text repairs were unchanged. Dialogue JSON, simple transcript JSON and Markdown
were byte-identical; overlap lists changed from empty to 33, 36 and 16 entries. The corpus gates
returned `PROMOTE` with 11 candidates, six repairs and five remaining review cases. Raw hashes
were verified by the per-session and corpus checks.

On the latest session, speaker publication completed without primary-ASR repetition. The selected
anonymous profile covers 94.5124% of remote speech; 45.431681 seconds remain unknown. These are
acoustic clusters, not verified human identities. The 16-item / 53.02-second review queue and
3.69-second chronology risk remain explicit. Outcome is `review_first`, lifecycle is
`ready_with_review`, export remains blocked, and unfinished judging remains resumable.

Private before/after artifacts and the replay corpus report are under
`sessions/_reports/session-audit/2026-09-24-hardening/`. The original transcript text is not included
in this public note.

## Deferred Optimization

`scripts/micro_asr_cache.py` is a tested candidate, **not connected to production**. Its single-flight
cache binds prepared PCM, model, executable, decoding options and relevant environment; aliases share
relative decodes and callers must rebind timestamps separately. Tests cover concurrent reuse, changed
inputs, corrupt entries, interrupted publication and timestamp rebinding.

Connecting it changes the frozen transcriber runtime pinned by Neural Echo v2.17 and its production
policy. The existing qualification cannot be reused for that change. Raw tracks remain locally for
only two of the 15 original hard/corpus sessions; 13 are missing. Do not update those policy hashes
just to satisfy tests. The next performance step needs a separately versioned qualification on an
available representative corpus, preserving local recall, echo safety, chronology and exact fallback.
Production transcriber, speaker selector, raw capture and resource configuration remain unchanged.

## Checks

Focused checks cover publication order, zero optional budget, bounded speaker/final-state timeout,
resume, primary/deferred failure separation, verification-only final refresh, legacy overlaps,
empty overlaps with real cross-role speech, malformed dialogue rows, invalid timing/IDs and cache
correctness. Invalid inputs block the audit and its Markdown report does not claim zero risks.

On 2026-09-24, `scripts/check.sh` completed with exit code 0, including Swift build, 13 Swift tests,
SwiftLint, Python compilation, frozen Neural Echo contracts, speaker attribution, review safety,
lifecycle recovery, release packaging contract checks and the final end-to-end fixture. The focused
overlap check and `git diff --check` also passed after the final malformed-input reporting change.

Hardware-dependent committed-PCM capture and CLI acceptance were skipped because ScreenCaptureKit
reported no shareable display. No new hardware capture soak was performed. The real-session resume
used existing recordings and cached evidence; its two publication stages took 4.351 and 4.371 seconds.
Those cached timings are not a cold-attribution or fresh-meeting latency guarantee.
