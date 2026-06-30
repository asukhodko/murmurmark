# Current Goal: Exportable Meeting Bundle

Status, 2026-06-30: current.

MurmurMark must end a successful meeting pipeline with one clear local handoff:
what to read, whether it is safe enough, what still needs review, where the export bundle is, and
what the retention/privacy recommendation says.

## Goal

After `record`, `process` and the minimal required review, the user should be able to run one command:

```bash
murmurmark finish SESSION
```

That command should:

- refresh readiness;
- create a guarded Markdown or Obsidian export bundle when readiness allows it;
- include JSON evidence by default;
- write retention plan and provider payload manifest without deleting raw audio;
- end with one read-only `next: less ...` handoff for the exported result;
- block honestly and point back to review/process when the session is not exportable.

## Out Of Scope

- Changing capture, Echo Guard or the main ASR path.
- Making risky sessions look exportable.
- Auto-deleting raw audio.
- Writing directly to Obsidian, Jira or external providers.
- Generating unsupported summaries without evidence IDs.

## Done When

- `murmurmark finish SESSION` works for an exportable session.
- A blocked session produces the same guarded blocked-export artifact as `murmurmark export`.
- Successful finish writes `export_manifest.json`, `retention_plan.json` and
  `provider_payload_manifest.json`.
- The final `next:` line points to a read-only local artifact, not a destructive command.
- README, runbooks, contracts and roadmap describe `finish` as the normal end of the CLI flow.
- Smoke and acceptance checks cover the command.

## Verification

- `swift build`
- `.venv/bin/python -m py_compile scripts/*.py`
- `scripts/smoke-cli-handoff.sh`
- `scripts/smoke-fixture.sh`
- `scripts/check.sh`
