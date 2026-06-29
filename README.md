# MurmurMark

MurmurMark is a local-first macOS meeting capture and notes pipeline.

It records the user's microphone and the selected meeting application's audio into separate local tracks, without virtual audio devices, without changing the meeting app's audio routing, and without uploading raw audio by default. The recording package is then processed into a speaker-aware transcript, evidence-backed meeting notes, and documentation updates under an explicit privacy policy.

The repository now contains a Swift CLI that wraps the local recording and processing pipeline end to
end: capture, Echo Guard preprocessing, `Me`/`Colleagues` transcription, timeline repair, audit
cleanup, review handoff, quality verdicts, evidence-backed extractive notes, export bundles and
retention planning. Full diarization and generative synthesis remain documented future work. When
synthesis still has review risk, its handoff points to `murmurmark review next` and does not
advertise export as the next step.

## Current CLI

### Local Install

For normal use, install a local wrapper once:

```bash
cd murmurmark

scripts/install-local.sh
export PATH="$HOME/.local/bin:$PATH"

murmurmark doctor
murmurmark config print
```

The wrapper points `MURMURMARK_HOME` at this checkout, so `murmurmark process`,
`murmurmark report`, `murmurmark audit`, `murmurmark review`, `murmurmark corpus` and `murmurmark export`
can be run from any directory. During development, `.build/debug/murmurmark` and
`swift run murmurmark ...` still work.

`murmurmark doctor` checks the current CLI home, config, core scripts, `ffmpeg`/`ffprobe`,
`whisper-cli`, Python runtime and modules, the configured whisper.cpp model, and macOS recording
permissions. It reports warnings without failing by default and prints the next normal CLI commands;
use `murmurmark doctor --strict` in automation when required checks must fail the command.

Normal CLI loop:

```bash
murmurmark doctor
murmurmark record --target-bundle system
murmurmark sessions
murmurmark process latest
murmurmark next latest
murmurmark next corpus
murmurmark open latest --kind notes
murmurmark status latest
# Follow printed review commands when the gate is review_first.
murmurmark export latest --format markdown --include-json
murmurmark retention plan latest
```

`status`, `report`, `open`, review, audit, cleanup/repair, synthesis, notes/transcript, export and retention commands
all print the next safe command for the current session state, so the terminal output is the main handoff.
For normal summary output, use the final `next: ...` line as the primary command to run next.
Machine-readable modes such as `--path-only`/`--command-only` and streamed content modes such as
`--cat` keep their single-purpose output clean.
`next` is the shortest answer when you only need the one command to run now.
`open` is the shortest answer when you need to inspect the selected local output: notes, transcript,
quality verdict, readiness or audit reports.
`sessions` is the quick picker for recent recordings: it shows each session path, label, time,
duration, readiness status, review burden and the next safe command. Use `--status review_required
--next-only` to print the current review queue as copyable commands, `--status exportable --next-only`
for sessions still waiting for export, or `--status exported --next-only` for post-export retention
steps. Use `--json` when an agent needs a machine-readable queue snapshot.
`status` is the quickest dashboard for already-generated readiness; `report` refreshes readiness first.
If readiness is not present yet, `status` and `next` point to `murmurmark process SESSION`.
Use `murmurmark next corpus` after corpus reports exist when you need one concrete next command
across the whole working-meeting corpus; add `--refresh` to rebuild session-quality and
operational-readiness reports first. If the recommended review lane pack is already built, it points
to the prepared audio/Markdown/answer-sheet handoff instead of rebuilding the same pack.
After a successful default export, `status`, `sessions` and `next` follow the export manifest and
point to retention planning; `next corpus` does the same when the corpus report's first export
command targets an already-exported session. Pass `--export-manifest` to `next` when the bundle was
written outside the default `exports/private/` directory.
`status` and `report` start with a short status such as `exported`, `exportable`, `review_required`, `incomplete` or `blocked`, plus
`recommended_next` and `handoff` commands for opening the selected notes, transcript and verdict.

### Local Release Bundle

To create an inspectable local bundle from tracked project files:

```bash
scripts/build-release-bundle.sh --verify

BUNDLE="$(find dist/release-bundles -maxdepth 1 -type d -name 'murmurmark-*' | sort | tail -1)"
MURMURMARK_PYTHON="$PWD/.venv/bin/python" "$BUNDLE/bin/murmurmark" doctor --strict
cat "$BUNDLE/release-manifest.json"
```

The bundle contains the release binary, wrapper, scripts, docs, examples, helper sources,
`murmurmark.config.example.json` and a manifest. It intentionally excludes `sessions/`,
`exports/private/`, raw audio, `.venv`, models, weights and `murmurmark.config.json`.
`--verify` runs the bundled wrapper through `doctor --strict`; use `--python PATH` to point at a
prepared Python environment when `.venv` is not present.
See [docs/contracts/release-bundle.md](docs/contracts/release-bundle.md).

### Public Repository Readiness

Before sharing or publishing the repository:

```bash
scripts/check-open-source-readiness.sh
```

This gate fails on tracked real sessions, raw audio, local configs, private prompts/glossaries,
personal paths and workspace-specific domain packs. The repository uses the MIT license; set a public
security contact before a public release.
See [docs/project/open-source-readiness.md](docs/project/open-source-readiness.md).

For the shortest local CLI handoff regression, run:

```bash
scripts/smoke-cli-handoff.sh
```

It builds a tiny processed fixture and then uses only `murmurmark ...` commands for
`status -> report -> next/open -> export -> retention`.

### End-to-End From an Existing Recording

Use this when the session directory already exists and raw recording is complete.

```bash
cd murmurmark

SESSION=./sessions/<session>

git pull
murmurmark process "$SESSION" --force-asr

murmurmark status "$SESSION"
murmurmark notes "$SESSION" --kind verdict
murmurmark notes "$SESSION"
murmurmark transcript "$SESSION"
murmurmark export "$SESSION" --format markdown --include-json
murmurmark retention plan "$SESSION"
murmurmark retention payload "$SESSION"
less "$(murmurmark transcript "$SESSION" --path-only)"
```

Use `--force-asr` when you want a cold rerun from the raw `CAF` tracks. Omit it for the normal
incremental path, where cached raw ASR JSON is reused when its model, language, prompt and windowing
metadata still match.

Local defaults come from `murmurmark.config.json` when it exists:

```bash
cp murmurmark.config.example.json murmurmark.config.json
$EDITOR murmurmark.config.json
murmurmark config print
```

Explicit CLI flags still win over config values.

For a regression set or several real meetings, build a private quality summary under the ignored
`sessions/_reports/` tree:

```bash
murmurmark corpus report

less sessions/_reports/session-quality/session_quality_report.md
```

When corpus order, remote-leak, gate and readiness reports already exist, `murmurmark corpus report`
also prints their short summaries without rebuilding them. Use `murmurmark corpus process all` when
those reports need to be rebuilt. Corpus commands keep per-session helper output quiet and print one
CLI summary with the next useful command. `murmurmark report corpus` is narrower: it refreshes
session-quality and operational-readiness without rebuilding heavier corpus diagnostics. When
review work remains, the CLI summary also prints `focus_session`, the blocking label/reason and the
first `murmurmark review ...` commands for that session. Its readiness block also prints
`sessions_in_scope` and `sessions_excluded`, so the corpus total can include old diagnostic captures
without making them look like working meetings. It also prints `review_actions` and
`grouped_review_rows`: the first is the number of answer-sheet decisions after safe grouping, the
second is how many raw review rows were packed behind those decisions. Corpus readiness and
`next corpus` summaries also print a `use` block: whether any notes are already usable, whether the
whole corpus is ready for medium-risk meetings, how many sessions still require review or processing,
and the minimum next command. They also end with the final copyable `next: ...` command.

