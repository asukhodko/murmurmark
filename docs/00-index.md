# Documentation Index

Read in this order:

1. [Product vision](product/vision.md)
2. [v1 product requirements](product/prd-v1.md)
3. [Open-source readiness](project/open-source-readiness.md)
4. [CLI MVP Definition of Done](project/cli-mvp-definition-of-done.md)
5. [Reliable transcription route](project/reliable-transcription-route.md)
6. [CLI roadmap](roadmap/murmurmark-cli-roadmap.md)
7. [System overview](architecture/system-overview.md)
8. [Capture architecture](architecture/capture.md)
9. [Experimental sidecar architecture](architecture/experimental-sidecar.md)
10. [Transcription architecture](architecture/transcription.md)
11. [Echo Guard architecture](architecture/echo-suppression.md)
12. [Evidence and synthesis](architecture/evidence-synthesis.md)
13. [Session package contract](contracts/session-package.md)
14. [Experimental sidecar contract](contracts/experimental-sidecar.md)
15. [Transcript and evidence contracts](contracts/transcript-and-evidence.md)
16. [Release bundle contract](contracts/release-bundle.md)
17. [Retention policy contract](contracts/retention-policy.md)
18. [Privacy and threat model](security/privacy-and-threat-model.md)
19. [First recording runbook](runbooks/first-recording.md)
20. [Echo Guard delay lab](runbooks/echo-guard-lab.md)
21. [Simple whisper.cpp transcription](runbooks/transcribe-simple-whispercpp.md)
22. [Tradeoffs](decisions/tradeoffs.md)
23. [RFC-0001](rfc/0001-v1-scope.md)
24. [ADR directory](adr/)
25. [ADR-0008](adr/0008-use-screencapturekit-for-first-cli-smoke.md)
26. [ADR-0009](adr/0009-derived-echo-suppression-only.md)
27. [ADR-0010](adr/0010-use-preserve-local-fir-for-current-echo-guard.md)
28. [Talk validation log](testing/2026-06-22-talk-validation.md)
29. [Echo Guard Local FIR validation log](testing/2026-06-23-echo-guard-local-fir.md)
30. [Mic remote bleed reduction](backlog/mic-remote-bleed-reduction.md)
31. [Complete echo removal research](research/2026-06-30-complete-echo-removal.md)

## Current Planning Entry Points

- Start with [README](../README.md) for the current command-line workflow and latest corpus snapshot.
- Active goal, acceptance gates and completed-goal context: [Current goal notes](project/current-goal.md).
- Route design: [Reliable transcription route](project/reliable-transcription-route.md).
- Use [CLI MVP Definition of Done](project/cli-mvp-definition-of-done.md) to check whether the command-line product gate still holds.
- Use [CLI roadmap](roadmap/murmurmark-cli-roadmap.md) for the next implementation goal and the dependency map.

## v1 Completeness Checklist

- Product purpose and non-goals are explicit.
- Capture mechanism is chosen and alternatives are recorded.
- The local session package is specified.
- Transcription, diarization and correction stages have clear inputs and outputs.
- Long-meeting behavior is specified.
- Synthesis is separate from transcription.
- Privacy modes and retention rules are explicit.
- Implementation milestones and acceptance criteria are documented.
