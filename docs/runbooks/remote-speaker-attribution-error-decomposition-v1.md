# Remote Speaker Attribution Error Decomposition v1 Runbook

Run from the repository root with the project virtual environment active.

```bash
source .venv/bin/activate

.venv/bin/python scripts/analyze-remote-speaker-attribution-errors-v1.py freeze
.venv/bin/python scripts/analyze-remote-speaker-attribution-errors-v1.py analyze
.venv/bin/python scripts/analyze-remote-speaker-attribution-errors-v1.py status
.venv/bin/python scripts/analyze-remote-speaker-attribution-errors-v1.py replay
```

The equivalent CLI surface is:

```bash
murmurmark corpus remote-error-decomposition freeze
murmurmark corpus remote-error-decomposition analyze
murmurmark corpus remote-error-decomposition status
murmurmark corpus remote-error-decomposition replay
```

## Reading The Result

Start with `decision`, then inspect `routing_evidence.axis_gains`. Per-corpus and per-stratum tables
show whether the aggregate direction is consistent across speakers, durations, gaps, transitions and
overlap states.

The result does not alter the selected transcript. A successful run only chooses the class of the
next experiment. Do not edit policy thresholds or regenerate hard-v2/hard-v3 after seeing the report.

The frozen result is `ADVANCE_STRONGER_SPEAKER_IDENTITY`. A normal replay must preserve input freeze
`710a0f6e9e2b7f0645d4974cb3557c61b80bd667a61ab6c4da8bd4ac8a3cebb8` and all tracked hashes.

## Recovery

If `analyze` reports an input mismatch, compare the named path with
`private/input_manifest.json`. Restore or reproduce the original upstream artifact; do not refresh the
freeze around an unexplained change. A deliberately new corpus requires a new contract version.
