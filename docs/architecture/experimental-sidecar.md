# Experimental Sidecar Architecture

MurmurMark needs a way to run unstable experiments during a meeting without making the stable
recording path fragile. The safe shape is not two independent recordings. The safe shape is one
capture process with one authoritative raw stream and one or more best-effort derived sidecars.

## Goal

Support this user-facing distinction:

```text
default mode:
  stable capture -> stable batch pipeline

experimental mode:
  stable capture -> stable batch pipeline
                 -> experimental sidecar artifacts
```

The experiment may produce a live draft, reusable ASR chunks, metrics or diagnostic clips. It must
not decide whether the meeting was captured, must not mutate raw CAF files and must not replace the
batch transcript until separate parity gates pass.

## Non-Goals

- No two concurrent `murmurmark record` processes for the same call.
- No independent backup recording through the same ScreenCaptureKit source.
- No production live transcript until live-vs-batch parity gates prove it.
- No experiment that requires changing the user's meeting app microphone, speaker or routing.

## Core Invariants

1. **Single capture owner.** Only one process owns ScreenCaptureKit capture for a session.
2. **Raw first.** Raw `audio/mic/*.caf` and `audio/remote/*.caf` are the only capture source of truth.
3. **Experiment after raw commit.** Sidecar work receives raw commit events or closed raw-derived
   segments, never the incoming sample buffers themselves.
4. **No blocking capture callbacks.** The ScreenCaptureKit callback may update lightweight counters;
   it must not run ASR, Echo Guard, derived audio writes, transcript reconciliation or heavy logging.
5. **Fail open.** When the sidecar queue is full, slow or broken, the sidecar disables itself and raw
   recording continues.
6. **Separate artifacts.** Sidecar files live under a dedicated derived namespace and cannot overwrite
   batch outputs.
7. **Batch authoritative.** `transcript.md`, selected notes, export and retention decisions come from
   the batch pipeline until promotion gates explicitly change that policy.
8. **Comparable output.** Every sidecar transcript/draft must be comparable against batch by time
   interval, source track, model, language and preprocessing profile.

## Proposed Runtime Shape

```text
ScreenCaptureKit
  -> capture callback
       -> raw writer -> audio/mic/000001.caf
                  \-> audio/remote/000001.caf
       -> commit tracker -> raw_segment_commits.jsonl
       -> sidecar worker reads committed raw intervals best-effort
```

The raw writer path is mandatory. The sidecar only sees lightweight commit events emitted after raw
frames were accepted by the writer. It never owns capture buffers and never runs heavy work in the
callback.

Recommended v1 sidecar:

```text
derived/experiments/live-shadow-v1/
  experiment_manifest.json
  state.json
  events.jsonl
  raw_segment_commits.jsonl
  audio/
    mic/000001.wav
    remote/000001.wav
  report.json
  report.md
  transcript.draft.md
  transcript.draft.json
  worker.log
  live_batch_comparison.json
```

The existing `derived/live/` path may stay as a compatibility alias while this shape settles, but new
code should prefer an experiment-id namespace when more than one experiment exists.

Implemented v1 contract:

```text
derived/experiments/live-shadow-v1/
  experiment_manifest.json
  state.json
  events.jsonl
  raw_segment_commits.jsonl
  report.json
  report.md
```

`derived/live/` remains the compatibility location for the current live draft, chunks, worker log,
comparison and final reconcile files. The experiment namespace is the contract surface. It records:

- `batch_authoritative: true`;
- `promotion_allowed: false`;
- `raw_capture_affected: false|true|unknown`;
- recovery and comparison commands;
- raw seconds recorded;
- raw commit rows seen;
- sidecar seconds captured/preprocessed/asr;
- whether backpressure disabled the sidecar;
- whether batch can be reproduced from raw CAF files without sidecar artifacts.

Inspect it with:

```bash
murmurmark experiment status SESSION|latest
murmurmark experiment report SESSION|latest
murmurmark experiment compare SESSION|latest --experiment live-shadow-v1
```

