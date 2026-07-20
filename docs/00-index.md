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
17. [Experimental sidecar contract](contracts/experimental-sidecar.md)
18. [Transcript and evidence contracts](contracts/transcript-and-evidence.md)
19. [Release bundle contract](contracts/release-bundle.md)
20. [Retention policy contract](contracts/retention-policy.md)
21. [Privacy and threat model](security/privacy-and-threat-model.md)
22. [First recording runbook](runbooks/first-recording.md)
23. [Echo Guard delay lab](runbooks/echo-guard-lab.md)
24. [Simple whisper.cpp transcription](runbooks/transcribe-simple-whispercpp.md)
25. [Causal recovery generalization runbook](runbooks/causal-recovery-generalization.md)
26. [Tradeoffs](decisions/tradeoffs.md)
27. [RFC-0001](rfc/0001-v1-scope.md)
28. [ADR directory](adr/)
29. [ADR-0008](adr/0008-use-screencapturekit-for-first-cli-smoke.md)
30. [ADR-0009](adr/0009-derived-echo-suppression-only.md)
31. [ADR-0010](adr/0010-use-preserve-local-fir-for-current-echo-guard.md)
32. [Talk validation log](testing/2026-06-22-talk-validation.md)
33. [Echo Guard Local FIR validation log](testing/2026-06-23-echo-guard-local-fir.md)
34. [Mic remote bleed reduction](backlog/mic-remote-bleed-reduction.md)
35. [Complete echo removal research](research/2026-06-30-complete-echo-removal.md)
36. [Planning and development history](history/README.md)

## Current Planning Entry Points

Planning snapshot: 2026-07-20. Speaker-Mode Transcript Quality Hardening v1 completed with
`DO_NOT_PROMOTE`: the sparse Echo Guard fix and automatic acoustic-mode evidence passed, while the
isolated transcript profile reached only `2.7%` duplicate and `7.9%` review reduction. The current
executable goal is Mixed-Utterance Remote Span Separation v1.

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
