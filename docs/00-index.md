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
21. [Retention policy contract](contracts/retention-policy.md)
22. [Privacy and threat model](security/privacy-and-threat-model.md)
23. [First recording runbook](runbooks/first-recording.md)
24. [Meeting cheat sheet](runbooks/meeting-cheatsheet.md)
25. [Echo Guard delay lab](runbooks/echo-guard-lab.md)
26. [Simple whisper.cpp transcription](runbooks/transcribe-simple-whispercpp.md)
27. [Causal recovery generalization runbook](runbooks/causal-recovery-generalization.md)
28. [Tradeoffs](decisions/tradeoffs.md)
29. [RFC-0001](rfc/0001-v1-scope.md)
30. [ADR directory](adr/)
31. [ADR-0008](adr/0008-use-screencapturekit-for-first-cli-smoke.md)
32. [ADR-0009](adr/0009-derived-echo-suppression-only.md)
33. [ADR-0010](adr/0010-use-preserve-local-fir-for-current-echo-guard.md)
34. [Talk validation log](testing/2026-06-22-talk-validation.md)
35. [Echo Guard Local FIR validation log](testing/2026-06-23-echo-guard-local-fir.md)
36. [Mic remote bleed reduction](backlog/mic-remote-bleed-reduction.md)
37. [Complete echo removal research](research/2026-06-30-complete-echo-removal.md)
38. [Echo Suppression Promotion v1 result](research/2026-07-23-echo-suppression-promotion-v1.md)
39. [Neural Residual Echo Suppression v1 result](research/2026-07-23-neural-residual-echo-v1.md)
40. [Speaker-Preserving Echo Adaptation Corpus v1 result](research/2026-07-23-speaker-preserving-echo-adaptation-corpus-v1.md)
41. [Planning and development history](history/README.md)

## Current Planning Entry Points

Planning snapshot: 2026-07-23. Speaker-Preserving Echo Adaptation Corpus v1 completed with
reproducible `DO_NOT_TRAIN`: privacy and session-disjoint splits passed, but no remote-only interval
passed the frozen confidence gate, so no valid echo supervision or synthetic pair exists. Replay
matched `414/414` files, no training ran and `local_fir` remains production. Evidence Notes And
Export v2 is now the current executable product goal: produce one deterministic, evidence-backed
handoff bundle with explicit review and export readiness.

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
