# Meeting Lifecycle Contract

Status: stable v1

Updated: 2026-09-25

## Purpose

`murmurmark meeting` is the high-level command for an ordinary meeting:

```bash
murmurmark meeting --target-bundle system
```

The command owns capture, authoritative batch processing and the safe part of post-processing. The
user starts it once and stops capture with `Ctrl-C`. A second `Ctrl-C` during processing stops work
at a checkpoint and prints the exact resume command.

`murmurmark record` remains the low-level capture-only command. Existing `process`, `enrich`,
`review`, `next` and `finish` commands remain available for diagnostics and recovery.

The high-level command runs capture through a short-lived internal `record` child. The child is the
only process that initializes ScreenCaptureKit/ReplayKit. It finalizes raw CAF and exits before the
parent starts the lifecycle supervisor. This process boundary prevents a stopped `SCStream` or its
ReplayKit XPC connection from surviving throughout a long ASR run and interfering with the next
meeting.

## Safety Boundary

- Raw `audio/mic/*.caf` and `audio/remote/*.caf` are the source of truth.
- `capture.starting` means startup was requested; `capture.started` is emitted only after
  `SCStream.startCapture` confirms success.
- Shareable-content lookup and stream start/stop use bounded callback bridges. A missing
  ScreenCaptureKit completion fails capture, releases the global recording lock and never starts the
  lifecycle supervisor.
- The lifecycle records SHA-256 identities before post-processing and verifies them at the end.
- Capture is finalized before any processing action starts.
- Capture keeps normal scheduling. The optional live sidecar uses the bounded `background` policy.
  Post-capture work may use `opportunistic` (`nice=20`, no Darwin background clamp) to consume idle
  CPU without promoting itself over normal applications. Both normal profiles cap native compute at
  three threads and serialize heavy ASR workers; only explicit `performance` is unlimited. A
  scheduling-policy failure must not invalidate raw capture.
- A non-partial `completed_with_warnings` capture may continue; its warnings remain visible and the
  existing process capture gates still reject interrupted, silent or sparse audio.
- The authoritative processing action is plain `murmurmark process SESSION`; `--full`, `--force-asr`
  and `--allow-partial` are never added automatically.
- Suggested review may apply only decisions already accepted by the existing conservative review
  gates. Unknown and conflicting rows remain open.
- Export runs only when `outcome.json.summary.can_export` is true. Raw deletion is never applied.
  A successful guarded export compacts rebuildable media under `derived/` unless the lifecycle was
  started with `--keep-debug-artifacts`.
- Live Shadow is optional and advisory. It cannot replace the batch transcript or alter this gate.

## Commands

Start a new lifecycle:

```bash
murmurmark meeting --target-bundle system
murmurmark meeting --target-bundle system --experiment live-shadow-v1
murmurmark meeting --target-bundle system --keep-debug-artifacts
```

An omitted `--out` creates a unique directory and prints:

```text
SESSION="sessions/<id>"
```

Resume interrupted post-processing:

```bash
murmurmark meeting --resume sessions/<id>
```

Resume never starts another capture.

Strictly verify a controlled one-command soak:

```bash
murmurmark acceptance --live-session "$SESSION" \
  --require-meeting-lifecycle \
  --report /tmp/murmurmark-meeting-lifecycle.json
```

The strict gate rejects a legacy manual `record -> process` session. It independently checks the
lifecycle schema and terminal result, required action provenance, absence of unsafe process flags,
the selected transcript path and current raw mic/remote SHA-256 values against the frozen
`before/after` evidence.

## State Machine

The supervisor uses a fixed allowlist. It does not execute `recommended_next` or parse human CLI
output.

```text
capture_validate
  -> inspect
  -> process
  -> authoritative handoff
  -> attribute_speakers (bounded, before optional work)
  -> enrich
  -> refresh_after_enrich
  -> review_suggested_preview
  -> review_suggested_apply
  -> refresh_after_review
  -> finish
  -> refresh_final_state (cache-only)
  -> complete
```

Conditional actions are chosen from structured JSON:

