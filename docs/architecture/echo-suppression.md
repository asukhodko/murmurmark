# Echo Guard Architecture

Echo Guard is the MurmurMark subsystem for handling remote audio that leaks into the microphone track.

The goal is not to make the raw microphone recording "clean". The goal is to keep role attribution and ASR input safe when `mic` contains a quieter, delayed, room-colored copy of `remote`.

For speaker bleed, the v1 engineering target is offline Echo Guard over the already separated `mic` and `remote` tracks. Headphones remain the simplest user workaround, but MurmurMark should not depend on BlackHole, Loopback or other routing changes to solve the algorithmic subtraction problem.

## Core Rule

Raw capture is never modified.

```text
audio/mic/*.caf
audio/remote/*.caf
```

These files remain the audit record. Echo Guard writes only derived artifacts under `derived/`.

## Pipeline Position

```text
Capture
  -> audio/mic/*.caf
  -> audio/remote/*.caf
  -> session.json

Preprocess
  -> working audio
  -> VAD
  -> Echo Guard diagnostics
  -> optional derived echo suppression

Transcribe
  -> selected mic_for_asr
  -> remote ASR
  -> role reconciliation
```

## Derived Layout

```text
session/
  derived/
    preprocess/
      audio/
        mic_raw_for_asr.wav
        remote_for_aec.wav
        mic_clean_linear.wav
        mic_clean_local_fir.wav
        mic_role_masked_for_asr.wav
        mic_role_preview.wav
        echo_hat_local_fir.wav
        mic_clean_webrtc.wav
        mic_clean_speex.wav
        mic_for_asr.wav
      mic_asr_segments/
        segments_manifest.json
        mic_000001.wav
        mic_000002.wav

      echo/
        echo_diagnostics.json
        echo_segments.jsonl
        local_fir_report.json
        local_fir_segments.jsonl
        speaker_state.jsonl
        echo_suppression_report.json
```

`mic_for_asr.wav` is the selected working microphone track for ASR. It may point to a prepared raw mic file or to a clean derived file. It is not a source of truth.

For the current `local_fir` engine, `mic_for_asr.wav` is copied from `mic_role_masked_for_asr.wav` only after the quality gate accepts the candidate. `mic_clean_local_fir.wav` remains the diagnostic cleaned signal, and `mic_role_preview.wav` is a listening aid that concatenates retained mic regions with short guards.

## CLI Modes and Profiles

- `off`: do nothing.
- `diagnostic`: detect probable remote bleed and materialize working audio, do not select clean audio.
- `clean --echo-profile conservative`: create a clean candidate and use it only if quality gates pass.
- `clean --echo-profile experimental_aggressive`: stronger cleanup for manual experiments; never default.

`local_fir` has an additional role policy:

- `preserve_local`: default. Prefer keeping local speech over deleting questionable regions.
- `role_safe`: hard-mute only high-confidence `remote_only` regions.
- `strict_silence`: hard-mute every remote-active mic region; this can delete local overlap speech.

Default for the next useful pipeline pass:

```yaml
echo_mode: clean
echo_engine: local_fir
echo_profile: conservative
role_policy: preserve_local
mic_asr_policy: use_role_mask_only_after_quality_gate
```

## Echo Diagnostics

Inputs:

```text
audio/mic/*.caf
audio/remote/*.caf
session.json
```

Outputs:

```text
derived/preprocess/echo/echo_diagnostics.json
derived/preprocess/echo/echo_segments.jsonl
```

Algorithm sketch:

1. Materialize working WAV files:
   - `mic_raw_for_asr.wav`
   - `remote_for_aec.wav`
2. Normalize format:
   - 48 kHz;
   - mono;
   - PCM float or int16;
   - common session timeline.
3. Split into windows:
   - coarse windows: 5-10 seconds;
   - analysis frames: 10-20 ms.
4. Estimate delay:
   - envelope correlation, GCC-PHAT or normalized cross-correlation;
   - search range around 0-1500 ms;
   - separate estimate per coarse window;
   - smooth the delay curve.
5. Compute bleed score:
   - correlation after delay alignment;
   - remote energy vs mic energy;
   - spectral-envelope similarity;
   - later text similarity after ASR.
