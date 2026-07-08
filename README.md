# MurmurMark

Local-first meeting transcription for sensitive work.

MurmurMark records a meeting into separate local `mic` and `remote` tracks, processes the session
locally, and produces a transcript, quality verdict, evidence-backed notes, export bundle and
retention plan.

The project is CLI-first. A future app can be useful, but the main product is a reliable command-line
pipeline with explicit evidence, review gates and local privacy controls.

## Mission

MurmurMark exists to turn sensitive working conversations into reliable local transcripts and
evidence-backed meeting memory without sending raw meeting audio to a cloud recorder.

It is meant for people who need to recover what was said, what was decided and what should happen
next, without supervising every processing stage. The system should never pretend that a risky
transcript is clean: unclear regions remain review items, and generated notes must point back to
utterance or audit evidence.

## Current Status

The CLI pipeline is usable for pilot notes on regular working meetings with a short explicit review
queue. It is not yet a fully unattended transcription appliance: guarded full-transcript export can
still require review, especially for order risks and remote leakage in `mic`.

Current corpus snapshot from `murmurmark next corpus --refresh` on 2026-07-02 after the broader
stronger-audio-judge pass:

- operational status: `pilot_ready_with_review`;
- working sessions in scope: `24`;
- diagnostic sessions excluded from readiness: `26`;
- session readiness: `15/24 ready_for_notes`, `9/24 review_first`, `0/24 do_not_use_without_manual_review`;
- selected notes review burden: `1.32 min`;
- full transcript/export review surface: `4.09 min`;
- mandatory review queue: `9` actions / `12` rows;
- low-materiality rows outside mandatory review: `28` rows / `70.95s`;
- corpus gate review limits: `15` actions / `25` rows;
- irreducible review gate: `irreducible_manual_review_queue_present`;
- safe suggested rows still pending: `0`.

This does not mean “zero review”. It means the current operational corpus has no hidden blocker:
safe local evidence has been applied where available, and the remaining queue is short, explicit and
not safely closable by the current local agents. The stronger local audio judge can now close a
narrow `probable_timing_overlap` class as `keep_me` only when group-overlap evidence shows strong
local support and weak remote/leak support; real double-talk and conflicting clips stay in review.
One `check_transcript_order` overlap is already closed this way through the normal suggested-review
apply path.
Single-word `так` tails without action/decision/risk markers are counted as low-materiality rather
than mandatory review.
Short exact partial duplicates with no unique `Me` content are also kept out of the mandatory queue.
The one risky session is represented as formal residual risk, not as unattended export readiness.
`finish` and `export` still block full transcript
bundles when transcript-only blockers remain.
The operational report includes `manual_tail_explanation`, which groups the remaining queue by
reason: weak/conflicting audio, ambiguous chronology, possible unique `Me` content inside remote
leak, and local-recall evidence that is still too weak for an automatic call.

Current reliability focus:
[Reliable Transcription Route](docs/project/reliable-transcription-route.md). Outcome and handoff
UX v1 are now in place: a processed session reports `ready_for_notes`, `review_first` or `blocked`,
with selected profile, verdict, review burden, export blockers and one next command.
Chunked/Resumable Processing v1 is now complete at the ASR layer: long `windowed` whisper.cpp runs
write validated chunk reports, interrupted `process` runs can resume from verified chunks, and
corpus gates treat failed chunk rebuilds as hard failures. Batch processing remains authoritative.
The live/near-realtime branch is still quarantined as a source of truth. Its segment writer now runs
behind an async bounded queue instead of doing derived live writes in the ScreenCaptureKit callback,
and the full fail-open proof allows controlled Live Evidence runs on real meetings, but live
promotion still requires broader passing real coverage. The latest audio milestone,
ASR-Positive Echo Candidate Hardening v1, remains important but shadow-only:
`coverage_v2_remote_gate_local_fir` improved `5/6` candidate-corpus sessions without local-recall
regression, yet `local_fir` is still the default ASR input until promotion gates are defined and
passed on a broader corpus.

## What Works Now

- Two-track macOS capture through ScreenCaptureKit: local microphone and selected system/app audio.
- Durable session package with raw CAF tracks, `session.json`, events and derived artifacts.
- Echo Guard preprocessing with local FIR cleanup and a preserve-local policy.
- Local `whisper.cpp` transcription with Russian support, prompt/domain hints, timeline repair and
  start-of-call repair.
- Audit layers for order, local recall, group overlaps, audio review and optional stronger local
  audio judge.
- Remote-forbidden evidence audit: shadow rows with remote/mic tokens, speaker state, transcript
  links, confidence, guarded seconds and corpus-level explanation.
- Conservative cleanup and repair profiles that write separate transcript candidates instead of
  mutating raw capture or baseline output.
- Deterministic extractive notes with quality verdicts and evidence IDs.
- Review lane packs, suggested answers, review apply flow and corpus readiness reports.
- Markdown/Obsidian-style export bundles and retention planning.
- Export Bundle Quality v1: `finish` writes a readable handoff with "Can I use this?", review
  burden, evidence-backed notes, transcript IDs and retention/privacy next steps.
- Local release bundle, self-test, acceptance gate and open-source readiness check.
- Recording reliability: duration/SIGINT complete normally, SIGTERM/SIGHUP/unrecovered capture stops
  become explicit partial sessions, severe wall-clock/audio-duration gaps are blocked as partial
  captures, all-silent mic+remote sessions are blocked before ASR, and `doctor` catches missing
  shareable displays before recording.

## Reliability Direction

The user-facing target is:

```text
record meeting -> process unattended -> get transcript, notes, verdict and exact next action
```

The result must be one of:

- `ready_for_notes`: notes are usable for ordinary follow-up;
- `review_first`: the transcript is useful, but a short explicit queue must be checked before
  medium-risk use or full export;
- `blocked`: the session is partial, missing required artifacts, or too risky to use without manual
  review.

This route is tracked in [Reliable Transcription Route](docs/project/reliable-transcription-route.md)
and in the CLI roadmap. The main work is to reduce review at the root, keep long processing
resumable and observable, and make every blocker explicit instead of asking the user to inspect
derived files by hand.

`Outcome Contract v1` is now the first handoff layer after readiness:

```text
derived/outcome/outcome.json
derived/outcome/outcome.md
derived/outcome/review_plan.json
derived/outcome/next_command.txt
derived/run/pipeline_run.json
```

`murmurmark process` writes these files at the end of the run, and `murmurmark report` /
`murmurmark outcome --refresh` can regenerate them without rerunning ASR. They are the compact
answer to: "Can I use this session now, what blocks export, and what exact command should I run
next?"

`murmurmark next` now uses `outcome.json` as the primary source when it exists. The run manifest
records step status, expected output checkpoints, missing output count, stuck-state summary and the
session-level resume command.

Reliable Processing UX v1 tightens this route: `outcome.json` now includes a compact `summary`
with `can_read_notes`, `can_export`, export blockers, transcript/notes/verdict paths, review burden,
first review lane and the next command, and `murmurmark outcome` prints that summary directly. Long
`process` stages also print a heartbeat with the stage reason, checkpoint count and resume command,
so a slow ASR stage does not look like a silent hang. While the run is active, the same live state is
written to `derived/pipeline-run/pipeline_run_state.json`; `murmurmark status SESSION` and
`murmurmark next SESSION` prefer that state over stale readiness and show the active step, ASR chunk
progress and resume command. If you stop `process` with `Ctrl-C`, MurmurMark terminates the current
child step, writes `pipeline_run_report.json` with `status: interrupted`, refreshes `outcome`, and
points back to the same `murmurmark process SESSION` command.

