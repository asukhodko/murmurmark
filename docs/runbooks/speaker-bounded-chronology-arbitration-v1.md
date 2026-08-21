# Speaker-Bounded Chronology Evidence Arbitration v1 Runbook

This is a maintainer corpus command, not part of a normal meeting lifecycle.

## Inspect And Replay

```bash
murmurmark corpus chronology-arbitration-v1 status
murmurmark corpus chronology-arbitration-v1 replay --write-snapshot
```

Replay verifies every frozen SHA-256 and compares regenerated public output byte for byte. Expected
decision for the current six-session corpus is `PROMOTE_CHRONOLOGY_EVIDENCE_ARBITRATION_V1`.

## Intentional Refresh

Refresh only after Post-Segmentation Transcript Rebaseline or one of its local evidence artifacts
has been deliberately requalified:

```bash
murmurmark corpus chronology-arbitration-v1 preflight
murmurmark corpus chronology-arbitration-v1 all --refresh --write-snapshot
murmurmark corpus chronology-arbitration-v1 replay --write-snapshot
murmurmark corpus terminal-gate-v1 all --refresh --write-snapshot
```

Do not use `--refresh` to hide drift. A stale input is a failed check until the upstream change has
its own qualification.

## Interpret The Result

```bash
jq '{decision, summary, gates}' \
  sessions/_reports/speaker-bounded-chronology-arbitration-v1/speaker_bounded_chronology_arbitration_report.json
```

The current result closes 38 rows / `255.97s` and leaves 14 rows / `89.97s`. Inspect private items
only when developing the next evidence layer:

```bash
jq -s 'group_by(.outcome) | map({outcome: .[0].outcome, rows: length})' \
  sessions/_reports/speaker-bounded-chronology-arbitration-v1/private/arbitration_items.jsonl
```

`remote_leak_or_asr_segmentation`, `true_chronology_risk` and `insufficient_evidence` are not
permission to edit a transcript. They remain explicit terminal blockers.

## Missing Stronger Judge

The stronger local judge is optional because some old sessions do not have it. Such absence must
reduce confidence or produce `insufficient_evidence`; it must never make a row easier to close.
