# MurmurMark
Local-first meeting transcription for sensitive work.

MurmurMark records separate microphone and remote tracks, then locally produces an auditable transcript, quality verdict and optional evidence-backed derivatives. The product is CLI-first. Batch processing is authoritative. Live preview is an optional shadow that cannot replace or weaken the durable recording.
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

Raw `audio/mic/*.caf` and `audio/remote/*.caf` files are immutable processing inputs; the writer records every post-start in-stream timestamp discontinuity where ScreenCaptureKit supplied no PCM.
Inserted silence keeps the timeline aligned but remains explicit `captured_audio=false` evidence: any measured gap blocks completeness in `status`, `outcome` and `transcript --cat`; derived profiles remain isolated behind no-regression gates.

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

The first authoritative handoff no longer waits for optional Neural Echo evaluation. It freezes a
content-addressed transcript snapshot before deferred work begins. A bounded speaker-attribution
attempt (up to five minutes) runs before optional audio judges. Enrichment, subsequent report, suggested
review and guarded export share one optional time budget, including a reconciliation reserve.
After that window, a cache-only final refresh (up to 30 seconds) synchronizes outcome without new inference.
Review keep/drop of Me reuses compatible frozen remote evidence, including word-level turns, as a
disclaimer-bearing provisional view. Ordinary `status`, `outcome` and `transcript --path-only` only
verify published evidence; they never start a speaker model. Budget expiry checkpoints completed
review work and is reported as deferred, not as an unexpected Python failure.
Provisional views mark per-turn review and approximate timing; missing times never become zero.
Compatible v3 evidence survives review; preview limits never truncate queues or answer templates.
See the [read-view contract](docs/contracts/transcript-publication-read-view.md) and [repair checkpoint](docs/project/2026-10-01-acoustic-publication-repair.md).
Optional interruption preserves the transcript; resume retries deferred work. Reconciliation keeps
compatible decisions, profile, speaker evidence and review queue consistent. Unsupported remote
speech is `remote_speaker_unknown`; `--aggregate` returns the exact role-only fallback.
A known group roster can reconcile one split anonymous voice without assigning names; see the [speaker contract](docs/contracts/speaker-resolved-transcript-default-v1.md).

Capture runs in a short-lived child process and releases ScreenCaptureKit/ReplayKit before batch
processing. A new meeting may start while an earlier one is processed in another terminal. Only one
capture may run at a time. Its lock is released after raw writers close and `session.json` is written;
optional Live Shadow finalization cannot reserve it. ScreenCaptureKit startup has a bounded timeout
and releases the lock on failure. Partial, sparse or silent capture blocks processing; `status` also
reports restart-correlated PCM gaps without changing raw CAF.

Heavy post-processing is serialized globally under `sessions/.murmurmark-processing/lease.lock`.
When another session already owns the lease, `process`, `enrich` or the post-capture part of
`meeting` waits in a FIFO queue and reports the owner and queue position. This lease is separate from
the recording lock: a new durable capture can start while older processing waits or runs. Interrupting
a waiter starts no heavy child and leaves an exact resume command; abandoned same-host queue tickets
are removed automatically.

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

Low-level `--reuse-asr-cache` reuses only an exact v2 raw cache; invalid or legacy inputs rebuild
automatically, while deterministic timeline/micro-ASR work still runs.

## Resource Use

Derived work runs with the `background` resource profile by default. MurmurMark sets `nice=20`,
applies the macOS background scheduling policy and limits native compute pools to three threads.
Batch ASR uses one track worker, and the live sidecar uses one ASR worker. Durable capture is never
demoted, so processing an older session cannot weaken a new recording. Across sessions, the global
processing lease admits only one heavy pipeline at a time; per-process thread limits therefore do not
multiply merely because two completed meetings are resumed together.

For faster post-recording work that still yields CPU to normal applications, use `opportunistic`.
It keeps `nice=20` and removes the Darwin background clamp, but retains the three-thread ceiling and
serial heavy ASR workers. It can use those three threads more consistently while the machine is idle;
`background` remains the safer choice during capture, low battery or limited charger power.

The defaults are configurable in `murmurmark.config.json`:

```json
"processing": {"resource_profile": "background", "max_compute_threads": 3}
```

Example for low-priority, work-conserving post-processing:

```json
"processing": {"resource_profile": "opportunistic", "max_compute_threads": 3}
```

Values `0` and values above `3` are normalized to `3` for `background` and `opportunistic`, including
legacy configs. Use `murmurmark process "$SESSION" --resource-profile performance` only for an
intentional foreground speed run. `performance` is the sole unlimited profile, restores parallel ASR
defaults and may occupy the machine.

