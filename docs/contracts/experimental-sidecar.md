# Experimental Sidecar Contract

Schema versions:

- `murmurmark.experimental_sidecar_manifest/v1`
- `murmurmark.experimental_sidecar_state/v1`
- `murmurmark.experimental_sidecar_report/v1`
- `murmurmark.experimental_sidecar_event/v1`
- `murmurmark.raw_segment_commit/v1`
- `murmurmark.live_progressive_target_me/v1`
- `murmurmark.live_remote_audio_guard/v1`
- `murmurmark.live_preview_snapshot/v1`
- `murmurmark.live_causal_remote_active_me_separation/v1`
- `murmurmark.live_causal_remote_active_me_separation_v1_report/v1`
- `murmurmark.live_causal_remote_active_me_separation_v1_outcome/v1`

The contract lives under:

```text
derived/experiments/<experiment-id>/
  experiment_manifest.json
  state.json
  events.jsonl
  raw_segment_commits.jsonl
  audio/
  report.json
  report.md
```

For the current live shadow experiment, `<experiment-id>` is `live-shadow-v1`.

`derived/live/` is a compatibility alias for existing draft/chunk tools: draft transcript, segment
list, live-vs-batch comparison and final reconcile. User-facing transcripts, notes, export and
retention continue to use batch artifacts.

The live worker may additionally write progressive local-speaker evidence:

```text
derived/live/causal-target-me/
  state.json
  enrollment.jsonl
  evaluations.jsonl
  candidates.jsonl
  clips/
  micro_asr/
```

These files use `murmurmark.live_progressive_target_me/v1`. Every candidate must state
`timeline_causal: true`, `used_batch_fields_for_selection: false`, `batch_authoritative: true` and
`publication_allowed: false`. Seed counts describe the past-only enrollment available before the
candidate interval. A candidate may also carry `remote_audio_guard` with schema
`murmurmark.live_remote_audio_guard/v1`. The guard records `mic_db`, `remote_db`,
`mic_minus_remote_db`, correlation, thresholds and `status`. The current conservative profile passes
when `remote_db <= -65` or `mic_minus_remote_db >= 20`; unavailable measurements are not accepted.
A candidate is evidence for parity comparison, not an authoritative transcript turn.

The base live chunk is persisted before progressive Target-Me runs. When the current base chunk is
already outside the configured lag budget, its mic record contains:

```json
{
  "causal_target_me_shadow": {
    "schema": "murmurmark.live_progressive_target_me/v1",
    "status": "skipped_lag_budget",
    "reason": "live_lag_budget_exceeded",
    "observed_live_lag_sec": 90.0,
    "max_live_lag_sec": 60.0,
    "batch_authoritative": true,
    "promotion_allowed": false
  }
}
```

`live_pipeline_report.json.causal_target_me_shadow.skipped_lag_budget_count` counts these chunks.
Target-Me micro-ASR also has a bounded child timeout; timeout or failure cannot retract an already
written base chunk or draft.

## Causal Remote-Active Me Separation

The explicit-only remote-active replay writes:

```text
derived/live/causal-remote-active-me-separation-v1/
  selection.jsonl
  residual_candidates.jsonl
  candidates.jsonl
  state.json
  report.md
```

`state.json` and every JSONL row use `murmurmark.live_causal_remote_active_me_separation/v1`.
Accepted candidates must carry all of these invariants:

- `selection_mode: recording_time_causal_remote_active_separation_v1`;
- `timeline_causal: true` and `used_batch_fields_for_selection: false`;
- past-only training evidence over earlier committed PCM;
- passing residual audio, remote-active, Target-Me and remote text guards;
- `batch_authoritative: true`, `publication_allowed: false` and `promotion_allowed: false`.

The focused corpus report writes
`sessions/_reports/live-pipeline/causal_remote_active_me_separation_v1.{json,jsonl,md}`. Its report
schema is `murmurmark.live_causal_remote_active_me_separation_v1_report/v1`; each disposition row
uses `murmurmark.live_causal_remote_active_me_separation_v1_outcome/v1`. Batch text and timing may
appear only in the evaluation reference. The profile is never selected by normal compare, preview,
transcript, notes or export commands.

## Causal Double-Talk Me Recovery

The explicit-only offline shadow writes:

```text
derived/live/causal-double-talk-me-recovery-v1/
  source_decisions.jsonl
  residual_views.jsonl
  candidates.jsonl
  state.json
  report.md
```

Rows use `murmurmark.causal_double_talk_me_recovery/v1`. An accepted candidate must have:

