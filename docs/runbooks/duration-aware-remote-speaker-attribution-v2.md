# Duration-Aware Remote Speaker Attribution v2 Runbook

The completed run is reproducible without changing production:

```bash
murmurmark corpus remote-duration-v2 status
murmurmark corpus remote-duration-v2 hard-status
murmurmark corpus remote-duration-v2 hard-replay
murmurmark corpus remote-duration-v2 replay
```

The original controlled sequence was:

```bash
murmurmark corpus remote-duration-v2 freeze
murmurmark corpus remote-duration-v2 develop
murmurmark corpus remote-duration-v2 evaluate-hard
murmurmark corpus remote-duration-v2 replay
```

Do not run `evaluate-hard` again: its one decision opening is already consumed. `replay` is allowed
because it uses the frozen candidate and verifies the existing result without selecting new settings.

Inspect aggregate evidence:

```bash
jq '{decision, selected_topology, hard_v2_metrics, coverage_v3_control_metrics, blockers}' \
  sessions/_reports/duration-aware-remote-speaker-attribution-v2/duration_aware_remote_speaker_attribution_report.json
```

Expected decision: `DO_NOT_PROMOTE_TOPOLOGY`. Do not copy private scripts, voice mappings, truth,
stems, embeddings, or predictions into tracked files.
