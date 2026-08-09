# Remote Speaker Enrollment Purity and Abstention Hardening v2

## Purpose

This development-only stage tests whether the existing session-local ECAPA enrollment can support
a monotonic additive speaker candidate. Every accepted Coverage v3 identity remains byte-exact;
the candidate may only fill existing abstentions.

## Frozen Inputs And Policy

The policy freezes 14 enrollment sources, 65 direct-truth review artifacts and 355 production
guards by SHA-256. Direct Truth Seed v1 is development evidence, not held-out promotion evidence.
The evaluator must run offline and may not read target text, infer names, link voices across
sessions, search thresholds or alter source artifacts.

Each six-second enrollment clip is split into three two-second windows. A profile qualifies only
when a mutually similar medoid core has at least four voiced windows from both source exemplars,
passes pairwise purity and stays separated from session-local impostor profiles. A residual item
also needs duration, speech-state, similarity, margin, context and original-candidate agreement.

## Outputs

Private, ignored evidence:

```text
sessions/_reports/remote-speaker-enrollment-purity-abstention-hardening-v2/private/
  embedding_requests.jsonl
  embedding_results.jsonl
  evaluation_core.json
  item_decisions.jsonl
  purity_profiles.json
```

Portable outputs:

```text
sessions/_reports/remote-speaker-enrollment-purity-abstention-hardening-v2/
  input_manifest.public.json
  remote_speaker_enrollment_purity_abstention_report.json
  remote_speaker_enrollment_purity_abstention_report.md
  replay_report.json
```

The tracked aggregate manifest is
`docs/testing/remote-speaker-enrollment-purity-abstention-hardening-v2-manifest.json`.

## Terminal Outcomes

- `CANDIDATE_READY_FOR_DISJOINT_TRUTH_V2`: development gates pass; only a new disjoint blind corpus
  may qualify promotion.
- `KEEP_COVERAGE_V3`: evidence is valid but the candidate does not provide material safe gains.
- `EVIDENCE_BOUND`: provenance, model, purity or deterministic replay cannot be established.

The frozen result is `KEEP_COVERAGE_V3`. Seven of fourteen profiles qualified, but the strict
candidate made zero additions and preserved zero of three confirmed v1 gains. It reduced v1 unsafe
accepts from 13 to the control value 8 by abstaining, while preserving all 68 Coverage v3 accepts.

## Safety

No raw audio, transcript, timestamp, ASR, Echo Guard, Coverage v3 decision or production profile is
changed. Missing artifacts and model failures abstain. Public outputs contain no speech text,
session IDs, names, reviewer identity, embeddings or absolute paths.
