# Speaker-Preserving Neural Echo v2

Date: 2026-08-04

Decision: **PROMOTE_SPEAKER_PRESERVING_NEURAL_ECHO_V2**

## Result

MurmurMark now has a guarded personalized pre-ASR Echo profile for speaker playback. It uses the
authoritative remote track, speaker state, controlled Target-Me enrollment, local WavLM and
Resemblyzer evidence, and direct whisper.cpp comparison to suppress only independently supported
remote leakage. The selected clean mic is transcribed directly; transcript cleanup receives zero
promotion credit.

The winning implementation is a hybrid selector, not a single end-to-end denoising network. Small
GRU residual-mask, complex-spectral and echo-mapper candidates, plus the pinned Microsoft DEC
baseline, removed echo but failed local-word, chronology, double-talk or runtime gates. The promoted
v2.15 selector instead applies bounded attenuation only to remote-supported windows and rolls each
unsafe window back to the exact `local_fir_role_masked` baseline.

## Frozen Evidence

| Check | Result |
| --- | ---: |
| controlled source replay | `1465/1465` files |
| immutable hard-test decision | `HARD_TEST_PASSED_V2_16` |
| corpus decision | `PROMOTE_SPEAKER_PRESERVING_NEURAL_ECHO_V2` |
| corpus sessions | `12` |
| candidate sessions | `5` |
| exact fallback sessions | `7` |
| remote-supported reduction | `41.940s` |
| remote-supported tokens removed | `90` |
| candidate local-token retention | `1.0` |
| maximum selector runtime factor | `0.242927` |
| post-ASR cleanup promotion credit | `0` |

The hard set is primarily a safety test. Both speaker-playback rows selected
`safety_exact_fallback`, and the no-speech row selected exact fallback. It therefore proves that the
locked selector does not force a candidate through an unsafe or inapplicable session; it does not
claim hard-set utility. Utility is established separately by the sealed twelve-session corpus.

Fingerprints:

```text
controlled corpus:
  be7b68f3267a20bfbd2fcf186587107e4201517e4edb3abbda3287857b008ffd
hard set:
  1d7e8a2d30142a089096f396f8559111e08bf19813955e5dd9457db88cf1db46
promotion corpus:
  a70e9bd3a0d1834a8c016a80c75d55f550da022b0fcf60ae32ca29cefe55b9de
hard report:
  bb282d3c29b141bdea4c3bb34c02f478142b74a787872a8e8f95e13acd6618cd
corpus report:
  819a8ce2e00df8f59f83fe99e0de30cad367e886b253930e6b6555a42031a7e3
```

## Production Contract

The normal batch order is:

```text
fresh local_fir baseline
  -> snapshot exact fallback
  -> baseline whisper.cpp evidence
  -> personalized bounded selector
  -> direct candidate whisper.cpp shadow
  -> whole-session and per-window safety gates
  -> transactional candidate publication or exact fallback
  -> ordinary transcript audits and synthesis
```

`mic_for_asr.wav`, `mic_role_masked_for_asr.wav` and `derived/asr/mic.wav` all receive the same
selected candidate bytes only after every session gate passes. The matching candidate transcript
artifacts are published in the same recoverable transaction. An interrupted publication is rolled
back on the next run.

Repeated processing first restores the exact FIR baseline before primary ASR. A fresh Echo Guard
run invalidates stale baseline snapshots and creates new ones from the current derived audio. This
prevents a previous candidate from being mistaken for the comparison baseline.

The profile activates only when all of these are available and fingerprint-compatible:

- local WavLM speaker model and runtime;
- private Controlled Echo Supervision enrollment;
- immutable hard and corpus promotion evidence;
- tracked v2.15 selector, audio, shadow and v2.16 evaluation policies;
- speaker-playback acoustic classification.

Headphones, low leakage, missing private evidence, stale hashes, unsupported runtime, no useful
remote residue or any safety regression select the exact `local_fir_role_masked` fallback. A clean
open-source checkout therefore remains useful without the private personalized artifacts.

## Verification

```bash
.venv/bin/python scripts/apply-speaker-preserving-neural-echo-v2.py \
  sessions/<session-id> --verify-only

.venv/bin/python scripts/evaluate-speaker-preserving-neural-echo-v2-16.py verify-hard
.venv/bin/python scripts/evaluate-speaker-preserving-neural-echo-v2-16.py verify-corpus
.venv/bin/python scripts/check-speaker-preserving-neural-echo-v2.py
```

Session output:

```text
derived/preprocess/speaker-preserving-neural-echo-v2/
  baseline_prepare_report.json
  production_selection_report.json
  publication_transaction.json
  baseline-local-fir-role-masked/
```

The detailed candidate evidence remains under versioned
`derived/preprocess/speaker-preserving-neural-echo-v2-15/` and ignored private corpus reports stay
under `sessions/_reports/`.

## Limits

- This is personalized evidence for the enrolled local user, not a universal echo model.
- Candidate sessions require additional bounded and full-shadow ASR, so first-run processing can be
  slower than exact fallback.
- The selector removes independently supported remote leakage; it does not promise waveform-perfect
  dereverberation.
- Post-ASR role and timeline repair remain final safety guards, but no longer carry the main burden
  on sessions where the pre-ASR candidate passes.
- A new machine or speaker needs its own controlled enrollment and guarded promotion evidence.

This result originally handed the critical path to Evidence Notes And Export v2. The later
source-separation review supplied a materially different bounded hypothesis: conserve mic as
Target-Me, remote-echo and other-local stems while conditioning on the exact remote reference.
Reference-Conditioned Target-Me Separation v1 later completed with `DO_NOT_PROMOTE`: its train/dev
data could not identify non-target local speech and hard-test stayed unopened. Target-Me
Identifiability Corpus v1 is the resulting bounded data prerequisite. This v2 result remains the
immutable production baseline and fallback throughout that work.
