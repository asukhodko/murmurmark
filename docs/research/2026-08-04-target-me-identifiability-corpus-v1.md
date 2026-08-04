# Target-Me Identifiability Corpus v1

Date: 2026-08-04

Decision: **READY_FOR_TARGET_CONDITIONED_TRAINING**

Fingerprint:
`530cb0fd23503884d438bc24be10fff45610da1fb8fe710aad1b6b6cd992b2ce`

## Result

The missing speaker-attribution supervision from Reference-Conditioned Target-Me Separation v1 is
now available as a local, private and reproducible corpus.

| evidence | result |
|---|---:|
| train/dev/hard non-target speakers | `4 / 2 / 2` |
| train/dev/hard full mixtures | `1200 / 300 / 300s` |
| rendered base items | `490` |
| paired query controls | `980` |
| split-local enrollment controls | `11` |
| enrollment similarity margin, median | `0.433614221` |
| non-target identity overlap | `0` |
| source-file overlap | `0` |
| enrollment/mixture source overlap | `0` |
| rendering-seed overlap | `0` |
| failed enrollment swaps | `0` |
| clipped or non-finite items | `0` |
| additive reconstruction error | `0.0` |
| replay | `2470/2470` audio files |
| published files verified | `2504/2504` |

Every full item contains independently known `target_me`, measured `remote_echo`, licensed
`other_local_speech` and measured keyboard/background stems. Correct Target-Me and swapped
other-speaker queries share exactly the same mixture bytes but have different enrollment vectors and
different expected target stems. A future model can no longer pass by ignoring the speaker query.

Two independent full `build --refresh` runs produced the same publication fingerprint and verified
all `2504` files. Corpus WAV files use a deterministic float32 writer without libsndfile's volatile
`PEAK` timestamp; the fingerprint therefore identifies audio and manifests rather than build time.

## Sources And Ownership

- Target-Me, remote echo and local noise reuse the frozen Controlled Echo Supervision Lab split
  without moving a source clip or session across boundaries.
- Non-target speech uses Mini LibriSpeech SLR31, CC BY 4.0. The selected speaker identities are
  disjoint across train, dev and hard.
- The fixed private Target-Me identity is intentionally present in all splits, while its recordings
  and enrollment audio remain split-local.
- Enrollment utterances never appear in mixture streams.
- Source archives, extracted speech, private voice, model vectors and rendered audio remain under
  ignored `sessions/_reports/`; tracked files contain no speech text or workstation path.

## Known Limit

The non-target source is English read speech while the controlled Target-Me phrases are Russian.
This is sufficient to establish speaker-query identifiability, because the paired swap changes only
the requested voice over the same mixture. It does not by itself prove robustness to spontaneous
Russian office speech. That robustness belongs to the later separator's hard and real-session gates,
not to this corpus-readiness decision.

The Target-Me source has less unique duration than the rendered train duration and is reused only
inside its original split under different other speakers, echo rows and acoustic renderings. The
data card exposes this fact; a later model must still pass unseen dev, hard and sealed meetings.

## Production Boundary

No separator was trained. `mic_for_asr.wav`, transcript selection, capture, Echo Guard and raw CAF
were unchanged. Speaker-Preserving Neural Echo v2 remains production.

`READY_FOR_TARGET_CONDITIONED_TRAINING` authorizes the separate
**Reference-Conditioned Target-Me Separation v2** experiment. That experiment must lock train/dev
gates before opening hard data and must finish with its own `PROMOTE` or `DO_NOT_PROMOTE` decision.

## Reproduce

See the [runbook](../runbooks/target-me-identifiability-corpus.md) and
[contract](../contracts/target-me-identifiability-corpus.md). The private immutable publication is
selected through:

```text
sessions/_reports/target-me-identifiability-corpus-v1/current.json
```

The ordinary meeting pipeline does not read this corpus.