## Lifecycle

### Preflight

Before starting an experimental sidecar:

- `doctor --strict` must pass;
- full capture fail-open proof must exist for controlled Live Evidence runs;
- corpus gates must allow controlled evidence collection;
- no other `murmurmark record` process may hold the recording lock;
- experiment configuration is written to `experiment_manifest.json` before audio starts.

### During Capture

The capture callback does the minimum:

1. validate sample buffer timing;
2. write samples to the durable raw CAF writer;
3. update committed frame counters and emit tiny raw commit rows when segment boundaries are crossed;
4. return.

It does not pass `CMSampleBuffer` objects to live segmenters, does not write derived live audio and
does not run ASR/Echo Guard/reconciliation.

If raw commit events or sidecar work exceed limits:

- mark sidecar status `disabled_backpressure`;
- stop materializing new sidecar segments;
- keep raw recording active;
- write one warning, not one warning per callback.

### Stop

On `Ctrl-C`:

1. stop ScreenCaptureKit;
2. close raw CAF files;
3. write `session.json`;
4. close the raw commit log;
5. wait only a bounded time for the sidecar worker;
6. terminate sidecar worker if needed;
7. run normal batch processing if requested;
8. compare sidecar output against batch;
9. keep promotion blocked unless gates pass.

If finalization is interrupted after raw files are closed, recovery must be possible:

```bash
SESSION="sessions/<session-id>"
murmurmark process "$SESSION"
murmurmark experiment compare "$SESSION" --experiment live-shadow-v1
```

## Known Failure Modes

### Callback Coupling

Risk: sidecar file IO, JSON writes or ASR work happens too close to the ScreenCaptureKit callback and
starves raw capture.

Mitigation: raw write first; the callback only records committed-frame progress. A separate worker
reads `raw_segment_commits.jsonl` and materializes WAV from raw CAF.

### CPU Contention

Risk: live ASR or preprocessing uses enough CPU during capture that the OS stops delivering audio
smoothly.

Mitigation: default sidecar worker priority is low; v1 may write sidecar chunks during capture but
defer heavy ASR until after stop for controlled pilots. True realtime ASR needs separate CPU-budget
gates.

### Memory Growth

Risk: long meetings plus slow sidecar worker accumulate unbounded sample buffers.

Mitigation: fixed queue limits, fixed segment windows, sidecar disable on overflow, metrics for
captured/dropped/disabled seconds.

### Boundary Drift

Risk: segment overlap creates duplicate words, missing words or reordered turns around chunk
boundaries.

Mitigation: each chunk has hard publish window and overlap context; comparison tracks boundary
duplicates, suppressed boundary words and missing local words separately.

### Cache Poisoning

Risk: live chunks are reused as batch ASR cache even though chunk geometry, preprocessing, language or
model differ.

Mitigation: live ASR cache bridge is strict and writes `not_eligible` unless metadata is compatible;
batch ASR remains the fallback.

### Split-Brain Status

Risk: user sees a live draft and treats it as final while batch later disagrees.

Mitigation: live draft headers and status must say `shadow`, `batch_authoritative: true` and
`promotion_allowed: false`. `status`, `next`, `finish` and `export` read batch readiness.

### Experiment Artifact Leakage

Risk: debug logs or manifests leak transcript text, private terms or audio-derived content into
tracked files.

Mitigation: experiment artifacts remain under ignored session-derived directories; tracked docs may
contain schemas and commands, not meeting content.

### Recovery Gaps

Risk: recording succeeds but post-stop sidecar/batch processing is interrupted.

Mitigation: recovery command processes existing session without creating a new recording; session
lock and experiment state are inspected separately.

## Compromises

### One Capture, Not Independent Redundancy

This design does not give two independent recordings. It gives one stable recording plus one
experimental derived path. Independent backup must come from outside this ScreenCaptureKit capture
pipeline, for example a server-side meeting recording or another device.

