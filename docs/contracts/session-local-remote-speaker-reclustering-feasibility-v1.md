# Session-Local Remote Speaker Re-Clustering Feasibility v1

## Purpose

This experiment separates two questions that earlier enrollment candidates mixed together:

1. do ECAPA and WavLM form a stable speaker partition inside one meeting;
2. can that partition be mapped safely to the existing anonymous Coverage v3 profiles.

It is an evaluation-only corpus layer. Coverage v3, selected transcripts, raw CAF, primary ASR and
Echo Guard remain unchanged.

## Frozen Policy

`policies/session-local-remote-speaker-reclustering-feasibility-v1.json` fixes:

- six sessions and the already published topology count of `14` profiles;
- 4-second windows, fixed-stride sampling and at most 64 windows per session;
- average-linkage agglomerative clustering with cosine distance and fixed session-local `K`;
- independent ECAPA and WavLM partitions;
- five-fold deterministic stability, ARI/NMI, silhouette and intersection consensus gates;
- post-freeze Hungarian mapping and the existing one-shot direct-truth thresholds;
- no cluster-count search, threshold search, production promotion or cross-session identity.

The topology count is a frozen prior. Window-to-speaker assignments are forbidden during prepare.

## Phases

`preflight` verifies source hashes, local model provenance and 355 production guards.

`prepare` reads only `id`, `role`, `start` and `end` from the selected dialogue. The selection code
does not inspect text, speaker IDs, speaker labels, names or direct truth. It writes embeddings and
two independent partitions.

`freeze` writes `murmurmark.session_local_remote_speaker_reclustering_freeze/v1` and freezes every
window, embedding and assignment before evaluation sources may be opened.

`evaluate` may then read Coverage assignments and 33 direct-truth items. Cluster IDs are aligned to
anonymous session-local profiles only for measurement. The mapping never changes a transcript.

## Schemas And Artifacts

Public artifacts under
`sessions/_reports/session-local-remote-speaker-reclustering-feasibility-v1/`:

- `freeze_manifest.json` — `murmurmark.session_local_remote_speaker_reclustering_freeze/v1`;
- `reclustering_pack.public.json` — `murmurmark.session_local_remote_speaker_reclustering_pack/v1`;
- `session_local_remote_speaker_reclustering_report.json` —
  `murmurmark.session_local_remote_speaker_reclustering_report/v1`;
- `session_local_remote_speaker_reclustering_report.md`;
- `replay_report.json` — `murmurmark.session_local_remote_speaker_reclustering_replay/v1`;
- `artifact_manifest.json` — `murmurmark.session_local_remote_speaker_reclustering_manifest/v1`.

Private artifacts contain session IDs, intervals, embeddings, post-freeze mappings and item-level
direct-truth decisions. Public artifacts contain aliases and aggregate metrics only.

## Terminal Outcomes

- `RECLUSTERING_ROUTE_READY`: geometry and post-freeze direct-truth gates pass.
- `LABEL_MAPPING_BOUND`: geometry passes, but safe cluster-to-profile mapping is not demonstrated.
- `EMBEDDING_GEOMETRY_BOUND`: ECAPA/WavLM partitions are unstable or disagree.
- `EVIDENCE_BOUND`: frozen inputs, provenance or safety invariants cannot be verified.

Every outcome is fail-open for production: Coverage v3 remains selected and the residual remains
explicitly unknown.

## Current Frozen Result

The one-shot result is `EMBEDDING_GEOMETRY_BOUND`: 347 unlabeled windows were frozen before labels;
minimum ECAPA/WavLM ARI was `0.090170`, minimum NMI `0.231989`, minimum stability ARI `0.465715`,
and maximum consensus fragmentation was `1.8`. Post-freeze evaluation preserved `0/3` confirmed
gains and lost three correct controls. The current ECAPA/WavLM re-clustering route is closed.
