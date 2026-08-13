# Disjoint Remote Speaker Model Qualification v1 Runbook

This experiment is complete. The commands remain for verification and exact replay; do not rerun
`prepare` to seek better metrics after truth was opened.

## Model Setup

Install the pinned Apache-2.0 model once:

```bash
.venv/bin/python scripts/setup-disjoint-remote-speaker-model-v1.py download
```

Verify an existing offline installation:

```bash
.venv/bin/python scripts/setup-disjoint-remote-speaker-model-v1.py verify
```

The default location is
`~/.local/share/murmurmark/models/disjoint-remote-speaker-model-qualification-v1/eres2netv2-common`.

## Frozen Flow

The completed run used the explicit freeze boundary:

```bash
murmurmark corpus remote-model-disjoint-v1 preflight
murmurmark corpus remote-model-disjoint-v1 prepare
murmurmark corpus remote-model-disjoint-v1 freeze
murmurmark corpus remote-model-disjoint-v1 evaluate
murmurmark corpus remote-model-disjoint-v1 replay
murmurmark corpus remote-model-disjoint-v1 finalize
murmurmark corpus remote-model-disjoint-v1 status
```

Expected status:

```text
decision: KEEP_COVERAGE_V3
candidate: 3dspeaker_eres2netv2_common
truth_v2_correct: 12/21
truth_v2_precision: 0.631579
truth_v2_recall: 0.571429
next: keep_coverage_v3_and_close_eres2netv2_route
```

## Interpretation

The model is strong on clean scripted speech: controlled hard B-cubed F1, pairwise precision,
known-speaker recall and boundary recall are all `1.0`, with zero open-set false attribution.
Real-session evidence is different. Seven special items received unsafe identities and two
previously correct truth-v1 controls were lost. This gap points to clip usability, purity and
single-speaker evidence before identity assignment.

Do not lower similarity or margin thresholds and do not add ERes2NetV2 to transcript selection.
The next bounded route is an error decomposition of the frozen unsafe accepts and misses, followed
by a separately frozen usability/single-speaker gate only if observable evidence supports it.

## Verification

```bash
.venv/bin/python scripts/check-disjoint-remote-speaker-model-qualification-v1.py
murmurmark corpus remote-model-disjoint-v1 replay
murmurmark corpus remote-model-disjoint-v1 status
```
