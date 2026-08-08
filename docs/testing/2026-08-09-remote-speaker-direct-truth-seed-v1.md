# Remote Speaker Direct Truth Seed v1 Result

Date: 2026-08-09
Decision: `REFERENCE_INSUFFICIENT`

## Frozen Result

- Source pack: 278 items / 851 words.
- Seed: 33 unique items / 116 words / 90.100820 seconds across 6 sessions.
- Changed cases: 11 newly accepted and 5 removed control acceptances, all included.
- Controls: 6 stable accepted, 6 stable abstentions, 3 protected-overlap candidates and 2
  embedding-unavailable candidates.
- Hidden repeat subset: 8 items.
- Blind review slots: 41.
- Direct answers: 0 primary / 0 repeat.
- Replay: byte-exact.

All source fingerprints and 355 inherited production guards pass. Review records expose no model
suggestion, stratum, speech text, human name or cross-session identity.

## Interpretation

The engineering part is complete: the exact cases needed to distinguish enrollment progress from
regression are small, frozen and locally reviewable. The evidence itself is still absent. Another
identity backend, centroid rule or threshold change would therefore repeat the same proxy-evaluation
problem.

Production remains Coverage v3. No raw audio, selected transcript, primary ASR, Echo Guard, ECAPA
shadow, interval result or enrollment result changed.

The next bounded milestone is **Remote Speaker Blind Review Completion v1**: fill only the 41 frozen
slots, verify repeat consistency and publish `DIRECT_TRUTH_SEED_READY` or the exact remaining gap.
