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
behind a bounded committed-PCM queue after durable raw writes, not inside the ScreenCaptureKit
callback. A progressive past-only Target-Me shadow now evaluates suppressed mic segments inside the
live worker, localizes candidate text to remote-free or speaker-confirmed subwindows, and runs focused
micro-ASR without using batch fields. The full fail-open proof allows
controlled Live Evidence runs on real meetings, but live promotion still requires passing parity
coverage. The latest audio milestone,
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

SESSION="sessions/$(date +%Y-%m-%d_%H-%M-%S)"
murmurmark record --out "$SESSION" --target-bundle system
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

Use the same `$SESSION` for every command after recording. `latest` is only a convenience pointer to
the newest session directory; it is unsafe when another terminal may start a newer recording before
the current processing finishes.

If capture stops before `Ctrl-C`, if written CAF tracks cover far less time than the wall-clock
recording, if both mic and remote tracks are effectively silent, or if a long recording contains only
a tiny amount of active audio, MurmurMark finalizes or blocks the session instead of pretending it is
complete. In that case `record`, `status`, `next` and `process` point to `murmurmark inspect ...`;
normal processing is blocked unless you explicitly pass `--allow-partial` for debugging.

Run only one `murmurmark record` process at a time. ScreenCaptureKit is not treated as a reliable
multi-client capture source for MurmurMark: starting a safe recording and a live recording in
parallel can leave both sessions unfinalized or empty. The CLI keeps a recording lock and rejects a
second concurrent `record` before it creates a broken session. The supported production path is one
durable raw capture followed by batch processing. Add `--experiment live-shadow-v1` only when you
want sidecar evidence; the batch transcript remains authoritative either way.

ScreenCaptureKit may skip audio buffers during silence or source inactivity. MurmurMark preserves
the meeting timeline in raw CAF files by inserting silence for timestamp gaps instead of compressing
the recording to only the buffers that arrived. If no ScreenCaptureKit audio samples arrive at the
start of recording, MurmurMark now tries short restarts and then fails fast as a partial capture
instead of letting a whole meeting become an empty transcript. If a long recording has only sparse
audio bursts, `process` blocks with `sparse_capture`; this catches cases where timestamp padding kept
the CAF duration correct but ScreenCaptureKit delivered almost no useful audio.

Then run:

```bash
murmurmark process "$SESSION"
murmurmark next "$SESSION"
murmurmark status "$SESSION"
murmurmark outcome "$SESSION"

murmurmark notes "$SESSION" --kind verdict
murmurmark notes "$SESSION"
murmurmark transcript "$SESSION"
murmurmark finish "$SESSION"
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
SESSION="sessions/$(date +%Y-%m-%d_%H-%M-%S)-live"
murmurmark record --out "$SESSION" --target-bundle system --experiment live-shadow-v1
# While recording, run in a second terminal:
murmurmark live watch "$SESSION"
# After Ctrl-C in the recording terminal:
murmurmark process "$SESSION"
murmurmark experiment status "$SESSION"
murmurmark experiment report "$SESSION"
murmurmark experiment compare "$SESSION" --experiment live-shadow-v1
```

The shape is:

```text
capture -> durable raw writer -> stable session
                    |
                    +-> nonblocking committed PCM queue -> live segmenter -> live ASR draft
```

In this mode the capture callback writes raw CAF first. Only after `AudioFileWriter` accepts the
frames does the experiment enqueue a copied committed PCM packet into a bounded nonblocking queue.
That queue writes closed segment files under `derived/experiments/live-shadow-v1/audio/` and appends
compatible rows to `derived/live/segments.jsonl`; `scripts/live-pipeline-shadow.py` then consumes
those closed segments and updates `derived/live/transcript.preview.md`. `murmurmark live watch`
shows this conservative preview by default. The complete candidate-only diagnostic view remains in
`derived/live/transcript.draft.md` and is available through `live watch --diagnostic-draft`. The raw
commit log still exists as evidence and as a post-stop fallback; neither preview path reads a
still-open CAF. The worker writes the base mic/remote chunk and refreshes both views before it runs the optional
causal Target-Me shadow. Target-Me micro-ASR has a bounded child timeout and is skipped when the
worker is already more than `60s` behind captured audio. The report exposes
`skipped_lag_budget_count`; losing optional speaker evidence is preferable to letting it delay the
base draft without bound.
The base `mic` and `remote` decodes are independent: on hosts with at least twelve logical CPUs the
worker runs them concurrently with four threads per decoder; smaller hosts keep the sequential path.
Each decoder remains in a bounded child process, so shutdown and fail-open behavior are unchanged.
The conservative preview publishes causal Target-Me only when its recording-time remote-energy
gate passes. A candidate is withheld when contemporary remote audio is active and mic does not
dominate it by at least `20 dB`; the diagnostic draft still retains the evidence for later parity
analysis. Batch remains authoritative in both files.
Every preview rewrite also appends `derived/live/preview_snapshots.jsonl` with UTC creation time,
covered chunk/end, policy, candidate counters and SHA-256. Session evidence requires a non-empty
snapshot timestamped before stop; a preview reconstructed after stop cannot satisfy that gate.
Realtime files under `derived/live/` are immutable evidence after recording stops. Post-stop recovery
writes under `derived/experiments/live-shadow-v1/fallback/` and never replaces realtime segments,
chunks, draft text, state or timestamps.
If the PCM queue or live ASR falls behind, only the experiment is disabled or marked partial. Raw
`audio/mic/*.caf` and `audio/remote/*.caf` remain the authoritative recording and `murmurmark process`
remains the authoritative transcript path.
The transport/worker reliability unit is covered by pre-stop, timeout, termination, backpressure and
fallback-isolation tests. Promotion still requires at least three fresh meaningful real meetings
with healthy raw/batch output, timestamped pre-stop artifacts and passing live-vs-batch gates.

