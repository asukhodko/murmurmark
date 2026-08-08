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

Remote Speaker Residual Reference Corpus v1 is the fourteenth frozen source. Its structurally valid
blind pack is accepted as measured evidence even when its scientific decision is
`REFERENCE_INSUFFICIENT`; readiness failures remain an explicit `remote_speaker_turns` blocker and
are never treated as input corruption or correct attribution.

Controlled Remote Speaker Truth Lab v1 is the fifteenth frozen source. Exact synthetic truth is
accepted as topology evidence, while `DO_NOT_ADVANCE` remains a valid scientific decision. The
qualified Coverage v3 control and rejected WavLM candidate are reported separately; neither can
create truth for a real residual proposal.

Duration-Aware Remote Speaker Attribution v2 is the sixteenth frozen source. Its one-shot hard-v2
decision, exact conservation gates and `DO_NOT_PROMOTE_TOPOLOGY` are valid evidence. The scorecard
records that word-level fusion preserves precision but fails known-speaker and boundary recall.

Segment-Context Remote Speaker Attribution v1 is the seventeenth frozen source. Its one-shot hard-v3
decision records that longer homogeneous context still fails boundary, identity and open-set gates.
`DO_NOT_PROMOTE_SEGMENT_CONTEXT` is accepted as a completed scientific result; production remains
unchanged and the next goal must decompose the three error classes before selecting another backend.

Remote Speaker Attribution Error Decomposition v1 is the eighteenth frozen source. Its exact
oracle matrix accounts for 393 words and 64 evaluated boundaries across v1, hard-v2 and hard-v3.
The fixed decision `ADVANCE_STRONGER_SPEAKER_IDENTITY` is accepted as a completed diagnostic result:
identity gain is `0.351382`, versus `0.063882` for segmentation and `0.036364` for overlap/open-set.
No candidate, production label or threshold was changed.

The nineteenth through twenty-third frozen sources qualify the ECAPA identity candidate, reject its
real-session promotion, decompose the residual, and close bounded interval and enrollment changes.
Their completed negative outcomes remain evidence and never weaken Coverage v3.

Remote Speaker Direct Truth Seed v1 is the twenty-fourth frozen source. It preserves the 278-item
residual pack and freezes 33 primary items, 8 hidden repeats and 41 blind review slots. Its
`REFERENCE_INSUFFICIENT` decision is valid measured evidence while answers remain 0/41; it cannot be
reinterpreted as speaker correctness or production promotion.

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
