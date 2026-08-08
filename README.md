# MurmurMark
Local-first meeting transcription for sensitive work.

MurmurMark records separate microphone and remote tracks, then locally produces an auditable transcript, quality verdict and optional evidence-backed derivatives.

The product is CLI-first. Batch processing is authoritative. Live preview is an optional shadow that cannot replace or weaken the durable recording.
## Mission
MurmurMark turns locally captured 1:1 and group calls into reliable, speaker-resolved transcripts
without sending raw meeting audio to a cloud recorder.

The user should start and stop a meeting recording once and receive an honest result without
supervising internal stages. The transcript must preserve words, order and timing, separate remote
participants by voice inside the session, protect genuine local speech and expose uncertainty.
Notes, summaries and work-system updates are optional derivatives, not the product mission.

## Reliability Contract
For a supported macOS setup, MurmurMark produces one of these outcomes:

- `ready_for_notes`: compatibility name meaning the selected transcript is ready; optional notes are usable when present;
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

From a developer checkout, use `source .venv/bin/activate && scripts/install-local.sh` instead. The
compatibility matrix and model fingerprint live in [`release/compatibility-v1.json`](release/compatibility-v1.json);
see the [installation runbook](docs/runbooks/install-and-upgrade.md).

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
transcript, verdict, unresolved review burden and raw preservation result; optional notes and export
status are included when those derivative stages are present.

The first authoritative handoff no longer waits for optional Neural Echo evaluation. Deferred
enrichment has an explicit time budget; Neural Echo is skipped when its frozen worst-case estimate
cannot fit after the review-evidence reserve. `status` finishes with `complete`, an executable recovery
command, or `human_decision_required` with a bounded item count and duration.

Capture runs in a short-lived child process. It exits and releases ScreenCaptureKit/ReplayKit before
batch processing begins. A new meeting may therefore start while an earlier meeting is still being
processed in another terminal. Run only one active capture at a time; the recording lock rejects a
second one. If ScreenCaptureKit startup does not complete, MurmurMark fails within a bounded timeout,
releases the lock and does not start post-processing. If capture is partial, sparse or silent,
processing blocks; `status` also reports restart-correlated PCM gaps measured without changing raw CAF.

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

An optional second terminal can attach without starting another capture or ASR with
`murmurmark live watch "sessions/<id>"`.

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

Suggested review closes only rows supported by current local evidence. `review suggested apply`
rebuilds the session-local queue for bounded passes, closing newly exposed safe rows in one command.
It stops at a stable manual remainder; unresolved rows remain explicit. `finish` attempts guarded
local export, writes retention recommendations and never deletes raw audio. After a successful
guarded export, `finish` removes rebuildable audio copies under `SESSION/derived/` by default. Raw
CAF, selected transcript, notes, verdict, review decisions
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
```

Frozen corpus sessions are skipped unless `--include-pinned` is explicit. Pin discovery includes
frozen-corpus, split, baseline, hard-test and private `pinned_sessions.json` manifests. Bulk
`all --older-than 7d --exclude-pinned` examples live in the [Retention Policy](docs/contracts/retention-policy.md).

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
      speaker-preserving-neural-echo-v2/
        production_selection_report.json
    transcript-simple/whisper-cpp/
    transcript-rich/speaker-resolved-default-v1/
      selection.json
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

The one-command lifecycle, Speaker-Preserving Neural Echo v2.17, Evidence Handoff v2, guarded export,
bounded resume and incremental ASR are promoted. The normal path is one command plus `Ctrl-C`.

Speaker-Resolved Transcript Default v1 promotes the fingerprint-verified Coverage v3 view into
ordinary `transcript`, Evidence Handoff and guarded export. It preserves every selected word and
uses exact aggregate `Colleagues` fallback when evidence is missing or stale. Refresh or verify it:

```bash
murmurmark audit speaker-default "$SESSION"
murmurmark audit speaker-default "$SESSION" --verify-only
murmurmark transcript "$SESSION"
```

The normal pipeline runs this selector automatically. `status` and `outcome` show the selected
speaker profile and fallback reason. `--rich` remains a compatible diagnostic view; use
`audit remote-residual` only for the v4 measured ceiling.

Anonymous Rich Transcript Handoff v1 passed all `1235` references on 6/6 sessions. Reviewed Remote
Speaker Naming v1 and Reviewed Speaker-Aware Meeting Memory v1 add only explicit session-local
labels and optional evidence-backed notes/export:

```bash
murmurmark speakers template "$SESSION"
# Edit review/remote-speaker-labels.v1.json: resolve every row, then set review_completed to true.
murmurmark speakers apply "$SESSION"
murmurmark transcript "$SESSION" --rich --reviewed-speakers
murmurmark notes "$SESSION" --reviewed-speakers
murmurmark export "$SESSION" --format markdown --include-json --reviewed-speakers
```
Speaker-aware memory and exact-text notes remain optional derivatives. Transcript Perfection Corpus
keeps 13/13 frozen sources explicit and never collapses unlike quality dimensions into one score.
Lexical Accuracy Reference Corpus v1 measures its exact 67-word digital subset at WER/CER `0` and
keeps real-meeting lexical correctness blocked by missing human-reviewed evidence:

```bash
murmurmark corpus lexical status
murmurmark corpus lexical replay \
  --write-manifest docs/testing/lexical-accuracy-reference-corpus-v1-manifest.json
