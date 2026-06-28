# RFC-0001: MurmurMark v1 Scope

Status: draft  
Date: 2026-06-22

## Summary

Build MurmurMark v1 as a local-first macOS meeting capture and notes pipeline.

v1 proves:

- reliable two-track local capture;
- explicit session package;
- local heavy transcription pipeline;
- evidence-backed synthesis;
- privacy and retention controls.

Current implementation status, 2026-06-24:

- capture, session packaging and Echo Guard preprocessing are implemented in the CLI spike;
- the practical transcription path is a local `whisper.cpp` bridge with `Me`/`Colleagues` roles, timeline repair and shadow audit output;
- full heavy-local transcription, diarization, evidence-backed synthesis and retention automation remain future work.

## Goals

- Native macOS capture of selected meeting app audio and selected microphone.
- No virtual audio devices by default.
- No meeting app routing changes.
- CLI-first workflow with future menubar app on the same core.
- Durable session package with `session.json` and `events.jsonl`.
- Local transcription profile with ASR, diarization, correction and quality report.
- Echo diagnostics so remote audio leaking into mic is not attributed to the user.
- Long-meeting support through windowing and reconciliation.
- Synthesis that cites transcript evidence.

## Non-Goals

- Production-grade signed app in the first implementation pass.
- Legal consent automation.
- Perfect speaker identity.
- Full Confluence/Jira/GitHub write integration.
- Training new models.
- Sending raw audio to cloud providers.

## Milestones

### M0: Capture Spike

Command:

```bash
murmurmark record \
  --target-bundle com.microsoft.teams2 \
  --mic none \
  --out ./session
```

Acceptance:

- remote audio is non-empty;
- system output is unchanged;
- cleanup is correct;
- events and session manifest are written.

### M1: Mic Capture

Acceptance:

- selected mic is captured independently;
- system default input is unchanged;
- target app continues using its own configured mic;
- mic disconnect creates warning, not silent failure.

### M2: Two-Source Session

Acceptance:

- `mic.caf` and `remote.caf` are produced together;
- host timestamps and sample counters are stored;
- `inspect` can report track health;
- stop-and-delete works.

### M3: Menubar UX

Acceptance:

- permission onboarding;
- app picker;
- mic picker;
- level meters;
- health warnings;
- start/pause/mark/stop/delete.

### M4: Pipeline Handoff

Acceptance:

- `pipeline_job.json` can be created;
- ASR-ready working audio can be materialized;
- raw retention policy can run after successful outputs.

### M4.5: Echo Guard Diagnostics and Derived Cleanup

Status: implemented for diagnostics, `linear_baseline` cleanup, session-wide `local_fir` cleanup, SpeexDSP cleanup, WebRTC APM cleanup and transcript-level leakage suppression.

Acceptance:

- `echo_diagnostics.json` can be created without changing raw audio;
- probable remote bleed segments are written to `echo_segments.jsonl`;
- quality report can summarize bleed and delay;
- transcript reconciliation can exclude remote-like mic utterances from `me`;
- optional clean mic is a derived artifact only;
- `linear_baseline` can create a conservative clean candidate and reject it through quality gates;
- `local_fir` can create `mic_clean_local_fir.wav`, `mic_role_masked_for_asr.wav`, `mic_role_preview.wav`, `local_fir_report.json`, `speaker_state.jsonl` and mic ASR chunk manifests;
- `local_fir` defaults to `preserve_local`, so quiet local speech is kept unless a region is confidently silent;
- `speexdsp` can create `mic_clean_speex.wav` through the local helper and reject it through the same quality gates;
- `webrtc-apm` can create `mic_clean_webrtc.wav` through the bundled Rust helper and reject it through the same quality gates.

### M5: Heavy-Local Transcription

Status: implemented for the current CLI through the local `whisper.cpp` path. The heavier ASR,
forced-alignment and diarization stack remains a future validator or replacement.

Current implemented path:

- exports ASR-ready mic and remote WAV files;
- runs local `whisper-cli` on overlapping windows;
- treats `remote` as authoritative `Colleagues`;
- treats `mic` as candidate `Me`;
- repairs long mic candidates that cross remote intervals;
- runs micro-ASR on short local islands;
- writes baseline, `shadow_v2`, cleanup and reviewed transcript artifacts;
- writes quality verdicts and evidence-backed extractive notes;
- exports Markdown/Obsidian bundles and retention/payload manifests;
- keeps uncertainty and audit evidence in JSON/JSONL reports.

Current M5 acceptance:

- remote track processed by primary ASR;
- mic track processed through selected `mic_for_asr`;
- remote-like mic utterances are not treated as the user's speech;
- `clean_dialogue*.json`, `quality_report*.json`, `transcript*.md`, quality verdict and notes emitted.

Future M5+ acceptance:

- remote diarization processed independently;
- `transcript.rich.json` and `speaker_map.json` emitted as stable rich artifacts.

### M6: Evidence-Backed Synthesis

Acceptance:

- notes contain utterance IDs;
- uncited facts are rejected or flagged;
- local-only policy works;
- docs patch plan is generated, not applied.

## Open Questions

- Minimum supported macOS version: target 14.4+ unless implementation proves 14.2 is stable enough.
- Whether Qwen3-ASR/ForcedAligner belongs in v1 or v1.x.
- Whether local voiceprints are safe enough for v1 or should wait.
- Which local LLM backend should be the first correction/synthesis adapter.
- Whether the first menubar app should use SwiftUI or AppKit-first status item.

## Risks

- Core Audio Process Tap behavior changes across macOS versions.
- Browser meetings may capture unrelated browser audio.
- Long-running sessions can drift between mic and remote clocks.
- Heavy local ASR may require workstation-class GPU.
- Speaker identity errors can cause worse notes than no identity.

## Required ADRs

- ADR-0001: Core Audio Taps as primary remote capture.
- ADR-0002: No virtual audio devices by default.
- ADR-0003: Separate CAF raw streams.
- ADR-0004: File contract between capture and pipeline.
- ADR-0005: Heavy-local transcription stack.
- ADR-0006: Evidence-backed synthesis.
- ADR-0008: ScreenCaptureKit bridge for the first CLI smoke build.
- ADR-0009: Derived-only echo suppression.
