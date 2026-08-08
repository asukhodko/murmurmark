# Stronger Remote Speaker Identity Backend Qualification v1 Runbook

Run from the repository root with the project virtual environment active.

## Setup

Inspect the isolated ECAPA runtime and pinned model:

```bash
murmurmark corpus remote-identity-v1 setup
```

If they are absent, install them once with network access:

```bash
murmurmark corpus remote-identity-v1 install
murmurmark corpus remote-identity-v1 setup
```

The model and runtime live outside the repository. `setup` must report `offline_ready: true` before
qualification. Model license, revision and hashes are frozen in the policy.

## Reproduce The Qualification

The completed corpus already has a one-shot opening ledger. Normal verification must use `status`
and `replay`; do not delete or regenerate hard-v4.

```bash
murmurmark corpus remote-identity-v1 preflight
murmurmark corpus remote-identity-v1 status
murmurmark corpus remote-identity-v1 replay
murmurmark corpus perfection all --verify-existing
```

The low-level equivalent is:

```bash
.venv/bin/python scripts/setup-remote-speaker-identity-backend-v1.py status
.venv/bin/python scripts/qualify-stronger-remote-speaker-identity-backend-v1.py preflight
.venv/bin/python scripts/qualify-stronger-remote-speaker-identity-backend-v1.py status
.venv/bin/python scripts/qualify-stronger-remote-speaker-identity-backend-v1.py replay
```

`freeze`, `develop`, `evaluate-hard` and `all` exist for a fresh isolated output root or automated
fixture. They are not routine commands for the completed default corpus.

## Expected Result

```text
decision: PROMOTE_LAB_IDENTITY_CANDIDATE
selected_candidate: speechbrain_ecapa_voxceleb_candidate
```

On hard-v4 the candidate must retain 154/154 words, B-cubed F1 `0.948042`, pairwise precision `1.0`,
known-speaker recall `0.947368`, zero open-set false attribution, all six mixed words fail-closed and
boundary recall `13/23`. Replay must remain byte-identical and the hard-v4 opening count must remain
one.

This result does not change the ordinary transcript. Its only permitted follow-up is a separate
ECAPA real-session shadow qualification against reviewed, session-local evidence.

## Recovery

- `backend_*_unavailable`: restore the exact pinned model/runtime; do not change the policy hashes.
- `upstream_guard_changed`: investigate the named frozen input instead of refreshing the guard.
- `hard_v4_*_mismatch`: preserve the corpus and compare the recorded SHA-256; do not reopen it.
- replay mismatch: stop promotion work and retain the existing Coverage v3 production path.

Missing dependencies fail closed. They must never cause a fallback to network access or alter
production outputs.
