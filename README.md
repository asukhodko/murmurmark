# MurmurMark

MurmurMark is a local-first macOS meeting capture and notes pipeline.

It records the user's microphone and the selected meeting application's audio into separate local tracks, without virtual audio devices, without changing the meeting app's audio routing, and without uploading raw audio by default. The recording package is then processed into a speaker-aware transcript, evidence-backed meeting notes, and documentation updates under an explicit privacy policy.

The repository now contains a first minimal Swift CLI plus local transcript and extractive notes scripts. Its current scope is capture, Echo Guard preprocessing, simple `Me`/`Colleagues` transcription, timeline repair for common mic/remote ordering failures, quality verdicts, and evidence-backed extractive notes. Full diarization and generative synthesis remain documented future work.

## Current CLI

### End-to-End From an Existing Recording

Use this when the session directory already exists and raw recording is complete.

```bash
cd murmurmark

SESSION=./sessions/<session>
MODEL="$HOME/.local/share/murmurmark/models/whisper.cpp/ggml-large-v3-q5_0.bin"

git pull
.venv/bin/python scripts/run-session-pipeline.py "$SESSION" \
  --model "$MODEL" \
  --language ru \
  --force-asr

jq '{status, outputs}' "$SESSION/derived/pipeline-run/pipeline_run_report.json"
less "$SESSION/derived/readiness/session_readiness.md"
less "$SESSION/derived/synthesis-simple/extractive/quality_verdict.md"
less "$SESSION/derived/synthesis-simple/extractive/notes.md"
```

For a regression set or several real meetings, build a private quality summary under the ignored
`sessions/_reports/` tree:

```bash
.venv/bin/python scripts/report-session-quality.py \
  sessions/<session-1> \
  sessions/<session-2>

less sessions/_reports/session-quality/session_quality_report.md
```

To turn audited sessions into a reusable private regression set for future cleanup and audio-judge
work, build a corpus from existing audio-review audits:

```bash
.venv/bin/python scripts/build-regression-corpus.py \
  sessions/<session-1> \
  sessions/<session-2> \
  --per-label 16 \
  --max-items 160

.venv/bin/python scripts/evaluate-regression-corpus.py \
  --corpus-dir sessions/_reports/regression-corpus

.venv/bin/python scripts/train-audio-judge-v0.py \
  --corpus-dir sessions/_reports/regression-corpus \
  --out-dir sessions/_reports/audio-judge-v0

.venv/bin/python scripts/report-operational-readiness.py \
  --session-quality sessions/_reports/session-quality/session_quality_report.json \
  --corpus-evaluation sessions/_reports/regression-corpus/regression_corpus_evaluation.json \
  --audio-judge sessions/_reports/audio-judge-v0/audio_judge_v0_report.json \
  --audio-judge-queue sessions/_reports/audio-judge-v0/audio_judge_v0_queue_predictions.jsonl

.venv/bin/python scripts/build-review-plan.py \
  --operational-readiness sessions/_reports/operational-readiness/operational_readiness_report.json

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

```bash
cd murmurmark

MODEL="$HOME/.local/share/murmurmark/models/whisper.cpp/ggml-large-v3-q5_0.bin"

git pull
swift build
.build/debug/murmurmark doctor

.build/debug/murmurmark record --target-bundle system

SESSION="$(ls -td sessions/* | head -1)"
echo "SESSION=\"$SESSION\""

.venv/bin/python scripts/run-session-pipeline.py "$SESSION" \
  --model "$MODEL" \
  --language ru

