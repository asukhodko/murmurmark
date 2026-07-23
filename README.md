# MurmurMark

Local-first meeting transcription for sensitive work.

MurmurMark records separate local microphone and remote system-audio tracks, processes them locally
and produces a transcript, quality verdict, evidence-backed notes, review plan, export bundle and
retention plan.

The product is CLI-first. Batch processing is authoritative. Live preview is an optional shadow
that cannot replace or weaken the durable recording.

## Mission

MurmurMark turns sensitive working conversations into reliable local transcripts and
evidence-backed meeting memory without sending raw meeting audio to a cloud recorder.

The user should be able to start and stop a meeting recording once and receive an honest result
without launching or supervising internal stages. Uncertain regions remain explicit review items.
Notes and exports point back to transcript or audit evidence.

## Reliability Contract

For a supported macOS setup, MurmurMark produces one of these outcomes:

- `ready_for_notes`: transcript and notes are usable for ordinary follow-up;
- `review_first`: the result is useful, but explicit review is required before guarded export;
- `blocked`: capture or transcript evidence is insufficient for safe use.

Raw `audio/mic/*.caf` and `audio/remote/*.caf` files are immutable processing inputs. Derived
profiles are isolated and selected only after no-regression gates pass.

## Install

Prerequisites:

- macOS with Screen and System Audio Recording and microphone permissions;
- Swift toolchain;
- `ffmpeg`, `whisper-cpp` and `jq`;
- project Python virtual environment and local whisper.cpp model.

```bash
cd murmurmark
source .venv/bin/activate

scripts/install-local.sh
export PATH="$HOME/.local/bin:$PATH"

murmurmark config init
murmurmark doctor --strict
murmurmark self-test
```

`scripts/install-local.sh` builds the release CLI and installs its wrapper into
`$HOME/.local/bin`. During development, `swift build` also provides `.build/debug/murmurmark`.

The optional stronger audio judge uses a local faster-whisper model. Its absence must not block the
normal pipeline; `murmurmark doctor` reports it as an optional warning.

## Stable Meeting Workflow

The normal meeting path is one command:

```bash
murmurmark meeting --target-bundle system
```

Run the install-time `doctor --strict` and `self-test` checks after an update or environment
change, not before every meeting.

The command prints `SESSION="sessions/<id>"`. The first `Ctrl-C` stops and finalizes capture, then
authoritative processing continues automatically. A second `Ctrl-C` checkpoints processing and
prints an exact `murmurmark meeting --resume SESSION` command. The final summary names the
transcript, notes, verdict, unresolved review burden, export status and raw preservation result.

Capture runs in a short-lived child process. It exits and releases ScreenCaptureKit/ReplayKit before
batch processing begins. A new meeting may therefore start while an earlier meeting is still being
processed in another terminal. Run only one active capture at a time; the recording lock rejects a
second one. If ScreenCaptureKit startup does not complete, MurmurMark fails within a bounded timeout,
releases the lock and does not start post-processing. If capture is partial, sparse or silent,
processing blocks instead of publishing an empty successful transcript.

`meeting` already owns status, notes and transcript production. Do not paste unconditional
`status/outcome/transcript` commands after it: when capture startup fails, no finalized session
exists for those commands. Run low-level accessors only after a successful lifecycle or while
diagnosing an existing finalized session.

An empty conversation can still be a valid result, for example when nobody joins a call. MurmurMark
classifies it as `verified_no_speech` only when durable capture is complete, both raw tracks cover
the session, the microphone contains acoustic activity, remote audio is silent, ASR produced only
known hallucinations, and the local-recall and chunk-rebuild audits are clear. The evidence is kept
in `derived/synthesis-simple/extractive/no_speech_evidence.json`. An empty transcript without all of
these checks remains `failed`.

### Low-Level Recovery And Diagnostics

The individual commands remain available when diagnosing a stage or recovering an older session:

```bash
SESSION="sessions/<id>"
murmurmark inspect "$SESSION"
murmurmark process "$SESSION"
murmurmark enrich "$SESSION"
murmurmark next "$SESSION"
murmurmark status "$SESSION"
murmurmark finish "$SESSION"
```

Plain `process` is the authoritative path. `process --full` is a blocking compatibility mode and is
not used by `meeting`. `--force-asr` and `--allow-partial` are diagnostics only and are never added
by the meeting supervisor.

An interrupted processing run is resumed with the same command and session path:

```bash
murmurmark meeting --resume "$SESSION"
```