This is acceptable because MurmurMark's highest-value guarantee is "do not corrupt the local raw
capture". Two local ScreenCaptureKit clients do not improve that guarantee.

### Near-Realtime First, True Realtime Later

The first useful sidecar does not need word-by-word realtime. It can write raw commit rows for
closed 60-second intervals, materialize WAV from raw CAF with delay, produce a draft later and
compare after stop. That is enough to prove chunking, handoff, backpressure and parity without
risking capture.

Current safe default: the raw sidecar worker does not read still-open CAF files. A real session
showed that `ffmpeg` can block while reading a growing CAF, which creates misleading "live" behavior
and can delay finalization. The worker now waits for the session to close unless
`MURMURMARK_RAW_SIDECAR_ALLOW_OPEN_RAW_READ=1` or `--allow-open-raw-read` is used for a lab-only
probe. That means `record --experiment live-shadow-v1` is controlled sidecar evidence, not guaranteed
visible live transcription. A true near-realtime draft needs a later chunk writer that produces
closed raw chunks during capture.

True low-latency transcription can come later after:

- capture fail-open proof stays green;
- controlled Live Evidence runs pass;
- CPU and queue budgets are measured on long meetings;
- live-vs-batch parity is good enough.

### Disable Sidecar Instead Of Saving Every Experiment

When backpressure happens, v1 should disable the sidecar instead of trying to preserve every
experimental fragment. Losing sidecar evidence is acceptable. Losing raw meeting audio is not.

### Separate Namespaces Instead Of One Unified Transcript

Experiment outputs stay in `derived/experiments/<experiment-id>/` even when they look useful. A later
promotion step may copy selected compatible artifacts into normal derived paths, but only after gates
prove compatibility.

## Promotion Gates

An experimental sidecar can become a default path only when a real corpus proves:

- no raw capture regression;
- no order mismatch regression;
- no missing `Me` speech above threshold;
- no remote leakage increase in `Me`;
- no review burden increase;
- selected notes remain available and cite valid utterance IDs;
- chunk-boundary duplicate/suppression risks are resolved;
- batch remains reproducible from raw CAF without sidecar artifacts.

Until then, the sidecar may collect evidence, not change user-facing truth.

## CLI Shape

Stable path:

```bash
murmurmark record --target-bundle system
murmurmark process latest
```

Current controlled experimental path:

```bash
murmurmark record --target-bundle system --experiment live-shadow-v1
murmurmark process latest
murmurmark experiment status latest
murmurmark experiment compare latest --experiment live-shadow-v1
```

Existing live session analysis path:

```bash
SESSION="sessions/<session-id>"
murmurmark experiment compare "$SESSION" --experiment live-shadow-v1
```

Legacy unsafe lab path:

```bash
MURMURMARK_ENABLE_UNSAFE_LIVE_PIPELINE=1 murmurmark record --target-bundle system --live-pipeline
```

The generic sidecar command still uses the same single-capture lock and must reject attempts to run a
second `record` process. Until soak/parity gates pass, use it as controlled evidence and keep plain
`record -> process` as the production path.

## Implementation Notes

- Keep the existing recording lock. It prevents the known bad two-process shape.
- Keep the sidecar queue bounded and non-blocking.
- Keep `raw_segment_commits.jsonl` append-only and small: it records committed intervals, not audio.
- Keep experiment manifests with explicit `experiment_id`, `schema`, `config`, `inputs`, `outputs`,
  `status`, `started_at`, `ended_at`, `disabled_reason`, `raw_capture_affected` and
  `promotion_allowed: false`.
- Keep one report that answers: "Did the experiment affect raw capture?" The expected answer for a
  successful fail-open run is machine-checkable `false`.
- Treat `derived/experiments/live-shadow-v1/` as the canonical sidecar namespace. `derived/live/`
  remains a compatibility alias for existing draft and comparison tools.
