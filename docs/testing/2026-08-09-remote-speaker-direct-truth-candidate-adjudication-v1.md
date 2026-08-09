# Remote Speaker Direct-Truth Candidate Adjudication v1 Result

Date: 2026-08-09
Decision: `KEEP_COVERAGE_V3`

## Frozen Result

- Inputs: 278 source items / 851 words, 33 primary truth items and 8 hidden repeats.
- Integrity: 65/65 review-pack files and 355/355 inherited production guards verified.
- Direct identity truth: 8 primary items; repeat consistency 7/8 (`0.875`).
- Control: 3 correct identities, 5 positive abstentions, 8 fail-closed unsafe accepts.
- Candidate: 4 correct identities, 4 positive abstentions, 13 fail-closed unsafe accepts.
- Changed evidence: 3 correct identity gains, 2 lost correct controls, net gain 1 (`0.125`).
- New false identity against positive truth: 0.
- Replay: byte-exact.

## Interpretation

The weighted-centroid candidate improved direct identity coverage in three rows, but most of its
11 apparent additions were unsupported by blind truth: eight were `mixed`, `unknown_speaker` or
`unusable`. It also removed two control decisions that direct truth confirmed. The candidate failed
the material gain, control preservation and fail-closed abstention gates.

Production remains Coverage v3. No transcript, raw audio, primary ASR, Echo Guard, threshold or
speaker label changed.

The next bounded route is **Remote Speaker Enrollment Purity and Abstention Hardening v2**: keep
Coverage v3 decisions monotonic, reject impure enrollment and mixed/weak additions, and use this
truth only for development. A disjoint held-out truth set remains mandatory before promotion.
