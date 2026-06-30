# Documentation Index

Read in this order:

1. [Product vision](product/vision.md)
2. [v1 product requirements](product/prd-v1.md)
3. [Open-source readiness](project/open-source-readiness.md)
4. [CLI MVP Definition of Done](project/cli-mvp-definition-of-done.md)
5. [CLI roadmap](roadmap/murmurmark-cli-roadmap.md)
6. [System overview](architecture/system-overview.md)
7. [Capture architecture](architecture/capture.md)
8. [Transcription architecture](architecture/transcription.md)
9. [Echo Guard architecture](architecture/echo-suppression.md)
10. [Evidence and synthesis](architecture/evidence-synthesis.md)
11. [Session package contract](contracts/session-package.md)
12. [Transcript and evidence contracts](contracts/transcript-and-evidence.md)
13. [Release bundle contract](contracts/release-bundle.md)
14. [Retention policy contract](contracts/retention-policy.md)
15. [Privacy and threat model](security/privacy-and-threat-model.md)
16. [First recording runbook](runbooks/first-recording.md)
17. [Echo Guard delay lab](runbooks/echo-guard-lab.md)
18. [Simple whisper.cpp transcription](runbooks/transcribe-simple-whispercpp.md)
19. [Tradeoffs](decisions/tradeoffs.md)
20. [RFC-0001](rfc/0001-v1-scope.md)
21. [ADR directory](adr/)
22. [ADR-0008](adr/0008-use-screencapturekit-for-first-cli-smoke.md)
23. [ADR-0009](adr/0009-derived-echo-suppression-only.md)
24. [ADR-0010](adr/0010-use-preserve-local-fir-for-current-echo-guard.md)
25. [Talk validation log](testing/2026-06-22-talk-validation.md)
26. [Echo Guard Local FIR validation log](testing/2026-06-23-echo-guard-local-fir.md)
27. [Mic remote bleed reduction](backlog/mic-remote-bleed-reduction.md)

## Current Planning Entry Points

- Start with [README](../README.md) for the current command-line workflow and latest corpus snapshot.
- Latest completed project goal: [Export Bundle Quality v1](project/current-goal.md).
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
