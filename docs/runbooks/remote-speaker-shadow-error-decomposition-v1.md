# Remote Speaker Shadow Error Decomposition v1 Runbook

This is a corpus-maintenance command. It is not part of an ordinary meeting.

## Run

```bash
murmurmark corpus remote-identity-shadow-errors-v1 preflight
murmurmark corpus remote-identity-shadow-errors-v1 freeze
murmurmark corpus remote-identity-shadow-errors-v1 analyze
murmurmark corpus remote-identity-shadow-errors-v1 replay
murmurmark corpus remote-identity-shadow-errors-v1 finalize
```

For the same sequence in one command:

```bash
murmurmark corpus remote-identity-shadow-errors-v1 all
murmurmark corpus remote-identity-shadow-errors-v1 status
```

Read the portable report:

```bash
less sessions/_reports/remote-speaker-shadow-error-decomposition-v1/\
remote_speaker_shadow_error_decomposition_report.md
```

Do not replace policy hashes when preflight fails. Determine which upstream artifact changed and
rerun its own frozen contract first.

## Current Result

```text
decision: ADVANCE_INTERVAL_PURIFICATION
next_goal: Bounded Remote Speaker Interval Purification v1
```

The failure scope contains 214 items, 699 words and 392.415726 seconds. The fixed routing result is:

| Axis | Items | Seconds | Item ratio | Seconds ratio | Material score |
|---|---:|---:|---:|---:|---:|
| interval purification | 93 | 201.273504 | 0.434579 | 0.512909 | 0.434579 |
| enrollment hardening | 83 | 119.920926 | 0.387850 | 0.305597 | 0.305597 |
| identity backend | 38 | 71.221296 | 0.177570 | 0.181495 | 0.177570 |

The interval axis exceeds the next material score by `0.128982`, above the fixed `0.10` dominance
gate. All 214 failure items are explained. Two embedding failures are confirmed silent. Of the four
independent-reference mismatch words, three refer to a coarse reference speaker absent from the
session-local mapping and one is a real machine-reference identity conflict. There is no human
truth for these four words.

Do not lower ECAPA thresholds or apply accepted shadow labels. The next experiment may change only
the bounded audio interval presented to the same frozen identity backend.
