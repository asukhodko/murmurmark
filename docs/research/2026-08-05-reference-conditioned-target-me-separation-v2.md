# Reference-Conditioned Target-Me Separation v2

Date: 2026-08-05

Status: complete, `DO_NOT_PROMOTE_REFERENCE_CONDITIONED_TARGET_ME_SEPARATION_V2`

Production baseline: Speaker-Preserving Neural Echo v2

## Question

Can a small local separator use a speaker enrollment to choose the requested local voice from one
fixed microphone mixture, while preserving the separately accounted remote echo and exact mixture
reconstruction?

The experiment follows the completed Target-Me Identifiability Corpus v1. Unlike v1, every mixture
has two supervised queries:

- the private Target-Me enrollment must recover `target_me`;
- a split-local non-target enrollment must recover `other_local_speech` from the same mixture bytes.

This paired control makes a constant local/echo prior observable. A model that ignores the query
cannot pass speaker attribution even if its three stems sum exactly to the microphone input.

## Frozen Basis

- Target-Me Identifiability Corpus v1:
  `530cb0fd23503884d438bc24be10fff45610da1fb8fe710aad1b6b6cd992b2ce`;
- `4/2/2` split-disjoint non-target speakers;
- `320/85/85` train/dev/hard items and `640/170/170` paired queries;
- v1 baseline decision:
  `DO_NOT_PROMOTE_REFERENCE_CONDITIONED_TARGET_ME_SEPARATION_V1`;
- byte-exact production fallback: Speaker-Preserving Neural Echo v2;
- no ordinary meeting audio in training and no network model loading.

Before training, v1 was replayed from its frozen checkpoint and cache. The observed medians matched
the prior report exactly:

| Metric | Replayed value |
|---|---:|
| Target-Me SNR | `11.469766 dB` |
| Target-Me improvement | `5.133877 dB` |
| Echo SNR | `7.788047 dB` |

## Candidate

The single candidate was fixed before dev evaluation:

- 16 kHz mono, four-second clips;
- complex STFT target mask;
- FiLM and token conditioning from the 512-dimensional WavLM x-vector enrollment;
- one 96-unit GRU layer;
- paired target loss plus a pair-sum consistency loss;
- `remote_echo` is an explicit accounted stem, not silently absorbed into the local output;
- `other_local = mixture - query_target - remote_echo` preserves exact accounting;
- 12 deterministic epochs, seed `8052026`, CPU/background resource profile.

The candidate checkpoint state fingerprint is
`c9379c935a7ca6d37419e7a06b44d5d1934d5876a0f792480b9c15408ad90f65`.
Repeated train/dev runs produced the same report fingerprint, state fingerprint and checkpoint
SHA-256.

## Measured Result

The model learned to use the speaker query, but did not reach ASR-safe waveform quality:

| Dev gate | Required | Observed | Result |
|---|---:|---:|---|
| Target-Me SNR median | `>=12 dB` | `4.852357 dB` | fail |
| Target-Me improvement median | `>=3 dB` | `5.017654 dB` | pass |
| Other-speaker SNR median | `>=12 dB` | `4.106966 dB` | fail |
| Correct-vs-wrong query margin | `>=3 dB` | `4.990806 dB` | pass |
| Query collapse rate | `<=5%` | `0%` | pass |
| Absent-query attenuation | `>=15 dB` | `8.293271 dB` | fail |
| Remote-only attenuation | `>=15 dB` | `197.645669 dB` | pass |
| Reconstruction max error | `<=1e-5` | `0` | pass |
| Clipped/non-finite outputs | `0/0` | `0/0` | pass |

The query margin and zero collapse rate are important: the new corpus did answer the
identifiability question. The candidate changes its output when the enrollment changes. Its
speaker extraction quality is simply too low, especially for quiet and absent speakers.

The immutable candidate lock therefore records `DEV_CANDIDATE_REJECTED`. Hard audio and the sealed
twelve-session meeting corpus remained unopened. The final decision is
`DO_NOT_PROMOTE_REFERENCE_CONDITIONED_TARGET_ME_SEPARATION_V2`, fingerprint
`5b9fb8ec1cbc84340bcda4245edfd1f2113493c8f73d8e7fc25bb0a572aab26c`.

## What This Establishes

1. The supervision problem from v1 is closed: speaker-query adherence is measurable and learned.
2. A small spectral FiLM+GRU extractor is below the required quality ceiling, despite exact
   reconstruction and useful query discrimination.
3. More epochs on the same dev set would tune the benchmark rather than fix the architecture.
4. Hard and meeting evaluation cannot rescue a candidate that already fails clean supervised dev.
5. Production Speaker-Preserving Neural Echo v2 remains unchanged and continues to fail open to
   `local_fir_role_masked` where its own gates do not apply.

## Future Audio Prerequisite

A future attempt should start from a pretrained target-speaker extraction representation or a
substantially larger multilingual speaker-query corpus. It should not be another small spectral
mask trained from scratch on these five train identities. Candidate options include a locally
pinned SpeakerBeam/VoiceFilter-style checkpoint, a pretrained separation encoder with enrollment
conditioning, or language-matched non-target speech expansion. License, offline availability,
runtime and deterministic inference must be frozen before reopening this track.

This is parallel research, not the immediate product bottleneck. The production echo profile is
already guarded. The critical path now returns to Evidence Notes And Export v2 so a successful
meeting produces one stable, evidence-backed handoff without profile-specific file discovery.

## Outputs

Private artifacts remain below:

```text
sessions/_reports/reference-conditioned-target-me-separation-v2/
  frozen_inputs.json
  preflight_report.json
  v1_replay_report.json
  cache/
  train-dev/
    separator.pt
    candidate_lock.json
    determinism_report.json
    dev_rows.jsonl
    train_dev_report.json
  hard-test/hard_test_report.json
  sealed-corpus/sealed_corpus_report.json
  data_card.json
  model_card.json
  corpus_report.json
  experiment_manifest.json
  decision.json
  decision.md
```

Raw CAF, the published identifiability corpus and production audio were not modified.
