# Multi-Component Residual Separator v1 Runbook

This runbook reproduces the completed local qualification. It is not part of ordinary
`murmurmark meeting` processing.

```bash
cd murmurmark
source .venv/bin/activate

python scripts/multi-component-residual-separator-v1.py preflight
python scripts/multi-component-residual-separator-v1.py train-dev
python scripts/multi-component-residual-separator-v1.py decide
python scripts/multi-component-residual-separator-v1.py verify
```

`train-dev` runs at the background resource policy and can take close to ten minutes. Do not run
`hard-test` after the frozen v1 result: the dev candidate is rejected and the controller denies
access before reading hard audio.

Inspect the result locally:

```bash
jq '{decision, limiting_stage, blockers}' \
  sessions/_reports/multi-component-residual-separator-v1/decision.json

jq '{target_me: .dev.aggregate.roles.target_me.target_snr_db.median,
     other_local: .dev.aggregate.metrics.other_local_snr_db.median,
     residual: .dev.aggregate.metrics.unexplained_residual_snr_db.median}' \
  sessions/_reports/multi-component-residual-separator-v1/train-dev/train_dev_report.json
```

Expected decision: `READY_FOR_STRONGER_LOCAL_SEPARATOR`. Production v2 remains the exact fallback.
