# Remote Speaker Shadow Error Decomposition v1

Status: completed with `ADVANCE_INTERVAL_PURIFICATION`
Version: `1`

## Purpose

This contract explains the frozen ECAPA real-session shadow result before another attribution
experiment is allowed. It is diagnostic-only. Remote Speaker Coverage v3, selected transcripts,
raw audio, the primary ASR and Echo Guard remain authoritative and unchanged.

## Frozen Scope

- 278 residual items and all 851 unknown Coverage v3 words;
- 68 accepted ECAPA proposals and 210 abstentions;
- 597.799509 word-attached residual seconds;
- 28 session-local enrollment exemplars for 14 anonymous speaker profiles;
- item and word decisions, embeddings, bounded clips, enrollment, independent machine reference,
  Coverage v3 artifacts, selected transcripts and raw CAF hashes;
- ECAPA similarity `0.50` and margin `0.30`, fixed before this analysis.

The input manifest recursively verifies inherited artifacts. A changed source, clip, exemplar,
selected transcript or raw-audio guard fails closed. No hash is refreshed automatically.

## Measurements

Each private item row records:

- exact interval duration, RMS, frame activity, speech-band support and silence state;
- speaker-bounded context, nearby known speakers, mixed-utterance and frozen-boundary risk;
- original ECAPA score, margin and decision reproduced from frozen embeddings;
- leave-one-out item decision and top-speaker stability for every session-local exemplar;
- reference grade and granularity without treating machine agreement as human truth;
- one primary cause, secondary causes, confidence and complete artifact provenance.

Primary causes are evaluated in a fixed order: insufficient audio, boundary or mixed speech,
enrollment instability, unmapped coarse reference, identity/reference conflict, similarity limit,
margin limit, supported acceptance, unreviewed acceptance, then evidence bound.

## Failure And Decision Contract

The failure scope is every abstention plus every accepted proposal contradicted by the available
independent reference. Accepted proposals without such a contradiction remain visible but do not
inflate the routing denominator.

Technical causes map to three axes:

- `interval_purification`: silence/weak support, boundary contamination and mixed speech;
- `enrollment_hardening`: leave-one-out instability;
- `identity_backend`: similarity or margin limits and mapped identity conflicts.

An axis can advance only when it covers at least 35% of failure items, at least 30% of failure
seconds and exceeds the second axis by at least `0.10` material score. At least 90% of failure items
must have an explicit cause. Otherwise the result is reference acquisition or `EVIDENCE_BOUND`.
Exactly one terminal outcome is emitted:

```text
ADVANCE_INTERVAL_PURIFICATION
ADVANCE_ENROLLMENT_HARDENING
ADVANCE_REFERENCE_ACQUISITION
ADVANCE_IDENTITY_BACKEND
EVIDENCE_BOUND
```

## Artifacts

Tracked:

```text
policies/remote-speaker-shadow-error-decomposition-v1.json
docs/testing/remote-speaker-shadow-error-decomposition-v1-manifest.json
```

Public generated:

```text
sessions/_reports/remote-speaker-shadow-error-decomposition-v1/
  input_manifest.public.json
  remote_speaker_shadow_error_decomposition_report.json
  remote_speaker_shadow_error_decomposition_report.md
  replay_report.json
```

Private ignored:

```text
sessions/_reports/remote-speaker-shadow-error-decomposition-v1/private/
  input_manifest.json
  item_error_decomposition.jsonl
  enrollment_diagnostics.jsonl
  reference_explanations.jsonl
```

Public outputs contain no speech text, human names, absolute paths or embedding values. Session IDs
are replaced by deterministic scenario IDs.

## Safety

- no production profile or transcript is written;
- no threshold is tuned from the result;
- no human name or cross-session voice identity is inferred;
- independent machine reference remains coarse diagnostic evidence;
- missing or silent evidence stays fail-open;
- replay must be byte-identical before the tracked manifest is finalized.
