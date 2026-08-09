# Stronger Local Remote Speaker Representation Qualification v1

## Purpose

This contract bounds a materially different local speaker representation after the ECAPA/WavLM
re-clustering route reached `EMBEDDING_GEOMETRY_BOUND`. It does not change production transcripts.

## Candidate

- backend: WeSpeaker ResNet34-LM ONNX;
- model: `hbredin/wespeaker-voxceleb-resnet34-LM` at revision
  `0ae88dcaf48cacdf741275d6d1a8101f45eee220`;
- model SHA-256: `7bb2f06e9df17cdf1ef14ee8a15ab08ed28e8d0ef5054ee135741560df2ec068`;
- license: Apache-2.0;
- runtime: local CPU ONNX Runtime with the pinned official 80-bin Kaldi fbank preprocessing;
- independence: ResNet/TSTP and large-margin VoxCeleb training differ from SpeechBrain ECAPA and
  WavLM.

The model is optional and stored outside the repository. Missing or changed model/runtime evidence
must fail open as `EVIDENCE_BOUND` or explicit unknown.

## Frozen Order

1. Verify the previous six-session, 347-window label-independent pack and 28 Transcript Perfection
   sources.
2. Compute WeSpeaker embeddings without text, speaker IDs, human names, Coverage assignments or
   direct truth.
3. Freeze model/runtime/worker provenance, preprocessing, fixed-K clusters, thresholds and the
   candidate pack.
4. Only after freeze, map clusters to Coverage profiles and evaluate all 33 direct-truth items.

Threshold search, post-hoc tuning and production promotion are forbidden.

## Outputs

All generated artifacts live under
`sessions/_reports/stronger-local-remote-speaker-representation-qualification-v1/`:

- `candidate_pack.public.json`;
- `freeze_manifest.json`;
- `stronger_local_remote_speaker_representation_report.json`;
- `stronger_local_remote_speaker_representation_report.md`;
- `replay_report.json`;
- `artifact_manifest.json`;
- private interval, embedding, mapping and direct-truth evidence under `private/`.

The report schema is `murmurmark.stronger_local_remote_speaker_representation_report/v1`.

## Terminal Outcomes

- `STRONGER_REPRESENTATION_READY`: frozen geometry, mapping and direct-truth gates all pass.
- `KEEP_EXPLICIT_UNKNOWN`: the candidate is reproducible but cannot safely reduce unknown speakers.
- `EVIDENCE_BOUND`: model, license, runtime, freeze or provenance is incomplete.

The one-shot result is `KEEP_EXPLICIT_UNKNOWN`. WeSpeaker reached minimum silhouette `0.263291` but
minimum stability ARI fell to `0.442394`. It preserved all `3/3` confirmed gains and lost no correct
controls, yet produced 17 unsafe accepts, including 12 new false identities, and six ambiguous
clusters. The lightweight embedding-plus-fixed-K route is closed without production promotion.

## Invariants

- Coverage v3 retains all 68 accepted assignments;
- all 355 production guards remain unchanged;
- selected transcripts, raw CAF, primary ASR and Echo Guard remain byte-identical;
- direct truth is unavailable before freeze;
- public reports contain no private intervals or human names;
- replay cannot mutate the report.
