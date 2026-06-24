# Documentation Index

Read in this order:

1. [Product vision](product/vision.md)
2. [v1 product requirements](product/prd-v1.md)
3. [System overview](architecture/system-overview.md)
4. [Capture architecture](architecture/capture.md)
5. [Transcription architecture](architecture/transcription.md)
6. [Echo Guard architecture](architecture/echo-suppression.md)
7. [Evidence and synthesis](architecture/evidence-synthesis.md)
8. [Session package contract](contracts/session-package.md)
9. [Transcript and evidence contracts](contracts/transcript-and-evidence.md)
10. [Privacy and threat model](security/privacy-and-threat-model.md)
11. [First recording runbook](runbooks/first-recording.md)
12. [Echo Guard delay lab](runbooks/echo-guard-lab.md)
13. [Simple whisper.cpp transcription](runbooks/transcribe-simple-whispercpp.md)
14. [Tradeoffs](decisions/tradeoffs.md)
15. [RFC-0001](rfc/0001-v1-scope.md)
16. [ADR directory](adr/)
17. [ADR-0008](adr/0008-use-screencapturekit-for-first-cli-smoke.md)
18. [ADR-0009](adr/0009-derived-echo-suppression-only.md)
19. [ADR-0010](adr/0010-use-preserve-local-fir-for-current-echo-guard.md)
20. [Talk validation log](testing/2026-06-22-talk-validation.md)
21. [Echo Guard Local FIR validation log](testing/2026-06-23-echo-guard-local-fir.md)
22. [Mic remote bleed reduction](backlog/mic-remote-bleed-reduction.md)

## v1 Completeness Checklist

- Product purpose and non-goals are explicit.
- Capture mechanism is chosen and alternatives are recorded.
- The local session package is specified.
- Transcription, diarization and correction stages have clear inputs and outputs.
- Long-meeting behavior is specified.
- Synthesis is separate from transcription.
- Privacy modes and retention rules are explicit.
- Implementation milestones and acceptance criteria are documented.