- `process` stops at the first authoritative handoff. Neural Echo synthesis and
  Speaker-Preserving Neural Echo candidate evaluation belong to deferred enrichment;
- `attribute_speakers` attempts current-profile attribution with its own 300-second cap before
  optional judges can consume the remaining budget. Failure preserves the transcript, is recorded
  as a warning and can be retried with explicit resume; it never justifies invented identities;
- `enrich` is skipped when the full pipeline report or the authoritative deferred checkpoint proves
  that the work is complete, or when the post-stop budget is exhausted;
- a later `reviewed_v1` read surface does not invalidate the frozen authoritative transcript for
  deferred resume: `enrich` verifies the handoff transcript path, size and SHA-256, then the final
  refresh returns to that authoritative profile before review decisions are reapplied;
- post-enrichment/review refreshes first select the current transcript profile, then bind compatible
  cached speaker evidence before writing `outcome`. Acoustic inference belongs to the initial
  bounded attribution action or an explicit full refresh, not each review convergence pass;
- successful review materialization rebuilds readiness and review-plan reports before cache-only
  speaker publication. Keep-only profile changes can preserve manifest-bound v1 clusters or v3 word
  turns as provisional evidence; missing or incompatible evidence stays explicitly unavailable;
- suggested preview is used only for a review gate;
- suggested apply is used only when `suggested_closure_auto_rows > 0`;
- `finish` is used only when the outcome explicitly allows export.
- `finish` applies derived-media compaction only after that guarded export succeeds. Compaction is
  fail-open and cannot change raw CAF or the exported bundle.
- `refresh_final_state` has a separate 30-second cap. It verifies existing speaker selection and
  can publish compatible cached provisional evidence, without speaker inference or review application.
  Unfinished enrichment stays visible as optional pending work; only authoritative pipeline failure
  produces the hard `last_pipeline_run` gate. A real `pipeline_failed` cannot yield lifecycle `ready`.

Bounded actions receive `MURMURMARK_ACTION_DEADLINE_EPOCH` for cooperative child checkpoints before
the supervisor's hard monotonic watchdog. Exit `75` means budget exhaustion, `130` means interruption.
Review batch reports are atomically checkpointed before/after commands and retain successfully applied
session rows if synthesis or report refresh is stopped. Suggested review does not start another
convergence pass with less than ten seconds remaining. Final cached reconciliation remains independent
of the exhausted optional window. A failed command with time remaining is still a genuine failure.

The supervisor snapshots machine-readable artifacts before `process` and both outcome refreshes.
`process` must either update its report or append explicit `checkpoint_reuse` provenance; a stale
report alone is not success. Each refresh must update `outcome.json` or `session_readiness.json`,
and their selected profiles must agree. Suggested review is skipped after a failed enrichment
refresh, and guarded export is skipped after a failed final refresh.

Before `finish`, the supervisor records the SHA-256 identity of any existing
`export_manifest.json`. The action passes only when this invocation creates or changes the
manifest, the manifest belongs to the current session, its selected profile matches the structured
outcome, it has no blockers, and its status is `exported` or `exported_with_warnings`. An old
successful manifest therefore cannot hide a newly blocked export.

Each action runs at most once per invocation. The total transition count is bounded. A failed hard
action ends the run; optional evidence and guarded export failures preserve the authoritative
transcript and become warnings or `ready_with_review`.

The default post-stop budget is one recorded-session duration. After required processing and the
initial attribution attempt, its remainder
is capped to `1800s` for all optional actions together: enrichment, both refreshes, suggested preview/apply
and guarded export. One monotonic deadline covers the whole optional window, not each action separately.
It is recorded in lifecycle state and may be changed by the diagnostic supervisor options
`--post-stop-budget-ratio` and `--max-enrichment-budget-sec`. Budget expiry sends the child a
graceful interrupt before bounded termination escalation (SIGTERM after 10s, SIGKILL after 15s).
Timeout handling records descendant ownership before interrupting: Foundation helpers and Python
workers can use separate process groups. It waits for their exit and validates PID birth times before
signaling surviving descendants, including after the command wrapper has exited.
Required capture/ASR and final metadata/raw-identity verification are not killed by this optional budget.
Budget handling itself never changes or deletes raw capture; ordinary guarded retention rules still apply.
Nested reconciliation records `interrupted_recoverable` and exits without a Python traceback;
the deferred pipeline points to `murmurmark enrich SESSION`, not a fresh authoritative `process`.

