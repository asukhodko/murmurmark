# Remote Speaker Enrollment Purity and Abstention Hardening v2 Runbook

Use only the frozen v2 policy and local ECAPA model. Do not change expected hashes or thresholds to
make the candidate pass.

## Run

```bash
murmurmark corpus remote-enrollment-purity-v2 preflight

murmurmark corpus remote-enrollment-purity-v2 all \
  --write-manifest docs/testing/remote-speaker-enrollment-purity-abstention-hardening-v2-manifest.json

murmurmark corpus remote-enrollment-purity-v2 status
murmurmark corpus perfection all --verify-existing
```

Expected frozen result:

```text
decision: KEEP_COVERAGE_V3
profiles: qualified=7 rejected=7
candidate additions: 0
preserved confirmed v1 gains: 0/3
unsafe accepts: control=8 candidate=8 v1=13
replay verified: True
```

## Interpretation

The strict subwindow core removed the weighted-centroid regressions but also removed every useful
addition. Seven profiles fail because their mutually consistent windows do not span both source
exemplars; one also has weak pairwise similarity. This is evidence that the existing two six-second
exemplars are not a sufficiently pure enrollment substrate.

Do not loosen thresholds on the same direct truth. The next bounded experiment must mine longer,
speaker-homogeneous intervals from the same session and freeze them before reusing development
truth. Disjoint truth is needed only after a candidate first passes these development gates.

## Failure Handling

- Hash or size mismatch: restore the frozen artifact or create a new versioned corpus.
- Missing ECAPA model or failed embedding: keep Coverage v3 and record `EVIDENCE_BOUND`.
- Replay mismatch: inspect serialization; never promote or rewrite source labels.
- Private content in a portable report: treat as a hard privacy failure.
