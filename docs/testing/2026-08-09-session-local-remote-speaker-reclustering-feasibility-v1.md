# Session-Local Remote Speaker Re-Clustering Feasibility v1

Date: 2026-08-09

## Result

Decision: `EMBEDDING_GEOMETRY_BOUND`.

The experiment froze 347 unlabeled four-second remote windows from six sessions before reading
Coverage speaker assignments or the 33 direct-truth answers. ECAPA and WavLM were clustered
independently with the same fixed per-session topology count.

## Geometry

| Metric | Frozen gate | Observed | Pass |
|---|---:|---:|---:|
| Minimum model agreement ARI | 0.70 | 0.090170 | no |
| Minimum model agreement NMI | 0.75 | 0.231989 | no |
| Minimum ECAPA silhouette | 0.15 | 0.178562 | yes |
| Minimum WavLM silhouette | 0.15 | 0.313499 | yes |
| Minimum ECAPA stability ARI | 0.75 | 0.475074 | no |
| Minimum WavLM stability ARI | 0.75 | 0.465715 | no |
| Maximum consensus fragmentation | 1.50 | 1.80 | no |

The failure is structural rather than a cluster-label permutation. On one five-speaker session WavLM
put 58 of 63 windows into one cluster, while ECAPA produced a materially different partition.

## Post-Freeze Direct Truth

- confirmed v1 gains preserved: `0/3`;
- unsafe accepts: `4`;
- new false identities: `4`;
- lost correct controls: `3`;
- embedding-unavailable silent items: `2/33`, safely abstained.

The label-conditioned homogeneous enrollment result and the label-independent result point to the
same practical boundary: current ECAPA/WavLM evidence cannot safely recover the remaining remote
speaker identities. Production remains Coverage v3; 68 accepts, exact words/timestamps, selected
transcripts, raw CAF, primary ASR, Echo Guard and 355 production guards are unchanged.

## Reproduction

```bash
murmurmark corpus remote-reclustering-v1 replay
.venv/bin/python scripts/check-session-local-remote-speaker-reclustering-feasibility-v1.py
```
