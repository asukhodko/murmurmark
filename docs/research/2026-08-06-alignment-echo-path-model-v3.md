# Alignment and Echo-Path Model v3 Qualification

Date: 2026-08-06

Decision: `READY_FOR_MULTI_COMPONENT_SEPARATOR`

Production impact: none. Speaker-Preserving Neural Echo v2 remains the selected pre-ASR profile;
hard and sealed inputs were hash-frozen but never evaluated or used for tuning.

## Question

Can the largest class from Pre-ASR Residual Echo Ceiling Map v1 be closed by a better physical echo
model alone: sub-window delay and drift, several bounded echo paths, nonlinear remote bases and
guarded suppression restricted to remote-only intervals?

## Frozen Protocol

The policy is `policies/alignment-echo-path-model-v3.json`. It freezes:

- the promoted production v2 decision and report;
- residual-map inputs, events, fingerprint and capability ordering;
- controlled supervision manifests and split;
- seven development sessions, five development controls, three hard sessions and three sealed
  sessions;
- direct-ASR, protected-local, chronology, opening, double-talk, runtime and exact-fallback gates;
- one bounded revision after development and no tuning from hard or sealed evidence.

The final candidate lock is
`sessions/_reports/alignment-echo-path-model-v3/candidate_lock.json`, fingerprint
`da769894fc1a5a229bdb9fcd97dd1e3ea37daf762fa80a20e4f70e809ee289f4`.
Every controlled, session, corpus and decision report repeats this fingerprint, so a report from an
older runtime cannot be reused after the candidate lock changes. Qualification commands also take an
exclusive process lock and capture the fingerprint before evaluation; concurrent runs cannot mix
reports or sign an in-flight result with a newer lock.

## Candidate Ladder

1. Whole-session delay is retained as a control only.
2. Sub-window delay refinement fits linear FIR paths with 128, 512 and 1280 taps.
3. A bounded Hammerstein rung adds `x * abs(x)` and `x^3` remote bases.
4. Held-out validation selects a model only inside confirmed remote-only speaker-state intervals.
5. Every local-only, double-talk, opening, other-local, uncertain and boundary interval remains
   sample-exact production v2 audio.

The implementation writes only the isolated profile
`derived/preprocess/alignment-echo-path-model-v3/`. It does not publish `mic_for_asr`, mutate raw CAF
or receive credit from post-ASR cleanup.

## Results

Controlled development contains 188 items:

| Metric | Result | Gate |
|---|---:|---:|
| Measured remote items safely changed | 11 / 32 | at least 12 |
| Median remote reduction | 2.552124 dB | at least 2.0 dB |
| p10 remote reduction | 1.628259 dB | at least 0.0 dB |
| Protected non-remote exact retention | 156 / 156 | 100% |
| Nonlinear candidates selected | 0 | only with independent gain |

Pre-commit review found that correlation and FIR helpers centered NumPy views in place, making the
ladder order-dependent. All observations from that runtime were invalidated before hard/sealed
access, the threshold was restored to `0.95`, and non-mutation regression tests were added. The
corrected initial run selected 4/32 remote items with median reduction `4.470565 dB`.

The only allowed revision then relaxed held-out coherence from `0.95` to `0.975`. It selected 11/32
remote items. The required coverage still was not met, so the protocol stopped. No further threshold
tuning is allowed.

The final real development pass remained audio-only because the controlled gate failed:

- 11 of 12 sessions produced an isolated candidate and one used exact fallback;
- 1684.870 seconds were changed, all inside eligible remote-only intervals;
- median and p90 remote coherence ratios were 0.872114 and 0.937482;
- protected-local and double-talk changed samples: 0;
- the required headphones/low-leak control changed by 1.100 seconds instead of exact fallback;
- three other controls also changed; all control changes totaled 143.028 seconds;
- direct-ASR gates were intentionally not run and therefore did not pass.

The required-control failure and controlled coverage miss mean the physical-model selector is not
sufficiently discriminative for production even though it can reduce coherent residual echo.

## Interpretation

The experiment closes the planned alignment and echo-path ladder rather than one parameterization.
Time-varying FIR paths help where the remaining mic signal is still a coherent transform of remote.
They do not explain enough of the controlled corpus, and the nonlinear bases did not provide stable
independent utility. More tuning on the same development data would weaken the evidence.

The next justified capability is a multi-component separator that models at least four outputs:

```text
production v2 mic + authoritative remote + Target-Me query + echo estimate
    -> Target-Me + remote echo + other-local + unexplained residual
```

It must preserve mixture consistency and use exact production fallback whenever source identity or
local preservation is uncertain. Another scalar residual mask or larger FIR bank is not the next
step.

## Reproduction

The normal verification does not open hard or sealed data:

```bash
.venv/bin/python scripts/alignment-echo-path-model-v3.py verify
.venv/bin/python scripts/check-alignment-echo-path-model-v3.py
```

Attempting a hard or sealed run after the failed development gate exits before reading those
sessions. Generated reports remain private under `sessions/_reports/alignment-echo-path-model-v3/`.
