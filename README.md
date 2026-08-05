# MurmurMark

Local-first meeting transcription for sensitive work.

MurmurMark records separate microphone and remote tracks, then locally produces a transcript, quality verdict, evidence-backed notes, review plan, export bundle and retention plan.

The product is CLI-first. Batch processing is authoritative. Live preview is an optional shadow that cannot replace or weaken the durable recording.

## Mission

MurmurMark turns sensitive working conversations into reliable local transcripts and evidence-backed meeting memory without sending raw meeting audio to a cloud recorder.

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

Supported release environment:

- macOS 15 or newer on Apple Silicon with Screen and System Audio Recording and microphone
  permissions;
- Python 3.12 or 3.13 with the required modules;
- `ffmpeg`, `ffprobe`, `whisper-cli` and the tested local whisper.cpp model.

Install a release archive transactionally:

```bash
shasum -a 256 -c murmurmark-<version>-<commit>.tar.gz.sha256
tar -xzf murmurmark-<version>-<commit>.tar.gz
cd murmurmark-<version>-<commit>
python3 scripts/release-bundle.py verify .
./install.sh --python /absolute/path/to/python3
export PATH="$HOME/.local/bin:$PATH"

mkdir -p "$HOME/murmurmark-workspace"
cd "$HOME/murmurmark-workspace"
export MURMURMARK_PYTHON=/absolute/path/to/python3
murmurmark config init
murmurmark doctor --strict
murmurmark self-test
```

The runtime is immutable under `$HOME/.local/share/murmurmark/releases/`; config, sessions and
exports stay in the external workspace. Reinstall and upgrade verify and self-test a staged release
before atomically switching the active version. A failed upgrade leaves the previous release
working.

From a developer checkout, use `source .venv/bin/activate && scripts/install-local.sh` instead.
The exact compatibility matrix, model fingerprint and optional dependencies live in
[`release/compatibility-v1.json`](release/compatibility-v1.json); see the
[installation runbook](docs/runbooks/install-and-upgrade.md).

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

## Resource Use

Derived work runs with the `background` resource profile by default. MurmurMark sets `nice=20`,
applies the macOS background scheduling policy and limits native compute pools to four threads.
Batch ASR uses one track worker, and the live sidecar uses one ASR worker. Durable capture is never
demoted, so processing an older session cannot weaken a new recording.

The defaults are configurable in `murmurmark.config.json`:

```json
"processing": {"resource_profile": "background", "max_compute_threads": 4}
```

Use `murmurmark process "$SESSION" --resource-profile performance` only for an intentional
foreground speed run. That restores the previous parallel ASR defaults and may occupy the machine.
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
deletes raw audio. After a successful guarded export, `finish` removes rebuildable audio copies
under `SESSION/derived/` by default. Raw CAF, selected transcript, notes, verdict, review decisions
and JSON/Markdown provenance remain available. Use `--keep-debug-artifacts` when the session is
needed for pipeline or audio-algorithm debugging.

```bash
murmurmark finish "$SESSION" --format markdown
murmurmark finish "$SESSION" --format obsidian
murmurmark finish "$SESSION" --keep-debug-artifacts
murmurmark retention plan "$SESSION"
```

Raw deletion requires a compatible policy, successful export and an explicit confirmation command.

### Compact Old Sessions

Remove rebuildable media while keeping raw CAF, final text and structured evidence:

```bash
murmurmark retention compact plan "$SESSION"
murmurmark retention compact apply "$SESSION" --confirm-delete-derived-media
murmurmark retention compact verify "$SESSION"
murmurmark retention compact plan all --older-than 7d --exclude-pinned
murmurmark retention compact apply all --older-than 7d --exclude-pinned \
  --confirm-delete-derived-media
```

Frozen corpus sessions are skipped unless `--include-pinned` is explicit. Pin discovery includes
frozen-corpus, split, baseline, hard-test and private `pinned_sessions.json` manifests. See
[Retention Policy](docs/contracts/retention-policy.md).

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
    handoff-v2/
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

Earlier classical, pretrained DEC and passive-corpus experiments ended safely in `DO_NOT_PROMOTE`
or `DO_NOT_TRAIN`; their counterexamples established the local-word gates. Controlled Echo
Supervision Lab v1 then supplied reproducible train/dev/hard evidence with replay `1465/1465`.

**Speaker-Preserving Neural Echo v2** is complete with guarded
`PROMOTE_SPEAKER_PRESERVING_NEURAL_ECHO_V2`. Its personalized hybrid selected candidate audio in
`5/12` sealed corpus sessions, removed `41.940s` and `90` remote-supported tokens, and retained all
candidate local tokens. The selected clean mic is transcribed directly; post-ASR cleanup received
zero promotion credit. The remaining `7/12` sessions used exact fallback, including headphones and
every unsafe or inapplicable case.

Reference-Conditioned Target-Me Separation v1 exposed an unidentifiable training set. The following
Target-Me Identifiability Corpus v1 added `4/2/2` split-disjoint non-target speakers and `980`
paired enrollment controls with exact replay `2470/2470`. Separation v2 then proved query adherence
(`4.991 dB` correct-vs-wrong, `0%` collapse), but missed the locked Target-Me, non-target and
absent-query dev gates. It therefore completed with reproducible `DO_NOT_PROMOTE`; hard and sealed
data remained unopened and Speaker-Preserving Neural Echo v2 remains production.