Chunked/Resumable Processing v1 is complete at the ASR layer. Default `windowed` whisper.cpp runs now
write per-window cache reports under `derived/transcript-simple/whisper-cpp/raw/chunks/<track>/`.
If the combined raw JSON is missing but the window cache is still compatible, the next
`murmurmark process SESSION` rebuilds the combined ASR JSON from cached windows instead of
rerunning every window. `process` also runs `check-asr-chunk-cache.py` and writes
`raw/chunk_rebuild_check.json`; the pipeline stops if raw ASR cannot be rebuilt from chunks. When
near-realtime mode produced compatible live ASR chunks, the live-cache bridge materializes the same
raw/chunks structure and still requires the rebuild check to pass. The batch transcript remains
authoritative. During long ASR, heartbeat lines include completed and remaining ASR audio seconds,
chunk counts, reuse counts and a current-run ETA when enough progress exists. Current corpus evidence
covers `14/50` sessions with `0` failed chunk rebuilds and `146/146` completed ASR chunks. A real
29-minute session was interrupted during `murmurmark process`, resumed with the same command, reused
`14` cached ASR chunks and passed the rebuild gate.

Live/near-realtime cache promotion is gated separately and is currently quarantined. Existing
diagnostic live sessions are negative evidence for the old inline writer: it could starve
ScreenCaptureKit audio delivery and leave raw capture mostly silent. A later controlled-real run also
showed sparse raw capture, so new real Live Evidence recording is disabled again. Lab pilots and
existing-session analysis remain useful, but valuable meetings must use the non-live production
route until capture isolation is proven again.

## What Is Still Out Of Scope

- Per-person diarization inside `Colleagues`.
- Product-complete echo removal is not yet a default capability. The current `local_fir` path
  reduces leakage and protects local speech; the active research path is a shadow `offline_aec_v2`
  lab tracked in [Complete Echo Removal Research](docs/research/2026-06-30-complete-echo-removal.md).
- Fully automatic zero-review summaries.
- Cloud ASR or cloud LLM by default.
- Jira/docs/Confluence writes without human review.
- Signed macOS app or menu bar UI.

## Install For Local Development

Prerequisites:

- macOS with Screen and System Audio Recording permission for the terminal or Codex app;
- Swift toolchain;
- Homebrew packages used by the pipeline, especially `ffmpeg`, `whisper-cpp`, `jq`;
- Python virtual environment with the project dependencies;
- local `whisper.cpp` model, currently `ggml-large-v3-q5_0.bin`.

The normal local install is:

```bash
cd murmurmark
source .venv/bin/activate

scripts/install-local.sh
export PATH="$HOME/.local/bin:$PATH"

murmurmark doctor
murmurmark self-test
murmurmark acceptance --skip-release
```

`scripts/install-local.sh` builds the Swift CLI and installs the `murmurmark` wrapper. If you skip
that step, use the development binary directly after `swift build`, for example
`.build/debug/murmurmark doctor`.

Optional local second-listener support:

```bash
source .venv/bin/activate
.venv/bin/pip install faster-whisper ctranslate2

export MURMURMARK_FASTER_WHISPER_MODEL="$HOME/.local/share/murmurmark/models/faster-whisper/large-v3"
murmurmark doctor
```

The stronger audio judge is optional. If the model is missing, the main pipeline should continue and
print a warning.

## Record A New Meeting

Start recording:

```bash
cd murmurmark
source .venv/bin/activate
export PATH="$HOME/.local/bin:$PATH"

murmurmark doctor
murmurmark record --target-bundle system
```

For real meetings, run recording from a logged-in desktop session with an awake display. If
`murmurmark doctor --strict` reports `shareable displays: 0`, ScreenCaptureKit cannot currently see
a capture source even if macOS permissions are granted. Wake the display and re-check before
recording:

```bash
caffeinate -u -t 5
murmurmark doctor --strict
```

Stop recording with `Ctrl-C`. Without `--duration`, recording is expected to continue until you stop
it. On success the CLI prints:

```bash
SESSION="sessions/<timestamp>"
recommended_next: murmurmark process sessions/<timestamp>
```

If capture stops before `Ctrl-C`, if written CAF tracks cover far less time than the wall-clock
recording, if both mic and remote tracks are effectively silent, or if a long recording contains only
a tiny amount of active audio, MurmurMark finalizes or blocks the session instead of pretending it is
complete. In that case `record`, `status`, `next` and `process` point to `murmurmark inspect ...`;
normal processing is blocked unless you explicitly pass `--allow-partial` for debugging.

Run only one `murmurmark record` process at a time. ScreenCaptureKit is not treated as a reliable
multi-client capture source for MurmurMark: starting a safe recording and a live recording in
parallel can leave both sessions unfinalized or empty. The CLI keeps a recording lock and rejects a
second concurrent `record` before it creates a broken session. For valuable meetings, do not attach
the live sidecar yet. The supported production path is one durable raw capture followed by batch
processing.

ScreenCaptureKit may skip audio buffers during silence or source inactivity. MurmurMark preserves
the meeting timeline in raw CAF files by inserting silence for timestamp gaps instead of compressing
the recording to only the buffers that arrived. If no ScreenCaptureKit audio samples arrive at the
start of recording, MurmurMark now tries short restarts and then fails fast as a partial capture
instead of letting a whole meeting become an empty transcript. If a long recording has only sparse
audio bursts, `process` blocks with `sparse_capture`; this catches cases where timestamp padding kept
the CAF duration correct but ScreenCaptureKit delivered almost no useful audio.

Then run:

```bash
murmurmark process latest
murmurmark next latest
murmurmark status latest
murmurmark outcome latest

murmurmark notes latest --kind verdict
murmurmark notes latest
murmurmark transcript latest
murmurmark finish latest
```

If `next` or `status` prints a review command, follow that command before relying on the result for
medium-risk work.

Experimental near-realtime work is now split into two paths.

The old inline live path is unsafe/lab-only:

```bash
MURMURMARK_ENABLE_UNSAFE_LIVE_PIPELINE=1 \
  murmurmark record --target-bundle system --live-pipeline
```

Do not hand-run `--live-pipeline` for a meeting. It previously correlated with sparse raw CAF
captures and remains quarantined.

The new controlled experiment path keeps the normal raw writer as the only source of truth:

```bash
murmurmark record --target-bundle system --experiment live-shadow-v1
murmurmark process latest
murmurmark experiment status latest
murmurmark experiment report latest
murmurmark experiment compare latest --experiment live-shadow-v1
```

The shape is:

```text
capture -> durable raw writer -> stable session
                    |
                    +-> best-effort sidecar queue -> live draft
```

In this mode the capture callback writes raw CAF first. After a successful raw write, a lightweight
commit tracker records segment boundaries in
`derived/experiments/live-shadow-v1/raw_segment_commits.jsonl`. A separate sidecar worker reads that
commit log, waits for paired `mic`/`remote` intervals and materializes WAV segments under
`derived/experiments/live-shadow-v1/audio/`. If open CAF files are not readable yet, the sidecar
waits; if it falls behind, it disables only the experiment. Raw `audio/mic/*.caf` and
`audio/remote/*.caf` remain the authoritative recording and `murmurmark process` remains the
authoritative transcript path.

