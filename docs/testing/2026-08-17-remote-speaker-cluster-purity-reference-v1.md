# Remote Speaker Cluster Purity Reference v1 Result

Date: 2026-08-17

## Result

Terminal route: `ADVANCE_SEGMENTATION`.

A private independent-machine transcript was frozen against one selected group-meeting rich
transcript. The public manifest contains only hashes and aggregate measurements.

| Metric | Result |
|---|---:|
| Reference remote speakers | 10 |
| Published session-local clusters | 4 |
| Reference remote words | 9,131 |
| Aligned reference words | 8,475 |
| Alignment ratio | 92.8157% |
| Attributed aligned words | 8,077 |
| Dominant-cluster weighted purity | 89.8106% |
| Dominant-cluster collisions | 3 |
| Merged reference speakers | 9 |
| Split reference speakers | 3 |
| Minority reference speakers | 6 |
| Minority reference words | 1,062 |
| Minority-speaker recall | 0% |

The reference contains substantially more voices than the selected four-cluster topology. Rare
speakers are absorbed by dominant clusters rather than preserved as separate voices. This is enough
to choose segmentation and minority-voice recovery as the next algorithmic axis.

## Evidence Limit

The source is an external machine transcript, not human-reviewed truth. It can expose structural
disagreement and cluster collisions, but cannot validate personal identity. Production Coverage v3,
its thresholds, selected words, timestamps and all selected transcript artifacts remain unchanged.

## Safety And Reproducibility

- private source text, speaker names, session IDs and item alignments remain below ignored
  `sessions/_reports/`;
- the tracked manifest contains generic evidence IDs and SHA-256 only;
- source mutation makes evaluation fail closed;
- replay is byte-exact;
- the synthetic check covers merges, collisions, minority loss, privacy and fail-open behavior;
- the exact aggregate transcript remains available through `murmurmark transcript --aggregate`.

Tracked evidence: [manifest](remote-speaker-cluster-purity-reference-v1-manifest.json).
