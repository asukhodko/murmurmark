# Transcript Integrity v1 Contract

`transcript_integrity_v1` is a derived post-ASR profile for repairing narrowly proven transcript
duplication and decoder repetition. It does not change capture, preprocessing, ASR, roles,
timestamps or speaker evidence.

## Inputs And Outputs

The stage reads one complete resolved profile selected by `--input-profile` and freezes SHA-256 for
all five inputs:

```text
clean_dialogue.<profile>.json
quality_report.<profile>.json
overlaps.<profile>.json
transcript.simple.<profile>.json
transcript.<profile>.md
```

It writes an isolated profile under `derived/transcript-simple/whisper-cpp/resolved/`:

```text
clean_dialogue.transcript_integrity_v1.json
quality_report.transcript_integrity_v1.json
overlaps.transcript_integrity_v1.json
transcript.simple.transcript_integrity_v1.json
transcript.transcript_integrity_v1.md
```

Audit artifacts live under `derived/transcript-simple/whisper-cpp/text-integrity/`:

```text
transcript_integrity_candidates.transcript_integrity_v1.jsonl
transcript_integrity_patches.transcript_integrity_v1.jsonl
transcript_integrity_review.transcript_integrity_v1.jsonl
transcript_integrity_report.transcript_integrity_v1.json
judge-cache/*.json
```

The report schema is `murmurmark.transcript_integrity_report/v1`. Candidate, patch and review rows
use `murmurmark.transcript_integrity_candidate/v1`, `murmurmark.transcript_integrity_patch/v1` and
`murmurmark.transcript_integrity_review/v1`.

## Repair Boundary

The stage detects:

- an adjacent utterance contained in a same-role temporal duplicate;
- a fuzzy duplicated suffix at a same-role boundary;
- an exact repeated prefix shared by contiguous same-role utterances;
- an exact repeated block inside one utterance;
- a short decoder loop repeated at least four times;
- an adjacent exact repeat that still needs independent evidence.

Temporal containment and exact boundary overlap may be repaired deterministically. Internal repeats,
decoder loops and adjacent exact repeats require matching local `faster-whisper large-v3` evidence.
The judge can remove a whole unsupported loop, remove one proven adjacent duplicate or replace text
with one independently supported copy. Audio similarity alone is never sufficient.

Ambiguous repeats, unavailable models, stale judge cache and conflicting ASR stay in
`transcript_integrity_review.*.jsonl`. Intentional repeats therefore fail open instead of being
silently removed.

## Invariants

- raw CAF is hashed before and after the run and is never written;
- retained utterance IDs, roles, source tracks, timestamps and speaker evidence are preserved;
- a dropped utterance or changed text has one explicit patch with evidence and reason;
- source and output schemas remain compatible with the selected transcript pipeline;
- rerunning from identical inputs and judge cache is byte-stable;
- previous profiles remain unchanged and are always valid fallback inputs.

## Promotion And Selection

`policies/transcript-integrity-v1.json` binds promotion to the exact SHA-256 of
`scripts/apply-transcript-integrity.py`. Automatic selection additionally requires:

1. `PROMOTE` in the tracked policy;
2. current hashes for every recorded input and output;
3. passing per-session invariants;
4. an input profile equal to the best currently selected base profile;
5. no newer current `reviewed_v1` or `agent_reviewed_v1` profile built from the integrity result.

If any check fails, synthesis and readiness use the previous selected profile. The profile is
applied before the speaker-resolved selector, so anonymous speaker evidence is rebuilt over the
repaired aggregate text rather than mutated by this stage.

## Corpus Qualification

`scripts/report-transcript-integrity-corpus.py` verifies current input, output and raw fingerprints,
all safety gates, a minimum session count and material repairs. Its public report uses anonymous
session slots and contains no meeting text, session identifiers or absolute paths.

The first qualification used three sessions: 19 candidates, 10 safe repairs and nine explicit
review cases. It passed all gates and produced `PROMOTE`.
