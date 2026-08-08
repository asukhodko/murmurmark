# Stronger Remote Speaker Identity Backend Qualification v1

Status: completed with `PROMOTE_LAB_IDENTITY_CANDIDATE`.

## Purpose

This layer checks whether a genuinely different local speaker-verification backend can close the
identity gap measured by Remote Speaker Attribution Error Decomposition v1. It qualifies at most one
laboratory candidate. It cannot change a real transcript, Coverage v3 or production profile
selection.

## Frozen Inputs

The policy is fixed in
`policies/stronger-remote-speaker-identity-backend-qualification-v1.json`. It records:

- the existing WavLM XVector control;
- the independently trained SpeechBrain ECAPA-TDNN candidate, exact model revision, license,
  runtime versions and SHA-256 for every required model file;
- immutable Error Decomposition v1 and Coverage v3 guards;
- Truth Lab v1, once-opened hard-v2 and once-opened hard-v3 as development evidence;
- audio preparation, calibration order, abstention rules and promotion gates;
- the private hard-v4 generator declared before candidate selection.

Development data may select one candidate and its thresholds. It cannot be described as blind after
selection. The disjoint hard-v4 uses new voices, vocabulary, enrollment, scripts, durations,
transitions, overlap and open-set cases. Its opening ledger permits exactly one selected candidate
and one opening.

## Backends

| Backend | Role | Model | Runtime |
|---|---|---|---|
| `wavlm_xvector_control` | frozen control | `microsoft/wavlm-base-plus-sv` | project virtual environment |
| `speechbrain_ecapa_voxceleb_candidate` | candidate | `speechbrain/spkrec-ecapa-voxceleb` at revision `0f99f2d0ebe89ac095bcc5903c4dd8f72b367286` | isolated local Python environment |

Models are downloaded by the operator and are not redistributed by MurmurMark. Qualification runs
offline after setup. A missing model, changed hash or incompatible runtime fails closed.

## Evaluation

Both backends receive the same exact-event partition and clean, disjoint enrollment. Similarity and
margin thresholds are selected only on the development corpora using the fixed policy order. The
hard-v4 evaluation measures:

- exact word and timestamp conservation;
- B-cubed precision, recall and F1;
- pairwise precision and recall;
- known-speaker attribution recall;
- boundary recall on evaluable speaker transitions;
- open-set false attribution;
- fail-closed handling of mixed speech.

`PROMOTE_LAB_IDENTITY_CANDIDATE` requires B-cubed F1 at least `0.85`, pairwise precision at least
`0.99`, known-speaker recall at least `0.80`, zero open-set false attribution, all mixed words marked
safe, boundary no-regression against the control and exact conservation. The alternative terminal
decision is `DO_NOT_PROMOTE_IDENTITY_BACKEND`.

## Outputs

Default root:

```text
sessions/_reports/stronger-remote-speaker-identity-backend-qualification-v1/
  hard_v4_public_manifest.json
  remote_speaker_identity_backend_qualification_report.json
  remote_speaker_identity_backend_qualification_report.md
  replay_report.json
  private/
    candidate_freeze.json
    development/
    hard-v4/
      frozen_manifest.json
      hard_v4_opening_ledger.json
    hard-v4-evaluation/
```

Tracked portable lineage:

```text
docs/testing/stronger-remote-speaker-identity-backend-qualification-v1-manifest.json
```

Schemas:

- `murmurmark.stronger_remote_speaker_identity_backend_qualification_policy/v1`;
- `murmurmark.remote_speaker_identity_hard_v4_frozen_manifest/v1`;
- `murmurmark.remote_speaker_identity_hard_v4_public_manifest/v1`;
- `murmurmark.remote_speaker_identity_candidate_freeze/v1`;
- `murmurmark.remote_speaker_identity_hard_v4_opening_ledger/v1`;
- `murmurmark.remote_speaker_identity_exact_event_prediction/v1`;
- `murmurmark.stronger_remote_speaker_identity_backend_qualification_report/v1`;
- `murmurmark.stronger_remote_speaker_identity_backend_replay/v1`;
- `murmurmark.stronger_remote_speaker_identity_backend_qualification_manifest/v1`.

## Safety Boundary

Synthetic speaker identities never transfer to real sessions. Public artifacts exclude scripts,
renderer voices, audio and absolute paths. A laboratory promotion permits only a separate fail-open
real-session shadow qualification. Production speaker attribution, selected transcripts, primary
ASR, Echo Guard, raw CAF and Coverage v3 remain unchanged.