Retention is explicit and local:

```bash
murmurmark retention plan "$SESSION"
murmurmark retention payload "$SESSION"
less "$SESSION/derived/retention/retention_plan.json"
less "$SESSION/derived/retention/provider_payload_manifest.json"
```

Both commands print a short handoff summary: selected plan or payload manifest, retention status,
raw-audio action counts, blockers/warnings, export-manifest state when present, `open` commands for
the local manifests, and the next safe command as a final copyable `next: ...` line. If readiness is
not exportable yet, or if only a forced export with blockers exists,
retention points back to the current `process`, `review` or successful `export` step instead of
suggesting a blocked privacy action.
`retention_plan.json` and `provider_payload_manifest.json` also store `recommended_next`,
`next_commands` and `open_commands`, so post-export privacy steps are machine-readable.

The tracked default policy is [examples/retention-policy.local-first.json](examples/retention-policy.local-first.json).
It keeps raw audio, forbids copying raw audio to export bundles and disables external providers.
Raw deletion is available only with a policy that requests deletion plus
`murmurmark retention apply "$SESSION" --confirm-delete-raw` after a successful export manifest.

To turn audited sessions into a reusable private regression set for future cleanup and audio-judge
work, build a corpus from existing audio-review audits:

```bash
murmurmark corpus build \
  sessions/<session-1> \
  sessions/<session-2> \
  --per-label 16 \
  --max-items 160

murmurmark corpus evaluate

murmurmark corpus train-audio-judge

murmurmark corpus taxonomy

murmurmark corpus gate

murmurmark corpus gate \
  --write-baseline sessions/_reports/corpus-gates/baseline.local.json

murmurmark corpus gate \
  --baseline sessions/_reports/corpus-gates/baseline.local.json

murmurmark review next latest
murmurmark review first-lane
murmurmark review workspace
murmurmark review agent
```

The step-by-step corpus commands are also self-guiding: `corpus build` points to `corpus evaluate`,
`corpus evaluate` points to `corpus train-audio-judge`, and `corpus train-audio-judge` points to
`corpus taxonomy`, while each step prints the local report to read.

For the normal full refresh, use one command:

```bash
murmurmark corpus process all --per-label 16 --max-items 160
```

This command completes the refresh and prints summaries even when corpus gates are currently
`failed`; use the printed review/process next commands to reduce the blockers. Run
`murmurmark corpus gate` separately when you need a strict non-zero gate for CI or release checks.
Operational readiness prefers a concrete `murmurmark process sessions/<id>` command only for the
first incomplete high-value session. Complete but risky sessions stay in the review lane commands;
the report does not ask you to rerun them as if artifacts were missing. Obvious diagnostic/smoke
sessions such as `audio-input-*`, `*-talk-routed`, `smoke` and `test`, plus known-duration captures
shorter than 60 seconds, are excluded from this operational scope, while their files remain available
for manual debugging.
`corpus gate` writes `sessions/_reports/corpus-gates/corpus_gates_report.*`. It also reads the
local-recall and remote-leak corpus reports when they exist. Complete sessions with blocking
local-recall risk fail the gate; pending remote-leak segment queues are warnings, not hard failures.
The JSON stores `recommended_next`, `next_commands` and `open_commands`, and the CLI prints
`read: less ...` plus a final `next: less ...` line before returning a non-zero status for a failed
strict gate.
`corpus taxonomy` writes `sessions/_reports/audio-error-taxonomy/audio_error_taxonomy_report.*`.
Use it after `corpus train-audio-judge` to see which error classes are already safe cleanup
candidates, which are mark-only, which need better labels, and which sessions/examples should drive
the next quality iteration. The report also splits broad classes like `uncertain` into diagnostic
subtypes and writes an `action_plan`, so the next repair can target one narrow failure mode. The CLI
prints `read: less ...`, possible follow-up commands and a final `next: less ...` line because the
safe first action is to inspect the action map before changing cleanup or repair rules.
`corpus remote-leak` writes `sessions/_reports/remote-leak-segment/remote_leak_segment_corpus_report.*`.
When plans are missing, the report points to `murmurmark corpus remote-leak --plan`; when protected
local-content intervals exist in complete sessions, it points to
`murmurmark review lane check_unique_me_content --session ...`; incomplete sessions are sent back
through `murmurmark process ...` first. The CLI mirrors that decision as `recommended_next` and
the final `next: ...`; when there is no executable queue item, it points to opening the Markdown
report.
`corpus local-recall` writes `sessions/_reports/local-recall/local_recall_corpus_report.*`.
It aggregates possible lost-`Me` and local-recall review items across the corpus; `--audit` refreshes
per-session local-recall audits first. Its CLI output uses the same `recommendation`, `read`,
`recommended_next` and final `next: ...` handoff as other corpus diagnostics.
`corpus local-recall-repair` writes
`sessions/_reports/local-recall-repair/local_recall_repair_corpus_report.*`. It summarizes
`local_recall_repair_v1` reports across sessions; `--repair` refreshes the candidate repair profile
first. Inserted `Me` turns remain explicit `needs_review`, and the report does not promote the
profile into `auto`. When inserted repairs exist in complete sessions, the corpus report includes
the first `murmurmark review lane check_local_recall --session ...` command for review; incomplete
sessions are sent back through `murmurmark process ...` first. The CLI prints that command as the
primary next action and keeps the Markdown report as the read target. The repair layer keeps micro-ASR
diagnostics and can recover short boundary-shifted rows when Whisper recognized text but the row
midpoint landed just outside the local island.
`murmurmark corpus process all` rebuilds per-session remote-leak plans before session-quality and
corpus aggregation. Use `corpus remote-leak --plan` when you want to refresh only that queue; the
aggregate report prints that command when plans are missing.
`passed_with_warnings` means the hard no-regression gates are green, but some historical sessions or
review queues still need cleanup before the whole repository is operationally ready.
The optional baseline is private generated state under ignored `sessions/_reports/`.
Use it before risky cleanup or ASR changes to catch regressions against the current corpus:
ready sessions, review burden, local-recall blockers/lost seconds and protected remote-leak queue
growth.

The lower-level scripts remain available for debugging specific files:

```bash
.venv/bin/python scripts/report-operational-readiness.py

# The report also writes next_commands: corpus process for structural blockers,
# otherwise the first review lane/workspace commands.

.venv/bin/python scripts/build-review-plan.py

.venv/bin/python scripts/review-decisions-cli.py \
  --template sessions/_reports/review-plan/review_decisions.template.jsonl \
  --out sessions/_reports/review-plan/review_decisions.jsonl

.venv/bin/python scripts/apply-review-decisions-batch.py \
  --decisions sessions/_reports/review-plan/review_decisions.jsonl \
  --review-template sessions/_reports/review-plan/review_decisions.template.jsonl \
  --synthesize

less sessions/_reports/regression-corpus/regression_corpus.md
less sessions/_reports/regression-corpus/regression_corpus_evaluation.md
less sessions/_reports/audio-judge-v0/audio_judge_v0_report.md
less sessions/_reports/operational-readiness/operational_readiness_report.md
less sessions/_reports/review-plan/review_plan.md
```

### End-to-End From a New Recording

This is the current practical path from a new local recording to the best available transcript candidate.
The `record` command keeps running until `Ctrl-C`; the following commands continue after the recording stops.
After a successful recording, the CLI prints `SESSION="..."`, `recommended_next` and the exact
`murmurmark process ...` command for that session.

```bash
cd murmurmark

git pull
murmurmark doctor
murmurmark config print

murmurmark record --target-bundle system

murmurmark process latest

murmurmark status latest
murmurmark open latest --kind notes
murmurmark open latest --kind transcript --command-only
murmurmark notes latest --kind verdict
murmurmark notes latest
murmurmark transcript latest
murmurmark export latest --format markdown --include-json
murmurmark retention plan latest
less "$(murmurmark transcript latest --path-only)"
```

