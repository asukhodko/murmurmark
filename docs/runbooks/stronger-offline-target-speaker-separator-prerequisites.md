# Stronger Offline Target-Speaker Separator Prerequisites v1 Runbook

This is a research-only command. It is not part of `murmurmark meeting`.

## Local Files

The pinned model and runtime are expected under:

```text
~/.local/share/murmurmark/models/stronger-offline-target-speaker-v1/
~/.local/share/murmurmark/runtimes/stronger-offline-target-speaker-v1-speechbrain/
```

The policy contains the exact model revision, file SHA-256 values, wheel names and wheel hashes.
Preparation may use the network. Every command below must work with the network disabled.

## Run

```bash
cd murmurmark
source .venv/bin/activate

HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  scripts/stronger-offline-target-speaker-separator-prerequisites-v1.py run
```

Inspect the immutable result:

```bash
jq '{decision, blockers, fingerprint}' \
  sessions/_reports/stronger-offline-target-speaker-separator-prerequisites-v1/decision.json

jq '{decision, passed, evidence_fingerprint, runs: [.runs[] | {
  load_sec, inference_sec, peak_rss_mb, output_sha256, torch_threads,
  nice: .resource_policy.nice_after, network_attempts
}]}' \
  sessions/_reports/stronger-offline-target-speaker-separator-prerequisites-v1/resource_preflight.json
```

Expected decision for the frozen 2026-08-06 environment:

```text
READY_FOR_STRONGER_SEPARATOR_QUALIFICATION
```

## Repeatability

Run `run` twice. The wall-clock fields may differ. These values must remain exact:

- both child `output_sha256` values;
- `resource_preflight.evidence_fingerprint`;
- `decision.fingerprint`;
- all frozen source and model hashes.

## Failure Handling

Do not edit hashes or thresholds to make the check pass. Restore the exact local model/runtime or
accept `CURRENT_RESOURCE_LIMIT_REACHED`. Never open hard/sealed data or send candidate audio to ASR
to compensate for a failed prerequisite.

The next stage may render expanded train/dev supervision and qualify the adapter. It still cannot
open hard/sealed or publish production audio before an immutable dev pass.
