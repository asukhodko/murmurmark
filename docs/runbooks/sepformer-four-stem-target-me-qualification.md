# SepFormer Four-Stem Target-Me Qualification v1 Runbook

This is an offline research command. It is not part of `murmurmark meeting`.

## Preconditions

```bash
cd murmurmark
source .venv/bin/activate

HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  scripts/stronger-offline-target-speaker-separator-prerequisites-v1.py verify
```

The pinned SepFormer, its private SpeechBrain runtime and WavLM enrollment model must already be
present. The command never downloads files.

## Run Once

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  scripts/sepformer-four-stem-target-me-qualification-v1.py run
```

`run` performs these monotonic steps:

1. `freeze` verifies source, model and production hashes;
2. `materialize` creates only train/dev supervision;
3. `calibrate-train` runs frozen SepFormer and locks WavLM assignment thresholds;
4. `evaluate-dev` consumes the lock and creates the one immutable dev result;
5. `decide` and `verify` publish and check the terminal decision.

The background resource policy applies `nice=20`, macOS background scheduling and at most four
compute threads. A normal run can take tens of minutes on CPU.

## Inspect

```bash
jq '{decision, blockers, fingerprint}' \
  sessions/_reports/sepformer-four-stem-target-me-qualification-v1/decision.json

jq '{decision, thresholds, checks, fingerprint}' \
  sessions/_reports/sepformer-four-stem-target-me-qualification-v1/train/calibration_lock.json

if [[ -f sessions/_reports/sepformer-four-stem-target-me-qualification-v1/dev/dev_report.json ]]; then
  jq '{decision, aggregate, checks, fingerprint}' \
    sessions/_reports/sepformer-four-stem-target-me-qualification-v1/dev/dev_report.json
fi
```

The frozen v1 result stops at rejected train calibration, so absence of `dev/dev_report.json` is
expected and proves that the dev gate was respected.

## Resume And Replay

The runner reuses verified materialized audio and immutable separator caches. If interrupted before
dev, rerun the required command or `run`. Once `dev/dev_report.json` exists, `evaluate-dev` only
verifies it. Cache time is cumulative across resumes. Delete nothing and change no threshold to
force a pass.

```bash
scripts/sepformer-four-stem-target-me-qualification-v1.py verify
scripts/check-sepformer-four-stem-target-me-qualification-v1.py
```

Never use `future_hard`, ordinary meetings, ASR text or transcript cleanup to tune this result.