`murmurmark process` is the normal post-recording command; internally it calls
`scripts/run-session-pipeline.py`. The runner calls Echo Guard,
whisper.cpp transcription, shadow timeline repair, local-recall audit, transcript-order audit,
group-overlap audit, audio-review audit,
`audit_cleanup_v1/v2`, optionally `audit_cleanup_v3/v4` when the local audio-judge queue exists,
and extractive synthesis, then writes
`derived/pipeline-run/pipeline_run_report.json` and `derived/readiness/session_readiness.md`.
While it runs, it prints each stage as `[run]`, `[passed]`, `[failed]` or `[skip]` with duration.
Long stages also emit a heartbeat such as `[run] transcribe_current still running (120.4s)`.
Use `--progress-interval-sec 0` to disable these heartbeat lines. `--plan-only` prints a compact
`pipeline_plan` block with enabled/skipped steps, heavier stages, expected output files,
`run_command` for executing that plan and `current_next` for the current session state instead of
the live stage log; the following readiness block is labelled `existing_readiness` because no new
processing was run. The same plan metadata is written to
`derived/pipeline-run/pipeline_run_report.json`, along with `recommended_next`, `next_commands` and
`open_commands`, so agents can inspect it without parsing terminal text. After every `process` run,
the final line is a single copyable `next: ...` command derived from the refreshed readiness.
Read `session_readiness.md` first, or run `murmurmark status SESSION` for the terminal version.
Use `murmurmark report SESSION` when readiness should be refreshed. The CLI prints a short status,
`recommended_next`, a `use` block with read/export booleans, blocker and minimum step, the session
use gate, selected profile, review burden, synthesis review item summary, next CLI commands,
`handoff` open/export commands, and links to the transcript, notes, quality verdict and audio-review
report, then ends with the final copyable `next: ...` command.
`recommended_next` prefers executable `murmurmark ...` actions from `next_commands`; read-only
commands such as `less ...` remain visible under `next` and `open`. The underlying
`session_readiness.json` stores the same `recommended_next`, `next_commands` and `open_commands`,
so agents can continue without parsing terminal output.
`murmurmark review next SESSION` is the short terminal handoff for that same information: it refreshes
session readiness, builds a session-local review plan when review is needed, and prints the
review-specific next commands. If the current readiness gate does not require review, it ignores any
stale session-local review plan and points back to `murmurmark next SESSION` / `murmurmark status
SESSION` instead. When a review plan exists for a review-required session, it also prints
`first_lane_flow` for the current blocker, `quick_lane_flow` for the fastest confirm/drop pass when
that is a different lane,
and `workspace_flow` for reviewing all lanes. It also prints the recommended first lane reason, the
fastest quick lane and the estimated remaining queue after the first lane, so the normal order is
visible without opening `review_plan.json`. When local Markdown reports already exist, `review next`
also prints `open` commands for readiness, review plan, review progress and operational readiness.
For `review_first` sessions, those next commands point to `murmurmark review next ...`,
`murmurmark review first-lane --session ...`, `murmurmark review workspace --session ...`,
`murmurmark review lane apply ...`, `murmurmark review workspace apply`,
`murmurmark review progress --session ...`, and `murmurmark review apply`.
Use `murmurmark review lane check_local_recall --session ...` when you need a specific lane rather
than the automatically recommended first lane.
`transcript.md` is the stable baseline output. Profile transcripts such as `transcript.shadow_v2.md`
or `transcript.audit_cleanup_v2.md` are separate candidates; the selected profile is written to
`quality_verdict.json`.
`audit_cleanup_v5/v6` are not part of the normal single-session runner. They are corpus/review-plan
steps for already audited sessions, usually run after the private regression and audio-judge reports
exist under `sessions/_reports/`.
The normal single-session runner also builds the audit-only remote-leak segment plan after
audio-review. If a session has `remote_leak` errors or partial `remote_duplicate` rows where `Me`
may still contain unique local content, readiness links to `remote_leak_segment_repair.md` before
any future repair work.
`murmurmark audit order` finds long `Me` turns that cross a `Colleagues` turn and continue after it:
these are the main remaining risk for wrong reply order in an otherwise readable transcript. Blocking
order risks are included in readiness review burden and block export until reviewed.
`check_transcript_order` review packs include short mic/remote clips around the crossed utterances,
so the reviewer can verify chronology without opening the full order audit first.
`murmurmark repair order` writes a separate `order_repair_v1` profile for the narrow safe case where
the long `Me` turn can be split by its saved source ASR segments. It never edits baseline/shadow
profiles; if the split is not safe, it marks the affected utterance for review instead. When gates
pass and at least one repair was applied, session-quality/readiness and synthesis `auto` can select
`order_repair_v1`. Fully repaired sessions clear the order-risk burden; partial repairs keep the
remaining unsafe regions as explicit review items. The repair report stores the same
`recommended_next`, `next_commands` and `open_commands` that the CLI prints, so terminal handoff and
JSON handoff stay in sync.
`murmurmark repair remote-leak` is audit-only. It reads `audio_review_audit.jsonl`, selects
`remote_leak` rows and partial `remote_duplicate` rows that look like probable transcript errors,
and writes a segment-level repair plan under
`derived/transcript-simple/whisper-cpp/remote-leak-repair/`. It does not edit transcript profiles
or raw audio. Its main purpose is to separate plain leak evidence from leak/duplicate intervals
where `Me` still contains unique local content and whole-utterance deletion is unsafe.
The plan JSON includes `recommended_next`, `next_commands` and `open_commands`; the CLI prints that
same handoff after the command finishes.
`murmurmark corpus order` aggregates those audits across the corpus and writes the current list of
chronology regression candidates under `sessions/_reports/transcript-order/`; corpus gates read this
report and fail if a complete session still has blocking chronology risk. The report points complete
blocking sessions to `murmurmark review lane check_transcript_order --session ...`; incomplete
blocking sessions go back through `murmurmark process ...`. The CLI prints the symbolic
`recommendation`, the report-opening command, executable `recommended_next` and a final copyable
`next: ...` line. Use
`murmurmark corpus order --repair` when you want to refresh order audits for the sessions in the
current session-quality report, try conservative
`order_repair_v1` for each session, refresh session-quality, and then rebuild the corpus order
summary in one pass. The corpus report includes an `order_repair` summary with applied repairs,
cleared sessions and remaining unrepaired order risks.
`murmurmark audit group-overlaps` classifies `Me`/`Colleagues` timeline overlaps into harmful, benign
and review buckets, writes listenable clips, and does not change transcripts or quality verdicts by itself.
`murmurmark cleanup` is the conservative cleanup over audit evidence. It writes separate
`audit_cleanup_*` profiles and only drops whole `Me` utterances when the audit strongly supports remote
duplicate or ASR-noise classification. It never edits `shadow_v2`. Its cleanup report stores
`recommended_next`, `next_commands` and `open_commands`; the CLI prints the same post-cleanup handoff.
`murmurmark synthesize` then selects or accepts a dialogue profile, writes a quality verdict, and creates local extractive notes where every item cites utterance IDs.

### Command Reference

Everyday CLI commands:

```bash
murmurmark doctor
murmurmark record --target-bundle system
murmurmark process latest
murmurmark next latest
murmurmark status latest
murmurmark open latest --kind notes
murmurmark review next latest
murmurmark export latest --format markdown --include-json
murmurmark retention plan latest
```

The top-level `murmurmark --help` uses the same split: everyday usage first, quality/corpus
maintenance separately, then setup and debugging commands. The top-level help intentionally keeps
review short; use `murmurmark review --help` when you need lane/workspace/apply details.

