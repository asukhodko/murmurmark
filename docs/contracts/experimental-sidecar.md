# Experimental Sidecar Contract

Schema versions:

- `murmurmark.experimental_sidecar_manifest/v1`
- `murmurmark.experimental_sidecar_state/v1`
- `murmurmark.experimental_sidecar_report/v1`
- `murmurmark.experimental_sidecar_event/v1`
- `murmurmark.raw_segment_commit/v1`

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

## Manifest

`experiment_manifest.json` is the durable experiment passport.

Required fields:

- `schema`: `murmurmark.experimental_sidecar_manifest/v1`.
- `experiment_id`: stable id, for example `live-shadow-v1`.
- `kind`: experiment kind, currently `near_realtime_shadow`.
- `status`: `not_started`, `recording`, `running`, `completed`, `disabled`, `failed` or `unknown`.
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
- how many sidecar seconds were captured, preprocessed and sent to ASR;
- whether chunks were dropped;
- whether backpressure was detected;
- whether the sidecar disabled itself;
- whether raw capture was affected;
- whether batch processing can be reproduced from raw CAF without sidecar artifacts;
- current batch pipeline status.

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

The sidecar worker waits for paired `mic` and `remote` rows with the same `index`, then materializes:

```text
derived/experiments/live-shadow-v1/audio/mic/000001.wav
derived/experiments/live-shadow-v1/audio/remote/000001.wav
```

For compatibility, it also writes `derived/live/segments.jsonl` rows pointing back to those canonical
experiment audio files.

## Report

`report.json` and `report.md` are readable summaries of the manifest and state. They are not
authoritative over `experiment_manifest.json` and `state.json`.

## Commands

```bash
murmurmark experiment status SESSION|latest
murmurmark experiment report SESSION|latest
murmurmark experiment compare SESSION|latest --experiment live-shadow-v1
```

`compare` runs the existing live-vs-batch comparison first, then refreshes the experiment contract.

## Invariants

- Raw CAF is the only capture source of truth.
- Sidecar receives raw commit rows, not capture sample buffers.
- Sidecar artifacts never overwrite batch outputs.
- Batch transcript is authoritative until separate parity gates promote an experiment.
- Sidecar failure is fail-open: raw capture must finalize and `murmurmark process SESSION` must work
  from raw files alone.
- A second concurrent `murmurmark record` remains forbidden by the recording lock.
