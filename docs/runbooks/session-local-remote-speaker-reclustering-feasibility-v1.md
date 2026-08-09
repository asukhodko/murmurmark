# Session-Local Remote Speaker Re-Clustering Feasibility v1

## Normal Use

The frozen result is already part of the repository evidence. Inspect it without recomputing models:

```bash
murmurmark corpus remote-reclustering-v1 status
murmurmark corpus remote-reclustering-v1 replay
```

Expected decision:

```text
EMBEDDING_GEOMETRY_BOUND
```

## Full Reproduction

Run the phases in order. `freeze` must complete before `evaluate`:

```bash
murmurmark corpus remote-reclustering-v1 preflight
murmurmark corpus remote-reclustering-v1 prepare
murmurmark corpus remote-reclustering-v1 freeze
murmurmark corpus remote-reclustering-v1 evaluate
murmurmark corpus remote-reclustering-v1 replay
murmurmark corpus remote-reclustering-v1 finalize
```

The equivalent one-command reproduction is:

```bash
murmurmark corpus remote-reclustering-v1 all
```

Model workers run locally with `nice=20`. No network access or cloud service is used.

## Interpretation

Read the aggregate report:

```bash
jq '{decision, geometry: .geometry.values, mapping: .mapping.values, direct_truth, next}' \
  sessions/_reports/session-local-remote-speaker-reclustering-feasibility-v1/session_local_remote_speaker_reclustering_report.json
```

`EMBEDDING_GEOMETRY_BOUND` means the two frozen embedding backends do not agree on a stable
session-local partition. Do not try to repair it by tuning mapping thresholds or relabeling the same
clusters. Keep Coverage v3 and explicit `unknown_remote_speaker` as the production behavior.

The next useful experiment must change the independent speaker representation or collect stronger
speaker-homogeneous evidence. It must not reopen this one-shot result with new thresholds.

## Safety Check

```bash
.venv/bin/python scripts/check-session-local-remote-speaker-reclustering-feasibility-v1.py
murmurmark corpus perfection all --verify-existing
```

These checks verify the frozen hash, deterministic replay, absence of speaker-label fields in the
blind pack, preservation of 68 Coverage v3 accepts and all 355 production guards.
