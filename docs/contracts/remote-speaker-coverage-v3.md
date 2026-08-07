# Remote Speaker Coverage v3 Contract

Status: `PROMOTE` for the optional session-local speaker-resolved read surface

Remote Speaker Coverage v3 is an isolated postprocessor over promoted Remote Speaker Diarization
v2. It may assign selected v2 `unknown` words to an existing anonymous session-local speaker. It may
not change v2 labels, create speakers, rewrite text, move timestamps, alter `Me` or affect the plain
aggregate transcript.

## Inputs

The profile requires fingerprinted v2 artifacts from the same selected dialogue and remote audio:

- promoted v2 report and artifact manifest;
- v2 frame, word and utterance attribution;
- v2 speaker map and rich transcript;
- selected dialogue, authoritative remote audio and raw v1 evidence referenced by the v2 manifest;
- promoted policy `policies/remote-speaker-coverage-v3.json` for publication.

Missing, stale or incompatible evidence returns the exact v2 fallback. V3 performs no model download
and no new embedding inference.

## Decision Rule

The profile `resemblyzer_seeded_frame_recovery_v3` considers only v2 `unknown` words. A word is
assigned when all of these conditions hold:

- the word is not protected by `possible_remote_overlap`;
- a related rejected v2 frame has cosine similarity `>= 0.66` and nearest-speaker margin `>= 0.008`;
- every supporting frame agrees on one speaker already seeded by v2;
- no accepted v2 frame conflicts with that speaker.

Otherwise the word remains explicit `unknown` with one cause: `embedding_unavailable`,
`similarity_below_threshold`, `margin_below_threshold`, `conflicting_frame_speakers` or
`protected_remote_overlap`.

## Outputs

Artifacts live under:

```text
derived/audit/remote-speaker-coverage-v3/
  artifact_manifest.json
  recovery_decisions.jsonl
  unknown_cause_map.json
  word_attribution.jsonl
  utterance_attribution.jsonl
  speaker_map.json
  transcript.rich.shadow.json
  transcript.rich.shadow.md
  report.json
  report.md
```

Schemas:

- `murmurmark.remote_speaker_coverage_report/v3`;
- `murmurmark.remote_speaker_coverage_decision/v3`;
- `murmurmark.remote_speaker_unknown_cause_map/v3`;
- `murmurmark.remote_speaker_coverage_artifact_manifest/v3`;
- `murmurmark.remote_speaker_coverage_corpus_report/v3`.

## Promotion Gates

The frozen six-session corpus requires:

- unknown words and seconds each reduced by at least `25%` from v2;
- attributed-only B-cubed F1 and pairwise precision each `>= 0.95`;
- every existing v2 speaker label unchanged;
- exact selected-word, text, timestamp, role, overlap and raw-audio conservation;
- all 1x1, group, internal-boundary and fallback controls passing;
- deterministic offline replay and an exact frozen manifest match.

The promoted result recovered 368 words and `199.533s`, reducing unknown words by `30.1887%` and
unknown seconds by `25.0113%`. Attributable remote speech reached `93.9312%`; B-cubed F1 is
`0.962171` and pairwise precision is `0.961675`. The remaining 851 words / `598.240s` stay unknown.

## Safety Boundary

- no voice-derived human names or cross-session identity;
- no capture, Echo Guard, ASR, selected-dialogue, export or retention change;
- no forced label from similarity alone or for coverage targets;
- no local mic multi-speaker inference;
- v2 remains the exact fallback when policy, implementation or inputs do not match.