jq '{status, outputs}' "$SESSION/derived/pipeline-run/pipeline_run_report.json"
less "$SESSION/derived/readiness/session_readiness.md"
less "$SESSION/derived/synthesis-simple/extractive/quality_verdict.md"
less "$SESSION/derived/synthesis-simple/extractive/notes.md"
```

`scripts/run-session-pipeline.py` is the normal post-recording runner. It calls Echo Guard,
whisper.cpp transcription, shadow timeline repair, local-recall audit, group-overlap audit, audio-review audit,
`audit_cleanup_v1..v4`, and extractive synthesis, then writes
`derived/pipeline-run/pipeline_run_report.json` and `derived/readiness/session_readiness.md`.
Read `session_readiness.md` first: it gives the session use gate, selected profile, review burden,
and links to the transcript, notes, quality verdict and audio-review report.
`transcript.md` is the stable baseline output. `transcript.shadow_v2.md` is the current best candidate when `repair_comparison.json` passes. The shadow profile does not replace the baseline transcript; it writes separate audit and comparison artifacts so changes can be checked before promotion.
`scripts/audit-group-overlaps.py` is an optional diagnostic step for group calls. It classifies `Me`/`Colleagues` timeline overlaps into harmful, benign and review buckets, writes listenable clips, and does not change transcripts or quality verdicts.
`scripts/apply-audit-cleanup.py` is an optional conservative cleanup over the group audit. It writes a separate `audit_cleanup_v1` profile and only drops whole `Me` utterances when the audit strongly supports remote duplicate or ASR-noise classification. It never edits `shadow_v2`.
`scripts/synthesize-simple-extractive.py` then selects or accepts a dialogue profile, writes a quality verdict, and creates local extractive notes where every item cites utterance IDs.

### Command Reference

```bash
swift build
swift run murmurmark doctor
swift run murmurmark list-apps
.build/debug/murmurmark record --target-bundle system
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
.venv/bin/python scripts/transcribe-simple-whispercpp.py ./sessions/<session> --prompt-file examples/domain-packs/backend-platform/whisper-prompt.ru.txt
.venv/bin/python scripts/transcribe-simple-whispercpp.py ./sessions/<session> --repair-profile shadow_v2 --skip-export --skip-transcribe
.venv/bin/python scripts/run-session-pipeline.py ./sessions/<session> --model "$HOME/.local/share/murmurmark/models/whisper.cpp/ggml-large-v3-q5_0.bin"
.venv/bin/python scripts/synthesize-simple-extractive.py ./sessions/<session> --transcript-profile auto
.venv/bin/python scripts/audit-local-recall.py ./sessions/<session> --profile shadow_v2
.venv/bin/python scripts/audit-group-overlaps.py ./sessions/<session> --profile shadow_v2 --write-clips
.venv/bin/python scripts/apply-audit-cleanup.py ./sessions/<session> --input-profile shadow_v2 --output-profile audit_cleanup_v1
.venv/bin/python scripts/synthesize-simple-extractive.py ./sessions/<session> --transcript-profile audit_cleanup_v1
.venv/bin/python scripts/build-audio-review-pack.py ./sessions/<session> --profile audit_cleanup_v1 --write-clips
.venv/bin/python scripts/audit-audio-review-pack.py ./sessions/<session>
.venv/bin/python scripts/apply-audit-cleanup.py ./sessions/<session> --input-profile audit_cleanup_v1 --output-profile audit_cleanup_v2
.venv/bin/python scripts/synthesize-simple-extractive.py ./sessions/<session> --transcript-profile audit_cleanup_v2
.venv/bin/python scripts/apply-audit-cleanup.py ./sessions/<session> --input-profile audit_cleanup_v2 --output-profile audit_cleanup_v3 --audio-judge-queue sessions/_reports/audio-judge-v0/audio_judge_v0_queue_predictions.jsonl
.venv/bin/python scripts/synthesize-simple-extractive.py ./sessions/<session> --transcript-profile audit_cleanup_v3
.venv/bin/python scripts/apply-audit-cleanup.py ./sessions/<session> --input-profile audit_cleanup_v3 --output-profile audit_cleanup_v4 --audio-judge-queue sessions/_reports/audio-judge-v0/audio_judge_v0_queue_predictions.jsonl
.venv/bin/python scripts/synthesize-simple-extractive.py ./sessions/<session> --transcript-profile audit_cleanup_v4
.venv/bin/python scripts/report-session-quality.py ./sessions/<session> --write-session-readiness
.venv/bin/python scripts/build-regression-corpus.py ./sessions/<session>
.venv/bin/python scripts/evaluate-regression-corpus.py
.venv/bin/python scripts/train-audio-judge-v0.py
.venv/bin/python scripts/report-operational-readiness.py
.venv/bin/python scripts/build-review-plan.py
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

