# SepFormer Four-Stem Target-Me Qualification v1

Date: 2026-08-07

Decision: `DO_NOT_ADVANCE_STRONGER_SEPARATOR`

## Scope

This stage tested whether the pinned offline SpeechBrain SepFormer could become a safe four-stem
Target-Me adapter around the exact Speaker-Preserving Neural Echo v2.17 fallback. It materialized a
frozen `12/4` train/dev split with 240 four-second items, split-disjoint non-target speakers and
exact Target-Me, remote echo, other-local and residual truth.

Only the 180 train items were inferred. The locked train gate failed, so the 60 dev items were not
opened by SepFormer or WavLM. Future-hard, sealed meetings, ordinary-meeting labels and direct ASR
remained closed.

## What Worked

On train, the two anonymous SepFormer outputs were usually separable when Target-Me was known to be
present:

- WavLM paired assignment accuracy: `1.0` over 96 paired rows;
- query collapse rate: `0.03125`;
- paired-query Target-Me margin median: `13.761 dB`;
- Target-Me SNR median: `11.124 dB`;
- other-local SNR median: `11.715 dB`;
- reconstruction maximum absolute error: `0.0`;
- maximum candidate peak: `0.881353`, with no non-finite output.

These are train diagnostics, not dev or production claims.

## Blocking Result

The adapter could not tell reliably whether the enrolled Target-Me speaker was present at all. The
locked presence score distributions overlapped:

- positive p05: `-0.186223`;
- negative p95: `0.067174`;
- locked separation gap: `-0.253397`;
- at the minimum threshold `0.0`, false accept rate: `0.125`;
- at the same threshold, false reject rate: `0.643939`.

Target-absent attenuation had a median of only `0.199 dB`. The residual stem also remained weak on
train at `-2.069 dB` median SNR. Advancing to dev would therefore turn many absent-Target-Me rows
into confident speech and would violate the fail-open contract.

## Resource And Reproducibility Result

The complete train separator cache used `1531.011s` of cumulative inference, four compute threads,
`nice=20` and `1864.953 MB` peak RSS. Network attempts were `0`. Cache time survived two deliberate
interruptions and resumed from verified per-item artifacts.

Frozen fingerprints:

- inputs: `a0d0c7b046d37121c08ebfe11c0d063d6a4ac4ce58fd29b61d6889bbf6639589`;
- corpus: `e7edbf3ce1991e7e8c21ed48b009de3d7f8c9c1c654fab7b2367ec1b436c0b0e`;
- train calibration: `dab6030fd1beb5902e2acf7b4dd49f7ecb7a2dbd160be6aa7728eadc8c6e398c`;
- terminal decision: `dad298538686e4accfac0c66fd746631694bb022a0a92ae210bbeb0df8972fbe`;
- verification: `f22650891de319d9de6602fa695150eff8e357060ed3ccd2e109b90ea2a60365`.

## Boundary And Reopening Prerequisite

This closes the current frozen SepFormer plus comparative-WavLM adapter. Reopening it requires an
independent Target-Me presence/absence detector that can abstain on absent and ambiguous speech,
with split-disjoint supervision and a locked validation result before SepFormer dev access. A
future design must also improve explicit residual preservation; stronger source assignment alone is
insufficient.

No threshold may be retuned from the materialized dev set. A new presence model needs a separate
goal, policy and train/dev contract. Until then, Speaker-Preserving Neural Echo v2.17 remains the
exact production plateau and the bounded pre-ASR audio frontier is closed.
