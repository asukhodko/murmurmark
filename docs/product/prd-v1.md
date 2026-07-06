# Product Requirements: v1

v1 is the first usable local workflow, not a polished commercial product.

It must prove the architecture: safe capture, durable session package, local transcription path, evidence-backed notes, and deletion policy.

## Current Status

As of the current CLI roadmap, MurmurMark has crossed from documentation-only planning into a usable
local workflow for capture, transcript preparation, review, export and retention planning.

Working now:

- two-track local recording through the Swift CLI;
- durable session package with raw `mic` and `remote` CAF tracks;
- Echo Guard preprocessing with `local_fir` and `preserve_local` role policy;
- local `whisper.cpp` transcription bridge;
- `Me`/`Colleagues` role reconciliation;
- timeline repair for long mic segments that cross remote speech;
- `shadow_v2` repair profile with no-regression gates, audit artifacts and start-of-call repair;
- local extractive synthesis with quality verdicts, review items and evidence-backed notes;
- Markdown/Obsidian-style export bundles, JSON/audit artifacts for review, and raw retention plans.
- first near-realtime shadow branch exists, but is quarantined: tests showed that older inline
  segment writing in `record --live-pipeline` can starve ScreenCaptureKit audio delivery and leave
  raw meeting tracks mostly silent. The current async bounded segment queue still needs
  capture-safety and parity proof. It is disabled by default and must not be used for real meetings.
- live-ASR cache bridge exists as a diagnostic/future acceleration layer; incompatible or unsafe
  chunks fall back to batch ASR.

Current operating point, measured by `murmurmark report corpus` on 2026-07-01:

- the current working corpus is `pilot_ready_with_review`: usable for pilot notes with explicit
  review, but not yet `medium_risk_ready`;
- `20` working sessions are in scope and `26` diagnostic sessions are excluded from operational
  readiness;
- `15/20` working sessions are `ready_for_notes`, five are `review_first`, and none are currently
  `do_not_use_without_manual_review`;
- selected evidence-backed notes carry about `0.81 min` of documented residual review burden;
- full transcript/export still has about `3.48 min` of explicit review surface;
- the remaining mandatory review queue is `5` actions / `8.78s` of raw audio;
- suggested review closure has no safe automatic keep/drop rows left; the remaining queue is treated
  as irreducible for the current local agents;
- readiness reconciliation is complete: MurmurMark no longer sends the user to empty review packs,
  and it keeps the remaining manual queue explicit. A narrow risky session is represented as formal
  residual risk instead of blocking the whole corpus as unusable.

Still future:

- near-realtime redesign beyond shadow: capture-safe segment production, stronger per-segment Echo
  Guard, resumable worker state, corpus parity gates and live-ASR cache reuse before it can shorten
  the authoritative post-meeting path;
- signed menubar app;
- remote speaker diarization inside `Colleagues`;
- heavy-local ASR stack with specialized models;
- strict glossary/domain correction beyond the current whisper prompt;
- polished generative notes, decisions and action extraction;
- docs/ticket export proposals with human review.

## Scope

Included:

- macOS local capture design;
- CLI-first capture workflow;
- optional future app UX concept;
- session package contract;
- local heavy transcription profile;
- long-meeting windowing design;
- evidence package for synthesis;
- privacy and retention policy;
- documentation sufficient for implementation.

Implemented in the current CLI spike:

- `doctor`, `list-apps`, `record`, `inspect`, `preprocess`, `export-audio` and `reconcile-transcript`;
- `process`, `status`, `report`, `audit`, `cleanup`, `repair`, `synthesize`, `notes`, `transcript`,
  `review`, `corpus`, `export` and `retention`;
- normal ScreenCaptureKit capture path for separate mic and remote audio;
- session package creation and inspection;
- Echo Guard diagnostics and derived cleanup engines;
- session-wide `local_fir` cleanup with the default `preserve_local` role policy;
- simple local `whisper.cpp` transcription with windowing, domain prompt, timeline repair and `shadow_v2` audit output;
- extractive local synthesis over transcript-derived JSON without LLM calls.

Excluded from v1 scope or not implemented yet:

- packaging a signed macOS app;
- training or fine-tuning ASR models;
- building a full docs/Jira/Confluence integration;
- legal review of recording consent rules.

## Functional Requirements

### Capture

- Select a target meeting app by bundle ID or process ID.
- Select a microphone without changing the system default input.
- Record target app audio and microphone audio at the same time.
- Write remote and mic audio into separate files.
- Keep the meeting app's input/output settings unchanged.
- Warn if mic or remote audio is silent for too long.
- Warn on clipping, writer backpressure, low disk, device change and target process restart.
- Support stop-and-delete.
- Emit `session.json` and `events.jsonl`.

