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

## Candidate Prefilter Completion

The follow-up experiment writes a separate namespace and never refreshes the immutable baseline:

```bash
.venv/bin/python scripts/check-causal-candidate-prefilter-v1.py

.venv/bin/python scripts/run-causal-candidate-prefilter-corpus-v1.py \
  --decision-only \
  --require-complete

# Expensive corpus execution is local and intentionally slow. Reuse existing outputs unless the
# algorithm or immutable inputs changed.
.venv/bin/python scripts/run-causal-candidate-prefilter-corpus-v1.py \
  --require-complete

.venv/bin/python scripts/report-causal-candidate-prefilter-v1.py
.venv/bin/python scripts/check-causal-candidate-prefilter-acceptance-v1.py
```

Primary output directory:

```text
sessions/_reports/live-pipeline/causal-candidate-coverage-cheap-negative-prefilter-v1/
  cheap_prefilter_decisions_v1.jsonl
  coverage_report_v1.json
  coverage_report_v1.md
  runtime_equivalence_v1.json
  no_regression_v1.json
  promotion_decision.json
  acceptance_report_v1.json
```

Current result is `DO_NOT_PROMOTE`: routes cover `783/783`, fixed recovery remains `4/11.56s`, all
per-session quality checks pass and all `65` frozen negative controls remain rejected. One newly
accepted candidate is post-hoc probable ASR noise, and `0/3` holdouts pass the runtime gates. `20`
expensive attempts time out fail-open; p95 reaches `42.634s`; final lag remains `0`.

Do not increase the `28s` timeout, weaken remote-forbidden guards or refresh the old corpus to make
this decision green. The next product goal is authoritative batch boundary/review closure. A future
persistent faster-whisper worker is a separate isolated hypothesis and must start from these exact
reports.
