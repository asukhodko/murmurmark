# MurmurMark

Local-first meeting memory for sensitive work.

MurmurMark records a meeting into separate local `mic` and `remote` tracks, processes the session
locally, and produces a reviewable transcript, quality verdict, evidence-backed notes, export bundle
and retention plan.

The project is CLI-first. A future app can be useful, but the main product is a reliable command-line
pipeline with explicit evidence, review gates and local privacy controls.

## Mission

MurmurMark exists to turn sensitive working conversations into durable, local, evidence-backed
memory without sending raw meeting audio to a cloud recorder.

It is meant for people who need to recover what was said, what was decided and what should happen
next, while keeping uncertainty visible. The system should never pretend that a risky transcript is
clean: unclear regions remain review items, and generated notes must point back to utterance or audit
evidence.

## Current Status

The CLI pipeline is usable for regular medium-risk working meetings with a short explicit review
queue.

Current corpus snapshot from `murmurmark next corpus --refresh` on 2026-06-30:

- operational status: `medium_risk_ready`;
- use gate: `can_use_medium_risk: true`;
- working sessions in scope: `15`;
- diagnostic sessions excluded from readiness: `26`;
- session readiness: `14/15 ready_for_notes`, `1/15 review_first`, `0 incomplete`;
- selected notes review burden: `0.55 min`;
- full transcript/export review surface: `3.05 min`;
- remaining actionable review queue: `0` actions;
- one session remains `review_first` because its residual risk is documented but no longer has an
  actionable review lane.

This does not mean "zero review". It means MurmurMark is already useful when the user accepts the
remaining explicit risk. Full transcript/export can still be blocked while evidence-backed notes are
usable.

Latest completed project goal: [Recording Reliability](docs/project/current-goal.md). `record` now
keeps capturing until explicit user stop, or writes a durable partial session with an honest next
command.

## What Works Now

- Two-track macOS capture through ScreenCaptureKit: local microphone and selected system/app audio.
- Durable session package with raw CAF tracks, `session.json`, events and derived artifacts.
- Echo Guard preprocessing with local FIR cleanup and a preserve-local policy.
- Local `whisper.cpp` transcription with Russian support, prompt/domain hints, timeline repair and
  start-of-call repair.
- Audit layers for order, local recall, group overlaps, audio review and optional stronger local
  audio judge.
- Conservative cleanup and repair profiles that write separate transcript candidates instead of
  mutating raw capture or baseline output.
- Deterministic extractive notes with quality verdicts and evidence IDs.
- Review lane packs, suggested answers, review apply flow and corpus readiness reports.
- Markdown/Obsidian-style export bundles and retention planning.
- Local release bundle, self-test, acceptance gate and open-source readiness check.
- Recording reliability: duration/SIGINT complete normally, SIGTERM/SIGHUP/unrecovered capture stops
  become explicit partial sessions, and `doctor` catches missing shareable displays before recording.

## What Is Still Out Of Scope

- Per-person diarization inside `Colleagues`.
- Studio-quality echo removal.
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

Stop recording with `Ctrl-C`. Without `--duration`, recording is expected to continue until you stop
it. On success the CLI prints:

```bash
SESSION="sessions/<timestamp>"
recommended_next: murmurmark process sessions/<timestamp>
```

If capture stops before `Ctrl-C`, MurmurMark finalizes a partial session instead of pretending it is
complete. In that case `record`, `status` and `next` point to `murmurmark inspect ...`; normal
processing is blocked unless you explicitly pass `--allow-partial` for debugging.

Then run:

```bash
murmurmark process latest
murmurmark next latest
murmurmark status latest

murmurmark notes latest --kind verdict
murmurmark notes latest
murmurmark transcript latest
```

If `next` or `status` prints a review command, follow that command before relying on the result for
medium-risk work.

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

murmurmark notes "$SESSION" --kind verdict
murmurmark notes "$SESSION"
murmurmark transcript "$SESSION"
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
murmurmark review first-lane --session latest

# Listen/edit the generated answer sheet, then:
murmurmark review lane apply first --session latest
murmurmark review apply --session latest
murmurmark status latest
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

The review loop writes decisions into separate reviewed profiles. It should not rewrite raw audio or
hide unresolved risk.

## Export And Retention

Export is local and blocks when readiness reports export blockers, unless `--force` is used
deliberately.

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

Useful targeted checks:

```bash
murmurmark audit local-recall latest
murmurmark audit order latest
murmurmark audit group-overlaps latest --write-clips
murmurmark audit audio-review latest --write-clips
murmurmark audit stronger-audio-judge latest --max-items 80
```

The stronger audio judge is a local second opinion over short review clips. It does not replace the
main `whisper.cpp` transcript.

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
murmurmark export latest --format markdown --include-json
murmurmark retention plan latest
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
- [Latest completed goal](docs/project/current-goal.md)

## Roadmap Summary

Current focus:

- keep the corpus at `medium_risk_ready` or better;
- keep `next`/`status`/`review` honest when the actionable queue is already empty but residual risk
  remains documented;
- make `process -> next -> review -> export -> retention` feel boring and repeatable;
- keep README, runbooks and roadmap aligned with the actual CLI.

Near-term goals for discussion:

1. Operational polish: make the happy path clearer when recording stops unexpectedly, when a session
   is partial, or when ASR will take a long time.
2. Export readiness: make Markdown/Obsidian bundles the normal output for usable meetings, with
   explicit blockers and retention guidance.
3. Regression discipline: keep a small stable corpus gate that catches transcript/order/local-recall
   regressions before new heuristics ship.
4. Evidence notes vNext: improve extractive notes quality while preserving citations and review
   flags.
5. Open-source release hardening: trim private fixtures, document setup, add security/contact
   guidance and keep generated/private artifacts ignored.

Recently completed:

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
scripts/check.sh
murmurmark self-test
murmurmark acceptance --skip-release
scripts/check-open-source-readiness.sh
```

Before trusting a pipeline change:

```bash
murmurmark corpus process all --per-label 16 --max-items 160
murmurmark corpus gate
murmurmark next corpus --refresh
```

Raw `audio/*.caf` captures are user data. Do not commit them, rewrite them or delete them through a
retention apply step unless that is the explicit requested action.