- `classification: genuine_double_talk` and non-empty local text;
- `selection_mode: recording_time_causal_double_talk_me_recovery_v1`;
- `timeline_causal: true` and `used_batch_fields_for_selection: false`;
- past-only causal echo training;
- independent residual/voice/ASR evidence;
- `remote_text_forbiddance: true` and `remote_audio_forbiddance: true`;
- `publication_allowed: false`, `promotion_allowed: false` and `batch_authoritative: true`.

The immutable corpus and reports are stored under
`sessions/_reports/live-pipeline/causal-double-talk-me-recovery-v1/`:

```text
corpus_manifest_v1.json
corpus_rows_v1.jsonl
outcomes_v1.jsonl
recovery_report_v1.json
recovery_report_v1.md
```

`corpus_manifest_v1.json` freezes every evaluation row and SHA-256 of raw capture, Echo Guard
inputs/outputs, committed chunks, past enrollment, normal preview and authoritative outputs. A
non-refreshing rebuild must fail closed if any input differs. Evaluation references may be used only
after selection.

The runtime stage lives under the existing causal recovery runtime as `double-talk-v1/`. It handles
at most one prioritized group per closed chunk, uses the strict hybrid ratio-mask residual, has a
bounded child timeout and writes its own stage counters. Its explicit comparison profile ends in
`_causal_double_talk_me_recovery_v1_runtime_v1`. It is excluded from default policies and normal
preview. Replay may gate only this new stage while keeping older Metal-dependent diagnostic parity
informational.

## Manifest

`experiment_manifest.json` is the durable experiment passport.

Required fields:

- `schema`: `murmurmark.experimental_sidecar_manifest/v1`.
- `experiment_id`: stable id, for example `live-shadow-v1`.
- `kind`: experiment kind, currently `near_realtime_shadow`.
- `status`: `not_started`, `recording`, `preview_running`, `running`, `completed`,
  `completed_partial_draft`, `disabled_backpressure`, `disabled_pcm_copy`, `disabled`, `failed` or
  `unknown`.
- `started_at`, `ended_at`: timestamps when known.
- `config`: experiment configuration and compatibility alias.
- `inputs`: raw/session inputs. Raw CAF paths are inputs, not outputs.
- `outputs`: links to raw commit log, sidecar chunks, draft, worker log, comparison and reports.
- `disabled_reason`: `sidecar_backpressure`, `sidecar_writer_disabled`, `sidecar_failed` or `null`.
- `raw_capture_affected`: `false`, `true` or `unknown`.
- `batch_authoritative`: always `true` for v1.
- `promotion_allowed`: always `false` for v1.
- `recovery_command`: command that resumes processing from the existing session without recording.
- `comparison_command`: command that refreshes sidecar-vs-batch comparison.

## State

`state.json` answers machine questions used by smoke tests, corpus reports and future status views:

- did the experiment start;
- how many raw seconds were recorded;
- how many raw commit rows were seen;
- live preview mode, currently `committed_pcm_queue_v1`;
- how many sidecar seconds were captured, preprocessed and sent to ASR;
- whether chunks were dropped;
- whether backpressure was detected;
- whether the sidecar disabled itself;
- whether raw capture was affected;
- whether batch processing can be reproduced from raw CAF without sidecar artifacts;
- current batch pipeline status.

Committed-PCM transport counters survive contract refreshes:

- `pending_pcm_packets`;
- `pending_pcm_seconds_by_source`;
- `max_pending_pcm_seconds`;
- `max_observed_pending_pcm_seconds`;
- `max_pending_pcm_packets`, an emergency guard rather than the primary backlog measure;
- `dropped_pcm_packets` and `artificial_write_delay_ms` for fail-open evidence.

The default `max_pending_pcm_seconds` is `30` per source. It may be reduced through
`MURMURMARK_LIVE_PCM_MAX_PENDING_SECONDS` for fail-open tests; production code clamps it to
`0.1...300s`.

For a successful fail-open backpressure scenario:

```json
{
  "answers": {
    "backpressure_detected": true,
    "sidecar_disabled": true,
    "raw_capture_affected": false,
    "batch_reproducible_from_raw": true
  }
}
```

## Events

`events.jsonl` is append-only. Each row includes:

- `schema`: `murmurmark.experimental_sidecar_event/v1`;
- `t`;
- `type`;
- `status`;
- `raw_capture_affected`;
- `batch_authoritative`;
- `promotion_allowed`.

The current writer appends `experiment_contract.refreshed` whenever the contract is rebuilt.

## Raw Segment Commits