`--experiment live-shadow-v1` is still controlled evidence, not promotion. Use plain
`murmurmark record --target-bundle system` for valuable meetings until soak and parity gates pass.
The experiment is useful to prove whether sidecar draft work can run beside stable capture without
damaging raw audio. See [Experimental sidecar architecture](docs/architecture/experimental-sidecar.md).

The sidecar contract lives under:

```text
derived/experiments/live-shadow-v1/
  experiment_manifest.json
  state.json
  events.jsonl
  report.json
  report.md
```

`derived/live/` remains a compatibility alias for existing draft/chunk tools. The contract is the
machine-readable source for sidecar status: whether the experiment started, how many raw commits and
sidecar seconds exist, whether backpressure disabled the sidecar, whether raw capture was affected,
and which command recovers processing from the existing raw CAF files. Inspect it with:

```bash
murmurmark experiment status latest
murmurmark experiment report latest
murmurmark experiment compare latest --experiment live-shadow-v1
```

`experiment compare` also resumes missing sidecar materialization from `raw_segment_commits.jsonl`.
This is intentional: recording should stop quickly, while slower live draft recovery can happen in
the explicit experiment step.

For lab evidence, prefer the raw-commit experiment over the unsafe legacy live path:

```bash
murmurmark record --target-bundle system --duration 120 --experiment live-shadow-v1
murmurmark process latest
murmurmark experiment compare latest --experiment live-shadow-v1
```

`murmurmark live pilot --controlled-real` still refuses to start a new real recording unless the
explicit unsafe escape hatch is passed. That command is kept for old evidence and compatibility, not
as the recommended path.

If a previous experiment already exists, inspect it without starting another recording:

```bash
SESSION="sessions/<session-id>"
murmurmark experiment status "$SESSION"
murmurmark experiment report "$SESSION"
murmurmark experiment compare "$SESSION" --experiment live-shadow-v1
```

The old unsafe escape hatch remains intentionally noisy:

```bash
murmurmark live pilot --controlled-real --skip-safety-gate --allow-unsafe-controlled-real-recording
```

Use it only for a meeting you are prepared to lose.

If recording finished but post-stop processing was interrupted, resume the same evidence collection
without starting another recording:

```bash
SESSION="sessions/<session-id>"
murmurmark process "$SESSION"
murmurmark experiment status "$SESSION"
murmurmark experiment compare "$SESSION" --experiment live-shadow-v1
```

The live worker is still shadow-grade, but it now has three lightweight protections before writing
draft text: per-chunk mic echo cleanup, a role gate that suppresses mic text when it duplicates the
same chunk's remote text, and a boundary gate that suppresses adjacent chunk repeats. These
protections reduce obvious live draft mistakes; they do not make live output authoritative.
After batch processing, `derived/live/live_parity_session_report.md` explains whether that one
session can count as a passing live comparison and lists the exact non-passing gates.

During final reconcile, `process` also runs a live-ASR cache bridge:

```text
derived/live/live_asr_cache_report.json
```

In v1 this bridge is strict. It reuses live chunks only when model, language, audio prep and chunk
geometry are batch-compatible, including overlap context compatible with batch ASR windows. When
eligible, it writes both top-level raw ASR JSON and materialized `raw/chunks/<track>/` reports; the
next `check-asr-chunk-cache.py --require-chunks` must prove rebuild parity. Otherwise it writes
`status: not_eligible` and the pipeline falls back to normal batch ASR.

To inspect live parity over a local corpus:

```bash
murmurmark corpus live all --refresh
less sessions/_reports/live-pipeline/live_corpus_gates_report.md
jq '.real_blocker_triage_summary' sessions/_reports/live-pipeline/live_corpus_gates_report.json
```

To test whether simple live-accessible suppressed-mic thresholds can recover lost `Me` speech:

```bash
.venv/bin/python scripts/report-suppressed-mic-policy-lab.py
.venv/bin/python scripts/report-suppressed-mic-policy-lab.py \
  --scope real \
  --out sessions/_reports/live-pipeline/suppressed_mic_policy_lab.real.json
```

The lab uses batch labels only for evaluation. It does not edit live drafts, batch transcripts or
promotion gates. Current evidence says simple audio/text thresholds are not enough as the main
local-recall fix: they either recover only a small safe slice or quickly reintroduce remote speech as
`Me`. Treat useful rules as shadow evidence and keep the next implementation focused on stronger
local-speaker or remote-forbidden evidence.

To test whether already published live `Me` turns are enough to build a causal Target-Me voiceprint:

```bash
.venv/bin/python scripts/report-live-target-me-enrollment-lab.py --method resemblyzer_dvector
.venv/bin/python scripts/report-live-target-me-enrollment-lab.py \
  --method resemblyzer_dvector \
  --scope real \
  --out sessions/_reports/live-pipeline/live_target_me_enrollment_lab.real.json
```

This lab also uses batch labels only for scoring. Current evidence says same-session live enrollment
is not enough yet: the capture-safe candidate scope has no usable positive live `Me` enrollment, and
the full real scope recovers only a tiny causal slice. The next design needs enrollment fallback,
warmup/calibration, or another local-speaker signal before Target-Me can be relied on in live mode.

To test whether a historical persistent local-speaker profile helps suppressed-mic rescue:

```bash
.venv/bin/python scripts/report-persistent-target-me-profile-lab.py \
  --method resemblyzer_dvector \
  --max-enrollment-segments 40 \
  --max-negative-enrollment-segments 40
.venv/bin/python scripts/report-persistent-target-me-profile-lab.py \
  --method resemblyzer_dvector \
  --scope real \
  --max-enrollment-segments 40 \
  --max-negative-enrollment-segments 40 \
  --out sessions/_reports/live-pipeline/persistent_target_me_profile_lab.real.json
```

This lab is also offline scoring only. It builds a Target-Me voice profile from earlier processed
sessions, tests it against suppressed live mic segments and writes ignored reports under
`sessions/_reports/live-pipeline/`. Current evidence says a persistent profile is useful as
supporting evidence, but not safe enough as the main rescue path: in the full real scope it recovers
`75.72s` local/mixed speech under the conservative remote guard, but still selects `8.64s`
remote-risk speech; in the capture-safe candidate scope it recovers `0.00s`. The next design still
needs stronger remote-forbidden evidence or a stricter online role gate before live promotion can be
considered.

To test composite gates that combine audio/text, session-local Target-Me and historical Target-Me
evidence:

```bash
.venv/bin/python scripts/report-suppressed-mic-composite-gate-lab.py
.venv/bin/python scripts/report-suppressed-mic-composite-gate-lab.py \
  --scope real \
  --out sessions/_reports/live-pipeline/suppressed_mic_composite_gate_lab.real.json
```

This is also a lab-only report. Current evidence narrows the path: in the full real scope,
`dual_target_remote_guard_v1` recovers `47.70s` local/mixed speech with `0.00s` remote-risk, and
`target_me_remote_guard_v1` recovers `116.10s` local/mixed speech with `2.44s` remote-risk. In the
stricter capture-safe candidate scope, composite gates recover `0.00s`. So composite evidence can be
a small shadow rescue, but it still does not close the live local-recall gap.

`compare-live-batch.py` now materializes that small zero-risk slice as
`derived/live/target-me-shadow/online_suppressed_mic_dual_target_remote_guard_v1/draft.{json,md}`.
The profile is still diagnostic-only: current real corpus adds `47.70s`, leaves `380.17s`
missing-Me, leaves existing live remote leak at `15.96s`, and keeps promotion blocked.

