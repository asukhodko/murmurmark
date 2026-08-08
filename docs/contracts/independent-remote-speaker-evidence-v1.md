# Independent Remote Speaker Evidence v1 Contract

Updated: 2026-08-08

## Purpose

Independent Remote Speaker Evidence v1 tests whether a genuinely different local voice model can
safely reduce the explicit remote-speaker `unknown` residue left by promoted Coverage v3. It is an
isolated audit profile. It does not change the selected transcript, Coverage v3 or aggregate
`Colleagues` fallback.

The frozen result is `DO_NOT_PROMOTE`. WavLM recovered 53 of 851 residual words and `23.357s` of
`598.240s`: `6.2280%` by words and `3.9043%` by seconds, below both `20%` gates. None of the five
new decisions in the existing reference session had direct reference coverage.

## Pinned Backend

- model: `microsoft/wavlm-base-plus-sv`, WavLM XVector;
- local path: `~/.local/share/murmurmark/models/target-me/wavlm-base-plus-sv`;
- license: CC-BY-SA-3.0; MurmurMark does not redistribute the model;
- runtime: CPU, offline, at most four Torch threads;
- model files, runtime versions and SHA-256 values are pinned in
  `policies/independent-remote-speaker-evidence-v1.json`.

Implicit download is forbidden. Missing or changed model files, stale v3 inputs, incomplete
enrollment or inference failure produces exact `FALLBACK_V3`.

## Evidence Rule

Enrollment uses only already attributed Coverage v3 remote speech. For each anonymous speaker,
`sha256(utterance_id) modulo 4` reserves bucket 3 for a test split and the other buckets for two
independent enrollment halves. Accepted test precision must be `1.0`.

Only bounded windows around v3 unknown words are evaluated. Attribution requires WavLM similarity
`>=0.90`, margin `>=0.04` and agreement across at least two windows. A strict exact-window path uses
similarity `>=0.94` and margin `>=0.08`. `conflicting_frame_speakers` and
`protected_remote_overlap` remain unknown.

## Outputs

```text
derived/audit/independent-remote-speaker-evidence-v1/
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

Schemas use the `murmurmark.independent_remote_speaker_*/v1` family. The corpus report schema is
`murmurmark.independent_remote_speaker_corpus_report/v1`; its frozen lineage uses
`murmurmark.independent_remote_speaker_frozen_manifest/v1`.

## Corpus Gates

Promotion requires six frozen sessions, `>=20%` recovery by both words and seconds, B-cubed F1
`>=0.962171`, pairwise precision `>=0.961675`, at least 20 directly referenced recovered words at
precision `>=0.98`, 5/5 internal boundaries and exact preservation of words, timestamps, roles,
`Me`, v2/v3 labels, raw audio and aggregate fallback.

The model passed conservation and precision gates but failed recovery and direct-reference gates.
Coverage v3 therefore remains the supported source. Repeating WavLM with looser thresholds is not a
valid continuation.