Speaker-Preserving Neural Echo direct ASR is sparse: every unchanged window must be reported as
`bit_exact_baseline_reuse`, and the set of decoded windows must exactly equal the candidate's changed
window set. Any mismatch fails the corpus reuse gate. A compatible authoritative handoff is also
reused when `process --skip-build` is used; skipping the Swift build is not an ASR-cache mismatch.

## Artifacts

All lifecycle state lives under:

```text
derived/meeting-lifecycle/
  state.json
  next_action.json
  events.jsonl
  report.json
  report.md
  lifecycle.lock
```

### `state.json`

Schema: `murmurmark.meeting_lifecycle_state/v1`.

Required fields:

- session and lifecycle status;
- current and next action;
- bounded transition count;
- action status, attempt count, timing and error;
- raw input SHA-256 manifest;
- exact resume command.

State writes are atomic. A stale `running` action is returned to `pending` only during explicit
resume.

### `next_action.json`

Schema: `murmurmark.meeting_next_action/v1`.

The file contains one allowlisted action id, a structured reason and whether the action is required,
conditional or terminal. Command strings are provenance only and are built by the supervisor.

### `events.jsonl`

Schema per row: `murmurmark.meeting_lifecycle_event/v1`.

Events include lifecycle start/resume, action start/result, interruption, raw verification and final
result. Rows contain timestamps and durations, but no meeting text or audio content.

### `report.json`

Schema: `murmurmark.meeting_lifecycle_report/v1`.

After validating the authoritative process output, the supervisor prints
`[meeting] transcript_available: <absolute path>` before running enrichment and suggested review.
This path is an available intermediate authoritative result, not the final lifecycle outcome or
guarded-export permission. The terminal report still publishes the final selected read surface.

The final report contains:

- `result`: `ready`, `ready_with_review`, `failed` or `interrupted`;
- selected transcript, notes and verdict paths;
- selected speaker profile, resolution state and exact fallback reason from Speaker-Resolved
  Transcript Default v1;
- unresolved review count, seconds and structured blockers;
- export status, blockers and manifest path;
- derived compaction status, removed byte count and debug-artifact preservation mode;
- raw preservation result;
- capture, capture-finalization, authoritative process, enrichment, total-after-stop and per-action
  elapsed time; `capture` is the recorded duration from `session.json`, while `total-after-stop`
  includes writer/sidecar finalization after `Ctrl-C` and all supervisor actions;
- warnings, stop reason, resume availability and exact resume command.
- `budgets`: configured ratio/cap, consumed and remaining time, and a structured terminal status;
- `deferred_work`: non-blocking command, status and reason for optional work left outside the first
  handoff;
- `manual_decisions`: at most 100 bounded items with interval, role, reason and allowed decisions,
  without transcript text. When the current reviewed profile and review progress prove complete
  coverage, the list is empty even if conservative `quality.needs_review` flags remain;
  `residual_quality_flag_count` keeps that uncertainty visible;
- `next`: `complete`, an allowlisted command, `human_decision_required`, or a hard failure reason.

Reliable Final Handoff v1 adds a stricter convergence invariant to this existing schema. A blocking
result exposes an allowlisted executable next action, a bounded manual decision item, or an explicit
non-actionable evidence limit.
Stage budgets and deferred-work reasons must be machine-readable; exceeding a budget cannot be
reported as silent success or leave a stale `running` action. The measured pre-change baseline is in
`docs/testing/2026-08-05-reliable-final-handoff-baseline.md`.

