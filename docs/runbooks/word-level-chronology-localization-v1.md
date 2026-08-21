# Word-Level Chronology Localization v1 Runbook

This is a maintainer corpus command. It is not part of an ordinary meeting lifecycle.

## Normal Check

```bash
murmurmark corpus chronology-localization-v1 status
murmurmark corpus chronology-localization-v1 replay --write-snapshot
```

Expected decision for the current frozen corpus:
`PROMOTE_WORD_LEVEL_CHRONOLOGY_LOCALIZATION_V1`, with 5 rows / `37.14s` remaining.

## Intentional Refresh

Refresh only after the upstream chronology corpus, clips, model or implementation was deliberately
requalified:

```bash
HF_HUB_OFFLINE=1 murmurmark corpus chronology-localization-v1 preflight
HF_HUB_OFFLINE=1 murmurmark corpus chronology-localization-v1 all --refresh --write-snapshot
murmurmark corpus chronology-localization-v1 replay --write-snapshot
murmurmark corpus terminal-gate-v1 all --refresh --write-snapshot
```

The first run decodes 28 short source clips. Later runs reuse the private fingerprinted cache.
Resource policy from MurmurMark still applies; direct maintenance runs may additionally use
`nice -n 20`.

## Inspect The Bound

```bash
jq '{decision, summary, chronology, gates}' \
  sessions/_reports/word-level-chronology-localization-v1/word_level_chronology_localization_report.json

jq -s 'group_by(.outcome) | map({outcome: .[0].outcome, rows: length,
  seconds: (map(.duration_sec) | add)})' \
  sessions/_reports/word-level-chronology-localization-v1/localization_items.jsonl
```

Private words and paths are available only for development:

```bash
jq -c 'select(.closed == false) | {alias, item_id, outcome, evidence}' \
  sessions/_reports/word-level-chronology-localization-v1/private/localization_items.jsonl
```

Do not lower thresholds to force zero residual. The five remaining rows lack a supported remote
word span and define the current evidence boundary. A stale clip, model-file signature or decode
hash produces `EVIDENCE_INCOMPLETE`; refresh only after the changed input was intentionally
requalified.