Operational queues and quality maintenance:

```bash
murmurmark sessions
murmurmark sessions --status review_required --next-only
murmurmark sessions --status exportable --next-only
murmurmark sessions --status exported --next-only
murmurmark report corpus
murmurmark next corpus
murmurmark corpus process all --per-label 16 --max-items 160
murmurmark corpus gate
murmurmark review workspace --session latest
murmurmark review workspace apply --session latest --dry-run
```

Advanced CLI diagnostics:

```bash
murmurmark preprocess ./sessions/<session> --echo diagnostic
murmurmark preprocess ./sessions/<session> --echo clean --echo-engine local_fir
murmurmark inspect ./sessions/<session>
murmurmark inspect ./sessions/<session> --echo
murmurmark export-audio ./sessions/<session>
murmurmark repair order ./sessions/<session> --input-profile auto --output-profile order_repair_v1
murmurmark repair local-recall ./sessions/<session> --input-profile auto --output-profile local_recall_repair_v1
```

Use the longer profile/debug matrices in
[docs/runbooks/transcribe-simple-whispercpp.md](docs/runbooks/transcribe-simple-whispercpp.md) when
you need to compare one pipeline layer. The normal user path is still `process -> next/status ->
review/export/retention`.

Development and release checks:

```bash
swift build
swift run murmurmark doctor
swift run murmurmark doctor --strict
swift run murmurmark list-apps
scripts/build-release-bundle.sh --verify
scripts/check-open-source-readiness.sh
scripts/check.sh
```

Internal scripts are still available when debugging one layer or comparing a CLI wrapper with its
underlying implementation. Prefer the CLI wrappers first; call Python scripts directly only when you
need exact intermediate files or script-specific flags:

```bash
.venv/bin/python scripts/run-session-pipeline.py ./sessions/<session>
.venv/bin/python scripts/transcribe-simple-whispercpp.py ./sessions/<session> --skip-export --skip-transcribe
.venv/bin/python scripts/report-session-quality.py ./sessions/<session> --write-session-readiness
.venv/bin/python scripts/build-review-plan.py --operational-readiness sessions/_reports/operational-readiness/operational_readiness_report.json
```

When called directly, `scripts/run-session-pipeline.py` and `scripts/transcribe-simple-whispercpp.py`
resolve the MurmurMark executable as `MURMURMARK_BIN`, then `murmurmark` from `PATH`, then
`.build/debug/murmurmark` as a development fallback.

Use `murmurmark status SESSION` to inspect the existing readiness dashboard. Use
`murmurmark report SESSION` after changing review decisions, cleanup profiles, exports, retention
state, or other derived artifacts that should refresh readiness.

Development check:

```bash
scripts/check.sh
```

This builds the CLI, runs SwiftLint and verifies `inspect`/`export-audio` on a synthetic two-track session package.

The normal minimal recorder uses ScreenCaptureKit to capture system/application audio and the microphone into separate local tracks. It does not require Loopback, BlackHole or any other virtual audio device. It writes:

```text
session/
  audio/mic/000001.caf
  audio/remote/000001.caf
  derived/asr/mic.wav
  derived/asr/remote.wav
  events.jsonl
  pipeline_job.json
  session.json
```

Echo Guard M0/M0.5/M1/M2/M2.5/M3 is implemented. The preprocess step materializes ASR/AEC working audio under `derived/preprocess/audio/`, writes `echo_diagnostics.json`, `echo_segments.jsonl` and, for `--echo clean`, a conservative `echo_suppression_report.json`. Cleanup engines currently available are `linear_baseline`, `local_fir`, `speexdsp` and `webrtc-apm`; the external engines build or invoke small local helpers when their toolchains are available.

`local_fir` is the current recommended cleaner for real speaker-bleed sessions. It fits short local FIR echo models from the already separated `remote` and `mic` tracks, writes a listenable `mic_clean_local_fir.wav`, a selected `mic_role_masked_for_asr.wav`, a compact `mic_role_preview.wav` for auditioning retained mic regions, and promotes the role-masked file to `mic_for_asr.wav` only when its quality gate accepts the candidate. It also writes `derived/preprocess/mic_asr_segments/segments_manifest.json` for future chunk-based mic ASR. `reconcile-transcript` marks mic utterances that match delayed remote speech and updates `quality_report.json`. Raw capture stays untouched.

For `local_fir`, `--echo-policy preserve_local` is the default because it avoids deleting local speech in ambiguous remote-active regions. It mutes detected silence, passes local-only speech raw, and passes remote-active ambiguous chunks as mildly cleaned/flagged audio. `--echo-policy role_safe` hard-mutes only high-confidence `remote_only` regions. `--echo-policy strict_silence` mutes all remote-active regions, knowingly sacrificing overlap speech.

`export-audio` uses `derived/preprocess/audio/mic_for_asr.wav` when Echo Guard has created it; otherwise it exports the raw mic capture.

The current transcription layer is `scripts/transcribe-simple-whispercpp.py`, normally reached through `murmurmark process`. It runs `export-audio`, prepares ASR-only speech-band `mic` audio and normalized `remote` audio, calls local `whisper-cli` on short overlapping windows, creates raw segment and candidate JSON, runs timeline repair plus role reconciliation, then writes `clean_dialogue.json`, `role_decisions.json`, `overlaps.json`, `quality_report.json`, `timeline_repair_report.json`, `transcript.md` and `transcript.simple.json` under `derived/transcript-simple/whisper-cpp/resolved/`. ASR preparation never changes raw capture. See [docs/runbooks/transcribe-simple-whispercpp.md](docs/runbooks/transcribe-simple-whispercpp.md).

The first synthesis bridge is `murmurmark synthesize`. It wraps
`scripts/synthesize-simple-extractive.py`, reads only transcript-derived JSON, chooses the best safe
dialogue profile, writes `quality_verdict.json`/`.md`, and creates extractive `notes.md` plus
`evidence_notes.json` and `review_items.jsonl` under `derived/synthesis-simple/extractive/`. It does
not call an LLM and does not infer facts beyond quoted utterances. Its v3 notes path builds topic
blocks, scores action/decision/risk/question candidates, hides meeting facilitation from Markdown,
shows only selected top items, and keeps every hidden weak/process/facilitation candidate in
`evidence_notes.json`. The `murmurmark synthesize`, `murmurmark notes` and
`murmurmark transcript` CLI handoffs also print the remaining `review_items` count and the main
review item types from `quality_verdict.json`; when review work remains, their primary next command
points to `murmurmark review next`. `notes` and `transcript` read the action handoff from the same
`quality_verdict.json` and append the local `less ...` command for the selected file.
`quality_verdict.json` and `synthesis_manifest.json` store the same `recommended_next`,
`next_commands` and `open_commands`, so agents can continue the pipeline without scraping terminal
output.

`murmurmark export` creates a local user-facing bundle under ignored `exports/private/` by default.
Markdown export writes `index.md`, `quality_verdict.md`, `notes.md`, `transcript.md` and
`export_manifest.json`; `--include-json` also copies evidence JSON. Obsidian export writes one
frontmatter Markdown note plus the same manifest. Export blocks sessions whose
`derived/readiness/session_readiness.json` contains `export_blockers`, so incomplete pipelines,
hard quality failures and unfinished review do not silently become finished artifacts. A blocked
export prints `recommended_next`, structured next commands and writes `*.export_blocked.json`; `--force` keeps the
blockers in `export_manifest.json` and is meant for debugging. After a successful export, the CLI
prints `recommended_next`, the manifest path, key output files, the matching retention commands and
a final copyable `next: ...` line.
The successful `export_manifest.json` also stores structured `next_commands`, `open_commands` and
`export_commands`, so an agent can continue with retention or open the exported files without parsing
terminal output.
Forced exports with blockers point back to `process` or `review next` first and list retention only
under `debug_retention`; the manifest mirrors that split with `debug_retention_commands`.

