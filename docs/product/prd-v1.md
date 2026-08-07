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

Current operating point, 2026-08-07:

- stable batch capture and processing produce a transcript, verdict, evidence notes, review plan,
  guarded export and retention plan;
- `local_speech_completion_v2` is selected for its frozen two-session scope after passing corpus,
  frozen-input, verdict and notes-evidence gates; `residual_local_recall_v1` remains fallback for
  sessions outside that promotion scope;
- Residual Audio Evidence Arbitration v1 classified all `66` audio-review rows / `196.920s` and
  completed with reproducible `DO_NOT_PROMOTE`; only `1` row / `0.640s` closed safely;
- Residual Local Recall Closure v1 classified all `13` rows / `48.073s` and safely closed `9` rows /
  `26.953s` without inserting speech; four ambiguous rows remain explicit;
- Speaker-Mode Transcript Quality Hardening v1 froze `18` acoustic and `22` profile sessions,
  proved three lossless retimes, one real double-talk row and one genuine `Me` row, then completed
  with `DO_NOT_PROMOTE` because duplicate/review reduction reached only `2.7%` / `7.9%`;
- Evidence-Backed Me Completion v2 classified six residual local-recall rows / `35.85s`, safely
  closed three / `22.4s`, repaired one damaged duplicate text fragment and left three / `13.45s`
  in explicit local-recall review;
- Mixed-Utterance Remote Span Separation v1 classified all `12` mixed `Me` rows / `54.940s` but
  completed with `DO_NOT_PROMOTE`: no split had enough independent local-island evidence;
- Echo Suppression Promotion v1 completed with reproducible `DO_NOT_PROMOTE`: its best candidate
  reduced bounded remote-risk by `68.2845%`, but passed only `3/5` speaker sessions and lost
  protected local speech on two counterexamples;
- Neural Residual Echo Suppression v1 completed with reproducible `DO_NOT_PROMOTE`: pinned
  Microsoft DEC removed all bounded remote-risk in the hard sessions, but protected-local recall
  fell to `45.45%`, chronology and double-talk recall to `0%`, and runtime exceeded the gate;
- Speaker-Preserving Echo Adaptation Corpus v1 completed with reproducible `DO_NOT_TRAIN`: privacy,
  session-disjoint splits and `192s` train / `96s` dev local-only coverage passed, but no
  remote-only interval passed the frozen confidence gate, synthetic pairing remained forbidden,
  hard-test double-talk stopped at `6s`, and no opening acknowledgement was independently confirmed;
- Controlled Echo Supervision Lab v1 completed with `READY_FOR_ADAPTATION`: five train, one dev and
  one hard-test capture passed, replay matches `1465/1465`, and the frozen corpus contains `1804s`
  train plus `352s` dev synthetic mixtures and `68s` hard-test double-talk;
- Speaker-Preserving Neural Echo v2 completed with guarded `PROMOTE`: candidate audio was selected
  for `5/12` sealed corpus sessions, removed `41.940s` and `90` remote-supported tokens, retained
  local tokens at `1.0`, and used exact fallback for the other `7/12`;
- Reference-Conditioned Target-Me Separation v1 completed with `DO_NOT_PROMOTE`: two train/dev
  attempts missed locked gates and the frozen corpus could not identify non-target local speech;
  hard-test stayed unopened and production v2 stayed byte-exact;
- Target-Me Identifiability Corpus v1 completed with `READY_FOR_TARGET_CONDITIONED_TRAINING`:
  `4/2/2` split-disjoint non-target speakers, `1200/300/300s` full mixtures, `490` items and
  `980` paired correct/wrong queries passed contamination, exact replay and publication gates;
- Reference-Conditioned Target-Me Separation v2 completed with `DO_NOT_PROMOTE`: its frozen
  paired-query candidate learned a `4.991 dB` correct-vs-wrong margin with `0%` collapse, but missed
  Target-Me, non-target and absent-query dev gates; hard and sealed data remained unopened and
  production v2 stayed byte-exact;
- Evidence Notes And Export v2 publishes one versioned handoff over selected transcript, verdict,
  unresolved review burden, evidence notes and guarded export readiness; its 110-session corpus
  has zero integrity, stale-manifest and deterministic-replay failures;
- Release-quality CLI packages the proven path with an explicit supported environment,
  deterministic artifact, idempotent transactional install/upgrade and packaged offline
  acceptance;
- Reliable Final Handoff v1 completes bounded cache/resume convergence, sparse candidate ASR reuse
  and actionable terminal review;
- Authoritative Incremental ASR and exact remote production are complete; remote-only work is
  quarantined after a measured `2.8651%..4.1040%` wall-time ceiling;
- Causal Canonical Mic ASR v1 completed with `DO_NOT_PROMOTE`: `0/147` candidate mic windows matched
  final canonical PCM because current Echo and branch selection have a session-end causal boundary;
- Remote Speaker Evidence Map v1 completed with promoted audit-only anonymous evidence over the
  authoritative remote track and no selected-text mutation;
- Anonymous Rich Transcript Handoff v1 and Reviewed Remote Speaker Naming v1 are promoted optional
  read surfaces; labels come only from an explicit fingerprint-bound session review;
- Pre-ASR Target-Me Isolation Limit v1 is complete. SepFormer preserved exact accounting and paired
  train assignment but failed Target-Me presence separation, ending in
  `DO_NOT_ADVANCE_STRONGER_SEPARATOR` before dev. Reviewed Speaker-Aware Meeting Memory v1 is
  promoted optional on 6/6 sessions. Evidence-Guarded Local Synthesis v1 completed with
  `DO_NOT_PROMOTE`; Evidence-Only Local Note Selection v1 is current;
- committed-PCM Live Shadow is capture-safe and advisory. Live promotion remains blocked and does
  not hold the stable CLI path.

The current technical North Star is to retain every independently confirmed local word while
removing recognizable authoritative remote content before primary ASR. `other_local` speech and
unexplained residual must stay explicit rather than being silently assigned to `Me` or deleted.
The personalized Echo selector activates only with compatible local enrollment, promotion evidence
and the v2.17-pinned transcriber/cache runtime; every unsupported acoustic mode, incompatibility or
regression uses exact `local_fir_role_masked`.
Any later separator remains isolated until a corpus-wide decision and cannot use exact remix or
audio quality alone as evidence of correct word attribution. Post-ASR duplicate cleanup receives no
promotion credit. Reopening the audio frontier requires an independently qualified abstaining
Target-Me presence detector. Free-text LLM synthesis remains unpromoted; ID-only evidence selection
is the current bounded research stage. External writes and UI remain optional work.

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
- no per-person labels in the selected plain `Colleagues` transcript; optional anonymous evidence
  remains separate;
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