`--experiment live-shadow-v1` is controlled evidence, not promotion. It can be used on real
meetings when you want live evidence, because raw CAF remains the source of truth and
`murmurmark process` remains the authoritative transcript path. Use plain
`murmurmark record --target-bundle system` when you do not need the sidecar evidence. The draft is
useful for watching whether sidecar work can run beside stable capture, but it is not a final result.
See [Experimental sidecar architecture](docs/architecture/experimental-sidecar.md).

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
murmurmark experiment status "$SESSION"
murmurmark experiment report "$SESSION"
murmurmark experiment compare "$SESSION" --experiment live-shadow-v1
murmurmark live evidence "$SESSION"
murmurmark experiment recover-draft "$SESSION" --experiment live-shadow-v1  # explicit fallback only
```

`experiment compare` is read-only with respect to live audio, chunks and draft text. It compares
whatever was actually produced by the recording-time worker and must finish without starting ASR.
Use `experiment recover-draft` only when a separate post-stop diagnostic draft is useful. Recovery
is never counted as pre-stop evidence and does not make the live result authoritative.
`live evidence` writes `derived/live/live_session_evidence.{json,md}` and gives one compact verdict
for capture health, pre-stop provenance, worker lag/termination, fallback isolation and parity. Use
`--strict` in an acceptance run when a non-passing session must return exit code `2`.
The default comparison computes the required parity gates. Expensive exploratory target-me shadow
profiles are opt-in:

```bash
MURMURMARK_COMPARE_WITH_LABS=1 scripts/compare-live-batch.py sessions/<session-id>
# or
scripts/compare-live-batch.py sessions/<session-id> --with-labs
```

For lab evidence, prefer the raw-commit experiment over the unsafe legacy live path:

```bash
SESSION="sessions/$(date +%Y-%m-%d_%H-%M-%S)-live-lab"
murmurmark record --out "$SESSION" --target-bundle system --duration 120 --experiment live-shadow-v1
murmurmark process "$SESSION"
murmurmark experiment compare "$SESSION" --experiment live-shadow-v1
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
The heavier causal Target-Me pass is best-effort: the base chunk is durable first, micro-ASR is
bounded, and lag pressure produces an explicit `skipped_lag_budget` result instead of holding the
draft pipeline indefinitely.
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
`online_live_me_remote_overlap_filter_plus_target_me_possible_timeline_safe_audio_safe_union_local_speaker_boundary_shadow_live_boundary_split_retime_v1`:
it combines online remote-overlap cleanup, timeline-safe `target_me_possible_v1` rescue,
`audio_safe_union_v1`, the relaxed remote-forbidden boundary classifier and a stricter
local-speaker boundary candidate that requires live speaker evidence and zero token overlap with
the overlapping remote text. It then applies a live-only boundary split/retime pass for mic/remote
overlap rows using only live chunk timing, source and text-overlap evidence. Current real corpus
result: `51.50s` missing-Me, `0.00s` measured remote leak, `2` contentful order mismatches and
`41` non-passing gates. This is the strongest non-oracle candidate so far, but still not
promotable. Its remaining missing-Me splits into
`37.73s` visible in suppressed mic without Target-Me evidence and `13.77s` not visible in
suppressed mic. The previous local-speaker boundary profile remains the baseline before
split/retime: it has the same `51.50s` missing-Me and `0.00s` measured remote leak, but `4`
contentful order mismatches. The live split/retime pass keeps the local-recall and remote-leak
numbers unchanged while lowering order risk from `4` to `2`.
The local-island split/retime oracle no longer proves an additional missing-Me gain on the real-live
subset: it stays at `51.50s` missing-Me with `0.00s` remote leak. The useful diagnostic gain is now
narrower: the remote-guarded voice-boundary shadow materializes the previous `0.32s`
speaker-confirmation candidate, but it uses a batch anchor and remains non-promotable.
The live corpus report also writes
`live_target_me_shadow_profile_best_live_implementable_remaining_gap`, which groups this residual
gap by Target-Me evidence and session. The next useful work is to tighten voice/remote-guard
evidence for the remaining mixed rows, while keeping batch authoritative.
The same block now includes suppressed-mic evidence slices. Current top suppressed evidence groups:
policy set `(none)` (`17.83s`), gate reason `(none)` (`34.45s`) and top batch-role label
`remote_dominant` (`32.90s`). A `known_hallucination` slice
(`12.42s`) is tracked separately and is never a rescue candidate. That means the remaining gap is
not a simple clean local speech queue; many rows are short mixed/remote-dominant fragments or ASR
artifacts that need better speaker evidence or stricter segmentation before they can be trusted.
The actionability split makes the next step concrete. Current best live-implementable missing-Me is
`51.50s` with `0.00s` measured remote leak and `2` contentful order mismatches. The remaining rows
include `12.41s` remote-dominant rows that must stay blocked, `12.42s` known hallucination that
must stay excluded, and `25.32s` mixed/speaker-boundary rows. The mixed/speaker subset now splits
into `10.58s` boundary-island micro-ASR work, `5.36s` mixed boundary voice gating, `8.58s`
duplicate-heavy voice disambiguation, `0.32s` speaker confirmation and `0.48s` low-value tail.
The `0.32s` speaker-confirmation candidate is already materialized in a diagnostic remote-guarded
voice-boundary shadow profile; it remains non-promotable.
The report also writes `live_next_unlock` (`murmurmark.live_next_unlock/v1`). It keeps batch
authoritative and explains full-corpus blockers, including historical unsafe/debug runs. For the
current unlock path, prefer `capture_safe_candidate_scope`: it excludes broken-capture evidence.
After Target-Me evidence and the best live-implementable profile were materialized for the latest
capture-safe sessions, the blocking order rows in the active unlock slice were repaired without
relaxing batch authority. The
previous remote-gap baseline profile is
`online_live_me_remote_overlap_filter_plus_target_me_possible_timeline_safe_audio_safe_union_local_speaker_boundary_shadow_live_boundary_split_retime_voice_activity_token_density_target_me_remote_gap_trim_micro_asr_v1`.
It combines sustained voice activity with a causal token-density boundary check over already-written
live ASR JSON. Long remote segments are moved past low-confidence leading spans only when at least
five reliable lexical tokens occur inside a six-second window. A temporal prior also prevents short
generic live phrases from matching a distant batch phrase only because its text is identical. On
the refreshed 14-session real corpus this profile has `5` contentful order mismatches: `0` are
gate-blocking and `5` remain advisory. In the active capture-safe unlock slice, triage has `0`
blocking / `2` advisory rows; the historical full-corpus triage retains one blocking row outside
that active slice. The profile additionally keeps only token-timestamped pieces of strongly
confirmed Target-Me segments that fall between guarded live remote intervals. It materializes `42`
pieces / `176.262s` across the real corpus and closes `15.38s` of missing Me without increasing
remote leakage or order risk. A live-only short-window micro-ASR pass now re-decodes only compact
Target-Me gaps. It accepts `3` pieces / `10.74s`, rejects `3` unsafe or already covered candidates,
and closes the last known remote-dominant Target-Me row / `4.68s`. The full profile now misses
`714.81s` of batch `Me`; the classified remaining-gap set is `81` rows / `268.01s`, and `40.29s`
of remote-like `Me` remains. Promotion stays blocked.

