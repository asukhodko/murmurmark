# Stronger Local Remote Speaker Representation Qualification v1 Runbook

This is a frozen research command, not an ordinary meeting workflow.

## Model Setup

Verify an existing local model:

```bash
.venv/bin/python scripts/setup-stronger-local-remote-speaker-representation-v1.py verify
```

Download the pinned model once, then use it offline:

```bash
.venv/bin/python scripts/setup-stronger-local-remote-speaker-representation-v1.py download
```

The runtime requires importable `onnxruntime`, `torch` and `torchaudio`. Evaluation sets
`HF_HUB_OFFLINE=1`, uses CPU and runs with `nice=20`.

## Frozen Flow

The completed experiment used separate commands so the freeze boundary was explicit:

```bash
murmurmark corpus remote-representation-v1 preflight
murmurmark corpus remote-representation-v1 prepare
murmurmark corpus remote-representation-v1 freeze
murmurmark corpus remote-representation-v1 evaluate
murmurmark corpus remote-representation-v1 replay
murmurmark corpus remote-representation-v1 finalize
murmurmark corpus remote-representation-v1 status
```

`all` is available only for a fresh, truth-unseen frozen run:

```bash
murmurmark corpus remote-representation-v1 all
```

Do not rerun `prepare` merely to seek better metrics. It discards this experiment's own freeze and
post-freeze outputs. Model, segmentation, K, thresholds and policy must be declared as a new version
before another evaluation.

## Expected Result

```text
decision: KEEP_EXPLICIT_UNKNOWN
candidate: wespeaker_resnet34_lm_onnx
windows: 347
minimum_candidate_stability_ari: 0.442394
preserved_confirmed_gains: 3/3
new_false_identities: 12
next: close_current_lightweight_local_representation_route
```

This result means WeSpeaker must not be wired into the selected transcript. The next meaningful
candidate class is temporal/end-to-end diarization over the remote track, not another threshold pass
over isolated embeddings.

## Verification

```bash
.venv/bin/python scripts/check-stronger-local-remote-speaker-representation-v1.py
```

The check verifies the terminal result, frozen hashes, label-free pack, deterministic replay and
production invariants.