It also materializes an online text-overlap filter for already-published live `Me` turns:
`online_live_me_remote_overlap_filter_plus_dual_target_remote_guard_v1` removes `15.96s` of
remote-like live `Me`, keeps measured remote leak at `0.00s`, and preserves the same `47.70s`
suppressed-mic rescue. This closes the current remote-leak symptom in shadow, but still leaves
`380.17s` missing-Me and `4` contentful order mismatches, so live promotion remains blocked.

The current best live-implementable shadow is
`online_live_me_remote_overlap_filter_plus_target_me_timeline_safe_audio_safe_union_v1`: it combines
the online remote-overlap cleanup, timeline-safe Target-Me rescue and the `audio_safe_union_v1`
suppressed-mic slice. Current real corpus result: `301.96s` missing-Me, `0.00s` measured remote
leak, `4` contentful order mismatches and `41` non-passing gates. This is the strongest non-oracle
candidate so far, but still not promotable. Its remaining missing-Me splits into `174.33s` visible
in suppressed mic with broader Target-Me evidence, `90.42s` visible without Target-Me evidence, and
`37.21s` not visible in suppressed mic. A less strict remote-guard profile reaches `276.93s`
missing-Me, but raises contentful order mismatches to `7`, so timeline safety is still buying real
protection. The timeline-safe Target-Me policy itself rejects only `18.30s` of candidates, all due
to contentful order risk and `0.00s` due to remote leak, so the larger remaining gap is mostly
speaker-evidence weakness rather than a simple timeline-safe threshold problem.

To inspect whether suppressed live mic segments contain your voice:

```bash
jq -r '.real_blocker_triage_summary.by_category.live_local_recall_gap.sessions[]?' \
  sessions/_reports/live-pipeline/live_corpus_gates_report.json |
  sed 's#^#sessions/#' |
  xargs .venv/bin/python scripts/audit-live-local-recall-target-me.py \
    --method resemblyzer_dvector \
    --max-items 80 \
    --no-progress

less sessions/_reports/live-local-recall-target-me/live_local_recall_target_me_corpus_report.md
```

The expected v1 result is still `shadow_only_do_not_promote`: the report should explain which gates
are evaluated and which remain blockers. Current live comparison checks the live draft against the
authoritative batch transcript for capture safety, order mismatch, missing `Me` speech, suspected
remote-in-`Me` leakage, selected batch review burden, notes readiness, adjacent chunk duplicates and
source-level `live_boundary_gate` suppression. Boundary suppression is split into two classes:
resolved suppression, where the suppressed words are fully covered by the previous emitted chunk, and
unresolved suppression, where unique words may have been lost. Only unresolved boundary suppression
blocks the chunk-boundary gate.
Passing those checks is still not promotion. With live quarantined, use this report only to inspect old diagnostic
evidence and gate failures. The Markdown report has a `Gate Issues` section with the concrete
session/gate/reason rows that currently prevent a passing comparison. Promotion checks use
`real_parity_dimensions`: only date-named real meeting sessions count there. `_debug_*`,
`live-pilot-*` and other lab sessions stay visible as diagnostic evidence, but they cannot satisfy
real coverage. Draft text recall is tracked separately from required artifacts: a session can have
all files present and still fail because the live draft text does not match the authoritative batch
transcript.
Use `--refresh` after live gate logic changes or after processing a session: it reruns
`compare-live-batch.py` from existing derived live/batch artifacts before aggregation. It does not
touch raw capture or the authoritative batch transcript.
When the capture fail-open proof has passed, the same report also writes
`capture_safe_candidate_scope` and `real_capture_safe_candidate_parity_dimensions`. This narrower
view counts only real live sessions that were meaningfully compared, passed capture safety, and have
the required live/batch artifacts. It is useful for seeing the remaining parity blockers without
mixing in old broken-capture evidence. It still does not permit promotion or normal production
live use; batch remains authoritative. Older reports may still expose
`controlled_real_live_pilot_allowed`, but the runner now refuses to start a new real live recording
without `--allow-unsafe-controlled-real-recording`. Treat that flag as a lab-only escape hatch, not a
meeting command.
The report also has `real_blocker_triage_summary` and a `Real Blocker Triage` Markdown section. Use
that first when deciding what to do next: it separates batch review/readiness debt, missing artifacts,
capture safety risks, local recall gaps, remote leakage and live draft drift. Triage is explanatory
only; it does not permit promotion or normal production live use.
For the active near-realtime goal, `objective_audit` is the quickest machine-readable summary: it
states whether real live sessions exist, whether they were compared with batch, whether required
dimensions are covered, which dimensions still block promotion, and whether batch remains
authoritative. Its safe current state is `ready_for_live_promotion: false` and
`new_real_live_collection_allowed: false`; `controlled_real_live_pilot_allowed` is a narrower
evidence-collection flag and must not be read as promotion.
It also reads the capture regression proof at
`sessions/_reports/capture-regression/capture_regression_check.json`. A normal static run of
`scripts/check-capture-regressions.sh` is useful, but it can only create `static_only` proof by
itself. If a previous full proof already exists, static checks preserve it instead of downgrading the
operator state. Real live collection still requires the full local proof at least once:

```bash
MURMURMARK_RUN_LIVE_CAPTURE_TEST=1 scripts/check-capture-regressions.sh
```

Until that report says `capture_safe_proof.status == "full_fail_open_proof_passed"`, live stays
quarantined even if old live-vs-batch comparisons look clean. If the full proof command is run and
fails in the current desktop session, the report should say `status: failed`; a static check may only
preserve an already-passed proof and records that fact in `preserved_from_previous_report`.
When `capture_safety` is among blocking dimensions and the full proof is missing,
`objective_audit.next_focus` must point to `capture_safe_redesign_before_more_live_coverage`.
After the proof passes and the capture-safe candidate slice has no blockers, `next_focus` may point
to `collect_controlled_capture_safe_live_pilot`; this still keeps promotion blocked and batch
authoritative.
While live is quarantined, `recommended_next` and `next:` point to triage, inspection or a controlled
pilot preflight. They must not suggest raw `record --live-pipeline` as the next action.

`murmurmark live gate` is the strict promotion gate for the current near-realtime objective. It
refreshes the same corpus report and exits non-zero until the required real live-vs-batch evidence is
complete:

```bash
murmurmark live gate
```

Under the hood it runs the strict target form:

```bash
murmurmark corpus live all --refresh \
  --min-live-sessions 3 \
  --min-compared-sessions 3 \
  --min-meaningful-compared-sessions 3 \
  --min-passing-compared-sessions 3 \
  --max-order-mismatches 0 \
  --max-missing-me-sec 0 \
  --max-remote-in-me-sec 0 \
  --max-boundary-duplicates 0 \
  --require-passing-gates \
  --fail-on-promotion
```

This gate is expected to fail until enough safe live evidence passes all parity checks. Do not try to
satisfy it by hand-running `record --live-pipeline`. New controlled evidence should use
`record --experiment live-shadow-v1`; batch output remains authoritative until this gate and the
surrounding promotion policy pass.

## Process An Existing Session

Use this when the raw recording already exists:

```bash
cd murmurmark
source .venv/bin/activate
export PATH="$HOME/.local/bin:$PATH"

SESSION=./sessions/<session-id>

murmurmark process "$SESSION"
murmurmark next "$SESSION"
murmurmark status "$SESSION"
murmurmark outcome "$SESSION"

murmurmark notes "$SESSION" --kind verdict
murmurmark notes "$SESSION"
murmurmark transcript "$SESSION"
murmurmark finish "$SESSION"
```

