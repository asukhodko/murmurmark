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

Current corpus snapshot from `murmurmark report corpus` on 2026-06-30:

- operational status: `not_ready`;
- use gate: `can_use_medium_risk: false`;
- working sessions in scope: `16`;
- diagnostic sessions excluded from readiness: `26`;
- session readiness: `14/16 ready_for_notes`, `1/16 review_first`, one risky session blocking the
  operational corpus;
- selected notes review burden: `0.69 min`;
- full transcript/export review surface: `3.19 min`;
- remaining actionable review queue: `5` actions, all in `sessions/2026-06-30_11-15-56`;
- suggested closure reports exist for `5` sessions: `8` generated suggestions, `0` actionable
  keep/drop rows, `8` needs-review rows, `61` manual rows remaining.

This does not mean the CLI is unusable. It means the current corpus is honestly blocked by a small
manual review queue instead of pretending to be green. Most sessions are still usable for
evidence-backed notes when their own `status` says so; full transcript/export can remain blocked
until the explicit review items are closed.

Current project goal: [Echo Guard Complete Removal v0](docs/project/current-goal.md). Latest
completed milestone: Export Bundle Quality v1; `finish` now writes a readable local handoff bundle
whose `index.md` answers whether the result can be used, what still needs review and what
retention/privacy step comes next.

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
- Export Bundle Quality v1: `finish` writes a readable handoff with "Can I use this?", review
  burden, evidence-backed notes, transcript IDs and retention/privacy next steps.
- Local release bundle, self-test, acceptance gate and open-source readiness check.
- Recording reliability: duration/SIGINT complete normally, SIGTERM/SIGHUP/unrecovered capture stops
  become explicit partial sessions, and `doctor` catches missing shareable displays before recording.

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
murmurmark finish latest
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

`review suggested` builds all lane packs, dry-runs generated suggested answers and prints a
`suggested_closure` block: before/after manual rows, generated/actionable/needs-review counts, a
conservative readiness projection, rows that can be closed without listening and the exact remaining
manual queue by lane. `review suggested apply` writes only those safe reviewed rows, keeps dots as
the manual queue, refreshes `reviewed_v1` readiness when anything was closed and still blocks export
when risk remains. If nothing is safe to close, it writes no decisions and points to the first
remaining manual lane. Run `murmurmark report corpus` after that when you want the corpus readiness
delta. The review loop writes decisions into separate reviewed profiles. It should
not rewrite raw audio or hide unresolved risk.

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
directly. Export is local and blocks when readiness reports export blockers, unless `--force` is used
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
- [Current goal](docs/project/current-goal.md)

## Roadmap Summary

Current focus:

- use the shadow `offline_aec_v2_v0` Echo Guard lab to understand which complete-removal
  hypotheses are worth pursuing next;
- keep `local_fir` as the default: v0 improves proxy metrics, but does not beat `local_fir` on
  ASR-token leakage gates;
- rank echo-removal candidates by remote-token leakage, local-word recall and artifacts, not by
  loudness or ERLE alone;
- keep `next`/`status`/`review` honest about residual risk while this lab is experimental;
- make `process -> next -> review -> export -> retention` feel boring and repeatable;
- keep README, runbooks and roadmap aligned with the actual CLI.

Near-term goals for discussion:

1. Echo Guard Complete Removal vNext: use the v0 negative result to test more targeted approaches:
   better speaker-state segmentation, segment-local candidate switching, target-speaker extraction
   or token-level remote-forbidden decoding.
2. Suggested review closure maintenance: keep the preview/apply path visible in session and corpus
   reports, distinguish generated/actionable/needs-review suggestions and avoid closing unmatched
   risky rows.
3. Operational polish: make the happy path clearer when recording stops unexpectedly, when a session
   is partial, or when ASR will take a long time.
4. Export readiness follow-up: keep improving the final handoff after Export Bundle Quality v1,
   especially Obsidian-vault placement and reviewed proposal exports.
5. Regression discipline: keep a small stable corpus gate that catches transcript/order/local-recall
   regressions before new heuristics ship.
6. Evidence notes vNext: improve extractive notes quality while preserving citations and review
   flags.
7. Open-source release hardening: trim private fixtures, document setup, add security/contact
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
