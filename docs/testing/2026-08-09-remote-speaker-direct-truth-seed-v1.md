# Remote Speaker Direct Truth Seed v1 Result

Date: 2026-08-09
Decision: `DIRECT_TRUTH_SEED_READY`

## Frozen Result

- Source pack: 278 items / 851 words.
- Seed: 33 unique items / 116 words / 90.100820 seconds across 6 sessions.
- Changed cases: 11 newly accepted and 5 removed control acceptances, all included.
- Controls: 6 stable accepted, 6 stable abstentions, 3 protected-overlap candidates and 2
  embedding-unavailable candidates.
- Hidden repeat subset: 8 items.
- Blind review slots: 41.
- Direct answers: 33 primary / 8 repeat.
- Primary outcomes: 8 anonymous speaker, 11 `unknown_speaker`, 4 `mixed`, 10 `unusable`.
- Hidden-repeat consistency: 7/8 (`0.875`).
- Replay: byte-exact.

All source fingerprints and 355 inherited production guards pass. Review records expose no model
suggestion, stratum, speech text, human name or cross-session identity.

## Interpretation

The blind queue is complete and reproducible. It provides the first bounded direct real-session
speaker evidence for the exact cases that distinguish enrollment gains from regressions. Evidence
margin is limited: repeat consistency equals the minimum gate, and mixed or silent exemplars led to
fail-closed `unknown_speaker`, `mixed` or `unusable` outcomes rather than forced identity.

Production remains Coverage v3. No raw audio, selected transcript, primary ASR, Echo Guard, ECAPA
shadow, interval result or enrollment result changed.

The next bounded milestone is **Remote Speaker Direct-Truth Candidate Adjudication v1**: compare the
unchanged Coverage v3 control and frozen enrollment candidate once against these direct anonymous
outcomes. It may qualify a later experiment, but cannot promote production or tune thresholds.
