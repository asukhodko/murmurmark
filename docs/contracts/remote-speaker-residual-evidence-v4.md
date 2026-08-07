# Remote Speaker Residual Evidence v4 Contract

Updated: 2026-08-07

## Purpose

V4 measures how much of the explicit `unknown` queue left by promoted Remote Speaker Coverage v3
can be assigned safely with another independent pass over authoritative remote audio. It is an
isolated evidence profile. It never changes the ordinary transcript or the promoted v3 fallback.

The frozen six-session result is `DO_NOT_PROMOTE`: v4 recovered 124 words / `83.640s`, reducing the
v3 residual by `14.5711%` of words and `13.9811%` of seconds. Both promotion thresholds were `20%`.
All precision, conservation, boundary and raw-audio gates passed.

## Inputs

```text
derived/audit/remote-speaker-coverage-v3/
  report.json
  artifact_manifest.json
  recovery_decisions.jsonl
  unknown_cause_map.json
  word_attribution.jsonl
  utterance_attribution.jsonl
  speaker_map.json
  transcript.rich.shadow.json
  transcript.rich.shadow.md
```

The v3 policy must be promoted and all source and artifact fingerprints must be current. Enrollment
comes from conservative v1 attributed remote utterances. The implementation uses the already local
Resemblyzer model; it performs no model download and no cross-session identity matching.

## Evidence Rule

For each published anonymous speaker, enrollment samples are split deterministically into even and
odd sets. Both split centroids and the full centroid must be stable and agree. A residual unit is
evaluated on bounded exact, compact, context and, where available, half windows.

Attribution requires:

- the original v3 similarity threshold `0.66` and margin `0.008`;
- agreement of full, split A and split B enrollment;
- one speaker across at least two accepted windows, or an exact window with similarity `>=0.72`,
  margin `>=0.02` and duration `>=1.25s`;
- agreement with a supported boundary anchor, if one exists.

`conflicting_frame_speakers` and `protected_remote_overlap` are never assigned by v4. Missing model,
incomplete split enrollment, stale lineage or inference failure yields exact `FALLBACK_V3`.

## Outputs

```text
derived/audit/remote-speaker-residual-evidence-v4/
  residual_units.jsonl
  residual_decisions.jsonl
  split_enrollment.json
  cause_ceiling.json
  word_attribution.jsonl
  utterance_attribution.jsonl
  speaker_map.json
  transcript.rich.shadow.json
  transcript.rich.shadow.md
  report.json
  report.md
  artifact_manifest.json
```

Schemas:

- `murmurmark.remote_speaker_residual_evidence_report/v4`;
- `murmurmark.remote_speaker_residual_unit/v4`;
- `murmurmark.remote_speaker_residual_decision/v4`;
- `murmurmark.remote_speaker_split_enrollment/v4`;
- `murmurmark.remote_speaker_residual_cause_map/v4`;
- `murmurmark.remote_speaker_residual_artifact_manifest/v4`.

The corpus report is `murmurmark.remote_speaker_residual_corpus_report/v4`; its frozen lineage is
`murmurmark.remote_speaker_residual_frozen_manifest/v4`.

## Corpus Decision

Promotion requires both unknown-word and unknown-second reduction `>=20%`, attributed-only B-cubed
F1 and pairwise precision `>=0.95`, exact existing labels, words and timestamps, all 1x1/group and
five boundary controls, deterministic replay and exact raw/source preservation.

The measured ceiling is:

| Cause | Recovered | Remaining |
|---|---:|---:|
| `margin_below_threshold` | 122 words / `48.495s` | 156 words / `62.387s` |
| `similarity_below_threshold` | 2 words / `35.145s` | 111 words / `155.936s` |
| `embedding_unavailable` | 0 words / `0s` | 211 words / `130.804s` |
| `conflicting_frame_speakers` | 0 words / `0s` | 233 words / `131.497s` |
| `protected_remote_overlap` | 0 words / `0s` | 16 words / `33.536s` |

The profile therefore remains audit-only. The supported speaker-resolved source stays promoted v3;
the exact aggregate `Colleagues` transcript remains the final fallback.
