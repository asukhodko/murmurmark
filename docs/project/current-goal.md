# Current Goal

Status: current

Updated: 2026-08-04

The stable product path remains `murmurmark meeting -> first Ctrl-C -> final result`. Raw CAF and
batch output are authoritative. Speaker-Preserving Neural Echo v2 remains the exact production
baseline; every missing artifact, incompatible acoustic mode or regression returns to its byte-exact
fallback.

Roadmap status and dependencies live in
`docs/roadmap/murmurmark-cli-roadmap.plan.yaml`. `scripts/check-planning-consistency.py` keeps the
README, roadmap and OpsKarta wording aligned.

## Target-Me Identifiability Corpus v1

OpsKarta nearest goal: Target-Me Identifiability Corpus v1: построить локальный воспроизводимый и speaker-disjoint train/dev/hard корпус с независимо известными target_me, remote_echo и non-target other_local speech, correct/wrong enrollment controls и акустическими вариантами основного speaker-mode; завершить READY_FOR_TARGET_CONDITIONED_TRAINING либо точным DO_NOT_TRAIN, не обучая production-модель и не меняя Speaker-Preserving Neural Echo v2.

## Why This Is Next

Reference-Conditioned Target-Me Separation v1 completed with
`DO_NOT_PROMOTE_REFERENCE_CONDITIONED_TARGET_ME_SEPARATION_V1`, fingerprint
`e3c925f005f0e85e7dc22e555e2a25701a1b297372babcc3551339da861324a3`.

The experiment established two different facts:

- the ideal complex-mask representation has a wide ceiling: Target-Me SNR p05 `58.383 dB` and echo
  SNR p05 `48.578 dB`;
- the bounded separator can overfit four examples, but two deterministic train/dev attempts missed
  the locked gates. The best reached `11.470/12 dB` Target-Me SNR and `7.788/8 dB` echo SNR.

The decisive gap is data identifiability. All trainable rows used one fixed Target-Me enrollment.
The only labelled `other_local` targets were keyboard and silence; no row contained independently
known speech from another nearby person. Exact remix therefore could not distinguish correct
Target-Me attribution from a semantic speaker swap. Hard-test and the sealed twelve-session corpus
stayed unopened, and production output remained unchanged.

## Objective

Create the smallest private corpus that can answer whether a separator actually follows a speaker
query. Every accepted example must provide known, independently sourced components:

```text
mic_mixture = target_me + remote_echo + other_local_speech + other_local_noise
```

Each example also carries:

- the correct Target-Me enrollment;
- at least one wrong-speaker enrollment;
- exact source identities and split ownership;
- acoustic rendering provenance, gain, delay and room/speaker-path parameters;
- SHA-256 for every source and rendered artifact;
- license, privacy and redistribution classification.

This goal prepares evidence only. It does not train or promote a separator.

## Data Route

1. Reuse the frozen controlled Target-Me speech only in its existing split.
2. Audit a local, permissively licensed multi-speaker speech source for non-target identities. Private
   meeting speech may be diagnostic only and must not become redistributable training material.
3. Render non-target speech through a path distinct from the exact remote echo path, preserving its
   clean source as independent ground truth.
4. Build target-only, remote-only, other-speaker-only, target+remote, target+other and full
   target+remote+other mixtures inside one split.
5. Generate correct-enrollment, wrong-enrollment and enrollment-swap pairs without crossing speaker
   or source ownership between train, dev and hard.
6. Replay the corpus from manifests and compare every artifact hash.

## Acceptance Gates

- at least `4` non-target speaker identities in train, `2` in dev and `2` in hard;
- no speaker identity, source clip or acoustic rendering seed crosses splits;
- at least `20 min` train, `5 min` dev and `5 min` hard full three-source mixtures;
- every split includes quiet Target-Me, quiet non-target speech, double-talk, keyboard/background and
  opening/backchannel examples;
- correct and wrong enrollment vectors exist for every speaker-bearing example;
- an enrollment-swap oracle changes speaker attribution while mixture conservation stays exact;
- target, echo and other-speech source SNR oracle gates pass before any trainable candidate;
- replay matches every tracked descriptor and raw source remains unchanged;
- private sources, model files and generated audio stay ignored; tracked reports contain no meeting
  text or absolute workstation paths;
- missing licenses, weak identity ownership, stale hashes or cross-split contamination fail closed.

## Decision

The goal ends in exactly one fingerprinted result:

- `READY_FOR_TARGET_CONDITIONED_TRAINING`; or
- `DO_NOT_TRAIN_TARGET_ME_IDENTIFIABILITY_V1` with a precise data, license or supervision ceiling.

`READY` authorizes a later separator experiment only. It does not change `mic_for_asr.wav`, transcript
selection, export or retention.

## Definition Of Done

- versioned corpus, item, enrollment, replay and decision schemas exist;
- manifests, data card, license/privacy audit and deterministic builder are reproducible locally;
- fixture tests cover semantic speaker swap, split contamination, stale source, missing enrollment,
  silence, clipping and interrupted publication;
- corpus replay and oracle reports produce one immutable decision;
- Speaker-Preserving Neural Echo v2 policy and all raw CAF hashes remain unchanged;
- README, architecture, contracts, runbook, current goal, roadmap and OpsKarta record the measured
  result and next dependency;
- full regression, planning, privacy and open-source checks pass;
- changes are committed, pushed to `origin/main`, and the worktree is clean.

## Outside This Goal

- training or promoting a separator;
- changing capture, Echo Guard, whisper.cpp or transcript post-processing;
- cloud inference or uploading private meeting audio;
- remote diarization, Live promotion, LLM synthesis and UI.

## Deferred Product Goal

Evidence Notes And Export v2 remains the next product handoff goal. It can proceed after this bounded
data prerequisite or in parallel if audio research is paused; it must continue to use the selected
production transcript profile.