murmurmark corpus perfection all --verify-existing
murmurmark audit remote-independent "$SESSION"
murmurmark corpus remote-independent all --verify-existing
```

The dependent critical path is:

```text
Meeting Lifecycle -> Echo/Target-Me evidence -> Reliable Handoff -> Incremental ASR
-> Remote Speaker Evidence (done: audit-only, 50.4% coverage)
-> Remote Speaker Diarization v2 (done: PROMOTE, 91.9% coverage)
-> Remote Speaker Coverage v3 (done: PROMOTE, 93.9% coverage)
-> Remote Speaker Residual Evidence v4 (done: DO_NOT_PROMOTE, measured ceiling)
-> Speaker-Resolved Transcript Default v1 (done: PROMOTE, ordinary read/handoff/export)
-> Lexical Accuracy Reference Corpus v1 (done: REFERENCE_INSUFFICIENT, bounded exact subset)
-> Independent Remote Speaker Evidence v1 (done: DO_NOT_PROMOTE, 53 words / 23.357s)
-> Remote Speaker Residual Reference Corpus v1 (current)
```

Independent WavLM preserved every safety gate but recovered only `6.2280%` of residual words and
`3.9043%` of residual seconds; none of its new decisions has direct reference coverage. **Remote
Speaker Residual Reference Corpus v1** is now current: build a blind private truth set for the frozen
851-word residual and all 53 WavLM proposals before changing diarization topology or thresholds.
See the [roadmap](docs/roadmap/murmurmark-cli-roadmap.md) and [OpsKarta plan](docs/roadmap/murmurmark-cli-roadmap.plan.yaml).
## Scope And Limitations

- Ordinary auto-selected transcripts use `Me`, fingerprint-verified session-local remote speaker
  IDs and aggregate `Colleagues` for unsupported words. Incompatible evidence returns the exact
  aggregate transcript; evidence from an older cleanup/review profile is never returned as current.
- Promoted v3 anonymous remote evidence covers `93.9312%` of frozen-corpus speech and leaves the
  remaining `6.0688%` explicit `unknown`; a rare participant without enough enrollment is not forced
  into a known voice.
- Independent WavLM evidence remains audit-only after `DO_NOT_PROMOTE`; machine agreement cannot
  replace direct candidate-targeted reference labels.
- `--reviewed-speakers` uses only explicit labels from the current session decision file. Human
  names are never inferred from voice or consumed implicitly by ordinary transcript/export.
- The personalized pre-ASR profile removes independently supported remote leakage on compatible
  sessions; `status` distinguishes the active ASR input from the optional advanced selector.
- Alignment/Echo-Path v3 is audit-only after `READY_FOR_MULTI_COMPONENT_SEPARATOR`; it is not a
  production audio profile and its hard/sealed sets remain unopened.
- SepFormer Four-Stem v1 stopped at train presence/absence separation; dev, hard, direct ASR and production stayed closed.
- Echo Guard records `speaker_playback`, `headphones_or_low_leak` or `uncertain` in
  `local_fir_report.json`; no user acoustic-mode flag is required.
- `local_speech_completion_v2` is selected only for sessions named by its passing frozen-corpus
  decision; stale hashes, missing local models or failed gates fall back without changing text.
- `mixed_utterance_separation_v1` is audit-only after `DO_NOT_PROMOTE`; it never replaces the
  selected transcript.
- `echo_suppression_promotion_v1` remains historical audit evidence after `DO_NOT_PROMOTE`.
- `speaker_preserving_neural_echo_v2` is the guarded personalized production selector. It runs only
  with compatible local enrollment, model, promotion evidence and pinned ASR runtime; otherwise it
  visibly returns to exact `local_fir_role_masked`. The current production contract is v2.17.
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
- Notes, summaries, retrieval and work-system proposals are optional derivatives outside the critical roadmap.
## Documentation

- [Documentation index](docs/00-index.md), [mission](docs/product/vision.md), [requirements](docs/product/prd-v1.md)
- [Current goal](docs/project/current-goal.md), [route](docs/project/reliable-transcription-route.md), [roadmap](docs/roadmap/murmurmark-cli-roadmap.md), [OpsKarta](docs/roadmap/murmurmark-cli-roadmap.plan.yaml)
- [Meeting lifecycle contract](docs/contracts/meeting-lifecycle.md)
- [Meeting cheat sheet](docs/runbooks/meeting-cheatsheet.md)
- [Transcription and review runbook](docs/runbooks/transcribe-simple-whispercpp.md)
- [Transcript Perfection Corpus contract](docs/contracts/transcript-perfection-corpus.md)
- [Lexical Accuracy Reference Corpus contract](docs/contracts/lexical-accuracy-reference-corpus.md)
- [Remote Speaker Coverage v3](docs/contracts/remote-speaker-coverage-v3.md) and [Residual Evidence v4](docs/contracts/remote-speaker-residual-evidence-v4.md) contracts
- [Independent Remote Speaker Evidence v1](docs/contracts/independent-remote-speaker-evidence-v1.md)
- [Speaker-Resolved Transcript Default v1](docs/contracts/speaker-resolved-transcript-default-v1.md)

## Development Checks

```bash
swift build
.venv/bin/python -m py_compile scripts/*.py
scripts/check-planning-consistency.py
scripts/check-open-source-readiness.sh
scripts/check.sh
murmurmark corpus remote-coverage all --verify-existing && murmurmark corpus remote-residual all --verify-existing
murmurmark corpus remote-independent all --verify-existing
murmurmark corpus speaker-default all --verify-existing
murmurmark corpus perfection all --verify-existing
```
The active roadmap uses OpsKarta v3. Validate it with the adjacent OpsKarta repository:
```bash
PYTHONPATH=../opskarta .venv/bin/python -m specs.v3.tools.cli \
  validate docs/roadmap/murmurmark-cli-roadmap.plan.yaml
```