Force ASR only when you intentionally want to regenerate the expensive transcription layer:

```bash
murmurmark process "$SESSION" --force-asr
```

For a dry run:

```bash
murmurmark process "$SESSION" --plan-only
```

For an interrupted or known-partial capture, `murmurmark status "$SESSION"` and
`murmurmark next "$SESSION"` point to `inspect` first. Processing is blocked by default. Use
`--allow-partial` only for debugging.

## Review Flow

The handoff rule is simple: when a command ends with `next: ...`, run that command next.

Common review commands:

```bash
murmurmark next latest
murmurmark review next latest

# First close only high-confidence generated suggestions, then inspect the exact manual remainder:
murmurmark review suggested latest
murmurmark review suggested apply latest

# If manual review is still required, listen/edit the generated answer sheet, then:
murmurmark review first-lane --session latest
murmurmark review lane apply first --session latest
murmurmark review apply --session latest
murmurmark status latest
murmurmark report corpus
```

If `review next` prints `review_actions: 0` and `review_handoff: no_actionable_review_rows`, do not
build or listen to another lane pack. The queue is exhausted; inspect the printed readiness/status
documents or improve the cleanup algorithm.

For the current corpus queue:

```bash
murmurmark next corpus --refresh

# With no actionable review rows, the current focus is usually:
murmurmark status sessions/<session-id>
murmurmark report sessions/<session-id>
```

`review suggested` builds all lane packs, refreshes lane suggestions from cached local evidence,
dry-runs generated suggested answers and prints a `suggested_closure` block: before/after manual
rows, generated/actionable/needs-review counts, a conservative readiness projection, rows that can be
closed without listening and the exact remaining manual queue by lane. `review suggested apply`
writes only those safe reviewed rows, preserves previously closed rows, keeps dots as the manual
queue, refreshes `reviewed_v1` readiness when anything was closed and still blocks export when risk
remains. If nothing is safe to close, it writes no new decisions and points to the first remaining
manual lane. Run `murmurmark report corpus` after that when you want the corpus readiness delta. The
review loop writes decisions into separate reviewed profiles. It should not rewrite raw audio or hide
unresolved risk.

After a `review suggested` preview exists, `murmurmark outcome`, `murmurmark next` and `status` prefer
`murmurmark review suggested apply ...` whenever safe rows can be closed. Manual lane listening comes
after that, and should cover only the remaining tail.

By default `review suggested` does not start a long new faster-whisper decode. It uses existing
`faster_whisper_judge.jsonl` rows and current lane packs. To deliberately compute a small targeted
batch during suggested review:

```bash
MURMURMARK_TARGETED_JUDGE_COMPUTE=1 \
MURMURMARK_TARGETED_JUDGE_MAX_COMPUTED=4 \
murmurmark review suggested apply latest
```

## Export And Retention

`finish` is the normal end of the CLI flow. It refreshes readiness, attempts the guarded export,
includes JSON evidence by default, and then writes retention and provider-payload recommendations when
export succeeds. It never deletes raw audio.

For Markdown exports, start with `index.md`. It answers "Can I use this?", shows the selected
transcript profile, verdict and review burden, links to notes/transcript/evidence, and prints the
next retention command. `quality_verdict.md` is the human trust report, `notes.md` is the extractive
working summary, and `transcript.md` is the full text with utterance IDs and review flags.

Obsidian format writes one self-contained frontmatter note with the same sections. It is safe to
archive locally: raw audio is not copied into the export bundle.

```bash
murmurmark finish latest
murmurmark finish "$SESSION" --format obsidian
```

If the session is not exportable yet, `finish` writes the blocked export report and ends with the next
review or processing command. For lower-level debugging you can still run export and retention
directly. Export is local and refreshes the outcome contract before it runs; it blocks unless
`outcome.json` says `ready_for_notes` with `export_status: allowed`. Use `--force` only for deliberate
debugging.

```bash
murmurmark export latest --format markdown --include-json
murmurmark export latest --format obsidian --include-json
```

Retention planning never deletes raw audio:

```bash
murmurmark retention plan latest
murmurmark retention payload latest
```

Raw deletion requires an explicit apply command, a policy that allows it, a successful export
manifest and `--confirm-delete-raw`:

```bash
murmurmark retention apply latest --confirm-delete-raw
```

## Corpus And Quality Loop

The corpus loop is the main guard against regressions:

```bash
murmurmark corpus process all --per-label 16 --max-items 160
murmurmark corpus gate
murmurmark next corpus --refresh
murmurmark report corpus
```

`corpus gate` now treats the review queue as a hard product signal: by default it allows at most
`15` packed mandatory review actions and `25` queue rows across the operational corpus. The current
corpus is below that line (`9` actions / `12` rows), so future cleanup must preserve the shorter
queue instead of hiding regressions behind a broad threshold.

Useful targeted checks:

```bash
murmurmark audit local-recall latest
murmurmark audit order latest
murmurmark audit group-overlaps latest --write-clips
murmurmark audit audio-review latest --write-clips
murmurmark audit stronger-audio-judge latest --max-items 80
```

The stronger audio judge is a local second opinion over short review clips. It does not replace the
main `whisper.cpp` transcript. The normal post-recording pipeline uses a broader `80` item budget by
default because the earlier `12` item cap left many `check_transcript_order` rows manually queued
even when local evidence could safely mark them as timing/double-talk.

## Command Reference

Most users should need only these commands:

```bash
murmurmark doctor
murmurmark record --target-bundle system
murmurmark process latest
murmurmark next latest
murmurmark status latest
murmurmark notes latest --kind verdict
murmurmark notes latest
murmurmark transcript latest
murmurmark review next latest
murmurmark finish latest
```

For the full command surface:

```bash
murmurmark --help
murmurmark process --help
murmurmark review --help
murmurmark corpus --help
```

## Artifacts

A processed session contains:

```text
sessions/<session-id>/
  audio/
    mic/000001.caf
    remote/000001.caf
  session.json
  events.jsonl
  pipeline_job.json
  derived/
    preprocess/
    transcript-simple/whisper-cpp/
    synthesis-simple/extractive/
    audit/
    readiness/
    retention/
```

Important user-facing files:

- transcript: `derived/transcript-simple/whisper-cpp/resolved/transcript*.md`;
- quality verdict: `derived/synthesis-simple/extractive/quality_verdict*.md`;
- notes: `derived/synthesis-simple/extractive/notes*.md`;
- review plan: `derived/readiness/review-plan/`;
- export manifest: `exports/private/<session-id>/export_manifest.json`.

Prefer the CLI commands (`murmurmark transcript`, `murmurmark notes`, `murmurmark open`) over guessing
the selected profile by filename.

## Remote-Forbidden Evidence Audit

This is the current Echo Guard hardening path. It is shadow/review-only: it does not change
`mic_for_asr.wav`, selected transcript profiles or raw CAF files.

```bash
SESSION=./sessions/<session-id>

murmurmark audit remote-forbidden "$SESSION" --profile auto
murmurmark report "$SESSION"
less "$SESSION/derived/audit/remote-forbidden/remote_forbidden_review.md"
```

The default audit uses Coverage v2: it keeps `local_fir` as production baseline, selects a small set
of ASR windows from speaker state and risky review artifacts, writes `selection_reason` for every
window, evaluates `coverage_v2_remote_gate_local_fir` as a shadow audio candidate, and stays
shadow-only.

