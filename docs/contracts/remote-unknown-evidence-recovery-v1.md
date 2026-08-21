# Remote Unknown Evidence Recovery v1

## Purpose

This shadow-only stage asks whether strict `remote_speaker_unknown` words from Remote Speaker
Coverage v3 can be assigned to an existing session-local speaker without weakening abstention.
Coverage v3, the selected transcript and the aggregate `Colleagues` fallback remain authoritative.

## Inputs

- a current, publishable `remote-speaker-coverage-v3` artifact set;
- a fingerprint-matched `independent-remote-speaker-evidence-v1` WavLM artifact set;
- the Coverage v3 source `remote-speaker-evidence-v1/utterance_attribution.jsonl`;
- `policies/remote-unknown-evidence-recovery-v1.json`.

Every source path, size and SHA-256 is recorded. Missing or stale independent evidence produces an
exact Coverage v3 fallback with all unknown words preserved.

## Decision Rule

A Coverage v3 unknown unit can be labelled only when:

1. split-enrollment WavLM accepts one existing session-local speaker;
2. an independent structural family agrees: attributed v1 utterance, nearby same-utterance anchor,
   or two close boundary anchors;
3. no structural family names another speaker;
4. the baseline cause is neither `protected_remote_overlap` nor `conflicting_frame_speakers`.

Neighbourhood alone and one similarity score are insufficient. Existing labels are immutable.

## Outputs

The stage writes an isolated `remote-unknown-evidence-recovery-v1` directory:

```text
recovery_decisions.jsonl
unknown_cause_map.json
word_attribution.jsonl
utterance_attribution.jsonl
speaker_map.json
transcript.rich.shadow.json
transcript.rich.shadow.md
report.json
report.md
artifact_manifest.json
```

`recovery_decisions.jsonl` contains one row for every baseline unknown word, including the Coverage
v3 cause, complete WavLM unit/windows, structural anchors, outcome and reason. Text, word IDs,
roles, timestamps, character offsets and order must remain exact.

## Corpus Qualification

The frozen input is the five strict sessions from Post-Segmentation Transcript Rebaseline v1:
547 words / `397.543570s`. Session `2026-08-21_15-58-36` is an untuned held-out control with
166 words / `286.137303s`. Direct Truth v1 and disjoint Direct Truth v2 provide 105 primary safety
items.

Promotion requires at least 5% word and second recovery, at least five newly recovered direct-truth
items, zero wrong-speaker assignments and zero new fail-closed acceptance. The completed result is
`EVIDENCE_BOUND`: 10 frozen words / `4.682812s` and one held-out word / `4.580652s` passed the local
rule, but no recovered item overlaps direct truth. Coverage v3 therefore remains selected.

## Schemas

- `murmurmark.remote_unknown_evidence_recovery_decision/v1`
- `murmurmark.remote_unknown_evidence_recovery_report/v1`
- `murmurmark.remote_unknown_evidence_recovery_manifest/v1`
- `murmurmark.remote_unknown_evidence_recovery_corpus_report/v1`
- `murmurmark.remote_unknown_evidence_recovery_snapshot/v1`

No cloud inference, cross-session identity, transcript text change or production promotion is part
of this contract.
