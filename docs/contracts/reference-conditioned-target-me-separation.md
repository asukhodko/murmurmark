# Reference-Conditioned Target-Me Separation Contract

This contract defines the isolated pre-ASR experiment that follows the promoted
`speaker_preserving_neural_echo_v2` baseline. It does not change production selection by itself.

## Inputs

Every run is bound to `policies/reference-conditioned-target-me-separation-v1.json`. The policy
pins:

- the promoted v2 production policy and sealed twelve-session corpus;
- the Controlled Echo Supervision train/dev/hard manifests and replay decision;
- the local Target-Me encoder and v2 fallback model hashes;
- data isolation, audio conservation and promotion gates.

Missing or changed evidence blocks the experiment. Network model downloads are forbidden during a
run. Reports use repository-relative or home-relative paths and never embed meeting text.

## Preflight

```bash
.venv/bin/python scripts/reference-conditioned-target-me-separation-v1.py \
  preflight --verify-audio all
```

Outputs under `sessions/_reports/reference-conditioned-target-me-separation-v1/`:

- `frozen_inputs.json`, schema `murmurmark.reference_conditioned_target_me_frozen_inputs/v1`;
- `preflight_report.json`, schema `murmurmark.reference_conditioned_target_me_preflight/v1`;
- `preflight_report.md`.

`READY_FOR_ORACLE_CEILING` requires all pinned hashes, required modules, local models, split counts
and controlled audio hashes to pass. Any mismatch produces `BLOCKED_PREFLIGHT`.

## Three-Stem Audio

The experiment accepts 16 kHz mono mic mixture, aligned digital remote, Target-Me enrollment,
delay evidence and speaker state. It returns equal-length finite stems:

```text
mic = target_me + remote_echo + other_local
```

Unassigned numerical residual belongs to `other_local`. The maximum reconstruction error is
`1e-5`. Exact remix proves accounting only; it does not prove correct speaker assignment.

## Oracle Ceiling

```bash
.venv/bin/python scripts/reference-conditioned-target-me-separation-v1.py oracle-ceiling
```

Outputs:

- `oracle_rows.jsonl`, schema `murmurmark.reference_conditioned_target_me_oracle_row/v1`;
- `oracle_ceiling_report.json`, schema
  `murmurmark.reference_conditioned_target_me_oracle_ceiling/v1`;
- `oracle_ceiling_report.md`.

The oracle uses only frozen `train` and `dev` synthetic pairs. It must not open `hard_test`. Ideal
ratio and complex masks measure representation capacity; they receive no promotion credit.

## Train/Dev Lock And Decision

```bash
.venv/bin/python scripts/reference-conditioned-target-me-separation-v1.py overfit-probe
.venv/bin/python scripts/reference-conditioned-target-me-separation-v1.py train-dev
.venv/bin/python scripts/reference-conditioned-target-me-separation-v1.py decide
```

`train-dev` writes an isolated checkpoint, row metrics and
`murmurmark.reference_conditioned_target_me_candidate_lock/v1`. Only
`DEV_CANDIDATE_LOCKED` may authorize hard-test access. `DEV_CANDIDATE_REJECTED` must keep both the
hard split and sealed production corpus unopened.

`decide` writes:

- `data_card.json`, schema `murmurmark.reference_conditioned_target_me_data_card/v1`;
- `model_card.json`, schema `murmurmark.reference_conditioned_target_me_model_card/v1`;
- `experiment_manifest.json`, schema
  `murmurmark.reference_conditioned_target_me_experiment_manifest/v1`;
- `corpus_report.json`, schema `murmurmark.reference_conditioned_target_me_corpus_report/v1`;
- `decision.json`, schema `murmurmark.reference_conditioned_target_me_decision/v1`;
- matching short Markdown reports.

Rejected checkpoints remain immutable evidence. A missing or changed checkpoint, candidate lock,
policy, frozen baseline or sealed manifest aborts decision materialization and leaves production
unchanged.

## Candidate Session Artifacts

A trainable candidate writes only isolated derived outputs:

```text
derived/preprocess/reference-conditioned-target-me-separation-v1/
  input_manifest.json
  model_manifest.json
  chunks.jsonl
  stems/target_me.wav
  stems/remote_echo.wav
  stems/other_local.wav
  reconstruction_report.json
  asr_shadow/
  session_report.json
```

Each manifest carries policy, model, input and output SHA-256 values. Raw CAF and v2 artifacts are
read-only.

## Selection Boundary

Candidate audio may become the mic input only after one frozen corpus decision:

```text
PROMOTE_REFERENCE_CONDITIONED_TARGET_ME_SEPARATION_V1
DO_NOT_PROMOTE_REFERENCE_CONDITIONED_TARGET_ME_SEPARATION_V1
```

Promotion requires direct whisper.cpp, protected-local, Target-Me, remote-forbidden, opening,
chronology, double-talk, no-speech, runtime, notes-evidence and guarded-export gates. Post-ASR
cleanup receives zero credit. Missing evidence, stale hashes, incompatible mode or inference
failure returns the byte-exact v2 fallback.

## Frozen v1 Result

The final v1 decision is
`DO_NOT_PROMOTE_REFERENCE_CONDITIONED_TARGET_ME_SEPARATION_V1`, fingerprint
`e3c925f005f0e85e7dc22e555e2a25701a1b297372babcc3551339da861324a3`. Two deterministic
train/dev attempts passed seven of nine gates; the best reached `11.470 dB` Target-Me SNR and
`7.788 dB` echo SNR against locked `12/8 dB` gates. The frozen train split also contained zero
independently supervised non-target local-speech rows and one fixed enrollment vector. Therefore
three-way speaker attribution was not identifiable. Hard-test and sealed corpus access remained
denied, and production v2 stayed byte-exact.