6. Write probable bleed segments with delay, confidence and double-talk flags.

Example `echo_diagnostics.json`:

```json
{
  "schema": "murmurmark.echo_diagnostics/v1",
  "session_id": "2026-06-22T20-42-57_7f3a",
  "inputs": {
    "mic": "audio/mic/000001.caf",
    "remote": "audio/remote/000001.caf"
  },
  "working_audio": {
    "mic": "derived/preprocess/audio/mic_raw_for_asr.wav",
    "remote": "derived/preprocess/audio/remote_for_aec.wav"
  },
  "summary": {
    "bleed_detected": true,
    "median_delay_ms": 182,
    "delay_range_ms": [145, 238],
    "segments_with_probable_bleed": 34,
    "recommendation": "try_conservative_aec"
  }
}
```

Example `echo_segments.jsonl`:

```jsonl
{"start":12.40,"end":18.20,"delay_ms":181,"bleed_score":0.82,"double_talk":false,"confidence":0.91}
{"start":44.10,"end":49.80,"delay_ms":207,"bleed_score":0.63,"double_talk":true,"confidence":0.74}
```

Diagnostics is safe: it does not change audio and can only add warnings and pipeline hints.

## Transcript-Level Suppression

This is the most important part for MurmurMark.

If a mic utterance matches a remote utterance by time, text and leakage diagnostics, it should not be treated as the user's speech.

Example:

```json
{
  "id": "utt_000203",
  "source_track": "mic",
  "raw_text": "да давайте посмотрим сло",
  "quality": {
    "possible_mic_leakage": true,
    "excluded_from_me_role": true,
    "matched_remote_utterance_id": "utt_000198",
    "reason": "matches remote utterance with 180 ms delay",
    "needs_review": false
  }
}
```

Rules:

- do not count excluded leakage as `me`;
- do not use it as evidence for actions assigned to the user;
- preserve uncertainty in `transcript.rich.json`;
- keep raw mic and remote references available.

This can improve role attribution even before audio cleanup works well.

CLI implementation:

```bash
murmurmark reconcile-transcript ./session
```

Default inputs and outputs:

```text
input:  derived/transcript/resolved/transcript.rich.json
input:  derived/preprocess/echo/echo_diagnostics.json
input:  derived/preprocess/echo/echo_segments.jsonl
output: derived/transcript/resolved/transcript.rich.json
output: derived/transcript/resolved/quality_report.json
output: derived/transcript/resolved/echo_reconciliation_report.json
```

The command is conservative. It excludes a mic utterance from `me` only when both checks pass:

- the mic utterance is close to a remote utterance after applying the Echo Guard delay;
- the mic and remote texts are similar enough.

## Local FIR Derived Cleanup

Status: implemented as the current recommended experimental engine for real speaker-bleed sessions.

Command:

```bash
murmurmark preprocess ./session --echo clean --echo-engine local_fir
```

Inputs:

```text
derived/preprocess/audio/mic_raw_for_asr.wav
derived/preprocess/audio/remote_for_aec.wav
```

Outputs:

```text
derived/preprocess/audio/mic_clean_local_fir.wav
derived/preprocess/audio/mic_role_masked_for_asr.wav
derived/preprocess/audio/mic_role_preview.wav
derived/preprocess/audio/echo_hat_local_fir.wav
derived/preprocess/mic_asr_segments/segments_manifest.json
derived/preprocess/echo/local_fir_report.json
derived/preprocess/echo/local_fir_segments.jsonl
derived/preprocess/echo/speaker_state.jsonl
derived/preprocess/echo/echo_suppression_report.json
```

Algorithm:

1. Read the already separated mic and remote working WAV files.
2. Convert DSP analysis to 16 kHz mono float.
3. Apply a 120 Hz high-pass filter before fitting.
4. Estimate file alignment delay with GCC-PHAT, including negative delay.
5. Classify 8 second windows and 2 second chunks as `remote_only`, `local_only`, `double_talk` or `silence`.
6. Fit an 80 ms FIR echo model from the nearest reliable remote-dominant region, or from the current remote-dominant chunk when that is stable.
7. Subtract strongly in remote-only regions, mildly in double-talk, and pass local-only regions raw.
8. Write both the cleaned diagnostic track and the role-selected ASR track.