`murmurmark audit` wraps the local audit scripts through the project Python runtime. `murmurmark audit
local-recall`, `murmurmark audit order`, `murmurmark audit group-overlaps` and `murmurmark audit audio-review` are the normal
entry points; direct Python script calls remain useful for debugging.
After every audit run, the CLI prints a compact handoff summary with the profile, key counters,
recommendation, `read: less ...` for the Markdown report, `recommended_next` and the final copyable
`next: ...` line. Risky audits point back to `murmurmark review next SESSION`; clean audits point to
`murmurmark report SESSION`.

The group overlap audit reads transcript overlaps, Echo Guard `speaker_state.jsonl`, and local audio derivatives, then writes `derived/audit/group-overlaps/`. It separates likely harmful `Me` duplicates or remote leakage from expected group-call double-talk and timing overlap. This is audit-only: no transcript, Echo Guard output, synthesis output, or `quality_verdict` is modified.

The local recall audit reads timeline-repair examples and
Echo Guard `speaker_state.jsonl`, then writes `derived/audit/local-recall/`. Its job is to explain
low `local_only_island_recall`: short or weak unrecovered islands are recorded as low-risk evidence,
and islands whose text is already covered by the remote transcript are treated as low-risk content
coverage rather than missing meeting content. Short islands that sit exactly on parent/child/remote
guard boundaries are labelled as likely harmless boundary fragments. Short acknowledgement-only
islands such as `понял` or `окей` are also low-risk audit evidence, while stronger unrecovered local
speech stays as a blocking `low_local_recall` risk. An empty timeline-repair examples file is a valid
zero-islands audit result, not a missing-input failure. It never edits transcripts.
`murmurmark corpus local-recall` aggregates those per-session audits into
`sessions/_reports/local-recall/` so possible lost `Me` regions are visible as one corpus queue.
When a complete session still has blocking local-recall evidence, the report includes the first
`murmurmark review lane check_local_recall --session ...` command. If only incomplete sessions are
left, it points to `murmurmark process ...` for the first one.

`murmurmark repair local-recall` wraps `scripts/apply-local-recall-repair.py`. It reads
`local_recall_items.jsonl`, runs micro-ASR on strong `possible_lost_me` local islands and writes a
separate `local_recall_repair_v1` profile over the best current safe profile. Inserted `Me` turns are
marked `needs_review`; baseline, cleanup and order profiles are not modified. Use this profile
explicitly when checking whether a missed short local phrase was recovered:
`murmurmark synthesize SESSION --transcript-profile local_recall_repair_v1`.
Its report carries `recommended_next`, `next_commands` and `open_commands`, and the CLI prints those
JSON commands after repair.
For short boundary islands, the repair also inspects raw micro-ASR rows. If Whisper produced text
whose timestamp overlaps or nearly touches the island but whose midpoint is outside it, the row can
be recovered through `boundary_overlap_fallback`; the attempt stays visible in
`local_recall_repair_micro_runs.local_recall_repair_v1.jsonl`.
Applied repair turns are included in the operational review queue as `local_recall_repair` items.
They use `local_recall_repair_v1` as the review input profile and allow the normal transcript
decisions: `keep_me`, `drop_me`, `needs_review` or `skip`. Corpus repair reports include
`next_commands` so inserted rows from complete sessions can be reviewed through
`murmurmark review lane check_local_recall --session ...` without opening the Markdown report first.
If a session with inserted rows is still incomplete, the report points to `murmurmark process ...`
before review. `murmurmark review agent` may clear a repair row automatically only when the inserted
phrase has strong local-only speaker-state evidence and high-confidence micro-ASR; otherwise it stays
in the normal review lane.

The transcript order audit reads `clean_dialogue` and `overlaps`, then writes
`derived/audit/order/`. It highlights long `Me` turns that wrap a `Colleagues` turn and have a
tail after that remote turn. Those regions are the most likely places where the Markdown may show a
local reaction before the remote phrase that caused it. The audit is report-only and does not edit
transcript profiles.

`murmurmark repair order` is the first explicit repair layer for those regions. It wraps
`scripts/apply-transcript-order-repair.py`, reads the selected dialogue profile plus saved
`candidate_utterances*.json` and `raw_segments*.json`, and writes only `order_repair_v1` artifacts
under `derived/transcript-simple/whisper-cpp/resolved/` and `order-repair/`. v1 applies only one
mechanical edit: split a long `Me` utterance into before/after `Me` utterances when source ASR
segments sit cleanly around the `Colleagues` turn. If source segments are missing, overlap text has
unique local content, or one `Me` utterance has several order risks, the original utterance is kept
and marked `quality.transcript_order_repair.status = needs_review`.

`murmurmark repair remote-leak` wraps `scripts/plan-remote-leak-segment-repair.py`. It is not a
cleanup profile. It reads the audio-review audit, writes a segment-level plan and keeps
`whole_me_drop_allowed = false` for protected local-content items. This is the next safe input for
future remote-leak/remote-duplicate work: first identify intervals that need split/re-ASR/text
repair or mark-only treatment, then add a separate repair profile only when the corpus evidence is
good enough.

Audit-informed cleanup is `murmurmark cleanup`. It wraps `scripts/apply-audit-cleanup.py`, reads
`clean_dialogue.shadow_v2.json` and the group overlap audit, then writes only `audit_cleanup_v1`
artifacts under `derived/transcript-simple/whisper-cpp/resolved/` and
`derived/transcript-simple/whisper-cpp/audit-cleanup/`. In conservative mode it may drop whole `Me`
utterances only when they are high-confidence remote duplicates or short unsupported ASR noise.
Double-talk, timing overlap, remote leak, and human-review regions are kept and marked.

The audio review layer is available through `murmurmark audit audio-review`.
It collects suspicious transcript regions from `needs_review`, overlaps, group-overlap audit and cleanup
rejections, cuts local comparison clips under `derived/audit/audio-review-pack/`, then classifies them
with local audio/text metrics. The output separates likely reliable regions, probable transcript errors
and regions that should go to a stronger local audio judge. It is audit-only and never edits transcript
profiles or raw capture. When local metrics disagree only between benign explanations such as
double-talk, timing overlap and reliable local speech, the audit treats the item as likely reliable
instead of sending it to the stronger-judge queue. An empty review pack is a valid no-op: the audit
still writes empty `audio_review_*` outputs and lets the session pipeline continue.

`audit_cleanup_v2` is the conservative cleanup profile that consumes the audio review audit. It reads
`audit_cleanup_v1` plus `derived/audit/audio-review-pack/audio_review_audit.jsonl`, then writes a
separate `audit_cleanup_v2` transcript profile. In v2, only high-confidence `remote_duplicate` and
short `asr_noise` `Me` utterances can be dropped. `remote_leak`, `lost_me`, `uncertain`,
`double_talk` and `timing_overlap` are kept and marked for review.

`audit_cleanup_v3` is the next conservative profile. It reads the selected cleanup profile plus
`sessions/_reports/audio-judge-v0/audio_judge_v0_queue_predictions.jsonl`. It can drop additional
whole `Me` utterances only when the local audio judge predicts `drop_error` with high confidence and
the original audio-review/text/local-support safety gates still pass. If v3 applies no patches,
`auto` synthesis and session-quality reporting keep using v2/v1 instead of promoting an empty v3
profile.

