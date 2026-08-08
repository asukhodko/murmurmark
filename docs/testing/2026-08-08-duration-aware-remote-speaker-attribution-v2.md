# Duration-Aware Remote Speaker Attribution v2 Result

## Decision

`DO_NOT_PROMOTE_TOPOLOGY`.

## Experiment

- Hard-v2 was frozen before candidate code saw its truth.
- It contains 4 scenarios, 125 words, 4 enrolled voices, 2 unseen open-set voices, 28 evaluated
  speaker boundaries, 8 mixed words, and separate enrollment scripts/audio.
- All source stems reconstruct their canonical mixture exactly.
- Truth Lab v1 supplied development evidence only.
- Three predeclared topologies were compared. Conservative WavLM/Resemblyzer fusion won development.
- Hard-v2 was opened once after candidate freeze. Replay is deterministic.

## Metrics

| Track | B-cubed F1 | Pairwise precision | Known recall | Boundary recall | Open-set false |
|---|---:|---:|---:|---:|---:|
| Selected fusion, development | 0.912728 | 1.000000 | 0.913043 | 0.846154 | 0 |
| Selected fusion, hard-v2 | 0.499381 | 1.000000 | 0.551402 | 0.321429 | 0 |
| Coverage v3 control, hard-v2 | 0.389824 | 1.000000 | 0.439252 | 0.214286 | 0 |

Fusion improved every measured coverage metric over the control and preserved perfect measured
precision, but remained far below the required recall and boundary gates.

## Conclusion

The remaining failure is representation granularity. Independent embeddings of short words are too
unstable, even when two backends must agree. The next experiment should identify longer
speaker-homogeneous spans and change points, then project a conservative anonymous ID onto words.
Coverage v3 remains the selected production topology.
