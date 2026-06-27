# Retention Policy Contract

Retention policy defines what MurmurMark may keep, export or delete after a
session has been processed.

The default posture is conservative:

- raw audio stays local;
- raw audio is never copied into export bundles;
- external providers are disabled;
- deletion is never implicit.

## Policy File

Tracked example:

```text
examples/retention-policy.local-first.json
```

Schema: `murmurmark.retention_policy/v1`.

```json
{
  "schema": "murmurmark.retention_policy/v1",
  "name": "local_first_keep_raw",
  "raw_audio": {
    "after_successful_export": "keep",
    "delete_requires_confirmation": true
  },
  "derived_artifacts": {
    "default_action": "keep"
  },
  "exports": {
    "default_out_dir": "exports/private",
    "copy_raw_audio": false,
    "include_json": true
  },
  "external_providers": {
    "allow": false,
    "require_payload_manifest": true
  },
  "audit": {
    "write_events": true,
    "content_free": true
  }
}
```

`raw_audio.after_successful_export` currently supports:

- `keep`;
- `delete`.

`delete` is honored only when all of these are true:

- command mode is `apply`;
- `--confirm-delete-raw` is present;
- export manifest exists;
- export manifest schema is `murmurmark.export_manifest/v1`;
- export status is `exported` or `exported_with_warnings`;
- export manifest has no blockers.

## Plan

Command:

```bash
murmurmark retention plan SESSION
```

Output:

```text
SESSION/derived/retention/retention_plan.json
```

Schema: `murmurmark.retention_plan/v1`.

The plan records:

- selected policy;
- export manifest status;
- raw audio files, bytes and SHA-256 hashes;
- planned action per raw file;
- external provider policy;
- warnings;
- whether the plan can be applied.

Plan mode never deletes files.

## Apply

Command:

```bash
murmurmark retention apply SESSION --policy ./policy.json --confirm-delete-raw
```

Apply mode may delete raw CAF files only when the plan says `can_apply: true`.
It never deletes derived transcript, notes, review or export files in v1.

## Audit Events

Destructive actions append content-free events to:

```text
SESSION/derived/retention/retention_audit.jsonl
```

Schema: `murmurmark.retention_audit_event/v1`.

Example:

```json
{
  "schema": "murmurmark.retention_audit_event/v1",
  "created_at": "2026-06-27T00:00:00Z",
  "session_id": "2026-06-26T14-31-17Z_170c89",
  "action": "delete_raw_audio",
  "path": "audio/mic/000001.caf",
  "source": "mic",
  "bytes": 123456,
  "sha256": "abc...",
  "reason": "policy_delete_after_successful_export"
}
```

Audit events must not contain transcript text, notes, speaker names or audio
content.