The quality report helper is `scripts/report-session-quality.py`. It reads existing derived JSON
for one or more sessions and writes a private JSON/CSV/Markdown summary under
`sessions/_reports/session-quality/` by default. It does not run ASR and does not modify session
audio or transcript artifacts. Audio-review counters are computed against the selected transcript
profile: if `audit_cleanup_v2` already removed a duplicate `Me` utterance, that audio-review item is
counted as resolved by cleanup and no longer inflates the remaining review burden.
Review seconds are measured as a union of active intervals per verdict, so duplicate audit rows for
the same utterance do not inflate the use gate.
Low local recall is also profile-aware enough for use gates: if `audit-local-recall.py` explains the
unrecovered islands as short or weak, the recall warning is kept in the audit report but does not
block `ready_for_notes`; possible lost `Me` speech still blocks the gate.
Those blocking local-recall items are included in the operational review queue and review plan with
short `ffplay` commands against the local mic capture, so `review_first` always points to concrete
audio to check.

`scripts/build-regression-corpus.py` collects high-value examples from existing
`audio_review_audit.jsonl` files across sessions. It balances examples by label, copies the already
cut audio-review clips into `sessions/_reports/regression-corpus/clips/`, and writes JSONL, JSON and
Markdown reports. The corpus is meant for future agent-driven cleanup checks and for a stronger local
audio judge. It is private generated data and does not modify sessions or raw audio.
`scripts/evaluate-regression-corpus.py` then buckets the corpus into silver cleanup positives,
silver keep negatives, mark-only regressions and examples that need a stronger audio judge.
`scripts/train-audio-judge-v0.py` trains a local shadow classifier on corpus silver labels using only
numeric audio/text metrics. It does not edit transcripts; it reports out-of-fold cross-session
validation quality, per-session errors, confidence buckets, cleanup precision by threshold and
candidate predictions for later cleanup experiments. Queue predictions stay conservative:
`drop_error` and `mark_only_error` remain human-review items until a separate cleanup profile consumes
them with its own gates. `audit_cleanup_v3` is that first consuming profile, but it only accepts
high-confidence `drop_error` candidates that also satisfy the existing cleanup safety checks.
`audit_cleanup_v4` is still conservative, but it adds a narrow expanded duplicate gate for
audio-judge `drop_error` predictions above `0.93`: strong text containment, low local support, good
overlap coverage, no protected markers and no notes impact are required. It remains mark-only for
`remote_leak`, `lost_me`, `uncertain`, double-talk and timing overlap.
`audit_cleanup_v5` materializes only safe `drop_me` edits from the `suggested_review_v1` shadow
report. It does not trust suggested review as human review and does not copy review marks wholesale:
it reads the currently selected cleanup profile, drops only whole `Me` utterances from sessions
classified as promising cleanup candidates, and writes a normal cleanup profile with its own gates.
Use it after `scripts/report-suggested-review-shadow.py`:

```bash
.venv/bin/python scripts/apply-suggested-cleanup.py
```

`auto` synthesis may select v5 when its cleanup gates pass and it applied at least one patch.
`audit_cleanup_v6` is the next conservative pass after a fresh audio-review pack has been rebuilt
for v5. It reuses the existing audio-review cleanup gates over `audit_cleanup_v5`; it does not use
the suggested-review shadow directly and does not add new ASR or audio-judge rules.
`scripts/report-operational-readiness.py` combines session quality, corpus readiness and audio-judge
shadow readiness into a practical verdict such as `pilot_ready_with_review` for medium-risk working
meetings. It also assigns per-session use gates such as `ready_for_notes` and `review_first`, then
writes a promotion plan and a short review queue with concrete `afplay` commands for audio checks
and report links for transcript-order checks. The promotion plan explains what still blocks
`medium_risk_ready`: unresolved warnings, sessions not ready for notes, and the remaining review
minutes. The queue is filtered through the selected transcript profile, so already-dropped `Me`
utterances do not stay in the operational review list. It also ignores stale audio-judge queue rows
when the current audio-review audit has since reclassified that item as reliable.
The report also includes `Review Queue Strategy`: lane counts, the first lane to close, and the
reason it was chosen. `quick_recommended_lane` still points to the fastest confirm/drop pass when it
exists, while `first_recommended_lane` targets the current blocker. The report also shows the
estimated queue remaining after that first lane. It reports both raw queue rows and packed review
actions, so the remaining work reflects grouped rows that can be answered once per `Me` utterance.
When a concrete review target is known, top-level `next_commands` and `murmurmark report corpus`
include `--session sessions/<id>` rather than a generic corpus-wide review command. The report also
surfaces the same target as `focus_session` and `focus_next`, so the next operator action is visible
without opening the JSON report.
`murmurmark report corpus` and `murmurmark next corpus` also print a compact `use` block derived
from the same readiness JSON: corpus-level usability summary, `can_use_any_notes`,
`can_use_medium_risk`, ready/review/incomplete session counts, review burden and the minimum next
command.
The generated review plan keeps both `raw_item_count` and `review_action_count`: raw items are source
risks, while actions are answer-sheet decisions after safe grouping by `Me` utterance.
`grouped_review_row_count` is the saved manual-action estimate.
`murmurmark review workspace` can then build all remaining lane packs, editable answer sheets
and one `review_workspace.md` index for the reviewer.

