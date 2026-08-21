# Speaker-Bounded Chronology Evidence Arbitration v1

Status: promoted read-only evidence layer.

## Purpose

The transcript-order audit deliberately over-reports any long `Me`/remote overlap. This layer
separates normal turn boundaries and genuine double-talk from chronology risks without changing a
transcript. The result narrows the chronology dimension of the terminal gate; it does not retime,
drop or relabel speech.

## Frozen Inputs

The policy points at the Post-Segmentation Transcript Rebaseline v1 private manifest. For every
session with a blocking order row, `freeze` records path, size and SHA-256 for:

- transcript-order report and item rows;
- selected dialogue;
- group-overlap rows and summary;
- speaker state;
- raw mic/remote audio and preprocessed mic evidence;
- optional local faster-whisper judge rows and summary.

Policy, implementation and rebaseline are also fingerprinted. Missing required evidence or any
later byte change fails closed. Missing optional stronger-audio evidence cannot close a row.

## Outcomes

Every frozen row receives exactly one outcome:

- `benign_turn_boundary`: the overlap is a timing boundary supported by group evidence and interval
  identity; closed;
- `confirmed_double_talk`: local and remote speech are independently supported; closed;
- `remote_leak_or_asr_segmentation`: evidence points at duplicate/leak/noise; remains open;
- `true_chronology_risk`: order evidence remains stronger than benign explanations; remains open;
- `insufficient_evidence`: judges are missing, weak or conflicting; remains open.

Only the first two outcomes reduce mandatory chronology review. A single similarity score, text
match or model label is insufficient.

## Outputs

```text
sessions/_reports/speaker-bounded-chronology-arbitration-v1/
  private/input_manifest.json
  private/arbitration_items.jsonl
  speaker_bounded_chronology_arbitration_report.json
  speaker_bounded_chronology_arbitration_report.md

docs/testing/speaker-bounded-chronology-arbitration-v1-snapshot.json
```

Schemas:

- `murmurmark.speaker_bounded_chronology_arbitration_policy/v1`;
- `murmurmark.speaker_bounded_chronology_arbitration_input/v1`;
- `murmurmark.speaker_bounded_chronology_arbitration_item/v1`;
- `murmurmark.speaker_bounded_chronology_arbitration_report/v1`;
- `murmurmark.speaker_bounded_chronology_arbitration_snapshot/v1`;
- `murmurmark.speaker_bounded_chronology_arbitration_replay/v1`.

## Decision

`PROMOTE_CHRONOLOGY_EVIDENCE_ARBITRATION_V1` requires a stable outcome for every row, local-only
offline evidence, exact input identity, privacy-safe public output, no transcript/raw mutation and
at least 50% closure by both row count and seconds. Otherwise the result is `EVIDENCE_BOUND` or an
input error. Promotion only authorizes the terminal instrument to consume `remaining_seconds`.

## Safety And Privacy

The stage is read-only. It cannot change raw audio, Echo Guard, ASR, text, roles, timestamps or
selected profiles. Public and tracked files contain no session IDs, paths or speech. Full
provenance remains under ignored `sessions/`.
