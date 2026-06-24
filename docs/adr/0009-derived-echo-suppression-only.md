# ADR-0009: Keep Echo Suppression as Derived Processing Only

Status: accepted  
Date: 2026-06-22

## Context

When the user records without headphones, remote audio can leak from speakers into the physical microphone. This means `audio/mic/*.caf` can contain both the user's voice and a quieter, delayed copy of `remote`.

The obvious response is to "clean the mic". That is risky. Acoustic echo cancellation can damage local speech, especially during double-talk, delay mismatch, clipping, room reflections and nonlinear speaker distortion.

MurmurMark also treats raw audio as an audit source. Rewriting raw mic would break that property.

## Decision

Echo suppression is a derived preprocessing stage only.

Rules:

- never modify `audio/mic/*.caf`;
- never modify `audio/remote/*.caf`;
- write diagnostics and clean candidates under `derived/preprocess/`;
- require transcript-level leakage suppression before trusting audio cleanup;
- use clean mic for ASR only after quality gates pass;
- keep raw mic as fallback and evidence.

## Consequences

Benefits:

- capture stays simple and reliable;
- raw evidence remains intact;
- the pipeline can improve ASR without hiding uncertainty;
- bad cleanup can be rejected safely;
- different AEC engines can be tested behind the same file contract.

Costs:

- more derived artifacts and reports;
- role reconciliation must understand leakage flags;
- quality gates need their own metrics and test sessions;
- perfect echo cancellation remains a longer-term project.

## Accepted Engines

Current recommended experimental engine:

- session-wide `local_fir`, using separated `remote` and `mic` files, conservative quality gates and a default `preserve_local` role policy.

Preferred production candidate:

- WebRTC Audio Processing Module / AEC3 in a separate helper.

Baseline candidate:

- SpeexDSP echo canceller for diagnostics and contract validation.

Architecture reference:

- PipeWire echo-cancel module model.

## Rejected Options

- Overwrite `audio/mic/*.caf`.
- Treat cleaned mic as the only source of truth.
- Run AEC inside the real-time capture path for the first product version.
- Enable privacy-risky AEC debug dumps by default.
- Block v1 recording on high-quality AEC.

## Follow-Up

Echo Guard is now implemented in the preprocess pipeline. Current derived artifacts include:

```text
derived/preprocess/audio/mic_raw_for_asr.wav
derived/preprocess/audio/remote_for_aec.wav
derived/preprocess/audio/mic_clean_local_fir.wav
derived/preprocess/audio/mic_role_masked_for_asr.wav
derived/preprocess/audio/mic_role_preview.wav
derived/preprocess/audio/echo_hat_local_fir.wav
derived/preprocess/audio/mic_clean_webrtc.wav
derived/preprocess/audio/mic_for_asr.wav
derived/preprocess/mic_asr_segments/segments_manifest.json
derived/preprocess/echo/echo_diagnostics.json
derived/preprocess/echo/echo_segments.jsonl
derived/preprocess/echo/local_fir_report.json
derived/preprocess/echo/local_fir_segments.jsonl
derived/preprocess/echo/speaker_state.jsonl
derived/preprocess/echo/echo_suppression_report.json
```
