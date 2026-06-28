# MurmurMark

MurmurMark is a local-first macOS meeting capture and notes pipeline.

It records the user's microphone and the selected meeting application's audio into separate local tracks, without virtual audio devices, without changing the meeting app's audio routing, and without uploading raw audio by default. The recording package is then processed into a speaker-aware transcript, evidence-backed meeting notes, and documentation updates under an explicit privacy policy.

The repository now contains a first minimal Swift CLI plus local transcript and extractive notes scripts. Its current scope is capture, Echo Guard preprocessing, simple `Me`/`Colleagues` transcription, timeline repair for common mic/remote ordering failures, quality verdicts, and evidence-backed extractive notes. Full diarization and generative synthesis remain documented future work.

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
murmurmark process latest
murmurmark report latest
# Follow printed review commands when the gate is review_first.
murmurmark export latest --format markdown --include-json
murmurmark retention plan latest
```

`report`, `review progress`, blocked `export` and retention commands all print the next safe command
for the current session state, so the terminal output is the main handoff. `report` starts with a
short status such as `exportable`, `review_required`, `incomplete` or `blocked`, plus
`recommended_next` and `handoff` commands for opening the selected notes, transcript and verdict.

### Local Release Bundle

To create an inspectable local bundle from tracked project files:

```bash
scripts/build-release-bundle.sh

BUNDLE="$(find dist/release-bundles -maxdepth 1 -type d -name 'murmurmark-*' | sort | tail -1)"
MURMURMARK_PYTHON="$PWD/.venv/bin/python" "$BUNDLE/bin/murmurmark" doctor --strict
cat "$BUNDLE/release-manifest.json"
```

The bundle contains the release binary, wrapper, scripts, docs, examples, helper sources,
`murmurmark.config.example.json` and a manifest. It intentionally excludes `sessions/`,
`exports/private/`, raw audio, `.venv`, models, weights and `murmurmark.config.json`.
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

### End-to-End From an Existing Recording

Use this when the session directory already exists and raw recording is complete.

```bash
cd murmurmark

SESSION=./sessions/<session>

git pull
murmurmark process "$SESSION" --force-asr

murmurmark report "$SESSION"
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
second is how many raw review rows were packed behind those decisions.

Retention is explicit and local:

```bash
murmurmark retention plan "$SESSION"
murmurmark retention payload "$SESSION"
less "$SESSION/derived/retention/retention_plan.json"
less "$SESSION/derived/retention/provider_payload_manifest.json"
```

Both commands print a short handoff summary: selected plan or payload manifest, raw-audio action
counts, blockers/warnings, and the next safe command.

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
`corpus taxonomy` writes `sessions/_reports/audio-error-taxonomy/audio_error_taxonomy_report.*`.
Use it after `corpus train-audio-judge` to see which error classes are already safe cleanup
candidates, which are mark-only, which need better labels, and which sessions/examples should drive
the next quality iteration. The report also splits broad classes like `uncertain` into diagnostic
subtypes and writes an `action_plan`, so the next repair can target one narrow failure mode.
`corpus remote-leak` writes `sessions/_reports/remote-leak-segment/remote_leak_segment_corpus_report.*`.
When plans are missing, the report points to `murmurmark corpus remote-leak --plan`; when protected
local-content intervals exist in complete sessions, it points to
`murmurmark review lane check_unique_me_content --session ...`; incomplete sessions are sent back
through `murmurmark process ...` first.
`corpus local-recall` writes `sessions/_reports/local-recall/local_recall_corpus_report.*`.
It aggregates possible lost-`Me` and local-recall review items across the corpus; `--audit` refreshes
per-session local-recall audits first.
`corpus local-recall-repair` writes
`sessions/_reports/local-recall-repair/local_recall_repair_corpus_report.*`. It summarizes
`local_recall_repair_v1` reports across sessions; `--repair` refreshes the candidate repair profile
first. Inserted `Me` turns remain explicit `needs_review`, and the report does not promote the
profile into `auto`. When inserted repairs exist in complete sessions, the corpus report includes
the first `murmurmark review lane check_local_recall --session ...` command for review; incomplete
sessions are sent back through `murmurmark process ...` first. The repair layer keeps micro-ASR
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
After a successful recording, the CLI prints `SESSION="..."` and the exact `murmurmark process ...`
command for that session.

