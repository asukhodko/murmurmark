# Documentation Index

Read in this order:

1. [Product vision](product/vision.md)
2. [v1 product requirements](product/prd-v1.md)
3. [Open-source readiness](project/open-source-readiness.md)
4. [CLI MVP Definition of Done](project/cli-mvp-definition-of-done.md)
5. [Current executable goal](project/current-goal.md)
6. [Reliable transcription route](project/reliable-transcription-route.md)
7. [CLI roadmap](roadmap/murmurmark-cli-roadmap.md)
8. [OpsKarta v3 roadmap plan](roadmap/murmurmark-cli-roadmap.plan.yaml)
9. [System overview](architecture/system-overview.md)
10. [Capture architecture](architecture/capture.md)
11. [Experimental sidecar architecture](architecture/experimental-sidecar.md)
12. [Causal recovery generalization](architecture/causal-recovery-generalization.md)
13. [Transcription architecture](architecture/transcription.md)
14. [Echo Guard architecture](architecture/echo-suppression.md)
15. [Evidence and synthesis](architecture/evidence-synthesis.md)
16. [Session package contract](contracts/session-package.md)
17. [Meeting lifecycle contract](contracts/meeting-lifecycle.md)
18. [Experimental sidecar contract](contracts/experimental-sidecar.md)
19. [Transcript and evidence contracts](contracts/transcript-and-evidence.md)
20. [Release bundle contract](contracts/release-bundle.md)
21. [Install and upgrade runbook](runbooks/install-and-upgrade.md)
22. [Retention policy contract](contracts/retention-policy.md)
23. [Privacy and threat model](security/privacy-and-threat-model.md)
24. [First recording runbook](runbooks/first-recording.md)
25. [Meeting cheat sheet](runbooks/meeting-cheatsheet.md)
26. [Echo Guard delay lab](runbooks/echo-guard-lab.md)
27. [Controlled Echo Supervision Lab](runbooks/controlled-echo-supervision-lab.md)
28. [Simple whisper.cpp transcription](runbooks/transcribe-simple-whispercpp.md)
29. [Causal recovery generalization runbook](runbooks/causal-recovery-generalization.md)
30. [Tradeoffs](decisions/tradeoffs.md)
31. [RFC-0001](rfc/0001-v1-scope.md)
32. [ADR directory](adr/)
33. [ADR-0008](adr/0008-use-screencapturekit-for-first-cli-smoke.md)
34. [ADR-0009](adr/0009-derived-echo-suppression-only.md)
35. [ADR-0010](adr/0010-use-preserve-local-fir-for-current-echo-guard.md)
36. [Talk validation log](testing/2026-06-22-talk-validation.md)
37. [Echo Guard Local FIR validation log](testing/2026-06-23-echo-guard-local-fir.md)
38. [Mic remote bleed reduction](backlog/mic-remote-bleed-reduction.md)
39. [Complete echo removal research](research/2026-06-30-complete-echo-removal.md)
40. [Echo Suppression Promotion v1 result](research/2026-07-23-echo-suppression-promotion-v1.md)
41. [Neural Residual Echo Suppression v1 result](research/2026-07-23-neural-residual-echo-v1.md)
42. [Speaker-Preserving Echo Adaptation Corpus v1 result](research/2026-07-23-speaker-preserving-echo-adaptation-corpus-v1.md)
43. [Speaker-Preserving Neural Echo v2 result](research/2026-08-04-speaker-preserving-neural-echo-v2.md)
44. [Reference-Conditioned Target-Me Separation v1](research/2026-08-04-reference-conditioned-target-me-separation-v1.md)
45. [Reference-Conditioned Target-Me Separation contract](contracts/reference-conditioned-target-me-separation.md)
46. [Target-Me Identifiability Corpus v1 result](research/2026-08-04-target-me-identifiability-corpus-v1.md)
47. [Target-Me Identifiability Corpus contract](contracts/target-me-identifiability-corpus.md)
48. [Target-Me Identifiability Corpus runbook](runbooks/target-me-identifiability-corpus.md)
49. [Reference-Conditioned Target-Me Separation v2 result](research/2026-08-05-reference-conditioned-target-me-separation-v2.md)
50. [Reference-Conditioned Target-Me Separation runbook](runbooks/reference-conditioned-target-me-separation.md)
51. [Pre-ASR Residual Echo Ceiling Map contract](contracts/pre-asr-residual-echo-ceiling-map.md)
52. [Pre-ASR Residual Echo Ceiling Map v1 result](research/2026-08-06-pre-asr-residual-echo-ceiling-map-v1.md)
53. [Alignment and Echo-Path Model v3 result](research/2026-08-06-alignment-echo-path-model-v3.md)
54. [Multi-Component Residual Separator v1 result](research/2026-08-06-multi-component-residual-separator-v1.md)
55. [Multi-Component Residual Separator contract](contracts/multi-component-residual-separator.md)
56. [Multi-Component Residual Separator runbook](runbooks/multi-component-residual-separator.md)
57. [Stronger Offline Separator Prerequisites result](research/2026-08-06-stronger-offline-target-speaker-separator-prerequisites-v1.md)
58. [Stronger Offline Separator Prerequisites contract](contracts/stronger-offline-target-speaker-separator-prerequisites.md)
59. [Stronger Offline Separator Prerequisites runbook](runbooks/stronger-offline-target-speaker-separator-prerequisites.md)
60. [Speaker-Preserving production requalification v2.17](research/2026-08-06-speaker-preserving-neural-echo-production-requalification-v2-17.md)
61. [SepFormer Four-Stem qualification contract](contracts/sepformer-four-stem-target-me-qualification.md)
62. [SepFormer Four-Stem qualification runbook](runbooks/sepformer-four-stem-target-me-qualification.md)
63. [SepFormer Four-Stem qualification result](research/2026-08-07-sepformer-four-stem-target-me-qualification-v1.md)
64. [Reliable Final Handoff v1 baseline](testing/2026-08-05-reliable-final-handoff-baseline.md)
65. [Canonical Live ASR Producer v1 result](testing/2026-08-06-canonical-live-asr-producer-v1.md)
66. [Causal Canonical Mic ASR v1 result](testing/2026-08-06-causal-canonical-mic-asr-v1.md)
67. [Remote Speaker Evidence Map v1 result](testing/2026-08-06-remote-speaker-evidence-map-v1.md)
68. [Remote Speaker Diarization v2 contract](contracts/remote-speaker-diarization-v2.md)
69. [Remote Speaker Diarization v2 result](testing/2026-08-07-remote-speaker-diarization-v2.md)
70. [Transcript Perfection Corpus contract](contracts/transcript-perfection-corpus.md)
71. [Transcript Perfection Corpus runbook](runbooks/transcript-perfection-corpus.md)
72. [Transcript Perfection Corpus baseline](testing/2026-08-07-transcript-perfection-corpus-v1.md)
73. [Remote Speaker Coverage v3 contract](contracts/remote-speaker-coverage-v3.md)
74. [Remote Speaker Coverage v3 runbook](runbooks/remote-speaker-coverage-v3.md)
75. [Remote Speaker Coverage v3 result](testing/2026-08-07-remote-speaker-coverage-v3.md)
76. [Remote Speaker Residual Evidence v4 contract](contracts/remote-speaker-residual-evidence-v4.md)
77. [Remote Speaker Residual Evidence v4 runbook](runbooks/remote-speaker-residual-evidence-v4.md)
78. [Remote Speaker Residual Evidence v4 result](testing/2026-08-07-remote-speaker-residual-evidence-v4.md)
79. [Evidence-Guarded Local Synthesis runbook](runbooks/evidence-guarded-local-synthesis.md)
80. [Evidence-Guarded Local Synthesis result](research/2026-08-07-evidence-guarded-local-synthesis-v1.md)
81. [Evidence-Only Local Note Selection runbook](runbooks/evidence-only-local-note-selection.md)
82. [Evidence-Only Local Note Selection result](research/2026-08-07-evidence-only-local-note-selection-v1.md)
83. [Session debug cycle: 2026-08-07_15-01-22](testing/2026-08-07-session-debug-cycle.md)
84. [Session debug cycle: 2026-08-07_16-03-37](testing/2026-08-07-session-2026-08-07_16-03-37-debug.md)
85. [Speaker-Resolved Transcript Default v1 contract](contracts/speaker-resolved-transcript-default-v1.md)
86. [Speaker-Resolved Transcript Default v1 runbook](runbooks/speaker-resolved-transcript-default-v1.md)
87. [Speaker-Resolved Transcript Default v1 result](testing/2026-08-08-speaker-resolved-transcript-default-v1.md)
88. [Lexical Accuracy Reference Corpus contract](contracts/lexical-accuracy-reference-corpus.md)
89. [Lexical Accuracy Reference Corpus runbook](runbooks/lexical-accuracy-reference-corpus.md)
90. [Lexical Accuracy Reference Corpus result](testing/2026-08-08-lexical-accuracy-reference-corpus-v1.md)
91. [Independent Remote Speaker Evidence v1 contract](contracts/independent-remote-speaker-evidence-v1.md)
92. [Independent Remote Speaker Evidence v1 runbook](runbooks/independent-remote-speaker-evidence-v1.md)
93. [Independent Remote Speaker Evidence v1 result](testing/2026-08-08-independent-remote-speaker-evidence-v1.md)
94. [Remote Speaker Residual Reference Corpus v1 contract](contracts/remote-speaker-residual-reference-corpus-v1.md)
95. [Remote Speaker Residual Reference Corpus v1 runbook](runbooks/remote-speaker-residual-reference-corpus-v1.md)
96. [Remote Speaker Residual Reference Corpus v1 result](testing/2026-08-08-remote-speaker-residual-reference-corpus-v1.md)
97. [Controlled Remote Speaker Truth Lab v1 contract](contracts/controlled-remote-speaker-truth-lab-v1.md)
98. [Controlled Remote Speaker Truth Lab v1 runbook](runbooks/controlled-remote-speaker-truth-lab-v1.md)
99. [Controlled Remote Speaker Truth Lab v1 result](testing/2026-08-08-controlled-remote-speaker-truth-lab-v1.md)
100. [Duration-Aware Remote Speaker Attribution v2 contract](contracts/duration-aware-remote-speaker-attribution-v2.md)
101. [Duration-Aware Remote Speaker Attribution v2 runbook](runbooks/duration-aware-remote-speaker-attribution-v2.md)
102. [Duration-Aware Remote Speaker Attribution v2 result](testing/2026-08-08-duration-aware-remote-speaker-attribution-v2.md)
103. [Segment-Context Remote Speaker Attribution v1 contract](contracts/segment-context-remote-speaker-attribution-v1.md)
104. [Segment-Context Remote Speaker Attribution v1 runbook](runbooks/segment-context-remote-speaker-attribution-v1.md)
105. [Segment-Context Remote Speaker Attribution v1 result](testing/2026-08-08-segment-context-remote-speaker-attribution-v1.md)
106. [Remote Speaker Attribution Error Decomposition v1 contract](contracts/remote-speaker-attribution-error-decomposition-v1.md)
107. [Remote Speaker Attribution Error Decomposition v1 runbook](runbooks/remote-speaker-attribution-error-decomposition-v1.md)
108. [Remote Speaker Attribution Error Decomposition v1 result](testing/2026-08-08-remote-speaker-attribution-error-decomposition-v1.md)
109. [Remote Speaker Attribution Error Decomposition v1 manifest](testing/remote-speaker-attribution-error-decomposition-v1-manifest.json)
110. [Stronger Remote Speaker Identity Backend Qualification v1 contract](contracts/stronger-remote-speaker-identity-backend-qualification-v1.md)
111. [Stronger Remote Speaker Identity Backend Qualification v1 runbook](runbooks/stronger-remote-speaker-identity-backend-qualification-v1.md)
112. [Stronger Remote Speaker Identity Backend Qualification v1 result](testing/2026-08-08-stronger-remote-speaker-identity-backend-qualification-v1.md)
113. [Stronger Remote Speaker Identity Backend Qualification v1 manifest](testing/stronger-remote-speaker-identity-backend-qualification-v1-manifest.json)
114. [ECAPA Remote Speaker Shadow Qualification v1 contract](contracts/ecapa-remote-speaker-shadow-qualification-v1.md)
115. [ECAPA Remote Speaker Shadow Qualification v1 runbook](runbooks/ecapa-remote-speaker-shadow-qualification-v1.md)
116. [ECAPA Remote Speaker Shadow Qualification v1 result](testing/2026-08-08-ecapa-remote-speaker-shadow-qualification-v1.md)
117. [ECAPA Remote Speaker Shadow Qualification v1 manifest](testing/ecapa-remote-speaker-shadow-qualification-v1-manifest.json)
118. [Remote Speaker Shadow Error Decomposition v1 contract](contracts/remote-speaker-shadow-error-decomposition-v1.md)
119. [Remote Speaker Shadow Error Decomposition v1 runbook](runbooks/remote-speaker-shadow-error-decomposition-v1.md)
120. [Remote Speaker Shadow Error Decomposition v1 result](testing/2026-08-09-remote-speaker-shadow-error-decomposition-v1.md)
121. [Remote Speaker Shadow Error Decomposition v1 manifest](testing/remote-speaker-shadow-error-decomposition-v1-manifest.json)
122. [Bounded Remote Speaker Interval Purification v1 contract](contracts/bounded-remote-speaker-interval-purification-v1.md)
123. [Bounded Remote Speaker Interval Purification v1 runbook](runbooks/bounded-remote-speaker-interval-purification-v1.md)
124. [Bounded Remote Speaker Interval Purification v1 result](testing/2026-08-09-bounded-remote-speaker-interval-purification-v1.md)
125. [Bounded Remote Speaker Interval Purification v1 manifest](testing/bounded-remote-speaker-interval-purification-v1-manifest.json)
126. [Session-Local Remote Speaker Enrollment Hardening v1 contract](contracts/session-local-remote-speaker-enrollment-hardening-v1.md)
127. [Session-Local Remote Speaker Enrollment Hardening v1 runbook](runbooks/session-local-remote-speaker-enrollment-hardening-v1.md)
128. [Session-Local Remote Speaker Enrollment Hardening v1 result](testing/2026-08-09-session-local-remote-speaker-enrollment-hardening-v1.md)
129. [Session-Local Remote Speaker Enrollment Hardening v1 manifest](testing/session-local-remote-speaker-enrollment-hardening-v1-manifest.json)
130. [Remote Speaker Direct Truth Seed v1 contract](contracts/remote-speaker-direct-truth-seed-v1.md)
131. [Remote Speaker Direct Truth Seed v1 runbook](runbooks/remote-speaker-direct-truth-seed-v1.md)
132. [Remote Speaker Direct Truth Seed v1 result](testing/2026-08-09-remote-speaker-direct-truth-seed-v1.md)
133. [Remote Speaker Direct Truth Seed v1 manifest](testing/remote-speaker-direct-truth-seed-v1-manifest.json)
134. [Remote Speaker Direct-Truth Candidate Adjudication v1 contract](contracts/remote-speaker-direct-truth-candidate-adjudication-v1.md)
135. [Remote Speaker Direct-Truth Candidate Adjudication v1 runbook](runbooks/remote-speaker-direct-truth-candidate-adjudication-v1.md)
136. [Remote Speaker Direct-Truth Candidate Adjudication v1 result](testing/2026-08-09-remote-speaker-direct-truth-candidate-adjudication-v1.md)
137. [Remote Speaker Direct-Truth Candidate Adjudication v1 manifest](testing/remote-speaker-direct-truth-candidate-adjudication-v1-manifest.json)
138. [Remote Speaker Enrollment Purity and Abstention Hardening v2 contract](contracts/remote-speaker-enrollment-purity-abstention-hardening-v2.md)
139. [Remote Speaker Enrollment Purity and Abstention Hardening v2 runbook](runbooks/remote-speaker-enrollment-purity-abstention-hardening-v2.md)
140. [Remote Speaker Enrollment Purity and Abstention Hardening v2 result](testing/2026-08-09-remote-speaker-enrollment-purity-abstention-hardening-v2.md)
141. [Remote Speaker Enrollment Purity and Abstention Hardening v2 manifest](testing/remote-speaker-enrollment-purity-abstention-hardening-v2-manifest.json)
142. [Session-Local Homogeneous Remote Speaker Enrollment Mining v1 contract](contracts/session-local-homogeneous-remote-speaker-enrollment-mining-v1.md)
143. [Session-Local Homogeneous Remote Speaker Enrollment Mining v1 runbook](runbooks/session-local-homogeneous-remote-speaker-enrollment-mining-v1.md)
144. [Session-Local Homogeneous Remote Speaker Enrollment Mining v1 result](testing/2026-08-09-session-local-homogeneous-remote-speaker-enrollment-mining-v1.md)
145. [Session-Local Homogeneous Remote Speaker Enrollment Mining v1 manifest](testing/session-local-homogeneous-remote-speaker-enrollment-mining-v1-manifest.json)
146. [Session-Local Remote Speaker Re-Clustering Feasibility v1 contract](contracts/session-local-remote-speaker-reclustering-feasibility-v1.md)
147. [Session-Local Remote Speaker Re-Clustering Feasibility v1 runbook](runbooks/session-local-remote-speaker-reclustering-feasibility-v1.md)
148. [Session-Local Remote Speaker Re-Clustering Feasibility v1 result](testing/2026-08-09-session-local-remote-speaker-reclustering-feasibility-v1.md)
149. [Session-Local Remote Speaker Re-Clustering Feasibility v1 manifest](testing/session-local-remote-speaker-reclustering-feasibility-v1-manifest.json)
150. [Planning and development history](history/README.md)

