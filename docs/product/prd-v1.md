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

Current operating point, 2026-07-18:

- the stable batch route is usable for working transcripts and evidence-backed notes, with guarded
  review/export when unresolved risks remain;
- the live corpus contains `33` sessions: `19` real meetings, `12` meaningful comparisons and `7`
  capture-safe candidate sessions;
- no live session passes every parity gate, so `shadow_only_do_not_promote` remains mandatory;
- Near-Realtime Live Parity Coverage v1 is complete as a transport/evidence milestone;
- Live Order and Role Reconciliation v1 is complete: all `23` auditable rows are classified, the
  `15` previous effective order blockers are now `0`, and no live turn mutation was required;
- Live Local Recall and Remote Leakage Hardening v1 is complete: all `118` local-recall rows have
  stable dispositions, the selected causal remote-energy shadow recovers `678.32s` aggregate
  missing `Me`, and all seven per-session no-regression gates pass;
- Causal Local-Island Micro-ASR v2 is complete: all `40` unresolved rows have stable outcomes, its
  explicit-only shadow reduces aggregate missing `Me` by another `255.77s`, remote-like `Me` stays
  at `108.42s`, effective order blockers stay at `0`, and all seven per-session gates pass;
- Causal Remote-Active Me Separation v1 is complete: `9/19` primary rows are accepted, all `16`
  mixed/double-talk cross-check rows are safely rejected, and aggregate missing `Me` falls by
  another `252.90s` without gate regressions;
- Recording-Time Causal Me Recovery Integration v1 is complete: the bounded runtime reproduces
  replay candidate sets and profile metrics `7/7`, while normal preview and batch stay unchanged;
- Live Recovery Runtime Efficiency and Real Evidence v1 is complete: per-stage watermarks,
  content-addressed DSP/candidate/micro-ASR caches and bounded invalidation pass the fixed corpus,
  while three fresh meaningful sessions prove recording-time execution, pre-stop candidates when
  evidence exists, no timeout/backpressure regression and zero final lag.
- Fast Authoritative Handoff v1 is complete. `process` publishes an atomic authoritative
  transcript/verdict/next checkpoint before optional `enrich`; three meaningful sessions pass with
  cold p50 `744.571s`, p95 `880.090s` and checkpoint reuse at most `0.032s`. Transcript fingerprints,
  selected profiles and quality metrics match sequential baselines; all source CAF hashes match.
  Live ASR remains reusable only under strict audio/model/prompt/window/provenance compatibility;
  historical incompatible cache falls back per track.
- Causal Double-Talk Me Recovery v1 is complete. Its immutable `16`-row / `65.07s` corpus has
  stable outcomes; `4` rows / `11.56s` are recovered, aggregate missing `Me` falls to `1639.73s`,
  remote-like `Me` remains `108.42s`, order blockers remain `0`, and every per-session/runtime/SHA
  gate passes. The result remains explicit-only and batch-authoritative.
- Causal Recovery Generalization and Promotion Readiness v1 is complete with `DO_NOT_PROMOTE`:
  `963` stable outcomes, `832` unchanged input hashes, three independent holdouts and zero accepted
  negative controls. Promotion is blocked by `268/783` expensive-stage coverage, three bounded
  runtime timeouts and one holdout order regression.
- The current bounded goal is Causal Candidate Coverage and Cheap Negative Prefilter v1. It must give
  all `783` eligible rows a cheap causal decision, keep expensive residual/Target-Me/micro-ASR work
  for plausible local speech, preserve the fixed `4` recoveries / `11.56s` and zero accepted
  negatives, and replay the same frozen holdouts without per-session quality regressions. No new
  meeting recording is required to start.

Still future:

- near-realtime promotion beyond shadow, conditional on full cheap-prefilter coverage, bounded
  runtime, zero accepted negatives and no per-session order/local-recall/remote-leak regression;
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
