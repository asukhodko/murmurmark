# Session-Local Remote Speaker Enrollment Hardening v1 Result

Date: 2026-08-09

Decision: `DO_NOT_ADVANCE_ENROLLMENT_HARDENING`.

## Result

The one-shot `contrastive_reliability_weighted_centroid_v1` changed 10 of 14 session-local
centroids. Against the frozen 278-item ECAPA control it:

- added 11 accepted items, 59 words and `44.694004s`;
- recovered 4 of 83 enrollment-scope items and `11.411687s`;
- removed 5 existing accepted items and 11 words;
- preserved structural precision at `1.0`;
- changed independent precision from `0.878788` to `0.894737`;
- introduced no new measured reference-error word and changed no accepted speaker identity.

The result missed the predeclared 5% scope-item gain by one item and failed the zero removed-control
acceptance gate. Material gross recovery therefore cannot be promoted.

## Interpretation

Enrollment quality matters, but two exemplars per profile and coarse machine reference do not
support a safe weighting rule. Retuning this candidate on the same evidence is closed. A small
direct real-session group-speaker truth seed is required before evaluating another identity backend
or deciding whether any of the 11 gains are genuine.

## Safety

All 278 items, 851 words, word IDs and timestamps are conserved. Item embeddings, thresholds,
Coverage v3, selected transcripts, raw CAF, primary ASR and Echo Guard are unchanged. Replay is
byte-identical and public artifacts contain no speech, names, absolute paths or embeddings.
