# ECAPA Remote Speaker Shadow Qualification v1

Status: completed with `DO_NOT_PROMOTE_REAL_IDENTITY`
Version: `1`

## Purpose

This contract tests the frozen lab-qualified ECAPA backend on real residual remote-speaker intervals.
It is a shadow qualification only. Remote Speaker Coverage v3 and the selected transcript remain
authoritative.

## Frozen Scope

- 6 real sessions;
- 278 residual items covering all 851 Coverage v3 unknown words;
- 598.239509 residual seconds, of which 597.799509 are attached to word intervals;
- 28 session-local enrollment exemplars for 14 anonymous speakers;
- ECAPA model revision `0f99f2d0ebe89ac095bcc5903c4dd8f72b367286`;
- cosine similarity `0.50`, margin `0.30`, CPU, offline, `nice=20`.

The private input manifest freezes policy, source artifacts, every clip and exemplar, Coverage v3
word files, selected-dialogue guards and inherited raw hashes before inference.

## Truth Grades

- `human_reviewed`: direct truth and the only grade eligible for production promotion;
- `structural_one_to_one`: session topology evidence for two 1x1 meetings;
- `independent_machine_reference`: an independently produced, utterance-level reference for one
  group meeting;
- `anonymous_machine_baseline`: diagnostic evidence only.

Machine agreement and voice similarity never become truth. The independent machine reference is
coarser than word-level speaker boundaries, so its precision is a conservative diagnostic gate,
not a human correctness estimate.

## Decision Contract

`PROMOTE_REAL_IDENTITY_CANDIDATE` requires every technical gate and all direct-reference gates.
`REFERENCE_INSUFFICIENT` is used only when technical gates pass but direct reviewed truth is too
small. Any failed technical gate yields `DO_NOT_PROMOTE_REAL_IDENTITY`.

Fixed technical gates include:

- exact conservation of all 851 word IDs, text and timestamps;
- no change to existing Coverage v3 labels or chronology;
- at least 20% recovered residual words and 20% recovered residual seconds;
- independent-reference proposal precision at least `0.99`;
- structural 1x1 precision `1.0`;
- zero reviewed false attribution;
- runtime at most 900 seconds and deterministic replay.

Production promotion additionally requires at least 50 human-reviewed proposal words, one reviewed
group session, two reviewed anonymous speakers and 10 reviewed negative/unknown words.

## Artifacts

Tracked:

```text
policies/ecapa-remote-speaker-shadow-qualification-v1.json
docs/testing/ecapa-remote-speaker-shadow-qualification-v1-manifest.json
```

Public generated:

```text
sessions/_reports/ecapa-remote-speaker-shadow-qualification-v1/
  input_manifest.public.json
  ecapa_remote_speaker_shadow_qualification_report.json
  ecapa_remote_speaker_shadow_qualification_report.md
  replay_report.json
```

Private ignored:

```text
sessions/_reports/ecapa-remote-speaker-shadow-qualification-v1/private/
  input_manifest.json
  embedding_request.json
  embeddings.json
  item_shadow_decisions.jsonl
  word_shadow_decisions.jsonl
```

Each private word row preserves the original word, interval, item and audio hash, model revision,
embedding digest, enrollment keys and centroid hashes, scores, margin, baseline/shadow label, truth
grade and decision provenance.

## Safety

- no session artifact is opened for writing;
- no human name is inferred;
- no voice identity is linked across sessions;
- no synthetic identity is transferred to real meetings;
- silent or invalid clips fail open to `unknown`;
- raw CAF, Echo Guard, primary ASR, selected transcripts and Coverage v3 stay unchanged.