## Live Shadow Workflow

Live Evidence uses the same durable capture and a best-effort committed-PCM sidecar:

```text
capture -> durable raw writer -> stable session
                    |
                    +-> bounded committed PCM queue -> live draft
```

Recording terminal:

```bash
murmurmark meeting --target-bundle system --experiment live-shadow-v1
```

During recording, the same terminal shows only newly added or revised conservative live turns.
The line `[live] inline preview started` confirms that the read-only console watcher is active.
To keep the recording terminal quiet, add `--live-no-console`.

An optional second terminal can attach to the same preview without starting another capture or ASR:

```bash
cd murmurmark
export PATH="$HOME/.local/bin:$PATH"

SESSION="sessions/<value-printed-by-the-recording-terminal>"
murmurmark live watch "$SESSION"
```

The preview is advisory. Sidecar timeout, lag or backpressure may make it partial, but must not
damage raw capture. The inline console is a separate fail-open reader of
`derived/live/transcript.preview.md`; it never receives audio and cannot block capture. The old
`--live-pipeline` transport is unsafe and lab-only.

## Review And Finish

`meeting` automatically previews suggested review and applies only rows accepted by the existing
conservative gates. It attempts guarded export only when the structured outcome permits it.
Uncertain rows remain explicit and are reported as `ready_with_review`.

Manual commands remain available for those unresolved rows:

```bash
murmurmark review next "$SESSION"
murmurmark review suggested "$SESSION"
murmurmark review suggested apply "$SESSION"
murmurmark status "$SESSION"
murmurmark finish "$SESSION"
```

Suggested review closes only rows supported by current local evidence. Unresolved rows remain
explicit. `finish` attempts guarded local export and writes retention recommendations; it never
deletes raw audio.

```bash
murmurmark finish "$SESSION" --format markdown
murmurmark finish "$SESSION" --format obsidian
murmurmark retention plan "$SESSION"
```

Raw deletion requires a compatible policy, successful export and an explicit confirmation command.

## Important Artifacts

```text
sessions/<session-id>/
  audio/mic/000001.caf
  audio/remote/000001.caf
  session.json
  events.jsonl
  derived/
    outcome/
    preprocess/
    transcript-simple/whisper-cpp/
    synthesis-simple/extractive/
      no_speech_evidence.json  # only for an empty selected dialogue
    readiness/
    audit/
    retention/
```

Prefer CLI accessors over guessing profile-specific filenames:

```bash
murmurmark transcript "$SESSION"
murmurmark transcript "$SESSION" --path-only
murmurmark notes "$SESSION" --kind verdict
murmurmark notes "$SESSION"
murmurmark open "$SESSION" --kind transcript --command-only
```

## Current Development Direction

**One-Command Meeting Lifecycle v1** is complete. The command, bounded supervisor, resume contract,
automated regression coverage, fresh permission-capable capture soak and strict lifecycle acceptance
all pass. The normal user path is now one command plus `Ctrl-C`; the older commands remain available
for diagnostics and recovery.

**Echo Suppression Promotion v1** is complete with a reproducible `DO_NOT_PROMOTE`. The best
candidate, `coverage_v2_remote_gate_local_fir`, reduced bounded ASR-visible remote-risk seconds by
`68.2845%` and stayed within the runtime budget, but passed only `3/5` applicable speaker sessions.
Two real counterexamples lost protected local speech or a short overlap acknowledgement. The
automatic production policy therefore keeps the exact `local_fir` baseline.

The current quality goal is **Neural Residual Echo Suppression v1**. It keeps the signed timeline,
frozen corpus and fail-open policy, but tests a local remote-conditioned suppressor that can operate
inside mixed speech instead of hard-gating whole remote-active regions. Promotion still requires
local recall, chronology, review-burden, runtime and full-shadow gates.

The stable batch CLI already supports durable capture, resumable processing, evidence-backed review,
guarded export and retention planning. `local_speech_completion_v2` is promoted for its frozen
two-session scope. It classified six remaining local-recall rows / `35.85s`, safely closed three
rows / `22.4s`, materialized two independently confirmed local fragments, recognized one fragment
already present in the transcript, and removed the duplicate ASR tail `дает сп`. Three ambiguous
intervals / `13.45s` remain explicit. `residual_local_recall_v1` remains the fallback outside this
frozen promotion scope.