A causal local-speaker follow-up now runs inside the live worker. It uses only enrollment audio from
closed earlier chunks, evaluates the current chunk before enrollment, and examines unpublished groups
from chunk-level suppressed mic chunks. Runtime artifacts live under
`derived/live/causal-target-me/`; the draft marks them as candidate-only. The direct comparison
profile `live_runtime_causal_target_me_direct_v1` publishes only candidates localized outside live
remote intervals. Speaker-confirmed sliding-window candidates remain diagnostic because they have no
safe insertion point in the live timeline.

The refreshed corpus now contains `15` real live sessions and `8` meaningful comparisons. Real
session `2026-07-10_16-00-29-live` proves recording-time execution with `131` pre-stop chunks and
`56` pre-stop accepted causal candidates while preserving complete raw CAF and a successful batch
transcript. It is not a passing comparison: final row-derived lag is `82.752s`, batch is
`review_first`, and live local-recall, remote-leak, order and boundary gates remain red.

This fresh evidence also changes the algorithmic conclusion. Direct runtime Target-Me improves
recall but regresses remote/order safety relative to the base policy, so its aggregate status is
`regression_detected`. Speaker-overlap remains safe only relative to direct. The remote-energy
follow-up restores remote/order metrics to the base level and is now the aggregate best
live-implementable policy. Live output remains shadow-only and batch authoritative.

