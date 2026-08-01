# Retention Policy Contract

Retention policy defines what MurmurMark may keep, export or delete after a
session has been processed.

The default posture is conservative:

- raw audio stays local;
- raw audio is never copied into export bundles;
- external providers are disabled;
- raw deletion is never implicit;
- rebuildable derived media may be compacted after a successful guarded export.

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

The CLI also prints a human-readable summary with:

- plan path;
- mode, derived retention status, raw-audio file count and planned action counts;
- `can_apply` and `applied`;
- export manifest path, success flag, status/reason and audit-log paths when known;
- warnings and the next safe command.

The JSON plan is the authoritative artifact; the CLI summary is only a handoff view.

The plan records:

- selected policy;
- export manifest status;
- raw audio files, bytes and SHA-256 hashes;
- planned action per raw file;
- external provider policy;
- warnings;
- whether the plan can be applied;
- `recommended_next`, `next_commands` and `open_commands` for the next retention/export step.

If an export manifest is present, the plan treats it as successful only when the manifest belongs to
the same session. A stale or mismatched `exports/private/<session>/export_manifest.json` is reported
as `export_manifest_session_mismatch` and cannot unlock retention payload or raw-audio deletion.

Plan mode never deletes files.

## Provider Payload Manifest

Command:

```bash
murmurmark retention payload SESSION
```

Output:

```text
SESSION/derived/retention/provider_payload_manifest.json
```

Schema: `murmurmark.provider_payload_manifest/v1`.

The manifest is an inventory and gate. It does not send data.
The CLI summary prints the manifest path, status, provider, payload counts, `sends_data`,
`raw_audio_included`, blockers/warnings and a reminder to inspect the manifest before any
external handoff.

Default local-first policy produces:

```json
{
  "schema": "murmurmark.provider_payload_manifest/v1",
  "status": "blocked",
  "blockers": ["external_providers_disabled_by_policy"],
  "payload_files": [],
  "raw_audio_included": false,
  "sends_data": false
}
```

If a policy enables external providers and the export manifest is successful,
the manifest lists candidate payload files from the export bundle with bytes,
SHA-256 hashes and content classes. Raw audio files are always blockers in v1.

Required fields:

```json
{
  "schema": "murmurmark.provider_payload_manifest/v1",
  "status": "ready_for_review",
  "provider": "provider-name",
  "purpose": "reviewed_export_handoff",
  "policy": {
    "external_providers": {
      "allow": true,
      "raw_audio_allowed": false,
      "require_payload_manifest": true
    }
  },
  "export_manifest": {},
  "blockers": [],
  "warnings": [],
  "candidate_files": [],
  "payload_files": [],
  "payload_file_count": 0,
  "payload_bytes": 0,
  "raw_audio_included": false,
  "sends_data": false,
  "recommended_next": "less SESSION/derived/retention/provider_payload_manifest.json",
  "next_commands": [
    {
      "id": "inspect_payload_manifest",
      "command": "less SESSION/derived/retention/provider_payload_manifest.json"
    }
  ],
  "open_commands": [
    {
      "id": "open_provider_payload_manifest",
      "command": "less SESSION/derived/retention/provider_payload_manifest.json"
    }
  ]
}
```

`status: ready_for_review` means the payload inventory is policy-compatible.
It is not approval to upload automatically.

`provider_payload_manifest.json` is also self-guiding: `recommended_next` points to inspecting the
manifest before any external handoff, `next_commands` contains that inspection command, and
`open_commands` links the payload and export manifests.

The payload manifest applies the same session-match check to the export manifest before inventorying
files for an external provider.

## Apply

Command:

```bash
murmurmark retention apply SESSION --policy ./policy.json --confirm-delete-raw
```

Apply mode may delete raw CAF files only when the plan says `can_apply: true`.
It never deletes derived transcript, notes, review or export files in v1.

## Derived Media Compaction

Derived-media compaction is independent from raw-audio deletion:

```bash
murmurmark retention compact plan SESSION
murmurmark retention compact apply SESSION --confirm-delete-derived-media
murmurmark retention compact verify SESSION
```

Output:

```text
SESSION/derived/retention/derived_compaction.json
SESSION/derived/retention/derived_compaction.md
```

Schema: `murmurmark.derived_compaction/v1`.

Mode `keep_raw` removes regular media files with an allowlisted audio suffix below
`SESSION/derived/`. It preserves:

- all files below `SESSION/audio/`;
- `session.json`, `events.jsonl` and pipeline metadata;
- selected transcript, notes and quality verdict;
- review decisions, readiness, outcome and retention artifacts;
- every JSON, JSONL and Markdown provenance artifact.

Compaction is allowed only when capture is finalized, no recording or lifecycle lock is active,
pipeline state is not `running`, raw files declared by `session.json` still exist with their
expected sizes, and a selected transcript exists. The implementation does not follow symlinks and
rejects any candidate whose resolved path leaves `SESSION/derived/`.

The manifest records candidate count and bytes, summaries by subtree and extension, raw and selected
output identities, inventory fingerprint, blockers, warnings, applied totals and verification
result. It does not hash every rebuildable audio copy because that would reread hundreds of
gigabytes before deleting data already reproducible from raw.

Bulk mode accepts `all`, `--older-than 7d` and `--exclude-pinned`. Frozen-corpus, split, baseline and
immutable hard-test manifests under `sessions/_reports/`, plus explicit pin files, provide the
pinned session set. This protects experiments that freeze exact derived-audio hashes through a
baseline manifest even when they do not write `frozen_corpus.json`. Pinned sessions require
`--include-pinned` before manual compaction.

After successful guarded export, `murmurmark finish` applies `keep_raw` compaction automatically.
`murmurmark meeting` inherits that behavior because it calls `finish` only when structured quality
gates permit export. Both commands accept:

```bash
--keep-debug-artifacts
```

This flag skips automatic compaction for sessions retained for audio or pipeline investigation.
Blocked export, review-first and failed sessions remain unmodified. Compaction failure is fail-open:
the final export remains valid and existing files are kept or reported as a partial cleanup.

Re-running `murmurmark process SESSION` may recreate derived media. A later compaction plan scans the
current filesystem and can return the session to thin storage.

Bulk summary output:

```text
sessions/_reports/retention-compaction/derived_compaction_report.json
sessions/_reports/retention-compaction/derived_compaction_report.md
```

Schema: `murmurmark.derived_compaction_report/v1`.

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

Derived compaction writes one content-free summary event per apply/verify operation with schema
`murmurmark.derived_compaction_audit_event/v1`. It contains counts, byte totals, inventory
fingerprints and failure reasons, not one row per deleted audio file.
