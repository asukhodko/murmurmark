# Meeting Lifecycle Contract

Status: experimental v1

Updated: 2026-07-22

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

## Safety Boundary

- Raw `audio/mic/*.caf` and `audio/remote/*.caf` are the source of truth.
- The lifecycle records SHA-256 identities before post-processing and verifies them at the end.
- Capture is finalized before any processing action starts.
- A non-partial `completed_with_warnings` capture may continue; its warnings remain visible and the
  existing process capture gates still reject interrupted, silent or sparse audio.
- The authoritative processing action is plain `murmurmark process SESSION`; `--full`, `--force-asr`
  and `--allow-partial` are never added automatically.
- Suggested review may apply only decisions already accepted by the existing conservative review
  gates. Unknown and conflicting rows remain open.
- Export runs only when `outcome.json.summary.can_export` is true. Retention is planned but raw
  deletion is never applied.
- Live Shadow is optional and advisory. It cannot replace the batch transcript or alter this gate.

## Commands

Start a new lifecycle:

```bash
murmurmark meeting --target-bundle system
murmurmark meeting --target-bundle system --experiment live-shadow-v1
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
  -> enrich
  -> refresh_after_enrich
  -> review_suggested_preview
  -> review_suggested_apply
  -> refresh_after_review
  -> finish
  -> complete
```

Conditional actions are chosen from structured JSON:

- `enrich` is skipped when the full pipeline report or the authoritative deferred checkpoint proves
  that the work is complete;
- suggested preview is used only for a review gate;
- suggested apply is used only when `suggested_closure_auto_rows > 0`;
- `finish` is used only when the outcome explicitly allows export.

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

The final report contains:

- `result`: `ready`, `ready_with_review`, `failed` or `interrupted`;
- selected transcript, notes and verdict paths;
- unresolved review count, seconds and structured blockers;
- export status, blockers and manifest path;
- raw preservation result;
- capture, capture-finalization, authoritative process, enrichment, total-after-stop and per-action
  elapsed time; `capture` is the recorded duration from `session.json`, while `total-after-stop`
  includes writer/sidecar finalization after `Ctrl-C` and all supervisor actions;
- warnings, stop reason, resume availability and exact resume command.

## Locking And Signals

`lifecycle.lock` is held with a non-blocking process lock. A second supervisor for the same session
fails without changing state.

An existing non-terminal lifecycle is continued only through explicit `meeting --resume`; an
ordinary invocation cannot silently adopt interrupted state.

- First `Ctrl-C` while capturing means `stop capture`, not `abort meeting`.
- The same rule applies when `--duration` is set: the duration timer and terminal signal race, and an
  early `Ctrl-C` is persisted as the explicit `sigint` stop reason.
- The recorder closes raw writers and writes `session.json` before the supervisor starts.
- Repeated `Ctrl-C`, `SIGTERM` or `SIGHUP` during bounded capture finalization is deferred so a slow
  writer or Live Shadow shutdown cannot leave raw files half-closed.
- `Ctrl-C` during processing is forwarded to the active child, the current action is marked
  interrupted, state is flushed and the resume command is printed.
- Each allowlisted action runs in a separate process session. Repeated delivery of the same terminal
  signal is coalesced, so nested Swift/Python workers receive one interrupt rather than a cascade. If
  graceful shutdown exceeds the bounded wait, the supervisor escalates without touching raw capture.

## Outcome Rules

- `ready`: authoritative transcript exists and guarded export completed.
- `ready_with_review`: authoritative transcript exists but explicit review or export follow-up
  remains.
- `failed`: capture is invalid, authoritative processing failed, required outputs are missing or raw
  identities changed.
- `interrupted`: processing stopped by signal and can be resumed.

Partial, sparse or silent capture cannot be reported as success. Missing optional evidence does not
damage the transcript and remains visible as review debt.