Current default parameters:

```text
sample_rate: 16000
delay search: -500..2000 ms
window: 8 sec
hop: 2 sec
FIR tail: 80 ms
regularization: 1e-2
high-pass: 120 Hz
remote-only strength: 1.0
double-talk strength: 0.35
preserve-local mic floor margin: 18 dB
```

Why the `2 sec` chunk exists: it is short enough to follow changing speaker activity and long enough to fit a stable echo estimate without reacting to every syllable. The wider `8 sec` window gives delay and role classification more context; the `2 sec` hop is the decision cadence for subtraction and role selection.

Default policy:

```text
preserve_local
```

This policy intentionally keeps ambiguous remote-active regions as mildly cleaned audio instead of hard-muting them. The product risk being avoided is losing the user's quiet speech, especially greetings, short confirmations and overlap speech. More aggressive policies exist for experiments, but they are not the default.

## Complete Echo Removal Research

Status: research and experiment plan, not a default engine.

The current `local_fir` engine is a preserve-local compromise: it reduces remote leakage, but it is not expected to remove every recognizable remote word from `mic`. Real sessions show that remaining remote residue creates review burden and role-attribution risk.

The next Echo Guard research line is documented in [Complete Echo Removal Research](../research/2026-06-30-complete-echo-removal.md). The working definition of "complete" is product-complete, not waveform-perfect:

- ASR on cleaned mic must not recover remote words as `Me`;
- local words must remain recoverable, including greetings, backchannels and overlap comments;
- genuinely ambiguous regions must stay auditable instead of being silently muted;
- raw CAF tracks remain immutable.

Promising directions:

- `offline_aec_v2`: long-tail adaptive filtering, drift-aware alignment, nonlinear remote bases, multi-hypothesis echo path banks and residual masks;
- neural residual echo suppression after a classical echo estimate;
- target-speaker extraction for `Me` from local-only enrollment islands;
- token-level remote-forbidden transcript construction as the final safety net.

None of these should replace `local_fir` by default until corpus gates show lower remote-token leakage without worse local-word recall.

## Offline AEC v2 Shadow Lab

Status: implemented as a diagnostic lab, not as a default Echo Guard engine.

Command:

```bash
.venv/bin/python scripts/echo-guard-offline-aec-v2-lab.py "$SESSION"
```

The lab assumes the normal working files already exist:

```text
derived/preprocess/audio/mic_raw_for_asr.wav
derived/preprocess/audio/remote_for_aec.wav
derived/preprocess/echo/speaker_state.jsonl
derived/preprocess/echo/local_fir_report.json
```

It creates multiple candidates:

- `linear_tail80`;
- `linear_tail160`;
- `linear_tail320`;
- `nonlinear_tail160_mask`;
- `nonlinear_tail160_remote_strongmask`;
- `nonlinear_tail160_remote_floor`.

The current nonlinear candidates use a small remote basis bank:

```text
remote
band_limited
clipped
tanh
compressed
signed_power
```

The first implementation is deliberately conservative:

- delay is estimated as a smoothed trajectory;
- FIR application uses offline FFT convolution;
- residual masking is strongest only in `remote_only` regions;
- local-only, opening/backchannel and double-talk preservation are measured separately;
- reports always say `promotion_decision: shadow_only_not_promoted`.

Primary outputs:

```text
derived/preprocess/audio/mic_clean_offline_aec_v2.wav
derived/preprocess/audio/echo_hat_offline_aec_v2.wav
derived/preprocess/echo/offline_aec_v2_report.json
derived/preprocess/echo/offline_aec_v2_candidates.jsonl
derived/preprocess/echo/offline_aec_v2_segments.jsonl
derived/preprocess/echo/offline_aec_v2_asr_leak_report.json
derived/preprocess/echo/offline_aec_v2_near_end_preservation_report.json
```

Current evidence on the first six-session smoke:

- no candidate is promoted;
- `nonlinear_tail160_remote_floor` improves proxy harmful-remote seconds and remote-only dB on all
  checked sessions;
