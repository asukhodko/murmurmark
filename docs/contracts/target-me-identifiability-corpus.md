# Target-Me Identifiability Corpus Contract

Status: active v1 contract

## Purpose

Target-Me Identifiability Corpus v1 is the smallest local evidence set that can prove whether a
future separator follows a speaker enrollment instead of memorizing one voice or treating every
near-microphone speaker as `Me`.

The corpus is evidence only. It does not train a model and cannot change the production
`mic_for_asr.wav`, transcript selection or Speaker-Preserving Neural Echo v2.

## Source Model

Every rendered base item has independently known additive stems:

```text
mic_mixture = target_me + remote_echo + other_local_speech + other_local_noise
```

- `target_me` comes from the already frozen controlled Target-Me recordings for the same split;
- `remote_echo` comes from the measured controlled speaker-playback echo for the same split;
- `other_local_speech` comes from a permissively licensed, independently recorded speaker;
- `other_local_noise` comes from the frozen keyboard/background controls or exact silence.

The first public source is Mini LibriSpeech SLR31, licensed CC BY 4.0. Source archives, extracted
files and rendered audio remain under ignored `sessions/_reports/`; tracked files contain only the
source URL, official checksum, contract and aggregate results.

## Split Ownership

The private Target-Me identity is intentionally constant across `train`, `dev` and `hard`: that is
the production query being tested. Its source sessions, utterance clips and enrollment clips must
remain split-local and may not cross a boundary.

Every non-target speaker identity is owned by exactly one split. A non-target speaker, source
utterance, segment, enrollment utterance or acoustic rendering seed may not cross splits. Enrollment
utterances may not be reused as mixture material.

Minimum independent non-target identities and rendered full-mixture duration:

| split | speakers | full mixtures |
|---|---:|---:|
| train | 4 | 20 minutes |
| dev | 2 | 5 minutes |
| hard | 2 | 5 minutes |

## Query Controls

Every speaker-bearing mixture has at least two query rows over the same mixture bytes:

1. correct Target-Me enrollment, where the expected target is `target_me`;
2. enrollment swap to the known other-local speaker, where the expected target is
   `other_local_speech`.

Each row records the correct and wrong enrollment artifacts. The swap must change the expected
speaker stem while preserving the exact mixture and additive reconstruction. This paired contract
is the semantic evidence missing from Reference-Conditioned Target-Me Separation v1.

## Acoustic Rendering

`other_local_speech` is rendered through deterministic near-microphone paths that are distinct from
the measured speaker-to-microphone echo path. The item descriptor records the source offset, gain,
finite impulse response, rendering seed and common anti-clipping scale.

The corpus must cover ordinary full double-talk, quiet Target-Me, quiet other-local speech,
keyboard/background noise and opening/backchannel material in every split. Target-only,
remote-only, other-speaker-only, target+remote and target+other controls remain explicit items.

## Outputs

Private outputs live at:

```text
sessions/_reports/target-me-identifiability-corpus-v1/
  sources/
  current.json
  published/<decision-fingerprint>/
    source_manifest.json
    split_manifest.json
    speaker_manifest.json
    item_manifest.jsonl
    query_manifest.jsonl
    enrollment_manifest.jsonl
    data_card.json
    privacy_licensing_manifest.json
    oracle_report.json
    replay_report.json
    corpus_decision.json
    corpus_decision.md
    audio/
    enrollments/
```

Versioned row schemas:

```text
murmurmark.target_me_identifiability_source/v1
murmurmark.target_me_identifiability_speaker/v1
murmurmark.target_me_identifiability_item/v1
murmurmark.target_me_identifiability_query/v1
murmurmark.target_me_identifiability_enrollment/v1
murmurmark.target_me_identifiability_oracle/v1
murmurmark.target_me_identifiability_replay/v1
murmurmark.target_me_identifiability_decision/v1
```

Every referenced artifact carries a relative path, byte count and SHA-256. Publication is
transactional: an interrupted build must leave the previous complete corpus untouched.
Float32 WAV files must not contain wall-clock metadata such as libsndfile's `PEAK` timestamp. Two
full builds over unchanged inputs must produce the same decision fingerprint.

## Oracles And Fail-Closed Rules

The decision fails closed on any of these conditions:

- missing or changed source, model, license or enrollment artifact;
- non-target identity, source utterance, enrollment utterance or rendering-seed contamination
  across splits;
- enrollment audio reused as mixture audio;
- missing correct/wrong enrollment control;
- an enrollment swap that does not change the expected speaker attribution;
- additive reconstruction error above `1e-5` or source replay SNR below `80 dB`;
- clipping, non-finite samples or mismatched sample rate/duration;
- a speaker-count or full-mixture-duration gate below the locked minimum;
- tracked private audio, speech text or absolute workstation paths.

## Decision

The immutable result is exactly one of:

```text
READY_FOR_TARGET_CONDITIONED_TRAINING
DO_NOT_TRAIN_TARGET_ME_IDENTIFIABILITY_V1
```

`READY` authorizes only a later, separately gated separator experiment. Production remains
byte-exact Speaker-Preserving Neural Echo v2 until that later experiment passes its own dev, hard
and sealed-corpus gates.