```bash
cd murmurmark

git pull
murmurmark doctor
murmurmark config print

murmurmark record --target-bundle system

murmurmark process latest

murmurmark report latest
murmurmark notes latest --kind verdict
murmurmark notes latest
murmurmark transcript latest
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
Use `--progress-interval-sec 0` to disable these heartbeat lines.
Read `session_readiness.md` first, or run `murmurmark report SESSION` for the terminal version. The
CLI prints a short status, `recommended_next`, the session use gate, selected profile, review burden,
synthesis review item summary, next CLI commands, `handoff` open/export commands, and links to the
transcript, notes, quality verdict and audio-review report.
`murmurmark review next SESSION` is the short terminal handoff for that same information: it refreshes
session readiness, builds a session-local review plan when review is needed, and prints the
review-specific next commands. When a review plan exists, it also prints `first_lane_flow` for the
current blocker, `quick_lane_flow` for the fastest confirm/drop pass when that is a different lane,
and `workspace_flow` for reviewing all lanes. It also prints the recommended first lane reason, the
fastest quick lane and the estimated remaining queue after the first lane, so the normal order is
visible without opening `review_plan.json`.
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
audio-review. If a session has `remote_leak` errors where `Me` may still contain unique local
content, readiness links to `remote_leak_segment_repair.md` before any future repair work.
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
remaining unsafe regions as explicit review items.
`murmurmark repair remote-leak` is audit-only. It reads `audio_review_audit.jsonl`, selects
`remote_leak` rows that look like probable transcript errors, and writes a segment-level repair plan
under `derived/transcript-simple/whisper-cpp/remote-leak-repair/`. It does not edit transcript
profiles or raw audio. Its main purpose is to separate plain leak evidence from leak intervals where
`Me` still contains unique local content and whole-utterance deletion is unsafe.
`murmurmark corpus order` aggregates those audits across the corpus and writes the current list of
chronology regression candidates under `sessions/_reports/transcript-order/`; corpus gates read this
report and fail if a complete session still has blocking chronology risk. The report points complete
blocking sessions to `murmurmark review lane check_transcript_order --session ...`; incomplete
blocking sessions go back through `murmurmark process ...`. Use
`murmurmark corpus order --repair` when you want to refresh order audits for the sessions in the
current session-quality report, try conservative
`order_repair_v1` for each session, refresh session-quality, and then rebuild the corpus order
summary in one pass. The corpus report includes an `order_repair` summary with applied repairs,
cleared sessions and remaining unrepaired order risks.
`murmurmark audit group-overlaps` classifies `Me`/`Colleagues` timeline overlaps into harmful, benign
and review buckets, writes listenable clips, and does not change transcripts or quality verdicts by itself.
`murmurmark cleanup` is the conservative cleanup over audit evidence. It writes separate
`audit_cleanup_*` profiles and only drops whole `Me` utterances when the audit strongly supports remote
duplicate or ASR-noise classification. It never edits `shadow_v2`.
`murmurmark synthesize` then selects or accepts a dialogue profile, writes a quality verdict, and creates local extractive notes where every item cites utterance IDs.

### Command Reference

```bash
swift build
swift run murmurmark doctor
swift run murmurmark doctor --strict
scripts/build-release-bundle.sh
scripts/check-open-source-readiness.sh
swift run murmurmark list-apps
murmurmark record --target-bundle system
murmurmark latest
murmurmark config print
murmurmark process latest
murmurmark process ./sessions/<session> --force-asr
murmurmark process ./sessions/<session> --plan-only --skip-build
murmurmark report ./sessions/<session>
murmurmark report latest
murmurmark report corpus
murmurmark notes ./sessions/<session>
murmurmark notes latest --kind verdict --path-only
murmurmark transcript ./sessions/<session>
murmurmark transcript latest --path-only
murmurmark audit local-recall ./sessions/<session> --profile shadow_v2
murmurmark audit order ./sessions/<session> --profile auto
murmurmark audit group-overlaps ./sessions/<session> --profile shadow_v2 --write-clips
murmurmark audit audio-review ./sessions/<session> --profile audit_cleanup_v2 --write-clips
murmurmark repair order ./sessions/<session> --input-profile auto --output-profile order_repair_v1
murmurmark repair local-recall ./sessions/<session> --input-profile auto --output-profile local_recall_repair_v1
murmurmark repair remote-leak ./sessions/<session>
murmurmark review first-lane
murmurmark review workspace
murmurmark review lane check_local_recall --session ./sessions/<session>
murmurmark review lane apply first --session ./sessions/<session>
murmurmark review latest --lane fast_confirm_drop
murmurmark review progress --session ./sessions/<session>
murmurmark review apply --session ./sessions/<session>
murmurmark corpus process all --per-label 16 --max-items 160
murmurmark corpus build ./sessions/<session> --per-label 16 --max-items 160
murmurmark corpus evaluate
murmurmark corpus train-audio-judge
murmurmark corpus taxonomy
murmurmark corpus gate
murmurmark corpus gate --write-baseline sessions/_reports/corpus-gates/baseline.local.json
murmurmark corpus gate --baseline sessions/_reports/corpus-gates/baseline.local.json
murmurmark corpus order
murmurmark corpus order --repair
murmurmark corpus local-recall
murmurmark corpus local-recall --audit
murmurmark corpus local-recall-repair
murmurmark corpus local-recall-repair --repair --no-synthesize
murmurmark corpus remote-leak
murmurmark corpus remote-leak --plan
murmurmark corpus report
murmurmark export ./sessions/<session> --format markdown --include-json
murmurmark export ./sessions/<session> --format obsidian
murmurmark retention plan ./sessions/<session>
murmurmark retention payload ./sessions/<session>
murmurmark retention apply ./sessions/<session> --policy ./policy.json --confirm-delete-raw
.build/debug/murmurmark preprocess ./sessions/<session> --echo diagnostic
.build/debug/murmurmark preprocess ./sessions/<session> --echo clean --echo-engine linear_baseline
.build/debug/murmurmark preprocess ./sessions/<session> --echo clean --echo-engine local_fir
.build/debug/murmurmark preprocess ./sessions/<session> --echo clean --echo-engine local_fir --echo-policy role_safe
.build/debug/murmurmark preprocess ./sessions/<session> --echo clean --echo-engine speexdsp
.build/debug/murmurmark preprocess ./sessions/<session> --echo clean --echo-engine webrtc-apm
.build/debug/murmurmark reconcile-transcript ./sessions/<session>
.build/debug/murmurmark inspect ./sessions/<session>
.build/debug/murmurmark inspect ./sessions/<session> --echo
.build/debug/murmurmark export-audio ./sessions/<session>
.venv/bin/python scripts/transcribe-simple-whispercpp.py ./sessions/<session>
.venv/bin/python scripts/transcribe-simple-whispercpp.py ./sessions/<session> --prompt-file domain-packs/<domain>/whisper-prompt.ru.txt
.venv/bin/python scripts/transcribe-simple-whispercpp.py ./sessions/<session> --repair-profile shadow_v2 --skip-export --skip-transcribe
.venv/bin/python scripts/run-session-pipeline.py ./sessions/<session>
murmurmark synthesize ./sessions/<session> --transcript-profile auto
murmurmark cleanup ./sessions/<session> --input-profile shadow_v2 --output-profile audit_cleanup_v1
murmurmark synthesize ./sessions/<session> --transcript-profile audit_cleanup_v1
murmurmark cleanup ./sessions/<session> --input-profile audit_cleanup_v1 --output-profile audit_cleanup_v2
murmurmark synthesize ./sessions/<session> --transcript-profile audit_cleanup_v2
murmurmark cleanup ./sessions/<session> --input-profile audit_cleanup_v2 --output-profile audit_cleanup_v3 --audio-judge-queue sessions/_reports/audio-judge-v0/audio_judge_v0_queue_predictions.jsonl
murmurmark synthesize ./sessions/<session> --transcript-profile audit_cleanup_v3
murmurmark cleanup ./sessions/<session> --input-profile audit_cleanup_v3 --output-profile audit_cleanup_v4 --audio-judge-queue sessions/_reports/audio-judge-v0/audio_judge_v0_queue_predictions.jsonl
murmurmark synthesize ./sessions/<session> --transcript-profile audit_cleanup_v4
.venv/bin/python scripts/apply-suggested-cleanup.py
murmurmark synthesize ./sessions/<session> --transcript-profile audit_cleanup_v5
murmurmark cleanup ./sessions/<session> --input-profile audit_cleanup_v5 --output-profile audit_cleanup_v6
murmurmark synthesize ./sessions/<session> --transcript-profile audit_cleanup_v6
murmurmark repair order ./sessions/<session> --input-profile auto --output-profile order_repair_v1
murmurmark repair local-recall ./sessions/<session> --input-profile auto --output-profile local_recall_repair_v1
murmurmark synthesize ./sessions/<session> --transcript-profile local_recall_repair_v1
murmurmark synthesize ./sessions/<session> --transcript-profile auto
.venv/bin/python scripts/report-session-quality.py ./sessions/<session> --write-session-readiness
.venv/bin/python scripts/build-regression-corpus.py ./sessions/<session>
.venv/bin/python scripts/evaluate-regression-corpus.py
.venv/bin/python scripts/train-audio-judge-v0.py
.venv/bin/python scripts/report-audio-error-taxonomy.py
.venv/bin/python scripts/report-operational-readiness.py
.venv/bin/python scripts/report-transcript-order-corpus.py
.venv/bin/python scripts/report-remote-leak-segment-corpus.py
.venv/bin/python scripts/build-review-plan.py
murmurmark review agent
.venv/bin/python scripts/review-decisions-cli.py --template sessions/_reports/review-plan/review_decisions.template.jsonl --out sessions/_reports/review-plan/review_decisions.jsonl
.venv/bin/python scripts/apply-review-decisions-batch.py --decisions sessions/_reports/review-plan/review_decisions.jsonl --synthesize
.venv/bin/python scripts/apply-review-decisions.py ./sessions/<session> --decisions sessions/_reports/review-plan/review_decisions.jsonl
.venv/bin/python scripts/echo-guard-delay-lab.py ./sessions/<session>
.venv/bin/python scripts/echo-guard-fir-lab.py ./sessions/<session>
.venv/bin/python scripts/echo-guard-local-subtract-lab.py ./sessions/<session> --start-sec <seconds>
```

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

`local_fir` is the current recommended experimental cleaner for real speaker-bleed sessions. It fits short local FIR echo models from the already separated `remote` and `mic` tracks, writes a listenable `mic_clean_local_fir.wav`, a selected `mic_role_masked_for_asr.wav`, a compact `mic_role_preview.wav` for auditioning retained mic regions, and promotes the role-masked file to `mic_for_asr.wav` only when its quality gate accepts the candidate. It also writes `derived/preprocess/mic_asr_segments/segments_manifest.json` for future chunk-based mic ASR. `reconcile-transcript` marks mic utterances that match delayed remote speech and updates `quality_report.json`. Raw capture stays untouched.

For `local_fir`, `--echo-policy preserve_local` is the default because it avoids deleting local speech in ambiguous remote-active regions. It mutes detected silence, passes local-only speech raw, and passes remote-active ambiguous chunks as mildly cleaned/flagged audio. `--echo-policy role_safe` hard-mutes only high-confidence `remote_only` regions. `--echo-policy strict_silence` mutes all remote-active regions, knowingly sacrificing overlap speech.

`export-audio` uses `derived/preprocess/audio/mic_for_asr.wav` when Echo Guard has created it; otherwise it exports the raw mic capture.

The temporary transcription bridge is `scripts/transcribe-simple-whispercpp.py`. It runs `export-audio`, prepares ASR-only speech-band `mic` audio and normalized `remote` audio, calls local `whisper-cli` on short overlapping windows, creates raw segment and candidate JSON, runs timeline repair plus role reconciliation, then writes `clean_dialogue.json`, `role_decisions.json`, `overlaps.json`, `quality_report.json`, `timeline_repair_report.json`, `transcript.md` and `transcript.simple.json` under `derived/transcript-simple/whisper-cpp/resolved/`. ASR preparation never changes raw capture. See [docs/runbooks/transcribe-simple-whispercpp.md](docs/runbooks/transcribe-simple-whispercpp.md).

The first synthesis bridge is `murmurmark synthesize`. It wraps
`scripts/synthesize-simple-extractive.py`, reads only transcript-derived JSON, chooses the best safe
dialogue profile, writes `quality_verdict.json`/`.md`, and creates extractive `notes.md` plus
`evidence_notes.json` and `review_items.jsonl` under `derived/synthesis-simple/extractive/`. It does
not call an LLM and does not infer facts beyond quoted utterances. Its v3 notes path builds topic
blocks, scores action/decision/risk/question candidates, hides meeting facilitation from Markdown,
shows only selected top items, and keeps every hidden weak/process/facilitation candidate in
`evidence_notes.json`. The `murmurmark synthesize` and `murmurmark notes` CLI handoffs also print
the remaining `review_items` count and the main review item types from `quality_verdict.json`.

`murmurmark export` creates a local user-facing bundle under ignored `exports/private/` by default.
Markdown export writes `index.md`, `quality_verdict.md`, `notes.md`, `transcript.md` and
`export_manifest.json`; `--include-json` also copies evidence JSON. Obsidian export writes one
frontmatter Markdown note plus the same manifest. Export blocks sessions whose
`derived/readiness/session_readiness.json` contains `export_blockers`, so incomplete pipelines,
hard quality failures and unfinished review do not silently become finished artifacts. A blocked
export prints structured next commands and writes `*.export_blocked.json`; `--force` keeps the
blockers in `export_manifest.json` and is meant for debugging. After a successful export, the CLI
prints the manifest path, key output files and the matching retention commands.

`murmurmark audit` wraps the local audit scripts through the project Python runtime. `murmurmark audit
local-recall`, `murmurmark audit order`, `murmurmark audit group-overlaps` and `murmurmark audit audio-review` are the normal
entry points; direct Python script calls remain useful for debugging.
After every audit run, the CLI prints a compact handoff summary with the profile, key counters,
recommendation and the report to open next.

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
separate `local_recall_repair_v1` profile. Inserted `Me` turns are always marked `needs_review`;
baseline, cleanup and order profiles are not modified. Use this profile explicitly when checking
whether a missed short local phrase was recovered:
`murmurmark synthesize SESSION --transcript-profile local_recall_repair_v1`.
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
before review.

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
`whole_me_drop_allowed = false` for every item. This is the next safe input for future remote-leak
work: first identify intervals that need split/re-ASR or mark-only treatment, then add a separate
repair profile only when the corpus evidence is good enough.

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
the exact non-dry-run command. After applying the lane, the CLI refreshes
`review_decisions_progress.json`: if more rows remain it points back to
the next remaining lane and the workspace flow. If progress does not exist yet, it falls back to
`murmurmark review first-lane` and `review lane apply first`. It prints
`murmurmark review apply` as the final batch step only when
`murmurmark review progress --session SESSION` says the review file is ready. `review progress`
also prints `next_lane` and the exact `review lane ...` / `review lane apply ...` commands for the
first remaining lane.
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
After `murmurmark review apply`, the CLI prints the next report command for the affected session or
corpus. For a single session it also prints the refreshed readiness summary, including the next
export or retention commands when the session is ready.
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
ASR noise, and `keep_me` for strong local-support cases that no longer need human review. It writes
`agent_reviewed_v1`, which is eligible for `--transcript-profile auto` after gates pass. It never
changes raw CAF files, Echo Guard outputs, ASR output or existing cleanup profiles.

```bash
murmurmark review agent
```

The normal manual review loop is available through the Swift CLI:

```bash
.build/debug/murmurmark review next latest
.build/debug/murmurmark review first-lane
.build/debug/murmurmark review lane check_local_recall --session latest
.build/debug/murmurmark review lane apply first --session latest
.build/debug/murmurmark review workspace
.build/debug/murmurmark review latest --lane fast_confirm_drop
.build/debug/murmurmark review workspace apply --session latest
.build/debug/murmurmark review progress --session latest
.build/debug/murmurmark review apply --session latest
```

`review next` writes its per-session review handoff under
`SESSION/derived/readiness/review-plan/`. `review first-lane --session SESSION`,
`review workspace --session SESSION`, `review workspace apply --session SESSION`,
`review progress --session SESSION`, and `review apply --session SESSION` default to those
session-local paths. Existing non-empty session-local plans are preserved by workspace/progress
commands, so an explicit lane pack is not replaced by an empty refresh. Use `review first-lane`,
`review workspace` or bare `review progress` when you want the global corpus queue.
When a session-local plan exists, `review next` also shows packed `review_actions`, saved grouped
rows and the remaining action count after the first lane, matching the units from
`murmurmark report corpus`.

Use the Python scripts directly only when debugging a specific review file, lane pack, or batch
application edge case.

`scripts/review-decisions-cli.py` is the fastest way to fill that checklist. It walks through
`review_decisions.template.jsonl`, plays the preferred stereo clip, shows the `Me`/`remote` texts and
writes `review_decisions.jsonl` after every answer. `Enter` accepts `suggested_decision`; `d`, `k`,
`r` and `s` mean `drop_me`, `keep_me`, `needs_review` and `skip`. The CLI respects
`allowed_decisions`, so `d=drop_me` is not accepted for audit-only local-recall rows. It also shows
nearby transcript turns from the reviewed profile, which helps distinguish leaked remote speech from
real local replies. When a row has several clips, the CLI prints `audio=1:...` shortcuts; type the
number to play that specific track without leaving the review flow. When all rows are closed, it
prints the `apply-review-decisions-batch.py --synthesize --refresh-reports` command that turns the
decisions into a fresh `reviewed_v1` profile and refreshed readiness reports.

`scripts/apply-review-decisions.py` closes the manual review loop. After the CLI or a manual edit has
filled each `decision` as `drop_me`, `keep_me`, `needs_review`, or `skip`, apply the decisions to a
session. The script
writes a separate `reviewed_v1` profile and never changes the automatic cleanup profiles. The
profile is eligible for `--transcript-profile auto` only when the review template has no remaining
`todo` rows for that session and every row respects `allowed_decisions`; partial or invalid review
stays as an audit artifact with failing gates.
For local-recall rows, `drop_me` is invalid because there is no transcript utterance to drop.
`keep_me` or `skip` closes the local-recall risk as checked; `needs_review` keeps it blocking.
The template also includes `suggested_decision` hints such as `drop_me` for probable duplicates, but
they are never applied until the `decision` field is edited explicitly.
For comparison, the suggested answer sheets can be applied into a separate shadow profile:

```bash
.build/debug/murmurmark review workspace apply \
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
operational readiness, and the next review plan, so the visible verdict is not stale.