Speaker-Mode Transcript Quality Hardening v1 completed with a reproducible `DO_NOT_PROMOTE`.
Automatic acoustic classification matched `17/17` labeled sessions, the sparse-overrange limiter
raised accepted Echo Guard candidates from `11` to `13`, and the latest long speaker session now
passes the clean-audio gate. The isolated transcript profile safely proved three lossless retimes,
one double-talk interval and one genuine `Me` row, but reached only `2.7%` duplicate reduction and
`7.9%` review reduction. It therefore remains shadow-only.

Mixed-Utterance Remote Span Separation v1 also completed with a reproducible `DO_NOT_PROMOTE`. Its
frozen scope contains `12` mixed `Me` rows / `54.940s` across `7` sessions. All rows received stable
word-level evidence, but none had enough independent proof to remove a remote span while preserving
every local island. The profile applied no edits, did not regress raw inputs, remote text, local
recall, chronology, notes or verdict, and remains ineligible for automatic selection.

The dependent critical path is:

```text
One-Command Meeting Lifecycle (done)
->
Mixed-Utterance Remote Span Separation (done, DO_NOT_PROMOTE)
-> Echo Suppression Promotion v1 (done, DO_NOT_PROMOTE)
-> Neural Residual Echo Suppression v1 (current)
-> Evidence Notes and Export
-> Release-quality CLI
```

Remote diarization, heavy local validators, LLM synthesis and UI are parallel or parked work. Live
promotion remains blocked; Live Shadow is maintained as advisory evidence only.

See the [current goal](docs/project/current-goal.md), [readable roadmap](docs/roadmap/murmurmark-cli-roadmap.md)
and [OpsKarta v3 plan](docs/roadmap/murmurmark-cli-roadmap.plan.yaml).

## Scope And Limitations

- Current roles are `Me` and aggregate `Colleagues`; individual remote-speaker diarization is future
  work.
- Echo Guard reduces remote leakage but cannot yet guarantee that remote speech is absent from every
  `Me` candidate.
- Echo Guard records `speaker_playback`, `headphones_or_low_leak` or `uncertain` in
  `local_fir_report.json`; no user acoustic-mode flag is required.
- `local_speech_completion_v2` is selected only for sessions named by its passing frozen-corpus
  decision; stale hashes, missing local models or failed gates fall back without changing text.
- `mixed_utterance_separation_v1` is audit-only after `DO_NOT_PROMOTE`; it never replaces the
  selected transcript.
- `echo_suppression_promotion_v1` is also audit-only after `DO_NOT_PROMOTE`; the production policy
  selects `local_fir_role_masked` and never launches a second full ASR.
- Batch transcript is authoritative; live output is not used for export or retention decisions.
- No cloud ASR or cloud raw-audio upload is required by the normal workflow.
- A future UI must reuse CLI contracts and is not required for a useful product.

## Documentation

- [Documentation index](docs/00-index.md)
- [Mission and vision](docs/product/vision.md)
- [Product requirements](docs/product/prd-v1.md)
- [Current goal](docs/project/current-goal.md)
- [Echo Suppression Promotion v1 result](docs/research/2026-07-23-echo-suppression-promotion-v1.md)
- [Reliable transcription route](docs/project/reliable-transcription-route.md)
- [Readable roadmap](docs/roadmap/murmurmark-cli-roadmap.md)
- [OpsKarta v3 roadmap](docs/roadmap/murmurmark-cli-roadmap.plan.yaml)
- [Meeting lifecycle contract](docs/contracts/meeting-lifecycle.md)
- [Meeting cheat sheet](docs/runbooks/meeting-cheatsheet.md)
- [First recording runbook](docs/runbooks/first-recording.md)
- [Transcription and review runbook](docs/runbooks/transcribe-simple-whispercpp.md)
- [Transcript and evidence contracts](docs/contracts/transcript-and-evidence.md)
- [Historical planning and development snapshots](docs/history/README.md)

## Development Checks

```bash
swift build
.venv/bin/python -m py_compile scripts/*.py
scripts/check-planning-consistency.py
scripts/check-open-source-readiness.sh
scripts/check.sh
```

The active roadmap uses OpsKarta v3. Validate and render it with the adjacent OpsKarta repository:

```bash
PYTHONPATH=../opskarta .venv/bin/python -m specs.v3.tools.cli \
  validate docs/roadmap/murmurmark-cli-roadmap.plan.yaml

PYTHONPATH=../opskarta .venv/bin/python -m specs.v3.tools.cli \
  render executive docs/roadmap/murmurmark-cli-roadmap.plan.yaml --view exec-top
```
