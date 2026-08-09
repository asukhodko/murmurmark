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
residual pack and freezes 33 primary items, 8 hidden repeats and 41 blind review slots. Its completed
`DIRECT_TRUTH_SEED_READY` result contains 8 attributed, 11 unknown, 4 mixed and 10 unusable primary
outcomes with 7/8 repeat consistency. It enables bounded candidate adjudication but cannot be
reinterpreted as broad speaker correctness or production promotion.

Remote Speaker Direct-Truth Candidate Adjudication v1 is the twenty-fifth source. Its one-shot
`KEEP_COVERAGE_V3` result records three correct gains, two lost correct controls and fail-closed
unsafe acceptance growth from 8 to 13. It keeps the failed candidate and future development work
separate from the production speaker profile.

Enrollment Purity and Abstention Hardening v2 is the twenty-sixth source. It restores control-level
fail-closed safety but qualifies only 7/14 profiles and preserves 0/3 confirmed gains.

Session-Local Homogeneous Remote Speaker Enrollment Mining v1 is the twenty-seventh source. Its
frozen dual-model pack selects 39 windows for 9/14 profiles, but preserves 0/3 gains, loses three
correct controls and introduces four false identities. `KEEP_EXISTING_ENROLLMENT` is a complete
negative result; the next experiment must remove Coverage labels from clustering itself.

Session-Local Remote Speaker Re-Clustering Feasibility v1 is the twenty-eighth source. It freezes
347 label-independent ECAPA/WavLM windows before Coverage assignments and direct truth. Its
`EMBEDDING_GEOMETRY_BOUND` result records minimum model agreement ARI `0.090170`, minimum stability
ARI `0.465715`, `0/3` preserved gains and three lost controls. This closes the current
ECAPA/WavLM re-clustering route without changing Coverage v3.

Stronger Local Remote Speaker Representation Qualification v1 is the twenty-ninth source. It freezes
the materially independent WeSpeaker ResNet34-LM model, official fbank preprocessing, 347 embeddings
and fixed-K candidate pack before labels/direct truth. `KEEP_EXPLICIT_UNKNOWN` preserves `3/3`
confirmed gains but records 12 new false identities, 17 unsafe accepts and six ambiguous clusters.
Fixed-window embedding routes remain audit-only; the next bounded class is temporal/end-to-end
remote diarization.

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