## Current Planning Entry Points

Planning snapshot: 2026-08-09. Durable capture, authoritative batch transcription, guarded review,
optional extractive notes/export and Speaker-Preserving Neural Echo v2.17 remain production. The bounded
pre-ASR frontier is closed: SepFormer could assign present Target-Me stems but failed reliable
presence/absence separation before dev. Anonymous rich transcript, explicit reviewed naming and
Reviewed Speaker-Aware Meeting Memory v1 are promoted optional read surfaces.

Free-text synthesis is closed with reproducible `DO_NOT_PROMOTE`; exact ID-only selection remains an
optional derivative. The mission now ends at a reliable speaker-resolved transcript. Remote Speaker
Diarization v2 passed `PROMOTE`; Coverage v3 raised attributable remote speech to `93.9312%` while
preserving B-cubed F1 `0.962171`, pairwise precision `0.961675` and every selected word. Residual
Evidence v4 safely recovered another 124 words / `83.640s`, but its `14.57%` word and `13.98%`
second reductions missed both `20%` promotion gates. It closed with reproducible `DO_NOT_PROMOTE`.
**Speaker-Resolved Transcript Default v1** is promoted for ordinary read/handoff/export with exact
aggregate fallback. **Lexical Accuracy Reference Corpus v1** closed with
`REFERENCE_INSUFFICIENT`: the exact 67-word digital subset has WER/CER `0`, while real-meeting
correctness still lacks human-reviewed evidence. The independent WavLM experiment over the frozen
`598.240s` unknown residue is complete with `DO_NOT_PROMOTE`: 53 words / `23.357s` recovered and no
direct candidate truth.
**Remote Speaker Residual Reference Corpus v1** materialized 278 blind items but closed
`REFERENCE_INSUFFICIENT`: all 53 proposals still lack direct truth. **Controlled Remote Speaker
Truth Lab v1** then froze 8 sessions and 240 exact words. The Coverage v3 control qualified on hard,
while the WavLM candidate closed `DO_NOT_ADVANCE` after two unseen open-set false attributions.
**Duration-Aware Remote Speaker Attribution v2** then opened a separate 125-word hard-v2 once.
Conservative fusion kept pairwise precision `1.0` and zero open-set errors but reached only `55.1402%`
known recall and `32.1429%` boundary recall, so it closed `DO_NOT_PROMOTE_TOPOLOGY`.
**Segment-Context Remote Speaker Attribution v1** also closed `DO_NOT_PROMOTE`: hard-v3 known recall
`44.5087%`, boundaries `0/20` and two open-set errors. **Remote Speaker Attribution Error
Decomposition v1** selected identity as the dominant axis: gain `0.351382` versus boundary
`0.063882` and overlap/open-set `0.036364`. **Stronger Remote Speaker Identity Backend
Qualification v1** then promoted ECAPA as a lab-only candidate after a one-shot hard-v4 result of
B-cubed F1 `0.948042`, known recall `0.947368`, pairwise precision `1.0`, zero open-set false
attribution and exact 154/154 word conservation. **ECAPA Remote Speaker Shadow Qualification v1**
then closed `DO_NOT_PROMOTE_REAL_IDENTITY`. **Remote Speaker Shadow Error Decomposition v1** routed
93/214 failures and `201.273504s` to the dominant interval axis. **Bounded Remote Speaker Interval
Purification v1** then closed `DO_NOT_ADVANCE`: 2 new words / `4.154556s` and one new reference
error. **Session-Local Remote Speaker Enrollment Hardening v1** also closed `DO_NOT_ADVANCE`: 11
new acceptances / `44.694004s`, five removed controls and only 4/83 target failures recovered.
**Remote Speaker Direct Truth Seed v1** then froze 33 primary items / 116 words / `90.100820s` and
8 hidden repeats across six sessions. Blind review closed all 41 slots with 8 attributed, 11 unknown,
4 mixed and 10 unusable primary outcomes; repeat consistency is `7/8`. The result is
`DIRECT_TRUTH_SEED_READY`. **Remote Speaker Direct-Truth Candidate Adjudication v1** then closed
`KEEP_COVERAGE_V3`: 3 correct gains, 2 lost correct controls and 13 versus 8 fail-closed unsafe
accepts. **Remote Speaker Enrollment Purity and Abstention Hardening v2** also kept Coverage v3:
7/14 profiles qualified, unsafe accepts returned to 8, but no identity was added. **Session-Local
Homogeneous Remote Speaker Enrollment Mining v1** then qualified 9/14 profiles and 39 windows, but
preserved 0/3 gains, lost three controls and added four false identities, so it also kept Coverage v3.
**Session-Local Remote Speaker Re-Clustering Feasibility v1** then froze 347 blind windows before
labels and reached `EMBEDDING_GEOMETRY_BOUND`: minimum ECAPA/WavLM ARI `0.090170`, stability
`0.465715`, preserved gains `0/3`. The current models are closed; the next bounded goal qualifies a
materially independent local speaker representation. Notes and retrieval remain parked.

- Start with [README](../README.md) for the current command-line workflow and product boundary.
- [Current goal notes](project/current-goal.md) define the recommended executable scope,
  implementation sequence and acceptance gates.
- Route design: [Reliable transcription route](project/reliable-transcription-route.md).
- Use [CLI MVP Definition of Done](project/cli-mvp-definition-of-done.md) to check whether the command-line product gate still holds.
- The [OpsKarta v3 plan](roadmap/murmurmark-cli-roadmap.plan.yaml) is authoritative for statuses,
  dependencies and roadmap views. The [CLI roadmap](roadmap/murmurmark-cli-roadmap.md) is its
  readable narrative.
- Historical goal and experiment sections are evidence records. They do not override the recommended
  goal or OpsKarta statuses. Detailed snapshots are indexed under [history](history/README.md).

## v1 Completeness Checklist

- Product purpose and non-goals are explicit.
- Capture mechanism is chosen and alternatives are recorded.
- The local session package is specified.
- Transcription, diarization and correction stages have clear inputs and outputs.
- Long-meeting behavior is specified.
- Synthesis is separate from transcription.
- Privacy modes and retention rules are explicit.
- Implementation milestones and acceptance criteria are documented.
