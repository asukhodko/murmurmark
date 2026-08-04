# Reference-Conditioned Target-Me Separation

This runbook reproduces the bounded v2 research result. It is not part of the normal
`murmurmark meeting` path and cannot replace production audio by itself.

## Prerequisites

- Target-Me Identifiability Corpus v1 current decision is
  `READY_FOR_TARGET_CONDITIONED_TRAINING`;
- publication fingerprint is
  `530cb0fd23503884d438bc24be10fff45610da1fb8fe710aad1b6b6cd992b2ce`;
- the local WavLM x-vector model passes its pinned SHA-256 checks;
- Speaker-Preserving Neural Echo v2 remains the production fallback;
- no network access is needed or allowed during the run.

## Reproduce The Frozen Result

```bash
cd murmurmark
source .venv/bin/activate

python scripts/reference-conditioned-target-me-separation-v2.py preflight
python scripts/reference-conditioned-target-me-separation-v2.py reproduce-v1
python scripts/reference-conditioned-target-me-separation-v2.py train-dev
python scripts/reference-conditioned-target-me-separation-v2.py decide
```

`train-dev` normally returns exit code `2` because the measured candidate is rejected by the
locked dev gates. This is an expected experimental decision, not a pipeline crash. `decide` then
returns success after writing the final `DO_NOT_PROMOTE` result.

The heavy stage applies MurmurMark's background resource policy (`nice=20`, bounded compute
threads). Reusing a valid cache is safe. Use `--refresh-cache` only to reverify and rebuild the
train/dev waveform cache.

## Hard Access

Run this only when `train-dev/candidate_lock.json` says both:

```text
decision = DEV_CANDIDATE_LOCKED
hard_test_access_authorized = true
```

Then one command consumes the single hard access:

```bash
python scripts/reference-conditioned-target-me-separation-v2.py hard-test
```

The frozen v2 candidate is rejected on dev, so this command must refuse access and must not create
`hard_access.json` or a hard waveform cache. Threshold changes after a dev result invalidate the
experiment; do not lower gates to unlock hard.

## Inspect

```bash
ROOT="sessions/_reports/reference-conditioned-target-me-separation-v2"

jq '{decision, fingerprint, blockers}' "$ROOT/train-dev/train_dev_report.json"
jq '.' "$ROOT/train-dev/determinism_report.json"
jq '{decision, fingerprint, limiting_stage, production_unchanged}' "$ROOT/decision.json"
less "$ROOT/decision.md"
```

Expected final decision:

```text
DO_NOT_PROMOTE_REFERENCE_CONDITIONED_TARGET_ME_SEPARATION_V2
```

Hard and sealed reports remain explicit `NOT_OPENED` records. Production stays byte-exact
Speaker-Preserving Neural Echo v2.
