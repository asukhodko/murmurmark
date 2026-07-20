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
- near-realtime shadow branch uses a bounded committed-PCM queue after durable raw writes. The old
  inline `record --live-pipeline` path remains quarantined, while controlled
  `record --experiment live-shadow-v1` runs are allowed as evidence collection. Three fresh real
  sessions prove complete raw capture, preview before stop, terminal workers and zero final lag.
  Live output is still advisory and cannot replace the batch transcript.
- live-ASR cache bridge exists as a diagnostic/future acceleration layer; incompatible or unsafe
  chunks fall back to batch ASR.

Current operating point, 2026-07-20:

- stable batch capture and processing produce a transcript, verdict, evidence notes, review plan,
  guarded export and retention plan;
- `residual_local_recall_v1` is the selected authoritative profile after passing corpus, verdict
  and notes-evidence gates;
- Residual Audio Evidence Arbitration v1 classified all `66` audio-review rows / `196.920s` and
  completed with reproducible `DO_NOT_PROMOTE`; only `1` row / `0.640s` closed safely;
- Residual Local Recall Closure v1 classified all `13` rows / `48.073s` and safely closed `9` rows /
  `26.953s` without inserting speech; four ambiguous rows remain explicit;
- Speaker-Mode Transcript Quality Hardening v1 froze `18` acoustic and `22` profile sessions,
  proved three lossless retimes, one real double-talk row and one genuine `Me` row, then completed
  with `DO_NOT_PROMOTE` because duplicate/review reduction reached only `2.7%` / `7.9%`;
- the current goal is Mixed-Utterance Remote Span Separation v1: remove only a proven remote span
  while retaining unique or protected local prefixes and tails;
- committed-PCM Live Shadow is capture-safe and advisory. Live promotion remains blocked and does
  not hold the stable CLI path.

Dependent work is: one consolidated Echo Suppression Promotion contract, Evidence Notes And
Export v2 and release-quality CLI. Remote diarization,
speaker mapping and `transcript.rich.json` form a parallel future branch after base quality closure.
Heavy validators, LLM synthesis, reviewed external integrations and UI remain research or optional
work outside the critical path.

Detailed experiment metrics through 2026-07-19 are preserved under `docs/history/`.

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

The near-realtime CLI mode reuses these contracts as a shadow branch. The old `--live-pipeline`
transport is disabled, while `record --experiment live-shadow-v1` uses copied committed PCM after
durable raw writes and may be used for controlled real evidence. It never changes the normal v1
product path: record raw `mic`/`remote`, then run `murmurmark process`; live preview is advisory and
batch remains authoritative.

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
