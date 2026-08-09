# Remote Speaker Direct-Truth Candidate Adjudication v1 Runbook

Use this runbook only with the frozen v1 policy and completed blind truth seed. Do not update hashes
to make a changed source pass.

## Run

```bash
murmurmark corpus remote-truth-adjudication-v1 preflight

murmurmark corpus remote-truth-adjudication-v1 all \
  --write-manifest docs/testing/remote-speaker-direct-truth-candidate-adjudication-v1-manifest.json

murmurmark corpus remote-truth-adjudication-v1 status
murmurmark corpus perfection all --verify-existing
```

Expected frozen decision:

```text
decision: KEEP_COVERAGE_V3
direct identity items: 8
correct identities: control=3 candidate=4
lost correct controls: 2
fail-closed unsafe accepts: control=8 candidate=13
replay verified: True
```

## Interpretation

The candidate found three direct identities that Coverage v3 abstained from. It also removed two
correct control identities and accepted eight `mixed`, `unknown_speaker` or `unusable` newly
accepted rows. Across all primary rows, fail-closed unsafe accepts rose by five. The one-item net
identity gain is not a safe promotion signal.

`KEEP_COVERAGE_V3` closes this exact weighted-centroid candidate. Do not retune its `0.50/0.30`
thresholds on the same truth. A later v2 candidate may use these answers as development evidence,
but requires disjoint held-out truth before any production qualification.

## Failure Handling

- `EVIDENCE_BOUND` with a hash or size mismatch: restore the exact frozen artifact or issue a new
  versioned corpus; never edit the expected hash alone.
- replay mismatch: keep production unchanged and inspect deterministic serialization.
- missing private answers or clips: restore local private evidence; do not reconstruct labels from
  model output.
- a public report containing speech, session IDs or reviewer data is a hard privacy failure.
