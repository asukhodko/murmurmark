# Temporal End-to-End Remote Diarization Qualification v1 Result

Date: 2026-08-10

## Decision

`KEEP_EXPLICIT_UNKNOWN`

The pinned Community-1-equivalent temporal backend was stable under a fixed 500 ms input shift, but
failed speaker-count, boundary, mapping and direct-truth safety gates. It is not a production
candidate.

## Frozen Evidence

- sessions: 6;
- expected session-local profiles: 14;
- blind remote windows: 347;
- direct-truth items: 33;
- candidate pack SHA-256:
  `dc958d9c150ece150781dcd0eb421821740ad42bd91dd8793961538be51e0268`;
- model SHA-256: `f23f04aa9d0f6b8b0a28de016d226dcbe92d7461a6e58045401acfbed623838a`;
- all Coverage labels and direct truth were read only after freeze.

## Temporal Evidence

- minimum multi-speaker stability ARI: `0.814301`;
- minimum activity Jaccard: `0.972946`;
- stable inferred cluster count: `6/6` canonical/shifted pairs;
- exact expected speaker count: `0/6` sessions;
- inferred clusters per session: `2, 2, 8, 6, 5, 4` for expected `1, 1, 5, 2, 3, 2`.

The temporal model is reproducible, but systematically fragments identities.

## Boundaries And Truth

- minimum remote-interval duration recall: `0.598626`;
- minimum remote-interval center recall: `0.701613`;
- maximum median boundary distance: `0.417s`;
- ambiguous mapped clusters: 3;
- minimum cluster purity: `0.5`;
- confirmed gains preserved: `2/3`;
- unsafe accepts: 9;
- new false identities: 7;
- correct controls lost: 1.

Good timing stability does not compensate for missed speech and unsafe identity mapping.

## Consequence

Coverage v3, selected transcripts, raw CAF, primary ASR and Echo Guard remain unchanged. ECAPA,
WavLM, WeSpeaker fixed-window and the tested temporal AHC/VBx route are now bounded on the same
six-session evidence. Another threshold pass over these models is not justified; the next work must
first establish a materially new source of identity evidence and a disjoint qualification route.
