# Pre-ASR Residual Echo Ceiling Map v1

Status: completed with `READY_FOR_ALIGNMENT_OR_ECHO_MODEL_V3`

Date: 2026-08-06

## Question

Speaker-Preserving Neural Echo v2 is the safest production profile available today, but direct mic
ASR still recognizes authoritative remote speech. This study asks which capability limits the
remaining result before another separator is designed.

The study is discovery-only. It freezes raw CAF, production v2, direct-ASR inputs and reports, then
classifies each material remote-supported event on two independent axes:

- `signal_truth`: what the audio, text and speaker-state evidence says is present;
- `production_blocker`: why the guarded production profile preserved that interval.

No audio, transcript, threshold for production or selected profile was changed.

## Frozen Evidence

- 14 real sessions: the 12-session production v2.16 corpus plus two fresh post-promotion sessions;
- 2447 residual events, of which 2068 are material;
- 16,456 matched remote tokens, 16,009 in material events;
- 7479.895 weighted material remote-supported seconds;
- 6869.306 actionable material seconds after excluding unsupported acoustic modes;
- frozen input fingerprint:
  `1df6cc703c130c178487cd0e8093c71e33cf249cd2eec23b031f75bae0b476c9`.

One bounded discovery revision was used before the final freeze. It accepts convergent strong remote
text, strong remote-only state and medium acoustic evidence when local evidence is weak. Further
threshold revisions are forbidden for this corpus.

## Signal Truth

| Class | Events | Weighted seconds | Sessions |
|---|---:|---:|---:|
| `confirmed_remote_echo` | 1199 | 4456.007 | 12 |
| `mixed_double_talk` | 290 | 1173.131 | 9 |
| `asr_instability` | 451 | 1207.163 | 9 |
| `unknown` | 128 | 643.597 | 12 |

The actionable unknown share is `9.216%`, below the locked `25%` stop threshold. The map therefore
has enough evidence to choose the next capability without pretending that every event is resolved.

## Production Blockers

| Blocker | Events | Weighted seconds | Sessions |
|---|---:|---:|---:|
| `echo_path_mismatch` | 693 | 2156.296 | 9 |
| `alignment_uncertainty` | 50 | 102.911 | 6 |
| `boundary_guard` | 51 | 184.013 | 9 |
| `local_preservation_guard` | 435 | 2124.220 | 10 |
| `target_identity_uncertainty` | 263 | 1258.702 | 8 |
| `metric_artifact` | 311 | 796.368 | 5 |
| `insufficient_evidence` | 60 | 246.796 | 9 |
| `unsupported_mode` | 205 | 610.589 | 4 |

## Capability Decision

The capability ranking uses only actionable material seconds:

| Capability | Weighted seconds | Share | Sessions | Gate |
|---|---:|---:|---:|---|
| `alignment_or_echo_model_v3` | 2443.222 | 35.567% | 9 | material |
| `multi_component_separator` | 2124.220 | 30.923% | 10 | material |
| `target_speaker_model` | 1258.702 | 18.324% | 8 | secondary |
| `remote_metric_repair` | 796.368 | 11.593% | 5 | secondary |
| `more_supervision` | 246.796 | 3.593% | 9 | secondary |

The deterministic decision is:

```text
READY_FOR_ALIGNMENT_OR_ECHO_MODEL_V3
```

The previous plan to start with another Target-Me separator is not supported by the measured
ordering. The largest tractable residue comes from time-varying alignment, echo-path mismatch and
boundary handling. A future multi-component separator remains important, but it must follow or
explicitly absorb that echo-path capability rather than compensate for it blindly.

## Next Bounded Experiment

Alignment and Echo-Path Model v3 Qualification should test, in isolation:

1. sub-window delay and drift tracking instead of one whole-session delay;
2. a small bank of echo-path hypotheses for room, speaker and gain changes;
3. nonlinear remote bases for loudspeaker coloration and mild distortion;
4. bounded suppression only where independent evidence confirms remote and weak local speech;
5. exact per-window fallback to production v2 for double-talk, uncertain Target-Me or regressions.

The experiment must lock its dev gates before hard or sealed data. Direct whisper.cpp on candidate
audio remains the semantic gate; post-ASR deletion receives no credit.

## Reproduction

```bash
.venv/bin/python scripts/pre-asr-residual-echo-ceiling-map-v1.py freeze
.venv/bin/python scripts/pre-asr-residual-echo-ceiling-map-v1.py run
.venv/bin/python scripts/pre-asr-residual-echo-ceiling-map-v1.py verify
```

The report lives under `sessions/_reports/pre-asr-residual-echo-map-v1/`. `verify` checks all frozen
SHA-256 values, event reconciliation and deterministic replay. The report is intentionally ignored
by Git because it refers to private local session artifacts.
