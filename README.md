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

Current corpus snapshot from `murmurmark report corpus` on 2026-07-01:

- operational status: `pilot_ready_with_review`;
- working sessions in scope: `19`;
- diagnostic sessions excluded from readiness: `26`;
- session readiness: `14/19 ready_for_notes`, `4/19 review_first`, `1/19` with manual review required;
- selected notes review burden: `0.86 min`;
- full transcript/export review surface: `3.52 min`;
- mandatory review queue: `7` actions / `10.85s` raw audio;
- irreducible review gate: `pilot_ready_with_irreducible_review`;
- safe suggested rows still pending: `0`.

This does not mean “zero review”. It means the current operational corpus has no hidden blocker:
safe local evidence has been applied where available, and the remaining queue is short, explicit and
not safely closable by the current local agents.

Current quality goal: [Operational Corpus Green v1](docs/project/current-goal.md). `review suggested
apply` is cumulative, keeps already closed rows, consumes cached stronger-audio-judge and Target-Me
evidence in lane suggestions, and makes `review progress`, `status`, `report` and `suggested_closure`
agree on the same remaining queue. Latest product milestone: Export Bundle Quality v1; `finish`
writes a readable local handoff bundle whose `index.md` answers whether the result can be used, what
still needs review and what retention/privacy step comes next.

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

Optional near-realtime shadow mode:

```bash
murmurmark record --target-bundle system --live-pipeline
murmurmark status latest
murmurmark latest
SESSION="sessions/<printed-session>"
less "$SESSION/derived/live/transcript.draft.md"
murmurmark next latest
```

This mode writes closed audio segments and a live draft under `derived/live/` while recording. After
stop it runs the existing batch-grade reconcile through `murmurmark process --skip-build` and writes
`derived/live/final_reconcile_report.json`. The normal batch result remains authoritative until
corpus gates prove that the live branch matches it. If the live worker fails, recording should still
produce a normal raw session package. If you need only the draft and want to run batch processing
manually later, use `--live-no-finalize`.

To inspect live parity over a local corpus:

```bash
murmurmark corpus live all
less sessions/_reports/live-pipeline/live_corpus_gates_report.md
```

The expected v1 result is still `shadow_only_do_not_promote`: the report should explain which gates
are evaluated and which remain blockers.

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

Review lane suggestions consume existing Target-Me rows as high-confidence keep evidence. A normal
`review suggested` run does not refresh Target-Me by default because it can be slower than the review
handoff itself. To refresh it inside the suggested flow deliberately:

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
- keep near-realtime processing as a shadow CLI branch: `record --live-pipeline` now writes durable
  live segments, a draft transcript and advisory live-vs-batch comparison artifacts, but the batch
  pipeline is still authoritative;
- use `murmurmark corpus live` to keep live promotion blocked until parity gates cover order, local
  recall, remote leakage, review burden and chunk-boundary risks;
- make `process -> next -> review -> export -> retention` feel boring and repeatable;
- keep README, runbooks and roadmap aligned with the actual CLI.

Near-term goals for discussion:

1. Operational Corpus Green follow-up: keep `pilot_ready_with_review` stable, reduce the remaining
   irreducible queue when new local evidence appears, and prevent status drift.
2. Target-Me evidence hardening: turn the promising `resemblyzer_dvector_v0` shadow signal into safe
   review suggestions behind corpus gates, without automatic transcript edits.
3. Near-Realtime Pipeline Shadow v1 follow-up: harden the first live segment/draft worker with
   overlap-aware Echo Guard, resumable worker state and corpus parity gates.
4. Audio candidate promotion readiness: keep `coverage_v2_remote_gate_local_fir` shadow-only until
   broader corpus gates prove it is safe beyond selected audit windows.
5. Operational polish: make the happy path clearer when recording stops unexpectedly, when a session
   is partial, or when ASR will take a long time.
6. Export readiness follow-up: keep improving the final handoff after Export Bundle Quality v1,
   especially Obsidian-vault placement and reviewed proposal exports.
7. Regression discipline: keep a small stable corpus gate that catches transcript/order/local-recall
   regressions before new heuristics ship.
8. Evidence notes vNext: improve extractive notes quality while preserving citations and review
   flags.
9. Open-source release hardening: trim private fixtures, document setup, add security/contact
   guidance and keep generated/private artifacts ignored.

Recently completed:

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
