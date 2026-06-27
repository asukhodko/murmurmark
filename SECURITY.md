# Security Policy

MurmurMark handles sensitive local meeting material: raw microphone audio,
remote meeting audio, transcripts, notes and review decisions.

## Reporting

This repository does not have a public security contact yet.

Until public release, report security or privacy issues privately to the
repository owner through the project coordination channel used for development.
Do not attach real meeting audio or transcripts unless explicitly requested and
approved.

## Privacy Expectations

- No telemetry by default.
- No network during capture by default.
- No raw audio upload by default.
- Raw audio must not be copied into export bundles.
- External-provider payloads require `murmurmark retention payload` and explicit
  policy approval.
- Retention deletion must be explicit and auditable.

## Local Checks

Run before sharing or publishing:

```bash
scripts/check-open-source-readiness.sh
murmurmark doctor --strict
```

The readiness check blocks tracked private paths, raw audio, local configs,
private prompts/glossaries and known workspace-specific strings.
