# Multi-Component Residual Separator Contract

`multi_component_residual_separator_v1` is an isolated research profile above the immutable
Speaker-Preserving Neural Echo v2 baseline. It never writes production `mic_for_asr`, raw CAF,
authoritative remote, transcript, notes or export artifacts.

## Inputs And Stems

The 16 kHz mono contract accepts production-v2 mic audio, a frozen Alignment/Echo-Path estimate and
a local Target-Me enrollment. It emits:

- `target_me`: speech matching the supplied query;
- `remote_echo`: the separately accounted remote estimate;
- `other_local`: nearby non-target speech and explained local material;
- `unexplained_residual`: noise and material not safely assigned elsewhere.

`other_local` is the exact remainder, so the four stems reconstruct the input within `1e-5`. Mixture
consistency does not authorize assigning residual remote to Target-Me.

## Evidence Isolation

Train, dev and hard non-target speakers are disjoint. Target-Me identity is intentionally shared
across splits because this is a personalized separator. Only train changes model weights; only dev
selects a candidate. Hard and sealed audio cannot be opened until the immutable dev lock passes.
ASR text and speaker state are evaluation evidence, never source ground truth.

The policy and all prerequisite SHA-256 values live in
`policies/multi-component-residual-separator-v1.json`. Missing or changed inputs fail closed for the
experiment and fail open to production v2 for the product.

## Promotion Boundary

Promotion requires waveform, identity, absence, reconstruction, runtime and direct whisper.cpp
gates. Confirmed Target-Me words, openings, chronology, double-talk and nearby other-local speech
must not regress. Post-ASR deletion and role cleanup receive zero promotion credit.

The v1 decision is `READY_FOR_STRONGER_LOCAL_SEPARATOR`. Dev failed before hard access, so the
profile has no apply command and cannot be selected by the normal meeting pipeline.
