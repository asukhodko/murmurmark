# Contributing

MurmurMark is a local-first CLI pipeline for meeting capture, transcription,
review and evidence-backed notes.

## Ground Rules

- Do not commit real meeting recordings, transcripts, notes or exported bundles.
- Do not commit local `murmurmark.config.json`.
- Do not commit workspace-specific prompts, glossaries, names, tickets, service
  names or customer data.
- Keep examples generic. Use `examples/domain-packs/example-domain/` for public
  examples and keep private domain packs outside git.
- Raw audio must stay local unless a future policy explicitly allows otherwise.

## Checks

Before committing:

```bash
scripts/check.sh
scripts/check-open-source-readiness.sh
```

Useful local smoke commands:

```bash
murmurmark doctor --strict
murmurmark retention plan ./sessions/<session>
murmurmark retention payload ./sessions/<session>
```

## Development Style

- Keep capture, processing, review, export and retention stages separately
  rerunnable.
- Prefer explicit JSON/JSONL manifests over hidden state.
- Preserve raw capture files unless the user explicitly applies a retention
  policy that permits deletion.
- Every generated factual note should trace back to evidence IDs.

## License

A public license has not been selected yet. Choose and add a `LICENSE` file
before public release.