### CLI

The CLI is the primary v1 product path. Any future GUI should reuse the same capture and pipeline
contracts instead of introducing a separate workflow.

The near-realtime CLI mode reuses these contracts as a future shadow branch, but the current
implementation is disabled by default. It may be used only for lab diagnostics with an explicit
unsafe environment flag until the async segment queue proves that it cannot affect raw
ScreenCaptureKit capture and live-vs-batch parity gates pass. The normal v1 product path is
batch-first: record raw `mic`/`remote`, then run `murmurmark process`.

Required commands:

```bash
murmurmark doctor
murmurmark list-apps
murmurmark list-audio-devices
murmurmark record --target-bundle com.microsoft.teams2 --mic default --out ./session
murmurmark inspect ./session
murmurmark process ./session
murmurmark status ./session
murmurmark review next ./session
murmurmark export ./session --format markdown --include-json
murmurmark retention plan ./session
```

### Optional Future App

The v1 product path is the CLI. A future menu bar or desktop app is useful only after the CLI
workflow is stable, and should expose the same commands and artifacts rather than creating a second
pipeline.

Possible controls:

- target app picker;
- microphone picker;
- storage/workspace selector;
- raw retention policy selector;
- permission status;
- remote and mic level meters;
- start, pause, mark, stop, stop-and-delete;
- visible recording state in the menu bar.

### Transcription

Future heavy-local validators or replacements may support:

- remote primary ASR through VibeVoice-ASR;
- remote diarization through pyannote Community-1;
- mic ASR through GigaAM-v3;
- strict domain correction through a local LLM adapter;
- quality report with uncertain regions;
- Markdown export as a view, not source of truth.

Current MVP transcription is narrower:

- one local `whisper.cpp` model for both tracks;
- `remote` is treated as authoritative `Colleagues`;
- `mic` is treated as candidate `Me`;
- no per-person diarization inside `Colleagues`;
- short overlapping ASR windows are reconciled into a global timeline;
- timeline repair and micro-ASR recover local islands when Whisper glues `Me` and `remote` turns together;
- `transcript.shadow_v2.md` is the best candidate only when `repair_comparison.json` passes.
- extractive notes can use the safe shadow dialogue when comparison gates pass.

The heavy-local stack above remains a future replacement or validation layer, not the current implementation.

Qwen3-ASR, Qwen3-ForcedAligner and similar models remain future validators unless implementation
effort is small after the primary CLI pipeline is stable.

### Synthesis

Synthesis must be separate from transcription.

Inputs:

- selected `clean_dialogue*.json` profile;
- `quality_report*.json`;
- `quality_verdict.json`;
- `corrections.jsonl` and audit JSON/JSONL evidence;
- domain pack;
- meeting context;
- optional retrieved docs/tickets/notes.

Outputs:

- meeting notes;
- decisions;
- action items;
- risks;
- open questions;
- docs export plan;
- optional patch plan for documentation repositories.

Every factual output must carry evidence IDs or be marked as requiring review.

## Non-Functional Requirements

- No telemetry by default.
- No network during capture by default.
- No raw audio in logs, crash reports or events.
- Raw audio retention must be explicit.
- The app must fail loudly on empty tracks.
- Capture must not depend on ASR availability.
- The session package must be processable later on another machine.
- The documentation must record licensing constraints for reference projects.

## Acceptance Criteria for First Implementation

Capture spike:

- records 30 minutes of remote app audio without changing system output;
- records selected microphone at the same time;
- writes non-empty `mic.caf` and `remote.caf`;
- emits inspectable `session.json`;
- leaves no orphan aggregate/tap resources after stop or crash recovery.

Pipeline spike:

- reads a session package;
- materializes ASR-ready working audio;
- runs Echo Guard diagnostics and selects `mic_for_asr.wav` through quality gates;
- produces current MVP transcript artifacts under `derived/transcript-simple/whisper-cpp/resolved/`;
- produces `transcript.md`, `transcript.shadow_v2.md`, `clean_dialogue*.json`, `role_decisions*.json`, `quality_report*.json` and audit artifacts;
- produces `quality_verdict.json`, `quality_verdict.md`, `notes.md`, `evidence_notes.json` and `review_items.jsonl` under `derived/synthesis-simple/extractive/`;
- keeps raw audio local;
- can delete raw audio after successful processing when configured.

Synthesis spike:

- produces notes with utterance IDs;
- rejects or flags unsupported decisions/actions;
- respects local-only policy.
