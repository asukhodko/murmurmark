# Documentation Index

Read in this order:

1. [Product vision](product/vision.md)
2. [v1 product requirements](product/prd-v1.md)
3. [Open-source readiness](project/open-source-readiness.md)
4. [System overview](architecture/system-overview.md)
5. [Capture architecture](architecture/capture.md)
6. [Transcription architecture](architecture/transcription.md)
7. [Echo Guard architecture](architecture/echo-suppression.md)
8. [Evidence and synthesis](architecture/evidence-synthesis.md)
9. [Session package contract](contracts/session-package.md)
10. [Transcript and evidence contracts](contracts/transcript-and-evidence.md)
11. [Release bundle contract](contracts/release-bundle.md)
12. [Retention policy contract](contracts/retention-policy.md)
13. [Privacy and threat model](security/privacy-and-threat-model.md)
14. [First recording runbook](runbooks/first-recording.md)
15. [Echo Guard delay lab](runbooks/echo-guard-lab.md)
16. [Simple whisper.cpp transcription](runbooks/transcribe-simple-whispercpp.md)
17. [Tradeoffs](decisions/tradeoffs.md)
18. [RFC-0001](rfc/0001-v1-scope.md)
19. [ADR directory](adr/)
20. [ADR-0008](adr/0008-use-screencapturekit-for-first-cli-smoke.md)
21. [ADR-0009](adr/0009-derived-echo-suppression-only.md)
22. [ADR-0010](adr/0010-use-preserve-local-fir-for-current-echo-guard.md)
23. [Talk validation log](testing/2026-06-22-talk-validation.md)
24. [Echo Guard Local FIR validation log](testing/2026-06-23-echo-guard-local-fir.md)
25. [Mic remote bleed reduction](backlog/mic-remote-bleed-reduction.md)

## v1 Completeness Checklist

- Product purpose and non-goals are explicit.
- Capture mechanism is chosen and alternatives are recorded.
- The local session package is specified.
- Transcription, diarization and correction stages have clear inputs and outputs.
- Long-meeting behavior is specified.
- Synthesis is separate from transcription.
- Privacy modes and retention rules are explicit.
- Implementation milestones and acceptance criteria are documented.
