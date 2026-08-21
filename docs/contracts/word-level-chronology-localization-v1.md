# Word-Level Chronology Localization v1

Status: promoted read-only evidence layer.

## Purpose

Speaker-Bounded Chronology Evidence Arbitration v1 reduced the broad order-audit queue to 14 rows /
`89.97s`, but its segment-level timestamps could not distinguish actual simultaneous speech from
wide ASR segments. This layer runs a local word-timestamp pass over exactly that frozen residual.
It narrows the chronology gate without editing a transcript.

## Frozen Inputs

The private input manifest fingerprints:

- the upstream chronology report, private rows and input manifest;
- this policy and implementation;
- every selected `mic_clean` and `remote` clip;
- the local faster-whisper `large-v3` model identity and decode configuration;
- the frozen 14-row queue.

The decoder is offline, CPU `int8`, beam 1, without VAD or previous-text conditioning, and with
`word_timestamps=true`. Decodes are cached by clip SHA-256, model SHA-256 and complete decode
configuration. Evaluation verifies every decode against the frozen clip hash and configuration;
replay also verifies the current model-file signature. Missing or changed evidence fails open to an
unresolved outcome.

## Localization

Selected `Me` text is aligned only against `mic_clean`; selected remote text is aligned separately
against the remote track. Low-probability words are excluded. A role span is supported only by a
minimum number of matching content tokens, containment, word probability and aggregate alignment
score. Local evidence must also be independent from the remote-text alignment.

Both tracks share the same clip timeline, so their supported first/last word timestamps provide the
actual overlap or gap. Broad published utterance timestamps remain untouched.

## Outcomes

- `localized_sequential_boundary`: independently supported role spans do not overlap beyond the
  fixed tolerance; closed;
- `localized_double_talk`: independently supported different role spans overlap; closed;
- `transferred_remote_leak_or_segmentation`: remote-only state and remote words move the row out of
  the chronology dimension; closed here, still explicit in its proper evidence class;
- `conflicting_role_alignment`: mic words are not independent from remote evidence; remains open;
- `insufficient_word_alignment`: one or both role spans cannot be localized; remains open;
- `evidence_unavailable`: local model or decode evidence is missing; remains open.

## Outputs

```text
sessions/_reports/word-level-chronology-localization-v1/
  private/input_manifest.json
  private/model_identity.json
  private/frozen_items.jsonl
  private/word_decodes.jsonl
  private/decode_cache/*.json
  private/localization_items.jsonl
  localization_items.jsonl
  word_level_chronology_localization_report.json
  word_level_chronology_localization_report.md
  replay_report.json
  artifact_manifest.json

docs/testing/word-level-chronology-localization-v1-snapshot.json
```

Public outputs contain aliases, item IDs, numeric evidence and hashes, but no session IDs, absolute
paths or speech. Full words and provenance remain under ignored `sessions/`.

## Promotion

`PROMOTE_WORD_LEVEL_CHRONOLOGY_LOCALIZATION_V1` requires a stable outcome for all 14 rows and at
least 50% closure by both row count and seconds. Promotion only lets Terminal Gate consume
`final_remaining_seconds`. It does not authorize transcript mutation, retiming or role changes.

The frozen result closes 9/14 rows and `52.83/89.97s`; the final chronology residual is 5 rows /
`37.14s`.