If `offline_aec_v2` ASR audit already exists and only evidence files need to be rebuilt:

```bash
murmurmark audit remote-forbidden "$SESSION" --skip-lab --profile auto
```

Corpus view:

```bash
.venv/bin/python scripts/report-remote-forbidden-corpus.py sessions/<session-a> sessions/<session-b>
less sessions/_reports/remote-forbidden/remote_forbidden_corpus_report.md
```

The corpus report includes guarded seconds, review-burden seconds and an explicit
`why_not_more_safe_sessions` explanation. If fewer than two sessions are safely improved, the layer
stays shadow-only and the report should say whether the blocker is missing ASR-visible baseline leak,
local-recall risk or weak quarantine-only evidence.

## ASR-Positive Echo Candidate

`coverage_v2_remote_gate_local_fir` is the current experimental Echo Guard audio candidate. It is a
real WAV candidate, but it is still shadow-only: it never replaces `mic_for_asr.wav`, never changes
the selected transcript profile and never modifies raw CAF files.

Run or refresh the candidate report for one session:

```bash
SESSION=./sessions/<session-id>

murmurmark audit asr-positive-echo-candidate "$SESSION"
less "$SESSION/derived/preprocess/echo/asr_positive_echo_candidate_report.md"
```

If `offline_aec_v2` ASR artifacts already exist:

```bash
murmurmark audit asr-positive-echo-candidate "$SESSION" --skip-lab
```

Build the corpus view:

```bash
murmurmark corpus echo-candidate \
  sessions/2026-06-23_14-04-37 \
  sessions/2026-06-25_11-14-27 \
  sessions/2026-06-26_11-15-50 \
  sessions/2026-06-26_12-04-04 \
  sessions/2026-06-29_16-31-02 \
  sessions/2026-06-30_17-17-20

murmurmark corpus gate
```

Current six-session result: `5/6` safe improved, `1/6` not applicable because sampled `local_fir`
had no ASR-visible remote leak, `0/6` local-recall regressions. The corpus gate checks that this
candidate remains `shadow_only_do_not_promote`; promotion to default Echo Guard is a separate future
goal.

## Target-Me Evidence Audit

Target-Me evidence is the current research path for hard double-talk and open-space cases. It is
shadow-only: it does not change capture, `local_fir`, `mic_for_asr.wav`, selected transcript
profiles or raw CAF files.

```bash
SESSION=./sessions/<session-id>

murmurmark audit target-me "$SESSION" --profile auto --max-items 80
less "$SESSION/derived/audit/target-me/target_me_report.md"
```

`--method auto` uses the strongest available local backend: WavLM if model files are present,
otherwise `resemblyzer_dvector_v0` if the package is installed, otherwise `mfcc_contrastive_v0`.
The MFCC methods enroll `Me` from high-confidence local-only transcript regions; the contrastive
variant also enrolls clean remote speech as a negative class. They are measurement layers, not
production identity models.

For the recommended local speaker-embedding audit, install `resemblyzer`:

```bash
.venv/bin/pip install resemblyzer

murmurmark audit target-me "$SESSION" --profile auto --max-items 80
```

This uses local d-vector embeddings from `resemblyzer.VoiceEncoder`. It is still an evidence layer:
it can suggest that a risky `Me` row is probably real local speech, but it does not edit transcripts
or cleanup profiles by itself.

Review lane suggestions consume existing Target-Me rows as high-confidence evidence. The audio-review
pack includes open readiness review-plan rows, so Target-Me can match `local_recall_*`, lost-`Me`,
uncertain-audio and order-risk rows by `source_audit_id`, not only by transcript utterance IDs. A
normal `review suggested` run does not refresh Target-Me by default because it can be slower than the
review handoff itself. To refresh it inside the suggested flow deliberately:

```bash
MURMURMARK_REVIEW_TARGET_ME_REFRESH=1 murmurmark review suggested apply "$SESSION"
```

An optional WavLM speaker-verification backend is wired but requires local model files:

```bash
mkdir -p "$HOME/.local/share/murmurmark/models/target-me/wavlm-base-plus-sv"
# Put config.json, preprocessor_config.json and pytorch_model.bin from:
# https://huggingface.co/microsoft/wavlm-base-plus-sv

murmurmark audit target-me "$SESSION" \
  --profile auto \
  --method wavlm_xvector \
  --out-dir-name target-me-wavlm \
  --max-items 80
```

If those files are missing, the WavLM audit writes `status: missing_embedding_model` instead of
falling back silently when `--method wavlm_xvector` is explicit.

Current six-session reading:

- enrollment is available in all six smoke sessions;
- `102` risky clips were audited;
- `mfcc_contrastive_v0`: `0` helpful rows, `0` corroborating rows, `0` review-burden reductions;
- `resemblyzer_dvector_v0`: `13` new keep-evidence rows / `48.82s`, plus `54` corroborating rows /
  `306.95s`;
- local probe: `torch`, `transformers`, `faster_whisper` and `resemblyzer` are installed, but no
  source-separation package is ready;
- readiness impact: `shadow_only_not_applied`; actual `ready_for_notes` / `review_first` / `risky`
  counts are unchanged until review-loop integration;
- research decision for d-vector: `promising_shadow_evidence_continue`;
- promotion decision: `shadow_only_do_not_promote`.

Interpretation: simple acoustic voiceprints are useful as a sanity check but not strong enough.
Local d-vector speaker embeddings are promising as a review/evidence layer, especially to protect
real `Me` utterances from being treated as remote duplicates. They are not yet a production cleanup
rule and need corpus gates before any automatic review decision.

## Documentation Map

- [Mission and vision](docs/product/vision.md)
- [Product requirements](docs/product/prd-v1.md)
- [CLI roadmap](docs/roadmap/murmurmark-cli-roadmap.md)
- [Roadmap plan, opskarta v3](docs/roadmap/murmurmark-cli-roadmap.plan.yaml)
- [Experimental sidecar architecture](docs/architecture/experimental-sidecar.md)
- [First recording runbook](docs/runbooks/first-recording.md)
- [Transcription and review runbook](docs/runbooks/transcribe-simple-whispercpp.md)
- [Transcript and evidence contracts](docs/contracts/transcript-and-evidence.md)
- [Evidence synthesis architecture](docs/architecture/evidence-synthesis.md)
- [Open-source readiness](docs/project/open-source-readiness.md)
- [Latest completed quality goal](docs/project/current-goal.md)

## Roadmap Summary

Current focus:

- stabilize the current production path first: `record --target-bundle system`, then
  `process latest`, `next`, `status`, `finish`;
- keep every failed capture explicit: silent/partial/interrupted recordings must block before ASR
  and must not look like empty successful transcripts;
- keep the operational corpus at `pilot_ready_with_review` or better;
- treat `murmurmark report corpus` as the source of truth for corpus readiness;
- keep the remaining manual queue short, explicit and explainable;
- keep the review-loop stable: suggested decisions are cumulative, status/report/progress agree on
  the remaining queue, and unresolved rows stay explicit;
- use cached stronger-audio-judge, Target-Me and remote-forbidden evidence before asking for manual
  review;
- keep `local_fir` as the default Echo Guard path while shadow audio candidates are compared by
  ASR-visible remote-token leakage, local-word recall and review burden;
- keep `next`/`status`/`review` honest about residual risk;
- keep near-realtime processing as a shadow/debug CLI branch, not the recommended production path:
  legacy `record --live-pipeline` is disabled by default, while new evidence goes through
  `record --experiment live-shadow-v1` and raw commit materialization;