The conservative follow-up `live_runtime_causal_target_me_remote_energy_v1` keeps the same causal
Target-Me candidates but publishes one only when the contemporary live `remote` interval is quiet
(`<= -65 dBFS`) or `mic` exceeds it by at least `20 dB`. The gate uses only closed live chunk audio;
it does not inspect batch labels. This profile is evaluated separately from the direct profile so a
failed experiment cannot weaken the existing baseline. It intentionally gives up ambiguous
double-talk recall to protect role attribution. Across `11` comparable sessions it recovers
`634.43s` of missing `Me`, keeps remote-like `Me` at `126.30s`, keeps blocking/advisory order at
`2 / 14`, improves weighted F1 by `0.029584`, and has zero per-session F1 regressions. The fresh
meeting provides its first pre-stop evidence, so its corpus status is `safe_shadow_candidate`.

Corpus profile ranking compares `comparable_*` gate counters so a runtime-only provenance gate does
not make the runtime algorithm look worse than a baseline that has no such gate. Promotion still
uses the complete gate set. There are still zero passing real sessions. Runtime provenance is now
available, but it does not override parity regressions.

The next live-only profile, `live_runtime_causal_target_me_speaker_overlap_v1`, also accepts
speaker-confirmed windows inside remote-active intervals, but only with strong micro-ASR/source
alignment and short backchannel or known-hallucination remote context. On the same corpus it reduces
missing Me `1881.44s -> 1829.64s`, keeps remote-like Me at `35.42s`, keeps blocking/advisory order at
`1 / 5`, and raises weighted F1 `0.772660 -> 0.774524`; maximum per-session F1 regression is `0`.
Its pre-stop evidence count is now one; it remains shadow-only because the full Target-Me path and
the session parity gates do not pass.

After comparison, verify that evidence was produced before stop rather than reconstructed later:

```bash
jq '.temporal_provenance, [.parity_gates.gates[] | select(.name | startswith("pre_stop"))]' \
  "$SESSION/derived/live/live_batch_comparison.json"
```

```bash
.venv/bin/python scripts/report-live-local-only-enrollment-probe.py --method resemblyzer_dvector
.venv/bin/python scripts/report-live-boundary-island-micro-asr-lab.py \
  --candidate-source causal-local-only-seed-live-segment \
  --source-scope live
```

For a focused refresh, compare the previous and runtime profiles without running every lab policy:

```bash
CURRENT=online_live_me_remote_overlap_filter_v1
RUNTIME=live_runtime_causal_target_me_direct_v1
.venv/bin/python scripts/report-live-boundary-island-micro-asr-lab.py \
  --candidate-source target-me-remote-gap \
  --source-scope live
.venv/bin/python scripts/report-live-corpus-gates.py all \
  --refresh \
  --refresh-lab-policy "$CURRENT" \
  --refresh-lab-policy "$RUNTIME"
```
The paired `live_speaker_boundary_evidence_lab` now splits the current real-live remaining gap into
`16.74s` future shadow-probe candidates and `34.76s` blocked rows. It still marks
`publication_ready_seconds = 0.0`, so this is design evidence for the next profile, not permission
to publish more live `Me`.
`live_soft_local_speaker_boundary_shadow_lab` then tests the cheap idea of relaxing the local
speaker boundary evidence for short, low-correlation, text-unique mic fragments. The profile is
materialized as
`online_live_me_remote_overlap_filter_plus_target_me_possible_timeline_safe_audio_safe_union_soft_local_speaker_boundary_shadow_live_boundary_split_retime_v1`,
but current corpus result is `no_incremental_gain`: missing-Me remains `51.50s`, remote leak stays
`0.00s`, and contentful order mismatches stay `2`. So the next unlock is not a softer loudness
threshold; it needs new speaker/boundary evidence.
The current diagnostic `live_local_island_split_lab` narrows this further: it finds `1` candidate
batch row / `10.58s` with `5.10s` of local-island audio/text, but token-recall rejects it
(`0.143 < 0.35`). This is enough to justify a timing/speaker-evidence prototype, not enough to
publish live output. The profile-level split oracle,
`online_live_me_remote_overlap_filter_plus_target_me_possible_timeline_safe_audio_safe_union_batch_remote_forbidden_local_island_split_oracle_v1`,
is still diagnostic: on the real-live subset it keeps remote leak at `0.00s` and remains at
`51.50s` missing-Me, so it no longer proves an additional missing-Me gain. The paired retime oracle,
`online_live_me_remote_overlap_filter_plus_target_me_possible_timeline_safe_audio_safe_union_batch_remote_forbidden_local_island_retime_oracle_v1`,
now has the same real-live missing-Me ceiling (`51.50s`), so the current gap is not a proven
retime-only win.
The blocker is earlier: live candidate selection and local-speaker/boundary evidence for mixed
regions without relying on batch labels.
`scripts/report-live-boundary-island-micro-asr-lab.py` is the first diagnostic prototype for that
top unit. It re-decodes the `5.10s` local island from live chunk audio and batch-reference mic
sources, writes `sessions/_reports/live-pipeline/live_boundary_island_micro_asr_lab.*`, and keeps
`promotion_allowed = false`. Current corpus evidence finds `1` live alignment candidate / `5.10s`:
the best live chunk attempt improves batch-token recall from `0.154` to `0.385` with remote
similarity `0.236`; the best batch-reference attempt reaches `0.462`. This proves the direction is
worth keeping, but it is still diagnostic-only and adds `0.0s` publication-ready live text. The
candidate is now also materialized as the lab-only shadow profile
`online_live_me_remote_overlap_filter_plus_target_me_possible_timeline_safe_audio_safe_union_live_boundary_micro_asr_lab_shadow_v1`.
Corpus result: it adds `1` micro-ASR turn / `5.10s`, lowers missing-Me from `86.85s` to `76.27s`,
keeps measured remote leak at `0.00s` and keeps contentful order mismatches at `2`. It is explicitly
not live-implementable because candidate selection still depends on lab/batch evidence.
The first live-only candidate lab now selects `99.40s` of suppressed mic candidates using only
live-available gates; `83.04s` are local or mixed, but `16.36s` are still remote-risk
(`precision_proxy = 0.835412`). This is useful evidence for the next implementation, but not enough
for publication. Its stricter `strict_zero_remote_risk_text_audio_v1` profile selects `36.12s` with
`0.00s` remote-risk under batch evaluation. That strict policy is now materialized as
`online_live_me_remote_overlap_filter_plus_target_me_possible_timeline_safe_strict_live_only_local_island_v1`:
after deduplication against existing live/Target-Me turns it adds `0.00s`, leaves missing-Me at
`117.57s`, keeps remote leak at `0.00s`, and remains blocked by the same `4` contentful order
mismatches / `41` non-passing gates. The combined
`online_live_me_remote_overlap_filter_plus_target_me_possible_timeline_safe_audio_safe_union_strict_live_only_local_island_v1`
profile adds the same `52.76s` as `audio_safe_union_v1`, and corpus missing-Me stays at `104.19s`
with `0.00s` remote leak. The `live_strict_local_island_shadow_delta_lab/v1` block records this as
`0.00s` incremental strict turns and `13.38s` closed missing-Me, with a negative net delta because
the current relaxed boundary profile is already stronger. So the next useful work is not
collecting more recordings or adding another publication threshold; it is online timing and
speaker-boundary evidence for still-uncovered mixed regions.

