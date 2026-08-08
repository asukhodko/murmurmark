# Bounded Remote Speaker Interval Purification v1 Result

Date: 2026-08-09

## Outcome

`DO_NOT_ADVANCE_INTERVAL_PURIFICATION`

The one predeclared `word_span_guard_80ms_v1` candidate was evaluated once on the frozen
real-session ECAPA shadow. No candidate parameters or identity thresholds were tuned after seeing
the result.

## Frozen Scope

- 278 items and 851 residual words;
- 93 interval-axis failure items / `201.273504s`;
- 58 speech-supported boundary or mixed-speech items;
- 35 insufficient-audio items kept fail-open;
- 28 unchanged enrollment exemplars;
- ECAPA revision `0f99f2d0ebe89ac095bcc5903c4dd8f72b367286`;
- similarity/margin thresholds `0.50/0.30`.

## Result

- 50 candidate clips were materialized;
- 35 insufficient-audio, 7 short and 1 overlapping-context item remained `unknown`;
- control: 68 accepted items / 156 words / `211.099681s`;
- candidate: 66 accepted items / 154 words / `209.538339s`;
- newly accepted: 2 items / 2 words / `4.154556s`;
- four control acceptances were removed;
- structural 1x1 precision remained `1.0`;
- coarse independent precision improved from `0.878788` to `0.967742`;
- one new independent-reference error remained.

The candidate failed every material recovery gate and the no-new-reference-error gate. Its useful
precision effect comes mainly from abstaining on acoustically unsupported control proposals, not
from recovering the interval residual.

## Invariants

- all 278 items and 851 words are conserved exactly;
- word IDs, text positions and timestamps are unchanged;
- enrollment, centroids, model and thresholds are unchanged;
- selected transcripts, Coverage v3, raw CAF, Echo Guard and primary ASR are unchanged;
- candidate outputs are shadow-only and private embeddings remain ignored;
- repeated full evaluation and replay are byte-identical.

## Consequence

The fixed word-boundary crop is closed. Retuning its guards on the same evidence is prohibited. The
next measured axis is session-local enrollment instability: 83 failure items / `119.920926s`.
