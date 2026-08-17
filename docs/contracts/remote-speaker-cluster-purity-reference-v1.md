# Remote Speaker Cluster Purity Reference v1

## Purpose

This audit compares the selected session-local remote speaker clusters with a private timestamped
reference transcript. It answers a bounded question: whether one published acoustic cluster mostly
contains one reference voice, and whether rare reference voices remain separated.

It does not prove a person's identity. An `independent_machine` source is diagnostic evidence only.
Speaker names and reference text stay under `sessions/_reports/` and are never written to tracked
artifacts.

## Command Surface

```bash
murmurmark corpus remote-cluster-purity-v1 import SESSION SOURCE \
  --source-id REFERENCE_ID \
  --trust-grade independent_machine \
  --local-speaker "LOCAL NAME"

murmurmark corpus remote-cluster-purity-v1 evaluate \
  --write-manifest docs/testing/remote-speaker-cluster-purity-reference-v1-manifest.json
murmurmark corpus remote-cluster-purity-v1 status
murmurmark corpus remote-cluster-purity-v1 replay
```

## Frozen Inputs

Each imported source records its SHA-256, byte count, trust grade, selected profile and selected
rich-transcript SHA-256. Evaluation also fingerprints the selection, rich transcript, Coverage v3
report, selected dialogue and exact aggregate transcript. Missing or changed inputs fail closed with
exit code `2`; no transcript is modified.

## Outputs

Private outputs live under:

```text
sessions/_reports/remote-speaker-cluster-purity-reference-v1/private/
  registry.json
  sources/<source-id>/source.txt
  sources/<source-id>/parsed.json
  evaluations/<source-id>/evaluation.json
  evaluations/<source-id>/item_alignment.jsonl
```

The privacy-safe aggregate surface is:

```text
sessions/_reports/remote-speaker-cluster-purity-reference-v1/
  report.json
  report.md
  reference_manifest.json
```

For a compatible session the audit also writes:

```text
derived/audit/remote-speaker-cluster-purity-reference-v1/summary.json
```

The schemas are:

- `murmurmark.remote_speaker_cluster_purity_registry/v1`;
- `murmurmark.remote_speaker_cluster_purity_private_evaluation/v1`;
- `murmurmark.remote_speaker_cluster_purity_session_summary/v1`;
- `murmurmark.remote_speaker_cluster_purity_reference_report/v1`;
- `murmurmark.remote_speaker_cluster_purity_reference_manifest/v1`.

## Metrics

- `alignment_ratio`: reference words aligned to selected local turns;
- `dominant_cluster_weighted_purity`: word-weighted share belonging to each cluster's dominant
  reference voice;
- `dominant_cluster_collisions`: clusters dominated by more than one sufficiently represented
  reference voice;
- `merged_reference_speakers`: reference voices sharing such clusters;
- `split_reference_speakers`: voices substantially distributed across multiple clusters;
- `minority_speaker_recall`: rare-speaker words assigned to a cluster dominated by that speaker;
- `explicit_unknown_reference_words`: aligned words left explicitly unattributed.

## Terminal Routes

- `ADVANCE_SEGMENTATION`: topology or cluster collisions show that interval boundaries and rare
  voices must be improved before another identity model.
- `ADVANCE_USABILITY_GATE`: topology is adequate, but cluster purity is below the diagnostic gate.
- `EVIDENCE_BOUND`: alignment is too weak or the source does not justify another algorithmic step.

The route is advisory. Coverage v3, selected transcript artifacts and production thresholds remain
unchanged.

## User Handoff

The default transcript may expose session-local acoustic cluster IDs. They are not names or stable
cross-session identities. When diagnostic purity evidence exists, `status` and `transcript` say so.
The exact role-only fallback is always available:

```bash
murmurmark transcript SESSION --aggregate --cat
```