The first synthesis bridge is `scripts/synthesize-simple-extractive.py`. It reads only transcript-derived JSON, chooses the best safe dialogue profile, writes `quality_verdict.json`/`.md`, and creates extractive `notes.md` plus `evidence_notes.json` and `review_items.jsonl` under `derived/synthesis-simple/extractive/`. It does not call an LLM and does not infer facts beyond quoted utterances. Its v3 notes path builds topic blocks, scores action/decision/risk/question candidates, hides meeting facilitation from Markdown, shows only selected top items, and keeps every hidden weak/process/facilitation candidate in `evidence_notes.json`.

The group overlap audit is `scripts/audit-group-overlaps.py`. It reads transcript overlaps, Echo Guard `speaker_state.jsonl`, and local audio derivatives, then writes `derived/audit/group-overlaps/`. It separates likely harmful `Me` duplicates or remote leakage from expected group-call double-talk and timing overlap. This is audit-only: no transcript, Echo Guard output, synthesis output, or `quality_verdict` is modified.

The local recall audit is `scripts/audit-local-recall.py`. It reads timeline-repair examples and
Echo Guard `speaker_state.jsonl`, then writes `derived/audit/local-recall/`. Its job is to explain
low `local_only_island_recall`: short or weak unrecovered islands are recorded as low-risk evidence,
while stronger unrecovered local speech stays as a blocking `low_local_recall` risk. It never edits
transcripts.

Audit-informed cleanup is `scripts/apply-audit-cleanup.py`. It reads `clean_dialogue.shadow_v2.json` and the group overlap audit, then writes only `audit_cleanup_v1` artifacts under `derived/transcript-simple/whisper-cpp/resolved/` and `derived/transcript-simple/whisper-cpp/audit-cleanup/`. In conservative mode it may drop whole `Me` utterances only when they are high-confidence remote duplicates or short unsupported ASR noise. Double-talk, timing overlap, remote leak, and human-review regions are kept and marked.

The audio review layer is `scripts/build-audio-review-pack.py` plus `scripts/audit-audio-review-pack.py`.
It collects suspicious transcript regions from `needs_review`, overlaps, group-overlap audit and cleanup
rejections, cuts local comparison clips under `derived/audit/audio-review-pack/`, then classifies them
with local audio/text metrics. The output separates likely reliable regions, probable transcript errors
and regions that should go to a stronger local audio judge. It is audit-only and never edits transcript
profiles or raw capture. When local metrics disagree only between benign explanations such as
double-talk, timing overlap and reliable local speech, the audit treats the item as likely reliable
instead of sending it to the stronger-judge queue.

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
numeric audio/text metrics. It does not edit transcripts; it reports cross-session validation quality
and candidate predictions for later cleanup experiments. Queue predictions stay conservative:
`drop_error` and `mark_only_error` remain human-review items until a separate cleanup profile consumes
them with its own gates. `audit_cleanup_v3` is that first consuming profile, but it only accepts
high-confidence `drop_error` candidates that also satisfy the existing cleanup safety checks.
`audit_cleanup_v4` is still conservative, but it adds a narrow expanded duplicate gate for
audio-judge `drop_error` predictions above `0.93`: strong text containment, low local support, good
overlap coverage, no protected markers and no notes impact are required. It remains mark-only for
`remote_leak`, `lost_me`, `uncertain`, double-talk and timing overlap.
`scripts/report-operational-readiness.py` combines session quality, corpus readiness and audio-judge
shadow readiness into a practical verdict such as `pilot_ready_with_review` for medium-risk working
meetings. It also assigns per-session use gates such as `ready_for_notes` and `review_first`, then
writes a promotion plan and a short review queue with concrete `afplay` commands for the
highest-priority audio-review clips. The promotion plan explains what still blocks
`medium_risk_ready`: unresolved warnings, sessions not ready for notes, and the remaining review
minutes. The queue is filtered through the selected transcript profile, so already-dropped `Me`
utterances do not stay in the operational review list. It also ignores stale audio-judge queue rows
when the current audio-review audit has since reclassified that item as reliable.