The micro-ASR path now also has a live-only candidate mode:

```bash
scripts/report-live-boundary-island-micro-asr-lab.py \
  --candidate-source live-only \
  --max-candidates 10 \
  --source-scope live
```

It writes `sessions/_reports/live-pipeline/live_boundary_micro_asr_live_candidates_lab.*` and
feeds the live-implementable shadow profile
`online_live_me_remote_overlap_filter_plus_target_me_possible_timeline_safe_audio_safe_union_live_boundary_micro_asr_live_only_shadow_v1`.
Current corpus result: the lab finds `3` alignment candidates / `13.76s`, but after deduplication
and timeline safety the materialized profile adds `0.00s`; it keeps missing-Me at `86.85s`, remote
leak at `0.00s` and contentful order mismatches at `2`. This is a useful negative result: batch
selection proved the micro-ASR idea has a `5.10s` ceiling on the current corpus, but live-only
candidate selection is still not precise enough to close that gap. The next useful direction is
remote-forbidden evidence that can find local islands not already covered by Target-Me/audio-safe
materialization.

The same script can explain the current duplicate-heavy local-recall blocker directly:

```bash
scripts/report-live-boundary-island-micro-asr-lab.py \
  --candidate-source live-duplicate-heavy \
  --source-scope live

scripts/report-live-boundary-island-micro-asr-lab.py \
  --candidate-source blocker-analysis \
  --source-scope live
```

`live-duplicate-heavy` is the first live-only selector for rows where mic text mostly duplicates
overlapping remote text but audio evidence is low-correlation enough to deserve a split probe. It
writes `live_duplicate_heavy_micro_asr_live_candidates_lab.*`. Current evidence: `4` live-selected
rows, `3` micro-ASR split candidates / `12.00s`, `promotion_allowed = false`.
`blocker-analysis` reads `capture_safe_candidate_local_recall_blocker_analysis` from the live corpus
report and writes `live_duplicate_heavy_micro_asr_lab.*`; it is batch-informed and remains a ceiling
check, not a live-implementable profile.

The corpus report now also writes
`live_mixed_speaker_boundary_voice_coverage_lab/v1`. It checks whether the remaining mixed/speaker
blocker is already covered by existing Target-Me voice rows. The Target-Me audit can now be run
with `--include-remaining-gap`, which feeds the current best-live-implementable remaining mixed
intervals into the same voice backend. It can also be run with `--fallback-persistent-profile`,
which copies historical persistent Target-Me classifications into sessions where same-session
enrollment is not ready. That fallback is diagnostic only and never creates publication candidates.
Current corpus result after the diagnostic materialization: `5` rows / `25.32s` remain in scope,
Target-Me coverage is available for the whole set, `0.32s` are already materialized in the
remote-guarded voice-boundary shadow profile, and `25.00s` remain weak or ambiguous voice evidence.
More recordings are not required for this blocker. The follow-up `live_tight_voice_remote_guard_lab`
then applies stricter voice/remote thresholds and finds no publishable candidate: candidate seconds
`0.00`, blocked seconds `25.00`, top blocker `blocked_target_me_audit_not_same_session_ok`
(`13.94s`).