When the supervisor stops optional enrichment at its budget, `pipeline_run_state.json` records
`deferred_budget_exhausted`. A compatible newer lifecycle result (`ready` or `ready_with_review`)
therefore remains the user-facing status; an internal interrupted child cannot hide the readable
authoritative transcript.
The supervisor reserves the smaller of `300s` and `20%` of the remaining enrichment budget for
final report/review reconciliation. Only the remainder is assigned to the `enrich` child. Exhausting
that child budget is a deferred-work outcome, not a failed meeting, and the reserved time remains
available for refreshing lifecycle artifacts.
Every following child gets only the remaining shared time. A timed-out report/review/export becomes
`deferred_budget_exhausted`; it does not overwrite the successful enrichment checkpoint. Unstarted
optional actions are also deferred when the deadline has expired. Reports list `budgets.deferred_actions`
and `deferred_work.pending_actions`, set `resume_available`, and print `meeting --resume SESSION`.
If a deferred action succeeds on resume, its checkpoint clears any `error` retained from an earlier
failed or interrupted attempt. A successful status and stale error must never coexist.

`refresh_final_state` has a separate 30-second bound and passes `MURMURMARK_FINALIZE_ONLY=1`.
Reconciliation then skips decision rebasing, verifies strict evidence without inference and
materializes the best compatible provisional view with `--cached-only`. Review metadata and
listening contexts are refreshed without producing lane WAVs. Existing audio packs, answer sheets
and immutable publication generations remain available. Missing/incompatible evidence remains
explicit; cached-only never promotes a weak cluster or invents a voice. The final consistency check
must pass before reconciliation reports completion. Timeout preserves checkpoints and remains a
deferred/failed-soft result, not a claim that optional quality checks passed.

An explicit `meeting --resume SESSION` also retries `deferred_budget_exhausted`, `failed_soft` and
skipped enrichment checkpoints with the budget supplied by the new invocation. Downstream refresh,
review and finish actions return to `pending`; completed capture, inspect and authoritative process
checkpoints are not repeated.
Explicit resume grants a new finite optional window under the supplied ratio/cap, excluding time already
spent by previous invocations. A deferred follow-up rebuilds readiness and preview first, including after
a partially completed apply. Enrichment already proved complete is not repeated. This is not an automatic
retry loop and does not turn a readable transcript into an approved export.

## Session-State Reconciliation

Deferred enrichment and review materialization end with `reconcile-session-state.py`. Its report is:

```text
derived/pipeline-run/state-reconciliation/state_reconciliation_report.json
schema: murmurmark.session_state_reconciliation/v1
```

The transaction refreshes session quality, operational readiness, the review plan and progress,
speaker selection, provisional speaker output and outcome in dependency order. When rebasing is
enabled, a closed decision is reused only by exact evidence SHA-256 or by an unambiguous bounded
interval/text identity with a still-allowed decision. One old decision cannot close multiple new
rows. Earlier applied decisions are archived in `review_decisions_history.jsonl` under
`murmurmark.review_decision_history/v1`.

The terminal consistency gate requires the selected profile to agree across readiness, outcome and
speaker selection, and requires review rows/seconds to agree with the canonical progress queue.
New progress reports additionally carry `murmurmark.review_queue_snapshot/v1`: reconciliation
requires an identical current snapshot in readiness and outcome. It distinguishes unresolved tasks
from answered rows, interval sum from timeline union, and unknown durations from zero. Current CLI
readers validate its source files and report `stale` instead of printing outdated queue totals.
Fresh cumulative decisions also own lane closure: earlier reviewed chronology rows cannot reappear
as burden merely because a later residual profile materializes a smaller template.
Failure writes `failed_recoverable`, the previous transcript fingerprint and an executable resume
command. It does not delete or overwrite raw capture. Repeating a completed reconciliation must not
change the selected transcript, current decision file or decision history.

`murmurmark status SESSION` accepts a lifecycle result only when the report schema is current, raw is
preserved, the selected profile matches readiness, the selected transcript exists and the report is
not older than readiness/outcome. A compatible report replaces an opaque status loop with
`complete`, `human_decision_required` plus exact item count and seconds, or
`blocked_unactionable` when the review queue is exhausted but a documented residual still blocks
export. The latter never pretends that repeating review would create a missing decision.