Timeline repair treats `remote` as the authoritative `Colleagues` timeline. If whisper.cpp glues a long `Me` segment across a remote reply, the bridge cuts that mic candidate around guarded remote intervals, keeps only local islands from Echo Guard speaker state, and can run micro-ASR on those short islands. If no local island can be recovered, the misleading long `Me` block is dropped rather than published whole. `source_start`, `source_end`, `timeline_repair_examples.jsonl`, and `role_decisions.json` remain available for audit.

`--repair-profile current` is the default and keeps the current transcript path stable. `--repair-profile shadow_v2` writes a separate candidate transcript, quality report, timeline-repair report, comparison gates, and audit examples without replacing `transcript.md`. Shadow repair seeds every short local-island micro-ASR choice with the current result, then tests wider windows, alternate mic sources, leading silence, narrow boundary-prefix fixes such as `адно` -> `Ладно`, and a guarded start-of-call repair for short opening turns such as `Привет`, `Меня слышно?`, `Привет, да`. `repair_comparison.json` must pass no-regression gates before any shadow behavior is promoted.

Before recording, macOS must allow the terminal or Codex app to use Screen & System Audio Recording
and Microphone. `doctor` prints the current permission state, local dependency checks, model path,
pipeline readiness and the next normal CLI commands.

For the first real run, use [docs/runbooks/first-recording.md](docs/runbooks/first-recording.md).

