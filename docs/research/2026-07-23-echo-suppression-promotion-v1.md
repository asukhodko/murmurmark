# Echo Suppression Promotion v1 Result

Status: completed with reproducible `DO_NOT_PROMOTE`

Date: 2026-07-23

## Question

Could MurmurMark replace the preserve-local `local_fir` ASR input with one classical or bounded
audio candidate that removes materially more remote speech without losing real `Me` speech?

## Contract

The experiment fixed one signed timeline convention for every engine:

```text
echo_at_mic(t) ~= remote(t - delay(t))
```

Positive delay means the remote render precedes the echo observed by the microphone. WebRTC AEC3,
SpeexDSP and Offline AEC now receive the same aligned remote timeline; negative delay is no longer
silently converted to an unsigned value.

The laboratory baseline is the exact role-aware `mic_role_masked_for_asr.wav`, not a reconstructed
approximation. Every candidate stores engine-native output separately from its canonical ASR view.
Raw CAF and the authoritative transcript remain unchanged.

## Frozen Corpus

Nine sessions cover:

- five speaker-playback meetings;
- three headphones or low-leak meetings;
- one verified no-speech meeting;
- 1x1 and group calls, office noise, double-talk, multiple remote speakers and a 1.5-hour session.

Headphones/low-leak and no-speech sessions correctly selected the no-op baseline. Candidate
promotion was evaluated on the five applicable speaker-playback sessions.

## Candidate Matrix

| Candidate | Result |
|---|---|
| `local_fir_role_masked` | production fallback and exact baseline |
| `coverage_v2_remote_gate_local_fir` | best observed; passed 3/5 applicable sessions |
| `webrtc_aec3_aligned_v1` | remote suppression improved, but double-talk/silence safety and runtime failed |
| `speex_mdf_aligned_v1` | runtime passed, remote improvement failed |
| `offline_aec_v2_best_nonlinear_v1` | remote probes improved, runtime was about `2.09x` baseline |
| `webrtc_aec3_coverage_gate_v1` | safety improved over native AEC3, runtime still failed |

The bounded ASR stage runs only after audio-integrity and runtime gates. Full shadow transcription
is reserved for a corpus finalist; there was no finalist, so no unnecessary full-session ASR was
run.

## Best Candidate Evidence

`coverage_v2_remote_gate_local_fir`:

- reduced ASR-visible remote-risk seconds from `38.53s` to `12.22s` (`68.2845%`);
- preserved ordinary local-only and explicit double-talk probe recall at `100%`;
- stayed within the runtime budget, worst ratio `1.1135x`;
- passed three speaker-playback sessions;
- failed two speaker-playback sessions.

The failures are decisive:

- `2026-07-20_15-15-26-live`: a protected local phrase retained only `45.45%` of baseline-local
  tokens, evidence retention was `45.45%`, and an order probe regressed;
- `2026-07-20_16-30-42-live`: a short local `Да` in a mixed interval disappeared and one
  remote-only probe still produced remote words.

These are not proxy-only failures. They are local `large-v3` ASR observations over exact bounded
audio intervals, with authoritative remote text subtracted before evaluating protected local
tokens.

## Decision

```text
DO_NOT_PROMOTE
fallback: local_fir_role_masked
next hypothesis: neural_residual_echo_suppression_v1
```

The production policy remains fail-open. Ordinary processing invokes the policy after `local_fir`;
the frozen decision keeps the exact baseline and does not run a second full ASR.

The result is deterministic: every session decision and the corpus decision replayed with identical
fingerprints over the same frozen inputs.

## Learned Boundary

`coverage_v2` is a strong remote-only suppressor, but it is still mute-like. When `speaker_state`
misclassifies quiet or short near-end speech as remote-only, the same operation that removes echo
also removes the user. Tuning a stronger floor or a more permissive text cleanup cannot solve this
without moving the error elsewhere.

The next candidate must operate inside mixed speech:

```text
aligned remote + classical echo estimate + mic mixture
-> speech-aware residual suppressor
-> local speech preserved during double-talk
```

A useful next experiment starts from the two frozen counterexamples above and tests a local,
remote-conditioned residual suppressor or target-speaker rescue. It must retain the same promotion
contract and may also end in `DO_NOT_PROMOTE`.