Corpus evidence is produced with:

```bash
murmurmark corpus lifecycle all --freeze-inputs
murmurmark corpus lifecycle all --require-frozen-inputs --require-passing-gates
```

The report freezes SHA-256 identities for lifecycle, readiness, outcome, review progress,
authoritative handoff provenance and candidate chunk-reuse evidence. It rejects dead-end blockers,
stale selected profiles, unexplained overruns and duplicate ASR of unchanged candidate windows.

## Locking And Signals

`lifecycle.lock` is held with a non-blocking process lock. A second supervisor for the same session
fails without changing state.

An existing non-terminal lifecycle is continued only through explicit `meeting --resume`; an
ordinary invocation cannot silently adopt interrupted state.

The separate global recording lock covers only the short-lived capture child. It is released before
batch processing. Thus one active capture and any number of older lifecycle supervisors may coexist,
while two concurrent captures remain forbidden.

Heavy post-processing has a separate FIFO lease at
`sessions/.murmurmark-processing/lease.lock`; queue tickets live under
`sessions/.murmurmark-processing/queue/`. Only one non-plan `process` or `enrich` pipeline may hold
that lease for a sessions root. A waiting pipeline persists owner, position and `capture_blocked=false`
in pipeline state. It must not acquire or delay the recording lock. The OS releases the file lock on
process death; the next contender removes malformed and dead same-host tickets before admission.

- First `Ctrl-C` while capturing means `stop capture`, not `abort meeting`.
- The same rule applies when `--duration` is set: the duration timer and terminal signal race, and an
  early `Ctrl-C` is persisted as the explicit `sigint` stop reason.
- The recorder closes raw writers and writes `session.json` before the supervisor starts.
- Normal ScreenCaptureKit capture accumulates RMS evidence while each raw buffer is durably written.
  Finalization reuses that evidence for the silent-track gate instead of decoding both complete CAF
  files under the global recording lock. Nonstandard capture backends retain the file-probe fallback.
- The global recording lock is released immediately after raw writers are closed and `session.json`
  is atomically written. Optional Live Shadow worker waits and reconciliation happen afterwards, so
  they cannot prevent the next independent capture from starting.
- Repeated `Ctrl-C`, `SIGTERM` or `SIGHUP` during bounded capture finalization is deferred so a slow
  writer or Live Shadow shutdown cannot leave raw files half-closed.
- `Ctrl-C` during processing is forwarded to the active child, the current action is marked
  interrupted, state is flushed and the resume command is printed.
- `Ctrl-C` while waiting for the processing lease removes only that queue ticket, starts no heavy
  child and prints the phase-appropriate `process` or `enrich` resume command.
- An inherited action deadline also bounds lease waiting. Internal cooperative exit `75` is budget
  deferral, `130` is interruption and real failures remain failures; a third-party executable's `75`
  does not automatically opt into this protocol. The strict frozen speaker selector is supervised
  externally rather than changed without requalification. Raw child cancellation diagnostics go to
  the stage journal, while the CLI prints a concise resumable state.
- Bounded helpers snapshot child ancestry and process birth times before cancellation. Escalation
  includes owned descendants with separate process groups, without signaling unrelated processes.
- Each allowlisted action runs in a separate process session. Repeated delivery of the same terminal
  signal is coalesced, so nested Swift/Python workers receive one interrupt rather than a cascade. If
  graceful shutdown exceeds the bounded wait, the supervisor escalates without touching raw capture.

## Outcome Rules

- `ready`: authoritative transcript exists and guarded export completed.
- `ready_with_review`: authoritative transcript exists but explicit review or export follow-up
  remains. It includes an allowlisted remediation command, bounded `manual_decisions`, or an
  explicit `blocked_unactionable` residual with no fictitious manual queue.
- `failed`: capture is invalid, authoritative processing failed, required outputs are missing or raw
  identities changed.
- `interrupted`: processing stopped by signal and can be resumed.

Partial, sparse or silent capture cannot be reported as success. Missing optional evidence does not
damage the transcript and remains visible as review debt.