`status` reports `processing_lease`, `asr_stage` and `primary_asr_chunks` separately. A value such as
`primary_asr_chunks: 88/88` means that the main chunk recognizer has finished; timeline repair,
bounded micro-ASR or final transcript assembly can still be running. The external pipeline observer
then reports `asr_stage: post_primary_timeline_and_micro_asr` without modifying the frozen,
corpus-qualified transcriber runtime.
The primary counter reserves both authoritative tracks as soon as the first track report appears, so
serial background ASR cannot first show `N/N` and later jump backwards to `N/2N`.
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
local export and writes retention recommendations. After a successful guarded export, `finish`
compacts the session to the selected transcript, notes, verdict, review decisions and
JSON/Markdown provenance. Raw CAF and rebuildable media are deleted. Use
`--keep-debug-artifacts` when the recording may be needed for retranscription, corpus work or
audio-algorithm debugging. Low-level export and retention commands are documented in the
[Retention Policy](docs/contracts/retention-policy.md).

Standalone `review suggested` reuses compatible evidence and computes at most four missing lane items;
`MURMURMARK_TARGETED_JUDGE_COMPUTE=0` makes it cache-only, as the lifecycle already does after its budget.
Source requirements are per item; suggested apply revalidates text, role, interval, audio and policy.
Historical rows never count as current coverage. See [review evidence hardening](docs/testing/2026-09-23-review-audio-evidence-hardening.md).
`meeting` prints the first transcript path before enrichment finishes, without approving guarded export.
`transcript --cat` adds the attribution disclaimer; `--path-only` preserves the existing artifact header.
Review-safe publication retains compatible remote labels after Me-only review without expanding frozen eligibility.
Text retention is not voice/lexical confirmation. Reports share a fingerprint-bound queue with sum, union and unknown durations; see [repair status](docs/testing/2026-09-30-review-safe-handoff.md).
### Compact Old Sessions
Low disk space triggers guarded cleanup before capture; see the [Retention Policy](docs/contracts/retention-policy.md). To keep raw CAF but remove rebuildable media manually:
```bash
murmurmark retention compact plan "$SESSION"
murmurmark retention compact apply "$SESSION" --confirm-delete-derived-media
murmurmark retention compact verify "$SESSION"
```
Archive completed unpinned sessions as text and structured evidence only:

```bash
murmurmark retention compact plan all --mode transcript_only --older-than 7d --exclude-pinned
murmurmark retention compact apply all --mode transcript_only --older-than 7d --exclude-pinned --confirm-delete-derived-media --confirm-delete-raw
murmurmark retention compact verify all --older-than 7d --exclude-pinned
```

`transcript_only` is irreversible: later ASR or audio analysis is impossible without an external
backup. It deletes only raw mic/remote paths declared by `session.json`, after verifying the
selected transcript. Frozen corpus and explicitly pinned sessions stay untouched. Details and pin
sources are in the [Retention Policy](docs/contracts/retention-policy.md).

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
    transcript-simple/whisper-cpp/  # includes the promoted text-integrity profile and audit
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
bounded resume and incremental ASR are promoted. Speaker-Resolved Transcript Default v1 publishes
fingerprint-verified Coverage v3 evidence without changing selected words. Compatible evidence below
the strict gate is shown as disclaimer-bearing `provisional`; unsupported remote speech remains
`remote_speaker_unknown`, and `--aggregate` preserves the exact role-only fallback.

Refresh or verify speaker evidence:
```bash
murmurmark audit speaker-default "$SESSION"
murmurmark audit speaker-default "$SESSION" --verify-only
murmurmark transcript "$SESSION"
```
The normal pipeline applies this selector to the selected transcript. `status` and `outcome` expose
the profile, disclaimer and fallback reason. Reviewed names remain explicit session-local input;
speaker-aware memory and notes are optional derivatives outside the critical product route.

The terminal instrument keeps continuity, chronology, speaker-count truth, unknown duration and
human-reviewed lexical accuracy as separate gates. **Review-Safe Attributed Handoff v1** is complete;
the current qualification task is **Bounded Evidence Compute v1**.
The [October 6 repair](docs/project/2026-10-06-transcript-evidence-repair.md) adds interval checks,
cached finalization and shared listening. ASR cache and new word/speaker selection remain unpromoted.
```bash
murmurmark corpus lexical-seed-v1 progress
murmurmark corpus terminal-gate-v1 status
murmurmark corpus terminal-gate-v1 replay
```