`scripts/build-review-plan.py` turns that operational review queue into a compact working checklist
under `sessions/_reports/review-plan/`. It groups nearby risky intervals by session, estimates the
actual listening time, keeps ready-to-run `afplay`/`ffplay` commands, and gives a small protocol for deciding
whether each `Me` candidate should be dropped, kept, or left as `needs_review`. Local-recall items
are audit-only review rows: they can clear or keep the local-recall risk, but they do not insert
missing text into the transcript. The plan itself is audit-only and does not edit transcript profiles.
For `remote_duplicate`, `drop_me` is suggested only when the duplicate covers enough of the whole
`Me` utterance. If the overlap is only a slice of a longer local utterance, the row is marked
`check_unique_me_content` and the safe default is `needs_review`.

`scripts/review-decisions-cli.py` is the fastest way to fill that checklist. It walks through
`review_decisions.template.jsonl`, plays the preferred stereo clip, shows the `Me`/`remote` texts and
writes `review_decisions.jsonl` after every answer. `Enter` accepts `suggested_decision`; `d`, `k`,
`r` and `s` mean `drop_me`, `keep_me`, `needs_review` and `skip`.

`scripts/apply-review-decisions.py` closes the manual review loop. After the CLI or a manual edit has
filled each `decision` as `drop_me`, `keep_me`, `needs_review`, or `skip`, apply the decisions to a
session. The script
writes a separate `reviewed_v1` profile and never changes the automatic cleanup profiles. The
profile is eligible for `--transcript-profile auto` only when the review template has no remaining
`todo` rows for that session; partial review stays as an audit artifact with failing gates.
For local-recall rows, `drop_me` is invalid because there is no transcript utterance to drop.
`keep_me` or `skip` closes the local-recall risk as checked; `needs_review` keeps it blocking.
The template also includes `suggested_decision` hints such as `drop_me` for probable duplicates, but
they are never applied until the `decision` field is edited explicitly.
`scripts/apply-review-decisions-batch.py` applies the same edited file to every session mentioned in
the review plan and can immediately regenerate extractive notes with `--synthesize`.

Timeline repair treats `remote` as the authoritative `Colleagues` timeline. If whisper.cpp glues a long `Me` segment across a remote reply, the bridge cuts that mic candidate around guarded remote intervals, keeps only local islands from Echo Guard speaker state, and can run micro-ASR on those short islands. If no local island can be recovered, the misleading long `Me` block is dropped rather than published whole. `source_start`, `source_end`, `timeline_repair_examples.jsonl`, and `role_decisions.json` remain available for audit.

`--repair-profile current` is the default and keeps the current transcript path stable. `--repair-profile shadow_v2` writes a separate candidate transcript, quality report, timeline-repair report, comparison gates, and audit examples without replacing `transcript.md`. Shadow repair seeds every short local-island micro-ASR choice with the current result, then tests wider windows, alternate mic sources, leading silence, narrow boundary-prefix fixes such as `адно` -> `Ладно`, and a guarded start-of-call repair for short opening turns such as `Привет`, `Меня слышно?`, `Привет, да`. `repair_comparison.json` must pass no-regression gates before any shadow behavior is promoted.

Before recording, macOS must allow the terminal or Codex app to use Screen & System Audio Recording and Microphone. `doctor` prints the current permission state and the basic toolchain checks.

For the first real run, use [docs/runbooks/first-recording.md](docs/runbooks/first-recording.md).

For real work, run `swift build` once and then use `.build/debug/murmurmark record --target-bundle system`. Without `--duration`, recording runs until `Ctrl-C`. Without `--out`, every recording gets a unique directory under `./sessions`, so previous sessions are not overwritten.

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
