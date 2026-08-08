# ECAPA Remote Speaker Shadow Qualification v1 Runbook

This is a corpus-maintenance command, not part of an ordinary meeting.

## Preflight And Freeze

```bash
murmurmark corpus remote-identity-shadow-v1 preflight
murmurmark corpus remote-identity-shadow-v1 freeze
murmurmark corpus remote-identity-shadow-v1 status
```

`freeze` validates the existing residual reference pack, all bounded clips and exemplars, Coverage
v3 unknown words, production guards and the pinned local model. Run it before `evaluate`. If any
hash changes, stop and investigate instead of replacing the policy hash.

## Evaluate And Replay

```bash
murmurmark corpus remote-identity-shadow-v1 evaluate
murmurmark corpus remote-identity-shadow-v1 replay
murmurmark corpus remote-identity-shadow-v1 finalize

less sessions/_reports/ecapa-remote-speaker-shadow-qualification-v1/\
ecapa_remote_speaker_shadow_qualification_report.md
```

`evaluate` runs one local offline ECAPA batch with `nice=20`. `replay` rescoring uses frozen
embeddings and must be byte-identical. `finalize` writes the tracked portable manifest without
rerunning inference.

For a fresh complete run:

```bash
murmurmark corpus remote-identity-shadow-v1 all
```

## Current Result

The frozen run produced `DO_NOT_PROMOTE_REAL_IDENTITY`:

- 68 accepted items;
- 156/851 recovered words, ratio `0.183314` below the fixed `0.20` gate;
- 211.099681/598.239509 recovered seconds, ratio `0.352868`;
- projected shadow coverage `0.939312 -> 0.960727`;
- structural 1x1 precision `1.0` over 27 accepted words;
- independent machine-reference precision `0.878788` over 33 accepted words;
- 2 silent clips failed open to `unknown`;
- 0 human-reviewed proposal words;
- deterministic replay passed; frozen runtime was `9.278614s`.

Do not lower thresholds or apply these shadow labels. Coverage v3 remains the supported result. The
next useful investigation is an error decomposition of the 68 accepted items and the abstentions,
with special attention to interval purity, enrollment contamination, short/silent audio and the
coarse utterance-level reference.
