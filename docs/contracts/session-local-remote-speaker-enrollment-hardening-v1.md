# Session-Local Remote Speaker Enrollment Hardening v1

## Purpose

This shadow-only stage tests one predeclared way to build more robust session-local ECAPA speaker
centroids from the frozen 28 enrollment exemplars. It does not alter Coverage v3, transcripts,
item audio, item embeddings, thresholds or production output.

## Frozen Inputs

The policy freezes by SHA-256:

- 278 ECAPA shadow items and 851 words;
- 83 enrollment-axis failures, 231 words and `119.920926s`;
- 28 exemplar embeddings in 14 session-local speaker profiles;
- control decisions, structural evidence and independent machine reference;
- the rejected interval candidate and all inherited production guards.

`freeze` fails if an input or inherited raw/transcript guard is missing or changed.

## Candidate

`contrastive_reliability_weighted_centroid_v1` uses enrollment embeddings only:

1. For each exemplar, compute similarity to the remaining exemplars of the same profile.
2. Compare it with the nearest other profile centroid in the same session.
3. Define reliability as positive cohesion multiplied by positive impostor margin.
4. Double the weight only when the existing `0.50/0.30` leave-one-out gate passes.
5. Normalize positive weights and build one centroid. If all weights are zero, preserve the control
   centroid. Missing profile embeddings produce `unknown`.

Target item embeddings, truth and outcomes are unavailable while centroids are built. Item
classification later reuses the frozen embeddings and thresholds `0.50/0.30`. No parameter search
or post-result adjustment is allowed.

## Outputs

Private, Git-ignored outputs:

```text
sessions/_reports/session-local-remote-speaker-enrollment-hardening-v1/private/
  input_manifest.json
  enrollment_candidate.jsonl
  candidate_centroids.json
  item_comparison.jsonl
```

Public outputs:

```text
sessions/_reports/session-local-remote-speaker-enrollment-hardening-v1/
  input_manifest.public.json
  session_local_remote_speaker_enrollment_hardening_report.json
  session_local_remote_speaker_enrollment_hardening_report.md
  replay_report.json
```

The tracked portable manifest is
`docs/testing/session-local-remote-speaker-enrollment-hardening-v1-manifest.json`.

## Outcomes

- `ADVANCE_HARDENED_ENROLLMENT_SHADOW`: material scope gain with no lost control acceptance,
  changed accepted speaker, precision loss, open-set regression or new reference error.
- `DO_NOT_ADVANCE_ENROLLMENT_HARDENING`: the candidate is valid but fails at least one material or
  safety gate. The same 28 exemplars must not be used for weight retuning.
- `EVIDENCE_BOUND`: frozen input, conservation, provenance or replay cannot be verified.

## Safety

The stage never infers names or links a voice across sessions. Public files contain no speech text,
absolute paths or embeddings. A positive outcome would still require separate qualification before
production use.