- keep live-ASR cache reuse behind strict eligibility gates; `not_eligible` is expected until
  live chunk geometry, audio prep, language and model match batch ASR expectations, and materialized
  live chunks must still pass the raw chunk rebuild check;
- use `murmurmark corpus live all --refresh` to inspect historical/debug live evidence and controlled
  evidence readiness; after full fail-open proof, the next live milestone is two more controlled
  passing real comparisons, not production promotion;
- make `process -> next -> review -> export -> retention` feel boring and repeatable;
- keep README, runbooks and roadmap aligned with the actual CLI.

Active goal and near-term candidates:

1. Active: Process Observability & Run Monitor v1: keep the stable non-live production path and make
   long `murmurmark process` runs observable from `status`/`next` without trusting stale reports.
2. Completed: Current Pipeline Stabilization v1: the supported production path is normal non-live
   `record -> process -> status/next/finish`; silent, sparse, partial and interrupted captures block
   before ASR, and live recording is quarantined.
3. Completed: Chunked/Resumable Processing v1: ASR work is chunk-addressed, cacheable,
   interrupt-safe and guarded by rebuild/corpus gates. The remaining work is broader corpus
   coverage, not the v1 mechanism.
4. Active live follow-up: Near-Realtime Live Parity Coverage v1. Capture-safe sidecar proof exists,
   `murmurmark corpus live all --refresh` compares real live chunks/drafts with batch output, and
   live promotion stays blocked. The comparison now uses ASR-segment granularity when available, with
   chunk fallback for older artifacts. Current next focus is `fix_live_local_recall_gap`: metric-aware
   triage keeps order risk visible but points the next implementation at lost local speech and the
   coarse live role gate. Segment-level parity still exposes ordering drift, suspected remote leakage
   in live `Me`, and lost local speech on controlled real evidence. The order-risk split is now visible: `20` same-chunk/same-source,
   `11` same-chunk/cross-source, `2` cross-chunk and `1` overlap-context mismatches. The corpus
   also separates primary risk: `19` role-conflict/remote-leak, `8` weak-match possible false
   positives and `7` direct timeline-reorder cases. A stricter same-role matcher confirms `9`
   role-constrained order mismatches (`6` same-source and `3` cross-source, all inside one live
   chunk), and the contentful same-role slice narrows this to `4` actionable order-risk examples.
   Ambiguity scoring narrows the stable contentful subset further to `2` examples.
   The corpus report now lists concrete
   `capture_safe_evaluable_local_recall_gap_examples` for the fix. Most missing Me seconds are
   visible in suppressed mic chunks. A text-only segment rescue is now recorded as diagnostic
   metadata (`6` real live candidate chunks / `31` candidate segments in the current corpus) but is
   not published into the live draft, because it can reintroduce remote as `Me`. The next
   implementation target is a stronger audio/evidence gate for local speech inside suppressed mic
   chunks. The corpus report now also labels suppressed mic ASR segments against batch:
   `49` Me-dominant segments / `209.58s` and `44` mixed segments / `199.92s` in real live runs.
   A rescue policy lab is now part of the report: current text-only rescue would recover only
   `152.6s` local speech while risking `73.62s` remote leak; strict unique-token text rescue is also
   unsafe (`143.52s` local / `219.12s` remote-risk). `remote_silent_text_v1` is much safer
   (`34.16s` local / `2.58s` remote-risk) but low-recall. The first audio/evidence policy lab shows
   `audio_mic_dominant_v1` has `24.0s` local / `0.0s` remote-risk, while `audio_safe_union_v1`
   recovers `68.42s` missing-Me at `2.58s` remote-risk. Fresh live chunks can now expose that policy
   as `live_rescue_shadow`: `2` real-live chunks / `9` segments, `45.36s` missing-Me recovered,
   `373.80s` missing-Me still left, `0.0s` measured remote-risk and `34` segment-level order
   mismatches still present in the current corpus. Published live `Me` also has `15.96s` suspected
   remote leakage. Rescue shadow remains candidate evidence, not normal live `Me`; full live
   promotion remains blocked. The scoped candidate diagnostic is stricter:
   `capture_safe_candidate` now reports `no_material_live_candidate`; the best live-implementable
   policy recovers only `1.80s` local speech, while the batch-oracle ceiling is `13.06s`. The next
   implementation needs stronger local-speaker evidence inside suppressed mic segments, not simply
   enabling an existing rescue policy. The new `live_local_recall_rescue_lab` block makes that
   explicit: in real live runs, suppressed mic ASR contains `369.74s` local/mixed speech, current
   candidate policies cover `181.20s` of it but also include `38.78s` remote-risk false positives,
   and `188.54s` still need Target-Me or stronger local-speaker evidence. In the stricter
   capture-safe candidate slice, `11.26s` still need that stronger evidence.
   `scripts/audit-live-local-recall-target-me.py` is the new shadow check for that gap. It cuts the
   suppressed live mic segments, compares them with the local Target-Me speaker embedding backend
   and writes `live_local_recall_target_me_*` reports. The first corpus pass shows that Target-Me can
   explain most of the remaining local gap (`287.98s` possible/confirmed local out of `295.34s`
   audited local seconds). The first safe candidate is `target_me_confirmed_remote_guard_v1`: it
   would recover `94.68s` missing-Me with `2.44s` remote-risk in the current corpus. It is still
   evidence only and does not publish live `Me`. `compare-live-batch.py` now also evaluates these
   policies as a counterfactual live shadow. The stricter `target_me_confirmed_remote_guard_v1`
   shadow recovers `128.85s` missing-Me and adds `0.0s` measured remote leak, but it still adds `3`
   contentful role-constrained order mismatches. The conservative
   `target_me_confirmed_remote_guard_timeline_safe_v1` subset avoids those regressions and now
   recovers `103.82s` missing-Me with `0.0s` measured remote leak and `0` new contentful order
   mismatches. `compare-live-batch.py` materializes that subset into
   `derived/live/target-me-shadow/target_me_confirmed_remote_guard_timeline_safe_v1/draft.{json,md}`
   as diagnostic-only output. A stricter batch-oracle diagnostic,
   `target_me_confirmed_remote_guard_timeline_safe_batch_remote_forbidden_oracle_v1`, removes live
   `Me` turns that the authoritative batch transcript classifies as remote-like. It drops measured
   live remote leak from `15.96s` to `0.0s`, but non-passing profile gates only improve from `42` to
   `41` and missing-Me remains `315.34s`. So the current blocker is no longer "collect more
   recordings" or "find whether remote leak exists"; it is fixing remaining local-recall, order,
   review/readiness and capture-safe live evidence gaps. The remaining missing-Me now has useful
   diagnostics: `278.13s` are already visible in suppressed mic ASR, `174.33s` have a broader
   Target-Me candidate, and `141.01s` have no Target-Me candidate. A second diagnostic ceiling,
   `target_me_confirmed_remote_guard_timeline_safe_batch_remote_forbidden_visible_suppressed_mic_oracle_v1`,
   adds safe visible suppressed mic segments and drops profile missing-Me to `140.41s` while keeping
   measured remote leak at `0.0s`. The first live-accessible attempts are not enough:
   `audio_safe_union_v1` is safe but leaves `301.96s` missing-Me, while `audio_low_corr_text_guard_v1`
   leaves only `117.52s` missing-Me but leaks `210.10s` remote-like text. The historical persistent
   Target-Me profile lab is also not enough as the main live rescue mechanism: in the full real
   scope it recovers `75.72s` local/mixed speech under the conservative remote guard, but still
   selects `8.64s` remote-risk speech; in the stricter capture-safe candidate scope it recovers
   `0.00s`. A composite gate lab gives a smaller but cleaner result: `dual_target_remote_guard_v1`
   recovers `47.70s` local/mixed speech at `0.00s` remote-risk in the full real scope, while
   `target_me_remote_guard_v1` recovers `116.10s` at `2.44s` remote-risk; the capture-safe candidate
   scope still recovers `0.00s`. This zero-risk slice is now materialized as
   `online_suppressed_mic_dual_target_remote_guard_v1`: it adds `47.70s`, leaves `380.17s`
   missing-Me and does not remove the existing `15.96s` live remote leak. The paired
   `online_live_me_remote_overlap_filter_plus_dual_target_remote_guard_v1` shadow removes that
   `15.96s` remote-like live `Me` using only live timing/text overlap and keeps the `47.70s`
   suppressed-mic rescue, but it still leaves `380.17s` missing-Me and `4` contentful order
   mismatches. The stronger live-implementable profile
   `online_live_me_remote_overlap_filter_plus_target_me_timeline_safe_audio_safe_union_v1` gets to
   `301.96s` missing-Me with `0.00s` remote leak, but still has `4` contentful order mismatches and
   `41` non-passing gates. The residual gap splits into `174.33s` visible with broader Target-Me
   evidence, `90.42s` visible without Target-Me evidence, and `37.21s` not visible in suppressed
   mic. A less strict remote-guard variant reaches `276.93s` missing-Me but raises contentful order
   mismatches to `7`. The timeline-safe Target-Me policy rejects only `18.30s` of candidates for
   order risk, so the main remaining opportunity is better local-speaker evidence for visible
   suppressed mic regions. Batch remains authoritative.
