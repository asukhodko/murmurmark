# Post-Segmentation Transcript Rebaseline v1 Runbook

Use this only when the current production transcript stack has already processed the intended real
sessions. The command audits existing artifacts; it does not repair or regenerate them.

## Build Or Refresh

```bash
murmurmark corpus post-segmentation-rebaseline all \
  --refresh \
  --write-snapshot
```

Review:

```bash
less sessions/_reports/post-segmentation-transcript-rebaseline-v1/post_segmentation_rebaseline_report.md
jq '.decision, .next_priority, .gates' \
  sessions/_reports/post-segmentation-transcript-rebaseline-v1/post_segmentation_rebaseline_report.json
```

`--refresh` changes the frozen private corpus. Do not use it merely to make a failed gate disappear.

## Exact Replay

```bash
murmurmark corpus post-segmentation-rebaseline all --verify-existing
```

A mismatch means a frozen policy, control or session input changed. Inspect the private manifest
locally, decide whether the change is expected, and refresh only after that decision.

## Interpreting The Result

- `rebaseline_evidence_complete=true`: the measurement itself is usable.
- `product_no_regression=false`: the report found a real product defect; the rebaseline can still be
  complete.
- `read_surfaces_coherent=true`: strict, provisional and aggregate views follow the current policy.
- `explicit_unknown`: unsupported speaker attribution, not an ASR word error count.
- `lexical_evidence=partial_no_fresh_independent_truth`: review queues exist, but they do not prove
  WER or correctness.

The first actionable residual in `residual_axes.jsonl` is the one recommended next target. Preserve
the report and its input manifest until that target is closed.