- this is a `remote_only` speech-aware floor, not waveform-perfect echo cancellation;
- one mostly-silent session regressed local-only recall proxy, which keeps the candidate blocked;
- faster-whisper ASR clip audit across all candidates found no session where `offline_aec_v2_v0`
  reduced remote-token leakage below `local_fir` without local-recall regression;
- the next iteration needs a genuinely ASR-positive mechanism, not a stronger dB/proxy mask.

## Other Conservative Cleanup Engines

Cleanup is for ASR quality, not for rewriting history.

Inputs:

```text
derived/preprocess/audio/mic_raw_for_asr.wav
derived/preprocess/audio/remote_for_aec.wav
derived/preprocess/echo/echo_diagnostics.json
```

Outputs:

```text
derived/preprocess/audio/mic_clean_webrtc.wav
derived/preprocess/echo/echo_suppression_report.json
```

Implemented comparison engines:

- `linear_baseline`;
- `speexdsp`;
- `webrtc-apm`.

WebRTC Audio Processing Module remains a production candidate for future comparison, but current real-session testing favored `local_fir` because it can be fit from nearby remote-only regions in the already captured files.

WebRTC APM is designed for real-time communications and operates on two frame-by-frame streams: a primary capture stream and a reverse/render stream. In MurmurMark terms:

- capture stream: `mic`;
- reverse/render stream: `remote`.

Offline wrapper sketch:

```text
for frame_time in session_timeline.step(10ms):
    delay_ms = delay_map.value_at(frame_time)

    remote_frame = remote.read_frame(frame_time - delay_ms)
    mic_frame = mic.read_frame(frame_time)

    apm.ProcessReverseStream(remote_frame)
    apm.set_stream_delay_ms(delay_ms)
    clean_frame = apm.ProcessStream(mic_frame)

    writer.write(clean_frame)
```

Real implementation needs a ring buffer and strict timeline handling. The render frame must be presented in the right order, and delay must describe when that render appears as echo in capture.

## Engine Candidates and Baselines

### WebRTC APM

Use as the main production candidate.

Pros:

- strong open-source AEC lineage;
- includes echo cancellation, noise suppression, AGC and VAD;
- has standalone packaging history through `webrtc-audio-processing`;
- permissive BSD-style licensing profile with patent grant in upstream WebRTC.

Cons:

- C++ dependency;
- awkward build and API surface;
- expects frame-based processing and correct delay handling.

Preferred integration:

```text
murmurmark-aec-webrtc
  input:
    mic_raw_for_asr.wav
    remote_for_aec.wav
    echo_diagnostics.json
  output:
    mic_clean_webrtc.wav
    echo_suppression_report.json
```

Keep this helper out of the capture core.

### SpeexDSP

Use as a baseline and diagnostic fallback.

Pros:

- simpler C API;
- useful documentation around frame size, filter length and timing;
- good for validating file contracts and delay estimation.

Cons:

- older AEC;
- likely weaker than WebRTC in difficult rooms, clipping, nonlinear distortion and double-talk.

### PipeWire Echo Cancel

Use as an architecture reference, not as a macOS dependency.

Its model is useful: a far-end playback stream is correlated with microphone capture and removed from the capture stream. MurmurMark needs the offline-file version of this model, not a system virtual source.

### Apple Voice Processing

Useful for apps that own the real-time audio session. It is not the main offline derived-AEC path for MurmurMark because MurmurMark records existing `mic` and `remote` tracks and processes them after capture.

### Linear Baseline

Status: implemented.

This is a small offline baseline inside the Swift CLI:

```bash
murmurmark preprocess ./session --echo clean --echo-engine linear_baseline
```

It uses Echo Guard diagnostics, estimates a per-segment gain between delayed `remote` and `mic`, writes `mic_clean_linear.wav`, then decides whether that clean file is safe enough to become `mic_for_asr`.

This is not production-grade AEC. Its purpose is to:

- prove the derived-cleanup contract;
- produce `echo_suppression_report.json`;
- exercise quality gates and fallback behavior;
- provide a baseline before SpeexDSP/WebRTC integration.

## Quality Gates

Never use clean mic by default just because it exists.

`mic_clean_*` can become `mic_for_asr` only if quality gates pass:

- residual remote similarity is substantially lower than in raw mic;
- residual remote similarity is low enough in absolute terms;
- probable local speech is not heavily reduced or damaged;
- ASR on clean mic does not lose important words compared with raw mic;
- no new clipping, dropouts, pumping or metallic artifacts;
- double-talk segments are not made worse.

Current non-`local_fir` conservative implementation thresholds:

- processed safe segments: `> 0`;
- `remote_similarity_after <= remote_similarity_before * 0.65`;
- `remote_similarity_after <= 0.45`;
- `near_end_speech_loss_ratio <= 0.30`.

Current `local_fir` conservative quality gate:

- reliable delay windows: `>= 10`;
- remote-only windows: `>= 5`;
- remote-only median reduction: `>= 3 dB`;
- local-only median energy delta: `-1.5..+1.0 dB`;
- local-only VAD duration ratio: `0.90..1.10`;
- output contains no NaN/Inf;
- clean output does not clip;
- peak scaling, if needed, is small.

For `local_fir`, acceptance promotes `mic_role_masked_for_asr.wav` to `mic_for_asr.wav`, not the diagnostic `mic_clean_local_fir.wav`. This keeps the ASR input aligned with the role policy.

Example `echo_suppression_report.json`:

```json
{
  "schema": "murmurmark.echo_suppression_report/v1",
  "session_id": "2026-06-22T20-42-57_7f3a",
  "engine": {
    "name": "webrtc-apm",
    "profile": "conservative",
    "frame_ms": 10,
    "sample_rate": 48000
  },
  "inputs": {
    "mic": "derived/preprocess/audio/mic_raw_for_asr.wav",
    "remote": "derived/preprocess/audio/remote_for_aec.wav",
    "diagnostics": "derived/preprocess/echo/echo_diagnostics.json"
  },
  "outputs": {
    "clean_mic": "derived/preprocess/audio/mic_clean_webrtc.wav"
  },
  "decision": {
    "accepted_for_asr": true,
    "mic_for_asr": "derived/preprocess/audio/mic_clean_webrtc.wav",
    "fallback": "derived/preprocess/audio/mic_raw_for_asr.wav"
  },
  "metrics": {
    "remote_similarity_before": 0.74,
    "remote_similarity_after": 0.31,
    "estimated_echo_reduction_db": 8.6,
    "near_end_speech_loss_ratio": 0.02,
    "segments_rejected": 5
  },
  "warnings": [
    {
      "type": "double_talk_risk",
      "start": 44.1,
      "end": 49.8
    }
  ]
}
```

If gates fail:

```json
{
  "decision": {
    "accepted_for_asr": false,
    "mic_for_asr": "derived/preprocess/audio/mic_raw_for_asr.wav",
    "reason": "near_end_speech_damage_detected"
  }
}
```

## Failure Modes

- Wrong delay: AEC does little or damages mic. Reject unstable delay maps.
- Double-talk: the user's speech overlaps remote speech. Prefer transcript-level suppression and conservative cleanup.
- Nonlinear distortion: speakers, clipping, AGC and room reflections cannot be perfectly modeled by simple linear filters.
- Browser capture mismatch: remote track may contain broader browser audio than the actual meeting tab.
- Debug dumps: AEC debug artifacts may contain sensitive audio and must be disabled by default.

## Milestones

### M0: Echo Diagnostics

Status: implemented in the Swift CLI.

```bash
murmurmark preprocess ./session --echo diagnostic
murmurmark inspect ./session --echo
```

Acceptance:

- writes `echo_diagnostics.json`;
- writes probable bleed segments;
- reports median delay and confidence;
- does not alter audio.

Implementation note:

- current analyzer uses RMS energy-envelope similarity;
- analysis frames are 20 ms;
- coarse windows are 10 seconds with 5 second step;
- delay search is 0-1500 ms;
- results are probable diagnostics, not proof of acoustic echo.

### M0.5: Linear Baseline Cleanup

Status: implemented in the Swift CLI.

```bash
murmurmark preprocess ./session --echo clean --echo-engine linear_baseline --echo-profile conservative
```

Acceptance:

- writes `mic_clean_linear.wav`;
- writes `echo_suppression_report.json`;
- keeps raw mic as fallback;
- uses `mic_clean_linear.wav` as `mic_for_asr` only if quality gates pass;
- keeps `mic_for_asr.wav` pointed at raw mic when cleanup is rejected.

### M1: SpeexDSP Baseline

Status: implemented in the Swift CLI through an external local helper.

```bash
murmurmark preprocess ./session --echo clean --echo-engine speexdsp --echo-profile conservative
```

Acceptance:

- writes `mic_clean_speex.wav`;
- writes `echo_suppression_report.json`;
- keeps raw mic as fallback;
- rejects clean output when metrics are poor.

Implementation note:

- `scripts/build-speexdsp-helper.sh` builds `.build/tools/murmurmark-aec-speexdsp`;
- the helper is compiled only when `speexdsp` and `pkg-config`/`pkgconf` are present;
- if the helper is missing, the CLI attempts to build it on demand and prints the install hint from the build script on failure.

### M2: WebRTC APM Helper

Status: implemented in the Swift CLI through a bundled Rust helper.

```bash
murmurmark preprocess ./session --echo clean --echo-engine webrtc-apm --echo-profile conservative
```

Acceptance:

- runs offline against a session package;
- uses delay diagnostics;
- writes `mic_clean_webrtc.wav`;
- chooses raw or clean through quality gates.

Implementation note:

- `tools/murmurmark-aec-webrtc/` builds a helper around the Rust `webrtc-audio-processing` crate with the `bundled` feature;
- `scripts/build-webrtc-apm-helper.sh` builds `.build/tools/murmurmark-aec-webrtc`;
- the helper processes 10 ms render/capture frames and passes the Echo Guard median delay into AEC3 when available;
- it requires `cargo`, `meson`, `ninja`, `cmake` and `abseil` on macOS when the helper has not been built yet.

### M2.5: Session-Wide Local FIR

Status: implemented through a Python helper called by the Swift CLI.

```bash
murmurmark preprocess ./session --echo clean --echo-engine local_fir
```

Acceptance:

- writes `mic_clean_local_fir.wav`;
- writes `echo_hat_local_fir.wav`;
- writes `mic_role_masked_for_asr.wav`;
- writes `mic_role_preview.wav`;
- writes `local_fir_report.json`, `local_fir_segments.jsonl` and `speaker_state.jsonl`;
- writes `derived/preprocess/mic_asr_segments/segments_manifest.json`;
- uses `preserve_local` as the default role policy;
- promotes the role-masked mic to `mic_for_asr.wav` only after the quality gate accepts.

Current validated behavior on a private validation session:

- stable delay around `-76.3 ms`;
- median remote-only reduction around `17 dB`;
- local-only VAD duration preserved;
- manual spot checks found no incorrect drops in the highest-risk muted regions after the `preserve_local` threshold update.

### M3: ASR-Aware Leakage Suppression

Status: implemented in the Swift CLI for existing `transcript.rich.json` files.

```bash
murmurmark reconcile-transcript ./session
```

Acceptance:

- mic utterances matching remote are excluded from `me`;
- notes do not use leakage segments as evidence for the user;
- uncertainty is preserved in `transcript.rich.json`;
- `quality_report.json` records Echo Guard transcript exclusions;
- `echo_reconciliation_report.json` records the matching evidence.

## References

- [WebRTC Audio Processing API](https://webrtc.googlesource.com/src/+/refs/heads/main/api/audio/audio_processing.h)
- [WebRTC LICENSE](https://webrtc.googlesource.com/src/+/refs/heads/main/LICENSE)
- [WebRTC PATENTS](https://webrtc.googlesource.com/src/+/refs/heads/main/PATENTS)
- [Rust webrtc-audio-processing wrapper](https://github.com/tonarino/webrtc-audio-processing)
- [SpeexDSP echo cancellation manual](https://www.speex.org/docs/manual/speex-manual/node7.html)
- [SpeexDSP COPYING](https://github.com/xiph/speexdsp/blob/master/COPYING)
- [PipeWire echo-cancel module](https://docs.pipewire.org/page_module_echo_cancel.html)
- [ICASSP 2022 Acoustic Echo Cancellation Challenge](https://arxiv.org/abs/2202.13290)