The next diagnostic layer is `live_same_session_voice_disambiguation_lab/v1`. It explains why the
tight voice/remote guard cannot publish the remaining mixed rows. Current evidence is blunt:
`25.00s` are classified as `needs_same_session_local_only_enrollment_probe`, because the live
Target-Me enrollment lab has `0` positive same-session `Me` examples in the affected sessions. The
next practical step is a causal local-only voice enrollment probe built from high-confidence mic
evidence, not more recordings and not a looser publication gate.

That probe is available as:

```bash
.venv/bin/python scripts/report-live-local-only-enrollment-probe.py --method resemblyzer_dvector
```

It reads the same-session `speaker_state.jsonl`, uses high-confidence `local_only` mic intervals as
positive seed audio and `remote_only` intervals as negative audio, and writes
`sessions/_reports/live-pipeline/live_local_only_enrollment_probe.json`. On the current affected
sessions it finds ready local-only enrollment seeds in all `3` sessions (`144.00s` accepted positive
audio total) and supports `24.52s` of the `25.00s` blocked mixed rows. The remaining unsupported row
is the low-value `0.48s` tail. The next step is to materialize a diagnostic local-only-seed mixed-row
shadow and run the normal parity gates; promotion stays blocked meanwhile.

The follow-up `live_only_retime_boundary_candidate_lab/v1` tests this more directly against the
current best-live-implementable remaining gap. Strict zero-remote anchors are safe but do not touch
that gap yet: `0.00s` missing-Me overlap, `0.00s` remote-risk. The best relaxed probe,
`relaxed_audio_text_anchor_oracle_gap_probe_v1`, reaches `18.69s` of missing-Me overlap, close to
the oracle-sized gap, but also brings `27.20s` of remote-risk. That makes the next target sharper:
use a remote-forbidden context/boundary gate around relaxed anchors. Relaxed anchors must not be
published as-is, and more recordings are not the current unlock.
The same lab now includes an evaluation-only ceiling,
`relaxed_audio_text_anchor_remote_forbidden_trimmed_zero_remote_evaluated_gate_v1`: after trimming
remote-forbidden intervals and accepting only zero-remote-risk groups under batch evaluation, it
recovers `14.79s` missing-Me with `0.00s` remote-risk from `31.28s` of candidate span. This is not a
publication rule; it is the comparison target for the live classifier.
The first strict live-only classifier,
`relaxed_audio_text_anchor_remote_forbidden_boundary_classifier_v1`, approximated the zero-risk
ceiling without batch labels but was too conservative after publication: it added only `1.48s`.
The current relaxed materialized profile,
`online_live_me_remote_overlap_filter_plus_target_me_possible_timeline_safe_remote_forbidden_relaxed_boundary_classifier_v1`,
keeps the same remote-forbidden multi-cut structure, accepts less mic-dominant anchor pieces, and
adds `4.10s` of suppressed-mic Me turns. Corpus missing-Me is now `100.23s`, measured remote leak is
still `0.00s`, and contentful order mismatches stay at `4`. So this is real progress, but still a
shadow-only profile. The next useful work is stronger online timing/local-speaker evidence for the
remaining boundary turns, not more recordings or a broader threshold.

Order-risk diagnostics now separates strict reorder from batch-interval overlap ambiguity. The
current real corpus has `28` strict live order mismatches and `6` batch-overlap ambiguity rows; the
same-role slice has `8` strict mismatches and `1` overlap ambiguity; the contentful same-role slice
still has `4` strict mismatches and `0` overlap ambiguity. The ambiguity rows stay audited, but they
do not count as strict reorder. Live remains shadow-only because local recall and strict contentful
order are still not good enough.

To inspect whether suppressed live mic segments contain your voice:

```bash
jq -r '.real_blocker_triage_summary.by_category.live_local_recall_gap.sessions[]?' \
  sessions/_reports/live-pipeline/live_corpus_gates_report.json |
  sed 's#^#sessions/#' |
  xargs .venv/bin/python scripts/audit-live-local-recall-target-me.py \
    --method resemblyzer_dvector \
    --include-remaining-gap \
    --fallback-persistent-profile \
    --max-items 80 \
    --no-progress

less sessions/_reports/live-local-recall-target-me/live_local_recall_target_me_corpus_report.md
```

`--include-remaining-gap` defaults to the current direct runtime-causal profile
`live_runtime_causal_target_me_direct_v1`; pass `--remaining-gap-profile` only when comparing a
different shadow profile deliberately.

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
live use; batch remains authoritative. The report also writes
`capture_safe_candidate_order_risk_triage`; if candidate order-risk rows are advisory-only, strict
order gates still stay red, but `objective_next_focus` can move to the next hard candidate blocker
instead of chasing old or weak-match order examples first. Older reports may still expose
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
murmurmark next "$SESSION"
murmurmark review next "$SESSION"