For real work, install the local wrapper with `scripts/install-local.sh` and then use
`murmurmark record --target-bundle system`. Without `--duration`, recording runs until `Ctrl-C`.
Without `--out`, every recording gets a unique directory under `./sessions`, so previous sessions
are not overwritten. On success, `record` prints the session path and the next `murmurmark process ...`
command.

Before trying cleanup on a real speaker-bleed session, run the [Echo Guard delay lab](docs/runbooks/echo-guard-lab.md). If delay confidence is unstable, do not trust cleanup output yet; use transcript-level suppression and keep raw mic for ASR fallback.

## Product Shape

```text
MurmurMark Capture
  -> mic.caf + remote.caf + session.json + events.jsonl

MurmurMark Transcribe
  -> transcript.rich.json + speaker_map.json + quality_report.json

MurmurMark Evidence
  -> utterance citations + corrections + review flags

MurmurMark Synthesis
  -> quality verdict, extractive notes, potential decisions/actions/risks, docs patches later

MurmurMark Policy
  -> privacy modes, retention, redaction, provider approvals
```

## v1 Decisions

- Intended remote audio capture: Core Audio Process Tap for the meeting app.
- Current minimal remote audio capture: ScreenCaptureKit audio output.
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
- [docs/runbooks/transcribe-simple-whispercpp.md](docs/runbooks/transcribe-simple-whispercpp.md): temporary local transcription with whisper.cpp.
- [docs/decisions/tradeoffs.md](docs/decisions/tradeoffs.md): accepted and rejected tradeoffs.
- [docs/rfc/0001-v1-scope.md](docs/rfc/0001-v1-scope.md): implementation RFC for v1.
- [docs/adr/](docs/adr/): architecture decision records.
- [docs/adr/0010-use-preserve-local-fir-for-current-echo-guard.md](docs/adr/0010-use-preserve-local-fir-for-current-echo-guard.md): current Echo Guard cleanup default.
- [docs/backlog/mic-remote-bleed-reduction.md](docs/backlog/mic-remote-bleed-reduction.md): active Echo Guard work for reducing remote audio bleed in the mic track.

## Implementation Posture

The intended repository shape is still a monorepo, but the first implemented piece is deliberately narrow:

```text
apps/macos/                 native menubar app, later
Sources/MurmurMarkCLI/      current minimal recorder CLI
Sources/MurmurMarkCaptureCore/
pipeline/                   Python-heavy ASR and synthesis code, later
docs/                       current focus
examples/                   domain packs and sample policies
```

The ScreenCaptureKit backend is a working bridge for the first local smoke tests. The documented Core Audio Process Tap backend remains the target design for more precise per-application capture.
