# Disjoint Remote Speaker Model Qualification v1

## Purpose

This contract qualifies one materially new local speaker representation against independent,
human-reviewed real-session truth. It cannot change Coverage v3, selected transcripts, raw audio,
ASR or Echo Guard.

## Frozen Candidate

- backend: 3D-Speaker ERes2NetV2 common;
- model: `iic/speech_eres2netv2_sv_zh-cn_16k-common@c0df10ae7e0dec76f922b2cd2dcef25f92225f09`;
- source: 3D-Speaker at `065629c313eaf1a01c65c640c46d77e61e9607b4`;
- checkpoint SHA-256: `0eb4057106b2573dd7b132cf0c36273ab29afd192c1610f80baa9c556dbb963c`;
- license: Apache-2.0;
- runtime: local PyTorch CPU on Apple Silicon, offline, `nice=20`;
- input: 16 kHz mono, 80-bin Kaldi fbank, zero dither and utterance-mean normalization;
- decision rule: same-session exemplar centroids plus full-clip and 1.6-second subwindow consensus.

Thresholds are selected only on the scripted controlled dev split. Truth v1, controlled hard and
Disjoint Truth v2 cannot tune the candidate.

## Protocol

1. `preflight` verifies model, runtime and frozen input hashes while item-level truth stays sealed.
2. `prepare` computes all embeddings twice and requires byte-identical output.
3. `freeze` pins code, policy, model, preprocessing, thresholds and every candidate prediction.
4. `evaluate` writes an unseal marker and reads Disjoint Truth v2 exactly once.
5. `replay` reconstructs private evaluation rows and the public aggregate report byte-for-byte.
6. `finalize` emits the artifact manifest.

Silent or unreadable clips fail open to `unknown_speaker`. No prediction may force an identity not
present in the frozen session-local choices.

## Outputs

Generated artifacts live under
`sessions/_reports/disjoint-remote-speaker-model-qualification-v1/`:

- `candidate_pack.public.json`;
- `freeze_manifest.json`;
- `disjoint_remote_speaker_model_qualification_report.json` and `.md`;
- `replay_report.json`;
- `artifact_manifest.json`;
- private embeddings, candidate predictions and item evaluation under `private/`.

Schemas:

- `murmurmark.disjoint_remote_speaker_model_candidate_pack/v1`;
- `murmurmark.disjoint_remote_speaker_model_freeze/v1`;
- `murmurmark.disjoint_remote_speaker_model_evaluation/v1`;
- `murmurmark.disjoint_remote_speaker_model_qualification_report/v1`;
- `murmurmark.disjoint_remote_speaker_model_replay/v1`.

## Terminal Decision

Allowed outcomes are `PROMOTE_SHADOW`, `KEEP_COVERAGE_V3` and `MODEL_UNAVAILABLE`.

The completed one-shot result is `KEEP_COVERAGE_V3`. ERes2NetV2 correctly attributed 12 of 21
positive items and made no wrong-known-speaker substitutions, but assigned identities to seven
`unknown/unusable` items. Attributed precision was `0.631579`, below the frozen `1.0` gate. It also
lost two truth-v1 correct controls and raised truth-v1 unsafe special accepts to 12. The candidate
therefore cannot open a shadow profile.

## Invariants

- 72 primary and 12 hidden-repeat Truth v2 slots are conserved;
- repeat determinism is `1.0`;
- all 71 controlled-hard words and timestamps are conserved;
- public artifacts contain no speech, human names, private labels or absolute paths;
- Coverage v3 and all 355 production guards remain authoritative;
- post-unseal tuning and production promotion are forbidden.
