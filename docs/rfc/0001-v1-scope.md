# RFC-0001: MurmurMark v1 Scope

Status: implemented
Date: 2026-06-22
Last updated: 2026-08-19

## Summary

Build MurmurMark v1 as a local-first macOS meeting transcription pipeline with optional evidence
derivatives.

v1 proves:

- reliable two-track local capture;
- explicit session package;
- local heavy transcription pipeline;
- speaker attribution and evidence-backed quality review;
- privacy and retention controls.

Current implementation status, 2026-08-20:

- capture, session packaging, Echo Guard preprocessing, resumable local `whisper.cpp` transcription,
  quality verdict, evidence-backed extractive notes, guarded export and retention are implemented;
- `local_speech_completion_v2` is promoted for its frozen two-session scope; it safely materializes
  independently confirmed local speech and exposes every unresolved local/text defect through an
  executable review lane. `residual_local_recall_v1` remains the fallback outside that scope;
- Speaker-Preserving Neural Echo v2.17 is the guarded pre-ASR production selector for compatible
  speaker-playback sessions and the pinned current ASR runtime, with exact
  `local_fir_role_masked` fallback;
- reference-conditioned three-stem separation v1 and v2 completed with `DO_NOT_PROMOTE`; the
  speaker-disjoint Target-Me corpus proved query identity, but the bounded separator missed locked
  waveform-quality gates and production remained unchanged;
- Evidence Handoff v2, guarded export, release-quality packaging and bounded one-command meeting
  completion are implemented;
- Authoritative Incremental ASR is promoted for exact batch resume. Remote-only and causal mic
  precomputation both completed with `DO_NOT_PROMOTE`; the current post-Echo mic has a measured
  session-end causal boundary;
- the audit-only anonymous speaker map over authoritative remote audio is implemented and promoted
  for optional evidence;
- anonymous rich transcript and explicit session-local reviewed naming are promoted optional read
  surfaces with fingerprint verification and anonymous fail-open behavior;
- Pre-ASR Target-Me Isolation Limit v1 is complete with `DO_NOT_ADVANCE_STRONGER_SEPARATOR` after
  train presence/absence evidence failed; dev, hard, sealed and direct ASR stayed closed while
  production v2.17 remained exact fallback;
- Reviewed Speaker-Aware Meeting Memory v1 is promoted as a separate opt-in reviewed notes/export
  handoff; ordinary outputs remain authoritative fallback;
- committed-PCM live preview exists as an advisory shadow, while batch remains authoritative;
- Evidence-Guarded Local Synthesis v1 completed with `DO_NOT_PROMOTE`; ID-only local evidence
  selection remains an explicit optional derivative;
- Remote Speaker Coverage v3 is promoted with `93.9312%` attributable remote speech, exact
  selected-word and v2-label conservation and aggregate `Colleagues` fallback. Transcript Perfection
  Corpus v1 remains the unified baseline. Residual Evidence v4 closed `DO_NOT_PROMOTE` at its measured
  safe ceiling; Speaker-Resolved Transcript Default v1 is promoted. Exact speaker labs rejected both
  Duration-Aware v2 and Segment-Context v1 candidates. Error Decomposition v1 identified speaker
  identity as the dominant error axis. ECAPA passed synthetic hard-v4 but failed real-session
  promotion; interval, enrollment, representation and temporal variants also did not advance.
  Disjoint Truth v2 and Cluster Purity Reference v1 localized the remaining topology defect to mixed
  boundaries and minority voices. Boundary/minority segmentation kept Coverage v3; fresh rebaseline
  passed and exposed three restart-bounded capture gaps. Capture continuity closure completed
  `EVIDENCE_BOUND`: software restart latency was removed and every remaining native source gap now
  blocks completeness. Remote Unknown Evidence Recovery is current; human-reviewed lexical truth,
  session-scoped lexical context and a terminal product gate follow.
  Production remains Coverage v3. Cross-session identity, summaries, cloud/external writes and UI
  remain future or optional;
- local domain glossaries remain private knowledge inputs. They are not compiled into production ASR
  context until direct lexical truth and no-regression gates qualify Session-Scoped Lexical Context.

## Goals

- Native macOS capture of selected meeting app audio and selected microphone.
- No virtual audio devices by default.
- No meeting app routing changes.
- CLI-first workflow with future menubar app on the same core.
- Durable session package with `session.json` and `events.jsonl`.
- Local transcription profile with ASR, `Me`/remote reconciliation, correction, quality report and
  qualified session-local remote speaker turns. Weak attribution remains explicit and plain
  aggregate transcript is the exact fallback.
- Echo diagnostics so remote audio leaking into mic is not attributed to the user.
- Long-meeting support through windowing and reconciliation.
- Optional synthesis may cite transcript evidence but does not define v1 transcript quality.

## Non-Goals

- Production-grade signed app in the first implementation pass.
- Legal consent automation.
- Cross-session or voice-inferred human identity.
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

Status: optional and deferred. It is not required for CLI usefulness or v1 acceptance.

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

Current M5+ status:

- remote diarization is processed independently at word/frame level;
- internal speaker changes and overlap are represented without word loss or duplication;
- rich anonymous speaker artifacts are stable; ordinary reading prefers verified attribution, then a
  disclaimer-bearing provisional view, while exact aggregate remains explicit and strict;
- direct-truth and disjoint model qualification kept Coverage v3; private cluster-purity evidence
  routed a bounded experiment to remote boundaries and minority preservation; it kept Coverage v3,
  so current work rebaselines the complete speaker-resolved result before another identity candidate;
- local multi-speaker mic remains conditional.

### M6: Evidence-Backed Synthesis

Status: implemented for deterministic extractive notes, quality verdict, evidence IDs and guarded
Markdown/Obsidian export. Generative synthesis and external write integrations remain future work.

Acceptance:

- notes contain utterance IDs;
- uncited facts are rejected or flagged;
- local-only policy works;
- docs patch plan is generated, not applied.

## Open Questions

- Minimum supported macOS version: target 14.4+ unless implementation proves 14.2 is stable enough.
- Whether Qwen3-ASR/ForcedAligner belongs in v1 or v1.x.
- Session-local anonymous voice evidence is allowed with abstention; cross-session identity remains
  forbidden without a separate privacy contract.
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
