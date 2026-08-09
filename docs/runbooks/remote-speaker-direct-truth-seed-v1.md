# Remote Speaker Direct Truth Seed v1 Runbook

This is a private development review. It does not run during an ordinary meeting.

## Verify The Frozen Pack

```bash
murmurmark corpus remote-truth-seed-v1 preflight
murmurmark corpus remote-truth-seed-v1 build
murmurmark corpus remote-truth-seed-v1 status
murmurmark corpus remote-truth-seed-v1 replay
```

Expected result before direct review:

```text
decision: REFERENCE_INSUFFICIENT
seed_items: 33
primary_answers: 0
repeat_answers: 0
remaining_slots: 41
```

## Blind Review

Request one opaque slot:

```bash
murmurmark corpus remote-truth-seed-v1 next
```

To play the target, every anonymous exemplar and the target once more in the same terminal:

```bash
murmurmark corpus remote-truth-seed-v1 next --play
```

Listen to the target clip and the anonymous exemplars printed by the command. Do not inspect
`seed_selection.jsonl`, `slot_map.jsonl`, the enrollment comparison or previous model references.

Record one explicit outcome:

```bash
murmurmark corpus remote-truth-seed-v1 grade SLOT_ID \
  --outcome remote_speaker_01
```

Use `unknown_speaker`, `mixed` or `unusable` whenever one anonymous speaker is not directly
supported. Hidden repeats intentionally look like ordinary slots.

Continue until `next` prints `review_queue: complete`, then run:

```bash
murmurmark corpus remote-truth-seed-v1 finalize
murmurmark corpus remote-truth-seed-v1 replay
murmurmark corpus remote-truth-seed-v1 status
```

`DIRECT_TRUTH_SEED_READY` permits a separate backend qualification goal. It does not change the
current transcript. `REFERENCE_INSUFFICIENT` means only the remaining blind slots are needed.

Completed frozen result:

```text
decision: DIRECT_TRUTH_SEED_READY
primary_answers: 33
repeat_answers: 8
repeat_consistency: 0.875
attributed / unknown / mixed / unusable: 8 / 11 / 4 / 10
```

Do not reopen or relabel this queue during candidate adjudication. Ambiguous exemplars were handled
fail-closed and are part of the evidence boundary.
