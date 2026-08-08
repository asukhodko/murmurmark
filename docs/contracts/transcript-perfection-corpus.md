# Transcript Perfection Corpus v1 Contract

Status: `BASELINE_ESTABLISHED`

Transcript Perfection Corpus v1 is the local convergence scorecard for MurmurMark's transcript
mission. It verifies existing frozen evidence, keeps each source gate intact and ranks measured
residual classes. It does not regenerate ASR, alter a transcript or collapse unlike metrics into a
single quality score.

## Operational Perfection

An operationally perfect result is the correct result supported by available evidence plus explicit
`unknown` where the evidence ends. This definition has two independent axes:

- **correctness:** supported labels, words, roles and ordering are right;
- **coverage:** how much of the meeting has enough evidence for such a result.

Abstention cannot improve correctness. More `unknown`, `needs_review` or `not_measured` may preserve
safety, but it never counts as higher quality. A missing reference is `not_measured`, not `passed`.

## Frozen Input Manifest

The tracked manifest is:

```text
docs/testing/transcript-perfection-corpus-v1-manifest.json
```

Schema: `murmurmark.transcript_perfection_manifest/v1`.

It contains only portable repository paths, byte sizes, SHA-256 values, schemas, dimensions and
evidence levels. It contains no transcript text, speaker names, raw audio or machine-specific
absolute paths. Private references and session artifacts remain ignored under `sessions/`.

Every required input must match its frozen byte count, hash and schema. Source-level promotion and
safety decisions remain mandatory. A scientifically complete `DO_NOT_PROMOTE` source is accepted as
a measured residual only when its own hard failures are empty; it is never reinterpreted as a
successful repair.

## Dimensions

The v1 scorecard always emits these dimensions:

1. `recognized_words`;
2. `chronology`;
3. `me_remote_roles`;
4. `remote_speaker_turns`;
5. `overlap`;
6. `missing_me`;
7. `remote_leakage`;
8. `acoustic_modes`.

Each row reports `status`, `correctness_status`, `coverage_status`, reference level, source IDs,
metrics and linked residual classes. Word conservation means that a downstream layer did not alter
already selected words. Lexical Accuracy Reference Corpus v1 now adds a bounded exact generated
subset: 67 words at WER/CER `0`. Real-meeting correctness remains
`real_meeting_reference_insufficient` until graded human-reviewed 1x1/group and acoustic coverage
exists. Weak machine agreement never counts as correctness.

## Outputs

The command writes only under:

```text
sessions/_reports/transcript-perfection-corpus-v1/
  input_manifest.json
  transcript_perfection_corpus_report.json
  transcript_perfection_corpus_report.md
  residual_ranking.jsonl
```

Schemas:

- `murmurmark.transcript_perfection_corpus_report/v1`;
- `murmurmark.transcript_perfection_residual/v1`.

The report decision is `BASELINE_ESTABLISHED` when all frozen inputs and source contracts pass.
That decision means the measuring instrument is valid. It does not mean the product is release-ready
or perfect. `release.ready` stays independent and lists every remaining blocker.

## Residual Ranking

The deterministic ranking uses severity, affected seconds, evidence strength, repairability and
number of affected sessions. Its formula is frozen in the manifest and copied to the report.

Seconds from different corpus scopes are never added. Evidence gaps such as missing real-meeting
lexical ground truth are listed separately from observed defects and cannot win an actionable repair ranking.
Every selected next goal names its source residual and must replay this scorecard after the change.
`lexical_prerequisite` keeps the required human-reviewed seed visible without presenting work that
cannot be completed autonomously as the global `next_goal`.

## Safety Boundary

- no capture, Echo Guard, ASR, transcript profile, diarization or export mutation;
- no threshold weakening or conversion of `unknown` into a pass;
- no cloud call, implicit model download or external publication;
- no tracked private reference text, human name or raw audio;
- no claim of lexical accuracy from word conservation;
- identical inputs produce byte-identical reports.
