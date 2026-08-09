# Temporal End-to-End Remote Diarization Qualification v1 Runbook

This is a frozen research workflow, not an ordinary meeting command.

## Local Setup

Download the pinned model and build the pinned Rust worker once:

```bash
.venv/bin/python scripts/setup-temporal-end-to-end-remote-diarization-v1.py all
```

Verify an existing offline installation:

```bash
.venv/bin/python scripts/setup-temporal-end-to-end-remote-diarization-v1.py verify
```

The default model directory is
`~/.local/share/murmurmark/models/temporal-remote-diarization-v1/dia-community-1/`.
Set `MURMURMARK_TEMPORAL_DIARIZATION_MODEL` only to point at a byte-identical pinned model.

## Frozen Flow

The completed experiment uses separate commands so the truth boundary remains visible:

```bash
murmurmark corpus remote-temporal-diarization-v1 preflight
murmurmark corpus remote-temporal-diarization-v1 prepare
murmurmark corpus remote-temporal-diarization-v1 freeze
murmurmark corpus remote-temporal-diarization-v1 evaluate
murmurmark corpus remote-temporal-diarization-v1 replay
murmurmark corpus remote-temporal-diarization-v1 finalize
murmurmark corpus remote-temporal-diarization-v1 status
```

`prepare` processes twelve full remote tracks: canonical and fixed shifted variants for six
sessions. Workers run at `nice=20`; normalized audio and private candidate artifacts can require
several gigabytes temporarily.

`all` is available only for a fresh truth-unseen experiment:

```bash
murmurmark corpus remote-temporal-diarization-v1 all
```

Do not rerun `prepare`, alter inferred speaker counts or tune thresholds after `freeze` to seek a
better result. Declare a new policy version first.

## Expected Result

```text
decision: KEEP_EXPLICIT_UNKNOWN
minimum_temporal_stability_ari: 0.814301
minimum_activity_jaccard: 0.972946
exact_speaker_count_sessions: 0/6
preserved_confirmed_gains: 2/3
new_false_identities: 7
next: close_available_local_remote_diarization_route_until_new_evidence
```

The backend is temporally reproducible, but it fragments speaker identities and misses too much
remote utterance activity. Do not wire it into the selected transcript or tune it against the same
truth set.

## Verification

```bash
.venv/bin/python scripts/check-temporal-end-to-end-remote-diarization-v1.py
```

The check verifies frozen provenance, label-free candidate artifacts, deterministic replay and
production invariants. Generated private audio and model weights are not tracked by Git.