# First close only high-confidence generated suggestions, then inspect the exact manual remainder:
murmurmark review suggested "$SESSION"
murmurmark review suggested apply "$SESSION"

# If manual review is still required, listen/edit the generated answer sheet, then:
murmurmark review first-lane --session "$SESSION"
murmurmark review lane apply first --session "$SESSION"
murmurmark review apply --session "$SESSION"
murmurmark status "$SESSION"
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
murmurmark review suggested apply "$SESSION"
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
murmurmark finish "$SESSION"
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
main `whisper.cpp` transcript. The normal post-recording pipeline first applies cheap conservative
cleanup, rebuilds the review pack from the residual queue, and then audits up to `80` items using
`mic_clean + remote`. This keeps the useful broad coverage while avoiding two redundant source
decodes per item. Use `murmurmark process "$SESSION" --stronger-audio-judge-exhaustive` only for a
deliberate four-source diagnostic run; compatible cached rows are reused in either mode.

## Command Reference

Most users should need only these commands:

```bash
murmurmark doctor
SESSION="sessions/$(date +%Y-%m-%d_%H-%M-%S)"
murmurmark record --out "$SESSION" --target-bundle system
murmurmark process "$SESSION"
murmurmark next "$SESSION"
murmurmark status "$SESSION"
murmurmark notes "$SESSION" --kind verdict
murmurmark notes "$SESSION"
murmurmark transcript "$SESSION"
murmurmark review next "$SESSION"
murmurmark finish "$SESSION"
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

- stabilize the current production path first: set `SESSION`, run
  `record --out "$SESSION" --target-bundle system`, then `process`, `next`, `status`, `finish`
  against the same session;
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
  `record --experiment live-shadow-v1` and committed-PCM experiment segments;
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
   chunk fallback for older artifacts. A focused materialization of the current best profile showed
   that the previous advisory-only order result came from missing profile artifacts. The blocking
   rows have now been repaired with causal token-density boundary timing and a short-phrase temporal
   matcher. Current next focus is `fix_live_local_recall_gap`: no additional recording is required,
   and the best profile has `0` gate-blocking / `5` advisory contentful order rows across all real
   live sessions. The active capture-safe unlock slice has `0` blocking / `2` advisory rows, while
   one historical full-corpus triage row stays blocking outside that slice. Segment-level
   parity still exposes ordering drift, suspected remote leakage in live `Me`, and lost local speech
   on controlled real evidence. The current real corpus has `60` base order mismatches: `30`
   same-chunk/same-source, `22` same-chunk/cross-source, `7` cross-chunk and `1` overlap-context
   mismatch. Primary risk is split into `19` role-conflict/remote-leak, `21` weak-match possible
   false positives, `9` same-source timeline reorders, `8` cross-source timeline reorders and `3`
   cross-chunk timeline reorders. The contentful same-role slice narrows this to `6` order-risk
   examples, with `3` unambiguous rows in the base comparison. The current micro-ASR profile
   leaves only advisory timing/match ambiguities in the active capture-safe path and clears its
   blocking order gate. Remote-gap token trimming closes two live-visible Target-Me rows (`15.38s`)
   without increasing remote leakage or order risk. Focused live-only micro-ASR closes the third
   row / `4.68s`, while its duplicate guard rejects candidates already covered by a base turn. The
   historical direct runtime results reduced missing Me, but the first fresh pre-stop real meeting
   exposed worse remote/order behavior. Direct Target-Me is therefore `regression_detected` and the
   aggregate best live-implementable policy is again the base remote-overlap filter. The next run
   must validate the lag-aware worker; local recall and remote leakage remain parallel blockers.
   The corpus report now lists concrete
   `capture_safe_evaluable_local_recall_gap_examples` for the fix. Most missing Me seconds are
   visible in suppressed mic chunks. A text-only segment rescue is now recorded as diagnostic
   metadata (`6` real live candidate chunks / `31` candidate segments in the current corpus) but is
   not published into the live draft, because it can reintroduce remote as `Me`. The next
   implementation target is a stronger audio/evidence gate for local speech inside suppressed mic
   chunks. The corpus report now also labels suppressed mic ASR segments against batch:
   `47` Me-dominant segments / `149.62s` and `44` mixed segments / `199.92s` in real live runs.
   A rescue policy lab is now part of the report: current text-only rescue would recover only
   `152.6s` local speech while risking `73.62s` remote leak; strict unique-token text rescue is also
   unsafe (`143.52s` local / `118.74s` remote-risk). `remote_silent_text_v1` is much safer
   (`34.16s` local / `2.58s` remote-risk) but low-recall. The first audio/evidence policy lab shows
   `audio_mic_dominant_v1` has `24.0s` local / `0.0s` remote-risk, while `audio_safe_union_v1`
   recovers `68.42s` missing-Me at `2.58s` remote-risk. Fresh live chunks can now expose that policy
   as `live_rescue_shadow`: `2` real-live chunks / `9` segments, `45.36s` missing-Me recovered,
   `350.36s` missing-Me still left, `0.0s` measured remote-risk and `34` segment-level order
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
   `audio_safe_union_v1` is safe but leaves `278.52s` missing-Me, while `audio_low_corr_text_guard_v1`
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
   `278.52s` missing-Me with `0.00s` remote leak, but still has `4` contentful order mismatches and
   `41` non-passing gates. The new
   `online_live_me_remote_overlap_filter_plus_target_me_possible_timeline_safe_remote_forbidden_relaxed_boundary_classifier_v1`
   shadow gets to `100.23s` missing-Me with the same `0.00s` remote leak and `4` contentful order
   mismatches. The current local-speaker boundary shadow
   `online_live_me_remote_overlap_filter_plus_target_me_possible_timeline_safe_audio_safe_union_local_speaker_boundary_shadow_v1`
   improves this to `86.85s` missing-Me, still with `0.00s` measured remote leak and `4`
   contentful order mismatches. Its residual gap splits into `73.08s` visible without Target-Me
   evidence and `13.77s` not visible in suppressed mic. The main remaining opportunity is now local-speaker
   evidence for visible suppressed mic regions that have no Target-Me policy. The remaining-gap
   evidence also shows `remote_dominant` (`32.90s`), `mixed` (`29.28s`),
   `me_dominant` (`24.28s`) and `known_hallucination` (`12.42s`) suppressed-mic labels, so the
   next profile should improve speaker evidence rather than just loosen audio/text gates.
   Actionability now points first to mixed-region segmentation:
   `25.32s` remain in the mixed/speaker-boundary subset. The actionable subset is narrower:
   `10.58s` are `local_island_split_candidate`; duplicate-heavy, remote-dominant and short tails
   stay blocked until stronger evidence exists. The local-island split lab now finds only one
   `10.58s` batch candidate with `5.10s` of local-island evidence, and rejects it by token recall.
   The diagnostic split/retime oracle now shows only a `1.16s` ceiling (`86.85s -> 85.69s`), but
   live remains blocked by order/readiness gates. The next implementation should focus on tightening
   voice/remote-guard evidence for the remaining mixed rows, not broad rescue or more recordings.
   The live-only candidate
   lab selects `99.40s`, but still carries `16.36s`
   remote-risk. Its strict zero-risk profile selects `36.12s` with `0.00s` remote-risk. The strict
   materialized shadow adds `0.00s` after deduplication against existing live/Target-Me turns, and
   the combined strict+audio-safe shadow adds the same `52.76s` as `audio_safe_union_v1`; the current
   best live-implementable profile leaves `51.50s` missing-Me with `0.00s` remote leak. This proves the next blocker is stronger
   voice/remote-guard evidence for mixed rows, not another broad add-more-local-speech threshold.
   The remote-forbidden boundary classifier is now also materialized as
   `online_live_me_remote_overlap_filter_plus_target_me_possible_timeline_safe_remote_forbidden_boundary_classifier_v1`;
   the guarded version adds `1.48s`, keeps remote leak at `0.00s`
   and does not increase contentful order mismatches. Historical boundary-retime and split/retime
   labs remain useful as diagnostics, but they are not promotion candidates because they depend on
   batch-derived timing or lose local speech in some cases. Focused materialization disproved the
   earlier advisory-only result and then supplied the evidence needed to repair the blocking rows.
   The current token-density profile leaves `0` blocking / `2` advisory rows in the active
   capture-safe path. The refreshed historical full-corpus triage retains one blocking row, so it
   stays visible as negative evidence. The next active unlock nevertheless moves to local recall
   without weakening remote-forbidden guards or batch authority.
   The current mixed/speaker-boundary queue is now `25.32s`: `0.32s` has been materialized in a
   diagnostic remote-guarded voice-boundary profile, and `25.00s` remain weak or ambiguous. The
   tight voice/remote guard lab finds `0.00s` safe candidates: `13.94s` are blocked by persistent
   Target-Me fallback, `10.58s` by low Target-Me-vs-remote delta, and `0.48s` as a low-value tail.
   The top unit is still `local_island_split_candidate` (`10.58s`), but publication needs stronger
   same-session voice disambiguation rather than whole-row rescue.
   The first micro-ASR lab for this unit now writes
   `live_boundary_island_micro_asr_lab.*`. It finds `1` live alignment candidate / `5.10s` and
   improves the top island's batch-token recall from `0.154` to `0.385` from live chunk audio
   (`0.462` from batch-reference mic), with publication still blocked. The candidate is now
   materialized as a diagnostic lab-shadow profile; it closes `10.58s` of the gap versus the best
   live-implementable profile without measured remote leak. The first live-only micro-ASR shadow is
   also materialized, but currently adds `0.00s` after deduplication and safety gates. This makes the
   next implementable step better online speaker/boundary evidence around mixed regions, not more
   recordings or looser micro-ASR thresholds.
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
incomplete sessions must not expose normal notes/transcript handoff, and `status` must agree with
`next` for the checked session.

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