`scripts/build-review-plan.py` turns that operational review queue into a compact working checklist
under `sessions/_reports/review-plan/`. It groups nearby risky intervals by session, estimates the
actual listening time, keeps ready-to-run `afplay`/`ffplay` commands, and gives a small protocol for deciding
whether each `Me` candidate should be dropped, kept, or left as `needs_review`. Local-recall items
are audit-only review rows: they can clear or keep the local-recall risk, but they do not insert
missing text into the transcript. Transcript-order rows are also audit-only for timeline content:
they can clear or keep the chronology risk, and `apply-review-decisions.py` records the result in
`quality.transcript_order_review` on affected utterances, but it does not move utterances or edit
text. The plan itself is audit-only and does not edit transcript profiles.
For `remote_duplicate`, `drop_me` is suggested only when the duplicate covers almost all of the
`Me` utterance and the text match is strong. If the overlap is only a slice of a longer local utterance,
the row is marked
`check_unique_me_content` and the safe default is `needs_review`.
The generated plan also splits the queue into `review_lane` groups:
`fast_confirm_drop`, `check_unique_me_content`, `check_local_recall`, `check_transcript_order`,
`confirm_benign`, and
`classify_audio`. Use those lanes to close the blocking lane first, then clear complete duplicate/noise
checks or slower meaning-heavy checks. The plan carries the `first_recommended_lane` from operational readiness,
and `murmurmark review first-lane` builds the corresponding lane pack directly, including WAV, Markdown
index and editable answer sheet.
`murmurmark review lane check_local_recall --session SESSION` builds one explicit lane pack, which is
useful for checking local-recall repair insertions when you intentionally want that lane.
To clear only the quickest lane, run the CLI with that lane; the output file still
keeps the full template, so later passes can continue with the remaining lanes.
For faster listening, `murmurmark review workspace` builds lane packs for all remaining lanes:
one WAV, a Markdown index and an editable answer sheet per lane under
`sessions/_reports/review-plan/lane-packs/`. Use `--session latest` or `--session ./sessions/<id>`
to focus the workspace on one session. The CLI output lists each lane pack with item count,
source row count, grouped rows saved, suggested compact answers, `afplay` and `$EDITOR` commands, so the workspace can be reviewed without
opening the JSON manifest first.
For one-off debugging, `scripts/build-review-lane-pack.py --lane fast_confirm_drop` creates a single
WAV, a Markdown index and an editable answer sheet under `sessions/_reports/review-plan/lane-packs/`.
The Markdown index is meant to be readable on its own: it shows the compact shortcut protocol,
allowed decisions per item, suggested decision reason, selected audio command, utterance ids and the
text evidence used for the row. Use the JSON manifest when tooling needs the same data
programmatically.
For lanes such as `check_transcript_order`, `check_unique_me_content` and `classify_audio`, the lane
pack groups rows that point to the same `Me` utterance. One answer can therefore close several
repeated review rows; the manifest keeps the full `review_row_keys` list so the apply step remains
explicit and auditable.
After listening to that pack, edit the generated `review_lane_answers.<lane>.txt` file and run
`murmurmark review lane apply <lane> --session SESSION` to copy those answers back into
`review_decisions.jsonl`. Use `murmurmark review lane apply first --session SESSION` after
`review first-lane`; `first` resolves to the lane named in `review_plan.json`. The lower-level Python
script remains available for debugging, but the Swift CLI uses the correct session-local paths by
default. Lane-pack commands print the exact `review lane apply ...` command for the generated answer
sheet, plus the `afplay`, `less` for the lane Markdown, `$EDITOR` and dry-run commands for that pack.
They also print `manual_flow`, optional `suggested_flow`, and `after_apply`, so the safe order is
visible without reading the runbook.
`review lane apply --dry-run` writes `review_lane_pack_apply_report.json`, prints the lane result and
either points back to the answer sheet when rows are still `todo`, or prints the exact non-dry-run
command when the dry run would close rows. After applying the lane, the CLI refreshes
`review_decisions_progress.json`: if more rows remain it points back to
the next remaining lane and the workspace flow. If that lane pack is already built, `review progress`
points to the prepared `afplay`, `less`, `$EDITOR`, dry-run and apply commands instead of asking to
build the same lane again. When the answer sheet already contains reviewed answers, the handoff
promotes dry-run/apply ahead of replaying the audio. If progress does not exist yet, it falls back to
`murmurmark review first-lane` and `review lane apply first`. It prints
`murmurmark review apply` as the final batch step only when
`murmurmark review progress --session SESSION` says the review file is ready. `review progress`
also prints raw row progress, packed review-action progress, `next_lane` and the exact
`review lane ...` / `review lane apply ...` commands for the first remaining lane.
Each lane also has `review_lane_answers.<lane>.suggested.txt`; use it as a review aid, not as a
silent replacement for listening when the meeting is medium-risk. Lane-pack output also prints
`suggested_dry_run` and `suggested_apply`; those commands use
`murmurmark review lane apply <lane> --answers-source suggested`, which reads
`review_lane_answers.<lane>.suggested.txt` explicitly and cannot be mixed with `--answers` or
`--answers-file`. Run the suggested command with `--dry-run` first.
When several lane answer sheets are edited, `murmurmark review workspace apply` applies
the whole `review_workspace.json` in one validated pass and then refreshes review progress.
Use `murmurmark review workspace apply --dry-run` to write only the validation report and print the
same handoff without changing `review_decisions.jsonl`. The apply output also prints per-lane
progress, the remaining lane Markdown to read, answer sheets to edit, and the next
lane/workspace-apply/progress/apply commands. Workspace output also prints
`manual_flow`, optional `suggested_flow`, and `after_apply`. It still prints `suggested_dry_run`
and `suggested_apply` for the generated `.suggested.txt` sheets; keep the same rule as lane review
and run the suggested path with `--dry-run` before writing decisions.
After `murmurmark review apply`, the CLI uses the refreshed readiness report for the primary `next`
command. For a single session it keeps `report_next: murmurmark report ...` as the explicit refresh
command and then prints the readiness summary, including export or retention commands when the
session is ready.
If `review apply` is run before a decisions file exists, or while review progress still has `todo`
rows, it prints `status: not_ready`, the missing file or progress summary, and the
`review workspace` / `review progress` commands to run next instead of exposing a low-level Python
error or starting batch apply too early.
`murmurmark review progress --session SESSION` then shows how much of the queue is actually closed,
prints the remaining work by lane, and gives the exact workspace/apply/progress command chain before
the heavier batch apply.

`murmurmark review agent` is the automatic medium-risk layer. It reads the current session-quality
report, audio-review audit rows and the audio-judge queue, then writes and applies a reduced
agent review scope under `sessions/_reports/review-plan/`. The scope contains only rows that the
rules can close without listening: whole-utterance `drop_me` for very clear remote duplicates or
ASR noise, `keep_me` for strong local-support cases that no longer need human review, and
high-confidence local-recall repair insertions with local-only speaker-state evidence. It writes
`agent_reviewed_v1`, which is eligible for `--transcript-profile auto` after gates pass. It never
changes raw CAF files, Echo Guard outputs, ASR output or existing cleanup profiles.

```bash
murmurmark review agent
```

The normal manual review loop is available through the Swift CLI:

```bash
murmurmark review next latest
murmurmark review first-lane
murmurmark review lane check_local_recall --session latest
murmurmark review lane apply first --session latest
murmurmark review workspace
murmurmark review latest --lane fast_confirm_drop
murmurmark review workspace apply --session latest
murmurmark review progress --session latest
murmurmark review apply --session latest
```

`review next` writes its per-session review handoff under
`SESSION/derived/readiness/review-plan/`. `review first-lane --session SESSION`,
`review workspace --session SESSION`, `review workspace apply --session SESSION`,
`review progress --session SESSION`, and `review apply --session SESSION` default to those
session-local paths. Existing non-empty session-local plans are preserved by workspace/progress
commands, so an explicit lane pack is not replaced by an empty refresh. Use `review first-lane`,
`review workspace` or bare `review progress` when you want the global corpus queue.
When a session-local plan exists and the session still requires review, `review next` also shows
packed `review_actions`, saved grouped rows and the remaining action count after the first lane,
matching the units from
`murmurmark report corpus`. The key review handoffs (`review next`, `review progress`,
`review apply`) also repeat the primary command as the final copyable `next: ...` line.

Use the Python scripts directly only when debugging a specific review file, lane pack, or batch
application edge case.

`scripts/review-decisions-cli.py` is the fastest way to fill that checklist. It walks through
`review_decisions.template.jsonl`, plays the preferred stereo clip, shows the `Me`/`remote` texts and
writes `review_decisions.jsonl` after every answer. `Enter` accepts `suggested_decision`; `d`, `c`,
`k`, `r` and `s` mean `drop_me`, `drop_remote`, `keep_me`, `needs_review` and `skip`. The CLI respects
`allowed_decisions`, so `d=drop_me` and `c=drop_remote` are not accepted for audit-only local-recall
or transcript-order rows. It also shows
nearby transcript turns from the reviewed profile, which helps distinguish leaked remote speech from
real local replies. When a row has several clips, the CLI prints `audio=1:...` shortcuts; type the
number to play that specific track without leaving the review flow. When all rows are closed, it
prints the `apply-review-decisions-batch.py --synthesize --refresh-reports` command that turns the
decisions into a fresh `reviewed_v1` profile and refreshed readiness reports.

`scripts/apply-review-decisions.py` closes the manual review loop. After the CLI or a manual edit has
filled each `decision` as `drop_me`, `drop_remote`, `keep_me`, `needs_review`, or `skip`, apply the
decisions to a session. The script
writes a separate `reviewed_v1` profile and never changes the automatic cleanup profiles. The
profile is eligible for `--transcript-profile auto` only when the review template has no remaining
`todo` rows for that session and every row respects `allowed_decisions`; partial or invalid review
stays as an audit artifact with failing gates.
For local-recall rows, `drop_me` is invalid because there is no transcript utterance to drop.
`keep_me` or `skip` closes the local-recall risk as checked; `needs_review` keeps it blocking.
For audio-review rows, `drop_remote` removes the reviewed `Colleagues` utterance when the remote
track contains a duplicate of local speech; use it only after listening confirms that remote, not
`Me`, is the duplicate.
The template also includes `suggested_decision` hints such as `drop_me` for probable duplicates, but
they are never applied until the `decision` field is edited explicitly.
For comparison, the suggested answer sheets can be applied into a separate shadow profile:

```bash
murmurmark review workspace apply \
  --answers-source suggested \
  --out sessions/_reports/review-plan/review_decisions.suggested.jsonl

.venv/bin/python scripts/apply-review-decisions-batch.py \
  --decisions sessions/_reports/review-plan/review_decisions.suggested.jsonl \
  --review-template sessions/_reports/review-plan/review_decisions.template.jsonl \
  --output-profile suggested_review_v1 \
  --synthesize \
  --out sessions/_reports/review-plan/review_decisions_apply.suggested_review_v1.json

.venv/bin/python scripts/report-suggested-review-shadow.py
.venv/bin/python scripts/apply-suggested-cleanup.py
.venv/bin/python scripts/apply-audit-cleanup.py ./sessions/<session> --input-profile audit_cleanup_v5 --output-profile audit_cleanup_v6
.venv/bin/python scripts/synthesize-simple-extractive.py ./sessions/<session> --transcript-profile audit_cleanup_v6
```

`suggested_review_v1` is generated from machine suggestions only. It is useful for measuring the
next cleanup candidate, but it is not selected by `--transcript-profile auto` and is not equivalent
to `reviewed_v1`. The shadow report writes `sessions/_reports/suggested-review-shadow/` and should
show no hard metric regressions before these rules are considered for promotion. A residual
`needs_review_ratio` risk means the session still needs review; it does not by itself prove that the
suggested cleanup edits are unsafe.
`scripts/apply-review-decisions-batch.py` applies the same edited file to every session mentioned in
the review plan and can immediately regenerate extractive notes with `--synthesize`. Use
`--refresh-reports` when the review is closed: it also refreshes session quality,
operational readiness, and the next review plan, so the visible verdict is not stale. Its report also
copies the refreshed `next_commands`, which lets `murmurmark review apply` and agents continue from
one machine-readable handoff instead of guessing the next shell command.

Timeline repair treats `remote` as the authoritative `Colleagues` timeline. If whisper.cpp glues a long `Me` segment across a remote reply, the bridge cuts that mic candidate around guarded remote intervals, keeps only local islands from Echo Guard speaker state, and can run micro-ASR on those short islands. If no local island can be recovered, the misleading long `Me` block is dropped rather than published whole. `source_start`, `source_end`, `timeline_repair_examples.jsonl`, and `role_decisions.json` remain available for audit.

`--repair-profile current` is the default and keeps the current transcript path stable. `--repair-profile shadow_v2` writes a separate candidate transcript, quality report, timeline-repair report, comparison gates, and audit examples without replacing `transcript.md`. Shadow repair seeds every short local-island micro-ASR choice with the current result, then tests wider windows, alternate mic sources, leading silence, narrow boundary-prefix fixes such as `адно` -> `Ладно`, and a guarded start-of-call repair for short opening turns such as `Привет`, `Меня слышно?`, `Привет, да`. `repair_comparison.json` must pass no-regression gates before any shadow behavior is promoted.

Before recording, macOS must allow the terminal or Codex app to use Screen & System Audio Recording
and Microphone. `doctor` prints the current permission state, local dependency checks, model path,
pipeline readiness and the next normal CLI commands.

For the first real run, use [docs/runbooks/first-recording.md](docs/runbooks/first-recording.md).

For real work, install the local wrapper with `scripts/install-local.sh` and then use
`murmurmark record --target-bundle system`. Without `--duration`, recording runs until `Ctrl-C`.
Without `--out`, every recording gets a unique directory under `./sessions`, so previous sessions
are not overwritten. On success, `record` prints the session path, `recommended_next` and the next
`murmurmark process ...` command.

Before trying cleanup on a real speaker-bleed session, run the [Echo Guard delay lab](docs/runbooks/echo-guard-lab.md). If delay confidence is unstable, do not trust cleanup output yet; use transcript-level suppression and keep raw mic for ASR fallback.

## Product Shape

```text
MurmurMark Capture
  -> mic.caf + remote.caf + session.json + events.jsonl

MurmurMark Process
  -> Echo Guard audio + transcript-simple profiles + quality reports

MurmurMark Review
  -> readiness gate + review plan + optional reviewed transcript profile

MurmurMark Synthesis
  -> quality verdict + evidence-backed notes + transcript Markdown

MurmurMark Handoff
  -> Markdown/Obsidian export bundle + retention/payload manifests
```

## v1 Decisions

- Current remote audio capture: ScreenCaptureKit app/system audio output.
- Future precise remote capture option: Core Audio Process Tap for the meeting app.
- Microphone capture: AUHAL/Core Audio for a selected input device.
- Raw format: two independent CAF streams, not one stereo L/R file.
- No virtual audio devices by default.
- No cloud upload during capture.
- Capture and transcription are separate stages connected by a file manifest.
- Long meetings are handled as a global timeline with overlapping ASR windows.
- Notes must be evidence-backed: decisions, actions and risks need utterance IDs.

## Documentation Map

- [docs/00-index.md](docs/00-index.md): reading order.
- [docs/product/vision.md](docs/product/vision.md): product intent and non-goals.
- [docs/product/prd-v1.md](docs/product/prd-v1.md): v1 requirements.
- [docs/architecture/system-overview.md](docs/architecture/system-overview.md): whole-system design.
- [docs/architecture/capture.md](docs/architecture/capture.md): macOS recorder design.
- [docs/architecture/transcription.md](docs/architecture/transcription.md): ASR, diarization and correction.
- [docs/architecture/echo-suppression.md](docs/architecture/echo-suppression.md): Echo Guard diagnostics and derived echo suppression.
- [docs/architecture/evidence-synthesis.md](docs/architecture/evidence-synthesis.md): notes and docs integration.
- [docs/contracts/session-package.md](docs/contracts/session-package.md): local session schema.
- [docs/contracts/transcript-and-evidence.md](docs/contracts/transcript-and-evidence.md): transcript, quality and evidence schemas.
- [docs/security/privacy-and-threat-model.md](docs/security/privacy-and-threat-model.md): privacy defaults and threat model.
- [docs/runbooks/first-recording.md](docs/runbooks/first-recording.md): first local recording check.
- [docs/runbooks/echo-guard-lab.md](docs/runbooks/echo-guard-lab.md): delay and candidate-clip investigation before echo cleanup.
- [docs/runbooks/transcribe-simple-whispercpp.md](docs/runbooks/transcribe-simple-whispercpp.md): local transcription and repair path with whisper.cpp.
- [docs/decisions/tradeoffs.md](docs/decisions/tradeoffs.md): accepted and rejected tradeoffs.
- [docs/rfc/0001-v1-scope.md](docs/rfc/0001-v1-scope.md): implementation RFC for v1.
- [docs/adr/](docs/adr/): architecture decision records.
- [docs/adr/0010-use-preserve-local-fir-for-current-echo-guard.md](docs/adr/0010-use-preserve-local-fir-for-current-echo-guard.md): current Echo Guard cleanup default.
- [docs/backlog/mic-remote-bleed-reduction.md](docs/backlog/mic-remote-bleed-reduction.md): active Echo Guard work for reducing remote audio bleed in the mic track.

## Implementation Posture

The repository is CLI-first today. Future UI work should reuse the same session, processing and
export contracts instead of creating a parallel product path:

```text
Sources/MurmurMarkCLI/      current CLI: record/process/report/review/export/retention
scripts/                    local processing, audit, review and synthesis helpers
docs/                       architecture, contracts, runbooks and roadmap
examples/                   domain packs and sample policies
```

The ScreenCaptureKit backend is the current working capture backend. The documented Core Audio Process Tap backend remains a future option for more precise per-application capture.
