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

The first authoritative handoff no longer waits for optional Neural Echo evaluation. Deferred
enrichment has an explicit time budget and a machine-readable reason when postponed. `status`
finishes with `complete`, an executable recovery command, or `human_decision_required` with a
bounded item count and duration; it must not send the user back into a `status` loop.

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
by the meeting supervisor. A repeated `process --skip-build` may reuse a compatible authoritative
handoff; the flag changes build work, not ASR compatibility.

## Resource Use

Derived work runs with the `background` resource profile by default. MurmurMark sets `nice=20`,
applies the macOS background scheduling policy and limits native compute pools to four threads.
Batch ASR uses one track worker, and the live sidecar uses one ASR worker. Durable capture is never
demoted, so processing an older session cannot weaken a new recording.

For faster post-recording work that still yields CPU to normal applications, use `opportunistic`.
It keeps `nice=20`, removes the Darwin background clamp and restores bounded parallel mic/remote
ASR. This mode can consume more power and produce more heat while the machine is otherwise idle;
`background` remains the safer choice during capture or when charger headroom matters.

The defaults are configurable in `murmurmark.config.json`:

```json
"processing": {"resource_profile": "background", "max_compute_threads": 4}
```

Example for low-priority, work-conserving post-processing:

```json
"processing": {"resource_profile": "opportunistic", "max_compute_threads": 0}
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

The quarantined remote-ASR producer is enabled only by the lab flag
`--canonical-live-asr-evidence`; ordinary Live Shadow does not run it. Its proofs remain blocked
from automatic batch reuse while the frozen corpus decision is `DO_NOT_PROMOTE`.

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

Speaker-Preserving Neural Echo v2 is the guarded production echo profile. Target-Me separation
experiments that missed locked gates remain isolated. Evidence Handoff v2, guarded export and the
release-quality CLI are complete; detailed decisions and metrics live in the roadmap and research
documents.

**Reliable Final Handoff v1** is complete. On the frozen three-session cache/resume verification,
p90 post-stop ratio is `0.059x`; there are no dead-end blockers, stale handoffs or unexplained
overruns, and exact Speaker-Preserving Neural Echo reuse passes `2/2` applicable sessions. This
proves bounded recovery and handoff convergence, not cold first-pass whisper.cpp speed.

**Authoritative Incremental ASR v1** is complete. Strict v2 chunk identity, integrity checks,
byte-identical replay and interrupted-batch reuse are promoted. Historical checkpoint evidence shows
a median process-time reduction of `98.94%`.

**Canonical Live ASR Producer v1** is also complete with `DO_NOT_PROMOTE`. Exact remote parity passed
on `3/3` frozen sessions, but remote-only precomputation reduced modeled post-stop wall time by only
`2.8651%..4.1040%`: mic and remote ASR already run in parallel, and mic remains the critical path.
The producer is quarantined behind `--canonical-live-asr-evidence`; ordinary meetings do not pay its
cost, and unpromoted recording-time proofs cannot enter batch automatically.

The current goal is **Causal Canonical Mic ASR v1**. It moves the exact mic preparation boundary
through Echo Guard and the selected Speaker-Preserving profile into delayed, checkpointable windows.
Only byte-identical canonical PCM and complete model/prompt/decode proof may be reused; every lag,
unsupported profile or mismatch remains an ordinary batch fallback.

The stable CLI supports durable capture, resumable processing, guarded profiles, evidence-backed
review, export and retention. Exact experiment metrics live in the research documents and roadmap.

The dependent critical path is:

```text
Meeting Lifecycle -> Echo evidence and controlled lab -> Speaker-Preserving Neural Echo v2 (done)
-> Reference-Conditioned v1 (done) -> Identifiability Corpus (done)
-> Target-Me Separation v2 (done) -> Evidence Export v2 (done) -> Release-quality CLI (done)
-> Reliable Final Handoff v1 (done) -> Authoritative Incremental ASR v1 (done)
-> Canonical Live ASR Producer v1 (done: DO_NOT_PROMOTE)
-> Causal Canonical Mic ASR v1 (current) -> Remote Speaker Evidence Map v1 (next)
```

Remote Speaker Evidence Map v1 follows the canonical mic-ASR gate. Speaker naming and
`transcript.rich.json` promotion follow the anonymous evidence map. Heavy local validators, LLM
synthesis and UI remain parallel or parked. Live promotion remains blocked; Live Shadow is advisory
evidence only.

See the [current goal](docs/project/current-goal.md), [readable roadmap](docs/roadmap/murmurmark-cli-roadmap.md)
and [OpsKarta v3 plan](docs/roadmap/murmurmark-cli-roadmap.plan.yaml).

## Scope And Limitations

- Current selected transcripts use `Me` and aggregate `Colleagues`; anonymous remote-speaker
  evidence follows the current authoritative ASR latency goal.
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

murmurmark corpus lifecycle all --require-frozen-inputs --require-passing-gates
```

The active roadmap uses OpsKarta v3. Validate and render it with the adjacent OpsKarta repository:

```bash
PYTHONPATH=../opskarta .venv/bin/python -m specs.v3.tools.cli \
  validate docs/roadmap/murmurmark-cli-roadmap.plan.yaml

PYTHONPATH=../opskarta .venv/bin/python -m specs.v3.tools.cli \
  render executive docs/roadmap/murmurmark-cli-roadmap.plan.yaml --view exec-top
```
