# Product Requirements: v1

v1 is the first usable local speaker-resolved transcription workflow, not a polished commercial
product.

It must prove safe capture, a durable session package, local transcription, evidence-backed speaker
attribution and an explicit deletion policy. Notes are optional derivatives.

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
- promoted speaker-resolved default with session-local anonymous remote speaker IDs, a clearly
  marked provisional read tier and an explicit exact aggregate fallback;
- quality verdicts and review items, plus optional local extractive notes;
- optional Markdown/Obsidian-style export bundles, JSON/audit artifacts for review, and raw
  retention plans;
- near-realtime shadow branch uses a bounded committed-PCM queue after durable raw writes. The old
  inline `record --live-pipeline` path remains quarantined, while controlled
  `record --experiment live-shadow-v1` runs are allowed as evidence collection. Three fresh real
  sessions prove complete raw capture, preview before stop, terminal workers and zero final lag.
  Live output is still advisory and cannot replace the batch transcript.
- live-ASR cache bridge exists as a diagnostic/future acceleration layer; incompatible or unsafe
  chunks fall back to batch ASR.

Current operating point, 2026-08-19:

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
  `DO_NOT_PROMOTE`; Evidence-Only Local Note Selection v1 remains an optional derivative;
- Remote Speaker Coverage v3 is promoted: `93.9312%` attributable remote speech,
  attributed-only B-cubed F1 `0.962171`, pairwise precision `0.961675`, 5/5 internal-boundary cases,
  exact v2-label and selected-word conservation plus aggregate fallback;
- Remote Speaker Residual Evidence v4 closed with `DO_NOT_PROMOTE`: 124 words / `83.640s` recovered,
  but `14.57%` word and `13.98%` second reductions missed both `20%` promotion gates;
- Speaker-Resolved Transcript Default v1 passed 6/6 frozen sessions. Handoff/export stay strict;
  ordinary reading adds a provisional cluster tier and explicit unknown, while exact aggregate
  remains available explicitly;
- Remote Speaker Attribution Error Decomposition v1 completed with
  `ADVANCE_STRONGER_SPEAKER_IDENTITY`: across 393 words and 64 boundaries, identity gain `0.351382`
  dominates segmentation `0.063882` and overlap/open-set `0.036364`; ECAPA subsequently passed
  synthetic hard-v4 but failed real-session promotion, and direct-truth adjudication kept Coverage v3;
- committed-PCM Live Shadow is capture-safe and advisory. Live promotion remains blocked and does
  not hold the stable CLI path.
- a fresh complete group-session revalidation confirmed durable two-track capture, exact ASR chunk
  completion, selected reviewed transcript and digitally closed review lanes. It also reproduced the
  remaining product limit: roughly one sixth of remote speech stayed explicit unknown and dense
  overlap still damaged words;
- review application now refreshes selected speaker evidence, discards stale cleanup-only harmful
  metrics for reviewed profiles and clears stale deferred-stage errors after successful recovery.