Production keeps `prompt_file: null`: broad static context can bias recognition. Session-Scoped
Lexical Context may be promoted only after direct lexical truth and multi-session no-regression
gates. The current critical path is:
```text
bounded chronology/continuity evidence
  -> review-safe attributed handoff
  -> qualified bounded evidence compute
  -> human-reviewed lexical truth
  -> session-scoped lexical context
  -> speaker-resolved terminal gate
```
Measured history and exact remaining bounds live in the
[roadmap](docs/roadmap/murmurmark-cli-roadmap.md) and
[OpsKarta plan](docs/roadmap/murmurmark-cli-roadmap.plan.yaml).
## Scope And Limitations
- Ordinary auto-selected transcripts use `Me` and the best current session-local remote speaker
  evidence. Verified labels are preferred; compatible labels below the strict session gate are
  published as `provisional`, and unsupported speech becomes `remote_speaker_unknown`. The Markdown
  header states coverage and failure reasons. `--aggregate` remains the exact role-only fallback.
- `remote_speaker_NN` is a session-local acoustic cluster, not a confirmed person; `status` exposes
  purity concerns and `murmurmark transcript SESSION --aggregate --cat` returns the role-only fallback.
- Promoted v3 anonymous remote evidence covers `93.9312%` of frozen-corpus speech and leaves the
  remaining `6.0688%` explicit `unknown`; a rare participant without enough enrollment is not forced
  into a known voice.
- Independent WavLM evidence and the private residual reference pack remain audit-only; machine
  agreement cannot replace direct truth. Synthetic labels cannot enter real sessions.
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
- Batch transcript is authoritative; promoted Transcript Integrity v1 repairs only fingerprint-bound duplicates/repetition and fails open to its base profile. Live is excluded from export/retention, and the normal workflow requires no cloud ASR or raw-audio upload.
- Notes, summaries, retrieval and work-system proposals are optional derivatives outside the critical roadmap.
- Domain packs are local inputs. `glossary.yaml` is not yet compiled into production ASR context;
  broad prompts remain disabled until Session-Scoped Lexical Context passes lexical corpus gates.
## Documentation
- [Documentation index](docs/00-index.md), [mission](docs/product/vision.md), [requirements](docs/product/prd-v1.md), [current goal](docs/project/current-goal.md), [route](docs/project/reliable-transcription-route.md), [roadmap](docs/roadmap/murmurmark-cli-roadmap.md), [OpsKarta](docs/roadmap/murmurmark-cli-roadmap.plan.yaml)
- [Meeting lifecycle contract](docs/contracts/meeting-lifecycle.md), [meeting cheat sheet](docs/runbooks/meeting-cheatsheet.md), [transcription runbook](docs/runbooks/transcribe-simple-whispercpp.md), [Transcript Integrity contract](docs/contracts/transcript-integrity-v1.md) and [runbook](docs/runbooks/transcript-integrity-v1.md)
- [Transcript Perfection Corpus](docs/contracts/transcript-perfection-corpus.md), [Terminal Gate instrument](docs/contracts/speaker-resolved-terminal-gate-instrument-v1.md), [Chronology Evidence Arbitration](docs/contracts/speaker-bounded-chronology-arbitration-v1.md), [Word-Level Chronology Localization](docs/contracts/word-level-chronology-localization-v1.md), [Lexical Accuracy Reference Corpus](docs/contracts/lexical-accuracy-reference-corpus.md), [Remote Speaker Coverage v3](docs/contracts/remote-speaker-coverage-v3.md) and [Residual Evidence v4](docs/contracts/remote-speaker-residual-evidence-v4.md)
- [Domain pack and lexical-context boundary](docs/contracts/domain-pack.md)
- [Independent evidence](docs/contracts/independent-remote-speaker-evidence-v1.md), [Remote Unknown Recovery](docs/contracts/remote-unknown-evidence-recovery-v1.md), [Controlled Truth Lab](docs/contracts/controlled-remote-speaker-truth-lab-v1.md), [Cluster Purity Reference](docs/contracts/remote-speaker-cluster-purity-reference-v1.md), and [Boundary result](docs/testing/2026-08-20-remote-speaker-boundary-minority-v1.md)
- [Speaker-Resolved Default](docs/contracts/speaker-resolved-transcript-default-v1.md) and [roster-constrained evidence](docs/contracts/roster-constrained-remote-speaker-evidence-v1.md)
## Development Checks
```bash
swift build
.venv/bin/python -m py_compile scripts/*.py
scripts/check-open-source-readiness.sh
scripts/check.sh
```
