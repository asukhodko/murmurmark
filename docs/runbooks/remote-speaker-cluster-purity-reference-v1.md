# Remote Speaker Cluster Purity Reference v1 Runbook

## When To Use It

Use this audit when a timestamped transcript from another recognizer covers the same meeting and
names its speakers. Keep that file private. A machine transcript can reveal merges and splits, but
cannot establish human-reviewed identity truth.

## Import And Evaluate

```bash
SOURCE=/path/to/private-reference.txt
SESSION=sessions/<session-id>

murmurmark corpus remote-cluster-purity-v1 import "$SESSION" "$SOURCE" \
  --source-id private-reference-01 \
  --trust-grade independent_machine \
  --local-speaker "Your local speaker label"

murmurmark corpus remote-cluster-purity-v1 evaluate
murmurmark corpus remote-cluster-purity-v1 status
murmurmark corpus remote-cluster-purity-v1 replay
```

Accepted input blocks have this shape:

```text
0:00 - 0:08
Speaker Name
Recognized text.
```

Use `--offset-sec` only when automatic lexical offset estimation cannot find at least eight stable
anchors. Record why an override was needed.

## Read The Result

Start with `decision`, `alignment_ratio`, `dominant_cluster_weighted_purity`, cluster collisions and
minority-speaker recall. Inspect private item alignments only when the aggregate route is unclear.

For an `independent_machine` source:

- treat `ADVANCE_SEGMENTATION` as a design route, not as proof that every reference name is right;
- keep session-local labels anonymous;
- use the aggregate transcript when speaker distinction would be misleading:

```bash
murmurmark transcript "$SESSION" --aggregate --cat
```

## Reproducibility And Failure

`replay` must be byte-exact. A changed source, rich transcript, selection or policy invalidates the
evaluation. Missing reference evidence leaves ordinary transcript selection usable and unverified;
it never blocks capture, ASR or aggregate handoff.