5. Audio candidate promotion readiness: keep `coverage_v2_remote_gate_local_fir` shadow-only, widen
   the corpus beyond the current six sessions and define the future default-promotion bar.
6. Target-Me evidence follow-up: keep using `resemblyzer_dvector_v0` and stronger-audio-judge as
   safe review evidence, shrink only rows with strong local proof, and keep ambiguous rows explicit.
7. Operational Corpus Green follow-up: keep `pilot_ready_with_review` stable, reduce the remaining
   irreducible queue when new local evidence appears, and prevent status drift.
8. Near-Realtime Pipeline Shadow v1 follow-up: raw-safe segment production exists via
   `record --experiment live-shadow-v1`; now harden the live draft worker around Me/local recall,
   per-fragment Echo Guard, resumable worker state and corpus parity gates.
9. Echo Guard promotion experiment: only after the shadow candidate passes a broader operational
   corpus, test a separate promoted profile that writes a non-default `mic_for_asr` candidate bundle.
10. Operational polish: make the happy path clearer when recording stops unexpectedly, when a session
   is partial, or when ASR will take a long time.
11. Export readiness follow-up: keep improving the final handoff after Export Bundle Quality v1,
   especially Obsidian-vault placement and reviewed proposal exports.
12. Regression discipline: keep a small stable corpus gate that catches transcript/order/local-recall
   regressions before new heuristics ship.
13. Evidence notes vNext: improve extractive notes quality while preserving citations and review
   flags.
13. Open-source release hardening: trim private fixtures, document setup, add security/contact
   guidance and keep generated/private artifacts ignored.

Recently completed:

- ASR-positive echo candidate hardening v1: `murmurmark audit asr-positive-echo-candidate` now writes
  `asr_positive_echo_candidate_report.{json,md}` for one session, and `murmurmark corpus
  echo-candidate` writes `asr_positive_echo_candidate_corpus_report.{json,md}`. Current six-session
  corpus: `5/6` safe improved, `1/6` not applicable, `0/6` local-recall regressions. `murmurmark
  corpus gate` reads the report and enforces `shadow_only_do_not_promote`.
- ASR-positive audio candidate v2: `coverage_v2_remote_gate_local_fir` is a real shadow audio
  candidate, not just a transcript token guard. It starts from the safe local-fir/segment-switch path
  and applies remote-floor cleanup only in Coverage v2 risk windows without strong local-speech
  evidence. Six-session smoke: `4/6` ASR audio candidate gate-passed sessions, `0/6` local-recall
  regressions, `2/6` explained as `no_baseline_asr_visible_leak`, no default promotion.
- Remote-Forbidden Evidence Coverage v2: ASR audit-window selection now reads speaker state,
  audio-review, stronger-audio-judge, group-overlap, transcript-overlap and local/order risk
  artifacts. Six-session smoke: `4/6` safe improved sessions, `0/6` local-recall regressions,
  `24` evaluable windows, `578` skipped by cap, no default promotion.
- Remote-Forbidden Evidence Hardening v1: `remote_forbidden_evidence.jsonl`,
  `remote_forbidden_summary.json`, `remote_forbidden_review.md`, session readiness metrics and
  corpus explanation are now normal artifacts. Six-session smoke: one safe improved session, zero
  local-recall regressions, no default promotion.
- Echo Guard Complete Removal vNext: segment switching plus `remote_forbidden_token_guard` produced
  the first ASR-positive remote-leakage improvement on a real difficult session, with no local-word
  recall regression in the six-session smoke corpus. It remains shadow-only.
- Readiness reconciliation: when `review_actions` is `0`, MurmurMark no longer recommends an empty
  `review first-lane`; it either points to a real review pack, a ready state or a documented
  non-actionable blocker.

Longer-term:

- richer transcript schema;
- better local audio validators;
- optional local LLM synthesis with evidence guards;
- reviewed docs/ticket export proposals;
- optional UI only after the CLI is mature.

## Development Checks

Before pushing code changes:

```bash
swift build
.venv/bin/python -m py_compile scripts/*.py
caffeinate -dimsu scripts/check.sh
murmurmark self-test
murmurmark acceptance --skip-release
scripts/check-open-source-readiness.sh
```

`caffeinate -dimsu` keeps the desktop session awake while the full check runs. This matters because
`doctor --strict` deliberately fails when ScreenCaptureKit sees no shareable display.

`scripts/check.sh` includes a static capture regression check. It keeps the ScreenCaptureKit system
audio contract pinned so `remote` capture does not silently regress back to digital silence.
When `sessions/_reports/session-quality/session_quality_report.json` exists, it also runs the
current-pipeline stabilization audit: no usable/review-first session may have an empty transcript,
incomplete sessions must not expose normal notes/transcript handoff, and `status latest` must agree
with `next latest`.

When changing recording code, also run one local system-audio and live fail-open probe:

```bash
MURMURMARK_RUN_LIVE_CAPTURE_TEST=1 scripts/check-capture-regressions.sh
```

The probe plays a short local tone, records with the normal `record --target-bundle system` path,
and fails if the `remote` track is silent or if ScreenCaptureKit restarts. It also runs a lab-only
unsafe live recording with a deliberately overloaded async live segment queue and fails if raw
`mic`/`remote` capture does not survive after live-derived segments disable themselves.

Before trusting a pipeline change:

```bash
murmurmark report corpus
scripts/check-current-pipeline-stabilization.py
murmurmark corpus process all --per-label 16 --max-items 160
murmurmark corpus gate
murmurmark next corpus --refresh
```

Raw `audio/*.caf` captures are user data. Do not commit them, rewrite them or delete them through a
retention apply step unless that is the explicit requested action.