**Evidence Notes And Export v2** is complete: its deterministic handoff binds transcript, verdict,
review burden and evidence notes; 110 corpus sessions have zero integrity, stale-manifest or replay
failures. Guarded export accepts only `ready` or verified `no_speech`.

**Release-quality CLI** is complete: the versioned archive has a complete SHA-256 inventory,
compatibility and license contracts, deterministic assembly, transactional install/upgrade and
isolated offline acceptance through Evidence Handoff v2 and guarded export.

The current goal is **Remote Speaker Evidence Map v1**: split authoritative `remote` speech into
stable anonymous speaker intervals in an audit-only rich transcript, with local reproducible
evidence and no automatic names or changes to the selected transcript.

The stable CLI supports durable capture, resumable processing, guarded profiles, evidence-backed
review, export and retention. Exact experiment metrics live in the research documents and roadmap.

The dependent critical path is:

```text
Meeting Lifecycle -> Echo evidence and controlled lab -> Speaker-Preserving Neural Echo v2 (done)
-> Reference-Conditioned v1 (done) -> Identifiability Corpus (done)
-> Target-Me Separation v2 (done) -> Evidence Export v2 (done) -> Release-quality CLI (done)
-> Remote Speaker Evidence Map v1 (current)
```

Speaker naming and `transcript.rich.json` promotion follow the anonymous evidence map. Heavy local
validators, LLM synthesis and UI remain parallel or parked. Live promotion remains blocked; Live
Shadow is advisory evidence only.

See the [current goal](docs/project/current-goal.md), [readable roadmap](docs/roadmap/murmurmark-cli-roadmap.md)
and [OpsKarta v3 plan](docs/roadmap/murmurmark-cli-roadmap.plan.yaml).

## Controlled Echo Supervision Lab

The completed private lab remains the frozen enrollment and evaluation source for the personalized
Echo profile. Ordinary users without it keep the exact safe fallback. Capture, replay and privacy
instructions live in the [Controlled Echo Supervision Lab runbook](docs/runbooks/controlled-echo-supervision-lab.md).

## Scope And Limitations

- Current selected transcripts use `Me` and aggregate `Colleagues`; anonymous remote-speaker
  evidence is the active shadow-only development goal.
- The personalized pre-ASR profile removes independently supported remote leakage on compatible
  speaker-playback sessions; it does not promise waveform-perfect echo removal on every room/device.
- Echo Guard records `speaker_playback`, `headphones_or_low_leak` or `uncertain` in
  `local_fir_report.json`; no user acoustic-mode flag is required.
- `local_speech_completion_v2` is selected only for sessions named by its passing frozen-corpus
  decision; stale hashes, missing local models or failed gates fall back without changing text.
- `mixed_utterance_separation_v1` is audit-only after `DO_NOT_PROMOTE`; it never replaces the
  selected transcript.
- `echo_suppression_promotion_v1` remains historical audit evidence after `DO_NOT_PROMOTE`.
- `speaker_preserving_neural_echo_v2` is the guarded personalized production selector. It runs only
  with matching local enrollment/model/promotion evidence and speaker playback; otherwise it
  returns to exact `local_fir_role_masked`.
- `reference_conditioned_target_me_separation_v1` is frozen research after `DO_NOT_PROMOTE`; its
  Target-Me, remote-echo and other-local stems never replace production.
- `reference_conditioned_target_me_separation_v2` is frozen research after `DO_NOT_PROMOTE`; it
  proved speaker-query adherence but failed locked dev waveform-quality gates, so hard and sealed
  meetings remained unopened.
- `neural_residual_echo_v1` is audit-only after `DO_NOT_PROMOTE`; it has no apply command and its
  ONNX models are never required by the normal meeting path.
- `speaker_preserving_echo_adaptation_corpus_v1` is a private local corpus audit after
  `DO_NOT_TRAIN`; it performed no training and cannot select an audio or transcript profile.
- Batch transcript is authoritative; live output is not used for export or retention decisions.
- No cloud ASR or cloud raw-audio upload is required by the normal workflow.
- A future UI must reuse CLI contracts and is not required for a useful product.

## Documentation

- [Documentation index](docs/00-index.md)
- [Mission and vision](docs/product/vision.md)
- [Product requirements](docs/product/prd-v1.md)
- [Current goal](docs/project/current-goal.md)
- [Speaker-Preserving Neural Echo v2 result](docs/research/2026-08-04-speaker-preserving-neural-echo-v2.md)
- [Reference-Conditioned Target-Me Separation v1](docs/research/2026-08-04-reference-conditioned-target-me-separation-v1.md)
- [Target-Me Identifiability Corpus v1](docs/research/2026-08-04-target-me-identifiability-corpus-v1.md)
- [Reference-Conditioned Target-Me Separation v2](docs/research/2026-08-05-reference-conditioned-target-me-separation-v2.md)
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
