# Bounded Remote Speaker Interval Purification v1 Runbook

## Run

```bash
murmurmark corpus remote-identity-interval-v1 preflight
murmurmark corpus remote-identity-interval-v1 all
murmurmark corpus remote-identity-interval-v1 status
murmurmark corpus remote-identity-interval-v1 replay
```

The ECAPA runtime and model are resolved from the same environment variables as the frozen shadow:

```text
MURMURMARK_REMOTE_SPEAKER_IDENTITY_RUNTIME
MURMURMARK_REMOTE_SPEAKER_ECAPA_MODEL
```

Network access is disabled by the worker. Inference runs at low priority.

## Inspect

```bash
jq '{decision, scope, candidate, comparison, gates, safety}' \
  sessions/_reports/bounded-remote-speaker-interval-purification-v1/\
bounded_remote_speaker_interval_purification_report.json

less sessions/_reports/bounded-remote-speaker-interval-purification-v1/\
bounded_remote_speaker_interval_purification_report.md
```

## Interpret

An advance result only opens a later shadow qualification. It does not select a production profile.
`DO_NOT_ADVANCE_INTERVAL_PURIFICATION` closes this fixed crop without inviting parameter tuning on
the same evidence. `EVIDENCE_BOUND` means the experiment could not establish a valid comparison.

If preflight reports a missing or changed artifact, restore the frozen source instead of rebuilding
it opportunistically. If ECAPA cannot embed an item, that item remains `unknown`.
