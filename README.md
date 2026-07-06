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
The live/near-realtime branch is still quarantined for real meetings. Its segment writer now runs
behind an async bounded queue instead of doing derived live writes in the ScreenCaptureKit callback,
but live promotion still requires real parity evidence. The latest audio milestone,
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
ScreenCaptureKit audio delivery and leave raw capture mostly silent. The new async bounded live
queue still needs capture-safety and parity proof. Do not collect real meetings with
`--live-pipeline` until those gates pass.

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

Experimental near-realtime shadow mode is disabled by default:

```bash
MURMURMARK_ENABLE_UNSAFE_LIVE_PIPELINE=1 \
  murmurmark record --target-bundle system --live-pipeline --live-segment-sec 60 --live-overlap-sec 5
```

Do not use `--live-pipeline` for real meetings. It is still quarantined: the first capture-safe
segment handoff is implemented, but the branch has not yet passed real live-vs-batch parity gates.
The supported production path is `murmurmark record --target-bundle system` followed by
`murmurmark process latest`.

This mode writes closed audio segments and a live draft under `derived/live/` while recording.
The callback path writes durable raw CAF first and then does a non-blocking enqueue into an async,
bounded live segment queue. If that queue falls behind, only live-derived segments are disabled;
raw `audio/mic/*.caf` and `audio/remote/*.caf` must remain batch-processable. Segments have a
non-overlapping authoritative window plus copied overlap context around it. After stop it runs the existing
batch-grade reconcile through `murmurmark process --skip-build` and writes
`derived/live/final_reconcile_report.json`. The normal batch result remains authoritative until
corpus gates prove that the live branch matches it. If the live worker or derived live segment writer
fails, recording should still produce a normal raw session package. After `Ctrl-C`, MurmurMark waits
only a short finalization tail for the live worker; a stuck worker is terminated and batch reconcile
continues. If you need only the draft and want to run batch processing manually later, use
`--live-no-finalize`.

For lab evidence, use the pilot runner instead of assembling the unsafe commands by hand:

```bash
scripts/run-live-parity-pilot.sh --duration 45
```

It first runs the local capture/live fail-open probe, then records a short lab-only live session,
runs the normal batch pipeline, compares live output with batch output and refreshes
`murmurmark corpus live all`. It writes `derived/live/live_parity_pilot_report.json` under the pilot
session. Promotion must remain blocked and the batch transcript remains authoritative.

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
murmurmark corpus live all
less sessions/_reports/live-pipeline/live_corpus_gates_report.md
jq '.real_blocker_triage_summary' sessions/_reports/live-pipeline/live_corpus_gates_report.json
```

The expected v1 result is still `shadow_only_do_not_promote`: the report should explain which gates
are evaluated and which remain blockers. Current live comparison checks the live draft against the
authoritative batch transcript for capture safety, order mismatch, missing `Me` speech, suspected
remote-in-`Me` leakage, selected batch review burden, notes readiness, adjacent chunk duplicates and
source-level `live_boundary_gate` suppression. A live draft can therefore be blocked even when the
final draft text has no duplicate: if a boundary gate had to suppress repeated text, that still
counts as chunk-boundary risk.
Passing those checks is still not promotion. With live quarantined, use this report only to inspect old diagnostic
evidence and gate failures. The Markdown report has a `Gate Issues` section with the concrete
session/gate/reason rows that currently prevent a passing comparison. Promotion checks use
`real_parity_dimensions`: only date-named real meeting sessions count there. `_debug_*`,
`live-pilot-*` and other lab sessions stay visible as diagnostic evidence, but they cannot satisfy
real coverage. Draft text recall is tracked separately from required artifacts: a session can have
all files present and still fail because the live draft text does not match the authoritative batch
transcript.
The report also has `real_blocker_triage_summary` and a `Real Blocker Triage` Markdown section. Use
that first when deciding what to do next: it separates batch review/readiness debt, missing artifacts,
capture safety risks, local recall gaps, remote leakage and live draft drift. Triage is explanatory
only; it does not make live safe for new real meetings.
While live is quarantined, `recommended_next` and `next:` point to triage and inspection commands for
existing artifacts. They must not suggest the strict live-coverage command as the next action.

The old live-coverage target remains a diagnostic report shape, not a current action item. Do not use
this command as an instruction to collect more live meetings while live recording is quarantined:

```bash
murmurmark corpus live all \
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

This target command is expected to fail. Do not try to satisfy it by recording real meetings with
`--live-pipeline`; the next live step is implementation redesign, not broader live coverage.

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
- [First recording runbook](docs/runbooks/first-recording.md)
- [Transcription and review runbook](docs/runbooks/transcribe-simple-whispercpp.md)
- [Transcript and evidence contracts](docs/contracts/transcript-and-evidence.md)
- [Evidence synthesis architecture](docs/architecture/evidence-synthesis.md)
- [Open-source readiness](docs/project/open-source-readiness.md)
- [Latest completed quality goal](docs/project/current-goal.md)

## Roadmap Summary

Current focus:

- stabilize the current production path first: `record --target-bundle system` without
  `--live-pipeline`, then `process latest`, `next`, `status`, `finish`;
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
- keep near-realtime processing as a shadow/debug CLI branch, not the recommended recording path
  during current stabilization: `record --live-pipeline` is disabled by default while the new async
  bounded segment queue proves that it cannot corrupt raw `mic`/`remote` capture;
- keep live-ASR cache reuse behind strict eligibility gates; `not_eligible` is expected until
  live chunk geometry, audio prep, language and model match batch ASR expectations, and materialized
  live chunks must still pass the raw chunk rebuild check;
- use `murmurmark corpus live` only to inspect historical/debug live evidence while live is
  quarantined; the next live milestone is a capture-safety proof for the async segment queue, not
  collecting more real live sessions;
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
4. Later: Near-Realtime Capture-Safe Redesign v1, after the stable production path is boring and
   repeatable again. Only after that may live parity coverage return.
5. Audio candidate promotion readiness: keep `coverage_v2_remote_gate_local_fir` shadow-only, widen
   the corpus beyond the current six sessions and define the future default-promotion bar.
6. Target-Me evidence follow-up: keep using `resemblyzer_dvector_v0` and stronger-audio-judge as
   safe review evidence, shrink only rows with strong local proof, and keep ambiguous rows explicit.
7. Operational Corpus Green follow-up: keep `pilot_ready_with_review` stable, reduce the remaining
   irreducible queue when new local evidence appears, and prevent status drift.
8. Near-Realtime Pipeline Shadow v1 follow-up: first redesign segment production so live work cannot
   affect raw capture, then harden the live draft worker with per-fragment Echo Guard, resumable
   worker state and corpus parity gates.
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