`raw_segment_commits.jsonl` is append-only evidence that raw audio was accepted by the durable writer
before the sidecar tried to do anything with it. Each row describes a committed interval in the
single raw CAF file. It does not contain audio samples.

Required fields:

- `schema`: `murmurmark.raw_segment_commit/v1`;
- `experiment_id`: currently `live-shadow-v1`;
- `source`: `mic` or `remote`;
- `index`: 1-based segment index;
- `start_sec`, `end_sec`, `duration_sec`;
- `raw_path`: raw CAF path, for example `audio/mic/000001.caf`;
- `frames_committed`: frames in this segment;
- `total_frames_committed`: total frames accepted by the raw writer up to `end_sec`;
- `sample_rate`;
- `status`: currently `committed`;
- `final`: `true` only for the trailing segment emitted when recording stops;
- `t`: commit timestamp.

Example:

```json
{
  "schema": "murmurmark.raw_segment_commit/v1",
  "experiment_id": "live-shadow-v1",
  "source": "mic",
  "index": 1,
  "start_sec": 0.0,
  "end_sec": 60.0,
  "duration_sec": 60.0,
  "raw_path": "audio/mic/000001.caf",
  "frames_committed": 2880000,
  "total_frames_committed": 2880000,
  "sample_rate": 48000,
  "status": "committed",
  "final": false
}
```

The normal live-preview path writes paired `mic` and `remote` segment files from committed PCM:

```text
derived/experiments/live-shadow-v1/audio/mic/000001.caf
derived/experiments/live-shadow-v1/audio/remote/000001.caf
```

For compatibility, it also writes `derived/live/segments.jsonl` rows pointing back to those canonical
experiment audio files.

The recording-time worker writes two Markdown views:

```text
derived/live/transcript.preview.md  # conservative default for live watch
derived/live/transcript.draft.md    # complete candidate-only diagnostics
derived/live/preview_snapshots.jsonl
derived/live/replay-lab/live_replay_matrix.json
derived/live/replay-lab/live_replay_matrix.md
```

`transcript.preview.md` may include a causal Target-Me candidate only when its
`murmurmark.live_remote_audio_guard/v1.status` is `passed`. Missing or rejected guard evidence must
remain absent from the preview and available in the diagnostic draft. Neither file is authoritative.

Each snapshot row contains `created_at`, `provenance`, `preview_policy`, `chunk_count`,
`processed_end_sec`, candidate counters, `content_bytes` and `content_sha256`. Recording-time proof
requires `chunk_count > 0`, `provenance == recording_time_committed_pcm` and
`created_at < session.ended_at`. Snapshot rows are append-only; comparison and evidence commands
must not create them.

## Recording-Time Causal Me Recovery

The explicit runtime shadow is stored under:

```text
derived/live/causal-me-recovery-runtime-v1/
  worker_state.json
  worker_events.jsonl
  state.json
  runtime_runs.jsonl
  draft.json
  transcript.shadow.md
  local-island-v2/{selection.jsonl,candidates.jsonl,state.json}
  remote-active-v1/{selection.jsonl,candidates.jsonl,state.json}
```

`worker_state.json` has schema `murmurmark.live_causal_me_recovery_worker/v1`. Required fields:

- `status`: `idle`, `running`, `running_with_pending`, `completed_one`,
  `base_draft_fallback_lag`, `base_draft_fallback_timeout`, `base_draft_fallback_error`,
  `disabled_fail_open`, `completed` or `completed_with_fallback`;
- `active` and `pending`: at most one row each;
- invocation counters, last completed chunk, last/max/final live lag and last error;
- timeout and lag budgets;
- `normal_preview_connected: false`;
- `base_draft_fallback: true`;
- `batch_authoritative: true` and `promotion_allowed: false`.

`state.json` has schema `murmurmark.live_causal_me_recovery_runtime/v1`. Every successful
invocation records its closed-chunk cutoff, submission/start/completion timestamps, stage latency,
candidate counts, `completed_before_stop`, `closed_current_and_past_chunks_only: true`,
`past_only_enrollment: true` and `used_batch_fields_for_selection: false`.

Candidate rows retain the underlying local-island or remote-active schema and add:

- `runtime_evidence` with invocation and stage timing;
- `runtime_publication_status`: `effective_candidate`, `superseded_by_later_base_turn` or
  `rejected_by_algorithm`;
- optional `runtime_supersession_evidence` explaining the later base `Me` overlap.

The runtime profile is explicit-only:

```text
online_live_me_remote_overlap_filter_live_boundary_split_retime_causal_remote_energy_
local_island_micro_asr_v2_causal_remote_active_me_separation_v1_runtime_v1
```

