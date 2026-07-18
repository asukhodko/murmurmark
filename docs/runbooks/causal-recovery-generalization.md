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

## Handoff To The Current Goal

This runbook freezes the `DO_NOT_PROMOTE` baseline. Causal Candidate Coverage and Cheap Negative
Prefilter v1 must use the same corpus membership, input SHA-256 manifest, fixed `4` recoveries /
`11.56s`, `65` adversarial controls and three holdouts. It must write a separate report namespace;
do not refresh or overwrite `causal-recovery-generalization-v1` merely to improve its decision.

The next acceptance run must prove:

- cheap causal decisions for `783/783` eligible rows;
- explicit routing counts for `cheap_reject`, `expensive_candidate` and `unresolved`;
- zero accepted remote-only, ASR-noise and adversarial controls;
- deterministic offline/runtime outcomes;
- holdout runtime p95 at most `30s` and final lag `0`;
- no per-session order, remote-like `Me`, token-F1 or mandatory-review regression.

Until those checks exist and pass, keep the current `DO_NOT_PROMOTE` decision and normal preview
unchanged.
