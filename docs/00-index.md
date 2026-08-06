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
53. [Reliable Final Handoff v1 baseline](testing/2026-08-05-reliable-final-handoff-baseline.md)
54. [Canonical Live ASR Producer v1 result](testing/2026-08-06-canonical-live-asr-producer-v1.md)
55. [Causal Canonical Mic ASR v1 result](testing/2026-08-06-causal-canonical-mic-asr-v1.md)
56. [Remote Speaker Evidence Map v1 result](testing/2026-08-06-remote-speaker-evidence-map-v1.md)
57. [Planning and development history](history/README.md)

## Current Planning Entry Points

Planning snapshot: 2026-08-06. Speaker-Preserving Neural Echo v2 completed with guarded
`PROMOTE_SPEAKER_PRESERVING_NEURAL_ECHO_V2`: the sealed corpus selected candidate audio in `5/12`
sessions, exact fallback in `7/12`, removed `41.940s` and `90` remote-supported tokens, and retained
all candidate local tokens. Reference-Conditioned Target-Me Separation v1 then completed with
`DO_NOT_PROMOTE`: oracle and overfit passed, but two train/dev attempts missed the locked gates and
the corpus had no labelled non-target local speech. Target-Me Identifiability Corpus v1 then
completed with `READY_FOR_TARGET_CONDITIONED_TRAINING`: `4/2/2` split-disjoint non-target speakers,
`1200/300/300s` full mixtures, `980` paired query controls, zero contamination and replay
`2470/2470`. Reference-Conditioned Target-Me Separation v2 then learned speaker-query adherence but
missed three immutable dev quality gates and completed with `DO_NOT_PROMOTE`; hard and sealed data
remained unopened. Speaker-Preserving Neural Echo v2 remains production. Evidence Notes And Export
v2 passes its 110-session integrity and deterministic-replay gate. Release-quality CLI now adds
deterministic archives, complete integrity metadata, transactional install/upgrade and packaged
offline acceptance. Reliable Final Handoff v1 now passes its frozen cache/resume and actionability
gate. Authoritative Incremental ASR v1 and the exact remote producer are complete; the latter is
`DO_NOT_PROMOTE` because remote-only precomputation saves just `2.8651%..4.1040%` modeled wall time.
Causal Canonical Mic ASR v1 also completed with `DO_NOT_PROMOTE`: the frozen corpus produced `0/147`
exact raw-fallback mic windows, and `5/30/120s` prefix probes all differed from final local-FIR PCM.
The current Echo path has a session-end causal boundary. Remote Speaker Evidence Map v1 completed
with `PROMOTE_AUDIT_ONLY`: it publishes high-precision session-local evidence for roughly half of
remote speech and leaves the rest aggregate. Anonymous rich and explicit reviewed naming are now
promoted optional read surfaces. The current technical North Star now returns to the pre-ASR audio
boundary: retain every confirmed `Me` word while removing recognizable authoritative remote. The
current goal is Pre-ASR Target-Me Isolation Limit v1. Its first residual map completed with
`READY_FOR_ALIGNMENT_OR_ECHO_MODEL_V3`: alignment and echo-path work accounts for `2443.222s`
(`35.567%`) of actionable residual evidence, ahead of multi-component separation and Target-Me
model work. The nearest bounded stage is therefore Alignment and Echo-Path Model v3 Qualification.
Reviewed Speaker-Aware Meeting Memory v1 remains ready but deferred until that frontier closes.

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