The current technical North Star is an authoritative transcript that preserves words, chronology
and roles, retains every independently confirmed local word, removes recognizable remote content
from the mic ASR input and attributes remote words to supported session-local anonymous speakers.
`other_local`, overlap and unexplained evidence must stay explicit rather than being silently
assigned or deleted.
The personalized Echo selector activates only with compatible local enrollment, promotion evidence
and the v2.17-pinned transcriber/cache runtime; every unsupported acoustic mode, incompatibility or
regression uses exact `local_fir_role_masked`.
Any later separator remains isolated until a corpus-wide decision and cannot use exact remix or
audio quality alone as evidence of correct word attribution. Post-ASR duplicate cleanup receives no
promotion credit. Reopening the audio frontier requires an independently qualified abstaining
Target-Me presence detector. Free-text LLM synthesis remains unpromoted; ID-only evidence selection
is an explicit opt-in view with exact source text. Speaker-Resolved Transcript Default v1 is
promoted. Lexical Accuracy Reference Corpus v1 closes `REFERENCE_INSUFFICIENT`: the exact generated
67-word subset has WER/CER `0`, while real-meeting correctness still lacks human-reviewed evidence.
The local `glossary.yaml` is currently a contract and private knowledge source, not a runtime ASR
input. Production keeps `prompt_file: null`: the default `--max-context 0` makes the present prompt
path ineffective, and a diagnostic A/B found no benefit from a broad static prompt. A compact
topic-specific prompt did improve the targeted terminology, so Session-Scoped Lexical Context v1 is
planned after Human-Reviewed Lexical Seed v1 and must pass multi-session no-regression gates.
Remote Speaker Residual Reference Corpus v1 closed `REFERENCE_INSUFFICIENT` after freezing 278 blind
items and all 53 WavLM proposals without direct truth. Controlled Remote Speaker Truth Lab v1 then
qualified the Coverage v3 control but rejected the WavLM candidate (`0.834325` B-cubed F1, two
open-set false attributions). Duration-Aware v2 then rejected word-level fusion on blind hard-v2:
known recall `0.551402`, boundary recall `0.321429`. Segment-Context v1 then failed blind hard-v3:
known recall `0.445087`, boundaries `0/20`, two open-set errors. Error Decomposition v1 then selected
speaker identity as the dominant axis: gain `0.351382` versus boundary `0.063882` and overlap/open-set
`0.036364`. Stronger Remote Speaker Identity Backend Qualification v1 then promoted ECAPA only as a
lab candidate after a one-shot hard-v4 result of B-cubed F1 `0.948042`, known recall `0.947368`,
pairwise precision `1.0` and zero open-set false attribution. The frozen real-session shadow later
closed with `DO_NOT_PROMOTE_REAL_IDENTITY`: 156/851 words and 211.100/598.240 seconds were proposed,
but the word ratio was `0.183314` and independent machine-reference precision `0.878788`. Shadow
Error Decomposition selected interval purification: 93/214 failures and `201.273504s`. Its one-shot
crop then closed `DO_NOT_ADVANCE`: 2 new words / `4.154556s` and one new reference error.
Session-Local Remote Speaker Enrollment Hardening v1 closed `DO_NOT_ADVANCE`: 11 new acceptances,
five removed controls and only 4/83 target failures recovered. Remote Speaker Direct Truth Seed v1
then froze 33 primary items / 116 words / `90.100820s`, 8 hidden repeats and 41 blind slots across
six sessions. Blind review completed 33/33 primary and 8/8 repeat answers: 8 attributed, 11 unknown,
4 mixed and 10 unusable, consistency `0.875`. Direct adjudication closed `KEEP_COVERAGE_V3`: three
correct gains came with two lost correct controls and 13 versus 8 fail-closed unsafe accepts. Remote
Speaker Enrollment Purity and Abstention Hardening v2 then closed `KEEP_COVERAGE_V3`: 7/14 profiles
qualified, zero identities were added and unsafe accepts returned to control 8. Homogeneous
enrollment, label-independent re-clustering, WeSpeaker and temporal AHC/VBx all closed without
promotion. Disjoint Truth v2 then completed 72 primary + 12 repeat decisions. Frozen ERes2NetV2
closed `KEEP_COVERAGE_V3`: 12/21 correct identities, precision `0.631579`, seven unsafe special
accepts and two lost truth-v1 controls. Cluster Purity Reference v1 then measured 10 reference
remote voices against four published acoustic clusters, weighted purity `0.898106` and minority
recall `0`, closing `ADVANCE_SEGMENTATION` and opening a bounded boundary experiment.
Frozen Boundary and Minority-Voice Segmentation v1 subsequently closed `KEEP_COVERAGE_V3`: real
boundary precision `0.044688`, speaker-count ratio `0.5`, minority recall `0.017161`, exact word
conservation and byte-exact replay. Current work rebaselines fresh speaker-resolved sessions before
choosing another model or heuristic.
Notes, external writes and UI remain optional.

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
- simple local `whisper.cpp` transcription with windowing, optional diagnostic prompt, timeline
  repair and `shadow_v2` audit output; production remains prompt-free until lexical gates pass;
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

Future heavy-local replacements and validators may support:

- remote primary ASR through VibeVoice-ASR;
- remote diarization through a pinned local backend such as pyannote Community-1;
- mic ASR through GigaAM-v3;
- strict domain correction through a local LLM adapter;
- quality report with uncertain regions;
- Markdown export as a view, not source of truth.

Current selected transcript is narrower:

- one local `whisper.cpp` model for both tracks;
- `remote` remains authoritative and the plain fallback is aggregate `Colleagues`;
- `mic` is treated as candidate `Me`;
- promoted Remote Speaker Coverage v3 provides `93.9312%` attributable remote speech, exact
  selected-word and v2-label conservation and explicit `unknown`;
- Speaker-Resolved Transcript Default v1 now selects that view for ordinary transcript/handoff/export
  only with current policy, implementation, corpus and session fingerprints; otherwise the exact
  aggregate transcript is returned;
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
