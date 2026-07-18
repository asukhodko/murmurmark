# Causal Recovery Generalization Runbook

Use this runbook only for the explicit causal recovery lab. It does not change normal live preview,
batch transcript, notes or export.

## Build And Freeze

`--refresh` intentionally replaces the frozen corpus. Do not use it for an ordinary verification.
An initial private corpus build must pass explicit `--regression-session`, `--holdout-session` and
`--holdout-kind SESSION=group|one_to_one` arguments. Once frozen, the builder reuses membership from
the manifest and the shorter command below is sufficient.

```bash
.venv/bin/python scripts/build-causal-recovery-generalization-corpus.py \
  --refresh \
  --require-valid
```

Subsequent immutable verification:

```bash
.venv/bin/python scripts/build-causal-recovery-generalization-corpus.py \
  --require-valid
```

## Evaluate And Decide

```bash
.venv/bin/python scripts/report-causal-recovery-generalization-v1.py \
  --require-decision

jq '{status, decision, blockers, next_experiment}' \
  sessions/_reports/live-pipeline/causal-recovery-generalization-v1/promotion_decision.json
```

`promotion_decision.status == "passed"` means the binary decision is complete and reproducible. It
does not mean the algorithm was promoted. Inspect `decision`, which is currently
`DO_NOT_PROMOTE`.

## Full Acceptance

```bash
.venv/bin/python scripts/check-causal-recovery-generalization-v1.py
```

This command verifies fail-open behavior, the immutable corpus, two identical outcome fingerprints,
all row explanations, unchanged input SHA-256 and the final binary decision.

Primary artifacts:

```text
corpus_manifest_v1.json
corpus_rows_v1.jsonl
outcomes_v1.jsonl
generalization_report_v1.json
generalization_report_v1.md
promotion_decision.json
fail_open_report_v1.json
acceptance_report_v1.json
```

## Recording-Time Holdout Replay

The current three holdouts are deliberately replayed into isolated output directories:

```bash
.venv/bin/python scripts/replay-live-causal-me-recovery-runtime.py SESSION \
  --stride-chunks 12 \
  --output-dir SESSION/derived/live/causal-recovery-generalization-v1/runtime \
  --verify-warm-final \
  --agreement-scope double-talk \
  --refresh
```

A failed replay is valid evidence when the child fails open and raw/batch artifacts remain usable.
Do not increase timeout or weaken remote-forbidden guards merely to turn the report green.
