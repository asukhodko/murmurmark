# Selected Read View And Compute Observations

Updated: 2026-10-06. Operational follow-up to Transcript Evidence Repair.
Four-session replay and the full automated suite passed. Hardware-dependent checks were skipped
because no shareable display was available. Private baselines/results are under
`sessions/_reports/selected-read-view-2026-10-06/`.
Installed-release self-test and final focused rechecks passed, including system Python 3.9
compatibility. Details: [verification report](../testing/2026-10-06-selected-read-view-repair.md).

## Scope

1. Publish all current review questions next to the selected transcript, including strict speaker
   selections. Keep stable question IDs, independent decisions and unknown queue completeness.
2. Use child times only when they are valid and text-conserving. Otherwise show the approximate
   parent range and intersecting utterance IDs; do not silently shift words or infer double-talk.
3. Bind the reading projection to independently verified source selection, current queue/gates and
   renderer identities. Atomically publish both files before the pointer. Stale/missing projections
   return the verified source with a warning, never trigger inference during read commands.
4. Observe primary/current-micro/shadow-micro activity outside the frozen producer. Keep resource
   policy unchanged and distinguish approximate activity windows from exact model execution costs.
5. Replay four available private sessions and synthetic interruption, stale, queue-closure,
   invalid-time and compatibility controls. Preserve raw, source text/roles/times/labels and gates.
6. Synchronize documentation/planning and finish the operational repair with tested commit/push.

The verified replays retain 107/156/229/80 open questions. The last case includes twelve unresolved
questions from an older profile: they remain in the appendix with their original scope, not assigned
to current utterances with coincident IDs. `needs_review` answers remain unresolved; these totals
must not be confused with unanswered-row counts. No automatic answer or profile rebasing was applied.

## Architecture

```text
verified strict/provisional/fallback source + current review snapshot + current outcome
    -> immutable reading JSON + Markdown
    -> verified default transcript path

frozen ASR producer -> run-scoped artifact observations -> pipeline report / heartbeat
```

No voice names, words or source times are inferred by this layer. Source rich evidence and old
immutable publications remain available. `--aggregate` and explicit-profile reads remain unchanged.

## Qualification Boundary

The cache, word-bounded replacement and cluster-gate changes remain **DO_NOT_PROMOTE**.
Eleven of twelve old Echo qualification raw pairs are unavailable. Four publication replays and
short exact-PCM canaries cannot replace no-speech, headphones, double-talk, local-content and
exact-fallback controls or a full producer cold/warm replay. Existing candidate-cache work remains
available, not silently dropped. Policy SHA values must not be updated merely to enable it.

Short Me hypotheses with wider recognition context remain explicit review questions. Mic raw,
cleaned mic and remote must be checked against independent word ownership before selecting or
discarding words. Similarity, density and agreement between mic variants are insufficient. This
repair does not claim corrected lexical content or speaker accuracy, nor a measured speedup.

Next: qualify the replacement producer corpus, add exact per-call telemetry and enable session-local
exact-PCM reuse only after conservation gates. Word ownership and voice purity have separate truth
requirements. The broader Bounded Evidence Compute goal stays current.
