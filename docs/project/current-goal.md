# Latest Completed Goal: Export Bundle Quality v1

Status, 2026-06-30: completed.

MurmurMark now ends a successful meeting pipeline with a local handoff bundle that can be read as
the user-facing result, not as a directory of internal derived artifacts.

## Goal

After `record`, `process` and the minimal required review, the user should be able to run:

```bash
murmurmark finish SESSION
```

The resulting Markdown or Obsidian bundle should answer:

- can this result be used;
- which transcript profile was selected;
- what the verdict and review burden are;
- which notes, decisions, actions, risks and open questions were extracted;
- which utterance IDs support the notes;
- which regions still need review;
- where the full transcript is;
- what the retention/privacy next step is.

## Completed Scope

- `index.md` is now the start page with "Can I use this?", selected profile, verdict, review burden,
  review-needed items, retention/privacy summary and the next command.
- `quality_verdict.md` is rendered as a human trust report rather than a raw metrics dump.
- `notes.md` is rendered as a readable extractive working summary with evidence IDs and review
  markers.
- `transcript.md` is rendered from `clean_dialogue*.json` when available and keeps roles, timestamps,
  utterance IDs, source track and review flags.
- Obsidian export writes one self-contained frontmatter note with verdict, notes, review items and
  transcript content.
- `export_manifest.json` records `bundle_quality: "v1"`.
- Export still blocks honestly when readiness has blockers. Forced exports remain debug artifacts and
  say "Do not use yet".
- Raw audio is not copied into export bundles.

## Out Of Scope

- Changing capture, Echo Guard or the main ASR path.
- Making risky sessions look exportable.
- Auto-deleting raw audio.
- Writing directly to Obsidian, Jira, docs repositories or external providers.
- Generating unsupported summaries without evidence IDs.

## Verification

- `swift build`
- `.venv/bin/python -m py_compile scripts/*.py`
- `git diff --check`
- `scripts/smoke-cli-handoff.sh`
- `scripts/smoke-fixture.sh`
- `scripts/check-open-source-readiness.sh`
- `scripts/check.sh`
- opskarta v3 validation for `docs/roadmap/murmurmark-cli-roadmap.plan.yaml`
- Manual export checks on:
  - an exportable session;
  - a real forced/debug 1x1 session;
  - a real blocked/forced group session;
  - an Obsidian single-note export.

## Commit

`6362d12 feat: improve export bundle handoff`

## Next Direction

The next large goal should move back from presentation to quality convergence: reduce the remaining
review burden by making suggested review closure and corpus gates more first-class, without changing
capture, Echo Guard or the primary ASR.