It is excluded from default comparison policies and cannot feed `transcript.preview.md`, batch
transcript, notes, export or promotion. A timeout, overload, lag skip or nonzero child exit must
leave the normal base draft byte-identical.

`raw_segment_commits.jsonl` remains a fallback and audit trail. If committed-PCM preview is missing
or partial, explicit `experiment recover-draft` may run the raw sidecar worker after recording stops.
Fallback outputs are isolated under:

```text
derived/experiments/live-shadow-v1/fallback/
  audio/{mic,remote}/NNNNNN.wav
  segments.jsonl
  chunks.jsonl
  transcript.draft.md
  live_pipeline_state.json
  live_pipeline_report.json
  worker.log
```

Fallback must not read still-open raw CAF files, overwrite `derived/live/`, or be counted as
recording-time evidence. Every fallback row carries `provenance: post_stop_raw_commit_recovery`.

## Report

`report.json` and `report.md` are readable summaries of the manifest and state. They are not
authoritative over `experiment_manifest.json` and `state.json`.

## Commands

```bash
murmurmark experiment status SESSION|latest
murmurmark experiment report SESSION|latest
murmurmark experiment compare SESSION|latest --experiment live-shadow-v1
murmurmark experiment recover-draft SESSION|latest --experiment live-shadow-v1
murmurmark live evidence SESSION|latest [--refresh] [--strict]
murmurmark live replay SESSION|latest [--refresh] [--with-labs] [--lab-policy POLICY]
```

`compare` runs the existing live-vs-batch comparison first, then refreshes the experiment contract.
It must not materialize audio or start ASR. `recover-draft` is the only command allowed to run
post-stop fallback ASR.

`live evidence` writes `murmurmark.live_session_evidence/v1` to
`derived/live/live_session_evidence.json` plus a Markdown view. The report separates
`transport_evidence_passed` from `all_parity_gates_passed` and checks capture safety, required
artifacts, authoritative batch, pre-stop chunks, terminal worker state, bounded lag/latency,
committed-PCM provenance, fallback isolation and meaningful comparison. It always keeps
`promotion_allowed: false`; `--strict` returns exit code `2` until every parity gate passes.
The narrower recording-time recovery objective has a separate aggregate command,
`murmurmark live recovery-evidence`. It requires manager `1.1.0+`, a true pre-stop recovery run,
healthy transport/batch, the incremental cache contract and zero final recovery lag, but does not
pretend that the still-red transcript-promotion gates have passed. Its report schema is
`murmurmark.live_recovery_real_evidence_report/v1`.
The default comparison includes `online_live_me_remote_overlap_filter_v1` as the direct baseline and
`live_runtime_causal_target_me_direct_v1` plus
`live_runtime_causal_target_me_remote_energy_v1` as runtime candidates, so the corpus report can
compute paired no-regression verdicts without offline Target-Me anchors. Other expensive or
batch-informed shadow policies remain opt-in.

`live replay` writes `murmurmark.live_replay_lab/v1`. It is an offline decision report over the
existing `live_batch_comparison.json`: every policy row contains missing `Me` seconds, remote-in-Me
seconds, blocking order errors, token F1 and deltas from the baseline. A policy is a safe candidate
only when it reduces missing local speech without increasing either remote leakage or blocking
order errors. The report also compares observed live chunk geometry with batch cache parameters.
It always records `batch_authoritative: true`, `raw_audio_modified: false` and
`production_defaults_changed: false`.

## Invariants

- Raw CAF is the only capture source of truth.
- Sidecar receives committed PCM packets after raw write; raw commit rows are fallback evidence.
- Progressive Target-Me evaluates a chunk before enrolling any seed from that chunk.
- Progressive enrollment, evaluation and candidate rows carry `created_at`, allowing parity reports
  to distinguish recording-time work from post-stop replay.
- Progressive micro-ASR is limited to unpublished groups from chunk-level suppressed mic chunks.
  The direct publish profile accepts only no-remote-overlap or remote-free-gap intervals; past-speaker-
  confirmed sliding windows remain diagnostic. Passed chunks are never duplicated.
- Sidecar artifacts never overwrite batch outputs.
- Fallback artifacts never overwrite realtime sidecar outputs.
- `compare` is read-only for audio, segment, chunk and draft artifacts.
- Worker state has a heartbeat, current stage/index and terminal status even after timeout.
- Batch transcript is authoritative until separate parity gates promote an experiment.
- Sidecar failure is fail-open: raw capture must finalize and `murmurmark process SESSION` must work
  from raw files alone.
- A second concurrent `murmurmark record` remains forbidden by the recording lock.
