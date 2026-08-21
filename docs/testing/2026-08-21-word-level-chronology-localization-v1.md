# Word-Level Chronology Localization v1 Result

Date: 2026-08-21.

## Result

- decision: `PROMOTE_WORD_LEVEL_CHRONOLOGY_LOCALIZATION_V1`;
- frozen residual: 14 rows / `89.97s`;
- closed: 9 rows / `52.83s` (`64.2857%` rows, `58.7196%` seconds);
- remaining: 5 rows / `37.14s`;
- outcomes: six sequential boundaries, two real double-talk rows, one remote-only transfer and five
  insufficient word alignments;
- end-to-end chronology closure: `308.8/345.94s`;
- public replay: byte exact;
- stale clip, model and decode provenance: fail closed to `EVIDENCE_INCOMPLETE`;
- transcript text, roles, published timestamps, selected profile and raw audio: unchanged.

## Important Observation

One previous 14-second segment overlap localized into a mic-clean span ending at `14.86s` and a
remote span starting at `16.72s`. The measured gap proves that segment-level overlap alone was not
evidence of damaged chronology.

The remaining five rows were not forced closed: their remote words could not be localized reliably.
That `37.14s` is the reproducible evidence bound passed to Terminal Gate.
