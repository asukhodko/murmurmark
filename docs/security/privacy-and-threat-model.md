# Privacy and Threat Model

MurmurMark exists because built-in cloud meeting recording can be too broad, too visible to non-participants, or too hard to control after the meeting.

Privacy is a product requirement, not a later feature.

## Default Security Posture

- No telemetry by default.
- No network during capture by default.
- No cloud upload of raw audio by default.
- No screen recording by default.
- No raw audio in logs.
- No transcript in crash reports.
- Explicit retention policy.
- Stop-and-delete control.
- Local encrypted workspace recommended.
- Human review before external docs updates.

## Sensitive Assets

- Raw mic audio.
- Raw remote meeting audio.
- Transcript text.
- Speaker identity mapping.
- Voiceprints or speaker embeddings.
- Meeting notes.
- Domain packs and glossary.
- Retrieved project context.
- Provider payload manifests.

## Threats

### Accidental Cloud Exposure

Risk: raw audio, transcript or notes are sent to an external provider unexpectedly.

Controls:

- network disabled during capture by default;
- synthesis policy controls external payloads;
- raw audio cannot be sent externally by default;
- explicit payload manifest before frontier/API mode;
- provider profile records data retention assumptions.

### Wrong Speaker Attribution

Risk: a statement is assigned to the wrong person.

Controls:

- distinguish `speaker_cluster` from participant identity;
- conservative identity mapping;
- manual confirmation for names;
- low-confidence owners blocked in notes;
- quality report consumed by synthesis.

### Local-Only Mic Speech

Risk: user is muted in meeting app but MurmurMark records physical mic speech.

Controls:

- clear UX copy: mic track is local microphone speech;
- pause mic capture;
- later `delete last N seconds` design target;
- `local_only_possible` quality flag;
- notes policy can exclude these segments.

### Empty or Wrong Recording

Risk: remote track is silent or captures the wrong app.

Controls:

- preflight dry run;
- source level meters;
- silence warnings;
- post-recording health summary;
- `inspect` command;
- degraded status when remote track is suspicious.

### Log Leakage

Risk: logs contain meeting content.

Controls:

- events contain only technical data;
- crash reports exclude transcript/audio;
- no ASR text in `events.jsonl`;
- no speaker-inferred names in capture logs.

### Over-Broad App Capture

Risk: browser app capture records unrelated tabs.

Controls:

- browser meeting mode warning;
- recommend dedicated browser profile/window;
- future extension/window-aware capture;
- event log records target mode.

### Unsafe Docs Automation

Risk: model writes incorrect or sensitive changes into docs/Jira/Confluence.

Controls:

- generate patches, do not apply automatically;
- require utterance citations;
- require human approval;
- run agent adapters in explicit worktrees/sandboxes.

## Privacy Modes

### Local Only

Default.

- raw audio local;
- transcript local;
- synthesis local;
- docs export local.

### Sanitized Frontier

- raw audio local;
- transcript redacted locally;
- selected context sent externally;
- names/project codenames configurable;
- payload manifest required.

### Full Frontier with Approval

- raw audio still not sent by default;
- full transcript/context may be sent only after explicit approval;
- provider retention profile required.

## Retention Policy

Implemented policy flow:

- `murmurmark retention plan SESSION` writes a content-free retention plan under
  `SESSION/derived/retention/`;
- the default tracked policy is `examples/retention-policy.local-first.json`;
- default policy keeps raw audio, forbids copying raw audio into export bundles and disables
  external providers;
- raw deletion requires an explicit policy, a successful export manifest, `retention apply` and
  `--confirm-delete-raw`.

Deletion events are technical audit events, not content logs.

## Consent Boundary

This repository does not provide legal advice. Implementations must make room for consent workflows appropriate to the user's jurisdiction and organization.

Product UX should not imply that hidden recording is automatically acceptable. A future consent checklist should be part of onboarding before real meetings.
