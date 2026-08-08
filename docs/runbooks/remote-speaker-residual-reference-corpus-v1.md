# Remote Speaker Residual Reference Corpus v1 Runbook

The corpus is a development and private-review tool. It does not run during an ordinary meeting.

## Build And Verify

```bash
murmurmark corpus remote-reference build
murmurmark corpus remote-reference status
murmurmark corpus remote-reference replay
```

Expected current decision:

```text
decision: REFERENCE_INSUFFICIENT
review_items: 0/278
proposal_words: 0/53
direct_reference_words: 0
candidate_precision: None
```

The public report is:

```text
sessions/_reports/remote-speaker-residual-reference-corpus-v1/
  remote_speaker_residual_reference_report.json
  remote_speaker_residual_reference_report.md
```

## Blind Review

Show the next unresolved item without exposing the WavLM candidate:

```bash
murmurmark corpus remote-reference next
```

Listen to the bounded clip and anonymous session-local exemplars printed by the command, then record
an explicit decision:

```bash
murmurmark corpus remote-reference grade ITEM_ID \
  --outcome remote_speaker_01 \
  --truth-grade human_reviewed
```

Use `unknown_speaker`, `mixed` or `unusable` when the evidence does not support one listed speaker.
Do not inspect `sealed_predictions.jsonl` before grading an item.

## Interpretation

`REFERENCE_INSUFFICIENT` is the correct result while direct truth is incomplete. It blocks tuning or
promotion from model agreement but leaves Coverage v3 and the ordinary transcript unchanged.

The frozen aggregate has a documented 0.440-second accounting gap from Coverage v3: 598.239509
seconds are in scope, while 597.799509 seconds are attached to the 851 word intervals. The gap is
tracked explicitly and is not fabricated into a word or timestamp.

Private speech, clips and answers remain under ignored `sessions/`. Never commit them.

